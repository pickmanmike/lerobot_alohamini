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


# Exact Actual83 bytes; only filesystem roots are relocated in the offline fixture.
ENUM_PREIMPORT_TRACE = """Traceback (most recent call last):
  File "/home/pickmanmike/.local/share/uv/python/cpython-3.12.13-linux-aarch64-gnu/lib/python3.12/enum.py", line 302, in __set_name__
    enum_member = enum_class._value2member_map_[value]
                  ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^
TypeError: unhashable type: 'Weights'

During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File "/home/pickmanmike/.local/share/uv/python/cpython-3.12.13-linux-aarch64-gnu/lib/python3.12/enum.py", line 309, in __set_name__
    raise KeyError
KeyError

During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File "<frozen runpy>", line 189, in _run_module_as_main
  File "<frozen runpy>", line 112, in _get_module_details
  File "/home/pickmanmike/lerobot_alohamini/src/lerobot/robots/alohamini/__init__.py", line 17, in <module>
    from .alohamini import AlohaMini
  File "/home/pickmanmike/lerobot_alohamini/src/lerobot/robots/alohamini/alohamini.py", line 34, in <module>
    from lerobot.processor import RobotAction, RobotObservation
  File "/home/pickmanmike/lerobot_alohamini/src/lerobot/processor/__init__.py", line 26, in <module>
    from .batch_processor import AddBatchDimensionProcessorStep
  File "/home/pickmanmike/lerobot_alohamini/src/lerobot/processor/batch_processor.py", line 27, in <module>
    from lerobot.configs import PipelineFeatureType, PolicyFeature
  File "/home/pickmanmike/lerobot_alohamini/src/lerobot/configs/__init__.py", line 25, in <module>
    from .default import DatasetConfig, EvalConfig, JobConfig, PeftConfig, WandBConfig
  File "/home/pickmanmike/lerobot_alohamini/src/lerobot/configs/default.py", line 19, in <module>
    from lerobot.transforms import ImageTransformsConfig
  File "/home/pickmanmike/lerobot_alohamini/src/lerobot/transforms/__init__.py", line 15, in <module>
    from .transforms import (
  File "/home/pickmanmike/lerobot_alohamini/src/lerobot/transforms/transforms.py", line 22, in <module>
    from torchvision.transforms import v2
  File "/home/pickmanmike/lerobot_alohamini/.venv/lib/python3.12/site-packages/torchvision/__init__.py", line 8, in <module>
    from torchvision import _meta_registrations, datasets, io, models, ops, transforms, utils  # usort:skip
    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/pickmanmike/lerobot_alohamini/.venv/lib/python3.12/site-packages/torchvision/models/__init__.py", line 15, in <module>
    from .swin_transformer import *
  File "/home/pickmanmike/lerobot_alohamini/.venv/lib/python3.12/site-packages/torchvision/models/swin_transformer.py", line 752, in <module>
    class Swin_V2_S_Weights(WeightsEnum):
  File "/home/pickmanmike/.local/share/uv/python/cpython-3.12.13-linux-aarch64-gnu/lib/python3.12/enum.py", line 595, in __new__
    enum_class = super().__new__(metacls, cls, bases, classdict, **kwds)
                 ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/pickmanmike/.local/share/uv/python/cpython-3.12.13-linux-aarch64-gnu/lib/python3.12/enum.py", line 315, in __set_name__
    or not issubclass(enum_class, Flag)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
KeyboardInterrupt
Error calling __set_name__ on '_proto_member' instance 'IMAGENET1K_V1' in 'Swin_V2_S_Weights'
"""


def enum_preimport_fixture(tmp_path, monkeypatch):
    import re

    assert hashlib.sha256(ENUM_PREIMPORT_TRACE.encode()).hexdigest() == (
        "aa234b55b63b9cb38b75940989bb5045b8e95f831ab06fdf6cff166405350a50")
    m, config, run, proof, state, directory = preimport_stop_fixture(tmp_path, monkeypatch)
    environment = tmp_path / "venv"
    python = environment / "bin/python"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"# fixture interpreter identity; never executed\n")
    config["host_python"] = str(python)
    proof["source"]["backend_contract"] = copy.deepcopy(config)
    stdlib = environment / "lib/python3.12/enum.py"
    data = ENUM_PREIMPORT_TRACE.replace(
        "/home/pickmanmike/lerobot_alohamini/.venv", environment.as_posix()).replace(
        "/home/pickmanmike/lerobot_alohamini", Path(config["host_repository"]).as_posix()).replace(
        "/home/pickmanmike/.local/share/uv/python/cpython-3.12.13-linux-aarch64-gnu/lib/python3.12/enum.py",
        stdlib.as_posix())
    sources = {}
    lines = data.splitlines()
    for index, line in enumerate(lines):
        frame = re.fullmatch(r'  File "([^"]+)", line ([0-9]+), in (.+)', line)
        if frame is None or not frame[1].startswith(environment.as_posix() + "/"):
            continue
        path, number = Path(frame[1]), int(frame[2])
        source = sources.setdefault(path, {})
        source[number] = lines[index + 1].strip()
    for path, source in sources.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        content = ["# retained import source"] * max(source)
        for number, line in source.items():
            content[number - 1] = line
        path.write_text("\n".join(content) + "\n", encoding="utf-8")
    (directory / "host-runtime.log").write_bytes(data.encode())
    proof["original_host_logs"]["host-runtime.log"] = hashlib.sha256(data.encode()).hexdigest()
    publish_preimport_fixture(run, proof, state)
    return m, config, run, proof, state, directory


def test_preimport_enum_interruption_accepts_exact_trace_only_with_fresh_proof_and_ack(tmp_path, monkeypatch):
    m, config, run, proof, state, directory = enum_preimport_fixture(tmp_path, monkeypatch)
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


@pytest.mark.parametrize("broken", [
    "footer", "prefix_type", "prefix_keyerror", "prefix_frame", "class_source", "terminal_context",
    "stdlib_path", "stdlib_source", "prefix_source", "host_execution", "missing_chain", "extra_footer",
    "trace_hash", "native_live", "fresh_torque", "fresh_lift", "fresh_thermal",
])
def test_preimport_enum_interruption_refuses_other_chains_execution_and_unqualified_proof(tmp_path, monkeypatch, broken):
    m, config, run, proof, state, directory = enum_preimport_fixture(tmp_path, monkeypatch)
    path = directory / "host-runtime.log"
    data = path.read_bytes()
    stdlib = Path(config["host_python"]).parent.parent / "lib/python3.12/enum.py"
    if broken == "footer":
        data = data.replace(b"instance 'IMAGENET1K_V1'", b"instance 'OTHER'")
    elif broken == "prefix_type":
        data = data.replace(b"TypeError: unhashable type: 'Weights'", b"RuntimeError: actual motor error")
    elif broken == "prefix_keyerror":
        data = data.replace(b"\nKeyError\n", b"\nValueError\n", 1)
    elif broken == "prefix_frame":
        data = data.replace(b"line 302, in __set_name__", b"line 302, in __call__", 1)
    elif broken == "class_source":
        data = data.replace(b"class Swin_V2_S_Weights(WeightsEnum):", b"class OtherWeights(WeightsEnum):")
    elif broken == "terminal_context":
        data = data.replace(b"line 315, in __set_name__", b"line 315, in __call__")
    elif broken == "stdlib_path":
        foreign = stdlib.with_name("other.py")
        foreign.write_bytes(stdlib.read_bytes())
        data = data.replace(stdlib.as_posix().encode(), foreign.as_posix().encode())
    elif broken in {"stdlib_source", "prefix_source"}:
        content = stdlib.read_text().splitlines()
        content[(315 if broken == "stdlib_source" else 302) - 1] = "raise RuntimeError('not the original source')"
        stdlib.write_text("\n".join(content) + "\n", encoding="utf-8")
    elif broken == "host_execution":
        source = Path(config["host_repository"]) / "src/lerobot/robots/alohamini/alohamini_host.py"
        line = source.read_text(encoding="utf-8").splitlines()[0].strip()
        frame = f'  File "{source.as_posix()}", line 1, in <module>\n    {line}\n'.encode()
        data = data.replace(b"KeyboardInterrupt", frame + b"KeyboardInterrupt")
    elif broken == "missing_chain":
        data = data[data.index(b"Traceback (most recent call last):", 1):]
    elif broken == "extra_footer":
        data += b"post-import execution\n"
    elif broken == "native_live":
        run["native_live_at"] = 1.
    elif broken == "fresh_torque":
        proof["readback"]["readbacks"][0]["value"] = 1
    elif broken == "fresh_lift":
        proof["readback"].pop("lift_stationary")
    elif broken == "fresh_thermal":
        proof["readback"]["lift_stationary"]["temperature_slots"][0][0] = 99.
    else:
        proof["original_host_logs"][path.name] = "0" * 64
    path.write_bytes(data)
    if broken != "trace_hash":
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


