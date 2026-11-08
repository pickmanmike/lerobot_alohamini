"""Whole-frame input invariants, without devices or transport."""

import copy
import importlib
from uuid import uuid4

import pytest

KEYS = tuple(
    f"arm_{side}_{joint}.pos"
    for side in ("left", "right")
    for joint in ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")
)


def contract():
    name = "examples.alohamini.am1_live_teleop"
    assert importlib.util.find_spec(name), "shared whole-frame teleop contract is missing"
    return importlib.import_module(name)


@pytest.mark.parametrize("value", [
    {}, {"profile": "ordinary-am1-arms-v1", "right_elbow_p": True},
    {"profile": "ordinary-am1-arms-v1", "right_elbow_p": 20},
    {"profile": "other", "right_elbow_p": 16},
    {"profile": "ordinary-am1-arms-v1", "right_elbow_p": 16, "extra": 1},
])
def test_ordinary_arm_policy_rejects_open_or_unsupported_settings(value):
    validate = getattr(contract(), "validate_ordinary_arm_profile", None)
    assert callable(validate), "ordinary profile must validate before authority or native settings"
    with pytest.raises(ValueError):
        validate(value)


@pytest.mark.parametrize("proportional", [16, 32])
def test_ordinary_arm_policy_is_closed_and_copied(proportional):
    validate = getattr(contract(), "validate_ordinary_arm_profile", None)
    assert callable(validate), "ordinary profile must validate before authority or native settings"
    value = {"profile": "ordinary-am1-arms-v1", "right_elbow_p": proportional}
    result = validate(value)
    assert result == value and result is not value


def source(seq=1, grant=None):
    return {
        "kind": "virtual-leaders",
        "source_id": "eea68073-9e21-4adc-ad23-6bf15009efe1",
        "sample_seq": seq,
        "acquired_at_monotonic_s": 100.01 + seq,
        "grant_received_at_monotonic_s": 100.0 + seq,
        "sent_at_monotonic_s": 100.02 + seq,
        "grant_id": grant or str(uuid4()),
    }


def workspace():
    calibration = {k.removesuffix(".pos"): [i % 6 + 1, 0, 0, 1000, 3000] for i, k in enumerate(KEYS)}
    value = {
        "version": 1,
        "units": {k: "gripper_opening_percent" if "gripper" in k else "normalized_arm" for k in KEYS},
        "bounds": {k: {"min": 0.0, "max": 10.0, "rate_per_s": 2.0} for k in KEYS},
        "reference": dict.fromkeys(KEYS, 0.0),
        "calibration": calibration,
        "raw_bounds": {k: [1000, 1200] if "gripper" in k else [2000, 2100] for k in KEYS},
    }
    for key, raw, bounds in ((KEYS[2], [2011, 2124], [1.1, 12.4]), (KEYS[8], [1847, 1960], [4.7, 16.0])):
        if key == KEYS[8]:
            value["calibration"][key.removesuffix(".pos")] = [3, 0, 0, 800, 2800]
        value["raw_bounds"][key] = raw
        value["bounds"][key].update(min=bounds[0], max=bounds[1])
        value["reference"][key] = 5.0
    return value


def test_mapping_preserves_both_sides_and_rejects_missing_or_extra_joints():
    m = contract()
    leader = {k.removeprefix("arm_"): float(i) for i, k in enumerate(KEYS)}
    assert m.map_leader_action(leader) == {k: float(i) for i, k in enumerate(KEYS)}
    for bad in (
        {**leader, "left_wrist_yaw.pos": 1},
        {k: v for k, v in leader.items() if k != "left_gripper.pos"},
    ):
        with pytest.raises(ValueError):
            m.map_leader_action(bad)


@pytest.mark.parametrize(
    "key,value", [(KEYS[0], True), (KEYS[1], float("nan")), (KEYS[2], 101), (KEYS[5], -1)]
)
def test_invalid_units_and_nonfinite_fields_never_become_motor_targets(key, value):
    with pytest.raises(ValueError):
        contract().validate_target({**dict.fromkeys(KEYS, 0.0), key: value})


def test_old_capture_cannot_be_made_fresh_by_a_new_grant_or_source_generation():
    m = contract()
    first = source()
    assert m.validate_source(first, grant_id=first["grant_id"]) == first
    for change in (
        {"grant_id": str(uuid4())},
        {"sample_seq": 2},
        {"source_id": str(uuid4())},
        {"grant_received_at_monotonic_s": first["acquired_at_monotonic_s"] + 0.001},
    ):
        bad = dict(first, **change)
        with pytest.raises(ValueError):
            m.validate_source(bad, grant_id=bad["grant_id"], previous=first)
    fresh = source(2)
    assert m.validate_source(fresh, grant_id=fresh["grant_id"], previous=first) == fresh


