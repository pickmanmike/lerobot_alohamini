"""Recovery admission and sole-owner guards; injected caches only."""

import copy
from pathlib import Path
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


def reporting_authority(run, evidence):
    import threading
    from collections import deque

    from tools.am1_session_core import SessionAuthority

    authority = SessionAuthority.__new__(SessionAuthority)
    authority.run = copy.deepcopy(run)
    authority.run.setdefault("deadline", None)
    authority.run.setdefault("progress_s", (authority.run.get("execution") or {}).get("progress_s", 0.0))
    authority.mutex = threading.RLock()
    authority.clock = lambda: 100.0
    authority.service_incarnation = str(uuid4())
    authority.controller = None
    authority.events = deque(
        (
            {"sequence": i, "at": 90.0 + i / 100, "run_id": run["run_id"], "event": "snapshot"}
            for i in range(64)
        ),
        maxlen=64,
    )
    authority.executor = SimpleNamespace(source="protected-physical-provider", supports=lambda _: False)
    authority._evidence = lambda: copy.deepcopy(evidence)
    return authority


def reporting_run(integral=0, suffix=""):
    from tests.robots.test_am1_elbow_recovery import drive, execution, provider

    p = provider(integral, suffix)
    drive(p)
    run = prior_native_i0()
    result = execution(p)
    result["recovery"]["gain_trial"]["binding"].update(
        run_id=run["run_id"], host_incarnation=run["backend_incarnation"]
    )
    run.update(recipe=p.definition.recipe_name, execution=result)
    run["completion_receipt"].update(recipe=p.definition.recipe_name, execution=copy.deepcopy(result))
    gain = copy.deepcopy(result["recovery"]["gain_trial"])
    gain.update(
        schema_version=1,
        state="restored",
        status="restored",
        restore_required=False,
        restored_integral_readback=0,
        restored_lock_readback=gain["original_registers"]["Lock"],
        restored_bytes=copy.deepcopy(gain["original_bytes"]),
        restored_at_monotonic_s=19.0,
        first_failure=None,
        snapshot_path="/private/gain-journal.json",
        events=[
            {
                "operation": "read",
                "register": "I_Coefficient",
                "phase": "restored",
                "started_at_monotonic_s": 18.001 + i / 1000,
                "completed_at_monotonic_s": 18.0015 + i / 1000,
                "value": 0,
            }
            for i in range(69 + 4 * integral)
        ],
    )
    gain["original_registers"].update(
        Model_Number=777,
        Firmware_Major_Version=3,
        Firmware_Minor_Version=10,
        Operating_Mode=0,
        Goal_Position=2472,
        Present_Position=2458,
        Min_Position_Limit=864,
        Max_Position_Limit=3073,
    )
    gain.update(
        motor="arm_left_elbow_flex",
        motor_id=3,
        motor_model="sts3215",
        cached_calibration=copy.deepcopy(result["recovery"]["calibration"]),
    )
    run["physical_result"]["readback"]["readback"]["elbow_integral"] = gain
    for i, row in enumerate(run["physical_result"]["readback"]["readback"]["readbacks"]):
        row.update(
            read_started_at_monotonic_s=167106.201 + i / 1000,
            read_completed_at_monotonic_s=167106.2015 + i / 1000,
        )
    return run


