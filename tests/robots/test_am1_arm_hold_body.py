from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from test_alohamini_scripted_leader import LocalHarness, arguments, module, pose
from test_am1_scripted_launchers import (
    REPO_ROOT,
    load_session,
    ps_literal,
    requires_powershell,
    run_powershell,
)


def hold_arguments(tmp_path, *, duration=12):
    return arguments(tmp_path / "stop") + [
        "--motion_profile", "ArmHoldBody", "--duration_s", str(duration),
    ]


def test_hold_provider_keeps_original_measured_seed_and_discards_pause_time():
    m = module()
    records, seed = [], pose(m)
    provider = m.ArmHoldBodyInput(seed, joint_keys=m.AM1_ARM_POSITION_KEYS, fps=10, emit=records.append)
    expected = {key.removeprefix("arm_"): value for key, value in seed.items()}
    seed.update({key: value + 2 for key, value in seed.items()})
    provider.admit(0.0)
    provider.advance(0.1, pose(m), 1)
    provider.freeze()
    provider.advance(100.0, pose(m), 2)
    provider.admit(200.0)
    provider.advance(210.0, pose(m), 3)
    assert provider.elapsed_s == pytest.approx(0.2)
    assert provider.get_action() == expected
    for index in range(4, 200):
        provider.advance(210.0 + (index - 3) / 10, pose(m), index)
        assert provider.get_action() == expected
        if provider.complete:
            break
    assert provider.complete and provider.elapsed_s == 12
    provider.finish("script_complete")
    assert not any(row["event"] == "am1_scripted_segment_plan" for row in records)
    summary = next(row for row in records if row["event"] == "am1_scripted_input_summary")
    assert summary["motion_profile"] == "ArmHoldBody"
    assert summary["requested"] == pose(m)
    assert summary["trajectory_s"] == 12


def test_actual_entrypoint_allows_normal_body_mapping_only_with_held_arm_profile(
    monkeypatch, tmp_path, capsys,
):
    m = module()
    original_map = m.make_local_body_action
    raw_mapping = m.AlohaMiniClient._from_keyboard_to_base_action
    harness = LocalHarness(monkeypatch, m)
    monkeypatch.setattr(m, "make_local_body_action", original_map)
    robot_type, original_init = m.AlohaMiniClient, m.AlohaMiniClient.__init__

    def init(robot, config):
        original_init(robot, config)
        robot.teleop_keys = config.teleop_keys
        robot.speed_levels, robot.speed_index = [{"xy": 0.1, "theta": 30.0}], 0

    monkeypatch.setattr(robot_type, "__init__", init)
    monkeypatch.setattr(robot_type, "_from_keyboard_to_base_action", raw_mapping, raising=False)
    monkeypatch.setattr(m.KeyboardTeleop, "get_action", lambda _: {"w", "u"})
    factory = m.ArmHoldBodyInput

    def provider(*args, **kwargs):
        harness.provider = factory(*args, **kwargs)
        return harness.provider

    monkeypatch.setattr(m, "ArmHoldBodyInput", provider)
    args = m.parse_args(hold_arguments(tmp_path), platform_name="Windows")
    assert m.run_teleoperation(args, input_fn=lambda _: "", monotonic=harness.clock, sleep_fn=harness.sleep) == 0
    actions = [row[1] for row in harness.events if row[0] == "worker_action"]
    assert any(action["x.vel"] == 0.1 and action["lift_axis.vel"] == 200 for action in actions)
    assert all({key: action[key] for key in harness.initial} == harness.initial for action in actions)
    assert harness.worker.duration == 12
    assert harness.events.index(("private_socket_close_and_join",)) < harness.events.index(("disconnect",))
    outer = [row[1] for row in harness.events if row[0] == "outer_action"]
    assert outer[-1] == m.make_zero_action()
    assert "body/lift keys disabled" not in capsys.readouterr().out


def test_real_sender_outputs_hold_body_and_release_without_replaying_body_input():
    m, clock = module(), SimpleNamespace(now=0.0)
    provider = m.ArmHoldBodyInput(pose(m), joint_keys=m.AM1_ARM_POSITION_KEYS, fps=10, emit=lambda _: None)
    held = {f"arm_{key}": value for key, value in provider.get_action().items()}
    sent, body = [], m.AM1LiveBodyMailbox()
    body.publish({"x.vel": 0.1, "y.vel": 0, "theta.vel": 0, "lift_axis.vel": 200}, published_at=0.0)

    class Command:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def send_action(self, action):
            sent.append(dict(action))

    robot = SimpleNamespace(make_live_command_sender=Command)
    sender = None

    def sleep(duration):
        clock.now += duration
        sender.note_fresh_observation(clock.now)
        if len(sent) == 2:
            body.clear()
        if len(sent) == 3:
            sender.stop_after_clearing_body()

    sender = m.AM1LiveActionSender(
        robot, initial_action=m.make_am1_live_action(held), initial_observation_sequence=0,
        fps=10, duration_s=12, profile_cadence=False, recovery_enabled=True,
        body_mailbox=body, monotonic=lambda: clock.now, sleep_fn=sleep,
    )
    sender._live_started_at = 0.0
    sender.start()
    sender.join()
    assert sender.snapshot().error is None
    assert len(sent) == 3
    assert sent[0]["x.vel"] == sent[0]["lift_axis.vel"] == 0
    assert sent[1]["x.vel"] == 0.1 and sent[1]["lift_axis.vel"] == 200
    assert sent[2]["x.vel"] == sent[2]["lift_axis.vel"] == 0
    assert all({key: action[key] for key in held} == held for action in sent)


