"""One named private WHEP upstream; device-bound opaque persisted browser resources."""

import asyncio
import base64
import re
import secrets
import ssl
import time
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientSession, ClientTimeout, web

MEDIA_LIMIT = 65536


class WHEPProxy:
    def __init__(self, auth, config):
        self.auth = auth
        self.config = config
        self.url = config["url"]
        parsed = urlsplit(self.url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path != "/p1-observer/whep"
        ):
            raise ValueError("one exact private TLS WHEP source required")
        self.origin = f"{parsed.scheme}://{parsed.netloc}"
        self.prefix = parsed.path + "/"
        self.tls = ssl.create_default_context(cafile=config["ca"])
        self.tls.minimum_version = ssl.TLSVersion.TLSv1_2
        self.lock = asyncio.Lock()
        self.session = None
        self.janitor = None
        with auth.db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS media_sessions(handle TEXT PRIMARY KEY,device TEXT,url TEXT,lease REAL,deadline REAL)"
            )

    async def start(self):
        credential = base64.b64encode(
            (self.config["username"] + ":" + self.config["password"]).encode()
        ).decode()
        self.session = ClientSession(
            timeout=ClientTimeout(total=3), headers={"Authorization": "Basic " + credential}
        )
        self.janitor = asyncio.create_task(self.reap())

    async def close(self):
        if self.janitor:
            self.janitor.cancel()
            await asyncio.gather(self.janitor, return_exceptions=True)
        if self.session:
            await self.session.close()
        # Keep exact valid resources across gateway replacement, not fabricated reconstructed URLs.

    def resource_url(self, location):
        parsed = urlsplit(location)
        if parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise web.HTTPBadRequest(reason="invalid upstream media resource")
        if (parsed.scheme or parsed.netloc) and f"{parsed.scheme}://{parsed.netloc}" != self.origin:
            raise web.HTTPBadRequest(reason="invalid upstream media resource")
        if not parsed.path.startswith(self.prefix) or not re.fullmatch(
            "[a-zA-Z0-9_-]{1,128}", parsed.path[len(self.prefix) :]
        ):
            raise web.HTTPBadRequest(reason="invalid upstream media resource")
        return self.origin + parsed.path

    async def upstream(self, method, url, data, headers):
        try:
            async with self.session.request(
                method, url, data=data, headers=headers, ssl=self.tls, allow_redirects=False
            ) as response:
                chunks = []
                size = 0
                async for chunk in response.content.iter_chunked(8192):
                    size += len(chunk)
                    if size > MEDIA_LIMIT:
                        raise web.HTTPBadGateway(reason="media response size limit")
                    chunks.append(chunk)
                body = b"".join(chunks)
                if 300 <= response.status < 400:
                    raise web.HTTPBadGateway(reason="invalid media response")
                selected = {}
                for key in ("ETag", "Accept-Patch", "Content-Type"):
                    value = response.headers.get(key)
                    if value is not None and len(value) <= 512:
                        selected[key] = value
                # Preserve necessary WHEP Link headers from this one pinned authenticated source.
                # The deployed MediaMTX configuration has no ICE servers or public relays.
                links = response.headers.getall("Link", [])
                if links:
                    if sum(map(len, links)) > 4096:
                        raise web.HTTPBadGateway(reason="media Link header bound")
                    selected["Link"] = ", ".join(links)
                if response.headers.get("Location"):
                    selected["Location"] = response.headers["Location"]
                return response.status, body, selected
        except (ClientError, OSError, TimeoutError):
            raise web.HTTPServiceUnavailable(reason="optional P1 video unavailable") from None

    async def exchange(self, method, device, handle, data, headers):
        async with self.lock:
            now = time.time()
            if method == "POST":
                with self.auth.db() as db:
                    if db.execute("SELECT count(*) FROM media_sessions").fetchone()[0] >= 2:
                        raise web.HTTPTooManyRequests(reason="two optional viewers maximum")
                status, body, result = await self.upstream(
                    "POST", self.url, data, {"Content-Type": "application/sdp"}
                )
                if status != 201:
                    raise web.HTTPServiceUnavailable(reason="optional P1 video unavailable")
                if result.get("Content-Type", "").split(";")[0] != "application/sdp" or not result.get(
                    "ETag"
                ):
                    raise web.HTTPBadGateway(reason="invalid WHEP session response")
                resource = self.resource_url(result.get("Location", ""))
                handle = secrets.token_urlsafe(24)
                with self.auth.db() as db:
                    db.execute(
                        "INSERT INTO media_sessions VALUES(?,?,?,?,?)",
                        (handle, device, resource, now + 60, now + 660),
                    )
                result["Location"] = "/api/media/" + handle
                return status, body, result
            if not handle or not re.fullmatch("[a-zA-Z0-9_-]{32}", handle):
                raise web.HTTPNotFound()
            with self.auth.db() as db:
                row = db.execute(
                    "SELECT * FROM media_sessions WHERE handle=? AND device=?", (handle, device)
                ).fetchone()
            if not row or row["lease"] <= now or row["deadline"] <= now:
                raise web.HTTPNotFound()
            resource = self.resource_url(row["url"])
            if method == "KEEPALIVE":
                with self.auth.db() as db:
                    db.execute(
                        "UPDATE media_sessions SET lease=? WHERE handle=?",
                        (min(now + 60, row["deadline"]), handle),
                    )
                return 204, b"", {}
            outgoing = {}
            if method == "PATCH":
                etag = headers.get("If-Match", "")
                if not etag or len(etag) > 256:
                    raise web.HTTPPreconditionRequired(reason="WHEP If-Match required")
                outgoing = {"Content-Type": "application/trickle-ice-sdpfrag", "If-Match": etag}
            status, body, result = await self.upstream(method, resource, data, outgoing)
            if (method == "DELETE" and 200 <= status < 300) or status in (404, 410):
                with self.auth.db() as db:
                    db.execute("DELETE FROM media_sessions WHERE handle=?", (handle,))
            result.pop("Location", None)
            return status, body, result

    async def reap(self):
        while True:
            await asyncio.sleep(2)
            try:
                async with self.lock:
                    with self.auth.db() as db:
                        rows = db.execute(
                            "SELECT m.* FROM media_sessions m LEFT JOIN devices d ON d.id=m.device WHERE m.lease<=? OR m.deadline<=? OR d.revoked=1",
                            (time.time(), time.time()),
                        ).fetchall()
                    for row in rows:
                        try:
                            status, _, _ = await self.upstream(
                                "DELETE", self.resource_url(row["url"]), b"", {}
                            )
                        except web.HTTPException:
                            continue  # Keep exact resources for cleanup retry after media returns.
                        if 200 <= status < 300 or status in (404, 410):
                            with self.auth.db() as db:
                                db.execute("DELETE FROM media_sessions WHERE handle=?", (row["handle"],))
            except (OSError, ValueError):
                pass