# Literal Actual96 note/packet/times; isolated IDs and fresh stopped proof below are synthetic.
ACTUAL96_CONFIGURE_STALE_NOTE = (
    '[AM1 TELEOP FAULT] {"reason":"actual configured temperature limit: arm_right_shoulder_pan; '
    'temperature: five-slot feedback window is stale (>0.5 s).","side":"right","register":"Present_Temper'
    'ature","raw":{"arm_right_shoulder_pan":31,"arm_right_shoulder_lift":27,"arm_right_elbow_flex":29,"ar'
    'm_right_wrist_flex":32,"arm_right_wrist_roll":30,"arm_right_gripper":29},"read_started_at_monotonic_'
    's":410730.158254421,"read_completed_at_monotonic_s":410730.159918472,"binding":{"recipe":"physical-l'
    'ive-teleop","run_id":"6486bf44-1e01-4cb2-9010-ea4572adafa1","host_incarnation":"59e3c32b-ecc4-486c-8'
    '00a-61ad6204f2b8"},"offending_motor":"arm_right_shoulder_pan","offending_value":31,"comparison":{"un'
    'its":"raw_register","lower_inclusive":0,"upper_exclusive":70,"temperature":{"ceiling_c":70,"samples"'
    ':[[410729.659892388,31],[410729.759878286,31],[410729.860166125,31],[410729.95995299,31],[410730.159'
    '918472,31]],"high_count":0,"numeric_outlier_count":0,"first_cause":"temperature: five-slot feedback '
    'window is stale (>0.5 s)."},"configured":{"Max_Temperature_Limit":{"value":70,"read_started_at_monot'
    'onic_s":410729.507972075,"read_completed_at_monotonic_s":410729.509958678}}},"prior_successful_healt'
    'h":null,"group_reply":{"binding":{"host_incarnation":"59e3c32b-ecc4-486c-800a-61ad6204f2b8","recipe"'
    ':"physical-live-teleop","run_id":"6486bf44-1e01-4cb2-9010-ea4572adafa1"},"request":{"binding":{"host'
    '_incarnation":"59e3c32b-ecc4-486c-800a-61ad6204f2b8","recipe":"physical-live-teleop","run_id":"6486b'
    'f44-1e01-4cb2-9010-ea4572adafa1"},"side":"right","address":63,"width":1,"ids":[1,2,3,4,5,6],"motors"'
    ':["arm_right_shoulder_pan","arm_right_shoulder_lift","arm_right_elbow_flex","arm_right_wrist_flex","'
    'arm_right_wrist_roll","arm_right_gripper"],"register":"Present_Temperature"},"group_call_started_at_'
    'monotonic_s":410730.158338845,"group_call_completed_at_monotonic_s":410730.159905898,"comm_result":0'
    ',"packets":[{"packet":[255,255,1,3,0,31,220],"device_error":0,"read_started_at_monotonic_s":410730.1'
    '58681711,"read_completed_at_monotonic_s":410730.159676698,"comm_result":0},{"packet":[255,255,2,3,0,'
    '27,223],"device_error":0,"read_started_at_monotonic_s":410730.159727327,"read_completed_at_monotonic'
    '_s":410730.159740586,"comm_result":0},{"packet":[255,255,3,3,0,29,220],"device_error":0,"read_starte'
    'd_at_monotonic_s":410730.159766604,"read_completed_at_monotonic_s":410730.159778308,"comm_result":0}'
    ',{"packet":[255,255,4,3,0,32,216],"device_error":0,"read_started_at_monotonic_s":410730.1598024,"rea'
    'd_completed_at_monotonic_s":410730.159813733,"comm_result":0},{"packet":[255,255,5,3,0,30,217],"devi'
    'ce_error":0,"read_started_at_monotonic_s":410730.159836585,"read_completed_at_monotonic_s":410730.15'
    '9847584,"comm_result":0},{"packet":[255,255,6,3,0,29,217],"device_error":0,"read_started_at_monotoni'
    'c_s":410730.159871343,"read_completed_at_monotonic_s":410730.159882565,"comm_result":0}],"transmit":'
    '{"packet":[255,255,254,10,130,63,1,1,2,3,4,5,6,32],"write_started_at_monotonic_s":410730.158375493,"'
    'write_completed_at_monotonic_s":410730.158676359,"result":14}}}'
)



def configure_cool_stale_fixture(tmp_path, monkeypatch):
    m, config, run, proof, state = cool_stale_preactivation_fixture(tmp_path, monkeypatch)
    fault = json.loads(ACTUAL96_CONFIGURE_STALE_NOTE.removeprefix("[AM1 TELEOP FAULT] "))
    binding = {
        "recipe": "physical-live-teleop", "run_id": run["run_id"],
        "host_incarnation": proof["host_incarnation"],
    }
    fault["binding"] = fault["group_reply"]["binding"] = fault["group_reply"]["request"]["binding"] = binding
    old = run["physical_result"]["readback"]
    cause = "RuntimeError: AM1 teleop " + fault["reason"]
    old["first_cause"] = run["native_first_cause"] = proof["original_first_cause"] = cause
    old["first_error_chain"] = [{"cause": cause, "notes": ["[AM1 TELEOP FAULT] " + json.dumps(fault)]}]
    proof["original_cleanup_origin"] = "preconfigure-cool-thermal-stale-lift-unqualified"
    rb = old["readback"]
    rb["started_at_monotonic_s"] = fault["read_completed_at_monotonic_s"] + .02
    for index, row in enumerate(rb["readbacks"]):
        row["read_started_at_monotonic_s"] = rb["started_at_monotonic_s"] + index * .001
        row["read_completed_at_monotonic_s"] = row["read_started_at_monotonic_s"] + .0005
    rb["completed_at_monotonic_s"] = rb["started_at_monotonic_s"] + .03
    return m, config, run, proof, state


def test_configure_cool_stale_origin_literal96_still_requires_fresh_proof_and_durable_ack(tmp_path, monkeypatch):
    m, config, run, proof, state = configure_cool_stale_fixture(tmp_path, monkeypatch)
    original = copy.deepcopy(run)
    raw = publish_incomplete_fixture(config, run, proof, state)
    actual, received, inventory = m.load_qualified_proof(config, run)
    assert actual == proof and received == raw and inventory == dict.fromkeys(m.MARKERS)
    operation = str(uuid.uuid4())
    ack = m.archive_and_ack(config, actual, received, inventory, operation)
    assert m.acknowledged_receipt({**actual, "acknowledgment": {**ack, "consumed_operation_id": operation}}, run, operation)
    assert run == original and run["uncertain"] is True and run["status"] == "faulted"
    assert run["physical_result"]["readback"]["readback"]["lift_readback"] == "unavailable-or-failed"
    assert not any((state / name).exists() for name in m.MARKERS)


@pytest.mark.parametrize("broken", [
    "old_origin", "partial_wrapper", "extra_rest", "extra_note", "prior_health", "missing_prior",
    "binding", "configured_time", "group_time", "packet_time", "packet_checksum", "device_error",
    "missing_group", "high", "high_votes", "not_stale", "wrong_side", "live", "progress",
    "old_nonzero", "restore_pending", "fresh_lift", "fresh_thermal", "fresh_nonzero", "boot", "age",
    "impossible_phase", "repeated_peak", "backward_peak", "packet_type", "source",
])
def test_configure_cool_stale_origin_near_misses_and_unqualified_fresh_proof_refuse(tmp_path, monkeypatch, broken):
    m, config, run, proof, state = configure_cool_stale_fixture(tmp_path, monkeypatch)
    old = run["physical_result"]["readback"]
    entry = old["first_error_chain"][0]
    fault = json.loads(entry["notes"][0].removeprefix("[AM1 TELEOP FAULT] "))
    if broken == "old_origin":
        proof["original_cleanup_origin"] = m.PREHOME_COOL_STALE_ORIGIN
    elif broken == "partial_wrapper":
        old["first_error_chain"].insert(0, {"cause": "RuntimeError: AlohaMini motor activation failed.", "notes": []})
    elif broken == "prior_health":
        fault["prior_successful_health"] = {"value": 31}
    elif broken == "missing_prior":
        fault.pop("prior_successful_health")
    elif broken == "binding":
        fault["binding"]["host_incarnation"] = str(uuid.uuid4())
    elif broken == "configured_time":
        fault["comparison"]["configured"]["Max_Temperature_Limit"]["read_completed_at_monotonic_s"] = fault["read_completed_at_monotonic_s"] + 1
    elif broken == "group_time":
        fault["group_reply"]["group_call_started_at_monotonic_s"] = fault["read_started_at_monotonic_s"] - 1
    elif broken == "packet_time":
        fault["group_reply"]["packets"][0]["read_completed_at_monotonic_s"] = fault["read_completed_at_monotonic_s"] + 1
    elif broken == "packet_checksum":
        fault["group_reply"]["packets"][0]["packet"][-1] ^= 1
    elif broken == "device_error":
        fault["group_reply"]["packets"][0]["device_error"] = 4
    elif broken == "missing_group":
        fault.pop("group_reply")
    elif broken == "high":
        fault["raw"]["arm_right_shoulder_pan"] = fault["offending_value"] = 70
    elif broken == "high_votes":
        fault["comparison"]["temperature"]["high_count"] = 1
    elif broken == "not_stale":
        fault["comparison"]["temperature"]["samples"][0][0] += .01
    elif broken == "impossible_phase":
        peaks = fault["comparison"]["temperature"]["samples"]
        for index in range(1, 4):
            peaks[index][0] = peaks[0][0] + index * .001
    elif broken == "repeated_peak":
        peaks = fault["comparison"]["temperature"]["samples"]
        peaks[1][0] = peaks[0][0]
    elif broken == "backward_peak":
        peaks = fault["comparison"]["temperature"]["samples"]
        peaks[1][0] = peaks[0][0] - .01
    elif broken == "packet_type":
        fault["group_reply"]["packets"][0] = []
    elif broken == "source":
        proof["source"]["backend_contract"] = dict(config, machine="foreign")
    elif broken == "wrong_side":
        fault["side"] = "left"
    elif broken == "live":
        run["native_live_at"] = 1.
    elif broken == "progress":
        run["progress_s"] = .1
    elif broken == "old_nonzero":
        old["readback"]["readbacks"][0]["value"] = 1
    elif broken == "restore_pending":
        old["readback"]["selected_gain_restore_required"] = True
    elif broken == "fresh_lift":
        proof["readback"].pop("lift_stationary")
    elif broken == "fresh_thermal":
        proof["readback"]["lift_stationary"]["temperature_slots"][0][0] = 90.
    elif broken == "fresh_nonzero":
        proof["readback"]["readbacks"][0]["value"] = 1
    elif broken == "boot":
        current_boot = proof["boot_id"]
        monkeypatch.setattr(m, "current_boot_id", lambda: current_boot)
        proof["boot_id"] = str(uuid.uuid4())
    elif broken == "age":
        monkeypatch.setattr(m.time, "monotonic", lambda: 1000.)
    entry["notes"] = ["[AM1 TELEOP FAULT] " + json.dumps(fault)]
    if broken == "extra_rest":
        entry["notes"].append('Original normal-rest acquisition: {"phase":"after_home"}')
    elif broken == "extra_note":
        entry["notes"].append("Unqualified operation followed")
    publish_incomplete_fixture(config, run, proof, state)
    with pytest.raises((ValueError, RuntimeError, KeyError)):
        m.load_qualified_proof(config, run)
    assert not list(state.glob("reconciliation-ack-*"))


