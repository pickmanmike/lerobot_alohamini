#!/usr/bin/env python

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
POWERSHELL = shutil.which("pwsh")
requires_powershell = pytest.mark.skipif(POWERSHELL is None, reason="PowerShell 7 is required")


def load_session():
    spec = importlib.util.spec_from_file_location("test_scripted_am1_session", REPO_ROOT / "tools/am1_session.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def ps_literal(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def run_powershell(body: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", body],
        cwd=REPO_ROOT, text=True, capture_output=True, timeout=30, check=False,
    )


@requires_powershell
def test_scripted_local_preflight_never_resolves_physical_leaders(tmp_path):
    config = json.loads((REPO_ROOT / "config/am1.local.example.json").read_text())
    del config["leader_pnp_map_path"]
    del config["leader_calibration_sha256"]
    config_path = tmp_path / "local.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    result = run_powershell(f"""
. {ps_literal(REPO_ROOT / 'tools/run_am1.ps1')}
function Get-Am1RuntimeLeaderPorts {{ throw 'PHYSICAL_PORT_ENUMERATION_FORBIDDEN' }}
function Assert-Am1LeaderCalibrationHashes {{ throw 'LEADER_CALIBRATION_ACCESS_FORBIDDEN' }}
function Assert-Am1ReviewedWorktree {{ Write-Output 'REVIEWED_SOURCE_CHECKED' }}
function Assert-Am1ImportRoot {{ Write-Output 'IMPORT_ROOT_CHECKED' }}
Invoke-Am1Launch -Mode Local -ConfigPath {ps_literal(config_path)} -DurationSeconds 180 `
    -LeaderSource Scripted -MotionProfile ArmSmoke -Preflight
""")

    assert result.returncode == 0, result.stderr
    assert "REVIEWED_SOURCE_CHECKED" in result.stdout
    assert "IMPORT_ROOT_CHECKED" in result.stdout
    assert "AM1_LOCAL_PREFLIGHT_READY" in result.stdout
    assert "--leader_source scripted --motion_profile ArmSmoke" in result.stdout
    assert "--duration_s 180" in result.stdout
    assert "--local_mode" in result.stdout
    assert "--teleop." not in result.stdout
    assert "--no_leader" not in result.stdout


@requires_powershell
@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ("-Mode Local -LeaderSource Scripted", "requires -MotionProfile ArmSmoke"),
        ("-Mode Local -LeaderSource Physical -MotionProfile ArmSmoke", "only for Scripted"),
        ("-Mode Arms -LeaderSource Scripted -MotionProfile ArmSmoke", "only for Local"),
        ("-Mode Base -LeaderSource Scripted -MotionProfile ArmSmoke", "only for Local"),
        ("-Mode Lift -LeaderSource Scripted -MotionProfile ArmSmoke", "only for Local"),
    ],
)
def test_launcher_rejects_source_profile_mismatch_before_config_or_leader_access(tmp_path, arguments, message):
    result = run_powershell(f"""
. {ps_literal(REPO_ROOT / 'tools/run_am1.ps1')}
Invoke-Am1Launch {arguments} -ConfigPath {ps_literal(tmp_path / 'missing.json')} -Preflight
""")

    assert result.returncode != 0
    assert message in result.stderr
    assert "Local AM1 config is missing" not in result.stderr


@requires_powershell
def test_scripted_launch_requires_unified_session_stop_path_before_preflight(tmp_path):
    result = run_powershell(f"""
. {ps_literal(REPO_ROOT / 'tools/run_am1.ps1')}
Invoke-Am1Launch -Mode Local -LeaderSource Scripted -MotionProfile ArmSmoke `
    -ConfigPath {ps_literal(tmp_path / 'missing.json')}
""")

    assert result.returncode != 0
    assert "unified session" in result.stderr
    assert "Local AM1 config is missing" not in result.stderr


@requires_powershell
def test_scripted_command_retains_envelope_and_stop_contract():
    stop_path = REPO_ROOT / "test-scripted-stop"
    result = run_powershell(f"""
