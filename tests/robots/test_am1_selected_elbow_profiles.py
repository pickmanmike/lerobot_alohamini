"""Explicit selected elbow timing and protected dispatch; no device access."""

from types import SimpleNamespace

import pytest

from examples.alohamini import scripted_leader
from examples.alohamini.am1_finite_task import JOINT_KEYS, FiniteTask
from examples.alohamini.am1_session_contract import RECIPES
from tests.robots.test_am1_persistent_session import Clock
from tests.robots.test_am1_physical_executor import ControlledHost, executor, observation, until
from tools import am1_physical_executor as physical

ELBOW = "arm_left_elbow_flex.pos"
CASES = [
    ("physical-left-elbow-compare", "LeftElbowCompareInput", "LeftElbowCompare", 18, 0.5),
    ("physical-left-elbow-dwell-compare", "LeftElbowDwellCompareInput", "LeftElbowDwellCompare", 24, 2),
]


def seed(elbow=39.0):
    result = {key: float(index + 10) for index, key in enumerate(JOINT_KEYS)}
    result[ELBOW] = elbow
    return result


def selected(class_name, elbow=39.0):
    factory = getattr(scripted_leader, class_name, None)
    assert factory is not None, "selected finite provider is missing"
    return factory(seed(elbow), joint_keys=JOINT_KEYS, fps=10, emit=lambda row: None)


def advance_to(provider, progress):
    provider.admit(0)
    for sequence in range(1, round(progress * 10) + 1):
        # Feedback changes deliberately: it must never reseed the requested program.
        provider.advance(sequence / 10, seed(40), sequence)


@pytest.mark.parametrize("recipe_name,class_name,profile,duration,hold", CASES)
def test_selected_recipe_dispatch_keeps_real_seed_and_finite_budget(
    recipe_name, class_name, profile, duration, hold
):
    recipe = RECIPES.get(recipe_name)
    assert recipe is not None, "selected physical recipe is missing"
    e = executor(ControlledHost())
    assert e.supports(recipe) and recipe.seed is None and recipe.live_s == 60
    assert recipe.trajectory_s == duration
    task = FiniteTask(recipe, seed(), 0, 1.5, start_held=True)
    assert task.snapshot()["profile"] == profile and task.provider.duration_s == duration
    assert task.snapshot()["initial_measured_reference"] == seed()
    assert task.provider.diagnostic_context is None  # preparation has not seeded the diagnostic yet
    assert not e.supports(SimpleNamespace(name=recipe_name, seed=17, mode="finite"))
    from tools.am1_pi_executor import PiExecutor

    assert recipe_name not in PiExecutor.recipes


@pytest.mark.parametrize("recipe_name,class_name,profile,duration,hold", CASES)
@pytest.mark.parametrize("elbow,endpoint", [(39.0, 42.0), (97.0, 100.0), (99.0, 96.0)])
def test_selected_cycles_hold_other_joints_and_reuse_frozen_endpoint(
    recipe_name, class_name, profile, duration, hold, elbow, endpoint
):
    p = selected(class_name, elbow)
    advance_to(p, 5)
    assert p.get_action()["left_elbow_flex.pos"] == endpoint
    assert p.diagnostic_context == {
        "profile": profile,
        "progress_s": 5.0,
        "phase": "endpoint",
        "cycle": 1,
        "reference": elbow,
        "endpoint_target": endpoint,
        "requested_target": endpoint,
        "sample_key": "cycle1_endpoint",
    }
    for key, value in seed(elbow).items():
        if key != ELBOW:
            assert p.get_action()[key.removeprefix("arm_")] == value
    period = 6 + 2 * hold
    for step in range(51, round((5 + period) * 10) + 1):
        p.advance(step / 10, seed(40), step)
    assert p.diagnostic_context["cycle"] == 2
    assert p.diagnostic_context["sample_key"] == "cycle2_endpoint"
    assert p.get_action()["left_elbow_flex.pos"] == endpoint
    assert p.origin == seed(elbow)


