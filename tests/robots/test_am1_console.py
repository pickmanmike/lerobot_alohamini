"""Hardware-free checks for the loopback AM1 console shell."""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
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


def test_camera_health_uses_request_start_and_reports_failed_fetch(running_console, monkeypatch):
    server, adapter = running_console
    cookie, _, _ = session_tokens(server)
    recorded = []
    adapter.camera_status = lambda status, *, acquired_at_ns: recorded.append(("ok", acquired_at_ns)) or True
    adapter.camera_status_failed = lambda *, acquired_at_ns: recorded.append(("failed", acquired_at_ns))
    clock = [1_000_000_000]
    monkeypatch.setattr(console.time, "time_ns", lambda: clock[0])

    class Response:
        status = 200

        def getheader(self, name, default=None):
            return "14" if name == "Content-Length" else default

        def read(self, size):
            return b'{"cameras":{}}'

    class CameraConnection:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            clock[0] = 1_800_000_000
            return Response()

        def close(self):
            pass

    monkeypatch.setattr(console.http.client, "HTTPConnection", CameraConnection)
    assert request(server, "GET", "/camera/status.json", cookie=cookie)[0] == 200
    assert recorded == [("ok", 1_000_000_000)]

    class FailedConnection(CameraConnection):
        def getresponse(self):
            raise OSError("camera unavailable")

    clock[0] = 2_000_000_000
    monkeypatch.setattr(console.http.client, "HTTPConnection", FailedConnection)
    assert request(server, "GET", "/camera/status.json", cookie=cookie)[0] == 503
    assert recorded[-1] == ("failed", 2_000_000_000)


def test_exact_owned_session_logs_are_bounded_and_exportable(running_console, tmp_path):
    server, _ = running_console
    cookie, _, _ = session_tokens(server)
    session_id = "20261001T120000-12345678"
    result = tmp_path / f"am1-session-{session_id}"
    result.mkdir()
    (result / f"am1-local-windows-{session_id}.log").write_text("client line\n", encoding="utf-8")
    (result / "am1-local-host-20261001-120001.log").write_text("host line\n", encoding="utf-8")
    (result / "am1-camera-20261001-120001.log").write_text("camera line\n", encoding="utf-8")
    (result / "ssh-control.log").write_text("ssh line\n", encoding="utf-8")
    (result / "session-summary.json").write_text(json.dumps({"session_id": session_id,
        "remote_logs": ["/home/operator/am1-local-host-20261001-120001.log",
                        "/home/operator/am1-camera-20261001-120001.log"]}), encoding="utf-8")
    adapter = console.ConsoleSessionAdapter(
        SimpleNamespace(windows_log_directory=tmp_path), ROOT, session, bridge_factory=lambda *args: None)
    adapter._on_created(session_id)
    server.session_adapter = adapter
    for kind, expected in (("client", b"client line"), ("host", b"host line"),
                           ("camera", b"camera line"), ("ssh", b"ssh line"),
                           ("summary", b'"remote_logs"')):
        status, _, body = request(server, "GET", f"/api/log?kind={kind}&session_id={session_id}", cookie=cookie)
        assert status == 200 and expected in body
        status, headers, exported = request(server, "GET", f"/api/log?kind={kind}&session_id={session_id}&download=1", cookie=cookie)
        assert status == 200 and exported == body
        assert headers["Content-Disposition"].startswith("attachment;")
    assert request(server, "GET", f"/api/log?kind=../../private&session_id={session_id}", cookie=cookie)[0] in (400, 404)
    assert request(server, "GET", "/api/log?kind=client", cookie=cookie)[0] == 400
    (result / f"am1-local-windows-{session_id}.log").write_bytes(b"x" * 2_000_001)
    assert request(server, "GET", f"/api/log?kind=client&session_id={session_id}", cookie=cookie)[0] == 413