. {ps_literal(REPO_ROOT / 'tools/run_am1.ps1')}
$config = Get-Content -Raw {ps_literal(REPO_ROOT / 'config/am1.local.example.json')} | ConvertFrom-Json
$command = New-Am1WindowsCommand -Mode Local -Config $config -RepositoryRoot {ps_literal(REPO_ROOT)} `
    -LeaderSource Scripted -MotionProfile ArmSmoke -LocalDurationSeconds 180 -StopRequestPath {ps_literal(stop_path)}
$command | ConvertTo-Json -Depth 6 -Compress
""")

    assert result.returncode == 0, result.stderr
    arguments = json.loads(result.stdout)["arguments"]
    for name, expected in {
        "--leader_source": "scripted", "--motion_profile": "ArmSmoke",
        "--startup_sync_duration_s": "30", "--max_start_mismatch": "10",
        "--fps": "10", "--duration_s": "180", "--external_stop_file": str(stop_path),
    }.items():
        assert arguments[arguments.index(name) + 1] == expected
    assert "--unified_session_enter_confirmations" in arguments
    assert "--no_leader" not in arguments
    assert not any(value.startswith("--teleop.") for value in arguments)


@requires_powershell
def test_scripted_preflight_still_rejects_changed_follower_envelope(tmp_path):
    config = json.loads((REPO_ROOT / "config/am1.local.example.json").read_text())
    config["arm_settings"]["host_max_relative_target"] = 21
    config_path = tmp_path / "local.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    result = run_powershell(f"""
. {ps_literal(REPO_ROOT / 'tools/run_am1.ps1')}
function Get-Am1RuntimeLeaderPorts {{ throw 'PHYSICAL_PORT_ENUMERATION_FORBIDDEN' }}
function Assert-Am1LeaderCalibrationHashes {{ throw 'LEADER_CALIBRATION_ACCESS_FORBIDDEN' }}
function Assert-Am1ReviewedWorktree {{}}
Invoke-Am1Launch -Mode Local -ConfigPath {ps_literal(config_path)} -LeaderSource Scripted `
    -MotionProfile ArmSmoke -Preflight
""")

    assert result.returncode != 0
    assert "must retain the validated AM1" in result.stderr
    assert "PHYSICAL_PORT_ENUMERATION_FORBIDDEN" not in result.stderr


@requires_powershell
def test_default_physical_preflight_still_resolves_leaders_and_calibration(tmp_path):
    result = run_powershell(f"""
. {ps_literal(REPO_ROOT / 'tools/run_am1.ps1')}
function Get-Am1RuntimeLeaderPorts {{ return [pscustomobject]@{{ left = 'COM8'; right = 'COM7' }} }}
function Assert-Am1LeaderCalibrationHashes {{ Write-Output 'PHYSICAL_CALIBRATION_CHECKED' }}
function Assert-Am1ReviewedWorktree {{ Write-Output 'REVIEWED_SOURCE_CHECKED' }}
function Assert-Am1ImportRoot {{ Write-Output 'IMPORT_ROOT_CHECKED' }}
Invoke-Am1Launch -Mode Local -ConfigPath {ps_literal(REPO_ROOT / 'config/am1.local.example.json')} -Preflight
""")

    assert result.returncode == 0, result.stderr
    assert "PHYSICAL_CALIBRATION_CHECKED" in result.stdout
    assert "--teleop.left_port COM8 --teleop.right_port COM7" in result.stdout
    assert "--leader_source scripted" not in result.stdout
    assert "--motion_profile" not in result.stdout


@requires_powershell
@pytest.mark.parametrize("scripted", [False, True])
def test_session_powershell_passes_explicit_scripted_selection_to_python(tmp_path, scripted):
    shutil.copy2(REPO_ROOT / "tools/run_am1_session.ps1", tmp_path / "run_am1_session.ps1")
    (tmp_path / "am1_session.py").write_text(
        "import json, sys\nprint(json.dumps(sys.argv[1:]))\n", encoding="utf-8",
    )
    config = tmp_path / "session.json"
    config.write_text(json.dumps({"windows_python": sys.executable}), encoding="utf-8")
    command = [
        POWERSHELL, "-NoLogo", "-NoProfile", "-File", str(tmp_path / "run_am1_session.ps1"),
        "-DurationSeconds", "180", "-ConfigPath", str(config),
    ]
    if scripted:
        command.extend(["-LeaderSource", "Scripted", "-MotionProfile", "ArmSmoke"])
    result = subprocess.run(command, text=True, capture_output=True, timeout=30, check=False)

    assert result.returncode == 0, result.stderr
    arguments = json.loads(result.stdout)
    assert arguments[:5] == ["--config", str(config), "start", "--duration-seconds", "180"]
    assert arguments[5:] == (["--leader-source", "scripted", "--motion-profile", "ArmSmoke"] if scripted else [])


@requires_powershell
@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ("-LeaderSource Scripted", "requires -MotionProfile ArmSmoke"),
        ("-LeaderSource Physical -MotionProfile ArmSmoke", "only for Scripted"),
    ],
)
def test_session_powershell_refuses_profile_mismatch_before_config(tmp_path, arguments, message):
    result = run_powershell(f"""
