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


def practical_robot(tmp_path, proportional):
    module = right_p20_module()
    state, directory, old_request = right_p20_files(tmp_path)
    legacy_marker = state / "response12-right-p20-used.json"
    legacy_marker.write_bytes(b"retained-packet12-consumption")
    old_request.unlink()
    request = dict(version=1, kind="practical13-right", request_id=RIGHT_P20_RUN,
                   source_head="a" * 40, config=dict(reference_raw=1847, direction=1), proportional=proportional)
    practical = state / "practical13-right-request.json"
    practical.write_bytes(module._right_p20_json(request)); practical.chmod(0o600)
    path = module.consume_right_p20_request(state, directory, "physical-live-right-elbow-practical",
                                            RIGHT_P20_RUN, RIGHT_P20_HOST, "a" * 40)
    assert path == directory / "practical13-right.json"
    assert legacy_marker.read_bytes() == b"retained-packet12-consumption"
    assert (state / ("practical13-right-used-" + RIGHT_P20_RUN + ".json")).exists()
    binding = module.load_right_p20_binding(path, directory, RIGHT_P20_RUN, RIGHT_P20_HOST)
    # Reuse the actual transaction fake, replacing only its private binding.
    separate = tmp_path / "legacy-fixture"; separate.mkdir()
    r, _, _ = right_p20_robot(separate)
    r._am1_right_p20_trial_binding = binding
    r._am1_visible_control_binding["recipe"] = "physical-live-right-elbow-practical"
    r._am1_right_p20_trial_directory = str(directory)
    trial = module.AM1RightElbowProportionalTrial(binding, directory)
    r._am1_right_elbow_p_trial = trial
    r._am1_practical_config = trial.config
    return r, trial, directory, request, legacy_marker


@pytest.mark.parametrize("proportional", [16, 24, 32])
def test_practical_fresh_transaction_restores_p16_and_keeps_packet12_consumed(tmp_path, proportional):
    r, trial, directory, request, legacy_marker = practical_robot(tmp_path, proportional)
    trial.prepare(r); trial.disable_torque(r); trial.apply(r)
    assert r.right_bus.values[("P_Coefficient", RIGHT_P20_MOTOR)] == proportional
    r.right_bus.values[("Torque_Enable", RIGHT_P20_MOTOR)] = 1
    r.right_bus.values[("Present_Current", RIGHT_P20_MOTOR)] = 76
    trial.verify_active(r)
    assert trial.receipt["configured_p"] == proportional
    assert trial.receipt["config"] == request["config"]
    r.right_bus.values[("Present_Current", RIGHT_P20_MOTOR)] = 77
    with pytest.raises(RuntimeError, match="current"):
        trial.verify_active(r)
    trial.restore(r)
    assert r.right_bus.values[("P_Coefficient", RIGHT_P20_MOTOR)] == 16
    assert r.right_bus.values[("Lock", RIGHT_P20_MOTOR)] == 0
    assert not trial.restore_required
    assert legacy_marker.read_bytes() == b"retained-packet12-consumption"
    if proportional == 16:
        module = right_p20_module()
        state = legacy_marker.parent
        repeated = dict(request, source_head="b" * 40)
        retry = state / "practical13-right-request.json"
        retry.write_bytes(module._right_p20_json(repeated)); retry.chmod(0o600)
        next_run = "55555555-5555-4555-8555-555555555555"
        next_directory = directory.parent / next_run[:8]; next_directory.mkdir(mode=0o700)
        with pytest.raises(ValueError, match="nonce"):
            module.consume_right_p20_request(state, next_directory, "physical-live-right-elbow-practical",
                                            next_run, RIGHT_P20_HOST, "b" * 40)
        assert retry.exists() and not list(next_directory.iterdir())


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



@pytest.mark.parametrize("new_source", [False, True])
@pytest.mark.parametrize("archive_kind", ["file", "dangling_symlink"])
def test_right_p20_archived_nonce_refuses_rewritten_request_before_global_claim(tmp_path, new_source, archive_kind):
    module, directory, path, request = consume_right_p20(tmp_path)
    state = request.parent
    archived = state / ("response12-right-p20-used-" + RIGHT_P20_RUN + ".json")
    (state / "response12-right-p20-used.json").rename(archived)
    original_archive = archived.read_bytes()
    if archive_kind == "dangling_symlink":
        archived.unlink()
        try: archived.symlink_to(state / "absent-retirement-record")
        except OSError: pytest.skip("native symlink privilege unavailable")
        assert not archived.exists() and archived.is_symlink()
    payload = right_p20_request()
    source = "b" * 40 if new_source else "a" * 40
    payload["source_head"] = source
    request.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8"); request.chmod(0o600)
    next_run = "55555555-5555-4555-8555-555555555555"
    next_dir = tmp_path / next_run[:8]; next_dir.mkdir(mode=0o700)
    with pytest.raises(ValueError, match="nonce"):
        module.consume_right_p20_request(state, next_dir, "physical-live-right-elbow", next_run, RIGHT_P20_HOST, source)
    assert request.exists() and not list(next_dir.iterdir())
    assert not (state / "response12-right-p20-used.json").exists()
    if archive_kind == "file": assert archived.read_bytes() == original_archive
    else: assert archived.is_symlink()


@pytest.mark.parametrize("global_marker_present", [False, True])
def test_right_p20_fresh_nonce_requires_absent_global_marker_after_explicit_retirement(tmp_path, global_marker_present):
    module, directory, path, request = consume_right_p20(tmp_path)
    state = request.parent
    used = state / "response12-right-p20-used.json"
    original_used = used.read_bytes()
    if not global_marker_present:
        used.rename(state / ("response12-right-p20-used-" + RIGHT_P20_RUN + ".json"))
    payload = right_p20_request()
    payload.update(request_id="66666666-6666-4666-8666-666666666666", source_head="b" * 40)
    request.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8"); request.chmod(0o600)
    next_run = "55555555-5555-4555-8555-555555555555"
    next_dir = tmp_path / next_run[:8]; next_dir.mkdir(mode=0o700)
    if global_marker_present:
        with pytest.raises(FileExistsError):
            module.consume_right_p20_request(state, next_dir, "physical-live-right-elbow", next_run, RIGHT_P20_HOST, "b" * 40)
        assert request.exists() and used.read_bytes() == original_used and not list(next_dir.iterdir())
    else:
        result = module.consume_right_p20_request(state, next_dir, "physical-live-right-elbow", next_run, RIGHT_P20_HOST, "b" * 40)
        assert result == next_dir / "response12-right-p20.json" and not request.exists()
        bound = module.load_right_p20_binding(result, next_dir, next_run, RIGHT_P20_HOST)
        assert bound["request"]["request_id"] == "66666666-6666-4666-8666-666666666666"
        assert json.loads(used.read_bytes())["request"]["request_id"] == "66666666-6666-4666-8666-666666666666"


LEFT_P_BOOT = "77777777-7777-4777-8777-777777777777"


def left_response_p_files(tmp_path, monkeypatch, *, proportional=32, workspace_profile=None, left_shoulder_startup_degrees=None,
                          workload_segment=None):
    module = right_p20_module()
    assert callable(getattr(module, "consume_left_response_p_request", None)), (
        "left proportional consumer missing"
    )
    monkeypatch.setattr(module, "_left_response_p_boot_id", lambda: LEFT_P_BOOT)
    state, directory = tmp_path / "state", tmp_path / RIGHT_P20_RUN[:8]
    state.mkdir(mode=0o700)
    directory.mkdir(mode=0o700)
    value = {
        "version": 1,
        "kind": "left-response-p",
        "request_id": RIGHT_P20_RUN,
        "source_head": "a" * 40,
        "proportional": proportional,
        "workload_segment": "left-maintained",
        "boot_id": LEFT_P_BOOT,
        "expires_at_monotonic_s": module.time.monotonic() + 60.0,
    }
    if workspace_profile is not None:
        value.update(workspace_profile=workspace_profile, workload_segment="left-reach")
    if workload_segment is not None:
        value["workload_segment"] = workload_segment
    if left_shoulder_startup_degrees is not None:
        value["left_shoulder_startup_degrees"] = left_shoulder_startup_degrees
    request = state / "left-response-p-request.json"
    request.write_bytes(module._right_p20_json(value))
    request.chmod(0o600)
    return module, state, directory, request, value


def left_response_p_robot(tmp_path, monkeypatch, *, proportional=32, workspace_profile=None, left_shoulder_startup_degrees=None,
                          workload_segment=None):
    module, state, directory, request, value = left_response_p_files(
        tmp_path, monkeypatch, proportional=proportional, workspace_profile=workspace_profile,
        left_shoulder_startup_degrees=left_shoulder_startup_degrees,
        workload_segment=workload_segment,
    )
    path = module.consume_left_response_p_request(
        state, directory, "physical-live-teleop", RIGHT_P20_RUN, RIGHT_P20_HOST, "a" * 40
    )
    binding = module.load_left_response_p_binding(path, directory, RIGHT_P20_RUN, RIGHT_P20_HOST)
    r = robot()
    r._am1_selected_joint_evidence_enabled = False
    del r._am1_elbow_recovery_binding
    r._am1_teleop_binding = {
        "run_id": RIGHT_P20_RUN,
        "host_incarnation": RIGHT_P20_HOST,
        "recipe": "physical-live-teleop",
    }
    baseline = dict(
        BASELINE,
        ID=3,
        Phase=12,
        Present_Position=2044,
        Goal_Position=2037,
        Max_Torque_Limit=1000,
        Torque_Limit=1000,
        Acceleration=254,
        Maximum_Acceleration=254,
        CW_Dead_Zone=1,
        CCW_Dead_Zone=1,
        Goal_Time=0,
        Goal_Velocity=0,
        Protection_Current=310,
        Max_Temperature_Limit=70,
        Min_Voltage_Limit=40,
        Max_Voltage_Limit=140,
        Protective_Torque=20,
        Protection_Time=200,
        Overload_Torque=80,
        Over_Current_Protection_Time=200,
        Velocity_closed_loop_P_proportional_coefficient=10,
        Velocity_closed_loop_I_integral_coefficient=200,
        Present_Current=0,
        Status=0,
        Present_Voltage=119,
        Present_Temperature=28,
    )
    if workspace_profile is not None:
        baseline.update(Present_Position=1090, Goal_Position=2050)
    r.left_bus.values.update({(k, ELBOW): v for k, v in baseline.items()})
    from lerobot.robots.alohamini.am1_live_teleop import STATIC

    r._am1_teleop_settings = {"left": {"registers": {k: {ELBOW: baseline[k]} for k in STATIC}}}
    r._am1_left_response_p_binding = binding
    trial = module.AM1LeftResponseProportionalTrial(binding, directory)
    r._am1_left_response_p_trial = trial
    return module, r, trial, directory, request, value


@pytest.mark.parametrize("proportional", [16, 32])
def test_left_response_p_consumes_once_and_binding_survives_request_deadline(
    tmp_path, monkeypatch, proportional
):
    module, r, trial, directory, request, value = left_response_p_robot(
        tmp_path, monkeypatch, proportional=proportional
    )
    assert not request.exists()
    assert (request.parent / ("left-response-p-used-" + value["request_id"] + ".json")).exists()
    assert (directory / "left-response-p-consumed.json").read_bytes() == module._right_p20_json(value)
    monkeypatch.setattr(module.time, "monotonic", lambda: value["expires_at_monotonic_s"] + 1.0)
    assert (
        module.load_left_response_p_binding(
            directory / "left-response-p.json", directory, RIGHT_P20_RUN, RIGHT_P20_HOST
        )
        == trial.binding
    )
    trial.prepare(r)
    trial.disable_torque(r)
    trial.apply(r)
    assert (
        trial.receipt["configured_p"] == proportional
        and trial.receipt["workload_segment"] == "left-maintained"
    )
    trial.restore(r)
    request.write_bytes(module._right_p20_json(value))
    request.chmod(0o600)
    with pytest.raises(ValueError):
        module.consume_left_response_p_request(
            request.parent, directory, "physical-live-teleop", RIGHT_P20_RUN, RIGHT_P20_HOST, "a" * 40
        )
    assert request.exists()


@pytest.mark.parametrize(
    "bad",
    [
        "expired",
        "boot",
        "source",
        "recipe",
        "gain",
        "gain_bool",
        "segment",
        "extra",
        "noncanonical",
        "hardlink",
    ],
)
def test_left_response_p_bad_request_is_rejected_before_consumption(tmp_path, monkeypatch, bad):
    import os

    module, state, directory, request, value = left_response_p_files(tmp_path, monkeypatch)
    recipe = "physical-live-teleop"
    if bad == "expired":
        value["expires_at_monotonic_s"] = 0.0
    elif bad == "boot":
        value["boot_id"] = "88888888-8888-4888-8888-888888888888"
    elif bad == "source":
        value["source_head"] = "b" * 40
    elif bad == "recipe":
        recipe = "physical-live-right-elbow"
    elif bad == "gain":
        value["proportional"] = 24
    elif bad == "gain_bool":
        value["proportional"] = True
    elif bad == "segment":
        value["workload_segment"] = "full"
    elif bad == "extra":
        value["cap_ma"] = 100
    request.write_bytes(module._right_p20_json(value))
    if bad == "noncanonical":
        request.write_text(json.dumps(value, indent=2), encoding="utf-8")
    if bad == "hardlink":
        os.link(request, state / "linked")
    with pytest.raises((RuntimeError, ValueError, OSError)):
        module.consume_left_response_p_request(
            state, directory, recipe, RIGHT_P20_RUN, RIGHT_P20_HOST, "a" * 40
        )
    assert request.exists() and not list(directory.iterdir())


