#!/usr/bin/env python

from __future__ import annotations

import ctypes
import importlib.util
import io
import json
import os
import signal
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def load_tool(name: str):
    path = REPO_ROOT / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"test_{name}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def stop_disposable_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def wait_for_process_output(
    output: bytearray,
    expected: bytes,
    *,
    timeout_s: float = 3.0,
) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if expected in bytes(output):
            return
        time.sleep(0.01)
    pytest.fail(f"timed out waiting for {expected!r}; output={bytes(output)!r}")


class FakeCtypesFunction:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.result


def fake_windows_kernel32(*, open_result=1234, wait_result=0x00000102, close_result=1):
    kernel32 = type("Kernel32", (), {})()
    kernel32.OpenProcess = FakeCtypesFunction(open_result)
    kernel32.WaitForSingleObject = FakeCtypesFunction(wait_result)
    kernel32.CloseHandle = FakeCtypesFunction(close_result)
    return kernel32


@pytest.mark.parametrize("value", ["1", "60", "120", "1800", 1, 1800])
def test_duration_accepts_only_exact_whole_seconds(value):
    module = load_tool("am1_session")

    assert module.parse_duration_seconds(value) == int(value)


@pytest.mark.parametrize(
    "value",
    [None, "", "0", "1801", "1.0", "1.5", "1e3", "nan", "inf", float("nan"), 1.5, True],
)
def test_duration_rejects_invalid_or_coercible_values_before_start(value):
    module = load_tool("am1_session")

    with pytest.raises(ValueError, match="whole number from 1 through 1800"):
        module.parse_duration_seconds(value)


def test_private_config_refuses_remote_shell_paths_and_embedded_browser_credentials(tmp_path):
    module = load_tool("am1_session")
    config = json.loads((REPO_ROOT / "config" / "am1.session.example.json").read_text(encoding="utf-8"))
    config_path = tmp_path / "session.json"

    config["remote_helper"] = "/tmp/helper;touch"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(module.SessionError, match="safe absolute Pi path"):
        module.SessionConfig.load(config_path)

    config["remote_helper"] = "/tmp/helper.py"
    config["browser_url"] = "http://user:secret@192.0.2.1:1984"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(module.SessionError, match="no embedded credentials"):
        module.SessionConfig.load(config_path)


def test_log_collection_refuses_untrusted_remote_path_before_scp(monkeypatch, tmp_path):
    module = load_tool("am1_session")
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("scp must not run")),
    )
    config = type("Config", (), {"remote_log_directory": "/safe/logs", "ssh_target": "am1-pi"})()

    ok, error = module._collect_with_scp(config, "/safe/logs/../private-map.json", tmp_path / "copy.log")

    assert ok is False
    assert "refused remote log path" in error


def test_local_session_lock_refuses_a_second_controller_and_releases_after_exit(tmp_path):
    module = load_tool("am1_session")
    lock_path = tmp_path / "active.lock"

    with module.LocalSessionLock(lock_path):
        with pytest.raises(module.SessionError, match="refusing a second session"):
            with module.LocalSessionLock(lock_path):
                pass

    with module.LocalSessionLock(lock_path):
        pass


def test_coordinator_orders_startup_cleanup_and_exact_log_collection(tmp_path):
    module = load_tool("am1_session")
    events: list[object] = []

    class Remote:
        def preflight(self):
            events.append("remote_preflight")
            return {"session_source_head": "session-head"}

        def start_camera(self):
            events.append("camera_start")
            return {"camera_log": "/logs/exact-camera.log", "browser_url": "http://camera"}

        def start_host(self):
            events.append("host_start")
            return {"host_log": "/logs/exact-host.log"}

        def stop(self):
            events.append("remote_stop")
            return {"host_exit": 0, "camera_exit": 0, "cleanup_verified": True}

    class Client:
        def run(self, *, duration_seconds, log_path, stop_requested):
            events.append(("client", duration_seconds, log_path.name, stop_requested()))
            return 0

    def collect(remote_path, destination):
        events.append(("copy", remote_path, destination.name))
        destination.write_text(remote_path, encoding="utf-8")
        return True, None

    outcome = module.SessionCoordinator(
        remote=Remote(),
        client=Client(),
        open_browser=lambda url: events.append(("browser", url)),
        collect_remote_log=collect,
        input_fn=lambda prompt: events.append(("prompt", prompt)) or "",
    ).run(
        duration_seconds=1800,
        session_id="20260920T120000-1234abcd",
        session_directory=tmp_path,
        client_log_path=tmp_path / "exact-client.log",
        stop_requested=lambda: False,
    )

    assert events[0:3] == ["remote_preflight", "camera_start", ("browser", "http://camera")]
    assert events[3][0] == "prompt"
    assert events[4:7] == [
        "host_start",
        ("client", 1800, "exact-client.log", False),
        "remote_stop",
    ]
    assert events[7:] == [
        ("copy", "/logs/exact-host.log", "exact-host.log"),
        ("copy", "/logs/exact-camera.log", "exact-camera.log"),
    ]
    assert outcome.operational_exit_code == 0
    assert outcome.final_exit_code == 0
    assert outcome.cleanup_verified is True
    assert outcome.remote_logs == ["/logs/exact-host.log", "/logs/exact-camera.log"]


def test_coordinator_reports_hardware_cleanup_before_slow_log_collection(tmp_path):
    module = load_tool("am1_session")
    events = []

    class Remote:
        def preflight(self): return {}
        def start_camera(self): return {"camera_log": "/logs/camera.log", "browser_url": "http://camera"}
        def start_host(self): return {"host_log": "/logs/host.log"}
        def stop(self): events.append("remote_stop"); return {"cleanup_verified": True}

    class Client:
        def run(self, **kwargs): return 0

    outcome = module.SessionCoordinator(
        remote=Remote(), client=Client(), open_browser=lambda url: None,
        collect_remote_log=lambda remote, local: events.append("collect") or (True, None),
        input_fn=lambda prompt: "",
        on_cleanup=lambda outcome: events.append(("hardware_cleanup", outcome.cleanup_verified)),
    ).run(
        duration_seconds=60, session_id="20260920T120000-1234abcd",
        session_directory=tmp_path, client_log_path=tmp_path / "client.log",
        stop_requested=lambda: False,
    )

    assert events == ["remote_stop", ("hardware_cleanup", True), "collect", "collect"]
    assert outcome.final_exit_code == 0


def test_readiness_refusal_never_starts_motor_or_client_and_cleans_camera(tmp_path):
    module = load_tool("am1_session")
    events: list[str] = []

    class Remote:
        def preflight(self):
            events.append("preflight")
            return {}

        def start_camera(self):
            events.append("camera")
            return {"camera_log": "/logs/camera.log", "browser_url": "http://camera"}

        def start_host(self):
            raise AssertionError("motor host must not start before exact readiness approval")

        def stop(self):
            events.append("stop")
            return {"camera_exit": 0, "cleanup_verified": True}

    class Client:
        def run(self, **kwargs):
            raise AssertionError("client must not start after readiness refusal")

    outcome = module.SessionCoordinator(
        remote=Remote(), client=Client(), open_browser=lambda url: None,
        collect_remote_log=lambda remote, local: (True, None), input_fn=lambda prompt: "no",
    ).run(
        duration_seconds=60, session_id="20260920T120000-1234abcd",
        session_directory=tmp_path, client_log_path=tmp_path / "client.log",
        stop_requested=lambda: False,
    )

    assert events == ["preflight", "camera", "stop"]
    assert outcome.operational_exit_code == 2
    assert outcome.final_exit_code == 2


def test_enter_only_readiness_is_announced_before_host_start(tmp_path, capsys):
    module = load_tool("am1_session")
    events = []

    class Remote:
        def preflight(self):
            events.append("preflight")
            return {}

        def start_camera(self):
            events.append("camera")
            return {"camera_log": "/logs/camera.log", "browser_url": "http://camera"}

        def start_host(self):
            events.append("host")
            return {"host_log": "/logs/host.log"}

        def stop(self):
            events.append("stop")
            return {"cleanup_verified": True}

    class Client:
        def run(self, **kwargs):
            events.append("client")
            return 0

        def cleanup_status(self):
            return {"cleanup_verified": True}

    def press_enter(prompt):
        events.append(("input", prompt, capsys.readouterr().out))
        return ""

    outcome = module.SessionCoordinator(
        remote=Remote(), client=Client(), open_browser=lambda url: events.append("browser"),
        collect_remote_log=lambda remote, local: (True, None), input_fn=press_enter,
    ).run(
        duration_seconds=90, session_id="20260921T120000-1234abcd",
        session_directory=tmp_path, client_log_path=tmp_path / "client.log",
        stop_requested=lambda: False,
    )

    input_event = next(event for event in events if isinstance(event, tuple))
    assert input_event[1] == ""
    assert "CONFIRMATION 1/3" in input_event[2]
    assert events.index(input_event) < events.index("host")
    assert outcome.final_exit_code == 0


def test_enter_only_readiness_rejects_text_without_starting_host(tmp_path):
    module = load_tool("am1_session")
    events = []

    class Remote:
        def preflight(self): return {}
        def start_camera(self): return {"camera_log": "/logs/camera.log", "browser_url": "http://camera"}
        def start_host(self): raise AssertionError("host must not start after a non-empty response")
        def stop(self): events.append("stop"); return {"cleanup_verified": True}

    class Client:
        def run(self, **kwargs): raise AssertionError("client must not start")
        def cleanup_status(self): return {"cleanup_verified": True, "not_started": True}

    outcome = module.SessionCoordinator(
        remote=Remote(), client=Client(), open_browser=lambda url: None,
        collect_remote_log=lambda remote, local: (True, None), input_fn=lambda prompt: "READY",
    ).run(
        duration_seconds=90, session_id="20260921T120000-1234abcd",
        session_directory=tmp_path, client_log_path=tmp_path / "client.log",
        stop_requested=lambda: False,
    )

    assert outcome.final_exit_code == 2
    assert "Enter only" in outcome.failure
    assert events == ["stop"]


