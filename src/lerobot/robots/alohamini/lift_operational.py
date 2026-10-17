"""AM1 owner's confirmed numeric temperature and bounded post-home relief.

No second serial owner, thread, firmware changes, or automatic recovery. Old
diagnostic profiles retain their single-reading policy for historical comparison.
"""

from __future__ import annotations

import json
import math
import time
from collections import deque
from dataclasses import replace
from typing import Any

from . import lift_motor_feedback as feedback
from .lift_relief import InstalledLiftCheck
from .motor_safety import write_register


class TemperatureWindow:
    SIZE = 5
    SLOT_S = 0.1
    MAX_AGE_S = 0.5
    CEILING_C = 55

    def __init__(self, *, ceiling_c: int = CEILING_C) -> None:
        if type(ceiling_c) is not int or not 0 < ceiling_c <= 100:
            raise ValueError("Temperature confirmation needs an explicit valid role ceiling.")
        self.ceiling_c = ceiling_c
        # Each entry is the actual peak reading/time in one occupied 100 ms
        # slot. Faster polling cannot create extra votes or erase a slot's high.
        self.samples: deque[tuple[float, int]] = deque(maxlen=self.SIZE)
        self._origin_ns: int | None = None
        self._last_slot: int | None = None
        self._last_sample_at: float | None = None
        self.failure: feedback.ComparisonRefusal | None = None
        self.outliers = 0

    @property
    def ready(self) -> bool:
        return len(self.samples) == self.SIZE and self.failure is None

    def refuse(self, reason: str) -> None:
        if self.failure is None:
            self.failure = feedback.ComparisonRefusal(reason)
        raise self.failure

    def assert_fresh(self, now: float, *, refreshing: bool = False) -> None:
        if self.failure is not None:
            raise self.failure
        if not math.isfinite(now) or not self.ready:
            self.refuse("temperature: five genuine fresh baseline time slots are required.")
        # A fresh read may replace the oldest slot. Before reading, check the
        # last RAW timestamp; update() then checks all retained peak timestamps
        # before any action can use them. No stale history becomes a normal low.
        oldest_required = self._last_sample_at if refreshing else self.samples[0][0]
        if now < self._last_sample_at or now - oldest_required > self.MAX_AGE_S:
            self.refuse("temperature: five-slot feedback window is stale (>0.5 s).")

    def update(
        self, value: int, now: float, *, cold_start: bool = False, expire_old_slots: bool = False,
    ) -> dict[str, Any]:
        if self.failure is not None:
            raise self.failure
        if (
            not isinstance(value, (int, float)) or isinstance(value, bool)
            or not math.isfinite(value) or not 0 <= value <= 255 or int(value) != value
            or not math.isfinite(now)
        ):
            self.refuse("temperature: invalid numeric feedback or timestamp.")
        if self._last_sample_at is not None and (
            now <= self._last_sample_at or now - self._last_sample_at > self.MAX_AGE_S
        ):
            self.refuse("temperature: repeated, backward or stale feedback timestamp.")
        if type(expire_old_slots) is not bool:
            raise ValueError("Thermal slot expiry must be explicitly selected.")
        if expire_old_slots:
            # Only the separate off/zero cleanup collector selects this. Missing
            # occupied slots reduce its count; fresh reads must fill all five.
            # The powered collector retains its original immediate age refusal.
            while self.samples and now - self.samples[0][0] > self.MAX_AGE_S:
                self.samples.popleft()
        # Integer nanoseconds avoid floating-point 0.1-boundary drift. The first
        # real reading anchors slots for the whole process, never each phase.
        sample_ns = round(now * 1_000_000_000)
        if self._origin_ns is None:
            self._origin_ns = sample_ns
        slot = (sample_ns - self._origin_ns) // round(self.SLOT_S * 1_000_000_000)
        if slot != self._last_slot:
            self.samples.append((now, int(value)))
            self._last_slot = slot
        elif value > self.samples[-1][1]:
            self.samples[-1] = (now, int(value))
        self._last_sample_at = now
        if now - self.samples[0][0] > self.MAX_AGE_S:
            self.refuse("temperature: five-slot feedback window is stale (>0.5 s).")
        if value >= self.ceiling_c:
            self.outliers += 1  # Includes the actual refusal-causing raw reading.
        high = sum(temperature >= self.ceiling_c for _, temperature in self.samples)
        if self.ready and high >= 3:
            self.refuse(
                f"temperature: majority (3/5 time slots) at or above {self.ceiling_c} C; stop latched."
            )
        if self.ready and cold_start and sum(t > feedback.COOL_START_C for _, t in self.samples) >= 3:
            self.refuse("temperature: confirmed starting temperature exceeds 40 C.")
        return {
            "count": len(self.samples), "high_count": high, "ready": self.ready,
            "slot_s": self.SLOT_S, "aggregation": "maximum_per_occupied_slot",
            "span_s": round(now - self.samples[0][0], 6),
            "values_c": [t for _, t in self.samples], "numeric_outlier_count": self.outliers,
        }


