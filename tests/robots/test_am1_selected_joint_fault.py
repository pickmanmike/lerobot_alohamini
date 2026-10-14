"""Exact rejected selected samples survive ordinary protected host cleanup."""

import copy
import json
import stat
from types import SimpleNamespace

import pytest

from lerobot.robots.alohamini import alohamini as robot_module, alohamini_host as host
from tests.robots.test_alohamini_lift_motor_feedback import status
from tests.robots.test_am1_selected_joint_evidence import (
    attach_port,
    binding,
    command,
    context,
    group_payload,
    observe,
    visible_guard,
    visible_register_packet,
    visible_robot,
)


@pytest.mark.parametrize("raw,ordinary_limit,threshold", [(16, 2000.0, 100.0), (-16, 2000.0, 100.0), (14, 80.0, 80.0)])
def test_current_refusal_retains_exact_sample_before_any_emit(monkeypatch, raw, ordinary_limit, threshold):
    obj, evidence, control, clock, sample, port = visible_guard(monkeypatch)
    evidence.context = context(progress_s=5.9, phase="outbound", cycle=1, requested_target=1.0, sample_key=None)
    obj._am1_selected_joint_action = {"expected_goal_raw": 2040, "sync_write_returned": True, "write_acknowledged": False}
    sample(2000, raw)
    monkeypatch.setattr(robot_module.time, "monotonic", lambda: clock[0])
    original_read = obj.left_bus._sync_read

    def read(address, length, ids, **kwargs):
        return (dict.fromkeys(ids, raw), 0) if address == 69 else original_read(address, length, ids, **kwargs)

    monkeypatch.setattr(obj.left_bus, "_sync_read", read)
    original = {"raw": raw, "ma": raw * 6.5, "read_started_at_monotonic_s": 1.0, "read_completed_at_monotonic_s": 1.0}
    evidence.emit = lambda row: pytest.fail("fault evidence emitted before protection")
    before = len(port.writes)
    with pytest.raises(RuntimeError, match="effort"):
        obj.read_and_check_currents(limit_ma=ordinary_limit, print_currents=False)
    fault = getattr(obj, "_am1_selected_joint_fault", None)
    assert isinstance(fault, dict), "Rejected raw current was lost before cleanup"
    assert fault["current"] == original
    assert fault["source"] == "ordinary_current" and fault["effective_limit_ma"] == threshold
    assert fault["diagnostic_current_cap_ma"] == 100.0 and fault["ordinary_limit_ma"] == ordinary_limit
    assert fault["position"]["raw"] == 2000 and fault["action"]["expected_goal_raw"] == 2040
    obj._am1_selected_joint_current["raw"] = 0
    obj._am1_selected_joint_position["raw"] = 999
    obj._am1_selected_joint_action["expected_goal_raw"] = 999
    evidence.context["progress_s"] = 99.0
    assert fault["current"] == original and fault["context"]["progress_s"] == 5.9
    assert fault["position"]["raw"] == 2000 and fault["action"]["expected_goal_raw"] == 2040
    assert len(port.writes) == before
    with pytest.raises(RuntimeError, match="effort"):
        evidence.check_visible_current(obj, 32, ordinary_limit)
    assert obj._am1_selected_joint_fault is fault and fault["current"] == original


