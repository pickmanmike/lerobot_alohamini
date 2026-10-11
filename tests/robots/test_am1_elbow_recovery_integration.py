"""Recovery admission and sole-owner guards; injected caches only."""

import copy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from lerobot.robots.alohamini import alohamini_host as host
from lerobot.robots.alohamini.selected_joint_evidence import AM1SelectedJointEvidence
from tests.robots.test_am1_selected_joint_evidence import robot


def args():
    return SimpleNamespace(
        robot_model="alohamini1",
        no_follower=False,
        no_cameras=True,
        am1_run_id=str(uuid4()),
        am1_host_incarnation=str(uuid4()),
        am1_protected_run_directory="/private/run",
    )


def flags(monkeypatch, integral="I1", recipe="physical-left-elbow-recovery-i1"):
    monkeypatch.setenv("AM1_ELBOW_RECOVERY", integral)
    monkeypatch.setenv("AM1_ELBOW_RECOVERY_RECIPE", recipe)
    monkeypatch.setenv("AM1_SCRIPTED_PREPARE", "1")
    monkeypatch.setenv("AM1_SELECTED_JOINT_EVIDENCE", "1")


def test_bare_gain_flag_is_refused_before_motor_construction(monkeypatch):
    monkeypatch.setenv("AM1_ELBOW_RECOVERY", "I1")
    monkeypatch.delenv("AM1_ELBOW_RECOVERY_RECIPE", raising=False)
    with pytest.raises(ValueError):
        host.elbow_recovery_binding(args())


@pytest.mark.parametrize(
    "field,value",
    [
        ("AM1_ELBOW_RECOVERY", "I2"),
        ("AM1_ELBOW_RECOVERY_RECIPE", "physical-left-elbow-original-return"),
        ("AM1_SCRIPTED_PREPARE", "0"),
        ("AM1_SELECTED_JOINT_EVIDENCE", "0"),
    ],
)
def test_recipe_identity_and_existing_gates_are_required(monkeypatch, field, value):
    flags(monkeypatch)
    monkeypatch.setenv(field, value)
    with pytest.raises(ValueError):
        host.elbow_recovery_binding(args())


def test_exact_recovery_binding_and_ordinary_no_effect(monkeypatch):
    flags(monkeypatch)
    actual = args()
    assert host.elbow_recovery_binding(actual) == {
        "run_id": actual.am1_run_id,
        "host_incarnation": actual.am1_host_incarnation,
        "recipe": "physical-left-elbow-recovery-i1",
        "integral": 1,
    }
    monkeypatch.delenv("AM1_ELBOW_RECOVERY")
    monkeypatch.delenv("AM1_ELBOW_RECOVERY_RECIPE")
    assert host.elbow_recovery_binding(actual) is None


def guarded(monkeypatch, *, current_raw=0, position_raw=2453):
    obj, reads, writes = robot(monkeypatch)
    ident = {"run_id": str(uuid4()), "host_incarnation": str(uuid4())}
    obj._am1_elbow_recovery_binding = dict(ident, recipe="physical-left-elbow-recovery-i1", integral=1)
    obj._am1_selected_joint_position = {
        "raw": position_raw,
        "normalized": 0.0,
        "read_started_at_monotonic_s": 0.99,
        "read_completed_at_monotonic_s": 1.0,
    }
    obj._am1_selected_joint_current = {
        "raw": current_raw,
        "ma": current_raw * 6.5,
        "read_started_at_monotonic_s": 0.99,
        "read_completed_at_monotonic_s": 1.0,
    }
    control = SimpleNamespace(binding=ident, state="paused", epoch=1)
    evidence = AM1SelectedJointEvidence(enabled=True, clock=lambda: 1.0, emit=lambda _: None)
    return obj, control, evidence, reads, writes


def test_native_cap_during_paused_hold_without_diagnostic_context(monkeypatch):
    obj, control, evidence, reads, writes = guarded(monkeypatch, current_raw=16)
    before = copy.deepcopy((reads, writes))
    with pytest.raises(RuntimeError, match="effort"):
        evidence.guard_recovery(obj, control, {})
    assert (reads, writes) == before


