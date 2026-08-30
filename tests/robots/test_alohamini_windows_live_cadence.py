#!/usr/bin/env python

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = REPO_ROOT / "examples" / "alohamini"
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


def load_teleoperate():
    name = f"test_teleoperate_bi_live_{id(object())}"
    spec = importlib.util.spec_from_file_location(name, EXAMPLES / "teleoperate_bi.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(EXAMPLES))
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
        sys.path.remove(str(EXAMPLES))
    return module


def parse_windows(module, *extra: str):
    return module.parse_args(
        ["--left_port", "COM8", "--right_port", "COM7", *extra],
        platform_name="Windows",
    )


def test_no_cameras_builds_empty_schema_and_only_decoupled_am1_opts_into_bounded_send():
    module = load_teleoperate()

    am1_arms_only = module.make_robot_config(
        parse_windows(module, "--no_keyboard", "--no_cameras")
    )
    am1_legacy_keyboard = module.make_robot_config(parse_windows(module, "--no_cameras"))
    am1_legacy_cameras = module.make_robot_config(parse_windows(module, "--no_keyboard"))
    am2 = module.make_robot_config(
        parse_windows(
            module,
            "--no_keyboard",
            "--no_cameras",
            "--robot_model",
            "alohamini2",
        )
    )

    assert am1_arms_only.cameras == {}
    assert am1_arms_only.command_send_timeout_ms == 50
    assert am1_legacy_keyboard.cameras == {}
    assert am1_legacy_keyboard.command_send_timeout_ms is None
    assert set(am1_legacy_cameras.cameras) == {"forward", "wrist_right"}
    assert am1_legacy_cameras.command_send_timeout_ms is None
    assert am2.cameras == {}
    assert am2.command_send_timeout_ms is None


def test_no_camera_client_import_stays_lazy_while_normal_default_schema_is_unchanged():
    code = f"""
import sys
sys.path.insert(0, {str(EXAMPLES)!r})
import teleoperate_bi
assert 'cv2' not in sys.modules
assert 'lerobot.cameras.opencv.camera_opencv' not in sys.modules
args = teleoperate_bi.parse_args(
    ['--left_port', 'COM8', '--right_port', 'COM7', '--no_cameras'],
    platform_name='Windows',
)
config = teleoperate_bi.make_robot_config(args)
assert config.cameras == {{}}
assert 'cv2' not in sys.modules
normal = teleoperate_bi.AlohaMiniClientConfig(remote_ip='127.0.0.1', id='normal')
assert set(normal.cameras) == {{'forward', 'wrist_right'}}
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_right_wrist_commissioning_scope_keeps_complete_target_and_holds_every_other_joint():
    module = load_teleoperate()
    approved = dict(FOLLOWER)
    latest = {key: value + 20.0 for key, value in FOLLOWER.items()}

    scoped = module.apply_am1_commissioning_scope(
        latest,
        approved,
        scope="right_wrist_flex",
    )

    assert set(scoped) == set(ARM_KEYS)
    assert scoped["arm_right_wrist_flex.pos"] == latest["arm_right_wrist_flex.pos"]
    assert {
        key: value for key, value in scoped.items() if key != "arm_right_wrist_flex.pos"
    } == {
        key: value for key, value in approved.items() if key != "arm_right_wrist_flex.pos"
    }


def test_all_joint_scope_is_identity_and_does_not_double_invert_right_wrist():
    module = load_teleoperate()

    scoped = module.apply_am1_commissioning_scope(
        FOLLOWER,
        {key: 0.0 for key in ARM_KEYS},
        scope="both",
    )

    assert scoped == FOLLOWER
    assert scoped["arm_right_wrist_flex.pos"] == FOLLOWER["arm_right_wrist_flex.pos"]


class SampleRobot:
    def __init__(self, *, advances: bool = True, observation=None):
        self.observation_sequence = 41
        self.advances = advances
        self.observation = dict(FOLLOWER if observation is None else observation)

    def get_observation(self):
        if self.advances:
            self.observation_sequence += 1
        return dict(self.observation)


class SampleLeader:
    def __init__(self, action=None):
        self.action = dict(LEADER if action is None else action)

    def get_action(self):
        return dict(self.action)


def test_fresh_live_sample_is_atomic_complete_and_keeps_follower_and_leader_convention_identity():
    module = load_teleoperate()

    sample = module.read_fresh_am1_live_sample(
        SampleRobot(),
        SampleLeader(),
        previous_sequence=41,
        monotonic=lambda: 12.5,
    )

    assert sample.observation_sequence == 42
    assert sample.observed_at == 12.5
    assert dict(sample.follower_positions) == FOLLOWER
    assert dict(sample.arm_target) == FOLLOWER
    assert sample.arm_target["arm_right_wrist_flex.pos"] == FOLLOWER["arm_right_wrist_flex.pos"]


@pytest.mark.parametrize(
    ("robot", "reason"),
    [
        (SampleRobot(advances=False), "did not advance"),
        (
            SampleRobot(observation={key: value for key, value in FOLLOWER.items() if key != ARM_KEYS[0]}),
            "keys are invalid",
        ),
    ],
)
def test_cached_or_partial_observation_is_a_latched_stale_sample_not_a_new_target(robot, reason):
    module = load_teleoperate()

    with pytest.raises(module.StaleFollowerObservation) as caught:
        module.read_fresh_am1_live_sample(
            robot,
            SampleLeader(),
            previous_sequence=41,
            monotonic=lambda: 12.5,
        )

    assert reason in str(caught.value)


def test_completion_spaced_deadline_never_catches_up_after_late_send():
    module = load_teleoperate()

    assert module.next_completion_spaced_deadline(10.37, fps=10) == pytest.approx(10.47)
    assert module.next_completion_spaced_deadline(11.92, fps=10) == pytest.approx(12.02)


def test_commissioning_scope_is_am1_only_and_requires_safe_operator_gate(capsys):
    module = load_teleoperate()

    with pytest.raises(SystemExit) as caught:
        parse_windows(
            module,
            "--robot_model",
            "alohamini2",
            "--live_arm_scope",
            "right_wrist_flex",
        )

    assert caught.value.code == 2
    assert "supported only for alohamini1" in capsys.readouterr().err

    with pytest.raises(SystemExit) as caught:
        parse_windows(module, "--live_arm_scope", "right_wrist_flex")

    assert caught.value.code == 2
    assert "requires --start_paused" in capsys.readouterr().err


def test_live_target_always_has_explicit_zero_base_and_lift():
    module = load_teleoperate()

    action = module.make_am1_live_action(FOLLOWER)

    assert action == {**FOLLOWER, **module.make_zero_action()}


def test_recording_path_keeps_right_wrist_value_identity_without_client_side_sign_transform():
    source = (EXAMPLES / "record_utils.py").read_text(encoding="utf-8")

    assert 'arm_action = {f"arm_{key}": value for key, value in leader_arm.get_action().items()}' in source
    assert "right_wrist_flex" not in source


def test_only_explicit_am1_arms_only_no_camera_mode_uses_decoupled_live_path():
    module = load_teleoperate()

    assert module.uses_decoupled_am1_live_loop(
        SimpleNamespace(robot_model="alohamini1", no_keyboard=True, no_cameras=True)
    ) is True
    assert module.uses_decoupled_am1_live_loop(
        SimpleNamespace(robot_model="alohamini1", no_keyboard=False, no_cameras=True)
    ) is False
    assert module.uses_decoupled_am1_live_loop(
        SimpleNamespace(robot_model="alohamini1", no_keyboard=True, no_cameras=False)
    ) is False
    assert module.uses_decoupled_am1_live_loop(
        SimpleNamespace(robot_model="alohamini2", no_keyboard=True, no_cameras=True)
    ) is False
    assert module.uses_decoupled_am1_live_loop(
        SimpleNamespace(robot_model="alohamini2pro", no_keyboard=True, no_cameras=True)
    ) is False


class TimedRobot:
    def __init__(self, *, observation_delay_s: float = 0.0, advance: bool = True):
        self.observation_delay_s = observation_delay_s
        self.advance = advance
        self.observation_sequence = 8
        self.sends: list[tuple[float, float, dict[str, float]]] = []
        self.first_observation_started = threading.Event()

    def get_observation(self):
        self.first_observation_started.set()
        time.sleep(self.observation_delay_s)
        if self.advance:
            self.observation_sequence += 1
        return dict(FOLLOWER)

    def send_action(self, action):
        started = time.monotonic()
        finished = time.monotonic()
        self.sends.append((started, finished, dict(action)))


def test_actual_live_runner_sends_unchanged_safe_target_while_sampler_blocks_past_watchdog():
    module = load_teleoperate()
    robot = TimedRobot(observation_delay_s=1.25)

    module.run_am1_live_sender(
        robot,
        SampleLeader(),
        initial_arm_target=FOLLOWER,
        initial_observation_sequence=robot.observation_sequence,
        fps=10,
        duration_s=1.1,
        live_arm_scope="both",
        profile_cadence=False,
    )

    assert robot.first_observation_started.is_set()
    assert len(robot.sends) >= 10
    assert all(action == {**FOLLOWER, **module.make_zero_action()} for _, _, action in robot.sends)
    intervals = [right[0] - left[0] for left, right in zip(robot.sends, robot.sends[1:], strict=False)]
    assert max(intervals) < 0.25
    assert min(intervals) >= 0.075


def test_actual_live_runner_consumes_fresh_complete_target_after_first_approved_send():
    module = load_teleoperate()
    events: list[tuple] = []
    changed_leader = {key: value + 20.0 for key, value in LEADER.items()}
    changed_target = {f"arm_{key}": value for key, value in changed_leader.items()}

    class AdvancingRobot(TimedRobot):
        def get_observation(self):
            events.append(("observation", self.observation_sequence))
            time.sleep(0.02)
            self.observation_sequence += 1
            return dict(FOLLOWER)

        def send_action(self, action):
            events.append(("send", dict(action)))
            super().send_action(action)

    class ChangedLeader:
        def get_action(self):
            events.append(("leader", dict(changed_leader)))
            return dict(changed_leader)

    robot = AdvancingRobot()
    module.run_am1_live_sender(
        robot,
        ChangedLeader(),
        initial_arm_target=FOLLOWER,
        initial_observation_sequence=robot.observation_sequence,
        fps=10,
        duration_s=0.35,
        live_arm_scope="both",
        profile_cadence=False,
    )

    first_send_index = next(index for index, event in enumerate(events) if event[0] == "send")
    first_leader_index = next(index for index, event in enumerate(events) if event[0] == "leader")
    assert first_send_index < first_leader_index
    assert events[first_send_index][1] == {**FOLLOWER, **module.make_zero_action()}
    assert any(action == {**changed_target, **module.make_zero_action()} for _, _, action in robot.sends)
    assert all(
        {key: action[key] for key in module.make_zero_action()} == module.make_zero_action()
        for _, _, action in robot.sends
    )


def test_actual_live_runner_latches_cached_observation_and_keeps_sending_frozen_target(capsys):
    module = load_teleoperate()
    robot = TimedRobot(advance=False)

    with pytest.raises(module.SafetyRefusal, match="did not advance"):
        module.run_am1_live_sender(
            robot,
            SampleLeader(action={**LEADER, "right_wrist_flex.pos": 70.0}),
            initial_arm_target=FOLLOWER,
            initial_observation_sequence=robot.observation_sequence,
            fps=10,
            duration_s=0.35,
            live_arm_scope="both",
            profile_cadence=False,
        )

    assert len(robot.sends) >= 3
    assert all(action == {**FOLLOWER, **module.make_zero_action()} for _, _, action in robot.sends)
    assert capsys.readouterr().out.count("STALE FOLLOWER OBSERVATION") == 1


def test_actual_live_runner_rejects_target_paired_with_overage_follower_sample(capsys):
    module = load_teleoperate()
    robot = TimedRobot()
    moved = {**LEADER, "right_wrist_flex.pos": 70.0}

    class DelayedMovedLeader:
        def get_action(self):
            time.sleep(1.05)
            return dict(moved)

    with pytest.raises(module.SafetyRefusal, match="freshness limit"):
        module.run_am1_live_sender(
            robot,
            DelayedMovedLeader(),
            initial_arm_target=FOLLOWER,
            initial_observation_sequence=robot.observation_sequence,
            fps=10,
            duration_s=1.25,
            live_arm_scope="both",
            profile_cadence=False,
        )

    assert len(robot.sends) >= 11
    assert all(action == {**FOLLOWER, **module.make_zero_action()} for _, _, action in robot.sends)
    output = capsys.readouterr().out
    assert output.count("STALE FOLLOWER OBSERVATION") == 1
    assert "reached the 1.0-second freshness limit after leader sampling" in output


def test_profile_cadence_output_is_bounded_not_printed_on_every_send(capsys):
    module = load_teleoperate()
    robot = TimedRobot(observation_delay_s=0.5)

    module.run_am1_live_sender(
        robot,
        SampleLeader(),
        initial_arm_target=FOLLOWER,
        initial_observation_sequence=robot.observation_sequence,
        fps=10,
        duration_s=0.35,
        live_arm_scope="both",
        profile_cadence=True,
    )

    reports = [
        line
        for line in capsys.readouterr().out.splitlines()
        if '"event": "am1_client_action_cadence"' in line
    ]
    assert len(robot.sends) >= 3
    assert len(reports) == 1
    assert f'"action_sequence": {len(robot.sends)}' in reports[0]
    assert '"longest_action_send_interval_ms"' in reports[0]


def test_actual_live_runner_waits_from_send_completion_instead_of_catching_up():
    module = load_teleoperate()

    class SlowSendRobot(TimedRobot):
        def send_action(self, action):
            started = time.monotonic()
            if len(self.sends) == 1:
                time.sleep(0.24)
            finished = time.monotonic()
            self.sends.append((started, finished, dict(action)))

    robot = SlowSendRobot(observation_delay_s=2.0)
    module.run_am1_live_sender(
        robot,
        SampleLeader(),
        initial_arm_target=FOLLOWER,
        initial_observation_sequence=robot.observation_sequence,
        fps=10,
        duration_s=0.58,
        live_arm_scope="both",
        profile_cadence=False,
    )

    post_send_delays = [right[0] - left[1] for left, right in zip(robot.sends, robot.sends[1:], strict=False)]
    assert post_send_delays
    assert min(post_send_delays) >= 0.075


def test_worker_primary_error_identity_and_join_precede_caller_cleanup(monkeypatch):
    module = load_teleoperate()
    events: list[str] = []
    primary = RuntimeError("leader sample failed")
    robot = TimedRobot()

    class ExplodingLeader:
        def get_action(self):
            events.append("worker_error")
            raise primary

    real_join = module.AM1LiveSampler.join

    def recording_join(self):
        result = real_join(self)
        events.append("worker_joined")
        return result

    monkeypatch.setattr(module.AM1LiveSampler, "join", recording_join)

    try:
        with pytest.raises(RuntimeError) as caught:
            module.run_am1_live_sender(
                robot,
                ExplodingLeader(),
                initial_arm_target=FOLLOWER,
                initial_observation_sequence=robot.observation_sequence,
                fps=10,
                duration_s=1.0,
                live_arm_scope="both",
                profile_cadence=False,
            )
    finally:
        events.append("final_zero")
        robot.send_action(module.make_zero_action())
        events.append("disconnect")

    assert caught.value is primary
    assert events.index("worker_error") < events.index("worker_joined")
    assert events.index("worker_joined") < events.index("final_zero") < events.index("disconnect")


def test_worker_error_published_during_final_join_is_not_lost():
    module = load_teleoperate()
    primary = RuntimeError("late leader sample failed")
    robot = TimedRobot(observation_delay_s=0.2)

    class LateExplodingLeader:
        def get_action(self):
            raise primary

    with pytest.raises(RuntimeError) as caught:
        module.run_am1_live_sender(
            robot,
            LateExplodingLeader(),
            initial_arm_target=FOLLOWER,
            initial_observation_sequence=robot.observation_sequence,
            fps=10,
            duration_s=0.05,
            live_arm_scope="both",
            profile_cadence=False,
        )

    assert caught.value is primary


@pytest.mark.parametrize("timeout_ms", [None, 50])
def test_client_applies_command_send_timeout_only_when_configured(timeout_ms):
    from lerobot.robots.alohamini.alohamini_client import AlohaMiniClient
    from lerobot.robots.alohamini.config_alohamini import AlohaMiniClientConfig

    class FakeSocket:
        def __init__(self):
            self.options: list[tuple[int, int]] = []

        def setsockopt(self, option, value):
            self.options.append((option, value))

        def connect(self, locator):
            self.locator = locator

    class FakeContext:
        def __init__(self):
            self.sockets: list[FakeSocket] = []

        def socket(self, socket_type):
            socket = FakeSocket()
            self.sockets.append(socket)
            return socket

    context = FakeContext()
    fake_zmq = SimpleNamespace(
        Context=lambda: context,
        PUSH=1,
        DEALER=2,
        CONFLATE=3,
        SNDTIMEO=4,
        RCVHWM=5,
        SNDHWM=6,
        LINGER=7,
    )
    client = AlohaMiniClient(
        AlohaMiniClientConfig(
            remote_ip="127.0.0.1",
            id="test",
            robot_model="alohamini1",
            cameras={},
            command_send_timeout_ms=timeout_ms,
        )
    )
    client._zmq = fake_zmq
    client._request_observation = lambda timeout: [b"handshake"]
    client._fill_observation_request_window = lambda: None

    client.connect()

    command_options = context.sockets[0].options
    if timeout_ms is None:
        assert (fake_zmq.SNDTIMEO, 50) not in command_options
        assert all(option != fake_zmq.SNDTIMEO for option, _ in command_options)
        assert all(option != fake_zmq.LINGER for option, _ in command_options)
        assert all(option != fake_zmq.LINGER for option, _ in context.sockets[1].options)
    else:
        assert (fake_zmq.SNDTIMEO, timeout_ms) in command_options
        assert (fake_zmq.LINGER, 0) in command_options
        assert (fake_zmq.LINGER, 0) in context.sockets[1].options


def test_run_teleoperation_joins_sampler_before_real_cleanup_and_preserves_worker_error(
    monkeypatch,
):
    module = load_teleoperate()
    events: list[tuple | str] = []
    primary = RuntimeError("live leader sample failed")
    cleanup_error = RuntimeError("left disconnect failed")

    class LifecycleRobot:
        instance = None

        def __init__(self, config):
            type(self).instance = self
            self.created_config = config
            self.config = SimpleNamespace(teleop_keys={"quit": "q"}, connect_timeout_s=1.0)
            self.observation_sequence = 0
            self.is_connected = False

        def connect(self):
            self.is_connected = True
            events.append("robot_connect")

        def get_observation(self):
            self.observation_sequence += 1
            events.append(("observation", self.observation_sequence))
            return dict(FOLLOWER)

        def send_action(self, action):
            events.append(("send", dict(action)))
            return action

        def disconnect(self):
            events.append("robot_disconnect")
            self.is_connected = False

    class LifecycleArm:
        def __init__(self, side):
            self.side = side

        def connect(self, calibrate=True):
            events.append(f"{self.side}_connect")

        def disconnect(self):
            events.append(f"{self.side}_disconnect")
            if self.side == "left":
                raise cleanup_error

    class LifecycleLeader:
        def __init__(self, config):
            self.left_arm = LifecycleArm("left")
            self.right_arm = LifecycleArm("right")
            self.read_count = 0

        def get_action(self):
            self.read_count += 1
            events.append(("leader_read", self.read_count))
            if self.read_count == 1:
                return dict(LEADER)
            raise primary

    monkeypatch.setattr(module, "AlohaMiniClient", LifecycleRobot)
    monkeypatch.setattr(module, "BiSOLeader", LifecycleLeader)
    real_join = module.AM1LiveSampler.join

    def recording_join(self):
        result = real_join(self)
        events.append("sampler_joined")
        return result

    monkeypatch.setattr(module.AM1LiveSampler, "join", recording_join)
    args = parse_windows(
        module,
        "--no_keyboard",
        "--no_cameras",
        "--no_rerun",
        "--fps",
        "10",
        "--duration_s",
        "1",
    )

    with pytest.raises(RuntimeError) as caught:
        module.run_teleoperation(
            args,
            sleep_fn=lambda duration: time.sleep(min(duration, 0.01)),
        )

    assert caught.value is primary
    assert any("left disconnect failed" in note for note in getattr(primary, "__notes__", ()))
    final_zero_index = max(
        index
        for index, event in enumerate(events)
        if isinstance(event, tuple)
        and event[0] == "send"
        and event[1] == module.make_zero_action()
    )
    assert events.index("sampler_joined") < final_zero_index
    assert final_zero_index < events.index("right_disconnect") < events.index("left_disconnect")
    assert events.index("left_disconnect") < events.index("robot_disconnect")
    assert LifecycleRobot.instance.created_config.cameras == {}
    assert LifecycleRobot.instance.created_config.command_send_timeout_ms == 50


def test_run_teleoperation_turns_live_worker_safety_refusal_into_status_two(monkeypatch, capsys):
    module = load_teleoperate()
    events: list[tuple | str] = []
    refusal = module.SafetyRefusal("live leader right wrist value nan must be finite")

    class FakeRobot:
        def __init__(self, config):
            self.config = SimpleNamespace(teleop_keys={"quit": "q"})
            self.observation_sequence = 1

        def connect(self):
            events.append("robot_connect")

        def send_action(self, action):
            events.append(("send", dict(action)))

        def disconnect(self):
            events.append("robot_disconnect")

    class FakeArm:
        def __init__(self, side):
            self.side = side

        def connect(self):
            events.append(f"{self.side}_connect")

        def disconnect(self):
            events.append(f"{self.side}_disconnect")

    class FakeLeader:
        def __init__(self, config):
            self.left_arm = FakeArm("left")
            self.right_arm = FakeArm("right")

    monkeypatch.setattr(module, "AlohaMiniClient", FakeRobot)
    monkeypatch.setattr(module, "BiSOLeader", FakeLeader)
    monkeypatch.setattr(module, "run_alignment_gate", lambda *args: (dict(FOLLOWER), dict(FOLLOWER)))
    monkeypatch.setattr(module, "run_am1_live_sender", lambda *args, **kwargs: (_ for _ in ()).throw(refusal))
    args = parse_windows(module, "--no_keyboard", "--no_cameras", "--no_rerun")

    status = module.run_teleoperation(args)

    captured = capsys.readouterr()
    assert status == 2
    assert "SAFETY REFUSAL: live leader right wrist value nan must be finite" in captured.out
    assert "Traceback" not in captured.err
    assert events[-3:] == ["right_disconnect", "left_disconnect", "robot_disconnect"]


def test_run_teleoperation_holds_stale_target_through_duration_then_returns_two_and_cleans_up(
    monkeypatch,
    capsys,
):
    module = load_teleoperate()
    events: list[tuple | str] = []

    class StaleRobot:
        instance = None

        def __init__(self, config):
            type(self).instance = self
            self.config = SimpleNamespace(teleop_keys={"quit": "q"}, connect_timeout_s=1.0)
            self.observation_sequence = 0
            self.actions: list[dict] = []

        def connect(self):
            events.append("robot_connect")

        def get_observation(self):
            if self.observation_sequence == 0:
                self.observation_sequence = 1
            events.append(("observation", self.observation_sequence))
            return dict(FOLLOWER)

        def send_action(self, action):
            copied = dict(action)
            self.actions.append(copied)
            events.append(("send", copied))

        def disconnect(self):
            events.append("robot_disconnect")

    class FakeArm:
        def __init__(self, side):
            self.side = side

        def connect(self):
            events.append(f"{self.side}_connect")

        def disconnect(self):
            events.append(f"{self.side}_disconnect")

    class OneSampleLeader:
        def __init__(self, config):
            self.left_arm = FakeArm("left")
            self.right_arm = FakeArm("right")

        def get_action(self):
            events.append("leader_read")
            return dict(LEADER)

    monkeypatch.setattr(module, "AlohaMiniClient", StaleRobot)
    monkeypatch.setattr(module, "BiSOLeader", OneSampleLeader)
    real_join = module.AM1LiveSampler.join

    def recording_join(self):
        result = real_join(self)
        events.append("sampler_joined")
        return result

    monkeypatch.setattr(module.AM1LiveSampler, "join", recording_join)
    args = parse_windows(
        module,
        "--no_keyboard",
        "--no_cameras",
        "--no_rerun",
        "--fps",
        "10",
        "--duration_s",
        "0.25",
    )

    status = module.run_teleoperation(args)

    captured = capsys.readouterr()
    reason = "observation_sequence did not advance (previous=1, current=1)"
    assert status == 2
    assert captured.out.count("STALE FOLLOWER OBSERVATION") == 1
    assert f"SAFETY REFUSAL: {reason}" in captured.out
    assert "Traceback" not in captured.err
    arm_actions = [action for action in StaleRobot.instance.actions if set(FOLLOWER) <= set(action)]
    assert len(arm_actions) >= 2
    assert all(action == {**FOLLOWER, **module.make_zero_action()} for action in arm_actions)
    final_zero_index = max(
        index
        for index, event in enumerate(events)
        if isinstance(event, tuple) and event == ("send", module.make_zero_action())
    )
    assert events.index("sampler_joined") < final_zero_index
    assert final_zero_index < events.index("right_disconnect") < events.index("left_disconnect")
    assert events.index("left_disconnect") < events.index("robot_disconnect")
