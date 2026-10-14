"""Non-actuating HTTPS/WSS gateway with explicit LAN opt-in and independent lifetime."""

import argparse
import asyncio
import hashlib
import ipaddress
import os
import re
import secrets
import socket
import sqlite3
import ssl
import time
import uuid
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import WSMsgType, web

from tools.am1_session_ipc import (
    CONTROL_LIMIT,
    IPCClient,
    encode,
    private_directory,
    private_json,
    strict_json,
)

COOKIE = "__Host-am1-device"
OPS = {
    "claim",
    "start",
    "lookup",
    "release",
    "handoff",
    "pause",
    "resume",
    "stop",
    "reconcile",
    "connect",
    "release_input",
    "grant",
    "input",
}
INTERACTIVE = {"connect", "release_input", "resume", "grant", "input"}
ASSETS = Path(__file__).with_name("am1_console_ui")


def unknown_outcome():
    return {
        "accepted": None,
        "status": "unknown",
        "uncertain": True,
        "reason": "owner outcome unknown; inspect state or lookup the same operation",
        "effect_admitted": None,
    }


async def close_socket(ws, transport, code=1000):
    # Bound the entire close, including blocked writes and cancellation cleanup.
    closing = asyncio.create_task(ws.close(code=code))
    closing.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
    try:
        async with asyncio.timeout(0.5):
            await asyncio.shield(closing)
    except (TimeoutError, ConnectionError, RuntimeError):
        transport.abort()
        closing.cancel()
    except asyncio.CancelledError:
        transport.abort()
        closing.cancel()
        raise


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class AuthStore:
    """Private local administration; opaque credential hashes only, pairing closed by default."""

    def __init__(self, directory):
        self.directory = private_directory(directory)
        self.path = self.directory / "devices.sqlite3"
        with self.db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS devices(id TEXT PRIMARY KEY, hash TEXT UNIQUE, csrf TEXT, control INTEGER, revoked INTEGER DEFAULT 0)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS pairing(hash TEXT PRIMARY KEY, expires REAL, control INTEGER)"
            )
        if os.name != "nt":
            self.path.chmod(0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=1)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def pair(self, control=False):
        code = secrets.token_urlsafe(24)
        with self.db() as db:
            db.execute("DELETE FROM pairing WHERE expires < ?", (time.time(),))
            if db.execute("SELECT count(*) FROM pairing").fetchone()[0] >= 8:
                raise ValueError("pairing limit")
            db.execute("INSERT INTO pairing VALUES(?,?,?)", (digest(code), time.time() + 60, int(control)))
        return code

    def enroll(self, code):
        if not isinstance(code, str) or len(code) > 128:
            return None
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM pairing WHERE hash=? AND expires>?", (digest(code), time.time())
            ).fetchone()
            if not row or db.execute("SELECT count(*) FROM devices").fetchone()[0] >= 32:
                return None
            db.execute("DELETE FROM pairing WHERE hash=?", (digest(code),))
            token, csrf, device = secrets.token_urlsafe(32), secrets.token_urlsafe(24), str(uuid.uuid4())
            db.execute(
                "INSERT INTO devices(id,hash,csrf,control) VALUES(?,?,?,?)",
                (device, digest(token), csrf, row["control"]),
            )
            return {"device_id": device, "csrf": csrf, "control": bool(row["control"]), "token": token}

    def resolve(self, token):
        if not token or len(token) > 128:
            return None
        with self.db() as db:
            row = db.execute("SELECT * FROM devices WHERE hash=? AND revoked=0", (digest(token),)).fetchone()
            return dict(row) if row else None

    def capable(self, device):
        with self.db() as db:
            return bool(
                db.execute(
                    "SELECT 1 FROM devices WHERE id=? AND control=1 AND revoked=0", (device,)
                ).fetchone()
            )

    def revoke(self, device):
        with self.db() as db:
            db.execute("UPDATE devices SET revoked=1 WHERE id=?", (device,))