@pytest.mark.parametrize("sink_error", [False, True])
@pytest.mark.parametrize("shutdown_sink_error", [False, True])
def test_host_cleanup_precedes_fault_emit_and_receipt_keeps_trigger(monkeypatch, tmp_path, sink_error, shutdown_sink_error):
    obj, evidence, _, _, _, port = visible_guard(monkeypatch)
    identity = {k: obj._am1_visible_control_binding[k] for k in ("run_id", "host_incarnation")}
    events, rows = [], []
    shutdown_output = []

    def shutdown_print(*args, **kwargs):
        shutdown_output.append(list(events))
        if shutdown_sink_error:
            raise OSError("shutdown sink unavailable")

    monkeypatch.setattr(host, "print", shutdown_print, raising=False)
    now = SimpleNamespace(value=10.0)
    clock = SimpleNamespace(
        monotonic=lambda: now.value,
        perf_counter=lambda: now.value,
        time_ns=lambda: int(now.value * 1e9),
        sleep=lambda seconds: setattr(now, "value", now.value + seconds),
    )
    evidence.clock = clock.monotonic

    def emit(row):
        assert events == ["robot-cleanup", "socket-close"]
        rows.append(copy.deepcopy(row))
        if sink_error:
            raise OSError("fault sink unavailable")

    evidence.emit = emit
    original_read = obj.left_bus._sync_read

    def read(address, length, ids, **kwargs):
        if address == 69:
            return dict.fromkeys(ids, 16), 0
        return original_read(address, length, ids, **kwargs)

    monkeypatch.setattr(obj.left_bus, "_sync_read", read)
    monkeypatch.setattr(host, "AM1SelectedJointEvidence", lambda **kw: evidence)
    monkeypatch.setattr(host, "AlohaMini", lambda _: obj)
    monkeypatch.setattr(host, "connect_robot", lambda *args, **kw: None)
    monkeypatch.setattr(host, "visible_control_binding", lambda args: obj._am1_visible_control_binding)
    monkeypatch.setenv("AM1_SELECTED_JOINT_EVIDENCE", "1")
    monkeypatch.setattr(host, "time", clock)
    monkeypatch.setattr(robot_module, "time", clock)

    def cleanup(**kwargs):
        events.append("robot-cleanup")
        obj._am1_selected_joint_current["raw"] = 0
        obj._am1_selected_joint_position["raw"] = 999
        obj._am1_shutdown_evidence = {"verified": True, "buses_closed": True, "cleanup_errors": []}

    obj.disconnect = cleanup
    metadata = context(
        profile="LeftElbowPositiveControl", progress_s=5.9, phase="outbound", cycle=1,
        reference=0.0, endpoint_target=10.0, requested_target=3.9, sample_key=None,
        acquisition_s=0.0, admitted_reference=0.0,
    )

    class Socket:
        def recv_string(self, flags):
            return json.dumps(command(identity, metadata, sequence=7))

        def recv_multipart(self, flags):
            pytest.fail("Rejected observation reached response polling")

    class Owner:
        watchdog_timeout_ms, connection_time_s, max_loop_freq_hz = 1000, 0.1, 30

        def __init__(self, _):
            self.zmq_cmd_socket = self.zmq_observation_socket = Socket()

        def disconnect(self):
            events.append("socket-close")

    monkeypatch.setattr(host, "AlohaMiniHost", Owner)
    args = host.make_parser().parse_args(["--no_cameras", "--skip_lift_home"])
    args.am1_run_id, args.am1_host_incarnation = identity["run_id"], identity["host_incarnation"]
    args.am1_cleanup_receipt = str(tmp_path / "cleanup.json")
    args.am1_physical_state_directory = str(tmp_path)
    args.profile_cadence = True
    with pytest.raises(RuntimeError, match="effort") as caught:
        host._run_host(args)
    receipt = host.write_am1_cleanup_receipt(args, obj, caught.value, args._am1_cleanup_errors)
    fault = receipt.get("selected_joint_fault")
    assert isinstance(fault, dict), "Cleanup receipt dropped the pre-cleanup trigger"
    assert shutdown_output and all(order == ["robot-cleanup", "socket-close"] for order in shutdown_output)
    assert events == ["robot-cleanup", "socket-close"] and len(rows) == 1
    assert fault == rows[0]
    assert fault["current"]["raw"] == 16 and fault["current"]["ma"] == 104.0
    assert fault["position"]["raw"] == 2000
    assert fault["control"]["accepted_action_sequence"] == 7
    assert fault["control"]["last_completed_observation_id"] == 0
    assert receipt["first_cause"] == "RuntimeError: Visible selected software effort ceiling reached"
    assert json.loads((tmp_path / "cleanup.json").read_text())["selected_joint_fault"] == fault
    assert len(port.writes) == 4  # Only the fixture's original stopped/active settings reads.
    if sink_error:
        assert "pending selected joint fault" in " ".join(caught.value.__notes__)
    if shutdown_sink_error:
        assert "shutdown reporting also failed" in " ".join(caught.value.__notes__)


