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

from __future__ import annotations

import argparse
import logging
import sys

from .alohamini import AlohaMini
from .config_alohamini import AlohaMiniConfig


def positive_speed_raw(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= 1300:
        raise argparse.ArgumentTypeError("Expected raw speed in the range 1..1300.")
    return parsed


def bounded_timeout(value: str) -> float:
    parsed = float(value)
    if not 0 < parsed <= 120:
        raise argparse.ArgumentTypeError("Expected timeout in the range (0, 120] seconds.")
    return parsed


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Commission the Aloha Mini 1 lift with one bounded home")
    parser.add_argument(
        "--robot_model",
        default="alohamini1",
        choices=["alohamini1"],
        help="Physical robot model (this commissioning command supports Aloha Mini 1 only).",
    )
    parser.add_argument(
        "--no_cameras",
        action="store_true",
        help="Construct the robot with an empty camera configuration.",
    )
    parser.add_argument(
        "--speed_raw",
        type=positive_speed_raw,
        default=200,
        help="Positive raw downward homing velocity (default: 200).",
    )
    parser.add_argument(
        "--timeout_s",
        type=bounded_timeout,
        default=20.0,
        help="Maximum homing duration in seconds (default: 20).",
    )
    return parser


def make_robot_config(args: argparse.Namespace) -> AlohaMiniConfig:
    config = AlohaMiniConfig(
        id="AlohaMiniRobot",
        robot_model=args.robot_model,
        # The commissioning command needs only the left body bus. This excludes
        # both follower arms and the entire right bus from activation/configuration.
        no_follower=True,
    )
    if args.no_cameras:
        config.cameras = {}
    return config


def run(args: argparse.Namespace) -> int:
    robot = AlohaMini(make_robot_config(args))
    print("Aloha Mini 1 lift commissioning")
    print("Direction: positive raw velocity = physically DOWN; negative raw velocity = physically UP")
    print(f"Homing speed: +{args.speed_raw} raw (DOWN)")
    print(f"Timeout: {args.timeout_s:.2f}s")
    print(f"Current threshold: {robot.lift.cfg.home_stall_current_ma:.1f}mA")
    print("Zero reference: process-local; normal host startup must home again")

    result_code = 1
    try:
        # No auto-calibration and no normal arm/base activation. LiftAxis.home()
        # independently performs zero-before-torque and bounded lift-only activation.
        robot.connect(calibrate=False, activate=False, home_lift=False)
        result = robot.lift.home(speed_raw=args.speed_raw, timeout_s=args.timeout_s)
        print(
            f"[OK] Lift home result: {result.stop_reason}; elapsed={result.elapsed_s:.2f}s; "
            f"position_raw={result.final_position_raw}; peak_current={result.peak_current_ma:.1f}mA"
        )
        result_code = 0
    except KeyboardInterrupt:
        print("[STOP] Lift commissioning interrupted; safe cleanup requested.", file=sys.stderr)
        result_code = 130
    except Exception as error:
        print(f"[FAIL] Lift home result: {type(error).__name__}: {error}", file=sys.stderr)
        result_code = 1
    finally:
        cleanup_errors = robot._safe_shutdown(close_buses=True)
        if cleanup_errors:
            print(
                f"[FAIL] Cleanup completed with issues: {'; '.join(cleanup_errors)}",
                file=sys.stderr,
            )
            result_code = 1
        else:
            print("[SAFE] Cleanup completed; any connected motors were zeroed and torque-disabled.")

    return result_code


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    return run(make_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
