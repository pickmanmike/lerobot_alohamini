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
