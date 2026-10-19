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
        wire = encode(
            {
                "id": str(uuid.uuid4()),
                "result": {"accepted": True, "status": "snapshot", "snapshot": snapshot},
            },
            RESPONSE_LIMIT,
        )
        assert len(wire) <= RESPONSE_LIMIT
        assert "reconciliation" not in snapshot["run"]
        reference = snapshot["run"]["details_reference"]
        assert reference["kind"] == "am1-status-field-reference" and reference["field"] == "run"
        from tests.robots.test_am1_status_details import resolve

        detailed = resolve(authority, reference)
        assert detailed == original and detailed["reconciliation"] == proof
        assert detailed["reconciliation"]["kind"] == "am1-actual-stopped-reconciliation"
        assert detailed["reconciliation"]["readback"] == proof["readback"]
        assert detailed["reconciliation"]["source"] == proof["source"]
        assert authority.run == original and authority.run["reconciliation"] == proof
        assert authority.db.execute("SELECT data FROM state WHERE id=1").fetchone()[0] == durable
        detailed["reconciliation"]["acknowledgment"]["validated"] = False
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
        snapshot = authority.snapshot()
        if executor.source == "protected-physical-provider":
            from tests.robots.test_am1_status_details import resolve
            from tools.am1_session_core import _reconciliation_reference

            assert "reconciliation" not in snapshot["run"]
            detailed = resolve(authority, snapshot["run"]["details_reference"])
            assert detailed == before and detailed["reconciliation"] == proof
            assert "presentation_version" not in detailed["reconciliation"]
            assert _reconciliation_reference(before) == proof
        else:
            assert snapshot["run"]["reconciliation"] == proof
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
                "temperature_slots": [
                    [samples[n]["sample_monotonic_s"], 34] for n in (1, 3, 5, 7, 8)
                ],
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


def incomplete_preactivation_fixture(tmp_path, monkeypatch):
    m, config, run, proof, state, _, _ = loader_fixture(tmp_path, monkeypatch)
    run.update(native_live_at=None, progress_s=0.0, cleanup="physical_cleanup_unknown")
    run["native_first_cause"] = (
        "RuntimeError: AM1 arm activation refused: 'arm_left_shoulder_lift' raw position 3224 "
        "is outside verified calibration limits 848..3223."
    )
    proof["original_first_cause"] = run["native_first_cause"]
    run["physical_result"].update(
        phase="terminal",
        run_id=run["run_id"],
        native_live_at=None,
        cleanup="physical_cleanup_unknown",
        uncertain=True,
    )
    old = run["physical_result"]["readback"]
    old.update(
        cleanup="physical_readback_verified",
        uncertain=False,
        cleanup_errors=[],
        provenance="protected-host/actual-motor-readback",
        first_cause=run["native_first_cause"],
        first_error_chain=[
            {"cause": "RuntimeError: AlohaMini motor activation failed.", "notes": []},
            {"cause": run["native_first_cause"], "notes": []},
        ],
    )
    old["readback"] = {
        "verified": True,
        "buses_closed": True,
        "lift_readback": "unavailable-or-failed",
        "selected_gain_restore_required": False,
        "cleanup_errors": [],
        "started_at_monotonic_s": 99.0,
        "completed_at_monotonic_s": 99.5,
        "readbacks": copy.deepcopy(proof["readback"]["readbacks"]),
    }
    proof["original_cleanup_origin"] = "preactivation-lift-unqualified"
    proof["original_markers"] = dict.fromkeys(m.MARKERS)
    (state / "cleanup-uncertain.json").unlink()
    return m, config, run, proof, state


def publish_incomplete_fixture(config, run, proof, state):
    old = run["physical_result"]["readback"]
    original = canonical(old) + b"\n"
    (Path(config["run_root"]) / run["run_id"][:8] / "cleanup.json").write_bytes(original)
    proof["original_cleanup_sha256"] = hashlib.sha256(original).hexdigest()
    proof["original_terminal_run_sha256"] = hashlib.sha256(canonical(run)).hexdigest()
    raw = canonical(proof)
    (state / ("reconciliation-" + run["run_id"] + ".json")).write_bytes(raw)
    return raw


def test_incomplete_preactivation_lift_origin_still_requires_fresh_proof_and_durable_ack(
    tmp_path, monkeypatch
):
    m, config, run, proof, state = incomplete_preactivation_fixture(tmp_path, monkeypatch)
    raw = publish_incomplete_fixture(config, run, proof, state)
    original_run = copy.deepcopy(run)
    actual, received, inventory = m.load_qualified_proof(config, run)
    assert actual == proof and received == raw and inventory == dict.fromkeys(m.MARKERS)
    operation = str(uuid.uuid4())
    acknowledgment = m.archive_and_ack(config, actual, received, inventory, operation)
    receipt = dict(actual, acknowledgment=dict(acknowledgment, consumed_operation_id=operation))
    assert m.acknowledged_receipt(receipt, run, operation)
    assert run == original_run
    assert all(not (state / name).exists() for name in m.MARKERS)
    assert m.load_qualified_proof(config, run)[2] == inventory


