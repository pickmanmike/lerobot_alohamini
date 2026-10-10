"""Portable finite execution through controlled simulated IO; no physical adapters."""

import importlib
import uuid

import pytest

from tests.robots.test_am1_persistent_session import Clock, command, start
from tools.am1_session_core import SessionAuthority


def runtime():
    assert importlib.util.find_spec("tools.am1_pi_executor"), "portable simulated executor is missing"
    return importlib.import_module("tools.am1_pi_executor")


def build(tmp_path, clock):
    m = runtime()
    io = m.SimulatedIO(clock=clock)
    e = m.PiExecutor(io, tmp_path / "admission", clock=clock)
    return io, e, SessionAuthority(tmp_path / "owner", clock=clock, executor=e)


def advance(a, c, seconds):
    for _ in range(round(seconds * 10)):
        c.advance(0.1)
        a.tick()


def test_real_provider_preparation_seed_cadence_mapping_and_repeat_completion(tmp_path):
    c = Clock()
    io, e, a = build(tmp_path, c)
    try:
        assert start(a, "sim-arm-smoke-repeat")["effect_admitted"]
        assert a.run["progress_s"] == 0
        advance(a, c, 2)
        assert a.run["progress_s"] == 0  # preparation is not trajectory progress
        advance(a, c, 4)
        assert a.run["progress_s"] > 0
        seed = dict(e.task.snapshot()["original_seed"])
        assert e.task.snapshot()["admissions"] == 1
        assert e.task.snapshot()["joint_amplitudes"]["arm_left_shoulder_lift.pos"] == 1.5
        before = a.run["progress_s"]
        c.advance(0.05)
        a.tick()
        assert a.run["progress_s"] == before
        c.advance(1)
        a.tick()
        assert a.run["progress_s"] <= before + 0.100001
        advance(a, c, 356)
        assert a.run["status"] == "completed"
        assert a.run["progress_s"] == 352
        assert a.run["execution"]["cycles_completed"] == 4
        assert a.run["execution"]["returns_qualified"] == 4
        assert a.run["execution"]["original_seed"] == seed
        assert a.run["execution"]["admissions"] == 1
        assert a.run["cleanup"] == "simulated_hold_acknowledged"
        assert a.run["source"] == "simulated-provider"
    finally:
        a.close()


def test_duplicate_detach_recovery_and_pause_do_not_reseed_or_reset_deadline(tmp_path):
    c = Clock()
    io, e, a = build(tmp_path, c)
    try:
        claim = a.handle(command("claim"), "owner")
        cmd = command(
            "start",
            operation_id=str(uuid.uuid4()),
            recipe="sim-arm-smoke-repeat",
            controller_generation=claim["controller_generation"],
        )
        result = a.handle(cmd, "owner")
        advance(a, c, 6)
        seed, deadline = e.task.snapshot()["original_seed"], a.run["deadline"]
        assert a.handle(cmd, "owner")["run_id"] == result["run_id"]
        for episode in range(1, 4):
            io.feedback_enabled = False
            c.advance(0.3)
            a.tick()
            assert a.run["status"] == "recovering"
            ceiling = a.run["recovery"]["ceiling"]
            io.feedback_enabled = True
            io.acknowledge = False
            c.advance(0.1)
            a.tick()
            assert a.run["recovery"]["ceiling"] == ceiling
            io.acknowledge = True
            advance(a, c, 0.4)
            assert a.run["status"] == "running"
            assert a.run["recovery_episodes"] == episode
            assert a.run["recovery"] is None
        assert a.run["deadline"] == deadline
        assert e.task.snapshot()["original_seed"] == seed
        assert e.task.snapshot()["admissions"] == 1
        a.handle(command("pause", run_id=result["run_id"]), "viewer")
        progress = a.run["progress_s"]
        advance(a, c, 2)
        assert a.run["progress_s"] == progress
        io.fault = "simulated_backend_fault"
        a.tick()
        assert a.run["status"] == "faulted"
        assert a.run["first_cause"] == "feedback_loss"
    finally:
        a.close()


