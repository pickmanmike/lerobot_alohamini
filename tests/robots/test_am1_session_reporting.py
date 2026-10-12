"""Real bounded IPC frames and gateway reporting without hardware owners."""

import asyncio
import copy
import hashlib
import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import am1_session_ipc as ipc
from tools.am1_session_service import Gateway


def oversized_result(accepted=True):
    run = {
        "run_id": str(uuid.uuid4()),
        "backend_incarnation": str(uuid.uuid4()),
        "status": "faulted",
        "recipe": "physical-left-elbow-positive-control",
        "progress_s": 28.0,
        "first_cause": "actual native fault",
        "cleanup": "physical_readback_verified",
        "uncertain": False,
        "execution": {"original_evidence": "x" * ipc.RESPONSE_LIMIT},
    }
    return {
        "accepted": accepted,
        "status": "started" if accepted else "refused",
        "effect_admitted": accepted,
        "run_id": run["run_id"],
        "snapshot": {"run": run},
    }


async def exchange(result, command=None, secret="correct", protective=False):
    calls = []
    authority = SimpleNamespace(
        handle=lambda c, d: (calls.append((c, d)) or copy.deepcopy(result)),
        snapshot=lambda: result["snapshot"],
    )
    owner = ipc.OwnerServer.__new__(ipc.OwnerServer)
    owner.secret, owner.authority = "correct", authority
    owner.active = {False: 0, True: 0}
    owner.slots = {False: asyncio.Semaphore(6), True: asyncio.Semaphore(2)}
    server = await asyncio.start_server(
        lambda r, w: owner.serve(r, w, protective), "127.0.0.1", 0, limit=ipc.CONTROL_LIMIT + 4
    )
    reader, writer = await asyncio.open_connection("127.0.0.1", server.sockets[0].getsockname()[1])
    correlation = str(uuid.uuid4())
    try:
        await ipc.write_frame(
            writer,
            {
                "secret": secret,
                "id": correlation,
                "device": "authorized-test-device",
                "command": command or {"op": "start", "operation_id": str(uuid.uuid4())},
            },
            ipc.CONTROL_LIMIT,
        )
        reply = ipc.strict_json(await ipc.read_frame(reader, ipc.RESPONSE_LIMIT))
        assert reply["id"] == correlation
        return reply["result"], calls
    finally:
        writer.close()
        await writer.wait_closed()
        server.close()
        await server.wait_closed()


@pytest.mark.parametrize("accepted", [True, False])
def test_post_operation_overflow_retains_actual_outcome_and_does_not_redispatch(accepted):
    result = oversized_result(accepted)
    original = copy.deepcopy(result)
    reply, calls = asyncio.run(exchange(result))
    assert len(calls) == 1
    assert reply["status"] == "reporting_error" and reply["code"] == "response_limit"
    assert reply["accepted"] is accepted and reply["effect_admitted"] is accepted
    assert reply["operation_result"]["status"] == result["status"]
    assert reply["run_id"] == result["run_id"]
    assert reply["run_summary"]["first_cause"] == "actual native fault"
    assert reply["run_summary"]["cleanup"] == "physical_readback_verified"
    assert reply["run_summary"]["uncertain"] is False
    assert "snapshot" not in reply and len(ipc.encode(reply)) < ipc.CONTROL_LIMIT
    assert result == original


def test_snapshot_overflow_has_concrete_correlated_error_and_run_identity():
    result = oversized_result()
    reply, calls = asyncio.run(exchange(result, {"op": "snapshot"}))
    assert calls == []
    assert reply["operation_result"] == {"accepted": True, "status": "snapshot"}
    assert reply["run_summary"]["run_id"] == result["run_id"]
    assert reply["response_bytes"] > ipc.RESPONSE_LIMIT
    assert reply["response_limit"] == ipc.RESPONSE_LIMIT


def test_unauthenticated_overflow_never_leaks_known_run():
    with pytest.raises(asyncio.IncompleteReadError):
        asyncio.run(exchange(oversized_result(), secret="wrong"))


def test_gateway_snapshot_surfaces_reporting_error_instead_of_missing_snapshot():
    async def scenario():
        gateway = Gateway.__new__(Gateway)
        error = {"status": "reporting_error", "code": "response_limit", "reason": "too large"}

        async def call(*args):
            return error

        gateway.call = call
        with pytest.raises(ipc.ReportingError) as caught:
            await gateway.current()
        assert caught.value.result == error

    asyncio.run(scenario())


