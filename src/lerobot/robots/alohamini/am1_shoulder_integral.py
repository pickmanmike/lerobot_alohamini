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
import os
import stat
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import uuid4

from lerobot.motors.feetech.tables import MODEL_NUMBER_TABLE

from .motor_safety import write_register

SHOULDER = "arm_left_shoulder_lift"
_SNAPSHOT_REGISTERS = (
    "Model_Number",
    "Firmware_Major_Version",
    "Firmware_Minor_Version",
    "Min_Position_Limit",
    "Max_Position_Limit",
    "Operating_Mode",
    "Torque_Enable",
    "P_Coefficient",
    "I_Coefficient",
    "D_Coefficient",
    "Minimum_Startup_Force",
)


class AM1ShoulderIntegralTrial:
    """One owner-scoped, torque-free I0 -> I1 -> I0 shoulder experiment.

    The owning robot must retain this object before calling ``configure`` so an
    applied write that raises can still be restored during its normal shutdown.
    No runtime feedback transaction, calibration write, or torque enable is added.
    """

    def __init__(self, *, snapshot_path: Path | None = None):
        self.snapshot_path = Path(snapshot_path) if snapshot_path is not None else None
        self.restore_required = False
        self._bus: Any = None
        self._identity: tuple | None = None
        self._snapshot: dict[str, Any] = {}
        self._snapshot_created = False

    @staticmethod
    def _owner_identity(robot: Any) -> tuple[Any, tuple]:
        if robot.config.robot_model != "alohamini1":
            raise RuntimeError("AM1 shoulder integral trial requires the alohamini1 follower owner.")
        bus = robot.left_bus
        if bus is None or not bus.is_connected:
            raise RuntimeError("AM1 shoulder integral trial requires the connected left bus owner.")
        if SHOULDER not in robot.left_arm_motors or SHOULDER not in bus.motors:
            raise RuntimeError("AM1 shoulder integral trial requires the selected left shoulder mapping.")
        motor = bus.motors[SHOULDER]
        if motor.model != "sts3215" or MODEL_NUMBER_TABLE[motor.model] != 777:
            raise RuntimeError("AM1 shoulder integral trial requires the sts3215 model (777).")
        calibration = bus.calibration.get(SHOULDER)
        if calibration is None or calibration.id != motor.id:
            raise RuntimeError("AM1 shoulder integral trial requires the matching cached calibration ID.")
        identity = (
            motor.id,
            motor.model,
            calibration.id,
            calibration.drive_mode,
            calibration.homing_offset,
            calibration.range_min,
            calibration.range_max,
        )
        return bus, identity

    @staticmethod
    def _read(bus: Any, register: str) -> int:
        value = bus.read(register, SHOULDER, normalize=False, num_retry=0)
        if isinstance(value, bool) or not isinstance(value, int):
            raise RuntimeError(f"AM1 shoulder {register} readback was not a raw integer.")
        return value

    def _prepare_snapshot_path(self) -> None:
        if self.snapshot_path is not None:
            return
        directory_name = os.environ.get("AM1_LOG_DIRECTORY")
        if not directory_name:
            raise RuntimeError("AM1 shoulder integral trial requires the established AM1_LOG_DIRECTORY.")
        owner_directory = Path(directory_name)
        if not owner_directory.is_absolute() or not owner_directory.is_dir():
            raise RuntimeError("AM1_LOG_DIRECTORY must be an existing absolute owner log directory.")
        directory = owner_directory / "am1-shoulder-integral-trials"
        with suppress(FileExistsError):
            directory.mkdir(mode=0o700)
        info = directory.stat(follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode) or directory.is_symlink():
            raise RuntimeError("AM1 shoulder integral snapshot directory must be an owned directory.")
        if os.name != "nt" and stat.S_IMODE(info.st_mode) & 0o077:
            raise RuntimeError("AM1 shoulder integral snapshot directory must have private permissions.")
        self.snapshot_path = directory / f"trial-{uuid4().hex}.json"

    def _persist_snapshot(self, status: str, *, error: BaseException | None = None) -> None:
        assert self.snapshot_path is not None
        self._snapshot.update(status=status, restore_required=self.restore_required)
        if error is not None:
            self._snapshot.setdefault("first_failure_type", type(error).__name__)
        data = (json.dumps(self._snapshot, sort_keys=True, indent=2) + "\n").encode("utf-8")
        temporary = self.snapshot_path.with_name(f".{self.snapshot_path.name}.{uuid4().hex}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if self._snapshot_created:
                os.replace(temporary, self.snapshot_path)
            else:
                # Publishing a complete inode also refuses an existing destination.
                os.link(temporary, self.snapshot_path)
                self._snapshot_created = True
        finally:
            temporary.unlink(missing_ok=True)

    def _retain_failure(self, status: str, error: BaseException) -> None:
        if not self._snapshot_created:
            return
        try:
            self._persist_snapshot(status, error=error)
        except BaseException as snapshot_error:
            error.add_note(f"Integral trial snapshot update also failed: {type(snapshot_error).__name__}.")

    def configure(self, robot: Any) -> bool:
        """Verify the stopped baseline, retain it privately, then set selected I1."""
        if os.environ.get("AM1_SHOULDER_INTEGRAL_TEST") != "1":
            return False
        for prerequisite in ("AM1_SCRIPTED_PREPARE", "AM1_LEFT_SHOULDER_EVIDENCE"):
            if os.environ.get(prerequisite) != "1":
                raise RuntimeError(f"AM1_SHOULDER_INTEGRAL_TEST=1 requires {prerequisite}=1.")
        if self.restore_required or self._snapshot_created:
            raise RuntimeError(
                "AM1 shoulder integral trial object cannot be reused; restore its owner first."
            )
        bus, identity = self._owner_identity(robot)
        self._prepare_snapshot_path()
        registers = {register: self._read(bus, register) for register in _SNAPSHOT_REGISTERS}
        expected = {
            "Model_Number": MODEL_NUMBER_TABLE[identity[1]],
            "Min_Position_Limit": identity[5],
            "Max_Position_Limit": identity[6],
            "Operating_Mode": 0,
            "Torque_Enable": 0,
            "P_Coefficient": 16,
            "I_Coefficient": 0,
            "D_Coefficient": 32,
            "Minimum_Startup_Force": 16,
        }
        for register, value in expected.items():
            if registers[register] != value:
                raise RuntimeError(
                    f"AM1 shoulder {register} baseline mismatch: expected {value}, read {registers[register]}."
                )
        self._bus, self._identity = bus, identity
        self._snapshot = {
            "schema_version": 1,
            "motor": SHOULDER,
            "motor_id": identity[0],
            "motor_model": identity[1],
            "original_registers": registers,
            "cached_calibration": dict(
                zip(
                    ("id", "drive_mode", "homing_offset", "range_min", "range_max"),
                    identity[2:],
                    strict=True,
                )
            ),
            "startup_force_low_byte": registers["Minimum_Startup_Force"] & 0xFF,
            "startup_force_high_byte": registers["Minimum_Startup_Force"] >> 8,
        }
        try:
            self._persist_snapshot("prepared")
            if self._read(bus, "Torque_Enable") != 0:
                raise RuntimeError("AM1 shoulder Torque_Enable must remain zero immediately before I1.")
            # Set before attempting the write: a failed ACK may still have applied I1.
            self.restore_required = True
            write_register(bus, "I_Coefficient", SHOULDER, 1)
            readback = self._read(bus, "I_Coefficient")
            if readback != 1:
                raise RuntimeError(f"AM1 shoulder I_Coefficient=1 verification failed: read {readback}.")
            self._snapshot["configured_integral_readback"] = readback
            self._persist_snapshot("configured")
        except BaseException as error:
            self._retain_failure("configure_failed", error)
            raise
        return True

    def restore(self, robot: Any) -> None:
        """Restore selected I0 only with the same owner and verified torque off."""
        if not self.restore_required:
            return
        try:
            bus, identity = self._owner_identity(robot)
            if bus is not self._bus or identity != self._identity:
                raise RuntimeError(
                    "AM1 shoulder integral restoration requires its original owner and mapping."
                )
            if self._read(bus, "Torque_Enable") != 0:
                raise RuntimeError("AM1 shoulder Torque_Enable must be verified zero before I0 restoration.")
            write_register(bus, "I_Coefficient", SHOULDER, 0)
            readback = self._read(bus, "I_Coefficient")
            if readback != 0:
                raise RuntimeError(f"AM1 shoulder I_Coefficient=0 restoration failed: read {readback}.")
            self._snapshot["restored_integral_readback"] = readback
            self.restore_required = False
            self._persist_snapshot("restored")
        except BaseException as error:
            self._retain_failure("restore_failed", error)
            raise
