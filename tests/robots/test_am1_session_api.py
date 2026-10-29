"""Boundary regressions use real IPC, independent fake processes and verified TLS."""

import asyncio
import json
import ssl
import time
import uuid

import pytest
from aiohttp import ClientSession, CookieJar, TCPConnector, WSMsgType

from tests.robots.am1_session_fixture import Cluster


def oid():
    return str(uuid.uuid4())


@pytest.fixture
def cluster(tmp_path):
    value = Cluster(tmp_path)
    try:
        yield value
    finally:
        value.close()


async def enrolled(cluster, control=True):
    tls = ssl.create_default_context(cafile=str(cluster.cert))
    session = ClientSession(cookie_jar=CookieJar(unsafe=True))
    session._test_tls = tls
    session._test_origin = cluster.url
    async with session.post(
        cluster.url + "/api/enroll",
        json={"code": cluster.pair(control)},
        ssl=tls,
        headers={"Origin": cluster.url},
    ) as response:
        assert response.status == 200
        data = await response.json()
    session._test_csrf = data["csrf"]
    session._test_device = data["device_id"]
    return session


async def post(session, cluster, path, data, **kwargs):
    headers = {"Origin": cluster.url, "X-AM1-CSRF": session._test_csrf}
    headers.update(kwargs.pop("headers", {}))
    async with session.post(
        cluster.url + path, json=data, headers=headers, ssl=session._test_tls, **kwargs
    ) as response:
        return response.status, await response.json()


async def snapshot(session, cluster):
    async with session.get(cluster.url + "/api/state", ssl=session._test_tls) as response:
        assert response.status == 200
        return (await response.json())["snapshot"]


def test_verified_tls_auth_and_mutation_boundary(cluster):
    # Removing capability, Origin, CSRF, strict JSON or body bounds admits unauthorized mutation.
    async def scenario():
        a = await enrolled(cluster)
        v = await enrolled(cluster, False)
        try:
            assert (await post(a, cluster, "/api/claim", {}))[1]["accepted"]
            assert (await post(v, cluster, "/api/claim", {}))[0] == 403
            assert (await post(a, cluster, "/api/claim", {}, headers={"Origin": "https://evil.invalid"}))[
                0
            ] == 403
            assert (await post(a, cluster, "/api/claim", {}, headers={"X-AM1-CSRF": "wrong"}))[0] == 403
            for body in ['{"op":"claim","op":"stop"}', '{"value":NaN}', "x" * 5000]:
                async with a.post(
                    cluster.url + "/api/claim",
                    data=body,
                    ssl=a._test_tls,
                    headers={
                        "Origin": cluster.url,
                        "X-AM1-CSRF": a._test_csrf,
                        "Content-Type": "application/json",
                    },
                ) as r:
                    assert r.status in (400, 413)
            async with a.post(
                cluster.url + "/api/claim",
                data="{}",
                ssl=a._test_tls,
                headers={"Origin": cluster.url, "X-AM1-CSRF": a._test_csrf, "Content-Type": "text/plain"},
            ) as r:
                assert r.status == 415
            async with (
                ClientSession() as outsider,
                outsider.get(cluster.url + "/api/state", ssl=a._test_tls) as r,
            ):
                assert r.status == 401
        finally:
            await a.close()
            await v.close()

    asyncio.run(scenario())


def test_finite_dedup_spectator_and_gateway_restart(cluster):
    # A gateway-owned cadence or spectator connect would stop/replace this exact run.
    async def scenario():
        a, b = await enrolled(cluster), await enrolled(cluster)
        try:
            claim = (await post(a, cluster, "/api/claim", {}))[1]
            command = {
                "operation_id": oid(),
                "recipe": "fake-finite",
                "controller_generation": claim["controller_generation"],
            }
            first = (await post(a, cluster, "/api/start", command))[1]
            retry = (await post(a, cluster, "/api/start", command))[1]
            assert first["accepted"] and retry["run_id"] == first["run_id"]
            before = await snapshot(a, cluster)
            assert (await post(b, cluster, "/api/attach", {}))[1]["snapshot"]["run"]["run_id"] == first[
                "run_id"
            ]
            assert (await snapshot(b, cluster))["controller"]["device_id"] == a._test_device
            cluster.stop("gateway")
            await asyncio.sleep(0.4)
            cluster.start_gateway()
            after = await snapshot(a, cluster)
            assert after["run"]["progress_s"] > before["run"]["progress_s"] + 0.2
            assert after["run"]["deadline"] == before["run"]["deadline"]
            assert after["run"]["seed"] == before["run"]["seed"]
            assert cluster.owner.pid != cluster.gateway.pid
        finally:
            await a.close()
            await b.close()

    asyncio.run(scenario())


def test_websocket_ticket_binding_acknowledgement_and_revoke(cluster):
    # Ticket-only admission or trusting a body device_id would let stale/foreign input run.
    async def scenario():
        a, b = await enrolled(cluster), await enrolled(cluster)
        try:
            attached = (await post(a, cluster, "/api/attach", {}))[1]
            ws = await b.ws_connect(
                cluster.url.replace("https:", "wss:") + "/api/ws",
                ssl=b._test_tls,
                headers={"Origin": cluster.url},
            )
            await ws.send_json({"ticket": attached["ticket"]})
            assert (await ws.receive(timeout=2)).type in (WSMsgType.CLOSE, WSMsgType.CLOSED)
            await ws.close()
            ws = await a.ws_connect(
                cluster.url.replace("https:", "wss:") + "/api/ws",
                ssl=a._test_tls,
                headers={"Origin": cluster.url},
            )
            await ws.send_json({"ticket": attached["ticket"]})
            state = await ws.receive_json(timeout=2)
            assert state["kind"] == "snapshot"
            await ws.send_json({"id": "bad", "command": {"op": "claim"}})
            # Unacknowledged snapshots may repeat before the command result.
            # Do not ACK them: this assertion specifically checks missing-ACK refusal.
            async with asyncio.timeout(2):
                while True:
                    result = await ws.receive_json(timeout=2)
                    if result.get("id") == "bad":
                        break
            assert result["accepted"] is False
            await ws.send_json({"ack": state["revision"]})
            await ws.send_json({"id": "claim", "command": {"op": "claim"}})
            while True:
                result = await ws.receive_json(timeout=2)
                if result.get("id") == "claim":
                    break
            assert result["accepted"]
            assert (await post(a, cluster, "/api/revoke", {}))[1]["accepted"]
            assert (await post(a, cluster, "/api/claim", {}))[0] == 401
            assert (await snapshot(b, cluster))["controller"] is None
            await ws.close()
        finally:
            await a.close()
            await b.close()

    asyncio.run(scenario())


def test_four_attached_clients_and_closed_slot_cleanup(cluster):
    # Counting verified peers twice incorrectly refuses the third enrolled spectator.
    async def scenario():
        session = await enrolled(cluster)
        sockets = []
        try:
            for _ in range(4):
                ticket = (await post(session, cluster, "/api/attach", {}))[1]["ticket"]
                ws = await session.ws_connect(
                    cluster.url.replace("https:", "wss:") + "/api/ws",
                    ssl=session._test_tls,
                    headers={"Origin": cluster.url},
                )
                await ws.send_json({"ticket": ticket})
                assert (await ws.receive_json(timeout=2))["kind"] == "snapshot"
                sockets.append(ws)
            ticket = (await post(session, cluster, "/api/attach", {}))[1]["ticket"]
            with pytest.raises(Exception) as caught:
                await session.ws_connect(
                    cluster.url.replace("https:", "wss:") + "/api/ws",
                    ssl=session._test_tls,
                    headers={"Origin": cluster.url},
                )
            assert caught.value.status == 429
            await sockets.pop().close()
            await asyncio.sleep(0.15)
            ws = await session.ws_connect(
                cluster.url.replace("https:", "wss:") + "/api/ws",
                ssl=session._test_tls,
                headers={"Origin": cluster.url},
            )
            await ws.send_json({"ticket": ticket})
            assert (await ws.receive_json(timeout=2))["kind"] == "snapshot"
            sockets.append(ws)
        finally:
            for ws in sockets:
                await ws.close()
            await session.close()

    asyncio.run(scenario())


