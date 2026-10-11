"""Selected-ID3 volatile gain transaction on the protected AM1 bus owner."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import stat
import time
import traceback
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from lerobot.motors import MotorNormMode

ELBOW = "arm_left_elbow_flex"
_SNAPSHOT_REGISTERS = (
    "Model_Number",
    "Firmware_Major_Version",
    "Firmware_Minor_Version",
    "Operating_Mode",
    "Torque_Enable",
    "Lock",
    "P_Coefficient",
    "I_Coefficient",
    "D_Coefficient",
    "Min_Position_Limit",
    "Max_Position_Limit",
    "Present_Position",
    "Goal_Position",
)
_PRESERVED_REGISTERS = (
    "Model_Number",
    "Firmware_Major_Version",
    "Firmware_Minor_Version",
    "Operating_Mode",
    "P_Coefficient",
    "D_Coefficient",
    "Min_Position_Limit",
    "Max_Position_Limit",
)


class AM1ElbowIntegralTrial:
    """Retain original settings before writes and restore I while Lock1 remains set.

    The owning robot retains this object before prepare. It never opens a bus,
    enables torque, writes a goal, or writes the combined bytes24/25 SDK alias.
    """

    def __init__(self, *, integral: int, snapshot_path: Path | None = None):
        if type(integral) is not int or integral not in (0, 1):
            raise ValueError("Selected elbow integral must be explicit integer0 or1")
        self.integral = integral
        self.snapshot_path = Path(snapshot_path) if snapshot_path is not None else None
        self.restore_required = False
        self._bus: Any = None
        self._identity: tuple | None = None
        self._created = False
        self._apply_attempted = False
        self._state_hash: str | None = None
        self._snapshot: dict[str, Any] = {"schema_version": 1, "integral": integral, "events": []}

    @staticmethod
    def requested_integral() -> int | None:
        value = os.environ.get("AM1_ELBOW_RECOVERY")
        if value is None:
            return None
        if value not in ("I0", "I1"):
            raise RuntimeError("AM1_ELBOW_RECOVERY must be explicit I0 or I1")
        return int(value[-1])

    @property
    def evidence(self) -> dict[str, Any]:
        return copy.deepcopy(self._snapshot)

    @staticmethod
    def _owner(robot: Any) -> tuple[Any, tuple]:
        if getattr(robot.config, "robot_model", None) != "alohamini1":
            raise RuntimeError("Selected elbow trial requires the AM1 follower owner")
        bus = robot.left_bus
        if bus is None or not bus.is_connected or ELBOW not in robot.left_arm_motors:
            raise RuntimeError("Selected elbow trial requires its connected left owner")
        motor = bus.motors.get(ELBOW)
        calibration = bus.calibration.get(ELBOW)
        if (
            motor is None
            or type(motor.id) is not int
            or motor.id != 3
            or motor.model != "sts3215"
            or motor.norm_mode is not MotorNormMode.RANGE_M100_100
            or calibration is None
            or any(
                type(getattr(calibration, k)) is not int
                for k in ("id", "drive_mode", "homing_offset", "range_min", "range_max")
            )
            or calibration.id != 3
            or calibration.drive_mode not in (0, 1)
            or not 0 <= calibration.range_min < calibration.range_max <= 4095
        ):
            raise RuntimeError("Selected elbow trial requires actual ID3 sts3215 calibrated mapping")
        return bus, (
            motor.id,
            motor.model,
            calibration.id,
            calibration.drive_mode,
            calibration.homing_offset,
            calibration.range_min,
            calibration.range_max,
        )

    def _same_owner(self, robot: Any) -> Any:
        bus, identity = self._owner(robot)
        if (
            bus is not self._bus
            or identity != self._identity
            or getattr(robot, "_am1_elbow_recovery_binding", None) != self._snapshot.get("binding")
        ):
            raise RuntimeError("Selected elbow restoration requires the same owner, mapping and run binding")
        return bus

    def _admission(self, robot: Any) -> dict:
        if (
            self.requested_integral() != self.integral
            or os.environ.get("AM1_SCRIPTED_PREPARE") != "1"
            or os.environ.get("AM1_SELECTED_JOINT_EVIDENCE") != "1"
            or os.environ.get("AM1_SHOULDER_INTEGRAL_TEST") == "1"
            or not getattr(robot, "_am1_protected_cleanup", False)
            or not getattr(robot, "_am1_selected_joint_evidence_enabled", False)
        ):
            raise RuntimeError("Selected elbow trial requires explicit isolated protected recipe admission")
        binding = getattr(robot, "_am1_elbow_recovery_binding", None)
        if not isinstance(binding, dict) or set(binding) != {
            "run_id",
            "host_incarnation",
            "recipe",
            "integral",
        }:
            raise RuntimeError("Selected elbow trial requires exact protected run binding")
        try:
            if any(str(UUID(binding[k])) != binding[k] for k in ("run_id", "host_incarnation")):
                raise ValueError("canonical UUID required")
            from examples.alohamini.am1_session_contract import ELBOW_RECOVERY_DEFINITIONS

            definition = ELBOW_RECOVERY_DEFINITIONS.get(binding["recipe"])
            if (
                definition is None
                or type(binding["integral"]) is not int
                or binding["integral"] != definition.integral
                or definition.integral != self.integral
            ):
                raise ValueError("exact selected recovery recipe/integral required")
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise RuntimeError("Selected elbow trial recipe/run binding refused") from error
        return copy.deepcopy(binding)

    def _read(self, register: str, *, phase: str) -> int:
        row = {
            "operation": "read",
            "register": register,
            "phase": phase,
            "started_at_monotonic_s": time.monotonic(),
        }
        self._snapshot["events"].append(row)
        try:
            value = self._bus.read(register, ELBOW, normalize=False, num_retry=0)
            if type(value) is not int:
                raise RuntimeError("Selected elbow readback must be a raw integer")
            row["value"] = value
            return value
        finally:
            row["completed_at_monotonic_s"] = time.monotonic()

    def _bytes(self, *, phase: str) -> dict[str, int]:
        result = {}
        for address in (24, 25):
            row = {
                "operation": "read",
                "address": address,
                "width": 1,
                "phase": phase,
                "started_at_monotonic_s": time.monotonic(),
            }
            self._snapshot["events"].append(row)
            try:
                value, _, _ = self._bus._read(
                    address,
                    1,
                    3,
                    num_retry=0,
                    raise_on_error=True,
                    err_msg="Selected elbow individual-byte read failed",
                )
                if type(value) is not int or not 0 <= value <= 255:
                    raise RuntimeError("Selected elbow individual-byte read must be uint8")
                row["value"] = value
                result[str(address)] = value
            finally:
                row["completed_at_monotonic_s"] = time.monotonic()
        return result

    @staticmethod
    def _private_directory(directory: Path) -> None:
        if not directory.is_absolute() or directory.resolve() != directory or directory.is_symlink():
            raise RuntimeError("Selected elbow state requires an absolute nonredirected owner directory")
        info = directory.stat(follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode) or (
            os.name != "nt" and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700)
        ):
            raise RuntimeError("Selected elbow state directory must be owned private0700")

    def _prepare_path(self) -> None:
        if self.snapshot_path is None:
            owner = Path(os.environ.get("AM1_LOG_DIRECTORY", ""))
            self._private_directory(owner)
            directory = owner / "am1-elbow-integral-trials"
            directory.mkdir(mode=0o700, exist_ok=True)
            self._private_directory(directory)
            self.snapshot_path = directory / ("trial-" + uuid4().hex + ".json")
        self._private_directory(self.snapshot_path.parent)
        if self.snapshot_path.resolve() != self.snapshot_path or self.snapshot_path.exists():
            raise RuntimeError("Selected elbow state must be a fresh nonredirected private file")

    @staticmethod
    def _sync_directory(directory: Path) -> None:
        if os.name == "nt":
            return
        fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _persist_snapshot(self, state: str, *, required: bool | None = None) -> None:
        assert self.snapshot_path is not None
        self._snapshot.update(
            state=state,
            status=state,
            restore_required=self.restore_required if required is None else required,
        )
        raw = (json.dumps(self._snapshot, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
        temporary = self.snapshot_path.with_name("." + self.snapshot_path.name + "." + uuid4().hex + ".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            if self._created:
                info = self.snapshot_path.lstat()
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or hashlib.sha256(self.snapshot_path.read_bytes()).hexdigest() != self._state_hash
                ):
                    raise RuntimeError("Selected elbow durable obligation changed externally")
                os.replace(temporary, self.snapshot_path)
            else:
                os.link(temporary, self.snapshot_path)
                self._created = True
            temporary.unlink(missing_ok=True)
            self._state_hash = hashlib.sha256(raw).hexdigest()
            self._sync_directory(self.snapshot_path.parent)
        finally:
            temporary.unlink(missing_ok=True)

    def _failure(self, state: str, error: BaseException) -> None:
        self._snapshot.setdefault(
            "first_failure",
            {
                "type": type(error).__name__,
                "message": str(error),
                "traceback": "".join(traceback.format_exception(error)),
            },
        )
        if self._created:
            try:
                self._persist_snapshot(state)
            except BaseException as persistence_error:
                error.add_note(
                    "Selected elbow obligation update also failed: " + type(persistence_error).__name__
                )

    def prepare(self, robot: Any) -> None:
        if self._created or self._bus is not None:
            raise RuntimeError("Selected elbow transaction cannot be reused")
        self._snapshot["binding"] = self._admission(robot)
        self._bus, self._identity = self._owner(robot)
        self._prepare_path()
        registers = {k: self._read(k, phase="baseline") for k in _SNAPSHOT_REGISTERS}
        raw_bytes = self._bytes(phase="baseline")
        expected = {
            "Model_Number": 777,
            "Firmware_Major_Version": 3,
            "Firmware_Minor_Version": 10,
            "Operating_Mode": 0,
            "Torque_Enable": 0,
            "P_Coefficient": 16,
            "I_Coefficient": 0,
            "D_Coefficient": 32,
            "Min_Position_Limit": self._identity[-2],
            "Max_Position_Limit": self._identity[-1],
        }
        if any(registers[k] != v for k, v in expected.items()) or registers["Lock"] not in (0, 1):
            raise RuntimeError(
                "Selected elbow actual stopped baseline differs from reviewed model/firmware/mode/PID/Lock"
            )
        if not self._identity[-2] <= registers["Present_Position"] <= self._identity[-1]:
            raise RuntimeError("Selected elbow actual measured hold is outside calibration")
        self._snapshot.update(
            motor=ELBOW,
            motor_id=3,
            motor_model="sts3215",
            original_registers=registers,
            original_bytes=raw_bytes,
            cached_calibration=dict(
                zip(
                    ("id", "drive_mode", "homing_offset", "range_min", "range_max"),
                    self._identity[2:],
                    strict=True,
                )
            ),
        )
        # Published and directory-fsynced BEFORE even a Torque/Lock write.
        self.restore_required = True
        try:
            self._persist_snapshot("prepared")
        except BaseException as error:
            self._failure("prepare_failed", error)
            raise

    def _write(self, register: str, value: int, *, phase: str, reconcile: bool = False) -> None:
        if register not in ("Torque_Enable", "Lock", "I_Coefficient"):
            raise ValueError("Selected elbow transaction cannot write that register")
        row = {
            "operation": "write",
            "register": register,
            "value": value,
            "phase": phase,
            "started_at_monotonic_s": time.monotonic(),
        }
        self._snapshot["events"].append(row)
        try:
            self._bus.write(register, ELBOW, value, normalize=False, num_retry=0)
        except BaseException as error:
            row["error_type"], row["error"] = type(error).__name__, str(error)
            self._failure(phase + "_failed", error)
            if not reconcile or not isinstance(error, Exception):
                raise
            try:
                actual = self._read(register, phase=phase + "_ack_reconciliation")
            except BaseException as read_error:
                error.add_note("Selected elbow acknowledgement readback failed: " + type(read_error).__name__)
                raise error from read_error
            if actual != value:
                raise
            row["reconciled"] = True
        finally:
            row["completed_at_monotonic_s"] = time.monotonic()
        if self._read(register, phase=phase + "_verify") != value:
            raise RuntimeError("Selected elbow " + register + " verification failed")

    def disable_torque(self, robot: Any) -> None:
        if not self._created:
            raise RuntimeError("Selected elbow identity/baseline has no qualified retained obligation")
        self._same_owner(robot)
        self._write("Torque_Enable", 0, phase="torque_off", reconcile=True)
        self._write("Lock", 1, phase="volatile_lock", reconcile=True)

    def _preserved(self, robot: Any, *, phase: str) -> dict:
        self._same_owner(robot)
        original = self._snapshot["original_registers"]
        actual = {k: self._read(k, phase=phase) for k in _PRESERVED_REGISTERS}
        actual_bytes = self._bytes(phase=phase)
        self._snapshot[phase + "_bytes"] = actual_bytes
        if any(actual[k] != original[k] for k in actual) or actual_bytes != self._snapshot["original_bytes"]:
            raise RuntimeError("Selected elbow identity/P/D/individual24/25 changed during trial")
        return actual

    def apply(self, robot: Any) -> None:
        try:
            if not self.restore_required or self._apply_attempted:
                raise RuntimeError("Selected elbow apply requires one fresh retained obligation")
            self._apply_attempted = True
            self._preserved(robot, phase="before_apply")
            if (
                self._read("Torque_Enable", phase="before_apply") != 0
                or self._read("Lock", phase="before_apply") != 1
            ):
                raise RuntimeError("Selected elbow requires actual Torque0/Lock1 immediately before I")
            if self._read("I_Coefficient", phase="before_apply") != 0:
                raise RuntimeError("Selected elbow baseline I0 was overwritten during setup")
            if self.integral == 1:
                self._write("I_Coefficient", 1, phase="configure_integral")
            actual = self._read("I_Coefficient", phase="configured")
            if actual != self.integral:
                raise RuntimeError("Selected elbow configured integral readback differs")
            self._snapshot["configured_integral_readback"] = actual
            self._persist_snapshot("configured")
        except BaseException as error:
            self._failure("configure_failed", error)
            raise

    @staticmethod
    def check_current(raw: int | float, limit_ma: float = 100.0) -> None:
        if (
            isinstance(raw, bool)
            or not isinstance(raw, (int, float))
            or not math.isfinite(raw)
            or abs(raw) * 6.5 >= min(100.0, limit_ma)
        ):
            raise RuntimeError("Selected elbow recovery experimental current ceiling reached")

    def verify_active(self, robot: Any) -> None:
        try:
            actual = self._preserved(robot, phase="active")
            actual.update(
                {
                    k: self._read(k, phase="active")
                    for k in ("I_Coefficient", "Lock", "Torque_Enable", "Present_Current")
                }
            )
            if (
                actual["I_Coefficient"] != self.integral
                or actual["Lock"] != 1
                or actual["Torque_Enable"] != 1
            ):
                raise RuntimeError("Selected elbow gain/Lock/torque was clobbered by ordinary activation")
            self.check_current(actual["Present_Current"])
            self._snapshot.update(
                active_verified=True, active_registers=actual, active_verified_at_monotonic_s=time.monotonic()
            )
            self._persist_snapshot("active")
        except BaseException as error:
            self._failure("active_failed", error)
            raise

    def restore(self, robot: Any) -> None:
        if not self.restore_required:
            return
        try:
            self.disable_torque(robot)
            original = self._snapshot["original_registers"]
            if self._read("I_Coefficient", phase="before_restore") != original["I_Coefficient"]:
                self._write(
                    "I_Coefficient", original["I_Coefficient"], phase="restore_integral", reconcile=True
                )
            actual_i = self._read("I_Coefficient", phase="restored")
            if actual_i != original["I_Coefficient"]:
                raise RuntimeError("Selected elbow original I restoration failed")
            self._preserved(robot, phase="restored")
            # Lock0 is permitted only AFTER original I and preserved fields verify.
            self._write("Lock", original["Lock"], phase="restore_original_lock", reconcile=True)
            self._snapshot.update(
                restored_integral_readback=actual_i,
                restored_lock_readback=original["Lock"],
                restored_at_monotonic_s=time.monotonic(),
            )
            self._persist_snapshot("restored", required=False)
            self.restore_required = False
        except BaseException as error:
            self._failure("restore_failed", error)
            raise
