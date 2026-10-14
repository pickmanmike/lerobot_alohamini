"""Selected Start descriptors remain bound through durable authority and presentation."""

import asyncio
import copy
import hashlib
import json
import uuid
from types import SimpleNamespace

import pytest

from examples.alohamini import am1_session_contract as contract
from tests.robots.test_am1_persistent_session import Clock, command
from tests.robots.test_am1_physical_authority import DelayedPhysicalExecutor, admit_live
from tests.robots.test_am1_selected_experiment import payload
from tools.am1_session_core import SessionAuthority
from tools.am1_session_service import Gateway

RECIPE = "physical-left-elbow-experiment"


def build(tmp_path, *, source="protected-physical-provider"):
    clock = Clock()
    executor = DelayedPhysicalExecutor(clock)
    executor.source = source
    executor.supports = lambda recipe: recipe.name in {RECIPE, "physical-left-elbow-positive-control"}
    executor.selected = []
    executor.set_selected_experiment = executor.selected.append
    authority = SessionAuthority(tmp_path, clock=clock, executor=executor)
    claim = authority.handle(command("claim"), "owner")
    request = command("start", operation_id=str(uuid.uuid4()), recipe=RECIPE,
                      controller_generation=claim["controller_generation"], selected_experiment=payload(minimum_response_raw=56))
    return clock, executor, authority, request


