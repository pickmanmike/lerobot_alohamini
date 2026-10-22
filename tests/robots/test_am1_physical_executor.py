"""Real adapter boundaries; injected hardware never opens a serial device."""

import copy
import importlib
import threading
import time
from types import SimpleNamespace

import pytest

from examples.alohamini.am1_finite_task import JOINT_KEYS
from tests.robots import test_alohamini_lift_operational as operational_tests

# Reuse the actual protected operation and grouped fake-bus fixture unchanged.
operating_robot = operational_tests.operating_robot
qualified_idle = operational_tests.qualified_idle
poll_idle_feedback = operational_tests.poll_idle_feedback


def module():
    assert importlib.util.find_spec("tools.am1_physical_executor"), "protected physical adapter missing"
    return importlib.import_module("tools.am1_physical_executor")


def client_sample(now=10.0, **marker_overrides):
    marker = {
        "version": 1,
        "state": "paused",
        "epoch": 1,
        "observation_id": 5,
        "run_id": "run",
        "host_incarnation": "host",
        "intent_revision": 0,
        "acquired_at_monotonic_s": now - 0.02,
        "read_started_at_monotonic_s": now - 0.04,
        "accepted_action_sequence": 4,
    }
    marker.update(marker_overrides)
    positions = dict.fromkeys(JOINT_KEYS, 7.0)
    positions["arm_left_shoulder_lift.pos"] = 110.0
    c = SimpleNamespace(
        observation_sequence=5,
        latest_raw_observation_keys=frozenset(JOINT_KEYS),
        latest_observation_received_at=now - 0.01,
        latest_observation_roundtrip_age_s=0.03,
        latest_observation_error=None,
        latest_am1_local_feedback=marker,
    )
    c.get_observation = lambda: positions
    return c


def test_feedback_uses_actual_affine_measurement_and_original_acquisition():
    m = module()
    sample = m.read_physical_sample(client_sample(), "run", "host", 4, clock=lambda: 10.0)
    assert sample["positions"]["arm_left_shoulder_lift.pos"] == 110.0
    assert sample["at"] == 9.96
    assert sample["sequence"] == 5
    assert sample["ack"]["accepted_action_sequence"] == 4
    assert sample["provenance"] == "protected-host/measured-feedback"


@pytest.mark.parametrize(
    "override",
    [
        {"run_id": "foreign"},
        {"host_incarnation": "previous"},
        {"observation_id": 4},
        {"read_started_at_monotonic_s": 8.0},
        {"acquired_at_monotonic_s": 11.0},
    ],
)
def test_stale_or_foreign_native_feedback_cannot_be_requalified(override):
    with pytest.raises((ValueError, RuntimeError)):
        module().read_physical_sample(client_sample(**override), "run", "host", 4, clock=lambda: 10.0)


def test_cached_reception_cannot_renew_real_feedback():
    c = client_sample()
    c.latest_observation_received_at = 10.0
    c.latest_am1_local_feedback["read_started_at_monotonic_s"] = 8.0
    with pytest.raises((ValueError, RuntimeError)):
        module().read_physical_sample(c, "run", "host", 4, clock=lambda: 10.0)


def retained_native_sample99_client():
    """Original combined06 observation99 values/times, with identity anonymized."""
    raw_and_normalized = {
        "arm_left_shoulder_pan.pos": (2238, 11.340967861100864),
        "arm_left_shoulder_lift.pos": (3225, 100.1684210526316),
        "arm_left_elbow_flex.pos": (2101, 11.996378451788132),
        "arm_left_wrist_flex.pos": (1547, -75.3959873284055),
        "arm_left_wrist_roll.pos": (2200, 7.44810744810745),
        "arm_left_gripper.pos": (3166, 84.48844884488449),
        "arm_right_shoulder_pan.pos": (2143, 2.8846153846153726),
        "arm_right_shoulder_lift.pos": (3252, 98.75840662183134),
        "arm_right_elbow_flex.pos": (1854, -7.142857142857139),
        "arm_right_wrist_flex.pos": (2772, 53.692848769050414),
        "arm_right_wrist_roll.pos": (2444, 19.365079365079367),
        "arm_right_gripper.pos": (1981, 21.121251629726206),
    }
    positions = {key: normalized for key, (_raw, normalized) in raw_and_normalized.items()}
    observation = dict(
        positions,
        **{
            "x.vel": 0.0,
            "y.vel": 0.0,
            "theta.vel": 0.0,
            "lift_axis.vel": 50,
            "lift_axis.height_mm": 10.458984375,
        },
    )
    marker = {
        "version": 1,
        "run_id": "run",
        "host_incarnation": "host",
        "observation_id": 99,
        "accepted_action_sequence": 96,
        "state": "active",
        "epoch": 6,
        "intent_revision": 2,
        "write_acknowledged": False,
        "read_started_at_monotonic_s": 93298.362833242,
        "acquired_at_monotonic_s": 93298.385650822,
        "accepted_action": dict(
            positions,
            **{
                "arm_left_shoulder_pan.pos": 12.818618396749159,
                "x.vel": 0.0,
                "y.vel": 0.0,
                "theta.vel": 0.0,
                "lift_axis.vel": 0,
            },
        ),
        "arm_feedback": {
            "version": 1,
            "positions": {
                key: {
                    "raw": raw,
                    "normalized": normalized,
                    "read_started_at_monotonic_s": 93298.362883427 if "_left_" in key else 93298.366766693,
                    "read_completed_at_monotonic_s": 93298.364696236 if "_left_" in key else 93298.368585854,
                }
                for key, (raw, normalized) in raw_and_normalized.items()
            },
        },
        "lift_feedback": {
            "version": 1,
            "height_mm": 10.458984375,
            "present_velocity_raw": 50,
            "sample_monotonic_s": 93298.343602744,
            "request_duration_s": 0.0017404389946023002,
        },
    }
    client = SimpleNamespace(
        latest_am1_local_feedback=marker,
        latest_raw_observation_keys=frozenset(observation),
        latest_observation_received_at=93298.387097244,
        latest_observation_roundtrip_age_s=0.13533435499994084,
        latest_observation_error=None,
    )
    client.get_observation = lambda: observation
    return client


def pending_sample_executor(sample, clock):
    from examples.alohamini.am1_live_teleop import TELEOP_SPEC

    e = executor(ControlledHost())
    e.live_spec = TELEOP_SPEC
    e.sample = copy.deepcopy(sample)
    e.clock = clock
    return e


def test_real_cached_client_sample99_pending40ms_then_advancing100(monkeypatch, tmp_path):
    import json

    from lerobot.robots.alohamini import alohamini_client as client_module
    from lerobot.robots.alohamini.config_alohamini import AlohaMiniClientConfig

    m = module()
    original = retained_native_sample99_client()
    clock = [original.latest_observation_received_at]
    monkeypatch.setattr(
        client_module, "time", SimpleNamespace(monotonic=lambda: clock[0], perf_counter=lambda: clock[0])
    )
    client = client_module.AlohaMiniClient(
        AlohaMiniClientConfig(
            remote_ip="127.0.0.1",
            robot_model="alohamini1",
            cameras={},
            calibration_dir=tmp_path,
        )
    )
    client._is_connected = True
    payload = dict(
        original.get_observation(), _am1_local_feedback=copy.deepcopy(original.latest_am1_local_feedback)
    )
    replies = [payload, None, None]

    def poll():
        value = replies.pop(0)
        if value is None:
            clock[0] += 0.040
            client._last_response_request_sent_at = None
            return None
        client._last_response_request_sent_at = (
            93298.251762889 if value["_am1_local_feedback"]["observation_id"] == 99 else 93298.387097244
        )
        return [json.dumps(value).encode()]

    monkeypatch.setattr(client, "_poll_and_get_latest_message", poll)
    first = m.read_physical_sample(client, "run", "host", 98, clock=lambda: clock[0])
    e = pending_sample_executor(first, lambda: clock[0])
    retained = e.sample
    pending = m.read_physical_sample(
        client, "run", "host", 99, clock=lambda: clock[0], allow_pending_sample=True
    )
    assert isinstance(pending, m.PendingSample)
    assert e._update_polled_sample(pending, None) == (False, True)
    assert e.sample is retained
    assert client.latest_observation_received_at == 93298.387097244
    assert client.latest_observation_roundtrip_age_s == 0.13533435499994084
    assert e.sample["at"] == 93298.251762889 and e.sample["sequence"] == 99
    with pytest.raises(m.FeedbackUnavailable, match="did not advance"):
        m.read_physical_sample(client, "run", "host", 99, clock=lambda: clock[0])

    # A synthetic advancing reply follows the exact retained99 cache miss.
    next_payload = copy.deepcopy(payload)
    marker = next_payload["_am1_local_feedback"]
    marker["observation_id"], marker["accepted_action_sequence"] = 100, 97
    marker["read_started_at_monotonic_s"] += 0.080
    marker["acquired_at_monotonic_s"] += 0.080
    marker["lift_feedback"]["sample_monotonic_s"] += 0.080
    for reading in marker["arm_feedback"]["positions"].values():
        reading["read_started_at_monotonic_s"] += 0.080
        reading["read_completed_at_monotonic_s"] += 0.080
    replies.append(next_payload)
    candidate = m.read_physical_sample(
        client, "run", "host", 99, clock=lambda: clock[0], allow_pending_sample=True
    )
    assert e._update_polled_sample(candidate, None) == (True, True)
    assert e.sample["sequence"] == 100 and e.sample["at"] > retained["at"]


@pytest.mark.parametrize(
    "changed", ["positions", "raw", "body", "ack", "request", "reply", "acquired", "identity", "type"]
)
def test_pending_same_id_changed_native_payload_is_refused(changed):
    m = module()
    client = retained_native_sample99_client()
    first = m.read_physical_sample(client, "run", "host", 98, clock=lambda: 93298.4)
    e = pending_sample_executor(first, lambda: 93298.45)
    candidate = copy.deepcopy(first)
    if changed == "positions":
        candidate["positions"]["arm_left_shoulder_pan.pos"] += 1
    elif changed == "raw":
        candidate["arm_feedback"]["positions"]["arm_left_shoulder_pan.pos"]["raw"] += 1
    elif changed == "body":
        candidate["body_feedback"]["lift_axis.vel"] = 0
    elif changed == "ack":
        candidate["ack"]["accepted_action_sequence"] += 1
    elif changed == "request":
        candidate["timing"]["original_request_sent_at_monotonic_s"] += 0.001
    elif changed == "reply":
        candidate["timing"]["reply_received_at_monotonic_s"] += 0.001
    elif changed == "acquired":
        candidate["ack"]["acquired_at_monotonic_s"] += 0.001
    elif changed == "identity":
        candidate["ack"]["host_incarnation"] = "foreign"
    elif changed == "type":
        candidate["ack"]["epoch"] = 6.0
    with pytest.raises(ValueError, match="pending.*payload"):
        e._update_polled_sample(m.PendingSample(candidate), None)
    assert e.sample == first


@pytest.mark.parametrize("changed", ["rewind", "identity", "malformed", "raw_mismatch", "stale"])
def test_pending_read_validates_original_feedback_before_allowing_cache(changed):
    m = module()
    client = retained_native_sample99_client()
    now = 93298.45
    if changed == "rewind":
        client.latest_am1_local_feedback["observation_id"] = 98
    elif changed == "identity":
        client.latest_am1_local_feedback["host_incarnation"] = "foreign"
    elif changed == "malformed":
        client.latest_observation_error = "invalid original payload"
    elif changed == "raw_mismatch":
        client.latest_am1_local_feedback["arm_feedback"]["positions"]["arm_left_shoulder_pan.pos"][
            "normalized"
        ] += 1
    elif changed == "stale":
        now = 93299.251762889
    with pytest.raises((m.FeedbackUnavailable, ValueError)):
        m.read_physical_sample(client, "run", "host", 99, clock=lambda: now, allow_pending_sample=True)


def test_pending_never_clears_real_transient_or_extends_original_feedback_age():
    m = module()
    client = retained_native_sample99_client()
    first = m.read_physical_sample(client, "run", "host", 98, clock=lambda: 93298.4)
    clock = [93298.45]
    e = pending_sample_executor(first, lambda: clock[0])
    e._transient = "genuine transport timeout"
    assert e._update_polled_sample(m.PendingSample(copy.deepcopy(first)), None) == (False, False)
    assert e._transient == "genuine transport timeout"
    e._transient = None
    clock[0] = first["at"] + 1.0
    assert e._update_polled_sample(m.PendingSample(copy.deepcopy(first)), None) == (False, False)
    assert e.sample["at"] == first["at"]
    assert e._update_polled_sample(None, "genuine socket failure") == (False, False)
    assert e._transient == "genuine socket failure"


def test_only_teleop_protected_host_opts_into_pending_sample():
    from examples.alohamini.am1_session_contract import RECIPES

    m = module()
    provider = object.__new__(m.ProtectedHostRun)
    provider.host = provider.camera = SimpleNamespace(poll=lambda: None)
    provider.run_id, provider.incarnation = "run", "host"
    provider.client = retained_native_sample99_client()
    provider.recipe = RECIPES["physical-live-teleop"]
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(m, "read_physical_sample", lambda *a, **kw: kw)
        assert provider.poll(99)["allow_pending_sample"] is True
        provider.recipe = RECIPES["physical-live-left-elbow"]
        assert provider.poll(99)["allow_pending_sample"] is False


def test_body_recipe_uses_original_200ms_press_release_and_zero_between():
    m = module()
    seen = []
    client = SimpleNamespace(
        _from_keyboard_to_base_action=lambda keys: {"keys": list(keys)},
        _from_keyboard_to_lift_action=lambda keys: {"lift_keys": list(keys)},
    )
    for elapsed in (1, 2.0, 2.1, 2.2, 2.7, 2.9, 3.4, 3.6, 4.1, 4.3, 11.9):
        seen.append(m.body_recipe_action(client, elapsed)["keys"])
    assert seen == [[], ["w"], ["w"], [], ["a"], [], ["u"], [], ["j"], [], []]


def test_live_recipes_have_no_synthetic_seed_or_simulated_provider_label():
    m = module()
    from examples.alohamini.am1_finite_task import FiniteTask
    from examples.alohamini.am1_session_contract import RECIPES

    recipe = RECIPES["physical-arm-smoke-repeat"]
    assert recipe.seed is None
    task = FiniteTask(
        recipe,
        dict.fromkeys(JOINT_KEYS, 7.0),
        time.monotonic(),
        1.5,
        start_held=True,
        feedback_provenance="protected-host/measured-feedback",
    )
    assert task.snapshot()["provenance"] == "existing-provider/protected-host/measured-feedback"
    assert task.snapshot()["initial_measured_reference"] == dict.fromkeys(JOINT_KEYS, 7.0)
    assert m.PhysicalExecutor.source == "protected-physical-provider"


def test_driver_factory_cannot_use_simulated_io(tmp_path):
    m = module()
    from tools.am1_pi_executor import SimulatedIO

    with pytest.raises((TypeError, ValueError)):
        m.PhysicalExecutor({}, None, io_factory=lambda *_: SimulatedIO())


class ControlledHost:
    provenance = "protected-host"

    def __init__(self):
        self.started = 0
        self.finished = 0
        self.actions = []
        self.sequence = 0
        self.acknowledge = True
        self.healthy = True
        self.clean = True
        self.run_id = self.incarnation = None
        self.marker = {"state": "ready", "epoch": -1, "intent_revision": 0, "accepted_action_sequence": -1}

    def start(self, run_id, incarnation, recipe, cancel):
        self.started += 1
        self.run_id = run_id
        self.incarnation = incarnation

    def poll(self, previous):
        m = module()
        if not self.healthy:
            raise m.FeedbackUnavailable("bounded test transport timeout")
        self.sequence += 1
        return {
            "positions": dict.fromkeys(JOINT_KEYS, 7.0),
            "sequence": self.sequence,
            "at": time.monotonic(),
            "ack": dict(self.marker, run_id=self.run_id, host_incarnation=self.incarnation),
            "observation": {},
            "provenance": "protected-host/measured-feedback",
        }

    def send(self, action):
        self.actions.append(action)
        if self.acknowledge:
            marker = action["_am1_local_control"]
            self.marker = {
                "state": "paused" if marker["mode"] == "pause" else "active",
                "epoch": marker["epoch"],
                "intent_revision": marker["intent_revision"],
                "accepted_action_sequence": marker["action_sequence"],
            }

    def finish(self, status):
        self.finished += 1
        return {
            "cleanup": "physical_readback_verified" if self.clean else "physical_cleanup_unknown",
            "uncertain": not self.clean,
            "terminal_status": status,
            "readback": {"fixture": True},
        }

    def body_action(self, elapsed):
        return dict.fromkeys(["x.vel", "y.vel", "theta.vel", "lift_axis.vel"], 0)


def observation():
    from tools.am1_local_observation import LocalObservationMailbox

    source = {
        "machine": "AM1",
        "source": "existing-camera",
        "device": "/dev/am_camera_chest",
        "framing_revision": "1",
        "role": "chest",
    }
    o = LocalObservationMailbox(source)
    o.qualified = True
    o.begin = lambda *_: None
    o.stop = lambda: None
    o.evidence = lambda: {"qualified": o.qualified, "age_s": 0 if o.qualified else None}
    return o


def executor(host):
    return module().PhysicalExecutor(
        {"shoulder_amplitude": 1.5, "source_pins": {}}, observation(), io_factory=lambda *_: host
    )


def pending_native_worker():
    """Actual worker and native control, with queued feedback and inert buses."""
    import queue

    from examples.alohamini.am1_session_contract import RECIPES
    from tests.robots.test_am1_live_teleop_native import frame, lane, sample

    now, identity, binding, robot, control = lane()
    control.apply(robot, frame(identity, binding, 1))
    original = sample(control)
    now[0] = 10.1
    control.apply(robot, frame(identity, binding, 2, mode="active", epoch=2, sample=original))
    measured = robot.get_observation()
    control.annotate(measured, acquired_at=now[0])
    requests, actions = queue.Queue(), queue.Queue()

    class NativeIO:
        provenance = "protected-host"

        def start(self, *args):
            pass

        def poll(self, previous):
            return requests.get(timeout=2)

        def send(self, action):
            control.apply(robot, action)
            actions.put(copy.deepcopy(action))

        def finish(self, status):
            return {"cleanup": "physical_readback_verified", "uncertain": False, "terminal_status": status}

    e = executor(NativeIO())
    e.clock = lambda: now[0]
    e.bind_run(identity["run_id"], RECIPES["physical-live-teleop"])
    e.host_incarnation = identity["host_incarnation"]
    e._live_binding, e._live_target = binding, original
    e._lifecycle.update(phase="live", native_live_at=10.0)
    e._epoch, e._epoch_mode, e._epoch_revision = 2, "active", 0
    e._action_sequence, e.desired, e._dispatch_ack = 2, "active", True
    e._live_admission_requested = True
    e.sample = {
        "sequence": control.observation_id,
        "at": now[0],
        "positions": robot.get_observation(),
        "ack": copy.deepcopy(measured["_am1_local_feedback"]),
        "observation": measured,
        "body_feedback": dict(module().ZERO_BODY),
        "provenance": "protected-host/measured-feedback",
    }
    e.worker = threading.Thread(target=e._run, daemon=True)
    e.worker.start()

    def step(polled=None):
        requests.put(module().PendingSample(copy.deepcopy(e.sample)) if polled is None else polled)
        e._wake.set()
        return actions.get(timeout=2)

    def stop():
        e.finish("stopped")
        requests.put(None)
        e._wake.set()
        e.worker.join(2)
        assert not e.worker.is_alive()

    return now, robot, control, e, step, stop


