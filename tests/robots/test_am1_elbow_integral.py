from __future__ import annotations

import importlib
import json
from types import SimpleNamespace

import pytest

from lerobot.motors import Motor, MotorCalibration, MotorNormMode
from lerobot.robots.alohamini.alohamini import AlohaMini

ELBOW = "arm_left_elbow_flex"
OTHER = "arm_left_shoulder_lift"
FLAGS = (
    "AM1_ELBOW_RECOVERY",
    "AM1_SCRIPTED_PREPARE",
    "AM1_SELECTED_JOINT_EVIDENCE",
    "AM1_SHOULDER_INTEGRAL_TEST",
)
BASELINE = {
    "Model_Number": 777,
    "Firmware_Major_Version": 3,
    "Firmware_Minor_Version": 10,
    "Operating_Mode": 0,
    "Torque_Enable": 0,
    "Lock": 0,
    "P_Coefficient": 16,
    "I_Coefficient": 0,
    "D_Coefficient": 32,
    "Min_Position_Limit": 864,
    "Max_Position_Limit": 3073,
    "Homing_Offset": 1845,
    "Present_Position": 2453,
    "Goal_Position": 2472,
}


class OwnerBus:
    def __init__(self):
        self.motors = {
            ELBOW: Motor(3, "sts3215", MotorNormMode.RANGE_M100_100),
            OTHER: Motor(2, "sts3215", MotorNormMode.RANGE_M100_100),
            "lift_axis": Motor(11, "sts3215", MotorNormMode.RANGE_M100_100),
        }
        self.calibration = {n: MotorCalibration(m.id, 0, 1845, 864, 3073) for n, m in self.motors.items()}
        self.is_connected = True
        self.values = {(r, n): v for n in self.motors for r, v in BASELINE.items()}
        self.raw = {24: 16, 25: 0}
        self.events = []
        self.fail_writes = {}
        self.read_sequences = {}
        self.before_write = None

    def read(self, register, motor, *, normalize, num_retry):
        self.events.append(("read", register, motor, normalize, num_retry))
        seq = self.read_sequences.get((register, motor))
        if seq:
            value = seq.pop(0)
            if isinstance(value, BaseException):
                raise value
            return value
        return self.values.get((register, motor), 0)

    def _read(self, address, length, motor_id, *, num_retry, raise_on_error=True, err_msg=""):
        self.events.append(("raw-read", address, length, motor_id, num_retry, raise_on_error))
        assert (address, length, motor_id, num_retry, raise_on_error) in (
            (24, 1, 3, 0, True),
            (25, 1, 3, 0, True),
        )
        return self.raw[address], 0, 0

    def write(self, register, motor, value, *, normalize, num_retry):
        if self.before_write:
            self.before_write(register, motor, value)
        self.events.append(("write", register, motor, value, normalize, num_retry))
        if motor == ELBOW and register == "I_Coefficient":
            assert self.values[("Torque_Enable", motor)] == 0
            assert self.values[("Lock", motor)] == 1, "I may be written only with Lock1"
        failure = self.fail_writes.pop((register, motor, value), None)
        if failure:
            error, applied = failure
            if applied:
                self.values[(register, motor)] = value
            raise error
        self.values[(register, motor)] = value

    def sync_read(self, register, names):
        self.events.append(("sync-read", register, tuple(names)))
        return {n: self.values.get((register, n), 0) for n in names}

    def disconnect(self, *, disable_torque):
        assert disable_torque is False
        self.events.append(("disconnect",))
        self.is_connected = False


def trial_class():
    return importlib.import_module("lerobot.robots.alohamini.am1_elbow_integral").AM1ElbowIntegralTrial


def robot():
    result = AlohaMini.__new__(AlohaMini)
    result.config = SimpleNamespace(robot_model="alohamini1", no_follower=False)
    result.left_bus, result.right_bus = OwnerBus(), OwnerBus()
    result.left_arm_motors, result.right_arm_motors = [OTHER, ELBOW], []
    result.base_motors, result.cameras = [], {}
    result.lift = SimpleNamespace(
        cfg=SimpleNamespace(name="lift_axis"), configure=lambda **k: None, mark_unhomed=lambda: None
    )
    result._am1_protected_cleanup = True
    result._am1_selected_joint_evidence_enabled = True
    result._am1_elbow_recovery_binding = {
        "run_id": "33333333-3333-4333-8333-333333333333",
        "host_incarnation": "44444444-4444-4444-8444-444444444444",
        "recipe": "physical-left-elbow-recovery-i1",
        "integral": 1,
    }
    return result


def writes(bus, motor=ELBOW):
    return [event for event in bus.events if event[0] == "write" and event[2] == motor]


