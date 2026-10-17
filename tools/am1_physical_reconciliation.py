"""Private stopped-state proof validation; this module never opens a motor device."""

import copy
import hashlib
import json
import math
import os
import socket
import stat
import subprocess
import threading
import time
import uuid
from pathlib import Path

MARKERS = ("cleanup-uncertain.json", "physical-in-progress.json")
CACHE_S = 5.0
INCOMPLETE_PREACTIVATION_ORIGIN = "preactivation-lift-unqualified"
PREHOME_COOL_STALE_ORIGIN = "prehome-cool-thermal-stale-lift-unqualified"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def terminal_run_hash(run):
    return hashlib.sha256(canonical(run)).hexdigest()


def acknowledged_receipt(receipt, run, operation_id):
    """The authority accepts an exact actual proof and a completed durable ACK."""
    if not isinstance(receipt, dict) or not isinstance(run, dict):
        return False
    incarnation = (run.get("physical_result") or {}).get("backend_incarnation")
    ack = receipt.get("acknowledgment")
    return bool(
        run.get("status") in {"stopped", "completed", "faulted", "interrupted"}
        and receipt.get("version") == 1
        and receipt.get("kind") == "am1-actual-stopped-reconciliation"
        and receipt.get("run_id") == run.get("run_id")
        and isinstance(incarnation, str)
        and receipt.get("host_incarnation") == incarnation
        and receipt.get("cleanup") == "physical_readback_verified"
        and receipt.get("uncertain") is False
        and receipt.get("original_terminal_run_sha256") == terminal_run_hash(run)
        and isinstance(ack, dict)
        and ack.get("version") == 1
        and ack.get("run_id") == run.get("run_id")
        and ack.get("host_incarnation") == incarnation
        and ack.get("consumed_operation_id") == operation_id
        and ack.get("validated") is True
        and ack.get("markers_archived") is True
        and isinstance(ack.get("proof_sha256"), str)
        and len(ack["proof_sha256"]) == 64
    )


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def sha(value):
    return hashlib.sha256(value).hexdigest()


def expected_readbacks():
    from examples.alohamini.am1_finite_task import JOINT_KEYS

    rows = {
        ("left" if "_left_" in k else "right", k.removesuffix(".pos"), "Torque_Enable") for k in JOINT_KEYS
    }
    body = ("base_left_wheel", "base_back_wheel", "base_right_wheel", "lift_axis")
    rows.update(
        ("left", motor, register) for motor in body for register in ("Torque_Enable", "Goal_Velocity")
    )
    return rows


def _original_origin_note(chain, prefix):
    notes = []
    for entry in chain:
        values = entry.get("notes")
        require(
            isinstance(values, list) and all(type(value) is str for value in values),
            "complete original prehome error notes required",
        )
        notes.extend(value for value in values if value.startswith(prefix))
    require(len(notes) == 1 and len(notes[0]) <= 16384, "one bounded original prehome note required")
    value = _json(notes[0][len(prefix) :])
    require(isinstance(value, dict), "original prehome note object required")
    return value


