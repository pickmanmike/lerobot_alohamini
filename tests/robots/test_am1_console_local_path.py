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
from tools.am1_console import ConsoleHandler, ConsoleServer, ConsoleSessionAdapter


ROOT = Path(__file__).resolve().parents[2]


class FakeSessionIO:
    """External robot feedback and session lifetime, not the input bridge."""
    SESSION_ID_PATTERN = re.compile(r"[0-9]{8}T[0-9]{6}-[0-9a-f]{8}")
    SessionError = RuntimeError

    def __init__(self, *, live_duration_s=25, variant="nominal"):
        self.stopped = threading.Event()
        self.native = None
        self.error = None
        self.on_live = lambda: None
        self.before_gate = lambda stage, emit: None
        self.live_duration_s = live_duration_s
        self.run_count = 0
        self.start_requests = []
        self.variant = variant
        self.startup_duration_s = 30 if variant in {"startup", "output"} else 0
        self.child = None
        self.reader = None
        self.process_records = []
        self.child_output = deque(maxlen=20)
        self.trace_event = lambda event: None
        self.emit_output = True
        self.output_records = []
        self.native_marker_delay_s = 0  # Explicit failure model; unchanged nominal workload.
        self.finalization_delay_s = 0
        self.native_closed_at = None

    def parse_duration_seconds(self, value):
        return int(value)

    def validate_leader_selection(self, source, profile):
        assert (source, profile) in {("physical", None), ("scripted", "ArmSmoke")}

    def request_stop(self, config, *, expected_session_id, wait):
        self.stopped.set()

    def run_start(self, repository, config, duration, **callbacks):
        if self.variant != "nominal":
            return self._run_child(repository, config, **callbacks)
        self.stopped.clear()
        self.run_count += 1
        self.start_requests.append((duration, callbacks.get("leader_source", "physical"), callbacks.get("motion_profile")))
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
            emit({"event": "preflight_passed", "sources": {"windows_source_head": "1" * 40}})
            for event in ("camera_ready", "host_ready"):
                emit({"event": event, "session_id": identity})
            if self.startup_duration_s:
                telemetry_thread = threading.Thread(target=telemetry, daemon=True)
                telemetry_thread.start()
                self._startup(native, emit)
                gates = ("live_start",)
            else:
                gates = ("sync_start", "live_start")
            for gate in gates:
                self.before_gate(gate, emit)
                if not native.wait_gate(gate, cancel=self.stopped.is_set, timeout_s=8):
                    return 2
            host_feedback[0] = {"state": "active", "epoch": 0}  # Fake actual host acknowledgement.
            native.note_live_admitted(host_epoch=0)
            if telemetry_thread is None:
                telemetry_thread = threading.Thread(target=telemetry, daemon=True)
                telemetry_thread.start()
            self.on_live()
            epoch, previous, last_output, output_offset = 0, None, 0.0, 0
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
                if self.emit_output and now - last_output >= .1:
                    data = b"FAKE HOST ordinary output\n"
                    emit({"event": "process_output", "session_id": identity, "source": "host",
                          "path": "/logs/am1-local-host-20261002-000000.log", "offset": output_offset,
                          "data_base64": base64.b64encode(data).decode(),
                          "acquired_at_ns": time.time_ns()})
                    output_offset += len(data)
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
            self.native_closed_at = time.monotonic()
            if self.finalization_delay_s:
                time.sleep(self.finalization_delay_s)
            emit({"event": "cleanup", "cleanup_verified": True})
            emit({"event": "session_complete", "session_id": identity,
                  "cleanup_verified": not native.is_connected and not native._worker.is_alive()})

    def _startup(self, native, emit):
        # Import only function code; all device boundaries below are synthetic
        # before run_startup_sync is invoked. Never construct a production robot.
        sys.path.insert(0, str(ROOT / "examples/alohamini"))
        from examples.alohamini.teleoperate_bi import run_startup_sync, ExternalStopRequested
        origin = {key: 9.3456789012345 for key in CONSOLE_ARM_KEYS}
        target = {key: 12.3456789012345 for key in CONSOLE_ARM_KEYS}
        started_at = time.monotonic()
        robot = SimpleNamespace(config=SimpleNamespace(connect_timeout_s=1, observation_request_window=3),
            observation_sequence=0, latest_observation_roundtrip_age_s=0, frame_count=0,
            max_step=0, all_body_zero=True, positions=dict(origin))
        def observe():
            robot.observation_sequence += 1
            robot.latest_observation_received_at = time.monotonic()
            robot.latest_am1_local_feedback = {"version": 1, "state": "ready", "epoch": -1,
                                                "observation_id": robot.observation_sequence}
            return dict(robot.positions)
        def send(action):
            body_zero = all(action[key] == 0 for key in ("x.vel", "y.vel", "theta.vel", "lift_axis.vel"))
            robot.all_body_zero &= body_zero
            assert body_zero
            robot.max_step = max(robot.max_step, max(abs(action[key]-robot.positions[key])
                                                    for key in CONSOLE_ARM_KEYS))
            robot.positions = {key: action[key] for key in CONSOLE_ARM_KEYS}
            robot.frame_count += 1
            if robot.frame_count == 1:
                emit({"event": "test_startup_phase", "phase": "ramp"})
            if robot.frame_count % 10 == 1:
                emit({"event": "test_startup_frame", "frame": robot.frame_count,
                      "elapsed_s": time.monotonic()-started_at, "all_body_zero": body_zero})
        def cancel():
            if self.stopped.is_set():
                raise ExternalStopRequested("synthetic owned child cancelled")
            if robot.frame_count and native.pause_requested():
                raise RuntimeError("synthetic startup refused after native input pause")
        def sleep(delay):
            if self.stopped.wait(delay):
                cancel()
        robot.get_observation = observe
        robot.send_action = send
        robot.retire_observation_requests = lambda: None
        emit({"event": "test_startup_phase", "phase": "prepare"})
        frozen, observation, _ = run_startup_sync(robot, SimpleNamespace(get_action=lambda: dict(target)),
            side="both", requested_duration_s=self.startup_duration_s, fps=10,
            max_start_mismatch=10, input_fn=lambda _: (_ for _ in ()).throw(AssertionError("no stdin gates")),
            monotonic=time.monotonic, sleep_fn=sleep, enter_confirmation=True,
            confirmation_gate=lambda stage: native.wait_gate(stage, cancel=self.stopped.is_set, timeout_s=8),
            cancel_check=cancel)
        assert frozen == observation == target
        emit({"event": "test_startup_phase", "phase": "complete"})
        emit({"event": "test_startup_summary", "frame_count": robot.frame_count,
              "elapsed_s": time.monotonic()-started_at, "all_body_zero": robot.all_body_zero,
              "max_step": robot.max_step, "observation_sequence": robot.observation_sequence})

    def _run_child(self, repository, config, **callbacks):
        """Same fake consumer, actual PS7 child and authenticated native pipe.

        Only the test shim is launched. No normal launcher/config is invoked.
        Like WindowsClient, retain cwd, inherited stdin and a new process group;
        stdout is captured for test evidence (not its ordinary foreground log).
        """
        self.stopped.clear()
        self.run_count += 1
        identity = f"20261002T000000-{self.run_count:08x}"
        pipe, auth = callbacks["console_prepare"](identity)
        callbacks["on_session_created"](identity)
        stop_path = config.local_state_directory / f"fake-native-stop-{self.run_count}"
        self.child = self.reader = None
        native_closed = threading.Event()
        replay = None
        def receive():
            for line in self.child.stdout:
                self.child_output.append(line)
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("event") == "test_native_lease":
                    self.trace_event(event)
                    continue
                if event.get("event") in {"test_wrapper_started", "test_child_launched", "test_process_started",
                                         "test_native_closed", "test_startup_summary"}:
                    self.process_records.append(event)
                if event.get("event") == "test_native_closed":
                    native_closed.set()
                elif event.get("event") == "test_live_started":
                    self.on_live()
                elif event.get("event") != "cleanup":
                    callbacks["emit"](event)
        command = [shutil.which("pwsh"), "-NoLogo", "-NoProfile", "-File",
            str(ROOT / "tests/robots/am1_console_fake_native.ps1"), "-Python", sys.executable,
            "-Pipe", pipe, "-Auth", str(auth), "-SessionId", identity,
            "-StopPath", str(stop_path), "-LiveDurationSeconds", str(self.live_duration_s),
            "-StartupDurationSeconds", str(self.startup_duration_s),
            "-MarkerDelaySeconds", str(self.native_marker_delay_s)]
        if self.variant == "output":
            command.append("-NoOutput")
            replay = SyntheticOutputReplay(config.local_state_directory / f"output-{self.run_count}",
                                           identity, callbacks["emit"], self.trace_event)
        try:
            if replay is not None:
                replay.start()
            callbacks["emit"]({"event": "test_process_launch", "session_id": identity,
                               "wall_time_ns": time.time_ns()})
            self.child = subprocess.Popen(command, cwd=repository, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
            self.reader = threading.Thread(target=receive, name="test-native-output", daemon=True)
            self.reader.start()
            while self.child.poll() is None:
                if self.stopped.wait(.02):
                    stop_path.touch()
                    self.child.wait(timeout=5)
            # The wrapper can die while its Python descendant still owns stdout.
            stop_path.touch()
            self.reader.join(2)
            assert not self.reader.is_alive(), "owned native output reader did not stop"
            assert native_closed.is_set(), "child did not verify native worker cleanup"
            closed = next(event for event in reversed(self.process_records)
                          if event["event"] == "test_native_closed")
            assert self.child.returncode == closed["native_exit_code"], "wrapper exit differs from native result"
            return self.child.returncode
        except BaseException as exc:
            self.error = exc
            raise
        finally:
            stop_path.touch()  # Always, including an already-exited wrapper.
            if self.child is not None and self.child.poll() is None:
                stop_path.touch()
                try:
                    self.child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    # Only this test's process tree; forced cleanup is a failure.
                    subprocess.run(["taskkill", "/PID", str(self.child.pid), "/T", "/F"],
                                   capture_output=True, timeout=5, check=False)
                    self.child.wait(timeout=3)
            if self.reader is not None:
                self.reader.join(2)
                if self.reader.is_alive():
                    wrapper = next((event for event in self.process_records
                                    if event["event"] == "test_wrapper_started"
                                    and event.get("session_id") == identity), None)
                    launched = next((event for event in self.process_records
                                     if event["event"] == "test_child_launched"
                                     and event.get("session_id") == identity and wrapper is not None
                                     and event.get("wrapper_pid") == wrapper["wrapper_pid"]), None)
                    if launched is not None:
                        # The owned PS shim recorded its .NET child on this
                        # private stdout channel. Kill that exact launch tree,
                        # including the venv redirector, even before Python can
                        # report its native startup marker.
                        subprocess.run(["taskkill", "/PID", str(launched["launch_pid"]), "/T", "/F"],
                                       capture_output=True, timeout=5, check=False)
                        self.reader.join(2)
                if not self.reader.is_alive():
                    self.child.stdout.close()  # Never block on a live BufferedReader.
            if replay is not None:
                replay.stop()
                self.output_records.append(replay.summary())
            event = {"event": "test_process_exit", "session_id": identity,
                     "wrapper_pid": self.child.pid if self.child else None,
                     "exit_code": self.child.returncode if self.child else None,
                     "wall_time_ns": time.time_ns()}
            self.process_records.append(event)
            callbacks["emit"](event)
            callbacks["emit"]({"event": "cleanup", "cleanup_verified": bool(
                native_closed.is_set() and self.reader is not None and not self.reader.is_alive()
                and self.error is None)})
            callbacks["emit"]({"event": "session_complete", "session_id": identity,
                "cleanup_verified": bool(native_closed.is_set() and self.reader is not None
                    and not self.reader.is_alive() and self.child is not None
                    and self.child.poll() is not None and self.error is None)})


class SyntheticOutputReplay:
    """Only log/transport IO is synthetic; use the actual forwarder and reader.

    Recorded envelope, not private content: 1365 B at 30 Hz, occasional three
    3205 B line bursts (about 42 KB/s), plus a 638 B camera status at 1 Hz.
    The ordinary 1536 B / 250 ms forwarder intentionally exposes skipped bytes.
    """
    def __init__(self, directory, identity, emit, trace=lambda event: None):
        from tools.am1_session import SSHRemote
        from tools.am1_session_remote import BestEffortReporter, RemoteSupervisor
        directory.mkdir(parents=True)
        self.identity = identity
        self.paths = {name: directory / f"synthetic-{name}.log" for name in ("host", "camera")}
        self.produced = dict.fromkeys(self.paths, 0)
        self.production_windows = {}
        self.bursts = 0
        self.forwarded = dict.fromkeys(self.paths, 0)
        self.max_chunk_bytes = 0
        self.headers = deque(maxlen=256)
        self.error = None
        self.done = threading.Event()
        read_fd, write_fd = os.pipe()
        os.set_blocking(write_fd, False)  # Same best-effort write contract as the supervisor.
        self.input = os.fdopen(read_fd, "r", encoding="utf-8")
        self.output = os.fdopen(write_fd, "w", encoding="utf-8", buffering=1)
        def receive(event):
            if event.get("event") == "process_output":
                width = len(base64.b64decode(event["data_base64"]))
                self.forwarded[event["source"]] += 1
                self.max_chunk_bytes = max(self.max_chunk_bytes, width)
                header = {"event": "output_received", "session_id": identity,
                          "source": event["source"], "offset": event["offset"], "bytes": width,
                          "acquired_at_ns": event["acquired_at_ns"],
                          "windows_received_at_ns": event["windows_received_at_ns"]}
                self.headers.append(header)
                trace(header)
            emit(event)
        self.remote = SSHRemote(SimpleNamespace(), identity, directory, telemetry_sink=receive)
        # Do not call SSHRemote.start/_command or RemoteSupervisor.preflight:
        # the only endpoint is this test-owned anonymous OS pipe.
        self.remote.process = SimpleNamespace(stdout=self.input)
        self.supervisor = RemoteSupervisor(SimpleNamespace(session_id=identity,
            state_directory=directory / "state", log_directory=directory,
            camera_head="0"*40, motor_head="0"*40, session_head="0"*40),
            BestEffortReporter(lambda event: os.write(self.output.fileno(),
                (json.dumps(event, separators=(",", ":")) + "\n").encode())))
        self.supervisor.children = {name: SimpleNamespace(log_path=path) for name, path in self.paths.items()}
        self.reader = threading.Thread(target=self.remote._read_events, name="test-real-event-reader", daemon=True)
        self.writer = threading.Thread(target=self._produce, name="test-output-producer", daemon=True)
        self.started_at = None
        self.ended_at = None

    @staticmethod
    def line(width):
        prefix, suffix = b'{"event":"synthetic_telemetry","padding":"', b'"}\n'
        return prefix + b"x"*(width-len(prefix)-len(suffix)) + suffix

    def _append(self, name, data):
        with self.paths[name].open("ab") as stream:
            stream.write(data)
        self.produced[name] += len(data)
        second = int(time.monotonic() - self.started_at)
        window = self.production_windows.setdefault(second, dict.fromkeys(self.paths, 0))
        window[name] += len(data)

    def _produce(self):
        self.started_at = time.monotonic()
        next_host = next_camera = self.started_at
        next_burst = self.started_at + 10
        try:
            while not self.done.is_set():
                now = time.monotonic()
                if now >= next_host:
                    self._append("host", self.line(1365))
                    next_host += 1/30
                if now >= next_camera:
                    self._append("camera", self.line(638))
                    next_camera += 1
                if now >= next_burst:
                    self._append("host", self.line(3205)*3)
                    self.bursts += 1
                    next_burst += 10
                self.supervisor.forward_output(now=now)
                self.done.wait(.005)
        except BaseException as exc:
            self.error = exc
        finally:
            self.ended_at = time.monotonic()

    def start(self):
        self.reader.start()
        self.writer.start()

    def stop(self):
        self.done.set()
        self.writer.join(3)
        assert not self.writer.is_alive(), "synthetic forwarder did not stop"
        self.output.close()  # EOF wakes actual SSHRemote._read_events.
        self.reader.join(3)
        assert not self.reader.is_alive(), "real event reader did not stop"
        self.input.close()
        assert self.error is None

    def summary(self):
        return {"session_id": self.identity, "produced_bytes": self.produced,
                "elapsed_s": self.ended_at-self.started_at, "bursts": self.bursts,
                "production_windows": self.production_windows,
                "forwarded_chunks": self.forwarded, "max_chunk_bytes": self.max_chunk_bytes,
                "reader_events": self.remote._control_evidence["event_received_count"],
                "headers": list(self.headers)}


def test_output_replay_uses_real_reader_bounds_and_offsets(tmp_path):
    identity = "20261002T000000-00000001"
    adapter = ConsoleSessionAdapter(SimpleNamespace(windows_log_directory=tmp_path), ROOT, FakeSessionIO())
    adapter._on_created(identity)
    replay = SyntheticOutputReplay(tmp_path / "logs", identity, adapter._emit)
    replay.start()
    try:
        deadline = time.monotonic() + 3
        while replay.forwarded["host"] < 4 and time.monotonic() < deadline:
            time.sleep(.01)
        headers = [event for event in replay.headers if event["source"] == "host"]
        assert len(headers) >= 4
        assert all(0 < event["bytes"] <= 1536 for event in headers)
        assert all(after["offset"] > before["offset"] for before, after in zip(headers, headers[1:]))
        assert any(after["offset"] > before["offset"] + before["bytes"]
                   for before, after in zip(headers, headers[1:])), "ordinary latest-output gaps must remain visible"
        output = adapter.read_output("host", identity)
        assert output["truncated"] is True and output["text"], "do not hide real skipped output"
        assert replay.remote._control_evidence["event_received_count"] >= len(headers)
        assert replay.forwarded["camera"] > 0
    finally:
        replay.stop()


def test_owned_native_launch_failure_closes_started_replay(tmp_path, monkeypatch):
    """A failed launcher must not leave test output producers/readers alive."""
    replays = []
    real_replay = SyntheticOutputReplay
    def create_replay(*args):
        replay = real_replay(*args)
        replays.append(replay)
        return replay
    failure = FileNotFoundError("test-only missing launcher")
    def fail_launch(*args, **kwargs):
        raise failure
    monkeypatch.setattr(sys.modules[__name__], "SyntheticOutputReplay", create_replay)
    monkeypatch.setattr(subprocess, "Popen", fail_launch)
    session = FakeSessionIO(variant="output")
    config = SimpleNamespace(local_state_directory=tmp_path)
    try:
        with pytest.raises(FileNotFoundError) as caught:
            session.run_start(ROOT, config, 5, console_prepare=lambda _: ("test-only", tmp_path / "auth"),
                on_session_created=lambda _: None, emit=lambda _: None)
        assert caught.value is failure
        assert replays and all(not item.writer.is_alive() and not item.reader.is_alive()
                              and item.input.closed and item.output.closed for item in replays)
    finally:
        # Keep the RED reproduction itself bounded and leak-free.
        for item in replays:
            if not item.output.closed:
                item.stop()


def test_output_replay_stops_when_reader_has_ended(tmp_path, monkeypatch):
    """A dead consumer must not trap the owned producer in a pipe write."""
    from tools.am1_session import SSHRemote
    real_reader = SSHRemote._read_events
    monkeypatch.setattr(SSHRemote, "_read_events", lambda remote: None)
    replay = SyntheticOutputReplay(tmp_path / "dead-reader", "20261002T000000-00000001", lambda _: None)
    replay.start()
    time.sleep(.8)  # Several real forwarded packets fill the undrained OS pipe.
    errors = []
    def stop():
        try:
            replay.stop()
        except BaseException as exc:
            errors.append(exc)
    closer = threading.Thread(target=stop, daemon=True)
    closer.start()
    try:
        closer.join(1)
        assert not closer.is_alive(), "shutdown must not wait for a dead output reader"
        assert not errors
        assert not replay.writer.is_alive() and replay.input.closed and replay.output.closed
    finally:
        # Drain the RED fixture so this intentional failure cannot leak handles.
        if closer.is_alive():
            drainer = threading.Thread(target=real_reader, args=(replay.remote,), daemon=True)
            drainer.start()
            closer.join(4)
            drainer.join(2)
        assert not closer.is_alive()


@pytest.mark.skipif(sys.platform != "win32", reason="Actual AF_PIPE is Windows-only")
def test_owned_native_wrapper_exit_stops_descendant(tmp_path):
    """Unexpected PS exit is a failure, but must still reap its native child."""
    session = FakeSessionIO(variant="native-child")
    config = SimpleNamespace(local_state_directory=tmp_path, windows_log_directory=tmp_path)
    adapter = ConsoleSessionAdapter(config, ROOT, session)
    assert adapter.operation({"kind": "Start", "duration_seconds": 5})["accepted"]
    try:
        deadline = time.monotonic() + 5
        while not any(event.get("event") == "test_process_started"
                      for event in session.process_records) and time.monotonic() < deadline:
            time.sleep(.01)
        assert session.process_records, "owned Python child must actually have started"
        session.child.kill()  # Only the exact test-owned wrapper, not the descendant.
        finished = adapter.wait(3)
        assert finished, "wrapper death must promptly request child Stop and finish cleanup"
        assert not session.reader.is_alive()
        assert any(event["event"] == "test_native_closed" for event in session.process_records)
        assert adapter.state()["cleanup_verified"] is False, "do not call an unexpected exit nominal success"
    finally:
        # Release the old implementation's blocked stdout before RED returns.
        (tmp_path / "fake-native-stop-1").touch()
        session.stopped.set()
        assert adapter.wait(5)


@pytest.mark.skipif(sys.platform != "win32", reason="Actual AF_PIPE is Windows-only")
def test_wrapper_exit_before_native_marker_reaps_owned_launch(tmp_path):
    """The venv child can hold stdout before it reports native startup."""
    session = FakeSessionIO(variant="native-child")
    session.native_marker_delay_s = 30
    config = SimpleNamespace(local_state_directory=tmp_path, windows_log_directory=tmp_path)
    adapter = ConsoleSessionAdapter(config, ROOT, session)
    assert adapter.operation({"kind": "Start", "duration_seconds": 5})["accepted"]
    launch = None
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            launch = next((e for e in session.process_records if e["event"] == "test_child_launched"), None)
            if launch is not None:
                break
            time.sleep(.01)
        assert launch is not None, "must identify the actual owned launch before killing its wrapper"
        assert not any(e["event"] == "test_process_started" for e in session.process_records)
        session.child.kill()
        assert adapter.wait(9), "early wrapper death must finish bounded cleanup"
        assert not session.reader.is_alive(), "must reap launch even without a native startup marker"
        assert session.child.stdout.closed
        assert adapter.state()["cleanup_verified"] is False, "forced cleanup is not nominal verification"
    finally:
        # Retain a safe exact-PID cleanup for the intended RED reproduction.
        if launch is not None and session.reader is not None and session.reader.is_alive():
            subprocess.run(["taskkill", "/PID", str(launch["launch_pid"]), "/T", "/F"],
                           capture_output=True, timeout=5, check=False)
            session.reader.join(3)
        session.stopped.set()
        assert adapter.wait(5)


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
@pytest.mark.parametrize("startup_seconds", [0, .2])
def test_owned_native_process_keeps_real_pipe_and_stops(tmp_path, startup_seconds):
    """Catch accidentally running the comparison consumer in the server process."""
    session = FakeSessionIO(live_duration_s=5)
    session.variant = "native-child"
    session.startup_duration_s = startup_seconds
    config = SimpleNamespace(local_state_directory=tmp_path, windows_log_directory=tmp_path)
    adapter = ConsoleSessionAdapter(config, ROOT, session)
    launched = adapter.operation({"kind": "Start", "duration_seconds": 5})
    assert launched["accepted"], launched
    identity, token = launched["session_id"], launched["control_token"]
    sequence = 0
    saw_keys = False
    first_live_sequence = None
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            state = adapter.state()
            if "input_epoch" not in state:
                assert state["phase"] not in {"failed", "complete"}, state
                time.sleep(.01)
                continue
            sequence += 1
            if state["phase"] == "live" and first_live_sequence is None:
                first_live_sequence = sequence
            result = adapter.body_input({"session_id": identity, "control_token": token,
                "epoch": state["input_epoch"], "seq": sequence, "active": True,
                "keys": ["w"] if first_live_sequence is not None and sequence > first_live_sequence + 3 else []})
            assert result["accepted"]
            gate = state.get("pending_gate")
            if gate is not None and gate[0] in {"sync_start", "live_start"}:
                adapter.operation({"kind": "Resume", "session_id": identity, "control_token": token,
                                   "gate_stage": gate[0], "host_epoch": gate[1]})
            saw_keys |= any(event.get("event") == "test_native_state" and event.get("keys") == ["w"]
                            for event in state["events"])
            if saw_keys:
                break
            time.sleep(.05)
        assert saw_keys, "the real native pipe consumer must receive the body input: " + repr(session.child_output)
        process_events = [event for event in adapter.state()["events"]
                          if event.get("event") == "test_process_started"]
        assert process_events and process_events[-1]["native_pid"] != os.getpid(), (
            "the native comparison is missing its separate owned process")
        wrappers = [event for event in session.process_records if event["event"] == "test_wrapper_started"]
        launches = [event for event in session.process_records if event["event"] == "test_child_launched"]
        assert wrappers and launches and launches[-1]["wrapper_pid"] == wrappers[-1]["wrapper_pid"]
        assert launches[-1]["launch_pid"] in (process_events[-1]["native_pid"], process_events[-1]["parent_pid"]), (
            "record actual owned launch ancestry, including the Windows venv redirector")
        output = adapter.read_output("host", identity)
        assert output["truncated"] is False, "small continuous chunks must not create false byte gaps"
        assert output["text"].count("FAKE HOST ordinary output") >= 2
        if startup_seconds:
            summaries = [event for event in adapter.state()["events"]
                         if event.get("event") == "test_startup_summary"]
            assert summaries, "representative startup must execute actual bounded sync, not immediate gates"
            assert summaries[-1]["frame_count"] == 5
            assert summaries[-1]["elapsed_s"] >= .2
            assert summaries[-1]["all_body_zero"] is True
            assert summaries[-1]["max_step"] <= .75
    finally:
        session.stopped.set()
        assert adapter.wait(5), "owned consumer must terminate before returning"
    assert session.child.poll() == 0
    assert not session.reader.is_alive()
    assert adapter.state()["cleanup_verified"] is True


@pytest.mark.parametrize("duration", ["0", "1800", "NaN"])
def test_bench_refuses_unqualified_native_duration_before_browser(tmp_path, duration):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True).returncode:
        pytest.skip("Node/Playwright unavailable")
    evidence = tmp_path / "bench.json"
    result = subprocess.run([node, str(ROOT / "tools/am1_reliability_bench.cjs"),
        "http://127.0.0.1:1/", "ArmSmoke", "AM1-RELIABILITY-01-arm-01", str(evidence), "1" * 40, duration],
        capture_output=True, text=True, timeout=10)
    assert result.returncode == 1 and "Native duration must be an established finite scenario" in result.stderr
    assert not evidence.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Actual AF_PIPE is Windows-only")
