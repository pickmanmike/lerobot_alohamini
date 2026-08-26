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
import builtins
import os
import sys
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

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
    parser.add_argument(
        "--teleop.calibration_dir",
        dest="calibration_dir",
        type=Path,
        default=None,
        help="Explicit calibration leaf directory for this bimanual run",
    )
    parser.add_argument(
        "--force_fresh_calibration",
        action="store_true",
        help="Clear cached SO-101 leader calibration before the two-arm calibration run",
    )
    add_leader_port_arguments(parser)
    args = parser.parse_args(argv)
    if args.force_fresh_calibration and args.arm_profile != "so-arm-5dof":
        parser.error("--force_fresh_calibration requires --teleop.arm_profile so-arm-5dof")
    return resolve_leader_ports(args, parser, platform_name=platform_name)


def make_leader_config(args: argparse.Namespace) -> BiSOLeaderConfig:
    config = make_normalized_bi_leader_config(
        left_port=args.left_port,
        right_port=args.right_port,
        leader_id=args.leader_id,
        arm_profile=args.arm_profile,
    )
    config.calibration_dir = args.calibration_dir
    return config


def _record_cleanup_error(
    errors: list[tuple[str, BaseException]],
    label: str,
    cleanup: Callable[[], None],
) -> None:
    try:
        cleanup()
    except BaseException as exc:
        errors.append((label, exc))


class _TeeTextIO:
    def __init__(self, console: Any, transcript: Any):
        self._console = console
        self._transcript = transcript

    def write(self, text: str) -> int:
        written = self._console.write(text)
        self._transcript.write(text)
        return written

    def flush(self) -> None:
        self._console.flush()
        self._transcript.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._console, name)


@contextmanager
def _am1_calibration_transcript_from_environment() -> Iterator[None]:
    transcript_path = os.environ.get("AM1_CALIBRATION_TRANSCRIPT_PATH")
    if not transcript_path:
        yield
        return

    original_stdout = sys.stdout
    original_stderr = sys.stderr
    original_input = builtins.input
    transcript = None
    tee_stdout = None
    tee_stderr = None
    transcript_started = False

    try:
        transcript = Path(transcript_path).open("a", encoding="utf-8", buffering=1)
        transcript.write("AM1_CALIBRATION_CHILD_OUTPUT_BEGIN\n")
        transcript_started = True
        transcript.flush()
        tee_stdout = _TeeTextIO(original_stdout, transcript)
        tee_stderr = _TeeTextIO(original_stderr, transcript)

        def input_with_transcript(prompt: object = "", /) -> str:
            prompt_text = str(prompt)
            if prompt_text:
                tee_stdout.write(prompt_text)
                tee_stdout.flush()
            return original_input()

        sys.stdout = tee_stdout
        sys.stderr = tee_stderr
        builtins.input = input_with_transcript
        yield
    finally:
        primary_error = sys.exception()
        cleanup_errors: list[tuple[str, BaseException]] = []
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        _record_cleanup_error(
            cleanup_errors,
            "input restoration",
            lambda: setattr(builtins, "input", original_input),
        )
        if tee_stdout is not None:
            _record_cleanup_error(cleanup_errors, "stdout flush", tee_stdout.flush)
        if tee_stderr is not None:
            _record_cleanup_error(cleanup_errors, "stderr flush", tee_stderr.flush)
        if transcript_started and transcript is not None:
            _record_cleanup_error(
                cleanup_errors,
                "transcript completion marker",
                lambda: transcript.write("AM1_CALIBRATION_CHILD_OUTPUT_END\n"),
            )
        if transcript is not None:
            _record_cleanup_error(cleanup_errors, "transcript flush", transcript.flush)
            _record_cleanup_error(cleanup_errors, "transcript close", transcript.close)

        if cleanup_errors:
            if primary_error is not None:
                for label, error in cleanup_errors:
                    primary_error.add_note(f"Cleanup failed during {label}: {error!r}")
            else:
                label, error = cleanup_errors[0]
                for extra_label, extra_error in cleanup_errors[1:]:
                    error.add_note(
                        f"Additional cleanup failure during {extra_label}: {extra_error!r}"
                    )
                raise error


