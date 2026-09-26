"""Hardware-free boundaries for unified AM1 SSH establishment."""

from __future__ import annotations

import importlib.util
import io
import sys
import threading
import time
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def load_session_tool():
    path = REPO_ROOT / "tools" / "am1_session.py"
    spec = importlib.util.spec_from_file_location("test_am1_session_ssh", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def fake_config():
    return type(
        "Config", (),
        {
            "ssh_target": "am1-pi", "remote_python": "/python", "remote_helper": "/helper.py",
            "remote_session_repository": "/session", "remote_session_head": "a" * 40,
            "remote_camera_repository": "/camera", "remote_camera_head": "b" * 40,
            "remote_camera_python": "/camera-python", "remote_motor_repository": "/motor",
            "remote_motor_head": "c" * 40, "remote_log_directory": "/logs",
            "remote_state_directory": "/state",
        },
    )()


class FakeProcess:
    def __init__(self, *, exit_code: int | None, stdout: str = ""):
        self.returncode = exit_code
        self.stdin = io.StringIO()
        self.stdout = io.StringIO(stdout)

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


def test_initial_preauth_timeout_retries_same_session_without_starting_host(monkeypatch, tmp_path):
    module = load_session_tool()
    session_id = "20260926T120000-1234abcd"
    remote = module.SSHRemote(fake_config(), session_id, tmp_path)
    launches = []

    def launch(command, **kwargs):
        launches.append(list(command))
        first = len(launches) == 1
        if first:
            kwargs["stderr"].write(
                "ssh: connect to host 192.168.1.134 port 22: Connection timed out\n"
            )
            kwargs["stderr"].flush()
            if "-E" in command:
                Path(command[command.index("-E") + 1]).write_text(
                    "debug1: Connecting to 192.168.1.134 [192.168.1.134] port 22.\n"
                    "ssh: connect to host 192.168.1.134 port 22: Connection timed out\n",
                    encoding="utf-8",
                )
        return FakeProcess(
            exit_code=255 if first else None,
            stdout="" if first else '{"event":"preflight_ready","session_id":"' + session_id + '"}\n',
        )

    monkeypatch.setattr(module.subprocess, "Popen", launch)
    monkeypatch.setattr(module, "SSH_INITIAL_BACKOFF_S", (0.0, 0.0), raising=False)

    result = remote.preflight()

    remote._heartbeat_stop.set()
    remote.stderr_stream.close()
    assert result["event"] == "preflight_ready"
    assert len(launches) == 2
    assert all(command[command.index("--session-id") + 1] == session_id for command in launches)
    assert all("-E" in command for command in launches)
    assert remote.fault() is None


@pytest.mark.parametrize(
    "client_trace,remote_stderr,expected_error,remote_command_possible",
    [
        (
            "debug1: Connecting to 192.168.1.134 [192.168.1.134] port 22.\n"
            "debug1: Connection established.\nHost key verification failed.\n",
            "",
            "Host key verification failed",
            False,
        ),
        (
            "debug1: Connecting to 192.168.1.134 [192.168.1.134] port 22.\n"
            "Permission denied (publickey).\n",
            "",
            "Permission denied (publickey)",
            False,
        ),
        (
            "debug1: Connecting to 192.168.1.134 [192.168.1.134] port 22.\n"
            "Authenticated to 192.168.1.134 using publickey.\n"
            "debug1: Sending command: /python /helper.py supervise\n",
            "ssh: connect to host 192.168.1.134 port 22: Connection timed out\n",
            "SSH controller exited",
            True,
        ),
        (
            "",
            "command-line: line 0: Bad configuration option: am1invalidoption\n",
            "Bad configuration option",
            False,
        ),
        (
            "debug1: Connecting to 192.168.1.134 [192.168.1.134] port 22.\n"
            "ssh: connect to host 192.168.1.134 port 22: Connection timed out\n"
            "Host key verification failed.\n",
            "",
            "SSH controller exited",
            True,
        ),
        (
            "debug1: Connecting to 192.168.1.134 [192.168.1.134] port 22.\n"
            "Permission denied (publickey).\n"
            "ssh: connect to host 192.168.1.134 port 22: Connection timed out\n",
            "",
            "SSH controller exited",
            True,
        ),
    ],
)
def test_auth_or_possible_remote_dispatch_never_relaunches_supervisor(
    monkeypatch, tmp_path, client_trace, remote_stderr, expected_error, remote_command_possible,
):
    module = load_session_tool()
    remote = module.SSHRemote(fake_config(), "20260926T120000-1234abcd", tmp_path)
    launches = []

    def launch(command, **kwargs):
        launches.append(list(command))
        kwargs["stderr"].write(remote_stderr)
        kwargs["stderr"].flush()
        Path(command[command.index("-E") + 1]).write_text(client_trace, encoding="utf-8")
        return FakeProcess(exit_code=255)

    monkeypatch.setattr(module.subprocess, "Popen", launch)
    monkeypatch.setattr(module, "SSH_INITIAL_BACKOFF_S", (0.0, 0.0))
    state_queries = []
    monkeypatch.setattr(
        remote, "_persisted_terminal_state",
        lambda: state_queries.append(remote.session_id) or {
            "cleanup_verified": False,
            "persisted_state_available": True,
            "persisted_status": "host_starting",
        },
    )

    with pytest.raises(module.SessionError) as caught:
        remote.preflight()
    cleanup = remote.stop()

    assert expected_error in str(caught.value)
    assert len(launches) == 1
    if remote_command_possible:
        assert state_queries == [remote.session_id]
        assert cleanup["cleanup_verified"] is False
        assert cleanup["persisted_status"] == "host_starting"
    else:
        assert state_queries == []
        assert cleanup["nothing_started"] is True
        assert cleanup["cleanup_verified"] is True


def test_three_preauth_failures_stop_without_remote_command_or_fourth_attempt(monkeypatch, tmp_path):
    module = load_session_tool()
    remote = module.SSHRemote(fake_config(), "20260926T120000-1234abcd", tmp_path)
    launches = []

    def launch(command, **kwargs):
        launches.append(list(command))
        kwargs["stderr"].write("ssh: connect to host 192.168.1.134 port 22: Connection refused\n")
        kwargs["stderr"].flush()
        Path(command[command.index("-E") + 1]).write_text(
            "debug1: Connecting to 192.168.1.134 [192.168.1.134] port 22.\n"
            "ssh: connect to host 192.168.1.134 port 22: Connection refused\n",
            encoding="utf-8",
        )
        return FakeProcess(exit_code=255)

    monkeypatch.setattr(module.subprocess, "Popen", launch)
    monkeypatch.setattr(module, "SSH_INITIAL_BACKOFF_S", (0.0, 0.0))

    with pytest.raises(module.SessionError, match="Connection refused"):
        remote.preflight()
    cleanup = remote.stop()

    assert len(launches) == 3
    assert cleanup["nothing_started"] is True
    assert cleanup["cleanup_verified"] is True
    assert [item["attempt"] for item in cleanup["ssh_initial_attempts"]] == [1, 2, 3]


def test_initial_retry_wait_is_promptly_cancellable(tmp_path):
    module = load_session_tool()
    remote = module.SSHRemote(fake_config(), "20260926T120000-1234abcd", tmp_path)
    calls = 0

    def stop_requested():
        nonlocal calls
        calls += 1
        return calls >= 2

    remote.set_stop_requested(stop_requested)
    start = time.monotonic()
    with pytest.raises(module.SessionStopped, match="reconnect wait"):
        remote._pause_before_initial_retry(6.0, start + 40.0)

    assert time.monotonic() - start < 0.5


def test_failed_initial_attempt_remains_diagnosable_when_backoff_budget_expires(
    monkeypatch, tmp_path,
):
    module = load_session_tool()
    remote = module.SSHRemote(fake_config(), "20260926T120000-1234abcd", tmp_path)

    def launch(command, **kwargs):
        Path(command[command.index("-E") + 1]).write_text(
            "debug1: Connecting to 192.168.1.134 [192.168.1.134] port 22.\n"
            "ssh: connect to host 192.168.1.134 port 22: Connection timed out\n",
            encoding="utf-8",
        )
        return FakeProcess(exit_code=255)

    monkeypatch.setattr(module.subprocess, "Popen", launch)
    monkeypatch.setattr(
        remote, "_pause_before_initial_retry",
        lambda *_: (_ for _ in ()).throw(module.SessionError("Initial SSH connection budget expired")),
    )

    with pytest.raises(module.SessionError, match="budget expired"):
        remote.preflight()
    cleanup = remote.stop()

    assert "Connection timed out" in cleanup["primary_fault"]["ssh_stderr"]
    assert "Connection timed out" in cleanup["ssh_initial_attempts"][0]["fault"]["ssh_stderr"]
    assert cleanup["ssh_initial_attempts"][0]["fault"]["ssh_client_trace"].endswith(
        "ssh-client-attempt-1.log"
    )


def test_unfinished_event_reader_prevents_no_dispatch_cleanup_shortcut(monkeypatch, tmp_path):
    module = load_session_tool()
    remote = module.SSHRemote(fake_config(), "20260926T120000-1234abcd", tmp_path)
    reader_release = threading.Event()
    state_queries = []

    def launch(command, **kwargs):
        Path(command[command.index("-E") + 1]).write_text(
            "debug1: Connecting to 192.168.1.134 [192.168.1.134] port 22.\n"
            "Host key verification failed.\n",
            encoding="utf-8",
        )
        return FakeProcess(exit_code=255)

    monkeypatch.setattr(module.subprocess, "Popen", launch)
    monkeypatch.setattr(remote, "_read_events", lambda: reader_release.wait(3))
    monkeypatch.setattr(
        remote, "_persisted_terminal_state",
        lambda: state_queries.append(remote.session_id) or {
            "cleanup_verified": False,
            "persisted_state_available": True,
            "persisted_status": "host_starting",
        },
    )

    try:
        with pytest.raises(module.SessionError, match="SSH controller exited"):
            remote.preflight()
        cleanup = remote.stop()
    finally:
        reader_release.set()
        remote.reader.join(timeout=1)

    assert state_queries == [remote.session_id]
    assert cleanup["cleanup_verified"] is False