def test_private_existing_windows_dacl_drops_explicit_broad_grants(tmp_path):
    # Merely removing inheritance leaves a pre-existing explicit Everyone access grant.
    import os
    import subprocess

    if os.name != "nt":
        pytest.skip("Windows DACL regression")
    from tools.am1_session_ipc import private_directory

    directory = tmp_path / "dedicated-fake-state"
    directory.mkdir()
    subprocess.run(
        ["icacls", str(directory), "/grant", "*S-1-1-0:(OI)(CI)F"], check=True, capture_output=True
    )
    private_directory(directory)
    result = subprocess.check_output(["icacls", str(directory)], text=True)
    assert "Everyone:" not in result


def test_strict_json_rejects_overflow_number():
    # parse_constant alone accepts exponent overflow as infinity.
    from tools.am1_session_ipc import strict_json

    with pytest.raises(ValueError):
        strict_json(b'{"value":1e999}')


async def ws_attached(session, cluster):
    result = (await post(session, cluster, "/api/attach", {}))[1]
    ws = await session.ws_connect(
        cluster.url.replace("https:", "wss:") + "/api/ws",
        ssl=session._test_tls,
        headers={"Origin": cluster.url},
    )
    await ws.send_json({"ticket": result["ticket"]})
    current = await ws.receive_json(timeout=2)
    await ws.send_json({"ack": current["revision"]})
    return ws, current["snapshot"]


async def ws_command(ws, command):
    correlation = oid()
    await ws.send_json({"id": correlation, "command": command})
    while True:
        reply = await ws.receive_json(timeout=2)
        if reply.get("id") == correlation:
            return reply


@pytest.mark.parametrize("recipe,joint,target", [
    ("physical-live-left-elbow", "arm_left_elbow_flex.pos", -0.3),
    ("physical-live-right-elbow", "arm_right_elbow_flex.pos", -0.3),
])
def test_physical_live_wss_close_releases_selected_run_before_sample_expiry(cluster, tmp_path, recipe, joint, target):
    from types import SimpleNamespace

    from aiohttp import web

    from tests.robots.test_am1_live_native import ExecutorNativeHost
    from tests.robots.test_am1_physical_executor import executor
    from tools.am1_session_ipc import OwnerServer
    from tools.am1_session_service import Gateway

    host = ExecutorNativeHost()
    e = executor(host)
    owner = OwnerServer(tmp_path / "physical-authority", executor=e)
    authority = owner.authority

    async def scenario():
        await owner.start()
        gateway = Gateway(owner.directory, tmp_path / "physical-gateway-auth")
        runner = web.AppRunner(gateway.app)
        server_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_tls.load_cert_chain(cluster.cert, cluster.key)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=server_tls)
        await site.start()
        gateway.origin = "https://127.0.0.1:" + str(site._server.sockets[0].getsockname()[1])
        local = SimpleNamespace(url=gateway.origin, cert=cluster.cert, pair=gateway.auth.pair)
        session = await enrolled(local)
        ws = None
        try:
            claim = (await post(session, local, "/api/claim", {}))[1]
            started = (await post(session, local, "/api/start", dict(
                recipe=recipe, operation_id=oid(), controller_generation=claim["controller_generation"]
            )))[1]
            assert started["accepted"]

            async def wait_run(status):
                ceiling = time.monotonic() + 2
                while time.monotonic() < ceiling:
                    authority.tick()
                    current = authority.snapshot()
                    if current["run"]["status"] == status:
                        return current
                    await asyncio.sleep(.01)
                assert False, authority.snapshot()

            current = await wait_run("paused")
            ws, current = await ws_attached(session, local)
            base = dict(run_id=started["run_id"], service_incarnation=current["service_incarnation"],
                        controller_generation=claim["controller_generation"],
                        host_incarnation=current["evidence"]["host_incarnation"],
                        intent_revision=current["run"]["intent_revision"])
            connected = await ws_command(ws, dict(op="connect", **base))
            assert connected["accepted"]
            base["connection_generation"] = connected["connection_generation"]
            assert (await ws_command(ws, dict(op="release_input", **base)))["accepted"]
            ceiling = time.monotonic() + 2
            while not authority.snapshot()["evidence"]["pose_aligned"] and time.monotonic() < ceiling:
                await asyncio.sleep(.01)
            assert (await ws_command(ws, dict(op="resume", **base, operation_id=oid(),
                expected_intent_revision=authority.run["intent_revision"])))["accepted"]
            current = await wait_run("running")
            base["intent_revision"] = current["run"]["intent_revision"]
            grant = await ws_command(ws, dict(op="grant", **base))
            assert (await ws_command(ws, dict(op="input", **base, grant_id=grant["grant_id"],
                                             seq=1, target={joint: current["run"]["live_input"]["cursor_norm"] + target})))["accepted"]
            await ws.close()
            ws = None
            ceiling = min(time.monotonic() + .1, grant["expires"])
            while authority.run["status"] != "paused" and time.monotonic() < ceiling:
                await asyncio.sleep(.005)
            assert time.monotonic() < grant["expires"]
            assert authority.run["status"] == "paused" and authority.run["intent"] is None
            assert authority.events[-1]["kind"] == "transport_connection_lost"
        finally:
            if ws:
                await ws.close()
            await session.close()
            await runner.cleanup()
            await owner.close()

    try:
        asyncio.run(scenario())
    finally:
        if not authority.closed:
            authority.close()
        if e.worker:
            e.worker.join(2)


def test_interactive_expiry_reconnect_and_enrolled_handoff_fences(cluster):
    # Missing WSS generation, grant expiry or enrolled-target fences replays stale movement.
    async def scenario():
        a, b, v = await enrolled(cluster), await enrolled(cluster), await enrolled(cluster, False)
        wa = wb = None
        try:
            claim = (await post(a, cluster, "/api/claim", {}))[1]
            start = (
                await post(
                    a,
                    cluster,
                    "/api/start",
                    {
                        "operation_id": oid(),
                        "recipe": "fake-interactive",
                        "controller_generation": claim["controller_generation"],
                    },
                )
            )[1]
            assert start["accepted"] and start["effect_admitted"] is False
            wa, state = await ws_attached(a, cluster)
            connection = await ws_command(wa, {"op": "connect", "run_id": start["run_id"]})
            ids = {
                "run_id": start["run_id"],
                "service_incarnation": state["service_incarnation"],
                "controller_generation": claim["controller_generation"],
                "connection_generation": connection["connection_generation"],
            }
            assert (
                await ws_command(
                    wa,
                    {
                        "op": "resume",
                        **ids,
                        "expected_intent_revision": (await snapshot(a, cluster))["run"]["intent_revision"],
                        "operation_id": oid(),
                    },
                )
            )["accepted"] is False
            assert (await ws_command(wa, {"op": "release_input", **ids}))["accepted"]
            assert (
                await ws_command(
                    wa,
                    {
                        "op": "resume",
                        **ids,
                        "expected_intent_revision": (await snapshot(a, cluster))["run"]["intent_revision"],
                        "operation_id": oid(),
                    },
                )
            )["effect_admitted"]
            grant = await ws_command(wa, {"op": "grant", **ids})
            assert (
                await ws_command(
                    wa, {"op": "input", **ids, "grant_id": grant["grant_id"], "seq": 1, "target": [0.5, 0, 0]}
                )
            )["effect_admitted"]
            await asyncio.sleep(0.3)
            assert (await snapshot(a, cluster))["run"]["intent"] is None
            assert not (
                await ws_command(
                    wa, {"op": "input", **ids, "grant_id": grant["grant_id"], "seq": 2, "target": [0.5, 0, 0]}
                )
            )["accepted"]
            await wa.close()
            wa, state = await ws_attached(a, cluster)
            assert not (
                await ws_command(
                    wa, {"op": "input", **ids, "grant_id": grant["grant_id"], "seq": 3, "target": [0.5, 0, 0]}
                )
            )["accepted"]
            claim = (await post(a, cluster, "/api/claim", {}))[1]
            assert (
                await post(
                    a, cluster, "/api/handoff", {"operation_id": oid(), "target_device_id": v._test_device}
                )
            )[0] == 403
            assert (
                await post(
                    a, cluster, "/api/handoff", {"operation_id": oid(), "target_device_id": b._test_device}
                )
            )[1]["accepted"]
            assert not (await ws_command(wa, {"op": "release_input", **ids}))["accepted"]
            assert (await snapshot(b, cluster))["controller"]["device_id"] == b._test_device
            assert (await snapshot(b, cluster))["run"]["status"] == "paused"
            assert (await post(b, cluster, "/api/stop", {"run_id": start["run_id"], "operation_id": oid()}))[
                1
            ]["accepted"]
        finally:
            for ws in (wa, wb):
                if ws:
                    await ws.close()
            for session in (a, b, v):
                await session.close()

    asyncio.run(scenario())


