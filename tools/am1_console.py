#!/usr/bin/env python
"""Loopback-only AM1 console shell; it does not start a robot on page load."""

from __future__ import annotations

import argparse
import base64
import binascii
import hmac
import http.client
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from pathlib import PurePosixPath
from urllib.parse import parse_qs, urlsplit

from tools.am1_console_model import ConsoleSnapshot


MAX_CONSOLE_LOG_BYTES = 2_000_000
MAX_OUTPUT_BYTES = 128_000
LOG_KINDS = frozenset({"client", "host", "camera", "ssh", "summary"})
REMOTE_LOG_NAME = re.compile(r"am1-(?:local-host|camera)-[A-Za-z0-9-]{1,100}\.log\Z")


class SessionMismatchError(Exception):
    """A log request refers to a session no longer owned by this console."""


class ConsoleSessionAdapter:
    """One console-facing adapter around the existing local session owner."""

    def __init__(self, config, repository: Path, session_module, *, bridge_factory=None):
        if bridge_factory is None:
            from examples.alohamini.am1_console_bridge import AM1ConsoleBridgeServer

            bridge_factory = AM1ConsoleBridgeServer
        self.config = config
        self.repository = repository
        self.session_module = session_module
        self.bridge_factory = bridge_factory
        self._lock = threading.Lock()
        self._created = threading.Event()
        self._worker: threading.Thread | None = None
        self._session_id: str | None = None
        self._phase = "idle"
        self._final_exit_code: int | None = None
        self._cleanup_verified: bool | None = None
        self._operator_stopped = False
        self._verified_source_heads: dict[str, str] | None = None
        self._verified_source_at_ns: int | None = None
        self._error: str | None = None
        self._events = deque(maxlen=128)
        self._outputs: dict[str, dict] = {}
        self._telemetry = ConsoleSnapshot()
        self._event_sink = None
        self._bridge = None
        self._control_token: str | None = None
        self._admitted_host_epoch: int | None = None
        self._admitted_at_ns: int | None = None

    def set_event_sink(self, sink):
        self._event_sink = sink

    def camera_status(self, status: dict, *, acquired_at_ns: int) -> bool:
        cameras = status.get("cameras") if isinstance(status, dict) else None
        if not isinstance(cameras, dict):
            self.camera_status_failed(acquired_at_ns=acquired_at_ns)
            return False
        with self._lock:
            self._telemetry.update({"event": "camera_status", "acquired_at_ns": acquired_at_ns,
                                    "roles": cameras})
        return True

    def camera_status_failed(self, *, acquired_at_ns: int) -> None:
        with self._lock:
            self._telemetry.update({"event": "camera_status_failed", "acquired_at_ns": acquired_at_ns})

    def _emit(self, event):
        event = dict(event)
        if event.get("event") == "process_output":
            # Output is not lifecycle/SSE traffic and cannot fill its queues.
            self._receive_output(event)
            return
        telemetry_only = event.get("event") in {"live_sample", "action_sent", "host_feedback", "system_sample", "live_admitted"}
        with self._lock:
            if event.get("session_id") is not None and event["session_id"] != self._session_id:
                return
            self._telemetry.update(event)
            if event.get("event") in {"host_feedback", "live_sample", "live_admitted"} and self._phase not in {
                "stopping", "client_exited", "complete", "operator_stopped", "failed", "cleanup_unknown",
            }:
                observation = self._telemetry.observation
                acquired = event.get("acquired_at_ns")
                if (event.get("event") == "live_admitted" and type(acquired) is int
                        and type(event.get("host_epoch")) is int and event["host_epoch"] >= 0
                        and (self._admitted_at_ns is None or acquired >= self._admitted_at_ns)):
                    # Only the native consumer's validated same-host active ack
                    # calls note_live_admitted; raw feedback/UI approval does not.
                    self._admitted_host_epoch = event["host_epoch"]
                    self._admitted_at_ns = acquired
                # Never use an older observation rejected by the cache.
                if (type(event.get("acquired_at_ns")) is int
                        and (event.get("event") == "live_admitted"
                             or event["acquired_at_ns"] == observation["acquired_at_ns"])):
                    if observation.get("host_state") == "active":
                        self._phase = "live" if observation.get("host_epoch") == self._admitted_host_epoch else "awaiting_live_ack"
                    elif observation.get("host_state") in {"paused", "ready"}:
                        self._phase = "paused" if observation["host_state"] == "paused" else "host_ready"
                        if (self._admitted_at_ns is not None and observation["acquired_at_ns"] is not None
                                and observation["acquired_at_ns"] >= self._admitted_at_ns
                                and (observation["host_state"] == "ready"
                                     or type(observation.get("host_epoch")) is not int
                                     or self._admitted_host_epoch is None
                                     or observation["host_epoch"] >= self._admitted_host_epoch)):
                            # A later timestamp on sequential telemetry does
                            # not make a superseded paused host epoch current.
                            self._admitted_host_epoch = None
            if telemetry_only:
                return
            self._events.append(event)
            if event.get("event") in {"preflight_passed", "camera_ready", "host_ready"} and self._phase != "stopping":
                self._phase = event["event"]
            elif event.get("event") == "stop_requested":
                self._phase = "stopping"
            elif event.get("event") == "client_exited" and self._phase != "stopping":
                self._phase = "client_exited"
            if event.get("event") == "preflight_passed" and isinstance(event.get("sources"), dict):
                self._verified_source_heads = {
                    key: value for key, value in event["sources"].items()
                    if isinstance(key, str) and key.endswith("_source_head")
                    and isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value)
                }
                self._verified_source_at_ns = time.time_ns()
            elif (event.get("event") == "cleanup" and not event.get("cleanup_verified")
                  and self._phase != "stopping"):
                self._phase = "cleanup_unknown"
            if event.get("event") in {"cleanup", "session_complete"} and type(event.get("cleanup_verified")) is bool:
                self._cleanup_verified = event["cleanup_verified"]
            if event.get("event") == "session_complete":
                self._operator_stopped = (event.get("operator_stopped") is True
                                          and event.get("final_exit_code") == 130
                                          and event.get("cleanup_verified") is True)
        if self._event_sink is not None:
            self._event_sink(event)

    def _receive_output(self, event):
        source, offset, encoded = event.get("source"), event.get("offset"), event.get("data_base64")
        path, acquired = event.get("path"), event.get("acquired_at_ns")
        if (source not in {"host", "camera"} or type(offset) is not int or offset < 0
                or not isinstance(encoded, str) or len(encoded) > 2048
                or not isinstance(path, str) or len(path) > 1024
                or type(acquired) is not int):
            return
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            return
        if not data or len(data) > 1536:
            return
        with self._lock:
            if event.get("session_id") != self._session_id:
                return
            current = self._outputs.get(source)
            gap = current is None and offset != 0 or current is not None and (
                offset != current["end_offset"] or path != current["path"])
            prior = current["data"] if current is not None and not gap else b""
            combined = prior + data
            self._outputs[source] = {
                "data": combined[-MAX_OUTPUT_BYTES:], "path": path,
                "end_offset": offset + len(data), "acquired_at_ns": acquired,
                "received_at_ns": event["windows_received_at_ns"]
                    if type(event.get("windows_received_at_ns")) is int else time.time_ns(),
                "truncated": bool(gap or len(combined) > MAX_OUTPUT_BYTES or
                                  current is not None and current["truncated"]),
            }

    def read_output(self, kind: str, expected_session_id: str) -> dict:
        """A bounded live/retained display; exact file export remains separate."""
        if kind not in LOG_KINDS - {"summary"} or not self.session_module.SESSION_ID_PATTERN.fullmatch(expected_session_id):
            raise ValueError("invalid AM1 output selection")
        with self._lock:
            if expected_session_id != self._session_id:
                raise SessionMismatchError("AM1 session changed before output read")
            current = dict(self._outputs[kind]) if kind in self._outputs else None
            running = self._worker is not None and self._worker.is_alive()
        result = {"session_id": expected_session_id, "source": kind, "text": "", "state": "Unavailable",
                  "reason": "Original output has not arrived; saved files remain available after collection.",
                  "truncated": False, "path": None, "acquired_at_ns": None}
        if current is not None:
            age_ms = max(0, (time.time_ns() - current["received_at_ns"]) / 1_000_000)
            result.update(text=current["data"].decode("utf-8", errors="replace"), path=current["path"],
                          acquired_at_ns=current["acquired_at_ns"], received_age_ms=age_ms,
                          truncated=current["truncated"], reason=None,
                          state="Live forwarded" if running and age_ms < 2000 else "Retained output")
        elif kind in {"client", "ssh"}:
            root = self.config.windows_log_directory.resolve()
            directory = root / f"am1-session-{expected_session_id}"
            name = f"am1-local-windows-{expected_session_id}.log" if kind == "client" else "ssh-control.log"
            path = directory / name
            try:
                if directory.is_symlink() or path.is_symlink() or path.resolve().parent != directory.resolve():
                    return result
                with path.open("rb") as stream:
                    stream.seek(0, 2)
                    size = stream.tell()
                    stream.seek(max(0, size - MAX_OUTPUT_BYTES))
                    data = stream.read(MAX_OUTPUT_BYTES)
                result.update(text=data.decode("utf-8", errors="replace"), path=str(path), reason=None,
                              state="Current file snapshot" if running else "Retained output",
                              acquired_at_ns=time.time_ns(), truncated=size > MAX_OUTPUT_BYTES)
            except OSError:
                pass
        return result

    def _on_created(self, session_id: str):
        with self._lock:
            self._session_id = session_id
            self._phase = "preflight"
        self._created.set()
        self._emit({"event": "session_created", "session_id": session_id})

    def _prepare_console(self, session_id: str):
        bridge = self.bridge_factory(session_id, self.config.local_state_directory, self._control_token)
        if hasattr(bridge, "set_telemetry_sink"):
            bridge.set_telemetry_sink(self._emit)
        with self._lock:
            self._bridge = bridge
        return bridge.pipe_name, bridge.auth_file

    def _gate(self, stage, evidence, cancel):
        # Prepared Start is the operator's approval for ordinary qualified
        # progression; actual camera/host events still must precede each gate.
        return not cancel()

    def _run(self, duration, leader_source, motion_profile):
        try:
            result = self.session_module.run_start(
                self.repository, self.config, duration,
                leader_source=leader_source, motion_profile=motion_profile,
                gate=self._gate, emit=self._emit, on_session_created=self._on_created,
                console_prepare=self._prepare_console,
            )
            with self._lock:
                self._final_exit_code = result
        except BaseException as exc:
            with self._lock:
                self._error = f"{type(exc).__name__}: {exc}"
                self._phase = "failed"
            self._emit({"event": "session_error", "reason": self._error})
        finally:
            bridge = self._bridge
            if bridge is not None:
                try:
                    bridge.close()
                except BaseException as exc:
                    with self._lock:
                        cleanup_note = f"pipe cleanup also failed: {type(exc).__name__}: {exc}"
                        self._error = f"{self._error}; {cleanup_note}" if self._error else cleanup_note
                        self._phase = "cleanup_unknown"
                        self._cleanup_verified = False
                self._bridge = None
            with self._lock:
                if self._cleanup_verified is False:
                    self._phase = "cleanup_unknown"
                elif self._error is not None:
                    self._phase = "failed"
                elif self._final_exit_code == 130 and self._operator_stopped and self._cleanup_verified is True:
                    self._phase = "operator_stopped"
                else:
                    self._phase = "complete" if self._final_exit_code == 0 else "cleanup_unknown" if self._final_exit_code == 3 else "failed"
            self._created.set()

    def state(self):
        with self._lock:
            state = {"session_id": self._session_id, "phase": self._phase,
                    "final_exit_code": self._final_exit_code, "cleanup_verified": self._cleanup_verified,
                    "error": self._error,
                    "events": list(self._events), "telemetry": self._telemetry.snapshot(now_ns=time.time_ns())}
            if state["phase"] == "live":
                age = state["telemetry"]["observation"]["age_ms"]
                if age is None or age > 1000:
                    state["phase"] = "feedback_stale"
            state["configured_source_pins"] = {name: getattr(self.config, name, None) for name in (
                "remote_session_head", "remote_motor_head", "remote_camera_head")}
            state["verified_source_heads"] = None if self._verified_source_heads is None else dict(self._verified_source_heads)
            state["verified_source_at_ns"] = self._verified_source_at_ns
            bridge = self._bridge
        if bridge is not None:
            state.update(bridge.snapshot())
        return state

    def read_log(self, kind: str, expected_session_id: str) -> tuple[str, bytes]:
        """Read only one named file from this console's exact owned result folder."""
        if kind not in LOG_KINDS:
            raise ValueError("unknown AM1 session log kind")
        if not self.session_module.SESSION_ID_PATTERN.fullmatch(expected_session_id):
            raise ValueError("invalid AM1 session identity")
        with self._lock:
            session_id = self._session_id
        if session_id != expected_session_id:
            raise SessionMismatchError("AM1 session changed before log read")
        if not isinstance(session_id, str) or not self.session_module.SESSION_ID_PATTERN.fullmatch(session_id):
            raise FileNotFoundError("no current AM1 session result")
        root = self.config.windows_log_directory.resolve()
        directory = root / f"am1-session-{session_id}"
        if directory.is_symlink() or not directory.is_dir() or directory.resolve().parent != root:
            raise FileNotFoundError("AM1 session result is unavailable")
        if kind == "client":
            name = f"am1-local-windows-{session_id}.log"
        elif kind == "ssh":
            name = "ssh-control.log"
        elif kind == "summary":
            name = "session-summary.json"
        else:
            summary_path = directory / "session-summary.json"
            summary = self._read_owned_file(summary_path, directory, limit=64_000)
            manifest = json.loads(summary)
            if not isinstance(manifest, dict) or manifest.get("session_id") != session_id:
                raise FileNotFoundError("AM1 session log manifest does not match")
            paths = manifest.get("remote_logs")
            if not isinstance(paths, list):
                raise FileNotFoundError("AM1 session log manifest is unavailable")
            prefix = "am1-local-host-" if kind == "host" else "am1-camera-"
            matches = [PurePosixPath(path).name for path in paths if isinstance(path, str)
                       and PurePosixPath(path).name.startswith(prefix)
                       and REMOTE_LOG_NAME.fullmatch(PurePosixPath(path).name)]
            if len(matches) != 1:
                raise FileNotFoundError("exact AM1 session log is unavailable or ambiguous")
            name = matches[0]
        return name, self._read_owned_file(directory / name, directory, limit=MAX_CONSOLE_LOG_BYTES)

    @staticmethod
    def _read_owned_file(path: Path, directory: Path, *, limit: int) -> bytes:
        if path.is_symlink() or path.resolve().parent != directory.resolve():
            raise FileNotFoundError("AM1 session log is outside the result folder")
        if path.stat().st_size > limit:
            raise OverflowError("AM1 session log exceeds the console read limit")
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise OverflowError("AM1 session log exceeds the console read limit")
        return data

    def wait(self, timeout: float) -> bool:
        worker = self._worker
        if worker is not None:
            worker.join(timeout)
        return worker is None or not worker.is_alive()

    def operation(self, payload):
        kind = payload.get("kind")
        if kind == "Start":
            with self._lock:
                if self._worker is not None and self._worker.is_alive():
                    # A second tab attaches to the current or completed result.
                    return {"accepted": True, "session_id": self._session_id, "phase": self._phase}
                if self._worker is not None and not (
                    self._phase in {"complete", "operator_stopped"} and self._bridge is None
                ):
                    return {"accepted": False, "session_id": self._session_id,
                            "phase": self._phase, "reason": "prior cleanup or failure is unresolved"}
                duration = self.session_module.parse_duration_seconds(payload.get("duration_seconds"))
                leader_source = payload.get("leader_source", "physical")
                motion_profile = payload.get("motion_profile")
                self.session_module.validate_leader_selection(leader_source, motion_profile)
                self._created.clear()
                self._session_id = None
                self._final_exit_code = None
                self._cleanup_verified = None
                self._operator_stopped = False
                self._verified_source_heads = None
                self._verified_source_at_ns = None
                self._error = None
                # Session-owned observations/actions cannot be attributed to
                # the next Start. Camera status is reacquired independently.
                self._telemetry = ConsoleSnapshot()
                self._admitted_host_epoch = None
                self._admitted_at_ns = None
                self._events.clear()
                self._outputs.clear()
                self._control_token = secrets.token_hex(32)
                self._phase = "starting"
                self._worker = threading.Thread(target=self._run, args=(duration, leader_source, motion_profile),
                                                name="am1-console-session", daemon=False)
                self._worker.start()
            if not self._created.wait(60):
                return {"accepted": False, "phase": "starting", "reason": "session identity not yet established"}
            state = self.state()
            return {"accepted": state["session_id"] is not None, "session_id": state["session_id"],
                    "phase": state["phase"], "error": state["error"],
                    "control_token": self._control_token if state["session_id"] is not None else None,
                    "input_epoch": state.get("input_epoch")}
        if kind == "Stop":
            expected = payload.get("session_id")
            with self._lock:
                current = self._session_id
                running = self._worker is not None and self._worker.is_alive()
            if not isinstance(expected, str) or not hmac.compare_digest(expected, current or ""):
                return {"accepted": False, "reason": "session identity mismatch"}
            if not running:
                return {"accepted": False, "reason": "session is already terminal; cleanup state is unchanged"}
            try:
                self.session_module.request_stop(self.config, expected_session_id=expected, wait=False)
            except self.session_module.SessionError as exc:
                return {"accepted": False, "reason": str(exc)}
            self._emit({"event": "stop_requested", "session_id": expected})
            return {"accepted": True, "session_id": expected, "phase": "stopping"}
        if kind in {"Pause", "Resume", "Approve", "ClaimInput"}:
            expected = payload.get("session_id")
            with self._lock:
                current, bridge, token, worker = self._session_id, self._bridge, self._control_token, self._worker
            if expected != current or bridge is None or worker is None or not worker.is_alive():
                return {"accepted": False, "reason": "active session identity is unavailable"}
            if kind == "ClaimInput":
                if bridge.snapshot().get("input_lease"):
                    return {"accepted": False, "reason": "the current input owner is still live"}
                new_token = secrets.token_hex(32)
                epoch = bridge.claim(new_token)
                with self._lock:
                    self._control_token = new_token
                self._emit({"event": "input_claimed", "session_id": expected, "input_epoch": epoch})
                return {"accepted": True, "session_id": expected, "control_token": new_token,
                        "input_epoch": epoch, "phase": "paused"}
            if payload.get("control_token") != token:
                return {"accepted": False, "reason": "input owner token mismatch"}
            if kind == "Pause":
                bridge.request_pause("operator")
                self._emit({"event": "pause_requested", "session_id": expected})
                return {"accepted": True, "phase": "pausing"}
            stage = "resume" if kind == "Resume" else "realign"
            requested_stage = payload.get("gate_stage", stage)
            if not isinstance(requested_stage, str):
                return {"accepted": False, "reason": "approval gate is invalid"}
            if kind == "Resume" and requested_stage in {"sync_start", "live_start"}:
                stage = requested_stage
            elif requested_stage != stage:
                return {"accepted": False, "reason": "approval does not match the displayed gate"}
            host_epoch = payload.get("host_epoch")
            if host_epoch is not None and type(host_epoch) is not int:
                return {"accepted": False, "reason": "host epoch is invalid"}
            accepted = bridge.approve(stage, host_epoch=host_epoch, token=token)
            self._emit({"event": "gate_approval", "session_id": expected, "stage": stage,
                        "host_epoch": host_epoch, "accepted": accepted})
            return {"accepted": accepted, "phase": "resume_pending" if accepted else "approval_refused"}
        return {"accepted": False, "reason": "operation is unavailable"}

    def body_input(self, payload):
        with self._lock:
            bridge, session_id, token, worker = self._bridge, self._session_id, self._control_token, self._worker
        if bridge is None or worker is None or not worker.is_alive() or payload.get("session_id") != session_id:
            return {"accepted": False, "reason": "no matching active session"}
        if payload.get("control_token") != token:
            return {"accepted": False, "reason": "input owner token mismatch"}
        accepted = bridge.browser_keys(token=token, epoch=payload.get("epoch"), seq=payload.get("seq"),
                                       keys=payload.get("keys"), active=payload.get("active"),
                                       **{key:payload[key] for key in ("release_reason", "first_release") if key in payload})
        snapshot = bridge.snapshot()
        return {"accepted": accepted, "input_epoch": snapshot["input_epoch"],
                "body_release_required": snapshot["body_release_required"]}


