"""Pinned moving-leg response windows use original native acquisitions without IO."""

import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from examples.alohamini import am1_session_contract as contract
from lerobot.robots.alohamini import alohamini_host as host
from tests.robots.test_alohamini_lift_motor_feedback import status
from tests.robots.test_am1_selected_joint_evidence import context, visible_register_packet, visible_robot


def descriptor(*, historical=False, **changes):
    name = (
        "SELECTED_ELBOW_EXPERIMENT_ENVELOPE_SHA256"
        if historical
        else "SELECTED_ELBOW_EXPERIMENT_RESPONSE_ENVELOPE_SHA256"
    )
    pin = getattr(contract, name, None)
    assert pin is not None, "separately pinned response envelope is missing"
    return contract.SelectedElbowExperiment.from_payload(
        {
            "schema_version": 1,
            "envelope_sha256": pin,
            "excursion_norm": 10.0,
            "ramp_s": 10.0,
            "endpoint_dwell_s": 2.0,
            "current_cap_ma": 100.0 if historical else 150.0,
            "live_ceiling_s": 60.0,
            "minimum_response_raw": 55,
            "return_tolerance_raw": 20,
            "goal_readback_checkpoints_s": [18.0, 19.0],
            **changes,
        }
    )


def guarded(monkeypatch, *, historical=False, excursion=10.0):
    obj, evidence, rows, identity, port, reads, writes = visible_robot(
        monkeypatch,
        [
            status(visible_register_packet(), motor_id=3),
            status(bytes([91]), motor_id=3),
            status(visible_register_packet(torque=1), motor_id=3),
            status(bytes([91]), motor_id=3),
        ],
    )
    definition = descriptor(historical=historical, excursion_norm=excursion)
    obj._am1_selected_elbow_experiment = definition
    identity.update(recipe=definition.recipe_name, experiment_sha256=definition.sha256)
    now = [0.5]
    evidence.clock = lambda: now[0]
    evidence.prepare_visible(obj, identity)
    evidence.verify_visible_active(obj)
    control = host.AM1LocalControl(
        **{key: identity[key] for key in ("run_id", "host_incarnation")},
        selected_evidence=evidence,
    )
    control.state, control.epoch = "active", 0
    sequence = [0]
    original_counts = (len(port.writes), len(reads), len(writes), len(rows))

    def tick(at, raw=2000, current=12, progress=None, *, mutate=None, run_guard=True):
        now[0] = at
        progress = 5 + at - 1 if progress is None else progress
        cycle, phase, fraction = definition.phase(progress)
        metadata = context(
            profile=definition.profile,
            progress_s=progress,
            phase=phase,
            cycle=cycle,
            reference=0.0,
            endpoint_target=excursion,
            requested_target=excursion * fraction,
            sample_key=definition.sample_key(progress),
            acquisition_s=0.0,
            admitted_reference=0.0,
            experiment_sha256=definition.sha256,
        )
        evidence.prepare(
            obj,
            {
                "arm_left_elbow_flex.pos": metadata["requested_target"],
                "x.vel": 0,
                "y.vel": 0,
                "theta.vel": 0,
                "lift_axis.vel": 0,
                "_am1_selected_joint_evidence": metadata,
            },
            control.binding,
        )
        sequence[0] += 1
        control.accepted_action_sequence = sequence[0]
        control.observation_id = sequence[0]
        obj._am1_selected_joint_action = {
            "requested": metadata["requested_target"],
            "expected_goal_raw": definition.encoded_goal(
                metadata["requested_target"],
                {
                    "drive_mode": 0,
                    "homing_offset": 1845,
                    "range_min": 1000,
                    "range_max": 3000,
                },
            ),
            "sync_write_returned": True,
            "write_started_at_monotonic_s": at - 0.03,
            "write_completed_at_monotonic_s": at - 0.025,
        }
        obj._am1_selected_joint_position = {
            "raw": raw,
            "normalized": (raw - 1000) / 10 - 100,
            "read_started_at_monotonic_s": at - 0.02,
            "read_completed_at_monotonic_s": at - 0.01,
        }
        obj._am1_selected_joint_current = {
            "raw": current,
            "ma": current * 6.5,
            "read_started_at_monotonic_s": at - 0.005,
            "read_completed_at_monotonic_s": at,
        }
        observation = {}
        control.annotate(observation, acquired_at=at)
        observation[host.AM1_LOCAL_FEEDBACK_KEY]["read_started_at_monotonic_s"] = at - 0.02
        if mutate is not None:
            mutate(obj, observation)
        if run_guard:
            evidence.guard_recovery(obj, control, observation)
        assert (len(port.writes), len(reads), len(writes), len(rows)) == original_counts
        return observation

    return SimpleNamespace(
        robot=obj,
        evidence=evidence,
        control=control,
        definition=definition,
        tick=tick,
        now=now,
        counts=original_counts,
        port=port,
        reads=reads,
        writes=writes,
        rows=rows,
    )


