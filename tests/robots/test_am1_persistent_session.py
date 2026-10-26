"""Authority behavior with fake executors and real physical adapters using simulated device IO."""

import uuid

import pytest

from tools.am1_fake_executor import FakeExecutor
from tools.am1_session_core import SessionAuthority


def test_live_elbow_specs_are_exact_and_match_admitted_recipe_limits():
    from examples.alohamini import am1_session_contract as contract

    specs = getattr(contract, "LIVE_ELBOW_SPECS", {})
    assert set(specs) == {"physical-live-left-elbow", "physical-live-right-elbow"}
    expected = [
        ("physical-live-left-elbow", "arm_left_elbow_flex.pos", "left", "LiveLeftElbow", -10.0, 0.0),
        ("physical-live-right-elbow", "arm_right_elbow_flex.pos", "right", "LiveRightElbow", -3.0, 0.0),
    ]
    for recipe, joint, side, profile, minimum, maximum in expected:
        spec = specs[recipe]
        assert spec.recipe == recipe and spec.joint == joint and spec.side == side
        assert spec.profile == profile
        assert (spec.corridor_min_delta_norm, spec.corridor_max_delta_norm) == (minimum, maximum)
        assert spec.rate_norm_per_s == 1.0 and spec.current_cap_ma == 150.0 and spec.live_s == 90.0
        assert spec.summary()["joint"] == joint and spec.summary()["recipe"] == recipe
        assert contract.RECIPES[recipe].mode == "interactive"
        assert contract.RECIPES[recipe].seed is None
        assert contract.RECIPES[recipe].trajectory_s is None
        assert contract.RECIPES[recipe].live_s == 90.0
    assert specs.get("physical-live-shoulder") is None
    assert specs.get("arm_right_elbow_flex.pos") is None


def test_live_elbow_registry_and_spec_cannot_change_after_selection():
    from dataclasses import FrozenInstanceError

    from examples.alohamini import am1_session_contract as contract

    specs = getattr(contract, "LIVE_ELBOW_SPECS", {})
    assert "physical-live-right-elbow" in specs
    with pytest.raises(TypeError):
        specs["physical-live-right-elbow"] = specs["physical-live-left-elbow"]
    with pytest.raises(FrozenInstanceError):
        specs["physical-live-right-elbow"].joint = "arm_left_elbow_flex.pos"
    summary = specs["physical-live-right-elbow"].summary()
    summary["joint"] = "arm_left_elbow_flex.pos"
    assert specs["physical-live-right-elbow"].joint == "arm_right_elbow_flex.pos"


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, n):
        self.now += n


def command(op, **kw):
    if op in {"pause", "stop", "resume", "reconcile", "release", "handoff"}:
        kw.setdefault("operation_id", str(uuid.uuid4()))
    return dict(op=op, **kw)


def start(a, recipe="fake-finite-short"):
    claim = a.handle(command("claim"), "owner")
    return a.handle(
        command(
            "start",
            operation_id=str(uuid.uuid4()),
            recipe=recipe,
            controller_generation=claim["controller_generation"],
        ),
        "owner",
    )


def test_duplicate_response_conflict_and_restart(tmp_path):
    c = Clock()
    a = SessionAuthority(tmp_path, clock=c)
    claim = a.handle(command("claim"), "owner")
    cmd = command(
        "start",
        operation_id=str(uuid.uuid4()),
        recipe="fake-finite-short",
        controller_generation=claim["controller_generation"],
    )
    r = a.handle(cmd, "owner")
    assert r["accepted"]
    assert a.handle(cmd, "owner")["run_id"] == r["run_id"]
    assert not a.handle(dict(cmd, recipe="fake-interactive"), "owner")["accepted"]
    a.close()
    b = SessionAuthority(tmp_path, clock=c)
    assert b.handle(cmd, "owner")["status"] == "interrupted"
    assert not start(b)["accepted"]
    assert b.handle(command("reconcile", run_id=r["run_id"]), "owner")["accepted"]
    assert start(b)["accepted"]
    b.close()


def test_finite_detach_bounded_progress_and_absolute_deadline(tmp_path):
    c = Clock()
    a = SessionAuthority(tmp_path, clock=c)
    start(a)
    c.advance(2)
    a.tick()
    s = a.snapshot()
    assert s["run"]["progress_s"] == pytest.approx(0.1)
    assert s["controller"] is None
    c.advance(10)
    a.tick()
    assert a.snapshot()["run"]["status"] == "stopped"
    assert a.snapshot()["run"]["cleanup"] == "held_body_zero"
    a.close()


def test_required_loss_recovery_and_pause_fence(tmp_path):
    c = Clock()
    e = FakeExecutor(clock=c)
    a = SessionAuthority(tmp_path, clock=c, executor=e)
    r = start(a)
    e.optional_quality = False
    c.advance(0.1)
    a.tick()
    assert a.snapshot()["run"]["progress_s"] == pytest.approx(0.1)
    e.required_observation = False
    a.tick()
    s = a.snapshot()["run"]
    assert s["status"] == "recovering"
    ceiling = s["deadline"]
    a.handle(command("pause", run_id=r["run_id"]), "other")
    e.required_observation = True
    c.advance(0.1)
    a.tick()
    assert a.snapshot()["run"]["status"] == "paused"
    assert a.snapshot()["run"]["deadline"] == ceiling
    assert a.handle(command("stop", run_id=r["run_id"]), "other")["accepted"]
    a.close()


def test_interactive_grant_expiry_and_reconnect_release(tmp_path):
    c = Clock()
    a = SessionAuthority(tmp_path, clock=c)
    r = start(a, "fake-interactive")
    h = a.handle(command("connect", run_id=r["run_id"]), "owner")
    base = {
        "run_id": r["run_id"],
        "service_incarnation": a.snapshot()["service_incarnation"],
        "controller_generation": a.handle(command("claim"), "owner")["controller_generation"],
        "connection_generation": h["connection_generation"],
    }
    assert not a.handle(
        command("resume", **base, expected_intent_revision=a.snapshot()["run"]["intent_revision"]), "owner"
    )["accepted"]
    assert a.handle(command("release_input", **base), "owner")["accepted"]
    assert a.handle(
        command("resume", **base, expected_intent_revision=a.snapshot()["run"]["intent_revision"]), "owner"
    )["accepted"]
    g = a.handle(command("grant", **base), "owner")
    inp = command("input", **base, grant_id=g["grant_id"], seq=1, target=[0.2])
    assert a.handle(inp, "owner")["effect_admitted"]
    c.advance(0.251)
    assert not a.handle(dict(inp, seq=2), "owner")["accepted"]
    a.tick()
    assert a.snapshot()["run"]["intent"] is None
    assert "grant_id" not in str(a.snapshot())
    a.close()


def test_lock_validation_and_bounded_events(tmp_path):
    a = SessionAuthority(tmp_path)
    with pytest.raises(RuntimeError):
        SessionAuthority(tmp_path)
    for cmd in [
        None,
        {"op": "start", "operation_id": True},
        {"op": "fault"},
        {"op": "claim", "device_id": "spoof"},
    ]:
        assert not a.handle(cmd, "owner")["accepted"]
    for _ in range(100):
        a.handle(command("claim"), "owner")
        a.handle(command("release"), "owner")
    assert len(a.snapshot()["events"]) <= 64
    a.close()


def test_stop_wins_recovery_and_first_cause_survives_restart(tmp_path):
    c = Clock()
    e = FakeExecutor(clock=c)
    a = SessionAuthority(tmp_path, clock=c, executor=e)
    r = start(a)
    e.required_observation = False
    a.tick()
    a.handle(command("stop", run_id=r["run_id"]), "other")
    e.required_observation = True
    a.tick()
    assert a.snapshot()["run"]["status"] == "stopped"
    assert a.snapshot()["run"]["first_cause"] == "required_proof_loss"
    a.close()
    b = SessionAuthority(tmp_path, clock=c)
    assert b.snapshot()["run"]["status"] == "stopped"
    assert not b.snapshot()["run"]["uncertain"]
    b.close()


def test_concurrent_claim_has_one_owner_and_explicit_handoff(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    a = SessionAuthority(tmp_path)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda d: (d, a.handle(command("claim"), d)), ["one", "two"]))
    assert sum(r["accepted"] for _, r in results) == 1
    winner = next(d for d, r in results if r["accepted"])
    loser = next(d for d, r in results if not r["accepted"])
    assert a.handle(command("handoff", target_device_id=loser), winner)["accepted"]
    assert not a.handle(command("claim"), winner)["accepted"]
    assert a.handle(command("claim"), loser)["accepted"]
    a.close()


def test_cross_process_lock_blocks_fake_legacy_owner(tmp_path):
    import os
    import subprocess
    import sys

    a = SessionAuthority(tmp_path)
    script = "from tools.am1_session_core import OwnerLock; import sys; OwnerLock(sys.argv[1])"
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(tmp_path / "owner.lock")],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    assert result.returncode != 0
    assert "namespace owner already active" in result.stderr
    a.close()
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(tmp_path / "owner.lock")],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    assert result.returncode == 0


def test_recovery_ceiling_and_episode_budget_are_not_renewed(tmp_path):
    c = Clock()
    e = FakeExecutor(clock=c)
    a = SessionAuthority(tmp_path, clock=c, executor=e)
    start(a)
    for _ in range(3):
        e.required_observation = False
        a.tick()
        ceiling = a.snapshot()["run"]["recovery"]["ceiling"]
        c.advance(0.01)
        a.tick()
        assert a.snapshot()["run"]["recovery"]["ceiling"] == ceiling
        e.required_observation = True
        a.tick()
        assert a.snapshot()["run"]["status"] == "running"
    e.required_observation = False
    a.tick()
    assert a.snapshot()["run"]["status"] == "faulted"
    a.close()


def test_input_rejects_boolean_sequence_and_stale_connection(tmp_path):
    c = Clock()
    a = SessionAuthority(tmp_path, clock=c)
    r = start(a, "fake-interactive")
    generation = a.handle(command("claim"), "owner")["controller_generation"]
    connection = a.handle(command("connect", run_id=r["run_id"]), "owner")["connection_generation"]
    base = {
        "run_id": r["run_id"],
        "service_incarnation": a.service_incarnation,
        "controller_generation": generation,
        "connection_generation": connection,
    }
    a.handle(command("release_input", **base), "owner")
    a.handle(
        command("resume", **base, expected_intent_revision=a.snapshot()["run"]["intent_revision"]), "owner"
    )
    grant = a.handle(command("grant", **base), "owner")["grant_id"]
    assert not a.handle(command("input", **base, grant_id=grant, seq=True, target=[0.1]), "owner")["accepted"]
    a.handle(command("connect", run_id=r["run_id"]), "owner")
    assert not a.handle(command("input", **base, grant_id=grant, seq=1, target=[0.1]), "owner")["accepted"]
    a.close()


