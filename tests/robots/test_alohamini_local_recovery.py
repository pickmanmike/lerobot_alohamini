#!/usr/bin/env python

"""Offline AM1 Local pause/resume safety contracts; never opens motor buses."""

from __future__ import annotations

import json
import importlib.util
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from lerobot.robots.alohamini.alohamini import AlohaMini
from lerobot.robots.alohamini import alohamini_host


CONTROL = "_am1_local_control"
FEEDBACK = "_am1_local_feedback"
ARM_KEYS = (
    "arm_left_shoulder_pan.pos", "arm_left_shoulder_lift.pos", "arm_left_elbow_flex.pos",
    "arm_left_wrist_flex.pos", "arm_left_wrist_roll.pos", "arm_left_gripper.pos",
    "arm_right_shoulder_pan.pos", "arm_right_shoulder_lift.pos", "arm_right_elbow_flex.pos",
    "arm_right_wrist_flex.pos", "arm_right_wrist_roll.pos", "arm_right_gripper.pos",
)
FOLLOWER = {key: float(index) for index, key in enumerate(ARM_KEYS)}
LEADER = {key.removeprefix("arm_"): value for key, value in FOLLOWER.items()}


class FreshReplyRobot:
    latest_observation_roundtrip_age_s = 0.0


def load_teleoperate():
    path = Path(__file__).resolve().parents[2] / "examples" / "alohamini" / "teleoperate_bi.py"
    module_name = f"test_am1_recovery_{id(object())}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
        sys.path.remove(str(path.parent))
    return module


class FakeLocalRobot:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def send_action(self, action):
        self.events.append(("action", dict(action)))

    def stop_motion(self):
        self.events.append(("zero_body",))

    def hold_follower_arms(self):
        self.events.append(("hold_present_arms",))


def test_delayed_post_pause_reply_cannot_qualify_as_current_feedback():
    module = load_teleoperate()

    class Robot(FreshReplyRobot):
        observation_sequence = 0
        latest_observation_error = None
        latest_raw_observation_keys = frozenset(FOLLOWER)
        latest_observation_roundtrip_age_s = 1.25

        def get_observation(self):
            self.observation_sequence += 1
            self.latest_observation_received_at = time.monotonic()
            return dict(FOLLOWER)

    class UnreadLeader:
        def get_action(self):
            raise AssertionError("a delayed host sample must not advance the leader")

    with pytest.raises(module.TransientFollowerObservation, match="request/reply age"):
        module.read_fresh_am1_live_sample(
            Robot(), UnreadLeader(), previous_sequence=0,
            require_current_request=True,
        )


def test_request_age_plus_local_sampling_delay_cannot_exceed_freshness_budget():
    module = load_teleoperate()

    class Robot:
        observation_sequence = 0
        latest_observation_error = None
        latest_raw_observation_keys = frozenset(FOLLOWER)
        latest_observation_roundtrip_age_s = 0.6

        def get_observation(self):
            self.observation_sequence += 1
            self.latest_observation_received_at = time.monotonic() - 0.6
            return dict(FOLLOWER)

    class Leader:
        def get_action(self): return dict(LEADER)

    with pytest.raises(module.StaleFollowerObservation, match="request/reply plus local age"):
        module.read_fresh_am1_live_sample(
            Robot(), Leader(), previous_sequence=0, require_current_request=True,
        )


@pytest.mark.parametrize("failed_send", [1, 2, "watchdog"])
def test_one_bounded_command_backpressure_pauses_then_requires_host_ack_to_resume(capsys, failed_send):
    module = load_teleoperate()
    host = FakeLocalRobot()
    control = alohamini_host.AM1LocalControl()

    class Sender:
        calls = 0

        def __enter__(self): return self
        def __exit__(self, *args): return False
        def send_action(self, action):
            self.calls += 1
            if failed_send == "watchdog" and self.calls == 2:
                control.watchdog_stop(host)
            if self.calls == failed_send or (failed_send == "watchdog" and 2 <= self.calls <= 4):
                raise alohamini_host.zmq.Again()
            control.apply(host, dict(action))

    class Robot(FreshReplyRobot):
        def __init__(self):
            self.observation_sequence = 0
            self.latest_observation_received_at = time.monotonic()
            self.latest_observation_error = None
            self.latest_raw_observation_keys = frozenset(FOLLOWER)
            self.latest_am1_local_feedback = {"version": 1, "state": "ready", "epoch": -1, "observation_id": 0}

        def make_live_command_sender(self): return Sender()
        def retire_observation_requests(self): pass
        def get_observation(self):
            time.sleep(0.03)
            self.observation_sequence += 1
            self.latest_observation_received_at = time.monotonic()
            feedback = {}
            control.annotate(feedback)
            self.latest_am1_local_feedback = feedback[FEEDBACK]
            return dict(FOLLOWER)

    class Leader:
        def get_action(self): return dict(LEADER)

    robot = Robot()
    active_announcements = []
    module.run_am1_live_sender(
        robot, Leader(), initial_arm_target=FOLLOWER, initial_observation_sequence=0,
        initial_follower_observed_at=robot.latest_observation_received_at,
        initial_follower_positions=FOLLOWER, fps=10, duration_s=1.1,
        live_arm_scope="both", profile_cadence=False,
        body_action_supplier=module.make_zero_action, recovery_enabled=True,
        announce_active=lambda: active_announcements.append(control.state),
    )
    assert control.state == "active" and control.epoch == 2
    assert active_announcements == ["active"]
    assert "PAUSED" in capsys.readouterr().out


def test_first_active_ack_after_pause_rechecks_leader_before_announcing():
    module = load_teleoperate()
    host = FakeLocalRobot()
    control = alohamini_host.AM1LocalControl()
    announced = []

    class Sender:
        calls = 0

        def __enter__(self): return self
        def __exit__(self, *args): return False
        def send_action(self, action):
            self.calls += 1
            if self.calls == 1:
                raise alohamini_host.zmq.Again()
            control.apply(host, dict(action))

    class Robot(FreshReplyRobot):
        def __init__(self):
            self.observation_sequence = 0
            self.latest_observation_received_at = time.monotonic()
            self.latest_observation_error = None
            self.latest_raw_observation_keys = frozenset(FOLLOWER)
            self.latest_am1_local_feedback = {
                "version": 1, "state": "ready", "epoch": -1, "observation_id": 0,
            }

        def make_live_command_sender(self): return Sender()
        def retire_observation_requests(self): pass
        def get_observation(self):
            time.sleep(0.025)
            self.observation_sequence += 1
            self.latest_observation_received_at = time.monotonic()
            feedback = {}
            control.annotate(feedback)
            self.latest_am1_local_feedback = feedback[FEEDBACK]
            return dict(FOLLOWER)

    class Leader:
        def get_action(self):
            if control.state == "active" and control.epoch == 2:
                return {**LEADER, "left_elbow_flex.pos": LEADER["left_elbow_flex.pos"] + 6}
            return dict(LEADER)

    with pytest.raises(module.SafetyRefusal, match="leader moved before live admission"):
        module.run_am1_live_sender(
            Robot(), Leader(), initial_arm_target=FOLLOWER, initial_observation_sequence=0,
            initial_follower_observed_at=time.monotonic(), initial_follower_positions=FOLLOWER,
            fps=10, duration_s=2, live_arm_scope="both", profile_cadence=False,
            body_action_supplier=module.make_zero_action, recovery_enabled=True,
            announce_active=lambda: announced.append(True),
        )
    assert announced == []
    assert all(event[1]["arm_left_elbow_flex.pos"] == FOLLOWER["arm_left_elbow_flex.pos"]
               for event in host.events if event[0] == "action")