def test_pending_worker_keeps_active_without_new_alignment_ack_or_revision_credit():
    assert hasattr(module(), "PendingSample"), "explicit cached-feedback result missing"
    now, _robot, _control, e, step, stop = pending_native_worker()
    try:
        original = e.sample
        assert step()["_am1_local_control"]["mode"] == "active"
        assert e.sample is original and e._aligned == [] and e._dispatch_ack
        now[0] += 0.04
        e._dispatch_ack = False
        assert step()["_am1_local_control"]["mode"] == "active"
        assert not e._dispatch_ack and not e._aligned and e._finite_completion is None
        assert e.sample is original
        e.set_intent_revision(1)
        e.desired = "active"  # A cached old active ACK cannot admit this new revision.
        assert step()["_am1_local_control"]["mode"] == "pause"
        assert not e.evidence()["native_ack"] and not e._dispatch_ack
        assert e.sample["ack"]["intent_revision"] == 0
    finally:
        stop()


@pytest.mark.parametrize("loss", ["observation", "fault", "original_age", "startup"])
def test_pending_worker_still_holds_and_cannot_supply_startup_credit(loss):
    assert hasattr(module(), "PendingSample"), "explicit cached-feedback result missing"
    now, _robot, _control, e, step, stop = pending_native_worker()
    try:
        if loss == "observation":
            e.observation.qualified = False
        elif loss == "fault":
            e._fault = "retained hard native fault"
        elif loss == "original_age":
            now[0] = e.sample["at"] + 1.0
        else:
            e._lifecycle.update(phase="startup", native_live_at=None)
            e.desired, e._epoch_mode, e._epoch = "hold", "pause", 1
            e._aligned = [9.8, 9.9, 10.0]
            e.sample["ack"].update(state="paused", epoch=1)
            e._live_admission_requested = False
        assert step()["_am1_local_control"]["mode"] == "pause"
        if loss == "startup":
            assert e._lifecycle["native_live_at"] is None
            assert not e._live_admission_requested
            assert e._aligned == [9.8, 9.9, 10.0]
        else:
            assert not e._dispatch_ack
        assert e._finite_completion is None
        if loss == "fault":
            assert not e.evidence()["native_ack"]
    finally:
        stop()


def test_pending_worker_keeps_original_input_expiry_at_actual_native_control():
    assert hasattr(module(), "PendingSample"), "explicit cached-feedback result missing"
    now, robot, control, e, step, stop = pending_native_worker()
    try:
        retained = e.sample
        assert step()["_am1_local_control"]["live_input"]["valid_until_monotonic_s"] == 10.25
        now[0] = 10.26  # Feedback remains inside its original 1s; input does not.
        before = len(robot.events)
        action = step()
        assert action["_am1_local_control"]["live_input"]["valid_until_monotonic_s"] == 10.25
        assert control.state == "paused" and control.live_snapshot()["state"] == "expired"
        assert not any(event[0] == "action" for event in robot.events[before:])
        assert ("zero",) in robot.events[before:] and ("hold",) in robot.events[before:]
        assert e.sample is retained and e.sample["at"] == 10.1
    finally:
        stop()


def test_live_executor_allowlist_freezes_spec_from_bound_recipe_only():
    from examples.alohamini.am1_session_contract import LIVE_ELBOW_SPECS, RECIPES, Recipe

    e = executor(ControlledHost())
    for name in ("physical-live-left-elbow", "physical-live-right-elbow"):
        assert e.supports(RECIPES[name])
    assert not e.supports(Recipe("physical-live-any-joint", "interactive", None, 90, seed=None))
    assert not e.supports(Recipe("physical-live-right-elbow", "finite", None, 90, seed=None))
    assert not e.supports(Recipe("physical-live-right-elbow", "interactive", None, 180, seed=None))
    e.bind_run("selected-right", RECIPES["physical-live-right-elbow"])
    assert e.live_spec is LIVE_ELBOW_SPECS["physical-live-right-elbow"]
    assert e.live_spec.joint == "arm_right_elbow_flex.pos"


def test_practical_executor_reuses_live_stream_without_a_preloaded_trajectory():
    from examples.alohamini.am1_session_contract import LIVE_ELBOW_SPECS, RECIPES, Recipe

    e = executor(ControlledHost())
    name = "physical-live-right-elbow-practical"
    assert e.supports(RECIPES[name])
    assert not e.supports(Recipe(name, "finite", None, 180, seed=None))
    assert not e.supports(Recipe(name, "interactive", 15, 180, seed=None))
    assert not e.supports(Recipe(name, "interactive", None, 301, seed=None))
    e.bind_run("practical-right", RECIPES[name])
    assert e.live_spec is LIVE_ELBOW_SPECS[name]
    assert e.task is None and e._live_target is None
    assert e._live_ceiling() == 180


def until(predicate, timeout=2):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate(), "worker condition did not complete inside bounded test budget"


def begin(e):
    from examples.alohamini.am1_session_contract import RECIPES

    r = RECIPES["physical-arm-smoke"]
    e.bind_run("run", r)
    assert e.begin(r)
    until(lambda: e.evidence()["lifecycle"]["phase"] == "live")
    return r


def test_idle_does_not_launch_and_native_dispatch_needs_actual_host_ack():
    host = ControlledHost()
    e = executor(host)
    try:
        e.hold()
        assert host.started == 0
        r = begin(e)
        assert e.evidence()["hold_acknowledged"]
        assert e.evidence()["pose_aligned"]
        host.acknowledge = False
        assert not e.dispatch(r)
        until(lambda: any(a["_am1_local_control"]["mode"] == "active" for a in host.actions))
        assert not e.dispatch(r)
        assert e.advance(time.monotonic(), 0.1)["progress_s"] == 0
        host.acknowledge = True
        until(lambda: e.dispatch(r))
        until(lambda: e.advance(time.monotonic(), 0.1)["preparation_s"] > 0)
        # Requested target never becomes fake measured plant state.
        assert set(e.evidence()["measured_positions"].values()) == {7.0}
    finally:
        e.close()
        e.worker.join(2)


def test_required_input_loss_holds_independently_of_web_and_fences_revision():
    host = ControlledHost()
    e = executor(host)
    try:
        r = begin(e)
        until(lambda: e.dispatch(r))
        e.observation.qualified = False
        until(lambda: e.evidence()["hold_acknowledged"])
        progress = e.advance(time.monotonic(), 0.1)["progress_s"]
        time.sleep(0.15)
        assert e.advance(time.monotonic(), 0.1)["progress_s"] == progress
        e.set_intent_revision(1)
        e.hold()
        e.observation.qualified = True
        until(lambda: e.evidence()["pose_aligned"])
        host.acknowledge = False
        assert not e.dispatch(r)
        assert not e.dispatch(r)
        assert e.evidence()["accepted_host_action"]["state"] != "active"
        e.set_intent_revision(2)
        e.hold()  # late active ack may not override user Pause.
        host.acknowledge = True
        until(lambda: e.evidence()["hold_acknowledged"])
        assert e.evidence()["acknowledged_intent_revision"] == 2
    finally:
        e.close()
        e.worker.join(2)


def test_actual_finite_return_receipt_survives_worker_hold_until_authority_consumes(tmp_path):
    from examples.alohamini.am1_session_contract import RECIPES
    from tests.robots.test_am1_persistent_session import Clock, start
    from tools.am1_session_core import SessionAuthority

    clock = Clock()

    class SteppedHost(ControlledHost):
        def poll(self, previous):
            clock.advance(0.1)
            sample = super().poll(previous)
            sample["at"] = clock()
            return sample

    host = SteppedHost()
    e = module().PhysicalExecutor(
        {"shoulder_amplitude": 1.5, "source_pins": {}},
        observation(),
        clock=clock,
        io_factory=lambda *_: host,
    )
    authority = SessionAuthority(tmp_path, clock=clock, executor=e)
    try:
        start(authority, "physical-arm-hold-body")

        def admitted():
            authority.tick()
            return authority.run["status"] == "running"

        until(admitted)
        deadline = authority.run["deadline"]
        # Execute the unchanged 12-second provider and real three-sample return gate.
        # Only the injected host clock is accelerated; elapsed trajectory is not completion.
        until(lambda: e.advance(clock(), 0)["progress_s"] == 12, timeout=7)
        assert not e.advance(clock(), 0)["complete"]
        assert e.evidence().get("finite_completion") is None
        until(lambda: e.advance(clock(), 0)["complete"])
        until(lambda: e.evidence()["hold_acknowledged"])
        evidence = e.evidence()
        assert not evidence["active_acknowledged"]
        receipt = evidence.get("finite_completion")
        assert receipt is not None
        assert receipt["execution"]["returns_qualified"] == 1
        assert receipt["execution"]["admissions"] == 1
        assert receipt["feedback_sequence"] == receipt["execution"]["feedback_sequence"]
        assert receipt["feedback_sequence"] < evidence["feedback_sequence"]
        assert receipt["accepted_host_action"]["state"] == "active"
        assert receipt["accepted_host_action"]["epoch"] < evidence["accepted_host_action"]["epoch"]
        assert receipt["run_id"] == authority.run["run_id"]
        assert receipt["host_incarnation"] == authority.run["backend_incarnation"]
        assert receipt["measured_positions"] == dict.fromkeys(JOINT_KEYS, 7.0)
        receipt["measured_positions"][JOINT_KEYS[0]] = 99
        assert e.evidence()["finite_completion"]["measured_positions"][JOINT_KEYS[0]] == 7
        authority.tick()
        assert authority.run["status"] == "finishing"
        assert authority.run["requested_terminal"] == "completed"
        assert authority.run["recovery_episodes"] == 0
        assert authority.run["first_cause"] is None
        assert authority.run["deadline"] == deadline
        until(lambda: e.evidence()["lifecycle"]["phase"] == "terminal")
        authority.tick()
        assert authority.run["status"] == "completed"
        assert authority.run["cleanup"] == "physical_readback_verified"
        # A revision change fences even a retained genuine completion receipt.
        e.set_intent_revision(1)
        assert e.evidence()["finite_completion"] is None
        assert authority.run["completion_receipt"]["intent_revision"] == 0
        assert authority.run["completion_receipt"]["execution"]["returns_qualified"] == 1
        assert e.supports(RECIPES["physical-arm-hold-body"])
    finally:
        authority.close()
        e.worker.join(2)


@pytest.mark.parametrize("pause_boundary", ["before_final_return", "after_final_return"])
def test_actual_pause_resume_requires_fresh_acknowledged_return(tmp_path, pause_boundary):
    from tests.robots.test_am1_persistent_session import Clock, command, start
    from tools.am1_session_core import SessionAuthority

    clock = Clock()

    class SteppedHost(ControlledHost):
        allow_active_ack = True

        def poll(self, previous):
            clock.advance(0.1)
            sample = super().poll(previous)
            sample["at"] = clock()
            return sample

        def send(self, action):
            if action["_am1_local_control"]["mode"] == "active" and not self.allow_active_ack:
                self.actions.append(action)
            else:
                super().send(action)

    host = SteppedHost()
    e = module().PhysicalExecutor(
        {"shoulder_amplitude": 1.5, "source_pins": {}},
        observation(),
        clock=clock,
        io_factory=lambda *_: host,
    )
    authority = SessionAuthority(tmp_path, clock=clock, executor=e)
    try:
        run = start(authority, "physical-arm-hold-body")

        def admitted():
            authority.tick()
            return authority.run["status"] == "running"

        until(admitted)
        deadline = authority.run["deadline"]
        if pause_boundary == "after_final_return":
            until(lambda: e.evidence().get("finite_completion") is not None, timeout=7)
        else:
            until(lambda: e.advance(clock(), 0)["progress_s"] >= 11.8, timeout=7)
        seed = dict(e.task.original_seed)
        assert e.task.complete == (pause_boundary == "after_final_return")
        assert authority.run["status"] == "running"
        paused = authority.handle(command("pause", run_id=run["run_id"]), "viewer")
        assert paused["accepted"] and authority.run["status"] == "paused"
        assert e.evidence()["finite_completion"] is None
        until(lambda: e.evidence()["pose_aligned"])
        claim = authority.handle(command("claim"), "owner")
        connection = authority.handle(command("connect", run_id=run["run_id"]), "owner")
        controls = {
            "run_id": run["run_id"],
            "controller_generation": claim["controller_generation"],
            "connection_generation": connection["connection_generation"],
            "service_incarnation": authority.service_incarnation,
        }
        assert authority.handle(command("release_input", **controls), "owner")["accepted"]
        host.allow_active_ack = False
        resumed = authority.handle(
            command("resume", expected_intent_revision=authority.run["intent_revision"], **controls), "owner"
        )
        assert resumed["accepted"] and authority.run["status"] == "resuming"
        revision = authority.run["intent_revision"]

        def active_requested():
            authority.tick()
            return e._epoch_mode == "active"

        until(active_requested)
        before = e.evidence()["feedback_sequence"]
        until(lambda: e.evidence()["feedback_sequence"] >= before + 3)
        assert e.evidence()["finite_completion"] is None
        host.allow_active_ack = True
        until(lambda: e.evidence().get("finite_completion") is not None)
        until(lambda: e.evidence()["hold_acknowledged"])
        receipt = e.evidence()["finite_completion"]
        assert receipt["intent_revision"] == revision
        if pause_boundary == "after_final_return":
            assert receipt["return_reacknowledgment"]["sample_count"] == 3
            assert receipt["return_reacknowledgment"]["span_s"] >= 0.2 - 1e-9
        else:
            assert "return_reacknowledgment" not in receipt
        assert receipt["execution"]["original_seed"] == seed
        assert receipt["execution"]["admissions"] == 1
        assert receipt["execution"]["returns_qualified"] == 1
        assert receipt["execution"]["progress_s"] == 12
        # The native hold can also precede the authority consuming Resume's ACK.
        authority.tick()
        assert authority.run["status"] == "finishing"
        assert authority.run["requested_terminal"] == "completed"
        assert authority.run["recovery_episodes"] == 0
        assert authority.run["first_cause"] is None
        assert authority.run["deadline"] == deadline
        assert e.task.original_seed == seed
        assert all(
            all(a[k] == 0 for k in module().ZERO_BODY)
            for a in host.actions
            if a["_am1_local_control"]["intent_revision"] == revision
        )
    finally:
        authority.close()
        e.worker.join(2)


def test_expected_transport_loss_survives_and_partial_finish_stays_uncertain():
    host = ControlledHost()
    e = executor(host)
    try:
        begin(e)
        host.healthy = False
        until(lambda: not e.evidence()["feedback"])
        assert e.worker.is_alive()
        assert e.evidence()["fault"] is None
        host.clean = False
        assert e.finish("stopped")["pending"]
        until(lambda: e.evidence()["lifecycle"]["phase"] == "terminal")
        assert e.evidence()["lifecycle"]["uncertain"]
        assert e.evidence()["lifecycle"]["cleanup"] == "physical_cleanup_unknown"
        assert host.finished == 1
        assert not e.reconcile()
    finally:
        e.close()
        e.worker.join(2)


@pytest.mark.parametrize(
    "receipt",
    [
        None,
        {
            "run_id": "other",
            "host_incarnation": "host",
            "cleanup": "physical_readback_verified",
            "uncertain": False,
        },
        {
            "run_id": "run",
            "host_incarnation": "host",
            "cleanup": "physical_cleanup_unknown",
            "uncertain": True,
        },
    ],
)
def test_process_exit_or_foreign_receipt_cannot_certify_cleanup(receipt):
    m = module()
    assert not m.qualified_cleanup_receipt(receipt, "run", "host")


def test_only_exact_real_readback_and_camera_release_complete_cleanup():
    m = module()
    receipt = {
        "version": 1,
        "run_id": "run",
        "host_incarnation": "host",
        "cleanup": "physical_readback_verified",
        "uncertain": False,
        "cleanup_errors": [],
        "readback": {
            "verified": True,
            "lift_readback": "qualified",
            "selected_gain_restore_required": False,
            "readbacks": [
                {
                    "bus": "left" if "_left_" in k else "right",
                    "motor": k.removesuffix(".pos"),
                    "register": "Torque_Enable",
                    "value": 0,
                }
                for k in JOINT_KEYS
            ]
            + [
                {"bus": "left", "motor": k, "register": reg, "value": 0}
                for k in m.BODY_MOTORS
                for reg in ("Torque_Enable", "Goal_Velocity")
            ],
        },
    }
    assert m.qualified_cleanup_receipt(receipt, "run", "host")
    receipt["readback"]["verified"] = False
    assert not m.qualified_cleanup_receipt(receipt, "run", "host")


def test_normal_client_can_use_explicit_local_transport_endpoints():
    from lerobot.robots.alohamini.alohamini_client import AlohaMiniClient
    from lerobot.robots.alohamini.config_alohamini import AlohaMiniClientConfig
    from tests.robots.test_alohamini_client_connection import observation_peer

    with observation_peer() as peer:
        config = AlohaMiniClientConfig(
            remote_ip="unreachable.invalid",
            cameras={},
            connect_timeout_s=1,
            command_send_timeout_ms=40,
            command_endpoint=f"tcp://127.0.0.1:{peer.command_port}",
            observation_endpoint=f"tcp://127.0.0.1:{peer.observation_port}",
        )
        client = AlohaMiniClient(config)
        client.connect()
        try:
            client.send_action({"x": 1})
            until(lambda: len(peer.actions) > 0)
        finally:
            client.disconnect()


def test_physical_units_are_finite_and_host_only_receives_device_access():
    m = module()
    command = m.protected_unit_command(
        "am1-pa01-host-test",
        "/tmp/run",
        ["/python", "-m", "protected-host"],
        environment={"PYTHONPATH": "/source/src"},
        runtime_s=720,
        stop_s=80,
        devices=True,
    )
    assert command[:2] == ["systemd-run", "--user"]
    assert "--wait" in command and "--collect" in command
    assert "PrivateDevices=no" in command
    assert "KillMode=mixed" in command and "KillSignal=SIGINT" in command
    assert "RuntimeMaxSec=720s" in command and "TimeoutStopSec=80s" in command
    assert "Restart=no" in command
    assert "RestrictAddressFamilies=AF_UNIX AF_INET" in command
    assert "/python" in command


