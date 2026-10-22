"""Packet16 helpers on the existing AM1 motor-owner thread.

This module owns no device, socket, thread or alternate execution path. The
shared frame contract is imported only by the explicitly selected mode.
"""

import copy
import json
import math
import time
from collections import deque
from contextlib import suppress
from uuid import UUID

RECIPE = "physical-live-teleop"
STATIC = (
    "ID",
    "Model_Number",
    "Firmware_Major_Version",
    "Firmware_Minor_Version",
    "Min_Position_Limit",
    "Max_Position_Limit",
    "Homing_Offset",
    "Phase",
    "P_Coefficient",
    "I_Coefficient",
    "D_Coefficient",
    "Acceleration",
    "Maximum_Acceleration",
    "Protection_Current",
    "Max_Temperature_Limit",
    "Min_Voltage_Limit",
    "Max_Voltage_Limit",
    "Operating_Mode",
)
HEALTH = ("Status", "Present_Temperature", "Present_Voltage", "Torque_Enable", "Lock")
LIVE_HEALTH = (*HEALTH, "Operating_Mode")


def _integer(value):
    # SDK integer scalars are accepted, bools and fractional raw values are not.
    import numbers

    if isinstance(value, bool) or not isinstance(value, numbers.Integral):
        raise RuntimeError("AM1 teleop raw register is not an integer")
    return int(value)


def binding_valid(robot):
    binding = getattr(robot, "_am1_teleop_binding", None)
    if binding is None:
        return False
    if (
        not isinstance(binding, dict)
        or set(binding) != {"run_id", "host_incarnation", "recipe"}
        or binding["recipe"] != RECIPE
        or robot.config.robot_model != "alohamini1"
        or getattr(robot.config, "no_follower", False)
        or getattr(robot.config, "use_degrees", False)
    ):
        raise RuntimeError("AM1 teleop protected identity/model/units differ")
    if any(
        type(binding[key]) is not str or str(UUID(binding[key])) != binding[key]
        for key in ("run_id", "host_incarnation")
    ):
        raise RuntimeError("AM1 teleop protected identity is invalid")
    return True


def _groups(robot):
    return (
        ("left", robot.left_bus, robot.left_arm_motors),
        ("right", robot.right_bus, robot.right_arm_motors),
    )


def _fault(robot, reason, side, register, raw, timing, *, offending_motor=None, comparison=None):
    error = RuntimeError("AM1 teleop " + reason)
    # Diagnostic failure must never replace or postpone the original guard.
    try:
        if getattr(robot, "_am1_teleop_first_error", None) is None:
            robot._am1_teleop_first_error = error
        if getattr(robot, "_am1_teleop_fault", None) is None:
            robot._am1_teleop_fault = {
                "reason": reason,
                "side": side,
                "register": register,
                "raw": copy.deepcopy(raw),
                **copy.deepcopy(timing),
                "binding": copy.deepcopy(getattr(robot, "_am1_teleop_binding", None)),
                "offending_motor": offending_motor,
                "offending_value": copy.deepcopy(raw.get(offending_motor)),
                "comparison": copy.deepcopy(comparison),
                "prior_successful_health": None,
            }
            with suppress(Exception):
                warnings = list(getattr(robot, "_am1_teleop_temperature_warnings", ()))[-4:]
                if warnings:
                    robot._am1_teleop_fault["temperature_warnings"] = [
                        {
                            key: copy.deepcopy(warning[key])
                            for key in (
                                "side", "motor", "id", "binding", "value_c", "sample_monotonic_s",
                                "numeric_outlier_count", "ceiling_c", "values_c", "high_count",
                            )
                        }
                        for warning in warnings
                    ]
            with suppress(Exception):
                prior = robot._am1_teleop_health
                prior_timing = prior[side]["timing"][register]
                prior_value = prior[side]["registers"][register][offending_motor]
                prior_started, prior_completed = (
                    prior_timing[key]
                    for key in ("read_started_at_monotonic_s", "read_completed_at_monotonic_s")
                )
                if (
                    prior["binding"] == robot._am1_teleop_binding
                    and type(prior_value) is int
                    and all(
                        type(value) in (int, float) and math.isfinite(value)
                        for value in (prior_started, prior_completed)
                    )
                    and 0 <= prior_started <= prior_completed <= timing["read_started_at_monotonic_s"]
                ):
                    robot._am1_teleop_fault["prior_successful_health"] = {
                        "binding": copy.deepcopy(prior["binding"]),
                        "register": register,
                        "motor": offending_motor,
                        "value": prior_value,
                        "read_started_at_monotonic_s": prior_started,
                        "read_completed_at_monotonic_s": prior_completed,
                    }
            current = getattr(robot, "_am1_teleop_group_receipts", {}).get(side)
            if (
                current is not None
                and current["binding"] == robot._am1_teleop_binding
                and (
                    current["request"]["register"] == register
                    or (
                        current["request"]["address"] == 33
                        and current["request"]["width"] == 38
                        and current["request"].get("registers") == list(LIVE_HEALTH)
                        and register in LIVE_HEALTH
                    )
                )
            ):
                started, completed = (
                    timing["read_started_at_monotonic_s"],
                    timing["read_completed_at_monotonic_s"],
                )
                call_started = current["group_call_started_at_monotonic_s"]
                call_completed = current["group_call_completed_at_monotonic_s"]
                matching = (
                    call_completed is not None
                    and current["comm_result"] == 0
                    and started <= call_started <= call_completed <= completed
                )
                in_flight = (
                    call_completed is None
                    and raw.get("packet") is not None
                    and call_started <= started <= completed
                )
                if matching or in_flight:
                    robot._am1_teleop_fault["group_reply"] = copy.deepcopy(current)
        note = "[AM1 TELEOP FAULT] " + json.dumps(
            robot._am1_teleop_fault,
            allow_nan=False,
            separators=(",", ":"),
        )
        if len(note.encode("utf-8")) <= 16384:
            error.add_note(note)
    except Exception:
        pass
    raise error


def _report_temperature_warnings(robot):
    # The existing sink is bounded/nonblocking. Startup has no print fallback;
    # retained warnings publish once when this sink becomes available.
    reporter = getattr(robot, "_am1_routine_report", None)
    if reporter is None:
        return
    for warning in getattr(robot, "_am1_teleop_temperature_warnings", ()):
        if not warning.get("_reported", False):
            with suppress(Exception):
                line = {key: value for key, value in warning.items() if key != "_reported"}
                if reporter("[AM1 TEMPERATURE WARNING] " + json.dumps(line, separators=(",", ":"))) is not False:
                    warning["_reported"] = True