class Peer:
    """At most one queued snapshot plus bounded responses, accounting for in-flight frame."""

    def __init__(self, ws, device, token):
        self.ws, self.device, self.token = ws, device, token
        self.queue = deque()
        self.bytes = self.inflight = 0
        self.wake = asyncio.Event()
        self.revision = None
        self.ack = False
        self.connection = None
        self.closed = False
        self.tasks = set()
        self.commands = {"normal": 0, "protective": 0}
        self.latest_input = None
        self.input_task = None
        self.sender = asyncio.create_task(self.send())

    def put(self, value, snapshot=False):
        if self.closed:
            return False
        data = encode(value)
        if snapshot:
            self.queue = deque(item for item in self.queue if not item[1])
            self.bytes = sum(len(item[0]) for item in self.queue)
        if self.bytes + self.inflight + len(data) > 65536:
            self.closed = True
            asyncio.create_task(close_socket(self.ws, self.ws._writer.transport, 1013))
            return False
        self.queue.append((data, snapshot))
        self.bytes += len(data)
        self.wake.set()
        return True

    async def send(self):
        try:
            while not self.closed:
                await self.wake.wait()
                while self.queue:
                    data, _ = self.queue.popleft()
                    self.bytes -= len(data)
                    self.inflight = len(data)
                    await asyncio.wait_for(self.ws.send_str(data.decode()), 0.5)
                    self.inflight = 0
                self.wake.clear()
        except (TimeoutError, ConnectionError, RuntimeError):
            self.closed = True
            await close_socket(self.ws, self.ws._writer.transport, 1013)

    async def close(self):
        self.closed = True
        self.sender.cancel()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(self.sender, *self.tasks, return_exceptions=True)
        self.tasks.clear()
        self.latest_input = None
        self.queue.clear()
        self.bytes = self.inflight = 0