def test_native_deadline_does_not_reset_during_pause(monkeypatch):
    obj, control, evidence, reads, writes = guarded(monkeypatch)
    evidence.guard_recovery(obj, control, {})
    evidence.clock = lambda: 61.0
    obj._am1_selected_joint_current.update(
        read_started_at_monotonic_s=60.99, read_completed_at_monotonic_s=61.0
    )
    obj._am1_selected_joint_position.update(
        read_started_at_monotonic_s=60.99, read_completed_at_monotonic_s=61.0
    )
    with pytest.raises(RuntimeError, match="60-second"):
        evidence.guard_recovery(obj, control, {})
    assert not reads and not writes


def test_native_wrong_direction_rejected_with_actual_fresh_cache(monkeypatch):
    obj, control, evidence, reads, writes = guarded(monkeypatch)
    evidence.guard_recovery(obj, control, {})
    obj._am1_selected_joint_position.update(raw=2450)
    with pytest.raises(RuntimeError, match="position"):
        evidence.guard_recovery(obj, control, {})
    assert not reads and not writes


@pytest.mark.parametrize(
    "recipe",
    [
        "physical-left-elbow-recovery-i1",
        "physical-left-elbow-recovery-i0-dwell",
        "physical-left-elbow-recovery-i1-dwell",
        "physical-left-elbow-recovery-i0-confirm",
        "physical-left-elbow-recovery-i1-confirm",
    ],
)
def test_conditional_recovery_is_unavailable_without_a_native_previous_run(recipe):
    from examples.alohamini.am1_session_contract import RECIPES
    from tools.am1_session_core import SessionAuthority

    authority = SessionAuthority.__new__(SessionAuthority)
    authority.run = None
    assert authority.elbow_recovery_prerequisite(RECIPES[recipe], {}) is None


def test_confirmation_final_sparse_key_uses_the_shared_definition(monkeypatch):
    from tests.robots.test_am1_selected_joint_evidence import binding, command, context

    obj, reads, writes = robot(monkeypatch)
    identity = binding()
    obj._am1_elbow_recovery_binding = dict(
        identity, recipe="physical-left-elbow-recovery-i0-confirm", integral=0
    )
    evidence = AM1SelectedJointEvidence(enabled=True, emit=lambda _: None)
    control = host.AM1LocalControl(**identity, selected_evidence=evidence)
    metadata = context(
        profile="LeftElbowRecoveryI0Confirm",
        progress_s=17.8,
        phase="final",
        cycle=2,
        sample_key="final_origin",
    )
    assert control.apply(obj, command(identity, metadata))
    assert (42, 2, {3: 2000}) in writes


def test_same_ack_gain_proof_keeps_actual_clocks_without_full_private_ledger(monkeypatch):
    import json

    obj, _, _ = robot(monkeypatch)
    identity = {"run_id": str(uuid4()), "host_incarnation": str(uuid4())}
    obj._am1_elbow_integral_trial = SimpleNamespace(
        evidence={
            "state": "active",
            "status": "active",
            "integral": 1,
            "active_verified": True,
            "active_verified_at_monotonic_s": 4.25,
            "binding": dict(identity, recipe="physical-left-elbow-recovery-i1", integral=1),
            "original_registers": {"I_Coefficient": 0},
            "original_bytes": {"24": 16, "25": 0},
            "active_registers": {"I_Coefficient": 1},
            "active_bytes": {"24": 16, "25": 0},
            "events": [
                {
                    "phase": "baseline",
                    "operation": "read",
                    "started_at_monotonic_s": 1.25,
                    "completed_at_monotonic_s": 1.5,
                    "value": 0,
                }
            ]
            * 1000,
            "snapshot_path": "/private/obligation.json",
        }
    )
    evidence = AM1SelectedJointEvidence(enabled=True, emit=lambda _: None)
    control = host.AM1LocalControl(**identity, selected_evidence=evidence)
    proof = evidence._base(obj, control, {"observation_id": 2, "acquired_at_monotonic_s": 5})["gain_trial"]
    assert len(json.dumps(proof).encode()) < 4096
    assert proof["active_verified_at_monotonic_s"] == 4.25
    assert "events" not in proof and "snapshot_path" not in proof
    assert proof["original_read_window"] == {"started_at_monotonic_s": 1.25, "completed_at_monotonic_s": 1.5}


