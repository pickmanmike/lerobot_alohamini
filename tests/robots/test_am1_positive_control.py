"""The positive control is a distinct I0 waveform, not a precision return gate."""

import copy
from functools import partial

import pytest

from examples.alohamini import am1_session_contract as contract, scripted_leader
from examples.alohamini.am1_finite_task import JOINT_KEYS, FiniteTask
from examples.alohamini.am1_scripted_prepare import PreparedScriptedInput, SHOULDER
from tests.robots.test_am1_elbow_response_profile import CALIBRATION, Feedback, norm, raw
from tests.robots.test_am1_selected_elbow_profiles import seed
from tests.robots.test_am1_physical_executor import ControlledHost, executor

KEY = "arm_left_elbow_flex.pos"
RECIPE = "physical-left-elbow-positive-control"
PROFILE = "LeftElbowPositiveControl"


def definition():
    value = getattr(contract, "LEFT_ELBOW_POSITIVE_CONTROL", None)
    assert value is not None, "positive control shared definition missing"
    return value


def provider(**kwargs):
    factory = getattr(scripted_leader, "LeftElbowPositiveControlInput", None)
    assert factory is not None, "positive control finite provider missing"
    initial = seed(norm(2399))
    initial[SHOULDER] = 99.8
    return factory(initial, joint_keys=JOINT_KEYS, fps=10, emit=lambda _: None, **kwargs)


def test_explicit_recipe_is_not_old_integral_or_historical_return():
    d = definition()
    recipe = contract.RECIPES[RECIPE]
    assert (recipe.trajectory_s, recipe.live_s, recipe.seed) == (30, 60, None)
    assert d.nominal_trajectory_s == 28
    assert RECIPE not in contract.ELBOW_RECOVERY_DEFINITIONS
    assert executor(ControlledHost()).supports(recipe)
    assert contract.RECIPES["physical-left-elbow-response-characterize"].live_s == 180


@pytest.mark.parametrize(
    "progress,phase,fraction",
    [
        (0, "baseline", 0),
        (2, "outbound", 0),
        (7, "outbound", 0.5),
        (12, "endpoint", 1),
        (14, "return", 1),
        (19, "return", 0.5),
        (24, "origin", 0),
        (26, "final", 0),
        (28, "final", 0),
    ],
)
def test_exact_shared_waveform_and_last_final_sparse_attempt(progress, phase, fraction):
    d = definition()
    assert d.phase(progress, 0) == (int(progress >= 2), phase, fraction)
    c = dict(
        profile=PROFILE,
        progress_s=progress,
        phase=phase,
        cycle=int(progress >= 2),
        reference=40.0,
        endpoint_target=50.0,
        requested_target=40 + fraction * 10,
        sample_key=d.sample_key(progress, 0),
        acquisition_s=0.0,
        admitted_reference=40.0,
    )
    assert d.context_valid(c)
    assert d.sample_key(25.8, 0) is None
    assert d.sample_key(27.8, 0) == "cycle1_origin"
    assert not d.context_valid(dict(c, requested_target=c["requested_target"] + 0.001))


@pytest.mark.parametrize("position", [-91, 91])
def test_full_absolute_corridor_refuses_without_clipping(position):
    cls = getattr(scripted_leader, "LeftElbowPositiveControlInput", None)
    assert cls is not None
    with pytest.raises(ValueError, match="corridor"):
        cls(seed(position), joint_keys=JOINT_KEYS, fps=10, emit=lambda _: None)


def test_measured_preparation_keeps_current_99_8_shoulder_and_all_other_targets():
    p = provider()
    prep = PreparedScriptedInput(
        p.origin,
        joint_keys=JOINT_KEYS,
        fps=10,
        provider=partial(type(p)),
        motion_profile=PROFILE,
        emit=lambda _: None,
    )
    prep.admit(0)
    for i in range(1, 31):
        assert prep.get_action()["left_shoulder_lift.pos"] == 99.8
        prep.advance(i / 10, p.origin, i)
        if not prep.preparing:
            break
    assert not prep.preparing and prep.origin == p.origin


