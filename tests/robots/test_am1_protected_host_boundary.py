"""Protected AM1 host boundaries with mock hardware; never opens servo devices."""

import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import zmq

from lerobot.robots.alohamini import alohamini_host as host
from lerobot.robots.alohamini.config_alohamini import AlohaMiniHostConfig
from tests.robots.test_alohamini_safe_bringup import make_activation_robot


def binding():
    return {"run_id": str(uuid4()), "host_incarnation": str(uuid4())}


class Motor:
    def __init__(self):
        self.events = []

    def send_action(self, action):
        self.events.append(("action", dict(action)))
        return dict(action)

    def stop_motion(self):
        self.events.append(("zero",))

    def hold_follower_arms(self):
        self.events.append(("hold",))


def command(identity, *, mode="active", epoch=0, revision=1):
    return {
        "x.vel": 0.0,
        host.AM1_LOCAL_CONTROL_KEY: {
            "version": 1,
            "mode": mode,
            "epoch": epoch,
            **identity,
            "intent_revision": revision,
            "action_sequence": revision,
        },
    }


def test_protected_host_rejects_foreign_or_unmarked_before_motor_effects():
    identity = binding()
    control = host.AM1LocalControl(**identity)
    robot = Motor()
    for bad in ({"x.vel": 0.0}, command(binding()), command(identity, revision=-1)):
        with pytest.raises(RuntimeError):
            control.apply(robot, bad)
    assert robot.events == []
    assert control.apply(robot, command(identity))
    assert robot.events == [("action", {"x.vel": 0.0})]


def test_protected_pause_ack_fences_stale_resume_and_preserves_sample_clock():
    identity = binding()
    control = host.AM1LocalControl(**identity)
    robot = Motor()
    control.apply(robot, command(identity))
    control.apply(robot, command(identity, mode="pause", epoch=1, revision=2))
    observation = {}
    control.annotate(observation, acquired_at=123.0)
    ack = observation[host.AM1_LOCAL_FEEDBACK_KEY]
    assert ack == {
        "version": 1,
        "state": "paused",
        "epoch": 1,
        "observation_id": 1,
        **identity,
        "intent_revision": 2,
        "acquired_at_monotonic_s": 123.0,
        "accepted_action_sequence": 2,
        "accepted_action": {"mode": "measured_arm_hold", "body_velocity": 0.0},
        "write_acknowledged": False,
    }
    events = list(robot.events)
    assert not control.apply(robot, command(identity, epoch=0))
    assert robot.events == events
    stale = command(identity, epoch=2, revision=1)
    stale[host.AM1_LOCAL_CONTROL_KEY]["action_sequence"] = 3
    with pytest.raises(RuntimeError, match="revision"):
        control.apply(robot, stale)
    assert control.apply(robot, command(identity, epoch=2, revision=3))
    assert robot.events[-1] == ("action", {"x.vel": 0.0})


def test_legacy_local_marker_contract_remains_unchanged():
    control, robot = host.AM1LocalControl(), Motor()
    assert control.apply(robot, {host.AM1_LOCAL_CONTROL_KEY: {"version": 1, "mode": "active", "epoch": 0}})
    observation = {}
    control.annotate(observation)
    assert set(observation[host.AM1_LOCAL_FEEDBACK_KEY]) == {"version", "state", "epoch", "observation_id"}


def test_protected_cleanup_reads_all_motors_before_bus_close_and_is_not_exit_proof():
    robot, left, right, events = make_activation_robot()
    robot._am1_protected_cleanup = True
    assert not robot._safe_shutdown(close_buses=True)
    evidence = robot._am1_shutdown_evidence
    assert evidence["verified"]
    torque_rows = [r for r in evidence["readbacks"] if r["register"] == "Torque_Enable"]
    assert len(torque_rows) == len(left.motors) + len(right.motors)
    assert all(r["value"] == 0 for r in torque_rows)
    body_rows = [r for r in evidence["readbacks"] if r["register"] == "Goal_Velocity"]
    assert {r["motor"] for r in body_rows} == {*robot.base_motors, "lift_axis"}
    first_close = next(i for i, e in enumerate(events) if e[1] == "disconnect")
    assert all(e[1] != "read" for e in events[first_close:])


