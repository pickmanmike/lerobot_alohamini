"""No-hardware protocol checks for the single native AM1 console input bridge."""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
import time

import pytest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("am1_console_bridge", ROOT / "examples" / "alohamini" / "am1_console_bridge.py")
assert SPEC and SPEC.loader
bridge = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bridge
SPEC.loader.exec_module(bridge)


def test_input_loss_zeros_and_latches_pause():
    client = bridge.AM1ConsoleBridgeClient("pipe", Path("auth"), "20261001T000000-1234abcd", clock=lambda: 0)
    client._gate_events[("live_start", None)] = threading.Event()
    client.accept_message({"session_id": client.session_id, "epoch": 1, "seq": 1,
                           "kind": "gate_ack", "payload": {"stage": "live_start", "host_epoch": None}}, received_at=0.99)
    client.note_live_admitted()
    client.accept_message({"session_id": client.session_id, "epoch": 1, "seq": 2,
                           "kind": "lease", "payload": {"valid": True, "keys": []}}, received_at=1.0)
    client.accept_message({"session_id": client.session_id, "epoch": 1, "seq": 3,
                           "kind": "lease", "payload": {"valid": True, "keys": ["w"]}}, received_at=1.0)
    assert client.body_keys(now=1.10) == {"w"}
    assert not client.pause_requested(now=1.10)
    assert client.body_keys(now=1.251) == set()
    assert client.pause_requested(now=1.251)
    client.accept_message({"session_id": client.session_id, "epoch": 1, "seq": 4,
                           "kind": "lease", "payload": {"valid": True, "keys": ["w"]}}, received_at=1.30)
    assert client.body_keys(now=1.31) == set()  # Reconnect is not automatic rearm.
    assert client.pause_requested(now=1.31)


def test_body_requires_host_ack_and_fresh_release_after_resume():
    client = bridge.AM1ConsoleBridgeClient("pipe", Path("auth"), "20261001T000000-1234abcd", clock=lambda: 0)
    sid = client.session_id
    client._gate_events[("live_start", None)] = threading.Event()
    client.accept_message({"session_id": sid, "epoch": 1, "seq": 1,
                           "kind": "gate_ack", "payload": {"stage": "live_start", "host_epoch": None}}, received_at=1)
    client.accept_message({"session_id": sid, "epoch": 1, "seq": 2,
                           "kind": "lease", "payload": {"valid": True, "keys": ["w"]}}, received_at=1)
    assert client.body_keys(now=1.01) == set()
    client.note_live_admitted()
    assert client.body_keys(now=1.02) == set()
    client.accept_message({"session_id": sid, "epoch": 1, "seq": 3,
                           "kind": "lease", "payload": {"valid": True, "keys": []}}, received_at=1.03)
    client.accept_message({"session_id": sid, "epoch": 1, "seq": 4,
                           "kind": "lease", "payload": {"valid": True, "keys": ["u"]}}, received_at=1.04)
    assert client.body_keys(now=1.05) == {"u"}


def test_stale_epoch_and_sequence_never_replay_body():
    state = bridge.AM1ConsoleInputState("20261001T000000-1234abcd", "private-token")
    assert state.browser_keys(token="private-token", epoch=1, seq=1, keys=["w"], active=True, now=1.0)
    assert state.lease(now=1.1)["keys"] == ["w"]
    assert not state.browser_keys(token="wrong", epoch=1, seq=2, keys=["u"], active=True, now=1.1)
    assert not state.browser_keys(token="private-token", epoch=1, seq=1, keys=["u"], active=True, now=1.1)
    state.claim("new-token", now=1.2)
    assert not state.browser_keys(token="private-token", epoch=1, seq=3, keys=["w"], active=True, now=1.3)
    assert state.lease(now=1.3)["keys"] == []
    assert state.epoch == 2


def test_first_pause_retains_reason_and_pre_release_input_without_secrets():
    state = bridge.AM1ConsoleInputState("20261001T000000-1234abcd", "private-token")
    state.browser_keys(token="private-token", epoch=1, seq=7, keys=["w"], active=True, now=10.0)
    state.request_pause("window-blur", now=10.05)
    first = dict(state.first_pause)
    assert first["reason"] == "window-blur"
    assert first["input_sequence"] == 7
    assert first["input_age_ms"] == pytest.approx(50)
    assert first["browser_active"] is True
    assert first["local_monotonic_s"] == 10.05
    assert "private-token" not in json.dumps(first)
    state.lease(now=10.4)
    state.request_pause("pipe disconnected", now=10.5)
    assert state.first_pause == first
    assert state.lease(now=10.5)["pause_evidence"] == first
    assert state.lease(now=10.5)["keys"] == []