@pytest.fixture(autouse=True)
def scope(monkeypatch, tmp_path):
    for flag in FLAGS:
        monkeypatch.delenv(flag, raising=False)
    monkeypatch.setenv("AM1_LOG_DIRECTORY", str(tmp_path))
    contract = importlib.import_module("examples.alohamini.am1_session_contract")
    definitions = {
        "physical-left-elbow-recovery-" + mode.lower() + suffix: SimpleNamespace(integral=i)
        for i, mode in ((0, "I0"), (1, "I1"))
        for suffix in ("", "-dwell", "-confirm")
    }
    monkeypatch.setattr(contract, "ELBOW_RECOVERY_DEFINITIONS", definitions, raising=False)


def enable(monkeypatch, r, integral=1):
    monkeypatch.setenv("AM1_ELBOW_RECOVERY", "I" + str(integral))
    monkeypatch.setenv("AM1_SCRIPTED_PREPARE", "1")
    monkeypatch.setenv("AM1_SELECTED_JOINT_EVIDENCE", "1")
    r._am1_elbow_recovery_binding.update(
        integral=integral, recipe="physical-left-elbow-recovery-i" + str(integral)
    )


def prepared(monkeypatch, r, integral=1):
    enable(monkeypatch, r, integral)
    t = trial_class()(integral=integral)
    r._am1_elbow_integral_trial = t
    t.prepare(r)
    return t


def test_i1_transaction_durable_before_write_and_restore_lock_last(monkeypatch):
    r = robot()
    t = prepared(monkeypatch, r)
    before = r.left_bus.values.copy()

    def durable(register, motor, value):
        if motor == ELBOW:
            state = json.loads(t.snapshot_path.read_bytes())
            assert state["restore_required"] is True
            assert state["original_registers"]["I_Coefficient"] == 0
            assert state["original_bytes"] == {"24": 16, "25": 0}

    r.left_bus.before_write = durable
    t.disable_torque(r)
    t.apply(r)
    assert r.left_bus.values[("I_Coefficient", ELBOW)] == 1
    r.left_bus.values[("Torque_Enable", ELBOW)] = 1
    t.verify_active(r)
    t.restore(r)
    assert t.restore_required is False
    assert r.left_bus.values[("I_Coefficient", ELBOW)] == 0
    assert r.left_bus.values[("Lock", ELBOW)] == 0
    assert [(e[1], e[3]) for e in writes(r.left_bus)][-2:] == [("I_Coefficient", 0), ("Lock", 0)]
    assert {k: v for k, v in r.left_bus.values.items() if k[1] != ELBOW} == {
        k: v for k, v in before.items() if k[1] != ELBOW
    }
    assert all(e[-1] == 0 for e in writes(r.left_bus))
    state = json.loads(t.snapshot_path.read_bytes())
    assert state["status"] == "restored" and state["restore_required"] is False


def test_i0_control_snapshots_and_restores_without_i1_write(monkeypatch):
    r = robot()
    t = prepared(monkeypatch, r, 0)
    t.disable_torque(r)
    t.apply(r)
    t.restore(r)
    assert not any(e[1] == "I_Coefficient" and e[3] == 1 for e in writes(r.left_bus))
    assert t.evidence["integral"] == 0 and t.restore_required is False


@pytest.mark.parametrize(
    "register,value",
    [
        ("Model_Number", 2569),
        ("Firmware_Major_Version", 2),
        ("Firmware_Minor_Version", 9),
        ("Operating_Mode", 1),
        ("Torque_Enable", 1),
        ("P_Coefficient", 20),
        ("I_Coefficient", 1),
        ("D_Coefficient", 31),
        ("Lock", 2),
    ],
)
def test_actual_baseline_drift_refuses_before_any_write(monkeypatch, register, value):
    r = robot()
    enable(monkeypatch, r)
    r.left_bus.values[(register, ELBOW)] = value
    with pytest.raises(RuntimeError):
        trial_class()(integral=1).prepare(r)
    assert writes(r.left_bus) == []


@pytest.mark.parametrize(
    "fault", ["id", "calibration", "binding", "recipe", "flag", "am2", "protected", "shoulder"]
)
def test_owner_and_explicit_admission_fail_closed(monkeypatch, fault):
    r = robot()
    enable(monkeypatch, r)
    if fault == "id":
        r.left_bus.motors[ELBOW].id = 4
    elif fault == "calibration":
        r.left_bus.calibration[ELBOW].id = 4
    elif fault == "binding":
        r._am1_elbow_recovery_binding["run_id"] = "foreign"
    elif fault == "recipe":
        r._am1_elbow_recovery_binding["recipe"] = "physical-left-elbow-original-return"
    elif fault == "flag":
        monkeypatch.delenv("AM1_SCRIPTED_PREPARE")
    elif fault == "am2":
        r.config.robot_model = "alohamini2"
    elif fault == "protected":
        r._am1_protected_cleanup = False
    else:
        monkeypatch.setenv("AM1_SHOULDER_INTEGRAL_TEST", "1")
    with pytest.raises(RuntimeError):
        trial_class()(integral=1).prepare(r)
    assert writes(r.left_bus) == []


