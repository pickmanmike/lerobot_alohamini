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


def descriptor(*, historical=False, tracking=False, operation=False, qualified_dwell=False, **changes):
    name = (
        "SELECTED_ELBOW_EXPERIMENT_OPERATION_DWELL_ENVELOPE_SHA256"
        if qualified_dwell
        else "SELECTED_ELBOW_EXPERIMENT_OPERATION_ENVELOPE_SHA256"
        if operation
        else "SELECTED_ELBOW_EXPERIMENT_ENVELOPE_SHA256"
        if historical
        else "SELECTED_ELBOW_EXPERIMENT_TRACKING_ENVELOPE_SHA256"
        if tracking
        else "SELECTED_ELBOW_EXPERIMENT_RESPONSE_ENVELOPE_SHA256"
    )
    pin = getattr(contract, name, None)
    assert pin is not None, "separately pinned response envelope is missing"
    if qualified_dwell:
        return contract.SelectedElbowExperiment.from_payload(
            {**contract.LEFT_ELBOW_COARSE_BENCH_V2.payload, **changes}
        )
    return contract.SelectedElbowExperiment.from_payload(
        {
            "schema_version": 2 if operation else 1,
            "envelope_sha256": pin,
            "excursion_norm": 10.0,
            "ramp_s": 10.0,
            "endpoint_dwell_s": 2.0,
            "current_cap_ma": 100.0 if historical else 150.0,
            "live_ceiling_s": 90.0 if operation else 60.0,
            "minimum_response_raw": 56 if qualified_dwell else 55,
            "return_tolerance_raw": 22 if qualified_dwell else 20,
            "goal_readback_checkpoints_s": [18.0, 19.0],
            **({"cycle_count": 2, "intercycle_hold_s": 6} if operation else {}),
            **changes,
        }
    )


def operation_rejoin(elapsed=0):
    return {
        "phase": "rejoin",
        "sample_key": None,
        "requested_target": 1.7 - elapsed,
        "rejoin": {
            "held_target": 1.7,
            "retained_target": 0.0,
            "elapsed_s": elapsed,
            "duration_s": 1.7,
            "observation_id": 1,
            "acquired_at_monotonic_s": 1.9,
        },
    }


def test_operation_native_intercycle_hold_and_measured_rejoin_keep_frozen_progress(monkeypatch):
    g = guarded(monkeypatch, operation=True)
    g.tick(1.9, raw=2017, progress=28)
    g.evidence.deactivate(g.robot)
    g.tick(2.0, raw=2017, progress=28, context_overrides=operation_rejoin())
    g.tick(2.05, raw=2017, progress=28, context_overrides=operation_rejoin())
    g.tick(2.1, raw=2016, progress=28, context_overrides=operation_rejoin(0.1))
    assert g.evidence.progress == 28 and g.evidence.reference == 0
    assert g.evidence.response_boundary["direction"] == -1
    assert g.evidence.response_boundary["status"] == "tracking"


def test_operation_native_rejoin_refuses_a_clock_jump_before_actuation(monkeypatch):
    g = guarded(monkeypatch, operation=True)
    g.tick(1.9, raw=2017, progress=28)
    g.evidence.deactivate(g.robot)
    g.tick(2.0, raw=2017, progress=28, context_overrides=operation_rejoin())
    with pytest.raises(RuntimeError, match="rejoin.*rate"):
        g.tick(2.1, raw=2016, progress=28, context_overrides=operation_rejoin(0.2))


def test_operation_native_rejoin_cannot_erase_unresolved_response_on_pause(monkeypatch):
    g = guarded(monkeypatch, operation=True)
    g.tick(1.0, raw=2000, progress=7)
    assert g.evidence.response_boundary["status"] == "pending"
    g.evidence.deactivate(g.robot)
    with pytest.raises(RuntimeError, match="continuity"):
        g.tick(
            1.1,
            raw=2000,
            progress=7,
            context_overrides={
                "phase": "rejoin",
                "sample_key": None,
                "requested_target": 0.0,
                "rejoin": {
                    "held_target": 0.0,
                    "retained_target": 5.0,
                    "elapsed_s": 0.0,
                    "duration_s": 5.0,
                    "observation_id": 1,
                    "acquired_at_monotonic_s": 1.0,
                },
            },
        )