def test_protected_cleanup_partial_read_is_unknown_and_still_closes_buses():
    robot, left, right, events = make_activation_robot()
    robot._am1_protected_cleanup = True
    left.read_sequences["Torque_Enable", "arm_left_gripper"] = [ConnectionError("lost read")]
    errors = robot._safe_shutdown(close_buses=True)
    assert any("readback" in e for e in errors)
    assert not robot._am1_shutdown_evidence["verified"]
    assert any(
        r.get("error") == "ConnectionError: lost read" for r in robot._am1_shutdown_evidence["readbacks"]
    )
    assert not left.is_connected and not right.is_connected
    assert any(r["motor"] == "arm_right_gripper" for r in robot._am1_shutdown_evidence["readbacks"])


@pytest.mark.skipif(os.name == "nt", reason="AM1 physical arbitration is native Pi flock")
def test_direct_and_inherited_admission_contend_before_construction(tmp_path):
    state = tmp_path / "state"
    first = host.AM1PhysicalAdmission(state)
    try:
        with pytest.raises(RuntimeError, match="owner"):
            host.AM1PhysicalAdmission(state)
        # Reuse the inherited open-file description, never contend with its parent.
        with pytest.raises(RuntimeError, match="serial"):
            host.AM1PhysicalAdmission(state, inherited_fd=first.canonical.fileno())
    finally:
        first.close()
    owner = host.AM1PhysicalAdmission(state)
    owner.close()


@pytest.mark.skipif(os.name == "nt", reason="AM1 physical arbitration is native Pi flock")
def test_admission_rejects_unlocked_foreign_fd_and_uncertain_cleanup(tmp_path):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    with (state / "active.lock").open("a+b") as unlocked, pytest.raises(RuntimeError, match="inherited"):
        host.AM1PhysicalAdmission(state, inherited_fd=unlocked.fileno())
    (state / "cleanup-uncertain.json").write_text("{}")
    with pytest.raises(RuntimeError, match="reconciliation"):
        host.AM1PhysicalAdmission(state)


@pytest.mark.skipif(os.name == "nt", reason="real filesystem IPC is native Pi transport")
def test_actual_protected_transport_uses_private_sockets_and_foreign_command_has_no_effect(tmp_path):
    directory = tmp_path / "run"
    directory.mkdir(mode=0o700)
    config = AlohaMiniHostConfig(protected_run_directory=str(directory))
    motor_host = host.AlohaMiniHost(config)
    client_context = zmq.Context()
    sender = client_context.socket(zmq.PUSH)
    sender.setsockopt(zmq.LINGER, 0)
    sender.connect(f"ipc://{directory}/command.sock")
    robot = Motor()
    control = host.AM1LocalControl(**binding())
    try:
        sender.send_string(json.dumps(command(binding())))
        assert motor_host.zmq_cmd_socket.poll(1000)
        with pytest.raises(RuntimeError, match="binding"):
            control.apply(robot, json.loads(motor_host.zmq_cmd_socket.recv_string()))
        assert not robot.events
        for name in ("command.sock", "observation.sock"):
            assert (directory / name).stat().st_mode & 0o777 == 0o600
        assert motor_host.zmq_cmd_socket.getsockopt_string(zmq.LAST_ENDPOINT).startswith("ipc://")
    finally:
        sender.close()
        client_context.term()
        motor_host.disconnect()
    assert not (directory / "command.sock").exists()
    assert not (directory / "observation.sock").exists()


def test_protected_receipt_preserves_first_fault_and_unknown(tmp_path):
    identity = binding()
    args = SimpleNamespace(
        am1_cleanup_receipt=str(tmp_path / "cleanup.json"), **{f"am1_{k}": v for k, v in identity.items()}
    )
    robot = SimpleNamespace(_am1_shutdown_evidence={"verified": False, "readbacks": [{"error": "lost"}]})
    receipt = host.write_am1_cleanup_receipt(args, robot, RuntimeError("first fault"), ["readback lost"])
    assert receipt["cleanup"] == "physical_cleanup_unknown" and receipt["uncertain"]
    assert receipt["first_cause"] == "RuntimeError: first fault"
    assert json.loads(Path(args.am1_cleanup_receipt).read_text()) == receipt


