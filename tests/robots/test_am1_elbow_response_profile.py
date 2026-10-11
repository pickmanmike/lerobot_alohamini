"""Signed finite response decisions use measured, phase-bound native evidence only."""

import copy
import json
from types import SimpleNamespace

import pytest

from examples.alohamini import am1_session_contract as contract, scripted_leader
from examples.alohamini.am1_finite_task import JOINT_KEYS, FiniteTask
from tests.robots.test_am1_persistent_session import start
from tests.robots.test_am1_physical_authority import admit_live, build, completed_handoff
from tests.robots.test_am1_physical_executor import ControlledHost, client_sample, executor
from tests.robots.test_am1_selected_elbow_profiles import seed
from tools.am1_physical_executor import read_physical_sample

KEY = "arm_left_elbow_flex.pos"
RECIPE = "physical-left-elbow-response-characterize"
PROFILE = "LeftElbowResponseCharacterize"
CALIBRATION = {"drive_mode": 0, "homing_offset": 1845, "range_min": 864, "range_max": 3073}
MOTOR = {
    "bus": "left",
    "name": KEY[:-4],
    "id": 3,
    "model": "sts3215",
    "normalization": "range_m100_100",
    "calibration": CALIBRATION,
}


def raw(value):
    return int(((value + 100) / 200) * 2209 + 864)


def norm(value):
    return (value - 864) / 2209 * 200 - 100


def provider():
    factory = getattr(scripted_leader, "LeftElbowResponseCharacterizeInput", None)
    assert factory is not None, "signed response provider is missing"
    return factory(seed(norm(2399)), joint_keys=JOINT_KEYS, fps=10, emit=lambda _: None)


class Feedback:
    def __init__(self, p, *, negative_moves=True, positive_moves=True):
        self.p, self.receipts = p, {}
        self.negative_moves, self.positive_moves = negative_moves, positive_moves

    def get(self, now, sequence, *, current=13.0):
        context = copy.deepcopy(self.p.diagnostic_context)
        requested = context["requested_target"]
        follows = self.negative_moves if context["cycle"] <= 2 else self.positive_moves
        measured_raw = raw(requested) if follows else 2399
        at = now - 0.005
        action = {
            "requested": requested,
            "relative_limited": requested,
            "current_limited": requested,
            "final_normalized": norm(raw(requested)),
            "expected_goal_raw": raw(requested),
            "sync_write_returned": True,
        }
        identity = {
            "version": 1,
            "run_id": "run",
            "host_incarnation": "host",
            "epoch": 0,
            "intent_revision": 0,
            "action_sequence": sequence,
            "observation_id": sequence,
            "acquired_at_monotonic_s": at,
        }
        item = dict(
            **identity,
            context=context,
            motor=copy.deepcopy(MOTOR),
            action=action,
            position={
                "raw": measured_raw,
                "normalized": norm(measured_raw),
                "read_started_at_monotonic_s": at - 0.001,
                "read_completed_at_monotonic_s": at,
            },
            current={
                "raw": round(current / 6.5),
                "ma": current,
                "read_started_at_monotonic_s": at - 0.001,
                "read_completed_at_monotonic_s": at,
            },
        )
        key = context["sample_key"]
        if key and key not in self.receipts:
            decoded = {
                "goal_position_raw": raw(requested),
                "present_position_raw": measured_raw,
                "present_current_ma": current,
                "present_load_raw": 0,
                "status": 0,
                "torque_enable": 1,
                "operating_mode": 0,
                "voltage_v": 12,
                "temperature_c": 35,
            }
            self.receipts[key] = dict(
                **copy.deepcopy(item),
                sample_key=key,
                diagnostic_available=True,
                goal_matches_expected_raw=True,
                decoded=decoded,
                read_started_at_monotonic_s=at,
                read_completed_at_monotonic_s=at + 0.001,
            )
        item["sparse_receipts"] = copy.deepcopy(self.receipts)
        positions = seed(norm(measured_raw))
        return positions, item


