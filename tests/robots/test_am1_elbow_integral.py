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


def left_response_p_files(tmp_path, monkeypatch, *, proportional=32):
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
    request = state / "left-response-p-request.json"
    request.write_bytes(module._right_p20_json(value))
    request.chmod(0o600)
    return module, state, directory, request, value


def left_response_p_robot(tmp_path, monkeypatch, *, proportional=32):
    module, state, directory, request, value = left_response_p_files(
        tmp_path, monkeypatch, proportional=proportional
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