def test_recovery_keeps_first_pause_and_records_next_independent_latch():
    state = bridge.AM1ConsoleInputState("20261001T000000-1234abcd", "private-token")
    state.browser_keys(token="private-token", epoch=1, seq=1, keys=[], active=True, now=1.0)
    state.request_pause("window-blur", now=1.01)
    first = dict(state.first_pause)
    state.request_gate("resume", host_epoch=2)
    state.browser_keys(token="private-token", epoch=1, seq=2, keys=[], active=True, now=1.02)
    assert state.approve("resume", host_epoch=2, token="private-token", now=1.03)
    assert state.gate_ack("resume", host_epoch=2, now=1.04)
    state.lease(now=1.4)
    assert state.first_pause == first
    assert state.lease(now=1.4)["pause_evidence"]["reason"] == "expired browser input"
    assert state.lease(now=1.4)["pause_evidence"]["pause_sequence"] == 2


def test_released_packet_preserves_allowed_browser_cause_and_native_log(capsys):
    state = bridge.AM1ConsoleInputState("20261001T000000-1234abcd", "private-token")
    state.browser_keys(token="private-token", epoch=1, seq=4, keys=[], active=True, now=1.0)
    assert state.browser_keys(token="private-token", epoch=1, seq=5, keys=[], active=False,
                              release_reason="window-blur", now=1.05)
    native = bridge.AM1ConsoleBridgeClient("unused", Path("unused"), state.session_id)
    native.accept_message({"session_id": state.session_id, "epoch": 1, "seq": 1,
                           "kind": "lease", "payload": state.lease(now=1.06)}, received_at=1.06)
    record = json.loads(capsys.readouterr().out)
    assert record["event"] == "am1_console_input_pause"
    assert record["reason"] == "window-blur"
    assert record["input_sequence"] == 5
    assert record["native_body_enabled"] is False
    assert "private-token" not in json.dumps(record)
    native.accept_message({"session_id": state.session_id, "epoch": 1, "seq": 2,
                           "kind": "lease", "payload": state.lease(now=1.4)}, received_at=1.4)
    assert capsys.readouterr().out == ""


def test_pause_evidence_never_echoes_extra_peer_fields(capsys):
    native = bridge.AM1ConsoleBridgeClient("unused", Path("unused"), "session")
    native.accept_message({"session_id": "session", "epoch": 1, "seq": 1, "kind": "lease",
                           "payload": {"valid": False, "keys": [], "pause_evidence": {
                               "pause_sequence": 1, "reason": "window-blur", "password": "not-for-evidence"}}},
                          received_at=1.0)
    assert "not-for-evidence" not in capsys.readouterr().out


def test_invalid_browser_reason_is_bounded_not_a_transport_error():
    state = bridge.AM1ConsoleInputState("session", "token")
    state.browser_keys(token="token", epoch=1, seq=1, keys=[], active=True, now=1.0)
    assert state.browser_keys(token="token", epoch=1, seq=2, keys=[], active=False,
                              release_reason=["untrusted"], now=1.1)
    assert state.first_pause["reason"] == "released browser input"


def test_typed_resume_requires_current_pending_request_and_explicit_approval():
    state = bridge.AM1ConsoleInputState("20261001T000000-1234abcd", "private-token")
    state.browser_keys(token="private-token", epoch=1, seq=1, keys=[], active=True, now=1.0)
    state.request_pause("operator")
    state.request_gate("resume", host_epoch=3)
    assert state.gate_ack("resume", host_epoch=3, now=1.1) is False
    assert not state.approve("resume", host_epoch=2, token="private-token", now=1.1)
    assert state.approve("resume", host_epoch=3, token="private-token", now=1.1)
    assert state.gate_ack("resume", host_epoch=3, now=1.1) is True
    assert state.gate_ack("resume", host_epoch=3, now=1.1) is False  # One-use acknowledgement.


def test_realign_explicit_approval_releases_startup_input_latch():
    state = bridge.AM1ConsoleInputState("20261001T000000-1234abcd", "private-token")
    state.browser_keys(token="private-token", epoch=1, seq=1, keys=[], active=True, now=1)
    state.request_pause("focus lost")
    state.request_gate("realign", host_epoch=None)
    assert state.browser_keys(token="private-token", epoch=1, seq=2, keys=[], active=True, now=1.1)
    assert state.approve("realign", host_epoch=None, token="private-token", now=1.1)
    assert state.gate_ack("realign", host_epoch=None, now=1.1)
    state.request_gate("live_start", host_epoch=None)
    assert state.gate_ack("live_start", host_epoch=None, now=1.11)


