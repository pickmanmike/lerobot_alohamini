"""Reconciliation consumes actual stopped proof without rewriting failed physical history."""

import copy
import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path

import pytest

from tests.robots.test_am1_persistent_session import Clock, command, start
from tests.robots.test_am1_physical_authority import DelayedPhysicalExecutor
from tools.am1_session_core import SessionAuthority


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def terminal_authority(tmp_path, receipt=None):
    clock = Clock()
    executor = DelayedPhysicalExecutor(clock)
    executor.source = "protected-physical-provider"
    authority = SessionAuthority(tmp_path, clock=clock, executor=executor)
    result = start(authority, "sim-arm-smoke-repeat")
    clock.advance(180)
    authority.tick()
    executor.phase = "terminal"
    executor.cleanup = "physical_cleanup_unknown"
    executor.uncertain = True
    executor.qualified = False
    authority.tick()
    authority.handle(command("claim"), "owner")
    executor.reconcile = lambda *args, **kwargs: receipt
    return authority, executor, result["run_id"]


def acknowledged(run, operation_id):
    incarnation = run["physical_result"]["backend_incarnation"]
    return {
        "version": 1,
        "kind": "am1-actual-stopped-reconciliation",
        "run_id": run["run_id"],
        "host_incarnation": incarnation,
        "cleanup": "physical_readback_verified",
        "uncertain": False,
        "original_terminal_run_sha256": hashlib.sha256(canonical(run)).hexdigest(),
        "acknowledgment": {
            "version": 1,
            "run_id": run["run_id"],
            "host_incarnation": incarnation,
            "operation_id": operation_id,
            "proof_sha256": "a" * 64,
            "consumed_operation_id": operation_id,
            "markers_archived": True,
            "validated": True,
        },
    }


def test_actual_stopped_reconciliation_does_not_require_ended_live_feedback_or_rewrite_failure(tmp_path):
    authority, executor, run_id = terminal_authority(tmp_path)
    try:
        before = copy.deepcopy(authority.run)
        request = command("reconcile", run_id=run_id)
        proof = acknowledged(before, request["operation_id"])
        executor.reconcile = lambda *args, **kwargs: proof
        result = authority.handle(request, "owner")
        assert result["accepted"], result
        assert result["status"] == "reconciled"
        assert authority.run["uncertain"] is False
        assert authority.run["reconciliation"] == proof
        for key, value in before.items():
            if key != "uncertain":
                assert authority.run[key] == value, key
        assert authority.run["status"] == "faulted"
        assert authority.handle(request, "owner") == result
    finally:
        authority.close()


def full_sized_receipt(authority, operation_id):
    """Full stopped records/calibration and monitor events, without hardware access."""
    _, _, proof = stopped_proof()
    rows = proof["readback"]["calibration_comparison"] = []
    for side in ("left", "right"):
        motors = [
            "arm_" + side + "_" + joint
            for joint in (
                "shoulder_pan",
                "shoulder_lift",
                "elbow_flex",
                "wrist_flex",
                "wrist_roll",
                "gripper",
            )
        ]
        if side == "left":
            motors += ["base_left_wheel", "base_back_wheel", "base_right_wheel", "lift_axis"]
        for motor in motors:
            for register, value in (
                ("ID", 1),
                ("Model_Number", 777),
                ("Homing_Offset", 0),
                ("Min_Position_Limit", 0),
                ("Max_Position_Limit", 4095),
            ):
                rows.append(
                    {
                        "bus": side,
                        "motor": motor,
                        "register": register,
                        "value": value,
                        "cached_expected": value,
                        "matches": True,
                        "read_started_at_monotonic_s": 99.12345678901234,
                        "read_completed_at_monotonic_s": 99.12445678901234,
                    }
                )
    stationary = proof["readback"]["lift_stationary"]
    for record in stationary["samples"]:
        record.update(
            acceleration_raw=254,
            elapsed_s=0.41234567890123,
            present_load_raw=0,
            lock=0,
            voltage_v=11.5,
            temperature_window={
                "aggregation": "maximum_per_occupied_slot",
                "count": 5,
                "high_count": 0,
                "numeric_outlier_count": 0,
                "ready": True,
                "slot_s": 0.1,
                "span_s": 0.41234567890123,
                "values_c": [34] * 5,
            },
        )
    stationary["events"] = copy.deepcopy(stationary["samples"])
    # The failed native cleanup retains its actual readback as separate history.
    authority.run["physical_result"]["readback"] = copy.deepcopy(proof["readback"])
    before = copy.deepcopy(authority.run)
    accepted = acknowledged(before, operation_id)
    proof.update({key: value for key, value in accepted.items() if key != "acknowledgment"})
    proof["acknowledgment"] = accepted["acknowledgment"]
    proof["acknowledgment"].update(
        original_terminal_run_sha256=proof["original_terminal_run_sha256"],
        completed_at_monotonic_s=100.02,
        validation_basis="fresh-native-proof",
    )
    return proof


