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
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
ALOHAMINI_EXAMPLES = REPO_ROOT / "examples" / "alohamini"


def load_example_module(script_name: str):
    module_name = f"test_{script_name}_{id(object())}"
    spec = importlib.util.spec_from_file_location(
        module_name,
        ALOHAMINI_EXAMPLES / f"{script_name}.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ALOHAMINI_EXAMPLES))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(ALOHAMINI_EXAMPLES))
    return module


def required_args(script_name: str) -> list[str]:
    if script_name == "record_bi":
        return ["--dataset.repo_id", "test/windows_client"]
    return []


@pytest.mark.parametrize("script_name", ["calibrate_bi", "teleoperate_bi", "record_bi"])
def test_importing_client_script_does_not_parse_arguments(monkeypatch, script_name):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("argument parsing ran during import")

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", fail_if_called)

    load_example_module(script_name)


@pytest.mark.parametrize("script_name", ["calibrate_bi", "teleoperate_bi", "record_bi"])
def test_importing_client_script_does_not_import_visualization_helpers(monkeypatch, script_name):
    monkeypatch.delitem(sys.modules, "record_utils", raising=False)
    monkeypatch.delitem(sys.modules, "lerobot.utils.visualization_utils", raising=False)

    load_example_module(script_name)

    assert "lerobot.utils.visualization_utils" not in sys.modules


@pytest.mark.parametrize("script_name", ["calibrate_bi", "teleoperate_bi", "record_bi"])
def test_linux_leader_ports_keep_stable_alias_defaults(script_name):
    module = load_example_module(script_name)

    args = module.parse_args(required_args(script_name), platform_name="Linux")
    config = module.make_leader_config(args)

    assert config.left_arm_config.port == "/dev/am_arm_leader_left"
    assert config.right_arm_config.port == "/dev/am_arm_leader_right"


@pytest.mark.parametrize("script_name", ["calibrate_bi", "teleoperate_bi", "record_bi"])
def test_windows_leader_ports_are_passed_to_both_arm_configs_unchanged(script_name):
    module = load_example_module(script_name)
    args = module.parse_args(
        [
            *required_args(script_name),
            "--teleop.left_port",
            "COM5",
            "--right_port",
            "COM10",
        ],
        platform_name="Windows",
    )

    config = module.make_leader_config(args)

    assert config.left_arm_config.port == "COM5"
    assert config.right_arm_config.port == "COM10"


@pytest.mark.parametrize("script_name", ["calibrate_bi", "teleoperate_bi", "record_bi"])
def test_windows_requires_both_explicit_leader_ports(capsys, script_name):
    module = load_example_module(script_name)

    with pytest.raises(SystemExit):
        module.parse_args(
            [*required_args(script_name), "--left_port", "COM5"],
            platform_name="Windows",
        )

    error_output = capsys.readouterr().err
    assert "Windows requires both leader ports" in error_output
    assert "--teleop.left_port COM5 --teleop.right_port COM6" in error_output


def test_record_no_rerun_disables_display_data():
    module = load_example_module("record_bi")

    args = module.parse_args(
        [
            "--dataset.repo_id",
            "test/windows_client",
            "--display_data",
            "true",
            "--no_rerun",
        ],
        platform_name="Linux",
    )

    assert not module.visualization_enabled(args)


def test_calibration_uses_passive_connections_and_disconnects_both_buses(monkeypatch):
    module = load_example_module("calibrate_bi")
    events = []

    class CalibrationArm:
        def __init__(self, name):
            self.name = name

        def connect(self, calibrate=True):
            events.append((self.name, "connect", calibrate))

        def disconnect(self):
            events.append((self.name, "disconnect"))

        def enable_torque(self):
            raise AssertionError("leader torque must remain disabled")

    class CalibrationLeader:
        def __init__(self, config):
            self.left_arm = CalibrationArm("left")
            self.right_arm = CalibrationArm("right")

        def calibrate(self):
            events.append(("leader", "calibrate"))

        def send_feedback(self, feedback):
            raise AssertionError("leader feedback must not be sent")

    monkeypatch.setattr(module, "BiSOLeader", CalibrationLeader)
    args = module.parse_args(
        ["--teleop.left_port", "COM5", "--teleop.right_port", "COM6"],
        platform_name="Windows",
    )

    module.run_calibration(args)

    assert events == [
        ("left", "connect", False),
        ("right", "connect", False),
        ("leader", "calibrate"),
        ("right", "disconnect"),
        ("left", "disconnect"),
    ]


def test_calibration_cleanup_does_not_hide_primary_failure(monkeypatch):
    module = load_example_module("calibrate_bi")
    primary_error = RuntimeError("calibration failed")

    class CalibrationArm:
        def __init__(self, *, disconnect_error=None):
            self.disconnect_error = disconnect_error

        def connect(self, calibrate=True):
            pass

        def disconnect(self):
            if self.disconnect_error is not None:
                raise self.disconnect_error

    class CalibrationLeader:
        def __init__(self, config):
            self.left_arm = CalibrationArm(disconnect_error=RuntimeError("cleanup failed"))
            self.right_arm = CalibrationArm()

        def calibrate(self):
            raise primary_error

    monkeypatch.setattr(module, "BiSOLeader", CalibrationLeader)
    args = module.parse_args(
        ["--teleop.left_port", "COM5", "--teleop.right_port", "COM6"],
        platform_name="Windows",
    )

    with pytest.raises(RuntimeError) as caught:
        module.run_calibration(args)

    assert caught.value is primary_error
    assert any("left leader disconnect" in note for note in caught.value.__notes__)


class FakeRobot:
    instances = []
    events: list[tuple]

    def __init__(self, config):
        self.config = SimpleNamespace(
            remote_ip=config.remote_ip,
            robot_model=config.robot_model,
            teleop_keys={"quit": "q"},
        )
        self.is_connected = False
        self.actions: list[dict[str, float]] = []
        self.events = type(self).events
        type(self).instances.append(self)

    def connect(self):
        self.events.append(("robot", "connect"))
        self.is_connected = True

    def get_observation(self):
        return {}

    def send_action(self, action):
        action_copy = dict(action)
        self.actions.append(action_copy)
        self.events.append(("robot", "send", action_copy))
        return action_copy

    def _from_keyboard_to_base_action(self, pressed_keys):
        return {"x.vel": 1.0, "y.vel": 0.0, "theta.vel": 0.0}

    def _from_keyboard_to_lift_action(self, pressed_keys):
        return {"lift_axis.vel": 0}

    def disconnect(self):
        self.events.append(("robot", "disconnect"))
        self.is_connected = False


class FakeArm:
    def __init__(self, name: str, events: list[tuple], *, connect_error: BaseException | None = None):
        self.name = name
        self.events = events
        self.connect_error = connect_error
        self.disconnect_error: BaseException | None = None
        self.is_connected = False

    def connect(self, calibrate: bool = True):
        self.events.append((self.name, "connect", calibrate))
        if self.connect_error is not None:
            raise self.connect_error
        self.is_connected = True

    def disconnect(self):
        self.events.append((self.name, "disconnect"))
        self.is_connected = False
        if self.disconnect_error is not None:
            raise self.disconnect_error

    def enable_torque(self):
        raise AssertionError("leader torque must remain disabled")


class FakeLeader:
    instances = []
    events: list[tuple]
    right_connect_error: BaseException | None = None
    left_disconnect_error: BaseException | None = None

    def __init__(self, config):
        self.config = config
        self.events = type(self).events
        self.left_arm = FakeArm("left", self.events)
        self.right_arm = FakeArm(
            "right",
            self.events,
            connect_error=type(self).right_connect_error,
        )
        self.left_arm.disconnect_error = type(self).left_disconnect_error
        type(self).instances.append(self)

    def get_action(self):
        self.events.append(("leader", "get_action"))
        return {"left_shoulder_pan.pos": 12.0, "right_shoulder_pan.pos": 34.0}

    def send_feedback(self, feedback):
        raise AssertionError("leader feedback must not be sent")


def prepare_teleoperation(monkeypatch, module, *, right_connect_error=None, left_disconnect_error=None):
    events: list[tuple] = []
    FakeRobot.instances = []
    FakeRobot.events = events
    FakeLeader.instances = []
    FakeLeader.events = events
    FakeLeader.right_connect_error = right_connect_error
    FakeLeader.left_disconnect_error = left_disconnect_error
    monkeypatch.setattr(module, "AlohaMiniClient", FakeRobot)
    monkeypatch.setattr(module, "BiSOLeader", FakeLeader)
    monkeypatch.setattr(
        module,
        "KeyboardTeleop",
        lambda config: (_ for _ in ()).throw(AssertionError("keyboard was constructed")),
    )
    monkeypatch.setattr(
        module,
        "load_rerun_functions",
        lambda: (_ for _ in ()).throw(AssertionError("Rerun helpers were loaded")),
    )
    return events


def teleoperation_args(module, *extra_args):
    return module.parse_args(
        [
            "--teleop.left_port",
            "COM5",
            "--teleop.right_port",
            "COM6",
            "--no_keyboard",
            "--no_rerun",
            *extra_args,
        ],
        platform_name="Windows",
    )


def test_no_keyboard_sends_zero_body_commands_and_keeps_arm_actions(monkeypatch):
    module = load_example_module("teleoperate_bi")
    prepare_teleoperation(monkeypatch, module)
    args = teleoperation_args(module, "--duration_s", "1")
    clock_values = iter((0.0, 0.0, 2.0))

    module.run_teleoperation(
        args,
        monotonic=lambda: next(clock_values),
        sleep_fn=lambda _: None,
    )

    robot = FakeRobot.instances[0]
    assert any("arm_left_shoulder_pan.pos" in action for action in robot.actions)
    assert all(action["x.vel"] == 0 for action in robot.actions)
    assert all(action["y.vel"] == 0 for action in robot.actions)
    assert all(action["theta.vel"] == 0 for action in robot.actions)
    assert all(action["lift_axis.vel"] == 0 for action in robot.actions)
    assert all("lift_axis.height_mm" not in action for action in robot.actions)


def test_start_paused_forwards_no_leader_action_before_confirmation(monkeypatch):
    module = load_example_module("teleoperate_bi")
    prepare_teleoperation(monkeypatch, module)
    args = teleoperation_args(module, "--start_paused", "--duration_s", "1")
    clock_values = iter((0.0, 0.0, 2.0))
    gate_checked = False

    def release_gate(prompt: str):
        nonlocal gate_checked
        robot = FakeRobot.instances[0]
        assert robot.actions
        assert all(not any(key.startswith("arm_") for key in action) for action in robot.actions)
        gate_checked = True
        return ""

    module.run_teleoperation(
        args,
        input_fn=release_gate,
        monotonic=lambda: next(clock_values),
        sleep_fn=lambda _: None,
    )

    assert gate_checked


def test_duration_exit_zeros_before_disconnect_and_cleans_all_devices(monkeypatch):
    module = load_example_module("teleoperate_bi")
    events = prepare_teleoperation(monkeypatch, module)
    args = teleoperation_args(module, "--duration_s", "1")
    clock_values = iter((0.0, 0.0, 2.0))

    module.run_teleoperation(
        args,
        monotonic=lambda: next(clock_values),
        sleep_fn=lambda _: None,
    )

    final_zero_index = max(
        index
        for index, event in enumerate(events)
        if event[:2] == ("robot", "send") and event[2] == module.make_zero_action()
    )
    left_disconnect_index = events.index(("left", "disconnect"))
    right_disconnect_index = events.index(("right", "disconnect"))
    robot_disconnect_index = events.index(("robot", "disconnect"))
    assert final_zero_index < left_disconnect_index
    assert final_zero_index < right_disconnect_index
    assert max(left_disconnect_index, right_disconnect_index) < robot_disconnect_index


def test_keyboard_interrupt_zeros_and_disconnects_connected_devices(monkeypatch):
    module = load_example_module("teleoperate_bi")
    events = prepare_teleoperation(monkeypatch, module)
    args = teleoperation_args(module)

    def interrupting_action(self):
        raise KeyboardInterrupt

    monkeypatch.setattr(FakeLeader, "get_action", interrupting_action)

    with pytest.raises(KeyboardInterrupt):
        module.run_teleoperation(args, sleep_fn=lambda _: None)

    assert any(event[:2] == ("robot", "send") and event[2] == module.make_zero_action() for event in events)
    assert ("left", "disconnect") in events
    assert ("right", "disconnect") in events
    assert ("robot", "disconnect") in events


def test_partial_leader_connection_failure_preserves_primary_error(monkeypatch):
    module = load_example_module("teleoperate_bi")
    primary_error = RuntimeError("right leader failed")
    events = prepare_teleoperation(
        monkeypatch,
        module,
        right_connect_error=primary_error,
        left_disconnect_error=RuntimeError("left cleanup failed"),
    )
    args = teleoperation_args(module)

    with pytest.raises(RuntimeError) as caught:
        module.run_teleoperation(args, sleep_fn=lambda _: None)

    assert caught.value is primary_error
    assert ("left", "disconnect") in events
    assert ("right", "disconnect") not in events
    assert ("robot", "disconnect") in events


def test_visualization_start_failure_cleans_connected_devices_and_preserves_error(monkeypatch):
    module = load_example_module("teleoperate_bi")
    events = prepare_teleoperation(monkeypatch, module)
    primary_error = RuntimeError("visualization failed")

    def fail_visualization_start(**kwargs):
        raise primary_error

    monkeypatch.setattr(
        module,
        "load_rerun_functions",
        lambda: (fail_visualization_start, lambda *args: None, lambda: events.append(("rerun", "shutdown"))),
    )
    args = module.parse_args(
        [
            "--teleop.left_port",
            "COM5",
            "--teleop.right_port",
            "COM6",
            "--no_keyboard",
        ],
        platform_name="Windows",
    )

    with pytest.raises(RuntimeError) as caught:
        module.run_teleoperation(args, sleep_fn=lambda _: None)

    assert caught.value is primary_error
    assert ("left", "disconnect") in events
    assert ("right", "disconnect") in events
    assert ("robot", "disconnect") in events
    assert ("rerun", "shutdown") in events


def test_no_robot_and_no_leader_do_not_construct_or_cleanup_skipped_devices(monkeypatch):
    module = load_example_module("teleoperate_bi")
    monkeypatch.setattr(
        module,
        "AlohaMiniClient",
        lambda config: (_ for _ in ()).throw(AssertionError("robot was constructed")),
    )
    monkeypatch.setattr(
        module,
        "BiSOLeader",
        lambda config: (_ for _ in ()).throw(AssertionError("leader was constructed")),
    )
    monkeypatch.setattr(
        module,
        "KeyboardTeleop",
        lambda config: (_ for _ in ()).throw(AssertionError("keyboard was constructed")),
    )
    monkeypatch.setattr(
        module,
        "load_rerun_functions",
        lambda: (_ for _ in ()).throw(AssertionError("Rerun helpers were loaded")),
    )
    args = teleoperation_args(
        module,
        "--no_robot",
        "--no_leader",
        "--duration_s",
        "1",
    )
    clock_values = iter((0.0, 2.0))

    module.run_teleoperation(
        args,
        monotonic=lambda: next(clock_values),
        sleep_fn=lambda _: None,
    )
