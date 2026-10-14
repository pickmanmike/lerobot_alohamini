from __future__ import annotations

import importlib
import json
from types import SimpleNamespace

import pytest

from lerobot.motors import Motor, MotorCalibration, MotorNormMode
from lerobot.motors.feetech.tables import MODEL_NUMBER_TABLE
from lerobot.robots.alohamini.alohamini import AlohaMini

SHOULDER = "arm_left_shoulder_lift"
OTHER = "arm_left_elbow_flex"
FLAGS = ("AM1_SHOULDER_INTEGRAL_TEST", "AM1_SCRIPTED_PREPARE", "AM1_LEFT_SHOULDER_EVIDENCE")
ORIGINAL = {
    "Model_Number": 777,
    "Firmware_Major_Version": 3,
    "Firmware_Minor_Version": 10,
    "Min_Position_Limit": 1000,
    "Max_Position_Limit": 2200,
    "Operating_Mode": 0,
    "Torque_Enable": 0,
    "P_Coefficient": 16,
    "I_Coefficient": 0,
    "D_Coefficient": 32,
    "Minimum_Startup_Force": 16,
}


class OwnerBus:
    def __init__(self):
        self.motors = {
            SHOULDER: Motor(2, "sts3215", MotorNormMode.RANGE_M100_100),
            OTHER: Motor(3, "sts3215", MotorNormMode.RANGE_M100_100),
        }
        self.calibration = {
            name: MotorCalibration(motor.id, 0, 0, 1000, 2200) for name, motor in self.motors.items()
        }
        self.model_number_table = MODEL_NUMBER_TABLE
        self.is_connected = True
        self.values = {(register, SHOULDER): value for register, value in ORIGINAL.items()}
        self.values.update({(register, OTHER): value for register, value in ORIGINAL.items()})
        self.events = []
        self.sequences = {}
        self.fail_writes = {}

    def read(self, register, motor, *, normalize, num_retry):
        self.events.append(("read", register, motor, normalize, num_retry))
        sequence = self.sequences.get((register, motor))
        if sequence:
            value = sequence.pop(0)
            if isinstance(value, BaseException):
                raise value
            return value
        return self.values[(register, motor)]

    def write(self, register, motor, value, *, normalize, num_retry):
        self.events.append(("write", register, motor, value, normalize, num_retry))
        failure = self.fail_writes.get((register, motor, value))
        if failure:
            error, apply = failure
            if apply:
                self.values[(register, motor)] = value
            raise error
        self.values[(register, motor)] = value

    def disconnect(self, *, disable_torque):
        self.events.append(("disconnect", disable_torque))
        self.is_connected = False


def trial_class():
    return importlib.import_module("lerobot.robots.alohamini.am1_shoulder_integral").AM1ShoulderIntegralTrial


def make_robot(tmp_path):
    robot = AlohaMini.__new__(AlohaMini)
    robot.config = SimpleNamespace(robot_model="alohamini1")
    robot.id = "private-test-owner"
    robot.calibration_dir = tmp_path
    robot.left_bus, robot.right_bus = OwnerBus(), None
    robot.left_arm_motors, robot.right_arm_motors = [SHOULDER, OTHER], []
    robot.base_motors, robot.cameras = [], {}
    robot.lift = SimpleNamespace(
        configure=lambda **kwargs: None,
        cfg=SimpleNamespace(name="lift_axis"),
        mark_unhomed=lambda: None,
    )
    robot._configure_bus_defaults = lambda _: None
    return robot


@pytest.fixture(autouse=True)
def clear_flags(monkeypatch, tmp_path):
    for flag in FLAGS:
        monkeypatch.delenv(flag, raising=False)
    monkeypatch.setenv("AM1_LOG_DIRECTORY", str(tmp_path))


def enable(monkeypatch):
    for flag in FLAGS:
        monkeypatch.setenv(flag, "1")


def i_writes(bus):
    return [event for event in bus.events if event[:3] == ("write", "I_Coefficient", SHOULDER)]


