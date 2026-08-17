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
import math
import sys
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

StartupSyncSide = Literal["left", "right", "both"]


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


class AlignmentRow(NamedTuple):
    joint: str
    follower_value: float
    leader_value: float
    signed_difference: float
    absolute_difference: float


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
    parser.add_argument("--no_rerun", action="store_true", help="Disable Rerun without importing visualization helpers")
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
        help="Maximum AM1 leader/follower startup difference in normalized units (default: 10.0)",
    )
    parser.add_argument(
        "--duration_s",
        type=float,
        default=0.0,
        help="Stop cleanly after this many seconds; 0 has no time limit",
    )
    parser.add_argument("--fps", type=int, default=30, help="Main loop frequency (frames per second)")
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
    return resolve_leader_ports(args, parser, platform_name=platform_name)


def make_leader_config(args: argparse.Namespace) -> BiSOLeaderConfig:
    return make_normalized_bi_leader_config(
        left_port=args.left_port,
        right_port=args.right_port,
        leader_id=args.leader_id,
        arm_profile=args.arm_profile,
    )


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
    print(f"  Keyboard: {'disabled' if args.no_keyboard else 'enabled'}")
    print(f"  Visualization: {'disabled' if args.no_rerun else 'enabled'}")
    print("  Action space: body joints -100..100; grippers 0..100")
    print("No leader action has yet been forwarded.")


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

    try:
        if not args.no_robot:
            robot = AlohaMiniClient(
                AlohaMiniClientConfig(
                    remote_ip=args.remote_ip,
                    id=args.robot_id,
                    robot_model=args.robot_model,
                )
            )
            robot.connect()
            robot_connected = True
            robot.send_action(make_zero_action())
        else:
            print("NO_ROBOT: robot client construction and connection skipped.")

        if not args.no_leader:
            leader = BiSOLeader(make_leader_config(args))
            leader.left_arm.connect()
            left_leader_connected = True
            leader.right_arm.connect()
            right_leader_connected = True
        else:
            print("NO_LEADER: leader construction and connection skipped.")

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
            if args.check_alignment_only:
                print("Alignment check passed; no arm action was sent.")
                return 0

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