def run(p, *, negative_moves=True, positive_moves=True, until=None, mutate=None):
    feed = Feedback(p, negative_moves=negative_moves, positive_moves=positive_moves)
    p.admit(0)
    steps = round((until or 60) * 10)
    for step in range(1, steps + 1):
        positions, selected = feed.get(step / 10, step)
        if mutate:
            mutate(p, positions, selected)
        p.advance(step / 10, positions, step, selected_feedback=selected)
        if p.complete:
            break
    return feed


def test_explicit_signed_admission_preserves_other_recipes():
    recipe = contract.RECIPES.get(RECIPE)
    assert recipe is not None, "signed physical recipe is missing"
    assert (recipe.trajectory_s, recipe.live_s, recipe.seed) == (60, 180, None)
    assert executor(ControlledHost()).supports(recipe)
    assert not executor(ControlledHost()).supports(SimpleNamespace(name=RECIPE, seed=17, mode="finite"))
    assert contract.RECIPES["physical-left-elbow-compare"].live_s == 60
    assert contract.RECIPES["physical-left-elbow-dwell-compare"].trajectory_s == 24


@pytest.mark.parametrize(
    "negative_moves,duration,cycles,branch",
    [(True, 18, 2, "negative_distinguished"), (False, 60, 4, "positive_control")],
)
def test_local_branch_is_measured_truthful_and_reuses_one_reference(negative_moves, duration, cycles, branch):
    p = provider()
    before = copy.deepcopy(p.origin)
    run(p, negative_moves=negative_moves)
    assert p.complete and p.elapsed_s == duration and p.duration_s == duration
    assert p.origin == before
    outcome = p.characterization
    assert outcome["branch"] == branch and len(outcome["cycles"]) == cycles
    assert outcome["baseline_raw"] == 2399
    assert all(c["return_qualified"] is True for c in outcome["cycles"])
    assert all(v == before[k] for k, v in {"arm_" + k: v for k, v in p.get_action().items()}.items())
    expected = getattr(contract, "LEFT_ELBOW_RESPONSE", None)
    assert expected is not None
    execution = {
        "progress_s": duration,
        "cycles_completed": cycles,
        "returns_qualified": 1,
        "characterization": outcome,
        "original_seed": p.origin,
    }
    assert expected.completion_expectation(execution) == (duration, cycles, 1)
    execution["progress_s"] = 60 if duration == 18 else 18
    assert expected.completion_expectation(execution) is None


def test_signed_ramps_use_real_quantization_one_unit_slope_and_other_joints_hold():
    p = provider()
    feed = Feedback(p, negative_moves=False)
    p.admit(0)
    previous = p.get_action()
    seen = {}
    for step in range(1, 601):
        positions, selected = feed.get(step / 10, step)
        p.advance(step / 10, positions, step, selected_feedback=selected)
        action = p.get_action()
        assert abs(action["left_elbow_flex.pos"] - previous["left_elbow_flex.pos"]) <= 0.100000001
        assert all(action[k.removeprefix("arm_")] == v for k, v in p.origin.items() if k != KEY)
        if p.diagnostic_context["phase"] == "endpoint":
            seen[p.diagnostic_context["cycle"]] = raw(action["left_elbow_flex.pos"])
        previous = action
    assert seen == {1: 2365, 2: 2365, 3: 2509, 4: 2509}
    assert p.characterization["cycles"][0]["response_threshold_raw"] == 17
    assert p.characterization["cycles"][2]["response_threshold_raw"] == 55


@pytest.mark.parametrize("elbow", [-99, 91])
def test_signed_envelope_refuses_truncated_or_clamped_endpoints(elbow):
    cls = getattr(scripted_leader, "LeftElbowResponseCharacterizeInput", None)
    assert cls is not None
    with pytest.raises(ValueError, match="signed.*range"):
        cls(seed(elbow), joint_keys=JOINT_KEYS, fps=10, emit=lambda _: None)