def test_log_request_is_bound_to_expected_session_during_transition(running_console, tmp_path):
    server, _ = running_console
    cookie, _, _ = session_tokens(server)
    first = "20261001T120000-12345678"
    second = "20261001T120100-87654321"
    for identity, line in ((first, "first"), (second, "second")):
        result = tmp_path / f"am1-session-{identity}"
        result.mkdir()
        (result / f"am1-local-windows-{identity}.log").write_text(line, encoding="utf-8")
    adapter = console.ConsoleSessionAdapter(
        SimpleNamespace(windows_log_directory=tmp_path), ROOT, session, bridge_factory=lambda *args: None)
    server.session_adapter = adapter
    adapter._on_created(first)
    assert request(server, "GET", f"/api/log?kind=client&session_id={first}", cookie=cookie)[2] == b"first"
    adapter._on_created(second)
    assert request(server, "GET", f"/api/log?kind=client&session_id={first}", cookie=cookie)[0] == 409
    assert request(server, "GET", f"/api/log?kind=client&session_id={second}", cookie=cookie)[2] == b"second"


def test_terminal_has_no_command_route(running_console):
    server, adapter = running_console
    cookie, csrf, _ = session_tokens(server)
    assert request(server, "GET", "/api/terminal", cookie=cookie)[0] == 404
    url = f"http://127.0.0.1:{server.server_address[1]}"
    assert request(server, "POST", "/api/command", body={"command": "anything"},
                   cookie=cookie, csrf=csrf, origin=url)[0] == 404
    assert adapter.operations == []


def test_log_capacity_cannot_starve_stop(running_console):
    server, adapter = running_console
    cookie, csrf, _ = session_tokens(server)
    assert server.log_slots.acquire(blocking=False)
    assert server.log_slots.acquire(blocking=False)
    try:
        assert request(server, "GET", "/api/log?kind=client", cookie=cookie)[0] == 503
        url = f"http://127.0.0.1:{server.server_address[1]}"
        status, _, _ = request(server, "POST", "/api/operation", body={"kind": "Stop", "session_id": "owned"},
                               cookie=cookie, csrf=csrf, origin=url)
        assert status == 200 and adapter.operations[-1]["kind"] == "Stop"
    finally:
        server.log_slots.release()
        server.log_slots.release()


def test_live_output_route_is_session_bound_and_cannot_starve_stop(running_console, tmp_path):
    server, _ = running_console
    cookie, csrf, _ = session_tokens(server)
    identity = "20261001T120000-deadbeef"
    adapter = console.ConsoleSessionAdapter(
        SimpleNamespace(windows_log_directory=tmp_path), ROOT, session, bridge_factory=lambda *args: None)
    adapter._on_created(identity)
    server.session_adapter = adapter
    status, _, body = request(server, "GET", f"/api/output?kind=host&session_id={identity}", cookie=cookie)
    assert status == 200 and json.loads(body)["state"] == "Unavailable"
    assert request(server, "GET", "/api/output?kind=host&session_id=20261001T120001-deadbeef", cookie=cookie)[0] == 409
    assert request(server, "GET", f"/api/output?kind=../../secret&session_id={identity}", cookie=cookie)[0] == 400
    assert request(server, "GET", f"/api/output?kind=host&session_id={identity}")[0] == 403
    server.session_adapter = FakeAdapter()
    assert server.log_slots.acquire(blocking=False) and server.log_slots.acquire(blocking=False)
    try:
        assert request(server, "GET", f"/api/output?kind=host&session_id={identity}", cookie=cookie)[0] == 503
        status, _, body = request(server, "POST", "/api/operation", cookie=cookie, csrf=csrf,
                                  origin=f"http://127.0.0.1:{server.server_address[1]}", body={"kind":"Stop"})
        assert status == 200 and json.loads(body)["accepted"]
    finally:
        server.log_slots.release()
        server.log_slots.release()


def test_operational_failure_does_not_hide_unverified_cleanup(tmp_path):
    adapter = console.ConsoleSessionAdapter(SimpleNamespace(), tmp_path, session, bridge_factory=lambda *args: None)
    adapter._emit({"event": "cleanup", "cleanup_verified": False, "operational_exit_code": 2})
    adapter._final_exit_code = 2
    adapter._phase = "failed"
    state = adapter.state()
    assert state["final_exit_code"] == 2
    assert state["cleanup_verified"] is False


