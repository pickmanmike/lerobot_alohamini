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

from collections import defaultdict
import json
import sys
from types import SimpleNamespace

import pytest

from lerobot.robots.alohamini import alohamini as alohamini_module
from lerobot.robots.alohamini import alohamini_calibrate as calibrate_module
from lerobot.robots.alohamini import alohamini_host as host_module
from lerobot.robots.alohamini.alohamini import AlohaMini
from lerobot.robots.alohamini.alohamini_calibrate import (
    make_parser as make_calibrate_parser,
    make_robot_config as make_calibrate_robot_config,
)
from lerobot.robots.alohamini.alohamini_host import (
    connect_robot,
    make_host_config,
    make_parser as make_host_parser,
    make_robot_config as make_host_robot_config,
)
from lerobot.robots.alohamini.alohamini_lift_home import (
    make_parser as make_lift_home_parser,
    make_robot_config as make_lift_home_robot_config,
)
from lerobot.robots.alohamini.lift_axis import LiftAxis, LiftAxisConfig
from lerobot.robots.alohamini.motor_safety import set_torque_enabled


class FakeBus:
    def __init__(self, name: str, motor_names: tuple[str, ...], events: list[tuple]):
        self.name = name
        self.motors = dict.fromkeys(motor_names, object())
        self.events = events
        self.read_calls: list[tuple[str, str, bool]] = []
        self.is_connected = True
        self.registers = defaultdict(int)
        self.read_sequences: dict[tuple[str, str], list[object]] = {}
        self.write_failures: dict[tuple[str, str, int], tuple[BaseException, bool]] = {}
        self.sync_write_failures: dict[str, BaseException] = {}
        self.position_step = 0

    def read(
        self,
        register: str,
        motor: str,
        *,
        normalize: bool = True,
        num_retry: int = 3,
    ) -> int | float:
        self.events.append((self.name, "read", register, motor))
        self.read_calls.append((register, motor, normalize))
        return self._read_value(register, motor)

    def _read_value(self, register: str, motor: str) -> int | float:
        sequence = self.read_sequences.get((register, motor))
        if sequence:
            value = sequence.pop(0)
            if isinstance(value, BaseException):
                raise value
            self.registers[(register, motor)] = value
            return value
        if register == "Present_Position" and self.position_step:
            self.registers[(register, motor)] += self.position_step
        return self.registers[(register, motor)]

    def write(
        self,
        register: str,
        motor: str,
        value: int | float,
        *,
        normalize: bool = True,
        num_retry: int = 3,
    ) -> None:
        int_value = int(value)
        self.events.append((self.name, "write", register, motor, int_value))
        failure = self.write_failures.get((register, motor, int_value))
        if failure is not None:
            error, apply_before_error = failure
            if apply_before_error:
                self.registers[(register, motor)] = int_value
            raise error
        self.registers[(register, motor)] = int_value

    def disconnect(self, disable_torque: bool = True) -> None:
        self.events.append((self.name, "disconnect", disable_torque))
        self.is_connected = False

    def sync_read(
        self,
        register: str,
        motors: str | list[str] | None = None,
        *,
        normalize: bool = True,
        num_retry: int = 3,
    ) -> dict[str, int | float]:
        names = tuple(self.motors) if motors is None else (motors,) if isinstance(motors, str) else tuple(motors)
        self.events.append((self.name, "sync_read", register, names, normalize))
        return {name: self._read_value(register, name) for name in names}

    def sync_write(
        self,
        register: str,
        values: dict[str, int | float],
        *,
        normalize: bool = True,
        num_retry: int = 0,
    ) -> None:
        self.events.append((self.name, "sync_write", register, dict(values), normalize))
        failure = self.sync_write_failures.get(register)
        if failure is not None:
            raise failure
        for motor, value in values.items():
            self.registers[(register, motor)] = value


class FakeLift:
    def __init__(self):
        self.cfg = SimpleNamespace(name="lift_axis")
        self.is_homed = False
        self.home_calls = 0

    def home(self):
        self.home_calls += 1
        self.is_homed = True
        return None

    def mark_unhomed(self):
        self.is_homed = False

    def apply_action(self, action):
        del action


def make_action_robot(*, trace_am1_left_elbow: bool, robot_model: str = "alohamini1"):
    events = []
    left = FakeBus(
        "left",
        (
            "arm_left_elbow_flex",
            "base_left_wheel",
            "base_back_wheel",
            "base_right_wheel",
        ),
        events,
    )
    left.registers[("Present_Position", "arm_left_elbow_flex")] = 10
    left.registers[("Present_Current", "arm_left_elbow_flex")] = 10
    robot = AlohaMini.__new__(AlohaMini)
    robot.config = SimpleNamespace(
        robot_model=robot_model,
        max_relative_target=4.0,
        trace_am1_left_elbow=trace_am1_left_elbow,
    )
    robot.left_bus = left
    robot.right_bus = None
    robot.left_arm_motors = ["arm_left_elbow_flex"]
    robot.right_arm_motors = []
    robot.base_motors = ["base_left_wheel", "base_back_wheel", "base_right_wheel"]
    robot.lift = FakeLift()
    robot.cameras = {}
    robot.logs = {}
    robot._gripper_current_limit_ma = 500.0
    robot._gripper_hold_close_step = 3.0
    robot._gripper_release_margin = 1.0
    robot._gripper_open_direction = {}
    robot._gripper_hold_goal = {}
    robot._gripper_hold_direction = {}
    robot._joint_current_limit_ma = 1800.0
    robot._joint_release_margin = 1.0
    robot._joint_hold_goal = {}
    robot._joint_hold_direction = {}
    robot._body_to_wheel_raw = lambda x, y, theta: {
        "base_left_wheel": 0,
        "base_back_wheel": 0,
        "base_right_wheel": 0,
    }
    return robot, left, events