# Literal Actual114 read-only baseline refusal; fresh stopped proof below remains synthetic.
ACTUAL114_RESPONSE_BASELINE = {'binding': {'host_incarnation': '1a96514b-89e7-49ef-b6f8-8cfe5be9c74f',
             'kind': 'left-response-p',
             'request': {'boot_id': '4ff03ddb-1495-485c-aae8-33a67d79a98f',
                         'expires_at_monotonic_s': 525964.05962306,
                         'kind': 'left-response-p',
                         'proportional': 32,
                         'request_id': 'f483a179-4902-413d-aec8-eb839d3902dc',
                         'source_head': '927c17aa52bdf8da3e0ab53d7a122bbb3749b816',
                         'version': 1,
                         'workload_segment': 'left-reach'},
             'request_sha256': '1762770d85bdb20d113118bef99766f0cc2ff39b133e5f4f2b75cba107586a14',
             'run_id': '120e914e-613d-4794-9d75-23c1567e0cd9',
             'version': 1},
 'events': [{'completed_at_monotonic_s': 525864.006334197,
             'operation': 'read',
             'phase': 'baseline',
             'register': 'Model_Number',
             'started_at_monotonic_s': 525864.00535018,
             'value': 777},
            {'completed_at_monotonic_s': 525864.00731601,
             'operation': 'read',
             'phase': 'baseline',
             'register': 'Firmware_Major_Version',
             'started_at_monotonic_s': 525864.006417197,
             'value': 3},
            {'completed_at_monotonic_s': 525864.008305545,
             'operation': 'read',
             'phase': 'baseline',
             'register': 'Firmware_Minor_Version',
             'started_at_monotonic_s': 525864.007390695,
             'value': 10},
            {'completed_at_monotonic_s': 525864.009818746,
             'operation': 'read',
             'phase': 'baseline',
             'register': 'Operating_Mode',
             'started_at_monotonic_s': 525864.008380063,
             'value': 0},
            {'completed_at_monotonic_s': 525864.010509948,
             'operation': 'read',
             'phase': 'baseline',
             'register': 'Torque_Enable',
             'started_at_monotonic_s': 525864.009893523,
             'value': 0},
            {'completed_at_monotonic_s': 525864.014249681,
             'operation': 'read',
             'phase': 'baseline',
             'register': 'Lock',
             'started_at_monotonic_s': 525864.010569078,
             'value': 0},
            {'completed_at_monotonic_s': 525864.015298864,
             'operation': 'read',
             'phase': 'baseline',
             'register': 'P_Coefficient',
             'started_at_monotonic_s': 525864.014371366,
             'value': 16},
            {'completed_at_monotonic_s': 525864.016291159,
             'operation': 'read',
             'phase': 'baseline',
             'register': 'I_Coefficient',
             'started_at_monotonic_s': 525864.015361698,
             'value': 0},
            {'completed_at_monotonic_s': 525864.017289879,
             'operation': 'read',
             'phase': 'baseline',
             'register': 'D_Coefficient',
             'started_at_monotonic_s': 525864.016337899,
             'value': 32},
            {'completed_at_monotonic_s': 525864.018288136,
             'operation': 'read',
             'phase': 'baseline',
             'register': 'Min_Position_Limit',
             'started_at_monotonic_s': 525864.017331064,
             'value': 864},
            {'completed_at_monotonic_s': 525864.019283171,
             'operation': 'read',
             'phase': 'baseline',
             'register': 'Max_Position_Limit',
             'started_at_monotonic_s': 525864.018328766,
             'value': 3073},
            {'completed_at_monotonic_s': 525864.020287892,
             'operation': 'read',
             'phase': 'baseline',
             'register': 'Present_Position',
             'started_at_monotonic_s': 525864.019323412,
             'value': 1090},
            {'completed_at_monotonic_s': 525864.021288556,
             'operation': 'read',
             'phase': 'baseline',
             'register': 'Goal_Position',
             'started_at_monotonic_s': 525864.020329817,
             'value': 2050},
            {'completed_at_monotonic_s': 525864.022287221,
             'operation': 'read',
             'phase': 'baseline',
             'register': 'ID',
             'started_at_monotonic_s': 525864.021329038,
             'value': 3},
            {'completed_at_monotonic_s': 525864.023282775,
             'operation': 'read',
             'phase': 'baseline',
             'register': 'Homing_Offset',
             'started_at_monotonic_s': 525864.022325758,
             'value': 1845},
            {'completed_at_monotonic_s': 525864.02428718,
             'operation': 'read',
             'phase': 'baseline',
             'register': 'Phase',
             'started_at_monotonic_s': 525864.023321404,
             'value': 12},
            {'completed_at_monotonic_s': 525864.025290808,
             'operation': 'read',
             'phase': 'baseline',
             'register': 'Max_Torque_Limit',
             'started_at_monotonic_s': 525864.024326661,
             'value': 1000},
            {'completed_at_monotonic_s': 525864.026288861,
             'operation': 'read',
             'phase': 'baseline',
             'register': 'Torque_Limit',
             'started_at_monotonic_s': 525864.025330511,
             'value': 1000},
            {'completed_at_monotonic_s': 525864.027281507,
             'operation': 'read',
             'phase': 'baseline',
             'register': 'CW_Dead_Zone',
             'started_at_monotonic_s': 525864.026326843,
             'value': 1},
            {'completed_at_monotonic_s': 525864.028299524,
             'operation': 'read',
             'phase': 'baseline',
             'register': 'CCW_Dead_Zone',
             'started_at_monotonic_s': 525864.027317785,
             'value': 1},
            {'completed_at_monotonic_s': 525864.029289948,
             'operation': 'read',
             'phase': 'baseline',
             'register': 'Acceleration',
             'started_at_monotonic_s': 525864.028348413,
             'value': 254},
            {'completed_at_monotonic_s': 525864.030288187,
             'operation': 'read',
             'phase': 'baseline',
             'register': 'Maximum_Acceleration',
             'started_at_monotonic_s': 525864.029328114,
             'value': 254},
            {'completed_at_monotonic_s': 525864.031283055,
             'operation': 'read',
             'phase': 'baseline',
             'register': 'Goal_Time',
             'started_at_monotonic_s': 525864.030327242,
             'value': 0},
            {'completed_at_monotonic_s': 525864.032290294,
             'operation': 'read',
             'phase': 'baseline',
             'register': 'Goal_Velocity',
             'started_at_monotonic_s': 525864.031319555,
             'value': 0},
            {'completed_at_monotonic_s': 525864.033293384,
             'operation': 'read',
             'phase': 'baseline',
             'register': 'Protection_Current',
             'started_at_monotonic_s': 525864.032329182,
             'value': 310},
            {'completed_at_monotonic_s': 525864.034288642,
             'operation': 'read',
             'phase': 'baseline',
             'register': 'Max_Temperature_Limit',
             'started_at_monotonic_s': 525864.033334977,
             'value': 70},
            {'completed_at_monotonic_s': 525864.035286491,
             'operation': 'read',
             'phase': 'baseline',
             'register': 'Min_Voltage_Limit',
             'started_at_monotonic_s': 525864.034325586,
             'value': 40},
            {'completed_at_monotonic_s': 525864.03628886,
             'operation': 'read',
             'phase': 'baseline',
             'register': 'Max_Voltage_Limit',
             'started_at_monotonic_s': 525864.035324325,
             'value': 140},
            {'completed_at_monotonic_s': 525864.037287025,
             'operation': 'read',
             'phase': 'baseline',
             'register': 'Protective_Torque',
             'started_at_monotonic_s': 525864.036328712,
             'value': 20},
            {'completed_at_monotonic_s': 525864.038299411,
             'operation': 'read',
             'phase': 'baseline',
             'register': 'Protection_Time',
             'started_at_monotonic_s': 525864.037323524,
             'value': 200},
            {'completed_at_monotonic_s': 525864.039281724,
             'operation': 'read',
             'phase': 'baseline',
             'register': 'Overload_Torque',
             'started_at_monotonic_s': 525864.038347411,
             'value': 80},
            {'completed_at_monotonic_s': 525864.040289167,
             'operation': 'read',
             'phase': 'baseline',
             'register': 'Over_Current_Protection_Time',
             'started_at_monotonic_s': 525864.039318483,
             'value': 200},
            {'completed_at_monotonic_s': 525864.041284276,
             'operation': 'read',
             'phase': 'baseline',
             'register': 'Velocity_closed_loop_P_proportional_coefficient',
             'started_at_monotonic_s': 525864.040327667,
             'value': 10},
            {'completed_at_monotonic_s': 525864.042288107,
             'operation': 'read',
             'phase': 'baseline',
             'register': 'Velocity_closed_loop_I_integral_coefficient',
             'started_at_monotonic_s': 525864.041321239,
             'value': 200},
            {'completed_at_monotonic_s': 525864.043286161,
             'operation': 'read',
             'phase': 'baseline',
             'register': 'Present_Current',
             'started_at_monotonic_s': 525864.042324403,
             'value': 0},
            {'completed_at_monotonic_s': 525864.044288918,
             'operation': 'read',
             'phase': 'baseline',
             'register': 'Present_Voltage',
             'started_at_monotonic_s': 525864.043323161,
             'value': 119},
            {'completed_at_monotonic_s': 525864.04528449,
             'operation': 'read',
             'phase': 'baseline',
             'register': 'Present_Temperature',
             'started_at_monotonic_s': 525864.044289566,
             'value': 28},
            {'completed_at_monotonic_s': 525864.046285525,
             'operation': 'read',
             'phase': 'baseline',
             'register': 'Status',
             'started_at_monotonic_s': 525864.045325046,
             'value': 0},
            {'address': 24,
             'completed_at_monotonic_s': 525864.047287968,
             'operation': 'read',
             'phase': 'baseline',
             'started_at_monotonic_s': 525864.046326821,
             'value': 16,
             'width': 1},
            {'address': 25,
             'completed_at_monotonic_s': 525864.048289892,
             'operation': 'read',
             'phase': 'baseline',
             'started_at_monotonic_s': 525864.047326523,
             'value': 0,
             'width': 1}],
 'frozen_teleop_registers': {'Acceleration': 254,
                             'D_Coefficient': 32,
                             'Firmware_Major_Version': 3,
                             'Firmware_Minor_Version': 10,
                             'Homing_Offset': 1845,
                             'ID': 3,
                             'I_Coefficient': 0,
                             'Max_Position_Limit': 3073,
                             'Max_Temperature_Limit': 70,
                             'Max_Voltage_Limit': 140,
                             'Maximum_Acceleration': 254,
                             'Min_Position_Limit': 864,
                             'Min_Voltage_Limit': 40,
                             'Model_Number': 777,
                             'Operating_Mode': 0,
                             'P_Coefficient': 16,
                             'Phase': 12,
                             'Protection_Current': 310},
 'kind': 'left-response-p',
 'motor': 'arm_left_elbow_flex',
 'motor_id': 3,
 'original_bytes': {'24': 16, '25': 0},
 'original_registers': {'Acceleration': 254,
                        'CCW_Dead_Zone': 1,
                        'CW_Dead_Zone': 1,
                        'D_Coefficient': 32,
                        'Firmware_Major_Version': 3,
                        'Firmware_Minor_Version': 10,
                        'Goal_Position': 2050,
                        'Goal_Time': 0,
                        'Goal_Velocity': 0,
                        'Homing_Offset': 1845,
                        'ID': 3,
                        'I_Coefficient': 0,
                        'Lock': 0,
                        'Max_Position_Limit': 3073,
                        'Max_Temperature_Limit': 70,
                        'Max_Torque_Limit': 1000,
                        'Max_Voltage_Limit': 140,
                        'Maximum_Acceleration': 254,
                        'Min_Position_Limit': 864,
                        'Min_Voltage_Limit': 40,
                        'Model_Number': 777,
                        'Operating_Mode': 0,
                        'Over_Current_Protection_Time': 200,
                        'Overload_Torque': 80,
                        'P_Coefficient': 16,
                        'Phase': 12,
                        'Present_Current': 0,
                        'Present_Position': 1090,
                        'Present_Temperature': 28,
                        'Present_Voltage': 119,
                        'Protection_Current': 310,
                        'Protection_Time': 200,
                        'Protective_Torque': 20,
                        'Status': 0,
                        'Torque_Enable': 0,
                        'Torque_Limit': 1000,
                        'Velocity_closed_loop_I_integral_coefficient': 200,
                        'Velocity_closed_loop_P_proportional_coefficient': 10},
 'original_settings_sha256': '58a201eda573458979914c8144880f8e709ac8d3774144c29900fef818e57fd1',
 'prepared_at_monotonic_s': 525864.048308225,
 'restore_required': False,
 'schema_version': 1,
 'state': 'baseline_refused',
 'status': 'baseline_refused'}