@pytest.mark.parametrize("active", [False, True])
def test_settings_current_trip_retains_first_group_and_skips_next_read(monkeypatch, active):
    packets = []
    if active:
        packets.extend([status(visible_register_packet(), motor_id=3), status(bytes([91]), motor_id=3)])
    packets.extend([
        status(visible_register_packet(torque=int(active), current=16), motor_id=3),
        status(bytes([91]), motor_id=3),
    ])
    obj, evidence, rows, identity, port, _, _ = visible_robot(monkeypatch, packets)
    clock = [10.0]

    def tick():
        clock[0] += 0.01
        return clock[0]

    evidence.clock = tick
    if active:
        evidence.prepare_visible(obj, identity)
    with pytest.raises(RuntimeError, match="effort"):
        evidence.verify_visible_active(obj) if active else evidence.prepare_visible(obj, identity)
    fault = obj._am1_selected_joint_fault
    assert fault["source"] == ("settings_active" if active else "settings_before")
    assert fault["current"]["raw"] == 16 and fault["current"]["ma"] == 104.0
    assert fault["current"]["read_completed_at_monotonic_s"] - fault["current"]["read_started_at_monotonic_s"] == pytest.approx(0.01)
    assert fault["position"]["raw"] == 2000
    assert len(port.writes) == (3 if active else 1)
    assert len(rows) == int(active)  # Rejected settings never wait for their log sink.


def test_sparse_current_trip_is_pending_until_cleanup_and_preserves_group_sample(monkeypatch):
    obj, evidence, _, _, _, _ = visible_guard(monkeypatch)
    identity = {k: obj._am1_visible_control_binding[k] for k in ("run_id", "host_incarnation")}
    control = host.AM1LocalControl(**identity, selected_evidence=evidence)
    metadata = context(
        profile="LeftElbowPositiveControl", reference=0.0, endpoint_target=10.0,
        acquisition_s=0.0, admitted_reference=0.0,
    )
    control.apply(obj, command(identity, metadata))
    observation = observe(obj, control)
    evidence.clock = lambda: observation[host.AM1_LOCAL_FEEDBACK_KEY]["acquired_at_monotonic_s"] + 0.01
    port = attach_port(obj, [status(group_payload(current=16, goal_time=0, goal_velocity=0, acceleration=77, lock=1), motor_id=3)])
    rows = []
    evidence.emit = rows.append
    with pytest.raises(RuntimeError, match="effort"):
        evidence.report(obj, control, observation)
    assert not [row for row in rows if row["kind"] == "sparse_readback"]
    fault = obj._am1_selected_joint_fault
    assert fault["source"] == "sparse_readback" and fault["current"]["raw"] == 16
    assert fault["current"]["ma"] == 104.0 != obj._am1_selected_joint_current["ma"]
    assert fault["last_completed_live_goal"]["goal_position_raw"] == 2000
    evidence.emit_pending_fault(obj)
    assert len([row for row in rows if row["kind"] == "sparse_readback"]) == 1
    assert len(port.writes) == 1


def experiment():
    from examples.alohamini.am1_session_contract import (
        SELECTED_ELBOW_EXPERIMENT_ENVELOPE_SHA256,
        SelectedElbowExperiment,
    )

    return SelectedElbowExperiment.from_payload({
        "schema_version": 1, "envelope_sha256": SELECTED_ELBOW_EXPERIMENT_ENVELOPE_SHA256,
        "excursion_norm": -10.0, "ramp_s": 10.0, "endpoint_dwell_s": 2.0,
        "current_cap_ma": 100.0, "live_ceiling_s": 60.0,
        "minimum_response_raw": 56, "return_tolerance_raw": 20,
        "goal_readback_checkpoints_s": [4.0, 5.0],
    })


def experiment_context(definition, progress):
    cycle, phase, fraction = definition.phase(progress)
    return context(
        profile=definition.profile, progress_s=progress, phase=phase, cycle=cycle,
        reference=0.0, endpoint_target=-10.0, requested_target=-10.0 * fraction,
        sample_key=definition.sample_key(progress), acquisition_s=0.0, admitted_reference=0.0,
        experiment_sha256=definition.sha256,
    )


def experiment_robot(monkeypatch):
    obj, evidence, rows, identity, port, reads, writes = visible_robot(monkeypatch, [
        status(visible_register_packet(), motor_id=3), status(bytes([91]), motor_id=3),
        status(visible_register_packet(torque=1), motor_id=3), status(bytes([91]), motor_id=3),
    ])
    definition = experiment()
    obj._am1_selected_elbow_experiment = definition
    identity.update(recipe=definition.recipe_name, experiment_sha256=definition.sha256)
    evidence.prepare_visible(obj, identity)
    evidence.verify_visible_active(obj)
    control = host.AM1LocalControl(**{k: identity[k] for k in ("run_id", "host_incarnation")}, selected_evidence=evidence)
    return obj, evidence, control, definition