@pytest.mark.parametrize("recipe_name,class_name,profile,duration,hold", CASES)
def test_selected_phase_capture_keys_and_origin_return(recipe_name, class_name, profile, duration, hold):
    p = selected(class_name)
    expected = {
        0: ("baseline", 0, "baseline", 39),
        2: ("outbound", 1, None, 39),
        3.5: ("outbound", 1, None, 40.5),
        5: ("endpoint", 1, "cycle1_endpoint", 42),
        5 + hold: ("return", 1, None, 42),
        8 + hold: ("origin", 1, "cycle1_origin", 39),
        8 + 2 * hold: ("outbound", 2, None, 39),
        11 + 2 * hold: ("endpoint", 2, "cycle2_endpoint", 42),
        14 + 3 * hold: ("origin", 2, "cycle2_origin", 39),
        duration - 2: ("final", 2, None, 39),
        duration: ("final", 2, None, 39),
    }
    p.admit(0)
    for step in range(round(duration * 10) + 1):
        if step:
            p.advance(step / 10, seed(40), step)
        progress = step / 10
        if progress in expected:
            phase, cycle, capture, goal = expected[progress]
            context = p.diagnostic_context
            assert (context["phase"], context["cycle"], context["sample_key"]) == (phase, cycle, capture)
            assert context["requested_target"] == pytest.approx(goal)
    assert p.complete and p.get_action() == {k.removeprefix("arm_"): v for k, v in seed().items()}


def test_selected_pause_discards_elapsed_time_and_repeated_feedback():
    p = selected("LeftElbowCompareInput")
    advance_to(p, 3)
    frozen = p.get_action()
    p.freeze()
    p.advance(100, seed(40), 31)
    assert p.elapsed_s == 3 and p.get_action() == frozen
    p.admit(101)
    p.advance(108, seed(40), 32)
    assert p.elapsed_s == 3.1
    with pytest.raises(ValueError, match="sequence"):
        p.advance(108.1, seed(40), 32)


def test_selected_rejects_incomplete_seed_and_amplitude_override():
    factory = getattr(scripted_leader, "LeftElbowCompareInput", None)
    assert factory is not None, "selected finite provider is missing"
    with pytest.raises(ValueError, match="exact"):
        factory({ELBOW: 39}, joint_keys=JOINT_KEYS, fps=10)
    with pytest.raises(ValueError, match="amplitude"):
        factory(seed(), joint_keys=JOINT_KEYS, fps=10, joint_amplitudes={ELBOW: 2})


@pytest.mark.parametrize("recipe_name,class_name,profile,duration,hold", CASES)
def test_selected_task_uses_fresh_preparation_and_actual_final_return(
    recipe_name, class_name, profile, duration, hold
):
    recipe = RECIPES.get(recipe_name)
    assert recipe is not None, "selected physical recipe is missing"
    initial = seed()
    initial["arm_left_shoulder_lift.pos"] = 105
    task = FiniteTask(recipe, initial, 0, 1.5)
    sequence = 0
    while task.provider.preparing:
        sequence += 1
        measured = {"arm_" + k: v for k, v in task.provider.get_action().items()}
        task.advance(sequence / 10, {"positions": measured, "sequence": sequence})
        assert task.provider.elapsed_s == 0
        assert sequence < 100
    reference = dict(task.original_seed)
    assert reference["arm_left_shoulder_lift.pos"] == 99
    assert task.provider.diagnostic_context["reference"] == 39
    while task.provider.elapsed_s < duration:
        sequence += 1
        task.advance(sequence / 10, {"positions": reference, "sequence": sequence})
    assert not task.complete
    for _ in range(2):
        sequence += 1
        task.advance(sequence / 10, {"positions": reference, "sequence": sequence})
    assert task.complete and task.snapshot()["cycles_completed"] == 2
    assert task.snapshot()["returns_qualified"] == 1 and task.snapshot()["admissions"] == 1


@pytest.mark.parametrize("recipe_name,class_name,profile,duration,hold", CASES)
def test_real_physical_worker_emits_selected_marker_only_after_preparation(
    recipe_name, class_name, profile, duration, hold
):
    recipe = RECIPES.get(recipe_name)
    assert recipe is not None, "selected physical recipe is missing"
    clock = Clock()

    class SteppedHost(ControlledHost):
        def poll(self, previous):
            clock.advance(0.1)
            sample = super().poll(previous)
            sample["at"] = clock()
            return sample

    host = SteppedHost()
    e = physical.PhysicalExecutor(
        {"shoulder_amplitude": 1.5, "source_pins": {}}, observation(), clock=clock, io_factory=lambda *_: host
    )
    try:
        e.bind_run("selected-run", recipe)
        assert e.begin(recipe)
        until(lambda: e.evidence()["lifecycle"]["phase"] == "live")
        until(lambda: e.dispatch(recipe))
        until(lambda: any("_am1_selected_joint_evidence" in a for a in host.actions), timeout=5)
        marker_index = next(i for i, a in enumerate(host.actions) if "_am1_selected_joint_evidence" in a)
        assert marker_index > 0
        for action in host.actions[:marker_index]:
            assert "_am1_selected_joint_evidence" not in action
        action = host.actions[marker_index]
        marker = action["_am1_selected_joint_evidence"]
        assert marker["profile"] == profile and marker["phase"] == "baseline"
        assert marker["requested_target"] == action[ELBOW] == 7
        assert all(action[k] == 7 for k in JOINT_KEYS)
        assert all(action[k] == 0 for k in physical.ZERO_BODY)
    finally:
        e.close()
        e.worker.join(2)