def _validate_prehome_cool_stale_origin(run, receipt, chain):
    """Recognize only retained cool, stale pre-home evidence; never qualify the lift."""
    require(
        isinstance(chain, list)
        and len(chain) == 2
        and all(isinstance(entry, dict) for entry in chain)
        and chain[0].get("cause") == "RuntimeError: AlohaMini motor activation failed."
        and chain[1].get("cause") == receipt["first_cause"],
        "original prehome activation wrapper and complete error chain required",
    )
    fault = _original_origin_note(chain, "[AM1 TELEOP FAULT] ")
    rest = _original_origin_note(chain, "Original normal-rest acquisition: ")
    binding = {
        "recipe": "physical-live-teleop",
        "run_id": run["run_id"],
        "host_incarnation": receipt["host_incarnation"],
    }
    require(
        run.get("recipe") == "physical-live-teleop"
        and rest.get("binding") == binding
        and fault.get("binding") == binding
        and rest.get("phase") == "before_home"
        and rest.get("qualified") is False
        and rest.get("samples") == []
        and rest.get("first_cause") == receipt["first_cause"],
        "original same-run before-home refusal without acquisition or homing required",
    )
    side, motor = fault.get("side"), fault.get("offending_motor")
    joints = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")
    names = ["arm_" + str(side) + "_" + joint for joint in joints]
    suffix = "; temperature: five-slot feedback window is stale (>0.5 s)."
    require(
        side in ("left", "right")
        and motor in names
        and fault.get("register") == "Present_Temperature"
        and fault.get("reason") == "actual configured temperature limit: " + motor + suffix
        and receipt["first_cause"] == "RuntimeError: AM1 teleop " + fault["reason"],
        "only exact mapped-arm cool thermal-window stale cause may use this origin",
    )
    comparison = fault.get("comparison") or {}
    thermal = comparison.get("temperature") or {}
    configured = (comparison.get("configured") or {}).get("Max_Temperature_Limit") or {}
    ceiling, raw = configured.get("value"), fault.get("raw")
    require(
        type(ceiling) is int
        and 0 < ceiling <= 100
        and thermal.get("ceiling_c") == ceiling
        and type(thermal.get("ceiling_c")) is int
        and comparison.get("units") == "raw_register"
        and comparison.get("lower_inclusive") == 0
        and comparison.get("upper_exclusive") == ceiling
        and thermal.get("high_count") == 0
        and thermal.get("numeric_outlier_count") == 0
        and thermal.get("first_cause") == suffix.removeprefix("; ")
        and isinstance(raw, dict)
        and set(raw) == set(names)
        and all(type(value) is int and 0 <= value < ceiling for value in raw.values())
        and type(fault.get("offending_value")) is int
        and fault["offending_value"] == raw[motor],
        "original configured cool values without high votes or outliers required",
    )
    started, completed = fault.get("read_started_at_monotonic_s"), fault.get("read_completed_at_monotonic_s")
    slots = thermal.get("samples")
    require(
        number(started)
        and number(completed)
        and 0 <= started <= completed
        and number(rest.get("started_at"))
        and rest["started_at"] <= started
        and completed <= receipt["readback"]["started_at_monotonic_s"]
        and isinstance(slots, list)
        and len(slots) == 5
        and all(
            isinstance(row, list)
            and len(row) == 2
            and number(row[0])
            and 0 <= row[0] <= completed
            and type(row[1]) is int
            and 0 <= row[1] < ceiling
            for row in slots
        ),
        "original timed cool five-slot stale evidence required",
    )
    times = [row[0] for row in slots]
    require(
        all(a < b for a, b in zip(times, times[1:], strict=False))
        and len({(round(at * 1e9) - round(times[0] * 1e9)) // 100_000_000 for at in times}) == 5
        and completed - times[0] > 0.5
        and times[-1] == completed
        and slots[-1][1] == raw[motor],
        "original advancing occupied slots must demonstrate cool stale history",
    )
    group = fault.get("group_reply") or {}
    request, packets = group.get("request") or {}, group.get("packets")
    require(
        group.get("binding") == binding
        and request.get("binding") == binding
        and request.get("side") == side
        and request.get("address") == 63
        and request.get("width") == 1
        and request.get("ids") == [1, 2, 3, 4, 5, 6]
        and request.get("motors") == names
        and request.get("register") == "Present_Temperature"
        and group.get("comm_result") == 0
        and isinstance(packets, list)
        and len(packets) == 6,
        "original exact one-byte six-motor temperature group required",
    )
    for ident, name, record in zip(range(1, 7), names, packets, strict=True):
        require(isinstance(record, dict), "original temperature packet record required")
        packet = record.get("packet")
        require(
            record.get("comm_result") == 0
            and record.get("device_error") == 0
            and isinstance(packet, list)
            and len(packet) == 7
            and all(type(value) is int and 0 <= value <= 255 for value in packet)
            and packet[:5] == [255, 255, ident, 3, 0]
            and packet[5] == raw[name]
            and sum(packet[2:]) & 255 == 255,
            "original checksum-valid cool temperature replies without device errors required",
        )


def validate_cleanup_origin(run, proof, receipt):
    """Qualify an explicit incomplete origin without promoting its old lift proof."""
    origin = proof.get("original_cleanup_origin")
    if origin is None:
        require(
            receipt.get("cleanup") == "physical_cleanup_unknown" and receipt.get("uncertain") is True,
            "literal unchanged original unknown cleanup receipt required",
        )
        return False
    require(
        origin in (INCOMPLETE_PREACTIVATION_ORIGIN, PREHOME_COOL_STALE_ORIGIN),
        "unsupported original cleanup origin",
    )
    lifecycle, rb = run.get("physical_result") or {}, receipt.get("readback") or {}
    require(
        run.get("status") == "faulted"
        and run.get("uncertain") is True
        and "native_live_at" in run
        and run["native_live_at"] is None
        and number(run.get("progress_s"))
        and run["progress_s"] == 0
        and lifecycle.get("phase") == "terminal"
        and lifecycle.get("uncertain") is True
        and lifecycle.get("cleanup") == "physical_cleanup_unknown"
        and "native_live_at" in lifecycle
        and lifecycle["native_live_at"] is None
        and lifecycle.get("readback") == receipt,
        "exact uncertain preactivation terminal origin required",
    )
    require(
        type(receipt.get("version")) is int
        and receipt["version"] == 1
        and receipt.get("run_id") == run.get("run_id")
        and receipt.get("host_incarnation")
        == lifecycle.get("backend_incarnation")
        == proof.get("host_incarnation")
        and receipt.get("first_cause") == run.get("native_first_cause") == proof.get("original_first_cause")
        and isinstance(receipt.get("first_cause"), str)
        and bool(receipt["first_cause"])
        and receipt.get("provenance") == "protected-host/actual-motor-readback"
        and receipt.get("cleanup") == "physical_readback_verified"
        and receipt.get("uncertain") is False
        and receipt.get("cleanup_errors") == []
        and receipt.get("expected_stop") is None
        and rb.get("verified") is True
        and rb.get("buses_closed") is True
        and rb.get("lift_readback") == "unavailable-or-failed"
        and rb.get("cleanup_errors") == []
        and rb.get("selected_gain_restore_required") is False,
        "only incomplete lift proof with otherwise actual known off cleanup may use this origin",
    )
    chain = receipt.get("first_error_chain")
    if origin == PREHOME_COOL_STALE_ORIGIN:
        _validate_prehome_cool_stale_origin(run, receipt, chain)
    else:
        require(
            receipt["first_cause"].startswith("RuntimeError: AM1 arm activation refused: ")
            and isinstance(chain, list)
            and len(chain) == 2
            and chain[0].get("cause") == "RuntimeError: AlohaMini motor activation failed."
            and chain[1].get("cause") == receipt["first_cause"],
            "original preactivation arm-guard cause and complete error chain required",
        )
    started, completed = rb.get("started_at_monotonic_s"), rb.get("completed_at_monotonic_s")
    rows = rb.get("readbacks")
    require(
        number(started)
        and number(completed)
        and 0 <= started <= completed
        and isinstance(rows, list)
        and len(rows) == 20
        and {(r.get("bus"), r.get("motor"), r.get("register")) for r in rows} == expected_readbacks(),
        "original twenty distinct timed zero/off readbacks required",
    )
    for row in rows:
        _timed_row(row, started, completed)
    require(
        proof.get("original_markers") == dict.fromkeys(MARKERS),
        "incomplete preactivation origin requires both original markers absent",
    )
    return True


def _timed_row(row, started, completed):
    a, b = row.get("read_started_at_monotonic_s"), row.get("read_completed_at_monotonic_s")
    require(
        number(a) and number(b) and started <= a <= b <= completed and b - a <= 0.04,
        "actual stopped register original 40 ms read times required",
    )
    require(
        type(row.get("value")) is int and row["value"] == 0 and not row.get("error"),
        "actual zero register readback required",
    )


def _raw_sample(record):
    trace = record.get("group_trace") or {}
    request, reply = trace.get("request_bytes"), trace.get("response_bytes")
    require(
        request == [255, 255, 254, 5, 130, 33, 38, 11, 40], "exact sole ID11 grouped read request required"
    )
    require(
        isinstance(reply, list)
        and len(reply) == 44
        and all(type(v) is int and 0 <= v <= 255 for v in reply)
        and reply[:5] == [255, 255, 11, 40, 0]
        and sum(reply[2:]) & 255 == 255
        and trace.get("comm_result") == 0
        and trace.get("sdk_servo_error") == 0,
        "complete checksum-valid error-free ID11 grouped response required",
    )
    payload = reply[5:-1]

    def word(offset):
        return payload[offset] | payload[offset + 1] << 8

    def signed(value):
        return -(value & 32767) if value & 32768 else value

    decoded = {
        "operating_mode": payload[0],
        "torque_enable": payload[7],
        "goal_velocity_raw": signed(word(13)),
        "present_position_raw": signed(word(23)),
        "present_velocity_raw": signed(word(25)),
        "voltage_raw": payload[29],
        "temperature_c": payload[30],
        "status": payload[32],
        "moving": payload[33],
        "present_current_raw": word(36),
        "present_current_ma": word(36) * 6.5,
    }
    require(
        all(type(record.get(k)) is type(v) and record[k] == v for k, v in decoded.items()),
        "record fields must match original acquired packet bytes",
    )
    return decoded


def validate_stopped_proof(config, run, proof, *, now, boot_id=None, durable_ack=False):
    """Check actual recorded evidence without opening, configuring or commanding devices."""
    require(isinstance(proof, dict) and isinstance(run, dict), "actual stopped proof required")
    canonical(proof)
    incarnation = (run.get("physical_result") or {}).get("backend_incarnation")
    require(
        proof.get("version") == 1
        and proof.get("kind") == "am1-actual-stopped-reconciliation"
        and proof.get("run_id") == run.get("run_id")
        and proof.get("host_incarnation") == incarnation,
        "exact original physical run/incarnation required",
    )
    require(
        run.get("status") in {"stopped", "completed", "faulted", "interrupted"}
        and run.get("uncertain") is True
        and proof.get("original_terminal_run_sha256") == terminal_run_hash(run),
        "unchanged uncertain terminal run required",
    )
    uuid.UUID(proof["run_id"])
    uuid.UUID(proof["host_incarnation"])
    uuid.UUID(proof["reconciliation_id"])
    require(
        proof.get("cleanup") == "physical_readback_verified"
        and proof.get("uncertain") is False
        and proof.get("no_actuation") is True,
        "read-only actual qualified stopped proof required",
    )
    require(
        proof.get("original_first_cause") == run.get("native_first_cause"),
        "original native first cause must remain intact",
    )
    require(
        (proof.get("source") or {}).get("backend_contract") == config,
        "exact protected backend source/config binding required",
    )
    started, completed = proof.get("started_at_monotonic_s"), proof.get("completed_at_monotonic_s")
    require(
        number(started)
        and number(completed)
        and started <= completed <= now
        and (durable_ack or now - completed <= 180.0),
        "fresh original native proof clock required",
    )
    require(
        proof.get("proof_max_age_s") == 180.0
        and proof.get("boot_id") == (current_boot_id() if boot_id is None else boot_id),
        "fresh stopped evidence must belong to the current native boot",
    )
    admission = proof.get("admission") or {}
    require(
        admission.get("both_locks_held") is True
        and admission.get("canonical_path") == config["physical_state_directory"].rstrip("/") + "/active.lock"
        and admission.get("serial_path")
        == config["physical_state_directory"].rstrip("/") + "/serial-owner.lock",
        "both original canonical locks required",
    )
    for key in ("owner_audit_before", "owner_audit_after"):
        audit = proof.get(key) or {}
        require(
            type(audit.get("host_unit_main_pid")) is int
            and audit["host_unit_main_pid"] == 0
            and type(audit.get("camera_unit_main_pid")) is int
            and audit["camera_unit_main_pid"] == 0
            and audit.get("bus_owner_pids") == []
            and audit.get("host_process_pids") == [],
            "actual no-owner audit required",
        )
    readback = proof.get("readback") or {}
    require(
        readback.get("verified") is True
        and readback.get("buses_closed") is True
        and readback.get("lift_readback") == "qualified"
        and readback.get("selected_gain_restore_required") is False
        and not readback.get("cleanup_errors"),
        "all physical buses closed and actual cleanup qualified required",
    )
    rows = readback.get("readbacks")
    require(
        isinstance(rows, list)
        and len(rows) == 20
        and {(r.get("bus"), r.get("motor"), r.get("register")) for r in rows} == expected_readbacks(),
        "all twenty distinct actual zero/off readbacks required",
    )
    for row in rows:
        _timed_row(row, started, completed)
    require(
        type(readback.get("gain_i_readback")) is int and readback["gain_i_readback"] == 0,
        "selected shoulder I0 restoration required",
    )
    gain = readback.get("gain_i_evidence") or {}
    require(
        (gain.get("bus"), gain.get("motor"), gain.get("register"))
        == ("left", "arm_left_shoulder_lift", "I_Coefficient"),
        "actual selected shoulder gain read required",
    )
    _timed_row(gain, started, completed)
    lift = readback.get("lift_stationary") or {}
    samples = lift.get("samples")
    entry = lift.get("qualification_started_at_monotonic_s")
    qualified = lift.get("qualified_at_monotonic_s")
    require(
        isinstance(samples, list)
        and len(samples) >= 5
        and number(entry)
        and number(qualified)
        and started <= entry <= qualified <= completed
        and qualified - entry <= 1.0,
        "original one-second lift qualification required",
    )
    cfg = lift.get("configuration") or {}
    require(
        cfg.get("id") == 11
        and cfg.get("model_number") == 777
        and cfg.get("operating_mode") == 1
        and cfg.get("torque_enable") == 0
        and cfg.get("goal_velocity_raw") == 0,
        "actual original configured ID11 mode/off/zero identity required",
    )
    low, high = cfg.get("min_voltage_limit_raw"), cfg.get("max_voltage_limit_raw")
    require(
        type(low) is int and type(high) is int and 0 < low <= high <= 255,
        "actual configured voltage limits required",
    )
    times = []
    for record in samples:
        stamp, duration = record.get("sample_monotonic_s"), record.get("request_duration_s")
        require(
            number(stamp)
            and number(duration)
            and 0 <= duration <= 0.04
            and entry <= stamp <= qualified
            and (not times or 0 < stamp - times[-1] <= 0.09),
            "advancing original 40 ms grouped acquisition required",
        )
        times.append(stamp)
        values = _raw_sample(record)
        require(
            values["operating_mode"] == 1
            and values["torque_enable"] == 0
            and values["goal_velocity_raw"] == 0
            and values["status"] == 0
            and values["present_current_ma"] < 2000
            and low <= values["voltage_raw"] <= high
            and values["moving"] in (0, 1),
            "actual stopped lift hard guards failed",
        )
    qualification = lift.get("qualification") or {}
    count = qualification.get("sample_count")
    require(type(count) is int and 5 <= count <= len(samples), "five actual stationary samples required")
    candidate = samples[-count:]
    window = candidate[-1]["sample_monotonic_s"] - candidate[0]["sample_monotonic_s"]
    require(
        window >= 0.2 - 1e-9
        and qualified - times[-1] <= 0.05
        and qualification.get("expected_torque") == 0
        and qualification.get("expected_goal_velocity_raw") == 0,
        "actual stationary 200 ms window at off/zero required",
    )
    origin = candidate[0]["present_position_raw"]
    offsets = [(r["present_position_raw"] - origin + 2048) % 4096 - 2048 for r in candidate]
    require(
        max(offsets) - min(offsets) <= 1
        and abs(offsets[-1]) <= 1
        and all(abs(r["present_velocity_raw"]) <= 50 for r in candidate),
        "stationary encoder/velocity bounds failed",
    )
    for index in range(2, len(candidate)):
        recent = candidate[index - 2 : index + 1]
        require(
            not (
                all(r["present_velocity_raw"] > 5 for r in recent)
                or all(r["present_velocity_raw"] < -5 for r in recent)
                or all(r["moving"] == 1 and abs(r["present_velocity_raw"]) <= 5 for r in recent)
            ),
            "persistent actual lift motion cannot reconcile",
        )
    require(
        qualification.get("window_s") == round(window, 3)
        and qualification.get("position_excursion_raw") == max(offsets) - min(offsets)
        and qualification.get("position_drift_raw") == offsets[-1],
        "stationary summary must match original acquisition",
    )
    slots = lift.get("temperature_slots")
    require(
        lift.get("temperature_ready") is True
        and lift.get("temperature_max_age_s") == 0.5
        and isinstance(slots, list)
        and len(slots) == 5,
        "five fresh actual thermal time slots required",
    )
    origin_ns = round(times[0] * 1e9)
    by_slot = {}
    for record in samples:
        stamp, temp = record["sample_monotonic_s"], record["temperature_c"]
        slot = (round(stamp * 1e9) - origin_ns) // 100_000_000
        if slot not in by_slot or temp > by_slot[slot][1]:
            by_slot[slot] = [stamp, temp]
    expected = list(by_slot.values())[-5:]
    require(
        slots == expected
        and len(expected) == 5
        and all(0 <= qualified - at <= 0.5 for at, _ in slots)
        and sum(temp >= 55 for _, temp in slots) < 3,
        "actual thermal peak slot provenance/freshness failed",
    )
    camera = proof.get("camera_release") or {}
    require(
        camera.get("state") == "released"
        and camera.get("session_id") == run["run_id"]
        and not camera.get("errors")
        and all(
            (camera.get("release_evidence") or {}).get(k) is True
            for k in ("backend_exited", "readers_joined", "viewer_socket_closed", "runtime_config_removed")
        ),
        "actual same-run camera release required",
    )
    return proof


def _private_directory(path):
    path = Path(path)
    require(path.is_absolute(), "literal absolute private directory required")
    for parent in (path, *path.parents):
        info = parent.lstat()
        require(
            not stat.S_ISLNK(info.st_mode) and stat.S_ISDIR(info.st_mode), "private path redirect refused"
        )
    info = path.lstat()
    require(
        info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700,
        "owned mode 0700 directory required",
    )


def _owned_bytes(path, mode):
    path = Path(path)
    for parent in path.parents:
        require(not parent.is_symlink(), "private file ancestor redirect refused")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(descriptor)
        require(
            stat.S_ISREG(info.st_mode)
            and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == mode
            and info.st_nlink == 1
            and info.st_size <= 1_048_576,
            "owned single-link preserved-mode bounded regular file required",
        )
        data = os.read(descriptor, 1_048_577)
        require(len(data) == info.st_size, "complete bounded private file read required")
        return data
    finally:
        os.close(descriptor)


def _private_bytes(path):
    return _owned_bytes(path, 0o600)


def _calibration_bytes(path):
    # Existing AM1 calibration is deliberately preserved at 0664; never chmod it.
    return _owned_bytes(path, 0o664)


def current_boot_id():
    value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    require(str(uuid.UUID(value)) == value, "actual canonical native boot UUID required")
    return value


def _json(data):
    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result, "duplicate private JSON key refused")
            result[key] = value
        return result

    def invalid(value):
        raise ValueError("nonfinite private JSON value: " + value)

    return json.loads(data, object_pairs_hook=pairs, parse_constant=invalid)


def _fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_new_or_same(path, data):
    if path.exists() or path.is_symlink():
        require(_private_bytes(path) == data, "existing reconciliation evidence differs")
        return
    temporary = path.parent / (".reconciliation-publish-" + str(uuid.uuid4()) + ".tmp")
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            _publish_no_replace(temporary, path)
        except FileExistsError:
            require(_private_bytes(path) == data, "existing reconciliation evidence differs")
        _fsync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def _publish_no_replace(temporary, path):
    if os.name == "nt":
        os.rename(temporary, path)  # Windows rename refuses an existing destination.
        return
    # Native Linux atomic no-replace publication avoids both partial final files
    # and the crash-created extra hard link of link()+unlink() publication.
    import ctypes

    library = ctypes.CDLL(None, use_errno=True)
    rename = library.renameat2
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(temporary), -100, os.fsencode(path), 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(path))