def test_snapshot_references_full_actual_reconciliation_within_existing_ipc_limit_without_mutation(tmp_path):
    from tools.am1_session_ipc import RESPONSE_LIMIT, encode

    authority, executor, run_id = terminal_authority(tmp_path)
    try:
        request = command("reconcile", run_id=run_id)
        proof = full_sized_receipt(authority, request["operation_id"])
        # A real-sized full proof plus original cleanup exceeds the wire contract.
        with pytest.raises(ValueError, match="message too large"):
            encode(
                {"id": str(uuid.uuid4()), "result": {"run": dict(authority.run, reconciliation=proof)}},
                RESPONSE_LIMIT,
            )
        executor.reconcile = lambda *args, **kwargs: proof
        assert authority.handle(request, "owner")["accepted"]
        original = copy.deepcopy(authority.run)
        durable = authority.db.execute("SELECT data FROM state WHERE id=1").fetchone()[0]
        snapshot = authority.snapshot()
        wire = encode({"id": str(uuid.uuid4()), "result": snapshot}, RESPONSE_LIMIT)
        reference = snapshot["run"]["reconciliation"]
        assert len(wire) <= RESPONSE_LIMIT
        assert reference["kind"] == "am1-actual-stopped-reconciliation"
        assert reference["presentation_version"] == 1
        assert reference["evidence_reference"] == {
            "kind": "am1-stopped-reconciliation-reference",
            "version": 1,
        }
        for key in (
            "run_id",
            "host_incarnation",
            "reconciliation_id",
            "original_terminal_run_sha256",
            "cleanup",
            "uncertain",
            "started_at_monotonic_s",
            "completed_at_monotonic_s",
            "acknowledgment",
        ):
            assert reference[key] == proof[key]
        assert reference["proof_sha256"] == proof["acknowledgment"]["proof_sha256"]
        assert "readback" not in reference and "source" not in reference
        assert authority.run == original and authority.run["reconciliation"] == proof
        assert authority.db.execute("SELECT data FROM state WHERE id=1").fetchone()[0] == durable
        snapshot["run"]["reconciliation"]["acknowledgment"]["validated"] = False
        assert authority.run == original
    finally:
        authority.close()


@pytest.mark.parametrize(
    "broken", ["kind", "ack", "run", "hash", "readback", "timestamp", "source", "readback_shape", "ack_size"]
)
def test_snapshot_does_not_project_malformed_reconciliation_as_qualified(tmp_path, broken):
    authority, executor, run_id = terminal_authority(tmp_path)
    try:
        proof = full_sized_receipt(authority, str(uuid.uuid4()))
        authority.run.update(uncertain=False, reconciliation=proof)
        if broken == "kind":
            proof["kind"] = "simulated-stopped-reconciliation"
        elif broken == "ack":
            del proof["acknowledgment"]
        elif broken == "run":
            proof["run_id"] = str(uuid.uuid4())
        elif broken == "hash":
            proof["original_terminal_run_sha256"] = "b" * 64
        elif broken == "readback":
            proof["readback"]["verified"] = False
        elif broken == "source":
            executor.source = "fake"
        elif broken == "readback_shape":
            proof["readback"] = ["not a qualified readback"]
        elif broken == "ack_size":
            proof["acknowledgment"]["unexpected_unbounded_data"] = "x" * 4096
        else:
            proof["completed_at_monotonic_s"] = "not a native acquisition timestamp"
        before = copy.deepcopy(authority.run)
        assert authority.snapshot()["run"]["reconciliation"] == proof
        assert authority.run == before and authority.run["run_id"] == run_id
    finally:
        authority.close()


@pytest.mark.parametrize("broken", ["boolean", "run", "incarnation", "hash", "ack", "operation"])
def test_physical_reconcile_refuses_boolean_foreign_or_unacknowledged_proof(tmp_path, broken):
    authority, executor, run_id = terminal_authority(tmp_path)
    try:
        request = command("reconcile", run_id=run_id)
        proof = acknowledged(authority.run, request["operation_id"])
        if broken == "boolean":
            proof = True
        elif broken == "run":
            proof["run_id"] = str(uuid.uuid4())
        elif broken == "incarnation":
            proof["host_incarnation"] = str(uuid.uuid4())
        elif broken == "hash":
            proof["original_terminal_run_sha256"] = "b" * 64
        elif broken == "ack":
            del proof["acknowledgment"]
        else:
            proof["acknowledgment"]["consumed_operation_id"] = str(uuid.uuid4())
        executor.qualified = True
        executor.reconcile = lambda *args, **kwargs: proof
        before = copy.deepcopy(authority.run)
        assert not authority.handle(request, "owner")["accepted"]
        assert authority.run == before
    finally:
        authority.close()