def test_stale_feedback_and_missing_ack_never_advance(tmp_path):
    c = Clock()
    io, e, a = build(tmp_path, c)
    try:
        io.acknowledge = False
        assert not start(a, "sim-arm-smoke")["accepted"]
        io.acknowledge = True
        assert start(a, "sim-arm-smoke")["effect_admitted"]
        advance(a, c, 6)
        progress = a.run["progress_s"]
        io.freeze_sequence = True
        advance(a, c, 0.5)
        assert a.run["status"] == "recovering"
        assert a.run["progress_s"] <= progress + 0.100001
        advance(a, c, 10)
        assert a.run["status"] == "faulted"
        assert a.run["first_cause"] == "feedback_loss"
    finally:
        a.close()


def test_preparation_wall_deadline_survives_pause_and_restart_is_uncertain(tmp_path):
    c = Clock()
    io, e, a = build(tmp_path, c)
    result = start(a, "sim-arm-smoke-repeat")
    a.handle(command("pause", run_id=result["run_id"]), "viewer")
    c.advance(20)
    a.tick()
    assert a.run["status"] == "faulted"
    assert "preparation" in a.run["first_cause"]
    a.close()
    io, e, a = build(tmp_path, c)
    result = start(a, "sim-arm-smoke-repeat")
    old_incarnation = a.service_incarnation
    a.close()
    io, e, a = build(tmp_path, c)
    try:
        assert a.run["status"] == "interrupted" and a.run["uncertain"]
        assert a.service_incarnation != old_incarnation
        assert a.run["cleanup"] == "unknown_after_restart"
        assert e.task is None
        assert not start(a, "sim-arm-smoke-repeat")["accepted"]
    finally:
        a.close()


def test_terminal_requires_backend_hold_ack_and_provider_return_evidence(tmp_path):
    c = Clock()
    io, e, a = build(tmp_path, c)
    try:
        result = start(a, "sim-arm-hold-body")
        advance(a, c, 11.9)
        assert a.run["status"] == "running"
        io.hold_acknowledge = False
        a.handle(command("stop", run_id=result["run_id"]), "viewer")
        assert a.run["cleanup"] == "simulated_cleanup_unacknowledged"
        assert a.run["uncertain"]
    finally:
        a.close()


def test_shared_admission_is_separate_from_owner_namespace(tmp_path):
    runtime()
    from examples.alohamini.am1_session_runtime import acquire_session_admission

    c = Clock()
    lock = acquire_session_admission(tmp_path / "admission")
    io, e, a = build(tmp_path, c)
    try:
        assert not start(a, "sim-arm-smoke")["effect_admitted"]
        assert e.task is None
    finally:
        a.close()
        lock.close()
    io, e, a = build(tmp_path / "next", c)
    try:
        assert start(a, "sim-arm-smoke")["effect_admitted"]
        with pytest.raises(RuntimeError, match="active"):
            acquire_session_admission(tmp_path / "next" / "admission")
    finally:
        a.close()


def test_fake_owner_cannot_dispatch_simulated_recipe_and_simulator_cannot_dispatch_fake(tmp_path):
    c = Clock()
    a = SessionAuthority(tmp_path / "fake", clock=c)
    try:
        assert not start(a, "sim-arm-smoke")["accepted"]
    finally:
        a.close()
    io, e, a = build(tmp_path / "sim", c)
    try:
        assert not start(a)["accepted"]
    finally:
        a.close()


@pytest.mark.parametrize(
    "host,port,origin",
    [
        ("0.0.0.0", 8443, "https://am1.invalid:8443"),
        ("192.168.1.20", 8443, None),
        ("192.168.1.20", 8443, "http://am1.invalid:8443"),
        ("192.168.1.20", 8443, "https://am1.invalid:8443/path"),
        ("192.168.1.20", 8443, "https://user@am1.invalid:8443"),
        ("192.168.1.20", 8443, "https://am1.invalid:8444"),
        ("192.168.1.20", 8443, "https://*.invalid:8443"),
        ("192.168.1.20", 0, "https://am1.invalid:8443"),
        ("8.8.8.8", 8443, "https://am1.invalid:8443"),
    ],
)
def test_listener_rejects_implicit_or_ambiguous_lan_exposure(host, port, origin):
    from tools import am1_session_service as service

    assert hasattr(service, "validate_listener"), "explicit listener validation is missing"
    with pytest.raises(ValueError):
        service.validate_listener(host, port, origin)