class StoppedGuard:
    def __init__(self, config):
        require(os.name == "posix", "actual reconciliation requires native Pi flock")
        import fcntl

        self.descriptors = []
        state = Path(config["physical_state_directory"])
        require(
            state == Path.home() / ".local/state/am1-session",
            "canonical physical admission namespace required",
        )
        _private_directory(state)
        try:
            for name in ("active.lock", "serial-owner.lock"):
                descriptor = os.open(state / name, os.O_RDWR | os.O_NOFOLLOW)
                self.descriptors.append(descriptor)
                info = os.fstat(descriptor)
                require(
                    stat.S_ISREG(info.st_mode)
                    and info.st_uid == os.getuid()
                    and stat.S_IMODE(info.st_mode) == 0o600,
                    "owned canonical mode 0600 lock required",
                )
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            assert_no_owner(config)
        except BaseException:
            self.close()
            raise

    def close(self):
        for descriptor in reversed(self.descriptors):
            os.close(descriptor)
        self.descriptors = []


def acquire_stopped_guard(config):
    return StoppedGuard(config)


def assert_no_owner(config, run_id=None):
    result = subprocess.run(
        ["pgrep", "-f", "[l]erobot.robots.alohamini.alohamini_host"],
        capture_output=True,
        text=True,
        timeout=2,
    )
    require(
        result.returncode == 1 and not result.stdout.strip() and not result.stderr.strip(),
        "physical host process is still active or owner audit failed",
    )
    # The resident unit keeps PrivateDevices=yes. Current bus admission is proved
    # by BOTH held shared locks and the immutable native producer's actual audit;
    # nonexistent /dev nodes inside this namespace are never owner evidence.
    if run_id is not None:
        uuid.UUID(run_id)
        for component in ("host", "camera"):
            unit = "am1-pa01-" + component + "-" + run_id
            result = subprocess.run(
                [
                    "systemctl",
                    "--user",
                    "show",
                    unit,
                    "--no-pager",
                    "-p",
                    "MainPID",
                    "-p",
                    "ControlPID",
                    "-p",
                    "Job",
                    "-p",
                    "ActiveState",
                ],
                capture_output=True,
                text=True,
                timeout=2,
            )
            require(result.returncode == 0 and not result.stderr.strip(), "owned unit inventory unavailable")
            row = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
            require(
                row.get("MainPID") == "0"
                and row.get("ControlPID") == "0"
                and row.get("Job") in ("", "0")
                and row.get("ActiveState") in ("inactive", "failed"),
                "owned physical/camera unit must be stopped and jobless",
            )