def stopped_proof():
    """Literal stopped sample/packet fixture; no simulated servo or transport."""
    run_id, incarnation = str(uuid.uuid4()), str(uuid.uuid4())
    run = {
        "run_id": run_id,
        "status": "faulted",
        "uncertain": True,
        "native_first_cause": "original thermal-window stale",
        "physical_result": {"backend_incarnation": incarnation},
    }
    config = {"physical_state_directory": "/private/state", "source_pins": "/private/manifest"}
    samples = []
    for n in range(9):
        payload = [0] * 38
        payload[0] = 1
        payload[23:25] = [41, 3]
        payload[29:31] = [115, 34]
        payload[36:38] = [2, 0]
        packet = [255, 255, 11, 40, 0, *payload]
        packet.append((~sum(packet[2:])) & 255)
        samples.append(
            {
                "phase": "reconciliation_stationary",
                "sample_monotonic_s": 99.6 + n * 0.05,
                "request_duration_s": 0.001,
                "operating_mode": 1,
                "torque_enable": 0,
                "goal_velocity_raw": 0,
                "present_position_raw": 809,
                "present_velocity_raw": 0,
                "moving": 0,
                "present_current_raw": 2,
                "present_current_ma": 13.0,
                "voltage_raw": 115,
                "temperature_c": 34,
                "status": 0,
                "group_trace": {
                    "request_bytes": [255, 255, 254, 5, 130, 33, 38, 11, 40],
                    "response_bytes": packet,
                    "comm_result": 0,
                    "sdk_servo_error": 0,
                },
            }
        )
    motors = [
        (side, "arm_" + side + "_" + joint, "Torque_Enable")
        for side in ("left", "right")
        for joint in ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")
    ]
    motors += [
        ("left", motor, register)
        for motor in ("base_left_wheel", "base_back_wheel", "base_right_wheel", "lift_axis")
        for register in ("Torque_Enable", "Goal_Velocity")
    ]
    proof = {
        "version": 1,
        "kind": "am1-actual-stopped-reconciliation",
        "run_id": run_id,
        "host_incarnation": incarnation,
        "reconciliation_id": str(uuid.uuid4()),
        "cleanup": "physical_readback_verified",
        "uncertain": False,
        "no_actuation": True,
        "original_terminal_run_sha256": hashlib.sha256(canonical(run)).hexdigest(),
        "original_first_cause": run["native_first_cause"],
        "original_cleanup_sha256": "a" * 64,
        "source": {"backend_contract": config, "manifest_sha256": "b" * 64},
        "original_markers": {
            "cleanup-uncertain.json": {"sha256": "a" * 64},
            "physical-in-progress.json": None,
        },
        "started_at_monotonic_s": 99.0,
        "completed_at_monotonic_s": 100.01,
        "boot_id": "00000000-0000-0000-0000-000000000001",
        "proof_max_age_s": 180.0,
        "admission": {
            "both_locks_held": True,
            "canonical_path": "/private/state/active.lock",
            "serial_path": "/private/state/serial-owner.lock",
        },
        "readback": {
            "verified": True,
            "buses_closed": True,
            "lift_readback": "qualified",
            "selected_gain_restore_required": False,
            "gain_i_readback": 0,
            "gain_i_evidence": {
                "bus": "left",
                "motor": "arm_left_shoulder_lift",
                "register": "I_Coefficient",
                "value": 0,
                "read_started_at_monotonic_s": 99.4,
                "read_completed_at_monotonic_s": 99.401,
            },
            "readbacks": [
                {
                    "bus": s,
                    "motor": m,
                    "register": r,
                    "value": 0,
                    "read_started_at_monotonic_s": 99.2,
                    "read_completed_at_monotonic_s": 99.201,
                }
                for s, m, r in motors
            ],
            "lift_stationary": {
                "samples": samples,
                "qualification_started_at_monotonic_s": 99.6,
                "qualified_at_monotonic_s": 100.001,
                "configuration": {
                    "id": 11,
                    "model_number": 777,
                    "operating_mode": 1,
                    "torque_enable": 0,
                    "goal_velocity_raw": 0,
                    "min_voltage_limit_raw": 55,
                    "max_voltage_limit_raw": 145,
                },
                "qualification": {
                    "sample_count": 9,
                    "window_s": 0.4,
                    "expected_torque": 0,
                    "expected_goal_velocity_raw": 0,
                    "position_excursion_raw": 0,
                    "position_drift_raw": 0,
                },
                "temperature_slots": [[99.6 + n * 0.1, 34] for n in range(5)],
                "temperature_ready": True,
                "temperature_max_age_s": 0.5,
            },
        },
        "camera_release": {
            "state": "released",
            "session_id": run_id,
            "errors": [],
            "release_evidence": dict.fromkeys(
                ("backend_exited", "readers_joined", "viewer_socket_closed", "runtime_config_removed"), True
            ),
        },
        "camera_release_sha256": "c" * 64,
        "owner_audit_before": {
            "host_unit_main_pid": 0,
            "camera_unit_main_pid": 0,
            "bus_owner_pids": [],
            "host_process_pids": [],
        },
        "owner_audit_after": {
            "host_unit_main_pid": 0,
            "camera_unit_main_pid": 0,
            "bus_owner_pids": [],
            "host_process_pids": [],
        },
    }
    return config, run, proof


def test_actual_stopped_raw_packets_complete_readback_and_fresh_thermal_slots_qualify():
    from tools import am1_physical_reconciliation as m

    config, run, proof = stopped_proof()
    assert m.validate_stopped_proof(config, run, proof, now=100.1, boot_id=proof["boot_id"]) == proof