@pytest.mark.parametrize(
    "fault", ["missing", "foreign", "stale", "mode", "goal", "clamp", "nonreturn", "other_joint"]
)
def test_no_repeat_or_expansion_on_missing_proof_or_unqualified_return(fault):
    p = provider()

    def mutate(p, positions, f):
        if p.elapsed_s < 8.8:
            return
        if fault == "missing":
            f["sparse_receipts"].pop("cycle1_origin", None)
        elif fault == "foreign":
            f["sparse_receipts"]["cycle1_origin"]["host_incarnation"] = "foreign"
        elif fault == "stale":
            f["current"]["read_completed_at_monotonic_s"] -= 1
        elif fault == "mode":
            f["sparse_receipts"]["cycle1_origin"]["decoded"]["operating_mode"] = 1
        elif fault == "goal":
            f["sparse_receipts"]["cycle1_origin"]["decoded"]["goal_position_raw"] += 1
        elif fault == "clamp":
            f["action"]["current_limited"] += 0.1
        elif fault == "nonreturn":
            f["position"]["raw"] = 2396
            f["position"]["normalized"] = positions[KEY] = norm(2396)
        elif fault == "other_joint":
            positions[JOINT_KEYS[0]] += 1.01

    with pytest.raises(ValueError):
        run(p, negative_moves=False, until=9, mutate=mutate)
    assert p.elapsed_s < 9 and p.origin[KEY] == norm(2399)


def test_adverse_current_trend_needs_distinct_actual_acquisitions_and_stops_before_repeat():
    p = provider()
    count = 0

    def mutate(p, positions, f):
        nonlocal count
        if p.diagnostic_context["phase"] == "endpoint":
            count += 1
            f["current"].update(raw=2 + count, ma=(2 + count) * 6.5)

    with pytest.raises(ValueError, match="rising current"):
        run(p, negative_moves=False, until=9, mutate=mutate)
    assert p.elapsed_s < 9


def test_freeze_discards_time_and_unqualified_return_window_without_reseeding():
    p = provider()
    run(p, until=3)
    origin, target, progress = copy.deepcopy(p.origin), p.get_action(), p.elapsed_s
    p.freeze()
    p.advance(100, seed(), 31, selected_feedback=None)
    assert p.elapsed_s == progress and p.get_action() == target
    p.admit(101)
    feed = Feedback(p)
    pos, f = feed.get(108, 32)
    p.advance(108, pos, 32, selected_feedback=f)
    assert p.elapsed_s == progress + 0.1 and p.origin == origin


@pytest.mark.parametrize(
    "field", ["host_incarnation", "action_sequence", "observation_id", "epoch", "intent_revision"]
)
def test_bound_read_sample_rejects_foreign_selected_cache(field):
    c = client_sample()
    c.latest_am1_local_feedback["selected_joint"] = {
        "version": 1,
        "run_id": "run",
        "host_incarnation": "host",
        "epoch": 1,
        "intent_revision": 0,
        "action_sequence": 4,
        "observation_id": 5,
        "acquired_at_monotonic_s": 9.98,
    }
    c.latest_am1_local_feedback["selected_joint"][field] = "foreign"
    with pytest.raises(ValueError, match="selected"):
        read_physical_sample(c, "run", "host", 4, clock=lambda: 10)


@pytest.mark.parametrize("negative_moves,duration,cycles", [(True, 18, 2), (False, 60, 4)])
def test_prepared_task_and_core_consume_exact_branch_proof(tmp_path, negative_moves, duration, cycles):
    p = provider()
    run(p, negative_moves=negative_moves)
    clock, e, a = build(tmp_path)
    e.supports = lambda r: r.name == RECIPE
    try:
        assert start(a, RECIPE)["accepted"]
        admit_live(a, e, clock)
        clock.advance(duration + 3.1)
        receipt, _ = completed_handoff(a, e, clock)
        receipt["measured_positions"] = copy.deepcopy(p.origin)
        receipt["execution"].update(
            progress_s=duration,
            cycles_completed=cycles,
            returns_qualified=1,
            characterization=p.characterization,
            original_seed=p.origin,
        )
        a.tick()
        assert a.run["status"] == "finishing" and a.run["recovery_episodes"] == 0
        assert a.run["completion_receipt"]["execution"]["characterization"] == p.characterization
    finally:
        a.close()


