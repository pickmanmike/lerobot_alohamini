import json
from pathlib import Path

import pytest

from tools.am1_virtual_bench import VirtualBenchPolicy


def health(path, seq=1, **changes):
    data = {
        "generation": "test",
        "running": True,
        "recording": True,
        "challenge_qualified": True,
        "sequence": seq,
        "source_system_relative_ticks": seq * 100,
        "capture_age_ms": 20,
        "round_trip_ms": 30,
        "received_wall_time_ms": 10000,
    }
    data.update(changes)
    path.write_text(json.dumps(data))


def policy(tmp_path):
    path = tmp_path / "latest-health.json"
    health(path)
    cfg = {
        "observer_health_path": str(path),
        "observer_generation": "test",
        "verified_coverage": ["arms", "lift"],
        "required_camera_roles": [],
        "recovery_episode_seconds": 10,
        "max_recoveries": 3,
    }
    return VirtualBenchPolicy(cfg, log_root=tmp_path), path


def paused():
    return {
        "virtual_bench_admitted": True,
        "phase": "paused",
        "native_connected": True,
        "input_epoch": 1,
        "pending_gate": ["resume", 1],
        "pause_required": True,
        "body_release_required": False,
        "input_pause": {"reason": "bench required coverage", "pause_sequence": 1},
        "telemetry": {
            "observation": {
                "acquired_at_ns": 9_970_000_000,
                "age_ms": 30,
                "host_state": "paused",
                "host_epoch": 1,
            },
            "recovery_pose": {
                "acquired_at_ns": 9_970_000_000,
                "age_ms": 30,
                "max_difference": 1.0,
                "joint_count": 12,
            },
        },
        "error": None,
    }


def test_original_frame_clocks_cannot_be_renewed(tmp_path):
    p, path = policy(tmp_path)
    assert not p.observer(now=0, wall_ms=10000)
    health(path, 2)
    assert p.observer(now=0.1, wall_ms=10000)
    assert not p.observer(now=1.2, wall_ms=10000)
    health(path, 3, capture_age_ms=600)
    assert not p.observer(now=1.3, wall_ms=10000)


@pytest.mark.parametrize(
    "change",
    [
        {"generation": "old"},
        {"recording": False},
        {"challenge_qualified": False},
        {"round_trip_ms": 751},
        {"received_wall_time_ms": 12000},
    ],
)
def test_stale_or_unqualified_delivery_refused(tmp_path, change):
    p, path = policy(tmp_path)
    p.observer(now=0, wall_ms=10000)
    health(path, 2, **change)
    assert not p.observer(now=0.1, wall_ms=10000)


def test_recovery_bound_to_exact_gate_and_pose(tmp_path):
    p, path = policy(tmp_path)
    s = paused()
    p.evaluate(s, now=0, wall_ms=10000)
    health(path, 2)
    r = p.evaluate(s, now=0.1, wall_ms=10000)
    assert r["recovery_eligible"]
    assert p.approval({"pause_sequence": 1, "input_epoch": 1, "host_epoch": 1}, s, now=0.1, wall_ms=10000)
    assert not p.approval({"pause_sequence": 0, "input_epoch": 1, "host_epoch": 1}, s, now=0.1, wall_ms=10000)
    s["telemetry"]["recovery_pose"]["max_difference"] = 3.1
    assert not p.evaluate(s, now=0.2, wall_ms=10000)["recovery_eligible"]


def test_operator_and_foreign_epoch_never_clear(tmp_path):
    p, path = policy(tmp_path)
    s = paused()
    p.evaluate(s, now=0, wall_ms=10000)
    health(path, 2)
    p.disarm("operator")
    assert not p.evaluate(s, now=0.1, wall_ms=10000)["recovery_eligible"]
    p, path = policy(tmp_path)
    p.evaluate(s, now=0, wall_ms=10000)
    s["input_epoch"] = 2
    health(path, 2)
    assert p.evaluate(s, now=0.1, wall_ms=10000)["disarmed"]


