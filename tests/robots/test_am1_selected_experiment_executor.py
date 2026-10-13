"""Frozen experiment admission cannot leak into ordinary protected recipes."""

import copy
import threading
import time
from types import SimpleNamespace

import pytest

from examples.alohamini import am1_session_contract as contract
from tests.robots.test_am1_physical_executor import ControlledHost, executor, module, until
from tests.robots.test_am1_selected_experiment import OperationFeedback, operation_definition, provider

RECIPE = "physical-left-elbow-experiment"


def payload():
    return {
        "schema_version": 1,
        "envelope_sha256": contract.SELECTED_ELBOW_EXPERIMENT_ENVELOPE_SHA256,
        "excursion_norm": -10.0,
        "ramp_s": 10.0,
        "endpoint_dwell_s": 2.0,
        "current_cap_ma": 100.0,
        "live_ceiling_s": 60.0,
        "minimum_response_raw": 56,
        "return_tolerance_raw": 22,
        "goal_readback_checkpoints_s": [4.0, 5.0],
    }


def definition():
    return contract.SelectedElbowExperiment.from_payload(payload())


def test_explicit_recipe_is_supported_but_definition_required_before_binding():
    e = executor(ControlledHost())
    assert e.supports(contract.RECIPES[RECIPE])
    with pytest.raises(RuntimeError, match="experiment"):
        e.bind_run("run", contract.RECIPES[RECIPE])
    assert e.run_id is None


def test_frozen_descriptor_setter_refuses_midrun_change_and_allows_idle_clear():
    e = executor(ControlledHost())
    spec = definition()
    e.set_selected_experiment(spec)
    before = e.selected_experiment.payload
    outer = spec.payload
    outer["goal_readback_checkpoints_s"].append(9.0)
    assert e.selected_experiment.payload == before
    e.worker = SimpleNamespace(is_alive=lambda: True)
    with pytest.raises(RuntimeError, match="active"):
        e.set_selected_experiment(None)
    assert e.selected_experiment.payload == before
    e.worker = None
    e.set_selected_experiment(None)
    assert e.selected_experiment is None


def test_descriptor_preserved_at_binding_and_never_uses_historical_return():
    e = executor(ControlledHost())
    spec = definition()
    e.set_selected_experiment(spec)
    e.return_reference = {"reference_raw": 2472}
    e.bind_run("run", contract.RECIPES[RECIPE])
    assert e.selected_experiment.payload == spec.payload
    assert e._reference_for_recipe() is None
    assert e._lifecycle["phase"] == "startup"


def test_native_io_rejects_descriptor_for_old_recipe_before_source_access():
    io = object.__new__(module().ProtectedHostRun)
    io.config = {}
    with pytest.raises(ValueError, match="experiment"):
        io.start(
            "run",
            "host",
            contract.RECIPES["physical-left-elbow-positive-control"],
            lambda: None,
            selected_experiment=payload(),
        )


def test_native_io_requires_frozen_parameters_for_new_recipe_before_source_access():
    io = object.__new__(module().ProtectedHostRun)
    io.config = {}
    with pytest.raises(ValueError, match="experiment"):
        io.start("run", "host", contract.RECIPES[RECIPE], lambda: None)


def test_descriptor_setter_rejects_unvalidated_ordinary_mapping():
    e = executor(ControlledHost())
    with pytest.raises(ValueError, match="experiment"):
        e.set_selected_experiment(copy.deepcopy(payload()))


class OperationHost(ControlledHost):
    """Real executor worker, accelerated injected clocks, ordinary owned ACKs only."""

    def __init__(self, clock):
        super().__init__()
        self.clock = clock
        self.positions = dict(provider(operation_definition()).origin)
        self.feed = None
        self.active_at_send = []
        self.closed_losses = 0
        self.loss_started = None
        self.loss_at = (27, 29)
        self.owner = None

    def start(self, run_id, incarnation, recipe, cancel, *, selected_experiment=None):
        assert selected_experiment == operation_definition().payload
        super().start(run_id, incarnation, recipe, cancel)

    def poll(self, previous):
        self.sequence += 1
        at = self.clock[0]
        marker = dict(self.marker, run_id=self.run_id, host_incarnation=self.incarnation)
        sample = {
            "positions": dict(self.positions),
            "sequence": self.sequence,
            "at": at - 0.006,
            "ack": marker,
            "observation": {},
            "provenance": "protected-host/measured-feedback",
        }
        task = self.owner.task
        if task and not task.provider.preparing:
            progress = task.provider.elapsed_s
            if (
                self.loss_started is None
                and self.closed_losses < 2
                and progress >= self.loss_at[self.closed_losses]
            ):
                self.loss_started = at
                self.owner.observation.qualified = False
            if self.loss_started is not None and at - self.loss_started >= 0.7:
                self.owner.observation.qualified = True
                self.loss_started = None
                self.closed_losses += 1
        action = self.actions[-1] if self.actions else {}
        context = action.get("_am1_selected_joint_evidence")
        if marker["state"] == "active" and context is not None:
            p = task.provider._program
            proxy = SimpleNamespace(
                origin=p.origin,
                definition=p.definition,
                diagnostic_context=context,
                _verified_receipts=p._verified_receipts,
            )
            if self.feed is None:
                self.feed = OperationFeedback(proxy)
                self.feed.settings["binding"].update(
                    run_id=self.run_id,
                    host_incarnation=self.incarnation,
                    recipe=RECIPE,
                    experiment_sha256=p.definition.sha256,
                )
            self.feed.p = proxy
            keys = set(self.feed.receipts)
            positions, item = self.feed.get(at, self.sequence)
            identity = {
                "run_id": self.run_id,
                "host_incarnation": self.incarnation,
                "epoch": marker["epoch"],
                "intent_revision": marker["intent_revision"],
            }
            item.update(identity)
            item["action_sequence"] = marker["accepted_action_sequence"]
            item["visible_control"] = copy.deepcopy(self.feed.settings)
            for key in set(self.feed.receipts) - keys:
                self.feed.receipts[key].update(identity)
                self.feed.receipts[key]["action_sequence"] = marker["accepted_action_sequence"]
            item["sparse_receipts"] = copy.deepcopy(self.feed.receipts)
            self.positions = dict(positions)
            sample.update(positions=positions, selected_joint=item)
        return sample

    def send(self, action):
        assert all(action[k] == 0 for k in ("x.vel", "y.vel", "theta.vel", "lift_axis.vel"))
        super().send(copy.deepcopy(action))
        self.active_at_send.append(self.owner.task.active if self.owner.task else False)


