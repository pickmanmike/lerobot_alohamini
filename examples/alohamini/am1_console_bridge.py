"""Private, typed Windows pipe for one AM1 native leader/input client.

The browser never owns a motor socket. This module carries bounded intent
between the existing session controller and the sole Windows action client.
"""

from __future__ import annotations

import getpass
import json
import math
import os
import queue
import secrets
import subprocess
import threading
import time
from multiprocessing.connection import Client, Listener
from pathlib import Path
from typing import Any, Callable


BODY_KEYS = frozenset({"w", "s", "z", "x", "a", "d", "u", "j", "t", "g"})
INPUT_MAX_AGE_S = 0.25
MAX_PIPE_BYTES = 4096
PREPARED_GATES = frozenset({"sync_start", "live_start"})
MANUAL_GATES = frozenset({"realign", "resume"})
CONSOLE_BODY_OBSERVATION_KEYS = frozenset({"x.vel", "y.vel", "theta.vel", "lift_axis.vel", "lift_axis.height_mm"})
CONSOLE_ARM_KEYS = frozenset(f"arm_{side}_{joint}.pos" for side in ("left", "right")
                             for joint in ("shoulder_pan", "shoulder_lift", "elbow_flex",
                                           "wrist_flex", "wrist_roll", "gripper"))


def _console_numbers(values: Any, allowed: frozenset[str]) -> dict[str, float]:
    result: dict[str, float] = {}
    for key in allowed:
        value = values.get(key) if hasattr(values, "get") else None
        if value is None or isinstance(value, (bool, str, bytes, dict, list, tuple, complex)):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(number):
            result[key] = number
    return result


def make_console_live_sample_event(sample: Any, action: dict[str, Any], *, raw_keys: frozenset[str],
                                   host_feedback: Any, wall_ns: int, monotonic_now: float,
                                   leader_source: str = "physical") -> dict[str, Any]:
    """Label the already-read sample; never infer missing body telemetry as zero."""
    age_ns = int(max(0.0, monotonic_now - sample.observed_at) * 1e9)
    feedback = host_feedback if isinstance(host_feedback, dict) else {}
    leader_values = _console_numbers(sample.arm_target, CONSOLE_ARM_KEYS)
    return {"event": "live_sample", "acquired_at_ns": max(0, wall_ns - age_ns),
            "observation_sequence": sample.observation_sequence,
            "host_observation_id": feedback.get("observation_id"),
            "host_state": feedback.get("state"), "host_epoch": feedback.get("epoch"),
            "follower_positions": _console_numbers(sample.follower_positions, CONSOLE_ARM_KEYS),
            "leader_positions": leader_values if leader_source == "physical" else {},
            "scripted_target": leader_values if leader_source == "scripted" else {},
            "requested_targets": _console_numbers(action, CONSOLE_ARM_KEYS),
            "body_observation": _console_numbers(sample.observation,
                                                  CONSOLE_BODY_OBSERVATION_KEYS & raw_keys)}


def make_console_host_feedback_event(sample: Any, feedback: dict[str, Any], *,
                                     wall_ns: int, monotonic_now: float) -> dict[str, Any]:
    age_ns = int(max(0.0, monotonic_now - sample.observed_at) * 1e9)
    return {"event": "host_feedback", "acquired_at_ns": max(0, wall_ns - age_ns),
            "observation_sequence": sample.observation_sequence,
            "host_observation_id": feedback.get("observation_id"),
            "host_state": feedback.get("state"), "host_epoch": feedback.get("epoch")}


def make_console_action_sent_event(action: Any, *, sequence: int, interval_ms: float,
                                   wall_ns: int) -> dict[str, Any]:
    return {"event": "action_sent", "acquired_at_ns": wall_ns, "action_sequence": sequence,
            "action_send_interval_ms": interval_ms,
            "requested_targets": _console_numbers(action, CONSOLE_ARM_KEYS | CONSOLE_BODY_OBSERVATION_KEYS)}


def publish_console_telemetry_best_effort(client: Any, build_event: Callable[[], dict[str, Any]]) -> None:
    """Display-only data must never turn a valid robot action into a failure."""
    try:
        client.publish_telemetry(build_event())
    except Exception:
        pass


def _encode(message: dict[str, Any]) -> bytes:
    payload = json.dumps(message, separators=(",", ":"), sort_keys=True).encode()
    if len(payload) > MAX_PIPE_BYTES:
        raise ValueError("console pipe message exceeds limit")
    return payload