def test_new_experiment_checkpoints_are_once_only_and_separate_from_phase_receipts(monkeypatch):
    obj, evidence, control, definition = experiment_robot(monkeypatch)
    rows = []
    evidence.emit = rows.append
    port = attach_port(obj, [
        status(group_payload(goal=1980, current=2, goal_time=0, goal_velocity=0, acceleration=77, lock=1), motor_id=3),
        status(group_payload(goal=1970, current=2, goal_time=0, goal_velocity=0, acceleration=77, lock=1), motor_id=3),
    ])
    for sequence, progress in enumerate((3.99, 4.0, 4.5, 5.0, 5.5), 1):
        control.apply(obj, command(control.binding, experiment_context(definition, progress), sequence=sequence))
        observation = observe(obj, control)
        evidence.report(obj, control, observation)
    probes = [row for row in rows if row["kind"] == "sparse_readback"]
    assert [row["checkpoint_s"] for row in probes] == [4.0, 5.0]
    assert [row["decoded"]["goal_position_raw"] for row in probes] == [1980, 1970]
    assert evidence.sparse_receipts == {} and len(port.writes) == 2
    evidence.publish(obj, control, observation)
    note = observation[host.AM1_LOCAL_FEEDBACK_KEY]["selected_joint"]
    assert len(note["goal_readbacks"]) == 2 and note["sparse_receipts"] == {}


def test_new_negative_experiment_guard_accepts_declared_direction_and_rejects_reverse(monkeypatch):
    obj, evidence, control, definition = experiment_robot(monkeypatch)
    for sequence, (progress, raw) in enumerate(((2.0, 2000), (3.0, 1990), (4.0, 1980)), 1):
        control.apply(obj, command(control.binding, experiment_context(definition, progress), sequence=sequence))
        observe(obj, control)
        obj._am1_selected_joint_position["raw"] = raw
        evidence.guard_visible(obj, control)
    obj._am1_selected_joint_position["raw"] = 1984
    with pytest.raises(RuntimeError, match="direction"):
        evidence.guard_visible(obj, control)


def test_new_host_binding_reads_exact_private_run_owned_definition(monkeypatch, tmp_path):
    definition = experiment()
    path = tmp_path / "selected-experiment.json"
    path.write_text(json.dumps(definition.payload, allow_nan=False, sort_keys=True, separators=(",", ":")))
    args = host.make_parser().parse_args(["--no_cameras"])
    identity = binding()
    args.am1_run_id, args.am1_host_incarnation = identity["run_id"], identity["host_incarnation"]
    args.am1_protected_run_directory = str(tmp_path)
    args.am1_selected_experiment_file = str(path)
    monkeypatch.setenv("AM1_VISIBLE_CONTROL_RECIPE", definition.recipe_name)
    monkeypatch.setenv("AM1_SCRIPTED_PREPARE", "1")
    monkeypatch.setenv("AM1_SELECTED_JOINT_EVIDENCE", "1")
    for name in ("AM1_ELBOW_RECOVERY", "AM1_ELBOW_RECOVERY_RECIPE", "AM1_SHOULDER_INTEGRAL_TEST"):
        monkeypatch.delenv(name, raising=False)
    actual_stat = host.os.fstat

    def private_stat(fd):
        result = actual_stat(fd)
        return SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_size=result.st_size, st_uid=123)

    monkeypatch.setattr(host.os, "fstat", private_stat)
    monkeypatch.setattr(host.os, "geteuid", lambda: 123, raising=False)
    assert host.visible_control_binding(args) == {
        **identity, "recipe": definition.recipe_name, "experiment_sha256": definition.sha256,
    }
    assert args._am1_selected_elbow_experiment == definition


