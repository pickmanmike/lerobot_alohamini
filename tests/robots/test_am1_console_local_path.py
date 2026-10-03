"""Real loopback/frontend/AF_PIPE integration; only robot/session IO is fake."""
from __future__ import annotations

import base64
from collections import deque
import json
import io
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from examples.alohamini.am1_console_bridge import (
    AM1ConsoleBridgeClient, AM1ConsoleBridgeServer, CONSOLE_ARM_KEYS, CONSOLE_BODY_OBSERVATION_KEYS,
    make_console_action_sent_event, make_console_host_feedback_event, make_console_live_sample_event,
)
from tools.am1_console import ConsoleServer, ConsoleSessionAdapter


ROOT = Path(__file__).resolve().parents[2]


class FakeSessionIO:
    """External robot feedback and session lifetime, not the input bridge."""
    SESSION_ID_PATTERN = re.compile(r"[0-9]{8}T[0-9]{6}-[0-9a-f]{8}")
    SessionError = RuntimeError

    def __init__(self, *, live_duration_s=25):
        self.stopped = threading.Event()
        self.native = None
        self.error = None
        self.on_live = lambda: None
        self.live_duration_s = live_duration_s
        self.run_count = 0

    def parse_duration_seconds(self, value):
        return int(value)

    def validate_leader_selection(self, source, profile):
        assert source == "physical" and profile is None

    def request_stop(self, config, *, expected_session_id, wait):
        self.stopped.set()

    def run_start(self, repository, config, duration, **callbacks):
        self.stopped.clear()
        self.run_count += 1
        identity = f"20261002T000000-{self.run_count:08x}"
        pipe, auth = callbacks["console_prepare"](identity)
        callbacks["on_session_created"](identity)
        native = self.native = AM1ConsoleBridgeClient(pipe, auth, identity)
        emit = callbacks["emit"]
        telemetry_stop = threading.Event()
        telemetry_thread = None
        host_feedback = [{"state": "ready", "epoch": 0}]
        def telemetry():
            sequence = 0
            positions = {key: 12.3456789012345 for key in CONSOLE_ARM_KEYS}
            while not telemetry_stop.wait(.1):
                sequence += 1
                # Complete native packet shapes remain flowing while the main
                # fake robot/session waits for explicit current-gate Resume.
                sample = SimpleNamespace(arm_target=positions, follower_positions=positions,
                    observed_at=time.monotonic(), observation_sequence=sequence,
                    observation={key: 0.0 for key in CONSOLE_BODY_OBSERVATION_KEYS})
                feedback = {"version": 1, **host_feedback[0], "observation_id": sequence}
                for event in (
                    make_console_live_sample_event(sample, positions, raw_keys=CONSOLE_BODY_OBSERVATION_KEYS,
                        host_feedback=feedback, wall_ns=time.time_ns(), monotonic_now=time.monotonic()),
                    make_console_host_feedback_event(sample, feedback, wall_ns=time.time_ns(),
                                                    monotonic_now=time.monotonic()),
                    make_console_action_sent_event(positions, sequence=sequence, interval_ms=100,
                                                  wall_ns=time.time_ns()),
                ):
                    native.publish_telemetry(event)
        try:
            native.connect()
            for event in ("camera_ready", "host_ready"):
                emit({"event": event, "session_id": identity})
            for gate in ("sync_start", "live_start"):
                if not native.wait_gate(gate, cancel=self.stopped.is_set, timeout_s=8):
                    return 2
            host_feedback[0] = {"state": "active", "epoch": 0}  # Fake actual host acknowledgement.
            native.note_live_admitted(host_epoch=0)
            telemetry_thread = threading.Thread(target=telemetry, daemon=True)
            telemetry_thread.start()
            self.on_live()
            epoch, previous, last_output = 0, None, 0.0
            deadline = time.monotonic() + self.live_duration_s
            while not self.stopped.is_set() and time.monotonic() < deadline:
                paused = native.pause_requested()
                keys = sorted(native.get_action())
                sample = (paused, tuple(keys))
                if sample != previous:
                    emit({"event": "test_native_state", "paused": paused, "keys": keys,
                          "host_epoch": epoch, "wall_time_ns": time.time_ns()})
                    previous = sample
                if paused:
                    epoch += 1
                    host_feedback[0] = {"state": "paused", "epoch": epoch}
                    if not native.wait_gate("resume", host_epoch=epoch,
                                            cancel=self.stopped.is_set, timeout_s=8):
                        return 0 if self.stopped.is_set() else 2
                    epoch += 1
                    host_feedback[0] = {"state": "active", "epoch": epoch}  # Not the UI approval itself.
                    native.note_live_admitted(host_epoch=epoch)
                now = time.monotonic()
                if now - last_output >= .1:
                    emit({"event": "process_output", "session_id": identity, "source": "host",
                          "path": "/logs/am1-local-host-20261002-000000.log", "offset": 0,
                          "data_base64": base64.b64encode(b"FAKE HOST ordinary output\n").decode(),
                          "acquired_at_ns": time.time_ns()})
                    last_output = now
                time.sleep(.02)
            return 0
        except BaseException as exc:
            self.error = exc
            raise
        finally:
            telemetry_stop.set()
            if telemetry_thread is not None:
                telemetry_thread.join(1)
            native.disconnect()
            emit({"event": "cleanup", "cleanup_verified": True})