def test_confirmation_acquisition_never_rebases_original_reference():
    d = definition()
    p = provider(
        positive_comparator={
            "run_id": "12345678-1234-1234-1234-123456789abc",
            "reference_normalized": norm(2410),
            "calibration": CALIBRATION,
            "original_seed": provider().origin,
        }
    )
    assert p.duration_s == 30 and p.diagnostic_context["phase"] == "acquire"
    assert p.diagnostic_context["reference"] == norm(2410)
    assert p.get_action()["left_elbow_flex.pos"] == norm(2399)
    assert d.context_valid(p.diagnostic_context)
    with pytest.raises(ValueError, match="acquisition"):
        provider(
            positive_comparator={
                "run_id": "12345678-1234-1234-1234-123456789abc",
                "reference_normalized": norm(2450),
                "calibration": CALIBRATION,
                "original_seed": provider().origin,
            }
        )


def test_visible_finite_task_routes_new_profile_without_old_reference():
    definition()
    task = FiniteTask(contract.RECIPES[RECIPE], provider().origin, 0, 3)
    assert task.profile == PROFILE and task.snapshot()["positive_control"]["program_completed"] is False
    with pytest.raises(ValueError, match="retained"):
        FiniteTask(contract.RECIPES[RECIPE], provider().origin, 0, 3, return_reference={})


def settings_proof():
    import hashlib
    import json

    registers = dict(
        Firmware_Major_Version=3,
        Firmware_Minor_Version=10,
        Model_Number=777,
        ID=3,
        Return_Delay_Time=0,
        Min_Position_Limit=864,
        Max_Position_Limit=3073,
        Homing_Offset=1845,
        Phase=0,
        P_Coefficient=16,
        I_Coefficient=0,
        D_Coefficient=32,
        Operating_Mode=0,
        Acceleration=254,
        Goal_Time=0,
        Goal_Velocity=0,
        Torque_Limit=1000,
        Maximum_Acceleration=254,
    )
    material = dict(registers=registers, individual_bytes={"24": 0, "25": 0})
    before = dict(
        registers=dict(
            registers,
            Torque_Enable=0,
            Lock=0,
            Goal_Position=2399,
            Present_Position=2399,
            Present_Current=0,
            Status=0,
            Present_Voltage=120,
            Present_Temperature=35,
        ),
        individual_bytes={"24": 0, "25": 0},
        read_started_at_monotonic_s=0.0,
        read_completed_at_monotonic_s=0.001,
    )
    active = copy.deepcopy(before)
    active["registers"].update(Torque_Enable=1, Lock=1, Present_Current=2)
    active.update(read_started_at_monotonic_s=0.002, read_completed_at_monotonic_s=0.003)
    return dict(
        version=1,
        binding=dict(run_id="run", host_incarnation="host", recipe=RECIPE),
        before=before,
        active=active,
        unchanged=True,
        settings_sha256=hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    )


class PositiveFeedback(Feedback):
    def __init__(self, p, *, moves=True, final_offset=0):
        super().__init__(p, negative_moves=moves, positive_moves=moves)
        self.settings = settings_proof()
        self.final_offset = final_offset

    def get(self, now, sequence, *, current=13.0):
        positions, item = super().get(now, sequence, current=current)
        phase = item["context"]["phase"]
        if phase == "final" and self.final_offset:
            item["position"].update(raw=2399 + self.final_offset, normalized=norm(2399 + self.final_offset))
            for key, value in item["sparse_receipts"].items():
                if key == "cycle1_origin":
                    value["decoded"]["present_position_raw"] = 2399 + self.final_offset
                    self.receipts[key] = copy.deepcopy(value)
        positions = dict(self.p.origin, **{KEY: item["position"]["normalized"]})
        item["visible_control"] = copy.deepcopy(self.settings)
        return positions, item


def run_positive(p, *, moves=True, final_offset=0):
    feed = PositiveFeedback(p, moves=moves, final_offset=final_offset)
    p.admit(0)
    previous = p.get_action()
    for i in range(1, 901):
        now = i / 30
        positions, item = feed.get(now, i)
        p.collect_feedback(now, positions, i, selected_feedback=item)
        if i % 3 == 0:
            p.advance(now, positions, i, selected_feedback=item)
            action = p.get_action()
            assert abs(action["left_elbow_flex.pos"] - previous["left_elbow_flex.pos"]) <= 0.100000001
            assert all(action[k.removeprefix("arm_")] == v for k, v in p.origin.items() if k != KEY)
            previous = action
        if p.complete:
            break
    assert p.complete
    return feed