@pytest.mark.parametrize("integral,suffix", [(0, ""), (1, ""), (0, "-confirm"), (1, "-confirm")])
def test_terminal_snapshot_bounds_only_duplicate_gain_ledger_and_preserves_full_native_run(integral, suffix):
    import json

    from tools.am1_session_ipc import CONTROL_LIMIT, RESPONSE_LIMIT, encode

    run = reporting_run(integral, suffix)
    original = copy.deepcopy(run)
    evidence = {
        "source": run["source"],
        "lifecycle": copy.deepcopy(run["physical_result"]),
        "finite_completion": copy.deepcopy(run["completion_receipt"]),
        "backend_contract": copy.deepcopy(run["backend_contract"]),
        "retained_return_reference": copy.deepcopy(run["retained_return_reference"]),
    }
    authority = reporting_authority(run, evidence)
    view = authority.snapshot()
    public = view["evidence"]["lifecycle"]["readback"]["readback"]
    gain = public["elbow_integral"]
    assert "events" not in gain and "snapshot_path" not in gain
    assert {
        k: v for k, v in view["run"].items() if k not in {"remaining_s", "trajectory_remaining_s"}
    } == authority.run
    assert authority.run == dict(original, deadline=None, progress_s=original["execution"]["progress_s"])
    assert authority._evidence() == evidence
    assert len(public["readbacks"]) == 20 and all(
        type(row["value"]) is int and row["value"] == 0 for row in public["readbacks"]
    )
    clocks = {"read_started_at_monotonic_s", "read_completed_at_monotonic_s"}
    original_rows = original["physical_result"]["readback"]["readback"]["readbacks"]
    assert public["readbacks"] == [{k: v for k, v in row.items() if k not in clocks} for row in original_rows]
    assert all(
        clocks <= set(row) for row in view["run"]["physical_result"]["readback"]["readback"]["readbacks"]
    )
    assert view["evidence"]["lifecycle"]["camera_release"] == original["physical_result"]["camera_release"]
    restored = original["physical_result"]["readback"]["readback"]["elbow_integral"]
    for key in (
        "state",
        "status",
        "restore_required",
        "integral",
        "binding",
        "original_registers",
        "original_bytes",
        "restored_bytes",
        "restored_integral_readback",
        "restored_lock_readback",
        "restored_at_monotonic_s",
        "first_failure",
    ):
        assert gain[key] == restored[key]
    framed = encode(
        {"id": str(uuid4()), "result": {"accepted": True, "status": "snapshot", "snapshot": view}}
    )
    assert len(framed) <= RESPONSE_LIMIT == 65536 and CONTROL_LIMIT == 4096
    if suffix == "-confirm":
        assert (
            view["run"]["execution"]["cycles_completed"] == view["run"]["execution"]["returns_qualified"] == 2
        )
    view["evidence"]["lifecycle"]["readback"]["readback"]["elbow_integral"]["original_registers"]["Lock"] = 9
    assert authority.run["physical_result"]["readback"]["readback"]["elbow_integral"] == restored
    assert json.dumps(authority._evidence(), sort_keys=True) == json.dumps(evidence, sort_keys=True)


def test_terminal_snapshot_preserves_restore_failure_and_ordinary_evidence():
    run = reporting_run()
    lifecycle = run["physical_result"]
    gain = lifecycle["readback"]["readback"]["elbow_integral"]
    failure = {"type": "TimeoutError", "message": "original restore read failed", "at_monotonic_s": 19.0}
    gain.update(state="restore_failed", status="restore_failed", restore_required=True, first_failure=failure)
    evidence = {"source": run["source"], "lifecycle": copy.deepcopy(lifecycle)}
    authority = reporting_authority(run, evidence)
    view = authority.snapshot()
    public = view["evidence"]["lifecycle"]["readback"]["readback"]["elbow_integral"]
    assert public["first_failure"] == failure and public["restore_required"] is True
    assert authority._evidence() == evidence
    evidence["lifecycle"]["readback"]["readback"].pop("elbow_integral")
    authority = reporting_authority(run, evidence)
    assert authority.snapshot()["evidence"] == evidence


def idle_recovery_evidence(run):
    return {
        "source": "protected-physical-provider",
        "backend_contract": copy.deepcopy(run["backend_contract"]),
        "retained_return_reference": copy.deepcopy(run["retained_return_reference"]),
        "lifecycle": {
            "run_id": None,
            "phase": "idle",
            "startup_deadline": None,
            "native_live_at": None,
            "cleanup": None,
            "uncertain": False,
            "terminal_status": None,
        },
        **dict.fromkeys(
            ("feedback", "native_ack", "active_acknowledged", "hold_acknowledged", "pose_aligned"), False
        ),
        **dict.fromkeys(
            (
                "fault",
                "transport_issue",
                "reconciliation",
                "host_incarnation",
                "finite_completion",
                "feedback_sequence",
                "measured_positions",
                "measured_body",
                "feedback_timing",
                "acknowledged_intent_revision",
                "recovery_prerequisite",
                "return_reference_error",
            )
        ),
        "accepted_host_action": {},
    }


def test_retained_completed_i0_qualifies_only_after_verified_idle_restart():
    from examples.alohamini.am1_session_contract import RECIPES

    run = prior_native_i0()
    run["service_incarnation"] = str(uuid4())
    run["intent"] = None
    evidence = idle_recovery_evidence(run)
    authority = reporting_authority(run, evidence)
    authority.executor.supports = lambda recipe: recipe.name == "physical-left-elbow-recovery-i1"
    before = copy.deepcopy(authority.run)
    proof = authority.elbow_recovery_prerequisite(RECIPES["physical-left-elbow-recovery-i1"], evidence)
    assert proof is not None and proof["run_id"] == run["run_id"]
    assert "physical-left-elbow-recovery-i1" in authority.snapshot()["recipes"]
    assert authority.run == before and authority._evidence() == evidence


