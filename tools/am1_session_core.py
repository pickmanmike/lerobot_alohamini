"""Durable session authority. Explicit executor outcomes; no hardware imports."""

import hashlib
import json
import math
import os
import sqlite3
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from examples.alohamini.am1_session_contract import RECIPES, uuid_text
from tools.am1_fake_executor import FakeExecutor

TERMINAL = {"stopped", "completed", "faulted", "interrupted"}


def _reconciliation_reference(run):
    """Present an acknowledged receipt; keep full evidence in the durable run."""
    receipt = run.get("reconciliation")
    if not isinstance(receipt, dict) or run.get("uncertain") is not False:
        return receipt
    ack = receipt.get("acknowledgment")
    if not isinstance(ack, dict):
        return receipt
    original = dict(run)
    original.pop("reconciliation", None)
    original["uncertain"] = True
    from tools.am1_physical_reconciliation import acknowledged_receipt

    try:
        if not acknowledged_receipt(receipt, original, ack.get("consumed_operation_id")):
            return receipt
        for key in ("run_id", "host_incarnation", "reconciliation_id"):
            if uuid_text(receipt[key]) != receipt[key]:
                return receipt
        for key in ("operation_id", "consumed_operation_id"):
            if uuid_text(ack[key]) != ack[key]:
                return receipt
        times = [
            receipt["started_at_monotonic_s"],
            receipt["completed_at_monotonic_s"],
            ack["completed_at_monotonic_s"],
        ]
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in times):
            return receipt
        rb = receipt.get("readback") or {}
        proof_hash = ack["proof_sha256"]
        if not (
            0 <= times[0] <= times[1] <= times[2]
            and type(receipt.get("version")) is int
            and type(ack.get("version")) is int
            and receipt.get("no_actuation") is True
            and isinstance(rb, dict)
            and rb.get("verified") is True
            and rb.get("buses_closed") is True
            and rb.get("lift_readback") == "qualified"
            and ack.get("original_terminal_run_sha256") == receipt["original_terminal_run_sha256"]
            and ack.get("validation_basis") in ("fresh-native-proof", "durable-ack-resume")
            and len(proof_hash) == 64
            and all(c in "0123456789abcdef" for c in proof_hash)
            and len(json.dumps(ack, allow_nan=False, separators=(",", ":")).encode()) <= 4096
        ):
            return receipt
    except (KeyError, TypeError, ValueError):
        return receipt
    fields = (
        "version",
        "kind",
        "run_id",
        "host_incarnation",
        "reconciliation_id",
        "original_terminal_run_sha256",
        "cleanup",
        "uncertain",
        "started_at_monotonic_s",
        "completed_at_monotonic_s",
        "acknowledgment",
    )
    return {
        **{key: receipt[key] for key in fields},
        "presentation_version": 1,
        "evidence_reference": {"kind": "am1-stopped-reconciliation-reference", "version": 1},
        "proof_sha256": proof_hash,
    }