def test_duplicate_action_sequence_cannot_reapply_or_claim_a_new_ack():
    identity = binding()
    control, robot = host.AM1LocalControl(**identity), Motor()
    sent = command(identity)
    assert control.apply(robot, sent)
    assert not control.apply(robot, sent)
    assert len(robot.events) == 1
    observation = {}
    control.annotate(observation, acquired_at=42.0)
    feedback = observation[host.AM1_LOCAL_FEEDBACK_KEY]
    assert feedback["accepted_action_sequence"] == 1
    assert feedback["accepted_action"] == {"x.vel": 0.0}
    assert feedback["write_acknowledged"] is False


def test_repeated_closed_shutdown_retains_original_readback_and_unknown():
    robot, left, right, events = make_activation_robot()
    robot._am1_protected_cleanup = True
    left.read_sequences["Torque_Enable", "arm_left_gripper"] = [ConnectionError("first read failed")]
    first_errors = robot._safe_shutdown(close_buses=True)
    original = robot._am1_shutdown_evidence
    assert first_errors
    assert robot._safe_shutdown(close_buses=True) == first_errors
    assert robot._am1_shutdown_evidence is original
    assert not original["verified"]


@pytest.mark.skipif(os.name == "nt", reason="real filesystem IPC is native Pi transport")
def test_protected_transport_refusal_never_unlinks_an_existing_owner_path(tmp_path):
    directory = tmp_path / "run"
    directory.mkdir(mode=0o700)
    previous = directory / "command.sock"
    previous.write_bytes(b"existing owner marker")
    with pytest.raises(RuntimeError, match="fresh"):
        host.AlohaMiniHost(AlohaMiniHostConfig(protected_run_directory=str(directory)))
    assert previous.read_bytes() == b"existing owner marker"


@pytest.mark.skipif(os.name == "nt", reason="AM1 physical arbitration is native Pi flock")
def test_inherited_parent_lock_survives_child_cleanup(tmp_path):
    parent = host.AM1PhysicalAdmission(tmp_path / "state")
    parent.serial.close()
    parent.serial = None  # Supervisor owns admission only; child owns serial lock.
    child = host.AM1PhysicalAdmission(tmp_path / "state", inherited_fd=parent.canonical.fileno())
    child.close()
    try:
        with pytest.raises(RuntimeError, match="owner"):
            host.AM1PhysicalAdmission(tmp_path / "state")
    finally:
        parent.close()
    finished = host.AM1PhysicalAdmission(tmp_path / "state")
    finished.close()


def test_launcher_print_command_forwards_protected_scope_without_effects():
    import shutil
    import subprocess

    git = shutil.which("git")
    bash = shutil.which("bash")
    if bash is None and git:
        candidate = Path(git).resolve().parents[1] / "bin" / "bash.exe"
        if candidate.is_file():
            bash = str(candidate)
    if bash is None:
        pytest.skip("bash is unavailable")
    launcher = Path(__file__).resolve().parents[2] / "tools/run_am1_host.sh"
    result = subprocess.run(
        [
            bash,
            str(launcher),
            "--mode",
            "local",
            "--print-command",
            "--am1-protected-run-directory",
            "/private/run",
            "--am1-admission-fd",
            "8",
            "--am1-host-runtime-s",
            "480",
        ],
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )
    assert "--am1-protected-run-directory /private/run" in result.stdout
    assert "--am1-admission-fd 8" in result.stdout
    assert "--am1-host-runtime-s 480" in result.stdout
    assert "--no_cameras" in result.stdout


def test_host_arbitration_refuses_before_robot_construction(monkeypatch):
    import sys

    constructed = []
    monkeypatch.setattr(sys, "argv", ["host", "--robot_model", "alohamini1", "--no_cameras"])
    monkeypatch.setattr(host, "AlohaMini", lambda config: constructed.append(config))

    def conflict(*args, **kwargs):
        raise RuntimeError("Another AM1 physical owner")

    monkeypatch.setattr(host, "AM1PhysicalAdmission", conflict)
    with pytest.raises(RuntimeError, match="owner"):
        host.main()
    assert not constructed


def test_host_refuses_simulated_lock_namespace_before_motor_effects(monkeypatch, tmp_path):
    import sys

    constructed = []
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "host",
            "--robot_model",
            "alohamini1",
            "--no_cameras",
            "--am1-physical-state-directory",
            str(tmp_path / "simulated"),
        ],
    )
    monkeypatch.setattr(host, "AlohaMini", lambda config: constructed.append(config))
    with pytest.raises(RuntimeError, match="canonical"):
        host.main()
    assert not constructed


