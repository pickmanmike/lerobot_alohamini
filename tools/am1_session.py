#!/usr/bin/env python

"""Run one bounded supervised AM1 LAN camera-plus-Local session from Windows."""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import urlsplit


DURATION_PATTERN = re.compile(r"^(?:[1-9]|[1-9][0-9]{1,2}|1[0-7][0-9]{2}|1800)$")
SESSION_ID_PATTERN = re.compile(r"^[0-9]{8}T[0-9]{6}-[0-9a-f]{8}$")
REMOTE_PATH_PATTERN = re.compile(r"^/[A-Za-z0-9._/-]+$")
REMOTE_LOG_NAME_PATTERN = re.compile(r"^am1-[A-Za-z0-9][A-Za-z0-9._-]*\.log$")
STOP_CONFIRMATION_TIMEOUT_S = 120.0
PERSISTED_STATE_ATTEMPTS = 3
PERSISTED_STATE_CONNECT_TIMEOUT_S = 4
PERSISTED_STATE_COMMAND_TIMEOUT_S = 8
PERSISTED_STATE_RETRY_DELAY_S = 0.25
SSH_STDERR_LIMIT = 4_000


class SessionError(RuntimeError):
    pass


class SessionStopped(RuntimeError):
    pass


class LocalSessionLock:
    """Hold one OS-backed controller lock without leaving a stale refusal file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.stream: Any | None = None

    def __enter__(self) -> "LocalSessionLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a+b")
        self.stream.seek(0, os.SEEK_END)
        if self.stream.tell() == 0:
            self.stream.write(b"\0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            self.stream.close()
            self.stream = None
            raise SessionError("Another AM1 unified session controller is active; refusing a second session.") from exc
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        del exc_type, exc, traceback
        if self.stream is None:
            return
        try:
            self.stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        finally:
            self.stream.close()
            self.stream = None


def parse_duration_seconds(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("DurationSeconds must be a whole number from 1 through 1800.")
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, str):
        text = value
    else:
        raise ValueError("DurationSeconds must be a whole number from 1 through 1800.")
    if not DURATION_PATTERN.fullmatch(text):
        raise ValueError("DurationSeconds must be a whole number from 1 through 1800.")
    return int(text)


@dataclass(frozen=True)
class SessionConfig:
    windows_python: Path
    local_config: Path
    local_state_directory: Path
    ssh_target: str
    browser_url: str
    remote_python: str
    remote_helper: str
    remote_session_repository: str
    remote_session_head: str
    remote_camera_repository: str
    remote_camera_head: str
    remote_camera_python: str
    remote_motor_repository: str
    remote_motor_head: str
    remote_log_directory: str
    remote_state_directory: str
    windows_log_directory: Path

    @classmethod
    def load(cls, path: Path) -> "SessionConfig":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SessionError(f"Unable to read the private session config {path}: {exc}") from exc
        if data.get("version") != 1:
            raise SessionError("Private AM1 session config version must be 1.")
        required = {
            "windows_python", "local_config", "local_state_directory", "ssh_target", "browser_url",
            "remote_python", "remote_helper", "remote_session_repository", "remote_session_head",
            "remote_camera_repository", "remote_camera_head", "remote_camera_python",
            "remote_motor_repository", "remote_motor_head", "remote_log_directory",
            "remote_state_directory", "windows_log_directory",
        }
        missing = sorted(required - set(data))
        if missing:
            raise SessionError("Private AM1 session config is missing: " + ", ".join(missing))
        values = {key: data[key] for key in required}
        for key, value in values.items():
            if not isinstance(value, str) or not value.strip():
                raise SessionError(f"Private AM1 session config field {key} must be a non-empty string.")
        for key in ("remote_session_head", "remote_camera_head", "remote_motor_head"):
            if not re.fullmatch(r"[0-9a-f]{40}", values[key]):
                raise SessionError(f"Private AM1 session config field {key} must be an exact commit SHA.")
        for key in (
            "remote_python",
            "remote_helper",
            "remote_session_repository",
            "remote_camera_repository",
            "remote_camera_python",
            "remote_motor_repository",
            "remote_log_directory",
            "remote_state_directory",
        ):
            remote_path = PurePosixPath(values[key])
            if (
                not REMOTE_PATH_PATTERN.fullmatch(values[key])
                or "//" in values[key]
                or ".." in remote_path.parts
            ):
                raise SessionError(f"Private AM1 session config field {key} must be a safe absolute Pi path.")
        if any(ch.isspace() for ch in values["ssh_target"]):
            raise SessionError("SSH target must not contain whitespace.")
        browser = urlsplit(values["browser_url"])
        if browser.scheme not in {"http", "https"} or not browser.hostname or browser.username or browser.password:
            raise SessionError("Browser URL must be HTTP(S) with no embedded credentials.")
        return cls(
            windows_python=Path(values["windows_python"]),
            local_config=Path(values["local_config"]),
            local_state_directory=Path(values["local_state_directory"]),
            ssh_target=values["ssh_target"],
            browser_url=values["browser_url"],
            remote_python=values["remote_python"],
            remote_helper=values["remote_helper"],
            remote_session_repository=values["remote_session_repository"],
            remote_session_head=values["remote_session_head"],
            remote_camera_repository=values["remote_camera_repository"],
            remote_camera_head=values["remote_camera_head"],
            remote_camera_python=values["remote_camera_python"],
            remote_motor_repository=values["remote_motor_repository"],
            remote_motor_head=values["remote_motor_head"],
            remote_log_directory=values["remote_log_directory"],
            remote_state_directory=values["remote_state_directory"],
            windows_log_directory=Path(values["windows_log_directory"]),
        )


@dataclass
class SessionOutcome:
    session_id: str
    requested_duration_seconds: int
    operational_exit_code: int
    final_exit_code: int
    cleanup_verified: bool
    remote_logs: list[str] = field(default_factory=list)
    missing_logs: list[str] = field(default_factory=list)
    cleanup: dict[str, Any] = field(default_factory=dict)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    failure: str | None = None
    sources: dict[str, str] = field(default_factory=dict)
    sync_timing: dict[str, Any] | None = None
    actual_live_duration_seconds: float | None = None
    started_at: str | None = None
    finished_at: str | None = None


class SessionCoordinator:
    """Small dependency-injected lifecycle core used by the real wrapper and fake tests."""

    def __init__(
        self,
        *,
        remote: Any,
        client: Any,
        open_browser: Callable[[str], None],
        collect_remote_log: Callable[[str, Path], tuple[bool, str | None]],
        input_fn: Callable[[str], str] = input,
        on_cleanup: Callable[[SessionOutcome], None] | None = None,
    ) -> None:
        self.remote = remote
        self.client = client
        self.open_browser = open_browser
        self.collect_remote_log = collect_remote_log
        self.input_fn = input_fn
        self.on_cleanup = on_cleanup

    def _input_with_stop(
        self,
        prompt: str,
        stop_requested: Callable[[], bool],
        remote_fault: Callable[[], Any] | None = None,
    ) -> str:
        if stop_requested():
            raise SessionStopped("session stop requested while waiting for operator readiness")
        responses: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)

        def read_input() -> None:
            try:
                responses.put(("value", self.input_fn(prompt)))
            except BaseException as exc:
                responses.put(("error", exc))

        threading.Thread(target=read_input, name="am1-session-input", daemon=True).start()
        while True:
            if stop_requested():
                raise SessionStopped("session stop requested while waiting for operator readiness")
            if remote_fault is not None and (fault := remote_fault()) is not None:
                raise SessionError(f"Pi session fault while waiting for operator readiness: {fault}")
            try:
                kind, value = responses.get(timeout=0.05)
            except queue.Empty:
                continue
            if kind == "error":
                raise value
            if remote_fault is not None and (fault := remote_fault()) is not None:
                raise SessionError(f"Pi session fault while waiting for operator readiness: {fault}")
            return str(value)

    def run(
        self,
        *,
        duration_seconds: int,
        session_id: str,
        session_directory: Path,
        client_log_path: Path,
        stop_requested: Callable[[], bool],
    ) -> SessionOutcome:
        outcome = SessionOutcome(
            session_id=session_id,
            requested_duration_seconds=duration_seconds,
            operational_exit_code=2,
            final_exit_code=2,
            cleanup_verified=False,
            started_at=datetime.now().astimezone().isoformat(),
        )
        remote_logs: list[str] = []
        remote_started = False
        remote_fault_observed_after_client_result: dict[str, Any] | None = None
        try:
            if hasattr(self.remote, "set_stop_requested"):
                self.remote.set_stop_requested(stop_requested)
            remote_started = True
            preflight = self.remote.preflight()
            print("AM1 session phase: Pi source and ownership preflight passed.", flush=True)
            outcome.sources = {
                key: str(value)
                for key, value in preflight.items()
                if key.endswith("_source_head")
            }
            camera = self.remote.start_camera()
            if camera.get("camera_log"):
                remote_logs.append(camera["camera_log"])
            self.open_browser(camera["browser_url"])
            print("AM1 session phase: all five camera sources are ready; the read-only viewer is open.", flush=True)
            print(
                "CONFIRMATION 1/3 — Verify all five required views are usable, the workspace is clear, "
                "carriage support and motor-power removal are accessible, and established robot/leader "
                "power is ready. Press Enter only to start the motor host.",
                flush=True,
            )
            approval = self._input_with_stop(
                "",
                stop_requested,
                getattr(self.remote, "fault", None),
            )
            if approval != "":
                outcome.failure = "physical/view readiness requires Enter only; non-empty input was refused"
                outcome.operational_exit_code = 2
            elif stop_requested():
                outcome.failure = "session stop requested before motor activation"
                outcome.operational_exit_code = 130
            else:
                print("AM1 session phase: operator readiness confirmed; starting the motor host.", flush=True)
                host = self.remote.start_host()
                if host.get("host_log"):
                    remote_logs.insert(0, host["host_log"])
                print(
                    "AM1 session phase: motor host is operational; handing the console to the Windows Local client.",
                    flush=True,
                )
                outcome.operational_exit_code = int(
                    self.client.run(
                        duration_seconds=duration_seconds,
                        log_path=client_log_path,
                        stop_requested=stop_requested,
                    )
                )
                if outcome.operational_exit_code != 0:
                    refusal = None
                    try:
                        for line in reversed(client_log_path.read_text(encoding="utf-8", errors="replace").splitlines()):
                            if line.startswith("SAFETY REFUSAL: "):
                                refusal = line.partition(": ")[2]
                                break
                    except OSError:
                        pass
                    outcome.failure = (
                        f"Windows Local client safety refusal: {refusal}"
                        if refusal is not None
                        else f"Windows Local client exited with status {outcome.operational_exit_code}"
                    )
                if hasattr(self.remote, "fault") and (remote_fault := self.remote.fault()) is not None:
                    if outcome.failure is None:
                        outcome.failure = "Pi session fault: " + json.dumps(remote_fault, sort_keys=True)
                        outcome.operational_exit_code = 2
                    else:
                        remote_fault_observed_after_client_result = dict(remote_fault)
        except (KeyboardInterrupt, SessionStopped):
            outcome.failure = "operator interrupt"
            outcome.operational_exit_code = 130
        except BaseException as exc:
            outcome.failure = f"{type(exc).__name__}: {exc}"
            outcome.operational_exit_code = 2
        finally:
            cleanup: dict[str, Any]
            if remote_started:
                try:
                    cleanup = dict(self.remote.stop())
                except BaseException as exc:
                    cleanup = {"cleanup_verified": False, "cleanup_error": f"{type(exc).__name__}: {exc}"}
            else:
                cleanup = {"cleanup_verified": True, "nothing_started": True}
            client_cleanup = (
                dict(self.client.cleanup_status())
                if hasattr(self.client, "cleanup_status")
                else {"cleanup_verified": True, "not_reported": True}
            )
            cleanup["client"] = client_cleanup
            if remote_fault_observed_after_client_result is not None:
                cleanup["remote_fault_observed_after_client_result"] = remote_fault_observed_after_client_result
            outcome.cleanup = cleanup
            outcome.cleanup_verified = bool(cleanup.get("cleanup_verified")) and bool(
                client_cleanup.get("cleanup_verified")
            )
            terminal_status = cleanup.get("terminal_status") or cleanup.get("persisted_status")
            if terminal_status in {"fault", "refused"} and outcome.operational_exit_code == 0:
                outcome.failure = f"Pi session ended in terminal state {terminal_status}"
                outcome.operational_exit_code = 2
            for key in ("host_log", "camera_log"):
                if cleanup.get(key) and cleanup[key] not in remote_logs:
                    if key == "host_log":
                        remote_logs.insert(0, cleanup[key])
                    else:
                        remote_logs.append(cleanup[key])
            outcome.remote_logs = list(remote_logs)

            if outcome.operational_exit_code != 0:
                outcome.final_exit_code = outcome.operational_exit_code
            elif not outcome.cleanup_verified:
                outcome.final_exit_code = 3
            else:
                outcome.final_exit_code = 0
            if self.on_cleanup is not None:
                try:
                    self.on_cleanup(outcome)
                except BaseException as exc:
                    outcome.cleanup["local_cleanup_callback_error"] = f"{type(exc).__name__}: {exc}"
                    outcome.cleanup_verified = False

            if client_log_path.exists():
                try:
                    start_ns = None
                    end_ns = None
                    paused_causes: dict[int, str] = {}
                    for line in client_log_path.read_text(encoding="utf-8", errors="replace").splitlines():
                        if not line.startswith("{"):
                            continue
                        try:
                            payload = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if payload.get("event") == "am1_startup_sync_timing":
                            outcome.sync_timing = payload
                        elif payload.get("event") == "am1_local_paused":
                            if type(payload.get("epoch")) is int and isinstance(payload.get("cause"), str):
                                paused_causes[payload["epoch"]] = payload["cause"]
                        elif payload.get("event") == "am1_local_recovered":
                            epoch = payload.get("epoch")
                            if type(epoch) is int and epoch - 1 in paused_causes:
                                outcome.warnings.append({
                                    "kind": "recovered_observation_gap",
                                    "epoch": epoch,
                                    "cause": paused_causes[epoch - 1],
                                    "pause_duration_s": payload.get("pause_duration_s"),
                                    "resume_mode": payload.get("resume_mode"),
                                })
                        elif payload.get("event") == "am1_client_live_start":
                            start_ns = payload.get("wall_time_ns")
                        elif payload.get("event") == "am1_client_action_cadence":
                            end_ns = payload.get("live_end_wall_time_ns")
                    if isinstance(start_ns, int) and isinstance(end_ns, int) and end_ns >= start_ns:
                        outcome.actual_live_duration_seconds = round((end_ns - start_ns) / 1e9, 6)
                except OSError:
                    pass

            missing: list[dict[str, str]] = []
            for remote_path in remote_logs:
                destination = session_directory / PurePosixPath(remote_path).name
                try:
                    ok, error = self.collect_remote_log(remote_path, destination)
                except BaseException as exc:
                    ok = False
                    error = f"{type(exc).__name__}: {exc}"
                if not ok:
                    missing.append(
                        {
                            "remote_path": remote_path,
                            "destination": str(destination),
                            "error": error or "unknown",
                        }
                    )
            outcome.missing_logs = [entry["remote_path"] for entry in missing]
            if missing:
                (session_directory / "missing-logs.json").write_text(
                    json.dumps({"session_id": session_id, "missing": missing}, indent=2) + "\n",
                    encoding="utf-8",
                )

            if outcome.operational_exit_code != 0:
                outcome.final_exit_code = outcome.operational_exit_code
            elif not outcome.cleanup_verified:
                outcome.final_exit_code = 3
            elif missing:
                outcome.final_exit_code = 4
            else:
                outcome.final_exit_code = 0
            outcome.finished_at = datetime.now().astimezone().isoformat()
            (session_directory / "session-summary.json").write_text(
                json.dumps(asdict(outcome), indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        return outcome


class SSHRemote:
    def __init__(self, config: SessionConfig, session_id: str, session_directory: Path) -> None:
        self.config = config
        self.session_id = session_id
        self.session_directory = session_directory
        self.process: subprocess.Popen[str] | None = None
        self.events: queue.Queue[dict[str, Any]] = queue.Queue()
        self.stderr_stream = None
        self.reader: threading.Thread | None = None
        self._fault: dict[str, Any] | None = None
        self._later_errors: list[dict[str, Any]] = []
        self._fault_lock = threading.Lock()
        self._stop_requested: Callable[[], bool] = lambda: False
        self._send_lock = threading.Lock()
        self._heartbeat_stop = threading.Event()
        self._heartbeat: threading.Thread | None = None
        self.stderr_path = self.session_directory / "ssh-control.log"

    def set_stop_requested(self, stop_requested: Callable[[], bool]) -> None:
        self._stop_requested = stop_requested

    def _command(self) -> list[str]:
        return [
            "ssh",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=10",
            "-o", "ConnectionAttempts=1",
            "-o", "ServerAliveInterval=2",
            "-o", "ServerAliveCountMax=3",
            self.config.ssh_target,
            self.config.remote_python,
            self.config.remote_helper,
            "supervise",
            "--session-id", self.session_id,
            "--session-repository", self.config.remote_session_repository,
            "--session-head", self.config.remote_session_head,
            "--camera-repository", self.config.remote_camera_repository,
            "--camera-head", self.config.remote_camera_head,
            "--camera-python", self.config.remote_camera_python,
            "--motor-repository", self.config.remote_motor_repository,
            "--motor-head", self.config.remote_motor_head,
            "--log-directory", self.config.remote_log_directory,
            "--state-directory", self.config.remote_state_directory,
        ]

    def _read_events(self) -> None:
        assert self.process and self.process.stdout
        for line in self.process.stdout:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            self.events.put(event)
            if event.get("event") in {"runtime_fault", "refused", "fault"}:
                self._record_fault(event)

    def _record_fault(self, event: dict[str, Any]) -> None:
        recorded = dict(event)
        with self._fault_lock:
            if self._fault is None:
                self._fault = recorded
            elif recorded != self._fault and recorded not in self._later_errors:
                self._later_errors.append(recorded)

    def _ssh_stderr(self) -> str:
        if self.stderr_stream is not None:
            try:
                self.stderr_stream.flush()
            except OSError:
                pass
        try:
            text = self.stderr_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return text[-SSH_STDERR_LIMIT:].strip()

    def _record_ssh_exit(self, context: str, *, relation: str = "before") -> dict[str, Any]:
        exit_status = self.process.returncode if self.process is not None else None
        event: dict[str, Any] = {
            "event": "fault",
            "reason": f"SSH controller exited with status {exit_status} {relation} {context}",
            "ssh_exit_status": exit_status,
        }
        if stderr := self._ssh_stderr():
            event["ssh_stderr"] = stderr
        self._record_fault(event)
        return event

    def diagnostics(self) -> dict[str, Any]:
        with self._fault_lock:
            return {
                "primary_fault": dict(self._fault) if self._fault is not None else None,
                "later_errors": [dict(error) for error in self._later_errors],
            }

    def _diagnostic_fields(self) -> dict[str, Any]:
        diagnostics = self.diagnostics()
        fields: dict[str, Any] = {}
        if diagnostics["primary_fault"] is not None:
            fields["primary_fault"] = diagnostics["primary_fault"]
        if diagnostics["later_errors"]:
            fields["later_errors"] = diagnostics["later_errors"]
        return fields

    def _wait(
        self,
        expected: str,
        timeout: float,
        *,
        allow_prior_fault: bool = False,
        ignore_stop_request: bool = False,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._stop_requested() and not ignore_stop_request:
                raise SessionStopped(f"session stop requested while waiting for Pi event {expected}")
            if self.process and self.process.poll() is not None and self.events.empty():
                self._record_ssh_exit(expected)
                raise SessionError(f"Pi session fault: {self.fault()}")
            if self._fault is not None and not allow_prior_fault:
                raise SessionError(f"Pi session fault: {self._fault}")
            try:
                event = self.events.get(timeout=min(0.1, max(deadline - time.monotonic(), 0.01)))
            except queue.Empty:
                continue
            if event.get("event") == expected:
                return event
            if event.get("event") in {"runtime_fault", "refused", "fault"}:
                self._record_fault(event)
                if not allow_prior_fault:
                    raise SessionError(f"Pi session fault: {self.fault()}")
        raise SessionError(f"Timed out waiting for Pi session event {expected}.")

    def _send(self, command: str) -> None:
        with self._send_lock:
            if not self.process or not self.process.stdin or self.process.poll() is not None:
                raise SessionError("Pi session control link is not available.")
            self.process.stdin.write(command + "\n")
            self.process.stdin.flush()

    def _heartbeat_loop(self) -> None:
        while not self._heartbeat_stop.wait(1.0):
            try:
                self._send("HEARTBEAT")
            except BaseException as exc:
                self._record_fault({
                    "event": "fault",
                    "reason": f"controller heartbeat failed: {type(exc).__name__}: {exc}",
                })
                return

    def preflight(self) -> dict[str, Any]:
        self.stderr_stream = self.stderr_path.open("w", encoding="utf-8")
        self.process = subprocess.Popen(
            self._command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.stderr_stream,
            text=True,
            bufsize=1,
        )
        if self.process.stdin is not None:
            try:
                os.set_blocking(self.process.stdin.fileno(), False)
            except (AttributeError, OSError):
                pass
        self.reader = threading.Thread(target=self._read_events, name="am1-session-events", daemon=True)
        self.reader.start()
        event = self._wait("preflight_ready", 30.0)
        self._heartbeat = threading.Thread(target=self._heartbeat_loop, name="am1-session-heartbeat", daemon=True)
        self._heartbeat.start()
        return event

    def start_camera(self) -> dict[str, Any]:
        self._send("START_CAMERA")
        event = self._wait("camera_ready", 60.0)
        configured_url = self.config.browser_url.rstrip("/")
        reported_url = str(event.get("browser_url", "")).rstrip("/")
        if configured_url != reported_url:
            raise SessionError(f"Camera URL mismatch: configured {configured_url}, reported {reported_url}")
        return event

    def start_host(self) -> dict[str, Any]:
        self._send("START_HOST")
        return self._wait("host_ready", 270.0)

    def fault(self) -> dict[str, Any] | None:
        with self._fault_lock:
            return dict(self._fault) if self._fault is not None else None

    def _persisted_terminal_state(self) -> dict[str, Any]:
        errors: list[str] = []
        nonterminal_observations: list[dict[str, Any]] = []
        latest_nonterminal: dict[str, Any] | None = None
        terminal_states = {"complete", "fault", "cleanup_unknown", "refused"}
        command = [
            "ssh", "-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={PERSISTED_STATE_CONNECT_TIMEOUT_S}",
            "-o", "ConnectionAttempts=1",
            "-o", "ServerAliveInterval=2",
            "-o", "ServerAliveCountMax=3",
            self.config.ssh_target, self.config.remote_python, self.config.remote_helper, "state",
            "--state-directory", self.config.remote_state_directory, "--session-id", self.session_id,
        ]
        for attempt in range(1, PERSISTED_STATE_ATTEMPTS + 1):
            try:
                completed = subprocess.run(
                    command,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=PERSISTED_STATE_COMMAND_TIMEOUT_S,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                errors.append(f"attempt {attempt}: SSH state query failed: {type(exc).__name__}: {exc}")
            else:
                stderr = completed.stderr.strip()[-SSH_STDERR_LIMIT:]

                def with_stderr(message: str) -> str:
                    return f"{message}; stderr: {stderr}" if stderr else message

                if completed.returncode not in {0, 2}:
                    message = f"attempt {attempt}: SSH state query exited {completed.returncode}"
                    errors.append(f"{message}: {stderr}" if stderr else message)
                else:
                    try:
                        state = json.loads(completed.stdout)
                    except json.JSONDecodeError as exc:
                        errors.append(
                            with_stderr(f"attempt {attempt}: persisted state was not valid JSON: {exc}")
                        )
                    else:
                        if not isinstance(state, dict):
                            errors.append(
                                with_stderr(f"attempt {attempt}: persisted state was not an object")
                            )
                        elif state.get("session_id") != self.session_id:
                            errors.append(
                                with_stderr(f"attempt {attempt}: persisted session identity did not match")
                            )
                        else:
                            status = state.get("status")
                            terminal = status in terminal_states
                            cleanup = state.get("cleanup") if isinstance(state.get("cleanup"), dict) else {}
                            recovered: dict[str, Any] = {
                                **cleanup,
                                "cleanup_verified": terminal and bool(cleanup.get("cleanup_verified")),
                                "host_log": state.get("host_log"),
                                "camera_log": state.get("camera_log"),
                                "persisted_status": status,
                                "terminal_status": status if terminal else None,
                                "stop_reason": state.get("stop_reason"),
                                "persisted_state_available": True,
                                "persisted_state_terminal": terminal,
                            }
                            if errors:
                                recovered["persisted_state_errors"] = list(errors)
                            if terminal:
                                if nonterminal_observations:
                                    recovered["persisted_state_observations"] = list(nonterminal_observations)
                                return recovered
                            recovered["cleanup_error"] = f"Persisted remote state is nonterminal: {status!r}"
                            latest_nonterminal = recovered
                            nonterminal_observations.append({"attempt": attempt, "status": status})
            if attempt < PERSISTED_STATE_ATTEMPTS:
                time.sleep(PERSISTED_STATE_RETRY_DELAY_S)
        if latest_nonterminal is not None:
            if errors:
                latest_nonterminal["persisted_state_errors"] = list(errors)
            latest_nonterminal["persisted_state_observations"] = list(nonterminal_observations)
            return latest_nonterminal
        return {
            "cleanup_verified": False,
            "persisted_state_available": False,
            "persisted_state_terminal": False,
            "persisted_state_error": errors[-1] if errors else "persisted state query failed",
            "persisted_state_errors": errors,
        }

    def stop(self) -> dict[str, Any]:
        self._heartbeat_stop.set()
        if not self.process:
            return {"cleanup_verified": True, "nothing_started": True, **self._diagnostic_fields()}
        if self.process.poll() is None:
            control_stop_error: str | None = None
            try:
                self._send("STOP")
            except BaseException as exc:
                control_stop_error = f"{type(exc).__name__}: {exc}"
                if self.process.stdin:
                    try:
                        self.process.stdin.close()
                    except OSError:
                        pass
                try:
                    self.process.wait(timeout=50)
                except (subprocess.TimeoutExpired, TimeoutError):
                    return {
                        "cleanup_verified": False,
                        "cleanup_error": "Pi control link failed and remote supervisor did not exit after EOF",
                        "control_stop_error": control_stop_error,
                        **self._diagnostic_fields(),
                    }
                if self.process.returncode not in {0, None}:
                    self._record_ssh_exit("cleanup verification")
                persisted = self._persisted_terminal_state()
                if persisted.get("persisted_state_available"):
                    return {
                        **persisted,
                        "control_stop_error": control_stop_error,
                        **self._diagnostic_fields(),
                    }
                return {
                    **persisted,
                    "cleanup_verified": False,
                    "cleanup_error": "Pi control link failed and persisted remote cleanup was unavailable",
                    "control_stop_error": control_stop_error,
                    **self._diagnostic_fields(),
                }
            try:
                event = self._wait(
                    "cleanup_complete",
                    45.0,
                    allow_prior_fault=True,
                    ignore_stop_request=True,
                )
            except BaseException as exc:
                if self.process.stdin:
                    self.process.stdin.close()
                controller_active = False
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    controller_active = True
                    self._record_fault(
                        {
                            "event": "fault",
                            "reason": "SSH controller remained active after cleanup event wait failed",
                        }
                    )
                else:
                    if self.process.returncode not in {0, None}:
                        self._record_ssh_exit("cleanup event wait failure", relation="after")
                    if self.stderr_stream:
                        self.stderr_stream.close()
                persisted = self._persisted_terminal_state()
                result = {
                    **persisted,
                    "control_cleanup_error": f"{type(exc).__name__}: {exc}",
                    **self._diagnostic_fields(),
                }
                if controller_active:
                    return {
                        **result,
                        "persisted_cleanup_verified": bool(persisted.get("cleanup_verified")),
                        "cleanup_verified": False,
                        "cleanup_error": "SSH controller remained active after cleanup event wait failed",
                    }
                if persisted.get("persisted_state_available"):
                    return result
                return {
                    **result,
                    "cleanup_verified": False,
                    "cleanup_error": f"{type(exc).__name__}: {exc}",
                }
            if self.process.stdin:
                self.process.stdin.close()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                return {
                    "cleanup_verified": False,
                    "cleanup_error": "SSH controller remained active after remote cleanup",
                    **self._diagnostic_fields(),
                }
            if self.process.returncode not in {0, None}:
                self._record_ssh_exit("cleanup_complete", relation="after")
            if self.stderr_stream:
                self.stderr_stream.close()
            return {
                **{key: value for key, value in event.items() if key not in {"event", "session_id"}},
                **self._diagnostic_fields(),
            }
        if self.process.returncode not in {0, None}:
            self._record_ssh_exit("cleanup verification")
        persisted = self._persisted_terminal_state()
        if persisted.get("persisted_state_available"):
            return {**persisted, **self._diagnostic_fields()}
        return {
            **persisted,
            "cleanup_verified": False,
            "cleanup_error": (
                f"SSH controller already exited {self.process.returncode}; persisted remote state unavailable"
            ),
            **self._diagnostic_fields(),
        }


class WindowsClient:
    def __init__(
        self,
        repository: Path,
        config: SessionConfig,
        remote_fault: Callable[[], Any],
        stop_request_path: Path,
    ) -> None:
        self.repository = repository
        self.config = config
        self.remote_fault = remote_fault
        self.stop_request_path = stop_request_path
        self._cleanup_status: dict[str, Any] = {"cleanup_verified": True, "not_started": True}

    def cleanup_status(self) -> dict[str, Any]:
        return dict(self._cleanup_status)

    def _force_reap_owned_client(self, process: subprocess.Popen[Any]) -> bool:
        if process.poll() is not None:
            return True
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                    check=False,
                )
            else:
                process.terminate()
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired, TimeoutError):
            return False
        return process.poll() is not None

    def _request_stop(self) -> None:
        try:
            self.stop_request_path.write_text("stop\n", encoding="utf-8")
        except OSError as exc:
            raise SessionError(f"Unable to write the cooperative client stop request: {exc}") from exc

    def run(self, *, duration_seconds: int, log_path: Path, stop_requested: Callable[[], bool]) -> int:
        powershell = shutil.which("pwsh")
        if not powershell:
            raise SessionError("PowerShell 7 (pwsh) is required.")
        command = [
            powershell, "-NoLogo", "-NoProfile", "-File", str(self.repository / "tools" / "run_am1.ps1"),
            "-Mode", "Local", "-ConfigPath", str(self.config.local_config),
            "-DurationSeconds", str(duration_seconds), "-LogPath", str(log_path),
            "-StopRequestPath", str(self.stop_request_path),
        ]
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        process = subprocess.Popen(command, cwd=self.repository, creationflags=creationflags)
        self._cleanup_status = {
            "cleanup_verified": False,
            "state": "running",
            "client_wrapper_pid": process.pid,
        }
        stop_sent = False
        user_stop_requested = False
        stop_sent_at: float | None = None
        try:
            while process.poll() is None:
                if not stop_sent and stop_requested():
                    self._request_stop()
                    stop_sent = True
                    user_stop_requested = True
                    stop_sent_at = time.monotonic()
                elif not stop_sent and self.remote_fault() is not None:
                    self._request_stop()
                    stop_sent = True
                    stop_sent_at = time.monotonic()
                if stop_sent_at is not None and time.monotonic() - stop_sent_at >= 30.0:
                    raise SessionError("Windows client did not honor its cooperative stop request within 30 seconds.")
                time.sleep(0.1)
        except KeyboardInterrupt:
            self._request_stop()
            stop_sent = True
            user_stop_requested = True
            stop_sent_at = time.monotonic()
        except BaseException:
            reaped = self._force_reap_owned_client(process)
            self._cleanup_status = {
                "cleanup_verified": False,
                "state": "forced_after_client_error",
                "client_wrapper_pid": process.pid,
                "owned_process_reaped": reaped,
            }
            raise
        try:
            wrapper_exit = int(process.wait(timeout=20.0))
        except KeyboardInterrupt:
            reaped = self._force_reap_owned_client(process)
            self._cleanup_status = {
                "cleanup_verified": False,
                "state": "forced_after_repeated_interrupt",
                "client_wrapper_pid": process.pid,
                "owned_process_reaped": reaped,
            }
            return 130
        except subprocess.TimeoutExpired as exc:
            reaped = self._force_reap_owned_client(process)
            self._cleanup_status = {
                "cleanup_verified": False,
                "state": "forced_after_cleanup_timeout",
                "client_wrapper_pid": process.pid,
                "owned_process_reaped": reaped,
            }
            raise SessionError("Windows client did not complete bounded cleanup after stop request.") from exc
        client_exit: int | None = None
        try:
            for line in reversed(log_path.read_text(encoding="utf-8", errors="replace").splitlines()):
                if line.startswith("AM1_CLIENT_EXIT_CODE="):
                    client_exit = int(line.partition("=")[2])
                    break
        except (OSError, ValueError):
            pass
        self._cleanup_status = {
            "cleanup_verified": client_exit is not None,
            "state": "exited" if client_exit is not None else "exit_marker_missing",
            "client_wrapper_pid": process.pid,
            "wrapper_exit": wrapper_exit,
            "client_exit": client_exit,
            "cooperative_stop_requested": stop_sent,
        }
        effective_exit = client_exit if client_exit is not None else wrapper_exit
        return 130 if user_stop_requested and effective_exit == 0 else effective_exit


def _git_head(repository: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        text=True, capture_output=True, timeout=15, check=False,
    )
    if completed.returncode != 0:
        raise SessionError("Unable to inspect the Windows session worktree.")
    return completed.stdout.strip()


def validate_local_preflight(repository: Path, config: SessionConfig, duration_seconds: int) -> None:
    required = [config.windows_python, config.local_config, repository / "tools" / "run_am1.ps1"]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise SessionError("Required local session path is missing: " + ", ".join(missing))
    if _git_head(repository) != config.remote_session_head:
        raise SessionError("Windows session source does not match the reviewed session commit.")
    status = subprocess.run(
        ["git", "-C", str(repository), "status", "--porcelain"],
        text=True, capture_output=True, timeout=15, check=False,
    )
    if status.returncode != 0 or status.stdout.strip():
        raise SessionError("Windows session worktree must be clean.")
    powershell = shutil.which("pwsh")
    if not powershell:
        raise SessionError("PowerShell 7 (pwsh) is required.")
    preflight = subprocess.run(
        [
            powershell, "-NoLogo", "-NoProfile", "-File", str(repository / "tools" / "run_am1.ps1"),
            "-Mode", "Local", "-ConfigPath", str(config.local_config),
            "-DurationSeconds", str(duration_seconds), "-Preflight",
        ],
        cwd=repository,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=45,
        check=False,
    )
    if preflight.returncode != 0 or "AM1_LOCAL_PREFLIGHT_READY" not in preflight.stdout:
        detail = preflight.stderr.strip() or preflight.stdout.strip() or f"exit {preflight.returncode}"
        raise SessionError(f"Windows Local preflight failed before remote hardware startup: {detail}")
    config.windows_log_directory.mkdir(parents=True, exist_ok=True)
    config.local_state_directory.mkdir(parents=True, exist_ok=True)


def _collect_with_scp(config: SessionConfig, remote_path: str, destination: Path) -> tuple[bool, str | None]:
    candidate = PurePosixPath(remote_path)
    expected_directory = PurePosixPath(config.remote_log_directory)
    if candidate.parent != expected_directory or not REMOTE_LOG_NAME_PATTERN.fullmatch(candidate.name):
        return False, "refused remote log path outside the configured exact-log directory"
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.part")
    try:
        completed = subprocess.run(
            [
                "scp", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ConnectionAttempts=1",
                f"{config.ssh_target}:{remote_path}", str(temporary),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        temporary.unlink(missing_ok=True)
        return False, f"{type(exc).__name__}: {exc}"
    if completed.returncode != 0:
        temporary.unlink(missing_ok=True)
        return False, completed.stderr.strip() or f"scp exited {completed.returncode}"
    try:
        os.replace(temporary, destination)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        return False, f"{type(exc).__name__}: {exc}"
    return True, None


def _open_browser(url: str) -> None:
    if os.name != "nt":
        raise SessionError("The supervised entrypoint is Windows-only.")
    os.startfile(url)  # type: ignore[attr-defined]


def _new_session_id() -> str:
    return datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]


def _active_path(config: SessionConfig) -> Path:
    return config.local_state_directory / "active.json"


def _write_active(config: SessionConfig, payload: dict[str, Any]) -> None:
    path = _active_path(config)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _windows_pid_running(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    synchronize = 0x00100000
    wait_object_0 = 0x00000000
    wait_timeout = 0x00000102
    wait_failed = 0xFFFFFFFF
    error_access_denied = 5
    error_invalid_parameter = 87

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(synchronize, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error == error_invalid_parameter:
            return False
        if error == error_access_denied:
            raise SessionError(f"Windows process {pid} liveness query failed: access denied.")
        raise SessionError(f"Windows process {pid} liveness could not be verified (error {error}).")

    wait_result = kernel32.WaitForSingleObject(handle, 0)
    wait_error = ctypes.get_last_error() if wait_result == wait_failed else 0
    if not kernel32.CloseHandle(handle):
        close_error = ctypes.get_last_error()
        if wait_result == wait_failed:
            raise SessionError(
                f"Windows process {pid} liveness could not be verified "
                f"(wait error {wait_error}; handle-close error {close_error})."
            )
        raise SessionError(
            f"Windows process {pid} liveness was read but its query handle could not be closed "
            f"(error {close_error})."
        )

    if wait_result == wait_timeout:
        return True
    if wait_result == wait_object_0:
        return False
    if wait_result == wait_failed:
        raise SessionError(f"Windows process {pid} liveness could not be verified (wait error {wait_error}).")
    raise SessionError(
        f"Windows process {pid} liveness returned an unexpected wait result 0x{wait_result:08x}."
    )


def _pid_running(pid: int) -> bool:
    if pid <= 0:
        raise SessionError(f"Invalid AM1 session controller PID: {pid}.")
    if os.name == "nt":
        return _windows_pid_running(pid)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _run_start_locked(repository: Path, config: SessionConfig, duration_seconds: int) -> int:
    active_path = _active_path(config)
    if active_path.exists():
        try:
            active = json.loads(active_path.read_text(encoding="utf-8"))
            if active.get("status") == "active" and _pid_running(int(active["controller_pid"])):
                raise SessionError(f"AM1 session {active['session_id']} is already active; refusing a second session.")
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            pass

    session_id = _new_session_id()
    if not SESSION_ID_PATTERN.fullmatch(session_id):
        raise AssertionError("generated invalid session identity")
    session_directory = config.windows_log_directory / f"am1-session-{session_id}"
    session_directory.mkdir(mode=0o700)
    stop_request = config.local_state_directory / f"stop-{session_id}"
    client_log = session_directory / f"am1-local-windows-{session_id}.log"
    _write_active(
        config,
        {
            "session_id": session_id,
            "controller_pid": os.getpid(),
            "session_directory": str(session_directory),
            "stop_request": str(stop_request),
            "status": "active",
        },
    )
    print(f"AM1_SESSION_ID={session_id}", flush=True)
    print(f"AM1_SESSION_RESULT={session_directory}", flush=True)
    print("AM1 session phase: starting bounded source and ownership preflight.", flush=True)
    remote = SSHRemote(config, session_id, session_directory)
    client = WindowsClient(repository, config, remote.fault, stop_request)
    def record_hardware_cleanup(outcome: SessionOutcome) -> None:
        _write_active(
            config,
            {
                "session_id": session_id,
                "controller_pid": os.getpid(),
                "session_directory": str(session_directory),
                "status": "collecting" if outcome.cleanup_verified else "cleanup_unknown",
                "operational_exit_code": outcome.operational_exit_code,
                "final_exit_code": outcome.final_exit_code,
                "cleanup_verified": outcome.cleanup_verified,
            },
        )

    outcome = SessionCoordinator(
        remote=remote,
        client=client,
        open_browser=_open_browser,
        collect_remote_log=lambda remote_path, destination: _collect_with_scp(config, remote_path, destination),
        on_cleanup=record_hardware_cleanup,
    ).run(
        duration_seconds=duration_seconds,
        session_id=session_id,
        session_directory=session_directory,
        client_log_path=client_log,
        stop_requested=stop_request.exists,
    )
    _write_active(
        config,
        {
            "session_id": session_id,
            "controller_pid": os.getpid(),
            "session_directory": str(session_directory),
            "status": "complete" if outcome.final_exit_code == 0 else "failed",
            "final_exit_code": outcome.final_exit_code,
        },
    )
    stop_request.unlink(missing_ok=True)
    if outcome.failure:
        print(f"AM1_SESSION_FAILURE={outcome.failure}", flush=True)
    print(f"AM1_SESSION_EXIT_CODE={outcome.final_exit_code}", flush=True)
    if outcome.missing_logs:
        print("Log collection is incomplete; rerun with -CollectOnly -SessionId " + session_id)
    if os.name == "nt":
        try:
            os.startfile(session_directory)  # type: ignore[attr-defined]
        except OSError as exc:
            print(f"AM1 session warning: unable to open result folder: {exc}", file=sys.stderr)
    return outcome.final_exit_code


def run_start(repository: Path, config: SessionConfig, duration_seconds: int) -> int:
    validate_local_preflight(repository, config, duration_seconds)
    with LocalSessionLock(config.local_state_directory / "active.lock"):
        return _run_start_locked(repository, config, duration_seconds)


def request_stop(config: SessionConfig) -> int:
    path = _active_path(config)
    try:
        active = json.loads(path.read_text(encoding="utf-8"))
        session_id = active["session_id"]
        pid = int(active["controller_pid"])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise SessionError(f"No valid active local AM1 session was found: {exc}") from exc
    if active.get("status") != "active":
        return int(active.get("final_exit_code", active.get("operational_exit_code", 2)))
    if active.get("status") == "active" and _pid_running(pid):
        stop_request = Path(active["stop_request"])
        stop_request.write_text(session_id + "\n", encoding="utf-8")
        print(f"Stop requested for owned AM1 session {session_id}; waiting for client-first cleanup.")
        # Covers the client's cooperative 30 s window, exact-tree reap,
        # remote 45 s cleanup, SSH exit verification, and scheduling margin.
        deadline = time.monotonic() + STOP_CONFIRMATION_TIMEOUT_S
        while time.monotonic() < deadline:
            current = json.loads(path.read_text(encoding="utf-8"))
            if current.get("status") != "active":
                return int(current.get("final_exit_code", current.get("operational_exit_code", 2)))
            time.sleep(0.2)
        raise SessionError("The active controller did not confirm bounded cleanup; use the physical stop instruction.")

    completed = subprocess.run(
        [
                "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ConnectionAttempts=1",
                "-o", "ServerAliveInterval=2", "-o", "ServerAliveCountMax=3",
            config.ssh_target, config.remote_python, config.remote_helper, "stop",
            "--state-directory", config.remote_state_directory, "--session-id", session_id,
        ],
        timeout=45, check=False,
    )
    return int(completed.returncode)


def collect_only(config: SessionConfig, session_id: str) -> int:
    if not SESSION_ID_PATTERN.fullmatch(session_id):
        raise SessionError("Collection session identity is invalid.")
    directory = config.windows_log_directory / f"am1-session-{session_id}"
    manifest_path = directory / "missing-logs.json"
    summary_path = directory / "session-summary.json"
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SessionError(f"No valid session summary exists for {session_id}: {exc}") from exc
    if not isinstance(summary, dict) or summary.get("session_id") != session_id:
        raise SessionError("Session summary identity does not match the collection request.")

    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SessionError(f"No valid missing-log manifest exists for {session_id}: {exc}") from exc
    else:
        manifest = {"session_id": session_id, "missing": []}
    if not isinstance(manifest, dict) or manifest.get("session_id") != session_id:
        raise SessionError("Missing-log manifest identity does not match the collection request.")

    requested: dict[str, Path] = {}
    missing = manifest.get("missing", [])
    if not isinstance(missing, list):
        raise SessionError("Missing-log manifest entries are not a list.")
    for entry in missing:
        if not isinstance(entry, dict) or not isinstance(entry.get("remote_path"), str):
            raise SessionError("Missing-log manifest entry is invalid.")
        remote_path = entry["remote_path"]
        candidate = PurePosixPath(remote_path)
        expected_remote_directory = PurePosixPath(config.remote_log_directory)
        if (
            candidate.parent != expected_remote_directory
            or not REMOTE_LOG_NAME_PATTERN.fullmatch(candidate.name)
        ):
            raise SessionError("Missing-log manifest remote path is outside the exact-log directory.")
        if not isinstance(entry.get("destination"), str):
            raise SessionError("Missing-log manifest destination is invalid.")
        destination = Path(entry["destination"])
        expected_destination = directory / candidate.name
        try:
            if destination.resolve() != expected_destination.resolve():
                raise SessionError("Missing-log manifest destination does not use the exact log filename.")
        except OSError as exc:
            raise SessionError(f"Unable to validate missing-log destination: {exc}") from exc
        requested[remote_path] = destination

    persisted = SSHRemote(config, session_id, directory)._persisted_terminal_state()
    if persisted.get("persisted_state_available"):
        for key in ("host_log", "camera_log"):
            remote_path = persisted.get(key)
            if isinstance(remote_path, str) and remote_path:
                requested.setdefault(remote_path, directory / PurePosixPath(remote_path).name)
    summary_logs = summary.get("remote_logs", [])
    if not isinstance(summary_logs, list):
        raise SessionError("Session summary remote logs are not a list.")
    for remote_path in summary_logs:
        if isinstance(remote_path, str) and remote_path:
            requested.setdefault(remote_path, directory / PurePosixPath(remote_path).name)

    remaining = []
    recovered_logs: list[str] = []
    for remote_path, destination in requested.items():
        try:
            if destination.resolve().parent != directory.resolve():
                raise SessionError("Recovered log destination is outside the exact session directory.")
        except OSError as exc:
            raise SessionError(f"Unable to validate recovered log destination: {exc}") from exc
        ok, error = _collect_with_scp(config, remote_path, destination)
        if not ok:
            remaining.append(
                {
                    "remote_path": remote_path,
                    "destination": str(destination),
                    "error": error or "unknown",
                }
            )
        else:
            recovered_logs.append(remote_path)

    recovery = {
        "session_id": session_id,
        "recovered_at": datetime.now().astimezone().isoformat(),
        "original_final_exit_code": summary.get("final_exit_code"),
        "original_failure": summary.get("failure"),
        "persisted_state": persisted,
        "recovered_logs": recovered_logs,
        "missing_logs": [entry["remote_path"] for entry in remaining],
        "original_summary_unchanged": True,
    }
    recovery_path = directory / "evidence-recovery.json"
    temporary_recovery = recovery_path.with_name(f".{recovery_path.name}.{os.getpid()}.tmp")
    temporary_recovery.write_text(json.dumps(recovery, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary_recovery, recovery_path)

    if remaining:
        manifest_path.write_text(
            json.dumps({"session_id": session_id, "missing": remaining}, indent=2) + "\n", encoding="utf-8"
        )
        return 4
    if not persisted.get("persisted_state_available") or not persisted.get("persisted_state_terminal"):
        reason = (
            persisted.get("cleanup_error")
            or persisted.get("persisted_state_error")
            or "exact persisted session state is not terminal"
        )
        print(f"AM1 same-session evidence recovery incomplete: {reason}", file=sys.stderr)
        return 4
    manifest_path.unlink(missing_ok=True)
    print(f"AM1 same-session evidence recovery complete: {directory}")
    print("The original operational result and session summary were not changed.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)
    start_parser = subparsers.add_parser("start")
    start_parser.add_argument("--duration-seconds", required=True)
    subparsers.add_parser("stop")
    collect_parser = subparsers.add_parser("collect")
    collect_parser.add_argument("--session-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = SessionConfig.load(args.config)
        repository = Path(__file__).resolve().parents[1]
        if args.command == "start":
            return run_start(repository, config, parse_duration_seconds(args.duration_seconds))
        if args.command == "stop":
            return request_stop(config)
        return collect_only(config, args.session_id)
    except (SessionError, ValueError) as exc:
        print(f"AM1 session refused: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("AM1 session stopped by operator interrupt.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
