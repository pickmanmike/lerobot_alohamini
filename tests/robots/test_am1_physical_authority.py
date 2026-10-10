"""Real-lifecycle authority gates exercised with non-actuating asynchronous receipts."""

import uuid

from tests.robots.test_am1_persistent_session import Clock, command, start
from tools.am1_session_core import SessionAuthority


class DelayedPhysicalExecutor:
    asynchronous = True
    source = "protected-am1-host"
    restart_cleanup = "unknown_after_restart"

    def __init__(self, clock):
        self.clock = clock
        self.run_id = None
        self.phase = "idle"
        self.live_at = None
        self.backend_incarnation = str(uuid.uuid4())
        self.started = self.dispatches = self.finishes = self.holds = 0
        self.qualified = False
        self.dispatch_ack = True
        self.active_ack = True
        self.finish_status = self.cleanup = None
        self.uncertain = False
        self.fault = None
        self.revision = 0
        self.execution = {"progress_s": 0.0, "complete": False}

    def supports(self, recipe):
        return recipe.name == "sim-arm-smoke-repeat"

    def bind_run(self, run_id, recipe):
        self.run_id = run_id

    def set_intent_revision(self, revision):
        self.revision = revision

    def begin(self, recipe):
        self.started += 1
        self.phase = "startup"
        self.startup_deadline = self.clock() + 180
        return True

    def evidence(self):
        return {
            "feedback": self.qualified,
            "required_observation": self.qualified,
            "native_ack": self.qualified,
            "active_acknowledged": self.qualified and self.active_ack,
            "pose_aligned": self.qualified,
            "fault": self.fault,
            "lifecycle": {
                "run_id": self.run_id,
                "backend_incarnation": self.backend_incarnation,
                "phase": self.phase,
                "startup_deadline": getattr(self, "startup_deadline", None),
                "native_live_at": self.live_at,
                "cleanup": self.cleanup,
                "uncertain": self.uncertain,
                "terminal_status": self.finish_status,
            },
        }

    def dispatch(self, recipe):
        self.dispatches += 1
        return self.qualified and self.dispatch_ack

    def advance(self, now, dt):
        return dict(self.execution)

    def hold(self):
        self.holds += 1
        return self.qualified

    def finish(self, status):
        self.finishes += 1
        self.finish_status = status
        self.phase = "finishing"
        return {"pending": True, "cleanup": None, "uncertain": False}

    def close(self):
        pass


def build(tmp_path):
    clock = Clock()
    executor = DelayedPhysicalExecutor(clock)
    return clock, executor, SessionAuthority(tmp_path, clock=clock, executor=executor)


def admit_live(authority, executor, clock):
    executor.phase = "live"
    executor.qualified = True
    executor.live_at = clock()
    authority.tick()


def test_start_is_accepted_without_fake_feedback_and_deduplicated_before_real_live(tmp_path):
    clock, executor, authority = build(tmp_path)
    try:
        claim = authority.handle(command("claim"), "owner")
        request = command(
            "start",
            operation_id=str(uuid.uuid4()),
            recipe="sim-arm-smoke-repeat",
            controller_generation=claim["controller_generation"],
        )
        accepted = authority.handle(request, "owner")
        assert accepted["accepted"] and not accepted["effect_admitted"]
        assert authority.run["status"] == "starting"
        assert authority.run["deadline"] is None
        assert authority.run["seed"] is None
        assert authority.handle(request, "owner")["run_id"] == accepted["run_id"]
        assert executor.started == 1 and executor.dispatches == 0
        clock.advance(40)
        authority.tick()
        assert authority.snapshot()["run"]["status"] == "starting"
        admit_live(authority, executor, clock)
        assert authority.run["native_live_at"] == 40
        assert authority.run["deadline"] == 460
        assert authority.run["preparation_ceiling"] == 60
        assert authority.run["status"] == "running"
        executor.live_at = 41  # A later receipt cannot renew the original admission.
        clock.advance(1)
        authority.tick()
        assert authority.run["deadline"] == 460
    finally:
        authority.close()


