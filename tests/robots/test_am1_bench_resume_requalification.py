"""Resume requalification through the real loopback page, HTTP adapter and native pipe."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.am1_console import ConsoleHandler, ConsoleServer, ConsoleSessionAdapter

ROOT = Path(__file__).resolve().parents[2]


def local_support():
    spec = importlib.util.spec_from_file_location(
        "resume_requalification_support", ROOT / "tests/robots/test_am1_console_local_path.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_route_race(tmp_path, intervention):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True).returncode:
        pytest.skip("Existing Playwright runtime required")
    support = local_support()
    session = support.FakeSessionIO(live_duration_s=20 if intervention == "expires" else 14)
    session.stop_exit_code = 130
    camera = ThreadingHTTPServer(("127.0.0.1", 0), support.FakeCamera)
    camera.daemon_threads = True
    camera.frame_count = 400
    camera.frame_period_s = 1 / 15
    camera.sequence_rate = 1000
    config = SimpleNamespace(
        browser_url=f"http://127.0.0.1:{camera.server_port}",
        local_state_directory=tmp_path,
        windows_log_directory=tmp_path,
        windows_session_head="1" * 40,
    )
    auth = tmp_path / "fake-camera.json"
    auth.write_text(json.dumps({"username": "test-only", "password": "test-only"}))
    adapter = ConsoleSessionAdapter(config, ROOT, session)
    health = tmp_path / "latest-health.json"
    virtual_config = tmp_path / "virtual-config.json"
    virtual_config.write_text(
        json.dumps(
            {
                "observer_health_path": str(health),
                "observer_generation": "fake-resume-capture",
                "verified_coverage": ["arms", "lift"],
                "required_camera_roles": [],
                "recovery_episode_seconds": 10,
                "max_recoveries": 3,
            }
        )
    )
    stop = threading.Event()
    publishing = threading.Event()
    publishing.set()
    publish_lock = threading.Lock()
    withdrawal_sent = threading.Event()
    restored = threading.Event()
    intervention_done = threading.Event()
    errors = []
    operations = []
    worker_threads = []
    trap = {
        "eligible_reads": 0,
        "injected": False,
        "proof": None,
        "paused_snapshot": None,
        "backend_refusal": None,
        "operator_result": None,
        "restored_at": None,
    }

    def wait_until(predicate, timeout, message):
        deadline = time.monotonic() + timeout
        while not stop.wait(0.01):
            if predicate():
                return
            assert time.monotonic() < deadline, message
        raise AssertionError("Fixture stopped before " + message)

    def freeze_original_health():
        publishing.clear()
        with publish_lock:
            pass  # Wait for any already-started external publication to finish.

    def observer_updates():
        sequence = 0
        try:
            while not stop.is_set():
                if publishing.is_set():
                    with publish_lock:
                        if publishing.is_set():
                            sequence += 1
                            report = {
                                "generation": "fake-resume-capture",
                                "running": True,
                                "recording": True,
                                "challenge_qualified": True,
                                "nonce": f"original-{sequence}",
                                "sequence": sequence,
                                "source_system_relative_ticks": sequence * 1_000_000,
                                "capture_age_ms": 20,
                                "round_trip_ms": 10,
                                "received_wall_time_ms": time.time_ns() / 1_000_000,
                            }
                            temporary = health.with_suffix(".tmp")
                            temporary.write_text(json.dumps(report))
                            for _attempt in range(10):
                                try:
                                    temporary.replace(health)
                                    break
                                except PermissionError:
                                    if stop.wait(0.01):
                                        return
                            else:
                                raise AssertionError("External observer publication stayed blocked")
                stop.wait(0.04)
        except BaseException as error:
            errors.append(error)

    real_operation = adapter.operation

    def operation(payload):
        operations.append((time.monotonic(), dict(payload)))
        if (
            intervention == "backend-refusal"
            and payload.get("kind") == "Resume"
            and payload.get("bench_recovery", {}).get("pause_sequence") == 2
        ):
            # The route qualified, but the actual backend must still reject evidence
            # that expires before its separate approval check. No fabricated reply.
            freeze_original_health()
            time.sleep(0.55)
            trap["backend_refusal"] = real_operation(payload)
            publishing.set()
            return trap["backend_refusal"]
        return real_operation(payload)

    adapter.operation = operation

    def recovery_episodes():
        try:
            if intervention == "expires":
                real_wait_gate = session.native.wait_gate

                def wait_gate(stage, **kwargs):
                    # Let the unchanged real 10 s episode expire first, rather than
                    # the external fake robot's normally shorter 8 s gate timeout.
                    if stage == "resume":
                        kwargs["timeout_s"] = 15
                    return real_wait_gate(stage, **kwargs)

                session.native.wait_gate = wait_gate
            # Complete one actual qualified recovery before the second route race.
            freeze_original_health()
            wait_until(lambda: adapter._bench_policy.seen_pause is True, 5, "first held episode")
            publishing.set()
            wait_until(lambda: adapter._bench_policy.recovery_count == 1, 5, "first actual Resume")
            assert not stop.wait(0.3)
            freeze_original_health()
            wait_until(
                lambda: adapter._bench_policy.seen_pause is True
                and adapter.state()["virtual_bench"]["pause_sequence"] == 2,
                5,
                "second held episode",
            )
            publishing.set()
            assert withdrawal_sent.wait(5), "second Resume route did not recheck qualification"
            assert not stop.wait(0.3)
            if intervention in {"Pause", "Stop", "ClaimInput"}:
                current = adapter.state()
                trap["operator_result"] = operation(
                    {
                        "kind": intervention,
                        "session_id": current["session_id"],
                        "control_token": adapter._control_token,
                    }
                )
                trap["after_operator"] = adapter.state()
            if intervention != "expires":
                # Return genuinely advancing evidence; only the original episode
                # may reuse it, never an operator pause, new owner or new gate.
                trap["restored_at"] = time.monotonic()
                restored.set()
                publishing.set()
            intervention_done.set()
        except BaseException as error:
            errors.append(error)

    def on_live():
        worker = threading.Thread(target=recovery_episodes, daemon=True)
        worker_threads.append(worker)
        worker.start()

    session.on_live = on_live

    class RaceHandler(ConsoleHandler):
        def do_GET(self):  # noqa: N802
            monitored = self.path == "/api/state" and self.headers.get("X-AM1-Bench-Monitor")
            if monitored and not trap["injected"]:
                state = adapter.state()
                bench = state.get("virtual_bench") or {}
                if bench.get("recovery_count") == 1 and bench.get("recovery_eligible") is True:
                    trap["eligible_reads"] += 1
                    if trap["eligible_reads"] == 1:
                        trap["proof"] = {
                            key: bench[key] for key in ("pause_sequence", "input_epoch", "host_epoch")
                        }
                        trap["approved_at"] = time.monotonic()
                        trap["remaining_at_approval"] = bench["remaining_seconds"]
                    elif trap["eligible_reads"] == 2:
                        trap["injected"] = True
                        freeze_original_health()
                        # Expire the genuine original receipt; never rewrite or re-age it.
                        time.sleep(0.55)
                        trap["paused_snapshot"] = adapter.state()
                        try:
                            super().do_GET()
                        finally:
                            withdrawal_sent.set()
                        return
            super().do_GET()

    server = ConsoleServer(("127.0.0.1", 0), config, auth, adapter)
    server.RequestHandlerClass = RaceHandler
    camera_thread = threading.Thread(target=camera.serve_forever, daemon=True)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    observer_thread = threading.Thread(target=observer_updates, daemon=True)
    for thread in (camera_thread, server_thread, observer_thread):
        thread.start()
    evidence_path = tmp_path / "bench.json"
    environment = dict(os.environ, AM1_BENCH_VIRTUAL_CONFIG=str(virtual_config), AM1_BENCH_HEADLESS="1")
    try:
        result = subprocess.run(
            [
                node,
                str(ROOT / "tools/am1_reliability_bench.cjs"),
                f"http://127.0.0.1:{server.server_port}",
                "ArmSmokeRepeat",
                "AM1-RELIABILITY-03-arm-01",
                str(evidence_path),
                "1" * 40,
            ],
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=35,
        )
        finished_at = time.monotonic()
        evidence = json.loads(evidence_path.read_text())
        for thread in worker_threads:
            thread.join(timeout=1)
        assert not errors, errors
        assert intervention_done.is_set() and trap["injected"]
        held = trap["paused_snapshot"]
        assert held["phase"] == "paused" and held["native_connected"] is True
        assert held["virtual_bench"]["required_coverage_qualified"] is False
        assert held["virtual_bench"]["disarmed"] is False
        assert held["telemetry"]["observation"]["age_ms"] <= 250
        assert held["virtual_bench"]["remaining_seconds"] > 0
        assert evidence["cleanup_verified"] is True
        requests = [row for row in evidence["records"] if row["event"] == "qualified_recovery_request"]
        assert len(requests) == 2 and [row["attempt"] for row in requests] == [1, 2]
        assert evidence["recovery_attempts"] == 2
        assert session.run_count == 1
        assert not session.error
        resumes = [(at, payload) for at, payload in operations if payload.get("kind") == "Resume"]
        assert all(payload["session_id"] == evidence["session_id"] for _, payload in resumes)
        return SimpleNamespace(
            result=result,
            evidence=evidence,
            operations=operations,
            trap=trap,
            resumes=resumes,
            recovery_count=adapter._bench_policy.recovery_count,
            finished_at=finished_at,
        )
    finally:
        stop.set()
        publishing.set()
        withdrawal_sent.set()
        session.stopped.set()
        adapter.wait(3)
        camera.shutdown()
        server.shutdown()
        camera.server_close()
        server.server_close()
        for thread in (observer_thread, *worker_threads):
            thread.join(timeout=2)


@pytest.mark.skipif(sys.platform != "win32", reason="real AF_PIPE and installed Edge")
def test_current_resume_waits_for_original_episode_requalification(tmp_path):
    run = run_route_race(tmp_path, "requalifies")
    assert run.result.returncode == 0, (run.evidence["failure"], run.result.stdout, run.result.stderr)
    assert run.evidence["failure"] is None and run.evidence["virtual_stop"] is None
    assert len(run.resumes) == 2
    at, resumed = run.resumes[1]
    assert at >= run.trap["restored_at"] and resumed["bench_recovery"] == run.trap["proof"]
    assert not any(payload.get("kind") == "Stop" for _, payload in run.operations)
    held = [row for row in run.evidence["records"] if row["event"] == "recovery_request_held"]
    assert held and all({key: row[key] for key in run.trap["proof"]} == run.trap["proof"] for row in held)
    completions = [row for row in run.evidence["records"] if row["event"] == "recovery_episode_complete"]
    assert len(completions) == 2 and all(0 <= row["elapsed_ms"] < 10_000 for row in completions)
    assert run.recovery_count == 2


@pytest.mark.skipif(sys.platform != "win32", reason="real AF_PIPE and installed Edge")
@pytest.mark.parametrize("intervention", ["expires", "Pause", "Stop", "ClaimInput", "backend-refusal"])
def test_pending_resume_keeps_terminal_guards(tmp_path, intervention):
    run = run_route_race(tmp_path, intervention)
    assert run.result.returncode == 1 and run.evidence["failure"]
    assert run.recovery_count == 1
    completions = [row for row in run.evidence["records"] if row["event"] == "recovery_episode_complete"]
    assert len(completions) == 1
    if intervention == "backend-refusal":
        assert len(run.resumes) == 2  # One real refusal must never lead to a third HTTP Resume.
        assert run.trap["backend_refusal"] == {
            "accepted": False,
            "reason": "bench recovery qualification refused",
        }
        assert run.evidence["failure"] == "bench recovery qualification refused"
        assert run.evidence["virtual_stop"] is None
    else:
        assert len(run.resumes) == 1  # The held second request never reaches the backend.
        if intervention == "expires":
            assert run.finished_at - run.trap["approved_at"] < run.trap["remaining_at_approval"] + 2
            assert (
                "Virtual recovery episode deadline" in run.evidence["failure"]
                or run.evidence["virtual_stop"] is not None
            )
            if run.evidence["virtual_stop"] is not None:
                assert run.evidence["virtual_stop"]["reason"] == "recovery budget exhausted"
        else:
            assert run.trap["operator_result"]["accepted"] is True
            changed = run.trap["after_operator"]
            assert changed["virtual_bench"]["disarmed"] is True

            if intervention == "ClaimInput":
                assert changed["input_epoch"] != run.trap["proof"]["input_epoch"]
            if intervention == "Stop":
                assert changed["virtual_bench"]["disarm_reason"] == "explicit stop"
                assert run.trap["operator_result"]["session_id"] == run.evidence["session_id"]
                assert any(
                    event["event"] == "stop_requested" and event["session_id"] == run.evidence["session_id"]
                    for event in changed["events"]
                )
                assert run.evidence["final_phase"] == "operator_stopped"
                assert run.evidence["final_exit_code"] == 130
                assert run.evidence["virtual_stop"] is None
                # Native owned Stop can finish before the held route's next read.
                # Either ordering must retain the actual Stop result and no Resume.
                assert run.evidence["failure"].strip() in {
                    "Virtual recovery ownership, gate or episode changed",
                    "Run was not successful\n\n130 !== 0",
                }
            else:
                assert "Virtual recovery ownership, gate or episode changed" in run.evidence["failure"]
            assert run.finished_at - run.trap["restored_at"] < 3
