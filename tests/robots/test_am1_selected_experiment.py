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
    result = {
        "schema_version": 1,
        "envelope_sha256": pin,
        "excursion_norm": -10.0,
        "ramp_s": 10.0,
        "endpoint_dwell_s": 2.0,
        "current_cap_ma": 100.0,
        "live_ceiling_s": 60.0,
        "minimum_response_raw": 56,
        "return_tolerance_raw": 22,
        "goal_readback_checkpoints_s": [4.0, 5.0],
    }
    return dict(result, **changes)


def definition(**changes):
    cls = getattr(contract, "SelectedElbowExperiment", None)
    assert cls is not None, "typed selected experiment is missing"
    return cls.from_payload(payload(**changes))


def operation_payload(**changes):
    pin = getattr(contract, "SELECTED_ELBOW_EXPERIMENT_OPERATION_ENVELOPE_SHA256", None)
    assert pin is not None, "fixed-reference operation envelope is missing"
    result = dict(
        payload(
            schema_version=2,
            envelope_sha256=pin,
            excursion_norm=10,
            ramp_s=10,
            endpoint_dwell_s=2,
            current_cap_ma=150,
            live_ceiling_s=90,
            goal_readback_checkpoints_s=[5, 37],
        ),
        cycle_count=2,
        intercycle_hold_s=6,
    )
    return dict(result, **changes)


class OperationFeedback(PositiveFeedback):
    """Simulated droop/residual; real provider proof paths, not physical movement."""

    def __init__(self, p, *, second_moves=True, residual_raw=17):
        super().__init__(p)
        self.second_moves = second_moves
        self.residual_raw = residual_raw

    def get(self, now, sequence, *, current=13.0):
        positions, item = super().get(now, sequence, current=current)
        context = item["context"]
        cycle, phase, fraction = self.p.definition.phase(context["progress_s"], context["acquisition_s"])
        offset = (
            0
            if phase == "baseline"
            else round(
                (75 if cycle == 1 else 75 - self.residual_raw) * fraction
                + (0 if cycle == 1 else self.residual_raw)
            )
            if phase == "outbound"
            else 75
            if phase == "endpoint"
            else round(self.residual_raw + (75 - self.residual_raw) * fraction)
            if phase == "return"
            else self.residual_raw
        )
        if cycle == 2 and not self.second_moves:
            offset = 17
        item["position"].update(raw=2399 + offset, normalized=norm(2399 + offset))
        positions[KEY] = norm(2399 + offset)
        key = context["sample_key"]
        if key is not None and key not in self.p._verified_receipts:
            item["sparse_receipts"][key]["decoded"]["present_position_raw"] = 2399 + offset
            self.receipts[key] = copy.deepcopy(item["sparse_receipts"][key])
        item["visible_control"]["binding"].update(recipe=RECIPE, experiment_sha256=self.p.definition.sha256)
        return positions, item


def run_operation_provider(p, *, second_moves=True, residual_raw=17, until=None):
    feed = OperationFeedback(p, second_moves=second_moves, residual_raw=residual_raw)
    p.admit(0)
    for i in range(1, 1900):
        now = i / 30
        positions, item = feed.get(now, i)
        p.collect_feedback(now, positions, i, selected_feedback=item)
        if i % 3 == 0:
            p.advance(now, positions, i, selected_feedback=item)
        if p.complete or until is not None and p.elapsed_s >= until:
            return p.positive_control
    pytest.fail("Composite selected provider did not finish")


def test_operation_cycles_measure_actual_start_and_keep_one_original_reference():
    p = provider(operation_definition())
    original = dict(p.origin)
    outcome = run_operation_provider(p)
    assert p.origin == original and p.elapsed_s == 58
    assert outcome["program_completed"] is True and outcome["coarse_functional_pass"] is True
    assert outcome["fine_return_pass"] is False
    assert len(outcome["cycles"]) == 2
    first, second = outcome["cycles"]
    assert first["starting_raw"] == 2399 and second["starting_raw"] == 2416
    assert first["reference_raw"] == second["reference_raw"] == outcome["reference_raw"]
    assert first["outward_response_raw"] == 75 and second["outward_response_raw"] == 58
    assert first["reverse_response_raw"] == second["reverse_response_raw"] == 58
    assert first["final_return_residual_raw"] == second["final_return_residual_raw"] == 17
    assert second["baseline_anchor_proof"]["context"]["phase"] == "intercycle_hold"
    assert outcome["intercycle_hold"]["window"]["sample_count"] >= 3
    execution = {
        "original_seed": original,
        "progress_s": 58,
        "cycles_completed": 2,
        "returns_qualified": 2,
        "positive_control": outcome,
    }
    assert p.definition.completion_expectation(execution) == (58, 2, 2)
    wrong = copy.deepcopy(execution)
    wrong["positive_control"]["cycles"][1]["starting_raw"] = 2399
    assert p.definition.completion_expectation(wrong) is None
    wrong = copy.deepcopy(execution)
    wrong["positive_control"]["cycles"][1]["endpoint_peak_proof"] = copy.deepcopy(
        first["endpoint_peak_proof"]
    )
    assert p.definition.completion_expectation(wrong) is None