def test_listener_loopback_default_and_exact_lan_origin():
    from tools import am1_session_service as service

    assert hasattr(service, "validate_listener"), "explicit listener validation is missing"
    assert service.validate_listener("127.0.0.1", 0, None) is None
    assert (
        service.validate_listener("192.168.1.20", 8443, "https://am1.invalid:8443")
        == "https://am1.invalid:8443"
    )


def test_legacy_and_portable_contenders_share_actual_admission_before_repository_or_device_access(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from tools import am1_session_remote as remote

    c = Clock()
    io, e, a = build(tmp_path, c)
    args = SimpleNamespace(
        state_directory=str(tmp_path / "admission"),
        session_id="20261009T120000-1234abcd",
        camera_head="fake",
        motor_head="fake",
        session_head="fake",
        session_repository="unused",
    )
    supervisor = remote.RemoteSupervisor(args, remote.BestEffortReporter(lambda row: None))
    try:
        assert start(a, "sim-arm-smoke")["effect_admitted"]
        with pytest.raises(remote.SessionRefusal, match="active"):
            supervisor.preflight()
    finally:
        a.close()

    class PastAdmissionError(Exception):
        pass

    def stop_before_repository(*args):
        raise PastAdmissionError()

    monkeypatch.setattr(remote, "_validate_repository", stop_before_repository)
    with pytest.raises(PastAdmissionError):
        supervisor.preflight()
    e = runtime().PiExecutor(runtime().SimulatedIO(clock=c), tmp_path / "admission", clock=c)
    a = SessionAuthority(tmp_path / "other-owner", clock=c, executor=e)
    try:
        assert not start(a, "sim-arm-smoke")["effect_admitted"]
        assert e.task is None
    finally:
        a.close()
        supervisor.lock_stream.close()


def test_snapshot_offers_only_executor_supported_recipes(tmp_path):
    c = Clock()
    io, e, a = build(tmp_path, c)
    try:
        assert a.snapshot().get("recipes") == ["sim-arm-smoke", "sim-arm-smoke-repeat", "sim-arm-hold-body"]
    finally:
        a.close()


def test_provider_return_is_measured_and_rejects_outside_original_envelope(tmp_path):
    c = Clock()
    io, e, a = build(tmp_path, c)
    try:
        start(a, "sim-arm-smoke-repeat")
        while a.run["progress_s"] < 88:
            advance(a, c, 0.1)
        assert a.run["status"] == "running"
        assert a.run["execution"]["cycles_completed"] == 1
        assert a.run["execution"]["returns_qualified"] == 0
        io.target["arm_left_shoulder_lift.pos"] = 90
        advance(a, c, 0.1)
        assert a.run["status"] == "faulted"
        assert "return" in a.run["first_cause"]
        assert a.run["execution"]["returns_qualified"] == 0
    finally:
        a.close()


def test_non_repeat_terminal_requires_fresh_qualified_return(tmp_path):
    c = Clock()
    io, e, a = build(tmp_path, c)
    try:
        start(a, "sim-arm-smoke")
        while a.run["progress_s"] < 87.9:
            advance(a, c, 0.1)
        io.target["arm_left_shoulder_lift.pos"] = 90
        advance(a, c, 0.2)
        assert a.run["status"] == "faulted"
        assert "return" in a.run["first_cause"]
    finally:
        a.close()


def test_simulated_runtime_can_reach_the_ui_recipe_boundary_through_real_ipc_and_https(tmp_path):
    import asyncio

    from tests.robots.am1_session_fixture import Cluster
    from tests.robots.test_am1_session_api import enrolled, post, snapshot

    cluster = Cluster(tmp_path, simulated=True)

    async def scenario():
        client = await enrolled(cluster)
        try:
            state = await snapshot(client, cluster)
            assert state["recipes"] == ["sim-arm-smoke", "sim-arm-smoke-repeat", "sim-arm-hold-body"]
            _, claim = await post(client, cluster, "/api/claim", {})
            _, result = await post(
                client,
                cluster,
                "/api/start",
                {
                    "operation_id": str(uuid.uuid4()),
                    "recipe": "sim-arm-hold-body",
                    "controller_generation": claim["controller_generation"],
                },
            )
            assert result["effect_admitted"]
            await asyncio.sleep(0.35)
            state = await snapshot(client, cluster)
            assert state["run"]["progress_s"] > 0
            assert state["run"]["source"] == "simulated-provider"
        finally:
            await client.close()

    try:
        asyncio.run(scenario())
    finally:
        cluster.close()


def test_missing_feedback_packet_holds_without_losing_owner_tick(tmp_path, monkeypatch):
    c = Clock()
    io, e, a = build(tmp_path, c)
    try:
        start(a, "sim-arm-smoke-repeat")
        monkeypatch.setattr(io, "feedback", lambda: None)
        c.advance(0.1)
        a.tick()
        assert a.run["status"] == "recovering"
        assert a.run["progress_s"] == 0
    finally:
        a.close()


def test_backend_send_exception_faults_and_retains_first_cause(tmp_path, monkeypatch):
    c = Clock()
    io, e, a = build(tmp_path, c)
    try:
        start(a, "sim-arm-smoke-repeat")

        def broken_send(action):
            raise OSError("injected simulated send failure")

        monkeypatch.setattr(io, "send", broken_send)
        c.advance(0.1)
        a.tick()
        assert a.run["status"] == "faulted"
        assert "injected simulated send failure" in a.run["first_cause"]
    finally:
        a.close()


def test_portable_import_and_execution_cannot_construct_device_network_or_physical_launcher(tmp_path):
    import subprocess
    import sys

    script = r"""
import builtins, sys
real_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name.split('.')[0] in {'zmq', 'serial', 'cv2', 'teleoperate_bi'} or name.startswith('lerobot.robots'):
        raise AssertionError('physical import: ' + name)
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
def audit(event, args):
    if event == 'socket.__new__' and args[1] in (2, 10):
        raise AssertionError('IP socket creation')
    if event == 'subprocess.Popen':
        raise AssertionError('physical launcher')
    if event == 'open' and isinstance(args[0], str) and (args[0].startswith('/dev/') or args[0].upper().startswith('COM')):
        raise AssertionError('device open')
sys.addaudithook(audit)
from tools.am1_pi_executor import PiExecutor, SimulatedIO
from tools.am1_session_core import SessionAuthority
from tests.robots.test_am1_persistent_session import Clock, start
from pathlib import Path
c = Clock()
e = PiExecutor(SimulatedIO(c), Path(sys.argv[1])/'admission', clock=c)
a = SessionAuthority(Path(sys.argv[1])/'owner', clock=c, executor=e)
assert start(a, 'sim-arm-smoke-repeat')['effect_admitted']
for _ in range(3600):
    c.advance(.1)
    a.tick()
assert a.run['status'] == 'completed'
assert a.run['execution']['returns_qualified'] == 4
a.close()
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, result.stderr


def test_shared_admission_contends_across_processes(tmp_path):
    import subprocess
    import sys

    from examples.alohamini.am1_session_runtime import acquire_session_admission

    lock = acquire_session_admission(tmp_path)
    script = """
import sys
from examples.alohamini.am1_session_runtime import acquire_session_admission
try:
    lock = acquire_session_admission(sys.argv[1])
except RuntimeError:
    sys.exit(23)
lock.close()
"""
    try:
        result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], timeout=10)
        assert result.returncode == 23
    finally:
        lock.close()
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], timeout=10)
    assert result.returncode == 0


def test_recovery_requires_three_advancing_aligned_samples_and_backend_hold_ack(tmp_path):
    c = Clock()
    io, e, a = build(tmp_path, c)
    try:
        start(a, "sim-arm-smoke-repeat")
        advance(a, c, 6)
        io.feedback_enabled = False
        io.hold_acknowledge = False
        a.tick()
        assert a.run["status"] == "recovering"
        io.feedback_enabled = True
        advance(a, c, 0.4)
        assert a.run["status"] == "recovering"
        io.hold_acknowledge = True
        e.hold()
        advance(a, c, 0.1)
        assert a.run["status"] == "recovering"
        advance(a, c, 0.3)
        assert a.run["status"] == "running"
    finally:
        a.close()


def test_failed_frame_ack_cannot_publish_progress_or_completion(tmp_path, monkeypatch):
    c = Clock()
    io, e, a = build(tmp_path, c)
    try:
        start(a, "sim-arm-hold-body")
        advance(a, c, 1)
        progress = a.run["progress_s"]
        monkeypatch.setattr(io, "send", lambda action: False)
        advance(a, c, 0.1)
        assert a.run["status"] == "faulted"
        assert a.run["progress_s"] == progress
        assert a.run["first_cause"] == "backend_command_unacknowledged"
    finally:
        a.close()