def response_baseline_refused_fixture(tmp_path, monkeypatch, *, ordinary_current_rest=False, volatile_stopped=False):
    m, config, run, proof, state = incomplete_preactivation_fixture(tmp_path, monkeypatch)
    old = run["physical_result"]["readback"]
    trial = copy.deepcopy(ACTUAL114_RESPONSE_BASELINE)
    trial["binding"].update(run_id=run["run_id"], host_incarnation=proof["host_incarnation"])
    if ordinary_current_rest:
        # Current Stage68 no-write refusal: PP1299 lies outside the1051..1164
        # corridor while Goal1140 and every original stopped setting remain legal.
        trial["binding"]["request"].update(
            source_head="1a14b2559aebf10df20183aa9fbe094fa0196f94",
            workload_segment="ordinary-arms",
            workspace_profile="ordinary-arms-left-current-rest-1051-1164",
            left_shoulder_startup_degrees=1,
        )
        trial["binding"]["request_sha256"] = hashlib.sha256(canonical(trial["binding"]["request"])).hexdigest()
        registers = trial["original_registers"]
        registers.update(Present_Position=1299, Goal_Position=1140, Present_Temperature=28, Present_Voltage=118)
        if volatile_stopped:
            # Actual963 has a legal current rest but uninitialized volatile fields;
            # it supplies no powered readiness, writes, or restoration obligation.
            trial["binding"]["request"]["source_head"] = "6325b718315257b59dc419e88744e7122bfc561d"
            trial["binding"]["request_sha256"] = hashlib.sha256(canonical(trial["binding"]["request"])).hexdigest()
            registers.update(Present_Position=1142, Goal_Position=0, Acceleration=0)
            trial["frozen_teleop_registers"]["Acceleration"] = 0
        for event in trial["events"]:
            event["value"] = registers[event["register"]] if "register" in event else trial["original_bytes"][str(event["address"])]
        trial["original_settings_sha256"] = hashlib.sha256(canonical({"registers": registers, "individual_bytes": trial["original_bytes"]})).hexdigest()
    cause = "RuntimeError: Selected elbow response P actual stopped baseline differs from its reviewed original"
    old.update(first_cause=cause, first_error_chain=[{"cause": cause, "notes": []}])
    old["readback"]["left_response_p_trial"] = trial
    old["readback"]["started_at_monotonic_s"] = 525864.05
    for index, row in enumerate(old["readback"]["readbacks"]):
        row["read_started_at_monotonic_s"] = 525864.05 + index * .001
        row["read_completed_at_monotonic_s"] = row["read_started_at_monotonic_s"] + .0005
    old["readback"]["completed_at_monotonic_s"] = 525864.08
    run["native_first_cause"] = proof["original_first_cause"] = cause
    config["host_head"] = proof["source"]["motor_head"] = trial["binding"]["request"]["source_head"]
    run["backend_contract"] = copy.deepcopy(config)
    run["recipe"] = "physical-live-teleop"
    proof["boot_id"] = trial["binding"]["request"]["boot_id"]
    proof["original_cleanup_origin"] = "preconfigure-left-response-baseline-refused-lift-unqualified"
    # Only synthetic fresh-proof timestamps move; original114 baseline reads remain literal.
    def fresh_clock(value):
        if isinstance(value, dict):
            return {key: fresh_clock(item) for key, item in value.items()}
        if isinstance(value, list):
            return [fresh_clock(item) for item in value]
        return value + 525800 if type(value) is float and 99 <= value <= 100.1 else value
    fresh = fresh_clock(proof)
    proof.clear()
    proof.update(fresh)
    from types import SimpleNamespace
    monkeypatch.setattr(m, "time", SimpleNamespace(monotonic=lambda: 525900.1))
    return m, config, run, proof, state