@pytest.mark.parametrize("proportional", [16, 32])
def test_left_response_p_durable_original_and_exact_restoration_without_legacy_current_cap(
    tmp_path, monkeypatch, proportional
):
    module, r, trial, directory, request, value = left_response_p_robot(
        tmp_path, monkeypatch, proportional=proportional
    )
    trial.prepare(r)
    assert not writes(r.left_bus)

    def retained(register, motor, new_value):
        saved = json.loads(trial.snapshot_path.read_bytes())
        assert saved["restore_required"] and saved["original_registers"]["P_Coefficient"] == 16
        if register == "P_Coefficient":
            assert (
                r.left_bus.values[("Torque_Enable", motor)] == 0 and r.left_bus.values[("Lock", motor)] == 1
            )

    r.left_bus.before_write = retained
    trial.disable_torque(r)
    trial.apply(r)
    r.left_bus.values[("Torque_Enable", ELBOW)] = 1
    r.left_bus.values[("Present_Current", ELBOW)] = 100  # 650mA exceeds superseded diagnostic caps.
    trial.verify_active(r)
    assert (
        trial.evidence["active_verified"]
        and trial.evidence["active_registers"]["P_Coefficient"] == proportional
    )
    assert trial.receipt["original_settings_sha256"] == trial.evidence["original_settings_sha256"]
    trial.restore(r)
    expected = [("Torque_Enable", 0), ("Lock", 1)]
    if proportional == 32:
        expected += [("P_Coefficient", 32)]
    expected += [("Torque_Enable", 0), ("Lock", 1)]
    if proportional == 32:
        expected += [("P_Coefficient", 16)]
    expected += [("Lock", 0)]
    assert [(e[1], e[3]) for e in writes(r.left_bus)] == expected
    assert not writes(r.left_bus, OTHER) and not writes(r.right_bus)
    assert not trial.restore_required and json.loads(trial.snapshot_path.read_bytes())["status"] == "restored"
    assert r.left_bus.raw == {24: 16, 25: 0}


@pytest.mark.parametrize(
    "mutation",
    ["max_torque", "torque_limit", "startup", "integral_limit", "deadband", "PID", "legacy", "AM2"],
)
def test_left_response_p_unknown_actual_baseline_retains_proof_without_writes(
    tmp_path, monkeypatch, mutation
):
    module, r, trial, directory, request, value = left_response_p_robot(tmp_path, monkeypatch)
    if mutation == "max_torque":
        r.left_bus.values[("Max_Torque_Limit", ELBOW)] = 500
    elif mutation == "torque_limit":
        r.left_bus.values[("Torque_Limit", ELBOW)] = 500
    elif mutation == "startup":
        r.left_bus.raw[24] = 17
    elif mutation == "integral_limit":
        r.left_bus.raw[25] = 1
    elif mutation == "deadband":
        r.left_bus.values[("CW_Dead_Zone", ELBOW)] = 2
    elif mutation == "PID":
        r.left_bus.values[("I_Coefficient", ELBOW)] = 1
    elif mutation == "legacy":
        r._am1_selected_joint_evidence_enabled = True
    else:
        r.config.robot_model = "alohamini2"
    with pytest.raises(RuntimeError):
        trial.prepare(r)
    assert not writes(r.left_bus)
    if mutation not in {"legacy", "AM2"}:
        assert trial.evidence["original_registers"] and trial.evidence["original_bytes"]
        assert json.loads(trial.snapshot_path.read_bytes())["status"] == "baseline_refused"
        assert not trial.restore_required


@pytest.mark.parametrize(
    "phase",
    [
        "apply_lost",
        "cancel",
        "restore_lost",
        "restore_failed",
        "preserved",
        "owner",
        "current",
        "configured_persist",
        "restored_persist",
    ],
)
def test_left_response_p_failure_keeps_original_until_verified_restore(tmp_path, monkeypatch, phase):
    module, r, trial, directory, request, value = left_response_p_robot(tmp_path, monkeypatch)
    trial.prepare(r)
    trial.disable_torque(r)
    if phase in {"apply_lost", "cancel"}:
        error = KeyboardInterrupt("cancel after P32") if phase == "cancel" else OSError("P32 ACK lost")
        r.left_bus.fail_writes[("P_Coefficient", ELBOW, 32)] = (error, True)
        with pytest.raises(type(error)):
            trial.apply(r)
        assert trial.restore_required
    else:
        if phase == "configured_persist":
            persist = trial._persist_snapshot

            def fail(state, **kwargs):
                if state == "configured":
                    raise OSError("configured persistence")
                return persist(state, **kwargs)

            monkeypatch.setattr(trial, "_persist_snapshot", fail)
            with pytest.raises(OSError):
                trial.apply(r)
        else:
            trial.apply(r)
        if phase == "restore_lost":
            r.left_bus.fail_writes[("P_Coefficient", ELBOW, 16)] = (OSError("restore ACK lost"), True)
        if phase == "restore_failed":
            r.left_bus.fail_writes[("P_Coefficient", ELBOW, 16)] = (OSError("restore refused"), False)
        if phase == "preserved":
            r.left_bus.raw[25] = 1
        if phase == "owner":
            r._am1_teleop_binding = dict(r._am1_teleop_binding, run_id="foreign")
        if phase == "current":
            r.left_bus.values[("Torque_Enable", ELBOW)] = 1
            r.left_bus.values[("Present_Current", ELBOW)] = 310
            with pytest.raises(RuntimeError):
                trial.verify_active(r)
            r.left_bus.values[("Present_Current", ELBOW)] = 0
        if phase == "restored_persist":
            persist = trial._persist_snapshot

            def fail(state, **kwargs):
                if state == "restored":
                    raise OSError("restored persistence")
                return persist(state, **kwargs)

            monkeypatch.setattr(trial, "_persist_snapshot", fail)
    if phase in {"restore_failed", "preserved", "owner", "restored_persist"}:
        with pytest.raises((RuntimeError, OSError)):
            trial.restore(r)
        assert trial.restore_required and json.loads(trial.snapshot_path.read_bytes())["restore_required"]
        if phase != "owner":
            assert r.left_bus.values[("Lock", ELBOW)] == 1
    else:
        trial.restore(r)
        assert not trial.restore_required and r.left_bus.values[("P_Coefficient", ELBOW)] == 16
        assert r.left_bus.values[("Lock", ELBOW)] == 0


@pytest.mark.parametrize("change", [None, "bus", "calibration", "binding", "frozen", "restored"])
def test_left_response_p_active_receipt_is_read_free_and_fenced_to_original_owner(
    tmp_path, monkeypatch, change
):
    import copy

    module, r, trial, directory, request, value = left_response_p_robot(tmp_path, monkeypatch)
    trial.prepare(r)
    trial.disable_torque(r)
    trial.apply(r)
    with pytest.raises(RuntimeError):
        trial.active_receipt(r)
    r.left_bus.values[("Torque_Enable", ELBOW)] = 1
    trial.verify_active(r)
    bus = r.left_bus
    if change == "bus":
        r.left_bus = OwnerBus()
    elif change == "calibration":
        r.left_bus.calibration[ELBOW].range_min += 1
    elif change == "binding":
        trial.binding["request"]["workload_segment"] = "left-reach"
        r._am1_left_response_p_binding = copy.deepcopy(trial.binding)
    elif change == "frozen":
        r._am1_teleop_settings["left"]["registers"]["Protection_Current"][ELBOW] = 500
    elif change == "restored":
        trial.restore(r)
    before = list(bus.events)
    if change is None:
        receipt = trial.active_receipt(r)
        assert receipt == trial.receipt
        receipt["configured_p"] = 99
        assert trial.active_receipt(r)["configured_p"] == 32
    else:
        with pytest.raises(RuntimeError):
            trial.active_receipt(r)
    assert bus.events == before


def test_left_response_p_retired_nonce_refuses_new_source_and_extended_expiry(tmp_path, monkeypatch):
    module, r, trial, directory, request, value = left_response_p_robot(tmp_path, monkeypatch)
    value.update(source_head="b" * 40, expires_at_monotonic_s=module.time.monotonic() + 120.0)
    request.write_bytes(module._right_p20_json(value))
    request.chmod(0o600)
    next_run = "66666666-6666-4666-8666-666666666666"
    next_directory = directory.parent / next_run[:8]
    next_directory.mkdir(mode=0o700)
    with pytest.raises(ValueError, match="nonce"):
        module.consume_left_response_p_request(
            request.parent, next_directory, "physical-live-teleop", next_run, RIGHT_P20_HOST, "b" * 40
        )
    assert request.exists() and not list(next_directory.iterdir())


def test_left_response_p_prepare_persistence_failure_never_reaches_device_write(tmp_path, monkeypatch):
    module, r, trial, directory, request, value = left_response_p_robot(tmp_path, monkeypatch)
    monkeypatch.setattr(
        trial,
        "_persist_snapshot",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("original persistence")),
    )
    with pytest.raises(OSError, match="original persistence"):
        trial.prepare(r)
    assert not writes(r.left_bus) and trial.restore_required
    with pytest.raises(RuntimeError, match="durable"):
        trial.disable_torque(r)
    assert not writes(r.left_bus)


def test_left_response_p_expiry_during_private_file_checks_refuses_before_consumption(tmp_path, monkeypatch):
    module, state, directory, request, value = left_response_p_files(tmp_path, monkeypatch)
    clock = iter((value["expires_at_monotonic_s"] - 1.0, value["expires_at_monotonic_s"]))
    monkeypatch.setattr(module.time, "monotonic", lambda: next(clock))
    with pytest.raises(ValueError, match="expired"):
        module.consume_left_response_p_request(
            state, directory, "physical-live-teleop", RIGHT_P20_RUN, RIGHT_P20_HOST, "a" * 40
        )
    assert request.exists() and not list(directory.iterdir())
    assert not list(state.glob("left-response-p-used-*"))


def test_left_response_p_does_not_reject_accepted_single_raw_temperature_anomaly(tmp_path, monkeypatch):
    module, r, trial, directory, request, value = left_response_p_robot(tmp_path, monkeypatch)
    trial.prepare(r)
    trial.disable_torque(r)
    trial.apply(r)
    r.left_bus.values[("Torque_Enable", ELBOW)] = 1
    r.left_bus.values[("Present_Temperature", ELBOW)] = 78
    trial.verify_active(r)  # Existing qualified owner windows decide actual thermal readiness.
    assert trial.evidence["active_registers"]["Present_Temperature"] == 78
    trial.restore(r)


def test_left_response_p_each_actual_bus_boundary_services_existing_startup_cadence(tmp_path, monkeypatch):
    module, r, trial, directory, request, value = left_response_p_robot(tmp_path, monkeypatch)
    from lerobot.robots.alohamini import am1_live_teleop

    bus = r.left_bus
    r._am1_teleop_startup_temperature = True
    r._lift_operation = object()
    monkeypatch.setattr(
        am1_live_teleop, "refresh_startup_temperatures", lambda robot: bus.events.append(("thermal-due",))
    )
    monkeypatch.setattr(r, "_refresh_am1_startup_lift_feedback", lambda: bus.events.append(("lift-due",)))
    trial.prepare(r)
    trial.disable_torque(r)
    trial.apply(r)
    r.left_bus.values[("Torque_Enable", ELBOW)] = 1
    trial.verify_active(r)
    trial.restore(r)
    for i, event in enumerate(bus.events):
        if event[0] in {"read", "raw-read", "write"} and event[1] != "Present_Temperature":
            assert bus.events[i - 2 : i] == [("thermal-due",), ("lift-due",)]
    assert not trial.restore_required


def test_left_response_p_startup_fault_remains_binding_but_shutdown_can_restore_original(
    tmp_path, monkeypatch
):
    module, r, trial, directory, request, value = left_response_p_robot(tmp_path, monkeypatch)
    from lerobot.robots.alohamini import am1_live_teleop

    trial.prepare(r)
    trial.disable_torque(r)
    trial.apply(r)
    r.left_bus.values[("Torque_Enable", ELBOW)] = 1
    r._am1_teleop_startup_temperature = True
    fault = RuntimeError("original startup thermal/lift fault")
    r._am1_teleop_first_error = fault
    original_slots = object()
    r._am1_teleop_temperatures = original_slots
    services = []

    def failed_refresh(robot):
        services.append("startup")
        raise fault

    monkeypatch.setattr(am1_live_teleop, "refresh_startup_temperatures", failed_refresh)
    with pytest.raises(RuntimeError) as caught:
        trial.verify_active(r)
    assert caught.value is fault and services == ["startup"]
    assert trial.restore_required and r.left_bus.values[("P_Coefficient", ELBOW)] == 32
    r._am1_shutdown_in_progress = True
    trial.restore(r)
    assert r._am1_teleop_first_error is fault and r._am1_teleop_temperatures is original_slots
    assert r._am1_teleop_startup_temperature is True and services == ["startup"]
    assert r.left_bus.values[("P_Coefficient", ELBOW)] == 16
    assert r.left_bus.values[("Torque_Enable", ELBOW)] == 0
    assert r.left_bus.values[("Lock", ELBOW)] == 0 and not trial.restore_required
    assert trial.evidence["first_failure"]["message"] == str(fault)


@pytest.mark.parametrize("phase", ["prepare", "apply", "verify", "receipt"])
def test_left_response_p_shutdown_is_not_active_comparison_authority(tmp_path, monkeypatch, phase):
    module, r, trial, directory, request, value = left_response_p_robot(tmp_path, monkeypatch)
    if phase != "prepare":
        trial.prepare(r)
        trial.disable_torque(r)
    if phase in {"verify", "receipt"}:
        trial.apply(r)
        r.left_bus.values[("Torque_Enable", ELBOW)] = 1
    if phase == "receipt":
        trial.verify_active(r)
    before = list(r.left_bus.events)
    r._am1_shutdown_in_progress = True
    operation = {
        "prepare": trial.prepare,
        "apply": trial.apply,
        "verify": trial.verify_active,
        "receipt": trial.active_receipt,
    }[phase]
    with pytest.raises(RuntimeError):
        operation(r)
    assert r.left_bus.events == before
    if phase != "prepare":
        trial.restore(r)
        assert r.left_bus.values[("P_Coefficient", ELBOW)] == 16 and not trial.restore_required


