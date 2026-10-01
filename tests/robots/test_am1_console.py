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
SESSION_SPEC = importlib.util.spec_from_file_location("am1_session_console_test", ROOT / "tools" / "am1_session.py")
assert SESSION_SPEC and SESSION_SPEC.loader
session = importlib.util.module_from_spec(SESSION_SPEC)
sys.modules[SESSION_SPEC.name] = session
SESSION_SPEC.loader.exec_module(session)


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


def test_duplicate_start_is_same_session(monkeypatch, tmp_path):
    created = threading.Event()
    finish = threading.Event()
    starts = []
    session_id = "20261001T000000-1234abcd"

    def fake_start(_repository, _config, _duration, **kwargs):
        starts.append(kwargs)
        kwargs["on_session_created"](session_id)
        created.set()
        finish.wait(2)
        return 0

    monkeypatch.setattr(session, "run_start", fake_start)
    adapter = console.ConsoleSessionAdapter(SimpleNamespace(local_state_directory=tmp_path), ROOT, session)
    try:
        first = adapter.operation({"kind": "Start", "duration_seconds": 10, "leader_source": "physical"})
        assert created.is_set()
        second = adapter.operation({"kind": "Start", "duration_seconds": 10, "leader_source": "physical"})
        assert first["session_id"] == second["session_id"] == session_id
        assert len(starts) == 1
        assert adapter.state()["session_id"] == session_id
    finally:
        finish.set()
        adapter.wait(3)


def test_completed_console_can_start_next_session(monkeypatch, tmp_path):
    identifiers = iter(("20261001T000000-1234abcd", "20261001T000100-1234abcd"))
    calls = []

    def fake_start(_repository, _config, _duration, **kwargs):
        identity = next(identifiers)
        calls.append(identity)
        kwargs["on_session_created"](identity)
        return 0

    monkeypatch.setattr(session, "run_start", fake_start)
    adapter = console.ConsoleSessionAdapter(SimpleNamespace(local_state_directory=tmp_path), ROOT, session)
    first = adapter.operation({"kind": "Start", "duration_seconds": 10})
    assert adapter.wait(3)
    second = adapter.operation({"kind": "Start", "duration_seconds": 10})
    assert adapter.wait(3)
    assert first["session_id"] != second["session_id"]
    assert len(calls) == 2


def test_stop_targets_exact_active_owner_and_returns_before_cleanup(tmp_path, monkeypatch):
    session_id = "20261001T000000-1234abcd"
    state = tmp_path / "active.json"
    stop_path = tmp_path / f"stop-{session_id}"
    state.write_text(json.dumps({"session_id": session_id, "controller_pid": 42, "status": "active",
                                 "stop_request": str(stop_path)}), encoding="utf-8")
    monkeypatch.setattr(session, "_pid_running", lambda _: True)
    config = SimpleNamespace(local_state_directory=tmp_path)
    with pytest.raises(session.SessionError, match="identity"):
        session.request_stop(config, expected_session_id="wrong", wait=False)
    assert not stop_path.exists()
    start = time.monotonic()
    assert session.request_stop(config, expected_session_id=session_id, wait=False) == 0
    assert time.monotonic() - start < 0.5
    assert stop_path.read_text(encoding="utf-8").strip() == session_id


def test_real_ready_events_advance_without_stdin(tmp_path):
    class Remote:
        def __init__(self):
            self.calls = []

        def preflight(self):
            self.calls.append("preflight")
            return {"motor_source_head": "a" * 40}

        def start_camera(self):
            self.calls.append("camera")
            return {"event": "camera_ready", "camera_log": "/tmp/am1-camera.log", "browser_url": "http://example.invalid"}

        def start_host(self):
            self.calls.append("host")
            return {"event": "host_ready", "host_log": "/tmp/am1-host.log"}

        def stop(self):
            self.calls.append("stop")
            return {"cleanup_verified": True, "host_exit": 0, "camera_exit": 0}

        def fault(self):
            return None

    class Client:
        def run(self, **_):
            return 0

        def cleanup_status(self):
            return {"cleanup_verified": True}

    remote = Remote()
    gates, events = [], []
    coordinator = session.SessionCoordinator(
        remote=remote, client=Client(), open_browser=lambda _: None,
        collect_remote_log=lambda *_: (True, None),
        input_fn=lambda _: pytest.fail("console must not consume terminal Enter"),
        gate=lambda stage, evidence, cancel: gates.append((stage, evidence)) or True,
        emit=events.append,
    )
    outcome = coordinator.run(duration_seconds=10, session_id="test", session_directory=tmp_path,
                              client_log_path=tmp_path / "client.log", stop_requested=lambda: False)
    assert outcome.final_exit_code == 0
    assert remote.calls == ["preflight", "camera", "host", "stop"]
    assert [stage for stage, _ in gates] == ["camera_ready", "host_ready"]
    assert [event["event"] for event in events if event.get("event") in {"camera_ready", "host_ready"}] == ["camera_ready", "host_ready"]


def test_stop_at_camera_gate_preserves_fault(tmp_path):
    class Remote:
        def preflight(self):
            return {}

        def start_camera(self):
            return {"event": "camera_ready", "browser_url": "http://example.invalid"}

        def start_host(self):
            pytest.fail("host must not start after gate refusal")

        def stop(self):
            return {"cleanup_verified": True}

        def fault(self):
            return None

    coordinator = session.SessionCoordinator(
        remote=Remote(), client=object(), open_browser=lambda _: None,
        collect_remote_log=lambda *_: (True, None),
        input_fn=lambda _: pytest.fail("terminal input forbidden"),
        gate=lambda *_: False,
    )
    outcome = coordinator.run(duration_seconds=10, session_id="test", session_directory=tmp_path,
                              client_log_path=tmp_path / "client.log", stop_requested=lambda: False)
    assert outcome.final_exit_code == 130
    assert outcome.stop_reason == "explicit_stop"


def test_unknown_cleanup_not_stopped(monkeypatch, tmp_path):
    session_id = "20261001T000000-1234abcd"

    def fake_start(_repository, _config, _duration, **kwargs):
        kwargs["on_session_created"](session_id)
        kwargs["emit"]({"event": "cleanup", "cleanup_verified": False})
        return 3

    monkeypatch.setattr(session, "run_start", fake_start)
    adapter = console.ConsoleSessionAdapter(SimpleNamespace(local_state_directory=tmp_path), ROOT, session)
    adapter.operation({"kind": "Start", "duration_seconds": 10, "leader_source": "physical"})
    adapter.wait(3)
    state = adapter.state()
    assert state["phase"] == "cleanup_unknown"
    assert state["final_exit_code"] == 3
    assert state["phase"] != "stopped"
    stop_calls = []
    monkeypatch.setattr(session, "request_stop", lambda *args, **kwargs: stop_calls.append((args, kwargs)) or 0)
    assert adapter.operation({"kind": "Stop", "session_id": session_id})["accepted"] is False
    assert stop_calls == []