@pytest.mark.parametrize("duration", [0, 11, 13, 180])
def test_hold_body_profile_rejects_any_native_duration_other_than_twelve(tmp_path, duration):
    m = module()
    with pytest.raises(SystemExit):
        m.parse_args(hold_arguments(tmp_path, duration=duration), platform_name="Windows")


def test_python_session_selection_requires_explicit_scripted_twelve_second_profile():
    m = load_session()
    m.validate_leader_selection("scripted", "ArmHoldBody", 12)
    with pytest.raises(ValueError):
        m.validate_leader_selection("scripted", "ArmHoldBody", 13)
    with pytest.raises(ValueError):
        m.validate_leader_selection("physical", "ArmHoldBody", 12)


@requires_powershell
def test_powershell_hold_body_command_selects_existing_owned_local_path():
    result = run_powershell(f"""
. {ps_literal(REPO_ROOT / "tools/run_am1.ps1")}
$config = Get-Content -Raw {ps_literal(REPO_ROOT / "config/am1.local.example.json")} | ConvertFrom-Json
$command = New-Am1WindowsCommand -Mode Local -Config $config -RepositoryRoot {ps_literal(REPO_ROOT)} -LeaderSource Scripted -MotionProfile ArmHoldBody -LocalDurationSeconds 12 -StopRequestPath {ps_literal(REPO_ROOT / "test-arm-hold-stop")}
$command | ConvertTo-Json -Depth 6 -Compress
""")
    assert result.returncode == 0, result.stderr
    args = json.loads(result.stdout)["arguments"]
    assert args[args.index("--motion_profile") + 1] == "ArmHoldBody"
    assert args[args.index("--duration_s") + 1] == "12"
    assert "--local_mode" in args and "--unified_session_enter_confirmations" in args
    assert "--no_keyboard" not in args
    assert "--no_leader" not in args
    assert not any(value.startswith("--teleop.") for value in args)


@requires_powershell
def test_session_powershell_passes_exact_hold_selection_and_rejects_wrong_duration(tmp_path):
    import sys

    wrapper = tmp_path / "run_am1_session.ps1"
    wrapper.write_text((REPO_ROOT / "tools/run_am1_session.ps1").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "am1_session.py").write_text(
        "import json,sys\nprint(json.dumps(sys.argv[1:]))\n", encoding="utf-8",
    )
    config = tmp_path / "session.json"
    config.write_text(json.dumps({"windows_python": sys.executable}), encoding="utf-8")
    result = run_powershell(f"""
& {ps_literal(wrapper)} -DurationSeconds 12 -LeaderSource Scripted -MotionProfile ArmHoldBody -ConfigPath {ps_literal(config)}
""")
    assert result.returncode == 0, result.stderr
    args = json.loads(result.stdout)
    assert args[args.index("--duration-seconds") + 1] == "12"
    assert args[args.index("--leader-source") + 1] == "scripted"
    assert args[args.index("--motion-profile") + 1] == "ArmHoldBody"
    rejected = run_powershell(f"""
& {ps_literal(wrapper)} -DurationSeconds 13 -LeaderSource Scripted -MotionProfile ArmHoldBody -ConfigPath {ps_literal(tmp_path / "missing.json")}
""")
    assert rejected.returncode != 0
    assert "ArmHoldBody requires the finite" in rejected.stderr
    assert "Private AM1 session config is missing" not in rejected.stderr


@pytest.mark.parametrize("value,forward", [(None, False), ("0", False), ("1", True), ("true", False)])
def test_ssh_remote_forwards_only_explicit_left_shoulder_evidence_opt_in(monkeypatch, tmp_path, value, forward):
    m = load_session()
    if value is None:
        monkeypatch.delenv("AM1_LEFT_SHOULDER_EVIDENCE", raising=False)
    else:
        monkeypatch.setenv("AM1_LEFT_SHOULDER_EVIDENCE", value)
    monkeypatch.setenv("AM1_UNRELATED_TEST", "must-not-be-forwarded")
    config = SimpleNamespace(
        ssh_target="am1-test", remote_python="/motor/.venv/bin/python", remote_helper="/session/helper.py",
        remote_session_repository="/session", remote_session_head="a" * 40,
        remote_camera_repository="/camera", remote_camera_head="b" * 40, remote_camera_python="/camera/python",
        remote_motor_repository="/motor", remote_motor_head="c" * 40,
        remote_log_directory="/logs", remote_state_directory="/state",
    )
    trace = tmp_path / "ssh.trace"
    command = m.SSHRemote(config, "20261006T120000-test1234", tmp_path)._command(trace)
    assert command[:command.index(config.ssh_target)] == [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ConnectionAttempts=1",
        "-o", "ServerAliveInterval=2", "-o", "ServerAliveCountMax=3", "-v", "-E", str(trace),
    ]
    expected = [config.ssh_target]
    if forward:
        expected += ["env", "AM1_LEFT_SHOULDER_EVIDENCE=1"]
    expected += [
        config.remote_python, config.remote_helper, "supervise",
        "--session-id", "20261006T120000-test1234",
        "--session-repository", "/session", "--session-head", "a" * 40,
        "--camera-repository", "/camera", "--camera-head", "b" * 40, "--camera-python", "/camera/python",
        "--motor-repository", "/motor", "--motor-head", "c" * 40,
        "--log-directory", "/logs", "--state-directory", "/state",
    ]
    assert command[command.index(config.ssh_target):] == expected