def test_enter_only_readiness_rejects_console_eof_without_starting_host(tmp_path):
    module = load_tool("am1_session")
    events = []

    class Remote:
        def preflight(self): return {}
        def start_camera(self): return {"camera_log": "/logs/camera.log", "browser_url": "http://camera"}
        def start_host(self): raise AssertionError("host must not start after console EOF")
        def stop(self): events.append("stop"); return {"cleanup_verified": True}

    class Client:
        def run(self, **kwargs): raise AssertionError("client must not start")
        def cleanup_status(self): return {"cleanup_verified": True, "not_started": True}

    def closed_console(prompt):
        raise EOFError("console input closed")

    outcome = module.SessionCoordinator(
        remote=Remote(), client=Client(), open_browser=lambda url: None,
        collect_remote_log=lambda remote, local: (True, None), input_fn=closed_console,
    ).run(
        duration_seconds=90, session_id="20260921T120000-1234abcd",
        session_directory=tmp_path, client_log_path=tmp_path / "client.log",
        stop_requested=lambda: False,
    )

    assert outcome.final_exit_code == 2
    assert outcome.failure == "EOFError: console input closed"
    assert events == ["stop"]


def test_stop_during_enter_confirmation_cancels_prompt_and_cleans_camera_promptly(tmp_path):
    module = load_tool("am1_session")
    stop = threading.Event()
    prompt_entered = threading.Event()
    release_prompt = threading.Event()
    stopped: list[str] = []
    result = []

    class Remote:
        def preflight(self, stop_requested=None): return {}
        def start_camera(self, stop_requested=None):
            return {"camera_log": "/logs/camera.log", "browser_url": "http://camera"}
        def start_host(self, stop_requested=None): raise AssertionError("host must not start")
        def stop(self): stopped.append("stop"); return {"cleanup_verified": True}

    class Client:
        def run(self, **kwargs): raise AssertionError("client must not start")

    def blocking_input(prompt):
        prompt_entered.set()
        release_prompt.wait(5)
        return ""

    coordinator = module.SessionCoordinator(
        remote=Remote(), client=Client(), open_browser=lambda url: None,
        collect_remote_log=lambda remote, local: (True, None), input_fn=blocking_input,
    )
    runner = threading.Thread(
        target=lambda: result.append(
            coordinator.run(
                duration_seconds=60,
                session_id="20260920T120000-1234abcd",
                session_directory=tmp_path,
                client_log_path=tmp_path / "client.log",
                stop_requested=stop.is_set,
            )
        )
    )
    runner.start()
    assert prompt_entered.wait(1)
    stop.set()
    runner.join(1)
    if runner.is_alive():
        release_prompt.set()
        runner.join(2)
        pytest.fail("stop request did not interrupt the Enter-confirmation wait")

    assert result[0].operational_exit_code == 130
    assert stopped == ["stop"]


def test_remote_fault_during_first_enter_refuses_before_host_start(tmp_path):
    module = load_tool("am1_session")
    remote_fault = threading.Event()
    prompt_entered = threading.Event()
    release_prompt = threading.Event()
    host_started: list[bool] = []
    stopped: list[str] = []
    result = []

    class Remote:
        def preflight(self): return {}
        def start_camera(self): return {"camera_log": "/logs/camera.log", "browser_url": "http://camera"}
        def start_host(self): host_started.append(True); return {"host_log": "/logs/host.log"}
        def fault(self):
            if remote_fault.is_set():
                return {"event": "fault", "reason": "SSH controller exited with status 255"}
            return None
        def stop(self): stopped.append("stop"); return {"cleanup_verified": False}

    class Client:
        def run(self, **kwargs): raise AssertionError("client must not start after a remote fault")
        def cleanup_status(self): return {"cleanup_verified": True, "not_started": True}

    def blocking_input(prompt):
        prompt_entered.set()
        release_prompt.wait(5)
        return ""

    coordinator = module.SessionCoordinator(
        remote=Remote(), client=Client(), open_browser=lambda url: None,
        collect_remote_log=lambda remote, local: (True, None), input_fn=blocking_input,
    )
    runner = threading.Thread(
        target=lambda: result.append(
            coordinator.run(
                duration_seconds=90,
                session_id="20260922T120000-1234abcd",
                session_directory=tmp_path,
                client_log_path=tmp_path / "client.log",
                stop_requested=lambda: False,
            )
        )
    )
    runner.start()
    assert prompt_entered.wait(1)
    remote_fault.set()
    runner.join(1)
    if runner.is_alive():
        release_prompt.set()
        runner.join(2)
        pytest.fail("remote failure did not interrupt the first Enter confirmation")

    release_prompt.set()
    assert host_started == []
    assert stopped == ["stop"]
    assert result[0].operational_exit_code == 2
    assert "SSH controller exited with status 255" in result[0].failure


def test_stop_during_remote_readiness_wait_is_forwarded_and_cleaned(tmp_path):
    module = load_tool("am1_session")
    stop = threading.Event()
    camera_wait_entered = threading.Event()
    stopped: list[str] = []
    result = []

    class Remote:
        def preflight(self, stop_requested=None): return {}
        def set_stop_requested(self, callback): self.stop_requested = callback
        def start_camera(self):
            camera_wait_entered.set()
            while not self.stop_requested():
                time.sleep(0.01)
            raise module.SessionStopped("stop requested during camera readiness")
        def stop(self): stopped.append("stop"); return {"cleanup_verified": True}

    class Client:
        def run(self, **kwargs): raise AssertionError("client must not start")

    coordinator = module.SessionCoordinator(
        remote=Remote(), client=Client(), open_browser=lambda url: None,
        collect_remote_log=lambda remote, local: (True, None), input_fn=lambda prompt: "",
    )
    runner = threading.Thread(
        target=lambda: result.append(
            coordinator.run(
                duration_seconds=60,
                session_id="20260920T120000-1234abcd",
                session_directory=tmp_path,
                client_log_path=tmp_path / "client.log",
                stop_requested=stop.is_set,
            )
        )
    )
    runner.start()
    assert camera_wait_entered.wait(1)
    stop.set()
    runner.join(2)

    assert not runner.is_alive()
    assert result[0].operational_exit_code == 130
    assert stopped == ["stop"]


def test_partial_camera_start_failure_still_requests_remote_cleanup(tmp_path):
    module = load_tool("am1_session")
    events: list[str] = []

    class Remote:
        def preflight(self): events.append("preflight"); return {}
        def start_camera(self): events.append("camera_partial"); raise module.SessionError("not ready")
        def stop(self): events.append("stop"); return {"cleanup_verified": True, "camera_log": "/logs/partial.log"}

    class Client:
        def run(self, **kwargs): raise AssertionError("client must not start")

    outcome = module.SessionCoordinator(
        remote=Remote(), client=Client(), open_browser=lambda url: None,
        collect_remote_log=lambda remote, local: (True, None), input_fn=lambda prompt: "",
    ).run(
        duration_seconds=60, session_id="20260920T120000-1234abcd",
        session_directory=tmp_path, client_log_path=tmp_path / "client.log",
        stop_requested=lambda: False,
    )

    assert events == ["preflight", "camera_partial", "stop"]
    assert outcome.remote_logs == ["/logs/partial.log"]
    assert outcome.final_exit_code == 2


def test_remote_preflight_failure_after_process_ownership_still_requests_cleanup(tmp_path):
    module = load_tool("am1_session")
    stopped = []

    class Remote:
        def preflight(self): raise module.SessionError("SSH preflight failed")
        def stop(self): stopped.append(True); return {"cleanup_verified": True}

    class Client:
        def run(self, **kwargs): raise AssertionError("client must not start")

    outcome = module.SessionCoordinator(
        remote=Remote(), client=Client(), open_browser=lambda url: None,
        collect_remote_log=lambda remote, local: (True, None), input_fn=lambda prompt: "",
    ).run(
        duration_seconds=60, session_id="20260920T120000-1234abcd",
        session_directory=tmp_path, client_log_path=tmp_path / "client.log",
        stop_requested=lambda: False,
    )

    assert stopped == [True]
    assert outcome.final_exit_code == 2


@pytest.mark.parametrize("mode", ["stop-before-host", "ctrl-c"])
def test_explicit_stop_and_ctrl_c_both_cleanup_without_relabeling_success(tmp_path, mode):
    module = load_tool("am1_session")
    stopped: list[str] = []

    class Remote:
        def preflight(self): return {}
        def start_camera(self): return {"camera_log": "/logs/camera.log", "browser_url": "http://camera"}
        def start_host(self):
            if mode == "stop-before-host":
                raise AssertionError("host must not start after an explicit early stop")
            return {"host_log": "/logs/host.log"}
        def stop(self): stopped.append("stop"); return {"cleanup_verified": True}

    class Client:
        def run(self, **kwargs): raise KeyboardInterrupt

    outcome = module.SessionCoordinator(
        remote=Remote(), client=Client(), open_browser=lambda url: None,
        collect_remote_log=lambda remote, local: (True, None), input_fn=lambda prompt: "",
    ).run(
        duration_seconds=60, session_id="20260920T120000-1234abcd",
        session_directory=tmp_path, client_log_path=tmp_path / "client.log",
        stop_requested=lambda: mode == "stop-before-host",
    )

    assert stopped == ["stop"]
    assert outcome.operational_exit_code == 130
    assert outcome.final_exit_code == 130


def test_primary_client_failure_survives_cleanup_and_copy_failures(tmp_path):
    module = load_tool("am1_session")

    class Remote:
        def preflight(self): return {}
        def start_camera(self): return {"camera_log": "/logs/camera.log", "browser_url": "http://camera"}
        def start_host(self): return {"host_log": "/logs/host.log"}
        def stop(self): return {"cleanup_verified": False, "cleanup_error": "host state unknown"}

    class Client:
        def run(self, **kwargs): return 7

    outcome = module.SessionCoordinator(
        remote=Remote(), client=Client(), open_browser=lambda url: None,
        collect_remote_log=lambda remote, local: (False, "copy failed"), input_fn=lambda prompt: "",
    ).run(
        duration_seconds=60, session_id="20260920T120000-1234abcd",
        session_directory=tmp_path, client_log_path=tmp_path / "client.log",
        stop_requested=lambda: False,
    )

    assert outcome.operational_exit_code == 7
    assert outcome.final_exit_code == 7
    assert outcome.cleanup_verified is False
    assert outcome.missing_logs == ["/logs/host.log", "/logs/camera.log"]
    manifest = json.loads((tmp_path / "missing-logs.json").read_text(encoding="utf-8"))
    assert [entry["remote_path"] for entry in manifest["missing"]] == outcome.missing_logs


