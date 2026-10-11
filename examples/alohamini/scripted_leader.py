"""Bounded AM1 input generator. No devices, transport, calibration, or feedback fabrication."""
from __future__ import annotations

import copy
import json
import math
import time
from collections.abc import Callable, Mapping
from typing import Any

if __package__:
    from .am1_session_contract import LEFT_ELBOW_ORIGINAL_RETURN, LEFT_ELBOW_RESPONSE
else:
    from am1_session_contract import LEFT_ELBOW_ORIGINAL_RETURN, LEFT_ELBOW_RESPONSE


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
        for start, first in enumerate(samples):
            chosen, raw = [first], [first["raw"]]
            previous = first
            for row in samples[start + 1:]:
                raw.append(row["raw"])
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
        if abs(observed[self.selected_joint] - self._reference) > 10:
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
