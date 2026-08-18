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

"""Run one bounded, AM1-only follower-joint diagnostic through the network client."""

from __future__ import annotations

import argparse
import math
import sys
import time
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


AM1_JOINTS = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
)
AM1_ARM_POSITION_KEYS = tuple(
    f"arm_{side}_{joint}.pos" for side in ("left", "right") for joint in AM1_JOINTS
)
ZERO_BODY_ACTION = {"x.vel": 0, "y.vel": 0, "theta.vel": 0, "lift_axis.vel": 0}
MAX_STEP = 0.75
MIN_MEASURABLE_CHANGE = 0.25


class SafetyRefusal(RuntimeError):
    """Expected diagnostic refusal that should exit without a traceback."""


@dataclass(frozen=True)
class JointPlan:
    selected_key: str
    follower_start: Mapping[str, float]
    start_value: float
    target_value: float
    total_steps: int
    frame_count: int
    fps: int


@dataclass(frozen=True)
class DiagnosticResult:
    outcome: str
    start_value: float
    target_value: float
    observed_value: float
    final_error: float


def _validated_follower_positions(follower_positions: Mapping[str, float]) -> dict[str, float]:
    """Return the exact, finite AM1 arm pose while ignoring legitimate body fields."""
    expected_keys = set(AM1_ARM_POSITION_KEYS)
    actual_arm_keys = {
        key for key in follower_positions if key.startswith("arm_") and key.endswith(".pos")
    }
    if actual_arm_keys != expected_keys:
        missing = sorted(expected_keys - actual_arm_keys)
        unexpected = sorted(actual_arm_keys - expected_keys)
        raise SafetyRefusal(
            "follower observation must contain exactly the 12 AM1 arm position keys; "
            f"missing={missing}, unexpected={unexpected}"
        )

    validated: dict[str, float] = {}
    for key in AM1_ARM_POSITION_KEYS:
        value = float(follower_positions[key])
        if not math.isfinite(value):
            raise SafetyRefusal(f"follower observation {key} must be finite, got {value}")
        lower = 0.0 if key.endswith("_gripper.pos") else -100.0
        if not lower <= value <= 100.0:
            expected_range = "0..100" if lower == 0.0 else "-100..100"
            raise SafetyRefusal(
                f"follower observation {key} value {value} is outside {expected_range}"
            )
        validated[key] = value
    return validated


def build_joint_plan(
    follower_positions: Mapping[str, float],
    *,
    side: str,
    joint: str,
    delta: float,
    duration_s: float,
    fps: int,
) -> JointPlan:
    selected_key = f"arm_{side}_{joint}.pos"
    follower_start = _validated_follower_positions(follower_positions)

    start_value = follower_start[selected_key]
    target_value = start_value + delta
    target_lower = 0.0 if joint == "gripper" else -100.0
    if not target_lower <= target_value <= 100.0:
        expected_range = "0..100" if target_lower == 0.0 else "-100..100"
        raise SafetyRefusal(
            f"diagnostic target {target_value} is outside {expected_range} for {selected_key}"
        )
    total_steps = max(1, math.ceil(duration_s * fps), math.ceil(abs(delta) / MAX_STEP))
    return JointPlan(
        selected_key=selected_key,
        follower_start=MappingProxyType(follower_start),
        start_value=start_value,
        target_value=target_value,
        total_steps=total_steps,
        frame_count=total_steps + 1,
        fps=fps,
    )


def build_joint_action(plan: JointPlan, frame_index: int) -> dict[str, float | int]:
    if not 0 <= frame_index <= plan.total_steps:
        raise ValueError(f"frame index {frame_index} is outside 0..{plan.total_steps}")
    alpha = frame_index / plan.total_steps
    action = dict(plan.follower_start)
    action[plan.selected_key] = plan.start_value + alpha * (plan.target_value - plan.start_value)
    return {**action, **ZERO_BODY_ACTION}