@pytest.mark.parametrize(
    "fault",
    [
        "unknown",
        "foreign_run",
        "live",
        "ack",
        "feedback",
        "host",
        "fault",
        "transport",
        "reconciliation",
        "completion",
        "accepted_action",
        "config",
        "reference",
        "same_service",
        "unknown_service",
        "intent",
        "restored_i",
        "restored_lock",
        "restore_required",
        "untyped_idle",
        "missing_idle_field",
        "typed_config",
    ],
)
def test_retained_recovery_restart_refuses_unknown_active_foreign_or_changed_proof(fault):
    from examples.alohamini.am1_session_contract import RECIPES

    run = prior_native_i0()
    run["service_incarnation"] = str(uuid4())
    run["intent"] = None
    evidence = idle_recovery_evidence(run)
    authority = reporting_authority(run, evidence)
    if fault == "untyped_idle":
        evidence["lifecycle"]["uncertain"] = 0
    elif fault == "missing_idle_field":
        evidence.pop("measured_positions")
    elif fault == "typed_config":
        authority.run["backend_contract"]["version"] = 1
        evidence["backend_contract"]["version"] = True
    elif fault == "unknown":
        evidence["lifecycle"]["uncertain"] = True
    elif fault == "foreign_run":
        evidence["lifecycle"]["run_id"] = str(uuid4())
    elif fault == "live":
        evidence["lifecycle"]["phase"] = "live"
    elif fault == "ack":
        evidence["active_acknowledged"] = True
    elif fault == "feedback":
        evidence["feedback"] = True
    elif fault == "host":
        evidence["host_incarnation"] = str(uuid4())
    elif fault == "fault":
        evidence["fault"] = "original native fault"
    elif fault == "transport":
        evidence["transport_issue"] = "feedback unavailable"
    elif fault == "reconciliation":
        evidence["reconciliation"] = {"state": "unknown"}
    elif fault == "completion":
        evidence["finite_completion"] = copy.deepcopy(run["completion_receipt"])
    elif fault == "accepted_action":
        evidence["accepted_host_action"] = {"state": "active"}
    elif fault == "config":
        evidence["backend_contract"]["host_head"] = "b" * 40
    elif fault == "reference":
        evidence["retained_return_reference"]["reference_raw"] += 1
    elif fault == "same_service":
        authority.service_incarnation = run["service_incarnation"]
    elif fault == "unknown_service":
        authority.service_incarnation = None
    elif fault == "intent":
        authority.run["intent"] = {"state": "active"}
    else:
        gain = authority.run["physical_result"]["readback"]["readback"]["elbow_integral"]
        if fault == "restored_i":
            gain["restored_integral_readback"] = 1
        elif fault == "restored_lock":
            gain["restored_lock_readback"] = 1
        else:
            gain["restore_required"] = True
    assert authority.elbow_recovery_prerequisite(RECIPES["physical-left-elbow-recovery-i1"], evidence) is None