def recovery_execution(*, follows=False):
    from tests.robots.test_am1_elbow_recovery import drive, execution, provider

    p = provider(0)
    drive(p, follows=follows)
    result = execution(p)
    result.update(
        complete=True,
        preparing=False,
        admissions=1,
        feedback_sequence=41,
        initial_measured_reference=dict(p.origin),
    )
    return result


@pytest.mark.parametrize(
    "fault",
    [None, "attainment", "gain_binding", "gain_pid", "count", "residual", "goal", "vector", "pre_live"],
)
def test_core_honest_static_diagnostic_completion_keeps_all_native_proof_guards(tmp_path, fault):
    from tests.robots.test_am1_elbow_recovery import reference
    from tests.robots.test_am1_elbow_response_profile import norm
    from tests.robots.test_am1_persistent_session import start
    from tests.robots.test_am1_physical_authority import admit_live, build, completed_handoff

    key = "arm_left_elbow_flex.pos"
    recipe = "physical-left-elbow-recovery-i0"
    clock, backend, authority = build(tmp_path)
    backend.supports = lambda r: r.name == recipe
    try:
        assert start(authority, recipe)["accepted"]
        if fault == "pre_live":
            clock.advance(8)
        admit_live(authority, backend, clock)
        clock.advance(8)
        receipt, _ = completed_handoff(authority, backend, clock)
        result = recovery_execution()
        result["recovery"]["gain_trial"]["binding"].update(
            run_id=receipt["run_id"], host_incarnation=receipt["host_incarnation"]
        )
        receipt["execution"].update(result)
        receipt["measured_positions"] = dict(result["original_seed"], **{key: norm(2453)})
        if fault == "attainment":
            receipt["execution"]["recovery"]["target_attained"] = True
        elif fault == "gain_binding":
            receipt["execution"]["recovery"]["gain_trial"]["binding"]["run_id"] = "foreign"
        elif fault == "gain_pid":
            receipt["execution"]["recovery"]["gain_trial"]["active_registers"]["P_Coefficient"] = 32
        elif fault == "count":
            receipt["execution"]["returns_qualified"] = 1
        elif fault == "residual":
            receipt["execution"]["recovery"]["final_return_residual_raw"] = 3
        elif fault == "goal":
            receipt["execution"]["recovery"]["origin_goal_raw"] = 2453
        elif fault == "vector":
            receipt["execution"]["original_seed"] = {key: result["original_seed"][key]}
            receipt["measured_positions"] = {key: norm(2453)}
        old = backend.evidence
        config = {"host_head": "9" * 40, "elbow_return_reference": {"sha256": "a" * 64}}
        backend.evidence = lambda: dict(old(), backend_contract=config, retained_return_reference=reference())
        authority.tick()
        assert (authority.run["status"] == "finishing") is (fault is None)
        if fault is None:
            assert authority.run["requested_terminal"] == "completed"
            assert authority.run["recovery_episodes"] == 0
            assert authority.run["execution"]["returns_qualified"] == 0
            assert not authority.run["execution"]["recovery"]["target_attained"]
    finally:
        authority.close()