def test_host_ack_arriving_after_initial_admission_deadline_cannot_announce_active(monkeypatch):
    module = load_teleoperate()
    monkeypatch.setattr(module, "AM1_LIVE_OBSERVATION_MAX_AGE_S", 10.0)
    factor = 10.0
    real_started = time.monotonic()
    clock = lambda: (time.monotonic() - real_started) * factor
    announced = []

    class Sender:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def send_action(self, action): pass

    class Robot(FreshReplyRobot):
        def __init__(self):
            self.observation_sequence = 0
            self.latest_observation_received_at = clock()
            self.latest_observation_error = None
            self.latest_raw_observation_keys = frozenset(FOLLOWER)
            self.latest_am1_local_feedback = {
                "version": 1, "state": "ready", "epoch": -1, "observation_id": 0,
            }

        def make_live_command_sender(self): return Sender()
        def retire_observation_requests(self): pass
        def get_observation(self):
            time.sleep(0.32)
            self.observation_sequence += 1
            self.latest_observation_received_at = clock()
            self.latest_am1_local_feedback = {
                "version": 1, "state": "active", "epoch": 0,
                "observation_id": self.observation_sequence,
            }
            return dict(FOLLOWER)

    with pytest.raises(module.SafetyRefusal, match="initial active acknowledgement.*within 3s"):
        module.run_am1_live_sender(
            Robot(), SimpleNamespace(get_action=lambda: dict(LEADER)),
            initial_arm_target=FOLLOWER, initial_observation_sequence=0,
            initial_follower_observed_at=clock(), initial_follower_positions=FOLLOWER,
            fps=10, duration_s=1.0, live_arm_scope="both", profile_cadence=False,
            body_action_supplier=module.make_zero_action, recovery_enabled=True,
            monotonic=clock, sleep_fn=lambda seconds: time.sleep(seconds / factor),
            announce_active=lambda: announced.append(True),
        )
    assert announced == []


def test_sender_cannot_mark_admission_after_deciding_startup_timeout(monkeypatch):
    module = load_teleoperate()
    monkeypatch.setattr(module, "AM1_LOCAL_AUTOMATIC_PAUSE_S", 0.05)

    class Sender:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def send_action(self, action): raise alohamini_host.zmq.Again()

    class Robot:
        def make_live_command_sender(self): return Sender()

    sender = module.AM1LiveActionSender(
        Robot(), initial_action=module.make_am1_live_action(FOLLOWER),
        initial_observation_sequence=0, fps=10, duration_s=1,
        profile_cadence=False, recovery_enabled=True,
    )
    sender.start()
    sender.join()
    with pytest.raises(module.SafetyRefusal, match="initial active acknowledgement"):
        sender.mark_live_admitted()


@pytest.mark.parametrize("fail_first_send", [False, True])
def test_failed_atomic_admission_never_prints_active_banner(monkeypatch, fail_first_send):
    module = load_teleoperate()
    host = FakeLocalRobot()
    control = alohamini_host.AM1LocalControl()
    announced = []
    monkeypatch.setattr(
        module.AM1LiveActionSender, "mark_live_admitted",
        lambda sender: (_ for _ in ()).throw(module.SafetyRefusal("admission already expired")),
    )

    class Sender:
        calls = 0

        def __enter__(self): return self
        def __exit__(self, *args): return False
        def send_action(self, action):
            self.calls += 1
            if fail_first_send and self.calls == 1:
                raise alohamini_host.zmq.Again()
            control.apply(host, dict(action))

    class Robot(FreshReplyRobot):
        def __init__(self):
            self.observation_sequence = 0
            self.latest_observation_received_at = time.monotonic()
            self.latest_observation_error = None
            self.latest_raw_observation_keys = frozenset(FOLLOWER)
            self.latest_am1_local_feedback = {
                "version": 1, "state": "ready", "epoch": -1, "observation_id": 0,
            }

        def make_live_command_sender(self): return Sender()
        def retire_observation_requests(self): pass
        def get_observation(self):
            time.sleep(0.02)
            self.observation_sequence += 1
            self.latest_observation_received_at = time.monotonic()
            feedback = {}
            control.annotate(feedback)
            self.latest_am1_local_feedback = feedback[FEEDBACK]
            return dict(FOLLOWER)

    with pytest.raises(module.SafetyRefusal, match="admission already expired"):
        module.run_am1_live_sender(
            Robot(), SimpleNamespace(get_action=lambda: dict(LEADER)),
            initial_arm_target=FOLLOWER, initial_observation_sequence=0,
            initial_follower_observed_at=time.monotonic(), initial_follower_positions=FOLLOWER,
            fps=10, duration_s=2, live_arm_scope="both", profile_cadence=False,
            body_action_supplier=module.make_zero_action, recovery_enabled=True,
            announce_active=lambda: announced.append(True),
        )
    assert announced == []


def test_resume_send_backpressure_keeps_prior_host_hold_until_new_pause_ack(capsys):
    module = load_teleoperate()
    host = FakeLocalRobot()
    control = alohamini_host.AM1LocalControl()

    class Sender:
        failed_resume = False

        def __enter__(self): return self
        def __exit__(self, *args): return False
        def send_action(self, action):
            marker = action[CONTROL]
            if marker["mode"] == "active" and marker["epoch"] == 2 and not self.failed_resume:
                self.failed_resume = True
                raise alohamini_host.zmq.Again()
            control.apply(host, dict(action))

    class Robot(FreshReplyRobot):
        def __init__(self):
            self.started_at = time.monotonic()
            self.observation_sequence = 0
            self.latest_observation_received_at = self.started_at
            self.latest_observation_error = None
            self.latest_raw_observation_keys = frozenset(FOLLOWER)
            self.latest_am1_local_feedback = {"version": 1, "state": "ready", "epoch": -1, "observation_id": 0}

        def make_live_command_sender(self): return Sender()
        def retire_observation_requests(self): pass
        def get_observation(self):
            time.sleep(0.03)
            elapsed = time.monotonic() - self.started_at
            if 0.15 < elapsed < 1.3:
                return dict(FOLLOWER)
            self.observation_sequence += 1
            self.latest_observation_received_at = time.monotonic()
            feedback = {}
            control.annotate(feedback)
            self.latest_am1_local_feedback = feedback[FEEDBACK]
            return dict(FOLLOWER)

    class Leader:
        def get_action(self): return dict(LEADER)

    robot = Robot()
    module.run_am1_live_sender(
        robot, Leader(), initial_arm_target=FOLLOWER, initial_observation_sequence=0,
        initial_follower_observed_at=robot.started_at, initial_follower_positions=FOLLOWER,
        fps=10, duration_s=2.9, live_arm_scope="both", profile_cadence=False,
        body_action_supplier=module.make_zero_action, recovery_enabled=True,
    )
    assert control.state == "active" and control.epoch == 4
    output = capsys.readouterr().out
    assert output.count("PAUSED") >= 2 and output.count("RECOVERED") == 1