@pytest.mark.parametrize("lag", ["one_frame", "future", "old"])
def test_native_acknowledged_context_uses_actual_phase_with_bounded_pipeline_lag(lag):
    p = provider()
    feed = run(p, until=3)
    old_context = copy.deepcopy(p.diagnostic_context)
    positions, f = feed.get(3.1, 31)
    p.advance(3.1, positions, 31, selected_feedback=f)
    positions, f = feed.get(3.2, 32)
    f["context"] = old_context
    f["action"].update(
        requested=old_context["requested_target"],
        relative_limited=old_context["requested_target"],
        current_limited=old_context["requested_target"],
        final_normalized=norm(raw(old_context["requested_target"])),
        expected_goal_raw=raw(old_context["requested_target"]),
    )
    if lag != "one_frame":
        f["context"]["progress_s"] = 4.0 if lag == "future" else 2.0
        _, phase, fraction, delta = contract.LEFT_ELBOW_RESPONSE.phase(f["context"]["progress_s"], False)
        f["context"].update(phase=phase, requested_target=f["context"]["reference"] + fraction * delta)
    if lag == "one_frame":
        p.advance(3.2, positions, 32, selected_feedback=f)
        assert p.elapsed_s == 3.2
    else:
        with pytest.raises(ValueError):
            p.advance(3.2, positions, 32, selected_feedback=f)


@pytest.mark.parametrize("negative_moves,duration,cycles", [(True, 18, 2), (False, 60, 4)])
def test_actual_prepared_task_freezes_seed_and_publishes_dynamic_completion(negative_moves, duration, cycles):
    assert RECIPE in contract.RECIPES
    task = FiniteTask(contract.RECIPES[RECIPE], seed(norm(2399)), 0, 1.5)
    sequence = 0
    while task.provider.preparing:
        sequence += 1
        task.advance(sequence / 10, {"positions": seed(norm(2399)), "sequence": sequence})
    p = task.provider._program
    frozen = copy.deepcopy(task.original_seed)
    feed = Feedback(p, negative_moves=negative_moves)
    while not task.complete:
        sequence += 1
        positions, selected = feed.get(sequence / 10, sequence)
        task.advance(
            sequence / 10, {"positions": positions, "sequence": sequence, "selected_joint": selected}
        )
        assert sequence < 650
    result = task.snapshot()
    assert result["progress_s"] == duration and result["cycles_completed"] == cycles
    assert result["original_seed"] == frozen and result["admissions"] == 1
    assert contract.LEFT_ELBOW_RESPONSE.completion_expectation(result) == (duration, cycles, 1)


@pytest.mark.parametrize(
    "fault",
    [
        "wrong_duration",
        "wrong_cycles",
        "wrong_branch",
        "bool_goal",
        "false_return",
        "adverse",
        "wrong_threshold",
        "wrong_endpoint",
    ],
)
def test_core_refuses_inconsistent_characterization_branch(tmp_path, fault):
    p = provider()
    run(p)
    clock, e, a = build(tmp_path)
    e.supports = lambda r: r.name == RECIPE
    try:
        assert start(a, RECIPE)["accepted"]
        admit_live(a, e, clock)
        clock.advance(21.1)
        receipt, _ = completed_handoff(a, e, clock)
        receipt["measured_positions"] = copy.deepcopy(p.origin)
        execution = receipt["execution"]
        execution.update(
            progress_s=18,
            cycles_completed=2,
            returns_qualified=1,
            characterization=p.characterization,
            original_seed=p.origin,
        )
        outcome = execution["characterization"]
        if fault == "wrong_duration":
            execution["progress_s"] = 60
        elif fault == "wrong_cycles":
            execution["cycles_completed"] = 4
        elif fault == "wrong_branch":
            outcome["branch"] = "positive_control"
        elif fault == "bool_goal":
            outcome["cycles"][0]["endpoint_goal_raw"] = True
        elif fault == "false_return":
            outcome["cycles"][0]["return_qualified"] = False
        elif fault == "adverse":
            outcome["cycles"][0]["adverse"] = True
        elif fault == "wrong_threshold":
            outcome["cycles"][0]["response_threshold_raw"] -= 1
        elif fault == "wrong_endpoint":
            outcome["cycles"][0]["endpoint_goal_raw"] = 2395
            outcome["cycles"][0]["response_threshold_raw"] = 4
        a.tick()
        assert a.run["status"] == "recovering" and "completion_receipt" not in a.run
    finally:
        a.close()