def test_operation_cannot_import_a_previous_reference_instead_of_its_fresh_seed():
    d = operation_definition()
    initial = provider().origin
    comparator = {
        "run_id": "12345678-1234-1234-1234-123456789abc",
        "reference_normalized": initial[KEY],
        "original_seed": initial,
    }
    with pytest.raises(ValueError, match="fresh"):
        scripted_leader.LeftElbowPositiveControlInput(
            initial, joint_keys=JOINT_KEYS, fps=10, definition=d, positive_comparator=comparator
        )


def test_operation_keeps_program_completion_separate_from_a_static_second_cycle():
    p = provider(operation_definition())
    outcome = run_operation_provider(p, second_moves=False)
    assert outcome["program_completed"] is True and outcome["coarse_functional_pass"] is False
    assert outcome["cycles"][0]["coarse_functional_pass"] is True
    assert outcome["cycles"][1]["starting_raw"] == 2416
    assert outcome["cycles"][1]["outward_response_raw"] == 0
    assert outcome["cycles"][1]["reverse_response_raw"] == 0
    execution = {
        "original_seed": p.origin,
        "progress_s": 58,
        "cycles_completed": 2,
        "returns_qualified": 1,
        "positive_control": outcome,
    }
    assert p.definition.completion_expectation(execution) == (58, 2, 1)


@pytest.mark.parametrize("residual,qualified", [(17, 1), (22, 0)])
def test_operation_partial_live_snapshot_counts_only_actual_first_cycle_without_global_completion(
    residual, qualified
):
    d = operation_definition()
    p = provider(d)
    outcome = run_operation_provider(p, residual_raw=residual, until=26)
    assert len(outcome["cycles"]) == 1 and outcome["program_completed"] is False
    task = FiniteTask(contract.RECIPES[RECIPE], p.origin, 0, 3, selected_experiment=d)
    task.provider._program = p
    feed = OperationFeedback(p, residual_raw=residual)
    feed.receipts = copy.deepcopy(p._sparse)
    positions, selected = feed.get(26.1, 1000)
    task.advance(26.1, {"positions": positions, "sequence": 1000, "selected_joint": selected})
    snapshot = task.snapshot()
    assert snapshot["cycles_completed"] == 1 and snapshot["returns_qualified"] == qualified
    assert snapshot["complete"] is False and task.finished is False
    assert snapshot["positive_control"]["program_completed"] is False
    assert d.completion_expectation(snapshot) is None


def operation_definition(**changes):
    return contract.SelectedElbowExperiment.from_payload(operation_payload(**changes))


def rejoin_sample(p, now, sequence, *, context=None, write_completed=None):
    """Returned native bus operation plus original, independently acquired caches."""
    positions, item = OperationFeedback(p).get(now, sequence)
    if context is not None:
        item["context"] = copy.deepcopy(context)
        requested = context["requested_target"]
        encoded = p.definition.encoded_goal(requested, item["motor"]["calibration"])
        item["action"].update(
            requested=requested,
            relative_limited=requested,
            current_limited=requested,
            expected_goal_raw=encoded,
            final_normalized=norm(encoded),
        )
    completed = now - 0.010 if write_completed is None else write_completed
    item["action"].update(
        write_started_at_monotonic_s=completed - 0.002,
        write_completed_at_monotonic_s=completed,
        write_acknowledged=False,
    )
    return positions, item