def test_recovery_duration_expires_even_if_no_command_send_ever_succeeds(capsys):
    module = load_teleoperate()
    factor = 20.0
    real_started = time.monotonic()
    clock = lambda: (time.monotonic() - real_started) * factor

    class Sender:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def send_action(self, action): raise alohamini_host.zmq.Again()

    class Robot(FreshReplyRobot):
        def __init__(self):
            self.observation_sequence = 0
            self.latest_observation_received_at = clock()
            self.latest_observation_error = None
            self.latest_raw_observation_keys = frozenset(FOLLOWER)
            self.latest_am1_local_feedback = {"version": 1, "state": "ready", "epoch": -1, "observation_id": 0}

        def make_live_command_sender(self): return Sender()
        def retire_observation_requests(self): pass
        def get_observation(self):
            time.sleep(0.002)
            self.observation_sequence += 1
            self.latest_observation_received_at = clock()
            feedback = dict(self.latest_am1_local_feedback)
            feedback["observation_id"] += 1
            self.latest_am1_local_feedback = feedback
            return dict(FOLLOWER)

    class Leader:
        def get_action(self): return dict(LEADER)

    robot = Robot()
    active_announcements = []
    with pytest.raises(module.SafetyRefusal, match="initial active acknowledgement did not arrive"):
        module.run_am1_live_sender(
            robot, Leader(), initial_arm_target=FOLLOWER, initial_observation_sequence=0,
            initial_follower_observed_at=robot.latest_observation_received_at,
            initial_follower_positions=FOLLOWER, fps=10, duration_s=1,
            live_arm_scope="both", profile_cadence=True,
            body_action_supplier=module.make_zero_action, recovery_enabled=True,
            monotonic=clock, sleep_fn=lambda seconds: time.sleep(seconds / factor),
            announce_active=lambda: active_announcements.append(True),
        )
    assert clock() < 4.0
    assert capsys.readouterr().out.count('"event": "am1_client_live_start"') == 1
    assert active_announcements == []


def test_unified_live_duration_begins_only_after_host_active_ack():
    module = load_teleoperate()
    factor = 10.0
    real_started = time.monotonic()
    clock = lambda: (time.monotonic() - real_started) * factor
    host = FakeLocalRobot()
    control = alohamini_host.AM1LocalControl()
    sent_at = []
    announced_at = []

    class Sender:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def send_action(self, action):
            sent_at.append(clock())
            control.apply(host, dict(action))

    class Robot(FreshReplyRobot):
        def __init__(self):
            self.observation_sequence = 0
            self.latest_observation_received_at = clock()
            self.latest_observation_error = None
            self.latest_raw_observation_keys = frozenset(FOLLOWER)
            self.latest_am1_local_feedback = {
                "version": 1, "state": "ready", "epoch": -1, "observation_id": 0,
            }

        def make_live_command_sender(self): return Sender()
        def retire_observation_requests(self): pass
        def get_observation(self):
            time.sleep(0.002)
            self.observation_sequence += 1
            self.latest_observation_received_at = clock()
            feedback = {}
            control.annotate(feedback)
            if clock() < 0.8:
                feedback[FEEDBACK]["state"] = "ready"
                feedback[FEEDBACK]["epoch"] = -1
            self.latest_am1_local_feedback = feedback[FEEDBACK]
            return dict(FOLLOWER)

    module.run_am1_live_sender(
        Robot(), SimpleNamespace(get_action=lambda: dict(LEADER)),
        initial_arm_target=FOLLOWER, initial_observation_sequence=0,
        initial_follower_observed_at=clock(), initial_follower_positions=FOLLOWER,
        fps=10, duration_s=1.0, live_arm_scope="both", profile_cadence=False,
        body_action_supplier=module.make_zero_action, recovery_enabled=True,
        monotonic=clock, sleep_fn=lambda seconds: time.sleep(seconds / factor),
        announce_active=lambda: announced_at.append(clock()),
    )
    assert len(announced_at) == 1
    assert sent_at[-1] - announced_at[0] >= 0.85


@pytest.mark.parametrize("leader_delta", [1.5, 6.0])
def test_delayed_initial_ack_never_forwards_unapproved_leader_jump(leader_delta):
    module = load_teleoperate()
    factor = 10.0
    real_started = time.monotonic()
    clock = lambda: (time.monotonic() - real_started) * factor
    host = FakeLocalRobot()
    control = alohamini_host.AM1LocalControl()
    sent = []
    announced_at = []
    timeline = []

    class Sender:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def send_action(self, action):
            sent.append((clock(), dict(action)))
            timeline.append(("send", action["arm_left_elbow_flex.pos"]))
            control.apply(host, dict(action))

    class Robot(FreshReplyRobot):
        def __init__(self):
            self.observation_sequence = 0
            self.latest_observation_received_at = clock()
            self.latest_observation_error = None
            self.latest_raw_observation_keys = frozenset(FOLLOWER)
            self.latest_am1_local_feedback = {
                "version": 1, "state": "ready", "epoch": -1, "observation_id": 0,
            }

        def make_live_command_sender(self): return Sender()
        def retire_observation_requests(self): pass
        def get_observation(self):
            time.sleep(0.002)
            self.observation_sequence += 1
            self.latest_observation_received_at = clock()
            feedback = {}
            control.annotate(feedback)
            if clock() < 0.6:
                feedback[FEEDBACK]["state"] = "ready"
                feedback[FEEDBACK]["epoch"] = -1
            self.latest_am1_local_feedback = feedback[FEEDBACK]
            return dict(FOLLOWER)

    leader_action = {**LEADER, "left_elbow_flex.pos": LEADER["left_elbow_flex.pos"] + leader_delta}
    kwargs = dict(
        initial_arm_target=FOLLOWER, initial_observation_sequence=0,
        initial_follower_observed_at=clock(), initial_follower_positions=FOLLOWER,
        fps=10, duration_s=1.6, live_arm_scope="both", profile_cadence=False,
        body_action_supplier=module.make_zero_action, recovery_enabled=True,
        monotonic=clock, sleep_fn=lambda seconds: time.sleep(seconds / factor),
        announce_active=lambda: (announced_at.append(clock()), timeline.append(("active", None))),
    )
    if leader_delta > module.STARTUP_SYNC_LEADER_DRIFT:
        with pytest.raises(module.SafetyRefusal, match="leader moved before live admission"):
            module.run_am1_live_sender(
                Robot(), SimpleNamespace(get_action=lambda: dict(leader_action)), **kwargs,
            )
        assert announced_at == []
        assert all(action["arm_left_elbow_flex.pos"] == FOLLOWER["arm_left_elbow_flex.pos"]
                   for _, action in sent)
    else:
        module.run_am1_live_sender(
            Robot(), SimpleNamespace(get_action=lambda: dict(leader_action)), **kwargs,
        )
        assert len(announced_at) == 1
        assert all(value == FOLLOWER["arm_left_elbow_flex.pos"] for event, value in
                   timeline[:timeline.index(("active", None))] if event == "send")
        values = [FOLLOWER["arm_left_elbow_flex.pos"]] + [
            action["arm_left_elbow_flex.pos"] for _, action in sent
        ]
        assert all(abs(after - before) <= module.STARTUP_SYNC_MAX_STEP + 1e-9
                   for before, after in zip(values, values[1:]))


def local_action(mode: str, epoch: int, *, x_vel: float = 0.0):
    return {
        CONTROL: {"version": 1, "mode": mode, "epoch": epoch},
        "arm_left_elbow_flex.pos": 12.0,
        "x.vel": x_vel,
        "y.vel": 0.0,
        "theta.vel": 0.0,
        "lift_axis.vel": 0,
    }


def test_host_pause_holds_measured_arms_and_zeros_body_before_ack():
    robot = FakeLocalRobot()
    control = alohamini_host.AM1LocalControl()
    assert control.apply(robot, local_action("active", 0, x_vel=0.15)) is True

    assert control.apply(robot, local_action("pause", 1, x_vel=0.15)) is True
    observation = {}
    control.annotate(observation)

    assert robot.events == [
        ("action", {"arm_left_elbow_flex.pos": 12.0, "x.vel": 0.15, "y.vel": 0.0,
                    "theta.vel": 0.0, "lift_axis.vel": 0}),
        ("zero_body",),
        ("hold_present_arms",),
    ]
    assert observation[FEEDBACK] == {"version": 1, "state": "paused", "epoch": 1, "observation_id": 1}