@pytest.mark.parametrize(
    "fault", ["raw_normalized", "encoded_goal", "final_normalized", "bool_mode", "raw_temperature"]
)
def test_actual_encoding_and_typed_register_proof_cannot_be_fabricated(fault):
    p = provider()

    def mutate(p, positions, f):
        if fault == "raw_normalized":
            f["position"]["raw"] += 3
        elif fault == "encoded_goal":
            f["action"]["expected_goal_raw"] += 3
        elif fault == "final_normalized":
            f["action"]["final_normalized"] += 0.1
        elif fault == "bool_mode":
            f["sparse_receipts"]["baseline"]["decoded"]["operating_mode"] = False
        elif fault == "raw_temperature":
            f["sparse_receipts"]["baseline"]["decoded"]["temperature_c"] = None

    with pytest.raises(ValueError):
        run(p, until=2, mutate=mutate)


def test_signed_encoder_threshold_uses_existing_real_bus_quantization():
    from lerobot.motors import Motor, MotorCalibration, MotorNormMode
    from lerobot.motors.feetech import FeetechMotorsBus

    bus = FeetechMotorsBus(
        "unused-response-test",
        {KEY[:-4]: Motor(3, "sts3215", MotorNormMode.RANGE_M100_100)},
        {KEY[:-4]: MotorCalibration(3, 0, 1845, 864, 3073)},
    )
    q0 = bus._normalize({3: 2399})[3]
    actual = bus._unnormalize({3: q0 - 3})[3]
    assert actual == 2365
    assert contract.LEFT_ELBOW_RESPONSE.response_threshold(actual - 2399, 0) == 17
    assert contract.LEFT_ELBOW_RESPONSE.response_threshold(33, 7) == 21
    assert contract.LEFT_ELBOW_RESPONSE.return_limit(7) == 21
    assert contract.LEFT_ELBOW_RESPONSE.response_threshold(1, 0) == 4


def test_early_completion_reports_zero_remaining_trajectory(tmp_path):
    p = provider()
    run(p)
    clock, e, a = build(tmp_path)
    e.supports = lambda r: r.name == RECIPE
    try:
        assert start(a, RECIPE)["accepted"]
        admit_live(a, e, clock)
        clock.advance(21.1)
        receipt, _ = completed_handoff(a, e, clock)
        receipt["measured_positions"] = copy.deepcopy(p.origin)
        receipt["execution"].update(
            progress_s=18,
            cycles_completed=2,
            returns_qualified=1,
            characterization=p.characterization,
            original_seed=p.origin,
        )
        a.tick()
        assert a.snapshot()["run"]["trajectory_remaining_s"] == 0
    finally:
        a.close()


def test_signed_task_reports_its_actual_ten_unit_selected_ceiling():
    task = FiniteTask(contract.RECIPES[RECIPE], seed(norm(2399)), 0, 1.5)
    assert task.snapshot()["joint_amplitudes"][KEY] == 10


def test_signed_launch_uses_selected_evidence_without_shoulder_experiment(monkeypatch, tmp_path):
    from tests.robots.test_am1_selected_elbow_profiles import (
        test_host_launch_env_cannot_inherit_or_enable_other_recipe_experiments,
    )

    test_host_launch_env_cannot_inherit_or_enable_other_recipe_experiments(
        monkeypatch, tmp_path, RECIPE, True, False
    )


