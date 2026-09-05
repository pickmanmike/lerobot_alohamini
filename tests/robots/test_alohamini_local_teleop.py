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

import importlib.util
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from lerobot.robots.alohamini.alohamini_client import AlohaMiniClient
from lerobot.robots.alohamini.config_alohamini import AlohaMiniClientConfig


REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_ROOT = REPO_ROOT / "examples" / "alohamini"
ARM_KEYS = (
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
FOLLOWER = {key: float(index) for index, key in enumerate(ARM_KEYS)}
LEADER = {key.removeprefix("arm_"): value for key, value in FOLLOWER.items()}


def load_teleoperate_module():
    module_name = f"test_am1_local_teleoperate_{id(object())}"
    spec = importlib.util.spec_from_file_location(module_name, EXAMPLES_ROOT / "teleoperate_bi.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(EXAMPLES_ROOT))
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
        sys.path.remove(str(EXAMPLES_ROOT))
    return module


def local_cli_args(*extra: str) -> list[str]:
    return [
        "--local_mode",
        "--left_port",
        "COM8",
        "--right_port",
        "COM7",
        "--startup_mode",
        "sync",
        "--startup_sync_duration_s",
        "120",
        "--max_start_mismatch",
        "10",
        "--start_paused",
        "--no_cameras",
        "--no_rerun",
        "--profile_cadence",
        "--fps",
        "10",
        "--duration_s",
        "30",
        *extra,
    ]


def local_cli_without(flag: str) -> list[str]:
    arguments = local_cli_args()
    arguments.remove(flag)
    return arguments


def test_local_cli_selects_the_decoupled_am1_path_with_bounded_send_timeout():
    module = load_teleoperate_module()

    args = module.parse_args(local_cli_args(), platform_name="Windows")
    config = module.make_robot_config(args)

    assert args.local_mode is True
    assert args.no_keyboard is False
    assert args.left_port == "COM8"
    assert args.right_port == "COM7"
    assert config.cameras == {}
    assert config.command_send_timeout_ms == module.AM1_COMMAND_SEND_TIMEOUT_MS
    assert module.uses_decoupled_am1_live_loop(args) is True


@pytest.mark.parametrize(
    ("arguments", "reason"),
    [
        (local_cli_without("--start_paused"), "--local_mode requires --start_paused"),
        (local_cli_without("--no_cameras"), "--local_mode requires --no_cameras"),
        (local_cli_without("--no_rerun"), "--local_mode requires --no_rerun"),
        (local_cli_without("--profile_cadence"), "--local_mode requires --profile_cadence"),
        (local_cli_args("--no_robot"), "--local_mode requires a robot connection"),
        (local_cli_args("--no_leader"), "--local_mode requires both leader connections"),
        (local_cli_args("--no_keyboard"), "--local_mode requires keyboard control"),
        (local_cli_args("--startup_mode", "strict"), "--local_mode requires --startup_mode sync"),
        (local_cli_args("--startup_sync_duration_s", "119"), "--local_mode requires --startup_sync_duration_s 120"),
        (local_cli_args("--max_start_mismatch", "9"), "--local_mode requires --max_start_mismatch 10"),
        (local_cli_args("--fps", "5"), "--local_mode requires --fps 10"),
        (local_cli_args("--duration_s", "31"), "--local_mode requires --duration_s greater than 0 and no more than 30"),
        (local_cli_args("--live_arm_scope", "right_wrist_flex"), "--local_mode requires --live_arm_scope both"),
        (
            local_cli_args("--startup_sync_only"),
            "--local_mode cannot be combined with --startup_sync_only or --check_alignment_only",
        ),
        (
            local_cli_args("--check_alignment_only"),
            "--local_mode cannot be combined with --startup_sync_only or --check_alignment_only",
        ),
        (local_cli_args("--base_only"), "--local_mode cannot be combined with --base_only or --lift_only"),
    ],
)
def test_local_cli_requires_the_physically_proven_bounded_shape(capsys, arguments, reason):
    module = load_teleoperate_module()

    with pytest.raises(SystemExit) as caught:
        module.parse_args(arguments, platform_name="Windows")

    assert caught.value.code == 2
    assert reason in capsys.readouterr().err


@pytest.mark.parametrize("robot_model", ["alohamini2", "alohamini2pro"])
def test_local_cli_is_isolated_from_am2_models(capsys, robot_model):
    module = load_teleoperate_module()

    with pytest.raises(SystemExit) as caught:
        module.parse_args(local_cli_args("--robot_model", robot_model), platform_name="Windows")

    assert caught.value.code == 2
    assert "--local_mode is supported only for alohamini1" in capsys.readouterr().err


def test_local_body_action_reuses_proven_base_and_lift_mappings_without_speed_changes():
    module = load_teleoperate_module()
    robot = AlohaMiniClient(AlohaMiniClientConfig(remote_ip="127.0.0.1", cameras={}))

    moving = module.make_local_body_action(robot, {"w", "u", "t", "g"})
    released = module.make_local_body_action(robot, set())

    assert moving == {"x.vel": 0.15, "y.vel": 0.0, "theta.vel": 0.0, "lift_axis.vel": 200}
    assert released == module.make_zero_action()
    assert robot.speed_index == 0


def test_local_body_mailbox_expires_nonzero_motion_at_250_ms_and_release_is_immediate():
    module = load_teleoperate_module()
    mailbox = module.AM1LiveBodyMailbox(max_age_s=0.25)
    moving = {"x.vel": 0.15, "y.vel": 0.0, "theta.vel": 0.0, "lift_axis.vel": 200}

    mailbox.publish(moving, published_at=1.0)
    assert mailbox.snapshot(now=1.249) == moving
    assert mailbox.snapshot(now=1.25) == module.make_zero_action()
    assert mailbox.expiration_count == 1

    mailbox.publish(moving, published_at=2.0)
    mailbox.publish(module.make_zero_action(), published_at=2.01)
    assert mailbox.snapshot(now=2.01) == module.make_zero_action()
    assert mailbox.expiration_count == 1


class DelegatingLiveCommandSender:
    def __init__(self, robot):
        self.robot = robot

    def __enter__(self):
        return self

    def send_action(self, action):
        self.robot.send_action(action)

    def __exit__(self, exc_type, exc, traceback):
        return False


def test_local_sender_first_sends_zero_body_then_expires_body_while_observation_stalls():
    module = load_teleoperate_module()

    class Robot:
        def __init__(self):
            self.observation_sequence = 8
            self.sends = []

        def make_live_command_sender(self):
            return DelegatingLiveCommandSender(self)

        def send_action(self, action):
            self.sends.append((time.monotonic(), dict(action)))

        def get_observation(self):
            time.sleep(0.6)
            self.observation_sequence += 1
            return dict(FOLLOWER)

    class Leader:
        def get_action(self):
            return dict(LEADER)

    robot = Robot()
    moving_body = {"x.vel": 0.15, "y.vel": 0.0, "theta.vel": 0.0, "lift_axis.vel": 200}

    module.run_am1_live_sender(
        robot,
        Leader(),
        initial_arm_target=FOLLOWER,
        initial_observation_sequence=robot.observation_sequence,
        fps=10,
        duration_s=0.45,
        live_arm_scope="both",
        profile_cadence=False,
        body_action_supplier=lambda: moving_body,
    )

    actions = [action for _, action in robot.sends]
    intervals = [right[0] - left[0] for left, right in zip(robot.sends, robot.sends[1:], strict=False)]
    assert actions[0] == {**FOLLOWER, **module.make_zero_action()}
    assert any(action["x.vel"] > 0 and action["lift_axis.vel"] > 0 for action in actions[1:])
    assert actions[-1] == {**FOLLOWER, **module.make_zero_action()}
    assert all({key: action[key] for key in ARM_KEYS} == FOLLOWER for action in actions)
    assert max(intervals) < 0.25
    assert min(intervals) >= 0.075


def test_local_sender_expires_body_if_the_input_supplier_itself_stalls():
    module = load_teleoperate_module()

    class Robot:
        def __init__(self):
            self.observation_sequence = 8
            self.sends = []

        def make_live_command_sender(self):
            return DelegatingLiveCommandSender(self)

        def send_action(self, action):
            self.sends.append((time.monotonic(), dict(action)))

        def get_observation(self):
            self.observation_sequence += 1
            return dict(FOLLOWER)

    class Leader:
        def get_action(self):
            return dict(LEADER)

    moving_body = {"x.vel": 0.15, "y.vel": 0.0, "theta.vel": 0.0, "lift_axis.vel": 200}
    supplier_calls = 0

    def stalled_supplier():
        nonlocal supplier_calls
        supplier_calls += 1
        if supplier_calls > 1:
            time.sleep(0.6)
        return moving_body

    robot = Robot()
    module.run_am1_live_sender(
        robot,
        Leader(),
        initial_arm_target=FOLLOWER,
        initial_observation_sequence=robot.observation_sequence,
        fps=10,
        duration_s=0.45,
        live_arm_scope="both",
        profile_cadence=False,
        body_action_supplier=stalled_supplier,
    )

    actions = [action for _, action in robot.sends]
    assert actions[0] == {**FOLLOWER, **module.make_zero_action()}
    assert any(action["x.vel"] > 0 for action in actions[1:])
    assert actions[-1] == {**FOLLOWER, **module.make_zero_action()}


def test_local_session_uses_the_decoupled_sender_and_holds_body_zero_through_both_gates(monkeypatch):
    module = load_teleoperate_module()
    captured = {}
    events = []

    class Robot:
        instance = None

        def __init__(self, config):
            type(self).instance = self
            self.config = config
            self.observation_sequence = 0
            self.is_connected = False
            self.actions = []

        def connect(self):
            self.is_connected = True

        def send_action(self, action):
            self.actions.append(dict(action))

        def disconnect(self):
            self.is_connected = False

        def _from_keyboard_to_base_action(self, keys):
            return {"x.vel": 0.15 if "w" in keys else 0.0, "y.vel": 0.0, "theta.vel": 0.0}

    class Arm:
        def connect(self):
            return None

        def disconnect(self):
            return None

    class Leader:
        def __init__(self, config):
            self.left_arm = Arm()
            self.right_arm = Arm()

    class Keyboard:
        def __init__(self, config):
            self.is_connected = False

        def connect(self):
            self.is_connected = True

        def get_action(self):
            return {"w", "u"}

        def disconnect(self):
            self.is_connected = False

    def startup_sync(robot, leader, **kwargs):
        robot.observation_sequence = 1
        events.append("sync")
        return dict(FOLLOWER), dict(FOLLOWER), 1.0

    def alignment_gate(robot, leader, max_start_mismatch, *, monotonic):
        robot.observation_sequence = 2
        events.append("post_enter_gate")
        return dict(FOLLOWER), dict(FOLLOWER), 2.0

    def live_sender(robot, leader, **kwargs):
        captured.update(kwargs)
        events.append("decoupled_live")
        assert kwargs["body_action_supplier"]() == {
            "x.vel": 0.15,
            "y.vel": 0.0,
            "theta.vel": 0.0,
            "lift_axis.vel": 200,
        }

    monkeypatch.setattr(module, "AlohaMiniClient", Robot)
    monkeypatch.setattr(module, "BiSOLeader", Leader)
    monkeypatch.setattr(module, "KeyboardTeleop", Keyboard)
    monkeypatch.setattr(module, "run_startup_sync", startup_sync)
    monkeypatch.setattr(module, "run_alignment_gate", alignment_gate)
    monkeypatch.setattr(module, "run_am1_live_sender", live_sender)
    monkeypatch.setattr(
        module,
        "load_rerun_functions",
        lambda: (_ for _ in ()).throw(AssertionError("Local mode must not load visualization")),
    )
    args = module.parse_args(local_cli_args(), platform_name="Windows")

    assert module.run_teleoperation(args, input_fn=lambda prompt: "", monotonic=lambda: 2.0) == 0

    assert events == ["sync", "post_enter_gate", "decoupled_live"]
    assert captured["initial_arm_target"] == FOLLOWER
    assert captured["initial_observation_sequence"] == 2
    assert all(action == module.make_zero_action() for action in Robot.instance.actions)
