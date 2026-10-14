"""Current-pose recovery is distinct from the failed historical return."""

import copy
from dataclasses import FrozenInstanceError

import pytest

from examples.alohamini import am1_session_contract as contract, scripted_leader
from examples.alohamini.am1_finite_task import JOINT_KEYS, FiniteTask
from tests.robots.test_am1_elbow_original_return import artifact
from tests.robots.test_am1_elbow_response_profile import Feedback, norm, raw
from tests.robots.test_am1_selected_elbow_profiles import seed

KEY = "arm_left_elbow_flex.pos"
BASE = "physical-left-elbow-recovery-"


def definitions():
    values = getattr(contract, "ELBOW_RECOVERY_DEFINITIONS", None)
    assert values is not None, "explicit current-pose recovery definitions are missing"
    return values


def reference():
    value = artifact()
    value["stopped_motor_head"] = "8" * 40
    return value


def comparator(positions, suffix=""):
    return {
        "run_id": "1073c54a-40f9-499b-af43-5a438615430d",
        "host_incarnation": "c43fb8f1-058a-4110-b613-adc79f4e146b",
        "proof_sha256": "b" * 64,
        "initial_measured_reference": copy.deepcopy(positions),
        "calibration": copy.deepcopy(reference()["calibration"]),
        "trajectory_s": 7 if suffix == "-dwell" else 5.5,
        "hold_s": 2 if suffix == "-dwell" else 0.5,
        "integral": 0,
    }


def provider(integral=0, suffix="", *, start=2453):
    definition = definitions()[BASE + f"i{integral}" + suffix]
    factory = getattr(scripted_leader, "LeftElbowRecoveryInput", None)
    assert factory is not None, "current-pose recovery provider is missing"
    positions = seed(norm(start))
    positions["arm_left_shoulder_lift.pos"] = 99.8
    return factory(
        positions,
        joint_keys=JOINT_KEYS,
        fps=10,
        definition=definition,
        return_reference=reference(),
        reference_sha256="a" * 64,
        emit=lambda _: None,
        recovery_comparator=comparator(positions, suffix) if integral == 1 and suffix != "-confirm" else None,
    )


class RecoveryFeedback(Feedback):
    def get(self, now, sequence, *, follows=True, current=13.0, measured=None):
        positions, selected = super().get(now, sequence, current=current)
        measured = (
            raw(selected["context"]["requested_target"])
            if measured is None and follows
            else (raw(self.p.origin[KEY]) if measured is None else measured)
        )
        positions.update(self.p.origin)
        positions[KEY] = norm(measured)
        selected["position"].update(raw=measured, normalized=norm(measured))
        selected["gain_trial"] = {
            "state": "active",
            "status": "active",
            "active_verified": True,
            "integral": self.p.definition.integral,
            "active_verified_at_monotonic_s": 0,
            "binding": {
                "run_id": "run",
                "host_incarnation": "host",
                "recipe": self.p.definition.recipe_name,
                "integral": self.p.definition.integral,
            },
            "active_registers": {
                "Model_Number": 777,
                "Firmware_Major_Version": 3,
                "Firmware_Minor_Version": 10,
                "Operating_Mode": 0,
                "P_Coefficient": 16,
                "D_Coefficient": 32,
                "I_Coefficient": self.p.definition.integral,
                "Lock": 1,
                "Torque_Enable": 1,
                "Min_Position_Limit": 864,
                "Max_Position_Limit": 3073,
                "Present_Current": 0,
            },
            "original_registers": {
                "I_Coefficient": 0,
                "P_Coefficient": 16,
                "D_Coefficient": 32,
                "Lock": 0,
                "Torque_Enable": 0,
            },
            "original_bytes": {"24": 16, "25": 0},
            "active_bytes": {"24": 16, "25": 0},
        }
        corrected = getattr(self, "_corrected_receipts", set())
        for key, receipt in selected["sparse_receipts"].items():
            if key not in corrected:
                self.receipts[key]["decoded"]["present_position_raw"] = measured
                corrected.add(key)
            receipt["decoded"]["present_position_raw"] = self.receipts[key]["decoded"]["present_position_raw"]
        self._corrected_receipts = corrected
        return positions, selected