def elbow_action(target: float = 20.0) -> dict[str, float]:
    return {
        "arm_left_elbow_flex.pos": target,
        "x.vel": 0.0,
        "y.vel": 0.0,
        "theta.vel": 0.0,
    }


def attach_right_elbow_bus(robot: AlohaMini, events: list[tuple]) -> FakeBus:
    right = FakeBus("right", ("arm_right_elbow_flex",), events)
    right.registers[("Present_Position", "arm_right_elbow_flex")] = 10
    robot.right_bus = right
    robot.right_arm_motors = ["arm_right_elbow_flex"]
    return right


def bimanual_elbow_action() -> dict[str, float]:
    return {**elbow_action(), "arm_right_elbow_flex.pos": 20.0}


def attach_right_shoulder_bus(robot: AlohaMini, events: list[tuple]) -> FakeBus:
    right = FakeBus("right", ("arm_right_shoulder_lift",), events)
    right.motors["arm_right_shoulder_lift"] = SimpleNamespace(id=2)
    right.registers[("Present_Position", "arm_right_shoulder_lift")] = 10
    right.registers[("Present_Current", "arm_right_shoulder_lift")] = 10
    robot.right_bus = right
    robot.right_arm_motors = ["arm_right_shoulder_lift"]
    return right


def right_shoulder_action(target: float = 20.0) -> dict[str, float]:
    return {
        **elbow_action(target=10.0),
        "arm_right_shoulder_lift.pos": target,
    }


def trace_records(captured: str) -> list[dict]:
    return [json.loads(line) for line in captured.splitlines() if line.startswith("{")]


def make_activation_robot(*, fail_arm_enable: bool = False):
    events = []
    left_names = (
        "arm_left_shoulder_pan",
        "arm_left_gripper",
        "base_left_wheel",
        "base_back_wheel",
        "base_right_wheel",
        "lift_axis",
    )
    right_names = ("arm_right_shoulder_pan", "arm_right_gripper")
    left = FakeBus("left", left_names, events)
    right = FakeBus("right", right_names, events)
    left.registers[("Present_Position", "arm_left_shoulder_pan")] = 101
    left.registers[("Present_Position", "arm_left_gripper")] = 202
    right.registers[("Present_Position", "arm_right_shoulder_pan")] = 303
    right.registers[("Present_Position", "arm_right_gripper")] = 404
    if fail_arm_enable:
        left.write_failures[("Torque_Enable", "arm_left_shoulder_pan", 1)] = (
            ConnectionError("missing acknowledgement"),
            False,
        )

    robot = AlohaMini.__new__(AlohaMini)
    robot.left_bus = left
    robot.right_bus = right
    robot.left_arm_motors = ["arm_left_shoulder_pan", "arm_left_gripper"]
    robot.right_arm_motors = ["arm_right_shoulder_pan", "arm_right_gripper"]
    robot.base_motors = ["base_left_wheel", "base_back_wheel", "base_right_wheel"]
    robot.lift = FakeLift()
    robot.cameras = {}
    return robot, left, right, events


def event_index(events: list[tuple], expected: tuple) -> int:
    return next(index for index, event in enumerate(events) if event == expected)


def test_arm_goals_are_seeded_from_raw_positions_before_torque_enable():
    robot, _, _, events = make_activation_robot()

    robot.activate_motors(home_lift=False)

    expected_positions = {
        ("left", "arm_left_shoulder_pan"): 101,
        ("left", "arm_left_gripper"): 202,
        ("right", "arm_right_shoulder_pan"): 303,
        ("right", "arm_right_gripper"): 404,
    }
    for (bus_name, motor), position in expected_positions.items():
        goal_index = event_index(events, (bus_name, "write", "Goal_Position", motor, position))
        torque_index = event_index(events, (bus_name, "write", "Torque_Enable", motor, 1))
        assert goal_index < torque_index


def test_wheel_and_lift_zero_goals_precede_normal_torque_activation():
    robot, _, _, events = make_activation_robot()

    robot.activate_motors(home_lift=False)

    first_enable_index = next(
        index
        for index, event in enumerate(events)
        if event[1:3] == ("write", "Torque_Enable") and event[-1] == 1
    )
    for motor in (*robot.base_motors, "lift_axis"):
        zero_index = event_index(events, ("left", "write", "Goal_Velocity", motor, 0))
        assert zero_index < first_enable_index


def test_missing_lock_acknowledgement_requires_correct_readback():
    events = []
    bus = FakeBus("left", ("motor",), events)
    bus.write_failures[("Lock", "motor", 1)] = (ConnectionError("no status packet"), True)

    set_torque_enabled(bus, ("motor",), enabled=True)

    assert bus.registers[("Lock", "motor")] == 1
    assert ("left", "read", "Lock", "motor") in events

    bad_bus = FakeBus("left", ("motor",), [])
    bad_bus.write_failures[("Lock", "motor", 1)] = (ConnectionError("no status packet"), False)
    with pytest.raises(RuntimeError, match="expected 1, read back 0"):
        set_torque_enabled(bad_bus, ("motor",), enabled=True)