@pytest.mark.parametrize(
    "moves,offset,coarse,fine", [(True, 0, True, True), (False, 0, False, True), (True, 11, True, False)]
)
def test_program_completion_is_separate_from_coarse_response_and_fine_accuracy(moves, offset, coarse, fine):
    p = provider()
    run_positive(p, moves=moves, final_offset=offset)
    o = p.positive_control
    assert p.elapsed_s == 28 and o["program_completed"] is True
    assert o["coarse_functional_pass"] is coarse and o["fine_return_pass"] is fine
    assert o["encoded_excursion_raw"] == raw(norm(2399) + 10) - 2399
    execution = dict(
        original_seed=p.origin,
        progress_s=28,
        cycles_completed=1,
        returns_qualified=int(coarse),
        positive_control=o,
    )
    assert definition().completion_expectation(execution) == (28, 1, int(coarse))
    wrong = copy.deepcopy(execution)
    wrong["positive_control"]["coarse_functional_pass"] = not coarse
    assert definition().completion_expectation(wrong) is None


@pytest.mark.parametrize("change", ["pid", "clock", "identity", "current"])
def test_actual_i0_proof_and_current_cap_refuse_before_progress(change):
    p = provider()
    p.admit(0)
    feed = PositiveFeedback(p)
    positions, item = feed.get(0.1, 1)
    if change == "pid":
        item["visible_control"]["active"]["registers"]["I_Coefficient"] = 1
    if change == "clock":
        item["position"]["read_started_at_monotonic_s"] = -1
    if change == "identity":
        item["visible_control"]["binding"]["host_incarnation"] = "foreign"
    if change == "current":
        item["current"]["ma"] = 100
    with pytest.raises(ValueError):
        p.advance(0.1, positions, 1, selected_feedback=item)
    assert p.elapsed_s == 0


def test_actual_freshness_and_declines_do_not_become_sustained_rise():
    p = provider()
    p.admit(0)
    feed = PositiveFeedback(p)
    for i in range(1, 121):
        positions, item = feed.get(i / 10, i)
        p.advance(i / 10, positions, i, selected_feedback=item)
    for j, current in enumerate([0, 6.5, 0, 13, 0, 19.5], 121):
        positions, item = feed.get(j / 10, j, current=current)
        p.advance(j / 10, positions, j, selected_feedback=item)
    assert not p.complete and p._adverse_reason is None


def test_missing_final_phase_proof_refuses_without_fabricating_completion():
    p = provider()
    p.admit(0)
    feed = PositiveFeedback(p)
    with pytest.raises(ValueError, match="sparse"):
        for i in range(1, 281):
            positions, item = feed.get(i / 10, i)
            item["sparse_receipts"].pop("cycle1_origin", None)
            p.advance(i / 10, positions, i, selected_feedback=item)
    assert not p.complete and p.positive_control["program_completed"] is False


def native_positive_fixture(*, moves=True, offset=0):
    from tests.robots.test_am1_physical_executor import terminal_cleanup_outcome

    p = provider()
    run_positive(p, moves=moves, final_offset=offset)
    run_id = "12345678-1234-1234-1234-123456789abc"
    host = "12345678-1234-1234-1234-123456789abd"
    o = p.positive_control
    o["visible_control"]["binding"].update(run_id=run_id, host_incarnation=host)
    for row in list(o["phase_receipts"].values()) + [
        o[k]
        for k in (
            "measured_origin_proof",
            "baseline_anchor_proof",
            "endpoint_anchor_proof",
            "endpoint_peak_proof",
            "first_return_proof",
        )
    ]:
        row.update(run_id=run_id, host_incarnation=host)
    execution = dict(
        profile=PROFILE,
        original_seed=p.origin,
        initial_measured_reference=p.origin,
        joint_amplitudes=dict.fromkeys(JOINT_KEYS, 1),
        progress_s=28,
        cycles_completed=1,
        returns_qualified=int(o["coarse_functional_pass"]),
        positive_control=o,
        feedback_sequence=840,
        admissions=1,
        complete=True,
        preparing=False,
    )
    receipt = dict(
        run_id=run_id,
        host_incarnation=host,
        recipe=RECIPE,
        execution=copy.deepcopy(execution),
        completed_at_monotonic_s=28.0,
        feedback_at_monotonic_s=27.99,
        feedback_sequence=840,
        intent_revision=0,
        required_observation=True,
        measured_positions=p.origin,
        accepted_host_action=dict(
            run_id=run_id,
            host_incarnation=host,
            intent_revision=0,
            epoch=0,
            state="active",
            accepted_action_sequence=840,
        ),
    )
    lifecycle = terminal_cleanup_outcome(run_id, host, "completed")
    lifecycle.update(run_id=run_id, phase="terminal", first_cause=None)
    lifecycle["readback"]["first_cause"] = None
    previous = dict(
        run_id=run_id,
        recipe=RECIPE,
        source="protected-physical-provider",
        status="completed",
        uncertain=False,
        cleanup="physical_readback_verified",
        first_cause=None,
        backend_incarnation=host,
        physical_result=lifecycle,
        execution=execution,
        completion_receipt=receipt,
        backend_contract={"host_head": "a" * 40},
    )
    return previous