def test_separate_150_pin_preserves_literal_initial_envelope_and_payload_shape():
    old = descriptor(historical=True)
    assert contract.SELECTED_ELBOW_EXPERIMENT_ENVELOPE["maximum_current_ma"] == 100
    assert old.envelope_sha256 == "62d3b365458639a3b3f436cb4da9d7314060bf8898c24f3edafd6e87ff73c742"
    assert old.response_rule is None
    new = descriptor()
    assert new.envelope_sha256 != old.envelope_sha256
    assert new.response_rule == {"commanded_error_raw": 30, "minimum_response_raw": 3, "window_s": 1.2}
    with pytest.raises(TypeError):
        new.response_rule["window_s"] = 2
    assert (
        new.sha256
        == hashlib.sha256(json.dumps(new.payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    )
    assert set(new.payload) == set(old.payload)
    with pytest.raises(ValueError):
        descriptor(historical=True, current_cap_ma=150)
    with pytest.raises(ValueError):
        descriptor(current_cap_ma=150.01)


@pytest.mark.parametrize("excursion", [10.0, -10.0])
def test_moving_goals_and_current_dips_do_not_erase_nonresponse(monkeypatch, excursion):
    g = guarded(monkeypatch, excursion=excursion)
    g.tick(1)
    g.tick(1.4, current=5)
    g.tick(1.8, current=13)
    with pytest.raises(RuntimeError, match="response"):
        g.tick(2.21, current=4)
    fault = g.robot._am1_selected_joint_fault
    assert fault["source"] == "response_guard"
    assert fault["current"]["raw"] == 4
    assert fault["response_boundary"]["signed_response_raw"] == 0
    assert fault["response_boundary"]["elapsed_s"] >= 1.2
    assert fault["response_boundary"]["distinct_sample_count"] == 4
    assert len(json.dumps(fault)) < 4096


@pytest.mark.parametrize("excursion", [10.0, -10.0])
def test_three_signed_ticks_qualify_each_window_without_goal_or_current_reset(monkeypatch, excursion):
    g = guarded(monkeypatch, excursion=excursion)
    sign = 1 if excursion > 0 else -1
    for at, ticks, current in [(1, 0, 12), (1.4, 1, 4), (1.8, 3, 14), (2.5, 5, 5), (2.6, 6, 13)]:
        g.tick(at, raw=2000 + sign * ticks, current=current)
    assert getattr(g.robot, "_am1_selected_joint_fault", None) is None
    assert g.evidence.response_boundary["status"] == "pending"
    assert g.evidence.response_boundary["anchor_position_raw"] == 2000 + sign * 6
    assert g.evidence.response_boundary["signed_response_raw"] == 0
    assert g.evidence.response_boundary["distinct_sample_count"] == 1


@pytest.mark.parametrize("ticks", [[0, 2, 1, 2, 2], [0, -1, 0, -1, -1]])
def test_noise_and_wrong_direction_cannot_satisfy_response(monkeypatch, ticks):
    g = guarded(monkeypatch)
    for at, raw_delta in zip([1, 1.3, 1.6, 1.9], ticks, strict=False):
        g.tick(at, raw=2000 + raw_delta)
    with pytest.raises(RuntimeError, match="response"):
        g.tick(2.21, raw=2000 + ticks[-1])


def test_error_below_30_is_tracking_without_tiny_precision_demand(monkeypatch):
    g = guarded(monkeypatch)
    g.tick(1, raw=2000)
    g.tick(1.3, raw=2010)
    for at in (1.6, 2.0, 2.4):
        g.tick(at, raw=2020)
    assert g.evidence.response_boundary["status"] == "tracking"


@pytest.mark.parametrize("cache", ["position", "current"])
def test_duplicate_acquisition_cannot_supply_three_ticks_or_reset_window(monkeypatch, cache):
    g = guarded(monkeypatch)
    g.tick(1)
    old = copy.deepcopy(getattr(g.robot, f"_am1_selected_joint_{cache}"))

    def duplicate(obj, _):
        current = getattr(obj, f"_am1_selected_joint_{cache}")
        current.update(
            {key: old[key] for key in ("read_started_at_monotonic_s", "read_completed_at_monotonic_s")}
        )

    if cache == "position":
        with pytest.raises(RuntimeError, match="ordering"):
            g.tick(1.2, raw=2003, mutate=duplicate)
        assert g.evidence.response_boundary["signed_response_raw"] == 0
        return
    g.tick(1.2, raw=2003, mutate=duplicate)
    assert g.evidence.response_boundary["signed_response_raw"] == 0
    g.tick(1.3, raw=2002)
    with pytest.raises(RuntimeError, match="response"):
        g.tick(2.21, raw=2002)


def test_response_first_observed_after_deadline_cannot_retroactively_qualify(monkeypatch):
    g = guarded(monkeypatch)
    g.tick(1)
    g.tick(1.6, raw=2001)
    with pytest.raises(RuntimeError, match="response"):
        g.tick(2.21, raw=2003)


def test_three_ticks_at_exact_boundary_qualify_with_original_start_and_completion_clocks(monkeypatch):
    g = guarded(monkeypatch)
    g.tick(1)
    g.tick(2.2, raw=2003)
    boundary = g.evidence.response_boundary
    assert boundary["status"] == "pending"
    assert boundary["anchor_position_started_at_monotonic_s"] == pytest.approx(2.18)
    assert boundary["anchor_current_started_at_monotonic_s"] == pytest.approx(2.195)
    assert boundary["last_current_completed_at_monotonic_s"] == 2.2


def test_valid_shrinking_pair_skew_cannot_credit_a_late_position_response(monkeypatch):
    g = guarded(monkeypatch)

    def anchor_skew(obj, observation):
        obj._am1_selected_joint_action.update(
            write_started_at_monotonic_s=0.96,
            write_completed_at_monotonic_s=0.97,
        )
        obj._am1_selected_joint_position.update(
            read_started_at_monotonic_s=0.98,
            read_completed_at_monotonic_s=0.99,
        )
        observation[host.AM1_LOCAL_FEEDBACK_KEY]["read_started_at_monotonic_s"] = 0.98

    def later_skew(obj, observation):
        obj._am1_selected_joint_action.update(
            write_started_at_monotonic_s=2.20,
            write_completed_at_monotonic_s=2.21,
        )
        obj._am1_selected_joint_position.update(
            read_started_at_monotonic_s=2.22,
            read_completed_at_monotonic_s=2.23,
        )
        obj._am1_selected_joint_current.update(
            read_started_at_monotonic_s=2.2305,
            read_completed_at_monotonic_s=2.231,
        )
        observation[host.AM1_LOCAL_FEEDBACK_KEY]["read_started_at_monotonic_s"] = 2.22

    g.tick(1.20, progress=5, mutate=anchor_skew)
    with pytest.raises(RuntimeError, match="response"):
        g.tick(2.231, raw=2003, progress=6.2, mutate=later_skew)
    fault = g.robot._am1_selected_joint_fault
    assert fault["source"] == "response_guard"
    assert fault["response_boundary"]["failure_kind"] == "nonresponse"
    assert fault["response_boundary"]["elapsed_s"] == pytest.approx(1.24)
    assert fault["response_boundary"]["signed_response_raw"] == 3
    assert fault["position"]["read_completed_at_monotonic_s"] == 2.23
    assert fault["current"]["read_completed_at_monotonic_s"] == 2.231


def test_protective_pause_after_rearm_retains_actual_new_pending_anchor(monkeypatch):
    g = guarded(monkeypatch)
    g.tick(1)
    g.tick(1.8, raw=2003)
    assert g.evidence.deactivate(g.robot) is None
    g.control.state, g.control.epoch = "paused", 1
    g.evidence.guard_recovery(g.robot, g.control, {})
    assert getattr(g.robot, "_am1_selected_joint_fault", None) is None
    g.control.state, g.control.epoch = "active", 2
    with pytest.raises(RuntimeError, match="response"):
        g.tick(2, raw=2006)
    fault = g.robot._am1_selected_joint_fault
    boundary = fault["response_boundary"]
    assert fault["source"] == "response_guard" and boundary["failure_kind"] == "continuity_lost"
    assert boundary["anchor_position_raw"] == fault["position"]["raw"] == 2003
    assert boundary["anchor_position_completed_at_monotonic_s"] == pytest.approx(1.79)
    assert boundary["anchor_current_completed_at_monotonic_s"] == 1.8
    assert boundary["distinct_sample_count"] == 1 and boundary["signed_response_raw"] == 0
    assert boundary["elapsed_s"] == 0
    assert (len(g.port.writes), len(g.reads), len(g.writes), len(g.rows)) == g.counts


def test_fresh_subthreshold_error_qualifies_tracking_without_three_tick_precision(monkeypatch):
    g = guarded(monkeypatch)
    g.tick(1)
    g.tick(1.1, raw=2002)
    assert g.evidence.response_boundary["signed_response_raw"] == 2
    assert g.evidence.response_boundary["signed_commanded_error_raw"] == 29
    assert g.evidence.response_boundary["status"] == "tracking"


def test_subthreshold_two_tick_jitter_does_not_restart_original_exposure(monkeypatch):
    g = guarded(monkeypatch)
    for at, raw in [(1, 2070), (1.2, 2072), (1.4, 2070), (1.6, 2072), (1.8, 2070), (2, 2072)]:
        g.tick(at, raw=raw, progress=11 + at)
    with pytest.raises(RuntimeError, match="response"):
        g.tick(2.21, raw=2070, progress=13.21)
    boundary = g.robot._am1_selected_joint_fault["response_boundary"]
    assert boundary["anchor_position_completed_at_monotonic_s"] == 0.99
    assert boundary["signed_response_raw"] == 0


@pytest.mark.parametrize("stale", [False, True])
def test_only_current_valid_subthreshold_tracking_can_retire_at_protective_pause(monkeypatch, stale):
    g = guarded(monkeypatch)
    g.tick(1)
    g.tick(1.1, raw=2002)
    g.now[0] = 1.7 if stale else 1.15
    assert g.evidence.deactivate(g.robot) is None
    if stale:
        with pytest.raises(RuntimeError, match="response"):
            g.tick(1.8, raw=2002)
    else:
        g.tick(1.3, raw=2002)
        assert getattr(g.robot, "_am1_selected_joint_fault", None) is None


@pytest.mark.parametrize("stale", [False, True])
def test_only_current_tracking_can_retire_at_leg_transition(monkeypatch, stale):
    g = guarded(monkeypatch)
    g.tick(1, raw=2070, progress=12)
    g.tick(1.1, raw=2072, progress=12.1)
    if stale:
        with pytest.raises(RuntimeError, match="response"):
            g.tick(1.8, raw=2072, progress=14)
    else:
        g.tick(1.2, raw=2072, progress=14)
        assert g.evidence.response_boundary["status"] == "tracking"


def test_missing_tracking_cache_cannot_obstruct_protective_deactivation(monkeypatch):
    g = guarded(monkeypatch)
    g.tick(1)
    g.tick(1.1, raw=2002)
    del g.robot._am1_selected_joint_current
    assert g.evidence.deactivate(g.robot) is None
    assert g.evidence.response_boundary["status"] == "continuity_lost"


def test_endpoint_with97_ticks_and_subthreshold_error_reverses_normally(monkeypatch):
    g = guarded(monkeypatch)
    g.tick(1, raw=2097, progress=12)
    g.tick(1.2, raw=2097, progress=14)
    assert g.evidence.response_boundary["status"] == "tracking"
    assert getattr(g.robot, "_am1_selected_joint_fault", None) is None


def test_exact_duplicate_frame_and_clocks_neither_credit_nor_renew_window(monkeypatch):
    g = guarded(monkeypatch)
    observation = g.tick(1)
    before = copy.deepcopy(g.evidence.response_boundary)
    g.now[0] = 1.2
    g.evidence.guard_recovery(g.robot, g.control, observation)
    assert g.evidence.response_boundary == before
    with pytest.raises(RuntimeError, match="response"):
        g.tick(2.21)


@pytest.mark.parametrize(
    "invalid", ["backwards", "stale", "future", "foreign", "missing_context", "wrong_goal"]
)
def test_invalid_acquisition_or_active_context_cannot_reset_exposure(monkeypatch, invalid):
    g = guarded(monkeypatch)
    g.tick(1)
    g.tick(1.2, raw=2001)
    anchor = g.evidence.response_boundary["anchor_position_raw"]

    def corrupt(obj, observation):
        if invalid == "backwards":
            obj._am1_selected_joint_current.update(
                read_started_at_monotonic_s=1.04, read_completed_at_monotonic_s=1.05
            )
        elif invalid == "stale":
            obj._am1_selected_joint_position.update(
                read_started_at_monotonic_s=0.1, read_completed_at_monotonic_s=0.2
            )
        elif invalid == "future":
            obj._am1_selected_joint_current["read_completed_at_monotonic_s"] = 2
        elif invalid == "foreign":
            observation[host.AM1_LOCAL_FEEDBACK_KEY]["run_id"] = "foreign"
        elif invalid == "missing_context":
            g.evidence.context = None
        else:
            obj._am1_selected_joint_action["expected_goal_raw"] += 1

    with pytest.raises(RuntimeError):
        g.tick(1.4, raw=2005, mutate=corrupt)
    assert g.evidence.response_boundary["anchor_position_raw"] == anchor


def test_same_direction_phase_transition_keeps_pending_window(monkeypatch):
    g = guarded(monkeypatch)
    g.tick(1, raw=2060, progress=11)
    g.tick(1.4, raw=2061, progress=12)
    with pytest.raises(RuntimeError, match="response"):
        g.tick(2.21, raw=2062, progress=12.8)


def test_leg_reversal_cannot_silently_erase_pending_obligation(monkeypatch):
    g = guarded(monkeypatch)
    g.tick(1, raw=2060, progress=13.7)
    with pytest.raises(RuntimeError, match="response"):
        g.tick(1.2, raw=2060, progress=14)
    assert g.robot._am1_selected_joint_fault["response_boundary"]["anchor_position_raw"] == 2060


def test_protective_deactivation_is_unconditional_and_paused_motion_cannot_credit_resume(monkeypatch):
    g = guarded(monkeypatch)
    g.tick(1)
    assert g.evidence.deactivate(g.robot) is None
    assert g.evidence.deactivate(g.robot) is None
    g.control.state, g.control.epoch = "paused", 1
    g.now[0] = 1.3
    g.robot._am1_selected_joint_position.update(
        raw=2005, read_started_at_monotonic_s=1.28, read_completed_at_monotonic_s=1.29
    )
    g.robot._am1_selected_joint_current.update(
        read_started_at_monotonic_s=1.295, read_completed_at_monotonic_s=1.3
    )
    g.evidence.guard_recovery(g.robot, g.control, {})
    assert getattr(g.robot, "_am1_selected_joint_fault", None) is None
    g.control.state, g.control.epoch = "active", 2
    with pytest.raises(RuntimeError, match="response"):
        g.tick(1.4, raw=2005)
    boundary = g.robot._am1_selected_joint_fault["response_boundary"]
    assert boundary["signed_response_raw"] == 0
    assert boundary["last_position_completed_at_monotonic_s"] == pytest.approx(0.99)


@pytest.mark.parametrize("ordinary,current,cap", [(2000, 24, 150), (80, 14, 80)])
def test_cap_refusal_has_priority_and_keeps_original_current_clocks(monkeypatch, ordinary, current, cap):
    g = guarded(monkeypatch)
    g.robot._joint_current_limit_ma = ordinary
    if ordinary == 2000:
        g.tick(1)
    with pytest.raises(RuntimeError, match="effort"):
        g.tick(2.21, current=current)
    fault = g.robot._am1_selected_joint_fault
    assert fault["source"] == "cached_guard"
    assert fault["effective_limit_ma"] == cap
    assert fault["current"]["read_started_at_monotonic_s"] == pytest.approx(2.205)
    assert fault["current"]["read_completed_at_monotonic_s"] == 2.21


def test_historical100_keeps_existing_moving_goal_guard_behavior(monkeypatch):
    g = guarded(monkeypatch, historical=True)
    for at in (1, 1.4, 1.8, 2.21):
        g.tick(at, current=4)
    assert getattr(g.robot, "_am1_selected_joint_fault", None) is None
    assert g.definition.response_rule is None


def test_boundary_is_retained_in_existing_feedback_and_full_fault_serializer(monkeypatch):
    g = guarded(monkeypatch)
    observation = g.tick(1)
    g.evidence.publish(g.robot, g.control, observation)
    boundary = observation[host.AM1_LOCAL_FEEDBACK_KEY]["selected_joint"]["response_boundary"]
    assert boundary["anchor_position_raw"] == 2000
    assert len(host.build_observation_multipart(observation, [])[0]) < 65536
    with pytest.raises(RuntimeError, match="response"):
        g.tick(2.21)
    receipt = {"selected_joint_fault": g.robot._am1_selected_joint_fault}
    assert json.loads(json.dumps(receipt))["selected_joint_fault"]["response_boundary"]["elapsed_s"] >= 1.2
