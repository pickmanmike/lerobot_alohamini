#!/usr/bin/env python

# Copyright 2026 The HuggingFace Inc. team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import argparse
import importlib.util
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
ALOHAMINI_EXAMPLES = REPO_ROOT / "examples" / "alohamini"
LEADER_POSE = {
    "left_shoulder_pan.pos": 0.0,
    "left_shoulder_lift.pos": 10.0,
    "left_elbow_flex.pos": 20.0,
    "left_wrist_flex.pos": 30.0,
    "left_wrist_roll.pos": 40.0,
    "left_gripper.pos": 50.0,
    "right_shoulder_pan.pos": 0.0,
    "right_shoulder_lift.pos": -10.0,
    "right_elbow_flex.pos": -20.0,
    "right_wrist_flex.pos": -30.0,
    "right_wrist_roll.pos": -40.0,
    "right_gripper.pos": 50.0,
}
FOLLOWER_POSE = {f"arm_{key}": value for key, value in LEADER_POSE.items()}


def load_example_module(script_name: str):
    module_name = f"test_{script_name}_{id(object())}"
    spec = importlib.util.spec_from_file_location(
        module_name,
        ALOHAMINI_EXAMPLES / f"{script_name}.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ALOHAMINI_EXAMPLES))
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
        sys.path.remove(str(ALOHAMINI_EXAMPLES))
    return module


def required_args(script_name: str) -> list[str]:
    if script_name == "record_bi":
        return ["--dataset.repo_id", "test/windows_client"]
    return []


@pytest.mark.parametrize("script_name", ["calibrate_bi", "teleoperate_bi", "record_bi"])
def test_importing_client_script_does_not_parse_arguments(monkeypatch, script_name):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("argument parsing ran during import")

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", fail_if_called)

    load_example_module(script_name)


@pytest.mark.parametrize("script_name", ["calibrate_bi", "teleoperate_bi", "record_bi"])
def test_importing_client_script_does_not_import_visualization_helpers(monkeypatch, script_name):
    monkeypatch.delitem(sys.modules, "record_utils", raising=False)
    monkeypatch.delitem(sys.modules, "lerobot.utils.visualization_utils", raising=False)

    load_example_module(script_name)

    assert "lerobot.utils.visualization_utils" not in sys.modules


@pytest.mark.parametrize("script_name", ["calibrate_bi", "teleoperate_bi", "record_bi"])
def test_linux_leader_ports_keep_stable_alias_defaults(script_name):
    module = load_example_module(script_name)

    args = module.parse_args(required_args(script_name), platform_name="Linux")
    config = module.make_leader_config(args)

    assert config.left_arm_config.port == "/dev/am_arm_leader_left"
    assert config.right_arm_config.port == "/dev/am_arm_leader_right"


@pytest.mark.parametrize("script_name", ["calibrate_bi", "teleoperate_bi", "record_bi"])
def test_windows_leader_ports_are_passed_to_both_arm_configs_unchanged(script_name):
    module = load_example_module(script_name)
    args = module.parse_args(
        [
            *required_args(script_name),
            "--teleop.left_port",
            "COM5",
            "--right_port",
            "COM10",
        ],
        platform_name="Windows",
    )

    config = module.make_leader_config(args)

    assert config.left_arm_config.port == "COM5"
    assert config.right_arm_config.port == "COM10"


@pytest.mark.parametrize("script_name", ["calibrate_bi", "teleoperate_bi", "record_bi"])
def test_alohamini_leader_children_use_normalized_positions(script_name):
    module = load_example_module(script_name)
    args = module.parse_args(required_args(script_name), platform_name="Linux")

    config = module.make_leader_config(args)

    assert config.left_arm_config.use_degrees is False
    assert config.right_arm_config.use_degrees is False


@pytest.mark.parametrize("script_name", ["calibrate_bi", "teleoperate_bi", "record_bi"])
def test_windows_requires_both_explicit_leader_ports(capsys, script_name):
    module = load_example_module(script_name)

    with pytest.raises(SystemExit):
        module.parse_args(
            [*required_args(script_name), "--left_port", "COM5"],
            platform_name="Windows",
        )

    error_output = capsys.readouterr().err
    assert "Windows requires both leader ports" in error_output
    assert "--teleop.left_port COM5 --teleop.right_port COM6" in error_output


def test_teleoperation_alignment_threshold_defaults_to_ten():
    module = load_example_module("teleoperate_bi")

    args = module.parse_args([], platform_name="Linux")

    assert args.max_start_mismatch == 10.0
    assert args.check_alignment_only is False


def test_startup_sync_cli_defaults_preserve_strict_mode():
    module = load_example_module("teleoperate_bi")

    args = module.parse_args([], platform_name="Linux")

    assert args.startup_mode == "strict"
    assert args.startup_sync_duration_s == 12.0
    assert args.startup_sync_side == "both"
    assert args.startup_sync_only is False
    assert args.max_start_mismatch == 10.0


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "-inf"])
def test_startup_sync_duration_rejects_nonpositive_or_nonfinite(capsys, value):
    module = load_example_module("teleoperate_bi")

    with pytest.raises(SystemExit) as caught:
        module.parse_args([f"--startup_sync_duration_s={value}"], platform_name="Linux")

    assert caught.value.code == 2
    assert "--startup_sync_duration_s must be finite and greater than zero" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("argv", "reason"),
    [
        (["--startup_mode", "sync", "--robot_model", "alohamini2"], "sync is supported only for alohamini1"),
        (["--startup_mode", "sync", "--robot_model", "alohamini2pro"], "sync is supported only for alohamini1"),
        (["--startup_mode", "sync", "--no_robot"], "sync requires both robot and leader connections"),
        (["--startup_mode", "sync", "--no_leader"], "sync requires both robot and leader connections"),
        (["--startup_sync_only"], "--startup_sync_only requires --startup_mode sync"),
        (["--startup_sync_side", "left"], "left or right requires --startup_sync_only"),
        (["--startup_sync_side", "right"], "left or right requires --startup_sync_only"),
        (
            ["--startup_mode", "sync", "--startup_sync_side", "left"],
            "left or right requires --startup_sync_only",
        ),
        (
            ["--startup_mode", "sync", "--startup_sync_side", "right"],
            "left or right requires --startup_sync_only",
        ),
        (
            ["--startup_mode", "sync", "--check_alignment_only"],
            "--check_alignment_only is incompatible with --startup_mode sync",
        ),
    ],
)
def test_startup_sync_rejects_incompatible_arguments(capsys, argv, reason):
    module = load_example_module("teleoperate_bi")

    with pytest.raises(SystemExit) as caught:
        module.parse_args(argv, platform_name="Linux")

    assert caught.value.code == 2
    assert reason in capsys.readouterr().err


@pytest.mark.parametrize("side", ["left", "right"])
def test_startup_sync_allows_one_side_only_for_sync_only(side):
    module = load_example_module("teleoperate_bi")

    args = module.parse_args(
        [
            "--startup_mode",
            "sync",
            "--startup_sync_side",
            side,
            "--startup_sync_only",
            "--start_paused",
            "--duration_s",
            "30",
        ],
        platform_name="Linux",
    )

    assert args.startup_sync_side == side
    assert args.start_paused is True
    assert args.duration_s == 30.0


def test_startup_sync_allows_both_for_normal_teleoperation():
    module = load_example_module("teleoperate_bi")

    args = module.parse_args(["--startup_mode", "sync"], platform_name="Linux")

    assert args.startup_sync_side == "both"
    assert args.startup_sync_only is False


def test_startup_sync_help_explains_sync_only_ignored_options():
    module = load_example_module("teleoperate_bi")

    help_text = module.build_parser().format_help()
    sync_only_action = next(
        action for action in module.build_parser()._actions if action.dest == "startup_sync_only"
    )

    assert "--startup_mode {strict,sync}" in help_text
    assert "--startup_sync_duration_s" in help_text
    assert "--startup_sync_side {left,right,both}" in help_text
    assert "--startup_sync_only" in help_text
    assert "--start_paused has no effect" in sync_only_action.help
    assert "--duration_s is unused" in sync_only_action.help


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "-inf"])
def test_teleoperation_rejects_nonpositive_or_nonfinite_alignment_threshold(capsys, value):
    module = load_example_module("teleoperate_bi")

    with pytest.raises(SystemExit):
        module.parse_args([f"--max_start_mismatch={value}"], platform_name="Linux")

    assert "--max_start_mismatch must be finite and greater than zero" in capsys.readouterr().err


