"""Hardware-free AM1 connection checks using the actual client and loopback peers."""

from __future__ import annotations

import queue
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager, suppress
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


@contextmanager
def cadence_client(monkeypatch, *, host_period_s=0.078):
    """Real inproc envelopes with deterministic synthetic host/worker timing."""
    from collections import deque

    from lerobot.robots.alohamini import alohamini_client as client_module

    context = zmq.Context()
    server, socket_ = context.socket(zmq.ROUTER), context.socket(zmq.DEALER)
    for peer_socket in (server, socket_):
        peer_socket.setsockopt(zmq.SNDHWM, 3)
        peer_socket.setsockopt(zmq.RCVHWM, 3)
        peer_socket.setsockopt(zmq.LINGER, 0)
    server.bind("inproc://am1-original-request-cadence")
    socket_.connect("inproc://am1-original-request-cadence")
    clock = SimpleNamespace(now=0.0, next=0.035, served=0, retired=0)
    client = object.__new__(AlohaMiniClient)
    client.config = SimpleNamespace(robot_model="alohamini1")
    client.zmq_observation_socket = socket_
    client.observation_request_window = 3
    client.polling_timeout_ms = 40
    client._observation_request_tokens = deque()
    client._observation_response_cache = {}
    client._observation_request_sent_at = {}
    client._observation_request_id = 0
    client._last_response_request_sent_at = None

    def advance(until, stop_on_reply=False):
        while clock.next <= until:
            clock.now = clock.next
            clock.served += 1
            clock.next += 0.035 if clock.served < 2 else host_period_s
            if server.poll(0, zmq.POLLIN):
                identity, token = server.recv_multipart()
                if token not in client._observation_request_tokens:
                    clock.retired += 1
                with suppress(zmq.Again):  # Same bounded response drop as the host.
                    server.send_multipart([identity, token, str(clock.served).encode()], zmq.NOBLOCK)
            if stop_on_reply and socket_.poll(0, zmq.POLLIN):
                return True
        clock.now = until
        return False

    class Poller:
        def register(self, socket, event):
            assert socket is socket_ and event == zmq.POLLIN

        def poll(self, milliseconds):
            if socket_.poll(0, zmq.POLLIN) or advance(clock.now + milliseconds / 1000, True):
                return [(socket_, zmq.POLLIN)]
            return []

    client._zmq = SimpleNamespace(
        Poller=Poller, NOBLOCK=zmq.NOBLOCK, POLLIN=zmq.POLLIN, ZMQError=zmq.ZMQError, Again=zmq.Again
    )
    monkeypatch.setattr(client_module, "time", SimpleNamespace(monotonic=lambda: clock.now))
    try:
        client._fill_observation_request_window()
        yield SimpleNamespace(client=client, clock=clock, advance=advance, server=server)
    finally:
        socket_.close(0)
        server.close(0)
        context.term()


@pytest.mark.parametrize("host_period_s", [0.035, 0.078])
def test_am1_short_empty_polls_do_not_starve_original_inflight_requests(monkeypatch, host_period_s):
    # Synthetic model: two quick availability replies then measured-scale78ms owner cycles.
    # It is a deterministic queue regression, not a replay of actual09 wire packets.
    with cadence_client(monkeypatch, host_period_s=host_period_s) as peer:
        client, advanced = peer.client, []
        for poll in range(120):
            message = client._poll_and_get_latest_message()
            if message is not None:
                advanced.append(
                    (poll, int(message[0]), peer.clock.now, client._last_response_request_sent_at)
                )
            assert len(client._observation_request_tokens) <= 3
            assert len(client._observation_response_cache) <= 3
            assert set(client._observation_request_sent_at) == set(client._observation_request_tokens)
            peer.advance(peer.clock.now + 1 / 30)  # Existing worker wait after each bounded poll.
        print(
            {
                "synthetic_host_period_s": host_period_s,
                "polls": 120,
                "advancing": len(advanced),
                "served": peer.clock.served,
                "retired_before_response": peer.clock.retired,
                "last_advance_poll": advanced[-1][0] if advanced else None,
            }
        )
        assert len(advanced) >= 60
        assert advanced[-1][0] >= 115
        assert all(0 <= received - requested < 1.0 for _, _, received, requested in advanced)


def test_am1_empty_poll_retains_credit_and_original_timestamp(monkeypatch):
    with cadence_client(monkeypatch) as peer:
        peer.clock.next = 0.078
        client = peer.client
        original = tuple(client._observation_request_tokens)
        timestamps = dict(client._observation_request_sent_at)
        assert client._poll_and_get_latest_message() is None
        assert tuple(client._observation_request_tokens) == original
        assert client._observation_request_sent_at == timestamps
        assert client._observation_request_id == 3
        assert client._last_response_request_sent_at is None