def test_real_configure_applies_selected_trial_then_shutdown_restores_before_close(monkeypatch, tmp_path):
    enable(monkeypatch)
    robot = make_robot(tmp_path)
    robot.configure()
    bus = robot.left_bus
    assert bus.values[("I_Coefficient", SHOULDER)] == 1
    assert bus.values[("I_Coefficient", OTHER)] == 0
    assert [event[3] for event in i_writes(bus)] == [0, 1]
    bus.events.clear()
    assert robot._safe_shutdown(close_buses=True) == []
    restore = ("write", "I_Coefficient", SHOULDER, 0, False, 2)
    assert restore in bus.events
    assert bus.events.index(restore) < bus.events.index(("disconnect", False))
    torque_proof = ("read", "Torque_Enable", SHOULDER, False, 0)
    assert bus.events.index(torque_proof) < bus.events.index(restore)


@pytest.mark.parametrize("value", [None, "0", "true", "01", " 1"])
def test_default_and_inexact_opt_in_have_no_bus_or_filesystem_effect(monkeypatch, tmp_path, value):
    robot = make_robot(tmp_path)
    if value is not None:
        monkeypatch.setenv(FLAGS[0], value)
    trial = trial_class()()
    assert trial.configure(robot) is False
    trial.restore(robot)
    assert robot.left_bus.events == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("flag,value", [(FLAGS[1], None), (FLAGS[1], "true"), (FLAGS[2], "0")])
def test_exact_opt_in_requires_preparation_and_evidence_before_io(monkeypatch, tmp_path, flag, value):
    enable(monkeypatch)
    if value is None:
        monkeypatch.delenv(flag)
    else:
        monkeypatch.setenv(flag, value)
    robot = make_robot(tmp_path)
    with pytest.raises(RuntimeError, match="AM1_SCRIPTED_PREPARE|AM1_LEFT_SHOULDER_EVIDENCE"):
        trial_class()().configure(robot)
    assert robot.left_bus.events == []


def test_default_snapshot_requires_established_private_owner_log_directory(monkeypatch, tmp_path):
    enable(monkeypatch)
    monkeypatch.delenv("AM1_LOG_DIRECTORY")
    robot = make_robot(tmp_path)
    with pytest.raises(RuntimeError, match="AM1_LOG_DIRECTORY"):
        trial_class()().configure(robot)
    assert i_writes(robot.left_bus) == []
    assert list(tmp_path.iterdir()) == []
    assert trial_class()(snapshot_path=tmp_path / "explicit.json").configure(robot) is True


@pytest.mark.parametrize(
    "change", ["other_robot", "other_model", "not_selected", "wrong_id", "no_calibration"]
)
def test_trial_rejects_foreign_model_or_mapping_before_io(monkeypatch, tmp_path, change):
    enable(monkeypatch)
    robot = make_robot(tmp_path)
    if change == "other_robot":
        robot.config.robot_model = "alohamini2"
    elif change == "other_model":
        robot.left_bus.motors[SHOULDER].model = "sts3095"
    elif change == "not_selected":
        robot.left_arm_motors.remove(SHOULDER)
    elif change == "wrong_id":
        robot.left_bus.calibration[SHOULDER].id = 9
    else:
        del robot.left_bus.calibration[SHOULDER]
    with pytest.raises(RuntimeError):
        trial_class()().configure(robot)
    assert robot.left_bus.events == []


@pytest.mark.parametrize(
    "register,value",
    [
        ("Model_Number", 2569),
        ("Min_Position_Limit", 999),
        ("Max_Position_Limit", 2201),
        ("Operating_Mode", 1),
        ("Torque_Enable", 1),
        ("P_Coefficient", 20),
        ("I_Coefficient", 1),
        ("D_Coefficient", 31),
        ("Minimum_Startup_Force", 272),
    ],
)
def test_stopped_baseline_refusal_never_writes_integral(monkeypatch, tmp_path, register, value):
    enable(monkeypatch)
    robot = make_robot(tmp_path)
    robot.left_bus.values[(register, SHOULDER)] = value
    trial = trial_class()()
    with pytest.raises(RuntimeError, match=register):
        trial.configure(robot)
    trial.restore(robot)
    assert i_writes(robot.left_bus) == []
    assert all(event[2:] == (SHOULDER, False, 0) for event in robot.left_bus.events)