@pytest.mark.parametrize("moves,offset", [(True, 0), (False, 0), (True, 11)])
def test_core_consumes_truthful_full_positive_completion_before_hold_recovery(tmp_path, moves, offset):
    from tests.robots.test_am1_persistent_session import start
    from tests.robots.test_am1_physical_authority import build, admit_live, completed_handoff

    clock, ex, authority = build(tmp_path)
    ex.supports = lambda recipe: recipe.name == RECIPE
    try:
        assert start(authority, RECIPE)["accepted"]
        admit_live(authority, ex, clock)
        clock.advance(28.1)
        receipt, current = completed_handoff(authority, ex, clock)
        native = native_positive_fixture(moves=moves, offset=offset)
        receipt["execution"] = native["execution"]
        receipt["execution"]["feedback_sequence"] = 840
        receipt["feedback_sequence"] = 840
        receipt["accepted_host_action"].update(epoch=0, accepted_action_sequence=840)
        current.update(accepted_action_sequence=841)
        old_evidence = ex.evidence
        ex.evidence = lambda: dict(old_evidence(), feedback_sequence=842)
        receipt["measured_positions"] = native["execution"]["original_seed"]
        binding = receipt["execution"]["positive_control"]["visible_control"]["binding"]
        binding.update(run_id=authority.run["run_id"], host_incarnation=ex.backend_incarnation)
        all_rows = list(receipt["execution"]["positive_control"]["phase_receipts"].values()) + [
            receipt["execution"]["positive_control"]["measured_origin_proof"],
            receipt["execution"]["positive_control"]["baseline_anchor_proof"],
            receipt["execution"]["positive_control"]["endpoint_anchor_proof"],
            receipt["execution"]["positive_control"]["endpoint_peak_proof"],
            receipt["execution"]["positive_control"]["first_return_proof"],
        ]
        for row in all_rows:
            row.update(run_id=authority.run["run_id"], host_incarnation=ex.backend_incarnation)
        assert authority._physical_completion(ex.evidence(), clock()) is not None
    finally:
        authority.close()


def test_confirmation_prerequisite_is_exact_primary_native_not_static_or_repeat():
    from tools.am1_session_core import SessionAuthority

    authority = object.__new__(SessionAuthority)
    previous = native_positive_fixture()
    authority.run = previous
    evidence = dict(
        lifecycle=previous["physical_result"], backend_contract=previous["backend_contract"], fault=None
    )
    method = getattr(authority, "positive_control_prerequisite", None)
    assert callable(method), "positive fixed-reference native prerequisite missing"
    proof = method(previous["run_id"], evidence)
    assert proof["reference_normalized"] == previous["execution"]["original_seed"][KEY]
    assert proof["calibration"] == CALIBRATION
    for change in ("coarse", "camera", "bus", "foreign", "repeat"):
        old = copy.deepcopy(previous)
        ev = copy.deepcopy(evidence)
        if change == "coarse":
            old["execution"]["positive_control"]["coarse_functional_pass"] = False
        if change == "camera":
            old["physical_result"]["camera_release"]["runtime_failed"] = True
        if change == "bus":
            old["physical_result"]["readback"]["readback"]["buses_closed"] = False
        if change == "foreign":
            ev["backend_contract"]["host_head"] = "b" * 40
        if change == "repeat":
            old["execution"]["positive_control"]["confirmation_of"] = old["run_id"]
        authority.run = old
        ev["lifecycle"] = old["physical_result"]
        assert method(old["run_id"], ev) is None


def finite_task_fixture(*, moves=True, positive_comparator=None):
    task = FiniteTask(
        contract.RECIPES[RECIPE], provider().origin, 0, 3, positive_comparator=positive_comparator
    )
    feed = None
    handoff_seen = False
    for i in range(1, 1501):
        now = i / 30
        if task.provider.preparing:
            sample = dict(sequence=i, positions=dict(task.initial_measured_reference))
        elif not handoff_seen:
            sample = dict(sequence=i, positions=dict(task.initial_measured_reference), selected_joint=None)
            handoff_seen = True
        else:
            if feed is None:
                feed = PositiveFeedback(task.provider._program, moves=moves)
            positions, item = feed.get(now, i)
            sample = dict(sequence=i, positions=positions, selected_joint=item)
        task.advance(now, sample)
        if task.complete:
            break
    assert task.complete
    return task


