#!/usr/bin/env python

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "examples" / "alohamini" / "diagnose_am1_joint.py"
FOLLOWER_POSE = {
    "arm_left_shoulder_pan.pos": 0.0,
    "arm_left_shoulder_lift.pos": 10.0,
    "arm_left_elbow_flex.pos": 100.0,
    "arm_left_wrist_flex.pos": 30.0,
    "arm_left_wrist_roll.pos": 40.0,
    "arm_left_gripper.pos": 50.0,
    "arm_right_shoulder_pan.pos": 0.0,
    "arm_right_shoulder_lift.pos": -10.0,
    "arm_right_elbow_flex.pos": -20.0,
    "arm_right_wrist_flex.pos": -30.0,
    "arm_right_wrist_roll.pos": -40.0,
    "arm_right_gripper.pos": 50.0,
}
ZERO_BODY = {"x.vel": 0, "y.vel": 0, "theta.vel": 0, "lift_axis.vel": 0}


def load_diagnostic_module():
    assert SCRIPT_PATH.exists(), "the bounded AM1 joint diagnostic script is missing"
    module_name = f"test_diagnose_am1_joint_{id(object())}"
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
    return module


def test_joint_diagnostic_defaults_are_bounded_for_the_left_elbow():
    module = load_diagnostic_module()

    args = module.parse_args([])

    assert args.remote_ip == "127.0.0.1"
    assert args.robot_id == "my_alohamini"
    assert args.side == "left"
    assert args.joint == "elbow_flex"
    assert args.delta == -10.0
    assert args.fps == 5
    assert args.duration_s == 5.0
    assert args.max_final_error == 1.0


@pytest.mark.parametrize(
    ("argv", "reason"),
    [
        (["--delta", "0"], "--delta must be finite, nonzero, and no larger than 10.0"),
        (["--delta", "nan"], "--delta must be finite, nonzero, and no larger than 10.0"),
        (["--delta", "10.0001"], "--delta must be finite, nonzero, and no larger than 10.0"),
        (["--fps", "0"], "--fps must be between 1 and 10"),
        (["--fps", "11"], "--fps must be between 1 and 10"),
        (["--duration_s", "0.1"], "--duration_s must be finite and between 0.2 and 10.0"),
        (["--duration_s", "inf"], "--duration_s must be finite and between 0.2 and 10.0"),
        (
            ["--max_final_error", "0"],
            "--max_final_error must be finite, greater than zero, and no larger than 5.0",
        ),
        (
            ["--max_final_error", "5.1"],
            "--max_final_error must be finite, greater than zero, and no larger than 5.0",
        ),
    ],
)
def test_joint_diagnostic_rejects_unbounded_arguments(capsys, argv, reason):
    module = load_diagnostic_module()

    with pytest.raises(SystemExit):
        module.parse_args(argv)

    assert reason in capsys.readouterr().err


def test_joint_diagnostic_actions_hold_other_joints_and_bound_every_step():
    module = load_diagnostic_module()
    plan = module.build_joint_plan(
        FOLLOWER_POSE,
        side="left",
        joint="elbow_flex",
        delta=-10.0,
        duration_s=5.0,
        fps=5,
    )

    actions = [module.build_joint_action(plan, index) for index in range(plan.frame_count)]

    assert plan.selected_key == "arm_left_elbow_flex.pos"
    assert plan.start_value == 100.0
    assert plan.target_value == 90.0
    assert plan.total_steps == 25
    assert plan.frame_count == 26
    assert actions[0] == {**FOLLOWER_POSE, **ZERO_BODY}
    assert actions[-1] == {**FOLLOWER_POSE, "arm_left_elbow_flex.pos": 90.0, **ZERO_BODY}
    for previous, current in zip(actions, actions[1:]):
        assert abs(current[plan.selected_key] - previous[plan.selected_key]) <= 0.75
        assert {
            key: value for key, value in current.items() if key != plan.selected_key
        } == {
            key: value for key, value in actions[0].items() if key != plan.selected_key
        }


def test_joint_plan_accepts_legitimate_body_observation_fields_without_forwarding_them():
    module = load_diagnostic_module()
    observation = {
        **FOLLOWER_POSE,
        "x.vel": 0.0,
        "y.vel": 0.0,
        "theta.vel": 0.0,
        "lift_axis.height_mm": 0.0,
    }

    plan = module.build_joint_plan(
        observation,
        side="left",
        joint="elbow_flex",
        delta=-1.0,
        duration_s=0.2,
        fps=5,
    )
    action = module.build_joint_action(plan, 0)

    assert set(action) == set(FOLLOWER_POSE) | set(ZERO_BODY)
    assert "lift_axis.height_mm" not in action