def prior_native_i0():
    from tests.robots.test_am1_elbow_recovery import reference
    from tests.robots.test_am1_physical_executor import terminal_cleanup_outcome

    run_id, incarnation = str(uuid4()), str(uuid4())
    result = recovery_execution()
    gain = result["recovery"]["gain_trial"]
    gain["binding"].update(run_id=run_id, host_incarnation=incarnation)
    lifecycle = terminal_cleanup_outcome(run_id, incarnation, "completed")
    lifecycle.update(run_id=run_id, phase="terminal", first_cause=None)
    lifecycle["readback"]["first_cause"] = None
    lifecycle["readback"]["readback"]["elbow_integral"] = {
        "state": "restored",
        "restore_required": False,
        "binding": copy.deepcopy(gain["binding"]),
        "restored_integral_readback": 0,
        "restored_lock_readback": gain["original_registers"]["Lock"],
        "first_failure": None,
    }
    run = {
        "run_id": run_id,
        "backend_incarnation": incarnation,
        "recipe": "physical-left-elbow-recovery-i0",
        "source": "protected-physical-provider",
        "status": "completed",
        "uncertain": False,
        "cleanup": "physical_readback_verified",
        "first_cause": None,
        "execution": result,
        "physical_result": lifecycle,
        "retained_return_reference": reference(),
        "backend_contract": {"host_head": "9" * 40, "elbow_return_reference": {"sha256": "a" * 64}},
        "completion_receipt": {
            "run_id": run_id,
            "host_incarnation": incarnation,
            "recipe": "physical-left-elbow-recovery-i0",
            "execution": copy.deepcopy(result),
            "completed_at_monotonic_s": 6.0,
        },
    }
    return run


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "unknown_cleanup",
        "foreign_lifecycle",
        "gain_restore",
        "lock_restore",
        "foreign_restore",
        "bool_restore",
        "missing_twenty",
        "first_fault",
        "invented_execution",
    ],
)
def test_i1_start_requires_actual_same_run_original_settings_restore(fault):
    from examples.alohamini.am1_session_contract import RECIPES
    from tools.am1_session_core import SessionAuthority

    authority = SessionAuthority.__new__(SessionAuthority)
    authority.run = prior_native_i0()
    run = authority.run
    if fault == "unknown_cleanup":
        run["uncertain"] = True
    elif fault == "gain_restore":
        run["physical_result"]["readback"]["readback"]["elbow_integral"]["restored_integral_readback"] = 1
    elif fault == "lock_restore":
        run["physical_result"]["readback"]["readback"]["elbow_integral"]["restored_lock_readback"] = 1
    elif fault == "foreign_restore":
        run["physical_result"]["readback"]["readback"]["elbow_integral"]["binding"]["run_id"] = "foreign"
    elif fault == "bool_restore":
        run["physical_result"]["readback"]["readback"]["elbow_integral"]["restored_integral_readback"] = False
    elif fault == "missing_twenty":
        run["physical_result"]["readback"]["readback"]["readbacks"].pop()
    elif fault == "first_fault":
        run["first_cause"] = "current_cap"
    elif fault == "invented_execution":
        run["completion_receipt"]["execution"]["recovery"]["target_attained"] = True
    evidence = {"lifecycle": copy.deepcopy(run["physical_result"])}
    if fault == "foreign_lifecycle":
        evidence["lifecycle"]["run_id"] = "foreign"
    proof = authority.elbow_recovery_prerequisite(RECIPES["physical-left-elbow-recovery-i1"], evidence)
    assert (proof is not None) is (fault is None)
    if proof:
        assert proof["run_id"] == run["run_id"] and proof["integral"] == 0
        assert set(proof) == {
            "run_id",
            "host_incarnation",
            "proof_sha256",
            "initial_measured_reference",
            "calibration",
            "trajectory_s",
            "hold_s",
            "integral",
        }