@pytest.mark.parametrize("recipe", ["fake-arm-smoke-repeat", "physical-left-elbow-recovery-i0"])
def test_confirmation_uuid_is_forbidden_for_ordinary_recipe(recipe):
    async def scenario():
        gateway = Gateway.__new__(Gateway)
        gateway.auth = SimpleNamespace(capable=lambda d: True)
        calls = []

        async def call(*args):
            calls.append(args)
            return {"accepted": True}

        gateway.call = call
        with pytest.raises(ValueError, match="confirmation"):
            await gateway.dispatch(
                {"op": "start", "recipe": recipe, "confirmation_of": str(uuid.uuid4())},
                {"control": True, "id": "controller"},
            )
        assert calls == []

    asyncio.run(scenario())


def test_only_canonical_positive_confirmation_uuid_passes_to_owner():
    async def scenario():
        gateway = Gateway.__new__(Gateway)
        gateway.auth = SimpleNamespace(capable=lambda d: True)
        calls = []

        async def call(c, d):
            calls.append(copy.deepcopy(c))
            return {"accepted": True}

        gateway.call = call
        command = {
            "op": "start",
            "recipe": "physical-left-elbow-positive-control",
            "confirmation_of": str(uuid.uuid4()),
        }
        await gateway.dispatch(command, {"control": True, "id": "controller"})
        assert calls == [command]
        with pytest.raises(ValueError, match="confirmation"):
            await gateway.dispatch(dict(command, confirmation_of="bad"), {"control": True, "id": "c"})

    asyncio.run(scenario())


def test_frame_caps_and_nonfinite_refusal_remain_exact():
    assert len(ipc.encode({"x": "a" * (ipc.CONTROL_LIMIT - 8)}, ipc.CONTROL_LIMIT)) == ipc.CONTROL_LIMIT
    with pytest.raises(ValueError, match="message too large"):
        ipc.encode({"x": "a" * (ipc.CONTROL_LIMIT - 7)}, ipc.CONTROL_LIMIT)
    with pytest.raises(ValueError):
        ipc.encode({"x": float("nan")})
    assert json.loads(ipc.encode({"x": 1.0}))["x"] == 1.0


def test_gateway_publish_reports_concrete_overflow_and_requires_fresh_ack():
    async def scenario():
        gateway = Gateway.__new__(Gateway)
        values = []
        delivered = asyncio.Event()
        peer = SimpleNamespace(ack=True, revision="old")

        def put(value, snapshot):
            values.append(value)
            delivered.set()

        peer.put = put
        gateway.peers = [peer]
        gateway.revision, gateway.incarnation = 0, "gateway"
        error = {"status": "reporting_error", "code": "response_limit", "reason": "full frame too large"}

        async def current():
            raise ipc.ReportingError(error)

        gateway.current = current
        task = asyncio.create_task(gateway.publish())
        try:
            await asyncio.wait_for(delivered.wait(), 0.5)
            assert values == [dict(error, kind="unavailable")]
            assert peer.ack is False and peer.revision != "old"
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_gateway_http_reports_known_presentation_error_not_command_refusal():
    from aiohttp.test_utils import make_mocked_request

    async def scenario():
        gateway = Gateway.__new__(Gateway)
        gateway.origin = "https://test.invalid"
        gateway.rates, gateway.http_active = {}, {"normal": 0, "protective": 0}
        gateway.auth = SimpleNamespace(resolve=lambda t: {"id": "device", "csrf": "csrf"})
        request = make_mocked_request(
            "GET",
            "/api/state",
            headers={"Host": "test.invalid", "Cookie": "__Host-am1-device=token"},
            sslcontext=object(),
        )
        error = {
            "accepted": True,
            "status": "reporting_error",
            "code": "response_limit",
            "reason": "full frame too large",
        }

        async def handler(r):
            raise ipc.ReportingError(error)

        response = await gateway.boundary(request, handler)
        assert response.status == 503 and json.loads(response.text) == error
        assert gateway.http_active["normal"] == 0

    asyncio.run(scenario())