class FakeCamera(BaseHTTPRequestHandler):
    requests = 0
    encoded = io.BytesIO()
    Image.new("RGB", (32, 24), "navy").save(encoded, format="JPEG")
    jpeg = encoded.getvalue()

    def log_message(self, *_):
        pass

    def do_GET(self):
        type(self).requests += 1
        # Synthetic flat-colour frames, no device/image acquisition. The real
        # camera frontend and proxy still decode/status-poll alongside input.
        if self.path == "/status.json":
            roles = ("forward", "backward", "chest", "wrist_left", "wrist_right")
            body = json.dumps({"cameras": {role: {"state": "fresh", "configured": True,
                                                  "age_ms": 0, "sequence": int(time.monotonic()*getattr(self.server, "sequence_rate", 10)),
                                                  "fps": 1/getattr(self.server, "frame_period_s", .1),
                                                  "rotation_degrees": 0} for role in roles}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        elif self.path.startswith("/api/frame.jpeg"):
            time.sleep(getattr(self.server, "snapshot_delay_s", 0))
            body = self.jpeg
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("X-Frame-Sequence", str(int(time.monotonic()*getattr(self.server, "sequence_rate", 10))))
            self.send_header("X-Frame-Age-Ms", "0")
        elif self.path.startswith("/api/stream.mjpeg"):
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()
            try:
                for _ in range(getattr(self.server, "frame_count", 60)):
                    self.wfile.write((f"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: {len(self.jpeg)}\r\n"
                                      f"X-Frame-Sequence: {int(time.monotonic()*getattr(self.server, 'sequence_rate', 10))}\r\nX-Frame-Age-Ms: 0\r\n\r\n").encode()
                                     + self.jpeg + b"\r\n")
                    self.wfile.flush()
                    time.sleep(getattr(self.server, "frame_period_s", .1))
            except (ConnectionError, OSError):
                pass
            return
        else:
            body = b"Fake camera: no acquired image"
            self.send_response(503)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.mark.skipif(sys.platform != "win32", reason="Actual AF_PIPE is Windows-only")
@pytest.mark.parametrize("case", ["healthy", "navigation", "blur", "hidden", "body-delay",
                                      "body-reject", "body-denied", "state-reject", "state-delay", "pending-stop", "camera-delay", "approval-order"])
def test_browser_loopback_native_path(tmp_path, case):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True).returncode:
        pytest.skip("Existing Playwright runtime required; never install it in this test")
    session = FakeSessionIO()
    camera = ThreadingHTTPServer(("127.0.0.1", 0), FakeCamera)
    camera.daemon_threads = True
    if case == "camera-delay":
        session.on_live = lambda: setattr(camera, "snapshot_delay_s", .65)
    config = SimpleNamespace(browser_url=f"http://127.0.0.1:{camera.server_port}",
                             local_state_directory=tmp_path, windows_log_directory=tmp_path)
    auth = tmp_path / "fake-camera.json"
    auth.write_text(json.dumps({"username": "test-only", "password": "test-only"}))
    bridges = []
    def bridge_factory(*args):
        owner = AM1ConsoleBridgeServer(*args)
        bridges.append(owner)
        return owner
    adapter = ConsoleSessionAdapter(config, ROOT, session, bridge_factory=bridge_factory)
    received = []
    real_body_input = adapter.body_input
    def observed_body_input(payload):
        received.append((payload.get("seq"), time.monotonic(), payload.get("active")))
        return real_body_input(payload)
    adapter.body_input = observed_body_input
    server = ConsoleServer(("127.0.0.1", 0), config, auth, adapter)
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (camera, server)]
    before = FakeCamera.requests
    for thread in threads:
        thread.start()
    try:
        result = subprocess.run([node, str(ROOT / "tests/cameras/am1_console_local_driver.cjs"),
                                 f"http://127.0.0.1:{server.server_port}", case],
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=35)
        intervals = [(seq, round((at - previous[1])*1000), active)
                     for previous, (seq, at, active) in zip(received, received[1:])]
        assert result.returncode == 0, result.stdout + result.stderr + f"\nServer body arrivals: {intervals[-20:]}"
        assert session.error is None
        assert FakeCamera.requests > before
        assert adapter.wait(3), "session cleanup worker must terminate before verdict"
        final = adapter.state()
        assert final["phase"] == "complete"
        assert final["error"] is None
        assert final["cleanup_verified"] is True
        assert not session.native.is_connected
        assert not session.native._worker.is_alive()
        assert bridges and all(not owner._thread.is_alive() and not owner.auth_file.exists() for owner in bridges)
    finally:
        session.stopped.set()
        adapter.wait(3)
        for owner in (server, camera):
            owner.shutdown()
            owner.server_close()
        for thread in threads:
            thread.join(2)


