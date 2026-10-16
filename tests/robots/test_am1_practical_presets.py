"""Fixed ordinary practical elbow authority, independent of experiment requests."""

import copy
import hashlib
import json
from dataclasses import FrozenInstanceError

import pytest

from examples.alohamini import am1_session_contract as contract


RIGHT = "physical-live-right-elbow-practical-p16"
LEFT = "physical-live-left-elbow-practical-p16"
WORKSPACE_CALIBRATION = {
    "arm_left_shoulder_pan": (1, 0, -1761, 731, 3438),
    "arm_left_shoulder_lift": (2, 0, 515, 848, 3223),
    "arm_left_elbow_flex": (3, 0, 1845, 864, 3073),
    "arm_left_wrist_flex": (4, 0, -1939, 1314, 3208),
    "arm_left_wrist_roll": (5, 0, 879, 0, 4095),
    "arm_left_gripper": (6, 0, 933, 1886, 3401),
    "arm_right_shoulder_pan": (1, 0, -1719, 752, 3456),
    "arm_right_shoulder_lift": (2, 0, -1852, 1331, 3264),
    "arm_right_elbow_flex": (3, 0, -658, 827, 3039),
    "arm_right_wrist_flex": (4, 0, -1609, 1461, 3167),
    "arm_right_wrist_roll": (5, 0, 1869, 0, 4095),
    "arm_right_gripper": (6, 0, -1811, 1657, 3191),
}
WORKSPACE_GOALS = dict(zip(WORKSPACE_CALIBRATION,
                          (2199, 3223, 2124, 1546, 2200, 3167, 2141, 3252, 1845, 2772, 2443, 1981), strict=True))


def settings_proof():
    registers = dict(Firmware_Major_Version=3, Firmware_Minor_Version=10, Model_Number=777, ID=3,
                     Return_Delay_Time=0, Min_Position_Limit=864, Max_Position_Limit=3073, Homing_Offset=1845,
                     Phase=12, P_Coefficient=16, I_Coefficient=0, D_Coefficient=32, Operating_Mode=0,
                     Acceleration=254, Goal_Time=0, Goal_Velocity=0, Torque_Limit=1000, Maximum_Acceleration=254,
                     Torque_Enable=0, Lock=0, Goal_Position=2124, Present_Position=2124, Present_Current=0,
                     Status=0, Present_Voltage=118, Present_Temperature=31)
    before = dict(registers=registers, individual_bytes={"24": 0, "25": 0},
                  read_started_at_monotonic_s=1.0, read_completed_at_monotonic_s=1.001)
    active = copy.deepcopy(before)
    active["registers"].update(Torque_Enable=1, Lock=1, Present_Current=2)
    active.update(read_started_at_monotonic_s=1.002, read_completed_at_monotonic_s=1.003)
    return dict(version=1, binding=dict(run_id="run", host_incarnation="host", recipe=LEFT),
                before=before, active=active, unchanged=True, settings_sha256=None)


def preset(recipe=RIGHT):
    presets = getattr(contract, "PRACTICAL_ELBOW_PRESETS", {})
    assert recipe in presets, "ordinary practical recipe has no closed server preset"
    return presets[recipe]


def context(recipe=RIGHT, *, left_raw=2125, right_raw=1856):
    selected = "arm_" + ("left" if recipe == LEFT else "right") + "_elbow_flex"
    goals = dict(WORKSPACE_GOALS, arm_left_elbow_flex=left_raw, arm_right_elbow_flex=right_raw)
    pose = {}
    for name, raw in goals.items():
        material = WORKSPACE_CALIBRATION[name]
        fraction = (raw - material[3]) / (material[4] - material[3])
        pose[name + ".pos"] = fraction * (100 if name.endswith("_gripper") else 200) - (
            0 if name.endswith("_gripper") else 100
        )
    return dict(
        robot_model="alohamini1", selected_model="sts3215",
        calibrations={name: list(value) for name, value in WORKSPACE_CALIBRATION.items()},
        nonselected_goals={name: raw for name, raw in goals.items() if name != selected}, right_pose=pose,
    )


