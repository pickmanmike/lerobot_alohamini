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


async def exchange(result, command=None, secret="correct", protective=False, authority=None):
    calls = []
    authority = authority or SimpleNamespace(
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


def selected_native_record():
    """The exact retained actual06 durable record, without presentation fields."""
    root = Path(
        os.environ.get(
            "AM1_REPORTING_FIXTURES", ".cache/am1-visible-control-04/reporting-tests/native-fixtures"
        )
    )
    path = root / "1838.json"
    if "AM1_REPORTING_FIXTURES" not in os.environ and not path.exists():
        pytest.skip("Actual06 private reporting fixture is not installed")
    raw = path.read_bytes()  # An explicitly configured fixture is mandatory, including its hash.
    assert (
        hashlib.sha256(raw).hexdigest() == "3a0cdaab475c6e61ea32c3fc8b23234043831f6e43b66ab73730068dbf30e506"
    )
    run = json.loads(raw)
    canonical = json.dumps(run, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    assert (
        hashlib.sha256(canonical).hexdigest()
        == "576157ed3817afca89f26001489931adea1496cdd31357febf8c2f2e2a8a525e"
    )
    assert run["status"] == "stopped" and run["physical_result"]["terminal_status"] == "completed"
    return run


def selected_finishing_record(phase="finishing"):
    """Retained actual06 proof; only owner/lifecycle state is reconstructed for reporting."""
    from examples.alohamini.am1_session_contract import SelectedElbowExperiment

    run = selected_native_record()
    run.update(status="finishing", cleanup=None, requested_terminal="completed")
    run["physical_result"]["phase"] = phase
    if phase == "finishing":
        run["physical_result"]["cleanup"] = None
    provider, evidence = physical_evidence(run)
    provider.selected_experiment = SelectedElbowExperiment.from_payload(run["selected_experiment"])
    return run, provider, evidence


@pytest.mark.parametrize("phase", ["finishing", "terminal"])
def test_actual_selected_finishing_proof_fits_without_premature_cleanup(phase, record_property):
    from tests.robots.test_am1_elbow_recovery_integration import reporting_authority

    run, provider, evidence = selected_finishing_record(phase)
    authority = reporting_authority(run, evidence)
    authority.executor = provider
    original, original_evidence = copy.deepcopy(authority.run), copy.deepcopy(evidence)
    execution_bytes = len(json.dumps(run["execution"], separators=(",", ":"), sort_keys=True).encode())
    assert execution_bytes == 29212 and 3 * execution_bytes > ipc.RESPONSE_LIMIT
    view = authority.snapshot()
    result = {"accepted": True, "status": "snapshot", "snapshot": view}
    size = len(ipc.encode({"id": str(uuid.uuid4()), "result": result}, ipc.RESPONSE_LIMIT))
    record_property("actual06_" + phase + "_owner_snapshot_bytes", size)
    reply, calls = asyncio.run(exchange(result, {"op": "snapshot"}))
    assert calls == [] and reply == result
    public_run = view["run"]
    assert public_run["status"] == "finishing" and public_run["cleanup"] is None
    assert public_run["requested_terminal"] == "completed" and public_run["uncertain"] is False
    assert public_run["first_cause"] is None and view["evidence"]["lifecycle"]["phase"] == phase
    assert public_run["execution"] == original["execution"]
    assert public_run["physical_result"] == original["physical_result"]
    assert len(public_run["physical_result"]["readback"]["readback"]["readbacks"]) == 20
    reference = public_run["completion_receipt"]["execution_reference"]
    assert reference["field"] == "run.execution"
    assert (
        reference["sha256"]
        == hashlib.sha256(
            json.dumps(run["execution"], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    assert view["evidence"]["finite_completion"]["execution_reference"] == reference
    assert "execution" not in public_run["completion_receipt"]
    assert "execution" not in view["evidence"]["finite_completion"]
    assert authority.run == original and authority._evidence() == original_evidence


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "running",
        "live",
        "foreign_run",
        "foreign_host",
        "different",
        "partial",
        "preparing",
        "bad_proof",
        "foreign_definition",
        "foreign_descriptor",
        "foreign_source",
    ],
)
def test_selected_finishing_reference_requires_whole_exact_bound_completion(bad):
    from tools.am1_session_core import _positive_completion_reference

    run, _, evidence = selected_finishing_record()
    if bad == "running":
        run["status"] = "running"
    elif bad == "live":
        evidence["lifecycle"]["phase"] = "live"
    elif bad == "foreign_run":
        evidence["lifecycle"]["run_id"] = str(uuid.uuid4())
    elif bad == "foreign_host":
        evidence["lifecycle"]["backend_incarnation"] = str(uuid.uuid4())
    elif bad == "different":
        run["execution"]["feedback_sequence"] += 1
    elif bad in {"partial", "preparing", "bad_proof", "foreign_definition"}:
        if bad == "partial":
            run["execution"]["complete"] = False
        elif bad == "preparing":
            run["execution"]["preparing"] = True
        elif bad == "bad_proof":
            run["execution"]["positive_control"]["phase_receipts"].pop("cycle1_origin")
        else:
            run["execution"]["experiment"]["sha256"] = "0" * 64
        run["completion_receipt"]["execution"] = copy.deepcopy(run["execution"])
        evidence["finite_completion"] = copy.deepcopy(run["completion_receipt"])
    elif bad == "foreign_descriptor":
        run["selected_experiment_sha256"] = "0" * 64
    elif bad == "foreign_source":
        evidence["source"] = "simulated-provider"
    original, original_evidence = copy.deepcopy(run), copy.deepcopy(evidence)
    reference = _positive_completion_reference(evidence, run)
    assert (reference is not None) is (bad is None)
    assert run == original and evidence == original_evidence


def test_actual_selected_finishing_stop_remains_exact_owned_and_socket_usable(tmp_path, record_property):
    from tools.am1_session_core import SessionAuthority

    run, provider, evidence = selected_finishing_record()
    # A real authority/database handles Stop; the provider consumes only retained cached proof.
    authority = SessionAuthority(tmp_path / "authority", clock=lambda: 100.0)
    nonactuating_executor = authority.executor
    authority.executor, authority.asynchronous = provider, True
    authority.run = copy.deepcopy(run)
    authority.run["finish_ceiling"] = 160.0
    authority._evidence = lambda: copy.deepcopy(evidence)
    before = copy.deepcopy(authority.run["execution"])
    command = {"op": "stop", "run_id": run["run_id"], "operation_id": str(uuid.uuid4())}
    try:
        result, _ = asyncio.run(exchange(None, command, protective=True, authority=authority))
        assert result == {"accepted": True, "status": "finishing", "effect_admitted": False}
        assert authority.run["requested_terminal"] == "stopped"
        assert authority.run["cleanup"] is None and authority.run["first_cause"] is None
        assert authority.run["execution"] == before and provider.worker is None
        reply, _ = asyncio.run(exchange(None, {"op": "snapshot"}, authority=authority))
        assert reply["status"] == "snapshot" and reply["snapshot"]["run"]["status"] == "finishing"
        record_property("actual06_post_stop_socket_result_bytes", len(ipc.encode(reply)))
        assert authority.db.execute("SELECT count(*) FROM mutations").fetchone()[0] == 1
        assert asyncio.run(exchange(None, command, protective=True, authority=authority))[0] == result
        assert authority.db.execute("SELECT count(*) FROM mutations").fetchone()[0] == 1
    finally:
        authority.executor, authority.asynchronous = nonactuating_executor, False
        authority.close()


def selected_idle_provider(run):
    from tests.robots.test_am1_physical_executor import ControlledHost, executor
    from tools.am1_local_observation import LocalObservationMailbox

    provider = executor(ControlledHost())
    provider.config = copy.deepcopy(run["backend_contract"])
    provider.config["source_pins"] = "/private/reviewed-idle-runtime-manifest.json"
    provider.clock = lambda: 100.0
    provider.observation = LocalObservationMailbox(run["sensing_source"], clock=provider.clock)
    provider.io_factory = lambda *_: pytest.fail("Retained idle reporting opened a physical IO owner")
    assert provider.worker is None and provider.run_id is None and provider._finite_completion is None
    return provider


def selected_idle_authority(tmp_path):
    from tests.robots.test_am1_elbow_recovery_integration import reporting_authority
    from tools.am1_session_core import SessionAuthority

    run = selected_native_record()
    writer = SessionAuthority(tmp_path / "authority", clock=lambda: 100.0)
    try:
        writer.run = copy.deepcopy(run)
        writer._save()
    finally:
        writer.close()
    provider = selected_idle_provider(run)
    authority = SessionAuthority(tmp_path / "authority", clock=provider.clock, executor=provider)
    authority.events = reporting_authority(run, provider.evidence()).events
    assert authority.run == run and authority.service_incarnation != run["service_incarnation"]
    return authority


def test_actual_selected_retained_idle_proof_fits_socket_without_current_completion(
    tmp_path, record_property
):
    authority = selected_idle_authority(tmp_path)
    try:
        original, evidence = copy.deepcopy(authority.run), authority.executor.evidence()
        assert evidence["lifecycle"]["phase"] == "idle" and evidence["finite_completion"] is None
        assert evidence["host_incarnation"] is None and evidence["accepted_host_action"] == {}
        assert evidence["backend_contract"] != original["backend_contract"]
        result, _ = asyncio.run(exchange(None, {"op": "snapshot"}, authority=authority))
        assert result["status"] == "snapshot" and result["accepted"] is True
        view = result["snapshot"]
        record_property("actual06_retained_idle_socket_result_bytes", len(ipc.encode(result)))
        assert view["evidence"] == evidence
        assert view["evidence"]["finite_completion"] is None
        public = copy.deepcopy(view["run"])
        assert public["execution"] == original["execution"]
        reference = public["completion_receipt"].pop("execution_reference")
        assert reference["field"] == "run.execution"
        assert (
            reference["sha256"]
            == hashlib.sha256(
                json.dumps(original["execution"], sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        )
        public["completion_receipt"]["execution"] = copy.deepcopy(public["execution"])
        public.pop("remaining_s")
        public.pop("trajectory_remaining_s")
        assert json.dumps(public, sort_keys=True) == json.dumps(original, sort_keys=True)
        assert authority.run == original and authority.executor.evidence() == evidence
        assert authority.executor.worker is None and authority.executor._reconciliation_worker is None
    finally:
        authority.close()


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "running",
        "finishing",
        "foreign_source",
        "current_host",
        "current_run",
        "current_completion",
        "current_ack",
        "current_feedback",
        "current_uncertain",
        "current_uncertain_zero",
        "owner_uncertain",
        "cleanup_uncertain",
        "owner_cleanup",
        "cleanup_row",
        "camera_session",
        "camera_release",
        "foreign_receipt_run",
        "foreign_receipt_host",
        "foreign_lifecycle",
        "invalid_uuid",
        "different",
        "partial",
        "preparing",
        "bad_proof",
        "numeric_type",
        "foreign_descriptor",
        "foreign_definition",
    ],
)
def test_selected_retained_idle_reference_requires_exact_complete_known_cleanup(bad):
    from tools.am1_session_core import _positive_completion_reference

    run = selected_native_record()
    evidence = selected_idle_provider(run).evidence()
    if bad in {"running", "finishing"}:
        run["status"] = bad
    elif bad == "foreign_source":
        evidence["source"] = "simulated-provider"
    elif bad == "current_host":
        evidence["host_incarnation"] = str(uuid.uuid4())
    elif bad == "current_run":
        evidence["lifecycle"]["run_id"] = str(uuid.uuid4())
    elif bad == "current_completion":
        evidence["finite_completion"] = copy.deepcopy(run["completion_receipt"])
    elif bad == "current_ack":
        evidence["accepted_host_action"] = {"run_id": run["run_id"]}
    elif bad == "current_feedback":
        evidence["feedback"] = True
    elif bad == "current_uncertain":
        evidence["lifecycle"]["uncertain"] = True
    elif bad == "current_uncertain_zero":
        evidence["lifecycle"]["uncertain"] = 0
    elif bad == "owner_uncertain":
        run["uncertain"] = True
    elif bad == "cleanup_uncertain":
        run["physical_result"]["uncertain"] = True
    elif bad == "owner_cleanup":
        run["cleanup"] = "physical_cleanup_unknown"
    elif bad == "cleanup_row":
        run["physical_result"]["readback"]["readback"]["readbacks"][0]["value"] = 1
    elif bad == "camera_session":
        run["physical_result"]["camera_release"]["session_id"] = str(uuid.uuid4())
    elif bad == "camera_release":
        run["physical_result"]["camera_release"]["release_evidence"]["readers_joined"] = False
    elif bad == "foreign_receipt_run":
        run["completion_receipt"]["run_id"] = str(uuid.uuid4())
    elif bad == "foreign_receipt_host":
        run["completion_receipt"]["host_incarnation"] = str(uuid.uuid4())
    elif bad == "foreign_lifecycle":
        run["physical_result"]["backend_incarnation"] = str(uuid.uuid4())
    elif bad == "invalid_uuid":
        run["run_id"] = run["completion_receipt"]["run_id"] = "invalid"
    elif bad == "different":
        run["execution"]["feedback_sequence"] += 1
    elif bad == "foreign_descriptor":
        run["selected_experiment_sha256"] = "0" * 64
    elif bad in {"partial", "preparing", "bad_proof", "numeric_type", "foreign_definition"}:
        if bad == "partial":
            run["execution"]["complete"] = False
        elif bad == "preparing":
            run["execution"]["preparing"] = True
        elif bad == "bad_proof":
            run["execution"]["positive_control"]["phase_receipts"].pop("cycle1_origin")
        elif bad == "numeric_type":
            run["execution"]["cycles_completed"] = 1.0
        else:
            run["execution"]["experiment"]["sha256"] = "0" * 64
        run["completion_receipt"]["execution"] = copy.deepcopy(run["execution"])
    before_run, before_evidence = copy.deepcopy(run), copy.deepcopy(evidence)
    assert (_positive_completion_reference(evidence, run) is not None) is (bad is None)
    assert run == before_run and evidence == before_evidence


def actual_selected_final_snapshot():
    root = Path(
        os.environ.get(
            "AM1_REPORTING_FIXTURES", ".cache/am1-visible-control-04/reporting-tests/native-fixtures"
        )
    )
    path = root / "9a45-final.json"
    if "AM1_REPORTING_FIXTURES" not in os.environ and not path.exists():
        pytest.skip("Actual07 private final snapshot is not installed")
    raw = path.read_bytes()
    assert (
        hashlib.sha256(raw).hexdigest() == "1fe0f0db9906485d29d2834ca4674755334560a13080d8156201d475c3cd269d"
    )
    value = ipc.strict_json(raw)
    assert value["run"]["run_id"] == "9a45da93-1f2b-4f97-aa7b-523ba9f0419c"
    assert value["run"]["status"] == "completed" and value["run"]["uncertain"] is False
    return value


def snapshot_http_gateway(value):
    gateway = Gateway.__new__(Gateway)
    gateway.tickets, gateway.revision, gateway.incarnation = {}, 0, str(uuid.uuid4())

    async def current():
        return copy.deepcopy(value)

    async def body(request):
        return {}

    gateway.current, gateway.body = current, body
    return gateway


@pytest.mark.parametrize("route", ["state", "attach"])
def test_actual_selected_final_http_wrappers_fit_without_payload_changes(route, record_property):
    value = actual_selected_final_snapshot()
    original = copy.deepcopy(value)
    gateway = snapshot_http_gateway(value)
    response = asyncio.run(getattr(gateway, route)({"device": {"id": "offline-viewer"}}))
    record_property("actual07_compact_http_" + route + "_bytes", len(response.body))
    assert response.status == 200 and len(response.body) <= ipc.RESPONSE_LIMIT
    assert response.content_type == "application/json"
    decoded = ipc.strict_json(response.body)
    assert decoded["snapshot"] == original and value == original
    if route == "state":
        assert set(decoded) == {"snapshot"} and response.headers["Cache-Control"] == "no-store"
    else:
        assert set(decoded) == {"snapshot", "ticket", "revision"}
        assert decoded["revision"] == gateway.incarnation + ":1"
        assert len(gateway.tickets) == 1 and len(decoded["ticket"]) == 43


def test_compact_http_json_preserves_typed_masked_strings_and_rejects_nonfinite():
    from tools.am1_session_service import _compact_http_json

    value = {
        "masked": "<masked>",
        "text": "literal spaces : ,\n雪",
        "int": 1,
        "float": 1.0,
        "boolean": True,
        "none": None,
        "nested": [False, {}, []],
    }
    original = copy.deepcopy(value)
    encoded = _compact_http_json(value)
    assert encoded == json.dumps(value, separators=(",", ":"), allow_nan=False)
    assert ipc.strict_json(encoded) == value and value == original
    assert type(ipc.strict_json(encoded)["float"]) is float
    for bad in (float("nan"), float("inf"), -float("inf")):
        with pytest.raises(ValueError):
            _compact_http_json({"value": bad})


@pytest.mark.parametrize("route", ["state", "attach"])
def test_snapshot_http_nonfinite_output_refuses_without_replacing_payload(route):
    value = {"run": {"run_id": "saved", "position": float("nan")}}
    gateway = snapshot_http_gateway(value)
    with pytest.raises(ValueError):
        asyncio.run(getattr(gateway, route)({"device": {"id": "offline-viewer"}}))
    assert value["run"]["run_id"] == "saved"


@pytest.mark.parametrize("route", ["state", "attach"])
def test_snapshot_http_correlated_reporting_error_is_not_serialized_as_snapshot(route):
    value = {
        "accepted": True,
        "status": "reporting_error",
        "code": "response_limit",
        "reason": "full response too large",
        "run_id": str(uuid.uuid4()),
        "operation_result": {"accepted": True, "status": "snapshot"},
    }
    gateway = snapshot_http_gateway(None)

    async def unavailable():
        raise ipc.ReportingError(value)

    gateway.current = unavailable
    with pytest.raises(ipc.ReportingError) as caught:
        asyncio.run(getattr(gateway, route)({"device": {"id": "offline-viewer"}}))
    assert caught.value.result == value


def test_compact_snapshot_attach_still_refuses_unexpected_body_before_snapshot():
    gateway = snapshot_http_gateway(None)
    calls = []

    async def body(request):
        return {"unrequested": True}

    async def current():
        calls.append("snapshot")
        return {}

    gateway.body, gateway.current = body, current
    with pytest.raises(ValueError, match="attach payload"):
        asyncio.run(gateway.attach({"device": {"id": "offline-viewer"}}))
    assert calls == [] and gateway.tickets == {}
