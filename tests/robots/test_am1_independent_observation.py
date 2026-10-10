"""Real byte evidence and finite source boundaries; no devices or remote hosts."""

import base64
import importlib
import io
import uuid

import pytest
from PIL import Image

from tests.robots.test_am1_persistent_session import Clock, command, start
from tools.am1_pi_executor import PiExecutor, SimulatedIO
from tools.am1_session_core import SessionAuthority


def module():
    assert importlib.util.find_spec("tools.am1_observation_worker"), "independent observation adapter missing"
    return importlib.import_module("tools.am1_observation_worker")


def request():
    return {
        "run_id": str(uuid.uuid4()),
        "generation": str(uuid.uuid4()),
        "machine": "fixture-p1",
        "source": "spare",
        "device": "fixture-device",
        "framing_revision": "arms-lift-v1",
        "duration_seconds": 480,
        "max_recording_bytes": 140 * 1024 * 1024,
        "active": True,
    }


def jpeg():
    output = io.BytesIO()
    Image.new("RGB", (640, 360), (90, 130, 170)).save(output, format="JPEG")
    return base64.b64encode(output.getvalue()).decode()


def frame(binding, nonce, sequence, **changes):
    value = {
        "event": "frame",
        "generation": binding["generation"],
        "nonce": nonce,
        "sequence": sequence,
        "source_system_relative_ticks": 10000000 + sequence * 1000000,
        "challenge_received_qpc_ticks": 9900000 + sequence * 1000000,
        "capture_age_ms": 5,
        "recording": True,
        "jpeg_base64": jpeg(),
        "binding": {key: binding[key] for key in module().BINDING_KEYS},
    }
    value.update(changes)
    return value


def feed(receiver, binding, c, seq):
    nonce = f"n{seq}"
    receiver.issue_challenge(nonce, c() * 1000 - 25)
    assert receiver.accept_bound(frame(binding, nonce, seq), c() * 1000)
    return receiver.evidence(c())


def test_actual_jpeg_binding_expiry_replay_and_processing_age(tmp_path):
    m = module()
    c = Clock()
    binding = request()
    r = m.BoundReceiver(tmp_path / "pixels", binding, clock=c)
    for seq in range(1, 4):
        evidence = feed(r, binding, c, seq)
        c.advance(0.11)
    assert evidence["qualified"]
    assert evidence["frame_sha256"] and evidence["sequence"] == 3
    assert not r.accept_bound(frame(binding, "n3", 3), c() * 1000)
    assert not r.evidence(c())["qualified"]
    c.advance(0.1)
    feed(r, binding, c, 4)
    c.advance(0.6)
    assert not r.evidence(c())["qualified"]
    bad = dict(binding, machine="other")
    r.issue_challenge("bad", c() * 1000 - 25)
    assert not r.accept_bound(frame(bad, "bad", 5), c() * 1000)
    r.issue_challenge("badjpeg", c() * 1000 - 25)
    assert not r.accept_bound(
        frame(binding, "badjpeg", 6, jpeg_base64=base64.b64encode(b"not pixels").decode()), c() * 1000
    )
    r.issue_challenge("late", c() * 1000 - 25)
    received = c() * 1000
    c.advance(0.6)
    assert not r.accept_bound(frame(binding, "late", 7), received)


