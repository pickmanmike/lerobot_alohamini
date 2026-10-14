"""Private WHEP lifecycle through actual trusted TLS upstream responses."""

import asyncio
import importlib
import ssl
import subprocess

import pytest
from aiohttp import web

from tests.robots.am1_session_fixture import OPENSSL
from tools.am1_session_service import AuthStore


def test_whep_exact_resources_device_binding_bounds_and_replacement(tmp_path):
    assert importlib.util.find_spec("tools.am1_media_proxy"), "authenticated WHEP lifecycle missing"
    from tools.am1_media_proxy import WHEPProxy

    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    subprocess.run(
        [
            OPENSSL,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )

    async def scenario():
        requests = []
        created = 0

        async def upstream(r):
            nonlocal created
            requests.append((r.method, r.path, r.headers.get("If-Match"), await r.text()))
            assert r.headers.get("Authorization", "").startswith("Basic ")
            if r.method == "POST":
                created += 1
                return web.Response(
                    status=201,
                    text="v=0\r\n",
                    headers={
                        "Location": f"/p1-observer/whep/session{created}",
                        "ETag": '"tag"',
                        "Content-Type": "application/sdp",
                    },
                )
            return web.Response(status=204, headers={"ETag": '"tag2"'})

        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", upstream)
        runner = web.AppRunner(app)
        await runner.setup()
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert, key)
        site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=tls)
        await site.start()
        url = f"https://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/p1-observer/whep"
        auth = AuthStore(tmp_path / "auth")
        config = {"url": url, "ca": str(cert), "username": "reader", "password": "fixture"}
        proxy = WHEPProxy(auth, config)
        await proxy.start()
        try:
            result = await proxy.exchange("POST", "device-a", None, b"v=0\r\n", {})
            assert result[0] == 201 and result[2]["ETag"] == '"tag"'
            handle = result[2]["Location"].rsplit("/", 1)[1]
            assert "127.0.0.1" not in result[2]["Location"]
            with pytest.raises(web.HTTPNotFound):
                await proxy.exchange("PATCH", "device-b", handle, b"", {"If-Match": '"tag"'})
            with pytest.raises(web.HTTPBadRequest):
                proxy.resource_url("https://evil.invalid/a")
            with pytest.raises(web.HTTPBadRequest):
                proxy.resource_url("/p1-observer/whep/../secret")
            with pytest.raises(web.HTTPBadRequest):
                proxy.resource_url("/p1-observer/whep/session?secret=a")
            await proxy.close()
            proxy = WHEPProxy(auth, config)
            await proxy.start()
            assert (
                await proxy.exchange("PATCH", "device-a", handle, b"a=ice-ufrag:x", {"If-Match": '"tag"'})
            )[0] == 204
            assert requests[-1][2] == '"tag"'
            second = await proxy.exchange("POST", "device-b", None, b"v=0\r\n", {})
            second_handle = second[2]["Location"].rsplit("/", 1)[1]
            with pytest.raises(web.HTTPTooManyRequests):
                await proxy.exchange("POST", "device-c", None, b"v=0\r\n", {})
            assert (await proxy.exchange("DELETE", "device-b", second_handle, b"", {}))[0] == 204
            assert (await proxy.exchange("DELETE", "device-a", handle, b"", {}))[0] == 204
            with pytest.raises(web.HTTPNotFound):
                await proxy.exchange("DELETE", "device-a", handle, b"", {})
        finally:
            await proxy.close()
            await runner.cleanup()

    asyncio.run(scenario())


def test_media_mutations_keep_host_origin_csrf_and_body_boundaries(tmp_path):
    from aiohttp.test_utils import make_mocked_request

    from tools.am1_session_service import COOKIE, Gateway

    gateway = Gateway(tmp_path / "unused-owner", tmp_path / "auth")
    gateway.origin = "https://fixture.invalid"
    device = gateway.auth.enroll(gateway.auth.pair())

    async def scenario():
        for method, content_type in [
            ("POST", "application/sdp"),
            ("PATCH", "application/trickle-ice-sdpfrag"),
            ("DELETE", "application/trickle-ice-sdpfrag"),
        ]:
            base = {
                "Host": "fixture.invalid",
                "Origin": gateway.origin,
                "Cookie": COOKIE + "=" + device["token"],
                "X-AM1-CSRF": device["csrf"],
                "Content-Type": content_type,
                "Content-Length": "0",
            }
            for change, status in [
                ({"Host": "evil.invalid"}, 403),
                ({"Origin": "https://evil.invalid"}, 403),
                ({"X-AM1-CSRF": "bad"}, 403),
                ({"Cookie": ""}, 401),
                ({"Content-Length": "65537"}, 413),
            ]:
                reached = []

                async def handler(request, reached=reached):
                    reached.append(True)
                    return web.Response(status=204)

                request = make_mocked_request(
                    method, gateway.origin + "/api/media/opaque", headers=dict(base, **change)
                )
                response = await gateway.boundary(request, handler)
                assert response.status == status and not reached
            request = make_mocked_request(method, gateway.origin + "/api/media/opaque", headers=base)
            assert (await gateway.boundary(request, handler)).status == 204

    asyncio.run(scenario())
