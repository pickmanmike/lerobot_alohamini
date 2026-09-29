"""Bounded, opt-in evidence on the existing AM1 motor owner; never a writer."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any


TRACKING_MOTORS = ("arm_left_shoulder_pan", "arm_right_elbow_flex")


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

    def __init__(self, *, emit: Callable[[dict], None] | None = None) -> None:
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
        if now < self.next_at or command_sequence <= self.last_sequence:
            return
        self.last_sequence = command_sequence
        action = robot.logs["arm_tracking_action"]
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
