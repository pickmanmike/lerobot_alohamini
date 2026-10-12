"""Decoded local frames reach the mailbox independently of expensive owner work."""

import asyncio
import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tests.robots.test_am1_local_observation import headers, jpeg, source
from tests.robots.test_am1_persistent_session import Clock
from tools.am1_local_observation import LocalObservationMailbox, LocalObservationWorker, LocalReceiver
from tools.am1_pi_executor import PiExecutor, SimulatedIO
from tools.am1_session_ipc import (
    CONTROL_LIMIT,
    RESPONSE_LIMIT,
    IPCClient,
    OwnerServer,
    read_frame,
    write_frame,
)


def current_decoded_frame(tmp_path, box, clock):
    box.begin(str(uuid4()), 60)
    receiver = LocalReceiver(tmp_path, box.request, clock=clock)
    owner, capture = str(uuid4()), str(uuid4())
    for sequence in range(1, 4):
        clock.advance(0.11)
        frame_headers = headers(clock, sequence, owner, capture)
        frame_headers["X-Camera-Owned-Session"] = box.request["run_id"]
        assert receiver.accept(jpeg(), frame_headers)
    evidence = receiver.evidence()
    assert evidence["qualified"]
    return evidence


def test_actual_owner_consumes_decoded_frame_while_owner_mutex_is_busy(tmp_path):
    clock = Clock()
    box = LocalObservationMailbox(source(), clock=clock, require_owned_session=True)
    frame = current_decoded_frame(tmp_path / "pixels", box, clock)
    owner = OwnerServer(
        tmp_path / "owner",
        PiExecutor(
            SimulatedIO(clock),
            tmp_path / "admission",
            clock=clock,
            observation=box,
            sensing_policy="local-camera-required",
        ),
    )
    owner.authority.clock = clock
    done = threading.Event()
    result = {}

    def deliver():
        result.update(owner.observation_sync(frame))
        done.set()

    worker = threading.Thread(target=deliver)
    try:
        with owner.authority.mutex:
            worker.start()
            delivered_promptly = done.wait(0.2)
            if not delivered_promptly:
                # Reproduce the original decoded-frame expiry behind owner work.
                clock.advance(0.55)
        worker.join(1)
        assert not worker.is_alive()
        assert delivered_promptly, result
        assert result["consumption"]["qualified"]
        assert result["consumption"]["consumed_at"] == clock()
        assert result["consumption"]["arrived_at"] == frame["arrived_at"]
        assert result["consumption"]["decoded_at"] == frame["decoded_at"]
    finally:
        worker.join(1)
        owner.authority.close()


def test_authenticated_socket_delivery_avoids_saturated_owner_slots_and_threads(tmp_path):
    async def scenario():
        # Match the constrained Pi pool: six ordinary plus two protective waiters.
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=8))
        clock = Clock()
        box = LocalObservationMailbox(source(), clock=clock, require_owned_session=True)
        frame = current_decoded_frame(tmp_path / "pixels", box, clock)
        owner = OwnerServer(
            tmp_path / "owner",
            PiExecutor(
                SimulatedIO(clock),
                tmp_path / "admission",
                clock=clock,
                observation=box,
                sensing_policy="local-camera-required",
            ),
        )
        owner.authority.clock = clock
        original_snapshot, original_handle = owner.authority.snapshot, owner.authority.handle
        entered = threading.Event()
        count_lock = threading.Lock()
        waiting = 0

        def queued(call, *args):
            nonlocal waiting
            with count_lock:
                waiting += 1
                if waiting == 8:
                    entered.set()
            return call(*args)

        owner.authority.snapshot = lambda: queued(original_snapshot)
        owner.authority.handle = lambda *args: queued(original_handle, *args)
        await owner.start()
        client = IPCClient(tmp_path / "owner")
        pending = []
        delivered_promptly, result = False, None
        try:
            with owner.authority.mutex:
                pending = [
                    asyncio.create_task(client.request({"op": "snapshot"}, "fixture")) for _ in range(6)
                ] + [
                    asyncio.create_task(
                        client.request({"op": "stop", "operation_id": str(uuid4())}, "fixture")
                    )
                    for _ in range(2)
                ]
                async with asyncio.timeout(0.5):
                    while not entered.is_set():
                        await asyncio.sleep(0.005)
                try:
                    result = await asyncio.wait_for(
                        client.request({"op": "observation_sync", "evidence": frame}, "observation-worker"),
                        0.2,
                    )
                    delivered_promptly = True
                except TimeoutError:
                    clock.advance(0.55)
            await asyncio.gather(*pending, return_exceptions=True)
        finally:
            await asyncio.gather(*pending, return_exceptions=True)
            await owner.close()
        assert delivered_promptly, "camera delivery queued behind saturated owner work"
        assert result["consumption"]["qualified"]
        assert result["consumption"]["arrived_at"] == frame["arrived_at"]
        assert result["consumption"]["decoded_at"] == frame["decoded_at"]
        assert result["consumption"]["consumed_at"] == clock()

    asyncio.run(scenario())