def captured_rejoin_provider():
    """Replay b72b04a4's frozen cursor; this fixture does not claim new movement."""
    p = scripted_leader.LeftElbowPositiveControlInput(
        seed(norm(2418)),
        joint_keys=JOINT_KEYS,
        fps=10,
        definition=operation_definition(),
        emit=lambda _: None,
    )
    p.elapsed_s = 28.2
    p.prepare_rejoin(20604.33, dict(p.origin, **{KEY: norm(2429)}), 1026, 20604.32805421)
    p._rejoin["elapsed_s"] = 0.5
    p._action[KEY] = 41.1930737890448
    # Synthetic owner schedule, not the unrecorded previous native prepare clock.
    p.admit(20605.101940067)
    return p


def test_captured_repeated_half_second_writes_bound_next_cursor_by_returned_native_clock():
    p = captured_rejoin_provider()
    original = dict(p.origin)
    captured_rows = (
        (
            987,
            20605.127683199,
            20605.117710994,
            20605.118550583,
            20605.120207337,
            20605.121651609,
            20605.124653988,
            20605.126595499,
            6.5,
        ),
        (
            988,
            20605.158685145,
            20605.149715992,
            20605.150554489,
            20605.15082721,
            20605.152651222,
            20605.155651082,
            20605.15759789,
            0,
        ),
        (
            989,
            20605.194859629,
            20605.181701545,
            20605.18405413,
            20605.184703016,
            20605.186667546,
            20605.18967111,
            20605.192595229,
            0,
        ),
    )
    for sequence, now, started, completed, ps, pc, cs, cc, current in captured_rows:
        positions, item = rejoin_sample(p, now, sequence, write_completed=completed)
        item["action"]["write_started_at_monotonic_s"] = started
        item["position"].update(
            raw=2429,
            normalized=norm(2429),
            read_started_at_monotonic_s=ps,
            read_completed_at_monotonic_s=pc,
        )
        item["current"].update(
            raw=round(current / 6.5),
            ma=current,
            read_started_at_monotonic_s=cs,
            read_completed_at_monotonic_s=cc,
        )
        positions[KEY] = norm(2429)
        p.collect_feedback(now, positions, sequence, selected_feedback=item)
    # The capture clock is an upper bound for owner processing, not a claimed guard dt.
    capture = 20605.201940067
    # The guard retains its first changed-cursor clock through identical retransmits.
    available = capture - captured_rows[0][3]
    p.advance(capture, positions, sequence, selected_feedback=item)
    assert p._rejoin["elapsed_s"] - 0.5 == pytest.approx(available)
    assert p.diagnostic_context["requested_target"] >= 41.1930737890448 - available - 1e-9
    assert p.elapsed_s == 28.2 and p.origin == original
    assert item["action"]["write_acknowledged"] is False


def test_rejoin_waits_for_native_cursor_without_catching_up_after_snapshot_delay():
    p = captured_rejoin_provider()
    previous = copy.deepcopy(p.diagnostic_context)
    previous["rejoin"]["elapsed_s"] = 0.4
    previous["requested_target"] = previous["rejoin"]["held_target"] - 0.4
    for sequence, now in ((989, 20605.201940067), (990, 20605.301940067)):
        positions, item = rejoin_sample(p, now, sequence, context=previous)
        p.advance(now, positions, sequence, selected_feedback=item)
        assert p._rejoin["elapsed_s"] == 0.5
    now = 20605.401940067
    positions, item = rejoin_sample(p, now, 991, write_completed=now - 0.02)
    p.advance(now, positions, 991, selected_feedback=item)
    assert p._rejoin["elapsed_s"] == pytest.approx(0.52)
    assert p.elapsed_s == 28.2


@pytest.mark.parametrize(
    "clock_change",
    [
        {"write_completed_at_monotonic_s": None},
        {"write_completed_at_monotonic_s": True},
        {"write_completed_at_monotonic_s": float("nan")},
        {"write_started_at_monotonic_s": -1},
        {"write_started_at_monotonic_s": 20605.201940067},
        {"write_completed_at_monotonic_s": 20605.301940067},
    ],
)
def test_rejoin_invalid_returned_bus_clock_cannot_create_cursor_progress(clock_change):
    p = captured_rejoin_provider()
    now = 20605.201940067
    positions, item = rejoin_sample(p, now, 989)
    item["action"].update(clock_change)
    with pytest.raises(ValueError, match="rejoin.*write"):
        p.advance(now, positions, 989, selected_feedback=item)
    assert p._rejoin["elapsed_s"] == 0.5 and p.elapsed_s == 28.2