def test_first_or_duplicate_stop_signal_cannot_interrupt_existing_cleanup(monkeypatch, tmp_path):
    import signal
    import sys

    state = tmp_path / ".local/state/am1-session"
    state.mkdir(parents=True, mode=0o700)
    monkeypatch.setattr(host.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(sys, "argv", ["host", "--robot_model", "alohamini1", "--no_cameras"])
    monkeypatch.setattr(
        host,
        "AM1PhysicalAdmission",
        lambda *args, **kwargs: SimpleNamespace(
            close=lambda: None,
            claim_run=lambda **kwargs: None,
            finish_run=lambda receipt: False,
        ),
    )
    signals = []

    def finishing(args):
        stop = signal.getsignal(signal.SIGTERM)
        args._am1_robot = SimpleNamespace(_am1_shutdown_in_progress=True)
        stop(signal.SIGTERM, None)
        stop(signal.SIGTERM, None)
        signals.append("cleanup continued")

    monkeypatch.setattr(host, "_run_host", finishing)
    host.main()
    assert signals == ["cleanup continued"]


@pytest.mark.parametrize("cleanup_fault", [False, True])
@pytest.mark.parametrize("protected", [False, True])
def test_requested_startup_stop_receipt_qualifies_only_actual_cleanup(
    monkeypatch, tmp_path, cleanup_fault, protected
):
    import signal
    import sys

    identity = binding()
    state = tmp_path / ".local/state/am1-session"
    state.mkdir(parents=True, mode=0o700)
    run = tmp_path / "run"
    run.mkdir(mode=0o700)
    monkeypatch.setattr(host.Path, "home", classmethod(lambda cls: tmp_path))
    argv = [
        "host",
        "--robot_model",
        "alohamini1",
        "--no_cameras",
        "--am1-physical-state-directory",
        str(state),
    ]
    if protected:
        argv += [
            "--max_relative_target",
            "20",
            "--max_loop_freq_hz",
            "30",
            "--am1-protected-run-directory",
            str(run),
            "--am1-run-id",
            identity["run_id"],
            "--am1-host-incarnation",
            identity["host_incarnation"],
            "--am1-cleanup-receipt",
            str(run / "cleanup.json"),
        ]
    monkeypatch.setattr(sys, "argv", argv)
    admission = host.AM1PhysicalAdmission.__new__(host.AM1PhysicalAdmission)
    admission.directory = state
    admission.canonical = admission.serial = None
    monkeypatch.setattr(host, "AM1PhysicalAdmission", lambda *args, **kwargs: admission)
    robot, left, right, events = make_activation_robot()
    if cleanup_fault:
        left.read_sequences["Torque_Enable", "arm_left_gripper"] = [ConnectionError("genuine cleanup loss")]

    def construct(config):
        marker = json.loads((state / "physical-in-progress.json").read_text())
        assert marker["scope"] == ("protected" if protected else "legacy")
        assert marker["run_id"] == identity["run_id"] if protected else marker["run_id"]
        return robot

    def stop_during_connect(robot, **kwargs):
        signal.getsignal(signal.SIGINT)(signal.SIGINT, None)

    monkeypatch.setattr(host, "AlohaMini", construct)
    monkeypatch.setattr(host, "connect_robot", stop_during_connect)
    with pytest.raises(KeyboardInterrupt):
        host.main()
    receipts = [run / "cleanup.json"] if protected else list(state.glob("physical-cleanup-*.json"))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text())
    assert receipt["run_id"] and receipt["host_incarnation"]
    assert receipt["expected_stop"]["signal"] == "SIGINT"
    assert receipt["expected_stop"]["interruption"][0]["cause"] == "KeyboardInterrupt: "
    assert not left.is_connected and not right.is_connected
    if cleanup_fault:
        assert receipt["uncertain"] and "genuine cleanup loss" in receipt["first_cause"]
        assert (state / "physical-in-progress.json").exists()
        assert (state / "cleanup-uncertain.json").exists()
    else:
        assert receipt["cleanup"] == "physical_readback_verified"
        assert receipt["first_cause"] is None and receipt["first_error_chain"] == []
        assert receipt["readback"]["buses_closed"]
        assert not (state / "physical-in-progress.json").exists()
        assert not (state / "cleanup-uncertain.json").exists()