def test_uncertain_i1_ack_retains_original_failure_and_restore_obligation(monkeypatch):
    r = robot()
    t = prepared(monkeypatch, r)
    t.disable_torque(r)
    primary = ConnectionError("I1 applied but ACK lost")
    r.left_bus.fail_writes[("I_Coefficient", ELBOW, 1)] = (primary, True)
    with pytest.raises(ConnectionError) as caught:
        t.apply(r)
    assert caught.value is primary and t.restore_required is True
    t.restore(r)
    assert t.restore_required is False and r.left_bus.values[("Lock", ELBOW)] == 0
    assert t.evidence["first_failure"]["message"] == str(primary)


def test_uncertain_restore_ack_reconciles_actual_i0_before_lock0(monkeypatch):
    r = robot()
    t = prepared(monkeypatch, r)
    t.disable_torque(r)
    t.apply(r)
    r.left_bus.fail_writes[("I_Coefficient", ELBOW, 0)] = (ConnectionError("restore ACK lost"), True)
    t.restore(r)
    assert t.restore_required is False
    assert t.evidence["first_failure"]["message"] == "restore ACK lost"
    assert any(e.get("reconciled") for e in t.evidence["events"])


def test_failed_restore_keeps_obligation_and_never_unlocks_trial_i1(monkeypatch):
    r = robot()
    t = prepared(monkeypatch, r)
    t.disable_torque(r)
    t.apply(r)
    r.left_bus.fail_writes[("I_Coefficient", ELBOW, 0)] = (ConnectionError("I0 not applied"), False)
    with pytest.raises(ConnectionError):
        t.restore(r)
    assert t.restore_required is True and r.left_bus.values[("Lock", ELBOW)] == 1
    assert not any(e[1] == "Lock" and e[3] == 0 for e in writes(r.left_bus))
    t.restore(r)
    assert t.restore_required is False


def test_foreign_owner_or_changed_bytes_cannot_restore(monkeypatch):
    r = robot()
    t = prepared(monkeypatch, r)
    t.disable_torque(r)
    t.apply(r)
    original = r.left_bus
    r.left_bus = OwnerBus()
    with pytest.raises(RuntimeError):
        t.restore(r)
    assert writes(r.left_bus) == [] and t.restore_required is True
    r.left_bus = original
    r.left_bus.raw[25] = 1
    with pytest.raises(RuntimeError):
        t.restore(r)
    assert t.restore_required is True and r.left_bus.values[("Lock", ELBOW)] == 1


def test_ordinary_setup_does_not_clobber_or_persist_selected_trial(monkeypatch):
    r = robot()
    enable(monkeypatch, r)
    r.configure()
    t = r._am1_elbow_integral_trial
    assert r.left_bus.values[("I_Coefficient", ELBOW)] == 1
    selected = writes(r.left_bus)
    assert not any(
        e[1]
        in (
            "P_Coefficient",
            "D_Coefficient",
            "Operating_Mode",
            "Maximum_Acceleration",
            "Acceleration",
            "Return_Delay_Time",
        )
        for e in selected
    )
    assert not any(e[1] == "Lock" and e[3] == 0 for e in selected)
    assert any(
        e[1] == "I_Coefficient" and e[2] == OTHER and e[3] == 0 for e in r.left_bus.events if e[0] == "write"
    )
    r.activate_motors(home_lift=False)
    assert r.left_bus.values[("Goal_Position", ELBOW)] == 2453
    assert t.evidence["active_verified"] is True
    assert r._safe_shutdown(close_buses=True) == []
    assert t.restore_required is False and r._am1_shutdown_evidence["selected_gain_restore_required"] is False
    assert r._am1_shutdown_evidence["elbow_integral"]["status"] == "restored"


