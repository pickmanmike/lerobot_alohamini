"""Hardware-free AM1 connection checks using the actual client and loopback peers."""

from __future__ import annotations

import queue
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

zmq = pytest.importorskip("zmq")

from lerobot.robots.alohamini.alohamini_client import AlohaMiniClient
from lerobot.robots.alohamini.config_alohamini import AlohaMiniClientConfig
from lerobot.utils.errors import DeviceNotConnectedError


@contextmanager
def observation_peer(*, delay_s=0.0, reply_token=None, reply=True, wrong_first=False):
    """Each socket belongs solely to this disposable peer thread, never a robot."""
    ready = queue.Queue()
    stop = threading.Event()
    errors = []
    actions = []

    def serve():
        context = zmq.Context()
        observations = context.socket(zmq.ROUTER)
        commands = context.socket(zmq.PULL)
        try:
            observation_port = observations.bind_to_random_port("tcp://127.0.0.1")
            command_port = commands.bind_to_random_port("tcp://127.0.0.1")
            ready.put((observation_port, command_port))
            poller = zmq.Poller()
            poller.register(observations, zmq.POLLIN)
            poller.register(commands, zmq.POLLIN)
            pending = []
            while not stop.is_set():
                events = dict(poller.poll(5))
                if commands in events:
                    actions.append(commands.recv_multipart())
                if observations in events:
                    identity, token = observations.recv_multipart()
                    if wrong_first:
                        observations.send_multipart([identity, b"retired", b'{}'])
                    if reply:
                        pending.append((time.monotonic() + delay_s, identity, reply_token or token))
                now = time.monotonic()
                for due, identity, token in pending:
                    if due <= now:
                        observations.send_multipart([identity, token, b'{}'])
                pending = [entry for entry in pending if entry[0] > now]
        except BaseException as exc:
            errors.append(exc)
        finally:
            observations.close(linger=0)
            commands.close(linger=0)
            context.term()

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    try:
        observation_port, command_port = ready.get(timeout=5)
        yield SimpleNamespace(observation_port=observation_port, command_port=command_port, actions=actions)
    finally:
        stop.set()
        worker.join(timeout=2)
        assert not worker.is_alive(), "local observation peer failed to stop"
        assert not errors


def make_client(peer, tmp_path, *, timeout_s=0.7):
    return AlohaMiniClient(AlohaMiniClientConfig(
        remote_ip="127.0.0.1", robot_model="alohamini1", id="connection-test",
        calibration_dir=tmp_path, cameras={}, connect_timeout_s=timeout_s,
        command_send_timeout_ms=50, observation_request_window=3,
        port_zmq_cmd=peer.command_port, port_zmq_observations=peer.observation_port,
    ))


@pytest.mark.parametrize("delay_s", [0.02, 0.16, 0.3])
def test_actual_connect_retains_handshake_across_short_polls(tmp_path, delay_s):
    with observation_peer(delay_s=delay_s, wrong_first=True) as peer:
        client = make_client(peer, tmp_path)
        start = time.monotonic()
        try:
            client.connect()
            elapsed = time.monotonic() - start
            assert client.is_connected
            assert delay_s <= elapsed < 0.65
            # Refilling may hit transient SNDHWM backpressure; it need not fill
            # every slot immediately, but must remain bounded.
            assert client._observation_request_id <= 2 + client.observation_request_window
            assert 0 < len(client._observation_request_tokens) <= client.observation_request_window
            assert len(client._observation_response_cache) <= client.observation_request_window
            assert set(client._observation_request_sent_at) == set(client._observation_request_tokens)
            assert b"1" not in client._observation_request_tokens
            # An availability reply must not become fresh movement/admission evidence.
            assert client.observation_sequence == 0
            assert client.latest_observation_received_at is None
            assert client._last_response_request_sent_at is None
            assert peer.actions == []
        finally:
            if client.is_connected:
                client.disconnect()