def _temperature(robot, side, bus, name, raw, timing, baseline_record, *, acquired):
    """TEMP-01: one original slot history per follower bus/ID/protected run.

    Followers explicitly confirm at their frozen installed limit. The shared
    lift collector keeps its own 55 C ceiling and cold-start rule.
    """
    from .lift_operational import TemperatureWindow

    states = getattr(robot, "_am1_teleop_temperatures", None)
    if states is None:
        states = robot._am1_teleop_temperatures = {}
    ceiling = baseline_record["registers"]["Max_Temperature_Limit"][name]
    key = (side, bus.motors[name].id)
    identity = (id(bus), name, bus.motors[name].model, copy.deepcopy(robot._am1_teleop_binding), ceiling)
    state = states.get(key)
    if state is None:
        state = states[key] = {
            "identity": identity, "bus": bus, "window": TemperatureWindow(ceiling_c=ceiling),
            "last": None, "receipts": deque(maxlen=64), "last_warning": -math.inf,
        }
    window = state["window"]
    value, now = raw[name], time.monotonic()
    evidence = None
    try:
        if state["identity"] != identity:
            window.refuse("temperature: frozen follower bus/ID/run/settings changed.")
        started, completed = (
            timing["read_started_at_monotonic_s"], timing["read_completed_at_monotonic_s"]
        )
        if (
            any(type(t) not in (int, float) or not math.isfinite(t) for t in (started, completed, now))
            or not 0 <= started <= completed <= now
            or now - started > window.MAX_AGE_S
        ):
            window.refuse("temperature: invalid or stale original acquisition interval.")
        original = (started, completed, value)
        if acquired:
            evidence = window.update(value, completed)
            state["last"] = original
            state["receipts"].append(original)
        else:
            if original not in state["receipts"]:
                window.refuse("temperature: original acquisition was not registered by this follower owner.")
            # read_groups and check_health consume the SAME original receipt.
            # Rechecking it neither invents a sample nor adds a thermal vote.
            if window.failure is not None:
                raise window.failure
            if len(window.samples) == window.SIZE:
                window.assert_fresh(now)
        evidence = {
            **(evidence or {}),
            "ceiling_c": ceiling, "count": len(window.samples), "ready": window.ready,
            "high_count": sum(t >= ceiling for _, t in window.samples),
            "values_c": [t for _, t in window.samples], "samples": list(window.samples),
            "numeric_outlier_count": window.outliers, "sample_monotonic_s": completed,
            "aggregation": "maximum_per_occupied_slot", "slot_s": window.SLOT_S,
        }
    except RuntimeError as error:
        comparison = {
            "units": "raw_register", "lower_inclusive": 0, "upper_exclusive": ceiling,
            "temperature": {
                "ceiling_c": ceiling, "samples": [list(sample) for sample in window.samples],
                "high_count": sum(t >= ceiling for _, t in window.samples),
                "numeric_outlier_count": window.outliers, "first_cause": str(error),
            },
        }
        with suppress(Exception):
            comparison["configured"] = {
                "Max_Temperature_Limit": {
                    "value": ceiling, **baseline_record.get("timing", {}).get("Max_Temperature_Limit", {}),
                }
            }
        _fault(
            robot, "actual configured temperature limit: " + name + "; " + str(error),
            side, "Present_Temperature", raw, timing, offending_motor=name, comparison=comparison,
        )
    if value >= ceiling and acquired and now - state["last_warning"] >= 1.0:
        warnings = getattr(robot, "_am1_teleop_temperature_warnings", None)
        if warnings is None:
            warnings = robot._am1_teleop_temperature_warnings = deque(maxlen=60)
        warning = {
            "side": side, "motor": name, "id": key[1], "binding": copy.deepcopy(robot._am1_teleop_binding),
            "value_c": value, **evidence,
        }
        warnings.append(warning)
        # Before the bounded host writer exists, retain evidence in memory.
        # _report_routine's print fallback must never block startup acquisition.
        _report_temperature_warnings(robot)
        state["last_warning"] = now
    return evidence


def refresh_startup_temperatures(robot, *, force=False):
    """Due original group reads on the existing owner, only until verified Live."""
    if (
        not getattr(robot, "_am1_teleop_startup_temperature", False)
        or getattr(robot, "_am1_teleop_temperature_refreshing", False)
    ):
        return
    _refresh_due_temperatures(robot, force=force)


def _refresh_due_temperatures(robot, *, force=False, active=None):
    from .lift_motor_feedback import POLL_S

    states = robot._am1_teleop_temperatures
    now = time.monotonic()
    due = force or any(
        (round(now * 1e9) - state["window"]._origin_ns) // round(state["window"].SLOT_S * 1e9)
        > state["window"]._last_slot
        or active is True and now - state["window"]._last_sample_at >= POLL_S
        for state in states.values()
    )
    if due:
        robot._am1_teleop_temperature_refreshing = True
        try:
            read_groups(robot, ("Present_Temperature",), active=active)
        finally:
            robot._am1_teleop_temperature_refreshing = False


def refresh_after_arm_retry(robot):
    """Retain the existing retry entrypoint for the shared owner scheduler."""
    service_due_feedback(robot)


class ThermalHistoryHoldRequiredError(RuntimeError):
    """Fresh RAW exists, but the original five-peak proof cannot permit motion."""


def enable_thermal_history(control, robot):
    """Opt in only after this real whole-teleop owner has its immutable Live clock."""
    admitted = control._live_summary.get("live_admitted_at_monotonic_s")
    if admitted is None or not getattr(robot, "_am1_teleop_temperatures", None):
        return
    operation = getattr(robot, "_lift_operation", None)
    if operation is None:
        return
    identity = {**control.binding, "recipe": RECIPE}
    context = {"binding": identity, "live_at": admitted,
               "deadline": control._live_summary["live_deadline_monotonic_s"]}
    previous = getattr(robot, "_am1_thermal_history_context", None)
    if previous is not None:
        if previous != context:
            raise RuntimeError("AM1 thermal history owner/Live clock changed")
        return
    if not binding_valid(robot) or robot._am1_teleop_binding != identity:
        raise RuntimeError("AM1 thermal history requires its original admitted owner")
    windows = [operation.temperature, *(state["window"] for state in robot._am1_teleop_temperatures.values())]
    if len(windows) != 13:
        raise RuntimeError("AM1 thermal history requires all original role windows")
    now = control.clock()
    for window in windows:
        window.assert_fresh(now)  # Startup must already have strictly qualified.
    robot._am1_thermal_history_context = copy.deepcopy(context)
    robot._am1_thermal_history_enabled = True
    robot._am1_thermal_history_episode = None
    for window in windows:
        window.allow_history_hold = True


def thermal_history_status(robot, *, collect_due=False):
    """Original-clock safety proof; history loss is distinct from lost/invalid RAW."""
    context = getattr(robot, "_am1_thermal_history_context", None)
    if context is None:
        if getattr(robot, "_am1_thermal_history_enabled", False):
            raise RuntimeError("AM1 thermal history original owner context disappeared")
        return None
    if not binding_valid(robot) or robot._am1_teleop_binding != context["binding"]:
        raise RuntimeError("AM1 thermal history protected identity changed")
    operation = robot._lift_operation
    operation.raise_if_faulted()
    windows = [operation.temperature, *(state["window"] for state in robot._am1_teleop_temperatures.values())]
    if len(windows) != 13 or any(window.allow_history_hold is not True for window in windows):
        raise RuntimeError("AM1 thermal history role/collector changed")
    now = time.monotonic()
    episode = getattr(robot, "_am1_thermal_history_episode", None)
    prior_deficits = [window.history_deficit_at for window in windows if window.history_deficit_at is not None]
    if collect_due:
        if getattr(robot, "_am1_teleop_first_error", None) is not None:
            raise robot._am1_teleop_first_error
        for window in windows:
            window.assert_fresh(now, refreshing=True)
        ceiling = episode["ceiling"] if episode else min(
            min(prior_deficits) + 10 if prior_deficits else context["deadline"], context["deadline"])
        if now >= ceiling:
            raise RuntimeError("AM1 thermal history original recovery deadline expired")
        if (getattr(robot, "_am1_thermal_history_collecting", False)
                or getattr(robot, "_am1_teleop_temperature_refreshing", False)):
            raise RuntimeError("AM1 thermal history nested due collection refused")
        robot._am1_thermal_history_collecting = True
        try:
            service_due_feedback(robot)
        finally:
            robot._am1_thermal_history_collecting = False
        now = time.monotonic()
        if now >= ceiling:
            raise RuntimeError("AM1 thermal history original recovery deadline expired")
    for window in windows:
        window.assert_fresh(now)  # RAW loss and every existing latch remain fatal.
    deficits = prior_deficits + [window.history_deficit_at for window in windows if window.history_deficit_at is not None]
    if deficits and episode is None:
        started = min(deficits)
        episode = robot._am1_thermal_history_episode = {
            "started": started, "ceiling": min(started + 10, context["deadline"]),
            "held_goals": None, "hold_sample": None, "zero_started": None, "zero_completed": None,
        }
    if episode is not None and now >= episode["ceiling"]:
        raise RuntimeError("AM1 thermal history original recovery deadline expired")
    return {
        "version": 1, "raw_sample_at_monotonic_s": min(window._last_sample_at for window in windows),
        "peaks_ready": all(window.ready for window in windows),
        "deficit_started_at_monotonic_s": episode["started"] if episode else None,
        "deficit_ceiling_monotonic_s": episode["ceiling"] if episode else None,
        "hold_sample_at_monotonic_s": episode["hold_sample"] if episode else None,
        "zero_read_started_at_monotonic_s": episode["zero_started"] if episode else None,
        "zero_read_completed_at_monotonic_s": episode["zero_completed"] if episode else None,
    }


