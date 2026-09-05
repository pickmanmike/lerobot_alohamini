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
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from lerobot.robots.alohamini import alohamini as alohamini_module
from lerobot.robots.alohamini.alohamini import AlohaMini
from lerobot.robots.alohamini.alohamini_client import AlohaMiniClient
from lerobot.robots.alohamini.alohamini_host import make_parser as make_host_parser
from lerobot.robots.alohamini.alohamini_host import make_robot_config as make_host_robot_config
from lerobot.robots.alohamini.config_alohamini import AlohaMiniClientConfig, AlohaMiniHostConfig


REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_ROOT = REPO_ROOT / "examples" / "alohamini"
BASE_KEYS = ("x.vel", "y.vel", "theta.vel")


def load_teleoperate_module():
    module_name = f"test_am1_base_teleoperate_{id(object())}"
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


class ConstructedBus:
    def __init__(self, *, port, motors, calibration):
        self.port = port
        self.motors = motors
        self.calibration = calibration
        self.is_connected = False


def test_pi_base_config_constructs_only_the_left_body_bus(monkeypatch, tmp_path):
    constructed = []

    def make_bus(**kwargs):
        bus = ConstructedBus(**kwargs)
        constructed.append(bus)
        return bus

    monkeypatch.setattr(alohamini_module, "FeetechMotorsBus", make_bus)
    args = make_host_parser().parse_args(
        ["--robot_model", "alohamini1", "--no_follower", "--no_cameras", "--skip_lift_home"]
    )
    config = replace(make_host_robot_config(args), calibration_dir=tmp_path)

    robot = AlohaMini(config)

    assert len(constructed) == 1
    assert constructed[0] is robot.left_bus
    assert robot.right_bus is None
    assert set(robot.left_bus.motors) == {
        "base_left_wheel",
        "base_back_wheel",
        "base_right_wheel",
        "lift_axis",
    }
    assert robot.left_arm_motors == []
    assert robot.right_arm_motors == []
    assert robot.cameras == {}


class RecordingBus:
    def __init__(self, events):
        self.events = events
        self.motors = {
            "base_left_wheel": object(),
            "base_back_wheel": object(),
            "base_right_wheel": object(),
            "lift_axis": object(),
        }
        self.is_connected = True

    def disconnect(self, disable_torque=True):
        self.events.append(("disconnect", disable_torque))
        self.is_connected = False


class UnhomedLift:
    cfg = SimpleNamespace(name="lift_axis")
    is_homed = False

    def home(self):
        raise AssertionError("base-only mode must not home the lift")

    def mark_unhomed(self):
        self.is_homed = False


def make_base_robot(events):
    robot = AlohaMini.__new__(AlohaMini)
    robot.left_bus = RecordingBus(events)
    robot.right_bus = None
    robot.left_arm_motors = []
    robot.right_arm_motors = []
    robot.base_motors = ["base_left_wheel", "base_back_wheel", "base_right_wheel"]
    robot.lift = UnhomedLift()
    robot.cameras = {}
    return robot


def test_pi_base_activation_enables_only_wheels_and_never_homes_or_torques_lift(monkeypatch):
    events = []
    robot = make_base_robot(events)
    monkeypatch.setattr(robot, "_seed_activation_goals", lambda: events.append(("seed",)))
    monkeypatch.setattr(
        alohamini_module,
        "set_torque_enabled",
        lambda bus, motors, *, enabled: events.append(("torque", tuple(motors), enabled)),
    )

    robot.activate_motors(home_lift=False)

    assert events == [
        ("seed",),
        ("torque", ("base_left_wheel", "base_back_wheel", "base_right_wheel"), True),
    ]
    assert robot.lift.is_homed is False
    robot.left_bus.is_connected = False


def test_pi_base_shutdown_zeros_base_and_lift_before_disconnect(monkeypatch):
    events = []
    robot = make_base_robot(events)
    monkeypatch.setattr(
        alohamini_module,
        "write_register",
        lambda bus, register, motor, value, **kwargs: events.append(("write", register, motor, value)),
    )
    monkeypatch.setattr(
        alohamini_module,
        "set_torque_enabled",
        lambda bus, motors, *, enabled: events.append(("torque", tuple(motors), enabled)),
    )

    assert robot._safe_shutdown(close_buses=True) == []

    disconnect_index = events.index(("disconnect", False))
    for motor in (*robot.base_motors, "lift_axis"):
        assert events.index(("write", "Goal_Velocity", motor, 0)) < disconnect_index
    assert all("arm_" not in repr(event) for event in events)


def test_pi_watchdog_default_remains_one_second():
    assert AlohaMiniHostConfig().watchdog_timeout_ms == 1000