@pytest.mark.parametrize("moves", [True, False])
def test_full_preparation_native_polling_and_completion_without_extra_accuracy_hold(moves):
    task = finite_task_fixture(moves=moves)
    execution = task.snapshot()
    assert (
        execution["progress_s"] == 28 and execution["admissions"] == 1 and execution["cycles_completed"] == 1
    )
    assert execution["returns_qualified"] == int(moves)
    assert execution["positive_control"]["coarse_functional_pass"] is moves
    assert execution["original_seed"][SHOULDER] == 99.8
    assert definition().completion_expectation(execution) == (28, 1, int(moves))


def test_prepared_new_provider_receives_selected_proof_on_its_first_post_seed_action():
    p = provider()
    prep = PreparedScriptedInput(
        p.origin, joint_keys=JOINT_KEYS, fps=10, provider=type(p), motion_profile=PROFILE, emit=lambda _: None
    )
    prep.admit(0)
    for i in range(1, 31):
        prep.advance(i / 10, p.origin, i)
        if not prep.preparing:
            break
    feed = PositiveFeedback(prep._program)
    positions, item = feed.get((i + 1) / 10, i + 1)
    prep.advance((i + 1) / 10, positions, i + 1, selected_feedback=item)
    assert prep.elapsed_s == 0.1


@pytest.mark.parametrize("change", ["clock", "coverage", "goal", "false_score", "only_elbow", "uuid"])
def test_full_shared_completion_refuses_malformed_or_incomplete_native_proof(change):
    execution = native_positive_fixture()["execution"]
    o = execution["positive_control"]
    if change == "clock":
        o["final_window"]["last_position_started_at"] = -1
    if change == "coverage":
        o["phase_coverage"].pop("return")
    if change == "goal":
        o["phase_receipts"]["cycle1_origin"]["decoded"]["goal_position_raw"] += 1
    if change == "false_score":
        o["reverse_response_raw"] = 0
    if change == "only_elbow":
        execution["original_seed"] = {KEY: o["reference_normalized"]}
    if change == "uuid":
        o.update(acquisition_s=2, confirmation_of="bad")
        execution["progress_s"] = 30
    assert definition().completion_expectation(execution) is None


def test_freeze_preserves_endpoint_evidence_reference_and_cannot_catch_up():
    p = provider()
    p.admit(0)
    feed = PositiveFeedback(p)
    for i in range(1, 151):
        positions, item = feed.get(i / 10, i)
        p.advance(i / 10, positions, i, selected_feedback=item)
    before = copy.deepcopy(p.origin)
    endpoint = copy.deepcopy(p._phase_samples[(1, "endpoint")])
    old = p.elapsed_s
    p.freeze()
    assert p._phase_samples[(1, "endpoint")] == endpoint
    p.admit(20)
    positions, item = feed.get(21, 151)
    p.advance(21, positions, 151, selected_feedback=item)
    assert p.elapsed_s == old + 0.1 and p.origin == before


def test_declared_primary_trajectory_is_28_before_motion_and_confirmation_is_30(tmp_path):
    from tests.robots.test_am1_persistent_session import start
    from tests.robots.test_am1_physical_authority import build

    clock, ex, authority = build(tmp_path)
    ex.supports = lambda r: r.name == RECIPE
    try:
        assert start(authority, RECIPE)["accepted"]
        assert authority.snapshot()["run"]["trajectory_remaining_s"] == 28
        authority.run["confirmation_of"] = "12345678-1234-1234-1234-123456789abc"
        assert authority.snapshot()["run"]["trajectory_remaining_s"] == 30
    finally:
        authority.close()


