"""Real owner feedback caches and signed metadata without device access."""

import copy
import json
from types import SimpleNamespace

import pytest

from lerobot.robots.alohamini import alohamini_host as host
from tests.robots.test_alohamini_lift_motor_feedback import status
from tests.robots.test_am1_selected_joint_evidence import (
    attach_port,
    binding,
    command,
    context,
    group_payload,
    observe,
    robot,
    sampler,
)

PROFILE = "LeftElbowResponseCharacterize"


def signed_context(progress=0.0, phase="baseline", cycle=0, target=0.0, endpoint=-3.0, **changes):
    return context(
        profile=PROFILE,
        progress_s=progress,
        phase=phase,
        cycle=cycle,
        requested_target=target,
        endpoint_target=endpoint,
        sample_key=(
            "baseline"
            if phase == "baseline"
            else f"cycle{cycle}_{phase}"
            if phase in {"endpoint", "origin"}
            else None
        ),
        **changes,
    )


def publish(evidence, obj, control, observation):
    method = getattr(evidence, "publish", None)
    assert callable(method), "Ordinary feedback lacks the same-ACK selected cache publication"
    method(obj, control, observation)
    return observation[host.AM1_LOCAL_FEEDBACK_KEY].get("selected_joint")


def test_same_ack_feedback_uses_original_caches_and_retained_receipt_without_read(monkeypatch):
    obj, reads, _ = robot(monkeypatch)
    evidence, rows = sampler()
    identity = binding()
    control = host.AM1LocalControl(**identity, selected_evidence=evidence)
    port = attach_port(obj, [status(group_payload(), motor_id=3)])
    control.apply(obj, command(identity))
    first = observe(obj, control)
    before = len(reads)
    note = publish(evidence, obj, control, first)
    assert not port.writes and len(reads) == before and not rows
    assert note["sparse_receipts"] == {}
    for key in (
        "run_id",
        "host_incarnation",
        "epoch",
        "intent_revision",
        "observation_id",
        "acquired_at_monotonic_s",
    ):
        assert note[key] == first[host.AM1_LOCAL_FEEDBACK_KEY][key]
    assert note["action_sequence"] == first[host.AM1_LOCAL_FEEDBACK_KEY]["accepted_action_sequence"]
    assert note["position"] == obj._am1_selected_joint_position
    assert note["current"] == obj._am1_selected_joint_current
    evidence.report(obj, control, first)
    original = copy.deepcopy(next(row for row in rows if row["kind"] == "sparse_readback"))
    second = observe(obj, control)
    note = publish(evidence, obj, control, second)
    assert len(port.writes) == 1 and note["sparse_receipts"]["baseline"] == original
    # Client mutation cannot rewrite the actual retained packet or refresh its clock.
    note["sparse_receipts"]["baseline"]["trace"]["response_bytes"][0] = 0
    note["position"]["raw"] = 999
    assert publish(evidence, obj, control, observe(obj, control))["sparse_receipts"]["baseline"] == original
    assert obj._am1_selected_joint_position["raw"] == 2000


@pytest.mark.parametrize(
    "change", ["run_id", "host_incarnation", "epoch", "intent_revision", "accepted_action_sequence"]
)
def test_foreign_or_unmatched_ack_never_publishes_selected_proof(monkeypatch, change):
    obj, _, _ = robot(monkeypatch)
    evidence, _ = sampler()
    identity = binding()
    control = host.AM1LocalControl(**identity, selected_evidence=evidence)
    control.apply(obj, command(identity))
    observation = observe(obj, control)
    observation[host.AM1_LOCAL_FEEDBACK_KEY][change] = (
        "foreign" if change.endswith("id") or change == "host_incarnation" else 999
    )
    assert publish(evidence, obj, control, observation) is None


def test_missing_and_stale_caches_keep_original_absence_and_timestamps(monkeypatch):
    obj, _, _ = robot(monkeypatch)
    evidence, _ = sampler()
    identity = binding()
    control = host.AM1LocalControl(**identity, selected_evidence=evidence)
    control.apply(obj, command(identity))
    observation = observe(obj, control)
    del obj._am1_selected_joint_current
    old_position = copy.deepcopy(obj._am1_selected_joint_position)
    evidence.clock = lambda: old_position["read_completed_at_monotonic_s"] + 2
    note = publish(evidence, obj, control, observation)
    assert note["current"] is None and note["position"] == old_position
    assert note["position"]["read_completed_at_monotonic_s"] != evidence.clock()