class BoundedInputTrace:
    """Test-only ring, frozen one second after the first unexpected expiry."""

    def __init__(self):
        self.records = deque(maxlen=3000)
        self.lock = threading.Lock()
        self.first_expiry = None
        self.prior_active_body = None
        self.max_active_arrival_gap_ms = 0

    def add(self, event, **fields):
        now = time.monotonic()
        with self.lock:
            if self.first_expiry is not None and now > self.first_expiry + 1:
                return
            self.records.append({"event": event, "python_monotonic_s": now,
                                 "wall_time_ns": time.time_ns(), **fields})
            if event == "http_body":
                prior = self.prior_active_body
                if fields.get("active"):
                    if prior is not None and prior[0:2] == (fields.get("session_id"), fields.get("epoch")):
                        self.max_active_arrival_gap_ms = max(self.max_active_arrival_gap_ms, 1000*(now-prior[2]))
                    self.prior_active_body = (fields.get("session_id"), fields.get("epoch"), now)
                else:
                    self.prior_active_body = None
            elif event == "service_pause":
                self.prior_active_body = None
            if event == "service_pause" and fields.get("reason") == "expired browser input":
                if self.first_expiry is None:
                    self.first_expiry = now


def test_input_timing_trace_is_bounded_and_separates_deliberate_pause(monkeypatch):
    at = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: at[0])
    trace = BoundedInputTrace()
    fields = {"session_id": "test-only", "epoch": 1, "active": True}
    trace.add("http_body", **fields)
    at[0] = .1
    trace.add("http_body", **fields)
    trace.add("service_pause", reason="operator")
    at[0] = 10
    trace.add("http_body", **fields)
    assert trace.max_active_arrival_gap_ms == pytest.approx(100), "operator dwell is not input starvation"
    for _ in range(4000):
        trace.add("test_context")
    assert len(trace.records) == 3000
    trace.add("service_pause", reason="expired browser input")
    at[0] = 10.9
    trace.add("last_context")
    at[0] = 11.1
    trace.add("outside_first_fault_window")
    assert trace.records[-1]["event"] == "last_context"
    assert trace.first_expiry == 10