def test_camera_owned_unit_allows_route_discovery_without_expanding_host(tmp_path, monkeypatch):
    import subprocess
    from uuid import uuid4

    from examples.alohamini.am1_session_contract import RECIPES

    m = module()
    state, runs = tmp_path / "state", tmp_path / "runs"
    state.mkdir(mode=0o700)
    runs.mkdir(mode=0o700)
    provider = object.__new__(m.ProtectedHostRun)
    provider.config = {
        "physical_state_directory": str(state),
        "run_root": str(runs),
        "host_head": "a" * 40,
        "host_repository": str(tmp_path),
        "host_python": "unused-python",
        "camera_python": "unused-python",
        "camera_repository": str(tmp_path),
        "camera_config": str(tmp_path / "config"),
        "camera_credentials": str(tmp_path / "credentials"),
        "camera_binary": str(tmp_path / "go2rtc"),
        "camera_state": str(tmp_path / "camera"),
    }
    provider.observation = SimpleNamespace(evidence=lambda: {"qualified": True})
    monkeypatch.setattr(provider, "_validate_source", lambda: None)
    launches = []

    def capture_launch(command, **kwargs):
        launches.append(command)
        if any(arg.startswith("--unit=am1-pa01-host-") for arg in command):
            raise OSError("injected stop before actual host launch")
        return SimpleNamespace(poll=lambda: None)

    monkeypatch.setattr(subprocess, "Popen", capture_launch)
    try:
        with pytest.raises(OSError, match="injected stop"):
            provider.start(str(uuid4()), str(uuid4()), RECIPES["physical-live-teleop"], lambda: None)
        camera, host = launches
        assert "RestrictAddressFamilies=AF_UNIX AF_INET AF_NETLINK" in camera
        assert "RestrictAddressFamilies=AF_UNIX AF_INET" in host
        assert "RuntimeMaxSec=590.0s" in camera and "TimeoutStopSec=20s" in camera
        assert "KillSignal=SIGTERM" in camera
        assert "RuntimeMaxSec=720s" in host and "TimeoutStopSec=80s" in host
        assert "KillSignal=SIGINT" in host
        for command in launches:
            assert command[:2] == ["systemd-run", "--user"]
            assert all(
                value in command
                for value in (
                    "--wait",
                    "--collect",
                    "NoNewPrivileges=yes",
                    "PrivateDevices=no",
                    "KillMode=mixed",
                    "Restart=no",
                    "UMask=0077",
                    "StandardError=inherit",
                )
            )
            assert sum(value.startswith("RestrictAddressFamilies=") for value in command) == 1
            assert not any("AF_INET6" in value for value in command)
    finally:
        for stream in (getattr(provider, "host_log", None), getattr(provider, "camera_log", None)):
            if stream:
                stream.close()


@pytest.mark.parametrize("option", [{}, {"camera_route_discovery": False}])
def test_route_discovery_is_disabled_for_default_protected_units(option):
    command = module().protected_unit_command(
        "am1-default-test",
        "/tmp/run",
        ["/python", "-m", "owner"],
        environment={},
        runtime_s=720,
        stop_s=80,
        devices=False,
        **option,
    )
    assert "RestrictAddressFamilies=AF_UNIX AF_INET" in command
    assert "PrivateDevices=yes" in command
    assert not any("AF_NETLINK" in value for value in command)


@pytest.mark.parametrize("option", [None, 0, 1, "true", "false", []])
def test_camera_route_discovery_requires_an_explicit_boolean(option):
    with pytest.raises(ValueError, match="camera route discovery.*bool"):
        module().protected_unit_command(
            "am1-default-test",
            "/tmp/run",
            ["/python"],
            environment={},
            runtime_s=720,
            stop_s=80,
            devices=False,
            camera_route_discovery=option,
        )


def test_live_selection_requires_explicit_backend_and_cannot_contaminate_default_sim(tmp_path):
    module()
    from tools.am1_pi_executor import select_executor

    kwargs = {
        "admission_directory": tmp_path / "sim",
        "shoulder_amplitude": 1.5,
        "observation": None,
        "sensing_policy": None,
    }
    sim = select_executor("simulated", None, **kwargs)
    assert sim.source == "simulated-provider"
    assert not sim.supports(
        __import__("examples.alohamini.am1_session_contract", fromlist=["RECIPES"]).RECIPES[
            "physical-arm-smoke"
        ]
    )
    with pytest.raises(ValueError):
        select_executor("simulated", {"version": 1}, **kwargs)
    with pytest.raises(ValueError):
        select_executor("protected-physical", None, **kwargs)


def test_required_camera_qualifies_before_physical_host_launch():
    m = module()
    seen = []
    camera = SimpleNamespace(poll=lambda: None)
    o = SimpleNamespace(evidence=lambda: {"qualified": len(seen) >= 3})

    def check():
        seen.append("cancel")

    m.wait_required_camera(camera, o, check, sleep=lambda _: None)
    assert len(seen) == 3
    camera.poll = lambda: 1
    with pytest.raises(RuntimeError, match="camera exited"):
        m.wait_required_camera(camera, o, check, sleep=lambda _: None)


def test_send_timeout_holds_without_killing_the_native_worker():
    host = ControlledHost()
    e = executor(host)
    try:
        begin(e)
        normal = host.send
        host.send = lambda _: (_ for _ in ()).throw(module().FeedbackUnavailable("send timeout"))
        until(lambda: e.evidence()["transport_issue"] == "send timeout")
        assert e.worker.is_alive() and e.evidence()["fault"] is None
        host.send = normal
        until(lambda: e.evidence()["hold_acknowledged"])
    finally:
        e.close()
        e.worker.join(2)


def test_stop_during_startup_is_a_clean_cancel_not_a_physical_fault():
    import threading

    host = ControlledHost()
    e = executor(host)
    entered = threading.Event()

    def blocked(run_id, incarnation, recipe, cancel):
        entered.set()
        while True:
            cancel()
            time.sleep(0.005)

    host.start = blocked
    try:
        from examples.alohamini.am1_session_contract import RECIPES

        r = RECIPES["physical-arm-smoke"]
        e.bind_run("run", r)
        e.begin(r)
        assert entered.wait(1)
        assert e.finish("stopped")["pending"]
        until(lambda: e.evidence()["lifecycle"]["phase"] == "terminal")
        assert e.evidence()["fault"] is None
        assert e.evidence()["lifecycle"]["terminal_status"] == "stopped"
    finally:
        e.close()
        e.worker.join(2)


def test_unexpected_native_watchdog_pause_requires_a_new_measured_hold_epoch():
    host = ControlledHost()
    e = executor(host)
    try:
        r = begin(e)
        until(lambda: e.dispatch(r))
        active = e.evidence()["accepted_host_action"]["epoch"]
        host.marker["state"] = "paused"
        until(lambda: e.evidence()["accepted_host_action"]["epoch"] > active)
        assert e.evidence()["accepted_host_action"]["state"] == "paused"
        assert not e._dispatch_ack
        until(lambda: e.evidence()["pose_aligned"])
        until(lambda: e.dispatch(r))
        assert e.evidence()["accepted_host_action"]["epoch"] == active + 2
        assert e.evidence()["accepted_host_action"]["state"] == "active"
        assert e.task.admissions == 1
    finally:
        e.close()
        e.worker.join(2)


def test_monitor_exit_is_not_actual_unit_exit_or_camera_release(monkeypatch):
    import subprocess

    m = module()
    unit = object.__new__(m.OwnedUnit)
    unit.name = "am1-pa01-host-test"
    unit.monitor = SimpleNamespace(poll=lambda: 1)
    outputs = [
        "Id=am1-pa01-host-test.service\nLoadState=loaded\nActiveState=active\nMainPID=42\nControlPID=0\n",
        "Id=am1-pa01-host-test.service\nLoadState=not-found\nActiveState=inactive\nMainPID=0\nControlPID=0\nJob=\n",
    ]

    def run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout=outputs.pop(0))

    monkeypatch.setattr(subprocess, "run", run)
    assert unit.poll() == 1
    assert not unit.actual_exited()
    assert unit.actual_exited()


def test_pending_launcher_is_not_exit_even_if_unit_is_not_registered(monkeypatch):
    import subprocess

    unit = object.__new__(module().OwnedUnit)
    unit.name = "am1-pa01-host-pending"
    unit.monitor = SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=0,
            stdout="Id=am1-pa01-host-pending.service\nLoadState=not-found\nActiveState=inactive\nMainPID=0\nControlPID=0\nJob=\n",
        ),
    )
    assert not unit.actual_exited()


def test_pending_start_job_is_not_exit_after_monitor_completion(monkeypatch):
    import subprocess

    unit = object.__new__(module().OwnedUnit)
    unit.name = "am1-pa01-host-pending"
    unit.monitor = SimpleNamespace(poll=lambda: 1)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=0,
            stdout="Id=am1-pa01-host-pending.service\nLoadState=loaded\nActiveState=inactive\nMainPID=0\nControlPID=0\nJob=123\n",
        ),
    )
    assert not unit.actual_exited()


def test_ordinary_stop_waits_for_pending_registration_then_stops_once(monkeypatch):
    import subprocess

    unit = object.__new__(module().OwnedUnit)
    unit.name = "am1-pa01-host-pending"
    unit.monitor = SimpleNamespace(poll=lambda: None)
    unit.stopping = False
    seen = []
    outputs = ["not-found", "loaded"]

    def run(args, **kwargs):
        seen.append(args)
        if "show" in args:
            state = outputs.pop(0)
            return SimpleNamespace(
                returncode=0,
                stdout=f"Id={unit.name}.service\nLoadState={state}\nActiveState=activating\nMainPID=0\nControlPID=0\nJob=123\n",
            )
        assert args == ["systemctl", "--user", "--no-block", "stop", unit.name]
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(module().time, "sleep", lambda _: None)
    unit.stop()
    unit.stop()
    assert len([args for args in seen if "stop" in args]) == 1
    assert len([args for args in seen if "show" in args]) == 2


def test_bounded_body_and_request_evidence_cannot_promote_client_default_zero():
    m = module()
    client = client_sample()
    observation = client.get_observation()
    observation.update(
        {"x.vel": 0.1, "y.vel": 0.0, "theta.vel": 0.0, "lift_axis.height_mm": 12.0, "lift_axis.vel": 0}
    )
    client.latest_raw_observation_keys = frozenset(
        (*JOINT_KEYS, "x.vel", "y.vel", "theta.vel", "lift_axis.height_mm")
    )
    sample = m.read_physical_sample(client, "run", "host", 4, clock=lambda: 10.0)
    assert sample["body_feedback"] == {
        "x.vel": 0.1,
        "y.vel": 0.0,
        "theta.vel": 0.0,
        "lift_axis.height_mm": 12.0,
    }
    assert "lift_axis.vel" not in sample["body_feedback"]
    assert sample["timing"]["reply_received_at_monotonic_s"] == 9.99
    assert sample["timing"]["original_request_sent_at_monotonic_s"] == pytest.approx(9.96)
    host = ControlledHost()
    e = executor(host)
    try:
        e.sample = sample
        evidence = e.evidence()
        assert evidence["measured_body"] == sample["body_feedback"]
        assert evidence["feedback_timing"] == sample["timing"]
        evidence["measured_body"]["x.vel"] = 9.0
        assert e.sample["body_feedback"]["x.vel"] == 0.1
    finally:
        e.close()


class BodyTranslationHost(ControlledHost):
    """Real client/host/lift translation, with an inert recording bus only."""

    def __init__(self, clock, *, height=12.3, homed=True, explicit_zero=False, preparing=False):
        super().__init__()
        from lerobot.robots.alohamini.alohamini import AlohaMini
        from lerobot.robots.alohamini.alohamini_client import AlohaMiniClient
        from lerobot.robots.alohamini.config_alohamini import AlohaMiniClientConfig
        from lerobot.robots.alohamini.lift_axis import LiftAxis, LiftAxisConfig

        class RecordingBus:
            is_connected = True
            motors = {"lift_axis": object()}

            def __init__(self):
                self.rows = []

            def write(self, register, motor, value, **kwargs):
                self.rows.append((register, motor, value))

            def sync_write(self, register, values, **kwargs):
                self.rows.append((register, "base", dict(values)))

        self.clock, self.preparing = clock, preparing
        self.explicit_zero, self.height = explicit_zero, height
        self.records, self.body_requests = [], []
        self.bus = RecordingBus()
        self.axis = LiftAxis(LiftAxisConfig(), self.bus, None)
        self.axis.is_homed = homed
        self.client = object.__new__(AlohaMiniClient)
        self.client.teleop_keys = AlohaMiniClientConfig(remote_ip="127.0.0.1", cameras={}).teleop_keys
        self.client.last_remote_state = {"lift_axis.height_mm": height}
        self.client.speed_levels, self.client.speed_index = [{"xy": 0.15, "theta": 45}], 0
        self.robot = object.__new__(AlohaMini)
        self.robot.config = SimpleNamespace(robot_model="alohamini1", max_relative_target=20.0)
        self.robot.cameras, self.robot.left_bus, self.robot.right_bus = {}, self.bus, None
        self.robot.base_motors = []
        self.robot.wheel_radius, self.robot.base_radius = 0.05, 0.15
        self.robot.lift, self.robot.logs = self.axis, {}
        self.robot._lift_operation = SimpleNamespace(
            apply_action=lambda action: self.axis.apply_action(action, height_mm=height)
        )
        # Arm acquisition is ControlledHost's measured fixture; no arm devices exist.
        self.robot.hold_follower_arms = lambda: None
        self.robot._limit_gripper_goal_by_current = lambda _bus, goals: goals
        self.robot._limit_joint_goal_by_current = lambda _bus, goals: goals
        self.robot._encode_am1_arm_goals = lambda _bus, goals, _raw: (goals, None)

    def start(self, run_id, incarnation, recipe, cancel):
        from lerobot.robots.alohamini.alohamini_host import AM1LocalControl

        super().start(run_id, incarnation, recipe, cancel)
        self.control = AM1LocalControl(run_id=run_id, host_incarnation=incarnation)

    def poll(self, previous):
        self.clock.advance(0.1)
        sample = super().poll(previous)
        sample["at"] = self.clock()
        if self.preparing:
            sample["positions"]["arm_left_shoulder_lift.pos"] = 110.0
        return sample

    def body_action(self, elapsed):
        body = module().body_recipe_action(self.client, elapsed)
        if self.explicit_zero:
            body["lift_axis.vel"] = 0
        self.body_requests.append((elapsed, dict(body)))
        return body

    def send(self, action):
        before = len(self.bus.rows)
        assert self.control.apply(self.robot, action)
        writes = self.bus.rows[before:]
        lift = [value for register, motor, value in writes if motor == "lift_axis"]
        self.records.append(
            {"action": dict(action), "lift_writes": lift, "accepted": self.control.accepted_action}
        )
        super().send(action)

    def finish(self, status):
        self.robot.stop_motion()
        return super().finish(status)


def body_translation_executor(**kwargs):
    from examples.alohamini.am1_session_contract import RECIPES
    from tests.robots.test_am1_persistent_session import Clock

    clock = Clock()
    host = BodyTranslationHost(clock, **kwargs)
    e = module().PhysicalExecutor(
        {"shoulder_amplitude": 1.5, "source_pins": {}},
        observation(),
        clock=clock,
        io_factory=lambda *_: host,
    )
    recipe = RECIPES["physical-arm-hold-body"]
    e.bind_run("10000000-0000-4000-8000-000000000001", recipe)
    assert e.begin(recipe)
    until(lambda: e.evidence()["lifecycle"]["phase"] == "live")
    until(lambda: e.dispatch(recipe))
    return host, e


def test_worker_body_height_pulses_reach_original_lift_and_release_zero():
    host, e = body_translation_executor()
    try:
        until(lambda: e.advance(host.clock(), 0)["progress_s"] >= 4.4, timeout=4)
        up = [r for r in host.records if r["action"].get("lift_axis.height_mm") == host.height + 50]
        down = [r for r in host.records if r["action"].get("lift_axis.height_mm") == host.height - 50]
        assert up and down
        assert all(r["lift_writes"][-1] == -1300 for r in up)
        assert all(r["lift_writes"][-1] == 1300 for r in down)
        assert all("lift_axis.vel" not in r["action"] for r in up + down)
        assert any(r["accepted"].get("x.vel") == 0.15 for r in host.records if r["accepted"])
        assert any(r["accepted"].get("theta.vel") == 45 for r in host.records if r["accepted"])
        released = [
            r
            for r in host.records
            if "lift_axis.height_mm" not in r["action"]
            and r["action"]["_am1_local_control"]["mode"] == "active"
        ]
        assert released and all(r["lift_writes"][-1] == 0 for r in released)
        assert all(r["action"]["lift_axis.vel"] == 0 for r in released)
    finally:
        e.close()
        e.worker.join(2)


def test_worker_explicit_velocity_zero_still_overrides_height_request():
    host, e = body_translation_executor(explicit_zero=True)
    try:
        until(lambda: any(r["action"].get("lift_axis.height_mm") == host.height + 50 for r in host.records))
        up = [r for r in host.records if r["action"].get("lift_axis.height_mm") == host.height + 50]
        assert up and all(r["action"]["lift_axis.vel"] == 0 for r in up)
        assert all(r["lift_writes"] == [-1300, 0] for r in up)
    finally:
        e.close()
        e.worker.join(2)


@pytest.mark.parametrize(
    "options,target_delta",
    [({"height": 5.0}, -50), ({"height": 600.0}, 50), ({"homed": False}, 50)],
)
def test_worker_height_translation_retains_lift_floor_soft_max_and_home_guards(options, target_delta):
    host, e = body_translation_executor(**options)
    try:
        until(
            lambda: any(
                r["action"].get("lift_axis.height_mm") == host.height + target_delta for r in host.records
            )
        )
        pulse = [
            r for r in host.records if r["action"].get("lift_axis.height_mm") == host.height + target_delta
        ]
        assert pulse and all(r["lift_writes"][-1] == 0 for r in pulse)
    finally:
        e.close()
        e.worker.join(2)


@pytest.mark.parametrize("interruption", ["hold", "pause", "stop", "input_loss"])
def test_worker_body_pulse_interruption_returns_actual_zero(interruption):
    host, e = body_translation_executor()
    try:
        until(lambda: any(r["lift_writes"] and r["lift_writes"][-1] == -1300 for r in host.records))
        if interruption == "stop":
            e.finish("stopped")
            until(lambda: e.evidence()["lifecycle"]["phase"] == "terminal")
        else:
            if interruption == "input_loss":
                e.observation.qualified = False
            else:
                if interruption == "pause":
                    e.set_intent_revision(1)
                e.hold()
            until(lambda: e.evidence()["hold_acknowledged"])
            assert all(host.records[-1]["action"][k] == 0 for k in module().ZERO_BODY)
            assert "lift_axis.height_mm" not in host.records[-1]["action"]
        assert [v for _, motor, v in host.bus.rows if motor == "lift_axis"][-1] == 0
        assert e.evidence()["fault"] is None
    finally:
        e.close()
        e.worker.join(2)


def test_worker_body_preparation_never_calls_or_applies_height_recipe():
    host, e = body_translation_executor(preparing=True)
    try:
        until(lambda: e.advance(host.clock(), 0)["preparation_s"] >= 1)
        assert e.advance(host.clock(), 0)["preparing"]
        assert host.body_requests == []
        assert all(all(r["action"][k] == 0 for k in module().ZERO_BODY) for r in host.records)
        assert all("lift_axis.height_mm" not in r["action"] for r in host.records)
        assert all(v == 0 for _, motor, v in host.bus.rows if motor == "lift_axis")
    finally:
        e.close()
        e.worker.join(2)


