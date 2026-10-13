"""Owner gates for the real provider-generated, simulated two-cycle preset."""

import copy
import json
import os
import uuid
from pathlib import Path

import pytest

from examples.alohamini.am1_session_contract import (
    LEFT_ELBOW_COARSE_BENCH_V1,
    LEFT_ELBOW_COARSE_BENCH_V2,
    SELECTED_ELBOW_OPERATION_PRESET,
)
from tests.robots.test_am1_persistent_session import command
from tests.robots.test_am1_physical_authority import admit_live, completed_handoff
from tests.robots.test_am1_selected_experiment_authority import build


def execution_fixture(run_id, host):
    path = Path(
        os.environ.get(
            "AM1_OPERATION_EXECUTION_FIXTURE",
            ".cache/am1-operation-recovery-07/composition-implementation/simulated-two-cycle-execution.json",
        )
    )
    execution = json.loads(path.read_bytes())  # Mandatory real-provider simulation; not a physical outcome.

    def bind(value):
        if isinstance(value, dict):
            if "run_id" in value:
                value["run_id"] = run_id
            if "host_incarnation" in value:
                value["host_incarnation"] = host
            if value.get("context", {}).get("progress_s", -1) >= 26 and "epoch" in value:
                value["epoch"] = 2  # Actual final epoch differs after an acknowledged recovery.
            for item in value.values():
                bind(item)
        elif isinstance(value, list):
            for item in value:
                bind(item)

    bind(execution)
    execution["feedback_sequence"] = 1740
    assert LEFT_ELBOW_COARSE_BENCH_V1.completion_expectation(execution) == (58.0, 2, 2)
    return execution


def operation(tmp_path):
    clock, executor, authority, request = build(tmp_path)
    request["selected_experiment"] = LEFT_ELBOW_COARSE_BENCH_V1.payload
    assert authority.handle(request, "owner")["accepted"]
    admit_live(authority, executor, clock)
    return clock, executor, authority, request


def test_supported_versioned_preset_is_shared_metadata_with_its_exact_descriptor(tmp_path):
    _, _, authority, _ = build(tmp_path)
    try:
        assert authority.snapshot()["presets"] == [
            dict(
                id="left-elbow-negative-coarse-bench-v2",
                version=2,
                label="Left elbow negative coarse bench v2 · two cycles · fine position unqualified",
                recipe="physical-left-elbow-experiment",
                selected_experiment={**LEFT_ELBOW_COARSE_BENCH_V2.payload, "excursion_norm": -10},
                selected_experiment_sha256="86c16ecbcfcb864b0ddb9caaff25d306a9c4d991730f853d6e09e91b50bbdd1c",
            ),
            dict(SELECTED_ELBOW_OPERATION_PRESET, selected_experiment=LEFT_ELBOW_COARSE_BENCH_V2.payload),
        ]

        authority.executor.source = "fake-provider"
        assert authority.snapshot()["presets"] == []
    finally:
        authority.close()


def test_dynamic_two_cycle_final_epoch_completes_authority_only_after_native_cleanup(tmp_path):
    clock, executor, authority, _ = operation(tmp_path)
    try:
        deadline = authority.run["deadline"]
        clock.advance(58.1)
        receipt, current = completed_handoff(authority, executor, clock)
        execution = execution_fixture(authority.run["run_id"], executor.backend_incarnation)
        receipt.update(
            execution=execution, feedback_sequence=1740, measured_positions=execution["original_seed"]
        )
        receipt["accepted_host_action"].update(epoch=2, accepted_action_sequence=1740)
        current.update(epoch=3, accepted_action_sequence=1741)
        evidence = executor.evidence
        executor.evidence = lambda: dict(evidence(), feedback_sequence=1742)
        executor.advance = lambda *_: pytest.fail("completion must consume original atomic receipt")
        authority.tick()
        assert authority.run["status"] == "finishing" and authority.run["requested_terminal"] == "completed"
        assert authority.run["progress_s"] == 58.0 and authority.run["execution"]["cycles_completed"] == 2
        assert authority.run["execution"]["returns_qualified"] == 2
        assert authority.run["deadline"] == deadline == 90 and authority.run["first_cause"] is None
        executor.phase, executor.cleanup = "terminal", "physical_readback_verified"
        authority.tick()
        assert authority.run["status"] == "completed" and authority.run["uncertain"] is False
    finally:
        authority.close()


