"""One original-reference approach; no gains, rebase, reverse or hardware access."""

import copy
import hashlib
import json

import pytest

from examples.alohamini import am1_session_contract as contract, scripted_leader
from examples.alohamini.am1_finite_task import JOINT_KEYS, FiniteTask
from tests.robots.test_am1_elbow_response_profile import CALIBRATION, Feedback, norm, raw
from tests.robots.test_am1_physical_executor import ControlledHost, executor, observation
from tests.robots.test_am1_selected_elbow_profiles import seed

KEY = "arm_left_elbow_flex.pos"
RECIPE = "physical-left-elbow-original-return"


def artifact():
    return {
        "version": 1,
        "original_run_id": "d85018ee-3dd6-45fe-b66a-b9288644b04b",
        "original_host_incarnation": "90dcf33c-01c2-40ff-944f-36f643ea3c52",
        "original_motor_head": "1" * 40,
        "original_executor_head": "2" * 40,
        "original_manifest_sha256": "3" * 64,
        "original_log_sha256": "4" * 64,
        "original_terminal_sha256": "5" * 64,
        "reference_raw": 2472,
        "reference_normalized": norm(2472),
        "baseline_noise_raw": 0,
        "motor": {"bus": "left", "name": KEY[:-4], "id": 3, "model": "sts3215"},
        "calibration": copy.deepcopy(CALIBRATION),
        "stopped_operation_id": "881a4a4d-245e-48af-bd0e-526b4ad22f50",
        "stopped_snapshot_sha256": "6" * 64,
        "stopped_motor_head": "1" * 40,
        "stopped_executor_head": "7" * 40,
        "stopped_manifest_sha256": "8" * 64,
        "stopped_position_raw": 2453,
        "stopped_goal_raw": 2472,
        "stopped_torque_enable": 0,
        "stopped_current_raw": 0,
        "stopped_mode": 0,
        "stopped_status": 0,
        "model_number": 777,
        "firmware_major": 3,
        "firmware_minor": 10,
        "admission_motor_head": "9" * 40,
    }


def definition():
    value = getattr(contract, "LEFT_ELBOW_ORIGINAL_RETURN", None)
    assert value is not None, "retained original-return definition is missing"
    return value


def provider():
    factory = getattr(scripted_leader, "LeftElbowOriginalReturnInput", None)
    assert factory is not None, "return-only provider is missing"
    positions = seed(norm(2453))
    positions["arm_left_shoulder_lift.pos"] = 99.8
    return factory(positions, joint_keys=JOINT_KEYS, fps=10, return_reference=artifact(),
                   reference_sha256="a" * 64, emit=lambda _: None)


class ReturnFeedback(Feedback):
    def get(self, now, sequence, *, returned=True, **kwargs):
        positions, selected = super().get(now, sequence, **kwargs)
        measured = raw(selected["context"]["requested_target"]) if returned else 2453
        positions.update(self.p.origin)
        positions[KEY] = norm(measured)
        selected["position"].update(raw=measured, normalized=norm(measured))
        for receipt in selected["sparse_receipts"].values():
            if receipt["context"]["phase"] == "origin":
                receipt["decoded"]["present_position_raw"] = measured
        return positions, selected


def drive(p, *, returned=True, until=5.5):
    feed = ReturnFeedback(p)
    p.admit(0)
    actions = []
    for step in range(1, round(until * 30) + 1):
        now = step / 30
        positions, selected = feed.get(now, step, returned=returned)
        p.collect_feedback(now, positions, step, selected_feedback=selected)
        if step % 3 == 0:
            p.advance(now, positions, step, selected_feedback=selected)
            actions.append(p.get_action())
    return feed, actions


def test_recipe_unavailable_without_private_retained_reference():
    recipe = contract.RECIPES.get(RECIPE)
    assert recipe is not None, "return-only recipe is missing"
    assert (recipe.trajectory_s, recipe.live_s, recipe.seed) == (5.5, 60, None)
    assert not executor(ControlledHost()).supports(recipe)


