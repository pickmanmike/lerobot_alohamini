"""Bounded, opt-in evidence on the existing AM1 motor owner; never a writer."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from typing import Any


TRACKING_MOTORS = ("arm_left_shoulder_pan", "arm_right_elbow_flex")
TRACKING_CONFIGURATION_REGISTERS = (
    "Torque_Enable", "Operating_Mode", "CW_Dead_Zone", "CCW_Dead_Zone",
    "Minimum_Startup_Force", "P_Coefficient", "I_Coefficient", "D_Coefficient",
    "Torque_Limit", "Max_Torque_Limit", "Acceleration", "Maximum_Acceleration",
    "Acceleration_Multiplier ", "Goal_Velocity", "Maximum_Velocity_Limit", "Goal_Time",
)


def read_tracking_configuration(robot: Any) -> None:
    """One sequential read-only snapshot on already-owned buses, before activation.

    Called only by the deferred diagnostic during ordinary connect. It does not
    connect/configure/activate anything, and must never be used to justify tuning
    from source intent instead of the actual recorded values.
    """
    record: dict[str, Any] = {
        "event": "am1_arm_tracking_configuration", "phase": "post_configuration_pre_activation",
        "wall_time_ns": time.time_ns(), "writes_performed": False, "joints": {}, "reads": [],
    }
    def emit() -> None:
        print("[AM1 ARM CONFIG] " + json.dumps(record, separators=(",", ":")), flush=True)
    try:
        for motor, bus in zip(TRACKING_MOTORS, (robot.left_bus, robot.right_bus), strict=True):
            values: dict[str, int] = {}
            record["joints"][motor] = values
            for register in TRACKING_CONFIGURATION_REGISTERS:
                read = {"motor": motor, "register": register, "started_at": time.monotonic()}
                record["reads"].append(read)
                values[register] = int(bus.read(register, motor, normalize=False, num_retry=0))
                read["completed_at"] = time.monotonic()
    except BaseException as error:
        record["read_error"] = f"{type(error).__name__}: {error}"
        try:
            emit()
        except BaseException as reporting_error:
            error.add_note(f"configuration reporting also failed: {reporting_error}")
        raise
    emit()


def tracking_enabled(robot: Any) -> bool:
    return (
        getattr(robot.config, "robot_model", None) == "alohamini1"
        and getattr(robot, "_arm_tracking_readback_enabled", False)
    )


class AM1ArmTrackingCapture:
    """At most four two-joint snapshots/s for 120 s, only after accepted actions.

    Position/current are the ordinary observation acquired after that action;
    only Goal_Position adds bus reads. No new thread, owner, write or retry.
    Sequential reads are not an atomic snapshot or a sync-write acknowledgement.
    """

    def __init__(self, *, start: str = "immediate", emit: Callable[[dict], None] | None = None) -> None:
        if start not in ("immediate", "right-elbow-request"):
            raise ValueError(f"Invalid arm tracking start: {start}")
        self.start = start
        self.baseline_requested: float | None = None
        self.emit = emit or self._print
        self.started_at: float | None = None
        self.next_at = 0.0
        self.last_sequence = 0
        self.count = 0
        self.finished = False

    @staticmethod
    def _print(record: dict) -> None:
        print("[AM1 ARM TRACKING] " + json.dumps(record, separators=(",", ":")), flush=True)

    def after_command(self, robot: Any, *, command_sequence: int, observation_id: int, epoch: int) -> None:
        if not tracking_enabled(robot) or self.finished:
            return
        now = time.monotonic()
        if command_sequence <= self.last_sequence:
            return
        action = robot.logs["arm_tracking_action"]
        if self.start == "right-elbow-request" and self.started_at is None:
            requested = float(action[TRACKING_MOTORS[1]]["requested"])
            if not math.isfinite(requested) or not -100 <= requested <= 100:
                raise ValueError("Invalid normalized right-elbow requested target in tracking evidence")
            context = {"baseline_requested": self.baseline_requested, "requested": requested,
                       "command_sequence": command_sequence, "observation_id": observation_id,
                       "epoch": epoch, "wall_time_ns": time.time_ns()}
            if self.baseline_requested is None:
                self.baseline_requested = requested
                self.last_sequence = command_sequence
                self.emit({**context, "event": "am1_arm_tracking_baseline",
                           "baseline_requested": requested, "accepted_at": now})
                return
            # Compare normalized REQUESTS, not raw-register quantization or feedback.
            # float canonicalizes int/float and signed zero; even one representable
            # requested step must trigger. Baseline is never replaced on recovery.
            if requested == self.baseline_requested:
                self.last_sequence = command_sequence
                return
            self.started_at = now
            self.emit({**context, "event": "am1_arm_tracking_trigger", "triggered_at": now})
            # Reporting can block. Keep the original deadline, but timestamp and
            # qualify the actual read after it returns, not before it started.
            now = time.monotonic()
        if self.started_at is None:
            self.started_at = now
        if now - self.started_at >= 120.0 or self.count >= 480:
            self.finished = True
            self.emit({
                "event": "am1_arm_tracking_capture_complete", "samples": self.count,
                "reason": "time_bound" if now - self.started_at >= 120.0 else "sample_bound",
                "wall_time_ns": time.time_ns(),
            })
            return
        if now < self.next_at:
            return
        self.last_sequence = command_sequence
        observation = robot.logs["arm_tracking_observation"]
        record = {
            "event": "am1_arm_tracking", "command_sequence": command_sequence,
            "observation_id": observation_id, "epoch": epoch, "sample": self.count + 1,
            "wall_time_ns": time.time_ns(), "read_started_at": now,
            "action_applied_at": robot.logs["arm_tracking_action_applied_at"],
            "observation_completed_at": observation["completed_at"],
            "sync_write_returned": True, "servo_write_acknowledged": False,
            "readback_writes_performed": False,
            "joints": {
                motor: {**action[motor], **observation["joints"][motor], "goal_position_readback": None}
                for motor in TRACKING_MOTORS
            },
        }
        def emit() -> None:
            record["read_completed_at"] = time.monotonic()
            self.emit(record)
        try:
            for motor, bus in zip(TRACKING_MOTORS, (robot.left_bus, robot.right_bus), strict=True):
                record["joints"][motor]["goal_position_readback"] = bus.read(
                    "Goal_Position", motor, num_retry=0,
                )
        except BaseException as error:
            record["readback_error"] = f"{type(error).__name__}: {error}"
            try:
                emit()
            except BaseException as reporting_error:
                error.add_note(f"arm tracking reporting also failed: {reporting_error}")
            raise
        emit()
        self.count += 1
        self.next_at = time.monotonic() + 0.25  # Completion-spaced; never catch up.

    def finish(self, stop_reason: str) -> None:
        """Evidence only, after host motor/socket cleanup; never read or retry."""
        if self.finished or self.start == "immediate":
            return
        self.finished = True
        self.emit({
            "event": "am1_arm_tracking_capture_complete", "samples": self.count,
            "reason": "no_trigger" if self.started_at is None else stop_reason,
            "stop_reason": stop_reason, "baseline_requested": self.baseline_requested,
            "triggered": self.started_at is not None, "wall_time_ns": time.time_ns(),
        })