def test_primary_client_failure_survives_collection_callback_exception(tmp_path):
    module = load_tool("am1_session")

    class Remote:
        def preflight(self): return {}
        def start_camera(self): return {"camera_log": "/logs/camera.log", "browser_url": "http://camera"}
        def start_host(self): return {"host_log": "/logs/host.log"}
        def stop(self): return {"cleanup_verified": True, "terminal_status": "complete"}

    class Client:
        def run(self, **kwargs): return 7

    def collect(*args):
        raise TimeoutError("copy timed out")

    outcome = module.SessionCoordinator(
        remote=Remote(), client=Client(), open_browser=lambda url: None,
        collect_remote_log=collect, input_fn=lambda prompt: "",
    ).run(
        duration_seconds=60, session_id="20260920T120000-1234abcd",
        session_directory=tmp_path, client_log_path=tmp_path / "client.log",
        stop_requested=lambda: False,
    )

    assert outcome.operational_exit_code == 7
    assert outcome.final_exit_code == 7
    assert outcome.missing_logs == ["/logs/host.log", "/logs/camera.log"]
    assert (tmp_path / "session-summary.json").is_file()


def test_persisted_runtime_fault_is_not_relabelled_success_after_verified_cleanup(tmp_path):
    module = load_tool("am1_session")

    class Remote:
        def preflight(self): return {}
        def start_camera(self): return {"browser_url": "http://camera"}
        def start_host(self): return {}
        def stop(self):
            return {
                "cleanup_verified": True,
                "persisted_status": "fault",
                "stop_reason": "controller_eof",
            }

    class Client:
        def run(self, **kwargs): return 0

    outcome = module.SessionCoordinator(
        remote=Remote(), client=Client(), open_browser=lambda url: None,
        collect_remote_log=lambda remote, local: (True, None), input_fn=lambda prompt: "",
    ).run(
        duration_seconds=60, session_id="20260920T120000-1234abcd",
        session_directory=tmp_path, client_log_path=tmp_path / "client.log",
        stop_requested=lambda: False,
    )

    assert outcome.operational_exit_code == 2
    assert outcome.final_exit_code == 2
    assert "fault" in outcome.failure


def test_remote_stop_recovers_verified_cleanup_from_persisted_state_after_control_exit(monkeypatch, tmp_path):
    module = load_tool("am1_session")
    config = type(
        "Config",
        (),
        {
            "ssh_target": "am1-pi",
            "remote_python": "/python",
            "remote_helper": "/helper.py",
            "remote_state_directory": "/state",
        },
    )()
    remote = module.SSHRemote(config, "20260920T120000-1234abcd", tmp_path)
    remote.process = type("Process", (), {"returncode": 0, "poll": lambda self: 0})()
    persisted = {
        "session_id": remote.session_id,
        "status": "complete",
        "host_log": "/logs/host.log",
        "camera_log": "/logs/camera.log",
        "stop_reason": "controller_eof",
        "cleanup": {"cleanup_verified": True, "host_exit": 0, "camera_exit": 0},
    }

    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: type(
            "Completed", (), {"returncode": 0, "stdout": json.dumps(persisted), "stderr": ""}
        )(),
    )

    assert remote.stop() == {
        "cleanup_verified": True,
        "host_exit": 0,
        "camera_exit": 0,
        "host_log": "/logs/host.log",
        "camera_log": "/logs/camera.log",
        "persisted_status": "complete",
        "terminal_status": "complete",
        "stop_reason": "controller_eof",
        "persisted_state_available": True,
        "persisted_state_terminal": True,
    }


def test_persisted_state_retains_nonterminal_status_reason_and_exact_logs(monkeypatch, tmp_path):
    module = load_tool("am1_session")
    config = type(
        "Config",
        (),
        {
            "ssh_target": "am1-pi",
            "remote_python": "/python",
            "remote_helper": "/helper.py",
            "remote_state_directory": "/state",
        },
    )()
    remote = module.SSHRemote(config, "20260922T120000-1234abcd", tmp_path)
    state = {
        "session_id": remote.session_id,
        "status": "host_starting",
        "host_log": "/logs/exact-host.log",
        "camera_log": "/logs/exact-camera.log",
        "cleanup": None,
    }
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: type(
            "Completed", (), {"returncode": 2, "stdout": json.dumps(state), "stderr": ""}
        )(),
    )

    recovered = remote._persisted_terminal_state()

    assert recovered["persisted_state_available"] is True
    assert recovered["persisted_state_terminal"] is False
    assert recovered["persisted_status"] == "host_starting"
    assert recovered["host_log"] == "/logs/exact-host.log"
    assert recovered["camera_log"] == "/logs/exact-camera.log"
    assert recovered["cleanup_verified"] is False
    assert "nonterminal" in recovered["cleanup_error"]


def test_persisted_state_retries_once_then_recovers_exact_terminal_state(monkeypatch, tmp_path):
    module = load_tool("am1_session")
    config = type(
        "Config",
        (),
        {
            "ssh_target": "am1-pi",
            "remote_python": "/python",
            "remote_helper": "/helper.py",
            "remote_state_directory": "/state",
        },
    )()
    remote = module.SSHRemote(config, "20260922T120000-1234abcd", tmp_path)
    state = {
        "session_id": remote.session_id,
        "status": "fault",
        "host_log": "/logs/exact-host.log",
        "camera_log": "/logs/exact-camera.log",
        "stop_reason": "controller_lease_expired",
        "cleanup": {"cleanup_verified": True, "host_exit": 0, "camera_exit": 0},
    }
    calls = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == 1:
            return type("Completed", (), {"returncode": 255, "stdout": "", "stderr": "link down"})()
        return type("Completed", (), {"returncode": 2, "stdout": json.dumps(state), "stderr": ""})()

    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(module.time, "sleep", lambda duration: None)

    recovered = remote._persisted_terminal_state()

    assert len(calls) == 2
    assert recovered["persisted_state_available"] is True
    assert recovered["persisted_state_terminal"] is True
    assert recovered["persisted_status"] == "fault"
    assert recovered["stop_reason"] == "controller_lease_expired"
    assert recovered["persisted_state_errors"] == ["attempt 1: SSH state query exited 255: link down"]


def test_persisted_state_retries_nonterminal_state_until_terminal(monkeypatch, tmp_path):
    module = load_tool("am1_session")
    config = type(
        "Config",
        (),
        {
            "ssh_target": "am1-pi",
            "remote_python": "/python",
            "remote_helper": "/helper.py",
            "remote_state_directory": "/state",
        },
    )()
    remote = module.SSHRemote(config, "20260922T120000-1234abcd", tmp_path)
    nonterminal = {
        "session_id": remote.session_id,
        "status": "stopping",
        "host_log": "/logs/exact-host.log",
        "camera_log": "/logs/exact-camera.log",
        "cleanup": None,
    }
    terminal = {
        **nonterminal,
        "status": "complete",
        "stop_reason": "controller_eof",
        "cleanup": {"cleanup_verified": True, "host_exit": 0, "camera_exit": 0},
    }
    responses = iter([nonterminal, terminal])
    calls = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        return type(
            "Completed",
            (),
            {"returncode": 0, "stdout": json.dumps(next(responses)), "stderr": ""},
        )()

    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(module.time, "sleep", lambda duration: None)

    recovered = remote._persisted_terminal_state()

    assert len(calls) == 2
    assert recovered["persisted_state_terminal"] is True
    assert recovered["persisted_status"] == "complete"
    assert recovered["cleanup_verified"] is True


def test_persisted_state_reports_every_failed_lookup_reason(monkeypatch, tmp_path):
    module = load_tool("am1_session")
    config = type(
        "Config",
        (),
        {
            "ssh_target": "am1-pi",
            "remote_python": "/python",
            "remote_helper": "/helper.py",
            "remote_state_directory": "/state",
        },
    )()
    remote = module.SSHRemote(config, "20260922T120000-1234abcd", tmp_path)
    responses = iter(
        [
            type("Completed", (), {"returncode": 255, "stdout": "", "stderr": "link unavailable"})(),
            type("Completed", (), {"returncode": 2, "stdout": "{", "stderr": "state lookup refused"})(),
            type(
                "Completed",
                (),
                {
                    "returncode": 0,
                    "stdout": json.dumps({"session_id": "20260922T120000-deadbeef", "status": "complete"}),
                    "stderr": "",
                },
            )(),
        ]
    )
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: next(responses))
    monkeypatch.setattr(module.time, "sleep", lambda duration: None)

    recovered = remote._persisted_terminal_state()

    assert recovered["persisted_state_available"] is False
    assert recovered["persisted_state_terminal"] is False
    assert recovered["persisted_state_errors"] == [
        "attempt 1: SSH state query exited 255: link unavailable",
        "attempt 2: persisted state was not valid JSON: Expecting property name enclosed in double quotes: "
        "line 1 column 2 (char 1); stderr: state lookup refused",
        "attempt 3: persisted session identity did not match",
    ]
    assert recovered["persisted_state_error"] == recovered["persisted_state_errors"][-1]


def test_control_ssh_uses_bounded_keepalive_and_noninteractive_authentication(tmp_path):
    module = load_tool("am1_session")
    config = type(
        "Config",
        (),
        {
            "ssh_target": "am1-pi",
            "remote_python": "/python",
            "remote_helper": "/helper.py",
            "remote_session_repository": "/session",
            "remote_session_head": "a" * 40,
            "remote_camera_repository": "/camera",
            "remote_camera_head": "b" * 40,
            "remote_camera_python": "/camera-python",
            "remote_motor_repository": "/motor",
            "remote_motor_head": "c" * 40,
            "remote_log_directory": "/logs",
            "remote_state_directory": "/state",
        },
    )()

    command = module.SSHRemote(config, "20260920T120000-1234abcd", tmp_path)._command()

    assert "BatchMode=yes" in command
    assert "ConnectTimeout=10" in command
    assert "ServerAliveInterval=2" in command
    assert "ServerAliveCountMax=3" in command


