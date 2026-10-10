"""Non-actuating feedback replay for the physical body recipe's normal-rest acquisition."""

import pytest

from examples.alohamini.am1_finite_task import JOINT_KEYS, FiniteTask
from examples.alohamini.am1_scripted_prepare import SHOULDER, PreparedScriptedInput
from examples.alohamini.am1_session_contract import RECIPES
from examples.alohamini.scripted_leader import ArmHoldBodyInput


def measured_positions(shoulder):
    return {key: shoulder if key == SHOULDER else 30.0 for key in JOINT_KEYS}


def test_physical_body_normal_rest_acquires_once_from_measured_replay_then_original_twelve_seconds():
    initial = measured_positions(110.0)
    task = FiniteTask(
        RECIPES["physical-arm-hold-body"],
        initial,
        0.0,
        shoulder_amplitude=1.5,
        start_held=True,
        feedback_provenance="measured-feedback-replay",
    )
    records = []
    task.provider._emit = lambda row: (records.append(row), task.record(row))
    assert isinstance(task.provider, PreparedScriptedInput)
    assert task.snapshot()["initial_measured_reference"] == initial
    assert task.original_seed is None and task.provider.elapsed_s == 0
    task.resume(0.0)
    # Feedback replay is independent of requested targets: the first actual raw-derived
    # normalized measurement remains110, then the recorded boundary convergence arrives.
    replay = [110.0, 108.0, 106.0, 104.0, 102.0, 100.0, 100.0, 100.0]
    previous_request = task.provider.get_action()[SHOULDER.removeprefix("arm_")]
    for sequence in range(1, 81):
        observed = (
            replay[sequence - 1]
            if sequence <= len(replay)
            else max(
                99.0,
                100.0 - (sequence - 9) * 0.075,
            )
        )
        now = sequence / 10
        task.advance(now, {"positions": measured_positions(observed), "sequence": sequence, "at": now})
        request = task.provider.get_action()
        requested_shoulder = request[SHOULDER.removeprefix("arm_")]
        assert 99.0 <= requested_shoulder <= 100.0
        assert abs(requested_shoulder - previous_request) <= 0.075 + 1e-12
        assert all(request[key.removeprefix("arm_")] == 30 for key in JOINT_KEYS if key != SHOULDER)
        previous_request = requested_shoulder
        if not task.provider.preparing:
            break
        assert task.provider.elapsed_s == 0 and task.original_seed is None
    assert not task.provider.preparing and now < 20
    assert task.provider.duration_s == 12 and RECIPES["physical-arm-hold-body"].live_s == 30
    seed = measured_positions(99.0)
    assert task.original_seed == seed
    seed_rows = [row for row in records if row["event"] == "am1_scripted_seed"]
    assert len(seed_rows) == 1 and seed_rows[0]["origin"] == seed
    assert isinstance(task.provider._program, ArmHoldBodyInput)
    assert task.provider._program.targets == seed
    assert task.provider._program.joint_amplitudes == dict.fromkeys(JOINT_KEYS, 3.0)
    for _ in range(123):
        sequence += 1
        now = sequence / 10
        task.advance(now, {"positions": seed, "sequence": sequence, "at": now})
    assert task.complete and task.snapshot()["progress_s"] == 12
    assert task.snapshot()["returns_qualified"] == 1
    assert task.original_seed == seed and task.admissions == 1
    assert len([row for row in records if row["event"] == "am1_scripted_seed"]) == 1


@pytest.mark.parametrize("recipe", ["physical-arm-hold-body", "sim-arm-hold-body"])
def test_in_range_body_path_remains_direct_original_hold_provider(recipe):
    initial = measured_positions(99.0)
    task = FiniteTask(RECIPES[recipe], initial, 0.0, shoulder_amplitude=0.25)
    assert isinstance(task.provider, ArmHoldBodyInput)
    assert task.original_seed == initial and task.provider.duration_s == 12
    assert task.provider.targets == initial
    assert task.provider.joint_amplitudes == dict.fromkeys(JOINT_KEYS, 3.0)


def test_simulated_body_does_not_opt_in_to_extended_normal_rest_acquisition():
    with pytest.raises(ValueError, match="outside"):
        FiniteTask(RECIPES["sim-arm-hold-body"], measured_positions(110.0), 0.0, 1.5)


@pytest.mark.parametrize("shoulder", [120.01, -120.01])
def test_physical_body_preserves_selected_normal_rest_limit(shoulder):
    with pytest.raises(ValueError, match="outside"):
        FiniteTask(RECIPES["physical-arm-hold-body"], measured_positions(shoulder), 0.0, 1.5)


def test_physical_body_refuses_other_joint_outside_original_range():
    initial = measured_positions(110.0)
    initial["arm_right_elbow_flex.pos"] = 100.01
    with pytest.raises(ValueError, match="outside"):
        FiniteTask(RECIPES["physical-arm-hold-body"], initial, 0.0, 1.5)


def test_physical_body_boundary_timeout_and_preparation_wall_budget_are_unchanged():
    task = FiniteTask(RECIPES["physical-arm-hold-body"], measured_positions(110.0), 0.0, 1.5)
    with pytest.raises(ValueError, match="boundary.*converge"):
        for sequence in range(1, 82):
            task.advance(
                sequence / 10,
                {
                    "positions": measured_positions(110.0),
                    "sequence": sequence,
                    "at": sequence / 10,
                },
            )
    assert task.original_seed is None and task.provider.elapsed_s == 0
    task.hold()
    with pytest.raises(ValueError, match="20-second preparation"):
        task.resume(20)
