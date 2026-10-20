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


@pytest.mark.parametrize("mismatch", ["run", "host", "intent", "epoch", "binding", "sequence", "unusable"])
def test_expiry_cache_rejects_noncurrent_native_poll(mismatch):
    e, identity = admitted_executor()
    e._epoch = 2
    e.sample["sequence"] = 6
    e.sample["ack"].update(epoch=2, live_input={"state": "expired", "last_input_seq": 1})
    polled_control = (e.revision, e._epoch, tuple(sorted(e._live_binding.items())))
    if mismatch == "run":
        e.sample["ack"]["run_id"] = str(uuid4())
    elif mismatch == "host":
        e.sample["ack"]["host_incarnation"] = str(uuid4())
    elif mismatch == "intent":
        e.set_intent_revision(identity["intent_revision"] + 1)
    elif mismatch == "epoch":
        e._epoch += 1
    elif mismatch == "binding":
        assert e.bind_control(**dict(identity, connection_generation=str(uuid4())))
    elif mismatch == "sequence":
        e.sample["sequence"] = 5
    e._retain_live_expiry(poll_control=polled_control, previous_sequence=5, usable=mismatch != "unusable")
    assert e._live_expired is None
    assert e._live_target is None


@pytest.mark.parametrize("held_sequence", [5, 7])
def test_current_expiry_survives_same_revision_held_ack_until_core_transition(held_sequence):
    e, _ = admitted_executor()
    e._epoch = 2
    expired = {
        "state": "expired", "cursor_intent_revision": 0, "cursor_feedback_sequence": 4,
        "last_input_seq": 1, "valid_until_monotonic_s": 9.99,
        "expiry_detected_at_monotonic_s": 10.0,
    }
    e.sample["sequence"] = 6
    e.sample["ack"].update(epoch=2, live_input=expired)
    polled_control = (e.revision, e._epoch, tuple(sorted(e._live_binding.items())))
    e._retain_live_expiry(poll_control=polled_control, previous_sequence=5, usable=True)
    assert e._live_expired == expired
    assert e._live_expired is not expired
    e._epoch, e._epoch_mode = 3, "pause"
    e._aligned = [9.7, 9.8, 10.0]
    e.sample["sequence"] = held_sequence
    e.sample["ack"].update(
        state="paused", epoch=3,
        live_input=dict(expired, state="held", cursor_feedback_sequence=held_sequence - 1),
    )
    e._retain_live_expiry(
        poll_control=(e.revision, e._epoch, tuple(sorted(e._live_binding.items()))),
        previous_sequence=6, usable=True,
    )
    view = e.evidence()
    assert view["hold_acknowledged"] and view["pose_aligned"]
    assert view["accepted_host_action"]["live_input"]["state"] == "held"
    assert view["live_input"] == expired
    assert e._live_expired == expired
    e.set_intent_revision(1)
    assert e._live_expired is None


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


@pytest.mark.parametrize("queued_expiry", [pytest.param(False, id="ordinary"), pytest.param(True, id="queued-expiry")])
@pytest.mark.parametrize("endpoint_rest", [False, True])
def test_real_core_executor_native_frame_expiry_and_fresh_rejoin_preserve_run(tmp_path, endpoint_rest, queued_expiry):
    import threading
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
            if endpoint_rest:
                from lerobot.motors import MotorCalibration
                from lerobot.motors.feetech import FeetechMotorsBus
                from lerobot.robots.alohamini.alohamini import AlohaMini

                bus = self.robot.left_bus
                name = "arm_left_shoulder_lift"
                bus.calibration[name] = MotorCalibration(2, 0, 515, 848, 3223)
                normalizer = FeetechMotorsBus("unused-endpoint-rest", bus.motors, bus.calibration)
                bus._normalize, bus._unnormalize = normalizer._normalize, normalizer._unnormalize
                bus.raw[name], bus.goals[name] = 3225, 3223
                self.robot.hold_follower_arms = AlohaMini.hold_follower_arms.__get__(self.robot)
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
            self.queue_expiry = False
            self.expiry_queued = threading.Event()
            self.release_expiry = threading.Event()
            self.original_expiry = None

        def poll(self, previous):
            self.control.check_live(self.robot)
            observation = self.robot.get_observation()
            at = time.monotonic()
            self.control.annotate(observation, acquired_at=at)
            marker = observation[host.AM1_LOCAL_FEEDBACK_KEY]
            sample = {
                "positions": self.robot.get_observation(),
                "sequence": marker["observation_id"],
                "at": at,
                "ack": marker,
                "observation": observation,
                "provenance": "protected-host/measured-feedback",
            }
            if self.queue_expiry and marker.get("live_input", {}).get("state") == "expired":
                self.queue_expiry = False
                self.original_expiry = copy.deepcopy(sample)
                self.expiry_queued.set()
                assert self.release_expiry.wait(2), "test did not release original expired native sample"
            return sample

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
        if endpoint_rest:
            key = "arm_left_shoulder_lift.pos"
            assert frozen_workspace["reference"][key] == pytest.approx(100 + 400 / 2375)
            assert e.evidence()["live_input"]["cursor"][key] == 100
            assert io.robot.left_bus.goals[key.removesuffix(".pos")] == 3223
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
        io.queue_expiry = queued_expiry
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
        if queued_expiry:
            # Retain the original native expiry in flight while Core advances
            # the hold revision; do not synthesize or retimestamp feedback.
            assert io.expiry_queued.wait(1), "native expiry was not captured"
        until(lambda: bool(step()) and a.run["status"] == "paused")
        assert a.run["deadline"] == frozen_deadline
        if queued_expiry:
            expected_revision = a.run["intent_revision"]
            expired = io.original_expiry["ack"]["live_input"]
            assert io.original_expiry["ack"]["intent_revision"] < expected_revision
            assert e._live_expired is None  # Core's new hold cleared the prior cache.
            io.release_expiry.set()

            def genuine_current_hold():
                step()
                with e.mutex:
                    ack = copy.deepcopy((e.sample or {}).get("ack", {}))
                return (
                    ack.get("state") == "paused"
                    and ack.get("intent_revision") == expected_revision
                    and ack.get("live_input", {}).get("cursor_intent_revision") == expected_revision
                    and e.evidence()["hold_acknowledged"]
                    and e.evidence()["pose_aligned"]
                )

            until(genuine_current_hold)
            with e.mutex:
                raw_ack = copy.deepcopy(e.sample["ack"])
            held = raw_ack["live_input"]
            assert held["state"] == "held"
            assert held["cursor_feedback_sequence"] > expired["cursor_feedback_sequence"]
            assert held["workspace"] == frozen_workspace
            assert held["reference"] == expired["reference"]
            assert held["live_deadline_monotonic_s"] == frozen_deadline
            assert e._live_target is None and e.desired == "hold"
            assert held["last_input_seq"] == expired["last_input_seq"] == 1
            assert held["valid_until_monotonic_s"] == expired["valid_until_monotonic_s"] == grant["expires"]
            assert held["expiry_detected_at_monotonic_s"] == expired["expiry_detected_at_monotonic_s"]
            projected = e.evidence()["live_input"]
            assert projected["cursor_intent_revision"] == expected_revision, {
                "raw_cursor_revision": held["cursor_intent_revision"],
                "published_cursor_revision": projected["cursor_intent_revision"],
                "raw_cursor_sequence": held["cursor_feedback_sequence"],
                "published_cursor_sequence": projected["cursor_feedback_sequence"],
                "raw_state": held["state"], "published_state": projected["state"],
            }
            assert a.snapshot()["run"]["live_input"]["cursor_intent_revision"] == expected_revision
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
        io.release_expiry.set()
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


