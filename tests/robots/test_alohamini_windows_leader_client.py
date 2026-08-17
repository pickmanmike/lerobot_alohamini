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
    try:
        spec.loader.exec_module(module)
    finally:
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


class FakeRobot:
    instances = []
    events: list[tuple]
    observation_poses: list[dict[str, float]]

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
        self.observation_index += 1
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
    action_poses: list[dict[str, float]]

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
        action = dict(type(self).action_poses[pose_index])
        self.action_index += 1
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
):
    events: list[tuple] = []
    FakeRobot.instances = []
    FakeRobot.events = events
    FakeRobot.observation_poses = list(observation_poses or [FOLLOWER_POSE])
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