def test_owner_real_evidence_freezes_recovers_same_run_and_stop_precedes_recovery(tmp_path):
    m = module()
    c = Clock()
    binding = request()
    mailbox = m.ObservationMailbox({key: binding[key] for key in m.SOURCE_KEYS}, clock=c)
    executor = PiExecutor(SimulatedIO(c), tmp_path / "admission", clock=c, observation=mailbox)
    authority = SessionAuthority(tmp_path / "owner", clock=c, executor=executor)
    try:
        result = start(authority, "sim-arm-smoke-repeat")
        assert result["accepted"] and authority.run["status"] == "preparing"
        source = mailbox.request
        r = m.BoundReceiver(tmp_path / "pixels", source, clock=c)
        deadline = authority.run["deadline"]
        for seq in range(1, 75):
            c.advance(0.1)
            mailbox.publish(feed(r, source, c, seq))
            authority.tick()
        assert authority.run["progress_s"] > 0
        seed = executor.task.snapshot()["original_seed"]
        progress = authority.run["progress_s"]
        c.advance(0.6)
        authority.tick()
        assert authority.run["status"] == "recovering"
        assert authority.run["progress_s"] == progress
        for seq in range(75, 81):
            c.advance(0.1)
            mailbox.publish(feed(r, source, c, seq))
            authority.tick()
        assert authority.run["status"] == "running"
        assert executor.task.snapshot()["original_seed"] == seed
        assert authority.run["deadline"] == deadline and authority.run["run_id"] == result["run_id"]
        assert authority.run["recovery_episodes"] == 1
        authority.handle(command("stop", run_id=result["run_id"]), "owner")
        c.advance(0.1)
        mailbox.publish(feed(r, source, c, 81))
        authority.tick()
        assert authority.run["status"] == "stopped" and not mailbox.request["active"]
        assert executor.evidence()["ack_provenance"] == "simulated-backend"
    finally:
        authority.close()


def test_mailbox_bad_generation_and_old_record_cannot_extend_proof(tmp_path):
    m = module()
    c = Clock()
    b = request()
    box = m.ObservationMailbox({key: b[key] for key in m.SOURCE_KEYS}, clock=c)
    box.begin(b["run_id"], 420)
    b = box.request
    r = m.BoundReceiver(tmp_path / "pixels", b, clock=c)
    for seq in range(1, 4):
        c.advance(0.11)
        e = feed(r, b, c, seq)
        box.publish(e)
    assert box.evidence()["qualified"]
    box.publish(dict(e, generation="other"))
    assert not box.evidence()["qualified"]
    c.advance(0.6)
    box.publish(e)
    assert not box.evidence()["qualified"]


def test_source_dedup_bounds_and_restart_never_reopen(tmp_path):
    assert importlib.util.find_spec("tools.am1_p1_observer_agent"), "bounded P1 source agent missing"
    from tools.am1_p1_observer_agent import CaptureLedger

    c = Clock()
    b = request()
    ledger = CaptureLedger(tmp_path, clock=c)
    first = ledger.start(b)
    c.advance(8)
    assert ledger.start(b) == first
    with pytest.raises(ValueError):
        ledger.start(dict(b, duration_seconds=660))
    with pytest.raises(ValueError):
        ledger.start(dict(b, run_id=str(uuid.uuid4())))
    ledger.terminal({"event": "complete", "camera_released": True, "success": True})
    assert ledger.start(b)["terminal"]["camera_released"]
    assert CaptureLedger(tmp_path, clock=c).start(b)["terminal"]["camera_released"]
    with pytest.raises(ValueError):
        ledger.start(dict(request(), duration_seconds=661))