def test_host_never_replays_pre_pause_action_or_resumes_without_pause_ack():
    robot = FakeLocalRobot()
    control = alohamini_host.AM1LocalControl()
    control.apply(robot, local_action("active", 0))
    control.apply(robot, local_action("pause", 1))
    stopped_count = len(robot.events)

    assert control.apply(robot, local_action("active", 0, x_vel=0.15)) is False
    assert robot.events == robot.events[:stopped_count]
    assert control.apply(robot, local_action("active", 2)) is True
    assert control.apply(robot, local_action("pause", 1)) is False
    assert robot.events[-1] == ("action", {"arm_left_elbow_flex.pos": 12.0,
                                          "x.vel": 0.0, "y.vel": 0.0,
                                          "theta.vel": 0.0, "lift_axis.vel": 0})


def test_host_accepts_pause_before_conflated_first_active_action():
    robot = FakeLocalRobot()
    control = alohamini_host.AM1LocalControl()

    assert control.apply(robot, local_action("pause", 1)) is True
    assert robot.events == [("zero_body",), ("hold_present_arms",)]
    assert control.state == "paused" and control.epoch == 1
    assert control.apply(robot, local_action("active", 0, x_vel=0.15)) is False


def test_host_watchdog_holds_arms_and_requires_new_pause_epoch_before_rearm():
    robot = FakeLocalRobot()
    control = alohamini_host.AM1LocalControl()
    control.apply(robot, local_action("active", 0))

    control.watchdog_stop(robot)
    assert robot.events[-2:] == [("zero_body",), ("hold_present_arms",)]
    assert control.apply(robot, local_action("active", 0, x_vel=0.15)) is False
    with pytest.raises(RuntimeError, match="acknowledged pause sequence"):
        control.apply(robot, local_action("active", 2, x_vel=0.15))
    control.apply(robot, local_action("pause", 1))
    assert control.apply(robot, local_action("active", 2)) is True


def test_host_accepts_only_final_body_zero_after_local_activation_and_holds_arms():
    robot = FakeLocalRobot()
    control = alohamini_host.AM1LocalControl()
    control.apply(robot, local_action("active", 0, x_vel=0.15))

    with pytest.raises(RuntimeError, match="marker missing"):
        control.apply(robot, {"x.vel": 0.01, "y.vel": 0, "theta.vel": 0, "lift_axis.vel": 0})
    assert control.apply(robot, load_teleoperate().make_zero_action()) is True
    assert control.state == "stopped"
    assert robot.events[-2:] == [("zero_body",), ("hold_present_arms",)]
    assert control.apply(robot, local_action("active", 0, x_vel=0.15)) is False


def test_outer_unified_local_cleanup_final_zero_reaches_host_without_fault(monkeypatch, tmp_path):
    module = load_teleoperate()
    host = FakeLocalRobot()
    control = alohamini_host.AM1LocalControl()
    events = []

    class Client:
        latest_observation_roundtrip_age_s = 0.0
        latest_am1_local_feedback = {
            "version": 1, "state": "ready", "epoch": -1, "observation_id": 1,
        }
        def __init__(self, config):
            self.config = SimpleNamespace(teleop_keys={"quit": "q"})
            self.observation_sequence = 0

        def connect(self, *, cancel_check=None): events.append("robot_connect")
        def send_action(self, action):
            control.apply(host, dict(action))
            return dict(action)
        def disconnect(self): events.append("robot_disconnect")

    class Arm:
        is_calibrated = True
        def connect(self, **kwargs): pass
        def disconnect(self): events.append("arm_disconnect")

    class Leader:
        def __init__(self, config):
            self.left_arm = Arm()
            self.right_arm = Arm()

    class Keyboard:
        is_connected = True
        def __init__(self, config): pass
        def connect(self): pass
        def get_action(self): return {}
        def disconnect(self): events.append("keyboard_disconnect")

    def sync(*args, **kwargs):
        return dict(FOLLOWER), dict(FOLLOWER), time.monotonic()

    def alignment(robot, *args, **kwargs):
        robot.observation_sequence += 1
        return sync()

    def live(robot, leader, **kwargs):
        assert kwargs["recovery_enabled"] is True
        control.apply(host, local_action("active", 0, x_vel=0.15))

    monkeypatch.setattr(module, "AlohaMiniClient", Client)
    monkeypatch.setattr(module, "BiSOLeader", Leader)
    monkeypatch.setattr(module, "KeyboardTeleop", Keyboard)
    monkeypatch.setattr(module, "run_startup_sync", sync)
    monkeypatch.setattr(module, "run_alignment_gate", alignment)
    monkeypatch.setattr(module, "run_am1_live_sender", live)
    args = module.parse_args([
        "--left_port", "COM8", "--right_port", "COM7", "--local_mode", "--no_cameras",
        "--no_rerun", "--start_paused", "--startup_mode", "sync",
        "--startup_sync_side", "both", "--startup_sync_duration_s", "30",
        "--max_start_mismatch", "10", "--live_arm_scope", "both", "--fps", "10",
        "--duration_s", "1", "--profile_cadence", "--external_stop_file", str(tmp_path / "stop"),
        "--unified_session_enter_confirmations",
    ], platform_name="win32")

    assert module.run_teleoperation(args, input_fn=lambda prompt: "") == 0
    assert control.state == "stopped"
    assert host.events[-2:] == [("zero_body",), ("hold_present_arms",)]
    assert events[-4:] == ["keyboard_disconnect", "arm_disconnect", "arm_disconnect", "robot_disconnect"]


def test_real_local_zmq_transport_pauses_recovers_and_latches_final_zero(capsys):
    """Loopback-only ROUTER/PULL peers exercise the actual token and socket paths."""
    module = load_teleoperate()
    from lerobot.robots.alohamini.alohamini_client import AlohaMiniClient
    from lerobot.robots.alohamini.config_alohamini import AlohaMiniClientConfig

    zmq = alohamini_host.zmq
    context = zmq.Context()
    command_socket = context.socket(zmq.PULL)
    observation_socket = context.socket(zmq.ROUTER)
    command_port = command_socket.bind_to_random_port("tcp://127.0.0.1")
    observation_port = observation_socket.bind_to_random_port("tcp://127.0.0.1")
    host = FakeLocalRobot()
    control = alohamini_host.AM1LocalControl()
    stop = threading.Event()
    gap_start = [None]
    errors = []

    def serve():
        try:
            while not stop.is_set():
                try:
                    command = json.loads(command_socket.recv_string(zmq.NOBLOCK))
                except zmq.Again:
                    pass
                else:
                    control.apply(host, command)
                try:
                    identity, token = observation_socket.recv_multipart(zmq.NOBLOCK)
                except zmq.Again:
                    pass
                else:
                    observation = dict(FOLLOWER)
                    control.annotate(observation)
                    started = gap_start[0]
                    gap_age = time.monotonic() - started if started is not None else -1.0
                    if not 0.2 < gap_age < 1.45:
                        observation_socket.send_multipart(
                            [identity, token, json.dumps(observation).encode("utf-8")], zmq.NOBLOCK,
                        )
                time.sleep(0.005)
        except BaseException as exc:
            errors.append(exc)
        finally:
            command_socket.close(0)
            observation_socket.close(0)

    server = threading.Thread(target=serve, name="fake-am1-zmq-peer")
    server.start()
    client = AlohaMiniClient(AlohaMiniClientConfig(
        remote_ip="127.0.0.1", port_zmq_cmd=command_port,
        port_zmq_observations=observation_port, id="fake-am1-local",
        robot_model="alohamini1", cameras={}, observation_request_window=3,
        polling_timeout_ms=100, command_send_timeout_ms=50,
    ))

    class Leader:
        def get_action(self): return dict(LEADER)

    try:
        client.connect()
        initial = client.get_observation()
        assert client.observation_sequence == 1
        gap_start[0] = time.monotonic()
        module.run_am1_live_sender(
            client, Leader(), initial_arm_target=FOLLOWER,
            initial_observation_sequence=client.observation_sequence,
            initial_follower_observed_at=client.latest_observation_received_at,
            initial_follower_positions=module.extract_am1_arm_positions(
                initial, source="loopback follower", leader_sample=False,
            ),
            fps=10, duration_s=2.6, live_arm_scope="both", profile_cadence=False,
            body_action_supplier=module.make_zero_action, recovery_enabled=True,
        )
        assert control.state == "active" and control.epoch == 2
        assert "RECOVERED" in capsys.readouterr().out
        client.send_action(module.make_zero_action())
        deadline = time.monotonic() + 0.6
        while control.state != "stopped" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert control.state == "stopped"
        assert host.events[-2:] == [("zero_body",), ("hold_present_arms",)]
        assert errors == []
    finally:
        if client.is_connected:
            client.disconnect()
        stop.set()
        server.join(timeout=2)
        context.term()
        assert not server.is_alive()