@pytest.mark.parametrize(
    "fault", ["camera_identity", "camera_error", "camera_runtime", "buses_open", "boolean_version"]
)
def test_conditional_admission_retains_complete_typed_motor_camera_cleanup(fault):
    from examples.alohamini.am1_session_contract import RECIPES
    from tools.am1_session_core import SessionAuthority

    authority = SessionAuthority.__new__(SessionAuthority)
    authority.run = prior_native_i0()
    lifecycle = authority.run["physical_result"]
    if fault == "camera_identity":
        lifecycle["camera_release"]["session_id"] = "foreign"
    elif fault == "camera_error":
        lifecycle["camera_release"]["errors"] = ["reader failed"]
    elif fault == "camera_runtime":
        lifecycle["camera_release"]["runtime_failed"] = True
    elif fault == "buses_open":
        lifecycle["readback"]["readback"]["buses_closed"] = False
    else:
        lifecycle["readback"]["version"] = True
    assert (
        authority.elbow_recovery_prerequisite(
            RECIPES["physical-left-elbow-recovery-i1"], {"lifecycle": copy.deepcopy(lifecycle)}
        )
        is None
    )


def test_actual_sdk_confirmation_receipts_roundtrip_gain_proof_without_private_ledger(monkeypatch):
    import json

    from lerobot.robots.alohamini.alohamini_client import AlohaMiniClient
    from tests.robots.test_alohamini_lift_motor_feedback import status
    from tests.robots.test_am1_elbow_recovery import RecoveryFeedback, provider
    from tests.robots.test_am1_physical_executor import client_sample
    from tests.robots.test_am1_selected_joint_evidence import (
        attach_port,
        binding,
        command,
        context,
        group_payload,
        observe,
    )
    from tools.am1_physical_executor import read_physical_sample

    obj, _, _ = robot(monkeypatch)
    identity = binding()
    obj._am1_elbow_recovery_binding = dict(
        identity, recipe="physical-left-elbow-recovery-i0-confirm", integral=0
    )
    p = provider(0, "-confirm")
    _, feedback = RecoveryFeedback(p).get(0.1, 1)
    gain = copy.deepcopy(feedback["gain_trial"])
    gain["binding"].update(identity)
    gain["active_registers"].update(Min_Position_Limit=1000, Max_Position_Limit=3000)
    gain["events"] = [
        {
            "phase": "baseline",
            "operation": "read",
            "started_at_monotonic_s": 0,
            "completed_at_monotonic_s": 0.01,
            "value": 0,
        }
    ] * 500
    obj._am1_elbow_integral_trial = SimpleNamespace(
        evidence=gain, restore_required=True, check_current=lambda *_: None
    )
    evidence = AM1SelectedJointEvidence(enabled=True, emit=lambda _: None)
    control = host.AM1LocalControl(**identity, selected_evidence=evidence)
    phases = [
        (0, "baseline", 0, 0, "baseline"),
        (5, "endpoint", 1, 3, "cycle1_endpoint"),
        (8.8, "origin", 1, 0, "cycle1_origin"),
        (12, "endpoint", 2, 3, "cycle2_endpoint"),
        (15.8, "origin", 2, 0, "cycle2_origin"),
        (17.8, "final", 2, 0, "final_origin"),
    ]
    goals = [2000, 2030, 2000, 2030, 2000, 2000]
    port = attach_port(obj, [status(group_payload(goal=goal), motor_id=3) for goal in goals])
    for sequence, (progress, phase, cycle, requested, key) in enumerate(phases, 1):
        metadata = context(
            profile="LeftElbowRecoveryI0Confirm",
            progress_s=progress,
            phase=phase,
            cycle=cycle,
            requested_target=requested,
            sample_key=key,
        )
        assert control.apply(obj, command(identity, metadata, sequence=sequence))
        evidence.report(obj, control, observe(obj, control))
    observation = observe(obj, control)
    evidence.publish(obj, control, observation)
    frames = host.build_observation_multipart(observation, [])
    assert len(port.writes) == 6 and len(frames[0]) < 65536
    client = AlohaMiniClient.__new__(AlohaMiniClient)
    client.config, client.logs = SimpleNamespace(cameras={}), {}
    parsed = client._parse_observation_message(frames)[0]
    marker = parsed[host.AM1_LOCAL_FEEDBACK_KEY]
    note = marker["selected_joint"]
    assert len(note["sparse_receipts"]) == 6
    assert "events" not in note["gain_trial"] and note["gain_trial"]["active_verified_at_monotonic_s"] == 0
    assert all(
        row["trace"]["checksum_ok"] and row["trace"]["response_id"] == 3
        for row in note["sparse_receipts"].values()
    )
    acquired = marker["acquired_at_monotonic_s"]
    marker["read_started_at_monotonic_s"] = acquired - 0.01
    actual = client_sample()
    actual.latest_am1_local_feedback = marker
    actual.latest_observation_received_at = acquired + 0.005
    actual.latest_observation_roundtrip_age_s = 0.02
    actual.get_observation = lambda: dict.fromkeys(
        __import__("examples.alohamini.am1_finite_task", fromlist=["JOINT_KEYS"]).JOINT_KEYS, 0.0
    )
    sample = read_physical_sample(
        actual, identity["run_id"], identity["host_incarnation"], -1, clock=lambda: acquired + 0.01
    )
    assert sample["selected_joint"] == note and "selected_joint" not in sample["ack"]
    assert len(json.dumps(sample["ack"]).encode()) < 4096