def test_rejoin_backwards_returned_write_clock_cannot_create_cursor_progress():
    p = captured_rejoin_provider()
    first = 20605.201940067
    positions, item = rejoin_sample(p, first, 989)
    p.collect_feedback(first, positions, 989, selected_feedback=item)
    now = first + 0.1
    positions, item = rejoin_sample(p, now, 990, write_completed=first - 0.02)
    with pytest.raises(ValueError, match="rejoin.*write"):
        p.advance(now, positions, 990, selected_feedback=item)
    assert p._rejoin["elapsed_s"] == 0.5


def test_rejoin_stale_acquisitions_cannot_reuse_a_valid_returned_write_for_progress():
    p = captured_rejoin_provider()
    now = 20605.201940067
    positions, item = rejoin_sample(p, now, 989)
    p.collect_feedback(now, positions, 989, selected_feedback=item)
    with pytest.raises(ValueError, match="rejoin.*stale"):
        p._advance_rejoin(now + 0.6)
    assert p._rejoin["elapsed_s"] == 0.5


def test_rejoin_waits_without_a_returned_native_cursor_and_drops_proof_on_protective_freeze():
    p = captured_rejoin_provider()
    p._advance_rejoin(20605.201940067)
    assert p._rejoin["elapsed_s"] == 0.5
    positions, item = rejoin_sample(p, 20605.301940067, 989)
    p.collect_feedback(20605.301940067, positions, 989, selected_feedback=item)
    p.freeze()
    new_hold = dict(p.origin, **{KEY: norm(2425)})
    p.prepare_rejoin(20605.401940067, new_hold, 990, 20605.395940067)
    p.admit(20605.401940067)
    p._advance_rejoin(20605.501940067)
    assert p._rejoin["elapsed_s"] == 0
    assert p.diagnostic_context["requested_target"] == new_hold[KEY]
    assert p.elapsed_s == 28.2 and p.origin[KEY] == norm(2418)


def test_operation_rejoin_starts_at_measured_hold_without_progress_or_seed_catchup():
    p = provider(operation_definition())
    p.admit(0)
    for i in range(1, 261):
        scripted_leader.ScriptedLeaderInput.advance(p, i / 10, p.origin, i)
    original, progress = dict(p.origin), p.elapsed_s
    p.freeze()
    held = dict(original, **{KEY: norm(2416)})
    p.prepare_rejoin(40, held, 700, 39.98)
    assert p.get_action()[KEY.removeprefix("arm_")] == held[KEY]
    c = p.diagnostic_context
    assert c["phase"] == "rejoin" and c["progress_s"] == progress
    assert c["rejoin"]["elapsed_s"] == 0 and p.definition.context_valid(c)
    p.admit(40)
    positions, item = rejoin_sample(p, 40.1, 701, write_completed=40)
    p.collect_feedback(40.1, positions, 701, selected_feedback=item)
    p._advance_rejoin(40.1)
    assert abs(p.get_action()[KEY.removeprefix("arm_")] - held[KEY]) <= 0.1 + 1e-9
    assert p.elapsed_s == progress and p.origin == original
    positions, item = rejoin_sample(p, 49, 702, write_completed=48.9)
    p.collect_feedback(49, positions, 702, selected_feedback=item)
    p._advance_rejoin(49)  # A delayed caller still requires fresh native proof and cannot catch up.
    assert abs(p.get_action()[KEY.removeprefix("arm_")] - held[KEY]) <= 0.2 + 1e-9
    assert p.elapsed_s == progress


@pytest.mark.parametrize("at,sequence", [(39, 700), (40, True), (40, -1)])
def test_operation_rejoin_rejects_missing_original_hold_acquisition(at, sequence):
    p = provider(operation_definition())
    with pytest.raises(ValueError, match="hold"):
        p.prepare_rejoin(at, p.origin, sequence, 39.98)