@pytest.mark.parametrize("disabled_connection", ["--no_robot", "--no_leader"])
def test_alignment_only_requires_robot_and_leader_connections(capsys, disabled_connection):
    module = load_example_module("teleoperate_bi")

    with pytest.raises(SystemExit):
        module.parse_args(["--check_alignment_only", disabled_connection], platform_name="Linux")

    assert "--check_alignment_only requires both robot and leader connections" in capsys.readouterr().err


def test_alignment_only_is_restricted_to_alohamini1(capsys):
    module = load_example_module("teleoperate_bi")

    with pytest.raises(SystemExit):
        module.parse_args(
            ["--check_alignment_only", "--robot_model", "alohamini2"],
            platform_name="Linux",
        )

    assert "--check_alignment_only is supported only for alohamini1" in capsys.readouterr().err


def test_record_no_rerun_disables_display_data():
    module = load_example_module("record_bi")

    args = module.parse_args(
        [
            "--dataset.repo_id",
            "test/windows_client",
            "--display_data",
            "true",
            "--no_rerun",
        ],
        platform_name="Linux",
    )

    assert not module.visualization_enabled(args)


def test_calibration_uses_passive_connections_and_disconnects_both_buses(monkeypatch):
    module = load_example_module("calibrate_bi")
    events = []

    class CalibrationArm:
        def __init__(self, name):
            self.name = name

        def connect(self, calibrate=True):
            events.append((self.name, "connect", calibrate))

        def disconnect(self):
            events.append((self.name, "disconnect"))

        def enable_torque(self):
            raise AssertionError("leader torque must remain disabled")

    class CalibrationLeader:
        def __init__(self, config):
            self.left_arm = CalibrationArm("left")
            self.right_arm = CalibrationArm("right")

        def calibrate(self):
            events.append(("leader", "calibrate"))

        def send_feedback(self, feedback):
            raise AssertionError("leader feedback must not be sent")

    monkeypatch.setattr(module, "BiSOLeader", CalibrationLeader)
    args = module.parse_args(
        ["--teleop.left_port", "COM5", "--teleop.right_port", "COM6"],
        platform_name="Windows",
    )

    module.run_calibration(args)

    assert events == [
        ("left", "connect", False),
        ("right", "connect", False),
        ("leader", "calibrate"),
        ("right", "disconnect"),
        ("left", "disconnect"),
    ]


def test_calibration_cleanup_does_not_hide_primary_failure(monkeypatch):
    module = load_example_module("calibrate_bi")
    primary_error = RuntimeError("calibration failed")

    class CalibrationArm:
        def __init__(self, *, disconnect_error=None):
            self.disconnect_error = disconnect_error

        def connect(self, calibrate=True):
            pass

        def disconnect(self):
            if self.disconnect_error is not None:
                raise self.disconnect_error

    class CalibrationLeader:
        def __init__(self, config):
            self.left_arm = CalibrationArm(disconnect_error=RuntimeError("cleanup failed"))
            self.right_arm = CalibrationArm()

        def calibrate(self):
            raise primary_error

    monkeypatch.setattr(module, "BiSOLeader", CalibrationLeader)
    args = module.parse_args(
        ["--teleop.left_port", "COM5", "--teleop.right_port", "COM6"],
        platform_name="Windows",
    )

    with pytest.raises(RuntimeError) as caught:
        module.run_calibration(args)

    assert caught.value is primary_error
    assert any("left leader disconnect" in note for note in caught.value.__notes__)


def test_am1_validation_rejects_out_of_range_joint_with_exact_identity():
    module = load_example_module("teleoperate_bi")
    values = {**LEADER_POSE, "right_shoulder_lift.pos": -105.8}

    with pytest.raises(
        module.SafetyRefusal,
        match=r"right shoulder_lift.*-105\.8.*-100\.\.100",
    ):
        module.extract_am1_arm_positions(values, source="leader", leader_sample=True)


@pytest.mark.parametrize(
    ("values", "reason"),
    [
        (
            {key: value for key, value in LEADER_POSE.items() if key != "left_wrist_roll.pos"},
            r"missing.*arm_left_wrist_roll\.pos",
        ),
        (
            {**LEADER_POSE, "arm_left_wrist_yaw.pos": 0.0},
            r"unexpected.*arm_left_wrist_yaw\.pos",
        ),
    ],
)
def test_am1_validation_rejects_missing_and_unexpected_arm_keys(values, reason):
    module = load_example_module("teleoperate_bi")

    with pytest.raises(module.SafetyRefusal, match=reason):
        module.extract_am1_arm_positions(values, source="leader", leader_sample=True)


def test_am1_validation_ignores_legitimate_zero_body_keys():
    module = load_example_module("teleoperate_bi")
    values = {
        **FOLLOWER_POSE,
        "x.vel": 0.0,
        "y.vel": 0.0,
        "theta.vel": 0.0,
        "lift_axis.vel": 0,
    }

    assert module.extract_am1_arm_positions(values, source="follower", leader_sample=False) == FOLLOWER_POSE


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_am1_validation_rejects_nonfinite_leader_values(value):
    module = load_example_module("teleoperate_bi")
    values = {**LEADER_POSE, "left_elbow_flex.pos": value}

    with pytest.raises(module.SafetyRefusal, match=r"left elbow_flex.*finite"):
        module.extract_am1_arm_positions(values, source="leader", leader_sample=True)


def test_am1_validation_accepts_normalized_boundaries_and_tolerance():
    module = load_example_module("teleoperate_bi")
    values = {
        **LEADER_POSE,
        "left_shoulder_pan.pos": -100.000001,
        "right_wrist_roll.pos": 100.000001,
        "left_gripper.pos": -0.000001,
        "right_gripper.pos": 100.000001,
    }

    assert module.extract_am1_arm_positions(values, source="leader", leader_sample=True) == {
        f"arm_{key}": float(value) for key, value in values.items()
    }


def test_am1_validation_rejects_values_beyond_numerical_tolerance():
    module = load_example_module("teleoperate_bi")
    values = {**LEADER_POSE, "left_gripper.pos": -0.000002}

    with pytest.raises(module.SafetyRefusal, match=r"left gripper.*0\.\.100"):
        module.extract_am1_arm_positions(values, source="leader", leader_sample=True)


def test_fresh_follower_observation_requires_sequence_increment():
    module = load_example_module("teleoperate_bi")
    stale_pose = {**FOLLOWER_POSE, "arm_left_shoulder_pan.pos": -1.0}
    fresh_pose = {**FOLLOWER_POSE, "arm_left_shoulder_pan.pos": 2.0}

    class SequencedRobot:
        observation_sequence = 7
        config = SimpleNamespace(connect_timeout_s=1.0)

        def __init__(self):
            self.calls = 0

        def get_observation(self):
            self.calls += 1
            if self.calls == 1:
                return stale_pose
            self.observation_sequence += 1
            return fresh_pose

    robot = SequencedRobot()

    assert module.get_fresh_follower_observation(robot) == fresh_pose
    assert robot.calls == 2


def test_alignment_rows_use_leader_minus_follower_difference():
    module = load_example_module("teleoperate_bi")
    leader_pose = {**FOLLOWER_POSE, "arm_right_elbow_flex.pos": -12.5}
    follower_pose = {**FOLLOWER_POSE, "arm_right_elbow_flex.pos": -20.0}

    rows = module.build_alignment_rows(follower_pose, leader_pose)
    row = next(row for row in rows if row.joint == "arm_right_elbow_flex.pos")

    assert row.follower_value == -20.0
    assert row.leader_value == -12.5
    assert row.signed_difference == 7.5
    assert row.absolute_difference == 7.5


def test_startup_sync_plan_extends_duration_for_step_limit():
    module = load_example_module("teleoperate_bi")
    target = {**FOLLOWER_POSE, "arm_left_shoulder_pan.pos": 3.0}

    plan = module.build_startup_sync_plan(
        FOLLOWER_POSE,
        target,
        side="both",
        requested_duration_s=0.2,
        fps=5,
    )

    assert plan.max_abs_delta == 3.0
    assert plan.total_steps == 4
    assert plan.frame_count == 5
    assert plan.largest_planned_per_frame_change == 0.75
    assert plan.estimated_actual_duration_s == 0.8


def test_startup_sync_plan_preserves_duration_for_zero_displacement():
    module = load_example_module("teleoperate_bi")

    plan = module.build_startup_sync_plan(
        FOLLOWER_POSE,
        FOLLOWER_POSE,
        side="both",
        requested_duration_s=12.0,
        fps=5,
    )

    assert plan.total_steps == 60
    assert plan.frame_count == 61
    assert plan.largest_planned_per_frame_change == 0.0
    assert plan.estimated_actual_duration_s == 12.0


