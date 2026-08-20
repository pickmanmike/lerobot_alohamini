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

import argparse
import json
import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from lerobot.utils.constants import HF_LEROBOT_CALIBRATION, ROBOTS


DEFAULT_CALIBRATION_FILE = HF_LEROBOT_CALIBRATION / ROBOTS / "alohamini" / "AlohaMiniRobot.json"

MOTOR_NAME = "elbow_flex"
CALIBRATION_KEYS = {
    "left": "arm_left_elbow_flex",
    "right": "arm_right_elbow_flex",
}
CALIBRATION_FIELDS = ("id", "drive_mode", "homing_offset", "range_min", "range_max")

# Every entry is a supported STS3215 control-table register. Keep this an explicit
# allowlist: the diagnostic is intentionally narrower than the full control table.
REGISTERS = (
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
    "Over_Current_Protection_Time",
    "Torque_Enable",
    "Acceleration",
    "Goal_Position",
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
)

CONFIRMATION_PROMPT = (
    "This diagnostic performs read-only access to follower elbow ID 3 on both buses. "
    "Enter exact uppercase READ to continue: "
)
REFUSAL_MESSAGE = (
    "Safety refusal: enter exact uppercase READ to permit read-only elbow register access; "
    "no bus was opened."
)
DUPLICATE_PORT_REFUSAL_MESSAGE = (
    "Safety refusal: left and right ports identify the same path or filesystem object; "
    "no bus was opened."
)


def _default_bus_factory(*, port: str, motors: dict[str, Any]) -> Any:
    from lerobot.motors.feetech import FeetechMotorsBus

    return FeetechMotorsBus(port=port, motors=motors)


def _make_motor_map() -> dict[str, Any]:
    from lerobot.motors import Motor, MotorNormMode

    return {MOTOR_NAME: Motor(3, "sts3215", MotorNormMode.RANGE_M100_100)}


def _load_elbow_calibration(calibration_file: Path) -> dict[str, dict[str, Any]]:
    raw = json.loads(calibration_file.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError(f"Calibration file '{calibration_file}' must contain a JSON object.")

    result: dict[str, dict[str, Any]] = {}
    for side, key in CALIBRATION_KEYS.items():
        entry = raw.get(key)
        if not isinstance(entry, Mapping):
            raise ValueError(f"Calibration file '{calibration_file}' is missing object '{key}'.")
        missing = [field for field in CALIBRATION_FIELDS if field not in entry]
        if missing:
            raise ValueError(
                f"Calibration entry '{key}' is missing required fields: {', '.join(missing)}."
            )
        result[side] = {field: entry[field] for field in CALIBRATION_FIELDS}
    return result


def _differences(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        key: {"left": left[key], "right": right[key]}
        for key in sorted(set(left) & set(right))
        if left[key] != right[key]
    }


def _read_registers(bus: Any) -> dict[str, Any]:
    return {register: bus.read(register, MOTOR_NAME, normalize=False) for register in REGISTERS}


def _exception_record(error: BaseException) -> dict[str, str]:
    return {"message": str(error), "type": type(error).__name__}


def _ports_identify_same_object(left_port: str, right_port: str) -> bool:
    if left_port == right_port:
        return True
    try:
        return os.path.samefile(left_port, right_port)
    except OSError:
        return False


def run_diagnostic(
    *,
    left_port: str,
    right_port: str,
    calibration_file: Path,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    error_fn: Callable[[str], None] | None = None,
    bus_factory: Callable[..., Any] = _default_bus_factory,
) -> int:
    """Compare both AM1 follower elbow ID 3 devices without issuing motor writes."""
    if error_fn is None:
        error_fn = lambda message: print(message, file=sys.stderr)

    try:
        confirmation = input_fn(CONFIRMATION_PROMPT)
    except (EOFError, KeyboardInterrupt):
        confirmation = ""
    if confirmation != "READ":
        output_fn(REFUSAL_MESSAGE)
        return 2
    if _ports_identify_same_object(left_port, right_port):
        output_fn(DUPLICATE_PORT_REFUSAL_MESSAGE)
        return 2

    buses: dict[str, Any] = {}
    cleanup_errors: list[dict[str, str]] = []
    primary_error: BaseException | None = None
    result: dict[str, Any] | None = None

    try:
        calibration = _load_elbow_calibration(Path(calibration_file))

        for side, port in (("left", left_port), ("right", right_port)):
            bus = bus_factory(port=port, motors=_make_motor_map())
            buses[side] = bus
            bus.connect()

        registers = {side: _read_registers(bus) for side, bus in buses.items()}
        result = {
            "calibration": {
                **calibration,
                "differences": _differences(calibration["left"], calibration["right"]),
            },
            "calibration_file": str(calibration_file),
            "diagnostic": "am1_follower_elbow_id3_read_only_compare",
            "motor": {"id": 3, "model": "sts3215", "normalization": "range_m100_100"},
            "registers": {
                **registers,
                "differences": _differences(registers["left"], registers["right"]),
            },
            "safety": {
                "confirmation": "READ",
                "phase_read": False,
                "writes_performed": False,
            },
            "status": "completed",
        }
    except Exception as error:
        primary_error = error
    finally:
        for side, bus in reversed(tuple(buses.items())):
            try:
                if bus.is_connected:
                    bus.disconnect(disable_torque=False)
            except Exception as error:
                cleanup_errors.append({"side": side, **_exception_record(error)})

    if primary_error is not None:
        error_fn(
            json.dumps(
                {
                    "cleanup_errors": cleanup_errors,
                    "error": _exception_record(primary_error),
                    "status": "error",
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 1

    if cleanup_errors:
        error_fn(
            json.dumps(
                {
                    "cleanup_errors": cleanup_errors,
                    "error": {
                        "message": "Register comparison completed, but bus cleanup failed.",
                        "type": "CleanupError",
                    },
                    "status": "error",
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 1

    assert result is not None
    output_fn(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read and compare supported raw STS3215 registers for the Aloha Mini 1 left and right "
            "follower elbow motors (ID 3). This diagnostic never writes motor registers."
        )
    )
    parser.add_argument("--left_port", default="/dev/am_arm_follower_left")
    parser.add_argument("--right_port", default="/dev/am_arm_follower_right")
    parser.add_argument(
        "--calibration_file",
        type=Path,
        default=DEFAULT_CALIBRATION_FILE,
        help="Existing AlohaMiniRobot.json calibration file to compare read-only.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return run_diagnostic(
        left_port=args.left_port,
        right_port=args.right_port,
        calibration_file=args.calibration_file,
    )


if __name__ == "__main__":
    raise SystemExit(main())