def _validate_sources(config, proof):
    require(socket.gethostname() == config["machine"], "actual AM1 machine required")
    for name in ("host", "camera"):
        repository = config[name + "_repository"]
        head = subprocess.check_output(
            ["git", "--no-optional-locks", "-C", repository, "rev-parse", "HEAD"], text=True, timeout=5
        ).strip()
        dirty = subprocess.check_output(
            ["git", "--no-optional-locks", "-C", repository, "status", "--porcelain"], text=True, timeout=5
        ).strip()
        require(not dirty and head == config[name + "_head"], "exact clean component source pin required")
    repository = str(Path(__file__).resolve().parent.parent)
    head = subprocess.check_output(
        ["git", "--no-optional-locks", "-C", repository, "rev-parse", "HEAD"], text=True, timeout=5
    ).strip()
    dirty = subprocess.check_output(
        ["git", "--no-optional-locks", "-C", repository, "status", "--porcelain"], text=True, timeout=5
    ).strip()
    require(
        not dirty and head == proof["source"].get("executor_head"),
        "actual clean reconciliation executor source pin required",
    )
    for path, digest in config["calibration_files"].items():
        data = _calibration_bytes(path)
        require(sha(data) == digest, "preserved actual calibration changed")
        validate_calibration_evidence(proof, _json(data))
    require(
        sha(_private_bytes(config["source_pins"])) == proof["source"]["manifest_sha256"],
        "literal deployment manifest binding required",
    )


