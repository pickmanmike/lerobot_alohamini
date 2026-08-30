#!/usr/bin/env python

# Copyright 2026 The HuggingFace Inc. team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Run Aloha Mini bimanual teleoperation from the PC leader client."""

from __future__ import annotations

import argparse
import json
import math
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, NamedTuple

from lerobot.robots.alohamini import AlohaMiniClient, AlohaMiniClientConfig
from lerobot.teleoperators.bi_so_leader import BiSOLeader, BiSOLeaderConfig
from lerobot.teleoperators.keyboard.teleop_keyboard import KeyboardTeleop, KeyboardTeleopConfig
from lerobot.utils.robot_utils import precise_sleep

from leader_client_utils import (
    add_leader_port_arguments,
    make_normalized_bi_leader_config,
    resolve_leader_ports,
)


AM1_ARM_POSITION_KEYS = (
    "arm_left_shoulder_pan.pos",
    "arm_left_shoulder_lift.pos",
    "arm_left_elbow_flex.pos",
    "arm_left_wrist_flex.pos",
    "arm_left_wrist_roll.pos",
    "arm_left_gripper.pos",
    "arm_right_shoulder_pan.pos",
    "arm_right_shoulder_lift.pos",
    "arm_right_elbow_flex.pos",
    "arm_right_wrist_flex.pos",
    "arm_right_wrist_roll.pos",
    "arm_right_gripper.pos",
)
ACTION_RANGE_TOLERANCE = 1e-6
STARTUP_SYNC_MAX_STEP = 0.75
STARTUP_SYNC_LEADER_DRIFT = 2.0
AM1_COMMAND_SEND_TIMEOUT_MS = 50
AM1_LIVE_OBSERVATION_MAX_AGE_S = 1.0
RIGHT_WRIST_FLEX_KEY = "arm_right_wrist_flex.pos"

StartupSyncSide = Literal["left", "right", "both"]
LiveArmScope = Literal["both", "right_wrist_flex"]


@dataclass(frozen=True)
class StartupSyncPlan:
    side: StartupSyncSide
    selected_keys: tuple[str, ...]
    follower_start: Mapping[str, float]
    frozen_leader_target: Mapping[str, float]
    requested_duration_s: float
    fps: int
    max_abs_delta: float
    total_steps: int
    frame_count: int
    largest_planned_per_frame_change: float
    estimated_actual_duration_s: float


class SafetyRefusal(ValueError):
    """An expected refusal to forward an unsafe Aloha Mini 1 arm sample."""


class StaleFollowerObservation(RuntimeError):
    """A live follower sample was cached, partial, or otherwise unusable."""


class TransientFollowerObservation(RuntimeError):
    """A follower request completed without a newer decoded observation."""


class AlignmentRow(NamedTuple):
    joint: str
    follower_value: float
    leader_value: float
    signed_difference: float
    absolute_difference: float


@dataclass(frozen=True)
class AM1LiveSample:
    observation_sequence: int
    observed_at: float
    observation: Mapping[str, Any]
    follower_positions: Mapping[str, float]
    arm_target: Mapping[str, float]


@dataclass(frozen=True)
class AM1LiveActionSenderSnapshot:
    action_sequence: int
    action_send_interval_ms: float
    longest_action_send_interval_ms: float
    live_end_wall_time_ns: int | None
    error: BaseException | None


