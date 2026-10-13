"""Opt-in selected-elbow evidence on the existing AM1 motor-owner thread."""

import copy
import hashlib
import json
import math
import time
from collections import deque

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
    prefix = "[AM1 SELECTED SETTINGS] " if row.get("kind") == "visible_settings" else PREFIX
    print(prefix + json.dumps(row, allow_nan=False, separators=(",", ":")), flush=True)


class AM1SelectedJointEvidence:
    """Diagnostic metadata cannot authorize or acknowledge a motor command."""

    def __init__(self, *, enabled=False, clock=time.monotonic, emit=_print):
        self.enabled, self.clock, self.emit = enabled, clock, emit
        self.context = None
        self.reference = self.endpoint = self.profile = None
        self.progress = -1.0
        self.attempted = set()
        self.sparse_receipts = {}
        self.last_observation_id = -1
        self.recovery_started_at = None
        self.recovery_initial_raw = None
        self.recovery_phase = None
        self.recovery_peak = None
        self.visible_settings = None
        self.visible_started_at = None
        self.visible_initial_raw = None
        self.visible_phase = self.visible_peak = self.visible_goal = None
        self.visible_current_rows = deque(maxlen=20)
        self.visible_position_rows = deque(maxlen=20)
        self.last_live_goal = None
        self._fault_emitted = False
        self._pending_sparse = None
        self.goal_checkpoint_attempted = set()
        self.goal_readbacks = {}
        self.response_boundary = None
        self._response_window = None
        self._response_last_acquisitions = None
        self._response_last_current_raw = None
        self._response_continuity_lost = False
        self._response_startup_hold = None
        self._response_rejoin = None
        self._response_startup_acquisitions = None
        self._response_origin_candidate = None
        self._response_leg_origin = None
        self._response_dwell = None
        self._hold_origin = None
        self._hold_binding = self._hold_cursor = self._hold_anchor = None
        self._hold_rows = deque(maxlen=12)
        self.intercycle_hold_admission = None
        self._hold_departed = False
        self._hold_departure = None
        self._hold_pending_departure = None

    VISIBLE_STATIC_REGISTERS = (
        "Firmware_Major_Version",
        "Firmware_Minor_Version",
        "Model_Number",
        "ID",
        "Return_Delay_Time",
        "Min_Position_Limit",
        "Max_Position_Limit",
        "Homing_Offset",
        "Phase",
        "P_Coefficient",
        "I_Coefficient",
        "D_Coefficient",
        "Operating_Mode",
        "Acceleration",
        "Goal_Time",
        "Goal_Velocity",
        "Torque_Limit",
        "Maximum_Acceleration",
    )
    VISIBLE_DYNAMIC_REGISTERS = (
        "Torque_Enable",
        "Lock",
        "Goal_Position",
        "Present_Position",
        "Present_Current",
        "Status",
        "Present_Voltage",
        "Present_Temperature",
    )

    def _visible_definition(self, robot, binding=None):
        actual = getattr(robot, "_am1_visible_control_binding", None)
        if actual is None:
            return None
        from examples.alohamini.am1_session_contract import (
            LEFT_ELBOW_POSITIVE_CONTROL,
            SelectedElbowExperiment,
            uuid_text,
        )

        expected = actual if binding is None else binding
        experiment = getattr(robot, "_am1_selected_elbow_experiment", None)
        definition = LEFT_ELBOW_POSITIVE_CONTROL if experiment is None else experiment
        keys = {"run_id", "host_incarnation", "recipe"}
        if experiment is not None:
            keys.add("experiment_sha256")
        motor = robot.left_bus.motors.get(MOTOR)
        if (
            not self.enabled
            or not getattr(robot, "_am1_selected_joint_evidence_enabled", False)
            or getattr(robot, "_am1_visible_control_evidence", None) is not self
            or getattr(robot.config, "robot_model", None) != "alohamini1"
            or not isinstance(actual, dict)
            or set(actual) != keys
            or actual != expected
            or (experiment is not None and not isinstance(experiment, SelectedElbowExperiment))
            or actual["recipe"] != definition.recipe_name
            or (experiment is not None and actual["experiment_sha256"] != experiment.sha256)
            or uuid_text(actual["run_id"]) != actual["run_id"]
            or uuid_text(actual["host_incarnation"]) != actual["host_incarnation"]
            or motor is None
            or motor.id != 3
            or motor.model != "sts3215"
            or motor.norm_mode is not MotorNormMode.RANGE_M100_100
        ):
            raise RuntimeError("Visible control differs from its exact opted-in protected owner")
        return definition

    def _visible_settings_read(self, robot, *, source):
        bus = robot.left_bus
        transport = DirectSdkTransport(
            bus.port, motor_id=3, port_handler=bus.port_handler, packet_handler=bus.packet_handler
        )
        started = self.clock()
        payload, first_trace = transport.read_group(0, 71, timeout_ms=40.0)
        first_completed = self.clock()
        current = int(bus._decode_sign("Present_Current", {3: int.from_bytes(payload[69:71], "little")})[3])
        present = int(bus._decode_sign("Present_Position", {3: int.from_bytes(payload[56:58], "little")})[3])
        sample = {
            "raw": current, "ma": current * 6.5,
            "read_started_at_monotonic_s": started, "read_completed_at_monotonic_s": first_completed,
        }
        position = {
            "raw": present, "normalized": float(robot_position(bus, present)),
            "read_started_at_monotonic_s": started, "read_completed_at_monotonic_s": first_completed,
        }
        if source == "settings_active":
            self.last_live_goal = {
                "source": source,
                "goal_position_raw": int(bus._decode_sign("Goal_Position", {3: int.from_bytes(payload[42:44], "little")})[3]),
                "read_started_at_monotonic_s": started, "read_completed_at_monotonic_s": first_completed,
                "torque_enable": payload[40], "operating_mode": payload[33], "status": payload[65],
            }
        self.check_visible_current(
            robot, current, getattr(robot, "_joint_current_limit_ma", 100),
            source=source, sample=sample, position=position,
        )
        maximum, second_trace = transport.read_group(85, 1, timeout_ms=40.0)
        ended = self.clock()
        registers = {}
        for name in (*self.VISIBLE_STATIC_REGISTERS, *self.VISIBLE_DYNAMIC_REGISTERS):
            address, width = get_address(bus.model_ctrl_table, bus.motors[MOTOR].model, name)
            if name == "Maximum_Acceleration":
                raw = int(maximum[0])
            else:
                if not 0 <= address < address + width <= 71:
                    raise RuntimeError("Visible selected settings table differs")
                raw = int.from_bytes(payload[address : address + width], "little")
            registers[name] = int(bus._decode_sign(name, {3: raw})[3])
        return {
            "registers": registers,
            "individual_bytes": {"24": payload[24], "25": payload[25]},
            "read_started_at_monotonic_s": started,
            "read_completed_at_monotonic_s": ended,
        }, [first_trace, second_trace]

    def prepare_visible(self, robot, binding):
        definition = self._visible_definition(robot, binding)
        if definition is None:
            raise RuntimeError("Visible read-only baseline requires its explicit owner")
        if self.visible_settings is not None:
            raise RuntimeError("Visible selected baseline cannot be replaced")
        before, traces = self._visible_settings_read(robot, source="settings_before")
        self.visible_settings = {
            "version": 1,
            "binding": copy.deepcopy(binding),
            "before": before,
            "active": None,
            "unchanged": False,
            "settings_sha256": None,
        }
        self.emit(
            {
                "kind": "visible_settings",
                "phase": "before",
                **copy.deepcopy(binding),
                "settings": copy.deepcopy(before),
                "traces": traces,
            }
        )
        registers, calibration = before["registers"], robot.left_bus.calibration[MOTOR]
        expected = {
            "Model_Number": 777,
            "Firmware_Major_Version": 3,
            "Firmware_Minor_Version": 10,
            "ID": 3,
            "Operating_Mode": 0,
            "P_Coefficient": 16,
            "I_Coefficient": 0,
            "D_Coefficient": 32,
            "Torque_Enable": 0,
            "Lock": 0,
            "Status": 0,
            "Homing_Offset": calibration.homing_offset,
        }
        if (
            any(registers[k] != value for k, value in expected.items())
            or not 0 <= registers["Min_Position_Limit"] < registers["Max_Position_Limit"] <= 4095
        ):
            raise RuntimeError("Visible selected actual baseline identity/settings differ")

    def verify_visible_active(self, robot):
        definition = self._visible_definition(robot)
        if definition is None or self.visible_settings is None:
            raise RuntimeError("Visible active settings require their original baseline")
        active, traces = self._visible_settings_read(robot, source="settings_active")
        self.visible_settings["active"] = active
        before = self.visible_settings["before"]
        original = {
            "registers": {k: before["registers"][k] for k in self.VISIBLE_STATIC_REGISTERS},
            "individual_bytes": before["individual_bytes"],
        }
        now = {
            "registers": {k: active["registers"][k] for k in self.VISIBLE_STATIC_REGISTERS},
            "individual_bytes": active["individual_bytes"],
        }
        self.visible_settings["unchanged"] = original == now
        self.visible_settings["settings_sha256"] = hashlib.sha256(
            json.dumps(original, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()
        self.emit(
            {
                "kind": "visible_settings",
                "phase": "active",
                **self.visible_settings["binding"],
                "settings": copy.deepcopy(active),
                "traces": traces,
                "unchanged": self.visible_settings["unchanged"],
            }
        )
        if not definition.settings_valid(self.visible_settings):
            raise RuntimeError("Visible selected active settings changed or were unqualified")

    def visible_control_proof(self, robot):
        if self._visible_definition(robot) is None or self.visible_settings is None:
            return None
        proof = copy.deepcopy(self.visible_settings)
        if len(json.dumps(proof, allow_nan=False, separators=(",", ":")).encode()) > 4096:
            raise RuntimeError("Visible actual settings proof exceeds its bounded feedback frame")
        return proof

    def capture_fault(self, robot, reason, *, source, current=None, position=None, ordinary_limit=None):
        """Retain the first rejected context without reads, emits or serialization."""
        if getattr(robot, "_am1_selected_joint_fault", None) is not None:
            return
        try:
            definition = self._visible_definition(robot)
            robot._am1_selected_joint_fault = {
                "version": 1,
                "kind": "fault_context",
                **copy.deepcopy(getattr(robot, "_am1_visible_control_binding", None) or {}),
                "motor": {"bus": "left", "name": MOTOR, "id": 3, "model": "sts3215"},
                "reason": reason,
                "source": source,
                "captured_at_monotonic_s": self.clock(),
                "current_scale_ma_per_raw": 6.5,
                "current": copy.deepcopy(current),
                "diagnostic_current_cap_ma": None if definition is None else definition.current_cap_ma,
                "ordinary_limit_ma": ordinary_limit,
                "effective_limit_ma": (
                    min(definition.current_cap_ma, ordinary_limit)
                    if definition is not None and ordinary_limit is not None else None
                ),
                "comparison": "abs(current_ma) >= effective_limit_ma" if ordinary_limit is not None else None,
                "context": copy.deepcopy(self.context),
                "action": copy.deepcopy(getattr(robot, "_am1_selected_joint_action", None)),
                "position": copy.deepcopy(
                    position if position is not None else getattr(robot, "_am1_selected_joint_position", None)
                ),
                "last_completed_live_goal": copy.deepcopy(self.last_live_goal),
            **({"response_boundary": copy.deepcopy(self.response_boundary)}
                   if self.response_boundary is not None else {}),
            }
        except BaseException as error:
            # A diagnostic failure cannot replace or delay the protective refusal.
            robot._am1_selected_joint_fault_capture_error = type(error).__name__

    def enrich_fault(self, robot, control):
        fault = getattr(robot, "_am1_selected_joint_fault", None)
        if isinstance(fault, dict) and control is not None and "control" not in fault:
            fault["control"] = {
                **copy.deepcopy(control.binding or {}),
                "state": control.state,
                "epoch": control.epoch,
                "intent_revision": control.intent_revision,
                "accepted_action_sequence": control.accepted_action_sequence,
                "last_completed_observation_id": control.observation_id,
            }

    def emit_pending_fault(self, robot):
        fault = getattr(robot, "_am1_selected_joint_fault", None)
        if isinstance(fault, dict) and not self._fault_emitted:
            self._fault_emitted = True
            self.emit(copy.deepcopy(fault))
        if self._pending_sparse is not None:
            row, self._pending_sparse = self._pending_sparse, None
            self.emit(row)

    def check_visible_current(self, robot, raw, ordinary_limit, *, source="cached_current", sample=None, position=None):
        definition = self._visible_definition(robot)
        if definition is None:
            return
        if type(raw) is not int or not _number(ordinary_limit) or ordinary_limit <= 0:
            raise RuntimeError("Visible actual selected current is unavailable")
        if abs(raw * 6.5) >= min(definition.current_cap_ma, ordinary_limit):
            self.capture_fault(
                robot, "Visible selected software effort ceiling reached", source=source,
                current=sample if sample is not None else getattr(robot, "_am1_selected_joint_current", None),
                position=position, ordinary_limit=ordinary_limit,
            )
            raise RuntimeError("Visible selected software effort ceiling reached")

    def deactivate(self, robot):
        # Protective Pause/Stop must always proceed. Retain an unresolved active
        # obligation so paused/coasting motion cannot qualify a later admission.
        if self._response_window is not None:
            if self._response_tracking_current(robot):
                self._response_window = None
            else:
                self._response_continuity_lost = True
                self.response_boundary = dict(self.response_boundary, status="continuity_lost")
        self.context = None
        self._response_rejoin = None
        self._response_origin_candidate = self._response_leg_origin = self._response_dwell = None
        self._clear_hold_admission()
        robot._am1_selected_joint_evidence_context = None

    def prepare(self, robot, action, binding, *, control_marker=None):
        cleaned = {k: v for k, v in action.items() if k != MARKER}
        if MARKER not in action:
            candidate = getattr(robot, "_am1_selected_elbow_experiment", None)
            if candidate is not None and getattr(candidate, "response_rule", None) is not None:
                definition = self._visible_definition(robot)
                actual = robot._am1_visible_control_binding
                if binding != {k: actual[k] for k in ("run_id", "host_incarnation")}:
                    self._response_refuse(robot, "Selected response startup owner binding is unavailable")
                self._prepare_response_startup_hold(robot, cleaned, definition)
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
        if not isinstance(metadata, dict):
            raise RuntimeError("Invalid selected joint evidence context keys")
        profile, phase, cycle = metadata.get("profile"), metadata.get("phase"), metadata.get("cycle")
        visible = self._visible_definition(robot)
        if visible is not None and profile != visible.profile:
            raise RuntimeError("Selected profile differs from its frozen protected experiment")
        visible_context = visible is not None and profile == visible.profile
        expected_keys = (
            CONTEXT_KEYS | {"acquisition_s", "admitted_reference"} if visible_context else CONTEXT_KEYS
        )
        if visible_context and getattr(robot, "_am1_selected_elbow_experiment", None) is not None:
            expected_keys |= {"experiment_sha256"}
        composed = visible_context and getattr(visible, "schema_version", 1) == 2
        if composed:
            expected_keys.add("rejoin")
        if set(metadata) != expected_keys:
            raise RuntimeError("Invalid selected joint evidence context keys")
        if (
            type(profile) is not str
            or type(phase) is not str
            or phase not in (PHASES | {"acquire", "intercycle_hold", "rejoin"} if composed
                             else PHASES | {"acquire"} if visible_context else PHASES)
            or type(cycle) is not int
        ):
            raise RuntimeError("Invalid selected joint evidence profile/phase/cycle")
        signed = None
        if profile not in PROFILES:
            # The literal shared file is projected into this motor owner too.
            # Ordinary and retained profiles do not acquire this dependency.
            from examples.alohamini.am1_session_contract import (
                ELBOW_RECOVERY_DEFINITIONS,
                LEFT_ELBOW_ORIGINAL_RETURN,
                LEFT_ELBOW_POSITIVE_CONTROL,
                LEFT_ELBOW_RESPONSE,
            )

            definitions = {
                d.profile: d
                for d in (
                    LEFT_ELBOW_RESPONSE,
                    LEFT_ELBOW_ORIGINAL_RETURN,
                    LEFT_ELBOW_POSITIVE_CONTROL,
                    *ELBOW_RECOVERY_DEFINITIONS.values(),
                )
            }
            if visible is not None:
                definitions[visible.profile] = visible
            signed = definitions.get(profile)
            if signed is None or not signed.context_valid(metadata):
                raise RuntimeError("Invalid selected joint signed profile context")
            if profile in {d.profile for d in ELBOW_RECOVERY_DEFINITIONS.values()}:
                recovery_binding = getattr(robot, "_am1_elbow_recovery_binding", None)
                if not isinstance(recovery_binding, dict) or recovery_binding != {
                    **binding,
                    "recipe": signed.recipe_name,
                    "integral": signed.integral,
                }:
                    raise RuntimeError("Selected recovery profile differs from its protected owner admission")
            calibration = robot.left_bus.calibration.get(MOTOR)
            if (
                calibration is None
                or type(calibration.id) is not int
                or calibration.id != 3
                or type(calibration.drive_mode) is not int
                or calibration.drive_mode not in (0, 1)
                or type(calibration.homing_offset) is not int
                or type(calibration.range_min) is not int
                or type(calibration.range_max) is not int
                or not 0 <= calibration.range_min < calibration.range_max <= 4095
            ):
                raise RuntimeError(
                    "Selected joint signed context requires the actual bounded ID3 calibration"
                )
        elif cycle not in (0, 1, 2):
            raise RuntimeError("Invalid selected joint evidence profile/phase/cycle")
        numeric = ("progress_s", "reference", "endpoint_target", "requested_target")
        if not all(_number(metadata[k]) for k in numeric):
            raise RuntimeError("Selected joint evidence requires finite numeric context")
        progress, reference, endpoint, requested = (float(metadata[k]) for k in numeric)
        duration = PROFILES[profile] if signed is None else signed.trajectory_s
        if not 0 <= progress <= duration or progress < self.progress:
            raise RuntimeError("Selected joint evidence progress regressed or exceeded its finite profile")
        expected_endpoint = reference + (3.0 if reference <= 97.0 else -3.0)
        if (
            not -100 <= reference <= 100
            or not -100 <= endpoint <= 100
            or (signed is None and not math.isclose(endpoint, expected_endpoint, rel_tol=0, abs_tol=1e-9))
        ):
            raise RuntimeError("Selected joint evidence changed the original bounded endpoint")
        if not _number(cleaned.get(POSITION)) or float(cleaned[POSITION]) != requested:
            raise RuntimeError("Selected joint requested context differs from the incoming action")
        lower = (
            min(reference, endpoint, metadata["admitted_reference"])
            if visible_context
            else min(reference, endpoint)
        )
        upper = (
            max(reference, endpoint, metadata["admitted_reference"])
            if visible_context
            else max(reference, endpoint)
        )
        if composed and phase == "rejoin":
            lower, upper = reference-abs(visible.excursion_norm), reference+abs(visible.excursion_norm)
        if not lower - 1e-9 <= requested <= upper + 1e-9:
            raise RuntimeError("Selected joint requested action exceeds its declared excursion")
        if signed is None and (
            (phase == "baseline" and cycle != 0)
            or (phase == "final" and cycle != 2)
            or (phase not in ("baseline", "final") and cycle not in (1, 2))
        ):
            raise RuntimeError("Selected joint evidence phase and cycle differ")
        original_return = signed is not None and (
            signed.profile == "LeftElbowOriginalReturn"
            or getattr(signed, "mode", None) in {"return", "dwell"}
        )
        if phase in ("baseline", "origin", "final") and requested != (
            endpoint if original_return and phase == "baseline" else reference
        ):
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
        if signed is not None and (hasattr(signed, "integral") or visible_context):
            expected_key = (
                signed.sample_key(progress, metadata["acquisition_s"])
                if visible_context
                else signed.sample_key(progress)
            )
        if sample_key is not None and sample_key != expected_key:
            raise RuntimeError("Selected joint evidence sample key differs from its phase")
        if getattr(visible, "response_rule", None) is not None and self._response_window is not None:
            leg = (cycle, self._response_direction(robot, visible, metadata))
            if self._response_continuity_lost or self.context is None or leg != self._response_window["leg"]:
                if not self._response_continuity_lost and self._response_tracking_current(robot):
                    self._response_window = None
                else:
                    self._response_refuse(robot, "Selected response continuity unavailable before active actuation",
                                          failure_kind="continuity_lost")
        if composed:
            self._prepare_response_rejoin(robot, visible, metadata)
        for key in ("x.vel", "y.vel", "theta.vel", "lift_axis.vel"):
            if not _number(cleaned.get(key)) or float(cleaned[key]) != 0:
                raise RuntimeError("Selected joint comparison requires ordinary body-zero commands")
        if self.reference is not None:
            if (reference, profile) != (self.reference, self.profile):
                raise RuntimeError("Selected joint evidence changed the original reference/profile")
            if endpoint != self.endpoint and (
                signed is None
                or (self.context is not None and self.context["phase"] == "final")
                or self.endpoint != reference - 3
                or endpoint != reference + 10
                or progress < getattr(signed, "decision_s", float("inf"))
                or cycle not in (3, 4)
            ):
                raise RuntimeError("Selected joint evidence changed or reversed its frozen endpoint")
        startup = self._response_startup_hold
        if (visible is not None and getattr(visible, "response_rule", None) is not None
                and startup is not None and self.profile is None):
            now, position, material = self._response_startup_pair(robot, visible)
            if now - startup["started_at"] >= 20:
                self._response_refuse(robot, "Selected response startup measured hold exceeded 20 seconds")
            admitted = metadata["admitted_reference"]
            measured = visible.measured_origin(admitted, material)
            if (progress != 0 or requested != admitted
                    or not visible.startup_hold_valid(admitted, measured, material)
                    or abs(measured - position["raw"]) > 1):
                self._response_refuse(robot, "Selected response first finite measured pose is unavailable")
        if visible is not None and getattr(visible, "intercycle_hold_admission_rule", None) is not None:
            self._prepare_hold_departure(robot, visible, metadata, binding, control_marker)
        self.reference, self.endpoint, self.profile = reference, endpoint, profile
        self.progress, self.context = progress, copy.deepcopy(metadata)
        robot._am1_selected_joint_evidence_context = copy.deepcopy(metadata)
        return cleaned

    def guard_recovery(self, robot, control, observation):
        """Bound the selected admission on every ordinary owner tick, including holds."""
        if getattr(robot, "_am1_visible_control_binding", None) is not None:
            self.guard_visible(robot, control, observation)
            return
        binding = getattr(robot, "_am1_elbow_recovery_binding", None)
        if binding is None:
            return
        from examples.alohamini.am1_session_contract import ELBOW_RECOVERY_DEFINITIONS

        definition = (
            ELBOW_RECOVERY_DEFINITIONS.get(binding.get("recipe")) if isinstance(binding, dict) else None
        )
        if (
            definition is None
            or not self.enabled
            or control.binding is None
            or binding
            != {**control.binding, "recipe": definition.recipe_name, "integral": definition.integral}
        ):
            raise RuntimeError("Recovery owner binding is unavailable or differs")
        now = self.clock()
        if self.recovery_started_at is None and control.state in {"paused", "active"} and control.epoch >= 0:
            self.recovery_started_at = now
        if self.recovery_started_at is not None and now - self.recovery_started_at >= definition.live_s:
            raise RuntimeError("Recovery 60-second native Live ceiling expired")
        current = getattr(robot, "_am1_selected_joint_current", None)
        position = getattr(robot, "_am1_selected_joint_position", None)
        for cache in (position, current):
            if (
                not isinstance(cache, dict)
                or not all(
                    _number(cache.get(k))
                    for k in ("read_started_at_monotonic_s", "read_completed_at_monotonic_s")
                )
                or not 0
                <= cache["read_started_at_monotonic_s"]
                <= cache["read_completed_at_monotonic_s"]
                <= now
                or now - cache["read_started_at_monotonic_s"] > 0.5
            ):
                raise RuntimeError("Recovery native original position/current acquisition is unavailable")
        if not _number(current.get("ma")) or abs(current["ma"]) >= definition.current_cap_ma:
            raise RuntimeError("Recovery selected software effort ceiling reached")
        raw = position.get("raw")
        calibration = robot.left_bus.calibration.get(MOTOR)
        if (
            type(raw) is not int
            or calibration is None
            or not calibration.range_min <= raw <= calibration.range_max
        ):
            raise RuntimeError("Recovery native position exceeds its calibration")
        if self.recovery_initial_raw is None:
            self.recovery_initial_raw = raw
        initial = self.recovery_initial_raw
        if definition.mode in {"return", "dwell"}:
            target = 2472
            distance = (target - initial) / (calibration.range_max - calibration.range_min) * 200
            if not 0 <= distance <= 3 or not initial - 2 <= raw <= target + 2:
                raise RuntimeError("Recovery native position left its measured-start target corridor")
        else:
            excursion = math.ceil((calibration.range_max - calibration.range_min) * 3 / 200)
            if abs(raw - initial) > excursion + 2:
                raise RuntimeError("Recovery native position left its frozen confirmation corridor")
        context = self.context
        if context is None or control.state != "active":
            return
        direction = None
        if context["phase"] in {"outbound", "endpoint"}:
            direction = 1 if context["endpoint_target"] > context["reference"] else -1
        elif context["phase"] in {"return", "origin"}:
            direction = 1 if definition.mode in {"return", "dwell"} else -1
        if direction is not None:
            phase = (context["cycle"], direction)
            if phase != self.recovery_phase:
                self.recovery_phase, self.recovery_peak = phase, raw
            if direction * (raw - self.recovery_peak) < -2:
                raise RuntimeError("Recovery native position reversed or oscillated against the approach")
            self.recovery_peak = (
                max(self.recovery_peak, raw) if direction > 0 else min(self.recovery_peak, raw)
            )

    def guard_visible(self, robot, control, observation=None):
        """Enforce the new finite probe on cached native reads, never another bus reader."""
        definition = self._visible_definition(robot)
        binding = getattr(robot, "_am1_visible_control_binding", None)
        expected_binding = {**(control.binding or {}), "recipe": definition.recipe_name} if definition is not None else None
        if expected_binding is not None and getattr(robot, "_am1_selected_elbow_experiment", None) is not None:
            expected_binding["experiment_sha256"] = definition.sha256
        if (
            definition is None
            or control.binding is None
            or binding != expected_binding
            or not definition.settings_valid(self.visible_settings)
        ):
            raise RuntimeError("Visible actual read-only settings/owner binding is unavailable")
        now = self.clock()
        if self.visible_started_at is None and control.state in {"paused", "active"} and control.epoch >= 0:
            self.visible_started_at = now
        if self.visible_started_at is not None and now - self.visible_started_at >= definition.live_s:
            raise RuntimeError(f"Visible {definition.live_s:g}-second native Live ceiling expired")
        position = getattr(robot, "_am1_selected_joint_position", None)
        current = getattr(robot, "_am1_selected_joint_current", None)
        for cache in (position, current):
            if (
                not isinstance(cache, dict)
                or not all(
                    _number(cache.get(k))
                    for k in ("read_started_at_monotonic_s", "read_completed_at_monotonic_s")
                )
                or not 0
                <= cache["read_started_at_monotonic_s"]
                <= cache["read_completed_at_monotonic_s"]
                <= now
                or now - cache["read_started_at_monotonic_s"] > 0.5
            ):
                raise RuntimeError("Visible native original position/current acquisition is unavailable")
        if not _number(current.get("ma")) or current.get("ma") != current.get("raw", float("nan")) * 6.5:
            raise RuntimeError("Visible actual selected current is unavailable")
        self.check_visible_current(
            robot, current.get("raw"), getattr(robot, "_joint_current_limit_ma", 100),
            source="cached_guard", sample=current,
        )
        raw, calibration = position.get("raw"), robot.left_bus.calibration[MOTOR]
        registers = self.visible_settings["before"]["registers"]
        low = max(calibration.range_min, registers["Min_Position_Limit"])
        high = min(calibration.range_max, registers["Max_Position_Limit"])
        if type(raw) is not int or not low <= raw <= high:
            raise RuntimeError("Visible native position exceeds actual calibrated/raw limits")
        if self.visible_initial_raw is None:
            self.visible_initial_raw = raw
        context = self.context
        # Keep the original declared reference through pauses. Before declaration,
        # the measured start supplies the envelope without moving any actuator.
        reference = (
            self.reference
            if self.reference is not None
            else context["reference"]
            if context is not None
            else robot_position(robot.left_bus, self.visible_initial_raw)
        )
        material = {
            k: int(getattr(calibration, k)) for k in ("drive_mode", "homing_offset", "range_min", "range_max")
        }
        magnitude = abs(getattr(definition, "excursion_norm", 10.0))
        endpoints = [definition.encoded_goal(reference + delta, material) for delta in (-magnitude, magnitude)]
        if any(value is None for value in endpoints) or not max(low, min(endpoints)) <= raw <= min(
            high, max(endpoints)
        ):
            raise RuntimeError("Visible native position left its frozen absolute excursion")
        if getattr(definition, "response_rule", None) is not None:
            self._guard_response(robot, control, observation, definition, position, current)
        if getattr(definition, "intercycle_hold_admission_rule", None) is not None:
            self._guard_hold_admission(robot, control, observation, definition, position, current, now)
        if context is None or control.state != "active":
            return
        direction = None
        sign = -1 if robot.left_bus.apply_drive_mode and calibration.drive_mode else 1
        outward_direction = sign * (1 if getattr(definition, "excursion_norm", 10.0) > 0 else -1)
        phase = context["phase"]
        if phase in {"outbound", "endpoint"}:
            direction = outward_direction
        elif phase in {"return", "origin", "final"}:
            direction = -outward_direction
        elif phase == "acquire" and context["reference"] != context["admitted_reference"]:
            direction = sign * (1 if context["reference"] > context["admitted_reference"] else -1)
        if getattr(definition, "schema_version", 1) == 2:
            direction = self._response_direction(robot, definition, context)
        if direction is not None:
            leg = (context["cycle"], direction)
            if leg != self.visible_phase:
                self.visible_phase, self.visible_peak = leg, raw
            if direction * (raw - self.visible_peak) < -2:
                raise RuntimeError("Visible native position reversed against its intended direction")
            self.visible_peak = max(self.visible_peak, raw) if direction > 0 else min(self.visible_peak, raw)
        action = getattr(robot, "_am1_selected_joint_action", None)
        goal = action.get("expected_goal_raw") if isinstance(action, dict) else None
        if type(goal) is not int:
            return  # Cap, original clocks and absolute geometry still apply without an action trace.
        if goal != self.visible_goal:
            self.visible_goal = goal
            self.visible_current_rows.clear()
            self.visible_position_rows.clear()
        point = (
            position["read_completed_at_monotonic_s"],
            current["read_completed_at_monotonic_s"],
            raw,
            abs(current["ma"]),
        )
        if not self.visible_position_rows or point[0] > self.visible_position_rows[-1][0]:
            self.visible_position_rows.append(point)
        errors = [item for item in self.visible_position_rows if item[2] != goal]
        growing, start, previous, previous_time = 0, None, None, None
        for item in errors:
            error = item[2] - goal
            if previous is not None and previous * error < 0:
                if abs(error) > abs(previous):
                    growing += 1
                    if start is None:
                        start = previous_time
                else:
                    growing, start = 0, None
            previous, previous_time = error, item[0]
            if growing >= 3 and abs(error) > 2 and start is not None and item[0] - start >= 0.2 - 1e-9:
                raise RuntimeError("Visible native fixed-goal growing position instability")
        if self.visible_current_rows and point[1] <= self.visible_current_rows[-1][1]:
            return
        if self.visible_current_rows and point[3] < self.visible_current_rows[-1][3]:
            self.visible_current_rows.clear()  # Any chronological decrease breaks a rising candidate.
        self.visible_current_rows.append(point)
        if phase not in {"endpoint", "origin", "final"}:
            return  # The predefined slow ramp is allowed before response appears.
        for start in range(len(self.visible_current_rows)):
            rows = list(self.visible_current_rows)[start:]
            if (
                len(rows) < 4
                or rows[-1][0] - rows[0][0] < 0.2 - 1e-9
                or rows[-1][1] - rows[0][1] < 0.2 - 1e-9
            ):
                continue
            # Equal plateaus and positive substeps remain in chronological order.
            selected, rises = rows[0][3], 0
            for item in rows[1:]:
                if item[3] - selected >= 6.5 - 1e-9:
                    selected, rises = item[3], rises + 1
            if rises >= 3 and max(item[2] for item in rows) - min(item[2] for item in rows) <= 2:
                raise RuntimeError("Visible native static endpoint adverse current rise")

    def _clear_hold_admission(self, *, discard_departure=False):
        self._hold_binding = self._hold_cursor = self._hold_anchor = None
        self._hold_rows.clear()
        self.intercycle_hold_admission = None
        self._hold_pending_departure = None
        if discard_departure:
            self._hold_departure = None
        # A matched returned first departure is an original-task fact. Pause
        # clears current eligibility, not the fact that cycle two already began.
        self._hold_departed = self._hold_departure is not None

    def abort_hold_admission(self):
        self._clear_hold_admission(discard_departure=True)

    @staticmethod
    def _hold_original_key(binding, definition, reference, calibration):
        return {**{k: binding[k] for k in ("run_id", "host_incarnation")},
                "experiment_sha256": definition.sha256, "reference": reference,
                "calibration": calibration, "measured_origin_raw": definition.measured_origin(reference, calibration)}

    def _prepare_hold_departure(self, robot, definition, context, binding, marker):
        keys = {"version", "mode", "run_id", "host_incarnation", "epoch", "intent_revision", "action_sequence"}
        if (not isinstance(marker, dict) or set(marker) != keys or type(marker["version"]) is not int
                or marker["version"] != 1 or marker["mode"] != "active"
                or any(marker[k] != v for k, v in binding.items())
                or any(type(marker[k]) is not int or marker[k] < 0
                       for k in ("epoch", "intent_revision", "action_sequence"))):
            raise RuntimeError("Selected intercycle hold incoming owner marker is unavailable")
        incoming = {**binding, "experiment_sha256": definition.sha256,
                    "epoch": marker["epoch"], "intent_revision": marker["intent_revision"]}
        calibration = {k: int(getattr(robot.left_bus.calibration[MOTOR], k))
                       for k in ("drive_mode", "homing_offset", "range_min", "range_max")}
        original_key = self._hold_original_key(binding, definition, context["reference"], calibration)
        if self._hold_origin is not None:
            original = self._hold_origin
            retained_key = {**{k: original[k] for k in ("run_id", "host_incarnation")},
                "experiment_sha256": original["context"]["experiment_sha256"],
                "reference": original["context"]["reference"], "calibration": original["motor"]["calibration"],
                "measured_origin_raw": original["position"]["raw"]}
            if original_key != retained_key:
                self._clear_hold_admission(discard_departure=True)
                raise RuntimeError("Selected intercycle hold original task identity changed before write")
        if self._hold_departure is not None and self._hold_departure["original_key"] != original_key:
            self._clear_hold_admission(discard_departure=True)
            raise RuntimeError("Selected intercycle hold returned departure belongs to another task")
        if self._hold_binding is not None and incoming != self._hold_binding:
            self._clear_hold_admission()
        if context["phase"] == "rejoin":
            self._clear_hold_admission()
        if context["cycle"] == 2 and context["phase"] == "outbound" and not self._hold_departed:
            if (self._hold_binding != incoming or self._hold_origin is None or self._hold_anchor is None
                    or not definition.intercycle_hold_admission_valid(self.intercycle_hold_admission,
                        self._hold_anchor, self._hold_origin, now=self.clock())
                    or marker["action_sequence"] <= self._hold_anchor["action_sequence"]):
                raise RuntimeError("Selected intercycle hold native admission is unavailable before departure")
            self._hold_pending_departure = copy.deepcopy(marker)

    def _guard_hold_admission(self, robot, control, observation, definition, position, current, now):
        if control.state != "active" or self.context is None:
            self._clear_hold_admission()
            return
        marker = self._matched_marker(control, observation if isinstance(observation, dict) else {})
        if (marker is None or not _number(now) or marker["acquired_at_monotonic_s"] > now
                or any(type(marker[k]) is not int or marker[k] < 0 for k in
                       ("epoch", "intent_revision", "accepted_action_sequence", "observation_id"))):
            raise RuntimeError("Selected intercycle hold original marker is unavailable")
        binding = {**control.binding, "experiment_sha256": definition.sha256,
                   "epoch": control.epoch, "intent_revision": control.intent_revision}
        if self._hold_binding != binding:
            self._clear_hold_admission()
            self._hold_binding = binding
        row = {**self._base(robot, control, marker), "position": copy.deepcopy(position),
               "current": copy.deepcopy(current), "captured_at_monotonic_s": now}
        row.pop("intercycle_hold_admission", None)
        context, action = self.context, row["action"]
        if not definition.measurement_proof_valid(row, self.reference, row["motor"]["calibration"],
                run_id=binding["run_id"], host_incarnation=binding["host_incarnation"]):
            raise RuntimeError("Selected intercycle hold actual native source is unavailable")
        original_key = (binding["run_id"], binding["host_incarnation"], definition.sha256)
        if self._hold_origin is not None and self._hold_origin["context"]["experiment_sha256"] != definition.sha256:
            raise RuntimeError("Selected intercycle hold original definition changed")
        if (self._hold_origin is None and context["phase"] == "baseline" and context["progress_s"] == 0
                and position["raw"] == definition.measured_origin(self.reference, row["motor"]["calibration"])):
            self._hold_origin = copy.deepcopy(row)
        if self._hold_origin is not None and original_key[:2] != (
                self._hold_origin["run_id"], self._hold_origin["host_incarnation"]):
            raise RuntimeError("Selected intercycle hold original owner changed")
        if context["cycle"] == 2 and context["phase"] == "outbound":
            if not self._hold_departed:
                pending = self._hold_pending_departure
                if (pending is None or any(pending[k] != binding[k]
                        for k in ("run_id", "host_incarnation", "epoch", "intent_revision"))
                        or pending["action_sequence"] != row["action_sequence"]):
                    raise RuntimeError("Selected intercycle hold departure lacks its returned matching write")
                self._hold_departure = {
                    "original_key": self._hold_original_key(binding, definition, self.reference, row["motor"]["calibration"]),
                    "epoch": row["epoch"], "intent_revision": row["intent_revision"],
                    "action_sequence": row["action_sequence"],
                    "certificate": self._response_native_certificate(context, marker, position, current),
                }
                self._hold_departed = True
                self._hold_pending_departure = None
            self.intercycle_hold_admission = None
            return
        if context["phase"] != "intercycle_hold" or context["cycle"] != 1:
            self._hold_cursor = self._hold_anchor = None
            self._hold_rows.clear()
            self.intercycle_hold_admission = None
            return
        accepted = control.accepted_action
        if (not isinstance(accepted, dict) or any(not _number(accepted.get(k)) or accepted[k] != 0
                for k in ("x.vel", "y.vel", "theta.vel", "lift_axis.vel"))
                or accepted.get(POSITION) != self.reference
                or action["expected_goal_raw"] != definition.encoded_goal(self.reference, row["motor"]["calibration"])):
            raise RuntimeError("Selected intercycle hold accepted body-zero action is unavailable")
        certificate = definition._hold_certificate(row)
        if self._hold_cursor is None:
            self._hold_cursor = {"action_sequence": row["action_sequence"],
                "write_completed_at_monotonic_s": action["write_completed_at_monotonic_s"],
                "certificate": certificate}
        item = [row["observation_id"], row["action_sequence"], context["progress_s"], position["raw"],
                position["read_started_at_monotonic_s"], position["read_completed_at_monotonic_s"], current["raw"],
                current["read_started_at_monotonic_s"], current["read_completed_at_monotonic_s"],
                marker["acquired_at_monotonic_s"], action["write_started_at_monotonic_s"],
                action["write_completed_at_monotonic_s"]]
        done = self._hold_cursor["write_completed_at_monotonic_s"]
        previous = self._hold_rows[-1] if self._hold_rows else None
        if previous is not None and (item[0] <= previous[0] or any(item[k] <= previous[k] for k in (4, 5, 7, 8, 9))):
            self._hold_rows.clear()
            self.intercycle_hold_admission = None
            return
        if (previous is not None and item[0] != previous[0] + 1
                or min(item[4], item[7]) < done or self._hold_origin is None
                or abs(item[3] - self._hold_origin["position"]["raw"]) > 40):
            self._hold_rows.clear()
            self.intercycle_hold_admission = None
            return
        self._hold_rows.append(item)
        self._hold_anchor = row
        self.intercycle_hold_admission = None
        for count in range(3, len(self._hold_rows) + 1):
            rows = list(self._hold_rows)[-count:]
            if any(rows[-1][k] - rows[0][k] < .2 - 1e-9 for k in (5, 8)):
                continue
            proof = {"version": 1, "status": "ready", "binding": binding,
                     "calibration": row["motor"]["calibration"], "reference_normalized": self.reference,
                     "measured_origin_raw": self._hold_origin["position"]["raw"],
                     "expected_goal_raw": definition.encoded_goal(self.reference, row["motor"]["calibration"]),
                     "original_reference": {"epoch": self._hold_origin["epoch"],
                         "intent_revision": self._hold_origin["intent_revision"],
                         "certificate": definition._hold_certificate(self._hold_origin)},
                     "hold_cursor": self._hold_cursor, "qualified_at_monotonic_s": now, "rows": rows}
            if definition.intercycle_hold_admission_valid(proof, row, self._hold_origin, now=now):
                self.intercycle_hold_admission = copy.deepcopy(proof)
            break  # Only the freshest contiguous qualifying suffix is eligible.

    @staticmethod
    def _response_direction(robot, definition, context):
        if not isinstance(context, dict):
            return None
        calibration = robot.left_bus.calibration[MOTOR]
        sign = -1 if robot.left_bus.apply_drive_mode and calibration.drive_mode else 1
        outward = sign * (1 if definition.excursion_norm > 0 else -1)
        if context["phase"] in {"outbound", "endpoint"}:
            return outward
        if context["phase"] in {"return", "origin", "final", "intercycle_hold"}:
            return -outward
        if context["phase"] == "rejoin":
            rejoin = context["rejoin"]
            delta = rejoin["retained_target"]-rejoin["held_target"]
            return None if delta == 0 else sign*(1 if delta > 0 else -1)
        if context["phase"] == "acquire" and context["reference"] != context["admitted_reference"]:
            return sign * (1 if context["reference"] > context["admitted_reference"] else -1)
        return None

    def _prepare_response_rejoin(self, robot, definition, metadata):
        """Validate the measured bridge before any native write; retain response obligations."""
        rejoin = metadata["rejoin"]
        previous = self._response_rejoin
        if rejoin is None:
            if previous is not None and (previous["value"]["elapsed_s"] != previous["value"]["duration_s"]
                                         or metadata["progress_s"] != previous["progress_s"]):
                self._response_refuse(robot, "Selected response rejoin ended before its retained goal")
            self._response_rejoin = None
            return
        now = self.clock()
        if previous is None:
            position = getattr(robot, "_am1_selected_joint_position", None)
            calibration = {k:int(getattr(robot.left_bus.calibration[MOTOR],k)) for k in
                           ("drive_mode","homing_offset","range_min","range_max")}
            goal = definition.encoded_goal(rejoin["held_target"], calibration)
            if (rejoin["elapsed_s"] != 0 or self.profile is None or self.context is not None
                    or not 0 <= now-rejoin["acquired_at_monotonic_s"] <= .5
                    or not isinstance(position,dict) or type(position.get("raw")) is not int
                    or not _number(position.get("read_started_at_monotonic_s"))
                    or not _number(position.get("read_completed_at_monotonic_s"))
                    or not 0 <= position["read_started_at_monotonic_s"] <= position["read_completed_at_monotonic_s"] <= now
                    or now-position["read_started_at_monotonic_s"] > .5
                    or abs(goal-position["raw"]) > 1):
                self._response_refuse(robot, "Selected response rejoin measured hold is unavailable")
        else:
            old = previous["value"]
            stable = ("held_target","retained_target","duration_s","observation_id","acquired_at_monotonic_s")
            delta = rejoin["elapsed_s"]-old["elapsed_s"]
            if (any(rejoin[k] != old[k] for k in stable) or metadata["progress_s"] != previous["progress_s"]
                    or not 0 <= delta <= min(.1,now-previous["at"])+1e-9):
                self._response_refuse(robot, "Selected response rejoin rate or frozen origin is unavailable")
        self._response_rejoin = {"value":copy.deepcopy(rejoin),
            "at":previous["at"] if previous is not None and delta == 0 else now,
            "progress_s":metadata["progress_s"]}

    def _response_refuse(self, robot, reason, *, failure_kind="unavailable"):
        if self.response_boundary is None:
            self.response_boundary = {"version": 1, "status": "unavailable"}
        else:
            self.response_boundary = dict(self.response_boundary, status="refused")
        self.response_boundary["failure_kind"] = failure_kind
        self.capture_fault(
            robot, reason, source="response_guard",
            current=getattr(robot, "_am1_selected_joint_current", None),
            ordinary_limit=getattr(robot, "_joint_current_limit_ma", 100),
        )
        fault = getattr(robot, "_am1_selected_joint_fault", None)
        if isinstance(fault, dict) and fault.get("source") == "response_guard":
            fault["comparison"] = reason
        raise RuntimeError(reason)

    def _response_tracking_current(self, robot):
        """Only the last valid active tracking acquisition can retire exposure."""
        try:
            boundary = self.response_boundary
            if (self._response_continuity_lost or not isinstance(boundary, dict)
                    or boundary.get("status") != "tracking"
                    or boundary["signed_commanded_error_raw"] >= boundary["commanded_error_threshold_raw"]):
                return False
            definition = getattr(robot, "_am1_selected_elbow_experiment", None)
            if definition is None or not definition.context_valid(self.context):
                return False
            actual = getattr(robot, "_am1_visible_control_binding", {})
            expected = self._response_window["binding"]
            if (actual.get("run_id"), actual.get("host_incarnation"), actual.get("experiment_sha256")) != (
                expected[0], expected[1], expected[4]
            ):
                return False
            position, current = robot._am1_selected_joint_position, robot._am1_selected_joint_current
            acquisitions = tuple(cache[key] for cache in (position, current) for key in
                                 ("read_started_at_monotonic_s", "read_completed_at_monotonic_s"))
            now = self.clock()
            return (
                acquisitions == self._response_last_acquisitions
                and all(_number(t) and 0 <= t <= now for t in acquisitions)
                and all(now - cache["read_started_at_monotonic_s"] <= 0.5 for cache in (position, current))
                and type(position.get("raw")) is int and position["raw"] == boundary["last_position_raw"]
                and type(current.get("raw")) is int and current["raw"] == self._response_last_current_raw
                and current.get("ma") == current["raw"] * 6.5
                and robot._am1_selected_joint_action.get("expected_goal_raw") == boundary["last_encoded_goal_raw"]
            )
        except Exception:
            return False  # A missing/invalid diagnostic must never obstruct protective release.

    def _response_startup_pair(self, robot, definition):
        now = self.clock()
        if (self.visible_started_at is not None
                and now - self.visible_started_at >= definition.live_s):
            raise RuntimeError(f"Visible {definition.live_s:g}-second native Live ceiling expired")
        if not definition.settings_valid(self.visible_settings):
            self._response_refuse(robot, "Selected response startup settings are unavailable")
        position = getattr(robot, "_am1_selected_joint_position", None)
        current = getattr(robot, "_am1_selected_joint_current", None)
        for cache in (position, current):
            if (not isinstance(cache, dict)
                    or not all(_number(cache.get(k)) for k in
                               ("read_started_at_monotonic_s", "read_completed_at_monotonic_s"))
                    or not 0 <= cache["read_started_at_monotonic_s"] <= cache["read_completed_at_monotonic_s"] <= now
                    or now - cache["read_started_at_monotonic_s"] > .5):
                self._response_refuse(robot, "Selected response startup original acquisition is unavailable")
        if (type(current.get("raw")) is not int or not _number(current.get("ma"))
                or current["ma"] != current["raw"] * 6.5):
            self._response_refuse(robot, "Selected response startup actual current is unavailable")
        self.check_visible_current(robot, current.get("raw"), getattr(robot, "_joint_current_limit_ma", 100),
                                   source="cached_guard", sample=current)
        acquisitions = tuple(cache[k] for cache in (position, current) for k in
                             ("read_started_at_monotonic_s", "read_completed_at_monotonic_s"))
        if (self._response_startup_acquisitions is not None
                and any(new < old for new, old in zip(acquisitions, self._response_startup_acquisitions))):
            self._response_refuse(robot, "Selected response startup original acquisition regressed")
        calibration = {k: int(getattr(robot.left_bus.calibration[MOTOR], k)) for k in
                       ("drive_mode", "homing_offset", "range_min", "range_max")}
        registers = self.visible_settings["before"]["registers"]
        if (type(position.get("raw")) is not int
                or not max(calibration["range_min"], registers["Min_Position_Limit"]) <= position["raw"]
                <= min(calibration["range_max"], registers["Max_Position_Limit"])):
            self._response_refuse(robot, "Selected response startup actual position is unavailable")
        self._response_startup_acquisitions = acquisitions
        return now, position, calibration

    def _prepare_response_startup_hold(self, robot, action, definition):
        now, position, calibration = self._response_startup_pair(robot, definition)
        if (self.reference is not None or self.profile is not None or self._response_window is not None
                or self._response_continuity_lost):
            self._response_refuse(robot, "Selected response finite context is missing before active actuation")
        body = {"x.vel", "y.vel", "theta.vel", "lift_axis.vel"}
        if (not body <= set(action) or any(not _number(action[k]) or action[k] != 0 for k in body)
                or any(k not in body and not (k.startswith("arm_") and k.endswith(".pos")) for k in action)
                or any(not _number(v) for v in action.values())):
            self._response_refuse(robot, "Selected response startup requires zero body intent")
        requested = action.get(POSITION)
        measured = definition.measured_origin(requested, calibration)
        if not definition.startup_hold_valid(requested, measured, calibration):
            self._response_refuse(robot, "Selected response startup measured hold is unavailable")
        startup = self._response_startup_hold
        binding = getattr(robot, "_am1_visible_control_binding", None)
        goal = definition.encoded_goal(requested, calibration)
        if startup is None:
            # One tick of sample-to-send settling is explicit; never clip, drift or renew the hold.
            if abs(measured - position["raw"]) > 1:
                self._response_refuse(robot, "Selected response startup requested movement is unavailable")
            self._response_startup_hold = {"requested": requested, "measured_raw": measured,
                                          "goal_raw": goal, "calibration": calibration,
                                          "binding": copy.deepcopy(binding), "started_at": now}
        elif (requested != startup["requested"] or goal != startup["goal_raw"]
              or calibration != startup["calibration"] or binding != startup["binding"]):
            self._response_refuse(robot, "Selected response startup frozen hold changed")
        elif now - startup["started_at"] >= 20:
            self._response_refuse(robot, "Selected response startup measured hold exceeded 20 seconds")
        elif abs(position["raw"] - startup["goal_raw"]) >= definition.startup_hold_error_raw:
            self._response_refuse(robot, "Selected response startup held position left tracking bound")

    def _guard_response_startup_hold(self, robot, control, marker, definition, position, current):
        now, _, calibration = self._response_startup_pair(robot, definition)
        startup = self._response_startup_hold
        action = getattr(robot, "_am1_selected_joint_action", None)
        if (startup is None or marker is None or self.reference is not None or self.profile is not None
                or self._response_window is not None or self._response_continuity_lost
                or startup["binding"] != getattr(robot, "_am1_visible_control_binding", None)
                or calibration != startup["calibration"]
                or not isinstance(action, dict) or action.get("requested") != startup["requested"]
                or action.get("expected_goal_raw") != startup["goal_raw"]
                or not definition.startup_hold_valid(action.get("requested"), startup["measured_raw"], calibration)
                or action.get("sync_write_returned") is not True):
            self._response_refuse(robot, "Selected response startup active hold is unavailable")
        if now - startup["started_at"] >= 20:
            self._response_refuse(robot, "Selected response startup measured hold exceeded 20 seconds")
        if abs(position["raw"] - startup["goal_raw"]) >= definition.startup_hold_error_raw:
            self._response_refuse(robot, "Selected response startup held position left tracking bound")
        if (not all(_number(action.get(k)) for k in
                    ("write_started_at_monotonic_s", "write_completed_at_monotonic_s"))
                or not 0 <= action["write_started_at_monotonic_s"] <= action["write_completed_at_monotonic_s"]
                <= position["read_started_at_monotonic_s"]
                or max(position["read_completed_at_monotonic_s"], current["read_completed_at_monotonic_s"])
                > marker["acquired_at_monotonic_s"]):
            self._response_refuse(robot, "Selected response startup action/acquisition ordering is unavailable")

    @staticmethod
    def _response_native_certificate(context, marker, position, current):
        # The full original native row remains in the normal evidence stream.
        # These bounded clocks/IDs identify it without duplicating its payload.
        return {
            "observation_id": marker["observation_id"],
            "progress_s": context["progress_s"],
            "phase": context["phase"],
            "position_raw": position["raw"],
            "position_read_started_at_monotonic_s": position["read_started_at_monotonic_s"],
            "position_read_completed_at_monotonic_s": position["read_completed_at_monotonic_s"],
            "current_read_started_at_monotonic_s": current["read_started_at_monotonic_s"],
            "current_read_completed_at_monotonic_s": current["read_completed_at_monotonic_s"],
            "acquired_at_monotonic_s": marker["acquired_at_monotonic_s"],
        }


    @staticmethod
    def _response_coarse_policy(definition):
        from examples.alohamini.am1_session_contract import LEFT_ELBOW_COARSE_BENCH_V2

        # The evidence supports this named condition, not every geometry-valid
        # descriptor that can be represented under the new envelope.
        return (
            definition.response_rule.get("qualified_endpoint_dwell") is True
            and definition.sha256 == LEFT_ELBOW_COARSE_BENCH_V2.sha256
        )

    def _response_coarse_origin(self, definition, binding, marker, position, current, direction):
        context = self.context
        if not self._response_coarse_policy(definition):
            return
        key = (*binding, context["cycle"], direction)
        candidate, origin, dwell = (
            self._response_origin_candidate,
            self._response_leg_origin,
            self._response_dwell,
        )
        if origin is not None and origin["key"] != key:
            self._response_leg_origin = self._response_dwell = None
        if dwell is not None and dwell["key"] != key:
            self._response_dwell = None
        phase = context["phase"]
        certificate = self._response_native_certificate(context, marker, position, current)
        if phase in {"baseline", "intercycle_hold"}:
            self._response_leg_origin = self._response_dwell = None
            self._response_origin_candidate = {
                "binding": binding,
                "cycle": 1 if phase == "baseline" else 2,
                "certificate": certificate,
            }
            return
        if phase == "outbound":
            if self._response_leg_origin is None and candidate is not None:
                previous = candidate["certificate"]
                if (
                    candidate["binding"] == binding
                    and candidate["cycle"] == context["cycle"]
                    and marker["acquired_at_monotonic_s"] - previous["position_read_started_at_monotonic_s"]
                    <= 0.5
                    and marker["acquired_at_monotonic_s"] - previous["current_read_started_at_monotonic_s"] <= 0.5
                ):
                    self._response_leg_origin = {"key": key, "origin": previous, "first_outbound": certificate}
            self._response_origin_candidate = None
            return
        self._response_origin_candidate = None
        if phase != "endpoint":
            # Recovery/rejoin, a new cycle or owner epoch must acquire a fresh
            # actual leg origin. Paused/coasting movement never earns this proof.
            self._response_leg_origin = self._response_dwell = None


    def _response_coarse_dwell(self, definition, binding, marker, position, current, latest, window):
        context, origin = self.context, self._response_leg_origin
        if (
            not self._response_coarse_policy(definition)
            or context["phase"] != "endpoint"
            or origin is None
            or origin["key"] != (*binding, context["cycle"], latest["direction"])
            or context["requested_target"] != context["endpoint_target"]
        ):
            return False
        response = latest["direction"] * (position["raw"] - origin["origin"]["position_raw"])
        if response < definition.minimum_response_raw:
            self._response_dwell = None
            return False
        dwell = self._response_dwell
        if dwell is None:
            # A first timely endpoint acquisition can explicitly retire its
            # late moving window. Already expired exposure remains a refusal.
            if window is not None and latest["elapsed_s"] >= definition.response_rule["window_s"] - 1e-9:
                return False
            retired = (
                None
                if window is None
                else dict(
                    latest,
                    status="retired_at_coarse_qualified_dwell",
                    anchor_phase=window["coarse_anchor_phase"],
                    anchor_progress_s=window["coarse_anchor_progress_s"],
                )
            )
            proof = {
                "version": 1,
                "binding": dict(
                    zip(("run_id", "host_incarnation", "epoch", "intent_revision", "experiment_sha256"), binding)
                ),
                "cycle": context["cycle"],
                "direction": latest["direction"],
                "leg_origin_raw": origin["origin"]["position_raw"],
                "minimum_leg_response_raw": definition.minimum_response_raw,
                "signed_leg_response_raw": response,
                "origin": copy.deepcopy(origin["origin"]),
                "first_outbound": copy.deepcopy(origin["first_outbound"]),
                "qualified": self._response_native_certificate(context, marker, position, current),
                "retired_window": retired,
            }
            self._response_dwell = dwell = {
                "key": origin["key"],
                "goal": latest["last_encoded_goal_raw"],
                "proof": proof,
            }
        if latest["last_encoded_goal_raw"] != dwell["goal"]:
            return False
        self._response_window = None
        proof = dict(dwell["proof"], signed_leg_response_raw=response)
        self.response_boundary = dict(latest, status="coarse_qualified_dwell", coarse_dwell_proof=proof)
        return True  # Only this response policy; outer local guards still run.

    def _guard_response(self, robot, control, observation, definition, position, current):
        rule = definition.response_rule
        if control.state != "active":
            self._response_origin_candidate = self._response_leg_origin = self._response_dwell = None
            if self._response_window is not None:
                if self._response_tracking_current(robot):
                    self._response_window = None
                else:
                    self._response_continuity_lost = True
                    self.response_boundary = dict(self.response_boundary, status="continuity_lost")
            return  # Existing protective hold/cap/cleanup always retain authority.
        if self._response_continuity_lost:
            self._response_refuse(robot, "Selected response continuity unavailable after protective hold",
                                  failure_kind="continuity_lost")
        context = self.context
        marker = self._matched_marker(control, observation if isinstance(observation, dict) else {},
                                      require_context=context is not None or self.profile is not None)
        if context is None and self.reference is None and self.profile is None:
            self._guard_response_startup_hold(robot, control, marker, definition, position, current)
            return
        if marker is None or not definition.context_valid(context):
            self._response_refuse(robot, "Selected response active source/context is unavailable")
        if (rule.get("qualified_endpoint_dwell") is True
                and marker["acquired_at_monotonic_s"] > self.clock()):
            self._response_refuse(robot, "Selected response original marker acquisition is unavailable")
        action = getattr(robot, "_am1_selected_joint_action", None)
        material = {k: int(getattr(robot.left_bus.calibration[MOTOR], k))
                    for k in ("drive_mode", "homing_offset", "range_min", "range_max")}
        goal = definition.encoded_goal(context["requested_target"], material)
        if (not isinstance(action, dict) or type(action.get("expected_goal_raw")) is not int
                or action["expected_goal_raw"] != goal or action.get("sync_write_returned") is not True
                or action.get("requested") != context["requested_target"]):
            self._response_refuse(robot, "Selected response actual encoded action is unavailable")
        if (not all(_number(action.get(k)) for k in
                    ("write_started_at_monotonic_s", "write_completed_at_monotonic_s"))
                or not 0 <= action["write_started_at_monotonic_s"] <= action["write_completed_at_monotonic_s"]
                <= position["read_started_at_monotonic_s"]
                or max(position["read_completed_at_monotonic_s"], current["read_completed_at_monotonic_s"])
                > marker["acquired_at_monotonic_s"]):
            self._response_refuse(robot, "Selected response action/acquisition ordering is unavailable")
        acquisitions = tuple(cache[key] for cache in (position, current) for key in
                             ("read_started_at_monotonic_s", "read_completed_at_monotonic_s"))
        if self._response_last_acquisitions is not None:
            if any(new < old for new, old in zip(acquisitions, self._response_last_acquisitions)):
                self._response_refuse(robot, "Selected response original acquisition regressed")
            if any(new == old for new, old in zip(acquisitions, self._response_last_acquisitions)):
                return  # Duplicate/partial acquisitions neither credit motion nor reset exposure.
        direction = self._response_direction(robot, definition, context)
        leg = (context["cycle"], direction)
        binding = (control.binding["run_id"], control.binding["host_incarnation"],
                   control.epoch, control.intent_revision, definition.sha256)
        window = self._response_window
        if window is not None and (window["leg"] != leg or window["binding"] != binding):
            self._response_refuse(robot, "Selected response active leg/owner continuity is unavailable",
                                  failure_kind="continuity_lost")
        self._response_last_acquisitions = acquisitions
        self._response_last_current_raw = current["raw"]
        self._response_coarse_origin(definition, binding, marker, position, current, direction)
        raw = position["raw"]
        error = 0 if direction is None else direction * (goal - raw)
        latest = {
            "version": 1, "commanded_error_threshold_raw": rule["commanded_error_raw"],
            "minimum_response_raw": rule["minimum_response_raw"], "window_s": rule["window_s"],
            "cycle": context["cycle"], "direction": direction, "last_position_raw": raw,
            "last_encoded_goal_raw": goal, "signed_commanded_error_raw": error,
            "last_position_started_at_monotonic_s": position["read_started_at_monotonic_s"],
            "last_current_started_at_monotonic_s": current["read_started_at_monotonic_s"],
            "last_position_completed_at_monotonic_s": position["read_completed_at_monotonic_s"],
            "last_current_completed_at_monotonic_s": current["read_completed_at_monotonic_s"],
        }
        if window is not None:
            response = direction * (raw - window["raw"])
            elapsed = max(position["read_completed_at_monotonic_s"] - window["position_at"],
                          current["read_completed_at_monotonic_s"] - window["current_at"])
            window["count"] += 1
            latest.update(
                anchor_position_raw=window["raw"],
                anchor_position_started_at_monotonic_s=window["position_started"],
                anchor_current_started_at_monotonic_s=window["current_started"],
                anchor_position_completed_at_monotonic_s=window["position_at"],
                anchor_current_completed_at_monotonic_s=window["current_at"],
                signed_response_raw=response, elapsed_s=elapsed, distinct_sample_count=window["count"],
            )
            self.response_boundary = dict(latest, status="pending")
            if self._response_coarse_dwell(definition, binding, marker, position, current, latest, window):
                return
            if error < rule["commanded_error_raw"]:
                self.response_boundary["status"] = "tracking"
                if response >= rule["minimum_response_raw"]:
                    self._response_window = None
                return  # Keep the anchor across subthreshold two-tick jitter.
            if (elapsed > rule["window_s"] + 1e-9
                    or elapsed >= rule["window_s"] - 1e-9 and response < rule["minimum_response_raw"]):
                self._response_refuse(robot, "Selected response not established within 1.2 seconds",
                                      failure_kind="nonresponse")
            if response >= rule["minimum_response_raw"]:
                self._response_window = None
            else:
                return
        elif self._response_coarse_dwell(definition, binding, marker, position, current, latest, window):
            return
        elif error < rule["commanded_error_raw"]:
            self.response_boundary = dict(latest, status="tracking")
            return
        if error >= rule["commanded_error_raw"]:
            self._response_window = {
                "leg": leg, "binding": binding, "raw": raw, "position_at": position["read_completed_at_monotonic_s"],
                "position_started": position["read_started_at_monotonic_s"],
                "current_started": current["read_started_at_monotonic_s"],
                "current_at": current["read_completed_at_monotonic_s"], "count": 1,
                **({"coarse_anchor_phase": context["phase"],
                    "coarse_anchor_progress_s": context["progress_s"]}
                   if rule.get("qualified_endpoint_dwell") is True else {}),
            }
            self.response_boundary = dict(latest, status="pending", anchor_position_raw=raw,
                anchor_position_started_at_monotonic_s=position["read_started_at_monotonic_s"],
                anchor_current_started_at_monotonic_s=current["read_started_at_monotonic_s"],
                anchor_position_completed_at_monotonic_s=position["read_completed_at_monotonic_s"],
                anchor_current_completed_at_monotonic_s=current["read_completed_at_monotonic_s"],
                signed_response_raw=0, elapsed_s=0.0, distinct_sample_count=1)

    @staticmethod
    def recovery_gain_proof(robot):
        """Expose bounded actual settings, keeping the durable write ledger private."""
        trial = getattr(robot, "_am1_elbow_integral_trial", None)
        if trial is None:
            return None
        evidence = trial.evidence
        keys = (
            "schema_version",
            "state",
            "status",
            "restore_required",
            "integral",
            "binding",
            "motor",
            "motor_id",
            "motor_model",
            "cached_calibration",
            "original_registers",
            "original_bytes",
            "active_registers",
            "active_bytes",
            "active_verified",
            "active_verified_at_monotonic_s",
            "first_failure",
        )
        proof = {key: copy.deepcopy(evidence[key]) for key in keys if key in evidence}
        rows = [
            row
            for row in evidence.get("events", [])
            if isinstance(row, dict)
            and row.get("phase") == "baseline"
            and row.get("operation") == "read"
            and _number(row.get("started_at_monotonic_s"))
            and _number(row.get("completed_at_monotonic_s"))
        ]
        if rows:
            proof["original_read_window"] = {
                "started_at_monotonic_s": min(row["started_at_monotonic_s"] for row in rows),
                "completed_at_monotonic_s": max(row["completed_at_monotonic_s"] for row in rows),
            }
        if len(json.dumps(proof, allow_nan=False, separators=(",", ":")).encode()) > 4096:
            raise RuntimeError("Recovery actual gain proof exceeds its bounded feedback frame")
        return proof

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
            **({"intercycle_hold_admission": copy.deepcopy(self.intercycle_hold_admission)}
               if self.intercycle_hold_admission is not None else {}),
            **({"response_boundary": copy.deepcopy(self.response_boundary)}
               if self.response_boundary is not None else {}),
            **(
                {"visible_control": self.visible_control_proof(robot)}
                if getattr(robot, "_am1_visible_control_binding", None) is not None
                else {}
            ),
            **(
                {"gain_trial": self.recovery_gain_proof(robot)}
                if getattr(robot, "_am1_elbow_integral_trial", None) is not None
                else {}
            ),
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
        if self.visible_settings is not None:
            names["acceleration_raw"] = "Acceleration"
            names["lock"] = "Lock"
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

    def _matched_marker(self, control, observation, *, require_context=True):
        if (not self.enabled or require_context and self.context is None
                or control.binding is None or control.state != "active"):
            return None
        marker = observation.get("_am1_local_feedback", {})
        if (
            not isinstance(marker, dict)
            or type(marker.get("version")) is not int
            or marker["version"] != 1
            or marker.get("state") != "active"
            or any(
                type(marker.get(key)) is not int
                for key in ("epoch", "intent_revision", "accepted_action_sequence")
            )
            or any(marker.get(k) != v for k, v in control.binding.items())
            or marker.get("epoch") != control.epoch
            or marker.get("intent_revision") != control.intent_revision
            or marker.get("accepted_action_sequence") != control.accepted_action_sequence
            or type(marker.get("observation_id")) is not int
            or not _number(marker.get("acquired_at_monotonic_s"))
        ):
            return None
        return marker

    def publish(self, robot, control, observation):
        """Copy actual caches before response; never read, emit or renew timestamps."""
        outer = observation.get("_am1_local_feedback")
        if isinstance(outer, dict):
            outer.pop("selected_joint", None)
        marker = self._matched_marker(control, observation)
        if marker is None:
            return
        marker["selected_joint"] = {
            **self._base(robot, control, marker),
            "position": copy.deepcopy(getattr(robot, "_am1_selected_joint_position", None)),
            "current": copy.deepcopy(getattr(robot, "_am1_selected_joint_current", None)),
            "sparse_receipts": copy.deepcopy(self.sparse_receipts),
            **({"goal_readbacks": copy.deepcopy(self.goal_readbacks)}
               if getattr(robot, "_am1_selected_elbow_experiment", None) is not None else {}),
        }

    def _emit_sparse(self, row):
        self._retain_sparse(row)
        self.emit(row)

    def _retain_sparse(self, row):
        retained = self.goal_readbacks if "checkpoint_s" in row else self.sparse_receipts
        retained[row["sample_key"]] = copy.deepcopy(row)

    def report(self, robot, control, observation):
        marker = self._matched_marker(control, observation)
        if marker is None:
            return  # No new labeled acquisition can be inferred from a stale/foreign view.
        row = self._base(robot, control, marker)
        position = copy.deepcopy(getattr(robot, "_am1_selected_joint_position", None))
        current = copy.deepcopy(getattr(robot, "_am1_selected_joint_current", None))
        observation_id = marker.get("observation_id")
        if type(observation_id) is int and observation_id > self.last_observation_id:
            self.last_observation_id = observation_id
            self.emit({**row, "kind": "native_trace", "position": position, "current": current})
        key = self.context["sample_key"]
        checkpoint = None
        if key is None or key in self.attempted:
            definition = getattr(robot, "_am1_selected_elbow_experiment", None)
            if definition is None:
                return
            progress = self.context["progress_s"] - self.context["acquisition_s"]
            for index, point in enumerate(definition.goal_readback_checkpoints_s):
                if progress >= point and index not in self.goal_checkpoint_attempted:
                    self.goal_checkpoint_attempted.add(index)
                    key, checkpoint = f"goal_checkpoint_{index + 1}", point
                    break
            if checkpoint is None:
                return
        else:
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
            **({"checkpoint_s": checkpoint} if checkpoint is not None else {}),
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
            self._emit_sparse(sparse)
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
            completed = self.clock()
            sparse["trace"] = trace
            decoded = self._decoded(bus, payload)
            sparse["decoded"] = decoded
            source = "goal_checkpoint" if checkpoint is not None else "sparse_readback"
            self.last_live_goal = {
                "source": source, "sample_key": key,
                "goal_position_raw": decoded["goal_position_raw"],
                "read_started_at_monotonic_s": started, "read_completed_at_monotonic_s": completed,
                "torque_enable": decoded["torque_enable"], "operating_mode": decoded["operating_mode"],
                "status": decoded["status"], "context": copy.deepcopy(self.context),
                "action": copy.deepcopy(row["action"]),
            }
            if decoded["status"] != 0 or decoded["torque_enable"] != 1 or decoded["operating_mode"] != 0:
                raise RuntimeError("Selected joint readback reported status/torque/mode fault")
            if self.visible_settings is not None:
                self.check_visible_current(
                    robot, decoded["present_current_raw"], getattr(robot, "_joint_current_limit_ma", 100),
                    source=source,
                    sample={
                        "raw": decoded["present_current_raw"], "ma": decoded["present_current_ma"],
                        "read_started_at_monotonic_s": started, "read_completed_at_monotonic_s": completed,
                    },
                    position={
                        "raw": decoded["present_position_raw"], "normalized": decoded["present_position_normalized"],
                        "read_started_at_monotonic_s": started, "read_completed_at_monotonic_s": completed,
                    },
                )
                before = self.visible_settings["before"]["registers"]
                fields = {
                    "goal_time_raw": "Goal_Time",
                    "goal_velocity_raw": "Goal_Velocity",
                    "acceleration_raw": "Acceleration",
                    "torque_limit_raw": "Torque_Limit",
                }
                if decoded["lock"] != 1 or any(decoded[k] != before[v] for k, v in fields.items()):
                    raise RuntimeError("Visible live selected settings changed from their read-only baseline")
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
        if primary is not None and getattr(robot, "_am1_visible_control_binding", None) is not None:
            self._retain_sparse(sparse)
            self._pending_sparse = copy.deepcopy(sparse)
            raise primary
        try:
            self._emit_sparse(sparse)
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