def service_due_feedback(robot):
    """Service due real role feedback between ordinary Live I/O phases."""
    from .lift_motor_feedback import POLL_S

    if getattr(robot, "_am1_teleop_first_error", None) is not None:
        raise robot._am1_teleop_first_error
    if not binding_valid(robot):
        raise RuntimeError("AM1 teleop retry feedback identity changed")
    operation = getattr(robot, "_lift_operation", None)
    if operation is None:
        raise RuntimeError("AM1 teleop retry requires its original lift owner")
    operation.raise_if_faulted()
    windows = [operation.temperature, *(state["window"] for state in robot._am1_teleop_temperatures.values())]
    now = time.monotonic()
    for window in windows:
        window.assert_fresh(now, refreshing=True)
    def refresh_lift_if_due():
        now = time.monotonic()
        for window in windows:
            window.assert_fresh(now, refreshing=True)
        window = operation.temperature
        due = ((round(now * 1e9) - window._origin_ns) // round(window.SLOT_S * 1e9) > window._last_slot
               or now - window._last_sample_at >= POLL_S)
        if due:
            drain = getattr(robot, "_am1_routine_lift_drain", None)
            if not callable(drain):
                raise RuntimeError("AM1 teleop retry requires its bounded routine lift drain")
            drain()
            # Formatting/queue submission consumes actual time; no earlier guard
            # grants permission to read after any role's last RAW receipt expires.
            now = time.monotonic()
            for window in windows:
                window.assert_fresh(now, refreshing=True)
            operation.poll(defer_sample_log=True)

    refresh_lift_if_due()
    now = time.monotonic()
    for window in windows:
        window.assert_fresh(now, refreshing=True)
    _refresh_due_temperatures(robot, active=True)
    # Other-role I/O can cross an anchored boundary. One bounded follow-up per
    # role keeps genuine slots; there is no retry-until-fresh loop or age waiver.
    refresh_lift_if_due()
    now = time.monotonic()
    for window in windows:
        window.assert_fresh(now, refreshing=True)
    _refresh_due_temperatures(robot, active=True)
    operation.temperature.assert_fresh(time.monotonic())
    _fresh_temperatures(robot)


def _fresh_temperatures(robot):
    for state in robot._am1_teleop_temperatures.values():
        state["window"].assert_fresh(time.monotonic())


def read_groups(robot, registers, *, active=None, _live_health=False):
    if getattr(robot, "_am1_teleop_first_error", None) is not None:
        raise robot._am1_teleop_first_error
    if _live_health and (tuple(registers) != LIVE_HEALTH or active is not True):
        raise RuntimeError("AM1 teleop live health window differs")
    records = {}
    for side, bus, names in _groups(robot):
        if bus is None or len(names) != 6:
            raise RuntimeError("AM1 teleop requires both exact six-motor follower chains")
        rows, timing, temperatures = {}, {}, {}
        if _live_health:
            from lerobot.motors.feetech.tables import MODEL_CONTROL_TABLE

            table = MODEL_CONTROL_TABLE["sts3215"]
            ids = [bus.motors[name].id for name in names]
            started = time.monotonic()
            # Exactly one original SDK transaction, with no decode until its
            # complete window is available. getData cannot decode width38.
            bus._setup_sync_reader(ids, 33, 38)
            comm = bus.sync_reader.txRxPacket()
            completed = time.monotonic()
            if not bus._is_comm_success(comm):
                raise ConnectionError(
                    "AM1 teleop live health read failed: " + bus.packet_handler.getTxRxResult(comm)
                )
            if not bus.sync_reader.last_result or any(
                not bus.sync_reader.isAvailable(motor_id, *table[register])
                for motor_id in ids
                for register in registers
            ):
                _fault(
                    robot,
                    "incomplete original live health window",
                    side,
                    "Live_Health_Block",
                    {},
                    {
                        "read_started_at_monotonic_s": started,
                        "read_completed_at_monotonic_s": completed,
                    },
                )
        for register in registers:
            if _live_health:
                values = {
                    name: bus.sync_reader.getData(bus.motors[name].id, *table[register]) for name in names
                }
            else:
                if register != "Present_Temperature":
                    refresh_startup_temperatures(robot)
                    if (
                        getattr(robot, "_am1_teleop_startup_temperature", False)
                        and getattr(robot, "_lift_operation", None) is not None
                    ):
                        robot._refresh_am1_startup_lift_feedback()
                started = time.monotonic()
                values = bus.sync_read(register, names, normalize=False, num_retry=0)
                completed = time.monotonic()
            if set(values) != set(names):
                raise RuntimeError("AM1 teleop incomplete original register acquisition")
            rows[register] = {name: _integer(value) for name, value in values.items()}
            timing[register] = {
                "read_started_at_monotonic_s": started,
                "read_completed_at_monotonic_s": completed,
            }
            baseline_record = getattr(robot, "_am1_teleop_settings", {}).get(side, {})
            baseline = baseline_record.get("registers", rows)
            for name, value in rows[register].items():
                reason = None
                if register == "Status" and value != 0:
                    reason = "actual status fault: " + name
                elif register in ("Torque_Enable", "Lock") and active is not None and value != int(active):
                    reason = "actual torque/lock fault: " + name
                elif register == "Operating_Mode" and value != 0:
                    reason = "actual follower operating mode changed: " + name
                elif register == "Present_Temperature":
                    temperatures[name] = _temperature(
                        robot, side, bus, name, rows[register], timing[register],
                        baseline_record or {"registers": rows, "timing": timing}, acquired=True,
                    )
                elif (
                    register == "Present_Voltage"
                    and not baseline["Min_Voltage_Limit"][name]
                    <= value
                    <= baseline["Max_Voltage_Limit"][name]
                ):
                    reason = "actual configured voltage limit: " + name
                if reason is not None:
                    comparison = {"units": "raw_register"}
                    try:
                        configured = ()
                        if register == "Present_Temperature":
                            comparison.update(
                                lower_inclusive=0, upper_exclusive=baseline["Max_Temperature_Limit"][name]
                            )
                            configured = ("Max_Temperature_Limit",)
                        elif register == "Present_Voltage":
                            comparison.update(
                                lower_inclusive=baseline["Min_Voltage_Limit"][name],
                                upper_inclusive=baseline["Max_Voltage_Limit"][name],
                            )
                            configured = ("Min_Voltage_Limit", "Max_Voltage_Limit")
                        else:
                            comparison["expected"] = (
                                int(active) if register in ("Torque_Enable", "Lock") else 0
                            )
                        if configured:
                            baseline_timing = baseline_record.get("timing", timing)
                            comparison["configured"] = {
                                key: {"value": baseline[key][name], **baseline_timing.get(key, {})}
                                for key in configured
                            }
                    except Exception:
                        pass  # Optional comparator context cannot replace the proven guard.
                    _fault(
                        robot,
                        reason,
                        side,
                        register,
                        rows[register],
                        timing[register],
                        offending_motor=name,
                        comparison=comparison,
                    )
        records[side] = {"registers": rows, "timing": timing}
        if temperatures:
            records[side]["temperature"] = temperatures
    return records


def check_health(robot, records, *, active):
    refresh_startup_temperatures(robot)
    baseline = robot._am1_teleop_settings
    for side, bus, names in _groups(robot):
        rows, limits = records[side]["registers"], baseline[side]["registers"]
        for name in names:
            if (
                rows["Status"][name] != 0
                or rows["Torque_Enable"][name] != int(active)
                or rows["Lock"][name] != int(active)
            ):
                raise RuntimeError("AM1 teleop actual status/torque/lock fault: " + name)
            thermal = _temperature(
                robot, side, bus, name, rows["Present_Temperature"],
                records[side]["timing"]["Present_Temperature"], baseline[side], acquired=False,
            )
            window = robot._am1_teleop_temperatures[side, bus.motors[name].id]["window"]
            history_hold = (window.allow_history_hold is True and len(window.samples) == window.SIZE
                            and window.history_deficit_at is not None)
            if active and not thermal["ready"] and not history_hold:
                raise RuntimeError("AM1 teleop five fresh follower temperature slots are required: " + name)
            if (
                not limits["Min_Voltage_Limit"][name]
                <= rows["Present_Voltage"][name]
                <= limits["Max_Voltage_Limit"][name]
            ):
                raise RuntimeError("AM1 teleop actual configured voltage limit: " + name)