class AcceleratedWake:
    def __init__(self, clock):
        self.clock = clock
        self.event = threading.Event()

    def wait(self, timeout):
        self.clock[0] += 1 / 30
        time.sleep(0.0007)

    def clear(self):
        self.event.clear()

    def set(self):
        self.event.set()


@pytest.mark.parametrize("stop_during_rejoin", [False, True])
def test_real_selected_executor_recovers_from_measured_hold_without_reseed_or_first_goal_jump(
    stop_during_rejoin,
):
    clock = [1.0]
    host = OperationHost(clock)
    e = executor(host)
    host.owner = e
    e.clock = lambda: clock[0]
    e._wake = AcceleratedWake(clock)
    d = operation_definition()
    recipe = contract.RECIPES[RECIPE]
    e.set_selected_experiment(d)
    e.bind_run("run", recipe)
    try:
        assert e.begin(recipe)
        until(lambda: e.evidence()["lifecycle"]["phase"] == "live")
        admitted = e.task.admitted_at
        deadline = e._lifecycle["native_live_at"] + 90
        end = time.monotonic() + 6
        stopped = False
        while time.monotonic() < end and e.worker.is_alive():
            e.dispatch(recipe)
            if stop_during_rejoin and any(
                a.get("_am1_selected_joint_evidence", {}).get("phase") == "rejoin" for a in host.actions
            ):
                e.finish("stopped")
                stopped = True
                break
            if e._finite_completion is not None:
                e.finish("completed")
                break
            time.sleep(0.002)
        until(lambda: not e.worker.is_alive(), timeout=2)
        assert e._fault is None
        assert e.task.admitted_at == admitted and e._lifecycle["native_live_at"] + 90 == deadline
        assert e.task.original_seed == provider(d).origin
        bridges = [
            a for a in host.actions if a.get("_am1_selected_joint_evidence", {}).get("phase") == "rejoin"
        ]
        assert bridges
        first_per_epoch = {}
        for index, action in enumerate(host.actions):
            marker = action["_am1_local_control"]
            if marker["mode"] == "active":
                first_per_epoch.setdefault(marker["epoch"], (index, action))
        for epoch, (index, action) in first_per_epoch.items():
            if epoch > 2:
                context = action["_am1_selected_joint_evidence"]
                assert context["phase"] == "rejoin" and context["rejoin"]["elapsed_s"] == 0
                assert host.active_at_send[index] is False  # Native ACK precedes task resume.
        first = bridges[0]["_am1_selected_joint_evidence"]
        assert first["requested_target"] == first["rejoin"]["held_target"]
        assert first["rejoin"]["elapsed_s"] == 0
        assert first["requested_target"] != first["rejoin"]["retained_target"]
        for a, b in zip(bridges, bridges[1:], strict=False):
            ca, cb = a["_am1_selected_joint_evidence"], b["_am1_selected_joint_evidence"]
            if ca["rejoin"]["acquired_at_monotonic_s"] == cb["rejoin"]["acquired_at_monotonic_s"]:
                assert ca["progress_s"] == cb["progress_s"]
                assert abs(cb["requested_target"] - ca["requested_target"]) <= 0.1 + 1e-9
        assert all(a["_am1_local_control"]["run_id"] == "run" for a in host.actions)
        assert all(a["_am1_local_control"]["host_incarnation"] == e.host_incarnation for a in host.actions)
        if stop_during_rejoin:
            assert stopped and e._finite_completion is None and e._lifecycle["terminal_status"] == "stopped"
        else:
            assert host.closed_losses == 2 and e.task.complete
            assert d.completion_expectation(e._finite_completion["execution"]) == (58, 2, 2)
    finally:
        e.finish("stopped")
        if e.worker:
            e.worker.join(timeout=2)