def test_operation_descriptor_preserves_schema1_and_pins_two_fixed_origin_cycles():
    old = definition()
    data = operation_payload()
    d = contract.SelectedElbowExperiment.from_payload(data)
    assert (d.nominal_trajectory_s, d.trajectory_s, d.live_s, d.cycle_count, d.intercycle_hold_s) == (
        58,
        60,
        90,
        2,
        6,
    )
    assert d.response_rule == {"commanded_error_raw": 45, "minimum_response_raw": 3, "window_s": 1.2}
    assert d.startup_hold_error_raw == 30 and d.current_cap_ma == 150
    assert (
        d.payload == data
        and d.sha256
        == hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    )
    assert old.payload == payload() and (old.nominal_trajectory_s, old.trajectory_s, old.live_s) == (
        28,
        30,
        60,
    )
    assert "cycle_count" not in old.payload and "intercycle_hold_s" not in old.payload


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": 1},
        {"schema_version": True},
        {"cycle_count": True},
        {"cycle_count": 1},
        {"cycle_count": 3},
        {"cycle_count": 2.0},
        {"intercycle_hold_s": True},
        {"intercycle_hold_s": float("nan")},
        {"intercycle_hold_s": 5.9},
        {"intercycle_hold_s": 6.1},
        {"live_ceiling_s": 90.1},
        {"live_ceiling_s": 58},
        {"ramp_s": 11.1},
        {"current_cap_ma": 150.1},
        {"unknown": 1},
    ],
)
def test_operation_descriptor_refuses_unpinned_or_unbounded_composition(change):
    data = operation_payload()
    data.update(change)
    with pytest.raises(ValueError):
        contract.SelectedElbowExperiment.from_payload(data)


@pytest.mark.parametrize(
    "at,cycle,phase,fraction",
    [
        (0, 0, "baseline", 0),
        (2, 1, "outbound", 0),
        (7, 1, "outbound", 0.5),
        (12, 1, "endpoint", 1),
        (14, 1, "return", 1),
        (24, 1, "origin", 0),
        (26, 1, "intercycle_hold", 0),
        (31.9, 1, "intercycle_hold", 0),
        (32, 2, "outbound", 0),
        (37, 2, "outbound", 0.5),
        (42, 2, "endpoint", 1),
        (44, 2, "return", 1),
        (54, 2, "origin", 0),
        (56, 2, "final", 0),
        (58, 2, "final", 0),
    ],
)
def test_operation_waveform_has_two_cycles_and_additional_measured_hold(at, cycle, phase, fraction):
    d = operation_definition()
    assert d.phase(at) == (cycle, phase, fraction)
    assert d.sample_key(25.8) == "cycle1_origin"
    assert d.sample_key(43) == "cycle2_endpoint"
    assert d.sample_key(57.8) == "cycle2_origin"
    assert d.sample_key(29) is None


def test_operation_preparation_accepts_only_descriptor_bound_duration_and_keeps_fresh_seed():
    d = operation_definition()
    initial = provider().origin
    task = FiniteTask(contract.RECIPES[RECIPE], initial, 0, 3, selected_experiment=d)
    assert task.provider.duration_s == 58 and task.admitted_at == 0
    assert task.provider.origin == initial and task.provider.get_action()["left_shoulder_lift.pos"] == 99.8
    with pytest.raises(ValueError):
        definition(schema_version=1, cycle_count=2, intercycle_hold_s=6)


def provider(d=None):
    initial = seed(norm(2399))
    initial[SHOULDER] = 99.8
    return scripted_leader.LeftElbowPositiveControlInput(
        initial, joint_keys=JOINT_KEYS, fps=10, emit=lambda _: None, definition=d or definition()
    )


def test_descriptor_pin_derives_finite_waveform_and_copies_input():
    d = definition()
    assert (d.recipe_name, d.profile, d.nominal_trajectory_s, d.trajectory_s, d.live_s) == (
        RECIPE,
        PROFILE,
        28,
        30,
        60,
    )
    data = payload()
    expected_hash = hashlib.sha256(
        json.dumps(data, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()
    ).hexdigest()
    assert d.payload == data and d.sha256 == expected_hash
    data["goal_readback_checkpoints_s"].append(6)
    assert d.payload["goal_readback_checkpoints_s"] == [4.0, 5.0]
    assert d.summary()["sha256"] == expected_hash


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": True},
        {"envelope_sha256": "0" * 64},
        {"unknown": 1},
        {"excursion_norm": True},
        {"excursion_norm": 0},
        {"excursion_norm": -10.01},
        {"ramp_s": 9.99},
        {"ramp_s": 11.1},
        {"endpoint_dwell_s": float("nan")},
        {"endpoint_dwell_s": 2.01},
        {"endpoint_dwell_s": 0.1},
        {"current_cap_ma": 100.01},
        {"current_cap_ma": 0},
        {"current_cap_ma": "100"},
        {"live_ceiling_s": 60.01},
        {"live_ceiling_s": 28},
        {"minimum_response_raw": True},
        {"minimum_response_raw": 0},
        {"return_tolerance_raw": -1},
        {"return_tolerance_raw": 22.5},
        {"goal_readback_checkpoints_s": [4, 5, 6]},
        {"goal_readback_checkpoints_s": [5, 4]},
        {"goal_readback_checkpoints_s": [4, 4]},
        {"goal_readback_checkpoints_s": [0]},
        {"goal_readback_checkpoints_s": [28]},
        {"goal_readback_checkpoints_s": [True]},
    ],
)
def test_invalid_descriptor_never_constructs_an_admitted_definition(change):
    cls = getattr(contract, "SelectedElbowExperiment", None)
    assert cls is not None, "typed selected experiment is missing"
    with pytest.raises(ValueError):
        cls.from_payload(payload(**change))


