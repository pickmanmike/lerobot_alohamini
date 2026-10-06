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


def lease_payload(keys, browser_at):
    return {"valid": True, "keys": keys, "browser_received_at_s": browser_at, "body_release_required": False}


def prepare_native_gate(client, at):
    client._gate_events[("live_start", None, 1)] = threading.Event()
    assert client.accept_message({"session_id": client.session_id, "epoch": 1, "seq": 0, "kind": "lease",
                                  "payload": lease_payload([], at)}, received_at=at)


@pytest.mark.parametrize("stage", ["sync_start", "live_start", "resume", "realign"])
@pytest.mark.parametrize("age, pipe_delay, expected", [(0.1, 0, True), (0.6, 0, False), (0.1, .251, False)])
def test_native_gate_ack_requires_original_fresh_receipt(stage, age, pipe_delay, expected):
    client = bridge.AM1ConsoleBridgeClient("pipe", Path("auth"), "session")
    event = threading.Event()
    client._gate_events[(stage, 2, 1)] = event
    client.accept_message({"session_id":"session", "epoch":1, "seq":1, "kind":"lease",
                           "payload":lease_payload([], 10)}, received_at=10 + age)
    accepted = client.accept_message({"session_id":"session", "epoch":1, "seq":2, "kind":"gate_ack",
                           "payload":{"stage":stage, "host_epoch":2}}, received_at=10 + age + pipe_delay)
    assert accepted is expected, "fresh pipe receipt cannot revive an expired gate packet"
    assert event.is_set() is expected


@pytest.mark.parametrize("stage", ["sync_start", "live_start"])
def test_automatic_startup_gate_preserves_fresh_held_input_with_body_disabled(stage):
    state = bridge.AM1ConsoleInputState("session", "token")
    state.request_gate(stage, host_epoch=None)
    state.browser_keys(token="token", epoch=1, seq=1, keys=["w"], active=True, now=1)
    assert state.gate_ack(stage, host_epoch=None, now=1.01)
    client = bridge.AM1ConsoleBridgeClient("pipe", Path("auth"), "session")
    client._gate_events[(stage, None, 1)] = threading.Event()
    client.accept_message({"session_id":"session", "epoch":1, "seq":1, "kind":"lease",
                           "payload":state.lease(now=1.02)}, received_at=1.02)
    assert client.accept_message({"session_id":"session", "epoch":1, "seq":2, "kind":"gate_ack",
                                "payload":{"stage":stage, "host_epoch":None}}, received_at=1.03)
    assert client.body_keys(now=1.04) == set()
    client.note_live_admitted()
    assert client.body_keys(now=1.05) == set(), "host admission still requires a subsequent release"


def test_short_browser_gap_expires_body_not_session_presence():
    state = bridge.AM1ConsoleInputState("session", "token")
    assert state.browser_keys(token="token", epoch=1, seq=1, keys=["w"], active=True, now=10)
    assert state.lease(now=10.249)["keys"] == ["w"]
    stopped = state.lease(now=10.25)
    assert stopped["valid"] is True, "250 ms body expiry must not be a full presence pause"
    assert stopped["keys"] == []
    assert stopped["body_release_required"] is True
    assert stopped["pause_required"] is False
    assert stopped["browser_received_at_s"] == 10, "forwarding never renews actual browser receipt"
    assert state.lease(now=11.499)["valid"] is True
    assert state.lease(now=11.5)["valid"] is False
    assert state.first_pause["reason"] == "expired browser input"


def test_gap_without_service_poll_requires_empty_release_before_new_body_press():
    state = bridge.AM1ConsoleInputState("session", "token")
    state.browser_keys(token="token", epoch=1, seq=1, keys=["u"], active=True, now=10)
    # This models a delayed browser callback, not a measured hardware event.
    state.browser_keys(token="token", epoch=1, seq=2, keys=["u"], active=True, now=10.94)
    assert state.lease(now=10.941)["pause_required"] is False
    assert state.lease(now=10.941)["keys"] == [], "an old held key cannot resume after a short gap"
    assert state.lease(now=10.941)["body_release_required"] is True
    state.browser_keys(token="token", epoch=1, seq=3, keys=[], active=True, now=10.95)
    state.browser_keys(token="token", epoch=1, seq=4, keys=["u"], active=True, now=10.96)
    assert state.lease(now=10.97)["keys"] == ["u"]
    assert state.lease(now=10.97)["pause_required"] is False