def test_owner_restart_reports_uncertain_and_real_os_lock_contends(cluster):
    # Losing persisted operation identity or acquiring the same lock dispatches a duplicate run.
    async def scenario():
        a = await enrolled(cluster)
        try:
            claim = (await post(a, cluster, "/api/claim", {}))[1]
            command = {
                "operation_id": oid(),
                "recipe": "fake-finite",
                "controller_generation": claim["controller_generation"],
            }
            started = (await post(a, cluster, "/api/start", command))[1]
            before = await snapshot(a, cluster)
            contender = cluster.spawn(
                ["-m", "tools.am1_session_ipc", "owner", "--state", str(cluster.owner_dir)]
            )
            try:
                contender.wait(timeout=3)
                assert contender.returncode != 0
                assert "namespace owner already active" in contender.stderr.read()
            finally:
                contender.stderr.close()
            old_pid = cluster.owner.pid
            cluster.stop("owner")
            cluster.start_owner()
            after = await snapshot(a, cluster)
            assert cluster.owner.pid != old_pid
            assert after["service_incarnation"] != before["service_incarnation"]
            assert after["run"]["run_id"] == started["run_id"]
            assert after["run"]["status"] == "interrupted"
            assert after["run"]["uncertain"] and after["run"]["deadline"] is None
            retry = (await post(a, cluster, "/api/start", command))[1]
            assert retry["run_id"] == started["run_id"] and retry["uncertain"]
            await asyncio.sleep(0.2)
            assert (await snapshot(a, cluster))["run"]["progress_s"] == after["run"]["progress_s"]
        finally:
            await a.close()

    asyncio.run(scenario())


def test_partial_ipc_frames_cannot_starve_owner_cadence_or_reserved_stop(cluster):
    # A single listener/semaphore or IPC-owned tick delays Stop behind incomplete normal clients.
    async def scenario():
        import struct

        a = await enrolled(cluster)
        stalled = []
        try:
            claim = (await post(a, cluster, "/api/claim", {}))[1]
            started = (
                await post(
                    a,
                    cluster,
                    "/api/start",
                    {
                        "operation_id": oid(),
                        "recipe": "fake-finite",
                        "controller_generation": claim["controller_generation"],
                    },
                )
            )[1]
            before = await snapshot(a, cluster)
            metadata = json.loads((cluster.owner_dir / "ipc.json").read_text())
            lane = metadata["lanes"]["False"]
            for _ in range(12):
                if "port" in lane:
                    reader, writer = await asyncio.open_connection("127.0.0.1", lane["port"])
                else:
                    reader, writer = await asyncio.open_unix_connection(lane["socket"])
                writer.write(struct.pack("!I", 4096) + b"{")
                await writer.drain()
                stalled.append((reader, writer))
            await asyncio.sleep(0.25)
            begin = time.monotonic()
            result = (
                await post(a, cluster, "/api/stop", {"run_id": started["run_id"], "operation_id": oid()})
            )[1]
            assert time.monotonic() - begin < 0.5 and result["accepted"]
            await asyncio.sleep(0.85)
            for reader, _ in stalled:
                assert await asyncio.wait_for(reader.read(1), 0.2) == b""
            after = await snapshot(a, cluster)
            assert after["run"]["status"] == "stopped"
            assert after["run"]["progress_s"] > before["run"]["progress_s"] + 0.1
            # Oversize/auth frames close before dispatch/protected response.
            for length, body in [(500000, b""), (1, b"{")]:
                if "port" in lane:
                    reader, writer = await asyncio.open_connection("127.0.0.1", lane["port"])
                else:
                    reader, writer = await asyncio.open_unix_connection(lane["socket"])
                writer.write(struct.pack("!I", length) + body)
                await writer.drain()
                assert await asyncio.wait_for(reader.read(1), 1.5) == b""
                writer.close()
                await writer.wait_closed()
        finally:
            for _, writer in stalled:
                writer.close()
                await writer.wait_closed()
            await a.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("controller", [None, {"device_id": "revoked"}, {"device_id": "new-owner"}])
def test_revocation_dispatches_fence_after_controller_expiry(controller):
    # Durable same-operation intent must be cancelled even after its lease expires.
    # Core, rather than a stale gateway snapshot, fences a different current owner.
    from tools.am1_session_service import Gateway

    async def scenario():
        gateway = object.__new__(Gateway)
        gateway.tickets = {"old": ("revoked", 100), "other": ("new-owner", 100)}
        gateway.peers = []
        calls = []

        async def current():
            return {"controller": controller}

        async def call(command, device):
            calls.append((command, device))
            return {"accepted": controller is None or controller["device_id"] == device}

        gateway.current, gateway.call = current, call
        await gateway.fence("revoked")
        assert len(calls) == 1
        command, device = calls[0]
        assert command["op"] == "release" and device == "revoked"
        assert uuid.UUID(command["operation_id"]).version == 4
        assert gateway.tickets == {"other": ("new-owner", 100)}

    asyncio.run(scenario())


@pytest.mark.parametrize("controller", [None, {"device_id": "new-owner"}])
def test_local_revocation_discovers_expired_disconnected_bench_intent(controller):
    from types import SimpleNamespace

    from tools.am1_session_service import Gateway

    async def scenario():
        gateway = object.__new__(Gateway)
        gateway.tickets, gateway.peers = {}, []
        gateway.auth = SimpleNamespace(capable=lambda device: device == "new-owner")
        state = {"controller": controller, "run": {"control_intent": {"eligible": True, "device_id": "revoked"}}}
        fenced, calls = asyncio.Event(), []

        async def current():
            return state

        async def call(command, device):
            calls.append((command, device))
            state["run"]["control_intent"]["eligible"] = False
            fenced.set()
            return {"accepted": True}

        gateway.current, gateway.call = current, call
        task = asyncio.create_task(gateway.publish())
        try:
            await asyncio.wait_for(fenced.wait(), .6)
            assert len(calls) == 1 and calls[0][0]["op"] == "release" and calls[0][1] == "revoked"
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_local_revocation_without_websocket_fences_controller(cluster):
    # Checking revoked peers only leaves a controller lease usable after local revocation.
    async def scenario():
        from tools.am1_session_service import AuthStore

        a, b = await enrolled(cluster), await enrolled(cluster)
        try:
            assert (await post(a, cluster, "/api/claim", {}))[1]["accepted"]
            AuthStore(cluster.auth_dir).revoke(a._test_device)
            await asyncio.sleep(0.25)
            assert (await snapshot(b, cluster))["controller"] is None
        finally:
            await a.close()
            await b.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("protective", ["pause", "stop"])
def test_private_fake_proof_loss_and_operator_override(cluster, protective):
    # Treating optional quality as required freezes work; auto recovery must not override an operator action.
    async def scenario():
        a, b = await enrolled(cluster), await enrolled(cluster)

        def flags(**values):
            temporary = cluster.flags.with_suffix(".new")
            temporary.write_text(json.dumps(values))
            temporary.replace(cluster.flags)

        try:
            claim = (await post(a, cluster, "/api/claim", {}))[1]
            started = (
                await post(
                    a,
                    cluster,
                    "/api/start",
                    {
                        "operation_id": oid(),
                        "recipe": "fake-finite",
                        "controller_generation": claim["controller_generation"],
                    },
                )
            )[1]
            before = await snapshot(a, cluster)
            flags(optional_quality=False)
            await asyncio.sleep(0.25)
            optional = await snapshot(a, cluster)
            assert optional["evidence"]["optional_quality"] is False
            assert optional["run"]["status"] == "running"
            assert optional["run"]["progress_s"] > before["run"]["progress_s"] + 0.1
            flags(required_observation=False)
            await asyncio.sleep(0.15)
            missing = await snapshot(a, cluster)
            assert missing["run"]["status"] == "recovering"
            await asyncio.sleep(0.15)
            assert (await snapshot(a, cluster))["run"]["progress_s"] == missing["run"]["progress_s"]
            assert (
                await post(
                    b, cluster, "/api/" + protective, {"run_id": started["run_id"], "operation_id": oid()}
                )
            )[1]["accepted"]
            flags(required_observation=True, optional_quality=True)
            await asyncio.sleep(0.2)
            after = await snapshot(a, cluster)
            assert after["run"]["status"] == ("paused" if protective == "pause" else "stopped")
            assert after["run"]["progress_s"] == missing["run"]["progress_s"]
            assert after["run"]["deadline"] == before["run"]["deadline"]
            assert after["run"]["first_cause"] == missing["run"]["first_cause"]
        finally:
            await a.close()
            await b.close()

    asyncio.run(scenario())


