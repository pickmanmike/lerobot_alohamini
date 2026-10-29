"""Private stopped-state proof validation; this module never opens a motor device."""

import ast
import copy
import hashlib
import json
import math
import os
import re
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
PRECONFIGURE_COOL_STALE_ORIGIN = "preconfigure-cool-thermal-stale-lift-unqualified"
FOLLOWER_BASELINE_EXPIRED_ORIGIN = "preconfigure-follower-baseline-expired-lift-unqualified"
PREIMPORT_STOP_ORIGIN = "preimport-stop-missing-host-receipt"
RESPONSE_BASELINE_REFUSED_ORIGIN = "preconfigure-left-response-baseline-refused-lift-unqualified"
RESPONSE_GOAL_RESTORE_ORIGIN = "preconfigure-left-response-goal-restore-incomplete-lift-unqualified"
RESPONSE_GOAL_RUN = "72de22d9-ee93-4f76-809a-f10bd88e512a"


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


def _validate_prehome_cool_stale_origin(run, receipt, chain, *, configure=False):
    """Recognize only retained cool, stale pre-home evidence; never qualify the lift."""
    if configure:
        require(
            isinstance(chain, list) and len(chain) == 1
            and isinstance(chain[0], dict) and set(chain[0]) == {"cause", "notes"}
            and chain[0]["cause"] == receipt["first_cause"]
            and isinstance(chain[0]["notes"], list) and len(chain[0]["notes"]) == 1,
            "original direct configure fault and its single structured note required",
        )
    else:
        require(
            isinstance(chain, list)
            and len(chain) == 2
            and all(isinstance(entry, dict) for entry in chain)
            and chain[0].get("cause") == "RuntimeError: AlohaMini motor activation failed."
            and chain[1].get("cause") == receipt["first_cause"],
            "original prehome activation wrapper and complete error chain required",
        )
    fault = _original_origin_note(chain, "[AM1 TELEOP FAULT] ")
    rest = None if configure else _original_origin_note(chain, "Original normal-rest acquisition: ")
    binding = {
        "recipe": "physical-live-teleop",
        "run_id": run["run_id"],
        "host_incarnation": receipt["host_incarnation"],
    }
    require(
        run.get("recipe") == "physical-live-teleop"
        and fault.get("binding") == binding
        and (configure or (
            rest.get("binding") == binding and rest.get("phase") == "before_home"
            and rest.get("qualified") is False and rest.get("samples") == []
            and rest.get("first_cause") == receipt["first_cause"])),
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
        and (configure or (number(rest.get("started_at")) and rest["started_at"] <= started))
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
    if configure:
        # Original maxima may move within occupied slots; their first retained
        # timestamp is not the original anchor. Only check consistency with ONE
        # fixed phase. This old stale diagnosis grants no thermal readiness:
        # fresh stopped proof below still replays its own exact origin/maxima.
        peaks_ns = [round(at * 1_000_000_000) for at in times]
        phases_ns = {0, *(at % 100_000_000 for at in peaks_ns)}
        occupied = all(a < b for a, b in zip(peaks_ns, peaks_ns[1:], strict=False)) and any(
            len({(at - phase) // 100_000_000 for at in peaks_ns}) == 5 for phase in phases_ns
        )
    else:
        occupied = len({(round(at * 1e9) - round(times[0] * 1e9)) // 100_000_000 for at in times}) == 5
    require(
        all(a < b for a, b in zip(times, times[1:], strict=False))
        and occupied
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
    if configure:
        require(
            set(fault) == {"reason", "side", "register", "raw", "read_started_at_monotonic_s",
                "read_completed_at_monotonic_s", "binding", "offending_motor", "offending_value",
                "comparison", "prior_successful_health", "group_reply"}
            and fault["prior_successful_health"] is None,
            "configure origin requires original first health failure without later rest/home evidence",
        )
        configured_started, configured_completed = (
            configured.get(key) for key in ("read_started_at_monotonic_s", "read_completed_at_monotonic_s")
        )
        group_started, group_completed = (
            group.get(key) for key in ("group_call_started_at_monotonic_s", "group_call_completed_at_monotonic_s")
        )
        transmit = group.get("transmit") or {}
        tx_started, tx_completed = (
            transmit.get(key) for key in ("write_started_at_monotonic_s", "write_completed_at_monotonic_s")
        )
        require(
            all(number(value) for value in (configured_started, configured_completed,
                group_started, group_completed, tx_started, tx_completed))
            and 0 <= configured_started <= configured_completed <= times[0]
            and started <= group_started <= tx_started <= tx_completed <= group_completed <= completed
            and transmit.get("packet") == [255, 255, 254, 10, 130, 63, 1, 1, 2, 3, 4, 5, 6, 32]
            and type(transmit.get("result")) is int and transmit["result"] == 14,
            "original configure ceiling acquisition and exact temperature read request chronology required",
        )
    for ident, name, record in zip(range(1, 7), names, packets, strict=True):
        require(isinstance(record, dict), "original temperature packet record required")
        if configure:
            packet_started, packet_completed = (
                record.get(key) for key in ("read_started_at_monotonic_s", "read_completed_at_monotonic_s")
            )
            require(number(packet_started) and number(packet_completed)
                and group_started <= packet_started <= packet_completed <= group_completed,
                "original configure temperature packet acquisition chronology required")
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


def _response_origin_settings():
    expected = {
            "Model_Number": 777, "Firmware_Major_Version": 3, "Firmware_Minor_Version": 10,
            "Operating_Mode": 0, "Torque_Enable": 0, "Lock": 0, "P_Coefficient": 16,
            "I_Coefficient": 0, "D_Coefficient": 32, "Min_Position_Limit": 864,
            "Max_Position_Limit": 3073, "ID": 3, "Homing_Offset": 1845, "Phase": 12,
            "Max_Torque_Limit": 1000, "Torque_Limit": 1000, "CW_Dead_Zone": 1,
            "CCW_Dead_Zone": 1, "Acceleration": 254, "Maximum_Acceleration": 254,
            "Goal_Time": 0, "Goal_Velocity": 0, "Protection_Current": 310,
            "Max_Temperature_Limit": 70, "Min_Voltage_Limit": 40, "Max_Voltage_Limit": 140,
            "Protective_Torque": 20, "Protection_Time": 200, "Overload_Torque": 80,
            "Over_Current_Protection_Time": 200, "Velocity_closed_loop_P_proportional_coefficient": 10,
            "Velocity_closed_loop_I_integral_coefficient": 200, "Present_Current": 0, "Status": 0,
        }
    frozen_keys = {
            "ID", "Model_Number", "Firmware_Major_Version", "Firmware_Minor_Version",
            "Min_Position_Limit", "Max_Position_Limit", "Homing_Offset", "Phase", "P_Coefficient",
            "I_Coefficient", "D_Coefficient", "Acceleration", "Maximum_Acceleration", "Protection_Current",
            "Max_Temperature_Limit", "Min_Voltage_Limit", "Max_Voltage_Limit", "Operating_Mode",
        }
    return expected, frozen_keys


def _validate_response_baseline_refused_origin(run, proof, receipt, chain):
    """This old refusal grants no readiness; the independent fresh stopped proof does."""
    cause = "RuntimeError: Selected elbow response P actual stopped baseline differs from its reviewed original"
    require(
        chain == [{"cause": cause, "notes": []}] and receipt["first_cause"] == cause,
        "exact direct selected response baseline refusal required",
    )
    trial = receipt["readback"].get("left_response_p_trial")
    fields = {
        "schema_version", "kind", "binding", "motor", "motor_id", "original_registers",
        "original_bytes", "frozen_teleop_registers", "prepared_at_monotonic_s",
        "original_settings_sha256", "events", "restore_required", "state", "status",
    }
    require(
        isinstance(trial, dict) and set(trial) == fields
        and type(trial["schema_version"]) is int and trial["schema_version"] == 1
        and trial["kind"] == "left-response-p" and trial["motor"] == "arm_left_elbow_flex"
        and type(trial["motor_id"]) is int and trial["motor_id"] == 3
        and trial["restore_required"] is False
        and trial["state"] == trial["status"] == "baseline_refused",
        "original left baseline-refused snapshot without effects or obligation required",
    )
    binding = trial["binding"]
    require(
        isinstance(binding, dict)
        and set(binding) == {"version", "kind", "run_id", "host_incarnation", "request_sha256", "request"}
        and type(binding["version"]) is int and binding["version"] == 1
        and binding["kind"] == trial["kind"] and binding["run_id"] == run["run_id"]
        and binding["host_incarnation"] == proof["host_incarnation"],
        "original same-run response binding required",
    )
    request = binding["request"]
    source = proof.get("source") or {}
    backend = source.get("backend_contract") or {}
    current_rest = isinstance(request, dict) and request.get("workload_segment") == "ordinary-arms"
    request_fields = {"version", "kind", "request_id", "source_head", "proportional",
                      "workload_segment", "boot_id", "expires_at_monotonic_s"}
    original_motor = "927c17aa52bdf8da3e0ab53d7a122bbb3749b816"
    if current_rest:
        request_fields |= {"workspace_profile", "left_shoulder_startup_degrees"}
        original_motor = source.get("motor_head")
        require(
            type(original_motor) is str and re.fullmatch("[0-9a-f]{40}", original_motor) is not None
            and request.get("workspace_profile") == "ordinary-arms-left-current-rest-1051-1164"
            and type(request.get("left_shoulder_startup_degrees")) is int
            and request["left_shoulder_startup_degrees"] == 1,
            "exact ordinary current-rest profile and startup allowance required",
        )
    require(
        isinstance(request, dict)
        and set(request) == request_fields
        and type(request["version"]) is int and request["version"] == 1
        and request["kind"] == "left-response-p"
        and type(request["proportional"]) is int and request["proportional"] == 32
        and request["workload_segment"] == ("ordinary-arms" if current_rest else "left-reach")
        and request["source_head"] == source.get("motor_head") == backend.get("host_head") == original_motor
        and run.get("backend_contract") == backend and run.get("recipe") == "physical-live-teleop"
        and request["boot_id"] == proof.get("boot_id")
        and binding["request_sha256"] == sha(canonical(request)),
        "exact original motor source and canonical consumed response request required",
    )
    for value in (request["request_id"], request["boot_id"]):
        require(type(value) is str and str(uuid.UUID(value)) == value, "canonical original request identity required")
    expected, frozen_keys = _response_origin_settings()
    registers, individual = trial["original_registers"], trial["original_bytes"]
    # This exact OFF/no-write state is an origin only, never powered readiness.
    volatile_stopped = (current_rest and isinstance(registers, dict)
                        and registers.get("Goal_Position") == 0 and registers.get("Acceleration") == 0)
    if volatile_stopped:
        expected["Acceleration"] = 0
    require(
        isinstance(registers, dict)
        and set(registers) == set(expected) | {"Present_Position", "Goal_Position", "Present_Voltage", "Present_Temperature"}
        and all(type(value) is int for value in registers.values())
        and all(registers[key] == value for key, value in expected.items())
        and 864 <= registers["Present_Position"] <= 3073
        and (1051 <= registers["Present_Position"] <= 1164 if volatile_stopped else
             not (1051 if current_rest else 2011) <= registers["Present_Position"] <= (1164 if current_rest else 2124))
        and (registers["Goal_Position"] == 0 if volatile_stopped else
             (864 if current_rest else 2011) <= registers["Goal_Position"] <= (3073 if current_rest else 2124))
        and 40 <= registers["Present_Voltage"] <= 140
        and 0 <= registers["Present_Temperature"] < 70
        and individual == {"24": 16, "25": 0}
        and all(type(value) is int for value in individual.values())
        and trial["original_settings_sha256"] == sha(canonical({"registers": registers, "individual_bytes": individual})),
        "only reviewed out-of-corridor or volatile OFF baseline with unchanged safe stopped settings required",
    )

    frozen = trial["frozen_teleop_registers"]
    require(
        isinstance(frozen, dict) and set(frozen) == frozen_keys
        and all(type(value) is int and value == registers[key] for key, value in frozen.items()),
        "unchanged actual original frozen teleop settings required",
    )
    events, prepared = trial["events"], trial["prepared_at_monotonic_s"]
    expiry = request["expires_at_monotonic_s"]
    require(
        isinstance(events, list) and len(events) == 40 and number(prepared)
        and number(expiry) and 0 <= prepared < expiry
        and prepared <= receipt["readback"]["started_at_monotonic_s"],
        "original pre-cleanup consumed baseline timing required",
    )
    observed, previous = set(), 0.0
    common = {"operation", "phase", "value", "started_at_monotonic_s", "completed_at_monotonic_s"}
    for row in events:
        require(isinstance(row, dict), "original baseline read row required")
        key = row.get("register") if "register" in row else row.get("address")
        wanted = common | ({"register"} if "register" in row else {"address", "width"})
        require(
            set(row) == wanted and row["operation"] == "read" and row["phase"] == "baseline"
            and key not in observed and type(row["value"]) is int
            and (key in registers and row["value"] == registers[key]
                 or type(key) is int and key in (24, 25) and row["width"] == 1
                 and type(row["width"]) is int and row["value"] == individual[str(key)]),
            "forty distinct value-matched read-only baseline rows required",
        )
        started, completed = row["started_at_monotonic_s"], row["completed_at_monotonic_s"]
        require(
            number(started) and number(completed) and previous <= started <= completed <= prepared
            and completed - started <= 0.04,
            "original ordered bounded baseline read timing required",
        )
        observed.add(key)
        previous = completed
    require(observed == set(registers) | {24, 25}, "complete original baseline register/byte reads required")


def _validate_follower_baseline_expired_origin(run, proof, receipt, chain):
    """An original OFF configure deadline grants no powered or lift readiness."""
    cause = "RuntimeError: AM1 teleop original follower baseline acquisition expired"
    rb = receipt["readback"]
    trial = rb.get("left_response_p_trial") or {}
    require(isinstance(trial, dict) and type(trial.get("schema_version")) is int
            and trial["schema_version"] == 1, "versioned original restored trial required")
    binding = trial.get("binding") or {}
    require(isinstance(binding, dict)
        and set(binding) == {"version", "kind", "run_id", "host_incarnation", "request_sha256", "request"}
        and type(binding.get("version")) is int and binding["version"] == 1,
        "closed original restored trial binding required")
    request = binding.get("request") or {}
    require(isinstance(request, dict)
        and set(request) == {"version", "kind", "request_id", "source_head", "proportional",
                            "workload_segment", "boot_id", "expires_at_monotonic_s",
                            "workspace_profile", "left_shoulder_startup_degrees"}
        and type(request.get("version")) is int and request["version"] == 1,
        "closed original ordinary response request required")
    for value in (request["request_id"], request["boot_id"]):
        require(type(value) is str and str(uuid.UUID(value)) == value,
                "canonical original request identity required")
    source = proof.get("source") or {}
    require(
        run.get("recipe") == "physical-live-teleop"
        and receipt["first_cause"] == cause and chain == [{"cause": cause, "notes": []}]
        and trial.get("kind") == "left-response-p" and trial.get("motor") == "arm_left_elbow_flex"
        and type(trial.get("motor_id")) is int and trial["motor_id"] == 3
        and trial.get("state") == trial.get("status") == "restored"
        and trial.get("restore_required") is False and "first_failure" not in trial
        and "active_verified" not in trial and "active_registers" not in trial
        and binding.get("run_id") == run["run_id"]
        and binding.get("host_incarnation") == proof["host_incarnation"]
        and request.get("source_head") == source.get("motor_head")
        == (source.get("backend_contract") or {}).get("host_head")
        and request.get("boot_id") == proof.get("boot_id")
        and request.get("kind") == binding.get("kind") == "left-response-p"
        and type(request.get("proportional")) is int and request["proportional"] == 32
        and request.get("workload_segment") == "ordinary-arms"
        and request.get("workspace_profile") == "ordinary-arms-left-current-rest-1051-1164"
        and type(request.get("left_shoulder_startup_degrees")) is int
        and request["left_shoulder_startup_degrees"] == 1
        and binding.get("request_sha256") == sha(canonical(request)),
        "exact direct pre-Live follower expiry with restored same-owner ordinary trial required",
    )
    registers, individual = trial.get("original_registers"), trial.get("original_bytes")
    expected, frozen_keys = _response_origin_settings()
    expected["Acceleration"] = 0
    expected.pop("Present_Current")  # A healthy stopped current is bounded below, not invented as zero.
    require(
        isinstance(registers, dict)
        and set(registers) == set(expected) | {"Present_Position", "Goal_Position", "Present_Current",
                                              "Present_Voltage", "Present_Temperature"}
        and all(type(v) is int for v in registers.values())
        and all(registers.get(key) == value for key, value in expected.items())
        and 1051 <= registers.get("Present_Position", -1) <= 1164
        and registers.get("Goal_Position") == registers["Present_Position"]
        and abs(registers.get("Present_Current", math.inf)) < registers.get("Protection_Current", 0)
        and 0 <= registers.get("Present_Temperature", -1) < registers.get("Max_Temperature_Limit", 0) <= 100
        and 0 < registers.get("Min_Voltage_Limit", 0) <= registers.get("Present_Voltage", -1)
        <= registers.get("Max_Voltage_Limit", 0) <= 255
        and individual == {"24": 16, "25": 0} and all(type(v) is int for v in individual.values())
        and trial.get("original_settings_sha256") == sha(canonical({"registers": registers, "individual_bytes": individual}))
        and trial.get("selected_acceleration_profile") == {"version": 1, "original_raw": 0, "active_raw": 254, "maximum_raw": 254}
        and type(trial.get("restored_p_readback")) is int and trial["restored_p_readback"] == 16
        and type(trial.get("restored_acceleration_readback")) is int and trial["restored_acceleration_readback"] == 0,
        "original healthy OFF settings and complete P16/Acceleration0 restoration required",
    )
    frozen = trial.get("frozen_teleop_registers")
    require(isinstance(frozen, dict) and set(frozen) == frozen_keys
        and all(type(value) is int and value == registers[key] for key, value in frozen.items()),
        "complete unchanged original frozen teleop settings required")
    prepared, configured, restored = (trial.get(key) for key in (
        "prepared_at_monotonic_s", "configured_at_monotonic_s", "restored_at_monotonic_s"))
    expiry = request.get("expires_at_monotonic_s")
    require(all(number(v) for v in (prepared, configured, restored, expiry))
        and 0 <= prepared <= configured < expiry and configured <= restored <= rb["started_at_monotonic_s"],
        "original ordered selected preparation/configuration/restoration required")
    events = trial.get("events")
    require(isinstance(events, list) and events, "actual restored settings reads required")
    reads, previous = {}, 0.0
    for row in events:
        require(isinstance(row, dict) and not {"error", "error_type", "reconciled"}.intersection(row),
                "original selected event errors cannot qualify")
        start, end = row.get("started_at_monotonic_s"), row.get("completed_at_monotonic_s")
        require(number(start) and number(end) and previous <= start <= end <= restored
                and type(row.get("value")) is int, "ordered bounded original selected events required")
        previous = end
        key = row.get("register") if "register" in row else row.get("address")
        if row.get("operation") == "write":
            require(row.get("register") in {"Torque_Enable", "Lock", "P_Coefficient", "Acceleration"}
                and row["value"] in {"Torque_Enable": (0,), "Lock": (0, 1), "P_Coefficient": (16, 32),
                                      "Acceleration": (0, 254)}[row["register"]],
                "pre-Live selected transaction cannot contain powered or other writes")
        else:
            require(row.get("operation") == "read" and (key != "Status" or row["value"] == 0),
                    "original selected hard-status fault cannot qualify")
            reads[(row.get("phase"), key)] = row["value"]
    preserved = set(registers) - {"P_Coefficient", "Torque_Enable", "Lock", "Present_Position", "Goal_Position",
                                  "Present_Current", "Present_Voltage", "Present_Temperature", "Status"}
    require(all(reads.get(("restored", key)) == registers[key] for key in preserved | {"P_Coefficient"})
        and all(reads.get(("restored", int(key))) == value for key, value in individual.items())
        and reads.get(("restored_acceleration", "Acceleration")) == 0
        and reads.get(("restore_original_lock_verify", "Lock")) == 0,
        "actual unchanged preserved settings/bytes/P16/Acceleration0/Lock0 readbacks required")


def _validate_stopped_response_completion(run, proof):
    """Only the separate stopped restoration can close Actual115's old obligation."""
    old = (run.get("physical_result") or {}).get("readback") or {}
    rb = old.get("readback") or {}
    trial = rb.get("left_response_p_trial") or {}
    binding, source = trial.get("binding") or {}, proof.get("source") or {}
    cause = "RuntimeError: Current-rest Goal write lost actual Torque0"
    require(
        run.get("run_id") == proof.get("run_id") == RESPONSE_GOAL_RUN
        and proof.get("host_incarnation")
        == old.get("host_incarnation")
        == "f19de2d7-e77f-4277-9e61-13824813ea31"
        and proof.get("original_cleanup_origin") == RESPONSE_GOAL_RESTORE_ORIGIN
        and run.get("recipe") == "physical-live-teleop"
        and run.get("status") == "faulted"
        and run.get("uncertain") is True
        and run.get("native_live_at") is None
        and type(run.get("progress_s")) in (int, float)
        and run["progress_s"] == 0
        and run.get("native_first_cause")
        == proof.get("original_first_cause")
        == old.get("first_cause")
        == cause
        and old.get("cleanup") == "physical_cleanup_unknown"
        and old.get("uncertain") is True
        and old.get("provenance") == "protected-host/actual-motor-readback"
        and bool(old.get("cleanup_errors"))
        and rb.get("verified") is False
        and rb.get("buses_closed") is True
        and rb.get("lift_readback") == "unavailable-or-failed"
        and rb.get("selected_gain_restore_required") is True
        and bool(rb.get("cleanup_errors"))
        and (run.get("physical_result") or {}).get("native_live_at") is None,
        "exact unchanged Actual115 pre-Live outstanding selected restoration required",
    )
    fields = {
        "schema_version",
        "kind",
        "binding",
        "motor",
        "motor_id",
        "original_registers",
        "original_bytes",
        "frozen_teleop_registers",
        "prepared_at_monotonic_s",
        "original_settings_sha256",
        "events",
        "restore_required",
        "state",
        "status",
        "first_failure",
        "priming_position",
    }
    request = binding.get("request") or {}
    require(
        set(trial) == fields
        and type(trial["schema_version"]) is int
        and trial["schema_version"] == 1
        and trial["kind"] == "left-response-p"
        and trial["motor"] == "arm_left_elbow_flex"
        and type(trial["motor_id"]) is int
        and trial["motor_id"] == 3
        and trial["state"] == trial["status"] == "restore_failed"
        and trial["restore_required"] is True
        and trial["first_failure"].get("type") == "RuntimeError"
        and trial["first_failure"].get("message") == cause.removeprefix("RuntimeError: ")
        and set(binding) == {"version", "kind", "run_id", "host_incarnation", "request_sha256", "request"}
        and type(binding["version"]) is int
        and binding["version"] == 1
        and binding["kind"] == trial["kind"]
        and binding["run_id"] == run["run_id"]
        and binding["host_incarnation"] == proof["host_incarnation"]
        and set(request)
        == {
            "version",
            "kind",
            "request_id",
            "source_head",
            "proportional",
            "workload_segment",
            "boot_id",
            "expires_at_monotonic_s",
            "workspace_profile",
        }
        and type(request["version"]) is int
        and request["version"] == 1
        and request["kind"] == trial["kind"]
        and type(request["proportional"]) is int
        and request["proportional"] == 32
        and request["workload_segment"] == "left-reach"
        and request["workspace_profile"] == "left-current-rest-1051-1164"
        and request["source_head"]
        == source.get("motor_head")
        == run["backend_contract"].get("host_head")
        == "c4b35ba83acb188f7884db5cfcd686aa41902e16"
        and source.get("backend_contract") == run["backend_contract"]
        and source.get("original_executor_head") == "9985a6333abf34081251b6799f1326bb2a53b64e"
        and request["boot_id"] == proof["boot_id"]
        and sha(canonical(request)) == binding["request_sha256"],
        "original current-rest request/source/run binding must remain exact",
    )
    for value in (request["request_id"], request["boot_id"]):
        require(type(value) is str and str(uuid.UUID(value)) == value, "canonical original identity required")
    registers, individual, original_events = (
        trial["original_registers"],
        trial["original_bytes"],
        trial["events"],
    )
    require(
        isinstance(registers, dict)
        and all(type(v) is int for v in registers.values())
        and individual == {"24": 16, "25": 0}
        and all(type(v) is int for v in individual.values())
        and all(
            registers.get(k) == v
            for k, v in {
                "ID": 3,
                "Model_Number": 777,
                "Firmware_Major_Version": 3,
                "Firmware_Minor_Version": 10,
                "Operating_Mode": 0,
                "Homing_Offset": 1845,
                "Phase": 12,
                "Min_Position_Limit": 864,
                "Max_Position_Limit": 3073,
                "Torque_Enable": 0,
                "Lock": 0,
                "P_Coefficient": 16,
                "I_Coefficient": 0,
                "D_Coefficient": 32,
                "Goal_Position": 2050,
                "Status": 0,
            }.items()
        )
        and 1051 <= registers["Present_Position"] <= 1164
        and sha(canonical({"registers": registers, "individual_bytes": individual}))
        == trial["original_settings_sha256"]
        and isinstance(original_events, list)
        and len(original_events) >= 40
        and [
            r.get("value")
            for r in original_events
            if r.get("operation") == "write" and r.get("register") == "Goal_Position"
        ]
        == [trial["priming_position"].get("value"), 2050]
        and not any(
            r.get("operation") == "write"
            and (
                r.get("register") not in {"Torque_Enable", "Lock", "Goal_Position"}
                or r.get("register") == "Torque_Enable"
                and r.get("value") != 0
                or r.get("register") == "Lock"
                and r.get("value") != 1
            )
            for r in original_events
        )
        and all(
            any(
                r.get("operation") == "read"
                and r.get("register") == "Torque_Enable"
                and r.get("value") == 1
                and r.get("phase") == phase
                for r in original_events
            )
            for phase in ("prime_current_rest_goal_after", "restore_original_goal_after")
        ),
        "original pre-P32 Goal/OFF failure and immutable original settings required",
    )
    envelope = proof.get("selected_restoration")
    require(
        isinstance(envelope, dict) and set(envelope) == {"sha256", "receipt"},
        "separate selected completion receipt required",
    )
    receipt = envelope["receipt"]
    keys = {
        "version",
        "kind",
        "completion_id",
        "run_id",
        "host_incarnation",
        "request_sha256",
        "source_head",
        "original_sha256",
        "original_settings_sha256",
        "original_lock",
        "selected_restoration_verified",
        "native_acknowledged",
        "events",
        "started_at_monotonic_s",
        "completed_at_monotonic_s",
    }
    original_raw = (json.dumps(trial, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    require(
        isinstance(receipt, dict)
        and set(receipt) == keys
        and type(receipt["version"]) is int
        and receipt["version"] == 1
        and receipt["kind"] == "left-response-p-stopped-completion"
        and receipt["run_id"] == run["run_id"]
        and receipt["host_incarnation"] == proof["host_incarnation"]
        and receipt["request_sha256"] == binding["request_sha256"]
        and receipt["source_head"] == request["source_head"]
        and receipt["original_sha256"] == sha(original_raw)
        and receipt["original_settings_sha256"] == trial["original_settings_sha256"]
        and type(receipt["original_lock"]) is int
        and receipt["original_lock"] == 0
        and receipt["selected_restoration_verified"] is True
        and receipt["native_acknowledged"] is False
        and envelope["sha256"] == sha(canonical(receipt)),
        "closed separate completion must bind original bytes and actual selected restoration",
    )
    require(
        type(receipt["completion_id"]) is str
        and str(uuid.UUID(receipt["completion_id"])) == receipt["completion_id"],
        "canonical completion UUID required",
    )
    started, ended = receipt["started_at_monotonic_s"], receipt["completed_at_monotonic_s"]
    require(
        number(started)
        and number(ended)
        and rb["completed_at_monotonic_s"] <= started <= ended
        and ended - started <= 20
        and number(proof.get("started_at_monotonic_s"))
        and 0 <= proof["started_at_monotonic_s"] - ended <= 180,
        "completion must be bounded and precede the fresh read-only stopped proof",
    )
    events = receipt["events"]
    require(
        isinstance(events, list) and 43 <= len(events) <= 45,
        "complete selected acquisition and final readbacks required",
    )

    def row(event, operation, register=None, *, address=None, value=None):
        required = {"operation", "phase", "value", "started_at_monotonic_s", "completed_at_monotonic_s"}
        required |= {"register"} if address is None else {"address", "width"}
        extras = {"error_type", "error", "reconciled"} if operation == "write" else set()
        require(
            isinstance(event, dict)
            and required <= set(event) <= required | extras
            and event["operation"] == operation
            and event["phase"] == "stopped_completion"
            and (
                event.get("register") == register
                if address is None
                else event.get("address") == address
                and type(event.get("address")) is int
                and type(event.get("width")) is int
                and event["width"] == 1
            )
            and type(event["value"]) is int
            and (value is None or event["value"] == value)
            and all(number(event[k]) for k in ("started_at_monotonic_s", "completed_at_monotonic_s"))
            and started <= event["started_at_monotonic_s"] <= event["completed_at_monotonic_s"] <= ended
            and (
                operation == "write"
                or event["completed_at_monotonic_s"] - event["started_at_monotonic_s"] <= 0.15
            ),
            "actual bounded value-matched stopped completion operation required",
        )

    order = [r.get("register") for r in original_events[:38]]
    require(
        len(registers) == len(set(order)) == 38 and set(order) == set(registers),
        "complete original baseline register order required",
    )
    actual, previous = {}, started
    for event, name in zip(events[:38], order, strict=True):
        row(event, "read", name)
        require(previous <= event["started_at_monotonic_s"], "completion read order changed")
        previous = event["completed_at_monotonic_s"]
        actual[name] = event["value"]
    dynamic = {"Lock", "Present_Position", "Present_Current", "Present_Voltage", "Present_Temperature"}
    require(
        all(actual[k] == v for k, v in registers.items() if k not in dynamic)
        and actual["Lock"] in (0, 1)
        and registers["Min_Position_Limit"] <= actual["Present_Position"] <= registers["Max_Position_Limit"]
        and abs(actual["Present_Current"]) < registers["Protection_Current"]
        and registers["Min_Voltage_Limit"] <= actual["Present_Voltage"] <= registers["Max_Voltage_Limit"],
        "fresh actual original static/P16/I0/D32/Goal/OFF/status/current/voltage required",
    )
    for event, address in zip(events[38:40], (24, 25), strict=True):
        row(event, "read", address=address, value=individual[str(address)])
        require(previous <= event["started_at_monotonic_s"], "completion byte read order changed")
        previous = event["completed_at_monotonic_s"]
    middle = events[40:-3]
    if actual["Lock"] == 0:
        require(not middle, "already restored Lock cannot authorize another write")
    else:
        require(len(middle) in (1, 2), "only the original Lock0 write may complete restoration")
        write = middle[0]
        row(write, "write", "Lock", value=0)
        require(previous <= write["started_at_monotonic_s"], "Lock write precedes fresh settings proof")
        if len(middle) == 2:
            row(middle[1], "read", "Lock", value=0)
            require(
                write.get("reconciled") is True
                and type(write.get("error_type")) is str
                and type(write.get("error")) is str
                and len(write["error"]) <= 160
                and write["started_at_monotonic_s"]
                <= middle[1]["started_at_monotonic_s"]
                <= middle[1]["completed_at_monotonic_s"]
                <= write["completed_at_monotonic_s"],
                "ambiguous Lock receipt requires actual nested matching readback",
            )
        else:
            require(
                not {"error", "error_type", "reconciled"}.intersection(write),
                "unknown Lock write cannot qualify from flags",
            )
        previous = write["completed_at_monotonic_s"]
    for event, (register, value) in zip(
        events[-3:], (("Lock", 0), ("Torque_Enable", 0), ("Goal_Position", 2050)), strict=True
    ):
        row(event, "read", register, value=value)
        require(previous <= event["started_at_monotonic_s"], "final completion read order changed")
        previous = event["completed_at_monotonic_s"]
    return receipt


def _load_selected_restoration(config, proof):
    envelope = proof["selected_restoration"]
    receipt = envelope["receipt"]
    folder = Path(config["run_root"]) / proof["run_id"][:8]
    original = _private_bytes(folder / "left-response-p-original.json")
    require(sha(original) == receipt["original_sha256"], "immutable original selected obligation changed")
    decoded = _json(original)
    require(
        (json.dumps(decoded, sort_keys=True, indent=2, allow_nan=False) + "\n").encode() == original
        and decoded.get("binding", {}).get("run_id") == proof["run_id"]
        and decoded.get("binding", {}).get("host_incarnation") == proof["host_incarnation"]
        and decoded.get("original_settings_sha256") == receipt["original_settings_sha256"],
        "literal original selected obligation encoding/binding changed",
    )
    raw = _private_bytes(
        folder / ("left-response-p-stopped-completion-" + receipt["completion_id"] + ".json")
    )
    require(
        sha(raw) == envelope["sha256"] and raw == canonical(receipt),
        "immutable separate selected completion changed",
    )
    return decoded


def validate_cleanup_origin(run, proof, receipt):
    """Qualify an explicit incomplete origin without promoting its old lift proof."""
    origin = proof.get("original_cleanup_origin")
    if origin == RESPONSE_GOAL_RESTORE_ORIGIN:
        require(receipt == (run.get("physical_result") or {}).get("readback"), "original selected cleanup changed")
        _validate_stopped_response_completion(run, proof)
        return False  # Actual115 retains its real present unknown/owner markers.
    if origin is None:
        require(
            receipt.get("cleanup") == "physical_cleanup_unknown" and receipt.get("uncertain") is True,
            "literal unchanged original unknown cleanup receipt required",
        )
        return False
    require(
        origin in (INCOMPLETE_PREACTIVATION_ORIGIN, PREHOME_COOL_STALE_ORIGIN,
                   PRECONFIGURE_COOL_STALE_ORIGIN, RESPONSE_BASELINE_REFUSED_ORIGIN, FOLLOWER_BASELINE_EXPIRED_ORIGIN),
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
    if origin == FOLLOWER_BASELINE_EXPIRED_ORIGIN:
        _validate_follower_baseline_expired_origin(run, proof, receipt, chain)
    elif origin == RESPONSE_BASELINE_REFUSED_ORIGIN:
        _validate_response_baseline_refused_origin(run, proof, receipt, chain)
    elif origin in (PREHOME_COOL_STALE_ORIGIN, PRECONFIGURE_COOL_STALE_ORIGIN):
        _validate_prehome_cool_stale_origin(run, receipt, chain, configure=origin == PRECONFIGURE_COOL_STALE_ORIGIN)
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


def validate_preimport_stop_origin(run, proof):
    """Eligibility only: a missing original receipt never proves a stopped motor."""
    lifecycle = run.get("physical_result") or {}
    execution = run.get("execution") or {}
    require(
        run.get("source") == "protected-physical-provider"
        and run.get("recipe") == "physical-live-teleop"
        and run.get("status") == run.get("requested_terminal") == "stopped"
        and run.get("uncertain") is True
        and run.get("cleanup") == "physical_cleanup_unknown"
        and "native_live_at" in run
        and run["native_live_at"] is None
        and number(run.get("progress_s"))
        and run["progress_s"] == 0
        and type(run.get("recovery_episodes")) is int
        and run["recovery_episodes"] == 0
        and execution.get("complete") is False
        and "fault" in execution
        and execution["fault"] is None
        and number(execution.get("progress_s"))
        and execution["progress_s"] == 0
        and all(key in run and run[key] is None for key in ("first_cause", "native_first_cause"))
        and lifecycle.get("phase") == "terminal"
        and lifecycle.get("terminal_status") == "stopped"
        and lifecycle.get("run_id") == run.get("run_id")
        and lifecycle.get("backend_incarnation") == proof.get("host_incarnation")
        and lifecycle.get("cleanup") == "physical_cleanup_unknown"
        and lifecycle.get("uncertain") is True
        and lifecycle.get("cleanup_errors") == []
        and all(
            key in lifecycle and lifecycle[key] is None
            for key in ("native_live_at", "first_cause", "readback")
        ),
        "exact uncertain preimport Stop with no native effect/cause/readback required",
    )
    require(
        proof.get("original_cleanup_origin") == PREIMPORT_STOP_ORIGIN
        and proof.get("original_cleanup_missing") is True
        and "original_cleanup_sha256" in proof
        and proof["original_cleanup_sha256"] is None
        and "original_first_cause" in proof
        and proof["original_first_cause"] is None
        and proof.get("original_markers") == dict.fromkeys(MARKERS),
        "explicit missing original cleanup and absent markers required; no fabricated receipt",
    )


def _validate_processor_import_frames(config, frames, *, enum_source=None):
    """Bounded pre-main package discovery; never evidence of stopped hardware."""
    require(
        5 <= len(frames) <= 64
        and [frame[2] for frame in frames[:2]] == ["_run_module_as_main", "_get_module_details"]
        and all(frame[0] == "<frozen runpy>" and frame[1] > 0 and frame[3] == [] for frame in frames[:2]),
        "package discovery must precede selected host main; bounded import chain required",
    )
    repository = Path(config["host_repository"]) / "src/lerobot"
    package = repository / "robots/alohamini"
    anchors = (
        (package / "__init__.py", "from .alohamini import AlohaMini"),
        (package / "alohamini.py", "from lerobot.processor import RobotAction, RobotObservation"),
    )
    for frame, (path, source) in zip(frames[2:4], anchors, strict=True):
        require(
            frame[0] == path.as_posix() and frame[2] == "<module>" and frame[3]
            and frame[3][0] == "    " + source,
            "current package discovery anchors required; no host execution frames",
        )
    # These are the non-device import roots demonstrated by the existing processor
    # path. A new dependency family needs evidence rather than a permissive fallback.
    site = Path(config["host_python"]).parent.parent / "lib/python3.12/site-packages"
    sources = {}
    total_bytes = 0
    for index, (name, number, context, body) in enumerate(frames[2:], 2):
        path = Path(name)
        local = path.is_relative_to(repository)
        if index < 4:
            permitted = path == anchors[index - 2][0]
        elif local:
            relative = path.relative_to(repository)
            permitted = bool(relative.parts) and relative.parts[0] in {"processor", "configs", "transforms"}
        else:
            relative = path.relative_to(site) if path.is_relative_to(site) else None
            permitted = (relative is not None and len(relative.parts) >= 2 and relative.parts[0] in {
                "torch", "torchvision", "sympy"
            }) or (enum_source is not None and path == enum_source and context in {"__new__", "__set_name__"})
        require(
            permitted and path.is_absolute() and path.as_posix() == name and ".." not in path.parts
            and path.suffix == ".py" and all(not p.is_symlink() for p in (path, *path.parents))
            and path.is_file() and path.stat().st_size <= 2 * 1024 * 1024,
            "literal bounded package-import source required; device/foreign source refused",
        )
        if name not in sources:
            data = path.read_bytes()
            total_bytes += len(data)
            require(total_bytes <= 16 * 1024 * 1024, "bounded total package-import source required")
            sources[name] = data.decode("utf-8").splitlines()
        source = sources[name]
        require(
            0 < number <= len(source) and len(source[number - 1]) <= 1024
            and 1 <= len(body) <= 2 and body[0] == "    " + source[number - 1].strip()
            and all(len(line) <= 1024 and re.fullmatch(r" +[~^]+", line) for line in body[1:])
            and re.fullmatch(r"<module>|[A-Za-z_][A-Za-z_0-9.]*", context),
            "trace must match actual bounded import source/carets",
        )
        if local:
            tree = ast.parse("\n".join(source))
            require(
                context == "<module>"
                and any(isinstance(node, (ast.Import, ast.ImportFrom))
                        and node.lineno <= number <= node.end_lineno for node in ast.walk(tree))
                and not any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                            and node.lineno <= number <= node.end_lineno for node in ast.walk(tree)),
                "repository frames must remain module imports; no construction or operational frame",
            )


def _load_preimport_stop_originals(config, proof):
    """Read immutable origin evidence; no old-off or no-actuation qualification."""
    directory = Path(config["run_root"]) / proof["run_id"][:8]
    cleanup = directory / "cleanup.json"
    require(
        not cleanup.exists() and not cleanup.is_symlink(), "original cleanup must remain literally absent"
    )
    hashes = proof.get("original_host_logs")
    require(
        isinstance(hashes, dict)
        and set(hashes) == {"host-runtime.log", "host-launch.log"}
        and all(type(value) is str and re.fullmatch("[0-9a-f]{64}", value) for value in hashes.values()),
        "both original host log hashes required",
    )
    logs = {}
    for name, digest in hashes.items():
        data = _private_bytes(directory / name)
        require(sha(data) == digest, "original host log changed")
        require(len(data) <= 65536, "bounded original import-cancel logs required")
        logs[name] = data.decode("utf-8")
    launch = re.fullmatch(
        r"Running as unit: am1-pa01-host-"
        + re.escape(proof["run_id"])
        + r"\.service; invocation ID: [0-9a-f]{32}\nFinished with result: success\n"
        r"Main processes terminated with: code=killed, status=2/INT\n"
        r"Service runtime: ([0-9]+(?:\.[0-9]+)?)s\nCPU time consumed: ([0-9]+(?:\.[0-9]+)?)s\n",
        logs["host-launch.log"],
    )
    require(
        launch is not None and all(math.isfinite(float(v)) and float(v) > 0 for v in launch.groups()),
        "one original same-run SIGINT host launch required",
    )
    lines = logs["host-runtime.log"].splitlines()
    enum_source, handled_frames = None, []
    if lines and lines[-1] == (
        "Error calling __set_name__ on '_proto_member' instance 'IMAGENET1K_V1' in 'Swin_V2_S_Weights'"
    ):
        # Recognize the complete retained Enum exception chain, not a trimmed
        # stderr suffix. Every handled and terminal frame is source-checked below.
        python = Path(config["host_python"]).resolve(strict=True)
        require(python.is_file(), "configured real interpreter required for enum import origin")
        enum_source = python.parent.parent / "lib/python3.12/enum.py"
        name = enum_source.as_posix()
        prefix = [
            "Traceback (most recent call last):",
            f'  File "{name}", line 302, in __set_name__',
            "    enum_member = enum_class._value2member_map_[value]",
            "                  ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^",
            "TypeError: unhashable type: 'Weights'", "",
            "During handling of the above exception, another exception occurred:", "",
            "Traceback (most recent call last):",
            f'  File "{name}", line 309, in __set_name__',
            "    raise KeyError", "KeyError", "",
            "During handling of the above exception, another exception occurred:", "",
        ]
        require(lines[:len(prefix)] == prefix, "exact original handled Weights enum chain required")
        handled_frames = [
            (name, 302, "__set_name__", prefix[2:4]),
            (name, 309, "__set_name__", [prefix[10]]),
        ]
        lines = lines[len(prefix):-1]
    require(
        lines and lines[0] == "Traceback (most recent call last):" and lines[-1] == "KeyboardInterrupt",
        "one complete original import-cancel traceback required",
    )
    frames = []
    for line in lines[1:-1]:
        match = re.fullmatch(r'  File "([^"]+)", line ([0-9]+), in (.+)', line)
        if match:
            frames.append((match[1], int(match[2]), match[3], []))
        else:
            require(bool(frames), "unexpected original traceback prefix")
            frames[-1][3].append(line)
    if len(frames) > 3 and frames[3][3] and frames[3][3][0] == (
        "    from lerobot.processor import RobotAction, RobotObservation"
    ):
        if enum_source is not None:
            site = Path(config["host_python"]).parent.parent / "lib/python3.12/site-packages"
            require(
                len(frames) >= 7
                and frames[-3] == ((site / "torchvision/models/swin_transformer.py").as_posix(), 752,
                                   "<module>", ["    class Swin_V2_S_Weights(WeightsEnum):"])
                and [(frame[0], frame[1], frame[2]) for frame in frames[-2:]] == [
                    (enum_source.as_posix(), 595, "__new__"),
                    (enum_source.as_posix(), 315, "__set_name__"),
                ],
                "exact original Swin weights construction in stdlib enum import required",
            )
        _validate_processor_import_frames(config, frames + handled_frames, enum_source=enum_source)
        return logs
    require(enum_source is None, "enum cancellation requires the retained processor discovery chain")
    require(
        len(frames) == 11
        and [f[2] for f in frames]
        == [
            "_run_module_as_main",
            "_get_module_details",
            "<module>",
            "<module>",
            "<module>",
            "dataclass",
            "wrap",
            "_process_class",
            "_cmp_fn",
            "_create_fn",
            "<module>",
        ]
        and all(f[0] == "<frozen runpy>" and f[1] > 0 and f[3] == [] for f in frames[:2])
        and frames[-1] == ("<string>", 0, "<module>", []),
        "exact package-import/dataclass cancellation chain required; no post-import frames",
    )
    root = Path(config["host_repository"]) / "src/lerobot/robots/alohamini"
    source_lines = [
        "from .alohamini import AlohaMini",
        "from .lift_axis import LiftAxis, LiftAxisConfig, LiftHomeResult",
        "@dataclass",
    ]
    for frame, name, expected in zip(
        frames[2:5], ("__init__.py", "alohamini.py", "lift_axis.py"), source_lines, strict=True
    ):
        path = root / name
        require(
            frame[0] == path.as_posix()
            and frame[1] > 0
            and not any(p.is_symlink() for p in (path, *path.parents)),
            "original import source path differs",
        )
        data = path.read_bytes()
        require(len(data) <= 1_048_576, "bounded original import source required")
        source = data.decode("utf-8").splitlines()
        require(
            frame[1] <= len(source) and source[frame[1] - 1].strip() == expected,
            "traceback must match actual pinned import source",
        )
        if name == "lift_axis.py":
            require(
                frame[1] < len(source) and source[frame[1]].strip() == "class LiftAxisConfig:",
                "only pre-construction LiftAxisConfig decorator cancellation eligible",
            )
    stdlib = frames[5][0]
    require(
        stdlib.startswith("/")
        and stdlib.endswith("/lib/python3.12/dataclasses.py")
        and all(f[0] == stdlib and f[1] > 0 for f in frames[5:10]),
        "one original Python3.12 dataclass chain required",
    )
    code = source_lines + [
        "return wrap(cls)",
        "return _process_class(cls, init, repr, eq, order, unsafe_hash,",
        "_cmp_fn('__eq__', '==',",
        "return _create_fn(name,",
        "exec(txt, globals, ns)",
    ]
    for frame, expected in zip(frames[2:10], code, strict=True):
        require(
            frame[3]
            and frame[3][0] == "    " + expected
            and len(frame[3]) <= 2
            and all(re.fullmatch(r" +\^+", line) for line in frame[3][1:]),
            "only original source/caret lines permitted in import-cancel trace",
        )
    return logs


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
        if slot not in by_slot or temp >= by_slot[slot][1]:
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
    if run.get("run_id") == RESPONSE_GOAL_RUN or proof.get("original_cleanup_origin") == RESPONSE_GOAL_RESTORE_ORIGIN:
        _validate_stopped_response_completion(run, proof)
    else:
        require("selected_restoration" not in proof, "selected completion cannot qualify another origin")
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
    if proof.get("original_cleanup_origin") == RESPONSE_GOAL_RESTORE_ORIGIN:
        require(_load_selected_restoration(config, proof) == run["physical_result"]["readback"]["readback"]["left_response_p_trial"],
                "original selected snapshot differs from failed cleanup history")
    if proof.get("original_cleanup_origin") == PREIMPORT_STOP_ORIGIN:
        validate_preimport_stop_origin(run, proof)
        _load_preimport_stop_originals(config, proof)
        original = None  # Preserve literal absence; never synthesize an old host receipt.
        incomplete_origin = True
    else:
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
    if proof.get("original_cleanup_origin") == PREIMPORT_STOP_ORIGIN:
        _load_preimport_stop_originals(config, proof)
    if proof.get("original_cleanup_origin") == RESPONSE_GOAL_RESTORE_ORIGIN:
        _load_selected_restoration(config, proof)
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
    if proof.get("original_cleanup_origin") == RESPONSE_GOAL_RESTORE_ORIGIN:
        _load_selected_restoration(config, proof)
    # The actual qualified proof, archive and explicit ACK are all durable first.
    for name, data in originals.items():
        if data is not None and (state / name).exists():
            require(_private_bytes(state / name) == data, "marker changed during guarded ACK")
            (state / name).unlink()
    if proof.get("original_cleanup_origin") in (
        INCOMPLETE_PREACTIVATION_ORIGIN,
        PREHOME_COOL_STALE_ORIGIN,
        PREIMPORT_STOP_ORIGIN,
        RESPONSE_BASELINE_REFUSED_ORIGIN,
    ):
        require(
            originals == dict.fromkeys(MARKERS)
            and all(not (state / name).exists() and not (state / name).is_symlink() for name in MARKERS),
            "both incomplete-origin markers must remain absent through durable ACK",
        )
    if proof.get("original_cleanup_origin") == PREIMPORT_STOP_ORIGIN:
        _load_preimport_stop_originals(config, proof)
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