def proof(recipe=RIGHT, *, protection=310, current=2):
    value = settings_proof()
    selected = preset(recipe)
    value["binding"].update(recipe=recipe)
    material = dict(zip(("id", "drive_mode", "homing_offset", "range_min", "range_max"),
                        WORKSPACE_CALIBRATION[selected.spec.joint.removesuffix(".pos")], strict=True))
    for item in (value["before"], value["active"]):
        item["registers"].update(
            Min_Position_Limit=material["range_min"], Max_Position_Limit=material["range_max"],
            Homing_Offset=material["homing_offset"], Phase=12, Protection_Current=protection,
            Goal_Position=selected.reference_raw, Present_Position=selected.reference_raw,
        )
    value["active"]["registers"]["Present_Current"] = current
    rehash(value)
    return value


def rehash(value):
    dynamic = {"Torque_Enable", "Lock", "Goal_Position", "Present_Position", "Present_Current", "Status",
               "Present_Voltage", "Present_Temperature", "Protection_Current"}
    material = dict(registers={k: v for k, v in value["before"]["registers"].items() if k not in dynamic},
                    individual_bytes=value["before"]["individual_bytes"])
    value["settings_sha256"] = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@pytest.mark.parametrize("recipe,reference,endpoint,limits,margin,span,direction", [
    (RIGHT, 1847, 1960, (1847, 1960), (1813, 1994), 2212, 1),
    (LEFT, 2124, 2011, (2011, 2124), (1977, 2158), 2209, -1),
])
def test_ordinary_presets_freeze_ten_physical_degrees_per_side(recipe, reference, endpoint, limits, margin, span, direction):
    selected = preset(recipe)
    workspace = selected.workspace
    assert selected.reference_raw == reference and selected.direction == direction
    assert selected.target_raw(0) == reference and selected.target_raw(10) == endpoint
    assert (workspace.command_min_raw, workspace.command_max_raw) == limits
    assert (workspace.measured_min_raw, workspace.measured_max_raw) == margin
    assert selected.spec.rate_norm_per_s == 5 * 4096 / 360 * 200 / span
    assert selected.spec.current_cap_ma == 500 and selected.spec.live_s == 180
    assert contract.RECIPES[recipe].live_s == 180 and contract.RECIPES[recipe].mode == "interactive"
    assert contract.LIVE_ELBOW_SPECS[recipe] == selected.spec
    assert contract.LIVE_ELBOW_WORKSPACES[recipe] == workspace
    material = dict(zip(("id", "drive_mode", "homing_offset", "range_min", "range_max"),
                        WORKSPACE_CALIBRATION[selected.spec.joint.removesuffix(".pos")], strict=True))
    for raw in range(material["range_min"], material["range_max"] + 1):
        assert contract.SignedElbowResponse.encoded_goal(workspace.normalize(raw), material) == raw
    for raw in margin + limits:
        geometry = selected.geometry(raw, workspace.normalize(raw))
        assert geometry["measured_origin_raw"] == raw
        assert (geometry["corridor_min_raw"], geometry["corridor_max_raw"]) == limits
        assert geometry["workspace"] == workspace.descriptor
    for raw in (margin[0] - 1, margin[1] + 1):
        with pytest.raises(ValueError):
            selected.geometry(raw, workspace.normalize(raw))
    with pytest.raises(ValueError):
        selected.geometry(reference, workspace.normalize(reference + 1))
    assert selected.spec.summary()["supported_workspace"] == workspace.summary()
    assert workspace.summary()["maximum_excursion_degrees"] == 10


def test_ordinary_payload_is_closed_immutable_and_cannot_relabel_experiment():
    selected = preset()
    assert selected.to_payload() == {"recipe": RIGHT}
    assert contract.OrdinaryPracticalElbowConfig.from_payload(selected.to_payload()) == selected
    for payload in ({}, {"recipe": RIGHT, "reference_raw": 1848}, {"recipe": RIGHT, "proportional": 24},
                    {"recipe": contract.PRACTICAL_ELBOW_RECIPE}, {"recipe": "physical-live-left-elbow"}):
        with pytest.raises(ValueError):
            contract.OrdinaryPracticalElbowConfig.from_payload(payload)
    with pytest.raises((FrozenInstanceError, AttributeError)):
        selected.recipe = LEFT
    with pytest.raises(TypeError):
        contract.PRACTICAL_ELBOW_PRESETS[RIGHT] = preset(LEFT)
    for degrees in (True, 20, 9.9, -10, float("nan")):
        with pytest.raises(ValueError):
            selected.target_raw(degrees)
    legacy = contract.PracticalElbowConfig(1847, 1)
    assert legacy.target_raw(20) == 2074
    assert legacy.workspace.id == "am1-right-elbow-practical-v1"
    assert legacy.to_payload() == {"reference_raw": 1847, "direction": 1}