def _install_group_reply_guard(robot, side, bus):
    """Validate original AM1 group replies before the SDK can slice their data."""
    from lerobot.motors.feetech.tables import MODEL_CONTROL_TABLE

    binding = tuple(sorted(robot._am1_teleop_binding.items()))
    reader, ph, port = bus.sync_reader, bus.packet_handler, bus.port_handler
    mapping = tuple((name, motor.id, motor.model) for name, motor in bus.motors.items())
    arm_names = tuple(getattr(robot, side + "_arm_motors"))
    arm_ids = tuple(bus.motors[name].id for name in arm_names)
    installed = getattr(bus, "_am1_teleop_reply_guard", None)
    if installed is not None:
        if installed != (
            robot,
            binding,
            reader,
            ph,
            port,
            mapping,
            arm_names,
            reader.txRxPacket,
            ph.rxPacket,
            port.writePort,
        ):
            raise RuntimeError("AM1 teleop group reply guard owner changed")
        return
    original, original_rx, original_write = reader.txRxPacket, ph.rxPacket, port.writePort
    if (
        reader.ph is not ph
        or reader.port is not port
        or getattr(original, "__self__", None) is not reader
        or getattr(original, "__func__", None) is not getattr(type(reader), "txRxPacket", None)
        or getattr(original_rx, "__self__", None) is not ph
        or getattr(original_rx, "__func__", None) is not getattr(type(ph), "rxPacket", None)
        or getattr(original_write, "__self__", None) is not port
        or getattr(original_write, "__func__", None) is not getattr(type(port), "writePort", None)
    ):
        raise RuntimeError("AM1 teleop group reply reader already wrapped or differs")
    table = MODEL_CONTROL_TABLE["sts3215"]
    receipts = getattr(robot, "_am1_teleop_group_receipts", None)
    if receipts is None:
        receipts = robot._am1_teleop_group_receipts = {}

    def guarded():
        if getattr(robot, "_am1_teleop_first_error", None) is not None:
            raise robot._am1_teleop_first_error
        receipts.pop(side, None)  # Never attribute a previous register/transaction to this call.
        request, receipt = {"binding": dict(binding), "side": side}, None

        def refuse(reason, raw=None, timing=None):
            comparison = None
            with suppress(Exception):
                comparison = {"units": "raw_packet_bytes", "request": copy.deepcopy(request)}
            _fault(
                robot,
                reason,
                side,
                request.get("register", "sync_read"),
                raw or {},
                timing or {},
                comparison=comparison,
            )

        def owner_valid():
            try:
                return (
                    binding_valid(robot)
                    and tuple(sorted(robot._am1_teleop_binding.items())) == binding
                    and getattr(robot, side + "_bus") is bus
                    and bus.is_connected
                    and bus.sync_reader is reader
                    and bus.packet_handler is ph
                    and bus.port_handler is port
                    and reader.ph is ph
                    and reader.port is port
                    and tuple((name, motor.id, motor.model) for name, motor in bus.motors.items()) == mapping
                    and tuple(getattr(robot, side + "_arm_motors")) == arm_names
                    and reader.txRxPacket is guarded
                )
            except Exception:
                return False

        if not owner_valid() or ph.rxPacket != original_rx or port.writePort != original_write:
            refuse("group reply protected owner/binding changed")
        address, width, ids = reader.start_address, reader.data_length, tuple(reader.data_dict)
        request.update(address=address, width=width, ids=list(ids))
        id_names = {motor_id: name for name, motor_id, _model in mapping}
        live_health = (address, width) == (33, 38) and ids == arm_ids and len(ids) == 6
        if (
            type(address) is not int
            or type(width) is not int
            or not (live_health or ((address, width) in table.values() and width in (1, 2)))
            or not ids
            or len(ids) > min(10, len(mapping))
            or len(set(ids)) != len(ids)
            or len(id_names) != len(mapping)
            or any(type(motor_id) is not int or motor_id not in id_names for motor_id in ids)
        ):
            refuse("group reply request differs from the bound AM1 register/IDs")
        request.update(
            address=address,
            width=width,
            ids=list(ids),
            motors=[id_names[motor_id] for motor_id in ids],
            register="Live_Health_Block"
            if live_health
            else next(name for name, spec in table.items() if spec == (address, width)),
        )
        if live_health:
            request["registers"] = list(LIVE_HEALTH)
        receipt = receipts[side] = {
            "binding": dict(binding),
            "request": copy.deepcopy(request),
            "group_call_started_at_monotonic_s": time.monotonic(),
            "group_call_completed_at_monotonic_s": None,
            "comm_result": None,
            "packets": [],
            "transmit": None,
        }
        seen = set()

        def checked_write(packet):
            started = time.monotonic()
            raw = {}
            with suppress(Exception):
                raw = {"packet": list(packet[:64])}
            timing = {
                "read_started_at_monotonic_s": started,
                "read_completed_at_monotonic_s": time.monotonic(),
            }
            if (
                not owner_valid()
                or port.writePort is not checked_write
                or (reader.start_address, reader.data_length, tuple(reader.data_dict))
                != (address, width, ids)
            ):
                refuse("group reply transmit owner/request changed", raw, timing)
            if receipt["transmit"] is not None:
                refuse("group reply duplicate transmit", raw, timing)
            transmit = receipt["transmit"] = {
                "packet": raw.get("packet"),
                "write_started_at_monotonic_s": None,
                "write_completed_at_monotonic_s": None,
                "result": None,
            }
            expected = [255, 255, 254, len(ids) + 4, 130, address, width, *ids]
            expected.append((~sum(expected[2:])) & 255)
            if (
                type(packet) is not list
                or any(type(byte) is not int for byte in packet)
                or packet != expected
            ):
                refuse("group reply transmit differs from original request/frame/checksum", raw, timing)
            transmit["write_started_at_monotonic_s"] = time.monotonic()
            try:
                result = original_write(packet)
                transmit["result"] = result
                return result
            finally:
                transmit["write_completed_at_monotonic_s"] = time.monotonic()

        def checked_rx(*args, **kwargs):
            started = time.monotonic()
            if receipt["transmit"] is None:
                refuse(
                    "group reply missing original transmit",
                    timing={"read_started_at_monotonic_s": started, "read_completed_at_monotonic_s": started},
                )
            packet, result = original_rx(*args, **kwargs)
            completed = time.monotonic()
            timing = {"read_started_at_monotonic_s": started, "read_completed_at_monotonic_s": completed}
            raw = {}
            try:
                raw = {"packet": list(packet[:256]), "device_error": packet[4] if len(packet) > 4 else None}
                if len(receipt["packets"]) < 10:
                    receipt["packets"].append({**copy.deepcopy(raw), **timing, "comm_result": result})
            except Exception:
                pass  # Optional capture cannot replace a proven receive guard.
            if result != 0:
                return packet, result  # The SDK retains its original transport-failure behavior.
            if (
                not owner_valid()
                or port.writePort is not checked_write
                or (reader.start_address, reader.data_length, tuple(reader.data_dict))
                != (
                    address,
                    width,
                    ids,
                )
            ):
                refuse("group reply owner/request changed during acquisition", raw, timing)
            if (
                type(packet) is not list
                or len(packet) < 6
                or len(packet) > 256
                or any(type(byte) is not int or not 0 <= byte <= 255 for byte in packet)
                or packet[:2] != [255, 255]
                or packet[-1] != ((~sum(packet[2:-1])) & 255)
            ):
                refuse("group reply invalid frame/header/checksum", raw, timing)
            if packet[4] != 0:
                refuse("group reply actual device error: " + str(packet[4]), raw, timing)
            if packet[2] not in ids or packet[2] in seen:
                refuse("group reply unexpected or duplicate motor ID", raw, timing)
            if packet[3] != width + 2 or len(packet) != width + 6:
                refuse("group reply payload width differs from original request", raw, timing)
            seen.add(packet[2])
            return packet, result

        ph.rxPacket = checked_rx
        port.writePort = checked_write
        try:
            result = original()
            receipt["comm_result"] = result
            return result
        finally:
            ph.rxPacket = original_rx
            port.writePort = original_write
            receipt["group_call_completed_at_monotonic_s"] = time.monotonic()

    reader.txRxPacket = guarded
    bus._am1_teleop_reply_guard = (
        robot,
        binding,
        reader,
        ph,
        port,
        mapping,
        arm_names,
        guarded,
        original_rx,
        original_write,
    )