def test_startup_stop_stays_finishing_until_exact_run_cleanup_without_dispatch(tmp_path):
    clock, executor, authority = build(tmp_path)
    try:
        run = start(authority, "sim-arm-smoke-repeat")
        assert run["accepted"]
        stopped = authority.handle(command("stop", run_id=run["run_id"]), "viewer")
        assert stopped["status"] == "finishing"
        assert authority.run["cleanup"] is None
        assert executor.finishes == 1 and executor.revision == 1
        # A late startup receipt, including actual Live, cannot revive the stopped task.
        admit_live(authority, executor, clock)
        assert authority.run["status"] == "finishing"
        assert executor.dispatches == 0
        executor.phase = "terminal"
        executor.cleanup = "physical_torque_off_readback_verified"
        executor.run_id = str(uuid.uuid4())
        authority.tick()
        assert authority.run["status"] == "finishing"
        executor.run_id = run["run_id"]
        holds_before = executor.holds
        authority.tick()
        assert authority.run["status"] == "stopped"
        assert authority.run["cleanup"] == "physical_torque_off_readback_verified"
        assert executor.holds == holds_before
    finally:
        authority.close()


def test_pause_during_startup_fences_late_native_live_and_sets_original_deadline(tmp_path):
    clock, executor, authority = build(tmp_path)
    try:
        run = start(authority, "sim-arm-smoke-repeat")
        authority.handle(command("pause", run_id=run["run_id"]), "viewer")
        clock.advance(5)
        admit_live(authority, executor, clock)
        assert authority.run["status"] == "paused"
        assert authority.run["deadline"] == 425
        assert executor.dispatches == 0 and executor.revision == 1
        clock.advance(420)
        authority.tick()
        assert authority.run["status"] == "finishing"
        assert authority.run["first_cause"] == "live_deadline"
    finally:
        authority.close()


def test_startup_timeout_unknown_cleanup_and_feedback_do_not_boolean_reconcile(tmp_path):
    clock, executor, authority = build(tmp_path)
    try:
        run = start(authority, "sim-arm-smoke-repeat")
        clock.advance(180)
        authority.tick()
        assert authority.run["status"] == "finishing"
        assert authority.run["first_cause"] == "physical_startup_timeout"
        assert authority.handle(command("snapshot"), "viewer")["accepted"]
        executor.phase = "terminal"
        executor.cleanup = "physical_cleanup_unknown"
        executor.uncertain = True
        authority.tick()
        assert authority.run["status"] == "faulted" and authority.run["uncertain"]
        assert not start(authority, "sim-arm-smoke-repeat")["accepted"]
        executor.qualified = True
        claim = authority.handle(command("claim"), "owner")
        result = authority.handle(command("reconcile", run_id=run["run_id"]), "owner")
        assert not result["accepted"] and authority.run["uncertain"]
        assert claim["accepted"]
    finally:
        authority.close()


def test_completed_trajectory_is_not_terminal_before_measured_cleanup_and_restart_is_uncertain(tmp_path):
    clock, executor, authority = build(tmp_path)
    run = start(authority, "sim-arm-smoke-repeat")
    admit_live(authority, executor, clock)
    executor.execution = {"progress_s": 352.0, "complete": True}
    clock.advance(0.1)
    authority.tick()
    assert authority.run["status"] == "finishing"
    assert authority.run["progress_s"] == 352
    assert authority.run["requested_terminal"] == "completed"
    authority.close()
    recovered = SessionAuthority(tmp_path, clock=clock, executor=DelayedPhysicalExecutor(clock))
    try:
        assert recovered.run["run_id"] == run["run_id"]
        assert recovered.run["status"] == "interrupted" and recovered.run["uncertain"]
        assert recovered.run["cleanup"] == "unknown_after_restart"
    finally:
        recovered.close()


def test_foreign_live_receipt_cannot_establish_deadline_or_dispatch(tmp_path):
    clock, executor, authority = build(tmp_path)
    try:
        run = start(authority, "sim-arm-smoke-repeat")
        executor.run_id = str(uuid.uuid4())
        admit_live(authority, executor, clock)
        assert authority.run["native_live_at"] is None
        assert authority.run["deadline"] is None and executor.dispatches == 0
        executor.run_id = run["run_id"]
        clock.advance(1)
        executor.live_at = -1
        authority.tick()
        assert authority.run["deadline"] is None
    finally:
        authority.close()


