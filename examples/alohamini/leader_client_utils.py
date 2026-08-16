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

"""Shared command-line handling for the two Aloha Mini leader-arm ports."""

from __future__ import annotations

import argparse
import platform

from lerobot.teleoperators.bi_so_leader import BiSOLeaderConfig
from lerobot.teleoperators.so_leader import SOLeaderConfig


DEFAULT_LEFT_LEADER_PORT = "/dev/am_arm_leader_left"
DEFAULT_RIGHT_LEADER_PORT = "/dev/am_arm_leader_right"


def make_normalized_bi_leader_config(
    *,
    left_port: str,
    right_port: str,
    leader_id: str,
    arm_profile: str,
) -> BiSOLeaderConfig:
    """Build Aloha Mini leader arms in the followers' normalized action space."""
    return BiSOLeaderConfig(
        left_arm_config=SOLeaderConfig(
            port=left_port,
            arm_profile=arm_profile,
            use_degrees=False,
        ),
        right_arm_config=SOLeaderConfig(
            port=right_port,
            arm_profile=arm_profile,
            use_degrees=False,
        ),
        id=leader_id,
    )


def add_leader_port_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--teleop.left_port",
        "--left_port",
        dest="left_port",
        default=None,
        help=f"Left leader serial port (POSIX default: {DEFAULT_LEFT_LEADER_PORT})",
    )
    parser.add_argument(
        "--teleop.right_port",
        "--right_port",
        dest="right_port",
        default=None,
        help=f"Right leader serial port (POSIX default: {DEFAULT_RIGHT_LEADER_PORT})",
    )


def resolve_leader_ports(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    *,
    platform_name: str | None = None,
) -> argparse.Namespace:
    """Require explicit Windows ports and retain the existing POSIX aliases."""
    platform_name = platform.system() if platform_name is None else platform_name
    if platform_name == "Windows" and (args.left_port is None or args.right_port is None):
        parser.error(
            "Windows requires both leader ports. Example: "
            "--teleop.left_port COM5 --teleop.right_port COM6"
        )

    if args.left_port is None:
        args.left_port = DEFAULT_LEFT_LEADER_PORT
    if args.right_port is None:
        args.right_port = DEFAULT_RIGHT_LEADER_PORT
    return args
