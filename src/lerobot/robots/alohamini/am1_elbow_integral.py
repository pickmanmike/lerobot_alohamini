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


# A single closed Packet12 comparison. This does not broaden the left/I trial.
RIGHT_P20_KIND = "response12-right-p20"
RIGHT_P20_MOTOR = "arm_right_elbow_flex"
RIGHT_P20_RECIPE = "physical-live-right-elbow"
RIGHT_P20_REQUEST_NAME = "response12-right-p20-request.json"
RIGHT_P20_DESCRIPTOR_NAME = "response12-right-p20.json"
RIGHT_P20_CONSUMED_NAME = "response12-right-p20-consumed.json"
PRACTICAL_KIND = "practical13-right"
PRACTICAL_RECIPE = "physical-live-right-elbow-practical"


def _right_p20_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _right_p20_file(path):
    path = Path(path)
    AM1ElbowIntegralTrial._private_directory(path.parent)
    if not path.is_absolute() or path.resolve() != path or path.is_symlink():
        raise ValueError("Right P20 requires its exact nonredirected private file")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= 2048
                or os.name != "nt" and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600)):
            raise ValueError("Right P20 requires an owned single-link private0600 bounded file")
        raw = stream.read(2049)
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value: raise ValueError("Duplicate right P20 field")
            value[key] = item
        return value
    value = json.loads(raw, object_pairs_hook=unique,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite right P20 value")))
    if _right_p20_json(value) != raw:
        raise ValueError("Right P20 file must contain exact canonical JSON")
    return value, raw, info


def _right_p20_request(value, source_head=None):
    import re
    if isinstance(value, dict) and value.get("kind") == PRACTICAL_KIND:
        from examples.alohamini.am1_session_contract import PracticalElbowConfig
        if (set(value) != {"version", "kind", "request_id", "source_head", "config", "proportional"}
                or type(value["version"]) is not int or value["version"] != 1
                or type(value["proportional"]) is not int or value["proportional"] not in (16, 24, 32)
                or type(value["source_head"]) is not str or re.fullmatch("[0-9a-f]{40}", value["source_head"]) is None
                or source_head is not None and value["source_head"] != source_head
                or str(UUID(value["request_id"])) != value["request_id"]):
            raise ValueError("Practical request differs from its closed current-source configuration")
        PracticalElbowConfig.from_payload(value["config"])
        return value
    from examples.alohamini.am1_session_contract import LIVE_ELBOW_WORKSPACES
    workspace = LIVE_ELBOW_WORKSPACES[RIGHT_P20_RECIPE]
    if (not isinstance(value, dict) or set(value) != {"version", "kind", "request_id", "source_head", "workspace_id", "context_sha256"}
            or type(value["version"]) is not int or value["version"] != 1 or value["kind"] != RIGHT_P20_KIND
            or type(value["source_head"]) is not str or re.fullmatch("[0-9a-f]{40}", value["source_head"]) is None
            or source_head is not None and value["source_head"] != source_head
            or value["workspace_id"] != workspace.id or value["context_sha256"] != workspace.context_sha256
            or str(UUID(value["request_id"])) != value["request_id"]):
        raise ValueError("Right P20 request differs from its exact source/current workspace")
    return value


def consume_right_p20_request(state, directory, recipe, run_id, host_incarnation, source_head):
    """Move the one request once before any device owner starts; no retry queue."""
    state, directory = Path(state), Path(directory)
    practical = recipe == PRACTICAL_RECIPE
    prefix = PRACTICAL_KIND if practical else "response12-right-p20"
    kind = PRACTICAL_KIND if practical else RIGHT_P20_KIND
    request = state / (prefix + "-request.json")
    if not request.exists() and not request.is_symlink():
        if practical:
            raise ValueError("Practical recipe requires one fresh private request")
        return None
    if recipe not in (RIGHT_P20_RECIPE, PRACTICAL_RECIPE):
        raise ValueError("Right P20 request is forbidden for a different side/body recipe")
    if any(str(UUID(value)) != value for value in (run_id, host_incarnation)):
        raise ValueError("Right P20 requires canonical current run/host identities")
    AM1ElbowIntegralTrial._private_directory(directory)
    if directory.name != run_id[:8]:
        raise ValueError("Right P20 requires the current protected run directory")
    value, raw, info = _right_p20_file(request)
    _right_p20_request(value, source_head)
    if value["kind"] != kind:
        raise ValueError("Right trial request kind differs from selected recipe")
    archived = state / (prefix + "-used-" + value["request_id"] + ".json")
    if archived.exists() or archived.is_symlink():
        raise ValueError("Right P20 request nonce was already retired")
    consumed, target = directory / (prefix + "-consumed.json"), directory / (prefix + ".json")
    if consumed.exists() or target.exists():
        raise ValueError("Right P20 request cannot be consumed twice")
    current = request.stat(follow_symlinks=False)
    if (current.st_dev, current.st_ino, current.st_size) != (info.st_dev, info.st_ino, info.st_size):
        raise ValueError("Right P20 request changed before consumption")
    used = archived if practical else state / "response12-right-p20-used.json"
    used_value = dict(version=1, kind=kind, run_id=run_id, host_incarnation=host_incarnation,
                      request_sha256=hashlib.sha256(raw).hexdigest(), request=value)
    descriptor = os.open(used, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(_right_p20_json(used_value)); stream.flush(); os.fsync(stream.fileno())
    AM1ElbowIntegralTrial._sync_directory(state)
    os.rename(request, consumed)
    # A failure after durable one-use consumption never creates a new opportunity.
    AM1ElbowIntegralTrial._sync_directory(state)
    AM1ElbowIntegralTrial._sync_directory(directory)
    retained, retained_raw, retained_info = _right_p20_file(consumed)
    if retained_raw != raw or (retained_info.st_dev, retained_info.st_ino) != (info.st_dev, info.st_ino):
        raise ValueError("Right P20 original changed during consumption")
    bound = dict(version=1, kind=kind, run_id=run_id, host_incarnation=host_incarnation,
                 request_sha256=hashlib.sha256(raw).hexdigest(), request=retained)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(_right_p20_json(bound)); stream.flush(); os.fsync(stream.fileno())
    AM1ElbowIntegralTrial._sync_directory(directory)
    return target


def load_right_p20_binding(path, directory, run_id, host_incarnation):
    directory, path = Path(directory), Path(path)
    practical = path.name == PRACTICAL_KIND + ".json"
    prefix = PRACTICAL_KIND if practical else "response12-right-p20"
    kind = PRACTICAL_KIND if practical else RIGHT_P20_KIND
    if (directory.name != run_id[:8] or path != directory / (prefix + ".json")
            or any(str(UUID(value)) != value for value in (run_id, host_incarnation))):
        raise ValueError("Right P20 descriptor requires the exact current protected path/run/host")
    value, _, _ = _right_p20_file(path)
    if (not isinstance(value, dict) or set(value) != {"version", "kind", "run_id", "host_incarnation", "request_sha256", "request"}
            or type(value["version"]) is not int or value["version"] != 1 or value["kind"] != kind
            or value["run_id"] != run_id or value["host_incarnation"] != host_incarnation):
        raise ValueError("Right P20 descriptor identity differs")
    request = _right_p20_request(value["request"])
    if request["kind"] != kind:
        raise ValueError("Right trial consumed request kind differs")
    retained, raw, _ = _right_p20_file(directory / (prefix + "-consumed.json"))
    if request != retained or hashlib.sha256(raw).hexdigest() != value["request_sha256"]:
        raise ValueError("Right P20 original request content/hash differs")
    return copy.deepcopy(value)


_RIGHT_P20_REGISTERS = tuple(dict.fromkeys((*_SNAPSHOT_REGISTERS, "ID", "Homing_Offset", "Phase",
    "Max_Torque_Limit", "Torque_Limit", "CW_Dead_Zone", "CCW_Dead_Zone", "Acceleration",
    "Maximum_Acceleration", "Goal_Time", "Goal_Velocity")))
_RIGHT_P20_PRESERVED = tuple(k for k in _RIGHT_P20_REGISTERS if k not in {
    "P_Coefficient", "Torque_Enable", "Lock", "Present_Position", "Goal_Position"})


class AM1RightElbowProportionalTrial(AM1ElbowIntegralTrial):
    """One consumed right-ID3 transaction with a durable original and restoration."""

    def __init__(self, binding, directory):
        self.practical = binding.get("kind") == PRACTICAL_KIND
        self.kind = PRACTICAL_KIND if self.practical else RIGHT_P20_KIND
        self.recipe = PRACTICAL_RECIPE if self.practical else RIGHT_P20_RECIPE
        self.prefix = PRACTICAL_KIND if self.practical else "response12-right-p20"
        self.proportional = binding["request"]["proportional"] if self.practical else 20
        self.current_cap_ma = 500 if self.practical else 150
        self.config = None
        if self.practical:
            from examples.alohamini.am1_session_contract import PracticalElbowConfig
            self.config = PracticalElbowConfig.from_payload(binding["request"]["config"])
        super().__init__(integral=0, snapshot_path=Path(directory) / (self.prefix + "-original.json"))
        self.binding = copy.deepcopy(binding)
        self.directory = Path(directory)
        self._snapshot = {"schema_version": 1, "kind": self.kind, "events": []}

    @staticmethod
    def _owner(robot):
        if (getattr(robot.config, "robot_model", None) != "alohamini1"
                or not getattr(robot, "_am1_protected_cleanup", False)
                or not getattr(robot, "_am1_selected_joint_evidence_enabled", False)):
            raise RuntimeError("Right P20 requires the protected selected AM1 owner")
        bus = robot.right_bus
        if bus is None or not bus.is_connected or RIGHT_P20_MOTOR not in robot.right_arm_motors:
            raise RuntimeError("Right P20 requires its connected right owner")
        motor, calibration = bus.motors.get(RIGHT_P20_MOTOR), bus.calibration.get(RIGHT_P20_MOTOR)
        identity = tuple(getattr(calibration, k, None) for k in ("id", "drive_mode", "homing_offset", "range_min", "range_max"))
        if (motor is None or type(motor.id) is not int or motor.id != 3 or motor.model != "sts3215"
                or motor.norm_mode is not MotorNormMode.RANGE_M100_100
                or any(type(v) is not int for v in identity) or identity != (3, 0, -658, 827, 3039)):
            raise RuntimeError("Right P20 requires exact actual ID3 firmware mapping")
        return bus, identity

    def _same_owner(self, robot):
        bus, identity = self._owner(robot)
        binding = self.binding
        visible = {k: binding[k] for k in ("run_id", "host_incarnation")}
        visible["recipe"] = self.recipe
        if (bus is not self._bus or identity != self._identity
                or getattr(robot, "_am1_right_p20_trial_binding", None) != binding
                or getattr(robot, "_am1_visible_control_binding", None) != visible
                or load_right_p20_binding(self.directory / (self.prefix + ".json"), self.directory,
                                          binding["run_id"], binding["host_incarnation"]) != binding):
            raise RuntimeError("Right P20 requires its same owner/mapping/current one-use binding")
        return bus

    def _read(self, register, *, phase):
        row = dict(operation="read", register=register, phase=phase, started_at_monotonic_s=time.monotonic())
        self._snapshot["events"].append(row)
        try:
            value = self._bus.read(register, RIGHT_P20_MOTOR, normalize=False, num_retry=0)
            if type(value) is not int:
                raise RuntimeError("Right P20 readback must be an actual raw integer")
            row["value"] = value
            return value
        finally:
            row["completed_at_monotonic_s"] = time.monotonic()

    def prepare(self, robot):
        if self._created or self._bus is not None:
            raise RuntimeError("Right P20 transaction cannot be reused")
        self._bus, self._identity = self._owner(robot)
        self._same_owner(robot)
        if (AM1ElbowIntegralTrial.requested_integral() is not None
                or os.environ.get("AM1_SHOULDER_INTEGRAL_TEST") == "1"
                or getattr(robot, "_am1_elbow_integral_trial", None) is not None):
            raise RuntimeError("Right P20 cannot inherit another gain trial")
        self._prepare_path()
        original = {k: self._read(k, phase="baseline") for k in _RIGHT_P20_REGISTERS}
        expected = dict(Model_Number=777, Firmware_Major_Version=3, Firmware_Minor_Version=10,
                        ID=3, Operating_Mode=0, Torque_Enable=0, Lock=0, P_Coefficient=16,
                        I_Coefficient=0, D_Coefficient=32, Min_Position_Limit=827, Max_Position_Limit=3039,
                        Homing_Offset=-658, Phase=12, CW_Dead_Zone=1, CCW_Dead_Zone=1)
        present = original["Present_Position"]
        low, high = ((self.config.workspace.measured_min_raw, self.config.workspace.measured_max_raw)
                     if self.practical else (1834, 1867))
        if any(original[k] != value for k, value in expected.items()) or not low <= present <= high:
            raise RuntimeError("Right P20 actual stopped baseline differs from reviewed original")
        self._snapshot.update(binding=copy.deepcopy(self.binding), motor=RIGHT_P20_MOTOR, motor_id=3,
                              original_registers=original, original_bytes=self._bytes(phase="baseline"),
                              prepared_at_monotonic_s=time.monotonic())
        self.restore_required = True
        try:
            self._persist_snapshot("prepared")
        except BaseException as error:
            self._failure("prepare_failed", error)
            raise

    def _write(self, register, value, *, phase, reconcile=False):
        allowed = {"Torque_Enable": (0,), "Lock": (0, 1), "P_Coefficient": (16, self.proportional)}
        if register not in allowed or type(value) is not int or value not in allowed[register]:
            raise ValueError("Right P20 transaction cannot write that register/value")
        row = dict(operation="write", register=register, value=value, phase=phase, started_at_monotonic_s=time.monotonic())
        self._snapshot["events"].append(row)
        try:
            self._bus.write(register, RIGHT_P20_MOTOR, value, normalize=False, num_retry=0)
        except BaseException as error:
            row.update(error_type=type(error).__name__, error=str(error)); self._failure(phase + "_failed", error)
            if not reconcile or not isinstance(error, Exception):
                raise
            try:
                actual = self._read(register, phase=phase + "_ack_reconciliation")
            except BaseException as read_error:
                error.add_note("Right P20 acknowledgement readback failed: " + type(read_error).__name__)
                raise error from read_error
            if actual != value:
                raise
            row["reconciled"] = True
        finally:
            row["completed_at_monotonic_s"] = time.monotonic()
        if self._read(register, phase=phase + "_verify") != value:
            raise RuntimeError("Right P20 write verification failed: " + register)

    def disable_torque(self, robot):
        if not self._created or not self.restore_required:
            raise RuntimeError("Right P20 has no durable outstanding original")
        self._same_owner(robot)
        self._write("Torque_Enable", 0, phase="torque_off", reconcile=True)
        self._write("Lock", 1, phase="volatile_lock", reconcile=True)

    def _preserved(self, robot, *, phase):
        self._same_owner(robot)
        original = self._snapshot["original_registers"]
        actual = {k: self._read(k, phase=phase) for k in _RIGHT_P20_PRESERVED}
        if (any(value != original[k] for k, value in actual.items())
                or self._bytes(phase=phase) != self._snapshot["original_bytes"]):
            raise RuntimeError("Right P20 preserved identity/I/D/profile/limits/individual bytes changed")

    def apply(self, robot):
        try:
            if not self.restore_required or self._apply_attempted:
                raise RuntimeError("Right P20 requires one fresh durable apply opportunity")
            self._apply_attempted = True
            self._preserved(robot, phase="before_apply")
            if any(self._read(k, phase="before_apply") != value for k, value in
                   {"Torque_Enable": 0, "Lock": 1, "P_Coefficient": 16}.items()):
                raise RuntimeError("Right P20 requires actual Torque0/Lock1/P16 immediately before apply")
            if self.proportional != 16:
                self._write("P_Coefficient", self.proportional, phase="configure_proportional")
            self._snapshot.update(configured_p_readback=self.proportional, configured_at_monotonic_s=time.monotonic())
            self._persist_snapshot("configured")
        except BaseException as error:
            self._failure("configure_failed", error)
            raise

    def verify_active(self, robot):
        try:
            self._preserved(robot, phase="active")
            actual = {k: self._read(k, phase="active") for k in
                      ("P_Coefficient", "Lock", "Torque_Enable", "Present_Current", "Status")}
            if (actual["P_Coefficient"] != self.proportional or actual["Lock"] != 1 or actual["Torque_Enable"] != 1
                    or actual["Status"] != 0 or abs(actual["Present_Current"]) * 6.5 >= self.current_cap_ma):
                raise RuntimeError("Right P20 active gain/Lock/torque/current/status refused")
            self._persist_snapshot("active")
        except BaseException as error:
            self._failure("active_failed", error)
            raise

    @property
    def receipt(self):
        if not self.restore_required or self._snapshot.get("configured_p_readback") != self.proportional:
            raise RuntimeError("Right P20 receipt requires its applied outstanding obligation")
        request = self.binding["request"]
        workspace = None if self.config is None else self.config.workspace
        return dict(version=1, kind=self.kind, run_id=self.binding["run_id"],
                    host_incarnation=self.binding["host_incarnation"], request_sha256=self.binding["request_sha256"],
                    source_head=request["source_head"], workspace_id=request["workspace_id"] if workspace is None else workspace.id,
                    context_sha256=request["context_sha256"] if workspace is None else workspace.context_sha256,
                    **({"config": self.config.to_payload()} if self.practical else {}),
                    original_p=16, configured_p=self.proportional,
                    restore_required=True, prepared_at_monotonic_s=self._snapshot["prepared_at_monotonic_s"],
                    configured_at_monotonic_s=self._snapshot["configured_at_monotonic_s"])

    def restore(self, robot):
        if not self.restore_required:
            return
        try:
            self.disable_torque(robot)
            if self._read("P_Coefficient", phase="before_restore") != 16:
                self._write("P_Coefficient", 16, phase="restore_proportional", reconcile=True)
            if self._read("P_Coefficient", phase="restored") != 16:
                raise RuntimeError("Right P20 original P16 restoration failed")
            self._preserved(robot, phase="restored")
            self._write("Lock", self._snapshot["original_registers"]["Lock"], phase="restore_original_lock", reconcile=True)
            self._snapshot.update(restored_p_readback=16, restored_at_monotonic_s=time.monotonic())
            self._persist_snapshot("restored", required=False)
            self.restore_required = False
        except BaseException as error:
            self._failure("restore_failed", error)
            raise



def right_p20_settings_valid(proof, run_id=None, host_incarnation=None):
    """Validate actual P16-before/P20-active; never coerce a trial into normal proof."""
    from examples.alohamini.am1_session_contract import LIVE_ELBOW_WORKSPACES
    import re
    static = {"Firmware_Major_Version", "Firmware_Minor_Version", "Model_Number", "ID", "Return_Delay_Time",
              "Min_Position_Limit", "Max_Position_Limit", "Homing_Offset", "Phase", "P_Coefficient", "I_Coefficient",
              "D_Coefficient", "Operating_Mode", "Acceleration", "Goal_Time", "Goal_Velocity", "Torque_Limit", "Maximum_Acceleration"}
    dynamic = {"Torque_Enable", "Lock", "Goal_Position", "Present_Position", "Present_Current", "Status", "Present_Voltage", "Present_Temperature"}
    try:
        trial = proof["right_p20_trial"]
        practical = trial.get("kind") == PRACTICAL_KIND
        recipe, kind, configured_p, current_cap = RIGHT_P20_RECIPE, RIGHT_P20_KIND, 20, 150
        workspace = LIVE_ELBOW_WORKSPACES[RIGHT_P20_RECIPE]
        if practical:
            from examples.alohamini.am1_session_contract import PracticalElbowConfig
            config = PracticalElbowConfig.from_payload(trial["config"])
            workspace = config.workspace
            recipe, kind, configured_p, current_cap = PRACTICAL_RECIPE, PRACTICAL_KIND, trial["configured_p"], 500
            if type(configured_p) is not int or configured_p not in (16, 24, 32):
                return False
            static.add("Protection_Current")
        if (set(proof) != {"version", "binding", "before", "active", "unchanged", "settings_sha256", "right_p20_trial"}
                or type(proof["version"]) is not int or proof["version"] != 1
                or proof["unchanged"] is not (configured_p == 16)):
            return False
        b, trial = proof["binding"], proof["right_p20_trial"]
        if (set(b) != {"run_id", "host_incarnation", "recipe"} or b["recipe"] != recipe
                or any(str(UUID(b[k])) != b[k] for k in ("run_id", "host_incarnation"))
                or run_id is not None and b["run_id"] != run_id
                or host_incarnation is not None and b["host_incarnation"] != host_incarnation):
            return False
        keys = {"version", "kind", "run_id", "host_incarnation", "request_sha256", "source_head", "workspace_id",
                "context_sha256", "original_p", "configured_p", "restore_required", "prepared_at_monotonic_s", "configured_at_monotonic_s"}
        if practical:
            keys.add("config")
        if (set(trial) != keys or type(trial["version"]) is not int or trial["version"] != 1
                or trial["kind"] != kind or trial["run_id"] != b["run_id"] or trial["host_incarnation"] != b["host_incarnation"]
                or trial["restore_required"] is not True or type(trial["original_p"]) is not int or trial["original_p"] != 16
                or type(trial["configured_p"]) is not int or trial["configured_p"] != configured_p
                or type(trial["request_sha256"]) is not str or re.fullmatch("[0-9a-f]{64}", trial["request_sha256"]) is None
                or type(trial["source_head"]) is not str or re.fullmatch("[0-9a-f]{40}", trial["source_head"]) is None
                or trial["workspace_id"] != workspace.id or trial["context_sha256"] != workspace.context_sha256):
            return False
        previous = 0
        for name, torque, p in (("before", 0, 16), ("active", 1, configured_p)):
            item = proof[name]; values, raw_bytes = item["registers"], item["individual_bytes"]
            a, z = item["read_started_at_monotonic_s"], item["read_completed_at_monotonic_s"]
            if (set(item) != {"registers", "individual_bytes", "read_started_at_monotonic_s", "read_completed_at_monotonic_s"}
                    or set(values) != static | dynamic or any(type(v) is not int for v in values.values())
                    or set(raw_bytes) != {"24", "25"} or any(type(v) is not int or not 0 <= v <= 255 for v in raw_bytes.values())
                    or any(values[k] != v for k, v in dict(Model_Number=777,Firmware_Major_Version=3,Firmware_Minor_Version=10,
                        ID=3,Operating_Mode=0,P_Coefficient=p,I_Coefficient=0,D_Coefficient=32,Torque_Enable=torque,Lock=torque,
                        Status=0,Min_Position_Limit=827,Max_Position_Limit=3039,Homing_Offset=-658,Phase=12).items())
                    or practical and values.get("Protection_Current", 0) <= 0
                    or abs(values["Present_Current"]) * 6.5 >= min(current_cap, values["Protection_Current"] * 6.5 if practical else current_cap)
                    or any(type(v) not in (int,float) or not math.isfinite(v) for v in (a,z)) or not 0 <= previous <= a <= z):
                return False
            previous = z
        before, active = proof["before"], proof["active"]
        prepared, configured = trial["prepared_at_monotonic_s"], trial["configured_at_monotonic_s"]
        if (any(type(v) not in (int,float) or not math.isfinite(v) for v in (prepared,configured))
                or not before["read_completed_at_monotonic_s"] <= prepared <= configured <= active["read_started_at_monotonic_s"]):
            return False
        # Hardware current is decoded from the already-read block. The historical
        # settings digest retains its exact register subset; equality covers it above.
        material = dict(registers={k:before["registers"][k] for k in static - {"Protection_Current"}},individual_bytes=before["individual_bytes"])
        return (all(before["registers"][k] == active["registers"][k] for k in static - {"P_Coefficient"})
                and before["individual_bytes"] == active["individual_bytes"]
                and hashlib.sha256(_right_p20_json(material)).hexdigest() == proof["settings_sha256"])
    except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
        return False


LEFT_RESPONSE_P_KIND = "left-response-p"
LEFT_CURRENT_REST_WORKSPACE_PROFILE = "left-current-rest-1051-1164"
RIGHT_RESPONSE_P_KIND = "right-response-p"


def _left_response_p_boot_id():
    value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    if str(UUID(value)) != value:
        raise ValueError("Selected elbow response P requires the actual canonical boot identity")
    return value


def _response_p_request(value, source_head=None, *, kind):
    import re
    from examples.alohamini.am1_live_teleop import CURRENT_REST_WORKSPACE_PROFILES

    if kind not in (LEFT_RESPONSE_P_KIND, RIGHT_RESPONSE_P_KIND):
        raise ValueError("Only the two selected response elbow roles are supported")
    segments = (("left-maintained", "left-reach", "left-peer-fixed", "ordinary-arms")
                if kind == LEFT_RESPONSE_P_KIND else ("right-peer-fixed",))
    if (
        not isinstance(value, dict)
        or set(value) not in ({
            "version",
            "kind",
            "request_id",
            "source_head",
            "proportional",
            "workload_segment",
            "boot_id",
            "expires_at_monotonic_s",
        }, {"version", "kind", "request_id", "source_head", "proportional", "workload_segment",
            "boot_id", "expires_at_monotonic_s", "workspace_profile"},
            {"version", "kind", "request_id", "source_head", "proportional", "workload_segment",
             "boot_id", "expires_at_monotonic_s", "workspace_profile", "left_shoulder_startup_degrees"})
        or type(value["version"]) is not int
        or value["version"] != 1
        or value["kind"] != kind
        or type(value["proportional"]) is not int
        or value["proportional"] not in (16, 32)
        or value["workload_segment"] not in segments
        or type(value["source_head"]) is not str
        or re.fullmatch("[0-9a-f]{40}", value["source_head"]) is None
        or source_head is not None
        and value["source_head"] != source_head
        or any(
            type(value[k]) is not str or str(UUID(value[k])) != value[k] for k in ("request_id", "boot_id")
        )
        or type(value["expires_at_monotonic_s"]) not in (int, float)
        or not math.isfinite(value["expires_at_monotonic_s"])
        or value["expires_at_monotonic_s"] <= 0
    ):
        raise ValueError("Selected elbow response P requires its closed current-source finite comparison request")
    if value["workload_segment"] == "ordinary-arms" and "workspace_profile" not in value:
        raise ValueError("Ordinary arms requires its explicit current-rest workspace profile")
    if "workspace_profile" in value and (
        kind != LEFT_RESPONSE_P_KIND
        or value["workload_segment"] not in CURRENT_REST_WORKSPACE_PROFILES
        or type(value["workspace_profile"]) is not str
        or value["workspace_profile"] != CURRENT_REST_WORKSPACE_PROFILES.get(value["workload_segment"])
        or value["proportional"] != 32
    ):
        raise ValueError("Current-rest profile requires its exact explicit LEFT/P32 workload request")
    if "left_shoulder_startup_degrees" in value and (
        kind != LEFT_RESPONSE_P_KIND
        or value.get("workspace_profile") != CURRENT_REST_WORKSPACE_PROFILES.get(value["workload_segment"])
        or value["workload_segment"] not in CURRENT_REST_WORKSPACE_PROFILES or value["proportional"] != 32
        or type(value["left_shoulder_startup_degrees"]) not in (int, float)
        or not math.isfinite(value["left_shoulder_startup_degrees"])
        or value["left_shoulder_startup_degrees"] != 1
    ):
        raise ValueError("Left shoulder startup allowance requires exactly 1 degree on the current-rest LEFT/P32 request")
    return value


def _left_response_p_request(value, source_head=None):
    return _response_p_request(value, source_head, kind=LEFT_RESPONSE_P_KIND)


def consume_left_response_p_request(state, directory, recipe, run_id, host_incarnation, source_head):
    return _consume_response_p_request(state, directory, recipe, run_id, host_incarnation, source_head,
                                       kind=LEFT_RESPONSE_P_KIND)


def consume_right_response_p_request(state, directory, recipe, run_id, host_incarnation, source_head):
    return _consume_response_p_request(state, directory, recipe, run_id, host_incarnation, source_head,
                                       kind=RIGHT_RESPONSE_P_KIND)


def _consume_response_p_request(state, directory, recipe, run_id, host_incarnation, source_head, *, kind):
    """Consume fresh boot-local admission once; expiry never invalidates restoration."""
    state, directory = Path(state), Path(directory)
    request = state / (kind + "-request.json")
    if not request.exists() and not request.is_symlink():
        return None
    if recipe != "physical-live-teleop" or any(
        type(v) is not str or str(UUID(v)) != v for v in (run_id, host_incarnation)
    ):
        raise ValueError("Selected elbow response P requires the exact current WholeRobot run/host")
    AM1ElbowIntegralTrial._private_directory(directory)
    if directory.name != run_id[:8]:
        raise ValueError("Selected elbow response P requires its current protected run directory")
    value, raw, info = _right_p20_file(request)
    _response_p_request(value, source_head, kind=kind)
    if (
        value["boot_id"] != _left_response_p_boot_id()
        or not 0 <= time.monotonic() < value["expires_at_monotonic_s"]
    ):
        raise ValueError("Selected elbow response P request boot/deadline admission expired")
    used = state / (kind + "-used-" + value["request_id"] + ".json")
    consumed, target = (
        directory / (kind + "-consumed.json"),
        directory / (kind + ".json"),
    )
    if used.exists() or used.is_symlink():
        raise ValueError("Selected elbow response P request nonce was already retired")
    if any(p.exists() or p.is_symlink() for p in (consumed, target)):
        raise ValueError("Selected elbow response P request cannot be consumed twice")
    current = request.stat(follow_symlinks=False)
    if (current.st_dev, current.st_ino, current.st_size) != (info.st_dev, info.st_ino, info.st_size):
        raise ValueError("Selected elbow response P request changed before consumption")
    bound = {
        "version": 1,
        "kind": kind,
        "run_id": run_id,
        "host_incarnation": host_incarnation,
        "request_sha256": hashlib.sha256(raw).hexdigest(),
        "request": value,
    }
    if not 0 <= time.monotonic() < value["expires_at_monotonic_s"]:
        raise ValueError("Selected elbow response P request expired before one-use consumption")
    descriptor = os.open(used, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(_right_p20_json(bound))
        stream.flush()
        os.fsync(stream.fileno())
    AM1ElbowIntegralTrial._sync_directory(state)
    os.rename(request, consumed)
    AM1ElbowIntegralTrial._sync_directory(state)
    AM1ElbowIntegralTrial._sync_directory(directory)
    _, retained, retained_info = _right_p20_file(consumed)
    if retained != raw or (retained_info.st_dev, retained_info.st_ino) != (info.st_dev, info.st_ino):
        raise ValueError("Selected elbow response P original request changed during consumption")
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(_right_p20_json(bound))
        stream.flush()
        os.fsync(stream.fileno())
    AM1ElbowIntegralTrial._sync_directory(directory)
    return target


def load_left_response_p_binding(path, directory, run_id, host_incarnation):
    return _load_response_p_binding(path, directory, run_id, host_incarnation, kind=LEFT_RESPONSE_P_KIND)


def load_right_response_p_binding(path, directory, run_id, host_incarnation):
    return _load_response_p_binding(path, directory, run_id, host_incarnation, kind=RIGHT_RESPONSE_P_KIND)


def _load_response_p_binding(path, directory, run_id, host_incarnation, *, kind):
    directory, path = Path(directory), Path(path)
    if (
        directory.name != run_id[:8]
        or path != directory / (kind + ".json")
        or any(type(v) is not str or str(UUID(v)) != v for v in (run_id, host_incarnation))
    ):
        raise ValueError("Selected elbow response P binding requires its exact current path/run/host")
    value, _, _ = _right_p20_file(path)
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "kind", "run_id", "host_incarnation", "request_sha256", "request"}
        or type(value["version"]) is not int
        or value["version"] != 1
        or value["kind"] != kind
        or value["run_id"] != run_id
        or value["host_incarnation"] != host_incarnation
    ):
        raise ValueError("Selected elbow response P immutable binding identity differs")
    request = _response_p_request(value["request"], kind=kind)
    retained, raw, _ = _right_p20_file(directory / (kind + "-consumed.json"))
    if retained != request or hashlib.sha256(raw).hexdigest() != value["request_sha256"]:
        raise ValueError("Selected elbow response P consumed original request content/hash differs")
    return copy.deepcopy(value)


_LEFT_RESPONSE_P_REGISTERS = tuple(
    dict.fromkeys(
        (
            *_RIGHT_P20_REGISTERS,
            "Protection_Current",
            "Max_Temperature_Limit",
            "Min_Voltage_Limit",
            "Max_Voltage_Limit",
            "Protective_Torque",
            "Protection_Time",
            "Overload_Torque",
            "Over_Current_Protection_Time",
            "Velocity_closed_loop_P_proportional_coefficient",
            "Velocity_closed_loop_I_integral_coefficient",
            "Present_Current",
            "Present_Voltage",
            "Present_Temperature",
            "Status",
        )
    )
)
_LEFT_RESPONSE_P_PRESERVED = tuple(
    k
    for k in _LEFT_RESPONSE_P_REGISTERS
    if k
    not in {
        "P_Coefficient",
        "Torque_Enable",
        "Lock",
        "Present_Position",
        "Goal_Position",
        "Present_Current",
        "Present_Voltage",
        "Present_Temperature",
        "Status",
    }
)


class AM1LeftResponseProportionalTrial(AM1ElbowIntegralTrial):
    """The closed left-ID3 WholeRobot comparison; the right role reuses its transaction."""

    side, kind, motor = "left", LEFT_RESPONSE_P_KIND, ELBOW
    calibration = (3, 0, 1845, 864, 3073)
    raw_bounds = (2011, 2124)

    def _load_binding(self, path, directory, run_id, host_incarnation):
        loader = load_left_response_p_binding if self.side == "left" else load_right_response_p_binding
        return loader(path, directory, run_id, host_incarnation)

    def __init__(self, binding, directory):
        if type(self) not in (AM1LeftResponseProportionalTrial, AM1RightResponseProportionalTrial):
            raise ValueError("Response P supports only the two exact elbow transaction types")
        self.directory = Path(directory)
        self.binding = self._load_binding(
            self.directory / (self.kind + ".json"),
            self.directory,
            binding["run_id"],
            binding["host_incarnation"],
        )
        if self.binding != binding:
            raise ValueError("Selected elbow response P constructor binding differs from its retained original")
        self.proportional = self.binding["request"]["proportional"]
        self.workspace_profile = self.binding["request"].get("workspace_profile")
        if self.workspace_profile is not None:
            self.raw_bounds = (1051, 1164)
        super().__init__(integral=0, snapshot_path=self.directory / (self.kind + "-original.json"))
        self._snapshot = {
            "schema_version": 1,
            "kind": self.kind,
            "binding": copy.deepcopy(binding),
            "events": [],
        }

    def _owner(self, robot):
        if self.side == "left":
            bus, identity = AM1ElbowIntegralTrial._owner(robot)
        else:
            bus = getattr(robot, "right_bus", None)
            if (getattr(robot.config, "robot_model", None) != "alohamini1"
                    or bus is None or not bus.is_connected or self.motor not in robot.right_arm_motors):
                raise RuntimeError("Right response P requires its connected AM1 right owner")
            motor, calibration = bus.motors.get(self.motor), bus.calibration.get(self.motor)
            if (motor is None or type(motor.id) is not int or motor.id != 3 or motor.model != "sts3215"
                    or motor.norm_mode is not MotorNormMode.RANGE_M100_100 or calibration is None
                    or any(type(getattr(calibration, k)) is not int for k in
                           ("id", "drive_mode", "homing_offset", "range_min", "range_max"))):
                raise RuntimeError("Right response P requires actual calibrated ID3 mapping")
            identity = (motor.id, motor.model, calibration.id, calibration.drive_mode,
                        calibration.homing_offset, calibration.range_min, calibration.range_max)
        if (
            not getattr(robot, "_am1_protected_cleanup", False)
            or getattr(robot, "_am1_selected_joint_evidence_enabled", False)
            or getattr(robot, "_am1_" + ("right" if self.side == "left" else "left") + "_response_p_binding", None) is not None
            or getattr(robot, "_am1_" + ("right" if self.side == "left" else "left") + "_response_p_trial", None) is not None
            or identity != (3, "sts3215", *self.calibration)
            or AM1ElbowIntegralTrial.requested_integral() is not None
            or any(
                os.environ.get(k) == "1"
                for k in ("AM1_SELECTED_JOINT_EVIDENCE", "AM1_SHOULDER_INTEGRAL_TEST")
            )
            or any(
                getattr(robot, k, None) is not None
                for k in (
                    "_am1_elbow_integral_trial",
                    "_am1_right_elbow_p_trial",
                    "_am1_elbow_recovery_binding",
                    "_am1_right_p20_trial_binding",
                    "_am1_visible_control_binding",
                    "_am1_practical_config",
                )
            )
        ):
            raise RuntimeError(
                "Selected elbow response P requires its exact protected WholeRobot owner without legacy trials"
            )
        return bus, identity

    def _retained_owner(self, robot):
        bus, identity = self._owner(robot)
        expected = {k: self.binding[k] for k in ("run_id", "host_incarnation")}
        expected["recipe"] = "physical-live-teleop"
        profile = self.binding["request"].get("workspace_profile")
        expected_bounds = ((1051, 1164) if profile is not None else
                           (2011, 2124) if self.side == "left" else (1847, 1960))
        if (
            self.workspace_profile != profile or self.raw_bounds != expected_bounds
            or bus is not self._bus
            or identity != self._identity
            or self.binding != self._snapshot["binding"]
            or getattr(robot, "_am1_" + self.side + "_response_p_trial", None) is not self
            or getattr(robot, "_am1_" + self.side + "_response_p_binding", None) != self.binding
            or getattr(robot, "_am1_teleop_binding", None) != expected
        ):
            raise RuntimeError("Selected elbow response P same owner/mapping/run binding changed")
        frozen = self._snapshot.get("frozen_teleop_registers")
        if frozen is not None:
            records = robot._am1_teleop_settings[self.side]["registers"]
            if any(records[k].get(self.motor) != v for k, v in frozen.items()):
                raise RuntimeError("Selected elbow response P original protected settings changed")
        return bus

    def _same_owner(self, robot):
        bus = self._retained_owner(robot)
        if (
            self._load_binding(
                self.directory / (self.kind + ".json"),
                self.directory,
                self.binding["run_id"],
                self.binding["host_incarnation"],
            )
            != self.binding
        ):
            raise RuntimeError("Selected elbow response P immutable consumed binding changed")
        return bus

    def _service_boundary(self):
        robot = self._robot
        self._retained_owner(robot)
        if getattr(robot, "_am1_shutdown_in_progress", False) is not True:
            from .am1_live_teleop import check_initial_temperature_budget, refresh_startup_temperatures

            if getattr(robot, "_am1_teleop_initial_temperature_pending", False):
                check_initial_temperature_budget(robot)
            refresh_startup_temperatures(robot)
            if (
                getattr(robot, "_am1_teleop_startup_temperature", False)
                and getattr(robot, "_lift_operation", None) is not None
            ):
                robot._refresh_am1_startup_lift_feedback()
        self._retained_owner(robot)

    def _read(self, register, *, phase):
        if register != "Present_Temperature":
            self._service_boundary()
        row = {"operation": "read", "register": register, "phase": phase,
               "started_at_monotonic_s": time.monotonic()}
        self._snapshot["events"].append(row)
        try:
            value = self._bus.read(register, self.motor, normalize=False, num_retry=0)
            if type(value) is not int:
                raise RuntimeError("Response P readback must be a raw integer")
            row["value"] = value
            return value
        finally:
            row["completed_at_monotonic_s"] = time.monotonic()

    def _bytes(self, *, phase):
        result = {}
        for address in (24, 25):
            self._service_boundary()
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
                    err_msg="Selected elbow response P individual-byte read failed",
                )
                if type(value) is not int or not 0 <= value <= 255:
                    raise RuntimeError("Selected elbow response P individual-byte read must be uint8")
                row["value"] = value
                result[str(address)] = value
            finally:
                row["completed_at_monotonic_s"] = time.monotonic()
        return result

    def prepare(self, robot):
        if (
            self._created
            or self._bus is not None
            or getattr(robot, "_am1_shutdown_in_progress", False) is True
        ):
            raise RuntimeError("Selected elbow response P transaction cannot be reused")
        self._bus, self._identity = self._owner(robot)
        self._robot = robot
        self._same_owner(robot)
        self._prepare_path()
        original = {k: self._read(k, phase="baseline") for k in _LEFT_RESPONSE_P_REGISTERS}
        raw_bytes = self._bytes(phase="baseline")
        from .am1_live_teleop import STATIC

        frozen = {k: robot._am1_teleop_settings[self.side]["registers"][k][self.motor] for k in STATIC}
        self._snapshot.update(
            motor=self.motor,
            motor_id=3,
            original_registers=original,
            original_bytes=raw_bytes,
            frozen_teleop_registers=frozen,
            prepared_at_monotonic_s=time.monotonic(),
            original_settings_sha256=hashlib.sha256(
                _right_p20_json({"registers": original, "individual_bytes": raw_bytes})
            ).hexdigest(),
        )
        expected = {
            "Model_Number": 777,
            "Firmware_Major_Version": 3,
            "Firmware_Minor_Version": 10,
            "ID": 3,
            "Operating_Mode": 0,
            "Torque_Enable": 0,
            "P_Coefficient": 16,
            "I_Coefficient": 0,
            "D_Coefficient": 32,
            "Min_Position_Limit": self.calibration[3],
            "Max_Position_Limit": self.calibration[4],
            "Homing_Offset": self.calibration[2],
            "Phase": 12,
            "Max_Torque_Limit": 1000,
            "Torque_Limit": 1000,
            "CW_Dead_Zone": 1,
            "CCW_Dead_Zone": 1,
            "Acceleration": 254,
            "Maximum_Acceleration": 254,
            "Goal_Time": 0,
            "Goal_Velocity": 0,
            "Status": 0,
        }
        if (
            any(original[k] != v for k, v in expected.items())
            or original["Lock"] not in (0, 1)
            or raw_bytes != {"24": 16, "25": 0}
            or any(original[k] != v for k, v in frozen.items())
            or not self.raw_bounds[0] <= original["Present_Position"] <= self.raw_bounds[1]
            or not (self.calibration[3] if self.workspace_profile else self.raw_bounds[0])
            <= original["Goal_Position"]
            <= (self.calibration[4] if self.workspace_profile else self.raw_bounds[1])
            or original["Protection_Current"] <= 0
            or not 0 < original["Max_Temperature_Limit"] <= 100
            or not 0 < original["Min_Voltage_Limit"] < original["Max_Voltage_Limit"]
            or not original["Min_Voltage_Limit"]
            <= original["Present_Voltage"]
            <= original["Max_Voltage_Limit"]
            or abs(original["Present_Current"]) >= original["Protection_Current"]
        ):
            self._persist_snapshot("baseline_refused", required=False)
            raise RuntimeError("Selected elbow response P actual stopped baseline differs from its reviewed original")
        # The original and restoration obligation reach durable storage before any write.
        self.restore_required = True
        try:
            self._persist_snapshot("prepared")
        except BaseException as error:
            self._failure("prepare_failed", error)
            raise

    def _write(self, register, value, *, phase, reconcile=False):
        allowed = {"Torque_Enable": (0,), "Lock": (0, 1), "P_Coefficient": (16, self.proportional)}
        if register not in allowed or type(value) is not int or value not in allowed[register]:
            raise ValueError("Selected elbow response P transaction cannot write that register/value")
        self._service_boundary()
        row = {
            "operation": "write",
            "register": register,
            "value": value,
            "phase": phase,
            "started_at_monotonic_s": time.monotonic(),
        }
        self._snapshot["events"].append(row)
        try:
            self._bus.write(register, self.motor, value, normalize=False, num_retry=0)
        except BaseException as error:
            row.update(error_type=type(error).__name__, error=str(error))
            self._failure(phase + "_failed", error)
            if not reconcile or not isinstance(error, Exception):
                raise
            try:
                actual = self._read(register, phase=phase + "_ack_reconciliation")
            except BaseException as read_error:
                error.add_note(
                    "Selected elbow response P acknowledgement readback failed: " + type(read_error).__name__
                )
                raise error from read_error
            if actual != value:
                raise
            row["reconciled"] = True
        finally:
            row["completed_at_monotonic_s"] = time.monotonic()
        if self._read(register, phase=phase + "_verify") != value:
            raise RuntimeError("Selected elbow response P write verification failed: " + register)

    def disable_torque(self, robot):
        if not self._created or not self.restore_required:
            raise RuntimeError("Selected elbow response P has no durable outstanding original")
        # A lost descriptor cannot prevent off on the still-retained exact device owner.
        self._retained_owner(robot)
        self._write("Torque_Enable", 0, phase="torque_off", reconcile=True)
        self._write("Lock", 1, phase="volatile_lock", reconcile=True)

    def _preserved(self, robot, *, phase):
        if phase == "active":
            self._retained_owner(robot)  # Powered verification performs only device I/O.
        else:
            self._same_owner(robot)
        original = self._snapshot["original_registers"]
        if (
            any(self._read(k, phase=phase) != original[k] for k in _LEFT_RESPONSE_P_PRESERVED)
            or self._bytes(phase=phase) != self._snapshot["original_bytes"]
        ):
            raise RuntimeError(
                "Selected elbow response P preserved identity/I/D/protection/profile/individual bytes changed"
            )

    def require_normal_activation_owner(self, robot):
        """Read-free fence while the ordinary owner seeds its fresh measured goals."""
        self._retained_owner(robot)
        from examples.alohamini.am1_live_teleop import CURRENT_REST_WORKSPACE_PROFILES
        if (self.workspace_profile not in CURRENT_REST_WORKSPACE_PROFILES.values() or self.side != "left"
                or self.proportional != 32 or not self.restore_required
                or self._snapshot.get("state") != "configured"
                or self._snapshot.get("configured_p_readback") != 32
                or getattr(robot, "_am1_shutdown_in_progress", False) is True):
            raise RuntimeError("Current-rest ordinary activation owner/phase changed or Stop prevailed")

    def normal_rest_startup_degrees(self, robot):
        """Read-free request authority for the separate, unchanged shoulder rest role."""
        self.require_normal_activation_owner(robot)
        request = self.binding["request"]
        if "left_shoulder_startup_degrees" not in request:
            return 0.5
        degrees = request["left_shoulder_startup_degrees"]
        from examples.alohamini.am1_live_teleop import CURRENT_REST_WORKSPACE_PROFILES
        if (type(degrees) not in (int, float) or not math.isfinite(degrees) or degrees != 1
                or request.get("workload_segment") not in CURRENT_REST_WORKSPACE_PROFILES
                or request.get("workspace_profile") != CURRENT_REST_WORKSPACE_PROFILES.get(request.get("workload_segment"))
                or request.get("proportional") != 32):
            raise RuntimeError("Left shoulder startup request authority changed")
        return 1.0

    def qualify_normal_activation(self, robot, all_goals):
        self.require_normal_activation_owner(robot)
        matching = [values for bus, values in all_goals if bus is self._bus]
        if len(matching) != 1 or type(matching[0].get(self.motor)) is not int:
            raise RuntimeError("Current-rest ordinary activation needs its exact selected measured map")
        present = matching[0][self.motor]
        rows = getattr(robot, "_am1_activation_readbacks", ())
        def one(register):
            actual = [row for row in rows if row.get("phase") == "after_home"
                      and row.get("motor") == self.motor and row.get("register") == register]
            if len(actual) != 1:
                raise RuntimeError("Current-rest original after-home raw qualification is unavailable")
            return actual[0]
        position, torque = one("Present_Position"), one("Torque_Enable")
        now = time.monotonic()
        if (not self.raw_bounds[0] <= present <= self.raw_bounds[1]
                or position.get("raw") != present or torque.get("raw") != 0
                or any("unavailable" in row or any(type(row.get(key)) not in (int, float)
                       or not math.isfinite(row[key]) for key in ("read_started_at", "read_completed_at"))
                       for row in (position, torque))
                or not 0 <= torque["read_started_at"] <= torque["read_completed_at"]
                <= position["read_started_at"] <= position["read_completed_at"] <= now
                or now - position["read_started_at"] >= robot.AM1_ACTIVATION_MAX_RAW_AGE_S):
            raise RuntimeError("Current-rest fresh after-home position/OFF/corridor qualification refused")
        evidence = {"policy": "ordinary-fresh-measured-after-home",
                    "position": copy.deepcopy(position), "torque_off": copy.deepcopy(torque)}
        self._snapshot["normal_activation"] = evidence
        return evidence  # The existing owner records its actual write/readback, without extra I/O.

    def apply(self, robot):
        try:
            if (
                not self.restore_required
                or self._apply_attempted
                or getattr(robot, "_am1_shutdown_in_progress", False) is True
            ):
                raise RuntimeError("Selected elbow response P requires one fresh durable apply opportunity")
            self._apply_attempted = True
            self._preserved(robot, phase="before_apply")
            if any(
                self._read(k, phase="before_apply") != v
                for k, v in {"Torque_Enable": 0, "Lock": 1, "P_Coefficient": 16}.items()
            ):
                raise RuntimeError("Selected elbow response P requires actual Torque0/Lock1/P16 before apply")
            if self.workspace_profile is not None and (
                self._read("Status", phase="before_apply") != 0
                or abs(self._read("Present_Current", phase="before_apply"))
                >= self._snapshot["original_registers"]["Protection_Current"]
                or getattr(robot, "_am1_shutdown_in_progress", False) is True
            ):
                raise RuntimeError("Current-rest P configuration requires its healthy stopped owner")
            if self.proportional != 16:
                self._write("P_Coefficient", self.proportional, phase="configure_proportional")
            self._snapshot.update(
                configured_p_readback=self.proportional, configured_at_monotonic_s=time.monotonic()
            )
            self._persist_snapshot("configured")
        except BaseException as error:
            self._failure("configure_failed", error)
            raise

    def verify_active(self, robot):
        try:
            if getattr(robot, "_am1_shutdown_in_progress", False) is True:
                raise RuntimeError("Selected elbow response P cannot qualify active settings during shutdown")
            if self.workspace_profile is not None:
                self.require_normal_activation_owner(robot)
                evidence = self._snapshot.get("normal_activation") or {}
                position, write, readback = (evidence.get(key) or {} for key in
                                             ("position", "goal_write", "goal_readback"))
                expected = position.get("raw")
                if (type(expected) is not int or not self.raw_bounds[0] <= expected <= self.raw_bounds[1]
                        or write.get("value") != expected or readback.get("raw") != expected
                        or readback.get("phase") != "seed_readback" or readback.get("motor") != self.motor
                        or readback.get("register") != "Goal_Position" or "unavailable" in readback
                        or any(key in write for key in ("error", "error_type"))
                        or not position["read_completed_at"] <= write.get("started_at_monotonic_s", -1)
                        <= write.get("completed_at_monotonic_s", -1) <= readback.get("read_started_at", -1)
                        <= readback.get("read_completed_at", -1) <= time.monotonic()):
                    raise RuntimeError("Current-rest ordinary fresh measured Goal acknowledgement is unavailable")
            self._preserved(robot, phase="active")
            actual = {
                k: self._read(k, phase="active")
                for k in (
                    "P_Coefficient",
                    "Lock",
                    "Torque_Enable",
                    "Present_Current",
                    "Status",
                    "Present_Voltage",
                    "Present_Temperature",
                )
            }
            original = self._snapshot["original_registers"]
            if (
                actual["P_Coefficient"] != self.proportional
                or actual["Lock"] != 1
                or actual["Torque_Enable"] != 1
                or actual["Status"] != 0
                or abs(actual["Present_Current"]) >= original["Protection_Current"]
                or not original["Min_Voltage_Limit"]
                <= actual["Present_Voltage"]
                <= original["Max_Voltage_Limit"]
                or getattr(robot, "_am1_teleop_first_error", None) is not None
            ):
                raise RuntimeError(
                    "Selected elbow response P active gain/Lock/torque/configured current/status/voltage refused"
                )
            # Existing owner thermal windows remain authoritative; one raw temperature is not a new vote.
            self._snapshot.update(
                state="active", status="active", active_verified=True,
                active_registers=actual, active_verified_at_monotonic_s=time.monotonic(),
            )
            # The durable configured file already owns restoration. Flush active evidence only after OFF.
        except BaseException as error:
            self._snapshot.update(state="active_failed", status="active_failed")
            self._snapshot.setdefault("first_failure", {
                "type": type(error).__name__, "message": str(error),
                "traceback": "".join(traceback.format_exception(error)),
            })
            raise

    @property
    def receipt(self):
        if not self.restore_required or self._snapshot.get("configured_p_readback") != self.proportional:
            raise RuntimeError("Selected elbow response P receipt requires its applied outstanding obligation")
        request = self.binding["request"]
        return {
            "version": 1,
            "kind": self.kind,
            "run_id": self.binding["run_id"],
            "host_incarnation": self.binding["host_incarnation"],
            "request_sha256": self.binding["request_sha256"],
            "source_head": request["source_head"],
            "request_id": request["request_id"],
            "workload_segment": request["workload_segment"],
            "original_p": 16,
            "configured_p": self.proportional,
            "restore_required": True,
            # Hash of the durable original registers/bytes, never a claim of durable active readback.
            "original_settings_sha256": self._snapshot["original_settings_sha256"],
            "prepared_at_monotonic_s": self._snapshot["prepared_at_monotonic_s"],
            "configured_at_monotonic_s": self._snapshot["configured_at_monotonic_s"],
        }

    def active_receipt(self, robot):
        """Read-free per-frame identity fence; never acquire or refresh a device sample."""
        self._retained_owner(robot)
        if (
            self._snapshot.get("state") != "active"
            or self._snapshot.get("active_verified") is not True
            or getattr(robot, "_am1_shutdown_in_progress", False) is True
        ):
            raise RuntimeError("Selected elbow response P active receipt requires its prior verified active phase")
        return copy.deepcopy(self.receipt)

    def restore(self, robot):
        if not self.restore_required:
            return
        try:
            self.disable_torque(robot)
            self._same_owner(robot)
            if self._read("P_Coefficient", phase="before_restore") != 16:
                self._write("P_Coefficient", 16, phase="restore_proportional", reconcile=True)
            if self._read("P_Coefficient", phase="restored") != 16:
                raise RuntimeError("Selected elbow response P original P16 restoration failed")
            # Goal is the ordinary owner's volatile command, not a gain-setting
            # rollback. Keep its original as evidence; never replay a stale Goal.
            self._preserved(robot, phase="restored")
            self._write(
                "Lock",
                self._snapshot["original_registers"]["Lock"],
                phase="restore_original_lock",
                reconcile=True,
            )
            self._snapshot.update(restored_p_readback=16, restored_at_monotonic_s=time.monotonic())
            self._persist_snapshot("restored", required=False)
            self.restore_required = False
        except BaseException as error:
            # An ambiguous final unlock or durable-result failure must retain Lock1 and the obligation.
            try:
                self._retained_owner(robot)
                self._write("Torque_Enable", 0, phase="restore_failed_torque_off", reconcile=True)
                self._write("Lock", 1, phase="restore_failed_lock", reconcile=True)
            except BaseException as hold_error:
                error.add_note(
                    "Selected elbow response P failed-restore hold also failed: " + type(hold_error).__name__
                )
            self._failure("restore_failed", error)
            raise