@pytest.mark.parametrize(
    "broken",
    [
        "duplicate",
        "gap",
        "late",
        "velocity",
        "moving",
        "position",
        "torque",
        "goal",
        "status",
        "mode",
        "current",
        "voltage",
        "temperature",
        "checksum",
        "packet_mismatch",
        "thermal_duplicate",
        "thermal_stale",
        "rows",
        "row_time",
        "bus_closed",
        "gain",
        "camera",
        "owner",
        "actuation",
        "source",
        "original_hash",
    ],
)
def test_unsafe_incomplete_or_misbound_stopped_evidence_cannot_reconcile(broken):
    from tools import am1_physical_reconciliation as m

    config, run, proof = stopped_proof()
    samples = proof["readback"]["lift_stationary"]["samples"]
    if broken == "duplicate":
        samples[1]["sample_monotonic_s"] = samples[0]["sample_monotonic_s"]
    elif broken == "gap":
        samples[1]["sample_monotonic_s"] += 0.2
    elif broken == "late":
        samples[-1]["sample_monotonic_s"] = 100.601
    elif broken in {
        "velocity",
        "moving",
        "position",
        "torque",
        "goal",
        "status",
        "mode",
        "current",
        "voltage",
        "temperature",
    }:
        key, value = {
            "velocity": ("present_velocity_raw", 100),
            "moving": ("moving", 1),
            "position": ("present_position_raw", 812),
            "torque": ("torque_enable", 1),
            "goal": ("goal_velocity_raw", 200),
            "status": ("status", 1),
            "mode": ("operating_mode", 0),
            "current": ("present_current_ma", 2000),
            "voltage": ("voltage_raw", 0),
            "temperature": ("temperature_c", 55),
        }[broken]
        for sample in samples[-1:] if broken == "position" else samples:
            sample[key] = value
            if broken == "current":
                sample["present_current_raw"] = 308
                sample["present_current_ma"] = 2002.0
            packet = sample["group_trace"]["response_bytes"]
            payload = packet[5:-1]
            for offset, field in (
                (13, "goal_velocity_raw"),
                (23, "present_position_raw"),
                (25, "present_velocity_raw"),
                (36, "present_current_raw"),
            ):
                raw = sample[field]
                encoded = abs(raw) | (32768 if raw < 0 else 0)
                payload[offset : offset + 2] = [encoded & 255, encoded >> 8]
            for offset, field in (
                (0, "operating_mode"),
                (7, "torque_enable"),
                (29, "voltage_raw"),
                (30, "temperature_c"),
                (32, "status"),
                (33, "moving"),
            ):
                payload[offset] = sample[field]
            packet[5:-1] = payload
            packet[-1] = (~sum(packet[2:-1])) & 255
        if broken == "temperature":
            proof["readback"]["lift_stationary"]["temperature_slots"] = [
                [99.6 + n * 0.1, 55] for n in range(5)
            ]
    elif broken == "checksum":
        samples[0]["group_trace"]["response_bytes"][-1] ^= 1
    elif broken == "packet_mismatch":
        samples[0]["present_position_raw"] = 810
    elif broken == "thermal_duplicate":
        proof["readback"]["lift_stationary"]["temperature_slots"][1][0] = 99.6
    elif broken == "thermal_stale":
        proof["readback"]["lift_stationary"]["temperature_slots"][0][0] = 99.0
    elif broken == "rows":
        proof["readback"]["readbacks"].pop()
    elif broken == "row_time":
        proof["readback"]["readbacks"][0]["read_completed_at_monotonic_s"] += 0.05
    elif broken == "bus_closed":
        proof["readback"]["buses_closed"] = False
    elif broken == "gain":
        proof["readback"]["gain_i_readback"] = 1
    elif broken == "camera":
        proof["camera_release"]["release_evidence"]["readers_joined"] = False
    elif broken == "owner":
        proof["owner_audit_after"]["bus_owner_pids"] = [123]
    elif broken == "actuation":
        proof["no_actuation"] = False
    elif broken == "source":
        proof["source"]["backend_contract"] = {}
    else:
        proof["original_terminal_run_sha256"] = "d" * 64
    with pytest.raises((ValueError, RuntimeError)):
        m.validate_stopped_proof(config, run, proof, now=100.1, boot_id=proof["boot_id"])


def await_state(worker, wanted):
    end = time.monotonic() + 2
    while time.monotonic() < end and worker.state not in wanted:
        time.sleep(0.005)
    assert worker.state in wanted, (worker.state, worker.error)


def worker_fixture(tmp_path, monkeypatch, gate=None):
    from tools import am1_physical_reconciliation as m

    config, run, proof = stopped_proof()
    state = tmp_path / "state"
    state.mkdir()
    config["physical_state_directory"] = str(state)
    proof["source"]["backend_contract"] = config
    original = b"original unknown receipt preserved\n"
    (state / "cleanup-uncertain.json").write_bytes(original)
    proof["original_markers"]["cleanup-uncertain.json"] = {"sha256": hashlib.sha256(original).hexdigest()}
    raw = canonical(proof)
    (state / ("reconciliation-" + run["run_id"] + ".json")).write_bytes(raw)
    releases = []

    class Guard:
        def close(self):
            releases.append(True)

    monkeypatch.setattr(m, "acquire_stopped_guard", lambda *_: Guard())
    monkeypatch.setattr(m, "_private_bytes", lambda path: Path(path).read_bytes())
    if os.name == "nt":
        monkeypatch.setattr(m, "_private_directory", lambda *_: None)
        monkeypatch.setattr(m, "_fsync_directory", lambda *_: None)

    def load(*_):
        if gate is not None:
            gate.wait(1)
        return proof, raw, {"cleanup-uncertain.json": original, "physical-in-progress.json": None}

    monkeypatch.setattr(m, "load_qualified_proof", load)
    monkeypatch.setattr(m, "assert_no_owner", lambda *_: None)
    monkeypatch.setattr(m, "current_boot_id", lambda: proof["boot_id"])
    proof["completed_at_monotonic_s"] = time.monotonic()
    raw = canonical(proof)
    (state / ("reconciliation-" + run["run_id"] + ".json")).write_bytes(raw)
    return m, config, run, proof, state, original, releases