class Gateway:
    def __init__(self, owner_state, auth_state):
        self.ipc = IPCClient(owner_state)
        self.auth = AuthStore(auth_state)
        self.origin = None
        self.peers = set()
        self.pending_ws = 0
        self.tickets = {}
        self.rates = {}
        self.http_active = {"normal": 0, "protective": 0}
        self.normal = asyncio.Semaphore(8)
        self.protective = asyncio.Semaphore(2)
        self.revision = 0
        self.incarnation = str(uuid.uuid4())
        self.app = web.Application(client_max_size=CONTROL_LIMIT, middlewares=[self.boundary])
        self.app.router.add_get("/", self.html)
        self.app.router.add_get("/assets/{name}", self.asset)
        self.app.router.add_post("/api/enroll", self.enroll)
        self.app.router.add_get("/api/session", self.session)
        self.app.router.add_get("/api/state", self.state)
        self.app.router.add_post("/api/attach", self.attach)
        self.app.router.add_post("/api/revoke", self.revoke)
        for op in OPS:
            self.app.router.add_post("/api/" + op, self.operation)
        self.app.router.add_get("/api/ws", self.websocket)
        self.app.on_startup.append(self.start)
        self.app.on_cleanup.append(self.cleanup)

    @web.middleware
    async def boundary(self, request, handler):
        try:
            for header in ("Host", "Origin", "X-AM1-CSRF", "Cookie", "Content-Type", "Content-Length"):
                if len(request.headers.getall(header, [])) > 1:
                    raise web.HTTPBadRequest(reason="ambiguous headers")
            if not request.secure or request.headers.get("Host") != self.origin.removeprefix("https://"):
                raise web.HTTPForbidden(reason="origin boundary")
            if request.query_string:
                raise web.HTTPBadRequest(reason="query not supported")
            mutation = request.method == "POST"
            websocket = request.path == "/api/ws"
            if (mutation or websocket) and request.headers.get("Origin") != self.origin:
                raise web.HTTPForbidden(reason="Origin")
            if mutation:
                if request.content_length is None or request.content_length > CONTROL_LIMIT:
                    raise web.HTTPRequestEntityTooLarge(
                        max_size=CONTROL_LIMIT, actual_size=request.content_length or 0
                    )
                if request.content_type != "application/json":
                    raise web.HTTPUnsupportedMediaType()
            if request.path.startswith("/api/") and request.path != "/api/enroll":
                cookie_parts = [
                    part.strip().split("=", 1)[0] for part in request.headers.get("Cookie", "").split(";")
                ]
                if cookie_parts.count(COOKIE) != 1:
                    raise web.HTTPUnauthorized()
                token = request.cookies.get(COOKIE)
                device = self.auth.resolve(token)
                if not device:
                    raise web.HTTPUnauthorized()
                request["device"], request["token"] = device, token
                if mutation and not secrets.compare_digest(
                    request.headers.get("X-AM1-CSRF", ""), device["csrf"]
                ):
                    raise web.HTTPForbidden(reason="CSRF")
            # Bounded rate buckets; hostile unenrolled clients share one bucket.
            identity = request.get("device", {}).get("id", "unenrolled")
            lane = "protective" if request.path in ("/api/pause", "/api/stop", "/api/revoke") else "normal"
            now = time.monotonic()
            bucket = self.rates.setdefault((identity, lane), deque())
            while bucket and bucket[0] < now - 1:
                bucket.popleft()
            if len(bucket) >= (10 if lane == "protective" else 40):
                raise web.HTTPTooManyRequests()
            bucket.append(now)
            if self.http_active[lane] >= (4 if lane == "protective" else 16):
                raise web.HTTPTooManyRequests(reason="inflight bound")
            self.http_active[lane] += 1
            try:
                return await handler(request)
            finally:
                self.http_active[lane] -= 1
        except web.HTTPException as exc:
            return web.json_response(
                {"accepted": False, "status": "refused", "reason": exc.reason, "effect_admitted": False},
                status=exc.status,
            )
        except (ValueError, TypeError, KeyError, UnicodeError):
            return web.json_response(
                {
                    "accepted": False,
                    "status": "refused",
                    "reason": "malformed request",
                    "effect_admitted": False,
                },
                status=400,
            )
        except (OSError, TimeoutError, asyncio.IncompleteReadError):
            return web.json_response(unknown_outcome(), status=503)

    async def body(self, request):
        value = strict_json(await asyncio.wait_for(request.read(), 1))
        if not isinstance(value, dict):
            raise ValueError("object required")
        return value

    async def call(self, command, device):
        lane = self.protective if command.get("op") in {"pause", "stop", "release"} else self.normal
        async with asyncio.timeout(1.5):
            async with lane:
                if command.get("op") not in {"snapshot", "release"} and not self.auth.capable(device):
                    return {
                        "accepted": False,
                        "status": "refused",
                        "reason": "revoked control context",
                        "effect_admitted": False,
                    }
                return await self.ipc.request(command, device)

    async def current(self):
        return (await self.call({"op": "snapshot"}, "gateway-view"))["snapshot"]

    def next_revision(self):
        self.revision += 1
        return self.incarnation + ":" + str(self.revision)

    async def html(self, request):
        text = (ASSETS / "index.html").read_text(encoding="utf-8")
        # Remote initializer executes before legacy bootstrap; independent camera scripts omitted.
        text = text.replace('<link rel="stylesheet" href="/camera/assets/style.css">', "")
        text = text.replace(
            '<script defer src="/camera/assets/freshness.js"></script><script defer src="/camera/assets/mjpeg.js"></script>',
            "",
        )
        text = text.replace(
            '<script defer src="/camera/assets/app.js"></script>',
            '<script defer src="/assets/session_transport.js"></script>',
        )
        text = text.replace(
            '<body data-console="compact">', '<body data-console="compact" data-session-mode="remote-fake">'
        )
        return web.Response(
            text=text,
            content_type="text/html",
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'",
            },
        )

    async def asset(self, request):
        name = request.match_info["name"]
        if name not in {"app.js", "session_transport.js", "style.css"}:
            raise web.HTTPNotFound()
        return web.FileResponse(ASSETS / name)

    async def enroll(self, request):
        body = await self.body(request)
        if set(body) != {"code"}:
            raise ValueError("enrollment")
        result = self.auth.enroll(body["code"])
        if not result:
            raise web.HTTPUnauthorized(reason="pairing closed or code expired")
        token = result.pop("token")
        response = web.json_response(dict(result, accepted=True))
        response.set_cookie(COOKIE, token, secure=True, httponly=True, samesite="Strict", path="/")
        return response

    async def session(self, request):
        d = request["device"]
        return web.json_response(
            {"device_id": d["id"], "csrf": d["csrf"], "control": bool(d["control"])},
            headers={"Cache-Control": "no-store"},
        )

    async def state(self, request):
        return web.json_response({"snapshot": await self.current()}, headers={"Cache-Control": "no-store"})

    async def attach(self, request):
        if await self.body(request):
            raise ValueError("attach payload")
        now = time.monotonic()
        self.tickets = {key: value for key, value in self.tickets.items() if value[1] > now}
        if len(self.tickets) >= 16:
            raise web.HTTPTooManyRequests()
        ticket = secrets.token_urlsafe(32)
        self.tickets[digest(ticket)] = (request["device"]["id"], now + 5)
        return web.json_response(
            {"ticket": ticket, "snapshot": await self.current(), "revision": self.next_revision()}
        )

    async def fence(self, device):
        self.tickets = {key: value for key, value in self.tickets.items() if value[0] != device}
        snapshot = await self.current()
        if snapshot.get("controller", {}) and snapshot["controller"]["device_id"] == device:
            await self.call({"op": "release", "operation_id": str(uuid.uuid4())}, device)
        for peer in list(self.peers):
            if peer.device == device:
                await close_socket(peer.ws, peer.ws._writer.transport, 1008)

    async def revoke(self, request):
        if await self.body(request):
            raise ValueError("revoke payload")
        self.auth.revoke(request["device"]["id"])
        await self.fence(request["device"]["id"])
        response = web.json_response({"accepted": True, "status": "revoked"})
        response.del_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="Strict")
        return response

    async def dispatch(self, command, device, peer=None):
        if not device["control"] or not self.auth.capable(device["id"]):
            raise web.HTTPForbidden(reason="control capability required")
        op = command.get("op")
        if op not in OPS or "device_id" in command:
            raise ValueError("operation")
        if op == "handoff" and not self.auth.capable(command.get("target_device_id")):
            raise web.HTTPForbidden(reason="enrolled control target required")
        if op in INTERACTIVE:
            if peer is None or not peer.ack:
                return {
                    "accepted": False,
                    "status": "refused",
                    "reason": "current WSS snapshot acknowledgement required",
                    "effect_admitted": False,
                }
            if op != "connect" and (
                not peer.connection or command.get("connection_generation") != peer.connection
            ):
                return {
                    "accepted": False,
                    "status": "refused",
                    "reason": "connection fence",
                    "effect_admitted": False,
                }
        result = await self.call(command, device["id"])
        if peer and op == "connect" and result.get("accepted"):
            peer.connection = result["connection_generation"]
        return result

    async def operation(self, request):
        command = await self.body(request)
        op = request.path.removeprefix("/api/")
        if "op" in command:
            raise ValueError("route supplies operation")
        return web.json_response(await self.dispatch(dict(command, op=op), request["device"]))

    def enqueue(self, peer, value):
        op = value["command"].get("op")
        if not peer.ack and op not in {"pause", "stop"}:
            peer.put(
                {
                    "kind": "result",
                    "id": value["id"],
                    "accepted": False,
                    "status": "refused",
                    "reason": "acknowledge current snapshot",
                    "effect_admitted": False,
                }
            )
            return
        if op == "input":
            if peer.latest_input:
                peer.put(
                    {
                        "kind": "result",
                        "id": peer.latest_input["id"],
                        "accepted": False,
                        "status": "superseded",
                        "effect_admitted": False,
                    }
                )
            peer.latest_input = value
            if not peer.input_task:
                peer.input_task = asyncio.create_task(self.inputs(peer))
                peer.tasks.add(peer.input_task)
                peer.input_task.add_done_callback(peer.tasks.discard)
            return
        lane = "protective" if op in {"pause", "stop"} else "normal"
        if peer.commands[lane] >= (2 if lane == "protective" else 6):
            peer.put(
                {
                    "kind": "result",
                    "id": value["id"],
                    "accepted": False,
                    "status": "refused",
                    "reason": "inflight bound",
                    "effect_admitted": False,
                }
            )
            return
        peer.commands[lane] += 1
        task = asyncio.create_task(self.execute_peer(peer, value, lane))
        peer.tasks.add(task)
        task.add_done_callback(peer.tasks.discard)

    async def inputs(self, peer):
        try:
            while peer.latest_input and not peer.closed:
                value, peer.latest_input = peer.latest_input, None
                await self.execute_peer(peer, value)
        finally:
            peer.input_task = None

    async def execute_peer(self, peer, value, lane=None):
        try:
            device = self.auth.resolve(peer.token)
            if not device:
                result = {
                    "accepted": False,
                    "status": "refused",
                    "reason": "revoked device",
                    "effect_admitted": False,
                }
            elif not peer.ack and value["command"].get("op") not in {"pause", "stop"}:
                result = {
                    "accepted": False,
                    "status": "refused",
                    "reason": "acknowledge current snapshot",
                    "effect_admitted": False,
                }
            else:
                result = await self.dispatch(value["command"], device, peer)
        except web.HTTPException as exc:
            result = {"accepted": False, "status": "refused", "reason": exc.reason, "effect_admitted": False}
        except (ValueError, TypeError, KeyError):
            result = {
                "accepted": False,
                "status": "refused",
                "reason": "malformed command",
                "effect_admitted": False,
            }
        except (OSError, TimeoutError, asyncio.IncompleteReadError):
            result = unknown_outcome()
        finally:
            if lane:
                peer.commands[lane] -= 1
        peer.put(dict(result, kind="result", id=value["id"]))

    async def websocket(self, request):
        if self.pending_ws + len(self.peers) >= 4:
            raise web.HTTPTooManyRequests()
        self.pending_ws += 1
        pending = True
        ws = web.WebSocketResponse(
            timeout=0.5, max_msg_size=CONTROL_LIMIT, heartbeat=2, compress=False, writer_limit=4096
        )
        peer = None
        try:
            # Bound transport buffering as well as serialized application output.
            request.transport.set_write_buffer_limits(high=4096, low=1024)
            request.transport.get_extra_info("socket").setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
            await ws.prepare(request)
            first = await asyncio.wait_for(ws.receive(), 1)
            if first.type != WSMsgType.TEXT:
                raise ValueError("ticket required")
            payload = strict_json(first.data)
            if (
                not isinstance(payload, dict)
                or set(payload) != {"ticket"}
                or not isinstance(payload["ticket"], str)
                or len(payload["ticket"]) > 128
            ):
                raise ValueError("ticket")
            key = digest(payload["ticket"])
            record = self.tickets.get(key)
            device = self.auth.resolve(request["token"])
            if (
                not record
                or record[0] != request["device"]["id"]
                or record[1] <= time.monotonic()
                or not device
            ):
                raise ValueError("invalid ticket")
            del self.tickets[key]  # No await between verification and single-use consume.
            peer = Peer(ws, device["id"], request["token"])
            self.peers.add(peer)
            self.pending_ws -= 1
            pending = False
            peer.revision = self.next_revision()
            peer.put({"kind": "snapshot", "revision": peer.revision, "snapshot": await self.current()}, True)
            count, since = 0, time.monotonic()
            async for message in ws:
                if message.type != WSMsgType.TEXT:
                    break
                now = time.monotonic()
                if now - since >= 1:
                    count, since = 0, now
                count += 1
                if count > 40:
                    break
                device = self.auth.resolve(peer.token)
                if not device:
                    break
                value = strict_json(message.data)
                if isinstance(value, dict) and set(value) == {"ack"}:
                    if value["ack"] != peer.revision:
                        raise ValueError("snapshot acknowledgement")
                    peer.ack = True
                    continue
                if (
                    not isinstance(value, dict)
                    or set(value) != {"id", "command"}
                    or not isinstance(value["id"], str)
                    or len(value["id"]) > 64
                    or not isinstance(value["command"], dict)
                ):
                    raise ValueError("command envelope")
                self.enqueue(peer, value)
        except (
            ValueError,
            TypeError,
            KeyError,
            TimeoutError,
            ConnectionError,
            OSError,
            asyncio.IncompleteReadError,
        ):
            await close_socket(ws, request.transport, 1008)
        finally:
            if pending:
                self.pending_ws -= 1
            if peer:
                self.peers.discard(peer)
                # Only an explicitly connected controller releases intent; spectators have no authority.
                if peer.connection:
                    try:
                        state = await self.current()
                        controller = state.get("controller")
                        if (
                            controller
                            and controller["device_id"] == peer.device
                            and state.get("run", {}).get("recipe") == "fake-interactive"
                        ):
                            await self.call(
                                {
                                    "op": "release_input",
                                    "run_id": state["run"]["run_id"],
                                    "service_incarnation": state["service_incarnation"],
                                    "controller_generation": controller["controller_generation"],
                                    "connection_generation": peer.connection,
                                },
                                peer.device,
                            )
                    except (OSError, ValueError, TimeoutError, asyncio.IncompleteReadError):
                        pass
                await peer.close()
            await close_socket(ws, request.transport)
        return ws

    async def publish(self):
        while True:
            await asyncio.sleep(0.1)
            try:
                snapshot = await self.current()
                controller = snapshot.get("controller")
                if controller and not self.auth.capable(controller["device_id"]):
                    await self.fence(controller["device_id"])
                    snapshot = await self.current()
                for peer in list(self.peers):
                    if not self.auth.resolve(peer.token):
                        await self.fence(peer.device)
                    else:
                        peer.put(
                            dict(
                                {"kind": "snapshot", "snapshot": snapshot},
                                **({"revision": peer.revision} if not peer.ack else {}),
                            ),
                            True,
                        )
            except (OSError, ValueError, TimeoutError, asyncio.IncompleteReadError):
                for peer in list(self.peers):
                    peer.put({"kind": "unavailable"}, True)

    async def start(self, app):
        self.publisher = asyncio.create_task(self.publish())

    async def cleanup(self, app):
        self.publisher.cancel()
        await asyncio.gather(self.publisher, return_exceptions=True)
        for peer in list(self.peers):
            await close_socket(peer.ws, peer.ws._writer.transport, 1001)
            await peer.close()


