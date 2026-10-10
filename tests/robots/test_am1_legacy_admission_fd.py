"""Legacy supervisor hands off only its held admission FD, without motor access."""

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import am1_session_remote as remote


def supervisor(tmp_path):
    args = SimpleNamespace(
        state_directory=str(tmp_path),
        session_id="20261010T180000-1234abcd",
        camera_head="camera",
        motor_head="motor",
        session_head="helper",
        motor_repository="/motor",
        log_directory="/logs",
        host_ready_timeout=180,
    )
    result = remote.RemoteSupervisor(args, remote.BestEffortReporter(lambda payload: None))
    result.session_root.mkdir(mode=0o700)
    return result


def test_host_spawn_hands_off_only_held_fd_and_keeps_parent_open(monkeypatch, tmp_path):
    owner = supervisor(tmp_path)
    owner.lock_stream = (tmp_path / "active.lock").open("a+b")
    fd = owner.lock_stream.fileno()
    called = []
    monkeypatch.setattr(
        remote.subprocess,
        "Popen",
        lambda command, **kwargs: called.append(kwargs) or SimpleNamespace(pid=123),
    )
    try:
        child = owner._spawn("host", ["fake-host"], {})
        assert called[0]["pass_fds"] == (fd,)
        assert called[0]["close_fds"] is True
        assert not os.get_inheritable(fd)
        assert not owner.lock_stream.closed
        child.control_stream.close()
        assert not owner.lock_stream.closed
    finally:
        owner.lock_stream.close()


def test_camera_spawn_never_inherits_physical_admission(monkeypatch, tmp_path):
    owner = supervisor(tmp_path)
    owner.lock_stream = (tmp_path / "active.lock").open("a+b")
    called = []
    monkeypatch.setattr(
        remote.subprocess,
        "Popen",
        lambda command, **kwargs: called.append(kwargs) or SimpleNamespace(pid=123),
    )
    try:
        child = owner._spawn("camera", ["fake-camera"], {})
        assert called[0]["pass_fds"] == () and called[0]["close_fds"] is True
        child.control_stream.close()
    finally:
        owner.lock_stream.close()


def test_real_host_spawn_without_admission_refuses_before_launch(monkeypatch, tmp_path):
    owner = supervisor(tmp_path)
    launches = []
    monkeypatch.setattr(remote.subprocess, "Popen", lambda *a, **kw: launches.append(kw))
    with pytest.raises(remote.SessionRefusal, match="admission"):
        owner._spawn("host", ["fake-host"], {})
    assert not launches
    assert not (owner.session_root / "host-control.log").exists()


def test_start_host_passes_exact_fd_to_normal_launcher(monkeypatch, tmp_path):
    owner = supervisor(tmp_path)
    owner.lock_stream = (tmp_path / "active.lock").open("a+b")
    owner.children["camera"] = object()
    commands = []
    child = SimpleNamespace(control_path=tmp_path / "host.log", log_path=None)
    owner._spawn = lambda name, command, env: commands.append(command) or child
    owner.save = lambda: None
    owner.emit = lambda *args, **kwargs: None
    replies = iter(["/logs/host.log", True])
    owner._wait_for = lambda *args: next(replies)
    try:
        owner.start_host()
        command = commands[0]
        assert command[:4] == ["bash", str(Path("/motor") / "tools/run_am1_host.sh"), "--mode", "local"]
        assert command[command.index("--am1-admission-fd") + 1] == str(owner.lock_stream.fileno())
        assert command[command.index("--am1-physical-state-directory") + 1] == str(tmp_path)
    finally:
        owner.lock_stream.close()


@pytest.mark.skipif(os.name == "nt", reason="actual inherited flock handoff requires the native Pi")
def test_actual_child_inherits_one_admission_fd_without_releasing_parent_lock(tmp_path):
    import fcntl

    owner = supervisor(tmp_path)
    owner.lock_stream = (tmp_path / "active.lock").open("a+b")
    fcntl.flock(owner.lock_stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    fd = owner.lock_stream.fileno()
    child_code = (
        "import fcntl,json,os,sys; fd=int(sys.argv[1]); "
        "fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB); "
        "print(json.dumps({'inode':os.fstat(fd).st_ino})); os.close(fd)"
    )
    try:
        child = owner._spawn("host", [sys.executable, "-c", child_code, str(fd)], dict(os.environ))
        assert child.process.wait(timeout=5) == 0
        child.control_stream.close()
        receipt = json.loads(child.control_path.read_text())
        assert receipt["inode"] == os.fstat(fd).st_ino
        assert not os.get_inheritable(fd)
        with (tmp_path / "active.lock").open("a+b") as competing, pytest.raises(BlockingIOError):
            fcntl.flock(competing.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        owner.lock_stream.close()


def test_parent_admission_refuses_durable_unknown_after_owner_lock_release(tmp_path):
    from examples.alohamini.am1_session_runtime import acquire_session_admission

    for name in ("physical-in-progress.json", "cleanup-uncertain.json"):
        marker = tmp_path / name
        marker.write_text("{}")
        with pytest.raises(RuntimeError, match="actual stopped reconciliation"):
            acquire_session_admission(tmp_path)
        marker.unlink()
    stream = acquire_session_admission(tmp_path)
    stream.close()
