"""Durable session authority. Explicit executor outcomes; no hardware imports."""

import base64
import hashlib
import json
import math
import os
import sqlite3
import stat
import threading
import time
import uuid
from collections import OrderedDict, deque
from pathlib import Path

from examples.alohamini.am1_session_contract import (
    ELBOW_RECOVERY_DEFINITIONS,
    LEFT_ELBOW_COARSE_BENCH_V1,
    LEFT_ELBOW_ORIGINAL_RETURN,
    LEFT_ELBOW_POSITIVE_CONTROL,
    LEFT_ELBOW_RESPONSE,
    RECIPES,
    SELECTED_ELBOW_OPERATION_PRESET,
    SelectedElbowExperiment,
    uuid_text,
)
from tools.am1_fake_executor import FakeExecutor

TERMINAL = {"stopped", "completed", "faulted", "interrupted"}


def _selected_experiment_definition(run):
    """Recover the exact admitted condition; a descriptor alone cannot grant authority."""
    if (
        not isinstance(run, dict)
        or run.get("recipe") != SelectedElbowExperiment.recipe_name
        or run.get("source") != "protected-physical-provider"
        or run.get("confirmation_of") is not None
    ):
        return None
    try:
        definition = SelectedElbowExperiment.from_payload(run.get("selected_experiment"))
        return definition if definition.sha256 == run.get("selected_experiment_sha256") else None
    except (AttributeError, TypeError, ValueError):
        return None


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


def _public_physical_evidence(evidence, run=None):
    """Bound duplicate terminal evidence; the durable run retains the complete proof."""
    if not isinstance(evidence, dict):
        return evidence
    lifecycle = evidence.get("lifecycle")
    if not isinstance(lifecycle, dict) or lifecycle.get("phase") != "terminal":
        return evidence
    completion = evidence.get("finite_completion")
    if isinstance(run, dict) and isinstance(completion, dict):
        execution, retained = completion.get("execution"), run.get("completion_receipt")
        try:
            if (
                evidence.get("source") == run.get("source") == "protected-physical-provider"
                and run.get("status") in TERMINAL
                and uuid_text(run.get("run_id")) == run.get("run_id")
                and uuid_text(run.get("backend_incarnation")) == run.get("backend_incarnation")
                and completion.get("run_id") == lifecycle.get("run_id") == run.get("run_id")
                and completion.get("host_incarnation")
                == lifecycle.get("backend_incarnation")
                == run.get("backend_incarnation")
                and isinstance(execution, dict)
                and isinstance(retained, dict)
                and _canonical(completion) == _canonical(retained)
            ):
                public_completion = {key: value for key, value in completion.items() if key != "execution"}
                public_completion["execution_reference"] = {
                    "kind": "am1-finite-completion-execution-reference",
                    "version": 1,
                    "field": "run.completion_receipt.execution",
                    "sha256": hashlib.sha256(_canonical(execution)).hexdigest(),
                }
                evidence = dict(evidence, finite_completion=public_completion)
        except (TypeError, ValueError):
            pass  # Malformed or different evidence remains complete and unqualified.
    receipt = lifecycle.get("readback")
    readback = receipt.get("readback") if isinstance(receipt, dict) else None
    gain = readback.get("elbow_integral") if isinstance(readback, dict) else None
    if not isinstance(gain, dict):
        return evidence
    fields = (
        "schema_version",
        "state",
        "status",
        "restore_required",
        "integral",
        "binding",
        "motor",
        "motor_id",
        "motor_model",
        "cached_calibration",
        "original_registers",
        "original_bytes",
        "restored_bytes",
        "restored_integral_readback",
        "restored_lock_readback",
        "restored_at_monotonic_s",
        "first_failure",
    )
    public_gain = {key: gain[key] for key in fields if key in gain}
    public_readback = dict(readback, elbow_integral=public_gain)
    rows = readback.get("readbacks")
    if isinstance(rows, list):
        duplicate_clocks = {"read_started_at_monotonic_s", "read_completed_at_monotonic_s"}
        public_readback["readbacks"] = [
            {key: value for key, value in row.items() if key not in duplicate_clocks}
            if isinstance(row, dict)
            else row
            for row in rows
        ]
    public_receipt = dict(receipt, readback=public_readback)
    return dict(evidence, lifecycle=dict(lifecycle, readback=public_receipt))


def _positive_completion_reference(evidence, run):
    """Matched receipt duplicates may refer to retained execution during cleanup too."""
    try:
        receipt, execution, lifecycle = run["completion_receipt"], run["execution"], evidence["lifecycle"]
        retained_idle = (
            lifecycle == {
                "run_id": None, "phase": "idle", "startup_deadline": None, "native_live_at": None,
                "cleanup": None, "uncertain": False, "terminal_status": None,
            }
            and lifecycle["uncertain"] is False
            and all(evidence.get(key) is False for key in (
                "feedback", "required_observation", "native_ack", "active_acknowledged",
                "hold_acknowledged", "pose_aligned",
            ))
            and all(key in evidence and evidence[key] is None for key in (
                "host_incarnation", "finite_completion", "fault", "transport_issue", "reconciliation",
                "feedback_sequence", "measured_positions", "measured_body", "feedback_timing",
                "acknowledged_intent_revision",
            ))
            and evidence.get("accepted_host_action") == {}
        )
        if retained_idle:
            from tools.am1_physical_executor import qualified_cleanup_receipt

            lifecycle = run.get("physical_result")
            motor = lifecycle.get("readback") if isinstance(lifecycle, dict) else None
            readback = motor.get("readback") if isinstance(motor, dict) else None
            camera = lifecycle.get("camera_release") if isinstance(lifecycle, dict) else None
            release = camera.get("release_evidence") if isinstance(camera, dict) else None
            if not (
                run.get("status") in TERMINAL and run.get("uncertain") is False
                and run.get("intent") is None and run.get("cleanup") == "physical_readback_verified"
                and isinstance(execution, dict) and execution.get("complete") is True
                and execution.get("preparing") is False and isinstance(lifecycle, dict)
                and lifecycle.get("phase") == "terminal" and lifecycle.get("uncertain") is False
                and lifecycle.get("cleanup") == run["cleanup"] and lifecycle.get("cleanup_errors") == []
                and qualified_cleanup_receipt(motor, run["run_id"], run["backend_incarnation"])
                and type(motor.get("version")) is int and isinstance(readback, dict)
                and readback.get("buses_closed") is True and readback.get("verified") is True
                and readback.get("cleanup_errors") == []
                and readback.get("selected_gain_restore_required") is False
                and isinstance(camera, dict) and type(camera.get("version")) is int and camera["version"] == 1
                and camera.get("session_id") == run["run_id"] and camera.get("state") == "released"
                and camera.get("errors") == [] and camera.get("runtime_failed") is False
                and isinstance(release, dict) and type(release.get("backend_returncode")) is int
                and release["backend_returncode"] == 0 and all(release.get(key) is True for key in (
                    "backend_exited", "readers_joined", "viewer_socket_closed", "runtime_config_removed",
                ))
            ):
                return None
        definition = (
            _selected_experiment_definition(run)
            if run.get("recipe") == SelectedElbowExperiment.recipe_name
            else LEFT_ELBOW_POSITIVE_CONTROL
        )
        if not (
            definition is not None
            and run.get("recipe") == definition.recipe_name
            and run.get("source") == evidence.get("source") == "protected-physical-provider"
            and run.get("status") in TERMINAL | {"finishing"}
            and uuid_text(run.get("run_id")) == run.get("run_id")
            and uuid_text(run.get("backend_incarnation")) == run.get("backend_incarnation")
            and isinstance(receipt, dict)
            and isinstance(execution, dict)
            and isinstance(lifecycle, dict)
            and (
                lifecycle.get("phase") == "terminal"
                or run.get("status") == "finishing" and lifecycle.get("phase") == "finishing"
            )
            and (
                run.get("status") != "finishing"
                or execution.get("complete") is True and execution.get("preparing") is False
            )
            and receipt.get("run_id") == lifecycle.get("run_id") == run["run_id"]
            and receipt.get("host_incarnation")
            == lifecycle.get("backend_incarnation")
            == run["backend_incarnation"]
            and receipt.get("recipe") == run["recipe"]
            and _canonical(execution) == _canonical(receipt.get("execution"))
            and (retained_idle or _canonical(evidence.get("finite_completion")) == _canonical(receipt))
            and (not isinstance(definition, SelectedElbowExperiment) or execution.get("experiment") == definition.summary())
            and definition.completion_expectation(execution) is not None
        ):
            return None
        return {
            "kind": "am1-finite-completion-execution-reference",
            "version": 1,
            "field": "run.execution",
            "sha256": hashlib.sha256(_canonical(execution)).hexdigest(),
        }
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


