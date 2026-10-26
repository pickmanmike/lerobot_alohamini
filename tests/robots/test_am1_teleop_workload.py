"""Motor-free selected segments of the existing AM1 bench workload."""

import pytest

from examples.alohamini.am1_teleop_workload import TeleopWorkload
from tests.robots.test_am1_live_teleop_contract import workspace


@pytest.mark.parametrize("segment,duration,offset,phase", [
    ("combined", 12.0, 96.0, "combined"),
    ("continuity", 24.0, 96.0, "combined"),
    ("body", 4.5, 168.0, "body"),
])
def test_selected_segment_uses_same_actual_reference_and_profile(segment, duration, offset, phase):
    selected = TeleopWorkload(workspace(), segment=segment)
    original = TeleopWorkload(workspace())
    assert selected.duration_s == duration
    assert selected.elapsed_s == 0 and selected.phase == phase
    for at in (0.0, duration / 2, duration - 0.1):
        selected.elapsed_s = at
        original.elapsed_s = offset + at
        assert selected.get_action() == original.get_action()
        assert selected.workspace == original.workspace
        assert selected.body_phase_elapsed == (pytest.approx(at) if segment == "body" else None)
    selected.elapsed_s = duration - 0.05
    selected.admit(10.0)
    selected.advance(10.1)
    assert selected.complete and selected.phase == "complete"
    assert selected.phases_completed == {phase}
    assert selected.body_phase_elapsed is None
    assert original.duration_s == 172.5


def test_segment_freezes_without_advancing_or_claiming_full_completion():
    selected = TeleopWorkload(workspace(), segment="continuity")
    selected.admit(1.0)
    selected.advance(1.1)
    selected.freeze()
    selected.advance(100.0)
    assert selected.elapsed_s == pytest.approx(0.1)
    assert not selected.complete and selected.phases_completed == set()
    selected.admit(100.0)
    selected.advance(100.1)
    assert selected.elapsed_s == pytest.approx(0.2)
    with pytest.raises(ValueError, match="segment"):
        TeleopWorkload(workspace(), segment="unrecognized")


def test_combined_body_selection_matches_last_five_original_cycles_and_body():
    selected = TeleopWorkload(workspace(), segment="combined-body")
    original = TeleopWorkload(workspace())
    assert selected.duration_s == 64.5 and selected._program_offset_s == 108.
    assert selected.elapsed_s == 0 and selected.phase == "combined"
    for at in (0., 6., 11.999, 12., 18., 24., 30., 36., 42., 48., 54., 59.999, 60., 60.1, 64.499):
        selected.elapsed_s, original.elapsed_s = at, 108. + at
        assert selected.get_action() == original.get_action()
        assert selected.phase == original.phase
        assert selected.body_phase_elapsed == original.body_phase_elapsed
        assert selected.workspace == original.workspace
    selected.elapsed_s = 59.9
    selected.admit(10.)
    selected.advance(10.1)
    assert selected.elapsed_s == 60. and selected.phase == "body"
    assert selected.body_phase_elapsed == 0. and selected.phases_completed == {"combined"}
    selected.freeze()
    selected.advance(100.)
    assert selected.elapsed_s == 60. and not selected.complete
    selected.elapsed_s = 64.4
    selected.admit(100.)
    selected.advance(100.1)
    assert selected.elapsed_s == 64.5 and selected.complete and selected.phase == "complete"
    assert selected.phases_completed == {"combined", "body"}
    assert selected.body_phase_elapsed is None
    assert original.duration_s == 172.5 and "individual" not in selected.phases_completed


def test_ordinary_arms_uses_explicit_legal_command_origin_and_original_measured_reference():
    value = workspace()
    shoulder = "arm_left_shoulder_lift.pos"
    value["reference"][shoulder] = 10.01
    origin = {**value["reference"], shoulder: 10.}
    selected = TeleopWorkload(value, segment="ordinary-arms", command_reference=origin)
    assert selected.duration_s == 12. and selected.absolute_targets is True
    assert selected.measured_reference == value["reference"] and selected.origin == origin
    assert selected.measured_reference[shoulder] == 10.01
    for at, expected in ((0., origin), (6., selected.targets), (12., origin)):
        selected.elapsed_s = at
        assert selected.get_action() == {key.removeprefix("arm_"): target for key, target in expected.items()}
        assert selected.body_phase_elapsed is None
    selected.elapsed_s = 3.
    expected = selected.get_action()
    selected.freeze()
    selected.advance(100.)
    assert selected.get_action() == expected and selected.origin == origin
    with pytest.raises(ValueError):
        TeleopWorkload(value, segment="ordinary-arms")


