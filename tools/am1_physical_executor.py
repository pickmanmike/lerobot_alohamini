"""Explicit protected physical recipes. No hardware is opened by module import."""

import copy
import hashlib
import json
import math
import threading
import time
from dataclasses import dataclass

from examples.alohamini.am1_finite_task import JOINT_KEYS
from examples.alohamini.am1_live_teleop import (
    TELEOP_ARM_KEYS,
    TELEOP_RECIPE,
    TELEOP_SPEC,
    validate_body,
    validate_source,
    validate_target,
    validate_workspace,
)
from examples.alohamini.am1_session_contract import (
    ELBOW_RECOVERY_DEFINITIONS,
    LEFT_ELBOW_ORIGINAL_RETURN,
    LEFT_ELBOW_POSITIVE_CONTROL,
    LEFT_ELBOW_RESPONSE,
    LIVE_ELBOW_SPECS,
    PRACTICAL_ELBOW_PRESETS,
    SelectedElbowExperiment,
)

FEEDBACK_MAX_AGE_S = 1.0  # Existing AM1 Local original request/acquisition contract.
ZERO_BODY = {"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0, "lift_axis.vel": 0}
LIVE_RECIPE = "physical-live-left-elbow"
LIVE_JOINT = "arm_left_elbow_flex.pos"


def load_elbow_return_reference(config, *, definition=LEFT_ELBOW_ORIGINAL_RETURN):
    """Read one exact private archived proof; it supplies no browser parameters."""
    import hashlib
    import os
    import stat
    from pathlib import Path

    from tools.am1_session_ipc import strict_json

    spec = config.get("elbow_return_reference")
    if not isinstance(spec, dict) or set(spec) != {"path", "sha256"}:
        raise ValueError("explicit private elbow return reference required")
    if type(spec["path"]) is not str or not Path(spec["path"]).is_absolute():
        raise ValueError("absolute private elbow return reference path required")
    if Path(spec["path"]).is_symlink():
        raise ValueError("private elbow return reference cannot be a symlink")
    descriptor = os.open(spec["path"], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_size > 16384
            or (os.name == "posix" and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077))
        ):
            raise ValueError("owned private small regular elbow return reference required")
        raw = stream.read(16385)
    if hashlib.sha256(raw).hexdigest() != spec["sha256"]:
        raise ValueError("elbow return reference content hash differs")
    value = definition.validate_artifact(strict_json(raw))
    if value["admission_motor_head"] != config.get("host_head"):
        raise ValueError("elbow return reference admission motor source differs")
    return copy.deepcopy(value)


class LiveInputExpiredBeforeAdmissionError(RuntimeError):
    """A valid whole frame expired before its original mailbox could admit it."""


class StartupCancelledError(RuntimeError):
    """An explicit Stop cancels admission without inventing a hardware fault."""


class FeedbackUnavailable(RuntimeError):  # noqa: N818 - existing adapter/test contract
    """A bounded missing/stale reply; owner survives and holds/recoveries remain bounded."""


def thermal_history_evidence(ack, now):
    """Validate the admitted owner's original thermal proof, never its receipt time."""
    if type(ack) is not dict:
        raise ValueError("native thermal acknowledgement malformed")
    if "thermal_history" not in ack:
        return None, True  # Legacy/nonthermal owners retain their existing contract.
    proof = ack["thermal_history"]
    fields = {"version", "raw_sample_at_monotonic_s", "peaks_ready",
              "deficit_started_at_monotonic_s", "deficit_ceiling_monotonic_s",
              "hold_sample_at_monotonic_s", "zero_read_started_at_monotonic_s",
              "zero_read_completed_at_monotonic_s"}
    if (type(proof) is not dict or set(proof) != fields or type(proof["version"]) is not int
            or proof["version"] != 1 or type(proof["peaks_ready"]) is not bool):
        raise ValueError("native thermal history proof malformed")
    raw = proof["raw_sample_at_monotonic_s"]
    if (type(raw) not in (int, float) or not math.isfinite(raw)
            or not 0 <= raw <= now or now - raw > .5):
        raise ValueError("native thermal original RAW feedback expired/invalid")
    started, ceiling = (proof[key] for key in
                         ("deficit_started_at_monotonic_s", "deficit_ceiling_monotonic_s"))
    hold, zero_start, zero_end = (proof[key] for key in
                                 ("hold_sample_at_monotonic_s", "zero_read_started_at_monotonic_s",
                                  "zero_read_completed_at_monotonic_s"))
    if started is None:
        if ceiling is not None or any(value is not None for value in (hold, zero_start, zero_end)) or not proof["peaks_ready"]:
            raise ValueError("native thermal history episode/proof missing")
        return copy.deepcopy(proof), True
    if (any(type(value) not in (int, float) or not math.isfinite(value) for value in (started, ceiling))
            or not 0 <= started <= now or not started < ceiling <= started + 10):
        raise ValueError("native thermal history original recovery clock invalid")
    if hold is None and zero_start is None and zero_end is None:
        return copy.deepcopy(proof), False
    if (any(type(value) not in (int, float) or not math.isfinite(value) for value in (hold, zero_start, zero_end))
            or not 0 <= hold <= zero_start <= zero_end <= now or zero_end - zero_start > .04
            or now - hold > .5):
        raise ValueError("native thermal actual zero/hold proof expired/invalid")
    return copy.deepcopy(proof), proof["peaks_ready"] and now < ceiling


@dataclass(frozen=True)
class PendingSample:
    """Validated cached feedback; not a new observation, receipt or ACK."""

    sample: dict