def test_arm_hold_seeds_raw_present_position_not_the_old_goal():
    events: list[tuple] = []

    class Bus:
        def __init__(self, name: str, positions: dict[str, int]):
            self.name = name
            self.positions = positions

        def read(self, register, motor, *, normalize=True, num_retry=3):
            events.append((self.name, "read", register, motor, normalize))
            return self.positions[motor]

        def write(self, register, motor, value, *, normalize=True, num_retry=3):
            events.append((self.name, "write", register, motor, value, normalize))

    robot = object.__new__(AlohaMini)
    robot.config = SimpleNamespace(robot_model="alohamini1", no_follower=False)
    robot.left_arm_motors = ["arm_left_elbow_flex"]
    robot.right_arm_motors = ["arm_right_elbow_flex"]
    robot.left_bus = Bus("left", {"arm_left_elbow_flex": 2011})
    robot.right_bus = Bus("right", {"arm_right_elbow_flex": 2117})

    robot.hold_follower_arms()

    assert events == [
        ("left", "read", "Present_Position", "arm_left_elbow_flex", False),
        ("left", "write", "Goal_Position", "arm_left_elbow_flex", 2011, False),
        ("right", "read", "Present_Position", "arm_right_elbow_flex", False),
        ("right", "write", "Goal_Position", "arm_right_elbow_flex", 2117, False),
    ]


def test_real_host_loop_applies_pause_before_ack_and_drops_queued_pre_pause_action(monkeypatch):
    events: list[tuple] = []
    sent_observations: list[dict] = []

    class CommandSocket:
        commands = [
            local_action("active", 0, x_vel=0.15),
            local_action("pause", 1, x_vel=0.15),
            local_action("active", 0, x_vel=0.15),
        ]

        def recv_string(self, flags):
            if self.commands:
                return json.dumps(self.commands.pop(0))
            raise alohamini_host.zmq.Again()

    class ObservationSocket:
        def recv_multipart(self, flags):
            return [b"client", b"request"]

        def send_multipart(self, parts, flags):
            sent_observations.append(json.loads(parts[2]))

    class Host:
        def __init__(self, config):
            self.zmq_cmd_socket = CommandSocket()
            self.zmq_observation_socket = ObservationSocket()
            self.watchdog_timeout_ms = 1000
            self.max_loop_freq_hz = 30
            self.connection_time_s = 0.5

        def disconnect(self):
            events.append(("host_disconnect",))

    class Robot(FakeLocalRobot):
        def __init__(self, config):
            super().__init__()
            self.config = config
            self.is_connected = False
            self.cameras = {}
            self.logs = {}

        def send_action(self, action):
            events.append(("action", dict(action)))

        def stop_motion(self):
            events.append(("zero_body",))

        def hold_follower_arms(self):
            events.append(("hold_present_arms",))

        def get_observation(self):
            return {}

        def disconnect(self, **kwargs):
            self.is_connected = False
            events.append(("robot_disconnect",))

    monkeypatch.setattr(alohamini_host, "AlohaMini", Robot)
    monkeypatch.setattr(alohamini_host, "AlohaMiniHost", Host)
    monkeypatch.setattr(
        alohamini_host,
        "connect_robot",
        lambda robot, **kwargs: setattr(robot, "is_connected", True),
    )
    monkeypatch.setattr(alohamini_host, "make_host_config", lambda args: object())
    monkeypatch.setattr(
        alohamini_host,
        "make_robot_config",
        lambda args: SimpleNamespace(robot_model="alohamini1", no_follower=False),
    )
    monkeypatch.setattr(alohamini_host, "make_parser", lambda: SimpleNamespace(
        parse_args=lambda: SimpleNamespace(
            robot_model="alohamini1", no_follower=False, no_cameras=True,
            skip_lift_home=False, lift_relief=False, lift_readback=False,
            profile_timing=False, profile_cadence=False, profile_lift_diagnostics=False,
        ),
    ))

    alohamini_host.main()

    assert events[:3] == [
        ("action", {"arm_left_elbow_flex.pos": 12.0, "x.vel": 0.15,
                    "y.vel": 0.0, "theta.vel": 0.0, "lift_axis.vel": 0}),
        ("zero_body",),
        ("hold_present_arms",),
    ]
    assert not any(event[0] == "action" for event in events[3:])
    assert any(obs.get(FEEDBACK, {}).get("state") == "paused"
               and obs[FEEDBACK].get("epoch") == 1
               and type(obs[FEEDBACK].get("observation_id")) is int
               for obs in sent_observations)
    assert events[-2:] == [("robot_disconnect",), ("host_disconnect",)]


@pytest.mark.parametrize("single_miss", [False, True])
def test_actual_local_loop_normal_delivery_and_one_missed_reply_keep_active(single_miss, capsys):
    module = load_teleoperate()
    host = FakeLocalRobot()
    control = alohamini_host.AM1LocalControl()

    class Sender:
        def __enter__(self): return self
        def send_action(self, action): control.apply(host, dict(action))
        def __exit__(self, *args): return False

    class Robot(FreshReplyRobot):
        def __init__(self):
            self.started = time.monotonic()
            self.observation_sequence = 0
            self.latest_observation_received_at = self.started
            self.latest_observation_error = None
            self.latest_raw_observation_keys = frozenset(FOLLOWER)
            self.latest_am1_local_feedback = {"version": 1, "state": "ready", "epoch": -1, "observation_id": 0}
            self.missed = False

        def make_live_command_sender(self): return Sender()
        def retire_observation_requests(self): raise AssertionError("one missed reply is not a pause")
        def get_observation(self):
            time.sleep(0.02)
            if single_miss and not self.missed and self.observation_sequence >= 3:
                self.missed = True
                return dict(FOLLOWER)
            self.observation_sequence += 1
            self.latest_observation_received_at = time.monotonic()
            feedback = {}
            control.annotate(feedback)
            self.latest_am1_local_feedback = feedback[FEEDBACK]
            return dict(FOLLOWER)

    class Leader:
        def get_action(self): return dict(LEADER)

    robot = Robot()
    module.run_am1_live_sender(
        robot, Leader(), initial_arm_target=FOLLOWER, initial_observation_sequence=0,
        initial_follower_observed_at=robot.started, initial_follower_positions=FOLLOWER,
        fps=10, duration_s=0.7, live_arm_scope="both", profile_cadence=False,
        body_action_supplier=module.make_zero_action, recovery_enabled=True,
    )

    assert control.state == "active" and control.epoch == 0
    assert not any(event[0] == "hold_present_arms" for event in host.events)
    assert len([event for event in host.events if event[0] == "action"]) >= 4
    assert "PAUSED" not in capsys.readouterr().out


