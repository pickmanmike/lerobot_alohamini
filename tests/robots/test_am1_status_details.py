"""Shared essential status and authenticated typed detail through real existing wrappers."""

import asyncio
import base64
import copy
import hashlib
import json
import uuid

import pytest

from tests.robots.test_am1_elbow_recovery_integration import reporting_authority
from tests.robots.test_am1_session_reporting import exchange, physical_evidence, selected_native_record
from tools import am1_session_ipc as ipc
from tools.am1_session_core import SessionAuthority
from tools.am1_session_service import Gateway


def native_authority(*, extra_cycle=False):
    run = selected_native_record()
    if extra_cycle:
        # Labelled bulk reconstruction of the existing exact source receipt layout,
        # not a newly qualified physical run. Real provider schema2 fixtures follow.
        outcome = run["execution"]["positive_control"]
        for key in ("endpoint", "origin"):
            outcome["phase_receipts"]["cycle2_" + key] = copy.deepcopy(
                outcome["phase_receipts"]["cycle1_" + key]
            )
        run["recovery"] = {"reason": "required_observation", "qualified_samples": 3, "progress_s": 28.0}
    provider, evidence = physical_evidence(run)
    authority = reporting_authority(run, evidence)
    authority.executor = provider
    authority.events.extend(
        {
            "kind": kind,
            "at": 100.0,
            "run_id": run["run_id"],
            "service_incarnation": authority.service_incarnation,
        }
        for kind in ("required_hold", "observation_qualified", "recovered")
    )
    return authority


def resolve(authority, reference):
    raw = bytearray()
    while len(raw) < reference["bytes"]:
        reply = authority.snapshot_detail(
            {"op": "snapshot", "reference": reference, "offset": len(raw), "limit": 8192}
        )
        assert reply["status"] == "detail" and reply["reference"] == reference
        assert reply["offset"] == len(raw) and reply["total_bytes"] == reference["bytes"]
        block = base64.b64decode(reply["data"], validate=True)
        assert reply["length"] == len(block) and 0 < len(block) <= 8192
        assert len(ipc.encode({"id": str(uuid.uuid4()), "result": reply})) < ipc.RESPONSE_LIMIT
        raw.extend(block)
    assert hashlib.sha256(raw).hexdigest() == reference["sha256"]
    return ipc.strict_json(raw)


@pytest.mark.parametrize("bulk", [False, True])
def test_complete_native_bulk_never_obscures_shared_essential_status(bulk):
    a = native_authority(extra_cycle=bulk)
    original = copy.deepcopy(a.run)
    view = a.snapshot()
    assert (
        len(
            ipc.encode(
                {
                    "id": str(uuid.uuid4()),
                    "result": {"accepted": True, "status": "snapshot", "snapshot": view},
                }
            )
        )
        < 20000
    )
    assert view["run"]["run_id"] == original["run_id"]
    assert view["run"]["status"] == original["status"] and view["run"]["uncertain"] is False
    assert view["run"]["cleanup"] == original["cleanup"]
    assert view["run"]["execution"]["original_seed"] == original["execution"]["original_seed"]
    assert view["run"]["execution"]["cycles_completed"] == original["execution"]["cycles_completed"]
    assert resolve(a, view["run"]["details_reference"]) == original
    assert a.run == original


@pytest.mark.parametrize(
    "bad",
    [
        "extra",
        "run",
        "operation",
        "host",
        "service",
        "field",
        "sha",
        "bytes",
        "negative",
        "offset_bool",
        "length_bool",
        "too_large",
    ],
)
def test_detail_read_fails_closed_for_foreign_stale_or_unbounded_reference(bad):
    a = native_authority()
    view = a.snapshot()
    command = {
        "op": "snapshot",
        "reference": copy.deepcopy(view["run"]["details_reference"]),
        "offset": 0,
        "limit": 8192,
    }
    if bad == "extra":
        command["unknown"] = True
    elif bad in {"run", "operation", "host", "service"}:
        command["reference"][bad + ("_incarnation" if bad in {"host", "service"} else "_id")] = str(
            uuid.uuid4()
        )
    elif bad == "field":
        command["reference"]["field"] = "../../auth"
    elif bad == "sha":
        command["reference"]["sha256"] = "0" * 64
    elif bad == "bytes":
        command["reference"]["bytes"] += 1
    elif bad == "negative":
        command["offset"] = -1
    elif bad == "offset_bool":
        command["offset"] = False
    elif bad == "length_bool":
        command["limit"] = True
    else:
        command["limit"] = 8193
    original = copy.deepcopy(a.run)
    with pytest.raises(ValueError):
        a.snapshot_detail(command)
    assert a.run == original