def test_pause_operation_retry_cannot_pause_a_later_resume(tmp_path):
    c = Clock()
    a = SessionAuthority(tmp_path, clock=c)
    r = start(a)
    oid = str(uuid.uuid4())
    pause = command("pause", run_id=r["run_id"], operation_id=oid)
    assert a.handle(pause, "owner")["accepted"]
    revision = a.snapshot()["run"]["intent_revision"]
    assert a.handle(pause, "owner")["accepted"]
    assert a.snapshot()["run"]["intent_revision"] == revision
    assert not a.handle(dict(pause, op="stop"), "owner")["accepted"]
    a.close()


def test_tick_does_not_commit_every_high_rate_sample(tmp_path):
    c = Clock()
    a = SessionAuthority(tmp_path, clock=c)
    start(a)
    statements = []
    a.db.set_trace_callback(statements.append)
    for _ in range(10):
        c.advance(0.01)
        a.tick()
    assert sum(s == "COMMIT" for s in statements) <= 1
    a.close()


def test_mutations_require_distinct_operation_identity(tmp_path):
    a = SessionAuthority(tmp_path)
    r = start(a)
    assert not a.handle({"op": "stop", "run_id": r["run_id"]}, "owner")["accepted"]
    a.close()


def test_snapshot_reports_remaining_trajectory_and_owner_fences(tmp_path):
    a = SessionAuthority(tmp_path)
    start(a)
    s = a.snapshot()
    assert s["controller"]["controller_generation"]
    assert s["run"]["trajectory_remaining_s"] == 1.0
    assert s["run"]["source"] == "fake"
    assert s["run"]["dispatch"] == "acknowledged"
    a.close()


def test_finite_presence_expiry_does_not_hold_authorized_fake_motion(tmp_path):
    c = Clock()
    e = FakeExecutor(clock=c)
    a = SessionAuthority(tmp_path, clock=c, executor=e)
    start(a)
    c.advance(2)
    a.tick()
    assert e.output == "fake_running"
    a.close()


def test_restart_old_operation_result_remains_after_new_run(tmp_path):
    c = Clock()
    a = SessionAuthority(tmp_path, clock=c)
    first = start(a)
    a.close()
    b = SessionAuthority(tmp_path, clock=c)
    b.handle(command("claim"), "owner")
    b.handle(command("reconcile", run_id=first["run_id"]), "owner")
    second = start(b)
    old = b.handle(command("lookup", operation_id=first["operation_id"]), "owner")
    assert old["run_id"] == first["run_id"]
    assert old["status"] == "interrupted"
    assert second["run_id"] != first["run_id"]
    b.close()


def test_repeated_proof_loss_keeps_original_recovery_ceiling(tmp_path):
    c = Clock()
    e = FakeExecutor(clock=c)
    a = SessionAuthority(tmp_path, clock=c, executor=e)
    start(a, "fake-finite")
    e.required_observation = False
    a.tick()
    ceiling = a.snapshot()["run"]["recovery"]["ceiling"]
    e.required_observation = True
    e.pose_aligned = False
    c.advance(1)
    a.tick()
    e.required_observation = False
    c.advance(1)
    a.tick()
    assert a.snapshot()["run"]["recovery"]["ceiling"] == ceiling
    a.close()


def test_transition_history_survives_terminal_restart(tmp_path):
    a = SessionAuthority(tmp_path)
    r = start(a)
    a.handle(command("stop", run_id=r["run_id"]), "owner")
    a.close()
    b = SessionAuthority(tmp_path)
    assert any(event["kind"] == "stopped" for event in b.snapshot()["events"])
    b.close()


def test_operation_id_cannot_change_command_class(tmp_path):
    a = SessionAuthority(tmp_path)
    r = start(a)
    assert not a.handle(command("stop", run_id=r["run_id"], operation_id=r["operation_id"]), "owner")[
        "accepted"
    ]
    a.close()


class CountingExecutor(FakeExecutor):
    def __init__(self, clock):
        super().__init__(clock=clock)
        self.dispatch_count = 0

    def dispatch(self, recipe):
        self.dispatch_count += 1
        return super().dispatch(recipe)


def interactive_controls(a, r):
    generation = a.handle(command("claim"), "owner")["controller_generation"]
    connection = a.handle(command("connect", run_id=r["run_id"]), "owner")["connection_generation"]
    base = {
        "run_id": r["run_id"],
        "service_incarnation": a.service_incarnation,
        "controller_generation": generation,
        "connection_generation": connection,
    }
    a.handle(command("release_input", **base), "owner")
    assert a.handle(
        command("resume", **base, expected_intent_revision=a.snapshot()["run"]["intent_revision"]), "owner"
    )["accepted"]
    return base


def test_replacing_grant_without_new_input_cannot_extend_applied_target(tmp_path):
    c = Clock()
    e = FakeExecutor(clock=c)
    a = SessionAuthority(tmp_path, clock=c, executor=e)
    r = start(a, "fake-interactive")
    base = interactive_controls(a, r)
    g = a.handle(command("grant", **base), "owner")
    assert a.handle(command("input", **base, grant_id=g["grant_id"], seq=1, target=[0.4]), "owner")[
        "effect_admitted"
    ]
    c.advance(0.2)
    a.handle(command("grant", **base), "owner")
    c.advance(0.06)
    a.tick()
    assert a.snapshot()["run"]["intent"] is None
    assert e.output == "held_body_zero"
    a.close()


@pytest.mark.parametrize("op", ["pause", "stop"])
def test_protective_intent_prevents_recovery_dispatch(tmp_path, op):
    c = Clock()
    e = CountingExecutor(c)
    a = SessionAuthority(tmp_path, clock=c, executor=e)
    r = start(a)
    e.required_observation = False
    a.tick()
    before = a.snapshot()["run"]["progress_s"]
    count = e.dispatch_count
    e.required_observation = True
    c.advance(0.1)
    result = a.handle(command(op, run_id=r["run_id"]), "other")
    assert result["accepted"]
    assert e.dispatch_count == count
    assert a.snapshot()["run"]["progress_s"] == before
    assert e.output == "held_body_zero"
    a.close()


@pytest.mark.parametrize("op", ["pause", "stop"])
def test_protective_intent_beats_near_completion_progress(tmp_path, op):
    c = Clock()
    e = CountingExecutor(c)
    a = SessionAuthority(tmp_path, clock=c, executor=e)
    r = start(a)
    for _ in range(9):
        c.advance(0.1)
        a.tick()
    before = a.snapshot()["run"]["progress_s"]
    c.advance(0.11)
    result = a.handle(command(op, run_id=r["run_id"]), "other")
    assert result["accepted"]
    assert a.snapshot()["run"]["progress_s"] == before
    assert a.snapshot()["run"]["status"] == ("paused" if op == "pause" else "stopped")
    a.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("feedback", False),
        ("feedback_age_s", 0.251),
        ("required_observation", False),
        ("observation_age_s", 0.501),
        ("fault", "fake_fault"),
        ("native_ack", False),
    ],
)
def test_initial_start_rejects_unqualified_effects(tmp_path, field, value):
    c = Clock()
    e = CountingExecutor(c)
    setattr(e, field, value)
    a = SessionAuthority(tmp_path, clock=c, executor=e)
    result = start(a)
    assert not result["effect_admitted"]
    assert e.dispatch_count == 0
    assert e.output == "held_body_zero"
    assert a.snapshot()["run"] is None
    a.close()


def test_initial_interactive_is_held_until_qualified_resume(tmp_path):
    c = Clock()
    e = CountingExecutor(c)
    a = SessionAuthority(tmp_path, clock=c, executor=e)
    r = start(a, "fake-interactive")
    assert r["accepted"]
    assert not r["effect_admitted"]
    assert e.dispatch_count == 0
    assert a.snapshot()["run"]["status"] == "paused"
    assert e.output == "held_body_zero"
    interactive_controls(a, r)
    assert e.dispatch_count == 1
    a.close()


def test_required_proof_allows_500ms_but_feedback_only_250ms(tmp_path):
    c = Clock()
    e = FakeExecutor(clock=c)
    e.observation_age_s = 0.5
    e.feedback_age_s = 0.25
    a = SessionAuthority(tmp_path, clock=c, executor=e)
    assert start(a)["effect_admitted"]
    a.close()


def test_incomplete_finite_deadline_cannot_claim_completion(tmp_path):
    c = Clock()
    a = SessionAuthority(tmp_path, clock=c)
    start(a)
    c.advance(12)
    a.tick()
    s = a.snapshot()["run"]
    assert s["status"] == "stopped"
    assert s["trajectory_remaining_s"] == 1.0
    assert s["first_cause"] == "live_deadline"
    a.close()


def test_distinct_recovery_after_ack_gets_new_bounded_episode(tmp_path):
    c = Clock()
    e = FakeExecutor(clock=c)
    a = SessionAuthority(tmp_path, clock=c, executor=e)
    start(a, "fake-finite")
    e.required_observation = False
    a.tick()
    assert a.snapshot()["run"]["recovery"]["ceiling"] == 10.0
    e.required_observation = True
    c.advance(1)
    a.tick()
    e.required_observation = False
    c.advance(1)
    a.tick()
    assert a.snapshot()["run"]["recovery"]["ceiling"] == 12.0
    assert a.snapshot()["run"]["recovery_episodes"] == 2
    a.close()


@pytest.mark.parametrize("boundary", ["accepted", "dispatching", "after_effect"])
def test_unclean_fake_exit_at_accept_dispatch_boundaries_never_autoplays(tmp_path, boundary):
    import os
    import subprocess
    import sys

    oid = str(uuid.uuid4())
    script = """
import os,sys
from tools.am1_session_core import SessionAuthority
from tools.am1_fake_executor import FakeExecutor
class ExitExecutor(FakeExecutor):
    def dispatch(self,recipe):
        if sys.argv[2]=='after_effect': super().dispatch(recipe)
        os._exit(73)
class ExitAuthority(SessionAuthority):
    def _save(self):
        super()._save()
        if self.run and self.run['dispatch']==sys.argv[2]: os._exit(73)
a=ExitAuthority(sys.argv[1],executor=ExitExecutor())
g=a.handle({'op':'claim'},'owner')['controller_generation']
a.handle({'op':'start','operation_id':sys.argv[3],'recipe':'fake-finite','controller_generation':g},'owner')
"""
    exited = subprocess.run(
        [sys.executable, "-B", "-c", script, str(tmp_path), boundary, oid],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
    )
    assert exited.returncode == 73, exited.stderr
    c = Clock()
    e = CountingExecutor(c)
    a = SessionAuthority(tmp_path, clock=c, executor=e)
    result = a.handle(command("lookup", operation_id=oid), "owner")
    assert result["status"] == "interrupted"
    assert result["uncertain"]
    assert e.dispatch_count == 0
    assert not start(a)["accepted"]
    assert e.dispatch_count == 0
    a.close()


