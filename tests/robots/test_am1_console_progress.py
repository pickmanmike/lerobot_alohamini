"""Display-only startup evidence and native-duration correlation, no hardware."""

from types import SimpleNamespace

import pytest

from tools import am1_console_model as model
from tools.am1_console import ConsoleSessionAdapter
from tools.am1_session_remote import RemoteSupervisor
from tools.am1_session import SessionCoordinator


def test_startup_steps_follow_observed_phases_not_elapsed_time():
    progress = model.ConsoleProgress()
    assert progress.snapshot(now=100)["startup"] is None
    for step, stage in enumerate(("connections", "cameras", "lift_home", "lift_relief",
                                  "leader_preparation", "arm_sync", "final_readiness"), 1):
        progress.update({"event": "startup_progress", "stage": stage}, received_at=step)
        view = progress.snapshot(now=step + 100)["startup"]
        assert view["step"] == step
        assert view["stage"] == stage
        assert view["elapsed_s"] == 100
    progress.update({"event": "startup_progress", "stage": "future_unknown"}, received_at=200)
    progress.update({"event": "startup_progress", "stage": "lift_home"}, received_at=201)
    assert progress.snapshot(now=202)["startup"]["step"] == 7
    # Frame zero may be coalesced out; every sample identifies the actual new plan.
    progress.update({"event":"startup_progress","stage":"arm_sync","plan_started_at":203,
                     "frames_sent":11},received_at=203)
    assert progress.snapshot(now=204)["startup"]["step"] == 6
    assert progress.snapshot(now=204)["startup"]["elapsed_s"] == 1
    progress.update({"event":"startup_progress","stage":"final_readiness","plan_started_at":200},received_at=205)
    assert progress.snapshot(now=205)["startup"]["step"] == 6


def timing(start=100, sample=100, duration=90):
    return {"event": "live_timing", "clock": "windows_monotonic", "live_started_at": start,
            "deadline": start + duration, "duration_s": duration, "sampled_at": sample}


def test_countdown_uses_native_origin_survives_pause_and_does_not_reset_on_resume():
    progress = model.ConsoleProgress()
    progress.update({"event": "live_admitted", "acquired_at_ns": 1}, received_at=10)
    assert progress.snapshot(now=10)["live_timing"]["state"] == "Unavailable"
    progress.update(timing(sample=102), received_at=102.1)
    assert progress.snapshot(now=105)["live_timing"]["remaining_s"] == 85
    progress.update(timing(sample=115), received_at=115)
    progress.update({"event": "host_feedback", "host_state": "paused"}, received_at=115)
    assert progress.snapshot(now=115.2)["live_timing"]["remaining_s"] == pytest.approx(74.8)
    progress.update({"event": "live_admitted", "host_epoch": 4}, received_at=116)
    progress.update(timing(start=116, sample=116), received_at=116)
    assert progress.snapshot(now=116)["live_timing"]["deadline"] == 190
    assert progress.snapshot(now=116)["live_timing"]["state"] == "Current"
    assert progress.snapshot(now=118)["live_timing"]["state"] == "Stale"
    progress.update(timing(sample=190), received_at=190)
    assert progress.snapshot(now=190)["live_timing"]["remaining_s"] == 0
    assert progress.snapshot(now=190)["live_timing"]["state"] == "Elapsed"


@pytest.mark.parametrize("change", [{"clock": "pi_monotonic"}, {"sampled_at": 103},
                                   {"deadline": float("nan")}, {"duration_s": -1}])
def test_invalid_or_uncorrelated_timing_is_unavailable(change):
    progress = model.ConsoleProgress()
    progress.update({**timing(sample=102), **change}, received_at=102)
    assert progress.snapshot(now=102)["live_timing"]["state"] == "Unavailable"


def test_adapter_retains_timing_across_http_state_reads_and_stops_without_fake_completion(tmp_path, monkeypatch):
    now = [102.0]
    monkeypatch.setattr("tools.am1_console.time.monotonic", lambda: now[0])
    adapter = ConsoleSessionAdapter(SimpleNamespace(), tmp_path, SimpleNamespace())
    adapter._on_created("20261004T000000-1234abcd")
    adapter._emit({"event": "startup_progress", "stage": "arm_sync", "frames_sent": 11,
                   "frame_count": 301, "fps": 10, "remaining_estimate_s": 29.0})
    adapter._emit(timing(sample=102))
    now[0] = 103
    assert adapter.state()["progress"]["live_timing"]["remaining_s"] == 87
    assert adapter.state()["progress"]["startup"]["frames_sent"] == 11
    adapter._emit({"event": "stop_requested"})
    assert adapter.state()["phase"] == "stopping"
    assert adapter.state()["cleanup_verified"] is None
    assert adapter.state()["progress"]["live_timing"]["state"] == "Stopped"


def test_owner_readiness_parser_reports_home_and_relief_from_actual_records_only():
    supervisor = RemoteSupervisor.__new__(RemoteSupervisor)
    supervisor._last_startup_stage = None
    emitted = []
    supervisor.emit = lambda kind, **fields: emitted.append({"event":kind, **fields})
    assert supervisor._host_readiness_progress('[LIFT OPERATIONAL] {"phase":"homing"}') is False
    assert emitted[-1]["stage"] == "lift_home"
    assert supervisor._host_readiness_progress('[LIFT OPERATIONAL] {"phase":"relief"}') is False
    assert emitted[-1]["stage"] == "lift_relief"
    count = len(emitted)
    assert supervisor._host_readiness_progress('[LIFT OPERATIONAL] {"phase":"future_phase"}') is False
    assert len(emitted) == count
    assert supervisor._host_readiness_progress('[LIFT OPERATIONAL] {"phase":"operational_ready"}') is True
    assert emitted[-1]["stage"] == "leader_preparation"


def test_display_progress_failure_does_not_interrupt_session_lifecycle():
    engine = SessionCoordinator.__new__(SessionCoordinator)
    def fail(event):
        raise RuntimeError("display unavailable")
    engine.emit = fail
    engine._display_progress("connections", source="session preflight")


def test_final_failure_reaches_the_existing_adapter_details(tmp_path):
    adapter=ConsoleSessionAdapter(SimpleNamespace(),tmp_path,SimpleNamespace())
    adapter._on_created("20261004T000000-1234abcd")
    adapter._emit({"event":"session_complete", "final_exit_code":2, "cleanup_verified":False,
                   "failure":"original safety refusal"})
    assert adapter.state()["error"] == "original safety refusal"


def test_progress_emission_failure_cannot_change_host_readiness():
    supervisor=RemoteSupervisor.__new__(RemoteSupervisor)
    supervisor._last_startup_stage=None
    supervisor.emit=lambda *args,**kwargs: (_ for _ in ()).throw(RuntimeError("display closed"))
    assert supervisor._host_readiness_progress('[LIFT OPERATIONAL] {"phase":"operational_ready"}') is True