def test_nonreading_websocket_cannot_delay_ticks_or_protective_stop(cluster):
    # An awaited fanout write or unbounded output queue stalls owner progress/Stop behind a slow subscriber.
    async def scenario():
        import socket

        from tools.am1_session_ipc import IPCClient

        a = await enrolled(cluster)
        slow = None
        extra_sockets = []
        slow_session = None
        try:
            claim = (await post(a, cluster, "/api/claim", {}))[1]
            started = (
                await post(
                    a,
                    cluster,
                    "/api/start",
                    {
                        "operation_id": oid(),
                        "recipe": "fake-finite",
                        "controller_generation": claim["controller_generation"],
                    },
                )
            )[1]
            ipc = IPCClient(cluster.owner_dir)
            # Fill the bounded historical tail through actual private IPC, then detach input ownership.
            for _ in range(36):
                await ipc.request({"op": "claim"}, a._test_device)
                await ipc.request({"op": "release", "operation_id": oid()}, a._test_device)

            def small_receive_socket(address):
                sock = socket.socket(address[0], address[1], address[2])
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
                return sock

            slow_session = ClientSession(
                cookie_jar=a.cookie_jar,
                connector=TCPConnector(socket_factory=small_receive_socket, force_close=True),
            )
            slow_session._test_tls = a._test_tls
            slow_session._test_csrf = a._test_csrf
            slow, before = await ws_attached(slow_session, cluster)
            assert len(before["events"]) == 64
            transport = slow._response.connection.transport
            transport.get_extra_info("socket").setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
            raw_transport = transport._ssl_protocol._transport
            transport.pause_reading()
            raw_transport.pause_reading()  # Stop actual kernel reads as well as decrypted delivery.
            for _ in range(20):
                if cluster.flags.with_suffix(".backpressure").exists():
                    break
                await asyncio.sleep(0.1)
            assert cluster.flags.with_suffix(".backpressure").exists(), (
                "real bounded writer/queue must hit backpressure"
            )
            await asyncio.sleep(
                0.65
            )  # Overall close deadline must reclaim capacity even when Close cannot drain.
            assert cluster.flags.with_suffix(".closed").read_text() == "True", (
                "bounded close must abort even if cancelled during cleanup"
            )
            live = await snapshot(a, cluster)
            assert live["run"]["progress_s"] > before["run"]["progress_s"] + 0.8
            began = time.monotonic()
            stopped = (
                await post(a, cluster, "/api/stop", {"run_id": started["run_id"], "operation_id": oid()})
            )[1]
            stop_latency = time.monotonic() - began
            assert stopped["accepted"] and stop_latency < 0.5
            assert (await snapshot(a, cluster))["run"]["cleanup"] == "held_body_zero"
            # The saturated peer's slot must be freed while it is still not reading.
            for _ in range(4):
                fresh, _ = await ws_attached(a, cluster)
                extra_sockets.append(fresh)
            transport.get_extra_info("socket").setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
            transport.resume_reading()
            raw_transport.resume_reading()
            async with asyncio.timeout(2):
                while not slow.closed:
                    await slow.receive()
            assert slow.close_code in (1013, 1006), "blocked Close delivery may require bounded TCP abort"
            print(
                json.dumps(
                    {
                        "real_backpressure": True,
                        "reclaimed_clients": len(extra_sockets),
                        "client_close_code": slow.close_code,
                        "stop_latency_s": stop_latency,
                        "owner_progress_delta_s": live["run"]["progress_s"] - before["run"]["progress_s"],
                    }
                )
            )
        finally:
            if slow:
                transport.resume_reading()
                raw_transport.resume_reading()
                await slow.close()
            for extra in extra_sockets:
                await extra.close()
            if slow_session:
                await slow_session.close()
            await a.close()

    asyncio.run(scenario())


def test_ticket_single_use_expiry_and_no_output_before_verification(cluster):
    # Cookie+Origin alone must not expose protected snapshots; an expired or reused ticket cannot attach.
    async def scenario():
        a = await enrolled(cluster)
        sockets = []
        try:
            ws = await a.ws_connect(
                cluster.url.replace("https:", "wss:") + "/api/ws",
                ssl=a._test_tls,
                headers={"Origin": cluster.url},
            )
            sockets.append(ws)
            with pytest.raises(asyncio.TimeoutError):
                await ws.receive(timeout=0.1)
            await ws.close()
            attached = (await post(a, cluster, "/api/attach", {}))[1]
            for index in range(2):
                ws = await a.ws_connect(
                    cluster.url.replace("https:", "wss:") + "/api/ws",
                    ssl=a._test_tls,
                    headers={"Origin": cluster.url},
                )
                sockets.append(ws)
                await ws.send_json({"ticket": attached["ticket"]})
                msg = await ws.receive(timeout=2)
                assert (msg.type == WSMsgType.TEXT) == (index == 0)
                await ws.close()
            attached = (await post(a, cluster, "/api/attach", {}))[1]
            await asyncio.sleep(5.1)
            ws = await a.ws_connect(
                cluster.url.replace("https:", "wss:") + "/api/ws",
                ssl=a._test_tls,
                headers={"Origin": cluster.url},
            )
            sockets.append(ws)
            await ws.send_json({"ticket": attached["ticket"]})
            assert (await ws.receive(timeout=2)).type in (WSMsgType.CLOSE, WSMsgType.CLOSED)
        finally:
            for ws in sockets:
                await ws.close()
            await a.close()

    asyncio.run(scenario())


def test_wss_stop_has_reserved_admission_during_delayed_input_reply(cluster):
    # Sequential receive/dispatch places Stop behind a prior input's slow reply.
    async def scenario():
        a = await enrolled(cluster)
        ws = None
        try:
            claim = (await post(a, cluster, "/api/claim", {}))[1]
            started = (
                await post(
                    a,
                    cluster,
                    "/api/start",
                    {
                        "operation_id": oid(),
                        "recipe": "fake-interactive",
                        "controller_generation": claim["controller_generation"],
                    },
                )
            )[1]
            ws, state = await ws_attached(a, cluster)
            connection = await ws_command(ws, {"op": "connect", "run_id": started["run_id"]})
            ids = {
                "run_id": started["run_id"],
                "service_incarnation": state["service_incarnation"],
                "controller_generation": claim["controller_generation"],
                "connection_generation": connection["connection_generation"],
            }
            assert (await ws_command(ws, {"op": "release_input", **ids}))["accepted"]
            assert (
                await ws_command(
                    ws,
                    {
                        "op": "resume",
                        **ids,
                        "expected_intent_revision": (await snapshot(a, cluster))["run"]["intent_revision"],
                        "operation_id": oid(),
                    },
                )
            )["accepted"]
            grant = await ws_command(ws, {"op": "grant", **ids})
            cluster.flags.write_text(json.dumps({"slow_input_response": True}))
            await ws.send_json(
                {
                    "id": "delayed-input",
                    "command": {
                        "op": "input",
                        **ids,
                        "grant_id": grant["grant_id"],
                        "seq": 1,
                        "target": [0.5],
                    },
                }
            )
            for _ in range(100):
                if cluster.flags.with_suffix(".admitted").exists():
                    break
                await asyncio.sleep(0.005)
            assert cluster.flags.with_suffix(".admitted").exists()
            began = time.monotonic()
            result = await ws_command(ws, {"op": "stop", "run_id": started["run_id"], "operation_id": oid()})
            assert result["accepted"] and time.monotonic() - began < 0.4
            assert (await snapshot(a, cluster))["run"]["status"] == "stopped"
        finally:
            if ws:
                await ws.close()
            await a.close()

    asyncio.run(scenario())


