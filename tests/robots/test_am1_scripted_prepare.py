"""Finite selected-shoulder preparation; no hardware or native gate substitutes."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "examples" / "alohamini"))
from am1_scripted_prepare import PreparedScriptedInput  # noqa: E402
from scripted_leader import ScriptedLeaderInput  # noqa: E402
from scripted_leader_repeat import ArmSmokeRepeatInput  # noqa: E402

SHOULDER = "arm_left_shoulder_lift.pos"
KEYS = tuple(f"arm_{side}_{joint}.pos" for side in ("left", "right") for joint in (
    "shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper",
))


def seed(shoulder=105.0):
    return {key: shoulder if key == SHOULDER else 30.0 for key in KEYS}


def provider(*, shoulder=105.0, program=ScriptedLeaderInput, profile="ArmSmoke"):
    records, calls = [], []

    def factory(*args, **kwargs):
        calls.append(dict(args[0]))
        return program(*args, **kwargs)

    wrapped = PreparedScriptedInput(seed(shoulder), joint_keys=KEYS, fps=10,
                                   provider=factory, motion_profile=profile, emit=records.append)
    return wrapped, records, calls


def finish_preparation(wrapped, start=0.0):
    now, sequence = start, 0
    wrapped.admit(now)
    while wrapped.preparing:
        now += .1
        sequence += 1
        observed = {"arm_" + key: value for key, value in wrapped.get_action().items()}
        wrapped.advance(now, observed, sequence)
        assert now - start < 10
    return now, sequence


def test_preparation_preserves_actual_initial_pose_and_waits_for_live():
    wrapped, records, calls = provider()
    assert wrapped.origin[SHOULDER] == 105.0
    assert wrapped.get_action()[SHOULDER.removeprefix("arm_")] == 100.0
    wrapped.advance(50, seed(100), 1)
    assert wrapped.elapsed_s == 0 and wrapped.preparing and calls == []
    assert wrapped.get_action()[SHOULDER.removeprefix("arm_")] == 100
    assert not any(row["event"] == "am1_scripted_seed" for row in records)


@pytest.mark.parametrize(("program", "profile", "duration"), [
    (ScriptedLeaderInput, "ArmSmoke", 88), (ArmSmokeRepeatInput, "ArmSmokeRepeat", 352),
])
def test_one_fresh_seed_handoff_keeps_original_program_and_no_catchup(program, profile, duration):
    wrapped, records, calls = provider(program=program, profile=profile)
    now, sequence = finish_preparation(wrapped)
    assert calls == [seed(99)]
    assert wrapped.origin == seed(99) and wrapped.elapsed_s == 0
    assert wrapped.duration_s == duration
    event = next(row for row in records if row["event"] == "am1_scripted_seed")
    assert event["observation_sequence"] == sequence and event["origin"] == seed(99)
    wrapped.advance(now + 9, seed(99), sequence + 1)
    assert wrapped.elapsed_s == pytest.approx(.1)
    wrapped.freeze()
    wrapped.admit(now + 30)
    wrapped.advance(now + 35, seed(99), sequence + 2)
    assert wrapped.elapsed_s == pytest.approx(.2)
    assert len(calls) == 1


def test_preparation_stays_in_command_range_at_original_slew_rate():
    wrapped, _, _ = provider()
    wrapped.admit(0)
    previous = 100.0
    for sequence in range(1, 80):
        now = sequence / 10
        observed = {"arm_" + key: value for key, value in wrapped.get_action().items()}
        wrapped.advance(now, observed, sequence)
        action = wrapped.get_action()
        goal = action[SHOULDER.removeprefix("arm_")]
        assert -100 <= goal <= 100
        assert abs(goal - previous) <= .075 + 1e-12
        assert all(action[key.removeprefix("arm_")] == 30 for key in KEYS if key != SHOULDER)
        previous = goal
        if not wrapped.preparing:
            break
    assert not wrapped.preparing


@pytest.mark.parametrize("shoulder", [120.01, -120.01, float("nan")])
def test_unsupported_initial_shoulder_refuses(shoulder):
    with pytest.raises(ValueError):
        provider(shoulder=shoulder)


def test_any_other_out_of_range_joint_refuses():
    initial = seed()
    initial["arm_right_wrist_flex.pos"] = 100.1
    with pytest.raises(ValueError):
        PreparedScriptedInput(initial, joint_keys=KEYS, fps=10, provider=ScriptedLeaderInput,
                              motion_profile="ArmSmoke", emit=lambda _: None)


def test_freeze_never_resets_preparation_wall_deadline_or_manufactures_program_seed():
    wrapped, records, calls = provider()
    wrapped.admit(10)
    wrapped.advance(10.1, seed(105), 1)
    wrapped.freeze()
    with pytest.raises(ValueError, match="20.*preparation"):
        wrapped.admit(30.1)
    assert calls == [] and wrapped.elapsed_s == 0
    assert not any(row["event"] == "am1_scripted_seed" for row in records)
    wrapped.finish("preparation_deadline")
    summary = next(row for row in records if row["event"] == "am1_scripted_preparation_summary")
    assert summary["preparation_wall_s"] == pytest.approx(20.1)


def test_persistent_raw_boundary_error_cannot_qualify_by_clipping_or_timeout():
    wrapped, _, calls = provider()
    wrapped.admit(0)
    with pytest.raises(ValueError, match="boundary.*converge"):
        for sequence in range(1, 100):
            wrapped.advance(sequence / 10, seed(104.16), sequence)
    assert calls == [] and wrapped.preparing and wrapped.elapsed_s == 0


def test_small_outside_boundary_feedback_may_move_inward_but_cannot_seed_until_inside():
    wrapped, _, calls = provider()
    wrapped.admit(0)
    for sequence in range(1, 36):
        wrapped.advance(sequence / 10, seed(100.166), sequence)
    assert wrapped.get_action()[SHOULDER.removeprefix("arm_")] == 99
    assert calls == [] and wrapped.elapsed_s == 0
    for sequence in range(36, 70):
        wrapped.advance(sequence / 10, seed(99.5), sequence)
        if not wrapped.preparing:
            break
    assert calls == [seed(99.5)]


def test_growing_error_and_oscillation_abort_the_finite_trial():
    wrapped, _, calls = provider()
    wrapped.admit(0)
    with pytest.raises(ValueError, match="error.*grew"):
        wrapped.advance(.1, seed(107), 1)
    assert calls == []
    wrapped, _, _ = provider()
    wrapped.admit(0)
    with pytest.raises(ValueError, match="oscillat"):
        for sequence, shoulder in enumerate([102, 98, 102, 98], 1):
            wrapped.advance(sequence / 10, seed(shoulder), sequence)



def test_preparation_pause_discards_progress_and_rejects_repeated_feedback():
    wrapped, _, _ = provider()
    wrapped.admit(0)
    wrapped.advance(.1, seed(105), 1)
    wrapped.freeze()
    wrapped.advance(5, seed(100), 2)
    assert wrapped.preparation_elapsed_s == pytest.approx(.1)
    wrapped.admit(6)
    wrapped.advance(7, seed(100), 3)
    assert wrapped.preparation_elapsed_s == pytest.approx(.2)
    with pytest.raises(ValueError, match="sequence"):
        wrapped.advance(7.1, seed(100), 3)


def test_deferred_events_perform_no_emit_inside_the_native_admission_lock():
    wrapped, records, _ = provider()
    wrapped.admit(0)
    initial = len(records)
    for sequence in range(1, 4):
        wrapped.advance(sequence / 10, seed(100), sequence, defer_events=True)
    assert len(records) == initial
    wrapped.flush_events()
    assert any(row["event"] == "am1_scripted_preparation_boundary_qualified" for row in records)


def test_prepare_does_not_replace_original_frozen_seed_after_feedback_changes():
    wrapped, records, calls = provider()
    now, sequence = finish_preparation(wrapped)
    wrapped.advance(now + .1, seed(98), sequence + 1)
    assert wrapped.origin == seed(99)
    assert calls == [seed(99)]
    assert len([row for row in records if row["event"] == "am1_scripted_seed"]) == 1



def test_resume_cannot_regress_the_preparation_monotonic_clock():
    wrapped, _, _ = provider()
    wrapped.admit(10)
    wrapped.advance(10.1, seed(105), 1)
    wrapped.freeze()
    with pytest.raises(ValueError, match="clock.*regress"):
        wrapped.admit(9)


def test_negative_boundary_preparation_is_inward_and_uses_one_genuine_seed():
    wrapped, records, calls = provider(shoulder=-105)
    assert wrapped.get_action()[SHOULDER.removeprefix("arm_")] == -100
    finish_preparation(wrapped)
    assert calls == [seed(-99)]
    assert wrapped.origin == seed(-99)
    seed_event = next(row for row in records if row["event"] == "am1_scripted_seed")
    assert "observed_at" not in seed_event
    assert seed_event["prepared_at"] > 0