def test_one_monotonic_approach_keeps_other_eleven_and_original_target():
    p = provider()
    _, actions = drive(p)
    commands = [a[KEY.removeprefix("arm_")] for a in actions]
    assert p.complete and p.elapsed_s == 5.5
    assert commands[:20] == [norm(2453)] * 20
    assert commands[-5:] == [norm(2472)] * 5
    assert all(0 <= b - a <= .1 + 1e-9 for a, b in zip(commands, commands[1:], strict=False))
    for action in actions:
        assert all(action[k.removeprefix("arm_")] == v for k, v in p.origin.items() if k != KEY)
    outcome = p.retained_return
    assert outcome["reference_raw"] == 2472 and outcome["starting_raw"] == 2453
    assert outcome["final_return_residual_raw"] <= 2
    assert p.origin[KEY] == norm(2453)


def test_actual_nineteen_tick_nonreturn_refuses_without_rebase_or_completion():
    p = provider()
    with pytest.raises(ValueError, match="original raw envelope"):
        drive(p, returned=False)
    assert not p.complete and p.origin[KEY] == norm(2453)
    assert p.retained_return["reference_raw"] == 2472
    assert p.retained_return["final_return_residual_raw"] is None


def test_current_high_shoulder_stays_identical_during_preparation():
    recipe = contract.RECIPES.get(RECIPE)
    assert recipe is not None, "return-only recipe is missing"
    positions = seed(norm(2453))
    positions["arm_left_shoulder_lift.pos"] = 99.8
    task = FiniteTask(recipe, positions, 0, 1.5, return_reference=artifact(), reference_sha256="a" * 64)
    for step in range(1, 41):
        task.advance(step / 10, {"sequence": step, "positions": positions})
        assert task.provider.get_action()["left_shoulder_lift.pos"] == 99.8
        if not task.provider.preparing:
            break
    assert not task.provider.preparing


@pytest.mark.parametrize("change", [
    {"baseline_noise_raw": True}, {"reference_raw": 2453}, {"stopped_torque_enable": 1},
    {"baseline_noise_raw": 1}, {"baseline_noise_raw": 2},
    {"stopped_position_raw": 2430}, {"motor": {"bus": "right", "name": KEY[:-4], "id": 3, "model": "sts3215"}},
])
def test_archived_reference_rejects_malformed_or_escalated_proof(change):
    value = artifact()
    value.update(change)
    with pytest.raises(ValueError):
        definition().validate_artifact(value)


def private_config(tmp_path, value=None):
    path = tmp_path / "original-return.private.json"
    data = json.dumps(artifact() if value is None else value).encode()
    path.write_bytes(data)
    path.chmod(0o600)
    return {"shoulder_amplitude": 1.5, "host_head": "9" * 40,
            "elbow_return_reference": {"path": str(path.resolve()), "sha256": hashlib.sha256(data).hexdigest()}}


@pytest.mark.parametrize("fault", ["hash", "source", "relative", "extra", "missing", "duplicate"])
def test_invalid_private_artifact_refuses_before_any_io(tmp_path, fault):
    from tools.am1_physical_executor import PhysicalExecutor

    config = private_config(tmp_path)
    if fault == "hash":
        config["elbow_return_reference"]["sha256"] = "f" * 64
    elif fault == "source":
        config["host_head"] = "0" * 40
    elif fault == "relative":
        config["elbow_return_reference"]["path"] = "relative.json"
    elif fault == "extra":
        config["elbow_return_reference"]["target"] = 2472
    elif fault == "missing":
        config.pop("elbow_return_reference")
    else:
        path = config["elbow_return_reference"]["path"]
        data = b'{"version":1,"version":1}'
        from pathlib import Path

        Path(path).write_bytes(data)
        config["elbow_return_reference"]["sha256"] = hashlib.sha256(data).hexdigest()
    calls = []
    e = PhysicalExecutor(config, observation(), io_factory=lambda *_: calls.append("constructed"))
    assert not e.supports(contract.RECIPES[RECIPE])
    with pytest.raises(RuntimeError):
        e.bind_run("new-run", contract.RECIPES[RECIPE])
    assert calls == [] and e.worker is None
    assert e.supports(contract.RECIPES["physical-left-elbow-compare"])