def test_pending_input_is_coalesced_instead_of_replayed(cluster):
    # A FIFO executes every buffered target; only one latest pending input may survive an in-flight reply.
    async def scenario():
        a = await enrolled(cluster)
        ws = None
        try:
            claim = (await post(a, cluster, "/api/claim", {}))[1]
            run = (
                await post(
                    a,
                    cluster,
                    "/api/start",
                    {
                        "operation_id": oid(),
                        "recipe": "fake-interactive",
                        "controller_generation": claim["controller_generation"],
                    },
                )
            )[1]
            ws, state = await ws_attached(a, cluster)
            connected = await ws_command(ws, {"op": "connect", "run_id": run["run_id"]})
            ids = {
                "run_id": run["run_id"],
                "service_incarnation": state["service_incarnation"],
                "controller_generation": claim["controller_generation"],
                "connection_generation": connected["connection_generation"],
            }
            await ws_command(ws, {"op": "release_input", **ids})
            await ws_command(
                ws,
                {
                    "op": "resume",
                    **ids,
                    "expected_intent_revision": (await snapshot(a, cluster))["run"]["intent_revision"],
                    "operation_id": oid(),
                },
            )
            grant = await ws_command(ws, {"op": "grant", **ids})
            cluster.flags.write_text(json.dumps({"slow_input_response": True}))

            def message(sequence):
                return {
                    "id": str(sequence),
                    "command": {
                        "op": "input",
                        **ids,
                        "grant_id": grant["grant_id"],
                        "seq": sequence,
                        "target": [sequence / 4],
                    },
                }

            await ws.send_json(message(1))
            for _ in range(100):
                if cluster.flags.with_suffix(".admitted").exists():
                    break
                await asyncio.sleep(0.005)
            await ws.send_json(message(2))
            await ws.send_json(message(3))
            results = {}
            while len(results) < 3:
                value = await ws.receive_json(timeout=2)
                if value.get("kind") == "result":
                    results[value["id"]] = value
            assert results["2"]["status"] == "superseded" and not results["2"]["effect_admitted"]
            skipped = results["2"]["diagnostic_timing"]
            assert set(skipped) == {"version", "clock", "gateway_received"}
            assert skipped["version"] == 1 and skipped["clock"] == "pi-monotonic"
            for key in ("1", "3"):
                assert len(results[key]["diagnostic_timing"]) == 10
                assert len(json.dumps(results[key])) <= 1024
            assert not results["3"][
                "accepted"
            ]  # Original grant expires while the first real reply is delayed.
            assert (await snapshot(a, cluster))["run"]["intent"] is None
        finally:
            if ws:
                await ws.close()
            await a.close()

    asyncio.run(scenario())


def test_pairing_is_closed_single_use_atomic_and_payload_identity_cannot_override(cluster):
    # Reusable/concurrently consumed enrollment or payload-supplied identities permit unauthorized control.
    async def scenario():
        tls = ssl.create_default_context(cafile=str(cluster.cert))
        code = cluster.pair(True)

        async def consume():
            async with (
                ClientSession(cookie_jar=CookieJar(unsafe=True)) as session,
                session.post(
                    cluster.url + "/api/enroll", json={"code": code}, ssl=tls, headers={"Origin": cluster.url}
                ) as r,
            ):
                return r.status

        assert sorted(await asyncio.gather(consume(), consume())) == [200, 401]
        async with (
            ClientSession() as unknown,
            unknown.post(
                cluster.url + "/api/enroll",
                json={"code": "closed-pairing"},
                ssl=tls,
                headers={"Origin": cluster.url},
            ) as r,
        ):
            assert r.status == 401
        a, b = await enrolled(cluster), await enrolled(cluster)
        try:
            assert (await post(a, cluster, "/api/claim", {"device_id": b._test_device}))[0] == 400
            assert (await snapshot(a, cluster))["controller"] is None
            async with a.post(
                cluster.url + "/api/claim",
                json={},
                ssl=tls,
                headers={"Origin": cluster.url, "X-AM1-CSRF": a._test_csrf, "Host": "wrong.invalid"},
            ) as r:
                assert r.status == 403
            token = next(iter(a.cookie_jar)).value
            async with (
                ClientSession() as bare,
                bare.post(
                    cluster.url + "/api/claim",
                    json={},
                    ssl=tls,
                    headers={
                        "Origin": cluster.url,
                        "X-AM1-CSRF": a._test_csrf,
                        "Cookie": f"__Host-am1-device={token}; __Host-am1-device=y",
                    },
                ) as r,
            ):
                assert r.status == 401
        finally:
            await a.close()
            await b.close()

    asyncio.run(scenario())


def test_lost_owner_reply_reports_unknown_instead_of_claiming_no_effect(cluster):
    # A timed-out reply can follow actual durable acceptance/effect; False falsely claims refusal.
    async def scenario():
        a = await enrolled(cluster)
        try:
            claim = (await post(a, cluster, "/api/claim", {}))[1]
            operation = oid()
            cluster.flags.write_text(json.dumps({"slow_start_response": True}))
            status, result = await post(
                a,
                cluster,
                "/api/start",
                {
                    "operation_id": operation,
                    "recipe": "fake-finite",
                    "controller_generation": claim["controller_generation"],
                },
            )
            assert status == 503 and result["status"] == "unknown"
            assert result["accepted"] is None and result["effect_admitted"] is None and result["uncertain"]
            state = await snapshot(a, cluster)
            assert state["run"]["operation_id"] == operation and state["run"]["dispatch"] == "acknowledged"
            known = (await post(a, cluster, "/api/lookup", {"operation_id": operation}))[1]
            assert known["accepted"] and known["run_id"] == state["run"]["run_id"]
        finally:
            await a.close()

    asyncio.run(scenario())


def test_private_state_refuses_directory_redirects(tmp_path):
    # A redirected state tree must never change ACLs/files outside the dedicated fake directory.
    import os
    import subprocess

    from tools.am1_session_ipc import private_directory

    target = tmp_path / "outside-state"
    target.mkdir()
    alias = tmp_path / "redirected-state"
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(alias), str(target)], check=True, capture_output=True
        )
    else:
        alias.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError):
        private_directory(alias)


def test_private_state_refuses_nonempty_unrelated_directory(tmp_path):
    # Supplying a shared/config directory must not rewrite its permissions or adopt its files.
    from tools.am1_session_ipc import private_directory

    unrelated = tmp_path / "shared-config"
    unrelated.mkdir()
    preserved = unrelated / "original.txt"
    preserved.write_text("unrelated state")
    with pytest.raises(ValueError):
        private_directory(unrelated)
    assert preserved.read_text() == "unrelated state"