@pytest.mark.parametrize("segment", ["full", "combined", "continuity", "body", "combined-body"])
def test_ordinary_plan_never_labels_legacy_workloads_as_twelve_second_actions(segment):
    assert TeleopWorkload(workspace(), segment=segment).plan() is None


def intent_workload():
    from examples.alohamini import am1_teleop_workload as module

    assert hasattr(module, "VirtualIntentWorkload"), "changing virtual intent source is missing"
    now = [10.]
    value = workspace()
    source = module.VirtualIntentWorkload(value, command_reference=value["reference"], clock=lambda: now[0],
                                         intent_channel="11111111-1111-4111-8111-111111111111")
    return source, now, value


def intent(seq, *, op="move", issued=10., expiry=15., targets=None, seconds=1.):
    value = {"seq": seq, "op": op, "intent_channel": "11111111-1111-4111-8111-111111111111",
             "issued_at_perf_counter_s": issued,
             "expires_at_perf_counter_s": expiry}
    if op in {"move", "return"}:
        value["transition_seconds"] = seconds
    if op == "move":
        value["targets"] = targets or {"arm_left_elbow_flex.pos": 2.}
    return value


def test_changing_intent_selects_subset_then_hold_direction_return_and_finish():
    from examples.alohamini.am1_live_teleop import map_leader_action

    source, now, value = intent_workload()
    origin = dict(value["reference"])
    assert not source.active and not source.complete
    assert source.submit(intent(1))
    source.admit(20.)
    source.advance(20.1)
    first = map_leader_action(source.get_action())
    assert first["arm_left_elbow_flex.pos"] == pytest.approx(4.7)
    assert all(first[key] == target for key, target in origin.items() if key != "arm_left_elbow_flex.pos")
    source.accepted(first)
    assert source.submit(intent(2, op="hold"))
    source.admit(20.1)
    source.advance(20.2)
    assert map_leader_action(source.get_action()) == first
    assert source.submit(intent(3, targets={"arm_right_gripper.pos": 8.}, seconds=.5))
    source.admit(20.2)
    source.advance(20.3)
    changed = map_leader_action(source.get_action())
    assert changed["arm_left_elbow_flex.pos"] == pytest.approx(4.7)
    assert changed["arm_right_gripper.pos"] == pytest.approx(1.6)
    source.accepted(changed)
    assert source.submit(intent(4, op="return", seconds=.1))
    source.admit(20.3)
    source.advance(20.4)
    assert map_leader_action(source.get_action()) == origin
    assert source.submit(intent(5, op="finish")) and source.complete
    assert not source.active and source.body_phase_elapsed is None
    plan = source.plan()
    assert plan["input_mode"] == "changing-intent" and plan["program_active_seconds"] is None
    assert plan["measured_reference"] == value["reference"] and plan["command_reference"] == origin


def test_changing_intent_original_expiry_runs_through_recovery_without_catchup():
    source, now, value = intent_workload()
    assert source.submit(intent(1, expiry=10.3))
    source.admit(20.)
    source.advance(20.1)
    first = source.get_action()
    source.freeze()
    source.advance(100.)
    assert source.get_action() == first and source.elapsed_s == pytest.approx(.1)
    now[0] = 10.31
    assert not source.active
    with pytest.raises(source.InputUnavailable):
        source.get_action()
    assert not source.submit(intent(1, issued=10.31, expiry=20.))
    assert not source.submit(intent(2, issued=10., expiry=10.2))
    assert not source.active
    assert source.submit(intent(3, issued=10.31, expiry=11., targets={"arm_left_elbow_flex.pos": 1.5}))
    source.admit(100.)
    source.advance(100.1)
    assert source.get_action()["left_elbow_flex.pos"] == pytest.approx(4.65)
    assert source.origin == value["reference"] and source.measured_reference == value["reference"]


