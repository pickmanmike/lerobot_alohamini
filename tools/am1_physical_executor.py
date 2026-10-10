"""Explicit protected physical recipes. No hardware is opened by module import."""

import copy
import math
import threading
import time

from examples.alohamini.am1_finite_task import JOINT_KEYS

FEEDBACK_MAX_AGE_S = 1.0  # Existing AM1 Local original request/acquisition contract.
ZERO_BODY = {"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0, "lift_axis.vel": 0}


class StartupCancelledError(RuntimeError):
    """An explicit Stop cancels admission without inventing a hardware fault."""


class FeedbackUnavailable(RuntimeError):  # noqa: N818 - existing adapter/test contract
    """A bounded missing/stale reply; owner survives and holds/recoveries remain bounded."""


def read_physical_sample(client, run_id, host_incarnation, previous_sequence, *, clock=time.monotonic):
    observation = client.get_observation()
    now = clock()
    marker = client.latest_am1_local_feedback
    if not isinstance(marker, dict) or marker.get("version") != 1:
        raise ValueError("protected host feedback marker missing")
    if marker.get("run_id") != run_id or marker.get("host_incarnation") != host_incarnation:
        raise ValueError("foreign protected host feedback binding")
    sequence = marker.get("observation_id")
    if type(sequence) is not int or sequence <= previous_sequence:
        raise FeedbackUnavailable("native feedback acquisition did not advance")
    started, completed = marker.get("read_started_at_monotonic_s"), marker.get("acquired_at_monotonic_s")
    received, request_age = client.latest_observation_received_at, client.latest_observation_roundtrip_age_s
    if any(
        type(v) not in (int, float) or not math.isfinite(v)
        for v in (started, completed, received, request_age)
    ):
        raise ValueError("native feedback original acquisition/request time unavailable")
    if not 0 <= started <= completed <= received <= now or request_age < 0:
        raise ValueError("native feedback acquisition/request clock invalid")
    # Both complete raw vector acquisition and original matching request must remain fresh.
    at = min(started, received - request_age)
    if now - at >= FEEDBACK_MAX_AGE_S:
        raise FeedbackUnavailable("native feedback original acquisition/request expired")
    if client.latest_observation_error:
        raise ValueError("malformed native observation: " + client.latest_observation_error)
    if not set(JOINT_KEYS).issubset(client.latest_raw_observation_keys):
        raise ValueError("incomplete actual arm feedback")
    positions = {key: observation.get(key) for key in JOINT_KEYS}
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in positions.values()):
        raise ValueError("invalid actual arm feedback")
    return {
        "positions": positions,
        "sequence": sequence,
        "at": at,
        "ack": dict(marker),
        "observation": dict(observation),
        "provenance": "protected-host/measured-feedback",
    }


def body_recipe_action(client, elapsed):
    # Original W/A/U/J 200 ms down / 500 ms released sequence, admitted finite native input.
    keys = []
    for start, key in ((2.0, "w"), (2.7, "a"), (3.4, "u"), (4.1, "j")):
        if start <= elapsed < start + 0.2 - 1e-9:
            keys = [key]
            break
    return {**client._from_keyboard_to_base_action(keys), **client._from_keyboard_to_lift_action(keys)}


