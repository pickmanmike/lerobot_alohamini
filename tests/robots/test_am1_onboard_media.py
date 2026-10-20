"""Onboard video keeps one capture, a bounded shared encoder and scoped signaling."""

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from aiohttp import web


def module():
    assert importlib.util.find_spec("tools.am1_onboard_media"), "onboard shared media boundary missing"
    from tools import am1_onboard_media

    return am1_onboard_media


def test_one_selected_encoder_is_shared_and_previous_process_exits_before_role_switch(tmp_path):
    from tools.am1_camera_viewer import FrameStore

    stores = {role: FrameStore() for role in ("forward", "chest")}
    for store in stores.values():
        store.publish(b"\xff\xd8\xff\xd9")
    children = []

    def spawn(command, **kwargs):
        child = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.buffer.read()"], **kwargs)
        children.append(child)
        return child

    encoder = module().SharedPrimaryEncoder(stores, "ffmpeg", process_factory=spawn)
    try:
        first = encoder.select("forward")
        shared = encoder.select("forward")
        assert len(children) == 1 and shared["encode_generation"] == first["encode_generation"]
        switched = encoder.select("chest")
        assert children[0].poll() is not None and len(children) == 2
        assert switched["role"] == "chest"
        assert switched["encode_generation"] != first["encode_generation"]
        command = encoder.command()
        assert command[command.index("-bf") + 1] == "0"
        assert command[command.index("-pix_fmt") + 1] == "yuv420p"
        assert "scale=640:360" not in command
        assert command[-1] == "rtsp://127.0.0.1:8554/am1_primary"
        with pytest.raises(ValueError):
            encoder.select("http://untrusted.invalid/")
    finally:
        encoder.close()
    assert all(child.poll() is not None for child in children)


def test_stalled_encoder_cannot_hold_capture_store_or_refresh_source_generation():
    from tools.am1_camera_viewer import FrameStore

    store = FrameStore()
    jpeg = b"\xff\xd8" + bytes(200_000) + b"\xff\xd9"
    store.publish(jpeg)

    def blocked(command, **kwargs):
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        if sys.platform == "linux":
            import fcntl

            # Pi pages can make the default pipe larger than this whole JPEG.
            # Request one small pipe before the feeder starts; Linux rounds to
            # its actual page size, which must still force this write to block.
            fcntl.fcntl(process.stdin.fileno(), fcntl.F_SETPIPE_SZ, 4096)
            assert fcntl.fcntl(process.stdin.fileno(), fcntl.F_GETPIPE_SZ) < len(jpeg)
        return process

    encoder = module().SharedPrimaryEncoder({"chest": store}, "ffmpeg", process_factory=blocked)
    try:
        first = encoder.select("chest")
        deadline = time.monotonic() + 2.0
        while encoder.status()["failure"] is None and time.monotonic() < deadline:
            if encoder.write_started is None:
                store.publish(jpeg)  # A delayed feeder still gets a fresh original sample.
            time.sleep(0.01)
        assert encoder.status()["failure"] == "encoder-write-stall"
        before_sequence = store.snapshot()[1]
        start = time.monotonic()
        for _ in range(30):
            store.publish(jpeg)
        assert store.snapshot()[1] == before_sequence + 30 and time.monotonic() - start < 0.25
        store.disconnected()
        store.publish(jpeg)
        assert encoder.status()["capture_generation"] == first["capture_generation"]
        assert encoder.status()["source_current"] is False
    finally:
        encoder.close()