def test_response_baseline_refusal_literal114_requires_fresh_proof_and_durable_ack(tmp_path, monkeypatch):
    m, config, run, proof, state = response_baseline_refused_fixture(tmp_path, monkeypatch)
    before = copy.deepcopy(run)
    raw = publish_incomplete_fixture(config, run, proof, state)
    actual, received, inventory = m.load_qualified_proof(config, run)
    assert actual == proof and received == raw and inventory == dict.fromkeys(m.MARKERS)
    acknowledged = str(uuid.uuid4())
    consumed = str(uuid.uuid4())
    ack = m.archive_and_ack(config, actual, received, inventory, acknowledged)
    assert m.acknowledged_receipt({**actual, "acknowledgment": {**ack, "consumed_operation_id": consumed}}, run, consumed)
    assert run == before and run["status"] == "faulted" and run["uncertain"] is True
    old = run["physical_result"]["readback"]
    assert old["readback"]["lift_readback"] == "unavailable-or-failed"
    assert old["readback"]["left_response_p_trial"]["restore_required"] is False
    assert all(not (state / name).exists() for name in m.MARKERS)


def test_response_baseline_refusal_current_rest_requires_fresh_proof_and_distinct_ack(tmp_path, monkeypatch):
    m, config, run, proof, state = response_baseline_refused_fixture(tmp_path, monkeypatch, ordinary_current_rest=True)
    before = copy.deepcopy(run)
    raw = publish_incomplete_fixture(config, run, proof, state)
    actual, received, inventory = m.load_qualified_proof(config, run)
    assert actual == proof and received == raw and inventory == dict.fromkeys(m.MARKERS)
    ack_operation, consumed = str(uuid.uuid4()), str(uuid.uuid4())
    ack = m.archive_and_ack(config, actual, received, inventory, ack_operation)
    assert m.acknowledged_receipt({**actual, "acknowledgment": {**ack, "consumed_operation_id": consumed}}, run, consumed)
    assert run == before and run["uncertain"] is True and run["status"] == "faulted"
    assert run["native_live_at"] is None and run["progress_s"] == 0
    assert run["physical_result"]["readback"]["readback"]["lift_readback"] == "unavailable-or-failed"
    assert all(not (state / name).exists() for name in m.MARKERS)


@pytest.mark.parametrize("broken", [
    "profile", "profile_absent", "segment", "allowance", "allowance_boolean", "allowance_float", "request_extra",
    "source", "source_invalid", "request_hash", "run", "host", "boot", "expiry", "first_cause",
    "inside", "outside_calibration", "goal_outside", "raw_p", "frozen", "current", "status",
    "torque", "lock", "hot", "voltage", "byte", "obligation", "state", "configured", "digest",
    "write", "missing_event", "event_value", "event_time", "live", "progress", "original_nonzero",
    "fresh_lift", "fresh_thermal", "fresh_nonzero", "proof_age", "proof_boot",
])
def test_response_baseline_current_rest_refuses_drift_and_missing_fresh_proof(tmp_path, monkeypatch, broken):
    m, config, run, proof, state = response_baseline_refused_fixture(tmp_path, monkeypatch, ordinary_current_rest=True)
    old = run["physical_result"]["readback"]
    trial = old["readback"]["left_response_p_trial"]
    binding, registers = trial["binding"], trial["original_registers"]
    request = binding["request"]
    if broken == "profile": request["workspace_profile"] = "left-current-rest-1051-1164"
    elif broken == "profile_absent": request.pop("workspace_profile")
    elif broken == "segment": request["workload_segment"] = "left-reach"
    elif broken == "allowance": request["left_shoulder_startup_degrees"] = 2
    elif broken == "allowance_boolean": request["left_shoulder_startup_degrees"] = True
    elif broken == "allowance_float": request["left_shoulder_startup_degrees"] = 1.0
    elif broken == "request_extra": request["extra"] = True
    elif broken == "source": request["source_head"] = "a" * 40
    elif broken == "source_invalid":
        request["source_head"] = config["host_head"] = proof["source"]["motor_head"] = "unsealed"
        run["backend_contract"] = copy.deepcopy(config)
    elif broken == "request_hash": binding["request_sha256"] = "a" * 64
    elif broken == "run": binding["run_id"] = str(uuid.uuid4())
    elif broken == "host": binding["host_incarnation"] = str(uuid.uuid4())
    elif broken == "boot": request["boot_id"] = str(uuid.uuid4())
    elif broken == "expiry": request["expires_at_monotonic_s"] = trial["prepared_at_monotonic_s"]
    elif broken == "first_cause": old["first_error_chain"][0]["cause"] = "another failure"
    elif broken == "inside": registers["Present_Position"] = 1100
    elif broken == "outside_calibration": registers["Present_Position"] = 3074
    elif broken == "goal_outside": registers["Goal_Position"] = 863
    elif broken == "raw_p": registers["P_Coefficient"] = 32
    elif broken == "frozen": trial["frozen_teleop_registers"]["P_Coefficient"] = 32
    elif broken == "current": registers["Present_Current"] = 1
    elif broken == "status": registers["Status"] = 1
    elif broken == "torque": registers["Torque_Enable"] = 1
    elif broken == "lock": registers["Lock"] = 1
    elif broken == "hot": registers["Present_Temperature"] = 70
    elif broken == "voltage": registers["Present_Voltage"] = 0
    elif broken == "byte": trial["original_bytes"]["24"] = 32
    elif broken == "obligation": trial["restore_required"] = True
    elif broken == "state": trial["state"] = "prepared"
    elif broken == "configured": trial["configured_at_monotonic_s"] = trial["prepared_at_monotonic_s"]
    elif broken == "digest": trial["original_settings_sha256"] = "a" * 64
    elif broken == "write": trial["events"][-1]["operation"] = "write"
    elif broken == "missing_event": trial["events"].pop()
    elif broken == "event_value": trial["events"][0]["value"] = 778
    elif broken == "event_time": trial["events"][0]["completed_at_monotonic_s"] = trial["prepared_at_monotonic_s"] + .1
    elif broken == "live": run["native_live_at"] = 525863.0
    elif broken == "progress": run["progress_s"] = .1
    elif broken == "original_nonzero": old["readback"]["readbacks"][0]["value"] = 1
    elif broken == "fresh_lift": proof["readback"]["lift_stationary"] = None
    elif broken == "fresh_thermal": proof["readback"]["lift_stationary"]["temperature_slots"][0][0] -= 1
    elif broken == "fresh_nonzero": proof["readback"]["readbacks"][0]["value"] = 1
    elif broken == "proof_age": monkeypatch.setattr(m.time, "monotonic", lambda: proof["completed_at_monotonic_s"] + 181)
    elif broken == "proof_boot": monkeypatch.setattr(m, "current_boot_id", lambda: str(uuid.uuid4()))
    if broken in {"inside", "outside_calibration", "goal_outside", "raw_p", "current", "status", "torque", "lock", "hot", "voltage", "byte"}:
        for event in trial["events"]:
            event["value"] = registers[event["register"]] if "register" in event else trial["original_bytes"][str(event["address"])]
        trial["original_settings_sha256"] = hashlib.sha256(canonical({"registers": registers, "individual_bytes": trial["original_bytes"]})).hexdigest()
    if broken in {"profile", "profile_absent", "segment", "allowance", "allowance_boolean", "allowance_float", "request_extra", "source", "source_invalid", "boot", "expiry"}:
        binding["request_sha256"] = hashlib.sha256(canonical(request)).hexdigest()
    publish_incomplete_fixture(config, run, proof, state)
    before = copy.deepcopy(run)
    with pytest.raises((ValueError, KeyError, TypeError)):
        m.load_qualified_proof(config, run)
    assert run == before and not (state / ("reconciliation-ack-" + run["run_id"] + ".json")).exists()