def test_changing_intent_expired_newer_intention_invalidates_obsolete_motion():
    source, now, _ = intent_workload()
    assert source.submit(intent(1))
    now[0] = 10.2
    assert not source.submit(intent(2, expiry=10.1))
    assert not source.active and source.command is None and source.phase == "waiting-intent"
    assert source.cursor == source.origin


def test_changing_intent_older_replay_cannot_invalidate_newer_current_intention():
    source, now, _ = intent_workload()
    assert source.submit(intent(2))
    now[0] = 10.2
    assert not source.submit(intent(1, expiry=10.1))
    assert source.active and source.phase == "intent-2-move" and source.command["seq"] == 2


def test_changing_intent_invalid_newer_target_invalidates_old_and_cannot_reuse_same_sequence():
    source, now, _ = intent_workload()
    assert source.submit(intent(1))
    with pytest.raises(ValueError):
        source.submit(intent(2, targets={"arm_left_gripper.pos": 11.}))
    assert not source.active and source.command is None
    assert not source.submit(intent(2, targets={"arm_left_gripper.pos": 2.}))


def test_virtual_intent_pure_decoder_preserves_retained_clock_without_current_time_or_state():
    from examples.alohamini import am1_teleop_workload as module

    assert hasattr(module, "decode_virtual_intent"), "shared closed intent decoder is missing"
    command = intent(1, issued=10., expiry=12.)
    decoded = module.decode_virtual_intent(command, intent_channel=command["intent_channel"])
    assert decoded == command and decoded is not command
    with pytest.raises(ValueError, match="channel"):
        module.decode_virtual_intent(command, intent_channel="22222222-2222-4222-8222-222222222222")


@pytest.mark.parametrize("change", [
    {"seq": True}, {"seq": 0}, {"issued_at_perf_counter_s": 10.1},
    {"expires_at_perf_counter_s": float("inf")}, {"expires_at_perf_counter_s": 10.},
    {"transition_seconds": 0.}, {"transition_seconds": True},
    {"targets": {}}, {"targets": {"body.x": 1.}},
    {"targets": {"arm_left_elbow_flex.pos": 12.41}},
    {"targets": {"arm_left_elbow_flex.pos": float("nan")}}, {"extra": 1},
    {"intent_channel": "22222222-2222-4222-8222-222222222222"},
])
def test_changing_intent_closed_contract_refuses_before_changing_legal_targets(change):
    source, now, value = intent_workload()
    command = intent(1)
    command.update(change)
    with pytest.raises(ValueError):
        source.submit(command)
    assert not source.active and source.cursor == value["reference"]


def left_entry_workload(seed=1268):
    from examples.alohamini.am1_live_teleop import LEFT_ENTRY_ENDPOINT_RAW, LEFT_ENTRY_WORKSPACE_PROFILE, normalized_raw
    from examples.alohamini.am1_teleop_workload import VirtualIntentWorkload

    value = workspace()
    value["workspace_profile"] = LEFT_ENTRY_WORKSPACE_PROFILE
    key = "arm_left_elbow_flex.pos"
    value["calibration"][key[:-4]] = [3, 0, 1845, 864, 3073]
    for joint, raw in value["raw_bounds"].items():
        cal = value["calibration"][joint[:-4]]
        held = seed if joint == key else raw[0]
        value["raw_bounds"][joint] = [LEFT_ENTRY_ENDPOINT_RAW, seed] if joint == key else [held, held]
        lo, hi = value["raw_bounds"][joint]
        value["reference"][joint] = normalized_raw(held, cal, gripper=joint.endswith("gripper.pos"))
        value["bounds"][joint] = dict(min=normalized_raw(lo, cal, gripper=joint.endswith("gripper.pos")),
                                    max=normalized_raw(hi, cal, gripper=joint.endswith("gripper.pos")),
                                    rate_per_s=4096/360*200/(3073-864) if joint == key else 1.)
    now = [10.]
    source = VirtualIntentWorkload(value, command_reference=value["reference"], left_entry=True,
                                  clock=lambda: now[0], intent_channel="11111111-1111-4111-8111-111111111111")
    endpoint = normalized_raw(LEFT_ENTRY_ENDPOINT_RAW, value["calibration"][key[:-4]])
    return source, now, value, endpoint