def test_proxy_uses_actual_source_identity_and_original_arrival_for_decode_progress(tmp_path):
    m = module()
    ids = [str(uuid.uuid4()) for _ in range(3)]
    received = []
    now = time.monotonic()
    status = {
        "owner_generation": ids[0],
        "cameras": {
            "chest": {
                "state": "fresh",
                "configured": True,
                "sequence": 11,
                "age_ms": 20,
                "capture_generation": ids[1],
                "arrived_at_monotonic_s": now - 0.02,
            }
        },
        "video": {
            "enabled": True,
            "role": "chest",
            "encode_generation": ids[2],
            "capture_generation": ids[1],
            "state": "running",
        },
    }
    auth = tmp_path / "viewer.json"
    auth.write_text(json.dumps({"username": "fixture", "password": "fixture-password"}))
    auth.chmod(0o600)

    class IPC:
        async def request(self, command, device):
            received.append((command, device))
            if command["op"] == "snapshot":
                return {"snapshot": {"run": {"run_id": "run-a", "recipe": "physical-live-teleop"}}}
            return {"accepted": True}

    async def scenario():
        requests = []

        async def upstream(request):
            requests.append((request.method, request.path))
            assert request.headers.get("Authorization", "").startswith("Basic ")
            if request.path == "/status.json":
                return web.json_response(status)
            if request.path == "/api/webrtc/chest":
                assert await request.read() == b"v=0\r\n"
                return web.Response(status=201, body=b"v=0\r\n", content_type="application/sdp")
            if request.path == "/api/stream.mjpeg":
                assert request.query == {"src": "chest"}
                return web.Response(
                    body=b"--frame\r\nfixture",
                    headers={"Content-Type": "multipart/x-mixed-replace; boundary=frame"},
                )
            raise web.HTTPNotFound()

        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", upstream)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        proxy = m.OnboardMediaProxy(
            {
                "url": "http://127.0.0.1:" + str(site._server.sockets[0].getsockname()[1]),
                "credentials": str(auth),
            },
            IPC(),
        )
        await proxy.start()
        try:
            result = await proxy.offer("chest", b"v=0\r\n", "viewer-a")
            assert result[0] == 201 and result[1] == b"v=0\r\n"
            token = result[2]["X-Camera-View-Generation"]
            assert str(uuid.UUID(token)) == token
            body = {
                "role": "chest",
                "owner_generation": ids[0],
                "capture_generation": ids[1],
                "encode_generation": ids[2],
                "view_generation": token,
                "decoded_frames": 3,
                "media_time": 0.3,
            }
            assert (await proxy.progress("viewer-a", body, now))["accepted"] is True
            command, device = received[-1]
            assert command["op"] == "view_progress" and device == "gateway-view"
            assert command["source_sequence"] == 11 and command["source_fresh"] is True
            assert command["received_at_monotonic_s"] == now - 0.02
            assert (await proxy.progress("viewer-a", body, now))["accepted"] is False
            assert (
                await proxy.progress(
                    "viewer-a",
                    {**body, "encode_generation": str(uuid.uuid4()), "decoded_frames": 4, "media_time": 0.4},
                    now,
                )
            )["accepted"] is False
            before = len(received)
            for changed in ({"view_generation": str(uuid.uuid4())}, {"role": "wrist_right"}):
                assert not (await proxy.progress("viewer-a", {**body, **changed}, now))["accepted"]
            with pytest.raises(web.HTTPBadRequest):
                await proxy.progress("viewer-a", {key: value for key, value in body.items() if key != "view_generation"}, now)
            assert len(received) == before
            with pytest.raises(web.HTTPTooManyRequests):
                await proxy.offer("chest", b"v=0\r\n", "viewer-a")
            await proxy.offer("chest", b"v=0\r\n", "viewer-b")
            with pytest.raises(web.HTTPTooManyRequests):
                await proxy.offer("chest", b"v=0\r\n", "viewer-c")
            status["cameras"]["chest"]["state"] = "stale"
            assert (await proxy.progress("viewer-a", {**body, "decoded_frames": 5, "media_time": 0.5}, now))[
                "accepted"
            ] is False
            assert received[-1][0]["source_fresh"] is False
            with pytest.raises(web.HTTPBadRequest):
                await proxy.offer("arbitrary", b"v=0\r\n", "viewer-a")
            with pytest.raises(web.HTTPRequestEntityTooLarge):
                await proxy.offer("chest", b"x" * 65537, "viewer-a")
            legacy = b"".join([chunk async for chunk in proxy.mjpeg("chest")])
            assert legacy == b"--frame\r\nfixture"
            assert all("?" not in path for _, path in requests)
        finally:
            await proxy.close()
            await runner.cleanup()

    asyncio.run(scenario())