def test_remote_fault_preserves_first_reason_and_records_later_heartbeat_error(tmp_path):
    module = load_tool("am1_session")
    remote = module.SSHRemote(type("Config", (), {})(), "20260922T120000-1234abcd", tmp_path)
    first = {"event": "runtime_fault", "reason": "controller heartbeat lease expired"}
    later = {"event": "fault", "reason": "controller heartbeat failed: control link unavailable"}

    remote._record_fault(first)
    remote._record_fault(later)

    assert remote.fault() == first
    assert remote.diagnostics()["later_errors"] == [later]


def test_remote_wait_reports_ssh_exit_status_and_stderr(tmp_path):
    module = load_tool("am1_session")
    remote = module.SSHRemote(type("Config", (), {})(), "20260922T120000-1234abcd", tmp_path)
    stderr_path = tmp_path / "ssh-control.log"
    stderr_path.write_text("Timeout, server 192.0.2.1 not responding.\n", encoding="utf-8")

    class Process:
        returncode = 255
        def poll(self): return 255

    remote.process = Process()
    remote.stderr_path = stderr_path

    with pytest.raises(module.SessionError, match="SSH controller exited with status 255"):
        remote._wait("host_ready", 0.2)

    fault = remote.fault()
    assert fault["ssh_exit_status"] == 255
    assert fault["ssh_stderr"] == "Timeout, server 192.0.2.1 not responding."


def test_remote_stop_records_ssh_exit_after_earlier_heartbeat_fault(monkeypatch, tmp_path):
    module = load_tool("am1_session")
    remote = module.SSHRemote(type("Config", (), {})(), "20260922T120000-1234abcd", tmp_path)
    remote.stderr_path.write_text("Timeout, server 192.0.2.1 not responding.\n", encoding="utf-8")
    heartbeat = {"event": "fault", "reason": "controller heartbeat failed: control link unavailable"}
    remote._record_fault(heartbeat)

    class Process:
        returncode = 255
        def poll(self): return 255

    remote.process = Process()
    monkeypatch.setattr(
        remote,
        "_persisted_terminal_state",
        lambda: {
            "cleanup_verified": True,
            "persisted_status": "fault",
            "terminal_status": "fault",
            "persisted_state_available": True,
            "persisted_state_terminal": True,
        },
    )

    cleanup = remote.stop()

    assert cleanup["primary_fault"] == heartbeat
    assert cleanup["later_errors"] == [
        {
            "event": "fault",
            "reason": "SSH controller exited with status 255 before cleanup verification",
            "ssh_exit_status": 255,
            "ssh_stderr": "Timeout, server 192.0.2.1 not responding.",
        }
    ]


def test_remote_stop_retains_diagnostics_after_cleanup_event_and_nonzero_ssh_exit(monkeypatch, tmp_path):
    module = load_tool("am1_session")
    remote = module.SSHRemote(type("Config", (), {})(), "20260922T120000-1234abcd", tmp_path)
    remote.stderr_path.write_text("connection reset after cleanup\n", encoding="utf-8")
    first = {"event": "runtime_fault", "reason": "first remote fault"}
    remote._record_fault(first)

    class Stream:
        def close(self): pass

    class Process:
        def __init__(self):
            self.stdin = Stream()
            self.returncode = None
        def poll(self): return self.returncode
        def wait(self, timeout): self.returncode = 255; return 255

    remote.process = Process()
    monkeypatch.setattr(remote, "_send", lambda command: None)
    monkeypatch.setattr(
        remote,
        "_wait",
        lambda *args, **kwargs: {
            "event": "cleanup_complete",
            "session_id": remote.session_id,
            "cleanup_verified": True,
        },
    )

    cleanup = remote.stop()

    assert cleanup["cleanup_verified"] is True
    assert cleanup["primary_fault"] == first
    assert cleanup["later_errors"] == [
        {
            "event": "fault",
            "reason": "SSH controller exited with status 255 after cleanup_complete",
            "ssh_exit_status": 255,
            "ssh_stderr": "connection reset after cleanup",
        }
    ]


def test_remote_stop_does_not_verify_cleanup_while_ssh_controller_remains_active(monkeypatch, tmp_path):
    module = load_tool("am1_session")
    remote = module.SSHRemote(type("Config", (), {})(), "20260922T120000-1234abcd", tmp_path)

    class Stream:
        def __init__(self): self.closed = False
        def close(self): self.closed = True

    class Process:
        returncode = None
        def __init__(self): self.stdin = Stream()
        def poll(self): return None
        def wait(self, timeout): raise module.subprocess.TimeoutExpired("ssh", timeout)

    process = Process()
    remote.process = process
    monkeypatch.setattr(remote, "_send", lambda command: None)
    monkeypatch.setattr(
        remote,
        "_wait",
        lambda *args, **kwargs: (_ for _ in ()).throw(module.SessionError("cleanup event timeout")),
    )
    monkeypatch.setattr(
        remote,
        "_persisted_terminal_state",
        lambda: {
            "cleanup_verified": True,
            "persisted_status": "complete",
            "terminal_status": "complete",
            "persisted_state_available": True,
            "persisted_state_terminal": True,
        },
    )

    cleanup = remote.stop()

    assert process.stdin.closed is True
    assert cleanup["persisted_cleanup_verified"] is True
    assert cleanup["cleanup_verified"] is False
    assert "remained active" in cleanup["cleanup_error"]
    assert "cleanup event timeout" in cleanup["control_cleanup_error"]


def test_remote_stop_closes_control_stdin_and_uses_persisted_cleanup_when_stop_send_fails(monkeypatch, tmp_path):
    module = load_tool("am1_session")
    config = type("Config", (), {})()
    remote = module.SSHRemote(config, "20260920T120000-1234abcd", tmp_path)

    class Stream:
        def __init__(self): self.closed = False
        def close(self): self.closed = True

    class Process:
        def __init__(self):
            self.stdin = Stream()
            self.returncode = None
        def poll(self): return self.returncode
        def wait(self, timeout): self.returncode = 2; return 2

    process = Process()
    remote.process = process
    monkeypatch.setattr(remote, "_send", lambda command: (_ for _ in ()).throw(module.SessionError("link down")))
    monkeypatch.setattr(
        remote,
        "_persisted_terminal_state",
        lambda: {
            "cleanup_verified": True,
            "terminal_status": "fault",
            "persisted_status": "fault",
            "persisted_state_available": True,
            "persisted_state_terminal": True,
        },
    )

    result = remote.stop()

    assert process.stdin.closed is True
    assert result["cleanup_verified"] is True
    assert result["terminal_status"] == "fault"
    assert "link down" in result["control_stop_error"]

def test_remote_readiness_requires_all_fresh_cameras_and_operational_host_marker():
    module = load_tool("am1_session_remote")
    camera = "\n".join(
        [
            "CAMERA_VIEW_URL=http://192.0.2.1:1984",
            "CAMERA_CONFIGURED_ROLES=forward,backward,chest,wrist_left,wrist_right",
            "CAMERA_STATUS "
            + json.dumps(
                {
                    "cameras": {
                        role: {"state": "fresh"}
                        for role in ("forward", "backward", "chest", "wrist_left", "wrist_right")
                    }
                },
                separators=(",", ":"),
            ),
        ]
    )

    ready = module.parse_camera_readiness(camera)

    assert ready == {
        "browser_url": "http://192.0.2.1:1984",
        "roles": ["forward", "backward", "chest", "wrist_left", "wrist_right"],
    }
    assert module.parse_camera_readiness(camera.replace('"fresh"', '"stale"', 1)) is None
    assert module.parse_camera_readiness(camera.replace(",wrist_right", "")) is None
    assert module.host_is_operational('{"phase":"operational_ready","height_mm":10.1}') is True
    assert module.host_is_operational('{"phase":"home_complete"}') is False


def test_remote_host_readiness_accepts_actual_lift_operational_log_format():
    module = load_tool("am1_session_remote")
    actual_record = (
        '[LIFT OPERATIONAL] {"phase":"operational_ready","elapsed_s":6.733,'
        '"height_mm":10.376953125,"policy":"am1-confirmed-temperature-relief-v1"}\n'
    )

    assert module.host_is_operational(actual_record) is True
    assert module.host_is_operational(actual_record.replace("operational_ready", "home_complete")) is False


def test_remote_bounded_reader_finds_late_host_readiness_and_waits_for_complete_line(tmp_path):
    module = load_tool("am1_session_remote")
    log_path = tmp_path / "host.log"
    limit = 2_000_000
    marker_offset = 2_070_000
    prefix = b'{"phase":"starting"}\n'
    filler = (b"x" * (marker_offset - len(prefix) - 1)) + b"\n"
    log_path.write_bytes(prefix + filler)
    assert log_path.stat().st_size == marker_offset

    initial = module._read_text(log_path, limit)

    assert len(initial) <= limit
    assert module.host_is_operational(initial) is False

    with log_path.open("a", encoding="utf-8") as stream:
        stream.write('{"phase":"operational_ready","height_mm":10.0}')
    incomplete = module._read_text(log_path, limit)

    assert len(incomplete) <= limit
    assert module.host_is_operational(incomplete) is False

    with log_path.open("a", encoding="utf-8") as stream:
        stream.write("\n")
    complete = module._read_text(log_path, limit)

    assert len(complete) <= limit
    assert module.host_is_operational(complete) is True


def test_remote_bounded_reader_preserves_camera_metadata_and_latest_status(tmp_path):
    module = load_tool("am1_session_remote")
    log_path = tmp_path / "camera.log"
    roles = ["forward", "backward", "chest", "wrist_left", "wrist_right"]
    stale_status = {
        "cameras": {role: {"state": "stale"} for role in roles},
        "sequence": 1,
    }
    fresh_status = {
        "cameras": {role: {"state": "fresh"} for role in roles},
        "sequence": 999,
    }
    lines = [
        "CAMERA_VIEW_URL=http://192.0.2.1:1984",
        "CAMERA_CONFIGURED_ROLES=" + ",".join(roles),
        "CAMERA_STATUS " + json.dumps(stale_status, separators=(",", ":")),
        *("filler-record" for _ in range(200)),
        "CAMERA_STATUS " + json.dumps(fresh_status, separators=(",", ":")),
    ]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    text = module._read_text(log_path, 1_024)

    assert len(text) <= 1_024
    assert module.parse_camera_readiness(text) == {
        "browser_url": "http://192.0.2.1:1984",
        "roles": roles,
    }