class PhysicalExecutor:
    source = "protected-physical-provider"
    asynchronous = True
    restart_cleanup = "physical_cleanup_unknown_after_restart"
    recipes = frozenset({"physical-arm-smoke", "physical-arm-smoke-repeat", "physical-arm-hold-body"})

    def __init__(self, config, observation, *, clock=time.monotonic, io_factory=None):
        from tools.am1_local_observation import LocalObservationMailbox

        if not isinstance(config, dict) or not config or not isinstance(observation, LocalObservationMailbox):
            raise ValueError(
                "physical backend requires explicit private config and actual local-camera adapter"
            )
        self.config = copy.deepcopy(config)
        self.observation = observation
        self.observation.require_owned_session = True
        self.clock = clock
        self.io_factory = io_factory
        self.mutex = threading.RLock()
        self.worker = None
        self.run_id = self.recipe = None
        self.host_incarnation = None
        self.revision = 0
        self.desired = "hold"
        self.finish_status = None
        self.sample = None
        self.task = None
        self._fault = None
        self._transient = None
        self._epoch = -1
        self._epoch_mode = None
        self._epoch_revision = -1
        self._action_sequence = 0
        self._aligned = []
        self._hold_reference = None
        self._dispatch_ack = False
        self._execution = {"progress_s": 0.0, "complete": False, "fault": None}
        self._lifecycle = {
            "run_id": None,
            "phase": "idle",
            "startup_deadline": None,
            "native_live_at": None,
            "cleanup": None,
            "uncertain": False,
            "terminal_status": None,
        }
        self._wake = threading.Event()

    def supports(self, recipe):
        return recipe.name in self.recipes and recipe.seed is None and recipe.mode == "finite"

    def sensing_metadata(self):
        return {
            "sensing_policy": "local-camera-required",
            "sensing_source": dict(self.observation.source),
            "observation_provenance": "real-local-camera/pi-decoded-arrival",
            "observation_freshness_basis": "pi-upstream-arrival-monotonic",
            "motor_commands_provenance": "protected-host/requested-and-accepted-separately",
            "feedback_provenance": "protected-host/measured-feedback",
            "ack_provenance": "protected-host/action-epoch-feedback-not-servo-ack",
            "backend_contract": copy.deepcopy(self.config),
            "gain_recipes": {
                "physical-arm-smoke": "selected-shoulder-i1-with-verified-i0-restore",
                "physical-arm-smoke-repeat": "selected-shoulder-i1-with-verified-i0-restore",
                "physical-arm-hold-body": "ordinary-body-baseline-no-integral-trial",
            },
        }

    def bind_run(self, run_id, recipe):
        with self.mutex:
            if (
                not self.supports(recipe)
                or (self.worker and self.worker.is_alive())
                or self._lifecycle["uncertain"]
            ):
                raise RuntimeError("previous physical owner is active or uncertain, or recipe unsupported")
            self.run_id, self.recipe = run_id, recipe
            self.host_incarnation = __import__("uuid").uuid4().__str__()
            self.revision = 0
            self.desired = "hold"
            self.finish_status = None
            self.sample = self.task = None
            self._fault = self._transient = None
            self._epoch = -1
            self._epoch_mode = None
            self._epoch_revision = -1
            self._action_sequence = 0
            self._aligned = []
            self._hold_reference = None
            self._dispatch_ack = False
            self._execution = {"progress_s": 0.0, "complete": False, "fault": None}
            self._lifecycle = {
                "run_id": run_id,
                "phase": "startup",
                "startup_deadline": self.clock() + 180,
                "native_live_at": None,
                "cleanup": None,
                "uncertain": False,
                "terminal_status": None,
            }
            self.observation.begin(run_id, recipe.live_s)

    def begin(self, recipe):
        with self.mutex:
            if self.recipe != recipe or self.worker and self.worker.is_alive():
                return False
            self.worker = threading.Thread(target=self._run, name="am1-protected-native", daemon=False)
            self.worker.start()
            return True  # Mailbox accepted only; authority does not mark physical effect admitted.

    def set_intent_revision(self, revision):
        with self.mutex:
            if type(revision) is not int or revision < self.revision:
                raise ValueError("physical intent revision must not regress")
            if revision != self.revision:
                self.revision = revision
                self.desired = "hold"
                self._dispatch_ack = False
                self._aligned = []
                self._hold_reference = None
        self._wake.set()

    def evidence(self):
        with self.mutex:
            now = self.clock()
            s = copy.deepcopy(self.sample)
            age = now - s["at"] if s else None
            fresh = s is not None and 0 <= age < FEEDBACK_MAX_AGE_S and self._transient is None
            ack = s["ack"] if s else {}
            matched = (
                fresh
                and ack.get("run_id") == self.run_id
                and ack.get("host_incarnation") == self.host_incarnation
            )
            revision = matched and ack.get("intent_revision") == self.revision
            hold = (
                revision
                and ack.get("state") == "paused"
                and ack.get("epoch") == self._epoch
                and self._epoch_mode == "pause"
            )
            active_ack = bool(
                revision
                and ack.get("state") == "active"
                and ack.get("epoch") == self._epoch
                and self._epoch_mode == "active"
            )
            aligned = bool(
                hold and len(self._aligned) >= 3 and self._aligned[-1] - self._aligned[0] >= 0.2 - 1e-9
            )
            o = self.observation.evidence()
            return {
                "source": self.source,
                "feedback": bool(fresh),
                "required_observation": o["qualified"],
                "optional_quality": False,
                "optional_observation": {"required": False, "state": "not-requested", "qualified": False},
                "pose_aligned": aligned or bool(self._dispatch_ack and active_ack),
                "hold_acknowledged": bool(hold),
                "native_ack": bool(hold or active_ack),
                "active_acknowledged": bool(active_ack),
                "fault": self._fault,
                "feedback_age_s": age,
                "observation_age_s": o["age_s"],
                "observation": o,
                "feedback_sequence": s["sequence"] if s else None,
                "measured_positions": s["positions"] if s else None,
                "accepted_host_action": ack,
                "acknowledged_intent_revision": ack.get("intent_revision"),
                "host_incarnation": self.host_incarnation,
                "lifecycle": copy.deepcopy(self._lifecycle),
                "transport_issue": self._transient,
                **self.sensing_metadata(),
            }

    def dispatch(self, recipe):
        with self.mutex:
            if recipe != self.recipe or self.finish_status or self._lifecycle["phase"] != "live":
                return False
            o = self.observation.evidence()
            if (
                not o["qualified"]
                or self.sample is None
                or self.clock() - self.sample["at"] >= FEEDBACK_MAX_AGE_S
            ):
                return False
            if self.desired != "active":
                if len(self._aligned) < 3 or self._aligned[-1] - self._aligned[0] < 0.2 - 1e-9:
                    return False
                self.desired = "active"
            self._wake.set()
            return self._dispatch_ack

    def hold(self):
        with self.mutex:
            if self._lifecycle["phase"] not in ("startup", "live"):
                return False  # Idle/restart/finished inspection never opens or commands hardware.
            if self.desired != "hold":
                self.desired = "hold"
                self._dispatch_ack = False
                self._aligned = []
                self._hold_reference = None
            self._wake.set()
            return self.evidence()["hold_acknowledged"]

    def advance(self, now, dt):
        with self.mutex:
            return copy.deepcopy(self._execution)

    def finish(self, status):
        with self.mutex:
            if self._lifecycle["phase"] == "idle":
                return {"cleanup": None, "uncertain": False}
            if self._lifecycle["phase"] == "terminal":
                return copy.deepcopy(self._lifecycle)
            if not self.finish_status or status in ("stopped", "faulted", "interrupted"):
                self.finish_status = status
            self.desired = "hold"
            self._dispatch_ack = False
            self._wake.set()
            return {"pending": True, "cleanup": None, "uncertain": False}

    def reconcile(self, run_id=None):
        return (
            False  # Administrative reconciliation requires fresh stopped bus evidence, never a Boolean clear.
        )

    def close(self):
        self.finish("interrupted")

    def _run(self):
        from examples.alohamini.am1_finite_task import FiniteTask
        from tools.am1_pi_executor import SimulatedIO

        io = None
        try:
            factory = self.io_factory or ProtectedHostRun
            io = factory(self.config)
            if isinstance(io, SimulatedIO) or getattr(io, "provenance", None) != "protected-host":
                raise TypeError("physical recipes require protected host IO, never simulated IO")

            def cancel():
                if self.finish_status:
                    raise StartupCancelledError("physical startup cancelled by Stop")
                if self.clock() >= self._lifecycle["startup_deadline"]:
                    raise RuntimeError("physical startup deadline expired")

            io.observation = self.observation
            io.start(self.run_id, self.host_incarnation, self.recipe, cancel)
            while True:
                with self.mutex:
                    if self.finish_status:
                        break
                    if self._lifecycle["phase"] == "startup":
                        cancel()
                    if (
                        self._lifecycle["native_live_at"] is not None
                        and self.clock() >= self._lifecycle["native_live_at"] + self.recipe.live_s
                    ):
                        self.finish_status = "stopped"
                        break
                try:
                    previous = self.sample["sequence"] if self.sample else -1
                    sample = io.poll(previous)
                    transient = None
                except (FeedbackUnavailable, OSError) as exc:
                    sample = None
                    transient = str(exc)
                with self.mutex:
                    if self.finish_status:
                        break
                    self._transient = transient
                    if sample is not None:
                        self.sample = sample
                    fresh = sample is not None
                    observed = self.observation.evidence()["qualified"]
                    if not fresh or not observed:
                        # This protection acts at native cadence even if web/database owner stalls.
                        self.desired = "hold"
                        self._dispatch_ack = False
                        self._aligned = []
                        self._hold_reference = None
                    latest_ack = self.sample["ack"] if self.sample else {}
                    if (
                        fresh
                        and self._epoch_mode == "active"
                        and latest_ack.get("state") == "paused"
                        and latest_ack.get("epoch") == self._epoch
                        and latest_ack.get("intent_revision") == self.revision
                    ):
                        # Native watchdog pause revokes the old active epoch. Requalify a new measured hold.
                        self.desired = "hold"
                        self._dispatch_ack = False
                        self._aligned = []
                        self._hold_reference = None
                    desired = "active" if self.desired == "active" and fresh and observed else "pause"
                    if self._epoch_mode != desired or self._epoch_revision != self.revision:
                        if desired == "pause":
                            self._epoch = (
                                1 if self._epoch < 0 else self._epoch + (2 if self._epoch % 2 else 1)
                            )
                            self._aligned = []
                            self._hold_reference = None
                        else:
                            self._epoch += 1
                        self._epoch_mode = desired
                        self._epoch_revision = self.revision
                    ack = self.sample["ack"] if self.sample else {}
                    matched = (
                        fresh
                        and ack.get("run_id") == self.run_id
                        and ack.get("host_incarnation") == self.host_incarnation
                        and ack.get("intent_revision") == self.revision
                        and ack.get("epoch") == self._epoch
                    )
                    if matched and desired == "pause" and ack.get("state") == "paused":
                        positions = self.sample["positions"]
                        if self._hold_reference is None:
                            self._hold_reference = dict(positions)
                        if all(abs(positions[k] - self._hold_reference[k]) <= 1 for k in JOINT_KEYS):
                            if not self._aligned or self.sample["at"] - self._aligned[-1] >= 0.1 - 1e-9:
                                self._aligned.append(self.sample["at"])
                                self._aligned = self._aligned[-3:]
                        else:
                            self._aligned = []
                            self._hold_reference = dict(positions)
                        if (
                            self._lifecycle["phase"] == "startup"
                            and observed
                            and len(self._aligned) >= 3
                            and self._aligned[-1] - self._aligned[0] >= 0.2 - 1e-9
                        ):
                            live = self.clock()
                            self._lifecycle.update(
                                phase="live", native_live_at=live, backend_incarnation=self.host_incarnation
                            )
                            self.task = FiniteTask(
                                self.recipe,
                                self.sample["positions"],
                                live,
                                self.config["shoulder_amplitude"],
                                start_held=True,
                                feedback_provenance="protected-host/measured-feedback",
                            )
                            self._execution = dict(self.task.snapshot(), fault=None)
                    if self.task:
                        if desired == "pause":
                            if self.task.active:
                                self.task.hold()
                        elif matched and ack.get("state") == "active":
                            self._dispatch_ack = True
                            if not self.task.active:
                                self.task.resume(self.clock())
                            if self.task.advance(self.clock(), self.sample):
                                self._execution = dict(self.task.snapshot(), fault=None)
                            if self.task.complete:
                                self.desired = "hold"
                        self.task.check(self.clock())
                    action = dict(ZERO_BODY)
                    if desired == "active" and self.task:
                        action.update({"arm_" + k: v for k, v in self.task.provider.get_action().items()})
                        if (
                            self.recipe.name == "physical-arm-hold-body"
                            and self._dispatch_ack
                            and not getattr(self.task.provider, "preparing", False)
                        ):
                            action.update(io.body_action(self.task.provider.elapsed_s))
                    self._action_sequence += 1
                    action["_am1_local_control"] = {
                        "version": 1,
                        "mode": desired,
                        "epoch": self._epoch,
                        "run_id": self.run_id,
                        "host_incarnation": self.host_incarnation,
                        "intent_revision": self.revision,
                        "action_sequence": self._action_sequence,
                    }
                    # Bound send is outside authority; the worker mutex fences concurrent Pause/Stop.
                    try:
                        io.send(action)
                    except (FeedbackUnavailable, OSError) as exc:
                        self._transient = str(exc)
                        self.desired = "hold"
                        self._dispatch_ack = False
                        self._aligned = []
                        self._hold_reference = None
                self._wake.wait(1 / 30)
                self._wake.clear()
        except StartupCancelledError:
            pass
        except Exception as exc:
            with self.mutex:
                self._fault = self._fault or f"{type(exc).__name__}: {exc}"
                self.finish_status = "faulted"
                self._execution = dict(self._execution, fault=self._fault)
        finally:
            with self.mutex:
                self._lifecycle["phase"] = "finishing"
                status = self.finish_status or "faulted"
                if self.task:
                    self.task.finish(status)
                    self._execution = dict(self.task.snapshot(), fault=self._fault)
            try:
                outcome = (
                    io.finish(status)
                    if io
                    else {
                        "cleanup": "physical_cleanup_unknown",
                        "uncertain": True,
                        "terminal_status": "faulted",
                    }
                )
            except Exception as exc:
                outcome = {
                    "cleanup": "physical_cleanup_unknown",
                    "uncertain": True,
                    "terminal_status": "faulted",
                    "error": str(exc),
                }
            with self.mutex:
                self.observation.stop()  # Required capture remains available throughout actual host finishing.
                self._lifecycle.update(outcome, phase="terminal", run_id=self.run_id)
                self._dispatch_ack = False


