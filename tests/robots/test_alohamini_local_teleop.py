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
import threading
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


def test_local_stale_transition_serializes_against_a_snapshotted_nonzero_body_send(
    monkeypatch,
):
    module = load_teleoperate_module()
    snapshot_taken = threading.Event()
    release_snapshot = threading.Event()
    events: list[tuple | str] = []
    moving_body = {"x.vel": 0.15, "y.vel": 0.0, "theta.vel": 0.0, "lift_axis.vel": 200}

    class LiveCommandSender:
        def __enter__(self):
            return self

        def send_action(self, action):
            events.append(("live_send", time.monotonic(), dict(action)))

        def __exit__(self, exc_type, exc, traceback):
            events.append("live_socket_closed")
            return False

    class Robot:
        observation_sequence = 8

        def make_live_command_sender(self):
            return LiveCommandSender()

    real_snapshot = module.AM1LiveBodyMailbox.snapshot

    def paused_nonzero_snapshot(self, *, now):
        action = real_snapshot(self, now=now)
        if any(float(value) != 0.0 for value in action.values()):
            snapshot_taken.set()
            if not release_snapshot.wait(timeout=1.0):
                raise AssertionError("timed out while holding the nonzero body snapshot")
        return action

    real_clear = module.AM1LiveBodyMailbox.clear

    def recording_clear(self):
        result = real_clear(self)
        events.append(("body_cleared", time.monotonic()))
        return result

    def stale_after_sender_snapshot(*args, **kwargs):
        if not snapshot_taken.wait(timeout=1.0):
            raise AssertionError("sender never snapshotted the nonzero body command")
        raise module.StaleFollowerObservation("forced terminal stale observation")

    def release_after_transition_attempt():
        if snapshot_taken.wait(timeout=1.0):
            time.sleep(0.1)
            release_snapshot.set()

    monkeypatch.setattr(module.AM1LiveBodyMailbox, "snapshot", paused_nonzero_snapshot)
    monkeypatch.setattr(module.AM1LiveBodyMailbox, "clear", recording_clear)
    monkeypatch.setattr(module, "read_fresh_am1_live_sample", stale_after_sender_snapshot)
    releaser = threading.Thread(target=release_after_transition_attempt, daemon=True)
    releaser.start()

    with pytest.raises(module.SafetyRefusal, match="forced terminal stale observation"):
        module.run_am1_live_sender(
            Robot(),
            object(),
            initial_arm_target=FOLLOWER,
            initial_observation_sequence=8,
            fps=10,
            duration_s=30.0,
            live_arm_scope="both",
            profile_cadence=False,
            body_action_supplier=lambda: moving_body,
        )

    releaser.join(timeout=1.0)
    assert not releaser.is_alive()
    body_clear_index = next(
        index
        for index, event in enumerate(events)
        if isinstance(event, tuple) and event[0] == "body_cleared"
    )
    live_actions = [
        (index, event)
        for index, event in enumerate(events)
        if isinstance(event, tuple) and event[0] == "live_send"
    ]
    assert any(event[2]["x.vel"] > 0 for _, event in live_actions)
    assert all(
        event[2]["x.vel"] == 0.0 and event[2]["lift_axis.vel"] == 0
        for index, event in live_actions
        if index > body_clear_index
    )
    assert events[-1] == "live_socket_closed"


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
            self.actions = iter(({"w", "u"}, {"q"}))

        def connect(self):
            self.is_connected = True

        def get_action(self):
            return next(self.actions)

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
        assert kwargs["body_action_supplier"]() == module.make_zero_action()
        assert kwargs["should_stop"]() is True

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