def test_configured_source_pins_are_not_claimed_as_preflight_verified(tmp_path):
    config = SimpleNamespace(remote_session_head="a" * 40, remote_motor_head="b" * 40,
                             remote_camera_head="c" * 40)
    adapter = console.ConsoleSessionAdapter(config, tmp_path, session, bridge_factory=lambda *args: None)
    idle = adapter.state()
    assert idle["configured_source_pins"]["remote_motor_head"] == "b" * 40
    assert idle["verified_source_heads"] is None
    adapter._emit({"event": "preflight_passed", "sources": {"motor_source_head": "b" * 40}})
    assert adapter.state()["verified_source_heads"] == {"motor_source_head": "b" * 40}


def test_stop_requested_remains_stopping_until_cleanup(tmp_path):
    adapter = console.ConsoleSessionAdapter(SimpleNamespace(), tmp_path, session, bridge_factory=lambda *args: None)
    adapter._emit({"event": "host_ready"})
    adapter._emit({"event": "stop_requested"})
    adapter._emit({"event": "camera_ready"})  # A late readiness event must not undo Stop.
    assert adapter.state()["phase"] == "stopping"


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
    adapter._emit({"event": "host_feedback", "acquired_at_ns": time.time_ns(),
                   "host_state": "active", "host_epoch": 7, "observation_sequence": 99})
    adapter._emit({"event": "action_sent", "acquired_at_ns": time.time_ns(),
                   "action_sequence": 88, "requested_targets": {"arm_left_elbow_flex.pos": 3.0}})
    assert adapter.state()["telemetry"]["observation"]["host_state"] == "active"
    second = adapter.operation({"kind": "Start", "duration_seconds": 10})
    assert adapter.wait(3)
    assert first["session_id"] != second["session_id"]
    assert len(calls) == 2
    snapshot = adapter.state()["telemetry"]
    assert snapshot["observation"]["host_state"] is None
    assert snapshot["action"]["sequence"] is None
    assert all(event["event"] == "session_created" for event in adapter.state()["events"])


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


def test_console_body_has_one_token_epoch_and_sequence(monkeypatch, tmp_path):
    from examples.alohamini.am1_console_bridge import AM1ConsoleInputState

    session_id = "20261001T000000-1234abcd"
    finish = threading.Event()
    created = []

    class FakeBridge:
        pipe_name = r"\\.\pipe\fake-am1"
        auth_file = tmp_path / "auth"

        def __init__(self, identity, directory, token):
            assert identity == session_id and directory == tmp_path
            self.state = AM1ConsoleInputState(identity, token)
            self.closed = False
            created.append(self)

        def browser_keys(self, *, token, epoch, seq, keys, active):
            return self.state.browser_keys(token=token, epoch=epoch, seq=seq, keys=keys, active=active, now=1.0)

        def snapshot(self):
            return {"input_epoch": self.state.epoch, "input_lease": self.state.lease(now=1.0)["valid"],
                    "pending_gate": self.state.pending_gate}

        def close(self):
            self.closed = True

    def fake_start(_repo, _config, _duration, **kwargs):
        kwargs["console_prepare"](session_id)
        kwargs["on_session_created"](session_id)
        finish.wait(2)
        return 0

    monkeypatch.setattr(session, "run_start", fake_start)
    adapter = console.ConsoleSessionAdapter(SimpleNamespace(local_state_directory=tmp_path), ROOT, session,
                                            bridge_factory=FakeBridge)
    try:
        result = adapter.operation({"kind": "Start", "duration_seconds": 10})
        token = result["control_token"]
        assert len(created) == 1
        assert adapter.operation({"kind": "Start", "duration_seconds": 10}).get("control_token") is None
        assert not adapter.body_input({"session_id": session_id, "control_token": "wrong", "epoch": 1,
                                       "seq": 1, "keys": ["w"], "active": True})["accepted"]
        assert adapter.body_input({"session_id": session_id, "control_token": token, "epoch": 1,
                                   "seq": 1, "keys": ["w"], "active": True})["accepted"]
        assert not adapter.body_input({"session_id": session_id, "control_token": token, "epoch": 1,
                                       "seq": 1, "keys": ["u"], "active": True})["accepted"]
        assert adapter.state()["input_lease"] is True
    finally:
        finish.set()
        adapter.wait(3)
    assert created[0].closed