@pytest.mark.parametrize(
    "broken",
    [
        "origin_missing",
        "origin_foreign",
        "live_started",
        "progress",
        "progress_boolean",
        "not_faulted",
        "native_live_started",
        "host_uncertain",
        "host_unknown",
        "lift_qualified",
        "buses_open",
        "unverified",
        "cleanup_error",
        "restore_pending",
        "nonzero",
        "boolean_zero",
        "missing_row",
        "duplicate_row",
        "late_row",
        "marker_inventory",
        "marker_appeared",
        "native_cause",
        "unrelated_cause",
        "error_chain_missing",
        "fresh_lift_missing",
        "fresh_thermal_stale",
    ],
)
def test_incomplete_lift_origin_cannot_promote_other_failures_or_weaken_stopped_proof(
    tmp_path, monkeypatch, broken
):
    m, config, run, proof, state = incomplete_preactivation_fixture(tmp_path, monkeypatch)
    old = run["physical_result"]["readback"]
    rb = old["readback"]
    if broken == "origin_missing":
        proof.pop("original_cleanup_origin")
    elif broken == "origin_foreign":
        proof["original_cleanup_origin"] = "ordinary-completion"
    elif broken == "live_started":
        run["native_live_at"] = 98.0
    elif broken == "native_live_started":
        run["physical_result"]["native_live_at"] = 98.0
    elif broken in {"progress", "progress_boolean"}:
        run["progress_s"] = 0.1 if broken == "progress" else False
    elif broken == "not_faulted":
        run["status"] = "stopped"
    elif broken == "host_uncertain":
        old["uncertain"] = True
    elif broken == "host_unknown":
        old["cleanup"] = "physical_cleanup_unknown"
    elif broken == "lift_qualified":
        rb["lift_readback"] = "qualified"
    elif broken == "buses_open":
        rb["buses_closed"] = False
    elif broken == "unverified":
        rb["verified"] = False
    elif broken == "cleanup_error":
        rb["cleanup_errors"] = ["actual failure"]
    elif broken == "restore_pending":
        rb["selected_gain_restore_required"] = True
    elif broken in {"nonzero", "boolean_zero"}:
        rb["readbacks"][0]["value"] = 1 if broken == "nonzero" else False
    elif broken == "missing_row":
        rb["readbacks"].pop()
    elif broken == "duplicate_row":
        rb["readbacks"][0] = copy.deepcopy(rb["readbacks"][1])
    elif broken == "late_row":
        rb["readbacks"][0]["read_completed_at_monotonic_s"] += 0.05
    elif broken == "marker_inventory":
        proof["original_markers"][m.MARKERS[0]] = {"sha256": "a" * 64}
    elif broken == "marker_appeared":
        (state / m.MARKERS[1]).write_bytes(b"foreign")
    elif broken == "native_cause":
        old["first_cause"] = "different native failure"
    elif broken == "unrelated_cause":
        cause = "RuntimeError: unrelated thermal-window failure"
        old["first_cause"] = run["native_first_cause"] = proof["original_first_cause"] = cause
        old["first_error_chain"][-1]["cause"] = cause
    elif broken == "error_chain_missing":
        old.pop("first_error_chain")
    elif broken == "fresh_lift_missing":
        proof["readback"].pop("lift_stationary")
    else:
        proof["readback"]["lift_stationary"]["temperature_slots"][0][0] = 99.0
    publish_incomplete_fixture(config, run, proof, state)
    with pytest.raises((ValueError, RuntimeError)):
        m.load_qualified_proof(config, run)


def test_incomplete_origin_marker_appearance_after_validation_refuses_ack(tmp_path, monkeypatch):
    m, config, run, proof, state = incomplete_preactivation_fixture(tmp_path, monkeypatch)
    publish_incomplete_fixture(config, run, proof, state)
    actual, raw, inventory = m.load_qualified_proof(config, run)
    (state / m.MARKERS[0]).write_bytes(b"foreign")
    with pytest.raises(ValueError):
        m.archive_and_ack(config, actual, raw, inventory, str(uuid.uuid4()))
    assert (state / m.MARKERS[0]).read_bytes() == b"foreign"
    assert not list(state.glob("reconciliation-ack-*"))


def test_incomplete_origin_marker_appearance_during_ack_cannot_return_qualified_receipt(
    tmp_path, monkeypatch
):
    m, config, run, proof, state = incomplete_preactivation_fixture(tmp_path, monkeypatch)
    publish_incomplete_fixture(config, run, proof, state)
    actual, raw, inventory = m.load_qualified_proof(config, run)
    original_write = m._write_new_or_same

    def changed_during_ack(path, data):
        original_write(path, data)
        if path.name.startswith("reconciliation-ack-"):
            (state / m.MARKERS[1]).write_bytes(b"foreign")

    monkeypatch.setattr(m, "_write_new_or_same", changed_during_ack)
    with pytest.raises(ValueError):
        m.archive_and_ack(config, actual, raw, inventory, str(uuid.uuid4()))
    assert (state / m.MARKERS[1]).read_bytes() == b"foreign"
    with pytest.raises(ValueError):
        m.load_qualified_proof(config, run)


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