def _retained_recovery_idle(evidence, previous, service_incarnation):
    """A new idle owner may reuse a completed proof with identical material bindings."""
    old_service = previous.get("service_incarnation")
    if (
        not isinstance(service_incarnation, str)
        or not isinstance(old_service, str)
        or uuid_text(service_incarnation) != service_incarnation
        or uuid_text(old_service) != old_service
        or old_service == service_incarnation
        or "intent" not in previous
        or previous["intent"] is not None
    ):
        return False
    for key in ("backend_contract", "retained_return_reference"):
        old, current = previous.get(key), evidence.get(key)
        if (
            not isinstance(old, dict)
            or not isinstance(current, dict)
            or json.dumps(old, sort_keys=True, allow_nan=False)
            != json.dumps(current, sort_keys=True, allow_nan=False)
        ):
            return False
    return (
        evidence.get("source") == previous.get("source") == "protected-physical-provider"
        and evidence.get("lifecycle")
        == {
            "run_id": None,
            "phase": "idle",
            "startup_deadline": None,
            "native_live_at": None,
            "cleanup": None,
            "uncertain": False,
            "terminal_status": None,
        }
        and evidence["lifecycle"]["uncertain"] is False
        and all(
            evidence.get(key) is False
            for key in (
                "feedback",
                "native_ack",
                "active_acknowledged",
                "hold_acknowledged",
                "pose_aligned",
            )
        )
        and all(
            key in evidence and evidence[key] is None
            for key in (
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
        )
        and evidence.get("accepted_host_action") == {}
    )


def _continuation_bytes(path, *, limit=65536, source=False):
    path = Path(path)
    if (
        not path.is_absolute()
        or path.resolve() != path
        or any(p.is_symlink() or p.is_junction() for p in (path, *path.parents))
    ):
        raise ValueError("literal ordinary continuation evidence required")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        uid = getattr(os, "getuid", lambda: info.st_uid)()
        modes = (0o600, 0o644, 0o700) if source else (0o600,)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != uid
            or not 0 < info.st_size <= limit
            or os.name != "nt"
            and stat.S_IMODE(info.st_mode) not in modes
        ):
            raise ValueError("owned bounded continuation evidence required")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        after = os.fstat(fd)
        stable = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_nlink",
            "st_uid",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if (
            len(raw) != info.st_size
            or len(raw) > limit
            or any(getattr(after, k) != getattr(info, k) for k in stable)
        ):
            raise ValueError("continuation evidence changed during read")
        return raw
    finally:
        os.close(fd)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


STATUS_DETAIL_LIMIT = 8192
STATUS_DETAIL_CACHE_BYTES = 8 * 1024**2
STATUS_REFERENCE_KEYS = {
    "kind",
    "version",
    "service_incarnation",
    "run_id",
    "operation_id",
    "host_incarnation",
    "field",
    "sha256",
    "bytes",
}


def _status_small(value, keys=None, limit=4096):
    """Shared presentation only; omitted bulk has an explicit full field reference."""
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if keys is not None and key not in keys:
            continue
        if item is None or type(item) in (bool, int, float):
            result[key] = item
        elif isinstance(item, str):
            result[key] = (
                item
                if len(item) <= 512
                else {"prefix": item[:512], "sha256": hashlib.sha256(item.encode()).hexdigest()}
            )
        elif len(_canonical(item)) <= limit:
            result[key] = item
    return result


def _status_execution(value):
    if not isinstance(value, dict):
        return value
    essential = {
        "profile",
        "provenance",
        "progress_s",
        "preparing",
        "preparation_s",
        "complete",
        "fault",
        "cycles_completed",
        "returns_qualified",
        "admissions",
        "feedback_sequence",
        "original_seed",
        "initial_measured_reference",
    }
    result = _status_small(value, essential, 2048)
    # Named outcome dictionaries share scalar summaries across every finite recipe.
    for key, item in value.items():
        if isinstance(item, dict) and key not in essential:
            scalars = {k: v for k, v in item.items() if v is None or type(v) in (bool, int, float, str)}
            if scalars:
                result[key] = _status_small(scalars, limit=512)
    return result