def test_signed_context_nine_sparse_phases_preserve_frozen_reference_and_existing_owner(monkeypatch):
    obj, _, _ = robot(monkeypatch)
    evidence, rows = sampler()
    identity = binding()
    control = host.AM1LocalControl(**identity, selected_evidence=evidence)
    goals = (2000, 1970, 2000, 1970, 2000, 2100, 2000, 2100, 2000)
    port = attach_port(obj, [status(group_payload(goal=goal), motor_id=3) for goal in goals])
    phases = (
        (0, "baseline", 0, 0, -3),
        (5, "endpoint", 1, -3, -3),
        (8.8, "origin", 1, 0, -3),
        (12, "endpoint", 2, -3, -3),
        (15.8, "origin", 2, 0, -3),
        (16, "outbound", 3, 0, 10),
        (26, "endpoint", 3, 10, 10),
        (36.8, "origin", 3, 0, 10),
        (47, "endpoint", 4, 10, 10),
        (57.8, "origin", 4, 0, 10),
        (60, "final", 4, 0, 10),
    )
    for sequence, args in enumerate(phases, 1):
        metadata = signed_context(*args)
        control.apply(obj, command(identity, metadata, sequence=sequence))
        observation = observe(obj, control)
        publish(evidence, obj, control, observation)
        evidence.report(obj, control, observation)
    note = publish(evidence, obj, control, observe(obj, control))
    assert len(port.writes) == 9
    assert set(note["sparse_receipts"]) == {
        "baseline",
        *(f"cycle{cycle}_{phase}" for cycle in range(1, 5) for phase in ("endpoint", "origin")),
    }
    assert all(row["context"]["reference"] == 0 for row in note["sparse_receipts"].values())
    assert all(row["goal_matches_expected_raw"] for row in note["sparse_receipts"].values())
    assert note["context"] == signed_context(60, "final", 4, 0, 10)
    assert len([row for row in rows if row["kind"] == "sparse_readback"]) == 9
    assert (
        len(host.build_observation_multipart({host.AM1_LOCAL_FEEDBACK_KEY: {"selected_joint": note}}, [])[0])
        < 65536
    )


@pytest.mark.parametrize(
    "metadata",
    [
        signed_context(3, "outbound", 1, -1, -3),
        signed_context(18, "final", 2, 0, -3),
        signed_context(20, "outbound", 3, 4, 10),
        signed_context(60, "final", 4, 0, 10),
    ],
)
def test_signed_declared_phase_and_bound_are_admitted(monkeypatch, metadata):
    obj, _, _ = robot(monkeypatch)
    evidence, _ = sampler()
    identity = binding()
    control = host.AM1LocalControl(**identity, selected_evidence=evidence)
    assert control.apply(obj, command(identity, metadata))


@pytest.mark.parametrize(
    "metadata",
    [
        signed_context(3, "outbound", 1, 1, 3),
        signed_context(3, "endpoint", 1, -3, -3),
        signed_context(15, "outbound", 3, 0, 10),
        signed_context(19, "final", 2, 0, -3),
        signed_context(60, "final", 2, 0, 10),
        signed_context(0, "baseline", 0, 0, -3, reference=95),
        {**signed_context(), "cycle": True},
    ],
)
def test_malformed_signed_policy_never_reaches_write(monkeypatch, metadata):
    obj, _, writes = robot(monkeypatch)
    evidence, _ = sampler()
    identity = binding()
    control = host.AM1LocalControl(**identity, selected_evidence=evidence)
    with pytest.raises(RuntimeError):
        control.apply(obj, command(identity, metadata))
    assert not writes


