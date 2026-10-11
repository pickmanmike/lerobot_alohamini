"""Opt-in selected-elbow evidence on the existing AM1 motor-owner thread."""

import copy
import json
import math
import time

from lerobot.motors import MotorNormMode
from lerobot.motors.motors_bus import get_address

from .lift_motor_feedback import DirectSdkTransport, TransportRefusal

MARKER = "_am1_selected_joint_evidence"
MOTOR = "arm_left_elbow_flex"
POSITION = MOTOR + ".pos"
PREFIX = "[AM1 SELECTED JOINT] "
PROFILES = {"LeftElbowCompare": 18.0, "LeftElbowDwellCompare": 24.0}
CONTEXT_KEYS = {
    "profile",
    "progress_s",
    "phase",
    "cycle",
    "reference",
    "endpoint_target",
    "requested_target",
    "sample_key",
}
PHASES = {"baseline", "outbound", "endpoint", "return", "origin", "final"}


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _print(row):
    print(PREFIX + json.dumps(row, allow_nan=False, separators=(",", ":")), flush=True)


class AM1SelectedJointEvidence:
    """Diagnostic metadata cannot authorize or acknowledge a motor command."""

    def __init__(self, *, enabled=False, clock=time.monotonic, emit=_print):
        self.enabled, self.clock, self.emit = enabled, clock, emit
        self.context = None
        self.reference = self.endpoint = self.profile = None
        self.progress = -1.0
        self.attempted = set()
        self.last_observation_id = -1

    def deactivate(self, robot):
        self.context = None
        robot._am1_selected_joint_evidence_context = None

    def prepare(self, robot, action, binding):
        cleaned = {k: v for k, v in action.items() if k != MARKER}
        if MARKER not in action:
            self.deactivate(robot)
            return cleaned
        if (
            not self.enabled
            or binding is None
            or not getattr(robot, "_am1_selected_joint_evidence_enabled", False)
        ):
            raise RuntimeError("Selected joint evidence requires the opted-in protected AM1 owner")
        if getattr(robot.config, "robot_model", None) != "alohamini1":
            raise RuntimeError("Selected joint evidence is AM1-only")
        motor = robot.left_bus.motors.get(MOTOR)
        if (
            motor is None
            or motor.id != 3
            or motor.model != "sts3215"
            or motor.norm_mode is not MotorNormMode.RANGE_M100_100
        ):
            raise RuntimeError("Selected joint evidence differs from the established left ID3 mapping")
        metadata = action[MARKER]
        if not isinstance(metadata, dict) or set(metadata) != CONTEXT_KEYS:
            raise RuntimeError("Invalid selected joint evidence context keys")
        profile, phase, cycle = metadata["profile"], metadata["phase"], metadata["cycle"]
        if (
            type(profile) is not str
            or type(phase) is not str
            or profile not in PROFILES
            or phase not in PHASES
            or type(cycle) is not int
            or cycle not in (0, 1, 2)
        ):
            raise RuntimeError("Invalid selected joint evidence profile/phase/cycle")
        numeric = ("progress_s", "reference", "endpoint_target", "requested_target")
        if not all(_number(metadata[k]) for k in numeric):
            raise RuntimeError("Selected joint evidence requires finite numeric context")
        progress, reference, endpoint, requested = (float(metadata[k]) for k in numeric)
        if not 0 <= progress <= PROFILES[profile] or progress < self.progress:
            raise RuntimeError("Selected joint evidence progress regressed or exceeded its finite profile")
        expected_endpoint = reference + (3.0 if reference <= 97.0 else -3.0)
        if (
            not -100 <= reference <= 100
            or not -100 <= endpoint <= 100
            or not math.isclose(endpoint, expected_endpoint, rel_tol=0, abs_tol=1e-9)
        ):
            raise RuntimeError("Selected joint evidence changed the original bounded endpoint")
        if not _number(cleaned.get(POSITION)) or float(cleaned[POSITION]) != requested:
            raise RuntimeError("Selected joint requested context differs from the incoming action")
        if not min(reference, endpoint) - 1e-9 <= requested <= max(reference, endpoint) + 1e-9:
            raise RuntimeError("Selected joint requested action exceeds its declared excursion")
        if (
            (phase == "baseline" and cycle != 0)
            or (phase == "final" and cycle != 2)
            or (phase not in ("baseline", "final") and cycle not in (1, 2))
        ):
            raise RuntimeError("Selected joint evidence phase and cycle differ")
        if phase in ("baseline", "origin", "final") and requested != reference:
            raise RuntimeError("Selected joint origin phase differs from its frozen reference")
        if phase == "endpoint" and requested != endpoint:
            raise RuntimeError("Selected joint endpoint phase differs from its fixed target")
        sample_key = metadata["sample_key"]
        expected_key = (
            "baseline"
            if phase == "baseline"
            else f"cycle{cycle}_{phase}"
            if phase in ("endpoint", "origin")
            else None
        )
        if sample_key is not None and sample_key != expected_key:
            raise RuntimeError("Selected joint evidence sample key differs from its phase")
        for key in ("x.vel", "y.vel", "theta.vel", "lift_axis.vel"):
            if not _number(cleaned.get(key)) or float(cleaned[key]) != 0:
                raise RuntimeError("Selected joint comparison requires ordinary body-zero commands")
        if self.reference is not None and (reference, endpoint, profile) != (
            self.reference,
            self.endpoint,
            self.profile,
        ):
            raise RuntimeError("Selected joint evidence changed the original reference/profile")
        self.reference, self.endpoint, self.profile = reference, endpoint, profile
        self.progress, self.context = progress, copy.deepcopy(metadata)
        robot._am1_selected_joint_evidence_context = copy.deepcopy(metadata)
        return cleaned

    def _base(self, robot, control, marker):
        bus, motor = robot.left_bus, robot.left_bus.motors[MOTOR]
        calibration = bus.calibration[MOTOR]
        return {
            "version": 1,
            **control.binding,
            "epoch": control.epoch,
            "intent_revision": control.intent_revision,
            "action_sequence": control.accepted_action_sequence,
            "observation_id": marker.get("observation_id"),
            "acquired_at_monotonic_s": marker.get("acquired_at_monotonic_s"),
            "context": copy.deepcopy(self.context),
            "motor": {
                "bus": "left",
                "name": MOTOR,
                "id": motor.id,
                "model": motor.model,
                "normalization": motor.norm_mode.value,
                "calibration": {
                    key: int(getattr(calibration, key))
                    for key in ("drive_mode", "homing_offset", "range_min", "range_max")
                },
            },
            "action": copy.deepcopy(getattr(robot, "_am1_selected_joint_action", None)),
        }

    def _decoded(self, bus, payload):
        names = {
            "goal_position_raw": "Goal_Position",
            "present_position_raw": "Present_Position",
            "goal_time_raw": "Goal_Time",
            "goal_velocity_raw": "Goal_Velocity",
            "torque_limit_raw": "Torque_Limit",
            "present_velocity_raw": "Present_Velocity",
            "present_load_raw": "Present_Load",
            "present_current_raw": "Present_Current",
            "torque_enable": "Torque_Enable",
            "operating_mode": "Operating_Mode",
            "status": "Status",
            "moving": "Moving",
            "voltage_raw": "Present_Voltage",
            "temperature_c": "Present_Temperature",
        }
        result = {}
        for key, register in names.items():
            address, width = get_address(bus.model_ctrl_table, bus.motors[MOTOR].model, register)
            if address < 33 or address + width > 71:
                raise RuntimeError("Selected motor table differs from the bounded feedback group")
            word = int.from_bytes(payload[address - 33 : address - 33 + width], "little")
            result[key] = int(bus._decode_sign(register, {3: word})[3])
        result["present_current_ma"] = result["present_current_raw"] * 6.5
        result["voltage_v"] = result["voltage_raw"] / 10
        result["present_position_normalized"] = float(robot_position(bus, result["present_position_raw"]))
        return result

    def report(self, robot, control, observation):
        if not self.enabled or self.context is None or control.binding is None or control.state != "active":
            return
        marker = observation.get("_am1_local_feedback", {})
        matched = (
            isinstance(marker, dict)
            and all(marker.get(k) == v for k, v in control.binding.items())
            and marker.get("epoch") == control.epoch
            and marker.get("intent_revision") == control.intent_revision
            and marker.get("accepted_action_sequence") == control.accepted_action_sequence
        )
        if not matched:
            return  # No new labeled acquisition can be inferred from a stale/foreign view.
        row = self._base(robot, control, marker)
        position = copy.deepcopy(getattr(robot, "_am1_selected_joint_position", None))
        current = copy.deepcopy(getattr(robot, "_am1_selected_joint_current", None))
        observation_id = marker.get("observation_id")
        if type(observation_id) is int and observation_id > self.last_observation_id:
            self.last_observation_id = observation_id
            self.emit({**row, "kind": "native_trace", "position": position, "current": current})
        key = self.context["sample_key"]
        if key is None or key in self.attempted:
            return
        self.attempted.add(key)  # One attempt even if the diagnostic reply is unavailable.
        started = self.clock()
        sparse = {
            **row,
            "kind": "sparse_readback",
            "sample_key": key,
            "read_started_at_monotonic_s": started,
            "diagnostic_available": False,
            "decoded": None,
            "trace": None,
        }
        if any(
            not isinstance(v, dict)
            or not _number(v.get("read_completed_at_monotonic_s"))
            or not 0 <= started - v["read_completed_at_monotonic_s"] <= 0.5
            for v in (position, current)
        ):
            sparse.update(
                unavailable_reason="ordinary selected acquisition unavailable",
                read_completed_at_monotonic_s=self.clock(),
            )
            self.emit(sparse)
            return
        bus = robot.left_bus
        transport = DirectSdkTransport(
            bus.port,
            motor_id=3,
            port_handler=bus.port_handler,
            packet_handler=bus.packet_handler,
        )
        primary = None
        try:
            payload, trace = transport.read_group(33, 38, timeout_ms=40.0)
            sparse["trace"] = trace
            decoded = self._decoded(bus, payload)
            sparse["decoded"] = decoded
            if decoded["status"] != 0 or decoded["torque_enable"] != 1 or decoded["operating_mode"] != 0:
                raise RuntimeError("Selected joint readback reported status/torque/mode fault")
            sparse["diagnostic_available"] = True
            sparse["goal_matches_expected_raw"] = (
                decoded["goal_position_raw"] == row["action"]["expected_goal_raw"]
            )
        except TransportRefusal as error:
            sparse["trace"] = error.trace
            sparse["unavailable_reason"] = f"{type(error).__name__}: {error}"
            if (
                error.trace.get("comm_result") != transport.sdk.COMM_RX_TIMEOUT
                or error.trace.get("response_bytes") != []
            ):
                primary = error
        except BaseException as error:
            sparse["unavailable_reason"] = f"{type(error).__name__}: {error}"
            primary = error
        sparse["read_completed_at_monotonic_s"] = self.clock()
        try:
            self.emit(sparse)
        except BaseException as emit_error:
            if primary is None:
                raise
            primary.add_note(f"Selected evidence reporting also failed: {emit_error}")
        if primary is not None:
            raise primary


def robot_position(bus, raw):
    """Same unbounded measured normalization as the ordinary AM1 observation."""
    calibration = bus.calibration[MOTOR]
    value = (raw - calibration.range_min) / (calibration.range_max - calibration.range_min) * 200 - 100
    return -value if bus.apply_drive_mode and calibration.drive_mode else value
