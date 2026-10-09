"""Scripted input contracts: real client/Local paths, fake feedback, no hardware."""
from __future__ import annotations

import importlib.util
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("reply_period,poll_ms,min_progress,max_progress", [
    (0.1, 150, 4.8, 6.3),  # Control: fresh replies, usually no empty poll.
    (0.1, 25, 4.8, 6.3),   # Same replies with intervening harmless empty polls.
    (0.2, 25, 2.3, 3.2),   # Keep the existing one-frame-per-valid-call cap.
])
def test_actual_local_request_window_keeps_progress_between_empty_polls(
    capsys, reply_period, poll_ms, min_progress, max_progress,
):
    """Real Local producer, sender, client token window and loopback-only peers."""
    from lerobot.robots.alohamini import alohamini_host
    from lerobot.robots.alohamini.alohamini_client import AlohaMiniClient
    from lerobot.robots.alohamini.config_alohamini import AlohaMiniClientConfig

    m = module()
    initial = pose(m)
    positions = dict(initial)
    actions, records, errors = [], [], []
    control = alohamini_host.AM1LocalControl()

    class Host:
        def send_action(self, action):
            actions.append(dict(action))
            positions.update({key: action[key] for key in initial if key in action})
        def stop_motion(self): pass
        def hold_follower_arms(self): pass

    zmq = alohamini_host.zmq
    context = zmq.Context()
    commands = context.socket(zmq.PULL)
    observations = context.socket(zmq.ROUTER)
    command_port = commands.bind_to_random_port("tcp://127.0.0.1")
    observation_port = observations.bind_to_random_port("tcp://127.0.0.1")
    stop = threading.Event()
    periodic = threading.Event()

    def serve():
        pending = []
        next_reply = time.monotonic()
        try:
            while not stop.is_set():
                try:
                    control.apply(Host(), json.loads(commands.recv_string(zmq.NOBLOCK)))
                except zmq.Again:
                    pass
                try:
                    pending.append(observations.recv_multipart(zmq.NOBLOCK))
                except zmq.Again:
                    pass
                if not periodic.is_set() or time.monotonic() >= next_reply:
                    for identity, token in pending:
                        observation = dict(positions)
                        control.annotate(observation)
                        observations.send_multipart([identity, token, json.dumps(observation).encode()], zmq.NOBLOCK)
                    pending.clear()
                    next_reply = time.monotonic() + reply_period
                time.sleep(0.001)
        except BaseException as exc:
            errors.append(exc)
        finally:
            commands.close(0)
            observations.close(0)

    peer = threading.Thread(target=serve, name="script-clock-loopback-peer")
    peer.start()
    robot = AlohaMiniClient(AlohaMiniClientConfig(
        remote_ip="127.0.0.1", port_zmq_cmd=command_port,
        port_zmq_observations=observation_port, robot_model="alohamini1",
        id="fake-script-clock", cameras={}, observation_request_window=3,
        polling_timeout_ms=poll_ms, command_send_timeout_ms=50,
    ))
    provider = m.ScriptedLeaderInput(initial, joint_keys=m.AM1_ARM_POSITION_KEYS, fps=10, emit=records.append)
    try:
        robot.connect()
        robot.get_observation()
        result = m.run_am1_live_sender(
            robot, provider, initial_arm_target=initial,
            initial_observation_sequence=robot.observation_sequence,
            initial_follower_observed_at=robot.latest_observation_received_at,
            initial_follower_positions=initial, fps=10, duration_s=6.2,
            live_arm_scope="both", profile_cadence=True,
            body_action_supplier=m.make_zero_action, recovery_enabled=True,
            scripted_input=provider, announce_active=periodic.set,
        )
        assert result == "duration_expired" and not provider.complete
        assert min_progress <= provider.elapsed_s <= max_progress
        assert max(a[m.AM1_ARM_POSITION_KEYS[0]] for a in actions) > 12.2
        assert all(a[key] == 0 for a in actions for key in m.make_zero_action())
        for before, after in zip(actions, actions[1:]):
            assert max(abs(after[k] - before[k]) for k in initial) <= .751
        output = capsys.readouterr().out
        assert '"event": "am1_local_paused"' not in output
        cadence = next(json.loads(line) for line in output.splitlines()
                       if '"event": "am1_client_action_cadence"' in line)
        if poll_ms == 25:
            assert cadence["observation_timeout_count"] > 20
            assert cadence["script_clock"]["preserved_empty_polls"] > 20
        assert cadence["script_clock"]["trajectory_s"] == pytest.approx(provider.elapsed_s, abs=1e-6)
        assert cadence["script_clock"]["stale_replies"] == 0
        assert cadence["action_sequence"] >= 50 and cadence["body_command_expiration_count"] == 0
        assert not any(t.name == "am1-live-action-sender" and t.is_alive() for t in threading.enumerate())
        robot.send_action(m.make_zero_action())
        deadline = time.monotonic() + .5
        while control.state != "stopped" and time.monotonic() < deadline:
            time.sleep(.002)
        assert control.state == "stopped"
        assert errors == []
    finally:
        if robot.is_connected:
            robot.disconnect()
        stop.set()
        peer.join(timeout=2)
        context.term()
        assert not peer.is_alive()