def test_startup_sync_actions_have_exact_endpoints_bounded_steps_and_zero_body():
    module = load_example_module("teleoperate_bi")
    target = {**FOLLOWER_POSE, "arm_left_shoulder_pan.pos": 3.0}
    plan = module.build_startup_sync_plan(
        FOLLOWER_POSE,
        target,
        side="both",
        requested_duration_s=0.2,
        fps=5,
    )

    frames = [module.build_startup_sync_action(plan, index) for index in range(plan.frame_count)]

    assert frames[0]["arm_left_shoulder_pan.pos"] == FOLLOWER_POSE["arm_left_shoulder_pan.pos"]
    assert frames[-1]["arm_left_shoulder_pan.pos"] == target["arm_left_shoulder_pan.pos"]
    for previous, current in zip(frames, frames[1:]):
        for key in plan.selected_keys:
            assert abs(current[key] - previous[key]) <= module.STARTUP_SYNC_MAX_STEP
    for frame in frames:
        assert {key: frame[key] for key in module.make_zero_action()} == module.make_zero_action()


@pytest.mark.parametrize(
    ("side", "required_prefix", "forbidden_prefix"),
    [("left", "arm_left_", "arm_right_"), ("right", "arm_right_", "arm_left_")],
)
def test_startup_sync_action_omits_unselected_arm_keys(side, required_prefix, forbidden_prefix):
    module = load_example_module("teleoperate_bi")
    plan = module.build_startup_sync_plan(
        FOLLOWER_POSE,
        FOLLOWER_POSE,
        side=side,
        requested_duration_s=0.2,
        fps=5,
    )

    action = module.build_startup_sync_action(plan, 0)
    arm_keys = {key for key in action if key.startswith("arm_")}

    assert len(arm_keys) == 6
    assert all(key.startswith(required_prefix) for key in arm_keys)
    assert all(not key.startswith(forbidden_prefix) for key in arm_keys)


def test_startup_sync_plan_rejects_out_of_range_selected_follower_start():
    module = load_example_module("teleoperate_bi")
    invalid_start = {**FOLLOWER_POSE, "arm_left_gripper.pos": -0.1}

    with pytest.raises(module.SafetyRefusal, match=r"follower left gripper.*0\.\.100"):
        module.build_startup_sync_plan(
            invalid_start,
            FOLLOWER_POSE,
            side="left",
            requested_duration_s=0.2,
            fps=5,
        )


def test_startup_sync_plan_copies_and_freezes_both_endpoint_mappings():
    module = load_example_module("teleoperate_bi")
    start = dict(FOLLOWER_POSE)
    target = dict(FOLLOWER_POSE)
    plan = module.build_startup_sync_plan(
        start,
        target,
        side="both",
        requested_duration_s=0.2,
        fps=5,
    )
    start["arm_left_shoulder_pan.pos"] = 99.0
    target["arm_left_shoulder_pan.pos"] = 98.0

    assert plan.follower_start["arm_left_shoulder_pan.pos"] == 0.0
    assert plan.frozen_leader_target["arm_left_shoulder_pan.pos"] == 0.0
    with pytest.raises(TypeError):
        plan.frozen_leader_target["arm_left_shoulder_pan.pos"] = 1.0


class FakeClock:
    def __init__(self, events: list[tuple]):
        self.now = 0.0
        self.events = events
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        self.events.append(("clock", "monotonic", self.now))
        return self.now

    def sleep(self, duration_s: float) -> None:
        assert duration_s >= 0
        self.sleeps.append(duration_s)
        self.events.append(("clock", "sleep", duration_s))
        self.now += duration_s

    def advance(self, duration_s: float) -> None:
        self.now += duration_s
        self.events.append(("clock", "advance", duration_s))


def arm_send_actions(events: list[tuple]) -> list[dict[str, float | int]]:
    return [
        event[2]
        for event in events
        if event[:2] == ("robot", "send") and any(key.startswith("arm_") for key in event[2])
    ]


class FakeRobot:
    instances = []
    events: list[tuple]
    observation_poses: list[dict[str, float]]
    observation_sequence_advances: list[bool]

    def __init__(self, config):
        self.config = SimpleNamespace(
            remote_ip=config.remote_ip,
            robot_model=config.robot_model,
            teleop_keys={"quit": "q"},
            connect_timeout_s=1.0,
        )
        self.is_connected = False
        self.actions: list[dict[str, float]] = []
        self.events = type(self).events
        self.observation_sequence = 0
        self.observation_index = 0
        type(self).instances.append(self)

    def connect(self):
        self.events.append(("robot", "connect"))
        self.is_connected = True

    def get_observation(self):
        pose_index = min(self.observation_index, len(type(self).observation_poses) - 1)
        observation = dict(type(self).observation_poses[pose_index])
        advances = type(self).observation_sequence_advances
        should_advance = advances[min(self.observation_index, len(advances) - 1)] if advances else True
        self.observation_index += 1
        if should_advance:
            self.observation_sequence += 1
        self.events.append(("robot", "get_observation", self.observation_sequence, observation))
        return observation

    def send_action(self, action):
        action_copy = dict(action)
        self.actions.append(action_copy)
        self.events.append(("robot", "send", action_copy))
        return action_copy

    def _from_keyboard_to_base_action(self, pressed_keys):
        return {"x.vel": 1.0, "y.vel": 0.0, "theta.vel": 0.0}

    def _from_keyboard_to_lift_action(self, pressed_keys):
        return {"lift_axis.vel": 0}

    def disconnect(self):
        self.events.append(("robot", "disconnect"))
        self.is_connected = False


class FakeArm:
    def __init__(self, name: str, events: list[tuple], *, connect_error: BaseException | None = None):
        self.name = name
        self.events = events
        self.connect_error = connect_error
        self.disconnect_error: BaseException | None = None
        self.is_connected = False

    def connect(self, calibrate: bool = True):
        self.events.append((self.name, "connect", calibrate))
        if self.connect_error is not None:
            raise self.connect_error
        self.is_connected = True

    def disconnect(self):
        self.events.append((self.name, "disconnect"))
        self.is_connected = False
        if self.disconnect_error is not None:
            raise self.disconnect_error

    def enable_torque(self):
        raise AssertionError("leader torque must remain disabled")


class FakeLeader:
    instances = []
    events: list[tuple]
    right_connect_error: BaseException | None = None
    left_disconnect_error: BaseException | None = None
    action_poses: list[dict[str, float] | BaseException]

    def __init__(self, config):
        self.config = config
        self.events = type(self).events
        self.action_index = 0
        self.left_arm = FakeArm("left", self.events)
        self.right_arm = FakeArm(
            "right",
            self.events,
            connect_error=type(self).right_connect_error,
        )
        self.left_arm.disconnect_error = type(self).left_disconnect_error
        type(self).instances.append(self)

    def get_action(self):
        pose_index = min(self.action_index, len(type(self).action_poses) - 1)
        queued = type(self).action_poses[pose_index]
        self.action_index += 1
        if isinstance(queued, BaseException):
            self.events.append(("leader", "get_action", queued))
            raise queued
        action = dict(queued)
        self.events.append(("leader", "get_action", action))
        return action

    def send_feedback(self, feedback):
        raise AssertionError("leader feedback must not be sent")


def prepare_teleoperation(
    monkeypatch,
    module,
    *,
    right_connect_error=None,
    left_disconnect_error=None,
    observation_poses=None,
    action_poses=None,
    observation_sequence_advances=None,
):
    events: list[tuple] = []
    FakeRobot.instances = []
    FakeRobot.events = events
    FakeRobot.observation_poses = list(observation_poses or [FOLLOWER_POSE])
    FakeRobot.observation_sequence_advances = list(observation_sequence_advances or [True])
    FakeLeader.instances = []
    FakeLeader.events = events
    FakeLeader.right_connect_error = right_connect_error
    FakeLeader.left_disconnect_error = left_disconnect_error
    FakeLeader.action_poses = list(action_poses or [LEADER_POSE])
    monkeypatch.setattr(module, "AlohaMiniClient", FakeRobot)
    monkeypatch.setattr(module, "BiSOLeader", FakeLeader)
    monkeypatch.setattr(
        module,
        "KeyboardTeleop",
        lambda config: (_ for _ in ()).throw(AssertionError("keyboard was constructed")),
    )
    monkeypatch.setattr(
        module,
        "load_rerun_functions",
        lambda: (_ for _ in ()).throw(AssertionError("Rerun helpers were loaded")),
    )
    return events


