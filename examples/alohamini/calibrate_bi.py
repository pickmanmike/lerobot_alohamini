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

"""Calibrate the two leader arms used by AlohaMini bimanual teleoperation.

Run this before ``teleoperate_bi.py`` to calibrate both arms independently of
the robot. The argument defaults intentionally match ``teleoperate_bi.py`` so
that both scripts read and write the same calibration files.
"""

import argparse
import sys
from collections.abc import Callable

from lerobot.teleoperators.bi_so_leader import BiSOLeader, BiSOLeaderConfig
from lerobot.utils.utils import init_logging

from leader_client_utils import (
    add_leader_port_arguments,
    make_normalized_bi_leader_config,
    resolve_leader_ports,
)


def parse_args(
    argv: list[str] | None = None,
    *,
    platform_name: str | None = None,
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--teleop.id",
        "--leader_id",
        dest="leader_id",
        type=str,
        default="so101_leader_bi",
        help="Leader arm device ID",
    )
    parser.add_argument(
        "--teleop.arm_profile",
        "--arm_profile",
        dest="arm_profile",
        type=str,
        default="so-arm-5dof",
        choices=["so-arm-5dof", "am-leader-6dof"],
        help="Leader arm profile selector",
    )
    add_leader_port_arguments(parser)
    args = parser.parse_args(argv)
    return resolve_leader_ports(args, parser, platform_name=platform_name)


def make_leader_config(args: argparse.Namespace) -> BiSOLeaderConfig:
    return make_normalized_bi_leader_config(
        left_port=args.left_port,
        right_port=args.right_port,
        leader_id=args.leader_id,
        arm_profile=args.arm_profile,
    )


def _record_cleanup_error(
    errors: list[tuple[str, BaseException]],
    label: str,
    cleanup: Callable[[], None],
) -> None:
    try:
        cleanup()
    except BaseException as exc:
        errors.append((label, exc))


def run_calibration(args: argparse.Namespace) -> None:
    leader = BiSOLeader(make_leader_config(args))
    left_connected = False
    right_connected = False
    try:
        leader.left_arm.connect(calibrate=False)
        left_connected = True
        leader.right_arm.connect(calibrate=False)
        right_connected = True
        leader.calibrate()
    finally:
        primary_error = sys.exception()
        cleanup_errors: list[tuple[str, BaseException]] = []
        if right_connected:
            _record_cleanup_error(cleanup_errors, "right leader disconnect", leader.right_arm.disconnect)
        if left_connected:
            _record_cleanup_error(cleanup_errors, "left leader disconnect", leader.left_arm.disconnect)

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
    init_logging()
    run_calibration(args)


if __name__ == "__main__":
    main()