def test_private_proof_distinguishes_archived_and_current_motor_sources(tmp_path):
    from tools.am1_physical_executor import PhysicalExecutor

    e = PhysicalExecutor(private_config(tmp_path), observation())
    assert e.supports(contract.RECIPES[RECIPE])
    assert e.return_reference["stopped_motor_head"] != e.config["host_head"]


@pytest.mark.parametrize("bad", [-100.01, 100.01])
def test_return_preparation_refuses_nonrepresentable_shoulder_without_adjusting_it(bad):
    positions = seed(norm(2453))
    positions["arm_left_shoulder_lift.pos"] = bad
    with pytest.raises(ValueError, match="initial range"):
        FiniteTask(contract.RECIPES[RECIPE], positions, 0, 1.5,
                   return_reference=artifact(), reference_sha256="a" * 64)


def test_return_keeps_initial_held_targets_despite_small_feedback_drift():
    positions = seed(norm(2453))
    positions["arm_left_shoulder_lift.pos"] = 99.8
    task = FiniteTask(contract.RECIPES[RECIPE], positions, 0, 1.5,
                      return_reference=artifact(), reference_sha256="a" * 64)
    observed = dict(positions, **{"arm_left_shoulder_lift.pos": 99.75})
    for step in range(1, 40):
        task.advance(step / 10, {"sequence": step, "positions": observed})
        assert task.provider.get_action()["left_shoulder_lift.pos"] == 99.8
        if not task.provider.preparing:
            break
    assert task.original_seed == positions


@pytest.mark.parametrize("fault", ["stale", "foreign", "position_span", "residual3", "encoded"])
def test_actual_clock_and_strict_final_raw_return_proof_cannot_be_relaxed(fault):
    p = provider()
    feed = ReturnFeedback(p)
    p.admit(0)
    with pytest.raises(ValueError):
        for step in range(1, 166):
            now = step / 30
            positions, selected = feed.get(now, step)
            if p.elapsed_s >= 5:
                if fault == "stale":
                    selected["position"]["read_started_at_monotonic_s"] = now - .501
                elif fault == "foreign":
                    selected["host_incarnation"] = "other-host"
                elif fault == "position_span":
                    selected["position"]["read_started_at_monotonic_s"] = 5 + (now - 5) / 4
                    selected["position"]["read_completed_at_monotonic_s"] = 5.001 + (now - 5) / 4
                elif fault == "residual3":
                    positions[KEY] = norm(2469)
                    selected["position"].update(raw=2469, normalized=norm(2469))
                else:
                    selected["action"]["expected_goal_raw"] = 2471
            p.collect_feedback(now, positions, step, selected_feedback=selected)
            if step % 3 == 0:
                p.advance(now, positions, step, selected_feedback=selected)
    assert not p.complete


def test_return_only_real_host_gate_accepts_its_baseline_without_accepting_old_contexts(monkeypatch):
    from lerobot.robots.alohamini.alohamini_host import AM1LocalControl
    from tests.robots.test_am1_selected_joint_evidence import binding, command, robot, sampler

    obj, _, writes = robot(monkeypatch)
    evidence, _ = sampler()
    identity = binding()
    control = AM1LocalControl(**identity, selected_evidence=evidence)
    p = provider()
    metadata = p.diagnostic_context
    control.apply(obj, command(identity, metadata))
    assert evidence.context == metadata
    assert all(address not in (21, 22, 23, 24) for address, _, _ in writes)
    broken = dict(metadata, profile="LeftElbowCompare")
    with pytest.raises(RuntimeError):
        control.apply(obj, command(identity, broken, sequence=2))


def completed_task():
    positions = provider().origin
    task = FiniteTask(contract.RECIPES[RECIPE], positions, 0, 1.5,
                      return_reference=artifact(), reference_sha256="a" * 64)
    for step in range(1, 80):
        task.advance(step / 10, {"sequence": step, "positions": positions})
        if not task.provider.preparing:
            break
    feed = ReturnFeedback(task.provider._program)
    for tick in range(1, 200):
        now = step / 10 + tick / 30
        observed, selected = feed.get(now, step + tick)
        task.advance(now, {"sequence": step + tick, "positions": observed, "selected_joint": selected})
        if task.provider.complete:
            return task, now
    pytest.fail("finite return did not reach its5.5 s boundary")