def make_direct_sync_fakes(
    monkeypatch,
    module,
    *,
    observation_poses,
    action_poses,
    observation_sequence_advances=None,
):
    events = prepare_teleoperation(
        monkeypatch,
        module,
        observation_poses=observation_poses,
        action_poses=action_poses,
        observation_sequence_advances=observation_sequence_advances,
    )
    robot = FakeRobot(SimpleNamespace(remote_ip="127.0.0.1", robot_model="alohamini1"))
    leader = FakeLeader(SimpleNamespace())
    return robot, leader, events


@pytest.mark.parametrize("response", ["", "sync", " SYNC", "SYNC "])
def test_sync_requires_exact_confirmation_before_any_arm_send(monkeypatch, response):
    module = load_example_module("teleoperate_bi")
    robot, leader, events = make_direct_sync_fakes(
        monkeypatch,
        module,
        observation_poses=[FOLLOWER_POSE],
        action_poses=[LEADER_POSE],
    )
    clock = FakeClock(events)

    def refuse_confirmation(prompt):
        assert "SYNC" in prompt
        assert arm_send_actions(events) == []
        return response

    with pytest.raises(module.SafetyRefusal, match="type exactly SYNC"):
        module.run_startup_sync(
            robot,
            leader,
            side="both",
            requested_duration_s=0.2,
            fps=5,
            max_start_mismatch=10.0,
            input_fn=refuse_confirmation,
            monotonic=clock.monotonic,
            sleep_fn=clock.sleep,
        )

    assert arm_send_actions(events) == []
    assert sum(event[:2] == ("leader", "get_action") for event in events) == 1


def test_sync_uses_post_confirmation_start_and_frozen_target_for_bounded_payloads(monkeypatch, capsys):
    module = load_example_module("teleoperate_bi")
    initial_follower = {**FOLLOWER_POSE, "arm_left_shoulder_pan.pos": -5.0}
    stale_after_confirmation = {**FOLLOWER_POSE, "arm_left_shoulder_pan.pos": -4.0}
    measured_start = {**FOLLOWER_POSE, "arm_left_shoulder_pan.pos": 0.0}
    initial_leader = {**LEADER_POSE, "left_shoulder_pan.pos": -5.0}
    frozen_leader = {**LEADER_POSE, "left_shoulder_pan.pos": 1.5}
    final_follower = {f"arm_{key}": value for key, value in frozen_leader.items()}
    robot, leader, events = make_direct_sync_fakes(
        monkeypatch,
        module,
        observation_poses=[initial_follower, stale_after_confirmation, measured_start, final_follower],
        action_poses=[
            initial_leader,
            frozen_leader,
            frozen_leader,
            {**frozen_leader, "left_shoulder_pan.pos": 2.0},
            {**frozen_leader, "left_shoulder_pan.pos": 2.5},
        ],
        observation_sequence_advances=[True, False, True, True],
    )
    clock = FakeClock(events)

    def confirm(prompt):
        assert arm_send_actions(events) == []
        events.append(("operator", "confirmation", "SYNC"))
        return "SYNC"

    frozen_target, final_observation = module.run_startup_sync(
        robot,
        leader,
        side="both",
        requested_duration_s=0.2,
        fps=5,
        max_start_mismatch=10.0,
        input_fn=confirm,
        monotonic=clock.monotonic,
        sleep_fn=clock.sleep,
    )

    sends = arm_send_actions(events)
    output = capsys.readouterr().out
    assert len(sends) == 3
    assert "Preliminary AM1 startup synchronization plan" in output
    assert "Final frozen-target AM1 startup synchronization plan" in output
    assert "Selected side: both" in output
    assert "Requested minimum duration: 0.200s" in output
    assert "Planned frames: 3" in output
    assert "not collision-aware" in output
    assert "motor disconnect" in output
    assert sends[0]["arm_left_shoulder_pan.pos"] == 0.0
    assert sends[-1]["arm_left_shoulder_pan.pos"] == 1.5
    assert frozen_target["arm_left_shoulder_pan.pos"] == 1.5
    assert final_observation == final_follower
    for previous, current in zip(sends, sends[1:]):
        for key in module.AM1_ARM_POSITION_KEYS:
            assert abs(current[key] - previous[key]) <= module.STARTUP_SYNC_MAX_STEP
    for action in sends:
        assert {key: action[key] for key in module.make_zero_action()} == module.make_zero_action()
    for index, event in enumerate(events):
        if event[:2] == ("robot", "send") and any(key.startswith("arm_") for key in event[2]):
            assert events[index - 1][:2] == ("leader", "get_action")
    assert clock.sleeps == pytest.approx([0.2, 0.2])


@pytest.mark.parametrize(
    ("bad_pose", "reason"),
    [
        ({key: value for key, value in LEADER_POSE.items() if key != "right_wrist_roll.pos"}, "missing"),
        ({**LEADER_POSE, "right_wrist_yaw.pos": 0.0}, "unexpected"),
        ({**LEADER_POSE, "right_elbow_flex.pos": "bad"}, "must be numeric"),
        ({**LEADER_POSE, "right_shoulder_lift.pos": math.inf}, "must be finite"),
        ({**LEADER_POSE, "right_wrist_flex.pos": 100.1}, "outside expected -100..100"),
    ],
)
def test_sync_invalid_unselected_leader_sample_aborts_before_affected_frame(
    monkeypatch,
    bad_pose,
    reason,
):
    module = load_example_module("teleoperate_bi")
    robot, leader, events = make_direct_sync_fakes(
        monkeypatch,
        module,
        observation_poses=[FOLLOWER_POSE, FOLLOWER_POSE],
        action_poses=[LEADER_POSE, LEADER_POSE, bad_pose],
    )
    clock = FakeClock(events)

    with pytest.raises(module.SafetyRefusal, match=reason):
        module.run_startup_sync(
            robot,
            leader,
            side="left",
            requested_duration_s=0.2,
            fps=5,
            max_start_mismatch=10.0,
            input_fn=lambda _: "SYNC",
            monotonic=clock.monotonic,
            sleep_fn=clock.sleep,
        )

    assert arm_send_actions(events) == []


def test_sync_selected_leader_drift_aborts_before_affected_frame(monkeypatch):
    module = load_example_module("teleoperate_bi")
    drifted = {**LEADER_POSE, "left_shoulder_pan.pos": 2.000001}
    robot, leader, events = make_direct_sync_fakes(
        monkeypatch,
        module,
        observation_poses=[FOLLOWER_POSE, FOLLOWER_POSE],
        action_poses=[LEADER_POSE, LEADER_POSE, LEADER_POSE, drifted],
    )
    clock = FakeClock(events)

    with pytest.raises(
        module.SafetyRefusal,
        match=r"left shoulder_pan.*frozen=0\.0.*current=2\.000001.*drift=2\.000001.*2\.0",
    ):
        module.run_startup_sync(
            robot,
            leader,
            side="both",
            requested_duration_s=0.2,
            fps=5,
            max_start_mismatch=10.0,
            input_fn=lambda _: "SYNC",
            monotonic=clock.monotonic,
            sleep_fn=clock.sleep,
        )

    assert len(arm_send_actions(events)) == 1


def test_sync_selected_leader_drift_equal_to_limit_is_allowed(monkeypatch):
    module = load_example_module("teleoperate_bi")
    boundary = {**LEADER_POSE, "left_shoulder_pan.pos": 2.0}
    robot, leader, events = make_direct_sync_fakes(
        monkeypatch,
        module,
        observation_poses=[FOLLOWER_POSE, FOLLOWER_POSE, FOLLOWER_POSE],
        action_poses=[LEADER_POSE, LEADER_POSE, LEADER_POSE, boundary],
    )
    clock = FakeClock(events)

    module.run_startup_sync(
        robot,
        leader,
        side="both",
        requested_duration_s=0.2,
        fps=5,
        max_start_mismatch=10.0,
        input_fn=lambda _: "SYNC",
        monotonic=clock.monotonic,
        sleep_fn=clock.sleep,
    )

    assert len(arm_send_actions(events)) == 2