MAX_POST_BYTES = 4096
CAMERA_ROLES = frozenset({"forward", "backward", "chest", "wrist_left", "wrist_right"})
CAMERA_ASSETS = frozenset({"app.js", "freshness.js", "mjpeg.js", "style.css"})
CONSOLE_ASSETS = frozenset({"app.js", "style.css"})
ALLOWED_OPERATIONS = frozenset({"Start", "Pause", "Resume", "Stop", "Approve", "ClaimInput"})
ALLOWED_BODY_KEYS = frozenset("wszxadujtg")
COOKIE_NAME = "am1_console"


def _camera_target(path: str) -> str | None:
    """Accept only the narrow viewer routes, never arbitrary backend URLs."""
    target = urlsplit(path)
    if target.scheme or target.netloc or target.fragment:
        return None
    if target.path == "/camera/status.json" and not target.query:
        return "/status.json"
    if target.path not in {"/camera/api/frame.jpeg", "/camera/api/stream.mjpeg"}:
        return None
    try:
        query = parse_qs(target.query, keep_blank_values=True, strict_parsing=True, max_num_fields=2)
    except ValueError:
        return None
    allowed = {"src", "cache"} if target.path.endswith("frame.jpeg") else {"src"}
    if not query or set(query) - allowed or any(len(values) != 1 for values in query.values()):
        return None
    if query.get("src", [None])[0] not in CAMERA_ROLES:
        return None
    if "cache" in query and query["cache"] != ["500ms"]:
        return None
    return target.path.removeprefix("/camera") + ("?" + target.query if target.query else "")


class ConsoleServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    request_queue_size = 32

    def __init__(self, address, config, camera_auth_file: Path, session_adapter):
        if address[0] != "127.0.0.1":
            raise ValueError("AM1 console must bind to 127.0.0.1 only")
        parsed = urlsplit(config.browser_url)
        if parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password or parsed.path not in {"", "/"}:
            raise ValueError("Camera browser URL must identify one HTTP viewer origin")
        credentials = json.loads(camera_auth_file.read_text(encoding="utf-8"))
        if (
            not isinstance(credentials, dict) or not isinstance(credentials.get("username"), str)
            or not isinstance(credentials.get("password"), str) or not credentials["username"]
            or not credentials["password"]
        ):
            raise ValueError("Private camera auth file needs username and password")
        self.camera_host = parsed.hostname
        self.camera_port = parsed.port or 80
        self.camera_authorization = "Basic " + base64.b64encode(
            f"{credentials['username']}:{credentials['password']}".encode()
        ).decode()
        self.session_adapter = session_adapter
        self.cookie_token = secrets.token_hex(32)
        self.csrf_token = secrets.token_hex(32)
        self.stop_event = threading.Event()
        self.camera_slots = threading.BoundedSemaphore(8)
        self.stream_slots = threading.BoundedSemaphore(5)
        self.event_slots = threading.BoundedSemaphore(4)
        self.log_slots = threading.BoundedSemaphore(2)
        self.events = deque(maxlen=128)
        self.event_sequence = 0
        self.event_condition = threading.Condition()
        if hasattr(session_adapter, "set_event_sink"):
            session_adapter.set_event_sink(self.emit)
        super().__init__(address, ConsoleHandler)

    def emit(self, event: dict) -> None:
        if not isinstance(event, dict):
            return
        with self.event_condition:
            self.event_sequence += 1
            self.events.append((self.event_sequence, dict(event)))
            self.event_condition.notify_all()

    def server_close(self) -> None:
        self.stop_event.set()
        with self.event_condition:
            self.event_condition.notify_all()
        super().server_close()

    def handle_error(self, request, client_address):
        # Never leak camera credentials, request targets or private session data.
        pass


