"""Hardware-free provenance and freshness checks for the AM1 console."""

from __future__ import annotations

from types import SimpleNamespace
from io import StringIO
import json

import numpy as np

from examples.alohamini.am1_console_bridge import (
    AM1ConsoleBridgeClient, make_console_live_sample_event, publish_console_telemetry_best_effort,
)
from tools.am1_console import ConsoleSessionAdapter
from tools.am1_console_model import ConsoleSnapshot, field
from tools.am1_session import SSHRemote
from tools.am1_session_remote import sample_pi_system


def test_servo_identity_and_units():
    cache = ConsoleSnapshot()
    cache.update({"event": "live_sample", "acquired_at_ns": 1_000_000_000,
                  "observation_sequence": 7, "host_observation_id": 9,
                  "follower_positions": {"arm_left_elbow_flex.pos": 2.0,
                                         "arm_right_elbow_flex.pos": -3.0},
                  "leader_positions": {"arm_left_elbow_flex.pos": 2.2},
                  "requested_targets": {"arm_right_elbow_flex.pos": -2.5},
                  "body_observation": {"lift_axis.height_mm": 10.25}})
    view = cache.snapshot(now_ns=1_200_000_000)
    servos = view["servos"]
    expected = field(2.0, "normalized -100..100", "Windows receipt of Pi follower observation",
                     1_000_000_000, "Live")
    assert {key: servos["follower.left_bus.arm_left_elbow_flex.pos"]["position"][key]
            for key in expected} == expected
    assert servos["follower.right_bus.arm_right_elbow_flex.pos"]["position"]["value"] == -3.0
    assert servos["leader.left_bus.arm_left_elbow_flex.pos"]["position"]["value"] == 2.2
    assert servos["follower.right_bus.arm_right_elbow_flex.pos"]["target"]["state"] == "Not sampled"
    assert view["body"]["lift_axis.height_mm"]["unit"] == "mm"
    assert view["body"]["x.vel"]["state"] == "Not sampled"
    assert view["observation"]["age_ms"] == 200.0


def test_missing_field_is_not_sampled():
    cache = ConsoleSnapshot()
    cache.update({"event": "live_sample", "acquired_at_ns": 10,
                  "follower_positions": {"arm_left_elbow_flex.pos": 1.0},
                  "requested_targets": {"x.vel": 0.0}, "body_observation": {}})
    view = cache.snapshot(now_ns=20)
    assert view["servos"]["follower.left_bus.arm_left_elbow_flex.pos"]["current"]["state"] == "Not sampled"
    assert view["servos"]["follower.right_bus.arm_right_elbow_flex.pos"]["position"]["value"] is None
    assert view["body"]["lift_axis.height_mm"]["value"] is None
    assert view["system"]["cpu_percent"]["state"] == "Unavailable"
    assert "battery_percent" not in view["system"]


def test_acquisition_age_survives_delivery_delay():
    cache = ConsoleSnapshot()
    cache.update({"event": "system_sample", "acquired_at_ns": 1_000_000_000,
                  "metrics": {"cpu_percent": 22.5}})
    view = cache.snapshot(now_ns=2_000_000_000)
    assert view["system"]["cpu_percent"]["acquired_at_ns"] == 1_000_000_000
    assert view["system"]["cpu_percent"]["state"] == "Snapshot"
    assert view["system"]["cpu_percent"]["age_ms"] == 1000.0


def test_snapshot_memory_bounded_and_faults_distinct():
    cache = ConsoleSnapshot(max_events=8)
    for index in range(100):
        cache.update({"event": "temperature_warning", "wall_time_ns": index + 1,
                      "reason": "isolated numeric reading"})
    cache.update({"event": "confirmed_motor_fault", "wall_time_ns": 101,
                  "reason": "servo status fault"})
    view = cache.snapshot(now_ns=102)
    assert len(view["events"]) == 8
    assert view["events"][-2]["severity"] == "warning"
    assert view["events"][-1]["severity"] == "fault"
    assert view["events"][-1]["reason"] == "servo status fault"