def test_native_approach_direction_guard_survives_return_to_origin_phase(monkeypatch):
    obj, control, evidence, reads, writes = guarded(monkeypatch)
    control.state = "active"
    evidence.context = {"cycle": 1, "phase": "return"}
    evidence.guard_recovery(obj, control, {})
    obj._am1_selected_joint_position["raw"] = 2462
    evidence.guard_recovery(obj, control, {})
    evidence.context = {"cycle": 1, "phase": "origin"}
    obj._am1_selected_joint_position["raw"] = 2459
    with pytest.raises(RuntimeError, match="reversed"):
        evidence.guard_recovery(obj, control, {})
    assert not reads and not writes


def native_i1_outcome(*, partial=False, follows=True):
    from tests.robots.test_am1_elbow_recovery import drive, execution, provider
    from tests.robots.test_am1_elbow_response_profile import norm

    p = provider(1)

    def cap_progress(provider, now, positions, selected):
        measured = min(selected["position"]["raw"], 2453 + (12 if partial is True else partial))
        positions["arm_left_elbow_flex.pos"] = norm(measured)
        selected["position"].update(raw=measured, normalized=norm(measured))
        for receipt in selected["sparse_receipts"].values():
            if receipt["context"]["phase"] == "origin":
                receipt["decoded"]["present_position_raw"] = 2453 + (12 if partial is True else partial)

    drive(p, follows=follows, mutate=cap_progress if partial else None)
    result = execution(p)
    result.update(
        complete=True,
        preparing=False,
        admissions=1,
        feedback_sequence=41,
        initial_measured_reference=dict(p.origin),
    )
    run = prior_native_i0()
    result["recovery"]["gain_trial"]["binding"].update(
        run_id=run["run_id"], host_incarnation=run["backend_incarnation"]
    )
    run.update(recipe=p.definition.recipe_name, execution=result)
    run["completion_receipt"].update(recipe=p.definition.recipe_name, execution=copy.deepcopy(result))
    run["physical_result"]["readback"]["readback"]["elbow_integral"]["binding"] = copy.deepcopy(
        result["recovery"]["gain_trial"]["binding"]
    )
    return run


@pytest.mark.parametrize(
    "partial,follows,dwell,confirm",
    [
        (True, True, True, False),
        (False, True, False, True),
        (False, False, False, False),
    ],
)
def test_conditional_workloads_require_the_actual_useful_partial_or_attained_result(
    partial, follows, dwell, confirm
):
    from examples.alohamini.am1_session_contract import RECIPES
    from tools.am1_session_core import SessionAuthority

    authority = SessionAuthority.__new__(SessionAuthority)
    authority.run = native_i1_outcome(partial=partial, follows=follows)
    evidence = {"lifecycle": copy.deepcopy(authority.run["physical_result"])}
    assert (
        authority.elbow_recovery_prerequisite(RECIPES["physical-left-elbow-recovery-i0-dwell"], evidence)
        is not None
    ) is dwell
    assert (
        authority.elbow_recovery_prerequisite(RECIPES["physical-left-elbow-recovery-i1-confirm"], evidence)
        is not None
    ) is confirm