def test_reconcile_validation_ack_and_consumption_are_separate_nonblocking_operations(tmp_path, monkeypatch):
    gate = threading.Event()
    m, config, run, proof, state, original, releases = worker_fixture(tmp_path, monkeypatch, gate)
    first, second, third = map(str, (uuid.uuid4(), uuid.uuid4(), uuid.uuid4()))
    worker = m.ReconciliationWorker(config, run, first)
    try:
        before = time.monotonic()
        assert worker.request(second) is False
        assert time.monotonic() - before < 0.1
        assert (state / "cleanup-uncertain.json").read_bytes() == original
        gate.set()
        await_state(worker, {"qualified"})
        assert worker.request(first) is False
        assert worker.request(second) is False
        await_state(worker, {"acknowledged"})
        assert not (state / "cleanup-uncertain.json").exists()
        archive = state / "reconciliation-archives" / run["run_id"] / "cleanup-uncertain.json"
        assert archive.read_bytes() == original
        assert worker.request(second) is False
        receipt = worker.request(third)
        assert receipt["run_id"] == run["run_id"]
        assert receipt["acknowledgment"]["operation_id"] == second
        assert receipt["acknowledgment"]["consumed_operation_id"] == third
        worker.thread.join(1)
        assert releases == [True]
    finally:
        gate.set()
        worker.close()
        worker.thread.join(1)


def test_cache_close_before_ack_keeps_unknown_markers_and_releases_owned_locks(tmp_path, monkeypatch):
    m, config, run, proof, state, original, releases = worker_fixture(tmp_path, monkeypatch)
    worker = m.ReconciliationWorker(config, run, str(uuid.uuid4()))
    await_state(worker, {"qualified"})
    worker.close()
    worker.thread.join(1)
    assert (state / "cleanup-uncertain.json").read_bytes() == original
    assert releases == [True]
    assert not list(state.glob("reconciliation-ack-*"))


def test_changed_marker_after_validation_refuses_ack_without_erasing_unknown(tmp_path, monkeypatch):
    m, config, run, proof, state, original, releases = worker_fixture(tmp_path, monkeypatch)
    worker = m.ReconciliationWorker(config, run, str(uuid.uuid4()))
    try:
        await_state(worker, {"qualified"})
        changed = b"foreign durable owner"
        (state / "cleanup-uncertain.json").write_bytes(changed)
        assert worker.request(str(uuid.uuid4())) is False
        await_state(worker, {"error"})
        assert (state / "cleanup-uncertain.json").read_bytes() == changed
        assert not list(state.glob("reconciliation-ack-*"))
    finally:
        worker.close()
        worker.thread.join(1)


def test_executor_queues_reconciliation_instead_of_boolean_clear(tmp_path, monkeypatch):
    from tests.robots.test_am1_physical_executor import observation
    from tools.am1_physical_executor import PhysicalExecutor

    gate = threading.Event()
    m, config, run, proof, state, original, releases = worker_fixture(tmp_path, monkeypatch, gate)
    executor = PhysicalExecutor(config, observation())
    try:
        request = str(uuid.uuid4())
        assert executor.reconcile(run, operation_id=request) is False
        assert executor._reconciliation_worker is not None
        assert executor.reconcile(run, operation_id=str(uuid.uuid4())) is False
        assert executor.evidence()["feedback"] is False
    finally:
        gate.set()
        executor.close()
        if getattr(executor, "_reconciliation_worker", None):
            executor._reconciliation_worker.thread.join(1)


def test_guarded_cache_expiry_leaves_original_unknown_markers_untouched(tmp_path, monkeypatch):
    m, config, run, proof, state, original, releases = worker_fixture(tmp_path, monkeypatch)
    clock = Clock()
    worker = m.ReconciliationWorker(config, run, str(uuid.uuid4()), clock=clock)
    await_state(worker, {"qualified"})
    clock.advance(5)
    assert worker.request(str(uuid.uuid4())) is False
    worker.thread.join(1)
    assert worker.state == "expired"
    assert releases == [True]
    assert (state / "cleanup-uncertain.json").read_bytes() == original


def test_durable_ack_retry_reuses_archived_original_without_rewriting_first_ack(tmp_path, monkeypatch):
    m, config, run, proof, state, original, releases = worker_fixture(tmp_path, monkeypatch)
    first_ack = str(uuid.uuid4())
    worker = m.ReconciliationWorker(config, run, str(uuid.uuid4()))
    await_state(worker, {"qualified"})
    worker.request(first_ack)
    await_state(worker, {"acknowledged"})
    worker.close()
    worker.thread.join(1)
    path = state / ("reconciliation-ack-" + run["run_id"] + ".json")
    persisted = path.read_bytes()
    retry = m.ReconciliationWorker(config, run, str(uuid.uuid4()))
    try:
        await_state(retry, {"qualified"})
        retry.request(str(uuid.uuid4()))
        await_state(retry, {"acknowledged"})
        receipt = retry.request(str(uuid.uuid4()))
        assert receipt["acknowledgment"]["operation_id"] == first_ack
        assert path.read_bytes() == persisted
        assert receipt["original_terminal_run_sha256"] == proof["original_terminal_run_sha256"]
    finally:
        retry.close()
        retry.thread.join(1)


