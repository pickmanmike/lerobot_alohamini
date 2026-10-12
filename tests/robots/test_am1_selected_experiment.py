"""Selected finite experiments retain their own admission, limits and measured verdict."""

import copy
import hashlib
import json

import pytest

from examples.alohamini import am1_session_contract as contract, scripted_leader
from examples.alohamini.am1_finite_task import JOINT_KEYS, FiniteTask
from examples.alohamini.am1_scripted_prepare import SHOULDER
from tests.robots.test_am1_elbow_response_profile import norm
from tests.robots.test_am1_positive_control import PositiveFeedback, settings_proof
from tests.robots.test_am1_selected_elbow_profiles import seed

RECIPE = "physical-left-elbow-experiment"
PROFILE = "LeftElbowExperiment"
KEY = "arm_left_elbow_flex.pos"


def payload(**changes):
    pin = getattr(contract, "SELECTED_ELBOW_EXPERIMENT_ENVELOPE_SHA256", None)
    assert pin is not None, "selected experiment envelope is missing"
    result = dict(schema_version=1, envelope_sha256=pin, excursion_norm=-10.0, ramp_s=10.0,
                  endpoint_dwell_s=2.0, current_cap_ma=100.0, live_ceiling_s=60.0,
                  minimum_response_raw=56, return_tolerance_raw=22,
                  goal_readback_checkpoints_s=[4.0, 5.0])
    return dict(result, **changes)


def definition(**changes):
    cls = getattr(contract, "SelectedElbowExperiment", None)
    assert cls is not None, "typed selected experiment is missing"
    return cls.from_payload(payload(**changes))


def provider(d=None):
    initial = seed(norm(2399))
    initial[SHOULDER] = 99.8
    return scripted_leader.LeftElbowPositiveControlInput(
        initial, joint_keys=JOINT_KEYS, fps=10, emit=lambda _: None, definition=d or definition())


