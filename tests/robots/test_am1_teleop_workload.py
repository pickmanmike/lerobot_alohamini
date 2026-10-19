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