class OwnerLock:
    """Shared namespace lock for fake authority and fake legacy contenders."""

    def __init__(self, path):
        self.stream = Path(path).open("a+b")  # noqa: SIM115 - authority lifetime owns this stream
        self.stream.seek(0, 2)
        if not self.stream.tell():
            self.stream.write(b"\0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.stream.close()
            raise RuntimeError("namespace owner already active") from exc

    def close(self):
        if self.stream.closed:
            return
        self.stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        self.stream.close()


class SessionAuthority:
    def __init__(self, state_directory, clock=time.monotonic, wall_clock=time.time, executor=None):
        self.clock, self.wall_clock = clock, wall_clock
        self.executor = executor or FakeExecutor(clock)
        self.asynchronous = getattr(self.executor, "asynchronous", False) is True
        self.directory = Path(state_directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.owner_lock = OwnerLock(self.directory / "owner.lock")
        self.mutex = threading.RLock()
        self.closed = False
        self.service_incarnation = str(uuid.uuid4())
        self.controller = None
        self.connection = None
        self.grant = None
        self.released = False
        self.events = deque(maxlen=64)
        self.last_tick = clock()
        self.last_checkpoint = clock()
        self.db = sqlite3.connect(self.directory / "sessions.sqlite3", check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, hash TEXT, device TEXT, result TEXT)"
        )
        self.db.execute("CREATE TABLE IF NOT EXISTS mutations (id TEXT PRIMARY KEY, hash TEXT, result TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS transitions (id INTEGER PRIMARY KEY, data TEXT)")
        rows = self.db.execute("SELECT data FROM transitions ORDER BY id DESC LIMIT 64").fetchall()
        self.events.extend(json.loads(row[0]) for row in reversed(rows))
        self.db.execute("CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT)")
        row = self.db.execute("SELECT data FROM state WHERE id=1").fetchone()
        self.run = json.loads(row[0]) if row else None
        if self.run and self.run["status"] not in TERMINAL:
            self.run.update(
                status="interrupted",
                uncertain=True,
                deadline=None,
                intent=None,
                cleanup=self.executor.restart_cleanup,
            )
            self.run["first_cause"] = self.run["first_cause"] or "service_restart"
            self._event("service_restart")
            self._save()
        self._hold()

    def _set_executor_revision(self):
        if self.asynchronous and self.run:
            self.executor.set_intent_revision(self.run["intent_revision"])

    def _hold(self):
        self._set_executor_revision()
        return self.executor.hold()

    def _evidence(self):
        if not self.asynchronous:
            return self.executor.evidence()
        try:
            return self.executor.evidence()
        except (OSError, RuntimeError, ValueError) as exc:
            return {
                "feedback": False,
                "required_observation": False,
                "native_ack": False,
                "pose_aligned": False,
                "fault": str(exc),
            }

    def _physical_lifecycle(self, evidence, now):
        """Consume cached exact-run receipts; never execute blocking backend work."""
        run = self.run
        lifecycle = evidence.get("lifecycle")
        matching = isinstance(lifecycle, dict) and lifecycle.get("run_id") == run["run_id"]
        if matching and run.get("backend_incarnation") is not None:
            matching = lifecycle.get("backend_incarnation") == run["backend_incarnation"]
        if run["status"] == "finishing":
            if matching and lifecycle.get("phase") == "terminal":
                cleanup = lifecycle.get("cleanup")
                uncertain = lifecycle.get("uncertain")
                if isinstance(cleanup, str) and cleanup and type(uncertain) is bool:
                    run["physical_result"] = json.loads(json.dumps(lifecycle, allow_nan=False))
                    run["native_first_cause"] = run["physical_result"].get("first_cause")
                    run["first_cause"] = run["first_cause"] or run["native_first_cause"]
                    run["cleanup"] = cleanup
                    run["uncertain"] = run["uncertain"] or uncertain
                    run["status"] = run["requested_terminal"]
                    if lifecycle.get("terminal_status") == "faulted":
                        run["status"] = "faulted"
                    self._event(run["status"])
                    self._save()
                    return True
            if now >= run["finish_ceiling"]:
                run.update(
                    status=run["requested_terminal"], cleanup="physical_cleanup_unknown", uncertain=True
                )
                run["first_cause"] = run["first_cause"] or "physical_cleanup_timeout"
                self._event(run["status"])
                self._save()
            return True
        if run.get("native_live_at") is not None:
            return False
        if now >= run["startup_deadline"]:
            self._finish("faulted", "physical_startup_timeout")
            return True
        native_live_at = lifecycle.get("native_live_at") if matching else None
        if not (
            matching
            and lifecycle.get("phase") == "live"
            and type(native_live_at) in (int, float)
            and math.isfinite(native_live_at)
            and run["accepted_at"] <= native_live_at <= now
        ):
            return True
        run["native_live_at"] = native_live_at
        incarnation = lifecycle.get("backend_incarnation")
        if isinstance(incarnation, str) and incarnation:
            run["backend_incarnation"] = incarnation
        run["deadline"] = native_live_at + RECIPES[run["recipe"]].live_s
        run["preparation_ceiling"] = min(native_live_at + 20, run["deadline"])
        run["dispatch"] = "acknowledged"
        if run["status"] == "starting":
            run["status"] = "preparing"
        self.last_tick = now
        self._event("native_live_admitted")
        self._save()
        return False

    def _physical_completion(self, evidence, now):
        """Validate one cached final return despite its subsequent intentional hold.

        Current required inputs still qualify. Only the completed return's active
        epoch may be historical; no transport or native work runs under this lock.
        """
        run = self.run
        if not (
            self.asynchronous
            and run["status"] in {"running", "resuming"}
            and RECIPES[run["recipe"]].mode == "finite"
            and evidence["feedback"]
            and evidence["required_observation"]
            and not evidence.get("transport_issue")
        ):
            return None
        receipt = evidence.get("finite_completion")
        lifecycle = evidence.get("lifecycle")
        current_ack = evidence.get("accepted_host_action")
        if not all(isinstance(value, dict) for value in (receipt, lifecycle, current_ack)):
            return None
        reack = receipt.get("return_reacknowledgment")
        if "return_reacknowledgment" in receipt and not (
            isinstance(reack, dict)
            and type(reack.get("sample_count")) is int
            and reack["sample_count"] == 3
            and type(reack.get("span_s")) in (int, float)
            and math.isfinite(reack["span_s"])
            and reack["span_s"] >= 0.2 - 1e-9
            and type(reack.get("first_feedback_sequence")) is int
            and type(receipt.get("feedback_sequence")) is int
            and 0 <= reack["first_feedback_sequence"] < receipt["feedback_sequence"]
        ):
            return None
        incarnation = run.get("backend_incarnation")
        identity = {
            "run_id": run["run_id"],
            "host_incarnation": incarnation,
            "intent_revision": run["intent_revision"],
        }
        ack = receipt.get("accepted_host_action")
        execution = receipt.get("execution")
        if not (
            incarnation
            and lifecycle.get("phase") == "live"
            and lifecycle.get("run_id") == run["run_id"]
            and lifecycle.get("backend_incarnation") == incarnation
            and isinstance(ack, dict)
            and isinstance(execution, dict)
            and all(
                container.get(key) == value
                for container in (receipt, ack, current_ack)
                for key, value in identity.items()
            )
            and receipt.get("recipe") == run["recipe"]
            and receipt.get("required_observation") is True
            and ack.get("state") == "active"
            and type(ack.get("epoch")) is int
            and ack["epoch"] >= 0
            and type(ack.get("accepted_action_sequence")) is int
            and ack["accepted_action_sequence"] >= 0
        ):
            return None
        acquired = receipt.get("feedback_at_monotonic_s")
        completed = receipt.get("completed_at_monotonic_s")
        sequence = receipt.get("feedback_sequence")
        current_sequence = evidence.get("feedback_sequence")
        seed, positions = execution.get("original_seed"), receipt.get("measured_positions")
        returns = 4 if run["recipe"].endswith("-repeat") else 1
        cycles = returns if "arm-smoke" in run["recipe"] else 0
        if not (
            all(type(value) in (int, float) and math.isfinite(value) for value in (acquired, completed))
            and run["native_live_at"] <= acquired <= completed <= now < run["deadline"]
            # Preserve the physical adapter's original one-second acquisition/request bound.
            and completed - acquired < 1.0
            and type(sequence) is int
            and sequence >= 0
            and type(current_sequence) is int
            and current_sequence >= sequence
            and execution.get("feedback_sequence") == sequence
            and execution.get("complete") is True
            and execution.get("preparing") is False
            and type(execution.get("admissions")) is int
            and execution["admissions"] == 1
            and type(execution.get("returns_qualified")) is int
            and execution["returns_qualified"] == returns
            and type(execution.get("cycles_completed")) is int
            and execution["cycles_completed"] == cycles
            and type(execution.get("progress_s")) in (int, float)
            and math.isfinite(execution["progress_s"])
            and execution.get("progress_s") == RECIPES[run["recipe"]].trajectory_s
            and isinstance(seed, dict)
            and seed
            and isinstance(positions, dict)
            and set(seed) == set(positions)
            and all(
                type(value) in (int, float) and math.isfinite(value)
                for value in (*seed.values(), *positions.values())
            )
        ):
            return None
        fields = (
            "run_id",
            "host_incarnation",
            "intent_revision",
            "recipe",
            "completed_at_monotonic_s",
            "feedback_at_monotonic_s",
            "feedback_sequence",
            "accepted_host_action",
            "required_observation",
            "measured_positions",
            "execution",
            "return_reacknowledgment",
        )
        return json.loads(
            json.dumps({key: receipt[key] for key in fields if key in receipt}, allow_nan=False)
        )

    def _event(self, kind):
        event = {
            "kind": kind,
            "at": self.clock(),
            "run_id": self.run["run_id"] if self.run else None,
            "service_incarnation": self.service_incarnation,
        }
        self.events.append(event)
        self.db.execute("INSERT INTO transitions(data) VALUES (?)", (json.dumps(event),))
        self.db.commit()

    def _save(self):
        self.db.execute("INSERT OR REPLACE INTO state VALUES (1,?)", (json.dumps(self.run),))
        if self.run:
            self.db.execute(
                "UPDATE operations SET result=? WHERE id=?",
                (json.dumps(self._run_result()), self.run["operation_id"]),
            )
        self.db.commit()

    def _run_result(self):
        return {
            "accepted": True,
            "status": self.run["status"],
            "run_id": self.run["run_id"],
            "operation_id": self.run["operation_id"],
            "uncertain": self.run["uncertain"],
            "effect_admitted": self.run["dispatch"] == "acknowledged",
            **{
                k: self.run[k]
                for k in (
                    "sensing_policy",
                    "sensing_source",
                    "observation_provenance",
                    "observation_freshness_basis",
                )
                if k in self.run
            },
        }

    def _refuse(self, reason):
        return {"accepted": False, "status": "refused", "reason": reason, "effect_admitted": False}

    def _owned(self, cmd, device):
        return bool(
            self.controller
            and self.controller["device_id"] == device
            and cmd.get("controller_generation") == self.controller["generation"]
            and self.clock() < self.controller["expires"]
        )

    def _connection_valid(self, cmd, device):
        return (
            self._owned(cmd, device)
            and self.connection
            and self.connection["device_id"] == device
            and cmd.get("connection_generation") == self.connection["generation"]
            and cmd.get("service_incarnation") == self.service_incarnation
        )

    def _clear_input(self):
        self.grant = None
        self.released = False
        if self.run:
            self.run["intent"] = None
        finite_owned = (
            self.run
            and RECIPES[self.run["recipe"]].mode == "finite"
            and (
                self.run["status"] == "running"
                or self.asynchronous
                and self.run["status"] in {"starting", "preparing", "resuming"}
            )
        )
        if not finite_owned:
            self._hold()

    def _finish(self, status, cause=None):
        if cause and not self.run["first_cause"]:
            self.run["first_cause"] = cause
        if self.asynchronous:
            if self.run["status"] == "finishing":
                if status == "stopped" and self.run["requested_terminal"] != "faulted":
                    self.run["requested_terminal"] = status
                    self._save()
                return
            self.run["status"] = "finishing"
            self.run["requested_terminal"] = status
            self.run["finish_ceiling"] = self.clock() + 60
            self.run["intent"] = None
            self.run["cleanup"] = None
            self._set_executor_revision()
            try:
                outcome = self.executor.finish(status)
            except (OSError, RuntimeError, ValueError) as exc:
                outcome = {"cleanup": "physical_cleanup_unknown", "uncertain": True}
                self.run["first_cause"] = self.run["first_cause"] or str(exc)
            if not outcome.get("pending"):
                self.run["status"] = status
                self.run["cleanup"] = outcome["cleanup"]
                self.run["uncertain"] = self.run["uncertain"] or outcome["uncertain"]
            self._event(self.run["status"])
            self._save()
            return
        self.run["status"] = status
        self.run["intent"] = None
        outcome = self.executor.finish(status)
        self.run["cleanup"] = outcome["cleanup"]
        self.run["uncertain"] = self.run["uncertain"] or outcome["uncertain"]
        self._hold()
        self._event(status)
        self._save()

    def handle(self, command, device_id):
        with self.mutex:
            if self.closed:
                return self._refuse("closed")
            if not isinstance(command, dict) or not isinstance(device_id, str) or not device_id:
                return self._refuse("invalid command or trusted identity")
            if "device_id" in command:
                return self._refuse("identity is adapter supplied")
            try:
                mutation_ops = {"pause", "stop", "resume", "reconcile", "release", "handoff"}
                oid = None
                if command.get("op") in mutation_ops:
                    oid = uuid_text(command["operation_id"])
                    if self.db.execute("SELECT 1 FROM operations WHERE id=?", (oid,)).fetchone():
                        return self._refuse("operation conflict")
                    payload = dict(command, trusted_device_id=device_id)
                    digest = hashlib.sha256(
                        json.dumps(payload, sort_keys=True, allow_nan=False).encode()
                    ).hexdigest()
                    row = self.db.execute("SELECT hash,result FROM mutations WHERE id=?", (oid,)).fetchone()
                    if row:
                        return json.loads(row[1]) if row[0] == digest else self._refuse("operation conflict")
                    # Record acceptance before dispatch. Restart cannot replay an uncertain mutation.
                    result = {"accepted": True, "status": "uncertain", "effect_admitted": False}
                    self.db.execute("INSERT INTO mutations VALUES (?,?,?)", (oid, digest, json.dumps(result)))
                    self.db.commit()
                protective = (
                    command.get("op") in {"pause", "stop"}
                    and self.run
                    and command.get("run_id") == self.run["run_id"]
                    and self.run["status"] not in TERMINAL
                )
                if protective:
                    # Lease/grant expiry still runs, but operator intent precedes motion admission.
                    self._expire_controls(self.clock())
                    self.last_tick = self.clock()
                else:
                    self.tick()
                result = self._handle(command, device_id)
                if protective:
                    self.tick()
                if oid:
                    self.db.execute("UPDATE mutations SET result=? WHERE id=?", (json.dumps(result), oid))
                    self.db.commit()
                return result
            except (ValueError, TypeError, KeyError, OverflowError):
                return self._refuse("malformed command")

    def _handle(self, c, d):
        op = c.get("op")
        now = self.clock()
        if op == "snapshot":
            return {"accepted": True, "status": "snapshot", "snapshot": self.snapshot()}
        if op == "claim":
            # A supplied generation is renewal-only, atomically fenced after expiry.
            if "controller_generation" in c and not self._owned(c, d):
                return self._refuse("controller renewal fence")
            if self.controller and self.controller["device_id"] != d:
                return self._refuse("controller occupied")
            if not self.controller:
                self.controller = {"device_id": d, "generation": str(uuid.uuid4()), "expires": now + 1.5}
                self._event("claim")
            else:
                self.controller["expires"] = now + 1.5
            return {
                "accepted": True,
                "status": "claimed",
                "controller_generation": self.controller["generation"],
                "presence_expires": self.controller["expires"],
            }
        if op in ("release", "handoff"):
            if not self.controller or self.controller["device_id"] != d:
                return self._refuse("not controller")
            if op == "handoff" and (
                not isinstance(c.get("target_device_id"), str) or not c["target_device_id"]
            ):
                return self._refuse("invalid target")
            self._clear_input()
            self.connection = None
            self.controller = (
                None
                if op == "release"
                else {
                    "device_id": c["target_device_id"],
                    "generation": str(uuid.uuid4()),
                    "expires": now + 1.5,
                }
            )
            if self.run and self.run["status"] not in TERMINAL:
                self.run["intent_revision"] += 1
                if RECIPES[self.run["recipe"]].mode == "interactive" or self.run["status"] == "recovering":
                    self.run["status"] = "paused"
                self._save()
            self._event(op)
            return {"accepted": True, "status": op}
        if op == "start":
            oid = uuid_text(c["operation_id"])
            if self.db.execute("SELECT 1 FROM mutations WHERE id=?", (oid,)).fetchone():
                return self._refuse("operation conflict")
            recipe = RECIPES.get(c.get("recipe"))
            if not recipe or not self.executor.supports(recipe):
                return self._refuse("unknown fake recipe")
            if set(c) - {"op", "operation_id", "recipe", "controller_generation"}:
                return self._refuse("recipe parameters forbidden")
            canonical = json.dumps(
                {"recipe": recipe.name, "device_id": d}, sort_keys=True, separators=(",", ":")
            )
            digest = hashlib.sha256(canonical.encode()).hexdigest()
            row = self.db.execute("SELECT hash,device,result FROM operations WHERE id=?", (oid,)).fetchone()
            if row:
                if row[0] != digest or row[1] != d:
                    return self._refuse("operation conflict")
                return json.loads(row[2])
            if not self._owned(c, d):
                return self._refuse("current controller lease required")
            if self.run and (self.run["uncertain"] or self.run["status"] not in TERMINAL):
                return self._refuse("reconciliation or terminal run required")
            evidence = self._evidence()
            preparing_observation = getattr(self.executor, "observation", None) is not None
            required_keys = (
                ("feedback", "native_ack")
                if preparing_observation
                else ("feedback", "required_observation", "native_ack")
            )
            historical_fault_clean = False
            if (
                evidence["fault"]
                and self.asynchronous
                and self.executor.source == "protected-physical-provider"
            ):
                qualify = getattr(self.executor, "can_start_after_fault", None)
                historical_fault_clean = (
                    isinstance(self.run, dict)
                    and evidence["fault"] == self.run.get("first_cause")
                    and isinstance(evidence.get("lifecycle"), dict)
                    and evidence["lifecycle"] == self.run.get("physical_result")
                    and callable(qualify)
                    and qualify(self.run) is True
                )
            if (
                evidence["fault"]
                and not historical_fault_clean
                or not self.asynchronous
                and not all(evidence[k] for k in required_keys)
            ):
                self._hold()
                return self._refuse("fresh qualified feedback/proof and acknowledgement required")
            self.run = {
                "run_id": str(uuid.uuid4()),
                "operation_id": oid,
                "recipe": recipe.name,
                "source": self.executor.source,
                "seed": None if self.asynchronous else recipe.seed,
                "status": "accepted",
                "dispatch": "accepted",
                "progress_s": 0.0,
                "deadline": None if self.asynchronous else now + recipe.live_s,
                "first_cause": None,
                "uncertain": False,
                "intent": None,
                "intent_revision": 0,
                "recovery": None,
                "recovery_episodes": 0,
                "cleanup": None,
                "service_incarnation": self.service_incarnation,
                "started_wall": self.wall_clock(),
            }
            metadata = getattr(self.executor, "sensing_metadata", None)
            if metadata:
                self.run.update(metadata())
            self.db.execute(
                "INSERT INTO operations VALUES (?,?,?,?)", (oid, digest, d, json.dumps(self._run_result()))
            )
            self._save()
            if self.asynchronous:
                self.run.update(
                    status="starting", accepted_at=now, native_live_at=None, startup_deadline=now + 180
                )
                self._save()
                try:
                    self.executor.bind_run(self.run["run_id"], recipe)
                    self._set_executor_revision()
                    if not self.executor.begin(recipe):
                        self._finish("faulted", "physical_startup_unaccepted")
                    else:
                        lifecycle = self._evidence().get("lifecycle", {})
                        startup_deadline = lifecycle.get("startup_deadline")
                        if (
                            lifecycle.get("run_id") == self.run["run_id"]
                            and type(startup_deadline) in (int, float)
                            and math.isfinite(startup_deadline)
                            and now < startup_deadline <= now + 180
                        ):
                            self.run["startup_deadline"] = startup_deadline
                        self._event("start")
                        self._save()
                except (OSError, RuntimeError, ValueError) as exc:
                    self._finish("faulted", str(exc))
                return self._run_result()
            if recipe.mode == "interactive":
                self.run["status"] = "paused"
                self._hold()
                self._event("start")
                self._save()
                return self._run_result()
            self.run["dispatch"] = "dispatching"
            self._save()
            try:
                if preparing_observation:
                    self.executor.bind_run(self.run["run_id"], recipe)
                admitted = self.executor.begin(recipe)
            except Exception:
                self.run["uncertain"] = True
                self._finish("faulted", "dispatch_exception")
                return self._run_result()
            self.run["dispatch"] = "acknowledged" if admitted else "unacknowledged"
            self.run["status"] = "running" if recipe.mode == "finite" and admitted else "paused"
            if admitted and preparing_observation:
                self.run["status"] = "preparing"
                self.run["preparation_ceiling"] = min(now + 20, self.run["deadline"])
            self.last_tick = now
            self._event("start")
            self._save()
            return self._run_result()
        if op == "lookup":
            oid = uuid_text(c["operation_id"])
            if self.db.execute("SELECT 1 FROM mutations WHERE id=?", (oid,)).fetchone():
                return self._refuse("operation conflict")
            row = self.db.execute("SELECT device,result FROM operations WHERE id=?", (oid,)).fetchone()
            return json.loads(row[1]) if row and row[0] == d else self._refuse("unknown operation")
        if not self.run or c.get("run_id") != self.run["run_id"]:
            return self._refuse("exact current run required")
        if op == "reconcile":
            if not self.run["uncertain"]:
                return self._refuse("no uncertainty")
            if not self.controller or self.controller["device_id"] != d:
                return self._refuse("owner required")
            physical_reconciliation = None
            if self.asynchronous:
                reconcile = getattr(self.executor, "reconcile", None)
                if self.executor.source == "protected-physical-provider":
                    from tools.am1_physical_reconciliation import acknowledged_receipt

                    physical_reconciliation = (
                        reconcile(json.loads(json.dumps(self.run)), operation_id=c["operation_id"])
                        if reconcile
                        else None
                    )
                    if not acknowledged_receipt(physical_reconciliation, self.run, c["operation_id"]):
                        return self._refuse("actual physical cleanup reconciliation pending or unqualified")
                elif not reconcile or not reconcile(self.run["run_id"]):
                    return self._refuse("actual physical cleanup reconciliation required")
            if physical_reconciliation is None and not self._evidence()["feedback"]:
                return self._refuse("feedback required")
            if physical_reconciliation is not None:
                self.run["reconciliation"] = physical_reconciliation
            self.run["uncertain"] = False
            self._save()
            return {"accepted": True, "status": "reconciled"}
        if op in ("pause", "stop"):
            if self.run["status"] in TERMINAL:
                return self._refuse("terminal")
            if self.asynchronous and self.run["status"] == "finishing":
                if op == "stop":
                    self._finish("stopped")
                    return {"accepted": True, "status": "finishing", "effect_admitted": False}
                return self._refuse("physical finishing already in progress")
            fault = self._evidence()["fault"]
            if fault and not self.run["first_cause"]:
                self.run["first_cause"] = str(fault)
            self.run["intent_revision"] += 1
            self._clear_input()
            if op == "stop":
                self._finish("stopped")
            else:
                self.run["status"] = "paused"
                self._hold()
                self._event("pause")
                self._save()
            return {"accepted": True, "status": self.run["status"], "effect_admitted": not self.asynchronous}
        if self.run["status"] in TERMINAL:
            return self._refuse("terminal")
        if op == "connect":
            if not self.controller or self.controller["device_id"] != d:
                return self._refuse("controller required")
            self.connection = {"device_id": d, "generation": str(uuid.uuid4()), "seq": -1}
            self._clear_input()
            if RECIPES[self.run["recipe"]].mode == "interactive":
                self.run["status"] = "paused"
            self._save()
            return {
                "accepted": True,
                "status": "connected",
                "connection_generation": self.connection["generation"],
                "snapshot": self.snapshot(),
            }
        if not self._connection_valid(c, d):
            return self._refuse("stale control generation")
        if op == "release_input":
            self._clear_input()
            self.released = True
            return {"accepted": True, "status": "released", "effect_admitted": True}
        if op == "resume":
            revision = c.get("expected_intent_revision")
            if type(revision) is not int or revision != self.run["intent_revision"]:
                return self._refuse("stale or malformed intent revision")
            e = self._evidence()
            if (
                self.run["status"] != "paused"
                or self.asynchronous
                and self.run.get("native_live_at") is None
                or not self.released
                or not all(e[k] for k in ("feedback", "required_observation", "pose_aligned", "native_ack"))
            ):
                return self._refuse("qualified release/alignment required")
            self.run["intent_revision"] += 1
            self._set_executor_revision()
            self.run["status"] = "running"
            self.last_tick = now
            self.run["dispatch"] = "dispatching"
            self._save()
            try:
                admitted = self.executor.dispatch(RECIPES[self.run["recipe"]])
            except Exception:
                self.run["uncertain"] = True
                self._finish("faulted", "dispatch_exception")
                return self._run_result()
            self.run["dispatch"] = "acknowledged" if admitted else "unacknowledged"
            if admitted:
                self.run["recovery"] = None
            elif self.asynchronous:
                self.run["status"] = "resuming"
                self.run["resume_ceiling"] = min(
                    now + 10,
                    self.run["deadline"],
                    self.run["recovery"]["ceiling"] if self.run["recovery"] else self.run["deadline"],
                )
            else:
                self.run["status"] = "paused"
                self._hold()
            self._save()
            return {"accepted": True, "status": self.run["status"], "effect_admitted": admitted}
        if op == "grant":
            if self.run["status"] != "running" or RECIPES[self.run["recipe"]].mode != "interactive":
                return self._refuse("interactive running required")
            self._clear_input()
            self.grant = {"id": str(uuid.uuid4()), "issued": now, "expires": now + 0.25}
            return {
                "accepted": True,
                "status": "granted",
                "grant_id": self.grant["id"],
                "issued": now,
                "expires": now + 0.25,
            }
        if op == "input":
            seq = c.get("seq")
            target = c.get("target")
            if not self.grant or c.get("grant_id") != self.grant["id"] or now >= self.grant["expires"]:
                return self._refuse("expired grant")
            if type(seq) is not int or seq <= self.connection["seq"]:
                return self._refuse("sequence must increase")
            if (
                not isinstance(target, list)
                or not 1 <= len(target) <= 16
                or any(type(x) not in (int, float) or not math.isfinite(x) or abs(x) > 1 for x in target)
            ):
                return self._refuse("invalid normalized fake target")
            if self.run["status"] != "running" or not self._evidence()["feedback"]:
                return self._refuse("not qualified")
            self.connection["seq"] = seq
            self.run["intent"] = list(target)
            return {"accepted": True, "status": "input", "effect_admitted": self.executor.apply(target)}
        return self._refuse("unknown operation")

    def _expire_controls(self, now):
        if self.controller and now >= self.controller["expires"]:
            self.controller = None
            self.connection = None
            self._clear_input()
            self._event("presence_expired")
        if self.grant and now >= self.grant["expires"]:
            self._clear_input()

    def tick(self):
        with self.mutex:
            if self.closed:
                return
            now = self.clock()
            dt = max(0.0, min(0.1, now - self.last_tick))
            self.last_tick = now
            self._expire_controls(now)
            r = self.run
            previous_status = r["status"] if r else None
            if not r or r["status"] in TERMINAL:
                return
            e = self._evidence()
            if self.asynchronous and r["status"] == "finishing":
                self._physical_lifecycle(e, now)
                return
            if e["fault"]:
                self._finish("faulted", str(e["fault"]))
                return
            if self.asynchronous and self._physical_lifecycle(e, now):
                return
            if now >= r["deadline"]:
                self._finish("stopped", "live_deadline")
                return
            if self.asynchronous and r["status"] == "resuming" and now >= r["resume_ceiling"]:
                self._finish("faulted", "physical_resume_timeout")
                return
            completion = self._physical_completion(e, now)
            if completion is not None:
                r["completion_receipt"] = completion
                r["execution"] = completion["execution"]
                r["progress_s"] = completion["execution"]["progress_s"]
                if completion["execution"].get("fault"):
                    self._finish("faulted", completion["execution"]["fault"])
                else:
                    self._finish("completed")
                return
            qualified = e["feedback"] and e["required_observation"] and e["native_ack"]
            if r["status"] == "preparing":
                if now >= r["preparation_ceiling"]:
                    self._finish("faulted", "observation_preparation_timeout")
                    return
                if qualified and e["pose_aligned"]:
                    try:
                        self._set_executor_revision()
                        admitted = self.executor.dispatch(RECIPES[r["recipe"]])
                    except (OSError, RuntimeError, ValueError) as exc:
                        self._finish("faulted", str(exc))
                        return
                    if admitted:
                        r["status"] = "running"
                        self._event("observation_qualified")
            elif self.asynchronous and r["status"] == "resuming":
                if now >= r["resume_ceiling"]:
                    self._finish("faulted", "physical_resume_timeout")
                    return
                if qualified and e["pose_aligned"]:
                    try:
                        self._set_executor_revision()
                        admitted = self.executor.dispatch(RECIPES[r["recipe"]])
                    except (OSError, RuntimeError, ValueError) as exc:
                        self._finish("faulted", str(exc))
                        return
                    if admitted:
                        r["status"] = "running"
                        r["dispatch"] = "acknowledged"
                        r["recovery"] = None
                        self._event("resumed")
            elif r["status"] == "running" and not (
                qualified and (not self.asynchronous or e.get("active_acknowledged") is True)
            ):
                r["recovery_episodes"] += 1
                r["first_cause"] = r["first_cause"] or (
                    "feedback_loss" if not e["feedback"] else "required_proof_loss"
                )
                ceiling = r["recovery"]["ceiling"] if r["recovery"] else min(now + 10, r["deadline"])
                r["recovery"] = {"started": now, "ceiling": ceiling, "revision": r["intent_revision"]}
                r["status"] = "recovering"
                self._clear_input()
                self._event("required_hold")
                if r["recovery_episodes"] > 3 or now >= ceiling:
                    self._finish("faulted", "recovery_budget")
                    return
            elif r["status"] == "recovering":
                recovery = r["recovery"]
                if now >= recovery["ceiling"]:
                    self._finish("faulted", "recovery_timeout")
                    return
                if qualified and e["pose_aligned"] and recovery["revision"] == r["intent_revision"]:
                    try:
                        self._set_executor_revision()
                        admitted = self.executor.dispatch(RECIPES[r["recipe"]])
                    except Exception:
                        r["uncertain"] = True
                        self._finish("faulted", "dispatch_exception")
                        return
                    fault = self._evidence()["fault"]
                    if fault:
                        self._finish("faulted", str(fault))
                        return
                    if admitted:
                        r["status"] = "running"
                        r["recovery"] = None
                        self._event("recovered")
                    else:
                        r["status"] = "recovering"
                        if not self.asynchronous:
                            self._hold()
            elif r["status"] == "running" and RECIPES[r["recipe"]].mode == "finite":
                try:
                    execution = self.executor.advance(now, dt)
                except (OSError, RuntimeError, ValueError) as exc:
                    self._finish("faulted", str(exc))
                    return
                r["execution"] = execution
                r["progress_s"] = execution["progress_s"]
                if execution.get("fault"):
                    self._finish("faulted", execution["fault"])
                    return
                if execution["complete"]:
                    self._finish("completed")
                    return
            if r["status"] != previous_status or now - self.last_checkpoint >= 1.0:
                self._save()
                self.last_checkpoint = now

    def snapshot(self):
        with self.mutex:
            run = None if not self.run else dict(self.run)
            if (
                run is not None
                and "reconciliation" in run
                and self.executor.source == "protected-physical-provider"
            ):
                run["reconciliation"] = _reconciliation_reference(self.run)
            return json.loads(
                json.dumps(
                    {
                        "service_incarnation": self.service_incarnation,
                        "controller": None
                        if not self.controller
                        else {
                            "device_id": self.controller["device_id"],
                            "controller_generation": self.controller["generation"],
                            "presence_expires": self.controller["expires"],
                        },
                        "run": None
                        if run is None
                        else dict(
                            run,
                            remaining_s=None
                            if self.run["deadline"] is None
                            else max(0, self.run["deadline"] - self.clock()),
                            trajectory_remaining_s=None
                            if RECIPES[self.run["recipe"]].trajectory_s is None
                            else max(0, RECIPES[self.run["recipe"]].trajectory_s - self.run["progress_s"]),
                        ),
                        "evidence": self._evidence(),
                        "recipes": [
                            name for name, recipe in RECIPES.items() if self.executor.supports(recipe)
                        ],
                        "events": list(self.events),
                    }
                )
            )

    def close(self):
        if self.asynchronous:
            with self.mutex:
                if self.closed:
                    return
                self.closed = True
            # Physical worker shutdown may join bounded homing/cleanup work. It must
            # not prevent worker receipt publication or status reads via this mutex.
            try:
                self.executor.close()
            finally:
                with self.mutex:
                    self.db.close()
                    self.owner_lock.close()
            return
        with self.mutex:
            if self.closed:
                return
            self.executor.close()
            self.closed = True
            self.db.close()
            self.owner_lock.close()