def test_one_explicit_confirmation_start_uses_native_fixed_reference_and_operation_dedup(tmp_path):
    import uuid
    from tests.robots.test_am1_persistent_session import command
    from tests.robots.test_am1_physical_authority import build

    clock, ex, authority = build(tmp_path)
    ex.supports = lambda r: r.name == RECIPE
    ex.source = "protected-physical-provider"
    prior = native_positive_fixture()
    authority.run = copy.deepcopy(prior)
    ev = dict(
        ex.evidence(),
        lifecycle=prior["physical_result"],
        backend_contract=prior["backend_contract"],
        fault=None,
    )
    ex.evidence = lambda: copy.deepcopy(ev)
    received = []
    ex.set_positive_prerequisite = lambda proof: received.append(copy.deepcopy(proof))
    try:
        claim = authority.handle(command("claim"), "owner")
        request = command(
            "start",
            recipe=RECIPE,
            confirmation_of=prior["run_id"],
            operation_id=str(uuid.uuid4()),
            controller_generation=claim["controller_generation"],
        )
        result = authority.handle(request, "owner")
        assert result["accepted"] and not result["effect_admitted"]
        assert authority.run["confirmation_of"] == prior["run_id"] and authority.run["status"] == "starting"
        assert received[0]["original_seed"] == prior["execution"]["original_seed"]
        assert authority.handle(request, "owner")["run_id"] == result["run_id"]
        assert ex.started == 1
    finally:
        authority.close()


def test_confirmation_fresh_other_joint_difference_is_recorded_or_refused_without_moving_it():
    primary = native_positive_fixture()
    comparison = dict(
        run_id=primary["run_id"],
        reference_normalized=primary["execution"]["original_seed"][KEY],
        calibration=CALIBRATION,
        original_seed=primary["execution"]["original_seed"],
        measured_origin_raw=primary["execution"]["positive_control"]["measured_origin_raw"],
        measured_origin_proof=primary["execution"]["positive_control"]["measured_origin_proof"],
    )
    initial = provider().origin
    initial["arm_right_wrist_roll.pos"] += 0.5
    p = type(provider())(
        initial, joint_keys=JOINT_KEYS, fps=10, emit=lambda _: None, positive_comparator=comparison
    )
    assert p.get_action()["right_wrist_roll.pos"] == initial["arm_right_wrist_roll.pos"]
    initial["arm_right_wrist_roll.pos"] += 1
    with pytest.raises(ValueError, match="held-joint"):
        type(p)(initial, joint_keys=JOINT_KEYS, fps=10, emit=lambda _: None, positive_comparator=comparison)


def test_full_confirmation_program_stays_fixed_original_reference_and_finishes_30():
    primary = native_positive_fixture()
    comparison = dict(
        run_id=primary["run_id"],
        reference_normalized=primary["execution"]["original_seed"][KEY],
        calibration=CALIBRATION,
        original_seed=primary["execution"]["original_seed"],
        measured_origin_raw=primary["execution"]["positive_control"]["measured_origin_raw"],
        measured_origin_proof=primary["execution"]["positive_control"]["measured_origin_proof"],
    )
    task = finite_task_fixture(positive_comparator=comparison)
    o = task.snapshot()["positive_control"]
    assert task.snapshot()["progress_s"] == 30 and o["acquisition_s"] == 2
    assert o["comparison_reference"] == comparison["original_seed"]
    assert o["reference_normalized"] == comparison["reference_normalized"]
    assert definition().completion_expectation(task.snapshot()) == (30, 1, 1)


def test_actual_sparse_current_sample_above_cap_refuses_even_when_latest_cache_is_quiet():
    p = provider()
    p.admit(0)
    feed = PositiveFeedback(p)
    positions, item = feed.get(0.1, 1)
    item["sparse_receipts"]["baseline"]["decoded"]["present_current_ma"] = 100
    with pytest.raises(ValueError, match="100 mA"):
        p.advance(0.1, positions, 1, selected_feedback=item)
    assert p.elapsed_s == 0


def test_sustained_endpoint_current_rise_and_wrong_way_position_remain_faults():
    p = provider()
    p.admit(0)
    feed = PositiveFeedback(p)
    for i in range(1, 121):
        positions, item = feed.get(i / 10, i)
        p.advance(i / 10, positions, i, selected_feedback=item)
    with pytest.raises(ValueError, match="rising current"):
        for i, current in enumerate((0, 6.5, 13, 19.5), 121):
            positions, item = feed.get(i / 10, i, current=current)
            p.advance(i / 10, positions, i, selected_feedback=item)
    assert not p.complete
    p = provider()
    p.admit(0)
    feed = PositiveFeedback(p)
    for i in range(1, 22):
        positions, item = feed.get(i / 10, i)
        p.advance(i / 10, positions, i, selected_feedback=item)
    positions, item = feed.get(2.2, 22)
    positions[KEY] = norm(2395)
    item["position"].update(raw=2395, normalized=norm(2395))
    with pytest.raises(ValueError, match="wrong outward"):
        p.advance(2.2, positions, 22, selected_feedback=item)


