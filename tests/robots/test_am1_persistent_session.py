"""Authority behavior with fake executors and real physical adapters using simulated device IO."""

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
def physical_live_authority(tmp_path):
    from tests.robots.test_am1_live_native import ExecutorNativeHost
    from tests.robots.test_am1_physical_executor import executor, until

    host = ExecutorNativeHost()
    e = executor(host)
    a = SessionAuthority(tmp_path, executor=e)
    try:
        r = start(a, "physical-live-left-elbow")
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


def test_physical_live_reacquisition_retains_reference_and_deadline(physical_live_authority):
    import time

    a, e, host, r = physical_live_authority
    base = physical_live_controls(a, r)
    initial = a.snapshot()["run"]
    grant = a.handle(command("grant", **base), "owner")
    assert a.handle(command("input", **base, grant_id=grant["grant_id"], seq=1,
                            target={"arm_left_elbow_flex.pos": -0.5}), "owner")["accepted"]
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
    assert current["live_input"]["cursor_norm"] == 0.0
    assert not a.handle(command("input", **base, grant_id=grant["grant_id"], seq=2,
                                target={"arm_left_elbow_flex.pos": -1.0}), "owner")["accepted"]
    new_grant = a.handle(command("grant", **fresh), "owner")
    assert a.handle(command("input", **fresh, grant_id=new_grant["grant_id"], seq=1,
                            target={"arm_left_elbow_flex.pos": -0.2}), "owner")["accepted"]
    assert e.task is None and e.worker.is_alive()


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
