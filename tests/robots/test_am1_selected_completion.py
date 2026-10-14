"""Selected two-cycle completion uses the existing protected atomic handoff."""

import copy

import pytest

from examples.alohamini.am1_session_contract import RECIPES
from tests.robots.test_am1_persistent_session import start
from tests.robots.test_am1_physical_authority import admit_live, build, completed_handoff

SELECTED = ["physical-left-elbow-compare", "physical-left-elbow-dwell-compare"]


def completion_boundary(tmp_path, recipe):
    clock, executor, authority = build(tmp_path)
    executor.supports = lambda candidate: candidate.name == recipe
    assert start(authority, recipe)["accepted"]
    admit_live(authority, executor, clock)
    clock.advance(RECIPES[recipe].trajectory_s + 3.1)
    receipt, _ = completed_handoff(authority, executor, clock)
    # The first captured selected run produced this 2-cycle/1-final-return receipt
    # before its intentional hold. Current native active ACK had already dropped,
    # while required camera and measured feedback still qualified.
    receipt["feedback_at_monotonic_s"] -= 0.102340448
    receipt["execution"].update(
        progress_s=RECIPES[recipe].trajectory_s,
        cycles_completed=2,
        returns_qualified=1,
    )
    original = executor.evidence
    executor.evidence = lambda: dict(original(), native_ack=False, pose_aligned=False)
    executor.advance = lambda *_: pytest.fail("atomic completion must precede hold recovery")
    return clock, executor, authority, receipt


@pytest.mark.parametrize("recipe", SELECTED)
def test_selected_completion_is_consumed_before_intentional_hold_recovery(tmp_path, recipe):
    clock, executor, authority, receipt = completion_boundary(tmp_path, recipe)
    try:
        deadline = authority.run["deadline"]
        original = copy.deepcopy(receipt)
        clock.advance(0.1)
        authority.tick()
        assert authority.run["status"] == "finishing"
        assert authority.run["requested_terminal"] == "completed"
        assert authority.run["recovery_episodes"] == 0
        assert authority.run["first_cause"] is None
        assert authority.run["deadline"] == deadline
        assert authority.run["completion_receipt"] == original
        assert authority.run["execution"] == original["execution"]
        assert executor.finishes == 1
    finally:
        authority.close()


@pytest.mark.parametrize("recipe", SELECTED)
@pytest.mark.parametrize(
    "returns,cycles", [(1, 0), (1, 1), (1, 3), (1, True), (0, 2), (2, 2), (True, 2)]
)
def test_selected_completion_requires_exact_typed_two_cycles_and_one_final_return(
    tmp_path, recipe, returns, cycles
):
    clock, executor, authority, receipt = completion_boundary(tmp_path, recipe)
    try:
        receipt["execution"].update(returns_qualified=returns, cycles_completed=cycles)
        authority.tick()
        assert authority.run["status"] == "recovering"
        assert authority.run["recovery_episodes"] == 1
        assert authority.run["first_cause"] == "required_proof_loss"
        assert "completion_receipt" not in authority.run
        assert executor.finishes == 0
    finally:
        authority.close()


@pytest.mark.parametrize("recipe", SELECTED)
@pytest.mark.parametrize("lost", ["feedback", "required_observation"])
def test_selected_completion_retains_current_required_input_gate(tmp_path, recipe, lost):
    clock, executor, authority, _ = completion_boundary(tmp_path, recipe)
    try:
        original = executor.evidence
        executor.evidence = lambda: dict(original(), **{lost: False})
        authority.tick()
        assert authority.run["status"] == "recovering"
        assert authority.run["recovery_episodes"] == 1
        assert "completion_receipt" not in authority.run
        assert executor.finishes == 0
    finally:
        authority.close()


@pytest.mark.parametrize(
    "recipe,returns,cycles",
    [
        ("sim-arm-smoke", 1, 1),
        ("physical-arm-smoke", 1, 1),
        ("sim-arm-smoke-repeat", 4, 4),
        ("physical-arm-smoke-repeat", 4, 4),
        ("physical-arm-hold-body", 1, 0),
    ],
)
def test_ordinary_completion_counts_remain_unchanged(tmp_path, recipe, returns, cycles):
    clock, executor, authority, receipt = completion_boundary(tmp_path, recipe)
    try:
        receipt["execution"].update(returns_qualified=returns, cycles_completed=cycles)
        authority.tick()
        assert authority.run["status"] == "finishing"
        assert authority.run["requested_terminal"] == "completed"
        assert authority.run["recovery_episodes"] == 0
        assert authority.run["completion_receipt"]["execution"] == receipt["execution"]
    finally:
        authority.close()