@pytest.mark.parametrize("descriptor", ["left-response-p.json", "left-response-p-consumed.json"])
@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_left_response_p_descriptor_loss_still_turns_off_but_cannot_restore_gain(
    tmp_path, monkeypatch, descriptor, damage
):
    module, r, trial, directory, request, value = left_response_p_robot(tmp_path, monkeypatch)
    trial.prepare(r)
    trial.disable_torque(r)
    trial.apply(r)
    r.left_bus.values[("Torque_Enable", ELBOW)] = 1
    trial.verify_active(r)
    path = directory / descriptor
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(b"corrupt")
    r._am1_shutdown_in_progress = True
    before = len(r.left_bus.events)
    trial.disable_torque(r)
    assert r.left_bus.values[("Torque_Enable", ELBOW)] == 0
    assert r.left_bus.values[("Lock", ELBOW)] == 1
    with pytest.raises((OSError, ValueError)):
        trial.restore(r)
    assert r.left_bus.values[("P_Coefficient", ELBOW)] == 32
    assert r.left_bus.values[("Torque_Enable", ELBOW)] == 0
    assert r.left_bus.values[("Lock", ELBOW)] == 1 and trial.restore_required
    assert json.loads(trial.snapshot_path.read_bytes())["restore_required"] is True
    assert trial.evidence["state"] == "restore_failed"
    assert not any(
        row[:3] == ("write", "P_Coefficient", ELBOW) for row in r.left_bus.events[before:]
    )


@pytest.mark.parametrize("change", ["bus", "calibration", "run"])
def test_left_response_p_descriptor_loss_does_not_bypass_retained_torque_off_owner(
    tmp_path, monkeypatch, change
):
    module, r, trial, directory, request, value = left_response_p_robot(tmp_path, monkeypatch)
    trial.prepare(r)
    trial.disable_torque(r)
    trial.apply(r)
    r.left_bus.values[("Torque_Enable", ELBOW)] = 1
    trial.verify_active(r)
    (directory / "left-response-p.json").unlink()
    bus = r.left_bus
    if change == "bus":
        r.left_bus = OwnerBus()
    elif change == "calibration":
        r.left_bus.calibration[ELBOW].range_min += 1
    else:
        r._am1_teleop_binding = dict(r._am1_teleop_binding, run_id="foreign")
    before = list(bus.events)
    other_before = list(r.left_bus.events)
    r._am1_shutdown_in_progress = True
    with pytest.raises(RuntimeError):
        trial.disable_torque(r)
    assert bus.events == before and r.left_bus.events == other_before
    assert bus.values[("Torque_Enable", ELBOW)] == 1 and trial.restore_required


@pytest.mark.parametrize("fault", [None, "current", "owner", "cancel"])
def test_left_response_p_powered_proof_keeps_durable_configured_obligation_until_off(
    tmp_path, monkeypatch, fault
):
    # Retire earlier fault-fixture owner cycles before intercepting this owner's private reads.
    import gc

    gc.collect()
    module, r, trial, directory, request, value = left_response_p_robot(tmp_path, monkeypatch)
    trial.prepare(r)
    trial.disable_torque(r)
    trial.apply(r)
    durable = trial.snapshot_path.read_bytes()
    configured = json.loads(durable)
    assert configured["state"] == "configured" and configured["restore_required"] is True
    assert configured["original_registers"]["P_Coefficient"] == 16
    r.left_bus.values[("Torque_Enable", ELBOW)] = 1
    persist = trial._persist_snapshot
    loads, saved = [], []
    original_load = module.load_left_response_p_binding

    def load(*args, **kwargs):
        loads.append("private-binding-read")
        return original_load(*args, **kwargs)

    def record(state, **kwargs):
        assert r.left_bus.values[("Torque_Enable", ELBOW)] == 0, "No private persistence while powered"
        saved.append(state)
        return persist(state, **kwargs)

    monkeypatch.setattr(module, "load_left_response_p_binding", load)
    monkeypatch.setattr(trial, "_persist_snapshot", record)
    bus = r.left_bus
    if fault == "current":
        bus.values[("Present_Current", ELBOW)] = 310
    elif fault == "owner":
        r._am1_teleop_binding = dict(r._am1_teleop_binding, run_id="foreign")
    elif fault == "cancel":
        r._am1_shutdown_in_progress = True
    before = list(bus.events)
    if fault is None:
        trial.verify_active(r)
        assert trial.active_receipt(r)["original_settings_sha256"] == configured["original_settings_sha256"]
        assert trial.evidence["state"] == "active" and trial.evidence["active_verified"] is True
    else:
        with pytest.raises(RuntimeError):
            trial.verify_active(r)
        assert trial.evidence["first_failure"] and trial.restore_required
        if fault in ("owner", "cancel"):
            assert bus.events == before
    assert loads == [] and saved == []
    assert trial.snapshot_path.read_bytes() == durable
    # Stop remains synchronous: OFF, original gain, original Lock, then one durable final proof.
    r._am1_teleop_binding = {k: trial.binding[k] for k in ("run_id", "host_incarnation")}
    r._am1_teleop_binding["recipe"] = "physical-live-teleop"
    r._am1_shutdown_in_progress = True
    trial.restore(r)
    assert bus.values[("Torque_Enable", ELBOW)] == 0 and bus.values[("P_Coefficient", ELBOW)] == 16
    assert bus.values[("Lock", ELBOW)] == 0 and trial.restore_required is False
    assert saved == ["restored"]
    final = json.loads(trial.snapshot_path.read_bytes())
    if fault is None:
        assert final["active_verified"] and final["active_registers"]["P_Coefficient"] == 32
    else:
        assert final["first_failure"] == trial.evidence["first_failure"]
    assert not list(directory.glob("*.tmp")) and not list(directory.glob(".*.tmp"))


RIGHT_RESPONSE_ELBOW = "arm_right_elbow_flex"


def right_response_p_robot(tmp_path, monkeypatch, *, proportional=32):
    seed = tmp_path / "left-seed"
    seed.mkdir(mode=0o700)
    module, r, _left, _directory, _request, value = left_response_p_robot(
        seed, monkeypatch, proportional=proportional
    )
    assert callable(getattr(module, "consume_right_response_p_request", None)), "right response consumer missing"
    assert callable(getattr(module, "load_right_response_p_binding", None)), "right response loader missing"
    state, parent = tmp_path / "right-state", tmp_path / "right-owner"
    state.mkdir(mode=0o700)
    parent.mkdir(mode=0o700)
    directory = parent / RIGHT_P20_RUN[:8]
    directory.mkdir(mode=0o700)
    value = dict(value, kind="right-response-p", workload_segment="right-peer-fixed")
    request = state / "right-response-p-request.json"
    request.write_bytes(module._right_p20_json(value))
    request.chmod(0o600)
    path = module.consume_right_response_p_request(
        state, directory, "physical-live-teleop", RIGHT_P20_RUN, RIGHT_P20_HOST, "a" * 40
    )
    binding = module.load_right_response_p_binding(path, directory, RIGHT_P20_RUN, RIGHT_P20_HOST)
    bus = OwnerBus()
    bus.motors = {RIGHT_RESPONSE_ELBOW: Motor(3, "sts3215", MotorNormMode.RANGE_M100_100)}
    bus.calibration = {RIGHT_RESPONSE_ELBOW: MotorCalibration(3, 0, -658, 827, 3039)}
    baseline = {register: val for (register, motor), val in r.left_bus.values.items() if motor == ELBOW}
    baseline.update(Min_Position_Limit=827, Max_Position_Limit=3039, Homing_Offset=-658,
                    Present_Position=1855, Goal_Position=1855)
    bus.values = {(key, RIGHT_RESPONSE_ELBOW): val for key, val in baseline.items()}
    r.right_bus, r.right_arm_motors = bus, [RIGHT_RESPONSE_ELBOW]
    del r._am1_left_response_p_binding
    del r._am1_left_response_p_trial
    from lerobot.robots.alohamini.am1_live_teleop import STATIC
    r._am1_teleop_settings["right"] = {"registers": {k: {RIGHT_RESPONSE_ELBOW: baseline[k]} for k in STATIC}}
    r._am1_right_response_p_binding = binding
    trial = module.AM1RightResponseProportionalTrial(binding, directory)
    r._am1_right_response_p_trial = trial
    return module, r, trial, directory, request, value


@pytest.mark.parametrize("proportional", [16, 32])
def test_right_response_p_exact_owner_original_active_and_lock_last_restoration(tmp_path, monkeypatch, proportional):
    module, r, trial, directory, request, value = right_response_p_robot(tmp_path, monkeypatch, proportional=proportional)
    bus, left_before = r.right_bus, list(r.left_bus.events)
    trial.prepare(r)
    assert not writes(bus, RIGHT_RESPONSE_ELBOW)
    original = json.loads(trial.snapshot_path.read_bytes())
    assert original["kind"] == "right-response-p" and original["motor"] == RIGHT_RESPONSE_ELBOW
    assert original["original_registers"]["P_Coefficient"] == 16 and original["restore_required"] is True
    assert original["original_registers"]["Homing_Offset"] == -658
    trial.disable_torque(r)
    trial.apply(r)
    configured = trial.snapshot_path.read_bytes()
    bus.values[("Torque_Enable", RIGHT_RESPONSE_ELBOW)] = 1
    # Active verification and receipts use only retained owner/device I/O; no private reads/writes powered.
    original_loader = module.load_right_response_p_binding
    monkeypatch.setattr(module, "load_right_response_p_binding", lambda *a, **k: pytest.fail("powered private read"))
    trial.verify_active(r)
    receipt = trial.active_receipt(r)
    assert receipt["kind"] == "right-response-p" and receipt["configured_p"] == proportional
    assert receipt["workload_segment"] == "right-peer-fixed" and receipt["original_p"] == 16
    assert set(receipt) == {"version", "kind", "run_id", "host_incarnation", "request_sha256", "source_head",
                            "request_id", "workload_segment", "original_p", "configured_p", "restore_required",
                            "original_settings_sha256", "prepared_at_monotonic_s", "configured_at_monotonic_s"}
    assert trial.snapshot_path.read_bytes() == configured
    monkeypatch.setattr(module, "load_right_response_p_binding", original_loader)
    r._am1_shutdown_in_progress = True
    trial.restore(r)
    assert bus.values[("P_Coefficient", RIGHT_RESPONSE_ELBOW)] == 16
    assert bus.values[("Torque_Enable", RIGHT_RESPONSE_ELBOW)] == 0
    assert bus.values[("Lock", RIGHT_RESPONSE_ELBOW)] == 0 and not trial.restore_required
    events = writes(bus, RIGHT_RESPONSE_ELBOW)
    assert events[-1][1:4] == ("Lock", RIGHT_RESPONSE_ELBOW, 0)
    assert all(row[1] in {"Torque_Enable", "Lock", "P_Coefficient"} for row in events)
    assert [row[3] for row in events if row[1] == "P_Coefficient"] == ([32, 16] if proportional == 32 else [])
    assert r.left_bus.events == left_before
    assert json.loads(trial.snapshot_path.read_bytes())["active_verified"] is True
    assert not request.exists() and (request.parent / ("right-response-p-used-" + value["request_id"] + ".json")).is_file()


@pytest.mark.parametrize("change", ["left_calibration", "wrong_bus", "run", "simultaneous_left"])
def test_right_response_p_side_identity_refuses_before_any_effect(tmp_path, monkeypatch, change):
    _module, r, trial, _directory, _request, _value = right_response_p_robot(tmp_path, monkeypatch)
    if change == "left_calibration":
        r.right_bus.calibration[RIGHT_RESPONSE_ELBOW] = MotorCalibration(3, 0, 1845, 864, 3073)
    elif change == "wrong_bus":
        r.right_bus = r.left_bus
    elif change == "run":
        r._am1_teleop_binding = dict(r._am1_teleop_binding, run_id="foreign")
    else:
        r._am1_left_response_p_binding = {"foreign": True}
    before = (list(r.left_bus.events), list(r.right_bus.events))
    with pytest.raises((RuntimeError, ValueError)):
        trial.prepare(r)
    assert (r.left_bus.events, r.right_bus.events) == before and not trial.restore_required


@pytest.mark.parametrize("field,value", [("kind", "left-response-p"), ("workload_segment", "left-peer-fixed"),
    ("workload_segment", "right-peer-maintained"), ("proportional", 64), ("proportional", True),
    ("source_head", "b" * 40), ("boot_id", "88888888-8888-4888-8888-888888888888"),
    ("expires_at_monotonic_s", 0.0), ("side", "right")])
def test_right_response_p_request_closed_side_source_and_deadline(tmp_path, monkeypatch, field, value):
    module, state, directory, left_path, original = left_response_p_files(tmp_path, monkeypatch)
    consumer = getattr(module, "consume_right_response_p_request", None)
    assert callable(consumer), "right response consumer missing"
    left_path.unlink()
    current = dict(original, kind="right-response-p", workload_segment="right-peer-fixed")
    current[field] = value
    path = state / "right-response-p-request.json"
    path.write_bytes(module._right_p20_json(current))
    path.chmod(0o600)
    with pytest.raises((ValueError, RuntimeError)):
        consumer(state, directory, "physical-live-teleop", RIGHT_P20_RUN, RIGHT_P20_HOST, "a" * 40)
    assert path.exists() and not list(directory.iterdir())
    assert not list(state.glob("right-response-p-used-*"))