@pytest.mark.skipif(sys.platform != "win32", reason="Actual AF_PIPE is Windows-only")
@pytest.mark.parametrize("browser_mode", ["foreground", "in-app-capture"])
def test_foreground_full_page_input_timing(tmp_path, monkeypatch, browser_mode):
    """Automated nominal exercise vs attended timing-only capture; no expiry rescue."""
    from tools.am1_console import ConsoleHandler
    from examples.alohamini.am1_console_bridge import AM1ConsoleInputState
    import random

    external = os.environ.get("AM1_TIMING_EXTERNAL_BROWSER") == "1"
    if not external and os.environ.get("AM1_TIMING_FOREGROUND") != "1":
        pytest.skip("Visible desktop exercise is opt-in; never open an unattended desktop test")
    if (browser_mode == "in-app-capture") != external:
        pytest.skip("Choose one ordinary foreground browser comparison at a time")

    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True).returncode:
        pytest.skip("Existing Playwright runtime required; never install it in this test")
    trace = BoundedInputTrace()
    session = FakeSessionIO(live_duration_s=120)
    # Match the recorded 640x480 / roughly 15 fps and 30-38 KiB camera frames.
    # It contains deterministic synthetic colour noise only, not private imagery.
    encoded = io.BytesIO()
    pixels = random.Random(11).randbytes(80 * 60 * 3)
    Image.frombytes("RGB", (80, 60), pixels).resize((640, 480), Image.Resampling.BILINEAR).save(
        encoded, format="JPEG", quality=40)
    assert 30 * 1024 <= len(encoded.getvalue()) <= 38 * 1024
    class RepresentativeCamera(FakeCamera):
        jpeg = encoded.getvalue()
    camera = ThreadingHTTPServer(("127.0.0.1", 0), RepresentativeCamera)
    camera.daemon_threads = True
    camera.frame_count = 1800
    camera.frame_period_s = 1 / 15
    camera.sequence_rate = 1000  # Every primary frame must have a distinct sequence.
    config = SimpleNamespace(browser_url=f"http://127.0.0.1:{camera.server_port}",
                             local_state_directory=tmp_path, windows_log_directory=tmp_path)
    auth = tmp_path / "fake-camera.json"
    auth.write_text(json.dumps({"username": "test-only", "password": "test-only"}))
    bridges = []
    def bridge_factory(*args):
        owner = AM1ConsoleBridgeServer(*args)
        bridges.append(owner)
        return owner
    adapter = ConsoleSessionAdapter(config, ROOT, session, bridge_factory=bridge_factory)
    handler_arrival = threading.local()
    real_post = ConsoleHandler.do_POST
    def post(handler):
        handler_arrival.at = time.monotonic()
        return real_post(handler)
    monkeypatch.setattr(ConsoleHandler, "do_POST", post)
    real_get, real_reply = ConsoleHandler.do_GET, ConsoleHandler._reply
    def get(handler):
        if handler.path == "/assets/input-probe.js":
            if handler._base_check() and handler._valid_cookie():
                handler._reply(200, (ROOT / "tests/cameras/am1_console_input_probe.js").read_bytes(),
                               "text/javascript")
            else:
                handler._reply(403)
            return
        return real_get(handler)
    def reply(handler, status, body=b"", mime="text/plain; charset=utf-8", *, cookie=False):
        if status == 200 and mime.startswith("text/html"):
            body = body.replace(b"<head>", b'<head><script src="/assets/input-probe.js"></script>', 1)
        elif status == 200 and handler.path == "/assets/app.js":
            # Measure the complete synchronous DOM rebuild after readState's
            # awaits. Timer-return alone does not measure that continuation.
            assert body.count(b"renderSnapshot(state);") == 1, "instrument the actual diagnostic continuation"
            body = body.replace(b"renderSnapshot(state);", b"am1TestTiming.snapshotWork(() => renderSnapshot(state));", 1)
        return real_reply(handler, status, body, mime, cookie=cookie)
    monkeypatch.setattr(ConsoleHandler, "do_GET", get)
    monkeypatch.setattr(ConsoleHandler, "_reply", reply)
    real_body = adapter.body_input
    def body(payload):
        trace.add("http_body", session_id=payload.get("session_id"), seq=payload.get("seq"),
                  epoch=payload.get("epoch"), active=payload.get("active"),
                  handler_arrival_s=getattr(handler_arrival, "at", None))
        result = real_body(payload)
        trace.add("body_result", session_id=payload.get("session_id"), seq=payload.get("seq"),
                  accepted=result.get("accepted"), reason=result.get("reason"))
        return result
    adapter.body_input = body
    real_pause = AM1ConsoleInputState.request_pause
    def pause(state, cause, *, now=None):
        before = state.pause_sequence
        result = real_pause(state, cause, now=now)
        if state.pause_sequence != before:
            trace.add("service_pause", session_id=state.session_id,
                      seq=state.last_browser_seq, reason=cause,
                      age_ms=state.pause_evidence.get("input_age_ms"),
                      pending_gate=state.pending_gate)
        return result
    monkeypatch.setattr(AM1ConsoleInputState, "request_pause", pause)
    real_accept = AM1ConsoleBridgeClient.accept_message
    def accept(native, message, *, received_at):
        result = real_accept(native, message, received_at=received_at)
        if message.get("kind") == "lease":
            payload = message.get("payload", {})
            evidence = payload.get("pause_evidence") or {}
            trace.add("native_lease", session_id=native.session_id, pipe_seq=message.get("seq"),
                      browser_seq=evidence.get("input_sequence"), valid=payload.get("valid"),
                      reason=evidence.get("reason"), accepted=result,
                      native_received_s=received_at)
        return result
    monkeypatch.setattr(AM1ConsoleBridgeClient, "accept_message", accept)
    real_send = AM1ConsoleBridgeServer._send
    def send(owner, kind, payload):
        if kind == "lease":
            with owner.lock:
                seq, pipe_seq = owner.state.last_browser_seq, owner._send_seq + 1
            # This is a send-time state snapshot, not the source of an already
            # constructed lease. For a pause, payload evidence is authoritative.
            trace.add("pipe_send", session_id=owner.session_id, browser_seq_at_send=seq, pipe_seq=pipe_seq,
                      pause_browser_seq=(payload.get("pause_evidence") or {}).get("input_sequence"),
                      valid=payload.get("valid"))
        return real_send(owner, kind, payload)
    monkeypatch.setattr(AM1ConsoleBridgeServer, "_send", send)
    real_emit = adapter._emit
    def emit(event):
        if event.get("event") == "test_native_state":
            trace.add("native_consumer", paused=event.get("paused"), keys=event.get("keys"),
                      host_epoch=event.get("host_epoch"))
        return real_emit(event)
    adapter._emit = emit
    def full_snapshots():
        adapter._emit({"event": "system_sample", "acquired_at_ns": time.time_ns(),
            "metrics": {"cpu_percent": 24, "memory_percent": 23, "cpu_temp_c": 48,
                        "uptime_s": 36000, "storage_free_bytes": 20_000_000_000, "throttled_raw": "0x0"}})
    session.on_live = full_snapshots
    server = ConsoleServer(("127.0.0.1", 0), config, auth, adapter)
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (camera, server)]
    for thread in threads:
        thread.start()
    try:
        if external:
            print(f"OFFLINE_TIMING_URL=http://127.0.0.1:{server.server_port}/", flush=True)
            deadline = time.monotonic() + 115
            while not session.stopped.wait(.1) and time.monotonic() < deadline:
                pass
            assert session.stopped.is_set(), "the external fake session must receive actual page Stop"
        else:
            result = subprocess.run([node, str(ROOT / "tests/cameras/am1_console_timing_driver.cjs"),
                f"http://127.0.0.1:{server.server_port}", str(tmp_path / "browser-timing.json")],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=125)
            print(result.stdout)
            assert result.returncode == 0, result.stdout + result.stderr
        assert session.error is None
        assert adapter.wait(3)
        final = adapter.state()
        assert final["cleanup_verified"] is True
        assert final["phase"] == "complete" and final["final_exit_code"] == 0 and final["error"] is None
        assert trace.first_expiry is None, "unexpected accepted-input expiry is a nominal-usability failure"
        assert session.run_count == (1 if external else 2), "only deliberate Start creates a new owner"
        assert not session.native.is_connected and not session.native._worker.is_alive()
        assert all(not owner._thread.is_alive() and not owner.auth_file.exists() for owner in bridges)
    finally:
        session.stopped.set()
        adapter.wait(3)
        (tmp_path / "python-timing.json").write_text(json.dumps({
            "clock": "Python monotonic seconds; not browser performance.now",
            "jpeg_bytes": len(RepresentativeCamera.jpeg), "dimensions": [640, 480],
            "primary_fps": 15, "records": list(trace.records)}, indent=2))
        pauses = [event for event in trace.records if event["event"] == "service_pause"]
        print("PYTHON_TIMING_SUMMARY=" + json.dumps({"jpeg_bytes":len(RepresentativeCamera.jpeg),
              "max_continuous_active_arrival_gap_ms":trace.max_active_arrival_gap_ms, "pauses":pauses}))
        print(f"BOUNDED_TIMING_FOLDER={tmp_path}")
        for owner in (server, camera):
            owner.shutdown()
            owner.server_close()
        for thread in threads:
            thread.join(2)