def test_forwarded_native_lease_cannot_make_old_browser_command_fresh():
    client = bridge.AM1ConsoleBridgeClient("pipe", Path("auth"), "session")
    prepare_native_gate(client, 10)
    client.accept_message({"session_id":"session", "epoch":1, "seq":1, "kind":"gate_ack",
                           "payload":{"stage":"live_start", "host_epoch":None}}, received_at=10)
    client.note_live_admitted()
    def forward(pipe_seq, keys, browser_at, received_at):
        return client.accept_message({"session_id":"session", "epoch":1, "seq":pipe_seq, "kind":"lease",
            "payload":{"valid":True, "keys":keys, "browser_received_at_s":browser_at,
                       "body_release_required":False}}, received_at=received_at)
    assert forward(2, [], 10, 10)
    assert forward(3, ["w"], 10.01, 10.01)
    assert client.body_keys(now=10.1) == {"w"}
    assert forward(4, ["w"], 10.01, 10.25)
    assert client.body_keys(now=10.261) == set(), "fresh pipe delivery cannot renew a stale browser command"
    assert not client.pause_requested(now=10.261)
    # The native consumer also latches body release, independently of service polling.
    assert forward(5, ["w"], 10.3, 10.3)
    assert client.body_keys(now=10.31) == set()
    assert forward(6, [], 10.32, 10.32)
    assert forward(7, ["w"], 10.33, 10.33)
    assert client.body_keys(now=10.34) == {"w"}
    for offset in range(1, 15):
        assert forward(7 + offset, [], 10.33, 10.33 + offset * .1)
    assert forward(22, [], 10.33, 11.829)
    assert not client.pause_requested(now=11.829)
    assert forward(23, [], 10.33, 11.831)
    assert client.pause_requested(now=11.831), "1.5 s presence expiry remains a latched full pause"


def test_native_fresh_nonempty_packet_cannot_erase_unpolled_body_gap():
    client = bridge.AM1ConsoleBridgeClient("pipe", Path("auth"), "session")
    prepare_native_gate(client, 10)
    client.accept_message({"session_id":"session", "epoch":1, "seq":1, "kind":"gate_ack",
                           "payload":{"stage":"live_start", "host_epoch":None}}, received_at=10)
    client.note_live_admitted()
    samples = [(2, [], 10, 10), (3, ["w"], 10.01, 10.01)]
    # Pipe delivery stays healthy; only the real browser receipt remains old.
    samples += [(4 + offset, ["w"], 10.01, 10.11 + offset * .1) for offset in range(8)]
    samples.append((12, ["w"], 10.94, 10.94))
    for seq, keys, browser_at, at in samples:
        client.accept_message({"session_id":"session", "epoch":1, "seq":seq, "kind":"lease",
            "payload":{"valid":True, "keys":keys, "browser_received_at_s":browser_at,
                       "body_release_required":False}}, received_at=at)
    assert client.body_keys(now=10.95) == set()
    assert not client.pause_requested(now=10.95)


def test_late_native_delivery_cannot_erase_pipe_loss_before_consumer_poll():
    client = bridge.AM1ConsoleBridgeClient("pipe", Path("auth"), "session")
    prepare_native_gate(client, 10)
    client.accept_message({"session_id":"session", "epoch":1, "seq":1, "kind":"gate_ack",
                           "payload":{"stage":"live_start", "host_epoch":None}}, received_at=10)
    client.note_live_admitted()
    for seq, keys, at in [(2, [], 10), (3, ["w"], 10.01), (4, ["w"], 10.4)]:
        client.accept_message({"session_id":"session", "epoch":1, "seq":seq, "kind":"lease",
            "payload":{"valid":True, "keys":keys, "browser_received_at_s":at,
                       "body_release_required":False}}, received_at=at)
    assert client.body_keys(now=10.41) == set()
    assert client.pause_requested(now=10.41), "native pipe freshness is still 250 ms, not 1.5 s"