def _decode(payload: bytes) -> dict[str, Any]:
    if len(payload) > MAX_PIPE_BYTES:
        raise ValueError("console pipe message exceeds limit")
    message = json.loads(payload)
    if not isinstance(message, dict):
        raise ValueError("console pipe message must be an object")
    return message


class AM1ConsoleInputState:
    """Single browser lease, monotonically sequenced within its own epoch."""

    def __init__(self, session_id: str, control_token: str):
        self.session_id = session_id
        self.control_token = control_token
        self.epoch = 1
        self.last_browser_seq = 0
        self.last_browser_at: float | None = None
        self.keys: list[str] = []
        self.browser_active = False
        self.ever_active = False
        self.forced_pause = False
        self.pending_gate: tuple[str, int | None] | None = None
        self.approved_gate: tuple[str, int | None] | None = None

    def _fresh(self, now: float) -> bool:
        return (self.browser_active and self.last_browser_at is not None
                and 0 <= now - self.last_browser_at < INPUT_MAX_AGE_S)

    def browser_keys(self, *, token: str, epoch: int, seq: int, keys: list[str], active: bool, now: float) -> bool:
        if (
            not secrets.compare_digest(token, self.control_token) or type(epoch) is not int or epoch != self.epoch
            or type(seq) is not int or seq <= self.last_browser_seq or type(active) is not bool
            or not isinstance(keys, list) or len(keys) > len(BODY_KEYS)
            or any(not isinstance(key, str) or key not in BODY_KEYS for key in keys)
            or not math.isfinite(now)
        ):
            return False
        self.last_browser_seq = seq
        self.last_browser_at = now
        self.browser_active = active
        self.keys = sorted(set(keys)) if active else []
        if active:
            self.ever_active = True
        elif self.ever_active:
            self.forced_pause = True
        return True

    def lease(self, *, now: float) -> dict[str, Any]:
        fresh = self._fresh(now)
        if not fresh and self.ever_active:
            self.forced_pause = True
        valid = fresh and not self.forced_pause
        return {"valid": valid, "keys": list(self.keys) if valid else [], "epoch": self.epoch,
                "pause_required": self.forced_pause}

    def claim(self, token: str, *, now: float) -> int:
        if not token or not math.isfinite(now):
            raise ValueError("new control owner is invalid")
        self.epoch += 1
        self.control_token = token
        self.last_browser_seq = 0
        self.last_browser_at = None
        self.browser_active = False
        self.keys = []
        self.forced_pause = True
        self.pending_gate = None
        self.approved_gate = None
        return self.epoch

    def request_pause(self, cause: str) -> None:
        del cause
        self.keys = []
        self.forced_pause = True

    def request_gate(self, stage: str, *, host_epoch: int | None) -> None:
        if stage not in PREPARED_GATES | MANUAL_GATES:
            raise ValueError("unknown console gate")
        if host_epoch is not None and type(host_epoch) is not int:
            raise ValueError("invalid host epoch")
        self.pending_gate = (stage, host_epoch)
        self.approved_gate = None

    def approve(self, stage: str, *, host_epoch: int | None, token: str, now: float) -> bool:
        if (
            stage not in MANUAL_GATES or self.pending_gate != (stage, host_epoch)
            or not secrets.compare_digest(token, self.control_token) or not self._fresh(now)
        ):
            return False
        self.approved_gate = (stage, host_epoch)
        return True

    def gate_ack(self, stage: str, *, host_epoch: int | None, now: float) -> bool:
        if self.pending_gate != (stage, host_epoch) or not self._fresh(now):
            return False
        if stage in PREPARED_GATES:
            if self.forced_pause:
                return False
        elif self.approved_gate != (stage, host_epoch):
            return False
        self.pending_gate = None
        self.approved_gate = None
        if stage in {"resume", "realign"}:
            self.forced_pause = False
        return True


