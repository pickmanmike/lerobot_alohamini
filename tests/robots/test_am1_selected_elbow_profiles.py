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


class NegativeHoldFeedback:
    """Finite simulated Native rows; this fixture never accesses a device."""

    def __init__(self, provider, *, residual=35, cached_current_before_repeat=False, outward=75):
        import copy

        from tests.robots.test_am1_positive_control import PositiveFeedback

        self.provider = provider
        self.feedback = PositiveFeedback(provider)
        self.residual = residual
        self.outward = outward
        self.cached_current_before_repeat = cached_current_before_repeat
        self.epoch = self.intent_revision = 0
        self.original = self.cursor = None
        self.rows = []
        self.proof = None
        self.copy = copy.deepcopy

    @staticmethod
    def certificate(item):
        return {
            "observation_id": item["observation_id"],
            "action_sequence": item["action_sequence"], "current_raw": item["current"]["raw"],
            "progress_s": item["context"]["progress_s"],
            "phase": item["context"]["phase"],
            "position_raw": item["position"]["raw"],
            **{
                f"{name}_read_{part}_at_monotonic_s": item[name][f"read_{part}_at_monotonic_s"]
                for name in ("position", "current")
                for part in ("started", "completed")
            },
            "acquired_at_monotonic_s": item["acquired_at_monotonic_s"],
        }

    def get(self, now, sequence):
        from tests.robots.test_am1_elbow_response_profile import norm

        p = self.provider
        positions, item = self.feedback.get(now, sequence)
        context = item["context"]
        cycle, phase, fraction = p.definition.phase(context["progress_s"], context["acquisition_s"])
        offset = (
            0 if phase == "baseline"
            else round((self.outward if cycle == 1 else self.outward-self.residual)*fraction
                       + (0 if cycle == 1 else self.residual)) if phase == "outbound"
            else self.outward if phase == "endpoint"
            else round(self.residual+(self.outward-self.residual)*fraction) if phase == "return"
            else self.residual
        )
        measured = 2399+(1 if p.definition.excursion_norm > 0 else -1)*offset
        item.update(run_id="12345678-1234-1234-1234-123456789abc",
                    host_incarnation="abcdef12-1234-1234-1234-123456789abc",
                    epoch=self.epoch, intent_revision=self.intent_revision)
        item["position"].update(raw=measured, normalized=norm(measured))
        positions[ELBOW] = norm(measured)
        item["action"].update(
            write_started_at_monotonic_s=now-.010,
            write_completed_at_monotonic_s=now-.008,
            write_acknowledged=False,
        )
        item["visible_control"]["binding"].update(
            recipe="physical-left-elbow-experiment", experiment_sha256=p.definition.sha256,
            run_id=item["run_id"], host_incarnation=item["host_incarnation"],
        )
        key = context["sample_key"]
        if key is not None and key not in p._verified_receipts:
            receipt = item["sparse_receipts"][key]
            receipt.update(run_id=item["run_id"], host_incarnation=item["host_incarnation"],
                           epoch=self.epoch, intent_revision=self.intent_revision)
            receipt["decoded"]["present_position_raw"] = measured
            self.feedback.receipts[key] = self.copy(receipt)
        if self.original is None:
            self.original = {
                "epoch": self.epoch,
                "intent_revision": self.intent_revision,
                "certificate": self.certificate(item),
            }
        if context["phase"] == "intercycle_hold":
            if self.cursor is not None and self.cached_current_before_repeat:
                item["current"].update(read_started_at_monotonic_s=now-.012,
                                       read_completed_at_monotonic_s=now-.011)
            row = [
                sequence, sequence, context["progress_s"], measured,
                item["position"]["read_started_at_monotonic_s"],
                item["position"]["read_completed_at_monotonic_s"], item["current"]["raw"],
                item["current"]["read_started_at_monotonic_s"],
                item["current"]["read_completed_at_monotonic_s"], item["acquired_at_monotonic_s"],
                item["action"]["write_started_at_monotonic_s"],
                item["action"]["write_completed_at_monotonic_s"],
            ]
            if self.cursor is None:
                self.cursor = {
                    "action_sequence": sequence,
                    "write_completed_at_monotonic_s": row[-1],
                    "certificate": self.certificate(item),
                }
            self.rows.append(row)
            del self.rows[:-12]
            if len(self.rows) >= 7:
                self.proof = {
                    "version": 1, "status": "ready",
                    "binding": {
                        "run_id": item["run_id"], "host_incarnation": item["host_incarnation"],
                        "experiment_sha256": p.definition.sha256,
                        "epoch": self.epoch, "intent_revision": self.intent_revision,
                    },
                    "calibration": self.copy(item["motor"]["calibration"]),
                    "reference_normalized": p.origin[ELBOW], "measured_origin_raw": 2399,
                    "expected_goal_raw": p.definition.encoded_goal(p.origin[ELBOW], item["motor"]["calibration"]),
                    "original_reference": self.copy(self.original), "hold_cursor": self.copy(self.cursor),
                    "qualified_at_monotonic_s": now, "rows": self.copy(self.rows),
                }
                item["intercycle_hold_admission"] = self.copy(self.proof)
        return positions, item

    def recover(self):
        self.epoch += 1
        self.intent_revision += 1
        self.rows.clear()
        self.cursor = self.proof = None


