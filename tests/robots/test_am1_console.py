"""Hardware-free checks for the loopback AM1 console shell."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.client import HTTPConnection
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("am1_console", ROOT / "tools" / "am1_console.py")
assert SPEC and SPEC.loader
console = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = console
SPEC.loader.exec_module(console)


class FakeAdapter:
    def __init__(self):
        self.operations = []
        self.body = []

    def state(self):
        return {"session_id": None, "phase": "idle", "events": []}

    def operation(self, payload):
        self.operations.append(payload)
        return {"accepted": True, "phase": "stopping"}

    def body_input(self, payload):
        self.body.append(payload)
        return {"accepted": True}


def request(server, method, path, *, body=None, cookie=None, csrf=None, origin=None, host=None):
    conn = HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
    headers = {"Host": host or f"127.0.0.1:{server.server_address[1]}"}
    if cookie:
        headers["Cookie"] = cookie
    if csrf:
        headers["X-AM1-CSRF"] = csrf
    if origin:
        headers["Origin"] = origin
    if body is not None:
        headers["Content-Type"] = "application/json"
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
    conn.request(method, path, body=body, headers=headers)
    response = conn.getresponse()
    data = response.read()
    result = response.status, dict(response.getheaders()), data
    conn.close()
    return result


@pytest.fixture
def running_console(tmp_path):
    credential = tmp_path / "camera-auth.json"
    credential.write_text(json.dumps({"username": "camera", "password": "private-test-value"}), encoding="utf-8")
    adapter = FakeAdapter()
    config = SimpleNamespace(browser_url="http://127.0.0.1:1984")
    server = console.ConsoleServer(("127.0.0.1", 0), config, credential, adapter)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, adapter
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def session_tokens(server):
    status, headers, page = request(server, "GET", "/")
    assert status == 200
    cookie = headers["Set-Cookie"].split(";", 1)[0]
    csrf = re.search(rb'<meta name="am1-csrf" content="([0-9a-f]+)"', page)
    assert csrf is not None
    return cookie, csrf.group(1).decode(), page


def test_open_is_read_only(running_console):
    server, adapter = running_console
    cookie, _, page = session_tokens(server)
    assert b"am1-camera-root" in page
    assert b"Control" in page
    assert adapter.operations == []
    status, _, data = request(server, "GET", "/api/state", cookie=cookie)
    assert status == 200 and json.loads(data)["phase"] == "idle"
    assert adapter.operations == []
    assert request(server, "GET", "/")[0] == 200  # Refresh attaches; it never starts.
    assert adapter.operations == []


def test_proxy_refuses_unlisted_target_and_never_exposes_credentials(running_console):
    server, _ = running_console
    cookie, _, page = session_tokens(server)
    for path in ("/camera/api/config", "/camera/api/streams", "/camera/http://other/", "/camera/api/frame.jpeg?src=wrong"):
        status, _, data = request(server, "GET", path, cookie=cookie)
        assert status in (400, 403, 404)
        assert b"private-test-value" not in data
    assert b"private-test-value" not in page
    assert request(server, "GET", "/camera/assets/app.js", cookie=cookie)[0] == 200


def test_foreign_origin_and_oversized_post_refused(running_console):
    server, adapter = running_console
    cookie, csrf, _ = session_tokens(server)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    payload = {"kind": "Stop", "session_id": "fake"}
    assert request(server, "POST", "/api/operation", body=payload, cookie=cookie, csrf=csrf,
                   origin="http://evil.invalid")[0] == 403
    assert request(server, "POST", "/api/operation", body=payload, cookie=cookie, csrf=csrf,
                   origin=url, host="evil.invalid")[0] == 403
    assert request(server, "POST", "/api/operation", body=b"x" * 5000, cookie=cookie, csrf=csrf,
                   origin=url)[0] == 413
    assert request(server, "POST", "/api/operation", body=payload, cookie=cookie,
                   origin=url)[0] == 403
    assert adapter.operations == []


def test_stalled_stream_cannot_starve_stop(tmp_path):
    release = threading.Event()

    class StalledCamera(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()
            release.wait(2)

    camera = ThreadingHTTPServer(("127.0.0.1", 0), StalledCamera)
    camera_thread = threading.Thread(target=camera.serve_forever, daemon=True)
    camera_thread.start()
    credential = tmp_path / "camera-auth.json"
    credential.write_text('{"username":"camera","password":"private-test-value"}', encoding="utf-8")
    adapter = FakeAdapter()
    config = SimpleNamespace(browser_url=f"http://127.0.0.1:{camera.server_address[1]}")
    server = console.ConsoleServer(("127.0.0.1", 0), config, credential, adapter)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        cookie, csrf, _ = session_tokens(server)
        stalled = threading.Thread(target=lambda: request(server, "GET", "/camera/api/stream.mjpeg?src=forward", cookie=cookie), daemon=True)
        stalled.start()
        time.sleep(0.05)
        start = time.monotonic()
        status, _, data = request(server, "POST", "/api/operation", body={"kind": "Stop", "session_id": "fake"},
                                  cookie=cookie, csrf=csrf, origin=f"http://127.0.0.1:{server.server_address[1]}")
        assert status == 200 and json.loads(data)["accepted"]
        assert time.monotonic() - start < 0.5
        assert adapter.operations == [{"kind": "Stop", "session_id": "fake"}]
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        camera.shutdown()
        camera.server_close()
        thread.join(timeout=3)
        camera_thread.join(timeout=3)


def test_small_mjpeg_part_is_forwarded_without_waiting_for_large_buffer(tmp_path):
    release = threading.Event()
    first_part = b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 4\r\n\r\n\xff\xd8\xff\xd9\r\n"

    class Camera(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()
            self.wfile.write(first_part)
            self.wfile.flush()
            release.wait(2)

    camera = ThreadingHTTPServer(("127.0.0.1", 0), Camera)
    camera_thread = threading.Thread(target=camera.serve_forever, daemon=True)
    camera_thread.start()
    credential = tmp_path / "camera-auth.json"
    credential.write_text('{"username":"camera","password":"private-test-value"}', encoding="utf-8")
    server = console.ConsoleServer(("127.0.0.1", 0),
                                   SimpleNamespace(browser_url=f"http://127.0.0.1:{camera.server_address[1]}"),
                                   credential, FakeAdapter())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        cookie, _, _ = session_tokens(server)
        conn = HTTPConnection("127.0.0.1", server.server_address[1], timeout=0.5)
        conn.request("GET", "/camera/api/stream.mjpeg?src=forward", headers={"Cookie": cookie})
        response = conn.getresponse()
        assert response.status == 200
        assert response.read(len(first_part)) == first_part
        conn.close()
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        camera.shutdown()
        camera.server_close()
        thread.join(timeout=3)
        camera_thread.join(timeout=3)