def drive(p, *, follows=True, until=None, mutate=None):
    feed = RecoveryFeedback(p)
    p.admit(0)
    actions = []
    for step in range(1, round((until or p.duration_s) * 30) + 1):
        now = step / 30
        observed, selected = feed.get(now, step, follows=follows)
        if mutate:
            mutate(p, now, observed, selected)
        p.collect_feedback(now, observed, step, selected_feedback=selected)
        if step % 3 == 0:
            p.advance(now, observed, step, selected_feedback=selected)
            actions.append(p.get_action())
    return actions


def execution(p):
    outcome = p.recovery
    return {
        "recovery": outcome,
        "original_seed": dict(p.origin),
        "progress_s": p.elapsed_s,
        "cycles_completed": len(outcome["cycles"]),
        "returns_qualified": (
            len(outcome["cycles"]) if p.definition.mode == "confirm" else int(outcome["target_attained"])
        ),
    }


def test_exact_six_frozen_definitions_and_explicit_recipes():
    values = definitions()
    assert set(values) == {BASE + f"i{i}" + suffix for i in (0, 1) for suffix in ("", "-dwell", "-confirm")}
    for name, definition in values.items():
        mode = "confirm" if name.endswith("-confirm") else "return"
        duration = 18 if mode == "confirm" else 7 if name.endswith("-dwell") else 5.5
        assert (definition.mode, definition.trajectory_s, definition.live_s, definition.current_cap_ma) == (
            mode,
            duration,
            60,
            100,
        )
        assert contract.RECIPES[name].seed is None
        with pytest.raises(FrozenInstanceError):
            definition.integral = 9


def test_new_provenance_allows_distinct_current_source_without_rewriting_old_validation():
    value = reference()
    assert definitions()[BASE + "i0"].validate_artifact(value) == value
    with pytest.raises(ValueError, match="stopped proof"):
        contract.LEFT_ELBOW_ORIGINAL_RETURN.validate_artifact(value)


@pytest.mark.parametrize("bad", ["pending", "x" * 40, "A" * 40, None])
def test_actual_historical_source_hash_is_validated_without_rewriting_the_proof(bad):
    value = reference()
    value["original_motor_head"] = bad
    with pytest.raises(ValueError, match="hash"):
        definitions()[BASE + "i0"].validate_artifact(value)


@pytest.mark.parametrize("integral,suffix", [(0, ""), (1, ""), (0, "-dwell"), (1, "-dwell")])
def test_static_return_is_an_honest_completed_program_not_target_attainment(integral, suffix):
    p = provider(integral, suffix)
    actions = drive(p, follows=False)
    result = p.recovery
    assert p.complete and not result["target_attained"]
    assert result["starting_raw"] == 2453 and result["reference_raw"] == 2472
    assert result["final_return_residual_raw"] == 19 and result["max_toward_target_raw"] == 0
    assert result["origin_goal_raw"] == 2472 and result["origin_present_raw"] == 2453
    assert result["cycles"] == [] and result["final_window"]["sample_count"] >= 3
    commands = [a[KEY.removeprefix("arm_")] for a in actions]
    assert commands[:20] == [norm(2453)] * 20
    assert commands[-5:] == [norm(2472)] * 5
    assert all(a <= b <= norm(2472) for a, b in zip(commands, commands[1:], strict=False))
    assert all(all(a[k.removeprefix("arm_")] == v for k, v in p.origin.items() if k != KEY) for a in actions)
    assert p.definition.completion_expectation(execution(p), reference(), "a" * 64) == (p.duration_s, 0, 0)


