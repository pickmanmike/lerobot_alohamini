"""Bounded AM1 input generator. No devices, transport, calibration, or feedback fabrication."""
from __future__ import annotations

import copy
import json
import math
import time
from collections.abc import Callable, Mapping
from typing import Any

if __package__:
    from .am1_session_contract import (
        ELBOW_RECOVERY_DEFINITIONS,
        LEFT_ELBOW_ORIGINAL_RETURN,
        LEFT_ELBOW_POSITIVE_CONTROL,
        LEFT_ELBOW_RESPONSE,
        SelectedElbowExperiment,
    )
else:
    from am1_session_contract import (
        ELBOW_RECOVERY_DEFINITIONS,
        LEFT_ELBOW_ORIGINAL_RETURN,
        LEFT_ELBOW_POSITIVE_CONTROL,
        LEFT_ELBOW_RESPONSE,
        SelectedElbowExperiment,
    )


class ScriptedLeaderInput:
    """One ArmSmoke cycle, advanced only by the owning admitted Local loop.

    get_action is deliberately pure. A delayed tick advances at most one nominal
    frame; pauses discard elapsed wall time, never the frozen origin or target.
    Feedback is evidence, not a new trajectory origin.
    """

    motion_profile = "ArmSmoke"
    hold_only = False
    selected_joint = None
    endpoint_hold_s = 0.5

    def __init__(
        self, initial_positions: Mapping[str, float], *, joint_keys: tuple[str, ...],
        fps: int, emit: Callable[[dict[str, Any]], None] | None = None,
        joint_amplitudes: Mapping[str, float] | None = None,
    ) -> None:
        if set(initial_positions) != set(joint_keys) or len(joint_keys) != 12:
            raise ValueError("scripted seed requires the exact AM1 arm-position key set")
        if fps != 10:
            raise ValueError("ArmSmoke requires the reviewed 10 Hz cadence")
        if self.hold_only and joint_amplitudes is not None:
            raise ValueError("hold-only input does not accept joint amplitude overrides")
        self.joint_amplitudes = dict.fromkeys(joint_keys, 3.0)
        if joint_amplitudes is not None:
            if not isinstance(joint_amplitudes, Mapping) or set(joint_amplitudes) - set(joint_keys):
                raise ValueError("joint amplitude overrides require known AM1 arm-position keys")
            for key, amplitude in joint_amplitudes.items():
                if isinstance(amplitude, bool):
                    raise ValueError("joint amplitude must be finite and greater than zero through 3")
                try:
                    amplitude = float(amplitude)
                except (TypeError, ValueError) as exc:
                    raise ValueError("joint amplitude must be finite and greater than zero through 3") from exc
                if not math.isfinite(amplitude) or not 0 < amplitude <= 3.0:
                    raise ValueError("joint amplitude must be finite and greater than zero through 3")
                self.joint_amplitudes[key] = amplitude
        self.origin = {key: float(initial_positions[key]) for key in joint_keys}
        for key, value in self.origin.items():
            lower = 0.0 if key.endswith("gripper.pos") else -100.0
            if not math.isfinite(value) or not lower <= value <= 100.0:
                raise ValueError(f"scripted seed {key} is outside {lower}..100")
        self.joint_keys = joint_keys
        self.elapsed_s = 0.0
        self.duration_s = 12.0 if self.hold_only else 2.0 + len(joint_keys) * 7.0 + 2.0
        if self.selected_joint is not None:
            self.duration_s = 4.0 + 2 * (6.0 + 2 * self.endpoint_hold_s)
        self._frame_s = 1.0 / fps
        self._last_tick: float | None = None
        self._sequence = -1
        self._action = dict(self.origin)
        self._observed: dict[str, float] | None = None
        self._phase: tuple[int | None, str] | None = None
        self._finished = False
        self._pending_events: list[dict[str, Any]] = []
        self._emit = emit or (lambda record: print(json.dumps(record, sort_keys=True), flush=True))
        self.targets = (
            dict(self.origin) if self.hold_only else {
                key: value + (self.joint_amplitudes[key] if value <= 97.0 else -self.joint_amplitudes[key])
                for key, value in self.origin.items()
            }
        )
        if self.selected_joint is not None:
            self.targets = dict(self.origin, **{self.selected_joint: self.targets[self.selected_joint]})
            self._selected_plan()
        elif self.hold_only:
            self._record("am1_scripted_hold_plan", duration_s=12.0, origin=dict(self.origin),
                         meaning="fixed measured-arm targets; normal owned body input allowed")
        else:
            for key in joint_keys:
                self._record("am1_scripted_segment_plan", joint=key, origin=self.origin[key],
                             target=self.targets[key], amplitude=self.targets[key] - self.origin[key],
                             ramp_s=3.0, endpoint_hold_s=0.5,
                             reduced=self.joint_amplitudes[key] < 3.0, skipped=False,
                             recipe="mapped_physical_excursion" if self.joint_amplitudes[key] < 3.0 else "original",
                             timing_changed=False)
        self.flush_events()

    def _selected_plan(self):
        self._record("am1_scripted_selected_plan", joint=self.selected_joint,
                     origin=self.origin[self.selected_joint], target=self.targets[self.selected_joint],
                     ramp_s=3.0, endpoint_hold_s=self.endpoint_hold_s, cycles=2,
                     baseline_s=2.0, final_s=2.0, duration_s=self.duration_s)

    @property
    def complete(self) -> bool:
        return self.elapsed_s >= self.duration_s

    def _record(self, event: str, **fields: Any) -> None:
        self._pending_events.append({"event": event, "input_source": "scripted", "motion_profile": self.motion_profile,
                                     "trajectory_s": round(self.elapsed_s, 6), "wall_time_ns": time.time_ns(), **fields})

    def flush_events(self) -> None:
        """The Local owner calls this outside all sender/transport locks."""
        while self._pending_events:
            self._emit(self._pending_events[0])
            self._pending_events.pop(0)

    def get_action(self) -> dict[str, float]:
        return {key.removeprefix("arm_"): value for key, value in self._action.items()}

    def admit(self, now: float) -> None:
        if not math.isfinite(now):
            raise ValueError("scripted clock must be finite")
        self._last_tick = now

    def freeze(self) -> None:
        self._last_tick = None

    def _position(self) -> tuple[int | None, str, float]:
        if self.hold_only:
            return None, "measured_hold", 0.0
        t = self.elapsed_s
        if t < 2.0:
            return None, "baseline", 0.0
        if t >= self.duration_s - 2.0:
            return None, "final_stationary", 0.0
        index = min(int((t - 2.0) // 7.0), len(self.joint_keys) - 1)
        within = t - 2.0 - index * 7.0
        if within < 3.0:
            return index, "outbound", within / 3.0
        if within < 3.5:
            return index, "endpoint", 1.0
        if within < 6.5:
            return index, "return", 1.0 - (within - 3.5) / 3.0
        return index, "origin_hold", 0.0

    def _record_observation(self, phase: tuple[int | None, str]) -> None:
        index, name = phase
        if index is None or self._observed is None:
            return
        key = self.joint_keys[index]
        self._record("am1_scripted_segment_observation", joint=key, phase=name,
                     observation_sequence=self._sequence, origin=self.origin[key],
                     requested=self._action[key], target=self.targets[key], observed=self._observed[key],
                     observed_displacement=self._observed[key] - self.origin[key],
                     tracking_error=self._action[key] - self._observed[key])

    def advance(
        self, now: float, observed: Mapping[str, float], observation_sequence: int, *, defer_events: bool = False,
    ) -> None:
        if self._last_tick is None or self.complete:
            return
        if not math.isfinite(now) or now < self._last_tick:
            raise ValueError("scripted clock regressed or is invalid")
        if observation_sequence <= self._sequence:
            raise ValueError("scripted feedback sequence did not advance")
        if set(observed) != set(self.origin) or not all(math.isfinite(v) for v in observed.values()):
            raise ValueError("scripted feedback is incomplete or invalid")
        self._sequence = observation_sequence
        self._observed = dict(observed)
        self.elapsed_s = min(self.duration_s, round(self.elapsed_s + min(now - self._last_tick, self._frame_s), 9))
        self._last_tick = now
        index, phase, fraction = self._position()
        if self._phase != (index, phase):
            if self._phase is not None:
                self._record_observation(self._phase)
            self._record("am1_scripted_phase", phase=phase,
                         joint=None if index is None else self.joint_keys[index])
            self._phase = (index, phase)
        self._action = dict(self.origin)
        if index is not None:
            key = self.joint_keys[index]
            self._action[key] += fraction * (self.targets[key] - self.origin[key])
        if not defer_events:
            self.flush_events()

    def finish(self, reason: str) -> None:
        if self._finished:
            return
        self._finished = True
        self.freeze()
        if self._phase is not None:
            self._record_observation(self._phase)
        self._record("am1_scripted_input_summary", stop_reason=reason,
                     profile_complete=self.complete, observation_sequence=self._sequence,
                     requested=dict(self._action), observed=self._observed)
        self.flush_events()


class ArmHoldBodyInput(ScriptedLeaderInput):
    """Explicit short body check using one frozen fresh follower-arm seed."""

    motion_profile = "ArmHoldBody"
    hold_only = True


class LeftElbowCompareInput(ScriptedLeaderInput):
    """Opt-in two-cycle elbow comparison with the original three-unit ramp."""

    motion_profile = "LeftElbowCompare"
    selected_joint = "arm_left_elbow_flex.pos"

    def __init__(self, initial_positions, *, joint_keys, fps, emit=None, joint_amplitudes=None):
        if joint_amplitudes is not None:
            raise ValueError("selected elbow comparison does not accept amplitude overrides")
        if self.selected_joint not in joint_keys:
            raise ValueError("selected elbow comparison requires the exact AM1 elbow key")
        super().__init__(initial_positions, joint_keys=joint_keys, fps=fps, emit=emit)

    def _selected_position(self):
        t = self.elapsed_s
        if t < 2.0:
            return 0, "baseline", 0.0
        if t >= self.duration_s - 2.0:
            return 2, "final", 0.0
        period = 6.0 + 2 * self.endpoint_hold_s
        cycle = min(int((t - 2.0) // period) + 1, 2)
        within = t - 2.0 - (cycle - 1) * period
        if within < 3.0:
            return cycle, "outbound", within / 3.0
        if within < 3.0 + self.endpoint_hold_s:
            return cycle, "endpoint", 1.0
        if within < 6.0 + self.endpoint_hold_s:
            return cycle, "return", 1.0 - (within - 3.0 - self.endpoint_hold_s) / 3.0
        return cycle, "origin", 0.0

    def _position(self):
        _, phase, fraction = self._selected_position()
        index = None if phase in {"baseline", "final"} else self.joint_keys.index(self.selected_joint)
        return index, phase, fraction

    @property
    def diagnostic_context(self):
        cycle, phase, _ = self._selected_position()
        sample_key = "baseline" if phase == "baseline" else (
            f"cycle{cycle}_{phase}" if phase in {"endpoint", "origin"} else None
        )
        return {
            "profile": self.motion_profile,
            "progress_s": self.elapsed_s,
            "phase": phase,
            "cycle": cycle,
            "reference": self.origin[self.selected_joint],
            "endpoint_target": self.targets[self.selected_joint],
            "requested_target": self._action[self.selected_joint],
            "sample_key": sample_key,
        }


class LeftElbowDwellCompareInput(LeftElbowCompareInput):
    """Separately selected two-second endpoint/origin dwell comparison."""

    motion_profile = "LeftElbowDwellCompare"
    endpoint_hold_s = 2.0


class LeftElbowResponseCharacterizeInput(ScriptedLeaderInput):
    """One measured signed decision; no motor access, reseeding or gain changes."""

    motion_profile = LEFT_ELBOW_RESPONSE.profile
    selected_joint = LEFT_ELBOW_RESPONSE.joint
    definition = LEFT_ELBOW_RESPONSE

    def _selected_plan(self):
        q0 = self.origin[self.selected_joint]
        if not -100 <= q0 - 3 <= q0 + 10 <= 100:
            raise ValueError("signed elbow endpoints exceed the original command range")
        self.duration_s = LEFT_ELBOW_RESPONSE.trajectory_s
        self.targets[self.selected_joint] = q0 - 3
        self._reference = q0
        self._initialize_selected_feedback()
        self._record("am1_scripted_selected_plan", joint=self.selected_joint, origin=q0,
                     signed_conditions=[-3.0, 10.0], slope_norm_per_s=1.0,
                     decision_s=16.0, completion_alternatives=[18.0, 60.0],
                     live_ceiling_s=180.0, other_joints="one frozen original seed", body="zero")

    def _initialize_selected_feedback(self):
        self._expanded = False
        self._branch = None
        self._identity = None
        self._motor = None
        self._baseline = []
        self._baseline_extrema = None
        self._baseline_raw = self._noise = None
        self._phase_samples = {}
        self._sparse = {}
        self._verified_receipts = {}
        self._last_cache_at = {"position": -1.0, "current": -1.0}
        self._current_binding = None
        self._adverse_reason = None
        self._final_residual = None
        self._collected_sequence = -1
        self._cycles = []

    @property
    def diagnostic_context(self):
        cycle, phase, fraction, delta = LEFT_ELBOW_RESPONSE.phase(self.elapsed_s, self._expanded)
        q0 = self.origin[self.selected_joint]
        return {
            "profile": self.motion_profile, "progress_s": self.elapsed_s, "phase": phase, "cycle": cycle,
            "reference": q0, "endpoint_target": q0 + delta,
            "requested_target": self._action[self.selected_joint],
            "sample_key": LEFT_ELBOW_RESPONSE.sample_key(self.elapsed_s, self._expanded),
        }

    def _position(self):
        _, phase, fraction, delta = LEFT_ELBOW_RESPONSE.phase(self.elapsed_s, self._expanded)
        self.targets[self.selected_joint] = self.origin[self.selected_joint] + delta
        index = None if phase in {"baseline", "final"} else self.joint_keys.index(self.selected_joint)
        return index, phase, fraction

    @property
    def characterization(self):
        return {
            "branch": self._branch, "baseline_raw": self._baseline_raw,
            "baseline_noise_raw": self._noise, "cycles": copy.deepcopy(self._cycles),
            "reference_normalized": self.origin[self.selected_joint],
            "calibration": None if self._motor is None else copy.deepcopy(self._motor["calibration"]),
            "final_return_residual_raw": self._final_residual,
        }

    @staticmethod
    def _number(value):
        return type(value) in (int, float) and math.isfinite(value)

    def collect_feedback(self, now, observed, observation_sequence, *, selected_feedback=None):
        if self._adverse_reason is not None:
            raise ValueError(self._adverse_reason)
        if type(observation_sequence) is not int or observation_sequence < self._collected_sequence:
            raise ValueError("signed native feedback sequence regressed")
        if observation_sequence == self._collected_sequence:
            return False
        self._feedback(now, observed, selected_feedback)
        self._collected_sequence = observation_sequence
        return True

    @property
    def awaiting_initial_feedback(self):
        return self._collected_sequence < 0 and self.elapsed_s == 0

    @staticmethod
    def _recent_suffix(samples, label, *, now=None):
        if len(samples) < 3 or samples[-1]["at"] - samples[0]["at"] < .2 - 1e-9:
            raise ValueError(f"signed {label} lacks three distinct actual acquisitions")
        if samples[-1]["position_at"] - samples[0]["position_at"] < .2 - 1e-9:
            raise ValueError(f"signed {label} position acquisition span is too short")
        for start in range(len(samples) - 3, -1, -1):
            suffix = samples[start:]
            if any(suffix[-1][k] - suffix[0][k] < .2 - 1e-9 for k in ("at", "position_at")):
                continue
            if now is not None and any(
                now - row[k] > .5 for row in suffix for k in ("current_started", "position_started")
            ):
                raise ValueError(f"signed {label} original acquisition is stale")
            return suffix
        raise ValueError(f"signed {label} acquisition window unavailable")

    def _rising_current_window(self, samples):
        native_continuity = getattr(self.definition, "intercycle_hold_admission_rule", None) is not None
        native_keys = ("native_observation_id", "native_epoch", "native_intent_revision")
        def original_marker(row):
            return (all(type(row.get(k)) is int and row[k] >= 0 for k in native_keys)
                    and all(self._number(row.get(k)) for k in ("at", "position_at")))
        for start, first in enumerate(samples):
            if native_continuity and not original_marker(first):
                continue
            chosen, raw = [first], [first["raw"]]
            previous = first
            for row in samples[start + 1:]:
                raw.append(row["raw"])
                if native_continuity and (
                    not original_marker(row)
                    or row["native_observation_id"] != previous["native_observation_id"] + 1
                    or any(row[k] != first[k] for k in ("native_epoch", "native_intent_revision"))
                    or any(row[k] <= previous[k] for k in ("at", "position_at"))
                ):
                    # A sparse client view cannot prove that current kept rising
                    # through omitted Native acquisitions. The sole motor owner's
                    # independent current/thermal/status guards remain active.
                    break
                if (
                    row["goal_raw"] != first["goal_raw"] or max(raw) - min(raw) > self._noise
                    or row["current_ma"] < previous["current_ma"]
                ):
                    break
                previous = row
                if row["current_ma"] - chosen[-1]["current_ma"] < 6.5 - 1e-9:
                    continue
                if len(chosen) < 3:
                    chosen.append(row)
                elif row["at"] - first["at"] >= .2 - 1e-9:
                    return [*chosen, row]
        return None

    def _feedback(self, now, observed, feedback):
        if not isinstance(feedback, dict) or feedback.get("version") != 1:
            raise ValueError("signed characterization requires actual selected feedback")
        context, action = feedback.get("context"), feedback.get("action")
        if not self.definition.context_valid(context) or not isinstance(action, dict):
            raise ValueError("signed selected phase/action proof is malformed")
        if (
            context["reference"] != self._reference
            or not 0 <= self.elapsed_s - context["progress_s"] <= .5 + 1e-9
        ):
            raise ValueError("signed selected feedback is ahead of or too far behind the commanded phase")
        identity = (feedback.get("run_id"), feedback.get("host_incarnation"))
        if not all(type(v) is str and v for v in identity):
            raise ValueError("signed selected feedback identity is missing")
        if self._identity is not None and identity != self._identity:
            raise ValueError("signed selected feedback identity changed")
        motor = feedback.get("motor")
        if not isinstance(motor, dict) or (
            motor.get("bus"), motor.get("name"), motor.get("id"), motor.get("model"), motor.get("normalization")
        ) != ("left", self.selected_joint[:-4], 3, "sts3215", "range_m100_100"):
            raise ValueError("signed selected feedback motor mapping differs")
        calibration = motor.get("calibration")
        if not isinstance(calibration, dict) or not all(
            type(calibration.get(k)) is int for k in ("drive_mode", "homing_offset", "range_min", "range_max")
        ) or not 0 <= calibration["range_min"] < calibration["range_max"] <= 4095 or calibration["drive_mode"] != 0:
            raise ValueError("signed selected calibration is invalid")
        if self._motor is not None and motor != self._motor:
            raise ValueError("signed selected calibration or motor mapping changed")
        self._identity, self._motor = identity, copy.deepcopy(motor)
        position, current = feedback.get("position"), feedback.get("current")
        for name, cache in (("position", position), ("current", current)):
            if not isinstance(cache, dict) or not all(self._number(cache.get(k)) for k in (
                "read_started_at_monotonic_s", "read_completed_at_monotonic_s"
            )) or not 0 <= cache["read_started_at_monotonic_s"] <= cache["read_completed_at_monotonic_s"] <= now:
                raise ValueError("signed selected acquisition clocks are unavailable")
            if now - cache["read_started_at_monotonic_s"] > .5:
                raise ValueError("signed selected acquisition is stale")
            if cache["read_completed_at_monotonic_s"] <= self._last_cache_at[name]:
                raise ValueError("signed selected acquisition did not advance")
        if type(position.get("raw")) is not int or not calibration["range_min"] <= position["raw"] <= calibration["range_max"]:
            raise ValueError("signed selected raw position exceeds calibration")
        if not self._number(position.get("normalized")) or not math.isclose(
            position["normalized"], observed[self.selected_joint], abs_tol=1e-9, rel_tol=0
        ) or not self._number(current.get("ma")):
            raise ValueError("signed selected position/current is unavailable")
        normalized = (position["raw"] - calibration["range_min"]) / (
            calibration["range_max"] - calibration["range_min"]
        ) * 200 - 100
        if not math.isclose(normalized, position["normalized"], abs_tol=1e-9, rel_tol=0):
            raise ValueError("signed selected raw and normalized feedback differ")
        requested = context["requested_target"]
        if not all(self._number(action.get(k)) and math.isclose(action[k], requested, rel_tol=0, abs_tol=1e-9)
                   for k in ("requested", "relative_limited", "current_limited")):
            raise ValueError("signed selected action was limited or differs from its command")
        if type(action.get("expected_goal_raw")) is not int or action.get("sync_write_returned") is not True:
            raise ValueError("signed selected actual encoded write attempt unavailable")
        expected = self.definition.encoded_goal(requested, calibration)
        if action["expected_goal_raw"] != expected:
            raise ValueError("signed selected actual encoder goal differs from its normalized command")
        final = (expected - calibration["range_min"]) / (
            calibration["range_max"] - calibration["range_min"]
        ) * 200 - 100
        if not self._number(action.get("final_normalized")) or not math.isclose(
            action["final_normalized"], final, abs_tol=1e-9, rel_tol=0
        ):
            raise ValueError("signed selected final normalized target differs from the actual encoded goal")
        if abs(current["ma"]) >= 1800:
            raise ValueError("signed selected current reached the existing joint hold bound")
        if isinstance(self.definition, SelectedElbowExperiment):
            magnitude = abs(self.definition.excursion_norm)
            low = self.definition.encoded_goal(self._reference-magnitude, calibration)
            high = self.definition.encoded_goal(self._reference+magnitude, calibration)
            if not low <= position["raw"] <= high:
                raise ValueError("signed selected position exceeds the absolute encoded experiment envelope")
        elif abs(observed[self.selected_joint] - self._reference) > 10:
            raise ValueError("signed selected position exceeds the absolute original envelope")
        for key in self.origin:
            if key != self.selected_joint and abs(observed[key] - self.origin[key]) > 1:
                raise ValueError("signed characterization held joint displaced")
        self._current_binding = {key: feedback.get(key) for key in (
            "epoch", "intent_revision", "action_sequence", "observation_id"
        )}
        if not all(type(v) is int and v >= 0 for v in self._current_binding.values()):
            raise ValueError("signed selected native ACK counters unavailable")
        for name, cache in (("position", position), ("current", current)):
            self._last_cache_at[name] = cache["read_completed_at_monotonic_s"]
        row = {
            "at": current["read_completed_at_monotonic_s"], "raw": position["raw"],
            "position_at": position["read_completed_at_monotonic_s"],
            "current_started": current["read_started_at_monotonic_s"],
            "position_started": position["read_started_at_monotonic_s"],
            "current_ma": abs(current["ma"]), "goal_raw": action["expected_goal_raw"],
        }
        if getattr(self.definition, "intercycle_hold_admission_rule", None) is not None:
            row.update(native_observation_id=feedback["observation_id"], native_epoch=feedback["epoch"],
                       native_intent_revision=feedback["intent_revision"])
        phase_key = (context["cycle"], context["phase"])
        samples = self._phase_samples.setdefault(phase_key, [])
        if samples and row["at"] <= samples[-1]["at"]:
            raise ValueError("signed selected current acquisition did not advance")
        samples.append(row)
        # Keep bounded actual windows, never synthesize acquisitions after a pause.
        del samples[:-20]
        if context["phase"] == "baseline":
            self._baseline.append(row)
            del self._baseline[:-20]
            if self._baseline_extrema is None:
                self._baseline_extrema = (row["raw"], row["raw"])
            else:
                self._baseline_extrema = (
                    min(self._baseline_extrema[0], row["raw"]),
                    max(self._baseline_extrema[1], row["raw"]),
                )
        receipts = feedback.get("sparse_receipts")
        if not isinstance(receipts, dict):
            raise ValueError("signed selected sparse phase evidence unavailable")
        self._sparse = copy.deepcopy(receipts)
        for key in self._sparse:
            self._receipt(key, now)
        if (context["phase"] == "endpoint" or (
            self.definition is LEFT_ELBOW_ORIGINAL_RETURN and context["phase"] == "origin"
        )) and len(samples) >= 4:
            last = self._rising_current_window(samples)
            if last is not None:
                self._adverse_reason = "signed endpoint has sustained rising current without measured response"
                self._record("am1_scripted_signed_adverse", reason=self._adverse_reason,
                             actual_current_window=copy.deepcopy(last))
                raise ValueError(self._adverse_reason)

    def _receipt(self, key, now, *, fresh=False):
        receipt = self._sparse.get(key)
        if not isinstance(receipt, dict) or receipt.get("diagnostic_available") is not True:
            raise ValueError("signed required sparse phase proof unavailable: " + key)
        if (receipt.get("run_id"), receipt.get("host_incarnation")) != self._identity:
            raise ValueError("signed sparse phase proof has foreign identity")
        recorded = self._verified_receipts.get(key)
        if recorded is not None and receipt != recorded:
            raise ValueError("signed retained sparse phase proof changed")
        counters = ("epoch", "intent_revision", "action_sequence", "observation_id")
        if not all(type(receipt.get(k)) is int and receipt[k] >= 0 for k in counters):
            raise ValueError("signed sparse native ACK counters unavailable")
        if recorded is None and (
            any(receipt[k] != self._current_binding[k] for k in ("epoch", "intent_revision"))
            or any(receipt[k] > self._current_binding[k] for k in ("action_sequence", "observation_id"))
        ):
            raise ValueError("signed sparse phase proof differs from its actual native ACK")
        if fresh and any(receipt[k] != self._current_binding[k] for k in ("epoch", "intent_revision")):
            raise ValueError("signed origin sparse proof belongs to an earlier control intent")
        context, action, decoded = receipt.get("context"), receipt.get("action"), receipt.get("decoded")
        if (
            not self.definition.context_valid(context) or context["sample_key"] != key
            or context["reference"] != self._reference
            or receipt.get("sample_key") != key or receipt.get("motor") != self._motor
            or not isinstance(action, dict) or not isinstance(decoded, dict)
            or receipt.get("goal_matches_expected_raw") is not True
            or type(decoded.get("goal_position_raw")) is not int
            or decoded["goal_position_raw"] != action.get("expected_goal_raw")
            or type(decoded.get("status")) is not int or decoded["status"] != 0
            or type(decoded.get("torque_enable")) is not int or decoded["torque_enable"] != 1
            or type(decoded.get("operating_mode")) is not int or decoded["operating_mode"] != 0
            or not all(self._number(decoded.get(k)) for k in (
                "present_current_ma", "present_load_raw", "voltage_v", "temperature_c", "present_position_raw"
            ))
        ):
            raise ValueError("signed sparse phase registers or command proof invalid: " + key)
        requested = context["requested_target"]
        if not all(self._number(action.get(k)) and math.isclose(action[k], requested, abs_tol=1e-9, rel_tol=0)
                   for k in ("requested", "relative_limited", "current_limited")) or (
            action["expected_goal_raw"] != self.definition.encoded_goal(requested, self._motor["calibration"])
        ):
            raise ValueError("signed sparse phase actual action differs from its declared encoded target")
        calibration = self._motor["calibration"]
        final = (action["expected_goal_raw"] - calibration["range_min"]) / (
            calibration["range_max"] - calibration["range_min"]
        ) * 200 - 100
        if not self._number(action.get("final_normalized")) or not math.isclose(
            action["final_normalized"], final, rel_tol=0, abs_tol=1e-9
        ):
            raise ValueError("signed sparse phase final target differs from actual encoder normalization")
        started, ended = receipt.get("read_started_at_monotonic_s"), receipt.get("read_completed_at_monotonic_s")
        if not self._number(started) or not self._number(ended) or not 0 <= started <= ended <= now:
            raise ValueError("signed sparse phase acquisition clock unavailable")
        acquired = receipt.get("acquired_at_monotonic_s")
        if not self._number(acquired) or not 0 <= acquired <= started:
            raise ValueError("signed sparse phase original observation clock unavailable")
        if fresh and now - started > .5:
            raise ValueError("signed origin sparse phase proof stale")
        if recorded is None:
            self._verified_receipts[key] = copy.deepcopy(receipt)
        return receipt

    def _qualify_baseline(self, now):
        samples = self._recent_suffix(self._baseline, "baseline", now=now)
        baseline = self._receipt("baseline", now)
        self._baseline_raw = baseline["action"]["expected_goal_raw"]
        self._noise = self._baseline_extrema[1] - self._baseline_extrema[0]
        if any(abs(v["raw"] - self._baseline_raw) > LEFT_ELBOW_RESPONSE.return_limit(self._noise)
               for v in samples):
            raise ValueError("signed baseline did not qualify the original reference")

    def _qualify_cycle(self, cycle, now):
        endpoint = self._receipt(f"cycle{cycle}_endpoint", now)
        self._receipt(f"cycle{cycle}_origin", now, fresh=True)
        samples = self._recent_suffix(self._phase_samples.get((cycle, "origin"), []), "returned", now=now)
        residual = max(abs(v["raw"] - self._baseline_raw) for v in samples)
        if residual > LEFT_ELBOW_RESPONSE.return_limit(self._noise):
            raise ValueError("signed cycle did not return to its original raw envelope")
        goal = endpoint["action"]["expected_goal_raw"]
        excursion = goal - self._baseline_raw
        if excursion == 0 or (excursion < 0) != (cycle <= 2):
            raise ValueError("signed endpoint actual encoder direction differs")
        rows = self._phase_samples.get((cycle, "endpoint"), [])
        self._recent_suffix(rows, "endpoint")
        signed = max((v["raw"] - self._baseline_raw) * (1 if excursion > 0 else -1) for v in rows)
        result = {
            "cycle": cycle, "endpoint_goal_raw": goal, "signed_response_raw": signed,
            "response_threshold_raw": LEFT_ELBOW_RESPONSE.response_threshold(excursion, self._noise),
            "return_residual_raw": residual, "return_qualified": True, "adverse": False,
        }
        self._cycles.append(result)
        self._record("am1_scripted_signed_cycle_qualified", **result)

    def _qualify_final(self, now):
        samples = self._recent_suffix(
            self._phase_samples.get((4 if self._expanded else 2, "final"), []), "final raw return", now=now
        )
        residual = max(abs(v["raw"] - self._baseline_raw) for v in samples)
        if residual > LEFT_ELBOW_RESPONSE.return_limit(self._noise):
            raise ValueError("signed final raw return exceeds the original measured envelope")
        self._final_residual = residual

    def validate_final_feedback(self, now, observed, selected_feedback):
        sequence = selected_feedback.get("observation_id") if isinstance(selected_feedback, dict) else None
        self.collect_feedback(now, observed, sequence, selected_feedback=selected_feedback)
        self._qualify_final(now)

    def advance(self, now, observed, observation_sequence, *, defer_events=False, selected_feedback=None):
        if self._last_tick is None or self.complete:
            return
        if self._adverse_reason is not None:
            raise ValueError(self._adverse_reason)
        if not math.isfinite(now) or now < self._last_tick or observation_sequence <= self._sequence:
            raise ValueError("signed clock/feedback sequence is invalid")
        self.collect_feedback(now, observed, observation_sequence, selected_feedback=selected_feedback)
        next_progress = round(self.elapsed_s + min(now - self._last_tick, self._frame_s), 9)
        if self.elapsed_s < 2 <= next_progress:
            self._qualify_baseline(now)
        for cycle, boundary in ((1, 9), (2, 16), (3, 37), (4, 58)):
            if self.elapsed_s < boundary <= next_progress:
                self._qualify_cycle(cycle, now)
                if cycle == 2:
                    useful = all(row["signed_response_raw"] >= row["response_threshold_raw"] for row in self._cycles)
                    self._expanded = not useful
                    self._branch = "negative_distinguished" if useful else "positive_control"
                    self.duration_s = 18.0 if useful else LEFT_ELBOW_RESPONSE.trajectory_s
                    self._record("am1_scripted_signed_branch", **self.characterization)
        if next_progress >= self.duration_s:
            self._qualify_final(now)
        super().advance(now, observed, observation_sequence, defer_events=defer_events)

    def freeze(self):
        super().freeze()
        for phase in ("origin", "endpoint"):
            cycle = self.diagnostic_context["cycle"]
            self._phase_samples.pop((cycle, phase), None)


class LeftElbowPositiveControlInput(LeftElbowResponseCharacterizeInput):
    """One baseline I0 out/back waveform; precision is a reported outcome."""

    motion_profile = LEFT_ELBOW_POSITIVE_CONTROL.profile
    selected_joint = LEFT_ELBOW_POSITIVE_CONTROL.joint
    definition = LEFT_ELBOW_POSITIVE_CONTROL

    def __init__(self, *args, positive_comparator=None, definition=None, **kwargs):
        if definition is not None:
            if not isinstance(definition, SelectedElbowExperiment):
                raise ValueError("selected experiment requires its exact validated definition")
            self.definition = definition
            self.motion_profile = definition.profile
        self._positive_comparator = copy.deepcopy(positive_comparator)
        super().__init__(*args, **kwargs)

    def _selected_plan(self):
        self._admission_reference = dict(self.origin)
        self._admitted_reference = self.origin[self.selected_joint]
        prior = self._positive_comparator
        self._composed = isinstance(self.definition, SelectedElbowExperiment) and self.definition.schema_version == 2
        if self._composed and prior is not None:
            raise ValueError("composed operation requires one fresh measured origin")
        self._reference = self._admitted_reference if prior is None else prior["reference_normalized"]
        self._acquisition_s = 0.0 if prior is None else 2.0
        if (not self._number(self._reference) or abs(self._reference - self._admitted_reference) > 2):
            raise ValueError("positive confirmation acquisition exceeds two units")
        self._excursion = getattr(self.definition, "excursion_norm", 10.0)
        self._raw_direction = 1 if self._excursion > 0 else -1
        magnitude = abs(self._excursion)
        if not -100 <= self._reference - magnitude <= self._reference + magnitude <= 100:
            raise ValueError("positive control full absolute corridor unavailable")
        if prior is not None:
            previous_seed = prior.get("original_seed")
            if not isinstance(previous_seed,dict) or set(previous_seed) != set(self.origin) or any(
                not self._number(v) or abs(self.origin[k]-v) > 1 for k,v in previous_seed.items() if k != self.selected_joint
            ):
                raise ValueError("positive confirmation held-joint admission differs from prior conditions")
            if __package__:
                from .am1_session_contract import uuid_text
            else:
                from am1_session_contract import uuid_text
            if uuid_text(prior["run_id"]) != prior["run_id"]:
                raise ValueError("positive confirmation prior identity invalid")
        self.origin[self.selected_joint] = self._reference
        self.targets = dict(self.origin, **{self.selected_joint:self._reference + self._excursion})
        self.duration_s = self.definition.nominal_trajectory_s + self._acquisition_s
        if isinstance(self.definition, SelectedElbowExperiment) and self.duration_s > self.definition.trajectory_s:
            raise ValueError("selected confirmation acquisition exceeds its finite trajectory envelope")
        self._initialize_selected_feedback()
        self._visible_settings = None
        self._positive_outcome = None
        self._coverage = {}
        self._cycle_coverage = {1: self._coverage, 2: {}} if self._composed else None
        self._cycle_outcomes = []
        self._intercycle_hold = None
        self._intercycle_anchor_proof = None
        self._hold_admission_rule = getattr(self.definition, "intercycle_hold_admission_rule", None)
        self._intercycle_admission_proof = None
        self._intercycle_admission_binding = None
        self._intercycle_last_native = self._intercycle_cursor_barrier = None
        self._intercycle_native_rows = []
        self._rejoin = None
        self._rejoin_targets = None
        self._rejoin_native_feedback = None
        self._measured_origin_raw = self._measured_origin_proof = self._admission_origin_proof = None
        self._baseline_anchor_proof = self._baseline_window = self._outward_anchor_raw = None
        self._baseline_closed = False
        self._endpoint_proofs=[]
        self._endpoint_window=self._endpoint_anchor_proof=self._endpoint_peak_proof=self._first_return_proof=None
        self._tracking = {}
        self._record("am1_scripted_positive_plan", reference=dict(self.origin),
                     admitted_reference=self._admitted_reference, acquisition_s=self._acquisition_s,
                     ramp_s=getattr(self.definition, "ramp_s", 10),
                     endpoint_hold_s=getattr(self.definition, "endpoint_dwell_s", 2), origin_hold_s=2, final_hold_s=2,
                     live_ceiling_s=self.definition.live_s, current_cap_ma=self.definition.current_cap_ma,
                     settings="read-only P16/I0/D32",
                     **({"experiment": self.definition.summary()} if isinstance(self.definition, SelectedElbowExperiment) else {}))

    @property
    def diagnostic_context(self):
        cycle, phase, _ = self.definition.phase(self.elapsed_s, self._acquisition_s)
        if self._rejoin is not None:
            phase = "rejoin"
        return {"profile":self.motion_profile, "progress_s":self.elapsed_s, "phase":phase, "cycle":cycle,
                "reference":self._reference, "endpoint_target":self._reference + self._excursion,
                "requested_target":self._action[self.selected_joint],
                "sample_key":None if self._rejoin is not None else self.definition.sample_key(self.elapsed_s, self._acquisition_s),
                "acquisition_s":self._acquisition_s, "admitted_reference":self._admitted_reference,
                **({"experiment_sha256":self.definition.sha256} if isinstance(self.definition, SelectedElbowExperiment) else {}),
                **({"rejoin":copy.deepcopy(self._rejoin)} if self._composed else {})}

    def prepare_rejoin(self, now, positions, observation_id, acquired_at):
        if (not self._composed or self.complete or self._last_tick is not None
                or not self._number(now) or not self._number(acquired_at) or not 0 <= now-acquired_at <= .5
                or type(observation_id) is not int or observation_id < 0
                or not isinstance(positions, dict) or set(positions) != set(self.origin)
                or not all(self._number(v) for v in positions.values())):
            raise ValueError("rejoin requires an original fresh measured hold acquisition")
        magnitude = abs(self._excursion)
        if (not self._reference-magnitude <= positions[self.selected_joint] <= self._reference+magnitude
                or any(abs(positions[k]-self.origin[k]) > 1 for k in positions if k != self.selected_joint)):
            raise ValueError("rejoin measured hold exceeds the original frozen envelope")
        _, _, fraction = self.definition.phase(self.elapsed_s, self._acquisition_s)
        retained = dict(self.origin, **{self.selected_joint:self._reference+self._excursion*fraction})
        duration = max(abs(retained[k]-positions[k]) for k in retained)
        self._rejoin_targets = (dict(positions), retained)
        self._rejoin = {"held_target":positions[self.selected_joint], "retained_target":retained[self.selected_joint],
            "elapsed_s":0.0, "duration_s":duration, "observation_id":observation_id,
            "acquired_at_monotonic_s":acquired_at}
        self._rejoin_native_feedback = None
        if self._hold_admission_rule is not None:
            self._clear_intercycle_admission()
        self._action = dict(positions)

    def _advance_rejoin(self, now):
        if self._last_tick is None or not self._number(now) or now < self._last_tick:
            raise ValueError("rejoin clock is unavailable or regressed")
        proof = self._rejoin_native_feedback
        if (proof is None or proof["context"]["rejoin"] != self._rejoin
                or proof["context"]["progress_s"] != self.elapsed_s):
            self._last_tick = now
            return  # A queued owner command is not an actual returned native write.
        if any(not 0 <= now-proof[k]["read_started_at_monotonic_s"] <= .5 for k in ("position", "current")):
            raise ValueError("rejoin native write proof is stale or regressed")
        # Native prepare precedes this returned bus operation. Pacing from the
        # original same-owner completion also bounds the next native prepare;
        # repeated writes never justify catch-up or imply a per-servo ACK.
        available = now-proof["cursor_write_completed_at_monotonic_s"]
        self._rejoin["elapsed_s"] = min(self._rejoin["duration_s"],
            self._rejoin["elapsed_s"]+min(now-self._last_tick,self._frame_s,available))
        self._last_tick = now
        duration = self._rejoin["duration_s"]
        fraction = 1.0 if duration == 0 else self._rejoin["elapsed_s"]/duration
        held, retained = self._rejoin_targets
        self._action = {k:held[k]+fraction*(retained[k]-held[k]) for k in held}

    def _position(self):
        _, phase, fraction = self.definition.phase(self.elapsed_s, self._acquisition_s)
        if phase == "acquire":
            requested = self._admitted_reference + (self._reference - self._admitted_reference) * self.elapsed_s / 2
            fraction = (requested - self._reference) / self._excursion
        return (None if phase in {"baseline", "origin", "final"} else self.joint_keys.index(self.selected_joint),
                phase, fraction)

    @property
    def positive_control(self):
        if self._positive_outcome is not None:
            return copy.deepcopy(self._positive_outcome)
        return {"program_completed":False, "coarse_functional_pass":False,
                "reference_normalized":self._reference, "admitted_reference":self._admitted_reference,
                "acquisition_s":self._acquisition_s,
                "confirmation_of":None if self._positive_comparator is None else self._positive_comparator["run_id"],
                **({"experiment":self.definition.summary()} if isinstance(self.definition, SelectedElbowExperiment) else {}),
                **({"cycles":copy.deepcopy(self._cycle_outcomes), "intercycle_hold":copy.deepcopy(self._intercycle_hold)}
                   if self._composed else {})}

    def _feedback(self, now, observed, feedback):
        current = feedback.get("current") if isinstance(feedback, dict) else None
        if not isinstance(current, dict) or not self._number(current.get("ma")) or abs(current["ma"]) >= self.definition.current_cap_ma:
            raise ValueError(f"positive control sampled current reached {self.definition.current_cap_ma:g} mA or is unavailable")
        proof = feedback.get("visible_control")
        if not self.definition.settings_valid(proof, feedback.get("run_id"), feedback.get("host_incarnation")):
            raise ValueError("positive control actual unchanged I0 settings proof unavailable")
        if self._visible_settings is not None and proof != self._visible_settings:
            raise ValueError("positive control immutable settings proof changed")
        context = feedback.get("context", {})
        if context.get("acquisition_s") != self._acquisition_s or context.get("admitted_reference") != self._admitted_reference:
            raise ValueError("positive control acquisition reference changed")
        if self._hold_admission_rule is not None:
            binding = tuple(feedback.get(k) for k in ("run_id", "host_incarnation", "epoch", "intent_revision"))
            if self._intercycle_admission_binding not in (None, binding):
                self._clear_intercycle_admission()
            self._intercycle_admission_binding = binding
        super()._feedback(now, observed, feedback)
        if self._hold_admission_rule is not None:
            self._intercycle_last_native = (feedback["acquired_at_monotonic_s"],feedback["action_sequence"])
        calibration = self._motor["calibration"]
        registers = proof["active"]["registers"]
        if (not 0 <= registers["Min_Position_Limit"] < registers["Max_Position_Limit"] <= 4095
                or self.definition.encoded_goal(self._reference-abs(self._excursion),calibration) < registers["Min_Position_Limit"]
                or self.definition.encoded_goal(self._reference+abs(self._excursion),calibration) > registers["Max_Position_Limit"]
                or registers["Homing_Offset"] != calibration["homing_offset"]
                or self._positive_comparator is not None and calibration != self._positive_comparator["calibration"]):
            raise ValueError("positive control settings/calibration or comparison differs")
        encoded_delta = (self.definition.encoded_goal(self._reference+self._excursion,calibration)
                         - self.definition.encoded_goal(self._reference,calibration))
        self._raw_direction = 1 if encoded_delta > 0 else -1
        if isinstance(self.definition, SelectedElbowExperiment) and not self.definition.criteria_valid(abs(encoded_delta)):
            raise ValueError("selected experiment raw criteria differ from its actual encoded excursion")
        self._visible_settings = copy.deepcopy(proof)
        if context.get("phase") == "rejoin":
            action = feedback["action"]
            started, completed = (action.get(k) for k in
                ("write_started_at_monotonic_s", "write_completed_at_monotonic_s"))
            previous = self._rejoin_native_feedback
            if (not self._number(started) or not self._number(completed)
                    or not 0 <= started <= completed <= min(feedback[k]["read_started_at_monotonic_s"]
                        for k in ("position", "current"))
                    or previous is not None and completed < previous["action"]["write_completed_at_monotonic_s"]):
                raise ValueError("rejoin actual native write clocks are unavailable or regressed")
            self._rejoin_native_feedback = {k:copy.deepcopy(feedback[k])
                for k in ("context", "action", "position", "current")}
            self._rejoin_native_feedback["cursor_write_completed_at_monotonic_s"] = (
                previous["cursor_write_completed_at_monotonic_s"]
                if previous is not None and previous["context"] == context else completed)
            return  # Real bridge acquisitions do not count as programme phase coverage.
        if self._measured_origin_raw is None:
            if self.elapsed_s != 0 or context["progress_s"] != 0:
                raise ValueError("positive original measured reference lacks its first native acquisition")
            measured=self.definition.measured_origin(self._reference,calibration)
            admitted=self.definition.measured_origin(self._admitted_reference,calibration)
            native=self._measurement_proof(feedback,now)
            if measured is None or admitted is None or feedback["position"]["raw"] not in {admitted,self.definition.encoded_goal(self._admitted_reference,calibration)}:
                raise ValueError("positive first native acquisition differs from frozen measured reference")
            self._measured_origin_raw=measured
            if self._positive_comparator is None:
                self._measured_origin_proof=native
            else:
                prior=self._positive_comparator
                if prior.get("measured_origin_raw") != measured or not self.definition.measurement_proof_valid(prior.get("measured_origin_proof"),self._reference,calibration,run_id=prior["run_id"]):
                    raise ValueError("positive confirmation original measured provenance differs")
                self._measured_origin_proof=copy.deepcopy(prior["measured_origin_proof"])
                self._admission_origin_proof=native
        phase, cycle = context["phase"], context["cycle"]
        row = self._phase_samples[(cycle,phase)][-1]
        if self._composed:
            self._coverage = self._cycle_coverage[2 if phase == "intercycle_hold" else max(1, cycle)]
        cov = self._coverage.setdefault(phase, {"sample_count":0, "first_current_at":row["at"],
            "first_position_at":row["position_at"], "minimum_raw":row["raw"], "maximum_raw":row["raw"]})
        cov.update(sample_count=cov["sample_count"]+1, last_current_at=row["at"], last_position_at=row["position_at"],
                   minimum_raw=min(cov["minimum_raw"],row["raw"]), maximum_raw=max(cov["maximum_raw"],row["raw"]))
        if phase=="baseline":
            if self._baseline_closed:
                raise ValueError("positive baseline phase regressed after departure")
            self._baseline_anchor_proof=self._measurement_proof(feedback,now)
            if self._baseline_raw is not None:
                self._qualify_positive_baseline(now)
        elif phase=="outbound":
            self._baseline_closed=True
        if self._composed and phase == "intercycle_hold":
            self._intercycle_anchor_proof = self._measurement_proof(feedback, now)
            if self._hold_admission_rule is not None:
                self._intercycle_admission_proof = copy.deepcopy(feedback.get("intercycle_hold_admission"))
                self._intercycle_native_rows.append({"sample":copy.deepcopy(row), "native_row":[
                    feedback["observation_id"], feedback["action_sequence"], context["progress_s"],
                    feedback["position"]["raw"], feedback["position"]["read_started_at_monotonic_s"],
                    feedback["position"]["read_completed_at_monotonic_s"], feedback["current"]["raw"],
                    feedback["current"]["read_started_at_monotonic_s"],
                    feedback["current"]["read_completed_at_monotonic_s"], feedback["acquired_at_monotonic_s"],
                    feedback["action"].get("write_started_at_monotonic_s"),
                    feedback["action"].get("write_completed_at_monotonic_s"),
                ]})
                del self._intercycle_native_rows[:-self._hold_admission_rule["maximum_samples"]]
        if phase=="endpoint":
            self._endpoint_proofs.append(self._measurement_proof(feedback,now))
            del self._endpoint_proofs[:-20]
        if phase=="return" and self._first_return_proof is None:
            endpoint=self._phase_samples.get((cycle,"endpoint"),[])
            suffix=self._recent_suffix(endpoint,"positive endpoint before actual return",now=now)
            if not self._endpoint_proofs or self._endpoint_proofs[-1]["position"]["read_completed_at_monotonic_s"] != suffix[-1]["position_at"]:
                raise ValueError("positive actual endpoint packet/window differs")
            self._endpoint_window=self._window(suffix,now)
            self._endpoint_anchor_proof=copy.deepcopy(self._endpoint_proofs[-1])
            self._endpoint_peak_proof=copy.deepcopy(max(self._endpoint_proofs,key=lambda proof:self._raw_direction*proof["position"]["raw"]))
            self._first_return_proof=self._measurement_proof(feedback,now)
        band = max(2, self._noise or 0)
        tracking_key = (cycle, phase) if self._composed else phase
        old = self._tracking.get(tracking_key)
        if old is not None:
            if phase == "outbound" and self._raw_direction*(row["raw"]-old["extreme"]) < -band:
                raise ValueError("positive control moved in the wrong outward direction")
            if phase == "return" and self._raw_direction*(row["raw"]-old["extreme"]) > band:
                raise ValueError("positive control moved in the wrong return direction")
        error = row["raw"] - row["goal_raw"]
        track = old or {"extreme":row["raw"], "goal":row["goal_raw"], "sign":0, "crossings":0, "error":0}
        track["extreme"] = (max(track["extreme"], row["raw"])
                            if (phase == "outbound") == (self._raw_direction > 0)
                            else min(track["extreme"], row["raw"]))
        sign = 0 if abs(error) <= band else (1 if error > 0 else -1)
        if track["goal"] == row["goal_raw"] and sign and track["sign"] and sign != track["sign"] and abs(error) > track["error"]:
            track["crossings"] += 1
            if track["crossings"] >= 3:
                raise ValueError("positive control has growing instability at an unchanged goal")
        track.update(goal=row["goal_raw"], sign=sign, error=abs(error))
        self._tracking[tracking_key] = track
        if phase in {"origin", "final"} and self._rising_current_window(self._phase_samples[(cycle,phase)]) is not None:
            self._adverse_reason = "positive control sustained rising current at an unchanged goal"
            raise ValueError(self._adverse_reason)

    def _measurement_proof(self,feedback,now):
        keys = ("version","run_id","host_incarnation","epoch","intent_revision","action_sequence","observation_id",
                "acquired_at_monotonic_s","context","motor","position","action")
        if self._hold_admission_rule is not None:
            keys += ("current",)
        return {"captured_at_monotonic_s":now,**{k:copy.deepcopy(feedback[k]) for k in keys}}

    def _clear_intercycle_admission(self):
        # Keep the frozen reference and its original measured provenance.
        # A new owner binding cannot relabel an old first-write cursor/window.
        if self._intercycle_last_native is not None:
            self._intercycle_cursor_barrier = self._intercycle_last_native
        self._intercycle_admission_proof = self._intercycle_anchor_proof = None
        self._intercycle_admission_binding = None
        self._intercycle_native_rows.clear()

    def _receipt(self,key,now,*,fresh=False):
        receipt = super()._receipt(key,now,fresh=fresh)
        if abs(receipt["decoded"]["present_current_ma"]) >= self.definition.current_cap_ma:
            raise ValueError(f"positive control sparse sampled current reached {self.definition.current_cap_ma:g} mA")
        return receipt

    @staticmethod
    def _window(samples, now):
        return {"sample_count":len(samples), "qualified_at_monotonic_s":now,
                "current_span_s":samples[-1]["at"]-samples[0]["at"],
                "position_span_s":samples[-1]["position_at"]-samples[0]["position_at"],
                **{f"{end}_{name}_{part}_at":row[key]
                   for end,row in (("first",samples[0]),("last",samples[-1]))
                   for name,part,key in (("current","started","current_started"),("current","completed","at"),
                                         ("position","started","position_started"),("position","completed","position_at"))}}

    def _qualify_positive_baseline(self, now):
        receipt = self._receipt("baseline", now)
        samples = self._recent_suffix(self._baseline, "positive baseline", now=now)
        self._baseline_raw = receipt["action"]["expected_goal_raw"]
        if self._baseline_raw != self.definition.encoded_goal(self._reference, self._motor["calibration"]):
            raise ValueError("positive control baseline reference differs")
        self._noise = self._baseline_extrema[1] - self._baseline_extrema[0]
        self._outward_anchor_raw=samples[-1]["raw"]
        self._baseline_window=self._window(samples,now)
        if max(abs(r["raw"] - self._baseline_raw) for r in samples) > max(2,self._noise):
            raise ValueError("positive control declared reference is not acquired")

    def _qualify_positive_final(self, now):
        cycle = 2 if self._composed else 1
        self._qualify_positive_cycle(now, cycle, "final")
        if self._composed:
            final = self._cycle_outcomes[-1]
            self._positive_outcome = dict(copy.deepcopy(final), program_completed=True,
                coarse_functional_pass=all(c["coarse_functional_pass"] for c in self._cycle_outcomes),
                fine_return_pass=all(c["fine_return_pass"] for c in self._cycle_outcomes),
                outward_response_raw=min(c["outward_response_raw"] for c in self._cycle_outcomes),
                reverse_response_raw=min(c["reverse_response_raw"] for c in self._cycle_outcomes),
                final_return_residual_raw=max(c["final_return_residual_raw"] for c in self._cycle_outcomes),
                cycles=copy.deepcopy(self._cycle_outcomes), intercycle_hold=copy.deepcopy(self._intercycle_hold))

    def _qualify_positive_cycle(self, now, cycle, final_phase):
        origin = self._receipt(f"cycle{cycle}_origin", now, fresh=True)
        samples = self._recent_suffix(self._phase_samples.get((cycle,final_phase),[]), "positive final", now=now)
        if self._first_return_proof is None or self._endpoint_window is None:
            raise ValueError("positive actual reverse lacks its qualified endpoint window")
        required = ("baseline", "outbound", "endpoint", "return", "origin", "final")
        if self._composed:
            required = (("baseline", "outbound", "endpoint", "return", "origin") if cycle == 1 else
                        ("intercycle_hold", "outbound", "endpoint", "return", "origin", "final"))
        for phase in required:
            c = self._coverage.get(phase, {})
            if (c.get("sample_count",0) < 3 or c.get("last_current_at",0)-c.get("first_current_at",0) < .2-1e-9
                    or c.get("last_position_at",0)-c.get("first_position_at",0) < .2-1e-9):
                raise ValueError("positive control actual phase coverage missing: " + phase)
        ep = self._receipt(f"cycle{cycle}_endpoint", now)
        endpoint_raw = ep["decoded"]["goal_position_raw"]
        if origin["decoded"]["goal_position_raw"] != self._baseline_raw:
            raise ValueError("positive control final encoded goal differs")
        excursion = abs(endpoint_raw - self._baseline_raw)
        if excursion <= 0:
            raise ValueError("positive control encoded excursion unavailable")
        anchor = (self._endpoint_peak_proof if self._composed else self._endpoint_anchor_proof)["position"]["raw"]
        outward = max(0, self._raw_direction*(self._endpoint_peak_proof["position"]["raw"] - self._outward_anchor_raw))
        reverse = max(0, max(self._raw_direction*(anchor-r["raw"]) for r in samples))
        residual = max(abs(r["raw"]-self._measured_origin_raw) for r in samples)
        residual = max(residual, abs(origin["decoded"]["present_position_raw"]-self._measured_origin_raw))
        goal_residual=max(max(abs(r["raw"]-self._baseline_raw) for r in samples),abs(origin["decoded"]["present_position_raw"]-self._baseline_raw))
        discriminating = self.definition.noise_discriminating(excursion,self._noise)
        minimum = getattr(self.definition, "minimum_response_raw", math.ceil(excursion/2))
        tolerance = getattr(self.definition, "return_tolerance_raw", math.floor(excursion/5))
        coarse = discriminating and outward >= minimum and reverse >= minimum and residual <= tolerance
        outcome = {"program_completed":True, "coarse_functional_pass":coarse,
            "fine_return_pass":residual <= 2, "noise_discriminating":discriminating,
            "reference_normalized":self._reference, "admitted_reference":self._admitted_reference,
            "reference_raw":self._baseline_raw, "endpoint_raw":endpoint_raw, "encoded_excursion_raw":excursion,
            "outward_response_raw":outward, "reverse_response_raw":reverse, "reverse_anchor_raw":anchor,
            "final_return_residual_raw":residual, "baseline_noise_raw":self._noise,
            "measured_origin_raw":self._measured_origin_raw,"encoded_goal_residual_raw":goal_residual,
            "measured_origin_proof":copy.deepcopy(self._measured_origin_proof),
            "admission_origin_proof":copy.deepcopy(self._admission_origin_proof),
            "outward_anchor_raw":self._outward_anchor_raw,
            "baseline_anchor_proof":copy.deepcopy(self._baseline_anchor_proof),"baseline_window":copy.deepcopy(self._baseline_window),
            "endpoint_anchor_proof":copy.deepcopy(self._endpoint_anchor_proof),
            "endpoint_peak_proof":copy.deepcopy(self._endpoint_peak_proof),
            "first_return_proof":copy.deepcopy(self._first_return_proof),"endpoint_window":copy.deepcopy(self._endpoint_window),
            "calibration":copy.deepcopy(self._motor["calibration"]), "adverse":False,
            "stationary":max(r["raw"] for r in samples)-min(r["raw"] for r in samples) <= 2,
            "acquisition_s":self._acquisition_s,
            "confirmation_of":None if self._positive_comparator is None else self._positive_comparator["run_id"],
            "admission_reference":copy.deepcopy(self._admission_reference),
            "comparison_reference":None if self._positive_comparator is None else copy.deepcopy(self._positive_comparator["original_seed"]),
            "other_joint_admission_differences":{} if self._positive_comparator is None else {
                k:self._admission_reference[k]-v for k,v in self._positive_comparator["original_seed"].items() if k != self.selected_joint},
            "visible_control":copy.deepcopy(self._visible_settings), "phase_coverage":copy.deepcopy(self._coverage),
            "phase_receipts":copy.deepcopy({k:v for k,v in self._sparse.items()
                if not self._composed or k in {"baseline", f"cycle{cycle}_endpoint", f"cycle{cycle}_origin"}}),
            "final_window":self._window(samples,now),
            **({"experiment":self.definition.summary()} if isinstance(self.definition, SelectedElbowExperiment) else {})}
        if self._composed:
            outcome.pop("program_completed")
            outcome.update(cycle=cycle, cycle_completed=True, starting_raw=self._outward_anchor_raw)
            if len(self._cycle_outcomes) == cycle:
                self._cycle_outcomes[-1] = outcome
            elif len(self._cycle_outcomes) == cycle - 1:
                self._cycle_outcomes.append(outcome)
            else:
                raise ValueError("composed cycle qualification order differs")
        else:
            self._positive_outcome = outcome

    def _qualify_intercycle_hold(self, now):
        proof = self._intercycle_anchor_proof
        admission, rule = self._intercycle_admission_proof, self._hold_admission_rule
        if rule is None:
            samples = self._recent_suffix(self._phase_samples.get((1,"intercycle_hold"), []),
                                          "measured intercycle hold", now=now)
            if (proof is None or proof["position"]["read_completed_at_monotonic_s"] != samples[-1]["position_at"]
                    or max(abs(r["raw"]-self._measured_origin_raw) for r in samples) > self.definition.return_tolerance_raw):
                raise ValueError("measured intercycle hold differs from the original return envelope")
        else:
            if not self.definition.intercycle_hold_admission_valid(
                    admission, proof, self._measured_origin_proof, now=now):
                raise ValueError("measured intercycle hold Native admission proof is unavailable or differs")
            if self._intercycle_cursor_barrier is not None:
                acquired, sequence = self._intercycle_cursor_barrier
                cursor = admission["hold_cursor"]
                if (cursor["write_completed_at_monotonic_s"] <= acquired
                        or cursor["action_sequence"] <= sequence):
                    raise ValueError("measured intercycle hold Native cursor predates the cleared owner binding")
            native = {row[0]:row for row in admission["rows"]}
            first, last = admission["rows"][0][0], admission["rows"][-1][0]
            actual = [row for row in self._intercycle_native_rows if first <= row["native_row"][0] <= last]
            # IPC may skip original Native rows; every consumed row in this
            # interval must still match, including the full latest acquisition.
            if (not actual or actual[-1]["native_row"] != admission["rows"][-1]
                    or any(native.get(row["native_row"][0]) != row["native_row"] for row in actual)):
                raise ValueError("measured intercycle hold Native rows differ from actual provider feedback")
            # Native paired acquisitions are the qualified window. The owner may
            # consume only a subset at its independent frame rate.
            samples = [{"raw": row[3], "position_started": row[4], "position_at": row[5],
                        "current_started": row[7], "at": row[8]} for row in admission["rows"]]
            if (max(abs(row["raw"]-self._measured_origin_raw) for row in samples) > rule["tolerance_raw"]
                    or max(row["raw"] for row in samples)-min(row["raw"] for row in samples) > rule["spread_raw"]):
                raise ValueError("measured intercycle hold exceeds its stable admission envelope")
        qualified_at = now if rule is None else admission["qualified_at_monotonic_s"]
        self._intercycle_hold = {"window":self._window(samples, qualified_at), "anchor_proof":copy.deepcopy(proof),
                                "starting_raw":samples[-1]["raw"], "reference_raw":self._baseline_raw}
        if rule is not None:
            self._intercycle_hold["admission_proof"] = copy.deepcopy(admission)
        self._baseline_anchor_proof = copy.deepcopy(proof)
        self._baseline_window = self._window(samples, qualified_at)
        self._outward_anchor_raw = samples[-1]["raw"]
        self._coverage = self._cycle_coverage[2]
        self._endpoint_proofs = []
        self._endpoint_window = self._endpoint_anchor_proof = self._endpoint_peak_proof = self._first_return_proof = None

    def advance(self, now, observed, observation_sequence, *, defer_events=False, selected_feedback=None):
        if self._last_tick is None or self.complete:
            return
        if not math.isfinite(now) or now < self._last_tick or observation_sequence <= self._sequence:
            raise ValueError("positive control clock/feedback sequence invalid")
        self.collect_feedback(now, observed, observation_sequence, selected_feedback=selected_feedback)
        if self._rejoin is not None:
            context = selected_feedback.get("context", {})
            if (context.get("phase") == "rejoin" and context.get("rejoin") == self._rejoin
                    and self._rejoin["elapsed_s"] == self._rejoin["duration_s"]):
                self._rejoin = self._rejoin_targets = None
                self._rejoin_native_feedback = None
                self._last_tick = now
            else:
                self._advance_rejoin(now)
            return
        next_progress = round(self.elapsed_s + min(now-self._last_tick,self._frame_s),9)
        if self.elapsed_s < self._acquisition_s+2 <= next_progress:
            self._qualify_positive_baseline(now)
        if self._composed:
            end = 2 + 2*self.definition.ramp_s + self.definition.endpoint_dwell_s + 2
            if self.elapsed_s < end <= next_progress:
                self._qualify_positive_cycle(now, 1, "origin")
            if self.elapsed_s < end+self.definition.intercycle_hold_s <= next_progress:
                self._qualify_intercycle_hold(now)
        if next_progress >= self.duration_s:
            self._qualify_positive_final(now)
        ScriptedLeaderInput.advance(self, now, observed, observation_sequence, defer_events=defer_events)

    def validate_final_feedback(self, now, observed, selected_feedback):
        # FiniteTask may already have collected this same original acquisition.
        # Re-ACK keeps the frozen waveform and requires fresh bounded final proof.
        sequence = selected_feedback.get("observation_id") if isinstance(selected_feedback,dict) else None
        self.collect_feedback(now,observed,sequence,selected_feedback=selected_feedback)
        self._qualify_positive_final(now)

    def freeze(self):
        ScriptedLeaderInput.freeze(self)
        self._rejoin_native_feedback = None
        if self._hold_admission_rule is not None:
            self._clear_intercycle_admission()
        self._tracking.clear()
        cycle,phase,_ = self.definition.phase(self.elapsed_s,self._acquisition_s)
        self._phase_samples.pop((cycle,phase),None)
        if phase == "baseline":
            self._baseline.clear()
        if phase == "endpoint" and self._first_return_proof is None:
            self._endpoint_proofs.clear()


class LeftElbowOriginalReturnInput(LeftElbowResponseCharacterizeInput):
    """One slower approach from stopped rest to a preserved original target."""

    motion_profile = LEFT_ELBOW_ORIGINAL_RETURN.profile
    definition = LEFT_ELBOW_ORIGINAL_RETURN

    def __init__(self, *args, return_reference, reference_sha256, **kwargs):
        self._retained = copy.deepcopy(self.definition.validate_artifact(return_reference))
        self._reference_sha256 = reference_sha256
        super().__init__(*args, **kwargs)

    def _selected_plan(self):
        self._reference = self._retained["reference_normalized"]
        self._initialize_selected_feedback()
        self._baseline_raw = self._retained["reference_raw"]
        self._noise = self._retained["baseline_noise_raw"]
        self._final_window = None
        self._origin_goal = self._origin_present = None
        self.duration_s = self.definition.trajectory_s
        start = self.definition.encoded_goal(self.origin[self.selected_joint], self._retained["calibration"])
        if start is None or abs(start - self._retained["stopped_position_raw"]) > 2 or not 0 < self._reference - self.origin[self.selected_joint] <= 3:
            raise ValueError("return-only starting pose differs from the archived stopped rest")
        self.targets[self.selected_joint] = self._reference
        self._record("am1_scripted_original_return_plan", original_run_id=self._retained["original_run_id"],
                     artifact_sha256=self._reference_sha256, starting_raw=start, reference_raw=self._baseline_raw,
                     ramp_s=3.0, hold_s=.5, trajectory_s=5.5, live_ceiling_s=60,
                     other_joints="fresh measured targets held exactly", body="zero", gain_trial=False)

    @property
    def diagnostic_context(self):
        cycle, phase, fraction = self.definition.phase(self.elapsed_s)
        start = self.origin[self.selected_joint]
        return {
            "profile": self.motion_profile, "progress_s": self.elapsed_s, "phase": phase, "cycle": cycle,
            "reference": self._reference, "endpoint_target": start,
            "requested_target": start + fraction * (self._reference - start),
            "sample_key": self.definition.sample_key(self.elapsed_s),
        }

    def _position(self):
        _, phase, fraction = self.definition.phase(self.elapsed_s)
        return (None if phase == "baseline" else self.joint_keys.index(self.selected_joint)), phase, fraction

    @property
    def retained_return(self):
        return {
            "artifact_sha256": self._reference_sha256, "original_run_id": self._retained["original_run_id"],
            "reference_raw": self._baseline_raw, "reference_normalized": self._reference,
            "baseline_noise_raw": self._retained["baseline_noise_raw"],
            "calibration": copy.deepcopy(self._retained["calibration"]),
            "starting_raw": self.definition.encoded_goal(self.origin[self.selected_joint], self._retained["calibration"]),
            "final_return_residual_raw": self._final_residual,
            "final_window": copy.deepcopy(self._final_window),
            "origin_goal_raw": self._origin_goal, "origin_present_raw": self._origin_present,
        }

    def _feedback(self, now, observed, feedback):
        super()._feedback(now, observed, feedback)
        if self._motor["calibration"] != self._retained["calibration"]:
            raise ValueError("return-only live calibration differs from the retained original reference")
        raw = feedback["position"]["raw"]
        if not self._retained["stopped_position_raw"] - 2 <= raw <= self._baseline_raw + 2:
            raise ValueError("return-only actual position left its bounded approach envelope")

    def _qualify_start(self, now):
        samples = self._recent_suffix(self._baseline, "return-only baseline", now=now)
        receipt = self._receipt("baseline", now)
        expected = self.definition.encoded_goal(self.origin[self.selected_joint], self._retained["calibration"])
        if receipt["action"]["expected_goal_raw"] != expected or any(
            abs(row["raw"] - self._retained["stopped_position_raw"]) > 2 for row in samples
        ):
            raise ValueError("return-only fresh baseline differs from the archived stopped rest")
        self._noise = self._baseline_extrema[1] - self._baseline_extrema[0]

    def _qualify_final(self, now):
        origin = self._receipt("cycle1_origin", now, fresh=True)
        samples = self._recent_suffix(self._phase_samples.get((1, "origin"), []), "original return", now=now)
        residual = max(abs(row["raw"] - self._baseline_raw) for row in samples)
        decoded = origin["decoded"]
        if residual > 2 or decoded["goal_position_raw"] != self._baseline_raw or (
            type(decoded["present_position_raw"]) is not int or abs(decoded["present_position_raw"] - self._baseline_raw) > 2
        ):
            raise ValueError("return-only did not reach its original raw envelope")
        self._final_residual = residual
        self._origin_goal, self._origin_present = decoded["goal_position_raw"], decoded["present_position_raw"]
        self._final_window = {
            "sample_count": len(samples), "qualified_at_monotonic_s": now,
            "current_span_s": samples[-1]["at"] - samples[0]["at"],
            "position_span_s": samples[-1]["position_at"] - samples[0]["position_at"],
            **{f"{end}_{name}_{part}_at": row[key]
               for end, row in (("first", samples[0]), ("last", samples[-1]))
               for name, part, key in (("current", "started", "current_started"), ("current", "completed", "at"),
                                       ("position", "started", "position_started"), ("position", "completed", "position_at"))},
        }

    def advance(self, now, observed, observation_sequence, *, defer_events=False, selected_feedback=None):
        if self._last_tick is None or self.complete:
            return
        self.collect_feedback(now, observed, observation_sequence, selected_feedback=selected_feedback)
        next_progress = round(self.elapsed_s + min(now - self._last_tick, self._frame_s), 9)
        if self.elapsed_s < 2 <= next_progress:
            self._qualify_start(now)
        if next_progress >= self.duration_s:
            self._qualify_final(now)
        ScriptedLeaderInput.advance(self, now, observed, observation_sequence, defer_events=defer_events)


class LeftElbowRecoveryInput(LeftElbowOriginalReturnInput):
    """One explicit bounded recovery workload, with independent actual gain proof."""

    def __init__(self, *args, definition, recovery_comparator=None, **kwargs):
        if not any(definition is item for item in ELBOW_RECOVERY_DEFINITIONS.values()):
            raise ValueError("an exact frozen recovery definition is required")
        self.definition, self.motion_profile = definition, definition.profile
        self._recovery_comparator = copy.deepcopy(recovery_comparator)
        super().__init__(*args, **kwargs)

    def _selected_plan(self):
        self._initialize_selected_feedback()
        self._starting_raw = self.definition.encoded_goal(
            self.origin[self.selected_joint], self._retained["calibration"]
        )
        if self._starting_raw is None:
            raise ValueError("recovery measured start is unrepresentable")
        self._reference = (
            self.origin[self.selected_joint]
            if self.definition.mode == "confirm"
            else self._retained["reference_normalized"]
        )
        self._baseline_raw = (
            self._starting_raw if self.definition.mode == "confirm" else self._retained["reference_raw"]
        )
        if self.definition.mode == "confirm":
            if not -100 <= self._reference < self._reference + 3 <= 100:
                raise ValueError("recovery confirmation corridor is unrepresentable")
            self.targets[self.selected_joint] = self._reference + 3
        else:
            if not 0 < self._reference - self.origin[self.selected_joint] <= 3:
                raise ValueError("recovery return would change direction or exceed its corridor")
            self.targets[self.selected_joint] = self._reference
        self._noise = 0
        self._final_window = None
        self._origin_goal = self._origin_present = None
        self._target_attained = False
        self._max_toward = 0
        self._trend = None
        self._error_sign = self._error_peak = self._growing_crossings = 0
        self._error_goal = None
        self._gain_trial = None
        self._validate_comparator()
        self.duration_s = self.definition.trajectory_s
        self._record(
            "am1_scripted_recovery_plan",
            recipe=self.definition.recipe_name,
            starting_raw=self._starting_raw,
            historical_target_raw=self._retained["reference_raw"],
            reference_raw=self._baseline_raw,
            expected_integral=self.definition.integral,
            current_cap_ma=self.definition.current_cap_ma,
            hold_s=self.definition.hold_s,
            trajectory_s=self.duration_s,
            live_ceiling_s=self.definition.live_s,
            other_joints="fresh measured targets held exactly",
            body="zero",
        )

    @property
    def diagnostic_context(self):
        cycle, phase, fraction = self.definition.phase(self.elapsed_s)
        start = self.origin[self.selected_joint]
        endpoint = self._reference + 3 if self.definition.mode == "confirm" else start
        requested = (
            self._reference + fraction * 3
            if self.definition.mode == "confirm"
            else start + fraction * (self._reference - start)
        )
        return {
            "profile": self.motion_profile,
            "progress_s": self.elapsed_s,
            "phase": phase,
            "cycle": cycle,
            "reference": self._reference,
            "endpoint_target": endpoint,
            "requested_target": requested,
            "sample_key": self.definition.sample_key(self.elapsed_s),
        }

    def _position(self):
        _, phase, fraction = self.definition.phase(self.elapsed_s)
        return (
            (None if phase in {"baseline", "final"} else self.joint_keys.index(self.selected_joint)),
            phase,
            fraction,
        )

    @property
    def recovery(self):
        return {
            "recipe": self.definition.recipe_name,
            "profile": self.motion_profile,
            "mode": self.definition.mode,
            "expected_integral": self.definition.integral,
            "hold_s": self.definition.hold_s,
            "current_cap_ma": self.definition.current_cap_ma,
            "artifact_sha256": self._reference_sha256,
            "original_run_id": self._retained["original_run_id"],
            "historical_target_raw": self._retained["reference_raw"],
            "reference_raw": self._baseline_raw,
            "reference_normalized": self._reference,
            "calibration": copy.deepcopy(self._retained["calibration"]),
            "starting_raw": self._starting_raw,
            "baseline_noise_raw": self._noise,
            "max_toward_target_raw": self._max_toward,
            "target_attained": self._target_attained,
            "final_return_residual_raw": self._final_residual,
            "final_window": copy.deepcopy(self._final_window),
            "origin_goal_raw": self._origin_goal,
            "origin_present_raw": self._origin_present,
            "cycles": copy.deepcopy(self._cycles),
            "gain_trial": copy.deepcopy(self._gain_trial),
            "comparator": copy.deepcopy(self._recovery_comparator),
        }

    def _validate_comparator(self):
        value = self._recovery_comparator
        if self.definition.integral != 1 or self.definition.mode != "return":
            if value is not None:
                raise ValueError("recovery comparator is only valid for an I1 return workload")
            return
        required = {
            "run_id",
            "host_incarnation",
            "proof_sha256",
            "initial_measured_reference",
            "calibration",
            "trajectory_s",
            "hold_s",
            "integral",
        }
        if not isinstance(value, dict) or set(value) != required:
            raise ValueError("recovery I1 requires an exact matched I0 comparator")
        proof = value["proof_sha256"]
        baseline = value["initial_measured_reference"]
        if (
            not all(type(value[k]) is str and value[k] for k in ("run_id", "host_incarnation"))
            or type(proof) is not str
            or len(proof) != 64
            or any(c not in "0123456789abcdef" for c in proof)
            or type(value["integral"]) is not int
            or value["integral"] != 0
            or value["calibration"] != self._retained["calibration"]
            or value["trajectory_s"] != self.definition.trajectory_s
            or value["hold_s"] != self.definition.hold_s
            or not isinstance(baseline, dict)
            or set(baseline) != set(self.origin)
            or not all(self._number(v) for v in baseline.values())
        ):
            raise ValueError("recovery comparator source, settings, timing or vector differs")
        baseline_raw = self.definition.encoded_goal(
            baseline[self.selected_joint], self._retained["calibration"]
        )
        if (
            baseline_raw is None
            or abs(baseline_raw - self._starting_raw) > 2
            or any(abs(baseline[k] - v) > 1 for k, v in self.origin.items() if k != self.selected_joint)
        ):
            raise ValueError("recovery comparator initial pose differs; no repositioning is permitted")

    def _rising_current_window(self, samples):
        # Only this explicit I1 workload records a sub-cap rise as hypothesis evidence.
        return None if self.definition.integral == 1 else super()._rising_current_window(samples)

    def _feedback(self, now, observed, feedback):
        current = feedback.get("current") if isinstance(feedback, dict) else None
        if (
            isinstance(current, dict)
            and self._number(current.get("ma"))
            and abs(current["ma"]) >= self.definition.current_cap_ma
        ):
            self._adverse_reason = "recovery selected current reached the 100 mA experimental cap"
            raise ValueError(self._adverse_reason)
        LeftElbowResponseCharacterizeInput._feedback(self, now, observed, feedback)
        endpoint = (
            self._reference + 3 if self.definition.mode == "confirm" else self.origin[self.selected_joint]
        )
        if not math.isclose(feedback["context"]["endpoint_target"], endpoint, abs_tol=1e-9, rel_tol=0):
            raise ValueError("recovery phase proof differs from its frozen measured start/endpoint")
        if self._motor["calibration"] != self._retained["calibration"]:
            raise ValueError("recovery live calibration differs from its archived provenance")
        gain = feedback.get("gain_trial")
        if not self.definition.gain_trial_valid(
            gain, run_id=feedback["run_id"], host_incarnation=feedback["host_incarnation"], now=now
        ):
            raise ValueError("recovery actual gain proof differs from its selected owner/setting")
        if self._gain_trial is not None and self._gain_trial != gain:
            raise ValueError("recovery actual gain proof changed during its workload")
        registers = gain["active_registers"]
        if (registers["Min_Position_Limit"], registers["Max_Position_Limit"]) != (
            self._retained["calibration"]["range_min"],
            self._retained["calibration"]["range_max"],
        ):
            raise ValueError("recovery actual gain proof calibration range differs")
        self._gain_trial = copy.deepcopy(gain)
        raw, context = feedback["position"]["raw"], feedback["context"]
        limit = self.definition.return_limit(self._noise) if self.definition.mode == "confirm" else 2
        upper = self.definition.encoded_goal(self.targets[self.selected_joint], self._retained["calibration"])
        if not self._starting_raw - limit <= raw <= upper + limit:
            raise ValueError("recovery wrong-direction response left its bounded approach envelope")
        self._max_toward = max(self._max_toward, raw - self._starting_raw)
        phase_key = context["cycle"], context["phase"]
        direction = -1 if self.definition.mode == "confirm" and context["phase"] == "return" else 1
        if (
            self._trend is not None
            and self._trend[:2] == phase_key
            and context["phase"] in {"return", "outbound"}
            and (raw - self._trend[2]) * direction < -max(2, self._noise)
        ):
            raise ValueError("recovery measured motion reversed the commanded direction")
        self._trend = *phase_key, raw
        goal = feedback["action"]["expected_goal_raw"]
        if goal != self._error_goal:
            self._error_goal = goal
            self._error_sign = self._error_peak = self._growing_crossings = 0
        error = raw - goal
        sign = 1 if error > 0 else -1
        if abs(error) > max(2, self._noise):
            if self._error_sign and sign != self._error_sign and abs(error) > self._error_peak:
                self._growing_crossings += 1
            self._error_sign, self._error_peak = sign, abs(error)
            if self._growing_crossings >= 3:
                raise ValueError("recovery growing oscillation crossed its target three times")
        samples = self._phase_samples[phase_key]
        if self.definition.integral == 0 and context["phase"] == "origin" and len(samples) >= 4:
            adverse = self._rising_current_window(samples)
            if adverse is not None:
                self._adverse_reason = (
                    "recovery endpoint has sustained rising current without measured response"
                )
                self._record(
                    "am1_scripted_recovery_adverse",
                    reason=self._adverse_reason,
                    actual_current_window=adverse,
                )
                raise ValueError(self._adverse_reason)

    def _qualify_start(self, now):
        samples = self._recent_suffix(self._baseline, "recovery measured baseline", now=now)
        receipt = self._receipt("baseline", now)
        if receipt["action"]["expected_goal_raw"] != self._starting_raw or any(
            abs(row["raw"] - self._starting_raw) > 2 for row in samples
        ):
            raise ValueError("recovery fresh measured baseline differs from its initial hold")
        self._noise = self._baseline_extrema[1] - self._baseline_extrema[0]

    def _qualify_recovery_cycle(self, cycle, now):
        endpoint = self._receipt(f"cycle{cycle}_endpoint", now)
        origin = self._receipt(f"cycle{cycle}_origin", now, fresh=True)
        returned = self._recent_suffix(
            self._phase_samples.get((cycle, "origin"), []), "recovery returned", now=now
        )
        endpoint_rows = self._recent_suffix(
            self._phase_samples.get((cycle, "endpoint"), []), "recovery endpoint"
        )
        residual = max(abs(row["raw"] - self._baseline_raw) for row in returned)
        goal = endpoint["action"]["expected_goal_raw"]
        threshold = LEFT_ELBOW_RESPONSE.response_threshold(goal - self._baseline_raw, self._noise)
        response = max(row["raw"] - self._baseline_raw for row in endpoint_rows)
        if (
            residual > self.definition.return_limit(self._noise)
            or origin["decoded"]["goal_position_raw"] != self._baseline_raw
            or abs(origin["decoded"]["present_position_raw"] - self._baseline_raw)
            > self.definition.return_limit(self._noise)
        ):
            raise ValueError("recovery confirmation did not return before another departure")
        if response < threshold:
            raise ValueError("recovery confirmation lacks useful measured movement")
        self._cycles.append(
            {
                "cycle": cycle,
                "endpoint_goal_raw": goal,
                "signed_response_raw": response,
                "response_threshold_raw": threshold,
                "return_residual_raw": residual,
                "return_qualified": True,
            }
        )

    def _qualify_final(self, now):
        confirm = self.definition.mode == "confirm"
        origin = self._receipt("final_origin" if confirm else "cycle1_origin", now, fresh=True)
        samples = self._recent_suffix(
            self._phase_samples.get((2, "final") if confirm else (1, "origin"), []),
            "recovery final position",
            now=now,
        )
        decoded = origin["decoded"]
        residual = max(abs(row["raw"] - self._baseline_raw) for row in samples)
        if (
            decoded["goal_position_raw"] != self._baseline_raw
            or type(decoded["present_position_raw"]) is not int
        ):
            raise ValueError("recovery original goal/present proof differs")
        limit = self.definition.return_limit(self._noise) if confirm else 2
        attained = residual <= limit and abs(decoded["present_position_raw"] - self._baseline_raw) <= limit
        if confirm and not attained:
            raise ValueError("recovery confirmation final return left its original envelope")
        self._final_residual, self._target_attained = residual, attained
        self._origin_goal, self._origin_present = (
            decoded["goal_position_raw"],
            decoded["present_position_raw"],
        )
        self._final_window = {
            "sample_count": len(samples),
            "qualified_at_monotonic_s": now,
            "current_span_s": samples[-1]["at"] - samples[0]["at"],
            "position_span_s": samples[-1]["position_at"] - samples[0]["position_at"],
            **{
                f"{end}_{name}_{part}_at": row[key]
                for end, row in (("first", samples[0]), ("last", samples[-1]))
                for name, part, key in (
                    ("current", "started", "current_started"),
                    ("current", "completed", "at"),
                    ("position", "started", "position_started"),
                    ("position", "completed", "position_at"),
                )
            },
        }

    def advance(self, now, observed, observation_sequence, *, defer_events=False, selected_feedback=None):
        if self._last_tick is None or self.complete:
            return
        if not math.isfinite(now) or now < self._last_tick or observation_sequence <= self._sequence:
            raise ValueError("recovery clock/feedback sequence is invalid")
        self.collect_feedback(now, observed, observation_sequence, selected_feedback=selected_feedback)
        next_progress = round(self.elapsed_s + min(now - self._last_tick, self._frame_s), 9)
        if self.elapsed_s < 2 <= next_progress:
            self._qualify_start(now)
        if self.definition.mode == "confirm":
            for cycle, boundary in ((1, 9), (2, 16)):
                if self.elapsed_s < boundary <= next_progress:
                    self._qualify_recovery_cycle(cycle, now)
        if next_progress >= self.duration_s:
            self._qualify_final(now)
        ScriptedLeaderInput.advance(self, now, observed, observation_sequence, defer_events=defer_events)