@pytest.mark.parametrize(
    ("pose", "joint", "delta", "reason"),
    [
        (
            {key: value for key, value in FOLLOWER_POSE.items() if key != "arm_right_gripper.pos"},
            "elbow_flex",
            -10.0,
            "exactly the 12 AM1 arm position keys",
        ),
        (
            {**FOLLOWER_POSE, "arm_left_unexpected.pos": 0.0},
            "elbow_flex",
            -10.0,
            "exactly the 12 AM1 arm position keys",
        ),
        (
            {**FOLLOWER_POSE, "arm_left_elbow_flex.pos": float("nan")},
            "elbow_flex",
            -10.0,
            "must be finite",
        ),
        (
            {**FOLLOWER_POSE, "arm_left_elbow_flex.pos": 95.0},
            "elbow_flex",
            10.0,
            "target 105.0 is outside -100..100",
        ),
        (
            {**FOLLOWER_POSE, "arm_left_gripper.pos": 95.0},
            "gripper",
            10.0,
            "target 105.0 is outside 0..100",
        ),
    ],
)
def test_joint_plan_refuses_invalid_pose_or_out_of_range_target(pose, joint, delta, reason):
    module = load_diagnostic_module()

    with pytest.raises(module.SafetyRefusal, match=reason):
        module.build_joint_plan(
            pose,
            side="left",
            joint=joint,
            delta=delta,
            duration_s=5.0,
            fps=5,
        )


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, duration):
        self.sleeps.append(duration)
        self.now += duration


class FakeRobot:
    def __init__(self, observation_poses):
        self.config = SimpleNamespace(connect_timeout_s=1.0, observation_request_window=1)
        self.observation_sequence = 0
        self.observation_poses = [dict(pose) for pose in observation_poses]
        self.observation_index = 0
        self.events = []

    def get_observation(self):
        pose_index = min(self.observation_index, len(self.observation_poses) - 1)
        pose = dict(self.observation_poses[pose_index])
        self.observation_index += 1
        self.observation_sequence += 1
        self.events.append(("get_observation", self.observation_sequence, pose))
        return pose

    def send_action(self, action):
        action = dict(action)
        self.events.append(("send_action", action))
        return action


class FakeLifecycleRobot(FakeRobot):
    def __init__(self, observation_poses, *, fail_arm=False, fail_disconnect=False):
        super().__init__(observation_poses)
        self.fail_arm = fail_arm
        self.fail_disconnect = fail_disconnect

    def connect(self):
        self.events.append(("connect",))

    def send_action(self, action):
        action = dict(action)
        self.events.append(("send_action", action))
        if self.fail_arm and any(key.startswith("arm_") for key in action):
            raise RuntimeError("primary arm send failed")
        return action

    def disconnect(self):
        self.events.append(("disconnect",))
        if self.fail_disconnect:
            raise RuntimeError("cleanup disconnect failed")


@pytest.mark.parametrize("response", ["", "move", " MOVE", "MOVE "])
def test_joint_diagnostic_requires_exact_move_before_any_arm_action(response):
    module = load_diagnostic_module()
    robot = FakeRobot([FOLLOWER_POSE])
    clock = FakeClock()

    with pytest.raises(module.SafetyRefusal, match="type exactly MOVE"):
        module.run_joint_diagnostic(
            robot,
            side="left",
            joint="elbow_flex",
            delta=-1.0,
            duration_s=0.2,
            fps=5,
            max_final_error=1.0,
            input_fn=lambda _: response,
            monotonic=clock.monotonic,
            sleep_fn=clock.sleep,
        )

    assert [event for event in robot.events if event[0] == "send_action"] == []


def test_joint_diagnostic_uses_fresh_final_pose_and_reports_observed_outcome(capsys):
    module = load_diagnostic_module()
    preliminary = {**FOLLOWER_POSE, "arm_left_elbow_flex.pos": 75.0}
    measured_start = {**FOLLOWER_POSE, "arm_left_elbow_flex.pos": 100.0}
    halfway = {**measured_start, "arm_left_elbow_flex.pos": 99.5}
    reached = {**measured_start, "arm_left_elbow_flex.pos": 99.0}
    robot = FakeRobot([preliminary, measured_start, measured_start, halfway, reached, reached, reached])
    clock = FakeClock()

    def authorize(prompt):
        robot.events.append(("operator", prompt, "MOVE"))
        return "MOVE"

    result = module.run_joint_diagnostic(
        robot,
        side="left",
        joint="elbow_flex",
        delta=-1.0,
        duration_s=0.2,
        fps=5,
        max_final_error=1.0,
        input_fn=authorize,
        monotonic=clock.monotonic,
        sleep_fn=clock.sleep,
    )

    actions = [event[1] for event in robot.events if event[0] == "send_action"]
    output = capsys.readouterr().out
    operator_index = next(index for index, event in enumerate(robot.events) if event[0] == "operator")
    first_send_index = next(index for index, event in enumerate(robot.events) if event[0] == "send_action")
    assert operator_index < first_send_index
    assert actions[0] == {**measured_start, **ZERO_BODY}
    assert actions[-1] == {**measured_start, "arm_left_elbow_flex.pos": 99.0, **ZERO_BODY}
    assert all(set(action) == set(FOLLOWER_POSE) | set(ZERO_BODY) for action in actions)
    assert result.outcome == "PASS"
    assert result.observed_value == 99.0
    assert result.final_error == 0.0
    assert "Requested target: arm_left_elbow_flex.pos=99.000" in output
    assert "Host-accepted target: unavailable (the current action channel has no acknowledgement)" in output
    assert "Observed position: arm_left_elbow_flex.pos=99.000" in output
    assert "Outcome: PASS" in output