def test_left_response_p_accepts_explicit_fixed_profile_without_changing_old_request_shape(tmp_path, monkeypatch):
    module, state, directory, request, original = left_response_p_files(tmp_path, monkeypatch)
    value = dict(original, workload_segment="left-peer-fixed")
    request.write_bytes(module._right_p20_json(value))
    path = module.consume_left_response_p_request(state, directory, "physical-live-teleop", RIGHT_P20_RUN, RIGHT_P20_HOST, "a" * 40)
    binding = module.load_left_response_p_binding(path, directory, RIGHT_P20_RUN, RIGHT_P20_HOST)
    assert binding["request"] == value and set(value) == set(original)


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_right_response_p_descriptor_loss_allows_off_without_forging_restoration(tmp_path, monkeypatch, damage):
    _module, r, trial, directory, _request, _value = right_response_p_robot(tmp_path, monkeypatch)
    trial.prepare(r)
    trial.disable_torque(r)
    trial.apply(r)
    r.right_bus.values[("Torque_Enable", RIGHT_RESPONSE_ELBOW)] = 1
    trial.verify_active(r)
    path = directory / "right-response-p.json"
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(b"corrupt")
    r._am1_shutdown_in_progress = True
    trial.disable_torque(r)
    with pytest.raises((OSError, ValueError)):
        trial.restore(r)
    assert r.right_bus.values[("Torque_Enable", RIGHT_RESPONSE_ELBOW)] == 0
    assert r.right_bus.values[("Lock", RIGHT_RESPONSE_ELBOW)] == 1
    assert r.right_bus.values[("P_Coefficient", RIGHT_RESPONSE_ELBOW)] == 32 and trial.restore_required
    assert json.loads(trial.snapshot_path.read_bytes())["restore_required"] is True


@pytest.mark.parametrize("fault", ["configure_ack", "restore_ack", "current", "status", "voltage", "cancel"])
def test_right_response_p_faults_remain_binding_and_sync_stop_restores(tmp_path, monkeypatch, fault):
    _module, r, trial, _directory, _request, _value = right_response_p_robot(tmp_path, monkeypatch)
    bus = r.right_bus
    trial.prepare(r)
    trial.disable_torque(r)
    if fault == "configure_ack":
        bus.fail_writes[("P_Coefficient", RIGHT_RESPONSE_ELBOW, 32)] = (OSError("lost configured ACK"), True)
        with pytest.raises(OSError):
            trial.apply(r)
    else:
        trial.apply(r)
        bus.values[("Torque_Enable", RIGHT_RESPONSE_ELBOW)] = 1
        if fault == "current":
            bus.values[("Present_Current", RIGHT_RESPONSE_ELBOW)] = 310
        elif fault == "status":
            bus.values[("Status", RIGHT_RESPONSE_ELBOW)] = 1
        elif fault == "voltage":
            bus.values[("Present_Voltage", RIGHT_RESPONSE_ELBOW)] = 39
        elif fault == "cancel":
            r._am1_shutdown_in_progress = True
        if fault in {"current", "status", "voltage", "cancel"}:
            with pytest.raises(RuntimeError):
                trial.verify_active(r)
        else:
            trial.verify_active(r)
            bus.fail_writes[("P_Coefficient", RIGHT_RESPONSE_ELBOW, 16)] = (OSError("lost restored ACK"), True)
    assert trial.restore_required
    r._am1_shutdown_in_progress = True
    trial.restore(r)
    assert bus.values[("P_Coefficient", RIGHT_RESPONSE_ELBOW)] == 16
    assert bus.values[("Lock", RIGHT_RESPONSE_ELBOW)] == 0
    assert bus.values[("Torque_Enable", RIGHT_RESPONSE_ELBOW)] == 0 and not trial.restore_required


@pytest.mark.parametrize("change", ["pid", "deadband", "startup_byte", "torque_limit", "firmware", "position"])
def test_right_response_p_unknown_stopped_baseline_is_proven_and_refused_without_write(tmp_path, monkeypatch, change):
    _module, r, trial, _directory, _request, _value = right_response_p_robot(tmp_path, monkeypatch)
    if change == "startup_byte":
        r.right_bus.raw[25] = 1
    else:
        register, value = {"pid": ("I_Coefficient", 1), "deadband": ("CW_Dead_Zone", 2),
            "torque_limit": ("Torque_Limit", 999), "firmware": ("Firmware_Minor_Version", 11),
            "position": ("Present_Position", 1961)}[change]
        r.right_bus.values[(register, RIGHT_RESPONSE_ELBOW)] = value
    with pytest.raises(RuntimeError):
        trial.prepare(r)
    assert not writes(r.right_bus, RIGHT_RESPONSE_ELBOW) and not writes(r.left_bus)
    saved = json.loads(trial.snapshot_path.read_bytes())
    assert saved["state"] == "baseline_refused" and saved["restore_required"] is False
    assert saved["original_registers"] and saved["original_bytes"]


def test_right_response_p_consumed_binding_outlives_admission_and_nonce_cannot_be_reissued(tmp_path, monkeypatch):
    module, r, trial, directory, request, value = right_response_p_robot(tmp_path, monkeypatch)
    monkeypatch.setattr(module.time, "monotonic", lambda: value["expires_at_monotonic_s"] + 1.)
    assert module.load_right_response_p_binding(directory / "right-response-p.json", directory,
                                                RIGHT_P20_RUN, RIGHT_P20_HOST) == trial.binding
    trial.prepare(r)
    trial.disable_torque(r)
    trial.apply(r)
    trial.restore(r)
    request.write_bytes(module._right_p20_json(dict(value, expires_at_monotonic_s=value["expires_at_monotonic_s"] + 100.)))
    request.chmod(0o600)
    with pytest.raises(ValueError, match="retired"):
        module.consume_right_response_p_request(request.parent, directory, "physical-live-teleop",
                                               RIGHT_P20_RUN, RIGHT_P20_HOST, "a" * 40)
    assert request.exists() and not trial.restore_required


@pytest.mark.parametrize("change", ["bus", "calibration", "run"])
def test_right_response_p_active_receipt_never_refreshes_changed_owner(tmp_path, monkeypatch, change):
    _module, r, trial, _directory, _request, _value = right_response_p_robot(tmp_path, monkeypatch)
    trial.prepare(r)
    trial.disable_torque(r)
    trial.apply(r)
    bus = r.right_bus
    bus.values[("Torque_Enable", RIGHT_RESPONSE_ELBOW)] = 1
    trial.verify_active(r)
    if change == "bus":
        r.right_bus = OwnerBus()
    elif change == "calibration":
        bus.calibration[RIGHT_RESPONSE_ELBOW].drive_mode = 1
    else:
        r._am1_teleop_binding = dict(r._am1_teleop_binding, host_incarnation="foreign")
    before = list(bus.events)
    with pytest.raises(RuntimeError):
        trial.active_receipt(r)
    assert bus.events == before and trial.restore_required


def test_right_response_p_uses_frozen_device_protection_and_restores_original_lock_one(tmp_path, monkeypatch):
    _module, r, trial, _directory, _request, _value = right_response_p_robot(tmp_path, monkeypatch)
    bus = r.right_bus
    bus.values[("Lock", RIGHT_RESPONSE_ELBOW)] = 1
    trial.prepare(r)
    trial.disable_torque(r)
    trial.apply(r)
    bus.values[("Torque_Enable", RIGHT_RESPONSE_ELBOW)] = 1
    bus.values[("Present_Current", RIGHT_RESPONSE_ELBOW)] = 100  # 650mA, below actual frozen310counts.
    bus.values[("Present_Temperature", RIGHT_RESPONSE_ELBOW)] = 95  # Single raw is not an extra thermal vote.
    trial.verify_active(r)
    assert trial.active_receipt(r)["configured_p"] == 32
    r._am1_shutdown_in_progress = True
    trial.restore(r)
    assert bus.values[("P_Coefficient", RIGHT_RESPONSE_ELBOW)] == 16
    assert bus.values[("Lock", RIGHT_RESPONSE_ELBOW)] == 1 and not trial.restore_required
    assert not any(row[1] == "Protection_Current" for row in writes(bus, RIGHT_RESPONSE_ELBOW))


CURRENT_REST_PROFILE = "left-current-rest-1051-1164"


def test_current_rest_request_is_exact_opt_in_and_consumed_once(tmp_path, monkeypatch):
    module, state, directory, request, original = left_response_p_files(
        tmp_path, monkeypatch, workspace_profile=CURRENT_REST_PROFILE
    )
    path = module.consume_left_response_p_request(state, directory, "physical-live-teleop", RIGHT_P20_RUN, RIGHT_P20_HOST, "a" * 40)
    binding = module.load_left_response_p_binding(path, directory, RIGHT_P20_RUN, RIGHT_P20_HOST)
    assert binding["request"] == original and binding["request"]["workspace_profile"] == CURRENT_REST_PROFILE
    assert not request.exists() and (state / ("left-response-p-used-" + original["request_id"] + ".json")).exists()
    trial = module.AM1LeftResponseProportionalTrial(binding, directory)
    assert trial.raw_bounds == (1051, 1164)


@pytest.mark.parametrize("bad", ["unknown", "null", "gain", "segment", "side", "extra", "version"])
def test_current_rest_request_cannot_broaden_other_role_or_experiment(tmp_path, monkeypatch, bad):
    module, state, directory, request, value = left_response_p_files(tmp_path, monkeypatch, workspace_profile=CURRENT_REST_PROFILE)
    if bad == "unknown":
        value["workspace_profile"] = "left-current-rest-1000-1200"
    elif bad == "null":
        value["workspace_profile"] = None
    elif bad == "gain":
        value["proportional"] = 16
    elif bad == "segment":
        value["workload_segment"] = "left-maintained"
    elif bad == "side":
        value.update(kind="right-response-p", workload_segment="right-peer-fixed")
    elif bad == "extra":
        value["raw_bounds"] = [1051, 1164]
    else:
        value["version"] = 2
    request.write_bytes(module._right_p20_json(value))
    raw = request.read_bytes()
    with pytest.raises(ValueError):
        module.consume_left_response_p_request(state, directory, "physical-live-teleop", RIGHT_P20_RUN, RIGHT_P20_HOST, "a" * 40)
    assert request.read_bytes() == raw and list(directory.iterdir()) == []


@pytest.mark.parametrize("workload_segment", ["left-reach", "ordinary-arms"])
def test_current_rest_original_goal_is_evidence_while_gain_restores_lock_last(tmp_path, monkeypatch, workload_segment):
    module, r, trial, directory, request, original = left_response_p_robot(tmp_path, monkeypatch,
        workload_segment=workload_segment,
        workspace_profile=("ordinary-arms-left-current-rest-1051-1164" if workload_segment == "ordinary-arms" else CURRENT_REST_PROFILE))
    trial.prepare(r)
    durable = json.loads(trial.snapshot_path.read_bytes())
    assert durable["restore_required"] is True and durable["original_registers"]["Goal_Position"] == 2050
    original_digest = durable["original_settings_sha256"]
    assert not writes(r.left_bus)
    trial.disable_torque(r)
    r.left_bus.values["Present_Position", ELBOW] = 1091
    observed = []
    def before_write(register, motor, value):
        if register in {"Goal_Position", "P_Coefficient"}:
            saved = json.loads(trial.snapshot_path.read_bytes())
            assert saved["restore_required"] and saved["original_registers"]["Goal_Position"] == 2050
            assert r.left_bus.values["Torque_Enable", motor] == 0 and r.left_bus.values["Lock", motor] == 1
            observed.append((register, value))
    r.left_bus.before_write = before_write
    trial.apply(r)
    assert r.left_bus.values["Goal_Position", ELBOW] == 2050 and r.left_bus.values["P_Coefficient", ELBOW] == 32
    assert "priming_position" not in trial.evidence and "primed_goal_readback" not in trial.evidence
    assert trial.receipt["original_settings_sha256"] == original_digest
    # Real ordinary activation is exercised in test_am1_pre_activation; this
    # transaction-only check preserves the owner's current volatile command.
    r.left_bus.values["Goal_Position", ELBOW] = 1091
    r.left_bus.values["Torque_Enable", ELBOW] = 1
    trial.restore(r)
    assert observed == [("P_Coefficient", 32), ("P_Coefficient", 16)]
    assert r.left_bus.values["Torque_Enable", ELBOW] == 0 and r.left_bus.values["Lock", ELBOW] == 0
    assert writes(r.left_bus)[-1][1:4] == ("Lock", ELBOW, 0)
    assert r.left_bus.values["Goal_Position", ELBOW] == 1091 and not trial.restore_required
    assert not writes(r.left_bus, OTHER) and not writes(r.right_bus)
    assert trial.evidence["original_registers"]["Present_Position"] == 1090