@pytest.mark.parametrize("broken", ["host_active", "audit_error", "unit_active", "unit_job", "unit_error"])
def test_resident_private_devices_owner_audit_refuses_active_or_failed_process_inventory(monkeypatch, broken):
    from types import SimpleNamespace

    from tools import am1_physical_reconciliation as m

    def result(args, **kwargs):
        if args[0] == "pgrep":
            if broken == "host_active":
                return SimpleNamespace(returncode=0, stdout="123\n", stderr="")
            if broken == "audit_error":
                return SimpleNamespace(returncode=2, stdout="", stderr="permission denied")
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        assert args[0] == "systemctl", "PrivateDevices owner must not inspect hidden /dev or open hardware"
        code = 1 if broken == "unit_error" else 0
        pid = "123" if broken == "unit_active" else "0"
        job = "123/start" if broken == "unit_job" else ""
        return SimpleNamespace(
            returncode=code,
            stdout=f"MainPID={pid}\nControlPID=0\nJob={job}\nActiveState=inactive\n",
            stderr="",
        )

    monkeypatch.setattr(m.subprocess, "run", result)
    with pytest.raises((ValueError, RuntimeError)):
        m.assert_no_owner({}, str(uuid.uuid4()))


@pytest.mark.parametrize("broken", ["age", "boot"])
def test_proof_from_old_boot_or_expired_native_acquisition_is_not_reconciliation(broken):
    from tools import am1_physical_reconciliation as m

    config, run, proof = stopped_proof()
    now = 280.011 if broken == "age" else 100.1
    boot = str(uuid.uuid4()) if broken == "boot" else proof["boot_id"]
    with pytest.raises(ValueError):
        m.validate_stopped_proof(config, run, proof, now=now, boot_id=boot)


def loader_fixture(tmp_path, monkeypatch):
    from tools import am1_physical_reconciliation as m

    config, run, proof = stopped_proof()
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    runroot = tmp_path / "runs"
    run_directory = runroot / run["run_id"][:8]
    run_directory.mkdir(parents=True)
    camera = tmp_path / "camera"
    camera.mkdir()
    config.update(physical_state_directory=str(state), run_root=str(runroot), camera_state=str(camera))
    proof["admission"].update(
        canonical_path=str(state) + "/active.lock", serial_path=str(state) + "/serial-owner.lock"
    )
    old = {
        "version": 1,
        "run_id": run["run_id"],
        "host_incarnation": proof["host_incarnation"],
        "cleanup": "physical_cleanup_unknown",
        "uncertain": True,
        "first_cause": run["native_first_cause"],
        "readback": {"verified": False},
    }
    run["physical_result"].update(readback=old, camera_release=proof["camera_release"])
    proof["original_terminal_run_sha256"] = hashlib.sha256(canonical(run)).hexdigest()
    original = canonical(old) + b"\n"
    proof["original_cleanup_sha256"] = hashlib.sha256(original).hexdigest()
    proof["original_markers"]["cleanup-uncertain.json"] = {"sha256": hashlib.sha256(original).hexdigest()}
    (state / "cleanup-uncertain.json").write_bytes(original)
    (run_directory / "cleanup.json").write_bytes(original)
    camera_raw = canonical(proof["camera_release"]) + b"\n"
    (camera / ("release-" + run["run_id"] + ".json")).write_bytes(camera_raw)
    proof["camera_release_sha256"] = hashlib.sha256(camera_raw).hexdigest()
    raw = canonical(proof)
    path = state / ("reconciliation-" + run["run_id"] + ".json")
    path.write_bytes(raw)
    monkeypatch.setattr(m, "_private_bytes", lambda path: Path(path).read_bytes())
    monkeypatch.setattr(m, "_private_directory", lambda *_: None)
    monkeypatch.setattr(m, "_validate_sources", lambda *_: None)
    if os.name == "nt":
        monkeypatch.setattr(m, "_fsync_directory", lambda *_: None)
    monkeypatch.setattr(m, "current_boot_id", lambda: proof["boot_id"])
    monkeypatch.setattr(m, "assert_no_owner", lambda *_: None)
    from types import SimpleNamespace

    monkeypatch.setattr(m, "time", SimpleNamespace(monotonic=lambda: 100.1))
    return m, config, run, proof, state, original, raw


def test_literal_loader_binds_original_unknown_receipt_camera_and_markers(tmp_path, monkeypatch):
    m, config, run, proof, state, original, raw = loader_fixture(tmp_path, monkeypatch)
    actual, bytes_read, markers = m.load_qualified_proof(config, run)
    assert actual == proof and bytes_read == raw
    assert markers == {"cleanup-uncertain.json": original, "physical-in-progress.json": None}