def test_interactive_resume_persists_dispatching_before_first_effect(tmp_path):
    import json
    import sqlite3

    c = Clock()

    class InspectExecutor(FakeExecutor):
        def dispatch(self, recipe):
            with sqlite3.connect(tmp_path / "sessions.sqlite3") as db:
                durable = json.loads(db.execute("SELECT data FROM state WHERE id=1").fetchone()[0])
            assert durable["dispatch"] == "dispatching"
            return super().dispatch(recipe)

    a = SessionAuthority(tmp_path, clock=c, executor=InspectExecutor(clock=c))
    r = start(a, "fake-interactive")
    interactive_controls(a, r)
    a.close()


def test_stop_retains_present_fault_without_motion_and_after_restart(tmp_path):
    c = Clock()
    e = CountingExecutor(c)
    a = SessionAuthority(tmp_path, clock=c, executor=e)
    r = start(a)
    before = a.snapshot()["run"]["progress_s"]
    count = e.dispatch_count
    e.fault = "fake_local_feedback_fault"
    c.advance(0.1)
    result = a.handle(command("stop", run_id=r["run_id"]), "other")
    assert result["accepted"]
    assert result["status"] == "stopped"
    assert e.output == "held_body_zero"
    assert e.dispatch_count == count
    assert a.snapshot()["run"]["progress_s"] == before
    assert a.snapshot()["run"]["first_cause"] == "fake_local_feedback_fault"
    a.close()
    b = SessionAuthority(tmp_path, clock=c)
    assert b.snapshot()["run"]["first_cause"] == "fake_local_feedback_fault"
    assert b.snapshot()["run"]["status"] == "stopped"
    b.close()


@pytest.mark.parametrize("admitted", [True, False])
def test_manual_resume_closes_only_acknowledged_recovery_episode(tmp_path, admitted):
    c = Clock()

    class ResumeExecutor(CountingExecutor):
        def dispatch(self, recipe):
            if self.dispatch_count:
                self.dispatch_count += 1
                if not admitted:
                    return False
                return FakeExecutor.dispatch(self, recipe)
            return super().dispatch(recipe)

    e = ResumeExecutor(c)
    a = SessionAuthority(tmp_path, clock=c, executor=e)
    r = start(a, "fake-finite")
    deadline = a.snapshot()["run"]["deadline"]
    e.required_observation = False
    a.tick()
    a.handle(command("pause", run_id=r["run_id"]), "owner")
    c.advance(1)
    e.required_observation = True
    generation = a.handle(command("claim"), "owner")["controller_generation"]
    connection = a.handle(command("connect", run_id=r["run_id"]), "owner")["connection_generation"]
    base = {
        "run_id": r["run_id"],
        "service_incarnation": a.service_incarnation,
        "controller_generation": generation,
        "connection_generation": connection,
    }
    a.handle(command("release_input", **base), "owner")
    result = a.handle(
        command("resume", **base, expected_intent_revision=a.snapshot()["run"]["intent_revision"]), "owner"
    )
    assert result["effect_admitted"] == admitted
    if not admitted:
        assert a.snapshot()["run"]["recovery"]["ceiling"] == 10.0
        assert a.snapshot()["run"]["status"] == "paused"
        a.close()
        return
    assert a.snapshot()["run"]["recovery"] is None
    c.advance(10)
    e.required_observation = False
    a.tick()
    s = a.snapshot()["run"]
    assert s["status"] == "recovering"
    assert s["recovery"]["ceiling"] == 21.0
    assert s["recovery_episodes"] == 2
    assert s["deadline"] == deadline
    assert s["first_cause"] == "required_proof_loss"
    a.close()


@pytest.mark.parametrize("loss", ["expiry", "release", "replacement"])
def test_generation_fenced_claim_cannot_reacquire_after_authority_loss(tmp_path, loss):
    clock = Clock()
    authority = SessionAuthority(tmp_path, clock=clock)
    try:
        generation = authority.handle(command("claim"), "owner")["controller_generation"]
        clock.advance(0.5)
        renewed = authority.handle(command("claim", controller_generation=generation), "owner")
        assert renewed["accepted"]
        assert renewed["controller_generation"] == generation
        assert not authority.handle(command("claim", controller_generation=generation), "other")["accepted"]
        if loss == "expiry":
            clock.advance(1.501)
            authority.tick()
            assert authority.snapshot()["controller"] is None
        else:
            assert authority.handle(command("release"), "owner")["accepted"]
            if loss == "replacement":
                replacement = authority.handle(command("claim"), "owner")["controller_generation"]
                assert replacement != generation
        before = authority.snapshot()["controller"]
        stale = authority.handle(command("claim", controller_generation=generation), "owner")
        assert not stale["accepted"], "renewal must never acquire or renew a different generation"
        after = authority.snapshot()["controller"]
        if loss == "replacement":
            assert after == before, "stale renewal must not extend replacement presence"
        else:
            assert after is None
        for invalid in (None, "", False, 123, []):
            assert not authority.handle(command("claim", controller_generation=invalid), "owner")["accepted"]
        explicit = authority.handle(command("claim"), "owner")
        assert explicit["accepted"], "explicit acquisition remains available"
        assert explicit["controller_generation"] != generation
    finally:
        authority.close()


@pytest.mark.parametrize("expected", [None, True, False, 1.0, "1", -1, {}, [], "missing", "old", "future"])
def test_resume_requires_exact_integer_expected_intent_revision(tmp_path, expected):
    clock = Clock()
    executor = CountingExecutor(clock)
    authority = SessionAuthority(tmp_path, clock=clock, executor=executor)
    try:
        run = start(authority)
        assert authority.handle(command("pause", run_id=run["run_id"]), "spectator")["accepted"]
        controller = authority.handle(command("claim"), "owner")["controller_generation"]
        connected = authority.handle(command("connect", run_id=run["run_id"]), "owner")
        ids = {
            "run_id": run["run_id"],
            "service_incarnation": authority.service_incarnation,
            "controller_generation": controller,
            "connection_generation": connected["connection_generation"],
        }
        assert authority.handle(command("release_input", **ids), "owner")["accepted"]
        request = command("resume", **ids)
        revision = authority.snapshot()["run"]["intent_revision"]
        if expected != "missing":
            request["expected_intent_revision"] = (
                revision - 1 if expected == "old" else revision + 1 if expected == "future" else expected
            )
        count = executor.dispatch_count
        result = authority.handle(request, "owner")
        assert not result["accepted"], "missing, malformed or stale revision must refuse before dispatch"
        assert not result["effect_admitted"]
        assert authority.snapshot()["run"]["status"] == "paused"
        assert executor.dispatch_count == count
        assert authority.snapshot()["run"]["intent_revision"] == revision
    finally:
        authority.close()


def test_admitted_resume_retry_remains_truthful_after_later_spectator_pause(tmp_path):
    clock = Clock()
    executor = CountingExecutor(clock)
    authority = SessionAuthority(tmp_path, clock=clock, executor=executor)
    try:
        run = start(authority)
        authority.handle(command("pause", run_id=run["run_id"]), "spectator")
        controller = authority.handle(command("claim"), "owner")["controller_generation"]
        connected = authority.handle(command("connect", run_id=run["run_id"]), "owner")
        ids = {
            "run_id": run["run_id"],
            "service_incarnation": authority.service_incarnation,
            "controller_generation": controller,
            "connection_generation": connected["connection_generation"],
        }
        authority.handle(command("release_input", **ids), "owner")
        request = command(
            "resume", **ids, expected_intent_revision=authority.snapshot()["run"]["intent_revision"]
        )
        admitted = authority.handle(request, "owner")
        assert admitted["accepted"] and admitted["effect_admitted"]
        assert authority.handle(command("pause", run_id=run["run_id"]), "spectator")["accepted"]
        count = executor.dispatch_count
        assert authority.handle(request, "owner") == admitted
        assert authority.snapshot()["run"]["status"] == "paused"
        assert executor.dispatch_count == count
        authority.handle(command("release_input", **ids), "owner")
        stale = dict(request, operation_id=str(uuid.uuid4()))
        assert not authority.handle(stale, "owner")["accepted"]
        fresh = dict(
            stale,
            operation_id=str(uuid.uuid4()),
            expected_intent_revision=authority.snapshot()["run"]["intent_revision"],
        )
        assert authority.handle(fresh, "owner")["effect_admitted"]
        assert executor.dispatch_count == count + 1
    finally:
        authority.close()

# These tests exercise the real physical adapter/worker with the existing simulated host IO.
@pytest.fixture
def physical_live_authority(tmp_path, request):
    from tests.robots.test_am1_live_native import ExecutorNativeHost
    from tests.robots.test_am1_physical_executor import executor, until

    host = ExecutorNativeHost()
    e = executor(host)
    a = SessionAuthority(tmp_path, executor=e)
    try:
        r = start(a, getattr(request, "param", "physical-live-left-elbow"))
        assert r["accepted"], r
        until(lambda: (a.tick() or a.snapshot()["run"]["status"] == "paused"))
        yield a, e, host, r
    finally:
        a.close()
        if e.worker:
            e.worker.join(2)


def physical_live_controls(a, r):
    from tests.robots.test_am1_physical_executor import until

    snapshot = a.snapshot()
    generation = snapshot["controller"]["controller_generation"]
    assert a.handle(command("claim", controller_generation=generation), "owner")["accepted"]
    base = {
        "run_id": r["run_id"],
        "service_incarnation": snapshot["service_incarnation"],
        "controller_generation": generation,
        "host_incarnation": snapshot["evidence"]["host_incarnation"],
    }
    connected = a.handle(command("connect", **base), "owner")
    assert connected["accepted"], connected
    base["connection_generation"] = connected["connection_generation"]
    assert a.handle(command("release_input", **base), "owner")["accepted"]
    until(lambda: a.snapshot()["evidence"]["pose_aligned"])
    result = a.handle(
        command("resume", **base, expected_intent_revision=a.run["intent_revision"]), "owner"
    )
    assert result["accepted"], result
    until(lambda: (a.tick() or a.run["status"] == "running"))
    base["intent_revision"] = a.run["intent_revision"]
    return base