@pytest.mark.parametrize("fault", ["gain_lost_ack", "gain_cancel", "restore_lost_ack", "restore_failed", "stop", "current", "status", "torque"])
def test_current_rest_gain_failure_preserves_original_obligation_without_goal_writes(tmp_path, monkeypatch, fault):
    module, r, trial, directory, request, original = left_response_p_robot(tmp_path, monkeypatch, workspace_profile=CURRENT_REST_PROFILE)
    trial.prepare(r)
    trial.disable_torque(r)
    if fault in {"gain_lost_ack", "gain_cancel"}:
        error = KeyboardInterrupt("gain interrupted") if fault == "gain_cancel" else OSError("gain ACK lost")
        r.left_bus.fail_writes["P_Coefficient", ELBOW, 32] = (error, True)
    elif fault == "stop":
        r._am1_shutdown_in_progress = True
    elif fault == "current":
        r.left_bus.values["Present_Current", ELBOW] = 310
    elif fault == "status":
        r.left_bus.values["Status", ELBOW] = 1
    elif fault == "torque":
        r.left_bus.values["Torque_Enable", ELBOW] = 1
    if fault in {"gain_lost_ack", "gain_cancel", "stop", "current", "status", "torque"}:
        with pytest.raises((RuntimeError, OSError, KeyboardInterrupt)):
            trial.apply(r)
        assert trial.restore_required
        if fault not in {"gain_lost_ack", "gain_cancel"}:
            assert r.left_bus.values["P_Coefficient", ELBOW] == 16
        r.left_bus.values["Status", ELBOW] = 0
        r.left_bus.values["Present_Current", ELBOW] = 0
        r.left_bus.values["Torque_Enable", ELBOW] = 0
    else:
        trial.apply(r)
        assert r.left_bus.values["P_Coefficient", ELBOW] == 32
    assert not [row for row in writes(r.left_bus) if row[1] == "Goal_Position"]
    if fault in {"restore_lost_ack", "restore_failed"}:
        r.left_bus.fail_writes["P_Coefficient", ELBOW, 16] = (OSError("original P ACK lost"), fault != "restore_failed")
    if fault == "restore_failed":
        with pytest.raises(OSError):
            trial.restore(r)
        assert trial.restore_required and r.left_bus.values["Torque_Enable", ELBOW] == 0
        assert r.left_bus.values["Lock", ELBOW] == 1 and trial.evidence["state"] == "restore_failed"
    else:
        trial.restore(r)
        assert r.left_bus.values["Goal_Position", ELBOW] == 2050 and not trial.restore_required
    assert not any(row[1] == "Torque_Enable" and row[3] == 1 for row in writes(r.left_bus))


def test_current_rest_original_goal_must_still_be_inside_unchanged_calibration(tmp_path, monkeypatch):
    module, r, trial, directory, request, original = left_response_p_robot(tmp_path, monkeypatch, workspace_profile=CURRENT_REST_PROFILE)
    r.left_bus.values["Goal_Position", ELBOW] = 3074
    with pytest.raises(RuntimeError):
        trial.prepare(r)
    assert not trial.restore_required and not writes(r.left_bus)


@pytest.mark.parametrize("phase,value", [("prime_current_rest_goal", 1090), ("restore_original_goal", 2050), ("ordinary_goal", 1091)])
def test_current_rest_transaction_cannot_write_goal_in_any_phase(tmp_path, monkeypatch, phase, value):
    _, r, trial, _, _, _ = left_response_p_robot(tmp_path, monkeypatch, workspace_profile=CURRENT_REST_PROFILE)
    trial.prepare(r)
    trial.disable_torque(r)
    trial.apply(r)
    before = list(r.left_bus.events)
    with pytest.raises(ValueError):
        trial._write("Goal_Position", value, phase=phase, reconcile=True)
    assert r.left_bus.events == before and trial.restore_required
    trial.restore(r)
    assert r.left_bus.values["Torque_Enable", ELBOW] == 0 and r.left_bus.values["Goal_Position", ELBOW] == 2050
    assert r.left_bus.values["Lock", ELBOW] == 0 and not trial.restore_required


@pytest.mark.parametrize("changed", ["profile", "bounds"])
def test_current_rest_instance_cannot_escape_immutable_request_corridor(tmp_path, monkeypatch, changed):
    module, r, trial, directory, request, original = left_response_p_robot(tmp_path, monkeypatch, workspace_profile=CURRENT_REST_PROFILE)
    trial.prepare(r)
    trial.disable_torque(r)
    if changed == "profile":
        trial.workspace_profile = None
    else:
        trial.raw_bounds = (1000, 1200)
    with pytest.raises(RuntimeError):
        trial.apply(r)
    assert not [row for row in writes(r.left_bus) if row[1] in {"Goal_Position", "P_Coefficient"}]
    assert trial.restore_required and r.left_bus.values["Torque_Enable", ELBOW] == 0
    trial.workspace_profile, trial.raw_bounds = CURRENT_REST_PROFILE, (1051, 1164)
    trial.restore(r)
    assert not trial.restore_required and r.left_bus.values["Goal_Position", ELBOW] == 2050


def stopped_115_original(tmp_path, monkeypatch, *, workload_segment="left-reach"):
    """Retain the failed pre-P32 original; this is not a fresh apply opportunity."""
    import hashlib

    module, r, trial, directory, _, _ = left_response_p_robot(
        tmp_path, monkeypatch, workload_segment=workload_segment,
        workspace_profile=("ordinary-arms-left-current-rest-1051-1164" if workload_segment == "ordinary-arms" else CURRENT_REST_PROFILE),
    )
    trial.prepare(r)
    # Preserve the historical115 schema, before the selected Acceleration obligation existed.
    trial._snapshot.pop("selected_acceleration_profile", None)
    trial._snapshot.update(
        first_failure={"type": "RuntimeError", "message": "Current-rest Goal write lost actual Torque0"},
        priming_position={"operation": "read", "register": "Present_Position", "value": 1090,
                          "phase": "current_rest_prime", "started_at_monotonic_s": 1.0,
                          "completed_at_monotonic_s": 1.001},
    )
    for index, (operation, register, value, phase) in enumerate((
        ("write", "Goal_Position", 1090, "prime_current_rest_goal"),
        ("read", "Torque_Enable", 1, "prime_current_rest_goal_after"),
        ("write", "Torque_Enable", 0, "torque_off"),
        ("write", "Lock", 1, "volatile_lock"),
        ("write", "Goal_Position", 2050, "restore_original_goal"),
        ("read", "Torque_Enable", 1, "restore_original_goal_after"),
        ("write", "Torque_Enable", 0, "restore_failed_torque_off"),
        ("write", "Lock", 1, "restore_failed_lock"),
    )):
        trial._snapshot["events"].append({"operation": operation, "register": register, "value": value,
            "phase": phase, "started_at_monotonic_s": 2.0 + index, "completed_at_monotonic_s": 2.001 + index})
    trial._persist_snapshot("restore_failed")
    r.left_bus.values["Lock", ELBOW] = 1
    original = trial.snapshot_path.read_bytes()
    guard_calls = []
    kwargs = {"run_id": RIGHT_P20_RUN, "host_incarnation": RIGHT_P20_HOST,
              "original_sha256": hashlib.sha256(original).hexdigest(), "source_head": "a" * 40,
              "completion_id": "55555555-5555-4555-8555-555555555555",
              "check_stopped": lambda: guard_calls.append(True)}
    return module, r, trial, directory, original, kwargs, guard_calls


def test_ordinary_arms_stopped_completion_preserves_exact_failed_original_and_gain_identity(tmp_path, monkeypatch):
    module, r, trial, directory, original, kwargs, guard_calls = stopped_115_original(
        tmp_path, monkeypatch, workload_segment="ordinary-arms")
    result = module.complete_stopped_left_response_p_original(r.left_bus, directory, **kwargs)
    assert trial.snapshot_path.read_bytes() == original and trial.restore_required
    assert result["selected_restoration_verified"] is True and result["native_acknowledged"] is False
    assert result["original_sha256"] == kwargs["original_sha256"]
    assert r.left_bus.values["P_Coefficient", ELBOW] == 16 and r.left_bus.values["Torque_Enable", ELBOW] == 0
    assert all(row[1] != "Goal_Position" for row in writes(r.left_bus)) and guard_calls


@pytest.mark.parametrize("segment", ["left-maintained", "left-peer-fixed"])
def test_current_rest_explicit_null_profile_cannot_enable_unprofiled_workloads(tmp_path, monkeypatch, segment):
    module, _state, _directory, _request, value = left_response_p_files(tmp_path, monkeypatch)
    value.update(workload_segment=segment, workspace_profile=None)
    with pytest.raises(ValueError):
        module._left_response_p_request(value, "a" * 40)


@pytest.mark.parametrize("changed", ["legacy_profile", "missing_profile", "gain", "side", "unknown_workload"])
def test_ordinary_arms_request_cannot_borrow_other_profile_gain_or_role(tmp_path, monkeypatch, changed):
    module, state, directory, request, value = left_response_p_files(tmp_path, monkeypatch,
        workload_segment="ordinary-arms", workspace_profile="ordinary-arms-left-current-rest-1051-1164")
    if changed == "legacy_profile":
        value["workspace_profile"] = CURRENT_REST_PROFILE
    elif changed == "missing_profile":
        value.pop("workspace_profile")
    elif changed == "gain":
        value["proportional"] = 16
    elif changed == "side":
        value["kind"] = "right-response-p"
    else:
        value["workload_segment"] = "ordinary-body"
    request.write_bytes(module._right_p20_json(value))
    before = request.read_bytes()
    with pytest.raises(ValueError):
        module.consume_left_response_p_request(state, directory, "physical-live-teleop", RIGHT_P20_RUN, RIGHT_P20_HOST, "a" * 40)
    assert request.read_bytes() == before and not list(directory.iterdir())


@pytest.mark.parametrize("lock", [0, 1])
def test_stopped_115_completion_only_original_lock_and_never_rewrites_failed_original(tmp_path, monkeypatch, lock):
    module, r, trial, directory, original, kwargs, guard_calls = stopped_115_original(tmp_path, monkeypatch)
    r.left_bus.values["Lock", ELBOW] = lock
    result = module.complete_stopped_left_response_p_original(r.left_bus, directory, **kwargs)
    assert trial.snapshot_path.read_bytes() == original and trial.restore_required
    assert [(row[1], row[3]) for row in writes(r.left_bus)] == ([] if lock == 0 else [("Lock", 0)])
    assert r.left_bus.values["Torque_Enable", ELBOW] == 0 and r.left_bus.values["Goal_Position", ELBOW] == 2050
    assert result["selected_restoration_verified"] is True and result["native_acknowledged"] is False
    assert result["original_sha256"] == kwargs["original_sha256"] and result["source_head"] == "a" * 40
    target = directory / ("left-response-p-stopped-completion-" + kwargs["completion_id"] + ".json")
    assert target.read_bytes() == module._right_p20_json(result)
    assert len(guard_calls) >= 2 * len(result["events"])
    before = list(r.left_bus.events)
    with pytest.raises((ValueError, RuntimeError, FileExistsError)):
        module.complete_stopped_left_response_p_original(r.left_bus, directory, **kwargs)
    assert r.left_bus.events == before


@pytest.mark.parametrize("bad", ["hash", "source", "binding", "calibration", "P", "I", "D", "goal", "torque", "status", "current", "voltage", "byte", "guard", "configured"])
def test_stopped_115_completion_refuses_mismatch_before_any_write(tmp_path, monkeypatch, bad):
    import hashlib

    module, r, trial, directory, original, kwargs, _ = stopped_115_original(tmp_path, monkeypatch)
    if bad == "hash":
        kwargs["original_sha256"] = "0" * 64
    elif bad == "source":
        kwargs["source_head"] = "b" * 40
    elif bad == "binding":
        kwargs["host_incarnation"] = "66666666-6666-4666-8666-666666666666"
    elif bad == "calibration":
        r.left_bus.calibration[ELBOW].homing_offset += 1
    elif bad == "byte":
        r.left_bus.raw[25] = 1
    elif bad == "guard":
        def refused():
            raise RuntimeError("stopped ownership changed")
        kwargs["check_stopped"] = refused
    elif bad == "configured":
        trial._snapshot["configured_p_readback"] = 32
        trial._persist_snapshot("restore_failed")
        original = trial.snapshot_path.read_bytes()
        kwargs["original_sha256"] = hashlib.sha256(original).hexdigest()
    else:
        register, value = {"P": ("P_Coefficient", 32), "I": ("I_Coefficient", 1),
            "D": ("D_Coefficient", 33), "goal": ("Goal_Position", 1090), "torque": ("Torque_Enable", 1),
            "status": ("Status", 1), "current": ("Present_Current", 310), "voltage": ("Present_Voltage", 39)}[bad]
        r.left_bus.values[register, ELBOW] = value
    with pytest.raises((ValueError, RuntimeError)):
        module.complete_stopped_left_response_p_original(r.left_bus, directory, **kwargs)
    assert not writes(r.left_bus) and trial.snapshot_path.read_bytes() == original
    assert not list(directory.glob("left-response-p-stopped-completion-*.json"))


@pytest.mark.parametrize("failure", ["lost_ack", "not_applied", "ack_read", "cancel", "post_goal", "post_torque", "post_read", "foreign", "publication"])
def test_stopped_115_completion_ambiguity_never_clears_original_obligation(tmp_path, monkeypatch, failure):
    module, r, trial, directory, original, kwargs, _ = stopped_115_original(tmp_path, monkeypatch)
    if failure in {"lost_ack", "not_applied", "cancel"}:
        error = KeyboardInterrupt("Lock interrupted") if failure == "cancel" else OSError("Lock ACK lost")
        r.left_bus.fail_writes["Lock", ELBOW, 0] = (error, failure != "not_applied")
    elif failure in {"post_goal", "post_torque", "foreign"}:
        def changed(register, motor, value):
            if (register, value) == ("Lock", 0):
                if failure == "foreign":
                    r.left_bus.calibration[ELBOW].homing_offset += 1
                else:
                    r.left_bus.values["Goal_Position" if failure == "post_goal" else "Torque_Enable", ELBOW] = 1090 if failure == "post_goal" else 1
        r.left_bus.before_write = changed
    elif failure == "post_read":
        r.left_bus.read_sequences["Goal_Position", ELBOW] = [2050, OSError("final read unavailable")]
    elif failure == "ack_read":
        r.left_bus.fail_writes["Lock", ELBOW, 0] = (OSError("Lock ACK lost"), False)
        r.left_bus.read_sequences["Lock", ELBOW] = [1, OSError("Lock acknowledgement unavailable")]
    else:
        monkeypatch.setattr(module.os, "fsync", lambda _: (_ for _ in ()).throw(OSError("durability refused")))
    if failure == "lost_ack":
        result = module.complete_stopped_left_response_p_original(r.left_bus, directory, **kwargs)
        assert result["selected_restoration_verified"] is True
        assert any(row.get("reconciled") is True and row.get("error_type") == "OSError" for row in result["events"])
    else:
        with pytest.raises((RuntimeError, OSError, KeyboardInterrupt)):
            module.complete_stopped_left_response_p_original(r.left_bus, directory, **kwargs)
        assert not list(directory.glob("left-response-p-stopped-completion-*.json"))
    assert trial.restore_required and trial.snapshot_path.read_bytes() == original
    assert not any(row[1] not in {"Lock", "Torque_Enable"} or row[3] != 0 for row in writes(r.left_bus))
    if failure == "foreign":
        assert [(row[1], row[3]) for row in writes(r.left_bus)] == [("Lock", 0)]
    else:
        assert r.left_bus.values["Torque_Enable", ELBOW] == 0