def test_actual_attainment_retains_two_tick_band_and_one_return():
    p = provider(1)
    drive(p)
    assert p.recovery["target_attained"] and p.recovery["final_return_residual_raw"] <= 2
    assert p.definition.completion_expectation(execution(p), reference(), "a" * 64) == (5.5, 0, 1)


def test_changed_measured_start_is_accepted_only_by_new_current_pose_recipe():
    p = provider(0, start=2459)
    drive(p, follows=False)
    assert p.recovery["starting_raw"] == 2459 and p.recovery["final_return_residual_raw"] == 13
    assert p.definition.completion_expectation(execution(p), reference(), "a" * 64) == (5.5, 0, 0)
    with pytest.raises(ValueError, match="stopped rest"):
        scripted_leader.LeftElbowOriginalReturnInput(
            seed(norm(2459)),
            joint_keys=JOINT_KEYS,
            fps=10,
            return_reference=artifact(),
            reference_sha256="a" * 64,
            emit=lambda _: None,
        )


@pytest.mark.parametrize("fault", ["progress", "residual", "gain_calibration", "expected_integral_bool"])
def test_completed_outcome_cannot_invent_progress_or_change_gain_calibration(fault):
    p = provider(1)
    drive(p, follows=False)
    value = execution(p)
    if fault == "progress":
        value["recovery"]["max_toward_target_raw"] = 100
    elif fault == "residual":
        value["recovery"]["final_return_residual_raw"] = 100
    elif fault == "gain_calibration":
        value["recovery"]["gain_trial"]["active_registers"]["Min_Position_Limit"] += 1
    else:
        value["recovery"]["expected_integral"] = True
    assert p.definition.completion_expectation(value, reference(), "a" * 64) is None


@pytest.mark.parametrize("integral", [0, 1])
def test_selected_current_cap_is_a_hard_abort(integral):
    p = provider(integral)
    p.admit(0)
    observed, selected = RecoveryFeedback(p).get(0.1, 1, current=100)
    with pytest.raises(ValueError, match="100"):
        p.collect_feedback(0.1, observed, 1, selected_feedback=selected)
    assert not p.complete


def test_subthreshold_static_rise_is_allowed_only_for_new_i1():
    def rising(p, now, observed, selected):
        if selected["context"]["phase"] == "origin":
            selected["current"]["ma"] = 6.5 * max(0, int((now - 5) / 0.07))

    with pytest.raises(ValueError, match="sustained rising"):
        drive(provider(0), follows=False, mutate=rising)
    p = provider(1)
    drive(p, follows=False, mutate=rising)
    assert p.complete and not p.recovery["target_attained"]


def test_wrong_direction_and_growing_oscillation_refuse_progress():
    p = provider(1)
    p.admit(0)
    observed, selected = RecoveryFeedback(p).get(0.1, 1, measured=2450)
    with pytest.raises(ValueError, match="direction|envelope"):
        p.collect_feedback(0.1, observed, 1, selected_feedback=selected)
    p = provider(1)

    def oscillating(p, now, observed, selected):
        if 3 <= now < 3.3:
            delta = (3 + int((now - 3) * 30)) * (-1 if round(now * 30) % 2 else 1)
            measured = selected["action"]["expected_goal_raw"] + delta
            observed[KEY] = norm(measured)
            selected["position"].update(raw=measured, normalized=norm(measured))

    with pytest.raises(ValueError, match="oscillation|direction|envelope"):
        drive(p, mutate=oscillating)


@pytest.mark.parametrize("fault", ["stale", "calibration", "sparse", "window", "count", "attainment", "gain"])
def test_malformed_feedback_or_outcome_never_qualifies(fault):
    p = provider(1)
    if fault in {"stale", "calibration", "sparse"}:

        def corrupt(p, now, observed, selected):
            if fault == "stale":
                selected["current"]["read_started_at_monotonic_s"] = now - 0.6
            elif fault == "calibration":
                selected["motor"]["calibration"]["homing_offset"] += 1
            elif selected["sparse_receipts"]:
                next(iter(selected["sparse_receipts"].values()))["decoded"]["goal_position_raw"] -= 1

        with pytest.raises(ValueError):
            drive(p, mutate=corrupt)
    else:
        drive(p, follows=False)
        value = execution(p)
        if fault == "window":
            value["recovery"]["final_window"]["position_span_s"] = 0.19
        elif fault == "count":
            value["returns_qualified"] = 1
        elif fault == "attainment":
            value["recovery"]["target_attained"] = True
        else:
            value["recovery"]["expected_integral"] = 0
        assert p.definition.completion_expectation(value, reference(), "a" * 64) is None