class ConsoleHandler(BaseHTTPRequestHandler):
    server_version = "AM1Console/1"
    sys_version = ""

    def log_message(self, *_):
        pass

    def _headers(self, status: int, mime: str, length: int | None = None, *, cookie: bool = False, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; "
                         "img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        if length is not None:
            self.send_header("Content-Length", str(length))
        if cookie:
            self.send_header("Set-Cookie", f"{COOKIE_NAME}={self.server.cookie_token}; HttpOnly; SameSite=Strict; Path=/")
        for name, value in (extra or {}).items():
            self.send_header(name, str(value))
        self.end_headers()

    def _reply(self, status: int, body=b"", mime="text/plain; charset=utf-8", *, cookie=False):
        self._headers(status, mime, len(body), cookie=cookie)
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, status: int, payload: dict):
        self._reply(status, json.dumps(payload, separators=(",", ":")).encode(), "application/json")

    def _origin(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def _valid_host(self):
        hosts = self.headers.get_all("Host", [])
        return len(hosts) == 1 and hosts[0] == self._origin().removeprefix("http://")

    def _valid_cookie(self):
        supplied = self.headers.get_all("Cookie", [])
        if len(supplied) != 1:
            return False
        parts = [part.strip() for part in supplied[0].split(";")]
        tokens = [part.partition("=")[2] for part in parts if part.partition("=")[0] == COOKIE_NAME]
        return len(tokens) == 1 and hmac.compare_digest(tokens[0], self.server.cookie_token)

    def _drain_small_post_body(self):
        # A Windows TCP peer can reset the refused response if its request
        # body is left unread. Bound both size and time for untrusted input.
        length = self.headers.get("Content-Length", "")
        if length.isdecimal() and int(length) <= MAX_POST_BYTES * 2:
            try:
                self.connection.settimeout(0.5)
                self.rfile.read(int(length))
            except OSError:
                pass

    def _base_check(self):
        if not self._valid_host() or self.headers.get("Upgrade") or self.headers.get("Transfer-Encoding"):
            if self.command == "POST":
                self._drain_small_post_body()
            self._reply(403)
            return False
        target = urlsplit(self.path)
        if target.scheme or target.netloc or target.fragment or ".." in target.path.split("/"):
            if self.command == "POST":
                self._drain_small_post_body()
            self._reply(403)
            return False
        return True

    def do_GET(self):
        if not self._base_check():
            return
        target = urlsplit(self.path)
        if target.path == "/" and not target.query:
            page = (Path(__file__).parent / "am1_console_ui" / "index.html").read_bytes()
            page = page.replace(b"AM1_CSRF_TOKEN", self.server.csrf_token.encode())
            self._reply(200, page, "text/html; charset=utf-8", cookie=True)
            return
        if not self._valid_cookie():
            self._reply(403)
            return
        if target.path.startswith("/assets/") and not target.query:
            name = target.path.removeprefix("/assets/")
            if name in CONSOLE_ASSETS:
                body = (Path(__file__).parent / "am1_console_ui" / name).read_bytes()
                mime = "text/css" if name.endswith(".css") else "text/javascript"
                self._reply(200, body, mime)
                return
        if target.path.startswith("/camera/assets/") and not target.query:
            name = target.path.removeprefix("/camera/assets/")
            if name in CAMERA_ASSETS:
                body = (Path(__file__).parent / "am1_camera" / name).read_bytes()
                mime = "text/css" if name.endswith(".css") else "text/javascript"
                self._reply(200, body, mime)
                return
        if target.path == "/api/state" and not target.query:
            self._json(200, self.server.session_adapter.state())
            return
        if target.path == "/api/log":
            self._serve_log(target)
            return
        if target.path == "/api/output":
            self._serve_output(target)
            return
        if target.path == "/api/events" and not target.query:
            self._events()
            return
        if target.path.startswith("/camera/"):
            camera_path = _camera_target(self.path)
            if camera_path is None:
                self._reply(404)
                return
            self._proxy_camera(camera_path)
            return
        self._reply(404)

    def _serve_output(self, target):
        if not self.server.log_slots.acquire(blocking=False):
            self._reply(503)
            return
        try:
            query = parse_qs(target.query, strict_parsing=True, keep_blank_values=True, max_num_fields=2)
            if set(query) != {"kind", "session_id"} or any(len(values) != 1 for values in query.values()):
                raise ValueError("invalid output request")
            payload = self.server.session_adapter.read_output(query["kind"][0], query["session_id"][0])
            self.connection.settimeout(2)
            self._json(200, payload)
        except ValueError:
            self._reply(400)
        except SessionMismatchError:
            self._reply(409)
        except OSError:
            pass
        finally:
            self.server.log_slots.release()

    def _serve_log(self, target):
        if not self.server.log_slots.acquire(blocking=False):
            self._reply(503)
            return
        try:
            try:
                query = parse_qs(target.query, strict_parsing=True, keep_blank_values=True)
            except ValueError:
                self._reply(400)
                return
            if set(query) - {"kind", "session_id", "download"} or len(query.get("kind", [])) != 1 or (
                len(query.get("session_id", [])) != 1
            ) or (
                "download" in query and query["download"] != ["1"]
            ):
                self._reply(400)
                return
            try:
                name, body = self.server.session_adapter.read_log(query["kind"][0], query["session_id"][0])
            except ValueError:
                self._reply(400)
                return
            except SessionMismatchError:
                self._reply(409)
                return
            except (FileNotFoundError, OSError):
                self._reply(404)
                return
            except OverflowError:
                self._reply(413)
                return
            extra = {"Content-Disposition": f'attachment; filename="{name}"'} if query.get("download") == ["1"] else None
            mime = "application/json" if query["kind"] == ["summary"] else "text/plain; charset=utf-8"
            self.connection.settimeout(2)
            try:
                self._headers(200, mime, len(body), extra=extra)
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
                pass
        finally:
            self.server.log_slots.release()

    def _events(self):
        if not self.server.event_slots.acquire(blocking=False):
            self._reply(503)
            return
        try:
            self._headers(200, "text/event-stream; charset=utf-8", extra={"Connection": "close"})
            cursor = self.server.event_sequence
            self.wfile.write(b": connected\n\n")
            while not self.server.stop_event.is_set():
                with self.server.event_condition:
                    self.server.event_condition.wait_for(
                        lambda: self.server.stop_event.is_set() or self.server.event_sequence > cursor,
                        timeout=10,
                    )
                    batch = [(seq, event) for seq, event in self.server.events if seq > cursor]
                    oldest = self.server.events[0][0] if self.server.events else cursor
                if batch and cursor + 1 < oldest:
                    self.wfile.write(b"event: dropped\ndata: {}\n\n")
                for seq, event in batch:
                    self.wfile.write(f"id: {seq}\ndata: {json.dumps(event, separators=(',', ':'))}\n\n".encode())
                    cursor = seq
                if not batch:
                    self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            pass
        finally:
            self.server.event_slots.release()

    def _proxy_camera(self, camera_path: str):
        streaming = camera_path.startswith("/api/stream.mjpeg")
        is_status = camera_path == "/status.json"
        request_started_ns = time.time_ns()

        def status_failed() -> None:
            if is_status and hasattr(self.server.session_adapter, "camera_status_failed"):
                self.server.session_adapter.camera_status_failed(acquired_at_ns=request_started_ns)

        if not self.server.camera_slots.acquire(blocking=False):
            status_failed()
            self._reply(503)
            return
        if streaming and not self.server.stream_slots.acquire(blocking=False):
            self.server.camera_slots.release()
            self._reply(503)
            return
        connection = None
        try:
            connection = http.client.HTTPConnection(self.server.camera_host, self.server.camera_port, timeout=2)
            connection.request("GET", camera_path, headers={"Authorization": self.server.camera_authorization})
            response = connection.getresponse()
            if response.status != 200:
                status_failed()
                self._reply(503)
                return
            if streaming:
                mime = response.getheader("Content-Type", "")
                if not mime.startswith("multipart/x-mixed-replace; boundary=frame"):
                    self._reply(502)
                    return
                self._headers(200, mime, extra={"Connection": "close"})
                while not self.server.stop_event.is_set():
                    # read(32768) waits to fill its buffer; a sparse MJPEG source
                    # must deliver each available part without that delay.
                    block = response.read1(32768)
                    if not block:
                        break
                    self.wfile.write(block)
                    self.wfile.flush()
            else:
                length = response.getheader("Content-Length")
                if length is None or not length.isdecimal() or int(length) > 1_000_000:
                    status_failed()
                    self._reply(502)
                    return
                body = response.read(int(length))
                if len(body) != int(length):
                    status_failed()
                    self._reply(502)
                    return
                mime = "application/json" if camera_path == "/status.json" else "image/jpeg"
                if is_status and hasattr(self.server.session_adapter, "camera_status"):
                    try:
                        if not self.server.session_adapter.camera_status(
                            json.loads(body), acquired_at_ns=request_started_ns
                        ):
                            self._reply(502)
                            return
                    except (ValueError, TypeError):
                        status_failed()
                        self._reply(502)
                        return
                extra = {}
                if mime == "image/jpeg":
                    for name in ("X-Frame-Sequence", "X-Frame-Age-Ms"):
                        value = response.getheader(name)
                        if value is not None:
                            extra[name] = value
                self._headers(200, mime, len(body), extra=extra)
                self.wfile.write(body)
        except (OSError, ValueError, http.client.HTTPException, BrokenPipeError, ConnectionResetError):
            status_failed()
            try:
                self._reply(503)
            except (OSError, ValueError):
                pass
        finally:
            if connection is not None:
                connection.close()
            if streaming:
                self.server.stream_slots.release()
            self.server.camera_slots.release()

    def do_POST(self):
        if not self._base_check():
            return
        target = urlsplit(self.path)
        if target.query or target.path not in {"/api/operation", "/api/body"}:
            self._drain_small_post_body()
            self._reply(404)
            return
        if (
            not self._valid_cookie() or self.headers.get_all("Origin", []) != [self._origin()]
            or self.headers.get_all("X-AM1-CSRF", []) != [self.server.csrf_token]
            or self.headers.get("Content-Type") != "application/json"
        ):
            self._drain_small_post_body()
            self._reply(403)
            return
        length = self.headers.get("Content-Length", "")
        if not length.isdecimal() or int(length) > MAX_POST_BYTES:
            self._drain_small_post_body()
            self._reply(413)
            return
        try:
            payload = json.loads(self.rfile.read(int(length)))
        except (ValueError, UnicodeDecodeError):
            self._reply(400)
            return
        if not isinstance(payload, dict):
            self._reply(400)
            return
        if target.path == "/api/operation":
            if payload.get("kind") not in ALLOWED_OPERATIONS:
                self._reply(400)
                return
            result = self.server.session_adapter.operation(payload)
        else:
            if not isinstance(payload.get("keys"), list) or any(key not in ALLOWED_BODY_KEYS for key in payload["keys"]):
                self._reply(400)
                return
            result = self.server.session_adapter.body_input(payload)
        self._json(200, result)

    do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_TRACE = do_CONNECT = do_HEAD = lambda self: self._reply(403)


def open_console_browser(address: str, state_directory: Path, *, direct: bool = False) -> None:
    """Opt-in local-only Edge process; never alter system/default-profile proxy settings."""
    if not direct:
        webbrowser.open(address)
        return
    target = urlsplit(address)
    if (sys.platform != "win32" or target.scheme != "http" or target.hostname != "127.0.0.1"
            or target.username is not None or target.password is not None):
        raise ValueError("Direct console browser requires a Windows loopback HTTP address")
    candidates = [Path(root) / "Microsoft/Edge/Application/msedge.exe"
                  for name in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")
                  if (root := os.environ.get(name))]
    edge = next((path for path in candidates if path.is_file()), None)
    if edge is None and (installed := shutil.which("msedge")):
        edge = Path(installed)
    if edge is None:
        raise FileNotFoundError("Microsoft Edge is unavailable for the direct console browser; no profile fallback")
    # A distinct profile gives these process flags effect even if ordinary Edge
    # is already open. Retain it privately; never change/delete the user's profile.
    profile = state_directory.resolve() / "edge-console-direct"
    profile.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([str(edge), "--no-proxy-server", f"--user-data-dir={profile}",
                      "--no-first-run", "--no-default-browser-check", f"--app={address}"],
                     shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)


def run_console(config_path: Path, *, no_browser: bool = False, direct_browser: bool = False,
                session_module=None) -> int:
    """Open only a loopback viewer/controller; session Start remains explicit."""
    if session_module is None:
        from tools import am1_session as session_module

    try:
        config = session_module.SessionConfig.load(config_path)
        auth_file = config.console_camera_auth_file
        if auth_file is None:
            raise ValueError("private console_camera_auth_file is not configured")
        repository = Path(__file__).resolve().parents[1]
        adapter = ConsoleSessionAdapter(config, repository, session_module)
        server = ConsoleServer(("127.0.0.1", 8765), config, auth_file, adapter)
    except (session_module.SessionError, OSError, ValueError) as exc:
        print(f"AM1 console refused before session start: {exc}")
        return 2
    primary_error: BaseException | None = None
    close_error: BaseException | None = None
    try:
        address = f"http://127.0.0.1:{server.server_address[1]}/"
        print(f"AM1 console: {address} (opening it never starts a robot)", flush=True)
        if not no_browser:
            if direct_browser:
                open_console_browser(address, config.local_state_directory, direct=True)
                print("AM1 console browser: dedicated Edge profile, process-local direct routing", flush=True)
            else:
                webbrowser.open(address)
        server.serve_forever()
    except KeyboardInterrupt:
        print("AM1 console stopping; checking its exact session owner.", flush=True)
    except BaseException as exc:
        primary_error = exc
    finally:
        try:
            server.server_close()
        except BaseException as exc:
            close_error = exc
    state = adapter.state()
    if state["session_id"] is not None and not adapter.wait(0):
        try:
            stop = adapter.operation({"kind": "Stop", "session_id": state["session_id"]})
            stopped = bool(stop.get("accepted")) and adapter.wait(30)
        except BaseException as exc:
            stopped = False
            if primary_error is None:
                primary_error = exc
        if not stopped:
            print("AM1 console exited with session cleanup unverified; use the existing exact-session Stop/Collect fallback.")
            return 3
    if primary_error is not None:
        print(f"AM1 console failed: {type(primary_error).__name__}: {primary_error}")
    if close_error is not None:
        print(f"AM1 console listener cleanup also failed: {type(close_error).__name__}: {close_error}")
    if close_error is not None:
        return 3
    if primary_error is not None:
        return 2
    if state.get("final_exit_code") not in (None, 0):
        return int(state["final_exit_code"])
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Loopback AM1 camera and supervised Local control console")
    parser.add_argument("--config", type=Path, required=True, help="existing private AM1 session configuration")
    browsers = parser.add_mutually_exclusive_group()
    browsers.add_argument("--no-browser", action="store_true", help="serve without opening a browser tab")
    browsers.add_argument("--direct-browser", action="store_true",
                          help="open dedicated Windows Edge console with process-local direct routing")
    args = parser.parse_args(argv)
    return run_console(args.config, no_browser=args.no_browser, direct_browser=args.direct_browser)


if __name__ == "__main__":
    raise SystemExit(main())
