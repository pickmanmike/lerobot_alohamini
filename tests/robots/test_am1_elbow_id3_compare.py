#!/usr/bin/env python

# Copyright 2024 The HuggingFace Inc. team. All rights reserved.
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

import importlib.util
import json
import os
from pathlib import Path
from types import ModuleType
from typing import Any


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "examples" / "alohamini" / "compare_am1_elbow_id3.py"
)

EXPECTED_REGISTERS = (
    "Firmware_Major_Version",
    "Firmware_Minor_Version",
    "Model_Number",
    "ID",
    "Baud_Rate",
    "Return_Delay_Time",
    "Response_Status_Level",
    "Min_Position_Limit",
    "Max_Position_Limit",
    "Max_Temperature_Limit",
    "Max_Voltage_Limit",
    "Min_Voltage_Limit",
    "Max_Torque_Limit",
    "Unloading_Condition",
    "LED_Alarm_Condition",
    "P_Coefficient",
    "D_Coefficient",
    "I_Coefficient",
    "Minimum_Startup_Force",
    "CW_Dead_Zone",
    "CCW_Dead_Zone",
    "Protection_Current",
    "Angular_Resolution",
    "Homing_Offset",
    "Operating_Mode",
    "Protective_Torque",
    "Protection_Time",
    "Overload_Torque",
    "Velocity_closed_loop_P_proportional_coefficient",
    "Over_Current_Protection_Time",
    "Velocity_closed_loop_I_integral_coefficient",
    "Torque_Enable",
    "Acceleration",
    "Goal_Position",
    "Goal_Time",
    "Goal_Velocity",
    "Torque_Limit",
    "Lock",
    "Present_Position",
    "Present_Velocity",
    "Present_Load",
    "Present_Voltage",
    "Present_Temperature",
    "Status",
    "Moving",
    "Present_Current",
    "Goal_Position_2",
    "Moving_Velocity",
    "Moving_Velocity_Threshold",
    "DTs",
    "Velocity_Unit_factor",
    "Hts",
    "Maximum_Velocity_Limit",
    "Maximum_Acceleration",
    "Acceleration_Multiplier ",
)