def test_sync_final_selected_mismatch_refuses_after_printing_full_table(monkeypatch, capsys):
    module = load_example_module("teleoperate_bi")
    mismatched = {**FOLLOWER_POSE, "arm_left_shoulder_pan.pos": 10.1}
    robot, leader, events = make_direct_sync_fakes(
        monkeypatch,
        module,
        observation_poses=[FOLLOWER_POSE, FOLLOWER_POSE, mismatched],
        action_poses=[LEADER_POSE, LEADER_POSE, LEADER_POSE, LEADER_POSE],
    )
    clock = FakeClock(events)

    with pytest.raises(module.SafetyRefusal, match=r"arm_left_shoulder_pan\.pos.*10\.1.*10\.0"):
        module.run_startup_sync(
            robot,
            leader,
            side="both",
            requested_duration_s=0.2,
            fps=5,
            max_start_mismatch=10.0,
            input_fn=lambda _: "SYNC",
            monotonic=clock.monotonic,
            sleep_fn=clock.sleep,
        )

    output = capsys.readouterr().out
    assert "follower value" in output
    assert "signed difference" in output
    assert len(arm_send_actions(events)) == 2


@pytest.mark.parametrize(
    ("side", "unselected_follower_key", "unselected_leader_key", "forbidden_prefix"),
    [
        ("left", "arm_right_shoulder_pan.pos", "right_shoulder_pan.pos", "arm_right_"),
        ("right", "arm_left_shoulder_pan.pos", "left_shoulder_pan.pos", "arm_left_"),
    ],
)
def test_sync_one_side_ignores_unselected_drift_and_final_mismatch_but_prints_it(
    monkeypatch,
    capsys,
    side,
    unselected_follower_key,
    unselected_leader_key,
    forbidden_prefix,
):
    module = load_example_module("teleoperate_bi")
    unselected_mismatch = {**FOLLOWER_POSE, unselected_follower_key: 20.1}
    unselected_drift = {**LEADER_POSE, unselected_leader_key: 3.0}
    robot, leader, events = make_direct_sync_fakes(
        monkeypatch,
        module,
        observation_poses=[FOLLOWER_POSE, FOLLOWER_POSE, unselected_mismatch],
        action_poses=[LEADER_POSE, LEADER_POSE, LEADER_POSE, unselected_drift],
    )
    clock = FakeClock(events)

    frozen_target, _ = module.run_startup_sync(
        robot,
        leader,
        side=side,
        requested_duration_s=0.2,
        fps=5,
        max_start_mismatch=10.0,
        input_fn=lambda _: "SYNC",
        monotonic=clock.monotonic,
        sleep_fn=clock.sleep,
    )

    assert frozen_target == FOLLOWER_POSE
    assert unselected_follower_key in capsys.readouterr().out
    assert all(
        not any(key.startswith(forbidden_prefix) for key in action)
        for action in arm_send_actions(events)
    )


def teleoperation_args(module, *extra_args):
    return module.parse_args(
        [
            "--teleop.left_port",
            "COM5",
            "--teleop.right_port",
            "COM6",
            "--no_keyboard",
            "--no_rerun",
            *extra_args,
        ],
        platform_name="Windows",
    )


def sync_args(module, *extra_args):
    return teleoperation_args(
        module,
        "--startup_mode",
        "sync",
        "--startup_sync_duration_s",
        "0.2",
        "--fps",
        "5",
        *extra_args,
    )


@pytest.mark.parametrize("robot_model", ["alohamini1", "alohamini2", "alohamini2pro"])
def test_strict_mode_never_calls_startup_sync(monkeypatch, robot_model):
    module = load_example_module("teleoperate_bi")
    events = prepare_teleoperation(monkeypatch, module)
    monkeypatch.setattr(
        module,
        "run_startup_sync",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("strict entered sync")),
    )
    args = teleoperation_args(
        module,
        "--startup_mode",
        "strict",
        "--robot_model",
        robot_model,
        "--duration_s",
        "0.2",
        "--fps",
        "5",
    )
    clock = FakeClock(events)

    assert module.run_teleoperation(args, monotonic=clock.monotonic, sleep_fn=clock.sleep) == 0
    assert arm_send_actions(events)


def test_startup_sync_only_skips_optional_resources_and_control_loop(monkeypatch):
    module = load_example_module("teleoperate_bi")
    events = prepare_teleoperation(
        monkeypatch,
        module,
        observation_poses=[FOLLOWER_POSE, FOLLOWER_POSE, FOLLOWER_POSE],
        action_poses=[LEADER_POSE, LEADER_POSE, LEADER_POSE, LEADER_POSE],
    )
    args = module.parse_args(
        [
            "--teleop.left_port",
            "COM5",
            "--teleop.right_port",
            "COM6",
            "--startup_mode",
            "sync",
            "--startup_sync_duration_s",
            "0.2",
            "--startup_sync_only",
            "--duration_s",
            "30",
            "--start_paused",
            "--fps",
            "5",
        ],
        platform_name="Windows",
    )
    clock = FakeClock(events)

    status = module.run_teleoperation(
        args,
        input_fn=lambda _: "SYNC",
        monotonic=clock.monotonic,
        sleep_fn=clock.sleep,
    )

    assert status == 0
    assert len(arm_send_actions(events)) == 2
    assert sum(event[:2] == ("leader", "get_action") for event in events) == 4
    assert ("left", "disconnect") in events
    assert ("right", "disconnect") in events
    assert ("robot", "disconnect") in events


def test_sync_handoff_reuses_frozen_target_without_extra_leader_read(monkeypatch):
    module = load_example_module("teleoperate_bi")
    frozen = {**LEADER_POSE, "left_shoulder_pan.pos": 0.5}
    final_follower = {f"arm_{key}": value for key, value in frozen.items()}
    events = prepare_teleoperation(
        monkeypatch,
        module,
        observation_poses=[FOLLOWER_POSE, FOLLOWER_POSE, final_follower],
        action_poses=[LEADER_POSE, frozen, frozen, frozen, {**frozen, "left_shoulder_pan.pos": 1.0}],
    )
    args = sync_args(module, "--duration_s", "0.2")
    clock = FakeClock(events)

    status = module.run_teleoperation(
        args,
        input_fn=lambda _: "SYNC",
        monotonic=clock.monotonic,
        sleep_fn=clock.sleep,
    )

    plan = module.build_startup_sync_plan(
        FOLLOWER_POSE,
        final_follower,
        side="both",
        requested_duration_s=0.2,
        fps=5,
    )
    arm_events = [
        (index, event)
        for index, event in enumerate(events)
        if event[:2] == ("robot", "send") and any(key.startswith("arm_") for key in event[2])
    ]
    final_sync_index, _ = arm_events[plan.frame_count - 1]
    first_ordinary_index, first_ordinary_event = arm_events[plan.frame_count]

    assert status == 0
    assert first_ordinary_event[2] == {**final_follower, **module.make_zero_action()}
    assert all(
        event[:2] != ("leader", "get_action")
        for event in events[final_sync_index + 1 : first_ordinary_index]
    )


def test_sync_handoff_first_action_forces_zero_body_with_keyboard(monkeypatch):
    module = load_example_module("teleoperate_bi")
    events = prepare_teleoperation(
        monkeypatch,
        module,
        observation_poses=[FOLLOWER_POSE, FOLLOWER_POSE, FOLLOWER_POSE],
        action_poses=[LEADER_POSE, LEADER_POSE, LEADER_POSE, LEADER_POSE],
    )

    class ActiveKeyboard:
        is_connected = False

        def __init__(self, config):
            self.config = config

        def connect(self):
            self.is_connected = True
            events.append(("keyboard", "connect"))

        def get_action(self):
            return {"forward", "lift_up"}

        def disconnect(self):
            self.is_connected = False
            events.append(("keyboard", "disconnect"))

    monkeypatch.setattr(module, "KeyboardTeleop", ActiveKeyboard)
    monkeypatch.setattr(
        FakeRobot,
        "_from_keyboard_to_lift_action",
        lambda self, keys: {"lift_axis.vel": 1},
    )
    args = module.parse_args(
        [
            "--teleop.left_port",
            "COM5",
            "--teleop.right_port",
            "COM6",
            "--no_rerun",
            "--startup_mode",
            "sync",
            "--startup_sync_duration_s",
            "0.2",
            "--fps",
            "5",
            "--duration_s",
            "0.2",
        ],
        platform_name="Windows",
    )
    clock = FakeClock(events)

    module.run_teleoperation(
        args,
        input_fn=lambda _: "SYNC",
        monotonic=clock.monotonic,
        sleep_fn=clock.sleep,
    )

    first_ordinary = arm_send_actions(events)[2]
    assert {key: first_ordinary[key] for key in module.make_zero_action()} == module.make_zero_action()