@pytest.mark.parametrize(
    "broken", ["missing_marker", "marker", "receipt", "camera", "proof_nonfinite", "unknown_marker"]
)
def test_literal_loader_cannot_promote_missing_changed_or_nonfinite_originals(tmp_path, monkeypatch, broken):
    m, config, run, proof, state, original, raw = loader_fixture(tmp_path, monkeypatch)
    if broken == "missing_marker":
        (state / "cleanup-uncertain.json").unlink()
    elif broken == "marker":
        (state / "cleanup-uncertain.json").write_bytes(b"{}")
    elif broken == "receipt":
        (Path(config["run_root"]) / run["run_id"][:8] / "cleanup.json").write_bytes(b"{}")
    elif broken == "camera":
        (Path(config["camera_state"]) / ("release-" + run["run_id"] + ".json")).write_bytes(b"{}")
    elif broken == "proof_nonfinite":
        (state / ("reconciliation-" + run["run_id"] + ".json")).write_bytes(raw[:-1] + b',"unreviewed":NaN}')
    else:
        (state / "physical-in-progress.json").write_bytes(b"{}")
    with pytest.raises((ValueError, RuntimeError)):
        m.load_qualified_proof(config, run)


def test_loader_uses_durable_actual_ack_and_exact_archives_when_originals_already_removed(
    tmp_path, monkeypatch
):
    m, config, run, proof, state, original, raw = loader_fixture(tmp_path, monkeypatch)
    archive = state / "reconciliation-archives" / run["run_id"]
    archive.mkdir(parents=True)
    (archive / "cleanup-uncertain.json").write_bytes(original)
    (state / "cleanup-uncertain.json").unlink()
    ack = {
        "version": 1,
        "run_id": run["run_id"],
        "host_incarnation": proof["host_incarnation"],
        "proof_sha256": hashlib.sha256(raw).hexdigest(),
        "validated": True,
        "markers_archived": True,
        "original_terminal_run_sha256": proof["original_terminal_run_sha256"],
        "operation_id": str(uuid.uuid4()),
    }
    (state / ("reconciliation-ack-" + run["run_id"] + ".json")).write_bytes(canonical(ack))
    assert m.load_qualified_proof(config, run)[2]["cleanup-uncertain.json"] == original
    (archive / "cleanup-uncertain.json").write_bytes(b"foreign")
    with pytest.raises(ValueError):
        m.load_qualified_proof(config, run)


@pytest.mark.skipif(os.name != "posix", reason="native POSIX permission semantics")
def test_private_literal_files_and_preserved_calibration_modes_reject_redirects(tmp_path):
    from tools import am1_physical_reconciliation as m

    path = tmp_path / "private.json"
    path.write_bytes(b"{}")
    path.chmod(0o600)
    assert m._private_bytes(path) == b"{}"
    path.chmod(0o664)
    with pytest.raises(ValueError):
        m._private_bytes(path)
    assert m._calibration_bytes(path) == b"{}"
    assert path.stat().st_mode & 0o777 == 0o664
    link = tmp_path / "redirect.json"
    link.symlink_to(path)
    with pytest.raises((ValueError, OSError)):
        m._calibration_bytes(link)


def test_next_physical_run_can_queue_its_own_reconciliation_after_consumed_prior_cache(monkeypatch):
    from types import SimpleNamespace

    from examples.alohamini.am1_session_contract import RECIPES
    from tests.robots.test_am1_physical_executor import observation
    from tools import am1_physical_reconciliation as m
    from tools.am1_physical_executor import PhysicalExecutor

    executor = PhysicalExecutor({"private": "mock"}, observation())
    executor._reconciliation_worker = SimpleNamespace(
        thread=SimpleNamespace(is_alive=lambda: False), state="consumed", run={"old": "run"}
    )
    executor.bind_run(str(uuid.uuid4()), RECIPES["physical-arm-smoke"])
    created = []

    class Queued:
        def __init__(self, *args):
            created.append(args)

    monkeypatch.setattr(m, "ReconciliationWorker", Queued)
    current = {"run_id": executor.run_id, "status": "faulted", "uncertain": True}
    assert executor.reconcile(current, operation_id=str(uuid.uuid4())) is False
    assert created and created[0][1] == current


def test_atomic_immutable_publication_failure_does_not_leave_partial_final_evidence(tmp_path, monkeypatch):
    from tools import am1_physical_reconciliation as m

    monkeypatch.setattr(m, "_private_bytes", lambda p: Path(p).read_bytes())
    if os.name == "nt":
        monkeypatch.setattr(m, "_fsync_directory", lambda *_: None)
    path = tmp_path / "actual-ack.json"
    original = m._publish_no_replace

    def refuse(temporary, final):
        assert temporary.read_bytes() == b"complete durable actual evidence\n"
        assert not final.exists()
        raise OSError("mock publication interruption")

    monkeypatch.setattr(m, "_publish_no_replace", refuse)
    with pytest.raises(OSError):
        m._write_new_or_same(path, b"complete durable actual evidence\n")
    assert not path.exists()
    monkeypatch.setattr(m, "_publish_no_replace", original)
    m._write_new_or_same(path, b"complete durable actual evidence\n")
    assert path.read_bytes() == b"complete durable actual evidence\n"
    assert path.stat().st_nlink == 1
    with pytest.raises(ValueError):
        m._write_new_or_same(path, b"foreign completed evidence")
    assert path.read_bytes() == b"complete durable actual evidence\n"