@pytest.mark.parametrize("physical_live_authority", [
    "physical-live-left-elbow", "physical-live-right-elbow",
], indirect=True)
def test_physical_live_selected_recipe_controls_only_its_frozen_joint(physical_live_authority):
    from examples.alohamini.am1_finite_task import JOINT_KEYS
    from tests.robots.test_am1_physical_executor import until
    from tools.am1_session_ipc import CONTROL_LIMIT, RESPONSE_LIMIT, encode, strict_json

    a, e, host, r = physical_live_authority
    right = a.run["recipe"] == "physical-live-right-elbow"
    joint, other = ("arm_right_elbow_flex.pos", "arm_left_elbow_flex.pos") if right else (
        "arm_left_elbow_flex.pos", "arm_right_elbow_flex.pos")
    view = a.snapshot()
    assert {item["recipe"] for item in view["live_elbow_recipes"]} == {
        "physical-live-left-elbow", "physical-live-right-elbow"}
    live = view["run"]["live_input"]
    assert live["joint"] == joint and live["side"] == ("right" if right else "left")
    assert live["profile"] == ("LiveRightElbow" if right else "LiveLeftElbow")
    assert live["rate_norm_per_s"] == 1.0
    assert (live["corridor_min_norm"], live["corridor_max_norm"]) == pytest.approx(
        (((1834-827)/2212*200-100, (1867-827)/2212*200-100) if right else (-10.0, 0.0)))
    if right:
        assert live["admission_version"] == 2
        assert live["workspace"]["id"] == "am1-right-elbow-rest-neighbourhood-v2"
        assert live["measured_origin_raw"] == 1845
    base = physical_live_controls(a, r)
    other_recipe = "physical-live-left-elbow" if right else "physical-live-right-elbow"
    assert not a.handle(command("start", operation_id=str(uuid.uuid4()), recipe=other_recipe,
                                controller_generation=base["controller_generation"]), "owner")["accepted"]
    grant = a.handle(command("grant", **base), "owner")
    invalid = command("input", **base, grant_id=grant["grant_id"], seq=1, target={other: 0.0})
    assert not a.handle(invalid, "owner")["accepted"]
    frame = command("input", **base, grant_id=grant["grant_id"], seq=1, target={joint: live["cursor_norm"] - .3})
    assert len(encode(frame, CONTROL_LIMIT)) < CONTROL_LIMIT
    assert a.handle(frame, "owner")["accepted"]
    until(lambda: host.control.live_snapshot().get("last_input_seq") == 1)
    assert all(host.control.accepted_action[k] == host.control.live_held[k] for k in JOINT_KEYS if k != joint)
    latest = [event[1] for event in host.robot.events if event[0] == "action"][-1]
    assert all(latest[k] == 0.0 for k in ("x.vel", "y.vel", "theta.vel", "lift_axis.vel"))
    a.tick()
    raw = encode({"id": str(uuid.uuid4()), "result": a.snapshot()})
    assert len(raw) < RESPONSE_LIMIT
    assert strict_json(raw)["result"]["run"]["recipe"] == (
        "physical-live-right-elbow" if right else "physical-live-left-elbow")


def test_physical_live_admission_refuses_native_joint_different_from_selected_recipe(tmp_path):
    from examples.alohamini.am1_session_contract import RECIPES
    from tests.robots.test_am1_live_native import ExecutorNativeHost
    from tests.robots.test_am1_physical_executor import executor, until

    host = ExecutorNativeHost()
    real_start = host.start
    host.start = lambda run, incarnation, recipe, cancel: real_start(
        run, incarnation, RECIPES["physical-live-left-elbow"], cancel)
    e = executor(host)
    a = SessionAuthority(tmp_path, executor=e)
    try:
        r = start(a, "physical-live-right-elbow")
        assert r["accepted"]
        until(lambda: e.evidence()["lifecycle"]["phase"] == "live")
        a.tick()
        assert a.run["first_cause"] == "physical_live_recipe_mismatch"
        assert a.run["status"] in {"finishing", "faulted"}
        assert a.run["intent"] is None
    finally:
        a.close()
        e.worker.join(2)


@pytest.mark.parametrize("foreign", [None, "run_id", "host_incarnation", "intent_revision", "action_sequence"])
def test_physical_live_bounded_native_write_proof_requires_current_ack_identity(
    physical_live_authority, foreign
):
    import copy

    from tools.am1_session_ipc import RESPONSE_LIMIT, encode, strict_json

    a, e, host, r = physical_live_authority
    evidence = copy.deepcopy(e.evidence())
    acknowledgment = evidence["accepted_host_action"]
    evidence["live_input"].update(
        measured_origin_raw=2000, corridor_min_raw=1889, corridor_max_raw=2000, accepted_target_norm=-0.3,
        encoded_goal_raw=1996, goal_write_returned=True,
        goal_write_started_at_monotonic_s=10.0, goal_write_completed_at_monotonic_s=10.01,
        goal_action_sequence=acknowledgment["accepted_action_sequence"],
        rejected_bulk={"data": "x" * 100000},
    )
    if foreign == "action_sequence":
        evidence["live_input"]["goal_action_sequence"] += 1
    elif foreign is not None:
        acknowledgment[foreign] = "foreign" if foreign != "intent_revision" else a.run["intent_revision"] - 1
    projected = a._live_input_status(evidence)
    view = a.snapshot()
    view["run"]["live_input"] = projected
    view["evidence"]["live_input"] = projected
    raw = encode({"id": str(uuid.uuid4()), "result": view})
    assert len(raw) < RESPONSE_LIMIT and "rejected_bulk" not in strict_json(raw)["result"]["run"]["live_input"]
    assert projected["measured_origin_raw"] == 2000
    assert projected["accepted_target_norm"] == -0.3
    if foreign is None:
        assert projected["encoded_goal_raw"] == 1996
        assert projected["goal_write_returned"] is True
        assert projected["goal_write_completed_at_monotonic_s"] == 10.01
        assert projected["goal_action_sequence"] == acknowledgment["accepted_action_sequence"]
    else:
        assert "encoded_goal_raw" not in projected and "goal_write_returned" not in projected


def test_physical_live_grant_renewal_preserves_sample_until_original_expiry(physical_live_authority):
    import time

    a, e, host, r = physical_live_authority
    base = physical_live_controls(a, r)
    deadline = a.run["deadline"]
    grant = a.handle(command("grant", **base), "owner")
    assert grant["accepted"]
    accepted = a.handle(command("input", **base, grant_id=grant["grant_id"], seq=1,
                                target={"arm_left_elbow_flex.pos": -0.5}), "owner")
    assert accepted["accepted"] and accepted["effect_admitted"]
    time.sleep(0.08)
    before = len(host.actions)
    renewal = a.handle(command("grant", **base), "owner")
    assert renewal["accepted"]
    assert a.run["intent"] == {"arm_left_elbow_flex.pos": -0.5}
    assert a.run["status"] == "running"
    assert a.run["first_cause"] is None
    time.sleep(0.07)
    assert all(action["_am1_local_control"]["mode"] == "active" for action in host.actions[before:])
    time.sleep(max(0, grant["expires"] - time.monotonic()) + 0.03)
    a.tick()
    assert a.run["status"] == "paused"
    assert a.run["intent"] is None
    assert a.run["first_cause"] is None
    assert a.snapshot()["run"]["deadline"] == deadline


@pytest.mark.parametrize("physical_live_authority", [
    "physical-live-left-elbow", "physical-live-right-elbow",
], indirect=True)
def test_physical_live_reacquisition_retains_reference_and_deadline(physical_live_authority):
    import time

    a, e, host, r = physical_live_authority
    right = a.run["recipe"] == "physical-live-right-elbow"
    joint = "arm_right_elbow_flex.pos" if right else "arm_left_elbow_flex.pos"
    base = physical_live_controls(a, r)
    initial = a.snapshot()["run"]
    grant = a.handle(command("grant", **base), "owner")
    assert a.handle(command("input", **base, grant_id=grant["grant_id"], seq=1,
                            target={joint: initial["live_input"]["cursor_norm"] - .5}), "owner")["accepted"]
    time.sleep(max(0, grant["expires"] - time.monotonic()) + 0.03)
    a.tick()
    assert a.run["status"] == "paused"
    fresh = physical_live_controls(a, r)
    current = a.snapshot()["run"]
    assert current["run_id"] == initial["run_id"]
    assert current["deadline"] == initial["deadline"]
    assert current["live_input"]["reference_norm"] == initial["live_input"]["reference_norm"]
    assert current["live_input"]["corridor_min_norm"] == initial["live_input"]["corridor_min_norm"]
    assert current["live_input"]["cursor_intent_revision"] == fresh["intent_revision"]
    assert current["live_input"]["cursor_norm"] == initial["live_input"]["reference_norm"]
    assert not a.handle(command("input", **base, grant_id=grant["grant_id"], seq=2,
                                target={joint: -1.0}), "owner")["accepted"]
    new_grant = a.handle(command("grant", **fresh), "owner")
    assert a.handle(command("input", **fresh, grant_id=new_grant["grant_id"], seq=1,
                            target={joint: current["live_input"]["cursor_norm"] - .2}), "owner")["accepted"]
    assert e.task is None and e.worker.is_alive()


@pytest.mark.parametrize("before_first_sample", [False, True])
def test_physical_live_native_watchdog_hold_requires_same_run_resume(
    physical_live_authority, before_first_sample
):
    from tests.robots.test_am1_physical_executor import until

    a, e, host, r = physical_live_authority
    base = physical_live_controls(a, r)
    until(lambda: a.snapshot()["evidence"]["active_acknowledged"])
    initial = a.snapshot()["run"]
    if not before_first_sample:
        grant = a.handle(command("grant", **base), "owner")
        assert a.handle(command("input", **base, grant_id=grant["grant_id"], seq=1,
                                target={"arm_left_elbow_flex.pos": -0.5}), "owner")["accepted"]
        until(lambda: host.control.live_snapshot().get("last_input_seq") == 1)
    # Invoke the production native watchdog against simulated motor IO. Its fresh
    # measured hold retains sensing/native ACK while revoking the active epoch.
    with e.mutex:
        host.control.watchdog_stop(host.robot)
    until(lambda: e.evidence()["hold_acknowledged"])
    evidence = e.evidence()
    assert all(evidence[k] for k in ("feedback", "required_observation", "native_ack"))
    assert not evidence["active_acknowledged"]
    a.tick()
    assert a.run["status"] == "recovering"
    assert a.run["first_cause"] == "native_active_ack_loss"
    until(lambda: (a.tick() or a.run["status"] == "paused"))
    assert a.run["intent"] is None
    assert not a.handle(command("grant", **base), "owner")["accepted"]
    assert a.handle(command("release_input", **base), "owner")["accepted"]
    until(lambda: e.evidence()["pose_aligned"])
    assert a.handle(command("claim", controller_generation=base["controller_generation"]), "owner")["accepted"]
    resumed = a.handle(command("resume", **base, expected_intent_revision=a.run["intent_revision"]), "owner")
    assert resumed["accepted"], resumed
    until(lambda: (a.tick() or a.run["status"] == "running"))
    base["intent_revision"] = a.run["intent_revision"]
    fresh = a.handle(command("grant", **base), "owner")
    assert a.handle(command("input", **base, grant_id=fresh["grant_id"], seq=2,
                            target={"arm_left_elbow_flex.pos": -0.2}), "owner")["accepted"]
    until(lambda: host.control.live_snapshot().get("last_input_seq") == 2)
    current = a.snapshot()["run"]
    assert current["run_id"] == initial["run_id"]
    assert current["native_live_at"] == initial["native_live_at"]
    assert current["deadline"] == initial["deadline"]
    assert current["live_input"]["reference_norm"] == initial["live_input"]["reference_norm"]
    assert current["first_cause"] == "native_active_ack_loss"