def test_activation_failure_zeros_body_disables_torque_and_closes_buses():
    robot, left, right, events = make_activation_robot(fail_arm_enable=True)

    with pytest.raises(RuntimeError, match="motor activation failed"):
        robot.activate_motors(home_lift=False)

    failure_index = event_index(
        events,
        ("left", "write", "Torque_Enable", "arm_left_shoulder_pan", 1),
    )
    for motor in (*robot.base_motors, "lift_axis"):
        assert any(
            index > failure_index and event == ("left", "write", "Goal_Velocity", motor, 0)
            for index, event in enumerate(events)
        )
    for bus_name, bus in (("left", left), ("right", right)):
        for motor in bus.motors:
            assert (bus_name, "write", "Torque_Enable", motor, 0) in events
        assert not bus.is_connected
    assert not robot.lift.is_homed


@pytest.mark.parametrize(
    ("parser_factory", "config_factory", "extra_args"),
    [
        (make_host_parser, make_host_robot_config, []),
        (make_calibrate_parser, make_calibrate_robot_config, []),
        (make_lift_home_parser, make_lift_home_robot_config, []),
    ],
)
def test_no_cameras_builds_an_empty_camera_configuration(parser_factory, config_factory, extra_args):
    args = parser_factory().parse_args(["--no_cameras", *extra_args])

    config = config_factory(args)

    assert config.cameras == {}


def test_empty_camera_configuration_constructs_no_camera_objects(monkeypatch, tmp_path):
    class ConstructionBus:
        def __init__(self, port, motors, calibration):
            self.port = port
            self.motors = motors
            self.calibration = calibration

    camera_configs = []

    def fake_make_cameras(configs):
        camera_configs.append(configs)
        return {}

    monkeypatch.setattr(alohamini_module, "FeetechMotorsBus", ConstructionBus)
    monkeypatch.setattr(alohamini_module, "make_cameras_from_configs", fake_make_cameras)
    args = make_host_parser().parse_args(["--robot_model", "alohamini1", "--no_cameras"])
    config = make_host_robot_config(args)
    config.calibration_dir = tmp_path

    robot = AlohaMini(config)

    assert camera_configs == [{}]
    assert robot.cameras == {}


def test_skip_lift_home_does_not_request_home():
    class RecordingRobot:
        def __init__(self):
            self.home_calls = 0

        def connect(self, *, home_lift: bool):
            if home_lift:
                self.home_calls += 1

    robot = RecordingRobot()
    connect_robot(robot, skip_lift_home=True)

    assert robot.home_calls == 0


def test_calibration_skip_lift_home_does_not_call_home(monkeypatch):
    created_robots = []

    class CalibrationRobot:
        def __init__(self, config):
            self.config = config
            self.is_calibrated = True
            self.is_connected = False
            self.home_calls = 0
            self.lift = SimpleNamespace(home=self._home)
            created_robots.append(self)

        def _home(self):
            self.home_calls += 1

        def connect(self, **kwargs):
            self.connect_kwargs = kwargs

        def calibrate(self):
            pass

    monkeypatch.setattr(calibrate_module, "AlohaMini", CalibrationRobot)
    monkeypatch.setattr(
        "sys.argv",
        ["alohamini_calibrate", "--robot_model", "alohamini1", "--no_cameras", "--skip_lift_home"],
    )

    calibrate_module.main()

    robot = created_robots[0]
    assert robot.config.cameras == {}
    assert robot.connect_kwargs == {"calibrate": False, "activate": False, "home_lift": False}
    assert robot.home_calls == 0


def test_host_safety_limits_are_applied_to_configs():
    args = make_host_parser().parse_args(
        ["--max_relative_target", "4.5", "--max_loop_freq_hz", "20"]
    )

    assert make_host_robot_config(args).max_relative_target == 4.5
    assert make_host_config(args).max_loop_freq_hz == 20


def test_trace_flag_is_host_only_and_defaults_off():
    parser = make_host_parser()

    assert "--trace_am1_left_elbow" in parser.format_help()
    args = parser.parse_args(["--robot_model", "alohamini1", "--no_cameras"])

    assert args.trace_am1_left_elbow is False
    assert make_host_robot_config(args).trace_am1_left_elbow is False
    trace_args = parser.parse_args(
        ["--robot_model", "alohamini1", "--no_cameras", "--trace_am1_left_elbow"]
    )
    assert make_host_robot_config(trace_args).trace_am1_left_elbow is True


def test_selected_trace_flag_defaults_off_and_propagates_right_shoulder():
    parser = make_host_parser()

    args = parser.parse_args(["--robot_model", "alohamini1", "--no_cameras"])
    selected_args = parser.parse_args(
        [
            "--robot_model",
            "alohamini1",
            "--no_cameras",
            "--trace_am1_joint",
            "arm_right_shoulder_lift",
        ]
    )

    assert args.trace_am1_joint is None
    assert make_host_robot_config(args).trace_am1_joint is None
    assert selected_args.trace_am1_joint == "arm_right_shoulder_lift"
    assert make_host_robot_config(selected_args).trace_am1_joint == "arm_right_shoulder_lift"


@pytest.mark.parametrize(
    "args",
    [
        SimpleNamespace(
            trace_am1_left_elbow=False,
            trace_am1_joint="arm_right_shoulder_lift",
            robot_model="alohamini2",
            no_follower=False,
        ),
        SimpleNamespace(
            trace_am1_left_elbow=False,
            trace_am1_joint="arm_right_shoulder_lift",
            robot_model="alohamini1",
            no_follower=True,
        ),
        SimpleNamespace(
            trace_am1_left_elbow=True,
            trace_am1_joint="arm_right_shoulder_lift",
            robot_model="alohamini1",
            no_follower=False,
        ),
        SimpleNamespace(
            trace_am1_left_elbow=False,
            trace_am1_joint="arm_right_elbow_flex",
            robot_model="alohamini1",
            no_follower=False,
        ),
    ],
)
def test_selected_trace_rejects_unsafe_or_ambiguous_host_modes(args):
    with pytest.raises(ValueError, match="trace_am1_joint"):
        host_module.validate_trace_args(args)