def test_original_private_json_precedes_write_and_retains_firmware_without_version_assumption(
    monkeypatch,
    tmp_path,
):
    enable(monkeypatch)
    robot = make_robot(tmp_path)
    robot.left_bus.values[("Firmware_Major_Version", SHOULDER)] = 99
    robot.left_bus.values[("Firmware_Minor_Version", SHOULDER)] = 42
    trial = trial_class()()
    original_write = robot.left_bus.write
    seen = []

    def write(register, motor, value, **kwargs):
        seen.append(json.loads(trial.snapshot_path.read_text(encoding="utf-8")))
        original_write(register, motor, value, **kwargs)

    monkeypatch.setattr(robot.left_bus, "write", write)
    assert trial.configure(robot) is True
    assert seen[0]["original_registers"]["I_Coefficient"] == 0
    snapshot = json.loads(trial.snapshot_path.read_text(encoding="utf-8"))
    assert snapshot["original_registers"]["Firmware_Major_Version"] == 99
    assert snapshot["original_registers"]["Firmware_Minor_Version"] == 42
    assert snapshot["original_registers"]["Minimum_Startup_Force"] == 16
    assert snapshot["startup_force_low_byte"] == 16
    assert snapshot["startup_force_high_byte"] == 0
    assert snapshot["status"] == "configured"
    assert not any(key in snapshot for key in ("environment", "port", "credentials"))
    assert all(event[2] == SHOULDER for event in robot.left_bus.events)
    assert all(event[3:] == (False, 0) for event in robot.left_bus.events if event[0] == "read")
    assert i_writes(robot.left_bus) == [("write", "I_Coefficient", SHOULDER, 1, False, 2)]
    assert {k: v for (k, name), v in robot.left_bus.values.items() if name == SHOULDER} == {
        **ORIGINAL,
        "I_Coefficient": 1,
        "Firmware_Major_Version": 99,
        "Firmware_Minor_Version": 42,
    }


def test_snapshot_failure_and_late_torque_enable_refuse_before_integral_write(monkeypatch, tmp_path):
    enable(monkeypatch)
    robot = make_robot(tmp_path)
    trial = trial_class()(snapshot_path=tmp_path / "missing" / "snapshot.json")
    with pytest.raises(OSError):
        trial.configure(robot)
    assert i_writes(robot.left_bus) == []
    robot = make_robot(tmp_path)
    robot.left_bus.sequences[("Torque_Enable", SHOULDER)] = [0, 1]
    with pytest.raises(RuntimeError, match="Torque_Enable"):
        trial_class()().configure(robot)
    assert i_writes(robot.left_bus) == []


@pytest.mark.parametrize("error", [ConnectionError("I1 write acknowledgement missing"), KeyboardInterrupt()])
def test_applied_write_failure_preserves_original_cause_and_requires_restore(monkeypatch, tmp_path, error):
    enable(monkeypatch)
    robot = make_robot(tmp_path)
    robot.left_bus.fail_writes[("I_Coefficient", SHOULDER, 1)] = (error, True)
    trial = trial_class()()
    with pytest.raises(type(error)) as caught:
        trial.configure(robot)
    assert caught.value is error
    assert trial.restore_required is True
    assert robot.left_bus.values[("I_Coefficient", SHOULDER)] == 1
    for flag in FLAGS:
        monkeypatch.delenv(flag)
    trial.restore(robot)
    assert robot.left_bus.values[("I_Coefficient", SHOULDER)] == 0
    assert trial.restore_required is False
    snapshot = json.loads(trial.snapshot_path.read_text(encoding="utf-8"))
    assert snapshot["status"] == "restored"