def test_signed_expansion_cannot_reverse_or_change_reference(monkeypatch):
    obj, _, writes = robot(monkeypatch)
    evidence, _ = sampler()
    identity = binding()
    control = host.AM1LocalControl(**identity, selected_evidence=evidence)
    control.apply(obj, command(identity, signed_context()))
    control.apply(obj, command(identity, signed_context(16, "outbound", 3, 0, 10), sequence=2))
    before = copy.deepcopy(writes)
    for metadata in (
        signed_context(18, "final", 2, 0, -3),
        signed_context(20, "outbound", 3, 5, 11, reference=1),
    ):
        with pytest.raises(RuntimeError):
            control.apply(obj, command(identity, metadata, sequence=3))
        assert writes == before


def test_native_reply_contains_cache_before_diagnostic_io(monkeypatch):
    from lerobot.robots.alohamini import alohamini as robot_module

    obj, _, _ = robot(monkeypatch)
    identity, events, replies = binding(), [], []
    now = SimpleNamespace(value=10.0)
    clock = SimpleNamespace(
        monotonic=lambda: now.value,
        perf_counter=lambda: now.value,
        time_ns=lambda: int(now.value * 1e9),
        sleep=lambda duration: setattr(now, "value", now.value + duration),
    )
    port = attach_port(obj, [status(group_payload(), motor_id=3)])
    real_write = port.writePort

    def diagnostic_write(packet):
        events.append("diagnostic")
        now.value += 0.04
        return real_write(packet)

    port.writePort = diagnostic_write
    cls = host.AM1SelectedJointEvidence
    monkeypatch.setattr(
        host, "AM1SelectedJointEvidence", lambda **kw: cls(**kw, clock=clock.monotonic, emit=lambda _: None)
    )
    monkeypatch.setenv("AM1_SELECTED_JOINT_EVIDENCE", "1")
    monkeypatch.setattr(host, "time", clock)
    monkeypatch.setattr(robot_module, "time", clock)
    monkeypatch.setattr(host, "AlohaMini", lambda _: obj)
    monkeypatch.setattr(host, "connect_robot", lambda *a, **kw: None)
    obj.disconnect = lambda **kw: None

    class Socket:
        def recv_string(self, flags):
            return json.dumps(command(identity))

        def recv_multipart(self, flags):
            return [b"client", b"original-token"]

        def send_multipart(self, parts, flags):
            events.append("response")
            replies.append(json.loads(parts[2]))

    class Owner:
        watchdog_timeout_ms, connection_time_s, max_loop_freq_hz = 1000, 0.03, 30

        def __init__(self, _):
            self.zmq_cmd_socket = self.zmq_observation_socket = Socket()

        def disconnect(self):
            pass

    monkeypatch.setattr(host, "AlohaMiniHost", Owner)
    args = host.make_parser().parse_args(["--no_cameras", "--skip_lift_home"])
    args.am1_run_id, args.am1_host_incarnation = identity["run_id"], identity["host_incarnation"]
    host._run_host(args)
    assert events == ["response", "diagnostic"]
    note = replies[0][host.AM1_LOCAL_FEEDBACK_KEY].get("selected_joint")
    assert isinstance(note, dict), "Ordinary response must carry actual selected cache before sparse read"
    assert note["sparse_receipts"] == {} and note["position"]["raw"] == 2000
    assert note["current"]["ma"] == 13
    assert now.value == pytest.approx(10.04)


@pytest.mark.parametrize(
    "key,value",
    [
        ("version", True),
        ("state", "paused"),
        ("epoch", False),
        ("intent_revision", False),
        ("accepted_action_sequence", True),
        ("observation_id", True),
        ("acquired_at_monotonic_s", float("nan")),
    ],
)
def test_malformed_native_ack_cannot_be_relabelled_as_actual_selected_evidence(monkeypatch, key, value):
    obj, _, _ = robot(monkeypatch)
    evidence, _ = sampler()
    identity = binding()
    control = host.AM1LocalControl(**identity, selected_evidence=evidence)
    control.apply(obj, command(identity))
    observation = observe(obj, control)
    observation[host.AM1_LOCAL_FEEDBACK_KEY][key] = value
    observation[host.AM1_LOCAL_FEEDBACK_KEY]["selected_joint"] = {"stale": True}
    assert publish(evidence, obj, control, observation) is None


