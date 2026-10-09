"""Scoped remapped virtual excursions; fake feedback and no hardware access."""
from __future__ import annotations

import json

import pytest
from test_alohamini_scripted_leader import LocalHarness, arguments, module, pose

SHOULDER = "arm_left_shoulder_lift.pos"
AMPLITUDE_ENV = "AM1_SCRIPTED_LEFT_SHOULDER_AMPLITUDE"


def test_reduced_excursion_changes_only_selected_joint_and_keeps_cycle_timing():
    m = module()
    initial = pose(m)
    records = []
    provider = m.ScriptedLeaderInput(
        initial, joint_keys=m.AM1_ARM_POSITION_KEYS, fps=10, emit=records.append,
        joint_amplitudes={SHOULDER: 1.5},
    )
    provider.admit(0)
    maximum = dict.fromkeys(initial, 0.0)
    for sequence in range(1, 881):
        provider.advance(sequence / 10, initial, sequence)
        action = provider.get_action()
        for key, origin in initial.items():
            maximum[key] = max(maximum[key], abs(action[key.removeprefix("arm_")] - origin))
    assert provider.complete and provider.elapsed_s == 88
    assert maximum[SHOULDER] == pytest.approx(1.5)
    assert all(maximum[key] == pytest.approx(3) for key in initial if key != SHOULDER)
    assert provider.get_action() == {key.removeprefix("arm_"): value for key, value in initial.items()}
    plans = [row for row in records if row["event"] == "am1_scripted_segment_plan"]
    selected = next(row for row in plans if row["joint"] == SHOULDER)
    assert selected["amplitude"] == 1.5 and selected["reduced"] is True
    assert selected["recipe"] == "mapped_physical_excursion" and selected["timing_changed"] is False
    assert all(row["ramp_s"] == 3 and row["endpoint_hold_s"] == .5 for row in plans)


@pytest.mark.parametrize("amplitudes", [
    {SHOULDER: 0}, {SHOULDER: -1}, {SHOULDER: 3.01},
    {SHOULDER: float("nan")}, {SHOULDER: float("inf")}, {"wrong.pos": 1},
])
@pytest.mark.parametrize("provider_name", ["ScriptedLeaderInput", "ArmSmokeRepeatInput"])
def test_invalid_excursion_override_refuses_before_any_plan(amplitudes, provider_name):
    m = module()
    records = []
    with pytest.raises(ValueError, match="amplitude"):
        getattr(m, provider_name)(pose(m), joint_keys=m.AM1_ARM_POSITION_KEYS, fps=10,
                                 emit=records.append, joint_amplitudes=amplitudes)
    assert records == []


def test_hold_body_rejects_explicit_excursion_override():
    m = module()
    with pytest.raises(ValueError, match="hold"):
        m.ArmHoldBodyInput(pose(m), joint_keys=m.AM1_ARM_POSITION_KEYS, fps=10,
                          joint_amplitudes={SHOULDER: 1.5})


def test_repeat_uses_one_frozen_override_and_original_seed_in_all_four_cycles():
    m = module()
    original = pose(m)
    amplitudes = {SHOULDER: 1.5}
    records = []
    provider = m.ArmSmokeRepeatInput(original, joint_keys=m.AM1_ARM_POSITION_KEYS, fps=10,
                                   emit=records.append, joint_amplitudes=amplitudes)
    amplitudes[SHOULDER] = 3
    provider.admit(0)
    for sequence in range(1, 3701):
        observed = {"arm_" + key: value for key, value in provider.get_action().items()}
        provider.advance(sequence / 10, observed, sequence)
        if provider.complete:
            break
    assert provider.complete and provider.elapsed_s == 352
    assert provider.cycles_completed == provider.returns_qualified == 4
    plans = [row for row in records if row["event"] == "am1_scripted_segment_plan"]
    selected = [row for row in plans if row["joint"] == SHOULDER]
    assert [row["cycle"] for row in selected] == [1, 2, 3, 4]
    assert all(row["origin"] == 12 and row["target"] == 13.5 for row in selected)
    assert all(row["amplitude"] == 3 for row in plans if row["joint"] != SHOULDER)
    assert provider.origin == original