# Exact Combined16 original note bytes; isolated loader sessions below rebind only IDs.
# Their later stopped-proof continuation is synthetic and does not qualify Combined16.
ACTUAL16_COOL_STALE_FAULT_NOTE = (
    '[AM1 TELEOP FAULT] {"reason":"actual configured temperature limit: arm_left_shoulder_pan; '
    'temperature: five-slot feedback window is stale (>0.5 s).","side":"left","register":"Present_Te'
    'mperature","raw":{"arm_left_shoulder_pan":32,"arm_left_shoulder_lift":31,"arm_left_elbow_flex":'
    '28,"arm_left_wrist_flex":30,"arm_left_wrist_roll":29,"arm_left_gripper":34},"read_started_at_mo'
    'notonic_s":155503.029919034,"read_completed_at_monotonic_s":155503.031678142,"binding":{"recipe'
    '":"physical-live-teleop","run_id":"94358dfa-4363-4658-9fd4-'
    'fe0dc82c5499","host_incarnation":"acf244a1-c2a4-482e-b648-'
    '558edc31d35e"},"offending_motor":"arm_left_shoulder_pan","offending_value":32,"comparison":{"un'
    'its":"raw_register","lower_inclusive":0,"upper_exclusive":70,"temperature":{"ceiling_c":70,"sam'
    'ples":[[155502.524492496,32],[155502.624574612,32],[155502.724499211,32],[155502.824513753,32],'
    '[155503.031678142,32]],"high_count":0,"numeric_outlier_count":0,"first_cause":"temperature: '
    'five-slot feedback window is stale (>0.5 s)."},"configured":{"Max_Temperature_Limit":{"value":7'
    '0,"read_started_at_monotonic_s":155502.410460883,"read_completed_at_monotonic_s":155502.4124485'
    '28}}},"prior_successful_health":null,"group_reply":{"binding":{"host_incarnation":"acf244a1-'
    'c2a4-482e-b648-558edc31d35e","recipe":"physical-live-teleop","run_id":"94358dfa-4363-4658-9fd4-'
    'fe0dc82c5499"},"request":{"binding":{"host_incarnation":"acf244a1-c2a4-482e-b648-'
    '558edc31d35e","recipe":"physical-live-teleop","run_id":"94358dfa-4363-4658-9fd4-'
    'fe0dc82c5499"},"side":"left","address":63,"width":1,"ids":[1,2,3,4,5,6],"motors":["arm_left_sho'
    'ulder_pan","arm_left_shoulder_lift","arm_left_elbow_flex","arm_left_wrist_flex","arm_left_wrist'
    '_roll","arm_left_gripper"],"register":"Present_Temperature"},"group_call_started_at_monotonic_s'
    '":155503.030073663,"group_call_completed_at_monotonic_s":155503.031652272,"comm_result":0,"pack'
    'ets":[{"packet":[255,255,1,3,0,32,219],"device_error":0,"read_started_at_monotonic_s":155503.03'
    '04097,"read_completed_at_monotonic_s":155503.031312384,"comm_result":0},{"packet":[255,255,2,3,'
    '0,31,219],"device_error":0,"read_started_at_monotonic_s":155503.031407458,"read_completed_at_mo'
    'notonic_s":155503.031425921,"comm_result":0},{"packet":[255,255,3,3,0,28,221],"device_error":0,'
    '"read_started_at_monotonic_s":155503.031464254,"read_completed_at_monotonic_s":155503.031478717'
    ',"comm_result":0},{"packet":[255,255,4,3,0,30,218],"device_error":0,"read_started_at_monotonic_'
    's":155503.031511772,"read_completed_at_monotonic_s":155503.031524772,"comm_result":0},{"packet"'
    ':[255,255,5,3,0,29,218],"device_error":0,"read_started_at_monotonic_s":155503.031555791,"read_c'
    'ompleted_at_monotonic_s":155503.031571031,"comm_result":0},{"packet":[255,255,6,3,0,34,212],"de'
    'vice_error":0,"read_started_at_monotonic_s":155503.031603679,"read_completed_at_monotonic_s":15'
    '5503.031619383,"comm_result":0}],"transmit":{"packet":[255,255,254,10,130,63,1,1,2,3,4,5,6,32],'
    '"write_started_at_monotonic_s":155503.030138719,"write_completed_at_monotonic_s":155503.0303932'
    '93,"result":14}}}'
)

ACTUAL16_BEFORE_HOME_NOTE = (
    'Original normal-rest acquisition: {"phase":"before_home","binding":{"recipe":"physical-live-tel'
    'eop","run_id":"94358dfa-4363-4658-9fd4-fe0dc82c5499","host_incarnation":"acf244a1-c2a4-482e-'
    "b648-"
    '558edc31d35e"},"calibration":[2,0,515,848,3223],"operating_displacement_bound_degrees":0.5,"ini'
    'tial_measured_raw":3223,"qualified":false,"samples":[],"settings":{},"started_at":155503.029860'
    '108,"first_cause":"RuntimeError: AM1 teleop actual configured temperature limit: '
    'arm_left_shoulder_pan; temperature: five-slot feedback window is stale (>0.5 s)."}'
)


def cool_stale_preactivation_fixture(tmp_path, monkeypatch):
    """Retain original packet/times; rebind the isolated session and use synthetic fresh proof."""
    m, config, run, proof, state = incomplete_preactivation_fixture(tmp_path, monkeypatch)
    fault = json.loads(ACTUAL16_COOL_STALE_FAULT_NOTE.removeprefix("[AM1 TELEOP FAULT] "))
    rest = json.loads(ACTUAL16_BEFORE_HOME_NOTE.removeprefix("Original normal-rest acquisition: "))
    binding = {
        "recipe": "physical-live-teleop",
        "run_id": run["run_id"],
        "host_incarnation": proof["host_incarnation"],
    }
    fault["binding"] = fault["group_reply"]["binding"] = fault["group_reply"]["request"]["binding"] = binding
    rest["binding"] = binding
    run["recipe"] = "physical-live-teleop"
    run["native_first_cause"] = rest["first_cause"]
    proof.update(
        original_cleanup_origin="prehome-cool-thermal-stale-lift-unqualified",
        original_first_cause=run["native_first_cause"],
    )
    old = run["physical_result"]["readback"]
    old["first_cause"] = run["native_first_cause"]
    old["first_error_chain"] = [
        {"cause": "RuntimeError: AlohaMini motor activation failed.", "notes": []},
        {
            "cause": run["native_first_cause"],
            "notes": [
                "[AM1 TELEOP FAULT] " + json.dumps(fault),
                "Original normal-rest acquisition: " + json.dumps(rest),
            ],
        },
    ]
    # Synthetic original stop register timing follows the retained fault note.
    rb = old["readback"]
    rb["started_at_monotonic_s"] = fault["read_completed_at_monotonic_s"] + 0.02
    for index, row in enumerate(rb["readbacks"]):
        row["read_started_at_monotonic_s"] = rb["started_at_monotonic_s"] + index * 0.001
        row["read_completed_at_monotonic_s"] = row["read_started_at_monotonic_s"] + 0.0005
    rb["completed_at_monotonic_s"] = rb["started_at_monotonic_s"] + 0.03
    return m, config, run, proof, state