@pytest.mark.parametrize("failure", ["archive-parent", "existing-ack-parent", "existing-archive-leaf"])
def test_directory_persistence_failure_before_ack_preserves_original_unknown_markers(
    tmp_path, monkeypatch, failure
):
    (
        m,
        config,
        run,
        proof,
        state,
        original,
        _,
    ) = worker_fixture(tmp_path, monkeypatch)
    raw = (state / ("reconciliation-" + run["run_id"] + ".json")).read_bytes()
    if failure in ("existing-ack-parent", "existing-archive-leaf"):
        archive = state / "reconciliation-archives" / run["run_id"]
        archive.mkdir(parents=True)
        (archive / "cleanup-uncertain.json").write_bytes(original)
    if failure == "existing-ack-parent":
        ack = {
            "version": 1,
            "run_id": run["run_id"],
            "host_incarnation": proof["host_incarnation"],
            "operation_id": str(uuid.uuid4()),
            "proof_sha256": hashlib.sha256(raw).hexdigest(),
            "validated": True,
            "markers_archived": True,
            "original_terminal_run_sha256": proof["original_terminal_run_sha256"],
            "completed_at_monotonic_s": time.monotonic(),
        }
        (state / ("reconciliation-ack-" + run["run_id"] + ".json")).write_bytes(canonical(ack))
    refused_directory = {
        "archive-parent": state / "reconciliation-archives",
        "existing-ack-parent": state,
        "existing-archive-leaf": state / "reconciliation-archives" / run["run_id"],
    }[failure]
    state_syncs = 0

    def persistence_failure(directory):
        nonlocal state_syncs
        if directory == state:
            state_syncs += 1
        if directory == refused_directory and (failure != "existing-ack-parent" or state_syncs == 2):
            raise OSError("mock directory persistence failure before uncertainty clear")

    monkeypatch.setattr(m, "_fsync_directory", persistence_failure)
    with pytest.raises(OSError, match="directory persistence failure"):
        m.archive_and_ack(
            config,
            proof,
            raw,
            {"cleanup-uncertain.json": original, "physical-in-progress.json": None},
            str(uuid.uuid4()),
        )
    assert (state / "cleanup-uncertain.json").read_bytes() == original
    if failure == "existing-archive-leaf":
        assert not (state / ("reconciliation-ack-" + run["run_id"] + ".json")).exists()


def test_already_durable_actual_ack_can_resume_historically_without_claiming_fresh_thermal(
    tmp_path, monkeypatch
):
    m, config, run, proof, state, original, raw = loader_fixture(tmp_path, monkeypatch)
    archive = state / "reconciliation-archives" / run["run_id"]
    archive.mkdir(parents=True)
    (archive / "cleanup-uncertain.json").write_bytes(original)
    (state / "cleanup-uncertain.json").unlink()
    ack = {
        "version": 1,
        "run_id": run["run_id"],
        "host_incarnation": proof["host_incarnation"],
        "proof_sha256": hashlib.sha256(raw).hexdigest(),
        "validated": True,
        "markers_archived": True,
        "original_terminal_run_sha256": proof["original_terminal_run_sha256"],
        "operation_id": str(uuid.uuid4()),
        "completed_at_monotonic_s": 100.2,
    }
    (state / ("reconciliation-ack-" + run["run_id"] + ".json")).write_bytes(canonical(ack))
    m.time.monotonic = lambda: 1000.0
    assert m.load_qualified_proof(config, run)[0]["completed_at_monotonic_s"] == 100.01
    assert (
        m.archive_and_ack(
            config,
            proof,
            raw,
            {"cleanup-uncertain.json": original, "physical-in-progress.json": None},
            str(uuid.uuid4()),
        )
        == ack
    )


def test_actual_calibration_comparison_must_cover_every_owned_motor_and_match_preserved_cache():
    from tools import am1_physical_reconciliation as m

    _, _, proof = stopped_proof()
    cached = {
        name: {"id": i + 1, "homing_offset": 17, "range_min": 20, "range_max": 4000}
        for i, (_, name, reg) in enumerate(sorted(m.expected_readbacks()))
        if reg == "Torque_Enable"
    }
    rows = []
    for side, name, reg in sorted(m.expected_readbacks()):
        if reg != "Torque_Enable":
            continue
        for register, wanted in {
            "ID": cached[name]["id"],
            "Model_Number": 777,
            "Homing_Offset": 17,
            "Min_Position_Limit": 20,
            "Max_Position_Limit": 4000,
        }.items():
            rows.append(
                {
                    "bus": side,
                    "motor": name,
                    "register": register,
                    "value": wanted,
                    "cached_expected": wanted,
                    "matches": True,
                    "read_started_at_monotonic_s": 99.2,
                    "read_completed_at_monotonic_s": 99.201,
                }
            )
    proof["readback"]["calibration_comparison"] = rows
    m.validate_calibration_evidence(proof, cached)
    rows[0]["cached_expected"] += 1
    with pytest.raises(ValueError):
        m.validate_calibration_evidence(proof, cached)