@pytest.mark.parametrize(
    "recipe_name",
    [
        "physical-left-elbow-recovery-i0",
        "physical-left-elbow-recovery-i1",
        "physical-left-elbow-recovery-i0-dwell",
        "physical-left-elbow-recovery-i1-dwell",
        "physical-left-elbow-recovery-i0-confirm",
        "physical-left-elbow-recovery-i1-confirm",
        "physical-arm-hold-body",
    ],
)
def test_only_exact_new_host_recipe_receives_gain_flags(monkeypatch, tmp_path, recipe_name):
    from pathlib import Path

    from examples.alohamini.am1_session_contract import ELBOW_RECOVERY_DEFINITIONS, RECIPES
    from lerobot.robots.alohamini.alohamini_client import AlohaMiniClient
    from tests.robots.test_am1_elbow_recovery import reference
    from tools import am1_physical_executor as physical

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    config = {
        "version": 1,
        "machine": "AM1",
        "host_head": "9" * 40,
        "camera_head": "b" * 40,
        "shoulder_amplitude": 1.5,
        "calibration_files": {"fixture": "c" * 64},
        "source_pins": {},
        "physical_state_directory": str(tmp_path / ".local/state/am1-session"),
        "run_root": str(tmp_path / "runs"),
    }
    for key in (
        "host_repository",
        "host_python",
        "camera_repository",
        "camera_python",
        "camera_config",
        "camera_credentials",
        "camera_binary",
        "camera_state",
    ):
        config[key] = str(tmp_path / key)
    launched = []

    class Unit:
        def __init__(self, name, repository, command, environment, *args, **kwargs):
            launched.append((name, dict(environment)))
            if "host" in name:
                directory = Path(command[command.index("--am1-protected-run-directory") + 1])
                (directory / "host-runtime.log").write_text('"operational_ready"')
                (directory / "observation.sock").touch()

        def poll(self):
            return None

    monkeypatch.setattr(physical, "OwnedUnit", Unit)
    monkeypatch.setattr(physical, "wait_required_camera", lambda *_: None)
    monkeypatch.setattr(physical.ProtectedHostRun, "_validate_source", lambda _: None)
    monkeypatch.setattr(physical, "load_elbow_return_reference", lambda *_, **__: reference())
    monkeypatch.setattr(AlohaMiniClient, "connect", lambda *_, **__: None)
    monkeypatch.setenv("AM1_ELBOW_RECOVERY", "I1")
    monkeypatch.setenv("AM1_ELBOW_RECOVERY_RECIPE", "physical-left-elbow-recovery-i1")
    monkeypatch.setenv("AM1_SHOULDER_INTEGRAL_TEST", "1")
    owner = physical.ProtectedHostRun(config)
    try:
        owner.start(str(uuid4()), str(uuid4()), RECIPES[recipe_name], lambda: None)
        env = next(env for name, env in launched if "host" in name)
        definition = ELBOW_RECOVERY_DEFINITIONS.get(recipe_name)
        if definition:
            assert env["AM1_ELBOW_RECOVERY"] == f"I{definition.integral}"
            assert env["AM1_ELBOW_RECOVERY_RECIPE"] == recipe_name
            assert env["AM1_SCRIPTED_PREPARE"] == env["AM1_SELECTED_JOINT_EVIDENCE"] == "1"
            assert "AM1_SHOULDER_INTEGRAL_TEST" not in env
        else:
            assert "AM1_ELBOW_RECOVERY" not in env and "AM1_ELBOW_RECOVERY_RECIPE" not in env
        assert all(
            "AM1_ELBOW_RECOVERY" not in e and "AM1_ELBOW_RECOVERY_RECIPE" not in e
            for name, e in launched
            if "camera" in name
        )
    finally:
        for stream in (owner.host_log, owner.camera_log):
            if stream is not None:
                stream.close()