def test_repeat_reduced_joint_return_refuses_drift_outside_retained_excursion():
    m = module()
    initial = pose(m)
    provider = m.ArmSmokeRepeatInput(initial, joint_keys=m.AM1_ARM_POSITION_KEYS, fps=10,
                                   emit=lambda _: None, joint_amplitudes={SHOULDER: 1.5})
    provider.admit(0)
    for sequence in range(1, 881):
        provider.advance(sequence / 10, initial, sequence)
    drift = dict(initial)
    drift[SHOULDER] = 14
    with pytest.raises(ValueError, match="return"):
        provider.advance(88.1, drift, 881)
    assert provider.elapsed_s == 88 and provider.returns_qualified == 0
    assert provider.origin == initial


def test_repeat_reduced_return_limit_keeps_other_joints_original_envelope():
    m = module()
    initial = pose(m)
    records = []
    provider = m.ArmSmokeRepeatInput(initial, joint_keys=m.AM1_ARM_POSITION_KEYS, fps=10,
                                   emit=records.append, joint_amplitudes={SHOULDER: 1.5})
    provider.admit(0)
    for sequence in range(1, 881):
        provider.advance(sequence / 10, initial, sequence)
    observed = dict(initial)
    observed[SHOULDER] = 13.4
    observed["arm_right_shoulder_lift.pos"] = 14.5
    for sequence, now in [(881, 88.1), (882, 88.2), (883, 88.3)]:
        provider.advance(now, observed, sequence)
    assert provider.returns_qualified == 1 and provider.origin == initial
    qualified = next(row for row in records if row["event"] == "am1_scripted_return_qualified")
    assert qualified["maximum_return_error"] == 2.5
    assert qualified["return_error_limits"][SHOULDER] == 1.5
    assert qualified["return_error_limits"]["arm_right_shoulder_lift.pos"] == 3


@pytest.mark.parametrize("raw", ["", " ", " 1.5", "1.5 ", "nan", "inf", "0", "-1", "3.01", "1_0", "one"])
def test_invalid_environment_override_refuses_during_argument_validation(monkeypatch, tmp_path, raw):
    m = module()
    monkeypatch.setenv(AMPLITUDE_ENV, raw)
    with pytest.raises(SystemExit):
        m.parse_args(arguments(tmp_path / "stop"), platform_name="Windows")


@pytest.mark.parametrize("replacement", [
    ["--leader_source", "physical"], ["--robot_model", "alohamini2"],
    ["--motion_profile", "ArmHoldBody", "--duration_s", "12"],
])
def test_environment_override_cannot_escape_scripted_arm_scope(monkeypatch, tmp_path, replacement):
    m = module()
    monkeypatch.setenv(AMPLITUDE_ENV, "1.5")
    with pytest.raises(SystemExit):
        m.parse_args(arguments(tmp_path / "stop") + replacement, platform_name="Windows")


@pytest.mark.parametrize("prepare", [False, True])
def test_actual_native_construction_preserves_override_through_preparation(
    monkeypatch, tmp_path, capsys, prepare,
):
    m = module()
    monkeypatch.setenv(AMPLITUDE_ENV, "1.5")
    if prepare:
        monkeypatch.setenv("AM1_SCRIPTED_PREPARE", "1")
    else:
        monkeypatch.delenv("AM1_SCRIPTED_PREPARE", raising=False)
    harness = LocalHarness(monkeypatch, m)
    assert harness.run(m, tmp_path) == 0
    assert harness.provider.targets[SHOULDER] == 13.5
    assert harness.provider.origin == harness.initial
    assert harness.provider.duration_s == 88
    output = capsys.readouterr().out
    plans = [json.loads(line) for line in output.splitlines()
             if line.startswith("{") and json.loads(line).get("event") == "am1_scripted_segment_plan"]
    selected = next(row for row in plans if row["joint"] == SHOULDER)
    assert selected["amplitude"] == 1.5 and selected["recipe"] == "mapped_physical_excursion"
    assert sum(event[0] == "admitted" for event in harness.events) == 1