def validate_calibration_evidence(proof, cached):
    motors = {(side, name) for side, name, reg in expected_readbacks() if reg == "Torque_Enable"}
    require(
        isinstance(cached, dict) and set(cached) == {name for _, name in motors},
        "complete preserved motor calibration map required",
    )
    wanted = {}
    for side, name in motors:
        entry = cached[name]
        for register, value in {
            "ID": entry["id"],
            "Model_Number": 777,
            "Homing_Offset": entry["homing_offset"],
            "Min_Position_Limit": entry["range_min"],
            "Max_Position_Limit": entry["range_max"],
        }.items():
            wanted[side, name, register] = value
    rows = (proof.get("readback") or {}).get("calibration_comparison")
    require(
        isinstance(rows, list)
        and len(rows) == 80
        and {(r.get("bus"), r.get("motor"), r.get("register")) for r in rows} == set(wanted),
        "all eighty actual configured motor identity/calibration reads required",
    )
    for row in rows:
        value = wanted[row["bus"], row["motor"], row["register"]]
        require(
            type(row.get("value")) is int
            and type(row.get("cached_expected")) is int
            and row["value"] == value == row["cached_expected"]
            and row.get("matches") is True
            and not row.get("error"),
            "actual hardware calibration must match unchanged cache",
        )
        a, b = row.get("read_started_at_monotonic_s"), row.get("read_completed_at_monotonic_s")
        require(
            number(a)
            and number(b)
            and proof["started_at_monotonic_s"] <= a <= b <= proof["completed_at_monotonic_s"]
            and b - a <= 0.04,
            "original bounded calibration read times required",
        )