def test_prepared_gate_needs_real_browser_lease():
    state = bridge.AM1ConsoleInputState("20261001T000000-1234abcd", "private-token")
    state.request_gate("sync_start", host_epoch=None)
    assert not state.gate_ack("sync_start", host_epoch=None, now=1.0)
    assert state.browser_keys(token="private-token", epoch=1, seq=1, keys=[], active=True, now=1.0)
    assert state.gate_ack("sync_start", host_epoch=None, now=1.01)


@pytest.mark.parametrize("stage", ["sync_start", "live_start"])
def test_released_startup_gate_requires_explicit_fresh_empty_approval(stage):
    state = bridge.AM1ConsoleInputState("20261001T000000-1234abcd", "private-token")
    state.browser_keys(token="private-token", epoch=1, seq=1, keys=["w"], active=True, now=1.0)
    state.browser_keys(token="private-token", epoch=1, seq=2, keys=[], active=False, now=1.1)
    state.request_gate(stage, host_epoch=None)
    state.browser_keys(token="private-token", epoch=1, seq=3, keys=[], active=True, now=1.2)
    assert not state.gate_ack(stage, host_epoch=None, now=1.21)  # No automatic rearm.
    assert not state.approve(stage, host_epoch=None, token="wrong", now=1.21)
    assert not state.approve(stage, host_epoch=3, token="private-token", now=1.21)
    assert not state.approve(stage, host_epoch=None, token="private-token", now=1.5)
    state.browser_keys(token="private-token", epoch=1, seq=4, keys=["u"], active=True, now=1.6)
    assert not state.approve(stage, host_epoch=None, token="private-token", now=1.61)
    state.browser_keys(token="private-token", epoch=1, seq=5, keys=[], active=True, now=1.7)
    assert state.approve(stage, host_epoch=None, token="private-token", now=1.71)
    state.browser_keys(token="private-token", epoch=1, seq=6, keys=["u"], active=True, now=1.72)
    assert not state.gate_ack(stage, host_epoch=None, now=1.73)
    state.browser_keys(token="private-token", epoch=1, seq=7, keys=[], active=True, now=1.74)
    assert state.gate_ack(stage, host_epoch=None, now=1.75)
    assert not state.gate_ack(stage, host_epoch=None, now=1.76)
    assert state.lease(now=1.76)["keys"] == []
    assert state.lease(now=1.76)["valid"]


@pytest.mark.parametrize("loss", ["release", "expiry", "pause", "gap_without_poll"])
def test_startup_approval_does_not_survive_subsequent_input_loss(loss):
    state = bridge.AM1ConsoleInputState("20261001T000000-1234abcd", "private-token")
    state.request_pause("startup input released")
    state.request_gate("sync_start", host_epoch=None)
    state.browser_keys(token="private-token", epoch=1, seq=1, keys=[], active=True, now=1.0)
    assert state.approve("sync_start", host_epoch=None, token="private-token", now=1.01)
    if loss == "release":
        state.browser_keys(token="private-token", epoch=1, seq=2, keys=[], active=False, now=1.1)
    elif loss == "expiry":
        assert not state.lease(now=1.3)["valid"]
    elif loss == "pause":
        state.request_pause("operator")
    state.browser_keys(token="private-token", epoch=1, seq=3, keys=[], active=True, now=1.4)
    assert not state.gate_ack("sync_start", host_epoch=None, now=1.41)
    assert state.approve("sync_start", host_epoch=None, token="private-token", now=1.42)
    assert state.gate_ack("sync_start", host_epoch=None, now=1.43)


@pytest.mark.skipif(sys.platform != "win32", reason="AF_PIPE is Windows-only")
def test_real_authenticated_pipe_carries_prepared_gate_and_bounded_body(tmp_path):
    session_id = "20261001T000000-1234abcd"
    server = bridge.AM1ConsoleBridgeServer(session_id, tmp_path, "private-token")
    client = bridge.AM1ConsoleBridgeClient(server.pipe_name, server.auth_file, session_id)
    try:
        client.connect()
        assert server.browser_keys(token="private-token", epoch=1, seq=1, keys=["w"], active=True)
        assert client.wait_gate("sync_start", timeout_s=1)
        assert server.browser_keys(token="private-token", epoch=1, seq=2, keys=["w"], active=True)
        assert client.wait_gate("live_start", timeout_s=1)
        client.note_live_admitted()
        assert server.browser_keys(token="private-token", epoch=1, seq=3, keys=[], active=True)
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and client._needs_release:
            time.sleep(0.02)
        assert server.browser_keys(token="private-token", epoch=1, seq=4, keys=["w"], active=True)
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and client.get_action() != {"w"}:
            time.sleep(0.02)
        assert client.get_action() == {"w"}
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and not client.pause_requested():
            time.sleep(0.02)
        assert client.get_action() == set()
        assert client.pause_requested()
    finally:
        client.disconnect()
        server.close()
    assert not server.auth_file.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="AF_PIPE is Windows-only")