@contextmanager
def _am1_calibration_progress(arm: Any, side: str) -> Iterator[None]:
    """Expose the last completed pre-input step without changing SOLeader."""

    bus = arm.bus
    original_disable_torque = bus.disable_torque
    original_write = bus.write
    missing_attribute = object()
    original_disable_torque_attribute = vars(bus).get("disable_torque", missing_attribute)
    original_write_attribute = vars(bus).get("write", missing_attribute)
    expected_operating_mode_motors = set(bus.motors)
    completed_operating_mode_motors: set[str] = set()
    operating_mode_started = False
    waiting_marker_emitted = False

    def disable_torque_with_progress(*args: Any, **kwargs: Any) -> Any:
        print(f"AM1_CALIBRATION_PROGRESS={side}_STARTING_TORQUE_DISABLE", flush=True)
        result = original_disable_torque(*args, **kwargs)
        print(f"AM1_CALIBRATION_PROGRESS={side}_TORQUE_DISABLE_COMPLETE", flush=True)
        return result

    def write_with_progress(
        data_name: str,
        motor: str,
        value: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        nonlocal operating_mode_started, waiting_marker_emitted
        if data_name == "Operating_Mode" and not operating_mode_started:
            print(f"AM1_CALIBRATION_PROGRESS={side}_STARTING_OPERATING_MODE_WRITES", flush=True)
            operating_mode_started = True

        result = original_write(data_name, motor, value, *args, **kwargs)

        if data_name == "Operating_Mode" and motor in expected_operating_mode_motors:
            completed_operating_mode_motors.add(motor)
            if (
                not waiting_marker_emitted
                and completed_operating_mode_motors == expected_operating_mode_motors
            ):
                print(f"AM1_CALIBRATION_PROGRESS={side}_OPERATING_MODE_WRITES_COMPLETE", flush=True)
                print(f"AM1_CALIBRATION_PROGRESS={side}_WAITING_FOR_MIDDLE_POSE_ENTER", flush=True)
                waiting_marker_emitted = True
        return result

    disable_torque_installed = False
    write_installed = False
    try:
        bus.disable_torque = disable_torque_with_progress
        disable_torque_installed = True
        bus.write = write_with_progress
        write_installed = True
        yield
    finally:
        try:
            if write_installed:
                if original_write_attribute is missing_attribute:
                    del bus.write
                else:
                    bus.write = original_write_attribute
        finally:
            if disable_torque_installed:
                if original_disable_torque_attribute is missing_attribute:
                    del bus.disable_torque
                else:
                    bus.disable_torque = original_disable_torque_attribute


def _run_am1_calibration_with_progress(leader: Any) -> None:
    with ExitStack() as stack:
        stack.enter_context(_am1_calibration_progress(leader.left_arm, "LEFT"))
        stack.enter_context(_am1_calibration_progress(leader.right_arm, "RIGHT"))
        leader.calibrate()


def run_calibration(args: argparse.Namespace) -> None:
    leader = BiSOLeader(make_leader_config(args))
    if args.force_fresh_calibration:
        for arm in (leader.left_arm, leader.right_arm):
            arm.calibration.clear()
            arm.bus.calibration.clear()
    left_connected = False
    right_connected = False
    try:
        leader.left_arm.connect(calibrate=False)
        left_connected = True
        leader.right_arm.connect(calibrate=False)
        right_connected = True
        if args.force_fresh_calibration:
            _run_am1_calibration_with_progress(leader)
        else:
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
    with _am1_calibration_transcript_from_environment():
        args = parse_args()
        init_logging()
        run_calibration(args)


if __name__ == "__main__":
    main()