def _load_module() -> ModuleType:
    assert SCRIPT_PATH.is_file(), f"missing implementation: {SCRIPT_PATH}"
    spec = importlib.util.spec_from_file_location("compare_am1_elbow_id3", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_calibration(path: Path) -> dict[str, dict[str, int]]:
    calibration = {
        "arm_left_elbow_flex": {
            "id": 3,
            "drive_mode": 0,
            "homing_offset": 101,
            "range_min": 950,
            "range_max": 3100,
        },
        "arm_right_elbow_flex": {
            "id": 3,
            "drive_mode": 1,
            "homing_offset": -87,
            "range_min": 3050,
            "range_max": 900,
        },
        "base_left_wheel": {
            "id": 8,
            "drive_mode": 0,
            "homing_offset": 0,
            "range_min": 0,
            "range_max": 4095,
        },
    }
    path.write_text(json.dumps(calibration), encoding="utf-8")
    return calibration


class FakeBus:
    def __init__(
        self,
        *,
        side: str,
        values: dict[str, int],
        connect_error: Exception | None = None,
        read_error_at: str | None = None,
        disconnect_error: Exception | None = None,
    ) -> None:
        self.side = side
        self.values = values
        self.connect_error = connect_error
        self.read_error_at = read_error_at
        self.disconnect_error = disconnect_error
        self.is_connected = False
        self.connect_calls: list[tuple[()]] = []
        self.read_calls: list[tuple[str, str, bool]] = []
        self.disconnect_calls: list[bool] = []

    def connect(self) -> None:
        self.connect_calls.append(())
        self.is_connected = True
        if self.connect_error is not None:
            raise self.connect_error

    def read(self, register: str, motor: str, *, normalize: bool = True) -> int:
        self.read_calls.append((register, motor, normalize))
        if register == self.read_error_at:
            raise RuntimeError(f"{self.side} read exploded")
        return self.values[register]

    def disconnect(self, *, disable_torque: bool = True) -> None:
        self.disconnect_calls.append(disable_torque)
        self.is_connected = False
        if self.disconnect_error is not None:
            raise self.disconnect_error

    def _forbidden(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("the read-only diagnostic invoked a forbidden bus operation")

    write = _forbidden
    sync_write = _forbidden
    configure_motors = _forbidden
    enable_torque = _forbidden
    disable_torque = _forbidden
    write_calibration = _forbidden


def _values(offset: int) -> dict[str, int]:
    return {register: offset + index for index, register in enumerate(EXPECTED_REGISTERS)}


def test_comparator_registers_match_authoritative_non_phase_table_order() -> None:
    from lerobot.motors.feetech.tables import STS_SMS_SERIES_CONTROL_TABLE

    module = _load_module()
    expected_registers = tuple(
        register for register in STS_SMS_SERIES_CONTROL_TABLE if register != "Phase"
    )

    assert tuple(module.REGISTERS) == expected_registers
    assert len(expected_registers) == 55
    assert "Phase" not in module.REGISTERS
    assert "Acceleration_Multiplier " in module.REGISTERS


def test_refusal_requires_exact_uppercase_read_before_creating_a_bus(tmp_path: Path) -> None:
    module = _load_module()
    events: list[str] = []

    def input_fn(prompt: str) -> str:
        events.append(f"prompt:{prompt}")
        return "read"

    def bus_factory(**kwargs: Any) -> FakeBus:
        events.append("bus-created")
        raise AssertionError("a refused diagnostic must not create a bus")

    output: list[str] = []
    status = module.run_diagnostic(
        left_port="LEFT",
        right_port="RIGHT",
        calibration_file=tmp_path / "missing.json",
        input_fn=input_fn,
        output_fn=output.append,
        error_fn=lambda message: None,
        bus_factory=bus_factory,
    )

    assert status == 2
    assert len(events) == 1 and events[0].startswith("prompt:")
    assert output == [
        "Safety refusal: enter exact uppercase READ to permit read-only elbow register access; no bus was opened."
    ]


def test_refusal_rejects_identical_port_arguments_before_calibration_or_bus_access(
    tmp_path: Path,
) -> None:
    module = _load_module()

    def bus_factory(**kwargs: Any) -> FakeBus:
        raise AssertionError("duplicate ports must be rejected before bus construction")

    output: list[str] = []
    status = module.run_diagnostic(
        left_port="SAME",
        right_port="SAME",
        calibration_file=tmp_path / "missing.json",
        input_fn=lambda prompt: "READ",
        output_fn=output.append,
        error_fn=lambda message: None,
        bus_factory=bus_factory,
    )

    assert status == 2
    assert output == [
        "Safety refusal: left and right ports identify the same path or filesystem object; "
        "no bus was opened."
    ]


def test_refusal_rejects_distinct_paths_to_the_same_filesystem_object_before_bus_access(
    tmp_path: Path,
) -> None:
    module = _load_module()
    left_port = tmp_path / "left-port"
    right_port = tmp_path / "right-port"
    left_port.touch()
    os.link(left_port, right_port)

    def bus_factory(**kwargs: Any) -> FakeBus:
        raise AssertionError("aliases of one device must be rejected before bus construction")

    output: list[str] = []
    status = module.run_diagnostic(
        left_port=str(left_port),
        right_port=str(right_port),
        calibration_file=tmp_path / "missing.json",
        input_fn=lambda prompt: "READ",
        output_fn=output.append,
        error_fn=lambda message: None,
        bus_factory=bus_factory,
    )

    assert status == 2
    assert output == [
        "Safety refusal: left and right ports identify the same path or filesystem object; "
        "no bus was opened."
    ]


def test_success_reads_only_raw_id3_registers_and_reports_calibration_differences(
    tmp_path: Path,
) -> None:
    module = _load_module()
    calibration_file = tmp_path / "AlohaMiniRobot.json"
    calibration = _write_calibration(calibration_file)
    events: list[str] = []
    buses: dict[str, FakeBus] = {}
    factory_calls: list[dict[str, Any]] = []

    def input_fn(prompt: str) -> str:
        events.append("confirmed")
        return "READ"

    def bus_factory(**kwargs: Any) -> FakeBus:
        factory_calls.append(kwargs)
        side = "left" if kwargs["port"] == "LEFT" else "right"
        events.append(f"created:{side}")
        bus = FakeBus(side=side, values=_values(100 if side == "left" else 200))
        buses[side] = bus
        return bus

    output: list[str] = []
    errors: list[str] = []
    status = module.run_diagnostic(
        left_port="LEFT",
        right_port="RIGHT",
        calibration_file=calibration_file,
        input_fn=input_fn,
        output_fn=output.append,
        error_fn=errors.append,
        bus_factory=bus_factory,
    )

    assert status == 0
    assert not errors
    assert events[0] == "confirmed"
    assert [call["port"] for call in factory_calls] == ["LEFT", "RIGHT"]
    for call in factory_calls:
        assert set(call) == {"port", "motors"}
        assert set(call["motors"]) == {"elbow_flex"}
        motor = call["motors"]["elbow_flex"]
        assert (motor.id, motor.model, motor.norm_mode.value) == (3, "sts3215", "range_m100_100")

    expected_reads = [(register, "elbow_flex", False) for register in EXPECTED_REGISTERS]
    assert tuple(module.REGISTERS) == EXPECTED_REGISTERS
    assert "Phase" not in module.REGISTERS
    assert buses["left"].read_calls == expected_reads
    assert buses["right"].read_calls == expected_reads
    assert buses["left"].disconnect_calls == [False]
    assert buses["right"].disconnect_calls == [False]

    assert len(output) == 1
    result = json.loads(output[0])
    assert result["status"] == "completed"
    assert result["diagnostic"] == "am1_follower_elbow_id3_read_only_compare"
    assert result["motor"] == {"id": 3, "model": "sts3215", "normalization": "range_m100_100"}
    assert result["calibration_file"] == str(calibration_file)
    assert result["calibration"]["left"] == calibration["arm_left_elbow_flex"]
    assert result["calibration"]["right"] == calibration["arm_right_elbow_flex"]
    assert result["calibration"]["differences"] == {
        "drive_mode": {"left": 0, "right": 1},
        "homing_offset": {"left": 101, "right": -87},
        "range_max": {"left": 3100, "right": 900},
        "range_min": {"left": 950, "right": 3050},
    }
    assert result["registers"]["left"]["Goal_Position"] == _values(100)["Goal_Position"]
    assert result["registers"]["right"]["Present_Current"] == _values(200)["Present_Current"]
    assert result["safety"] == {
        "confirmation": "READ",
        "phase_read": False,
        "writes_performed": False,
    }
    assert output[0] == json.dumps(result, indent=2, sort_keys=True)


def test_partial_connection_failure_closes_every_open_bus_without_disabling_torque(
    tmp_path: Path,
) -> None:
    module = _load_module()
    calibration_file = tmp_path / "AlohaMiniRobot.json"
    _write_calibration(calibration_file)
    buses: dict[str, FakeBus] = {}

    def bus_factory(**kwargs: Any) -> FakeBus:
        side = "left" if kwargs["port"] == "LEFT" else "right"
        bus = FakeBus(
            side=side,
            values=_values(0),
            connect_error=RuntimeError("right handshake failed") if side == "right" else None,
        )
        buses[side] = bus
        return bus

    errors: list[str] = []
    status = module.run_diagnostic(
        left_port="LEFT",
        right_port="RIGHT",
        calibration_file=calibration_file,
        input_fn=lambda prompt: "READ",
        output_fn=lambda message: None,
        error_fn=errors.append,
        bus_factory=bus_factory,
    )

    assert status == 1
    assert buses["left"].disconnect_calls == [False]
    assert buses["right"].disconnect_calls == [False]
    error = json.loads(errors[0])
    assert error["error"] == {"message": "right handshake failed", "type": "RuntimeError"}
    assert error["cleanup_errors"] == []


def test_read_failure_closes_both_buses_and_cleanup_does_not_hide_original_error(
    tmp_path: Path,
) -> None:
    module = _load_module()
    calibration_file = tmp_path / "AlohaMiniRobot.json"
    _write_calibration(calibration_file)
    buses: dict[str, FakeBus] = {}

    def bus_factory(**kwargs: Any) -> FakeBus:
        side = "left" if kwargs["port"] == "LEFT" else "right"
        bus = FakeBus(
            side=side,
            values=_values(0),
            read_error_at="Goal_Position" if side == "left" else None,
            disconnect_error=RuntimeError("left close failed") if side == "left" else None,
        )
        buses[side] = bus
        return bus

    errors: list[str] = []
    status = module.run_diagnostic(
        left_port="LEFT",
        right_port="RIGHT",
        calibration_file=calibration_file,
        input_fn=lambda prompt: "READ",
        output_fn=lambda message: None,
        error_fn=errors.append,
        bus_factory=bus_factory,
    )

    assert status == 1
    assert buses["left"].disconnect_calls == [False]
    assert buses["right"].disconnect_calls == [False]
    error = json.loads(errors[0])
    assert error["error"] == {"message": "left read exploded", "type": "RuntimeError"}
    assert error["cleanup_errors"] == [
        {"message": "left close failed", "side": "left", "type": "RuntimeError"}
    ]


def test_disconnect_failure_after_success_reports_cleanup_error_without_completed_result(
    tmp_path: Path,
) -> None:
    module = _load_module()
    calibration_file = tmp_path / "AlohaMiniRobot.json"
    _write_calibration(calibration_file)
    buses: dict[str, FakeBus] = {}

    def bus_factory(**kwargs: Any) -> FakeBus:
        side = "left" if kwargs["port"] == "LEFT" else "right"
        bus = FakeBus(
            side=side,
            values=_values(0),
            disconnect_error=RuntimeError("right close failed") if side == "right" else None,
        )
        buses[side] = bus
        return bus

    output: list[str] = []
    errors: list[str] = []
    status = module.run_diagnostic(
        left_port="LEFT",
        right_port="RIGHT",
        calibration_file=calibration_file,
        input_fn=lambda prompt: "READ",
        output_fn=output.append,
        error_fn=errors.append,
        bus_factory=bus_factory,
    )

    assert status == 1
    assert output == []
    assert buses["left"].disconnect_calls == [False]
    assert buses["right"].disconnect_calls == [False]
    error = json.loads(errors[0])
    assert error["status"] == "error"
    assert error["error"] == {
        "message": "Register comparison completed, but bus cleanup failed.",
        "type": "CleanupError",
    }
    assert error["cleanup_errors"] == [
        {"message": "right close failed", "side": "right", "type": "RuntimeError"}
    ]


def test_missing_calibration_is_reported_without_constructing_a_bus(tmp_path: Path) -> None:
    module = _load_module()
    factory_calls: list[dict[str, Any]] = []
    errors: list[str] = []

    status = module.run_diagnostic(
        left_port="LEFT",
        right_port="RIGHT",
        calibration_file=tmp_path / "missing.json",
        input_fn=lambda prompt: "READ",
        output_fn=lambda message: None,
        error_fn=errors.append,
        bus_factory=lambda **kwargs: factory_calls.append(kwargs),
    )

    assert status == 1
    assert factory_calls == []
    error = json.loads(errors[0])
    assert error["status"] == "error"
    assert error["error"]["type"] == "FileNotFoundError"
    assert "missing.json" in error["error"]["message"]


def test_default_calibration_path_uses_repository_calibration_constant(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    from lerobot.utils import constants

    calibration_root = tmp_path / "configured-calibration-root"
    monkeypatch.setattr(constants, "HF_LEROBOT_CALIBRATION", calibration_root)
    module = _load_module()

    assert module.DEFAULT_CALIBRATION_FILE == (
        constants.HF_LEROBOT_CALIBRATION / constants.ROBOTS / "alohamini" / "AlohaMiniRobot.json"
    )
