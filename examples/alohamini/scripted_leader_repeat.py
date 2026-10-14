"""Opt-in finite composition of the reviewed ArmSmoke trajectory."""
from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Mapping
from typing import Any

from scripted_leader import ScriptedLeaderInput


class ArmSmokeRepeatInput:
    """Four cycles in one admitted Local session, always using the original seed.

    The existing sender admits fresh feedback and owns cancellation/recovery.
    A return outside the small trajectory envelope refuses; it never rebases or
    generates a corrective jump. Boundary qualification pauses trajectory time.
    """

    def __init__(
        self, initial_positions: Mapping[str, float], *, joint_keys: tuple[str, ...],
        fps: int, emit: Callable[[dict[str, Any]], None] | None = None,
        joint_amplitudes: Mapping[str, float] | None = None,
    ) -> None:
        self._emit = emit or (lambda record: print(json.dumps(record, sort_keys=True), flush=True))
        self._pending_events: list[dict[str, Any]] = []
        self._cycle_number = 1
        self.cycles_completed = 0
        self.returns_qualified = 0
        self._sequence = -1
        self._last_tick: float | None = None
        self._return_first: float | None = None
        self._return_count = 0
        self._waiting_for_return = False
        self._complete = False
        self._finished = False
        self._observed: dict[str, float] | None = None
        self._fps = fps
        self._cycle = ScriptedLeaderInput(initial_positions, joint_keys=joint_keys, fps=fps,
                                          emit=self._cycle_event, joint_amplitudes=joint_amplitudes)
        self._joint_amplitudes = dict(self._cycle.joint_amplitudes)
        self.origin = dict(self._cycle.origin)
        self.joint_keys = joint_keys
        self.duration_s = 4 * self._cycle.duration_s
        self.flush_events()

    @property
    def elapsed_s(self) -> float:
        return (self._cycle_number - 1) * self._cycle.duration_s + self._cycle.elapsed_s

    @property
    def complete(self) -> bool:
        return self._complete

    def _cycle_event(self, record: dict[str, Any]) -> None:
        record = {**record, "motion_profile": "ArmSmokeRepeat", "cycle": self._cycle_number,
                  "trajectory_s": (self._cycle_number - 1) * 88 + record["trajectory_s"]}
        if record["event"] == "am1_scripted_input_summary":
            record["event"] = "am1_scripted_cycle_summary"
        self._pending_events.append(record)

    def _record(self, event: str, **fields: Any) -> None:
        self._pending_events.append({"event": event, "input_source": "scripted", "motion_profile": "ArmSmokeRepeat",
                                     "cycle": self._cycle_number, "trajectory_s": round(self.elapsed_s, 6),
                                     "wall_time_ns": time.time_ns(), **fields})

    def flush_events(self) -> None:
        self._cycle.flush_events()
        while self._pending_events:
            self._emit(self._pending_events[0])
            self._pending_events.pop(0)

    def get_action(self) -> dict[str, float]:
        return self._cycle.get_action()

    def admit(self, now: float) -> None:
        if self._finished or self.complete:
            return
        self._cycle.admit(now)
        self._last_tick = now

    def freeze(self) -> None:
        self._last_tick = None
        self._return_first = None
        self._return_count = 0
        self._cycle.freeze()

    def advance(
        self, now: float, observed: Mapping[str, float], observation_sequence: int, *, defer_events: bool = False,
    ) -> None:
        if self._last_tick is None or self._finished or self.complete:
            return
        if not math.isfinite(now) or now < self._last_tick:
            raise ValueError("scripted clock regressed or is invalid")
        if observation_sequence <= self._sequence:
            raise ValueError("scripted feedback sequence did not advance")
        if set(observed) != set(self.origin) or not all(math.isfinite(v) for v in observed.values()):
            raise ValueError("scripted feedback is incomplete or invalid")
        if self._waiting_for_return:
            return_errors = {key: abs(observed[key] - self.origin[key]) for key in self.origin}
            error = max(return_errors.values())
            for key, joint_error in return_errors.items():
                limit = self._joint_amplitudes[key]
                if joint_error > limit:
                    raise ValueError(f"ArmSmokeRepeat return for {key} exceeds {limit} normalized units: {joint_error}")
            self._return_first = now if self._return_first is None else self._return_first
            self._return_count += 1
            span = now - self._return_first
            if self._return_count >= 3 and span >= .2 - 1e-9:
                self.returns_qualified += 1
                self._record("am1_scripted_return_qualified", sample_count=self._return_count,
                             span_s=span, maximum_return_error=error, observation_sequence=observation_sequence,
                             observed=dict(observed), original_seed=dict(self.origin),
                             return_error_limits=dict(self._joint_amplitudes))
                self._cycle.finish("script_complete")
                if self._cycle_number == 4:
                    self._complete = True
                else:
                    self._cycle_number += 1
                    self._cycle = ScriptedLeaderInput(self.origin, joint_keys=self.joint_keys,
                                                     fps=self._fps, emit=self._cycle_event,
                                                     joint_amplitudes=self._joint_amplitudes)
                    self._cycle.admit(now)
                    self._waiting_for_return = False
                    self._return_first = None
                    self._return_count = 0
        else:
            self._cycle.advance(now, observed, observation_sequence, defer_events=True)
            if self._cycle.complete:
                self.cycles_completed += 1
                self._waiting_for_return = True
                self._record("am1_scripted_cycle_complete", observation_sequence=observation_sequence)
        self._sequence = observation_sequence
        self._observed = dict(observed)
        self._last_tick = now
        if not defer_events:
            self.flush_events()

    def finish(self, reason: str) -> None:
        if self._finished:
            return
        self._finished = True
        self.freeze()
        self._cycle.finish(reason)
        self._record("am1_scripted_input_summary", stop_reason=reason, profile_complete=self.complete,
                     cycles_completed=self.cycles_completed, returns_qualified=self.returns_qualified,
                     original_seed=dict(self.origin), observation_sequence=self._sequence,
                     requested={f"arm_{k}": v for k, v in self.get_action().items()}, observed=self._observed)
        self.flush_events()