@pytest.mark.parametrize("case", ["healthy", "navigation", "blur", "hidden", "body-delay",
                                      "body-presence-loss", "short-browser-stall",
                                      "body-reject", "body-denied", "state-reject", "state-delay", "pending-stop", "camera-delay", "approval-order", "startup-approval", "bench-body", "bench-arm", "bench-arm-short", "bench-pause", "bench-foreign", "bench-monitor-failure", "bench-camera-loss", "bench-finalize", "bench-finalize-stall", "bench-finalize-late-result"])
def test_browser_loopback_native_path(tmp_path, case):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True).returncode:
        pytest.skip("Existing Playwright runtime required; never install it in this test")
    session = FakeSessionIO()
    if case.startswith("bench-"):
        session.live_duration_s = 12
    if case == "bench-finalize":
        session.finalization_delay_s = 1.2
    if case == "bench-finalize-stall":
        session.finalization_delay_s = 12
    if case == "bench-finalize-late-result":
        session.finalization_delay_s = 9.5
    startup_release = threading.Event()
    if case == "startup-approval":
        def before_gate(stage, emit):
            if stage == "live_start":
                emit({"event": "test_before_live_gate"})
                assert startup_release.wait(5), "frontend must deliver a real startup blur before requesting the gate"
        session.before_gate = before_gate
    camera = ThreadingHTTPServer(("127.0.0.1", 0), FakeCamera)
    camera.daemon_threads = True
    if case == "camera-delay":
        session.on_live = lambda: setattr(camera, "snapshot_delay_s", .65)
    if case == "bench-camera-loss":
        session.on_live = lambda: threading.Timer(1, lambda: setattr(camera, "snapshot_delay_s", 3)).start()
    config = SimpleNamespace(browser_url=f"http://127.0.0.1:{camera.server_port}",
                             local_state_directory=tmp_path, windows_log_directory=tmp_path,
                             windows_session_head="1" * 40)
    auth = tmp_path / "fake-camera.json"
    auth.write_text(json.dumps({"username": "test-only", "password": "test-only"}))
    bridges = []
    def bridge_factory(*args):
        owner = AM1ConsoleBridgeServer(*args)
        bridges.append(owner)
        return owner
    adapter = ConsoleSessionAdapter(config, ROOT, session, bridge_factory=bridge_factory)
    if case == "bench-pause":
        session.on_live = lambda: adapter._bridge.request_pause("operator")
    if case == "bench-foreign":
        real_operation = adapter.operation
        def competing_start(payload):
            if payload.get("kind") == "Start":
                real_operation({"kind": "Start", "duration_seconds": 12})
            return real_operation(payload)
        adapter.operation = competing_start
    received = []
    real_body_input = adapter.body_input
    def observed_body_input(payload):
        received.append((payload.get("seq"), time.monotonic(), payload.get("active")))
        result = real_body_input(payload)
        if not payload.get("active") and payload.get("release_reason") == "window-blur":
            startup_release.set()  # Only after the real HTTP owner received the release.
        return result
    adapter.body_input = observed_body_input
    server = ConsoleServer(("127.0.0.1", 0), config, auth, adapter)
    if case == "bench-monitor-failure":
        failures = [0]
        session.on_live = lambda: failures.__setitem__(0, 2)
        class FailedBenchReads(ConsoleHandler):
            def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler requires this method name.
                if self.path == "/api/state" and self.headers.get("X-AM1-Bench-Monitor") and failures[0]:
                    failures[0] -= 1
                    self.send_error(503, "Fake monitor read failure")
                    return
                super().do_GET()
        server.RequestHandlerClass = FailedBenchReads
    if case == "bench-finalize-late-result":
        delayed = [False]
        class LateTerminalRead(ConsoleHandler):
            def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler requires this method name.
                if (self.path == "/api/state" and self.headers.get("X-AM1-Bench-Monitor")
                        and session.native_closed_at is not None and not delayed[0]
                        and time.monotonic() - session.native_closed_at >= 9):
                    delayed[0] = True
                    time.sleep(1.5)
                super().do_GET()
        server.RequestHandlerClass = LateTerminalRead
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (camera, server)]
    before = FakeCamera.requests
    for thread in threads:
        thread.start()
    try:
        arm_bench = case in {"bench-arm", "bench-arm-short"}
        driver = ([node, str(ROOT / "tools/am1_reliability_bench.cjs"),
                   f"http://127.0.0.1:{server.server_port}",
                   "ArmSmoke" if arm_bench else "BodyPressRelease",
                   "AM1-RELIABILITY-01-arm-01" if arm_bench else "AM1-RELIABILITY-01-body-01",
                   str(tmp_path / "bench.json"), "1" * 40]
                  if case.startswith("bench-") else
                  [node, str(ROOT / "tests/cameras/am1_console_local_driver.cjs"),
                   f"http://127.0.0.1:{server.server_port}", case])
        if case == "bench-arm-short":
            driver.append("30")
        result = subprocess.run(driver,
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=35)
        intervals = [(seq, round((at - previous[1])*1000), active)
                     for previous, (seq, at, active) in zip(received, received[1:])]
        if case.startswith("bench-"):
            evidence = json.loads((tmp_path / "bench.json").read_text())
            assert evidence["start_count"] == session.run_count == 1
            if case == "bench-foreign":
                assert result.returncode == 1
                assert evidence["session_id"] is None
                assert evidence["failure"] == "Start attached to another owner"
                assert not session.stopped.is_set(), "An attached foreign session must not receive Stop"
                return
            assert evidence["cleanup_verified"] is True
            operations = [record["kind"] for record in evidence["records"] if record["event"] == "operation_result"]
            assert not {"Resume", "Approve", "ClaimInput"}.intersection(operations)
            if case in {"bench-pause", "bench-monitor-failure", "bench-camera-loss", "bench-finalize-stall", "bench-finalize-late-result"}:
                assert result.returncode == 1
                assert evidence["failure"]
                if case in {"bench-pause", "bench-monitor-failure"}:
                    assert evidence["pulses"] == []
                elif case == "bench-camera-loss":
                    assert "Required camera view lost" in evidence["failure"]
                else:
                    assert "finalization deadline" in evidence["failure"]
                if case != "bench-finalize-late-result":
                    assert "Stop" in operations
            else:
                assert evidence["failure"] is None
                assert "Stop" not in operations
                arm_duration = 30 if case == "bench-arm-short" else 180
                assert session.start_requests == ([(arm_duration, "scripted", "ArmSmoke")] if arm_bench else [(12, "physical", None)])
                assert [pulse["key"] for pulse in evidence["pulses"]] == ([] if arm_bench else ["w", "a", "u", "j"])
        if case not in {"bench-pause", "bench-monitor-failure", "bench-camera-loss", "bench-finalize-stall", "bench-finalize-late-result"}:
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
    variant = os.environ.get("AM1_TIMING_VARIANT", "nominal")
    assert variant in {"nominal", "native-child", "startup", "output"}
    assert not external or variant == "nominal", "process comparisons use the existing automated driver"
    session = FakeSessionIO(live_duration_s=120, variant=variant)
    session.trace_event = lambda event: trace.add("native_lease" if event["event"] == "test_native_lease" else event["event"], **{
        key: value for key, value in event.items() if key not in {"event", "wall_time_ns"}},
        source_wall_time_ns=event.get("wall_time_ns"))
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
        trace.add("http_get", path=handler.path.split("?", 1)[0])
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
    def send(owner, kind, payload, **kwargs):
        if kind == "lease":
            with owner.lock:
                seq, pipe_seq = owner.state.last_browser_seq, owner._send_seq + 1
            # This is a send-time state snapshot, not the source of an already
            # constructed lease. For a pause, payload evidence is authoritative.
            trace.add("pipe_send", session_id=owner.session_id, browser_seq_at_send=seq, pipe_seq=pipe_seq,
                      pause_browser_seq=(payload.get("pause_evidence") or {}).get("input_sequence"),
                      valid=payload.get("valid"))
        return real_send(owner, kind, payload, **kwargs)
    monkeypatch.setattr(AM1ConsoleBridgeServer, "_send", send)
    real_emit = adapter._emit
    def emit(event):
        if event.get("event") == "test_native_state":
            trace.add("native_consumer", paused=event.get("paused"), keys=event.get("keys"),
                      host_epoch=event.get("host_epoch"), session_id=event.get("session_id"),
                      native_pid=event.get("native_pid"), source_wall_time_ns=event.get("wall_time_ns"))
        elif event.get("event") in {"test_process_launch", "test_process_started", "test_process_exit", "test_startup_phase"}:
            trace.add(event["event"], **{key: value for key, value in event.items()
                                       if key not in {"event", "wall_time_ns"}},
                      source_wall_time_ns=event.get("wall_time_ns"))
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
                f"http://127.0.0.1:{server.server_port}", str(tmp_path / "browser-timing.json"),
                "45" if session.startup_duration_s else "8"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=125 + 2 * session.startup_duration_s)
            print(result.stdout)
            assert result.returncode == 0, result.stdout + result.stderr
        assert session.error is None
        assert adapter.wait(3)
        final = adapter.state()
        assert final["cleanup_verified"] is True
        assert final["phase"] == "complete" and final["final_exit_code"] == 0 and final["error"] is None
        assert trace.first_expiry is None, "unexpected accepted-input expiry is a nominal-usability failure"
        assert session.run_count == (1 if external else 2), "only deliberate Start creates a new owner"
        if variant == "nominal":
            assert not session.native.is_connected and not session.native._worker.is_alive()
        else:
            assert session.child.poll() == 0 and not session.reader.is_alive()
            started = [event for event in session.process_records if event["event"] == "test_process_started"]
            closed = [event for event in session.process_records if event["event"] == "test_native_closed"]
            assert len(started) == len(closed) == 2
            assert all(event["native_pid"] != os.getpid() for event in started)
            if session.startup_duration_s:
                summaries = [event for event in session.process_records if event["event"] == "test_startup_summary"]
                assert len(summaries) == 2
                assert all(event["frame_count"] == 301 and event["elapsed_s"] >= 30
                           and event["all_body_zero"] and event["max_step"] <= .75 for event in summaries)
            if variant == "output":
                assert len(session.output_records) == 2
                assert all(record["forwarded_chunks"]["host"] > 100
                           and record["forwarded_chunks"]["camera"] > 20
                           and record["max_chunk_bytes"] <= 1536 for record in session.output_records)
                assert all(39_000 < record["produced_bytes"]["host"] / record["elapsed_s"] < 50_000
                           and 600 < record["produced_bytes"]["camera"] / record["elapsed_s"] < 700
                           and record["bursts"] >= 3 for record in session.output_records), (
                    "the output comparison must actually produce its recorded rate/burst envelope")
        assert all(not owner._thread.is_alive() and not owner.auth_file.exists() for owner in bridges)
    finally:
        session.stopped.set()
        adapter.wait(3)
        (tmp_path / "python-timing.json").write_text(json.dumps({
            "clock": "Python monotonic seconds; not browser performance.now",
            "variant": variant, "process_records": session.process_records, "output_records": session.output_records,
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