@pytest.mark.parametrize("child_name", ["camera", "host"])
def test_remote_records_exact_child_log_before_later_readiness_failure(tmp_path, child_name):
    module = load_tool("am1_session_remote")
    args = type(
        "Args",
        (),
        {
            "session_id": "20260920T120000-1234abcd",
            "state_directory": str(tmp_path / "state"),
            "camera_head": "camera-head",
            "motor_head": "motor-head",
            "session_head": "session-head",
            "camera_python": "/python",
            "camera_repository": "/camera",
            "motor_repository": "/motor",
            "log_directory": "/logs",
            "camera_ready_timeout": 1.0,
            "host_ready_timeout": 1.0,
        },
    )()
    supervisor = module.RemoteSupervisor(args, module.BestEffortReporter(lambda payload: None))
    if child_name == "host":
        supervisor.children["camera"] = object()
    child = module.OwnedChild(child_name, object(), 100, tmp_path / "control")
    supervisor._spawn = lambda *args, **kwargs: child
    calls = 0

    def wait_for(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return f"/logs/exact-{child_name}.log"
        raise module.SessionRefusal("not ready")

    supervisor._wait_for = wait_for
    supervisor.save = lambda: None

    with pytest.raises(module.SessionRefusal, match="not ready"):
        (supervisor.start_camera if child_name == "camera" else supervisor.start_host)()

    assert supervisor.state[f"{child_name}_log"] == f"/logs/exact-{child_name}.log"


def test_remote_cleanup_signals_only_owned_process_groups_and_never_killall():
    module = load_tool("am1_session_remote")
    signals: list[tuple[int, int]] = []

    class Child:
        def __init__(self, pid):
            self.pid = pid
            self.returncode = None

        def poll(self): return self.returncode
        def wait(self, timeout):
            self.returncode = 0
            return 0

    def killpg(pgid, signum):
        signals.append((pgid, signum))

    host = module.OwnedChild("host", Child(101), 101, Path("host-control"), "/logs/host.log")
    camera = module.OwnedChild("camera", Child(202), 202, Path("camera-control"), "/logs/camera.log")

    result = module.stop_owned_children([host, camera], killpg=killpg, timeout_s=1)

    assert [pgid for pgid, _ in signals] == [101, 202]
    assert result["cleanup_verified"] is True
    assert result["host_exit"] == 0
    assert result["camera_exit"] == 0


def test_remote_cleanup_nonzero_child_exit_is_not_verified():
    module = load_tool("am1_session_remote")

    class Child:
        def poll(self): return 120

    child = module.OwnedChild("host", Child(), 101, Path("host-control"), "/logs/host.log")
    result = module.stop_owned_children([child], killpg=lambda *args: None, timeout_s=1)

    assert result["host_exit"] == 120
    assert result["cleanup_verified"] is False
    assert "host:exit:120" in result["cleanup_errors"]


def test_remote_cleanup_stops_owned_children_even_when_state_save_fails(monkeypatch, tmp_path):
    module = load_tool("am1_session_remote")
    args = type(
        "Args",
        (),
        {
            "session_id": "20260920T120000-1234abcd",
            "state_directory": str(tmp_path / "state"),
            "camera_head": "camera-head",
            "motor_head": "motor-head",
            "session_head": "session-head",
            "cleanup_timeout": 1.0,
        },
    )()
    events = []
    supervisor = module.RemoteSupervisor(args, module.BestEffortReporter(events.append))
    supervisor.children["host"] = object()
    stopped = []
    monkeypatch.setattr(
        module,
        "stop_owned_children",
        lambda children, timeout_s: stopped.extend(children) or {"cleanup_verified": True, "cleanup_errors": []},
    )
    supervisor.save = lambda: (_ for _ in ()).throw(OSError("state disk full"))

    result = supervisor.cleanup(reason="controller_stop")

    assert stopped == [supervisor.children["host"]]
    assert result["cleanup_verified"] is False
    assert result["cleanup_errors"] == ["state:pre-cleanup-save:OSError:state disk full", "state:terminal-save:OSError:state disk full"]
    assert events[-1]["terminal_status"] == "cleanup_unknown"
    assert events[-1]["cleanup_verified"] is False


@pytest.mark.parametrize(
    ("reason", "expected_status"),
    [("controller_stop", "complete"), ("signal", "complete"), ("controller_eof", "fault"),
     ("host_exit_2", "fault"), ("refusal", "refused"), ("supervisor_fault", "fault")],
)
def test_remote_terminal_status_preserves_runtime_failure_after_cleanup(tmp_path, reason, expected_status):
    module = load_tool("am1_session_remote")
    args = type(
        "Args",
        (),
        {
            "session_id": "20260920T120000-1234abcd",
            "state_directory": str(tmp_path / "state"),
            "camera_head": "camera-head",
            "motor_head": "motor-head",
            "session_head": "session-head",
            "cleanup_timeout": 1.0,
        },
    )()
    events = []
    supervisor = module.RemoteSupervisor(args, module.BestEffortReporter(events.append))
    supervisor.save = lambda: None
    module.stop_owned_children = lambda children, timeout_s: {"cleanup_verified": True, "cleanup_errors": []}

    supervisor.cleanup(reason=reason)

    assert supervisor.state["status"] == expected_status
    assert events[-1]["terminal_status"] == expected_status


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group signal semantics")
def test_posix_process_group_sigint_reaches_only_the_owned_nonhardware_child(tmp_path):
    module = load_tool("am1_session_remote")
    marker = tmp_path / "stopped"
    ready = tmp_path / "ready"
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import os,pathlib,signal,time; "
                f"p=pathlib.Path({str(marker)!r}); r=pathlib.Path({str(ready)!r}); "
                "signal.signal(signal.SIGINT, lambda *_: (p.write_text('SIGINT'), os._exit(0))); "
                "r.write_text('ready'); "
                "time.sleep(30)"
            ),
        ],
        start_new_session=True,
    )
    child = module.OwnedChild("host", process, process.pid, tmp_path / "control", None)
    deadline = time.monotonic() + 5
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ready.exists()

    result = module.stop_owned_children([child], timeout_s=5)

    assert result["cleanup_verified"] is True
    assert result["host_exit"] == 0
    assert marker.read_text(encoding="utf-8") == "SIGINT"


def test_remote_event_output_failure_cannot_prevent_owned_cleanup():
    module = load_tool("am1_session_remote")
    cleaned: list[str] = []

    reporter = module.BestEffortReporter(lambda payload: (_ for _ in ()).throw(BlockingIOError()))
    reporter.emit({"event": "host_ready"})
    module.cleanup_after_controller_loss(lambda: cleaned.append("cleanup"), reporter)

    assert cleaned == ["cleanup"]


def test_remote_controller_lease_expires_without_fresh_heartbeat(tmp_path):
    module = load_tool("am1_session_remote")
    args = type(
        "Args",
        (),
        {
            "session_id": "20260920T120000-1234abcd",
            "state_directory": str(tmp_path / "state"),
            "camera_head": "camera-head",
            "motor_head": "motor-head",
            "session_head": "session-head",
            "controller_lease_timeout": 5.0,
        },
    )()
    supervisor = module.RemoteSupervisor(args, module.BestEffortReporter(lambda payload: None))
    supervisor.children["camera"] = object()
    supervisor.last_controller_contact = time.monotonic() - 6.0

    assert supervisor.controller_lease_expired() is True
    supervisor.note_controller_contact()
    assert supervisor.controller_lease_expired() is False


def test_remote_command_reader_refreshes_contact_for_heartbeat(monkeypatch, tmp_path):
    module = load_tool("am1_session_remote")
    args = type(
        "Args",
        (),
        {
            "session_id": "20260920T120000-1234abcd",
            "state_directory": str(tmp_path / "state"),
            "camera_head": "camera-head",
            "motor_head": "motor-head",
            "session_head": "session-head",
        },
    )()
    supervisor = module.RemoteSupervisor(args, module.BestEffortReporter(lambda payload: None))
    supervisor.last_controller_contact = 0.0
    commands = module.queue.Queue()
    monkeypatch.setattr(module.sys, "stdin", io.StringIO("HEARTBEAT\n"))

    module._read_commands(supervisor, commands)

    assert supervisor.last_controller_contact > 0.0
    assert commands.get_nowait() == "HEARTBEAT"
    assert commands.get_nowait() == "__EOF__"


def test_remote_readiness_wait_refuses_when_controller_lease_expires(tmp_path):
    module = load_tool("am1_session_remote")
    args = type(
        "Args",
        (),
        {
            "session_id": "20260920T120000-1234abcd",
            "state_directory": str(tmp_path / "state"),
            "camera_head": "camera-head",
            "motor_head": "motor-head",
            "session_head": "session-head",
            "controller_lease_timeout": 0.01,
        },
    )()
    supervisor = module.RemoteSupervisor(args, module.BestEffortReporter(lambda payload: None))

    class Process:
        def poll(self): return None

    child = module.OwnedChild("host", Process(), 123, tmp_path / "control")
    supervisor.children["host"] = child
    supervisor.last_controller_contact = time.monotonic() - 1.0

    with pytest.raises(module.SessionRefusal, match="heartbeat lease expired"):
        supervisor._wait_for(child, lambda: None, 1.0, "motor host readiness")


def test_remote_readiness_wait_classifies_controller_stop_without_refusal(tmp_path):
    module = load_tool("am1_session_remote")
    args = type(
        "Args",
        (),
        {
            "session_id": "20260920T120000-1234abcd",
            "state_directory": str(tmp_path / "state"),
            "camera_head": "camera-head",
            "motor_head": "motor-head",
            "session_head": "session-head",
            "controller_lease_timeout": 5.0,
        },
    )()
    supervisor = module.RemoteSupervisor(args, module.BestEffortReporter(lambda payload: None))

    class Process:
        def poll(self): return None

    child = module.OwnedChild("camera", Process(), 123, tmp_path / "control")
    supervisor.children["camera"] = child
    supervisor.controller_stop_requested.set()

    with pytest.raises(module.SessionControlStop) as stopped:
        supervisor._wait_for(child, lambda: None, 1.0, "camera readiness")

    assert stopped.value.reason == "controller_stop"