def operational_body_translation_host(qualified_idle, monkeypatch):
    """Keep the actual protected lift operation; record its existing inert bus writes."""
    robot, clock = qualified_idle
    host = BodyTranslationHost(clock.monotonic, height=robot._lift_operation.height_mm)
    host.robot, host.axis, host.bus = robot, robot.lift, robot.left_bus
    host.bus.rows = []
    write = host.bus.write

    def recorded_write(register, motor, value, **options):
        write(register, motor, value, **options)
        host.bus.rows.append((register, motor, value))

    monkeypatch.setattr(host.bus, "write", recorded_write)
    monkeypatch.setattr(robot, "hold_follower_arms", lambda: None)
    host.start(
        "10000000-0000-4000-8000-000000000001",
        "20000000-0000-4000-8000-000000000001",
        "physical-arm-hold-body",
        None,
    )
    return host, robot._lift_operation, clock


def send_operational_body_recipe(host, elapsed, *, action_overrides=None):
    action = host.body_action(elapsed)
    action.update(action_overrides or {})
    action["_am1_local_control"] = {
        "version": 1,
        "mode": "active",
        "epoch": 0,
        "run_id": host.run_id,
        "host_incarnation": host.incarnation,
        "intent_revision": 0,
        "action_sequence": len(host.records),
    }
    host.send(action)


@pytest.mark.parametrize("elapsed", [1.0, 2.0, 2.2, 2.7, 2.9, 3.6, 4.3, 11.9])
def test_finite_body_release_writes_zero_without_requalifying_uncertain_operational_lift(
    qualified_idle,
    monkeypatch,
    elapsed,
):
    host, op, clock = operational_body_translation_host(qualified_idle, monkeypatch)
    for _ in range(3):
        poll_idle_feedback(host.robot, clock, velocity=50)
    assert op._idle_uncertain
    qualified_at, anchor, zero_reference = op._idle_qualified_at, op._last_idle_height, op.lift._z0_deg
    band, samples, thermal = op._idle_uncertain_band, list(op._stationary), list(op.temperature.samples)
    reads = len(op.transport.group_reads)
    send_operational_body_recipe(host, elapsed)
    assert host.records[-1]["lift_writes"] == [0]
    assert host.records[-1]["action"]["lift_axis.vel"] == 0
    assert "lift_axis.height_mm" not in host.records[-1]["action"]
    assert host.records[-1]["accepted"]["x.vel"] == (0.15 if elapsed == 2.0 else 0)
    assert host.records[-1]["accepted"]["theta.vel"] == (45 if elapsed == 2.7 else 0)
    assert op._idle_uncertain and op._idle_qualified_at == qualified_at
    assert op._idle_uncertain_band == band and list(op._stationary) == samples
    assert op._last_idle_height == anchor and op.lift._z0_deg == zero_reference
    assert list(op.temperature.samples) == thermal and len(op.transport.group_reads) == reads
    assert op.failure is None and op.bus.expected_goal == 0


@pytest.mark.parametrize(
    "elapsed,overrides",
    [
        (3.4, None),
        (4.1, None),
        (3.4, {"lift_axis.vel": 0}),  # Arbitrary height + explicit zero remains a height request.
        (1.0, {"lift_axis.vel": 200}),
        (1.0, {"lift_axis.vel": -200}),
    ],
)
def test_finite_body_new_lift_motion_still_refuses_uncertain_operational_feedback(
    qualified_idle,
    monkeypatch,
    elapsed,
    overrides,
):
    host, op, clock = operational_body_translation_host(qualified_idle, monkeypatch)
    for _ in range(3):
        poll_idle_feedback(host.robot, clock, velocity=-50)
    assert op._idle_uncertain
    before = len(host.bus.rows)
    with pytest.raises(RuntimeError, match="new lift motion refused.*uncertain") as caught:
        send_operational_body_recipe(host, elapsed, action_overrides=overrides)
    assert op.failure is caught.value and op._idle_uncertain
    assert len(host.bus.rows) == before and op.bus.expected_goal == 0


def test_finite_body_lift_pulse_requires_genuine_operational_requalification(qualified_idle, monkeypatch):
    host, op, clock = operational_body_translation_host(qualified_idle, monkeypatch)
    for _ in range(3):
        poll_idle_feedback(host.robot, clock, velocity=50)
    assert op._idle_uncertain
    zero_reference, anchor = op.lift._z0_deg, op._last_idle_height
    qualified_at = op._idle_qualified_at
    send_operational_body_recipe(host, 2.2)
    assert op._idle_uncertain and op._idle_qualified_at == qualified_at
    for _ in range(7):
        poll_idle_feedback(host.robot, clock)
    assert not op._idle_uncertain and op._idle_qualified_at > qualified_at
    send_operational_body_recipe(host, 3.4)
    assert host.records[-1]["lift_writes"] == [-1300]
    assert host.records[-1]["action"]["lift_axis.height_mm"] == host.height + 50
    assert "lift_axis.vel" not in host.records[-1]["action"]
    assert op.lift._z0_deg == zero_reference and op._last_idle_height == anchor
    assert op.failure is None


def test_finite_body_zero_releases_cannot_extend_operational_uncertainty_deadline(
    qualified_idle,
    monkeypatch,
):
    host, op, clock = operational_body_translation_host(qualified_idle, monkeypatch)
    for _ in range(3):
        poll_idle_feedback(host.robot, clock, velocity=50)
    qualified_at = op._idle_qualified_at
    assert op._idle_uncertain
    with pytest.raises(RuntimeError, match="failed stopped qualification within 1 s") as caught:
        for _ in range(40):
            send_operational_body_recipe(host, 2.2)
            assert op._idle_uncertain and op._idle_qualified_at == qualified_at
            poll_idle_feedback(host.robot, clock, velocity=50)
    assert op.failure is caught.value and op.bus.expected_goal == 0
    assert 1.0 <= clock.now - qualified_at < 1.036
    before = len(host.bus.rows)
    with pytest.raises(RuntimeError) as repeated:
        send_operational_body_recipe(host, 2.2)
    assert repeated.value is caught.value and len(host.bus.rows) == before


def terminal_cleanup_outcome(run_id, incarnation, status):
    """Complete inert host outcome matching the existing twenty-read cleanup contract."""
    joints = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")
    readbacks = [
        {"bus": side, "motor": f"arm_{side}_{joint}", "register": "Torque_Enable", "value": 0}
        for side in ("left", "right")
        for joint in joints
    ] + [
        {"bus": "left", "motor": motor, "register": register, "value": 0}
        for motor in ("base_left_wheel", "base_back_wheel", "base_right_wheel", "lift_axis")
        for register in ("Torque_Enable", "Goal_Velocity")
    ]
    receipt = {
        "version": 1,
        "run_id": run_id,
        "host_incarnation": incarnation,
        "cleanup": "physical_readback_verified",
        "uncertain": False,
        "cleanup_errors": [],
        "first_cause": "ComparisonRefusal: baseline: persistent motion",
        "readback": {
            "verified": True,
            "buses_closed": True,
            "lift_readback": "qualified",
            "selected_gain_restore_required": False,
            "cleanup_errors": [],
            "readbacks": readbacks,
        },
    }
    return {
        "cleanup": "physical_readback_verified",
        "uncertain": False,
        "terminal_status": status,
        "first_cause": receipt["first_cause"],
        "readback": receipt,
        "backend_incarnation": incarnation,
        "cleanup_errors": [],
        "camera_release": {
            "version": 1,
            "session_id": run_id,
            "state": "released",
            "errors": [],
            "runtime_failed": False,
            "release_evidence": {
                "backend_exited": True,
                "backend_returncode": 0,
                "readers_joined": True,
                "viewer_socket_closed": True,
                "runtime_config_removed": True,
            },
        },
    }


class StartupFaultThenBlockedHost(ControlledHost):
    def __init__(self):
        super().__init__()
        self.cleanup_release = threading.Event()
        self.next_start_entered = threading.Event()
        self.next_start_release = threading.Event()

    def start(self, run_id, incarnation, recipe, cancel):
        super().start(run_id, incarnation, recipe, cancel)
        if self.started == 1:
            raise RuntimeError(
                "protected host exited during startup; inspect original host log and cleanup receipt"
            )
        self.next_start_entered.set()
        while not self.next_start_release.wait(0.005):
            cancel()

    def finish(self, status):
        if self.started == 1:
            assert self.cleanup_release.wait(2)
        return terminal_cleanup_outcome(self.run_id, self.incarnation, status)


def historical_fault_authority(tmp_path):
    from tests.robots.test_am1_persistent_session import start
    from tools.am1_session_core import SessionAuthority

    host = StartupFaultThenBlockedHost()
    e = executor(host)
    authority = SessionAuthority(tmp_path, executor=e)
    first = start(authority, "physical-arm-hold-body")
    until(lambda: e.evidence()["fault"] is not None)
    authority.tick()
    assert authority.run["status"] == "finishing"
    host.cleanup_release.set()
    until(lambda: e.evidence()["lifecycle"]["phase"] == "terminal")
    e.worker.join(2)
    authority.tick()
    assert authority.run["status"] == "faulted"
    assert authority.run["native_live_at"] is None and authority.run["deadline"] is None
    assert authority.run["uncertain"] is False and authority.run["cleanup"] == "physical_readback_verified"
    assert len(authority.run["physical_result"]["readback"]["readback"]["readbacks"]) == 20
    return authority, e, host, first


def test_terminal_start_after_known_startup_fault_requires_new_native_admission(tmp_path):
    from tests.robots.test_am1_persistent_session import start

    authority, e, host, first = historical_fault_authority(tmp_path)
    try:
        original = authority.run
        saved = copy.deepcopy(original)
        fault = e.evidence()["fault"]
        assert fault == original["first_cause"]
        assert e.evidence()["feedback"] is False and e.evidence()["native_ack"] is False
        old_incarnation = e.host_incarnation
        old_operation = authority.db.execute(
            "SELECT result FROM operations WHERE id=?", (first["operation_id"],)
        ).fetchone()
        result = start(authority, "physical-arm-hold-body")
        assert result["accepted"]  # Existing unconditional historical-fault veto deadlocks here.
        assert result["status"] == "starting" and result["effect_admitted"] is False
        assert host.next_start_entered.wait(1)
        assert result["run_id"] != first["run_id"] and e.host_incarnation != old_incarnation
        assert authority.run["deadline"] is None and authority.run["native_live_at"] is None
        assert authority.run["progress_s"] == 0 and authority.run["recovery_episodes"] == 0
        assert saved == original  # Original fault, native cause and full cleanup remain immutable.
        assert (
            authority.db.execute(
                "SELECT result FROM operations WHERE id=?", (first["operation_id"],)
            ).fetchone()
            == old_operation
        )
        assert e.evidence()["fault"] is None  # Only ordinary accepted new-run bind resets its cache.
        host.next_start_release.set()
        until(lambda: e.evidence()["lifecycle"]["phase"] == "live")
        authority.tick()
        assert authority.run["backend_incarnation"] == e.host_incarnation
        assert (
            authority.run["native_live_at"] is not None
            and authority.run["deadline"] > authority.run["native_live_at"]
        )
        assert saved == original
    finally:
        host.cleanup_release.set()
        host.next_start_release.set()
        authority.close()
        e.worker.join(2)


@pytest.mark.parametrize(
    "changed",
    [
        "uncertain",
        "unknown_cleanup",
        "not_terminal",
        "foreign_lifecycle",
        "foreign_incarnation",
        "foreign_receipt",
        "missing_row",
        "bool_zero",
        "malformed_row",
        "unqualified_lift",
        "readback_unverified",
        "bool_receipt_version",
        "missing_restore_flag",
        "bus_open",
        "camera_foreign",
        "camera_backend_live",
        "camera_exit_failure",
        "worker",
        "reconciler",
    ],
)
def test_terminal_start_refuses_unqualified_or_active_previous_physical_owner(tmp_path, changed):
    from tests.robots.test_am1_persistent_session import start

    authority, e, host, _ = historical_fault_authority(tmp_path)
    try:
        lifecycle = e._lifecycle
        receipt = lifecycle["readback"]
        readback = receipt["readback"]
        camera = lifecycle["camera_release"]
        if changed == "uncertain":
            lifecycle["uncertain"] = True
        elif changed == "unknown_cleanup":
            lifecycle["cleanup"] = "physical_cleanup_unknown"
        elif changed == "not_terminal":
            lifecycle["phase"] = "finishing"
        elif changed == "foreign_lifecycle":
            lifecycle["run_id"] = "10000000-0000-4000-8000-000000000001"
        elif changed == "foreign_incarnation":
            lifecycle["backend_incarnation"] = "10000000-0000-4000-8000-000000000001"
        elif changed == "foreign_receipt":
            receipt["host_incarnation"] = "10000000-0000-4000-8000-000000000001"
        elif changed == "missing_row":
            readback["readbacks"].pop()
        elif changed == "bool_zero":
            readback["readbacks"][0]["value"] = False
        elif changed == "malformed_row":
            readback["readbacks"][0] = None
        elif changed == "unqualified_lift":
            readback["lift_readback"] = "unknown"
        elif changed == "readback_unverified":
            readback["verified"] = False
        elif changed == "bool_receipt_version":
            receipt["version"] = True
        elif changed == "missing_restore_flag":
            readback.pop("selected_gain_restore_required")
        elif changed == "bus_open":
            readback["buses_closed"] = False
        elif changed == "camera_foreign":
            camera["session_id"] = "10000000-0000-4000-8000-000000000001"
        elif changed == "camera_backend_live":
            camera["release_evidence"]["backend_exited"] = False
        elif changed == "camera_exit_failure":
            camera["release_evidence"]["backend_returncode"] = 1
        elif changed == "worker":
            e.worker = SimpleNamespace(is_alive=lambda: True, join=lambda *_: None)
        elif changed == "reconciler":
            e._reconciliation_worker = SimpleNamespace(
                thread=SimpleNamespace(is_alive=lambda: True),
                state="pending",
                error=None,
                close=lambda: None,
            )
        authority.run["physical_result"] = copy.deepcopy(lifecycle)
        saved = copy.deepcopy(authority.run)
        fault = e.evidence()["fault"]
        result = start(authority, "physical-arm-hold-body")
        assert not result["accepted"] and not result["effect_admitted"]
        assert authority.run == saved and e.evidence()["fault"] == fault
        assert host.started == 1 and not host.next_start_entered.is_set()
    finally:
        host.cleanup_release.set()
        host.next_start_release.set()
        authority.close()
        e.worker.join(2)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_terminal_start_other_executor_fault_has_no_physical_exception(tmp_path, asynchronous):
    from tests.robots.test_am1_persistent_session import start
    from tools.am1_fake_executor import FakeExecutor
    from tools.am1_session_core import SessionAuthority

    e = FakeExecutor()
    e.asynchronous = asynchronous
    e.fault = "current fake fault"
    authority = SessionAuthority(tmp_path, executor=e)
    try:
        result = start(authority)
        assert not result["accepted"] and not result["effect_admitted"]
        assert authority.run is None and e.fault == "current fake fault"
    finally:
        authority.close()


def test_terminal_start_current_evidence_failure_cannot_inherit_historical_permission(tmp_path):
    from tests.robots.test_am1_persistent_session import start

    authority, e, host, _ = historical_fault_authority(tmp_path)
    original_evidence = e.observation.evidence
    fault = e.evidence()["fault"]

    def unavailable():
        raise RuntimeError(fault)  # Even identical wording is a current adapter failure.

    try:
        e.observation.evidence = unavailable
        result = start(authority, "physical-arm-hold-body")
        assert not result["accepted"] and not result["effect_admitted"]
        assert authority.run["status"] == "faulted" and e._fault == fault
        assert host.started == 1
    finally:
        e.observation.evidence = original_evidence
        host.cleanup_release.set()
        host.next_start_release.set()
        authority.close()
        e.worker.join(2)


class LiveFeedbackFaultThenBlockedHost(StartupFaultThenBlockedHost):
    """Real worker lifecycle with distinct owner, executor and native causes."""

    native_cause = "RuntimeError: Visible selected software effort ceiling reached"

    def __init__(self):
        super().__init__()
        self.native_exit = threading.Event()

    def start(self, run_id, incarnation, recipe, cancel):
        ControlledHost.start(self, run_id, incarnation, recipe, cancel)
        if self.started > 1:
            self.healthy = True
            self.native_exit.clear()
            self.next_start_entered.set()
            while not self.next_start_release.wait(0.005):
                cancel()

    def poll(self, previous):
        if self.native_exit.is_set():
            raise RuntimeError("protected host exited; inspect original host log and cleanup receipt")
        return ControlledHost.poll(self, previous)

    def finish(self, status):
        if self.started == 1:
            assert self.cleanup_release.wait(2)
        outcome = terminal_cleanup_outcome(self.run_id, self.incarnation, status)
        outcome["first_cause"] = self.native_cause
        outcome["readback"]["first_cause"] = self.native_cause
        return outcome


def historical_live_feedback_fault_authority(tmp_path):
    from tests.robots.test_am1_persistent_session import start
    from tools.am1_session_core import SessionAuthority

    host = LiveFeedbackFaultThenBlockedHost()
    e = executor(host)
    authority = SessionAuthority(tmp_path, executor=e)
    first = start(authority, "physical-arm-hold-body")

    def admitted():
        authority.tick()
        return authority.run["status"] == "running"

    until(admitted)
    original_deadline = authority.run["deadline"]
    assert authority.run["native_live_at"] is not None
    host.healthy = False
    until(lambda: e.evidence()["feedback"] is False)
    authority.tick()
    assert authority.run["status"] == "recovering"
    assert authority.run["first_cause"] == "feedback_loss"
    host.native_exit.set()
    until(lambda: e.evidence()["fault"] is not None)
    authority.tick()
    assert authority.run["status"] == "finishing"
    host.cleanup_release.set()
    until(lambda: e.evidence()["lifecycle"]["phase"] == "terminal")
    e.worker.join(2)
    authority.tick()
    assert authority.run["status"] == "faulted"
    assert authority.run["uncertain"] is False and authority.run["cleanup"] == "physical_readback_verified"
    assert authority.run["first_cause"] == "feedback_loss"
    assert authority.run["native_first_cause"] == host.native_cause
    assert authority.run["deadline"] == original_deadline
    assert authority.run["native_first_cause"] != e.evidence()["fault"]
    return authority, e, host, first


def test_live_feedback_loss_and_distinct_native_fault_allow_only_fresh_next_start_after_cleanup(tmp_path):
    from tests.robots.test_am1_persistent_session import start

    authority, e, host, first = historical_live_feedback_fault_authority(tmp_path)
    try:
        original = authority.run
        saved = copy.deepcopy(original)
        resident_fault = e.evidence()["fault"]
        old_incarnation = e.host_incarnation
        old_operation = authority.db.execute(
            "SELECT result FROM operations WHERE id=?", (first["operation_id"],)
        ).fetchone()
        result = start(authority, "physical-arm-hold-body")
        assert result["accepted"] and result["status"] == "starting" and result["effect_admitted"] is False
        assert host.next_start_entered.wait(1)
        assert result["run_id"] != first["run_id"] and e.host_incarnation != old_incarnation
        assert authority.run["native_live_at"] is None and authority.run["deadline"] is None
        assert authority.run["progress_s"] == 0 and authority.run["recovery_episodes"] == 0
        assert authority.run["first_cause"] is None and e.evidence()["fault"] is None
        assert saved == original and resident_fault != original["first_cause"]
        assert (
            authority.db.execute(
                "SELECT result FROM operations WHERE id=?", (first["operation_id"],)
            ).fetchone()
            == old_operation
        )
        host.next_start_release.set()
        until(lambda: e.evidence()["lifecycle"]["phase"] == "live")
        authority.tick()
        assert authority.run["native_live_at"] is not None
        assert authority.run["backend_incarnation"] == e.host_incarnation
        assert saved == original
    finally:
        host.cleanup_release.set()
        host.next_start_release.set()
        authority.close()
        e.worker.join(2)