def test_joint_diagnostic_refuses_a_stale_observation_sequence_before_any_arm_action():
    module = load_diagnostic_module()
    clock = FakeClock()

    class StaleRobot(FakeRobot):
        def get_observation(self):
            clock.now += 0.6
            self.events.append(("get_observation", self.observation_sequence, FOLLOWER_POSE))
            return dict(FOLLOWER_POSE)

    robot = StaleRobot([FOLLOWER_POSE])

    with pytest.raises(module.SafetyRefusal, match="sequence-fresh"):
        module.run_joint_diagnostic(
            robot,
            side="left",
            joint="elbow_flex",
            delta=-1.0,
            duration_s=0.2,
            fps=5,
            max_final_error=1.0,
            input_fn=lambda _: "MOVE",
            monotonic=clock.monotonic,
            sleep_fn=clock.sleep,
        )

    assert [event for event in robot.events if event[0] == "send_action"] == []


@pytest.mark.parametrize(
    ("final_value", "expected_outcome"),
    [
        (50.0, "NO_MEASURABLE_MOVEMENT"),
        (51.0, "WRONG_DIRECTION"),
        (49.6, "INCOMPLETE"),
    ],
)
def test_joint_diagnostic_distinguishes_nonconvergence_outcomes(final_value, expected_outcome):
    module = load_diagnostic_module()
    starting_pose = {**FOLLOWER_POSE, "arm_left_elbow_flex.pos": 50.0}
    final_pose = {**starting_pose, "arm_left_elbow_flex.pos": final_value}
    robot = FakeRobot([starting_pose, starting_pose, final_pose])
    clock = FakeClock()

    result = module.run_joint_diagnostic(
        robot,
        side="left",
        joint="elbow_flex",
        delta=-1.0,
        duration_s=0.2,
        fps=5,
        max_final_error=0.1,
        input_fn=lambda _: "MOVE",
        monotonic=clock.monotonic,
        sleep_fn=clock.sleep,
    )

    assert result.outcome == expected_outcome


def test_diagnostic_lifecycle_refuses_without_move_and_still_zeros_and_disconnects(capsys):
    module = load_diagnostic_module()
    args = module.parse_args(["--delta", "-1", "--duration_s", "0.2"])
    robot = FakeLifecycleRobot([FOLLOWER_POSE])
    factory_args = []

    status = module.run_diagnostic(
        args,
        robot_factory=lambda received_args: factory_args.append(received_args) or robot,
        input_fn=lambda _: "move",
        monotonic=FakeClock().monotonic,
        sleep_fn=lambda _: None,
    )

    actions = [event[1] for event in robot.events if event[0] == "send_action"]
    assert status == 2
    assert factory_args == [args]
    assert robot.events[0] == ("connect",)
    assert actions == [ZERO_BODY, ZERO_BODY]
    assert robot.events[-1] == ("disconnect",)
    assert "SAFETY REFUSAL: operator did not type exactly MOVE; no arm action sent" in capsys.readouterr().out


def test_diagnostic_lifecycle_preserves_primary_failure_when_disconnect_cleanup_fails():
    module = load_diagnostic_module()
    args = module.parse_args(["--delta", "-1", "--duration_s", "0.2"])
    robot = FakeLifecycleRobot(
        [FOLLOWER_POSE, FOLLOWER_POSE],
        fail_arm=True,
        fail_disconnect=True,
    )
    clock = FakeClock()

    with pytest.raises(RuntimeError, match="primary arm send failed") as exc_info:
        module.run_diagnostic(
            args,
            robot_factory=lambda _: robot,
            input_fn=lambda _: "MOVE",
            monotonic=clock.monotonic,
            sleep_fn=clock.sleep,
        )

    assert any("cleanup disconnect" in note for note in exc_info.value.__notes__)
    assert robot.events[-1] == ("disconnect",)
    assert ("send_action", ZERO_BODY) in robot.events


def test_diagnostic_client_factory_is_am1_only_and_disables_camera_schema(monkeypatch):
    module = load_diagnostic_module()
    args = module.parse_args(["--robot.remote_ip", "192.0.2.10", "--robot.id", "diagnostic"])
    captured = {}

    class FakeConfig:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class FakeClient:
        def __init__(self, config):
            self.config = config

    import lerobot.robots.alohamini as alohamini_package

    monkeypatch.setattr(alohamini_package, "AlohaMiniClientConfig", FakeConfig)
    monkeypatch.setattr(alohamini_package, "AlohaMiniClient", FakeClient)

    robot = module._make_robot(args)

    assert isinstance(robot, FakeClient)
    assert captured == {
        "remote_ip": "192.0.2.10",
        "id": "diagnostic",
        "robot_model": "alohamini1",
        "cameras": {},
    }