@pytest.mark.parametrize("broken", [
    "origin", "source", "request_source", "request_hash", "request_run", "request_host", "request_boot",
    "request_kind", "request_extra", "request_expiry", "trial_kind", "trial_state", "trial_extra",
    "obligation", "configured", "snapshot_digest", "frozen", "static", "boolean_raw", "pp_inside",
    "pp_outside_cal", "goal_outside", "current", "status", "torque", "lock", "hot", "voltage",
    "byte", "write", "missing_event", "duplicate_event", "event_error", "event_value", "event_time",
    "live", "progress", "original_nonzero", "fresh_lift", "fresh_thermal", "fresh_nonzero", "age", "boot",
])
def test_response_baseline_refusal_cannot_qualify_other_origin_or_incomplete_new_proof(tmp_path, monkeypatch, broken):
    m, config, run, proof, state = response_baseline_refused_fixture(tmp_path, monkeypatch)
    old = run["physical_result"]["readback"]
    trial = old["readback"]["left_response_p_trial"]
    binding, regs = trial["binding"], trial["original_registers"]
    request = binding["request"]
    if broken == "origin":
        proof["original_cleanup_origin"] = m.INCOMPLETE_PREACTIVATION_ORIGIN
    elif broken == "source":
        config["host_head"] = proof["source"]["motor_head"] = "a" * 40
    elif broken == "request_source":
        request["source_head"] = "a" * 40
    elif broken == "request_hash":
        binding["request_sha256"] = "a" * 64
    elif broken == "request_run":
        binding["run_id"] = str(uuid.uuid4())
    elif broken == "request_host":
        binding["host_incarnation"] = str(uuid.uuid4())
    elif broken == "request_boot":
        request["boot_id"] = str(uuid.uuid4())
    elif broken == "request_kind":
        request["kind"] = "right-response-p"
    elif broken == "request_extra":
        request["extra"] = True
    elif broken == "request_expiry":
        request["expires_at_monotonic_s"] = 525863.0
    elif broken == "trial_kind":
        trial["kind"] = "right-response-p"
    elif broken == "trial_state":
        trial["status"] = "prepared"
    elif broken == "trial_extra":
        trial["active_registers"] = dict(regs)
    elif broken == "obligation":
        trial["restore_required"] = True
    elif broken == "configured":
        trial["configured_at_monotonic_s"] = trial["prepared_at_monotonic_s"]
    elif broken == "snapshot_digest":
        trial["original_settings_sha256"] = "a" * 64
    elif broken == "frozen":
        trial["frozen_teleop_registers"]["P_Coefficient"] = 32
    elif broken == "static":
        regs["Protection_Current"] = 0
    elif broken == "boolean_raw":
        regs["Torque_Enable"] = False
    elif broken == "pp_inside":
        regs["Present_Position"] = 2050
    elif broken == "pp_outside_cal":
        regs["Present_Position"] = 863
    elif broken == "goal_outside":
        regs["Goal_Position"] = 1090
    elif broken == "current":
        regs["Present_Current"] = 1
    elif broken == "status":
        regs["Status"] = 1
    elif broken == "torque":
        regs["Torque_Enable"] = 1
    elif broken == "lock":
        regs["Lock"] = 1
    elif broken == "hot":
        regs["Present_Temperature"] = 70
    elif broken == "voltage":
        regs["Present_Voltage"] = 0
    elif broken == "byte":
        trial["original_bytes"]["24"] = 0
    elif broken == "write":
        trial["events"][-1]["operation"] = "write"
    elif broken == "missing_event":
        trial["events"].pop()
    elif broken == "duplicate_event":
        trial["events"][-1] = copy.deepcopy(trial["events"][-2])
    elif broken == "event_error":
        trial["events"][0]["error"] = "fault"
    elif broken == "event_value":
        trial["events"][0]["value"] = 778
    elif broken == "event_time":
        trial["events"][1]["started_at_monotonic_s"] = 525864.0
    elif broken == "live":
        run["native_live_at"] = 525863.0
    elif broken == "progress":
        run["progress_s"] = .1
    elif broken == "original_nonzero":
        old["readback"]["readbacks"][0]["value"] = 1
    elif broken == "fresh_lift":
        proof["readback"]["lift_stationary"] = None
    elif broken == "fresh_thermal":
        proof["readback"]["lift_stationary"]["temperature_slots"][0][0] -= 1
    elif broken == "fresh_nonzero":
        proof["readback"]["readbacks"][0]["value"] = 1
    elif broken == "age":
        monkeypatch.setattr(m.time, "monotonic", lambda: proof["completed_at_monotonic_s"] + 181)
    elif broken == "boot":
        monkeypatch.setattr(m, "current_boot_id", lambda: str(uuid.uuid4()))
    # Mutated register guards must still refuse with internally coherent hashes/events.
    if broken in {"static", "boolean_raw", "pp_inside", "pp_outside_cal", "goal_outside", "current", "status", "torque", "lock", "hot", "voltage", "byte"}:
        for event in trial["events"]:
            event["value"] = regs[event["register"]] if "register" in event else trial["original_bytes"][str(event["address"])]
        trial["original_settings_sha256"] = hashlib.sha256(canonical({"registers": regs, "individual_bytes": trial["original_bytes"]})).hexdigest()
    if broken in {"request_source", "request_kind", "request_extra", "request_expiry", "request_boot"}:
        binding["request_sha256"] = hashlib.sha256(canonical(request)).hexdigest()
    publish_incomplete_fixture(config, run, proof, state)
    before = copy.deepcopy(run)
    with pytest.raises((ValueError, KeyError, TypeError)):
        m.load_qualified_proof(config, run)
    assert run == before
    assert not (state / ("reconciliation-ack-" + run["run_id"] + ".json")).exists()
RESPONSE_GOAL_ORIGIN = "preconfigure-left-response-goal-restore-incomplete-lift-unqualified"
RESPONSE_GOAL_RUN = "72de22d9-ee93-4f76-809a-f10bd88e512a"
RESPONSE_GOAL_HOST = "f19de2d7-e77f-4277-9e61-13824813ea31"