def test_physical_live_resume_initial_native_transition_does_not_pause(physical_live_authority):
    from tests.robots.test_am1_physical_executor import until

    a, e, host, r = physical_live_authority
    snapshot = a.snapshot()
    base = dict(run_id=r["run_id"], service_incarnation=a.service_incarnation,
                controller_generation=snapshot["controller"]["controller_generation"],
                host_incarnation=e.host_incarnation)
    connected = a.handle(command("connect", **base), "owner")
    base["connection_generation"] = connected["connection_generation"]
    assert a.handle(command("release_input", **base), "owner")["accepted"]
    until(lambda: e.evidence()["pose_aligned"])
    assert a.handle(command("resume", **base, expected_intent_revision=a.run["intent_revision"]), "owner")["accepted"]

    def observe_initial_transition():
        # Fence only the simulated worker so Core can inspect the exact held
        # predecessor ACK immediately after admitting dispatch, before the send.
        with e.mutex:
            a.tick()
            if a.run["status"] != "running":
                return False
            evidence = e.evidence()
            assert evidence["native_ack"] and not evidence["active_acknowledged"]
            a.tick()
            assert a.run["status"] == "running"
            assert a.run["first_cause"] is None
            return True

    until(observe_initial_transition)
    until(lambda: e.evidence()["active_acknowledged"])
    a.tick()
    assert a.run["status"] == "running"


def test_physical_live_resume_cannot_rebase_transition_on_post_dispatch_watchdog(
    physical_live_authority, monkeypatch
):
    from tests.robots.test_am1_physical_executor import until

    a, e, host, r = physical_live_authority
    snapshot = a.snapshot()
    base = dict(run_id=r["run_id"], service_incarnation=a.service_incarnation,
                controller_generation=snapshot["controller"]["controller_generation"],
                host_incarnation=e.host_incarnation)
    connected = a.handle(command("connect", **base), "owner")
    base["connection_generation"] = connected["connection_generation"]
    assert a.handle(command("release_input", **base), "owner")["accepted"]
    until(lambda: e.evidence()["pose_aligned"])
    assert a.handle(command("claim", controller_generation=base["controller_generation"]), "owner")["accepted"]
    initial = a.snapshot()["run"]
    real_save, real_dispatch = a._save, e.dispatch
    admitted_predecessor = []

    def delayed_save():
        real_save()
        if a.run["status"] == "running" and a.run["dispatch"] == "dispatching":
            # A slow durable checkpoint can let the revised measured hold
            # qualify before the first Resume dispatch, without mocking its ACK.
            until(lambda: e.evidence()["pose_aligned"])

    def dispatch_then_watchdog(recipe):
        admitted_predecessor.append(e.evidence()["accepted_host_action"]["epoch"])
        admitted = real_dispatch(recipe)
        assert admitted
        until(lambda: e.evidence()["active_acknowledged"])
        with e.mutex:
            host.control.watchdog_stop(host.robot)
        until(lambda: e.evidence()["hold_acknowledged"])
        return admitted

    monkeypatch.setattr(a, "_save", delayed_save)
    monkeypatch.setattr(e, "dispatch", dispatch_then_watchdog)
    result = a.handle(command("resume", **base, expected_intent_revision=a.run["intent_revision"]), "owner")
    assert result["accepted"] and result["effect_admitted"]
    assert e.evidence()["accepted_host_action"]["epoch"] == admitted_predecessor[0] + 2
    a.tick()
    assert a.run["status"] == "recovering"
    assert a.run["first_cause"] == "native_active_ack_loss"
    assert not a.handle(command("grant", **base, intent_revision=a.run["intent_revision"]), "owner")["accepted"]
    assert a.run["deadline"] == initial["deadline"]
    assert a.run["native_live_at"] == initial["native_live_at"]


def test_physical_live_progress_is_retained_in_bounded_views_and_terminal(physical_live_authority):
    import json

    from tests.robots.test_am1_physical_executor import until
    from tools.am1_session_ipc import RESPONSE_LIMIT, encode, strict_json

    a, e, host, r = physical_live_authority
    physical_live_controls(a, r)
    until(lambda: e.advance(0, 0)["progress_s"] > 0.1)
    a.tick()
    assert a.run["progress_s"] > 0.1
    assert a.run["execution"]["profile"] == "LiveLeftElbow"
    for view in (a.snapshot(),):
        raw = encode({"id": str(uuid.uuid4()), "result": view})
        assert len(raw) < RESPONSE_LIMIT
        run = strict_json(raw)["result"]["run"]
        assert run["progress_s"] > 0.1
        assert run["execution"]["profile"] == "LiveLeftElbow"
        assert run["execution"]["complete"] is False
    assert a.handle(command("stop", run_id=r["run_id"]), "owner")["accepted"]
    until(lambda: (a.tick() or a.run["status"] == "stopped"))
    assert a.run["progress_s"] > 0.1
    assert a.run["execution"]["profile"] == "LiveLeftElbow"
    retained = json.loads(a.db.execute("SELECT data FROM state WHERE id=1").fetchone()[0])
    assert retained["execution"] == a.run["execution"]
    assert retained["cleanup"] == "physical_readback_verified"


@pytest.mark.parametrize("changes", [
    {"seq": True}, {"target": [0.5]}, {"target": {"arm_right_elbow_flex.pos": -0.5}},
    {"target": {"arm_left_elbow_flex.pos": -0.5, "arm_right_elbow_flex.pos": 7.0}},
    {"target": {"arm_left_elbow_flex.pos": True}}, {"intent_revision": 99},
    {"host_incarnation": str(uuid.uuid4())}, {"valid_until_monotonic_s": 9999999999.0},
])
def test_physical_live_refuses_unqualified_target_and_identity(physical_live_authority, changes):
    a, e, host, r = physical_live_authority
    base = physical_live_controls(a, r)
    grant = a.handle(command("grant", **base), "owner")
    frame = command("input", **base, grant_id=grant["grant_id"], seq=1,
                    target={"arm_left_elbow_flex.pos": -0.5})
    frame.update(changes)
    assert not a.handle(frame, "owner")["accepted"]
    assert a.run["intent"] is None


def test_physical_live_connect_and_pending_frames_cannot_cross_pause(physical_live_authority):
    a, e, host, r = physical_live_authority
    base = physical_live_controls(a, r)
    connection = a.connection["generation"]
    stale = dict(base, controller_generation=str(uuid.uuid4()))
    assert not a.handle(command("connect", **stale), "owner")["accepted"]
    assert a.connection["generation"] == connection
    grant = a.handle(command("grant", **base), "owner")
    assert a.handle(command("pause", run_id=r["run_id"]), "owner")["accepted"]
    assert not a.handle(command("grant", **base), "owner")["accepted"]
    assert not a.handle(command("input", **base, grant_id=grant["grant_id"], seq=1,
                                target={"arm_left_elbow_flex.pos": -0.5}), "owner")["accepted"]
    assert a.run["status"] == "paused"
    assert a.run["intent"] is None

def test_physical_live_explicit_recipe_has_bounded_native_lifetime():
    from examples.alohamini.am1_session_contract import RECIPES

    recipe = RECIPES.get("physical-live-left-elbow")
    assert recipe is not None, "explicit physical live selection is missing"
    assert recipe.mode == "interactive"
    assert recipe.seed is None
    assert recipe.trajectory_s is None
    assert recipe.live_s == 90.0


def test_physical_live_release_requires_new_qualified_resume(physical_live_authority):
    a, e, host, r = physical_live_authority
    base = physical_live_controls(a, r)
    grant = a.handle(command("grant", **base), "owner")
    assert a.handle(command("input", **base, grant_id=grant["grant_id"], seq=1,
                            target={"arm_left_elbow_flex.pos": -0.5}), "owner")["accepted"]
    assert a.handle(command("release_input", **base), "owner")["accepted"]
    assert a.run["status"] == "paused"
    assert a.run["intent"] is None
    assert not a.handle(command("grant", **base), "owner")["accepted"]
    assert not a.handle(command("release_input", **base, target={}), "owner")["accepted"]


@pytest.mark.parametrize("late_op", ["connect", "release", "handoff", "release_input", "resume", "grant", "input"])
def test_physical_live_pending_stop_fences_late_controls_and_allows_next_start(physical_live_authority, late_op):
    from tests.robots.test_am1_physical_executor import until

    a, e, host, first = physical_live_authority
    base = physical_live_controls(a, first)
    grant = a.handle(command("grant", **base), "owner")
    frame = command(late_op, **base)
    if late_op == "handoff":
        frame["target_device_id"] = "replacement-controller"
    elif late_op == "resume":
        frame["expected_intent_revision"] = a.run["intent_revision"]
    elif late_op == "input":
        frame.update(grant_id=grant["grant_id"], seq=1, target={"arm_left_elbow_flex.pos": -0.5})
    # This existing test-only adapter RLock makes the cleanup scheduling window deterministic.
    # No physical process/device is involved; the worker uses simulated measured device IO.
    with e.mutex:
        stopped = a.handle(command("stop", run_id=first["run_id"]), "owner")
        assert stopped["accepted"] and stopped["status"] == "finishing"
        revision = a.run["intent_revision"]
        generation = a.controller["generation"]
        renewed = a.handle(command("claim", controller_generation=generation), "owner")
        assert renewed["accepted"]
        assert a.run["status"] == "finishing"
        assert a.run["requested_terminal"] == "stopped"
        refused = a.handle(frame, "owner")
        assert not refused["accepted"], refused
        assert a.run["status"] == "finishing"
        assert a.run["requested_terminal"] == "stopped"
        assert a.run["intent_revision"] == revision
        assert a.controller["generation"] == generation
        assert e.finish_status == "stopped"
    until(lambda: (a.tick() or a.run["status"] == "stopped"))
    until(lambda: not e.worker.is_alive())
    assert a.run["cleanup"] == "physical_readback_verified"
    assert a.run["uncertain"] is False
    assert a.run["physical_result"]["phase"] == "terminal"
    assert a.run["physical_result"]["run_id"] == first["run_id"]
    assert a.run["first_cause"] is None
    assert not a.handle(command("connect", **base), "owner")["accepted"]
    assert a.run["status"] == "stopped"
    assert a.handle(command("claim", controller_generation=generation), "owner")["accepted"]
    next_run = a.handle(command("start", operation_id=str(uuid.uuid4()), recipe="physical-live-left-elbow",
                                controller_generation=generation), "owner")
    assert next_run["accepted"], next_run
    assert next_run["run_id"] != first["run_id"]
    until(lambda: (a.tick() or a.run["status"] == "paused"))
    assert a.run["native_live_at"] is not None
    assert a.run["deadline"] - a.run["native_live_at"] == 90.0