def test_stopped_115_completion_accepts_retained_actual_original_bytes(tmp_path, monkeypatch):
    import hashlib
    from pathlib import Path

    retained = Path(__file__).resolve().parents[2] / ".cache/am1-teleop-consolidation-16/physical-runs/combined-115"
    if not (retained / "left-response-p-original.json").is_file():
        pytest.skip("Actual115 original is private operating evidence")
    module = importlib.import_module("lerobot.robots.alohamini.am1_elbow_integral")
    original = (retained / "left-response-p-original.json").read_bytes()
    assert hashlib.sha256(original).hexdigest() == "58f7a8483430ed81be740ac09ee45bc56b518004f59c46e17371b7ab99ef167a"
    value = json.loads(original)
    binding = value["binding"]
    directory = tmp_path / binding["run_id"][:8]
    directory.mkdir(mode=0o700)
    for name in ("left-response-p-original.json", "left-response-p.json", "left-response-p-consumed.json"):
        target = directory / name
        target.write_bytes((retained / name).read_bytes())
        target.chmod(0o600)
    bus = OwnerBus()
    bus.values.update({(key, ELBOW): item for key, item in value["original_registers"].items()})
    bus.values["Lock", ELBOW] = 1
    receipt = module.complete_stopped_left_response_p_original(bus, directory,
        run_id=binding["run_id"], host_incarnation=binding["host_incarnation"],
        original_sha256=hashlib.sha256(original).hexdigest(), source_head=binding["request"]["source_head"],
        completion_id="77777777-7777-4777-8777-777777777777", check_stopped=lambda: None)
    assert receipt["selected_restoration_verified"] is True
    assert [(row[1], row[3]) for row in writes(bus)] == [("Lock", 0)]
    assert (directory / "left-response-p-original.json").read_bytes() == original


def test_stopped_115_failed_publication_can_finish_with_already_original_lock(tmp_path, monkeypatch):
    module, r, trial, directory, original, kwargs, _ = stopped_115_original(tmp_path, monkeypatch)
    sync = module.os.fsync
    monkeypatch.setattr(module.os, "fsync", lambda _: (_ for _ in ()).throw(OSError("durability refused")))
    with pytest.raises(OSError):
        module.complete_stopped_left_response_p_original(r.left_bus, directory, **kwargs)
    assert r.left_bus.values["Lock", ELBOW] == 0 and trial.restore_required
    assert trial.snapshot_path.read_bytes() == original
    monkeypatch.setattr(module.os, "fsync", sync)
    r.left_bus.events.clear()
    kwargs["completion_id"] = "88888888-8888-4888-8888-888888888888"
    receipt = module.complete_stopped_left_response_p_original(r.left_bus, directory, **kwargs)
    assert not writes(r.left_bus) and receipt["selected_restoration_verified"] is True
    assert trial.snapshot_path.read_bytes() == original and trial.restore_required


@pytest.mark.parametrize("failure", ["read", "byte", "guard"])
def test_stopped_115_prewrite_fault_preserves_completed_and_failed_actual_rows(tmp_path, monkeypatch, failure):
    module, r, trial, directory, original, kwargs, _ = stopped_115_original(tmp_path, monkeypatch)
    r.left_bus.events.clear()
    if failure == "read":
        r.left_bus.read_sequences["Operating_Mode", ELBOW] = [OSError("initial SDK read failed")]
    elif failure == "byte":
        read = r.left_bus._read
        def failed_byte(address, *args, **named):
            if address == 25:
                raise OSError("individual-byte read failed")
            return read(address, *args, **named)
        r.left_bus._read = failed_byte
    else:
        def changed_guard():
            if len([row for row in r.left_bus.events if row[0] == "read"]) >= 3:
                raise RuntimeError("stopped guard changed after three reads")
        kwargs["check_stopped"] = changed_guard
    with pytest.raises((OSError, RuntimeError)) as caught:
        module.complete_stopped_left_response_p_original(r.left_bus, directory, **kwargs)
    prefix = "Stopped completion original retained; actual events: "
    notes = [note for note in getattr(caught.value, "__notes__", ()) if note.startswith(prefix)]
    assert len(notes) == 1
    rows = json.loads(notes[0][len(prefix):])
    assert len(rows) >= 3 and all(row["operation"] == "read" for row in rows)
    assert rows[-1]["error_type"] == ("RuntimeError" if failure == "guard" else "OSError")
    assert rows[-1]["completed_at_monotonic_s"] >= rows[-1]["started_at_monotonic_s"]
    assert all("value" in row and "error_type" not in row for row in rows[:-1])
    if failure == "read":
        assert rows[-1]["register"] == "Operating_Mode" and "value" not in rows[-1]
    elif failure == "byte":
        assert rows[-1]["address"] == 25 and "value" not in rows[-1]
    assert not writes(r.left_bus) and trial.snapshot_path.read_bytes() == original
    assert not list(directory.glob("left-response-p-stopped-completion-*.json"))


def test_current_rest_normal_activation_transaction_never_writes_goal_even_when_goal_can_enable(tmp_path, monkeypatch):
    _, r, trial, _, _, _ = left_response_p_robot(tmp_path, monkeypatch, workspace_profile=CURRENT_REST_PROFILE)
    original_write = r.left_bus.write
    def observed_goal_transition(register, motor, value, **kwargs):
        result = original_write(register, motor, value, **kwargs)
        if register == "Goal_Position":
            r.left_bus.values["Torque_Enable", motor] = 1
        return result
    r.left_bus.write = observed_goal_transition
    trial.prepare(r)
    trial.disable_torque(r)
    trial.apply(r)
    assert r.left_bus.values["P_Coefficient", ELBOW] == 32
    assert r.left_bus.values["Torque_Enable", ELBOW] == 0
    assert r.left_bus.values["Goal_Position", ELBOW] == 2050
    assert trial.evidence["original_registers"]["Goal_Position"] == 2050
    assert not [row for row in writes(r.left_bus) if row[1] == "Goal_Position"]
    # Ordinary activation/task owns this volatile Goal; the P transaction must
    # never replay the stale original command at shutdown.
    r.left_bus.values["Goal_Position", ELBOW] = 1091
    r.left_bus.values["Torque_Enable", ELBOW] = 1
    trial.restore(r)
    assert r.left_bus.values["Goal_Position", ELBOW] == 1091
    assert r.left_bus.values["Torque_Enable", ELBOW] == 0
    assert r.left_bus.values["P_Coefficient", ELBOW] == 16
    assert writes(r.left_bus)[-1][1:4] == ("Lock", ELBOW, 0)
    assert not trial.restore_required and not [row for row in writes(r.left_bus) if row[1] == "Goal_Position"]


@pytest.mark.parametrize("failure", ["gain_ack", "cancel", "stop"])
def test_current_rest_normal_activation_p_failure_retains_original_without_any_goal_write(tmp_path, monkeypatch, failure):
    _, r, trial, _, _, _ = left_response_p_robot(tmp_path, monkeypatch, workspace_profile=CURRENT_REST_PROFILE)
    trial.prepare(r)
    trial.disable_torque(r)
    if failure == "stop":
        r._am1_shutdown_in_progress = True
    else:
        error = KeyboardInterrupt("P configuration cancelled") if failure == "cancel" else OSError("P ACK lost")
        r.left_bus.fail_writes["P_Coefficient", ELBOW, 32] = (error, failure == "cancel")
    with pytest.raises((RuntimeError, OSError, KeyboardInterrupt)):
        trial.apply(r)
    assert trial.restore_required and trial.evidence["original_registers"]["Goal_Position"] == 2050
    assert not [row for row in writes(r.left_bus) if row[1] == "Goal_Position"]
    trial.restore(r)
    assert r.left_bus.values["P_Coefficient", ELBOW] == 16 and r.left_bus.values["Lock", ELBOW] == 0
    assert r.left_bus.values["Torque_Enable", ELBOW] == 0
    assert r.left_bus.values["Goal_Position", ELBOW] == 2050 and not trial.restore_required


@pytest.mark.parametrize("degrees", [1, 1.0])
def test_left_shoulder_startup_request_is_explicit_one_use_and_retained(tmp_path, monkeypatch, degrees):
    module, r, trial, directory, request, value = left_response_p_robot(
        tmp_path, monkeypatch, workspace_profile=CURRENT_REST_PROFILE,
        left_shoulder_startup_degrees=degrees,
    )
    assert not request.exists()
    assert trial.binding["request"]["left_shoulder_startup_degrees"] == 1
    assert module.load_left_response_p_binding(
        directory / "left-response-p.json", directory, RIGHT_P20_RUN, RIGHT_P20_HOST
    )["request"] == value
    with pytest.raises(RuntimeError):
        trial.normal_rest_startup_degrees(r)
    trial.prepare(r)
    trial.disable_torque(r)
    trial.apply(r)
    boundary = list(r.left_bus.events)
    assert trial.normal_rest_startup_degrees(r) == 1
    assert r.left_bus.events == boundary, "startup authority fence must perform no device I/O"
    request.write_bytes(module._right_p20_json(value))
    request.chmod(0o600)
    with pytest.raises(ValueError, match="retired"):
        module.consume_left_response_p_request(
            request.parent, directory, "physical-live-teleop", RIGHT_P20_RUN, RIGHT_P20_HOST, "a" * 40
        )


@pytest.mark.parametrize("degrees", [None, True, False, 0, .5, 1.000001, 2, "1", {}, float("nan"), float("inf"), -float("inf")])
def test_left_shoulder_startup_request_rejects_nonexact_values(tmp_path, monkeypatch, degrees):
    module, _, directory, request, value = left_response_p_files(
        tmp_path, monkeypatch, workspace_profile=CURRENT_REST_PROFILE
    )
    original = request.read_bytes()
    value["left_shoulder_startup_degrees"] = degrees
    with pytest.raises(ValueError):
        module._left_response_p_request(value, "a" * 40)
    assert request.read_bytes() == original and not list(directory.iterdir())


@pytest.mark.parametrize("bad", ["profile_absent", "profile_other", "gain", "segment", "side", "source"])
def test_left_shoulder_startup_request_cannot_broaden_other_authority(tmp_path, monkeypatch, bad):
    module, state, directory, request, value = left_response_p_files(
        tmp_path, monkeypatch, workspace_profile=CURRENT_REST_PROFILE,
        left_shoulder_startup_degrees=1,
    )
    if bad == "profile_absent":
        del value["workspace_profile"]
    elif bad == "profile_other":
        value["workspace_profile"] = "left-current-rest-1000-1200"
    elif bad == "gain":
        value["proportional"] = 16
    elif bad == "segment":
        value["workload_segment"] = "left-maintained"
    elif bad == "side":
        value.update(kind="right-response-p", workload_segment="right-peer-fixed")
    else:
        value["source_head"] = "b" * 40
    request.write_bytes(module._right_p20_json(value))
    original = request.read_bytes()
    with pytest.raises(ValueError):
        module.consume_left_response_p_request(
            state, directory, "physical-live-teleop", RIGHT_P20_RUN, RIGHT_P20_HOST, "a" * 40
        )
    assert request.read_bytes() == original and not list(directory.iterdir())


def test_left_shoulder_startup_default_stays_half_degree_for_existing_current_rest(tmp_path, monkeypatch):
    _, r, trial, _, _, _ = left_response_p_robot(
        tmp_path, monkeypatch, workspace_profile=CURRENT_REST_PROFILE
    )
    trial.prepare(r)
    trial.disable_torque(r)
    trial.apply(r)
    assert "left_shoulder_startup_degrees" not in trial.binding["request"]
    assert trial.normal_rest_startup_degrees(r) == .5


@pytest.mark.parametrize("changed", ["request", "request_removed", "robot_binding", "profile", "stop", "restored"])
def test_left_shoulder_startup_authority_cannot_survive_mutation_or_stop(tmp_path, monkeypatch, changed):
    _, r, trial, _, _, _ = left_response_p_robot(
        tmp_path, monkeypatch, workspace_profile=CURRENT_REST_PROFILE,
        left_shoulder_startup_degrees=1,
    )
    trial.prepare(r)
    trial.disable_torque(r)
    trial.apply(r)
    assert trial.normal_rest_startup_degrees(r) == 1
    if changed == "request":
        trial.binding["request"]["left_shoulder_startup_degrees"] = 2
    elif changed == "request_removed":
        del trial.binding["request"]["left_shoulder_startup_degrees"]
    elif changed == "robot_binding":
        import copy
        r._am1_left_response_p_binding = copy.deepcopy(trial.binding)
        del r._am1_left_response_p_binding["request"]["left_shoulder_startup_degrees"]
    elif changed == "profile":
        trial.workspace_profile = None
    elif changed == "stop":
        r._am1_shutdown_in_progress = True
    else:
        trial.restore(r)
    boundary = list(r.left_bus.events)
    with pytest.raises(RuntimeError):
        trial.normal_rest_startup_degrees(r)
    assert r.left_bus.events == boundary


