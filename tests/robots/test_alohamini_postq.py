"""Synthetic post-Q probes: no serial devices, real sockets, or powered hardware."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shlex
import signal
import subprocess
import sys
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.robots.test_alohamini_lift_operational import operating_robot
from tests.robots.test_am1_lean_launchers import BASH, HOST_HELPER, REPO_ROOT


def output_probe(report_path: str) -> None:
    """Subprocess uses real poll -> get_observation with the existing fake buses.

    The fake clock charges actual blocked-output time to the sample. The large
    single record deliberately fills pipe buffers: synthetic pressure, not a
    claim that the historical record was this large or its terminal was paused.
    """
    path = Path(report_path)
    real_monotonic = time.monotonic
    real_sleep = time.sleep
    result = {"error": None, "frames": [], "cleanup_error": None}
    with pytest.MonkeyPatch.context() as patch:
        robot, clock = operating_robot.__wrapped__(patch, path.parent)
        with contextlib.redirect_stdout(io.StringIO()):
            robot.connect(calibrate=False)
        operation = robot._lift_operation
        emit = operation.emit

        def pressure(record):
            started = real_monotonic()
            try:
                emit({**record, "synthetic_output_pressure": "x" * (1024 * 1024)})
            finally:
                clock.sleep(real_monotonic() - started)

        operation.emit = pressure
        path.with_suffix(".ready").write_text("fake hardware ready", encoding="utf-8")
        try:
            operation.poll()
            robot.get_observation()
            operation.emit = emit
            first_sample = operation.last_record["sample_monotonic_s"]
            for _ in range(20):
                real_sleep(0.03)
                clock.sleep(0.03)
                operation.poll()
                robot.get_observation()
            result["monitored_idle_s"] = operation.last_record["sample_monotonic_s"] - first_sample
        except BaseException as error:
            result.update(error=str(error), frames=[f.name for f in traceback.extract_tb(error.__traceback__)])
        finally:
            operation.emit = emit
            try:
                robot.disconnect()
            except BaseException as error:
                result["cleanup_error"] = str(error)
            result.update(
                goal=robot.left_bus.registers[("Goal_Velocity", "lift_axis")],
                torque=robot.left_bus.registers[("Torque_Enable", "lift_axis")],
                bus_closed=not robot.left_bus.is_connected,
            )
            path.write_text(json.dumps(result), encoding="utf-8")
    raise SystemExit(1 if result["error"] or result["cleanup_error"] else 0)


def terminate_probe(process):
    """Bound teardown of only the subprocess tree created by this test."""
    if process.poll() is None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True, timeout=10, check=False,
            )
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)


def launch_runtime(tmp_path, child):
    # Execute the actual production runtime tail, not a hand-written tee clone.
    # Preflight is intentionally excluded: no device aliases or host is opened.
    runtime = "set +e\n" + HOST_HELPER.read_text(encoding="utf-8").rsplit("\nset +e\n", 1)[1]
    log = tmp_path / "host.log"
    script = (
        "set -uo pipefail\nmode=local\n"
        f"repository_root={shlex.quote(REPO_ROOT.as_posix())}\n"
        f"log_path={shlex.quote(log.as_posix())}\n"
        f"command=({shlex.quote(Path(sys.executable).as_posix())} -c {shlex.quote(child)})\n"
        "die() { printf '%s\\n' \"$*\" >&2; return 2; }\n" + runtime
    )
    process = subprocess.Popen(
        [BASH, "-c", script], cwd=REPO_ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        start_new_session=os.name != "nt",
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )
    return process, log


@pytest.mark.parametrize("viewer", ["draining", "blocked", "closed"])
def test_local_launcher_output_consumer_cannot_stall_lift_monitoring(tmp_path, viewer):
    # Restoring a synchronous tee/terminal in Local's runtime must fail this.
    report = tmp_path / "probe.json"
    child = (
        f"import sys; sys.path.insert(0, {REPO_ROOT.as_posix()!r}); "
        "from tests.robots.test_alohamini_postq import output_probe; "
        f"output_probe({report.as_posix()!r})"
    )
    process, log = launch_runtime(tmp_path, child)
    try:
        if viewer != "draining":
            deadline = time.monotonic() + 20
            while not report.with_suffix(".ready").exists():
                assert time.monotonic() < deadline, "fake worker failed to start"
                assert process.poll() is None, "worker exited before fake hardware was ready"
                time.sleep(0.01)
            if viewer == "closed":
                process.stdout.close()
                process.wait(timeout=15)
            else:
                time.sleep(0.8)  # Synthetic output stall exceeds unchanged 0.5 s freshness.
                process.communicate(timeout=15)
        else:
            process.communicate(timeout=20)
        result = json.loads(report.read_text(encoding="utf-8"))
        assert process.returncode == 0, result
        assert result["error"] is None and result["cleanup_error"] is None
        assert result["monitored_idle_s"] > 0.5
        assert result["goal"] == result["torque"] == 0 and result["bus_closed"]
        evidence = log.read_text(encoding="utf-8")
        assert '"synthetic_output_pressure"' in evidence
        assert '"phase":"shutdown_verified"' in evidence
        assert "HOST_EXIT_CODE=0" in evidence
    finally:
        terminate_probe(process)
        if not process.stdout.closed:
            process.stdout.close()


@pytest.mark.parametrize("exit_code", [0, 2, 23])
def test_local_runtime_records_primary_exit_without_relabeling(tmp_path, exit_code):
    process, log = launch_runtime(
        tmp_path, f"print('synthetic cleanup complete'); raise SystemExit({exit_code})",
    )
    try:
        process.communicate(timeout=15)
        assert process.returncode == exit_code
        assert f"HOST_EXIT_CODE={exit_code}" in log.read_text(encoding="utf-8")
    finally:
        terminate_probe(process)
        process.stdout.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX foreground process-group SIGINT check")
def test_local_foreground_sigint_reaches_child_and_preserves_cleanup(tmp_path):
    marker = tmp_path / "ready"
    child = (
        "import time\nfrom pathlib import Path\n"
        f"Path({marker.as_posix()!r}).write_text('ready')\n"
        "try:\n    time.sleep(15)\n"
        "except KeyboardInterrupt:\n    print('synthetic zero/off cleanup complete', flush=True)\n"
    )
    process, log = launch_runtime(tmp_path, child)
    try:
        deadline = time.monotonic() + 10
        while not marker.exists():
            assert time.monotonic() < deadline
            time.sleep(0.01)
        os.killpg(process.pid, signal.SIGINT)
        process.communicate(timeout=5)
        assert process.returncode == 0
        evidence = log.read_text(encoding="utf-8")
        assert "synthetic zero/off cleanup complete" in evidence
        assert "HOST_EXIT_CODE=0" in evidence
    finally:
        terminate_probe(process)
        process.stdout.close()


@pytest.mark.parametrize("fault", [None, "left_arm_delay", "wheel_delay", "wheel_error"])
def test_actual_host_after_client_exit_keeps_polling_or_truthfully_stops(
    operating_robot, monkeypatch, capsys, fault,
):
    from lerobot.robots.alohamini import alohamini_host as host

    robot, clock = operating_robot
    polls = []
    commands = []
    closed = []
    injected = []
    primary = OSError("synthetic underlying motor I/O failure")
    original_connect = robot.connect

    def connect(**kwargs):
        original_connect(calibrate=False, **kwargs)
        # Add a fake arm read to exercise the same observation stages as Local.
        robot.left_arm_motors = ["synthetic_left_arm"]
        operation = robot._lift_operation
        poll = operation.poll
        sync_read = robot.left_bus.sync_read

        def checked_poll():
            poll()
            polls.append(clock.now)

        def modeled_io(register, motors):
            target = robot.left_arm_motors if fault == "left_arm_delay" else robot.base_motors
            if fault and motors == target and len(commands) >= 2 and not injected:
                injected.append(fault)
                if fault == "wheel_error":
                    raise primary
                clock.sleep(35.2)  # Synthetic blocked read/write/flush/retry duration.
            return sync_read(register, motors)

        operation.poll = checked_poll
        robot.left_bus.sync_read = modeled_io

    def receive(flags):
        if len(commands) < 2:
            commands.append(clock.now)
            return json.dumps({"x.vel": 0, "y.vel": 0, "theta.vel": 0, "lift_axis.vel": 0})
        raise host.zmq.Again()  # Client has exited; no real socket exists.

    def no_observations(**kwargs):
        raise host.zmq.Again()

    monkeypatch.setattr(robot, "connect", connect)
    monkeypatch.setattr(host, "AlohaMini", lambda config: robot)
    monkeypatch.setattr(host.time, "perf_counter", clock.monotonic)
    monkeypatch.setattr(sys, "argv", ["host", "--robot_model", "alohamini1", "--no_cameras"])
    monkeypatch.setattr(host, "AlohaMiniHost", lambda config: SimpleNamespace(
        connection_time_s=3, max_loop_freq_hz=30, watchdog_timeout_ms=1000,
        zmq_cmd_socket=SimpleNamespace(recv_string=receive),
        zmq_observation_socket=SimpleNamespace(recv_multipart=no_observations),
        disconnect=lambda: closed.append("socket_closed"),
    ))
    if fault:
        with pytest.raises(Exception) as caught:
            host.main()
        if fault == "wheel_error":
            assert caught.value is primary
        else:
            assert caught.value is robot._lift_operation.failure
            assert "five-reading feedback window is stale" in str(caught.value)
            frames = [f.name for f in traceback.extract_tb(caught.value.__traceback__)]
            assert "get_observation" in frames and "_require_latest" in frames
            notes = " ".join(getattr(caught.value, "__notes__", []))
            assert "AM1 observation freshness context" in notes
            metric = "left_arm_ms" if fault == "left_arm_delay" else "base_ms"
            assert f'"{metric}":35200.0' in notes
    else:
        host.main()
        assert len(polls) >= 80
        assert polls[-1] - commands[-1] > 2.5
        assert robot._lift_operation.failure is None
    assert closed == ["socket_closed"]
    assert not robot.left_bus.is_connected
    assert robot.left_bus.registers[("Goal_Velocity", "lift_axis")] == 0
    assert robot.left_bus.registers[("Torque_Enable", "lift_axis")] == 0
    assert '"phase":"shutdown_verified"' in capsys.readouterr().out


@pytest.mark.parametrize("failure_stage", ["monitor", "shutdown", "interrupt", "motor_error"])
def test_failed_log_sink_cannot_skip_cleanup_or_replace_primary_error(
    operating_robot, monkeypatch, failure_stage,
):
    from lerobot.robots.alohamini import alohamini_host as host

    robot, clock = operating_robot
    closed = []
    sink_errors = []
    motor_error = OSError("synthetic primary motor failure")
    connected = False
    fail_output = False
    original_connect = robot.connect

    class FailedDisk(io.StringIO):
        def write(self, text):
            nonlocal fail_output
            if connected and failure_stage == "monitor":
                fail_output = True
            if text.startswith("Shutting down"):
                fail_output = True
            if fail_output:
                error = OSError(28, "synthetic log disk full")
                sink_errors.append(error)
                raise error
            return super().write(text)

    def connect(**kwargs):
        nonlocal connected
        original_connect(calibrate=False, **kwargs)
        connected = True

    def receive(flags):
        nonlocal fail_output
        fail_output = True
        if failure_stage == "interrupt":
            raise KeyboardInterrupt
        raise motor_error

    def observation():
        # The command handler intentionally handles malformed-command failures;
        # fail in actual motor observation instead, where the primary must escape.
        nonlocal fail_output
        fail_output = True
        raise motor_error

    monkeypatch.setattr(robot, "connect", connect)
    if failure_stage == "motor_error":
        monkeypatch.setattr(robot, "get_observation", observation)
    monkeypatch.setattr(host, "AlohaMini", lambda config: robot)
    monkeypatch.setattr(host.time, "perf_counter", clock.monotonic)
    monkeypatch.setattr(sys, "argv", ["host", "--robot_model", "alohamini1", "--no_cameras"])

    def no_command(flags):
        raise host.zmq.Again()

    monkeypatch.setattr(host, "AlohaMiniHost", lambda config: SimpleNamespace(
        connection_time_s=0 if failure_stage == "shutdown" else 1,
        max_loop_freq_hz=30, watchdog_timeout_ms=1000,
        zmq_cmd_socket=SimpleNamespace(recv_string=receive if failure_stage == "interrupt" else no_command),
        disconnect=lambda: closed.append("socket_closed"),
    ))
    with contextlib.redirect_stdout(FailedDisk()), pytest.raises(OSError) as caught:
        host.main()

    assert not robot.left_bus.is_connected
    assert closed == ["socket_closed"]
    assert robot.left_bus.registers[("Goal_Velocity", "lift_axis")] == 0
    assert robot.left_bus.registers[("Torque_Enable", "lift_axis")] == 0
    assert caught.value is (motor_error if failure_stage == "motor_error" else sink_errors[0])
    assert "robot disconnect also failed" in " ".join(caught.value.__notes__)