def test_finite_task_completes_at_proven_five_point_five_without_extra_success_dwell():
    task, _ = completed_task()
    result = task.snapshot()
    assert result["complete"] and result["progress_s"] == 5.5
    assert result["cycles_completed"] == 0 and result["returns_qualified"] == 1
    window = result["retained_return"]["final_window"]
    assert window["sample_count"] >= 3
    assert window["current_span_s"] >= .2 - 1e-9 and window["position_span_s"] >= .2 - 1e-9
    assert definition().completion_expectation(result, artifact(), "a" * 64) == (5.5, 0, 1)


def test_real_reader_clock_and_first_reply_lag_do_not_advance_or_rebase_program():
    from tests.robots.test_am1_physical_executor import client_sample
    from tools.am1_physical_executor import read_physical_sample

    positions = provider().origin
    task = FiniteTask(contract.RECIPES[RECIPE], positions, 0, 1.5,
                      return_reference=artifact(), reference_sha256="a" * 64)
    for step in range(1, 80):
        task.advance(step / 10, {"sequence": step, "positions": positions})
        if not task.provider.preparing:
            break
    previous = -1
    feed = ReturnFeedback(task.provider._program)
    for tick in range(1, 210):
        now = step / 10 + tick / 30
        observed, selected = feed.get(now, tick)
        marker = {"selected_joint": selected} if tick != 1 else {}
        c = client_sample(now, state="active", epoch=0, observation_id=tick,
                          acquired_at_monotonic_s=now - .005, read_started_at_monotonic_s=now - .02,
                          accepted_action_sequence=tick, **marker)
        c.get_observation = lambda observed=observed: observed
        c.latest_observation_received_at = now - .001
        c.latest_observation_roundtrip_age_s = .029
        sample = read_physical_sample(c, "run", "host", previous, clock=lambda now=now: now)
        assert sample["at"] < selected["current"]["read_completed_at_monotonic_s"] < now
        task.advance(now, sample)
        previous = tick
        if tick == 1:
            assert task.provider.elapsed_s == 0
        if task.complete:
            break
    assert task.complete and task.snapshot()["progress_s"] == 5.5
    assert task.original_seed == positions
    assert task.provider.retained_return["reference_raw"] == 2472


def test_freeze_preserves_reference_and_latches_actual_adverse_origin():
    p = provider()
    drive(p, until=5.1)
    original = copy.deepcopy(p.origin)
    progress, action = p.elapsed_s, p.get_action()
    p.freeze()
    assert p.origin == original and p.elapsed_s == progress and p.get_action() == action
    p._adverse_reason = "actual sustained rising current"
    p.admit(8)
    observed, selected = ReturnFeedback(p).get(8.1, 999)
    with pytest.raises(ValueError, match="sustained rising"):
        p.advance(8.1, observed, 999, selected_feedback=selected)
    assert p.elapsed_s == progress and p.retained_return["reference_raw"] == 2472


def test_artifact_changed_after_binding_stops_before_driver_construction(tmp_path):
    from pathlib import Path

    from tests.robots.test_am1_physical_executor import until
    from tools.am1_physical_executor import PhysicalExecutor

    config = private_config(tmp_path)
    calls = []
    e = PhysicalExecutor(config, observation(), io_factory=lambda *_: calls.append("constructed"))
    e.bind_run("new-run", contract.RECIPES[RECIPE])
    Path(config["elbow_return_reference"]["path"]).write_text("{}", encoding="utf-8")
    try:
        e.begin(contract.RECIPES[RECIPE])
        until(lambda: e.evidence()["lifecycle"]["phase"] == "terminal")
        assert calls == [] and "hash differs" in e.evidence()["fault"]
    finally:
        e.close()
        e.worker.join(2)