def test_input_loss_zeros_and_latches_pause():
    client = bridge.AM1ConsoleBridgeClient("pipe", Path("auth"), "20261001T000000-1234abcd", clock=lambda: 0)
    prepare_native_gate(client, .99)
    client.accept_message({"session_id": client.session_id, "epoch": 1, "seq": 1,
                           "kind": "gate_ack", "payload": {"stage": "live_start", "host_epoch": None}}, received_at=0.99)
    client.note_live_admitted()
    client.accept_message({"session_id": client.session_id, "epoch": 1, "seq": 2,
                           "kind": "lease", "payload": lease_payload([], 1.0)}, received_at=1.0)
    client.accept_message({"session_id": client.session_id, "epoch": 1, "seq": 3,
                           "kind": "lease", "payload": lease_payload(["w"], 1.0)}, received_at=1.0)
    assert client.body_keys(now=1.10) == {"w"}
    assert not client.pause_requested(now=1.10)
    assert client.body_keys(now=1.251) == set()
    assert client.pause_requested(now=1.251)
    client.accept_message({"session_id": client.session_id, "epoch": 1, "seq": 4,
                           "kind": "lease", "payload": lease_payload(["w"], 1.30)}, received_at=1.30)
    assert client.body_keys(now=1.31) == set()  # Reconnect is not automatic rearm.
    assert client.pause_requested(now=1.31)


def test_body_requires_host_ack_and_fresh_release_after_resume():
    client = bridge.AM1ConsoleBridgeClient("pipe", Path("auth"), "20261001T000000-1234abcd", clock=lambda: 0)
    sid = client.session_id
    prepare_native_gate(client, 1)
    client.accept_message({"session_id": sid, "epoch": 1, "seq": 1,
                           "kind": "gate_ack", "payload": {"stage": "live_start", "host_epoch": None}}, received_at=1)
    client.accept_message({"session_id": sid, "epoch": 1, "seq": 2,
                           "kind": "lease", "payload": lease_payload(["w"], 1)}, received_at=1)
    assert client.body_keys(now=1.01) == set()
    client.note_live_admitted()
    assert client.body_keys(now=1.02) == set()
    client.accept_message({"session_id": sid, "epoch": 1, "seq": 3,
                           "kind": "lease", "payload": lease_payload([], 1.03)}, received_at=1.03)
    client.accept_message({"session_id": sid, "epoch": 1, "seq": 4,
                           "kind": "lease", "payload": lease_payload(["u"], 1.04)}, received_at=1.04)
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
    state.lease(now=2.6)
    assert state.first_pause == first
    assert state.lease(now=2.6)["pause_evidence"]["reason"] == "expired browser input"
    assert state.lease(now=2.6)["pause_evidence"]["pause_sequence"] == 2


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
    state.browser_keys(token="private-token", epoch=1, seq=2, keys=[], active=True, now=1.05)
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


@pytest.mark.parametrize("stage", ["sync_start", "live_start", "resume", "realign"])
def test_expired_body_release_cannot_be_mistaken_for_a_fresh_empty_gate_packet(stage):
    state = bridge.AM1ConsoleInputState("session", "token")
    state.request_pause("operator", now=1)
    state.request_gate(stage, host_epoch=2)
    state.browser_keys(token="token", epoch=1, seq=1, keys=[], active=True, now=1)
    assert state.approve(stage, host_epoch=2, token="token", now=1.01)
    state.browser_keys(token="token", epoch=1, seq=2, keys=["w"], active=True, now=1.4)
    assert state.keys == []  # Suppressed input is NOT an actual empty release packet.
    assert state.body_release_required
    assert not state.approve(stage, host_epoch=2, token="token", now=1.41)
    assert not state.gate_ack(stage, host_epoch=2, now=1.41)
    state.browser_keys(token="token", epoch=1, seq=3, keys=[], active=True, now=1.42)
    assert not state.gate_ack(stage, host_epoch=2, now=1.43), "old approval cannot revive"
    assert state.approve(stage, host_epoch=2, token="token", now=1.44)
    assert state.gate_ack(stage, host_epoch=2, now=1.45)