class AM1LiveActionMailbox:
    """Publish only the latest complete validated action atomically."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._action: dict[str, float | int] | None = None

    def publish(self, action: Mapping[str, float | int]) -> None:
        with self._lock:
            self._action = dict(action)

    def snapshot(self) -> dict[str, float | int] | None:
        with self._lock:
            return None if self._action is None else dict(self._action)


class AM1LiveActionSender:
    """Own the live PUSH socket and completion-spaced action cadence in one thread."""

    def __init__(
        self,
        robot: Any,
        *,
        initial_action: Mapping[str, float | int],
        initial_observation_sequence: int,
        fps: int,
        duration_s: float,
        profile_cadence: bool,
        monotonic: Callable[[], float] = time.monotonic,
        wall_time_ns: Callable[[], int] = time.time_ns,
        sleep_fn: Callable[[float], None] = precise_sleep,
    ) -> None:
        if fps <= 0:
            raise ValueError("fps must be greater than zero")
        self.mailbox = AM1LiveActionMailbox()
        self._robot = robot
        self._initial_action = dict(initial_action)
        self._initial_observation_sequence = initial_observation_sequence
        self._fps = fps
        self._duration_s = duration_s
        self._profile_cadence = profile_cadence
        self._monotonic = monotonic
        self._wall_time_ns = wall_time_ns
        self._sleep_fn = sleep_fn
        self._stop_requested = threading.Event()
        self._finished = threading.Event()
        self._state_lock = threading.Lock()
        self._action_sequence = 0
        self._last_send_interval_ms = 0.0
        self._longest_send_interval_ms = 0.0
        self._live_end_wall_time_ns: int | None = None
        self._error: BaseException | None = None
        self._thread = threading.Thread(
            target=self._run,
            name="am1-live-action-sender",
            daemon=False,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_requested.set()

    def join(self) -> None:
        self._thread.join()

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def wait(self, timeout: float) -> bool:
        return self._finished.wait(timeout)

    def publish(self, action: Mapping[str, float | int]) -> None:
        self.mailbox.publish(action)

    def snapshot(self) -> AM1LiveActionSenderSnapshot:
        with self._state_lock:
            return AM1LiveActionSenderSnapshot(
                action_sequence=self._action_sequence,
                action_send_interval_ms=self._last_send_interval_ms,
                longest_action_send_interval_ms=self._longest_send_interval_ms,
                live_end_wall_time_ns=self._live_end_wall_time_ns,
                error=self._error,
            )

    def _run(self) -> None:
        primary_error: BaseException | None = None
        action = dict(self._initial_action)
        last_send_started_at: float | None = None
        started_at = self._monotonic()
        try:
            with self._robot.make_live_command_sender() as command_sender:
                while True:
                    with self._state_lock:
                        action_sequence = self._action_sequence
                    now = self._monotonic()
                    if action_sequence > 0 and self._stop_requested.is_set():
                        break
                    if action_sequence > 0 and self._duration_s > 0 and now - started_at >= self._duration_s:
                        break

                    send_started_at = self._monotonic()
                    if self._profile_cadence and action_sequence == 0:
                        print(
                            json.dumps(
                                {
                                    "event": "am1_client_live_start",
                                    "initial_observation_sequence": self._initial_observation_sequence,
                                    "right_wrist_requested": action[RIGHT_WRIST_FLEX_KEY],
                                    "wall_time_ns": self._wall_time_ns(),
                                },
                                sort_keys=True,
                            )
                        )
                    command_sender.send_action(action)
                    send_completed_at = self._monotonic()
                    send_interval_ms = (
                        0.0
                        if last_send_started_at is None
                        else (send_started_at - last_send_started_at) * 1e3
                    )
                    with self._state_lock:
                        self._action_sequence += 1
                        self._last_send_interval_ms = send_interval_ms
                        self._longest_send_interval_ms = max(
                            self._longest_send_interval_ms,
                            send_interval_ms,
                        )
                    last_send_started_at = send_started_at

                    latest_action = self.mailbox.snapshot()
                    if latest_action is not None:
                        action = latest_action
                    deadline = next_completion_spaced_deadline(send_completed_at, fps=self._fps)
                    self._sleep_fn(max(deadline - self._monotonic(), 0.0))
        except BaseException as exc:
            primary_error = exc
        finally:
            diagnostic_error: BaseException | None = None
            live_end_wall_time_ns = None
            if self._profile_cadence:
                try:
                    live_end_wall_time_ns = self._wall_time_ns()
                except BaseException as exc:
                    diagnostic_error = exc
            if primary_error is not None and diagnostic_error is not None and diagnostic_error is not primary_error:
                primary_error.add_note(
                    f"Cadence diagnostics failed while capturing the live-end wall clock: {diagnostic_error!r}"
                )
            with self._state_lock:
                self._live_end_wall_time_ns = live_end_wall_time_ns
                self._error = primary_error or diagnostic_error
            self._finished.set()


def _joint_identity(key: str) -> tuple[str, str]:
    side_and_joint = key.removeprefix("arm_").removesuffix(".pos")
    side, joint = side_and_joint.split("_", maxsplit=1)
    return side, joint


def extract_am1_arm_positions(
    values: dict[str, Any],
    *,
    source: str,
    leader_sample: bool,
) -> dict[str, float]:
    """Validate and return the exact normalized AM1 arm-position mapping."""
    if leader_sample:
        arm_values = {
            key if key.startswith("arm_") else f"arm_{key}": value
            for key, value in values.items()
            if key.endswith(".pos")
        }
    else:
        arm_values = {
            key: value for key, value in values.items() if key.startswith("arm_") and key.endswith(".pos")
        }

    expected = set(AM1_ARM_POSITION_KEYS)
    actual = set(arm_values)
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if missing or unexpected:
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if unexpected:
            details.append(f"unexpected {', '.join(unexpected)}")
        raise SafetyRefusal(f"{source} AM1 arm-position keys are invalid: {'; '.join(details)}")

    validated: dict[str, float] = {}
    for key in AM1_ARM_POSITION_KEYS:
        side, joint = _joint_identity(key)
        try:
            value = float(arm_values[key])
        except (TypeError, ValueError) as exc:
            raise SafetyRefusal(f"{source} {side} {joint} value {arm_values[key]!r} must be numeric") from exc
        if not math.isfinite(value):
            raise SafetyRefusal(f"{source} {side} {joint} value {value} must be finite")

        if leader_sample:
            lower = 0.0 if joint == "gripper" else -100.0
            upper = 100.0
            if value < lower - ACTION_RANGE_TOLERANCE or value > upper + ACTION_RANGE_TOLERANCE:
                expected_range = "0..100" if joint == "gripper" else "-100..100"
                raise SafetyRefusal(
                    f"{source} {side} {joint} value {value} is outside expected {expected_range}"
                )
        validated[key] = value

    return validated


def read_fresh_am1_live_sample(
    robot: Any,
    leader: Any,
    *,
    previous_sequence: int,
    monotonic: Callable[[], float] = time.monotonic,
) -> AM1LiveSample:
    """Read one fresh follower observation followed by one complete leader target."""
    observation = robot.get_observation()
    observed_at = monotonic()
    observation_sequence = int(robot.observation_sequence)
    if observation_sequence == previous_sequence:
        raise TransientFollowerObservation(
            "observation_sequence did not advance "
            f"(previous={previous_sequence}, current={observation_sequence})"
        )
    if observation_sequence < previous_sequence:
        raise SafetyRefusal(
            "observation_sequence regressed "
            f"(previous={previous_sequence}, current={observation_sequence})"
        )

    follower_positions = extract_am1_arm_positions(
        dict(observation),
        source="live follower observation",
        leader_sample=False,
    )
    validate_selected_sync_positions(
        follower_positions,
        AM1_ARM_POSITION_KEYS,
        source="live follower observation",
    )

    arm_target = extract_am1_arm_positions(
        leader.get_action(),
        source="live leader",
        leader_sample=True,
    )
    leader_sampled_at = monotonic()
    observation_age_s = leader_sampled_at - observed_at
    if observation_age_s >= AM1_LIVE_OBSERVATION_MAX_AGE_S:
        raise StaleFollowerObservation(
            f"follower observation age {observation_age_s:.3f}s reached the "
            f"{AM1_LIVE_OBSERVATION_MAX_AGE_S:.1f}-second freshness limit after leader sampling"
        )
    return AM1LiveSample(
        observation_sequence=observation_sequence,
        observed_at=observed_at,
        observation=MappingProxyType(dict(observation)),
        follower_positions=MappingProxyType(follower_positions),
        arm_target=MappingProxyType(arm_target),
    )


def apply_am1_commissioning_scope(
    latest_arm_target: Mapping[str, float],
    approved_arm_target: Mapping[str, float],
    *,
    scope: LiveArmScope,
) -> dict[str, float]:
    """Select a complete AM1 arm target without introducing a sign transform."""
    latest = extract_am1_arm_positions(
        dict(latest_arm_target),
        source="latest live leader target",
        leader_sample=False,
    )
    approved = extract_am1_arm_positions(
        dict(approved_arm_target),
        source="approved live hold target",
        leader_sample=False,
    )
    validate_selected_sync_positions(latest, AM1_ARM_POSITION_KEYS, source="latest live leader target")
    validate_selected_sync_positions(approved, AM1_ARM_POSITION_KEYS, source="approved live hold target")

    if scope == "both":
        return latest
    if scope == "right_wrist_flex":
        scoped = dict(approved)
        scoped[RIGHT_WRIST_FLEX_KEY] = latest[RIGHT_WRIST_FLEX_KEY]
        return scoped
    raise ValueError(f"Unsupported live arm scope: {scope!r}")


def make_am1_live_action(arm_target: Mapping[str, float]) -> dict[str, float | int]:
    validated = extract_am1_arm_positions(
        dict(arm_target),
        source="live arm target",
        leader_sample=False,
    )
    validate_selected_sync_positions(validated, AM1_ARM_POSITION_KEYS, source="live arm target")
    return {**validated, **make_zero_action()}


def next_completion_spaced_deadline(send_completed_at: float, *, fps: int) -> float:
    if fps <= 0:
        raise ValueError("fps must be greater than zero")
    return send_completed_at + 1.0 / fps


def selected_arm_position_keys(side: StartupSyncSide) -> tuple[str, ...]:
    if side not in {"left", "right", "both"}:
        raise ValueError(f"Unsupported startup sync side: {side!r}")
    if side == "both":
        return AM1_ARM_POSITION_KEYS
    return tuple(key for key in AM1_ARM_POSITION_KEYS if key.startswith(f"arm_{side}_"))


def validate_selected_sync_positions(
    positions: Mapping[str, float],
    selected_keys: tuple[str, ...],
    *,
    source: str,
) -> None:
    for key in selected_keys:
        side, joint = _joint_identity(key)
        try:
            value = float(positions[key])
        except (KeyError, TypeError, ValueError) as exc:
            raise SafetyRefusal(f"{source} {side} {joint} must be present and numeric") from exc
        if not math.isfinite(value):
            raise SafetyRefusal(f"{source} {side} {joint} value {value} must be finite")
        lower = 0.0 if joint == "gripper" else -100.0
        upper = 100.0
        if value < lower - ACTION_RANGE_TOLERANCE or value > upper + ACTION_RANGE_TOLERANCE:
            expected_range = "0..100" if joint == "gripper" else "-100..100"
            raise SafetyRefusal(
                f"{source} {side} {joint} value {value} is outside expected {expected_range}"
            )


def build_startup_sync_plan(
    follower_start: Mapping[str, float],
    frozen_leader_target: Mapping[str, float],
    *,
    side: StartupSyncSide,
    requested_duration_s: float,
    fps: int,
) -> StartupSyncPlan:
    if not math.isfinite(requested_duration_s) or requested_duration_s <= 0:
        raise SafetyRefusal("startup sync duration must be finite and greater than zero")
    if fps <= 0:
        raise SafetyRefusal("startup sync fps must be greater than zero")

    selected_keys = selected_arm_position_keys(side)
    follower_copy = extract_am1_arm_positions(
        dict(follower_start),
        source="follower",
        leader_sample=False,
    )
    target_copy = extract_am1_arm_positions(
        dict(frozen_leader_target),
        source="frozen leader target",
        leader_sample=True,
    )
    validate_selected_sync_positions(follower_copy, selected_keys, source="follower")
    max_abs_delta = max(abs(target_copy[key] - follower_copy[key]) for key in selected_keys)
    duration_steps = math.ceil(requested_duration_s * fps)
    step_limit_steps = math.ceil(max_abs_delta / STARTUP_SYNC_MAX_STEP)
    total_steps = max(1, duration_steps, step_limit_steps)
    frame_count = total_steps + 1
    largest_change = max_abs_delta / total_steps
    estimated_duration = total_steps / fps
    if largest_change > STARTUP_SYNC_MAX_STEP + ACTION_RANGE_TOLERANCE:
        raise SafetyRefusal(
            f"startup sync planned per-frame change {largest_change} exceeds "
            f"STARTUP_SYNC_MAX_STEP {STARTUP_SYNC_MAX_STEP}"
        )

    return StartupSyncPlan(
        side=side,
        selected_keys=selected_keys,
        follower_start=MappingProxyType(follower_copy),
        frozen_leader_target=MappingProxyType(target_copy),
        requested_duration_s=requested_duration_s,
        fps=fps,
        max_abs_delta=max_abs_delta,
        total_steps=total_steps,
        frame_count=frame_count,
        largest_planned_per_frame_change=largest_change,
        estimated_actual_duration_s=estimated_duration,
    )


def build_startup_sync_action(
    plan: StartupSyncPlan,
    frame_index: int,
) -> dict[str, float | int]:
    if frame_index < 0 or frame_index > plan.total_steps:
        raise ValueError(f"startup sync frame index {frame_index} is outside 0..{plan.total_steps}")

    if frame_index == 0:
        arm_action = {key: plan.follower_start[key] for key in plan.selected_keys}
    elif frame_index == plan.total_steps:
        arm_action = {key: plan.frozen_leader_target[key] for key in plan.selected_keys}
    else:
        alpha = frame_index / plan.total_steps
        arm_action = {
            key: plan.follower_start[key]
            + alpha * (plan.frozen_leader_target[key] - plan.follower_start[key])
            for key in plan.selected_keys
        }

    validate_selected_sync_positions(arm_action, plan.selected_keys, source="synchronization target")
    if frame_index > 0:
        previous_index = frame_index - 1
        for key in plan.selected_keys:
            if previous_index == 0:
                previous_value = plan.follower_start[key]
            else:
                previous_alpha = previous_index / plan.total_steps
                previous_value = plan.follower_start[key] + previous_alpha * (
                    plan.frozen_leader_target[key] - plan.follower_start[key]
                )
            change = abs(arm_action[key] - previous_value)
            if change > STARTUP_SYNC_MAX_STEP + ACTION_RANGE_TOLERANCE:
                raise SafetyRefusal(
                    f"startup sync frame {frame_index} change for {key} is {change}, "
                    f"above STARTUP_SYNC_MAX_STEP {STARTUP_SYNC_MAX_STEP}"
                )
    return {**arm_action, **make_zero_action()}


def get_fresh_follower_observation(robot: Any) -> dict[str, Any]:
    """Wait until the client proves that a newly decoded observation arrived."""
    previous_sequence = robot.observation_sequence
    deadline = time.monotonic() + robot.config.connect_timeout_s
    observation: dict[str, Any] = {}
    while robot.observation_sequence == previous_sequence:
        observation = robot.get_observation()
        if robot.observation_sequence == previous_sequence and time.monotonic() >= deadline:
            raise RuntimeError("Timed out waiting for a fresh follower observation for alignment.")
    return observation


def build_alignment_rows(
    follower_positions: dict[str, float],
    leader_positions: dict[str, float],
) -> list[AlignmentRow]:
    rows = []
    for joint in AM1_ARM_POSITION_KEYS:
        follower_value = follower_positions[joint]
        leader_value = leader_positions[joint]
        signed_difference = leader_value - follower_value
        rows.append(
            AlignmentRow(
                joint=joint,
                follower_value=follower_value,
                leader_value=leader_value,
                signed_difference=signed_difference,
                absolute_difference=abs(signed_difference),
            )
        )
    return rows


def _print_alignment_table(rows: list[AlignmentRow]) -> None:
    print("AM1 leader/follower alignment (normalized units):")
    print(
        f"  {'joint':<36} {'follower value':>14} {'leader value':>14} "
        f"{'signed difference':>18} {'absolute difference':>20}"
    )
    for row in rows:
        print(
            f"  {row.joint:<36} {row.follower_value:>14.3f} {row.leader_value:>14.3f} "
            f"{row.signed_difference:>18.3f} {row.absolute_difference:>20.3f}"
        )


def _print_startup_sync_plan(plan: StartupSyncPlan, *, label: str) -> None:
    print(f"{label} AM1 startup synchronization plan:")
    print(f"  Selected side: {plan.side}")
    print(f"  Requested minimum duration: {plan.requested_duration_s:.3f}s")
    print(f"  Largest selected-joint displacement: {plan.max_abs_delta:.3f}")
    print(f"  Planned intervals: {plan.total_steps}")
    print(f"  Planned frames: {plan.frame_count}")
    print(f"  Largest planned per-frame change: {plan.largest_planned_per_frame_change:.3f}")
    print(f"  Estimated actual duration: {plan.estimated_actual_duration_s:.3f}s")


def _print_startup_sync_safety_instructions() -> None:
    print("Startup synchronization is not collision-aware joint-space interpolation.")
    print("Use empty grippers and clear the complete follower arm envelope.")
    print("Place both leaders in moderate poses and hold them still until verification completes.")
    print("Keep people, objects, the other arm, and the chassis clear of any uncertain path.")
    print("Keep the follower motor disconnect immediately accessible.")


def validate_startup_sync_leader_drift(
    current_leader: Mapping[str, float],
    frozen_leader_target: Mapping[str, float],
    selected_keys: tuple[str, ...],
) -> None:
    for key in selected_keys:
        side, joint = _joint_identity(key)
        signed_difference = current_leader[key] - frozen_leader_target[key]
        drift = abs(signed_difference)
        if drift > STARTUP_SYNC_LEADER_DRIFT:
            raise SafetyRefusal(
                f"startup sync leader drift for {side} {joint}: "
                f"frozen={frozen_leader_target[key]}, current={current_leader[key]}, "
                f"signed_difference={signed_difference}, drift={drift} exceeds "
                f"STARTUP_SYNC_LEADER_DRIFT {STARTUP_SYNC_LEADER_DRIFT}"
            )


def verify_startup_sync_result(
    follower_positions: Mapping[str, float],
    frozen_leader_target: Mapping[str, float],
    *,
    selected_keys: tuple[str, ...],
    max_start_mismatch: float,
) -> list[AlignmentRow]:
    rows = build_alignment_rows(dict(follower_positions), dict(frozen_leader_target))
    _print_alignment_table(rows)
    selected = set(selected_keys)
    mismatches = [
        row
        for row in rows
        if row.joint in selected and row.absolute_difference > max_start_mismatch
    ]
    if mismatches:
        worst = max(mismatches, key=lambda row: row.absolute_difference)
        raise SafetyRefusal(
            f"startup sync verification mismatch for {worst.joint}: "
            f"follower={worst.follower_value}, frozen={worst.leader_value}, "
            f"signed_difference={worst.signed_difference}, "
            f"absolute_difference={worst.absolute_difference} exceeds "
            f"--max_start_mismatch {max_start_mismatch}"
        )
    return rows


def run_startup_sync(
    robot: Any,
    leader: Any,
    *,
    side: StartupSyncSide,
    requested_duration_s: float,
    fps: int,
    max_start_mismatch: float,
    input_fn: Callable[[str], str],
    monotonic: Callable[[], float],
    sleep_fn: Callable[[float], None],
) -> tuple[dict[str, float], dict[str, Any]]:
    print("HOLD LEADERS STILL — STARTUP SYNCHRONIZATION IN PROGRESS")
    initial_observation = get_fresh_follower_observation(robot)
    initial_follower = extract_am1_arm_positions(
        initial_observation,
        source="follower",
        leader_sample=False,
    )
    initial_leader = extract_am1_arm_positions(
        leader.get_action(),
        source="leader",
        leader_sample=True,
    )
    preliminary_plan = build_startup_sync_plan(
        initial_follower,
        initial_leader,
        side=side,
        requested_duration_s=requested_duration_s,
        fps=fps,
    )
    _print_alignment_table(build_alignment_rows(initial_follower, initial_leader))
    _print_startup_sync_plan(preliminary_plan, label="Preliminary")
    _print_startup_sync_safety_instructions()
    if input_fn("Type exactly SYNC and press Enter to begin follower motion: ") != "SYNC":
        raise SafetyRefusal("startup synchronization requires the operator to type exactly SYNC")

    start_observation = get_fresh_follower_observation(robot)
    follower_start = extract_am1_arm_positions(
        start_observation,
        source="follower",
        leader_sample=False,
    )
    frozen_target = extract_am1_arm_positions(
        leader.get_action(),
        source="leader",
        leader_sample=True,
    )
    plan = build_startup_sync_plan(
        follower_start,
        frozen_target,
        side=side,
        requested_duration_s=requested_duration_s,
        fps=fps,
    )
    _print_alignment_table(build_alignment_rows(follower_start, frozen_target))
    _print_startup_sync_plan(plan, label="Final frozen-target")

    current_leader = extract_am1_arm_positions(
        leader.get_action(),
        source="leader",
        leader_sample=True,
    )
    validate_startup_sync_leader_drift(
        current_leader,
        plan.frozen_leader_target,
        plan.selected_keys,
    )
    robot.send_action(build_startup_sync_action(plan, 0))
    previous_send_completed_at = monotonic()
    frame_period_s = 1.0 / plan.fps

    for frame_index in range(1, plan.frame_count):
        next_send_not_before = previous_send_completed_at + frame_period_s
        sleep_fn(max(next_send_not_before - monotonic(), 0.0))
        current_leader = extract_am1_arm_positions(
            leader.get_action(),
            source="leader",
            leader_sample=True,
        )
        validate_startup_sync_leader_drift(
            current_leader,
            plan.frozen_leader_target,
            plan.selected_keys,
        )
        robot.send_action(build_startup_sync_action(plan, frame_index))
        previous_send_completed_at = monotonic()

    validated_frozen_target = extract_am1_arm_positions(
        dict(plan.frozen_leader_target),
        source="frozen leader target",
        leader_sample=True,
    )
    verification_attempts = int(getattr(robot.config, "observation_request_window", 1)) + 1
    for verification_index in range(verification_attempts):
        final_observation = get_fresh_follower_observation(robot)
        final_follower = extract_am1_arm_positions(
            final_observation,
            source="follower",
            leader_sample=False,
        )
        try:
            verify_startup_sync_result(
                final_follower,
                validated_frozen_target,
                selected_keys=plan.selected_keys,
                max_start_mismatch=max_start_mismatch,
            )
        except SafetyRefusal:
            if verification_index == verification_attempts - 1:
                raise
            continue
        break

    return dict(plan.frozen_leader_target), final_observation


def run_alignment_gate(
    robot: Any,
    leader: Any,
    max_start_mismatch: float,
) -> tuple[dict[str, float], dict[str, Any]]:
    observation = get_fresh_follower_observation(robot)
    follower_positions = extract_am1_arm_positions(
        observation,
        source="follower",
        leader_sample=False,
    )
    leader_positions = extract_am1_arm_positions(
        leader.get_action(),
        source="leader",
        leader_sample=True,
    )
    rows = build_alignment_rows(follower_positions, leader_positions)
    _print_alignment_table(rows)

    mismatched_rows = [row for row in rows if row.absolute_difference > max_start_mismatch]
    if mismatched_rows:
        worst = max(mismatched_rows, key=lambda row: row.absolute_difference)
        raise SafetyRefusal(
            f"startup alignment mismatch for {worst.joint}: follower={worst.follower_value}, "
            f"leader={worst.leader_value}, signed_difference={worst.signed_difference}, "
            f"absolute_difference={worst.absolute_difference} exceeds --max_start_mismatch "
            f"{max_start_mismatch}"
        )
    return leader_positions, observation


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no_robot", action="store_true", help="Do not construct or connect the robot client")
    parser.add_argument("--no_leader", action="store_true", help="Do not construct or connect the leader arms")
    parser.add_argument("--no_keyboard", action="store_true", help="Disable keyboard base and lift control")
    parser.add_argument(
        "--no_cameras",
        action="store_true",
        help="Construct the client with an empty camera schema and perform no camera fallback work",
    )
    parser.add_argument("--no_rerun", action="store_true", help="Disable Rerun without importing visualization helpers")
    parser.add_argument(
        "--require_calibration_match",
        action="store_true",
        help="For AM1 no-robot mapping only, refuse leaders without loaded calibration",
    )
    parser.add_argument("--start_paused", action="store_true", help="Wait for Enter before forwarding leader actions")
    parser.add_argument(
        "--check_alignment_only",
        action="store_true",
        help="Validate AM1 leader/follower alignment, send no arm action, and exit",
    )
    parser.add_argument(
        "--startup_mode",
        choices=("strict", "sync"),
        default="strict",
        help="AM1 startup behavior: strict alignment refusal or operator-authorized synchronization (default: strict)",
    )
    parser.add_argument(
        "--startup_sync_duration_s",
        type=float,
        default=12.0,
        help="Requested minimum synchronization duration in seconds (default: 12.0)",
    )
    parser.add_argument(
        "--startup_sync_side",
        choices=("left", "right", "both"),
        default="both",
        help="Follower side synchronized by sync mode (default: both)",
    )
    parser.add_argument(
        "--startup_sync_only",
        action="store_true",
        help=(
            "Synchronize, verify, and exit; --start_paused has no effect and "
            "--duration_s is unused in this mode"
        ),
    )
    parser.add_argument(
        "--max_start_mismatch",
        type=float,
        default=10.0,
        help=(
            "AM1 final convergence verification tolerance in normalized units; does not limit "
            "the initial mismatch that sync may plan (default: 10.0)"
        ),
    )
    parser.add_argument(
        "--duration_s",
        type=float,
        default=0.0,
        help="Stop cleanly after this many seconds; 0 has no time limit",
    )
    parser.add_argument("--fps", type=int, default=30, help="Main loop frequency (frames per second)")
    parser.add_argument(
        "--live_arm_scope",
        choices=("both", "right_wrist_flex"),
        default="both",
        help=(
            "AM1 live forwarding scope; right_wrist_flex holds every other arm joint at the "
            "final approved startup target (default: both)"
        ),
    )
    parser.add_argument(
        "--profile_cadence",
        action="store_true",
        help=(
            "Emit a default-off JSON live-start marker and one bounded timing summary "
            "after live AM1 forwarding"
        ),
    )
    parser.add_argument(
        "--robot.remote_ip",
        "--remote_ip",
        dest="remote_ip",
        default="127.0.0.1",
        help="Aloha Mini host IP address",
    )
    parser.add_argument(
        "--robot.id",
        "--robot_id",
        dest="robot_id",
        default="my_alohamini",
        help="Robot ID",
    )
    parser.add_argument(
        "--robot.robot_model",
        "--robot_model",
        dest="robot_model",
        default="alohamini1",
        choices=["alohamini1", "alohamini2", "alohamini2pro"],
        help="Aloha Mini model. Must match the Pi host setting.",
    )
    parser.add_argument(
        "--teleop.id",
        "--leader_id",
        dest="leader_id",
        default="so101_leader_bi",
        help="Leader arm device ID",
    )
    parser.add_argument(
        "--teleop.arm_profile",
        "--arm_profile",
        dest="arm_profile",
        default="so-arm-5dof",
        choices=["so-arm-5dof", "am-leader-6dof"],
        help="Leader arm profile selector",
    )
    add_leader_port_arguments(parser)
    return parser


def parse_args(
    argv: list[str] | None = None,
    *,
    platform_name: str | None = None,
) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.fps <= 0:
        parser.error("--fps must be greater than zero")
    if args.duration_s < 0:
        parser.error("--duration_s must be zero or greater")
    if not math.isfinite(args.max_start_mismatch) or args.max_start_mismatch <= 0:
        parser.error("--max_start_mismatch must be finite and greater than zero")
    if not math.isfinite(args.startup_sync_duration_s) or args.startup_sync_duration_s <= 0:
        parser.error("--startup_sync_duration_s must be finite and greater than zero")
    if args.startup_sync_only and args.startup_mode != "sync":
        parser.error("--startup_sync_only requires --startup_mode sync")
    if args.startup_sync_side in {"left", "right"} and not args.startup_sync_only:
        parser.error("--startup_sync_side left or right requires --startup_sync_only")
    if args.startup_mode == "sync":
        if args.robot_model != "alohamini1":
            parser.error("--startup_mode sync is supported only for alohamini1")
        if args.no_robot or args.no_leader:
            parser.error("--startup_mode sync requires both robot and leader connections")
        if args.check_alignment_only:
            parser.error("--check_alignment_only is incompatible with --startup_mode sync")
    if args.check_alignment_only and (args.no_robot or args.no_leader):
        parser.error("--check_alignment_only requires both robot and leader connections")
    if args.check_alignment_only and args.robot_model != "alohamini1":
        parser.error("--check_alignment_only is supported only for alohamini1")
    if args.require_calibration_match:
        if args.robot_model != "alohamini1":
            parser.error("--require_calibration_match requires --robot.robot_model alohamini1")
        if args.arm_profile != "so-arm-5dof":
            parser.error("--require_calibration_match requires --teleop.arm_profile so-arm-5dof")
        if not args.no_robot:
            parser.error("--require_calibration_match requires --no_robot")
        if args.no_leader:
            parser.error("--require_calibration_match requires leader connections")
    if args.live_arm_scope == "right_wrist_flex":
        if args.robot_model != "alohamini1":
            parser.error("--live_arm_scope right_wrist_flex is supported only for alohamini1")
        if not args.start_paused:
            parser.error("--live_arm_scope right_wrist_flex requires --start_paused")
        if not args.no_keyboard:
            parser.error("--live_arm_scope right_wrist_flex requires --no_keyboard")
        if not args.no_cameras:
            parser.error("--live_arm_scope right_wrist_flex requires --no_cameras")
        if args.no_robot or args.no_leader:
            parser.error("--live_arm_scope right_wrist_flex requires robot and leader connections")
    if args.profile_cadence and not (
        args.robot_model == "alohamini1" and args.no_keyboard and args.no_cameras
    ):
        parser.error("--profile_cadence requires alohamini1 with --no_keyboard and --no_cameras")
    return resolve_leader_ports(args, parser, platform_name=platform_name)


def make_leader_config(args: argparse.Namespace) -> BiSOLeaderConfig:
    return make_normalized_bi_leader_config(
        left_port=args.left_port,
        right_port=args.right_port,
        leader_id=args.leader_id,
        arm_profile=args.arm_profile,
    )


def make_robot_config(args: argparse.Namespace) -> AlohaMiniClientConfig:
    config_kwargs: dict[str, Any] = {
        "remote_ip": args.remote_ip,
        "id": args.robot_id,
        "robot_model": args.robot_model,
        "command_send_timeout_ms": (
            AM1_COMMAND_SEND_TIMEOUT_MS
            if args.robot_model == "alohamini1" and args.no_keyboard and args.no_cameras
            else None
        ),
    }
    if args.no_cameras:
        config_kwargs["cameras"] = {}
    return AlohaMiniClientConfig(**config_kwargs)


def uses_decoupled_am1_live_loop(args: argparse.Namespace) -> bool:
    return args.robot_model == "alohamini1" and args.no_keyboard and args.no_cameras


def make_zero_action() -> dict[str, float | int]:
    """Return only supported velocity keys; no lift height target is fabricated."""
    return {
        "x.vel": 0.0,
        "y.vel": 0.0,
        "theta.vel": 0.0,
        "lift_axis.vel": 0,
    }


def load_rerun_functions() -> tuple[Callable[..., None], Callable[..., None], Callable[[], None]]:
    """Import repository visualization helpers only when visualization is enabled."""
    from lerobot.utils.visualization_utils import init_rerun, log_rerun_data
    from lerobot.utils.rerun_visualization import shutdown_rerun

    return init_rerun, log_rerun_data, shutdown_rerun


def _attempt_cleanup(
    errors: list[tuple[str, BaseException]],
    label: str,
    cleanup: Callable[[], Any],
) -> None:
    try:
        cleanup()
    except BaseException as exc:
        errors.append((label, exc))


def _print_connection_summary(args: argparse.Namespace) -> None:
    print("Aloha Mini client ready:")
    print(f"  Pi address: {args.remote_ip}")
    print(f"  Robot model: {args.robot_model}")
    print(f"  Left leader port: {args.left_port}")
    print(f"  Right leader port: {args.right_port}")
    print(f"  Leader profile: {args.arm_profile}")
    print(f"  FPS: {args.fps}")
    print(f"  Cameras: {'disabled' if args.no_cameras else 'enabled'}")
    if args.robot_model == "alohamini1":
        print(f"  Live arm scope: {args.live_arm_scope}")
    print(f"  Keyboard: {'disabled' if args.no_keyboard else 'enabled'}")
    print(f"  Visualization: {'disabled' if args.no_rerun else 'enabled'}")
    print("  Action space: body joints -100..100; grippers 0..100")
    print("No leader action has yet been forwarded.")


def run_am1_live_sender(
    robot: Any,
    leader: Any,
    *,
    initial_arm_target: Mapping[str, float],
    initial_observation_sequence: int,
    fps: int,
    duration_s: float,
    live_arm_scope: LiveArmScope,
    profile_cadence: bool,
    initial_follower_positions: Mapping[str, float] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    wall_time_ns: Callable[[], int] = time.time_ns,
    sleep_fn: Callable[[float], None] = precise_sleep,
    should_stop: Callable[[], bool] | None = None,
    sample_callback: Callable[[AM1LiveSample], None] | None = None,
) -> None:
    """Read devices on the caller thread while a private worker sends live actions."""
    approved_target = extract_am1_arm_positions(
        dict(initial_arm_target),
        source="approved initial live target",
        leader_sample=False,
    )
    validate_selected_sync_positions(
        approved_target,
        AM1_ARM_POSITION_KEYS,
        source="approved initial live target",
    )
    follower_hold_target = (
        extract_am1_arm_positions(
            dict(initial_follower_positions),
            source="approved initial follower observation",
            leader_sample=False,
        )
        if initial_follower_positions is not None
        else None
    )
    if follower_hold_target is not None:
        validate_selected_sync_positions(
            follower_hold_target,
            AM1_ARM_POSITION_KEYS,
            source="approved initial follower observation",
        )
    hold_target = approved_target
    safe_target = dict(approved_target)
    observed_positions = None if follower_hold_target is None else dict(follower_hold_target)
    initial_action = make_am1_live_action(safe_target)

    sender = AM1LiveActionSender(
        robot,
        initial_action=initial_action,
        initial_observation_sequence=initial_observation_sequence,
        fps=fps,
        duration_s=duration_s,
        profile_cadence=profile_cadence,
        monotonic=monotonic,
        wall_time_ns=wall_time_ns,
        sleep_fn=sleep_fn,
    )
    sender_started = False
    last_used_observation_sequence = initial_observation_sequence
    observation_timeout_count = 0
    terminal_stale_reason: str | None = None
    started_at = monotonic()
    latest_observed_at: float | None = started_at if follower_hold_target is not None else None
    last_fresh_observed_at = started_at

    try:
        sender.start()
        sender_started = True
        while sender.is_alive():
            sender_snapshot = sender.snapshot()
            if sender_snapshot.error is not None:
                break
            if should_stop is not None and should_stop():
                sender.stop()
                break

            if terminal_stale_reason is not None:
                if duration_s == 0:
                    sender.stop()
                else:
                    sender.wait(0.01)
                continue

            try:
                sample = read_fresh_am1_live_sample(
                    robot,
                    leader,
                    previous_sequence=last_used_observation_sequence,
                    monotonic=monotonic,
                )
            except TransientFollowerObservation as exc:
                observation_timeout_count += 1
                observation_age_s = max(0.0, monotonic() - last_fresh_observed_at)
                if observation_age_s >= AM1_LIVE_OBSERVATION_MAX_AGE_S:
                    terminal_stale_reason = (
                        f"follower observation age {observation_age_s:.3f}s reached the "
                        f"{AM1_LIVE_OBSERVATION_MAX_AGE_S:.1f}-second freshness limit; {exc}"
                    )
                    print(
                        "STALE FOLLOWER OBSERVATION — holding the last safe complete arm target for "
                        f"the remainder of this process: {terminal_stale_reason}"
                    )
                continue
            except StaleFollowerObservation as exc:
                terminal_stale_reason = str(exc)
                print(
                    "STALE FOLLOWER OBSERVATION — holding the last safe complete arm target for "
                    f"the remainder of this process: {terminal_stale_reason}"
                )
                continue

            safe_target = apply_am1_commissioning_scope(
                sample.arm_target,
                hold_target,
                scope=live_arm_scope,
            )
            sender.publish(make_am1_live_action(safe_target))
            observed_positions = dict(sample.follower_positions)
            latest_observed_at = sample.observed_at
            last_fresh_observed_at = sample.observed_at
            last_used_observation_sequence = sample.observation_sequence
            if sample_callback is not None:
                sample_callback(sample)
    finally:
        primary_error = sys.exception()
        diagnostic_errors: list[tuple[str, BaseException]] = []
        join_error: BaseException | None = None
        if sender_started:
            if primary_error is not None:
                sender.stop()
            try:
                sender.join()
            except BaseException as exc:
                join_error = exc
        final_snapshot = sender.snapshot()
        if profile_cadence:
            try:
                report_now = monotonic()
                print(
                    json.dumps(
                        {
                            "event": "am1_client_action_cadence",
                            "live_end_wall_time_ns": final_snapshot.live_end_wall_time_ns,
                            "action_sequence": final_snapshot.action_sequence,
                            "action_send_interval_ms": round(final_snapshot.action_send_interval_ms, 3),
                            "longest_action_send_interval_ms": round(
                                final_snapshot.longest_action_send_interval_ms,
                                3,
                            ),
                            "observation_sequence": last_used_observation_sequence,
                            "observation_age_ms": (
                                None
                                if latest_observed_at is None
                                else round(max(0.0, report_now - latest_observed_at) * 1e3, 3)
                            ),
                            "observation_timeout_count": observation_timeout_count,
                            "stale_latched": terminal_stale_reason is not None,
                            "right_wrist_requested": safe_target[RIGHT_WRIST_FLEX_KEY],
                            "right_wrist_observed": (
                                None
                                if observed_positions is None
                                else observed_positions[RIGHT_WRIST_FLEX_KEY]
                            ),
                        },
                        sort_keys=True,
                    )
                )
            except BaseException as exc:
                diagnostic_errors.append(("emitting the final cadence report", exc))

        if primary_error is not None:
            if join_error is not None and join_error is not primary_error:
                primary_error.add_note(f"Cleanup failed while joining AM1 live action sender: {join_error!r}")
            if final_snapshot.error is not None and final_snapshot.error is not primary_error:
                primary_error.add_note(f"AM1 live action sender also failed: {final_snapshot.error!r}")
            for label, exc in diagnostic_errors:
                if exc is not primary_error:
                    primary_error.add_note(f"Cadence diagnostics failed while {label}: {exc!r}")
        else:
            outcome_error = join_error or final_snapshot.error
            if outcome_error is None and terminal_stale_reason is not None:
                outcome_error = SafetyRefusal(terminal_stale_reason)
            if outcome_error is not None:
                for label, exc in diagnostic_errors:
                    if exc is not outcome_error:
                        outcome_error.add_note(f"Cadence diagnostics failed while {label}: {exc!r}")
                raise outcome_error
            if diagnostic_errors:
                _, outcome_error = diagnostic_errors[0]
                for label, exc in diagnostic_errors[1:]:
                    if exc is not outcome_error:
                        outcome_error.add_note(f"Cadence diagnostics also failed while {label}: {exc!r}")
                raise outcome_error


def run_teleoperation(
    args: argparse.Namespace,
    *,
    input_fn: Callable[[str], str] = input,
    monotonic: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], None] = precise_sleep,
) -> int:
    robot = None
    leader = None
    keyboard = None
    robot_connected = False
    left_leader_connected = False
    right_leader_connected = False
    keyboard_connected = False
    visualization_started = False
    log_rerun_data = None
    shutdown_rerun = None
    pending_arm_action: dict[str, float] | None = None
    pending_observation: dict[str, Any] = {}
    teleoperation_active_announced = False

    try:
        if not args.no_robot:
            robot = AlohaMiniClient(make_robot_config(args))
            robot.connect()
            robot_connected = True
            robot.send_action(make_zero_action())
        else:
            print("NO_ROBOT: robot client construction and connection skipped.")

        if not args.no_leader:
            leader = BiSOLeader(make_leader_config(args))
            if args.require_calibration_match:
                left_leader_connected = True
                leader.left_arm.connect(calibrate=False)
                try:
                    if not leader.left_arm.is_calibrated:
                        raise SafetyRefusal("left leader calibration is missing or does not match the connected arm; refusing without calibration")
                except SafetyRefusal as exc:
                    print(f"SAFETY REFUSAL: {exc}")
                    return 2
                right_leader_connected = True
                leader.right_arm.connect(calibrate=False)
                try:
                    if not leader.right_arm.is_calibrated:
                        raise SafetyRefusal("right leader calibration is missing or does not match the connected arm; refusing without calibration")
                except SafetyRefusal as exc:
                    print(f"SAFETY REFUSAL: {exc}")
                    return 2
            else:
                leader.left_arm.connect()
                left_leader_connected = True
                leader.right_arm.connect()
                right_leader_connected = True
        else:
            print("NO_LEADER: leader construction and connection skipped.")

        if args.robot_model == "alohamini1" and robot_connected and right_leader_connected:
            try:
                if args.startup_mode == "strict":
                    pending_arm_action, pending_observation = run_alignment_gate(
                        robot,
                        leader,
                        args.max_start_mismatch,
                    )
                    if args.check_alignment_only:
                        print("Alignment check passed; no arm action was sent.")
                        return 0
                else:
                    pending_arm_action, pending_observation = run_startup_sync(
                        robot,
                        leader,
                        side=args.startup_sync_side,
                        requested_duration_s=args.startup_sync_duration_s,
                        fps=args.fps,
                        max_start_mismatch=args.max_start_mismatch,
                        input_fn=input_fn,
                        monotonic=monotonic,
                        sleep_fn=sleep_fn,
                    )
                    print("SYNCHRONIZATION COMPLETE")
                    if args.startup_sync_only:
                        return 0
            except SafetyRefusal as exc:
                print(f"SAFETY REFUSAL: {exc}")
                return 2

        if not args.no_keyboard:
            keyboard = KeyboardTeleop(KeyboardTeleopConfig(id="my_laptop_keyboard"))
            keyboard.connect()
            keyboard_connected = keyboard.is_connected
            if not keyboard_connected:
                raise RuntimeError("Keyboard control was enabled, but the keyboard listener did not connect.")

        if not args.no_rerun:
            init_rerun, log_rerun_data, shutdown_rerun = load_rerun_functions()
            visualization_started = True
            init_rerun(session_name="alohamini_teleop")

        if args.start_paused:
            if robot_connected:
                robot.send_action(make_zero_action())
            _print_connection_summary(args)
            if args.robot_model == "alohamini1":
                print("PRESS ENTER TO ENABLE LIVE TELEOPERATION")
                input_fn("")
            else:
                input_fn("Press Enter to begin forwarding leader actions... ")
            if args.robot_model == "alohamini1" and robot_connected and right_leader_connected:
                try:
                    pending_arm_action, pending_observation = run_alignment_gate(
                        robot,
                        leader,
                        args.max_start_mismatch,
                    )
                except SafetyRefusal as exc:
                    print(f"SAFETY REFUSAL: {exc}")
                    return 2

        if (
            uses_decoupled_am1_live_loop(args)
            and robot_connected
            and right_leader_connected
            and pending_arm_action is not None
        ):
            initial_follower_positions = extract_am1_arm_positions(
                pending_observation,
                source="approved initial follower observation",
                leader_sample=False,
            )

            def stop_requested() -> bool:
                if not keyboard_connected:
                    return False
                keyboard_keys = keyboard.get_action()
                quit_key = robot.config.teleop_keys.get("quit", "q")
                return quit_key in keyboard_keys

            sample_callback = None
            if log_rerun_data is not None:
                approved_target = dict(pending_arm_action)

                def log_live_sample(sample: AM1LiveSample) -> None:
                    scoped = apply_am1_commissioning_scope(
                        sample.arm_target,
                        approved_target,
                        scope=args.live_arm_scope,
                    )
                    log_rerun_data(dict(sample.observation), make_am1_live_action(scoped))

                sample_callback = log_live_sample

            print("TELEOPERATION ACTIVE — LEADER MOVEMENT IS NOW ALLOWED")
            teleoperation_active_announced = True
            try:
                run_am1_live_sender(
                    robot,
                    leader,
                    initial_arm_target=pending_arm_action,
                    initial_observation_sequence=robot.observation_sequence,
                    initial_follower_positions=initial_follower_positions,
                    fps=args.fps,
                    duration_s=args.duration_s,
                    live_arm_scope=args.live_arm_scope,
                    profile_cadence=args.profile_cadence,
                    monotonic=monotonic,
                    sleep_fn=sleep_fn,
                    should_stop=stop_requested,
                    sample_callback=sample_callback,
                )
            except SafetyRefusal as exc:
                print(f"SAFETY REFUSAL: {exc}")
                return 2
            return 0

        started_at = monotonic()
        while True:
            if args.duration_s > 0 and monotonic() - started_at >= args.duration_s:
                break

            loop_started_at = time.perf_counter()
            keyboard_keys = keyboard.get_action() if keyboard_connected else {}
            if keyboard_connected:
                quit_key = robot.config.teleop_keys.get("quit", "q") if robot is not None else "q"
                if quit_key in keyboard_keys:
                    break

            forwarding_pending_sample = pending_arm_action is not None
            if forwarding_pending_sample:
                observation = pending_observation
                arm_action = pending_arm_action
                pending_arm_action = None
                pending_observation = {}
            else:
                observation = robot.get_observation() if robot_connected else {}
                raw_arm_action = leader.get_action() if right_leader_connected else {}
                if args.robot_model == "alohamini1" and right_leader_connected:
                    try:
                        arm_action = extract_am1_arm_positions(
                            raw_arm_action,
                            source="leader",
                            leader_sample=True,
                        )
                    except SafetyRefusal as exc:
                        print(f"SAFETY REFUSAL: {exc}")
                        return 2
                else:
                    arm_action = {f"arm_{key}": value for key, value in raw_arm_action.items()}

            if forwarding_pending_sample:
                body_action = make_zero_action()
            elif keyboard_connected and robot is not None:
                body_action = {
                    **robot._from_keyboard_to_base_action(keyboard_keys),
                    **robot._from_keyboard_to_lift_action(keyboard_keys),
                }
            else:
                body_action = make_zero_action()

            action = {**arm_action, **body_action}
            if log_rerun_data is not None:
                log_rerun_data(observation, action)
            if robot_connected:
                if (
                    args.robot_model == "alohamini1"
                    and right_leader_connected
                    and not teleoperation_active_announced
                    and any(key.startswith("arm_") for key in arm_action)
                ):
                    print("TELEOPERATION ACTIVE — LEADER MOVEMENT IS NOW ALLOWED")
                    teleoperation_active_announced = True
                robot.send_action(action)

            sleep_fn(max(1.0 / args.fps - (time.perf_counter() - loop_started_at), 0.0))
            if args.no_robot:
                print(f"[NO_ROBOT] action -> {action}")
    finally:
        primary_error = sys.exception()
        cleanup_errors: list[tuple[str, BaseException]] = []

        if robot_connected:
            _attempt_cleanup(cleanup_errors, "final robot zero command", lambda: robot.send_action(make_zero_action()))
        if keyboard_connected:
            _attempt_cleanup(cleanup_errors, "keyboard disconnect", keyboard.disconnect)
        if right_leader_connected:
            _attempt_cleanup(cleanup_errors, "right leader disconnect", leader.right_arm.disconnect)
        if left_leader_connected:
            _attempt_cleanup(cleanup_errors, "left leader disconnect", leader.left_arm.disconnect)
        if robot_connected:
            _attempt_cleanup(cleanup_errors, "robot disconnect", robot.disconnect)
        if visualization_started and shutdown_rerun is not None:
            _attempt_cleanup(cleanup_errors, "visualization shutdown", shutdown_rerun)

        print(
            "Shutdown complete: final zero requested when connected; "
            "keyboard, leader buses, robot client, and visualization cleaned up when started."
        )
        if cleanup_errors:
            if primary_error is not None:
                for label, error in cleanup_errors:
                    primary_error.add_note(f"Cleanup failed during {label}: {error!r}")
            else:
                label, error = cleanup_errors[0]
                for extra_label, extra_error in cleanup_errors[1:]:
                    error.add_note(f"Additional cleanup failure during {extra_label}: {extra_error!r}")
                raise error

    return 0


def main() -> None:
    args = parse_args()
    status = run_teleoperation(args)
    if status:
        raise SystemExit(status)


if __name__ == "__main__":
    main()