def test_native_wrong_direction_remains_a_fault_while_ramp_goal_advances(monkeypatch):
    obj, control, evidence, reads, writes = guarded(monkeypatch)
    control.state = "active"
    evidence.context = {"cycle": 1, "phase": "return", "requested_target": 44.0}
    evidence.guard_recovery(obj, control, {})
    obj._am1_selected_joint_position["raw"] = 2462
    evidence.context["requested_target"] = 44.1
    evidence.guard_recovery(obj, control, {})
    obj._am1_selected_joint_position["raw"] = 2459
    evidence.context["requested_target"] = 44.2
    with pytest.raises(RuntimeError, match="reversed"):
        evidence.guard_recovery(obj, control, {})
    assert not reads and not writes


@pytest.mark.parametrize("progress,eligible", [(2, False), (3, True)])
def test_clear_three_tick_partial_recovery_is_distinct_from_old_benchmark(progress, eligible):
    from examples.alohamini.am1_session_contract import LEFT_ELBOW_RESPONSE, RECIPES
    from tools.am1_session_core import SessionAuthority

    authority = SessionAuthority.__new__(SessionAuthority)
    authority.run = native_i1_outcome(partial=progress)
    outcome = authority.run["execution"]["recovery"]
    assert outcome["max_toward_target_raw"] == progress and not outcome["target_attained"]
    assert LEFT_ELBOW_RESPONSE.response_threshold(33, 0) == 17
    evidence = {"lifecycle": copy.deepcopy(authority.run["physical_result"])}
    assert (
        authority.elbow_recovery_prerequisite(RECIPES["physical-left-elbow-recovery-i0-dwell"], evidence)
        is not None
    ) is eligible


def test_matched_i1_dwell_remains_admissible_after_static_fresh_i0_dwell():
    from examples.alohamini.am1_session_contract import RECIPES
    from tests.robots.test_am1_elbow_recovery import drive, execution, provider
    from tools.am1_session_core import SessionAuthority

    authority = SessionAuthority.__new__(SessionAuthority)
    authority.run = native_i1_outcome(partial=True)
    evidence = {"lifecycle": copy.deepcopy(authority.run["physical_result"])}
    prerequisite = authority.elbow_recovery_prerequisite(
        RECIPES["physical-left-elbow-recovery-i0-dwell"], evidence
    )
    assert prerequisite is not None
    p = provider(0, "-dwell", start=2465)
    drive(p, follows=False)
    result = execution(p)
    result.update(
        complete=True,
        preparing=False,
        admissions=1,
        feedback_sequence=41,
        initial_measured_reference=dict(p.origin),
    )
    run = prior_native_i0()
    result["recovery"]["gain_trial"]["binding"].update(
        run_id=run["run_id"], host_incarnation=run["backend_incarnation"]
    )
    run.update(recipe=p.definition.recipe_name, execution=result, recovery_prerequisite=prerequisite)
    run["completion_receipt"].update(recipe=p.definition.recipe_name, execution=copy.deepcopy(result))
    run["physical_result"]["readback"]["readback"]["elbow_integral"]["binding"] = copy.deepcopy(
        result["recovery"]["gain_trial"]["binding"]
    )
    authority.run = run
    assert result["recovery"]["max_toward_target_raw"] == 0 and not result["recovery"]["target_attained"]
    proof = authority.elbow_recovery_prerequisite(
        RECIPES["physical-left-elbow-recovery-i1-dwell"], {"lifecycle": copy.deepcopy(run["physical_result"])}
    )
    assert proof is not None and proof["initial_measured_reference"] == dict(p.origin)
    assert proof["trajectory_s"] == 7 and proof["hold_s"] == 2 and proof["integral"] == 0