def test_setup_clobber_and_cleanup_restore_failure_remain_unknown(monkeypatch):
    r = robot()
    enable(monkeypatch, r)
    r.configure()
    t = r._am1_elbow_integral_trial
    r.left_bus.values[("I_Coefficient", ELBOW)] = 0
    with pytest.raises(RuntimeError):
        t.verify_active(r)
    r.left_bus.values[("I_Coefficient", ELBOW)] = 1
    r.left_bus.fail_writes[("I_Coefficient", ELBOW, 0)] = (ConnectionError("restoration refused"), False)
    issues = r._safe_shutdown(close_buses=True)
    assert any("elbow" in e and "restor" in e for e in issues)
    assert r._am1_shutdown_evidence["selected_gain_restore_required"] is True
    assert r._am1_shutdown_evidence["verified"] is False
    assert not any(e[1] == "Lock" and e[3] == 0 for e in writes(r.left_bus))


def test_configure_cancellation_restores_same_owner_before_bus_close(monkeypatch):
    r = robot()
    enable(monkeypatch, r)
    primary = KeyboardInterrupt()
    r.left_bus.fail_writes[("I_Coefficient", ELBOW, 1)] = (primary, True)
    with pytest.raises(KeyboardInterrupt):
        r.configure()
    t = r._am1_elbow_integral_trial
    assert t.restore_required is True
    assert r._safe_shutdown(close_buses=True) == []
    assert t.restore_required is False
    assert writes(r.left_bus)[-1][1:4] == ("Lock", ELBOW, 0)


def test_durable_snapshot_failure_prevents_first_write(monkeypatch):
    r = robot()
    enable(monkeypatch, r)
    t = trial_class()(integral=1)

    def refusal(*a, **k):
        raise OSError("private snapshot unavailable")

    monkeypatch.setattr(t, "_persist_snapshot", refusal)
    with pytest.raises(OSError):
        t.prepare(r)
    assert writes(r.left_bus) == []


def test_trial_snapshot_cannot_overwrite_foreign_state(monkeypatch, tmp_path):
    r = robot()
    enable(monkeypatch, r)
    path = tmp_path / "trial.json"
    path.write_text("existing-original")
    with pytest.raises((RuntimeError, FileExistsError)):
        trial_class()(integral=1, snapshot_path=path).prepare(r)
    assert path.read_text() == "existing-original" and writes(r.left_bus) == []


def test_unqualified_physical_identity_cleanup_never_writes_selected_registers(monkeypatch):
    r = robot()
    enable(monkeypatch, r)
    r.left_bus.values[("Model_Number", ELBOW)] = 2569
    with pytest.raises(RuntimeError):
        r.configure()
    assert r._safe_shutdown(close_buses=True)
    assert writes(r.left_bus) == []
    assert r._am1_shutdown_evidence["verified"] is False


def test_foreign_run_binding_refuses_restoration_before_write(monkeypatch):
    r = robot()
    t = prepared(monkeypatch, r)
    t.disable_torque(r)
    t.apply(r)
    before = list(writes(r.left_bus))
    r._am1_elbow_recovery_binding["run_id"] = "55555555-5555-4555-8555-555555555555"
    with pytest.raises(RuntimeError):
        t.restore(r)
    assert writes(r.left_bus) == before and t.restore_required is True


def test_control_apply_is_single_use_even_after_active_state(monkeypatch):
    r = robot()
    t = prepared(monkeypatch, r, 0)
    t.disable_torque(r)
    t.apply(r)
    r.left_bus.values[("Torque_Enable", ELBOW)] = 1
    t.verify_active(r)
    t.disable_torque(r)
    with pytest.raises(RuntimeError):
        t.apply(r)


@pytest.mark.parametrize("raw", [float("nan"), float("inf"), float("-inf"), True])
def test_invalid_current_never_qualifies_trial(raw):
    with pytest.raises(RuntimeError):
        trial_class().check_current(raw)


@pytest.mark.parametrize(
    "raw,limit,refused", [(16, 1800, True), (-16, 1800, True), (15, 1800, False), (8, 50, True)]
)
def test_existing_current_acquisition_enforces_trial_cap_without_marker_or_extra_poll(
    monkeypatch, raw, limit, refused
):
    r = robot()
    t = prepared(monkeypatch, r)
    t.disable_torque(r)
    t.apply(r)
    r._am1_selected_joint_evidence_context = None
    r._overcurrent_count = {}
    r._overcurrent_trip_n = 3
    r._last_currents_log_t = 0
    r.left_bus.values[("Present_Current", ELBOW)] = raw
    assert r._selected_joint_evidence_active() is True
    if refused:
        with pytest.raises(RuntimeError, match="current ceiling"):
            r.read_and_check_currents(limit, False)
    else:
        r.read_and_check_currents(limit, False)
    assert [e for e in r.left_bus.events if e[0] == "sync-read"] == [
        ("sync-read", "Present_Current", tuple(r.left_bus.motors))
    ]
    assert r._am1_selected_joint_current["raw"] == raw
    assert (
        r._am1_selected_joint_current["read_started_at_monotonic_s"]
        <= r._am1_selected_joint_current["read_completed_at_monotonic_s"]
    )