def test_native_sample_uses_actual_raw_keys_and_original_receipt_time(tmp_path):
    sample = SimpleNamespace(observed_at=10.0, observation_sequence=8,
                             follower_positions={"arm_left_elbow_flex.pos": 1.0},
                             arm_target={"arm_left_elbow_flex.pos": 1.5},
                             observation={"lift_axis.height_mm": 11.0, "x.vel": 0.0})
    event = make_console_live_sample_event(
        sample, {"arm_left_elbow_flex.pos": 1.5, "x.vel": 0.0},
        raw_keys=frozenset({"x.vel"}), host_feedback={"observation_id": 13, "state": "active", "epoch": 2},
        wall_ns=20_000_000_000, monotonic_now=10.25,
    )
    assert event["acquired_at_ns"] == 19_750_000_000
    assert event["body_observation"] == {"x.vel": 0.0}
    assert "x.vel" not in event["requested_targets"]  # The queue sample is not the sender's body action.
    assert event["host_observation_id"] == 13
    bridge = AM1ConsoleBridgeClient("unused", tmp_path / "unused", "session")
    bridge.publish_telemetry(event)
    assert bridge._telemetry_pending["live_sample"] == event


def test_adapter_receives_cached_data_without_motor_polling(tmp_path):
    class FakeSession:
        pass

    adapter = ConsoleSessionAdapter(SimpleNamespace(), tmp_path, FakeSession, bridge_factory=lambda *args: None)
    adapter._emit({"event": "system_sample", "acquired_at_ns": 100, "metrics": {"cpu_percent": 4.0}})
    state = adapter.state()
    assert state["telemetry"]["system"]["cpu_percent"]["value"] == 4.0


def test_pi_system_sampler_is_read_only_and_uses_actual_sources(tmp_path):
    proc = tmp_path / "proc"
    sysroot = tmp_path / "sys"
    proc.mkdir()
    (sysroot / "class" / "thermal" / "thermal_zone0").mkdir(parents=True)
    (proc / "stat").write_text("cpu  110 0 110 880 0 0 0 0\n", encoding="ascii")
    (proc / "meminfo").write_text("MemTotal: 1000 kB\nMemAvailable: 600 kB\n", encoding="ascii")
    (proc / "uptime").write_text("12.5 0\n", encoding="ascii")
    (sysroot / "class" / "thermal" / "thermal_zone0" / "temp").write_text("42000\n", encoding="ascii")
    metrics, counts = sample_pi_system(proc_root=proc, sys_root=sysroot, storage_root=tmp_path,
                                        previous_cpu=(200, 1000), throttling=lambda: "throttled=0x0")
    assert counts == (220, 1100)
    assert metrics["cpu_percent"] == 20.0
    assert metrics["memory_percent"] == 40.0
    assert metrics["cpu_temp_c"] == 42.0
    assert metrics["uptime_s"] == 12.5
    assert metrics["throttled_raw"] == "0x0"
    assert metrics["storage_free_bytes"] > 0


def test_pi_system_events_do_not_accumulate_in_lifecycle_queue(tmp_path):
    seen = []
    config = SimpleNamespace()
    remote = SSHRemote(config, "session", tmp_path, telemetry_sink=seen.append)
    remote.process = SimpleNamespace(stdout=StringIO(
        '{"event":"system_sample","acquired_at_ns":100,"metrics":{"cpu_percent":2}}\n'
        '{"event":"camera_ready","session_id":"session"}\n'))
    remote._read_events()
    assert [item["event"] for item in seen] == ["system_sample"]
    assert remote.events.qsize() == 1
    assert remote.events.get_nowait()["event"] == "camera_ready"


def test_only_actual_sent_action_updates_body_target():
    cache = ConsoleSnapshot()
    cache.update({"event": "live_sample", "acquired_at_ns": 100,
                  "follower_positions": {}, "leader_positions": {},
                  "requested_targets": {}, "body_observation": {"x.vel": 0.0}})
    cache.update({"event": "action_sent", "acquired_at_ns": 200, "action_sequence": 3,
                  "action_send_interval_ms": 100.0, "requested_targets": {"x.vel": 0.25}})
    view = cache.snapshot(now_ns=300)
    assert view["body"]["x.vel"]["value"] == 0.0
    assert view["body_targets"]["x.vel"]["value"] == 0.25
    assert view["body_targets"]["x.vel"]["source"] == "Windows native action sent to Pi"
    assert view["action"]["sequence"] == 3