def module():
    directory = Path(__file__).resolve().parents[2] / "examples" / "alohamini"
    name = "am1_scripted_teleoperate_test"
    spec = importlib.util.spec_from_file_location(name, directory / "teleoperate_bi.py")
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    sys.path.insert(0, str(directory))
    try:
        spec.loader.exec_module(result)
    finally:
        sys.path.remove(str(directory))
    return result


def pose(m):
    return {key: (50.0 if "gripper" in key else 12.0) for key in m.AM1_ARM_POSITION_KEYS}


def arguments(stop_file):
    return ["--leader_source", "scripted", "--motion_profile", "ArmSmoke",
            "--local_mode", "--no_cameras", "--no_rerun", "--start_paused",
            "--startup_mode", "sync", "--startup_sync_duration_s", "30", "--fps", "10",
            "--duration_s", "180", "--profile_cadence", "--external_stop_file", str(stop_file),
            "--unified_session_enter_confirmations"]


def test_explicit_scripted_entrypoint_never_resolves_ports(monkeypatch, tmp_path):
    m = module()
    monkeypatch.setattr(m, "resolve_leader_ports", lambda *a, **kw: pytest.fail("leader port access"))
    args = m.parse_args(arguments(tmp_path / "stop"), platform_name="Windows")
    assert args.leader_source == "scripted" and args.motion_profile == "ArmSmoke"
    assert not args.no_leader  # A provider, not a pretend physical connection or disabled arm path.


@pytest.mark.parametrize("replacement", [
    ["--leader_source", "physical"], ["--motion_profile", "wrong"],
    ["--robot_model", "alohamini2"], ["--robot_model", "alohamini2pro"], ["--no_robot"],
    ["--no_leader"], ["--require_calibration_match"],
])
def test_scripted_is_explicit_am1_only_without_unsafe_fallback(tmp_path, replacement):
    m = module()
    with pytest.raises(SystemExit):
        m.parse_args(arguments(tmp_path / "stop") + replacement, platform_name="Windows")


def test_provider_reads_are_pure_and_pause_has_no_catchup_or_rebase():
    m = module()
    initial = pose(m)
    provider = m.ScriptedLeaderInput(initial, joint_keys=m.AM1_ARM_POSITION_KEYS, fps=10)
    expected = {k.removeprefix("arm_"): v for k, v in initial.items()}
    for _ in range(50):
        assert provider.get_action() == expected
    assert provider.elapsed_s == 0
    provider.admit(10.0)
    for index in range(35):
        provider.advance(10.1 + index / 10, initial, index + 1)
    before = provider.get_action()
    assert provider.elapsed_s == pytest.approx(3.5)
    assert before != expected
    provider.freeze()
    provider.advance(300.0, {k: v + 1 for k, v in initial.items()}, 40)
    assert provider.get_action() == before
    provider.admit(400.0)
    provider.advance(420.0, initial, 41)
    assert provider.elapsed_s == pytest.approx(3.6)  # Not 20 seconds of queued motion.
    assert max(abs(provider.get_action()[k] - before[k]) for k in before) <= 0.101


def test_complete_profile_exact_schema_small_inward_segments_and_measured_evidence():
    m = module()
    initial = {k: (0.0 if "gripper" in k else 99.5) for k in m.AM1_ARM_POSITION_KEYS}
    records = []
    provider = m.ScriptedLeaderInput(initial, joint_keys=m.AM1_ARM_POSITION_KEYS, fps=10, emit=records.append)
    provider.admit(0)
    previous = provider.get_action()
    maximum = {k: 0.0 for k in initial}
    for index in range(1, 1000):
        measured = {f"arm_{k}": v for k, v in previous.items()}
        provider.advance(index / 10, measured, index)
        current = provider.get_action()
        assert set(current) == {k.removeprefix("arm_") for k in initial}
        changed = []
        for key, origin in initial.items():
            value = current[key.removeprefix("arm_")]
            assert (0 if "gripper" in key else -100) <= value <= 100
            assert abs(value - previous[key.removeprefix("arm_")]) <= .101
            maximum[key] = max(maximum[key], abs(value - origin))
            if value != origin:
                changed.append(key)
        assert len(changed) <= 1
        previous = current
        if provider.complete:
            break
    assert provider.complete and provider.elapsed_s == pytest.approx(88)
    assert all(value == pytest.approx(3) for value in maximum.values())
    assert current == {k.removeprefix("arm_"): v for k, v in initial.items()}
    plans = [r for r in records if r["event"] == "am1_scripted_segment_plan"]
    assert len(plans) == 12
    assert all(r["target"] == r["origin"] + (3 if "gripper" in r["joint"] else -3) for r in plans)
    measured = [r for r in records if r["event"] == "am1_scripted_segment_observation"]
    assert measured and all("observed_displacement" in r and "tracking_error" in r for r in measured)


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), 101.0])
def test_provider_rejects_invalid_seed_instead_of_inventing_a_start(invalid):
    m = module()
    initial = pose(m)
    initial[m.AM1_ARM_POSITION_KEYS[0]] = invalid
    with pytest.raises(ValueError):
        m.ScriptedLeaderInput(initial, joint_keys=m.AM1_ARM_POSITION_KEYS, fps=10)


