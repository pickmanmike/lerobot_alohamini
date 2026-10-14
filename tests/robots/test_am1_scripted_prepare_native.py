"""Native opt-in preparation uses the established Local gates and motor-free boundaries."""

from __future__ import annotations

import json

import pytest
from test_alohamini_scripted_leader import LocalHarness, arguments, module

SHOULDER = "arm_left_shoulder_lift.pos"


def selected_args(m, tmp_path, profile="ArmSmoke"):
    duration = 420 if profile == "ArmSmokeRepeat" else 180
    return m.parse_args(
        arguments(tmp_path / "stop")
        + [
            "--motion_profile",
            profile,
            "--duration_s",
            str(duration),
        ],
        platform_name="Windows",
    )


def test_prepare_optin_refuses_body_profile_before_robot_connection(monkeypatch, tmp_path):
    monkeypatch.setenv("AM1_SCRIPTED_PREPARE", "1")
    m = module()
    with pytest.raises(SystemExit) as refused:
        m.parse_args(
            arguments(tmp_path / "stop")
            + [
                "--motion_profile",
                "ArmHoldBody",
                "--duration_s",
                "12",
            ],
            platform_name="Windows",
        )
    assert refused.value.code == 2


@pytest.mark.parametrize("profile", ["ArmSmoke", "ArmSmokeRepeat"])
def test_native_prepare_uses_current_alignment_and_defers_original_seed_to_handoff(
    monkeypatch,
    tmp_path,
    capsys,
    profile,
):
    monkeypatch.setenv("AM1_SCRIPTED_PREPARE", "1")
    m = module()
    harness = LocalHarness(monkeypatch, m, quit_after=15)
    harness.initial[SHOULDER] = harness.positions[SHOULDER] = 105.0
    monkeypatch.setattr(
        m, "run_startup_sync", lambda *a, **kw: pytest.fail("nominal sync entered preparation")
    )
    args = selected_args(m, tmp_path, profile)
    assert (
        m.run_teleoperation(args, input_fn=lambda _: "", monotonic=harness.clock, sleep_fn=harness.sleep) == 0
    )
    output = capsys.readouterr().out
    records = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    seeds = [row for row in records if row.get("event") == "am1_scripted_seed"]
    assert len(seeds) == 1
    assert seeds[0]["origin"][SHOULDER] == pytest.approx(99.0, abs=0.2)
    assert seeds[0]["observation_sequence"] > 2
    assert harness.worker.duration == (420 if profile == "ArmSmokeRepeat" else 180)
    summary = next(row for row in records if row.get("event") == "am1_scripted_input_summary")
    assert 0 < summary["trajectory_s"] < 15
    admitted = harness.events.index(("admitted",))
    inward = next(
        index
        for index, event in enumerate(harness.events)
        if event[0] == "worker_action" and event[1][SHOULDER] < 100
    )
    assert inward > admitted
    before_admission = [event[1] for event in harness.events[:admitted] if event[0] == "worker_action"]
    assert before_admission and all(action[SHOULDER] == 100 for action in before_admission)
    assert all(
        action[key] == 0
        for _, action in [event for event in harness.events if event[0] == "worker_action"]
        for key in m.make_zero_action()
    )
    assert harness.events.index(("private_socket_close_and_join",)) < harness.events.index(("disconnect",))


@pytest.mark.parametrize("selected_value,other_value", [(111.0, 12.0), (105.0, 101.0)])
def test_native_prepare_preserves_alignment_and_other_joint_range_refusals(
    monkeypatch,
    tmp_path,
    selected_value,
    other_value,
):
    monkeypatch.setenv("AM1_SCRIPTED_PREPARE", "1")
    m = module()
    harness = LocalHarness(monkeypatch, m)
    harness.initial[SHOULDER] = harness.positions[SHOULDER] = selected_value
    other = "arm_right_elbow_flex.pos"
    harness.initial[other] = harness.positions[other] = other_value
    assert harness.run(m, tmp_path) == 2
    assert harness.worker is None
    assert all(
        not set(m.AM1_ARM_POSITION_KEYS).intersection(event[1])
        for event in harness.events
        if event[0] == "outer_action"
    )