@pytest.mark.parametrize("recipe", [RIGHT, LEFT])
@pytest.mark.parametrize("left_raw,right_raw", [(1977, 1994), (2158, 1813), (2011, 1960), (2125, 1856)])
def test_shared_context_admits_cross_side_rest_without_mutating_actual_holds(recipe, left_raw, right_raw):
    value = context(recipe, left_raw=left_raw, right_raw=right_raw)
    original = copy.deepcopy(value)
    selected = preset(recipe)
    raw = left_raw if recipe == LEFT else right_raw
    admission = selected.workspace.admission(value, raw, selected.workspace.normalize(raw))
    assert value == original
    assert admission["measured_origin_raw"] == raw
    assert admission["workspace"] == selected.workspace.descriptor


@pytest.mark.parametrize("recipe", [RIGHT, LEFT])
@pytest.mark.parametrize("mutation", ["other_elbow", "other_goal", "calibration", "pose", "extra_pose", "missing_pose", "selected_goal"])
def test_shared_context_rejects_unqualified_goal_pose_mapping_or_shape(recipe, mutation):
    value = context(recipe)
    selected = preset(recipe)
    other = "arm_left_elbow_flex" if recipe == RIGHT else "arm_right_elbow_flex"
    if mutation == "other_elbow":
        value["nonselected_goals"][other] = 1976 if recipe == RIGHT else 1995
    elif mutation == "other_goal":
        value["nonselected_goals"]["arm_left_shoulder_pan"] += 1
    elif mutation == "calibration":
        value["calibrations"][selected.spec.joint.removesuffix(".pos")][2] += 1
    elif mutation == "pose":
        value["right_pose"]["arm_left_wrist_roll.pos"] += 1.001
    elif mutation == "extra_pose":
        value["right_pose"]["body.x"] = 0
    elif mutation == "missing_pose":
        value["right_pose"].pop(selected.spec.joint)
    else:
        value["nonselected_goals"][selected.spec.joint.removesuffix(".pos")] = selected.reference_raw
    with pytest.raises(ValueError):
        selected.workspace.qualify(value)


@pytest.mark.parametrize("recipe", [RIGHT, LEFT])
def test_read_only_settings_validate_actual_side_and_lower_firmware_current(recipe):
    selected = preset(recipe)
    value = proof(recipe)
    original = copy.deepcopy(value)
    assert selected.settings_valid(value, "run", "host")
    assert value == original
    assert not selected.settings_valid(value, "different-run", "host")
    assert not selected.settings_valid(value, "run", "different-host")
    assert not selected.settings_valid(proof(recipe, current=77))
    assert selected.settings_valid(proof(recipe, protection=20, current=19))
    assert not selected.settings_valid(proof(recipe, protection=20, current=20))
    assert not selected.settings_valid(proof(recipe, protection=0))


@pytest.mark.parametrize("recipe", [RIGHT, LEFT])
@pytest.mark.parametrize("mutation", ["gain", "mapping", "phase", "firmware", "protection_change", "hash", "extra", "bytes", "voltage", "thermal"])
def test_read_only_settings_refuse_tampered_or_foreign_proof(recipe, mutation):
    selected = preset(recipe)
    value = proof(recipe)
    if mutation == "protection_change":
        value["active"]["registers"]["Protection_Current"] += 1
    elif mutation == "hash":
        value["settings_sha256"] = "0" * 64
    elif mutation == "extra":
        value["active"]["registers"]["trusted"] = 1
    elif mutation == "bytes":
        value["active"]["individual_bytes"]["24"] = 99
    else:
        key, changed = {"gain": ("P_Coefficient", 24), "mapping": ("Homing_Offset", 0),
                        "phase": ("Phase", 0), "firmware": ("Firmware_Minor_Version", 9),
                        "voltage": ("Present_Voltage", 0), "thermal": ("Present_Temperature", 55)}[mutation]
        for item in (value["before"], value["active"]):
            item["registers"][key] = changed
        rehash(value)
    assert not selected.settings_valid(value)