def guarded(
    monkeypatch,
    *,
    historical=False,
    excursion=10.0,
    tracking=False,
    operation=False,
    qualified_dwell=False,
    negative_hold=False,
    reference=0.0,
    calibration=None,
    clock_offset=0.0,
    definition_changes=None,
):
    material = calibration or {"drive_mode": 0, "homing_offset": 1845, "range_min": 1000, "range_max": 3000}
    seed_raw = contract.SelectedElbowExperiment.encoded_goal(reference, material)
    registers = {
        "range_min": material["range_min"],
        "range_max": material["range_max"],
        "goal": seed_raw,
        "present": seed_raw,
    }
    obj, evidence, rows, identity, port, reads, writes = visible_robot(
        monkeypatch,
        [
            status(visible_register_packet(**registers), motor_id=3),
            status(bytes([91]), motor_id=3),
            status(visible_register_packet(torque=1, **registers), motor_id=3),
            status(bytes([91]), motor_id=3),
        ],
    )

    obj.left_bus.calibration["arm_left_elbow_flex"] = type(obj.left_bus.calibration["arm_left_elbow_flex"])(
        id=3, **material
    )
    definition = descriptor(
        historical=historical, tracking=tracking, operation=operation, qualified_dwell=qualified_dwell,
        excursion_norm=10 if qualified_dwell and excursion == 10 else excursion,
        **(definition_changes or {})
    )
    if negative_hold:
        definition = contract.LEFT_ELBOW_NEGATIVE_COARSE_BENCH_V3
        operation, excursion = True, -10
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
    if negative_hold:
        control.intent_revision = 0
    sequence = [0]
    original_counts = (len(port.writes), len(reads), len(writes), len(rows))

    def tick(at, raw=None, current=12, progress=None, *, mutate=None, run_guard=True, context_overrides=None):
        progress = 5 + at - 1 if progress is None else progress
        at += clock_offset
        now[0] = at - .04 if negative_hold else at
        raw = seed_raw if raw is None else raw
        cycle, phase, fraction = definition.phase(progress)
        metadata = context(
            profile=definition.profile,
            progress_s=progress,
            phase=phase,
            cycle=cycle,
            reference=reference,
            endpoint_target=reference + excursion,
            requested_target=reference + excursion * fraction,
            sample_key=definition.sample_key(progress),
            acquisition_s=0.0,
            admitted_reference=reference,
            experiment_sha256=definition.sha256,
            **({"rejoin": None} if operation else {}),
        )
        metadata.update(context_overrides or {})
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
            **({"control_marker": {"version": 1, "mode": "active", **control.binding,
                "epoch": control.epoch, "intent_revision": control.intent_revision,
                "action_sequence": sequence[0] + 1}} if negative_hold else {}),
        )
        now[0] = at
        sequence[0] += 1
        control.accepted_action_sequence = sequence[0]
        control.observation_id = sequence[0]
        if negative_hold:
            control.accepted_action = {"arm_left_elbow_flex.pos": metadata["requested_target"],
                "x.vel": 0, "y.vel": 0, "theta.vel": 0, "lift_axis.vel": 0}
        obj._am1_selected_joint_action = {
            "requested": metadata["requested_target"],
            "expected_goal_raw": definition.encoded_goal(
                metadata["requested_target"],
                material,
            ),
            "sync_write_returned": True,
            "write_started_at_monotonic_s": at - 0.03,
            "write_completed_at_monotonic_s": at - 0.025,
        }
        obj._am1_selected_joint_position = {
            "raw": raw,
            "normalized": (raw - material["range_min"])
            / (material["range_max"] - material["range_min"])
            * 200
            - 100,
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


def startup_fixture(monkeypatch, *, tracking=False):
    from lerobot.robots.alohamini import alohamini as robot_module

    g = guarded(monkeypatch, tracking=tracking)
    g.control.state, g.control.epoch, g.control.intent_revision = "paused", 1, 0
    monkeypatch.setattr(robot_module.time, "monotonic", lambda: g.now[0])
    startup_observe(g, 0.8)
    return g


def startup_observe(g, at, *, raw=2000, current=0):
    g.now[0] = at
    g.robot._am1_selected_joint_position = {
        "raw": raw,
        "normalized": (raw - 1000) / 10 - 100,
        "read_started_at_monotonic_s": at - 0.02,
        "read_completed_at_monotonic_s": at - 0.01,
    }
    g.robot._am1_selected_joint_current = {
        "raw": current,
        "ma": current * 6.5,
        "read_started_at_monotonic_s": at - 0.005,
        "read_completed_at_monotonic_s": at,
    }
    observation = {}
    g.control.annotate(observation, acquired_at=at)
    g.evidence.guard_recovery(g.robot, g.control, observation)
    return observation


def startup_action(g, *, requested=0.0, sequence=12, metadata=None, **body):
    result = {
        "arm_left_elbow_flex.pos": requested,
        "x.vel": 0,
        "y.vel": 0,
        "theta.vel": 0,
        "lift_axis.vel": 0,
        host.AM1_LOCAL_CONTROL_KEY: {
            "version": 1,
            "mode": "active",
            "epoch": 2,
            "intent_revision": 0,
            "action_sequence": sequence,
            **g.control.binding,
        },
        **body,
    }
    if metadata is not None:
        result["_am1_selected_joint_evidence"] = metadata
    return result


def startup_send(g, action, at):
    g.now[0] = at
    assert g.control.apply(g.robot, action)
    observation = g.robot.get_observation()
    g.control.annotate(observation, acquired_at=at)
    g.evidence.guard_recovery(g.robot, g.control, observation)
    return observation


def test_actual_local_control_preparation_hold_then_first_context_is_supported(monkeypatch):
    g = startup_fixture(monkeypatch)
    startup_send(g, startup_action(g), 1)
    assert g.control.state == "active" and g.control.epoch == 2
    assert g.evidence.context is None and g.evidence.response_boundary is None
    startup_observe(g, 3.2)
    metadata = context(
        profile=g.definition.profile,
        progress_s=0,
        phase="baseline",
        cycle=0,
        reference=0.0,
        endpoint_target=10.0,
        requested_target=0.0,
        sample_key="baseline",
        acquisition_s=0.0,
        admitted_reference=0.0,
        experiment_sha256=g.definition.sha256,
    )
    startup_send(g, startup_action(g, sequence=13, metadata=metadata), 3.21)
    assert g.evidence.response_boundary["status"] == "tracking"
    before = (len(g.writes), len(g.port.writes))
    with pytest.raises(RuntimeError, match="response"):
        g.control.apply(g.robot, startup_action(g, sequence=14))
    assert (len(g.writes), len(g.port.writes)) == before
    assert g.control.accepted_action_sequence == 13
    # Protective Pause must still execute after the rejected unmarked command.
    protective = []
    monkeypatch.setattr(g.robot, "stop_motion", lambda: protective.append("stop"))
    monkeypatch.setattr(g.robot, "hold_follower_arms", lambda: protective.append("measured-hold"))
    pause = startup_action(g, sequence=15)
    pause[host.AM1_LOCAL_CONTROL_KEY].update(mode="pause", epoch=3)
    assert g.control.apply(g.robot, pause)
    assert g.control.state == "paused" and g.evidence.context is None
    assert protective == ["stop", "measured-hold"]


@pytest.mark.parametrize(
    "change",
    [{"requested": 3}, {"x.vel": 1}, {"lift_axis.vel": 0.1}, {"lift_axis.height_mm": 2}, {"requested": True}],
)
def test_unmarked_startup_movement_refuses_before_actual_send(monkeypatch, change):
    g = startup_fixture(monkeypatch)
    g.now[0] = 1
    before = (len(g.writes), len(g.port.writes), g.control.accepted_action_sequence)
    with pytest.raises(RuntimeError, match="response|startup"):
        g.control.apply(g.robot, startup_action(g, **change))
    assert (len(g.writes), len(g.port.writes), g.control.accepted_action_sequence) == before


def test_startup_initial_single_tick_settling_freezes_target_without_drift(monkeypatch):
    g = startup_fixture(monkeypatch)
    startup_observe(g, 0.9, raw=2001)
    startup_send(g, startup_action(g), 1)
    startup_observe(g, 1.1, raw=2001)
    before = (len(g.writes), g.control.accepted_action_sequence)
    with pytest.raises(RuntimeError, match="response|startup"):
        g.control.apply(g.robot, startup_action(g, requested=0.1, sequence=13))
    assert (len(g.writes), g.control.accepted_action_sequence) == before


def test_startup_hold_20_second_wall_is_not_the_moving_leg_window(monkeypatch):
    g = startup_fixture(monkeypatch)
    startup_send(g, startup_action(g), 1)
    startup_observe(g, 19.9)
    startup_send(g, startup_action(g, sequence=13), 20)
    startup_observe(g, 20.99)
    g.now[0] = 21
    before = len(g.writes)
    with pytest.raises(RuntimeError, match="20 seconds"):
        g.control.apply(g.robot, startup_action(g, sequence=14))
    assert len(g.writes) == before
    assert g.evidence.deactivate(g.robot) is None


@pytest.mark.parametrize("elapsed,accepted", [(19.999, True), (20.0, False), (20.001, False)])
@pytest.mark.parametrize("tracking", [False, True])
def test_first_finite_context_retains_original_startup_deadline_before_send(
    monkeypatch, elapsed, accepted, tracking
):
    g = startup_fixture(monkeypatch, tracking=tracking)
    startup_send(g, startup_action(g), 1.0)
    startup_observe(g, 1.0 + elapsed - 0.01)
    metadata = context(
        profile=g.definition.profile,
        progress_s=0,
        phase="baseline",
        cycle=0,
        reference=0.0,
        endpoint_target=10.0,
        requested_target=0.0,
        sample_key="baseline",
        acquisition_s=0.0,
        admitted_reference=0.0,
        experiment_sha256=g.definition.sha256,
    )
    action = startup_action(g, sequence=13, metadata=metadata)
    g.now[0] = 1.0 + elapsed
    before = (len(g.writes), len(g.port.writes), g.control.accepted_action_sequence)
    if accepted:
        startup_send(g, action, g.now[0])
        assert len(g.writes) > before[0] and g.control.accepted_action_sequence == 13
        assert g.evidence.profile == g.definition.profile
        assert g.evidence.response_boundary["status"] == "tracking"
    else:
        with pytest.raises(RuntimeError, match="startup measured hold exceeded 20 seconds"):
            g.control.apply(g.robot, action)
        assert (len(g.writes), len(g.port.writes), g.control.accepted_action_sequence) == before
        assert g.evidence.reference is None and g.evidence.profile is None and g.evidence.context is None
        assert g.evidence._response_startup_hold["started_at"] == 1.0


@pytest.mark.parametrize("route", ["prepare", "postread"])
@pytest.mark.parametrize("sign", [-1, 1])
def test_startup_hold_drift_at_30_raw_refuses_without_another_write(monkeypatch, route, sign):
    g = startup_fixture(monkeypatch)
    startup_send(g, startup_action(g), 1)
    # Subthreshold error remains an ordinary hold, without a precision/stall claim.
    startup_observe(g, 1.05, raw=2000 + sign * 29)
    g.now[0] = 1.1
    g.robot._am1_selected_joint_position.update(
        raw=2000 + sign * 30,
        normalized=sign * 3.0,
        read_started_at_monotonic_s=1.08,
        read_completed_at_monotonic_s=1.09,
    )
    g.robot._am1_selected_joint_current.update(
        read_started_at_monotonic_s=1.095,
        read_completed_at_monotonic_s=1.1,
    )
    before = (len(g.writes), len(g.port.writes), g.control.accepted_action_sequence)
    with pytest.raises(RuntimeError, match="startup"):
        if route == "prepare":
            g.control.apply(g.robot, startup_action(g, sequence=13))
        else:
            observation = {}
            g.control.annotate(observation, acquired_at=1.1)
            g.evidence.guard_recovery(g.robot, g.control, observation)
    assert (len(g.writes), len(g.port.writes), g.control.accepted_action_sequence) == before
    fault = g.robot._am1_selected_joint_fault
    assert fault["position"]["raw"] == 2000 + sign * 30
    assert fault["action"]["expected_goal_raw"] == 2000
    assert fault["response_boundary"]["failure_kind"] == "unavailable"


def test_startup_stale_original_cache_and_cap_refuse_before_send(monkeypatch):
    g = startup_fixture(monkeypatch)
    g.now[0] = 1.4
    before = len(g.writes)
    with pytest.raises(RuntimeError, match="acquisition|startup|response"):
        g.control.apply(g.robot, startup_action(g))
    assert len(g.writes) == before
    # A new fixture isolates actual cap priority over malformed startup motion.
    g = startup_fixture(monkeypatch)
    g.robot._am1_selected_joint_current.update(raw=24, ma=156.0)
    g.now[0] = 1
    before = len(g.writes)
    with pytest.raises(RuntimeError, match="effort"):
        g.control.apply(g.robot, startup_action(g, requested=3))
    assert len(g.writes) == before
    assert g.robot._am1_selected_joint_fault["source"] == "cached_guard"


@pytest.mark.parametrize("raw", [1224, 1655, 1686, 2312, 2399, 2458])
def test_startup_contract_keeps_measured_origin_separate_from_actual_floor_goal(raw):
    d = descriptor()
    calibration = {"drive_mode": 0, "homing_offset": 1845, "range_min": 864, "range_max": 3073}
    requested = (raw - 864) / 2209 * 200 - 100
    assert d.startup_hold_valid(requested, raw, calibration)
    assert d.encoded_goal(requested, calibration) in {raw, raw - 1}
    assert not d.startup_hold_valid(requested, raw + 1, calibration)
    assert not descriptor(historical=True).startup_hold_valid(requested, raw, calibration)


@pytest.mark.parametrize("raw", [1224, 1655, 1686, 2312, 2399, 2458])
def test_real_prepared_input_handoff_preserves_measured_reference_before_floor_encoding(raw):
    from functools import partial

    from examples.alohamini.am1_finite_task import JOINT_KEYS
    from examples.alohamini.am1_scripted_prepare import PreparedScriptedInput
    from examples.alohamini.scripted_leader import LeftElbowPositiveControlInput
    from tests.robots.test_am1_selected_elbow_profiles import seed

    d = descriptor()
    q0 = (raw - 864) / 2209 * 200 - 100
    initial = seed(q0)
    prepared = PreparedScriptedInput(
        initial,
        joint_keys=JOINT_KEYS,
        fps=10,
        provider=partial(LeftElbowPositiveControlInput, definition=d),
        motion_profile=d.profile,
        duration_s=d.nominal_trajectory_s,
        emit=lambda _: None,
    )
    prepared.admit(0)
    for sequence in range(1, 35):
        prepared.advance(sequence / 10, initial, sequence)
        if not prepared.preparing:
            break
    assert not prepared.preparing
    metadata = prepared.diagnostic_context
    assert metadata["progress_s"] == 0 and metadata["phase"] == "baseline"
    assert metadata["admitted_reference"] == metadata["reference"] == metadata["requested_target"] == q0
    assert prepared.get_action()["left_elbow_flex.pos"] == q0
    assert d.context_valid(metadata)


def test_first_finite_handoff_accepts_fresh_single_tick_seed_without_reopening_startup(monkeypatch):
    g = startup_fixture(monkeypatch)
    startup_send(g, startup_action(g), 1)
    startup_observe(g, 3.2, raw=2001)
    metadata = context(
        profile=g.definition.profile,
        progress_s=0,
        phase="baseline",
        cycle=0,
        reference=0.1,
        endpoint_target=10.1,
        requested_target=0.1,
        sample_key="baseline",
        acquisition_s=0.0,
        admitted_reference=0.1,
        experiment_sha256=g.definition.sha256,
    )
    g.now[0] = 3.21
    assert g.control.apply(g.robot, startup_action(g, requested=0.1, sequence=13, metadata=metadata))
    assert g.evidence.reference == 0.1 and g.evidence.profile == g.definition.profile
    g.evidence.deactivate(g.robot)
    before = len(g.writes)
    with pytest.raises(RuntimeError, match="response"):
        g.control.apply(g.robot, startup_action(g, sequence=14))
    assert len(g.writes) == before


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


def test_tracking45_is_separately_pinned_with_unchanged_effort_shape_and_startup30():
    initial, original, tracking = descriptor(historical=True), descriptor(), descriptor(tracking=True)
    assert initial.envelope_sha256 == "62d3b365458639a3b3f436cb4da9d7314060bf8898c24f3edafd6e87ff73c742"
    assert original.envelope_sha256 == "069b64bcb205f2ef4c188c804fc3d2f7a622179dfa098496dd71d8de0cf1084f"
    assert original.response_rule == {"commanded_error_raw": 30, "minimum_response_raw": 3, "window_s": 1.2}
    assert tracking.envelope_sha256 not in {initial.envelope_sha256, original.envelope_sha256}
    assert tracking.response_rule == {"commanded_error_raw": 45, "minimum_response_raw": 3, "window_s": 1.2}
    assert tracking.startup_hold_error_raw == original.startup_hold_error_raw == 30
    assert set(tracking.payload) == set(original.payload) == set(initial.payload)
    assert tracking.current_cap_ma == original.current_cap_ma == 150
    assert tracking.nominal_trajectory_s == original.nominal_trajectory_s == 28
    assert contract.SelectedElbowExperiment.from_payload(tracking.payload).summary() == tracking.summary()
    with pytest.raises(ValueError):
        descriptor(tracking=True, current_cap_ma=150.01)


@pytest.mark.parametrize("tracking", [False, True])
def test_actual05_endpoint_plateau38_40_distinguishes_tracking45_from_original30(monkeypatch, tracking):
    calibration = {"drive_mode": 0, "homing_offset": 1845, "range_min": 864, "range_max": 3073}
    reference = (2312 - 864) / 2209 * 200 - 100
    g = guarded(monkeypatch, tracking=tracking, reference=reference, calibration=calibration)
    g.tick(10.0, raw=2382, current=12, progress=11.8)
    g.tick(10.4, raw=2382, current=12, progress=12.2)
    g.tick(10.8, raw=2383, current=15, progress=12.6)
    if not tracking:
        with pytest.raises(RuntimeError, match="response"):
            g.tick(11.203055786, raw=2384, current=14, progress=12.9)
        fault = g.robot._am1_selected_joint_fault
        assert fault["response_boundary"]["signed_response_raw"] == 2
        assert fault["response_boundary"]["signed_commanded_error_raw"] == 38
        return
    g.tick(11.203055786, raw=2384, current=14, progress=12.9)
    assert g.evidence.response_boundary["status"] == "tracking"
    assert g.evidence.response_boundary["last_encoded_goal_raw"] == 2422
    assert g.evidence.response_boundary["signed_commanded_error_raw"] == 38
    g.tick(11.303055786, raw=2384, current=14, progress=14)
    assert g.evidence.response_boundary["direction"] == -1
    assert g.evidence.response_boundary["status"] == "tracking"
    assert getattr(g.robot, "_am1_selected_joint_fault", None) is None


@pytest.mark.parametrize("excursion", [-10.0, 10.0])
@pytest.mark.parametrize("response", [0, 2, 3])
def test_tracking45_large_deficit_retains_signed3_response_maxclock1_2_rule(monkeypatch, excursion, response):
    g = guarded(monkeypatch, tracking=True, excursion=excursion)
    sign = 1 if excursion > 0 else -1
    g.tick(1, raw=2000, progress=7)
    g.tick(1.6, raw=2000 + sign * min(response, 1), current=5, progress=7.6)
    if response < 3:
        with pytest.raises(RuntimeError, match="response"):
            g.tick(2.2, raw=2000 + sign * response, current=4, progress=8.2)
        boundary = g.robot._am1_selected_joint_fault["response_boundary"]
        assert boundary["commanded_error_threshold_raw"] == 45
        assert boundary["signed_response_raw"] == response
    else:
        g.tick(2.2, raw=2000 + sign * response, current=4, progress=8.2)
        assert g.evidence.response_boundary["status"] == "pending"
        assert g.evidence.response_boundary["anchor_position_raw"] == 2000 + sign * 3
        assert g.evidence.response_boundary["distinct_sample_count"] == 1


@pytest.mark.parametrize("route", ["prepare", "postread"])
def test_tracking45_startup_still_refuses30_raw_drift_before_another_write(monkeypatch, route):
    g = startup_fixture(monkeypatch, tracking=True)
    startup_send(g, startup_action(g), 1)
    g.now[0] = 1.1
    g.robot._am1_selected_joint_position.update(
        raw=2030, normalized=3.0, read_started_at_monotonic_s=1.08, read_completed_at_monotonic_s=1.09
    )
    g.robot._am1_selected_joint_current.update(
        read_started_at_monotonic_s=1.095, read_completed_at_monotonic_s=1.1
    )
    before = len(g.writes)
    with pytest.raises(RuntimeError, match="startup"):
        if route == "prepare":
            g.control.apply(g.robot, startup_action(g, sequence=13))
        else:
            observation = {}
            g.control.annotate(observation, acquired_at=1.1)
            g.evidence.guard_recovery(g.robot, g.control, observation)
    assert len(g.writes) == before


def test_tracking45_jitter_current_dips_and_goal_reduction_do_not_renew_large_error_window(monkeypatch):
    g = guarded(monkeypatch, tracking=True)
    g.tick(1, raw=2000, progress=6.5)
    g.tick(1.1, raw=2002, current=5, progress=6.6)
    assert g.evidence.response_boundary["status"] == "tracking"
    assert g.evidence.response_boundary["anchor_position_raw"] == 2000
    with pytest.raises(RuntimeError, match="response"):
        g.tick(2.21, raw=2002, current=4, progress=6.7)
    assert g.robot._am1_selected_joint_fault["response_boundary"]["signed_response_raw"] == 2


def test_tracking45_protective_hold_cannot_credit_or_erase_unresolved_movement(monkeypatch):
    g = guarded(monkeypatch, tracking=True)
    g.tick(1, progress=7)
    g.tick(1.6, raw=2003, progress=7.6)
    assert g.evidence.response_boundary["anchor_position_raw"] == 2003
    assert g.evidence.deactivate(g.robot) is None
    g.control.state, g.control.epoch = "paused", 1
    g.evidence.guard_recovery(g.robot, g.control, {})
    g.control.state, g.control.epoch = "active", 2
    with pytest.raises(RuntimeError, match="response"):
        g.tick(1.7, raw=2006, progress=7.7)
    fault = g.robot._am1_selected_joint_fault
    assert fault["response_boundary"]["failure_kind"] == "continuity_lost"
    assert fault["response_boundary"]["anchor_position_raw"] == 2003


def test_tracking45_cap_still_precedes_expired_response_fault(monkeypatch):
    g = guarded(monkeypatch, tracking=True)
    g.tick(1, progress=7)
    with pytest.raises(RuntimeError, match="effort"):
        g.tick(2.21, current=24, progress=8.21)
    fault = g.robot._am1_selected_joint_fault
    assert fault["current"]["ma"] == 156
    assert fault["effective_limit_ma"] == 150
    assert fault["source"] == "cached_guard"


def test_tracking45_wire_task_provider_and_simulated_measured_verdict_remain_coherent():
    from examples.alohamini.am1_finite_task import FiniteTask
    from tests.robots.test_am1_selected_experiment import provider, run_selected

    d = descriptor(
        tracking=True,
        excursion_norm=10,
        ramp_s=10,
        endpoint_dwell_s=2,
        current_cap_ma=150,
        live_ceiling_s=60,
        minimum_response_raw=56,
        return_tolerance_raw=22,
        goal_readback_checkpoints_s=[5, 10],
    )
    wire = contract.SelectedElbowExperiment.from_payload(json.loads(json.dumps(d.payload)))
    assert wire.sha256 == d.sha256 and wire.summary() == d.summary()
    p = provider(wire)
    task = FiniteTask(contract.RECIPES[d.recipe_name], p.origin, 0, 3, selected_experiment=wire)
    assert task.snapshot()["experiment"] == d.summary()
    assert task.provider.get_action()["left_shoulder_lift.pos"] == 99.8
    run_selected(p)  # Explicitly simulated measured trajectory, not physical movement.
    proof = p.positive_control
    assert proof["experiment"] == d.summary()
    assert proof["coarse_functional_pass"] is True
    execution = {
        "original_seed": p.origin,
        "progress_s": 28,
        "cycles_completed": 1,
        "returns_qualified": 1,
        "positive_control": proof,
    }
    assert d.completion_expectation(execution) == (28, 1, 1)


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


@pytest.mark.parametrize("tracking", [False, True])
def test_valid_shrinking_pair_skew_cannot_credit_a_late_position_response(monkeypatch, tracking):
    g = guarded(monkeypatch, tracking=tracking)

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

    g.tick(1.20, progress=7 if tracking else 5, mutate=anchor_skew)
    with pytest.raises(RuntimeError, match="response"):
        g.tick(2.231, raw=2003, progress=8.2 if tracking else 6.2, mutate=later_skew)
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


@pytest.mark.parametrize("tracking", [False, True])
def test_exact_duplicate_frame_and_clocks_neither_credit_nor_renew_window(monkeypatch, tracking):
    g = guarded(monkeypatch, tracking=tracking)
    observation = g.tick(1, progress=7 if tracking else None)
    before = copy.deepcopy(g.evidence.response_boundary)
    g.now[0] = 1.2
    g.evidence.guard_recovery(g.robot, g.control, observation)
    assert g.evidence.response_boundary == before
    with pytest.raises(RuntimeError, match="response"):
        g.tick(2.21, progress=8.21 if tracking else None)


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


def operation03_guard(monkeypatch, *, qualified_dwell=True, clock_offset=0.0):
    return guarded(
        monkeypatch,
        operation=True,
        qualified_dwell=qualified_dwell,
        reference=(2445.2 - 1000) / 11.05 - 100,
        calibration={"drive_mode": 0, "homing_offset": 1845, "range_min": 1000, "range_max": 3210},
        clock_offset=clock_offset,
    )


def operation03_plateau(monkeypatch, *, qualified_dwell=True, clock_offset=0.0):
    """Real native guard path with the recorded 2445 -> 2509 outward response."""
    g = operation03_guard(monkeypatch, qualified_dwell=qualified_dwell, clock_offset=clock_offset)
    g.tick(1.9, raw=2445, progress=1.9, current=16)
    g.tick(2.0, raw=2445, progress=2.0, current=16)
    for n in range(1, 100):
        at = 2 + n / 10
        g.tick(at, raw=min(2509, 2445 + round(n * 0.65)), progress=at, current=16)
    assert g.robot._am1_selected_joint_action["expected_goal_raw"] == 2554
    assert g.evidence.response_boundary["anchor_position_raw"] == 2509
    return g


def test_v2_coarse_response_retires_timely_late_window_at_constant_endpoint(monkeypatch):
    g = operation03_plateau(monkeypatch)
    for at in (12.0, 12.2, 12.6, 13.11, 13.9):
        g.tick(at, raw=2509, progress=at, current=16)
    boundary = g.evidence.response_boundary
    assert boundary["status"] == "coarse_qualified_dwell"
    assert boundary["signed_commanded_error_raw"] == 46
    assert boundary["coarse_dwell_proof"]["leg_origin_raw"] == 2445
    assert boundary["coarse_dwell_proof"]["signed_leg_response_raw"] == 64
    retired = boundary["coarse_dwell_proof"]["retired_window"]
    assert retired["status"] == "retired_at_coarse_qualified_dwell"
    assert retired["signed_response_raw"] == 0
    assert retired["elapsed_s"] == pytest.approx(0.1)
    assert retired["anchor_position_raw"] == 2509
    assert getattr(g.robot, "_am1_selected_joint_fault", None) is None
    g.tick(14, raw=2509, progress=14, current=16)
    assert g.evidence.response_boundary["status"] != "coarse_qualified_dwell"


def test_retained_v1_still_requires_late_window_response_in_endpoint(monkeypatch):
    g = operation03_plateau(monkeypatch, qualified_dwell=False)
    g.tick(12, raw=2509, progress=12, current=16)
    with pytest.raises(RuntimeError, match="response"):
        g.tick(13.11, raw=2509, progress=13.11, current=16)
    boundary = g.robot._am1_selected_joint_fault["response_boundary"]
    assert boundary["failure_kind"] == "nonresponse" and boundary["signed_response_raw"] == 0


def test_qualified_dwell_cannot_retire_an_already_expired_moving_window(monkeypatch):
    g = operation03_plateau(monkeypatch)
    with pytest.raises(RuntimeError, match="response"):
        g.tick(13.11, raw=2509, progress=12, current=16)
    assert g.robot._am1_selected_joint_fault["response_boundary"]["failure_kind"] == "nonresponse"


@pytest.mark.parametrize("start_phase", ["absent", "stale", "second_cycle_without_hold"])
def test_qualified_dwell_requires_fresh_actual_same_leg_origin(monkeypatch, start_phase):
    g = operation03_guard(monkeypatch)
    if start_phase == "stale":
        g.tick(1, raw=2445, progress=1.9)
    if start_phase == "second_cycle_without_hold":
        g.tick(1.9, raw=2445, progress=1.9)
        g.tick(2, raw=2445, progress=2)
    base = 32 if start_phase == "second_cycle_without_hold" else 2
    g.tick(3, raw=2509, progress=base + 9.9)
    g.tick(3.1, raw=2509, progress=base + 10)
    with pytest.raises(RuntimeError, match="response"):
        g.tick(4.21, raw=2509, progress=base + 11.1)


def test_coarse_dwell_does_not_disable_static_adverse_current_or_reversal(monkeypatch):
    g = operation03_plateau(monkeypatch)
    g.tick(12, raw=2509, progress=12, current=16)
    for at, current in ((12.1, 17), (12.2, 18)):
        g.tick(at, raw=2509, progress=at, current=current)
    with pytest.raises(RuntimeError, match="adverse current"):
        g.tick(12.3, raw=2509, progress=12.3, current=19)


def test_coarse_dwell_preserves_current_cap_priority(monkeypatch):
    g = operation03_plateau(monkeypatch)
    with pytest.raises(RuntimeError, match="effort"):
        g.tick(12, raw=2509, progress=12, current=24)
    assert g.robot._am1_selected_joint_fault["source"] == "cached_guard"


def test_coarse_dwell_rejects_retreat_against_intended_direction(monkeypatch):
    g = operation03_plateau(monkeypatch)
    g.tick(12, raw=2509, progress=12, current=16)
    with pytest.raises(RuntimeError, match="reversed"):
        g.tick(12.1, raw=2506, progress=12.1, current=16)


def test_under_56_actual_ticks_cannot_earn_coarse_dwell(monkeypatch):
    g = operation03_guard(monkeypatch)
    g.tick(1.9, raw=2445, progress=1.9)
    g.tick(2, raw=2445, progress=2)
    g.tick(3, raw=2500, progress=11.9)
    g.tick(3.1, raw=2500, progress=12)
    with pytest.raises(RuntimeError, match="response"):
        g.tick(4.21, raw=2500, progress=13.1)


def test_second_cycle_origin_is_actual_interhold_position_after_recovery(monkeypatch):
    g = guarded(monkeypatch, operation=True, qualified_dwell=True)
    g.tick(1.9, raw=2017, progress=28)
    g.evidence.deactivate(g.robot)
    g.control.epoch = 1
    g.tick(2, raw=2017, progress=28, context_overrides=operation_rejoin())
    g.tick(2.1, raw=2017, progress=28, context_overrides=operation_rejoin(0.1))
    for n in range(2, 18):
        g.tick(2 + n / 10, raw=2017, progress=28, context_overrides=operation_rejoin(n / 10))
    g.tick(3.8, raw=2017, progress=28)
    g.tick(3.9, raw=2017, progress=31.9)
    g.tick(4, raw=2017, progress=32)
    g.tick(4.1, raw=2073, progress=41.9)
    g.tick(4.2, raw=2073, progress=42)
    boundary = g.evidence.response_boundary
    assert boundary["status"] == "coarse_qualified_dwell"
    assert boundary["coarse_dwell_proof"]["leg_origin_raw"] == 2017
    assert boundary["coarse_dwell_proof"]["signed_leg_response_raw"] == 56
    assert g.evidence.reference == 0
    g.tick(5.3, raw=2073, progress=43.1)
    assert getattr(g.robot, "_am1_selected_joint_fault", None) is None


def test_coarse_dwell_full_native_feedback_keeps_original_serialization_limits(monkeypatch):
    g = operation03_plateau(monkeypatch, clock_offset=23441.123456789)
    observation = g.tick(12, raw=2509, progress=12, current=16)
    g.evidence.publish(g.robot, g.control, observation)
    boundary = observation[host.AM1_LOCAL_FEEDBACK_KEY]["selected_joint"]["response_boundary"]
    assert len(json.dumps(boundary, allow_nan=False, separators=(",", ":")).encode()) <= 4096
    assert len(host.build_observation_multipart(observation, [])[0]) < 65536
    certificate = boundary["coarse_dwell_proof"]["qualified"]
    assert certificate["position_read_completed_at_monotonic_s"] == pytest.approx(23453.113456789)
    assert boundary["coarse_dwell_proof"]["retired_window"]["signed_response_raw"] == 0


@pytest.mark.parametrize(
    "invalid", ["future_marker", "foreign_marker", "wrong_goal", "old_position", "duplicate_current"]
)
def test_v2_dwell_never_qualifies_unmatched_or_nonadvancing_native_acquisition(monkeypatch, invalid):
    g = operation03_plateau(monkeypatch)
    old_current = copy.deepcopy(g.robot._am1_selected_joint_current)

    def corrupt(obj, observation):
        marker = observation[host.AM1_LOCAL_FEEDBACK_KEY]
        if invalid == "future_marker":
            marker["acquired_at_monotonic_s"] = 100
        elif invalid == "foreign_marker":
            marker["run_id"] = "foreign"
        elif invalid == "wrong_goal":
            obj._am1_selected_joint_action["expected_goal_raw"] += 1
        elif invalid == "old_position":
            obj._am1_selected_joint_position["read_started_at_monotonic_s"] = 10
        else:
            obj._am1_selected_joint_current = copy.deepcopy(old_current)

    if invalid == "duplicate_current":
        g.tick(12, raw=2509, progress=12, current=16, mutate=corrupt)
        assert g.evidence.response_boundary["status"] == "pending"
        with pytest.raises(RuntimeError, match="response"):
            g.tick(13.11, raw=2509, progress=13.11, current=16)
    else:
        with pytest.raises(RuntimeError):
            g.tick(12, raw=2509, progress=12, current=16, mutate=corrupt)
    assert g.evidence.response_boundary.get("status") != "coarse_qualified_dwell"


@pytest.mark.parametrize("changed", ["pause", "epoch", "intent"])
def test_v2_dwell_origin_is_not_reused_after_control_continuity_changes(monkeypatch, changed):
    g = operation03_plateau(monkeypatch)
    g.tick(12, raw=2509, progress=12, current=16)
    if changed == "pause":
        g.evidence.deactivate(g.robot)
        g.control.state = "paused"
        g.control.epoch = 1
        g.evidence.guard_recovery(g.robot, g.control, {})
        g.control.state = "active"
        g.control.epoch = 2
    elif changed == "epoch":
        g.control.epoch = 1
    else:
        g.control.intent_revision = 1
    g.tick(12.2, raw=2509, progress=12.2, current=16)
    assert g.evidence.response_boundary["status"] == "pending"
    with pytest.raises(RuntimeError, match="response"):
        g.tick(13.41, raw=2509, progress=13.41, current=16)


def test_v2_moving_phase_stall_still_faults_before_a_later_endpoint_can_qualify(monkeypatch):
    g = operation03_guard(monkeypatch)
    g.tick(1.9, raw=2445, progress=1.9, current=16)
    g.tick(2, raw=2445, progress=2, current=16)
    g.tick(3, raw=2445, progress=7, current=16)
    with pytest.raises(RuntimeError, match="response"):
        g.tick(4.21, raw=2445, progress=8.21, current=16)
    boundary = g.robot._am1_selected_joint_fault["response_boundary"]
    assert boundary["failure_kind"] == "nonresponse" and boundary["signed_response_raw"] == 0


def test_v2_second_cycle_cannot_credit_original_q0_instead_of_actual_start(monkeypatch):
    g = guarded(
        monkeypatch,
        operation=True,
        qualified_dwell=True,
        calibration={"drive_mode": 0, "homing_offset": 1845, "range_min": 1000, "range_max": 3400},
    )
    g.tick(1.9, raw=2217, progress=31.9)
    g.tick(2, raw=2217, progress=32)
    g.tick(3, raw=2272, progress=41.9)
    g.tick(3.1, raw=2272, progress=42)
    assert g.evidence.reference == 0
    assert g.evidence.response_boundary["status"] == "pending"
    with pytest.raises(RuntimeError, match="response"):
        g.tick(4.21, raw=2272, progress=43.1)


def test_v2_qualified_dwell_preserves_original_native_live_deadline(monkeypatch):
    g = operation03_plateau(monkeypatch)
    g.tick(12, raw=2509, progress=12, current=16)
    with pytest.raises(RuntimeError, match="Live ceiling"):
        g.tick(92, raw=2509, progress=13, current=16)


def test_v2_duplicate_complete_frame_cannot_create_or_refresh_a_dwell_proof(monkeypatch):
    g = operation03_plateau(monkeypatch)
    before = copy.deepcopy(g.evidence.response_boundary)
    observation = g.tick(12, raw=2509, progress=12, current=16, run_guard=False)
    previous_position = g.robot._am1_selected_joint_position
    previous_current = g.robot._am1_selected_joint_current
    previous_position.update(read_started_at_monotonic_s=11.88, read_completed_at_monotonic_s=11.89)
    previous_current.update(read_started_at_monotonic_s=11.895, read_completed_at_monotonic_s=11.9)
    g.robot._am1_selected_joint_action.update(
        write_started_at_monotonic_s=11.87, write_completed_at_monotonic_s=11.875
    )
    observation[host.AM1_LOCAL_FEEDBACK_KEY]["read_started_at_monotonic_s"] = 11.88
    g.evidence.guard_recovery(g.robot, g.control, observation)
    assert g.evidence.response_boundary == before
    with pytest.raises(RuntimeError, match="response"):
        g.tick(13.11, raw=2509, progress=13.11, current=16)


def test_dwell_fixture_exercises_exact_named_v2_descriptor(monkeypatch):
    g = operation03_guard(monkeypatch)
    assert g.definition.sha256 == "31336b83330fb5398778b4cfc0397011a3ae063d47efdf9c02e62d685475d473"
    assert g.definition.goal_readback_checkpoints_s == (5, 37)


@pytest.mark.parametrize(
    "changes", [{"minimum_response_raw": 60}, {"excursion_norm": 9, "minimum_response_raw": 50}]
)
def test_other_valid_v2_conditions_cannot_retire_the_diagnosed_endpoint_obligation(monkeypatch, changes):
    excursion = changes.get("excursion_norm", 10)
    g = guarded(
        monkeypatch,
        operation=True,
        qualified_dwell=True,
        excursion=excursion,
        reference=(2445.2 - 1000) / 11.05 - 100,
        calibration={"drive_mode": 0, "homing_offset": 1845, "range_min": 1000, "range_max": 3210},
        definition_changes={"minimum_response_raw": changes["minimum_response_raw"]},
    )
    g.tick(1.9, raw=2445, progress=1.9, current=16)
    g.tick(2, raw=2445, progress=2, current=16)
    measured = 2509 if excursion == 10 else 2498
    g.tick(3, raw=measured, progress=11.9, current=16)
    g.tick(3.1, raw=measured, progress=12, current=16)
    assert g.definition.envelope_sha256 == contract.LEFT_ELBOW_COARSE_BENCH_V2.envelope_sha256
    assert g.definition.sha256 != contract.LEFT_ELBOW_COARSE_BENCH_V2.sha256
    with pytest.raises(RuntimeError, match="response"):
        g.tick(4.21, raw=measured, progress=13.1, current=16)
    boundary = g.robot._am1_selected_joint_fault["response_boundary"]
    assert boundary["failure_kind"] == "nonresponse" and boundary["signed_response_raw"] == 0
    assert "coarse_dwell_proof" not in boundary


def negative_native_hold(monkeypatch, *, raws=(1965, 1965, 1965), mutate=None):
    g = guarded(monkeypatch, negative_hold=True)
    g.tick(1, raw=2000, progress=0)
    for index, raw in enumerate(raws):
        observation = g.tick(2 + index*.1, raw=raw, progress=31.6+index*.1, mutate=mutate)
    return g, observation


def test_negative_v3_native_hold_ready_before_second_write_keeps_original_reference(monkeypatch):
    g, observation = negative_native_hold(monkeypatch)
    g.evidence.publish(g.robot, g.control, observation)
    selected = observation[host.AM1_LOCAL_FEEDBACK_KEY]["selected_joint"]
    proof = selected["intercycle_hold_admission"]
    assert proof["measured_origin_raw"] == 2000 and proof["expected_goal_raw"] == 2000
    assert proof["original_reference"]["certificate"]["progress_s"] == 0
    assert proof["rows"][-1][3] == 1965 and len(proof["rows"]) == 3
    assert proof["hold_cursor"]["action_sequence"] == 2
    assert proof["hold_cursor"]["write_completed_at_monotonic_s"] == 1.975
    assert g.evidence.response_boundary["status"] == "tracking"
    g.tick(2.3, raw=1965, progress=32)
    assert g.evidence._hold_departed is True


@pytest.mark.parametrize("case", ["missing_origin", "residual41", "spread3", "stale", "duplicate",
                                  "future", "foreign_intent", "pause", "current_cap"])
def test_negative_v3_native_hold_refuses_unqualified_first_second_cycle_write(monkeypatch, case):
    raws = (1959, 1959, 1959) if case == "residual41" else (1965, 1966, 1968) if case == "spread3" else (1965,)*3
    if case == "current_cap":
        with pytest.raises(RuntimeError, match="ceiling"):
            negative_native_hold(monkeypatch, mutate=lambda obj, _: obj._am1_selected_joint_current.update(raw=24, ma=156))
        return
    g = guarded(monkeypatch, negative_hold=True)
    if case != "missing_origin":
        g.tick(1, raw=2000, progress=0)
    for index, raw in enumerate(raws):
        def mutate(obj, observation):
            marker = observation[host.AM1_LOCAL_FEEDBACK_KEY]
            if case == "duplicate":
                marker["observation_id"] = 7
            if case == "future":
                marker["acquired_at_monotonic_s"] += 1
        if case == "future":
            with pytest.raises(RuntimeError, match="intercycle hold original marker"):
                g.tick(2 + index*.1, raw=raw, progress=31.6+index*.1, mutate=mutate)
            return
        g.tick(2 + index*.1, raw=raw, progress=31.6+index*.1, mutate=mutate)
    if case == "pause":
        g.evidence.deactivate(g.robot)
    if case == "foreign_intent":
        g.control.intent_revision += 1
    before = len(g.writes), len(g.port.writes)
    with pytest.raises(RuntimeError, match="intercycle hold"):
        g.tick(3 if case == "stale" else 2.3, raw=1965, progress=32)
    assert (len(g.writes), len(g.port.writes)) == before


def test_negative_v3_native_recovery_keeps_original_but_requires_new_hold_cursor(monkeypatch):
    g, _ = negative_native_hold(monkeypatch)
    original = g.evidence._hold_origin.copy()
    g.evidence.deactivate(g.robot)
    assert g.evidence._hold_origin == original and g.evidence.intercycle_hold_admission is None
    g.control.epoch += 2
    g.control.intent_revision += 1
    for index in range(3):
        observation = g.tick(3+index*.1, raw=1964, progress=31.8)
    g.evidence.publish(g.robot, g.control, observation)
    proof = observation[host.AM1_LOCAL_FEEDBACK_KEY]["selected_joint"]["intercycle_hold_admission"]
    assert proof["binding"]["epoch"] == 2
    assert proof["original_reference"]["epoch"] == 0 and proof["measured_origin_raw"] == 2000
    assert proof["hold_cursor"]["action_sequence"] == 5


def test_negative_v3_native_cached_current_precedes_repeat_but_follows_first_hold_write(monkeypatch):
    g = guarded(monkeypatch, negative_hold=True)
    g.tick(1, raw=2000, progress=0)
    g.tick(2, raw=1965, progress=31.6)
    def cached(obj, _):
        obj._am1_selected_joint_current.update(read_started_at_monotonic_s=g.now[0]-.035,
                                               read_completed_at_monotonic_s=g.now[0]-.03)
    g.tick(2.1, raw=1965, progress=31.7, mutate=cached)
    observation = g.tick(2.24, raw=1965, progress=31.8, mutate=cached)
    g.evidence.publish(g.robot, g.control, observation)
    proof = observation[host.AM1_LOCAL_FEEDBACK_KEY]["selected_joint"]["intercycle_hold_admission"]
    assert proof["rows"][-1][8] < proof["rows"][-1][11]
    assert proof["rows"][-1][7] > proof["hold_cursor"]["write_completed_at_monotonic_s"]


@pytest.mark.parametrize("change", [None, "intent", "epoch"])
def test_negative_v3_actual_control_checks_incoming_binding_before_second_write(monkeypatch, change):
    g, _ = negative_native_hold(monkeypatch)
    metadata = {**g.evidence.context, "progress_s": 32, "phase": "outbound", "cycle": 2,
                "sample_key": None, "requested_target": 0.0}
    action = startup_action(g, sequence=10, metadata=metadata)
    action[host.AM1_LOCAL_CONTROL_KEY].update(epoch=0)
    if change == "intent":
        action[host.AM1_LOCAL_CONTROL_KEY]["intent_revision"] = 1
    elif change == "epoch":
        action[host.AM1_LOCAL_CONTROL_KEY]["epoch"] = 2
    sent = []
    monkeypatch.setattr(g.robot, "send_action", lambda value: sent.append(value) or value)
    g.now[0] = 2.25
    if change is None:
        assert g.control.apply(g.robot, action)
        assert len(sent) == 1 and g.control.accepted_action_sequence == 10
        assert not g.evidence._hold_departed  # A returned write still awaits its matching native feedback.
    else:
        with pytest.raises(RuntimeError, match="intercycle hold|active epoch"):
            g.control.apply(g.robot, action)
        assert sent == [] and g.control.accepted_action_sequence == 4


def test_negative_v3_failed_actual_send_clears_departure_and_cannot_credit_prior_feedback(monkeypatch):
    g, _ = negative_native_hold(monkeypatch)
    metadata = {**g.evidence.context, "progress_s": 32, "phase": "outbound", "cycle": 2,
                "sample_key": None, "requested_target": 0.0}
    action = startup_action(g, sequence=10, metadata=metadata)
    action[host.AM1_LOCAL_CONTROL_KEY].update(epoch=0)
    def failed(_):
        raise RuntimeError("simulated send failure")
    monkeypatch.setattr(g.robot, "send_action", failed)
    g.now[0] = 2.25
    with pytest.raises(RuntimeError, match="simulated send failure"):
        g.control.apply(g.robot, action)
    assert g.control.accepted_action_sequence == 4 and not g.evidence._hold_departed
    assert g.evidence._hold_pending_departure is None
    assert g.evidence.intercycle_hold_admission is None
    observation = {}
    g.control.annotate(observation, acquired_at=2.2)
    with pytest.raises(RuntimeError, match="intercycle hold departure"):
        g.evidence.guard_recovery(g.robot, g.control, observation)
    assert not g.evidence._hold_departed


def negative_v3_actual_trace(monkeypatch, *, count=13, period=.2/11, wide_counters=False, baseline_count=1):
    from lerobot.robots.alohamini import alohamini as robot_module
    material = {"drive_mode": 0, "homing_offset": 1845, "range_min": 864, "range_max": 3073}
    reference = (2445-material["range_min"])/(material["range_max"]-material["range_min"])*200-100
    g = guarded(monkeypatch, negative_hold=True, reference=reference, calibration=material)
    g.control.state, g.control.epoch, g.control.intent_revision = "ready", -1, -1
    g.control.accepted_action_sequence = -1
    sequence_offset = 9007199254740900 if wide_counters else 0
    g.control.observation_id = sequence_offset
    sensor, at = [2445], [23441.123456789123]
    def clock():
        at[0] += .000001234567
        return at[0]
    def read(address, length, ids, **kwargs):
        g.reads.append((address, length, tuple(ids)))
        return dict.fromkeys(ids, sensor[0] if address == 56 else 23 if address == 69 else 0), 0
    monkeypatch.setattr(g.robot.left_bus, "_sync_read", read)
    monkeypatch.setattr(robot_module.time, "monotonic", clock)
    g.evidence.clock = clock
    g.evidence.attempted.add("baseline")
    g.evidence.goal_checkpoint_attempted.update((0, 1))
    def send(progress, raw, moment, sequence, *, epoch=0, intent=0):
        at[0], sensor[0] = 23441.123456789123+moment, raw
        cycle, phase, fraction = g.definition.phase(progress)
        metadata = context(profile=g.definition.profile, progress_s=progress, phase=phase, cycle=cycle,
            reference=reference, endpoint_target=reference-10, requested_target=reference-10*fraction,
            sample_key=g.definition.sample_key(progress), acquisition_s=0.0, admitted_reference=reference,
            experiment_sha256=g.definition.sha256, rejoin=None)
        action = startup_action(g, requested=metadata["requested_target"], metadata=metadata, sequence=sequence+sequence_offset)
        action[host.AM1_LOCAL_CONTROL_KEY].update(epoch=epoch,intent_revision=intent)
        assert g.control.apply(g.robot, action)
        observation = g.robot.get_observation()
        g.control.annotate(observation, acquired_at=clock())
        g.evidence.guard_recovery(g.robot, g.control, observation)
        g.evidence.publish(g.robot, g.control, observation)
        g.evidence.report(g.robot, g.control, observation)
        return observation
    for index in range(baseline_count):
        send(0,2445,1+index*.05,index)
    for index in range(count):
        observation = send(31.5+index*.01, 2410, 2+index*period, index+baseline_count)
    g.send_native = send
    return g, observation


@pytest.mark.parametrize("wide_counters", [False, True])
def test_negative_v3_full_actual_native_serializers_fit_with_twelve_original_rows(monkeypatch, record_property, wide_counters):
    g, observation = negative_v3_actual_trace(monkeypatch,wide_counters=wide_counters)
    selected = observation[host.AM1_LOCAL_FEEDBACK_KEY]["selected_joint"]
    proof = selected["intercycle_hold_admission"]
    assert len(proof["rows"]) == 12
    assert selected["action"]["relative_limited"] == selected["action"]["current_limited"]
    assert selected["action"]["write_acknowledged"] is False
    proof_bytes = len(json.dumps({"intercycle_hold_admission": proof}, allow_nan=False, separators=(",", ":")).encode())
    native_bytes = len(host.build_observation_multipart(observation, [])[0])
    record_property("v3_named_admission_wrapper_bytes", proof_bytes)
    record_property("v3_actual_full_native_bytes", native_bytes)
    assert proof_bytes <= 4096 and native_bytes < 65536
    assert len([row for row in g.rows if row.get("kind") == "native_trace"]) == 14


def test_negative_v3_dense_twenty_actual_acquisitions_do_not_truncate_into_ready(monkeypatch):
    g, observation = negative_v3_actual_trace(monkeypatch, count=20, period=.01)
    assert len(g.evidence._hold_rows) == 12 and g.evidence.intercycle_hold_admission is None
    assert "intercycle_hold_admission" not in observation[host.AM1_LOCAL_FEEDBACK_KEY]["selected_joint"]
    assert len([row for row in g.rows if row.get("kind") == "native_trace"]) == 21


@pytest.mark.parametrize("bad", ["null_binding", "original_future_current", "original_bool_current",
                                 "origin_position_after_acquired"])
def test_negative_v3_shared_proof_refuses_malformed_original_native_source(monkeypatch, bad):
    g, _ = negative_native_hold(monkeypatch)
    proof, origin, anchor = (copy.deepcopy(g.evidence.intercycle_hold_admission),
                            copy.deepcopy(g.evidence._hold_origin), copy.deepcopy(g.evidence._hold_anchor))
    if bad == "null_binding":
        proof["binding"]["run_id"] = None
    elif bad == "original_future_current":
        origin["current"]["read_completed_at_monotonic_s"] = origin["acquired_at_monotonic_s"] + 1
    elif bad == "original_bool_current":
        origin["current"]["raw"] = True
    else:
        origin["acquired_at_monotonic_s"] = origin["position"]["read_started_at_monotonic_s"]
    proof["original_reference"]["certificate"] = g.definition._hold_certificate(origin)
    assert not g.definition.intercycle_hold_admission_valid(proof, anchor, origin, now=2.2)


@pytest.mark.parametrize("wide_counters", [False, True])
def test_negative_v3_actual_owner_http_and_websocket_producers_preserve_full_bound_status(monkeypatch, record_property, wide_counters):
    import asyncio
    from tests.robots.test_am1_elbow_recovery_integration import reporting_authority
    from tests.robots.test_am1_session_reporting import (actual_selected_final_snapshot, exchange,
        native_status_field, snapshot_http_gateway)
    from tools import am1_session_ipc as ipc
    from tools.am1_session_service import Peer

    g, observation = negative_v3_actual_trace(monkeypatch,wide_counters=wide_counters)
    selected = copy.deepcopy(observation[host.AM1_LOCAL_FEEDBACK_KEY]["selected_joint"])
    # Reporting composition only: retain the historical full twenty-row cleanup
    # shape while adding exact new simulated native evidence to the private field.
    prior = actual_selected_final_snapshot()
    run, evidence = copy.deepcopy(prior["run"]), copy.deepcopy(prior["evidence"])
    run["execution"]["positive_control"]["simulated_v3_native_admission"] = selected
    authority = reporting_authority(run, evidence)
    original = copy.deepcopy(authority.run)
    view = authority.snapshot()
    assert len(view["run"]["physical_result"]["readback"]["readback"]["readbacks"]) == 20
    result = {"accepted": True, "status": "snapshot", "snapshot": view}
    owner_bytes = len(ipc.encode({"id": "12345678-1234-1234-1234-123456789abc", "result": result}, ipc.RESPONSE_LIMIT))
    record_property("v3_actual_owner_frame_bytes", owner_bytes)
    reply, calls = asyncio.run(exchange(result, {"op": "snapshot"}))
    assert reply == result and calls == []
    retained = native_status_field(authority, view["run"]["details_reference"])
    assert retained == original and retained["execution"]["positive_control"]["simulated_v3_native_admission"] == selected
    for route in ("state", "attach"):
        response = asyncio.run(getattr(snapshot_http_gateway(view), route)({"device": {"id": "offline-viewer"}}))
        record_property("v3_actual_http_" + route + "_bytes", len(response.body))
        assert response.status == 200 and len(response.body) <= ipc.RESPONSE_LIMIT
        assert ipc.strict_json(response.body)["snapshot"] == view
    async def websocket():
        sent, received = [], asyncio.Event()
        async def send_str(data):
            sent.append(data)
            received.set()
        peer = Peer(SimpleNamespace(send_str=send_str), "offline-viewer", "simulated-token")
        try:
            payload = {"kind": "snapshot", "revision": "12345678-1234-1234-1234-123456789abc:1234567890", "snapshot": view}
            assert peer.put(payload, True)
            await asyncio.wait_for(received.wait(), 1)
            assert len(sent) == 1 and ipc.strict_json(sent[0]) == payload
            return len(sent[0].encode())
        finally:
            await peer.close()
    record_property("v3_actual_websocket_snapshot_bytes", asyncio.run(websocket()))
    assert authority.run == original


@pytest.mark.parametrize("skip_first_baseline", [False,True])
def test_negative_v3_actual_native_to_owner_delayed_two_snapshot_admission(monkeypatch,skip_first_baseline):
    from examples.alohamini import scripted_leader
    from tests.robots.test_am1_selected_elbow_profiles import JOINT_KEYS, seed
    g, _ = negative_v3_actual_trace(monkeypatch,baseline_count=2 if skip_first_baseline else 1)
    traces = [row for row in g.rows if row.get("kind") == "native_trace"]
    p = scripted_leader.LeftElbowPositiveControlInput(seed(g.evidence.reference), joint_keys=JOINT_KEYS,
        fps=10, emit=lambda _: None, definition=g.definition)
    p.admit(traces[0]["acquired_at_monotonic_s"]-.1)
    def consume(row, sequence):
        item = {**copy.deepcopy(row), "sparse_receipts": {}}
        now = row["acquired_at_monotonic_s"] + .025
        positions = {**p.origin, p.selected_joint: row["position"]["normalized"]}
        p.collect_feedback(now, positions, sequence, selected_feedback=item)
        return now
    consume(traces[1 if skip_first_baseline else 0], 1)
    p.elapsed_s = 31.9  # Focused boundary consumption, not a whole-run completion claim.
    p._baseline_closed = True
    p._baseline_raw = g.definition.encoded_goal(g.evidence.reference, traces[0]["motor"]["calibration"])
    consume(traces[-4], 2)
    now = consume(traces[-1], 3)
    p._qualify_intercycle_hold(now)
    hold = p._intercycle_hold
    assert hold["window"]["sample_count"] == 12
    assert hold["window"]["qualified_at_monotonic_s"] < hold["anchor_proof"]["captured_at_monotonic_s"]
    assert hold["window"]["qualified_at_monotonic_s"] == traces[-1]["intercycle_hold_admission"]["qualified_at_monotonic_s"]
    assert g.definition.intercycle_hold_admission_valid(hold["admission_proof"],hold["anchor_proof"],p._measured_origin_proof,now=now)
    if skip_first_baseline:
        assert hold["admission_proof"]["original_reference"]["certificate"]["observation_id"] < p._measured_origin_proof["observation_id"]
        assert p._measured_origin_proof["observation_id"] == traces[1]["observation_id"]
        assert hold["admission_proof"]["original_reference"]["certificate"]["observation_id"] == traces[0]["observation_id"]


def test_negative_v3_first_hold_cursor_prewrite_current_waits_for_postcursor_window(monkeypatch):
    g = guarded(monkeypatch, negative_hold=True)
    g.tick(1, raw=2000, progress=0)
    def early_current(obj, _):
        obj._am1_selected_joint_current.update(read_started_at_monotonic_s=1.96,read_completed_at_monotonic_s=1.97)
    g.tick(2,raw=1965,progress=31.5,mutate=early_current)
    cursor = copy.deepcopy(g.evidence._hold_cursor)
    assert g.evidence.intercycle_hold_admission is None
    for index in range(3):
        g.tick(2.1+index*.1,raw=1965,progress=31.6+index*.1)
    proof = g.evidence.intercycle_hold_admission
    assert proof is not None and proof["hold_cursor"] == cursor
    assert all(min(row[4],row[7])>=cursor["write_completed_at_monotonic_s"] for row in proof["rows"])



def test_negative_v3_actual_matched_departure_survives_pause_and_measured_rejoin(monkeypatch):
    g, _ = negative_native_hold(monkeypatch)
    original = copy.deepcopy(g.evidence._hold_origin)
    sent = []
    monkeypatch.setattr(g.robot, "stop_motion", lambda: None)
    monkeypatch.setattr(g.robot, "hold_follower_arms", lambda: None)
    def returned(action):
        sent.append(copy.deepcopy(action))
        target = action["arm_left_elbow_flex.pos"]
        at = g.now[0]
        g.robot._am1_selected_joint_action = {
            "requested": target, "expected_goal_raw": g.definition.encoded_goal(target, original["motor"]["calibration"]),
            "sync_write_returned": True, "write_started_at_monotonic_s": at-.03,
            "write_completed_at_monotonic_s": at-.025,
        }
        return action
    monkeypatch.setattr(g.robot, "send_action", returned)
    def apply(sequence, at, metadata, raw, *, epoch=0, intent=0):
        g.now[0] = at
        action = startup_action(g, requested=metadata["requested_target"], sequence=sequence, metadata=metadata)
        action[host.AM1_LOCAL_CONTROL_KEY].update(epoch=epoch,intent_revision=intent)
        assert g.control.apply(g.robot, action)
        g.robot._am1_selected_joint_position.update(raw=raw,normalized=(raw-1000)/10-100,
            read_started_at_monotonic_s=at-.02,read_completed_at_monotonic_s=at-.01)
        g.robot._am1_selected_joint_current.update(raw=12,ma=78,
            read_started_at_monotonic_s=at-.005,read_completed_at_monotonic_s=at)
        observation = {}
        g.control.annotate(observation,acquired_at=at)
        observation[host.AM1_LOCAL_FEEDBACK_KEY]["read_started_at_monotonic_s"] = at-.02
        g.evidence.guard_recovery(g.robot,g.control,observation)
        return observation
    outbound = {**g.evidence.context,"progress_s":32,"phase":"outbound","cycle":2,
                "sample_key":None,"requested_target":0.0}
    apply(10,2.3,outbound,1965)
    assert g.evidence._hold_departed and g.evidence._hold_pending_departure is None
    pause = {host.AM1_LOCAL_CONTROL_KEY:{"version":1,"mode":"pause",**g.control.binding,
             "epoch":1,"intent_revision":1,"action_sequence":11}}
    g.now[0] = 2.31
    assert g.control.apply(g.robot,pause)
    assert g.control.state == "paused" and g.evidence._hold_departed
    assert g.evidence.intercycle_hold_admission is None and not g.evidence._hold_rows
    assert g.evidence._hold_pending_departure is None and g.evidence._hold_cursor is None
    for index in range(36):
        elapsed = index*.1
        metadata = {**outbound,"phase":"rejoin","requested_target":-3.5+elapsed,
            "rejoin":{"held_target":-3.5,"retained_target":0.0,"elapsed_s":elapsed,"duration_s":3.5,
                "observation_id":5,"acquired_at_monotonic_s":2.3}}
        apply(12+index,2.4+elapsed,metadata,1965+index,epoch=2,intent=2)
    apply(48,6.01,outbound,2000,epoch=2,intent=2)
    assert len(sent) == 38 and g.control.accepted_action_sequence == 48
    assert g.evidence.reference == 0 and g.evidence.progress == 32
    assert g.evidence._hold_origin == original and g.evidence._hold_departed
    assert g.evidence.intercycle_hold_admission is None and g.evidence._hold_pending_departure is None


def test_negative_v3_aborted_returned_departure_cannot_authorize_another_write(monkeypatch):
    g, _ = negative_native_hold(monkeypatch)
    g.tick(2.3,raw=1965,progress=32)
    assert g.evidence._hold_departed
    g.evidence.abort_hold_admission()
    assert not g.evidence._hold_departed and g.evidence._hold_pending_departure is None
    with pytest.raises(RuntimeError,match="intercycle hold"):
        g.tick(2.4,raw=1965,progress=32)



@pytest.mark.parametrize("change", ["new_run", "new_host", "definition", "calibration", "reference"])
def test_negative_v3_returned_departure_does_not_authorize_changed_original_task(monkeypatch,change):
    g, _ = negative_native_hold(monkeypatch)
    g.tick(2.3,raw=1965,progress=32)
    assert g.evidence._hold_departed
    marker = {"version":1,"mode":"active",**g.control.binding,"epoch":0,"intent_revision":0,"action_sequence":20}
    metadata = copy.deepcopy(g.evidence.context)
    binding = dict(g.control.binding)
    definition = g.definition
    if change == "new_run":
        binding["run_id"] = "00000000-0000-4000-8000-000000000099"
        marker.update(binding)
    elif change == "new_host":
        binding["host_incarnation"] = "00000000-0000-4000-8000-000000000099"
        marker.update(binding)
    elif change == "definition":
        definition = contract.LEFT_ELBOW_COARSE_BENCH_V2
    elif change == "calibration":
        old = g.robot.left_bus.calibration["arm_left_elbow_flex"]
        g.robot.left_bus.calibration["arm_left_elbow_flex"] = type(old)(id=3,drive_mode=0,homing_offset=1845,range_min=1001,range_max=3000)
    else:
        metadata["reference"] += .1
    before = len(g.writes),len(g.port.writes)
    with pytest.raises(RuntimeError,match="original task identity"):
        g.evidence._prepare_hold_departure(g.robot,definition,metadata,binding,marker)
    assert not g.evidence._hold_departed and g.evidence._hold_departure is None
    assert (len(g.writes),len(g.port.writes)) == before


@pytest.mark.parametrize("change", ["qualified_before_acquired", "qualified_after_capture", "capture_after_consumer", "stale_consumer"])
def test_negative_v3_shared_proof_orders_distinct_producer_and_consumer_clocks(monkeypatch,change):
    g,_ = negative_native_hold(monkeypatch)
    proof = copy.deepcopy(g.evidence.intercycle_hold_admission)
    anchor = copy.deepcopy(g.evidence._hold_anchor)
    anchor["captured_at_monotonic_s"] += .025
    now = anchor["captured_at_monotonic_s"]+.01
    assert g.definition.intercycle_hold_admission_valid(proof,anchor,g.evidence._hold_origin,now=now)
    if change == "qualified_before_acquired":
        proof["qualified_at_monotonic_s"] = anchor["acquired_at_monotonic_s"]-.001
    elif change == "qualified_after_capture":
        proof["qualified_at_monotonic_s"] = anchor["captured_at_monotonic_s"]+.001
    elif change == "capture_after_consumer":
        anchor["captured_at_monotonic_s"] = now+.001
    else:
        now += .6
    assert not g.definition.intercycle_hold_admission_valid(proof,anchor,g.evidence._hold_origin,now=now)



def test_negative_v3_actual_first_native_and_later_owner_origin_corruption_refuses(monkeypatch):
    from examples.alohamini import scripted_leader
    from tests.robots.test_am1_selected_elbow_profiles import JOINT_KEYS,seed
    g,_ = negative_v3_actual_trace(monkeypatch,baseline_count=2)
    traces = [row for row in g.rows if row.get("kind") == "native_trace"]
    p = scripted_leader.LeftElbowPositiveControlInput(seed(g.evidence.reference),joint_keys=JOINT_KEYS,
        fps=10,emit=lambda _:None,definition=g.definition)
    origin = p._measurement_proof(traces[1],traces[1]["acquired_at_monotonic_s"]+.025)
    anchor,proof = g.evidence._hold_anchor,g.evidence.intercycle_hold_admission
    now = anchor["captured_at_monotonic_s"]+.01
    assert g.definition.intercycle_hold_admission_valid(proof,anchor,origin,now=now)
    for invalid in ("epoch","intent","current_cap","current_bool","observation_bool","observation_future",
                    "action_future","raw","q0","read_future","read_stale","acquired_future","same_id_different_row"):
        changed = copy.deepcopy(proof)
        original = changed["original_reference"]
        cert = original["certificate"]
        if invalid in {"epoch","intent"}:
            original["epoch" if invalid == "epoch" else "intent_revision"] += 1
        elif invalid == "current_cap":
            cert["current_raw"] = 24
        elif invalid == "current_bool":
            cert["current_raw"] = True
        elif invalid == "observation_bool":
            cert["observation_id"] = True
        elif invalid == "observation_future":
            cert["observation_id"] = origin["observation_id"]+1
        elif invalid == "action_future":
            cert["action_sequence"] = origin["action_sequence"]+1
        elif invalid == "raw":
            cert["position_raw"] += 1
        elif invalid == "q0":
            changed["reference_normalized"] += .1
        elif invalid == "read_future":
            cert["current_read_started_at_monotonic_s"] = origin["current"]["read_started_at_monotonic_s"]+.001
        elif invalid == "read_stale":
            cert["current_read_started_at_monotonic_s"] = cert["acquired_at_monotonic_s"]-.501
        elif invalid == "acquired_future":
            cert["acquired_at_monotonic_s"] = origin["acquired_at_monotonic_s"]+.001
        else:
            cert["observation_id"] = origin["observation_id"]
        assert not g.definition.intercycle_hold_admission_valid(changed,anchor,origin,now=now),invalid


def test_negative_v3_actual_changed_epoch_requires_and_accepts_new_native_hold_cursor(monkeypatch):
    from examples.alohamini import scripted_leader
    from tests.robots.test_am1_selected_elbow_profiles import JOINT_KEYS,seed
    g,_ = negative_v3_actual_trace(monkeypatch)
    traces = [row for row in g.rows if row.get("kind") == "native_trace"]
    p = scripted_leader.LeftElbowPositiveControlInput(seed(g.evidence.reference),joint_keys=JOINT_KEYS,
        fps=10,emit=lambda _:None,definition=g.definition)
    p.admit(traces[0]["acquired_at_monotonic_s"]-.1)
    def consume(row,sequence):
        now = row["acquired_at_monotonic_s"]+.025
        positions = {**p.origin,p.selected_joint:row["position"]["normalized"]}
        p.collect_feedback(now,positions,sequence,selected_feedback={**copy.deepcopy(row),"sparse_receipts":{}})
        return now
    consume(traces[0],1)
    p.elapsed_s,p._baseline_closed,p._baseline_raw = 31.9,True,g.definition.encoded_goal(g.evidence.reference,traces[0]["motor"]["calibration"])
    consume(traces[-4],2)
    consume(traces[-1],3)
    original,old_cursor = copy.deepcopy(p._measured_origin_proof),copy.deepcopy(g.evidence._hold_cursor)
    p.freeze()
    monkeypatch.setattr(g.robot,"stop_motion",lambda:None)
    monkeypatch.setattr(g.robot,"hold_follower_arms",lambda:None)
    pause = {host.AM1_LOCAL_CONTROL_KEY:{"version":1,"mode":"pause",**g.control.binding,
        "epoch":1,"intent_revision":1,"action_sequence":20}}
    assert g.control.apply(g.robot,pause)
    for index in range(13):
        g.send_native(31.7+index*.01,2410,3+index*.2/11,21+index,epoch=2,intent=2)
    fresh = [row for row in g.rows if row.get("kind") == "native_trace" and row["epoch"] == 2]
    p.admit(fresh[0]["acquired_at_monotonic_s"])
    consume(fresh[-4],4)
    now = consume(fresh[-1],5)
    p._qualify_intercycle_hold(now)
    hold = p._intercycle_hold
    assert hold["admission_proof"]["binding"]["epoch"] == 2
    assert hold["admission_proof"]["hold_cursor"]["action_sequence"] > old_cursor["action_sequence"]
    assert hold["admission_proof"]["hold_cursor"]["write_completed_at_monotonic_s"] > p._intercycle_cursor_barrier[0]
    assert hold["admission_proof"]["original_reference"]["epoch"] == 0 and p._measured_origin_proof == original