def test_selected_trace_startup_summary_identifies_right_shoulder_id2(capsys):
    host_module.print_trace_startup_summary(
        SimpleNamespace(
            max_relative_target=10.0,
            trace_am1_left_elbow=False,
            trace_am1_joint="arm_right_shoulder_lift",
        )
    )

    record = trace_records(capsys.readouterr().out)[-1]
    assert record == {
        "event": "am1_joint_trace_startup",
        "timestamp_ns": record["timestamp_ns"],
        "effective_max_relative_target": 10.0,
        "motor": "arm_right_shoulder_lift",
        "motor_id": 2,
        "side": "right",
    }
    assert record["timestamp_ns"] > 0


@pytest.mark.parametrize(
    "args",
    [
        SimpleNamespace(trace_am1_left_elbow=True, robot_model="alohamini2", no_follower=False),
        SimpleNamespace(trace_am1_left_elbow=True, robot_model="alohamini1", no_follower=True),
    ],
)
def test_trace_flag_rejects_invalid_host_modes_before_connection(args):
    validate = getattr(host_module, "validate_trace_args", lambda value: None)

    with pytest.raises(ValueError, match="trace_am1_left_elbow"):
        validate(args)


def test_trace_startup_summary_reports_effective_limiter_and_motor(capsys):
    emit = getattr(host_module, "print_trace_startup_summary", lambda args: None)

    emit(SimpleNamespace(max_relative_target=4.0))

    record = trace_records(capsys.readouterr().out)[-1]
    assert record == {
        "event": "am1_left_elbow_trace_startup",
        "timestamp_ns": record["timestamp_ns"],
        "effective_max_relative_target": 4.0,
        "motor": "arm_left_elbow_flex",
    }
    assert record["timestamp_ns"] > 0


def test_trace_host_rejects_invalid_mode_before_constructing_robot(monkeypatch):
    def must_not_construct(config):
        del config
        pytest.fail("trace validation should run before robot construction")

    monkeypatch.setattr(
        sys,
        "argv",
        ["alohamini_host", "--robot_model", "alohamini2", "--trace_am1_left_elbow"],
    )
    monkeypatch.setattr(host_module, "AlohaMini", must_not_construct)

    with pytest.raises(ValueError, match="trace_am1_left_elbow"):
        host_module.main()


def test_trace_host_prints_startup_summary_before_constructing_robot(monkeypatch, capsys):
    class StopBeforeConnection(Exception):
        pass

    def stop_before_connection(config):
        del config
        raise StopBeforeConnection

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "alohamini_host",
            "--robot_model",
            "alohamini1",
            "--no_cameras",
            "--max_relative_target",
            "4",
            "--trace_am1_left_elbow",
        ],
    )
    monkeypatch.setattr(host_module, "AlohaMini", stop_before_connection)

    with pytest.raises(StopBeforeConnection):
        host_module.main()

    record = trace_records(capsys.readouterr().out)[-1]
    assert record["event"] == "am1_left_elbow_trace_startup"
    assert record["effective_max_relative_target"] == 4.0
    assert record["motor"] == "arm_left_elbow_flex"


def test_trace_records_the_actual_am1_left_elbow_action_boundary(capsys):
    robot, left, events = make_action_robot(trace_am1_left_elbow=True)

    sent = robot.send_action(elbow_action())

    record = trace_records(capsys.readouterr().out)[-1]
    assert sent["arm_left_elbow_flex.pos"] == 14.0
    assert record["event"] == "am1_left_elbow_action_boundary"
    assert record["timestamp_ns"] > 0
    assert record["motor"] == "arm_left_elbow_flex"
    assert record["requested_normalized_target"] == 20.0
    assert record["relative_limiter_present_normalized"] == 10.0
    assert record["relative_limiter_target_normalized"] == 14.0
    assert record["final_left_bus_target_normalized"] == 14.0
    assert record["goal_position_sync_write"] == {
        "attempted": True,
        "sdk_transmit": "completed",
        "servo_acknowledgement": "sync-write supplies no servo acknowledgement",
    }
    assert record["readbacks"] == {
        "Goal_Position": {"normalized": 14.0},
        "Present_Position": {"raw": 10},
        "Present_Current": {"raw": 10, "ma": 65.0},
        "Torque_Enable": {"raw": 0},
        "Lock": {"raw": 0},
        "Operating_Mode": {"raw": 0},
    }
    assert left.read_calls == [
        ("Goal_Position", "arm_left_elbow_flex", True),
        ("Present_Position", "arm_left_elbow_flex", False),
        ("Present_Current", "arm_left_elbow_flex", False),
        ("Torque_Enable", "arm_left_elbow_flex", False),
        ("Lock", "arm_left_elbow_flex", False),
        ("Operating_Mode", "arm_left_elbow_flex", False),
    ]
    left_write = event_index(
        events,
        ("left", "sync_write", "Goal_Position", {"arm_left_elbow_flex": 14.0}, True),
    )
    body_zero = event_index(
        events,
        (
            "left",
            "sync_write",
            "Goal_Velocity",
            {"base_left_wheel": 0, "base_back_wheel": 0, "base_right_wheel": 0},
            True,
        ),
    )
    first_diagnostic_read = next(index for index, event in enumerate(events) if event[1] == "read")
    assert left_write < body_zero < first_diagnostic_read
    assert all("Phase" not in event for event in events)


