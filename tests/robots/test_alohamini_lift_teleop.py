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
from lerobot.robots.alohamini import lift_axis as lift_axis_module
from lerobot.robots.alohamini.alohamini import AlohaMini
from lerobot.robots.alohamini.alohamini_host import connect_robot
from lerobot.robots.alohamini.alohamini_host import make_parser as make_host_parser
from lerobot.robots.alohamini.alohamini_host import make_robot_config as make_host_robot_config
from lerobot.robots.alohamini.config_alohamini import AlohaMiniHostConfig
from lerobot.robots.alohamini.lift_axis import LiftAxis, LiftAxisConfig


REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_ROOT = REPO_ROOT / "examples" / "alohamini"
ZERO_BASE = {"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0}


def load_teleoperate_module():
    module_name = f"test_am1_lift_teleoperate_{id(object())}"
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


def test_pi_lift_config_constructs_only_the_left_body_bus(monkeypatch, tmp_path):
    constructed = []

    def make_bus(**kwargs):
        bus = ConstructedBus(**kwargs)
        constructed.append(bus)
        return bus

    monkeypatch.setattr(alohamini_module, "FeetechMotorsBus", make_bus)
    args = make_host_parser().parse_args(["--robot_model", "alohamini1", "--no_follower", "--no_cameras"])
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


def test_pi_lift_startup_requests_homing_exactly_once():
    calls = []

    class Robot:
        def connect(self, *, home_lift):
            calls.append(home_lift)

    connect_robot(Robot(), skip_lift_home=False)

    assert calls == [True]


def test_pi_lift_homing_failure_prevents_normal_activation(monkeypatch):
    events = []

    class FailingLift:
        cfg = SimpleNamespace(name="lift_axis")
        is_homed = False

        def home(self):
            events.append("home")
            raise RuntimeError("homing failed")

    robot = AlohaMini.__new__(AlohaMini)
    robot.left_bus = SimpleNamespace(is_connected=False)
    robot.right_bus = None
    robot.left_arm_motors = []
    robot.right_arm_motors = []
    robot.base_motors = ["base_left_wheel", "base_back_wheel", "base_right_wheel"]
    robot.lift = FailingLift()
    robot.cameras = {}
    monkeypatch.setattr(robot, "_seed_activation_goals", lambda: events.append("seed"))
    monkeypatch.setattr(robot, "_safe_shutdown", lambda *, close_buses: events.append("shutdown") or [])
    monkeypatch.setattr(
        alohamini_module,
        "set_torque_enabled",
        lambda *args, **kwargs: events.append("torque"),
    )

    with pytest.raises(RuntimeError, match="motor activation failed"):
        robot.activate_motors(home_lift=True)

    assert events == ["home", "shutdown"]


def test_pi_watchdog_remains_one_second_for_lift_mode():
    assert AlohaMiniHostConfig().watchdog_timeout_ms == 1000


def lift_cli_args(*extra: str) -> list[str]:
    return [
        "--lift_only",
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


def test_windows_lift_only_cli_needs_no_com_address_and_uses_bounded_send_timeout():
    module = load_teleoperate_module()

    args = module.parse_args(lift_cli_args(), platform_name="Windows")
    config = module.make_robot_config(args)

    assert args.lift_only is True
    assert args.left_port is None
    assert args.right_port is None
    assert config.cameras == {}
    assert config.command_send_timeout_ms == module.AM1_COMMAND_SEND_TIMEOUT_MS


@pytest.mark.parametrize(
    ("arguments", "reason"),
    [
        (
            [
                "--lift_only",
                "--no_cameras",
                "--no_rerun",
                "--start_paused",
                "--fps",
                "10",
                "--duration_s",
                "30",
            ],
            "--lift_only requires --no_leader",
        ),
        (lift_cli_args("--no_robot"), "--lift_only requires a robot connection"),
        (lift_cli_args("--no_keyboard"), "--lift_only requires keyboard control"),
        (
            [
                "--lift_only",
                "--no_leader",
                "--no_rerun",
                "--start_paused",
                "--fps",
                "10",
                "--duration_s",
                "30",
            ],
            "--lift_only requires --no_cameras",
        ),
        (
            [
                "--lift_only",
                "--no_leader",
                "--no_cameras",
                "--start_paused",
                "--fps",
                "10",
                "--duration_s",
                "30",
            ],
            "--lift_only requires --no_rerun",
        ),
        (
            [
                "--lift_only",
                "--no_leader",
                "--no_cameras",
                "--no_rerun",
                "--fps",
                "10",
                "--duration_s",
                "30",
            ],
            "--lift_only requires --start_paused",
        ),
        (lift_cli_args("--fps", "5"), "--lift_only requires --fps 10"),
        (
            lift_cli_args("--duration_s", "0"),
            "--lift_only requires --duration_s greater than 0 and no more than 30",
        ),
        (
            lift_cli_args("--duration_s", "31"),
            "--lift_only requires --duration_s greater than 0 and no more than 30",
        ),
        (
            lift_cli_args("--duration_s", "nan"),
            "--lift_only requires --duration_s greater than 0 and no more than 30",
        ),
    ],
)
def test_windows_lift_only_cli_requires_the_bounded_safe_shape(capsys, arguments, reason):
    module = load_teleoperate_module()

    with pytest.raises(SystemExit) as caught:
        module.parse_args(arguments, platform_name="Windows")

    assert caught.value.code == 2
    assert reason in capsys.readouterr().err


@pytest.mark.parametrize("robot_model", ["alohamini2", "alohamini2pro"])
def test_windows_lift_only_cli_is_isolated_from_am2_models(capsys, robot_model):
    module = load_teleoperate_module()

    with pytest.raises(SystemExit) as caught:
        module.parse_args(lift_cli_args("--robot_model", robot_model), platform_name="Windows")

    assert caught.value.code == 2
    assert "--lift_only is supported only for alohamini1" in capsys.readouterr().err


def test_windows_rejects_combined_base_and_lift_only_modes(capsys):
    module = load_teleoperate_module()

    with pytest.raises(SystemExit) as caught:
        module.parse_args(lift_cli_args("--base_only"), platform_name="Windows")

    assert caught.value.code == 2
    assert "--base_only and --lift_only cannot be combined" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("pressed", "expected_lift"),
    [
        ({"u"}, 200),
        ({"j"}, -200),
        ({"u", "j"}, 0),
        (set(), 0),
        ({"w", "s", "z", "x", "a", "d", "t", "g"}, 0),
    ],
)
def test_lift_only_action_accepts_only_bounded_u_j_commands(pressed, expected_lift):
    module = load_teleoperate_module()
    robot = SimpleNamespace(config=SimpleNamespace(teleop_keys={"lift_up": "u", "lift_down": "j"}))

    action = module.make_lift_only_action(robot, pressed)

    assert action == {**ZERO_BASE, "lift_axis.vel": expected_lift}


def test_lift_only_logical_directions_map_to_am1_raw_directions(monkeypatch):
    module = load_teleoperate_module()
    writes = []

    class Bus:
        motors = {"lift_axis": object()}

        def read(self, data_name, motor, **kwargs):
            assert data_name == "Present_Position"
            assert motor == "lift_axis"
            return 0

    lift = LiftAxis(
        LiftAxisConfig(soft_min_mm=-1000.0, descent_floor_mm=-1000.0),
        bus_left=Bus(),
        bus_right=None,
    )
    lift.is_homed = True
    monkeypatch.setattr(
        lift_axis_module,
        "write_register",
        lambda bus, register, motor, value, **kwargs: writes.append((register, motor, value)),
    )
    robot = SimpleNamespace(config=SimpleNamespace(teleop_keys={"lift_up": "u", "lift_down": "j"}))

    lift.apply_action(module.make_lift_only_action(robot, {"u"}))
    lift.apply_action(module.make_lift_only_action(robot, {"j"}))

    assert writes == [
        ("Goal_Velocity", "lift_axis", -200),
        ("Goal_Velocity", "lift_axis", 200),
    ]


def test_lift_descent_refuses_at_real_floor_and_is_permitted_above_it(monkeypatch):
    module = load_teleoperate_module()
    writes = []

    class Bus:
        motors = {"lift_axis": object()}

    lift = LiftAxis(LiftAxisConfig(), bus_left=Bus(), bus_right=None)
    lift.is_homed = True
    heights = iter((2.05, 5.0, 12.3))
    monkeypatch.setattr(lift, "get_height_mm", lambda: next(heights))
    monkeypatch.setattr(
        lift_axis_module,
        "write_register",
        lambda bus, register, motor, value, **kwargs: writes.append((register, motor, value)),
    )
    robot = SimpleNamespace(config=SimpleNamespace(teleop_keys={"lift_up": "u", "lift_down": "j"}))
    down = module.make_lift_only_action(robot, {"j"})

    lift.apply_action(down)
    lift.apply_action(down)
    lift.apply_action(down)

    assert lift.cfg.descent_floor_mm == 5.0
    assert writes == [
        ("Goal_Velocity", "lift_axis", 0),
        ("Goal_Velocity", "lift_axis", 0),
        ("Goal_Velocity", "lift_axis", 200),
    ]


def test_lift_only_loop_sends_release_zero_and_ignores_observations_and_base_keys():
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
        config = SimpleNamespace(
            teleop_keys={"lift_up": "u", "lift_down": "j", "quit": "q"}
        )

        def __init__(self):
            self.actions = []

        def get_observation(self):
            raise AssertionError("lift-only command cadence must not wait for observations")

        def send_action(self, action):
            self.actions.append(dict(action))
            clock.now += 0.35

    class Keyboard:
        def __init__(self):
            self.samples = iter(({"u"}, set(), {"j"}, {"w", "t"}, {"q"}))

        def get_action(self):
            return next(self.samples)

    robot = Robot()
    module.run_lift_only_loop(
        robot,
        Keyboard(),
        fps=10,
        duration_s=30.0,
        monotonic=clock,
        sleep_fn=clock.sleep,
    )

    assert robot.actions == [
        {**ZERO_BASE, "lift_axis.vel": 200},
        {**ZERO_BASE, "lift_axis.vel": 0},
        {**ZERO_BASE, "lift_axis.vel": -200},
        {**ZERO_BASE, "lift_axis.vel": 0},
    ]
    assert sleeps == [0.1, 0.1, 0.1, 0.1]


def test_lift_only_session_constructs_no_leader_and_finally_zeros_before_disconnect(monkeypatch):
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

        def get_observation(self):
            raise AssertionError("lift-only session must not request observations")

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
            self.samples = iter(({"u"}, {"q"}))

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
        lambda config: (_ for _ in ()).throw(AssertionError("lift-only mode must not construct leaders")),
    )
    monkeypatch.setattr(
        module,
        "load_rerun_functions",
        lambda: (_ for _ in ()).throw(AssertionError("lift-only mode must not load visualization")),
    )
    args = module.parse_args(lift_cli_args(), platform_name="Windows")

    assert module.run_teleoperation(args, input_fn=lambda prompt: "", sleep_fn=lambda seconds: None) == 0

    robot = Robot.instances[-1]
    assert robot.actions[-1] == {**ZERO_BASE, "lift_axis.vel": 0}
    assert events.index(("robot", "send", robot.actions[-1])) < events.index(("robot", "disconnect"))
    assert all(action[key] == 0 for action in robot.actions for key in ZERO_BASE)
    assert all("lift_axis.height_mm" not in action for action in robot.actions)
    assert all(not any(key.startswith("arm_") for key in action) for action in robot.actions)