def test_post_sync_start_paused_rechecks_and_forwards_final_validated_sample(monkeypatch, capsys):
    module = load_example_module("teleoperate_bi")
    post_pause_leader = {**LEADER_POSE, "left_wrist_roll.pos": 6.0}
    post_pause_follower = {f"arm_{key}": value for key, value in post_pause_leader.items()}
    events = prepare_teleoperation(
        monkeypatch,
        module,
        observation_poses=[FOLLOWER_POSE, FOLLOWER_POSE, FOLLOWER_POSE, post_pause_follower],
        action_poses=[
            LEADER_POSE,
            LEADER_POSE,
            LEADER_POSE,
            LEADER_POSE,
            post_pause_leader,
            {**post_pause_leader, "left_wrist_roll.pos": 7.0},
        ],
    )
    args = sync_args(module, "--start_paused", "--duration_s", "0.2")
    clock = FakeClock(events)
    responses = iter(("SYNC", ""))

    status = module.run_teleoperation(
        args,
        input_fn=lambda _: next(responses),
        monotonic=clock.monotonic,
        sleep_fn=clock.sleep,
    )

    first_ordinary = arm_send_actions(events)[2]
    assert status == 0
    assert "Action space: body joints -100..100; grippers 0..100" in capsys.readouterr().out
    assert first_ordinary == {**post_pause_follower, **module.make_zero_action()}
    first_ordinary_index = next(
        index
        for index, event in enumerate(events)
        if event[:2] == ("robot", "send") and event[2] == first_ordinary
    )
    assert sum(event[:2] == ("leader", "get_action") for event in events[:first_ordinary_index]) == 5


def test_post_sync_start_paused_refuses_moved_sample_before_ordinary_send(monkeypatch, capsys):
    module = load_example_module("teleoperate_bi")
    moved = {**LEADER_POSE, "right_wrist_flex.pos": 20.1}
    events = prepare_teleoperation(
        monkeypatch,
        module,
        observation_poses=[FOLLOWER_POSE, FOLLOWER_POSE, FOLLOWER_POSE, FOLLOWER_POSE],
        action_poses=[LEADER_POSE, LEADER_POSE, LEADER_POSE, LEADER_POSE, moved],
    )
    args = sync_args(module, "--start_paused", "--duration_s", "0.2")
    clock = FakeClock(events)
    responses = iter(("SYNC", ""))

    status = module.run_teleoperation(
        args,
        input_fn=lambda _: next(responses),
        monotonic=clock.monotonic,
        sleep_fn=clock.sleep,
    )

    assert status == 2
    assert "arm_right_wrist_flex.pos" in capsys.readouterr().out
    assert len(arm_send_actions(events)) == 2


def test_sync_duration_clock_starts_after_sync_and_post_sync_pause(monkeypatch):
    module = load_example_module("teleoperate_bi")
    events = prepare_teleoperation(
        monkeypatch,
        module,
        observation_poses=[FOLLOWER_POSE, FOLLOWER_POSE, FOLLOWER_POSE, FOLLOWER_POSE],
        action_poses=[LEADER_POSE, LEADER_POSE, LEADER_POSE, LEADER_POSE, LEADER_POSE],
    )
    args = sync_args(module, "--start_paused", "--duration_s", "0.2")
    clock = FakeClock(events)
    responses = iter(("SYNC", ""))

    def confirm_then_pause(prompt):
        response = next(responses)
        if response == "":
            clock.advance(100.0)
        return response

    status = module.run_teleoperation(
        args,
        input_fn=confirm_then_pause,
        monotonic=clock.monotonic,
        sleep_fn=clock.sleep,
    )

    assert status == 0
    assert len(arm_send_actions(events)) >= 3


def test_sync_drift_refusal_returns_two_without_reverse_and_cleans_up(monkeypatch, capsys):
    module = load_example_module("teleoperate_bi")
    drifted = {**LEADER_POSE, "left_shoulder_pan.pos": 2.000001}
    events = prepare_teleoperation(
        monkeypatch,
        module,
        observation_poses=[FOLLOWER_POSE, FOLLOWER_POSE],
        action_poses=[LEADER_POSE, LEADER_POSE, LEADER_POSE, drifted],
    )
    args = sync_args(module, "--duration_s", "0.2")
    clock = FakeClock(events)

    status = module.run_teleoperation(
        args,
        input_fn=lambda _: "SYNC",
        monotonic=clock.monotonic,
        sleep_fn=clock.sleep,
    )

    captured = capsys.readouterr()
    assert status == 2
    assert "SAFETY REFUSAL" in captured.out
    assert "left shoulder_pan" in captured.out
    assert "frozen=0.0" in captured.out
    assert "current=2.000001" in captured.out
    assert "drift=2.000001" in captured.out
    assert "Traceback" not in captured.err
    assert len(arm_send_actions(events)) == 1
    last_arm_index = max(
        index
        for index, event in enumerate(events)
        if event[:2] == ("robot", "send") and any(key.startswith("arm_") for key in event[2])
    )
    assert all(
        not any(key.startswith("arm_") for key in event[2])
        for event in events[last_arm_index + 1 :]
        if event[:2] == ("robot", "send")
    )
    assert ("left", "disconnect") in events
    assert ("right", "disconnect") in events
    assert ("robot", "disconnect") in events


def test_sync_final_mismatch_returns_two_without_ordinary_send_and_cleans_up(monkeypatch, capsys):
    module = load_example_module("teleoperate_bi")
    mismatched = {**FOLLOWER_POSE, "arm_left_shoulder_pan.pos": 10.1}
    events = prepare_teleoperation(
        monkeypatch,
        module,
        observation_poses=[FOLLOWER_POSE, FOLLOWER_POSE, mismatched],
        action_poses=[LEADER_POSE, LEADER_POSE, LEADER_POSE, LEADER_POSE],
    )
    args = sync_args(module, "--duration_s", "0.2")
    clock = FakeClock(events)

    status = module.run_teleoperation(
        args,
        input_fn=lambda _: "SYNC",
        monotonic=clock.monotonic,
        sleep_fn=clock.sleep,
    )

    assert status == 2
    assert "arm_left_shoulder_pan.pos" in capsys.readouterr().out
    assert len(arm_send_actions(events)) == 2
    assert ("left", "disconnect") in events
    assert ("right", "disconnect") in events
    assert ("robot", "disconnect") in events


@pytest.mark.parametrize("failure", [RuntimeError("sync read failed"), KeyboardInterrupt()])
def test_sync_failure_or_interrupt_preserves_primary_and_cleans_up(monkeypatch, failure):
    module = load_example_module("teleoperate_bi")
    events = prepare_teleoperation(
        monkeypatch,
        module,
        left_disconnect_error=RuntimeError("cleanup failed"),
        observation_poses=[FOLLOWER_POSE, FOLLOWER_POSE],
        action_poses=[LEADER_POSE, LEADER_POSE, failure],
    )
    args = sync_args(module)
    clock = FakeClock(events)

    with pytest.raises(type(failure)) as caught:
        module.run_teleoperation(
            args,
            input_fn=lambda _: "SYNC",
            monotonic=clock.monotonic,
            sleep_fn=clock.sleep,
        )

    assert caught.value is failure
    assert any(
        event[:2] == ("robot", "send") and event[2] == module.make_zero_action()
        for event in events
    )
    assert ("right", "disconnect") in events
    assert ("left", "disconnect") in events
    assert ("robot", "disconnect") in events


def test_sync_visualization_failure_after_verification_preserves_primary_and_cleans_up(monkeypatch):
    module = load_example_module("teleoperate_bi")
    events = prepare_teleoperation(
        monkeypatch,
        module,
        observation_poses=[FOLLOWER_POSE, FOLLOWER_POSE, FOLLOWER_POSE],
        action_poses=[LEADER_POSE, LEADER_POSE, LEADER_POSE, LEADER_POSE],
    )
    primary_error = RuntimeError("visualization failed after sync")

    def fail_visualization_start(**kwargs):
        events.append(("rerun", "init"))
        raise primary_error

    monkeypatch.setattr(
        module,
        "load_rerun_functions",
        lambda: (
            fail_visualization_start,
            lambda *args: None,
            lambda: events.append(("rerun", "shutdown")),
        ),
    )
    args = module.parse_args(
        [
            "--teleop.left_port",
            "COM5",
            "--teleop.right_port",
            "COM6",
            "--no_keyboard",
            "--startup_mode",
            "sync",
            "--startup_sync_duration_s",
            "0.2",
            "--fps",
            "5",
        ],
        platform_name="Windows",
    )
    clock = FakeClock(events)

    with pytest.raises(RuntimeError) as caught:
        module.run_teleoperation(
            args,
            input_fn=lambda _: "SYNC",
            monotonic=clock.monotonic,
            sleep_fn=clock.sleep,
        )

    assert caught.value is primary_error
    assert len(arm_send_actions(events)) == 2
    assert max(
        index
        for index, event in enumerate(events)
        if event[:2] == ("robot", "send") and any(key.startswith("arm_") for key in event[2])
    ) < events.index(("rerun", "init"))
    assert ("right", "disconnect") in events
    assert ("left", "disconnect") in events
    assert ("robot", "disconnect") in events
    assert ("rerun", "shutdown") in events