def negative_hold_provider(definition=None):
    from examples.alohamini import am1_session_contract as contract
    from tests.robots.test_am1_elbow_response_profile import norm

    return scripted_leader.LeftElbowPositiveControlInput(
        seed(norm(2399)), joint_keys=JOINT_KEYS, fps=10, emit=lambda _: None,
        definition=definition or contract.LEFT_ELBOW_NEGATIVE_COARSE_BENCH_V3,
    )


def run_negative_hold(p, feed, *, stop=58, start_sequence=0, clock_offset=0, mutate=None, skip_consumption=False, ipc_delay=0, skip_first_baseline=False):
    p.admit(clock_offset + start_sequence/30)
    for sequence in range(start_sequence+1, start_sequence+1901):
        now = clock_offset+sequence/30
        positions, item = feed.get(now-ipc_delay, sequence)
        if mutate is not None:
            mutate(item)
        if skip_first_baseline and sequence == start_sequence+1:
            continue
        if skip_consumption and item["context"]["phase"] == "intercycle_hold" and sequence % 4 == 1:
            continue
        p.collect_feedback(now, positions, sequence, selected_feedback=item)
        if sequence % 3 == 0:
            p.advance(now, positions, sequence, selected_feedback=item)
        if p.complete or p.elapsed_s >= stop:
            return sequence, now, positions, item
    pytest.fail("negative simulated provider did not reach its finite boundary")


def test_negative_v3_native_hold_admits_35_without_reseeding_or_changing_return_verdicts():
    p = negative_hold_provider()
    original = dict(p.origin)
    feed = NegativeHoldFeedback(p)
    run_negative_hold(p, feed)
    outcome = p.positive_control
    assert p.complete and p.elapsed_s == 58 and p.origin == original
    assert outcome["program_completed"] is True
    assert outcome["coarse_functional_pass"] is False and outcome["fine_return_pass"] is False
    assert [cycle["starting_raw"] for cycle in outcome["cycles"]] == [2399, 2364]
    assert [cycle["final_return_residual_raw"] for cycle in outcome["cycles"]] == [35, 35]
    assert [cycle["reference_raw"] for cycle in outcome["cycles"]] == [2399, 2399]
    hold = outcome["intercycle_hold"]
    assert hold["admission_proof"] == feed.proof
    assert hold["starting_raw"] == 2364 and hold["reference_raw"] == 2399
    assert hold["admission_proof"]["hold_cursor"]["certificate"]["phase"] == "intercycle_hold"
    assert hold["admission_proof"]["rows"][-1][2] == 31.9
    assert hold["anchor_proof"]["action"]["write_acknowledged"] is False
    execution = dict(original_seed=original, progress_s=58, cycles_completed=2,
                     returns_qualified=0, positive_control=outcome)
    assert p.definition.completion_expectation(execution) == (58, 2, 0)


@pytest.mark.parametrize("change", ["residual41", "spread3", "stale", "missing", "foreign",
                                    "actual_suffix", "original_reference"])