@pytest.mark.parametrize("metadata", [
    {}, {"browser_received_at_s": float("nan"), "body_release_required": False},
    {"browser_received_at_s": 10.1, "body_release_required": False},
    {"browser_received_at_s": True, "body_release_required": False},
    {"browser_received_at_s": 10, "body_release_required": 0},
    {"browser_received_at_s": 10**1000, "body_release_required": False},
])
def test_missing_or_invalid_original_browser_receipt_fails_closed(metadata):
    client = bridge.AM1ConsoleBridgeClient("pipe", Path("auth"), "session")
    prepare_native_gate(client, 10)
    client.accept_message({"session_id":"session", "epoch":1, "seq":1, "kind":"gate_ack",
                           "payload":{"stage":"live_start", "host_epoch":None}}, received_at=10)
    client.note_live_admitted()
    client.accept_message({"session_id":"session", "epoch":1, "seq":2, "kind":"lease",
                           "payload":lease_payload([], 10)}, received_at=10)
    client.accept_message({"session_id":"session", "epoch":1, "seq":3, "kind":"lease",
                           "payload":lease_payload(["w"], 10.01)}, received_at=10.01)
    assert client.body_keys(now=10.011) == {"w"}
    client.accept_message({"session_id":"session", "epoch":1, "seq":4, "kind":"lease",
                           "payload":{"valid":True, "keys":[], **metadata}}, received_at=10.02)
    assert client.pause_requested(now=10.021)
    assert client.body_keys(now=10.021) == set()


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
        assert state.lease(now=1.3)["body_release_required"]
        assert state.approved_gate is None  # Gate approval still expires at 250 ms.
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
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not client.pause_requested():
            time.sleep(0.02)
        assert client.get_action() == set()
        assert client.pause_requested()
    finally:
        client.disconnect()
        server.close()
    assert not server.auth_file.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="AF_PIPE is Windows-only")
def test_real_resume_has_fresh_lease_before_ack_and_host_admission(tmp_path):
    server = bridge.AM1ConsoleBridgeServer("session", tmp_path, "token")
    client = bridge.AM1ConsoleBridgeClient(server.pipe_name, server.auth_file, "session")
    ack_sent, release_ack = threading.Event(), threading.Event()
    completed = []
    waiter = None
    real_send = server._send
    def send(kind, payload, **kwargs):
        sent = real_send(kind, payload, **kwargs)
        if kind == "gate_ack" and sent:
            ack_sent.set()
            release_ack.wait(2)  # Expose consumer scheduling before the next server send.
        return sent
    try:
        server.request_pause("operator")
        server.browser_keys(token="token", epoch=1, seq=1, keys=[], active=True)
        client.connect()
        deadline = time.monotonic() + 1
        while client._received_at is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert client._received_at is not None and client.pause_requested()
        server._send = send
        waiter = threading.Thread(target=lambda: completed.append(client.wait_gate("resume", host_epoch=2, timeout_s=2)))
        waiter.start()
        deadline = time.monotonic() + 1
        while server.snapshot()["pending_gate"] != ("resume", 2) and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.browser_keys(token="token", epoch=1, seq=2, keys=[], active=True)
        assert server.approve("resume", host_epoch=2, token="token")
        assert ack_sent.wait(1)
        waiter.join(1)
        assert completed == [True]
        client.note_live_admitted(host_epoch=2)
        assert not client.pause_requested(), "actual fresh lease must precede approved handoff"
        assert client.get_action() == set()
    finally:
        release_ack.set()
        client.disconnect()
        server.close()
        if waiter is not None:
            waiter.join(2)