def _teleop_elbow_settings_summary(proof, acquired_at, *, run_id=None, host_incarnation=None):
    """Compact cached settings only; preserve the full proof in native evidence."""
    registers = ("ID", "Model_Number", "Firmware_Major_Version", "Firmware_Minor_Version",
                 "P_Coefficient", "I_Coefficient", "D_Coefficient", "Operating_Mode")
    row_keys = {"motor", "motor_id", "calibration", "original", "active",
                "original_read_timing", "active_read_timing"}
    if (not isinstance(proof, dict) or set(proof) not in ({"left", "right"}, {"left", "right", "trial"})
            or type(acquired_at) not in (int, float) or not math.isfinite(acquired_at) or acquired_at < 0):
        return None
    result = {"version": 1}
    try:
        for side in ("left", "right"):
            row = proof[side]
            if (not isinstance(row, dict) or set(row) != row_keys
                    or row["motor"] != "arm_" + side + "_elbow_flex"
                    or type(row["motor_id"]) is not int or row["motor_id"] != 3
                    or not isinstance(row["calibration"], list) or len(row["calibration"]) != 5
                    or any(type(v) is not int for v in row["calibration"])):
                return None
            summary = {key: copy.deepcopy(row[key]) for key in ("motor", "motor_id", "calibration")}
            for phase in ("original", "active"):
                values = row[phase]
                times = row[phase + "_read_timing"]
                if (not isinstance(values, dict) or not set(registers) <= set(values)
                        or any(type(values[key]) is not int for key in registers)
                        or not isinstance(times, dict) or set(times) != set(values)):
                    return None
                intervals = []
                for timing in times.values():
                    if not isinstance(timing, dict) or set(timing) != {
                            "read_started_at_monotonic_s", "read_completed_at_monotonic_s"}:
                        return None
                    start, end = (timing[key] for key in
                                  ("read_started_at_monotonic_s", "read_completed_at_monotonic_s"))
                    if (any(type(v) not in (int, float) or not math.isfinite(v) for v in (start, end))
                            or not 0 <= start <= end <= acquired_at):
                        return None
                    intervals.append((start, end))
                summary[phase] = [values[key] for key in registers]
                summary[phase + "_read_interval"] = [min(v[0] for v in intervals), max(v[1] for v in intervals)]
            if summary["original_read_interval"][1] > summary["active_read_interval"][0]:
                return None
            result[side] = summary
        if "trial" in proof:
            from uuid import UUID

            trial = proof["trial"]
            keys = {"version", "kind", "run_id", "host_incarnation", "request_sha256", "source_head",
                    "request_id", "workload_segment", "original_p", "configured_p", "restore_required",
                    "original_settings_sha256", "prepared_at_monotonic_s", "configured_at_monotonic_s"}
            if (not isinstance(trial, dict) or set(trial) != keys
                    or type(trial["version"]) is not int or trial["version"] != 1
                    or trial["restore_required"] is not True
                    or type(trial["original_p"]) is not int or trial["original_p"] != 16
                    or type(trial["configured_p"]) is not int or trial["configured_p"] not in (16, 32)
                    or trial["run_id"] != run_id or trial["host_incarnation"] != host_incarnation
                    or any(type(v) is not str or str(UUID(v)) != v for v in
                           (run_id, host_incarnation, trial["request_id"]))
                    or any(type(trial[key]) is not str or len(trial[key]) != width
                           or any(c not in "0123456789abcdef" for c in trial[key])
                           for key, width in (("request_sha256", 64), ("source_head", 40),
                                              ("original_settings_sha256", 64)))):
                return None
            side = {"left-response-p": "left", "right-response-p": "right"}.get(trial["kind"])
            if side is None or trial["workload_segment"] != side + "-peer-fixed":
                return None
            prepared, configured = (trial[key] for key in
                                    ("prepared_at_monotonic_s", "configured_at_monotonic_s"))
            if (any(type(v) not in (int, float) or not math.isfinite(v) for v in (prepared, configured))
                    or not 0 <= prepared <= configured <= result[side]["active_read_interval"][0] <= acquired_at):
                return None
            original = [3, 777, 3, 10, 16, 0, 32, 0]
            for name in ("left", "right"):
                active = list(original)
                if name == side:
                    active[4] = trial["configured_p"]
                calibration = [3, 0, 1845, 864, 3073] if name == "left" else [3, 0, -658, 827, 3039]
                if (result[name]["original"] != original or result[name]["active"] != active
                        or result[name]["calibration"] != calibration):
                    return None
            # All full receipt fields were validated above. The full-proof digest
            # binds them while the closed trial-only vectors keep the real ACK
            # within its existing cap without dropping arm/lift/thermal feedback.
            result = {"version": 2, **{name: [result[name]["calibration"], result[name]["original"],
                      result[name]["active"], result[name]["original_read_interval"]
                      + result[name]["active_read_interval"]] for name in ("left", "right")},
                      "trial": [trial[key] for key in ("kind", "request_sha256", "request_id", "configured_p")]}
        result["sha256"] = hashlib.sha256(
            json.dumps(proof, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    except (KeyError, TypeError, ValueError):
        return None
    return result


def read_physical_sample(
    client,
    run_id,
    host_incarnation,
    previous_sequence,
    *,
    clock=time.monotonic,
    allow_pending_sample=False,
):
    if type(allow_pending_sample) is not bool:
        raise ValueError("pending sample opt-in requires bool")
    observation = client.get_observation()
    now = clock()
    marker = client.latest_am1_local_feedback
    if allow_pending_sample and type(previous_sequence) is int and previous_sequence == -1 and marker is None:
        received_sequence = getattr(client, "observation_sequence", ...)
        raw_keys = getattr(client, "latest_raw_observation_keys", ...)
        if (type(received_sequence) is int and received_sequence == 0
                and getattr(client, "latest_observation_received_at", ...) is None
                and getattr(client, "latest_observation_roundtrip_age_s", ...) is None
                and getattr(client, "latest_observation_error", ...) is None
                and type(raw_keys) is frozenset and not raw_keys
                and type(observation) is dict and not observation):
            # Connect's availability handshake leaves this cache empty. No first
            # packet may supply feedback/ACK/Live credit; the existing worker
            # retries in hold under its original startup deadline and Stop fence.
            raise FeedbackUnavailable("initial native observation not yet received")
    if not isinstance(marker, dict) or marker.get("version") != 1:
        def diagnostic_scalar(value, *, integer_only=False):
            if type(value) is int and -(1 << 63) <= value < (1 << 63):
                return value
            if not integer_only and (value is None or type(value) is float and math.isfinite(value)):
                return value
            return {"available": value is not ..., "type": "missing" if value is ... else type(value).__name__[:48]}

        raw_keys = getattr(client, "latest_raw_observation_keys", ...)
        parse_error = getattr(client, "latest_observation_error", ...)
        context = {
            "observation_sequence": diagnostic_scalar(getattr(client, "observation_sequence", ...), integer_only=True),
            "received_at": diagnostic_scalar(getattr(client, "latest_observation_received_at", ...)),
            "request_age_s": diagnostic_scalar(getattr(client, "latest_observation_roundtrip_age_s", ...)),
            "parse_error": parse_error[:160] if type(parse_error) is str else diagnostic_scalar(parse_error),
            "raw_key_count": len(raw_keys) if type(raw_keys) is frozenset else diagnostic_scalar(raw_keys, integer_only=True),
            "marker_key_present": "_am1_local_feedback" in raw_keys if type(raw_keys) is frozenset else None,
            "returned_key_count": len(observation) if type(observation) is dict else diagnostic_scalar(observation, integer_only=True),
            "marker_type": type(marker).__name__[:48],
            "marker_version": diagnostic_scalar(marker.get("version", ...)) if type(marker) is dict else diagnostic_scalar(...),
        }
        raise ValueError("protected host feedback marker missing; context=" + json.dumps(context, sort_keys=True, separators=(",", ":")))
    if marker.get("run_id") != run_id or marker.get("host_incarnation") != host_incarnation:
        raise ValueError("foreign protected host feedback binding")
    sequence = marker.get("observation_id")
    pending = type(sequence) is int and sequence == previous_sequence and allow_pending_sample
    if (
        type(sequence) is not int
        or sequence < previous_sequence
        or sequence == previous_sequence
        and not pending
    ):
        raise FeedbackUnavailable("native feedback acquisition did not advance")
    started, completed = marker.get("read_started_at_monotonic_s"), marker.get("acquired_at_monotonic_s")
    received, request_age = client.latest_observation_received_at, client.latest_observation_roundtrip_age_s
    if any(
        type(v) not in (int, float) or not math.isfinite(v)
        for v in (started, completed, received, request_age)
    ):
        raise ValueError("native feedback original acquisition/request time unavailable")
    if not 0 <= started <= completed <= received <= now or request_age < 0:
        raise ValueError("native feedback acquisition/request clock invalid")
    # Both complete raw vector acquisition and original matching request must remain fresh.
    at = min(started, received - request_age)
    if now - at >= FEEDBACK_MAX_AGE_S:
        raise FeedbackUnavailable("native feedback original acquisition/request expired")
    if client.latest_observation_error:
        raise ValueError("malformed native observation: " + client.latest_observation_error)
    if not set(JOINT_KEYS).issubset(client.latest_raw_observation_keys):
        raise ValueError("incomplete actual arm feedback")
    positions = {key: observation.get(key) for key in JOINT_KEYS}
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in positions.values()):
        raise ValueError("invalid actual arm feedback")
    arm_feedback = marker.get("arm_feedback")
    if arm_feedback is not None:
        if (
            not isinstance(arm_feedback, dict)
            or set(arm_feedback) != {"version", "positions"}
            or type(arm_feedback["version"]) is not int
            or arm_feedback["version"] != 1
            or not isinstance(arm_feedback["positions"], dict)
            or set(arm_feedback["positions"]) != set(TELEOP_ARM_KEYS)
        ):
            raise ValueError("invalid twelve-joint original native feedback")
        for key, reading in arm_feedback["positions"].items():
            fields = {"raw", "normalized", "read_started_at_monotonic_s", "read_completed_at_monotonic_s"}
            if not isinstance(reading, dict) or set(reading) != fields or type(reading["raw"]) is not int:
                raise ValueError("invalid original native raw reading")
            raw_started, raw_completed, normalized = (
                reading["read_started_at_monotonic_s"], reading["read_completed_at_monotonic_s"], reading["normalized"]
            )
            if (
                any(type(v) not in (int, float) or not math.isfinite(v) for v in (raw_started, raw_completed, normalized))
                or not 0 <= raw_started <= raw_completed <= completed
                or normalized != positions[key]
            ):
                raise ValueError("original native raw reading differs from measured feedback")
            at = min(at, raw_started)
        if now - at >= FEEDBACK_MAX_AGE_S:
            raise FeedbackUnavailable("native raw feedback original acquisition expired")
    lift_feedback = marker.get("lift_feedback")
    if lift_feedback is not None:
        fields = {"version", "present_velocity_raw", "height_mm", "sample_monotonic_s", "request_duration_s"}
        if (
            not isinstance(lift_feedback, dict) or set(lift_feedback) != fields
            or type(lift_feedback["version"]) is not int or lift_feedback["version"] != 1
            or type(lift_feedback["present_velocity_raw"]) is not int
            or not {"lift_axis.vel", "lift_axis.height_mm"} <= client.latest_raw_observation_keys
        ):
            raise ValueError("original native lift feedback missing or malformed")
        height, sampled, duration = (lift_feedback[key] for key in
                                    ("height_mm", "sample_monotonic_s", "request_duration_s"))
        if (
            any(type(v) not in (int, float) or not math.isfinite(v) for v in (height, sampled, duration))
            or not 0 <= duration <= sampled <= completed
            or height != observation.get("lift_axis.height_mm")
        ):
            raise ValueError("original native lift feedback interval/value differs")
        # Keep the existing sample timestamp and transaction duration. The
        # conservative original start is never replaced by host/client receipt.
        at = min(at, sampled - duration)
        if now - at >= FEEDBACK_MAX_AGE_S:
            raise FeedbackUnavailable("native lift feedback original acquisition expired")
        observation = {**observation, "lift_axis.vel": lift_feedback["present_velocity_raw"],
                       "_am1_lift_feedback": copy.deepcopy(lift_feedback)}
    selected = marker.get("selected_joint")
    if selected is not None:
        if (
            not isinstance(selected, dict)
            or type(selected.get("version")) is not int
            or selected["version"] != 1
        ):
            raise ValueError("invalid selected native feedback")
        fields = {
            "run_id": run_id,
            "host_incarnation": host_incarnation,
            "epoch": marker.get("epoch"),
            "intent_revision": marker.get("intent_revision"),
            "action_sequence": marker.get("accepted_action_sequence"),
            "observation_id": sequence,
            "acquired_at_monotonic_s": completed,
        }
        if any(selected.get(k) != v or type(selected.get(k)) is not type(v) for k, v in fields.items()):
            raise ValueError("foreign selected native feedback binding")
    sample = {
        "positions": positions,
        "sequence": sequence,
        "at": at,
        "ack": copy.deepcopy({k: v for k, v in marker.items() if k != "selected_joint"}),
        "observation": {**observation, **({"_am1_arm_feedback": copy.deepcopy(arm_feedback)} if arm_feedback is not None else {})},
        "body_feedback": {
            key: observation[key]
            for key in ("x.vel", "y.vel", "theta.vel", "lift_axis.vel", "lift_axis.height_mm")
            if key in client.latest_raw_observation_keys
            and type(observation.get(key)) in (int, float)
            and math.isfinite(observation[key])
        },
        "timing": {
            "read_started_at_monotonic_s": started,
            "acquired_at_monotonic_s": completed,
            "original_request_sent_at_monotonic_s": received - request_age,
            "reply_received_at_monotonic_s": received,
            "original_request_roundtrip_age_s": request_age,
            **({"lift_feedback": copy.deepcopy(lift_feedback)} if lift_feedback is not None else {}),
        },
        "provenance": "protected-host/measured-feedback",
        **({"selected_joint": copy.deepcopy(selected)} if selected is not None else {}),
        **({"arm_feedback": copy.deepcopy(arm_feedback)} if arm_feedback is not None else {}),
        **({"lift_feedback": copy.deepcopy(lift_feedback)} if lift_feedback is not None else {}),
    }
    # The source marker was correlated above. Forward only its compact cached
    # proof: full settings/Goal payloads must not displace the essential ACK.
    if isinstance(selected, dict) and "teleop_elbow_settings" in selected:
        compact = _teleop_elbow_settings_summary(selected["teleop_elbow_settings"], completed,
                                                run_id=run_id, host_incarnation=host_incarnation)
        if compact is not None:
            candidate = {**sample["ack"], "teleop_elbow_settings": compact}
            # Match the existing Core projection after removing duplicated live
            # workspace data. Missing proof refuses the peer run, never freshness.
            presented = {key: value for key, value in candidate.items() if key != "live_input"}
            if len(json.dumps(presented, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()) <= 4096:
                sample["ack"] = candidate
    return PendingSample(sample) if pending else sample


def body_recipe_action(client, elapsed):
    # Original W/A/U/J 200 ms down / 500 ms released sequence, admitted finite native input.
    keys = []
    for start, key in ((2.0, "w"), (2.7, "a"), (3.4, "u"), (4.1, "j")):
        if start <= elapsed < start + 0.2 - 1e-9:
            keys = [key]
            break
    lift = client._from_keyboard_to_lift_action(keys)
    # This recipe's original no-U/J mapping redundantly requests the current
    # height alongside zero velocity. Send the stop alone; the protected lift
    # operation keeps its stopped uncertainty and refuses genuine height motion.
    if keys in ([], ["w"], ["a"]) and lift.get("lift_axis.vel") == 0:
        lift.pop("lift_axis.height_mm", None)
    return {**client._from_keyboard_to_base_action(keys), **lift}


class PhysicalExecutor:
    source = "protected-physical-provider"
    asynchronous = True
    restart_cleanup = "physical_cleanup_unknown_after_restart"
    selected_joint_recipes = frozenset(
        {
            "physical-left-elbow-compare",
            "physical-left-elbow-dwell-compare",
            LEFT_ELBOW_RESPONSE.recipe_name,
            LEFT_ELBOW_POSITIVE_CONTROL.recipe_name,
            SelectedElbowExperiment.recipe_name,
            LEFT_ELBOW_ORIGINAL_RETURN.recipe_name,
            *ELBOW_RECOVERY_DEFINITIONS,
        }
    )
    recipes = (
        frozenset({"physical-arm-smoke", "physical-arm-smoke-repeat", "physical-arm-hold-body"})
        | selected_joint_recipes
    )

    def __init__(self, config, observation, *, clock=time.monotonic, io_factory=None):
        from tools.am1_local_observation import LocalObservationMailbox

        if not isinstance(config, dict) or not config or not isinstance(observation, LocalObservationMailbox):
            raise ValueError(
                "physical backend requires explicit private config and actual local-camera adapter"
            )
        self.config = copy.deepcopy(config)
        self.return_reference = None
        self.return_reference_error = None
        self.recovery_reference = None
        self.recovery_prerequisite = None
        if "elbow_return_reference" in config:
            try:
                self.return_reference = load_elbow_return_reference(config)
            except (ValueError, OSError) as error:
                self.return_reference_error = str(error)
            try:
                self.recovery_reference = load_elbow_return_reference(
                    config,
                    definition=next(iter(ELBOW_RECOVERY_DEFINITIONS.values())),
                )
            except (ValueError, OSError) as error:
                if self.return_reference is None:
                    self.return_reference_error = str(error)
        self.observation = observation
        self.observation.require_owned_session = True
        self.clock = clock
        self.io_factory = io_factory
        self.mutex = threading.RLock()
        self.worker = None
        self.run_id = self.recipe = None
        self.live_spec = None
        self.host_incarnation = None
        self.revision = 0
        self.desired = "hold"
        self.finish_status = None
        self.sample = None
        self.task = None
        self._fault = None
        self._transient = None
        self._epoch = -1
        self._epoch_mode = None
        self._epoch_revision = -1
        self._action_sequence = 0
        self._aligned = []
        self._hold_reference = None
        self._dispatch_ack = False
        self._execution = {"progress_s": 0.0, "complete": False, "fault": None}
        self._finite_completion = None
        self._completion_returns = []
        self.positive_prerequisite = None
        self.selected_experiment = None
        self._lifecycle = {
            "run_id": None,
            "phase": "idle",
            "startup_deadline": None,
            "native_live_at": None,
            "cleanup": None,
            "uncertain": False,
            "terminal_status": None,
        }
        self._wake = threading.Event()
        self._reconciliation_worker = None
        self._live_binding = None
        self._live_target = None
        self._live_last_seq = -1
        self._live_admission_requested = False
        self._live_expired = None
        self._teleop_source = None
        self._reset_input_transport_trace()

    def _reset_input_transport_trace(self):
        self._input_transport_trace = {
            "version": 1, "clock": "pi-monotonic",
            "provenance": "protected-executor/mailbox-and-send",
            "recent_limit": 32, "dropped_rows": 0, "invalid_rows": 0,
            "recent": [], "first_native_expiry": None, "first_native_expiry_seen": False,
        }

    def _input_transport_row(self, event, marker):
        """Copy only bounded diagnostic primitives, never targets or PC clocks."""
        if self.live_spec is not TELEOP_SPEC:
            return None
        try:
            binding = marker.get("live_binding") or {}
            row = {key: marker[key] for key in ("run_id", "host_incarnation")}
            row.update({key: binding[key] for key in
                        ("service_incarnation", "controller_generation", "connection_generation")})
            if any(type(value) is not str or not 0 < len(value) <= 64 for value in row.values()):
                raise ValueError("unbounded diagnostic identity")
            for key, minimum in (("intent_revision", 0), ("epoch", -1)):
                value = marker[key]
                if type(value) is not int or not minimum <= value < 2**63:
                    raise ValueError("invalid diagnostic revision/epoch")
                row[key] = value
            if type(marker["mode"]) is not str or marker["mode"] not in ("active", "pause"):
                raise ValueError("invalid diagnostic mode")
            row["mode"] = marker["mode"]
            action_sequence = marker.get("action_sequence")
            if event == "worker_send" and (type(action_sequence) is not int or not 0 <= action_sequence < 2**63):
                raise ValueError("invalid diagnostic action sequence")
            row["action_sequence"] = action_sequence if event == "worker_send" else None
            original = marker.get("live_input")
            if original is not None:
                source = original["source"]
                item = {"source_id": source["source_id"], "source_kind": source["kind"],
                        "sample_seq": source["sample_seq"], "seq": original["seq"],
                        "grant_id": original["grant_id"],
                        "valid_until_monotonic_s": original["valid_until_monotonic_s"]}
                if (type(item["source_id"]) is not str or not 0 < len(item["source_id"]) <= 64
                        or type(item["grant_id"]) is not str or not 0 < len(item["grant_id"]) <= 64
                        or type(item["source_kind"]) is not str
                        or item["source_kind"] not in ("usb-leaders", "virtual-leaders")
                        or any(type(item[key]) is not int or not 0 <= item[key] < 2**63
                               for key in ("sample_seq", "seq"))
                        or type(item["valid_until_monotonic_s"]) not in (int, float)
                        or not math.isfinite(item["valid_until_monotonic_s"])
                        or item["valid_until_monotonic_s"] < 0):
                    raise ValueError("invalid diagnostic original input")
                row["input"] = item
            at = self.clock()
            if type(at) not in (int, float) or not math.isfinite(at) or at < 0:
                raise ValueError("invalid diagnostic Pi clock")
            row["event"] = event
            row["send_started_at_monotonic_s" if event == "worker_send" else "admitted_at_monotonic_s"] = at
            return row
        except Exception:
            self._input_transport_trace["invalid_rows"] = min(2**63 - 1, self._input_transport_trace["invalid_rows"] + 1)
            return None  # Diagnostic failure never changes admission, expiry or the original send exception.

    def _append_input_transport(self, row, *, outcome=None, error_type=None):
        if row is None:
            return
        try:
            if row["event"] == "worker_send":
                at = self.clock()
                if (type(at) not in (int, float) or not math.isfinite(at)
                        or at < row["send_started_at_monotonic_s"]
                        or outcome not in ("returned", "raised")
                        or error_type is not None and (type(error_type) is not str or len(error_type) > 64)):
                    raise ValueError("invalid diagnostic send completion")
                row.update(send_completed_at_monotonic_s=at, outcome=outcome, error_type=error_type)
            trace = self._input_transport_trace
            trace["recent"].append(row)
            if len(trace["recent"]) > trace["recent_limit"]:
                del trace["recent"][0]
                trace["dropped_rows"] = min(2**63 - 1, trace["dropped_rows"] + 1)
        except Exception:
            self._input_transport_trace["invalid_rows"] = min(2**63 - 1, self._input_transport_trace["invalid_rows"] + 1)

    def _freeze_input_transport_expiry(self, sample):
        trace = self._input_transport_trace
        if self.live_spec is not TELEOP_SPEC or trace["first_native_expiry_seen"]:
            return
        trace["first_native_expiry_seen"] = True
        try:
            ack, expired = sample["ack"], sample["ack"]["live_input"]
            first = {key: ack[key] for key in ("run_id", "host_incarnation", "intent_revision", "epoch")}
            first.update(feedback_sequence=sample["sequence"], last_input_seq=expired["last_input_seq"],
                         valid_until_monotonic_s=expired["valid_until_monotonic_s"],
                         expiry_detected_at_monotonic_s=expired["expiry_detected_at_monotonic_s"],
                         observed_at_monotonic_s=self.clock())
            if (any(type(first[key]) is not str or not 0 < len(first[key]) <= 64
                    for key in ("run_id", "host_incarnation"))
                    or any(type(first[key]) is not int or not 0 <= first[key] < 2**63
                           for key in ("intent_revision", "epoch", "feedback_sequence", "last_input_seq"))
                    or any(type(first[key]) not in (int, float) or not math.isfinite(first[key]) or first[key] < 0
                           for key in ("valid_until_monotonic_s", "expiry_detected_at_monotonic_s", "observed_at_monotonic_s"))):
                raise ValueError("invalid diagnostic expiry")
            first["rows"] = copy.deepcopy(trace["recent"])
            trace["first_native_expiry"] = first
        except Exception:
            trace["invalid_rows"] = min(2**63 - 1, trace["invalid_rows"] + 1)

    def supports(self, recipe):
        if recipe.name == TELEOP_RECIPE:
            return recipe.seed is None and recipe.mode == "interactive" and recipe.trajectory_s is None and recipe.live_s == 300
        return (
            (recipe.name in self.recipes or recipe.name in LIVE_ELBOW_SPECS)
            and recipe.seed is None
            and recipe.mode == ("interactive" if recipe.name in LIVE_ELBOW_SPECS else "finite")
            and (recipe.name not in LIVE_ELBOW_SPECS or recipe.trajectory_s is None
                 and recipe.live_s == LIVE_ELBOW_SPECS[recipe.name].live_s)
            and (recipe.name != LEFT_ELBOW_ORIGINAL_RETURN.recipe_name or self.return_reference is not None)
            and (recipe.name not in ELBOW_RECOVERY_DEFINITIONS or self.recovery_reference is not None)
        )

    def set_recovery_prerequisite(self, proof):
        """The native authority supplies its retained prior run, never browser parameters."""
        with self.mutex:
            if self.worker and self.worker.is_alive():
                raise RuntimeError("Recovery prerequisite cannot change during an active owner")
            self.recovery_prerequisite = copy.deepcopy(proof)

    def set_positive_prerequisite(self, proof):
        with self.mutex:
            if self.worker and self.worker.is_alive():
                raise RuntimeError("Positive prerequisite cannot change during an active owner")
            self.positive_prerequisite = copy.deepcopy(proof)

    def set_selected_experiment(self, definition):
        """Freeze only an authority-validated descriptor before native admission."""
        with self.mutex:
            if self.worker and self.worker.is_alive():
                raise RuntimeError("Selected experiment cannot change during an active owner")
            if definition is not None and not isinstance(definition, SelectedElbowExperiment):
                raise ValueError("Validated selected experiment definition required")
            self.selected_experiment = (
                SelectedElbowExperiment.from_payload(definition.payload) if definition is not None else None
            )

    def _live_ceiling(self):
        return (
            self.selected_experiment.live_s
            if self.recipe.name == SelectedElbowExperiment.recipe_name and self.selected_experiment is not None
            else self.recipe.live_s
        )

    def _reference_for_recipe(self):
        if self.recipe and self.recipe.name in {LEFT_ELBOW_POSITIVE_CONTROL.recipe_name, SelectedElbowExperiment.recipe_name}:
            return None
        return (
            self.recovery_reference
            if self.recipe and self.recipe.name in ELBOW_RECOVERY_DEFINITIONS
            else self.return_reference
        )

    def sensing_metadata(self):
        return {
            "sensing_policy": "local-camera-required",
            "sensing_source": dict(self.observation.source),
            "observation_provenance": "real-local-camera/pi-decoded-arrival",
            "observation_freshness_basis": "pi-upstream-arrival-monotonic",
            "motor_commands_provenance": "protected-host/requested-and-accepted-separately",
            "feedback_provenance": "protected-host/measured-feedback",
            "ack_provenance": "protected-host/action-epoch-feedback-not-servo-ack",
            "backend_contract": copy.deepcopy(self.config),
            "retained_return_reference": copy.deepcopy(
                self._reference_for_recipe() or self.recovery_reference
            ),
            "recovery_prerequisite": copy.deepcopy(self.recovery_prerequisite),
            "positive_prerequisite": copy.deepcopy(self.positive_prerequisite),
            "return_reference_error": self.return_reference_error,
            "gain_recipes": {
                "physical-arm-smoke": "selected-shoulder-i1-with-verified-i0-restore",
                "physical-arm-smoke-repeat": "selected-shoulder-i1-with-verified-i0-restore",
                "physical-arm-hold-body": "ordinary-body-baseline-no-integral-trial",
                "physical-left-elbow-compare": "ordinary-baseline-selected-elbow-evidence-no-gain-trial",
                "physical-left-elbow-dwell-compare": "ordinary-baseline-selected-elbow-evidence-no-gain-trial",
                LEFT_ELBOW_RESPONSE.recipe_name: "ordinary-baseline-signed-elbow-response-no-gain-trial",
                LEFT_ELBOW_ORIGINAL_RETURN.recipe_name: "ordinary-configuration-original-reference-return-no-gain-trial",
            },
        }

    def can_start_after_fault(self, run, *, observed_fault):
        """Allow a fresh startup after this exact resident fault's verified cleanup.

        This checks cached terminal evidence only. The current executor fault is
        matched under its mutex; owner and native first causes remain separate.
        It neither clears them nor admits effects without new-run feedback.
        """
        with self.mutex:
            lifecycle = self._lifecycle
            if not (
                isinstance(run, dict)
                and run.get("status") == "faulted"
                and run.get("uncertain") is False
                and run.get("cleanup") == "physical_readback_verified"
                and run.get("run_id") == self.run_id
                and isinstance(self._fault, str)
                and self._fault
                and type(observed_fault) is str
                and observed_fault == self._fault
                and type(run.get("first_cause")) is str
                and bool(run["first_cause"])
                and lifecycle.get("run_id") == self.run_id
                and lifecycle.get("backend_incarnation") == self.host_incarnation
                and run.get("backend_incarnation", self.host_incarnation) == self.host_incarnation
                and lifecycle.get("phase") == "terminal"
                and lifecycle.get("terminal_status") == "faulted"
                and lifecycle.get("uncertain") is False
                and lifecycle.get("cleanup") == "physical_readback_verified"
                and lifecycle.get("cleanup_errors") == []
                and run.get("physical_result") == lifecycle
                and not (self.worker and self.worker.is_alive())
                and not (self._reconciliation_worker and self._reconciliation_worker.thread.is_alive())
            ):
                return False
            try:
                from examples.alohamini.am1_session_contract import uuid_text

                if (
                    uuid_text(self.run_id) != self.run_id
                    or uuid_text(self.host_incarnation) != self.host_incarnation
                ):
                    return False
                receipt = lifecycle.get("readback")
                readback = receipt.get("readback") if isinstance(receipt, dict) else None
                camera = lifecycle.get("camera_release")
                native_cause = lifecycle.get("first_cause")
                if not (
                    "native_first_cause" in run
                    and "first_cause" in lifecycle
                    and isinstance(receipt, dict)
                    and "first_cause" in receipt
                    and (native_cause is None or type(native_cause) is str and bool(native_cause))
                    and run["native_first_cause"] == native_cause == receipt["first_cause"]
                    and isinstance(readback, dict)
                    and type(receipt.get("version")) is int
                    and readback.get("verified") is True
                    and readback.get("buses_closed") is True
                    and readback.get("cleanup_errors") == []
                    and readback.get("selected_gain_restore_required") is False
                    and qualified_cleanup_receipt(receipt, self.run_id, self.host_incarnation)
                    and isinstance(camera, dict)
                    and type(camera.get("version")) is int
                    and camera["version"] == 1
                    and camera.get("session_id") == self.run_id
                    and camera.get("state") == "released"
                    and camera.get("errors") == []
                    and camera.get("runtime_failed") is False
                ):
                    return False
                release = camera.get("release_evidence")
                return bool(
                    isinstance(release, dict)
                    and type(release.get("backend_returncode")) is int
                    and release["backend_returncode"] == 0
                    and all(
                        release.get(k) is True
                        for k in (
                            "backend_exited",
                            "readers_joined",
                            "viewer_socket_closed",
                            "runtime_config_removed",
                        )
                    )
                )
            except (AttributeError, KeyError, TypeError, ValueError):
                return False  # Malformed cached cleanup never becomes permission to start.

    def bind_run(self, run_id, recipe):
        with self.mutex:
            if recipe.name == SelectedElbowExperiment.recipe_name and self.selected_experiment is None:
                raise RuntimeError("Selected experiment definition required before binding")
            if (
                not self.supports(recipe)
                or (self.worker and self.worker.is_alive())
                or (self._reconciliation_worker and self._reconciliation_worker.thread.is_alive())
                or self._lifecycle["uncertain"]
            ):
                raise RuntimeError("previous physical owner is active or uncertain, or recipe unsupported")
            self.run_id, self.recipe = run_id, recipe
            self.live_spec = TELEOP_SPEC if recipe.name == TELEOP_RECIPE else LIVE_ELBOW_SPECS.get(recipe.name)
            self._teleop_source = None
            self._reconciliation_worker = None
            self.host_incarnation = __import__("uuid").uuid4().__str__()
            self.revision = 0
            self.desired = "hold"
            self.finish_status = None
            self.sample = self.task = None
            self._live_binding = None
            self._live_target = None
            self._live_last_seq = -1
            self._live_admission_requested = False
            self._live_expired = None
            self._fault = self._transient = None
            self._epoch = -1
            self._epoch_mode = None
            self._epoch_revision = -1
            self._action_sequence = 0
            self._aligned = []
            self._hold_reference = None
            self._dispatch_ack = False
            self._execution = {"progress_s": 0.0, "complete": False, "fault": None}
            self._reset_input_transport_trace()
            self._finite_completion = None
            self._completion_returns = []
            self._lifecycle = {
                "run_id": run_id,
                "phase": "startup",
                "startup_deadline": self.clock() + 180,
                "native_live_at": None,
                "cleanup": None,
                "uncertain": False,
                "terminal_status": None,
            }
            self.observation.begin(run_id, self._live_ceiling())

    def begin(self, recipe):
        with self.mutex:
            if self.recipe != recipe or self.worker and self.worker.is_alive():
                return False
            self.worker = threading.Thread(target=self._run, name="am1-protected-native", daemon=False)
            self.worker.start()
            return True  # Mailbox accepted only; authority does not mark physical effect admitted.

    def set_intent_revision(self, revision):
        with self.mutex:
            if type(revision) is not int or revision < self.revision:
                raise ValueError("physical intent revision must not regress")
            if revision != self.revision:
                self._live_target = None
                self._live_expired = None
                self.revision = revision
                self._finite_completion = None
                self._completion_returns = []
                self.desired = "hold"
                self._dispatch_ack = False
                self._aligned = []
                self._hold_reference = None
        self._wake.set()

    def bind_control(self, *, run_id, service_incarnation, controller_generation, connection_generation,
                     host_incarnation, intent_revision):
        with self.mutex:
            if run_id != self.run_id or host_incarnation != self.host_incarnation:
                return False
            if (type(service_incarnation) is not str or not service_incarnation
                    or any(type(v) is not str or not v for v in (controller_generation, connection_generation))
                    or type(intent_revision) is not int or intent_revision < 0
                    or intent_revision < self.revision):
                return False
            owner = {"service_incarnation": service_incarnation, "controller_generation": controller_generation,
                     "connection_generation": connection_generation}
            if owner != self._live_binding or intent_revision != self.revision:
                self._live_expired = None
                if owner != self._live_binding:
                    self._live_last_seq = -1
                self._live_binding = owner
                self._live_target = None
                self.revision = intent_revision
                self.desired = "hold"
                self._dispatch_ack = False
                self._aligned = []
                self._hold_reference = None
            self._wake.set()
            return True

    def apply(self, target, *, valid_until_monotonic_s, intent_revision, run_id, service_incarnation,
              controller_generation, connection_generation, host_incarnation, grant_id, seq, body=None, source=None,
              classify_expiry=False):
        with self.mutex:
            if self.recipe is not None and self.recipe.name == TELEOP_RECIPE:
                return self._apply_teleop(target, body=body, source=source,
                    valid_until_monotonic_s=valid_until_monotonic_s, intent_revision=intent_revision,
                    run_id=run_id, service_incarnation=service_incarnation, controller_generation=controller_generation,
                    connection_generation=connection_generation, host_incarnation=host_incarnation,
                    grant_id=grant_id, seq=seq, classify_expiry=classify_expiry)
            joint = self.live_spec.joint if self.live_spec is not None else None
            if (self.recipe is None or self.live_spec is None or self.finish_status
                    or self._lifecycle["phase"] != "live" or self.desired != "active"
                    or run_id != self.run_id or host_incarnation != self.host_incarnation
                    or intent_revision != self.revision or self._live_binding != {
                        "service_incarnation": service_incarnation, "controller_generation": controller_generation,
                        "connection_generation": connection_generation}
                    or not isinstance(target, dict) or set(target) != {joint}
                    or type(target[joint]) not in (int, float) or not math.isfinite(target[joint])
                    or type(valid_until_monotonic_s) not in (int, float) or not math.isfinite(valid_until_monotonic_s)
                    or self.clock() >= valid_until_monotonic_s
                    or valid_until_monotonic_s > self.clock() + .25 + 1e-9
                    or type(seq) is not int or seq <= self._live_last_seq
                    or type(grant_id) is not str or not grant_id):
                return False
            corridor = (self.sample or {}).get("ack", {}).get("live_input", {})
            if (not corridor or corridor.get("joint") != joint
                    or not corridor.get("corridor_min_norm", float("inf")) <= target[joint]
                    <= corridor.get("corridor_max_norm", float("-inf"))):
                return False
            self._live_target = {"target": copy.deepcopy(target), "grant_id": grant_id, "seq": seq,
                                 "valid_until_monotonic_s": valid_until_monotonic_s}
            self._live_last_seq = seq
            self._wake.set()
            return True

    def _apply_teleop(self, target, *, body, source, valid_until_monotonic_s, intent_revision,
                      run_id, service_incarnation, controller_generation, connection_generation,
                      host_incarnation, grant_id, seq, classify_expiry=False):
        if (self.finish_status or self._fault is not None
            or self._lifecycle["phase"] != "live" or self.desired != "active"
            or run_id != self.run_id or host_incarnation != self.host_incarnation or intent_revision != self.revision
            or self._live_binding != {"service_incarnation": service_incarnation,
                "controller_generation": controller_generation, "connection_generation": connection_generation}
            or type(seq) is not int or seq <= self._live_last_seq
            or type(valid_until_monotonic_s) not in (int, float) or not math.isfinite(valid_until_monotonic_s)):
            return False
        try:
            _thermal, thermal_ready = self._thermal_qualification((self.sample or {}).get("ack", {}), self.clock())
        except ValueError:
            return False
        if not thermal_ready or (_thermal is not None and _thermal["deficit_started_at_monotonic_s"] is not None):
            return False  # Only a new actual active ACK can close the held history episode.
        checked_at = self.clock()
        if (type(checked_at) not in (int, float) or not math.isfinite(checked_at)
                or valid_until_monotonic_s > checked_at + .25 + 1e-9):
            return False
        try:
            workspace = validate_workspace((self.sample or {}).get("ack", {}).get("live_input", {}).get("workspace"))
            target = validate_target(target, workspace=workspace)
            body = validate_body(body)
            source = validate_source(source, grant_id=grant_id, previous=self._teleop_source)
        except (KeyError, TypeError, ValueError):
            return False
        # Core's earlier grant check can precede evidence work or this mutex.
        # Only a fully valid frame gets this exact nonadmission classification.
        now = self.clock()
        if type(now) not in (int, float) or not math.isfinite(now) or now < checked_at:
            return False
        if now >= valid_until_monotonic_s:
            if classify_expiry is True:
                raise LiveInputExpiredBeforeAdmissionError("original input grant expired before mailbox admission")
            return False
        self._live_target = {"target": target, "body": body, "source": source, "grant_id": grant_id, "seq": seq,
                             "valid_until_monotonic_s": valid_until_monotonic_s}
        self._teleop_source = copy.deepcopy(source)
        self._live_last_seq = seq
        self._append_input_transport(self._input_transport_row("mailbox_admitted", {
            "run_id": self.run_id, "host_incarnation": self.host_incarnation,
            "intent_revision": self.revision, "epoch": self._epoch, "mode": "active",
            "live_binding": self._live_binding, "live_input": self._live_target,
        }))
        self._wake.set()
        return True

    def _thermal_qualification(self, ack, now):
        identity = (self.run_id, self.host_incarnation)
        if "thermal_history" in ack:
            self._thermal_history_proof_identity = identity
        elif getattr(self, "_thermal_history_proof_identity", None) == identity:
            raise ValueError("native thermal history proof disappeared")
        proof, ready = thermal_history_evidence(ack, now)
        if proof is not None and proof["deficit_started_at_monotonic_s"] is not None:
            live = self._lifecycle.get("native_live_at")
            started, ceiling = (proof[key] for key in
                                ("deficit_started_at_monotonic_s", "deficit_ceiling_monotonic_s"))
            if (type(live) not in (int, float) or not math.isfinite(live) or started < live
                    or ceiling != min(started + 10, live + self._live_ceiling())):
                raise ValueError("native thermal history original Live/recovery clock changed")
        return proof, ready

    def _update_polled_sample(self, polled, transient):
        pending = isinstance(polled, PendingSample)
        if pending:

            def original_payload(value):
                # The client ndarray is derived from the separately validated
                # positions. Every original safety value/identity/time remains.
                return json.dumps(
                    {k: v for k, v in value.items() if k != "observation"},
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )

            try:
                unchanged = (
                    self.live_spec is TELEOP_SPEC
                    and self.sample is not None
                    and transient is None
                    and original_payload(polled.sample) == original_payload(self.sample)
                )
            except (AttributeError, TypeError, ValueError):
                unchanged = False
            if not unchanged:
                raise ValueError("pending native feedback payload changed or is not admitted")
            # Preserve the accepted sample and any earlier real transport fault.
            # Only advancing acquisition can clear that fault or add proof.
        else:
            self._transient = transient
            if polled is not None:
                self.sample = polled
        advanced = not pending and polled is not None
        thermal_ready = True
        if self.sample is not None and self.live_spec is TELEOP_SPEC:
            _thermal, thermal_ready = self._thermal_qualification(self.sample["ack"], self.clock())
            if not thermal_ready:
                self._live_target = None  # A later qualified Resume must never replay this target.
        usable = bool(
            thermal_ready and (pending or advanced)
            and self.sample is not None
            and 0 <= self.clock() - self.sample["at"] < FEEDBACK_MAX_AGE_S
            and self._transient is None
            and self._fault is None
        )
        return advanced, usable

    def _retain_live_expiry(self, *, poll_control, previous_sequence, usable):
        """Keep current expiry until Core consumes it; fence a superseded poll."""
        sample = self.sample or {}
        ack = sample.get("ack", {})
        current_control = (
            self.revision, self._epoch, tuple(sorted((self._live_binding or {}).items()))
        )
        if (
            usable
            and poll_control == current_control
            and ack.get("run_id") == self.run_id
            and ack.get("host_incarnation") == self.host_incarnation
            and type(ack.get("intent_revision")) is int
            and ack["intent_revision"] == self.revision
            and type(ack.get("epoch")) is int
            and ack["epoch"] == self._epoch
            and type(sample.get("sequence")) is int
            and sample["sequence"] > previous_sequence
            and ack.get("live_input", {}).get("state") == "expired"
        ):
            self._live_expired = copy.deepcopy(ack["live_input"])
            self._freeze_input_transport_expiry(sample)

    def evidence(self):
        with self.mutex:
            now = self.clock()
            s = copy.deepcopy(self.sample)
            age = now - s["at"] if s else None
            fresh = (
                s is not None
                and 0 <= age < FEEDBACK_MAX_AGE_S
                and self._transient is None
                and self._fault is None
            )
            ack = s["ack"] if s else {}
            thermal, thermal_fault, terminal_clean = None, None, False
            if self.live_spec is TELEOP_SPEC:
                if self._lifecycle.get("phase") == "terminal":
                    try:
                        from examples.alohamini.am1_session_contract import uuid_text

                        lifecycle = self._lifecycle
                        receipt = lifecycle.get("readback")
                        readback = receipt.get("readback") if isinstance(receipt, dict) else {}
                        camera = lifecycle.get("camera_release") or {}
                        release = camera.get("release_evidence") or {}
                        terminal_clean = bool(
                            uuid_text(self.run_id) == self.run_id
                            and uuid_text(self.host_incarnation) == self.host_incarnation
                            and lifecycle.get("run_id") == self.run_id
                            and lifecycle.get("backend_incarnation") == self.host_incarnation
                            and ack.get("run_id") == self.run_id
                            and ack.get("host_incarnation") == self.host_incarnation
                            and type(lifecycle.get("terminal_status")) is str
                            and lifecycle["terminal_status"] in {"stopped", "completed", "faulted", "interrupted"}
                            and lifecycle.get("uncertain") is False
                            and lifecycle.get("cleanup") == "physical_readback_verified"
                            and lifecycle.get("cleanup_errors") == []
                            and not (self.worker and self.worker.is_alive())
                            and not (self._reconciliation_worker and self._reconciliation_worker.thread.is_alive())
                            and isinstance(receipt, dict) and type(receipt.get("version")) is int
                            and "first_cause" in lifecycle and "first_cause" in receipt
                            and (lifecycle["first_cause"] is None
                                 or type(lifecycle["first_cause"]) is str and bool(lifecycle["first_cause"]))
                            and receipt["first_cause"] == lifecycle["first_cause"]
                            and isinstance(readback, dict) and readback.get("verified") is True
                            and readback.get("buses_closed") is True and readback.get("cleanup_errors") == []
                            and readback.get("selected_gain_restore_required") is False
                            and qualified_cleanup_receipt(receipt, self.run_id, self.host_incarnation)
                            and type(camera.get("version")) is int and camera["version"] == 1
                            and camera.get("session_id") == self.run_id and camera.get("state") == "released"
                            and camera.get("errors") == [] and camera.get("runtime_failed") is False
                            and type(release.get("backend_returncode")) is int and release["backend_returncode"] == 0
                            and all(release.get(k) is True for k in
                                    ("backend_exited", "readers_joined", "viewer_socket_closed", "runtime_config_removed"))
                        )
                    except (AttributeError, KeyError, TypeError, ValueError):
                        terminal_clean = False  # Incomplete or foreign cleanup retains live proof validation.
                if terminal_clean:
                    # The closed owner's ACK stays diagnostic, never fresh
                    # permission or a new live thermal fault. Resident faults remain.
                    fresh = False
                else:
                    try:
                        thermal, thermal_ready = self._thermal_qualification(ack, now)
                        fresh = fresh and thermal_ready
                    except ValueError as error:
                        fresh, thermal_fault = False, str(error)
            matched = (
                fresh
                and ack.get("run_id") == self.run_id
                and ack.get("host_incarnation") == self.host_incarnation
            )
            revision = matched and ack.get("intent_revision") == self.revision
            hold = (
                revision
                and ack.get("state") == "paused"
                and ack.get("epoch") == self._epoch
                and self._epoch_mode == "pause"
            )
            active_ack = bool(
                revision
                and ack.get("state") == "active"
                and ack.get("epoch") == self._epoch
                and self._epoch_mode == "active"
            )
            live_transition = bool(self.live_spec is not None
                and matched and revision and self.desired == "active" and ack.get("state") == "paused"
                and self._epoch_mode == "active" and ack.get("epoch") == self._epoch - 1
                and len(self._aligned) >= 3 and self._aligned[-1] - self._aligned[0] >= .2 - 1e-9)
            aligned = bool(
                hold and len(self._aligned) >= 3 and self._aligned[-1] - self._aligned[0] >= 0.2 - 1e-9
            )
            o = self.observation.evidence()
            return {
                "source": self.source,
                "feedback": bool(fresh),
                "required_observation": o["qualified"] and not terminal_clean,
                "optional_quality": False,
                "optional_observation": {"required": False, "state": "not-requested", "qualified": False},
                "pose_aligned": aligned or bool(self._dispatch_ack and active_ack),
                "hold_acknowledged": bool(hold),
                "native_ack": bool(hold or active_ack or live_transition),
                "active_acknowledged": bool(active_ack),
                "finite_completion": copy.deepcopy(self._finite_completion),
                "fault": self._fault or thermal_fault,
                **({"thermal_history": thermal} if thermal is not None else {}),
                "feedback_age_s": age,
                "observation_age_s": o["age_s"],
                "observation": o,
                "feedback_sequence": s["sequence"] if s else None,
                "measured_positions": s["positions"] if s else None,
                "measured_body": s.get("body_feedback", {}) if s else None,
                "feedback_timing": s.get("timing") if s else None,
                "accepted_host_action": ack,
                "acknowledged_intent_revision": ack.get("intent_revision"),
                "host_incarnation": self.host_incarnation,
                "lifecycle": copy.deepcopy(self._lifecycle),
                "transport_issue": self._transient,
                **({"live_input": copy.deepcopy(self._live_expired or ack.get("live_input", {"state": "held", **({} if self.live_spec is TELEOP_SPEC else {"joint": self.live_spec.joint})}))}
                   if self.live_spec is not None else {}),
                "reconciliation": {
                    "state": self._reconciliation_worker.state,
                    "error": self._reconciliation_worker.error,
                }
                if self._reconciliation_worker
                else None,
                **self.sensing_metadata(),
            }

    def dispatch(self, recipe):
        with self.mutex:
            if recipe != self.recipe or self.finish_status or self._lifecycle["phase"] != "live":
                return False
            o = self.observation.evidence()
            if (
                not o["qualified"]
                or not self.evidence()["feedback"]
                or self.sample is None
                or self.clock() - self.sample["at"] >= FEEDBACK_MAX_AGE_S
            ):
                return False
            if self.desired != "active":
                if len(self._aligned) < 3 or self._aligned[-1] - self._aligned[0] < 0.2 - 1e-9:
                    return False
                self.desired = "active"
            self._wake.set()
            return self._dispatch_ack or (self.live_spec is not None and self.evidence()["native_ack"])

    def hold(self):
        with self.mutex:
            self._live_target = None
            self._completion_returns = []
            if self._lifecycle["phase"] not in ("startup", "live"):
                return False  # Idle/restart/finished inspection never opens or commands hardware.
            if self.desired != "hold":
                self.desired = "hold"
                self._dispatch_ack = False
                self._aligned = []
                self._hold_reference = None
            self._wake.set()
            return self.evidence()["hold_acknowledged"]

    def advance(self, now, dt):
        with self.mutex:
            execution = copy.deepcopy(self._execution)
            if self.live_spec is TELEOP_SPEC:
                # Core already persists the full execution and exposes its typed
                # run reference; essential status receives only scalar summaries.
                execution["input_transport_trace"] = copy.deepcopy(self._input_transport_trace)
            return execution

    def finish(self, status):
        with self.mutex:
            if self._lifecycle["phase"] == "idle":
                return {"cleanup": None, "uncertain": False}
            if self._lifecycle["phase"] == "terminal":
                return copy.deepcopy(self._lifecycle)
            if not self.finish_status or status in ("stopped", "faulted", "interrupted"):
                self.finish_status = status
            self.desired = "hold"
            self._dispatch_ack = False
            self._wake.set()
            return {"pending": True, "cleanup": None, "uncertain": False}

    def reconcile(self, run=None, *, operation_id=None):
        from tools.am1_physical_reconciliation import ReconciliationWorker, terminal_run_hash

        if not isinstance(run, dict) or not isinstance(operation_id, str):
            return False
        with self.mutex:
            if self.worker and self.worker.is_alive():
                return False
            worker = self._reconciliation_worker
            if worker and terminal_run_hash(worker.run) != terminal_run_hash(run):
                return False
            if worker is None or not worker.thread.is_alive() and worker.state in ("error", "expired"):
                self._reconciliation_worker = ReconciliationWorker(self.config, run, operation_id)
                return False
            receipt = worker.request(operation_id)
            if isinstance(receipt, dict):
                self._lifecycle["uncertain"] = False
            return receipt

    def close(self):
        if self._reconciliation_worker:
            self._reconciliation_worker.close()
        self.finish("interrupted")

    def _run(self):
        from examples.alohamini.am1_finite_task import FiniteTask
        from tools.am1_pi_executor import SimulatedIO

        io = None
        live_spec = self.live_spec
        live_input = live_spec is not None
        try:
            definition = ELBOW_RECOVERY_DEFINITIONS.get(self.recipe.name, LEFT_ELBOW_ORIGINAL_RETURN)
            loaded = (
                load_elbow_return_reference(self.config, definition=definition)
                if self.recipe.name in ELBOW_RECOVERY_DEFINITIONS
                else load_elbow_return_reference(self.config)
                if self.recipe.name == LEFT_ELBOW_ORIGINAL_RETURN.recipe_name
                else None
            )
            if self.recipe.name in {LEFT_ELBOW_ORIGINAL_RETURN.recipe_name, *ELBOW_RECOVERY_DEFINITIONS} and (
                loaded != self._reference_for_recipe()
            ):
                raise ValueError("frozen elbow return reference changed before IO")
            factory = self.io_factory or ProtectedHostRun
            io = factory(self.config)
            if isinstance(io, SimulatedIO) or getattr(io, "provenance", None) != "protected-host":
                raise TypeError("physical recipes require protected host IO, never simulated IO")

            def cancel():
                if self.finish_status:
                    raise StartupCancelledError("physical startup cancelled by Stop")
                if self.clock() >= self._lifecycle["startup_deadline"]:
                    raise RuntimeError("physical startup deadline expired")

            io.observation = self.observation
            io.start(
                self.run_id, self.host_incarnation, self.recipe, cancel,
                **({"selected_experiment": self.selected_experiment.payload}
                   if self.recipe.name == SelectedElbowExperiment.recipe_name else {}),
            )
            while True:
                with self.mutex:
                    if self.finish_status:
                        break
                    if self._lifecycle["phase"] == "startup":
                        cancel()
                    poll_control = (
                        self.revision, self._epoch, tuple(sorted((self._live_binding or {}).items()))
                    )
                    if (
                        self._lifecycle["native_live_at"] is not None
                        and self.clock() >= self._lifecycle["native_live_at"] + self._live_ceiling()
                    ):
                        self.finish_status = "stopped"
                        break
                try:
                    previous = self.sample["sequence"] if self.sample else -1
                    sample = io.poll(previous)
                    transient = None
                except (FeedbackUnavailable, OSError) as exc:
                    sample = None
                    transient = str(exc)
                with self.mutex:
                    if self.finish_status:
                        break
                    advanced, usable = self._update_polled_sample(sample, transient)
                    observed = self.observation.evidence()["qualified"]
                    if not usable or not observed:
                        # This protection acts at native cadence even if web/database owner stalls.
                        self.desired = "hold"
                        self._dispatch_ack = False
                        self._aligned = []
                        self._hold_reference = None
                        self._completion_returns = []
                    latest_ack = self.sample["ack"] if self.sample else {}
                    if advanced and live_input:
                        self._retain_live_expiry(
                            poll_control=poll_control, previous_sequence=previous, usable=usable
                        )
                    if (
                        advanced
                        and usable
                        and self._epoch_mode == "active"
                        and latest_ack.get("state") == "paused"
                        and latest_ack.get("epoch") == self._epoch
                        and latest_ack.get("intent_revision") == self.revision
                    ):
                        # Native watchdog pause revokes the old active epoch. Requalify a new measured hold.
                        self.desired = "hold"
                        self._dispatch_ack = False
                        self._aligned = []
                        self._hold_reference = None
                        self._completion_returns = []
                    desired = "active" if self.desired == "active" and usable and observed else "pause"
                    if (
                        not advanced
                        and desired == "active"
                        and (self._epoch_mode != "active" or self._epoch_revision != self.revision)
                    ):
                        desired = "pause"  # Cached ACK cannot activate a new revision/epoch.
                    if self._epoch_mode != desired or self._epoch_revision != self.revision:
                        if desired == "pause":
                            # Revisions can coalesce before a queued pause reaches
                            # the host. Keep its pending odd epoch until original
                            # current feedback proves that pause was accepted.
                            pause_acknowledged = (
                                usable
                                and latest_ack.get("run_id") == self.run_id
                                and latest_ack.get("host_incarnation") == self.host_incarnation
                                and latest_ack.get("state") == "paused"
                                and latest_ack.get("epoch") == self._epoch
                                and latest_ack.get("intent_revision") == self._epoch_revision
                            )
                            if self._epoch_mode != "pause" or pause_acknowledged:
                                self._epoch = (
                                    1 if self._epoch < 0 else self._epoch + (2 if self._epoch % 2 else 1)
                                )
                            self._aligned = []
                            self._hold_reference = None
                        else:
                            if (self.task and self.recipe.name == SelectedElbowExperiment.recipe_name
                                    and self.selected_experiment.schema_version == 2):
                                self.task.prepare_resume(self.clock(), self.sample)
                            self._epoch += 1
                        self._epoch_mode = desired
                        self._epoch_revision = self.revision
                    ack = self.sample["ack"] if self.sample else {}
                    matched = (
                        usable
                        and ack.get("run_id") == self.run_id
                        and ack.get("host_incarnation") == self.host_incarnation
                        and ack.get("intent_revision") == self.revision
                        and ack.get("epoch") == self._epoch
                    )
                    if advanced and matched and desired == "pause" and ack.get("state") == "paused":
                        positions = self.sample["positions"]
                        if self._hold_reference is None:
                            self._hold_reference = dict(positions)
                        if all(abs(positions[k] - self._hold_reference[k]) <= 1 for k in JOINT_KEYS):
                            if not self._aligned or self.sample["at"] - self._aligned[-1] >= 0.1 - 1e-9:
                                self._aligned.append(self.sample["at"])
                                self._aligned = self._aligned[-3:]
                        else:
                            self._aligned = []
                            self._hold_reference = dict(positions)
                        if live_input and observed and len(self._aligned) >= 3 and self._aligned[-1] - self._aligned[0] >= .2 - 1e-9:
                            self._live_admission_requested = True
                            admitted = ack.get("live_input", {})
                            if self._lifecycle["phase"] == "startup" and admitted.get("live_admitted_at_monotonic_s") is not None:
                                self._lifecycle.update(phase="live", native_live_at=admitted["live_admitted_at_monotonic_s"],
                                    native_deadline_monotonic_s=admitted["live_deadline_monotonic_s"],
                                    backend_incarnation=self.host_incarnation)
                        if (
                            self._lifecycle["phase"] == "startup"
                            and not live_input
                            and observed
                            and len(self._aligned) >= 3
                            and self._aligned[-1] - self._aligned[0] >= 0.2 - 1e-9
                        ):
                            live = self.clock()
                            self._lifecycle.update(
                                phase="live", native_live_at=live, backend_incarnation=self.host_incarnation
                            )
                            self.task = FiniteTask(
                                self.recipe,
                                self.sample["positions"],
                                live,
                                self.config["shoulder_amplitude"],
                                start_held=True,
                                feedback_provenance="protected-host/measured-feedback",
                                **({"selected_experiment": self.selected_experiment}
                                   if self.recipe.name == SelectedElbowExperiment.recipe_name else {}),
                                **({"positive_comparator": copy.deepcopy(self.positive_prerequisite)}
                                   if self.recipe.name == LEFT_ELBOW_POSITIVE_CONTROL.recipe_name else {}),
                                **(
                                    {
                                        "return_reference": self._reference_for_recipe(),
                                        "reference_sha256": self.config["elbow_return_reference"]["sha256"],
                                    }
                                    if self.recipe.name
                                    in {LEFT_ELBOW_ORIGINAL_RETURN.recipe_name, *ELBOW_RECOVERY_DEFINITIONS}
                                    else {}
                                ),
                                **(
                                    {"recovery_comparator": copy.deepcopy(self.recovery_prerequisite)}
                                    if self.recipe.name in ELBOW_RECOVERY_DEFINITIONS
                                    and ELBOW_RECOVERY_DEFINITIONS[self.recipe.name].integral == 1
                                    and ELBOW_RECOVERY_DEFINITIONS[self.recipe.name].mode == "return"
                                    else {}
                                ),
                            )
                            self._execution = dict(self.task.snapshot(), fault=None)
                    if live_input:
                        if advanced:
                            self._dispatch_ack = bool(matched and ack.get("state") == "active")
                        native = copy.deepcopy(ack.get("live_input", {}))
                        self._execution = {"progress_s": 0 if self._lifecycle["native_live_at"] is None else
                            max(0, self.clock() - self._lifecycle["native_live_at"]), "complete": False,
                            "fault": self._fault, "profile": live_spec.profile, "live_input": native,
                            "provenance": ("leader-origin/protected-host/measured-feedback" if live_spec is TELEOP_SPEC else
                                           "browser-origin/protected-host/measured-feedback")}
                    if self.task:
                        if desired == "pause":
                            if self.task.active:
                                self.task.hold()
                        elif advanced and matched and ack.get("state") == "active":
                            self._dispatch_ack = True
                            if not self.task.active:
                                self.task.resume(self.clock())
                            was_complete = self.task.complete
                            advanced = self.task.advance(self.clock(), self.sample)
                            if advanced:
                                self._execution = dict(self.task.snapshot(), fault=None)
                            if self.task.complete:
                                reack = None
                                qualified_return = advanced and not was_complete
                                if advanced and was_complete and self._finite_completion is None:
                                    # An explicit revision change can follow an already qualified
                                    # completion. Re-acknowledge its original return, never restart
                                    # or rebase the finite provider, before holding again.
                                    for key, origin in self.task.original_seed.items():
                                        if (
                                            self.recipe.name == LEFT_ELBOW_ORIGINAL_RETURN.recipe_name
                                            and key == LEFT_ELBOW_ORIGINAL_RETURN.joint
                                        ):
                                            origin = self.task.provider.retained_return[
                                                "reference_normalized"
                                            ]
                                        limit = (
                                            (10.0 if key == LEFT_ELBOW_POSITIVE_CONTROL.joint else 1.0)
                                            if self.recipe.name in {LEFT_ELBOW_POSITIVE_CONTROL.recipe_name, SelectedElbowExperiment.recipe_name} else
                                            1.0
                                            if self.task.profile
                                            in {
                                                "ArmHoldBody",
                                                LEFT_ELBOW_RESPONSE.profile,
                                                LEFT_ELBOW_ORIGINAL_RETURN.profile,
                                            }
                                            else self.task.joint_amplitudes[key]
                                        )
                                        if abs(self.sample["positions"][key] - origin) > limit:
                                            raise ValueError(
                                                f"{self.task.profile} return exceeds the original envelope for {key}"
                                            )
                                    self._completion_returns.append(
                                        (self.sample["at"], self.sample["sequence"])
                                    )
                                    self._completion_returns = self._completion_returns[-3:]
                                    span = self._completion_returns[-1][0] - self._completion_returns[0][0]
                                    qualified_return = (
                                        len(self._completion_returns) == 3 and span >= 0.2 - 1e-9
                                    )
                                    if qualified_return:
                                        reack = {
                                            "sample_count": 3,
                                            "span_s": span,
                                            "first_feedback_sequence": self._completion_returns[0][1],
                                        }
                                if qualified_return and self._finite_completion is None:
                                    # Freeze the actual qualified return and active ACK before the
                                    # ordinary hold advances the host epoch. Publish atomically with
                                    # current evidence; a later hold must not replace this proof.
                                    self._finite_completion = {
                                        "run_id": self.run_id,
                                        "host_incarnation": self.host_incarnation,
                                        "intent_revision": self.revision,
                                        "recipe": self.recipe.name,
                                        "completed_at_monotonic_s": self.clock(),
                                        "feedback_at_monotonic_s": self.sample["at"],
                                        "feedback_sequence": self.sample["sequence"],
                                        "accepted_host_action": copy.deepcopy(ack),
                                        "required_observation": observed,
                                        "measured_positions": copy.deepcopy(self.sample["positions"]),
                                        "execution": copy.deepcopy(self._execution),
                                    }
                                    if reack is not None:
                                        self._finite_completion["return_reacknowledgment"] = reack
                                if self._finite_completion is not None:
                                    self.desired = "hold"
                        self.task.check(self.clock())
                    action = dict(ZERO_BODY)
                    if desired == "active" and self.task:
                        action.update({"arm_" + k: v for k, v in self.task.provider.get_action().items()})
                        if self.recipe.name in self.selected_joint_recipes and not getattr(
                            self.task.provider, "preparing", False
                        ):
                            action["_am1_selected_joint_evidence"] = self.task.provider.diagnostic_context
                        if (
                            self.recipe.name == "physical-arm-hold-body"
                            and self._dispatch_ack
                            and not getattr(self.task.provider, "preparing", False)
                        ):
                            body = io.body_action(self.task.provider.elapsed_s)
                            if "lift_axis.height_mm" in body and "lift_axis.vel" not in body:
                                # Preserve the original height request without inventing velocity precedence.
                                action.pop("lift_axis.vel")
                            action.update(body)
                    self._action_sequence += 1
                    action["_am1_local_control"] = {
                        "version": 1,
                        "mode": desired,
                        "epoch": self._epoch,
                        "run_id": self.run_id,
                        "host_incarnation": self.host_incarnation,
                        "intent_revision": self.revision,
                        "action_sequence": self._action_sequence,
                    }
                    if live_input:
                        # One stored sample, unchanged through every native send.
                        marker = action["_am1_local_control"]
                        marker.update(version=2, live_binding=copy.deepcopy(self._live_binding),
                            live_admitted=self._live_admission_requested,
                            live_input=copy.deepcopy(self._live_target) if desired == "active" else None)
                    # Bound send is outside authority; the worker mutex fences concurrent Pause/Stop.
                    send_trace = self._input_transport_row("worker_send", action["_am1_local_control"])
                    try:
                        try:
                            io.send(action)
                        except BaseException as exc:
                            self._append_input_transport(send_trace, outcome="raised", error_type=type(exc).__name__)
                            raise
                        else:
                            # This is only the transport call returning, never a
                            # unique native admission or an SDK-write receipt.
                            self._append_input_transport(send_trace, outcome="returned")
                    except (FeedbackUnavailable, OSError) as exc:
                        self._transient = str(exc)
                        self.desired = "hold"
                        self._dispatch_ack = False
                        self._aligned = []
                        self._hold_reference = None
                        self._completion_returns = []
                self._wake.wait(1 / 30)
                self._wake.clear()
        except StartupCancelledError:
            pass
        except Exception as exc:
            with self.mutex:
                self._fault = self._fault or f"{type(exc).__name__}: {exc}"
                self.finish_status = "faulted"
                self._execution = dict(self._execution, fault=self._fault)
        finally:
            with self.mutex:
                self._lifecycle["phase"] = "finishing"
                status = self.finish_status or "faulted"
                if self.task:
                    self.task.finish(status)
                    self._execution = dict(self.task.snapshot(), fault=self._fault)
            try:
                outcome = (
                    io.finish(status)
                    if io
                    else {
                        "cleanup": "physical_cleanup_unknown",
                        "uncertain": True,
                        "terminal_status": "faulted",
                    }
                )
            except Exception as exc:
                outcome = {
                    "cleanup": "physical_cleanup_unknown",
                    "uncertain": True,
                    "terminal_status": "faulted",
                    "error": str(exc),
                }
            with self.mutex:
                self.observation.stop()  # Required capture remains available throughout actual host finishing.
                self._lifecycle.update(outcome, phase="terminal", run_id=self.run_id)
                self._dispatch_ack = False


BODY_MOTORS = ("base_left_wheel", "base_back_wheel", "base_right_wheel", "lift_axis")


def qualified_cleanup_receipt(receipt, run_id, incarnation):
    if (
        not isinstance(receipt, dict)
        or receipt.get("version") != 1
        or receipt.get("run_id") != run_id
        or receipt.get("host_incarnation") != incarnation
    ):
        return False
    readback = receipt.get("readback")
    if (
        receipt.get("cleanup") != "physical_readback_verified"
        or receipt.get("uncertain") is not False
        or receipt.get("cleanup_errors")
        or not isinstance(readback, dict)
    ):
        return False
    if (
        not readback.get("verified")
        or readback.get("lift_readback") != "qualified"
        or readback.get("selected_gain_restore_required")
    ):
        return False
    expected = {
        ("left" if "_left_" in k else "right", k.removesuffix(".pos"), "Torque_Enable") for k in JOINT_KEYS
    }
    expected.update(("left", k, "Torque_Enable") for k in BODY_MOTORS)
    expected.update(("left", k, "Goal_Velocity") for k in BODY_MOTORS)
    rows = readback.get("readbacks")
    if not isinstance(rows, list) or len(rows) != len(expected):
        return False
    return {(r.get("bus"), r.get("motor"), r.get("register")) for r in rows} == expected and all(
        type(r.get("value")) is int and r["value"] == 0 and not r.get("error") for r in rows
    )


class ProtectedHostRun:
    """One bounded sole local client; the existing child host alone opens servo buses."""

    provenance = "protected-host"

    def __init__(self, config):
        from pathlib import Path

        self.config = config
        self.host = self.camera = self.client = None
        self.observation = None
        self.host_log = self.camera_log = None
        self.run_id = self.incarnation = None
        self.recipe = None
        self.run_directory = None
        self.receipt = None
        required = {
            "version",
            "machine",
            "host_repository",
            "host_head",
            "host_python",
            "camera_repository",
            "camera_head",
            "camera_python",
            "camera_config",
            "camera_credentials",
            "camera_binary",
            "camera_state",
            "physical_state_directory",
            "run_root",
            "shoulder_amplitude",
            "calibration_files",
            "source_pins",
        }
        if set(config) not in (required, required | {"elbow_return_reference"}) or config["version"] != 1:
            raise ValueError("exact protected backend configuration required")
        if not 0 < float(config["shoulder_amplitude"]) <= 3 or not config["calibration_files"]:
            raise ValueError("actual calibration and mapped amplitude required")
        for k in ("host_head", "camera_head"):
            if not __import__("re").fullmatch("[0-9a-f]{40}", config[k]):
                raise ValueError("exact component source commit required")
        for k in required - {
            "version",
            "machine",
            "host_head",
            "camera_head",
            "shoulder_amplitude",
            "calibration_files",
            "source_pins",
        }:
            if not Path(config[k]).is_absolute():
                raise ValueError("absolute protected backend paths required")
        if Path(config["physical_state_directory"]).resolve() != Path.home() / ".local/state/am1-session":
            raise ValueError("canonical physical admission namespace required")

    def _validate_source(self):
        import hashlib
        import socket
        import subprocess
        from pathlib import Path

        from examples.alohamini.am1_session_contract import LIVE_ELBOW_WORKSPACES
        workspace = LIVE_ELBOW_WORKSPACES.get(getattr(getattr(self, "recipe", None), "name", None))
        if workspace is not None:
            files = self.config.get("calibration_files", {})
            if len(files) != 1 or set(files.values()) != {workspace.calibration_sha256}:
                raise RuntimeError("Live workspace requires the preserved full calibration content")
        if socket.gethostname() != self.config["machine"]:
            raise RuntimeError("AM1 machine identity mismatch")
        for name in ("host", "camera"):
            repository = self.config[name + "_repository"]
            head = subprocess.check_output(
                ["git", "-C", repository, "rev-parse", "HEAD"], text=True, timeout=5
            ).strip()
            dirty = subprocess.check_output(
                ["git", "-C", repository, "status", "--porcelain"], text=True, timeout=5
            ).strip()
            if dirty or head != self.config[name + "_head"]:
                raise RuntimeError(name + " source pin/cleanliness mismatch")
        for path, digest in self.config["calibration_files"].items():
            if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest:
                raise RuntimeError("preserved calibration content changed")

    def start(self, run_id, incarnation, recipe, cancel, *, selected_experiment=None):
        definition = None
        if recipe.name == SelectedElbowExperiment.recipe_name:
            if selected_experiment is None:
                raise ValueError("Selected experiment descriptor required before native IO")
            definition = SelectedElbowExperiment.from_payload(selected_experiment)
        elif selected_experiment is not None:
            raise ValueError("Selected experiment descriptor forbidden for ordinary recipes")
        if recipe.name == LEFT_ELBOW_ORIGINAL_RETURN.recipe_name:
            load_elbow_return_reference(self.config)  # Preserve the old strict pre-IO interface.
        elif recipe.name in ELBOW_RECOVERY_DEFINITIONS:
            load_elbow_return_reference(self.config, definition=ELBOW_RECOVERY_DEFINITIONS[recipe.name])
        import os
        from pathlib import Path

        from lerobot.robots.alohamini.alohamini_client import AlohaMiniClient
        from lerobot.robots.alohamini.config_alohamini import AlohaMiniClientConfig

        self.run_id, self.incarnation = run_id, incarnation
        self.recipe = recipe
        self._validate_source()
        cancel()
        state = Path(self.config["physical_state_directory"])
        if (state / "cleanup-uncertain.json").exists() or (state / "physical-in-progress.json").exists():
            raise RuntimeError("previous physical cleanup requires actual stopped reconciliation")
        pending_response = [state / (side + "-response-p-request.json") for side in ("left", "right")]
        if all(path.exists() or path.is_symlink() for path in pending_response):
            raise ValueError("response P admission requires one selected elbow request")
        # The isolated hardware host unit acquires canonical + serial admission before robot construction.
        # Parent/client stays in its existing PrivateDevices=yes namespace.
        cancel()
        root = Path(self.config["run_root"])
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.run_directory = root / run_id[:8]
        self.run_directory.mkdir(mode=0o700)
        self.receipt = self.run_directory / "cleanup.json"
        from lerobot.robots.alohamini.am1_elbow_integral import (
            consume_left_response_p_request,
            consume_right_p20_request,
            consume_right_response_p_request,
        )
        left_response_p_file = consume_left_response_p_request(
            state, self.run_directory, recipe.name, run_id, incarnation, self.config["host_head"]
        )
        right_response_p_file = None
        if left_response_p_file is None:
            right_response_p_file = consume_right_response_p_request(
                state, self.run_directory, recipe.name, run_id, incarnation, self.config["host_head"]
            )
        # Named baseline presets derive fixed native settings from the authenticated
        # recipe and this fresh run identity; experimental nonces are unrelated.
        right_p20_file = None
        if recipe.name not in PRACTICAL_ELBOW_PRESETS:
            right_p20_file = consume_right_p20_request(state, self.run_directory, recipe.name,
                                                     run_id, incarnation, self.config["host_head"])
        experiment_file = None
        if definition is not None:
            import json

            experiment_file = self.run_directory / "selected-experiment.json"
            descriptor = os.open(experiment_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(definition.payload, stream, sort_keys=True, separators=(",", ":"), allow_nan=False)
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["AM1_CAMERA_PYTHON"] = self.config["camera_python"]
        env["AM1_CAMERA_LOG_DIRECTORY"] = str(self.run_directory)
        self.camera_log = (self.run_directory / "camera-launch.log").open("xb")
        camera_command = [
            "bash",
            self.config["camera_repository"] + "/tools/run_am1_camera.sh",
            "--config",
            self.config["camera_config"],
            "--credentials",
            self.config["camera_credentials"],
            "--binary",
            self.config["camera_binary"],
            "--state-dir",
            self.config["camera_state"],
            "--owned-session",
            run_id,
            "--source-budget-seconds",
            str(180 + recipe.live_s + 60 + 30),
        ]
        camera_environment = {
            k: env[k] for k in ("PYTHONDONTWRITEBYTECODE", "AM1_CAMERA_PYTHON", "AM1_CAMERA_LOG_DIRECTORY")
        }
        self.camera = OwnedUnit(
            "am1-pa01-camera-" + run_id,
            self.config["camera_repository"],
            camera_command,
            camera_environment,
            180 + recipe.live_s + 60 + 50,
            20,
            signal_name="SIGTERM",
            log=self.camera_log,
            camera_route_discovery=True,
        )
        wait_required_camera(self.camera, self.observation, cancel)
        cancel()
        env["AM1_MOTOR_PYTHON"] = self.config["host_python"]
        env["AM1_LOG_DIRECTORY"] = str(self.run_directory)
        env["AM1_SYNC_SHOULDER_READBACK"] = "1"
        env["AM1_LEFT_SHOULDER_EVIDENCE"] = "1"
        # Never carry the arm-only selected integral experiment into the body recipe.
        for k in (
            "AM1_SHOULDER_INTEGRAL_TEST",
            "AM1_SCRIPTED_PREPARE",
            "AM1_SELECTED_JOINT_EVIDENCE",
            "AM1_ELBOW_RECOVERY",
            "AM1_ELBOW_RECOVERY_RECIPE",
            "AM1_VISIBLE_CONTROL_RECIPE",
        ):
            env.pop(k, None)
        if recipe.name in ("physical-arm-smoke", "physical-arm-smoke-repeat"):
            env["AM1_SHOULDER_INTEGRAL_TEST"] = "1"
            env["AM1_SCRIPTED_PREPARE"] = "1"
        if recipe.name in PhysicalExecutor.selected_joint_recipes or recipe.name in LIVE_ELBOW_SPECS:
            for k in ("AM1_SYNC_SHOULDER_READBACK", "AM1_LEFT_SHOULDER_EVIDENCE"):
                env.pop(k, None)
            env["AM1_SCRIPTED_PREPARE"] = "1"
            env["AM1_SELECTED_JOINT_EVIDENCE"] = "1"
        if recipe.name == TELEOP_RECIPE:
            for k in ("AM1_SYNC_SHOULDER_READBACK", "AM1_LEFT_SHOULDER_EVIDENCE"):
                env.pop(k, None)
            env["AM1_SCRIPTED_PREPARE"] = "1"
        if recipe.name in {LEFT_ELBOW_POSITIVE_CONTROL.recipe_name, SelectedElbowExperiment.recipe_name, *LIVE_ELBOW_SPECS, TELEOP_RECIPE}:
            env["AM1_VISIBLE_CONTROL_RECIPE"] = recipe.name
        if recipe.name in ELBOW_RECOVERY_DEFINITIONS:
            env["AM1_ELBOW_RECOVERY"] = f"I{ELBOW_RECOVERY_DEFINITIONS[recipe.name].integral}"
            env["AM1_ELBOW_RECOVERY_RECIPE"] = recipe.name
        self.host_log = (self.run_directory / "host-launch.log").open("xb")
        host_command = [
            self.config["host_python"],
            "-B",
            "-m",
            "lerobot.robots.alohamini.alohamini_host",
            "--robot_model",
            "alohamini1",
            "--no_cameras",
            "--max_relative_target",
            "20.0",
            "--max_loop_freq_hz",
            "30",
            "--profile_timing",
            "true",
            "--profile_cadence",
            "--am1-protected-run-directory",
            str(self.run_directory),
            "--am1-run-id",
            run_id,
            "--am1-host-incarnation",
            incarnation,
            "--am1-cleanup-receipt",
            str(self.receipt),
            "--am1-physical-state-directory",
            str(state),
        ]
        if experiment_file is not None:
            host_command.extend(["--am1-selected-experiment-file", str(experiment_file)])
        if right_p20_file is not None:
            host_command.extend(["--am1-right-p20-trial-file", str(right_p20_file)])
        if left_response_p_file is not None:
            host_command.extend(["--am1-left-response-p-trial-file", str(left_response_p_file)])
        if right_response_p_file is not None:
            host_command.extend(["--am1-right-response-p-trial-file", str(right_response_p_file)])
        host_environment = {
            k: env[k]
            for k in (
                "AM1_LOG_DIRECTORY",
                "AM1_SYNC_SHOULDER_READBACK",
                "AM1_LEFT_SHOULDER_EVIDENCE",
                "AM1_SHOULDER_INTEGRAL_TEST",
                "AM1_SCRIPTED_PREPARE",
                "AM1_SELECTED_JOINT_EVIDENCE",
                "AM1_ELBOW_RECOVERY",
                "AM1_ELBOW_RECOVERY_RECIPE",
            "AM1_VISIBLE_CONTROL_RECIPE",
            )
            if k in env
        }
        host_environment.update(
            PYTHONPATH=self.config["host_repository"] + "/src",
            PYTHONDONTWRITEBYTECODE="1",
            PYTHONUNBUFFERED="1",
        )
        self.host = OwnedUnit(
            "am1-pa01-host-" + run_id,
            self.config["host_repository"],
            host_command,
            host_environment,
            720,
            80,
            signal_name="SIGINT",
            log=self.host_log,
        )
        # This fresh run directory cannot borrow another run's readiness. Retain
        # its post-home marker while activation finishes and the socket binds;
        # startup receipts and routine lift output may exceed the old log tail.
        logpath = self.run_directory / "host-runtime.log"
        ready_marker = b'"operational_ready"'
        ready, log_offset, log_overlap = False, 0, b""
        while True:
            cancel()
            if self.host.poll() is not None:
                raise RuntimeError(
                    "protected host exited during startup; inspect original host log and cleanup receipt"
                )
            if self.camera.poll() is not None:
                raise RuntimeError("owned required camera exited during startup; inspect original camera log")
            if not ready and logpath.exists():
                with logpath.open("rb") as stream:
                    stream.seek(log_offset)
                    chunk = stream.read(64000)
                log_offset += len(chunk)
                scanned = log_overlap + chunk
                ready = ready_marker in scanned
                log_overlap = scanned[-(len(ready_marker) - 1):]
            # Socket bind alone is not ready. Every bounded scan/wait keeps the
            # same cancellation deadline and actual host/camera exit fences.
            if ready and (self.run_directory / "observation.sock").exists():
                break
            time.sleep(0.05)
        cancel()
        self.client = AlohaMiniClient(
            AlohaMiniClientConfig(
                remote_ip="127.0.0.1",
                robot_model="alohamini1",
                cameras={},
                polling_timeout_ms=40,
                connect_timeout_s=5,
                command_send_timeout_ms=40,
                observation_request_window=3,
                command_endpoint="ipc://" + str(self.run_directory / "command.sock"),
                observation_endpoint="ipc://" + str(self.run_directory / "observation.sock"),
            )
        )
        self.client.connect(cancel_check=cancel)

    def poll(self, previous):
        if self.host.poll() is not None:
            raise RuntimeError("protected host exited; original first cause retained in host log/receipt")
        if self.camera.poll() is not None:
            raise RuntimeError("required owned camera exited before physical finishing")
        return read_physical_sample(
            self.client,
            self.run_id,
            self.incarnation,
            previous,
            allow_pending_sample=self.recipe.name == TELEOP_RECIPE,
        )

    def send(self, action):
        import zmq

        try:
            self.client.send_action(action)
        except zmq.ZMQError as exc:
            raise FeedbackUnavailable("bounded local command transport: " + str(exc)) from exc

    def body_action(self, elapsed):
        return body_recipe_action(self.client, elapsed)

    def finish(self, status):
        import json
        from pathlib import Path

        errors = []
        finish_deadline = time.monotonic() + 55
        receipt = None
        # One ordinary signal to the owned host group; its normal disconnect/readback remains authoritative.
        if self.host is not None and not self.host.actual_exited():
            try:
                self.host.stop()
                self.host.wait_actual_exit(timeout=max(0.1, finish_deadline - time.monotonic() - 20))
            except TimeoutError:
                errors.append("physical host cleanup deadline expired; owner retained, no forced rearm")
            except (__import__("subprocess").SubprocessError, OSError, RuntimeError) as exc:
                errors.append("physical host cooperative cleanup unconfirmed: " + str(exc))
        if self.receipt is not None and self.receipt.exists():
            try:
                receipt = json.loads(self.receipt.read_bytes())
            except (ValueError, OSError) as exc:
                errors.append("physical receipt unavailable: " + str(exc))
        if self.client is not None and self.client.is_connected:
            try:
                self.client.disconnect()
            except Exception as exc:
                errors.append("local client close: " + str(exc))
        motors = qualified_cleanup_receipt(receipt, self.run_id, self.incarnation)
        no_device_effects = self.host is None
        # Required camera remains through host finishing. Release main process once; it cleans its child.
        camera_receipt = None
        host_exited = self.host is None or self.host.actual_exited()
        if self.camera is not None and not self.camera.actual_exited() and host_exited:
            try:
                self.camera.stop()
                self.camera.wait_actual_exit(timeout=max(0.1, finish_deadline - time.monotonic()))
            except (TimeoutError, __import__("subprocess").SubprocessError, OSError, RuntimeError) as exc:
                errors.append("owned camera cooperative release unconfirmed: " + str(exc))
        if self.run_id:
            path = Path(self.config["camera_state"]) / ("release-" + self.run_id + ".json")
            if path.exists():
                try:
                    camera_receipt = json.loads(path.read_bytes())
                except (ValueError, OSError) as exc:
                    errors.append("camera receipt unavailable: " + str(exc))
        camera_clean = self.camera is None or (
            isinstance(camera_receipt, dict)
            and camera_receipt.get("state") == "released"
            and camera_receipt.get("session_id") == self.run_id
            and not camera_receipt.get("errors")
            and all(
                (camera_receipt.get("release_evidence") or {}).get(k)
                for k in (
                    "backend_exited",
                    "readers_joined",
                    "viewer_socket_closed",
                    "runtime_config_removed",
                )
            )
        )
        clean = (motors or no_device_effects) and camera_clean and not errors
        # Release canonical only when host has actually exited; unknown cleanup remains persistently latched.
        for stream in (self.host_log, self.camera_log):
            if stream:
                stream.close()
        first = receipt.get("first_cause") if isinstance(receipt, dict) else None
        return {
            "cleanup": "physical_readback_verified"
            if clean and motors
            else ("no_physical_device_effects" if clean else "physical_cleanup_unknown"),
            "uncertain": not clean,
            "terminal_status": "faulted" if first else status,
            "first_cause": first,
            "readback": receipt,
            "camera_release": camera_receipt,
            "cleanup_errors": errors,
            "backend_incarnation": self.incarnation,
            "run_directory": str(self.run_directory),
        }


def protected_unit_command(
    name,
    cwd,
    command,
    *,
    environment,
    runtime_s,
    stop_s,
    devices,
    signal_name="SIGINT",
    log_path=None,
    camera_route_discovery=False,
):
    if not 0 < runtime_s <= 920 or not 0 < stop_s <= 80 or signal_name not in ("SIGINT", "SIGTERM"):
        raise ValueError("invalid bounded owned unit limits")
    if type(camera_route_discovery) is not bool:
        raise ValueError("camera route discovery requires an explicit bool")
    args = [
        "systemd-run",
        "--user",
        "--unit=" + name,
        "--wait",
        "--collect",
        "-p",
        "WorkingDirectory=" + cwd,
        "-p",
        "NoNewPrivileges=yes",
        "-p",
        "PrivateDevices=" + ("no" if devices else "yes"),
        "-p",
        "KillMode=mixed",
        "-p",
        "KillSignal=" + signal_name,
        "-p",
        "Restart=no",
        "-p",
        "UMask=0077",
        "-p",
        "RuntimeMaxSec=" + str(runtime_s) + "s",
        "-p",
        "TimeoutStopSec=" + str(stop_s) + "s",
        "-p",
        # go2rtc needs route/interface discovery; only its camera owner opts in.
        "RestrictAddressFamilies=AF_UNIX AF_INET" + (" AF_NETLINK" if camera_route_discovery else ""),
    ]
    if log_path:
        args.extend(["-p", "StandardOutput=append:" + log_path, "-p", "StandardError=inherit"])
    for k, v in environment.items():
        args.extend(["-p", "Environment=" + k + "=" + v])
    return args + command


class OwnedUnit:
    """Administrative monitor of an exact finite user unit; monitor exit is never cleanup proof."""

    def __init__(
        self,
        name,
        cwd,
        command,
        environment,
        runtime_s,
        stop_s,
        *,
        signal_name,
        log,
        camera_route_discovery=False,
    ):
        import subprocess
        from pathlib import Path

        self.name = name
        runtime_name = "host-runtime.log" if "-host-" in name else "camera-runtime.log"
        log_path = str(Path(log.name).parent / runtime_name)
        args = protected_unit_command(
            name,
            cwd,
            command,
            environment=environment,
            runtime_s=runtime_s,
            stop_s=stop_s,
            devices=True,
            signal_name=signal_name,
            log_path=log_path,
            camera_route_discovery=camera_route_discovery,
        )
        self.monitor = subprocess.Popen(
            args, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
        )
        self.stopping = False

    def poll(self):
        return self.monitor.poll()

    def wait(self, timeout):
        return self.monitor.wait(timeout=timeout)

    def _unit_state(self, timeout=3):
        import subprocess

        reply = subprocess.run(
            [
                "systemctl",
                "--user",
                "show",
                self.name,
                "--no-pager",
                "--property=Id,LoadState,ActiveState,MainPID,ControlPID,Job",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        values = dict(line.split("=", 1) for line in reply.stdout.splitlines() if "=" in line)
        if reply.returncode not in (0, 4) or values.get("Id") != self.name + ".service":
            raise RuntimeError("exact owned unit state unavailable")
        return values

    def actual_exited(self, timeout=3):
        import subprocess

        # A pending systemd-run can still register/start the unit. Fence that future dispatch,
        # then require fresh unit state including absence of an outstanding start/stop job.
        if self.monitor.poll() is None:
            return False
        try:
            values = self._unit_state(timeout)
            return (
                values.get("LoadState") in ("loaded", "not-found")
                and values.get("ActiveState") in ("inactive", "failed")
                and values.get("MainPID") == "0"
                and values.get("ControlPID") == "0"
                and values.get("Job") in ("", "0")
            )
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            return False

    def wait_actual_exit(self, timeout):
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("owned unit actual exit unconfirmed")
            if self.actual_exited(timeout=min(3, remaining)):
                break
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))
        return self.monitor.poll()

    def stop(self):
        import subprocess

        if self.stopping:
            return
        # Do not send Stop to a not-yet-registered name and then let its pending launcher
        # create a new start job. Wait boundedly for this exact launch to register or end.
        registration_deadline = time.monotonic() + 5
        while True:
            remaining = registration_deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("owned unit launch registration unconfirmed; owner retained")
            values = self._unit_state(timeout=min(1, remaining))
            if values.get("LoadState") == "loaded":
                break
            if values.get("LoadState") != "not-found":
                raise RuntimeError("owned unit launch state unconfirmed; owner retained")
            if self.monitor.poll() is not None and self.actual_exited(timeout=min(1, remaining)):
                return
            time.sleep(min(0.05, max(0, registration_deadline - time.monotonic())))
        self.stopping = True
        # One nonblocking ordinary request cancels a queued start or stops the actual unit.
        result = subprocess.run(
            ["systemctl", "--user", "--no-block", "stop", self.name], capture_output=True, timeout=5
        )
        if result.returncode:
            raise RuntimeError("owned unit cooperative stop refused")


def wait_required_camera(camera, observation, cancel, *, sleep=time.sleep):
    if observation is None:
        raise ValueError("actual required local-camera mailbox unavailable")
    while True:
        cancel()
        if camera.poll() is not None:
            raise RuntimeError("required owned camera exited before physical host launch")
        if observation.evidence()["qualified"]:
            return
        sleep(0.05)


def prepare_left_response_p_request(config_path, *, ttl_seconds=120, workspace_profile=None,
                                    left_shoulder_startup_degrees=None, workload_segment="left-reach"):
    """Prepare one explicit boot-local LEFT-P32 workload request; never consume or Start it.

    This self-contained function is relayed by the PC CLI and imports the installed
    validators. It does not require deploying this CLI into the physical runtime.
    """
    import hashlib
    import os
    import time
    import uuid
    from pathlib import Path

    from lerobot.robots.alohamini.am1_elbow_integral import (
        AM1ElbowIntegralTrial,
        _left_response_p_boot_id,
        _left_response_p_request,
        _right_p20_file,
        _right_p20_json,
    )
    from tools.am1_camera_viewer import load_private
    from tools.am1_physical_executor import ProtectedHostRun
    from tools.am1_physical_reconciliation import (
        _publish_no_replace,
        acquire_stopped_guard,
    )
    from examples.alohamini.am1_live_teleop import CURRENT_REST_WORKSPACE_PROFILES

    if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 120:
        raise ValueError("Preparation expiry must be within 1–120 seconds")
    if type(workload_segment) is not str or workload_segment not in CURRENT_REST_WORKSPACE_PROFILES:
        raise ValueError("Only left-reach and explicit ordinary-arms preparation are supported")
    if workload_segment == "ordinary-arms" and workspace_profile is None:
        raise ValueError("Ordinary arms requires explicit current-rest preparation")
    if workspace_profile is not None and (type(workspace_profile) is not str
            or workspace_profile != CURRENT_REST_WORKSPACE_PROFILES[workload_segment]):
        raise ValueError("Only the explicit current-rest workspace profile is supported")
    if left_shoulder_startup_degrees is not None and (
        type(left_shoulder_startup_degrees) not in (int, float)
        or left_shoulder_startup_degrees != 1
        or workspace_profile != CURRENT_REST_WORKSPACE_PROFILES[workload_segment]
    ):
        raise ValueError("Left shoulder startup allowance requires exactly 1 degree and the current-rest workspace")
    path = Path(config_path)
    if not path.is_absolute() or path.resolve() != path or path.is_symlink():
        raise ValueError("Exact absolute installed physical configuration required")
    config = load_private(path)
    provider = ProtectedHostRun(config)
    provider._validate_source()
    state = Path(config["physical_state_directory"])
    AM1ElbowIntegralTrial._private_directory(state)
    guard = acquire_stopped_guard(config)
    temporary = None
    try:
        forbidden = [state / name for name in (
            "cleanup-uncertain.json", "physical-in-progress.json",
            "left-response-p-request.json", "right-response-p-request.json",
            "response12-right-p20-request.json")]
        if any(p.exists() or p.is_symlink() for p in forbidden):
            raise ValueError("Stopped known-clean state and no pending response request required")
        request = {
            "version": 1, "kind": "left-response-p", "request_id": str(uuid.uuid4()),
            "source_head": config["host_head"], "proportional": 32, "workload_segment": workload_segment,
            "boot_id": _left_response_p_boot_id(), "expires_at_monotonic_s": time.monotonic() + ttl_seconds,
        }
        if workspace_profile is not None:
            request["workspace_profile"] = workspace_profile
        if left_shoulder_startup_degrees is not None:
            request["left_shoulder_startup_degrees"] = left_shoulder_startup_degrees
        _left_response_p_request(request, config["host_head"])
        used = state / ("left-response-p-used-" + request["request_id"] + ".json")
        if used.exists() or used.is_symlink():
            raise ValueError("Fresh preparation nonce was already retired")
        raw = _right_p20_json(request)
        temporary = state / (".left-response-p-prepare-" + request["request_id"] + ".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if _right_p20_file(temporary)[1] != raw:
            raise ValueError("Canonical preparation readback differs")
        if (request["boot_id"] != _left_response_p_boot_id()
                or not 0 <= time.monotonic() < request["expires_at_monotonic_s"]):
            raise ValueError("Preparation boot or original expiry changed before publication")
        if any(p.exists() or p.is_symlink() for p in forbidden):
            raise ValueError("Physical state or pending request changed during preparation")
        pending = state / "left-response-p-request.json"
        _publish_no_replace(temporary, pending)
        AM1ElbowIntegralTrial._sync_directory(state)
        if _right_p20_file(pending)[1] != raw:
            raise ValueError("Published canonical preparation readback differs")
        return {"path": str(pending), "sha256": hashlib.sha256(raw).hexdigest(), "request": request,
                "canonical_read_verified": True, "hardware_started": False}
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
        guard.close()


def main(argv=None):
    """Administrative PC relay only; the persistent execution path is unchanged."""
    import argparse
    import inspect
    import re
    import shlex
    import subprocess
    from pathlib import PurePosixPath

    from tools.am1_session_ipc import strict_json

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--prepare-left-response-p", action="store_true", required=True,
                        help="Prepare one explicit temporary LEFT-P32 workload without starting hardware")
    parser.add_argument("--ssh", choices=("am1-pi",), required=True)
    parser.add_argument("--config", required=True, help="Existing installed Pi physical.json")
    parser.add_argument("--ttl-seconds", type=int, default=120)
    parser.add_argument("--left-response-workspace", choices=("current-rest",), default=None,
                        help="Explicitly select the approved LEFT1051..1164 corridor and ordinary measured-q startup")
    parser.add_argument("--left-response-workload", choices=("left-reach", "ordinary-arms"), default="left-reach",
                        help="Explicit target scope; ordinary-arms preserves only LEFT elbow P32 and zero body intent")
    parser.add_argument("--left-shoulder-startup-degrees", type=int, choices=(1,), default=None,
                        help="Explicit temporary 1-degree inward LEFT shoulder startup allowance for current-rest only")
    args = parser.parse_args(argv)
    if args.left_shoulder_startup_degrees is not None and args.left_response_workspace is None:
        parser.error("Left shoulder startup allowance requires --left-response-workspace current-rest")
    if args.left_response_workload == "ordinary-arms" and args.left_response_workspace is None:
        parser.error("Ordinary arms requires --left-response-workspace current-rest")
    if (not 1 <= args.ttl_seconds <= 120
            or re.fullmatch(r"/home/pickmanmike/\.local/share/am1-physical-adapter-01/"
                            r"deployments/[0-9a-f]{16}/physical\.json", args.config) is None
            or str(PurePosixPath(args.config)) != args.config):
        parser.error("Existing installed physical.json and preparation expiry within 1–120 seconds required")
    from examples.alohamini.am1_live_teleop import CURRENT_REST_WORKSPACE_PROFILES
    profile_args = [CURRENT_REST_WORKSPACE_PROFILES[args.left_response_workload]] if args.left_response_workspace is not None else []
    profile_keyword = ", workspace_profile=sys.argv[3]" if profile_args else ""
    startup_args = ["1"] if args.left_shoulder_startup_degrees is not None else []
    startup_keyword = ", left_shoulder_startup_degrees=int(sys.argv[4])" if startup_args else ""
    workload_args = ["ordinary-arms"] if args.left_response_workload == "ordinary-arms" else []
    workload_keyword = (", workload_segment=sys.argv[" + str(3 + len(profile_args) + len(startup_args)) + "]"
                        if workload_args else "")
    # Fixed reviewed code, no command/interpreter/source/nonce override. SSH's remote
    # shell receives only quoted fixed arguments, never interpolated source text.
    payload = (
        "import json, sys\n"
        "sys.path[:0] = ['/home/pickmanmike/am1_physical_trial', "
        "'/home/pickmanmike/lerobot_alohamini/src']\n"
        + inspect.getsource(prepare_left_response_p_request)
        + "\nprint(json.dumps(prepare_left_response_p_request(sys.argv[1], "
        "ttl_seconds=int(sys.argv[2])" + profile_keyword + startup_keyword + workload_keyword + "), sort_keys=True))\n"
    )
    remote = shlex.join(["/home/pickmanmike/lerobot_alohamini/.venv/bin/python", "-B", "-",
                         args.config, str(args.ttl_seconds), *profile_args, *startup_args, *workload_args])
    # An ambiguous SSH receipt is never retried automatically: the pending request
    # may already exist and must remain available for explicit inspection.
    reply = subprocess.run(
        ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", args.ssh, remote],
        input=payload, text=True, encoding="utf-8", capture_output=True, timeout=30, check=True, shell=False,
    )
    if len(reply.stdout) > 4096 or type(strict_json(reply.stdout)) is not dict:
        raise ValueError("Bounded preparation receipt unavailable; do not retry or overwrite pending authority")
    print(reply.stdout.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