def test_negative_v3_refuses_unqualified_native_hold_before_second_departure(change):
    p = negative_hold_provider()
    feed = NegativeHoldFeedback(p, residual=41 if change == "residual41" else 17)
    original = dict(p.origin)

    def mutate(item):
        if item["context"]["phase"] != "intercycle_hold":
            return
        proof = item.get("intercycle_hold_admission")
        if change == "missing":
            item.pop("intercycle_hold_admission", None)
        elif proof is not None:
            if change == "spread3":
                proof["rows"][-2][3] -= 3
            elif change == "stale":
                proof["qualified_at_monotonic_s"] -= 1
            elif change == "foreign":
                proof["binding"]["host_incarnation"] = "foreign"
            elif change == "actual_suffix":
                proof["rows"][-2][3] -= 1
            elif change == "original_reference":
                proof["original_reference"]["certificate"]["observation_id"] += 1

    with pytest.raises(ValueError, match="intercycle hold"):
        run_negative_hold(p, feed, stop=32, mutate=mutate)
    assert p.elapsed_s == 31.9 and p.origin == original and not p.complete
    assert len(p.positive_control["cycles"]) == 1


def test_negative_v3_recovery_requires_fresh_native_hold_and_keeps_original_reference():
    p = negative_hold_provider()
    feed = NegativeHoldFeedback(p)
    sequence, now, positions, _ = run_negative_hold(p, feed, stop=31.9)
    original_seed = dict(p.origin)
    original_proof = p._measured_origin_proof.copy()
    old_proof = feed.copy(feed.proof)
    p.freeze()
    feed.recover()
    p.prepare_rejoin(now+10, positions, sequence+1, now+9.999)
    assert p.origin == original_seed and p._measured_origin_proof == original_proof
    p.admit(now+10)
    index, at = sequence, now+10
    while p._rejoin is not None:
        index += 1
        at += .1
        positions, item = feed.get(at, index)
        item["action"].update(write_started_at_monotonic_s=at-.102,
                              write_completed_at_monotonic_s=at-.1)
        p.advance(at, positions, index, selected_feedback=item)
        assert p.elapsed_s == 31.9
        assert index < sequence+100
    for _ in range(9):
        index += 1
        at += 1/30
        positions, item = feed.get(at, index)
        item["intercycle_hold_admission"] = feed.copy(old_proof)
        p.collect_feedback(at, positions, index, selected_feedback=item)
    with pytest.raises(ValueError, match="intercycle hold"):
        p.advance(at, positions, index, selected_feedback=item)
    assert p.elapsed_s == 31.9 and p.origin == original_seed
    assert p._measured_origin_proof == original_proof
    index += 1
    at += .1
    positions, item = feed.get(at, index)
    p.advance(at, positions, index, selected_feedback=item)
    assert p.elapsed_s == 32 and p.origin == original_seed
    hold = p.positive_control["intercycle_hold"]
    assert hold["admission_proof"]["binding"]["epoch"] == 1
    assert hold["admission_proof"]["original_reference"]["epoch"] == 0
    assert p._measured_origin_proof == original_proof and p._measured_origin_raw == 2399


def test_old_v2_still_refuses_35_raw_hold_and_keeps_four_key_hold_shape():
    from examples.alohamini import am1_session_contract as contract

    p = negative_hold_provider(contract.LEFT_ELBOW_COARSE_BENCH_V2)
    feed = NegativeHoldFeedback(p)
    with pytest.raises(ValueError, match="original return envelope"):
        run_negative_hold(p, feed, stop=32)
    assert p.elapsed_s == 31.9 and p.origin[ELBOW] == feed.proof["reference_normalized"]
    p = negative_hold_provider(contract.LEFT_ELBOW_COARSE_BENCH_V2)
    run_negative_hold(p, NegativeHoldFeedback(p, residual=17), stop=32)
    assert set(p.positive_control["intercycle_hold"]) == {"window", "anchor_proof", "starting_raw", "reference_raw"}