@pytest.mark.skipif(sys.platform != "win32", reason="AF_PIPE is Windows-only")
def test_owner_replacement_cannot_relabel_captured_lease_or_approve_old_waiter(tmp_path):
    server = bridge.AM1ConsoleBridgeServer("session", tmp_path, "token")
    client = bridge.AM1ConsoleBridgeClient(server.pipe_name, server.auth_file, "session")
    transferred = threading.Event()
    completed = []
    waiter = None
    real_send = server._send
    def send(kind, payload, **kwargs):
        if kind == "lease" and payload.get("valid") and not transferred.is_set() and server.snapshot()["pending_gate"] is None:
            # Real inactive release and ClaimInput after the old pair was captured.
            server.browser_keys(token="token", epoch=1, seq=3, keys=[], active=False)
            server.claim("replacement-token")
            transferred.set()
        return real_send(kind, payload, **kwargs)
    try:
        server.request_pause("operator")
        server.browser_keys(token="token", epoch=1, seq=1, keys=[], active=True)
        client.connect()
        deadline = time.monotonic() + 1
        while client._received_at is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert client.pause_requested()
        waiter = threading.Thread(target=lambda: completed.append(client.wait_gate("resume", host_epoch=2, timeout_s=2)))
        waiter.start()
        deadline = time.monotonic() + 1
        while server.snapshot()["pending_gate"] != ("resume", 2) and time.monotonic() < deadline:
            time.sleep(.01)
        server._send = send
        assert server.browser_keys(token="token", epoch=1, seq=2, keys=[], active=True)
        assert server.approve("resume", host_epoch=2, token="token")
        assert transferred.wait(1)
        waiter.join(3)
        assert completed == [False], "a replacement owner did not approve the old Resume"
        assert client._epoch == 2
        assert client.pause_requested()
        assert client.get_action() == set()
        assert server.state.approved_gate is None
        assert server.snapshot()["gate_ack_evidence"] is None, "a rejected captured acknowledgement was not sent"
    finally:
        client.disconnect()
        server.close()
        if waiter is not None:
            waiter.join(2)


@pytest.mark.skipif(sys.platform != "win32", reason="AF_PIPE is Windows-only")
def test_unused_pipe_closes_without_orphan_accept_thread(tmp_path):
    server = bridge.AM1ConsoleBridgeServer("20261001T000000-1234abcd", tmp_path, "private-token")
    server.close()
    assert not server._thread.is_alive()
    assert not server.auth_file.exists()


def test_native_lease_expiry_retains_cause_without_printing_on_action_consumer(capsys):
    client = bridge.AM1ConsoleBridgeClient("unused", Path("unused"), "session")
    prepare_native_gate(client, .99)
    client.accept_message({"session_id": "session", "epoch": 1, "seq": 1, "kind": "gate_ack",
                           "payload": {"stage": "live_start", "host_epoch": None}}, received_at=.99)
    client.accept_message({"session_id": "session", "epoch": 1, "seq": 2, "kind": "lease",
                           "payload": lease_payload([], 1)}, received_at=1)
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
    state.lease(now=2.5)
    first = {"reason": "window-blur", "local_wall_time_ms": 1000, "input_sequence": 1,
             "input_epoch": 1, "route": "control"}
    state.browser_keys(token="private-token", epoch=1, seq=3, keys=[], active=False, now=2.6,
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
    from tools.am1_console import ConsoleSessionAdapter
    adapter=ConsoleSessionAdapter(SimpleNamespace(),tmp_path,SimpleNamespace())
    adapter._on_created(server.session_id)
    def receive(event):
        received.append(event)
        adapter._emit(event)
    server.set_telemetry_sink(receive)
    timing_origin=time.monotonic()
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
                {"event":"startup_progress", "stage":"arm_sync", "frames_sent":17, "frame_count":301},
                {"event":"live_timing", "clock":"windows_monotonic", "live_started_at":timing_origin,
                 "deadline":timing_origin+90, "sampled_at":time.monotonic(), "duration_s":90},
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
        assert {event["event"] for event in received} >= {
            "live_sample", "host_feedback", "action_sent", "startup_progress", "live_timing"}
        view=adapter.state()["progress"]
        assert view["startup"]["frames_sent"] == 17
        assert view["live_timing"]["deadline"] == timing_origin+90
        assert view["live_timing"]["remaining_s"] < 90
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