def test_initial_directory_sync_failure_retains_lock_restoration_obligation(monkeypatch):
    r = robot()
    enable(monkeypatch, r)
    t = trial_class()(integral=1)
    real = t._sync_directory
    calls = []

    def interrupted(directory):
        calls.append(directory)
        if len(calls) == 1:
            raise OSError("initial durable directory sync failed")
        real(directory)

    monkeypatch.setattr(t, "_sync_directory", interrupted)
    with pytest.raises(OSError):
        t.prepare(r)
    assert t.restore_required is True
    assert writes(r.left_bus) == []
    t.restore(r)
    assert t.restore_required is False
    assert r.left_bus.values[("Lock", ELBOW)] == 0
    assert t.evidence["first_failure"]["message"] == "initial durable directory sync failed"


def test_final_durable_state_failure_cannot_clear_obligation(monkeypatch):
    r = robot()
    t = prepared(monkeypatch, r)
    t.disable_torque(r)
    t.apply(r)
    real = t._persist_snapshot

    def failed(state, **kwargs):
        if state == "restored":
            raise OSError("restored state not durable")
        real(state, **kwargs)

    monkeypatch.setattr(t, "_persist_snapshot", failed)
    with pytest.raises(OSError):
        t.restore(r)
    assert t.restore_required is True
    assert r.left_bus.values[("I_Coefficient", ELBOW)] == 0
    assert r.left_bus.values[("Lock", ELBOW)] == 0
    monkeypatch.setattr(t, "_persist_snapshot", real)
    t.restore(r)
    assert t.restore_required is False


# Packet12: fixed right/P20 comparison, no parameterized gain API.
RIGHT_P20_MOTOR = "arm_right_elbow_flex"
RIGHT_P20_RUN = "33333333-3333-4333-8333-333333333333"
RIGHT_P20_HOST = "44444444-4444-4444-8444-444444444444"


def right_p20_module():
    return importlib.import_module("lerobot.robots.alohamini.am1_elbow_integral")


def right_p20_request():
    from examples.alohamini.am1_session_contract import LIVE_ELBOW_WORKSPACES
    workspace = LIVE_ELBOW_WORKSPACES["physical-live-right-elbow"]
    return dict(version=1, kind="response12-right-p20", request_id=RIGHT_P20_RUN,
                source_head="a" * 40, workspace_id=workspace.id, context_sha256=workspace.context_sha256)


def right_p20_files(tmp_path):
    state, directory = tmp_path / "state", tmp_path / RIGHT_P20_RUN[:8]
    state.mkdir(mode=0o700); directory.mkdir(mode=0o700)
    request = state / "response12-right-p20-request.json"
    request.write_text(json.dumps(right_p20_request(), sort_keys=True, separators=(",", ":")), encoding="utf-8")
    request.chmod(0o600)
    return state, directory, request


def consume_right_p20(tmp_path, *, recipe="physical-live-right-elbow"):
    state, directory, request = right_p20_files(tmp_path)
    module = right_p20_module()
    assert callable(getattr(module, "consume_right_p20_request", None)), "one-use right P20 request consumer missing"
    path = module.consume_right_p20_request(state, directory, recipe, RIGHT_P20_RUN, RIGHT_P20_HOST, "a" * 40)
    return module, directory, path, request


def right_p20_robot(tmp_path):
    module, directory, path, request = consume_right_p20(tmp_path)
    r = robot()
    bus = r.right_bus
    bus.motors = {RIGHT_P20_MOTOR: Motor(3, "sts3215", MotorNormMode.RANGE_M100_100)}
    bus.calibration = {RIGHT_P20_MOTOR: MotorCalibration(3, 0, -658, 827, 3039)}
    baseline = dict(BASELINE, ID=3, Phase=12, Min_Position_Limit=827, Max_Position_Limit=3039,
                    Homing_Offset=-658, Present_Position=1845, Goal_Position=1845, Max_Torque_Limit=1000,
                    Torque_Limit=1000, Acceleration=254, Maximum_Acceleration=254, CW_Dead_Zone=1,
                    CCW_Dead_Zone=1, Status=0, Present_Voltage=118, Present_Temperature=33)
    bus.values = {(k, RIGHT_P20_MOTOR): v for k, v in baseline.items()}
    r.right_arm_motors = [RIGHT_P20_MOTOR]
    r._am1_visible_control_binding = dict(run_id=RIGHT_P20_RUN, host_incarnation=RIGHT_P20_HOST,
                                         recipe="physical-live-right-elbow")
    r._am1_right_p20_trial_binding = module.load_right_p20_binding(path, directory, RIGHT_P20_RUN, RIGHT_P20_HOST)
    r._am1_right_p20_trial_directory = str(directory)
    trial = module.AM1RightElbowProportionalTrial(r._am1_right_p20_trial_binding, directory)
    r._am1_right_elbow_p_trial = trial
    return r, trial, directory