def base_cli_args(*extra: str) -> list[str]:
    return [
        "--base_only",
        "--no_leader",
        "--no_cameras",
        "--no_rerun",
        "--start_paused",
        "--fps",
        "10",
        "--duration_s",
        "30",
        *extra,
    ]


def test_windows_base_only_cli_needs_no_com_address():
    module = load_teleoperate_module()

    args = module.parse_args(base_cli_args(), platform_name="Windows")

    assert args.base_only is True
    assert args.left_port is None
    assert args.right_port is None


def test_windows_base_only_uses_the_bounded_am1_command_send_timeout():
    module = load_teleoperate_module()
    args = module.parse_args(base_cli_args(), platform_name="Windows")

    config = module.make_robot_config(args)

    assert config.command_send_timeout_ms == module.AM1_COMMAND_SEND_TIMEOUT_MS


@pytest.mark.parametrize(
    ("arguments", "reason"),
    [
        (
            [
                "--base_only", "--no_cameras", "--no_rerun", "--start_paused",
                "--fps", "10", "--duration_s", "30",
            ],
            "--base_only requires --no_leader",
        ),
        (base_cli_args("--no_robot"), "--base_only requires a robot connection"),
        (base_cli_args("--no_keyboard"), "--base_only requires keyboard control"),
        (
            [
                "--base_only", "--no_leader", "--no_rerun", "--start_paused",
                "--fps", "10", "--duration_s", "30",
            ],
            "--base_only requires --no_cameras",
        ),
        (
            [
                "--base_only", "--no_leader", "--no_cameras", "--start_paused",
                "--fps", "10", "--duration_s", "30",
            ],
            "--base_only requires --no_rerun",
        ),
        (
            ["--base_only", "--no_leader", "--no_cameras", "--no_rerun", "--fps", "10", "--duration_s", "30"],
            "--base_only requires --start_paused",
        ),
        (base_cli_args("--fps", "5"), "--base_only requires --fps 10"),
        (
            base_cli_args("--duration_s", "0"),
            "--base_only requires --duration_s greater than 0 and no more than 30",
        ),
        (
            base_cli_args("--duration_s", "31"),
            "--base_only requires --duration_s greater than 0 and no more than 30",
        ),
        (
            base_cli_args("--duration_s", "nan"),
            "--base_only requires --duration_s greater than 0 and no more than 30",
        ),
    ],
)
def test_windows_base_only_cli_requires_the_bounded_safe_shape(capsys, arguments, reason):
    module = load_teleoperate_module()

    with pytest.raises(SystemExit) as caught:
        module.parse_args(arguments, platform_name="Windows")

    assert caught.value.code == 2
    assert reason in capsys.readouterr().err


@pytest.mark.parametrize("robot_model", ["alohamini2", "alohamini2pro"])
def test_windows_base_only_cli_is_isolated_from_am2_models(capsys, robot_model):
    module = load_teleoperate_module()

    with pytest.raises(SystemExit) as caught:
        module.parse_args(base_cli_args("--robot_model", robot_model), platform_name="Windows")

    assert caught.value.code == 2
    assert "--base_only is supported only for alohamini1" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("pressed", "axis", "expected_sign"),
    [
        ({"w"}, "x.vel", 1),
        ({"s"}, "x.vel", -1),
        ({"z"}, "y.vel", 1),
        ({"x"}, "y.vel", -1),
        ({"a"}, "theta.vel", 1),
        ({"d"}, "theta.vel", -1),
    ],
)
def test_base_keyboard_pairs_produce_opposite_commands(pressed, axis, expected_sign):
    client = AlohaMiniClient(AlohaMiniClientConfig(remote_ip="127.0.0.1", cameras={}))

    action = client._from_keyboard_to_base_action(pressed)

    assert action[axis] * expected_sign > 0
    assert all(action[key] == 0 for key in BASE_KEYS if key != axis)


def test_releasing_all_base_keys_produces_zero_body_velocity():
    client = AlohaMiniClient(AlohaMiniClientConfig(remote_ip="127.0.0.1", cameras={}))

    assert client._from_keyboard_to_base_action(set()) == {
        "x.vel": 0.0,
        "y.vel": 0.0,
        "theta.vel": 0.0,
    }


def test_base_only_action_never_uses_lift_mapping():
    module = load_teleoperate_module()

    class Robot:
        def _from_keyboard_to_base_action(self, keys):
            assert keys == {"w"}
            return {"x.vel": 0.15, "y.vel": 0.0, "theta.vel": 0.0}

        def _from_keyboard_to_lift_action(self, keys):
            raise AssertionError("base-only mode must not consult lift controls")

    assert module.make_base_only_action(Robot(), {"w"}) == {
        "x.vel": 0.15,
        "y.vel": 0.0,
        "theta.vel": 0.0,
        "lift_axis.vel": 0,
    }