def test_unused_pipe_closes_without_orphan_accept_thread(tmp_path):
    server = bridge.AM1ConsoleBridgeServer("20261001T000000-1234abcd", tmp_path, "private-token")
    server.close()
    assert not server._thread.is_alive()
    assert not server.auth_file.exists()


def test_native_lease_expiry_retains_cause_without_printing_on_action_consumer(capsys):
    client = bridge.AM1ConsoleBridgeClient("unused", Path("unused"), "session")
    client._gate_events[("live_start", None)] = threading.Event()
    client.accept_message({"session_id": "session", "epoch": 1, "seq": 1, "kind": "gate_ack",
                           "payload": {"stage": "live_start", "host_epoch": None}}, received_at=.99)
    client.accept_message({"session_id": "session", "epoch": 1, "seq": 2, "kind": "lease",
                           "payload": {"valid": True, "keys": []}}, received_at=1)
    client.note_live_admitted()
    assert client.pause_requested(now=1.251)
    event = client._telemetry_pending["am1_console_input_pause"]
    assert event["reason"] == "native input expired"
    assert event["input_age_ms"] == pytest.approx(251)
    assert event["input_sequence"] == 2
    assert capsys.readouterr().out == ""
    assert client.pause_requested(now=2)
    assert client._telemetry_pending["am1_console_input_pause"] == event


def test_delayed_release_keeps_browser_first_cause_separate_from_server_expiry():
    state = bridge.AM1ConsoleInputState("session", "private-token")
    state.browser_keys(token="private-token", epoch=1, seq=1, keys=[], active=True, now=1)
    state.lease(now=1.3)
    first = {"reason": "window-blur", "local_wall_time_ms": 1000, "input_sequence": 1,
             "input_epoch": 1, "route": "control"}
    state.browser_keys(token="private-token", epoch=1, seq=3, keys=[], active=False, now=1.4,
                       release_reason="document-hidden", first_release=first)
    assert state.first_pause["reason"] == "expired browser input"
    assert state.browser_first_pause == first
    assert state.lease(now=1.4)["browser_first_pause"] == first


def test_oversized_display_metadata_cannot_break_bounded_pipe():
    state = bridge.AM1ConsoleInputState("session", "private-token")
    state.browser_keys(token="private-token", epoch=1, seq=1, keys=[], active=True, now=1)
    huge = int("9" * 3700)
    first = {"reason": "window-blur", "local_wall_time_ms": huge, "input_sequence": 1,
             "input_epoch": 1, "route": "control"}
    assert state.browser_keys(token="private-token", epoch=1, seq=2, keys=[], active=False, now=1.1,
                              first_release=first)
    assert state.browser_first_pause is None
    assert len(bridge._encode(state.lease(now=1.1))) < bridge.MAX_PIPE_BYTES
    assert not state.browser_keys(token="private-token", epoch=1, seq=huge, keys=[], active=True, now=1.2)