def test_provider_refuses_duplicate_feedback_and_clock_regression():
    m = module()
    provider = m.ScriptedLeaderInput(pose(m), joint_keys=m.AM1_ARM_POSITION_KEYS, fps=10)
    provider.admit(0)
    provider.advance(.1, pose(m), 1)
    with pytest.raises(ValueError, match="sequence"):
        provider.advance(.2, pose(m), 1)
    with pytest.raises(ValueError, match="clock"):
        provider.advance(0, pose(m), 2)


def repeat_provider(m, *, records=None):
    factory = getattr(m, "ArmSmokeRepeatInput", None)
    assert factory is not None, "opt-in repeated native provider is missing"
    return factory(pose(m), joint_keys=m.AM1_ARM_POSITION_KEYS, fps=10,
                   emit=(records if records is not None else []).append)


def first_repeat_boundary(provider, initial):
    provider.admit(0)
    for seq in range(1, 881):
        provider.advance(seq / 10, initial, seq)
    assert provider.elapsed_s == pytest.approx(88)
    assert not provider.complete


def test_repeat_completes_four_continuous_cycles_with_one_original_seed():
    m = module()
    original = pose(m)
    mutable_seed = dict(original)
    records = []
    factory = getattr(m, "ArmSmokeRepeatInput", None)
    assert factory is not None, "opt-in repeated native provider is missing"
    provider = factory(mutable_seed, joint_keys=m.AM1_ARM_POSITION_KEYS, fps=10, emit=records.append)
    mutable_seed.update({k: v + 2 for k, v in mutable_seed.items()})
    provider.admit(0)
    previous = provider.get_action()
    for seq in range(1, 3701):
        measured = {f"arm_{k}": v + .5 for k, v in previous.items()}
        provider.advance(seq / 10, measured, seq)
        current = provider.get_action()
        assert max(abs(current[k] - previous[k]) for k in current) <= .101
        assert sum(current[k.removeprefix('arm_')] != v for k, v in original.items()) <= 1
        previous = current
        if provider.complete:
            break
    assert provider.complete and provider.elapsed_s == pytest.approx(352)
    assert seq > 3520  # Fresh boundary windows are evidence, never trajectory padding.
    assert provider.origin == original
    plans = [r for r in records if r['event'] == 'am1_scripted_segment_plan']
    assert len(plans) == 48
    assert all(r['origin'] == original[r['joint']] and r['target'] == original[r['joint']] + 3 for r in plans)
    assert not any(r['event'] == 'am1_scripted_input_summary' for r in records)
    provider.finish('script_complete')
    summaries = [r for r in records if r['event'] == 'am1_scripted_input_summary']
    assert len(summaries) == 1
    assert summaries[0]['profile_complete'] is True
    assert summaries[0]['motion_profile'] == 'ArmSmokeRepeat'
    assert summaries[0]['cycles_completed'] == summaries[0]['returns_qualified'] == 4


def test_repeat_boundary_needs_three_advancing_returns_over_point_two_seconds():
    m = module()
    records = []
    provider = repeat_provider(m, records=records)
    first_repeat_boundary(provider, pose(m))
    original_action = provider.get_action()
    for seq, now in [(881, 88.1), (882, 88.2)]:
        provider.advance(now, pose(m), seq)
        assert provider.elapsed_s == 88 and provider.get_action() == original_action
    provider.advance(88.3, pose(m), 883)
    provider.advance(88.4, pose(m), 884)
    assert provider.elapsed_s == pytest.approx(88.1)
    boundary = [r for r in records if r['event'] == 'am1_scripted_return_qualified']
    assert len(boundary) == 1 and boundary[0]['cycle'] == 1
    assert boundary[0]['sample_count'] == 3 and boundary[0]['span_s'] >= .2 - 1e-9


def test_repeat_refuses_drift_duplicate_or_invalid_boundary_feedback():
    m = module()
    provider = repeat_provider(m)
    first_repeat_boundary(provider, pose(m))
    with pytest.raises(ValueError, match='sequence'):
        provider.advance(88.1, pose(m), 880)
    drift = pose(m)
    drift[m.AM1_ARM_POSITION_KEYS[0]] += 3.01
    with pytest.raises(ValueError, match='return'):
        provider.advance(88.2, drift, 881)
    assert provider.elapsed_s == 88 and not provider.complete
    assert provider.origin == pose(m)


def test_repeat_freeze_discards_partial_return_window_without_catchup():
    m = module()
    provider = repeat_provider(m)
    first_repeat_boundary(provider, pose(m))
    provider.advance(88.1, pose(m), 881)
    provider.advance(88.2, pose(m), 882)
    provider.freeze()
    provider.advance(299.0, pose(m), 883)
    provider.admit(300.0)
    provider.advance(300.1, pose(m), 884)
    provider.advance(300.2, pose(m), 885)
    assert provider.elapsed_s == 88 and not provider.complete
    provider.advance(300.3, pose(m), 886)
    provider.advance(320.0, pose(m), 887)
    assert provider.elapsed_s == pytest.approx(88.1)
    assert provider.origin == pose(m)