def test_actual_wss_worker_mailbox_loss_and_same_generation_recovery(tmp_path):
    """Actual TLS WSS + authenticated owner IPC, using JPEG bytes at the capture boundary."""
    import asyncio
    import ssl
    import subprocess

    from aiohttp import web

    from tests.robots.am1_session_fixture import OPENSSL
    from tools.am1_observation_worker import ObservationWorker
    from tools.am1_session_ipc import OwnerServer

    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    subprocess.run(
        [
            OPENSSL,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )

    async def scenario():
        m = module()
        identity = {k: request()[k] for k in m.SOURCE_KEYS}
        box = m.ObservationMailbox(identity)
        owner = OwnerServer(
            tmp_path / "owner", PiExecutor(SimulatedIO(), tmp_path / "admission", observation=box)
        )
        await owner.start()
        requests = []
        sequence = 0
        binding = None

        async def source(r):
            nonlocal sequence, binding
            assert r.headers["Authorization"] == "Bearer " + ("a" * 64)
            ws = web.WebSocketResponse()
            await ws.prepare(r)
            async for message in ws:
                data = __import__("json").loads(message.data)
                if data["op"] == "start":
                    binding = data["request"]
                    requests.append(binding)
                if data["op"] == "challenge":
                    sequence += 1
                    await asyncio.sleep(0.015)
                    await ws.send_json(frame(binding, data["nonce"], sequence))
            return ws

        app = web.Application()
        app.router.add_get("/observer", source)
        runner = web.AppRunner(app)
        await runner.setup()
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert, key)
        site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=tls)
        await site.start()
        worker = ObservationWorker(
            {
                "owner_state": str(tmp_path / "owner"),
                "state": str(tmp_path / "worker"),
                "url": f"wss://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/observer",
                "ca": str(cert),
                "token": "a" * 64,
            }
        )
        task = asyncio.create_task(worker.run())

        async def until(predicate, seconds=5):
            async with asyncio.timeout(seconds):
                while not predicate():
                    if task.done():
                        task.result()
                    await asyncio.sleep(0.05)

        try:
            result = start(owner.authority, "sim-arm-smoke-repeat")
            assert result["accepted"]
            await until(lambda: owner.authority.snapshot()["run"]["status"] == "running")
            before = owner.authority.snapshot()
            generation = box.request["generation"]
            pause = tmp_path / "worker" / "pause-delivery"
            pause.touch()
            await until(lambda: owner.authority.snapshot()["run"]["status"] == "recovering")
            held = owner.authority.snapshot()["run"]["progress_s"]
            await asyncio.sleep(0.2)
            assert owner.authority.snapshot()["run"]["progress_s"] == held
            pause.unlink()
            await until(lambda: owner.authority.snapshot()["run"]["status"] == "running")
            after = owner.authority.snapshot()
            assert after["run"]["run_id"] == before["run"]["run_id"]
            assert after["run"]["deadline"] == before["run"]["deadline"]
            assert after["run"]["recovery_episodes"] == 1
            assert after["evidence"]["observation_provenance"] == "real-p1/pi-decoded"
            assert {v["generation"] for v in requests} == {generation}
            assert (tmp_path / "worker" / generation / "delivery.jsonl").stat().st_size > 0
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.sleep(0.6)
            assert not owner.authority.snapshot()["evidence"]["required_observation"]
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await owner.close()
            await runner.cleanup()

    asyncio.run(scenario())


def test_new_capture_while_active_and_expired_nonce_fail_closed(tmp_path):
    from tools.am1_p1_observer_agent import CaptureLedger, LatestEncoder

    c = Clock()
    ledger = CaptureLedger(tmp_path / "source", clock=c)
    b = request()
    ledger.start(b)
    with pytest.raises(ValueError, match="release"):
        ledger.start(request())
    r = module().BoundReceiver(tmp_path / "frames", b, clock=c)
    r.issue_challenge("expired", c() * 1000 - 800)
    assert not r.accept_bound(frame(b, "expired", 1), c() * 1000)

    async def bounded():
        encoder = LatestEncoder(
            {"ffmpeg": "never-executed", "rtsp_url": "rtsp://u:p@127.0.0.1:8555/p1-observer"}
        )
        for i in range(50):
            encoder.offer(bytes([i]))
        assert encoder.queue.qsize() == 1 and encoder.dropped == 49
        assert encoder.queue.get_nowait() == bytes([49])
        cmd = encoder.command()
        assert cmd[cmd.index("-bf") + 1] == "0" and cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"

    __import__("asyncio").run(bounded())