@pytest.mark.parametrize(
    "changed",
    [
        "missing_run_native",
        "missing_lifecycle_native",
        "missing_receipt_native",
        "run_native_mismatch",
        "lifecycle_native_mismatch",
        "receipt_native_mismatch",
        "bool_native",
        "empty_native",
        "missing_owner",
        "bool_owner",
        "empty_owner",
    ],
)
def test_distinct_cause_start_admission_refuses_missing_malformed_or_foreign_binding(tmp_path, changed):
    from tests.robots.test_am1_persistent_session import start

    authority, e, host, _ = historical_live_feedback_fault_authority(tmp_path)
    try:
        lifecycle = e._lifecycle
        if changed == "missing_run_native":
            authority.run.pop("native_first_cause")
        elif changed == "missing_lifecycle_native":
            lifecycle.pop("first_cause")
        elif changed == "missing_receipt_native":
            lifecycle["readback"].pop("first_cause")
        elif changed == "run_native_mismatch":
            authority.run["native_first_cause"] = "foreign native cause"
        elif changed == "lifecycle_native_mismatch":
            lifecycle["first_cause"] = "foreign native cause"
        elif changed == "receipt_native_mismatch":
            lifecycle["readback"]["first_cause"] = "foreign native cause"
        elif changed in {"bool_native", "empty_native"}:
            cause = False if changed == "bool_native" else ""
            authority.run["native_first_cause"] = lifecycle["first_cause"] = lifecycle["readback"][
                "first_cause"
            ] = cause
        elif changed == "missing_owner":
            authority.run.pop("first_cause")
        elif changed == "bool_owner":
            authority.run["first_cause"] = False
        else:
            authority.run["first_cause"] = ""
        authority.run["physical_result"] = copy.deepcopy(lifecycle)
        saved = copy.deepcopy(authority.run)
        result = start(authority, "physical-arm-hold-body")
        assert not result["accepted"] and not result["effect_admitted"]
        assert authority.run == saved and host.started == 1 and not host.next_start_entered.is_set()
    finally:
        host.cleanup_release.set()
        host.next_start_release.set()
        authority.close()
        e.worker.join(2)


@pytest.mark.parametrize("observed", [None, False, "", "foreign cached fault"])
def test_observed_executor_fault_must_match_resident_typed_fault_under_terminal_binding(tmp_path, observed):
    authority, e, host, _ = historical_live_feedback_fault_authority(tmp_path)
    try:
        assert e.can_start_after_fault(authority.run, observed_fault=observed) is False
    finally:
        host.cleanup_release.set()
        host.next_start_release.set()
        authority.close()
        e.worker.join(2)


def test_terminal_fault_qualifier_requires_observed_fault_keyword(tmp_path):
    authority, e, host, _ = historical_fault_authority(tmp_path)
    try:
        with pytest.raises(TypeError):
            e.can_start_after_fault(authority.run)
    finally:
        host.cleanup_release.set()
        host.next_start_release.set()
        authority.close()
        e.worker.join(2)


def test_executor_only_fault_with_explicit_consistent_native_none_still_requires_fresh_admission(tmp_path):
    from tests.robots.test_am1_persistent_session import start

    authority, e, host, _ = historical_fault_authority(tmp_path)
    try:
        e._lifecycle["first_cause"] = e._lifecycle["readback"]["first_cause"] = None
        authority.run["native_first_cause"] = None
        authority.run["physical_result"] = copy.deepcopy(e._lifecycle)
        original = copy.deepcopy(authority.run)
        result = start(authority, "physical-arm-hold-body")
        assert result["accepted"] and not result["effect_admitted"]
        assert host.next_start_entered.wait(1) and authority.run["native_live_at"] is None
        assert original["first_cause"] and original["native_first_cause"] is None
    finally:
        host.cleanup_release.set()
        host.next_start_release.set()
        authority.close()
        e.worker.join(2)


def test_right_workspace_source_refuses_configured_foreign_calibration_before_owner_io(monkeypatch):
    from examples.alohamini.am1_session_contract import RECIPES
    provider=object.__new__(module().ProtectedHostRun)
    import socket
    provider.config={"calibration_files":{"/unused/calibration.json":"a"*64},"machine":socket.gethostname(),
                     "host_repository":"/unused/motor","host_head":"0"*40,"camera_repository":"/unused/camera","camera_head":"0"*40}
    provider.recipe=RECIPES["physical-live-right-elbow"]
    import subprocess
    monkeypatch.setattr(subprocess,"check_output",lambda *a,**k: (_ for _ in ()).throw(AssertionError("No Git/source IO before workspace context refusal")))
    with pytest.raises(RuntimeError,match="workspace.*calibration"):
        provider._validate_source()



@pytest.mark.parametrize("recipe,present", [("physical-live-right-elbow", True), ("physical-live-left-elbow", True),
                                           ("physical-arm-hold-body", True), ("physical-live-right-elbow", False)])
def test_right_p20_executor_consumes_only_one_current_request_before_owned_units(tmp_path, monkeypatch, recipe, present):
    from tests.robots.test_am1_elbow_integral import right_p20_request, RIGHT_P20_RUN, RIGHT_P20_HOST
    from examples.alohamini.am1_session_contract import RECIPES
    import json
    m = module()
    state, root = tmp_path / "state", tmp_path / "runs"
    state.mkdir(mode=0o700); root.mkdir(mode=0o700)
    request = state / "response12-right-p20-request.json"
    if present:
        request.write_text(json.dumps(right_p20_request(), sort_keys=True, separators=(",", ":"))); request.chmod(0o600)
    provider = object.__new__(m.ProtectedHostRun)
    provider.config = {"physical_state_directory": str(state), "run_root": str(root), "host_head": "a" * 40,
                       "host_repository": str(tmp_path), "host_python": "unused-python", "camera_python": "unused-python",
                       "camera_repository": str(tmp_path), "camera_config": str(tmp_path / "config"),
                       "camera_credentials": str(tmp_path / "credentials"), "camera_binary": str(tmp_path / "binary"),
                       "camera_state": str(tmp_path / "camera")}
    provider.observation = SimpleNamespace()
    monkeypatch.setattr(provider, "_validate_source", lambda: None)
    monkeypatch.setattr(m, "wait_required_camera", lambda *args: None)
    owned = []
    def unit(name, *args, **kwargs):
        owned.append((name, args))
        if "host" in name: raise OSError("stop before hardware host creation")
        return SimpleNamespace()
    monkeypatch.setattr(m, "OwnedUnit", unit)
    if present and recipe != "physical-live-right-elbow":
        with pytest.raises(ValueError, match="different side/body"):
            provider.start(RIGHT_P20_RUN, RIGHT_P20_HOST, RECIPES[recipe], lambda: None)
        assert request.exists() and not owned
        return
    with pytest.raises(OSError, match="before hardware"):
        provider.start(RIGHT_P20_RUN, RIGHT_P20_HOST, RECIPES[recipe], lambda: None)
    host_command = owned[-1][1][1]
    if present:
        assert not request.exists(), "trial request must be consumed before any owned unit"
        option = host_command.index("--am1-right-p20-trial-file")
        descriptor = root / RIGHT_P20_RUN[:8] / "response12-right-p20.json"
        assert host_command[option + 1] == str(descriptor) and descriptor.exists()
    else:
        assert "--am1-right-p20-trial-file" not in host_command


@pytest.mark.parametrize("proportional", [16, 24, 32])
def test_practical_executor_consumes_fresh_private_request_before_any_owned_unit(tmp_path, monkeypatch, proportional):
    from examples.alohamini.am1_session_contract import RECIPES
    from tests.robots.test_am1_elbow_integral import RIGHT_P20_RUN, RIGHT_P20_HOST
    import json

    m = module()
    state, root = tmp_path / "state", tmp_path / "runs"
    state.mkdir(mode=0o700); root.mkdir(mode=0o700)
    request = state / "practical13-right-request.json"
    request.write_text(json.dumps(dict(version=1, kind="practical13-right", request_id="00000000-0000-4000-8000-000000000013",
                                      source_head="a" * 40, config=dict(reference_raw=1847, direction=1), proportional=proportional),
                                  sort_keys=True, separators=(",", ":")))
    request.chmod(0o600)
    provider = object.__new__(m.ProtectedHostRun)
    provider.config = {"physical_state_directory": str(state), "run_root": str(root), "host_head": "a" * 40,
                       "host_repository": str(tmp_path), "host_python": "unused-python", "camera_python": "unused-python",
                       "camera_repository": str(tmp_path), "camera_config": str(tmp_path / "config"),
                       "camera_credentials": str(tmp_path / "credentials"), "camera_binary": str(tmp_path / "binary"),
                       "camera_state": str(tmp_path / "camera")}
    provider.observation = SimpleNamespace()
    monkeypatch.setattr(provider, "_validate_source", lambda: None)
    monkeypatch.setattr(m, "wait_required_camera", lambda *args: None)
    owned = []
    descriptor = root / RIGHT_P20_RUN[:8] / "practical13-right.json"
    def unit(name, *args, **kwargs):
        assert not request.exists() and descriptor.exists(), "consume private authority before any owned process"
        owned.append((name, args))
        if "host" in name:
            raise OSError("stop before hardware host creation")
        return SimpleNamespace()
    monkeypatch.setattr(m, "OwnedUnit", unit)
    with pytest.raises(OSError, match="before hardware"):
        provider.start(RIGHT_P20_RUN, RIGHT_P20_HOST, RECIPES["physical-live-right-elbow-practical"], lambda: None)
    host_command = owned[-1][1][1]
    assert host_command[host_command.index("--am1-right-p20-trial-file") + 1] == str(descriptor)
    bound = json.loads(descriptor.read_text())
    assert bound["kind"] == "practical13-right" and bound["request"]["proportional"] == proportional
    assert bound["request"]["config"] == {"reference_raw":1847, "direction":1}
    assert bound["run_id"] == RIGHT_P20_RUN and bound["host_incarnation"] == RIGHT_P20_HOST



def test_unacknowledged_pause_revision_coalescing_preserves_expiry_and_fresh_rejoin(monkeypatch):
    """Synthetic source race, not a reconstructed missing Combined21 command.

    Reuse the actual worker/native owner, holding back its first queued pause
    until two authority revisions coalesce. The250ms input expiry stays real.
    """
    from tests.robots.test_am1_live_teleop_native import sample

    now, robot, control, e, step, stop = pending_native_worker()
    original_apply, deliver, native_errors = control.apply, [False], []
    original = copy.deepcopy(e._live_target)
    frozen = control.live_snapshot()
    def apply(owned_robot, action):
        if not deliver[0] and action["_am1_local_control"]["mode"] == "pause":
            return False  # Latest-only transport has not delivered this pause.
        try:
            return original_apply(owned_robot, action)
        except RuntimeError as error:
            native_errors.append(str(error))
            return False
    monkeypatch.setattr(control, "apply", apply)
    def feedback():
        observation = robot.get_observation()
        control.annotate(observation, acquired_at=now[0])
        return dict(e.sample, sequence=control.observation_id, at=now[0],
                    positions=robot.get_observation(), observation=observation,
                    ack=copy.deepcopy(observation["_am1_local_feedback"]))
    try:
        now[0] = original["valid_until_monotonic_s"] + 0.01
        assert control.check_live(robot)
        assert control.state == "paused" and control.epoch == 2
        assert control.live_sample is None and control.live_snapshot()["state"] == "expired"
        assert control.live_snapshot()["valid_until_monotonic_s"] == original["valid_until_monotonic_s"]
        e.hold()
        pending = step()["_am1_local_control"]
        assert (pending["mode"], pending["epoch"]) == ("pause", 3)
        e.set_intent_revision(1)
        e.set_intent_revision(2)
        deliver[0] = True
        current = step()["_am1_local_control"]
        assert native_errors == [], native_errors
        assert (current["epoch"], current["intent_revision"]) == (3, 2)
        assert control.state == "paused" and control.epoch == 3 and control.intent_revision == 2
        assert not e.evidence()["native_ack"] and e._aligned == [] and not e.dispatch(e.recipe)
        for when in (10.27, 10.38, 10.49):
            now[0] = when
            held = step(feedback())["_am1_local_control"]
            assert held["mode"] == "pause" and held["epoch"] == 3
        assert e.evidence()["hold_acknowledged"] and e.evidence()["pose_aligned"]
        assert e.dispatch(e.recipe)
        now[0] = 10.5
        active = step(feedback())["_am1_local_control"]
        assert (active["mode"], active["epoch"], active["intent_revision"]) == ("active", 4, 2)
        now[0] = 10.51
        step(feedback())
        identity = dict(run_id=e.run_id, host_incarnation=e.host_incarnation,
                        intent_revision=2, **e._live_binding)
        assert not e.apply(original["target"], body=original["body"], source=original["source"],
                           valid_until_monotonic_s=original["valid_until_monotonic_s"],
                           grant_id=original["grant_id"], seq=1, **identity)
        fresh = sample(control, seq=2, expiry=now[0] + 0.25,
                       source_id=original["source"]["source_id"], acquired=2.01)
        args = dict(target=fresh["target"], body=fresh["body"], source=fresh["source"],
                    valid_until_monotonic_s=fresh["valid_until_monotonic_s"],
                    grant_id=fresh["grant_id"], seq=2, **identity)
        assert not e.apply(**dict(args, host_incarnation="foreign-host"))
        assert e.apply(**args)
        assert not e.apply(**args)  # Original input sequence is still anti-replay.
        step()
        assert control.live_snapshot()["last_input_seq"] == 2
        assert control.live_snapshot()["valid_until_monotonic_s"] == fresh["valid_until_monotonic_s"]
        assert control.live_snapshot()["workspace"] == frozen["workspace"]
        assert control.live_snapshot()["live_deadline_monotonic_s"] == frozen["live_deadline_monotonic_s"]
        assert e.run_id == control.binding["run_id"] and e._fault is None
    finally:
        stop()


@pytest.mark.parametrize("wrong", ["run", "host", "revision", "epoch", "state", "stale"])
def test_pending_pause_ack_must_prove_exact_fresh_accepted_epoch_before_advancing(wrong):
    now, robot, control, e, step, stop = pending_native_worker()
    try:
        e.hold()
        assert step()["_am1_local_control"]["epoch"] == 3
        measured = robot.get_observation()
        control.annotate(measured, acquired_at=now[0])
        polled = dict(e.sample, sequence=control.observation_id, at=now[0], observation=measured,
                      ack=copy.deepcopy(measured["_am1_local_feedback"]))
        ack = polled["ack"]
        if wrong == "run":
            ack["run_id"] = "foreign-run"
        elif wrong == "host":
            ack["host_incarnation"] = "foreign-host"
        elif wrong == "revision":
            ack["intent_revision"] -= 1
        elif wrong == "epoch":
            ack["epoch"] -= 2
        elif wrong == "state":
            ack["state"] = "active"
        else:
            polled["at"] = now[0] - 1.0
        e.set_intent_revision(1)
        marker = step(polled)["_am1_local_control"]
        assert (marker["mode"], marker["epoch"], marker["intent_revision"]) == ("pause", 3, 1)
        assert e._aligned == [] and not e.evidence()["native_ack"] and not e.dispatch(e.recipe)
        assert control.state == "paused" and control.epoch == 3
        assert e._fault is None
    finally:
        stop()


def test_pending_pause_ack_exact_accepted_hold_preserves_existing_plus_two_rule():
    now, robot, control, e, step, stop = pending_native_worker()
    try:
        e.hold()
        assert step()["_am1_local_control"]["epoch"] == 3
        measured = robot.get_observation()
        control.annotate(measured, acquired_at=now[0])
        accepted = dict(e.sample, sequence=control.observation_id, at=now[0], observation=measured,
                        ack=copy.deepcopy(measured["_am1_local_feedback"]))
        e.set_intent_revision(1)
        marker = step(accepted)["_am1_local_control"]
        assert (marker["mode"], marker["epoch"], marker["intent_revision"]) == ("pause", 5, 1)
        assert control.state == "paused" and control.epoch == 5 and control.intent_revision == 1
        assert e._aligned == [] and not e.evidence()["native_ack"]
        assert e._fault is None
    finally:
        stop()


@pytest.mark.parametrize("recipe,present", [("physical-live-teleop", True),
    ("physical-live-teleop", False), ("physical-arm-hold-body", True)])
def test_left_response_request_is_bound_before_any_owned_unit(tmp_path, monkeypatch, recipe, present):
    from examples.alohamini.am1_session_contract import RECIPES
    from lerobot.robots.alohamini import am1_elbow_integral as gains
    from tests.robots.test_am1_elbow_integral import RIGHT_P20_HOST, RIGHT_P20_RUN
    m = module()
    state, root = tmp_path / "state", tmp_path / "runs"
    state.mkdir(mode=0o700)
    root.mkdir(mode=0o700)
    provider = object.__new__(m.ProtectedHostRun)
    provider.config = {"physical_state_directory": str(state), "run_root": str(root), "host_head": "a" * 40,
        "host_repository": str(tmp_path), "host_python": "unused-python", "camera_python": "unused-python",
        "camera_repository": str(tmp_path), "camera_config": str(tmp_path / "config"),
        "camera_credentials": str(tmp_path / "credentials"), "camera_binary": str(tmp_path / "binary"),
        "camera_state": str(tmp_path / "camera")}
    provider.observation = SimpleNamespace()
    monkeypatch.setattr(provider, "_validate_source", lambda: None)
    monkeypatch.setattr(m, "wait_required_camera", lambda *args: None)
    owned, calls = [], []
    descriptor = root / RIGHT_P20_RUN[:8] / "left-response-p.json"
    def consume(actual_state, directory, actual_recipe, run, host, source):
        assert not owned, "comparison authority must qualify before camera or motor ownership"
        assert (actual_state, directory, actual_recipe, run, host, source) == (
            state, root / RIGHT_P20_RUN[:8], recipe, RIGHT_P20_RUN, RIGHT_P20_HOST, "a" * 40)
        calls.append(actual_recipe)
        if present and actual_recipe != "physical-live-teleop":
            raise ValueError("left comparison requires the whole-arm shared path")
        if present:
            descriptor.write_text("{}")
            return descriptor
        return None
    monkeypatch.setattr(gains, "consume_left_response_p_request", consume, raising=False)
    monkeypatch.setattr(gains, "consume_right_p20_request", lambda *args: None)
    def unit(name, *args, **kwargs):
        owned.append((name, args))
        if "host" in name:
            raise OSError("stop before hardware host creation")
        return SimpleNamespace()
    monkeypatch.setattr(m, "OwnedUnit", unit)
    if present and recipe != "physical-live-teleop":
        with pytest.raises(ValueError, match="whole-arm shared path"):
            provider.start(RIGHT_P20_RUN, RIGHT_P20_HOST, RECIPES[recipe], lambda: None)
        assert not owned and calls == [recipe]
    else:
        with pytest.raises(OSError, match="before hardware"):
            provider.start(RIGHT_P20_RUN, RIGHT_P20_HOST, RECIPES[recipe], lambda: None)
        assert calls == [recipe]
        command = owned[-1][1][1]
        option = "--am1-left-response-p-trial-file"
        if present:
            assert command[command.index(option) + 1] == str(descriptor)
        else:
            assert option not in command