def test_onboard_sdp_preserves_gateway_auth_body_and_query_guards(tmp_path):
    from aiohttp.test_utils import make_mocked_request

    from tools.am1_session_service import COOKIE, Gateway

    module()
    gateway = Gateway(tmp_path / "owner", tmp_path / "auth")
    gateway.origin = "https://fixture.invalid"
    device = gateway.auth.enroll(gateway.auth.pair())

    async def scenario():
        headers = {
            "Host": "fixture.invalid",
            "Origin": gateway.origin,
            "Cookie": COOKIE + "=" + device["token"],
            "X-AM1-CSRF": device["csrf"],
            "Content-Type": "application/sdp",
            "Content-Length": "12000",
        }
        reached = []

        async def handler(request):
            reached.append(True)
            return web.Response(status=204)

        for change, expected in [
            ({"Host": "other.invalid"}, 403),
            ({"Origin": "https://other.invalid"}, 403),
            ({"X-AM1-CSRF": "bad"}, 403),
            ({"Cookie": ""}, 401),
            ({"Content-Length": "65537"}, 413),
            ({"Content-Type": "text/plain"}, 415),
        ]:
            request = make_mocked_request(
                "POST", gateway.origin + "/api/onboard/webrtc/chest", headers=dict(headers, **change)
            )
            assert (await gateway.boundary(request, handler)).status == expected
        assert not reached
        good = make_mocked_request("POST", gateway.origin + "/api/onboard/webrtc/chest", headers=headers)
        assert (await gateway.boundary(good, handler)).status == 204
        queried = make_mocked_request(
            "POST", gateway.origin + "/api/onboard/webrtc/chest?src=forward", headers=headers
        )
        assert (await gateway.boundary(queried, handler)).status == 400

    asyncio.run(scenario())


def test_primary_backend_enables_only_fixed_signaling_and_loopback_publisher():
    from tools import am1_camera_viewer as viewer

    config = {"bind": "192.168.1.134", "cameras": {"chest": "/dev/am_camera_chest"}}
    backend = viewer.backend_config(config, "private-fixture", primary_webrtc=True)
    assert backend["streams"]["chest"].endswith("input_format=mjpeg&video_size=640x480&framerate=30")
    assert backend["streams"]["am1_primary"] is None
    assert backend["rtsp"]["listen"] == "127.0.0.1:8554"
    assert backend["webrtc"]["ice_servers"] == []
    assert backend["webrtc"]["listen"] == "192.168.1.134:8555/udp"
    assert set(backend["api"]["allow_paths"]) == {"/api/stream.mjpeg", "/api/webrtc"}
    assert "ffmpeg" not in backend["app"]["modules"] and "exec" not in backend["app"]["modules"]
    store = viewer.FrameStore()
    store.publish(b"\xff\xd8\xff\xd9")
    actual = store.status()
    assert actual["capture_generation"] == store.metadata_snapshot()[1]["capture_generation"]
    assert actual["arrived_at_monotonic_s"] == store.metadata_snapshot()[1]["arrived_at"]