def test_repeat_cancellation_at_boundary_never_starts_another_cycle():
    m = module()
    records = []
    provider = repeat_provider(m, records=records)
    first_repeat_boundary(provider, pose(m))
    provider.advance(88.1, pose(m), 881)
    provider.finish('explicit_stop')
    provider.admit(100.0)
    provider.advance(100.1, pose(m), 882)
    provider.finish('script_complete')
    assert provider.elapsed_s == 88 and not provider.complete
    assert len([r for r in records if r['event'] == 'am1_scripted_segment_plan']) == 12
    summary = [r for r in records if r['event'] == 'am1_scripted_input_summary']
    assert len(summary) == 1 and summary[0]['stop_reason'] == 'explicit_stop'
    assert summary[0]['profile_complete'] is False


@pytest.mark.parametrize('losing_state', ['paused', 'epoch', 'stopped', 'finished', 'dead', 'duration', 'fault', 'stale'])
def test_real_sender_blocks_repeat_boundary_when_admission_or_feedback_is_lost(losing_state):
    m = module()
    provider = repeat_provider(m)
    first_repeat_boundary(provider, pose(m))
    sender = m.AM1LiveActionSender(
        object(), initial_action=m.make_am1_live_action(pose(m)), initial_observation_sequence=880,
        fps=10, duration_s=1 if losing_state == 'duration' else 420, profile_cadence=False,
        recovery_enabled=True, monotonic=lambda: 88.1,
    )
    sender._thread = SimpleNamespace(is_alive=lambda: losing_state != 'dead')
    sender._live_started_at = 0
    if losing_state == 'paused':
        sender._recovery_state = 'paused'
    if losing_state == 'epoch':
        sender._recovery_epoch = 2
    if losing_state == 'stopped':
        sender._stop_requested.set()
    if losing_state == 'finished':
        sender._finished.set()
    if losing_state == 'fault':
        sender._error = RuntimeError('real sender fault')
    sample = m.AM1LiveSample(881, 87 if losing_state == 'stale' else 88.1,
                            pose(m), pose(m), pose(m), 0.0)
    assert sender.advance_scripted_input(provider, sample, epoch=0, clock_active=True) is None
    assert provider.elapsed_s == 88 and not provider.complete and provider.returns_qualified == 0