def _get_fresh_observation(robot, *, monotonic) -> Mapping[str, float]:
    previous_sequence = robot.observation_sequence
    deadline = monotonic() + robot.config.connect_timeout_s
    while True:
        observation = robot.get_observation()
        if robot.observation_sequence > previous_sequence:
            return observation
        if monotonic() >= deadline:
            raise SafetyRefusal(
                "timed out waiting for a sequence-fresh follower observation; no arm action sent"
            )


def run_joint_diagnostic(
    robot,
    *,
    side: str,
    joint: str,
    delta: float,
    duration_s: float,
    fps: int,
    max_final_error: float,
    input_fn=input,
    monotonic,
    sleep_fn,
) -> DiagnosticResult:
    """Run one confirmed, measured-pose-relative AM1 joint movement."""
    preliminary_observation = _get_fresh_observation(robot, monotonic=monotonic)
    preliminary_plan = build_joint_plan(
        preliminary_observation,
        side=side,
        joint=joint,
        delta=delta,
        duration_s=duration_s,
        fps=fps,
    )
    print("AM1 bounded single-joint diagnostic")
    print(f"Selected joint: {preliminary_plan.selected_key}")
    print(
        f"Requested movement: {delta:+.3f} normalized units over approximately "
        f"{duration_s:.3f} seconds at {fps} fps"
    )
    print("All other arm joints will be held at the final pre-move measured pose.")
    print("Base and lift commands will remain explicitly zero throughout.")
    print("Keep the physical disconnect reachable and stop for unexpected motion or resistance.")
    if input_fn("Type exactly MOVE to obtain a fresh pose and begin: ") != "MOVE":
        raise SafetyRefusal("operator did not type exactly MOVE; no arm action sent")

    final_start_observation = _get_fresh_observation(robot, monotonic=monotonic)
    plan = build_joint_plan(
        final_start_observation,
        side=side,
        joint=joint,
        delta=delta,
        duration_s=duration_s,
        fps=fps,
    )
    print(f"Measured start: {plan.selected_key}={plan.start_value:.3f}")
    print(f"Requested target: {plan.selected_key}={plan.target_value:.3f}")
    print("Host-accepted target: unavailable (the current action channel has no acknowledgement)")

    latest_observation = final_start_observation
    previous_send_complete: float | None = None
    period_s = 1.0 / fps
    for frame_index in range(plan.frame_count):
        if previous_send_complete is not None:
            remaining_s = previous_send_complete + period_s - monotonic()
            if remaining_s > 0:
                sleep_fn(remaining_s)
        action = build_joint_action(plan, frame_index)
        client_result = robot.send_action(action)
        previous_send_complete = monotonic()
        latest_observation = _get_fresh_observation(robot, monotonic=monotonic)
        latest_positions = _validated_follower_positions(latest_observation)
        requested_value = float(action[plan.selected_key])
        observed_value = latest_positions[plan.selected_key]
        returned_value = (
            float(client_result[plan.selected_key])
            if isinstance(client_result, Mapping) and plan.selected_key in client_result
            else None
        )
        returned_text = "unavailable" if returned_value is None else f"{returned_value:.3f}"
        print(
            f"Progress: frame {frame_index + 1}/{plan.frame_count}, "
            f"requested={requested_value:.3f}, client_return={returned_text}, "
            f"observed={observed_value:.3f}"
        )

    # Observe beyond the request window so the result cannot be an older queued pose.
    for _ in range(robot.config.observation_request_window + 1):
        latest_observation = _get_fresh_observation(robot, monotonic=monotonic)
        _validated_follower_positions(latest_observation)

    observed_value = _validated_follower_positions(latest_observation)[plan.selected_key]
    final_error = plan.target_value - observed_value
    observed_delta = observed_value - plan.start_value
    if abs(final_error) <= max_final_error:
        outcome = "PASS"
    elif abs(observed_delta) <= MIN_MEASURABLE_CHANGE:
        outcome = "NO_MEASURABLE_MOVEMENT"
    elif observed_delta * delta < 0:
        outcome = "WRONG_DIRECTION"
    else:
        outcome = "INCOMPLETE"
    print(f"Observed position: {plan.selected_key}={observed_value:.3f}")
    print(f"Final error: {final_error:+.3f}")
    print(f"Outcome: {outcome}")
    return DiagnosticResult(
        outcome=outcome,
        start_value=plan.start_value,
        target_value=plan.target_value,
        observed_value=observed_value,
        final_error=final_error,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--robot.remote_ip", "--remote_ip", dest="remote_ip", default="127.0.0.1")
    parser.add_argument("--robot.id", "--robot_id", dest="robot_id", default="my_alohamini")
    parser.add_argument("--side", choices=("left", "right"), default="left")
    parser.add_argument("--joint", choices=AM1_JOINTS, default="elbow_flex")
    parser.add_argument("--delta", type=float, default=-10.0)
    parser.add_argument("--fps", type=int, default=5)
    parser.add_argument("--duration_s", type=float, default=5.0)
    parser.add_argument("--max_final_error", type=float, default=1.0)
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not math.isfinite(args.delta) or args.delta == 0 or abs(args.delta) > 10.0:
        parser.error("--delta must be finite, nonzero, and no larger than 10.0")
    if not 1 <= args.fps <= 10:
        parser.error("--fps must be between 1 and 10")
    if not math.isfinite(args.duration_s) or not 0.2 <= args.duration_s <= 10.0:
        parser.error("--duration_s must be finite and between 0.2 and 10.0")
    if not math.isfinite(args.max_final_error) or not 0 < args.max_final_error <= 5.0:
        parser.error("--max_final_error must be finite, greater than zero, and no larger than 5.0")
    return args


def _make_robot(args: argparse.Namespace):
    from lerobot.robots.alohamini import AlohaMiniClient, AlohaMiniClientConfig

    return AlohaMiniClient(
        AlohaMiniClientConfig(
            remote_ip=args.remote_ip,
            id=args.robot_id,
            robot_model="alohamini1",
            cameras={},
        )
    )


def run_diagnostic(
    args: argparse.Namespace,
    *,
    robot_factory=None,
    input_fn=input,
    monotonic=time.monotonic,
    sleep_fn=time.sleep,
) -> int:
    """Own client connection and cleanup while preserving the primary failure."""
    factory = _make_robot if robot_factory is None else robot_factory
    robot = None
    connected = False
    primary_failure: BaseException | None = None
    status = 0
    try:
        robot = factory(args)
        robot.connect()
        connected = True
        robot.send_action(dict(ZERO_BODY_ACTION))
        result = run_joint_diagnostic(
            robot,
            side=args.side,
            joint=args.joint,
            delta=args.delta,
            duration_s=args.duration_s,
            fps=args.fps,
            max_final_error=args.max_final_error,
            input_fn=input_fn,
            monotonic=monotonic,
            sleep_fn=sleep_fn,
        )
        status = 0 if result.outcome == "PASS" else 1
    except SafetyRefusal as exc:
        primary_failure = exc
        print(f"SAFETY REFUSAL: {exc}")
        status = 2
    except BaseException as exc:
        primary_failure = exc
        raise
    finally:
        cleanup_errors: list[tuple[str, BaseException]] = []
        if connected:
            try:
                robot.send_action(dict(ZERO_BODY_ACTION))
            except BaseException as exc:
                cleanup_errors.append(("final body zero", exc))
            try:
                robot.disconnect()
            except BaseException as exc:
                cleanup_errors.append(("client disconnect", exc))

        if cleanup_errors:
            if primary_failure is not None:
                for label, error in cleanup_errors:
                    primary_failure.add_note(f"Cleanup failed during {label}: {error!r}")
                    if isinstance(primary_failure, SafetyRefusal):
                        print(f"CLEANUP ERROR during {label}: {error!r}")
            else:
                label, error = cleanup_errors[0]
                for extra_label, extra_error in cleanup_errors[1:]:
                    error.add_note(
                        f"Additional cleanup failure during {extra_label}: {extra_error!r}"
                    )
                raise error

        if connected:
            print("Cleanup complete: final base/lift zero requested and client disconnected.")
    return status


def main(argv: list[str] | None = None) -> int:
    return run_diagnostic(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
