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

import json
from types import SimpleNamespace

import pytest

from lerobot.motors import MotorCalibration
from lerobot.robots.alohamini import alohamini_host
from lerobot.robots.alohamini.alohamini import AlohaMini
from lerobot.robots.alohamini.alohamini_host import make_parser
from lerobot.robots.alohamini.config_alohamini import AlohaMiniConfig


RIGHT_WRIST = "arm_right_wrist_flex"


def calibration(*, motor_id: int, drive_mode: int = 0) -> dict[str, int]:
    return {
        "id": motor_id,
        "drive_mode": drive_mode,
        "homing_offset": 0,
        "range_min": 1000,
        "range_max": 3000,
    }


AM1_ARM_JOINT_IDS = {
    "shoulder_pan": 1,
    "shoulder_lift": 2,
    "elbow_flex": 3,
    "wrist_flex": 4,
    "wrist_roll": 5,
    "gripper": 6,
}


def make_unconnected_robot(tmp_path, robot_model: str) -> AlohaMini:
    calibration_path = tmp_path / "AlohaMiniRobot.json"
    calibration_data = {
        f"arm_{side}_{joint}": calibration(motor_id=motor_id)
        for side in ("left", "right")
        for joint, motor_id in AM1_ARM_JOINT_IDS.items()
    }
    calibration_data.update(
        {
            "base_left_wheel": calibration(motor_id=8),
            "base_back_wheel": calibration(motor_id=9),
            "base_right_wheel": calibration(motor_id=10),
            "lift_axis": calibration(motor_id=11),
        }
    )
    calibration_path.write_text(
        json.dumps(calibration_data),
        encoding="utf-8",
    )
    return AlohaMini(
        AlohaMiniConfig(
            id="AlohaMiniRobot",
            robot_model=robot_model,
            cameras={},
            calibration_dir=tmp_path,
        )
    )


def test_am1_right_wrist_runtime_direction_is_symmetric_and_does_not_rewrite_calibration(tmp_path):
    calibration_path = tmp_path / "AlohaMiniRobot.json"
    robot = make_unconnected_robot(tmp_path, "alohamini1")

    assert robot.calibration[RIGHT_WRIST].drive_mode == 0
    assert json.loads(calibration_path.read_text(encoding="utf-8"))[RIGHT_WRIST]["drive_mode"] == 0
    assert robot.right_bus.calibration[RIGHT_WRIST].drive_mode == 1
    for name, entry in robot.right_bus.calibration.items():
        if name != RIGHT_WRIST:
            assert entry == robot.calibration[name]
    for name, entry in robot.left_bus.calibration.items():
        assert entry == robot.calibration[name]

    assert robot.right_bus._normalize({4: 1000})[4] == pytest.approx(100.0)
    assert robot.right_bus._unnormalize({4: 100.0})[4] == 1000
    assert robot.right_bus._normalize({1: 1000})[1] == pytest.approx(-100.0)


@pytest.mark.parametrize("robot_model", ["alohamini2", "alohamini2pro"])
def test_am2_models_do_not_reflect_the_right_wrist_runtime_direction(tmp_path, robot_model):
    robot = make_unconnected_robot(tmp_path, robot_model)

    assert robot.right_bus.calibration[RIGHT_WRIST].drive_mode == 0
    assert robot.right_bus._normalize({4: 1000})[4] == pytest.approx(-100.0)
    assert robot.right_bus._unnormalize({4: 100.0})[4] == 3000


def test_am1_right_wrist_runtime_direction_is_reapplied_after_calibration_assignment():
    robot = AlohaMini.__new__(AlohaMini)
    robot.config = SimpleNamespace(robot_model="alohamini1")
    robot.right_bus = SimpleNamespace(
        calibration={
            RIGHT_WRIST: MotorCalibration(
                id=4,
                drive_mode=0,
                homing_offset=-1609,
                range_min=1461,
                range_max=3167,
            )
        }
    )

    robot._apply_runtime_joint_directions()

    assert robot.right_bus.calibration[RIGHT_WRIST] == MotorCalibration(
        id=4,
        drive_mode=1,
        homing_offset=-1609,
        range_min=1461,
        range_max=3167,
    )