BODY_MOTORS = ("base_left_wheel", "base_back_wheel", "base_right_wheel", "lift_axis")


def qualified_cleanup_receipt(receipt, run_id, incarnation):
    if (
        not isinstance(receipt, dict)
        or receipt.get("version") != 1
        or receipt.get("run_id") != run_id
        or receipt.get("host_incarnation") != incarnation
    ):
        return False
    readback = receipt.get("readback")
    if (
        receipt.get("cleanup") != "physical_readback_verified"
        or receipt.get("uncertain") is not False
        or receipt.get("cleanup_errors")
        or not isinstance(readback, dict)
    ):
        return False
    if (
        not readback.get("verified")
        or readback.get("lift_readback") != "qualified"
        or readback.get("selected_gain_restore_required")
    ):
        return False
    expected = {
        ("left" if "_left_" in k else "right", k.removesuffix(".pos"), "Torque_Enable") for k in JOINT_KEYS
    }
    expected.update(("left", k, "Torque_Enable") for k in BODY_MOTORS)
    expected.update(("left", k, "Goal_Velocity") for k in BODY_MOTORS)
    rows = readback.get("readbacks")
    if not isinstance(rows, list) or len(rows) != len(expected):
        return False
    return {(r.get("bus"), r.get("motor"), r.get("register")) for r in rows} == expected and all(
        type(r.get("value")) is int and r["value"] == 0 and not r.get("error") for r in rows
    )