def test_normal_scripted_workflow_keeps_original_out_of_range_seed_refusal(monkeypatch, tmp_path):
    monkeypatch.delenv("AM1_SCRIPTED_PREPARE", raising=False)
    m = module()
    harness = LocalHarness(monkeypatch, m)
    harness.initial[SHOULDER] = harness.positions[SHOULDER] = 105.0
    assert harness.run(m, tmp_path) == 2
    assert harness.worker is None


def test_preparation_alignment_change_refuses_before_nominal_resynchronization(monkeypatch, tmp_path):
    monkeypatch.setenv("AM1_SCRIPTED_PREPARE", "1")
    m = module()
    harness = LocalHarness(monkeypatch, m)
    harness.initial[SHOULDER] = harness.positions[SHOULDER] = 105.0
    read = m.AlohaMiniClient.get_observation

    def changed_alignment(robot):
        if robot.observation_sequence >= 2:
            harness.positions[SHOULDER] = 111.0
        return read(robot)

    monkeypatch.setattr(m.AlohaMiniClient, "get_observation", changed_alignment)
    monkeypatch.setattr(
        m, "run_startup_sync", lambda *a, **kw: pytest.fail("preparation triggered nominal resynchronization")
    )
    assert (
        m.run_teleoperation(
            selected_args(m, tmp_path), input_fn=lambda _: "", monotonic=harness.clock, sleep_fn=harness.sleep
        )
        == 2
    )
    assert harness.worker is None


def test_real_sender_freezes_inward_preparation_on_console_pause_and_sends_body_zero():
    import time

    from test_alohamini_local_recovery import CONTROL, FEEDBACK, FreshReplyRobot, alohamini_host

    m = module()
    seed = {key: (50.0 if "gripper" in key else 12.0) for key in m.AM1_ARM_POSITION_KEYS}
    seed[SHOULDER] = 100.1
    positions, emitted, outgoing, paused_targets = dict(seed), [], [], []
    control = alohamini_host.AM1LocalControl()
    admitted_at = []
    provider = m.PreparedScriptedInput(
        seed,
        joint_keys=m.AM1_ARM_POSITION_KEYS,
        fps=10,
        provider=m.ScriptedLeaderInput,
        motion_profile="ArmSmoke",
        emit=emitted.append,
    )
    initial_target = {f"arm_{key}": value for key, value in provider.get_action().items()}

    class Host:
        def send_action(self, action):
            positions.update({key: action[key] for key in seed})

        def stop_motion(self):
            pass

        def hold_follower_arms(self):
            pass

    host = Host()

    class Command:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def send_action(self, action):
            outgoing.append((bool(admitted_at), dict(action)))
            control.apply(host, dict(action))

    class Robot(FreshReplyRobot):
        def __init__(self):
            self.started = time.monotonic()
            self.observation_sequence = 0
            self.latest_observation_received_at = self.started
            self.latest_observation_error = None
            self.latest_raw_observation_keys = frozenset(seed)
            self.latest_am1_local_feedback = {"version": 1, "state": "ready", "epoch": -1, "observation_id": 0}

        def make_live_command_sender(self):
            return Command()

        def retire_observation_requests(self):
            pass

        def get_observation(self):
            time.sleep(0.02)
            self.observation_sequence += 1
            self.latest_observation_received_at = time.monotonic()
            feedback = {}
            control.annotate(feedback)
            self.latest_am1_local_feedback = feedback[FEEDBACK]
            if control.state == "paused":
                paused_targets.append(dict(provider.get_action()))
            return dict(positions)

    robot = Robot()
    with pytest.raises(m.SafetyRefusal, match="Resume approval was not received"):
        m.run_am1_live_sender(
            robot,
            provider,
            initial_arm_target=initial_target,
            initial_observation_sequence=0,
            initial_follower_observed_at=robot.started,
            initial_follower_positions=seed,
            fps=10,
            duration_s=180,
            live_arm_scope="both",
            profile_cadence=False,
            body_action_supplier=m.make_zero_action,
            recovery_enabled=True,
            scripted_input=provider,
            announce_active=lambda: admitted_at.append(time.monotonic()),
            control_pause_requested=lambda: any(
                active and action.get(SHOULDER, 100) < 99.9 for active, action in outgoing
            ),
            manual_gate=lambda _: False,
        )
    assert admitted_at and provider.preparing and provider.elapsed_s == 0
    assert any(active and action.get(SHOULDER, 100) < 100 for active, action in outgoing)
    assert all(action.get(SHOULDER, 100) == 100 for active, action in outgoing if not active)
    assert len(paused_targets) >= 3 and all(target == paused_targets[0] for target in paused_targets)
    paused_actions = [action for _, action in outgoing if action[CONTROL]["mode"] == "pause"]
    assert paused_actions and all(
        action[key] == 0 for action in paused_actions for key in m.make_zero_action()
    )
    assert not any(row.get("event") == "am1_scripted_seed" for row in emitted)
    assert control.state == "paused"