def test_descriptor_pin_derives_finite_waveform_and_copies_input():
    d = definition()
    assert (d.recipe_name, d.profile, d.nominal_trajectory_s, d.trajectory_s, d.live_s) == (RECIPE, PROFILE, 28, 30, 60)
    data = payload()
    expected_hash = hashlib.sha256(json.dumps(data, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()).hexdigest()
    assert d.payload == data and d.sha256 == expected_hash
    data["goal_readback_checkpoints_s"].append(6)
    assert d.payload["goal_readback_checkpoints_s"] == [4.0, 5.0]
    assert d.summary()["sha256"] == expected_hash


@pytest.mark.parametrize("change", [
    {"schema_version": True}, {"envelope_sha256": "0" * 64}, {"unknown": 1},
    {"excursion_norm": True}, {"excursion_norm": 0}, {"excursion_norm": -10.01},
    {"ramp_s": 9.99}, {"ramp_s": 11.1}, {"endpoint_dwell_s": float("nan")},
    {"endpoint_dwell_s": 2.01}, {"endpoint_dwell_s": 0.1},
    {"current_cap_ma": 100.01}, {"current_cap_ma": 0}, {"current_cap_ma": "100"},
    {"live_ceiling_s": 60.01}, {"live_ceiling_s": 28},
    {"minimum_response_raw": True}, {"minimum_response_raw": 0},
    {"return_tolerance_raw": -1}, {"return_tolerance_raw": 22.5},
    {"goal_readback_checkpoints_s": [4, 5, 6]}, {"goal_readback_checkpoints_s": [5, 4]},
    {"goal_readback_checkpoints_s": [4, 4]}, {"goal_readback_checkpoints_s": [0]},
    {"goal_readback_checkpoints_s": [28]}, {"goal_readback_checkpoints_s": [True]},
])
def test_invalid_descriptor_never_constructs_an_admitted_definition(change):
    cls = getattr(contract, "SelectedElbowExperiment", None)
    assert cls is not None, "typed selected experiment is missing"
    with pytest.raises(ValueError):
        cls.from_payload(payload(**change))


def test_signed_waveform_has_one_frozen_reference_and_context_digest():
    p = provider()
    p.admit(0)
    last = 0
    for at, phase, target in [(0, "baseline", norm(2399)), (7, "outbound", norm(2399) - 5),
                              (12, "endpoint", norm(2399) - 10), (19, "return", norm(2399) - 5),
                              (24, "origin", norm(2399)), (28, "final", norm(2399))]:
        for i in range(last + 1, round(at * 10) + 1):
            scripted_leader.ScriptedLeaderInput.advance(p, i / 10, p.origin, i)
        last = round(at * 10)
        c = p.diagnostic_context
        assert c["phase"] == phase and c["requested_target"] == pytest.approx(target)
        assert c["experiment_sha256"] == p.definition.sha256 and p.definition.context_valid(c)
        assert all(p.get_action()[k.removeprefix("arm_")] == v for k, v in p.origin.items() if k != KEY)
        assert not p.definition.context_valid(dict(c, experiment_sha256="0" * 64))


def selected_feedback(p, *, moves=True, final_offset=0):
    feed = PositiveFeedback(p, moves=moves, final_offset=final_offset)
    feed.settings["binding"].update(recipe=RECIPE, experiment_sha256=p.definition.sha256)
    return feed


def run_selected(p, *, moves=True, final_offset=0):
    feed = selected_feedback(p, moves=moves, final_offset=final_offset)
    p.admit(0)
    for i in range(1, 901):
        now = i / 30
        positions, item = feed.get(now, i)
        p.collect_feedback(now, positions, i, selected_feedback=item)
        if i % 3 == 0:
            p.advance(now, positions, i, selected_feedback=item)
        if p.complete:
            return feed
    pytest.fail("finite selected provider did not complete")


@pytest.mark.parametrize("direction", [-10.0, 10.0])
@pytest.mark.parametrize("moves,coarse", [(True, True), (False, False)])
def test_signed_native_response_and_return_have_independent_program_verdict(direction, moves, coarse):
    p = provider(definition(excursion_norm=direction))
    run_selected(p, moves=moves)
    o = p.positive_control
    assert o["program_completed"] is True and o["coarse_functional_pass"] is coarse
    assert o["encoded_excursion_raw"] == (111 if direction < 0 else 110)
    assert o["outward_response_raw"] >= 55 if moves else o["outward_response_raw"] == 0
    assert o["reverse_response_raw"] >= 55 if moves else o["reverse_response_raw"] == 0
    execution = dict(original_seed=p.origin, progress_s=28, cycles_completed=1,
                     returns_qualified=int(coarse), positive_control=o)
    assert p.definition.completion_expectation(execution) == (28, 1, int(coarse))
    wrong = copy.deepcopy(execution)
    wrong["positive_control"]["experiment"]["sha256"] = "0" * 64
    assert p.definition.completion_expectation(wrong) is None


def test_declared_practical_criteria_require_actual_calibration_without_changing_old_defaults():
    p = provider(definition(minimum_response_raw=54))
    feed = selected_feedback(p)
    positions, item = feed.get(.1, 1)
    with pytest.raises(ValueError, match="criteria"):
        p.collect_feedback(.1, positions, 1, selected_feedback=item)
    p = provider(definition(return_tolerance_raw=23))
    feed = selected_feedback(p)
    positions, item = feed.get(.1, 1)
    with pytest.raises(ValueError, match="criteria"):
        p.collect_feedback(.1, positions, 1, selected_feedback=item)
    assert contract.LEFT_ELBOW_POSITIVE_CONTROL.current_cap_ma == 100
    old = settings_proof()
    assert contract.LEFT_ELBOW_POSITIVE_CONTROL.settings_valid(old)
    old["binding"].update(recipe=RECIPE, experiment_sha256=definition().sha256)
    assert not contract.LEFT_ELBOW_POSITIVE_CONTROL.settings_valid(old)


def test_selected_task_requires_its_descriptor_and_retains_fresh_measured_holds():
    d = definition()
    initial = provider(d).origin
    task = FiniteTask(contract.RECIPES[RECIPE], initial, 0, 3, selected_experiment=d)
    assert task.profile == PROFILE and task.provider.origin == initial
    assert task.provider.get_action()["left_shoulder_lift.pos"] == 99.8
    with pytest.raises(ValueError, match="experiment"):
        FiniteTask(contract.RECIPES[RECIPE], initial, 0, 3)
    with pytest.raises(ValueError, match="experiment"):
        FiniteTask(contract.RECIPES["physical-left-elbow-positive-control"], initial, 0, 3, selected_experiment=d)


def test_changed_timing_confirmation_preparation_uses_its_exact_derived_duration():
    d = definition(excursion_norm=-6.0, ramp_s=6.0, endpoint_dwell_s=1.0,
                   minimum_response_raw=34, return_tolerance_raw=13)
    initial = provider(d).origin
    comparator = dict(run_id="12345678-1234-1234-1234-123456789abc",
                      reference_normalized=initial[KEY], original_seed=initial)
    task = FiniteTask(contract.RECIPES[RECIPE], initial, 0, 3,
                      selected_experiment=d, positive_comparator=comparator)
    assert task.provider.duration_s == 21
    for i in range(1, 31):
        task.advance(i / 10, dict(sequence=i, positions=initial))
        if not task.provider.preparing:
            break
    assert not task.provider.preparing
    assert task.provider._program.definition is d and task.provider._program.duration_s == 21


@pytest.mark.parametrize("receipt_current,accepted", [(78.0, True), (85.0, False)])
def test_selected_sparse_receipt_uses_its_declared_smaller_cap_with_a_quiet_latest_cache(receipt_current, accepted):
    p = provider(definition(current_cap_ma=80.0))
    p.admit(0)
    feed = selected_feedback(p)
    positions, item = feed.get(.1, 1)
    item["sparse_receipts"]["baseline"]["decoded"]["present_current_ma"] = receipt_current
    if accepted:
        p.advance(.1, positions, 1, selected_feedback=item)
        assert p.elapsed_s == .1
    else:
        with pytest.raises(ValueError, match="80 mA"):
            p.advance(.1, positions, 1, selected_feedback=item)
        assert p.elapsed_s == 0