def prepare_settings(robot, *, defer_temperature_baseline=False):
    """Capture installed settings; only the selected OFF transaction may defer its first history."""
    if not binding_valid(robot):
        raise RuntimeError("AM1 teleop settings need the protected binding")
    if defer_temperature_baseline:
        if (sum(getattr(robot, "_am1_" + side + "_response_p_binding", None) is not None
                for side in ("left", "right")) != 1
                or getattr(robot, "_am1_teleop_temperatures", None)
                or hasattr(robot, "_am1_teleop_initial_temperature_deadline")):
            raise RuntimeError("AM1 selected initial thermal acquisition cannot be reused or reset")
        # Private original/configured durability consumes this same finite startup budget.
        robot._am1_teleop_initial_temperature_deadline = time.monotonic() + 1.0
        robot._am1_teleop_initial_temperature_binding = copy.deepcopy(robot._am1_teleop_binding)
        robot._am1_teleop_initial_temperature_pending = True
    for side, bus, _names in _groups(robot):
        _install_group_reply_guard(robot, side, bus)
    health = tuple(k for k in HEALTH if k != "Present_Temperature") if defer_temperature_baseline else HEALTH
    records = read_groups(robot, (*STATIC, *health), active=False)
    robot._am1_teleop_settings = records
    for side, bus, names in _groups(robot):
        rows = records[side]["registers"]
        for name in names:
            cal = robot._am1_activation_calibration(bus, name)
            expected = {
                "ID": cal[0],
                "Model_Number": 777,
                "Min_Position_Limit": cal[3],
                "Max_Position_Limit": cal[4],
                "Homing_Offset": cal[2],
                "Operating_Mode": 0,
            }
            if any(rows[key][name] != value for key, value in expected.items()):
                raise RuntimeError("AM1 teleop installed identity/calibration/mode differs: " + name)
            if (
                rows["Protection_Current"][name] <= 0
                or not 0 < rows["Max_Temperature_Limit"][name] <= 100
                or not 0 < rows["Min_Voltage_Limit"][name] < rows["Max_Voltage_Limit"][name]
            ):
                raise RuntimeError("AM1 teleop installed protection settings are invalid: " + name)
    if defer_temperature_baseline:
        check_initial_temperature_budget(robot)
        return
    check_health(robot, records, active=False)
    robot._am1_teleop_startup_temperature = True
    _qualify_initial_temperatures(robot, time.monotonic() + 1.0)


def check_initial_temperature_budget(robot):
    """The selected OFF preparation cannot restart its original qualification clock."""
    if (not binding_valid(robot)
            or getattr(robot, "_am1_shutdown_in_progress", False) is True
            or getattr(robot, "_am1_teleop_initial_temperature_pending", None) is not True
            or robot._am1_teleop_binding != robot._am1_teleop_initial_temperature_binding):
        raise RuntimeError("AM1 selected initial thermal acquisition owner/phase changed")
    if getattr(robot, "_am1_teleop_first_error", None) is not None:
        raise robot._am1_teleop_first_error
    deadline = robot._am1_teleop_initial_temperature_deadline
    if type(deadline) not in (int, float) or not math.isfinite(deadline) or time.monotonic() >= deadline:
        raise RuntimeError("AM1 teleop original follower baseline acquisition expired")
    return deadline


def qualify_startup_temperatures(robot):
    """First genuine follower histories after the selected transaction's private OFF work."""
    deadline = check_initial_temperature_budget(robot)
    if getattr(robot, "_am1_teleop_temperatures", None):
        raise RuntimeError("AM1 selected initial thermal acquisition cannot be reused or reset")
    records = read_groups(robot, ("Present_Temperature",), active=False)
    for side in records:
        for key in ("registers", "timing"):
            robot._am1_teleop_settings[side][key].update(records[side][key])
        robot._am1_teleop_settings[side]["temperature"] = records[side]["temperature"]
    robot._am1_teleop_startup_temperature = True
    _qualify_initial_temperatures(robot, deadline)
    check_initial_temperature_budget(robot)
    robot._am1_teleop_initial_temperature_pending = False


def _qualify_initial_temperatures(robot, deadline):
    while not all(state["window"].ready for state in robot._am1_teleop_temperatures.values()):
        if getattr(robot, "_am1_teleop_initial_temperature_pending", False):
            check_initial_temperature_budget(robot)
        if time.monotonic() >= deadline:
            raise RuntimeError("AM1 teleop original follower baseline acquisition expired")
        next_slot = min(
            (state["window"]._origin_ns / 1e9)
            + (state["window"]._last_slot + 1) * state["window"].SLOT_S
            for state in robot._am1_teleop_temperatures.values()
        )
        delay = next_slot - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        refresh_startup_temperatures(robot, force=True)
    _fresh_temperatures(robot)


def _response_receipt(robot, side):
    """Read-free proof for one explicitly admitted response comparison."""
    binding = getattr(robot, "_am1_" + side + "_response_p_binding", None)
    trial = getattr(robot, "_am1_" + side + "_response_p_trial", None)
    if binding is None and trial is None:
        return None
    from .am1_elbow_integral import AM1LeftResponseProportionalTrial, AM1RightResponseProportionalTrial

    other = "right" if side == "left" else "left"
    trial_type = AM1LeftResponseProportionalTrial if side == "left" else AM1RightResponseProportionalTrial
    if (not binding_valid(robot) or type(trial) is not trial_type or trial.binding != binding
            or getattr(robot, "_am1_" + other + "_response_p_binding", None) is not None
            or getattr(robot, "_am1_" + other + "_response_p_trial", None) is not None):
        raise RuntimeError(f"AM1 {side} response transaction binding changed")
    receipt = trial.active_receipt(robot)
    allowed = ("left-maintained", "left-reach", "left-peer-fixed") if side == "left" else ("right-peer-fixed",)
    if (receipt.get("kind") != side + "-response-p" or receipt.get("original_p") != 16
            or receipt.get("configured_p") not in (16, 32) or receipt.get("restore_required") is not True
            or receipt.get("workload_segment") not in allowed
            or any(receipt.get(key) != robot._am1_teleop_binding[key] for key in ("run_id", "host_incarnation"))):
        raise RuntimeError(f"AM1 {side} response active settings proof differs")
    return receipt


def left_response_receipt(robot):
    """Retain the existing left receipt API and exact receipt shape."""
    return _response_receipt(robot, "left")


def right_response_receipt(robot):
    return _response_receipt(robot, "right")


def response_receipt(robot):
    return left_response_receipt(robot) or right_response_receipt(robot)


def _left_response_target_valid(robot, control, sample):
    receipt = response_receipt(robot)
    if receipt is None:
        return True
    from examples.alohamini.am1_live_teleop import ZERO_BODY

    if not isinstance(sample.get("source"), dict) or sample["source"].get("kind") != "virtual-leaders":
        return False
    if set(sample["body"]) != set(ZERO_BODY) or any(sample["body"][key] != 0 for key in ZERO_BODY):
        return False
    workspace = control._live_summary["workspace"]
    side = "left" if receipt["kind"] == "left-response-p" else "right"
    allowed = {"arm_" + side + "_elbow_flex.pos"}
    if receipt["workload_segment"] == "left-reach":
        allowed.add("arm_left_gripper.pos")
    for key, reference in workspace["reference"].items():
        if key in allowed:
            continue
        cal = workspace["calibration"][key.removesuffix(".pos")]
        tick = (100 if key.endswith("_gripper.pos") else 200) / (cal[4] - cal[3])
        if abs(sample["target"][key] - reference) > tick + 1e-9:
            return False
    return True


def verify_settings(robot):
    records = read_groups(robot, (*STATIC, *HEALTH), active=True)
    expected = {side: {register: dict(robot._am1_teleop_settings[side]["registers"][register])
                       for register in STATIC} for side, _, _ in _groups(robot)}
    trial = response_receipt(robot)
    if trial is not None:
        side = "left" if trial["kind"] == "left-response-p" else "right"
        if trial["workload_segment"].endswith("-peer-fixed"):
            for role in ("left", "right"):
                name = "arm_" + role + "_elbow_flex"
                if expected[role]["P_Coefficient"][name] != 16:
                    raise RuntimeError("AM1 response both original elbow baselines differ")
        if expected[side]["P_Coefficient"]["arm_" + side + "_elbow_flex"] != 16:
            raise RuntimeError(f"AM1 {side} response original baseline differs")
        expected[side]["P_Coefficient"]["arm_" + side + "_elbow_flex"] = trial["configured_p"]
    if any(
        records[side]["registers"][register] != expected[side][register]
        for side, _, _ in _groups(robot)
        for register in STATIC
    ):
        raise RuntimeError("AM1 teleop installed settings changed during startup")
    check_health(robot, records, active=True)
    robot._am1_teleop_active_settings = records