def test_wss_input_diagnostic_span_survives_real_ipc_and_tls(cluster, monkeypatch):
    async def scenario():
        a = await enrolled(cluster)
        ws = None
        try:
            claim = (await post(a, cluster, "/api/claim", {}))[1]
            assert "diagnostic_timing" not in claim
            assert set(claim["timing"]) == {"service_received_at", "service_completed_at"}
            run = (await post(a, cluster, "/api/start", {
                "operation_id": oid(), "recipe": "fake-interactive",
                "controller_generation": claim["controller_generation"]}))[1]
            ws, state = await ws_attached(a, cluster)
            connected = await ws_command(ws, {"op": "connect", "run_id": run["run_id"]})
            ids = {"run_id": run["run_id"], "service_incarnation": state["service_incarnation"],
                   "controller_generation": claim["controller_generation"],
                   "connection_generation": connected["connection_generation"]}
            await ws_command(ws, {"op": "release_input", **ids})
            refused_grant = await ws_command(ws, {"op": "grant", **ids})
            assert refused_grant["accepted"] is False and refused_grant["reason"] == "interactive running required"
            await ws_command(ws, {"op": "resume", **ids, "operation_id": oid(),
                "expected_intent_revision": (await snapshot(a, cluster))["run"]["intent_revision"]})
            grant = await ws_command(ws, {"op": "grant", **ids})
            grant_marks = ("gateway_received", "gateway_dispatch", "gateway_ipc_started", "core_entered",
                           "core_locked", "core_tick_completed", "core_returned", "gateway_ipc_completed",
                           "reply_ready", "reply_send_preparation_started")
            assert set(grant["diagnostic_timing"]) == {"version", "clock", *grant_marks}
            assert [grant["diagnostic_timing"][key] for key in grant_marks] == sorted(
                grant["diagnostic_timing"][key] for key in grant_marks)
            assert grant["expires"] - grant["issued"] == .25
            assert len(json.dumps(grant["diagnostic_timing"])) <= 512 and len(json.dumps(grant)) <= 1024
            assert refused_grant["effect_admitted"] is False
            assert set(refused_grant["diagnostic_timing"]) == set(grant["diagnostic_timing"])
            foreign_grant = await ws_command(ws, {"op": "grant", **dict(ids, connection_generation=oid())})
            assert foreign_grant["accepted"] is False and foreign_grant["effect_admitted"] is False
            assert set(foreign_grant["diagnostic_timing"]) == {
                "version", "clock", "gateway_received", "reply_ready", "reply_send_preparation_started"}
            result = await ws_command(ws, {"op": "input", **ids, "grant_id": grant["grant_id"],
                                          "seq": 1, "target": [.2]})
            assert result["accepted"] and result["effect_admitted"]
            span = result["diagnostic_timing"]
            ordered = ("gateway_received", "gateway_dispatch", "gateway_ipc_started", "core_entered",
                       "core_locked", "core_tick_completed", "core_returned", "gateway_ipc_completed")
            assert set(span) == {"version", "clock", *ordered}
            assert span["version"] == 1 and span["clock"] == "pi-monotonic"
            assert all(type(span[key]) is float and span[key] >= 0 for key in ordered)
            assert [span[key] for key in ordered] == sorted(span[key] for key in ordered)
            assert len(json.dumps(span)) <= 512 and "target" not in span and "device" not in span
            stale = await ws_command(ws, {"op": "input", **ids, "grant_id": oid(), "seq": 2, "target": [.3]})
            assert not stale["accepted"] and stale["reason"] == "expired grant"
            assert set(stale["diagnostic_timing"]) == set(span)
            assert len(json.dumps(result)) <= 1024
            # Exercise the actual PC result consumer with the real refused reply.
            from types import SimpleNamespace
            from tools.am1_leader_adapter import SessionClient, CommandRefusedError, expired_input_refusal
            async def acknowledged(client, socket, ceiling):
                return None
            async def send(value):
                client.pending[value["id"]].set_result(stale)
            client = SimpleNamespace(transport_error=None, reader=None, pending={},
                                     ws=SimpleNamespace(closed=False, send_json=send))
            monkeypatch.setattr(SessionClient, "_wait_current_ack", acknowledged)
            with pytest.raises(CommandRefusedError) as refused:
                await SessionClient.command(client, {"op": "input"})
            assert refused.value.response == stale and expired_input_refusal(refused.value)
            assert refused.value.response["diagnostic_timing"] == stale["diagnostic_timing"]
        finally:
            if ws:
                await ws.close()
            await a.close()
    asyncio.run(scenario())

@pytest.mark.parametrize("cancelled", [False, True])
@pytest.mark.parametrize("op", ["input", "grant"])
def test_input_diagnostic_gateway_failure_does_not_replace_unknown_or_cancellation(monkeypatch, cancelled, op):
    from types import SimpleNamespace
    from tools import am1_session_service as service
    from tools import am1_session_core as core

    async def scenario():
        gateway = service.Gateway.__new__(service.Gateway)
        gateway.auth = SimpleNamespace(resolve=lambda _: {"id": "owner", "control": True})
        outputs = []
        peer = SimpleNamespace(token="private-test-token", ack=True, put=lambda value, **kwargs: outputs.append(value))
        original = asyncio.CancelledError() if cancelled else TimeoutError("original owner timeout")
        async def dispatch(*args):
            raise original
        gateway.dispatch = dispatch
        monkeypatch.setattr(core, "_input_diagnostic_clock", lambda: None)
        value = {"id": "original-request", "command": {"op": op}, "_diagnostic_received": None}
        if cancelled:
            with pytest.raises(asyncio.CancelledError) as raised:
                await gateway.execute_peer(peer, value)
            assert raised.value is original and outputs == []
        else:
            await gateway.execute_peer(peer, value)
            assert outputs == [dict(service.unknown_outcome(), kind="result", id="original-request")]
    asyncio.run(scenario())

@pytest.mark.parametrize("client_timestamp", [False, True])
def test_input_diagnostic_websocket_receive_precedes_auth_parse_and_rejects_client_stamp(monkeypatch, client_timestamp):
    from types import SimpleNamespace
    from tools import am1_session_service as service

    async def scenario():
        clock, resolved, parsed, queued = [100.], [], [], []
        command = {"id": "original-input", "command": {"op": "input"}}
        if client_timestamp:
            command["received_at"] = 1.
        text = json.dumps(command)
        class Socket:
            async def prepare(self, request):
                return None
            async def receive(self):
                return SimpleNamespace(type=WSMsgType.TEXT, data=json.dumps({"ticket": "once"}))
            async def messages(self):
                yield SimpleNamespace(type=WSMsgType.TEXT, data=text)
            def __aiter__(self):
                return self.messages()
        class Peer:
            def __init__(self, socket, device, token):
                self.device, self.token, self.connection = device, token, None
                self.ack, self.latest_input, self.input_task = True, None, object()
            def put(self, value, snapshot=False):
                queued.append(value)
            async def close(self):
                return None
        peer = Peer(None, "owner", "private-test-token")
        def resolve(token):
            resolved.append(clock[0])
            if len(resolved) == 2:
                clock[0] += .2  # Work after TEXT delivery, before envelope parsing.
            return {"id": "owner", "control": True}
        original_json = service.strict_json
        def parse(value):
            parsed.append(clock[0])
            result = original_json(value)
            if value == text:
                clock[0] += .3
            return result
        async def close(*args):
            return None
        gateway = service.Gateway.__new__(service.Gateway)
        gateway.pending_ws, gateway.peers, gateway.revision = 0, set(), 0
        gateway.incarnation = "test-gateway"
        gateway.tickets = {service.digest("once"): ("owner", 200.)}
        gateway.auth = SimpleNamespace(resolve=resolve)
        async def current():
            return {}
        gateway.current = current
        monkeypatch.setattr(service.web, "WebSocketResponse", lambda **kwargs: Socket())
        monkeypatch.setattr(service, "Peer", lambda *args: peer)
        monkeypatch.setattr(service, "strict_json", parse)
        monkeypatch.setattr(service, "close_socket", close)
        monkeypatch.setattr(service, "time", SimpleNamespace(monotonic=lambda: clock[0]))
        monkeypatch.setattr(service, "_input_diagnostic_monotonic", lambda: clock[0])
        transport = SimpleNamespace(set_write_buffer_limits=lambda **kwargs: None,
            get_extra_info=lambda key: SimpleNamespace(setsockopt=lambda *args: None))
        class Request(dict):
            pass
        request = Request(token="private-test-token", device={"id": "owner"})
        request.transport = transport
        await gateway.websocket(request)
        assert len(resolved) == 2 and len(parsed) == 2 and clock[0] == 100.5
        if client_timestamp:
            assert peer.latest_input is None, "external envelope cannot provide a receive timestamp"
        else:
            assert peer.latest_input["_diagnostic_received"] == 100., "receive must precede auth/parse work"
            assert set(peer.latest_input["command"]) == {"op"}
            gateway.enqueue(peer, {"id": "direct-call", "command": {"op": "input"}})
            assert peer.latest_input["_diagnostic_received"] == 100.5, "direct callers keep diagnostic-clock fallback"
    asyncio.run(scenario())