def test_current_selected_elbow_settings_cross_existing_ack_without_goal_payload():
    """Keep the ordinary essential ACK while admitting cached baseline evidence."""
    import hashlib
    import json

    from lerobot.robots.alohamini.am1_live_teleop import STATIC
    from tools.am1_session_core import _status_small

    m = module()
    client = client_sample()
    marker = client.latest_am1_local_feedback
    registers = dict.fromkeys(STATIC, 0)
    registers.update(ID=3, Model_Number=777, Firmware_Major_Version=3, Firmware_Minor_Version=10,
                     P_Coefficient=16, I_Coefficient=0, D_Coefficient=32, Operating_Mode=0)
    timing = {k: {"read_started_at_monotonic_s": 8., "read_completed_at_monotonic_s": 8.01}
              for k in STATIC}
    proof = {side: {"motor": "arm_" + side + "_elbow_flex", "motor_id": 3,
                    "calibration": [3, 0, offset, low, high],
                    "original": dict(registers), "active": dict(registers),
                    "original_read_timing": copy.deepcopy(timing),
                    "active_read_timing": {k: {"read_started_at_monotonic_s": 8.1,
                                               "read_completed_at_monotonic_s": 8.11} for k in STATIC}}
             for side, offset, low, high in (("left", 1845, 864, 3073), ("right", -658, 827, 3039))}
    selected = {"version": 1, "run_id": marker["run_id"], "host_incarnation": marker["host_incarnation"],
                "epoch": marker["epoch"], "intent_revision": marker["intent_revision"],
                "action_sequence": marker["accepted_action_sequence"], "observation_id": marker["observation_id"],
                "acquired_at_monotonic_s": marker["acquired_at_monotonic_s"],
                "teleop_elbow_settings": proof, "goal_readbacks": {"teleop_1": {"preserved": True}}}
    marker["selected_joint"] = selected
    marker["arm_feedback"] = {"version": 1, "positions": {
        key: {"raw": 1800, "normalized": client.get_observation()[key],
              "read_started_at_monotonic_s": 9.961, "read_completed_at_monotonic_s": 9.97}
        for key in m.TELEOP_ARM_KEYS}}
    sample = m.read_physical_sample(client, "run", "host", 4, clock=lambda: 10.)
    compact = sample["ack"]["teleop_elbow_settings"]
    assert compact["version"] == 1
    assert compact["sha256"] == hashlib.sha256(
        json.dumps(proof, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    for side in ("left", "right"):
        assert compact[side]["motor"] == proof[side]["motor"]
        assert compact[side]["motor_id"] == 3
        assert compact[side]["calibration"] == proof[side]["calibration"]
        assert compact[side]["original"] == compact[side]["active"] == [3, 777, 3, 10, 16, 0, 32, 0]
        assert compact[side]["original_read_interval"] == [8., 8.01]
        assert compact[side]["active_read_interval"] == [8.1, 8.11]
    assert "selected_joint" not in sample["ack"] and "goal_readbacks" not in sample["ack"]
    assert sample["selected_joint"] == selected
    assert sample["ack"]["acquired_at_monotonic_s"] == marker["acquired_at_monotonic_s"]
    assert _status_small({"accepted_host_action": sample["ack"]})["accepted_host_action"] == sample["ack"]
    proof["left"]["original"]["P_Coefficient"] = 32
    assert compact["left"]["original"][4] == 16


@pytest.mark.parametrize("field", ["run_id", "host_incarnation", "epoch", "intent_revision",
                                   "action_sequence", "observation_id", "acquired_at_monotonic_s"])
def test_foreign_selected_settings_cannot_cross_the_existing_ack(field):
    m = module()
    client = client_sample()
    marker = client.latest_am1_local_feedback
    selected = {"version": 1, "run_id": marker["run_id"], "host_incarnation": marker["host_incarnation"],
                "epoch": marker["epoch"], "intent_revision": marker["intent_revision"],
                "action_sequence": marker["accepted_action_sequence"], "observation_id": marker["observation_id"],
                "acquired_at_monotonic_s": marker["acquired_at_monotonic_s"],
                "teleop_elbow_settings": {"left": {}, "right": {}}}
    selected[field] = selected[field] + 1 if type(selected[field]) in (int, float) else "foreign"
    marker["selected_joint"] = selected
    with pytest.raises(ValueError, match="foreign selected"):
        m.read_physical_sample(client, "run", "host", 4, clock=lambda: 10.)


def response_trial_sample(side="right", proportional=32):
    from lerobot.robots.alohamini.am1_live_teleop import STATIC
    from tests.robots.test_am1_elbow_integral import RIGHT_P20_HOST, RIGHT_P20_RUN

    client = client_sample(run_id=RIGHT_P20_RUN, host_incarnation=RIGHT_P20_HOST)
    marker = client.latest_am1_local_feedback
    registers = dict.fromkeys(STATIC, 0)
    registers.update(ID=3, Model_Number=777, Firmware_Major_Version=3, Firmware_Minor_Version=10,
                     P_Coefficient=16, I_Coefficient=0, D_Coefficient=32, Operating_Mode=0)
    proof = {}
    for name, offset, low, high in (("left", 1845, 864, 3073), ("right", -658, 827, 3039)):
        proof[name] = {"motor": "arm_" + name + "_elbow_flex", "motor_id": 3,
                       "calibration": [3, 0, offset, low, high], "original": dict(registers),
                       "active": {**registers, "P_Coefficient": proportional if name == side else 16},
                       "original_read_timing": {key: {"read_started_at_monotonic_s": 8.,
                           "read_completed_at_monotonic_s": 8.01} for key in STATIC},
                       "active_read_timing": {key: {"read_started_at_monotonic_s": 8.1,
                           "read_completed_at_monotonic_s": 8.11} for key in STATIC}}
    proof["trial"] = {"version": 1, "kind": side + "-response-p", "run_id": RIGHT_P20_RUN,
        "host_incarnation": RIGHT_P20_HOST, "request_sha256": "a" * 64, "source_head": "b" * 40,
        "request_id": "66666666-6666-4666-8666-666666666666", "workload_segment": side + "-peer-fixed",
        "original_p": 16, "configured_p": proportional, "restore_required": True,
        "original_settings_sha256": "c" * 64, "prepared_at_monotonic_s": 8.03,
        "configured_at_monotonic_s": 8.05}
    marker["selected_joint"] = {"version": 1, "run_id": marker["run_id"],
        "host_incarnation": marker["host_incarnation"], "epoch": marker["epoch"],
        "intent_revision": marker["intent_revision"], "action_sequence": marker["accepted_action_sequence"],
        "observation_id": marker["observation_id"], "acquired_at_monotonic_s": marker["acquired_at_monotonic_s"],
        "teleop_elbow_settings": proof}
    marker["arm_feedback"] = {"version": 1, "positions": {key: {
        "raw": 1800, "normalized": client.get_observation()[key],
        "read_started_at_monotonic_s": 9.961, "read_completed_at_monotonic_s": 9.97}
        for key in module().TELEOP_ARM_KEYS}}
    return client, proof


@pytest.mark.parametrize("side", ["left", "right"])
@pytest.mark.parametrize("proportional", [16, 32])
def test_response_trial_compact_proof_fits_current_ack_without_replacing_full_evidence(side, proportional):
    import hashlib
    import json

    from tools.am1_session_core import _status_small

    m = module()
    client, proof = response_trial_sample(side, proportional)
    marker = client.latest_am1_local_feedback
    sample = m.read_physical_sample(client, marker["run_id"], marker["host_incarnation"], 4, clock=lambda: 10.)
    compact = sample["ack"].get("teleop_elbow_settings")
    assert compact is not None, "current explicit response trial proof must reach the real ACK"
    trial = proof["trial"]
    fields = ("kind", "request_sha256", "request_id", "configured_p")
    assert compact["version"] == 2
    assert compact["trial"] == [trial[key] for key in fields]
    assert compact[side][2][4] == proportional
    assert compact["left"][1] == compact["right"][1] == [3, 777, 3, 10, 16, 0, 32, 0]
    for name in ("left", "right"):
        assert compact[name][0] == proof[name]["calibration"]
        assert compact[name][3] == [8., 8.01, 8.1, 8.11]
    assert compact["sha256"] == hashlib.sha256(
        json.dumps(proof, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    assert _status_small({"accepted_host_action": sample["ack"]})["accepted_host_action"] == sample["ack"]
    assert sample["selected_joint"]["teleop_elbow_settings"] == proof
    assert sample["ack"]["acquired_at_monotonic_s"] == marker["acquired_at_monotonic_s"]
    proof["trial"]["configured_p"] = 64
    assert compact["trial"][3] == proportional


@pytest.mark.parametrize("side", ["left", "right"])
def test_response_trial_current_full_ack_with_thermal_history_survives_unchanged_cap(side):
    import json

    from tools.am1_session_core import _status_small

    client = retained_native_sample99_client()
    template, proof = response_trial_sample(side)
    marker = client.latest_am1_local_feedback
    marker.update(run_id=template.latest_am1_local_feedback["run_id"],
                  host_incarnation=template.latest_am1_local_feedback["host_incarnation"])
    marker["thermal_history"] = {"version": 1, "raw_sample_at_monotonic_s": 93298.34,
        "peaks_ready": True, "deficit_started_at_monotonic_s": None,
        "deficit_ceiling_monotonic_s": None, "hold_sample_at_monotonic_s": None,
        "zero_read_started_at_monotonic_s": None, "zero_read_completed_at_monotonic_s": None}
    marker["selected_joint"] = {"version": 1, "run_id": marker["run_id"],
        "host_incarnation": marker["host_incarnation"], "epoch": marker["epoch"],
        "intent_revision": marker["intent_revision"], "action_sequence": marker["accepted_action_sequence"],
        "observation_id": marker["observation_id"], "acquired_at_monotonic_s": marker["acquired_at_monotonic_s"],
        "teleop_elbow_settings": proof}
    sample = module().read_physical_sample(client, marker["run_id"], marker["host_incarnation"], 98,
                                          clock=lambda: 93298.4)
    ack = sample["ack"]
    assert ack["teleop_elbow_settings"]["version"] == 2
    assert set(ack) == (set(marker) - {"selected_joint"}) | {"teleop_elbow_settings"}
    assert ack["arm_feedback"] == marker["arm_feedback"]
    assert ack["lift_feedback"] == marker["lift_feedback"]
    assert ack["thermal_history"] == marker["thermal_history"]
    assert 3500 < len(json.dumps(ack, sort_keys=True, separators=(",", ":")).encode()) <= 4096
    assert _status_small({"accepted_host_action": ack})["accepted_host_action"] == ack
    assert sample["selected_joint"]["teleop_elbow_settings"] == proof
    # Exercise the actual PC consumer on the same full ACK, not a second hand-built wire schema.
    from tests.robots.test_am1_leader_adapter import peer_baseline_state, peer_workspace
    from tools.am1_leader_adapter import SelectedLeftWorkload

    workspace = peer_workspace()
    state = peer_baseline_state(workspace)
    state["run"].update(run_id=marker["run_id"], backend_incarnation=marker["host_incarnation"],
                        intent_revision=marker["intent_revision"])
    state["evidence"].update(host_incarnation=marker["host_incarnation"],
                            acknowledged_intent_revision=marker["intent_revision"], accepted_host_action=ack)
    task = SelectedLeftWorkload(workspace, segment=side + "-peer-fixed", peer_side=side,
                               peer_direction=-1 if side == "left" else 1, peer_raw_step=52, peer_active_p=32)
    assert task.observe_peer_baseline(state)
    ordinary = module()._teleop_elbow_settings_summary({name: proof[name] for name in ("left", "right")},
                                                      marker["acquired_at_monotonic_s"])
    state["evidence"]["accepted_host_action"] = {**ack, "teleop_elbow_settings": ordinary}
    unbound = SelectedLeftWorkload(workspace, segment=side + "-peer-fixed", peer_side=side,
                                  peer_direction=-1 if side == "left" else 1, peer_raw_step=52, peer_active_p=32)
    with pytest.raises(RuntimeError, match="baseline"):
        unbound.observe_peer_baseline(state)
    # The existing cap still omits proof if a later ACK is larger; never discard its feedback to fit.
    marker["retained_extra"] = "x" * 4096
    larger = module().read_physical_sample(client, marker["run_id"], marker["host_incarnation"], 98,
                                          clock=lambda: 93298.4)
    assert "teleop_elbow_settings" not in larger["ack"]
    assert larger["ack"]["arm_feedback"] == marker["arm_feedback"]
    assert larger["selected_joint"]["teleop_elbow_settings"] == proof


@pytest.mark.parametrize("bad", ["missing_owner", "run", "host", "version_bool", "restore", "kind", "segment",
    "gain", "original", "hash", "source", "uuid", "nonfinite", "reversed", "after_active", "extra",
    "peer_gain", "original_gain", "firmware", "calibration"])
def test_response_trial_compact_proof_refuses_foreign_or_modified_original_without_requalification(bad):
    from tests.robots.test_am1_elbow_integral import RIGHT_P20_HOST, RIGHT_P20_RUN

    m = module()
    _client, proof = response_trial_sample()
    changes = {"run": ("run_id", "foreign"), "host": ("host_incarnation", "foreign"),
        "version_bool": ("version", True), "restore": ("restore_required", False),
        "kind": ("kind", "left-response-p"), "segment": ("workload_segment", "full"),
        "gain": ("configured_p", 64), "original": ("original_p", 32), "hash": ("request_sha256", "f" * 63),
        "source": ("source_head", "f" * 39), "uuid": ("request_id", "foreign"),
        "nonfinite": ("prepared_at_monotonic_s", float("nan")),
        "reversed": ("prepared_at_monotonic_s", 8.06),
        "after_active": ("configured_at_monotonic_s", 8.12), "extra": ("arbitrary", 1)}
    if bad in changes:
        key, value = changes[bad]
        proof["trial"][key] = value
    elif bad == "peer_gain":
        proof["left"]["active"]["P_Coefficient"] = 32
    elif bad == "original_gain":
        proof["right"]["original"]["P_Coefficient"] = 32
    elif bad == "firmware":
        proof["right"]["active"]["Firmware_Minor_Version"] = 11
    elif bad == "calibration":
        proof["right"]["calibration"] = proof["left"]["calibration"]
    kwargs = {} if bad == "missing_owner" else {"run_id": RIGHT_P20_RUN, "host_incarnation": RIGHT_P20_HOST}
    assert m._teleop_elbow_settings_summary(proof, 9.98, **kwargs) is None


@pytest.mark.parametrize("present", [True, False])
def test_right_response_request_reaches_only_its_exact_owned_host_argument(tmp_path, monkeypatch, present):
    from examples.alohamini.am1_session_contract import RECIPES
    from lerobot.robots.alohamini import am1_elbow_integral as gains
    from tests.robots.test_am1_elbow_integral import RIGHT_P20_HOST, RIGHT_P20_RUN

    m = module()
    state, root = tmp_path / "state", tmp_path / "runs"
    state.mkdir(mode=0o700)
    root.mkdir(mode=0o700)
    provider = object.__new__(m.ProtectedHostRun)
    provider.config = {"physical_state_directory": str(state), "run_root": str(root), "host_head": "a" * 40,
        "host_repository": str(tmp_path), "host_python": "unused-python", "camera_python": "unused-python",
        "camera_repository": str(tmp_path), "camera_config": str(tmp_path / "config"),
        "camera_credentials": str(tmp_path / "credentials"), "camera_binary": str(tmp_path / "binary"),
        "camera_state": str(tmp_path / "camera")}
    provider.observation = SimpleNamespace()
    monkeypatch.setattr(provider, "_validate_source", lambda: None)
    monkeypatch.setattr(m, "wait_required_camera", lambda *args: None)
    monkeypatch.setattr(gains, "consume_left_response_p_request", lambda *args: None)
    monkeypatch.setattr(gains, "consume_right_p20_request", lambda *args: None)
    owned, calls = [], []
    descriptor = root / RIGHT_P20_RUN[:8] / "right-response-p.json"
    def consume(actual_state, directory, recipe, run, host, source):
        assert not owned
        assert (actual_state, directory, recipe, run, host, source) == (
            state, root / RIGHT_P20_RUN[:8], "physical-live-teleop", RIGHT_P20_RUN, RIGHT_P20_HOST, "a" * 40)
        calls.append(source)
        return descriptor if present else None
    monkeypatch.setattr(gains, "consume_right_response_p_request", consume)
    def unit(name, *args, **kwargs):
        owned.append((name, args))
        if "host" in name:
            raise OSError("stop before hardware host creation")
        return SimpleNamespace()
    monkeypatch.setattr(m, "OwnedUnit", unit)
    with pytest.raises(OSError, match="before hardware"):
        provider.start(RIGHT_P20_RUN, RIGHT_P20_HOST, RECIPES["physical-live-teleop"], lambda: None)
    assert calls == ["a" * 40]
    command = owned[-1][1][1]
    option = "--am1-right-response-p-trial-file"
    assert (option in command) is present
    if present:
        assert command[command.index(option) + 1] == str(descriptor)
    assert "--am1-left-response-p-trial-file" not in command


@pytest.mark.parametrize("broken", [False, True])
def test_dual_response_requests_refuse_before_either_nonce_or_owned_unit(tmp_path, monkeypatch, broken):
    from pathlib import Path

    from examples.alohamini.am1_session_contract import RECIPES
    from lerobot.robots.alohamini import am1_elbow_integral as gains
    from tests.robots.test_am1_elbow_integral import RIGHT_P20_HOST, RIGHT_P20_RUN

    m = module()
    state, root = tmp_path / "state", tmp_path / "runs"
    state.mkdir(mode=0o700)
    left, right = state / "left-response-p-request.json", state / "right-response-p-request.json"
    left.write_bytes(b"left original request")
    if broken:
        original = Path.is_symlink
        monkeypatch.setattr(Path, "is_symlink", lambda path: path == right or original(path))
    else:
        right.write_bytes(b"right original request")
    provider = object.__new__(m.ProtectedHostRun)
    provider.config = {"physical_state_directory": str(state), "run_root": str(root), "host_head": "a" * 40}
    monkeypatch.setattr(provider, "_validate_source", lambda: None)
    def forbidden(*args, **kwargs):
        pytest.fail("dual requests must refuse before nonce consumption or ownership")
    monkeypatch.setattr(gains, "consume_left_response_p_request", forbidden)
    monkeypatch.setattr(gains, "consume_right_response_p_request", forbidden)
    monkeypatch.setattr(m, "OwnedUnit", forbidden)
    with pytest.raises(ValueError, match="one selected"):
        provider.start(RIGHT_P20_RUN, RIGHT_P20_HOST, RECIPES["physical-live-teleop"], lambda: None)
    assert left.read_bytes() == b"left original request"
    if not broken:
        assert right.read_bytes() == b"right original request"
    assert not list(state.glob("*-used-*"))


def initial_empty_real_client(tmp_path, monkeypatch, clock):
    from lerobot.robots.alohamini import alohamini_client as client_module
    from lerobot.robots.alohamini.config_alohamini import AlohaMiniClientConfig

    monkeypatch.setattr(client_module, "time", SimpleNamespace(monotonic=lambda: clock[0], perf_counter=time.perf_counter))
    client = client_module.AlohaMiniClient(AlohaMiniClientConfig(
        remote_ip="127.0.0.1", robot_model="alohamini1", cameras={}, calibration_dir=tmp_path))
    client._is_connected = True  # Existing handshake establishes availability without populating decoded cache.
    monkeypatch.setattr(client, "_poll_and_get_latest_message", lambda: None)
    return client


def test_initial_empty_real_client_waits_without_inventing_feedback_then_accepts_first_packet(tmp_path, monkeypatch):
    import json

    m = module()
    clock = [10.]
    client = initial_empty_real_client(tmp_path, monkeypatch, clock)
    for _ in range(2):
        with pytest.raises(m.FeedbackUnavailable, match="initial"):
            m.read_physical_sample(client, "run", "host", -1, clock=lambda: clock[0], allow_pending_sample=True)
        assert client.observation_sequence == 0 and client.latest_am1_local_feedback is None
        assert client.latest_observation_received_at is None and client.latest_raw_observation_keys == frozenset()
        clock[0] += .04
    original = client_sample(now=clock[0])
    payload = dict(original.get_observation(), _am1_local_feedback=original.latest_am1_local_feedback)
    client._last_response_request_sent_at = clock[0] - .03
    monkeypatch.setattr(client, "_poll_and_get_latest_message", lambda: [json.dumps(payload).encode()])
    actual = m.read_physical_sample(client, "run", "host", -1, clock=lambda: clock[0], allow_pending_sample=True)
    assert client.observation_sequence == 1 and actual["sequence"] == 5
    assert actual["ack"] == original.latest_am1_local_feedback
    assert actual["at"] == pytest.approx(clock[0]-.04)


@pytest.mark.parametrize("bad", ["not_pending", "later_read", "previous_float", "sequence", "sequence_bool",
                                 "missing_sequence", "received", "missing_received", "request_age", "raw_keys",
                                 "cached_state", "missing_raw_keys", "received_without_marker", "parse", "marker_version", "foreign", "process_long"])
def test_initial_empty_grace_never_accepts_received_or_ambiguous_feedback(tmp_path, monkeypatch, bad):
    import json

    m = module()
    clock = [10.]
    client = initial_empty_real_client(tmp_path, monkeypatch, clock)
    previous, pending = -1, True
    if bad == "not_pending":
        pending = False
    elif bad == "later_read":
        previous = 0
    elif bad == "previous_float":
        previous = -1.
    elif bad == "sequence":
        client._observation_sequence = 1
    elif bad == "sequence_bool":
        client._observation_sequence = False
    elif bad == "missing_sequence":
        del client._observation_sequence
    elif bad == "received":
        client._latest_observation_received_at = 10.
    elif bad == "missing_received":
        del client._latest_observation_received_at
    elif bad == "request_age":
        client._latest_observation_roundtrip_age_s = 0.
    elif bad == "raw_keys":
        client._latest_raw_observation_keys = frozenset(JOINT_KEYS)
    elif bad == "missing_raw_keys":
        del client._latest_raw_observation_keys
    elif bad == "cached_state":
        client.last_remote_state = {"arm_left_elbow_flex.pos": 0.}
    elif bad == "parse":
        monkeypatch.setattr(client, "_poll_and_get_latest_message", lambda: [b"{malformed"])
    else:
        payload = dict.fromkeys(JOINT_KEYS, 7.)
        if bad == "marker_version":
            payload["_am1_local_feedback"] = {"version": 0}
        elif bad == "foreign":
            payload["_am1_local_feedback"] = client_sample(run_id="foreign").latest_am1_local_feedback
        client._last_response_request_sent_at = 9.99
        monkeypatch.setattr(client, "_poll_and_get_latest_message", lambda: [json.dumps(payload).encode()])
        if bad == "process_long":
            def fail_processing(*args):
                raise RuntimeError("x" * 500)
            monkeypatch.setattr(client, "_remote_state_from_obs", fail_processing)
    with pytest.raises(ValueError) as caught:
        m.read_physical_sample(client, "run", "host", previous, clock=lambda: clock[0], allow_pending_sample=pending)
    if bad != "foreign":
        message = str(caught.value)
        assert message.startswith("protected host feedback marker missing; context=")
        context = json.loads(message.split("; context=", 1)[1])
        assert context["marker_type"] == ("dict" if bad == "marker_version" else "NoneType")
        assert set(context) == {"observation_sequence", "received_at", "request_age_s", "parse_error",
                               "raw_key_count", "marker_key_present", "returned_key_count", "marker_type", "marker_version"}
        if bad == "received_without_marker":
            assert context["observation_sequence"] == 1 and context["received_at"] == 10.
            assert context["raw_key_count"] == len(JOINT_KEYS) and context["marker_key_present"] is False
            assert context["returned_key_count"] > 0 and context["parse_error"] is None
        elif bad == "parse":
            assert context["observation_sequence"] == 0 and context["received_at"] is None
            assert context["parse_error"] == "remote observation message could not be parsed"
            assert context["raw_key_count"] == context["returned_key_count"] == 0
        elif bad == "missing_sequence":
            assert context["observation_sequence"]["available"] is False
        elif bad == "process_long":
            assert len(context["parse_error"]) == 160 and context["parse_error"].startswith("RuntimeError: ")
            assert len(message) < 1500


@pytest.mark.parametrize("outcome", ["first_packet", "original_deadline", "stop"])
def test_initial_empty_real_client_worker_keeps_startup_hold_deadline_and_cancellation(tmp_path, monkeypatch, outcome):
    import json

    from examples.alohamini.am1_session_contract import RECIPES

    m = module()
    clock = [10.]
    client = initial_empty_real_client(tmp_path, monkeypatch, clock)

    class InitialHost(ControlledHost):
        valid = False
        admitted_at = None

        def send(self, action):
            super().send(action)
            if action["_am1_local_control"].get("live_admitted"):
                if self.admitted_at is None:
                    self.admitted_at = clock[0]
                self.marker["live_input"] = {"live_admitted_at_monotonic_s": self.admitted_at,
                                             "live_deadline_monotonic_s": self.admitted_at + 300.}

        def poll(self, previous):
            if self.valid:
                clock[0] += .05
                self.sequence += 1
                stamp = clock[0]
                marker = dict(self.marker, version=1, observation_id=self.sequence,
                              run_id=self.run_id, host_incarnation=self.incarnation,
                              read_started_at_monotonic_s=stamp-.01, acquired_at_monotonic_s=stamp)
                payload = dict(dict.fromkeys(JOINT_KEYS, 7.), _am1_local_feedback=marker)
                client._last_response_request_sent_at = stamp-.02
                monkeypatch.setattr(client, "_poll_and_get_latest_message", lambda: [json.dumps(payload).encode()])
            return m.read_physical_sample(client, self.run_id, self.incarnation, previous,
                                          clock=lambda: clock[0], allow_pending_sample=True)

    host = InitialHost()
    e = executor(host)
    e.clock = lambda: clock[0]
    recipe = RECIPES["physical-live-teleop"]
    e.bind_run("run", recipe)
    original_deadline = e.evidence()["lifecycle"]["startup_deadline"]
    assert e.begin(recipe)
    try:
        until(lambda: bool(host.actions) or not e.worker.is_alive())
        assert e.worker.is_alive() and e.evidence()["fault"] is None
        assert e.evidence()["lifecycle"]["native_live_at"] is None
        assert not e.evidence()["feedback"] and not e.evidence()["pose_aligned"]
        assert all(set(action) == set(m.ZERO_BODY) | {"_am1_local_control"} for action in host.actions)
        assert all(action["_am1_local_control"]["mode"] == "pause" for action in host.actions)
        assert all(all(action[key] == 0 for key in m.ZERO_BODY) for action in host.actions)
        if outcome == "first_packet":
            host.valid = True
            e._wake.set()
            until(lambda: e.evidence()["lifecycle"]["phase"] == "live")
            assert e.evidence()["hold_acknowledged"] and e.evidence()["pose_aligned"]
            assert e.evidence()["feedback"] and client.observation_sequence > 0
            assert e.evidence()["fault"] is None
        elif outcome == "original_deadline":
            clock[0] = original_deadline
            e._wake.set()
            until(lambda: not e.worker.is_alive())
            assert e.evidence()["lifecycle"]["native_live_at"] is None
            assert e.evidence()["fault"] == "RuntimeError: physical startup deadline expired"
        else:
            e.finish("stopped")
            e.worker.join(2)
            assert not e.worker.is_alive() and e.evidence()["fault"] is None
            assert e.evidence()["lifecycle"]["native_live_at"] is None
        assert e.evidence()["lifecycle"]["startup_deadline"] == original_deadline
    finally:
        e.close()
        e.worker.join(2)
        assert not e.worker.is_alive() and host.finished == 1


def request_preparation_config(tmp_path, monkeypatch):
    import hashlib
    import json
    import socket
    import subprocess
    import uuid
    from pathlib import Path

    from lerobot.robots.alohamini import am1_elbow_integral as gains
    from tools import am1_physical_reconciliation as reconciliation

    state = tmp_path / ".local/state/am1-session"
    state.mkdir(parents=True, mode=0o700)
    calibration = tmp_path / "calibration.json"
    calibration.write_bytes(b"original calibration")
    config = {key: str(tmp_path / key) for key in (
        "host_repository", "host_python", "camera_repository", "camera_python", "camera_config",
        "camera_credentials", "camera_binary", "camera_state", "run_root", "source_pins")}
    config.update(version=1, machine="AM1-test", host_head="927c17aa52bdf8da3e0ab53d7a122bbb3749b816",
                  camera_head="b" * 40, physical_state_directory=str(state), shoulder_amplitude=1.5,
                  calibration_files={str(calibration): hashlib.sha256(calibration.read_bytes()).hexdigest()})
    path = tmp_path / "physical.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    path.chmod(0o600)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(socket, "gethostname", lambda: config["machine"])
    def git(args, **kwargs):
        assert args[:2] == ["git", "-C"]
        side = "host" if args[2] == config["host_repository"] else "camera"
        return config[side + "_head"] + "\n" if args[3:] == ["rev-parse", "HEAD"] else ""
    monkeypatch.setattr(subprocess, "check_output", git)
    boot = "77777777-7777-4777-8777-777777777777"
    monkeypatch.setattr(gains, "_left_response_p_boot_id", lambda: boot)
    guard = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(reconciliation, "acquire_stopped_guard", lambda value: guard)
    monkeypatch.setattr(uuid, "uuid4", lambda: uuid.UUID("66666666-6666-4666-8666-666666666666"))
    return path, config, gains, calibration


def test_left_response_preparation_canonical_once_only_and_no_hardware(tmp_path, monkeypatch):
    import hashlib
    from pathlib import Path

    from tests.robots.test_am1_elbow_integral import RIGHT_P20_HOST, RIGHT_P20_RUN

    m = module()
    assert callable(getattr(m, "prepare_left_response_p_request", None)), "canonical preparation missing"
    path, config, gains, _calibration = request_preparation_config(tmp_path, monkeypatch)
    monkeypatch.setattr(m.ProtectedHostRun, "start", lambda *args, **kwargs: pytest.fail("no Start"))
    monkeypatch.setattr(m, "OwnedUnit", lambda *args, **kwargs: pytest.fail("no owned device process"))
    started = gains.time.monotonic()
    result = m.prepare_left_response_p_request(str(path), ttl_seconds=120)
    pending = Path(result["path"])
    request, raw, info = gains._right_p20_file(pending)
    assert result["hardware_started"] is False and result["canonical_read_verified"] is True
    assert result["request"] == request
    assert request["kind"] == "left-response-p" and request["proportional"] == 32
    assert request["workload_segment"] == "left-reach" and request["source_head"] == config["host_head"]
    assert request["boot_id"] == gains._left_response_p_boot_id()
    assert started < request["expires_at_monotonic_s"] <= gains.time.monotonic() + 120
    assert raw == gains._right_p20_json(request) and not raw.endswith(b"\n")
    assert result["sha256"] == hashlib.sha256(raw).hexdigest() and info.st_nlink == 1
    directory = tmp_path / RIGHT_P20_RUN[:8]
    directory.mkdir(mode=0o700)
    descriptor = gains.consume_left_response_p_request(
        pending.parent, directory, "physical-live-teleop", RIGHT_P20_RUN, RIGHT_P20_HOST, config["host_head"])
    assert descriptor.exists() and not pending.exists()
    assert gains.load_left_response_p_binding(descriptor, directory, RIGHT_P20_RUN, RIGHT_P20_HOST)["request"] == request
    with pytest.raises(ValueError, match="retired"):
        m.prepare_left_response_p_request(str(path))
    assert not pending.exists()


@pytest.mark.parametrize("obstruction", ["left-response-p-request.json", "right-response-p-request.json",
    "response12-right-p20-request.json", "cleanup-uncertain.json", "physical-in-progress.json",
    "calibration", "source", "boot_changed", "expired"])
def test_left_response_preparation_refuses_without_overwrite(tmp_path, monkeypatch, obstruction):
    from pathlib import Path

    m = module()
    assert callable(getattr(m, "prepare_left_response_p_request", None)), "canonical preparation missing"
    path, config, gains, calibration = request_preparation_config(tmp_path, monkeypatch)
    state = Path(config["physical_state_directory"])
    original = b"retained original"
    if obstruction.endswith(".json"):
        (state / obstruction).write_bytes(original)
    elif obstruction == "calibration":
        calibration.write_bytes(b"changed calibration")
    elif obstruction == "source":
        import subprocess

        monkeypatch.setattr(subprocess, "check_output", lambda *args, **kwargs: "wrong head")
    elif obstruction == "boot_changed":
        values = iter([gains._left_response_p_boot_id(), "88888888-8888-4888-8888-888888888888"])
        monkeypatch.setattr(gains, "_left_response_p_boot_id", lambda: next(values))
    else:
        values = iter([100., 221.])
        monkeypatch.setattr(gains.time, "monotonic", lambda: next(values))
    with pytest.raises((ValueError, RuntimeError, FileExistsError)):
        m.prepare_left_response_p_request(str(path))
    if obstruction.endswith(".json"):
        assert (state / obstruction).read_bytes() == original
    assert not (state / "left-response-p-request.json").exists() or obstruction == "left-response-p-request.json"
    assert not list(state.glob("*-used-*")) and not list(state.glob(".*.tmp"))


@pytest.mark.parametrize("ttl", [0, 121, True, float("nan"), float("inf")])
def test_left_response_preparation_ttl_is_closed(tmp_path, monkeypatch, ttl):
    m = module()
    assert callable(getattr(m, "prepare_left_response_p_request", None)), "canonical preparation missing"
    path, config, _gains, _calibration = request_preparation_config(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="120"):
        m.prepare_left_response_p_request(str(path), ttl_seconds=ttl)
    from pathlib import Path

    assert not list(Path(config["physical_state_directory"]).iterdir())


def test_left_response_preparation_cli_uses_only_fixed_reviewed_ssh_payload(monkeypatch, capsys):
    import json
    import shlex
    import subprocess

    m = module()
    assert callable(getattr(m, "main", None)), "preparation CLI missing"
    config = "/home/pickmanmike/.local/share/am1-physical-adapter-01/deployments/c5adf5f6c29e909a/physical.json"
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps({"prepared": True}), stderr="")
    monkeypatch.setattr(subprocess, "run", run)
    assert m.main(["--prepare-left-response-p", "--ssh", "am1-pi", "--config", config]) == 0
    args, options = calls.pop()
    assert args[:7] == ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "am1-pi"]
    assert shlex.split(args[7]) == ["/home/pickmanmike/lerobot_alohamini/.venv/bin/python", "-B", "-", config, "120"]
    assert options["shell"] is False and options["timeout"] == 30 and options["text"] is True
    assert options.get("encoding") == "utf-8", "Windows stdin must preserve the native Python source bytes"
    assert "def prepare_left_response_p_request(" in options["input"]
    assert "/home/pickmanmike/am1_physical_trial" in options["input"]
    assert "consume_left_response_p_request" not in options["input"]
    assert json.loads(capsys.readouterr().out) == {"prepared": True}
    for changed in (["--ssh", "am1-pi;echo"], ["--config", config + "';echo bad"],
                    ["--config", "../../etc/passwd"], ["--ttl-seconds", "121"]):
        with pytest.raises((SystemExit, ValueError)):
            m.main(["--prepare-left-response-p", "--ssh", "am1-pi", "--config", config, *changed])
        assert not calls


@pytest.mark.parametrize("boundary", ["publication_race", "published_readback", "active_owner", "symlink",
                                    "legacy_symlink"])
def test_left_response_preparation_atomic_publication_and_guard_release(tmp_path, monkeypatch, boundary):
    from pathlib import Path

    from tools import am1_physical_reconciliation as reconciliation

    m = module()
    path, config, gains, _calibration = request_preparation_config(tmp_path, monkeypatch)
    state = Path(config["physical_state_directory"])
    pending = state / "left-response-p-request.json"
    closed = []
    def guard(value):
        if boundary == "active_owner":
            raise RuntimeError("Another physical owner is active")
        return SimpleNamespace(close=lambda: closed.append(True))
    monkeypatch.setattr(reconciliation, "acquire_stopped_guard", guard)
    if boundary == "publication_race":
        publish = reconciliation._publish_no_replace
        def race(temporary, target):
            assert not target.exists()
            assert gains._right_p20_file(temporary)[0]["proportional"] == 32
            target.write_bytes(b"other preserved request")
            publish(temporary, target)
        monkeypatch.setattr(reconciliation, "_publish_no_replace", race)
    elif boundary == "published_readback":
        read = gains._right_p20_file
        def failed_read(target):
            if target == pending:
                raise ValueError("Published receipt unavailable")
            return read(target)
        monkeypatch.setattr(gains, "_right_p20_file", failed_read)
    elif boundary in ("symlink", "legacy_symlink"):
        redirected = pending if boundary == "symlink" else state / "response12-right-p20-request.json"
        is_symlink = Path.is_symlink
        monkeypatch.setattr(Path, "is_symlink", lambda target: target == redirected or is_symlink(target))
    with pytest.raises((ValueError, RuntimeError, FileExistsError)):
        m.prepare_left_response_p_request(str(path))
    assert closed == ([] if boundary == "active_owner" else [True])
    assert not list(state.glob("*.tmp")) and not list(state.glob("*-used-*"))
    if boundary == "publication_race":
        assert pending.read_bytes() == b"other preserved request"
    elif boundary == "published_readback":
        # Ambiguous preparation preserves the once-only request; never delete/retry it.
        assert gains._right_p20_json(__import__("json").loads(pending.read_bytes())) == pending.read_bytes()
    else:
        assert not pending.exists()
        if boundary == "legacy_symlink":
            assert redirected.is_symlink() and not redirected.exists()


def test_left_response_preparation_relay_executes_without_new_installed_helper(tmp_path, monkeypatch, capsys):
    import io
    import json
    import subprocess
    import sys
    from contextlib import redirect_stdout

    m = module()
    path, _config, _gains, _calibration = request_preparation_config(tmp_path, monkeypatch)
    native_config = "/home/pickmanmike/.local/share/am1-physical-adapter-01/deployments/c5adf5f6c29e909a/physical.json"
    def remote(args, **options):
        output = io.StringIO()
        with monkeypatch.context() as context, redirect_stdout(output):
            # Map only the native fixture pathname for this Windows test; the actual
            # relay argv/path is checked separately. Installed runtime lacks the CLI.
            context.setattr(sys, "argv", ["-", str(path), "120"])
            context.delattr(m, "prepare_left_response_p_request")
            exec(compile(options["input"], "<fixed-preparation-relay>", "exec"), {})
        return SimpleNamespace(stdout=output.getvalue(), stderr="", returncode=0)
    monkeypatch.setattr(subprocess, "run", remote)
    assert m.main(["--prepare-left-response-p", "--ssh", "am1-pi", "--config", native_config]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["canonical_read_verified"] and receipt["hardware_started"] is False
    assert receipt["request"]["proportional"] == 32 and receipt["request"]["workload_segment"] == "left-reach"


def test_left_response_preparation_unknown_ssh_receipt_is_not_replayed(monkeypatch):
    import subprocess

    m = module()
    calls = []
    def timeout(args, **options):
        calls.append(args)
        raise subprocess.TimeoutExpired(args, options["timeout"])
    monkeypatch.setattr(subprocess, "run", timeout)
    config = "/home/pickmanmike/.local/share/am1-physical-adapter-01/deployments/c5adf5f6c29e909a/physical.json"
    with pytest.raises(subprocess.TimeoutExpired):
        m.main(["--prepare-left-response-p", "--ssh", "am1-pi", "--config", config])
    assert len(calls) == 1


@pytest.mark.parametrize("supported", [True, False])
def test_left_response_preparation_current_rest_requires_installed_profile_validator(tmp_path, monkeypatch, supported):
    import hashlib
    from pathlib import Path

    m = module()
    path, config, gains, _calibration = request_preparation_config(tmp_path, monkeypatch)
    state = Path(config["physical_state_directory"])
    native_validate = gains._left_response_p_request
    validated = []

    def validate(request, source_head):
        assert not list(state.iterdir()), "installed validation must precede any publication"
        assert request["workspace_profile"] == "left-current-rest-1051-1164"
        assert request["source_head"] == source_head == config["host_head"]
        validated.append(dict(request))
        if not supported:
            raise ValueError("Installed source does not support this workspace profile")
        return native_validate({key: value for key, value in request.items() if key != "workspace_profile"}, source_head)

    monkeypatch.setattr(gains, "_left_response_p_request", validate)
    monkeypatch.setattr(m.ProtectedHostRun, "start", lambda *args, **kwargs: pytest.fail("no Start"))
    monkeypatch.setattr(m, "OwnedUnit", lambda *args, **kwargs: pytest.fail("no device process"))
    if not supported:
        with pytest.raises(ValueError, match="Installed source"):
            m.prepare_left_response_p_request(str(path), workspace_profile="left-current-rest-1051-1164")
        assert len(validated) == 1 and not list(state.iterdir())
        return
    result = m.prepare_left_response_p_request(str(path), workspace_profile="left-current-rest-1051-1164")
    request, raw, _info = gains._right_p20_file(Path(result["path"]))
    assert request == validated[0] == result["request"]
    assert set(request) == {"version", "kind", "request_id", "source_head", "proportional", "workload_segment",
                            "boot_id", "expires_at_monotonic_s", "workspace_profile"}
    assert request["proportional"] == 32 and request["workload_segment"] == "left-reach"
    assert raw == gains._right_p20_json(request) and not raw.endswith(b"\n")
    assert result["sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["canonical_read_verified"] and result["hardware_started"] is False
    assert not list(state.glob("*-used-*")) and not list(state.glob(".*.tmp"))


@pytest.mark.parametrize("profile", [True, False, "", "current-rest", "left-current-rest-1051-1165", {"min": 1051}])
def test_left_response_preparation_current_rest_profile_is_closed(tmp_path, monkeypatch, profile):
    from pathlib import Path

    m = module()
    path, config, _gains, _calibration = request_preparation_config(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="workspace profile"):
        m.prepare_left_response_p_request(str(path), workspace_profile=profile)
    assert not list(Path(config["physical_state_directory"]).iterdir())


def test_left_response_preparation_current_rest_cli_relays_explicit_profile_only(tmp_path, monkeypatch, capsys):
    import io
    import json
    import shlex
    import subprocess
    import sys
    from contextlib import redirect_stdout

    m = module()
    path, config, gains, _calibration = request_preparation_config(tmp_path, monkeypatch)
    native_config = "/home/pickmanmike/.local/share/am1-physical-adapter-01/deployments/7438c74662d59a02/physical.json"
    native_validate = gains._left_response_p_request
    seen = []

    def validate(request, source_head):
        seen.append(dict(request))
        return native_validate({key: value for key, value in request.items() if key != "workspace_profile"}, source_head)

    monkeypatch.setattr(gains, "_left_response_p_request", validate)

    def remote(args, **options):
        assert shlex.split(args[7]) == ["/home/pickmanmike/lerobot_alohamini/.venv/bin/python", "-B", "-",
                                       native_config, "120", "left-current-rest-1051-1164"]
        assert options["shell"] is False and options["encoding"] == "utf-8"
        output = io.StringIO()
        with monkeypatch.context() as context, redirect_stdout(output):
            context.setattr(sys, "argv", ["-", str(path), "120", "left-current-rest-1051-1164"])
            context.delattr(m, "prepare_left_response_p_request")
            exec(compile(options["input"], "<fixed-preparation-relay>", "exec"), {})
        return SimpleNamespace(stdout=output.getvalue(), stderr="", returncode=0)

    monkeypatch.setattr(subprocess, "run", remote)
    command = ["--prepare-left-response-p", "--ssh", "am1-pi", "--config", native_config,
               "--left-response-workspace", "current-rest"]
    assert m.main(command) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["request"] == seen[0]
    assert receipt["request"]["workspace_profile"] == "left-current-rest-1051-1164"
    assert receipt["request"]["source_head"] == config["host_head"]
    assert receipt["canonical_read_verified"] and receipt["hardware_started"] is False


def test_left_response_preparation_current_rest_cli_rejects_free_bounds_before_ssh(monkeypatch):
    import subprocess

    m = module()
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("no SSH on unsupported profile"))
    with pytest.raises(SystemExit):
        m.main(["--prepare-left-response-p", "--ssh", "am1-pi", "--config",
                "/home/pickmanmike/.local/share/am1-physical-adapter-01/deployments/7438c74662d59a02/physical.json",
                "--left-response-workspace", "1051..1165"])


@pytest.mark.parametrize("supported", [True, False])
def test_left_shoulder_startup_preparation_validates_installed_support_before_publication(tmp_path, monkeypatch, supported):
    from pathlib import Path
    m = module()
    path, config, gains, _ = request_preparation_config(tmp_path, monkeypatch)
    state = Path(config["physical_state_directory"])
    native_validate = gains._left_response_p_request
    seen = []
    def validate(request, source_head):
        assert not list(state.iterdir())
        seen.append(dict(request))
        if not supported:
            raise ValueError("Installed startup allowance unavailable")
        return native_validate(request, source_head)
    monkeypatch.setattr(gains, "_left_response_p_request", validate)
    monkeypatch.setattr(m.ProtectedHostRun, "start", lambda *a, **k: pytest.fail("no Start"))
    monkeypatch.setattr(m, "OwnedUnit", lambda *a, **k: pytest.fail("no device process"))
    if not supported:
        with pytest.raises(ValueError, match="Installed startup"):
            m.prepare_left_response_p_request(str(path), workspace_profile="left-current-rest-1051-1164",
                                             left_shoulder_startup_degrees=1)
        assert len(seen) == 1 and not list(state.iterdir())
        return
    result = m.prepare_left_response_p_request(str(path), workspace_profile="left-current-rest-1051-1164",
                                              left_shoulder_startup_degrees=1)
    assert result["request"] == seen[0]
    assert result["request"]["left_shoulder_startup_degrees"] == 1
    assert gains._right_p20_file(Path(result["path"]))[0] == result["request"]
    assert result["hardware_started"] is False
    assert not list(state.glob("*-used-*")) and not list(state.glob(".*.tmp"))


@pytest.mark.parametrize("degrees,profile", [(True, "left-current-rest-1051-1164"),
    (False, "left-current-rest-1051-1164"), (.5, "left-current-rest-1051-1164"),
    (1.000001, "left-current-rest-1051-1164"), (2, "left-current-rest-1051-1164"),
    ("1", "left-current-rest-1051-1164"), (float("nan"), "left-current-rest-1051-1164"),
    (float("inf"), "left-current-rest-1051-1164"), (1, None)])
def test_left_shoulder_startup_preparation_rejects_free_bounds_and_missing_profile(tmp_path, monkeypatch, degrees, profile):
    from pathlib import Path
    m = module()
    path, config, _, _ = request_preparation_config(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="startup"):
        m.prepare_left_response_p_request(str(path), workspace_profile=profile,
                                         left_shoulder_startup_degrees=degrees)
    assert not list(Path(config["physical_state_directory"]).iterdir())


def test_left_shoulder_startup_cli_relays_exact_opt_in_and_canonical_request(tmp_path, monkeypatch, capsys):
    import io
    import json
    import shlex
    import subprocess
    import sys
    from contextlib import redirect_stdout
    m = module()
    path, config, _, _ = request_preparation_config(tmp_path, monkeypatch)
    native_config = "/home/pickmanmike/.local/share/am1-physical-adapter-01/deployments/7438c74662d59a02/physical.json"
    calls = []
    def remote(args, **options):
        calls.append(args)
        assert shlex.split(args[7]) == ["/home/pickmanmike/lerobot_alohamini/.venv/bin/python", "-B", "-",
                                       native_config, "120", "left-current-rest-1051-1164", "1"]
        assert options["shell"] is False
        output = io.StringIO()
        with monkeypatch.context() as context, redirect_stdout(output):
            context.setattr(sys, "argv", ["-", str(path), "120", "left-current-rest-1051-1164", "1"])
            context.delattr(m, "prepare_left_response_p_request")
            exec(compile(options["input"], "<fixed-preparation-relay>", "exec"), {})
        return SimpleNamespace(stdout=output.getvalue(), stderr="", returncode=0)
    monkeypatch.setattr(subprocess, "run", remote)
    assert m.main(["--prepare-left-response-p", "--ssh", "am1-pi", "--config", native_config,
                   "--left-response-workspace", "current-rest", "--left-shoulder-startup-degrees", "1"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["request"]["left_shoulder_startup_degrees"] == 1
    assert result["request"]["workspace_profile"] == "left-current-rest-1051-1164"
    assert result["request"]["source_head"] == config["host_head"]
    assert result["hardware_started"] is False and len(calls) == 1


@pytest.mark.parametrize("extra", [["--left-shoulder-startup-degrees", "1"],
    ["--left-response-workspace", "current-rest", "--left-shoulder-startup-degrees", "0.5"],
    ["--left-response-workspace", "current-rest", "--left-shoulder-startup-degrees", "2"]])
def test_left_shoulder_startup_cli_refuses_missing_profile_or_free_bound_before_ssh(monkeypatch, extra):
    import subprocess
    m = module()
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("no SSH for unsupported startup allowance"))
    with pytest.raises(SystemExit):
        m.main(["--prepare-left-response-p", "--ssh", "am1-pi", "--config",
                "/home/pickmanmike/.local/share/am1-physical-adapter-01/deployments/7438c74662d59a02/physical.json", *extra])

@pytest.fixture
def readiness_gate_case(tmp_path, monkeypatch):
    """Run the real startup gate with file replay and inert unit/client boundaries."""
    from pathlib import Path
    from lerobot.robots.alohamini import alohamini_client
    from examples.alohamini.am1_session_contract import RECIPES
    m = module()
    _, config, _, _ = request_preparation_config(tmp_path, monkeypatch)
    provider = m.ProtectedHostRun(config)
    monkeypatch.setattr(provider, "_validate_source", lambda: None)
    provider.observation = SimpleNamespace(evidence=lambda: {"qualified": True})
    state = SimpleNamespace(now=0.0, deadline=.6, steps=0, initial_log=b"", append=b"",
                            socket_at_start=False, socket_step=1, fault=None, fault_step=1,
                            clients=[], reads=[], host=None, camera=None, provider=provider)
    run_id="28122793-8395-4d47-9491-29a6cfc00d9a"
    incarnation="751e9680-ce4c-40fd-a4e5-cf2b61ae8160"
    run_directory=Path(config["run_root"]) / run_id[:8]
    logpath=run_directory / "host-runtime.log"
    socketpath=run_directory / "observation.sock"
    original_open=Path.open

    class Reader:
        def __init__(self, stream):
            self.stream=stream
        def __enter__(self):
            self.stream.__enter__()
            return self
        def __exit__(self, *args):
            return self.stream.__exit__(*args)
        def seek(self, *args):
            return self.stream.seek(*args)
        def read(self, size=-1):
            state.reads.append((self.stream.tell(), size))
            return self.stream.read(size)

    def opened(path, mode="r", *args, **kwargs):
        stream=original_open(path, mode, *args, **kwargs)
        return Reader(stream) if path == logpath and mode == "rb" else stream
    monkeypatch.setattr(Path, "open", opened)

    def unit(name, *args, **kwargs):
        result=SimpleNamespace(returncode=None)
        result.poll=lambda: result.returncode
        if "-host-" in name:
            state.host=result
            logpath.write_bytes(state.initial_log)
            if state.socket_at_start:
                socketpath.touch()
        else:
            state.camera=result
        return result
    monkeypatch.setattr(m, "OwnedUnit", unit)

    class Client:
        def __init__(self, config):
            self.connected=False
            state.clients.append(self)
        def connect(self, *, cancel_check):
            cancel_check()
            self.connected=True
    monkeypatch.setattr(alohamini_client, "AlohaMiniClient", Client)

    def cancel():
        if state.fault == "stop" and state.steps >= state.fault_step:
            raise m.StartupCancelledError("physical startup cancelled by Stop")
        if state.now >= state.deadline:
            raise RuntimeError("physical startup deadline expired")

    def sleep(seconds):
        assert seconds == .05
        state.steps += 1
        state.now += seconds
        if state.steps == 1 and state.append:
            with original_open(logpath, "ab") as stream:
                stream.write(state.append)
        if state.steps == state.socket_step:
            socketpath.touch()
        if state.steps == state.fault_step and state.fault in {"host", "camera"}:
            getattr(state, state.fault).returncode=1
    monkeypatch.setattr(m.time, "sleep", sleep)
    state.start=lambda: provider.start(run_id, incarnation, RECIPES["physical-live-teleop"], cancel)
    state.logpath, state.socketpath = logpath, socketpath
    try:
        yield state
    finally:
        for stream in (provider.host_log, provider.camera_log):
            if stream is not None:
                stream.close()


def test_readiness_gate_replays_actual118_output_burst_before_socket(readiness_gate_case):
    case=readiness_gate_case
    ready=b'[LIFT OPERATIONAL] {"phase":"operational_ready"}'
    # Actual118: ready line ends at203637; receipts end at266824. The first
    # routine lift record then removes readiness from the previous64k tail.
    case.initial_log=b"x" * 203225 + ready + b" " * (412-len(ready)-1) + b"\n" + b"s" * 63187
    case.append=b"c" * 94 + b"l" * 1364
    assert len(case.initial_log) == 266824
    assert b'"operational_ready"' not in (case.initial_log+case.append)[-64000:]
    case.start()
    assert len(case.clients) == 1 and case.clients[0].connected
    assert case.reads[0][0] == 0
    assert all(0 < size <= 64000 for _, size in case.reads)
    assert [at for at, _ in case.reads] == sorted(set(at for at, _ in case.reads))


def test_readiness_gate_latches_marker_while_its_socket_is_absent(readiness_gate_case):
    case=readiness_gate_case
    case.initial_log=b'[LIFT OPERATIONAL] {"phase":"operational_ready"}\n'
    case.append=b"l" * 128000
    case.socket_step=3
    case.start()
    assert case.steps == 3 and case.clients[0].connected
    assert len(case.reads) == 1, "latched readiness must not depend on later output volume"


def test_readiness_gate_matches_marker_split_across_bounded_reads(readiness_gate_case):
    case=readiness_gate_case
    case.initial_log=b"x" * 63991 + b'"operational_ready"' + b"\n"
    case.socket_at_start=True
    case.start()
    assert case.clients[0].connected
    assert case.reads == [(0, 64000), (64000, 64000)]


@pytest.mark.parametrize("fault,pattern", [("stop", "cancelled by Stop"),
    ("deadline", "startup deadline expired"), ("host", "host exited during startup"),
    ("camera", "camera exited during startup")])
def test_readiness_gate_retains_stop_deadline_and_owned_exit_fences(readiness_gate_case, fault, pattern):
    case=readiness_gate_case
    case.initial_log=b'[LIFT OPERATIONAL] {"phase":"operational_ready"}\n'
    case.socket_step=1
    case.fault=fault
    if fault == "deadline":
        case.deadline=.05
    with pytest.raises(RuntimeError, match=pattern):
        case.start()
    assert case.clients == []


def test_readiness_gate_socket_or_other_run_marker_cannot_supply_readiness(readiness_gate_case, tmp_path):
    case=readiness_gate_case
    other=tmp_path / "other-run"
    other.mkdir()
    (other / "host-runtime.log").write_bytes(b'"operational_ready"')
    case.initial_log=b'"phase":"live"\n' * 5000
    case.socket_at_start=True
    with pytest.raises(RuntimeError, match="startup deadline expired"):
        case.start()
    assert not case.clients
    assert all(0 < size <= 64000 for _, size in case.reads)

def test_readiness_gate_matches_marker_split_across_writer_polls(readiness_gate_case):
    case = readiness_gate_case
    case.initial_log = b'[LIFT OPERATIONAL] {"phase":"operational_'
    case.append = b'ready"}\n'
    case.start()
    assert case.clients[0].connected
    assert case.reads == [(0, 64000), (len(case.initial_log), 64000)]


@pytest.mark.parametrize("fault", ["stop", "deadline"])
def test_readiness_gate_rechecks_cancellation_after_socket_before_client(readiness_gate_case, monkeypatch, fault):
    from pathlib import Path
    case = readiness_gate_case
    case.initial_log = b'[LIFT OPERATIONAL] {"phase":"operational_ready"}\n'
    case.socket_at_start = True
    exists = Path.exists
    def cancellation_at_socket(path):
        present = exists(path)
        if path == case.socketpath and present:
            if fault == "stop":
                case.fault, case.fault_step = "stop", 0
            else:
                case.deadline = 0
        return present
    monkeypatch.setattr(Path, "exists", cancellation_at_socket)
    with pytest.raises(RuntimeError, match="cancelled by Stop" if fault == "stop" else "startup deadline expired"):
        case.start()
    assert not case.clients