class ProtectedHostRun:
    """One bounded sole local client; the existing child host alone opens servo buses."""

    provenance = "protected-host"

    def __init__(self, config):
        from pathlib import Path

        self.config = config
        self.host = self.camera = self.client = None
        self.observation = None
        self.host_log = self.camera_log = None
        self.run_id = self.incarnation = None
        self.run_directory = None
        self.receipt = None
        required = {
            "version",
            "machine",
            "host_repository",
            "host_head",
            "host_python",
            "camera_repository",
            "camera_head",
            "camera_python",
            "camera_config",
            "camera_credentials",
            "camera_binary",
            "camera_state",
            "physical_state_directory",
            "run_root",
            "shoulder_amplitude",
            "calibration_files",
            "source_pins",
        }
        if set(config) != required or config["version"] != 1:
            raise ValueError("exact protected backend configuration required")
        if not 0 < float(config["shoulder_amplitude"]) <= 3 or not config["calibration_files"]:
            raise ValueError("actual calibration and mapped amplitude required")
        for k in ("host_head", "camera_head"):
            if not __import__("re").fullmatch("[0-9a-f]{40}", config[k]):
                raise ValueError("exact component source commit required")
        for k in required - {
            "version",
            "machine",
            "host_head",
            "camera_head",
            "shoulder_amplitude",
            "calibration_files",
            "source_pins",
        }:
            if not Path(config[k]).is_absolute():
                raise ValueError("absolute protected backend paths required")
        if Path(config["physical_state_directory"]).resolve() != Path.home() / ".local/state/am1-session":
            raise ValueError("canonical physical admission namespace required")

    def _validate_source(self):
        import hashlib
        import socket
        import subprocess
        from pathlib import Path

        if socket.gethostname() != self.config["machine"]:
            raise RuntimeError("AM1 machine identity mismatch")
        for name in ("host", "camera"):
            repository = self.config[name + "_repository"]
            head = subprocess.check_output(
                ["git", "-C", repository, "rev-parse", "HEAD"], text=True, timeout=5
            ).strip()
            dirty = subprocess.check_output(
                ["git", "-C", repository, "status", "--porcelain"], text=True, timeout=5
            ).strip()
            if dirty or head != self.config[name + "_head"]:
                raise RuntimeError(name + " source pin/cleanliness mismatch")
        for path, digest in self.config["calibration_files"].items():
            if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest:
                raise RuntimeError("preserved calibration content changed")

    def start(self, run_id, incarnation, recipe, cancel):
        import os
        from pathlib import Path

        from lerobot.robots.alohamini.alohamini_client import AlohaMiniClient
        from lerobot.robots.alohamini.config_alohamini import AlohaMiniClientConfig

        self.run_id, self.incarnation = run_id, incarnation
        self._validate_source()
        cancel()
        state = Path(self.config["physical_state_directory"])
        if (state / "cleanup-uncertain.json").exists() or (state / "physical-in-progress.json").exists():
            raise RuntimeError("previous physical cleanup requires actual stopped reconciliation")
        # The isolated hardware host unit acquires canonical + serial admission before robot construction.
        # Parent/client stays in its existing PrivateDevices=yes namespace.
        cancel()
        root = Path(self.config["run_root"])
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.run_directory = root / run_id[:8]
        self.run_directory.mkdir(mode=0o700)
        self.receipt = self.run_directory / "cleanup.json"
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["AM1_CAMERA_PYTHON"] = self.config["camera_python"]
        env["AM1_CAMERA_LOG_DIRECTORY"] = str(self.run_directory)
        self.camera_log = (self.run_directory / "camera-launch.log").open("xb")
        camera_command = [
            "bash",
            self.config["camera_repository"] + "/tools/run_am1_camera.sh",
            "--config",
            self.config["camera_config"],
            "--credentials",
            self.config["camera_credentials"],
            "--binary",
            self.config["camera_binary"],
            "--state-dir",
            self.config["camera_state"],
            "--owned-session",
            run_id,
            "--source-budget-seconds",
            str(180 + recipe.live_s + 60 + 30),
        ]
        camera_environment = {
            k: env[k] for k in ("PYTHONDONTWRITEBYTECODE", "AM1_CAMERA_PYTHON", "AM1_CAMERA_LOG_DIRECTORY")
        }
        self.camera = OwnedUnit(
            "am1-pa01-camera-" + run_id,
            self.config["camera_repository"],
            camera_command,
            camera_environment,
            180 + recipe.live_s + 60 + 50,
            20,
            signal_name="SIGTERM",
            log=self.camera_log,
        )
        wait_required_camera(self.camera, self.observation, cancel)
        cancel()
        env["AM1_MOTOR_PYTHON"] = self.config["host_python"]
        env["AM1_LOG_DIRECTORY"] = str(self.run_directory)
        env["AM1_SYNC_SHOULDER_READBACK"] = "1"
        env["AM1_LEFT_SHOULDER_EVIDENCE"] = "1"
        # Never carry the arm-only selected integral experiment into the body recipe.
        for k in ("AM1_SHOULDER_INTEGRAL_TEST", "AM1_SCRIPTED_PREPARE"):
            env.pop(k, None)
        if recipe.name in ("physical-arm-smoke", "physical-arm-smoke-repeat"):
            env["AM1_SHOULDER_INTEGRAL_TEST"] = "1"
            env["AM1_SCRIPTED_PREPARE"] = "1"
        self.host_log = (self.run_directory / "host-launch.log").open("xb")
        host_command = [
            self.config["host_python"],
            "-B",
            "-m",
            "lerobot.robots.alohamini.alohamini_host",
            "--robot_model",
            "alohamini1",
            "--no_cameras",
            "--max_relative_target",
            "20.0",
            "--max_loop_freq_hz",
            "30",
            "--profile_timing",
            "true",
            "--profile_cadence",
            "--am1-protected-run-directory",
            str(self.run_directory),
            "--am1-run-id",
            run_id,
            "--am1-host-incarnation",
            incarnation,
            "--am1-cleanup-receipt",
            str(self.receipt),
            "--am1-physical-state-directory",
            str(state),
        ]
        host_environment = {
            k: env[k]
            for k in (
                "AM1_LOG_DIRECTORY",
                "AM1_SYNC_SHOULDER_READBACK",
                "AM1_LEFT_SHOULDER_EVIDENCE",
                "AM1_SHOULDER_INTEGRAL_TEST",
                "AM1_SCRIPTED_PREPARE",
            )
            if k in env
        }
        host_environment.update(
            PYTHONPATH=self.config["host_repository"] + "/src",
            PYTHONDONTWRITEBYTECODE="1",
            PYTHONUNBUFFERED="1",
        )
        self.host = OwnedUnit(
            "am1-pa01-host-" + run_id,
            self.config["host_repository"],
            host_command,
            host_environment,
            720,
            80,
            signal_name="SIGINT",
            log=self.host_log,
        )
        while True:
            cancel()
            if self.host.poll() is not None:
                raise RuntimeError(
                    "protected host exited during startup; inspect original host log and cleanup receipt"
                )
            if self.camera.poll() is not None:
                raise RuntimeError("owned required camera exited during startup; inspect original camera log")
            # Existing post-home operational_ready precedes client handshake. Socket bind alone is not ready.
            logpath = self.run_directory / "host-runtime.log"
            if logpath.exists():
                with logpath.open("rb") as stream:
                    stream.seek(max(0, logpath.stat().st_size - 64000))
                    ready = b'"operational_ready"' in stream.read()
                if ready and (self.run_directory / "observation.sock").exists():
                    break
            time.sleep(0.05)
        cancel()
        self.client = AlohaMiniClient(
            AlohaMiniClientConfig(
                remote_ip="127.0.0.1",
                robot_model="alohamini1",
                cameras={},
                polling_timeout_ms=40,
                connect_timeout_s=5,
                command_send_timeout_ms=40,
                observation_request_window=3,
                command_endpoint="ipc://" + str(self.run_directory / "command.sock"),
                observation_endpoint="ipc://" + str(self.run_directory / "observation.sock"),
            )
        )
        self.client.connect(cancel_check=cancel)

    def poll(self, previous):
        if self.host.poll() is not None:
            raise RuntimeError("protected host exited; original first cause retained in host log/receipt")
        if self.camera.poll() is not None:
            raise RuntimeError("required owned camera exited before physical finishing")
        return read_physical_sample(self.client, self.run_id, self.incarnation, previous)

    def send(self, action):
        import zmq

        try:
            self.client.send_action(action)
        except zmq.ZMQError as exc:
            raise FeedbackUnavailable("bounded local command transport: " + str(exc)) from exc

    def body_action(self, elapsed):
        return body_recipe_action(self.client, elapsed)

    def finish(self, status):
        import json
        from pathlib import Path

        errors = []
        finish_deadline = time.monotonic() + 55
        receipt = None
        # One ordinary signal to the owned host group; its normal disconnect/readback remains authoritative.
        if self.host is not None and not self.host.actual_exited():
            try:
                self.host.stop()
                self.host.wait_actual_exit(timeout=max(0.1, finish_deadline - time.monotonic() - 20))
            except TimeoutError:
                errors.append("physical host cleanup deadline expired; owner retained, no forced rearm")
            except (__import__("subprocess").SubprocessError, OSError, RuntimeError) as exc:
                errors.append("physical host cooperative cleanup unconfirmed: " + str(exc))
        if self.receipt is not None and self.receipt.exists():
            try:
                receipt = json.loads(self.receipt.read_bytes())
            except (ValueError, OSError) as exc:
                errors.append("physical receipt unavailable: " + str(exc))
        if self.client is not None and self.client.is_connected:
            try:
                self.client.disconnect()
            except Exception as exc:
                errors.append("local client close: " + str(exc))
        motors = qualified_cleanup_receipt(receipt, self.run_id, self.incarnation)
        no_device_effects = self.host is None
        # Required camera remains through host finishing. Release main process once; it cleans its child.
        camera_receipt = None
        host_exited = self.host is None or self.host.actual_exited()
        if self.camera is not None and not self.camera.actual_exited() and host_exited:
            try:
                self.camera.stop()
                self.camera.wait_actual_exit(timeout=max(0.1, finish_deadline - time.monotonic()))
            except (TimeoutError, __import__("subprocess").SubprocessError, OSError, RuntimeError) as exc:
                errors.append("owned camera cooperative release unconfirmed: " + str(exc))
        if self.run_id:
            path = Path(self.config["camera_state"]) / ("release-" + self.run_id + ".json")
            if path.exists():
                try:
                    camera_receipt = json.loads(path.read_bytes())
                except (ValueError, OSError) as exc:
                    errors.append("camera receipt unavailable: " + str(exc))
        camera_clean = self.camera is None or (
            isinstance(camera_receipt, dict)
            and camera_receipt.get("state") == "released"
            and camera_receipt.get("session_id") == self.run_id
            and not camera_receipt.get("errors")
            and all(
                (camera_receipt.get("release_evidence") or {}).get(k)
                for k in (
                    "backend_exited",
                    "readers_joined",
                    "viewer_socket_closed",
                    "runtime_config_removed",
                )
            )
        )
        clean = (motors or no_device_effects) and camera_clean and not errors
        # Release canonical only when host has actually exited; unknown cleanup remains persistently latched.
        for stream in (self.host_log, self.camera_log):
            if stream:
                stream.close()
        first = receipt.get("first_cause") if isinstance(receipt, dict) else None
        return {
            "cleanup": "physical_readback_verified"
            if clean and motors
            else ("no_physical_device_effects" if clean else "physical_cleanup_unknown"),
            "uncertain": not clean,
            "terminal_status": "faulted" if first else status,
            "first_cause": first,
            "readback": receipt,
            "camera_release": camera_receipt,
            "cleanup_errors": errors,
            "backend_incarnation": self.incarnation,
            "run_directory": str(self.run_directory),
        }