def test_p1_adapter_actual_loopback_capture_lifecycle_without_camera(tmp_path, monkeypatch):
    import asyncio
    import json
    import os

    if os.name != "nt":
        pytest.skip("P1 Windows launch context boundary")
    from tools.am1_p1_observer_agent import P1Agent

    async def scenario():
        b = request()
        b["machine"] = os.environ["COMPUTERNAME"]
        spawned = []
        commands = []

        class Process:
            returncode = None

            def __init__(self):
                self.stdout = asyncio.StreamReader()
                self.done = asyncio.Event()

            async def wait(self):
                await self.done.wait()
                return self.returncode

        process = Process()

        async def spawn(*args, **kwargs):
            spawned.append(args)
            assert (
                args[0] == "powershell.exe"
                and "-File" in args
                and kwargs["stdin"] == asyncio.subprocess.DEVNULL
            )
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)

        async def capture(reader, writer):
            while line := await reader.readline():
                command = json.loads(line)
                commands.append(command)
                assert command["token"] == agent.capture_token
                if command["event"] == "frame":
                    writer.write((json.dumps(frame(b, command["nonce"], 1)) + "\n").encode())
                    await writer.drain()
                if command["event"] == "stop":
                    terminal = {
                        "event": "complete",
                        "generation": b["generation"],
                        "success": True,
                        "camera_released": True,
                    }
                    process.stdout.feed_data((json.dumps(terminal) + "\n").encode())
                    process.stdout.feed_eof()
                    process.returncode = 0
                    process.done.set()
                    break
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(capture, "127.0.0.1", 0)
        agent = P1Agent(
            {
                "source": {k: b[k] for k in module().SOURCE_KEYS},
                "state": str(tmp_path / "agent"),
                "token": "a" * 64,
                "capture_port": server.sockets[0].getsockname()[1],
            }
        )
        try:
            await agent.start_capture(b)
            await agent.start_capture(b)
            assert len(spawned) == 1
            async with asyncio.timeout(2):
                while agent.bridge is None:
                    await asyncio.sleep(0.01)
            await agent.local_command("frame", "originalnonce")
            delivered = await asyncio.wait_for(agent.latest.get(), 2)
            assert delivered["binding"] == {k: b[k] for k in module().BINDING_KEYS}
            assert delivered["nonce"] == "originalnonce"
            await agent.local_command("stop")
            await asyncio.wait_for(agent.capture_task, 2)
            assert agent.status()["terminal"]["camera_released"]
            await agent.start_capture(b)
            assert len(spawned) == 1
            assert commands[-1]["event"] == "stop"
        finally:
            await agent.close()
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


@pytest.mark.parametrize("proof_delay", [9.0, 18.0])
def test_source_wait_defers_boundary_acquisition_but_never_original_preparation_deadline(
    tmp_path, proof_delay
):
    m = module()
    c = Clock()
    source = request()
    box = m.ObservationMailbox({key: source[key] for key in m.SOURCE_KEYS}, clock=c)
    io = SimulatedIO(c)
    executor = PiExecutor(io, tmp_path / "admission", clock=c, observation=box)
    authority = SessionAuthority(tmp_path / "owner", clock=c, executor=executor)
    try:
        result = start(authority, "sim-arm-smoke-repeat")
        task = executor.task
        admission = executor.admission
        original_reference = dict(task.provider.origin)
        original_admitted_at = task.admitted_at
        original_deadline = authority.run["deadline"]
        for _ in range(round(proof_delay * 10)):
            c.advance(0.1)
            authority.tick()
        assert authority.run["status"] == "preparing" and authority.run["progress_s"] == 0
        r = m.BoundReceiver(tmp_path / "frames", box.request, clock=c)
        for seq in range(1, 66):
            c.advance(0.1)
            box.publish(feed(r, box.request, c, seq))
            authority.tick()
            if authority.run["status"] == "faulted":
                break
        assert executor.task is task
        assert task.admitted_at == original_admitted_at
        assert authority.run["run_id"] == result["run_id"] and authority.run["deadline"] == original_deadline
        assert task.admissions == 1
        if proof_delay == 9:
            assert authority.run["status"] == "running", authority.run["first_cause"]
            assert authority.run["progress_s"] > 0
            assert task.original_seed == original_reference and executor.admission is admission
        else:
            assert authority.run["status"] == "faulted"
            assert (
                authority.run["first_cause"]
                == "20-second preparation wall deadline expired without a qualified seed"
            )
            assert 20 <= c() - original_admitted_at < 20.2
            assert task.original_seed is None
    finally:
        authority.close()