def test_selected_trace_records_the_actual_am1_right_shoulder_action_boundary(capsys):
    robot, left, events = make_action_robot(trace_am1_left_elbow=False)
    right = attach_right_shoulder_bus(robot, events)
    robot.config.trace_am1_joint = "arm_right_shoulder_lift"

    sent = robot.send_action(right_shoulder_action())

    record = trace_records(capsys.readouterr().out)[-1]
    assert sent["arm_right_shoulder_lift.pos"] == 14.0
    assert record == {
        "event": "am1_joint_action_boundary",
        "timestamp_ns": record["timestamp_ns"],
        "motor": "arm_right_shoulder_lift",
        "motor_id": 2,
        "side": "right",
        "requested_normalized_target": 20.0,
        "relative_limiter_present_normalized": 10.0,
        "relative_limiter_target_normalized": 14.0,
        "final_bus_target_normalized": 14.0,
        "goal_position_sync_write": {
            "attempted": True,
            "sdk_transmit": "completed",
            "servo_acknowledgement": "sync-write supplies no servo acknowledgement",
        },
        "readbacks": {
            "Goal_Position": {"normalized": 14.0},
            "Present_Position": {"raw": 10},
            "Present_Current": {"raw": 10, "ma": 65.0},
            "Torque_Enable": {"raw": 0},
            "Lock": {"raw": 0},
            "Operating_Mode": {"raw": 0},
        },
    }
    assert record["timestamp_ns"] > 0
    assert left.read_calls == []
    assert right.read_calls == [
        ("Goal_Position", "arm_right_shoulder_lift", True),
        ("Present_Position", "arm_right_shoulder_lift", False),
        ("Present_Current", "arm_right_shoulder_lift", False),
        ("Torque_Enable", "arm_right_shoulder_lift", False),
        ("Lock", "arm_right_shoulder_lift", False),
        ("Operating_Mode", "arm_right_shoulder_lift", False),
    ]
    right_write = event_index(
        events,
        ("right", "sync_write", "Goal_Position", {"arm_right_shoulder_lift": 14.0}, True),
    )
    body_zero = event_index(
        events,
        (
            "left",
            "sync_write",
            "Goal_Velocity",
            {"base_left_wheel": 0, "base_back_wheel": 0, "base_right_wheel": 0},
            True,
        ),
    )
    first_diagnostic_read = next(index for index, event in enumerate(events) if event[1] == "read")
    assert right_write < body_zero < first_diagnostic_read
    assert all("Phase" not in event for event in events)


def test_selected_trace_numeric_capture_cannot_abort_an_action():
    class TraceHostileFloat(float):
        def __float__(self):
            raise ValueError("trace-only conversion must not run")

    robot, _, events = make_action_robot(trace_am1_left_elbow=False)
    attach_right_shoulder_bus(robot, events)
    robot.config.trace_am1_joint = "arm_right_shoulder_lift"

    sent = robot.send_action(right_shoulder_action(target=TraceHostileFloat(20.0)))

    assert sent["arm_right_shoulder_lift.pos"] == 14.0
    assert any(event[1:3] == ("sync_write", "Goal_Position") for event in events)
    assert any(event[1:3] == ("sync_write", "Goal_Velocity") for event in events)


def test_selected_trace_final_target_is_after_the_current_limiter(capsys):
    robot, _, events = make_action_robot(trace_am1_left_elbow=False)
    right = attach_right_shoulder_bus(robot, events)
    right.registers[("Present_Current", "arm_right_shoulder_lift")] = 300
    robot.config.trace_am1_joint = "arm_right_shoulder_lift"

    sent = robot.send_action(right_shoulder_action())

    record = trace_records(capsys.readouterr().out)[-1]
    assert record["relative_limiter_target_normalized"] == 14.0
    assert record["final_bus_target_normalized"] == 10.0
    assert record["readbacks"]["Goal_Position"] == {"normalized": 10}
    assert sent["arm_right_shoulder_lift.pos"] == 10