def test_right_p20_request_is_one_use_and_original_is_retained(tmp_path):
    module, directory, path, request = consume_right_p20(tmp_path)
    assert not request.exists()
    retained = directory / "response12-right-p20-consumed.json"
    assert json.loads(retained.read_bytes()) == right_p20_request()
    binding = module.load_right_p20_binding(path, directory, RIGHT_P20_RUN, RIGHT_P20_HOST)
    assert binding["request"] == right_p20_request()
    assert module.consume_right_p20_request(request.parent, directory, "physical-live-right-elbow",
                                           RIGHT_P20_RUN, RIGHT_P20_HOST, "a" * 40) is None
    with pytest.raises((RuntimeError, ValueError)):
        module.load_right_p20_binding(path, directory, RIGHT_P20_RUN, "55555555-5555-4555-8555-555555555555")


@pytest.mark.parametrize("bad", ["side", "source", "context", "bulk", "noncanonical", "hardlink", "symlink"])
def test_right_p20_bad_request_never_consumes_or_writes(tmp_path, bad):
    import os
    state, directory, request = right_p20_files(tmp_path)
    module = right_p20_module()
    assert callable(getattr(module, "consume_right_p20_request", None)), "one-use right P20 request consumer missing"
    payload = right_p20_request()
    recipe = "physical-live-right-elbow"
    if bad == "side": recipe = "physical-live-left-elbow"
    elif bad == "source": payload["source_head"] = "b" * 40
    elif bad == "context": payload["context_sha256"] = "b" * 64
    elif bad == "bulk": payload["extra"] = "x" * 4096
    if bad in {"source", "context", "bulk"}: request.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    if bad == "noncanonical": request.write_text(json.dumps(payload, indent=2))
    if bad == "hardlink": os.link(request, state / "second-link")
    if bad == "symlink":
        original = state / "original"; request.rename(original)
        try: request.symlink_to(original)
        except OSError: pytest.skip("native symlink privilege unavailable")
    before = request.read_bytes()
    with pytest.raises((RuntimeError, ValueError, OSError)):
        module.consume_right_p20_request(state, directory, recipe, RIGHT_P20_RUN, RIGHT_P20_HOST, "a" * 40)
    assert request.read_bytes() == before
    assert not list(directory.iterdir())


def test_right_p20_durable_original_precedes_any_write_and_restores_before_unlock(tmp_path):
    r, trial, directory = right_p20_robot(tmp_path)
    trial.prepare(r)
    assert not writes(r.right_bus, RIGHT_P20_MOTOR)
    def retained(register, motor, value):
        saved = json.loads(trial.snapshot_path.read_bytes())
        assert saved["restore_required"] is True
        assert saved["original_registers"]["P_Coefficient"] == 16
        if register == "P_Coefficient":
            assert r.right_bus.values[("Torque_Enable", motor)] == 0
            assert r.right_bus.values[("Lock", motor)] == 1
    r.right_bus.before_write = retained
    trial.disable_torque(r); trial.apply(r)
    assert r.right_bus.values[("P_Coefficient", RIGHT_P20_MOTOR)] == 20
    r.right_bus.values[("Torque_Enable", RIGHT_P20_MOTOR)] = 1
    trial.verify_active(r)
    trial.restore(r)
    events = [(e[1], e[3]) for e in writes(r.right_bus, RIGHT_P20_MOTOR)]
    assert events == [("Torque_Enable", 0), ("Lock", 1), ("P_Coefficient", 20),
                      ("Torque_Enable", 0), ("Lock", 1), ("P_Coefficient", 16), ("Lock", 0)]
    assert not trial.restore_required
    assert json.loads(trial.snapshot_path.read_bytes())["state"] == "restored"
    assert not writes(r.left_bus)
    with pytest.raises(RuntimeError): trial.apply(r)


