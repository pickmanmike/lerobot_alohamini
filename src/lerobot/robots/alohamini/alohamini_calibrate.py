#!/usr/bin/env python

import argparse
import logging

from .alohamini import AlohaMini
from .config_alohamini import AlohaMiniConfig


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Calibrate AlohaMini and exit")
    parser.add_argument(
        "--robot_model",
        type=str,
        default="alohamini1",
        choices=["alohamini1", "alohamini2", "alohamini2pro"],
        help=(
            "Robot model. Must match the physical AlohaMini hardware: "
            "alohamini1, alohamini2, or alohamini2pro."
        ),
    )
    parser.add_argument(
        "--no_follower",
        action="store_true",
        help="Skip follower arm calibration.",
    )
    parser.add_argument(
        "--no_cameras",
        action="store_true",
        help="Construct the calibration robot with an empty camera configuration.",
    )
    parser.add_argument(
        "--skip_lift_home",
        action="store_true",
        help="Do not home the lift after motor calibration.",
    )
    parser.add_argument(
        "--id",
        type=str,
        default="AlohaMiniRobot",
        help="Robot ID used for the calibration file.",
    )
    return parser


def make_robot_config(args: argparse.Namespace) -> AlohaMiniConfig:
    config = AlohaMiniConfig(
        id=args.id,
        robot_model=args.robot_model,
        no_follower=args.no_follower,
    )
    if args.no_cameras:
        config.cameras = {}
    return config


def main():
    args = make_parser().parse_args()

    logging.info("Configuring AlohaMini for calibration")
    robot = AlohaMini(make_robot_config(args))

    try:
        logging.info("Connecting AlohaMini without auto-calibration")
        robot.connect(calibrate=False, activate=False, home_lift=False)
        robot.calibrate()
        if robot.is_calibrated and not args.skip_lift_home:
            result = robot.lift.home()
            print(
                f"Lift axis homed to a process-local 0mm reference "
                f"({result.stop_reason}, {result.elapsed_s:.2f}s)."
            )
        print("AlohaMini calibration complete.")
    finally:
        if robot.is_connected:
            robot.disconnect()


if __name__ == "__main__":
    main()
