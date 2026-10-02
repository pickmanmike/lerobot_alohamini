"""Real loopback/frontend/AF_PIPE integration; only robot/session IO is fake."""
from __future__ import annotations

import base64
import json
import io
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

    def __init__(self):
        self.stopped = threading.Event()
        self.native = None
        self.error = None
        self.on_live = lambda: None

    def parse_duration_seconds(self, value):
        return int(value)

    def validate_leader_selection(self, source, profile):
        assert source == "physical" and profile is None

    def request_stop(self, config, *, expected_session_id, wait):
        self.stopped.set()

    def run_start(self, repository, config, duration, **callbacks):
        identity = "20261002T000000-1234abcd"
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
            deadline = time.monotonic() + 25
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
                                                  "age_ms": 0, "sequence": int(time.monotonic()*10),
                                                  "rotation_degrees": 0} for role in roles}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        elif self.path.startswith("/api/frame.jpeg"):
            time.sleep(getattr(self.server, "snapshot_delay_s", 0))
            body = self.jpeg
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("X-Frame-Sequence", str(int(time.monotonic()*10)))
            self.send_header("X-Frame-Age-Ms", "0")
        elif self.path.startswith("/api/stream.mjpeg"):
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()
            try:
                for _ in range(60):
                    self.wfile.write((f"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: {len(self.jpeg)}\r\n"
                                      f"X-Frame-Sequence: {int(time.monotonic()*10)}\r\nX-Frame-Age-Ms: 0\r\n\r\n").encode()
                                     + self.jpeg + b"\r\n")
                    self.wfile.flush()
                    time.sleep(.1)
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
                                      "body-reject", "body-denied", "state-reject", "state-delay", "pending-stop", "camera-delay"])
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