def test_required_loss_holds_then_bounded_stop(tmp_path):
    p, path = policy(tmp_path)
    s = paused()
    s.update(phase="live", pause_required=False, pending_gate=None)
    r = p.evaluate(s, now=0, wall_ms=10000)
    assert r["action"] == "hold"
    s = paused()
    assert p.evaluate(s, now=10.1, wall_ms=10000)["action"] == "stop"


def test_config_cannot_enroll_unverified_or_foreign_files(tmp_path):
    p, path = policy(tmp_path)
    with pytest.raises(ValueError):
        VirtualBenchPolicy({**p.config, "verified_coverage": []}, log_root=tmp_path)
    with pytest.raises(ValueError):
        VirtualBenchPolicy(
            {**p.config, "observer_health_path": str(tmp_path.parent / "latest-health.json")},
            log_root=tmp_path,
        )


def test_http_reads_do_not_consume_pending_hold(tmp_path):
    p, path = policy(tmp_path)
    s = paused()
    s.update(phase="live", pause_required=False, pending_gate=None)
    assert p.evaluate(s, now=0, wall_ms=10000)["action"] == "hold"
    assert p.evaluate(s, now=0.1, wall_ms=10000)["action"] == "hold"


def test_episode_count_and_absolute_budget_never_reset(tmp_path):
    p, path = policy(tmp_path)
    s = paused()
    p.evaluate(s, now=0, wall_ms=10000)
    health(path, 2)
    assert p.evaluate(s, now=0.1, wall_ms=10000)["recovery_eligible"]
    # Current fresh files and repeated gate requests cannot extend the origin.
    health(path, 3)
    assert p.evaluate(s, now=9.9, wall_ms=10000)["remaining_seconds"] == pytest.approx(0.1)
    health(path, 4)
    assert p.evaluate(s, now=10, wall_ms=10000)["action"] == "stop"
    assert p.evaluate(s, now=10.1, wall_ms=10000)["disarmed"]


def test_completion_and_native_fault_do_not_qualify(tmp_path):
    p, path = policy(tmp_path)
    s = paused()
    p.evaluate(s, now=0, wall_ms=10000)
    health(path, 2)
    s["phase"] = "failed"
    assert not p.evaluate(s, now=0.1, wall_ms=10000)["recovery_eligible"]


def test_paused_pose_uses_original_qualified_sample_time():
    from types import SimpleNamespace

    from examples.alohamini.am1_console_bridge import CONSOLE_ARM_KEYS, make_console_host_feedback_event
    from tools.am1_console_model import ConsoleSnapshot

    positions = dict.fromkeys(CONSOLE_ARM_KEYS, 2.0)
    sample = SimpleNamespace(
        observed_at=10.0,
        observation_sequence=2,
        follower_positions=positions,
        arm_target=dict.fromkeys(positions, 3.0),
    )
    event = make_console_host_feedback_event(
        sample,
        {"state": "paused", "epoch": 1, "observation_id": 2},
        wall_ns=20_000_000_000,
        monotonic_now=10.1,
    )
    cache = ConsoleSnapshot()
    cache.update(event)
    first = cache.snapshot(now_ns=20_000_000_000)["recovery_pose"]
    assert first["joint_count"] == 12 and first["max_difference"] == 1
    assert first["age_ms"] == pytest.approx(100)
    assert cache.snapshot(now_ns=20_200_000_000)["recovery_pose"]["age_ms"] == pytest.approx(300)
    cache.update({**event, "acquired_at_ns": 19_000_000_000, "arm_target": positions})
    assert cache.snapshot(now_ns=20_200_000_000)["recovery_pose"]["max_difference"] == 1
    cache.update({**event, "acquired_at_ns": 20_300_000_000, "arm_target": {}})
    assert cache.snapshot(now_ns=20_300_000_000)["recovery_pose"] is None


def test_foreign_terminal_event_cannot_disarm_current_bench(tmp_path):
    from types import SimpleNamespace

    from tools.am1_console import ConsoleSessionAdapter

    p, path = policy(tmp_path)
    adapter = ConsoleSessionAdapter(SimpleNamespace(), tmp_path, SimpleNamespace())
    adapter._session_id = "current"
    adapter._bench_policy = p
    adapter._emit({"event": "client_exited", "session_id": "prior"})
    assert p.disarmed is None
    adapter._emit({"event": "client_exited", "session_id": "current"})
    assert p.disarmed == "terminal runtime event"