def test_numpy_scalar_telemetry_is_bounded_and_json_safe():
    sample = SimpleNamespace(observed_at=1.0, observation_sequence=1,
                             follower_positions={"arm_left_elbow_flex.pos": np.float32(2)},
                             arm_target={"arm_left_elbow_flex.pos": np.float32(3)},
                             observation={"lift_axis.height_mm": np.float32(4)})
    event = make_console_live_sample_event(
        sample, {"arm_left_elbow_flex.pos": np.float32(3)},
        raw_keys=frozenset({"lift_axis.height_mm"}), host_feedback={},
        wall_ns=1_000_000_000, monotonic_now=1.0,
    )
    assert json.loads(json.dumps(event))["body_observation"]["lift_axis.height_mm"] == 4.0


def test_camera_health_uses_existing_status_without_private_source_paths():
    cache = ConsoleSnapshot()
    cache.update({"event": "camera_status", "acquired_at_ns": 1_000_000_000,
                  "roles": {"forward": {"state": "fresh", "age_ms": 110,
                                        "sequence": 13, "fps": 14.0,
                                        "device": "/dev/private-camera"}}})
    view = cache.snapshot(now_ns=1_100_000_000)
    assert view["cameras"]["forward"]["state"] == "fresh"
    assert view["cameras"]["forward"]["age_ms"] == 210.0
    assert view["cameras"]["forward"]["acquired_at_ns"] == 1_000_000_000
    assert "device" not in view["cameras"]["forward"]
    assert view["cameras"]["backward"]["state"] == "Unavailable"


def test_delayed_failed_and_out_of_order_camera_status_cannot_renew_liveness():
    cache = ConsoleSnapshot()
    cache.update({"event": "camera_status", "acquired_at_ns": 1_000_000_000,
                  "roles": {"forward": {"state": "fresh", "age_ms": 100, "sequence": 4}}})
    assert cache.snapshot(now_ns=1_700_000_000)["cameras"]["forward"]["age_ms"] == 800.0
    cache.update({"event": "camera_status_failed", "acquired_at_ns": 1_800_000_000})
    failed = cache.snapshot(now_ns=1_900_000_000)["cameras"]["forward"]
    assert failed["state"] == "Unavailable"
    assert failed["sequence"] == 4  # Last evidence remains, but is not live.
    cache.update({"event": "camera_status", "acquired_at_ns": 1_500_000_000,
                  "roles": {"forward": {"state": "fresh", "age_ms": 0, "sequence": 3}}})
    assert cache.snapshot(now_ns=2_000_000_000)["cameras"]["forward"]["state"] == "Unavailable"


def test_fast_telemetry_updates_cache_without_flooding_lifecycle_events(tmp_path):
    adapter = ConsoleSessionAdapter(SimpleNamespace(), tmp_path, object(), bridge_factory=lambda *args: None)
    pushed = []
    adapter.set_event_sink(pushed.append)
    for index in range(100):
        adapter._emit({"event": "action_sent", "acquired_at_ns": index + 1,
                       "action_sequence": index + 1, "requested_targets": {"x.vel": 0.0}})
    assert adapter.state()["telemetry"]["action"]["sequence"] == 100
    assert adapter.state()["events"] == []
    assert pushed == []


def test_system_samples_use_snapshot_provenance_and_servos_freeze_after_cleanup():
    cache = ConsoleSnapshot()
    cache.update({"event": "host_ready"})
    cache.update({"event": "system_sample", "acquired_at_ns": 100_000_000,
                  "metrics": {"cpu_temp_c": 42.0}})
    cache.update({"event": "live_sample", "acquired_at_ns": 100_000_000,
                  "follower_positions": {"arm_left_elbow_flex.pos": 1.0},
                  "leader_positions": {}, "body_observation": {}})
    active = cache.snapshot(now_ns=200_000_000)
    assert active["system"]["cpu_temp_c"]["state"] == "Snapshot"
    assert active["servos"]["follower.left_bus.arm_left_elbow_flex.pos"]["position"]["state"] == "Live"
    cache.update({"event": "cleanup", "cleanup_verified": True})
    stopped = cache.snapshot(now_ns=300_000_000)
    assert stopped["system"]["cpu_temp_c"]["state"] == "Snapshot"
    assert stopped["servos"]["follower.left_bus.arm_left_elbow_flex.pos"]["position"]["state"] == "Snapshot"