@pytest.mark.parametrize(("gap_end", "manual"), [(1.35, False), (2.15, False), (3.45, True), (4.35, True)])
def test_actual_local_loop_pauses_at_one_second_and_auto_resumes_after_current_feedback(capsys, gap_end, manual):
    module = load_teleoperate()
    host = FakeLocalRobot()
    host_control = alohamini_host.AM1LocalControl()

    class Sender:
        def __enter__(self):
            return self

        def send_action(self, action):
            host_control.apply(host, dict(action))

        def __exit__(self, exc_type, exc, traceback):
            return False

    class Robot(FreshReplyRobot):
        def __init__(self):
            self.started_at = time.monotonic()
            self.observation_sequence = 0
            self.latest_observation_received_at = self.started_at
            self.latest_observation_error = None
            self.latest_raw_observation_keys = frozenset(FOLLOWER)
            self.latest_am1_local_feedback = {"version": 1, "state": "ready", "epoch": -1, "observation_id": 0}
            self.retire_count = 0

        def make_live_command_sender(self):
            return Sender()

        def retire_observation_requests(self):
            self.retire_count += 1

        def get_observation(self):
            time.sleep(0.03)
            elapsed = time.monotonic() - self.started_at
            if 0.15 < elapsed < gap_end:
                return dict(FOLLOWER)
            self.observation_sequence += 1
            self.latest_observation_received_at = time.monotonic()
            feedback = {}
            host_control.annotate(feedback)
            self.latest_am1_local_feedback = feedback[FEEDBACK]
            return dict(FOLLOWER)

    class Leader:
        def get_action(self):
            return dict(LEADER)

    robot = Robot()
    prompts = []
    module.run_am1_live_sender(
        robot,
        Leader(),
        initial_arm_target=FOLLOWER,
        initial_observation_sequence=0,
        initial_follower_observed_at=robot.started_at,
        initial_follower_positions=FOLLOWER,
        fps=10,
        duration_s=gap_end + 0.9,
        live_arm_scope="both",
        profile_cadence=False,
        body_action_supplier=module.make_zero_action,
        recovery_enabled=True,
        input_fn=lambda prompt: prompts.append(prompt) or "",
    )

    assert robot.retire_count == 1
    assert ("zero_body",) in host.events
    assert ("hold_present_arms",) in host.events
    assert host_control.state == "active" and host_control.epoch == 2
    output = capsys.readouterr().out
    assert "PAUSED" in output and "RECOVERED" in output
    assert len(prompts) == int(manual)
    assert ("RESUME-NEEDS-ENTER" in output) == manual
    assert f'"resume_mode": "{"manual" if manual else "automatic"}"' in output


def test_actual_local_loop_recovers_twice_without_permanent_latch(capsys):
    module = load_teleoperate()
    host = FakeLocalRobot()
    control = alohamini_host.AM1LocalControl()

    class Sender:
        def __enter__(self): return self
        def send_action(self, action): control.apply(host, dict(action))
        def __exit__(self, *args): return False

    class Robot(FreshReplyRobot):
        def __init__(self):
            self.started = time.monotonic()
            self.observation_sequence = 0
            self.latest_observation_received_at = self.started
            self.latest_observation_error = None
            self.latest_raw_observation_keys = frozenset(FOLLOWER)
            self.latest_am1_local_feedback = {"version": 1, "state": "ready", "epoch": -1, "observation_id": 0}
            self.retired = 0

        def make_live_command_sender(self): return Sender()
        def retire_observation_requests(self): self.retired += 1
        def get_observation(self):
            time.sleep(0.02)
            elapsed = time.monotonic() - self.started
            if 0.15 < elapsed < 1.35 or 1.95 < elapsed < 3.15:
                return dict(FOLLOWER)
            self.observation_sequence += 1
            self.latest_observation_received_at = time.monotonic()
            feedback = {}
            control.annotate(feedback)
            self.latest_am1_local_feedback = feedback[FEEDBACK]
            return dict(FOLLOWER)

    class Leader:
        def get_action(self): return dict(LEADER)

    robot = Robot()
    module.run_am1_live_sender(
        robot, Leader(), initial_arm_target=FOLLOWER, initial_observation_sequence=0,
        initial_follower_observed_at=robot.started, initial_follower_positions=FOLLOWER,
        fps=10, duration_s=4.1, live_arm_scope="both", profile_cadence=False,
        body_action_supplier=module.make_zero_action, recovery_enabled=True,
        input_fn=lambda _: (_ for _ in ()).throw(AssertionError("both short gaps auto-resume")),
    )

    assert robot.retired == 2
    assert control.state == "active" and control.epoch == 4
    assert capsys.readouterr().out.count("RECOVERED") == 2


def test_sender_pauses_during_blocked_reader_and_never_republishes_held_body_input():
    module = load_teleoperate()
    host = FakeLocalRobot()
    control = alohamini_host.AM1LocalControl()

    class CommandSender:
        def __enter__(self): return self
        def send_action(self, action): control.apply(host, dict(action))
        def __exit__(self, *args): return False

    class Robot(FreshReplyRobot):
        def make_live_command_sender(self): return CommandSender()

    started = time.monotonic()
    body = module.AM1LiveBodyMailbox()
    body.publish({"x.vel": 0.15, "y.vel": 0, "theta.vel": 0, "lift_axis.vel": 0}, published_at=started)
    sender = module.AM1LiveActionSender(
        Robot(), initial_action=module.make_am1_live_action(FOLLOWER),
        initial_observation_sequence=1, initial_follower_observed_at=started,
        fps=10, duration_s=1.5, profile_cadence=False, body_mailbox=body,
        recovery_enabled=True,
    )
    sender.start()
    sender.mark_live_admitted()  # This sender-only fixture begins after host admission.
    time.sleep(1.15)  # The owning observation reader is deliberately unable to run here.
    body.publish({"x.vel": 0.15, "y.vel": 0, "theta.vel": 0, "lift_axis.vel": 0}, published_at=time.monotonic())
    sender.join()

    assert sender.snapshot().error is None
    assert control.state == "paused" and control.epoch == 1
    assert host.events[-2:] == [("zero_body",), ("hold_present_arms",)]
    assert body.snapshot(now=time.monotonic()) == module.make_zero_action()


