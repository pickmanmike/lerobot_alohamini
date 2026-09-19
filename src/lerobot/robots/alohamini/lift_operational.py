"""AM1 owner's confirmed numeric temperature and bounded post-home relief.

No second serial owner, thread, firmware changes, or automatic recovery. Old
diagnostic profiles retain their single-reading policy for historical comparison.
"""

from __future__ import annotations

from collections import deque
from dataclasses import replace
import json
import math
import time
from typing import Any

from . import lift_motor_feedback as feedback
from .lift_relief import InstalledLiftCheck
from .motor_safety import write_register


class TemperatureWindow:
    SIZE = 5
    MAX_AGE_S = 0.5
    CEILING_C = 55

    def __init__(self) -> None:
        self.samples: deque[tuple[float, int]] = deque(maxlen=self.SIZE)
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
            self.refuse("temperature: five genuine fresh baseline samples are required.")
        # Before the next read only, the oldest of the prior five is about to be
        # replaced. Validate the new five in update(), before any command uses it.
        # Otherwise a genuine 10 Hz stream could falsely expire its sixth tick.
        oldest_required = self.samples[-1 if refreshing else 0][0]
        if now < self.samples[-1][0] or now - oldest_required > self.MAX_AGE_S:
            self.refuse("temperature: five-reading feedback window is stale (>0.5 s).")

    def update(self, value: int, now: float, *, cold_start: bool = False) -> dict[str, Any]:
        if self.failure is not None:
            raise self.failure
        if (
            not isinstance(value, (int, float)) or isinstance(value, bool)
            or not math.isfinite(value) or not 0 <= value <= 255 or int(value) != value
            or not math.isfinite(now)
        ):
            self.refuse("temperature: invalid numeric feedback or timestamp.")
        if self.samples and (now <= self.samples[-1][0] or now - self.samples[-1][0] > self.MAX_AGE_S):
            self.refuse("temperature: repeated, backward or stale feedback timestamp.")
        self.samples.append((now, int(value)))
        if now - self.samples[0][0] > self.MAX_AGE_S:
            self.refuse("temperature: five-reading feedback window is stale (>0.5 s).")
        high = sum(temperature >= self.CEILING_C for _, temperature in self.samples)
        if self.ready and high >= 3:
            self.refuse("temperature: majority (3/5) at or above 55 C; stop latched.")
        if self.ready and cold_start and sum(t > feedback.COOL_START_C for _, t in self.samples) >= 3:
            self.refuse("temperature: confirmed starting temperature exceeds 40 C.")
        if value >= self.CEILING_C:
            self.outliers += 1
        return {
            "count": len(self.samples), "high_count": high, "ready": self.ready,
            "span_s": round(now - self.samples[0][0], 6),
            "values_c": [t for _, t in self.samples], "numeric_outlier_count": self.outliers,
        }