def validate_listener(host, port, origin):
    """Explicit single-interface LAN opt-in; default development loopback preserved."""
    address = ipaddress.ip_address(host)
    lan = any(
        address in ipaddress.ip_network(network)
        for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
    )
    if host != "127.0.0.1" and not lan:
        raise ValueError("listener requires loopback or one explicit private LAN IPv4 address")
    if not (port == 0 or 1024 <= port <= 65535):
        raise ValueError("unprivileged listener port required")
    if origin is None:
        if host != "127.0.0.1":
            raise ValueError("LAN listener requires a separately configured exact HTTPS origin")
        return None
    parsed = urlsplit(origin)
    if (
        not port
        or parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.port != port
        or not parsed.hostname
        or parsed.hostname != parsed.hostname.lower()
        or not re.fullmatch(r"[a-z0-9]+(?:[a-z0-9.-]*[a-z0-9])?", parsed.hostname)
        or ".." in parsed.hostname
        or origin != f"https://{parsed.hostname}:{port}"
    ):
        raise ValueError("exact HTTPS origin with matching configured port required")
    return origin


async def run_gateway(args):
    origin = validate_listener(args.host, args.port, getattr(args, "origin", None))
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain(args.cert, args.key)
    gateway = Gateway(args.owner_state, args.auth_state)
    runner = web.AppRunner(
        gateway.app,
        access_log=None,
        shutdown_timeout=1,
        max_line_size=2048,
        max_field_size=2048,
        read_bufsize=4096,
        keepalive_timeout=5,
    )
    await runner.setup()
    site = web.TCPSite(runner, args.host, args.port, ssl_context=tls)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    gateway.origin = origin or f"https://127.0.0.1:{port}"
    # Ready metadata contains no credential, pairing code or private IPC address.
    private_json(args.ready_file, {"url": gateway.origin, "pid": os.getpid()})
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


def main():
    parser = argparse.ArgumentParser(description="Explicit loopback fake HTTPS gateway/local enrollment")
    subs = parser.add_subparsers(dest="mode", required=True)
    gateway = subs.add_parser("gateway")
    for name in ("owner-state", "auth-state", "cert", "key", "ready-file"):
        gateway.add_argument("--" + name, required=True)
    gateway.add_argument("--host", default="127.0.0.1")
    gateway.add_argument("--origin", help="Exact HTTPS origin required for explicit private LAN listener")
    gateway.add_argument("--port", type=int, default=0)
    pair = subs.add_parser("pair")
    pair.add_argument("--auth-state", required=True)
    pair.add_argument("--control", action="store_true")
    revoke = subs.add_parser("revoke")
    revoke.add_argument("--auth-state", required=True)
    revoke.add_argument("--device-id", required=True)
    args = parser.parse_args()
    if args.mode == "gateway":
        asyncio.run(run_gateway(args))
    elif args.mode == "pair":
        store = AuthStore(args.auth_state)
        private_json(store.directory / "pairing.json", {"code": store.pair(args.control), "expires_in_s": 60})
    else:
        AuthStore(args.auth_state).revoke(args.device_id)


if __name__ == "__main__":
    main()