class LocalHarness:
    """Synchronous fake worker/host: actual startup and Local producer run with fake time."""

    def __init__(self, monkeypatch, m, *, quit_after=None, fault_after=None, pause=False,
                 seed_fault=None, cleanup_error=False):
        self.now = 0.0
        self.events = []
        self.initial = pose(m)
        self.positions = dict(self.initial)
        self.worker = None
        self.provider = None
        self.paused = False
        self.pause_elapsed = []
        harness = self

        class Robot:
            def __init__(self, config):
                self.config = config
                self.observation_sequence = 0
                self.latest_raw_observation_keys = frozenset(harness.initial)
                self.latest_observation_roundtrip_age_s = 0.0
                self.latest_observation_received_at = 0.0
                self.latest_observation_error = None
                self.latest_am1_local_feedback = dict(version=1, state="ready", epoch=-1, observation_id=0)

            def connect(self, **kwargs): harness.events.append(("connect",))
            def disconnect(self):
                harness.events.append(("disconnect",))
                if cleanup_error:
                    raise OSError("fake disconnect failure")
            def retire_observation_requests(self): harness.events.append(("retire",))
            def send_action(self, action):
                harness.events.append(("outer_action", dict(action)))
                harness.positions.update({k: action[k] for k in harness.initial if k in action})
            def get_observation(self):
                harness.now += .05
                self.observation_sequence += 1
                self.latest_observation_received_at = harness.now
                if seed_fault:
                    self.latest_observation_roundtrip_age_s = seed_fault
                worker = harness.worker
                if worker:
                    elapsed = 0 if worker.live_started is None else harness.now - worker.live_started
                    if fault_after is not None and elapsed >= fault_after:
                        raise m.SafetyRefusal("fake genuine host fault")
                    if pause and not harness.paused and elapsed >= 4:
                        harness.paused = True
                        worker.request_pause("fake observation gap")
                        harness.now += 1.1
                    if worker.state in {"paused", "resuming"}:
                        harness.pause_elapsed.append(harness.provider.elapsed_s)
                    state = "active" if worker.state == "resuming" else worker.state
                    self.latest_am1_local_feedback.update(state=state, epoch=worker.epoch)
                self.latest_am1_local_feedback["observation_id"] = self.observation_sequence
                return dict(harness.positions)

        class Worker:
            def __init__(self, robot, **kwargs):
                harness.worker = self
                self.action = dict(kwargs["initial_action"])
                self.stopped = False
                self.state = "active"
                self.epoch = 0
                self.pause_at = None
                self.live_started = None
                self.duration = kwargs["duration_s"]
                self.body = kwargs["body_mailbox"]
            def start(self): harness.events.append(("private_socket_open",))
            def is_alive(self):
                if self.stopped or (self.live_started is not None and harness.now - self.live_started >= self.duration):
                    return False
                if self.state != "paused":
                    action = {**self.action, **self.body.snapshot(now=harness.now)}
                    harness.events.append(("worker_action", action))
                    harness.positions.update({k: action[k] for k in harness.initial})
                return True
            def snapshot(self): return m.AM1LiveActionSenderSnapshot(1, 100, 100, None, None)
            def recovery_snapshot(self): return self.state, self.epoch, self.pause_at, "fake observation gap"
            def note_fresh_observation(self, value): pass
            def mark_live_admitted(self):
                self.live_started = harness.now
                harness.events.append(("admitted",))
            def enable_initial_catchup(self): pass
            def publish(self, action): self.action = dict(action)
            def advance_scripted_input(self, provider, sample, *, epoch, clock_active):
                if (self.state != "active" or self.epoch != epoch or self.stopped
                        or self.live_started is None or harness.now - self.live_started >= self.duration):
                    return None
                if not clock_active:
                    provider.admit(harness.now)
                provider.advance(harness.now, sample.follower_positions, sample.observation_sequence, defer_events=True)
                if provider.complete:
                    self.stop_after_clearing_body()
                    return "script_complete"
                return "advanced"
            def request_pause(self, reason):
                self.state = "paused"
                self.epoch += 1
                self.pause_at = harness.now
                self.body.suspend()
                harness.events.append(("host_hold", dict(harness.positions)))
            def note_initial_pause_acknowledged(self): pass
            def resume_from(self, follower_positions, **kwargs):
                self.state = "resuming"
                self.epoch += 1
                self.action = m.make_am1_live_action(follower_positions)
                harness.events.append(("resume_from_hold", dict(follower_positions)))
            def acknowledge_resume(self, **kwargs):
                self.state = "active"
                self.body.resume()
                harness.events.append(("resume_ack",))
            def stop_after_clearing_body(self):
                self.body.suspend()
                self.stopped = True
                harness.events.append(("sender_stop",))
            def join(self): harness.events.append(("private_socket_close_and_join",))

        class Keyboard:
            is_connected = True
            def __init__(self, config): pass
            def connect(self): pass
            def disconnect(self): harness.events.append(("keyboard_disconnect",))
            def get_action(self):
                if quit_after is not None and harness.worker and harness.worker.live_started is not None:
                    if harness.now - harness.worker.live_started >= quit_after:
                        return {"q"}
                return {"w", "a", "u", "j", "x", "+"}

        original = m.ScriptedLeaderInput
        def provider(*args, **kwargs):
            harness.provider = original(*args, **kwargs)
            return harness.provider
        monkeypatch.setattr(m, "ScriptedLeaderInput", provider)
        monkeypatch.setattr(m, "AlohaMiniClient", Robot)
        monkeypatch.setattr(m, "KeyboardTeleop", Keyboard)
        monkeypatch.setattr(m, "AM1LiveActionSender", Worker)
        monkeypatch.setattr(m, "BiSOLeader", lambda *a, **k: pytest.fail("physical leader constructed"))
        monkeypatch.setattr(m, "make_leader_config", lambda *a, **k: pytest.fail("physical calibration read"))
        monkeypatch.setattr(m, "resolve_leader_ports", lambda *a, **k: pytest.fail("COM enumerated"))
        monkeypatch.setattr(m, "make_local_body_action", lambda *a, **k: pytest.fail("body key conversion"))

    def clock(self): return self.now
    def sleep(self, duration): self.now += duration
    def run(self, m, tmp_path, *, duration=180, input_fn=None):
        args = m.parse_args(arguments(tmp_path / "stop") + ["--duration_s", str(duration)], platform_name="Windows")
        return m.run_teleoperation(args, input_fn=input_fn or (lambda prompt: ""),
                                  monotonic=self.clock, sleep_fn=self.sleep)


@pytest.mark.parametrize('cancel_after', [None, 88.1])
def test_actual_native_entrypoint_dispatches_repeat_and_preserves_cancellation(
    monkeypatch, tmp_path, capsys, cancel_after,
):
    m = module()
    harness = LocalHarness(monkeypatch, m, quit_after=cancel_after)
    args = m.parse_args(arguments(tmp_path / 'stop') +
                        ['--motion_profile', 'ArmSmokeRepeat', '--duration_s', '420'], platform_name='Windows')
    factory = m.ArmSmokeRepeatInput
    def provider(*a, **kw):
        harness.provider = factory(*a, **kw)
        return harness.provider
    monkeypatch.setattr(m, 'ArmSmokeRepeatInput', provider)
    assert m.run_teleoperation(args, input_fn=lambda prompt: '',
                               monotonic=harness.clock, sleep_fn=harness.sleep) == 0
    output = capsys.readouterr().out
    summary = [json.loads(line) for line in output.splitlines()
               if line.startswith('{') and json.loads(line).get('event') == 'am1_scripted_input_summary']
    assert len(summary) == 1 and summary[0]['motion_profile'] == 'ArmSmokeRepeat'
    assert summary[0]['profile_complete'] is (cancel_after is None)
    assert summary[0]['stop_reason'] == ('script_complete' if cancel_after is None else 'manual_q')
    assert sum(e[0] == 'connect' for e in harness.events) == 1
    assert sum(e[0] == 'admitted' for e in harness.events) == 1
    assert harness.events.index(('private_socket_close_and_join',)) < harness.events.index(('disconnect',))
    if cancel_after is None:
        assert summary[0]['trajectory_s'] == 352
        assert summary[0]['cycles_completed'] == summary[0]['returns_qualified'] == 4
    else:
        assert not harness.provider.complete and harness.provider.elapsed_s < 100