@pytest.mark.parametrize("profile,age,valid", [
    (None, .375, False),
    ("ordinary-arms-left-current-rest-1051-1164", .375, False),
    ("ordinary-am1-arms-v1", .375, True),
    ("ordinary-am1-arms-v1", .5, False),
])
def test_source_lifetime_is_scoped_to_the_admitted_ordinary_arm_profile(profile, age, valid):
    m = contract()
    frame = source()
    frame["sent_at_monotonic_s"] = frame["grant_received_at_monotonic_s"] + age
    if valid:
        assert m.validate_source(frame, grant_id=frame["grant_id"], profile=profile) == frame
    else:
        with pytest.raises(ValueError, match="remain fresh"):
            m.validate_source(frame, grant_id=frame["grant_id"], profile=profile)


def test_body_height_has_explicit_precedence_and_never_overrides_nonzero_velocity():
    m = contract()
    zero = {"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0, "lift_axis.vel": 0}
    assert m.validate_body(dict(zero, **{"lift_axis.height_mm": 50.0})) == dict(
        zero, **{"lift_axis.height_mm": 50.0}
    )
    for bad in (
        dict(zero, **{"x.vel": 0.151}),
        dict(zero, **{"lift_axis.vel": 200, "lift_axis.height_mm": 50}),
        dict(zero, **{"raw_motor_1": 1}),
    ):
        with pytest.raises(ValueError):
            m.validate_body(bad)


def test_workspace_checks_actual_calibration_roundtrip_and_target_bounds():
    m = contract()
    w = workspace()
    assert m.validate_workspace(w) == w
    target = dict.fromkeys(KEYS, 5.0)
    assert m.validate_target(target, workspace=w) == target
    with pytest.raises(ValueError):
        m.validate_target({**target, KEYS[0]: 11}, workspace=w)
    for field in ("raw_bounds", "calibration"):
        bad = copy.deepcopy(w)
        bad[field][KEYS[0] if field == "raw_bounds" else KEYS[0].removesuffix(".pos")][-1] += 100
        with pytest.raises(ValueError):
            m.validate_workspace(bad)


@pytest.mark.parametrize("mutation", ["span", "rate", "elbow"])
def test_workspace_cannot_expand_physical_travel_or_rate_when_roundtrip_is_valid(mutation):
    m, w = contract(), workspace()
    key = KEYS[2] if mutation == "elbow" else KEYS[0]
    if mutation == "rate":
        w["bounds"][key]["rate_per_s"] = 99.0
    else:
        w["raw_bounds"][key][1] += 1 if mutation == "elbow" else 100
        w["bounds"][key]["max"] += 0.1 if mutation == "elbow" else 10
    with pytest.raises(ValueError):
        m.validate_workspace(w)


@pytest.mark.parametrize("bad", [None, "absent_profile", "unknown_profile", "wrong_left", "wrong_right", "extra", "span", "rate", "calibration", "reference"])
def test_current_rest_workspace_only_admits_approved_exact_opt_in_geometry(bad):
    m, value = contract(), workspace()
    key = "arm_left_elbow_flex.pos"
    value["workspace_profile"] = "left-current-rest-1051-1164"
    value["calibration"][key.removesuffix(".pos")] = [3, 0, 1845, 864, 3073]
    value["raw_bounds"][key] = [1051, 1164]
    endpoints = [m.normalized_raw(raw, value["calibration"][key.removesuffix(".pos")]) for raw in (1051, 1164)]
    value["bounds"][key].update(min=endpoints[0], max=endpoints[1])
    value["reference"][key] = m.normalized_raw(1090, value["calibration"][key.removesuffix(".pos")])
    if bad == "absent_profile":
        value.pop("workspace_profile")
    elif bad == "unknown_profile":
        value["workspace_profile"] = "left-current-rest-1000-1200"
    elif bad == "wrong_left":
        value["raw_bounds"][key][0] -= 1
    elif bad == "wrong_right":
        value["raw_bounds"]["arm_right_elbow_flex.pos"] = [1051, 1164]
    elif bad == "extra":
        value["raw_goal"] = 1090
    elif bad == "span":
        value["raw_bounds"][key][1] += 1
    elif bad == "rate":
        value["bounds"][key]["rate_per_s"] = 99
    elif bad == "calibration":
        value["calibration"][key.removesuffix(".pos")][2] += 1
    elif bad == "reference":
        value["reference"][key] = endpoints[1] + .1
    if bad:
        with pytest.raises(ValueError):
            m.validate_workspace(value)
    else:
        assert m.validate_workspace(value) == value
        target = dict(value["reference"])
        target[key] = endpoints[1]
        assert m.validate_target(target, workspace=value) == target
        with pytest.raises(ValueError):
            m.validate_target(dict(target, **{key: endpoints[1] + .1}), workspace=value)


def test_ordinary_arms_workspace_is_a_distinct_closed_current_rest_profile():
    m, value = contract(), workspace()
    key = "arm_left_elbow_flex.pos"
    value["workspace_profile"] = "ordinary-arms-left-current-rest-1051-1164"
    value["calibration"][key.removesuffix(".pos")] = [3, 0, 1845, 864, 3073]
    value["raw_bounds"][key] = [1051, 1164]
    low, high = (m.normalized_raw(raw, value["calibration"][key.removesuffix(".pos")]) for raw in (1051, 1164))
    value["bounds"][key].update(min=low, max=high)
    value["reference"][key] = m.normalized_raw(1090, value["calibration"][key.removesuffix(".pos")])
    assert m.validate_workspace(value) == value
    changed = copy.deepcopy(value)
    changed["raw_bounds"][key][0] -= 1
    with pytest.raises(ValueError):
        m.validate_workspace(changed)


@pytest.mark.parametrize("fault", [None, "profile", "right", "calibration", "lower", "upper", "rate"])
def test_extended_left_gripper_workspace_is_exact_role_profile_mapping_and_interval(fault):
    m, value = contract(), workspace()
    elbow = "arm_left_elbow_flex.pos"
    value["workspace_profile"] = "ordinary-arms-left-current-rest-1051-1164"
    value["calibration"][elbow.removesuffix(".pos")] = [3, 0, 1845, 864, 3073]
    value["raw_bounds"][elbow] = [1051, 1164]
    endpoints = [m.normalized_raw(raw, value["calibration"][elbow.removesuffix(".pos")]) for raw in (1051, 1164)]
    value["bounds"][elbow].update(min=endpoints[0], max=endpoints[1])
    value["reference"][elbow] = m.normalized_raw(1090, value["calibration"][elbow.removesuffix(".pos")])
    key = "arm_right_gripper.pos" if fault == "right" else "arm_left_gripper.pos"
    cal = [6, 0, 934 if fault == "calibration" else 933, 1886, 3401]
    raw = [1885 if fault == "lower" else 1886, 3103 if fault == "upper" else 3102]
    value["calibration"][key.removesuffix(".pos")] = cal
    value["raw_bounds"][key] = raw
    low, high = [m.normalized_raw(v, cal, gripper=True) for v in raw]
    value["bounds"][key].update(min=low, max=high, rate_per_s=20.001 if fault == "rate" else 20.)
    value["reference"][key] = m.normalized_raw(3094, cal, gripper=True)
    if fault == "profile":
        value["workspace_profile"] = "left-current-rest-1051-1164"
    if fault:
        with pytest.raises(ValueError):
            m.validate_workspace(value)
    else:
        assert m.validate_workspace(value) == value
        target = dict(value["reference"], **{key: 0.})
        assert m.validate_target(target, workspace=value) == target
        with pytest.raises(ValueError):
            m.validate_target(dict(target, **{key: -.01}), workspace=value)


@pytest.mark.parametrize("profile,endpoint,upper,seed", [
    ("ordinary-arms-left-entry-p16-1164-1324",1164,1324,seed) for seed in (1165,1299,1324)
] + [("ordinary-arms-left-entry-p16-1108-1268",1108,1268,seed) for seed in (1165,1204,1268)])
def test_left_entry_workspace_is_closed_to_fresh_seed_and_single_joint(profile,endpoint,upper,seed):
    m, value = contract(), workspace()
    value["workspace_profile"] = profile
    for key in KEYS:
        cal = value["calibration"][key.removesuffix(".pos")]
        raw = value["raw_bounds"][key][0]
        value["raw_bounds"][key] = [raw, raw]
        point = m.normalized_raw(raw, cal, gripper=key.endswith("gripper.pos"))
        value["bounds"][key].update(min=point, max=point)
        value["reference"][key] = point
    key = "arm_left_elbow_flex.pos"
    cal = value["calibration"][key.removesuffix(".pos")] = [3, 0, 1845, 864, 3073]
    value["raw_bounds"][key] = [endpoint, seed]
    value["reference"][key] = m.normalized_raw(seed, cal)
    value["bounds"][key] = {"min": m.normalized_raw(endpoint, cal), "max": m.normalized_raw(seed, cal),
                            "rate_per_s": 4096 / 360 * 200 / (3073-864)}
    assert m.validate_workspace(value) == value
    for mutation in ("outer", "inner", "reference", "other", "rate"):
        bad = copy.deepcopy(value)
        if mutation == "outer": bad["raw_bounds"][key][1] = upper+1
        elif mutation == "inner": bad["raw_bounds"][key][0] = endpoint-1
        elif mutation == "reference": bad["reference"][key] -= .01
        elif mutation == "other": bad["raw_bounds"][KEYS[0]][1] += 1
        else: bad["bounds"][key]["rate_per_s"] *= 1.01
        with pytest.raises(ValueError): m.validate_workspace(bad)


def test_current_entry_descriptor_and_endpoint_constants_do_not_reinterpret_legacy_history():
    m=contract()
    assert m.LEFT_ENTRY_WORKSPACE_PROFILE == "ordinary-arms-left-entry-p16-1108-1268"
    assert (m.LEFT_ENTRY_ENDPOINT_RAW,m.LEFT_ENTRY_START_MIN_RAW,m.LEFT_ENTRY_START_MAX_RAW) == (1108,1165,1268)
    assert m.LEFT_ENTRY_START_MAX_RAW-m.LEFT_ENTRY_ENDPOINT_RAW == 160


def ordinary_start_workspace():
    m = contract()
    value = dict(version=1, workspace_profile=m.ORDINARY_AM1_ARMS_PROFILE,
        calibration={k:list(v) for k,v in m.ORDINARY_AM1_ARM_CALIBRATION.items()},
        units={}, bounds={}, raw_bounds={}, reference={})
    commands = {}
    for key,(low,high) in m.ORDINARY_AM1_RAW_BOUNDS.items():
        cal=value["calibration"][key[:-4]]; grip=key.endswith("gripper.pos")
        raw=(low+high)//2
        if key == "arm_left_shoulder_lift.pos": raw=3229
        elif key == "arm_left_wrist_flex.pos": raw=2760
        elif key == "arm_right_wrist_flex.pos": raw=3126
        goal=3223 if key == "arm_left_shoulder_lift.pos" else 2762 if key == "arm_left_wrist_flex.pos" else 3127 if key == "arm_right_wrist_flex.pos" else raw
        value["units"][key]="gripper_opening_percent" if grip else "normalized_arm"
        value["raw_bounds"][key]=[low,high]
        value["bounds"][key]=dict(min=m.normalized_raw(low,cal,gripper=grip),max=m.normalized_raw(high,cal,gripper=grip),rate_per_s=1.)
        value["reference"][key]=m.normalized_raw(raw,cal,gripper=grip)
        commands[key]=m.normalized_raw(goal,cal,gripper=grip)
    value["startup_acquisition"]=dict(version=1,displacement_bound_degrees=1.,measured_reference=dict(value["reference"]),command_reference=commands)
    return value


def test_ordinary_start_reference_allowance_never_widens_task_command_bounds():
    m,value=contract(),ordinary_start_workspace()
    assert m.validate_workspace(value)==value
    commands=value["startup_acquisition"]["command_reference"]
    assert m.validate_target(commands,workspace=value)==commands
    for key in ("arm_left_shoulder_lift.pos","arm_left_wrist_flex.pos","arm_right_wrist_flex.pos"):
        with pytest.raises(ValueError): m.validate_target(dict(commands,**{key:value["reference"][key]}),workspace=value)


@pytest.mark.parametrize("fault",["degree","bool","extra","command","far-reference","elbow-reference","foreign-profile","mapping","task-bounds","missing","arbitrary-shift"])
def test_ordinary_start_reference_allowance_is_exact_and_role_bound(fault):
    m,value=contract(),ordinary_start_workspace(); key="arm_right_wrist_flex.pos"
    a=value["startup_acquisition"]
    if fault == "degree": a["displacement_bound_degrees"]=2.
    elif fault == "bool": a["version"]=True
    elif fault == "extra": a["extra"]=True
    elif fault == "command": a["command_reference"][key]=value["reference"][key]
    elif fault == "far-reference": value["reference"][key]-=10.
    elif fault == "elbow-reference": value["reference"]["arm_left_elbow_flex.pos"]=100.
    elif fault == "foreign-profile": value["workspace_profile"]="ordinary-arms-left-current-rest-1051-1164"
    elif fault == "mapping": value["calibration"][key[:-4]][0]=5
    elif fault == "task-bounds": value["raw_bounds"][key][0]-=1
    elif fault == "arbitrary-shift": a["command_reference"]["arm_right_shoulder_pan.pos"]+=.01
    else: value.pop("startup_acquisition")
    with pytest.raises(ValueError): m.validate_workspace(value)


def test_ordinary_start_keeps_original_seed_maps_when_powered_measurement_has_residual():
    m,value=contract(),ordinary_start_workspace()
    original=copy.deepcopy(value["startup_acquisition"])
    value["reference"]["arm_left_shoulder_lift.pos"]=99.9
    value["reference"]["arm_right_shoulder_pan.pos"]+=.1
    assert m.validate_workspace(value)==value
    assert value["startup_acquisition"]==original