def response_goal_completion_fixture(tmp_path, monkeypatch, *, already_unlocked=False, lost_ack=False):
    m, config, run, proof, state, _old, _raw = loader_fixture(tmp_path, monkeypatch)
    previous = Path(config["run_root"]) / run["run_id"][:8]
    run.update(
        run_id=RESPONSE_GOAL_RUN,
        native_live_at=None,
        progress_s=0.0,
        recipe="physical-live-teleop",
        cleanup="physical_cleanup_unknown",
    )
    folder = previous.with_name(RESPONSE_GOAL_RUN[:8])
    previous.rename(folder)
    proof.update(
        run_id=RESPONSE_GOAL_RUN,
        host_incarnation=RESPONSE_GOAL_HOST,
        boot_id="4ff03ddb-1495-485c-aae8-33a67d79a98f",
        original_cleanup_origin=RESPONSE_GOAL_ORIGIN,
    )
    config["host_head"] = proof["source"]["motor_head"] = "c4b35ba83acb188f7884db5cfcd686aa41902e16"
    proof["source"]["original_executor_head"] = "9985a6333abf34081251b6799f1326bb2a53b64e"
    run["backend_contract"] = copy.deepcopy(config)
    run["physical_result"].update(
        backend_incarnation=RESPONSE_GOAL_HOST,
        run_id=RESPONSE_GOAL_RUN,
        phase="terminal",
        cleanup="physical_cleanup_unknown",
        uncertain=True,
        native_live_at=None,
    )
    cause = "RuntimeError: Current-rest Goal write lost actual Torque0"
    run["native_first_cause"] = proof["original_first_cause"] = cause
    trial = copy.deepcopy(ACTUAL114_RESPONSE_BASELINE)
    trial.update(
        state="restore_failed",
        status="restore_failed",
        restore_required=True,
        first_failure={
            "type": "RuntimeError",
            "message": cause.removeprefix("RuntimeError: "),
            "traceback": "retained original failure",
        },
    )
    trial["binding"].update(run_id=RESPONSE_GOAL_RUN, host_incarnation=RESPONSE_GOAL_HOST)
    request = trial["binding"]["request"]
    request.update(
        request_id="307a568d-fb85-4db4-b761-29d84e1416fb",
        source_head=config["host_head"],
        boot_id=proof["boot_id"],
        expires_at_monotonic_s=535666.267898183,
        workspace_profile="left-current-rest-1051-1164",
    )
    trial["binding"]["request_sha256"] = hashlib.sha256(canonical(request)).hexdigest()
    trial["original_registers"].update(Present_Position=1090, Goal_Position=2050, Present_Temperature=27)
    trial["original_settings_sha256"] = hashlib.sha256(
        canonical({"registers": trial["original_registers"], "individual_bytes": trial["original_bytes"]})
    ).hexdigest()
    for index, event in enumerate(trial["events"]):
        event.update(
            started_at_monotonic_s=535574.0 + index * 0.001,
            completed_at_monotonic_s=535574.0005 + index * 0.001,
        )
        event["value"] = (
            trial["original_registers"][event["register"]]
            if "register" in event
            else trial["original_bytes"][str(event["address"])]
        )
    trial["prepared_at_monotonic_s"] = 535574.05

    def old_event(operation, register, value, phase):
        at = 535574.1 + len(trial["events"]) * 0.001
        row = {
            "operation": operation,
            "register": register,
            "value": value,
            "phase": phase,
            "started_at_monotonic_s": at,
            "completed_at_monotonic_s": at + 0.0005,
        }
        trial["events"].append(row)
        return row

    trial["priming_position"] = copy.deepcopy(
        old_event("read", "Present_Position", 1090, "current_rest_prime")
    )
    for phase, value in (("prime_current_rest_goal", 1090), ("restore_original_goal", 2050)):
        old_event("read", "Torque_Enable", 0, phase + "_before")
        old_event("write", "Goal_Position", value, phase)
        old_event("read", "Goal_Position", value, phase + "_verify")
        old_event("read", "Torque_Enable", 1, phase + "_after")
    old_event("write", "Torque_Enable", 0, "restore_failed_torque_off")
    old_event("read", "Torque_Enable", 0, "restore_failed_torque_off_verify")
    old_event("write", "Lock", 1, "restore_failed_lock")
    old_event("read", "Lock", 1, "restore_failed_lock_verify")
    old = run["physical_result"]["readback"]
    old.update(
        run_id=RESPONSE_GOAL_RUN,
        host_incarnation=RESPONSE_GOAL_HOST,
        cleanup="physical_cleanup_unknown",
        uncertain=True,
        first_cause=cause,
        cleanup_errors=[cause],
        provenance="protected-host/actual-motor-readback",
        first_error_chain=[{"cause": cause, "notes": []}],
    )
    old["readback"] = {
        "verified": False,
        "buses_closed": True,
        "lift_readback": "unavailable-or-failed",
        "selected_gain_restore_required": True,
        "cleanup_errors": [cause],
        "left_response_p_trial": trial,
        "started_at_monotonic_s": 535574.5,
        "completed_at_monotonic_s": 535574.6,
        "readbacks": copy.deepcopy(proof["readback"]["readbacks"]),
    }
    for index, row in enumerate(old["readback"]["readbacks"]):
        row.update(
            read_started_at_monotonic_s=535574.5 + index * 0.001,
            read_completed_at_monotonic_s=535574.5005 + index * 0.001,
        )

    def fresh_clock(value):
        if isinstance(value, dict):
            return {key: fresh_clock(item) for key, item in value.items()}
        if isinstance(value, list):
            return [fresh_clock(item) for item in value]
        return value + 535500 if type(value) is float and 99 <= value <= 100.1 else value

    proof = fresh_clock(proof)
    proof["camera_release"]["session_id"] = RESPONSE_GOAL_RUN
    run["physical_result"]["camera_release"] = copy.deepcopy(proof["camera_release"])
    rows = []

    def completion_row(operation, register, value, **extra):
        at = 535598.0 + len(rows) * 0.001
        row = {
            "operation": operation,
            "phase": "stopped_completion",
            "value": value,
            "started_at_monotonic_s": at,
            "completed_at_monotonic_s": at + 0.0005,
            **extra,
        }
        if register is not None:
            row["register"] = register
        rows.append(row)

    for event in trial["events"][:40]:
        if "register" in event:
            completion_row(
                "read",
                event["register"],
                (0 if already_unlocked else 1) if event["register"] == "Lock" else event["value"],
            )
        else:
            completion_row("read", None, event["value"], address=event["address"], width=1)
    if not already_unlocked:
        completion_row(
            "write",
            "Lock",
            0,
            **({"reconciled": True, "error_type": "OSError", "error": "lost receipt"} if lost_ack else {}),
        )
        if lost_ack:
            completion_row("read", "Lock", 0)
            rows[-2]["completed_at_monotonic_s"] = rows[-1]["completed_at_monotonic_s"] + 0.0001
    for register, value in (("Lock", 0), ("Torque_Enable", 0), ("Goal_Position", 2050)):
        completion_row("read", register, value)
    original_raw = (json.dumps(trial, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    completion = {
        "version": 1,
        "kind": "left-response-p-stopped-completion",
        "completion_id": str(uuid.uuid4()),
        "run_id": RESPONSE_GOAL_RUN,
        "host_incarnation": RESPONSE_GOAL_HOST,
        "request_sha256": trial["binding"]["request_sha256"],
        "source_head": config["host_head"],
        "original_sha256": hashlib.sha256(original_raw).hexdigest(),
        "original_settings_sha256": trial["original_settings_sha256"],
        "original_lock": 0,
        "selected_restoration_verified": True,
        "native_acknowledged": False,
        "events": rows,
        "started_at_monotonic_s": 535598.0,
        "completed_at_monotonic_s": 535598.1,
    }
    proof["selected_restoration"] = {
        "sha256": hashlib.sha256(canonical(completion)).hexdigest(),
        "receipt": completion,
    }
    from types import SimpleNamespace

    monkeypatch.setattr(m, "time", SimpleNamespace(monotonic=lambda: 535600.1))
    monkeypatch.setattr(m, "current_boot_id", lambda: proof["boot_id"])
    return m, config, run, proof, state


def publish_response_goal_fixture(config, run, proof, state):
    folder = Path(config["run_root"]) / run["run_id"][:8]
    old = run["physical_result"]["readback"]
    original = canonical(old) + b"\n"
    (folder / "cleanup.json").write_bytes(original)
    proof["original_cleanup_sha256"] = hashlib.sha256(original).hexdigest()
    trial = old["readback"]["left_response_p_trial"]
    (folder / "left-response-p-original.json").write_bytes(
        (json.dumps(trial, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    )
    completion = proof.get("selected_restoration", {}).get("receipt")
    if completion:
        (
            folder / ("left-response-p-stopped-completion-" + completion["completion_id"] + ".json")
        ).write_bytes(canonical(completion))
    claim = canonical({"run_id": run["run_id"], "host_incarnation": proof["host_incarnation"]})
    proof["original_markers"] = {
        "cleanup-uncertain.json": {"sha256": hashlib.sha256(original).hexdigest()},
        "physical-in-progress.json": {"sha256": hashlib.sha256(claim).hexdigest()},
    }
    (state / "cleanup-uncertain.json").write_bytes(original)
    (state / "physical-in-progress.json").write_bytes(claim)
    camera_raw = canonical(proof["camera_release"]) + b"\n"
    (Path(config["camera_state"]) / ("release-" + run["run_id"] + ".json")).write_bytes(camera_raw)
    proof["camera_release_sha256"] = hashlib.sha256(camera_raw).hexdigest()
    proof["original_terminal_run_sha256"] = hashlib.sha256(canonical(run)).hexdigest()
    raw = canonical(proof)
    (state / ("reconciliation-" + run["run_id"] + ".json")).write_bytes(raw)
    return raw


@pytest.mark.parametrize("already_unlocked,lost_ack", [(False, False), (True, False), (False, True)])
def test_response_goal_completion115_requires_separate_actual_completion_fresh_proof_and_ack(
    tmp_path, monkeypatch, already_unlocked, lost_ack
):
    m, config, run, proof, state = response_goal_completion_fixture(
        tmp_path, monkeypatch, already_unlocked=already_unlocked, lost_ack=lost_ack
    )
    before = copy.deepcopy(run)
    raw = publish_response_goal_fixture(config, run, proof, state)
    actual, received, markers = m.load_qualified_proof(config, run)
    assert actual == proof and received == raw and all(value is not None for value in markers.values())
    consumed = str(uuid.uuid4())
    ack = m.archive_and_ack(config, actual, received, markers, str(uuid.uuid4()))
    assert m.acknowledged_receipt(
        {**actual, "acknowledgment": {**ack, "consumed_operation_id": consumed}}, run, consumed
    )
    assert (
        run == before
        and run["uncertain"]
        and run["physical_result"]["readback"]["readback"]["selected_gain_restore_required"]
    )
    assert all(not (state / name).exists() for name in m.MARKERS)


@pytest.mark.parametrize(
    "bad",
    [
        "missing",
        "generic",
        "foreign_run",
        "foreign_host",
        "source",
        "request",
        "original",
        "settings",
        "sha",
        "claimed_ack",
        "completion_after",
        "completion_old",
        "goal_write",
        "p_write",
        "final_torque",
        "final_goal",
        "final_lock",
        "missing_read",
        "backward",
        "slow",
        "extra",
        "no_lift",
        "stale_thermal",
        "boot",
        "missing_file",
        "changed_file",
        "changed_original",
    ],
)
def test_response_goal_completion115_never_substitutes_flags_or_unqualified_evidence(
    tmp_path, monkeypatch, bad
):
    m, config, run, proof, state = response_goal_completion_fixture(tmp_path, monkeypatch)
    completed = proof["selected_restoration"]["receipt"]
    if bad == "missing":
        proof.pop("selected_restoration")
    elif bad == "generic":
        proof.pop("original_cleanup_origin")
        proof.pop("selected_restoration")
    elif bad == "foreign_run":
        completed["run_id"] = str(uuid.uuid4())
    elif bad == "foreign_host":
        completed["host_incarnation"] = str(uuid.uuid4())
    elif bad == "source":
        completed["source_head"] = "a" * 40
    elif bad == "request":
        completed["request_sha256"] = "a" * 64
    elif bad == "original":
        completed["original_sha256"] = "a" * 64
    elif bad == "settings":
        completed["original_settings_sha256"] = "a" * 64
    elif bad == "sha":
        proof["selected_restoration"]["sha256"] = "a" * 64
    elif bad == "claimed_ack":
        completed["native_acknowledged"] = True
    elif bad == "completion_after":
        completed["completed_at_monotonic_s"] = proof["started_at_monotonic_s"] + 0.1
    elif bad == "completion_old":
        completed["started_at_monotonic_s"] -= 200
        completed["completed_at_monotonic_s"] -= 200
    elif bad in {"goal_write", "p_write"}:
        completed["events"][-4]["register"] = "Goal_Position" if bad == "goal_write" else "P_Coefficient"
    elif bad == "final_torque":
        completed["events"][-2]["value"] = 1
    elif bad == "final_goal":
        completed["events"][-1]["value"] = 1090
    elif bad == "final_lock":
        completed["events"][-3]["value"] = 1
    elif bad == "missing_read":
        completed["events"].pop(0)
    elif bad == "backward":
        completed["events"][1]["started_at_monotonic_s"] -= 1
    elif bad == "slow":
        completed["events"][0]["completed_at_monotonic_s"] += 0.151
    elif bad == "extra":
        completed["scope"] = "ordinary operation"
    elif bad == "no_lift":
        proof["readback"]["lift_readback"] = "unavailable-or-failed"
    elif bad == "stale_thermal":
        proof["readback"]["lift_stationary"]["temperature_ready"] = False
    elif bad == "boot":
        proof["boot_id"] = str(uuid.uuid4())
    if "selected_restoration" in proof and bad != "sha":
        proof["selected_restoration"]["sha256"] = hashlib.sha256(canonical(completed)).hexdigest()
    publish_response_goal_fixture(config, run, proof, state)
    folder = Path(config["run_root"]) / run["run_id"][:8]
    path = folder / ("left-response-p-stopped-completion-" + completed["completion_id"] + ".json")
    if bad == "missing_file":
        path.unlink()
    elif bad == "changed_file":
        path.write_bytes(path.read_bytes() + b" ")
    elif bad == "changed_original":
        (folder / "left-response-p-original.json").write_bytes(b"changed original")
    before = copy.deepcopy(run)
    with pytest.raises((ValueError, KeyError, TypeError, FileNotFoundError)):
        m.load_qualified_proof(config, run)
    assert run == before and not (state / ("reconciliation-ack-" + run["run_id"] + ".json")).exists()
    assert all((state / name).exists() for name in m.MARKERS)


@pytest.mark.parametrize("changed", ["completion", "original"])
def test_response_goal_completion115_changed_after_validation_never_receives_ack(
    tmp_path, monkeypatch, changed
):
    m, config, run, proof, state = response_goal_completion_fixture(tmp_path, monkeypatch)
    raw = publish_response_goal_fixture(config, run, proof, state)
    actual, received, markers = m.load_qualified_proof(config, run)
    folder = Path(config["run_root"]) / run["run_id"][:8]
    path = folder / (
        "left-response-p-original.json"
        if changed == "original"
        else "left-response-p-stopped-completion-"
        + proof["selected_restoration"]["receipt"]["completion_id"]
        + ".json"
    )
    path.write_bytes(b"changed")
    with pytest.raises(ValueError):
        m.archive_and_ack(config, actual, received, markers, str(uuid.uuid4()))
    assert received == raw and all((state / name).exists() for name in m.MARKERS)



def test_response_baseline_volatile_stopped_requires_fresh_proof_and_distinct_ack(tmp_path, monkeypatch):
    m, config, run, proof, state = response_baseline_refused_fixture(
        tmp_path, monkeypatch, ordinary_current_rest=True, volatile_stopped=True
    )
    before = copy.deepcopy(run)
    raw = publish_incomplete_fixture(config, run, proof, state)
    actual, received, inventory = m.load_qualified_proof(config, run)
    assert actual == proof and received == raw and inventory == dict.fromkeys(m.MARKERS)
    assert not m.acknowledged_receipt(actual, run, str(uuid.uuid4()))
    ack_operation, consumed = str(uuid.uuid4()), str(uuid.uuid4())
    ack = m.archive_and_ack(config, actual, received, inventory, ack_operation)
    assert m.acknowledged_receipt({**actual, "acknowledgment": {**ack, "consumed_operation_id": consumed}}, run, consumed)
    assert run == before and run["uncertain"] is True and run["status"] == "faulted"
    assert run["native_live_at"] is None and run["progress_s"] == 0
    trial = run["physical_result"]["readback"]["readback"]["left_response_p_trial"]
    assert (trial["original_registers"]["Present_Position"], trial["original_registers"]["Goal_Position"],
            trial["original_registers"]["Acceleration"]) == (1142, 0, 0)
    assert not trial["restore_required"] and all(row["operation"] == "read" for row in trial["events"])
    assert all(not (state / name).exists() for name in m.MARKERS)


@pytest.mark.parametrize("broken", [
    "outside_corridor", "outside_calibration", "goal_only", "acceleration_only", "other_goal", "other_acceleration",
    "maximum_acceleration", "p32", "torque", "lock", "status", "current", "frozen", "byte", "digest",
    "write", "missing_event", "obligation", "configured", "source", "host", "boot", "request_hash",
    "calibration", "live", "progress", "fresh_lift", "fresh_thermal", "fresh_nonzero", "proof_age", "proof_boot",
])
def test_response_baseline_volatile_stopped_denies_other_states_and_incomplete_proof(tmp_path, monkeypatch, broken):
    m, config, run, proof, state = response_baseline_refused_fixture(
        tmp_path, monkeypatch, ordinary_current_rest=True, volatile_stopped=True
    )
    trial = run["physical_result"]["readback"]["readback"]["left_response_p_trial"]
    registers, binding = trial["original_registers"], trial["binding"]
    if broken == "outside_corridor": registers["Present_Position"] = 1299
    elif broken == "outside_calibration": registers["Present_Position"] = 3074
    elif broken == "goal_only": registers["Acceleration"] = 254
    elif broken == "acceleration_only": registers["Goal_Position"] = 1142
    elif broken == "other_goal": registers["Goal_Position"] = 1
    elif broken == "other_acceleration": registers["Acceleration"] = 1
    elif broken == "maximum_acceleration": registers["Maximum_Acceleration"] = 0
    elif broken == "p32": registers["P_Coefficient"] = 32
    elif broken == "torque": registers["Torque_Enable"] = 1
    elif broken == "lock": registers["Lock"] = 1
    elif broken == "status": registers["Status"] = 1
    elif broken == "current": registers["Present_Current"] = 1
    elif broken == "frozen": trial["frozen_teleop_registers"]["Acceleration"] = 254
    elif broken == "byte": trial["original_bytes"]["24"] = 32
    elif broken == "digest": trial["original_settings_sha256"] = "a" * 64
    elif broken == "write": trial["events"][-1]["operation"] = "write"
    elif broken == "missing_event": trial["events"].pop()
    elif broken == "obligation": trial["restore_required"] = True
    elif broken == "configured": trial["configured_p_readback"] = 32
    elif broken == "source": binding["request"]["source_head"] = "a" * 40
    elif broken == "host": binding["host_incarnation"] = str(uuid.uuid4())
    elif broken == "boot": binding["request"]["boot_id"] = str(uuid.uuid4())
    elif broken == "request_hash": binding["request_sha256"] = "a" * 64
    elif broken == "calibration": registers["Min_Position_Limit"] = 863
    elif broken == "live": run["native_live_at"] = 525863.0
    elif broken == "progress": run["progress_s"] = .1
    elif broken == "fresh_lift": proof["readback"]["lift_stationary"] = None
    elif broken == "fresh_thermal": proof["readback"]["lift_stationary"]["temperature_slots"][0][0] -= 1
    elif broken == "fresh_nonzero": proof["readback"]["readbacks"][0]["value"] = 1
    elif broken == "proof_age": monkeypatch.setattr(m.time, "monotonic", lambda: proof["completed_at_monotonic_s"] + 181)
    elif broken == "proof_boot": monkeypatch.setattr(m, "current_boot_id", lambda: str(uuid.uuid4()))
    if broken not in {"frozen", "byte", "digest", "write", "missing_event", "obligation", "configured", "source", "host", "boot", "request_hash", "live", "progress", "fresh_lift", "fresh_thermal", "fresh_nonzero", "proof_age", "proof_boot"}:
        trial["frozen_teleop_registers"] = {key: registers[key] for key in trial["frozen_teleop_registers"]}
        for row in trial["events"]:
            row["value"] = registers[row["register"]] if "register" in row else trial["original_bytes"][str(row["address"])]
        trial["original_settings_sha256"] = hashlib.sha256(canonical({"registers": registers, "individual_bytes": trial["original_bytes"]})).hexdigest()
    if broken in {"source", "boot"}: binding["request_sha256"] = hashlib.sha256(canonical(binding["request"])).hexdigest()
    publish_incomplete_fixture(config, run, proof, state)
    before = copy.deepcopy(run)
    with pytest.raises((ValueError, KeyError, TypeError)):
        m.load_qualified_proof(config, run)
    assert run == before and not m._ack_path(state, run["run_id"]).exists()
