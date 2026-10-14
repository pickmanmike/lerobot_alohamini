"""Real JPEG/local arrival evidence and fixed sensing policy; no physical IO."""

import importlib
import io
import uuid

import pytest
from PIL import Image

from tests.robots.test_am1_persistent_session import Clock, command, start
from tools.am1_pi_executor import PiExecutor, SimulatedIO
from tools.am1_session_core import SessionAuthority


def module():
    assert importlib.util.find_spec("tools.am1_local_observation"), "local current-image adapter missing"
    return importlib.import_module("tools.am1_local_observation")


def source():
    return {
        "machine": "fixture-pi",
        "source": "am1-camera-viewer",
        "device": "/dev/am_camera_chest",
        "framing_revision": "native-chest-v1",
        "role": "chest",
    }


def jpeg():
    stream = io.BytesIO()
    Image.new("RGB", (640, 480), (45, 85, 110)).save(stream, format="JPEG")
    return stream.getvalue()


def headers(c, seq, owner, capture):
    return {
        "X-Camera-Machine": "fixture-pi",
        "X-Camera-Device": "/dev/am_camera_chest",
        "X-Camera-Role": "chest",
        "X-Camera-Owner-Generation": owner,
        "X-Camera-Capture-Generation": capture,
        "X-Frame-Sequence": str(seq),
        "X-Frame-Arrived-Monotonic-S": repr(c()),
        "X-Frame-Timing": "pi-upstream-arrival-monotonic",
    }


def receiver(tmp_path, c):
    m = module()
    box = m.LocalObservationMailbox(source(), clock=c)
    box.begin(str(uuid.uuid4()), 420)
    return box, m.LocalReceiver(tmp_path, box.request, clock=c)


def feed(box, r, c, seq, owner, capture, payload=None):
    assert r.accept(jpeg() if payload is None else payload, headers(c, seq, owner, capture))
    e = r.evidence()
    box.publish(e)
    return e


def test_original_arrival_expires_even_if_http_response_is_recent(tmp_path):
    c = Clock()
    box, r = receiver(tmp_path, c)
    owner, capture = str(uuid.uuid4()), str(uuid.uuid4())
    for seq in range(1, 4):
        c.advance(0.11)
        feed(box, r, c, seq, owner, capture)
    assert box.evidence()["qualified"]
    last = headers(c, 4, owner, capture)
    c.advance(0.501)
    assert not r.accept(jpeg(), last)
    box.publish(r.evidence())
    assert not box.evidence()["qualified"]
    assert not r.accept(jpeg(), headers(c, 5, owner, capture) | {"X-Frame-Arrived-Monotonic-S": "nan"})


def test_decode_restart_and_retired_generation_replay_fail_closed(tmp_path):
    c = Clock()
    box, r = receiver(tmp_path, c)
    owner, capture = str(uuid.uuid4()), str(uuid.uuid4())
    for seq in range(1, 4):
        c.advance(0.11)
        feed(box, r, c, seq, owner, capture)
    assert box.evidence()["qualified"]
    c.advance(0.1)
    assert not r.accept(b"not an image", headers(c, 4, owner, capture))
    box.publish(r.evidence())
    assert not box.evidence()["qualified"]
    newer = str(uuid.uuid4())
    c.advance(0.1)
    feed(box, r, c, 5, owner, newer)
    assert not box.evidence()["qualified"]
    assert not r.accept(jpeg(), headers(c, 6, owner, capture))
    for seq in range(7, 10):
        c.advance(0.11)
        feed(box, r, c, seq, owner, newer)
    assert box.evidence()["qualified"]
    e = r.evidence()
    box.publish(dict(e, generation=str(uuid.uuid4())))
    assert box.evidence()["qualified"]
    c.advance(0.6)
    box.publish(e)
    assert not box.evidence()["qualified"]