def test_left_entry_intent_uses_actual_calibration_slow_endpoint_and_fixed_other_goals():
    from examples.alohamini.am1_live_teleop import LEFT_ENTRY_ENDPOINT_RAW, map_leader_action

    source, now, value, endpoint = left_entry_workload()
    key = "arm_left_elbow_flex.pos"
    assert source.submit(intent(1, expiry=40., targets={key: endpoint}, seconds=15.))
    source.admit(20.)
    source.advance(20.1)
    action = map_leader_action(source.get_action())
    assert 0 < source.origin[key]-action[key] <= value["bounds"][key]["rate_per_s"]*.1
    assert all(action[joint] == origin for joint, origin in source.origin.items() if joint != key)
    plan = source.plan()
    assert plan["workspace_profile"] == value["workspace_profile"]
    assert plan["proposed_entry_endpoint_raw"] == LEFT_ENTRY_ENDPOINT_RAW and plan["proposed_entry_endpoint"] == endpoint
    assert plan["physical_endpoint_verified"] is False


@pytest.mark.parametrize("change", ["return", "away", "retired_endpoint", "nonselected", "fast", "too_long"])
def test_left_entry_refuses_return_away_other_joints_and_unsafe_ramp(change):
    source, now, value, endpoint = left_entry_workload()
    key = "arm_left_elbow_flex.pos"
    command = intent(1, expiry=40., targets={key: endpoint}, seconds=15.)
    if change == "return": command = intent(1, op="return", seconds=15.)
    elif change == "away": command["targets"][key] = source.origin[key]
    elif change == "retired_endpoint":
        from examples.alohamini.am1_live_teleop import normalized_raw
        command["targets"][key] = normalized_raw(1164, value["calibration"][key[:-4]])
    elif change == "nonselected": command["targets"]["arm_right_elbow_flex.pos"] = source.origin["arm_right_elbow_flex.pos"]
    elif change == "fast": command["transition_seconds"] = 1.
    elif change == "too_long": command["transition_seconds"] = 26.
    with pytest.raises(ValueError, match="entry"):
        source.submit(command)
    assert not source.active and source.cursor == source.origin


def test_left_entry_rejoin_uses_inward_held_cursor_without_renewing_original_budgets():
    from examples.alohamini.am1_live_teleop import map_leader_action, normalized_raw

    source, now, value, endpoint = left_entry_workload()
    key = "arm_left_elbow_flex.pos"
    assert source.submit(intent(1, expiry=30., targets={key: endpoint}, seconds=15.))
    source.admit(20.)
    for index in range(10):
        source.advance(20.+(index+1)/10)
    source.accepted(map_leader_action(source.get_action()))
    source.freeze()
    before = (source.elapsed_s, source.ramp_elapsed_s, dict(source.command), dict(source.origin))
    held = dict(source.cursor, **{key: normalized_raw(1250, value["calibration"][key[:-4]])})
    source.rejoin(held, remaining_wall_s=26.)
    assert map_leader_action(source.get_action()) == held
    assert (source.elapsed_s, source.ramp_elapsed_s, source.command, source.origin) == before
    source.admit(80.)
    source.advance(80.1)
    action = map_leader_action(source.get_action())
    assert endpoint < action[key] < held[key]
    assert held[key]-action[key] <= value["bounds"][key]["rate_per_s"]*.1
    assert source.ramp_elapsed_s == pytest.approx(1.1)
    source.freeze()
    with pytest.raises(ValueError):
        source.rejoin(dict(source.origin, **{key: source.origin[key]+1.}))
    now[0] = 30.1
    assert not source.active
    with pytest.raises(source.InputUnavailable): source.get_action()


def test_left_entry_hold_and_finish_are_inward_only_and_active_budget_is_not_reset():
    source, now, value, endpoint = left_entry_workload(seed=1165)
    key = "arm_left_elbow_flex.pos"
    assert source.submit(intent(1, expiry=40., targets={key: endpoint}, seconds=6.))
    source.admit(20.)
    for step in range(60): source.advance(20.+(step+1)/10)
    source.accepted({"arm_"+joint: value for joint, value in source.get_action().items()})
    assert source.submit(intent(2, op="hold", expiry=40.))
    assert source.get_action()[key.removeprefix("arm_")] == endpoint
    source.elapsed_s = 24.95
    source.admit(26.)
    source.advance(26.1)
    assert source.elapsed_s == 25. and not source.active
    assert not source.submit(intent(3, expiry=40., targets={key: endpoint}, seconds=6.))
    assert source.submit(intent(4, op="finish", expiry=40.)) and source.complete