def test_large_initial_mismatch_refuses_before_arm_send_and_cleans_up(monkeypatch, capsys):
    module = load_example_module("teleoperate_bi")
    events = prepare_teleoperation(
        monkeypatch,
        module,
        observation_poses=[{**FOLLOWER_POSE, "arm_left_shoulder_pan.pos": 10.1}],
    )
    args = teleoperation_args(module, "--duration_s", "1")

    status = module.run_teleoperation(args, sleep_fn=lambda _: None)

    output = capsys.readouterr().out
    assert status == 2
    assert "SAFETY REFUSAL" in output
    assert "arm_left_shoulder_pan.pos" in output
    assert "follower value" in output
    assert "leader value" in output
    assert "signed difference" in output
    assert "absolute difference" in output
    assert "10.1" in output
    assert "10.0" in output
    robot = FakeRobot.instances[0]
    assert robot.actions
    assert all(action == module.make_zero_action() for action in robot.actions)
    assert ("left", "disconnect") in events
    assert ("right", "disconnect") in events
    assert ("robot", "disconnect") in events


def test_initial_gate_forwards_the_validated_sample_first(monkeypatch):
    module = load_example_module("teleoperate_bi")
    first_pose = {**LEADER_POSE, "left_shoulder_pan.pos": 1.0}
    unchecked_next_pose = {**LEADER_POSE, "left_shoulder_pan.pos": 2.0}
    events = prepare_teleoperation(
        monkeypatch,
        module,
        observation_poses=[{f"arm_{key}": value for key, value in first_pose.items()}],
        action_poses=[first_pose, unchecked_next_pose],
    )
    args = teleoperation_args(module, "--duration_s", "1")
    clock_values = iter((0.0, 0.0, 2.0))

    status = module.run_teleoperation(
        args,
        monotonic=lambda: next(clock_values),
        sleep_fn=lambda _: None,
    )

    first_arm_send_index = next(
        index
        for index, event in enumerate(events)
        if event[:2] == ("robot", "send") and any(key.startswith("arm_") for key in event[2])
    )
    first_arm_action = events[first_arm_send_index][2]
    assert status == 0
    assert first_arm_action == {
        **{f"arm_{key}": value for key, value in first_pose.items()},
        **module.make_zero_action(),
    }
    assert sum(event[:2] == ("leader", "get_action") for event in events[:first_arm_send_index]) == 1


def test_initial_validated_sample_forces_zero_body_despite_keyboard_input(monkeypatch):
    module = load_example_module("teleoperate_bi")
    events = prepare_teleoperation(monkeypatch, module)

    class ActiveKeyboard:
        is_connected = False

        def __init__(self, config):
            self.config = config

        def connect(self):
            self.is_connected = True
            events.append(("keyboard", "connect"))

        def get_action(self):
            return {"forward", "lift_up"}

        def disconnect(self):
            events.append(("keyboard", "disconnect"))
            self.is_connected = False

    monkeypatch.setattr(module, "KeyboardTeleop", ActiveKeyboard)
    monkeypatch.setattr(
        FakeRobot,
        "_from_keyboard_to_lift_action",
        lambda self, pressed_keys: {"lift_axis.vel": 1},
    )
    args = module.parse_args(
        [
            "--teleop.left_port",
            "COM5",
            "--teleop.right_port",
            "COM6",
            "--no_rerun",
            "--duration_s",
            "1",
        ],
        platform_name="Windows",
    )
    clock_values = iter((0.0, 0.0, 2.0))

    status = module.run_teleoperation(
        args,
        monotonic=lambda: next(clock_values),
        sleep_fn=lambda _: None,
    )

    first_arm_action = next(action for action in FakeRobot.instances[0].actions if "arm_left_shoulder_pan.pos" in action)
    assert status == 0
    assert {key: first_arm_action[key] for key in module.make_zero_action()} == module.make_zero_action()


def test_start_paused_rechecks_fresh_both_sides_before_forwarding(monkeypatch, capsys):
    module = load_example_module("teleoperate_bi")
    moved_pose = {**LEADER_POSE, "right_wrist_flex.pos": 20.1}
    events = prepare_teleoperation(
        monkeypatch,
        module,
        observation_poses=[FOLLOWER_POSE, FOLLOWER_POSE],
        action_poses=[LEADER_POSE, moved_pose],
    )
    args = teleoperation_args(module, "--start_paused", "--duration_s", "1")
    clock_values = iter((0.0, 0.0, 2.0))

    status = module.run_teleoperation(
        args,
        input_fn=lambda _: "",
        monotonic=lambda: next(clock_values),
        sleep_fn=lambda _: None,
    )

    output = capsys.readouterr().out
    robot = FakeRobot.instances[0]
    leader = FakeLeader.instances[0]
    assert status == 2
    assert "SAFETY REFUSAL" in output
    assert "arm_right_wrist_flex.pos" in output
    assert robot.observation_index == 2
    assert leader.action_index == 2
    assert all(not any(key.startswith("arm_") for key in action) for action in robot.actions)
    assert ("left", "disconnect") in events
    assert ("right", "disconnect") in events
    assert ("robot", "disconnect") in events


def test_start_paused_forwards_second_validated_sample_without_extra_read(monkeypatch, capsys):
    module = load_example_module("teleoperate_bi")
    second_pose = {**LEADER_POSE, "left_wrist_roll.pos": 6.0}
    second_follower_pose = {f"arm_{key}": value for key, value in second_pose.items()}
    events = prepare_teleoperation(
        monkeypatch,
        module,
        observation_poses=[FOLLOWER_POSE, second_follower_pose],
        action_poses=[LEADER_POSE, second_pose, {**second_pose, "left_wrist_roll.pos": 7.0}],
    )
    args = teleoperation_args(module, "--start_paused", "--duration_s", "1")
    clock_values = iter((0.0, 0.0, 2.0))

    status = module.run_teleoperation(
        args,
        input_fn=lambda _: "",
        monotonic=lambda: next(clock_values),
        sleep_fn=lambda _: None,
    )

    first_arm_send_index = next(
        index
        for index, event in enumerate(events)
        if event[:2] == ("robot", "send") and any(key.startswith("arm_") for key in event[2])
    )
    first_arm_action = events[first_arm_send_index][2]
    assert status == 0
    assert "Action space: body joints -100..100; grippers 0..100" in capsys.readouterr().out
    assert first_arm_action == {
        **{f"arm_{key}": value for key, value in second_pose.items()},
        **module.make_zero_action(),
    }
    assert sum(event[:2] == ("leader", "get_action") for event in events[:first_arm_send_index]) == 2


def test_check_alignment_only_avoids_optional_runtime_resources_and_arm_send(monkeypatch):
    module = load_example_module("teleoperate_bi")
    prepare_teleoperation(monkeypatch, module)
    args = teleoperation_args(module, "--check_alignment_only", "--duration_s", "1")

    status = module.run_teleoperation(
        args,
        monotonic=lambda: (_ for _ in ()).throw(AssertionError("main loop clock was read")),
        sleep_fn=lambda _: (_ for _ in ()).throw(AssertionError("main loop slept")),
    )

    assert status == 0
    assert all(
        not any(key.startswith("arm_") for key in action) for action in FakeRobot.instances[0].actions
    )


def test_out_of_range_startup_sample_is_expected_refusal(monkeypatch, capsys):
    module = load_example_module("teleoperate_bi")
    prepare_teleoperation(
        monkeypatch,
        module,
        action_poses=[{**LEADER_POSE, "right_shoulder_lift.pos": -105.8}],
    )
    args = teleoperation_args(module, "--duration_s", "1")

    status = module.run_teleoperation(args, sleep_fn=lambda _: None)

    captured = capsys.readouterr()
    assert status == 2
    assert (
        "SAFETY REFUSAL: leader right shoulder_lift value -105.8 is outside expected -100..100"
        in captured.out
    )
    assert "Traceback" not in captured.err
    assert all(
        not any(key.startswith("arm_") for key in action) for action in FakeRobot.instances[0].actions
    )