def test_am1_all_lost_credits_retry_only_at_original_one_second_and_recover(monkeypatch):
    with cadence_client(monkeypatch) as peer:
        peer.clock.next = 100.0  # This peer loses the entire first request window.
        client = peer.client
        for _ in range(3):
            peer.server.recv_multipart(zmq.NOBLOCK)
        assert client._poll_and_get_latest_message() is None
        assert tuple(client._observation_request_tokens) == (b"1", b"2", b"3")
        peer.clock.now = 0.95
        assert client._poll_and_get_latest_message() is None
        assert tuple(client._observation_request_tokens) == (b"1", b"2", b"3")
        assert client._observation_request_sent_at == {b"1": 0.0, b"2": 0.0, b"3": 0.0}
        peer.clock.now = 1.0
        assert client._poll_and_get_latest_message() is None
        assert tuple(client._observation_request_tokens) == (b"4", b"5", b"6")
        assert client._observation_request_sent_at == {b"4": 1.0, b"5": 1.0, b"6": 1.0}
        identity, token = peer.server.recv_multipart(zmq.NOBLOCK)
        peer.server.send_multipart([identity, token, b"recovered"])
        peer.clock.now = 1.1
        assert client._poll_and_get_latest_message() == [b"recovered"]
        assert client._last_response_request_sent_at == 1.0
        assert len(client._observation_request_tokens) <= 3


def test_am1_exactly_stale_cached_reply_is_excluded_before_later_fresh_selection(monkeypatch):
    with cadence_client(monkeypatch) as peer:
        peer.clock.next = 100.0
        client = peer.client
        requests = [peer.server.recv_multipart(zmq.NOBLOCK) for _ in range(3)]
        identity = requests[0][0]
        peer.server.send_multipart([identity, b"1", b"first"])
        peer.clock.now = 0.8
        assert client._poll_and_get_latest_message() == [b"first"]
        assert peer.server.recv_multipart(zmq.NOBLOCK)[1] == b"4"
        peer.server.send_multipart([identity, b"2", b"stale"])
        peer.server.send_multipart([identity, b"4", b"fresh"])
        client._drain_observation_responses(limit=3)
        assert b"2" in client._observation_response_cache and b"4" in client._observation_response_cache
        peer.clock.now = 1.0  # Token2 original request was at0, token4 at.8.
        assert client._poll_and_get_latest_message() == [b"fresh"]
        assert client._last_response_request_sent_at == 0.8
        assert b"2" not in client._observation_response_cache
        assert b"2" not in client._observation_request_sent_at
        assert len(client._observation_request_tokens) <= 3


def test_am1_explicit_retire_still_fences_late_pre_pause_reply(monkeypatch):
    with cadence_client(monkeypatch) as peer:
        peer.clock.next = 100.0
        client = peer.client
        identity = [peer.server.recv_multipart(zmq.NOBLOCK) for _ in range(3)][0][0]
        peer.clock.now = 0.2
        client.retire_observation_requests()
        assert tuple(client._observation_request_tokens) == (b"4", b"5", b"6")
        assert client._last_response_request_sent_at is None
        peer.server.send_multipart([identity, b"1", b"late-before-pause"])
        peer.server.send_multipart([identity, b"4", b"after-pause"])
        peer.clock.now = 0.25
        assert client._poll_and_get_latest_message() == [b"after-pause"]
        assert client._last_response_request_sent_at == 0.2
        assert b"1" not in client._observation_response_cache
        assert set(client._observation_request_sent_at) == set(client._observation_request_tokens)


def test_am1_failed_request_send_never_invents_credit_or_timestamp(monkeypatch):
    with cadence_client(monkeypatch) as peer:
        client = peer.client
        client._observation_request_tokens.clear()
        client._observation_request_sent_at.clear()
        client.zmq_observation_socket = SimpleNamespace(
            send=lambda *_args, **_kwargs: (_ for _ in ()).throw(zmq.Again())
        )
        client._fill_observation_request_window()
        assert not client._observation_request_tokens
        assert not client._observation_request_sent_at
        assert not client._observation_response_cache


def test_am2_empty_poll_retains_existing_one_missing_credit_retirement(monkeypatch):
    with cadence_client(monkeypatch) as peer:
        peer.clock.next = 100.0
        client = peer.client
        client.config.robot_model = "alohamini2"
        assert client._poll_and_get_latest_message() is None
        assert tuple(client._observation_request_tokens) == (b"2", b"3", b"4")
        assert b"1" not in client._observation_request_sent_at


def test_am1_reply_crossing_original_request_age_during_poll_is_excluded(monkeypatch):
    with cadence_client(monkeypatch) as peer:
        client = peer.client
        peer.clock.now, peer.clock.next = 0.98, 1.01
        assert client._poll_and_get_latest_message() is None
        assert client._last_response_request_sent_at is None
        assert not {b"1", b"2", b"3"}.intersection(client._observation_request_tokens)
        assert all(sent >= 1.0 for sent in client._observation_request_sent_at.values())