class ActionBus:
    def __init__(
        self,
        positions: dict[str, float],
        *,
        base: bool = False,
        currents: dict[str, float] | None = None,
    ):
        self.positions = positions
        self.currents = currents or dict.fromkeys(positions, 0.0)
        self.motors = dict.fromkeys(positions)
        if base:
            self.motors.update(
                dict.fromkeys(("base_left_wheel", "base_back_wheel", "base_right_wheel"))
            )
        self.is_connected = True
        self.writes: list[tuple[str, dict[str, float]]] = []

    def sync_read(self, register: str, motors: list[str]) -> dict[str, float]:
        if register == "Present_Position":
            return {motor: self.positions[motor] for motor in motors}
        if register == "Present_Current":
            return {motor: self.currents[motor] for motor in motors}
        raise AssertionError(f"Unexpected read: {register}")

    def sync_write(self, register: str, values: dict[str, float]) -> None:
        self.writes.append((register, values))


def make_action_robot(*, right_wrist_current_raw: float = 0.0) -> AlohaMini:
    robot = AlohaMini.__new__(AlohaMini)
    robot.config = SimpleNamespace(max_relative_target=20.0)
    robot.left_bus = ActionBus({"arm_left_wrist_flex": 0.0}, base=True)
    robot.right_bus = ActionBus(
        {RIGHT_WRIST: 10.0},
        currents={RIGHT_WRIST: right_wrist_current_raw},
    )
    robot.left_arm_motors = ["arm_left_wrist_flex"]
    robot.right_arm_motors = [RIGHT_WRIST]
    robot.base_motors = ["base_left_wheel", "base_back_wheel", "base_right_wheel"]
    robot.cameras = {}
    robot.wheel_radius = 0.05
    robot.base_radius = 0.125
    robot.lift = SimpleNamespace(apply_action=lambda action: None)
    robot.logs = {}
    robot._gripper_current_limit_ma = 500.0
    robot._gripper_release_margin = 1.0
    robot._gripper_hold_goal = {}
    robot._gripper_hold_direction = {}
    robot._gripper_open_direction = {}
    robot._gripper_hold_close_step = 3
    robot._joint_current_limit_ma = 1800.0
    robot._joint_release_margin = 1.0
    robot._joint_hold_goal = {}
    robot._joint_hold_direction = {}
    return robot


def test_action_diagnostics_report_requested_limited_and_observed_right_wrist_values():
    robot = make_action_robot()

    sent = robot.send_action(
        {
            "arm_left_wrist_flex.pos": 0.0,
            "arm_right_wrist_flex.pos": 50.0,
            "x.vel": 0.0,
            "y.vel": 0.0,
            "theta.vel": 0.0,
            "lift_axis.height_mm": 0.0,
        }
    )

    assert sent["arm_right_wrist_flex.pos"] == pytest.approx(30.0)
    assert robot.logs["action_diagnostics"] == {
        "target_limited": True,
        "right_wrist_requested": 50.0,
        "right_wrist_final": 30.0,
        "right_wrist_observed": 10.0,
    }


def test_action_diagnostics_report_current_limited_final_target():
    robot = make_action_robot(right_wrist_current_raw=300.0)

    sent = robot.send_action(
        {
            "arm_left_wrist_flex.pos": 0.0,
            "arm_right_wrist_flex.pos": 20.0,
            "x.vel": 0.0,
            "y.vel": 0.0,
            "theta.vel": 0.0,
            "lift_axis.height_mm": 0.0,
        }
    )

    assert sent["arm_right_wrist_flex.pos"] == pytest.approx(10.0)
    assert robot.logs["action_diagnostics"] == {
        "target_limited": True,
        "right_wrist_requested": 20.0,
        "right_wrist_final": 10.0,
        "right_wrist_observed": 10.0,
    }