def test_resume_waits_for_exact_ack_and_later_pause_fences_delayed_ack(tmp_path):
    clock, executor, authority = build(tmp_path)
    try:
        run = start(authority, "sim-arm-smoke-repeat")
        admit_live(authority, executor, clock)
        deadline = authority.run["deadline"]
        authority.handle(command("pause", run_id=run["run_id"]), "owner")
        connection = authority.handle(command("connect", run_id=run["run_id"]), "owner")
        base = {
            "run_id": run["run_id"],
            "service_incarnation": authority.service_incarnation,
            "controller_generation": authority.controller["generation"],
            "connection_generation": connection["connection_generation"],
        }
        assert authority.handle(command("release_input", **base), "owner")["accepted"]
        executor.dispatch_ack = False
        reply = authority.handle(
            command("resume", **base, expected_intent_revision=authority.run["intent_revision"]), "owner"
        )
        assert reply["status"] == "resuming" and not reply["effect_admitted"]
        assert executor.revision == authority.run["intent_revision"]
        authority.handle(command("pause", run_id=run["run_id"]), "viewer")
        dispatches_before = executor.dispatches
        executor.dispatch_ack = True
        clock.advance(0.1)
        authority.tick()
        assert authority.run["status"] == "paused"
        assert executor.dispatches == dispatches_before
        assert authority.run["deadline"] == deadline
    finally:
        authority.close()


def test_recovery_closes_only_after_executor_ack_and_later_loss_gets_new_bounded_episode(tmp_path):
    clock, executor, authority = build(tmp_path)
    try:
        start(authority, "sim-arm-smoke-repeat")
        admit_live(authority, executor, clock)
        deadline = authority.run["deadline"]
        for episode in range(1, 4):
            executor.qualified = False
            clock.advance(0.1)
            authority.tick()
            assert authority.run["status"] == "recovering"
            ceiling = authority.run["recovery"]["ceiling"]
            executor.qualified = True
            executor.dispatch_ack = False
            holds_before = executor.holds
            clock.advance(0.3)
            authority.tick()
            assert authority.run["status"] == "recovering"
            assert authority.run["recovery"]["ceiling"] == ceiling
            assert executor.holds == holds_before  # Do not cancel the pending native resume handshake.
            executor.dispatch_ack = True
            authority.tick()
            assert authority.run["status"] == "running"
            assert authority.run["recovery"] is None
            assert authority.run["recovery_episodes"] == episode
        executor.qualified = False
        authority.tick()
        assert authority.run["status"] == "finishing"
        assert authority.run["recovery_episodes"] == 4
        assert authority.run["first_cause"] == "feedback_loss"
        assert authority.run["deadline"] == deadline
    finally:
        authority.close()


def test_expected_evidence_transport_exception_preserves_first_cause_and_owner_access(tmp_path):
    clock, executor, authority = build(tmp_path)
    try:
        start(authority, "sim-arm-smoke-repeat")
        admit_live(authority, executor, clock)
        executor.qualified = False
        authority.tick()
        original = executor.evidence

        def broken_evidence():
            raise OSError("bounded backend receipt transport failed")

        executor.evidence = broken_evidence
        clock.advance(0.1)
        authority.tick()
        assert authority.run["status"] == "finishing"
        assert authority.run["first_cause"] == "feedback_loss"
        assert authority.handle(command("snapshot"), "viewer")["accepted"]
        executor.evidence = original
        executor.phase = "terminal"
        executor.cleanup = "physical_torque_off_readback_verified"
        authority.tick()
        assert authority.run["status"] == "faulted"
    finally:
        authority.close()


def test_physical_finishing_timeout_retains_uncertainty_without_false_release(tmp_path):
    clock, executor, authority = build(tmp_path)
    try:
        run = start(authority, "sim-arm-smoke-repeat")
        authority.handle(command("stop", run_id=run["run_id"]), "viewer")
        clock.advance(60)
        authority.tick()
        assert authority.run["status"] == "stopped"
        assert authority.run["cleanup"] == "physical_cleanup_unknown" and authority.run["uncertain"]
        assert authority.run["first_cause"] == "physical_cleanup_timeout"
        assert executor.finishes == 1
        assert not start(authority, "sim-arm-smoke-repeat")["accepted"]
    finally:
        authority.close()