def test_signed_waveform_has_one_frozen_reference_and_context_digest():
    p = provider()
    p.admit(0)
    last = 0
    for at, phase, target in [
        (0, "baseline", norm(2399)),
        (7, "outbound", norm(2399) - 5),
        (12, "endpoint", norm(2399) - 10),
        (19, "return", norm(2399) - 5),
        (24, "origin", norm(2399)),
        (28, "final", norm(2399)),
    ]:
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
    execution = {
        "original_seed": p.origin,
        "progress_s": 28,
        "cycles_completed": 1,
        "returns_qualified": int(coarse),
        "positive_control": o,
    }
    assert p.definition.completion_expectation(execution) == (28, 1, int(coarse))
    wrong = copy.deepcopy(execution)
    wrong["positive_control"]["experiment"]["sha256"] = "0" * 64
    assert p.definition.completion_expectation(wrong) is None


def test_declared_practical_criteria_require_actual_calibration_without_changing_old_defaults():
    p = provider(definition(minimum_response_raw=54))
    feed = selected_feedback(p)
    positions, item = feed.get(0.1, 1)
    with pytest.raises(ValueError, match="criteria"):
        p.collect_feedback(0.1, positions, 1, selected_feedback=item)
    p = provider(definition(return_tolerance_raw=23))
    feed = selected_feedback(p)
    positions, item = feed.get(0.1, 1)
    with pytest.raises(ValueError, match="criteria"):
        p.collect_feedback(0.1, positions, 1, selected_feedback=item)
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
        FiniteTask(
            contract.RECIPES["physical-left-elbow-positive-control"], initial, 0, 3, selected_experiment=d
        )


def test_changed_timing_confirmation_preparation_uses_its_exact_derived_duration():
    d = definition(
        excursion_norm=-6.0,
        ramp_s=6.0,
        endpoint_dwell_s=1.0,
        minimum_response_raw=34,
        return_tolerance_raw=13,
    )
    initial = provider(d).origin
    comparator = {
        "run_id": "12345678-1234-1234-1234-123456789abc",
        "reference_normalized": initial[KEY],
        "original_seed": initial,
    }
    task = FiniteTask(
        contract.RECIPES[RECIPE], initial, 0, 3, selected_experiment=d, positive_comparator=comparator
    )
    assert task.provider.duration_s == 21
    for i in range(1, 31):
        task.advance(i / 10, {"sequence": i, "positions": initial})
        if not task.provider.preparing:
            break
    assert not task.provider.preparing
    assert task.provider._program.definition is d and task.provider._program.duration_s == 21


@pytest.mark.parametrize("receipt_current,accepted", [(78.0, True), (85.0, False)])
def test_selected_sparse_receipt_uses_its_declared_smaller_cap_with_a_quiet_latest_cache(
    receipt_current, accepted
):
    p = provider(definition(current_cap_ma=80.0))
    p.admit(0)
    feed = selected_feedback(p)
    positions, item = feed.get(0.1, 1)
    item["sparse_receipts"]["baseline"]["decoded"]["present_current_ma"] = receipt_current
    if accepted:
        p.advance(0.1, positions, 1, selected_feedback=item)
        assert p.elapsed_s == 0.1
    else:
        with pytest.raises(ValueError, match="80 mA"):
            p.advance(0.1, positions, 1, selected_feedback=item)
        assert p.elapsed_s == 0


