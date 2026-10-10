"""Real adapter boundaries; injected hardware never opens a serial device."""

import importlib
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
    assert "/python" in command


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