def test_later_pause_revokes_queued_resume_approval():
    from examples.alohamini.am1_console_bridge import AM1ConsoleInputState

    state = AM1ConsoleInputState("session", "owner")
    assert state.browser_keys(token="owner", epoch=1, seq=1, keys=[], active=True, now=1)
    state.request_pause("bench required coverage", now=1.01)
    state.request_gate("resume", host_epoch=1)
    assert state.browser_keys(token="owner", epoch=1, seq=2, keys=[], active=True, now=1.02)
    assert state.approve("resume", host_epoch=1, token="owner", now=1.03)
    state.request_pause("operator", now=1.04)
    assert state.browser_keys(token="owner", epoch=1, seq=3, keys=[], active=True, now=1.05)
    assert not state.gate_ack("resume", host_epoch=1, now=1.06)
    assert state.forced_pause


def test_prepared_gate_deadline_keeps_original_observer_age(tmp_path):
    p, path = policy(tmp_path)
    p.observer(now=0, wall_ms=10000)
    health(path, 2, capture_age_ms=490)
    assert p.observer(now=0.1, wall_ms=10000)
    assert p.coverage_expires_at() == pytest.approx(0.11)
    # A new console status read cannot extend the same frame's useful deadline.
    assert p.observer(now=0.105, wall_ms=10005)
    assert p.coverage_expires_at() == pytest.approx(0.11)


def test_concurrent_health_publication_uses_post_read_local_clock(tmp_path):
    p, path = policy(tmp_path)
    p.observer(now=0, wall_ms=10000)
    health(path, 2, received_wall_time_ms=10002)
    assert p.observer(now=0.001, wall_ms=10001, clock=lambda: 0.003, wall_clock_ms=lambda: 10003)
    assert p.coverage_expires_at() == pytest.approx(0.472)
    # Truly future timestamps remain invalid, rather than being clamped to fresh.
    health(path, 3, received_wall_time_ms=11000)
    assert not p.observer(now=0.004, wall_ms=10004, clock=lambda: 0.005, wall_clock_ms=lambda: 10005)


def test_transient_shared_read_preserves_original_expiry(tmp_path, monkeypatch):
    p, path = policy(tmp_path)
    p.observer(now=0, wall_ms=10000)
    health(path, 2)
    assert p.observer(now=0.1, wall_ms=10000)
    original_read = Path.read_text

    def refusal(self, *args, **kwargs):
        if self == path:
            raise PermissionError("synthetic Windows sharing refusal")
        return original_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", refusal)
    assert p.observer(now=0.2, wall_ms=10100)
    assert p.observer_metadata_uncertain
    assert p.coverage_expires_at() == pytest.approx(0.57)
    assert not p.observer(now=0.6, wall_ms=10500)
    assert p.sequence == 2 and p.advanced_at == 0.1


def test_return_transit_is_included_in_frame_age_upper_bound(tmp_path):
    p, path = policy(tmp_path)
    p.observer(now=0, wall_ms=10000)
    health(path, 2, capture_age_ms=20, round_trip_ms=700)
    assert not p.observer(now=0.1, wall_ms=10000)
    health(path, 3, capture_age_ms=20, round_trip_ms=450)
    assert p.observer(now=0.2, wall_ms=10000)
    assert p.coverage_expires_at() == pytest.approx(0.25)
    assert not p.observer(now=0.26, wall_ms=10060)


def test_recovery_rechecks_original_observer_deadline_after_preemption(tmp_path):
    p, path = policy(tmp_path)
    p.observer(now=0, wall_ms=10000)
    health(path, 2, round_trip_ms=450)
    monotonic = iter((0.1, 0.2))
    wall = iter((10000, 10100))
    result = p.evaluate(
        paused(),
        now=0.1,
        wall_ms=10000,
        clock=lambda: next(monotonic),
        wall_clock_ms=lambda: next(wall),
    )
    assert p.observer_expires_at == pytest.approx(0.15)
    assert not result["required_coverage_qualified"]
    assert not result["recovery_eligible"]