def qualify_rest(robot, bus, name, calibration, *, phase, present_raw):
    """Acquire the supported shoulder rest without widening its command range.

    The ordinary half-degree initial inward bound may be explicitly selected as
    one degree by the retained current-rest action request. This operating bound
    does not change legal goals, calibration, health checks or other joint roles.
    """
    def refuse(reason):
        raise RuntimeError("AM1 arm activation refused: normal-rest " + reason)

    if (
        not binding_valid(robot)
        or bus is not robot.left_bus
        or name != "arm_left_shoulder_lift"
        or calibration != (2, 0, 515, 848, 3223)
        or bus.motors[name].id != 2
        or bus.motors[name].model != "sts3215"
        or phase not in {"before_home", "after_home"}
    ):
        refuse("protected role/mapping/calibration differs")
    binding = copy.deepcopy(robot._am1_teleop_binding)
    trial = robot._am1_current_rest_activation_trial()
    bound_degrees = 0.5 if trial is None else trial.normal_rest_startup_degrees(robot)
    allowance_request = None if trial is None else {
        "request_id": trial.binding["request"]["request_id"],
        "request_sha256": trial.binding["request_sha256"],
    }

    def check_allowance():
        current = robot._am1_current_rest_activation_trial()
        if (current is not trial
                or (0.5 if current is None else current.normal_rest_startup_degrees(robot)) != bound_degrees
                or current is not None and {
                    "request_id": current.binding["request"]["request_id"],
                    "request_sha256": current.binding["request_sha256"],
                } != allowance_request):
            refuse("startup allowance authority changed")

    baseline = robot._am1_teleop_settings["left"]["registers"]
    expected = {register: baseline[register][name] for register in STATIC}
    started = time.monotonic()
    deadline = started + 1.0
    proof = {
        "phase": phase,
        "binding": binding,
        "calibration": list(calibration),
        "operating_displacement_bound_degrees": bound_degrees,
        "startup_allowance_request": copy.deepcopy(allowance_request),
        "initial_measured_raw": present_raw,
        "qualified": False,
        "samples": [],
        "settings": {},
        "started_at": started,
    }
    robot._am1_teleop_rest_evidence.append(proof)

    def read(register):
        check_allowance()
        refresh_startup_temperatures(robot)
        began = time.monotonic()
        if not started <= began < deadline:
            refuse("acquisition deadline expired")
        value = robot._read_am1_activation_raw(bus, register, name, phase="normal_rest_" + phase, num_retry=0)
        ended = time.monotonic()
        check_allowance()
        if not began <= ended <= began + 0.04 or ended >= deadline:
            refuse("register read exceeded its original40ms budget")
        return value

    def seed_for(raw):
        check_allowance()
        if raw < calibration[3]:
            refuse("lower endpoint is outside the supported rest role")
        seed = max(calibration[3], min(calibration[4], raw))
        displacement = abs(raw - seed)
        cap = robot._am1_relative_target_cap(name + ".pos")
        if (
            type(cap) not in (int, float)
            or not math.isfinite(cap)
            or cap <= 0
            or displacement > math.floor(cap * (calibration[4] - calibration[3]) / 200)
            or displacement > math.floor(bound_degrees * bus.model_resolution_table["sts3215"] / 360)
        ):
            refuse("rest-to-legal-seed displacement exceeds the selected operating or measured-error bound")
        return seed

    try:
        if phase == "after_home" and not any(
            entry["phase"] == "before_home"
            and entry["qualified"]
            and entry["binding"] == binding
            and tuple(entry["calibration"]) == calibration
            and entry["operating_displacement_bound_degrees"] == bound_degrees
            and entry.get("startup_allowance_request") == allowance_request
            for entry in robot._am1_teleop_rest_evidence
        ):
            refuse("post-home endpoint needs its original before-home qualification")
        seed_for(present_raw)
        for register in (*STATIC, "CW_Dead_Zone", "CCW_Dead_Zone"):
            actual = proof["settings"][register] = read(register)
            if register in expected and actual != expected[register]:
                refuse("installed settings changed: " + register)
        if robot._am1_activation_calibration(bus, name) != calibration:
            refuse("calibration changed")
        old_goal = read("Goal_Position")
        # A stale torque-off goal is observed, not used as the new rest anchor.
        # Historical firmware could report the adjacent unrepresentable goal.
        seed_for(old_goal)
        positions, first_completed = [present_raw], None
        window = robot._am1_teleop_temperatures["left", bus.motors[name].id]["window"]
        while (
            len(proof["samples"]) < 9
            or round((proof["samples"][-1]["completed_at"] - first_completed) * 1e9) < 400_000_000
        ):
            if phase == "after_home":
                robot._refresh_am1_startup_lift_feedback()
            if first_completed is not None:
                delay = first_completed + len(proof["samples"]) * 0.05 - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
            refresh_startup_temperatures(robot)
            row = {"started_at": time.monotonic()}
            proof["samples"].append(row)
            for register in (
                "Torque_Enable",
                "Lock",
                "Operating_Mode",
                "Goal_Position",
                "Present_Position",
                "Present_Current",
                "Status",
                "Present_Temperature",
                "Present_Velocity",
                "Moving",
                "Present_Voltage",
            ):
                if register == "Present_Temperature":
                    stamp, _, value = robot._am1_teleop_temperatures["left", bus.motors[name].id]["last"]
                    row[register] = value
                    row["temperature_acquired_at"] = stamp
                    continue  # Already classified by the shared original group acquisition.
                value = row[register] = read(register)
                if register == "Present_Position":
                    seed_for(value)
                    positions.append(value)
                    valid = max(positions) - min(positions) <= 1
                elif register in {
                    "Torque_Enable",
                    "Lock",
                    "Operating_Mode",
                    "Status",
                    "Present_Velocity",
                    "Moving",
                }:
                    valid = value == 0
                elif register == "Goal_Position":
                    valid = value == old_goal
                elif register == "Present_Current":
                    valid = 0 <= value * 6.5 < min(150, expected["Protection_Current"] * 6.5)
                else:
                    valid = expected["Min_Voltage_Limit"] <= value <= expected["Max_Voltage_Limit"]
                if not valid:
                    refuse("off/quiet/health qualification failed: " + register)
            row["completed_at"] = time.monotonic()
            if first_completed is None:
                first_completed = row["completed_at"]
        if phase == "after_home":
            robot._refresh_am1_startup_lift_feedback()
        _fresh_temperatures(robot)
        if any(read(register) != value for register, value in proof["settings"].items()):
            refuse("installed settings changed during acquisition")
        if phase == "after_home":
            robot._refresh_am1_startup_lift_feedback()
        _fresh_temperatures(robot)
        if (
            not binding_valid(robot)
            or robot._am1_teleop_binding != binding
            or robot._am1_activation_calibration(bus, name) != calibration
        ):
            refuse("binding/calibration changed during acquisition")
        seed = seed_for(positions[-1])
        proof.update(
            qualified=True,
            seed_raw=seed,
            measured_raw=positions[-1],
            temperature_slots=list(window.samples),
            completed_at=time.monotonic(),
            window_s=round(proof["samples"][-1]["completed_at"] - first_completed, 6),
        )
        return seed
    except Exception as error:
        proof["first_cause"] = f"{type(error).__name__}: {error}"
        error.add_note("Original normal-rest acquisition: " + json.dumps(proof, separators=(",", ":")))
        raise


def hold_goals(robot):
    """Verify the literal current command map; holding never reseeds from PP."""
    if not binding_valid(robot):
        raise RuntimeError("AM1 teleop hold requires its protected identity")
    binding = copy.deepcopy(robot._am1_teleop_binding)
    calibrations = {
        name: robot._am1_activation_calibration(bus, name)
        for _, bus, names in _groups(robot)
        for name in names
    }
    records = read_groups(robot, ("Goal_Position",))
    for side, bus, names in _groups(robot):
        for name in names:
            calibration = calibrations[name]
            goal = records[side]["registers"]["Goal_Position"][name]
            if not calibration[3] <= goal <= calibration[4]:
                raise RuntimeError("AM1 teleop held raw goal exceeds unchanged calibration: " + name)
            if robot._am1_activation_calibration(bus, name) != calibration:
                raise RuntimeError("AM1 teleop held calibration changed: " + name)
    now = time.monotonic()
    if (
        not binding_valid(robot)
        or robot._am1_teleop_binding != binding
        or any(
            not 0
            <= group["timing"]["Goal_Position"]["read_started_at_monotonic_s"]
            <= group["timing"]["Goal_Position"]["read_completed_at_monotonic_s"]
            <= now
            or now - group["timing"]["Goal_Position"]["read_started_at_monotonic_s"]
            >= robot.AM1_ACTIVATION_MAX_RAW_AGE_S
            for group in records.values()
        )
    ):
        raise RuntimeError("AM1 teleop literal held-goal acquisition expired or identity changed")
    return records