def test_pi_wall_clock_skew_does_not_change_windows_receipt_age(tmp_path, monkeypatch):
    from tools import am1_session

    monkeypatch.setattr(am1_session.time, "time_ns", lambda: 1_000_000_000)
    seen = []
    remote = SSHRemote(SimpleNamespace(), "session", tmp_path, telemetry_sink=seen.append)
    remote.process = SimpleNamespace(stdout=StringIO(
        '{"event":"system_sample","acquired_at_ns":9000000000000,"metrics":{"cpu_temp_c":40}}\n'))
    remote._read_events()
    cache = ConsoleSnapshot()
    cache.update(seen[0])
    view = cache.snapshot(now_ns=1_100_000_000)
    assert view["system"]["cpu_temp_c"]["age_ms"] == 100.0
    assert view["system_sample_pi_wall_ns"] == 9_000_000_000_000
    assert view["system"]["cpu_temp_c"]["state"] == "Snapshot"
    assert view["system"]["cpu_temp_c"]["acquired_at_ns"] == 9_000_000_000_000
    assert view["system"]["cpu_temp_c"]["windows_received_at_ns"] == 1_000_000_000
    assert "lower bound" in view["system"]["cpu_temp_c"]["age_basis"]


def test_scripted_input_is_not_mislabelled_as_a_physical_leader():
    sample = SimpleNamespace(observed_at=1.0, observation_sequence=1,
                             follower_positions={"arm_left_elbow_flex.pos": 1.0},
                             arm_target={"arm_left_elbow_flex.pos": 2.0}, observation={})
    scripted = make_console_live_sample_event(
        sample, {}, raw_keys=frozenset(), host_feedback={}, wall_ns=1_000_000_000,
        monotonic_now=1.0, leader_source="scripted")
    assert scripted["leader_positions"] == {}
    assert scripted["scripted_target"]["arm_left_elbow_flex.pos"] == 2.0
    cache = ConsoleSnapshot()
    cache.update(scripted)
    assert not any(key.startswith("leader.") for key in cache.snapshot(now_ns=1_000_000_000)["servos"])


def test_disconnected_physical_leaders_do_not_persist_into_next_session():
    cache = ConsoleSnapshot()
    cache.update({"event": "live_sample", "acquired_at_ns": 100,
                  "leader_positions": {"arm_left_elbow_flex.pos": 2.0}})
    assert "leader.left_bus.arm_left_elbow_flex.pos" in cache.snapshot(now_ns=101)["servos"]
    cache.update({"event": "client_exited"})
    assert "leader.left_bus.arm_left_elbow_flex.pos" not in cache.snapshot(now_ns=102)["servos"]


def test_existing_host_feedback_can_mark_pause_and_new_epoch_without_an_arm_read():
    cache = ConsoleSnapshot()
    cache.update({"event": "live_sample", "acquired_at_ns": 100,
                  "host_state": "active", "host_epoch": 2,
                  "follower_positions": {"arm_left_elbow_flex.pos": 1.0}})
    cache.update({"event": "host_feedback", "acquired_at_ns": 200,
                  "host_state": "paused", "host_epoch": 3, "host_observation_id": 12})
    view = cache.snapshot(now_ns=300)
    assert view["observation"]["host_state"] == "paused"
    assert view["observation"]["host_epoch"] == 3
    assert view["servos"]["follower.left_bus.arm_left_elbow_flex.pos"]["position"]["state"] == "Snapshot"


def test_console_subscriber_failure_cannot_escape_into_native_control():
    class FailingSubscriber:
        def publish_telemetry(self, event):
            raise RuntimeError("display backend closed")

    publish_console_telemetry_best_effort(FailingSubscriber(), lambda: {"event": "live_sample"})
    publish_console_telemetry_best_effort(FailingSubscriber(), lambda: (_ for _ in ()).throw(RuntimeError("format")))