@pytest.mark.parametrize("fault", [None, "target", "residual", "count", "span", "source", "artifact", "sparse", "last_started", "pre_live", "joint_vector"])
def test_authority_completion_requires_the_preserved_target_and_exact_proof(tmp_path, fault):
    from tests.robots.test_am1_persistent_session import start
    from tests.robots.test_am1_physical_authority import admit_live, build, completed_handoff

    task, _ = completed_task()
    clock, e, authority = build(tmp_path)
    e.supports = lambda r: r.name == RECIPE
    try:
        assert start(authority, RECIPE)["accepted"]
        if fault == "pre_live":
            clock.advance(8)
        admit_live(authority, e, clock)
        clock.advance(8)
        receipt, _ = completed_handoff(authority, e, clock)
        result = task.snapshot()
        receipt["execution"].update(result, feedback_sequence=41)
        receipt["measured_positions"] = dict(task.original_seed, **{KEY: norm(2472)})
        original = e.evidence
        proof = artifact()
        config = {"host_head": "9" * 40, "elbow_return_reference": {"sha256": "a" * 64}}
        if fault == "target":
            receipt["measured_positions"][KEY] = norm(2453)
        elif fault == "residual":
            receipt["execution"]["retained_return"]["final_return_residual_raw"] = 3
        elif fault == "count":
            receipt["execution"]["retained_return"]["final_window"]["sample_count"] = 2
        elif fault == "span":
            receipt["execution"]["retained_return"]["final_window"]["position_span_s"] = .199
        elif fault == "source":
            config["host_head"] = "0" * 40
        elif fault == "artifact":
            config["elbow_return_reference"]["sha256"] = "b" * 64
        elif fault == "sparse":
            receipt["execution"]["retained_return"]["origin_goal_raw"] = 2453
        elif fault == "last_started":
            receipt["execution"]["retained_return"]["final_window"]["last_position_started_at"] = 0
        elif fault == "joint_vector":
            receipt["execution"]["original_seed"] = {KEY: task.original_seed[KEY]}
            receipt["measured_positions"] = {KEY: norm(2472)}
        e.evidence = lambda: dict(original(), retained_return_reference=proof, backend_contract=config)
        authority.tick()
        assert (authority.run["status"] == "finishing") is (fault is None)
        if fault is None:
            assert authority.run["completion_receipt"]["measured_positions"][KEY] == norm(2472)
    finally:
        authority.close()


def test_new_baseline_jitter_cannot_expand_the_original_two_tick_return_band():
    p = provider()
    feed = ReturnFeedback(p)
    p.admit(0)
    with pytest.raises(ValueError, match="original raw envelope"):
        for step in range(1, 166):
            now = step / 30
            positions, selected = feed.get(now, step)
            phase = selected["context"]["phase"]
            measured = (2452 if step % 2 else 2454) if phase == "baseline" else 2469 if phase == "origin" else selected["position"]["raw"]
            positions[KEY] = norm(measured)
            selected["position"].update(raw=measured, normalized=norm(measured))
            p.collect_feedback(now, positions, step, selected_feedback=selected)
            if step % 3 == 0:
                p.advance(now, positions, step, selected_feedback=selected)
    assert p._noise == 2 and p.retained_return["baseline_noise_raw"] == 0
    assert p.retained_return["final_return_residual_raw"] is None


def test_return_only_launch_never_enables_gain_or_extra_shoulder_experiments(monkeypatch, tmp_path):
    from tests.robots.test_am1_selected_elbow_profiles import (
        test_host_launch_env_cannot_inherit_or_enable_other_recipe_experiments,
    )
    from tools import am1_physical_executor as physical

    monkeypatch.setattr(physical, "load_elbow_return_reference", lambda _: artifact())
    test_host_launch_env_cannot_inherit_or_enable_other_recipe_experiments(monkeypatch, tmp_path, RECIPE, True, False)


def test_real_rising_current_at_static_original_target_stops_before_return_completion():
    p = provider()
    feed = ReturnFeedback(p)
    p.admit(0)
    with pytest.raises(ValueError, match="sustained rising current"):
        for step in range(1, 166):
            now = step / 30
            current = 6.5 * max(0, int((now - 5) / .07))
            positions, selected = feed.get(now, step, returned=False, current=current)
            p.collect_feedback(now, positions, step, selected_feedback=selected)
            if step % 3 == 0:
                p.advance(now, positions, step, selected_feedback=selected)
    assert p.elapsed_s < 5.5 and not p.complete
    assert p.retained_return["final_return_residual_raw"] is None
