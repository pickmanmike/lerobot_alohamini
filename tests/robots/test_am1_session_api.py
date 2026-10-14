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
            result = await ws.receive_json(timeout=2)
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