def test_windows_client_preserves_foreground_interactive_handles(monkeypatch, tmp_path):
    module = load_tool("am1_session")
    calls = []
    log = tmp_path / "client.log"
    log.write_text("AM1_CLIENT_EXIT_CODE=0\n", encoding="utf-8")

    class Process:
        pid = 1234
        def poll(self): return 0
        def wait(self, timeout): return 0

    def popen(command, **kwargs):
        calls.append((command, kwargs))
        return Process()

    monkeypatch.setattr(module.subprocess, "Popen", popen)
    monkeypatch.setattr(module.shutil, "which", lambda name: "pwsh")
    config = type("Config", (), {"local_config": tmp_path / "local.json"})()
    client = module.WindowsClient(tmp_path, config, lambda: None, tmp_path / "stop")

    assert client.run(duration_seconds=60, log_path=log, stop_requested=lambda: False) == 0
    assert "stdin" not in calls[0][1]
    assert "stdout" not in calls[0][1]
    assert "stderr" not in calls[0][1]


def test_windows_client_runtime_polling_does_not_write_to_console(monkeypatch, tmp_path):
    module = load_tool("am1_session")
    log = tmp_path / "client.log"
    log.write_text('{"event": "am1_client_live_start"}\nAM1_CLIENT_EXIT_CODE=0\n', encoding="utf-8")

    class Process:
        pid = 1234

        def __init__(self): self.polls = 0
        def poll(self):
            self.polls += 1
            return None if self.polls == 1 else 0
        def wait(self, timeout): return 0

    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: Process())
    monkeypatch.setattr(module.shutil, "which", lambda name: "pwsh")
    monkeypatch.setattr(module.time, "sleep", lambda duration: None)
    monkeypatch.setattr("builtins.print", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("console write")))
    config = type("Config", (), {"local_config": tmp_path / "local.json"})()

    client = module.WindowsClient(tmp_path, config, lambda: None, tmp_path / "stop")

    assert client.run(duration_seconds=60, log_path=log, stop_requested=lambda: False) == 0