def test_durable_marker_survives_unverified_finish_and_is_not_replaced(tmp_path):
    admission = host.AM1PhysicalAdmission.__new__(host.AM1PhysicalAdmission)
    admission.directory = tmp_path
    admission.canonical = admission.serial = None
    identity = binding()
    admission.claim_run(**identity, scope="legacy")
    original = (tmp_path / "physical-in-progress.json").read_bytes()
    assert not admission.finish_run({**identity, "uncertain": True, "cleanup": "physical_cleanup_unknown"})
    assert (tmp_path / "physical-in-progress.json").read_bytes() == original
    with pytest.raises(RuntimeError, match="reconciliation"):
        admission.claim_run(**binding(), scope="protected")
    assert (tmp_path / "physical-in-progress.json").read_bytes() == original


def test_legacy_body_only_cleanup_qualifies_only_configured_components():
    robot, left, right, events = make_activation_robot()
    robot._am1_protected_cleanup = True
    robot.config.no_follower = True
    robot.right_bus = None
    left.motors = {name: motor for name, motor in left.motors.items() if not name.startswith("arm_")}
    assert not robot._safe_shutdown(close_buses=True)
    evidence = robot._am1_shutdown_evidence
    assert evidence["verified"] and evidence["buses_closed"]
    assert all(row["bus"] == "left" for row in evidence["readbacks"])
    assert not any(event[0] == "right" for event in events)


@pytest.mark.skipif(os.name == "nt", reason="abrupt native owner death requires Pi flock")
def test_abrupt_owner_death_leaves_marker_and_refuses_direct_and_inherited_legacy(tmp_path):
    import fcntl
    import subprocess
    import sys

    state = tmp_path / "state"
    code = """
import os, sys
from uuid import uuid4
from lerobot.robots.alohamini.alohamini_host import AM1PhysicalAdmission
owner = AM1PhysicalAdmission(sys.argv[1])
owner.claim_run(run_id=str(uuid4()), host_incarnation=str(uuid4()), scope='protected')
os._exit(91)
"""
    result = subprocess.run([sys.executable, "-c", code, str(state)], timeout=45, check=False)
    assert result.returncode == 91
    with pytest.raises(RuntimeError, match="reconciliation"):
        host.AM1PhysicalAdmission(state)
    with (state / "active.lock").open("a+b") as legacy_parent:
        fcntl.flock(legacy_parent.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="reconciliation"):
            host.AM1PhysicalAdmission(state, inherited_fd=legacy_parent.fileno())
    assert (state / "physical-in-progress.json").exists()


def test_genuine_startup_fault_is_retained_even_with_stop_metadata_and_qualified_cleanup(tmp_path):
    robot, left, right, events = make_activation_robot()
    robot._am1_protected_cleanup = True
    assert not robot._safe_shutdown(close_buses=True)
    identity = binding()
    args = SimpleNamespace(
        am1_cleanup_receipt=str(tmp_path / "cleanup.json"),
        **{f"am1_{key}": value for key, value in identity.items()},
        _am1_stop_signal="SIGTERM",
        _am1_stop_interruption=KeyboardInterrupt(),
    )
    receipt = host.write_am1_cleanup_receipt(args, robot, ConnectionError("first actual startup loss"), [])
    assert not receipt["uncertain"]
    assert receipt["first_cause"] == "ConnectionError: first actual startup loss"
    assert receipt["expected_stop"]["signal"] == "SIGTERM"


def test_invalid_legacy_scope_refuses_before_claim_or_physical_admission(monkeypatch):
    import sys

    attempted = []
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "host",
            "--robot_model",
            "alohamini1",
            "--no_cameras",
            "--no_follower",
            "--lift_relief",
            "--lift_readback",
        ],
    )

    def acquire(*args, **kwargs):
        attempted.append("admission")
        raise AssertionError("Invalid CLI must be rejected before admission")

    monkeypatch.setattr(host, "AM1PhysicalAdmission", acquire)
    with pytest.raises(SystemExit, match="2"):
        host.main()
    assert not attempted
