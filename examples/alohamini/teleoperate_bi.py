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
import sys
import time
from collections.abc import Callable
from typing import Any

from lerobot.robots.alohamini import AlohaMiniClient, AlohaMiniClientConfig
from lerobot.teleoperators.bi_so_leader import BiSOLeader, BiSOLeaderConfig
from lerobot.teleoperators.keyboard.teleop_keyboard import KeyboardTeleop, KeyboardTeleopConfig
from lerobot.teleoperators.so_leader import SOLeaderConfig
from lerobot.utils.robot_utils import precise_sleep

from leader_client_utils import add_leader_port_arguments, resolve_leader_ports


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no_robot", action="store_true", help="Do not construct or connect the robot client")
    parser.add_argument("--no_leader", action="store_true", help="Do not construct or connect the leader arms")
    parser.add_argument("--no_keyboard", action="store_true", help="Disable keyboard base and lift control")
    parser.add_argument("--no_rerun", action="store_true", help="Disable Rerun without importing visualization helpers")
    parser.add_argument("--start_paused", action="store_true", help="Wait for Enter before forwarding leader actions")
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
    return resolve_leader_ports(args, parser, platform_name=platform_name)


def make_leader_config(args: argparse.Namespace) -> BiSOLeaderConfig:
    return BiSOLeaderConfig(
        left_arm_config=SOLeaderConfig(port=args.left_port, arm_profile=args.arm_profile),
        right_arm_config=SOLeaderConfig(port=args.right_port, arm_profile=args.arm_profile),
        id=args.leader_id,
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
    print("No leader action has yet been forwarded.")


def run_teleoperation(
    args: argparse.Namespace,
    *,
    input_fn: Callable[[str], str] = input,
    monotonic: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], None] = precise_sleep,
) -> None:
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

            observation = robot.get_observation() if robot_connected else {}
            arm_action = leader.get_action() if right_leader_connected else {}
            arm_action = {f"arm_{key}": value for key, value in arm_action.items()}

            if keyboard_connected and robot is not None:
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


def main() -> None:
    args = parse_args()
    run_teleoperation(args)


if __name__ == "__main__":
    main()