def test_completed_program_reack_collects_one_fresh_native_frame_once_and_refreshes_final_window():
    task = finite_task_fixture()
    p = task.provider._program
    old = copy.deepcopy(task.snapshot())
    now = task.last_frame + 0.1
    sequence = task.sequence + 3
    feed = PositiveFeedback(p)
    feed.receipts = copy.deepcopy(p._sparse)
    feed.settings = copy.deepcopy(p._visible_settings)
    positions, item = feed.get(now, sequence)
    assert task.advance(now, dict(sequence=sequence, positions=positions, selected_joint=item))
    outcome = task.snapshot()["positive_control"]
    assert outcome["final_window"]["qualified_at_monotonic_s"] == now
    assert (
        outcome["final_window"]["last_position_completed_at"]
        > old["positive_control"]["final_window"]["last_position_completed_at"]
    )
    assert task.snapshot()["progress_s"] == 28 and task.snapshot()["admissions"] == 1
    assert definition().completion_expectation(task.snapshot()) == (28, 1, 1)


@pytest.mark.parametrize(
    "noise,discriminating", [(0, True), (21, True), (22, False), (23, False), (30, False), (55, False)]
)
def test_noise_cannot_qualify_beyond_either_fixed_coarse_limit(noise, discriminating):
    native = native_positive_fixture()
    execution = native["execution"]
    o = execution["positive_control"]
    assert o["encoded_excursion_raw"] == 110
    o["baseline_noise_raw"] = noise
    o["noise_discriminating"] = discriminating
    o["coarse_functional_pass"] = discriminating
    execution["returns_qualified"] = int(discriminating)
    assert definition().completion_expectation(execution) == (28, 1, int(discriminating))
    p = provider()
    run_positive(p)
    p._noise = noise
    p._qualify_positive_final(p._last_tick)
    assert p.positive_control["noise_discriminating"] is discriminating
    assert p.positive_control["coarse_functional_pass"] is discriminating


class MeasuredPhaseFeedback(PositiveFeedback):
    def __init__(self, p, initial_raw, baseline_raw, endpoint_raw, final_raw):
        super().__init__(p)
        self.initial_raw, self.baseline_raw, self.endpoint_raw, self.final_raw = (
            initial_raw,
            baseline_raw,
            endpoint_raw,
            final_raw,
        )
        for item in (self.settings["before"], self.settings["active"]):
            item["registers"].update(Goal_Position=initial_raw, Present_Position=initial_raw)

    def get(self, now, sequence, *, current=13.0):
        positions, item = super().get(now, sequence, current=current)
        c = item["context"]
        t = c["progress_s"] - c["acquisition_s"]
        phase = c["phase"]
        if phase == "baseline":
            measured = self.initial_raw + round((self.baseline_raw - self.initial_raw) * min(t / 1.9, 1))
        elif phase == "outbound":
            measured = self.baseline_raw + round((self.endpoint_raw - self.baseline_raw) * (t - 2) / 10)
        elif phase == "endpoint":
            measured = self.endpoint_raw
        elif phase == "return":
            measured = self.final_raw + round((self.endpoint_raw - self.final_raw) * (1 - (t - 14) / 10))
        else:
            measured = self.final_raw
        item["position"].update(raw=measured, normalized=norm(measured))
        positions[KEY] = norm(measured)
        for key, row in item["sparse_receipts"].items():
            if row["observation_id"] == sequence:
                row["position"].update(raw=measured, normalized=norm(measured))
                row["decoded"]["present_position_raw"] = measured
                self.receipts[key] = copy.deepcopy(row)
        return positions, item


def measured_control(initial, latebaseline, endpoint, final):
    p = type(provider())(seed(norm(initial)), joint_keys=JOINT_KEYS, fps=10, emit=lambda _: None)
    feed = MeasuredPhaseFeedback(p, initial, latebaseline, endpoint, final)
    p.admit(0)
    for i in range(1, 841):
        now = i / 30
        positions, item = feed.get(now, i)
        p.collect_feedback(now, positions, i, selected_feedback=item)
        if i % 3 == 0:
            p.advance(now, positions, i, selected_feedback=item)
    assert p.complete
    return p