@pytest.mark.skipif(sys.platform != "win32", reason="Actual AF_PIPE is Windows-only")
def test_full_native_telemetry_does_not_hide_current_resume_gate(tmp_path, capsys):
    """Use the actual native event shapes/pipe, not a tiny fake host payload."""
    from types import SimpleNamespace
    server = bridge.AM1ConsoleBridgeServer("20261002T000000-1234abcd", tmp_path, "private-token")
    client = bridge.AM1ConsoleBridgeClient(server.pipe_name, server.auth_file, server.session_id)
    stop = threading.Event()
    received = []
    server.set_telemetry_sink(received.append)
    sequence = [0]
    def browser():
        while not stop.wait(.05):
            sequence[0] += 1
            server.browser_keys(token="private-token", epoch=1, seq=sequence[0], keys=[], active=True)
    producer = threading.Thread(target=browser, daemon=True)
    positions = {key: 12.3456789012345 for key in bridge.CONSOLE_ARM_KEYS}
    sample = SimpleNamespace(arm_target=positions, follower_positions=positions,
                             observed_at=time.monotonic(), observation_sequence=17,
                             observation={key: 0.0 for key in bridge.CONSOLE_BODY_OBSERVATION_KEYS})
    try:
        producer.start()
        client.connect()
        assert client.wait_gate("sync_start", timeout_s=2)
        assert client.wait_gate("live_start", timeout_s=2)
        client.note_live_admitted()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            sample.observed_at = time.monotonic()
            feedback = {"state": "active", "epoch": 0, "observation_id": 17}
            for event in (
                bridge.make_console_live_sample_event(sample, positions, raw_keys=bridge.CONSOLE_BODY_OBSERVATION_KEYS,
                    host_feedback=feedback, wall_ns=time.time_ns(), monotonic_now=time.monotonic()),
                bridge.make_console_host_feedback_event(sample, feedback, wall_ns=time.time_ns(),
                                                       monotonic_now=time.monotonic()),
                bridge.make_console_action_sent_event(positions, sequence=17, interval_ms=100, wall_ns=time.time_ns()),
            ):
                client.publish_telemetry(event)
            time.sleep(.1)
        server.request_pause("operator")
        completed = []
        waiter = threading.Thread(target=lambda: completed.append(client.wait_gate("resume", host_epoch=1,
                                  cancel=stop.is_set, timeout_s=2)), daemon=True)
        waiter.start()
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and server.snapshot()["pending_gate"] != ("resume", 1):
            time.sleep(.01)
        assert server.snapshot()["pending_gate"] == ("resume", 1)
        request = server.snapshot()["gate_request_evidence"]
        assert request["stage"] == "resume" and request["host_epoch"] == 1
        assert request["accepted"] is True and request["input_epoch"] == 1
        assert server.snapshot()["native_connected"] is True
        assert not completed, "a displayed request is not automatic approval"
        assert server.approve("resume", host_epoch=1, token="private-token")
        waiter.join(2)
        assert completed == [True]
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and (server.snapshot()["gate_ack_evidence"] or {}).get("stage") != "resume":
            time.sleep(.01)
        ack = server.snapshot()["gate_ack_evidence"]
        assert ack["stage"] == "resume" and ack["host_epoch"] == 1
        records = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
        assert any(event.get("event") == "am1_console_gate_request" and event.get("stage") == "resume"
                   and event.get("host_epoch") == 1 for event in records)
        assert any(event.get("event") == "am1_console_gate_result" and event.get("stage") == "resume"
                   and event.get("result") == "acknowledged" for event in records)
        assert {event["event"] for event in received} >= {"live_sample", "host_feedback", "action_sent"}
    finally:
        stop.set()
        producer.join(1)
        client.disconnect()
        server.close()


def test_gate_evidence_failure_does_not_replace_cancellation(monkeypatch, tmp_path):
    client = bridge.AM1ConsoleBridgeClient("unused", tmp_path / "unused", "session")
    def broken_output(*args, **kwargs):
        raise BrokenPipeError("display output closed")
    monkeypatch.setattr(bridge, "print", broken_output, raising=False)
    assert not client.wait_gate("resume", host_epoch=1, cancel=lambda: True, timeout_s=1)
    assert not client._gate_events


@pytest.mark.skipif(sys.platform != "win32", reason="Actual AF_PIPE is Windows-only")
@pytest.mark.parametrize("epoch,stage,host_epoch,reason", [
    (0, "resume", 1, "input epoch mismatch"),
    (1, "unknown-stage", 1, "invalid gate"),
    (1, "resume", 1.5, "invalid gate"),
])
def test_rejected_native_gate_is_not_reported_as_accepted(tmp_path, epoch, stage, host_epoch, reason):
    server = bridge.AM1ConsoleBridgeServer("20261002T000000-1234abcd", tmp_path, "private-token")
    client = bridge.AM1ConsoleBridgeClient(server.pipe_name, server.auth_file, server.session_id)
    try:
        client.connect()
        # Inject on the existing worker's own outbound queue, not a second pipe writer.
        client._outbound.put_nowait({"session_id": server.session_id, "epoch": epoch, "seq": 0,
            "kind": "gate_request", "payload": {"stage": stage, "host_epoch": host_epoch}})
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and server.snapshot()["gate_request_evidence"] is None:
            time.sleep(.01)
        state = server.snapshot()
        assert state["gate_request_evidence"]["accepted"] is False
        assert state["gate_request_evidence"]["rejection"] == reason
        assert state["pending_gate"] is None
        assert state["gate_ack_evidence"] is None
        assert "private-token" not in json.dumps(state)
    finally:
        client.disconnect()
        server.close()