class OperationalLift(InstalledLiftCheck):
    """Synchronous AM1 lift monitor; polled once per normal host iteration."""

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
        self.height_mm = 0.0
        self._last_warning = -math.inf
        self._last_goal = 0
        self._goal_since = time.monotonic()
        self._stationary: deque[dict[str, Any]] = deque()
        self._high_idle_current = 0
        self._last_idle_height: float | None = None

    def emit(self, record: dict[str, Any]) -> None:
        record = {key: value for key, value in record.items() if key != "comparison_profile"}
        print("[LIFT OPERATIONAL] " + json.dumps(
            {**record, "policy": "am1-confirmed-temperature-relief-v1", "wall_time_ns": time.time_ns()},
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
            raise
        if record["temperature_c"] >= 55 and now - self._last_warning >= 1.0:
            self.monitor.record(
                "temperature_warning", event="temperature_outlier",
                message="Numeric high reading retained; fewer than 3/5 high. Monitoring continues.",
                numeric_outlier_count=self.temperature.outliers,
            )
            self._last_warning = now

    def raise_if_faulted(self) -> None:
        if self.failure is not None:
            raise self.failure

    def start(self):
        try:
            self.monitor.read_configuration()
            # All ordinary setup writes have finished. Final off follows zero/Lock/mode.
            write_register(self.bus, "Goal_Velocity", self.lift.cfg.name, 0)
            write_register(self.bus, "Torque_Enable", self.lift.cfg.name, 0)
            self.monitor.qualify_stationary(
                "baseline", expected_torque=0, expected_goal=0, cold_start=True,
            )
            self.temperature.assert_fresh(time.monotonic())
            result, _ = self.home_and_relieve()
            self._goal_since = time.monotonic()
            self.poll()
            self.monitor.record(
                "operational_ready", height_mm=self.height_mm,
                zero_reference="process-local, original home zero retained",
                temperature_policy="3/5 >=55 C, all five within 0.5 s",
                support="Keep carriage support and motor-power removal accessible before shutdown.",
            )
            return result
        except BaseException as error:
            self.failure = error
            raise

    def _check_idle(self, record: dict[str, Any]) -> None:
        """Incremental existing stationary evidence, without sleeping in the live loop."""
        now = record["sample_monotonic_s"]
        self._stationary.append(record)
        while len(self._stationary) > 1 and now - self._stationary[0]["sample_monotonic_s"] > 0.5:
            self._stationary.popleft()
        origin = int(self._stationary[0]["present_position_raw"])
        offsets = [feedback._position_delta(origin, int(r["present_position_raw"])) for r in self._stationary]
        recent = list(self._stationary)[-feedback.STATIONARY_PERSISTENT_SAMPLES:]
        velocities = [int(r["present_velocity_raw"]) for r in recent]
        persistent = len(recent) >= feedback.STATIONARY_PERSISTENT_SAMPLES and (
            all(v > feedback.STILL_VELOCITY_RAW for v in velocities)
            or all(v < -feedback.STILL_VELOCITY_RAW for v in velocities)
            or all(r["moving"] == 1 and abs(r["present_velocity_raw"]) <= feedback.STILL_VELOCITY_RAW for r in recent)
        )
        moving = (
            abs(record["present_velocity_raw"]) > feedback.STATIONARY_REPORTED_VELOCITY_LIMIT_RAW
            or max(offsets) - min(offsets) > feedback.STATIONARY_POSITION_TOLERANCE_RAW
            or persistent
        )
        if moving:
            self._stationary.clear()
            self._high_idle_current = 0
            if now - self._goal_since >= feedback.SETTLE_TIMEOUT_S:
                self.refuse(record, "live: lift failed stopped qualification within 1 s.")
            return
        complete = (
            len(self._stationary) >= feedback.STATIONARY_MIN_SAMPLES
            and now - self._stationary[0]["sample_monotonic_s"] >= feedback.STATIONARY_WINDOW_S
        )
        if not complete:
            if now - self._goal_since >= feedback.SETTLE_TIMEOUT_S:
                self.refuse(record, "live: no usable stopped window within 1 s.")
            return
        if self._last_idle_height is None:
            self._last_idle_height = self.height_mm
        if abs(self.height_mm - self._last_idle_height) > 0.5:
            self.refuse(record, "live: unexpected stationary lift displacement.")
        self._high_idle_current = self._high_idle_current + 1 if record["present_current_ma"] >= 200 else 0
        if self._high_idle_current >= 3:
            self.refuse(record, "live: stationary current >=200 mA for three consecutive samples.")

    def poll(self) -> None:
        self.raise_if_faulted()
        try:
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
            if not math.isfinite(self.height_mm) or not -0.5 <= self.height_mm <= self.lift.cfg.soft_max_mm + 0.5:
                self.refuse(record, "live: lift height exceeded travel bounds.")
            if record["moving"] not in (0, 1):
                self.refuse(record, "live: invalid Moving feedback.")
            if goal == 0:
                self._check_idle(record)
            elif now - self._goal_since >= feedback.STATIONARY_WINDOW_S and goal * record["present_velocity_raw"] < -abs(goal) * feedback.STILL_VELOCITY_RAW:
                self.refuse(record, "live: unexpected lift motion direction.")
            self.emit(record)
        except BaseException as error:
            self.failure = error
            raise

    def _require_latest(self) -> None:
        self.raise_if_faulted()
        try:
            self.temperature.assert_fresh(time.monotonic())
            if self.last_record is None:
                raise feedback.ComparisonRefusal("live: no paired lift feedback available.")
        except BaseException as error:
            self.failure = error
            raise

    def apply_action(self, action: dict[str, float]) -> None:
        self._require_latest()
        try:
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

        def cleanup_temperature(record: dict[str, Any], cold: bool) -> None:
            record.update(cleanup_only=True, earlier_failure=str(self.failure) if self.failure else None)
            if self.failure is None:
                # A previously healthy session must still fail on newly confirmed
                # heat during shutdown. Carry the actual history across this edge.
                self._check_temperature(record, cold)

        self.monitor.temperature_check = cleanup_temperature
        try:
            super().cleanup_readback()
            self.monitor.record("shutdown_verified", numeric_outlier_count=self.temperature.outliers)
        finally:
            self.monitor.temperature_check = temperature_check