def test_confirmation_freezes_fresh_reference_and_needs_first_return_before_second_departure():
    p = provider(1, "-confirm", start=2472)
    actions = drive(p)
    assert p.complete and len(p.recovery["cycles"]) == 2 and p.recovery["target_attained"]
    assert p.recovery["reference_raw"] == 2472 and p.recovery["starting_raw"] == 2472
    assert p.definition.completion_expectation(execution(p), reference(), "a" * 64) == (18, 2, 2)
    assert all(abs(a[KEY.removeprefix("arm_")] - norm(2472)) <= 3 for a in actions)
    p = provider(1, "-confirm", start=2472)

    def nonreturn(p, now, observed, selected):
        if selected["context"]["phase"] == "origin":
            observed[KEY] = norm(2491)
            selected["position"].update(raw=2491, normalized=norm(2491))
            for receipt in selected["sparse_receipts"].values():
                if receipt["context"]["phase"] == "origin":
                    receipt["decoded"]["present_position_raw"] = 2491

    with pytest.raises(ValueError, match="return"):
        drive(p, mutate=nonreturn)
    assert p.elapsed_s < 9 and len(p.recovery["cycles"]) == 0


def test_new_preparation_preserves_all_twelve_seed_targets_and_finite_noresponse_counts():
    definitions()
    positions = seed(norm(2453))
    positions["arm_left_shoulder_lift.pos"] = 99.8
    task = FiniteTask(
        contract.RECIPES[BASE + "i1"],
        positions,
        0,
        1.5,
        return_reference=reference(),
        reference_sha256="a" * 64,
        recovery_comparator=comparator(positions),
    )
    for step in range(1, 41):
        task.advance(step / 10, {"sequence": step, "positions": positions})
        assert task.provider.get_action()["left_shoulder_lift.pos"] == 99.8
        if not task.provider.preparing:
            break
    assert task.original_seed == positions
    p = task.provider._program
    feed = RecoveryFeedback(p)
    now = task.last_frame
    for tick in range(1, 170):
        at = now + tick / 30
        observed, selected = feed.get(at, step + tick, follows=False)
        task.advance(at, {"sequence": step + tick, "positions": observed, "selected_joint": selected})
        if task.complete:
            break
    result = task.snapshot()
    assert task.complete and result["progress_s"] == 5.5
    assert result["returns_qualified"] == result["cycles_completed"] == 0
    assert not result["recovery"]["target_attained"]


def test_absolute_new_live_deadline_and_pause_do_not_rebase():
    p = provider(1)
    drive(p, until=3)
    original, progress, action = copy.deepcopy(p.origin), p.elapsed_s, p.get_action()
    p.freeze()
    p.admit(4)
    assert p.origin == original and p.elapsed_s == progress and p.get_action() == action
    positions = seed(norm(2453))
    task = FiniteTask(
        contract.RECIPES[BASE + "i1"],
        positions,
        0,
        1.5,
        return_reference=reference(),
        reference_sha256="a" * 64,
        recovery_comparator=comparator(positions),
    )
    with pytest.raises(ValueError, match="60"):
        task.check(60)