def _status_lifecycle(value, *, rows=False):
    if not isinstance(value, dict):
        return value
    keys = {
        "version",
        "run_id",
        "backend_incarnation",
        "host_incarnation",
        "phase",
        "cleanup",
        "uncertain",
        "cleanup_errors",
        "first_cause",
        "native_live_at",
        "startup_deadline",
        "terminal_status",
        "requested_status",
    }
    result = _status_small(value, keys)
    receipt = value.get("readback")
    if isinstance(receipt, dict):
        public = _status_small(
            receipt,
            {
                "version",
                "run_id",
                "host_incarnation",
                "cleanup",
                "uncertain",
                "cleanup_errors",
                "first_cause",
                "completed_at_monotonic_s",
                "first_error_chain",
                "expected_stop",
                "selected_joint_fault",
            },
        )
        readback = receipt.get("readback")
        if isinstance(readback, dict):
            fields = {
                "verified",
                "buses_closed",
                "lift_readback",
                "cleanup_errors",
                "selected_gain_restore_required",
            }
            if rows:
                fields.add("readbacks")
            public["readback"] = _status_small(readback, fields, 8192)
        result["readback"] = public
    camera = value.get("camera_release")
    if isinstance(camera, dict):
        result["camera_release"] = _status_small(
            camera,
            {"version", "session_id", "state", "errors", "runtime_failed", "release_evidence", "released_at"},
        )
    return result


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
        definition = _selected_experiment_definition(run)
        if run["recipe"] == SelectedElbowExperiment.recipe_name and definition is None:
            self._finish("faulted", "selected experiment durable descriptor is unavailable")
            return True
        run["deadline"] = native_live_at + (definition.live_s if definition else RECIPES[run["recipe"]].live_s)
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
        duration = RECIPES[run["recipe"]].trajectory_s
        positive_definition = None
        if run["recipe"] in {LEFT_ELBOW_POSITIVE_CONTROL.recipe_name, SelectedElbowExperiment.recipe_name}:
            positive_definition = (
                _selected_experiment_definition(run)
                if run["recipe"] == SelectedElbowExperiment.recipe_name
                else LEFT_ELBOW_POSITIVE_CONTROL
            )
            if positive_definition is None or (
                isinstance(positive_definition, SelectedElbowExperiment)
                and execution.get("experiment") != positive_definition.summary()
            ):
                return None
            expected = positive_definition.completion_expectation(execution)
            if expected is None:
                return None
            duration, cycles, returns = expected
        elif run["recipe"] == LEFT_ELBOW_RESPONSE.recipe_name:
            expected = LEFT_ELBOW_RESPONSE.completion_expectation(execution)
            if expected is None:
                return None
            duration, cycles, returns = expected
        elif run["recipe"] == LEFT_ELBOW_ORIGINAL_RETURN.recipe_name:
            backend = evidence.get("backend_contract")
            spec = backend.get("elbow_return_reference") if isinstance(backend, dict) else None
            reference = evidence.get("retained_return_reference")
            if (
                not isinstance(spec, dict)
                or not isinstance(reference, dict)
                or reference.get("admission_motor_head") != backend.get("host_head")
            ):
                return None
            expected = LEFT_ELBOW_ORIGINAL_RETURN.completion_expectation(
                execution,
                reference,
                spec.get("sha256"),
            )
            if expected is None:
                return None
            duration, cycles, returns = expected
        elif run["recipe"] in ELBOW_RECOVERY_DEFINITIONS:
            definition = ELBOW_RECOVERY_DEFINITIONS[run["recipe"]]
            backend = evidence.get("backend_contract")
            spec = backend.get("elbow_return_reference") if isinstance(backend, dict) else None
            reference = evidence.get("retained_return_reference")
            if (
                not isinstance(spec, dict)
                or not isinstance(reference, dict)
                or reference.get("admission_motor_head") != backend.get("host_head")
            ):
                return None
            expected = definition.completion_expectation(execution, reference, spec.get("sha256"))
            gain = execution.get("recovery", {}).get("gain_trial")
            if expected is None or not definition.gain_trial_valid(
                gain,
                run_id=run["run_id"],
                host_incarnation=incarnation,
                now=receipt.get("completed_at_monotonic_s"),
            ):
                return None
            duration, cycles, returns = expected
        elif run["recipe"] in {"physical-left-elbow-compare", "physical-left-elbow-dwell-compare"}:
            returns, cycles = 1, 2
        else:
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
            and execution.get("progress_s") == duration
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
        if positive_definition is not None:
            from examples.alohamini.am1_finite_task import JOINT_KEYS

            outcome = execution["positive_control"]
            window = outcome["final_window"]
            origin_receipt = outcome["phase_receipts"][f"cycle{cycles}_origin"]
            prerequisite = run.get("positive_prerequisite")
            if run.get("confirmation_of") is not None and (
                not isinstance(prerequisite,dict) or prerequisite.get("run_id") != outcome["confirmation_of"]
                or prerequisite.get("original_seed") != outcome["comparison_reference"]
                or prerequisite.get("reference_normalized") != outcome["reference_normalized"]
                or prerequisite.get("calibration") != outcome["calibration"]
                or prerequisite.get("measured_origin_raw") != outcome["measured_origin_raw"]
                or prerequisite.get("measured_origin_proof") != outcome["measured_origin_proof"]
            ):
                return None
            if (set(seed) != set(JOINT_KEYS)
                or any(origin_receipt[k] != ack[k] for k in ("epoch","intent_revision"))
                or origin_receipt["action_sequence"] > ack["accepted_action_sequence"]
                or origin_receipt["observation_id"] > sequence
                or not positive_definition.settings_valid(outcome["visible_control"],run["run_id"],incarnation)
                or min(window["first_current_started_at"],window["first_position_started_at"]) < run["native_live_at"]
                or not window["qualified_at_monotonic_s"] <= completed
                or completed-window["last_position_started_at"] > .5
                or any(abs(positions[k]-v) > (10 if k == positive_definition.joint else 1) for k,v in seed.items())
                or outcome.get("confirmation_of") != run.get("confirmation_of")):
                return None
        if run["recipe"] == LEFT_ELBOW_ORIGINAL_RETURN.recipe_name:
            from examples.alohamini.am1_finite_task import JOINT_KEYS

            raw = LEFT_ELBOW_ORIGINAL_RETURN.encoded_goal(
                positions.get(LEFT_ELBOW_ORIGINAL_RETURN.joint), reference["calibration"]
            )
            window = execution["retained_return"]["final_window"]
            if (
                set(seed) != set(JOINT_KEYS)
                or raw is None
                or abs(raw - reference["reference_raw"]) > 2
                or min(window["first_current_started_at"], window["first_position_started_at"])
                < run["native_live_at"]
                or window["qualified_at_monotonic_s"] > completed
                or any(
                    abs(positions[k] - v) > 1
                    for k, v in seed.items()
                    if k != LEFT_ELBOW_ORIGINAL_RETURN.joint
                )
            ):
                return None
        if run["recipe"] in ELBOW_RECOVERY_DEFINITIONS:
            from examples.alohamini.am1_finite_task import JOINT_KEYS

            outcome = execution["recovery"]
            window = outcome["final_window"]
            raw = definition.encoded_goal(positions.get(definition.joint), reference["calibration"])
            if (
                set(seed) != set(JOINT_KEYS)
                or raw is None
                or min(window["first_current_started_at"], window["first_position_started_at"])
                < run["native_live_at"]
                or window["qualified_at_monotonic_s"] > completed
                or abs(raw - outcome["reference_raw"]) > outcome["final_return_residual_raw"]
                or any(abs(positions[k] - v) > 1 for k, v in seed.items() if k != definition.joint)
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

        self._archive_terminal()

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

    def _startup_literal_receipts(self, run, record):
        lifecycle = run["physical_result"]
        cleanup = _continuation_bytes(Path(lifecycle["run_directory"]) / "cleanup.json")
        camera = _continuation_bytes(
            Path(run["backend_contract"]["camera_state"]) / ("release-" + run["run_id"] + ".json")
        )
        return (
            hashlib.sha256(cleanup).hexdigest() == record["failed_cleanup_sha256"]
            and hashlib.sha256(camera).hexdigest() == record["failed_camera_sha256"]
            and _canonical(json.loads(cleanup)) == _canonical(lifecycle["readback"])
            and _canonical(json.loads(camera)) == _canonical(lifecycle["camera_release"])
        )

    def _startup_source_transition(self, record, evidence):
        from tools.am1_session_ipc import strict_json

        transition, prior = record["source_transition"], record["prior_run"]
        before, after = transition["before"], transition["after"]
        paths = {
            "src/lerobot/robots/alohamini/alohamini.py",
            "src/lerobot/robots/alohamini/alohamini_host.py",
        }
        if (
            _canonical(before) != _canonical(self.run["backend_contract"])
            or _canonical(after) != _canonical(evidence.get("backend_contract"))
            or _canonical(transition["before_reference"]) != _canonical(self.run["retained_return_reference"])
            or _canonical(transition["after_reference"])
            != _canonical(evidence.get("retained_return_reference"))
        ):
            return False
        note = self.run.get("startup_continuation")
        if note is None:
            if (
                _canonical(before) != _canonical(prior["backend_contract"])
                or _canonical(transition["before_reference"])
                != _canonical(prior["retained_return_reference"])
                or set(transition["changed_motor_files"]) != paths
            ):
                return False
        else:
            if (
                not isinstance(note, dict)
                or set(note) != {"failed_run_id", "prior_run_id", "receipt_sha256"}
                or any(uuid_text(note[k]) != note[k] for k in ("failed_run_id", "prior_run_id"))
                or note["prior_run_id"] != prior["run_id"]
                or note["failed_run_id"] in (self.run["run_id"], prior["run_id"])
                or not isinstance(note["receipt_sha256"], str)
                or len(note["receipt_sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in note["receipt_sha256"])
            ):
                return False
            archive = self.directory / (
                "elbow-recovery-startup-continuation." + note["receipt_sha256"] + ".json"
            )
            raw = _continuation_bytes(archive)
            old = strict_json(raw)
            fields = {
                "version",
                "kind",
                "failed_run_id",
                "failed_terminal_sha256",
                "failed_cleanup_sha256",
                "failed_camera_sha256",
                "prior_run",
                "source_transition",
            }
            if (
                hashlib.sha256(raw).hexdigest() != note["receipt_sha256"]
                or not isinstance(old, dict)
                or set(old) != fields
                or type(old["version"]) is not int
                or old["version"] != 1
                or old["kind"] != "am1-elbow-startup-continuation"
                or old["failed_run_id"] != note["failed_run_id"]
                or any(
                    not isinstance(old[k], str)
                    or len(old[k]) != 64
                    or any(c not in "0123456789abcdef" for c in old[k])
                    for k in ("failed_terminal_sha256", "failed_cleanup_sha256", "failed_camera_sha256")
                )
                or _canonical(old["prior_run"]) != _canonical(prior)
            ):
                return False
            previous = old["source_transition"]
            if (
                _canonical(previous["before"]) != _canonical(prior["backend_contract"])
                or _canonical(previous["before_reference"]) != _canonical(prior["retained_return_reference"])
                or _canonical(previous["after"]) != _canonical(before)
                or _canonical(previous["after_reference"]) != _canonical(transition["before_reference"])
                or set(previous["changed_motor_files"]) != paths
                or not self._startup_material_transition(previous, historical=True)
            ):
                return False
        return self._startup_material_transition(transition, all_final=note is not None)

    def _startup_material_transition(self, transition, *, historical=False, all_final=False):
        """Historical intermediate manifests are sealed; final bytes belong to the current checkout."""
        from tools.am1_session_ipc import strict_json

        paths = {
            "src/lerobot/robots/alohamini/alohamini.py",
            "src/lerobot/robots/alohamini/alohamini_host.py",
        }
        if set(transition) != {
            "before",
            "after",
            "before_reference",
            "after_reference",
            "changed_motor_files",
        }:
            return False
        before, after = transition["before"], transition["after"]
        changed = set(transition["changed_motor_files"])
        if (
            set(before) != set(after)
            or {k for k in before if _canonical(before[k]) != _canonical(after[k])}
            - {"host_head", "source_pins", "elbow_return_reference"}
            or before["host_head"] == after["host_head"]
            or _canonical(transition["after_reference"])
            != _canonical(dict(transition["before_reference"], admission_motor_head=after["host_head"]))
            or not changed
            or not changed <= paths
        ):
            return False
        motor_paths = (
            paths
            | {
                "src/lerobot/robots/alohamini/" + name
                for name in (
                    "alohamini_client.py",
                    "config_alohamini.py",
                    "lift_motor_feedback.py",
                    "lift_operational.py",
                    "lift_relief.py",
                    "selected_joint_evidence.py",
                    "am1_elbow_integral.py",
                )
            }
            | {"tools/run_am1_host.sh", "examples/alohamini/am1_session_contract.py"}
        )
        manifest_fields = {
            "version",
            "motor",
            "helper",
            "camera",
            "calibration_sha256",
            "lift_delta_files",
            "executor_base",
            "executor_head",
            "executor_bundle_sha256",
        }
        projection_fields = {"base", "head", "ref", "files", "bundle_sha256"}
        manifests, rows = [], []

        def digest(value, length):
            return (
                isinstance(value, str)
                and len(value) == length
                and all(c in "0123456789abcdef" for c in value)
            )

        for config in (before, after):
            manifest = strict_json(_continuation_bytes(config["source_pins"]))
            motor = manifest.get("motor")
            if (
                set(manifest) != manifest_fields
                or type(manifest.get("version")) is not int
                or manifest["version"] != 2
                or not isinstance(motor, dict)
                or set(motor) != projection_fields
                or motor["head"] != config["host_head"]
                or not digest(motor["base"], 40)
                or not digest(motor["head"], 40)
                or not digest(motor["bundle_sha256"], 64)
                or not isinstance(motor["ref"], str)
                or not motor["ref"].startswith("refs/heads/")
                or not isinstance(motor["files"], list)
                or len(motor["files"]) != 11
                or not digest(manifest["executor_base"], 40)
                or not digest(manifest["executor_head"], 40)
                or not digest(manifest["executor_bundle_sha256"], 64)
                or not digest(manifest["calibration_sha256"], 64)
            ):
                return False
            file_rows = {}
            for row in motor["files"]:
                if (
                    not isinstance(row, dict)
                    or set(row) != {"path", "sha256"}
                    or row["path"] not in motor_paths
                    or row["path"] in file_rows
                    or not digest(row["sha256"], 64)
                ):
                    return False
                file_rows[row["path"]] = row["sha256"]
            if set(file_rows) != motor_paths:
                return False
            manifests.append(manifest)
            rows.append(file_rows)
            ref = config["elbow_return_reference"]
            if (
                set(ref) != {"path", "sha256"}
                or not digest(ref["sha256"], 64)
                or hashlib.sha256(_continuation_bytes(ref["path"])).hexdigest() != ref["sha256"]
            ):
                return False
        old_manifest, new_manifest = manifests
        if any(
            _canonical(old_manifest[k]) != _canonical(new_manifest[k])
            for k in manifest_fields - {"motor", "executor_head", "executor_bundle_sha256"}
        ) or any(old_manifest["motor"][k] != new_manifest["motor"][k] for k in ("base", "ref")):
            return False
        old, new = rows
        if {p for p in old if old[p] != new[p]} != changed:
            return False
        for path in changed:
            row = transition["changed_motor_files"][path]
            if (
                set(row) != {"before_sha256", "after_sha256"}
                or row["before_sha256"] != old[path]
                or row["after_sha256"] != new[path]
            ):
                return False
        if not historical:
            for path in motor_paths if all_final else changed:
                if (
                    hashlib.sha256(
                        _continuation_bytes(Path(after["host_repository"]) / path, limit=262144, source=True)
                    ).hexdigest()
                    != new[path]
                ):
                    return False
        return True

    @staticmethod
    def _startup_configured_only(gain, calibration):
        """Known configured/restored stage, never an invented active verification."""
        if any(
            k in gain
            for k in ("active_verified", "active_registers", "active_bytes", "active_verified_at_monotonic_s")
        ) or gain.get("before_apply_bytes") != gain.get("original_bytes"):
            return False
        events = gain.get("events")
        if not isinstance(events, list) or not 1 <= len(events) <= 128:
            return False
        end = 0.0
        for row in events:
            if (
                not isinstance(row, dict)
                or row.get("operation") not in ("read", "write")
                or not isinstance(row.get("phase"), str)
                or row["phase"].startswith("active")
                or row.get("error") is not None
                or row.get("error_type") is not None
                or type(row.get("value")) is not int
            ):
                return False
            start, completed = row.get("started_at_monotonic_s"), row.get("completed_at_monotonic_s")
            if (
                any(type(t) not in (int, float) or not math.isfinite(t) for t in (start, completed))
                or not end <= start <= completed
            ):
                return False
            end = completed
        phases = ("baseline", "before_apply", "configured", "restored", "restore_original_lock_verify")
        previous = 0.0
        for phase in phases:
            rows = [r for r in events if r["phase"] == phase and r["operation"] == "read"]
            if not rows or min(r["started_at_monotonic_s"] for r in rows) < previous:
                return False
            previous = max(r["completed_at_monotonic_s"] for r in rows)
        restored_at = gain.get("restored_at_monotonic_s")
        if type(restored_at) not in (int, float) or not math.isfinite(restored_at) or restored_at < end:
            return False

        def read(phase, key, expected):
            rows = [
                r
                for r in events
                if r["phase"] == phase and r["operation"] == "read" and r.get("register") == key
            ]
            return len(rows) == 1 and rows[0]["value"] == expected

        original = gain["original_registers"]
        expected = {
            "Model_Number": 777,
            "Firmware_Major_Version": 3,
            "Firmware_Minor_Version": 10,
            "Operating_Mode": 0,
            "P_Coefficient": 16,
            "D_Coefficient": 32,
            "Min_Position_Limit": calibration["range_min"],
            "Max_Position_Limit": calibration["range_max"],
        }
        if (
            any(
                type(original.get(k)) is not int or original[k] != v
                for k, v in dict(expected, Torque_Enable=0).items()
            )
            or not all(
                read("baseline", k, v)
                for k, v in dict(expected, I_Coefficient=0, Lock=0, Torque_Enable=0).items()
            )
            or not all(
                read("before_apply", k, v)
                for k, v in dict(expected, I_Coefficient=0, Lock=1, Torque_Enable=0).items()
            )
            or not all(read("restored", k, v) for k, v in expected.items())
            or not read("configured", "I_Coefficient", 1)
            or not read("restored", "I_Coefficient", 0)
            or not read("restore_original_lock_verify", "Lock", 0)
        ):
            return False
        for phase in ("baseline", "before_apply", "restored"):
            for address, value in gain["original_bytes"].items():
                rows = [
                    r
                    for r in events
                    if r["phase"] == phase and r["operation"] == "read" and r.get("address") == int(address)
                ]
                if (
                    len(rows) != 1
                    or type(rows[0].get("width")) is not int
                    or rows[0]["width"] != 1
                    or rows[0]["value"] != value
                ):
                    return False
        return True

    def _startup_continuation(self, recipe, evidence):
        from tools.am1_physical_executor import qualified_cleanup_receipt
        from tools.am1_session_ipc import strict_json

        run = self.run
        definition = ELBOW_RECOVERY_DEFINITIONS.get(recipe.name)
        if (
            definition is None
            or definition.integral != 1
            or definition.mode != "return"
            or definition.hold_s != 0.5
            or not isinstance(run, dict)
            or run.get("recipe") != recipe.name
            or run.get("status") != "faulted"
            or run.get("source") != "protected-physical-provider"
            or run.get("uncertain") is not False
            or run.get("cleanup") != "physical_readback_verified"
            or type(run.get("progress_s")) not in (int, float)
            or run["progress_s"] != 0
            or run.get("native_live_at") is not None
            or run.get("deadline") is not None
            or run.get("execution") is not None
            or run.get("completion_receipt") is not None
            or run.get("intent") is not None
            or run.get("recovery_episodes") != 0
            or run.get("native_first_cause")
            != "ComparisonRefusal: temperature: five-slot feedback window is stale (>0.5 s)."
        ):
            return None
        try:
            raw = _continuation_bytes(self.directory / "elbow-recovery-startup-continuation.json")
            record = strict_json(raw)
            fields = {
                "version",
                "kind",
                "failed_run_id",
                "failed_terminal_sha256",
                "failed_cleanup_sha256",
                "failed_camera_sha256",
                "prior_run",
                "source_transition",
            }
            if (
                not isinstance(record, dict)
                or set(record) != fields
                or type(record["version"]) is not int
                or record["version"] != 1
                or record["kind"] != "am1-elbow-startup-continuation"
                or record["failed_run_id"] != run["run_id"]
                or record["failed_terminal_sha256"] != hashlib.sha256(_canonical(run)).hexdigest()
                or not isinstance(record["prior_run"], dict)
                or hashlib.sha256(_canonical(record["prior_run"])).hexdigest()
                != run["recovery_prerequisite"]["proof_sha256"]
                or not self._startup_source_transition(record, evidence)
                or not self._startup_literal_receipts(run, record)
            ):
                return None
            lifecycle = run["physical_result"]
            incarnation = lifecycle["backend_incarnation"]
            receipt = lifecycle["readback"]
            rb = receipt["readback"]
            gain = rb["elbow_integral"]
            original = gain["original_registers"]
            expected_registers = {
                "Model_Number": 777,
                "Firmware_Major_Version": 3,
                "Firmware_Minor_Version": 10,
                "Operating_Mode": 0,
                "P_Coefficient": 16,
                "D_Coefficient": 32,
            }
            active = gain.get("active_registers")
            original_bytes = gain.get("original_bytes")
            linked = run.get("startup_continuation") is not None
            if (
                any(
                    type(original.get(k)) is not int or original[k] != v
                    for k, v in expected_registers.items()
                )
                or type(gain.get("configured_integral_readback")) is not int
                or gain["configured_integral_readback"] != 1
                or not isinstance(original_bytes, dict)
                or set(original_bytes) != {"24", "25"}
                or any(type(v) is not int or not 0 <= v <= 255 for v in original_bytes.values())
                or gain.get("restored_bytes") != original_bytes
            ):
                return None
            if linked:
                if not self._startup_configured_only(gain, run["recovery_prerequisite"]["calibration"]):
                    return None
            elif (
                not isinstance(active, dict)
                or gain.get("active_verified") is not True
                or gain.get("active_bytes") != original_bytes
                or any(
                    type(active.get(k)) is not int or active[k] != v
                    for k, v in dict(expected_registers, I_Coefficient=1, Lock=1, Torque_Enable=1).items()
                )
            ):
                return None
            camera = lifecycle["camera_release"]
            release = camera["release_evidence"]
            if (
                uuid_text(incarnation) != incarnation
                or lifecycle.get("run_id") != run["run_id"]
                or lifecycle.get("phase") != "terminal"
                or lifecycle.get("terminal_status") != "faulted"
                or lifecycle.get("native_live_at") is not None
                or lifecycle.get("uncertain") is not False
                or lifecycle.get("cleanup_errors") != []
                or not qualified_cleanup_receipt(receipt, run["run_id"], incarnation)
                or type(receipt.get("version")) is not int
                or rb.get("buses_closed") is not True
                or rb.get("cleanup_errors") != []
                or rb.get("selected_gain_restore_required") is not False
                or gain.get("state") != "restored"
                or gain.get("status") != "restored"
                or gain.get("restore_required") is not False
                or type(gain.get("integral")) is not int
                or gain["integral"] != 1
                or gain.get("binding")
                != {
                    "run_id": run["run_id"],
                    "host_incarnation": incarnation,
                    "recipe": recipe.name,
                    "integral": 1,
                }
                or type(original.get("I_Coefficient")) is not int
                or original["I_Coefficient"] != 0
                or type(original.get("Lock")) is not int
                or original["Lock"] != 0
                or type(gain.get("restored_integral_readback")) is not int
                or gain["restored_integral_readback"] != 0
                or type(gain.get("restored_lock_readback")) is not int
                or gain["restored_lock_readback"] != 0
                or gain.get("restored_bytes") != gain.get("original_bytes")
                or gain.get("first_failure") is not None
                or type(camera.get("version")) is not int
                or camera["version"] != 1
                or camera.get("session_id") != run["run_id"]
                or camera.get("state") != "released"
                or camera.get("errors") != []
                or camera.get("runtime_failed") is not False
                or type(release.get("backend_returncode")) is not int
                or release["backend_returncode"] != 0
                or any(
                    release.get(k) is not True
                    for k in (
                        "backend_exited",
                        "readers_joined",
                        "viewer_socket_closed",
                        "runtime_config_removed",
                    )
                )
            ):
                return None
            transition = record["source_transition"]
            # Compare the new idle owner against the explicitly validated material transition.
            expected_idle = dict(
                run,
                backend_contract=transition["after"],
                retained_return_reference=transition["after_reference"],
            )
            if not _retained_recovery_idle(evidence, expected_idle, self.service_incarnation):
                return None
            prior = record["prior_run"]
            proof = self._recovery_prerequisite_from(recipe, {}, prior, retained=True)
            if proof is None or _canonical(proof) != _canonical(run["recovery_prerequisite"]):
                return None
            self._startup_continuation_note = {
                "failed_run_id": run["run_id"],
                "prior_run_id": prior["run_id"],
                "receipt_sha256": hashlib.sha256(raw).hexdigest(),
            }
            return proof
        except (OSError, AttributeError, KeyError, TypeError, ValueError):
            return None

    def positive_control_prerequisite(self, confirmation_of, evidence):
        """One explicit confirmation of this authority's intact completed primary."""
        from tools.am1_physical_executor import qualified_cleanup_receipt

        old = self.run
        try:
            if (not isinstance(old,dict) or uuid_text(confirmation_of) != confirmation_of
                or old.get("run_id") != confirmation_of or old.get("recipe") != LEFT_ELBOW_POSITIVE_CONTROL.recipe_name
                or old.get("source") != "protected-physical-provider" or old.get("status") != "completed"
                or old.get("uncertain") is not False or old.get("first_cause") is not None
                or old.get("cleanup") != "physical_readback_verified" or old.get("confirmation_of") is not None
                or old.get("backend_contract") != evidence.get("backend_contract") or evidence.get("fault") is not None):
                return None
            receipt, lifecycle, execution = old["completion_receipt"],old["physical_result"],old["execution"]
            outcome = execution["positive_control"]
            if (receipt.get("execution") != execution or receipt.get("run_id") != confirmation_of
                or receipt.get("recipe") != LEFT_ELBOW_POSITIVE_CONTROL.recipe_name
                or receipt.get("host_incarnation") != old.get("backend_incarnation")
                or LEFT_ELBOW_POSITIVE_CONTROL.completion_expectation(execution) is None
                or outcome.get("confirmation_of") is not None or outcome.get("coarse_functional_pass") is not True
                or outcome.get("stationary") is not True or outcome.get("adverse") is not False
                or lifecycle.get("run_id") != confirmation_of or lifecycle.get("backend_incarnation") != receipt["host_incarnation"]
                or lifecycle.get("phase") != "terminal" or lifecycle.get("terminal_status") != "completed"
                or lifecycle.get("uncertain") is not False or lifecycle.get("cleanup_errors") != []
                or lifecycle.get("first_cause") is not None
                or not LEFT_ELBOW_POSITIVE_CONTROL.settings_valid(outcome["visible_control"],confirmation_of,receipt["host_incarnation"])
                or not qualified_cleanup_receipt(lifecycle.get("readback"),confirmation_of,receipt["host_incarnation"])):
                return None
            current = evidence.get("lifecycle")
            if current != lifecycle:
                if (current != {"run_id":None,"phase":"idle","startup_deadline":None,"native_live_at":None,
                                "cleanup":None,"uncertain":False,"terminal_status":None}
                    or old.get("service_incarnation") == getattr(self,"service_incarnation",None)
                    or evidence.get("source") != "protected-physical-provider"
                    or any(evidence.get(k) is not False for k in ("feedback","native_ack","active_acknowledged","hold_acknowledged","pose_aligned"))
                    or any(evidence.get(k) is not None for k in ("transport_issue","reconciliation","host_incarnation","finite_completion"))
                    or evidence.get("accepted_host_action") != {}):
                    return None
            readback = lifecycle["readback"]["readback"]
            camera = lifecycle.get("camera_release")
            release = camera.get("release_evidence") if isinstance(camera,dict) else None
            if (readback.get("verified") is not True or readback.get("buses_closed") is not True
                or readback.get("cleanup_errors") != [] or readback.get("selected_gain_restore_required") is not False
                or not isinstance(camera,dict) or type(camera.get("version")) is not int or camera["version"] != 1
                or camera.get("session_id") != confirmation_of or camera.get("state") != "released"
                or camera.get("errors") != [] or camera.get("runtime_failed") is not False
                or not isinstance(release,dict) or type(release.get("backend_returncode")) is not int
                or release["backend_returncode"] != 0 or any(release.get(k) is not True for k in
                    ("backend_exited","readers_joined","viewer_socket_closed","runtime_config_removed"))):
                return None
            return {"run_id":confirmation_of,"reference_normalized":outcome["reference_normalized"],
                    "original_seed":dict(execution["original_seed"]),
                    "measured_origin_raw":outcome["measured_origin_raw"],
                    "measured_origin_proof":outcome["measured_origin_proof"],
                    "calibration":outcome["calibration"],"proof_sha256":hashlib.sha256(_canonical(receipt)).hexdigest()}
        except (KeyError,ValueError,TypeError,AttributeError):
            return None

    def elbow_recovery_prerequisite(self, recipe, evidence):
        self._startup_continuation_note = None
        proof = self._recovery_prerequisite_from(recipe, evidence, self.run)
        return proof if proof is not None else self._startup_continuation(recipe, evidence)

    def _recovery_prerequisite_from(self, recipe, evidence, previous, *, retained=False):
        """Qualify only this authority's retained native result before replacing its run."""
        definition = ELBOW_RECOVERY_DEFINITIONS.get(recipe.name)
        if definition is None or not isinstance(previous, dict):
            return None
        old = ELBOW_RECOVERY_DEFINITIONS.get(previous.get("recipe"))
        try:
            from tools.am1_physical_executor import qualified_cleanup_receipt

            receipt = previous.get("completion_receipt")
            lifecycle = previous.get("physical_result")
            execution = previous.get("execution")
            reference = previous.get("retained_return_reference")
            spec = previous.get("backend_contract", {}).get("elbow_return_reference", {})
            if (
                old is None
                or previous.get("source") != "protected-physical-provider"
                or previous.get("status") != "completed"
                or previous.get("uncertain") is not False
                or previous.get("cleanup") != "physical_readback_verified"
                or previous.get("first_cause") is not None
                or not isinstance(receipt, dict)
                or not isinstance(lifecycle, dict)
                or not (
                    retained
                    or evidence.get("lifecycle") == lifecycle
                    or _retained_recovery_idle(evidence, previous, getattr(self, "service_incarnation", None))
                )
                or lifecycle.get("phase") != "terminal"
                or lifecycle.get("terminal_status") != "completed"
                or lifecycle.get("uncertain") is not False
                or lifecycle.get("cleanup_errors") != []
                or lifecycle.get("first_cause") is not None
                or receipt.get("run_id") != previous["run_id"]
                or receipt.get("recipe") != old.recipe_name
                or receipt.get("execution") != execution
                or receipt.get("host_incarnation") != previous.get("backend_incarnation")
                or lifecycle.get("run_id") != previous["run_id"]
                or lifecycle.get("backend_incarnation") != receipt["host_incarnation"]
                or old.completion_expectation(execution, reference, spec.get("sha256")) is None
                or not qualified_cleanup_receipt(
                    lifecycle.get("readback"), previous["run_id"], receipt["host_incarnation"]
                )
            ):
                return None
            motor_receipt = lifecycle["readback"]
            readback = motor_receipt.get("readback")
            camera = lifecycle.get("camera_release")
            release = camera.get("release_evidence") if isinstance(camera, dict) else None
            if (
                type(motor_receipt.get("version")) is not int
                or not isinstance(readback, dict)
                or readback.get("buses_closed") is not True
                or readback.get("verified") is not True
                or readback.get("cleanup_errors") != []
                or readback.get("selected_gain_restore_required") is not False
                or not isinstance(camera, dict)
                or type(camera.get("version")) is not int
                or camera["version"] != 1
                or camera.get("session_id") != previous["run_id"]
                or camera.get("state") != "released"
                or camera.get("errors") != []
                or camera.get("runtime_failed") is not False
                or not isinstance(release, dict)
                or type(release.get("backend_returncode")) is not int
                or release["backend_returncode"] != 0
                or any(
                    release.get(k) is not True
                    for k in (
                        "backend_exited",
                        "readers_joined",
                        "viewer_socket_closed",
                        "runtime_config_removed",
                    )
                )
            ):
                return None
            gain = execution["recovery"].get("gain_trial")
            if not old.gain_trial_valid(
                gain,
                run_id=previous["run_id"],
                host_incarnation=receipt["host_incarnation"],
                now=receipt["completed_at_monotonic_s"],
            ):
                return None
            restored = lifecycle["readback"]["readback"].get("elbow_integral")
            if (
                not isinstance(restored, dict)
                or restored.get("state") != "restored"
                or restored.get("restore_required") is not False
                or restored.get("binding") != gain["binding"]
                or type(restored.get("restored_integral_readback")) is not int
                or restored["restored_integral_readback"] != 0
                or type(restored.get("restored_lock_readback")) is not int
                or restored["restored_lock_readback"] != gain["original_registers"]["Lock"]
                or restored.get("first_failure") is not None
            ):
                return None
            outcome = execution["recovery"]
            # Directed partial recovery is a separate question from the old half-excursion benchmark.
            useful = outcome["max_toward_target_raw"] >= outcome["baseline_noise_raw"] + 3
            if definition.mode == "confirm":
                eligible = (
                    old.mode == "return"
                    and old.integral == definition.integral
                    and useful
                    and outcome["target_attained"]
                )
            elif definition.hold_s == 2:
                eligible = (
                    (
                        old.mode == "return"
                        and old.integral == 1
                        and old.hold_s == 0.5
                        and useful
                        and outcome["target_attained"] is False
                    )
                    if definition.integral == 0
                    else (
                        old.mode == "return"
                        and old.integral == 0
                        and old.hold_s == 2
                        and isinstance(previous.get("recovery_prerequisite"), dict)
                        and previous["recovery_prerequisite"].get("integral") == 1
                        and previous["recovery_prerequisite"].get("hold_s") == 0.5
                    )
                )
            else:
                eligible = old.mode == "return" and old.integral == 0 and old.hold_s == 0.5
            if not eligible:
                return None
            from examples.alohamini.am1_finite_task import JOINT_KEYS

            initial = execution.get("initial_measured_reference")
            if (
                not isinstance(initial, dict)
                or set(initial) != set(JOINT_KEYS)
                or not all(type(v) in (int, float) and math.isfinite(v) for v in initial.values())
            ):
                return None
            digest = hashlib.sha256(
                json.dumps(previous, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()
            ).hexdigest()
            return {
                "run_id": previous["run_id"],
                "host_incarnation": receipt["host_incarnation"],
                "proof_sha256": digest,
                "initial_measured_reference": json.loads(json.dumps(initial)),
                "calibration": json.loads(json.dumps(outcome["calibration"])),
                "trajectory_s": old.trajectory_s,
                "hold_s": old.hold_s,
                "integral": old.integral,
            }
        except (AttributeError, KeyError, TypeError, ValueError):
            return None

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
            allowed = {"op", "operation_id", "recipe", "controller_generation"}
            if recipe.name == LEFT_ELBOW_POSITIVE_CONTROL.recipe_name:
                allowed.add("confirmation_of")
            selected_definition = None
            if recipe.name == SelectedElbowExperiment.recipe_name:
                allowed.add("selected_experiment")
                if self.executor.source != "protected-physical-provider":
                    return self._refuse("selected experiment requires the protected physical source")
                try:
                    selected_definition = SelectedElbowExperiment.from_payload(c.get("selected_experiment"))
                except (AttributeError, TypeError, ValueError) as error:
                    return self._refuse(str(error))
            if set(c) - allowed:
                return self._refuse("recipe parameters forbidden")
            confirmation_of = c.get("confirmation_of")
            if "confirmation_of" in c:
                try:
                    if uuid_text(confirmation_of) != confirmation_of:
                        return self._refuse("canonical positive confirmation identity required")
                except (ValueError,TypeError,AttributeError):
                    return self._refuse("canonical positive confirmation identity required")
            canonical = json.dumps(
                {"recipe": recipe.name, "device_id": d, **({"confirmation_of":confirmation_of} if confirmation_of else {}),
                 **({"selected_experiment":selected_definition.payload} if selected_definition else {})},
                sort_keys=True, separators=(",", ":")
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
            positive_prerequisite = None
            selected_setter = getattr(self.executor, "set_selected_experiment", None)
            if callable(selected_setter):
                try:
                    selected_setter(selected_definition)
                except (RuntimeError, ValueError) as error:
                    return self._refuse(str(error))
            elif selected_definition is not None:
                return self._refuse("selected experiment requires the protected executor")
            if recipe.name == LEFT_ELBOW_POSITIVE_CONTROL.recipe_name:
                if confirmation_of is not None:
                    positive_prerequisite = self.positive_control_prerequisite(confirmation_of,evidence)
                    if positive_prerequisite is None:
                        return self._refuse("positive confirmation requires qualified primary native outcome and known cleanup")
                setter = getattr(self.executor,"set_positive_prerequisite",None)
                if callable(setter):
                    try:
                        setter(positive_prerequisite)
                    except RuntimeError as error:
                        return self._refuse(str(error))
                elif confirmation_of is not None:
                    return self._refuse("positive confirmation requires protected executor")
            recovery_prerequisite = None
            recovery_definition = ELBOW_RECOVERY_DEFINITIONS.get(recipe.name)
            if recovery_definition is not None:
                first_i0 = (
                    recovery_definition.integral == 0
                    and recovery_definition.mode == "return"
                    and recovery_definition.hold_s == 0.5
                )
                if not first_i0:
                    recovery_prerequisite = self.elbow_recovery_prerequisite(recipe, evidence)
                    if recovery_prerequisite is None:
                        return self._refuse(
                            "recovery comparison requires its own qualified native prior outcome and restored settings"
                        )
                setter = getattr(self.executor, "set_recovery_prerequisite", None)
                if callable(setter):
                    try:
                        setter(recovery_prerequisite)
                    except RuntimeError as error:
                        return self._refuse(str(error))
                elif not first_i0:
                    return self._refuse("recovery prerequisite requires the protected native executor")
            continuation_note = (
                getattr(self, "_startup_continuation_note", None) if recovery_prerequisite else None
            )
            self._archive_terminal()
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
            if recipe.name == LEFT_ELBOW_POSITIVE_CONTROL.recipe_name:
                self.run["confirmation_of"] = confirmation_of
                self.run["positive_prerequisite"] = positive_prerequisite
            if selected_definition is not None:
                self.run["selected_experiment"] = selected_definition.payload
                self.run["selected_experiment_sha256"] = selected_definition.sha256
            if recovery_definition is not None:
                self.run["recovery_prerequisite"] = recovery_prerequisite
                if continuation_note is not None:
                    self.run["startup_continuation"] = dict(continuation_note)
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
            trajectory = None if run is None else RECIPES[run["recipe"]].trajectory_s
            if run is not None and run["recipe"] == LEFT_ELBOW_POSITIVE_CONTROL.recipe_name:
                trajectory = 28.0 + (2.0 if run.get("confirmation_of") is not None else 0.0)
            if run is not None and run["recipe"] == SelectedElbowExperiment.recipe_name:
                definition = _selected_experiment_definition(run)
                if definition is not None:
                    trajectory = definition.nominal_trajectory_s
            if run is not None and run["recipe"] == LEFT_ELBOW_RESPONSE.recipe_name:
                execution = run.get("execution")
                branch = LEFT_ELBOW_RESPONSE.branch_expectation(
                    execution.get("characterization") if isinstance(execution, dict) else None
                )
                if branch is not None:
                    trajectory = branch[0]
            if (
                run is not None
                and "reconciliation" in run
                and self.executor.source == "protected-physical-provider"
            ):
                run["reconciliation"] = _reconciliation_reference(self.run)
            evidence = self._evidence()
            reference = _positive_completion_reference(evidence, run) if run is not None else None
            public_evidence = _public_physical_evidence(evidence, run)
            if reference is not None:
                public_receipt = {
                    key: value for key, value in run["completion_receipt"].items() if key != "execution"
                }
                run["completion_receipt"] = dict(public_receipt, execution_reference=reference)
                if isinstance(evidence.get("finite_completion"), dict):
                    public_evidence = dict(
                        public_evidence, finite_completion=dict(public_receipt, execution_reference=reference)
                    )
            view = json.loads(
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
                            if trajectory is None
                            else max(0, trajectory - self.run["progress_s"]),
                        ),
                        "evidence": public_evidence,
                        "recipes": [
                            name
                            for name, recipe in RECIPES.items()
                            if self.executor.supports(recipe)
                            and (name != SelectedElbowExperiment.recipe_name
                                 or self.executor.source == "protected-physical-provider")
                            and (
                                name not in ELBOW_RECOVERY_DEFINITIONS
                                or (
                                    ELBOW_RECOVERY_DEFINITIONS[name].integral == 0
                                    and ELBOW_RECOVERY_DEFINITIONS[name].mode == "return"
                                    and ELBOW_RECOVERY_DEFINITIONS[name].hold_s == 0.5
                                )
                                or self.elbow_recovery_prerequisite(recipe, self._evidence()) is not None
                            )
                        ],
                        "events": list(self.events),
                    }
                )
            )

            view["presets"] = [dict(SELECTED_ELBOW_OPERATION_PRESET,
                selected_experiment=LEFT_ELBOW_COARSE_BENCH_V1.payload)] if (
                    SelectedElbowExperiment.recipe_name in view["recipes"]
                    and self.executor.source == "protected-physical-provider") else []
            if self.executor.source != "protected-physical-provider":
                return view
            return self._essential_status(view, evidence)

    def _status_reference(self, field, value):
        raw = _canonical(value)
        run = self.run or {}
        reference = dict(
            kind="am1-status-field-reference",
            version=1,
            service_incarnation=run.get("service_incarnation", self.service_incarnation)
            if field == "run"
            else self.service_incarnation,
            run_id=run.get("run_id"),
            operation_id=run.get("operation_id"),
            host_incarnation=run.get("backend_incarnation")
            or (
                run.get("physical_result", {}).get("backend_incarnation")
                if isinstance(run.get("physical_result"), dict)
                else None
            ),
            field=field,
            sha256=hashlib.sha256(raw).hexdigest(),
            bytes=len(raw),
        )
        cache = getattr(self, "_status_details", None)
        if cache is None:
            self._status_details = cache = OrderedDict()
        key = _canonical(reference)
        if len(raw) <= STATUS_DETAIL_CACHE_BYTES:
            cache[key] = raw
            cache.move_to_end(key)
            while len(cache) > 64 or sum(map(len, cache.values())) > STATUS_DETAIL_CACHE_BYTES:
                cache.popitem(last=False)
        return reference


    def _essential_status(self, view, evidence):
        run = view["run"]
        native_reference = self._status_reference("run", self.run) if run is not None else None
        evidence_reference = self._status_reference("evidence", evidence)
        events_reference = self._status_reference("events", view["events"])
        if run is not None:
            keys = {
                "run_id",
                "operation_id",
                "recipe",
                "source",
                "status",
                "dispatch",
                "progress_s",
                "deadline",
                "first_cause",
                "native_first_cause",
                "uncertain",
                "intent",
                "intent_revision",
                "recovery",
                "recovery_episodes",
                "cleanup",
                "service_incarnation",
                "backend_incarnation",
                "native_live_at",
                "startup_deadline",
                "finish_ceiling",
                "resume_ceiling",
                "requested_terminal",
                "remaining_s",
                "trajectory_remaining_s",
                "selected_experiment",
                "selected_experiment_sha256",
                "sensing_policy",
                "sensing_source",
                "observation_provenance",
                "observation_freshness_basis",
                "backend_contract",
            }
            public = _status_small(run, keys)
            public["execution"] = _status_execution(run.get("execution"))
            public["physical_result"] = _status_lifecycle(run.get("physical_result"), rows=True)
            receipt = run.get("completion_receipt")
            if isinstance(receipt, dict):
                public["completion_receipt"] = _status_small(
                    receipt,
                    {
                        "run_id",
                        "host_incarnation",
                        "intent_revision",
                        "recipe",
                        "completed_at_monotonic_s",
                        "feedback_at_monotonic_s",
                        "feedback_sequence",
                        "required_observation",
                        "return_reacknowledgment",
                    },
                )
            public["details_reference"] = native_reference
            view["run"] = public
        keys = {
            "source",
            "feedback",
            "required_observation",
            "optional_quality",
            "optional_observation",
            "pose_aligned",
            "hold_acknowledged",
            "native_ack",
            "active_acknowledged",
            "fault",
            "feedback_age_s",
            "observation_age_s",
            "observation",
            "feedback_sequence",
            "measured_positions",
            "measured_body",
            "feedback_timing",
            "accepted_host_action",
            "acknowledged_intent_revision",
            "host_incarnation",
            "transport_issue",
            "reconciliation",
            "sensing_policy",
            "sensing_source",
            "observation_provenance",
            "observation_freshness_basis",
            "motor_commands_provenance",
            "feedback_provenance",
            "ack_provenance",
            "backend_contract",
            "retained_return_reference",
        }
        public = _status_small(evidence, keys)
        public["lifecycle"] = _status_lifecycle(evidence.get("lifecycle"))
        completion = evidence.get("finite_completion")
        public["finite_completion"] = _status_small(
            completion,
            {
                "run_id",
                "host_incarnation",
                "intent_revision",
                "recipe",
                "completed_at_monotonic_s",
                "feedback_at_monotonic_s",
                "feedback_sequence",
                "required_observation",
                "return_reacknowledgment",
            },
        )
        public["details_reference"] = evidence_reference
        view.update(
            evidence=public,
            events=view["events"][-8:],
            events_reference=events_reference,
            status_projection={"kind": "am1-essential-status", "version": 1},
        )
        return view


    def _archive_terminal(self):
        if (
            not self.run
            or self.run.get("source") != "protected-physical-provider"
            or self.run.get("status") not in TERMINAL
        ):
            return
        from tools.am1_session_ipc import private_json, strict_json

        run_id = uuid_text(self.run["run_id"])
        raw = _canonical(self.run)
        sha = hashlib.sha256(raw).hexdigest()
        directory = self.directory / "run-results" / run_id
        for path in (directory.parent, directory):
            if path.is_symlink() or path.is_junction():
                raise ValueError("redirected native result archive")
            path.mkdir(mode=0o700, exist_ok=True)
            info = path.stat()
            if (
                not stat.S_ISDIR(info.st_mode)
                or os.name != "nt"
                and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700)
            ):
                raise ValueError("private native result archive required")
        target = directory / (sha + ".json")
        if target.exists():
            if _continuation_bytes(target, limit=STATUS_DETAIL_CACHE_BYTES) != raw:
                raise ValueError("original native result archive changed")
        else:
            temporary = target.with_suffix(".new")
            if temporary.is_symlink() or temporary.is_junction():
                raise ValueError("redirected native archive temporary")
            if temporary.exists():
                _continuation_bytes(temporary, limit=STATUS_DETAIL_CACHE_BYTES)
            private_json(target, strict_json(raw), limit=STATUS_DETAIL_CACHE_BYTES)


    def snapshot_detail(self, command):
        """Authenticated caller already owns the read path; never tick, act or reseed."""
        with self.mutex:
            if (
                not isinstance(command, dict)
                or set(command) != {"op", "reference", "offset", "limit"}
                or command["op"] != "snapshot"
            ):
                raise ValueError("closed detail command required")
            reference, offset, limit = command["reference"], command["offset"], command["limit"]
            if not isinstance(reference, dict) or set(reference) != STATUS_REFERENCE_KEYS:
                raise ValueError("closed native field reference required")
            if (
                reference.get("kind") != "am1-status-field-reference"
                or type(reference.get("version")) is not int
                or reference["version"] != 1
                or reference.get("field") not in {"run", "evidence", "events"}
                or type(reference.get("bytes")) is not int
                or not 0 < reference["bytes"] <= STATUS_DETAIL_CACHE_BYTES
                or not isinstance(reference.get("sha256"), str)
                or len(reference["sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in reference["sha256"])
                or type(offset) is not int
                or not 0 <= offset < reference["bytes"]
                or type(limit) is not int
                or not 1 <= limit <= STATUS_DETAIL_LIMIT
            ):
                raise ValueError("bounded native field reference required")
            for key in ("service_incarnation", "run_id", "operation_id", "host_incarnation"):
                if reference[key] is not None and uuid_text(reference[key]) != reference[key]:
                    raise ValueError("canonical native detail identity required")
            raw = getattr(self, "_status_details", {}).get(_canonical(reference))
            if raw is None and reference["field"] == "run" and reference["run_id"] is not None:
                try:
                    directory = self.directory / "run-results" / reference["run_id"]
                    for ancestor in (directory.parent, directory):
                        info = ancestor.lstat()
                        if (
                            ancestor.is_symlink()
                            or ancestor.is_junction()
                            or not stat.S_ISDIR(info.st_mode)
                            or os.name != "nt"
                            and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700)
                        ):
                            raise ValueError("redirected or nonprivate native archive")
                    raw = _continuation_bytes(
                        directory / (reference["sha256"] + ".json"), limit=STATUS_DETAIL_CACHE_BYTES
                    )
                    value = json.loads(raw)
                    host = value.get("backend_incarnation") or (
                        value.get("physical_result", {}).get("backend_incarnation")
                        if isinstance(value.get("physical_result"), dict)
                        else None
                    )
                    if (
                        _canonical(value) != raw
                        or value.get("status") not in TERMINAL
                        or host != reference["host_incarnation"]
                        or any(
                            value.get(k) != reference[r]
                            for k, r in (
                                ("run_id", "run_id"),
                                ("operation_id", "operation_id"),
                                ("service_incarnation", "service_incarnation"),
                            )
                        )
                    ):
                        raise ValueError("foreign archived native result")
                except (OSError, AttributeError, KeyError, TypeError) as error:
                    raise ValueError("native detail reference unavailable") from error
            if (
                raw is None
                or len(raw) != reference["bytes"]
                or hashlib.sha256(raw).hexdigest() != reference["sha256"]
            ):
                raise ValueError("stale or unavailable native detail reference")
            data = raw[offset : offset + limit]
            return dict(
                accepted=True,
                status="detail",
                reference=reference,
                offset=offset,
                length=len(data),
                total_bytes=len(raw),
                encoding="base64",
                data=base64.b64encode(data).decode("ascii"),
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
