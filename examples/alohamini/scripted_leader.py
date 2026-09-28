"""Bounded AM1 input generator. No devices, transport, calibration, or feedback fabrication."""
from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Mapping
from typing import Any


class ScriptedLeaderInput:
    """One ArmSmoke cycle, advanced only by the owning admitted Local loop.

    get_action is deliberately pure. A delayed tick advances at most one nominal
    frame; pauses discard elapsed wall time, never the frozen origin or target.
    Feedback is evidence, not a new trajectory origin.
    """

    def __init__(
        self, initial_positions: Mapping[str, float], *, joint_keys: tuple[str, ...],
        fps: int, emit: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        if set(initial_positions) != set(joint_keys) or len(joint_keys) != 12:
            raise ValueError("scripted seed requires the exact AM1 arm-position key set")
        if fps != 10:
            raise ValueError("ArmSmoke requires the reviewed 10 Hz cadence")
        self.origin = {key: float(initial_positions[key]) for key in joint_keys}
        for key, value in self.origin.items():
            lower = 0.0 if key.endswith("gripper.pos") else -100.0
            if not math.isfinite(value) or not lower <= value <= 100.0:
                raise ValueError(f"scripted seed {key} is outside {lower}..100")
        self.joint_keys = joint_keys
        self.elapsed_s = 0.0
        self.duration_s = 2.0 + len(joint_keys) * 7.0 + 2.0
        self._frame_s = 1.0 / fps
        self._last_tick: float | None = None
        self._sequence = -1
        self._action = dict(self.origin)
        self._observed: dict[str, float] | None = None
        self._phase: tuple[int | None, str] | None = None
        self._finished = False
        self._pending_events: list[dict[str, Any]] = []
        self._emit = emit or (lambda record: print(json.dumps(record, sort_keys=True), flush=True))
        self.targets = {
            key: value + (3.0 if value <= 97.0 else -3.0)
            for key, value in self.origin.items()
        }
        for key in joint_keys:
            self._record("am1_scripted_segment_plan", joint=key, origin=self.origin[key],
                         target=self.targets[key], amplitude=self.targets[key] - self.origin[key],
                         ramp_s=3.0, endpoint_hold_s=0.5, reduced=False, skipped=False)
        self.flush_events()

    @property
    def complete(self) -> bool:
        return self.elapsed_s >= self.duration_s

    def _record(self, event: str, **fields: Any) -> None:
        self._pending_events.append({"event": event, "input_source": "scripted", "motion_profile": "ArmSmoke",
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