class AM1ConsoleBridgeClient:
    """Native client's one bounded IO worker; never runs on the action sender."""

    def __init__(self, pipe_name: str, auth_file: Path, session_id: str, *, clock=time.monotonic):
        self.pipe_name, self.auth_file, self.session_id = pipe_name, auth_file, session_id
        self.clock = clock
        self._lock = threading.Lock()
        self._conn = None
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._outbound: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=8)
        self._telemetry_pending: dict[str, dict[str, Any]] = {}
        self._gate_events: dict[tuple[str, int | None], threading.Event] = {}
        self._epoch = 0
        self._last_seq = 0
        self._received_at: float | None = None
        self._keys: set[str] = set()
        self._lease_valid = False
        self._pause_latched = True
        self._body_enabled = False
        self._needs_release = True
        self._connected = False
        self._error: str | None = None

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return self._connected and self._error is None

    def connect(self) -> None:
        if os.name != "nt":
            raise RuntimeError("AM1 console input pipe is Windows-only")
        authkey = bytes.fromhex(self.auth_file.read_text(encoding="ascii").strip())
        if len(authkey) != 32:
            raise ValueError("AM1 console pipe auth key is invalid")
        conn = Client(self.pipe_name, family="AF_PIPE", authkey=authkey)
        conn.send_bytes(_encode({"session_id": self.session_id, "epoch": 0, "seq": 0,
                                 "kind": "hello", "payload": {}}))
        if not conn.poll(5):
            conn.close()
            raise TimeoutError("AM1 console pipe did not acknowledge its client")
        welcome = _decode(conn.recv_bytes(MAX_PIPE_BYTES))
        if welcome.get("kind") != "welcome" or welcome.get("session_id") != self.session_id:
            conn.close()
            raise ValueError("AM1 console pipe acknowledged a different session")
        if type(welcome.get("epoch")) is not int or type(welcome.get("seq")) is not int:
            conn.close()
            raise ValueError("AM1 console pipe welcome is malformed")
        self._conn = conn
        with self._lock:
            self._connected = True
            self._epoch = welcome["epoch"]
            self._last_seq = welcome["seq"]
        self._worker = threading.Thread(target=self._io, name="am1-console-native-input", daemon=True)
        self._worker.start()

    def _io(self) -> None:
        assert self._conn is not None
        try:
            while not self._stop.is_set():
                try:
                    message = self._outbound.get_nowait()
                except queue.Empty:
                    with self._lock:
                        message = self._telemetry_pending.pop(next(iter(self._telemetry_pending))) if self._telemetry_pending else None
                    if message is not None:
                        try:
                            packet = _encode({"session_id": self.session_id, "epoch": self._epoch,
                                              "seq": 0, "kind": "telemetry", "payload": message})
                        except (TypeError, ValueError):
                            pass  # A display-only sample is not allowed to fault native input.
                        else:
                            self._conn.send_bytes(packet)
                else:
                    self._conn.send_bytes(_encode(message))
                if self._conn.poll(0.05):
                    self.accept_message(_decode(self._conn.recv_bytes(MAX_PIPE_BYTES)), received_at=self.clock())
        except (EOFError, OSError, ValueError) as exc:
            with self._lock:
                self._error = f"{type(exc).__name__}: {exc}"
                self._keys.clear()
                self._pause_latched = True
                self._body_enabled = False
                self._needs_release = True
        finally:
            with self._lock:
                self._connected = False

    def accept_message(self, message: dict[str, Any], *, received_at: float) -> bool:
        if message.get("session_id") != self.session_id or not math.isfinite(received_at):
            return False
        epoch, seq, kind, payload = message.get("epoch"), message.get("seq"), message.get("kind"), message.get("payload")
        if type(epoch) is not int or type(seq) is not int or not isinstance(payload, dict):
            return False
        with self._lock:
            if epoch < self._epoch or (epoch == self._epoch and seq <= self._last_seq):
                return False
            if epoch > self._epoch:
                self._epoch = epoch
                self._last_seq = 0
                self._keys.clear()
                self._pause_latched = True
                self._body_enabled = False
                self._needs_release = True
            self._last_seq = seq
            if kind == "lease":
                keys = payload.get("keys")
                valid = payload.get("valid") is True and isinstance(keys, list) and all(key in BODY_KEYS for key in keys)
                self._lease_valid = valid
                self._keys = set(keys) if valid and not self._pause_latched else set()
                self._received_at = received_at
                if not valid:
                    self._pause_latched = True
                    self._body_enabled = False
                    self._needs_release = True
                elif self._body_enabled and not keys:
                    self._needs_release = False
                return True
            if kind == "gate_ack":
                stage, host_epoch = payload.get("stage"), payload.get("host_epoch")
                event = self._gate_events.get((stage, host_epoch))
                if event is None:
                    return False
                if stage in PREPARED_GATES | {"resume"}:
                    self._pause_latched = False
                    self._body_enabled = False
                    self._needs_release = True
                event.set()
                return True
        return False

    def publish_telemetry(self, event: dict[str, Any]) -> None:
        """Replace a pending display sample without delaying action or approval traffic."""
        if event.get("event") not in {"live_sample", "action_sent", "host_feedback"}:
            return
        with self._lock:
            self._telemetry_pending[event["event"]] = event

    def body_keys(self, *, now: float | None = None) -> set[str]:
        at = self.clock() if now is None else now
        with self._lock:
            if (not self._lease_valid or self._received_at is None
                    or not 0 <= at - self._received_at < INPUT_MAX_AGE_S):
                self._keys.clear()
                self._pause_latched = True
                self._body_enabled = False
                self._needs_release = True
                return set()
            return set() if self._pause_latched or not self._body_enabled or self._needs_release else set(self._keys)

    def note_live_admitted(self) -> None:
        """Called only after current host active acknowledgement, not UI approval."""
        with self._lock:
            self._body_enabled = not self._pause_latched
            self._needs_release = True
            self._keys.clear()

    def get_action(self) -> set[str]:
        return self.body_keys()

    def pause_requested(self, *, now: float | None = None) -> bool:
        self.body_keys(now=now)
        with self._lock:
            if self._pause_latched:
                self._body_enabled = False
                self._needs_release = True
            return self._pause_latched

    def wait_gate(self, stage: str, *, host_epoch: int | None = None,
                  cancel: Any = None, timeout_s: float = 30.0) -> bool:
        if stage not in PREPARED_GATES | MANUAL_GATES:
            raise ValueError("unknown console gate")
        event = threading.Event()
        key = (stage, host_epoch)
        with self._lock:
            if key in self._gate_events:
                raise ValueError("console gate already pending")
            self._gate_events[key] = event
            epoch = self._epoch
        try:
            self._outbound.put_nowait({"session_id": self.session_id, "epoch": epoch, "seq": 0,
                                       "kind": "gate_request", "payload": {"stage": stage, "host_epoch": host_epoch}})
            deadline = self.clock() + timeout_s
            while self.clock() < deadline:
                if cancel is not None and cancel():
                    return False
                if event.wait(0.05):
                    return True
                if not self.is_connected:
                    return False
            return False
        finally:
            with self._lock:
                self._gate_events.pop(key, None)

    def disconnect(self) -> None:
        self._stop.set()
        if self._conn is not None:
            try:
                self._conn.close()
            except OSError:
                pass
        if self._worker is not None:
            self._worker.join(timeout=1)
        with self._lock:
            self._connected = False
            self._keys.clear()
            self._pause_latched = True
            self._body_enabled = False
            self._needs_release = True