def test_camera_script_loads_shared_encoder_without_gateway_packages_or_working_directory(tmp_path):
    from tools import am1_camera_viewer as viewer

    source = Path(viewer.__file__).resolve()
    command = [
        sys.executable,
        "-B",
        "-c",
        "import runpy,sys;sys.path.insert(0,sys.argv[1]);v=runpy.run_path(sys.argv[2]);"
        "assert v['SharedPrimaryEncoder'].__module__.endswith('am1_onboard_media')",
        str(source.parent),
        str(source),
    ]
    result = subprocess.run(
        command,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("case", ["fresh", "delayed", "stale", "nonadvancing", "foreign"])
def test_selected_view_progress_entrypoint_records_real_phase_times_without_renewing_proof(tmp_path, monkeypatch, case):
    from types import SimpleNamespace
    from tests.robots.test_am1_live_teleop_executor import authority_lane
    from tools import am1_session_service as service

    a, e, _ = authority_lane(tmp_path)
    clock = SimpleNamespace(now=10.)
    a.clock = lambda: clock.now
    monkeypatch.setattr(service.time, "monotonic", lambda: clock.now)
    proxy = object.__new__(module().OnboardMediaProxy)
    proxy.clock, proxy.web = lambda: clock.now, web
    proxy.viewers = {"viewer-a": {"frames": 0, "media_time": -1, "ready": True, "role": "chest",
                                  "view_generation": "11111111-1111-4111-8111-111111111111"}}
    report = {"owner_generation": "owner", "cameras": {"chest": {
        "state": "stale" if case == "stale" else "fresh", "configured": True, "sequence": 11,
        "capture_generation": "capture", "arrived_at_monotonic_s": 9.98}},
        "video": {"role": "chest", "encode_generation": "encode", "capture_generation": "capture", "state": "running"}}
    async def status():
        clock.now += .02
        return report
    proxy.status = status
    commands = []
    class IPC:
        async def request(self, command, device):
            commands.append(command)
            assert device == "gateway-view"
            if command["op"] == "snapshot":
                clock.now += .04
                return {"snapshot": {"run": {"run_id": e.run_id, "recipe": "physical-live-teleop"}}}
            clock.now += .5 if case == "delayed" else .03
            with a.mutex:
                return a._handle(command, device)
    proxy.ipc = IPC()
    gateway = object.__new__(service.Gateway)
    gateway.onboard = proxy
    body = dict(role="chest", owner_generation="owner", capture_generation="capture",
                encode_generation="foreign" if case == "foreign" else "encode",
                view_generation=proxy.viewers["viewer-a"]["view_generation"], decoded_frames=3, media_time=.3)
    class Request(dict):
        path = "/api/onboard/progress"
        async def read(self):
            clock.now += .01
            return json.dumps(body).encode()
    async def scenario():
        request = Request(device={"id": "viewer-a"})
        if case == "nonadvancing":
            await gateway.onboard_request(request)
            before = len(commands)
            clock.now = 10.49
        else:
            before = 0
        received = clock.now
        response = await gateway.onboard_request(request)
        result = json.loads(response.body)
        assert result["accepted"] is (case == "fresh")
        timing = result["view_delivery"]
        assert timing["request_received_at_monotonic_s"] == received
        assert timing["request_body_completed_at_monotonic_s"] == pytest.approx(received + .01)
        assert timing["status_started_at_monotonic_s"] == pytest.approx(received + .01)
        assert timing["status_completed_at_monotonic_s"] == pytest.approx(received + .03)
        assert timing["source_arrived_at_monotonic_s"] == 9.98
        assert timing["decoded_frames"] == 3 and timing["media_time"] == .3 and timing["source_sequence"] == 11
        assert timing["owner_enqueued_at_monotonic_s"] is None
        assert timing["http_response_ready_at_monotonic_s"] == clock.now
        if case in {"foreign", "nonadvancing"}:
            assert len(commands) == before
            assert timing["owner_snapshot_started_at_monotonic_s"] is None
            assert timing["core"] is None
        else:
            assert timing["owner_snapshot_started_at_monotonic_s"] == pytest.approx(received + .03)
            assert timing["owner_snapshot_completed_at_monotonic_s"] == pytest.approx(received + .07)
            assert timing["owner_progress_started_at_monotonic_s"] == pytest.approx(received + .07)
            assert timing["owner_progress_completed_at_monotonic_s"] == clock.now
            assert timing["core"]["core_started_at_monotonic_s"] == clock.now
            assert timing["original_floor_at_monotonic_s"] == (received if case == "stale" else 9.98)
            if case == "fresh":
                assert result["required_view"]["qualified"]
                assert timing["core"]["qualified_until_monotonic_s"] == 10.48
            elif case == "delayed":
                assert result["reason"] == "selected-view original receipt or progress invalid"
            else:
                assert result["reason"] == "selected source is not fresh"
    try:
        asyncio.run(scenario())
    finally:
        a.run = None
        a.close()


@pytest.mark.parametrize("costs,expected", [
    ([0.] * 8, [i / 10 for i in range(8)]),
    ([.02] * 8, [i / 10 for i in range(8)]),
    ([.04] * 8, [i / 10 for i in range(8)]),
    ([.02, .02, .13, .02, .02, .02], [0., .1, .2, .4, .5, .6]),
])
def test_shared_feeder_keeps_declared_clock_without_write_cost_drift_or_catchup(monkeypatch, costs, expected):
    """Synthetic finite costs, real FrameStore/_feed; no encoder/network/device claim."""
    from types import SimpleNamespace

    from tools.am1_camera_viewer import FrameStore

    m = module()
    now, next_capture, published, starts, delivered = [0.], [1 / 15], [0], [], []
    store = FrameStore(clock=lambda: now[0])
    def publish():
        published[0] += 1
        store.publish(b"\xff\xd8" + published[0].to_bytes(2, "big") + b"\xff\xd9")
    publish()
    generation = store.metadata_snapshot()[1]["capture_generation"]
    def advance(seconds):
        end = now[0] + seconds
        while next_capture[0] <= end:
            now[0] = next_capture[0]
            publish()
            next_capture[0] += 1 / 15
        now[0] = end
    class Stop:
        stopped, waits = False, 0
        def is_set(self):
            return self.stopped
        def wait(self, seconds):
            assert seconds >= 0
            advance(seconds)
            self.waits += 1
            self.stopped = self.waits == len(costs)
            return self.stopped
    class Pipe:
        def write(self, value):
            starts.append(now[0])
            delivered.append(int.from_bytes(value[2:4], "big"))
            advance(costs[len(starts) - 1])
            return len(value)
    monkeypatch.setattr(m, "time", SimpleNamespace(monotonic=lambda: now[0]))
    process = SimpleNamespace(stdin=Pipe(), poll=lambda: None)
    encoder = m.SharedPrimaryEncoder({"chest": store}, "ffmpeg", process_factory=lambda *args, **kwargs: process)
    encoder._feed(process, Stop(), "chest", generation)
    assert encoder.command()[encoder.command().index("-framerate") + 1] == "10"
    assert starts == pytest.approx(expected, abs=1e-9)
    assert all(b - a >= .1 - 1e-9 for a, b in zip(starts, starts[1:], strict=False))
    assert delivered == sorted(set(delivered))  # Latest genuine JPEG only, no repeat/catch-up votes.
    assert encoder.frames == len(costs) and encoder.sequence == delivered[-1]
    assert encoder.dropped == delivered[-1] - delivered[0] + 1 - len(delivered)
    assert encoder.failure is None and encoder.write_started is None


@pytest.mark.parametrize("same_role", [False, True])
@pytest.mark.parametrize("new_progress", [False, True])
@pytest.mark.parametrize("old_fresh", [False, True])
def test_canceled_progress_server_work_cannot_replace_newer_offer_or_progress(tmp_path, monkeypatch, same_role, new_progress, old_fresh):
    """Real Gateway/proxy/Core order; only upstream media and IPC waiting are simulated."""
    import copy
    from types import SimpleNamespace

    from tests.robots.test_am1_live_teleop_executor import authority_lane
    from tools import am1_session_service as service

    a, e, _ = authority_lane(tmp_path)
    clock = SimpleNamespace(now=10.)
    a.clock = lambda: clock.now
    monkeypatch.setattr(service.time, "monotonic", lambda: clock.now)
    proxy = object.__new__(module().OnboardMediaProxy)
    proxy.clock, proxy.web = lambda: clock.now, web
    proxy.viewers = {"viewer-a": {"frames": 0, "media_time": -1, "at": 9., "offered_at": 8.,
                                  "ready": True, "role": "chest", "view_generation": "11111111-1111-4111-8111-111111111111"}}
    old_report = {"owner_generation": "owner", "cameras": {"chest": {
        "state": "fresh" if old_fresh else "stale", "configured": True, "sequence": 11,
        "capture_generation": "capture-old", "arrived_at_monotonic_s": 9.98}},
        "video": {"role": "chest", "encode_generation": "encode-old", "capture_generation": "capture-old", "state": "running"}}
    current_report = [old_report]
    async def status():
        return current_report[0]
    proxy.status = status
    async def upstream(*args, **kwargs):
        return 201, b"v=0\r\n", {"Content-Type": "application/sdp"}
    proxy.upstream = upstream
    gateway = object.__new__(service.Gateway)
    gateway.onboard = proxy
    class Request(dict):
        method = "POST"
        def __init__(self, path, body, role=None):
            super().__init__(device={"id": "viewer-a"})
            self.path, self.content, self.match_info = path, body, {"role": role}
        async def read(self):
            return self.content
    async def scenario():
        blocked, release = asyncio.Event(), asyncio.Event()
        proxy.offer_lock = asyncio.Lock()
        class IPC:
            async def request(self, command, device):
                if command["op"] == "snapshot":
                    return {"snapshot": {"run": {"run_id": e.run_id, "recipe": "physical-live-teleop"}}}
                if command["encode_generation"] == "encode-old":
                    blocked.set()
                    await release.wait()
                with a.mutex:
                    return a._handle(command, device)
        proxy.ipc = IPC()
        old_body = {"role": "chest", "owner_generation": "owner", "capture_generation": "capture-old",
                    "encode_generation": "encode-old", "view_generation": proxy.viewers["viewer-a"]["view_generation"],
                    "decoded_frames": 3, "media_time": .3}
        server_work = asyncio.create_task(gateway.onboard_request(Request("/api/onboard/progress", json.dumps(old_body).encode())))
        async def browser_wait():
            return await asyncio.shield(server_work)
        browser = asyncio.create_task(browser_wait())
        try:
            await asyncio.wait_for(blocked.wait(), 1)
            browser.cancel()
            with pytest.raises(asyncio.CancelledError):
                await browser
            assert not server_work.done()  # A canceled browser receipt is not server cancellation.
            clock.now = 10.1
            role = "chest" if same_role else "wrist_right"
            report = {"owner_generation": "owner", "cameras": {role: {
                "state": "fresh", "configured": True, "sequence": 20,
                "capture_generation": "capture-new", "arrived_at_monotonic_s": 10.09}},
                "video": {"role": role, "encode_generation": "encode-new", "capture_generation": "capture-new", "state": "running"}}
            current_report[0] = report
            offered = await gateway.onboard_request(Request("/api/onboard/webrtc/" + role, b"v=0\r\n", role))
            assert offered.status == 201 and a._teleop_view["decoded_frames"] == 0
            assert not a._teleop_view_qualified()
            if new_progress:
                clock.now = 10.15
                report["cameras"][role].update(sequence=21, arrived_at_monotonic_s=10.14)
                body = {"role": role, "owner_generation": "owner", "capture_generation": "capture-new",
                        "encode_generation": "encode-new", "view_generation": offered.headers["X-Camera-View-Generation"],
                        "decoded_frames": 1, "media_time": .1}
                result = json.loads((await gateway.onboard_request(Request("/api/onboard/progress", json.dumps(body).encode()))).body)
                assert result["accepted"] and result["required_view"]["qualified"]
            before = copy.deepcopy(a._teleop_view)
            clock.now = 10.18
            release.set()
            result = json.loads((await asyncio.wait_for(server_work, 1)).body)
            assert not result["accepted"]
            assert result["view_delivery"]["owner_result"]["reason"] == "selected-view original receipt superseded"
            assert result["view_delivery"]["core"]["core_accepted_at_monotonic_s"] is None
            assert a._teleop_view == before and a._teleop_view["role"] == role
            assert a._teleop_view["qualified_until"] == (10.64 if new_progress else 0.)
        finally:
            release.set()
            await asyncio.gather(server_work, return_exceptions=True)
    try:
        asyncio.run(scenario())
    finally:
        a.run = None
        a.close()


@pytest.mark.parametrize("stage", ["offer_status", "offer_upstream", "after_complete", "progress_status", "failed_offer"])
@pytest.mark.parametrize("same_role", [False, True])
def test_view_attachment_retires_old_reports_before_offer_work_and_after_same_source_reconnect(tmp_path, monkeypatch, stage, same_role):
    """Real Gateway/proxy/Core; a new offer retires old browser work before any await."""
    import copy
    from types import SimpleNamespace

    from tests.robots.test_am1_live_teleop_executor import authority_lane
    from tools import am1_session_service as service

    a, e, _ = authority_lane(tmp_path)
    clock = SimpleNamespace(now=10.)
    a.clock = lambda: clock.now
    monkeypatch.setattr(service.time, "monotonic", lambda: clock.now)
    proxy = object.__new__(module().OnboardMediaProxy)
    proxy.clock, proxy.web = lambda: clock.now, web
    proxy.viewers = {"viewer-a": {"frames": 0, "media_time": -1, "at": 9., "offered_at": 8.,
                                  "ready": True, "role": "chest", "view_generation": "11111111-1111-4111-8111-111111111111"}}
    report = {"owner_generation": "owner", "cameras": {role: {
        "state": "fresh", "configured": True, "sequence": 11,
        "capture_generation": "capture", "arrived_at_monotonic_s": 9.98}
        for role in ("chest", "wrist_right")},
        "video": {"role": "chest", "encode_generation": "encode", "capture_generation": "capture", "state": "running"}}
    gateway = object.__new__(service.Gateway)
    gateway.onboard = proxy
    class Request(dict):
        method = "POST"
        def __init__(self, path, body, role=None):
            super().__init__(device={"id": "viewer-a"})
            self.path, self.content, self.match_info = path, body, {"role": role}
        async def read(self):
            return self.content
    async def scenario():
        blocked, release = asyncio.Event(), asyncio.Event()
        proxy.offer_lock = asyncio.Lock()
        commands = []
        class IPC:
            async def request(self, command, device):
                if command["op"] == "snapshot":
                    return {"snapshot": {"run": {"run_id": e.run_id, "recipe": "physical-live-teleop"}}}
                commands.append(command)
                with a.mutex:
                    return a._handle(command, device)
        proxy.ipc = IPC()
        block_status = [stage in {"offer_status", "progress_status"}]
        async def status():
            if block_status[0]:
                block_status[0] = False
                blocked.set()
                await release.wait()
            return report
        proxy.status = status
        async def upstream(*args, **kwargs):
            if stage == "offer_upstream":
                blocked.set()
                await release.wait()
            return (201, b"bad" if stage == "failed_offer" else b"v=0\r\n", {"Content-Type": "application/sdp"})
        proxy.upstream = upstream
        old_body = {"role": "chest", "owner_generation": "owner", "capture_generation": "capture",
                    "encode_generation": "encode", "view_generation": proxy.viewers["viewer-a"]["view_generation"],
                    "decoded_frames": 30, "media_time": 3.}
        def progress():
            return gateway.onboard_request(Request("/api/onboard/progress", json.dumps(old_body).encode()))
        role = "chest" if same_role else "wrist_right"
        def offer():
            return gateway.onboard_request(Request("/api/onboard/webrtc/" + role, b"v=0\r\n", role))
        task = None
        try:
            if stage == "progress_status":
                task = asyncio.create_task(progress())
                await asyncio.wait_for(blocked.wait(), 1)
                clock.now = 10.1
                offered = await offer()
                assert offered.status == 201
                before_commands = len(commands)
                before = copy.deepcopy(a._teleop_view)
                clock.now = 10.2
                release.set()
                result = json.loads((await asyncio.wait_for(task, 1)).body)
            else:
                clock.now = 10.1
                if stage in {"offer_status", "offer_upstream"}:
                    task = asyncio.create_task(offer())
                    await asyncio.wait_for(blocked.wait(), 1)
                elif stage == "failed_offer":
                    with pytest.raises(web.HTTPBadGateway):
                        await offer()
                else:
                    offered = await offer()
                    assert offered.status == 201
                    report["video"]["role"] = role
                before_commands = len(commands)
                before = copy.deepcopy(a._teleop_view)
                clock.now = 10.2
                report["cameras"]["chest"].update(sequence=12, arrived_at_monotonic_s=10.19)
                result = json.loads((await progress()).body)
            assert result["accepted"] is False
            assert len(commands) == before_commands  # Rejected at attachment, before owner IPC.
            assert a._teleop_view == before
            if stage in {"after_complete", "progress_status"}:
                token = offered.headers["X-Camera-View-Generation"]  # Existing Gateway header pass-through.
                assert token != old_body["view_generation"] and str(uuid.UUID(token)) == token
                clock.now = 10.24
                report["video"]["role"] = role
                report["cameras"][role].update(sequence=13, arrived_at_monotonic_s=10.23)
                current = {**old_body, "role": role, "view_generation": token, "decoded_frames": 1, "media_time": .1}
                accepted = json.loads((await gateway.onboard_request(Request("/api/onboard/progress", json.dumps(current).encode()))).body)
                assert accepted["accepted"] and accepted["required_view"]["qualified"]
                assert commands[-1]["received_at_monotonic_s"] == 10.23
                assert a._teleop_view["qualified_until"] == 10.73
        finally:
            release.set()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
    try:
        asyncio.run(scenario())
    finally:
        a.run = None
        a.close()


@pytest.mark.parametrize("old_fresh", [False, True])
@pytest.mark.parametrize("source_changed", [False, True])
def test_older_gateway_request_cannot_replace_newer_view_with_an_older_source_floor(tmp_path, monkeypatch, old_fresh, source_changed):
    """Real Gateway/proxy/Core; receipt order differs from source freshness order."""
    import copy
    from types import SimpleNamespace

    from tests.robots.test_am1_live_teleop_executor import authority_lane
    from tools import am1_session_service as service

    a, e, _ = authority_lane(tmp_path)
    clock = SimpleNamespace(now=10.2)
    a.clock = lambda: clock.now
    monkeypatch.setattr(service.time, "monotonic", lambda: clock.now)
    proxy = object.__new__(module().OnboardMediaProxy)
    proxy.clock, proxy.web = lambda: clock.now, web
    token = "11111111-1111-4111-8111-111111111111"
    proxy.viewers = {"viewer-a": {"frames": 0, "media_time": -1, "ready": True, "role": "chest",
                                  "view_generation": token}}
    report = {"owner_generation": "owner", "cameras": {"chest": {
        "state": "fresh" if old_fresh else "stale", "configured": True, "sequence": 11,
        "capture_generation": "capture", "arrived_at_monotonic_s": 10.19}},
        "video": {"role": "chest", "encode_generation": "encode", "capture_generation": "capture", "state": "running"}}
    async def status():
        return copy.deepcopy(report)
    proxy.status = status
    gateway = object.__new__(service.Gateway)
    gateway.onboard = proxy
    class Request(dict):
        path = "/api/onboard/progress"
        def __init__(self, body):
            super().__init__(device={"id": "viewer-a"})
            self.content = json.dumps(body).encode()
        async def read(self):
            return self.content
    async def scenario():
        blocked, release = asyncio.Event(), asyncio.Event()
        commands = []
        class IPC:
            async def request(self, command, device):
                if command["op"] == "snapshot":
                    return {"snapshot": {"run": {"run_id": e.run_id, "recipe": "physical-live-teleop"}}}
                commands.append(copy.deepcopy(command))
                if command["decoded_frames"] == 3:
                    blocked.set()
                    await release.wait()
                with a.mutex:
                    return a._handle(command, device)
        proxy.ipc = IPC()
        body = {'role': 'chest', 'owner_generation': 'owner', 'capture_generation': 'capture', 'encode_generation': 'encode', 'view_generation': token, 'decoded_frames': 3, 'media_time': 0.3}
        old = asyncio.create_task(gateway.onboard_request(Request(body)))
        try:
            await asyncio.wait_for(blocked.wait(), 1)
            clock.now = 10.25
            report["cameras"]["chest"].update(state="fresh", sequence=12, arrived_at_monotonic_s=10.1)
            if source_changed:
                report["cameras"]["chest"]["capture_generation"] = "capture-new"
                report["video"].update(capture_generation="capture-new", encode_generation="encode-new")
                body.update(capture_generation="capture-new", encode_generation="encode-new")
            current = json.loads((await gateway.onboard_request(Request(dict(body, decoded_frames=4, media_time=.4)))).body)
            assert current["accepted"] and current["required_view"]["qualified"]
            assert commands[-1]["received_at_monotonic_s"] == 10.1
            assert commands[-1]["request_received_at_monotonic_s"] == 10.25
            assert commands[0]["request_received_at_monotonic_s"] == 10.2
            assert commands[0]["received_at_monotonic_s"] == (10.19 if old_fresh else 10.2)
            before = copy.deepcopy(a._teleop_view)
            clock.now = 10.3
            release.set()
            stale = json.loads((await asyncio.wait_for(old, 1)).body)
            assert not stale["view_delivery"]["owner_result"]["accepted"]
            assert stale["view_delivery"]["owner_result"]["reason"] == "selected-view original receipt superseded"
            assert a._teleop_view == before and a._teleop_view_qualified()
            assert a._teleop_view["qualified_until"] == 10.6  # Source age, not younger HTTP receipt.
            assert a.run["status"] == "running"
            clock.now = 10.31
            report["cameras"]["chest"].update(state="stale", sequence=13)
            latest = json.loads((await gateway.onboard_request(Request(dict(body, decoded_frames=5, media_time=.5)))).body)
            assert not latest["accepted"] and latest["reason"] == "selected source is not fresh"
            assert not a._teleop_view_qualified() and a.run["status"] == "paused"
        finally:
            release.set()
            await asyncio.gather(old, return_exceptions=True)
    try:
        asyncio.run(scenario())
    finally:
        a.run = None
        a.close()