& {ps_literal(REPO_ROOT / 'tools/run_am1_session.ps1')} -DurationSeconds 180 {arguments} `
    -ConfigPath {ps_literal(tmp_path / 'missing.json')}
""")

    assert result.returncode != 0
    assert message in result.stderr
    assert "Private AM1 session config is missing" not in result.stderr


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ([], {"leader_source": "physical", "motion_profile": None}),
        (["--leader-source", "scripted", "--motion-profile", "ArmSmoke"],
         {"leader_source": "scripted", "motion_profile": "ArmSmoke"}),
    ],
)
def test_session_python_cli_passes_source_and_profile_to_lifecycle(monkeypatch, arguments, expected):
    module = load_session()
    config = object()
    calls = []
    monkeypatch.setattr(module.SessionConfig, "load", lambda path: config)
    monkeypatch.setattr(module, "run_start", lambda *args, **kwargs: calls.append((args, kwargs)) or 17)

    assert module.main(["--config", "not-opened.json", "start", "--duration-seconds", "180", *arguments]) == 17
    assert calls == [((REPO_ROOT, config, 180), expected)]


@pytest.mark.parametrize(
    "arguments",
    [
        ["--leader-source", "scripted"],
        ["--leader-source", "physical", "--motion-profile", "ArmSmoke"],
        ["--leader-source", "scripted", "--motion-profile", "Other"],
    ],
)
def test_session_python_cli_refuses_profile_mismatch_before_reading_private_config(monkeypatch, arguments):
    module = load_session()
    monkeypatch.setattr(
        module.SessionConfig, "load",
        lambda path: (_ for _ in ()).throw(AssertionError("config must not be accessed")),
    )

    assert module.main(["--config", "not-opened.json", "start", "--duration-seconds", "180", *arguments]) == 2


@pytest.mark.parametrize("scripted", [False, True])
def test_python_local_preflight_propagates_scripted_selection_without_bypassing_source_check(
    monkeypatch, tmp_path, scripted,
):
    module = load_session()
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools/run_am1.ps1").write_text("unused", encoding="utf-8")
    local_config = tmp_path / "local.json"
    local_config.write_text("{}", encoding="utf-8")
    head = "a" * 40
    config = SimpleNamespace(
        windows_python=Path(sys.executable), local_config=local_config, remote_session_head=head,
        windows_log_directory=tmp_path / "logs", local_state_directory=tmp_path / "state",
    )
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if command[0] == "git":
            output = head if command[-2:] == ["rev-parse", "HEAD"] else ""
        else:
            output = "AM1_LOCAL_PREFLIGHT_READY"
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(module.shutil, "which", lambda name: "pwsh")
    selection = {"leader_source": "scripted", "motion_profile": "ArmSmoke"} if scripted else {}

    module.validate_local_preflight(tmp_path, config, 180, **selection)

    assert commands[:2] == [
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        ["git", "-C", str(tmp_path), "status", "--porcelain"],
    ]
    assert "-Preflight" in commands[2]
    assert commands[2][commands[2].index("-DurationSeconds") + 1] == "180"
    if scripted:
        assert commands[2][-4:] == ["-LeaderSource", "Scripted", "-MotionProfile", "ArmSmoke"]
    else:
        assert "-LeaderSource" not in commands[2]


@pytest.mark.parametrize("scripted", [False, True])
def test_windows_client_propagates_selection_to_real_local_launcher(monkeypatch, tmp_path, scripted):
    module = load_session()
    commands = []
    log = tmp_path / "client.log"
    log.write_text("AM1_CLIENT_EXIT_CODE=0\n", encoding="utf-8")

    class Process:
        pid = 1234

        def poll(self): return 0
        def wait(self, timeout): return 0

    monkeypatch.setattr(module.subprocess, "Popen", lambda command, **kwargs: commands.append(command) or Process())
    monkeypatch.setattr(module.shutil, "which", lambda name: "pwsh")
    config = SimpleNamespace(local_config=tmp_path / "local.json")
    selection = {"leader_source": "scripted", "motion_profile": "ArmSmoke"} if scripted else {}
    client = module.WindowsClient(REPO_ROOT, config, lambda: None, tmp_path / "stop", **selection)

    assert client.run(duration_seconds=180, log_path=log, stop_requested=lambda: False) == 0
    command = commands[0]
    assert command[command.index("-File") + 1] == str(REPO_ROOT / "tools/run_am1.ps1")
    assert command[command.index("-StopRequestPath") + 1] == str(tmp_path / "stop")
    if scripted:
        assert command[-4:] == ["-LeaderSource", "Scripted", "-MotionProfile", "ArmSmoke"]
    else:
        assert "-LeaderSource" not in command


def scripted_outcome(module, tmp_path, *, reason="script_complete", client_exit=0, remote_cleanup=None,
                     client_cleanup=None, remote_fault=None, summary_override=None, on_cleanup=None):
    summary = {
        "event": "am1_scripted_input_summary", "input_source": "scripted",
        "motion_profile": "ArmSmoke", "stop_reason": reason,
        "profile_complete": reason == "script_complete",
    }
    if summary_override is not None:
        summary = summary_override

    class Remote:
        def preflight(self):
            return {"session_source_head": "a" * 40, "motor_source_head": "b" * 40,
                    "camera_source_head": "c" * 40, "private_path": "/private/never-published"}

        def start_camera(self): return {"browser_url": "http://camera"}
        def start_host(self): return {}
        def stop(self):
            return remote_cleanup if remote_cleanup is not None else {
                "cleanup_verified": True, "host_exit": 0, "camera_exit": 0, "terminal_status": "complete",
            }

    class Client:
        def run(self, *, log_path, **kwargs):
            if reason is not None:
                log_path.write_text(json.dumps(summary) + "\n", encoding="utf-8")
            if remote_fault is not None:
                remote.fault = lambda: remote_fault
            return client_exit

        def cleanup_status(self):
            return client_cleanup if client_cleanup is not None else {
                "cleanup_verified": True, "wrapper_exit": 0, "client_exit": client_exit,
            }

    remote = Remote()
    return module.SessionCoordinator(
        remote=remote, client=Client(), open_browser=lambda url: None,
        collect_remote_log=lambda remote, local: (True, None), input_fn=lambda prompt: "",
        leader_source="scripted", motion_profile="ArmSmoke", on_cleanup=on_cleanup,
    ).run(
        duration_seconds=180, session_id="20260928T120000-1234abcd", session_directory=tmp_path,
        client_log_path=tmp_path / "client.log", stop_requested=lambda: False,
    )


@pytest.mark.parametrize(
    ("reason", "client_exit"),
    [("script_complete", 0), ("manual_q", 0), ("explicit_stop", 130),
     ("duration_expired", 0), ("fault", 2), ("keyboard_interrupt", 130)],
)
def test_scripted_session_preserves_distinct_stop_and_input_evidence(tmp_path, capsys, reason, client_exit):
    outcome = scripted_outcome(load_session(), tmp_path, reason=reason, client_exit=client_exit)

    assert outcome.input_source == "scripted"
    assert outcome.motion_profile == "ArmSmoke"
    assert outcome.stop_reason == reason
    assert outcome.scripted_input_summary["stop_reason"] == reason
    assert outcome.final_exit_code == client_exit
    assert outcome.sources == {
        "session_source_head": "a" * 40, "motor_source_head": "b" * 40, "camera_source_head": "c" * 40,
    }
    output = capsys.readouterr().out
    assert "SCRIPTED LEADER INPUT" in output
    assert "REAL FOLLOWER MOTION" in output
    assert "Physical leader controllers remain disconnected" in output
    persisted = json.loads((tmp_path / "session-summary.json").read_text())
    assert persisted["stop_reason"] == reason
    assert persisted["scripted_input_summary"]["input_source"] == "scripted"


@pytest.mark.parametrize(
    ("changes", "reason", "exit_code"),
    [
        ({"client_exit": 2}, "fault", 2),
        ({"remote_cleanup": {"cleanup_verified": False, "host_exit": 0, "camera_exit": 0}}, "cleanup_unknown", 3),
        ({"remote_cleanup": {"cleanup_verified": True, "host_exit": 7, "camera_exit": 0}}, "fault", 2),
        ({"remote_cleanup": {"cleanup_verified": True, "camera_exit": 0}}, "cleanup_unknown", 3),
        ({"client_cleanup": {"cleanup_verified": True, "wrapper_exit": 7, "client_exit": 0}}, "fault", 2),
        ({"client_cleanup": {"cleanup_verified": False, "client_exit": None}}, "cleanup_unknown", 3),
        ({"remote_fault": {"event": "runtime_fault", "reason": "real host fault"}}, "fault", 2),
        ({"reason": None}, "unknown", 2),
        ({"summary_override": {"event": "am1_scripted_input_summary", "input_source": "physical",
                               "motion_profile": "ArmSmoke", "stop_reason": "script_complete"}}, "unknown", 2),
    ],
)
def test_scripted_completion_never_masks_fault_or_unknown_cleanup(tmp_path, changes, reason, exit_code):
    outcome = scripted_outcome(load_session(), tmp_path, **changes)

    assert outcome.stop_reason == reason
    assert outcome.final_exit_code == exit_code
    assert outcome.stop_reason != "script_complete"
    if outcome.scripted_input_summary is not None:
        assert outcome.scripted_input_summary["stop_reason"] == "script_complete"


def test_scripted_completion_is_retracted_if_local_cleanup_recording_fails(tmp_path):
    def fail_cleanup(outcome):
        raise OSError("state storage failed")

    outcome = scripted_outcome(load_session(), tmp_path, on_cleanup=fail_cleanup)

    assert outcome.stop_reason == "cleanup_unknown"
    assert outcome.final_exit_code == 3
    assert outcome.cleanup_verified is False


@pytest.mark.parametrize("reason", ["explicit_stop", "keyboard_interrupt"])
@pytest.mark.parametrize("host_exit", [0, 7])
def test_scripted_cancellation_preserves_real_wrapper_130_without_hiding_host_failure(tmp_path, reason, host_exit):
    outcome = scripted_outcome(
        load_session(), tmp_path, reason=reason, client_exit=130,
        remote_cleanup={"cleanup_verified": host_exit == 0, "host_exit": host_exit, "camera_exit": 0},
        client_cleanup={"cleanup_verified": True, "wrapper_exit": 130, "client_exit": 130,
                        "stop_context": {"origin": reason}},
    )

    assert outcome.stop_reason == (reason if host_exit == 0 else "fault")
    assert outcome.final_exit_code == (130 if host_exit == 0 else 2)
    assert outcome.scripted_input_summary["stop_reason"] == reason


@pytest.mark.parametrize("profile_complete", [None, False, 0, "true"])
def test_script_complete_requires_explicit_boolean_profile_completion(tmp_path, profile_complete):
    summary = {
        "event": "am1_scripted_input_summary", "input_source": "scripted",
        "motion_profile": "ArmSmoke", "stop_reason": "script_complete",
    }
    if profile_complete is not None:
        summary["profile_complete"] = profile_complete
    outcome = scripted_outcome(load_session(), tmp_path, summary_override=summary)

    assert outcome.stop_reason == "unknown"
    assert outcome.final_exit_code == 2
    assert outcome.scripted_input_summary == summary


def test_real_session_start_preserves_scripted_selection_and_stop_reason_through_final_state(
    monkeypatch, tmp_path, capsys,
):
    module = load_session()
    state = tmp_path / "state"
    logs = tmp_path / "logs"
    state.mkdir()
    logs.mkdir()
    local_config = tmp_path / "local.json"
    local_config.write_text("{}", encoding="utf-8")
    head = "d" * 40
    config = SimpleNamespace(
        windows_python=Path(sys.executable), local_config=local_config, remote_session_head=head,
        windows_log_directory=logs, local_state_directory=state,
    )
    commands = []
    confirmations = []

    class Remote:
        def __init__(self, *args): pass
        def preflight(self): return {"session_source_head": head}
        def start_camera(self): return {"browser_url": "http://camera"}
        def start_host(self): return {}
        def fault(self): return None
        def stop(self): return {"cleanup_verified": True, "host_exit": 0, "camera_exit": 0}

    class Process:
        pid = 1234

        def poll(self): return 0
        def wait(self, timeout): return 0

    def popen(command, **kwargs):
        commands.append(command)
        log_path = Path(command[command.index("-LogPath") + 1])
        log_path.write_text(
            json.dumps({"event": "am1_scripted_input_summary", "input_source": "scripted",
                        "motion_profile": "ArmSmoke", "stop_reason": "manual_q"})
            + "\nAM1_CLIENT_EXIT_CODE=0\n", encoding="utf-8",
        )
        return Process()

    def run(command, **kwargs):
        commands.append(command)
        output = (head if command[-2:] == ["rev-parse", "HEAD"] else "") if command[0] == "git" else (
            "AM1_LOCAL_PREFLIGHT_READY"
        )
        return subprocess.CompletedProcess(command, 0, output, "")

    real_coordinator = module.SessionCoordinator

    def coordinator(**kwargs):
        return real_coordinator(**kwargs, input_fn=lambda prompt: confirmations.append(prompt) or "")

    monkeypatch.setattr(module, "SessionCoordinator", coordinator)
    monkeypatch.setattr(module, "SSHRemote", Remote)
    monkeypatch.setattr(module, "_open_browser", lambda url: None)
    monkeypatch.setattr(module.os, "startfile", lambda path: None, raising=False)
    monkeypatch.setattr(module.shutil, "which", lambda name: "pwsh")
    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(module.subprocess, "Popen", popen)

    assert module.run_start(REPO_ROOT, config, 180, leader_source="scripted", motion_profile="ArmSmoke") == 0

    assert len(confirmations) == 1
    launch_commands = [command for command in commands if "-LeaderSource" in command]
    assert len(launch_commands) == 2
    assert all(command[-4:] == ["-LeaderSource", "Scripted", "-MotionProfile", "ArmSmoke"]
               for command in launch_commands)
    active = json.loads((state / "active.json").read_text())
    assert active["input_source"] == "scripted"
    assert active["motion_profile"] == "ArmSmoke"
    assert active["stop_reason"] == "manual_q"
    summary = json.loads((Path(active["session_directory"]) / "session-summary.json").read_text())
    assert summary["sources"]["windows_source_head"] == head
    assert "AM1_SESSION_STOP_REASON=manual_q" in capsys.readouterr().out