def test_left_entry_fresh_qualified_limiter_retreat_rebases_without_old_source_clamp():
    from examples.alohamini.am1_live_teleop import map_leader_action, normalized_raw
    source, now, value, endpoint = left_entry_workload()
    key = "arm_left_elbow_flex.pos"
    assert source.submit(intent(1, expiry=40., targets={key: endpoint}, seconds=20.))
    source.admit(20.)
    for index in range(10): source.advance(20.+(index+1)/10)
    source.accepted(map_leader_action(source.get_action()))
    held = dict(source.cursor, **{key: normalized_raw(1267, value["calibration"][key[:-4]])})
    assert held[key] > source.cursor[key]
    source.freeze(); source.rejoin(held, remaining_wall_s=26.)
    assert map_leader_action(source.get_action()) == held
    assert source.elapsed_s == source.ramp_elapsed_s == 1.
    assert source.command["transition_seconds"] == 20. and source.command["expires_at_perf_counter_s"] == 40.
    source.admit(80.); source.advance(80.1)
    assert endpoint < map_leader_action(source.get_action())[key] < held[key]
    assert source.origin == value["reference"] and source.targets[key] == endpoint


def test_left_entry_current_profile_late_rejoin_replans_remaining_ramp_without_new_intent_or_budget():
    from examples.alohamini.am1_live_teleop import normalized_raw, map_leader_action
    source, now, value, endpoint = left_entry_workload(seed=1268)
    key = "arm_left_elbow_flex.pos"
    issued, expiry = 10., 55.
    now[0] = issued
    command = intent(1, issued=issued, expiry=expiry, targets={key:endpoint}, seconds=15.)
    assert source.submit(command)
    source.elapsed_s = source.ramp_elapsed_s = 14.735
    previous = dict(source.cursor, **{key:normalized_raw(1111,value["calibration"][key[:-4]])})
    source.accepted(previous)
    held = dict(source.cursor, **{key:normalized_raw(1213,value["calibration"][key[:-4]])})
    now[0] = 25.7255688
    source.freeze()
    source.rejoin(held, remaining_wall_s=26.)
    assert source.command == command and source.origin == value["reference"]
    assert source.elapsed_s == source.ramp_elapsed_s == 14.735
    assert source.get_action()[key.removeprefix("arm_")] == held[key]
    source.admit(100.)
    for index in range(94): source.advance(100.+(index+1)/10)
    result = map_leader_action(source.get_action())
    assert result[key] == endpoint and source.elapsed_s <= 25.
    assert all(result[joint] == value for joint,value in source.origin.items() if joint != key)
    assert source.plan()["physical_endpoint_verified"] is False


@pytest.mark.parametrize("budget", ["ttl", "active", "wall", "unknown_wall"])
def test_left_entry_actual_replan_refuses_only_insufficient_original_budgets(budget):
    from examples.alohamini.am1_live_teleop import normalized_raw
    source, now, value, endpoint = left_entry_workload()
    key = "arm_left_elbow_flex.pos"
    assert source.submit(intent(1, expiry=40., targets={key:endpoint}, seconds=15.))
    source.elapsed_s = source.ramp_elapsed_s = 14.735
    held = dict(source.cursor, **{key:normalized_raw(1213,value["calibration"][key[:-4]])})
    remaining_wall = 26.
    if budget == "ttl": now[0] = 38.
    elif budget == "active": source.elapsed_s = 24.
    elif budget == "wall": remaining_wall = 2.
    elif budget == "unknown_wall": remaining_wall = None
    before = (dict(source.command), dict(source.origin), source.elapsed_s, source.ramp_elapsed_s)
    with pytest.raises(ValueError, match="entry"):
        source.rejoin(held, remaining_wall_s=remaining_wall)
    assert (source.command, source.origin, source.elapsed_s, source.ramp_elapsed_s) == before