def test_fixed_policy_ignores_advisory_p1_and_freezes_recovers_same_local_run(tmp_path):
    m = module()
    c = Clock()
    box = m.LocalObservationMailbox(source(), clock=c)
    executor = PiExecutor(
        SimulatedIO(c),
        tmp_path / "admission",
        clock=c,
        observation=box,
        sensing_policy="local-camera-required",
    )
    authority = SessionAuthority(tmp_path / "owner", clock=c, executor=executor)
    try:
        result = start(authority, "sim-arm-smoke-repeat")
        assert result["accepted"] and authority.run["status"] == "preparing"
        assert result["sensing_policy"] == "local-camera-required"
        assert authority.run["sensing_source"] == source()
        r = m.LocalReceiver(tmp_path / "pixels", box.request, clock=c)
        owner, capture = str(uuid.uuid4()), str(uuid.uuid4())
        for seq in range(1, 76):
            c.advance(0.1)
            feed(box, r, c, seq, owner, capture)
            authority.tick()
        before = authority.snapshot()
        assert before["run"]["status"] == "running" and before["run"]["progress_s"] > 0
        assert before["run"]["recovery_episodes"] == 0
        assert before["evidence"]["optional_observation"]["state"] == "not-requested"
        assert not before["evidence"]["optional_quality"]
        seed = executor.task.original_seed
        deadline = before["run"]["deadline"]
        c.advance(0.6)
        authority.tick()
        held = authority.run["progress_s"]
        assert authority.run["status"] == "recovering"
        c.advance(0.1)
        authority.tick()
        assert authority.run["progress_s"] == held
        for seq in range(76, 85):
            c.advance(0.1)
            feed(box, r, c, seq, owner, capture)
            authority.tick()
        assert authority.run["status"] == "running" and authority.run["recovery_episodes"] == 1
        assert authority.run["deadline"] == deadline
        assert executor.task.original_seed == seed and executor.task.admissions == 1
        authority.handle(command("stop", run_id=result["run_id"]), "owner")
        persisted = authority.handle(command("lookup", operation_id=result["operation_id"]), "owner")
        assert persisted["sensing_policy"] == "local-camera-required"
        assert persisted["sensing_source"] == source()
    finally:
        authority.close()


def test_policy_selection_cannot_hide_missing_required_source(tmp_path):
    from tools.am1_observation import ObservationMailbox

    c = Clock()
    strict = ObservationMailbox(
        {k: source()[k] for k in ("machine", "source", "device", "framing_revision")}, c
    )
    executor = PiExecutor(
        SimulatedIO(c), tmp_path / "strict", clock=c, observation=strict, sensing_policy="p1-required"
    )
    assert not executor.evidence()["required_observation"]
    for policy, box in [("simulated", strict), ("local-camera-required", strict), ("p1-required", None)]:
        with pytest.raises(ValueError):
            PiExecutor(SimulatedIO(c), tmp_path / policy, clock=c, observation=box, sensing_policy=policy)


def test_delayed_foreign_p1_record_cannot_invalidate_current_local_proof(tmp_path):
    c = Clock()
    box, r = receiver(tmp_path, c)
    owner, capture = str(uuid.uuid4()), str(uuid.uuid4())
    for seq in range(1, 4):
        c.advance(0.11)
        feed(box, r, c, seq, owner, capture)
    before = box.evidence()
    foreign = dict(
        r.evidence(),
        run_id=str(uuid.uuid4()),
        generation=str(uuid.uuid4()),
        source="p1-spare",
        device="external-device",
        qualified=False,
    )
    box.publish(foreign)
    assert box.evidence() == before
    box.publish(dict(r.evidence(), qualified=False, reason="current local bytes malformed"))
    assert not box.evidence()["qualified"]