@pytest.mark.parametrize('duration', [0, 180, 419, 421, 1800])
def test_repeat_native_ceiling_cannot_be_omitted_shortened_or_extended(tmp_path, duration):
    m = module()
    with pytest.raises(SystemExit):
        m.parse_args(arguments(tmp_path / 'stop') +
                     ['--motion_profile', 'ArmSmokeRepeat', '--duration_s', str(duration)], platform_name='Windows')


@pytest.mark.parametrize("kind", ["empty", "old_reply", "combined_age"])
def test_fake_time_local_empty_polls_are_distinct_from_unqualified_replies(monkeypatch, tmp_path, capsys, kind):
    m = module()
    harness = LocalHarness(monkeypatch, m, quit_after=6.2)
    read = m.AlohaMiniClient.get_observation
    polls = []

    def interleaved(robot):
        worker = harness.worker
        if worker is not None and worker.live_started is not None:
            polls.append(harness.now)
            if len(polls) % 2:
                harness.now += .05
                if kind != "empty":
                    robot.observation_sequence += 1
                    robot.latest_am1_local_feedback["observation_id"] = robot.observation_sequence
                    robot.latest_observation_received_at = harness.now - (.5 if kind == "combined_age" else 0)
                    robot.latest_observation_roundtrip_age_s = .6 if kind == "combined_age" else 1.01
                return dict(harness.positions)
        robot.latest_observation_roundtrip_age_s = 0.0
        return read(robot)

    monkeypatch.setattr(m.AlohaMiniClient, "get_observation", interleaved)
    assert harness.run(m, tmp_path) == 0
    actions = [e[1] for e in harness.events if e[0] == "worker_action"]
    assert all(a[key] == 0 for a in actions for key in m.make_zero_action())
    if kind == "empty":
        assert 6.0 <= harness.provider.elapsed_s <= 6.3
        assert max(a[m.AM1_ARM_POSITION_KEYS[0]] for a in actions) > 14.5
    elif kind == "old_reply":
        assert harness.provider.elapsed_s == 0  # Each real stale reply resets admission time.
        assert all(a[m.AM1_ARM_POSITION_KEYS[0]] == 12 for a in actions)
    else:
        assert any(e[0] == "host_hold" for e in harness.events)
        assert harness.provider.elapsed_s < 1
    assert harness.events.index(("private_socket_close_and_join",)) < harness.events.index(("disconnect",))


@pytest.mark.parametrize("enter_delay", [0.0, 20.0])
def test_fake_time_script_clock_discards_recovery_and_enter_waits(monkeypatch, tmp_path, capsys, enter_delay):
    """Long waits are virtual; real Local recovery still qualifies same-host epochs."""
    m = module()
    harness = LocalHarness(monkeypatch, m)
    read = m.AlohaMiniClient.get_observation
    get_key = m.KeyboardTeleop.get_action
    gaps, paused_progress, resumed_ticks, entered = [], [], [], []

    def interrupted(robot):
        worker = harness.worker
        if worker is not None and worker.live_started is not None:
            if worker.state == "active" and len(gaps) < 2 and harness.provider.elapsed_s >= 2 + 2 * len(gaps):
                gaps.append(harness.provider.elapsed_s)
                worker.request_pause("synthetic recorded feedback outage")
                harness.now += 1.1 if len(gaps) == 1 else 3.1
            if worker.state in {"paused", "resuming"}:
                paused_progress.append((len(gaps), harness.provider.elapsed_s))
        return read(robot)

    def keys(keyboard):
        if harness.provider is not None and harness.provider.elapsed_s >= 6:
            return {"q"}
        return get_key(keyboard)

    def enter(_prompt):
        if harness.worker is not None:
            entered.append(harness.now)
            harness.now += enter_delay
        return ""

    factory = m.ScriptedLeaderInput
    def monitored_provider(*args, **kwargs):
        provider = factory(*args, **kwargs)
        advance = provider.advance
        def measured_advance(*a, **kw):
            before = provider.elapsed_s
            advance(*a, **kw)
            resumed_ticks.append(provider.elapsed_s - before)
        provider.advance = measured_advance
        return provider

    monkeypatch.setattr(m, "ScriptedLeaderInput", monitored_provider)
    monkeypatch.setattr(m.AlohaMiniClient, "get_observation", interrupted)
    monkeypatch.setattr(m.KeyboardTeleop, "get_action", keys)
    assert harness.run(m, tmp_path, duration=80, input_fn=enter) == 0
    assert len(gaps) == 2 and len(entered) == 1
    for index in (1, 2):
        assert {progress for group, progress in paused_progress if group == index} == {gaps[index-1]}
    assert max(resumed_ticks) <= .100001 and 6 <= harness.provider.elapsed_s <= 6.1
    assert harness.events.count(("resume_ack",)) == 2
    output = capsys.readouterr().out
    assert output.count('"event": "am1_local_resume_input_requested"') == 1
    assert output.count('"event": "am1_local_resume_input_received"') == 1
    assert '"stop_reason": "manual_q"' in output and '"profile_complete": false' in output