def test_actual_codec_rounding_does_not_inflate_measured_movement():
    from lerobot.motors import Motor, MotorCalibration, MotorNormMode
    from lerobot.motors.feetech import FeetechMotorsBus

    bus = FeetechMotorsBus(
        "__fixture",
        {KEY[:-4]: Motor(3, "sts3215", MotorNormMode.RANGE_M100_100)},
        {KEY[:-4]: MotorCalibration(3, 0, 1845, 864, 3073)},
    )
    assert bus._unnormalize({3: norm(1224)})[3] == 1223
    assert bus._unnormalize({3: norm(1224) + 10})[3] == 1334
    p = measured_control(1224, 1224, 1279, 1223)
    o = p.positive_control
    assert o["outward_response_raw"] == 55
    assert o["reverse_response_raw"] == 56 and o["final_return_residual_raw"] == 1
    assert o["reference_raw"] == 1223 and o["endpoint_raw"] == 1334 and o["encoded_excursion_raw"] == 111
    assert o["measured_origin_raw"] == 1224 and o["outward_anchor_raw"] == 1224
    assert o["encoded_goal_residual_raw"] == 0 and o["coarse_functional_pass"] is False
    execution = dict(
        original_seed=p.origin, progress_s=28, cycles_completed=1, returns_qualified=0, positive_control=o
    )
    assert definition().completion_expectation(execution) == (28, 1, 0)


def test_actual_late_baseline_is_movement_anchor_without_command_reseed():
    p = measured_control(2458, 2468, 2513, 2458)
    o = p.positive_control
    assert o["outward_response_raw"] == 45
    assert o["reference_raw"] == 2458 and o["measured_origin_raw"] == 2458
    assert o["outward_anchor_raw"] == 2468 and o["reverse_response_raw"] == 55
    assert o["baseline_noise_raw"] == 10 and o["coarse_functional_pass"] is False
    assert p.origin[KEY] == norm(2458) and p.targets[KEY] == norm(2458) + 10


@pytest.mark.parametrize("final,residual,encoded_residual,fine", [(1224, 0, 1, True), (1221, 3, 2, False)])
def test_original_measured_origin_and_encoded_goal_residual_are_distinct(
    final, residual, encoded_residual, fine
):
    p = measured_control(1224, 1224, 1280, final)
    o = p.positive_control
    assert o["final_return_residual_raw"] == residual
    assert o["encoded_goal_residual_raw"] == encoded_residual and o["fine_return_pass"] is fine
    execution = dict(
        original_seed=p.origin, progress_s=28, cycles_completed=1, returns_qualified=1, positive_control=o
    )
    assert definition().completion_expectation(execution) == (28, 1, 1)


@pytest.mark.parametrize(
    "change", ["original_raw", "late_anchor", "late_clock", "first_id", "normalized_rebase"]
)
def test_actual_reference_and_phase_anchor_proof_cannot_be_relabelled(change):
    execution = native_positive_fixture()["execution"]
    o = execution["positive_control"]
    if change == "original_raw":
        o["measured_origin_raw"] += 1
    if change == "late_anchor":
        o["outward_anchor_raw"] += 1
    if change == "late_clock":
        o["baseline_window"]["last_position_completed_at"] += 0.001
    if change == "first_id":
        o["measured_origin_proof"]["run_id"] = "foreign"
    if change == "normalized_rebase":
        o["reference_normalized"] += 1
    assert definition().completion_expectation(execution) is None


def test_first_actual_return_refuses_stale_endpoint_after_pause_without_extending_hold():
    p = provider()
    p.admit(0)
    feed = PositiveFeedback(p)
    for i in range(1, 141):
        positions, item = feed.get(i / 10, i)
        p.advance(i / 10, positions, i, selected_feedback=item)
    assert p.elapsed_s == 14
    p.freeze()
    p.admit(20)
    positions, item = feed.get(20.1, 141)
    with pytest.raises(ValueError, match="endpoint.*stale"):
        p.advance(20.1, positions, 141, selected_feedback=item)
    assert p.elapsed_s == 14 and not p.complete


@pytest.mark.parametrize("change", ["stale_start", "foreign_return", "relabelled_anchor"])
def test_first_return_endpoint_proof_cannot_be_relabelled(change):
    execution = native_positive_fixture()["execution"]
    o = execution["positive_control"]
    assert definition().completion_expectation(execution) == (28, 1, 1)
    if change == "stale_start":
        o["endpoint_window"]["first_position_started_at"] = (
            o["endpoint_window"]["qualified_at_monotonic_s"] - 0.501
        )
    elif change == "foreign_return":
        o["first_return_proof"]["host_incarnation"] = "foreign"
    else:
        o["reverse_anchor_raw"] += 1
    assert definition().completion_expectation(execution) is None