def _ack_path(state, run_id):
    return state / ("reconciliation-ack-" + run_id + ".json")


def load_qualified_proof(config, run):
    run_id = str(uuid.UUID(run["run_id"]))
    require(run_id == run["run_id"], "canonical literal run UUID required")
    state = Path(config["physical_state_directory"])
    raw = _private_bytes(state / ("reconciliation-" + run_id + ".json"))
    proof = _json(raw)
    old_ack = None
    if _ack_path(state, run_id).exists():
        old_ack = _json(_private_bytes(_ack_path(state, run_id)))
        require(
            type(old_ack.get("version")) is int
            and old_ack["version"] == 1
            and old_ack.get("run_id") == run_id
            and old_ack.get("host_incarnation") == proof.get("host_incarnation")
            and old_ack.get("proof_sha256") == sha(raw)
            and old_ack.get("validated") is True
            and old_ack.get("markers_archived") is True
            and old_ack.get("original_terminal_run_sha256") == terminal_run_hash(run),
            "existing durable reconciliation ACK mismatch",
        )
        uuid.UUID(old_ack["operation_id"])
    proof = validate_stopped_proof(config, run, proof, now=time.monotonic(), durable_ack=old_ack is not None)
    _validate_sources(config, proof)
    original = _private_bytes(Path(config["run_root"]) / run_id[:8] / "cleanup.json")
    old = _json(original)
    require(
        sha(original) == proof.get("original_cleanup_sha256")
        and old == (run.get("physical_result") or {}).get("readback")
        and old.get("run_id") == run_id
        and old.get("host_incarnation") == proof["host_incarnation"]
        and old.get("first_cause") == proof["original_first_cause"],
        "literal unchanged original unknown cleanup receipt required",
    )
    incomplete_origin = validate_cleanup_origin(run, proof, old)
    camera_raw = _private_bytes(Path(config["camera_state"]) / ("release-" + run_id + ".json"))
    require(
        sha(camera_raw) == proof.get("camera_release_sha256")
        and _json(camera_raw) == proof["camera_release"]
        and proof["camera_release"] == (run.get("physical_result") or {}).get("camera_release"),
        "literal genuine same-run camera release required",
    )
    markers = proof.get("original_markers")
    require(
        isinstance(markers, dict)
        and set(markers) == set(MARKERS)
        and (incomplete_origin or markers[MARKERS[0]] is not None),
        "exact original unknown marker inventory required",
    )
    originals = {}
    for name, binding in markers.items():
        path = state / name
        if binding is None:
            require(not path.exists() and not path.is_symlink(), "foreign physical marker appeared")
            originals[name] = None
            continue
        require(
            isinstance(binding, dict) and set(binding) == {"sha256"}, "literal original marker hash required"
        )
        if path.exists() or path.is_symlink():
            data = _private_bytes(path)
        else:
            require(old_ack is not None, "missing original marker without durable actual ACK")
            archive = state / "reconciliation-archives" / run_id / name
            _private_directory(archive.parent)
            data = _private_bytes(archive)
        require(sha(data) == binding["sha256"], "original physical marker changed")
        marker = _json(data)
        require(
            marker.get("run_id") == run_id and marker.get("host_incarnation") == proof["host_incarnation"],
            "foreign original physical marker",
        )
        if name == MARKERS[0]:
            require(data == original, "original unknown latch must match immutable receipt bytes")
        originals[name] = data
    assert_no_owner(config, run_id)
    return proof, raw, originals