def test_prehome_cool_stale_origin_requires_fresh_stopped_proof_and_separate_durable_ack(
    tmp_path, monkeypatch
):
    m, config, run, proof, state = cool_stale_preactivation_fixture(tmp_path, monkeypatch)
    raw = publish_incomplete_fixture(config, run, proof, state)
    before = copy.deepcopy(run)
    actual, received, inventory = m.load_qualified_proof(config, run)
    assert actual == proof and received == raw and inventory == dict.fromkeys(m.MARKERS)
    operation = str(uuid.uuid4())
    ack = m.archive_and_ack(config, actual, received, inventory, operation)
    assert m.acknowledged_receipt(
        {**actual, "acknowledgment": {**ack, "consumed_operation_id": operation}}, run, operation
    )
    assert run == before and run["uncertain"] is True and run["status"] == "faulted"
    assert all(not (state / name).exists() for name in m.MARKERS)


@pytest.mark.parametrize(
    "broken",
    [
        "old_origin",
        "live",
        "progress",
        "post_home",
        "rest_missing",
        "rest_duplicate",
        "rest_qualified",
        "rest_samples",
        "binding",
        "first_cause",
        "partial_chain",
        "fault_duplicate",
        "fault_malformed",
        "numeric_high",
        "slot_high",
        "slot_duplicate",
        "not_stale",
        "high_count",
        "outlier_count",
        "ceiling",
        "impossible_ceiling",
        "device_error",
        "packet_error",
        "packet_checksum",
        "packet_width",
        "packet_missing",
        "packet_raw",
        "group_error",
        "raw_boolean",
        "cleanup_error",
        "nonzero",
        "fresh_lift_missing",
        "fresh_thermal_stale",
    ],
)
def test_prehome_cool_stale_origin_cannot_admit_heat_device_fault_or_incomplete_proof(
    tmp_path, monkeypatch, broken
):
    m, config, run, proof, state = cool_stale_preactivation_fixture(tmp_path, monkeypatch)
    old = run["physical_result"]["readback"]
    notes = old["first_error_chain"][1]["notes"]
    fault = json.loads(notes[0].removeprefix("[AM1 TELEOP FAULT] "))
    rest = json.loads(notes[1].removeprefix("Original normal-rest acquisition: "))
    if broken == "old_origin":
        proof["original_cleanup_origin"] = "preactivation-lift-unqualified"
    elif broken == "live":
        run["native_live_at"] = 99.0
    elif broken == "progress":
        run["progress_s"] = 0.1
    elif broken == "post_home":
        rest["phase"] = "after_home"
    elif broken == "rest_qualified":
        rest["qualified"] = True
    elif broken == "rest_samples":
        rest["samples"] = [{"position": 3223}]
    elif broken == "binding":
        fault["binding"]["run_id"] = str(uuid.uuid4())
    elif broken == "first_cause":
        rest["first_cause"] = "RuntimeError: genuine motor error"
    elif broken == "numeric_high":
        fault["offending_value"] = fault["raw"]["arm_left_shoulder_pan"] = 90
    elif broken == "slot_high":
        fault["comparison"]["temperature"]["samples"][0][1] = 90
    elif broken == "slot_duplicate":
        fault["comparison"]["temperature"]["samples"][1][0] = fault["comparison"]["temperature"]["samples"][
            0
        ][0]
    elif broken == "not_stale":
        fault["comparison"]["temperature"]["samples"][0][0] += 0.02
    elif broken in {"high_count", "outlier_count"}:
        fault["comparison"]["temperature"][
            "high_count" if broken == "high_count" else "numeric_outlier_count"
        ] = 1
    elif broken == "ceiling":
        fault["comparison"]["temperature"]["ceiling_c"] = 90
    elif broken == "impossible_ceiling":
        fault["comparison"]["temperature"]["ceiling_c"] = 150
        fault["comparison"]["configured"]["Max_Temperature_Limit"]["value"] = 150
        fault["comparison"]["upper_exclusive"] = 150
    elif broken == "device_error":
        fault["group_reply"]["packets"][0]["device_error"] = 4
    elif broken == "packet_error":
        fault["group_reply"]["packets"][0]["packet"][4] = 4
    elif broken == "packet_checksum":
        fault["group_reply"]["packets"][0]["packet"][-1] ^= 1
    elif broken == "packet_width":
        fault["group_reply"]["request"]["width"] = 38
    elif broken == "packet_missing":
        fault["group_reply"]["packets"].pop()
    elif broken == "packet_raw":
        fault["raw"]["arm_left_shoulder_pan"] = 33
    elif broken == "group_error":
        fault["group_reply"]["comm_result"] = -1
    elif broken == "raw_boolean":
        fault["offending_value"] = False
    elif broken == "cleanup_error":
        old["cleanup_errors"] = ["failed"]
    elif broken == "nonzero":
        old["readback"]["readbacks"][0]["value"] = 1
    elif broken == "fresh_lift_missing":
        proof["readback"].pop("lift_stationary")
    elif broken == "fresh_thermal_stale":
        proof["readback"]["lift_stationary"]["temperature_slots"][0][0] = 90.0
    notes[:] = [
        "[AM1 TELEOP FAULT] " + json.dumps(fault),
        "Original normal-rest acquisition: " + json.dumps(rest),
    ]
    if broken == "rest_missing":
        notes.pop()
    elif broken == "rest_duplicate":
        notes.append(notes[1])
    elif broken == "fault_duplicate":
        notes.append(notes[0])
    elif broken == "fault_malformed":
        notes[0] = "[AM1 TELEOP FAULT] {bad"
    elif broken == "partial_chain":
        old["first_error_chain"].pop(0)
    publish_incomplete_fixture(config, run, proof, state)
    with pytest.raises((ValueError, RuntimeError)):
        m.load_qualified_proof(config, run)