@pytest.mark.parametrize("cached_native_fault", [None, "retained native motor/feedback fault"])
def test_physical_live_expired_presence_claim_and_pause_preserve_finishing(physical_live_authority, cached_native_fault):
    import time

    from tests.robots.test_am1_physical_executor import until

    a, e, host, first = physical_live_authority
    physical_live_controls(a, first)
    with e.mutex:
        # Exercise the authority's existing cached-native-fault contract with simulated IO.
        e._fault = cached_native_fault
        stopped = a.handle(command("stop", run_id=first["run_id"]), "owner")
        assert stopped["accepted"] and stopped["status"] == "finishing"
        revision = a.run["intent_revision"]
        old_generation = a.controller["generation"]
        time.sleep(max(0, a.controller["expires"] - a.clock()) + 0.02)
        a.tick()
        assert a.controller is None
        assert a.run["status"] == "finishing"
        assert a.run["requested_terminal"] == "stopped"
        assert a.run["first_cause"] == cached_native_fault
        claimed = a.handle(command("claim"), "replacement-controller")
        assert claimed["accepted"]
        assert claimed["controller_generation"] != old_generation
        assert a.controller["device_id"] == "replacement-controller"
        assert a.run["status"] == "finishing"
        assert a.run["intent_revision"] == revision
        assert not a.handle(command("pause", run_id=first["run_id"]), "replacement-controller")["accepted"]
        assert a.run["status"] == "finishing"
        assert a.run["requested_terminal"] == "stopped"
        assert a.run["first_cause"] == cached_native_fault
        assert e.finish_status == "stopped"
        assert e._fault == cached_native_fault
    until(lambda: (a.tick() or a.run["status"] == "stopped"))
    until(lambda: not e.worker.is_alive())
    assert a.run["cleanup"] == "physical_readback_verified"
    assert a.run["uncertain"] is False
    assert a.run["physical_result"]["phase"] == "terminal"
    assert a.run["physical_result"]["run_id"] == first["run_id"]
    assert a.run["first_cause"] == cached_native_fault
    assert e._fault == cached_native_fault


@pytest.mark.parametrize("physical_live_authority", [
    "physical-live-left-elbow", "physical-live-right-elbow",
], indirect=True)
def test_live_voluntary_release_new_gesture_qualified_rearm(physical_live_authority):
    from tests.robots.test_am1_physical_executor import until

    a, e, host, r = physical_live_authority
    base = physical_live_controls(a, r)
    until(lambda: (a.tick() or a.snapshot()["evidence"].get("active_acknowledged") is True))
    before = a.snapshot()["run"]
    value = before["live_input"]
    joint = value["joint"]
    grant = a.handle(command("grant", **base), "owner")
    target = max(value["corridor_min_norm"], min(value["cursor_norm"] - 0.2, value["corridor_max_norm"]))
    assert a.handle(command("input", **base, grant_id=grant["grant_id"], seq=1,
                            target={joint: target}), "owner")["accepted"]
    released = a.handle(command("release_input", **base, release_kind="voluntary_jog"), "owner")
    assert released["accepted"], released
    revision = a.run["intent_revision"]
    assert a.snapshot()["run"]["live_input"]["rearm"] == {
        "eligible": True, "released_intent_revision": revision,
    }
    assert a.run["intent"] is None and a.run["status"] == "paused"
    a.tick()
    assert "rearm" not in a.run["live_input"]  # A volatile gesture permission is never durable history.
    assert not a.handle(command("grant", **base), "owner")["accepted"]
    connected = a.handle(command("connect", **base, rearm_of_intent_revision=revision), "owner")
    assert connected["accepted"], connected
    fresh = dict(base, connection_generation=connected["connection_generation"])
    fresh.pop("intent_revision")
    assert a.handle(command("release_input", **fresh, rearm_of_intent_revision=revision), "owner")["accepted"]
    until(lambda: (a.tick() or a.snapshot()["evidence"].get("pose_aligned") is True))
    resumed = a.handle(command("resume", **fresh, expected_intent_revision=revision + 1,
                               rearm_of_intent_revision=revision), "owner")
    assert resumed["accepted"], resumed
    assert a.run["intent_revision"] == revision + 2
    until(lambda: (a.tick() or a.snapshot()["evidence"].get("active_acknowledged") is True))
    after = a.snapshot()["run"]
    for key in ("run_id", "deadline", "native_live_at", "recovery_episodes"):
        assert after[key] == before[key]
    for key in ("reference_norm", "corridor_min_norm", "corridor_max_norm"):
        assert after["live_input"][key] == value[key]
    assert after["live_input"]["rearm"]["eligible"] is False
    assert a.run["intent"] is None  # Rearm is not an input or replay.
    fresh["intent_revision"] = a.run["intent_revision"]
    grant = a.handle(command("grant", **fresh), "owner")
    assert a.handle(command("input", **fresh, grant_id=grant["grant_id"], seq=1,
                            target={joint: target}), "owner")["accepted"]


@pytest.mark.parametrize("loss", ["pause", "plain_release", "expiry", "foreign_connect", "wrong_revision"])
def test_live_rearm_cannot_cross_strict_loss(physical_live_authority, loss):
    import time
    from tests.robots.test_am1_physical_executor import until

    a, e, host, r = physical_live_authority
    base = physical_live_controls(a, r)
    until(lambda: (a.tick() or a.snapshot()["evidence"].get("active_acknowledged") is True))
    grant = a.handle(command("grant", **base), "owner")
    assert a.handle(command("input", **base, grant_id=grant["grant_id"], seq=1,
                            target={"arm_left_elbow_flex.pos": -0.2}), "owner")["accepted"]
    if loss == "expiry":
        time.sleep(max(0, grant["expires"] - time.monotonic()) + 0.02)
        a.tick()
    released = a.handle(command("release_input", **base, release_kind="voluntary_jog"), "owner")
    assert released["accepted"], released
    revision = a.run["intent_revision"]
    if loss == "pause":
        assert a.handle(command("pause", run_id=r["run_id"]), "another-viewer")["accepted"]
    elif loss == "plain_release":
        assert a.handle(command("release_input", **base), "owner")["accepted"]
    elif loss == "foreign_connect":
        explicit = a.handle(command("connect", **base), "owner")
        assert explicit["accepted"]
    elif loss == "wrong_revision":
        revision -= 1
    auto = a.handle(command("connect", **base, rearm_of_intent_revision=revision), "owner")
    assert not auto["accepted"], (loss, auto)
    assert a.run["status"] == "paused" and a.run["intent"] is None


def test_live_initial_release_cannot_claim_ordinary_rearm(physical_live_authority):
    a, e, host, r = physical_live_authority
    s = a.snapshot()
    base = dict(run_id=r["run_id"], service_incarnation=s["service_incarnation"],
                controller_generation=s["controller"]["controller_generation"],
                host_incarnation=s["evidence"]["host_incarnation"])
    connected = a.handle(command("connect", **base), "owner")
    base["connection_generation"] = connected["connection_generation"]
    base["intent_revision"] = a.run["intent_revision"]
    assert a.handle(command("release_input", **base, release_kind="voluntary_jog"), "owner")["accepted"]
    assert a.snapshot()["run"]["live_input"]["rearm"]["eligible"] is False
    assert not a.handle(command("connect", **base, rearm_of_intent_revision=a.run["intent_revision"]), "owner")["accepted"]


@pytest.mark.parametrize("receipt", ["cached-active", "expired-paused", "paused-native"])
def test_voluntary_release_cannot_reclassify_expiry_during_feedback(physical_live_authority, monkeypatch, receipt):
    import copy
    import time
    from tests.robots.test_am1_physical_executor import until

    a, e, host, r = physical_live_authority
    base = physical_live_controls(a, r)
    until(lambda: (a.tick() or a.snapshot()["evidence"].get("active_acknowledged") is True))
    grant = a.handle(command("grant", **base), "owner")
    assert a.handle(command("input", **base, grant_id=grant["grant_id"], seq=1,
                            target={"arm_left_elbow_flex.pos": -.2}), "owner")["accepted"]
    evidence = a._evidence
    calls = []
    def delayed_feedback():
        calls.append(time.monotonic())
        value = copy.deepcopy(evidence())
        # The public command ticks first, then reads evidence inside release classification.
        if len(calls) == 2:
            assert time.monotonic() < grant["expires"]
            if receipt != "paused-native":
                time.sleep(grant["expires"] - time.monotonic() + .03)
            if receipt != "cached-active":
                value["active_acknowledged"] = False
                value["accepted_host_action"]["state"] = "paused"
                value["live_input"]["state"] = "expired"
        return value
    monkeypatch.setattr(a, "_evidence", delayed_feedback)
    result = a.handle(command("release_input", **base, release_kind="voluntary_jog"), "owner")
    assert result["accepted"], result  # Holding remains available.
    assert len(calls) >= 2
    if receipt != "paused-native":
        assert time.monotonic() >= grant["expires"]
    assert a.run["status"] == "paused" and a.run["intent"] is None
    assert not a.snapshot()["run"]["live_input"]["rearm"]["eligible"]
    assert not a.handle(command("connect", **base, rearm_of_intent_revision=a.run["intent_revision"]), "owner")["accepted"]