def physical_evidence(run):
    """Actual adapter evidence schema, using only injected cached records/no worker."""
    from examples.alohamini.am1_session_contract import RECIPES
    from tests.robots.test_am1_physical_executor import ControlledHost, executor
    from tools.am1_local_observation import LocalObservationMailbox

    provider = executor(ControlledHost())
    provider.clock = lambda: 100.0
    provider.config = copy.deepcopy(run["backend_contract"])
    provider.run_id, provider.host_incarnation = run["run_id"], run["backend_incarnation"]
    provider.recipe = RECIPES[run["recipe"]]
    provider.recovery_reference = copy.deepcopy(run.get("retained_return_reference"))
    provider.recovery_prerequisite = copy.deepcopy(run.get("recovery_prerequisite"))
    provider._finite_completion = copy.deepcopy(run.get("completion_receipt"))
    provider._lifecycle = copy.deepcopy(run["physical_result"])
    provider._fault = run.get("first_cause")
    receipt = run.get("completion_receipt") or {}
    ack = copy.deepcopy(receipt.get("accepted_host_action", {}))
    provider._epoch, provider._epoch_mode = ack.get("epoch", 0), "active"
    provider.revision = ack.get("intent_revision", 0)
    provider.sample = {
        "at": receipt.get("feedback_at_monotonic_s", 0.0),
        "sequence": receipt.get("feedback_sequence", run["execution"]["feedback_sequence"]),
        "positions": copy.deepcopy(receipt.get("measured_positions", run["execution"]["original_seed"])),
        "ack": ack,
    }
    # Full actual mailbox schema, with no new images or fabricated freshness at terminal.
    provider.observation = LocalObservationMailbox(provider.observation.source, clock=provider.clock)
    evidence = provider.evidence()
    assert provider.worker is None and provider._reconciliation_worker is None
    return provider, evidence