def test_bridge_cleanup_failure_retains_primary_session_error(monkeypatch, tmp_path):
    session_id = "20261001T000000-1234abcd"

    class BrokenBridge:
        pipe_name = r"\\.\pipe\fake-am1"
        auth_file = tmp_path / "auth"

        def __init__(self, *_): pass
        def close(self): raise OSError("pipe close failed")
        def snapshot(self): return {"input_epoch": 1, "input_lease": False}

    def fake_start(_repo, _config, _duration, **kwargs):
        kwargs["console_prepare"](session_id)
        kwargs["on_session_created"](session_id)
        raise RuntimeError("original startup failure")

    monkeypatch.setattr(session, "run_start", fake_start)
    adapter = console.ConsoleSessionAdapter(SimpleNamespace(local_state_directory=tmp_path), ROOT, session,
                                            bridge_factory=BrokenBridge)
    adapter.operation({"kind": "Start", "duration_seconds": 10})
    assert adapter.wait(3)
    state = adapter.state()
    assert "original startup failure" in state["error"]
    assert "pipe close failed" in state["error"]
    assert state["phase"] == "cleanup_unknown"


def test_native_client_launch_receives_pipe_paths_not_auth_secret(monkeypatch, tmp_path):
    commands = []

    class Process:
        pid = 123

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    def popen(command, **kwargs):
        commands.append(command)
        return Process()

    monkeypatch.setattr(session.subprocess, "Popen", popen)
    monkeypatch.setattr(session.shutil, "which", lambda _: "pwsh")
    config = SimpleNamespace(local_config=tmp_path / "am1.local.json")
    log_path = tmp_path / "client.log"
    log_path.write_text("AM1_CLIENT_EXIT_CODE=0\n", encoding="utf-8")
    client = session.WindowsClient(ROOT, config, lambda: None, tmp_path / "stop",
                                   console_pipe=r"\\.\pipe\private", console_auth_file=tmp_path / "auth",
                                   console_session_id="20261001T000000-1234abcd")
    assert client.run(duration_seconds=10, log_path=log_path, stop_requested=lambda: False) == 0
    command = commands[0]
    assert command[command.index("-ConsolePipe") + 1] == r"\\.\pipe\private"
    assert command[command.index("-ConsoleAuthFile") + 1] == str(tmp_path / "auth")
    assert command[command.index("-ConsoleSessionId") + 1] == "20261001T000000-1234abcd"
    assert "private-test-value" not in " ".join(command)


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 unavailable")
def test_local_powershell_command_passes_console_pipe_only_when_requested(tmp_path):
    auth = tmp_path / "private.auth"
    auth.write_text("not-a-real-secret", encoding="utf-8")
    helper = ROOT / "tools" / "run_am1.ps1"
    example = ROOT / "config" / "am1.local.example.json"
    script = f"""
. '{str(helper).replace("'", "''")}'
$config = Get-Content -LiteralPath '{str(example).replace("'", "''")}' -Raw | ConvertFrom-Json
$command = New-Am1WindowsCommand -Mode Local -Config $config -RepositoryRoot '{str(ROOT).replace("'", "''")}' `
 -LeftPort 'COM8' -RightPort 'COM7' -LocalDurationSeconds 10 `
 -StopRequestPath '{str(tmp_path / "stop").replace("'", "''")}' `
 -ConsolePipe '\\\\.\\pipe\\am1-test' -ConsoleAuthFile '{str(auth).replace("'", "''")}' `
 -ConsoleSessionId '20261001T000000-1234abcd'
$command.arguments | ConvertTo-Json -Compress
"""
    result = subprocess.run([shutil.which("pwsh"), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
                            cwd=ROOT, capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode == 0, result.stderr
    arguments = json.loads(result.stdout.splitlines()[-1])
    assert arguments[arguments.index("--console_pipe") + 1] == r"\\.\pipe\am1-test"
    assert arguments[arguments.index("--console_auth_file") + 1] == str(auth)
    assert arguments[arguments.index("--console_session_id") + 1] == "20261001T000000-1234abcd"
    assert "not-a-real-secret" not in result.stdout


def test_console_launcher_is_read_only_until_start(monkeypatch, tmp_path):
    auth = tmp_path / "camera-auth.json"
    auth.write_text(json.dumps({"username": "test", "password": "fake-only"}), encoding="utf-8")
    events = []
    config = SimpleNamespace(console_camera_auth_file=auth, local_state_directory=tmp_path,
                             browser_url="http://127.0.0.1:1984")

    class FakeServer:
        server_address = ("127.0.0.1", 8765)

        def __init__(self, config, auth_file, adapter, address):
            assert auth_file == auth and address == ("127.0.0.1", 8765)
            events.append("bound")

        def serve_forever(self): events.append("serving")
        def server_close(self): events.append("closed")

    monkeypatch.setattr(session.SessionConfig, "load", lambda _: config)
    monkeypatch.setattr(console, "ConsoleServer", FakeServer)
    monkeypatch.setattr(console, "ConsoleSessionAdapter", lambda *_: SimpleNamespace(
        state=lambda: {"session_id": None}, wait=lambda _: True))
    monkeypatch.setattr(console.webbrowser, "open", lambda url: events.append(("browser", url)))
    monkeypatch.setattr(session, "run_start", lambda *_a, **_kw: pytest.fail("page open must not start a session"))
    assert console.run_console(tmp_path / "config.json", session_module=session) == 0
    assert events == ["bound", ("browser", "http://127.0.0.1:8765/"), "serving", "closed"]


def test_console_port_collision_refuses_without_browser_or_session(monkeypatch, tmp_path):
    auth = tmp_path / "auth"
    auth.write_text("{}", encoding="utf-8")
    config = SimpleNamespace(console_camera_auth_file=auth)
    monkeypatch.setattr(session.SessionConfig, "load", lambda _: config)
    monkeypatch.setattr(console, "ConsoleSessionAdapter", lambda *_: object())
    monkeypatch.setattr(console, "ConsoleServer", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("in use")))
    monkeypatch.setattr(console.webbrowser, "open", lambda _: pytest.fail("collision must not open browser"))
    assert console.run_console(tmp_path / "config.json", session_module=session) == 2


def test_console_server_fault_requests_exact_stop_before_exit(monkeypatch, tmp_path):
    identity = "20261001T000000-1234abcd"
    events = []
    config = SimpleNamespace(console_camera_auth_file=tmp_path / "auth")

    class FakeServer:
        server_address = ("127.0.0.1", 8765)
        def __init__(self, *_): pass
        def serve_forever(self): raise RuntimeError("server fault")
        def server_close(self): events.append("closed")

    class FakeAdapter:
        def __init__(self, *_): self.stopped = False
        def state(self): return {"session_id": identity}
        def wait(self, _): return self.stopped
        def operation(self, payload):
            events.append(payload)
            self.stopped = True
            return {"accepted": True}

    monkeypatch.setattr(session.SessionConfig, "load", lambda _: config)
    monkeypatch.setattr(console, "ConsoleServer", FakeServer)
    monkeypatch.setattr(console, "ConsoleSessionAdapter", FakeAdapter)
    assert console.run_console(tmp_path / "config.json", no_browser=True, session_module=session) == 2
    assert events == ["closed", {"kind": "Stop", "session_id": identity}]


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 unavailable")
def test_powershell_console_command_uses_configured_python_without_start(tmp_path):
    helper = ROOT / "tools" / "run_am1_console.ps1"
    auth = tmp_path / "private-auth.json"
    auth.write_text("{}", encoding="utf-8")
    config = tmp_path / "session.json"
    config.write_text(json.dumps({"windows_python": sys.executable,
                                  "console_camera_auth_file": str(auth)}), encoding="utf-8")
    script = f"""
. '{str(helper).replace("'", "''")}'
$command = New-Am1ConsoleCommand -ConfigPath '{str(config).replace("'", "''")}' -NoBrowser
$command | ConvertTo-Json -Compress
"""
    result = subprocess.run([shutil.which("pwsh"), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
                            cwd=ROOT, capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode == 0, result.stderr
    command = json.loads(result.stdout.splitlines()[-1])
    assert command["executable"] == sys.executable
    assert command["arguments"][:2] == ["-m", "tools.am1_console"]
    assert "--no-browser" in command["arguments"]
    assert not any("session start" in str(arg) for arg in command["arguments"])