def physical_live_returned_write(a, e, host, r, monkeypatch):
    """Real native receipt path; only the existing simulated robot's helper return is injected."""
    import math
    import time

    from examples.alohamini.am1_session_contract import LIVE_ELBOW_SPECS
    from tests.robots.test_am1_physical_executor import until

    spec = LIVE_ELBOW_SPECS[a.run["recipe"]]
    original = host.robot.send_action

    def returned(action):
        began = time.monotonic()
        result = original(action)
        binding = getattr(host.robot, "_am1_live_action_binding", None)
        if binding:
            minimum, span = (827, 2212) if spec.side == "right" else (864, 2209)
            host.robot._am1_selected_joint_action = {
                **binding, "side": spec.side, "motor": spec.joint.removesuffix(".pos"),
                "expected_goal_raw": math.floor((action[spec.joint] + 100) / 200 * span + minimum),
                "sync_write_returned": True, "write_started_at_monotonic_s": began,
                "write_completed_at_monotonic_s": time.monotonic(),
            }
        return result

    monkeypatch.setattr(host.robot, "send_action", returned)
    base = physical_live_controls(a, r)
    live = a.snapshot()["run"]["live_input"]
    grant = a.handle(command("grant", **base), "owner")
    target = max(live["corridor_min_norm"], live["cursor_norm"] - .2)
    assert a.handle(command("input", **base, grant_id=grant["grant_id"], seq=0,
                            target={spec.joint: target}), "owner")["accepted"]
    until(lambda: e.evidence().get("live_input", {}).get("last_successful_write"))
    return base, grant


def retained_live_write_views(a, expected):
    import base64
    import hashlib
    import json

    from tools.am1_session_ipc import RESPONSE_LIMIT, encode

    durable = json.loads(a.db.execute("SELECT data FROM state WHERE id=1").fetchone()[0])
    assert durable["live_input"]["last_successful_write"] == expected
    view = a.snapshot()
    assert len(encode({"id": str(uuid.uuid4()), "result": view})) < RESPONSE_LIMIT
    assert view["run"]["live_input"]["last_successful_write"] == expected
    assert view["evidence"]["live_input"]["last_successful_write"] == expected
    reference = view["run"]["details_reference"]
    raw = bytearray()
    while len(raw) < reference["bytes"]:
        reply = a.snapshot_detail(dict(op="snapshot", reference=reference, offset=len(raw), limit=8192))
        raw.extend(base64.b64decode(reply["data"], validate=True))
    assert hashlib.sha256(raw).hexdigest() == reference["sha256"]
    assert json.loads(raw)["live_input"]["last_successful_write"] == expected


@pytest.mark.parametrize("physical_live_authority", [
    "physical-live-left-elbow", "physical-live-right-elbow",
], indirect=True)
def test_live_write_history_survives_core_expiry_revision_and_terminal(physical_live_authority, monkeypatch):
    import copy

    from tests.robots.test_am1_physical_executor import until

    a, e, host, r = physical_live_authority
    base, grant = physical_live_returned_write(a, e, host, r, monkeypatch)
    deadline, revision = a.run["deadline"], a.run["intent_revision"]
    until(lambda: e.evidence()["live_input"]["state"] == "expired")
    expected = copy.deepcopy(e.evidence()["live_input"]["last_successful_write"])
    assert expected["valid_until_monotonic_s"] == grant["expires"]
    a.tick()  # Expiry advances revision and clears the executor's expired-evidence cache.
    assert a.run["status"] == "paused" and a.run["intent_revision"] > revision
    assert a.run["deadline"] == deadline
    retained_live_write_views(a, expected)
    until(lambda: e.evidence().get("acknowledged_intent_revision") == a.run["intent_revision"])
    projected = a.snapshot()["run"]["live_input"]
    current = {"encoded_goal_raw", "goal_write_returned", "goal_write_started_at_monotonic_s",
               "goal_write_completed_at_monotonic_s", "goal_action_sequence"}
    assert not current.intersection(projected)
    assert not a.handle(command("grant", **dict(base, intent_revision=a.run["intent_revision"])), "owner")["accepted"]
    # Historical success alone cannot qualify a current Resume or ordinary rearm.
    stale = copy.deepcopy(e.evidence())
    stale.update(feedback=False, required_observation=False, native_ack=False, pose_aligned=False)
    with monkeypatch.context() as patch:
        patch.setattr(e, "evidence", lambda: copy.deepcopy(stale))
        ids = dict(base, intent_revision=a.run["intent_revision"])
        assert a.handle(command("release_input", **ids, release_kind="voluntary_jog"), "owner")["accepted"]
        assert a.snapshot()["run"]["live_input"]["rearm"]["eligible"] is False
        assert not a.handle(command("resume", **ids, expected_intent_revision=a.run["intent_revision"]), "owner")["accepted"]
        assert a.snapshot()["evidence"]["native_ack"] is False
    assert a.handle(command("stop", run_id=r["run_id"]), "owner")["accepted"]
    until(lambda: (a.tick() or a.run["status"] == "stopped"))
    retained_live_write_views(a, expected)


@pytest.mark.parametrize("physical_live_authority", [
    "physical-live-left-elbow", "physical-live-right-elbow",
], indirect=True)
def test_live_write_history_stop_before_checkpoint_retains_native_copy(physical_live_authority, monkeypatch):
    import copy

    from tests.robots.test_am1_physical_executor import until

    a, e, host, r = physical_live_authority
    physical_live_returned_write(a, e, host, r, monkeypatch)
    assert "last_successful_write" not in a.run.get("live_input", {})
    original, seen = e.evidence, []

    def observed():
        value = original()
        receipt = value.get("live_input", {}).get("last_successful_write")
        if receipt:
            seen.append(copy.deepcopy(receipt))
        return value

    monkeypatch.setattr(e, "evidence", observed)
    assert a.handle(command("stop", run_id=r["run_id"]), "owner")["accepted"]
    until(lambda: (a.tick() or a.run["status"] == "stopped"))
    assert seen, "protective Stop must capture cached native history without a pre-command tick"
    expected = seen[-1]
    retained_live_write_views(a, expected)


@pytest.mark.parametrize("physical_live_authority", [
    "physical-live-left-elbow", "physical-live-right-elbow",
], indirect=True)
def test_live_write_history_validation_copies_closed_native_scalars(physical_live_authority, monkeypatch):
    import copy
    import time

    a, e, host, r = physical_live_authority
    physical_live_returned_write(a, e, host, r, monkeypatch)
    evidence = copy.deepcopy(e.evidence())
    expected = copy.deepcopy(evidence["live_input"]["last_successful_write"])
    projected = a._live_input_status(evidence)
    assert projected["last_successful_write"] == expected
    assert len(__import__("json").dumps(expected)) < 2048
    projected["last_successful_write"]["encoded_goal_raw"] = -1
    assert evidence["live_input"]["last_successful_write"] == expected
    # First validate the positive fixture, then reject every malformed replacement
    # without relabeling or losing this same-run retained record.
    a.run["live_input"] = a._live_input_status(evidence, include_rearm=False)
    corruptions = {
        "foreign_run": {"run_id": str(uuid.uuid4())}, "foreign_host": {"host_incarnation": str(uuid.uuid4())},
        "foreign_service": {"service_incarnation": str(uuid.uuid4())}, "wrong_joint": {"joint": "arm_left_wrist_flex.pos"},
        "wrong_side": {"side": "other"}, "future_revision": {"intent_revision": a.run["intent_revision"] + 1},
        "bad_owner": {"controller_generation": "not-uuid"}, "bad_connection": {"connection_generation": "NOT-UUID"},
        "bad_grant": {"grant_id": "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"}, "wrong_version": {"version": True},
        "wrong_provenance": {"provenance": "client"}, "unreturned": {"goal_write_returned": False},
        "nan": {"accepted_target_norm": float("nan")}, "infinity": {"valid_until_monotonic_s": float("inf")},
        "bool_sequence": {"last_input_seq": True}, "negative_epoch": {"epoch": -1},
        "negative_raw": {"encoded_goal_raw": -1}, "outside_norm": {"accepted_target_norm": 101},
        "future_write": {"goal_write_completed_at_monotonic_s": time.monotonic() + 100},
        "expired_before_write": {"valid_until_monotonic_s": expected["goal_write_started_at_monotonic_s"]},
        "unordered": {"goal_write_started_at_monotonic_s": expected["goal_write_completed_at_monotonic_s"] + 1},
        "bulk": {"unexpected": ["bulk"] * 10000}, "nested": {"grant_id": {"nested": "bad"}},
    }
    for label, changes in corruptions.items():
        bad = copy.deepcopy(evidence)
        bad["live_input"]["last_successful_write"].update(changes)
        assert a._live_input_status(bad)["last_successful_write"] == expected, label
        previous = a.run.pop("live_input")
        assert "last_successful_write" not in a._live_input_status(bad), label
        a.run["live_input"] = previous
    cleared = dict(evidence, live_input={"state": "held", "joint": expected["joint"]})
    assert a._live_input_status(cleared)["last_successful_write"] == expected
    old = copy.deepcopy(expected)
    old.update(intent_revision=0, controller_generation=str(uuid.uuid4()), connection_generation=str(uuid.uuid4()))
    cleared["live_input"]["last_successful_write"] = old
    assert a._live_input_status(cleared)["last_successful_write"] == old
    # Expiry is not a receipt TTL: a returned write may complete after its old grant.
    # A schema-only timing copy exercises a completion straddling the original expiry.
    old["goal_write_started_at_monotonic_s"] = old["goal_write_completed_at_monotonic_s"] - .002
    old["valid_until_monotonic_s"] = old["goal_write_completed_at_monotonic_s"] - .001
    assert a._live_input_status(cleared)["last_successful_write"] == old


def test_live_write_history_is_not_synthesized_without_a_receipt(physical_live_authority):
    from tests.robots.test_am1_physical_executor import until

    a, e, host, r = physical_live_authority
    assert "last_successful_write" not in a.snapshot()["run"]["live_input"]
    assert a.handle(command("stop", run_id=r["run_id"]), "owner")["accepted"]
    until(lambda: (a.tick() or a.run["status"] == "stopped"))
    assert "last_successful_write" not in (a.snapshot()["run"].get("live_input") or {})