@pytest.mark.parametrize("phase", ["apply", "restore", "foreign_owner", "preserved", "current"])
def test_right_p20_failures_keep_original_and_restoration_obligation(tmp_path, phase):
    r, trial, directory = right_p20_robot(tmp_path)
    trial.prepare(r); trial.disable_torque(r)
    error = OSError("original comparison write failed")
    if phase == "apply": r.right_bus.fail_writes[("P_Coefficient", RIGHT_P20_MOTOR, 20)] = (error, True)
    if phase == "apply":
        with pytest.raises(OSError) as caught: trial.apply(r)
        assert caught.value is error
    else:
        trial.apply(r)
        if phase == "restore": r.right_bus.fail_writes[("P_Coefficient", RIGHT_P20_MOTOR, 16)] = (error, False)
        elif phase == "foreign_owner": r._am1_right_p20_trial_binding = dict(r._am1_right_p20_trial_binding, run_id="foreign")
        elif phase == "preserved": r.right_bus.raw[25] = 1
        elif phase == "current": r.right_bus.values[("Torque_Enable", RIGHT_P20_MOTOR)] = 1; r.right_bus.values[("Present_Current", RIGHT_P20_MOTOR)] = 24
        with pytest.raises((RuntimeError, OSError)):
            trial.verify_active(r) if phase == "current" else trial.restore(r)
    assert trial.restore_required
    saved = json.loads(trial.snapshot_path.read_bytes())
    assert saved["restore_required"] is True and saved["original_registers"]["P_Coefficient"] == 16
    if phase == "apply":
        trial.restore(r)
        assert not trial.restore_required and trial.evidence["first_failure"]["message"] == str(error)
    elif phase == "restore":
        assert r.right_bus.values[("Lock", RIGHT_P20_MOTOR)] == 1



def test_right_p20_actual_robot_configure_and_shutdown_use_trial_without_left_changes(tmp_path):
    r, unused, directory = right_p20_robot(tmp_path)
    del r._am1_right_elbow_p_trial
    # The old I-trial bus enforces its selected-left Lock1 invariant; use a nonselected left motor here.
    r.left_bus.motors.pop(ELBOW)
    r.left_arm_motors = [OTHER]
    readings = []
    evidence = SimpleNamespace(live_spec=object(), motor=RIGHT_P20_MOTOR, position=RIGHT_P20_MOTOR + ".pos",
                               _bus=lambda obj: obj.right_bus)
    evidence.prepare_visible = lambda obj, binding: readings.append((obj.right_bus.values[("P_Coefficient", RIGHT_P20_MOTOR)],
                                                                    obj.right_bus.values[("Lock", RIGHT_P20_MOTOR)]))
    r._am1_visible_control_evidence = evidence
    r.configure()
    assert readings == [(16, 0)]
    assert r.right_bus.values[("P_Coefficient", RIGHT_P20_MOTOR)] == 20
    assert getattr(r, "_am1_right_elbow_p_trial", None) is not None
    assert r._am1_elbow_integral_trial is None
    assert not r._safe_shutdown(close_buses=True)
    assert r.right_bus.values[("P_Coefficient", RIGHT_P20_MOTOR)] == 16
    assert r.right_bus.values[("Lock", RIGHT_P20_MOTOR)] == 0
    assert r._am1_shutdown_evidence["right_p20_trial"]["state"] == "restored"
    assert r._am1_shutdown_evidence["selected_gain_restore_required"] is False


def test_right_p20_shutdown_restore_failure_never_claims_known_cleanup(tmp_path):
    r, trial, directory = right_p20_robot(tmp_path)
    trial.prepare(r); trial.disable_torque(r); trial.apply(r)
    r.right_bus.fail_writes[("P_Coefficient", RIGHT_P20_MOTOR, 16)] = (OSError("restore not applied"), False)
    errors = r._safe_shutdown(close_buses=True)
    assert any("right elbow proportional" in error for error in errors)
    assert r._am1_shutdown_evidence["selected_gain_restore_required"] is True
    assert r._am1_shutdown_evidence["verified"] is False
    assert r.right_bus.values[("Lock", RIGHT_P20_MOTOR)] == 1


def test_right_p20_host_only_binds_the_exact_current_cli_file(tmp_path, monkeypatch):
    from lerobot.robots.alohamini import alohamini_host as host
    module, directory, path, request = consume_right_p20(tmp_path)
    assert callable(getattr(host, "right_p20_trial_binding", None)), "host has no exact-file right P20 admission"
    monkeypatch.setenv("AM1_SCRIPTED_PREPARE", "1")
    monkeypatch.setenv("AM1_SELECTED_JOINT_EVIDENCE", "1")
    args = SimpleNamespace(am1_right_p20_trial_file=str(path), am1_protected_run_directory=str(directory),
                           am1_run_id=RIGHT_P20_RUN, am1_host_incarnation=RIGHT_P20_HOST,
                           robot_model="alohamini1", no_follower=False, no_cameras=True)
    visible = dict(run_id=RIGHT_P20_RUN, host_incarnation=RIGHT_P20_HOST, recipe="physical-live-right-elbow")
    actual = host.right_p20_trial_binding(args, visible)
    assert actual == module.load_right_p20_binding(path, directory, RIGHT_P20_RUN, RIGHT_P20_HOST)
    with pytest.raises(ValueError): host.right_p20_trial_binding(args, dict(visible, recipe="physical-live-left-elbow"))
    args.am1_right_p20_trial_file = None
    assert host.right_p20_trial_binding(args, visible) is None



