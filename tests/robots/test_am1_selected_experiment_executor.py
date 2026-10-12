"""Frozen experiment admission cannot leak into ordinary protected recipes."""

import copy
from types import SimpleNamespace

import pytest

from examples.alohamini import am1_session_contract as contract
from tests.robots.test_am1_physical_executor import ControlledHost, executor, module

RECIPE = "physical-left-elbow-experiment"


def payload():
    return dict(schema_version=1,
        envelope_sha256=contract.SELECTED_ELBOW_EXPERIMENT_ENVELOPE_SHA256,
        excursion_norm=-10.0, ramp_s=10.0, endpoint_dwell_s=2.0,
        current_cap_ma=100.0, live_ceiling_s=60.0,
        minimum_response_raw=56, return_tolerance_raw=22,
        goal_readback_checkpoints_s=[4.0, 5.0])


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
        io.start("run", "host", contract.RECIPES["physical-left-elbow-positive-control"],
            lambda: None, selected_experiment=payload())


def test_native_io_requires_frozen_parameters_for_new_recipe_before_source_access():
    io = object.__new__(module().ProtectedHostRun)
    io.config = {}
    with pytest.raises(ValueError, match="experiment"):
        io.start("run", "host", contract.RECIPES[RECIPE], lambda: None)


def test_descriptor_setter_rejects_unvalidated_ordinary_mapping():
    e = executor(ControlledHost())
    with pytest.raises(ValueError, match="experiment"):
        e.set_selected_experiment(copy.deepcopy(payload()))