@pytest.mark.parametrize("invalid", ["secret", "device", "command", "envelope", "protective_lane"])
def test_fast_local_route_keeps_authentication_and_closed_envelope_checks(tmp_path, invalid):
    async def scenario():
        clock = Clock()
        box = LocalObservationMailbox(source(), clock=clock)
        frame = current_decoded_frame(tmp_path / "pixels", box, clock)
        owner = OwnerServer(
            tmp_path / "owner",
            PiExecutor(
                SimulatedIO(clock),
                tmp_path / "admission",
                clock=clock,
                observation=box,
                sensing_policy="local-camera-required",
            ),
        )
        protective = invalid == "protective_lane"
        server = await asyncio.start_server(
            lambda reader, writer: owner.serve(reader, writer, protective),
            "127.0.0.1",
            0,
            limit=CONTROL_LIMIT + 4,
        )
        reader, writer = await asyncio.open_connection("127.0.0.1", server.sockets[0].getsockname()[1])
        envelope = {
            "secret": owner.secret,
            "id": str(uuid4()),
            "device": "observation-worker",
            "command": {"op": "observation_sync", "evidence": frame},
        }
        if invalid == "secret":
            envelope["secret"] = "incorrect"
        elif invalid == "device":
            envelope["device"] = "other-worker"
        elif invalid == "command":
            envelope["command"]["unexpected"] = True
        elif invalid == "envelope":
            envelope["unexpected"] = True
        try:
            await write_frame(writer, envelope, CONTROL_LIMIT)
            with pytest.raises((asyncio.IncompleteReadError, ConnectionError)):
                await read_frame(reader, RESPONSE_LIMIT)
            assert not box.evidence()["qualified"]
            assert box.record is None
        finally:
            writer.close()
            await writer.wait_closed()
            server.close()
            await server.wait_closed()
            owner.authority.close()

    asyncio.run(scenario())


def test_same_decoded_frame_still_expires_and_request_snapshot_cannot_mutate_owner(tmp_path):
    clock = Clock()
    box = LocalObservationMailbox(source(), clock=clock, require_owned_session=True)
    frame = current_decoded_frame(tmp_path, box, clock)
    first = box.sync(frame)
    assert first["consumption"]["qualified"]
    first["request"]["active"] = False
    first["request"]["generation"] = str(uuid4())
    assert box.request["active"]
    assert box.request["generation"] == frame["generation"]
    clock.advance(0.501)
    expired = box.sync(frame)
    assert not expired["consumption"]["qualified"]
    assert expired["consumption"]["reason"] == "original local arrival expired at owner consumption"
    assert expired["consumption"]["consumed_at"] == clock()
    assert frame["arrived_at"] == first["consumption"]["arrived_at"]
    assert frame["decoded_at"] == first["consumption"]["decoded_at"]


@pytest.mark.parametrize("operation", ["stop", "new_generation"])
def test_delivery_and_lifecycle_change_have_coherent_atomic_snapshots(tmp_path, operation):
    clock = Clock()
    box = LocalObservationMailbox(source(), clock=clock, require_owned_session=True)
    frame = current_decoded_frame(tmp_path, box, clock)
    publishing = threading.Event()
    release = threading.Event()
    changed = threading.Event()
    result, failures = {}, []

    def gated_clock():
        if threading.current_thread().name == "frame-delivery" and not release.is_set():
            publishing.set()
            assert release.wait(1)
        return clock()

    box.clock = gated_clock

    def deliver():
        try:
            result.update(box.sync(frame))
        except BaseException as error:
            failures.append(error)

    def change_lifecycle():
        if operation == "stop":
            box.stop()
        else:
            box.begin(str(uuid4()), 60)
        changed.set()

    delivery = threading.Thread(target=deliver, name="frame-delivery")
    lifecycle = threading.Thread(target=change_lifecycle)
    delivery.start()
    try:
        assert publishing.wait(0.2), failures
        lifecycle.start()
        assert not changed.wait(0.05)
    finally:
        release.set()
        delivery.join(1)
        if lifecycle.ident is not None:
            lifecycle.join(1)
    assert not failures
    assert result["request"]["active"]
    assert result["request"]["generation"] == frame["generation"]
    assert result["consumption"]["generation"] == frame["generation"]
    assert result["consumption"]["qualified"]
    later = box.sync(frame)
    assert not later["consumption"]["qualified"]
    if operation == "stop":
        assert not later["request"]["active"]
    else:
        assert later["request"]["generation"] != frame["generation"]