def test_logged_process_completes_and_captures_output_when_display_sink_is_blocked(tmp_path):
    helper = REPO_ROOT / "tools" / "am1_logged_process.py"
    log = tmp_path / "runtime.log"
    payload_size = 2_000_000
    process = subprocess.Popen(
        [
            sys.executable,
            str(helper),
            "--log",
            str(log),
            "--",
            sys.executable,
            "-c",
            f"import sys; sys.stdout.write('x' * {payload_size}); sys.stdout.flush()",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert process.wait(timeout=10) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    assert log.stat().st_size == payload_size


def test_logged_process_shows_remaining_live_time_from_disposable_reporter(tmp_path):
    helper = REPO_ROOT / "tools" / "am1_logged_process.py"
    log = tmp_path / "runtime.log"
    result = subprocess.run(
        [
            sys.executable,
            str(helper),
            "--log",
            str(log),
            "--duration-seconds",
            "60",
            "--",
            sys.executable,
            "-c",
            (
                "import time; "
                "print('{\"event\": \"am1_client_live_start\"}', flush=True); "
                "time.sleep(0.4)"
            ),
        ],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0
    assert "AM1 live time remaining:" in result.stdout


def test_powershell_logged_command_returns_with_blocked_outer_display(tmp_path):
    powershell = shutil.which("pwsh")
    if powershell is None:
        pytest.skip("PowerShell 7 is required")
    log = tmp_path / "runtime.log"
    payload_size = 2_000_000
    command = (
        f". '{REPO_ROOT / 'tools' / 'run_am1.ps1'}'; "
        f"$arguments = @('-c', \"import sys; sys.stdout.write('x' * {payload_size}); sys.stdout.flush()\"); "
        f"$exitCode = Invoke-Am1LoggedCommand -Executable '{sys.executable}' -Arguments $arguments "
        f"-LogPath '{log}'; exit $exitCode"
    )
    process = subprocess.Popen(
        [powershell, "-NoLogo", "-NoProfile", "-Command", command],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert process.wait(timeout=15) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    assert log.stat().st_size == payload_size


@pytest.mark.parametrize("stop_kind", ["explicit", "ctrl-c"])
def test_windows_client_labels_user_stop_130_even_when_child_cleanup_exits_zero(monkeypatch, tmp_path, stop_kind):
    module = load_tool("am1_session")
    log = tmp_path / "client.log"
    log.write_text("AM1_CLIENT_EXIT_CODE=0\n", encoding="utf-8")

    class Process:
        pid = 1234

        def __init__(self): self.returncode = None
        def poll(self): return self.returncode
        def wait(self, timeout): return self.returncode

    process = Process()
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(module.shutil, "which", lambda name: "pwsh")
    monkeypatch.setattr(module.WindowsClient, "_request_stop", lambda self: setattr(process, "returncode", 0))
    if stop_kind == "ctrl-c":
        monkeypatch.setattr(module.time, "sleep", lambda duration: (_ for _ in ()).throw(KeyboardInterrupt))
    config = type("Config", (), {"local_config": tmp_path / "local.json"})()
    client = module.WindowsClient(tmp_path, config, lambda: None, tmp_path / "stop")

    exit_code = client.run(
        duration_seconds=60,
        log_path=log,
        stop_requested=lambda: stop_kind == "explicit",
    )

    assert exit_code == 130


def test_windows_client_force_reaps_exact_owned_process_when_cooperative_stop_is_ignored(monkeypatch, tmp_path):
    module = load_tool("am1_session")
    log = tmp_path / "client.log"
    monotonic_values = iter([0.0, 0.0, 31.0])

    class Process:
        pid = 4321
        def poll(self): return None

    process = Process()
    reaped = []
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(module.shutil, "which", lambda name: "pwsh")
    monkeypatch.setattr(module.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(module.time, "sleep", lambda duration: None)
    monkeypatch.setattr(module.WindowsClient, "_request_stop", lambda self: None)
    monkeypatch.setattr(
        module.WindowsClient,
        "_force_reap_owned_client",
        lambda self, owned: reaped.append(owned.pid) or True,
    )
    config = type("Config", (), {"local_config": tmp_path / "local.json"})()
    client = module.WindowsClient(tmp_path, config, lambda: None, tmp_path / "stop")

    with pytest.raises(module.SessionError, match="did not honor"):
        client.run(duration_seconds=60, log_path=log, stop_requested=lambda: True)

    assert reaped == [4321]
    assert client.cleanup_status()["cleanup_verified"] is False
    assert client.cleanup_status()["owned_process_reaped"] is True


def test_windows_client_second_ctrl_c_force_reaps_only_owned_process(monkeypatch, tmp_path):
    module = load_tool("am1_session")
    log = tmp_path / "client.log"

    class Process:
        pid = 7654

        def poll(self): return None
        def wait(self, timeout): raise KeyboardInterrupt

    process = Process()
    reaped = []
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(module.shutil, "which", lambda name: "pwsh")
    monkeypatch.setattr(module.time, "sleep", lambda duration: (_ for _ in ()).throw(KeyboardInterrupt))
    monkeypatch.setattr(module.WindowsClient, "_request_stop", lambda self: None)
    monkeypatch.setattr(
        module.WindowsClient,
        "_force_reap_owned_client",
        lambda self, owned: reaped.append(owned.pid) or True,
    )
    config = type("Config", (), {"local_config": tmp_path / "local.json"})()
    client = module.WindowsClient(tmp_path, config, lambda: None, tmp_path / "stop")

    assert client.run(duration_seconds=60, log_path=log, stop_requested=lambda: False) == 130
    assert reaped == [7654]
    assert client.cleanup_status() == {
        "cleanup_verified": False,
        "state": "forced_after_repeated_interrupt",
        "client_wrapper_pid": 7654,
        "owned_process_reaped": True,
    }


def test_coordinator_marks_unverified_client_cleanup_even_when_remote_cleanup_passes(tmp_path):
    module = load_tool("am1_session")

    class Remote:
        def preflight(self): return {}
        def start_camera(self): return {"browser_url": "http://camera"}
        def start_host(self): return {}
        def stop(self): return {"cleanup_verified": True}

    class Client:
        def run(self, **kwargs): return 0
        def cleanup_status(self): return {"cleanup_verified": False, "state": "forced"}

    outcome = module.SessionCoordinator(
        remote=Remote(), client=Client(), open_browser=lambda url: None,
        collect_remote_log=lambda remote, local: (True, None), input_fn=lambda prompt: "",
    ).run(
        duration_seconds=60, session_id="20260920T120000-1234abcd",
        session_directory=tmp_path, client_log_path=tmp_path / "client.log",
        stop_requested=lambda: False,
    )

    assert outcome.cleanup_verified is False
    assert outcome.final_exit_code == 3
    assert outcome.cleanup["client"]["state"] == "forced"


def test_collect_only_refuses_manifest_destination_outside_exact_session_directory(tmp_path):
    module = load_tool("am1_session")
    session_id = "20260920T120000-1234abcd"
    directory = tmp_path / f"am1-session-{session_id}"
    directory.mkdir()
    outside = tmp_path / "outside.log"
    (directory / "missing-logs.json").write_text(
        json.dumps(
            {
                "session_id": session_id,
                "missing": [
                    {
                        "remote_path": "/logs/am1-host.log",
                        "destination": str(outside),
                        "error": "old failure",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (directory / "session-summary.json").write_text(
        json.dumps({"session_id": session_id}), encoding="utf-8"
    )
    config = type(
        "Config",
        (),
        {
            "windows_log_directory": tmp_path,
            "remote_log_directory": "/logs",
            "ssh_target": "am1-pi",
        },
    )()

    with pytest.raises(module.SessionError, match="exact log filename"):
        module.collect_only(config, session_id)

    assert not outside.exists()


def test_collect_only_refuses_manifest_overwrite_of_original_summary(tmp_path):
    module = load_tool("am1_session")
    session_id = "20260920T120000-1234abcd"
    directory = tmp_path / f"am1-session-{session_id}"
    directory.mkdir()
    summary_path = directory / "session-summary.json"
    original_summary = json.dumps({"session_id": session_id}) + "\n"
    summary_path.write_text(original_summary, encoding="utf-8")
    (directory / "missing-logs.json").write_text(
        json.dumps(
            {
                "session_id": session_id,
                "missing": [
                    {
                        "remote_path": "/logs/am1-exact-host.log",
                        "destination": str(summary_path),
                        "error": "old failure",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    config = type("Config", (), {"windows_log_directory": tmp_path, "remote_log_directory": "/logs"})()

    with pytest.raises(module.SessionError, match="exact log filename"):
        module.collect_only(config, session_id)

    assert summary_path.read_text(encoding="utf-8") == original_summary


@pytest.mark.parametrize(
    ("artifact", "identity"),
    [
        ("session-summary.json", None),
        ("session-summary.json", "20260920T120000-deadbeef"),
        ("missing-logs.json", None),
        ("missing-logs.json", "20260920T120000-deadbeef"),
    ],
)
def test_collect_only_requires_exact_session_identity(tmp_path, artifact, identity):
    module = load_tool("am1_session")
    session_id = "20260920T120000-1234abcd"
    directory = tmp_path / f"am1-session-{session_id}"
    directory.mkdir()
    (directory / "session-summary.json").write_text(
        json.dumps({"session_id": session_id}), encoding="utf-8"
    )
    payload = {"missing": []} if artifact == "missing-logs.json" else {}
    if identity is not None:
        payload["session_id"] = identity
    (directory / artifact).write_text(json.dumps(payload), encoding="utf-8")
    config = type("Config", (), {"windows_log_directory": tmp_path})()

    with pytest.raises(module.SessionError, match="identity does not match"):
        module.collect_only(config, session_id)


def test_collect_only_discovers_exact_session_logs_without_rewriting_original_summary(monkeypatch, tmp_path):
    module = load_tool("am1_session")
    session_id = "20260922T120000-1234abcd"
    directory = tmp_path / f"am1-session-{session_id}"
    directory.mkdir()
    summary_path = directory / "session-summary.json"
    original_summary = json.dumps(
        {
            "session_id": session_id,
            "operational_exit_code": 2,
            "final_exit_code": 2,
            "failure": "SessionError: SSH controller exited with status 255",
            "remote_logs": ["/logs/am1-exact-camera.log"],
            "missing_logs": [],
        },
        indent=2,
    ) + "\n"
    summary_path.write_text(original_summary, encoding="utf-8")
    config = type(
        "Config",
        (),
        {
            "windows_log_directory": tmp_path,
            "remote_log_directory": "/logs",
            "ssh_target": "am1-pi",
            "remote_python": "/python",
            "remote_helper": "/helper.py",
            "remote_state_directory": "/state",
        },
    )()
    recovered_state = {
        "cleanup_verified": True,
        "host_exit": 0,
        "camera_exit": 0,
        "host_log": "/logs/am1-exact-host.log",
        "camera_log": "/logs/am1-exact-camera.log",
        "persisted_status": "fault",
        "terminal_status": "fault",
        "stop_reason": "controller_lease_expired",
        "persisted_state_available": True,
        "persisted_state_terminal": True,
    }
    monkeypatch.setattr(module.SSHRemote, "_persisted_terminal_state", lambda self: recovered_state)

    copied = []
    def collect(config, remote_path, destination):
        copied.append((remote_path, destination.name))
        destination.write_text(remote_path + "\n", encoding="utf-8")
        return True, None
    monkeypatch.setattr(module, "_collect_with_scp", collect)

    assert module.collect_only(config, session_id) == 0

    assert summary_path.read_text(encoding="utf-8") == original_summary
    assert copied == [
        ("/logs/am1-exact-host.log", "am1-exact-host.log"),
        ("/logs/am1-exact-camera.log", "am1-exact-camera.log"),
    ]
    recovery = json.loads((directory / "evidence-recovery.json").read_text(encoding="utf-8"))
    assert recovery["session_id"] == session_id
    assert recovery["original_final_exit_code"] == 2
    assert recovery["persisted_state"]["stop_reason"] == "controller_lease_expired"
    assert recovery["recovered_logs"] == ["/logs/am1-exact-host.log", "/logs/am1-exact-camera.log"]


def test_collect_only_keeps_nonterminal_recovery_incomplete(monkeypatch, tmp_path, capsys):
    module = load_tool("am1_session")
    session_id = "20260922T120000-1234abcd"
    directory = tmp_path / f"am1-session-{session_id}"
    directory.mkdir()
    summary_path = directory / "session-summary.json"
    original_summary = json.dumps({"session_id": session_id, "final_exit_code": 2}) + "\n"
    summary_path.write_text(original_summary, encoding="utf-8")
    config = type(
        "Config",
        (),
        {
            "windows_log_directory": tmp_path,
            "remote_log_directory": "/logs",
            "ssh_target": "am1-pi",
            "remote_python": "/python",
            "remote_helper": "/helper.py",
            "remote_state_directory": "/state",
        },
    )()
    monkeypatch.setattr(
        module.SSHRemote,
        "_persisted_terminal_state",
        lambda self: {
            "cleanup_verified": False,
            "persisted_status": "host_starting",
            "persisted_state_available": True,
            "persisted_state_terminal": False,
            "cleanup_error": "Persisted remote state is nonterminal: 'host_starting'",
        },
    )

    assert module.collect_only(config, session_id) == 4
    assert summary_path.read_text(encoding="utf-8") == original_summary
    recovery = json.loads((directory / "evidence-recovery.json").read_text(encoding="utf-8"))
    assert recovery["persisted_state"]["persisted_status"] == "host_starting"
    assert recovery["original_summary_unchanged"] is True
    assert "Persisted remote state is nonterminal: 'host_starting'" in capsys.readouterr().err


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows process semantics")
def test_windows_pid_query_leaves_disposable_child_alive():
    module = load_tool("am1_session")
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert module._pid_running(process.pid) is True
        with pytest.raises(subprocess.TimeoutExpired):
            process.wait(timeout=0.25)
    finally:
        stop_disposable_process(process)


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows process semantics")
@pytest.mark.parametrize(
    ("wait_result", "expected"),
    [
        (0x00000102, True),
        (0x00000000, False),
    ],
)
def test_windows_pid_query_uses_and_closes_query_handle(monkeypatch, wait_result, expected):
    module = load_tool("am1_session")
    kernel32 = fake_windows_kernel32(wait_result=wait_result)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: kernel32)
    monkeypatch.setattr(
        module.os,
        "kill",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("os.kill must not query Windows PIDs")),
    )

    assert module._pid_running(424242) is expected
    assert kernel32.OpenProcess.calls == [(0x00100000, False, 424242)]
    assert kernel32.WaitForSingleObject.calls == [(1234, 0)]
    assert kernel32.CloseHandle.calls == [(1234,)]


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows process semantics")
def test_windows_pid_query_treats_invalid_parameter_as_absent(monkeypatch):
    module = load_tool("am1_session")
    kernel32 = fake_windows_kernel32(open_result=0, wait_result=0xFFFFFFFF)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: kernel32)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 87)
    monkeypatch.setattr(
        module.os,
        "kill",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("os.kill must not query Windows PIDs")),
    )

    assert module._pid_running(424242) is False
    assert kernel32.CloseHandle.calls == []


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows process semantics")
@pytest.mark.parametrize(
    ("last_error", "message"),
    [
        (5, "access denied"),
        (12345, "could not be verified"),
    ],
)
def test_windows_pid_query_refuses_access_denied_or_unknown(monkeypatch, last_error, message):
    module = load_tool("am1_session")
    kernel32 = fake_windows_kernel32(open_result=0, wait_result=0xFFFFFFFF)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: kernel32)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: last_error)
    monkeypatch.setattr(
        module.os,
        "kill",
        lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError("query denied")),
    )

    with pytest.raises(module.SessionError, match=message):
        module._pid_running(424242)


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows process semantics")
def test_request_stop_keeps_fake_controller_alive_to_acknowledge_cleanup(monkeypatch, tmp_path):
    module = load_tool("am1_session")
    state = tmp_path / "state"
    state.mkdir()
    active_path = state / "active.json"
    stop_request = state / "stop-20260921T120000-1234abcd"
    controller_script = "\n".join(
        [
            "import json, os, sys, time",
            "from pathlib import Path",
            "stop_request = Path(sys.argv[1])",
            "active_path = Path(sys.argv[2])",
            "deadline = time.monotonic() + 10",
            "while time.monotonic() < deadline:",
            "    if stop_request.exists():",
            "        payload = json.loads(active_path.read_text(encoding='utf-8'))",
            "        payload.update(status='complete', final_exit_code=130)",
            "        temporary = active_path.with_name('.active.ack.tmp')",
            "        temporary.write_text(json.dumps(payload), encoding='utf-8')",
            "        os.replace(temporary, active_path)",
            "        time.sleep(30)",
            "        raise SystemExit(0)",
            "    time.sleep(0.01)",
            "raise SystemExit(3)",
        ]
    )
    process = subprocess.Popen(
        [sys.executable, "-c", controller_script, str(stop_request), str(active_path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    active_path.write_text(
        json.dumps(
            {
                "session_id": "20260921T120000-1234abcd",
                "controller_pid": process.pid,
                "stop_request": str(stop_request),
                "status": "active",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "STOP_CONFIRMATION_TIMEOUT_S", 2.0)
    config = type("Config", (), {"local_state_directory": state})()

    try:
        assert module.request_stop(config) == 130
        assert stop_request.read_text(encoding="utf-8") == "20260921T120000-1234abcd\n"
        assert process.poll() is None
    finally:
        stop_disposable_process(process)


def test_stop_returns_recorded_result_during_post_cleanup_collection_without_second_ssh(monkeypatch, tmp_path):
    module = load_tool("am1_session")
    state = tmp_path / "state"
    state.mkdir()
    (state / "active.json").write_text(
        json.dumps(
            {
                "session_id": "20260920T120000-1234abcd",
                "controller_pid": os.getpid(),
                "status": "collecting",
                "operational_exit_code": 130,
                "final_exit_code": 130,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("SSH must not run after cleanup")),
    )
    config = type("Config", (), {"local_state_directory": state})()

    assert module.request_stop(config) == 130


def test_powershell_entrypoint_declares_start_stop_and_collection_without_numeric_binding():
    text = (REPO_ROOT / "tools" / "run_am1_session.ps1").read_text(encoding="utf-8")

    assert "[string]$DurationSeconds" in text
    assert "[switch]$Stop" in text
    assert "[switch]$CollectOnly" in text
    assert "ConvertTo-Am1SessionDuration" in text
    assert "[int]$DurationSeconds" not in text
    assert "2>&1 | Out-Host" not in text


def test_unified_local_command_enables_enter_only_client_confirmations():
    text = (REPO_ROOT / "tools" / "run_am1.ps1").read_text(encoding="utf-8")
    local_command = text.split("if ($Mode -eq 'Local')", 2)[2].split(
        "$arguments += @(\n            '--no_cameras'", 1
    )[0]

    assert "--unified_session_enter_confirmations" in local_command


def test_camera_page_uses_neutral_read_only_viewer_wording():
    page = (REPO_ROOT / "tools" / "am1_camera" / "index.html").read_text(encoding="utf-8")

    assert "Keep motor power off" not in page
    assert "read-only" in page.lower()


def test_controller_prints_identity_and_persists_pre_client_failure(monkeypatch, tmp_path, capsys):
    module = load_tool("am1_session")
    session_id = "20260921T120000-1234abcd"
    logs = tmp_path / "logs"
    state = tmp_path / "state"
    logs.mkdir()
    state.mkdir()
    config = type(
        "Config",
        (),
        {
            "windows_log_directory": logs,
            "local_state_directory": state,
        },
    )()

    class Remote:
        def __init__(self, config, actual_session_id, session_directory):
            assert actual_session_id == session_id

        def set_stop_requested(self, callback): pass
        def fault(self): return None
        def preflight(self): raise module.SessionError("fake pre-client startup failure")
        def stop(self): return {"cleanup_verified": True, "nothing_started": True}

    class Client:
        def __init__(self, *args): pass
        def run(self, **kwargs): raise AssertionError("client must not start")
        def cleanup_status(self): return {"cleanup_verified": True, "not_started": True}

    monkeypatch.setattr(module, "_new_session_id", lambda: session_id)
    monkeypatch.setattr(module, "SSHRemote", Remote)
    monkeypatch.setattr(module, "WindowsClient", Client)
    monkeypatch.setattr(module.os, "startfile", lambda path: None)

    exit_code = module._run_start_locked(REPO_ROOT, config, 90)

    output = capsys.readouterr().out
    result_directory = logs / f"am1-session-{session_id}"
    summary = json.loads((result_directory / "session-summary.json").read_text(encoding="utf-8"))
    assert output.index(f"AM1_SESSION_ID={session_id}") < output.index("fake pre-client startup failure")
    assert f"AM1_SESSION_RESULT={result_directory}" in output
    assert "AM1_SESSION_FAILURE=SessionError: fake pre-client startup failure" in output
    assert "AM1_SESSION_EXIT_CODE=2" in output
    assert summary["failure"] == "SessionError: fake pre-client startup failure"
    assert exit_code == 2


@pytest.mark.parametrize("value", ["0", "1801", "1.5", "NaN", "1e3"])
def test_powershell_entrypoint_rejects_raw_invalid_duration_before_config_or_ssh(value, tmp_path):
    powershell = shutil.which("pwsh")
    if powershell is None:
        pytest.skip("PowerShell 7 is required")
    result = subprocess.run(
        [
            powershell, "-NoLogo", "-NoProfile", "-File",
            str(REPO_ROOT / "tools" / "run_am1_session.ps1"),
            "-DurationSeconds", value,
            "-ConfigPath", str(tmp_path / "missing.json"),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode != 0
    assert "whole number from 1 through 1800" in (result.stdout + result.stderr)
    assert "Private AM1 session config is missing" not in (result.stdout + result.stderr)


def test_powershell_entrypoint_displays_child_output_and_returns_exact_child_exit(tmp_path):
    powershell = shutil.which("pwsh")
    if powershell is None:
        pytest.skip("PowerShell 7 is required")
    fake_python = tmp_path / "fake-python.cmd"
    fake_python.write_text("@echo SESSION_CHILD_OUTPUT\r\n@exit /b 7\r\n", encoding="utf-8")
    config = tmp_path / "session.json"
    config.write_text(json.dumps({"windows_python": str(fake_python)}), encoding="utf-8")

    result = subprocess.run(
        [
            powershell, "-NoLogo", "-NoProfile", "-File",
            str(REPO_ROOT / "tools" / "run_am1_session.ps1"),
            "-Stop", "-ConfigPath", str(config),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 7
    assert "SESSION_CHILD_OUTPUT" in result.stdout


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows console and PowerShell semantics")
def test_real_powershell_entrypoint_exposes_each_prompt_before_input_and_hands_off_to_client(tmp_path):
    powershell = shutil.which("pwsh")
    if powershell is None:
        pytest.skip("PowerShell 7 is required")
    shutil.copy2(REPO_ROOT / "tools" / "run_am1_session.ps1", tmp_path / "run_am1_session.ps1")
    (tmp_path / "am1_session.py").write_text(
        "\n".join(
            [
                "import sys",
                "print('AM1_SESSION_ID=fake-session', flush=True)",
                "print('AM1_SESSION_RESULT=C:/fake/results', flush=True)",
                "for index in range(1, 4):",
                "    try:",
                "        print(f'CONFIRMATION {index}/3>', flush=True)",
                "        response = input()",
                "    except EOFError:",
                "        print('FAKE_REFUSAL=console EOF', flush=True)",
                "        raise SystemExit(2)",
                "    if response != '':",
                "        print('FAKE_REFUSAL=Enter only', flush=True)",
                "        raise SystemExit(2)",
                "    print(f'FAKE_STAGE_{index}_CONFIRMED', flush=True)",
                "try:",
                "    print('FAKE_CLIENT_INPUT>', flush=True)",
                "    client_input = input()",
                "except EOFError:",
                "    print('FAKE_REFUSAL=client EOF', flush=True)",
                "    raise SystemExit(2)",
                "print(f'FAKE_CLIENT_INPUT={client_input}', flush=True)",
                "raise SystemExit(0 if client_input == 'Q' else 2)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    config = tmp_path / "session.json"
    config.write_text(json.dumps({"windows_python": sys.executable}), encoding="utf-8")
    process = subprocess.Popen(
        [
            powershell, "-NoLogo", "-NoProfile", "-File", str(tmp_path / "run_am1_session.ps1"),
            "-DurationSeconds", "90", "-ConfigPath", str(config),
        ],
        cwd=tmp_path,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
    )
    output = bytearray()

    def read_output():
        assert process.stdout is not None
        while chunk := process.stdout.read(1):
            output.extend(chunk)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    try:
        assert process.stdin is not None
        for index in range(1, 4):
            wait_for_process_output(output, f"CONFIRMATION {index}/3>".encode())
            process.stdin.write(b"\r\n")
            process.stdin.flush()
            wait_for_process_output(output, f"FAKE_STAGE_{index}_CONFIRMED".encode())
        wait_for_process_output(output, b"FAKE_CLIENT_INPUT>")
        process.stdin.write(b"Q\r\n")
        process.stdin.flush()
        assert process.wait(timeout=10) == 0
        reader.join(timeout=2)
    finally:
        stop_disposable_process(process)

    rendered = bytes(output)
    assert b"AM1_SESSION_ID=fake-session" in rendered
    assert b"AM1_SESSION_RESULT=C:/fake/results" in rendered
    assert b"FAKE_CLIENT_INPUT=Q" in rendered


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows console and PowerShell semantics")
def test_real_powershell_entrypoint_treats_console_eof_as_refusal(tmp_path):
    powershell = shutil.which("pwsh")
    if powershell is None:
        pytest.skip("PowerShell 7 is required")
    shutil.copy2(REPO_ROOT / "tools" / "run_am1_session.ps1", tmp_path / "run_am1_session.ps1")
    (tmp_path / "am1_session.py").write_text(
        "import sys\n"
        "print('AM1_SESSION_RESULT=C:/fake/results', flush=True)\n"
        "try:\n"
        "    print('CONFIRMATION 1/3>', flush=True)\n"
        "    input()\n"
        "except EOFError:\n"
        "    print('FAKE_REFUSAL=console EOF', flush=True)\n"
        "    raise SystemExit(2)\n"
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )
    config = tmp_path / "session.json"
    config.write_text(json.dumps({"windows_python": sys.executable}), encoding="utf-8")
    process = subprocess.Popen(
        [
            powershell, "-NoLogo", "-NoProfile", "-File", str(tmp_path / "run_am1_session.ps1"),
            "-DurationSeconds", "90", "-ConfigPath", str(config),
        ],
        cwd=tmp_path,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
    )
    output = bytearray()

    def read_output():
        assert process.stdout is not None
        while chunk := process.stdout.read(1):
            output.extend(chunk)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    try:
        wait_for_process_output(output, b"CONFIRMATION 1/3>")
        assert process.stdin is not None
        process.stdin.close()
        assert process.wait(timeout=10) == 2
        reader.join(timeout=2)
    finally:
        stop_disposable_process(process)

    assert b"FAKE_REFUSAL=console EOF" in bytes(output)


def test_local_runtime_uses_direct_logger_instead_of_tee_pipeline():
    text = (REPO_ROOT / "tools" / "run_am1.ps1").read_text(encoding="utf-8")
    function = text.split("function Invoke-Am1LoggedCommand", 1)[1].split(
        "function Assert-Am1ReviewedWorktree", 1
    )[0]

    assert "am1_logged_process.py" in function
    assert "Tee-Object" not in function
    assert "Out-Host" not in function


def test_local_runtime_exit_marker_and_startup_metadata_never_block_on_console_output():
    text = (REPO_ROOT / "tools" / "run_am1.ps1").read_text(encoding="utf-8")
    runtime = text.split("$isUnifiedSession =", 1)[1]

    assert "if (-not $isUnifiedSession)" in runtime
    assert "if ($isUnifiedSession)" in runtime
    assert '"AM1_CLIENT_EXIT_CODE=$clientExit" | Add-Content' in runtime
    assert '"AM1_CLIENT_EXIT_CODE=$clientExit" | Tee-Object' in runtime