def startup_continuation_fixture(tmp_path):
    import hashlib

    from tests.robots.test_am1_physical_executor import terminal_cleanup_outcome
    from tools.am1_session_core import _canonical

    def private(path, value):
        path.write_bytes(_canonical(value))
        path.chmod(0o600)

    prior = prior_native_i0()
    ref = copy.deepcopy(prior["retained_return_reference"])
    before_ref = tmp_path / "before-reference.json"
    private(before_ref, ref)
    ref_sha = hashlib.sha256(before_ref.read_bytes()).hexdigest()
    prior["backend_contract"]["elbow_return_reference"] = {"path": str(before_ref), "sha256": ref_sha}
    prior["execution"]["recovery"]["artifact_sha256"] = ref_sha
    prior["completion_receipt"]["execution"] = copy.deepcopy(prior["execution"])
    prior.update(service_incarnation=str(uuid4()), intent=None, deadline=60.0, progress_s=5.5)
    paths = [
        "src/lerobot/robots/alohamini/" + name
        for name in (
            "alohamini.py",
            "alohamini_client.py",
            "alohamini_host.py",
            "config_alohamini.py",
            "lift_motor_feedback.py",
            "lift_operational.py",
            "lift_relief.py",
            "selected_joint_evidence.py",
            "am1_elbow_integral.py",
        )
    ] + ["tools/run_am1_host.sh", "examples/alohamini/am1_session_contract.py"]
    motor = tmp_path / "motor"
    changed = {}
    old_rows = []
    new_rows = []
    for path in paths:
        before = hashlib.sha256(("before " + path).encode()).hexdigest()
        is_changed = path.endswith(("/alohamini.py", "/alohamini_host.py"))
        data = ("after " if is_changed else "before ") + path
        target = motor / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(data)
        target.chmod(0o644)
        after = hashlib.sha256(target.read_bytes()).hexdigest()
        old_rows.append({"path": path, "sha256": before})
        new_rows.append({"path": path, "sha256": after})
        if is_changed:
            changed[path] = {"before_sha256": before, "after_sha256": after}

    def manifest(head, rows, bundle):
        return {
            "version": 2,
            "motor": {
                "base": "1" * 40,
                "head": head,
                "ref": "refs/heads/reviewed",
                "files": rows,
                "bundle_sha256": bundle,
            },
            "helper": {"head": "2" * 40},
            "camera": {"head": "3" * 40},
            "calibration_sha256": "4" * 64,
            "lift_delta_files": [],
            "executor_base": "5" * 40,
            "executor_head": "6" * 40,
            "executor_bundle_sha256": "7" * 64,
        }

    before_manifest = tmp_path / "before-manifest.json"
    after_manifest = tmp_path / "after-manifest.json"
    private(before_manifest, manifest(prior["backend_contract"]["host_head"], old_rows, "8" * 64))
    private(after_manifest, manifest("a" * 40, new_rows, "9" * 64))
    prior["backend_contract"].update(
        source_pins=str(before_manifest), host_repository=str(motor), camera_state=str(tmp_path / "camera")
    )
    failed = copy.deepcopy(prior)
    failed.update(
        run_id=str(uuid4()),
        recipe="physical-left-elbow-recovery-i1",
        status="faulted",
        progress_s=0,
        deadline=None,
        native_live_at=None,
        first_cause="startup timeout",
        native_first_cause="ComparisonRefusal: temperature: five-slot feedback window is stale (>0.5 s).",
        recovery_episodes=0,
    )
    failed.pop("execution")
    failed.pop("completion_receipt")
    proof_authority = reporting_authority(prior, {"lifecycle": copy.deepcopy(prior["physical_result"])})
    from examples.alohamini.am1_session_contract import RECIPES

    failed["recovery_prerequisite"] = proof_authority.elbow_recovery_prerequisite(
        RECIPES[failed["recipe"]], proof_authority._evidence()
    )
    incarnation = str(uuid4())
    life = terminal_cleanup_outcome(failed["run_id"], incarnation, "faulted")
    life.update(
        run_id=failed["run_id"],
        phase="terminal",
        native_live_at=None,
        first_cause=failed["native_first_cause"],
        run_directory=str(tmp_path / "run"),
    )
    gain = copy.deepcopy(prior["execution"]["recovery"]["gain_trial"])
    gain.update(
        state="restored",
        status="restored",
        integral=1,
        restore_required=False,
        configured_integral_readback=1,
        active_verified=True,
        restored_integral_readback=0,
        restored_lock_readback=0,
        restored_bytes=copy.deepcopy(gain["original_bytes"]),
        first_failure=None,
    )
    gain["binding"] = {
        "run_id": failed["run_id"],
        "host_incarnation": incarnation,
        "recipe": failed["recipe"],
        "integral": 1,
    }
    gain["original_registers"].update(
        Model_Number=777, Firmware_Major_Version=3, Firmware_Minor_Version=10, Operating_Mode=0
    )
    gain["active_registers"]["I_Coefficient"] = 1
    life["readback"]["readback"]["elbow_integral"] = gain
    failed["physical_result"] = life
    run_dir = Path(life["run_directory"])
    run_dir.mkdir()
    private(run_dir / "cleanup.json", life["readback"])
    camera_dir = tmp_path / "camera"
    camera_dir.mkdir()
    camera_file = camera_dir / ("release-" + failed["run_id"] + ".json")
    private(camera_file, life["camera_release"])
    after = copy.deepcopy(prior["backend_contract"])
    after.update(host_head="a" * 40, source_pins=str(after_manifest))
    after_ref = copy.deepcopy(ref)
    after_ref["admission_motor_head"] = "a" * 40
    ref_file = tmp_path / "after-reference.json"
    private(ref_file, after_ref)
    after["elbow_return_reference"] = {
        "path": str(ref_file),
        "sha256": hashlib.sha256(ref_file.read_bytes()).hexdigest(),
    }
    record = {
        "version": 1,
        "kind": "am1-elbow-startup-continuation",
        "failed_run_id": failed["run_id"],
        "failed_terminal_sha256": hashlib.sha256(_canonical(failed)).hexdigest(),
        "failed_cleanup_sha256": hashlib.sha256((run_dir / "cleanup.json").read_bytes()).hexdigest(),
        "failed_camera_sha256": hashlib.sha256(camera_file.read_bytes()).hexdigest(),
        "prior_run": prior,
        "source_transition": {
            "before": copy.deepcopy(prior["backend_contract"]),
            "after": after,
            "before_reference": ref,
            "after_reference": after_ref,
            "changed_motor_files": changed,
        },
    }
    evidence = idle_recovery_evidence(
        dict(failed, backend_contract=after, retained_return_reference=after_ref)
    )
    authority = reporting_authority(failed, evidence)
    authority.directory = tmp_path
    receipt = tmp_path / "elbow-recovery-startup-continuation.json"
    private(receipt, record)
    return authority, evidence, record, receipt, after_manifest