@pytest.mark.parametrize("fault", ["missing", "binding", "integral", "bytes", "future", "pid", "lock"])
def test_actual_gain_proof_must_match_the_selected_feedback_and_preserved_bytes(fault):
    p = provider(1)
    p.admit(0)
    observed, selected = RecoveryFeedback(p).get(0.1, 1)
    proof = selected["gain_trial"]
    if fault == "missing":
        selected.pop("gain_trial")
    elif fault == "binding":
        proof["binding"]["run_id"] = "foreign"
    elif fault == "integral":
        proof["active_registers"]["I_Coefficient"] = 0
    elif fault == "bytes":
        proof["active_bytes"]["25"] = 1
    elif fault == "future":
        proof["active_verified_at_monotonic_s"] = 0.2
    elif fault == "pid":
        proof["active_registers"]["P_Coefficient"] = 32
    else:
        proof["active_registers"]["Lock"] = 0
    with pytest.raises(ValueError, match="gain"):
        p.collect_feedback(0.1, observed, 1, selected_feedback=selected)


@pytest.mark.parametrize("fault", ["missing", "pose", "joint", "hold", "calibration", "integral"])
def test_i1_comparison_requires_matched_i0_without_repositioning(fault):
    definition = definitions()[BASE + "i1"]
    positions = seed(norm(2453))
    value = comparator(positions)
    if fault == "missing":
        value = None
    elif fault == "pose":
        value["initial_measured_reference"][KEY] = norm(2450)
    elif fault == "joint":
        value["initial_measured_reference"]["arm_right_wrist_roll.pos"] += 2
    elif fault == "hold":
        value["hold_s"] = 2
    elif fault == "calibration":
        value["calibration"]["homing_offset"] += 1
    else:
        value["integral"] = 1
    with pytest.raises(ValueError, match="comparator"):
        scripted_leader.LeftElbowRecoveryInput(
            positions,
            joint_keys=JOINT_KEYS,
            fps=10,
            definition=definition,
            return_reference=reference(),
            reference_sha256="a" * 64,
            recovery_comparator=value,
            emit=lambda _: None,
        )


def test_self_consistent_phase_metadata_cannot_change_the_frozen_measured_start():
    p = provider(1)
    p.admit(0)
    observed, selected = RecoveryFeedback(p).get(0.1, 1)
    requested = p.origin[KEY] + 0.1
    selected["context"].update(endpoint_target=requested, requested_target=requested)
    selected["action"].update(
        requested=requested,
        relative_limited=requested,
        current_limited=requested,
        final_normalized=norm(raw(requested)),
        expected_goal_raw=raw(requested),
    )
    with pytest.raises(ValueError, match="frozen"):
        p.collect_feedback(0.1, observed, 1, selected_feedback=selected)


def test_monotonic_catch_up_against_changing_goals_is_not_fixed_goal_oscillation():
    p = provider(1, "-confirm", start=2472)

    def catch_up(p, now, observed, selected):
        context = selected["context"]
        if context["cycle"] == 1 and context["phase"] == "outbound":
            progress = context["progress_s"]
            offset = 0 if progress < 2.4 else 8 if progress < 3.2 else 20 if progress < 4.3 else 32
            measured = 2472 + offset
            if progress >= 4.3:
                measured = max(measured, selected["action"]["expected_goal_raw"])
            observed[KEY] = norm(measured)
            selected["position"].update(raw=measured, normalized=norm(measured))

    drive(p, mutate=catch_up)
    assert p.complete and p.recovery["target_attained"] and len(p.recovery["cycles"]) == 2


def test_actual_alternating_response_at_one_fixed_encoded_goal_still_aborts():
    p = provider(1)
    drive(p, until=3)
    feed = RecoveryFeedback(p)
    goal = raw(p.diagnostic_context["requested_target"])
    with pytest.raises(ValueError, match="direction|oscillation"):
        for tick, offset in enumerate((3, -4, 5, -6), 1):
            now = 3 + tick / 30
            observed, selected = feed.get(now, 100 + tick, measured=goal + offset)
            assert selected["action"]["expected_goal_raw"] == goal
            p.collect_feedback(now, observed, 100 + tick, selected_feedback=selected)
    assert not p.complete and p.elapsed_s == 3