def test_selected_trace_output_failure_keeps_a_successful_action_successful(monkeypatch):
    robot, _, events = make_action_robot(trace_am1_left_elbow=False)
    attach_right_shoulder_bus(robot, events)
    robot.config.trace_am1_joint = "arm_right_shoulder_lift"

    def fail_serialization(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("selected trace serialization failed")

    monkeypatch.setattr(alohamini_module.json, "dumps", fail_serialization)

    sent = robot.send_action(right_shoulder_action())

    assert sent["arm_right_shoulder_lift.pos"] == 14.0
    assert any(event[1:3] == ("sync_write", "Goal_Position") for event in events)
    assert any(event[1:3] == ("sync_write", "Goal_Velocity") for event in events)


def test_selected_trace_diagnostic_conversion_failure_keeps_action_successful(capsys):
    class UnconvertibleCurrent:
        def __float__(self):
            raise ValueError("selected current conversion failed")

    robot, _, events = make_action_robot(trace_am1_left_elbow=False)
    right = attach_right_shoulder_bus(robot, events)
    right.read_sequences[("Present_Current", "arm_right_shoulder_lift")] = [
        10,
        UnconvertibleCurrent(),
    ]
    robot.config.trace_am1_joint = "arm_right_shoulder_lift"

    sent = robot.send_action(right_shoulder_action())

    record = trace_records(capsys.readouterr().out)[-1]
    assert sent["arm_right_shoulder_lift.pos"] == 14.0
    assert record["diagnostic_reads"] == {
        "error": "ValueError: selected current conversion failed"
    }
    assert any(event[1:3] == ("sync_write", "Goal_Position") for event in events)
    assert any(event[1:3] == ("sync_write", "Goal_Velocity") for event in events)


def test_selected_trace_can_reuse_the_reviewed_left_elbow_path(capsys):
    robot, left, events = make_action_robot(trace_am1_left_elbow=False)
    left.motors["arm_left_elbow_flex"] = SimpleNamespace(id=3)
    robot.config.trace_am1_joint = "arm_left_elbow_flex"

    sent = robot.send_action(elbow_action())

    record = trace_records(capsys.readouterr().out)[-1]
    assert sent["arm_left_elbow_flex.pos"] == 14.0
    assert record["event"] == "am1_joint_action_boundary"
    assert record["motor"] == "arm_left_elbow_flex"
    assert record["motor_id"] == 3
    assert record["side"] == "left"
    assert record["readbacks"]["Goal_Position"] == {"normalized": 14.0}
    assert all("Phase" not in event for event in events)


def test_selected_trace_rejects_an_unreviewed_direct_config(capsys):
    robot, _, events = make_action_robot(trace_am1_left_elbow=False)
    right = attach_right_elbow_bus(robot, events)
    robot.config.trace_am1_joint = "arm_right_elbow_flex"

    robot.send_action(bimanual_elbow_action())

    assert trace_records(capsys.readouterr().out) == []
    assert right.read_calls == []


def test_selected_trace_stays_off_for_an_ambiguous_direct_config(capsys):
    robot, _, events = make_action_robot(trace_am1_left_elbow=True)
    right = attach_right_shoulder_bus(robot, events)
    robot.config.trace_am1_joint = "arm_right_shoulder_lift"

    robot.send_action(right_shoulder_action())

    records = trace_records(capsys.readouterr().out)
    assert [record["event"] for record in records] == ["am1_left_elbow_action_boundary"]
    assert right.read_calls == []


@pytest.mark.parametrize(
    ("failure_stage", "failing_bus", "register", "selected_write", "body_write"),
    [
        (
            "left_goal_position",
            "left",
            "Goal_Position",
            {
                "attempted": False,
                "sdk_transmit": "not attempted",
                "status": "not attempted because left Goal_Position sync write failed",
                "servo_acknowledgement": "sync-write supplies no servo acknowledgement",
            },
            {
                "attempted": False,
                "status": "not attempted because left Goal_Position sync write failed",
            },
        ),
        (
            "right_goal_position",
            "right",
            "Goal_Position",
            {
                "attempted": True,
                "sdk_transmit": "failed",
                "error": "ConnectionError: right_goal_position failed",
                "servo_acknowledgement": "sync-write supplies no servo acknowledgement",
            },
            {
                "attempted": False,
                "status": "not attempted because right Goal_Position sync write failed",
            },
        ),
        (
            "base_goal_velocity",
            "left",
            "Goal_Velocity",
            {
                "attempted": True,
                "sdk_transmit": "completed",
                "servo_acknowledgement": "sync-write supplies no servo acknowledgement",
            },
            {
                "attempted": True,
                "sdk_transmit": "failed",
                "error": "ConnectionError: base_goal_velocity failed",
            },
        ),
    ],
)
def test_selected_right_trace_preserves_action_write_failures(
    capsys, failure_stage, failing_bus, register, selected_write, body_write
):
    robot, left, events = make_action_robot(trace_am1_left_elbow=False)
    right = attach_right_shoulder_bus(robot, events)
    robot.config.trace_am1_joint = "arm_right_shoulder_lift"
    original_error = ConnectionError(f"{failure_stage} failed")
    (right if failing_bus == "right" else left).sync_write_failures[register] = original_error

    with pytest.raises(ConnectionError) as raised:
        robot.send_action(right_shoulder_action())

    record = trace_records(capsys.readouterr().out)[-1]
    assert raised.value is original_error
    assert record["event"] == "am1_joint_action_boundary"
    assert record["motor"] == "arm_right_shoulder_lift"
    assert record["side"] == "right"
    assert record["action_write_failure"] == {"stage": failure_stage}
    assert record["goal_position_sync_write"] == selected_write
    assert record["body_goal_velocity_sync_write"] == body_write
    assert record["readbacks"] == {
        "status": "not attempted before successful body Goal_Velocity sync write"
    }
    assert left.read_calls == []
    assert right.read_calls == []
    assert all("Phase" not in event for event in events)


def test_trace_reports_sync_write_failure_and_reraises_original_error(capsys):
    robot, left, _ = make_action_robot(trace_am1_left_elbow=True)
    failure = ConnectionError("original sync write failure")
    left.registers[("Present_Current", "arm_left_elbow_flex")] = 300
    left.sync_write_failures["Goal_Position"] = failure

    with pytest.raises(ConnectionError, match="original sync write failure") as raised:
        robot.send_action(elbow_action())

    record = trace_records(capsys.readouterr().out)[-1]
    assert raised.value is failure
    assert record["goal_position_sync_write"] == {
        "attempted": True,
        "sdk_transmit": "failed",
        "error": "ConnectionError: original sync write failure",
        "servo_acknowledgement": "sync-write supplies no servo acknowledgement",
    }
    assert record["relative_limiter_target_normalized"] == 14.0
    assert record["final_left_bus_target_normalized"] == 10.0
    assert record["diagnostic_reads"] == "not attempted because Goal_Position sync write failed"


@pytest.mark.parametrize("trace_failure", ["serialization", "output"])
def test_trace_failure_cannot_replace_the_original_left_write_error(monkeypatch, trace_failure):
    robot, left, _ = make_action_robot(trace_am1_left_elbow=True)
    original_error = ConnectionError("original left sync write failure")
    left.sync_write_failures["Goal_Position"] = original_error

    def fail_trace(*args, **kwargs):
        del args, kwargs
        raise RuntimeError(f"{trace_failure} failed")

    if trace_failure == "serialization":
        monkeypatch.setattr(alohamini_module.json, "dumps", fail_trace)
    else:
        monkeypatch.setattr("builtins.print", fail_trace)

    with pytest.raises(ConnectionError) as raised:
        robot.send_action(elbow_action())

    assert raised.value is original_error


def test_trace_diagnostic_conversion_failure_keeps_successful_action_successful(capsys):
    class UnconvertibleCurrent:
        def __float__(self):
            raise ValueError("current conversion failed")

    robot, left, events = make_action_robot(trace_am1_left_elbow=True)
    left.read_sequences[("Present_Current", "arm_left_elbow_flex")] = [10, UnconvertibleCurrent()]

    sent = robot.send_action(elbow_action())

    record = trace_records(capsys.readouterr().out)[-1]
    assert sent["arm_left_elbow_flex.pos"] == 14.0
    assert record["diagnostic_reads"] == {"error": "ValueError: current conversion failed"}
    assert any(event[1:3] == ("sync_write", "Goal_Position") for event in events)
    assert any(event[1:3] == ("sync_write", "Goal_Velocity") for event in events)


def test_trace_output_failure_after_successful_writes_keeps_action_successful(monkeypatch):
    robot, left, events = make_action_robot(trace_am1_left_elbow=True)

    def fail_serialization(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("trace serialization failed")

    monkeypatch.setattr(alohamini_module.json, "dumps", fail_serialization)

    sent = robot.send_action(elbow_action())

    assert sent["arm_left_elbow_flex.pos"] == 14.0
    assert any(event[1:3] == ("sync_write", "Goal_Position") for event in events)
    assert any(event[1:3] == ("sync_write", "Goal_Velocity") for event in events)


@pytest.mark.parametrize(
    ("failing_bus", "register", "stage", "right_write", "body_write"),
    [
        (
            "right",
            "Goal_Position",
            "right_goal_position",
            {"attempted": True, "sdk_transmit": "failed", "error": "ConnectionError: right_goal_position failed"},
            {
                "attempted": False,
                "status": "not attempted because right Goal_Position sync write failed",
            },
        ),
        (
            "left",
            "Goal_Velocity",
            "base_goal_velocity",
            {"attempted": True, "sdk_transmit": "completed"},
            {
                "attempted": True,
                "sdk_transmit": "failed",
                "error": "ConnectionError: base_goal_velocity failed",
            },
        ),
    ],
)
def test_trace_reports_later_write_failures_without_early_readbacks(
    capsys, failing_bus, register, stage, right_write, body_write
):
    robot, left, events = make_action_robot(trace_am1_left_elbow=True)
    right = attach_right_elbow_bus(robot, events)
    original_error = ConnectionError(f"{stage} failed")
    (right if failing_bus == "right" else left).sync_write_failures[register] = original_error

    with pytest.raises(ConnectionError) as raised:
        robot.send_action(bimanual_elbow_action())

    record = trace_records(capsys.readouterr().out)[-1]
    assert raised.value is original_error
    assert record["goal_position_sync_write"] == {
        "attempted": True,
        "sdk_transmit": "completed",
        "servo_acknowledgement": "sync-write supplies no servo acknowledgement",
    }
    assert record["action_write_failure"] == {"stage": stage}
    assert record["right_goal_position_sync_write"] == right_write
    assert record["body_goal_velocity_sync_write"] == body_write
    assert record["readbacks"] == {
        "status": "not attempted before successful body Goal_Velocity sync write"
    }
    assert record.get("diagnostic_reads") != "not attempted because body Goal_Velocity sync write failed"
    assert left.read_calls == []
    assert all("Phase" not in event for event in events)


class UnprintableTraceError(ConnectionError):
    def __str__(self):
        raise RuntimeError("exception string conversion must not run outside trace protection")


@pytest.mark.parametrize(
    ("failure_stage", "failing_bus", "register"),
    [
        ("left_goal_position", "left", "Goal_Position"),
        ("right_goal_position", "right", "Goal_Position"),
        ("base_goal_velocity", "left", "Goal_Velocity"),
    ],
)
def test_unprintable_write_error_preserves_original_identity(
    capsys, failure_stage, failing_bus, register
):
    robot, left, events = make_action_robot(trace_am1_left_elbow=True)
    right = attach_right_elbow_bus(robot, events) if failure_stage != "left_goal_position" else None
    original_error = UnprintableTraceError()
    (right if failing_bus == "right" else left).sync_write_failures[register] = original_error

    with pytest.raises(UnprintableTraceError) as raised:
        robot.send_action(bimanual_elbow_action() if right else elbow_action())

    record = trace_records(capsys.readouterr().out)[-1]
    assert raised.value is original_error
    assert "UnprintableTraceError: <unavailable>" in json.dumps(record)
    if failure_stage == "left_goal_position":
        assert record["goal_position_sync_write"]["sdk_transmit"] == "failed"
    else:
        assert record["action_write_failure"]["stage"] == failure_stage


def test_unprintable_diagnostic_error_after_successful_writes_keeps_action_successful(
    monkeypatch, capsys
):
    robot, left, events = make_action_robot(trace_am1_left_elbow=True)
    original_error = UnprintableTraceError()

    def fail_diagnostics():
        raise original_error

    monkeypatch.setattr(robot, "_read_am1_left_elbow_trace_registers", fail_diagnostics)

    sent = robot.send_action(elbow_action())

    record = trace_records(capsys.readouterr().out)[-1]
    assert sent["arm_left_elbow_flex.pos"] == 14.0
    assert record["diagnostic_reads"] == {"error": "UnprintableTraceError: <unavailable>"}
    assert any(event[1:3] == ("sync_write", "Goal_Position") for event in events)
    assert any(event[1:3] == ("sync_write", "Goal_Velocity") for event in events)


@pytest.mark.parametrize(
    ("robot_model", "trace_enabled"),
    [("alohamini1", False), ("alohamini2", False), ("alohamini2", True), ("alohamini2pro", False), ("alohamini2pro", True)],
)
def test_trace_is_default_off_and_non_am1_models_never_emit_or_read_diagnostics(
    capsys, robot_model, trace_enabled
):
    robot, _, events = make_action_robot(
        trace_am1_left_elbow=trace_enabled,
        robot_model=robot_model,
    )

    robot.send_action(elbow_action())

    assert trace_records(capsys.readouterr().out) == []
    assert not [event for event in events if event[1] == "read"]
    assert all("Phase" not in event for event in events)


@pytest.mark.parametrize(
    ("robot_model", "selected_motor"),
    [
        ("alohamini1", None),
        ("alohamini2", "arm_right_shoulder_lift"),
        ("alohamini2pro", "arm_right_shoulder_lift"),
    ],
)
def test_selected_trace_is_default_off_and_never_applies_to_am2_models(
    capsys, robot_model, selected_motor
):
    robot, left, events = make_action_robot(
        trace_am1_left_elbow=False,
        robot_model=robot_model,
    )
    right = attach_right_shoulder_bus(robot, events)
    robot.config.trace_am1_joint = selected_motor

    robot.send_action(right_shoulder_action())

    assert trace_records(capsys.readouterr().out) == []
    assert left.read_calls == []
    assert right.read_calls == []
    assert all("Phase" not in event for event in events)


def test_alohamini1_bus_defaults_do_not_read_or_write_phase():
    events = []
    bus = FakeBus("left", ("motor",), events)
    robot = AlohaMini.__new__(AlohaMini)
    robot.config = SimpleNamespace(robot_model="alohamini1")

    robot._configure_bus_defaults(bus)

    assert all("Phase" not in event for event in events)


def make_lift_axis(events: list[tuple]) -> tuple[LiftAxis, FakeBus]:
    bus = FakeBus("left", ("lift_axis",), events)
    config = LiftAxisConfig(
        home_down_speed=200,
        home_timeout_s=1.0,
        home_stall_samples=2,
        home_min_motion_ticks=2.0,
        home_poll_interval_s=0.0,
    )
    return LiftAxis(config, bus_left=bus, bus_right=None), bus


def test_unhomed_lift_commands_only_request_zero_velocity():
    events = []
    lift, _ = make_lift_axis(events)

    lift.apply_action({"lift_axis.height_mm": 100.0})
    lift.apply_action({"lift_axis.vel": 200.0})

    goal_writes = [event for event in events if event[1:3] == ("write", "Goal_Velocity")]
    assert goal_writes
    assert all(event[-1] == 0 for event in goal_writes)
    assert not lift.is_homed


def test_lift_home_uses_positive_raw_downward_velocity_and_finishes_zeroed():
    events = []
    lift, bus = make_lift_axis(events)
    bus.read_sequences[("Present_Current", "lift_axis")] = [100, 100]

    result = lift.home(speed_raw=200, timeout_s=1.0)

    positive_index = event_index(events, ("left", "write", "Goal_Velocity", "lift_axis", 200))
    torque_index = event_index(events, ("left", "write", "Torque_Enable", "lift_axis", 1))
    zeros_before_torque = [
        index
        for index, event in enumerate(events)
        if event == ("left", "write", "Goal_Velocity", "lift_axis", 0) and index < torque_index
    ]
    assert zeros_before_torque
    assert positive_index > torque_index
    assert [event for event in events if event[1:3] == ("write", "Goal_Velocity")][-1][-1] == 0
    assert bus.registers[("Torque_Enable", "lift_axis")] == 1
    assert result.stop_reason == "current threshold"
    assert lift.is_homed


def test_lift_home_timeout_finishes_zeroed_and_torque_disabled(monkeypatch):
    events = []
    lift, bus = make_lift_axis(events)
    bus.position_step = 10
    bus.read_sequences[("Present_Current", "lift_axis")] = [0]
    monotonic_values = iter((0.0, 0.0, 2.0))
    monkeypatch.setattr(
        "lerobot.robots.alohamini.lift_axis.time.monotonic",
        lambda: next(monotonic_values),
    )

    with pytest.raises(TimeoutError):
        lift.home(speed_raw=200, timeout_s=1.0)

    goal_writes = [event for event in events if event[1:3] == ("write", "Goal_Velocity")]
    assert any(event[-1] == 200 for event in goal_writes)
    assert goal_writes[-1][-1] == 0
    assert bus.registers[("Torque_Enable", "lift_axis")] == 0
    assert not lift.is_homed


@pytest.mark.parametrize("failure", [RuntimeError("position read failed"), KeyboardInterrupt()])
def test_lift_home_exception_and_interrupt_finish_zeroed_and_torque_disabled(failure):
    events = []
    lift, bus = make_lift_axis(events)
    bus.read_sequences[("Present_Position", "lift_axis")] = [0, failure]

    with pytest.raises(type(failure)):
        lift.home(speed_raw=200, timeout_s=1.0)

    goal_writes = [event for event in events if event[1:3] == ("write", "Goal_Velocity")]
    assert any(event[-1] == 200 for event in goal_writes)
    assert goal_writes[-1][-1] == 0
    assert bus.registers[("Torque_Enable", "lift_axis")] == 0
    assert not lift.is_homed