def test_owned_startup_continuation_revalidates_full_private_proof_without_public_duplication(tmp_path):
    from examples.alohamini.am1_session_contract import RECIPES
    from tools.am1_session_ipc import encode

    authority, evidence, record, _, _ = startup_continuation_fixture(tmp_path)
    original = copy.deepcopy(authority.run)
    proof = authority.elbow_recovery_prerequisite(RECIPES[authority.run["recipe"]], evidence)
    assert proof == authority.run["recovery_prerequisite"] and authority.run == original
    assert set(authority._startup_continuation_note) == {"failed_run_id", "prior_run_id", "receipt_sha256"}
    assert "prior_run" not in authority.snapshot()["evidence"] and authority.run == original
    assert (
        len(
            encode(
                {
                    "id": str(uuid4()),
                    "result": {"accepted": True, "status": "snapshot", "snapshot": authority.snapshot()},
                }
            )
        )
        <= 65536
    )


@pytest.mark.parametrize(
    "bad",
    [
        "duplicate_manifest",
        "missing_manifest",
        "extra_manifest",
        "other_file",
        "camera_material",
        "gain_bytes_missing",
        "gain_pid",
        "gain_identity",
        "configured_i",
        "active_unverified",
    ],
)
def test_startup_continuation_requires_closed_source_shape_and_actual_gain_restore(tmp_path, bad):
    import hashlib
    import json

    from examples.alohamini.am1_session_contract import RECIPES
    from tools.am1_session_core import _canonical

    authority, evidence, record, receipt, manifest_path = startup_continuation_fixture(tmp_path)
    if bad in {"duplicate_manifest", "missing_manifest", "extra_manifest", "other_file", "camera_material"}:
        manifest = json.loads(manifest_path.read_bytes())
        if bad == "duplicate_manifest":
            manifest["motor"]["files"].append(copy.deepcopy(manifest["motor"]["files"][0]))
        elif bad == "missing_manifest":
            manifest["motor"]["files"].pop()
        elif bad == "extra_manifest":
            manifest["unexpected"] = True
        elif bad == "other_file":
            manifest["motor"]["files"][1]["sha256"] = "e" * 64
        else:
            manifest["camera"]["head"] = "f" * 40
        manifest_path.write_bytes(_canonical(manifest))
    else:
        gain = authority.run["physical_result"]["readback"]["readback"]["elbow_integral"]
        if bad == "gain_bytes_missing":
            gain.pop("original_bytes")
            gain.pop("restored_bytes")
        elif bad == "gain_pid":
            gain["original_registers"]["P_Coefficient"] = 32
        elif bad == "gain_identity":
            gain["original_registers"]["Model_Number"] = 778
        elif bad == "configured_i":
            gain["configured_integral_readback"] = 0
        else:
            gain["active_verified"] = False
        record["failed_terminal_sha256"] = hashlib.sha256(_canonical(authority.run)).hexdigest()
        cleanup = Path(authority.run["physical_result"]["run_directory"]) / "cleanup.json"
        cleanup.write_bytes(_canonical(authority.run["physical_result"]["readback"]))
        record["failed_cleanup_sha256"] = hashlib.sha256(cleanup.read_bytes()).hexdigest()
        receipt.write_bytes(_canonical(record))
    original = copy.deepcopy(authority.run)
    assert authority.elbow_recovery_prerequisite(RECIPES[authority.run["recipe"]], evidence) is None
    assert authority.run == original


@pytest.mark.parametrize(
    "bad",
    ["live", "progress", "unknown", "native_cause", "prior", "comparator", "active_owner", "foreign_owner"],
)
def test_startup_continuation_keeps_zero_live_fault_and_fresh_idle_boundaries(tmp_path, bad):
    import hashlib

    from examples.alohamini.am1_session_contract import RECIPES
    from tools.am1_session_core import _canonical

    authority, evidence, record, receipt, _ = startup_continuation_fixture(tmp_path)
    if bad == "live":
        authority.run["native_live_at"] = 1.0
    elif bad == "progress":
        authority.run["progress_s"] = 0.1
    elif bad == "unknown":
        authority.run["uncertain"] = True
    elif bad == "native_cause":
        authority.run["native_first_cause"] = "Different startup fault"
    elif bad == "prior":
        record["prior_run"]["first_cause"] = "altered original"
    elif bad == "comparator":
        authority.run["recovery_prerequisite"]["hold_s"] = 2.0
    elif bad == "active_owner":
        evidence["active_acknowledged"] = True
    else:
        evidence["lifecycle"]["service_incarnation"] = str(uuid4())
    record["failed_terminal_sha256"] = hashlib.sha256(_canonical(authority.run)).hexdigest()
    receipt.write_bytes(_canonical(record))
    original = copy.deepcopy(authority.run)
    assert authority.elbow_recovery_prerequisite(RECIPES[authority.run["recipe"]], evidence) is None
    assert authority.run == original