def poll_health(robot):
    """Actual installed device limits; contact/nonresponse keep their role semantics."""
    binding = copy.deepcopy(robot._am1_teleop_binding)
    records = read_groups(robot, LIVE_HEALTH, active=True, _live_health=True)
    # The other bus can finish after a new anchored slot becomes due. Acquire
    # that slot once before consuming the original windows; never retry a
    # latched fault or refresh a stale last RAW receipt.
    states = robot._am1_teleop_temperatures
    now = time.monotonic()
    if any(
        (round(now * 1e9) - state["window"]._origin_ns) // round(state["window"].SLOT_S * 1e9)
        > state["window"]._last_slot
        for state in states.values()
    ):
        for state in states.values():
            state["window"].assert_fresh(now, refreshing=True)
        temperatures = read_groups(robot, ("Present_Temperature",), active=True)
        for side, fresh in temperatures.items():
            for key in ("registers", "timing"):
                records[side][key]["Present_Temperature"] = fresh[key]["Present_Temperature"]
            records[side]["temperature"] = fresh["temperature"]
    check_health(robot, records, active=True)
    for side, _, names in _groups(robot):
        if any(records[side]["registers"]["Operating_Mode"][name] != 0 for name in names):
            raise RuntimeError("AM1 teleop actual follower operating mode changed")
    if binding == robot._am1_teleop_binding:
        records["binding"] = binding
    robot._am1_teleop_health = records
    _report_temperature_warnings(robot)
    robot._am1_teleop_startup_temperature = False


def check_sampled_current(robot, side, values, started, completed, *, names=None):
    baseline = robot._am1_teleop_settings[side]
    limits = baseline["registers"]["Protection_Current"]
    if names is not None:
        names = tuple(names)
        if not names or len(set(names)) != len(names) or set(names) - set(limits) or set(values) != set(names):
            raise RuntimeError("AM1 sampled current motor subset differs from its frozen mapping")
        limits = {name: limits[name] for name in names}
    sample = {
        "raw": {name: _integer(values[name]) for name in limits},
        "read_started_at_monotonic_s": started,
        "read_completed_at_monotonic_s": completed,
    }
    samples = getattr(robot, "_am1_teleop_currents", {})
    samples[side] = sample
    robot._am1_teleop_currents = samples
    for name, raw in sample["raw"].items():
        if abs(raw) >= limits[name]:
            comparison = {"units": "raw_register"}
            try:
                comparison["absolute_upper_exclusive"] = limits[name]
                comparison["configured"] = {
                    "Protection_Current": {
                        "value": limits[name],
                        **baseline.get("timing", {}).get("Protection_Current", {}),
                    }
                }
            except Exception:
                pass  # Keep the actual current fault if optional timing is unavailable.
            _fault(
                robot,
                "actual configured current protection reached: " + name,
                side,
                "Present_Current",
                sample["raw"],
                {
                    key: sample[key]
                    for key in ("read_started_at_monotonic_s", "read_completed_at_monotonic_s")
                },
                offending_motor=name,
                comparison=comparison,
            )


def before_write(control, robot):
    # A legacy force limiter can catch read errors; the original hard fault still fences every later write.
    if getattr(robot, "_am1_teleop_first_error", None) is not None:
        raise robot._am1_teleop_first_error
    # Original input expiry is checked before validation and after collection.
    control.before_live_write(robot)
    if not binding_valid(robot) or robot._am1_teleop_binding != {**control.binding, "recipe": RECIPE}:
        raise RuntimeError("AM1 teleop protected write identity changed")
    for _, bus, names in _groups(robot):
        for name in names:
            if (
                list(robot._am1_activation_calibration(bus, name))
                != control._live_summary["workspace"]["calibration"][name]
            ):
                raise RuntimeError("AM1 teleop calibration changed before write: " + name)
    # Calibration work may consume history margin. Complete the bounded
    # whole-owner collection before the final all-role write permission; every
    # intermediate collection guard still checks original RAW freshness.
    if getattr(robot, "_am1_thermal_history_enabled", False):
        service_due_feedback(robot)
    if getattr(control, "live_sample", None) is not None and not _left_response_target_valid(robot, control, control.live_sample):
        raise RuntimeError("AM1 left response nonselected target or body changed before write")
    control.before_live_write(robot)  # A genuine read cannot renew input expiry.
    status = thermal_history_status(robot)
    if status is not None and status["deficit_started_at_monotonic_s"] is not None and (
        not status["peaks_ready"] or status["hold_sample_at_monotonic_s"] is None
        or time.monotonic() - status["hold_sample_at_monotonic_s"] > 0.5
    ):
        raise ThermalHistoryHoldRequiredError("AM1 thermal history needs a verified qualified hold")


def workspace(robot, identity, measured, *, goals=None):
    from examples.alohamini.am1_live_teleop import TELEOP_ARM_KEYS, normalized_raw, validate_workspace

    if not binding_valid(robot) or robot._am1_teleop_binding != {**identity, "recipe": RECIPE}:
        raise RuntimeError("AM1 teleop workspace binding differs")
    if set(TELEOP_ARM_KEYS) - set(measured):
        raise RuntimeError("AM1 teleop complete actual feedback is unavailable")
    result = {"version": 1, "units": {}, "bounds": {}, "reference": {}, "calibration": {}, "raw_bounds": {}}
    receipt = response_receipt(robot)
    profile = None
    if receipt is not None:
        side = "left" if receipt["kind"] == "left-response-p" else "right"
        trial = getattr(robot, "_am1_" + side + "_response_p_trial")
        profile = trial.binding["request"].get("workspace_profile")
        if profile is not None:
            if (side != "left" or profile != "left-current-rest-1051-1164"
                    or receipt["configured_p"] != 32 or receipt["workload_segment"] != "left-reach"):
                raise RuntimeError("AM1 current-rest workspace requires its exact active left trial")
            result["workspace_profile"] = profile
    if goals is None:
        goals = read_groups(robot, ("Goal_Position",))
    for side, bus, names in _groups(robot):
        for name in names:
            key, gripper = name + ".pos", name.endswith("_gripper")
            cal = robot._am1_activation_calibration(bus, name)
            if not bus.apply_drive_mode:
                raise RuntimeError("AM1 teleop calibrated direction application changed")
            seed = goals[side]["registers"]["Goal_Position"][name]
            if not cal[3] <= seed <= cal[4]:
                raise RuntimeError("AM1 teleop actual held goal is outside calibration")
            q0 = measured[key]
            if type(q0) not in (int, float) or not math.isfinite(q0):
                raise RuntimeError("AM1 teleop actual resting pose is invalid")
            if name.endswith("_elbow_flex"):
                low, high = ((1051, 1164) if profile is not None else (2011, 2124)) if side == "left" else (1847, 1960)
                if not low <= seed <= high:
                    raise RuntimeError("AM1 teleop actual elbow rest is outside supported bench workspace")
            else:
                distance = math.floor((20 * (cal[4] - cal[3]) / 100) if gripper else 10 * 4096 / 360)
                # Reuse the known-clear smoke direction, reversed at an upper endpoint.
                direction = (1 if q0 <= (80 if gripper else 97) else -1) * (-1 if cal[1] else 1)
                endpoint = max(cal[3], min(cal[4], seed + direction * distance))
                low, high = sorted((seed, endpoint))
            if not cal[3] <= low <= high <= cal[4]:
                raise RuntimeError("AM1 teleop bench workspace exceeds actual calibration")
            endpoints = sorted(normalized_raw(raw, cal, gripper=gripper) for raw in (low, high))
            rate = 20.0 if gripper else 5 * 4096 / 360 * 200 / (cal[4] - cal[3])
            result["units"][key] = "gripper_opening_percent" if gripper else "normalized_arm"
            result["bounds"][key] = {"min": endpoints[0], "max": endpoints[1], "rate_per_s": rate}
            result["reference"][key] = q0
            result["calibration"][name] = list(cal)
            result["raw_bounds"][key] = [low, high]
    return validate_workspace(result)