@pytest.mark.parametrize("pause", [False, True])
def test_actual_scripted_entrypoint_sync_live_completion_and_cleanup(monkeypatch, tmp_path, capsys, pause):
    m = module()
    harness = LocalHarness(monkeypatch, m, pause=pause)
    assert harness.run(m, tmp_path) == 0
    assert harness.provider.complete
    actions = [e[1] for e in harness.events if e[0] in {"worker_action", "outer_action"}]
    assert all(all(action[k] == 0 for k in m.make_zero_action()) for action in actions)
    assert any(a.get(m.AM1_ARM_POSITION_KEYS[0], 12) > 13 for a in actions)
    startup = [e[1] for e in harness.events[:harness.events.index(("admitted",))] if e[0] == "outer_action"]
    assert all(a[k] == harness.initial[k] for a in startup for k in harness.initial if k in a)
    assert harness.events.index(("private_socket_close_and_join",)) < harness.events.index(("disconnect",))
    assert harness.events[-3][0] == "outer_action" and harness.events[-3][1] == m.make_zero_action()
    output = capsys.readouterr().out
    assert '"stop_reason": "script_complete"' in output
    assert "SCRIPTED LEADER INPUT" in output and "TELEOPERATION ACTIVE" in output
    if pause:
        assert len(harness.pause_elapsed) > 3 and len(set(harness.pause_elapsed)) == 1
        assert ("resume_ack",) in harness.events


@pytest.mark.parametrize("kind", ["q", "duration", "fault", "cleanup"])
def test_actual_scripted_termination_reasons_and_cleanup(monkeypatch, tmp_path, capsys, kind):
    m = module()
    harness = LocalHarness(monkeypatch, m, quit_after=4 if kind == "q" else None,
                           fault_after=4 if kind == "fault" else None, cleanup_error=kind == "cleanup")
    if kind == "cleanup":
        with pytest.raises(OSError, match="fake disconnect failure"):
            harness.run(m, tmp_path)
    else:
        assert harness.run(m, tmp_path, duration=2 if kind == "duration" else 180) == (2 if kind == "fault" else 0)
    output = capsys.readouterr().out
    expected = {"q": "manual_q", "duration": "duration_expired", "fault": "fault", "cleanup": "fault"}[kind]
    assert f'"stop_reason": "{expected}"' in output
    assert ("private_socket_close_and_join",) in harness.events and harness.events[-1] == ("disconnect",)


def test_no_fresh_seed_means_no_arm_command_or_live_owner(monkeypatch, tmp_path, capsys):
    m = module()
    harness = LocalHarness(monkeypatch, m, seed_fault=1.5)
    assert harness.run(m, tmp_path) == 2
    assert harness.worker is None and harness.provider is None
    assert all(event[1] == m.make_zero_action() for event in harness.events if event[0] == "outer_action")
    assert "current follower request/reply" in capsys.readouterr().out


@pytest.mark.parametrize("kind", ["explicit_stop", "keyboard_interrupt", "primary_error"])
def test_scripted_cancellation_and_primary_error_are_not_completion(monkeypatch, tmp_path, capsys, kind):
    m = module()
    harness = LocalHarness(monkeypatch, m, cleanup_error=kind == "primary_error")
    original_advance = m.ScriptedLeaderInput  # Factory wraps the real provider for this entrypoint.
    primary = RuntimeError("original scripted producer failure")

    def factory(*args, **kwargs):
        instance = original_advance(*args, **kwargs)
        advance = instance.advance
        def interrupted(now, feedback, sequence, **options):
            if instance.elapsed_s > 3:
                if kind == "keyboard_interrupt":
                    raise KeyboardInterrupt()
                if kind == "primary_error":
                    raise primary
                raise m.ExternalStopRequested("scripted test explicit Stop")
            advance(now, feedback, sequence, **options)
        instance.advance = interrupted
        return instance
    monkeypatch.setattr(m, "ScriptedLeaderInput", factory)
    if kind == "explicit_stop":
        assert harness.run(m, tmp_path) == 130
    elif kind == "keyboard_interrupt":
        with pytest.raises(KeyboardInterrupt):
            harness.run(m, tmp_path)
    else:
        with pytest.raises(RuntimeError) as failure:
            harness.run(m, tmp_path)
        assert failure.value is primary
        assert any("fake disconnect failure" in note for note in primary.__notes__)
    reason = "fault" if kind == "primary_error" else kind
    assert f'"stop_reason": "{reason}"' in capsys.readouterr().out
    assert harness.events.index(("private_socket_close_and_join",)) < harness.events.index(("disconnect",))