@pytest.mark.parametrize("name", ["f778", "79def"])
def test_saved_native_typed_terminal_record_full_snapshot_socket(name, record_property):
    from tests.robots.test_am1_elbow_recovery_integration import reporting_authority

    root = Path(
        os.environ.get(
            "AM1_REPORTING_FIXTURES", ".cache/am1-visible-control-04/reporting-tests/native-fixtures"
        )
    )
    raw = (root / (name + ".json")).read_bytes()  # Mandatory actual private fixtures; never silently skipped.
    native = json.loads(raw)
    run = native["run"] if name == "f778" else native
    expected = {
        "f778": "aa257b80a5d11a029f9873b228c812f625b81d324c6b7bf62fe7e5ece70d43c6",
        "79def": "eac17ce176890d5352a80953d64d59c3d83fbf74590f4dbeb9975dbe5934fd3a",
    }
    canonical = json.dumps(run, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    assert hashlib.sha256(canonical).hexdigest() == expected[name]
    provider, evidence = physical_evidence(run)
    authority = reporting_authority(run, evidence)
    authority.executor = provider
    original = copy.deepcopy(authority.run)
    view = authority.snapshot()
    result = {"accepted": True, "status": "snapshot", "snapshot": view}
    size = len(ipc.encode({"id": str(uuid.uuid4()), "result": result}, ipc.RESPONSE_LIMIT))
    record_property(name + "_full_wrapped_bytes", size)
    reply, calls = asyncio.run(exchange(result, {"op": "snapshot"}))
    assert reply == result and calls == []
    assert reply["snapshot"]["run"]["execution"]["recovery"]["target_attained"] is False
    assert (
        reply["snapshot"]["run"]["physical_result"]["readback"]["readback"]["elbow_integral"]["state"]
        == "restored"
    )
    assert authority.run == original and authority._evidence() == evidence


def positive_execution(*, complete, moves, run_id, host_incarnation, confirmation=False):
    import scservo_sdk

    from examples.alohamini.am1_finite_task import FiniteTask
    from examples.alohamini.am1_session_contract import RECIPES

    # Native-shaped mock packets flow through the real grouped-read trace/decoder.
    from lerobot.motors import Motor, MotorCalibration, MotorNormMode
    from lerobot.motors.feetech import FeetechMotorsBus
    from lerobot.robots.alohamini.lift_motor_feedback import DirectSdkTransport
    from lerobot.robots.alohamini.selected_joint_evidence import AM1SelectedJointEvidence
    from tests.robots.test_alohamini_lift_motor_feedback import ByteStreamPort, status
    from tests.robots.test_am1_positive_control import (
        CALIBRATION,
        KEY,
        RECIPE,
        PositiveFeedback,
        native_positive_fixture,
        provider,
    )
    from tests.robots.test_am1_selected_joint_evidence import group_payload

    key = "arm_left_elbow_flex"
    bus = FeetechMotorsBus(
        "unused-reporting-fixture",
        {key: Motor(3, "sts3215", MotorNormMode.RANGE_M100_100)},
        {key: MotorCalibration(3, 0, 1845, 864, 3073)},
    )
    decoder = AM1SelectedJointEvidence(enabled=True)
    traces = {}

    def bind_and_trace(item):
        item.update(run_id=run_id, host_incarnation=host_incarnation)
        item["visible_control"]["binding"].update(run_id=run_id, host_incarnation=host_incarnation)
        for sample_key, receipt in item["sparse_receipts"].items():
            receipt.update(run_id=run_id, host_incarnation=host_incarnation, kind="sparse_readback")
            if sample_key in traces:
                receipt.update(copy.deepcopy(traces[sample_key]))
                continue
            payload = bytearray(
                group_payload(
                    goal=receipt["decoded"]["goal_position_raw"],
                    present=receipt["decoded"]["present_position_raw"],
                    current=2,
                )
            )
            payload[11:15] = b"\x00" * 4  # Actual goal-time/velocity fields are unchanged zero.
            port = ByteStreamPort([status(bytes(payload), motor_id=3)])
            transport = DirectSdkTransport(
                "unused-reporting-fixture",
                motor_id=3,
                port_handler=port,
                packet_handler=scservo_sdk.PacketHandler(0),
            )
            acquired, trace = transport.read_group(33, 38, timeout_ms=40.0)
            receipt.update(trace=trace, decoded=decoder._decoded(bus, acquired))
            traces[sample_key] = {"trace": copy.deepcopy(trace), "decoded": copy.deepcopy(receipt["decoded"])}
        return item

    initial = provider().origin
    comparison = None
    if confirmation:
        primary = native_positive_fixture()
        comparison = {
            "run_id": primary["run_id"],
            "reference_normalized": primary["execution"]["original_seed"][KEY],
            "calibration": CALIBRATION,
            "original_seed": primary["execution"]["original_seed"],
            "measured_origin_raw": primary["execution"]["positive_control"]["measured_origin_raw"],
            "measured_origin_proof": primary["execution"]["positive_control"]["measured_origin_proof"],
        }
    task = FiniteTask(
        RECIPES[RECIPE],
        initial,
        0.0,
        3.0,
        feedback_provenance="native-feedback",
        positive_comparator=comparison,
    )
    feed = None
    for sequence in range(1, 1201):
        now = sequence / 30
        if task.provider.preparing:
            positions, selected = initial, None
        else:
            if feed is None:
                feed = PositiveFeedback(task.provider._program, moves=moves)
            positions, selected = feed.get(now, sequence)
            selected = bind_and_trace(selected)
        task.advance(now, {"positions": positions, "sequence": sequence, "selected_joint": selected})
        if task.complete or not complete and task.provider.elapsed_s >= 27.0:
            break
    assert task.complete is complete
    return task.snapshot()


@pytest.mark.parametrize(
    "state", ["running", "faulted", "completed-pass", "completed-static", "completed-confirmation"]
)
def test_legitimate_positive_full_phase_record_fits_snapshot_and_socket(state, record_property):
    from tests.robots.test_am1_elbow_recovery_integration import reporting_authority

    root = Path(
        os.environ.get(
            "AM1_REPORTING_FIXTURES", ".cache/am1-visible-control-04/reporting-tests/native-fixtures"
        )
    )
    run = json.loads((root / "79def.json").read_bytes())
    run = copy.deepcopy(run)  # Labelled injected new-profile record, retaining the full native cleanup shape.
    complete = state.startswith("completed")
    execution = positive_execution(
        complete=complete,
        moves=state != "completed-static",
        run_id=run["run_id"],
        host_incarnation=run["backend_incarnation"],
        confirmation=state == "completed-confirmation",
    )
    run.update(
        recipe="physical-left-elbow-positive-control",
        execution=execution,
        status="completed" if complete else state,
        progress_s=execution["progress_s"],
        first_cause="selected current reached 100 mA" if state == "faulted" else None,
    )
    cleanup = run["physical_result"]["readback"]["readback"]
    cleanup.pop("elbow_integral")  # New I0 recipe performs no gain transaction; keep all20 real-shaped rows.
    for key in ("gain_recipes", "recovery_prerequisite", "startup_continuation", "retained_return_reference"):
        run.pop(key, None)
    run["backend_contract"].pop("elbow_return_reference", None)
    if complete:
        run["completion_receipt"].update(recipe=run["recipe"], execution=copy.deepcopy(execution))
        if state == "completed-confirmation":
            run["confirmation_of"] = execution["positive_control"]["confirmation_of"]
    else:
        run.pop("completion_receipt", None)
    if state == "running":
        run["physical_result"] = {
            "phase": "live",
            "run_id": run["run_id"],
            "host_incarnation": run["backend_incarnation"],
        }
        run.update(cleanup=None, uncertain=False)
    else:
        run["physical_result"]["requested_status"] = run["status"]
    provider, evidence = physical_evidence(run)
    authority = reporting_authority(run, evidence)
    authority.executor = provider
    original, original_evidence = copy.deepcopy(authority.run), copy.deepcopy(evidence)
    view = authority.snapshot()
    result = {"accepted": True, "status": "snapshot", "snapshot": view}
    full_size = len(ipc.encode({"id": str(uuid.uuid4()), "result": result}, ipc.RESPONSE_LIMIT))
    record_property(state + "_full_wrapped_bytes", full_size)
    reply, calls = asyncio.run(exchange(result, {"op": "snapshot"}))
    assert calls == [] and reply == result
    outcome = reply["snapshot"]["run"]["execution"]["positive_control"]
    assert outcome["program_completed"] is complete
    if complete:
        assert set(outcome["phase_coverage"]) == {
            "baseline",
            "outbound",
            "endpoint",
            "return",
            "origin",
            "final",
        } | ({"acquire"} if state == "completed-confirmation" else set())
        assert outcome["coarse_functional_pass"] is (state != "completed-static")
        public_receipt = reply["snapshot"]["run"]["completion_receipt"]
        assert "execution" not in public_receipt
        reference = public_receipt["execution_reference"]
        assert reference["kind"] == "am1-finite-completion-execution-reference" and reference["version"] == 1
        assert reference["field"] == "run.execution"
        assert (
            reference["sha256"]
            == hashlib.sha256(
                json.dumps(execution, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            ).hexdigest()
        )
        assert reply["snapshot"]["evidence"]["finite_completion"]["execution_reference"] == reference
        assert {k: v for k, v in public_receipt.items() if k != "execution_reference"} == {
            k: v for k, v in original["completion_receipt"].items() if k != "execution"
        }
        assert reply["snapshot"]["run"]["execution"] == original["execution"]
        assert set(outcome["phase_receipts"]) == {"baseline", "cycle1_endpoint", "cycle1_origin"}
        assert all(
            r["run_id"] == run["run_id"] and r["trace"]["response_complete"] is True
            for r in outcome["phase_receipts"].values()
        )
    if state != "running":
        assert len(reply["snapshot"]["run"]["physical_result"]["readback"]["readback"]["readbacks"]) == 20
    assert authority.run == original and authority._evidence() == original_evidence


@pytest.mark.parametrize(
    "bad", [None, "active", "foreign", "missing_uuid", "different", "numeric_type", "bad_proof"]
)
def test_positive_duplicate_reference_requires_exact_typed_terminal_proof(bad):
    from tests.robots.test_am1_positive_control import native_positive_fixture
    from tools.am1_session_core import _positive_completion_reference

    run = native_positive_fixture()
    evidence = {
        "source": run["source"],
        "lifecycle": copy.deepcopy(run["physical_result"]),
        "finite_completion": copy.deepcopy(run["completion_receipt"]),
    }
    original = copy.deepcopy(run)
    assert _positive_completion_reference(evidence, run) is not None
    if bad == "active":
        run["status"] = "running"
    elif bad == "foreign":
        evidence["finite_completion"]["host_incarnation"] = str(uuid.uuid4())
    elif bad == "missing_uuid":
        run["run_id"] = None
    elif bad == "different":
        run["execution"]["feedback_sequence"] += 1
    elif bad == "numeric_type":
        v = run["execution"]["positive_control"]["reference_raw"]
        run["execution"]["positive_control"]["reference_raw"] = float(v)
    elif bad == "bad_proof":
        run["execution"]["positive_control"]["phase_receipts"]["baseline"]["run_id"] = "foreign"
        run["completion_receipt"]["execution"] = copy.deepcopy(run["execution"])
        evidence["finite_completion"] = copy.deepcopy(run["completion_receipt"])
    reference = _positive_completion_reference(evidence, run)
    if bad is None:
        assert reference is not None and run == original
    else:
        assert reference is None