# Synthetic fresh readback proof with the retained Combined18 import-trace shape.
# These fixtures are offline contrasts and grant no physical cleanup credit.
def preimport_stop_fixture(tmp_path, monkeypatch):
    m, config, run, proof, state, _, _ = loader_fixture(tmp_path, monkeypatch)
    run.update(
        status="stopped",
        requested_terminal="stopped",
        cleanup="physical_cleanup_unknown",
        source="protected-physical-provider",
        recipe="physical-live-teleop",
        native_live_at=None,
        progress_s=0.0,
        recovery_episodes=0,
        first_cause=None,
        native_first_cause=None,
        execution={"complete": False, "fault": None, "progress_s": 0.0},
    )
    run["physical_result"].update(
        phase="terminal",
        terminal_status="stopped",
        run_id=run["run_id"],
        uncertain=True,
        cleanup="physical_cleanup_unknown",
        native_live_at=None,
        first_cause=None,
        readback=None,
        cleanup_errors=[],
    )
    proof.update(
        original_cleanup_origin="preimport-stop-missing-host-receipt",
        original_first_cause=None,
        original_cleanup_sha256=None,
        original_cleanup_missing=True,
        original_markers=dict.fromkeys(m.MARKERS),
    )
    directory = Path(config["run_root"]) / run["run_id"][:8]
    (directory / "cleanup.json").unlink()
    (state / "cleanup-uncertain.json").unlink()
    repository = Path(__file__).resolve().parents[2]
    config["host_repository"] = str(repository)
    proof["source"]["backend_contract"] = copy.deepcopy(config)
    prefix = (repository / "src/lerobot/robots/alohamini").as_posix()
    stdlib = "/retained/python/lib/python3.12/dataclasses.py"
    trace = (
        "Traceback (most recent call last):\n"
        '  File "<frozen runpy>", line 189, in _run_module_as_main\n'
        '  File "<frozen runpy>", line 112, in _get_module_details\n'
        f'  File "{prefix}/__init__.py", line 17, in <module>\n'
        "    from .alohamini import AlohaMini\n"
        f'  File "{prefix}/alohamini.py", line 42, in <module>\n'
        "    from .lift_axis import LiftAxis, LiftAxisConfig, LiftHomeResult\n"
        f'  File "{prefix}/lift_axis.py", line 59, in <module>\n'
        "    @dataclass\n     ^^^^^^^^^\n"
        f'  File "{stdlib}", line 1275, in dataclass\n'
        "    return wrap(cls)\n           ^^^^^^^^^\n"
        f'  File "{stdlib}", line 1265, in wrap\n'
        "    return _process_class(cls, init, repr, eq, order, unsafe_hash,\n"
        "           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n"
        f'  File "{stdlib}", line 1092, in _process_class\n'
        "    _cmp_fn('__eq__', '==',\n"
        f'  File "{stdlib}", line 667, in _cmp_fn\n'
        "    return _create_fn(name,\n           ^^^^^^^^^^^^^^^^\n"
        f'  File "{stdlib}", line 473, in _create_fn\n'
        "    exec(txt, globals, ns)\n"
        '  File "<string>", line 0, in <module>\nKeyboardInterrupt\n'
    ).encode()
    launch = (
        f"Running as unit: am1-pa01-host-{run['run_id']}.service; invocation ID: "
        "3948f26868774d6dbecd7112518e0ccf\nFinished with result: success\n"
        "Main processes terminated with: code=killed, status=2/INT\n"
        "Service runtime: 4.676s\nCPU time consumed: 4.830s\n"
    ).encode()
    for name, data in (("host-runtime.log", trace), ("host-launch.log", launch)):
        (directory / name).write_bytes(data)
    proof["original_host_logs"] = {
        name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
        for name in ("host-runtime.log", "host-launch.log")
    }
    publish_preimport_fixture(run, proof, state)
    return m, config, run, proof, state, directory


def publish_preimport_fixture(run, proof, state):
    proof["original_terminal_run_sha256"] = hashlib.sha256(canonical(run)).hexdigest()
    raw = canonical(proof)
    (state / ("reconciliation-" + run["run_id"] + ".json")).write_bytes(raw)
    return raw


def test_preimport_stop_missing_receipt_requires_fresh_proof_and_durable_ack(tmp_path, monkeypatch):
    m, config, run, proof, state, directory = preimport_stop_fixture(tmp_path, monkeypatch)
    original = copy.deepcopy(run)
    actual, raw, inventory = m.load_qualified_proof(config, run)
    assert actual == proof and inventory == dict.fromkeys(m.MARKERS)
    operation = str(uuid.uuid4())
    ack = m.archive_and_ack(config, actual, raw, inventory, operation)
    receipt = dict(actual, acknowledgment=dict(ack, consumed_operation_id=operation))
    assert m.acknowledged_receipt(receipt, run, operation)
    assert run == original and run["uncertain"] is True and run["physical_result"]["readback"] is None
    assert not (directory / "cleanup.json").exists()
    assert all(not (state / name).exists() for name in m.MARKERS)
    assert m.load_qualified_proof(config, run)[0] == proof