@pytest.mark.parametrize(("release_at", "early_enter_refused"), [(1.45, False), (2.1, True)])
def test_moved_leader_and_held_body_key_require_enter_before_bounded_resume(
    capsys, release_at, early_enter_refused,
):
    module = load_teleoperate()
    host = FakeLocalRobot()
    control = alohamini_host.AM1LocalControl()

    class Sender:
        def __enter__(self): return self
        def send_action(self, action): control.apply(host, dict(action))
        def __exit__(self, *args): return False

    class Robot(FreshReplyRobot):
        def __init__(self):
            self.started = time.monotonic()
            self.observation_sequence = 0
            self.latest_observation_received_at = self.started
            self.latest_observation_error = None
            self.latest_raw_observation_keys = frozenset(FOLLOWER)
            self.latest_am1_local_feedback = {"version": 1, "state": "ready", "epoch": -1, "observation_id": 0}
            self.retired = 0

        def make_live_command_sender(self): return Sender()
        def retire_observation_requests(self): self.retired += 1
        def get_observation(self):
            time.sleep(0.02)
            elapsed = time.monotonic() - self.started
            if 0.15 < elapsed < 1.35:
                return dict(FOLLOWER)
            self.observation_sequence += 1
            self.latest_observation_received_at = time.monotonic()
            feedback = {}
            control.annotate(feedback)
            self.latest_am1_local_feedback = feedback[FEEDBACK]
            return dict(FOLLOWER)

    class Leader:
        def __init__(self, robot): self.robot = robot
        def get_action(self):
            result = dict(LEADER)
            if time.monotonic() - self.robot.started > 1.3:
                result["left_elbow_flex.pos"] += 2.0
            return result

    robot = Robot()
    prompts = []

    def body_input():
        elapsed = time.monotonic() - robot.started
        x = 0.15 if 1.05 < elapsed < release_at else 0.0
        return {"x.vel": x, "y.vel": 0, "theta.vel": 0, "lift_axis.vel": 0}

    def run():
        module.run_am1_live_sender(
            robot, Leader(robot), initial_arm_target=FOLLOWER,
            initial_observation_sequence=0, initial_follower_observed_at=robot.started,
            initial_follower_positions=FOLLOWER, fps=10, duration_s=2.6,
            live_arm_scope="both", profile_cadence=False, body_action_supplier=body_input,
            recovery_enabled=True, input_fn=lambda prompt: prompts.append(prompt) or "",
        )

    if early_enter_refused:
        with pytest.raises(module.SafetyRefusal, match="body controls must be released before Enter"):
            run()
    else:
        run()

    assert len(prompts) == 1
    assert robot.retired == 1
    assert (control.state, control.epoch) == (("paused", 1) if early_enter_refused else ("active", 2))
    assert "RESUME-NEEDS-ENTER" in capsys.readouterr().out
    hold_index = host.events.index(("hold_present_arms",))
    resumed_actions = [event[1] for event in host.events[hold_index + 1:] if event[0] == "action"]
    if early_enter_refused:
        assert resumed_actions == []
        return
    assert resumed_actions[0]["arm_left_elbow_flex.pos"] == FOLLOWER["arm_left_elbow_flex.pos"]
    assert all(action.get("x.vel", 0) == 0 for action in resumed_actions)
    assert all(
        abs(next_action["arm_left_elbow_flex.pos"] - action["arm_left_elbow_flex.pos"]) <= 0.75
        for action, next_action in zip(resumed_actions, resumed_actions[1:])
    )


def test_unusable_feedback_refuses_after_thirty_fake_seconds_without_replaying_arms():
    module = load_teleoperate()
    host = FakeLocalRobot()
    control = alohamini_host.AM1LocalControl()
    factor = 30.0
    real_started = time.monotonic()
    clock = lambda: (time.monotonic() - real_started) * factor

    class Sender:
        def __enter__(self): return self
        def send_action(self, action): control.apply(host, dict(action))
        def __exit__(self, *args): return False

    class Robot(FreshReplyRobot):
        def __init__(self):
            self.observation_sequence = 0
            self.latest_observation_received_at = clock()
            self.latest_observation_error = None
            self.latest_raw_observation_keys = frozenset(FOLLOWER)
            self.latest_am1_local_feedback = {"version": 1, "state": "ready", "epoch": -1, "observation_id": 0}

        def make_live_command_sender(self): return Sender()
        def retire_observation_requests(self): pass
        def get_observation(self):
            time.sleep(0.002)
            if clock() < 0.2:
                self.observation_sequence += 1
                self.latest_observation_received_at = clock()
                feedback = {}
                control.annotate(feedback)
                self.latest_am1_local_feedback = feedback[FEEDBACK]
            return dict(FOLLOWER)

    class Leader:
        def get_action(self): return dict(LEADER)

    robot = Robot()
    with pytest.raises(module.SafetyRefusal, match="did not qualify within 30s"):
        module.run_am1_live_sender(
            robot, Leader(), initial_arm_target=FOLLOWER,
            initial_observation_sequence=0,
            initial_follower_observed_at=robot.latest_observation_received_at,
            initial_follower_positions=FOLLOWER, fps=10, duration_s=100,
            live_arm_scope="both", profile_cadence=False,
            body_action_supplier=module.make_zero_action, recovery_enabled=True,
            monotonic=clock, sleep_fn=lambda seconds: time.sleep(seconds / factor),
            input_fn=lambda _: (_ for _ in ()).throw(AssertionError("no feedback cannot resume")),
        )

    assert control.state == "paused"
    hold_index = host.events.index(("hold_present_arms",))
    assert not any(event[0] == "action" for event in host.events[hold_index + 1:])


@pytest.mark.parametrize("fault", ["malformed", "motor_fault", "host_restart"])
def test_actual_local_loop_does_not_filter_malformed_motor_or_host_restart_faults(fault):
    module = load_teleoperate()
    host = FakeLocalRobot()
    control = alohamini_host.AM1LocalControl()

    class Sender:
        def __enter__(self): return self
        def send_action(self, action): control.apply(host, dict(action))
        def __exit__(self, *args): return False

    class Robot(FreshReplyRobot):
        def __init__(self):
            self.started = time.monotonic()
            self.observation_sequence = 0
            self.latest_observation_received_at = self.started
            self.latest_observation_error = None
            self.latest_raw_observation_keys = frozenset(FOLLOWER)
            self.latest_am1_local_feedback = {"version": 1, "state": "ready", "epoch": -1, "observation_id": 0}

        def make_live_command_sender(self): return Sender()
        def retire_observation_requests(self): raise AssertionError("fault must not become recovery")
        def get_observation(self):
            time.sleep(0.02)
            if self.observation_sequence >= 3 and fault == "motor_fault":
                raise RuntimeError("lift current protection latched")
            self.observation_sequence += 1
            self.latest_observation_received_at = time.monotonic()
            feedback = {}
            control.annotate(feedback)
            self.latest_am1_local_feedback = feedback[FEEDBACK]
            if self.observation_sequence >= 3 and fault == "malformed":
                self.latest_observation_error = "invalid arm field"
            if self.observation_sequence >= 3 and fault == "host_restart":
                self.latest_am1_local_feedback = {
                    "version": 1, "state": "ready", "epoch": -1, "observation_id": feedback[FEEDBACK]["observation_id"] + 1000,
                }
            return dict(FOLLOWER)

    class Leader:
        def get_action(self): return dict(LEADER)

    robot = Robot()
    expected = RuntimeError if fault == "motor_fault" else module.SafetyRefusal
    with pytest.raises(expected, match={
        "malformed": "invalid arm field", "motor_fault": "lift current protection latched",
        "host_restart": "host state/epoch changed unexpectedly",
    }[fault]):
        module.run_am1_live_sender(
            robot, Leader(), initial_arm_target=FOLLOWER, initial_observation_sequence=0,
            initial_follower_observed_at=robot.started, initial_follower_positions=FOLLOWER,
            fps=10, duration_s=2, live_arm_scope="both", profile_cadence=False,
            body_action_supplier=module.make_zero_action, recovery_enabled=True,
        )


@pytest.mark.parametrize("end_kind", ["q", "external_stop", "duration"])
def test_paused_local_loop_honors_q_stop_and_finite_expiry(end_kind):
    module = load_teleoperate()
    host = FakeLocalRobot()
    control = alohamini_host.AM1LocalControl()
    factor = 20.0
    real_started = time.monotonic()
    clock = lambda: (time.monotonic() - real_started) * factor
    quit_requested = False

    class Sender:
        def __enter__(self): return self
        def send_action(self, action): control.apply(host, dict(action))
        def __exit__(self, *args): return False

    class Robot(FreshReplyRobot):
        def __init__(self):
            self.observation_sequence = 0
            self.latest_observation_received_at = clock()
            self.latest_observation_error = None
            self.latest_raw_observation_keys = frozenset(FOLLOWER)
            self.latest_am1_local_feedback = {"version": 1, "state": "ready", "epoch": -1, "observation_id": 0}

        def make_live_command_sender(self): return Sender()
        def retire_observation_requests(self): pass
        def get_observation(self):
            time.sleep(0.002)
            if clock() < 0.2:
                self.observation_sequence += 1
                self.latest_observation_received_at = clock()
                feedback = {}
                control.annotate(feedback)
                self.latest_am1_local_feedback = feedback[FEEDBACK]
            return dict(FOLLOWER)

    class Leader:
        def get_action(self): return dict(LEADER)

    def body_input():
        nonlocal quit_requested
        if end_kind == "q" and clock() >= 1.5:
            quit_requested = True
        return module.make_zero_action()

    robot = Robot()
    def run():
        module.run_am1_live_sender(
            robot, Leader(), initial_arm_target=FOLLOWER, initial_observation_sequence=0,
            initial_follower_observed_at=robot.latest_observation_received_at,
            initial_follower_positions=FOLLOWER, fps=10,
            duration_s=1.7 if end_kind == "duration" else 100,
            live_arm_scope="both", profile_cadence=False,
            body_action_supplier=body_input, recovery_enabled=True,
            monotonic=clock, sleep_fn=lambda seconds: time.sleep(seconds / factor),
            should_stop=lambda: quit_requested or (end_kind == "external_stop" and clock() >= 1.5),
        )

    if end_kind == "duration":
        with pytest.raises(module.SafetyRefusal, match="session duration expired while paused"):
            run()
    else:
        run()

    assert control.state == "paused"
    assert clock() < 3.0