def test_base_only_action_ignores_speed_and_lift_keys_to_keep_the_lowest_speed():
    module = load_teleoperate_module()
    client = AlohaMiniClient(AlohaMiniClientConfig(remote_ip="127.0.0.1", cameras={}))

    action = module.make_base_only_action(client, {"w", "t", "u", "j"})

    assert action == {
        "x.vel": 0.15,
        "y.vel": 0.0,
        "theta.vel": 0.0,
        "lift_axis.vel": 0,
    }
    assert client.speed_index == 0


def test_base_only_loop_skips_observations_zeros_on_release_and_never_catches_up():
    module = load_teleoperate_module()
    sleeps = []

    class Clock:
        now = 0.0

        def __call__(self):
            return self.now

        def sleep(self, seconds):
            sleeps.append(seconds)
            self.now += seconds

    clock = Clock()

    class Robot:
        config = SimpleNamespace(teleop_keys={"quit": "q"})

        def __init__(self):
            self.actions = []

        def _from_keyboard_to_base_action(self, keys):
            return {
                "x.vel": 0.15 if "w" in keys else 0.0,
                "y.vel": 0.0,
                "theta.vel": 0.0,
            }

        def get_observation(self):
            raise AssertionError("base-only action cadence must not wait for observations")

        def send_action(self, action):
            self.actions.append(dict(action))
            clock.now += 0.35

    class Keyboard:
        def __init__(self):
            self.samples = iter(({"w"}, set(), {"q"}))

        def get_action(self):
            return next(self.samples)

    robot = Robot()
    module.run_base_only_loop(
        robot,
        Keyboard(),
        fps=10,
        duration_s=30.0,
        monotonic=clock,
        sleep_fn=clock.sleep,
    )

    assert robot.actions == [
        {"x.vel": 0.15, "y.vel": 0.0, "theta.vel": 0.0, "lift_axis.vel": 0},
        {"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0, "lift_axis.vel": 0},
    ]
    assert sleeps == [0.1, 0.1]


def test_base_only_session_constructs_no_leader_and_finally_zeros_before_disconnect(monkeypatch):
    module = load_teleoperate_module()
    events = []

    class Robot:
        instances = []

        def __init__(self, config):
            self.config = SimpleNamespace(teleop_keys=config.teleop_keys)
            self.is_connected = False
            self.actions = []
            type(self).instances.append(self)

        def connect(self):
            self.is_connected = True
            events.append(("robot", "connect"))

        def _from_keyboard_to_base_action(self, keys):
            return {
                "x.vel": 0.15 if "w" in keys else 0.0,
                "y.vel": 0.0,
                "theta.vel": 0.0,
            }

        def get_observation(self):
            raise AssertionError("base-only session must not request observations")

        def send_action(self, action):
            copied = dict(action)
            self.actions.append(copied)
            events.append(("robot", "send", copied))
            return copied

        def disconnect(self):
            events.append(("robot", "disconnect"))
            self.is_connected = False

    class Keyboard:
        def __init__(self, config):
            self.is_connected = False
            self.samples = iter(({"w"}, {"q"}))

        def connect(self):
            self.is_connected = True

        def get_action(self):
            return next(self.samples)

        def disconnect(self):
            events.append(("keyboard", "disconnect"))
            self.is_connected = False

    monkeypatch.setattr(module, "AlohaMiniClient", Robot)
    monkeypatch.setattr(module, "KeyboardTeleop", Keyboard)
    monkeypatch.setattr(
        module,
        "BiSOLeader",
        lambda config: (_ for _ in ()).throw(AssertionError("base-only mode must not construct leaders")),
    )
    monkeypatch.setattr(
        module,
        "load_rerun_functions",
        lambda: (_ for _ in ()).throw(AssertionError("base-only mode must not load visualization")),
    )
    args = module.parse_args(base_cli_args(), platform_name="Windows")

    assert module.run_teleoperation(args, input_fn=lambda prompt: "", sleep_fn=lambda seconds: None) == 0

    robot = Robot.instances[-1]
    assert robot.actions[-1] == {
        "x.vel": 0.0,
        "y.vel": 0.0,
        "theta.vel": 0.0,
        "lift_axis.vel": 0,
    }
    assert events.index(("robot", "send", robot.actions[-1])) < events.index(("robot", "disconnect"))
    assert all("lift_axis.height_mm" not in action for action in robot.actions)
    assert all(not any(key.startswith("arm_") for key in action) for action in robot.actions)
