"""No-hardware protocol checks for the single native AM1 console input bridge."""

from __future__ import annotations

import importlib.util
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