def test_selected_start_retains_exact_descriptor_hash_and_deduplicates_before_effect(tmp_path):
    clock, executor, authority, request = build(tmp_path)
    try:
        original = copy.deepcopy(request["selected_experiment"])
        result = authority.handle(request, "owner")
        assert result["accepted"] and result["status"] == "starting"
        digest = hashlib.sha256(json.dumps(original, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        assert authority.run["selected_experiment"] == original
        assert authority.run["selected_experiment_sha256"] == digest
        assert executor.selected[0].payload == original
        assert authority.handle(request, "owner")["run_id"] == result["run_id"]
        assert executor.started == 1 and len(executor.selected) == 1
        conflict = copy.deepcopy(request)
        conflict["selected_experiment"]["current_cap_ma"] = 99.0
        assert authority.handle(conflict, "owner")["reason"] == "operation conflict"
        request["selected_experiment"]["goal_readback_checkpoints_s"].append(6)
        assert authority.run["selected_experiment"] == original and executor.selected[0].payload == original
        persisted = json.loads(authority.db.execute("SELECT * FROM state").fetchone()[1])
        assert persisted["selected_experiment"] == original and persisted["selected_experiment_sha256"] == digest
    finally:
        authority.close()


@pytest.mark.parametrize("change", [None, {"raw_origin": 2458}, {"integral": 1}, {"current_cap_ma": 100.1},
                                    {"excursion_norm": True}, {"envelope_sha256": "0" * 64}])
def test_invalid_selected_start_refuses_before_executor_or_durable_effect(tmp_path, change):
    _, executor, authority, request = build(tmp_path)
    try:
        if change is None:
            request.pop("selected_experiment")
        else:
            request["selected_experiment"].update(change)
        result = authority.handle(request, "owner")
        assert result["accepted"] is False
        assert authority.run is None and executor.started == 0 and executor.selected == []
        assert authority.db.execute("SELECT count(*) FROM operations").fetchone()[0] == 0
    finally:
        authority.close()


@pytest.mark.parametrize("source", ["simulated-provider", "fake-provider", "protected-am1-host"])
def test_selected_recipe_requires_actual_physical_source(tmp_path, source):
    _, executor, authority, request = build(tmp_path, source=source)
    try:
        assert authority.handle(request, "owner")["accepted"] is False
        assert authority.run is None and executor.started == 0 and executor.selected == []
        assert RECIPE not in authority.snapshot()["recipes"]
    finally:
        authority.close()


def test_descriptor_for_other_recipe_and_initial_selected_confirmation_are_refused(tmp_path):
    _, executor, authority, request = build(tmp_path)
    try:
        old = dict(request, recipe="physical-left-elbow-positive-control")
        assert authority.handle(old, "owner")["accepted"] is False
        confirmation = dict(request, confirmation_of=str(uuid.uuid4()))
        assert authority.handle(confirmation, "owner")["accepted"] is False
        assert executor.started == 0 and executor.selected == []
        old.pop("selected_experiment")
        assert authority.handle(old, "owner")["accepted"] is True
        assert executor.selected == [None]
    finally:
        authority.close()


def test_selected_native_deadline_and_snapshot_duration_use_frozen_run(tmp_path):
    clock, executor, authority, request = build(tmp_path)
    request["selected_experiment"].update(excursion_norm=-3.0, ramp_s=3.0, endpoint_dwell_s=.5,
                                         live_ceiling_s=40.0, minimum_response_raw=17, return_tolerance_raw=6)
    try:
        assert authority.handle(request, "owner")["accepted"]
        clock.advance(2)
        admit_live(authority, executor, clock)
        assert authority.run["deadline"] == 42
        assert authority.snapshot()["run"]["trajectory_remaining_s"] == 12.5
        executor.live_at = 3
        clock.advance(1)
        authority.tick()
        assert authority.run["deadline"] == 42
    finally:
        authority.close()


def test_selected_descriptor_survives_restart_without_new_dispatch(tmp_path):
    clock, executor, authority, request = build(tmp_path)
    result = authority.handle(request, "owner")
    assert result["accepted"]
    original = copy.deepcopy(authority.run)
    authority.close()
    new_executor = DelayedPhysicalExecutor(clock)
    new_executor.source = "protected-physical-provider"
    new_executor.supports = executor.supports
    restarted = SessionAuthority(tmp_path, clock=clock, executor=new_executor)
    try:
        assert restarted.run["selected_experiment"] == original["selected_experiment"]
        assert restarted.run["selected_experiment_sha256"] == original["selected_experiment_sha256"]
        assert restarted.handle(request, "owner")["run_id"] == original["run_id"]
        assert new_executor.started == 0
    finally:
        restarted.close()


def test_gateway_selected_descriptor_scope_validation_and_control_frame_bound():
    async def scenario():
        gateway = Gateway.__new__(Gateway)
        gateway.auth = SimpleNamespace(capable=lambda _: True)
        calls = []

        async def call(c, d):
            calls.append(copy.deepcopy(c))
            return {"accepted": True}

        gateway.call = call
        valid = {"op": "start", "recipe": RECIPE, "selected_experiment": payload(minimum_response_raw=56)}
        device = {"control": True, "id": "owner"}
        await gateway.dispatch(valid, device)
        assert calls == [valid]
        for invalid in [dict(valid, op="pause"), dict(valid, recipe="physical-left-elbow-positive-control"),
                        {"op": "start", "recipe": RECIPE}, dict(valid, selected_experiment={"x": "a" * 4096})]:
            with pytest.raises(ValueError, match="experiment"):
                await gateway.dispatch(invalid, device)
        assert calls == [valid]

    asyncio.run(scenario())


def native_execution(run_id, host, *, moves=True):
    from tests.robots.test_am1_selected_experiment import definition, provider, run_selected

    selected = definition(minimum_response_raw=56)
    p = provider(selected)
    run_selected(p, moves=moves)
    outcome = p.positive_control
    outcome["visible_control"]["binding"].update(run_id=run_id, host_incarnation=host)
    rows = list(outcome["phase_receipts"].values()) + [outcome[k] for k in
            ("measured_origin_proof", "baseline_anchor_proof", "endpoint_anchor_proof",
             "endpoint_peak_proof", "first_return_proof")]
    for row in rows:
        row.update(run_id=run_id, host_incarnation=host)
    return dict(profile=selected.profile, original_seed=p.origin, initial_measured_reference=p.origin,
                experiment=selected.summary(), progress_s=28.0, cycles_completed=1,
                returns_qualified=int(moves), positive_control=outcome, feedback_sequence=840,
                admissions=1, complete=True, preparing=False, fault=None)


@pytest.mark.parametrize("moves", [True, False])
def test_selected_typed_completion_keeps_motion_verdict_separate_and_rejects_descriptor_tampering(tmp_path, moves):
    from tests.robots.test_am1_physical_authority import completed_handoff

    clock, executor, authority, request = build(tmp_path)
    try:
        assert authority.handle(request, "owner")["accepted"]
        admit_live(authority, executor, clock)
        clock.advance(28.1)
        receipt, current = completed_handoff(authority, executor, clock)
        execution = native_execution(authority.run["run_id"], executor.backend_incarnation, moves=moves)
        receipt.update(execution=execution, feedback_sequence=840, measured_positions=execution["original_seed"])
        receipt["accepted_host_action"].update(epoch=0, accepted_action_sequence=840)
        current.update(accepted_action_sequence=841)
        old = executor.evidence
        executor.evidence = lambda: dict(old(), feedback_sequence=842)
        assert authority._physical_completion(executor.evidence(), clock()) is not None
        assert execution["positive_control"]["coarse_functional_pass"] is moves
        original = copy.deepcopy(receipt)
        for change in ("payload", "hash", "execution"):
            saved = copy.deepcopy(authority.run)
            if change == "payload":
                authority.run["selected_experiment"]["current_cap_ma"] = 99.0
            elif change == "hash":
                authority.run["selected_experiment_sha256"] = "0" * 64
            else:
                receipt["execution"]["experiment"]["excursion_norm"] = 10.0
            assert authority._physical_completion(executor.evidence(), clock()) is None
            authority.run = saved
            receipt.clear()
            receipt.update(copy.deepcopy(original))
    finally:
        authority.close()


def test_selected_terminal_full_wrapper_references_complete_internal_native_execution():
    from tests.robots.test_am1_elbow_recovery_integration import reporting_authority
    from tests.robots.test_am1_physical_executor import terminal_cleanup_outcome
    from tools.am1_session_ipc import RESPONSE_LIMIT, encode

    run_id, host = str(uuid.uuid4()), str(uuid.uuid4())
    execution = native_execution(run_id, host)
    data = payload(minimum_response_raw=56)
    selected = contract.SelectedElbowExperiment.from_payload(data)
    lifecycle = terminal_cleanup_outcome(run_id, host, "completed")
    lifecycle.update(run_id=run_id, phase="terminal", backend_incarnation=host)
    receipt = dict(run_id=run_id, host_incarnation=host, recipe=RECIPE, execution=copy.deepcopy(execution))
    run = dict(run_id=run_id, backend_incarnation=host, recipe=RECIPE, source="protected-physical-provider",
               status="completed", progress_s=28.0, deadline=60.0, uncertain=False,
               cleanup="physical_readback_verified", execution=execution, completion_receipt=receipt,
               selected_experiment=data, selected_experiment_sha256=selected.sha256, physical_result=lifecycle)
    evidence = dict(source=run["source"], lifecycle=lifecycle, finite_completion=copy.deepcopy(receipt))
    authority = reporting_authority(run, evidence)
    original = copy.deepcopy(authority.run)
    snapshot = authority.snapshot()
    wrapper = {"id": str(uuid.uuid4()), "result": {"accepted": True, "status": "snapshot", "snapshot": snapshot}}
    assert len(encode(wrapper, RESPONSE_LIMIT)) < RESPONSE_LIMIT
    public = snapshot["run"]["completion_receipt"]
    from tests.robots.test_am1_status_details import resolve

    assert "execution" not in public
    assert resolve(authority, snapshot["run"]["details_reference"])["execution"] == execution
    assert resolve(authority, snapshot["evidence"]["details_reference"])["finite_completion"]["execution"] == execution
    assert snapshot["run"]["execution"]["cycles_completed"] == execution["cycles_completed"]
    assert authority.run == original