def test_distinct_local_losses_close_only_on_ack_then_consume_three_episode_budget(tmp_path):
    clock, executor, authority, request = operation(tmp_path)
    try:
        identity = (
            authority.run["run_id"],
            authority.run["deadline"],
            copy.deepcopy(authority.run["selected_experiment"]),
        )
        base = executor.evidence
        local = {"required_observation": True}
        executor.evidence = lambda: dict(base(), **local)
        for episode in range(1, 4):
            local["required_observation"] = False
            clock.advance(0.1)
            authority.tick()
            assert authority.run["status"] == "recovering" and authority.run["recovery_episodes"] == episode
            ceiling = authority.run["recovery"]["ceiling"]
            local["required_observation"], executor.dispatch_ack = True, False
            clock.advance(0.3)
            authority.tick()
            assert authority.run["status"] == "recovering" and authority.run["recovery"]["ceiling"] == ceiling
            executor.dispatch_ack = True
            authority.tick()
            assert authority.run["status"] == "running" and authority.run["recovery"] is None
            assert (
                authority.run["run_id"],
                authority.run["deadline"],
                authority.run["selected_experiment"],
            ) == identity
        local["required_observation"] = False
        authority.tick()
        assert authority.run["status"] == "finishing" and authority.run["requested_terminal"] == "faulted"
        assert authority.run["first_cause"] == "required_proof_loss"
        assert authority.run["recovery_episodes"] == 4 and executor.started == 1
        assert authority.handle(request, "owner")["run_id"] == identity[0] and executor.started == 1
    finally:
        authority.close()


@pytest.mark.parametrize("op", ["pause", "stop"])
def test_explicit_pause_stop_precedes_qualified_automatic_recovery(tmp_path, op):
    clock, executor, authority, _ = operation(tmp_path)
    try:
        run_id, deadline = authority.run["run_id"], authority.run["deadline"]
        executor.qualified = False
        authority.tick()
        dispatches = executor.dispatches
        assert authority.run["status"] == "recovering"
        assert authority.handle(command(op, run_id=run_id, operation_id=str(uuid.uuid4())), "viewer")[
            "accepted"
        ]
        executor.qualified = True
        clock.advance(0.2)
        authority.tick()
        assert authority.run["status"] == ("paused" if op == "pause" else "finishing")
        assert authority.run["deadline"] == deadline and executor.dispatches == dispatches
        assert authority.run["first_cause"] == "feedback_loss"
    finally:
        authority.close()


def test_negative_catalog_condition_uses_one_fresh_ordinary_start_with_exact_preserved_criteria(tmp_path):
    _, executor, authority, request = build(tmp_path)
    try:
        preset = next(
            p for p in authority.snapshot()["presets"] if p["id"] == "left-elbow-negative-coarse-bench-v2"
        )
        original = copy.deepcopy(preset["selected_experiment"])
        request["selected_experiment"] = preset["selected_experiment"]
        result = authority.handle(request, "owner")
        assert result["accepted"] and result["status"] == "starting"
        assert executor.started == 1 and len(executor.selected) == 1
        assert authority.run["selected_experiment"] == original
        assert (
            authority.run["selected_experiment_sha256"]
            == preset["selected_experiment_sha256"]
            == "86c16ecbcfcb864b0ddb9caaff25d306a9c4d991730f853d6e09e91b50bbdd1c"
        )
        assert original["excursion_norm"] == -10 and original["current_cap_ma"] == 150
        assert original["minimum_response_raw"] == 56 and original["return_tolerance_raw"] == 22
        assert original["cycle_count"] == 2 and original["intercycle_hold_s"] == 6
        assert authority.run["first_cause"] is None and authority.run["status"] != "completed"
        assert authority.handle(request, "owner")["run_id"] == result["run_id"] and executor.started == 1
        preset["selected_experiment"]["excursion_norm"] = 10
        preset["selected_experiment"]["goal_readback_checkpoints_s"].append(40)
        current = next(
            p for p in authority.snapshot()["presets"] if p["id"] == "left-elbow-negative-coarse-bench-v2"
        )
        assert current["selected_experiment"] == authority.run["selected_experiment"] == original
        assert authority.snapshot()["run"]["trajectory_remaining_s"] == 58
    finally:
        authority.close()