@pytest.mark.parametrize("bad", ["permission", "owner", "oversize", "foreign_directory", "noncanonical", "duplicate_key"])
def test_new_host_experiment_file_refuses_invalid_native_boundary(monkeypatch, tmp_path, bad):
    definition = experiment()
    directory = tmp_path / "foreign" if bad == "foreign_directory" else tmp_path
    directory.mkdir(exist_ok=True)
    path = directory / "selected-experiment.json"
    data = json.dumps(definition.payload, allow_nan=False, sort_keys=True, separators=(",", ":"))
    if bad == "noncanonical":
        data += "\n"
    if bad == "duplicate_key":
        data = '{"schema_version":1,' + data[1:]
    path.write_text(data)
    args = host.make_parser().parse_args(["--no_cameras"])
    identity = binding()
    args.am1_run_id, args.am1_host_incarnation = identity["run_id"], identity["host_incarnation"]
    args.am1_protected_run_directory, args.am1_selected_experiment_file = str(tmp_path), str(path)
    monkeypatch.setenv("AM1_VISIBLE_CONTROL_RECIPE", definition.recipe_name)
    monkeypatch.setenv("AM1_SCRIPTED_PREPARE", "1")
    monkeypatch.setenv("AM1_SELECTED_JOINT_EVIDENCE", "1")
    for name in ("AM1_ELBOW_RECOVERY", "AM1_ELBOW_RECOVERY_RECIPE", "AM1_SHOULDER_INTEGRAL_TEST"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(host.os, "fstat", lambda fd: SimpleNamespace(
        st_mode=stat.S_IFREG | (0o640 if bad == "permission" else 0o600),
        st_size=4097 if bad == "oversize" else len(data), st_uid=124 if bad == "owner" else 123,
    ))
    monkeypatch.setattr(host.os, "geteuid", lambda: 123, raising=False)
    with pytest.raises(ValueError):
        host.visible_control_binding(args)
    assert not hasattr(args, "_am1_selected_elbow_experiment")


def test_crossed_experiment_checkpoint_is_not_retried_after_missing_reply(monkeypatch):
    obj, evidence, control, definition = experiment_robot(monkeypatch)
    evidence.emit = lambda row: None
    port = attach_port(obj, [])
    for sequence, progress in enumerate((4.1, 4.2, 5.1, 5.2), 1):
        control.apply(obj, command(control.binding, experiment_context(definition, progress), sequence=sequence))
        evidence.report(obj, control, observe(obj, control))
    assert len(port.writes) == 2 and len(evidence.goal_readbacks) == 2
    assert all(row["diagnostic_available"] is False for row in evidence.goal_readbacks.values())
    assert all(row["trace"]["reply_timeout_ms"] == 40.0 for row in evidence.goal_readbacks.values())


def test_current_trip_does_not_serialize_before_protection(monkeypatch):
    obj, evidence, _, _, sample, _ = visible_guard(monkeypatch)
    sample(2000, 16)

    def forbidden(*args, **kwargs):
        pytest.fail("Protective selected capture performed serialization")

    monkeypatch.setattr(host.json, "dumps", forbidden)
    with pytest.raises(RuntimeError, match="effort"):
        evidence.check_visible_current(obj, 16, 2000.0)
    assert obj._am1_selected_joint_fault["current"]["raw"] == 16


@pytest.mark.parametrize("sink_error", [False, True])
def test_interrupted_host_also_cleans_before_exit_output(monkeypatch, sink_error):
    obj, evidence, _, _, _, _ = visible_guard(monkeypatch)
    events, output = [], []
    clock = SimpleNamespace(monotonic=lambda: 10.0, perf_counter=lambda: 10.0, time_ns=lambda: 10_000_000_000)
    monkeypatch.setattr(host, "time", clock)
    monkeypatch.setattr(host, "AM1SelectedJointEvidence", lambda **kwargs: evidence)
    monkeypatch.setattr(host, "AlohaMini", lambda _: obj)
    monkeypatch.setattr(host, "connect_robot", lambda *args, **kwargs: None)
    monkeypatch.setattr(host, "visible_control_binding", lambda args: None)
    obj.disconnect = lambda **kwargs: events.append("robot-cleanup")

    def interrupted():
        raise KeyboardInterrupt

    obj.get_observation = interrupted

    def report(*args, **kwargs):
        output.append(list(events))
        if sink_error:
            raise OSError("shutdown sink unavailable")

    monkeypatch.setattr(host, "print", report, raising=False)

    class Socket:
        def recv_string(self, flags):
            raise host.zmq.Again()

    class Owner:
        watchdog_timeout_ms, connection_time_s, max_loop_freq_hz = 1000, 0.1, 30

        def __init__(self, _):
            self.zmq_cmd_socket = self.zmq_observation_socket = Socket()

        def disconnect(self):
            events.append("socket-close")

    monkeypatch.setattr(host, "AlohaMiniHost", Owner)
    args = host.make_parser().parse_args(["--no_cameras", "--skip_lift_home"])
    if sink_error:
        with pytest.raises(OSError, match="shutdown sink"):
            host._run_host(args)
    else:
        host._run_host(args)
    assert output and all(order == ["robot-cleanup", "socket-close"] for order in output)