def test_recovery_cleanup_preserves_primary_motor_fault_if_sender_join_also_fails(monkeypatch):
    module = load_teleoperate()
    original_join = module.AM1LiveActionSender.join

    def failing_join(sender):
        original_join(sender)
        raise RuntimeError("secondary sender join failure")

    monkeypatch.setattr(module.AM1LiveActionSender, "join", failing_join)

    class Sender:
        def __enter__(self): return self
        def send_action(self, action): pass
        def __exit__(self, *args): return False

    class Robot(FreshReplyRobot):
        def __init__(self):
            self.observation_sequence = 0
            self.latest_observation_received_at = time.monotonic()
            self.latest_am1_local_feedback = {"version": 1, "state": "ready", "epoch": -1, "observation_id": 0}

        def make_live_command_sender(self): return Sender()
        def get_observation(self): raise RuntimeError("primary motor telemetry fault")

    class Leader:
        def get_action(self): return dict(LEADER)

    robot = Robot()
    with pytest.raises(RuntimeError, match="primary motor telemetry fault") as caught:
        module.run_am1_live_sender(
            robot, Leader(), initial_arm_target=FOLLOWER, initial_observation_sequence=0,
            initial_follower_observed_at=robot.latest_observation_received_at,
            initial_follower_positions=FOLLOWER, fps=10, duration_s=2,
            live_arm_scope="both", profile_cadence=False,
            body_action_supplier=module.make_zero_action, recovery_enabled=True,
        )
    assert "secondary sender join failure" in " ".join(caught.value.__notes__)


def test_initial_admission_age_expiry_refreshes_alignment_before_sender(monkeypatch):
    module = load_teleoperate()
    aligned = []
    sent = []

    class Sender:
        def __enter__(self): return self
        def send_action(self, action): sent.append(dict(action))
        def __exit__(self, *args): return False

    class Robot(FreshReplyRobot):
        def __init__(self):
            self.config = SimpleNamespace(connect_timeout_s=2.0)
            self.observation_sequence = 1
            self.latest_am1_local_feedback = {
                "version": 1, "state": "ready", "epoch": -1, "observation_id": 1,
            }

        def make_live_command_sender(self): return Sender()

    robot = Robot()

    def fresh_gate(*args, **kwargs):
        aligned.append(kwargs)
        robot.observation_sequence += 1
        return dict(FOLLOWER), dict(FOLLOWER), 100.0

    monkeypatch.setattr(module, "run_alignment_gate", fresh_gate)
    with pytest.raises(module.SafetyRefusal):
        module.run_am1_live_sender(
            robot, SimpleNamespace(get_action=lambda: dict(LEADER)),
            initial_arm_target=FOLLOWER, initial_observation_sequence=1,
            initial_follower_observed_at=98.9, initial_follower_positions=FOLLOWER,
            fps=10, duration_s=2, live_arm_scope="both", profile_cadence=False,
            body_action_supplier=module.make_zero_action, recovery_enabled=True,
            monotonic=lambda: 100.0, should_stop=lambda: True,
        )
    assert len(aligned) == 1
    assert aligned[0]["require_current_request"] is True
    assert sent == []


def test_unified_active_banner_waits_for_sender_start(monkeypatch):
    module = load_teleoperate()
    announced = []
    failure = RuntimeError("sender socket could not open")
    monkeypatch.setattr(
        module.AM1LiveActionSender, "start",
        lambda sender: (_ for _ in ()).throw(failure),
    )

    class Robot(FreshReplyRobot):
        observation_sequence = 1
        latest_am1_local_feedback = {
            "version": 1, "state": "ready", "epoch": -1, "observation_id": 1,
        }

    with pytest.raises(RuntimeError) as caught:
        module.run_am1_live_sender(
            Robot(), SimpleNamespace(get_action=lambda: dict(LEADER)),
            initial_arm_target=FOLLOWER, initial_observation_sequence=1,
            initial_follower_observed_at=time.monotonic(),
            initial_follower_positions=FOLLOWER,
            fps=10, duration_s=2, live_arm_scope="both", profile_cadence=False,
            body_action_supplier=module.make_zero_action, recovery_enabled=True,
            announce_active=lambda: announced.append(True),
        )
    assert caught.value is failure
    assert announced == []


def test_qualified_pause_loses_feedback_again_and_does_not_wait_forever_for_enter(capsys):
    module = load_teleoperate()
    host = FakeLocalRobot()
    control = alohamini_host.AM1LocalControl()
    factor = 30.0
    real_started = time.monotonic()
    clock = lambda: (time.monotonic() - real_started) * factor
    release_enter = threading.Event()

    class Sender:
        def __enter__(self): return self
        def send_action(self, action): control.apply(host, dict(action))
        def __exit__(self, *args): return False

    class Robot(FreshReplyRobot):
        def __init__(self):
            self.observation_sequence = 0
            self.latest_observation_received_at = clock()
            self.latest_observation_error = None
            self.latest_raw_observation_keys = frozenset(FOLLOWER)
            self.latest_am1_local_feedback = {"version": 1, "state": "ready", "epoch": -1, "observation_id": 0}

        def make_live_command_sender(self): return Sender()
        def retire_observation_requests(self): pass
        def get_observation(self):
            time.sleep(0.002)
            elapsed = clock()
            if elapsed < 0.2 or 1.5 < elapsed < 4.0:
                self.observation_sequence += 1
                self.latest_observation_received_at = clock()
                feedback = {}
                control.annotate(feedback)
                self.latest_am1_local_feedback = feedback[FEEDBACK]
            return dict(FOLLOWER)

    class Leader:
        def get_action(self):
            result = dict(LEADER)
            if clock() > 1.5:
                result["left_elbow_flex.pos"] += 2
            return result

    def await_enter(_):
        release_enter.wait(5)
        return ""

    robot = Robot()
    try:
        with pytest.raises(module.SafetyRefusal, match="feedback did not qualify within 30s"):
            module.run_am1_live_sender(
                robot, Leader(), initial_arm_target=FOLLOWER, initial_observation_sequence=0,
                initial_follower_observed_at=robot.latest_observation_received_at,
                initial_follower_positions=FOLLOWER, fps=10, duration_s=100,
                live_arm_scope="both", profile_cadence=False,
                body_action_supplier=module.make_zero_action, recovery_enabled=True,
                monotonic=clock, sleep_fn=lambda seconds: time.sleep(seconds / factor),
                input_fn=await_enter,
            )
    finally:
        release_enter.set()
    assert "RESUME-NEEDS-ENTER" in capsys.readouterr().out