@pytest.mark.parametrize("which", ["position", "current"])
def test_acquisition_must_advance_across_phase_boundary(which):
    p = provider()
    feed = run(p, until=2)
    positions, f = feed.get(2.1, 21)
    f[which]["read_started_at_monotonic_s"] = 1.994
    f[which]["read_completed_at_monotonic_s"] = 1.995
    with pytest.raises(ValueError, match="acquisition did not advance"):
        p.advance(2.1, positions, 21, selected_feedback=f)


def test_full_native_reader_uses_processing_clock_and_actual_one_frame_ack_lag():
    task = FiniteTask(contract.RECIPES[RECIPE], seed(norm(2399)), 0, 1.5)
    sequence = 0
    while task.provider.preparing:
        sequence += 1
        task.advance(sequence / 10, {"positions": seed(norm(2399)), "sequence": sequence})
    p = task.provider._program
    feed = Feedback(p)
    previous_context = None
    for _ in range(33):
        sequence += 1
        now = sequence / 10
        positions, selected = feed.get(now, sequence)
        if p.elapsed_s == 3.1:
            selected["context"] = previous_context
            requested = previous_context["requested_target"]
            selected["action"].update(
                requested=requested,
                relative_limited=requested,
                current_limited=requested,
                final_normalized=norm(raw(requested)),
                expected_goal_raw=raw(requested),
            )
        previous_context = copy.deepcopy(p.diagnostic_context)
        client = client_sample(now)
        marker = client.latest_am1_local_feedback
        marker.update(
            state="active",
            observation_id=sequence,
            epoch=0,
            intent_revision=0,
            accepted_action_sequence=sequence,
            read_started_at_monotonic_s=now - 0.04,
            acquired_at_monotonic_s=now - 0.01,
        )
        selected.update(
            epoch=0,
            intent_revision=0,
            action_sequence=sequence,
            observation_id=sequence,
            acquired_at_monotonic_s=now - 0.01,
        )
        selected["position"].update(
            read_started_at_monotonic_s=now - 0.03, read_completed_at_monotonic_s=now - 0.02
        )
        selected["current"].update(
            read_started_at_monotonic_s=now - 0.02, read_completed_at_monotonic_s=now - 0.015
        )
        marker["selected_joint"] = selected
        client.get_observation = lambda positions=positions: positions
        sample = read_physical_sample(client, "run", "host", sequence - 1, clock=lambda now=now: now)
        assert sample["at"] < selected["position"]["read_completed_at_monotonic_s"]
        assert task.advance(now, sample)
    assert p.elapsed_s == 3.3 and task.original_seed == seed(norm(2399))


def test_adverse_episode_is_not_erased_by_freeze_and_resume():
    p = provider()
    count = 0

    def rising(p, positions, f):
        nonlocal count
        if p.diagnostic_context["phase"] == "endpoint":
            count += 1
            f["current"].update(raw=2 + count, ma=(2 + count) * 6.5)

    with pytest.raises(ValueError, match="rising current"):
        run(p, negative_moves=False, until=9, mutate=rising)
    p.freeze()
    p.admit(20)
    positions, f = Feedback(p, negative_moves=False).get(20.1, 300)
    with pytest.raises(ValueError, match="rising current"):
        p.advance(20.1, positions, 300, selected_feedback=f)


@pytest.mark.parametrize(
    "field", ["epoch", "intent_revision", "action_sequence", "observation_id", "phase_action"]
)
def test_sparse_phase_binding_is_literal_and_cannot_qualify_foreign_ack(field):
    p = provider()

    def mutate(p, positions, f):
        if p.elapsed_s < 8.8:
            return
        receipt = f["sparse_receipts"]["cycle1_origin"]
        if field == "phase_action":
            receipt["action"]["requested"] += 0.1
        else:
            receipt[field] += 1000

    with pytest.raises(ValueError):
        run(p, negative_moves=False, until=9, mutate=mutate)