class FakeClock:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_host_command_gap_uses_receive_timestamp_before_action_processing():
    clock = FakeClock(10.0)
    state = alohamini_host.HostCommandState(
        watchdog_timeout_ms=1000,
        diagnostics_enabled=True,
        clock=clock,
    )

    clock.now = 20.0
    state.record_command({}, received_at=10.1)
    clock.now = 30.0
    state.record_command({}, received_at=10.25)

    assert state.snapshot()["last_receive_gap_ms"] == pytest.approx(150.0)
    clock.now = 30.999
    assert not state.watchdog_due()
    clock.now = 31.001
    assert state.watchdog_due()


def test_host_cadence_counts_every_receive_gap_over_watchdog_and_reports_last_offender():
    clock = FakeClock(100.0)
    state = alohamini_host.HostCommandState(
        watchdog_timeout_ms=1000,
        diagnostics_enabled=True,
        clock=clock,
    )

    for received_at in (100.1, 100.9, 101.901, 102.901, 104.101):
        clock.now = received_at
        state.record_command({}, received_at=received_at)

    report_prefix = "[HOST CADENCE] "
    report = json.loads(state.format_report().removeprefix(report_prefix))
    assert report["receive_gap_over_watchdog_count"] == 2
    assert report["last_receive_gap_over_watchdog_sequence"] == 5
    assert report["last_receive_gap_over_watchdog_ms"] == pytest.approx(1200.0)


def test_host_command_state_uses_monotonic_intervals_and_latches_watchdog():
    clock = FakeClock(100.0)
    state = alohamini_host.HostCommandState(
        watchdog_timeout_ms=1000,
        diagnostics_enabled=True,
        clock=clock,
    )

    clock.now = 100.1
    state.record_command(
        {
            "target_limited": False,
            "right_wrist_requested": 12.0,
            "right_wrist_final": 12.0,
            "right_wrist_observed": 11.5,
        }
    )
    clock.now = 100.35
    state.record_command(
        {
            "target_limited": True,
            "right_wrist_requested": 50.0,
            "right_wrist_final": 30.0,
            "right_wrist_observed": 10.0,
        }
    )

    assert state.snapshot() == {
        "command_sequence": 2,
        "last_receive_gap_ms": pytest.approx(250.0),
        "max_receive_gap_ms": pytest.approx(250.0),
        "receive_gap_over_watchdog_count": 0,
        "last_receive_gap_over_watchdog_sequence": None,
        "last_receive_gap_over_watchdog_ms": None,
        "watchdog_events": 0,
        "target_limited": True,
        "right_wrist_requested": 50.0,
        "right_wrist_final": 30.0,
        "right_wrist_observed": 10.0,
    }

    clock.now = 101.35
    assert not state.watchdog_due()
    clock.now = 101.351
    assert state.watchdog_due()
    assert not state.watchdog_due()
    report_prefix = "[HOST CADENCE] "
    report = state.format_report()
    assert report.startswith(report_prefix)
    report_state = json.loads(report.removeprefix(report_prefix))
    assert report_state == {
        "command_sequence": 2,
        "last_receive_gap_ms": pytest.approx(250.0),
        "max_receive_gap_ms": pytest.approx(250.0),
        "receive_gap_over_watchdog_count": 0,
        "last_receive_gap_over_watchdog_sequence": None,
        "last_receive_gap_over_watchdog_ms": None,
        "watchdog_events": 1,
        "target_limited": True,
        "right_wrist_requested": 50.0,
        "right_wrist_final": 30.0,
        "right_wrist_observed": 10.0,
    }

    clock.now = 101.4
    state.record_command({})
    clock.now = 102.39
    assert not state.watchdog_due()


def test_host_cadence_instrumentation_is_default_off():
    args = make_parser().parse_args([])
    assert not args.profile_cadence

    clock = FakeClock(1.0)
    state = alohamini_host.HostCommandState(
        watchdog_timeout_ms=1000,
        diagnostics_enabled=args.profile_cadence,
        clock=clock,
    )
    clock.now = 1.1
    state.record_command({"target_limited": True})

    assert state.snapshot() is None