def apply_frame(control, robot, command, marker_key):
    """Validate one original frame, then reuse the ordinary protected executor."""
    from examples.alohamini.am1_live_teleop import (
        TELEOP_ARM_KEYS,
        ZERO_BODY,
        validate_body,
        validate_source,
        validate_target,
    )

    marker = command.get(marker_key)
    base = {"version", "mode", "epoch", "run_id", "host_incarnation", "intent_revision", "action_sequence"}
    if (
        not isinstance(marker, dict)
        or set(marker) != base | {"live_binding", "live_input", "live_admitted"}
        or marker.get("version") != 2
        or type(marker.get("live_admitted")) is not bool
        or any(marker.get(key) != value for key, value in (control.binding or {}).items())
        or any(
            type(marker.get(key)) is not int or marker[key] < 0
            for key in ("epoch", "intent_revision", "action_sequence")
        )
        or marker.get("mode") not in {"active", "pause"}
        or set(command) != {marker_key, *ZERO_BODY}
        or any(type(command[key]) not in (int, float) or command[key] != 0 for key in ZERO_BODY)
    ):
        return False
    if (
        marker["action_sequence"] <= control.accepted_action_sequence
        or marker["intent_revision"] < control.intent_revision
    ):
        return False
    owner = marker["live_binding"]
    if not isinstance(owner, dict) or set(owner) != {
        "service_incarnation",
        "controller_generation",
        "connection_generation",
    }:
        return False
    try:
        if any(type(value) is not str or str(UUID(value)) != value for value in owner.values()):
            return False
    except ValueError:
        return False
    if owner != control.live_binding and (
        marker["mode"] != "pause"
        or control.live_binding is not None
        and marker["intent_revision"] <= control.intent_revision
    ):
        return False
    if marker["mode"] == "active" and (
        marker["intent_revision"] != control.intent_revision
        or not (
            control.state == "active"
            and marker["epoch"] == control.epoch
            or control.state == "paused"
            and marker["epoch"] == control.epoch + 1
            and marker["epoch"] % 2 == 0
        )
    ):
        return False
    original = marker["live_input"]
    if marker["mode"] == "active" and not control.check_live(robot):
        # Enable the original admitted owner before its first active ACK.
        return False
    if getattr(robot, "_am1_thermal_history_enabled", False):
        service_due_feedback(robot)
    history = thermal_history_status(robot)
    if marker["mode"] == "pause" and original is not None:
        return False
    if not control.check_live(robot):
        return False
    if history is not None and history["deficit_started_at_monotonic_s"] is not None and original is not None:
        raise ThermalHistoryHoldRequiredError("AM1 thermal history revokes pending input before admission")
    if original is not None:
        if (
            not isinstance(original, dict)
            or set(original) != {"target", "body", "source", "grant_id", "seq", "valid_until_monotonic_s"}
            or type(original["seq"]) is not int
            or original["seq"] < 0
            or type(original["valid_until_monotonic_s"]) not in (int, float)
            or not math.isfinite(original["valid_until_monotonic_s"])
            or not control.clock() < original["valid_until_monotonic_s"] <= control.clock() + 0.25 + 1e-9
            or original["seq"] < control._live_summary["last_input_seq"]
            or original["seq"] == control._live_summary["last_input_seq"]
            and original != control.live_sample
        ):
            return False
        try:
            validate_target(original["target"], workspace=control._live_summary["workspace"])
            validate_body(original["body"])
            if not _left_response_target_valid(robot, control, original):
                return False
            if original != control.live_sample:
                validate_source(
                    original["source"], grant_id=original["grant_id"], previous=control._teleop_source
                )
        except (ValueError, KeyError):
            return False
    standard = {key: marker[key] for key in base}
    standard["version"] = 1
    action = {**ZERO_BODY, marker_key: standard}
    if marker["mode"] == "pause":
        accepted = control._apply_standard(robot, action)
        if not accepted:
            return False
        changed = owner != control.live_binding
        control.live_binding, control.live_sample = copy.deepcopy(owner), None
        if changed:
            control._live_summary["last_input_seq"] = -1
        measured = robot.get_observation()
        goals = read_groups(robot, ("Goal_Position",))
        held = {
            name + ".pos": value
            for side, bus, names in _groups(robot)
            for name, value in robot._normalize_arm_feedback(
                bus, goals[side]["registers"]["Goal_Position"]
            ).items()
        }
        literal = {side: dict(goals[side]["registers"]["Goal_Position"]) for side, _, _ in _groups(robot)}
        if (
            getattr(control, "_teleop_hold_epoch", None) == control.epoch
            and control._teleop_held_goals != literal
        ):
            raise RuntimeError("AM1 teleop exact raw held goals changed during hold")
        control._teleop_hold_epoch, control._teleop_held_goals = control.epoch, literal
        if "workspace" not in control._live_summary:
            control._live_summary["workspace"] = workspace(robot, control.binding, measured, goals=goals)
            control._live_summary["reference"] = dict(control._live_summary["workspace"]["reference"])
        if marker["live_admitted"] and "live_admitted_at_monotonic_s" not in control._live_summary:
            now = control.clock()
            control._live_summary.update(
                live_admitted_at_monotonic_s=now, live_deadline_monotonic_s=now + 300
            )
        control._live_summary.update(
            state="held",
            cursor=held,
            accepted_target=dict(held),
            body=dict(ZERO_BODY),
            held_goals_raw=literal,
            held_goal_acquisition=goals,
            cursor_feedback_sequence=control.observation_id,
            cursor_intent_revision=control.intent_revision,
        )
        control.live_cursor_at = control.clock()
        return True
    if "live_deadline_monotonic_s" not in control._live_summary:
        return False
    if original is None:
        # Readiness acknowledges an existing literal raw hold, never a synthetic frame.
        accepted = control._apply_standard(robot, action)
        if accepted and history is not None:
            # Actual owner work may consume the admission proof. Collect before
            # ACK capture, then qualify all thirteen original histories together.
            service_due_feedback(robot)
            if not control.check_live(robot):  # Collection cannot renew the original Live/input clocks.
                return False
            current = thermal_history_status(robot)
            if not current["peaks_ready"] or (
                current["deficit_started_at_monotonic_s"] is not None
                and current["hold_sample_at_monotonic_s"] is None
            ):
                raise ThermalHistoryHoldRequiredError("AM1 thermal history has not requalified")
            if current["deficit_started_at_monotonic_s"] is not None:
                robot._am1_thermal_history_episode = None
        return accepted
    if control.state == "paused" and control._live_summary["state"] == "expired":
        return False
    if control.state == "paused" and (
        any(original["body"].get(key, 0) != 0 for key in ZERO_BODY)
        or "lift_axis.height_mm" in original["body"]
    ):
        return False
    if original != control.live_sample:
        control.live_sample = copy.deepcopy(original)
        control._teleop_source = copy.deepcopy(original["source"])
        control._live_summary.update(
            last_input_seq=original["seq"],
            source=copy.deepcopy(original["source"]),
            grant_id=original["grant_id"],
            valid_until_monotonic_s=original["valid_until_monotonic_s"],
            accepted_at_monotonic_s=control.clock(),
        )
    elapsed = max(0, control.clock() - control.live_cursor_at)
    cursor = control._live_summary["cursor"]
    following = {
        key: cursor[key]
        + max(
            -elapsed * control._live_summary["workspace"]["bounds"][key]["rate_per_s"],
            min(
                elapsed * control._live_summary["workspace"]["bounds"][key]["rate_per_s"],
                original["target"][key] - cursor[key],
            ),
        )
        for key in TELEOP_ARM_KEYS
    }
    action.update(following)
    for key in ZERO_BODY:
        action.pop(key, None)
    action.update(original["body"])
    robot._am1_live_before_write = lambda: before_write(control, robot)
    accepted = control._apply_standard(robot, action)
    if accepted:
        control._live_summary.update(
            state="active",
            cursor=following,
            accepted_target={key: (control.accepted_action or {}).get(key) for key in TELEOP_ARM_KEYS},
            body=copy.deepcopy(original["body"]),
            write_acknowledged=False,
        )
        control.live_cursor_at = control.clock()
    return accepted