@pytest.mark.parametrize("wall_frame", [0.115, 0.13])
def test_origin_sparse_capture_stays_fresh_with_capped_slow_ticks(wall_frame):
    p = provider()
    feed = Feedback(p, negative_moves=False)
    p.admit(0)
    adjusted = set()
    for step in range(1, 601):
        now = step * wall_frame
        positions, f = feed.get(now, step)
        for key, r in feed.receipts.items():
            if key.endswith("_origin") and key not in adjusted:
                started = now - wall_frame + 0.025
                r.update(
                    read_started_at_monotonic_s=started,
                    read_completed_at_monotonic_s=started + 0.002,
                    acquired_at_monotonic_s=started - 0.005,
                    action_sequence=step - 1,
                    observation_id=step - 1,
                )
                for cache in (r["position"], r["current"]):
                    cache.update(
                        read_started_at_monotonic_s=started - 0.01,
                        read_completed_at_monotonic_s=started - 0.005,
                    )
                adjusted.add(key)
        f["sparse_receipts"] = copy.deepcopy(feed.receipts)
        p.advance(now, positions, step, selected_feedback=f)
    assert p.complete and p.elapsed_s == 60 and len(p.characterization["cycles"]) == 4
    assert len(adjusted) == 4


def test_raw_return_window_uses_position_acquisitions_not_current_span():
    p = provider()

    def mutate(p, positions, f):
        if p.elapsed_s >= 8.5:
            at = 8.595 + (p.elapsed_s - 8.5) * 0.1
            f["position"].update(read_started_at_monotonic_s=at - 0.001, read_completed_at_monotonic_s=at)

    with pytest.raises(ValueError, match="position"):
        run(p, negative_moves=False, until=9, mutate=mutate)


def test_final_raw_drift_inside_old_normalized_band_cannot_complete():
    p = provider()

    def mutate(p, positions, f):
        if p.elapsed_s >= 17.5:
            f["position"]["raw"] = 2410
            f["position"]["normalized"] = positions[KEY] = norm(2410)

    with pytest.raises(ValueError, match="final.*raw"):
        run(p, until=18, mutate=mutate)


def test_genuinely_stale_origin_receipt_refuses_departure_with_fresh_normal_feedback():
    p = provider()
    feed = run(p, until=8.9)
    positions, selected = feed.get(9.6, 90)
    with pytest.raises(ValueError, match="origin sparse.*stale"):
        p.advance(9.6, positions, 90, selected_feedback=selected)
    assert p.elapsed_s == 8.9


def test_final_drift_after_provider_end_refuses_task_completion():
    task = FiniteTask(contract.RECIPES[RECIPE], seed(norm(2399)), 0, 1.5)
    sequence = 0
    while task.provider.preparing:
        sequence += 1
        task.advance(sequence / 10, {"positions": seed(norm(2399)), "sequence": sequence})
    p = task.provider._program
    feed = Feedback(p)
    while not p.complete:
        sequence += 1
        positions, selected = feed.get(sequence / 10, sequence)
        task.advance(
            sequence / 10, {"positions": positions, "sequence": sequence, "selected_joint": selected}
        )
    assert not task.complete
    sequence += 1
    positions, selected = feed.get(sequence / 10, sequence)
    selected["position"].update(raw=2410, normalized=norm(2410))
    positions[KEY] = norm(2410)
    with pytest.raises(ValueError, match="final.*raw"):
        task.advance(
            sequence / 10, {"positions": positions, "sequence": sequence, "selected_joint": selected}
        )
    assert not task.complete


def test_chosen_short_branch_reports_remaining_hold_before_final_qualification(tmp_path):
    p = provider()
    run(p, until=16)
    assert p.characterization["final_return_residual_raw"] is None
    assert contract.LEFT_ELBOW_RESPONSE.branch_expectation(p.characterization) == (18, 2, 1)
    clock, e, a = build(tmp_path)
    e.supports = lambda recipe: recipe.name == RECIPE
    try:
        assert start(a, RECIPE)["accepted"]
        admit_live(a, e, clock)
        a.run["execution"] = {
            "progress_s": 16,
            "characterization": p.characterization,
        }
        a.run["progress_s"] = 16
        assert a.snapshot()["run"]["trajectory_remaining_s"] == 2
        execution = dict(
            a.run["execution"],
            progress_s=18,
            cycles_completed=2,
            returns_qualified=1,
            original_seed=p.origin,
        )
        assert contract.LEFT_ELBOW_RESPONSE.completion_expectation(execution) is None
    finally:
        a.close()