LEFT_ENTRY_PROFILE = "ordinary-arms-left-entry-p16-1108-1268"


@pytest.mark.parametrize("raw", [1165, 1204, 1268])
def test_left_entry_p16_transaction_admits_fresh_stopped_origin_without_gain_or_goal_write(tmp_path, monkeypatch, raw):
    _, r, trial, _, _, request = left_response_p_robot(
        tmp_path, monkeypatch, proportional=16, workspace_profile=LEFT_ENTRY_PROFILE,
        workload_segment="ordinary-arms", left_shoulder_startup_degrees=1,
    )
    r.left_bus.values["Present_Position", ELBOW] = raw
    r.left_bus.values["Goal_Position", ELBOW] = 1140
    trial.prepare(r)
    trial.disable_torque(r)
    trial.apply(r)
    assert trial.raw_bounds == (1108, 1268)
    assert trial.receipt["configured_p"] == 16 and trial.normal_rest_startup_degrees(r) == 1
    assert not [event for event in writes(r.left_bus) if event[1] in {"P_Coefficient", "Goal_Position"}]
    r._am1_shutdown_in_progress = True
    trial.restore(r)
    assert not trial.restore_required and r.left_bus.values["P_Coefficient", ELBOW] == 16
    assert r.left_bus.values["Goal_Position", ELBOW] == 1140 and not writes(r.right_bus)


@pytest.mark.parametrize("bad", ["inside", "outside", "gain", "status", "current", "torque", "stop"])
def test_left_entry_p16_refuses_other_gains_unbounded_rest_and_unhealthy_owner(tmp_path, monkeypatch, bad):
    module, r, trial, _, _, _ = left_response_p_robot(
        tmp_path, monkeypatch, proportional=16, workspace_profile=LEFT_ENTRY_PROFILE,
        workload_segment="ordinary-arms",
    )
    r.left_bus.values["Present_Position", ELBOW] = 1204
    if bad == "inside": r.left_bus.values["Present_Position", ELBOW] = 1164
    elif bad == "outside": r.left_bus.values["Present_Position", ELBOW] = 1269
    elif bad == "gain": r.left_bus.values["P_Coefficient", ELBOW] = 32
    elif bad == "status": r.left_bus.values["Status", ELBOW] = 1
    elif bad == "current": r.left_bus.values["Present_Current", ELBOW] = 310
    elif bad == "torque": r.left_bus.values["Torque_Enable", ELBOW] = 1
    else: r._am1_shutdown_in_progress = True
    with pytest.raises(RuntimeError): trial.prepare(r)
    assert not trial.restore_required and not writes(r.left_bus)


@pytest.mark.parametrize("changed", ["p32", "side", "workload", "unknown", "allowance", "legacy"])
def test_left_entry_request_cannot_borrow_p32_or_another_role(tmp_path, monkeypatch, changed):
    module, _, _, _, value = left_response_p_files(
        tmp_path, monkeypatch, proportional=16, workspace_profile=LEFT_ENTRY_PROFILE,
        workload_segment="ordinary-arms",
    )
    if changed == "p32": value["proportional"] = 32
    elif changed == "side": value["kind"] = "right-response-p"
    elif changed == "workload": value["workload_segment"] = "left-reach"
    elif changed == "unknown": value["workspace_profile"] += "-other"
    elif changed == "legacy": value["workspace_profile"] = "ordinary-arms-left-entry-p16-1164-1324"
    else: value["left_shoulder_startup_degrees"] = 2
    with pytest.raises(ValueError): module._left_response_p_request(value, "a" * 40)



def test_selected_acceleration_actual963_snapshot_is_durable_before_configuration_and_restored(tmp_path,monkeypatch):
    from pathlib import Path
    module,r,trial,_,_,_=left_response_p_robot(tmp_path,monkeypatch,workload_segment="ordinary-arms",
        workspace_profile="ordinary-arms-left-current-rest-1051-1164",left_shoulder_startup_degrees=1)
    original_run=json.loads((Path(__file__).resolve().parents[2]/".cache/am1-teleop-consolidation-16/physical-runs/am1-virtual-963c01dd-4824-4f6b-ae51-2266312c1c9e/cleanup.json").read_bytes())
    actual=original_run["readback"]["left_response_p_trial"]["original_registers"]
    assert actual["Acceleration"]==actual["Goal_Position"]==0 and actual["Present_Position"]==1142
    r.left_bus.values.update({(k,ELBOW):v for k,v in actual.items()})
    from lerobot.robots.alohamini.am1_live_teleop import STATIC
    r._am1_teleop_settings={"left":{"registers":{k:{ELBOW:actual[k]} for k in STATIC}}}
    trial.prepare(r)
    saved=json.loads(trial.snapshot_path.read_bytes())
    assert saved["restore_required"] and saved["original_registers"]==actual and not writes(r.left_bus)
    trial.disable_torque(r)
    def before_write(register,motor,value):
        if register in ("Acceleration","P_Coefficient"):
            durable=json.loads(trial.snapshot_path.read_bytes())
            assert durable["restore_required"] and durable["original_registers"]["Acceleration"]==0
            assert r.left_bus.values["Torque_Enable",ELBOW]==0 and r.left_bus.values["Lock",ELBOW]==1
    r.left_bus.before_write=before_write
    trial.apply(r)
    assert r.left_bus.values["Acceleration",ELBOW]==254 and r.left_bus.values["P_Coefficient",ELBOW]==32
    assert r.left_bus.values["Goal_Position",ELBOW]==0
    r.left_bus.values["Goal_Position",ELBOW]=1142  # The ordinary owner later seeds q0; this transaction never writes Goal.
    trial.restore(r)
    assert (r.left_bus.values["Acceleration",ELBOW],r.left_bus.values["P_Coefficient",ELBOW],r.left_bus.values["Lock",ELBOW])==(0,16,0)
    assert r.left_bus.values["Goal_Position",ELBOW]==1142 and not trial.restore_required
    assert all(row[1] in {"Torque_Enable","Lock","P_Coefficient","Acceleration"} for row in writes(r.left_bus))
    assert not writes(r.left_bus,OTHER) and not writes(r.right_bus)



def selected_acceleration_trial(tmp_path,monkeypatch,*,entry=False,acceleration=0,goal=0):
    from lerobot.robots.alohamini.am1_live_teleop import STATIC
    from examples.alohamini.am1_live_teleop import LEFT_ENTRY_WORKSPACE_PROFILE
    module,r,trial,directory,request,value=left_response_p_robot(tmp_path,monkeypatch,proportional=16 if entry else 32,
        workload_segment="ordinary-arms",workspace_profile=LEFT_ENTRY_WORKSPACE_PROFILE if entry else "ordinary-arms-left-current-rest-1051-1164",left_shoulder_startup_degrees=1)
    r.left_bus.values["Acceleration",ELBOW]=acceleration;r.left_bus.values["Goal_Position",ELBOW]=goal
    r.left_bus.values["Present_Position",ELBOW]=1204 if entry else 1142
    r._am1_teleop_settings={"left":{"registers":{k:{ELBOW:r.left_bus.values[k,ELBOW]} for k in STATIC}}}
    return module,r,trial,directory


@pytest.mark.parametrize("entry",[False,True])
def test_selected_acceleration_interrupted_profile_uses_existing_stopped_completion_without_goal(tmp_path,monkeypatch,entry):
    import hashlib
    from uuid import uuid4
    module,r,trial,directory=selected_acceleration_trial(tmp_path,monkeypatch,entry=entry)
    trial.prepare(r);trial.disable_torque(r);trial.apply(r)
    raw=trial.snapshot_path.read_bytes();before=list(r.left_bus.events);checks=[]
    def stopped():checks.append(True)
    result=module.complete_stopped_left_response_p_original(r.left_bus,directory,run_id=trial.binding["run_id"],
        host_incarnation=trial.binding["host_incarnation"],source_head=trial.binding["request"]["source_head"],
        original_sha256=hashlib.sha256(raw).hexdigest(),completion_id=str(uuid4()),check_stopped=stopped)
    assert checks and result["selected_restoration_verified"] and result["native_acknowledged"] is False
    assert result["original_acceleration"]==result["restored_acceleration_readback"]==0
    assert (r.left_bus.values["P_Coefficient",ELBOW],r.left_bus.values["Acceleration",ELBOW],r.left_bus.values["Torque_Enable",ELBOW],r.left_bus.values["Lock",ELBOW])==(16,0,0,0)
    assert trial.snapshot_path.read_bytes()==raw and json.loads(raw)["restore_required"]
    added=[row for row in r.left_bus.events[len(before):] if row[0]=="write"]
    assert all(row[1] in {"P_Coefficient","Acceleration","Lock"} for row in added)
    assert not any(row[1]=="Goal_Position" for row in writes(r.left_bus))


@pytest.mark.parametrize("guard",["torque","lock","maximum","status","current"])
def test_selected_acceleration_configuration_requires_fresh_off_health_before_write(tmp_path,monkeypatch,guard):
    _,r,trial,_=selected_acceleration_trial(tmp_path,monkeypatch)
    trial.prepare(r);trial.disable_torque(r)
    register,value={"torque":("Torque_Enable",1),"lock":("Lock",0),"maximum":("Maximum_Acceleration",100),"status":("Status",1),"current":("Present_Current",310)}[guard]
    r.left_bus.values[register,ELBOW]=value
    with pytest.raises(RuntimeError):trial._write("Acceleration",254,phase="configure_acceleration")
    assert trial.restore_required and r.left_bus.values["Acceleration",ELBOW]==0
    assert not [row for row in writes(r.left_bus) if row[1] in {"Acceleration","Goal_Position"}]


@pytest.mark.parametrize("entry,acceleration,goal",[(False,0,0),(False,254,32767),(True,0,0),(True,254,32767)])
def test_selected_acceleration_keeps_tested_active_role_and_exact_original(tmp_path,monkeypatch,entry,acceleration,goal):
    _,r,trial,_=selected_acceleration_trial(tmp_path,monkeypatch,entry=entry,acceleration=acceleration,goal=goal)
    trial.prepare(r);trial.disable_torque(r);trial.apply(r)
    assert trial.configured_acceleration(r)==254
    trial._preserved(r,phase="active")
    r.left_bus.values["Acceleration",ELBOW]=73
    with pytest.raises(RuntimeError):trial._preserved(r,phase="active")
    r.left_bus.values["Acceleration",ELBOW]=254
    trial.restore(r)
    assert r.left_bus.values["Acceleration",ELBOW]==acceleration and r.left_bus.values["Goal_Position",ELBOW]==goal
    assert not trial.restore_required and not any(row[1]=="Goal_Position" for row in writes(r.left_bus))


@pytest.mark.parametrize("fault",["configure_lost_ack","configure_cancel","configure_readback","restore_lost_ack","restore_failed","restore_readback"])
def test_selected_acceleration_write_or_readback_failure_keeps_durable_original_and_off(tmp_path,monkeypatch,fault):
    _,r,trial,_=selected_acceleration_trial(tmp_path,monkeypatch)
    trial.prepare(r);trial.disable_torque(r)
    if fault.startswith("configure"):
        if fault=="configure_readback":
            r.left_bus.before_write=lambda reg,name,value:r.left_bus.read_sequences.__setitem__((reg,name),[0]) if reg=="Acceleration" else None
        else:r.left_bus.fail_writes["Acceleration",ELBOW,254]=(KeyboardInterrupt("interrupted") if fault=="configure_cancel" else OSError("ACK lost"),True)
        with pytest.raises((RuntimeError,OSError,KeyboardInterrupt)):trial.apply(r)
        assert trial.restore_required and json.loads(trial.snapshot_path.read_bytes())["restore_required"]
        r.left_bus.before_write=None
    else:
        trial.apply(r)
        if fault=="restore_readback":
            r.left_bus.before_write=lambda reg,name,value:r.left_bus.read_sequences.__setitem__((reg,name),[254]) if reg=="Acceleration" and value==0 else None
        else:r.left_bus.fail_writes["Acceleration",ELBOW,0]=(OSError("restore ACK lost"),fault=="restore_lost_ack")
    if fault in {"restore_failed","restore_readback"}:
        with pytest.raises((RuntimeError,OSError)):trial.restore(r)
        assert trial.restore_required and r.left_bus.values["Torque_Enable",ELBOW]==0 and r.left_bus.values["Lock",ELBOW]==1
        assert json.loads(trial.snapshot_path.read_bytes())["restore_required"]
    else:
        trial.restore(r)
        assert not trial.restore_required and r.left_bus.values["Acceleration",ELBOW]==0
    assert not any(row[1]=="Goal_Position" for row in writes(r.left_bus))


@pytest.mark.parametrize("fault",["arbitrary","maximum","outside","legacy"])
def test_selected_acceleration_does_not_authorize_another_profile_or_baseline(tmp_path,monkeypatch,fault):
    if fault=="legacy":
        _,r,trial,_,_,_=left_response_p_robot(tmp_path,monkeypatch,workspace_profile=CURRENT_REST_PROFILE)
        r.left_bus.values["Acceleration",ELBOW]=0
        r._am1_teleop_settings["left"]["registers"]["Acceleration"][ELBOW]=0
    else:
        _,r,trial,_=selected_acceleration_trial(tmp_path,monkeypatch)
        reg,val={"arbitrary":("Acceleration",73),"maximum":("Maximum_Acceleration",100),"outside":("Present_Position",1165)}[fault]
        r.left_bus.values[reg,ELBOW]=val
        if reg in r._am1_teleop_settings["left"]["registers"]:r._am1_teleop_settings["left"]["registers"][reg][ELBOW]=val
    with pytest.raises(RuntimeError):trial.prepare(r)
    assert not writes(r.left_bus) and not trial.restore_required