def protected_unit_command(
    name, cwd, command, *, environment, runtime_s, stop_s, devices, signal_name="SIGINT", log_path=None
):
    if not 0 < runtime_s <= 920 or not 0 < stop_s <= 80 or signal_name not in ("SIGINT", "SIGTERM"):
        raise ValueError("invalid bounded owned unit limits")
    args = [
        "systemd-run",
        "--user",
        "--unit=" + name,
        "--wait",
        "--collect",
        "-p",
        "WorkingDirectory=" + cwd,
        "-p",
        "NoNewPrivileges=yes",
        "-p",
        "PrivateDevices=" + ("no" if devices else "yes"),
        "-p",
        "KillMode=mixed",
        "-p",
        "KillSignal=" + signal_name,
        "-p",
        "Restart=no",
        "-p",
        "UMask=0077",
        "-p",
        "RuntimeMaxSec=" + str(runtime_s) + "s",
        "-p",
        "TimeoutStopSec=" + str(stop_s) + "s",
        "-p",
        "RestrictAddressFamilies=AF_UNIX AF_INET",
    ]
    if log_path:
        args.extend(["-p", "StandardOutput=append:" + log_path, "-p", "StandardError=inherit"])
    for k, v in environment.items():
        args.extend(["-p", "Environment=" + k + "=" + v])
    return args + command