def linked_startup_continuation_fixture(tmp_path):
    import hashlib
    import json

    from tests.robots.test_am1_physical_executor import terminal_cleanup_outcome
    from tools.am1_session_core import _canonical

    authority, _, first, receipt, intermediate_manifest = startup_continuation_fixture(tmp_path)
    old_raw = receipt.read_bytes()
    old_sha = hashlib.sha256(old_raw).hexdigest()
    archive = tmp_path / ("elbow-recovery-startup-continuation." + old_sha + ".json")
    archive.write_bytes(old_raw)
    archive.chmod(0o600)
    run = copy.deepcopy(authority.run)
    run.update(
        run_id=str(uuid4()),
        service_incarnation=str(uuid4()),
        backend_contract=copy.deepcopy(first["source_transition"]["after"]),
        retained_return_reference=copy.deepcopy(first["source_transition"]["after_reference"]),
    )
    run["startup_continuation"] = {
        "failed_run_id": first["failed_run_id"],
        "prior_run_id": first["prior_run"]["run_id"],
        "receipt_sha256": old_sha,
    }
    incarnation = str(uuid4())
    life = terminal_cleanup_outcome(run["run_id"], incarnation, "faulted")
    life.update(
        run_id=run["run_id"],
        phase="terminal",
        native_live_at=None,
        first_cause=run["native_first_cause"],
        run_directory=str(tmp_path / "second-run"),
    )
    gain = copy.deepcopy(authority.run["physical_result"]["readback"]["readback"]["elbow_integral"])
    gain["binding"] = {
        "run_id": run["run_id"],
        "host_incarnation": incarnation,
        "recipe": run["recipe"],
        "integral": 1,
    }
    for key in ("active_registers", "active_bytes", "active_verified", "active_verified_at_monotonic_s"):
        gain.pop(key, None)
    gain["before_apply_bytes"] = copy.deepcopy(gain["original_bytes"])
    original = gain["original_registers"]
    original.update(Min_Position_Limit=864, Max_Position_Limit=3073, Torque_Enable=0)
    events = []

    def event(phase, register=None, value=None, address=None):
        start = 1.0 + len(events) * 0.01
        row = {
            "phase": phase,
            "operation": "read",
            "value": value,
            "started_at_monotonic_s": start,
            "completed_at_monotonic_s": start + 0.005,
        }
        if register is not None:
            row["register"] = register
        else:
            row.update(address=address, width=1)
        events.append(row)

    for phase, fields in (
        ("baseline", original),
        (
            "before_apply",
            {k: v for k, v in original.items() if k not in {"Present_Position", "Goal_Position"}},
        ),
    ):
        for key, value in fields.items():
            event(phase, key, 1 if phase == "before_apply" and key == "Lock" else value)
        for address, value in gain["original_bytes"].items():
            event(phase, value=value, address=int(address))
    event("configured", "I_Coefficient", 1)
    event("restored", "I_Coefficient", 0)
    for key, value in original.items():
        if key not in {"Present_Position", "Goal_Position", "I_Coefficient", "Lock", "Torque_Enable"}:
            event("restored", key, value)
    for address, value in gain["original_bytes"].items():
        event("restored", value=value, address=int(address))
    event("restore_original_lock_verify", "Lock", 0)
    gain["events"] = events
    gain["restored_at_monotonic_s"] = events[-1]["completed_at_monotonic_s"] + 0.001
    life["readback"]["readback"]["elbow_integral"] = gain
    run["physical_result"] = life
    run_dir = Path(life["run_directory"])
    run_dir.mkdir()
    cleanup = run_dir / "cleanup.json"
    cleanup.write_bytes(_canonical(life["readback"]))
    cleanup.chmod(0o600)
    camera = Path(run["backend_contract"]["camera_state"]) / ("release-" + run["run_id"] + ".json")
    camera.write_bytes(_canonical(life["camera_release"]))
    camera.chmod(0o600)
    final_manifest = json.loads(intermediate_manifest.read_bytes())
    final_manifest["motor"].update(head="b" * 40, bundle_sha256="c" * 64)
    delta = "src/lerobot/robots/alohamini/alohamini.py"
    target = Path(run["backend_contract"]["host_repository"]) / delta
    target.write_bytes(b"final reviewed acquisition repair")
    row = next(r for r in final_manifest["motor"]["files"] if r["path"] == delta)
    before_sha = row["sha256"]
    row["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    final_path = tmp_path / "final-manifest.json"
    final_path.write_bytes(_canonical(final_manifest))
    final_path.chmod(0o600)
    before = copy.deepcopy(run["backend_contract"])
    after = copy.deepcopy(before)
    after.update(host_head="b" * 40, source_pins=str(final_path))
    reference = copy.deepcopy(run["retained_return_reference"])
    after_reference = dict(reference, admission_motor_head="b" * 40)
    ref_path = tmp_path / "final-reference.json"
    ref_path.write_bytes(_canonical(after_reference))
    ref_path.chmod(0o600)
    after["elbow_return_reference"] = {
        "path": str(ref_path),
        "sha256": hashlib.sha256(ref_path.read_bytes()).hexdigest(),
    }
    record = {
        "version": 1,
        "kind": "am1-elbow-startup-continuation",
        "failed_run_id": run["run_id"],
        "failed_terminal_sha256": hashlib.sha256(_canonical(run)).hexdigest(),
        "failed_cleanup_sha256": hashlib.sha256(cleanup.read_bytes()).hexdigest(),
        "failed_camera_sha256": hashlib.sha256(camera.read_bytes()).hexdigest(),
        "prior_run": copy.deepcopy(first["prior_run"]),
        "source_transition": {
            "before": before,
            "after": after,
            "before_reference": reference,
            "after_reference": after_reference,
            "changed_motor_files": {delta: {"before_sha256": before_sha, "after_sha256": row["sha256"]}},
        },
    }
    receipt.write_bytes(_canonical(record))
    evidence = idle_recovery_evidence(
        dict(run, backend_contract=after, retained_return_reference=after_reference)
    )
    authority = reporting_authority(run, evidence)
    authority.directory = tmp_path
    return authority, evidence, record, receipt, archive, final_path


def test_linked_configured_only_startup_reuses_exact_prior_after_two_source_steps(tmp_path):
    from examples.alohamini.am1_session_contract import RECIPES

    authority, evidence, _, _, _, _ = linked_startup_continuation_fixture(tmp_path)
    original = copy.deepcopy(authority.run)
    proof = authority.elbow_recovery_prerequisite(RECIPES[authority.run["recipe"]], evidence)
    assert proof == authority.run["recovery_prerequisite"] and authority.run == original
    assert set(authority._startup_continuation_note) == {"failed_run_id", "prior_run_id", "receipt_sha256"}


@pytest.mark.parametrize(
    "bad",
    [
        "missing_archive",
        "tampered_archive",
        "foreign_note",
        "recursive",
        "hardlink",
        "active_false",
        "active_phase",
        "stage_order",
        "configured",
        "configured_event",
        "clock",
        "gain_failure",
        "original_pid",
        "original_torque",
        "restore_bytes",
        "byte_event",
        "intermediate_source",
        "foreign_delta",
        "final_material",
        "final_file",
        "live",
        "execution",
        "unknown",
        "camera",
        "active_owner",
    ],
)
def test_linked_startup_refuses_missing_or_changed_source_stage_cleanup_and_archive(tmp_path, bad):
    import hashlib
    import json
    import os

    from examples.alohamini.am1_session_contract import RECIPES
    from tools.am1_session_core import _canonical

    authority, evidence, record, receipt, archive, manifest = linked_startup_continuation_fixture(tmp_path)
    gain = authority.run["physical_result"]["readback"]["readback"]["elbow_integral"]
    if bad == "missing_archive":
        archive.unlink()
    elif bad == "tampered_archive":
        archive.write_bytes(archive.read_bytes() + b" ")
    elif bad == "foreign_note":
        authority.run["startup_continuation"]["failed_run_id"] = str(uuid4())
    elif bad == "recursive":
        old = json.loads(archive.read_bytes())
        old["source_transition"]["before"] = copy.deepcopy(authority.run["backend_contract"])
        raw = _canonical(old)
        digest = hashlib.sha256(raw).hexdigest()
        new_archive = archive.with_name("elbow-recovery-startup-continuation." + digest + ".json")
        new_archive.write_bytes(raw)
        new_archive.chmod(0o600)
        authority.run["startup_continuation"]["receipt_sha256"] = digest
    elif bad == "hardlink":
        os.link(archive, tmp_path / "alias.json")
    elif bad == "active_false":
        gain["active_verified"] = False
    elif bad == "active_phase":
        gain["events"][-1]["phase"] = "active"
    elif bad == "stage_order":
        event = next(r for r in gain["events"] if r["phase"] == "configured")
        gain["events"].remove(event)
        gain["events"].append(event)
        for i, row in enumerate(gain["events"]):
            row.update(started_at_monotonic_s=1.0 + i * 0.01, completed_at_monotonic_s=1.005 + i * 0.01)
        gain["restored_at_monotonic_s"] = gain["events"][-1]["completed_at_monotonic_s"] + 0.001
    elif bad == "configured":
        gain["configured_integral_readback"] = 0
    elif bad == "configured_event":
        gain["events"] = [r for r in gain["events"] if r["phase"] != "configured"]
    elif bad == "clock":
        gain["events"][-1]["started_at_monotonic_s"] = 0.0
    elif bad == "gain_failure":
        gain["first_failure"] = {"type": "RuntimeError", "message": "gain mismatch"}
    elif bad == "original_pid":
        gain["original_registers"]["P_Coefficient"] = 32
    elif bad == "original_torque":
        gain["original_registers"]["Torque_Enable"] = 1
    elif bad == "restore_bytes":
        gain["restored_bytes"]["25"] = 1
    elif bad == "byte_event":
        gain["events"] = [
            r for r in gain["events"] if not (r["phase"] == "restored" and r.get("address") == 25)
        ]
    elif bad == "intermediate_source":
        authority.run["backend_contract"]["host_head"] = "e" * 40
        record["source_transition"]["before"] = copy.deepcopy(authority.run["backend_contract"])
    elif bad in {"foreign_delta", "final_material"}:
        value = json.loads(manifest.read_bytes())
        if bad == "foreign_delta":
            next(r for r in value["motor"]["files"] if r["path"].endswith("alohamini_client.py"))[
                "sha256"
            ] = "e" * 64
        else:
            value["camera"]["head"] = "e" * 40
        manifest.write_bytes(_canonical(value))
    elif bad == "final_file":
        (
            Path(authority.run["backend_contract"]["host_repository"])
            / "src/lerobot/robots/alohamini/alohamini_client.py"
        ).write_bytes(b"foreign actual bytes")
    elif bad == "live":
        authority.run["native_live_at"] = 1.0
    elif bad == "execution":
        authority.run["execution"] = {"progress_s": 0}
    elif bad == "unknown":
        authority.run["uncertain"] = True
    elif bad == "camera":
        authority.run["physical_result"]["camera_release"]["session_id"] = str(uuid4())
    else:
        evidence["active_acknowledged"] = True
    cleanup = Path(authority.run["physical_result"]["run_directory"]) / "cleanup.json"
    cleanup.write_bytes(_canonical(authority.run["physical_result"]["readback"]))
    record["failed_cleanup_sha256"] = hashlib.sha256(cleanup.read_bytes()).hexdigest()
    camera = Path(authority.run["backend_contract"]["camera_state"]) / (
        "release-" + authority.run["run_id"] + ".json"
    )
    camera.write_bytes(_canonical(authority.run["physical_result"]["camera_release"]))
    record["failed_camera_sha256"] = hashlib.sha256(camera.read_bytes()).hexdigest()
    record["failed_terminal_sha256"] = hashlib.sha256(_canonical(authority.run)).hexdigest()
    receipt.write_bytes(_canonical(record))
    original = copy.deepcopy(authority.run)
    assert authority.elbow_recovery_prerequisite(RECIPES[authority.run["recipe"]], evidence) is None
    assert authority.run == original


@pytest.mark.parametrize(
    "mode,source,allowed",
    [
        (0o700, True, True),
        (0o700, False, False),
        (0o720, True, False),
        (0o660, True, False),
        (0o600, False, True),
        (0o644, True, True),
        (0o644, False, False),
    ],
)
def test_continuation_reader_preserves_posix_private_modes_with_owned_executable_source(
    tmp_path, monkeypatch, mode, source, allowed
):
    import os
    import stat

    import tools.am1_session_core as core

    path = tmp_path / "owned-file"
    data = b"unchanged owned executable source or private proof"
    path.write_bytes(data)
    actual_os = os

    def fstat(fd):
        row = actual_os.fstat(fd)
        fields = {
            k: getattr(row, k)
            for k in ("st_dev", "st_ino", "st_nlink", "st_uid", "st_size", "st_mtime_ns", "st_ctime_ns")
        }
        return SimpleNamespace(**fields, st_mode=stat.S_IFREG | mode)

    # Windows does not represent POSIX execute/privacy bits. Inject ONLY fstat
    # mode and the POSIX policy selector; actual opened bytes/path/link/UID and
    # bounded-read checks still run. Native runs use the same portable contrast.
    policy_os = SimpleNamespace(**vars(actual_os))
    policy_os.name = "posix"
    policy_os.fstat = fstat
    policy_os.getuid = lambda: path.stat().st_uid
    monkeypatch.setattr(core, "os", policy_os)
    if allowed:
        assert core._continuation_bytes(path, source=source) == data
    else:
        with pytest.raises(ValueError, match="owned bounded"):
            core._continuation_bytes(path, source=source)
    assert path.read_bytes() == data