def test_local_terminal_stale_refuses_promptly_and_joins_sender_before_outer_cleanup(
    monkeypatch,
    capsys,
):
    module = load_teleoperate_module()
    events: list[tuple | str] = []

    class LiveCommandSender:
        def __enter__(self):
            events.append("live_socket_open")
            return self

        def send_action(self, action):
            events.append(("live_send", time.monotonic(), dict(action)))

        def __exit__(self, exc_type, exc, traceback):
            events.append("live_socket_closed")
            return False

    class Robot:
        instance = None

        def __init__(self, config):
            type(self).instance = self
            self.config = config
            self.observation_sequence = 0

        def connect(self):
            events.append("robot_connect")

        def send_action(self, action):
            events.append(("main_send", dict(action)))

        def get_observation(self):
            time.sleep(0.02)
            return dict(FOLLOWER)

        def make_live_command_sender(self):
            return LiveCommandSender()

        def disconnect(self):
            events.append("robot_disconnect")

        def _from_keyboard_to_base_action(self, keys):
            return {
                "x.vel": 0.15 if "w" in keys else 0.0,
                "y.vel": 0.0,
                "theta.vel": 0.0,
            }

    class Arm:
        def __init__(self, side):
            self.side = side

        def connect(self):
            events.append(f"{self.side}_connect")

        def disconnect(self):
            events.append(f"{self.side}_disconnect")

    class Leader:
        def __init__(self, config):
            self.left_arm = Arm("left")
            self.right_arm = Arm("right")

        def get_action(self):
            return dict(LEADER)

    class Keyboard:
        def __init__(self, config):
            self.is_connected = False

        def connect(self):
            self.is_connected = True
            events.append("keyboard_connect")

        def get_action(self):
            return {"w"}

        def disconnect(self):
            self.is_connected = False
            events.append("keyboard_disconnect")

    def startup_sync(robot, leader, **kwargs):
        robot.observation_sequence = 1
        return dict(FOLLOWER), dict(FOLLOWER), time.monotonic()

    def alignment_gate(robot, leader, max_start_mismatch, *, monotonic):
        robot.observation_sequence = 2
        return dict(FOLLOWER), dict(FOLLOWER), time.monotonic()

    real_clear = module.AM1LiveBodyMailbox.clear

    def recording_clear(self):
        result = real_clear(self)
        events.append("body_cleared")
        return result

    real_join = module.AM1LiveActionSender.join

    def recording_join(self):
        result = real_join(self)
        events.append("sender_joined")
        return result

    monkeypatch.setattr(module, "AlohaMiniClient", Robot)
    monkeypatch.setattr(module, "BiSOLeader", Leader)
    monkeypatch.setattr(module, "KeyboardTeleop", Keyboard)
    monkeypatch.setattr(module, "run_startup_sync", startup_sync)
    monkeypatch.setattr(module, "run_alignment_gate", alignment_gate)
    monkeypatch.setattr(module, "AM1_LIVE_OBSERVATION_MAX_AGE_S", 0.18)
    monkeypatch.setattr(module.AM1LiveBodyMailbox, "clear", recording_clear)
    monkeypatch.setattr(module.AM1LiveActionSender, "join", recording_join)
    args = module.parse_args(local_cli_args(), platform_name="Windows")
    args.duration_s = 1.2

    started_at = time.monotonic()
    status = module.run_teleoperation(args, input_fn=lambda prompt: "")
    elapsed_s = time.monotonic() - started_at

    assert status == 2
    assert elapsed_s < 0.8
    captured = capsys.readouterr()
    assert captured.out.count("STALE FOLLOWER OBSERVATION") == 1
    assert "SAFETY REFUSAL: follower observation age" in captured.out
    assert "Traceback" not in captured.err

    body_clear_index = events.index("body_cleared")
    live_actions = [
        (index, event)
        for index, event in enumerate(events)
        if isinstance(event, tuple) and event[0] == "live_send"
    ]
    assert any(event[2]["x.vel"] > 0 for index, event in live_actions if index < body_clear_index)
    assert all(
        event[2]["x.vel"] == 0.0 and event[2]["lift_axis.vel"] == 0
        for index, event in live_actions
        if index > body_clear_index
    )

    sender_join_index = events.index("sender_joined")
    live_close_index = events.index("live_socket_closed")
    final_zero_index = max(
        index
        for index, event in enumerate(events)
        if isinstance(event, tuple) and event == ("main_send", module.make_zero_action())
    )
    assert body_clear_index < live_close_index < sender_join_index < final_zero_index
    assert final_zero_index < events.index("right_disconnect") < events.index("left_disconnect")
    assert events.index("left_disconnect") < events.index("robot_disconnect")
