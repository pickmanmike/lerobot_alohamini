"""Offline races between a qualified console approval and a new terminal pause."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_alohamini_local_recovery import (
    CONTROL,
    FEEDBACK,
    FOLLOWER,
    LEADER,
    FakeLocalRobot,
    FreshReplyRobot,
    alohamini_host,
    load_teleoperate,
)
from test_am1_console_bridge import bridge, lease_payload
from test_am1_virtual_bench import health, paused, policy

from tools import am1_console as console
from tools.am1_console import ConsoleSessionAdapter


def paused_input():
    state = bridge.AM1ConsoleInputState("owned", "token")
    assert state.browser_keys(token="token", epoch=1, seq=1, keys=[], active=True, now=10)
    state.request_pause("bench required coverage", now=10.01)
    assert state.browser_keys(token="token", epoch=1, seq=2, keys=[], active=True, now=10.02)
    return state


@pytest.mark.parametrize("stage", ["resume", "realign"])
@pytest.mark.parametrize("cause", ["operator", "window-blur", "document-hidden", "pagehide"])
def test_new_terminal_pause_revokes_queued_manual_approval_after_empty_renewal(stage, cause):
    state = paused_input()
    state.request_gate(stage, host_epoch=7)
    assert state.approve(stage, host_epoch=7, token="token", now=10.03)
    first_pause = dict(state.pause_evidence)
    state.request_pause(cause, now=10.04)
    assert state.browser_keys(token="token", epoch=1, seq=3, keys=[], active=True, now=10.05)
    assert not state.gate_ack(stage, host_epoch=7, now=10.06)
    assert state.forced_pause
    assert state.pause_evidence == first_pause, "revocation must preserve the original first cause"


def test_active_ack_after_episode_deadline_cannot_erase_recovery_budget(tmp_path):
    bench, path = policy(tmp_path)
    state = paused()
    bench.evaluate(state, now=0, wall_ms=10000)
    health(path, 2, received_wall_time_ms=20010)
    state.update(phase="live", pause_required=False, pending_gate=None)
    state["telemetry"]["observation"]["host_state"] = "active"
    current = bench.evaluate(state, now=10.01, wall_ms=20010)
    assert current["required_coverage_qualified"]
    assert current["action"] == "stop"
    assert current["disarmed"]
    assert current["recovery_count"] == 0


def test_delayed_observation_evaluation_cannot_freshen_prepared_gate_snapshot(monkeypatch, tmp_path):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(
        console,
        "time",
        SimpleNamespace(
            monotonic=lambda: clock.now,
            time_ns=lambda: int(clock.now * 1e9),
        ),
    )

    class Policy:
        def evaluate(self, state, *, now, wall_ms):
            assert now == 100.0
            clock.now += 0.3  # Finite file-read or scheduling delay after the age check.
            return {"required_coverage_qualified": True}

        def coverage_expires_at(self):
            return 100.25  # Original observer/camera clocks expire during publication.

    adapter = ConsoleSessionAdapter(SimpleNamespace(), tmp_path, SimpleNamespace())
    adapter._bench_policy = Policy()
    adapter._bench_snapshot({})
    assert not adapter._bench_prepared_gate_qualified()


@pytest.mark.parametrize("loss", ["new-invalid-lease", "original-pipe-expiry", "original-browser-expiry"])
def test_native_waiter_rechecks_pause_and_original_clocks_after_ack(monkeypatch, loss):
    clock = SimpleNamespace(now=10.0)
    client = bridge.AM1ConsoleBridgeClient(
        "unused-pipe", Path("unused-auth"), "owned", clock=lambda: clock.now
    )
    client._epoch, client._connected = 1, True
    real_event = threading.Event

    class AckThenLoss(real_event):
        def wait(self, timeout=None):
            assert client.accept_message(
                {
                    "session_id": "owned",
                    "epoch": 1,
                    "seq": 1,
                    "kind": "lease",
                    "payload": lease_payload([], 10),
                },
                received_at=10,
            )
            assert client.accept_message(
                {
                    "session_id": "owned",
                    "epoch": 1,
                    "seq": 2,
                    "kind": "gate_ack",
                    "payload": {"stage": "resume", "host_epoch": 7},
                },
                received_at=10,
            )
            assert self.is_set()
            clock.now = 10.25
            if loss == "new-invalid-lease":
                clock.now = 10.01
                assert client.accept_message(
                    {
                        "session_id": "owned",
                        "epoch": 1,
                        "seq": 3,
                        "kind": "lease",
                        "payload": {
                            "valid": False,
                            "keys": [],
                            "browser_received_at_s": 10.01,
                            "body_release_required": True,
                        },
                    },
                    received_at=10.01,
                )
            elif loss == "original-browser-expiry":
                assert client.accept_message(
                    {
                        "session_id": "owned",
                        "epoch": 1,
                        "seq": 3,
                        "kind": "lease",
                        "payload": lease_payload([], 10),
                    },
                    received_at=10.2,
                )
            return True

    monkeypatch.setattr(bridge.threading, "Event", AckThenLoss)
    assert not client.wait_gate("resume", host_epoch=7, timeout_s=1)


def test_operator_pause_serializes_with_automatic_qualification_and_gate_install(tmp_path):
    entered, release, pause_called, pause_done = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    state, results, events = paused_input(), {}, []
    state.request_gate("resume", host_epoch=7)

    class Policy:
        disarmed = None

        def approval(self, evidence, current, **kwargs):
            return self.disarmed is None

        def disarm(self, reason):
            self.disarmed = reason

    class Bridge:
        def snapshot(self):
            return {"input_epoch": 1}

        def approve(self, stage, *, host_epoch, token):
            entered.set()
            assert release.wait(2)
            events.append("approve")
            return state.approve(stage, host_epoch=host_epoch, token=token, now=10.03)

        def request_pause(self, cause):
            events.append("pause")
            state.request_pause(cause, now=10.04)

    adapter = ConsoleSessionAdapter(SimpleNamespace(), tmp_path, SimpleNamespace())
    adapter._session_id, adapter._control_token = "owned", "token"
    adapter._worker, adapter._bridge = SimpleNamespace(is_alive=lambda: True), Bridge()
    adapter._bench_policy = Policy()
    adapter.state = lambda: {}

    def approve():
        results["approval"] = adapter.operation(
            {
                "kind": "Resume",
                "session_id": "owned",
                "control_token": "token",
                "host_epoch": 7,
                "bench_recovery": {"input_epoch": 1, "pause_sequence": 1, "host_epoch": 7},
            }
        )

    def pause():
        try:
            pause_called.set()
            results["pause"] = adapter.operation(
                {"kind": "Pause", "session_id": "owned", "control_token": "token"}
            )
        finally:
            pause_done.set()

    approval_thread, pause_thread = threading.Thread(target=approve), threading.Thread(target=pause)
    approval_thread.start()
    try:
        assert entered.wait(2)
        pause_thread.start()
        assert pause_called.wait(2)
        assert not pause_done.wait(0.1), "Pause must not interleave before the qualified gate is installed"
        assert adapter._bench_policy.disarmed is None
    finally:
        release.set()
        approval_thread.join(2)
        if pause_thread.ident is not None:
            pause_thread.join(2)
    assert not approval_thread.is_alive() and not pause_thread.is_alive()
    assert results["approval"]["accepted"] and results["pause"]["accepted"]
    assert events == ["approve", "pause"]
    assert adapter._bench_policy.disarmed == "operator pause"
    assert state.browser_keys(token="token", epoch=1, seq=3, keys=[], active=True, now=10.05)
    assert not state.gate_ack("resume", host_epoch=7, now=10.06)


@pytest.mark.parametrize("pause_during", ["postapproval-feedback", "pending-active-ack"])
def test_new_console_pause_after_gate_ack_suppresses_actual_outgoing_resume(capsys, pause_during):
    module = load_teleoperate()
    host, control = FakeLocalRobot(), alohamini_host.AM1LocalControl()
    first_approval, renewed_pause, first_resume_sent = threading.Event(), threading.Event(), threading.Event()
    console_paused, sent, sent_after_pause, gates = [False], [], [], []

    class Sender:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def send_action(self, action):
            sent.append(dict(action))
            if renewed_pause.is_set():
                sent_after_pause.append(dict(action))
            if pause_during == "pending-active-ack" and action[CONTROL]["epoch"] == 2:
                # A finite delayed delivery leaves the host in its measured hold.
                # The consumer then receives a new console Pause while resuming.
                first_resume_sent.set()
                return
            control.apply(host, dict(action))

    class Robot(FreshReplyRobot):
        def __init__(self):
            self.started = time.monotonic()
            self.observation_sequence = 0
            self.latest_observation_received_at = self.started
            self.latest_observation_error = None
            self.latest_raw_observation_keys = frozenset(FOLLOWER)
            self.latest_am1_local_feedback = {
                "version": 1,
                "state": "ready",
                "epoch": -1,
                "observation_id": 0,
            }

        def make_live_command_sender(self):
            return Sender()

        def retire_observation_requests(self):
            pass

        def get_observation(self):
            time.sleep(0.02)
            self.observation_sequence += 1
            self.latest_observation_received_at = time.monotonic()
            feedback = {}
            control.annotate(feedback)
            self.latest_am1_local_feedback = feedback[FEEDBACK]
            if (
                first_approval.is_set()
                and control.state == "paused"
                and (pause_during == "postapproval-feedback" or first_resume_sent.is_set())
            ):
                console_paused[0] = True
                renewed_pause.set()
            return dict(FOLLOWER)

    robot = Robot()

    def pause_requested():
        if not first_approval.is_set() and time.monotonic() - robot.started >= 0.25:
            console_paused[0] = True
        return console_paused[0]

    def approve(epoch):
        gates.append(epoch)
        if len(gates) == 1:
            console_paused[0] = False
            first_approval.set()
            return True
        return False

    with pytest.raises(module.SafetyRefusal):
        module.run_am1_live_sender(
            robot,
            SimpleNamespace(get_action=lambda: dict(LEADER)),
            initial_arm_target=FOLLOWER,
            initial_observation_sequence=0,
            initial_follower_observed_at=robot.started,
            initial_follower_positions=FOLLOWER,
            fps=10,
            duration_s=2,
            live_arm_scope="both",
            profile_cadence=False,
            body_action_supplier=module.make_zero_action,
            recovery_enabled=True,
            control_pause_requested=pause_requested,
            manual_gate=approve,
        )
    assert first_approval.is_set() and renewed_pause.is_set()
    assert len(gates) >= 2, "the first acknowledged approval became obsolete and needs a new gate"
    assert ("hold_present_arms",) in host.events
    assert all(action[CONTROL]["mode"] == "pause" for action in sent_after_pause)
    expected_epoch = 3 if pause_during == "pending-active-ack" else 1
    assert all(action[CONTROL]["epoch"] <= expected_epoch for action in sent)
    if pause_during == "postapproval-feedback":
        assert all(action[CONTROL]["epoch"] <= 1 for action in sent)
    else:
        assert sum(action[CONTROL]["epoch"] == 2 for action in sent) == 1
    assert control.state == "paused" and control.epoch == expected_epoch
    assert "am1_local_recovered" not in capsys.readouterr().out