def test_selected_acceleration_cannot_apply_after_original_persistence_failure(tmp_path,monkeypatch):
    _,r,trial,_=selected_acceleration_trial(tmp_path,monkeypatch)
    def failed(*a,**k):raise OSError("original did not reach durable storage")
    monkeypatch.setattr(trial,"_persist_snapshot",failed)
    with pytest.raises(OSError):trial.prepare(r)
    r.left_bus.values["Lock",ELBOW]=1
    with pytest.raises(RuntimeError):trial.apply(r)
    assert not writes(r.left_bus)


def stopped_selected_acceleration(module, r, trial, directory, *, check_stopped=lambda: None, completion_id=None, original_sha256=None, source_head=None):
    import hashlib
    from uuid import uuid4
    return module.complete_stopped_left_response_p_original(
        r.left_bus, directory, run_id=trial.binding["run_id"],
        host_incarnation=trial.binding["host_incarnation"],
        source_head=source_head or trial.binding["request"]["source_head"],
        original_sha256=original_sha256 or hashlib.sha256(trial.snapshot_path.read_bytes()).hexdigest(),
        completion_id=completion_id or str(uuid4()), check_stopped=check_stopped,
    )


@pytest.mark.parametrize("changed", ["source", "boot", "original_sha", "profile", "snapshot_acceleration", "snapshot_static", "snapshot_event", "torque", "status", "current", "maximum", "static", "actual_acceleration", "lock", "stopped"])
def test_selected_acceleration_interrupted_completion_refuses_changed_authority_or_hardware(tmp_path, monkeypatch, changed):
    module,r,trial,directory=selected_acceleration_trial(tmp_path,monkeypatch)
    trial.prepare(r);trial.disable_torque(r);trial.apply(r)
    options={}
    if changed=="source": options["source_head"]="b"*40
    elif changed=="boot": monkeypatch.setattr(module,"_left_response_p_boot_id",lambda:"88888888-8888-4888-8888-888888888888")
    elif changed=="original_sha": options["original_sha256"]="0"*64
    elif changed.startswith("snapshot") or changed=="profile":
        saved=json.loads(trial.snapshot_path.read_bytes())
        if changed=="profile": saved["selected_acceleration_profile"]["active_raw"]=73
        elif changed=="snapshot_acceleration": saved["original_registers"]["Acceleration"]=73
        elif changed=="snapshot_static": saved["frozen_teleop_registers"]["Acceleration"]=254
        else: saved["events"].append({"operation":"write","register":"Goal_Position","value":1142})
        trial.snapshot_path.write_bytes((json.dumps(saved,sort_keys=True,indent=2,allow_nan=False)+"\n").encode())
    elif changed=="stopped":
        def lost(): raise RuntimeError("stopped maintenance authority lost")
        options["check_stopped"]=lost
    else:
        register,value={"torque":("Torque_Enable",1),"status":("Status",1),"current":("Present_Current",310),
                        "maximum":("Maximum_Acceleration",100),"static":("D_Coefficient",31),
                        "actual_acceleration":("Acceleration",73),"lock":("Lock",0)}[changed]
        r.left_bus.values[register,ELBOW]=value
    raw=trial.snapshot_path.read_bytes();before=list(writes(r.left_bus))
    with pytest.raises((RuntimeError,ValueError)):
        stopped_selected_acceleration(module,r,trial,directory,**options)
    assert writes(r.left_bus)==before
    assert trial.snapshot_path.read_bytes()==raw and json.loads(raw)["restore_required"]
    assert not list(directory.glob("left-response-p-stopped-completion-*.json"))


@pytest.mark.parametrize("fault",["acceleration_lost_ack", "acceleration_failed", "acceleration_readback", "lock_failed", "publication_sync", "cancel_after_write"])
def test_selected_acceleration_interrupted_completion_retains_original_on_failure(tmp_path,monkeypatch,fault):
    module,r,trial,directory=selected_acceleration_trial(tmp_path,monkeypatch)
    trial.prepare(r);trial.disable_torque(r);trial.apply(r)
    raw=trial.snapshot_path.read_bytes();before=len(writes(r.left_bus))
    if fault=="acceleration_lost_ack": r.left_bus.fail_writes["Acceleration",ELBOW,0]=(OSError("ACK lost"),True)
    elif fault=="acceleration_failed": r.left_bus.fail_writes["Acceleration",ELBOW,0]=(OSError("restore failed"),False)
    elif fault=="acceleration_readback":
        r.left_bus.before_write=lambda reg,name,value:r.left_bus.read_sequences.__setitem__((reg,name),[254]) if reg=="Acceleration" else None
    elif fault=="lock_failed": r.left_bus.fail_writes["Lock",ELBOW,0]=(OSError("Lock restore failed"),False)
    elif fault=="publication_sync":
        def failed(_): raise OSError("completion sync failed")
        monkeypatch.setattr(module.AM1ElbowIntegralTrial,"_sync_directory",failed)
    else: r.left_bus.fail_writes["Acceleration",ELBOW,0]=(KeyboardInterrupt("stopped completion interrupted"),True)
    if fault=="acceleration_lost_ack":
        result=stopped_selected_acceleration(module,r,trial,directory)
        assert result["selected_restoration_verified"] and any(row.get("reconciled") for row in result["events"])
    else:
        with pytest.raises((RuntimeError,OSError,KeyboardInterrupt)):
            stopped_selected_acceleration(module,r,trial,directory)
        assert not list(directory.glob("left-response-p-stopped-completion-*.json"))
        assert r.left_bus.values["Torque_Enable",ELBOW]==0
    assert trial.snapshot_path.read_bytes()==raw and json.loads(raw)["restore_required"]
    assert all(row[1] in {"P_Coefficient","Acceleration","Lock","Torque_Enable"} for row in writes(r.left_bus)[before:])
    assert not any(row[1]=="Goal_Position" for row in writes(r.left_bus))


def test_selected_acceleration_interrupted_completion_has_separate_expired_motion_authority_and_exclusive_receipt(tmp_path,monkeypatch):
    from uuid import uuid4
    module,r,trial,directory=selected_acceleration_trial(tmp_path,monkeypatch)
    trial.prepare(r);trial.disable_torque(r);trial.apply(r)
    raw=trial.snapshot_path.read_bytes()
    monkeypatch.setattr(module.time,"monotonic",lambda:trial.binding["request"]["expires_at_monotonic_s"]+1.)
    identity=str(uuid4())
    result=stopped_selected_acceleration(module,r,trial,directory,completion_id=identity)
    assert result["selected_restoration_verified"] and result["native_acknowledged"] is False
    target=directory/("left-response-p-stopped-completion-"+identity+".json")
    receipt=target.read_bytes();before=list(writes(r.left_bus))
    with pytest.raises(ValueError,match="fresh"):
        stopped_selected_acceleration(module,r,trial,directory,completion_id=identity)
    assert target.read_bytes()==receipt and trial.snapshot_path.read_bytes()==raw and writes(r.left_bus)==before


@pytest.mark.parametrize("changed", ["deleted", "replaced", "preparation_failed"])
def test_selected_acceleration_cannot_configure_from_changed_or_failed_durable_original(tmp_path,monkeypatch,changed):
    _,r,trial,_=selected_acceleration_trial(tmp_path,monkeypatch)
    trial.prepare(r);trial.disable_torque(r)
    if changed=="deleted": trial.snapshot_path.unlink()
    elif changed=="replaced": trial.snapshot_path.write_bytes(trial.snapshot_path.read_bytes()+b" ")
    else: trial._failure("prepare_failed",OSError("directory sync did not finish"))
    before=list(writes(r.left_bus))
    with pytest.raises((RuntimeError,OSError)): trial.apply(r)
    assert writes(r.left_bus)==before and r.left_bus.values["Acceleration",ELBOW]==0


def test_selected_acceleration_interrupted_between_failed_write_and_outer_handler_restores_original(tmp_path,monkeypatch):
    module,r,trial,directory=selected_acceleration_trial(tmp_path,monkeypatch)
    trial.prepare(r);trial.disable_torque(r)
    r.left_bus.fail_writes["Acceleration",ELBOW,254]=(OSError("ACK lost before outer handler"),True)
    with pytest.raises(OSError): trial._write("Acceleration",254,phase="configure_acceleration")
    assert json.loads(trial.snapshot_path.read_bytes())["state"]=="configure_acceleration_failed"
    result=stopped_selected_acceleration(module,r,trial,directory)
    assert result["selected_restoration_verified"] and r.left_bus.values["Acceleration",ELBOW]==0
    assert not any(row[1]=="Goal_Position" for row in writes(r.left_bus))


@pytest.mark.parametrize("acceleration",[0,254])
def test_selected_acceleration_missing_marker_cannot_borrow_historical_goal_completion(tmp_path,monkeypatch,acceleration):
    module,r,trial,directory=selected_acceleration_trial(tmp_path,monkeypatch,acceleration=acceleration)
    trial.prepare(r);trial.disable_torque(r);trial.apply(r)
    saved=json.loads(trial.snapshot_path.read_bytes());saved.pop("selected_acceleration_profile")
    trial.snapshot_path.write_bytes((json.dumps(saved,sort_keys=True,indent=2,allow_nan=False)+"\n").encode())
    raw=trial.snapshot_path.read_bytes();before=list(writes(r.left_bus))
    with pytest.raises((RuntimeError,ValueError)):
        stopped_selected_acceleration(module,r,trial,directory)
    assert writes(r.left_bus)==before and trial.snapshot_path.read_bytes()==raw


@pytest.mark.parametrize("phase",["configure","restore"])
def test_selected_acceleration_write_rechecks_actual_off_before_further_configuration(tmp_path,monkeypatch,phase):
    _,r,trial,_=selected_acceleration_trial(tmp_path,monkeypatch)
    trial.prepare(r);trial.disable_torque(r)
    if phase=="restore": trial.apply(r)
    def lost_off(register,motor,value):
        if register=="Acceleration": r.left_bus.values["Torque_Enable",ELBOW]=1
    r.left_bus.before_write=lost_off
    with pytest.raises(RuntimeError,match="OFF|Torque"):
        trial.apply(r) if phase=="configure" else trial.restore(r)
    assert r.left_bus.values["Torque_Enable",ELBOW]==0 and trial.restore_required
    assert json.loads(trial.snapshot_path.read_bytes())["restore_required"]
    if phase=="configure":
        assert not [row for row in writes(r.left_bus) if row[1]=="P_Coefficient"]
    assert not [row for row in writes(r.left_bus) if row[1]=="Goal_Position" or row[1]=="Torque_Enable" and row[3]!=0]


@pytest.mark.parametrize("register",["P_Coefficient","Acceleration"])
def test_selected_acceleration_stopped_write_rechecks_actual_off_before_next_setting(tmp_path,monkeypatch,register):
    module,r,trial,directory=selected_acceleration_trial(tmp_path,monkeypatch)
    trial.prepare(r);trial.disable_torque(r);trial.apply(r)
    original=trial.snapshot_path.read_bytes();before=len(writes(r.left_bus))
    def lost_off(actual,motor,value):
        if actual==register:r.left_bus.values["Torque_Enable",ELBOW]=1
    r.left_bus.before_write=lost_off
    with pytest.raises(RuntimeError,match="OFF|Torque"):
        stopped_selected_acceleration(module,r,trial,directory)
    assert r.left_bus.values["Torque_Enable",ELBOW]==0 and trial.snapshot_path.read_bytes()==original
    assert not list(directory.glob("left-response-p-stopped-completion-*.json"))
    added=writes(r.left_bus)[before:]
    setting=next(row for row in added if row[1]==register)
    operation=max(index for index,row in enumerate(r.left_bus.events) if row is setting)
    assert r.left_bus.events[operation+1][0:3]==("read","Torque_Enable",ELBOW)
    if register=="P_Coefficient":assert not any(row[1]=="Acceleration" for row in added)
    assert not any(row[1]=="Goal_Position" or row[1]=="Lock" and row[3]==0 for row in added)


@pytest.mark.parametrize("goal",[-1,32768,65535,65536])
def test_selected_acceleration_goal_telemetry_uses_decoded_nonnegative_word_format(tmp_path,monkeypatch,goal):
    _,r,trial,_=selected_acceleration_trial(tmp_path,monkeypatch,goal=goal)
    with pytest.raises(RuntimeError):trial.prepare(r)
    assert not writes(r.left_bus) and not trial.restore_required


@pytest.mark.parametrize("location",["original","actual"])
@pytest.mark.parametrize("goal",[32768,65535])
def test_selected_acceleration_stopped_goal_telemetry_rejects_impossible_decoded_word(tmp_path,monkeypatch,location,goal):
    import hashlib
    module,r,trial,directory=selected_acceleration_trial(tmp_path,monkeypatch)
    trial.prepare(r);trial.disable_torque(r);trial.apply(r)
    if location=="original":
        saved=json.loads(trial.snapshot_path.read_bytes());saved["original_registers"]["Goal_Position"]=goal
        saved["original_settings_sha256"]=hashlib.sha256(module._right_p20_json({"registers":saved["original_registers"],"individual_bytes":saved["original_bytes"]})).hexdigest()
        trial.snapshot_path.write_bytes((json.dumps(saved,sort_keys=True,indent=2,allow_nan=False)+"\n").encode())
    else:r.left_bus.values["Goal_Position",ELBOW]=goal
    raw=trial.snapshot_path.read_bytes();before=list(writes(r.left_bus))
    with pytest.raises((ValueError,RuntimeError)):
        stopped_selected_acceleration(module,r,trial,directory)
    assert writes(r.left_bus)==before and trial.snapshot_path.read_bytes()==raw