def test_owner_detail_path_is_authenticated_bounded_read_and_does_not_tick():
    a = native_authority()
    reference = a.snapshot()["run"]["details_reference"]
    a.handle = lambda *_: pytest.fail("detail read entered mutating/ticking handle")
    command = {"op": "snapshot", "reference": reference, "offset": 0, "limit": 8192}
    reply, calls = asyncio.run(exchange(None, command, authority=a))
    assert reply["status"] == "detail" and calls == []
    with pytest.raises(asyncio.IncompleteReadError):
        asyncio.run(exchange(None, command, secret="wrong", authority=a))


def test_current_idle_is_not_retained_completion_or_historical_action():
    a = native_authority()
    evidence = a._evidence()
    evidence.update(
        feedback=False,
        required_observation=False,
        finite_completion=None,
        host_incarnation=None,
        accepted_host_action={},
        fault=None,
        lifecycle={"phase": "idle", "run_id": None, "uncertain": False},
    )
    a._evidence = lambda: copy.deepcopy(evidence)
    view = a.snapshot()
    assert view["evidence"]["lifecycle"]["phase"] == "idle"
    assert view["evidence"]["finite_completion"] is None
    assert view["evidence"]["accepted_host_action"] == {}
    assert view["run"]["status"] == "stopped"


def test_terminal_archive_is_full_typed_private_and_survives_explicit_next_start_and_restart(tmp_path):
    from examples.alohamini.am1_session_contract import LEFT_ELBOW_COARSE_BENCH_V1
    from tests.robots.test_am1_selected_experiment_authority import build

    _, executor, a, request = build(tmp_path / "owner")
    run = selected_native_record()
    try:
        a.run = copy.deepcopy(run)
        a._save()
        reference = a._status_reference("run", run)
        request["selected_experiment"] = LEFT_ELBOW_COARSE_BENCH_V1.payload
        admitted = a.handle(
            request, "owner"
        )  # A real ordinary new Start, not manual current-row replacement.
        assert admitted["accepted"] and admitted["run_id"] != run["run_id"] and executor.started == 1
        a._status_details.clear()  # Prior terminal evidence must be durable, not only RAM.
        assert resolve(a, reference) == run
    finally:
        a.close()
    b = SessionAuthority(tmp_path / "owner", clock=lambda: 101.0)
    try:
        assert b.run["run_id"] == admitted["run_id"] and b.run["status"] == "interrupted"
        assert resolve(b, reference) == run
        files = list((b.directory / "run-results").rglob("*.json"))
        assert any(ipc.strict_json(path.read_bytes()) == run for path in files)
    finally:
        b.close()


@pytest.mark.parametrize("change", ["content", "host", "symlink"])
def test_archived_detail_rejects_modified_or_redirected_original(tmp_path, change):
    a = SessionAuthority(tmp_path / "owner", clock=lambda: 100.0)
    run = selected_native_record()
    try:
        a.run = run
        a._save()
        reference = a._status_reference("run", run)
        a._status_details.clear()
        path = a.directory / "run-results" / run["run_id"] / (reference["sha256"] + ".json")
        if change == "host":
            reference["host_incarnation"] = str(uuid.uuid4())
        elif change == "content":
            path.write_bytes(path.read_bytes() + b" ")
        else:
            original = path.with_suffix(".original")
            path.rename(original)
            try:
                path.symlink_to(original)
            except OSError:
                pytest.skip("symlinks unavailable in this Windows test account")
        with pytest.raises(ValueError):
            resolve(a, reference)
    finally:
        a.close()


def test_zero_live_original_host_fallback_is_bound_and_detail_cache_remains_bounded():
    a = native_authority()
    original = copy.deepcopy(a.run)
    a.run["backend_incarnation"] = None
    a.run["physical_result"]["backend_incarnation"] = original["backend_incarnation"]
    view = a.snapshot()
    ref = view["run"]["details_reference"]
    assert ref["host_incarnation"] == original["backend_incarnation"]
    assert resolve(a, ref) == a.run
    for seq in range(70):
        a.run["progress_s"] = seq / 10
        a.snapshot()
    assert len(a._status_details) <= 64 and sum(map(len, a._status_details.values())) <= 8 * 1024**2
    with pytest.raises(ValueError):
        resolve(a, ref)


def test_existing_http_state_detail_read_retains_auth_route_and_typed_bytes():
    a = native_authority()
    reference = a.snapshot()["run"]["details_reference"]
    g = Gateway.__new__(Gateway)
    commands = []

    async def call(command, device):
        commands.append((command, device))
        return a.snapshot_detail(command)

    g.call = call
    request = type(
        "Request", (), {"query": {"reference": json.dumps(reference), "offset": "0", "limit": "8192"}}
    )()
    response = asyncio.run(g.state(request))
    reply = ipc.strict_json(response.body)
    assert reply["status"] == "detail" and response.headers["Cache-Control"] == "no-store"
    assert len(response.body) < ipc.RESPONSE_LIMIT
    assert commands[0][0]["op"] == "snapshot"