def test_failed_readback_still_restores_and_refuses_energized_or_foreign_owner(monkeypatch, tmp_path):
    enable(monkeypatch)
    robot = make_robot(tmp_path)
    robot.left_bus.sequences[("I_Coefficient", SHOULDER)] = [0, 0]
    trial = trial_class()()
    with pytest.raises(RuntimeError, match="I_Coefficient"):
        trial.configure(robot)
    assert trial.restore_required is True
    robot.left_bus.values[("Torque_Enable", SHOULDER)] = 1
    robot.left_bus.events.clear()
    with pytest.raises(RuntimeError, match="Torque_Enable"):
        trial.restore(robot)
    assert i_writes(robot.left_bus) == []
    original_bus = robot.left_bus
    robot.left_bus = OwnerBus()
    with pytest.raises(RuntimeError, match="owner"):
        trial.restore(robot)
    assert robot.left_bus.events == []
    robot.left_bus = original_bus
    original_bus.values[("Torque_Enable", SHOULDER)] = 0
    trial.restore(robot)
    original_bus.events.clear()
    trial.restore(robot)
    assert original_bus.events == []


@pytest.mark.parametrize("failure", ["write", "readback", "disconnected", "torque_proof", "interrupted"])
def test_shutdown_reports_restore_uncertainty_and_attempts_bus_close(monkeypatch, tmp_path, failure):
    enable(monkeypatch)
    robot = make_robot(tmp_path)
    robot.configure()
    assert robot.left_bus.values[("I_Coefficient", SHOULDER)] == 1
    bus = robot.left_bus
    if failure == "write":
        bus.fail_writes[("I_Coefficient", SHOULDER, 0)] = (ConnectionError("restore write failed"), False)
    elif failure == "readback":
        bus.sequences[("I_Coefficient", SHOULDER)] = [1]
    elif failure == "interrupted":
        bus.fail_writes[("I_Coefficient", SHOULDER, 0)] = (KeyboardInterrupt(), False)
    elif failure == "disconnected":
        bus.is_connected = False
    else:
        bus.sequences[("Torque_Enable", SHOULDER)] = [ConnectionError("torque proof failed")]
    errors = robot._safe_shutdown(close_buses=True)
    assert any("integral" in error.lower() for error in errors)
    assert robot._am1_shoulder_integral_trial.restore_required is True
    if failure != "disconnected":
        assert ("disconnect", False) in bus.events
    if failure in ("disconnected", "torque_proof"):
        assert [event[3] for event in i_writes(bus)] == [0, 1]


def test_partial_configure_hook_preserves_trial_for_shutdown(monkeypatch, tmp_path):
    enable(monkeypatch)
    robot = make_robot(tmp_path)
    primary = ConnectionError("I1 applied but configure failed")
    robot.left_bus.fail_writes[("I_Coefficient", SHOULDER, 1)] = (primary, True)
    with pytest.raises(ConnectionError) as caught:
        robot.configure()
    assert caught.value is primary
    assert robot._am1_shoulder_integral_trial.restore_required is True
    assert robot._safe_shutdown(close_buses=True) == []
    assert robot.left_bus.values[("I_Coefficient", SHOULDER)] == 0


def test_reconfigure_restores_previous_trial_before_new_snapshot(monkeypatch, tmp_path):
    enable(monkeypatch)
    robot = make_robot(tmp_path)
    robot.configure()
    previous = robot._am1_shoulder_integral_trial
    robot.configure()
    assert previous.restore_required is False
    assert json.loads(previous.snapshot_path.read_text(encoding="utf-8"))["status"] == "restored"
    assert robot._am1_shoulder_integral_trial is not previous
    assert robot.left_bus.values[("I_Coefficient", SHOULDER)] == 1
    assert robot._am1_shoulder_integral_trial.snapshot_path != previous.snapshot_path


def test_original_read_failure_is_terminal_zero_retry_and_never_writes(monkeypatch, tmp_path):
    enable(monkeypatch)
    robot = make_robot(tmp_path)
    primary = ConnectionError("model snapshot unavailable")
    robot.left_bus.sequences[("Model_Number", SHOULDER)] = [primary]
    with pytest.raises(ConnectionError) as caught:
        trial_class()().configure(robot)
    assert caught.value is primary
    assert robot.left_bus.events == [("read", "Model_Number", SHOULDER, False, 0)]