class AM1RightResponseProportionalTrial(AM1LeftResponseProportionalTrial):
    """The closed right-ID3 comparison uses the same durable restoration machinery."""

    side, kind, motor = "right", RIGHT_RESPONSE_P_KIND, "arm_right_elbow_flex"
    calibration = (3, 0, -658, 827, 3039)
    raw_bounds = (1847, 1960)


def complete_stopped_left_response_p_original(
    bus, directory, *, run_id, host_incarnation, original_sha256, source_head,
    completion_id, check_stopped,
):
    """Verify one failed pre-P32 original and finish only its original Lock.

    The caller owns the existing stopped maintenance locks/source guard. This
    never opens a bus, changes P/Goal/torque, revives a trial, or acknowledges
    native cleanup. The failed original remains immutable.
    """
    import re

    directory = Path(directory)
    AM1ElbowIntegralTrial._private_directory(directory)
    if (not callable(check_stopped) or type(completion_id) is not str
            or str(UUID(completion_id)) != completion_id
            or type(original_sha256) is not str or re.fullmatch("[0-9a-f]{64}", original_sha256) is None):
        raise ValueError("Stopped completion requires its exact original and fresh identity")
    target = directory / ("left-response-p-stopped-completion-" + completion_id + ".json")
    if target.exists() or target.is_symlink():
        raise ValueError("Stopped completion output must be fresh")
    binding = load_left_response_p_binding(directory / "left-response-p.json", directory, run_id, host_incarnation)
    request = _response_p_request(binding["request"], source_head, kind=LEFT_RESPONSE_P_KIND)
    from examples.alohamini.am1_live_teleop import CURRENT_REST_WORKSPACE_PROFILES
    if (request.get("workload_segment") not in CURRENT_REST_WORKSPACE_PROFILES
        or request.get("workspace_profile") != CURRENT_REST_WORKSPACE_PROFILES[request["workload_segment"]]):
        raise ValueError("Stopped completion requires the exact current-rest request")
    path = directory / "left-response-p-original.json"

    def original_file():
        if path.is_symlink() or path.resolve() != path:
            raise ValueError("Stopped completion original path changed")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= 131072
                    or os.name != "nt" and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600)):
                raise ValueError("Stopped completion requires its owned private bounded original")
            raw = stream.read(131073)
        if hashlib.sha256(raw).hexdigest() != original_sha256:
            raise ValueError("Stopped completion original SHA differs")
        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("Duplicate stopped original field")
                value[key] = item
            return value
        value = json.loads(raw, object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite original")))
        if (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode() != raw:
            raise ValueError("Stopped completion original encoding differs")
        return value

    original = original_file()
    registers, raw_bytes = original.get("original_registers", {}), original.get("original_bytes")
    events = original.get("events", [])
    goal_writes = [row.get("value") for row in events
                   if row.get("operation") == "write" and row.get("register") == "Goal_Position"]
    if (original.get("binding") != binding or original.get("kind") != LEFT_RESPONSE_P_KIND
            or original.get("schema_version") != 1 or original.get("state") != "restore_failed"
            or original.get("status") != "restore_failed" or original.get("restore_required") is not True
            or original.get("motor") != ELBOW or original.get("motor_id") != 3
            or original.get("first_failure", {}).get("type") != "RuntimeError"
            or original.get("first_failure", {}).get("message") != "Current-rest Goal write lost actual Torque0"
            or any(key in original for key in ("configured_p_readback", "configured_at_monotonic_s",
                                               "active_verified", "active_registers"))
            or set(registers) != set(_LEFT_RESPONSE_P_REGISTERS) or raw_bytes != {"24": 16, "25": 0}
            or any(type(value) is not int for value in registers.values())
            or (registers["P_Coefficient"], registers["I_Coefficient"], registers["D_Coefficient"],
                registers["Lock"], registers["Torque_Enable"], registers["Goal_Position"], registers["Status"])
            != (16, 0, 32, 0, 0, 2050, 0)
            or not 1051 <= registers["Present_Position"] <= 1164
            or goal_writes != [registers["Present_Position"], 2050]
            or any(row.get("operation") == "write" and
                   (row.get("register") not in {"Goal_Position", "Torque_Enable", "Lock"}
                    or row.get("register") == "Torque_Enable" and row.get("value") != 0
                    or row.get("register") == "Lock" and row.get("value") != 1) for row in events)
            or any(not any(row.get("operation") == "read" and row.get("register") == "Torque_Enable"
                           and row.get("phase") == phase and row.get("value") == 1 for row in events)
                   for phase in ("prime_current_rest_goal_after", "restore_original_goal_after"))
            or hashlib.sha256(_right_p20_json({"registers": registers, "individual_bytes": raw_bytes})).hexdigest()
            != original.get("original_settings_sha256")):
        raise ValueError("Stopped completion is limited to the retained pre-P32 Goal/OFF failure")
    identity = {"Model_Number": 777, "Firmware_Major_Version": 3, "Firmware_Minor_Version": 10,
                "ID": 3, "Operating_Mode": 0, "Phase": 12, "Min_Position_Limit": 864,
                "Max_Position_Limit": 3073, "Homing_Offset": 1845}
    if any(registers[key] != value for key, value in identity.items()):
        raise ValueError("Stopped completion original device identity/calibration differs")
    started = time.monotonic()
    rows = []

    def check():
        check_stopped()
        motor, cal = bus.motors.get(ELBOW), bus.calibration.get(ELBOW)
        if (not started <= time.monotonic() < started + 20.0
                or not bus.is_connected or motor is None or type(motor.id) is not int
                or motor.id != 3 or motor.model != "sts3215"
                or motor.norm_mode is not MotorNormMode.RANGE_M100_100 or cal is None
                or any(type(getattr(cal, key)) is not int for key in
                       ("id", "drive_mode", "homing_offset", "range_min", "range_max"))
                or tuple(getattr(cal, k) for k in ("id", "drive_mode", "homing_offset", "range_min", "range_max"))
                != AM1LeftResponseProportionalTrial.calibration):
            raise RuntimeError("Stopped completion exact connected left owner/calibration changed")
        if load_left_response_p_binding(directory / "left-response-p.json", directory, run_id, host_incarnation) != binding:
            raise RuntimeError("Stopped completion immutable consumed binding changed")

    def read(register=None, address=None):
        row = {"operation": "read", "phase": "stopped_completion",
               "started_at_monotonic_s": time.monotonic()}
        if address is None:
            row["register"] = register
        else:
            row.update(address=address, width=1)
        rows.append(row)
        try:
            check()
            row["started_at_monotonic_s"] = time.monotonic()
            if address is None:
                value = bus.read(register, ELBOW, normalize=False, num_retry=0)
            else:
                value, _, _ = bus._read(address, 1, 3, num_retry=0, raise_on_error=True,
                                        err_msg="Stopped completion individual-byte read failed")
            row.update(value=value, completed_at_monotonic_s=time.monotonic())
            check()
            if (type(value) is not int or not 0 <= row["completed_at_monotonic_s"]
                    - row["started_at_monotonic_s"] <= 0.15):
                raise RuntimeError("Stopped completion actual read must be a raw integer")
            return value
        except BaseException as error:
            row.update(error_type=type(error).__name__, error=str(error)[:160])
            raise
        finally:
            row.setdefault("completed_at_monotonic_s", time.monotonic())

    attempted = False
    try:
        check()
        actual = {key: read(key) for key in _LEFT_RESPONSE_P_REGISTERS}
        actual_bytes = {str(address): read(address=address) for address in (24, 25)}
        dynamic = {"Lock", "Present_Position", "Present_Current", "Present_Voltage", "Present_Temperature"}
        if (any(actual[key] != registers[key] for key in set(registers) - dynamic)
                or actual["Lock"] not in (0, 1) or actual_bytes != raw_bytes
                or not registers["Min_Position_Limit"] <= actual["Present_Position"] <= registers["Max_Position_Limit"]
                or abs(actual["Present_Current"]) >= registers["Protection_Current"]
                or not registers["Min_Voltage_Limit"] <= actual["Present_Voltage"] <= registers["Max_Voltage_Limit"]):
            raise RuntimeError("Stopped completion fresh original settings/Goal/OFF/health differ")
        if original_file() != original:
            raise ValueError("Stopped completion retained original changed")
        if actual["Lock"] == 1:
            check()
            attempted = True
            row = {"operation": "write", "phase": "stopped_completion", "register": "Lock", "value": 0,
                   "started_at_monotonic_s": time.monotonic()}
            rows.append(row)
            try:
                bus.write("Lock", ELBOW, 0, normalize=False, num_retry=0)
            except Exception as error:
                row.update(error_type=type(error).__name__, error=str(error)[:160])
                try:
                    actual_lock = read("Lock")
                except BaseException as read_error:
                    error.add_note("Stopped completion Lock acknowledgement readback failed")
                    raise error from read_error
                if actual_lock != 0:
                    raise error
                row["reconciled"] = True
            finally:
                row["completed_at_monotonic_s"] = time.monotonic()
            check()
        if (read("Lock"), read("Torque_Enable"), read("Goal_Position")) != (0, 0, 2050):
            raise RuntimeError("Stopped completion final Lock/OFF/Goal readbacks differ")
        check()
        if original_file() != original:
            raise ValueError("Stopped completion original changed before publication")
        receipt = {"version": 1, "kind": "left-response-p-stopped-completion", "completion_id": completion_id,
                   "run_id": run_id, "host_incarnation": host_incarnation, "request_sha256": binding["request_sha256"],
                   "source_head": source_head, "original_sha256": original_sha256,
                   "original_settings_sha256": original["original_settings_sha256"], "original_lock": 0,
                   "selected_restoration_verified": True, "native_acknowledged": False, "events": rows,
                   "started_at_monotonic_s": started, "completed_at_monotonic_s": time.monotonic()}
        temporary = target.with_name("." + target.name + "." + uuid4().hex + ".tmp")
        linked = False
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(_right_p20_json(receipt))
                stream.flush()
                os.fsync(stream.fileno())
            check()
            os.link(temporary, target)
            linked = True
            temporary.unlink()
            AM1ElbowIntegralTrial._sync_directory(directory)
        except BaseException:
            if linked:
                target.unlink()
            raise
        finally:
            temporary.unlink(missing_ok=True)
        return receipt
    except BaseException as error:
        if attempted:
            try:
                check()
                row = {"operation": "write", "phase": "stopped_completion_failed_off", "register": "Torque_Enable",
                       "value": 0, "started_at_monotonic_s": time.monotonic()}
                rows.append(row)
                try:
                    bus.write("Torque_Enable", ELBOW, 0, normalize=False, num_retry=0)
                except Exception as off_error:
                    if read("Torque_Enable") != 0:
                        raise off_error
                    row["reconciled"] = True
                finally:
                    row["completed_at_monotonic_s"] = time.monotonic()
                check()
                if read("Torque_Enable") != 0:
                    raise RuntimeError("Stopped completion failure containment OFF is unverified")
            except BaseException as off_error:
                error.add_note("Stopped completion failure containment: " + type(off_error).__name__ + ": " + str(off_error))
        error.add_note("Stopped completion original retained; actual events: " + _right_p20_json(rows).decode())
        raise
