"""Same whole-frame sample at authority and persistent executor boundaries."""

import copy
from uuid import uuid4

import pytest

from examples.alohamini.am1_session_contract import RECIPES, Recipe
from tests.robots.test_am1_live_teleop_contract import KEYS, source, workspace
from tests.robots.test_am1_physical_executor import ControlledHost, executor


def admitted_executor():
    e = executor(ControlledHost())
    e.clock = lambda: 10.0
    recipe = RECIPES["physical-live-teleop"]
    assert e.supports(recipe), "persistent owner lacks whole-frame live mode"
    e.bind_run(str(uuid4()), recipe)
    identity = {
        "run_id": e.run_id,
        "host_incarnation": e.host_incarnation,
        "service_incarnation": str(uuid4()),
        "controller_generation": str(uuid4()),
        "connection_generation": str(uuid4()),
        "intent_revision": 0,
    }
    assert e.bind_control(**identity)
    e._lifecycle.update(phase="live", native_live_at=9.0, native_deadline_monotonic_s=309.0)
    e.desired = "active"
    e.sample = {
        "at": 10.0,
        "sequence": 1,
        "positions": dict.fromkeys(KEYS, 0.0),
        "ack": {
            "run_id": e.run_id,
            "host_incarnation": e.host_incarnation,
            "intent_revision": 0,
            "state": "active",
            "epoch": 0,
            "live_input": {"state": "active", "workspace": workspace()},
        },
    }
    return e, identity


def test_exact_whole_frame_preserves_source_body_expiry_and_rejects_regrant_replay():
    e, identity = admitted_executor()
    src = source()
    body = {"x.vel": 0.1, "y.vel": 0.0, "theta.vel": 0.0, "lift_axis.vel": 0}
    target = dict.fromkeys(KEYS, 5.0)
    assert e.apply(
        target,
        body=body,
        source=src,
        valid_until_monotonic_s=10.2,
        grant_id=src["grant_id"],
        seq=1,
        **identity,
    )
    assert e._live_target == {
        "target": target,
        "body": body,
        "source": src,
        "valid_until_monotonic_s": 10.2,
        "grant_id": src["grant_id"],
        "seq": 1,
    }
    retained = copy.deepcopy(e._live_target)
    assert not e.apply(
        target,
        body=body,
        source=dict(src, grant_id=str(uuid4())),
        valid_until_monotonic_s=10.24,
        grant_id=str(uuid4()),
        seq=2,
        **identity,
    )
    assert e._live_target == retained
    assert not e.supports(Recipe("physical-live-teleop", "interactive", None, 301, seed=None))
    assert not e.supports(Recipe("physical-live-teleop", "finite", 180, 300, seed=None))