def test_runtime_validation_rejects_out_of_range_without_forwarding_it(monkeypatch, capsys):
    module = load_example_module("teleoperate_bi")
    invalid_pose = {**LEADER_POSE, "left_elbow_flex.pos": math.inf}
    prepare_teleoperation(
        monkeypatch,
        module,
        action_poses=[LEADER_POSE, invalid_pose],
    )
    args = teleoperation_args(module, "--duration_s", "1")
    clock_values = iter((0.0, 0.0, 0.0, 2.0))

    status = module.run_teleoperation(
        args,
        monotonic=lambda: next(clock_values),
        sleep_fn=lambda _: None,
    )

    assert status == 2
    assert "leader left elbow_flex value inf must be finite" in capsys.readouterr().out
    assert all(
        action.get("arm_left_elbow_flex.pos") != math.inf for action in FakeRobot.instances[0].actions
    )


def test_runtime_does_not_reapply_max_start_mismatch(monkeypatch):
    module = load_example_module("teleoperate_bi")
    later_pose = {**LEADER_POSE, "left_shoulder_pan.pos": 90.0}
    prepare_teleoperation(
        monkeypatch,
        module,
        observation_poses=[FOLLOWER_POSE],
        action_poses=[LEADER_POSE, later_pose],
    )
    args = teleoperation_args(module, "--max_start_mismatch", "1", "--duration_s", "1")
    clock_values = iter((0.0, 0.0, 0.0, 2.0))

    status = module.run_teleoperation(
        args,
        monotonic=lambda: next(clock_values),
        sleep_fn=lambda _: None,
    )

    assert status == 0
    assert any(action.get("arm_left_shoulder_pan.pos") == 90.0 for action in FakeRobot.instances[0].actions)


def test_no_keyboard_sends_zero_body_commands_and_keeps_arm_actions(monkeypatch):
    module = load_example_module("teleoperate_bi")
    prepare_teleoperation(monkeypatch, module)
    args = teleoperation_args(module, "--duration_s", "1")
    clock_values = iter((0.0, 0.0, 2.0))

    module.run_teleoperation(
        args,
        monotonic=lambda: next(clock_values),
        sleep_fn=lambda _: None,
    )

    robot = FakeRobot.instances[0]
    assert any("arm_left_shoulder_pan.pos" in action for action in robot.actions)
    assert all(action["x.vel"] == 0 for action in robot.actions)
    assert all(action["y.vel"] == 0 for action in robot.actions)
    assert all(action["theta.vel"] == 0 for action in robot.actions)
    assert all(action["lift_axis.vel"] == 0 for action in robot.actions)
    assert all("lift_axis.height_mm" not in action for action in robot.actions)


def test_start_paused_forwards_no_leader_action_before_confirmation(monkeypatch):
    module = load_example_module("teleoperate_bi")
    prepare_teleoperation(monkeypatch, module)
    args = teleoperation_args(module, "--start_paused", "--duration_s", "1")
    clock_values = iter((0.0, 0.0, 2.0))
    gate_checked = False

    def release_gate(prompt: str):
        nonlocal gate_checked
        robot = FakeRobot.instances[0]
        assert robot.actions
        assert all(not any(key.startswith("arm_") for key in action) for action in robot.actions)
        gate_checked = True
        return ""

    module.run_teleoperation(
        args,
        input_fn=release_gate,
        monotonic=lambda: next(clock_values),
        sleep_fn=lambda _: None,
    )

    assert gate_checked


def test_duration_exit_zeros_before_disconnect_and_cleans_all_devices(monkeypatch):
    module = load_example_module("teleoperate_bi")
    events = prepare_teleoperation(monkeypatch, module)
    args = teleoperation_args(module, "--duration_s", "1")
    clock_values = iter((0.0, 0.0, 2.0))

    module.run_teleoperation(
        args,
        monotonic=lambda: next(clock_values),
        sleep_fn=lambda _: None,
    )

    final_zero_index = max(
        index
        for index, event in enumerate(events)
        if event[:2] == ("robot", "send") and event[2] == module.make_zero_action()
    )
    left_disconnect_index = events.index(("left", "disconnect"))
    right_disconnect_index = events.index(("right", "disconnect"))
    robot_disconnect_index = events.index(("robot", "disconnect"))
    assert final_zero_index < left_disconnect_index
    assert final_zero_index < right_disconnect_index
    assert max(left_disconnect_index, right_disconnect_index) < robot_disconnect_index


def test_keyboard_interrupt_zeros_and_disconnects_connected_devices(monkeypatch):
    module = load_example_module("teleoperate_bi")
    events = prepare_teleoperation(monkeypatch, module)
    args = teleoperation_args(module)

    def interrupting_action(self):
        raise KeyboardInterrupt

    monkeypatch.setattr(FakeLeader, "get_action", interrupting_action)

    with pytest.raises(KeyboardInterrupt):
        module.run_teleoperation(args, sleep_fn=lambda _: None)

    assert any(event[:2] == ("robot", "send") and event[2] == module.make_zero_action() for event in events)
    assert ("left", "disconnect") in events
    assert ("right", "disconnect") in events
    assert ("robot", "disconnect") in events


def test_partial_leader_connection_failure_preserves_primary_error(monkeypatch):
    module = load_example_module("teleoperate_bi")
    primary_error = RuntimeError("right leader failed")
    events = prepare_teleoperation(
        monkeypatch,
        module,
        right_connect_error=primary_error,
        left_disconnect_error=RuntimeError("left cleanup failed"),
    )
    args = teleoperation_args(module)

    with pytest.raises(RuntimeError) as caught:
        module.run_teleoperation(args, sleep_fn=lambda _: None)

    assert caught.value is primary_error
    assert ("left", "disconnect") in events
    assert ("right", "disconnect") not in events
    assert ("robot", "disconnect") in events


def test_visualization_start_failure_cleans_connected_devices_and_preserves_error(monkeypatch):
    module = load_example_module("teleoperate_bi")
    events = prepare_teleoperation(monkeypatch, module)
    primary_error = RuntimeError("visualization failed")

    def fail_visualization_start(**kwargs):
        raise primary_error

    monkeypatch.setattr(
        module,
        "load_rerun_functions",
        lambda: (fail_visualization_start, lambda *args: None, lambda: events.append(("rerun", "shutdown"))),
    )
    args = module.parse_args(
        [
            "--teleop.left_port",
            "COM5",
            "--teleop.right_port",
            "COM6",
            "--no_keyboard",
        ],
        platform_name="Windows",
    )

    with pytest.raises(RuntimeError) as caught:
        module.run_teleoperation(args, sleep_fn=lambda _: None)

    assert caught.value is primary_error
    assert ("left", "disconnect") in events
    assert ("right", "disconnect") in events
    assert ("robot", "disconnect") in events
    assert ("rerun", "shutdown") in events


def test_no_robot_and_no_leader_do_not_construct_or_cleanup_skipped_devices(monkeypatch):
    module = load_example_module("teleoperate_bi")
    monkeypatch.setattr(
        module,
        "AlohaMiniClient",
        lambda config: (_ for _ in ()).throw(AssertionError("robot was constructed")),
    )
    monkeypatch.setattr(
        module,
        "BiSOLeader",
        lambda config: (_ for _ in ()).throw(AssertionError("leader was constructed")),
    )
    monkeypatch.setattr(
        module,
        "KeyboardTeleop",
        lambda config: (_ for _ in ()).throw(AssertionError("keyboard was constructed")),
    )
    monkeypatch.setattr(
        module,
        "load_rerun_functions",
        lambda: (_ for _ in ()).throw(AssertionError("Rerun helpers were loaded")),
    )
    args = teleoperation_args(
        module,
        "--no_robot",
        "--no_leader",
        "--duration_s",
        "1",
    )
    clock_values = iter((0.0, 2.0))

    module.run_teleoperation(
        args,
        monotonic=lambda: next(clock_values),
        sleep_fn=lambda _: None,
    )


def test_windows_startup_sync_commissioning_docs_contain_approved_safety_sequence():
    text = (REPO_ROOT / "docs" / "alohamini" / "alohamini.md").read_text(encoding="utf-8")
    required = (
        "not collision-aware",
        "STARTUP_SYNC_MAX_STEP = 0.75",
        "STARTUP_SYNC_LEADER_DRIFT = 2.0",
        "type exactly `SYNC`",
        "S1",
        "S2",
        "S3",
        "S4",
        "S5",
        "S6",
        "192.168.1.134",
        "COM7",
        "COM8",
        "so101_leader_bi",
        "so-arm-5dof",
        "--startup_sync_only",
        "--start_paused",
        "--no_keyboard",
        "--no_rerun",
    )

    for marker in required:
        assert marker in text
