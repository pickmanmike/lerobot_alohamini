"""Opt-in tracking plumbing with disposable subprocesses; no SSH or hardware."""

import json
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tests.robots.test_am1_scripted_launchers import POWERSHELL, REPO_ROOT, requires_powershell
from tests.robots.test_am1_unified_session import load_tool


@requires_powershell
@pytest.mark.parametrize("enabled", [False, True])
def test_entrypoint_forwards_only_explicit_tracking_opt_in(tmp_path, enabled):
    shutil.copy2(REPO_ROOT / "tools/run_am1_session.ps1", tmp_path / "run_am1_session.ps1")
    (tmp_path / "am1_session.py").write_text("import json,sys; print(json.dumps(sys.argv[1:]))\n")
    config = tmp_path / "session.json"
    config.write_text(json.dumps({"windows_python": sys.executable}))
    args = [POWERSHELL, "-NoLogo", "-NoProfile", "-File", str(tmp_path / "run_am1_session.ps1"),
            "-DurationSeconds", "180", "-ConfigPath", str(config),
            "-LeaderSource", "Scripted", "-MotionProfile", "ArmSmoke"]
    if enabled:
        args.append("-ArmTrackingReadback")
    result = subprocess.run(args, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert ("--arm-tracking-readback" in json.loads(result.stdout)) is enabled


def test_cli_requires_scripted_mode_before_reading_config(monkeypatch):
    module = load_tool("am1_session")
    monkeypatch.setattr(module.SessionConfig, "load", lambda _: pytest.fail("config opened before refusal"))
    assert module.main(["--config", "missing.json", "start", "--duration-seconds", "180",
                        "--arm-tracking-readback"]) == 2


def test_cli_transmits_opt_in_to_lifecycle(monkeypatch):
    module = load_tool("am1_session")
    monkeypatch.setattr(module.SessionConfig, "load", lambda _: object())
    calls = []
    monkeypatch.setattr(module, "run_start", lambda *args, **kwargs: calls.append(kwargs) or 0)
    assert module.main(["--config", "missing.json", "start", "--duration-seconds", "180",
                        "--leader-source", "scripted", "--motion-profile", "ArmSmoke",
                        "--arm-tracking-readback"]) == 0
    assert calls == [{"leader_source": "scripted", "motion_profile": "ArmSmoke", "arm_tracking_readback": True}]


@pytest.mark.parametrize("enabled", [False, True])
def test_ssh_command_and_remote_host_preserve_explicit_opt_in(monkeypatch, tmp_path, enabled):
    module = load_tool("am1_session")
    config = module.SessionConfig.load(REPO_ROOT / "config/am1.session.example.json")
    remote = module.SSHRemote(config, "20260928T000000-1234abcd", tmp_path, arm_tracking_readback=enabled)
    command = remote._command()
    assert ("--arm-tracking-readback" in command) is enabled
    pi = load_tool("am1_session_remote")
    args = pi.build_parser().parse_args(command[command.index("supervise"):])
    args.state_directory = str(tmp_path / "state")
    supervisor = pi.RemoteSupervisor(args, pi.BestEffortReporter(lambda payload: None))
    supervisor.children["camera"] = object()
    launched = []
    def spawn(name, argv, env):
        launched.append((name, argv, env))
        return SimpleNamespace(control_path=tmp_path / "control", log_path=None)
    monkeypatch.setattr(supervisor, "_spawn", spawn)
    monkeypatch.setattr(supervisor, "_wait_for", lambda *a, **kw: str(tmp_path / "host.log"))
    monkeypatch.setattr(supervisor, "save", lambda: None)
    monkeypatch.setenv("AM1_ARM_TRACKING_READBACK", "1")  # Ambient env must not opt in ordinary use.
    monkeypatch.setenv("AM1_ARM_TRACKING_START", "right-elbow-request")
    supervisor.start_host()
    assert len(launched) == 1 and launched[0][0] == "host"
    assert launched[0][1][-2:] == ["--mode", "local"]
    assert launched[0][2]["AM1_ARM_TRACKING_READBACK"] == ("1" if enabled else "0")
    assert launched[0][2]["AM1_ARM_TRACKING_START"] == "immediate"


@requires_powershell
def test_entrypoint_forwards_explicit_deferred_selection(tmp_path):
    shutil.copy2(REPO_ROOT / "tools/run_am1_session.ps1", tmp_path / "run_am1_session.ps1")
    (tmp_path / "am1_session.py").write_text("import json,sys; print(json.dumps(sys.argv[1:]))\n")
    config = tmp_path / "session.json"
    config.write_text(json.dumps({"windows_python": sys.executable}))
    result = subprocess.run([
        POWERSHELL, "-NoLogo", "-NoProfile", "-File", str(tmp_path / "run_am1_session.ps1"),
        "-DurationSeconds", "180", "-ConfigPath", str(config), "-LeaderSource", "Scripted",
        "-MotionProfile", "ArmSmoke", "-ArmTrackingReadback", "-ArmTrackingStart", "RightElbowRequest",
    ], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    args = json.loads(result.stdout)
    assert args[args.index("--arm-tracking-start")+1] == "right-elbow-request"


def test_deferred_selection_reaches_actual_remote_spawn_environment(monkeypatch, tmp_path):
    module = load_tool("am1_session")
    config = module.SessionConfig.load(REPO_ROOT / "config/am1.session.example.json")
    remote = module.SSHRemote(config, "20260928T000000-1234abcd", tmp_path,
                              arm_tracking_readback=True, arm_tracking_start="right-elbow-request")
    command = remote._command()
    pi = load_tool("am1_session_remote")
    args = pi.build_parser().parse_args(command[command.index("supervise"):])
    args.state_directory = str(tmp_path / "state")
    supervisor = pi.RemoteSupervisor(args, pi.BestEffortReporter(lambda payload: None))
    supervisor.children["camera"] = object()
    launched = []
    def spawn(name, argv, env):
        launched.append(env)
        return SimpleNamespace(control_path=tmp_path / "control", log_path=None)
    monkeypatch.setattr(supervisor, "_spawn", spawn)
    monkeypatch.setattr(supervisor, "_wait_for", lambda *a, **kw: str(tmp_path / "host.log"))
    monkeypatch.setattr(supervisor, "save", lambda: None)
    supervisor.start_host()
    assert launched[0]["AM1_ARM_TRACKING_START"] == "right-elbow-request"


def test_cli_deferred_requires_opt_in_before_reading_config(monkeypatch):
    module = load_tool("am1_session")
    monkeypatch.setattr(module.SessionConfig, "load", lambda _: pytest.fail("config opened before refusal"))
    assert module.main(["--config", "missing.json", "start", "--duration-seconds", "180",
                        "--leader-source", "scripted", "--motion-profile", "ArmSmoke",
                        "--arm-tracking-start", "right-elbow-request"]) == 2


def test_cli_deferred_selection_reaches_lifecycle(monkeypatch):
    module = load_tool("am1_session")
    monkeypatch.setattr(module.SessionConfig, "load", lambda _: object())
    calls = []
    monkeypatch.setattr(module, "run_start", lambda *args, **kwargs: calls.append(kwargs) or 0)
    assert module.main(["--config", "missing.json", "start", "--duration-seconds", "180",
                        "--leader-source", "scripted", "--motion-profile", "ArmSmoke",
                        "--arm-tracking-readback", "--arm-tracking-start", "right-elbow-request"]) == 0
    assert calls[0]["arm_tracking_start"] == "right-elbow-request"