@pytest.mark.parametrize(
    "broken",
    [
        "origin",
        "missing_declaration",
        "missing_hash_key",
        "fake_receipt_hash",
        "status",
        "requested",
        "live",
        "native_live",
        "progress",
        "bool_progress",
        "execution_progress",
        "execution_fault",
        "complete",
        "recovery",
        "cause",
        "native_cause",
        "host_cause",
        "readback",
        "cleanup_error",
        "terminal_status",
        "marker_inventory",
        "marker_appeared",
        "cleanup_appeared",
        "trace_hash",
        "launch_hash",
        "trace_noise",
        "trace_other_import",
        "trace_post_main",
        "trace_device_error",
        "launch_other_run",
        "launch_other_signal",
        "launch_extra",
        "source_line",
        "fresh_torque",
        "fresh_lift",
        "fresh_thermal",
    ],
)
def test_preimport_stop_origin_refuses_other_shapes_and_unqualified_fresh_proof(
    tmp_path, monkeypatch, broken
):
    m, config, run, proof, state, directory = preimport_stop_fixture(tmp_path, monkeypatch)
    lifecycle = run["physical_result"]
    if broken == "origin":
        proof["original_cleanup_origin"] = "ordinary-cancel"
    elif broken == "missing_declaration":
        proof.pop("original_cleanup_missing")
    elif broken == "missing_hash_key":
        proof.pop("original_cleanup_sha256")
    elif broken == "fake_receipt_hash":
        proof["original_cleanup_sha256"] = "a" * 64
    elif broken == "status":
        run["status"] = "faulted"
    elif broken == "requested":
        run["requested_terminal"] = "completed"
    elif broken == "live":
        run["native_live_at"] = 98.0
    elif broken == "native_live":
        lifecycle["native_live_at"] = 98.0
    elif broken in ("progress", "bool_progress"):
        run["progress_s"] = 0.1 if broken == "progress" else False
    elif broken == "execution_progress":
        run["execution"]["progress_s"] = 0.1
    elif broken == "execution_fault":
        run["execution"]["fault"] = "actual failure"
    elif broken == "complete":
        run["execution"]["complete"] = True
    elif broken == "recovery":
        run["recovery_episodes"] = 1
    elif broken == "cause":
        run["first_cause"] = "owner failure"
    elif broken == "native_cause":
        run["native_first_cause"] = proof["original_first_cause"] = "actual failure"
    elif broken == "host_cause":
        lifecycle["first_cause"] = "actual failure"
    elif broken == "readback":
        lifecycle["readback"] = {}
    elif broken == "cleanup_error":
        lifecycle["cleanup_errors"] = ["actual failure"]
    elif broken == "terminal_status":
        lifecycle["terminal_status"] = "faulted"
    elif broken == "marker_inventory":
        proof["original_markers"][m.MARKERS[0]] = {"sha256": "a" * 64}
    elif broken == "marker_appeared":
        (state / m.MARKERS[1]).write_bytes(b"foreign")
    elif broken == "cleanup_appeared":
        (directory / "cleanup.json").write_bytes(b"null")
    elif broken in ("trace_hash", "launch_hash"):
        proof["original_host_logs"]["host-runtime.log" if broken == "trace_hash" else "host-launch.log"] = (
            "a" * 64
        )
    elif broken.startswith("trace_"):
        path = directory / "host-runtime.log"
        data = path.read_bytes()
        if broken == "trace_noise":
            data = b"operational_ready\n" + data
        elif broken == "trace_other_import":
            data = data.replace(b"from .lift_axis import", b"from .lift_operational import")
        elif broken == "trace_post_main":
            data = data.replace(b"in _get_module_details", b"in main")
        else:
            data = data.replace(b"KeyboardInterrupt", b"RuntimeError: device error")
        path.write_bytes(data)
        proof["original_host_logs"][path.name] = hashlib.sha256(data).hexdigest()
    elif broken.startswith("launch_"):
        path = directory / "host-launch.log"
        data = path.read_bytes()
        if broken == "launch_other_run":
            data = data.replace(run["run_id"].encode(), str(uuid.uuid4()).encode())
        elif broken == "launch_other_signal":
            data = data.replace(b"2/INT", b"9/KILL")
        else:
            data += b"unexpected second launch\n"
        path.write_bytes(data)
        proof["original_host_logs"][path.name] = hashlib.sha256(data).hexdigest()
    elif broken == "source_line":
        source = tmp_path / "changed-source"
        source.mkdir()
        config["host_repository"] = str(source)
        proof["source"]["backend_contract"] = copy.deepcopy(config)
    elif broken == "fresh_torque":
        proof["readback"]["readbacks"][0]["value"] = 1
    elif broken == "fresh_lift":
        proof["readback"].pop("lift_stationary")
    else:
        proof["readback"]["lift_stationary"]["temperature_slots"][0][0] = 99.0
    publish_preimport_fixture(run, proof, state)
    with pytest.raises((ValueError, OSError, RuntimeError, TypeError)):
        m.load_qualified_proof(config, run)


@pytest.mark.parametrize("change", ["cleanup", "runtime", "launch"])
def test_preimport_origin_original_appearance_or_change_before_ack_refuses(tmp_path, monkeypatch, change):
    m, config, run, proof, state, directory = preimport_stop_fixture(tmp_path, monkeypatch)
    actual, raw, inventory = m.load_qualified_proof(config, run)
    path = (
        directory
        / {"cleanup": "cleanup.json", "runtime": "host-runtime.log", "launch": "host-launch.log"}[change]
    )
    path.write_bytes(b"changed")
    with pytest.raises((ValueError, OSError)):
        m.archive_and_ack(config, actual, raw, inventory, str(uuid.uuid4()))
    assert not list(state.glob("reconciliation-ack-*"))


def test_preimport_origin_cleanup_appearance_during_ack_never_returns_qualified(tmp_path, monkeypatch):
    m, config, run, proof, state, directory = preimport_stop_fixture(tmp_path, monkeypatch)
    actual, raw, inventory = m.load_qualified_proof(config, run)
    original = m._write_new_or_same

    def appearance(path, data):
        original(path, data)
        if path.name.startswith("reconciliation-ack-"):
            (directory / "cleanup.json").write_bytes(b"unexpected")

    monkeypatch.setattr(m, "_write_new_or_same", appearance)
    with pytest.raises(ValueError):
        m.archive_and_ack(config, actual, raw, inventory, str(uuid.uuid4()))
    with pytest.raises(ValueError):
        m.load_qualified_proof(config, run)


