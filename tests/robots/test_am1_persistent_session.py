"""Fake authority behavior tests; no physical adapters."""

import uuid

import pytest

from tools.am1_fake_executor import FakeExecutor
from tools.am1_session_core import SessionAuthority


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
    assert not a.handle(command("resume", **base), "owner")["accepted"]
    assert a.handle(command("release_input", **base), "owner")["accepted"]
    assert a.handle(command("resume", **base), "owner")["accepted"]
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
    a.handle(command("resume", **base), "owner")
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
    assert a.handle(command("resume", **base), "owner")["accepted"]
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