def test_physical_executor_close_does_not_hold_authority_mutex(tmp_path):
    import threading

    clock, executor, authority = build(tmp_path)
    snapshot_ready = threading.Event()

    def snapshot_from_worker():
        authority.snapshot()
        snapshot_ready.set()

    def close_worker():
        thread = threading.Thread(target=snapshot_from_worker)
        thread.start()
        executor.close_saw_responsive_authority = snapshot_ready.wait(0.5)
        thread.join(0.1)

    executor.close = close_worker
    authority.close()
    assert executor.close_saw_responsive_authority


def test_physical_terminal_preserves_full_native_receipt_and_earlier_first_cause(tmp_path):
    clock, executor, authority = build(tmp_path)
    try:
        run = start(authority, "sim-arm-smoke-repeat")
        admit_live(authority, executor, clock)
        executor.qualified = False
        authority.tick()
        assert authority.run["first_cause"] == "feedback_loss"
        authority.handle(command("stop", run_id=run["run_id"]), "viewer")
        receipt = {
            "run_id": run["run_id"],
            "phase": "terminal",
            "backend_incarnation": executor.backend_incarnation,
            "native_live_at": executor.live_at,
            "cleanup": "physical_cleanup_partial",
            "uncertain": True,
            "terminal_status": "faulted",
            "first_cause": "native lift readback failed",
            "actual_readback": {"arms": {"torque_off": True}, "lift": {"verified": False}},
            "camera_release": {"verified": True, "child_pid": 412},
            "errors": [{"stage": "cleanup", "message": "lift status unavailable"}],
        }
        original_evidence = executor.evidence
        executor.evidence = lambda: dict(original_evidence(), lifecycle=receipt)
        authority.tick()
        assert authority.run["physical_result"] == receipt
        assert authority.run["first_cause"] == "feedback_loss"
        assert authority.run["native_first_cause"] == "native lift readback failed"
        assert authority.run["status"] == "faulted" and authority.run["uncertain"]
        receipt["actual_readback"]["arms"]["torque_off"] = False
        assert authority.run["physical_result"]["actual_readback"]["arms"]["torque_off"] is True
    finally:
        authority.close()


def test_native_live_freezes_backend_and_foreign_terminal_cannot_replace_receipt(tmp_path):
    clock, executor, authority = build(tmp_path)
    try:
        run = start(authority, "sim-arm-smoke-repeat")
        incarnation = executor.backend_incarnation
        admit_live(authority, executor, clock)
        assert authority.run["backend_incarnation"] == incarnation
        executor.backend_incarnation = str(uuid.uuid4())
        clock.advance(0.1)
        authority.tick()
        assert authority.run["backend_incarnation"] == incarnation
        authority.handle(command("stop", run_id=run["run_id"]), "viewer")
        executor.phase = "terminal"
        executor.cleanup = "physical_torque_off_readback_verified"
        authority.tick()
        assert authority.run["status"] == "finishing"
        assert "physical_result" not in authority.run
        executor.backend_incarnation = incarnation
        original_evidence = executor.evidence
        executor.evidence = lambda: dict(
            original_evidence(),
            lifecycle=dict(original_evidence()["lifecycle"], first_cause="native actual first cause"),
        )
        authority.tick()
        assert authority.run["backend_incarnation"] == incarnation
        assert authority.run["physical_result"]["backend_incarnation"] == incarnation
        assert authority.run["first_cause"] == "native actual first cause"
        assert authority.run["native_first_cause"] == "native actual first cause"
    finally:
        authority.close()


def test_already_acknowledged_watchdog_hold_opens_bounded_recovery(tmp_path):
    clock, executor, authority = build(tmp_path)
    try:
        start(authority, "sim-arm-smoke-repeat")
        admit_live(authority, executor, clock)
        deadline = authority.run["deadline"]
        # Native worker has already completed its odd measured hold before this owner tick.
        # Measurement, required camera and native hold ACK are all currently qualified.
        executor.active_ack = False
        executor.dispatch_ack = False
        clock.advance(0.1)
        authority.tick()
        assert authority.run["status"] == "recovering"
        assert authority.run["recovery_episodes"] == 1
        ceiling = authority.run["recovery"]["ceiling"]
        clock.advance(0.3)
        authority.tick()
        assert authority.run["status"] == "recovering"
        assert authority.run["recovery"]["ceiling"] == ceiling
        executor.active_ack = True
        executor.dispatch_ack = True
        authority.tick()
        assert authority.run["status"] == "running"
        assert authority.run["recovery"] is None
        assert authority.run["deadline"] == deadline
    finally:
        authority.close()