def test_live_write_history_terminal_reopen_keeps_original_run_binding(physical_live_authority, monkeypatch):
    import copy

    from tests.robots.test_am1_live_native import ExecutorNativeHost
    from tests.robots.test_am1_physical_executor import executor, until

    a, e, host, r = physical_live_authority
    physical_live_returned_write(a, e, host, r, monkeypatch)
    until(lambda: e.evidence()["live_input"]["state"] == "expired")
    expected = copy.deepcopy(e.evidence()["live_input"]["last_successful_write"])
    a.tick()
    assert a.handle(command("stop", run_id=r["run_id"]), "owner")["accepted"]
    until(lambda: (a.tick() or a.run["status"] == "stopped"))
    a.close()
    replacement = executor(ExecutorNativeHost())
    restored = SessionAuthority(a.directory, executor=replacement)
    try:
        assert restored.service_incarnation != expected["service_incarnation"]
        assert replacement.host_incarnation != expected["host_incarnation"]
        assert restored.run["status"] == "stopped"
        retained_live_write_views(restored, expected)
        assert restored._live_input_status({})["last_successful_write"] == expected
    finally:
        restored.close()


def test_live_write_history_absent_native_keeps_only_immutable_admission(physical_live_authority, monkeypatch):
    import copy

    a, e, host, r = physical_live_authority
    physical_live_returned_write(a, e, host, r, monkeypatch)
    a.tick()
    stored = copy.deepcopy(a.run["live_input"])
    immutable = {"joint", "reference_norm", "corridor_min_norm", "corridor_max_norm", "measured_origin_raw",
                 "corridor_min_raw", "corridor_max_raw", "admission_version", "workspace",
                 "live_admitted_at_monotonic_s", "live_deadline_monotonic_s"}
    for evidence in ({}, {"live_input": {}}, {"live_input": {"state": "held", "joint": stored["joint"]}}):
        result = a._live_input_status(evidence)
        assert {k: result[k] for k in immutable if k in stored} == {k: stored[k] for k in immutable if k in stored}
        assert result["last_successful_write"] == stored["last_successful_write"]
        assert not {"cursor_norm", "cursor_feedback_sequence", "cursor_intent_revision",
                    "goal_write_returned", "encoded_goal_raw", "goal_action_sequence"}.intersection(result)
        if not evidence.get("live_input"):
            assert "rearm" not in result and "state" not in result
        if "workspace" in result:
            result["workspace"]["context_sha256"] = "changed"
            assert a.run["live_input"]["workspace"] == stored["workspace"]


def test_live_write_history_terminal_cannot_reproject_cached_current_write(physical_live_authority, monkeypatch):
    import copy

    a, e, host, r = physical_live_authority
    physical_live_returned_write(a, e, host, r, monkeypatch)
    evidence = copy.deepcopy(e.evidence())
    assert a._live_input_status(evidence)["goal_write_returned"] is True
    status = a.run["status"]
    try:
        for terminal in ("finishing", "stopped"):
            a.run["status"] = terminal
            result = a._live_input_status(evidence)
            assert "goal_write_returned" not in result and "encoded_goal_raw" not in result
            assert result["last_successful_write"] == evidence["live_input"]["last_successful_write"]
    finally:
        a.run["status"] = status


def test_live_write_history_clock_reset_retains_only_prevalidated_storage(physical_live_authority, monkeypatch):
    import copy

    a, e, host, r = physical_live_authority
    physical_live_returned_write(a, e, host, r, monkeypatch)
    evidence = copy.deepcopy(e.evidence())
    expected = copy.deepcopy(evidence["live_input"]["last_successful_write"])
    a.run["live_input"] = a._live_input_status(evidence, include_rearm=False)
    with monkeypatch.context() as patch:
        patch.setattr(a, "clock", lambda: 0.0)  # A later OS boot resets monotonic time.
        assert a._live_input_status({})["last_successful_write"] == expected
        saved = a.run["live_input"].pop("last_successful_write")
        assert "last_successful_write" not in a._live_input_status(evidence)
        a.run["live_input"]["last_successful_write"] = saved


@pytest.mark.parametrize("physical_live_authority", ["physical-live-right-elbow"], indirect=True)
def test_live_legacy_terminal_record_reopen_is_canonically_unchanged(physical_live_authority):
    import base64
    import hashlib
    import json
    import time

    from tests.robots.test_am1_live_native import ExecutorNativeHost
    from tests.robots.test_am1_physical_executor import executor, until
    from tools.am1_session_core import _canonical

    a, e, host, r = physical_live_authority
    assert a.handle(command("stop", run_id=r["run_id"]), "owner")["accepted"]
    until(lambda: (a.tick() or a.run["status"] == "stopped"))
    # Legacy d16 layout: zero inputs and a returned measured-hold write, without
    # an input's last_successful_write. Preserve this old terminal record exactly.
    now = time.monotonic()
    a.run["live_input"].update(last_input_seq=-1, encoded_goal_raw=1845, goal_write_returned=True,
                             goal_write_started_at_monotonic_s=now-.002,
                             goal_write_completed_at_monotonic_s=now-.001, goal_action_sequence=3)
    assert "last_successful_write" not in a.run["live_input"]
    a._save()
    expected = _canonical(a.run)
    a.close()
    replacement = executor(ExecutorNativeHost())
    restored = SessionAuthority(a.directory, executor=replacement)
    try:
        durable = restored.db.execute("SELECT data FROM state WHERE id=1").fetchone()[0]
        assert _canonical(json.loads(durable)) == expected
        assert _canonical(restored.run) == expected
        view = restored.snapshot()
        reference = view["run"]["details_reference"]
        raw = bytearray()
        while len(raw) < reference["bytes"]:
            reply = restored.snapshot_detail(dict(op="snapshot", reference=reference, offset=len(raw), limit=8192))
            raw.extend(base64.b64decode(reply["data"], validate=True))
        assert hashlib.sha256(raw).hexdigest() == reference["sha256"]
        assert _canonical(json.loads(raw)) == expected
        assert view["evidence"]["native_ack"] is False
        assert view["evidence"]["active_acknowledged"] is False
        assert restored.controller is None and restored.connection is None
        assert restored.grant is None and restored.applied_input is None
        assert replacement.worker is None
        assert not restored.handle(command("grant", run_id=r["run_id"]), "owner")["accepted"]
        assert _canonical(restored.run) == expected
    finally:
        restored.close()

@pytest.mark.parametrize("expired", [False, True])
def test_input_diagnostic_core_marks_are_separate_from_authority_clock(tmp_path, monkeypatch, expired):
    from tools import am1_session_core as core

    c = Clock()
    a = SessionAuthority(tmp_path, clock=c)
    try:
        run = start(a, "fake-interactive")
        base = interactive_controls(a, run)
        grant = a.handle(command("grant", **base), "owner")
        assert "diagnostic_timing" not in grant
        if expired:
            c.advance(.251)
        stamps = iter((100., 100.01, 100.03, 100.04))
        monkeypatch.setattr(core, "_input_diagnostic_clock", lambda: next(stamps), raising=False)
        result = a.handle(command("input", **base, grant_id=grant["grant_id"], seq=1, target=[.2]), "owner")
        assert result["accepted"] is (not expired)
        if expired:
            assert result["reason"] == "expired grant" and not result["effect_admitted"]
        assert result["diagnostic_timing"] == {
            "version": 1, "clock": "pi-monotonic", "core_entered": 100., "core_locked": 100.01,
            "core_tick_completed": 100.03, "core_returned": 100.04}
        assert c.now == (.251 if expired else 0.) and grant["expires"] == .25
        assert "diagnostic_timing" not in a.handle(command("claim", **base), "owner")
    finally:
        a.close()


@pytest.mark.parametrize("failure", [None, float("nan"), True, "raise"])
def test_input_diagnostic_failure_omits_only_diagnostics(tmp_path, monkeypatch, failure):
    from tools import am1_session_core as core

    c = Clock()
    a = SessionAuthority(tmp_path, clock=c)
    try:
        run = start(a, "fake-interactive")
        base = interactive_controls(a, run)
        grant = a.handle(command("grant", **base), "owner")
        def failed_clock():
            if failure == "raise":
                raise RuntimeError("diagnostic clock unavailable")
            return failure
        monkeypatch.setattr(core, "_input_diagnostic_clock", failed_clock, raising=False)
        result = a.handle(command("input", **base, grant_id=grant["grant_id"], seq=1, target=[.2]), "owner")
        assert result == {"accepted": True, "status": "input", "effect_admitted": True}
        assert a.run["intent"] == [.2] and c.now == 0.
    finally:
        a.close()

def test_input_diagnostic_marks_bound_actual_mutex_wait_and_tick(tmp_path, monkeypatch):
    import threading
    import time
    from tools import am1_session_core as core

    c = Clock()
    a = SessionAuthority(tmp_path, clock=c)
    entered = threading.Event()
    calls, results, errors, tick_bounds = [], [], [], []
    original_tick = a.tick
    def diagnostic():
        value = time.monotonic()
        calls.append(value)
        entered.set()
        return value
    def tick():
        tick_bounds.append(time.monotonic())
        original_tick()
        tick_bounds.append(time.monotonic())
    try:
        run = start(a, "fake-interactive")
        base = interactive_controls(a, run)
        grant = a.handle(command("grant", **base), "owner")
        monkeypatch.setattr(core, "_input_diagnostic_clock", diagnostic)
        monkeypatch.setattr(a, "tick", tick)
        def submit():
            try:
                results.append(a.handle(command("input", **base, grant_id=grant["grant_id"], seq=1, target=[.2]), "owner"))
            except BaseException as error:
                errors.append(error)
        with a.mutex:
            worker = threading.Thread(target=submit)
            worker.start()
            assert entered.wait(1) and len(calls) == 1 and not results
            released_at = time.monotonic()
        worker.join(2)
        assert not worker.is_alive() and not errors and results[0]["accepted"]
        span = results[0]["diagnostic_timing"]
        assert span["core_entered"] <= released_at <= span["core_locked"]
        assert span["core_locked"] <= tick_bounds[0] <= tick_bounds[1] <= span["core_tick_completed"] <= span["core_returned"]
        assert c.now == 0. and grant["expires"] == .25
    finally:
        a.close()


def test_input_diagnostic_failure_preserves_original_executor_exception(tmp_path, monkeypatch):
    from tools import am1_session_core as core

    c = Clock()
    a = SessionAuthority(tmp_path, clock=c)
    original = RuntimeError("original input failure")
    try:
        run = start(a, "fake-interactive")
        base = interactive_controls(a, run)
        grant = a.handle(command("grant", **base), "owner")
        def clock_failure():
            raise ValueError("diagnostic failure")
        def apply_failure(target):
            raise original
        monkeypatch.setattr(core, "_input_diagnostic_clock", clock_failure)
        monkeypatch.setattr(a.executor, "apply", apply_failure)
        with pytest.raises(RuntimeError) as raised:
            a.handle(command("input", **base, grant_id=grant["grant_id"], seq=1, target=[.2]), "owner")
        assert raised.value is original
    finally:
        a.close()