@pytest.mark.parametrize("reply_token", [None, b"unmatched", b"41"])
def test_actual_connect_times_out_without_a_matching_current_reply(tmp_path, reply_token):
    with observation_peer(reply=reply_token is not None, reply_token=reply_token) as peer:
        client = make_client(peer, tmp_path, timeout_s=0.24)
        client._observation_request_id = 41  # Token 41 belongs to a retired request.
        start = time.monotonic()
        with pytest.raises(DeviceNotConnectedError, match="Timeout waiting"):
            client.connect()
        assert 0.2 <= time.monotonic() - start < 0.8
        assert client._observation_request_id == 42  # No abandoned-request stream.
        assert not client.is_connected
        assert client.zmq_observation_socket.closed
        assert client.zmq_cmd_socket.closed
        assert client.zmq_context.closed
        assert not client._observation_request_tokens
        assert not client._observation_response_cache
        assert not client._observation_request_sent_at
        assert peer.actions == []


def test_actual_connect_cancels_promptly_while_waiting_for_delayed_reply(tmp_path):
    with observation_peer(delay_s=1.0) as peer:
        client = make_client(peer, tmp_path, timeout_s=2.0)
        reason = RuntimeError("operator cancelled startup")
        start = time.monotonic()

        def cancel():
            if time.monotonic() - start >= 0.12:
                raise reason

        with pytest.raises(RuntimeError) as caught:
            client.connect(cancel_check=cancel)
        assert caught.value is reason
        assert time.monotonic() - start < 0.5
        assert client._observation_request_id == 1
        assert client.zmq_observation_socket.closed
        assert client.zmq_cmd_socket.closed
        assert client.zmq_context.closed
        assert peer.actions == []


@pytest.mark.parametrize("send_timeout_ms", [None, 50])
def test_actual_failed_connect_with_queued_data_has_bounded_cleanup(tmp_path, send_timeout_ms):
    # Reserve (but never listen on) an ephemeral loopback port. A DEALER queues its
    # request without a peer. Infinite linger must not hang context termination.
    # The process is disposable: subprocess.run kills/reaps it on timeout even on RED.
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
        script = f'''
import time
from pathlib import Path
from lerobot.robots.alohamini.alohamini_client import AlohaMiniClient
from lerobot.robots.alohamini.config_alohamini import AlohaMiniClientConfig
from lerobot.utils.errors import DeviceNotConnectedError
client = AlohaMiniClient(AlohaMiniClientConfig(
    remote_ip="127.0.0.1", robot_model="alohamini1", id="queued-test", cameras={{}},
    calibration_dir=Path({str(tmp_path)!r}), connect_timeout_s=0.2,
    command_send_timeout_ms={send_timeout_ms!r}, port_zmq_cmd={port}, port_zmq_observations={port},
))
print("CONNECT_START", flush=True)
start = time.monotonic()
try:
    client.connect()
except DeviceNotConnectedError:
    assert time.monotonic() - start < 1.0
    assert client.zmq_cmd_socket.closed and client.zmq_observation_socket.closed
    assert client.zmq_context.closed
    assert not client.is_connected
    print("BOUNDED_CLEANUP_PASS", flush=True)
else:
    raise AssertionError("Connection unexpectedly succeeded without a peer")
'''
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "BOUNDED_CLEANUP_PASS" in result.stdout


def test_failed_connect_preserves_primary_and_reports_separate_cleanup_failure(tmp_path):
    reason = RuntimeError("operator stop")
    closures = []

    class Socket:
        def setsockopt(self, option, value): pass
        def connect(self, locator): pass

        def close(self, linger=None):
            closures.append(linger)

    class Context:
        def socket(self, kind): return Socket()

        def term(self):
            raise RuntimeError("separate context cleanup failure")

    client = make_client(SimpleNamespace(command_port=1, observation_port=2), tmp_path)
    client._zmq = SimpleNamespace(
        Context=Context, PUSH=1, DEALER=2, CONFLATE=3, SNDTIMEO=4, LINGER=5, RCVHWM=6, SNDHWM=7,
    )

    def cancel():
        raise reason

    with pytest.raises(RuntimeError) as caught:
        client.connect(cancel_check=cancel)
    assert caught.value is reason
    assert closures == [0, 0]
    assert any("separate context cleanup failure" in note for note in reason.__notes__)