def test_connection_rebind_keeps_source_history_but_clears_movement():
    e, identity = admitted_executor()
    src = source()
    assert e.apply(
        dict.fromkeys(KEYS, 5.0),
        body={"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0, "lift_axis.vel": 0},
        source=src,
        valid_until_monotonic_s=10.2,
        grant_id=src["grant_id"],
        seq=1,
        **identity,
    )
    rebound = dict(identity, connection_generation=str(uuid4()), intent_revision=1)
    assert e.bind_control(**rebound)
    assert e._live_target is None and e.desired == "hold"
    e.desired = "active"
    assert not e.apply(
        dict.fromkeys(KEYS, 5.0),
        body={"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0, "lift_axis.vel": 0},
        source=src,
        valid_until_monotonic_s=10.24,
        grant_id=src["grant_id"],
        seq=1,
        **rebound,
    )


def authority_lane(tmp_path):
    from tools.am1_session_core import SessionAuthority

    e, identity = admitted_executor()
    a = SessionAuthority(tmp_path, clock=lambda: 10.0, executor=e)
    identity["service_incarnation"] = a.service_incarnation
    a.controller = {"device_id": "pc", "generation": identity["controller_generation"], "expires": 11.5}
    a.connection = {"device_id": "pc", "generation": identity["connection_generation"], "seq": -1}
    a.run = {
        "run_id": e.run_id,
        "operation_id": str(uuid4()),
        "recipe": "physical-live-teleop",
        "status": "running",
        "intent_revision": 0,
        "service_incarnation": a.service_incarnation,
        "deadline": 309.0,
        "native_live_at": 9.0,
        "first_cause": None,
        "recovery": None,
        "uncertain": False,
        "progress_s": 0.0,
        "cleanup": None,
        "dispatch": "acknowledged",
    }
    e.bind_control(**identity)
    e.desired = "active"
    return a, e, identity


def test_authority_accepts_exact_frame_and_retains_source_history_when_input_clears(tmp_path):
    a, e, ids = authority_lane(tmp_path)
    try:
        a._teleop_view = {"run_id": e.run_id, "qualified_until": 10.4}
        grant = a._handle(dict(op="grant", **ids), "pc")
        assert grant["accepted"]
        src = source(grant=grant["grant_id"])
        frame = dict(
            op="input",
            **ids,
            grant_id=grant["grant_id"],
            seq=1,
            source=src,
            target=dict.fromkeys(KEYS, 5.0),
            body={"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0, "lift_axis.vel": 0},
        )
        accepted = a._handle(frame, "pc")
        assert accepted["accepted"], accepted
        assert e._live_target["source"] == src
        a._clear_input()
        assert a.run["input_source"] == src
    finally:
        a.run = None
        a.close()


def test_view_progress_is_internal_and_static_decode_never_renews_required_view(tmp_path):
    a, e, _ = authority_lane(tmp_path)
    try:
        progress = {
            "op": "view_progress",
            "run_id": e.run_id,
            "role": "chest",
            "owner_generation": "owner",
            "capture_generation": "capture",
            "encode_generation": str(uuid4()),
            "decoded_frames": 1,
            "media_time": 0.1,
            "source_sequence": 1,
            "source_fresh": True,
            "received_at_monotonic_s": 9.9,
        }
        assert not a._handle(progress, "pc")["accepted"]
        assert a._handle(progress, "gateway-view")["accepted"]
        original = a._teleop_view["qualified_until"]
        assert a._handle(dict(progress, received_at_monotonic_s=10.0), "gateway-view")["accepted"]
        assert a._teleop_view["qualified_until"] == original
        assert a._handle(dict(progress, decoded_frames=0), "gateway-view")["accepted"]
        assert not a._teleop_view_qualified()
    finally:
        a.run = None
        a.close()


def test_real_core_executor_native_frame_expiry_and_fresh_rejoin_preserve_run(tmp_path):
    import time

    from lerobot.robots.alohamini import alohamini_host as host
    from tests.robots.test_am1_live_teleop_native import lane
    from tests.robots.test_am1_persistent_session import command, start
    from tests.robots.test_am1_physical_executor import until
    from tools.am1_session_core import SessionAuthority

    class NativeIO:
        provenance = "protected-host"

        def start(self, run_id, incarnation, recipe, cancel):
            _, _, _, self.robot, _ = lane()
            # Production send_action uses getattr(..., no-op) before an input
            # hook exists. The injected bus fixture calls the hook directly.
            self.robot._am1_live_before_write = lambda: None
            self.robot._am1_teleop_binding = {
                "run_id": run_id,
                "host_incarnation": incarnation,
                "recipe": recipe.name,
            }
            self.control = host.AM1LocalControl(
                run_id=run_id, host_incarnation=incarnation, live=True, live_recipe=recipe.name
            )
            self.actions = []

        def poll(self, previous):
            self.control.check_live(self.robot)
            observation = self.robot.get_observation()
            at = time.monotonic()
            self.control.annotate(observation, acquired_at=at)
            marker = observation[host.AM1_LOCAL_FEEDBACK_KEY]
            return {
                "positions": self.robot.get_observation(),
                "sequence": marker["observation_id"],
                "at": at,
                "ack": marker,
                "observation": observation,
                "provenance": "protected-host/measured-feedback",
            }

        def send(self, action):
            self.actions.append(copy.deepcopy(action))
            self.control.apply(self.robot, action)

        def finish(self, status):
            return {"cleanup": "physical_readback_verified", "uncertain": False, "terminal_status": status}

    io = NativeIO()
    e = executor(io)
    a = SessionAuthority(tmp_path, executor=e)
    decode = [0]

    def step():
        if a.run:
            decode[0] += 1
            a._handle(
                {
                    "op": "view_progress",
                    "run_id": a.run["run_id"],
                    "role": "chest",
                    "owner_generation": "owner",
                    "capture_generation": "capture",
                    "encode_generation": "encode",
                    "decoded_frames": decode[0],
                    "media_time": decode[0] / 30,
                    "source_sequence": decode[0],
                    "source_fresh": True,
                    "received_at_monotonic_s": time.monotonic(),
                },
                "gateway-view",
            )
        if a.controller:
            a._handle({"op": "claim", "controller_generation": a.controller["generation"]}, "owner")
        a.tick()
        return a.run

    def ids():
        return {
            "run_id": a.run["run_id"],
            "service_incarnation": a.service_incarnation,
            "host_incarnation": e.host_incarnation,
            "controller_generation": a.controller["generation"],
            "connection_generation": a.connection["generation"] if a.connection else "",
            "intent_revision": a.run["intent_revision"],
        }

    def resume():
        connected = a.handle(dict(op="connect", **ids()), "owner")
        assert connected["accepted"], connected
        assert a.handle(dict(op="release_input", **ids()), "owner")["accepted"]
        until(
            lambda: (
                bool(step())
                and e.evidence()["hold_acknowledged"]
                and e.evidence()["pose_aligned"]
                and e.evidence()["live_input"].get("cursor_intent_revision") == a.run["intent_revision"]
            )
        )
        result = a.handle(
            command("resume", **ids(), expected_intent_revision=a.run["intent_revision"]), "owner"
        )
        assert result["accepted"], result
        try:
            until(
                lambda: bool(step()) and a.run["status"] == "running" and e.evidence()["active_acknowledged"]
            )
        except AssertionError as error:
            raise AssertionError(
                {
                    "status": a.run["status"],
                    "cause": a.run.get("first_cause"),
                    "native_state": io.control.live_snapshot().get("state"),
                }
            ) from error

    try:
        assert start(a, "physical-live-teleop")["accepted"]
        until(lambda: bool(step()) and a.run.get("native_live_at") is not None)
        frozen_deadline = a.run["deadline"]
        frozen_workspace = copy.deepcopy(e.evidence()["live_input"]["workspace"])
        resume()
        grant = a.handle(dict(op="grant", **ids()), "owner")
        src = source(grant=grant["grant_id"])
        target = {
            k: b["max"] if abs(b["max"] - frozen_workspace["reference"][k]) > 1e-6 else b["min"]
            for k, b in frozen_workspace["bounds"].items()
        }
        packet = dict(
            op="input",
            **ids(),
            target=target,
            body={"x.vel": 0.05, "y.vel": 0.0, "theta.vel": 0.0, "lift_axis.vel": 0},
            source=src,
            grant_id=grant["grant_id"],
            seq=1,
        )
        admitted = a.handle(packet, "owner")
        assert admitted["accepted"], admitted
        until(lambda: bool(step()) and e.evidence()["live_input"].get("last_input_seq") == 1)
        assert io.control.live_snapshot()["source"] == src
        assert io.control.live_snapshot()["body"]["x.vel"] == 0.05
        received_samples = [
            x["_am1_local_control"]["live_input"]
            for x in io.actions
            if x["_am1_local_control"].get("live_input")
        ]
        assert received_samples and all(
            s["valid_until_monotonic_s"] == grant["expires"] for s in received_samples
        )
        until(lambda: bool(step()) and a.run["status"] == "paused")
        assert a.run["deadline"] == frozen_deadline
        resume()
        new_grant = a.handle(dict(op="grant", **ids()), "owner")
        stale = dict(
            packet,
            **ids(),
            seq=2,
            grant_id=new_grant["grant_id"],
            source=dict(src, grant_id=new_grant["grant_id"]),
        )
        assert not a.handle(stale, "owner")["accepted"]
        fresh = source(2, grant=new_grant["grant_id"])
        valid = dict(
            stale, source=fresh, body={"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0, "lift_axis.vel": 0}
        )
        admitted = a.handle(valid, "owner")
        assert admitted["accepted"], admitted
        until(lambda: bool(step()) and e.evidence()["live_input"].get("last_input_seq") == 2)
        assert io.control.live_snapshot()["workspace"] == frozen_workspace
        assert a.run["deadline"] == frozen_deadline
    finally:
        if a.run:
            a.handle(command("stop", run_id=a.run["run_id"]), "owner")
        if e.worker:
            e.worker.join(2)
        a.close()


def test_real_protected_host_launch_enables_only_newmode_required_acquisition_gate(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from lerobot.robots.alohamini import alohamini_host as host
    from tools import am1_physical_executor as m

    state, runs = tmp_path / "state", tmp_path / "runs"
    state.mkdir()
    runs.mkdir()
    provider = object.__new__(m.ProtectedHostRun)
    provider.config = {
        "physical_state_directory": str(state),
        "run_root": str(runs),
        "host_head": "a" * 40,
        "host_repository": str(tmp_path),
        "host_python": "unused",
        "camera_python": "unused",
        "camera_repository": str(tmp_path),
        "camera_config": str(tmp_path / "config"),
        "camera_credentials": str(tmp_path / "credentials"),
        "camera_binary": str(tmp_path / "go2rtc"),
        "camera_state": str(tmp_path / "camera"),
    }
    provider.observation = SimpleNamespace()
    monkeypatch.setattr(provider, "_validate_source", lambda: None)
    monkeypatch.setattr(m, "wait_required_camera", lambda *args: None)
    captured = []

    def owned(name, cwd, command, environment, *args, **kwargs):
        if "host" in name:
            captured.append((command, environment))
            raise OSError("injected stop before actual host launch")
        return SimpleNamespace()

    monkeypatch.setattr(m, "OwnedUnit", owned)
    run_id, incarnation = str(uuid4()), str(uuid4())
    try:
        with pytest.raises(OSError, match="injected stop"):
            provider.start(run_id, incarnation, RECIPES["physical-live-teleop"], lambda: None)
        command, environment = captured[0]
        for key in ("AM1_VISIBLE_CONTROL_RECIPE", "AM1_SCRIPTED_PREPARE"):
            monkeypatch.setenv(key, environment.get(key, ""))
        assert environment.get("AM1_SCRIPTED_PREPARE") == "1", (
            "real launch omitted required newmode acquisition gate"
        )
        assert environment["AM1_VISIBLE_CONTROL_RECIPE"] == "physical-live-teleop"
        args = host.make_parser().parse_args(command[4:])
        assert host.visible_control_binding(args) == {
            "run_id": run_id,
            "host_incarnation": incarnation,
            "recipe": "physical-live-teleop",
        }
    finally:
        for stream in (getattr(provider, "host_log", None), getattr(provider, "camera_log", None)):
            if stream:
                stream.close()


@pytest.mark.parametrize("stale_raw", [False, True])
def test_real_host_codec_and_client_preserve_original_twelve_raw_reads(tmp_path, monkeypatch, stale_raw):
    import time

    from lerobot.robots.alohamini import alohamini_client as client_module, alohamini_host as host
    from lerobot.robots.alohamini.config_alohamini import AlohaMiniClientConfig
    from tools.am1_physical_executor import FeedbackUnavailable, read_physical_sample

    now = time.monotonic()
    read_started, completed = now - 0.05, now - 0.02
    positions = {key: float(index) for index, key in enumerate(KEYS)}
    raw_feedback = {
        "version": 1,
        "positions": {
            key: {
                "raw": 1800 + index,
                "normalized": positions[key],
                "read_started_at_monotonic_s": now - 1.1 if stale_raw and index == 0 else read_started + 0.01,
                "read_completed_at_monotonic_s": completed - 0.001,
            }
            for index, key in enumerate(KEYS)
        },
    }
    run_id, incarnation = str(uuid4()), str(uuid4())
    control = host.AM1LocalControl(
        run_id=run_id, host_incarnation=incarnation, live=True, live_recipe="physical-live-teleop"
    )
    observation = {
        **positions,
        "_am1_arm_feedback": raw_feedback,
        "x.vel": 0.0,
        "y.vel": 0.0,
        "theta.vel": 0.0,
    }
    control.annotate(observation, acquired_at=completed)
    observation[host.AM1_LOCAL_FEEDBACK_KEY]["read_started_at_monotonic_s"] = read_started
    parts = host.build_observation_multipart(observation, ())
    client = client_module.AlohaMiniClient(
        AlohaMiniClientConfig(
            remote_ip="127.0.0.1", robot_model="alohamini1", cameras={}, calibration_dir=tmp_path
        )
    )
    client._is_connected = True
    client._last_response_request_sent_at = now - 0.06
    monkeypatch.setattr(client, "_poll_and_get_latest_message", lambda: parts)
    if stale_raw:
        with pytest.raises(FeedbackUnavailable, match="expired"):
            read_physical_sample(client, run_id, incarnation, -1)
    else:
        sample = read_physical_sample(client, run_id, incarnation, -1)
        # The normal public state deliberately filters extras; the retained
        # authenticated native marker carries these exact original read facts.
        assert "_am1_arm_feedback" not in client.last_remote_state
        assert sample["arm_feedback"] == raw_feedback
        assert sample["ack"]["arm_feedback"] == raw_feedback
        assert sample["observation"]["_am1_arm_feedback"] == raw_feedback
        assert sample["at"] == client._last_response_request_sent_at
        assert sample["arm_feedback"] is not raw_feedback


@pytest.mark.parametrize("velocity", [0, 17])
def test_real_codec_retains_original_lift_velocity_for_actual_body_zero(tmp_path, monkeypatch, velocity):
    import time

    from lerobot.robots.alohamini import alohamini_host as host
    from lerobot.robots.alohamini.alohamini_client import AlohaMiniClient
    from lerobot.robots.alohamini.config_alohamini import AlohaMiniClientConfig
    from tools.am1_leader_adapter import native_frame_ack
    from tools.am1_physical_executor import read_physical_sample

    now = time.monotonic()
    original = {
        "version": 1,
        "present_velocity_raw": velocity,
        "height_mm": 23.0,
        "sample_monotonic_s": now - 0.06,
        "request_duration_s": 0.01,
    }
    run_id, incarnation = str(uuid4()), str(uuid4())
    control = host.AM1LocalControl(
        run_id=run_id, host_incarnation=incarnation, live=True, live_recipe="physical-live-teleop"
    )
    observation = {
        **dict.fromkeys(KEYS, 7.0),
        "x.vel": 0.0,
        "y.vel": 0.0,
        "theta.vel": 0.0,
        "lift_axis.vel": velocity,
        "lift_axis.height_mm": 23.0,
        "_am1_lift_feedback": original,
    }
    control.annotate(observation, acquired_at=now - 0.02)
    observation[host.AM1_LOCAL_FEEDBACK_KEY]["read_started_at_monotonic_s"] = now - 0.04
    parts = host.build_observation_multipart(observation, ())
    client = AlohaMiniClient(
        AlohaMiniClientConfig(
            remote_ip="127.0.0.1", robot_model="alohamini1", cameras={}, calibration_dir=tmp_path
        )
    )
    client._is_connected = True
    client._last_response_request_sent_at = now - 0.05
    monkeypatch.setattr(client, "_poll_and_get_latest_message", lambda: parts)
    sample = read_physical_sample(client, run_id, incarnation, -1)
    assert "lift_axis.vel" not in client.last_remote_state
    assert sample["body_feedback"]["lift_axis.vel"] == velocity
    assert sample["lift_feedback"] == sample["ack"]["lift_feedback"] == original
    assert sample["timing"]["lift_feedback"] == original
    assert sample["at"] == original["sample_monotonic_s"] - original["request_duration_s"]
    src = {"source_id": str(uuid4()), "sample_seq": 1, "grant_id": str(uuid4())}
    snapshot = {
        "run": {
            "live_input": {
                "last_input_seq": 1,
                "source": src,
                "body": {"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0, "lift_axis.vel": 0},
            }
        },
        "evidence": {
            "active_acknowledged": True,
            "feedback": True,
            "native_ack": True,
            "measured_body": sample["body_feedback"],
        },
    }
    assert native_frame_ack(snapshot, 1, src, body_zero=True) is (velocity == 0)


def test_old_original_lift_feedback_cannot_be_retimed_by_a_new_host_envelope():
    from tests.robots.test_am1_physical_executor import client_sample
    from tools.am1_physical_executor import FeedbackUnavailable, read_physical_sample

    client = client_sample()
    observation = client.get_observation()
    observation.update({"lift_axis.vel": 0, "lift_axis.height_mm": 23.0})
    client.latest_raw_observation_keys = frozenset(observation)
    client.latest_am1_local_feedback["lift_feedback"] = {
        "version": 1,
        "present_velocity_raw": 0,
        "height_mm": 23.0,
        "sample_monotonic_s": 8.9,
        "request_duration_s": 0.01,
    }
    with pytest.raises(FeedbackUnavailable, match="lift.*expired"):
        read_physical_sample(client, "run", "host", 4, clock=lambda: 10.0)


def test_real_codec_core_ipc_https_wss_preserves_raw12_lift_and_actual_zero(tmp_path, monkeypatch):
    import asyncio
    import json
    import ssl
    import subprocess
    import time
    from types import SimpleNamespace

    from aiohttp import web

    from lerobot.robots.alohamini import alohamini_client as client_module, alohamini_host as host
    from lerobot.robots.alohamini.config_alohamini import AlohaMiniClientConfig
    from tests.robots.am1_session_fixture import OPENSSL
    from tools.am1_leader_adapter import SessionClient, native_frame_ack
    from tools.am1_physical_executor import read_physical_sample
    from tools.am1_session_ipc import RESPONSE_LIMIT, OwnerServer, encode
    from tools.am1_session_service import Gateway

    a, e, ids = authority_lane(tmp_path / "authority")
    e._epoch, e._epoch_mode = 0, "active"
    src, zero = source(), {"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0, "lift_axis.vel": 0}
    raw = {
        "version": 1,
        "positions": {
            key: {
                "raw": 2000 + index,
                "normalized": 0.0,
                "read_started_at_monotonic_s": 9.96,
                "read_completed_at_monotonic_s": 9.98,
            }
            for index, key in enumerate(KEYS)
        },
    }
    lift = {
        "version": 1,
        "present_velocity_raw": 0,
        "height_mm": 23.0,
        "sample_monotonic_s": 9.94,
        "request_duration_s": 0.01,
    }
    control = host.AM1LocalControl(
        run_id=e.run_id, host_incarnation=e.host_incarnation, live=True, live_recipe="physical-live-teleop"
    )
    control.state, control.epoch, control.intent_revision = "active", 0, 0
    control._live_summary = {
        **e.sample["ack"]["live_input"],
        "state": "active",
        "source": src,
        "last_input_seq": 1,
        "body": zero,
        "accepted_target": dict.fromkeys(KEYS, 0.0),
        "cursor": dict.fromkeys(KEYS, 0.0),
        "reference": workspace()["reference"],
        "live_admitted_at_monotonic_s": 9.0,
        "live_deadline_monotonic_s": 309.0,
    }
    observation = {
        **dict.fromkeys(KEYS, 0.0),
        **zero,
        "lift_axis.height_mm": 23.0,
        "_am1_arm_feedback": raw,
        "_am1_lift_feedback": lift,
    }
    control.annotate(observation, acquired_at=9.98)
    observation[host.AM1_LOCAL_FEEDBACK_KEY]["read_started_at_monotonic_s"] = 9.95
    parts = host.build_observation_multipart(observation, ())
    native_client = client_module.AlohaMiniClient(
        AlohaMiniClientConfig(
            remote_ip="127.0.0.1", cameras={}, robot_model="alohamini1", calibration_dir=tmp_path
        )
    )
    native_client._is_connected = True
    native_client._last_response_request_sent_at = 9.95
    monkeypatch.setattr(native_client, "_poll_and_get_latest_message", lambda: parts)
    monkeypatch.setattr(
        client_module, "time", SimpleNamespace(monotonic=lambda: 10.0, perf_counter=time.perf_counter)
    )
    e.sample = read_physical_sample(native_client, e.run_id, e.host_incarnation, -1, clock=lambda: 10.0)
    assert len(json.dumps(e.sample["ack"]).encode()) > 4096
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
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
            "subjectAltName=IP:127.0.0.1,DNS:localhost",
        ],
        check=True,
        capture_output=True,
    )

    async def scenario():
        owner = OwnerServer(tmp_path / "ipc-owner")
        owner.authority.close()
        owner.authority = a
        owner.stopping.set()  # Presentation-only fixed-clock fixture; no actuation/tick worker.
        gateway = Gateway(owner.directory, tmp_path / "auth")
        enrolled = gateway.auth.enroll(gateway.auth.pair(control=True))
        a.controller["device_id"] = enrolled["device_id"]
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert, key)
        runner = web.AppRunner(gateway.app, access_log=None, shutdown_timeout=1)
        client = None
        try:
            await owner.start()
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=tls)
            await site.start()
            gateway.origin = f"https://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
            storage = tmp_path / "storage.json"
            storage.write_text(
                json.dumps(
                    {
                        "cookies": [
                            {
                                "name": "__Host-am1-device",
                                "value": enrolled["token"],
                                "domain": "127.0.0.1",
                                "path": "/",
                                "secure": True,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            client = SessionClient(gateway.origin, cert, storage)
            await client.attach()
            await asyncio.sleep(0.12)  # Also exercise the production publication path after initial ACK.
            snapshot = client.snapshot
            ack = snapshot["evidence"]["accepted_host_action"]
            assert ack["arm_feedback"] == raw and ack["lift_feedback"] == lift
            assert "live_input" not in ack  # Only the duplicate workspace/summary is removed.
            assert snapshot["run"]["live_input"]["workspace"] == workspace()
            assert snapshot["run"]["live_input"]["reference"] == workspace()["reference"]
            assert snapshot["run"]["deadline"] == 309.0
            assert snapshot["run"]["live_input"]["source"] == src
            assert ack["run_id"] == ids["run_id"] and ack["host_incarnation"] == ids["host_incarnation"]
            assert ack["read_started_at_monotonic_s"] == 9.95 and ack["acquired_at_monotonic_s"] == 9.98
            assert native_frame_ack(snapshot, 1, src, body_zero=True)
            assert len(encode({"kind": "snapshot", "snapshot": snapshot})) < RESPONSE_LIMIT
        finally:
            if client:
                await client.close()
            await runner.cleanup()
            a.run = None
            await owner.close()

    asyncio.run(scenario())