class OperationalLift(InstalledLiftCheck):
    """Synchronous AM1 lift monitor with bounded same-owner consumer refresh."""

    # Operating allowance only, not a manufacturer accuracy/safety specification.
    # Strict startup, relief and cleanup retain the shared diagnostic criterion.
    IDLE_POSITION_SPAN_MM = 0.10

    def __init__(self, robot) -> None:
        super().__init__(robot)
        # At raw +200, 600 mm needs about 146 s, not the diagnostic's 20 s.
        # Keep the travel/fault guards and stop on bottom detection, not the deadline.
        # Copy the config so the legacy/opt-in diagnostic limit is unchanged.
        self.lift.cfg = replace(self.lift.cfg, home_timeout_s=180.0)
        self.temperature = TemperatureWindow()
        self.failure: BaseException | None = None
        self.monitor.temperature_check = self._check_temperature
        self.monitor.min_stationary_samples = 5
        self.monitor.sample_timeout_ms = 40.0
        # Finite home/relief current is distinct from stationary >=200 mA.
        # A gross current fault is never subject to the temperature window.
        self.monitor.immediate_current_abort_ma = 2000.0
        self.last_record: dict[str, Any] | None = None
        # One initial poll and at most one refresh at each of the two consumers.
        # Do not overwrite the earlier raw evidence when a consumer refreshes.
        self._pending_samples: list[dict[str, Any]] = []
        self.last_emit_ms = 0.0
        self.height_mm = 0.0
        self._last_warning = -math.inf
        self._last_goal = 0
        self._goal_since = time.monotonic()
        self._stationary: deque[dict[str, Any]] = deque()
        self._idle_position_span_raw = math.floor(
            self.IDLE_POSITION_SPAN_MM / abs(self.lift._deg_per_tick * self.lift._mm_per_deg)
        )
        self._high_idle_current = 0
        self._last_idle_height: float | None = None
        self._idle_qualified_at: float | None = None
        self._idle_uncertain = False
        self._idle_uncertain_band: tuple[int, int, int] | None = None

    def emit(self, record: dict[str, Any]) -> None:
        record = {key: value for key, value in record.items() if key != "comparison_profile"}
        print("[LIFT OPERATIONAL] " + json.dumps(
            {**record, "policy": "am1-confirmed-temperature-slots-relief-v2", "wall_time_ns": time.time_ns()},
            allow_nan=False, separators=(",", ":"),
        ), flush=True)

    def _check_temperature(self, record: dict[str, Any], cold_start: bool) -> None:
        now = record["sample_monotonic_s"]
        if record["request_duration_s"] > 0.04:
            raise feedback.ComparisonRefusal("lift feedback exceeded its 40 ms request budget.")
        try:
            record["temperature_window"] = self.temperature.update(
                record["temperature_c"], now, cold_start=cold_start,
            )
        except feedback.ComparisonRefusal:
            record["temperature_history"] = list(self.temperature.samples)
            record["temperature_slot_s"] = self.temperature.SLOT_S
            record["numeric_outlier_count"] = self.temperature.outliers
            raise
        if getattr(self.robot, "_am1_teleop_startup_temperature", False):
            # Same owner, no recursive lift poll. Keep follower history continuous
            # through finite lift home/relief before ordinary Live polling owns it.
            from .am1_live_teleop import refresh_startup_temperatures

            refresh_startup_temperatures(self.robot)
        if record["temperature_c"] >= 55 and now - self._last_warning >= 1.0:
            self.monitor.record(
                "temperature_warning", event="temperature_outlier",
                message="Numeric high reading retained in its 100 ms slot; confirmation not reached.",
                numeric_outlier_count=self.temperature.outliers,
            )
            self._last_warning = now

    def raise_if_faulted(self) -> None:
        if self.failure is not None:
            raise self.failure

    def _qualify_startup_off(self, *, before_torque: bool = False) -> dict[str, Any]:
        """Acquire observed off state, then prove the unchanged strict baseline."""
        previous_clock = None

        def clock(record: dict[str, Any] | None = None) -> float:
            nonlocal previous_clock
            value = self.monitor.monotonic()
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or value < 0
                or previous_clock is not None
                and value < previous_clock
            ):
                self.refuse(
                    {**(record or {"phase": "startup_off_clock"}), "invalid_clock_value_repr": repr(value)},
                    "startup off: original clock was invalid or regressed.",
                )
            previous_clock = value
            return value

        requested_at = clock()
        deadline = requested_at + feedback.STATIONARY_TIMEOUT_S
        request = self.monitor.record(
            "startup_off_request_started",
            requested_at_monotonic_s=requested_at,
            absolute_deadline_monotonic_s=deadline,
            timeout_s=feedback.STATIONARY_TIMEOUT_S,
            write_evidence="requested start and helper return; not proof of wire effect",
        )

        def remaining(record: dict[str, Any]) -> float:
            value = deadline - clock(record)
            if value <= 0:
                self.refuse(record, "startup off: original deadline expired before the next operation.")
            return value

        # Keep the existing helper's SDK retry/readback behavior. There is no
        # additional request, enabled write, outer retry or renewed deadline.
        for register in ("Goal_Velocity", "Torque_Enable"):
            remaining(request)
            started = clock(request)
            try:
                write_register(self.bus, register, self.lift.cfg.name, 0)
            except BaseException as error:
                try:
                    self.monitor.record(
                        "startup_off_write_helper_failed",
                        register=register,
                        requested_value=0,
                        requested_at_monotonic_s=started,
                        helper_failed_at_monotonic_s=clock(request),
                        absolute_deadline_monotonic_s=deadline,
                        error_type=type(error).__name__,
                        error=str(error),
                        wire_effect="unqualified",
                    )
                except BaseException as diagnostic_error:
                    error.add_note("Startup off write diagnostic also failed: " + type(diagnostic_error).__name__)
                raise  # Preserve the write's first cause even if diagnostic timing fails.
            helper_returned_at = clock(request)
            request = self.monitor.record(
                "startup_off_write_helper_returned",
                register=register,
                requested_value=0,
                requested_at_monotonic_s=started,
                helper_returned_at_monotonic_s=helper_returned_at,
                absolute_deadline_monotonic_s=deadline,
                wire_effect="not established by helper return",
            )
            remaining(request)

        anchor = None
        minimum = maximum = 0
        last_stamp = None
        recent_motion: deque[dict[str, Any]] = deque(maxlen=feedback.STATIONARY_PERSISTENT_SAMPLES)

        def observe(record: dict[str, Any]) -> bool:
            nonlocal anchor, minimum, maximum, last_stamp
            stamp = record["sample_monotonic_s"]
            now = clock(record)
            duration = record["request_duration_s"]
            if (
                type(stamp) not in (int, float)
                or not math.isfinite(stamp)
                or not helper_returned_at <= stamp <= now <= deadline
            ):
                self.refuse(
                    record, "startup off: original feedback timestamp was outside the off request interval."
                )
            if (
                type(duration) not in (int, float)
                or not math.isfinite(duration)
                or not 0 <= duration <= self.monitor.sample_timeout_ms / 1000
            ):
                self.refuse(record, "startup off: original request duration was outside the 40 ms budget.")
            if last_stamp is not None and stamp <= last_stamp:
                self.refuse(record, "startup off: original feedback timestamp did not advance.")
            last_stamp = stamp
            if int(record["torque_enable"]) not in (0, 1):
                self.refuse(record, "startup off: torque readback was not 0 or 1.")
            velocity, moving = int(record["present_velocity_raw"]), int(record["moving"])
            if moving not in (0, 1):
                self.refuse(record, "startup off: Moving readback was not 0 or 1.")
            if abs(velocity) > feedback.STATIONARY_REPORTED_VELOCITY_LIMIT_RAW:
                self.refuse(record, "startup off: reported velocity exceeded the stationary evidence bound.")
            if float(record["present_current_ma"]) >= feedback.CURRENT_ABORT_MA:
                self.refuse(record, "startup off: stationary current reached 200 mA.")
            raw = int(record["present_position_raw"])
            if anchor is None:
                anchor = raw
            offset = feedback._position_delta(anchor, raw)
            minimum, maximum = min(minimum, offset), max(maximum, offset)
            record["startup_off_position_origin_raw"] = anchor
            record["startup_off_position_delta_raw"] = offset
            if (
                maximum - minimum > feedback.STATIONARY_POSITION_TOLERANCE_RAW
                or abs(offset) > feedback.STATIONARY_POSITION_TOLERANCE_RAW
            ):
                self.refuse(record, "startup off: whole-phase position excursion or drift exceeded one tick.")
            # Retain the ordinary stationary indicator policy across acquisition
            # and baseline. Isolated indicators cannot replace the whole fresh
            # off window, and changing phase cannot erase a persistent sequence.
            recent_motion.append(record)
            velocities = [int(sample["present_velocity_raw"]) for sample in recent_motion]
            record["startup_off_recent_velocity_raw"] = velocities
            record["startup_off_recent_moving"] = [int(sample["moving"]) for sample in recent_motion]
            if len(recent_motion) == feedback.STATIONARY_PERSISTENT_SAMPLES:
                moving_without_velocity = all(
                    int(sample["moving"]) == 1 and abs(int(sample["present_velocity_raw"])) <= feedback.STILL_VELOCITY_RAW
                    for sample in recent_motion
                )
                same_positive = all(value > feedback.STILL_VELOCITY_RAW for value in velocities)
                same_negative = all(value < -feedback.STILL_VELOCITY_RAW for value in velocities)
                if moving_without_velocity or same_positive or same_negative:
                    if record["phase"] == "startup_off_acquisition":
                        self.refuse(
                            record, "startup off: velocity or Moving reported persistent motion during the stationary window."
                        )
                    record["startup_off_motion_candidate_reset"] = True
                    record["startup_off_indicator_sample_times"] = [
                        sample["sample_monotonic_s"] for sample in recent_motion
                    ]
                    record["startup_off_indicator_phases"] = [sample["phase"] for sample in recent_motion]
                    return True
            record["startup_off_motion_candidate_reset"] = False
            return False
        while True:
            budget = min(self.monitor.sample_timeout_ms, remaining(request) * 1000)
            record = self.monitor.sample(
                "startup_off_acquisition",
                expected_torque=None,
                expected_goal=0,
                cold_start=True,
                timeout_ms=budget,
                emit_sample=False,
            )
            record["absolute_deadline_monotonic_s"] = deadline
            if clock(record) > deadline:
                self.refuse(record, "startup off: original deadline exceeded by acquisition feedback.")
            observe(record)
            self.emit(record)
            if int(record["torque_enable"]) == 0:
                break  # This first zero is acquisition evidence, not a baseline vote.
            self.monitor.sleep(min(feedback.POLL_S, remaining(record)))
            remaining(record)

        # Preserve the ordinary 50 ms cadence across this new phase boundary;
        # three reports must not become a shorter persistence test. This sleep
        # consumes the original deadline and cannot renew thermal readiness.
        self.monitor.sleep(min(feedback.POLL_S, remaining(record)))
        remaining(record)

        stationary_samples = self.monitor.min_stationary_samples
        self.monitor.min_stationary_samples = 1 + math.ceil(
            (self.temperature.SIZE - 1) * self.temperature.SLOT_S / feedback.POLL_S
        )
        try:
            baseline = self.monitor.qualify_stationary(
                "baseline",
                expected_torque=0,
                expected_goal=0,
                cold_start=True,
                absolute_deadline_s=deadline,
                on_sample=observe,
                startup_off_candidate_reset=True,
            )
        finally:
            self.monitor.min_stationary_samples = stationary_samples
        if clock(baseline) > deadline:
            self.refuse(baseline, "startup off: original deadline exceeded by complete baseline.")
        self.monitor.record(
            "startup_off_baseline_qualified",
            sample_monotonic_s=baseline["sample_monotonic_s"],
            requested_at_monotonic_s=requested_at,
            absolute_deadline_monotonic_s=deadline,
            first_acquired_off_at_monotonic_s=record["sample_monotonic_s"],
            position_origin_raw=anchor,
            whole_phase_position_excursion_raw=maximum - minimum,
            whole_phase_position_drift_raw=feedback._position_delta(
                anchor, int(baseline["present_position_raw"])
            ),
            strict_baseline=baseline["stationary_qualification"],
        )
        if before_torque:
            self.bus.expected_torque = 0
            self.monitor.record(
                "before_torque_off_acquisition_completed",
                sample_monotonic_s=baseline["sample_monotonic_s"],
                strict_baseline=baseline["stationary_qualification"],
            )
        # Equal/lower replies keep the original peak time. Phase work can cross
        # its next slot even before the nominal raw poll interval is due. Keep
        # the same off/zero/cold acquisition, observer and original deadline.
        now = clock(baseline)
        self.temperature.assert_fresh(now, refreshing=True)
        budget = deadline - now
        if budget <= 0:
            self.refuse(baseline, "startup off: original deadline expired before thermal handoff.")
        slot = (round(now * 1_000_000_000) - self.temperature._origin_ns) // round(
            self.temperature.SLOT_S * 1_000_000_000
        )
        if now - self.temperature._last_sample_at >= feedback.POLL_S or slot > self.temperature._last_slot:
            handoff = self.monitor.sample(
                "startup_off_handoff", expected_torque=0, expected_goal=0, cold_start=True,
                timeout_ms=min(self.monitor.sample_timeout_ms, budget * 1000), emit_sample=False,
            )
            handoff["absolute_deadline_monotonic_s"] = deadline
            if clock(handoff) > deadline:
                self.refuse(handoff, "startup off: original deadline exceeded by thermal handoff.")
            if observe(handoff):
                self.refuse(handoff, "startup off: persistent motion invalidated the completed stationary window.")
            self.emit(handoff)
            now = clock(handoff)
            if now > deadline:
                self.refuse(handoff, "startup off: original deadline exceeded after thermal handoff evidence.")
        # No output or second-clock decision between this strict guard and return.
        self.temperature.assert_fresh(now)
        return baseline

    def _qualify_before_torque(self) -> None:
        # Reuse the same off acquisition and nine-sample strict baseline, with
        # its one original 600 ms budget and the existing thermal history.
        self.monitor.record("before_torque_off_acquisition_started")
        self._qualify_startup_off(before_torque=True)

    def start(self):
        try:
            self.monitor.read_configuration()
            self._qualify_startup_off()
            result, _ = self.home_and_relieve(
                allow_one_count_variation=True, qualify_initial_direction=True,
                require_quiet_post_home=True,
                am1_post_home_readiness=True,
                relief_poll_s=feedback.POLL_S,
            )
            self._goal_since = time.monotonic()
            self.poll()
            self.monitor.record(
                "operational_ready", height_mm=self.height_mm,
                zero_reference="process-local, original home zero retained",
                temperature_policy="3/5 100 ms slot maxima >=55 C, all five readings within 0.5 s",
                support="Keep carriage support and motor-power removal accessible before shutdown.",
            )
            return result
        except BaseException as error:
            self.failure = error
            raise

    def _check_idle(self, record: dict[str, Any]) -> None:
        """Incremental existing stationary evidence, without sleeping in the live loop."""
        now = record["sample_monotonic_s"]
        qualified = self._idle_qualified_at is not None
        if qualified:
            self._check_idle_load(record)
        if self._idle_uncertain:
            # Neither a bad sample nor a partial candidate renews this deadline.
            if now - self._idle_qualified_at >= feedback.SETTLE_TIMEOUT_S:
                self.refuse(record, "live: lift failed stopped qualification within 1 s.")
            origin, low, high = self._idle_uncertain_band
            offset = feedback._position_delta(origin, int(record["present_position_raw"]))
            low, high = min(low, offset), max(high, offset)
            if high - low > self._idle_position_span_raw:
                self.refuse(record, "live: lift displaced during stopped-feedback uncertainty.")
            self._idle_uncertain_band = (origin, low, high)
        self._stationary.append(record)
        while len(self._stationary) > 1 and now - self._stationary[0]["sample_monotonic_s"] > 0.5:
            self._stationary.popleft()
        origin = int(self._stationary[0]["present_position_raw"])
        offsets = [feedback._position_delta(origin, int(r["present_position_raw"])) for r in self._stationary]
        recent = list(self._stationary)[-feedback.STATIONARY_PERSISTENT_SAMPLES:]
        velocities = [int(r["present_velocity_raw"]) for r in recent]
        persistent_velocity = len(recent) >= feedback.STATIONARY_PERSISTENT_SAMPLES and (
            all(v > feedback.STILL_VELOCITY_RAW for v in velocities)
            or all(v < -feedback.STILL_VELOCITY_RAW for v in velocities)
        )
        persistent_moving = len(recent) >= feedback.STATIONARY_PERSISTENT_SAMPLES and all(
            r["moving"] == 1 and abs(r["present_velocity_raw"]) <= feedback.STILL_VELOCITY_RAW for r in recent
        )
        motion_evidence = (
            abs(record["present_velocity_raw"]) > feedback.STATIONARY_REPORTED_VELOCITY_LIMIT_RAW
            or max(offsets) - min(offsets) > self._idle_position_span_raw
            or persistent_moving
        )
        if qualified and motion_evidence:
            self.refuse(record, "live: unexpected stationary lift motion.")
        if motion_evidence or persistent_velocity:
            if qualified and not self._idle_uncertain:
                # Only velocity-only ambiguity gets this bounded grace. Anchor the
                # entire episode so resetting candidates cannot hide position drift.
                self._idle_uncertain = True
                self._idle_uncertain_band = (origin, min(offsets), max(offsets))
                self.emit({**record, "phase": "idle_velocity_uncertain", "lift_goal_held_zero": True})
            self._stationary.clear()
            if not qualified and now - self._goal_since >= feedback.SETTLE_TIMEOUT_S:
                self.refuse(record, "live: lift failed stopped qualification within 1 s.")
            return
        complete = (
            len(self._stationary) >= feedback.STATIONARY_MIN_SAMPLES
            and now - self._stationary[0]["sample_monotonic_s"] >= feedback.STATIONARY_WINDOW_S
        )
        if not complete:
            if not qualified and now - self._goal_since >= feedback.SETTLE_TIMEOUT_S:
                self.refuse(record, "live: no usable stopped window within 1 s.")
            return
        if not qualified:
            self._check_idle_load(record)
        self._idle_qualified_at = now
        if self._idle_uncertain:
            self.emit({**record, "phase": "idle_requalified", "lift_goal_held_zero": True})
            self._idle_uncertain = False
            self._idle_uncertain_band = None

    def _check_idle_load(self, record: dict[str, Any]) -> None:
        # Current and displacement remain active even during velocity uncertainty.
        if self._last_idle_height is None:
            self._last_idle_height = self.height_mm
        if abs(self.height_mm - self._last_idle_height) > 0.5:
            self.refuse(record, "live: unexpected stationary lift displacement.")
        self._high_idle_current = self._high_idle_current + 1 if record["present_current_ma"] >= 200 else 0
        if self._high_idle_current >= 3:
            self.refuse(record, "live: stationary current >=200 mA for three consecutive samples.")

    def poll(self, *, defer_sample_log: bool = False) -> None:
        self.raise_if_faulted()
        try:
            if len(self._pending_samples) >= 3:
                raise feedback.ComparisonRefusal("live: pending feedback exceeds one host iteration.")
            # Check before reading: a gap must not be washed away by five new lows.
            self.temperature.assert_fresh(time.monotonic(), refreshing=True)
            record = self.monitor.sample(
                "live", expected_torque=1, expected_goal=self.bus.expected_goal, emit_sample=False,
            )
            self.height_mm = self._height_from_record(record)  # Sole encoder update for this sample.
            record["height_mm"] = self.height_mm
            self.last_record = record
            now = record["sample_monotonic_s"]
            goal = self.bus.expected_goal
            if goal != self._last_goal:
                self._last_goal = goal
                self._goal_since = now
                self._stationary.clear()
                self._last_idle_height = None
                self._high_idle_current = 0
                self._idle_qualified_at = None
                self._idle_uncertain = False
                self._idle_uncertain_band = None
            if not math.isfinite(self.height_mm) or not -0.5 <= self.height_mm <= self.lift.cfg.soft_max_mm + 0.5:
                self.refuse(record, "live: lift height exceeded travel bounds.")
            if record["moving"] not in (0, 1):
                self.refuse(record, "live: invalid Moving feedback.")
            if goal == 0:
                self._check_idle(record)
            elif now - self._goal_since >= feedback.STATIONARY_WINDOW_S and goal * record["present_velocity_raw"] < -abs(goal) * feedback.STILL_VELOCITY_RAW:
                self.refuse(record, "live: unexpected lift motion direction.")
            self._pending_samples.append(record)
            if not defer_sample_log:
                self.emit_pending_sample()
        except BaseException as error:
            self.failure = error
            raise

    def emit_pending_sample(self) -> None:
        """Write the bounded raw batch after its host-loop consumers.

        This is at most three records, not another reader or asynchronous queue.
        Original sample timestamps are retained; logging never refreshes them.
        Fault/transition records remain immediate through the existing monitor.
        """
        if not self._pending_samples:
            return
        emit_started = time.monotonic()
        try:
            while self._pending_samples:
                # A failed sink may already have written this record. Preserve
                # the original no-retry behavior; later records remain pending.
                self.emit(self._pending_samples.pop(0))
        except BaseException as error:
            if self.failure is None:
                self.failure = error
            raise
        finally:
            self.last_emit_ms = (time.monotonic() - emit_started) * 1000

    def _require_latest(self) -> None:
        self.raise_if_faulted()
        try:
            if self.last_record is None:
                raise feedback.ComparisonRefusal("live: no paired lift feedback available.")
            now = time.monotonic()
            # Work since the loop's first read may consume the retained peaks'
            # margin. Only still-fresh RAW feedback permits one genuine read;
            # never catch/clear a latched window refusal or retry until qualified.
            self.temperature.assert_fresh(now, refreshing=True)
            # Decide and qualify the cached history at the same actual clock.
            # Recheck RAW freshness here: work in the first guard cannot grant
            # permission to read after the latest acquisition itself expires.
            history_now = time.monotonic()
            self.temperature.assert_fresh(history_now, refreshing=True)
            if history_now - self.temperature.samples[0][0] > self.temperature.MAX_AGE_S:
                self.poll(defer_sample_log=True)
                history_now = time.monotonic()
            # After a real read this is its actual completion-time guard; with
            # no read there is no intervening work or second-clock boundary race.
            self.temperature.assert_fresh(history_now)
        except BaseException as error:
            self.failure = error
            raise

    def apply_action(self, action: dict[str, float]) -> None:
        self._require_latest()
        try:
            if self._idle_uncertain and (
                f"{self.lift.cfg.name}.height_mm" in action
                or action.get(f"{self.lift.cfg.name}.vel", 0) != 0
            ):
                self.refuse(self.last_record, "live: new lift motion refused while stopped feedback is uncertain.")
            self.lift.apply_action(action, height_mm=self.height_mm)
        except BaseException as error:
            self.failure = error
            raise

    def contribute_observation(self, observation: dict[str, Any]) -> None:
        self._require_latest()
        observation[f"{self.lift.cfg.name}.height_mm"] = self.height_mm
        # Retain the existing raw-velocity observation convention.
        observation[f"{self.lift.cfg.name}.vel"] = self.last_record["present_velocity_raw"]

    def cleanup_readback(self) -> None:
        # Cleanup evidence may be fresh even after a latched fault. It never clears
        # that fault or rearms; genuine status/transport/zero/off failures still fail.
        temperature_check = self.monitor.temperature_check
        cleanup_window = TemperatureWindow()
        cleanup_window.samples.extend(self.temperature.samples)
        for name in ("_origin_ns", "_last_slot", "_last_sample_at", "outliers"):
            setattr(cleanup_window, name, getattr(self.temperature, name))

        def cleanup_temperature(record: dict[str, Any], cold: bool) -> None:
            record.update(cleanup_only=True, earlier_failure=str(self.failure) if self.failure else None)
            if self.failure is None:
                # sample() already checked fresh transport and actual mode/off/
                # zero/current/status. Preserve every unexpired real peak; never
                # manufacture a vote for the slot missed during ordinary shutdown.
                try:
                    if record["request_duration_s"] > 0.04:
                        raise feedback.ComparisonRefusal("lift feedback exceeded its 40 ms request budget.")
                    record["temperature_window"] = cleanup_window.update(
                        record["temperature_c"], record["sample_monotonic_s"],
                        cold_start=cold, expire_old_slots=True,
                    )
                    record["temperature_collection"] = "off-zero-fresh-slots-v1"
                except feedback.ComparisonRefusal as error:
                    record["temperature_history"] = list(cleanup_window.samples)
                    record["temperature_slot_s"] = cleanup_window.SLOT_S
                    record["numeric_outlier_count"] = cleanup_window.outliers
                    self.failure = error
                    raise

        self.monitor.temperature_check = cleanup_temperature
        try:
            self.bus.expected_goal = 0
            self.bus.expected_torque = 0
            write_register(self.bus, "Torque_Enable", self.lift.cfg.name, 0)
            self.monitor.qualify_stationary(
                "cleanup_readback", expected_torque=0, expected_goal=0,
                allow_settling=True, timeout_s=feedback.SETTLE_TIMEOUT_S,
                cleanup_temperature_ready=lambda: self.failure is not None or cleanup_window.ready,
            )
            if self.failure is None:
                cleanup_window.assert_fresh(time.monotonic())
            self.lift.mark_unhomed()
            self.monitor.record("shutdown_verified", numeric_outlier_count=cleanup_window.outliers)
        finally:
            self.monitor.temperature_check = temperature_check