def archive_and_ack(config, proof, raw, originals, operation_id):
    """Called only by the guarded worker after an explicit distinct ACK request."""
    state = Path(config["physical_state_directory"])
    assert_no_owner(config, proof["run_id"])
    require(
        _private_bytes(state / ("reconciliation-" + proof["run_id"] + ".json")) == raw,
        "immutable proof changed before ACK",
    )
    acknowledgment_path = _ack_path(state, proof["run_id"])
    require(current_boot_id() == proof["boot_id"], "stopped proof boot changed before ACK")
    if not acknowledgment_path.exists():
        require(
            time.monotonic() - proof["completed_at_monotonic_s"] <= 180.0,
            "stopped proof expired before new ACK",
        )
    archive = state / "reconciliation-archives" / proof["run_id"]
    for directory in (archive.parent, archive):
        directory.mkdir(mode=0o700, exist_ok=True)
        _private_directory(directory)
        _fsync_directory(directory.parent)
    # Check every marker before archiving or clearing any one of them.
    for name, data in originals.items():
        path = state / name
        if data is None:
            require(
                not path.exists() and not path.is_symlink(), "foreign physical marker appeared before ACK"
            )
        elif path.exists() or path.is_symlink():
            require(_private_bytes(path) == data, "original physical marker changed before ACK")
        else:
            require(
                _private_bytes(archive / name) == data and _ack_path(state, proof["run_id"]).exists(),
                "missing original marker without archived ACK",
            )
    for name, data in originals.items():
        if data is not None:
            _write_new_or_same(archive / name, data)
    _fsync_directory(archive)
    ack = {
        "version": 1,
        "run_id": proof["run_id"],
        "host_incarnation": proof["host_incarnation"],
        "operation_id": operation_id,
        "proof_sha256": sha(raw),
        "validated": True,
        "markers_archived": True,
        "original_terminal_run_sha256": proof["original_terminal_run_sha256"],
        "completed_at_monotonic_s": time.monotonic(),
    }
    if acknowledgment_path.exists():
        persisted = _json(_private_bytes(acknowledgment_path))
        require(
            all(
                persisted.get(k) == ack[k]
                for k in (
                    "version",
                    "run_id",
                    "host_incarnation",
                    "proof_sha256",
                    "validated",
                    "markers_archived",
                    "original_terminal_run_sha256",
                )
            ),
            "durable ACK binding differs",
        )
        ack = persisted
        _fsync_directory(acknowledgment_path.parent)
    else:
        require(
            time.monotonic() - proof["completed_at_monotonic_s"] <= 180.0,
            "stopped proof expired before durable new ACK",
        )
        _write_new_or_same(acknowledgment_path, canonical(ack) + b"\n")
    # The actual qualified proof, archive and explicit ACK are all durable first.
    for name, data in originals.items():
        if data is not None and (state / name).exists():
            require(_private_bytes(state / name) == data, "marker changed during guarded ACK")
            (state / name).unlink()
    if proof.get("original_cleanup_origin") in (INCOMPLETE_PREACTIVATION_ORIGIN, PREHOME_COOL_STALE_ORIGIN):
        require(
            originals == dict.fromkeys(MARKERS)
            and all(not (state / name).exists() and not (state / name).is_symlink() for name in MARKERS),
            "both incomplete-origin markers must remain absent through durable ACK",
        )
    _fsync_directory(state)
    return ack