# Retained Combined66 package-discovery cancellation, with paths rebound only.
# Offline origin evidence never replaces the separately qualified fresh readback.
PROCESSOR_PREIMPORT_TRACE = (
    ('<frozen runpy>', 189, '_run_module_as_main', ()),
    ('<frozen runpy>', 112, '_get_module_details', ()),
    ('repo/lerobot/robots/alohamini/__init__.py', 17, '<module>', ('    from .alohamini import AlohaMini',)),
    ('repo/lerobot/robots/alohamini/alohamini.py', 34, '<module>', ('    from lerobot.processor import RobotAction, RobotObservation',)),
    ('repo/lerobot/processor/__init__.py', 26, '<module>', ('    from .batch_processor import AddBatchDimensionProcessorStep',)),
    ('repo/lerobot/processor/batch_processor.py', 27, '<module>', ('    from lerobot.configs import PipelineFeatureType, PolicyFeature',)),
    ('repo/lerobot/configs/__init__.py', 25, '<module>', ('    from .default import DatasetConfig, EvalConfig, JobConfig, PeftConfig, WandBConfig',)),
    ('repo/lerobot/configs/default.py', 19, '<module>', ('    from lerobot.transforms import ImageTransformsConfig',)),
    ('repo/lerobot/transforms/__init__.py', 15, '<module>', ('    from .transforms import (',)),
    ('repo/lerobot/transforms/transforms.py', 22, '<module>', ('    from torchvision.transforms import v2',)),
    ('venv/lib/python3.12/site-packages/torchvision/__init__.py', 8, '<module>', ('    from torchvision import _meta_registrations, datasets, io, models, ops, transforms, utils  # usort:skip', '    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^')),
    ('venv/lib/python3.12/site-packages/torchvision/models/__init__.py', 2, '<module>', ('    from .convnext import *',)),
    ('venv/lib/python3.12/site-packages/torchvision/models/convnext.py', 9, '<module>', ('    from ..ops.misc import Conv2dNormActivation, Permute',)),
    ('venv/lib/python3.12/site-packages/torchvision/ops/__init__.py', 23, '<module>', ('    from .poolers import MultiScaleRoIAlign',)),
    ('venv/lib/python3.12/site-packages/torchvision/ops/poolers.py', 10, '<module>', ('    from .roi_align import roi_align',)),
    ('venv/lib/python3.12/site-packages/torchvision/ops/roi_align.py', 7, '<module>', ('    from torch._dynamo.utils import is_compile_supported',)),
    ('venv/lib/python3.12/site-packages/torch/_dynamo/__init__.py', 13, '<module>', ('    from . import (',)),
    ('venv/lib/python3.12/site-packages/torch/_dynamo/aot_compile.py', 15, '<module>', ('    from torch._dynamo.convert_frame import GraphRuntimeEnv',)),
    ('venv/lib/python3.12/site-packages/torch/_dynamo/convert_frame.py', 62, '<module>', ('    from torch._dynamo.symbolic_convert import TensorifyState',)),
    ('venv/lib/python3.12/site-packages/torch/_dynamo/symbolic_convert.py', 54, '<module>', ('    from torch._dynamo.exc import ObservedException, TensorifyScalarRestartAnalysis',)),
    ('venv/lib/python3.12/site-packages/torch/_dynamo/exc.py', 44, '<module>', ('    from .utils import counters',)),
    ('venv/lib/python3.12/site-packages/torch/_dynamo/utils.py', 70, '<module>', ('    import torch.fx.experimental.symbolic_shapes',)),
    ('venv/lib/python3.12/site-packages/torch/fx/experimental/symbolic_shapes.py', 3, '<module>', ('    import sympy',)),
    ('venv/lib/python3.12/site-packages/sympy/__init__.py', 31, '<module>', ('    from sympy.core.cache import lazy_function',)),
    ('venv/lib/python3.12/site-packages/sympy/core/__init__.py', 9, '<module>', ('    from .expr import Expr, AtomicExpr, UnevaluatedExpr',)),
    ('venv/lib/python3.12/site-packages/sympy/core/expr.py', 4188, '<module>', ('    from .mul import Mul',)),
    ('venv/lib/python3.12/site-packages/sympy/core/mul.py', 2212, '<module>', ('    from .numbers import Rational',)),
    ('venv/lib/python3.12/site-packages/sympy/core/numbers.py', 4467, '<module>', ('    from .power import Pow',)),
    ('venv/lib/python3.12/site-packages/sympy/core/power.py', 14, '<module>', ('    from .relational import is_gt, is_lt',)),
    ('venv/lib/python3.12/site-packages/sympy/core/relational.py', 909, '<module>', ('    class GreaterThan(_Greater):',)),
    ('venv/lib/python3.12/site-packages/sympy/core/basic.py', 218, '__init_subclass__', ('    _prepare_class_assumptions(cls)',)),
    ('venv/lib/python3.12/site-packages/sympy/core/assumptions.py', 624, '_prepare_class_assumptions', ("    v = cls.__dict__.get(attrname, '')", '        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^')),
)


def processor_preimport_fixture(tmp_path, monkeypatch):
    m, config, run, proof, state, directory = preimport_stop_fixture(tmp_path, monkeypatch)
    repository = Path(config["host_repository"])
    environment = tmp_path / "venv"
    config["host_python"] = str(environment / "bin/python")
    proof["source"]["backend_contract"] = copy.deepcopy(config)
    lines = ["Traceback (most recent call last):"]
    for name, number, context, body in PROCESSOR_PREIMPORT_TRACE:
        if name.startswith("repo/"):
            path = repository / "src" / name.removeprefix("repo/")
            name = path.as_posix()
        elif name.startswith("venv/"):
            path = environment / name.removeprefix("venv/")
            path.parent.mkdir(parents=True, exist_ok=True)
            source = ["# retained dependency source"] * number
            source[number - 1] = body[0].strip()
            path.write_text("\n".join(source) + "\n", encoding="utf-8")
            name = path.as_posix()
        lines.append(f'  File "{name}", line {number}, in {context}')
        lines.extend(body)
    data = ("\n".join(lines) + "\nKeyboardInterrupt\n").encode()
    (directory / "host-runtime.log").write_bytes(data)
    proof["original_host_logs"]["host-runtime.log"] = hashlib.sha256(data).hexdigest()
    publish_preimport_fixture(run, proof, state)
    return m, config, run, proof, state, directory