def test_qualified_dwell_pin_preserves_historical_payload_and_fixed_waveform():
    old = contract.LEFT_ELBOW_COARSE_BENCH_V1
    new = getattr(contract, "LEFT_ELBOW_COARSE_BENCH_V2", None)
    assert new is not None, "current qualified-dwell definition missing"
    assert old.sha256 == "07480c167c2f94b80565decfe251f586adfc349d9a657124b331b15c2d114ab1"
    assert old.envelope_sha256 == "2d55c9e82d82ea3e66b6bf336853ad84a84cbf2cc3a62ff5e30a7f2f6f55ecef"
    assert new.sha256 != old.sha256 and new.envelope_sha256 != old.envelope_sha256
    for at in [0, 1.9, 2, 11.9, 12, 13.9, 14, 26, 28, 31.9, 32, 41.9, 42, 44, 58]:
        assert old.phase(at) == new.phase(at) and old.sample_key(at) == new.sample_key(at)
    assert new.minimum_response_raw == 56 and new.return_tolerance_raw == 22
    assert new.response_rule["qualified_endpoint_dwell"] is True
    assert "qualified_endpoint_dwell" not in old.response_rule
    assert contract.SelectedElbowExperiment.from_payload(old.payload).sha256 == old.sha256
    assert contract.SelectedElbowExperiment.from_payload(new.payload).sha256 == new.sha256



def negative_v3_payload(**changes):
    definition = getattr(contract, "LEFT_ELBOW_NEGATIVE_COARSE_BENCH_V3", None)
    assert definition is not None, "separately pinned negative hold admission condition missing"
    return dict(definition.payload, **changes)


def test_negative_v3_separates_stable_hold_admission_from_original_return_and_fine_scoring():
    d = contract.SelectedElbowExperiment.from_payload(negative_v3_payload())
    assert d.schema_version == 2 and d.excursion_norm == -10
    assert d.intercycle_hold_tolerance_raw == 40
    assert d.return_tolerance_raw == 22 and d.minimum_response_raw == 56
    assert d.current_cap_ma == 150 and d.nominal_trajectory_s == 58 and d.live_s == 90
    assert d.cycle_count == 2 and d.intercycle_hold_s == 6
    assert d.goal_readback_checkpoints_s == (5, 37)
    assert "qualified_endpoint_dwell" not in d.response_rule
    assert d.response_rule == {"commanded_error_raw":45,"minimum_response_raw":3,"window_s":1.2}
    assert d.startup_hold_error_raw == 30
    assert contract.SelectedElbowExperiment.from_payload(d.payload).sha256 == d.sha256
    assert d.sha256 not in {contract.LEFT_ELBOW_COARSE_BENCH_V1.sha256, contract.LEFT_ELBOW_COARSE_BENCH_V2.sha256}
    for old, digest in [(contract.LEFT_ELBOW_COARSE_BENCH_V1,"07480c167c2f94b80565decfe251f586adfc349d9a657124b331b15c2d114ab1"),
                        (contract.LEFT_ELBOW_COARSE_BENCH_V2,"31336b83330fb5398778b4cfc0397011a3ae063d47efdf9c02e62d685475d473")]:
        assert old.sha256 == digest and "intercycle_hold_tolerance_raw" not in old.payload
        assert contract.SelectedElbowExperiment.from_payload(old.payload).sha256 == digest


@pytest.mark.parametrize("change", [
    {"schema_version":3}, {"excursion_norm":10}, {"ramp_s":9}, {"endpoint_dwell_s":1},
    {"current_cap_ma":149}, {"live_ceiling_s":89}, {"minimum_response_raw":55},
    {"return_tolerance_raw":35}, {"cycle_count":1}, {"intercycle_hold_s":5},
    {"goal_readback_checkpoints_s":[5,38]}, {"intercycle_hold_tolerance_raw":True},
    {"intercycle_hold_tolerance_raw":40.0}, {"intercycle_hold_tolerance_raw":41},
    {"intercycle_hold_spread_raw":3},
])
def test_negative_v3_condition_is_closed_without_altering_old_geometric_envelopes(change):
    with pytest.raises((ValueError, TypeError)):
        contract.SelectedElbowExperiment.from_payload(negative_v3_payload(**change))


def test_hold_admission_field_cannot_be_added_to_an_old_v2_condition():
    for old in (contract.LEFT_ELBOW_COARSE_BENCH_V1, contract.LEFT_ELBOW_COARSE_BENCH_V2):
        with pytest.raises(ValueError):
            contract.SelectedElbowExperiment.from_payload(dict(old.payload, intercycle_hold_tolerance_raw=40))