@pytest.mark.parametrize("skip_consumption", [False, True])
def test_negative_v3_matches_native_rows_when_owner_skips_cache_snapshots(skip_consumption):
    p = negative_hold_provider()
    feed = NegativeHoldFeedback(p)
    run_negative_hold(p, feed, stop=32, skip_consumption=skip_consumption)
    hold = p.positive_control["intercycle_hold"]
    assert hold["starting_raw"] == 2364 and p.elapsed_s == 32
    assert hold["window"]["sample_count"] >= 3
    assert len(hold["admission_proof"]["rows"]) == 12


def test_negative_v3_cached_current_can_precede_latest_identical_native_hold_write():
    p = negative_hold_provider()
    feed = NegativeHoldFeedback(p, cached_current_before_repeat=True)
    run_negative_hold(p, feed, stop=32)
    row = p.positive_control["intercycle_hold"]["admission_proof"]["rows"][-1]
    assert row[8] < row[11] <= row[4]
    assert p.elapsed_s == 32 and p.positive_control["intercycle_hold"]["starting_raw"] == 2364


@pytest.mark.parametrize("counter", ["epoch", "intent_revision"])
def test_negative_v3_binding_change_cannot_count_previous_admission_acquisitions(counter):
    p = negative_hold_provider()
    feed = NegativeHoldFeedback(p)
    sequence, now, _, _ = run_negative_hold(p, feed, stop=31.9)
    original = feed.copy(p._measured_origin_proof)
    setattr(feed, counter, 1)
    # Adversarial claim retaining old rows under a new outer binding. Actual
    # provider feedback under that binding consists of only this acquisition.
    positions, item = feed.get(now+.1, sequence+1)
    with pytest.raises(ValueError, match="intercycle hold"):
        p.advance(now+.1, positions, sequence+1, selected_feedback=item)
    assert p.elapsed_s == 31.9 and p._measured_origin_proof == original



@pytest.mark.parametrize("skip_first_baseline", [False,True])
def test_negative_v3_completed_two_cycles_preserve_distinct_delayed_owner_capture(skip_first_baseline):
    import copy
    p = negative_hold_provider()
    original = dict(p.origin)
    run_negative_hold(p,NegativeHoldFeedback(p,outward=100),ipc_delay=.025,skip_first_baseline=skip_first_baseline)
    outcome = p.positive_control
    hold = outcome["intercycle_hold"]
    assert p.complete and p.origin == original and p.elapsed_s == 58
    assert outcome["program_completed"] is True
    assert [row["outward_response_raw"] for row in outcome["cycles"]] == [100,65]
    assert [row["reverse_response_raw"] for row in outcome["cycles"]] == [65,65]
    assert [row["final_return_residual_raw"] for row in outcome["cycles"]] == [35,35]
    assert outcome["coarse_functional_pass"] is False and outcome["fine_return_pass"] is False
    assert hold["window"]["qualified_at_monotonic_s"] == hold["admission_proof"]["qualified_at_monotonic_s"]
    assert hold["admission_proof"]["qualified_at_monotonic_s"] < hold["anchor_proof"]["captured_at_monotonic_s"]
    if skip_first_baseline:
        assert hold["admission_proof"]["original_reference"]["certificate"]["observation_id"] < outcome["measured_origin_proof"]["observation_id"]
    execution = dict(original_seed=original,progress_s=58,cycles_completed=2,returns_qualified=0,positive_control=outcome)
    assert p.definition.completion_expectation(execution) == (58,2,0)
    for invalid in ("window_clock_replaced_by_capture","capture_before_qualification","future_qualification"):
        altered = copy.deepcopy(execution)
        item = altered["positive_control"]["intercycle_hold"]
        if invalid == "window_clock_replaced_by_capture":
            item["window"]["qualified_at_monotonic_s"] = item["anchor_proof"]["captured_at_monotonic_s"]
            altered["positive_control"]["cycles"][1]["baseline_window"] = copy.deepcopy(item["window"])
        elif invalid == "capture_before_qualification":
            item["anchor_proof"]["captured_at_monotonic_s"] = item["admission_proof"]["qualified_at_monotonic_s"]-.001
            altered["positive_control"]["cycles"][1]["baseline_anchor_proof"] = copy.deepcopy(item["anchor_proof"])
        else:
            item["admission_proof"]["qualified_at_monotonic_s"] = item["anchor_proof"]["captured_at_monotonic_s"]+.001
        assert p.definition.completion_expectation(altered) is None