def test_right_p20_copied_consumed_request_cannot_rearm_another_start(tmp_path):
    module, directory, path, request = consume_right_p20(tmp_path)
    request.write_bytes((directory / "response12-right-p20-consumed.json").read_bytes()); request.chmod(0o600)
    next_run = "55555555-5555-4555-8555-555555555555"
    next_dir = tmp_path / next_run[:8]; next_dir.mkdir(mode=0o700)
    with pytest.raises((ValueError, RuntimeError, FileExistsError)):
        module.consume_right_p20_request(request.parent, next_dir, "physical-live-right-elbow", next_run, RIGHT_P20_HOST, "a" * 40)
    assert request.exists() and not list(next_dir.iterdir())


def test_right_p20_rejected_unprepared_baseline_still_gets_protective_torque_off(tmp_path):
    r, trial, directory = right_p20_robot(tmp_path)
    r.right_bus.values[("Torque_Enable", RIGHT_P20_MOTOR)] = 1
    with pytest.raises(RuntimeError): trial.prepare(r)
    assert not trial.restore_required
    assert not r._safe_shutdown(close_buses=False)
    assert r.right_bus.values[("Torque_Enable", RIGHT_P20_MOTOR)] == 0
    assert r._am1_shutdown_evidence["verified"]


@pytest.mark.parametrize("phase", ["cancel_applied", "configured_persist", "restored_persist", "restore_ack_lost", "foreign_bus", "foreign_mapping"])
def test_right_p20_ambiguous_and_persistence_failures_are_not_silent_rearm(tmp_path, monkeypatch, phase):
    r, trial, directory = right_p20_robot(tmp_path)
    trial.prepare(r); trial.disable_torque(r)
    if phase == "cancel_applied":
        original = KeyboardInterrupt("after actual P20 write")
        r.right_bus.fail_writes[("P_Coefficient", RIGHT_P20_MOTOR, 20)] = (original, True)
        with pytest.raises(KeyboardInterrupt) as caught: trial.apply(r)
        assert caught.value is original and trial.restore_required
        trial.restore(r)
        assert trial.evidence["first_failure"]["type"] == "KeyboardInterrupt"
    elif phase == "configured_persist":
        persist = trial._persist_snapshot
        def fail(state, **kw):
            if state == "configured": raise OSError("configured record failed")
            return persist(state, **kw)
        monkeypatch.setattr(trial, "_persist_snapshot", fail)
        with pytest.raises(OSError, match="configured record"): trial.apply(r)
        assert trial.restore_required and r.right_bus.values[("P_Coefficient", RIGHT_P20_MOTOR)] == 20
        trial.restore(r)
    else:
        trial.apply(r)
        if phase == "restored_persist":
            persist = trial._persist_snapshot
            def fail(state, **kw):
                if state == "restored": raise OSError("restored record failed")
                return persist(state, **kw)
            monkeypatch.setattr(trial, "_persist_snapshot", fail)
            with pytest.raises(OSError, match="restored record"): trial.restore(r)
            assert trial.restore_required and json.loads(trial.snapshot_path.read_bytes())["restore_required"]
        elif phase == "restore_ack_lost":
            r.right_bus.fail_writes[("P_Coefficient", RIGHT_P20_MOTOR, 16)] = (OSError("ACK lost"), True)
            trial.restore(r)
            assert not trial.restore_required and trial.evidence["first_failure"]["message"] == "ACK lost"
        else:
            before = list(r.right_bus.events)
            if phase == "foreign_bus": r.right_bus = OwnerBus()
            else: r.right_bus.calibration[RIGHT_P20_MOTOR].range_min += 1
            with pytest.raises(RuntimeError): trial.restore(r)
            assert trial.restore_required
            assert not writes(r.right_bus, RIGHT_P20_MOTOR)[len([e for e in before if e[0] == "write"]):]
            return
    if phase != "restored_persist":
        assert not trial.restore_required
        assert r.right_bus.values[("P_Coefficient", RIGHT_P20_MOTOR)] == 16
        assert r.right_bus.values[("Lock", RIGHT_P20_MOTOR)] == 0