@pytest.mark.parametrize("fault", ["status", "missing_reply"])
def test_original_fault_or_unavailable_row_is_retained_without_new_read_or_qualification(monkeypatch, fault):
    obj, _, _ = robot(monkeypatch)
    evidence, rows = sampler()
    identity = binding()
    control = host.AM1LocalControl(**identity, selected_evidence=evidence)
    port = attach_port(
        obj, [] if fault == "missing_reply" else [status(group_payload(servo_status=8), motor_id=3)]
    )
    control.apply(obj, command(identity))
    observation = observe(obj, control)
    if fault == "status":
        with pytest.raises(RuntimeError, match="status/torque/mode"):
            evidence.report(obj, control, observation)
    else:
        evidence.report(obj, control, observation)
    original = next(row for row in rows if row["kind"] == "sparse_readback")
    note = publish(evidence, obj, control, observation)
    assert note["sparse_receipts"]["baseline"] == original
    assert original["diagnostic_available"] is False and len(port.writes) == 1
    if fault == "status":
        assert original["decoded"]["status"] == 8
    else:
        assert original["decoded"] is None and original["trace"]["response_bytes"] == []


def test_pause_cannot_publish_the_previous_active_context(monkeypatch):
    obj, _, _ = robot(monkeypatch)
    evidence, _ = sampler()
    identity = binding()
    control = host.AM1LocalControl(**identity, selected_evidence=evidence)
    control.apply(obj, command(identity))
    obj.stop_motion, obj.hold_follower_arms = lambda: None, lambda: None
    paused = command(identity, sequence=2, mode="pause", epoch=1)
    paused.pop("_am1_selected_joint_evidence")
    control.apply(obj, paused)
    assert publish(evidence, obj, control, observe(obj, control)) is None


def test_signed_short_final_branch_cannot_later_expand(monkeypatch):
    obj, _, writes = robot(monkeypatch)
    evidence, _ = sampler()
    identity = binding()
    control = host.AM1LocalControl(**identity, selected_evidence=evidence)
    control.apply(obj, command(identity, signed_context(16, "final", 2, 0, -3)))
    before = copy.deepcopy(writes)
    with pytest.raises(RuntimeError):
        control.apply(obj, command(identity, signed_context(18, "outbound", 3, 2, 10), sequence=2))
    assert writes == before


def test_signed_early_origin_skips_read_and_late_origin_reads_exactly_once(monkeypatch):
    obj, _, _ = robot(monkeypatch)
    evidence, rows = sampler()
    identity = binding()
    control = host.AM1LocalControl(**identity, selected_evidence=evidence)
    port = attach_port(obj, [status(group_payload(goal=2000), motor_id=3)])
    early = dict(signed_context(8.5, "origin", 1, 0, -3), sample_key=None)
    control.apply(obj, command(identity, early))
    evidence.report(obj, control, observe(obj, control))
    assert not port.writes and evidence.sparse_receipts == {}
    for sequence, progress in ((2, 8.8), (3, 8.9)):
        control.apply(obj, command(identity, signed_context(progress, "origin", 1, 0, -3), sequence=sequence))
        evidence.report(obj, control, observe(obj, control))
    assert len(port.writes) == 1
    row = next(row for row in rows if row["kind"] == "sparse_readback")
    assert row["context"]["progress_s"] == 8.8
    assert row["sample_key"] == "cycle1_origin" and row["diagnostic_available"] is True


@pytest.mark.parametrize("profile,progress", [("LeftElbowCompare", 8.5), ("LeftElbowDwellCompare", 10.0)])
def test_original_selected_profiles_keep_first_origin_read(monkeypatch, profile, progress):
    obj, _, _ = robot(monkeypatch)
    evidence, _ = sampler()
    identity = binding()
    control = host.AM1LocalControl(**identity, selected_evidence=evidence)
    port = attach_port(obj, [status(group_payload(goal=2000), motor_id=3)])
    metadata = context(
        profile=profile,
        progress_s=progress,
        phase="origin",
        cycle=1,
        requested_target=0.0,
        sample_key="cycle1_origin",
    )
    control.apply(obj, command(identity, metadata))
    evidence.report(obj, control, observe(obj, control))
    assert len(port.writes) == 1
    assert evidence.sparse_receipts["cycle1_origin"]["context"] == metadata