def test_nonlocal_mailbox_retains_authority_lock_and_existing_consumption_clock():
    entered = []

    class OwnerLock:
        def __enter__(self):
            entered.append("owner")

        def __exit__(self, *_):
            entered.append("released")

    class OtherMailbox:
        request = {"p1": True}

        def publish(self, evidence):
            assert entered == ["owner"]
            self.received = evidence

        def evidence(self):
            assert entered == ["owner"]
            return {"qualified": False}

        def sync(self, evidence):
            pytest.fail("another observation path must retain owner locking")

    mailbox = OtherMailbox()
    server = OwnerServer.__new__(OwnerServer)
    server.authority = SimpleNamespace(
        executor=SimpleNamespace(observation=mailbox),
        mutex=OwnerLock(),
        clock=lambda: 42,
    )
    result = server.observation_sync({"p1": "original"})
    assert entered == ["owner", "released"]
    assert mailbox.received == {"p1": "original"}
    assert result["consumption"] == {"qualified": False, "consumed_at": 42}


def test_camera_consumption_does_not_read_or_renew_native_current_fault_clocks(tmp_path, monkeypatch):
    from tests.robots.test_am1_selected_joint_evidence import visible_guard

    robot, guard, control, native_clock, _, port = visible_guard(monkeypatch, current_raw=16)
    original_current = copy.deepcopy(robot._am1_selected_joint_current)
    original_position = copy.deepcopy(robot._am1_selected_joint_position)
    original_writes = len(port.writes)
    clock = Clock()
    clock.now = native_clock[0]
    box = LocalObservationMailbox(source(), clock=clock)
    frame = current_decoded_frame(tmp_path, box, clock)
    server = OwnerServer.__new__(OwnerServer)
    server.authority = SimpleNamespace(
        executor=SimpleNamespace(
            observation=box,
            evidence=lambda: pytest.fail("camera consumption must not read motor evidence"),
        ),
        mutex=threading.RLock(),
        clock=clock,
    )
    assert server.observation_sync(frame)["consumption"]["qualified"]
    native_clock[0] = clock()
    with pytest.raises(RuntimeError, match="effort"):
        guard.guard_recovery(robot, control, {})
    fault = robot._am1_selected_joint_fault
    assert fault["current"] == original_current
    assert fault["position"] == original_position
    assert fault["captured_at_monotonic_s"] == clock()
    assert fault["current"]["read_completed_at_monotonic_s"] < clock()
    assert fault["effective_limit_ma"] == 100
    assert len(port.writes) == original_writes


def test_worker_delivers_actual_decode_before_existing_pacing_sleep(tmp_path, monkeypatch):
    import aiohttp

    from tools import am1_local_observation as local
    from tools.am1_camera_viewer import write_private

    clock = Clock()
    box = LocalObservationMailbox(source(), clock=clock)
    box.begin(str(uuid4()), 60)
    credentials = tmp_path / "credentials.json"
    write_private(credentials, {"username": "fixture", "password": "fixture-password"})
    monkeypatch.setattr(local, "validate_local_endpoint", lambda *_: None)
    monkeypatch.setattr(
        local, "LocalReceiver", lambda directory, binding: LocalReceiver(directory, binding, clock=clock)
    )
    worker = LocalObservationWorker(
        {
            "owner_state": str(tmp_path / "owner"),
            "state": str(tmp_path / "worker"),
            "url": "http://127.0.0.1:9999/api/frame.jpeg?src=chest",
            "credentials": str(credentials),
            "source": source(),
        }
    )
    deliveries = []
    owner, capture = str(uuid4()), str(uuid4())

    class FixtureIPC:
        async def request(self, command, device):
            assert device == "observation-worker"
            evidence = command["evidence"]
            if evidence is not None:
                deliveries.append(evidence)
                box.publish(evidence)
            return {"request": box.request, "consumption": dict(box.evidence(), consumed_at=clock())}

    class Content:
        async def read(self, limit):
            return jpeg()

        def at_eof(self):
            return True

    class Response:
        status = 200
        content = Content()

        async def __aenter__(self):
            clock.advance(0.11)
            self.headers = headers(clock, 1, owner, capture) | {"Content-Type": "image/jpeg"}
            return self

        async def __aexit__(self, *_):
            pass

    class Session:
        def __init__(self, **_):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def get(self, *_args, **_kwargs):
            return Response()

    async def pacing(duration):
        assert duration == 0.075
        assert len(deliveries) == 1, "decoded evidence must be delivered before pacing"
        assert deliveries[0]["sequence"] == 1
        assert deliveries[0]["frame_sha256"] == worker.receiver.record["frame_sha256"]
        assert deliveries[0]["arrived_at"] <= deliveries[0]["decoded_at"] == clock()
        raise asyncio.CancelledError

    worker.ipc = FixtureIPC()
    monkeypatch.setattr(aiohttp, "ClientSession", Session)
    monkeypatch.setattr(local.asyncio, "sleep", pacing)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker.run())