@pytest.mark.parametrize(
    "selected_value,other_value,accepted",
    [
        (105.0, 12.0, True),
        (-105.0, 12.0, True),
        (120.1, 12.0, False),
        (105.0, 101.0, False),
    ],
)
def test_preparation_fresh_feedback_range_exception_is_only_the_selected_shoulder(
    selected_value,
    other_value,
    accepted,
):
    import time

    m = module()
    seed = {key: (50.0 if "gripper" in key else 12.0) for key in m.AM1_ARM_POSITION_KEYS}
    seed[SHOULDER] = 105.0
    provider = m.PreparedScriptedInput(
        seed,
        joint_keys=m.AM1_ARM_POSITION_KEYS,
        fps=10,
        provider=m.ScriptedLeaderInput,
        motion_profile="ArmSmoke",
        emit=lambda row: None,
    )
    measured = dict(seed)
    measured[SHOULDER] = selected_value
    measured["arm_right_elbow_flex.pos"] = other_value

    class Robot:
        observation_sequence = 0
        latest_observation_received_at = time.monotonic()
        latest_observation_error = None
        latest_raw_observation_keys = frozenset(seed)
        latest_observation_roundtrip_age_s = 0.001

        def get_observation(self):
            self.observation_sequence += 1
            self.latest_observation_received_at = time.monotonic()
            return dict(measured)

    if accepted:
        sample = m.read_fresh_am1_live_sample(
            Robot(), provider, previous_sequence=0, require_current_request=True
        )
        assert sample.follower_positions[SHOULDER] == selected_value
        assert sample.arm_target[SHOULDER] == 100.0
    else:
        with pytest.raises(m.SafetyRefusal):
            m.read_fresh_am1_live_sample(Robot(), provider, previous_sequence=0, require_current_request=True)


def test_preparation_sender_refuses_more_than_native_alignment_limit_before_worker(monkeypatch):
    import time

    m = module()
    seed = {key: (50.0 if "gripper" in key else 12.0) for key in m.AM1_ARM_POSITION_KEYS}
    seed[SHOULDER] = 111.0
    provider = m.PreparedScriptedInput(
        seed,
        joint_keys=m.AM1_ARM_POSITION_KEYS,
        fps=10,
        provider=m.ScriptedLeaderInput,
        motion_profile="ArmSmoke",
        emit=lambda row: None,
    )
    initial = {f"arm_{key}": value for key, value in provider.get_action().items()}

    class Robot:
        latest_am1_local_feedback = {"version": 1, "state": "ready", "epoch": -1, "observation_id": 1}
        latest_observation_roundtrip_age_s = 0.001

    monkeypatch.setattr(
        m, "AM1LiveActionSender", lambda *a, **kw: pytest.fail("sender constructed before alignment refusal")
    )
    with pytest.raises(m.SafetyRefusal, match="preparation.*native.*alignment"):
        m.run_am1_live_sender(
            Robot(),
            provider,
            initial_arm_target=initial,
            initial_observation_sequence=1,
            initial_follower_observed_at=time.monotonic(),
            initial_follower_positions=seed,
            fps=10,
            duration_s=180,
            live_arm_scope="both",
            profile_cadence=False,
            body_action_supplier=m.make_zero_action,
            recovery_enabled=True,
            scripted_input=provider,
        )
