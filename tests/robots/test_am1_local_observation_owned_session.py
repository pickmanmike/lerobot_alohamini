"""A physical required camera belongs to its current finite owned session."""

import json
from uuid import uuid4

import pytest

from tests.robots.test_am1_local_observation import Clock, headers, jpeg, source
from tools.am1_local_observation import LocalObservationMailbox, LocalReceiver


def publish_frames(box, receiver, clock, owner, capture, owned_session):
    for sequence in range(1, 4):
        clock.advance(0.11)
        response_headers = headers(clock, sequence, owner, capture)
        if owned_session is not None:
            response_headers["X-Camera-Owned-Session"] = owned_session
        assert receiver.accept(jpeg(), response_headers)
        box.publish(receiver.evidence())


@pytest.mark.parametrize("owned_session", [None, "", "bad-uuid", str(uuid4())])
def test_physical_mailbox_cannot_use_missing_or_old_camera_lease(tmp_path, owned_session):
    clock = Clock()
    box = LocalObservationMailbox(source(), clock=clock, require_owned_session=True)
    box.begin(str(uuid4()), 420)
    receiver = LocalReceiver(tmp_path, box.request, clock=clock)
    publish_frames(box, receiver, clock, str(uuid4()), str(uuid4()), owned_session)
    assert receiver.evidence()["qualified"]
    assert not box.evidence()["qualified"]
    assert "owned session" in box.evidence()["reason"]


def test_current_owned_camera_session_qualifies_with_original_arrival_and_deadline(tmp_path):
    clock = Clock()
    box = LocalObservationMailbox(source(), clock=clock, require_owned_session=True)
    run_id = str(uuid4())
    box.begin(run_id, 420)
    receiver = LocalReceiver(tmp_path, box.request, clock=clock)
    publish_frames(box, receiver, clock, str(uuid4()), str(uuid4()), run_id)
    evidence = box.evidence()
    assert evidence["qualified"]
    assert evidence["owned_session"] == run_id
    assert receiver.evidence()["owned_session"] == run_id
    assert json.loads((tmp_path / "latest.json").read_text())["owned_session"] == run_id
    original_arrival = evidence["arrived_at"]
    clock.advance(0.501)
    assert not box.evidence()["qualified"]
    assert box.evidence()["arrived_at"] == original_arrival


def test_new_run_does_not_rebind_an_old_fresh_viewer_lease(tmp_path):
    clock = Clock()
    box = LocalObservationMailbox(source(), clock=clock, require_owned_session=True)
    previous_run = str(uuid4())
    owner, capture = str(uuid4()), str(uuid4())
    box.begin(previous_run, 420)
    receiver = LocalReceiver(tmp_path / "first", box.request, clock=clock)
    publish_frames(box, receiver, clock, owner, capture, previous_run)
    assert box.evidence()["qualified"]
    box.stop()
    current_run = str(uuid4())
    box.begin(current_run, 420)
    current_receiver = LocalReceiver(tmp_path / "second", box.request, clock=clock)
    # The worker gets the new request binding but the existing viewer still has its old lease.
    publish_frames(box, current_receiver, clock, owner, capture, previous_run)
    assert not box.evidence()["qualified"]
    assert box.request["run_id"] == current_run


def test_current_worker_foreign_owned_session_unqualifies_without_weakening_source_binding(tmp_path):
    clock = Clock()
    box = LocalObservationMailbox(source(), clock=clock, require_owned_session=True)
    run_id = str(uuid4())
    box.begin(run_id, 420)
    receiver = LocalReceiver(tmp_path, box.request, clock=clock)
    publish_frames(box, receiver, clock, str(uuid4()), str(uuid4()), run_id)
    current = receiver.evidence()
    assert box.evidence()["qualified"]
    box.publish(dict(current, owned_session=str(uuid4()), sequence=4))
    assert not box.evidence()["qualified"]
    box.publish(dict(current, run_id=str(uuid4()), owned_session=run_id))
    assert not box.evidence()["qualified"]


def test_manual_and_simulated_local_policy_still_accepts_existing_viewer_without_owned_header(tmp_path):
    clock = Clock()
    box = LocalObservationMailbox(source(), clock=clock)
    box.begin(str(uuid4()), 420)
    receiver = LocalReceiver(tmp_path, box.request, clock=clock)
    publish_frames(box, receiver, clock, str(uuid4()), str(uuid4()), None)
    assert box.evidence()["qualified"]
    assert box.evidence()["owned_session"] is None


def test_relabeling_an_identical_cached_snapshot_does_not_retain_old_qualification(tmp_path):
    clock = Clock()
    box = LocalObservationMailbox(source(), clock=clock, require_owned_session=True)
    run_id = str(uuid4())
    box.begin(run_id, 420)
    receiver = LocalReceiver(tmp_path, box.request, clock=clock)
    owner, capture = str(uuid4()), str(uuid4())
    publish_frames(box, receiver, clock, owner, capture, run_id)
    response_headers = headers(clock, 3, owner, capture)
    response_headers["X-Camera-Owned-Session"] = str(uuid4())
    assert not receiver.accept(jpeg(), response_headers)
    box.publish(receiver.evidence())
    assert not box.evidence()["qualified"]


def test_bad_jpeg_in_current_owned_source_retains_original_decode_failure_reason(tmp_path):
    clock = Clock()
    box = LocalObservationMailbox(source(), clock=clock, require_owned_session=True)
    run_id = str(uuid4())
    box.begin(run_id, 420)
    receiver = LocalReceiver(tmp_path, box.request, clock=clock)
    owner, capture = str(uuid4()), str(uuid4())
    publish_frames(box, receiver, clock, owner, capture, run_id)
    clock.advance(0.1)
    response_headers = headers(clock, 4, owner, capture)
    response_headers["X-Camera-Owned-Session"] = run_id
    assert not receiver.accept(b"bad jpeg", response_headers)
    box.publish(receiver.evidence())
    assert not box.evidence()["qualified"]
    assert "identify image" in box.evidence()["reason"]
    assert receiver.evidence()["owned_session"] == run_id


def test_owned_requirement_is_fixed_at_begin_and_cannot_downgrade_active_run(tmp_path):
    clock = Clock()
    box = LocalObservationMailbox(source(), clock=clock, require_owned_session=True)
    box.begin(str(uuid4()), 420)
    box.require_owned_session = False
    receiver = LocalReceiver(tmp_path, box.request, clock=clock)
    publish_frames(box, receiver, clock, str(uuid4()), str(uuid4()), None)
    assert box.request["require_owned_session"] is True
    assert not box.evidence()["qualified"]


def test_receiver_normalizes_owned_uuid_before_propagating_current_evidence(tmp_path):
    clock = Clock()
    box = LocalObservationMailbox(source(), clock=clock, require_owned_session=True)
    run_id = str(uuid4())
    box.begin(run_id, 420)
    receiver = LocalReceiver(tmp_path, box.request, clock=clock)
    publish_frames(box, receiver, clock, str(uuid4()), str(uuid4()), run_id.upper())
    assert receiver.evidence()["owned_session"] == run_id
    assert box.evidence()["qualified"]