def test_sender_pause_between_feedback_and_publish_cannot_advance_script(monkeypatch, tmp_path):
    m = module()
    harness = LocalHarness(monkeypatch, m, quit_after=6)
    worker_class = m.AM1LiveActionSender
    publish = worker_class.publish
    injected = []

    def racing_publish(self, action):
        publish(self, action)
        if not injected and harness.provider.elapsed_s > 3:
            injected.append(harness.provider.elapsed_s)
            self.request_pause("fake concurrent sender freshness pause")
    monkeypatch.setattr(worker_class, "publish", racing_publish)
    factory = m.ScriptedLeaderInput

    def guarded_factory(*args, **kwargs):
        instance = factory(*args, **kwargs)
        advance = instance.advance
        def assert_admitted(now, feedback, sequence, **options):
            assert harness.worker.state == "active", "script advanced after concurrent pause"
            advance(now, feedback, sequence, **options)
        instance.advance = assert_admitted
        return instance
    monkeypatch.setattr(m, "ScriptedLeaderInput", guarded_factory)
    assert harness.run(m, tmp_path) == 0
    assert injected and ("resume_ack",) in harness.events


@pytest.mark.parametrize("losing_state", ["paused", "epoch", "stopped", "finished", "dead", "duration", "fault", "stale"])
def test_real_sender_atomic_script_commit_refuses_lost_admission(losing_state):
    m = module()
    provider = m.ScriptedLeaderInput(pose(m), joint_keys=m.AM1_ARM_POSITION_KEYS, fps=10, emit=lambda row: None)
    provider.elapsed_s = 87.95  # Synthetic final-tick boundary, not physical evidence.
    provider.admit(.95)
    sender = m.AM1LiveActionSender(
        object(), initial_action=m.make_am1_live_action(pose(m)), initial_observation_sequence=1,
        fps=10, duration_s=1 if losing_state == "duration" else 180, profile_cadence=False,
        recovery_enabled=True, monotonic=lambda: 1.0,
    )
    sender._thread = SimpleNamespace(is_alive=lambda: losing_state != "dead")
    sender._live_started_at = 0
    if losing_state == "paused": sender._recovery_state = "paused"
    if losing_state == "epoch": sender._recovery_epoch = 2
    if losing_state == "stopped": sender._stop_requested.set()
    if losing_state == "finished": sender._finished.set()
    if losing_state == "fault": sender._error = RuntimeError("real sender fault")
    sample = m.AM1LiveSample(2, 0 if losing_state == "stale" else 1.0, pose(m), pose(m), pose(m), 0.0)
    assert sender.advance_scripted_input(provider, sample, epoch=0, clock_active=True) is None
    assert provider.elapsed_s == 87.95 and not provider.complete


def test_real_sender_commits_completion_atomically_but_emits_outside_locks():
    m = module()
    provider = m.ScriptedLeaderInput(pose(m), joint_keys=m.AM1_ARM_POSITION_KEYS, fps=10, emit=lambda row: None)
    provider.elapsed_s = 87.95
    provider.admit(.9)
    sender = m.AM1LiveActionSender(
        object(), initial_action=m.make_am1_live_action(pose(m)), initial_observation_sequence=1,
        fps=10, duration_s=180, profile_cadence=False, recovery_enabled=True, monotonic=lambda: 1.0,
    )
    sender._thread = SimpleNamespace(is_alive=lambda: True)
    sender._live_started_at = 0
    emitted = []
    def emit(row):
        assert not sender._body_send_gate.locked() and not sender._state_lock.locked()
        emitted.append(row)
    provider._emit = emit
    sample = m.AM1LiveSample(2, 1.0, pose(m), pose(m), pose(m), 0.0)
    assert sender.advance_scripted_input(provider, sample, epoch=0, clock_active=True) == "script_complete"
    assert provider.complete and sender._stop_requested.is_set() and not emitted
    provider.flush_events()
    assert emitted


def test_atomic_script_tick_rechecks_request_plus_local_age_after_gate_wait():
    m = module()
    provider = m.ScriptedLeaderInput(pose(m), joint_keys=m.AM1_ARM_POSITION_KEYS, fps=10, emit=lambda row: None)
    provider.admit(.8)
    sender = m.AM1LiveActionSender(
        object(), initial_action=m.make_am1_live_action(pose(m)), initial_observation_sequence=1,
        fps=10, duration_s=180, profile_cadence=False, recovery_enabled=True, monotonic=lambda: 1.0,
    )
    sender._thread = SimpleNamespace(is_alive=lambda: True)
    sender._live_started_at = 0
    sample = SimpleNamespace(observed_at=.8, request_roundtrip_age_s=.9,
                             observation_sequence=2, follower_positions=pose(m))
    assert sender.advance_scripted_input(provider, sample, epoch=0, clock_active=True) is None
    assert provider.elapsed_s == 0