@pytest.mark.parametrize(
    "recipe_name,selected_enabled,integral_enabled",
    [
        ("physical-left-elbow-compare", True, False),
        ("physical-left-elbow-dwell-compare", True, False),
        ("physical-arm-smoke", False, True),
        ("physical-arm-hold-body", False, False),
    ],
)
def test_host_launch_env_cannot_inherit_or_enable_other_recipe_experiments(
    monkeypatch,
    tmp_path,
    recipe_name,
    selected_enabled,
    integral_enabled,
):
    from pathlib import Path

    from lerobot.robots.alohamini.alohamini_client import AlohaMiniClient

    recipe = RECIPES.get(recipe_name)
    assert recipe is not None, "selected physical recipe is missing"
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    config = {
        "version": 1,
        "machine": "AM1",
        "host_head": "a" * 40,
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
    monkeypatch.setattr(AlohaMiniClient, "connect", lambda *args, **kwargs: None)
    monkeypatch.setenv("AM1_SELECTED_JOINT_EVIDENCE", "1")
    monkeypatch.setenv("AM1_SHOULDER_INTEGRAL_TEST", "1")
    monkeypatch.setenv("AM1_SCRIPTED_PREPARE", "1")
    monkeypatch.setenv("AM1_SYNC_SHOULDER_READBACK", "1")
    monkeypatch.setenv("AM1_LEFT_SHOULDER_EVIDENCE", "1")
    host = physical.ProtectedHostRun(config)
    try:
        host.start("12345678-selected", "incarnation", recipe, lambda: None)
        motor_env = next(env for name, env in launched if "host" in name)
        assert ("AM1_SELECTED_JOINT_EVIDENCE" in motor_env) == selected_enabled
        assert ("AM1_SYNC_SHOULDER_READBACK" in motor_env) == (not selected_enabled)
        assert ("AM1_LEFT_SHOULDER_EVIDENCE" in motor_env) == (not selected_enabled)
        assert ("AM1_SHOULDER_INTEGRAL_TEST" in motor_env) == integral_enabled
        assert ("AM1_SCRIPTED_PREPARE" in motor_env) == (selected_enabled or integral_enabled)
        assert all("AM1_SELECTED_JOINT_EVIDENCE" not in env for name, env in launched if "camera" in name)
    finally:
        for stream in (host.host_log, host.camera_log):
            if stream is not None:
                stream.close()


@pytest.mark.parametrize("recipe_name,class_name,profile,duration,hold", CASES)
def test_selected_final_return_refuses_measured_excursion_beyond_original_envelope(
    recipe_name,
    class_name,
    profile,
    duration,
    hold,
):
    task = FiniteTask(RECIPES[recipe_name], seed(), 0, 1.5)
    sequence = 0
    while task.provider.preparing or task.provider.elapsed_s < duration:
        sequence += 1
        task.advance(sequence / 10, {"positions": seed(), "sequence": sequence})
    actual = seed()
    actual[ELBOW] = 42.01
    with pytest.raises(ValueError, match="return exceeds.*elbow"):
        task.advance((sequence + 1) / 10, {"positions": actual, "sequence": sequence + 1})
    assert not task.complete and task.original_seed == seed()


def test_ordinary_physical_worker_never_emits_selected_context():
    clock = Clock()

    class SteppedHost(ControlledHost):
        def poll(self, previous):
            clock.advance(0.1)
            sample = super().poll(previous)
            sample["at"] = clock()
            return sample

    host = SteppedHost()
    e = physical.PhysicalExecutor(
        {"shoulder_amplitude": 1.5, "source_pins": {}}, observation(), clock=clock, io_factory=lambda *_: host
    )
    try:
        recipe = RECIPES["physical-arm-hold-body"]
        e.bind_run("ordinary-run", recipe)
        assert e.begin(recipe)
        until(lambda: e.evidence()["lifecycle"]["phase"] == "live")
        until(lambda: e.dispatch(recipe))
        until(lambda: e.advance(clock(), 0)["progress_s"] >= 0.2)
        assert any(a["_am1_local_control"]["mode"] == "active" for a in host.actions)
        assert all("_am1_selected_joint_evidence" not in a for a in host.actions)
    finally:
        e.close()
        e.worker.join(2)