def test_full_sparse_proof_stays_local_and_authority_ack_frame_remains_bounded(monkeypatch, tmp_path):
    from lerobot.robots.alohamini import alohamini_host as host
    from tests.robots.test_alohamini_lift_motor_feedback import status
    from tests.robots.test_am1_selected_joint_evidence import (
        attach_port,
        binding,
        command,
        group_payload,
        observe,
        robot,
        sampler,
    )
    from tests.robots.test_am1_selected_joint_feedback import signed_context

    obj, _, _ = robot(monkeypatch)
    evidence, _ = sampler()
    identity = binding()
    control = host.AM1LocalControl(**identity, selected_evidence=evidence)
    goals = (2000, 1970, 2000, 1970, 2000, 2100, 2000, 2100, 2000)
    port = attach_port(obj, [status(group_payload(goal=goal), motor_id=3) for goal in goals])
    phases = (
        (0, "baseline", 0, 0, -3),
        (5, "endpoint", 1, -3, -3),
        (8.8, "origin", 1, 0, -3),
        (12, "endpoint", 2, -3, -3),
        (15.8, "origin", 2, 0, -3),
        (16, "outbound", 3, 0, 10),
        (26, "endpoint", 3, 10, 10),
        (36.8, "origin", 3, 0, 10),
        (47, "endpoint", 4, 10, 10),
        (57.8, "origin", 4, 0, 10),
        (60, "final", 4, 0, 10),
    )
    for sequence, args in enumerate(phases, 1):
        control.apply(obj, command(identity, signed_context(*args), sequence=sequence))
        observation = observe(obj, control)
        evidence.publish(obj, control, observation)
        evidence.report(obj, control, observation)
    observation = observe(obj, control)
    evidence.publish(obj, control, observation)
    marker = observation[host.AM1_LOCAL_FEEDBACK_KEY]
    assert len(port.writes) == 9
    note = copy.deepcopy(marker["selected_joint"])
    assert len(note["sparse_receipts"]) == 9
    assert all(
        row["trace"]["request_bytes"] and row["trace"]["response_bytes"]
        for row in note["sparse_receipts"].values()
    )
    acquired = marker["acquired_at_monotonic_s"]
    marker["read_started_at_monotonic_s"] = acquired - 0.01
    client = client_sample()
    client.latest_am1_local_feedback = marker
    client.latest_observation_received_at = acquired + 0.005
    client.latest_observation_roundtrip_age_s = 0.02
    client.get_observation = lambda: seed(0)
    sample = read_physical_sample(
        client,
        identity["run_id"],
        identity["host_incarnation"],
        -1,
        clock=lambda: acquired + 0.01,
    )
    assert sample["selected_joint"] == note
    assert "selected_joint" not in sample["ack"]
    clock, e, a = build(tmp_path)
    try:
        assert start(a, "sim-arm-smoke-repeat")["accepted"]
        admit_live(a, e, clock)
        receipt, current_ack = completed_handoff(a, e, clock)
        diagnostic_free_ack = copy.deepcopy(sample["ack"])
        diagnostic_free_ack.update(receipt["accepted_host_action"])
        receipt["accepted_host_action"] = diagnostic_free_ack
        current_ack.update({k: v for k, v in diagnostic_free_ack.items() if k not in current_ack})
        a.tick()
        assert a.run["status"] == "finishing"
        assert a.run["completion_receipt"]["accepted_host_action"] == diagnostic_free_ack
        assert len(json.dumps(a.snapshot(), separators=(",", ":")).encode()) < 65536
        assert sample["selected_joint"]["sparse_receipts"] == note["sparse_receipts"]
    finally:
        a.close()
