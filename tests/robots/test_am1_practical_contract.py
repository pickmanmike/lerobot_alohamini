"""Practical elbow geometry, using the retained actual rest-context operands."""

import copy
import math

import pytest

from examples.alohamini import am1_session_contract as contract


RECIPE = "physical-live-right-elbow-practical"


def config(direction=1, reference_raw=1847):
    return contract.PracticalElbowConfig.from_payload(dict(reference_raw=reference_raw, direction=direction))


@pytest.mark.parametrize("direction,endpoint10,endpoint20", [(1, 1960, 2074), (-1, 1734, 1620)])
def test_practical_targets_use_degrees_inward_counts_and_installed_mapping(direction, endpoint10, endpoint20):
    selected = config(direction)
    material = dict(drive_mode=0, homing_offset=-658, range_min=827, range_max=3039)
    assert selected.target_raw(10) == endpoint10
    assert selected.target_raw(20) == endpoint20
    for degrees in (10, 20):
        raw = selected.target_raw(degrees)
        normalized = selected.target_norm(degrees)
        assert contract.SignedElbowResponse.encoded_goal(normalized, material) == raw
        assert abs(raw - 1847) * 360 / 4096 <= degrees
        assert abs(raw - 1847) == math.floor(degrees * 4096 / 360)
    assert selected.spec.rate_norm_per_s == 5 * 4096 / 360 * 200 / 2212
    assert selected.spec.current_cap_ma == 500
    assert selected.spec.live_s == contract.RECIPES[RECIPE].live_s == 180
    assert selected.spec.practical_motion is True
    assert selected.spec.summary()["practical_motion"] is True
    assert "supported_workspace" not in selected.spec.summary()


@pytest.mark.parametrize("payload", [
    {}, {"reference_raw": 1847}, {"reference_raw": 1847, "direction": 0},
    {"reference_raw": True, "direction": 1}, {"reference_raw": 1847.0, "direction": 1},
    {"reference_raw": 1847, "direction": True}, {"reference_raw": 1847, "direction": 1, "live_s": 301},
    {"reference_raw": 3000, "direction": 1}, {"reference_raw": 900, "direction": -1},
])
def test_practical_configuration_is_closed_and_cannot_leave_calibrated_range(payload):
    with pytest.raises(ValueError):
        contract.PracticalElbowConfig.from_payload(payload)


def test_practical_workspace_is_fixed_while_actual_q0_is_fresh():
    selected = config()
    workspace = selected.workspace
    descriptor = copy.deepcopy(workspace.descriptor)
    for raw in (1813, 1845, 1847, 1860, 2074, 2108):
        geometry = selected.geometry(raw, workspace.normalize(raw))
        assert geometry["measured_origin_raw"] == raw
        assert (geometry["corridor_min_raw"], geometry["corridor_max_raw"]) == (1847, 2074)
        assert (geometry["measured_min_raw"], geometry["measured_max_raw"]) == (1813, 2108)
        assert geometry["workspace"] == descriptor
    for raw in (1812, 2109):
        with pytest.raises(ValueError):
            selected.geometry(raw, workspace.normalize(raw))
    with pytest.raises(ValueError):
        selected.geometry(1847, workspace.normalize(1848))
    assert selected.to_payload() == {"reference_raw": 1847, "direction": 1}
    assert workspace.descriptor == descriptor
    assert set(descriptor) == {"version", "id", "context_sha256", "evidence_min_raw", "evidence_max_raw"}
    assert descriptor["id"] == "am1-right-elbow-practical-v1"
    assert config(-1).workspace.descriptor["context_sha256"] != descriptor["context_sha256"]
    assert config(reference_raw=1848).workspace.descriptor["context_sha256"] != descriptor["context_sha256"]


def test_practical_admission_reuses_retained_v2_context_without_changing_actual_eleven_goals():
    from tests.robots.test_am1_live_native import wrist_workspace_context

    context = wrist_workspace_context(2444, left_wrist_raw=1547, shoulder_pan_raw=2138,
                                      left_elbow_raw=2125, left_gripper_raw=3168)
    original = copy.deepcopy(context)
    workspace = config().workspace
    geometry = workspace.admission(context, 1845, workspace.normalize(1845))
    assert geometry["measured_origin_raw"] == 1845
    assert context == original
    context["nonselected_goals"]["arm_left_shoulder_pan"] += 1
    with pytest.raises(ValueError):
        workspace.admission(context, 1845, workspace.normalize(1845))


def test_practical_mode_does_not_change_legacy_specs_or_workspace_identity():
    left = contract.LIVE_ELBOW_SPECS["physical-live-left-elbow"]
    right = contract.LIVE_ELBOW_SPECS["physical-live-right-elbow"]
    workspace = contract.LIVE_ELBOW_WORKSPACES[right.recipe]
    assert left.current_cap_ma == right.current_cap_ma == 150
    assert left.rate_norm_per_s == right.rate_norm_per_s == 1
    assert left.live_s == right.live_s == 90
    assert "practical_motion" not in left.summary()
    assert "practical_motion" not in right.summary()
    assert workspace.id == "am1-right-elbow-rest-neighbourhood-v2"
    assert (workspace.command_min_raw, workspace.command_max_raw) == (1834, 1867)
    assert RECIPE not in contract.LIVE_ELBOW_WORKSPACES


def test_practical_normalized_targets_roundtrip_every_supported_integer_count():
    material = dict(drive_mode=0, homing_offset=-658, range_min=827, range_max=3039)
    workspace = config().workspace
    for raw in range(827, 3040):
        normalized = workspace.normalize(raw)
        assert contract.SignedElbowResponse.encoded_goal(normalized, material) == raw
        assert abs(normalized - contract.LiveWorkspaceSpec.normalize(raw)) < 1e-9


@pytest.mark.parametrize("reference_raw,direction", [(827, 1), (2812, 1), (1054, -1), (3039, -1)])
def test_practical_measured_margin_clamps_to_installed_calibrated_limits(reference_raw, direction):
    workspace = config(direction, reference_raw).workspace
    assert 827 <= workspace.measured_min_raw <= workspace.command_min_raw
    assert workspace.command_max_raw <= workspace.measured_max_raw <= 3039
    workspace.geometry(reference_raw, workspace.normalize(reference_raw))