def test_local_worker_real_http_and_owner_ipc_expire_and_recover(tmp_path, monkeypatch):
    import asyncio
    import socket
    import threading

    from tools import am1_camera_viewer as viewer
    from tools.am1_session_ipc import OwnerServer

    m = module()
    # OS route validation has a separate behavior test; this cross-platform boundary
    # uses actual HTTP and IPC sockets with no physical camera/network route.
    monkeypatch.setattr(m, "validate_local_endpoint", lambda url, configured: None)
    identity = source() | {"machine": socket.gethostname()}
    credentials = tmp_path / "credentials.json"
    viewer.write_private(credentials, {"username": "fixture", "password": "fixture-private-password"})
    store = viewer.FrameStore()
    store.publish(jpeg())
    server = viewer.make_server(
        ("127.0.0.1", 0), {"chest": store}, {"chest": identity["device"]}, viewer.load_private(credentials)
    )
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    stopping = threading.Event()

    def frames():
        while not stopping.wait(0.033):
            store.publish(jpeg())

    producer = threading.Thread(target=frames, daemon=True)
    producer.start()

    async def scenario():
        box = m.LocalObservationMailbox(identity)
        owner = OwnerServer(
            tmp_path / "owner",
            PiExecutor(
                SimulatedIO(), tmp_path / "admission", observation=box, sensing_policy="local-camera-required"
            ),
        )
        await owner.start()
        worker = m.LocalObservationWorker(
            {
                "owner_state": str(tmp_path / "owner"),
                "state": str(tmp_path / "worker"),
                "credentials": str(credentials),
                "source": identity,
                "url": f"http://127.0.0.1:{server.server_port}/api/frame.jpeg?src=chest",
            }
        )
        task = asyncio.create_task(worker.run())

        async def until(predicate):
            async with asyncio.timeout(5):
                while not predicate():
                    if task.done():
                        task.result()
                    await asyncio.sleep(0.025)

        try:
            result = start(owner.authority, "sim-arm-smoke-repeat")
            await until(lambda: owner.authority.snapshot()["run"]["status"] == "running")
            before = owner.authority.snapshot()
            pause = tmp_path / "worker" / "pause-delivery"
            pause.touch()
            await until(lambda: owner.authority.snapshot()["run"]["status"] == "recovering")
            held = owner.authority.snapshot()["run"]["progress_s"]
            await asyncio.sleep(0.2)
            assert owner.authority.snapshot()["run"]["progress_s"] == held
            pause.unlink()
            await until(lambda: owner.authority.snapshot()["run"]["status"] == "running")
            after = owner.authority.snapshot()
            assert after["run"]["run_id"] == result["run_id"]
            assert after["run"]["deadline"] == before["run"]["deadline"]
            assert after["run"]["recovery_episodes"] == 1
            assert after["evidence"]["observation_provenance"] == "real-local-camera/pi-decoded-arrival"
            assert (worker.receiver.directory / "latest.jpeg").read_bytes() == jpeg()
            assert (worker.receiver.directory / "delivery.jsonl").stat().st_size > 0
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await owner.close()

    try:
        asyncio.run(scenario())
    finally:
        stopping.set()
        producer.join(2)
        server.shutdown()
        server.server_close()
        serving.join(2)


@pytest.mark.parametrize(
    "route",
    [
        [{"type": "local", "dst": "192.168.1.20", "dev": "lo"}],
        [{"dst": "192.168.1.20", "dev": "wlan0"}],
    ],
)
def test_local_endpoint_requires_same_machine_local_route(monkeypatch, route):
    import json
    import socket
    import subprocess

    m = module()
    identity = source() | {"machine": socket.gethostname()}
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, json.dumps(route))
    )
    if route[0].get("type") == "local":
        m.validate_local_endpoint("http://192.168.1.20:1984/api/frame.jpeg?src=chest", identity)
    else:
        with pytest.raises(ValueError, match="locally"):
            m.validate_local_endpoint("http://192.168.1.20:1984/api/frame.jpeg?src=chest", identity)


def test_camera_reconnect_retires_old_generation_before_new_qualification(tmp_path):
    c = Clock()
    box, r = receiver(tmp_path, c)
    owner, capture = str(uuid.uuid4()), str(uuid.uuid4())
    for seq in range(1, 4):
        c.advance(0.11)
        old = feed(box, r, c, seq, owner, capture)
    assert box.evidence()["qualified"]
    c.advance(0.1)
    feed(box, r, c, 4, owner, str(uuid.uuid4()))
    assert not box.evidence()["qualified"]
    box.publish(dict(old, sequence=5))
    assert not box.evidence()["qualified"]


def test_repeated_http_snapshot_does_not_renew_or_remove_current_proof(tmp_path):
    c = Clock()
    box, r = receiver(tmp_path, c)
    owner, capture = str(uuid.uuid4()), str(uuid.uuid4())
    for seq in range(1, 4):
        c.advance(0.11)
        feed(box, r, c, seq, owner, capture)
    last = headers(c, 3, owner, capture)
    original = r.evidence()["arrived_at"]
    c.advance(0.1)
    assert r.accept(jpeg(), last)
    assert r.evidence()["qualified"] and r.evidence()["arrived_at"] == original
    c.advance(0.401)
    assert not r.accept(jpeg(), last)
    assert not r.evidence()["qualified"]


def test_malformed_jpeg_with_oversized_decoded_dimensions_unqualifies_without_crashing(tmp_path):
    c = Clock()
    box, r = receiver(tmp_path, c)
    payload = bytearray(jpeg())
    marker = payload.index(b"\xff\xc0")
    payload[marker + 5 : marker + 9] = b"\xff\xff\xff\xff"
    assert not r.accept(bytes(payload), headers(c, 1, str(uuid.uuid4()), str(uuid.uuid4())))
    assert not r.evidence()["qualified"]