class ReconciliationWorker:
    """Five-second guarded cache; filesystem work never holds authority/executor mutexes."""

    def __init__(self, config, run, operation_id, *, clock=time.monotonic):
        self.config, self.run = copy.deepcopy(config), copy.deepcopy(run)
        self.initial_operation_id = operation_id
        self.clock = clock
        self.mutex = threading.Lock()
        self.wake = threading.Event()
        self.state = "validating"
        self.error = None
        self.closed = False
        self.ack_operation_id = None
        self.result = None
        self.deadline = None
        self.thread = threading.Thread(target=self._run, name="am1-stopped-reconciliation", daemon=False)
        self.thread.start()

    def request(self, operation_id):
        uuid.UUID(operation_id)
        with self.mutex:
            if self.closed or self.deadline is not None and self.clock() >= self.deadline:
                self.wake.set()
                return False
            if self.state == "qualified" and operation_id != self.initial_operation_id:
                self.ack_operation_id = operation_id
                self.state = "ack-queued"
                self.wake.set()
            elif self.state == "acknowledged" and operation_id not in (
                self.initial_operation_id,
                self.ack_operation_id,
            ):
                result = copy.deepcopy(self.result)
                result["acknowledgment"]["consumed_operation_id"] = operation_id
                result["acknowledgment"]["validation_basis"] = (
                    "durable-ack-resume"
                    if self.clock() - result["completed_at_monotonic_s"] > 180.0
                    else "fresh-native-proof"
                )
                self.state = "consumed"
                self.wake.set()
                return result
        return False

    def close(self):
        with self.mutex:
            self.closed = True
        self.wake.set()

    def _run(self):
        guard = None
        try:
            guard = acquire_stopped_guard(self.config)
            proof, raw, originals = load_qualified_proof(self.config, self.run)
            with self.mutex:
                self.deadline = self.clock() + CACHE_S
                self.state = "qualified"
            while True:
                with self.mutex:
                    if self.closed or self.state == "consumed":
                        break
                    if self.clock() >= self.deadline:
                        self.state = "expired"
                        break
                    ack_operation = self.ack_operation_id if self.state == "ack-queued" else None
                    remaining = self.deadline - self.clock()
                if ack_operation:
                    ack = archive_and_ack(self.config, proof, raw, originals, ack_operation)
                    with self.mutex:
                        self.result = {**copy.deepcopy(proof), "acknowledgment": ack}
                        self.deadline = self.clock() + CACHE_S
                        self.state = "acknowledged"
                self.wake.wait(min(0.1, remaining))
                self.wake.clear()
        except Exception as error:
            with self.mutex:
                self.error = self.error or f"{type(error).__name__}: {error}"
                self.state = "error"
        finally:
            if guard is not None:
                guard.close()