@pytest.mark.parametrize("acked,inflight", [(False, 0), (True, 6), (True, 0)])
def test_grant_receive_anchor_and_early_refusals_stay_server_only(monkeypatch, acked, inflight):
    from types import SimpleNamespace
    from tools import am1_session_service as service

    async def scenario():
        seen, replies = [], []
        gateway = service.Gateway.__new__(service.Gateway)
        async def execute(peer, value, lane):
            seen.append(value)
            peer.commands[lane] -= 1
        gateway.execute_peer = execute
        peer = SimpleNamespace(ack=acked, commands={"normal": inflight, "protective": 0}, tasks=set(),
            put=lambda result, **kwargs: replies.append((result, kwargs)))
        monkeypatch.setattr(service, "_input_diagnostic_monotonic", lambda: 200.)
        envelope = {"id": "grant-request", "command": {"op": "grant"}}
        gateway.enqueue(peer, envelope, received_at=100.)
        await asyncio.gather(*tuple(peer.tasks))
        assert envelope == {"id": "grant-request", "command": {"op": "grant"}}
        if acked and not inflight:
            assert seen[0]["_diagnostic_received"] == 100. and not replies
            gateway.enqueue(peer, envelope)
            await asyncio.gather(*tuple(peer.tasks))
            assert seen[1]["_diagnostic_received"] == 200., "direct server calls use diagnostic fallback"
        else:
            result, kwargs = replies[0]
            assert result["accepted"] is False and result["effect_admitted"] is False
            assert result["diagnostic_timing"] == {"version": 1, "clock": "pi-monotonic",
                                                     "gateway_received": 100., "reply_ready": 200.}
            assert kwargs == {"grant_reply": True} and not seen
    asyncio.run(scenario())


@pytest.mark.parametrize("op", ["input", "grant"])
@pytest.mark.parametrize("phase", ["before-ipc", "ipc"])
def test_partial_unknown_gateway_preserves_only_already_computed_marks(monkeypatch, op, phase):
    from types import SimpleNamespace
    from tools import am1_session_service as service
    from tools import am1_session_core as core

    async def scenario():
        gateway = service.Gateway.__new__(service.Gateway)
        gateway.auth = SimpleNamespace(resolve=lambda _: {"id":"owner", "control":True}, capable=lambda _:True)
        original = TimeoutError("original owner timeout")
        calls, outputs = [], []
        class BeforeIPCFailure:
            async def __aenter__(self):
                raise original
            async def __aexit__(self, *args):
                return False
        async def request(command, device):
            calls.append((copy.deepcopy(command),device))
            raise original
        gateway.normal = BeforeIPCFailure() if phase == "before-ipc" else asyncio.Semaphore(8)
        gateway.protective = asyncio.Semaphore(2)
        gateway.ipc = SimpleNamespace(request=request)
        times = iter([10.,20.,30.])
        monkeypatch.setattr(core,"_input_diagnostic_clock",lambda:next(times))
        peer = SimpleNamespace(token="private-test-token", ack=True, connection="same-connection",
            commands={"normal":1}, put=lambda value, **kwargs:outputs.append(value))
        command = {"op":op, "connection_generation":"same-connection"}
        await gateway.execute_peer(peer,{"id":"original-request","command":command,"_diagnostic_received":5.},"normal")
        assert peer.commands["normal"] == 0 and len(outputs) == 1
        result = outputs[0]
        assert {key:value for key,value in result.items() if key!="diagnostic_timing"} == dict(
            service.unknown_outcome(),kind="result",id="original-request")
        span = result["diagnostic_timing"]
        expected = {"version":1,"clock":"pi-monotonic","gateway_received":5.,"gateway_dispatch":10.}
        if phase == "ipc": expected["gateway_ipc_started"] = 20.
        if op == "grant": expected["reply_ready"] = 20. if phase == "before-ipc" else 30.
        assert span == expected
        assert len(json.dumps(span)) <= 512
        assert not any(key.startswith("core_") for key in span) and "gateway_ipc_completed" not in span
        assert len(calls) == (phase == "ipc"), "unknown never replays the request"
        assert command == {"op":op,"connection_generation":"same-connection"}
    import copy
    asyncio.run(scenario())


@pytest.mark.parametrize("op", ["input", "grant", "snapshot"])
@pytest.mark.parametrize("phase", ["before-ipc", "ipc"])
def test_partial_unknown_call_keeps_original_exception_and_nongrant_controls(monkeypatch, op, phase):
    from types import SimpleNamespace
    from tools import am1_session_service as service
    from tools import am1_session_core as core

    async def scenario():
        gateway = service.Gateway.__new__(service.Gateway)
        gateway.auth = SimpleNamespace(capable=lambda _:True)
        original = OSError("original IPC failure")
        class BeforeIPCFailure:
            async def __aenter__(self): raise original
            async def __aexit__(self, *args): return False
        async def request(*args): raise original
        gateway.normal = BeforeIPCFailure() if phase == "before-ipc" else asyncio.Semaphore(8)
        gateway.protective = asyncio.Semaphore(2)
        gateway.ipc = SimpleNamespace(request=request)
        times = iter([10.,20.])
        monkeypatch.setattr(core,"_input_diagnostic_clock",lambda:next(times))
        with pytest.raises(OSError) as caught:
            await gateway.call({"op":op},"owner")
        assert caught.value is original
        marks = getattr(original,"_am1_gateway_partial_marks",None)
        if op == "snapshot": assert marks is None
        else:
            assert marks == ({"gateway_dispatch":10.} if phase == "before-ipc" else {
                "gateway_dispatch":10.,"gateway_ipc_started":20.})
    asyncio.run(scenario())


# Compact wire lengths reproduce retained 74ef/b2bf sizes without publishing private histories.
def _peer_queue_snapshot(wire_bytes, *, revision=None):
    from tools.am1_session_ipc import encode
    value = {"kind": "snapshot", "snapshot": {"retained_size_fixture": ""}}
    if revision is not None:
        value["revision"] = revision
    value["snapshot"]["retained_size_fixture"] = "x" * (wire_bytes - len(encode(value)))
    assert len(encode(value)) == wire_bytes
    return value


class _PeerQueueSocket:
    def __init__(self):
        from types import SimpleNamespace
        self.entered, self.release, self.closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        self.sent, self.close_codes = [], []
        self.aborted = False
        self._writer = SimpleNamespace(transport=SimpleNamespace(abort=self.abort))

    def abort(self):
        self.aborted = True

    async def send_str(self, value):
        self.entered.set()
        await self.release.wait()
        if self.closed.is_set():
            raise ConnectionError("fake socket already closed")
        self.sent.append(json.loads(value))

    async def close(self, *, code):
        self.close_codes.append(code)
        self.closed.set()

    async def delivered(self, count):
        async with asyncio.timeout(.4):
            while len(self.sent) < count:
                await asyncio.sleep(.001)


def test_peer_queue_control_reply_evicts_only_queued_snapshot_and_keeps_stop_fifo():
    from tools import am1_session_service as service
    async def scenario():
        ws = _PeerQueueSocket()
        peer = service.Peer(ws, "owner", "private-test-token")
        gateway = service.Gateway.__new__(service.Gateway)
        observation = _peer_queue_snapshot(31326)
        connect = dict(accepted=True, status="connected", connection_generation=oid(),
                       snapshot=observation["snapshot"])
        try:
            assert peer.put(observation, True)
            await asyncio.wait_for(ws.entered.wait(), .2)
            gateway.command_reply(peer, {"id": "protective-stop", "command": {"op": "stop"}},
                                  {"accepted": True, "status": "finishing", "effect_admitted": False})
            assert peer.put(observation, True)
            gateway.command_reply(peer, {"id": "connect", "command": {"op": "connect"}}, connect)
            assert not peer.closed, "queued optional status must yield space to the command result"
            assert peer.bytes + peer.inflight <= 65536
            assert [json.loads(row[0])["id"] for row in peer.queue] == ["protective-stop", "connect"]
            assert all(row[1] is False for row in peer.queue)
            await asyncio.sleep(.05)
            ws.release.set()
            await ws.delivered(3)
            assert [row.get("id") for row in ws.sent] == [None, "protective-stop", "connect"]
            assert ws.sent[-1]["snapshot"] == observation["snapshot"]
            assert not ws.close_codes
        finally:
            await peer.close()
    asyncio.run(scenario())


