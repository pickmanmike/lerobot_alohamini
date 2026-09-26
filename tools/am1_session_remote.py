#!/usr/bin/env python3

"""Session-scoped Pi supervisor for the existing AM1 camera and Local launchers.

The helper owns only children it starts, has no listener, and treats loss of its
controlling SSH stdin as a cleanup request. Runtime children continue to log
directly to their existing files; stdout carries only small transition records.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TextIO


SESSION_ID_PATTERN = re.compile(r"^[0-9]{8}T[0-9]{6}-[0-9a-f]{8}$")
TERMINAL_STATES = {"complete", "fault", "cleanup_unknown", "refused"}
REQUIRED_CAMERA_ROLES = {"forward", "backward", "chest", "wrist_left", "wrist_right"}


class SessionRefusal(RuntimeError):
    pass


class SessionControlStop(SessionRefusal):
    """A lifecycle stop/loss observed while a readiness call is blocking."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _read_text(path: Path, limit: int = 2_000_000) -> str:
    if limit <= 0:
        return ""

    def complete_lines(data: bytes) -> bytes:
        if data.endswith(b"\n"):
            return data
        last_newline = data.rfind(b"\n")
        return data[: last_newline + 1] if last_newline >= 0 else b""

    try:
        with path.open("rb") as stream:
            size = os.fstat(stream.fileno()).st_size
            if size <= limit:
                data = complete_lines(stream.read(limit))
            else:
                head_limit = min(64_000, max(1, limit // 4))
                tail_limit = limit - head_limit
                head = complete_lines(stream.read(head_limit))

                tail_start = size - tail_limit
                probe_start = max(0, tail_start - 1)
                stream.seek(probe_start)
                tail = stream.read(tail_limit + (tail_start > 0))
                if tail_start > 0:
                    if tail.startswith(b"\n"):
                        tail = tail[1:]
                    else:
                        first_newline = tail.find(b"\n")
                        tail = tail[first_newline + 1 :] if first_newline >= 0 else b""
                tail = complete_lines(tail)
                data = head + tail
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace")


def parse_camera_readiness(text: str) -> dict[str, Any] | None:
    url = None
    roles: list[str] | None = None
    latest_status: dict[str, Any] | None = None
    for line in text.splitlines():
        if line.startswith("CAMERA_VIEW_URL="):
            url = line.partition("=")[2].strip()
        elif line.startswith("CAMERA_CONFIGURED_ROLES="):
            roles = [role for role in line.partition("=")[2].strip().split(",") if role]
        elif line.startswith("CAMERA_STATUS "):
            try:
                latest_status = json.loads(line.removeprefix("CAMERA_STATUS "))
            except json.JSONDecodeError:
                continue
    if (
        not url
        or roles is None
        or len(roles) != len(REQUIRED_CAMERA_ROLES)
        or set(roles) != REQUIRED_CAMERA_ROLES
        or latest_status is None
    ):
        return None
    cameras = latest_status.get("cameras")
    if not isinstance(cameras, dict) or set(cameras) != set(roles):
        return None
    if any(not isinstance(cameras[role], dict) or cameras[role].get("state") != "fresh" for role in roles):
        return None
    return {"browser_url": url, "roles": roles}


def camera_readiness_failure(text: str) -> str | None:
    """Summarize sanitized refusal, latest role diagnostic and nonfresh state."""
    lines = text.splitlines()
    refusal = next((line[:256] for line in reversed(lines) if line.startswith("CAMERA_REFUSAL ")), None)
    role_diagnostic = next(
        (line for line in reversed(lines) if re.fullmatch(
            r"CAMERA_ROLE_UNAVAILABLE role=(forward|backward|chest|wrist_left|wrist_right) "
            r"cause=(?:[A-Za-z]{1,48} errno=(?:None|[0-9]+)|ValueError backend HTTP status [0-9]+|[A-Za-z]+)",
            line,
        )),
        None,
    )
    if refusal:
        return refusal + (f"; last {role_diagnostic}" if role_diagnostic else "")
    latest_status: dict[str, Any] | None = None
    for line in lines:
        if line.startswith("CAMERA_STATUS "):
            try:
                candidate = json.loads(line.removeprefix("CAMERA_STATUS "))
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                latest_status = candidate
    cameras = latest_status.get("cameras") if latest_status is not None else None
    if not isinstance(cameras, dict):
        return role_diagnostic
    not_fresh = []
    for role in sorted(REQUIRED_CAMERA_ROLES):
        state = cameras.get(role, {}).get("state") if isinstance(cameras.get(role), dict) else None
        if state != "fresh":
            safe_state = state if isinstance(state, str) and state in {"stale", "unavailable", "missing"} else "unknown"
            not_fresh.append(f"{role}={safe_state}")
    state_summary = "camera roles not fresh: " + ", ".join(not_fresh) if not_fresh else None
    return state_summary + (f"; last {role_diagnostic}" if role_diagnostic else "") if state_summary else role_diagnostic


def host_is_operational(text: str) -> bool:
    for line in text.splitlines():
        if line.startswith("[LIFT OPERATIONAL] "):
            line = line.removeprefix("[LIFT OPERATIONAL] ")
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("phase") == "operational_ready":
            return True
    return False


class BestEffortReporter:
    """Never let a slow/closed control pipe delay process monitoring or cleanup."""

    def __init__(self, writer: Callable[[dict[str, Any]], None]) -> None:
        self._writer = writer

    def emit(self, payload: dict[str, Any]) -> None:
        try:
            self._writer(payload)
        except (BlockingIOError, BrokenPipeError, OSError):
            pass


def _nonblocking_stdout_writer(payload: dict[str, Any]) -> None:
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    try:
        os.set_blocking(sys.stdout.fileno(), False)
    except (AttributeError, OSError):
        pass
    os.write(sys.stdout.fileno(), encoded)


@dataclass
class OwnedChild:
    name: str
    process: Any
    process_group: int
    control_path: Path
    log_path: str | None = None
    control_stream: TextIO | None = field(default=None, repr=False)


def _wait_child(child: OwnedChild, timeout_s: float) -> int | None:
    try:
        return int(child.process.wait(timeout=timeout_s))
    except (subprocess.TimeoutExpired, TimeoutError):
        return None


def stop_owned_children(
    children: list[OwnedChild],
    *,
    killpg: Callable[[int, int], None] | None = None,
    timeout_s: float = 15.0,
) -> dict[str, Any]:
    if killpg is None:
        killpg = os.killpg
    result: dict[str, Any] = {"cleanup_verified": True, "cleanup_errors": []}
    for child in children:
        exit_code = child.process.poll()
        if exit_code is None:
            try:
                killpg(child.process_group, signal.SIGINT)
            except ProcessLookupError:
                pass
            except OSError as exc:
                result["cleanup_errors"].append(f"{child.name}:SIGINT:{type(exc).__name__}")
            exit_code = _wait_child(child, timeout_s)
        if exit_code is None:
            try:
                killpg(child.process_group, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except OSError as exc:
                result["cleanup_errors"].append(f"{child.name}:SIGTERM:{type(exc).__name__}")
            exit_code = _wait_child(child, min(timeout_s, 5.0))
        if exit_code is None:
            result["cleanup_verified"] = False
            result["cleanup_errors"].append(f"{child.name}:still-running-after-bounded-signals")
        elif exit_code != 0:
            result["cleanup_errors"].append(f"{child.name}:exit:{exit_code}")
        result[f"{child.name}_exit"] = exit_code
        if child.control_stream is not None:
            try:
                child.control_stream.close()
            except OSError as exc:
                result["cleanup_errors"].append(f"{child.name}:control-close:{type(exc).__name__}")
    if result["cleanup_errors"]:
        result["cleanup_verified"] = False
    return result


def cleanup_after_controller_loss(cleanup: Callable[[], None], reporter: BestEffortReporter) -> None:
    cleanup()
    reporter.emit({"event": "controller_lost_cleanup_complete"})


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def _git_output(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=15,
        check=False,
    )
    if completed.returncode != 0:
        raise SessionRefusal(f"Unable to inspect repository {repository}: {completed.stderr.strip()}")
    return completed.stdout.strip()


def _validate_repository(path: Path, expected_head: str, label: str) -> None:
    if not path.is_dir():
        raise SessionRefusal(f"{label} repository is missing: {path}")
    actual = _git_output(path, "rev-parse", "HEAD")
    if actual != expected_head:
        raise SessionRefusal(f"{label} source mismatch: expected {expected_head}, found {actual}")
    if _git_output(path, "status", "--porcelain"):
        raise SessionRefusal(f"{label} repository is not clean: {path}")


def _conflicting_owners() -> list[str]:
    conflicts: list[str] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue
        if any(
            owner in cmdline
            for owner in (
                "lerobot.robots.alohamini.alohamini_host",
                "tools/am1_camera_viewer.py",
                "tools/run_am1_host.sh",
                "tools/run_am1_camera.sh",
            )
        ):
            conflicts.append(f"pid={entry.name}")
    return conflicts


class RemoteSupervisor:
    def __init__(self, args: argparse.Namespace, reporter: BestEffortReporter) -> None:
        self.args = args
        self.reporter = reporter
        self.session_root = Path(args.state_directory) / args.session_id
        self.state_path = self.session_root / "state.json"
        self.active_path = Path(args.state_directory) / "active.json"
        self.children: dict[str, OwnedChild] = {}
        self.lock_stream: TextIO | None = None
        self.controller_closed = threading.Event()
        self.controller_stop_requested = threading.Event()
        self.stop_requested = threading.Event()
        self.last_controller_contact = time.monotonic()
        self.state: dict[str, Any] = {
            "session_id": args.session_id,
            "supervisor_pid": os.getpid(),
            "status": "initializing",
            "camera_log": None,
            "host_log": None,
            "camera_source_head": args.camera_head,
            "motor_source_head": args.motor_head,
            "session_source_head": args.session_head,
            "cleanup": None,
        }

    def note_controller_contact(self) -> None:
        self.last_controller_contact = time.monotonic()

    def controller_lease_expired(self) -> bool:
        return bool(self.children) and (
            time.monotonic() - self.last_controller_contact > self.args.controller_lease_timeout
        )

    def save(self) -> None:
        _atomic_json(self.state_path, self.state)
        _atomic_json(
            self.active_path,
            {"session_id": self.args.session_id, "supervisor_pid": os.getpid(), "state_path": str(self.state_path)},
        )

    def emit(self, event: str, **fields: Any) -> None:
        self.reporter.emit({"event": event, "session_id": self.args.session_id, **fields})

    def preflight(self) -> None:
        if not SESSION_ID_PATTERN.fullmatch(self.args.session_id):
            raise SessionRefusal("Invalid session identity.")
        state_directory = Path(self.args.state_directory)
        state_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(state_directory, 0o700)
        import fcntl

        self.lock_stream = (state_directory / "active.lock").open("a+", encoding="utf-8")
        try:
            fcntl.flock(self.lock_stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.lock_stream.close()
            self.lock_stream = None
            raise SessionRefusal("Another AM1 unified session supervisor is active; refusing takeover.") from exc
        self.session_root.mkdir(parents=True, exist_ok=False, mode=0o700)
        os.chmod(self.session_root, 0o700)
        _validate_repository(Path(self.args.session_repository), self.args.session_head, "session helper")
        _validate_repository(Path(self.args.camera_repository), self.args.camera_head, "camera")
        _validate_repository(Path(self.args.motor_repository), self.args.motor_head, "motor")
        for required in (
            Path(self.args.camera_repository) / "tools" / "run_am1_camera.sh",
            Path(self.args.motor_repository) / "tools" / "run_am1_host.sh",
            Path(self.args.camera_python),
        ):
            if not required.exists():
                raise SessionRefusal(f"Required session path is missing: {required}")
        conflicts = _conflicting_owners()
        if conflicts:
            raise SessionRefusal(
                "A camera or motor owner already exists; refusing takeover (" + ", ".join(conflicts) + ")."
            )
        self.state["status"] = "preflight_ready"
        self.save()
        self.emit(
            "preflight_ready",
            session_source_head=self.args.session_head,
            camera_source_head=self.args.camera_head,
            motor_source_head=self.args.motor_head,
        )

    def _spawn(self, name: str, command: list[str], env: dict[str, str]) -> OwnedChild:
        control_path = self.session_root / f"{name}-control.log"
        control_stream = control_path.open("w", encoding="utf-8", buffering=1)
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=control_stream,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            start_new_session=True,
        )
        child = OwnedChild(name, process, process.pid, control_path, control_stream=control_stream)
        self.children[name] = child
        return child

    def _wait_for(self, child: OwnedChild, predicate: Callable[[], Any], timeout_s: float, label: str) -> Any:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.controller_stop_requested.is_set():
                raise SessionControlStop("controller_stop", f"{label} stopped by the controller before readiness")
            if self.stop_requested.is_set():
                raise SessionControlStop("signal", f"{label} stopped by a supervisor signal before readiness")
            if self.controller_closed.is_set():
                raise SessionControlStop("controller_eof", f"{label} lost its controller before readiness")
            if self.controller_lease_expired():
                raise SessionControlStop(
                    "controller_lease_expired",
                    f"{label} cancelled after controller heartbeat lease expired",
                )
            exit_code = child.process.poll()
            if exit_code is not None:
                raise SessionRefusal(f"{label} exited before readiness with status {exit_code}")
            if result := predicate():
                return result
            time.sleep(0.05)
        raise SessionRefusal(f"Timed out waiting for {label} readiness")

    @staticmethod
    def _marker(control_path: Path, key: str) -> str | None:
        prefix = key + "="
        for line in _read_text(control_path, 64_000).splitlines():
            if line.startswith(prefix):
                return line.removeprefix(prefix).strip()
        return None

    def start_camera(self) -> None:
        if self.children:
            raise SessionRefusal("Camera start is out of order.")
        env = dict(os.environ)
        env["AM1_CAMERA_PYTHON"] = self.args.camera_python
        env["AM1_CAMERA_LOG_DIRECTORY"] = self.args.log_directory
        child = self._spawn(
            "camera",
            ["bash", str(Path(self.args.camera_repository) / "tools" / "run_am1_camera.sh")],
            env,
        )
        log_path = self._wait_for(
            child,
            lambda: self._marker(child.control_path, "CAMERA_LOG"),
            10.0,
            "camera launcher",
        )
        child.log_path = log_path
        self.state.update(status="camera_starting", camera_log=log_path)
        self.save()
        try:
            readiness = self._wait_for(
                child,
                lambda: parse_camera_readiness(_read_text(Path(log_path))),
                self.args.camera_ready_timeout,
                "camera viewer",
            )
        except SessionRefusal as exc:
            detail = camera_readiness_failure(_read_text(Path(log_path)))
            suffix = f"; {detail}" if detail else ""
            raise SessionRefusal(f"{exc}{suffix}; CAMERA_LOG={log_path}") from exc
        self.state.update(status="camera_ready", camera_log=log_path, browser_url=readiness["browser_url"])
        self.save()
        self.emit("camera_ready", camera_log=log_path, **readiness)

    def start_host(self) -> None:
        if set(self.children) != {"camera"}:
            raise SessionRefusal("Motor-host start is out of order.")
        env = dict(os.environ)
        env["AM1_LOG_DIRECTORY"] = self.args.log_directory
        child = self._spawn(
            "host",
            ["bash", str(Path(self.args.motor_repository) / "tools" / "run_am1_host.sh"), "--mode", "local"],
            env,
        )
        log_path = self._wait_for(
            child,
            lambda: self._marker(child.control_path, "HOST_LOG"),
            10.0,
            "motor-host launcher",
        )
        child.log_path = log_path
        self.state.update(status="host_starting", host_log=log_path)
        self.save()
        self._wait_for(
            child,
            lambda: host_is_operational(_read_text(Path(log_path))),
            self.args.host_ready_timeout,
            "motor host operational_ready",
        )
        self.state.update(status="host_ready", host_log=log_path)
        self.save()
        self.emit("host_ready", host_log=log_path)

    def check_children(self) -> tuple[str, int] | None:
        for name, child in self.children.items():
            if (exit_code := child.process.poll()) is not None:
                return name, int(exit_code)
        return None

    def cleanup(self, *, reason: str) -> dict[str, Any]:
        self.state["status"] = "stopping"
        self.state["stop_reason"] = reason
        state_errors: list[str] = []
        try:
            self.save()
        except BaseException as exc:
            state_errors.append(f"state:pre-cleanup-save:{type(exc).__name__}:{exc}")
        ordered = [self.children[name] for name in ("host", "camera") if name in self.children]
        cleanup = stop_owned_children(ordered, timeout_s=self.args.cleanup_timeout)
        if state_errors:
            cleanup["cleanup_errors"].extend(state_errors)
            cleanup["cleanup_verified"] = False
        if not cleanup["cleanup_verified"]:
            terminal_status = "cleanup_unknown"
        elif reason in {"controller_stop", "signal"}:
            terminal_status = "complete"
        elif reason == "refusal":
            terminal_status = "refused"
        else:
            terminal_status = "fault"
        self.state["cleanup"] = cleanup
        self.state["status"] = terminal_status
        try:
            self.save()
        except BaseException as exc:
            cleanup["cleanup_errors"].append(f"state:terminal-save:{type(exc).__name__}:{exc}")
            cleanup["cleanup_verified"] = False
            terminal_status = "cleanup_unknown"
            self.state["cleanup"] = cleanup
            self.state["status"] = terminal_status
        self.emit(
            "cleanup_complete",
            reason=reason,
            terminal_status=terminal_status,
            host_log=self.state.get("host_log"),
            camera_log=self.state.get("camera_log"),
            **cleanup,
        )
        return cleanup


def _read_commands(supervisor: RemoteSupervisor, commands: queue.Queue[str]) -> None:
    try:
        for line in sys.stdin:
            supervisor.note_controller_contact()
            command = line.strip()
            if command == "STOP":
                supervisor.controller_stop_requested.set()
            commands.put(command)
    finally:
        supervisor.controller_closed.set()
        commands.put("__EOF__")


def supervise(args: argparse.Namespace) -> int:
    reporter = BestEffortReporter(_nonblocking_stdout_writer)
    supervisor = RemoteSupervisor(args, reporter)
    commands: queue.Queue[str] = queue.Queue()

    def request_stop(signum: int, frame: Any) -> None:
        del signum, frame
        supervisor.stop_requested.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    cleanup_done = False
    try:
        supervisor.preflight()
        reader = threading.Thread(target=_read_commands, args=(supervisor, commands), daemon=True)
        reader.start()
        while True:
            if supervisor.controller_stop_requested.is_set():
                supervisor.cleanup(reason="controller_stop")
                cleanup_done = True
                return 0
            if supervisor.stop_requested.is_set():
                supervisor.cleanup(reason="signal")
                cleanup_done = True
                return 0
            if supervisor.controller_lease_expired():
                supervisor.emit("runtime_fault", reason="controller heartbeat lease expired")
                supervisor.cleanup(reason="controller_lease_expired")
                cleanup_done = True
                return 2
            if supervisor.controller_closed.is_set() and commands.empty():
                cleanup_after_controller_loss(lambda: supervisor.cleanup(reason="controller_eof"), reporter)
                cleanup_done = True
                return 2
            if failed := supervisor.check_children():
                name, exit_code = failed
                supervisor.emit("runtime_fault", child=name, exit_code=exit_code)
                supervisor.cleanup(reason=f"{name}_exit_{exit_code}")
                cleanup_done = True
                return exit_code or 2
            try:
                command = commands.get(timeout=0.1)
            except queue.Empty:
                continue
            if command == "START_CAMERA":
                supervisor.start_camera()
            elif command == "START_HOST":
                supervisor.start_host()
            elif command == "HEARTBEAT":
                continue
            elif command == "STOP":
                supervisor.cleanup(reason="controller_stop")
                cleanup_done = True
                return 0
            elif command == "__EOF__":
                cleanup_after_controller_loss(lambda: supervisor.cleanup(reason="controller_eof"), reporter)
                cleanup_done = True
                return 2
            else:
                raise SessionRefusal(f"Unknown or empty session command: {command!r}")
    except SessionControlStop as exc:
        clean_stop = exc.reason in {"controller_stop", "signal"}
        if not clean_stop:
            supervisor.emit("runtime_fault", reason=str(exc), stop_reason=exc.reason)
        if supervisor.children and not cleanup_done:
            supervisor.cleanup(reason=exc.reason)
            cleanup_done = True
        else:
            supervisor.state["status"] = "complete" if clean_stop else "fault"
            supervisor.state["stop_reason"] = exc.reason
            if supervisor.session_root.exists():
                supervisor.save()
        return 0 if clean_stop else 2
    except SessionRefusal as exc:
        supervisor.state["failure"] = str(exc)
        supervisor.emit("refused", reason=str(exc))
        if supervisor.children and not cleanup_done:
            supervisor.cleanup(reason="refusal")
            cleanup_done = True
        else:
            supervisor.state["status"] = "refused"
            if supervisor.session_root.exists():
                supervisor.save()
        return 2
    except BaseException as exc:
        supervisor.state["failure"] = f"{type(exc).__name__}: {exc}"
        supervisor.emit("fault", reason=supervisor.state["failure"])
        if supervisor.children and not cleanup_done:
            supervisor.cleanup(reason="supervisor_fault")
        raise
    finally:
        if supervisor.lock_stream is not None:
            supervisor.lock_stream.close()


def stop_active(args: argparse.Namespace) -> int:
    active_path = Path(args.state_directory) / "active.json"
    try:
        active = json.loads(active_path.read_text(encoding="utf-8"))
        session_id = active["session_id"]
        pid = int(active["supervisor_pid"])
        state_path = Path(active["state_path"])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise SessionRefusal(f"No valid active AM1 session was found: {exc}") from exc
    if args.session_id and args.session_id != session_id:
        raise SessionRefusal(f"Active session is {session_id}, not {args.session_id}.")
    try:
        cmdline = (Path("/proc") / str(pid) / "cmdline").read_bytes().replace(b"\0", b" ").decode()
    except OSError as exc:
        raise SessionRefusal(f"The recorded supervisor is not running: {exc}") from exc
    if "am1_session_remote.py" not in cmdline or "supervise" not in cmdline or session_id not in cmdline:
        raise SessionRefusal("Recorded PID does not identify the owned AM1 session supervisor.")
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + args.cleanup_timeout + 5
    while time.monotonic() < deadline:
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            time.sleep(0.1)
            continue
        if state.get("status") in TERMINAL_STATES:
            print(json.dumps(state, sort_keys=True))
            return 0 if state.get("status") == "complete" else 2
        time.sleep(0.1)
    raise SessionRefusal("The owned supervisor did not produce a verified terminal state.")


def print_state(args: argparse.Namespace) -> int:
    if not SESSION_ID_PATTERN.fullmatch(args.session_id):
        raise SessionRefusal("Invalid session identity.")
    state_path = Path(args.state_directory) / args.session_id / "state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SessionRefusal(f"No valid persisted state exists for {args.session_id}: {exc}") from exc
    if state.get("session_id") != args.session_id:
        raise SessionRefusal("Persisted session identity does not match the requested session.")
    print(json.dumps(state, sort_keys=True))
    return 0 if state.get("status") == "complete" else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    supervise_parser = subparsers.add_parser("supervise")
    supervise_parser.add_argument("--session-id", required=True)
    supervise_parser.add_argument("--session-repository", required=True)
    supervise_parser.add_argument("--session-head", required=True)
    supervise_parser.add_argument("--camera-repository", required=True)
    supervise_parser.add_argument("--camera-head", required=True)
    supervise_parser.add_argument("--camera-python", required=True)
    supervise_parser.add_argument("--motor-repository", required=True)
    supervise_parser.add_argument("--motor-head", required=True)
    supervise_parser.add_argument("--log-directory", required=True)
    supervise_parser.add_argument("--state-directory", required=True)
    supervise_parser.add_argument("--camera-ready-timeout", type=float, default=30.0)
    supervise_parser.add_argument("--host-ready-timeout", type=float, default=240.0)
    supervise_parser.add_argument("--cleanup-timeout", type=float, default=15.0)
    supervise_parser.add_argument("--controller-lease-timeout", type=float, default=6.0)

    stop_parser = subparsers.add_parser("stop")
    stop_parser.add_argument("--state-directory", required=True)
    stop_parser.add_argument("--session-id")
    stop_parser.add_argument("--cleanup-timeout", type=float, default=15.0)

    state_parser = subparsers.add_parser("state")
    state_parser.add_argument("--state-directory", required=True)
    state_parser.add_argument("--session-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "supervise":
            return supervise(args)
        if args.command == "stop":
            return stop_active(args)
        return print_state(args)
    except SessionRefusal as exc:
        print(f"AM1 session refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