@pytest.mark.parametrize("offset", [-0.001, 0.0, 0.001], ids=["before", "at", "after"])
def test_authority_preserves_runtime_expiry_nonadmission_after_delayed_evidence(tmp_path, monkeypatch, offset):
    a, e, ids = authority_lane(tmp_path)
    now = [10.0]
    a.clock = e.clock = lambda: now[0]
    a._teleop_view = {"run_id": e.run_id, "qualified_until": 10.8}

    def frame(grant, sample_seq, network_seq):
        return dict(op="input", **ids, grant_id=grant["grant_id"], seq=network_seq,
                    source=source(sample_seq, grant=grant["grant_id"]),
                    target=dict.fromkeys(KEYS, 5.0),
                    body={"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0, "lift_axis.vel": 0})

    try:
        first_grant = a._handle(dict(op="grant", **ids), "pc")
        first_frame = frame(first_grant, 1, 1)
        assert a._handle(first_frame, "pc")["accepted"] is True
        original_target, original_source = copy.deepcopy(e._live_target), copy.deepcopy(e._teleop_source)
        original_workspace = copy.deepcopy(e.sample["ack"]["live_input"]["workspace"])
        now[0] = 10.01
        grant = a._handle(dict(op="grant", **ids), "pc")
        candidate = frame(grant, 2, 2)
        evidence = e.evidence
        delayed = [False]

        def delayed_evidence():
            if not delayed[0]:
                delayed[0] = True
                # Same Pi clock advances AFTER Core captured its command now.
                now[0] = grant["expires"] + offset
            return evidence()

        monkeypatch.setattr(e, "evidence", delayed_evidence)
        result = a._handle(candidate, "pc")
        assert delayed == [True]
        assert a.run["status"] == "running" and a.run["intent_revision"] == 0
        assert a.run["deadline"] == 309.0 and a.run["native_live_at"] == 9.0
        assert e.sample["ack"]["live_input"]["workspace"] == original_workspace
        if offset < 0:
            assert result["accepted"] is True and result["effect_admitted"] is True
            assert e._live_target["valid_until_monotonic_s"] == grant["expires"]
        else:
            assert result == {"accepted": False, "status": "refused", "effect_admitted": False,
                              "reason": "expired grant"}
            assert e._live_target == original_target and e._teleop_source == original_source
            assert e._live_last_seq == a.connection["seq"] == 1
            assert a.run["input_source"] == first_frame["source"]
            assert a.run["intent"] == first_frame["target"]
            fresh_grant = a._handle(dict(op="grant", **ids), "pc")
            assert a._handle(candidate, "pc")["accepted"] is False  # Original grant/frame stays refused.
            replay = frame(fresh_grant, 1, 3)
            assert a._handle(replay, "pc")["accepted"] is False  # Accepted source history never resets.
            fresh = frame(fresh_grant, 3, 3)
            assert a._handle(fresh, "pc")["accepted"] is True
            assert e._live_target["source"] == fresh["source"]
            assert e._live_target["valid_until_monotonic_s"] == fresh_grant["expires"]
            assert a.run["deadline"] == 309.0 and a.run["recovery"] is None
    finally:
        a.run = None
        a.close()


@pytest.mark.parametrize("invalid", [
    "finishing", "lifecycle", "inactive", "run", "host", "revision", "binding", "replay",
    "target", "body", "source", "workspace", "far_future", "boolean_expiry", "infinite_expiry", "fault",
])
def test_runtime_expiry_cannot_reclassify_any_other_mailbox_refusal(invalid):
    e, ids = admitted_executor()
    e.clock = lambda: 10.3
    src = source()
    options = dict(**ids, target=dict.fromkeys(KEYS, 5.0),
                   body={"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0, "lift_axis.vel": 0},
                   source=src, grant_id=src["grant_id"], seq=1, valid_until_monotonic_s=10.2)
    if invalid == "finishing":
        e.finish_status = "stopped"
    elif invalid == "lifecycle":
        e._lifecycle["phase"] = "starting"
    elif invalid == "inactive":
        e.desired = "hold"
    elif invalid in {"run", "host", "revision"}:
        options[{"run": "run_id", "host": "host_incarnation", "revision": "intent_revision"}[invalid]] = (
            1 if invalid == "revision" else str(uuid4()))
    elif invalid == "binding":
        options["connection_generation"] = str(uuid4())
    elif invalid == "replay":
        e._live_last_seq = 1
    elif invalid == "target":
        options["target"] = {"unknown": 0}
    elif invalid == "body":
        options["body"] = {"unknown": 0}
    elif invalid == "source":
        options["source"] = dict(src, sample_seq=True)
    elif invalid == "workspace":
        e.sample["ack"]["live_input"]["workspace"] = {}
    elif invalid in {"far_future", "boolean_expiry", "infinite_expiry"}:
        options["valid_until_monotonic_s"] = {"far_future": 10.6, "boolean_expiry": True,
                                             "infinite_expiry": float("inf")}[invalid]
    else:
        e._fault = "original protected fault"
    assert e.apply(**options, classify_expiry=True) is False
    assert e._live_target is None and e._teleop_source is None
    assert e._live_last_seq == (1 if invalid == "replay" else -1)


@pytest.mark.parametrize("kind", ["runtime", "io", "same_name"])
def test_authority_runtime_expiry_mapping_does_not_catch_foreign_errors(tmp_path, monkeypatch, kind):
    a, e, ids = authority_lane(tmp_path)
    a._teleop_view = {"run_id": e.run_id, "qualified_until": 10.4}
    error_type = {"runtime": RuntimeError, "io": OSError,
                  "same_name": type("LiveInputExpiredBeforeAdmissionError", (RuntimeError,), {})}[kind]
    primary = error_type("original unrelated mailbox error")

    def failed(*args, **kwargs):
        raise primary

    try:
        grant = a._handle(dict(op="grant", **ids), "pc")
        packet = dict(op="input", **ids, grant_id=grant["grant_id"], seq=1,
                      source=source(grant=grant["grant_id"]), target=dict.fromkeys(KEYS, 5.0),
                      body={"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0, "lift_axis.vel": 0})
        monkeypatch.setattr(e, "apply", failed)
        with pytest.raises(error_type) as caught:
            a._handle(packet, "pc")
        assert caught.value is primary
        assert e._live_target is None and e._teleop_source is None
    finally:
        a.run = None
        a.close()


def test_runtime_validation_delay_cannot_make_original_far_future_expiry_valid(monkeypatch):
    from tools import am1_physical_executor as physical

    e, ids = admitted_executor()
    now = [10.0]
    e.clock = lambda: now[0]
    src = source()
    original = physical.validate_source

    def delayed(*args, **kwargs):
        value = original(*args, **kwargs)
        now[0] = 10.1
        return value

    monkeypatch.setattr(physical, "validate_source", delayed)
    assert e.apply(dict.fromkeys(KEYS, 5.0), **ids,
                   body={"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0, "lift_axis.vel": 0},
                   source=src, grant_id=src["grant_id"], seq=1,
                   valid_until_monotonic_s=10.3) is False
    assert e._live_target is None and e._teleop_source is None and e._live_last_seq == -1


@pytest.mark.parametrize("mode", ["default", "false", "true", "truthy_integer", "truthy_string"])
def test_runtime_expiry_reporting_requires_explicit_trusted_core_opt_in(mode):
    from tools.am1_physical_executor import LiveInputExpiredBeforeAdmissionError

    e, ids = admitted_executor()
    e.clock = lambda: 10.3
    src = source()
    options = dict(**ids, target=dict.fromkeys(KEYS, 5.0), source=src, grant_id=src["grant_id"],
                   seq=1, valid_until_monotonic_s=10.2,
                   body={"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0, "lift_axis.vel": 0})
    if mode != "default":
        options["classify_expiry"] = {"false": False, "true": True,
                                      "truthy_integer": 1, "truthy_string": "true"}[mode]
    if mode == "true":
        with pytest.raises(LiveInputExpiredBeforeAdmissionError):
            e.apply(**options)
    else:
        assert e.apply(**options) is False
    assert e._live_target is None and e._teleop_source is None and e._live_last_seq == -1


@pytest.mark.parametrize("status", ["paused", "running", "resuming"])
def test_thermal_history_native_core_uses_original_local_episode_even_from_hold(tmp_path, status):
    a, e, ids = authority_lane(tmp_path)
    now = [10.]
    a.clock = e.clock = lambda: now[0]
    a.run.update(status=status, recovery_episodes=0, resume_ceiling=18.)
    e._execution = {"progress_s": 1., "complete": False, "fault": None}
    live = e.sample["ack"]["live_input"]
    live["reference"] = live["workspace"]["reference"]
    a._teleop_view = {"run_id": e.run_id, "qualified_until": 20.}
    e.sample["ack"]["thermal_history"] = {
        "version": 1, "raw_sample_at_monotonic_s": 9.98, "peaks_ready": False,
        "deficit_started_at_monotonic_s": 9.8, "deficit_ceiling_monotonic_s": 19.8,
        "hold_sample_at_monotonic_s": 9.97,
        "zero_read_started_at_monotonic_s": 9.97, "zero_read_completed_at_monotonic_s": 9.98,
    }
    e._live_target = {"old": "must be discarded"}
    try:
        evidence = e.evidence()
        assert evidence["feedback"] is False
        assert evidence["thermal_history"]["raw_sample_at_monotonic_s"] == 9.98
        a.tick()
        assert a.run["status"] == "recovering"
        assert a.run["recovery"] == {"started": 9.8, "ceiling": 19.8, "revision": 0}
        assert a.run["recovery_episodes"] == 1
        assert e._live_target is None and e.desired == "hold"
        original = dict(a.run["recovery"])
        now[0] = 10.1
        e.sample["at"] = now[0]
        e.sample["ack"]["thermal_history"]["raw_sample_at_monotonic_s"] = now[0]
        a.tick()
        assert a.run["recovery"] == original and a.run["recovery_episodes"] == 1
        assert a.run["deadline"] == 309.
    finally:
        a.run = None
        a.close()


def test_thermal_history_native_raw_cache_cannot_borrow_the_generic_one_second_budget():
    e, _ids = admitted_executor()
    e.sample["ack"]["thermal_history"] = {
        "version": 1, "raw_sample_at_monotonic_s": 9.499, "peaks_ready": True,
        "deficit_started_at_monotonic_s": None, "deficit_ceiling_monotonic_s": None,
        "hold_sample_at_monotonic_s": None,
        "zero_read_started_at_monotonic_s": None, "zero_read_completed_at_monotonic_s": None,
    }
    assert e.clock() - e.sample["at"] == 0.
    assert e.evidence()["feedback"] is False
    assert not e.dispatch(RECIPES["physical-live-teleop"])


@pytest.mark.parametrize("field,value", [
    ("raw_sample_at_monotonic_s", 10.1), ("raw_sample_at_monotonic_s", float("nan")),
    ("peaks_ready", 1), ("zero_read_completed_at_monotonic_s", 10.02),
    ("hold_sample_at_monotonic_s", 9.499),
])
def test_thermal_history_native_malformed_or_long_zero_proof_never_qualifies(field, value):
    e, _ids = admitted_executor()
    proof = {
        "version": 1, "raw_sample_at_monotonic_s": 9.99, "peaks_ready": True,
        "deficit_started_at_monotonic_s": 9.8, "deficit_ceiling_monotonic_s": 19.8,
        "hold_sample_at_monotonic_s": 9.97,
        "zero_read_started_at_monotonic_s": 9.97, "zero_read_completed_at_monotonic_s": 9.98,
    }
    proof[field] = value
    e.sample["ack"]["thermal_history"] = proof
    assert e.evidence()["feedback"] is False



def test_thermal_history_proof_cannot_disappear_after_the_native_owner_enabled_it():
    e, _ids = admitted_executor()
    e.sample["ack"]["thermal_history"] = {
        "version": 1, "raw_sample_at_monotonic_s": 9.99, "peaks_ready": True,
        "deficit_started_at_monotonic_s": None, "deficit_ceiling_monotonic_s": None,
        "hold_sample_at_monotonic_s": None,
        "zero_read_started_at_monotonic_s": None, "zero_read_completed_at_monotonic_s": None,
    }
    assert e.evidence()["feedback"]
    del e.sample["ack"]["thermal_history"]
    assert not e.evidence()["feedback"] and e.evidence()["fault"]



@pytest.mark.parametrize("case", ["late_core", "revision", "native_deadline", "three_episodes", "unqualified_new_anchor", "uncertain"])
def test_thermal_history_core_budget_cannot_restart_and_distinct_episodes_remain_bounded(tmp_path, case):
    a, e, ids = authority_lane(tmp_path)
    now = [10.]
    a.clock = e.clock = lambda: now[0]
    a.run.update(status="paused", recovery_episodes=0, resume_ceiling=18.)
    e._execution = {"progress_s": 1., "complete": False, "fault": None}
    live = e.sample["ack"]["live_input"]
    live["reference"] = live["workspace"]["reference"]
    a._teleop_view = {"run_id": e.run_id, "qualified_until": 400.}
    def proof(started, *, ready=False):
        e.sample["at"] = now[0]
        a.controller["expires"] = now[0] + 2
        e.sample["ack"]["thermal_history"] = {
            "version": 1, "raw_sample_at_monotonic_s": now[0] - .01, "peaks_ready": ready,
            "deficit_started_at_monotonic_s": started,
            "deficit_ceiling_monotonic_s": min(started + 10, a.run["deadline"]) if started is not None else None,
            "hold_sample_at_monotonic_s": now[0] - .02,
            "zero_read_started_at_monotonic_s": now[0] - .02,
            "zero_read_completed_at_monotonic_s": now[0] - .01,
        }
    try:
        if case == "late_core":
            now[0] = 20.
            proof(9.8)
            a.tick()
            assert a.run["requested_terminal"] == "faulted"
            assert a.run["first_cause"] == "recovery_timeout"
        elif case == "uncertain":
            proof(9.8)
            a.run["uncertain"] = True
            a.tick()
            assert a.run["requested_terminal"] == "faulted"
        elif case == "unqualified_new_anchor":
            proof(9.8)
            a.tick()
            original = dict(a.run["recovery"])
            now[0] = 10.2
            proof(10.1)
            a.tick()
            assert a.run["requested_terminal"] == "faulted"
            assert a.run["recovery_episodes"] == 1 and a.run["recovery"] == original
            assert a.run["first_cause"] == "feedback_loss"
        elif case == "native_deadline":
            now[0] = 308.9
            proof(308.8)
            a.tick()
            assert a.run["recovery"] == {"started": 308.8, "ceiling": 309., "revision": 0}
            assert a.run["deadline"] == 309.
        elif case == "revision":
            proof(9.8)
            a.tick()
            a.run["intent_revision"] = 1
            e.set_intent_revision(1)
            e.sample["ack"]["intent_revision"] = 1
            now[0] = 10.1
            proof(9.8)
            a.tick()
            assert a.run["recovery"] == {"started": 9.8, "ceiling": 19.8, "revision": 1}
            assert a.run["recovery_episodes"] == 1
        else:
            for index in range(4):
                now[0] = 10. + index
                proof(now[0] - .1)
                a.tick()
                assert a.run["recovery_episodes"] == index + 1
                if index == 3:
                    assert a.run["requested_terminal"] == "faulted"
                    break
                assert a.run["status"] == "recovering"
                # Completed actual active ACK is the only closure permitting a distinct episode.
                now[0] += .1
                proof(None, ready=True)
                e.sample["ack"]["thermal_history"].update(
                    deficit_started_at_monotonic_s=None, deficit_ceiling_monotonic_s=None,
                    hold_sample_at_monotonic_s=None, zero_read_started_at_monotonic_s=None,
                    zero_read_completed_at_monotonic_s=None,
                )
                e.desired, e._epoch_mode, e._epoch = "active", "active", 0
                a.run.update(status="running", recovery=None)
                a.tick()
                assert e.evidence()["active_acknowledged"]
    finally:
        a.run = None
        a.close()



def test_thermal_history_actual_worker_requires_new_qualified_hold_then_actual_active_ack():
    from tests.robots.test_am1_physical_executor import pending_native_worker

    now, robot, control, e, step, stop = pending_native_worker()
    frozen = copy.deepcopy(control.live_snapshot())
    started = now[0]
    def polled(ready, *, closed=False):
        measured = robot.get_observation()
        control.annotate(measured, acquired_at=now[0])
        proof = {
            "version": 1, "raw_sample_at_monotonic_s": now[0], "peaks_ready": ready,
            "deficit_started_at_monotonic_s": None if closed else started,
            "deficit_ceiling_monotonic_s": None if closed else started + 10,
            "hold_sample_at_monotonic_s": None if closed else now[0],
            "zero_read_started_at_monotonic_s": None if closed else now[0],
            "zero_read_completed_at_monotonic_s": None if closed else now[0],
        }
        marker = measured["_am1_local_feedback"]
        marker["thermal_history"] = proof  # Explicit synthetic metadata; actual owner proof tested separately.
        return {"sequence": marker["observation_id"], "at": now[0], "positions": robot.get_observation(),
                "ack": copy.deepcopy(marker), "observation": measured,
                "body_feedback": dict.fromkeys(("x.vel", "y.vel", "theta.vel", "lift_axis.vel"), 0.),
                "provenance": "protected-host/measured-feedback"}
    try:
        action = step(polled(False))
        assert action["_am1_local_control"]["mode"] == "pause"
        assert action["_am1_local_control"]["live_input"] is None
        assert e._live_target is None and control.state == "paused"
        assert not e.evidence()["feedback"] and not e.evidence()["active_acknowledged"]
        for offset in (.1, .2, .3):
            now[0] = started + offset
            step(polled(True))
        evidence = e.evidence()
        assert evidence["hold_acknowledged"] and evidence["pose_aligned"]
        assert not evidence["active_acknowledged"]
        assert e.dispatch(RECIPES["physical-live-teleop"])
        action = step(polled(True))
        assert action["_am1_local_control"]["mode"] == "active"
        assert action["_am1_local_control"]["live_input"] is None
        now[0] += .01
        step(polled(True, closed=True))
        assert e.evidence()["active_acknowledged"]
        assert control.live_snapshot()["workspace"] == frozen["workspace"]
        assert control.live_snapshot()["reference"] == frozen["reference"]
        assert control.live_snapshot()["live_deadline_monotonic_s"] == frozen["live_deadline_monotonic_s"]
    finally:
        stop()



@pytest.mark.parametrize("proof", [None, {}, {"version": True}, {"raw_sample_at_monotonic_s": 10.}])
def test_thermal_history_present_null_or_partial_proof_is_never_legacy_absence(proof):
    e, _ids = admitted_executor()
    e.sample["ack"]["thermal_history"] = proof
    assert not e.evidence()["feedback"] and e.evidence()["fault"]


@pytest.mark.parametrize("case", ["advancing", "true_future", "stale", "episode_expired"])
def test_thermal_history_core_consumes_original_proof_at_the_current_clock(tmp_path, monkeypatch, case):
    a, e, ids = authority_lane(tmp_path)
    now = [19.99 if case == "episode_expired" else 10.]
    a.clock = e.clock = lambda: now[0]
    a.run.update(status="paused", recovery_episodes=0, resume_ceiling=30.)
    a.controller["expires"] = 40.
    e._execution = {"progress_s": 1., "complete": False, "fault": None}
    e.sample["ack"]["live_input"]["reference"] = e.sample["ack"]["live_input"]["workspace"]["reference"]
    a._teleop_view = {"run_id": e.run_id, "qualified_until": 40.}
    original, called = e.evidence, []
    def delayed_evidence():
        if not called:
            called.append(True)
            now[0] = 20.02 if case == "episode_expired" else 10.02
            # Explicit synthetic metadata advances during the real Core evidence call.
            # Its original values are consumed unchanged, not retimestamped by Core.
            raw = now[0] + .001 if case == "true_future" else now[0] - (.501 if case == "stale" else .001)
            started = 10.01
            e.sample["at"] = now[0]
            e.sample["ack"]["thermal_history"] = {
                "version": 1, "raw_sample_at_monotonic_s": raw, "peaks_ready": False,
                "deficit_started_at_monotonic_s": started, "deficit_ceiling_monotonic_s": started + 10,
                "hold_sample_at_monotonic_s": None,
                "zero_read_started_at_monotonic_s": None, "zero_read_completed_at_monotonic_s": None,
            }
        return original()
    monkeypatch.setattr(e, "evidence", delayed_evidence)
    try:
        a.tick()
        assert called
        proof = e.sample["ack"]["thermal_history"]
        assert proof["deficit_started_at_monotonic_s"] == 10.01
        assert proof["deficit_ceiling_monotonic_s"] == 20.009999999999998
        assert a.run["deadline"] == 309.
        if case == "advancing":
            assert a.run["status"] == "recovering"
            assert a.run["recovery"] == {"started": 10.01, "ceiling": 20.009999999999998, "revision": 0}
            assert a.run["recovery_episodes"] == 1
            assert proof["raw_sample_at_monotonic_s"] == 10.019
        else:
            assert a.run["requested_terminal"] == "faulted"
            if case == "episode_expired":
                assert a.run["first_cause"] == "recovery_timeout"
    finally:
        a.run = None
        a.close()


@pytest.mark.parametrize("change", [None, "recent", "resident_fault", "live", "held", "startup", "finishing",
    "uncertain", "unknown_cleanup", "foreign_run", "foreign_host", "foreign_ack", "missing_row", "nonzero_row",
    "bool_row", "buses_open", "gain_restore", "missing_readback", "camera_active", "camera_foreign", "camera_failed",
    "reader_alive", "backend_alive", "bad_returncode", "worker_alive", "reconcile_alive", "malformed_cleanup", "invalid_source_identity"])
def test_thermal_terminal_cleanup_does_not_revalidate_old_raw_as_a_current_fault(change):
    from types import SimpleNamespace

    from tests.robots.test_am1_physical_executor import terminal_cleanup_outcome

    e, _ids = admitted_executor()
    now = 10.01 if change == "recent" else 10.6
    e.clock = lambda: now
    proof = {"version": 1, "raw_sample_at_monotonic_s": 9.98, "peaks_ready": True,
             "deficit_started_at_monotonic_s": None, "deficit_ceiling_monotonic_s": None,
             "hold_sample_at_monotonic_s": None, "zero_read_started_at_monotonic_s": None,
             "zero_read_completed_at_monotonic_s": None}
    e.sample["ack"]["thermal_history"] = proof
    outcome = terminal_cleanup_outcome(e.run_id, e.host_incarnation, "stopped")
    outcome["first_cause"] = outcome["readback"]["first_cause"] = None
    e._lifecycle.update(outcome, phase="terminal", run_id=e.run_id)
    if change == "resident_fault":
        e._fault = "RuntimeError: actual resident native fault"
    elif change in {"live", "held", "startup", "finishing"}:
        e._lifecycle["phase"] = "live" if change == "held" else change
        if change == "held":
            e.desired = "hold"
    elif change == "uncertain":
        e._lifecycle["uncertain"] = True
    elif change == "unknown_cleanup":
        e._lifecycle["cleanup"] = "physical_cleanup_unknown"
    elif change == "foreign_run":
        e._lifecycle["run_id"] = str(uuid4())
    elif change == "foreign_host":
        e._lifecycle["backend_incarnation"] = str(uuid4())
    elif change == "foreign_ack":
        e.sample["ack"]["host_incarnation"] = str(uuid4())
    elif change == "missing_row":
        e._lifecycle["readback"]["readback"]["readbacks"].pop()
    elif change in {"nonzero_row", "bool_row"}:
        e._lifecycle["readback"]["readback"]["readbacks"][0]["value"] = True if change == "bool_row" else 1
    elif change == "buses_open":
        e._lifecycle["readback"]["readback"]["buses_closed"] = False
    elif change == "gain_restore":
        e._lifecycle["readback"]["readback"]["selected_gain_restore_required"] = True
    elif change == "missing_readback":
        e._lifecycle.pop("readback")
    elif change == "camera_active":
        e._lifecycle["camera_release"]["state"] = "running"
    elif change == "camera_foreign":
        e._lifecycle["camera_release"]["session_id"] = str(uuid4())
    elif change == "camera_failed":
        e._lifecycle["camera_release"]["runtime_failed"] = True
    elif change in {"reader_alive", "backend_alive", "bad_returncode"}:
        key = {"reader_alive": "readers_joined", "backend_alive": "backend_exited",
               "bad_returncode": "backend_returncode"}[change]
        e._lifecycle["camera_release"]["release_evidence"][key] = 1 if change == "bad_returncode" else False
    elif change == "worker_alive":
        e.worker = SimpleNamespace(is_alive=lambda: True)
    elif change == "reconcile_alive":
        e._reconciliation_worker = SimpleNamespace(thread=SimpleNamespace(is_alive=lambda: True), state="pending", error=None)
    elif change == "malformed_cleanup":
        e._lifecycle["readback"]["readback"]["readbacks"][0] = None
    elif change == "invalid_source_identity":
        e.run_id = e._lifecycle["run_id"] = e.sample["ack"]["run_id"] = "not-a-native-run"
        e._lifecycle["readback"]["run_id"] = e._lifecycle["camera_release"]["session_id"] = e.run_id
    original = copy.deepcopy((e.sample, e._lifecycle, e._fault))
    evidence = e.evidence()
    if change in {None, "recent", "resident_fault"}:
        assert evidence["fault"] == e._fault
        assert all(evidence[k] is False for k in ("feedback", "required_observation", "native_ack",
                                                  "active_acknowledged", "hold_acknowledged", "pose_aligned"))
    else:
        assert evidence["fault"] == "native thermal original RAW feedback expired/invalid"
    assert (e.sample, e._lifecycle, e._fault) == original
    assert evidence["accepted_host_action"]["thermal_history"]["raw_sample_at_monotonic_s"] == 9.98


@pytest.mark.parametrize("boundary", ["evidence", "new_start"])
def test_real_core_terminal_thermal_ack_allows_only_new_startup_after_known_cleanup(tmp_path, boundary):
    import threading
    import time

    from lerobot.robots.alohamini import alohamini_host as host
    from tests.robots.test_am1_live_teleop_native import lane
    from tests.robots.test_am1_persistent_session import command, start
    from tests.robots.test_am1_physical_executor import terminal_cleanup_outcome, until
    from tools.am1_session_core import SessionAuthority

    class NativeIO:
        provenance = "protected-host"

        def __init__(self):
            self.started = 0
            self.entered, self.release = threading.Event(), threading.Event()

        def start(self, run_id, incarnation, recipe, cancel):
            self.started += 1
            self.run_id, self.incarnation = run_id, incarnation
            if self.started > 1:
                self.entered.set()
                while not self.release.wait(.005):
                    cancel()
            _, _, _, self.robot, _ = lane()
            self.robot._am1_live_before_write = lambda: None
            self.robot._am1_teleop_binding = {"run_id": run_id, "host_incarnation": incarnation, "recipe": recipe.name}
            self.control = host.AM1LocalControl(run_id=run_id, host_incarnation=incarnation,
                                               live=True, live_recipe=recipe.name)

        def poll(self, previous):
            self.control.check_live(self.robot)
            measured = self.robot.get_observation()
            at = time.monotonic()
            self.control.annotate(measured, acquired_at=at)
            marker = measured[host.AM1_LOCAL_FEEDBACK_KEY]
            marker["thermal_history"] = {"version": 1, "raw_sample_at_monotonic_s": at, "peaks_ready": True,
                "deficit_started_at_monotonic_s": None, "deficit_ceiling_monotonic_s": None,
                "hold_sample_at_monotonic_s": None, "zero_read_started_at_monotonic_s": None,
                "zero_read_completed_at_monotonic_s": None}  # Explicit synthetic thermal sensor proof.
            return {"positions": self.robot.get_observation(), "sequence": marker["observation_id"],
                    "at": at, "ack": marker, "observation": measured, "provenance": "protected-host/measured-feedback"}

        def send(self, action):
            self.control.apply(self.robot, action)

        def finish(self, status):
            result = terminal_cleanup_outcome(self.run_id, self.incarnation, status)
            result["first_cause"] = result["readback"]["first_cause"] = None
            return result

    io = NativeIO()
    e = executor(io)
    a = SessionAuthority(tmp_path, executor=e)
    try:
        first = start(a, "physical-live-teleop")
        assert first["accepted"] and first["status"] == "starting" and not first["effect_admitted"]
        def live():
            a.tick()
            return a.run.get("native_live_at") is not None
        until(live)
        stopped = a.handle(command("stop", run_id=a.run["run_id"]), "owner")
        assert stopped["accepted"]
        until(lambda: e.evidence()["lifecycle"]["phase"] == "terminal")
        e.worker.join(2)
        a.tick()
        assert a.run["status"] == "stopped" and a.run["cleanup"] == "physical_readback_verified"
        assert a.run["uncertain"] is False and e._fault is None
        old_run = a.run
        original, sample = copy.deepcopy(a.run), copy.deepcopy(e.sample)
        prior_run, prior_host = e.run_id, e.host_incarnation
        old_result = a.db.execute("SELECT result FROM operations WHERE id=?", (first["operation_id"],)).fetchone()
        time.sleep(.55)  # Genuine wall time: old thermal RAW expires while the closed host stays off.
        if boundary == "evidence":
            evidence = e.evidence()
            assert evidence["fault"] is None
            assert not evidence["feedback"] and not evidence["native_ack"] and not evidence["required_observation"]
        else:
            result = start(a, "physical-live-teleop")
            assert result["accepted"] and result["status"] == "starting" and result["effect_admitted"] is False
            assert io.entered.wait(1)
            assert e.run_id != prior_run and e.host_incarnation != prior_host
            assert e.sample is None and e._fault is None
            a.tick()
            assert a.run["native_live_at"] is None and a.run["deadline"] is None and a.run["progress_s"] == 0
            assert not e.evidence()["feedback"] and not e.evidence()["native_ack"]
            io.release.set()
            until(live)
            assert e.sample["at"] > sample["at"]
        assert old_run == original and original["run_id"] == prior_run
        assert a.db.execute("SELECT result FROM operations WHERE id=?", (first["operation_id"],)).fetchone() == old_result
        if boundary == "evidence":
            assert a.run == original and e.sample == sample
    finally:
        io.release.set()
        a.close()
        if e.worker:
            e.worker.join(2)


@pytest.mark.parametrize("case", ["fresh", "delayed", "stale", "nonadvancing"])
def test_selected_view_core_receipt_preserves_original_floor_and_reports_actual_policy_clock(tmp_path, case):
    a, e, _ = authority_lane(tmp_path)
    now = [10.]
    a.clock = lambda: now[0]
    command = dict(op="view_progress", run_id=e.run_id, role="chest", owner_generation="owner",
                   capture_generation="capture", encode_generation="encode", decoded_frames=3,
                   media_time=.3, source_sequence=11, source_fresh=True, received_at_monotonic_s=9.98)
    try:
        if case == "delayed":
            now[0] = 10.49
        elif case == "stale":
            command["source_fresh"] = False
        elif case == "nonadvancing":
            assert a._handle(command, "gateway-view")["required_view"]["qualified"]
            now[0] = 10.49
            command["received_at_monotonic_s"] = 10.2  # A new HTTP receipt cannot renew old decoded counters.
        result = a._handle(command, "gateway-view")
        assert result["accepted"] is (case != "delayed")
        if case != "delayed":
            assert result["required_view"]["qualified"] is (case == "fresh")
        timing = result["view_timing"]
        assert timing["core_started_at_monotonic_s"] == now[0]
        assert timing["core_completed_at_monotonic_s"] == now[0]
        assert timing["original_floor_at_monotonic_s"] == command["received_at_monotonic_s"]
        assert timing["core_accepted_at_monotonic_s"] == (None if case == "delayed" else now[0])
        assert timing["qualified_until_monotonic_s"] == (
            10.48 if case in {"fresh", "nonadvancing"} else 0. if case == "stale" else None)
        assert command["received_at_monotonic_s"] == (10.2 if case == "nonadvancing" else 9.98)
    finally:
        a.run = None
        a.close()


# Operation-first bench policy is opt-in; strict visual teleoperation is unchanged.
def _bench_intent(a):
    a.run["bench_continuity"] = True
    a.run["control_intent"] = {
        "eligible": True, "device_id": "pc",
        "controller_generation": a.controller["generation"],
        "service_incarnation": a.service_incarnation,
        "start_operation_id": a.run["operation_id"],
    }


def _reclaim(a, generation, **changes):
    proof = {'run_id': a.run["run_id"], 'service_incarnation': a.service_incarnation, 'start_operation_id': a.run["operation_id"], 'previous_generation': generation}
    proof.update(changes)
    return {'op': "claim", 'operation_id': str(uuid4()), 'reacquire': proof}


@pytest.mark.parametrize("policy", [None, False, True])
def test_bench_start_records_immutable_policy_and_extends_only_current_application_lease(tmp_path, monkeypatch, policy):
    from tools.am1_session_core import SessionAuthority

    e, _ = admitted_executor()
    a = SessionAuthority(tmp_path, clock=lambda: 10., executor=e)
    monkeypatch.setattr(e, "begin", lambda recipe: True)  # No device worker; real admission/binding still run.
    try:
        claim = a._handle({"op": "claim"}, "pc")
        assert a.controller["expires"] == 11.5  # Idle Claim retains its original short lease.
        request = {'op': "start", 'operation_id': str(uuid4()), 'recipe': "physical-live-teleop", 'controller_generation': claim["controller_generation"]}
        if policy is not None:
            request["bench_continuity"] = policy
        result = a._handle(request, "pc")
        assert result["accepted"], result
        assert a.run.get("bench_continuity", False) is (policy is True)
        assert a.controller["expires"] == (16. if policy is True else 13.)
        if policy is True:
            assert a.run["control_intent"]["eligible"]
            assert a.snapshot()["run"]["bench_continuity"] is True
            assert not a._handle(dict(request, bench_continuity=False), "pc")["accepted"]
        else:
            assert "control_intent" not in a.run
    finally:
        a.run = None
        a.close()


@pytest.mark.parametrize("phase", ["before_start", "during_evidence"])
@pytest.mark.parametrize("policy", [None, False, True])
def test_teleop_start_never_extends_expired_idle_authority(tmp_path, monkeypatch, phase, policy):
    from tools.am1_session_core import SessionAuthority

    e, _ = admitted_executor()
    now, begun = [10.], []
    a = SessionAuthority(tmp_path, clock=lambda: now[0], executor=e)
    monkeypatch.setattr(e, "begin", lambda recipe: begun.append(recipe) or True)
    try:
        claimed = a._handle({"op": "claim"}, "pc")
        generation, original_run_id = claimed["controller_generation"], e.run_id
        assert a.controller["expires"] == 11.5 and a.run is None
        original_evidence = a._evidence
        def late_evidence():
            evidence = original_evidence()
            now[0] = 11.5
            return evidence
        if phase == "during_evidence":
            monkeypatch.setattr(a, "_evidence", late_evidence)
        else:
            now[0] = 11.5
        request = {"op": "start", "operation_id": str(uuid4()), "recipe": "physical-live-teleop",
                   "controller_generation": generation}
        if policy is not None:
            request["bench_continuity"] = policy
        result = a._handle(request, "pc")
        assert result["accepted"] is False and result["reason"] == "current controller lease required"
        assert a.run is None and a.controller["generation"] == generation and a.controller["expires"] == 11.5
        assert begun == [] and e.run_id == original_run_id
    finally:
        a.run = None
        a.close()


@pytest.mark.parametrize("policy", [1, "true", None])
def test_bench_start_refuses_non_boolean_policy(tmp_path, monkeypatch, policy):
    from tools.am1_session_core import SessionAuthority

    e, _ = admitted_executor()
    a = SessionAuthority(tmp_path, clock=lambda: 10., executor=e)
    monkeypatch.setattr(e, "begin", lambda recipe: True)
    try:
        claim = a._handle({"op": "claim"}, "pc")
        result = a._handle({'op': "start", 'operation_id': str(uuid4()), 'recipe': "physical-live-teleop", 'controller_generation': claim["controller_generation"], 'bench_continuity': policy}, "pc")
        assert not result["accepted"] and a.run is None
    finally:
        a.run = None
        a.close()


def test_bench_advisory_view_preserves_actual_view_state_and_native_input_expiry(tmp_path):
    a, e, ids = authority_lane(tmp_path)
    _bench_intent(a)
    now = [10.]
    a.clock = e.clock = lambda: now[0]
    try:
        assert a._teleop_view_status() == {"required": False, "qualified": False, "role": None,
                                            "cause": "required_view_expired"}
        grant = a._handle(dict(op="grant", **ids), "pc")
        assert grant["accepted"], grant
        src = source(grant=grant["grant_id"])
        packet = dict(op="input", **ids, grant_id=grant["grant_id"], seq=1, source=src,
                      target=dict.fromkeys(KEYS, 5.),
                      body={"x.vel": 0., "y.vel": 0., "theta.vel": 0., "lift_axis.vel": 0.})
        assert a._handle(packet, "pc")["accepted"]
        assert a.applied_input["expires"] == 10.25
        now[0] = 10.251
        a._expire_controls(now[0])
        assert a.run["status"] == "paused" and e._live_target is None
        assert a.run["control_intent"]["eligible"]
        assert not a._handle(packet, "pc")["accepted"]
    finally:
        a.run = None
        a.close()


def test_bench_advisory_never_accepts_usb_source_or_bypasses_required_local_feedback(tmp_path):
    a, e, ids = authority_lane(tmp_path)
    _bench_intent(a)
    try:
        a._teleop_view = {"run_id": e.run_id, "qualified_until": 11.}
        grant = a._handle(dict(op="grant", **ids), "pc")
        frame = dict(op="input", **ids, grant_id=grant["grant_id"], seq=1,
                     source=dict(source(grant=grant["grant_id"]), kind="usb-leaders"),
                     target=dict.fromkeys(KEYS, 5.),
                     body={"x.vel": 0., "y.vel": 0., "theta.vel": 0., "lift_axis.vel": 0.})
        assert not a._handle(frame, "pc")["accepted"]
        assert e._live_target is None
        frame["source"]["kind"] = "virtual-leaders"
        e.sample["at"] = 8.  # Genuine stale native feedback; advisory video cannot admit it.
        assert not a._handle(frame, "pc")["accepted"]
        assert e._live_target is None
        e.sample["at"] = 10.
        e.observation.qualified = False  # Required local images remain distinct from browser presentation.
        assert e.evidence()["feedback"] is True and e.evidence()["required_observation"] is False
        assert not a._handle(frame, "pc")["accepted"]
        assert e._live_target is None
    finally:
        a.run = None
        a.close()


def test_bench_current_renewal_survives_three_seconds_but_never_revives_expired_generation(tmp_path):
    a, e, ids = authority_lane(tmp_path)
    _bench_intent(a)
    now = [10.]
    a.clock = e.clock = lambda: now[0]
    try:
        renewed = a._handle({'op': "claim", 'controller_generation': ids["controller_generation"]}, "pc")
        assert renewed["lease_seconds"] == 6. and renewed["presence_expires"] == 16.
        assert renewed["timing"] == {"service_received_at": 10., "service_completed_at": 10.}
        now[0] = 13.1
        a._expire_controls(now[0])
        assert a.controller["generation"] == ids["controller_generation"]
        now[0] = 16.
        a._expire_controls(now[0])
        assert a.controller is None and a.run["status"] == "paused"
        assert not a._handle({'op': "claim", 'controller_generation': ids["controller_generation"]}, "pc")["accepted"]
    finally:
        a.run = None
        a.close()


def test_bench_expired_authority_reacquires_new_generation_without_replaying_old_input(tmp_path):
    a, e, ids = authority_lane(tmp_path)
    _bench_intent(a)
    now = [10.]
    a.clock = e.clock = lambda: now[0]
    try:
        old_generation = ids["controller_generation"]
        old_deadline = a.run["deadline"]
        reference = copy.deepcopy(e.sample["ack"]["live_input"]["workspace"])
        a.run["input_source"] = source(seq=1)
        a.run["intent"] = dict.fromkeys(KEYS, 5.)
        a.grant = {"id": str(uuid4()), "expires": 10.2}
        a.controller["expires"] = 10.01
        now[0] = 10.02
        a._expire_controls(now[0])
        reclaimed = a._handle(_reclaim(a, old_generation), "pc")
        assert reclaimed["accepted"], reclaimed
        new_generation = reclaimed["controller_generation"]
        assert new_generation != old_generation
        assert a.controller["expires"] == 16.02
        assert a.run["status"] == "paused" and a.run["deadline"] == old_deadline
        assert a.connection is None and a.grant is None and a.applied_input is None
        assert a.run["intent"] is None and e._live_target is None
        assert a.run["input_source"]["sample_seq"] == 1  # Old source history stays a replay fence.
        assert e.sample["ack"]["live_input"]["workspace"] == reference
        assert not a._handle(dict(op="connect", **ids), "pc")["accepted"]
        newids = dict(ids, controller_generation=new_generation)
        connected = a._handle(dict(op="connect", **newids), "pc")
        assert connected["accepted"]
        assert not a._handle(dict(op="grant", **newids), "pc")["accepted"]
        assert a.events[-1]["kind"] == "controller_reacquired"
    finally:
        a.run = None
        a.close()


@pytest.mark.parametrize("intervening", ["pause", "stop", "release", "handoff", "takeover", "same_device_claim"])
def test_bench_automatic_intent_stays_invalid_after_newer_action_even_when_new_owner_expires(tmp_path, intervening):
    a, e, ids = authority_lane(tmp_path)
    _bench_intent(a)
    now = [10.]
    a.clock = e.clock = lambda: now[0]
    old_generation = ids["controller_generation"]
    try:
        if intervening in {"takeover", "same_device_claim"}:
            a.controller["expires"] = 10.01
            now[0] = 10.02
            a._expire_controls(now[0])
            actor = "other" if intervening == "takeover" else "pc"
            assert a._handle({"op": "claim"}, actor)["accepted"]
        else:
            request = {'op': intervening, 'run_id': a.run["run_id"], 'operation_id': str(uuid4())}
            if intervening == "handoff":
                request["target_device_id"] = "other"
            assert a._handle(request, "pc")["accepted"]
        if a.controller:
            now[0] = a.controller["expires"]
            a._expire_controls(now[0])
        assert not a.run["control_intent"]["eligible"]
        assert not a._handle(_reclaim(a, old_generation), "pc")["accepted"]
        assert a.controller is None
    finally:
        a.run = None
        a.close()


@pytest.mark.parametrize("field", ["run_id", "service_incarnation", "start_operation_id", "previous_generation"])
def test_bench_reacquire_requires_exact_current_operation_and_service(tmp_path, field):
    a, e, ids = authority_lane(tmp_path)
    _bench_intent(a)
    try:
        a.controller = None
        a.connection = None
        a.run["status"] = "paused"
        result = a._handle(_reclaim(a, ids["controller_generation"], **{field: str(uuid4())}), "pc")
        assert not result["accepted"] and a.controller is None
    finally:
        a.run = None
        a.close()


def test_bench_expired_release_revokes_durable_intent_without_disturbing_another_owner(tmp_path):
    a, e, ids = authority_lane(tmp_path)
    _bench_intent(a)
    try:
        a.controller = None
        assert a._handle({'op': "release", 'operation_id': str(uuid4())}, "pc")["accepted"]
        assert not a.run["control_intent"]["eligible"]
        assert not a._handle(_reclaim(a, ids["controller_generation"]), "pc")["accepted"]
    finally:
        a.run = None
        a.close()


@pytest.mark.parametrize("operation", ["connect", "release_input"])
def test_bench_accepted_pause_cancels_automatic_control_even_before_generation_expires(tmp_path, operation):
    a, e, ids = authority_lane(tmp_path)
    _bench_intent(a)
    try:
        assert a._handle({'op': "pause", 'run_id': a.run["run_id"], 'operation_id': str(uuid4())}, "pc")["accepted"]
        assert a.controller["generation"] == ids["controller_generation"]
        assert not a._handle(dict(op=operation, **ids), "pc")["accepted"]
    finally:
        a.run = None
        a.close()


def test_bench_reacquire_idempotent_reply_never_regrants_or_extends_new_authority(tmp_path):
    a, e, ids = authority_lane(tmp_path)
    _bench_intent(a)
    now = [10.]
    a.clock = e.clock = lambda: now[0]
    try:
        a.run.update(status="paused", recovery_episodes=0)
        e._execution = {"progress_s": 0., "complete": False, "fault": None}
        live = e.sample["ack"]["live_input"]
        live["reference"] = live["workspace"]["reference"]
        a.controller = a.connection = None
        request = _reclaim(a, ids["controller_generation"])
        first = a.handle(request, "pc")
        assert first["accepted"]
        expires = a.controller["expires"]
        generation = a.controller["generation"]
        now[0] = 10.1
        e.sample["at"] = now[0]
        assert a.handle(request, "pc") == first
        assert a.controller["expires"] == expires and a.controller["generation"] == generation
        a.controller = None
        assert a.handle(request, "pc") == first
        assert a.controller is None  # Retained accepted receipt is not renewed authority.
        assert not a.handle(dict(request, operation_id=str(uuid4())), "pc")["accepted"]
    finally:
        a.run = None
        a.close()


def test_bench_owner_restart_invalidates_intent_and_never_auto_reclaims(tmp_path):
    from tools.am1_session_core import SessionAuthority

    a, e, ids = authority_lane(tmp_path)
    _bench_intent(a)
    a.run.update(mode="interactive", started_wall=0.)
    request = _reclaim(a, ids["controller_generation"])
    a._save()
    # End only this non-actuating fixture authority, keeping its durable state.
    a.run = None
    a.close()
    restored = SessionAuthority(tmp_path, clock=lambda: 10., executor=e)
    try:
        assert restored.run["status"] == "interrupted"
        assert not restored.run["control_intent"]["eligible"]
        assert not restored.handle(request, "pc")["accepted"]
        assert restored.controller is None and e._live_target is None
    finally:
        restored.run = None
        restored.close()


@pytest.mark.parametrize("case", ["new_zero_fence", "old_role", "old_same_role_source", "late_false", "late_zero", "late_advancing", "new_advancing", "nonadvancing", "foreign_run"])
def test_selected_view_original_floor_cannot_rollback_current_run_evidence(tmp_path, case):
    a, e, _ = authority_lane(tmp_path)
    a.clock = lambda: 10.2
    current = dict(op="view_progress", run_id=e.run_id, role="chest", owner_generation="owner",
                   capture_generation="capture-new", encode_generation="encode-new", decoded_frames=10,
                   media_time=1., source_sequence=20, source_fresh=True, received_at_monotonic_s=10.1)
    if case == "new_zero_fence":
        current.update(decoded_frames=0, media_time=0., source_fresh=False)
    try:
        assert a._handle(current, "gateway-view")["accepted"]
        before = copy.deepcopy(a._teleop_view)
        late = dict(current, decoded_frames=11, media_time=1.1, source_sequence=21,
                    received_at_monotonic_s=10.05)
        if case in {"new_zero_fence", "old_same_role_source"}:
            late.update(capture_generation="capture-old", encode_generation="encode-old", source_fresh=True)
        if case == "old_role":
            late["role"] = "wrist_right"
        if case == "late_false":
            late["source_fresh"] = False
        if case == "late_zero":
            late.update(decoded_frames=0, media_time=0.)
        if case in {"new_advancing", "nonadvancing"}:
            late["received_at_monotonic_s"] = 10.15
        if case == "nonadvancing":
            late.update(decoded_frames=current["decoded_frames"], media_time=current["media_time"])
        if case == "foreign_run":
            late["run_id"] = str(uuid4())
        result = a._handle(late, "gateway-view")
        assert result["accepted"] is (case in {"new_advancing", "nonadvancing"})
        if case == "new_advancing":
            assert a._teleop_view["qualified_until"] == 10.65
        else:
            assert a._teleop_view == before
        if case not in {"new_advancing", "nonadvancing", "foreign_run"}:
            assert result["reason"] == "selected-view original receipt superseded"
            assert result["view_timing"]["core_accepted_at_monotonic_s"] is None
            assert result["view_timing"]["original_floor_at_monotonic_s"] == 10.05
    finally:
        a.run = None
        a.close()



def test_selected_view_equal_floor_retains_existing_admission_without_extending_age(tmp_path):
    a, e, _ = authority_lane(tmp_path)
    a.clock = lambda: 10.2
    command = dict(op="view_progress", run_id=e.run_id, role="chest", owner_generation="owner",
                   capture_generation="capture", encode_generation="encode", decoded_frames=1,
                   media_time=.1, source_sequence=11, source_fresh=True, received_at_monotonic_s=10.1)
    try:
        assert a._handle(command, "gateway-view")["accepted"]
        # Equal floors do not establish request order; preserve existing semantics,
        # including a current source replacement, but never manufacture younger proof.
        result = a._handle(dict(command, encode_generation="encode-new", decoded_frames=2, media_time=.2), "gateway-view")
        assert result["accepted"] and result["required_view"]["qualified"]
        assert a._teleop_view["qualified_until"] == 10.6
        assert a._handle(dict(command, encode_generation="encode-new", decoded_frames=0), "gateway-view")["accepted"]
        assert not a._teleop_view_qualified()
    finally:
        a.run = None
        a.close()