def test_peer_queue_optional_terminal_snapshot_pressure_preserves_initial_ack_fence():
    from tools import am1_session_service as service
    async def scenario():
        ws = _PeerQueueSocket()
        peer = service.Peer(ws, "owner", "private-test-token")
        gateway = service.Gateway.__new__(service.Gateway)
        peer.revision = "initial-revision"
        initial = _peer_queue_snapshot(36870, revision=peer.revision)
        optional = _peer_queue_snapshot(36870)
        try:
            assert peer.put(initial, True)
            await asyncio.wait_for(ws.entered.wait(), .2)
            assert peer.put(optional, True) is False
            assert not peer.closed, "optional status pressure is not proof of a slow reader"
            assert peer.ack is False and peer.connection is None
            gateway.enqueue(peer, {"id": "claim-before-ack", "command": {"op": "claim"}})
            assert peer.commands == {"normal": 0, "protective": 0}
            assert peer.ack is False and peer.connection is None
            await asyncio.sleep(.05)
            ws.release.set()
            await ws.delivered(2)
            assert ws.sent[0] == initial
            assert ws.sent[1]["id"] == "claim-before-ack" and ws.sent[1]["accepted"] is False
            assert peer.ack is False and not ws.close_codes
            assert peer.put(optional, True)
            await ws.delivered(3)
            assert ws.sent[-1] == optional and peer.ack is False
        finally:
            await peer.close()
    asyncio.run(scenario())


def test_peer_queue_snapshot_pressure_preserves_grant_reserved_bytes_and_reply_bound():
    from tools import am1_session_service as service
    from tools.am1_session_ipc import encode
    async def scenario():
        ws = _PeerQueueSocket()
        peer = service.Peer(ws, "owner", "private-test-token")
        observation = _peer_queue_snapshot(31326)
        grant = {"kind": "result", "id": "grant", "accepted": True, "status": "granted", "padding": ""}
        grant["padding"] = "x" * (65536 - 31326 - 80 - len(encode(grant)))
        try:
            assert peer.put(observation, True)
            await asyncio.wait_for(ws.entered.wait(), .2)
            assert peer.put(grant, grant_reply=True)
            assert peer.queue[0][2] == 80 and peer.bytes + peer.inflight == 65536
            assert peer.put(observation, True) is False
            assert not peer.closed and peer.bytes + peer.inflight == 65536
            assert json.loads(peer.queue[0][0]) == grant and peer.queue[0][2] == 80
            assert peer.put({"kind": "result", "id": "excess-reply"}) is False
            assert peer.closed, "command replies cannot evict an earlier Grant or its reservation"
            await asyncio.wait_for(ws.closed.wait(), .2)
            assert 1013 in ws.close_codes
        finally:
            await peer.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("first_size,queued_reply", [(31326, True), (36870, False)])
def test_peer_queue_reply_only_or_inflight_pressure_still_closes(first_size, queued_reply):
    from tools import am1_session_service as service
    async def scenario():
        ws = _PeerQueueSocket()
        peer = service.Peer(ws, "owner", "private-test-token")
        reply = dict(kind="result", id="connect", accepted=True, status="connected",
                     connection_generation=oid(), snapshot=_peer_queue_snapshot(31326)["snapshot"])
        try:
            assert peer.put(_peer_queue_snapshot(first_size), True)
            await asyncio.wait_for(ws.entered.wait(), .2)
            if queued_reply:
                assert peer.put(dict(reply, id="first-connect"))
            assert peer.put(reply) is False
            assert peer.closed
            await asyncio.wait_for(ws.closed.wait(), .2)
            assert 1013 in ws.close_codes
        finally:
            await peer.close()
    asyncio.run(scenario())


def test_peer_queue_blocked_send_keeps_half_second_close():
    from tools import am1_session_service as service
    async def scenario():
        ws = _PeerQueueSocket()
        peer = service.Peer(ws, "owner", "private-test-token")
        try:
            assert peer.put(_peer_queue_snapshot(36870), True)
            await asyncio.wait_for(ws.entered.wait(), .2)
            await asyncio.wait_for(ws.closed.wait(), .8)
            assert peer.closed and 1013 in ws.close_codes and ws.sent == []
        finally:
            await peer.close()
    asyncio.run(scenario())


def test_peer_queue_intrinsically_oversized_initial_snapshot_rejects_and_closes(monkeypatch):
    from types import SimpleNamespace
    from tools import am1_session_service as service
    async def scenario():
        class Socket(_PeerQueueSocket):
            async def prepare(self, request):
                return None
            async def receive(self):
                return SimpleNamespace(type=WSMsgType.TEXT, data=json.dumps({"ticket": "once"}))
        ws = Socket()
        gateway = service.Gateway.__new__(service.Gateway)
        gateway.pending_ws, gateway.peers, gateway.revision = 0, set(), 0
        gateway.incarnation = "test-gateway"
        gateway.tickets = {service.digest("once"): ("owner", time.monotonic() + 10)}
        gateway.auth = SimpleNamespace(resolve=lambda _: {"id": "owner", "control": True})
        async def current():
            return {"oversized": "x" * 65536}
        gateway.current = current
        original_peer, created = service.Peer, []
        def create_peer(*args):
            peer = original_peer(*args)
            created.append(peer)
            return peer
        monkeypatch.setattr(service, "Peer", create_peer)
        monkeypatch.setattr(service.web, "WebSocketResponse", lambda **_: ws)
        class Request(dict):
            pass
        request = Request(token="private-test-token", device={"id": "owner"})
        request.transport = SimpleNamespace(set_write_buffer_limits=lambda **_: None,
            get_extra_info=lambda _: SimpleNamespace(setsockopt=lambda *args: None))
        await gateway.websocket(request)
        assert ws.close_codes[0] == 1008 and ws.sent == []
        assert len(created) == 1 and created[0].ack is False and created[0].connection is None
        assert gateway.pending_ws == 0 and gateway.peers == set()
    asyncio.run(scenario())



def test_transport_detach_cannot_enter_through_gateway_dispatch_or_enqueue():
    from types import SimpleNamespace
    from tools import am1_session_service as service
    async def scenario():
        gateway = service.Gateway.__new__(service.Gateway)
        gateway.auth = SimpleNamespace(resolve=lambda _: {"id": "owner", "control": True}, capable=lambda _: True)
        async def forbidden(*args):
            pytest.fail("public transport_detach reached private owner IPC")
        gateway.call = forbidden
        replies = []
        peer = SimpleNamespace(ack=True, connection="current", token="private-test-token",
            commands={"normal": 0, "protective": 0}, tasks=set(), put=lambda value, **_: replies.append(value))
        command = {"op": "transport_detach"}
        with pytest.raises(ValueError, match="operation"):
            await gateway.dispatch(command, {"id": "owner", "control": True}, peer)
        gateway.enqueue(peer, {"id": "forged-detach", "command": command})
        await asyncio.gather(*tuple(peer.tasks))
        assert len(replies) == 1 and replies[0]["accepted"] is False and replies[0]["effect_admitted"] is False
        assert peer.commands == {"normal": 0, "protective": 0}
    asyncio.run(scenario())


def test_transport_detach_has_no_public_http_route_or_wss_operation(cluster):
    async def scenario():
        session = await enrolled(cluster)
        ws = None
        try:
            async with session.post(cluster.url + "/api/transport_detach", json={}, ssl=session._test_tls,
                    headers={"Origin": cluster.url, "X-AM1-CSRF": session._test_csrf}) as response:
                assert response.status == 404
            ws, _ = await ws_attached(session, cluster)
            denied = await ws_command(ws, {"op": "transport_detach", "origin": "server"})
            assert denied["accepted"] is False and denied["effect_admitted"] is False
            assert (await snapshot(session, cluster))["controller"] is None
        finally:
            if ws:
                await ws.close()
            await session.close()
    asyncio.run(scenario())



def test_transport_detach_revoked_gateway_capability_never_reaches_private_owner():
    from types import SimpleNamespace
    from tools import am1_session_service as service
    async def scenario():
        gateway = service.Gateway.__new__(service.Gateway)
        gateway.normal, gateway.protective = asyncio.Semaphore(8), asyncio.Semaphore(2)
        gateway.auth = SimpleNamespace(capable=lambda _: False)
        async def forbidden(*args):
            pytest.fail("revoked transport detach reached private owner")
        gateway.ipc = SimpleNamespace(request=forbidden)
        result = await gateway.call({"op": "transport_detach"}, "revoked-owner")
        assert result == {"accepted": False, "status": "refused", "reason": "revoked control context",
                          "effect_admitted": False}
    asyncio.run(scenario())