def test_preimport_processor_discovery_accepts_retained_trace_only_with_fresh_proof(tmp_path, monkeypatch):
    m, config, run, proof, state, directory = processor_preimport_fixture(tmp_path, monkeypatch)
    original = copy.deepcopy(run)
    logs = {name: (directory / name).read_bytes() for name in proof["original_host_logs"]}
    actual, raw, inventory = m.load_qualified_proof(config, run)
    assert actual == proof and inventory == dict.fromkeys(m.MARKERS)
    operation = str(uuid.uuid4())
    ack = m.archive_and_ack(config, actual, raw, inventory, operation)
    receipt = dict(actual, acknowledgment=dict(ack, consumed_operation_id=operation))
    assert m.acknowledged_receipt(receipt, run, operation)
    assert m.load_qualified_proof(config, run)[0] == proof
    assert run == original and run["uncertain"] is True and run["native_first_cause"] is None
    assert run["physical_result"]["readback"] is None and not (directory / "cleanup.json").exists()
    assert {name: (directory / name).read_bytes() for name in logs} == logs
    assert all(not (state / name).exists() for name in m.MARKERS)


@pytest.mark.parametrize(
    "broken",
    [
        "discovery", "package_source", "local_function", "host_main", "physical_module",
        "foreign_dependency", "dependency_source", "caret", "trace_bound", "frame_bound",
        "source_bound", "source_escape", "fresh_torque", "fresh_lift", "fresh_thermal",
    ],
)
def test_preimport_processor_discovery_refuses_execution_and_unqualified_proof(tmp_path, monkeypatch, broken):
    m, config, run, proof, state, directory = processor_preimport_fixture(tmp_path, monkeypatch)
    path = directory / "host-runtime.log"
    data = path.read_bytes()
    dependency = Path(config["host_python"]).parent.parent / "lib/python3.12/site-packages/sympy/core/assumptions.py"
    if broken == "discovery":
        data = data.replace(b"in _get_module_details", b"in _run_code")
    elif broken == "package_source":
        data = data.replace(b"from lerobot.processor import RobotAction, RobotObservation", b"from lerobot.motors import Motor")
    elif broken == "local_function":
        data = data.replace(b'line 26, in <module>', b'line 26, in __init__', 1)
    elif broken in ("host_main", "physical_module"):
        source = Path(config["host_repository"]) / "src/lerobot" / (
            "robots/alohamini/alohamini_host.py" if broken == "host_main" else "motors/motors_bus.py"
        )
        body = source.read_text(encoding="utf-8").splitlines()[0].strip()
        extra = f'  File "{source.as_posix()}", line 1, in <module>\n    {body}\n'.encode()
        data = data.replace(b"KeyboardInterrupt", extra + b"KeyboardInterrupt")
    elif broken == "foreign_dependency":
        foreign = tmp_path / "foreign.py"
        foreign.write_bytes(dependency.read_bytes())
        data = data.replace(dependency.as_posix().encode(), foreign.as_posix().encode())
    elif broken == "dependency_source":
        dependency.write_text("# source changed\n", encoding="utf-8")
    elif broken == "caret":
        data = data.replace(b'        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^', b'    unexpected execution text')
    elif broken == "trace_bound":
        data = data.replace(b"KeyboardInterrupt", b" " * 65536 + b"KeyboardInterrupt")
    elif broken == "frame_bound":
        frame = f'  File "{dependency.as_posix()}", line 624, in _prepare_class_assumptions\n    v = cls.__dict__.get(attrname, '')\n'.encode()
        data = data.replace(b"KeyboardInterrupt", frame * 65 + b"KeyboardInterrupt")
    elif broken == "source_bound":
        dependency.write_bytes(b"#" * (2 * 1024 * 1024 + 1))
    elif broken == "source_escape":
        data = data.replace(dependency.as_posix().encode(), (dependency.parent / ".." / "core" / dependency.name).as_posix().encode())
    elif broken == "fresh_torque":
        proof["readback"]["readbacks"][0]["value"] = 1
    elif broken == "fresh_lift":
        proof["readback"].pop("lift_stationary")
    else:
        proof["readback"]["lift_stationary"]["temperature_slots"][0][0] = 99.0
    path.write_bytes(data)
    proof["original_host_logs"][path.name] = hashlib.sha256(data).hexdigest()
    publish_preimport_fixture(run, proof, state)
    with pytest.raises((ValueError, OSError, RuntimeError, TypeError)):
        m.load_qualified_proof(config, run)
    assert not list(state.glob("reconciliation-ack-*"))
    assert not (directory / "cleanup.json").exists()


@pytest.mark.parametrize(
    "later_temperature, retimestamp_peak, accepted",
    [(34, True, True), (33, False, True), (33, True, False)],
)
def test_stopped_thermal_replay_retains_actual_latest_equal_peak_without_refreshing_lower(
    later_temperature, retimestamp_peak, accepted
):
    from tools import am1_physical_reconciliation as m

    config, run, proof = stopped_proof()
    lift = proof["readback"]["lift_stationary"]
    samples = lift["samples"]
    # The later receipt in each other slot is lower; their real earlier maxima
    # remain. Only the first slot varies between an equal and a lower receipt.
    for index in (1, 3, 5, 7):
        sample = samples[index]
        sample["temperature_c"] = later_temperature if index == 1 else 33
        packet = sample["group_trace"]["response_bytes"]
        packet[5 + 30] = sample["temperature_c"]
        packet[-1] = (~sum(packet[2:-1])) & 255
    indices = (1 if retimestamp_peak else 0, 2, 4, 6, 8)
    lift["temperature_slots"] = [[samples[index]["sample_monotonic_s"], 34] for index in indices]
    original = copy.deepcopy(proof)
    if accepted:
        assert m.validate_stopped_proof(config, run, proof, now=100.1, boot_id=proof["boot_id"]) == proof
    else:
        with pytest.raises(ValueError, match="thermal peak slot provenance/freshness"):
            m.validate_stopped_proof(config, run, proof, now=100.1, boot_id=proof["boot_id"])
    assert proof == original
