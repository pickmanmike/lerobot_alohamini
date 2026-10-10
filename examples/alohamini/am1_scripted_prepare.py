"""Opt-in finite same-owner preparation before one original scripted seed."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Mapping
from typing import Any

SHOULDER = "arm_left_shoulder_lift.pos"


class PreparedScriptedInput:
    """Hold, qualify the representable boundary, move inward, then seed once.

    The native owner supplies only admitted, fresh follower observations. This
    provider has no motor or transport access. Its 20-second wall budget survives
    freezes; its separate preparation clock never catches up after delayed ticks.
    """

    def __init__(
        self,
        initial_positions: Mapping[str, float],
        *,
        joint_keys: tuple[str, ...],
        fps: int,
        provider: Callable[..., Any],
        motion_profile: str | None = None,
        emit: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        if fps != 10 or len(joint_keys) != 12 or set(initial_positions) != set(joint_keys):
            raise ValueError("preparation requires the exact AM1 12-joint seed at 10 Hz")
        self.motion_profile = motion_profile or getattr(provider, "motion_profile", None)
        if self.motion_profile not in {"ArmSmoke", "ArmSmokeRepeat", "ArmHoldBody"}:
            raise ValueError("preparation supports only ArmSmoke, ArmSmokeRepeat and ArmHoldBody")
        self.origin = {key: float(initial_positions[key]) for key in joint_keys}
        if SHOULDER not in self.origin:
            raise ValueError("preparation requires the selected left shoulder-lift")
        for key, value in self.origin.items():
            lower = 0.0 if key.endswith("gripper.pos") else -100.0
            margin = 20.0 if key == SHOULDER else 0.0
            if not math.isfinite(value) or not lower - margin <= value <= 100 + margin:
                raise ValueError(f"preparation seed {key} is outside its qualified initial range")
        self.joint_keys, self._fps, self._provider = joint_keys, fps, provider
        self.duration_s = {"ArmSmoke": 88.0, "ArmSmokeRepeat": 352.0, "ArmHoldBody": 12.0}[
            self.motion_profile
        ]
        self.preparation_elapsed_s = 0.0
        self._program = None
        self._action = dict(self.origin)
        self._boundary = min(100.0, max(-100.0, self.origin[SHOULDER]))
        self._inward = (
            99.0
            if self.origin[SHOULDER] >= 99
            else (-99.0 if self.origin[SHOULDER] <= -99 else self.origin[SHOULDER])
        )
        self._action[SHOULDER] = self._boundary
        self._initial_error = max(1.0, abs(self.origin[SHOULDER] - self._boundary))
        self._phase = "boundary"
        self._phase_progress = 0.0
        self._started_at = self._last_tick = None
        self._last_now = None
        self._sequence = -1
        self._observed = None
        self._qualified = []
        self._qualification_started = None
        self._last_large_error_sign = 0
        self._oscillations = 0
        self._finished = False
        self._pending_events = []
        self._emit = emit or (lambda row: print(json.dumps(row, sort_keys=True), flush=True))
        self._record(
            "am1_scripted_preparation_plan",
            initial_observed=dict(self.origin),
            boundary_target=self._boundary,
            inward_target=self._inward,
            maximum_initial_gap=20.0,
            boundary_convergence_deadline_s=8.0,
            wall_deadline_s=20.0,
            slew_norm_per_s=0.75,
            target_error_limit=1.0,
            final_qualified_hold_s=2.0,
            meaning="initial boundary gap is explicit; original program is not seeded yet",
        )
        self.flush_events()

    @property
    def preparing(self) -> bool:
        return self._program is None

    @property
    def elapsed_s(self) -> float:
        return 0.0 if self.preparing else self._program.elapsed_s

    @property
    def complete(self) -> bool:
        return False if self.preparing else self._program.complete

    def _record(self, event: str, **fields: Any) -> None:
        self._pending_events.append(
            {
                "event": event,
                "input_source": "scripted",
                "motion_profile": self.motion_profile,
                "trajectory_s": round(self.elapsed_s, 6),
                "wall_time_ns": time.time_ns(),
                "preparation_s": round(self.preparation_elapsed_s, 6),
                "preparation_wall_s": None if self._started_at is None else self._last_now - self._started_at,
                **fields,
            }
        )

    def flush_events(self) -> None:
        while self._pending_events:
            self._emit(self._pending_events[0])
            self._pending_events.pop(0)

    def get_action(self) -> dict[str, float]:
        if not self.preparing:
            return self._program.get_action()
        return {key.removeprefix("arm_"): value for key, value in self._action.items()}

    def _deadline(self, now: float) -> None:
        self._last_now = now  # processing time of this check, never feedback acquisition time
        if self._started_at is not None and now - self._started_at >= 20:
            raise ValueError("20-second preparation wall deadline expired without a qualified seed")

    def admit(self, now: float) -> None:
        if self._finished or self.complete:
            return
        if not math.isfinite(now):
            raise ValueError("preparation clock must be finite")
        if not self.preparing:
            self._program.admit(now)
            return
        if self._last_now is not None and now < self._last_now:
            raise ValueError("preparation clock regressed across admission")
        self._deadline(now)
        if self._started_at is None:
            self._started_at = now
        self._last_tick = self._last_now = now

    def freeze(self) -> None:
        self._last_tick = None
        self._qualified.clear()
        self._qualification_started = None
        if not self.preparing:
            self._program.freeze()

    def _stationary(self, observed: Mapping[str, float], *, final: bool) -> bool:
        valid = all(abs(observed[key] - self._action[key]) <= 1 for key in self.joint_keys)
        if final:
            valid = valid and all(
                (0 if key.endswith("gripper.pos") else -100) <= observed[key] <= 100
                for key in self.joint_keys
            )
        if not valid:
            self._qualified.clear()
            self._qualification_started = None
            return False
        self._qualified.append(dict(observed))
        if any(
            max(row[key] for row in self._qualified) - min(row[key] for row in self._qualified) > 1
            for key in self.joint_keys
        ):
            self._qualified = [dict(observed)]
            self._qualification_started = self.preparation_elapsed_s
        if self._qualification_started is None:
            self._qualification_started = self.preparation_elapsed_s
        span = self.preparation_elapsed_s - self._qualification_started
        return len(self._qualified) >= 3 and span >= (2.0 if final else 0.2) - 1e-9

    def advance(
        self,
        now: float,
        observed: Mapping[str, float],
        observation_sequence: int,
        *,
        defer_events: bool = False,
    ) -> None:
        if self._finished or self.complete:
            return
        if not self.preparing:
            self._program.advance(now, observed, observation_sequence, defer_events=True)
            self._program.flush_events()
            if not defer_events:
                self.flush_events()
            return
        if self._last_tick is None:
            return
        if not math.isfinite(now) or now < self._last_tick:
            raise ValueError("preparation clock regressed or is invalid")
        self._deadline(now)
        if observation_sequence <= self._sequence:
            raise ValueError("preparation feedback sequence did not advance")
        if set(observed) != set(self.origin) or not all(math.isfinite(value) for value in observed.values()):
            raise ValueError("preparation feedback is incomplete or invalid")
        self.preparation_elapsed_s = round(self.preparation_elapsed_s + min(now - self._last_tick, 0.1), 9)
        self._last_tick = self._last_now = now
        self._sequence, self._observed = observation_sequence, dict(observed)
        error = observed[SHOULDER] - self._action[SHOULDER]
        if abs(error) > self._initial_error + 1:
            raise ValueError(f"preparation target error grew beyond the finite trial envelope: {error}")
        if abs(error) > 1:
            sign = 1 if error > 0 else -1
            if self._last_large_error_sign and sign != self._last_large_error_sign:
                self._oscillations += 1
            self._last_large_error_sign = sign
            if self._oscillations >= 3:
                raise ValueError(
                    "preparation oscillation crossed the target three times outside the 1-unit band"
                )
        if self._phase == "boundary":
            if self._stationary(observed, final=False):
                self._record(
                    "am1_scripted_preparation_boundary_qualified",
                    observation_sequence=observation_sequence,
                    observed=dict(observed),
                    target=dict(self._action),
                )
                self._phase = "inward"
                self._phase_progress = self.preparation_elapsed_s
                self._qualified.clear()
                self._qualification_started = None
            elif now - self._started_at >= 8:
                raise ValueError("preparation boundary failed to converge within 8 seconds")
        elif self._phase == "inward":
            distance = self._inward - self._boundary
            step = min(abs(distance), 0.75 * (self.preparation_elapsed_s - self._phase_progress))
            self._action[SHOULDER] = self._boundary + (step if distance >= 0 else -step)
            if step >= abs(distance):
                self._phase = "settle"
                self._qualified.clear()
                self._qualification_started = None
        elif self._stationary(observed, final=True):
            seed = dict(observed)
            self._record(
                "am1_scripted_preparation_complete",
                observation_sequence=observation_sequence,
                observed=seed,
                target=dict(self._action),
                original_program_duration_s=self.duration_s,
            )
            self._record(
                "am1_scripted_seed",
                observation_sequence=observation_sequence,
                origin=seed,
                prepared_at=now,
                meaning="one fresh frozen follower seed after finite preparation",
            )
            self._program = self._provider(
                seed, joint_keys=self.joint_keys, fps=self._fps, emit=self._pending_events.append
            )
            if self._program.duration_s != self.duration_s:
                raise ValueError("prepared program duration does not match its original profile")
            self.origin = dict(self._program.origin)
            self._program.admit(now)
        if not defer_events:
            self.flush_events()

    def finish(self, reason: str) -> None:
        if self._finished:
            return
        self._finished = True
        self.freeze()
        self._record(
            "am1_scripted_preparation_summary",
            stop_reason=reason,
            preparation_complete=not self.preparing,
            observation_sequence=self._sequence,
            observed=self._observed,
        )
        if self.preparing:
            self._record(
                "am1_scripted_input_summary",
                stop_reason=reason,
                profile_complete=False,
                observation_sequence=self._sequence,
                requested=dict(self._action),
                observed=self._observed,
            )
        else:
            self._program.finish(reason)
        self.flush_events()