def _write_private_auth_file(path: Path, key: bytes) -> None:
    """Create under existing private state, remove inherited Windows ACLs."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as stream:
            stream.write(key.hex())
        if os.name == "nt":
            identity = subprocess.run(["whoami"], capture_output=True, text=True, timeout=5, check=True).stdout.strip()
            if not identity:
                raise RuntimeError("unable to determine Windows owner of pipe auth file")
            completed = subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", f"{identity}:R"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError("unable to restrict AM1 console pipe auth file ACL")
    except BaseException:
        path.unlink(missing_ok=True)
        raise


class AM1ConsoleBridgeServer:
    """Session-owned authenticated pipe; one native client and one browser lease."""

    def __init__(self, session_id: str, state_directory: Path, control_token: str, *, clock=time.monotonic):
        if os.name != "nt":
            raise RuntimeError("AM1 console bridge server is Windows-only")
        self.session_id = session_id
        self.clock = clock
        self.state = AM1ConsoleInputState(session_id, control_token)
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.pipe_name = rf"\\.\pipe\am1-console-{secrets.token_hex(12)}"
        self.auth_file = state_directory / f".am1-console-{session_id}-{secrets.token_hex(6)}.auth"
        state_directory.mkdir(parents=True, exist_ok=True)
        self._authkey = secrets.token_bytes(32)
        _write_private_auth_file(self.auth_file, self._authkey)
        try:
            self.listener = Listener(self.pipe_name, family="AF_PIPE", authkey=self._authkey)
        except BaseException:
            self.auth_file.unlink(missing_ok=True)
            raise
        self.connection = None
        self._telemetry_sink = None
        self._send_seq = 0
        self._thread = threading.Thread(target=self._io, name="am1-console-pipe-owner", daemon=True)
        self._thread.start()

    def _send(self, kind: str, payload: dict[str, Any]) -> None:
        if self.connection is None:
            return
        with self.lock:
            self._send_seq += 1
            epoch = self.state.epoch
            sequence = self._send_seq
        self.connection.send_bytes(_encode({"session_id": self.session_id, "epoch": epoch, "seq": sequence,
                                            "kind": kind, "payload": payload}))

    def _io(self) -> None:
        try:
            conn = self.listener.accept()
            self.connection = conn
            if not conn.poll(5):
                return
            hello = _decode(conn.recv_bytes(MAX_PIPE_BYTES))
            if hello.get("kind") != "hello" or hello.get("session_id") != self.session_id:
                return
            self._send("welcome", {})
            while not self.stop_event.is_set():
                if conn.poll(0.05):
                    message = _decode(conn.recv_bytes(MAX_PIPE_BYTES))
                    if message.get("session_id") != self.session_id:
                        break
                    if message.get("kind") == "gate_request":
                        payload = message.get("payload", {})
                        stage, host_epoch = payload.get("stage"), payload.get("host_epoch")
                        with self.lock:
                            if message.get("epoch") != self.state.epoch:
                                continue
                            self.state.request_gate(stage, host_epoch=host_epoch)
                    elif message.get("kind") == "telemetry" and message.get("epoch") == self.state.epoch:
                        payload = message.get("payload")
                        if isinstance(payload, dict) and payload.get("event") in {"live_sample", "action_sent", "host_feedback"}:
                            sink = self._telemetry_sink
                            if sink is not None:
                                try:
                                    sink(payload)
                                except Exception:
                                    pass  # A display subscriber must not tear down the input bridge.
                with self.lock:
                    pending = self.state.pending_gate
                    if pending is not None and self.state.gate_ack(pending[0], host_epoch=pending[1], now=self.clock()):
                        ack = {"stage": pending[0], "host_epoch": pending[1]}
                    else:
                        ack = None
                    lease = self.state.lease(now=self.clock())
                if ack is not None:
                    self._send("gate_ack", ack)
                self._send("lease", lease)
        except (EOFError, OSError, ValueError):
            pass
        finally:
            with self.lock:
                self.state.request_pause("pipe disconnected")
            if self.connection is not None:
                try:
                    self.connection.close()
                except OSError:
                    pass
                self.connection = None

    def browser_keys(self, *, token: str, epoch: int, seq: int, keys: list[str], active: bool) -> bool:
        with self.lock:
            return self.state.browser_keys(token=token, epoch=epoch, seq=seq, keys=keys,
                                           active=active, now=self.clock())

    def set_telemetry_sink(self, sink: Any) -> None:
        self._telemetry_sink = sink

    def request_pause(self, cause: str) -> None:
        with self.lock:
            self.state.request_pause(cause)

    def approve(self, stage: str, *, host_epoch: int | None, token: str) -> bool:
        with self.lock:
            return self.state.approve(stage, host_epoch=host_epoch, token=token, now=self.clock())

    def claim(self, token: str) -> int:
        with self.lock:
            return self.state.claim(token, now=self.clock())

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            lease = self.state.lease(now=self.clock())
            return {"input_epoch": self.state.epoch, "input_lease": lease["valid"],
                    "pending_gate": self.state.pending_gate, "pause_required": lease["pause_required"]}

    def close(self) -> None:
        self.stop_event.set()
        if self.connection is None and self._thread.is_alive():
            # Closing a Windows AF_PIPE Listener does not wake its pending
            # accept(). Authenticate one private local connection so the
            # owner thread can observe stop_event and exit before unlink.
            try:
                wake = Client(self.pipe_name, family="AF_PIPE", authkey=self._authkey)
                wake.close()
            except (OSError, EOFError):
                pass
        if self.connection is not None:
            try:
                self.connection.close()
            except OSError:
                pass
        self.listener.close()
        self._thread.join(timeout=2)
        self.auth_file.unlink(missing_ok=True)
        if self._thread.is_alive():
            raise RuntimeError("AM1 console pipe owner did not terminate during cleanup")