class OwnedUnit:
    """Administrative monitor of an exact finite user unit; monitor exit is never cleanup proof."""

    def __init__(self, name, cwd, command, environment, runtime_s, stop_s, *, signal_name, log):
        import subprocess
        from pathlib import Path

        self.name = name
        runtime_name = "host-runtime.log" if "-host-" in name else "camera-runtime.log"
        log_path = str(Path(log.name).parent / runtime_name)
        args = protected_unit_command(
            name,
            cwd,
            command,
            environment=environment,
            runtime_s=runtime_s,
            stop_s=stop_s,
            devices=True,
            signal_name=signal_name,
            log_path=log_path,
        )
        self.monitor = subprocess.Popen(
            args, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
        )
        self.stopping = False

    def poll(self):
        return self.monitor.poll()

    def wait(self, timeout):
        return self.monitor.wait(timeout=timeout)

    def _unit_state(self, timeout=3):
        import subprocess

        reply = subprocess.run(
            [
                "systemctl",
                "--user",
                "show",
                self.name,
                "--no-pager",
                "--property=Id,LoadState,ActiveState,MainPID,ControlPID,Job",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        values = dict(line.split("=", 1) for line in reply.stdout.splitlines() if "=" in line)
        if reply.returncode not in (0, 4) or values.get("Id") != self.name + ".service":
            raise RuntimeError("exact owned unit state unavailable")
        return values

    def actual_exited(self, timeout=3):
        import subprocess

        # A pending systemd-run can still register/start the unit. Fence that future dispatch,
        # then require fresh unit state including absence of an outstanding start/stop job.
        if self.monitor.poll() is None:
            return False
        try:
            values = self._unit_state(timeout)
            return (
                values.get("LoadState") in ("loaded", "not-found")
                and values.get("ActiveState") in ("inactive", "failed")
                and values.get("MainPID") == "0"
                and values.get("ControlPID") == "0"
                and values.get("Job") in ("", "0")
            )
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            return False

    def wait_actual_exit(self, timeout):
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("owned unit actual exit unconfirmed")
            if self.actual_exited(timeout=min(3, remaining)):
                break
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))
        return self.monitor.poll()

    def stop(self):
        import subprocess

        if self.stopping:
            return
        # Do not send Stop to a not-yet-registered name and then let its pending launcher
        # create a new start job. Wait boundedly for this exact launch to register or end.
        registration_deadline = time.monotonic() + 5
        while True:
            remaining = registration_deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("owned unit launch registration unconfirmed; owner retained")
            values = self._unit_state(timeout=min(1, remaining))
            if values.get("LoadState") == "loaded":
                break
            if values.get("LoadState") != "not-found":
                raise RuntimeError("owned unit launch state unconfirmed; owner retained")
            if self.monitor.poll() is not None and self.actual_exited(timeout=min(1, remaining)):
                return
            time.sleep(min(0.05, max(0, registration_deadline - time.monotonic())))
        self.stopping = True
        # One nonblocking ordinary request cancels a queued start or stops the actual unit.
        result = subprocess.run(
            ["systemctl", "--user", "--no-block", "stop", self.name], capture_output=True, timeout=5
        )
        if result.returncode:
            raise RuntimeError("owned unit cooperative stop refused")


def wait_required_camera(camera, observation, cancel, *, sleep=time.sleep):
    if observation is None:
        raise ValueError("actual required local-camera mailbox unavailable")
    while True:
        cancel()
        if camera.poll() is not None:
            raise RuntimeError("required owned camera exited before physical host launch")
        if observation.evidence()["qualified"]:
            return
        sleep(0.05)
