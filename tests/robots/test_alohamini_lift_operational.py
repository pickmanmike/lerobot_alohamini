"""Normal AM1 host lift policy, with fake clock/mechanics and grouped bytes only."""

from __future__ import annotations

import json

import pytest

from lerobot.motors import MotorCalibration
from lerobot.motors.feetech import FeetechMotorsBus
from lerobot.robots.alohamini import alohamini as robot_module
from lerobot.robots.alohamini import lift_relief
from lerobot.robots.alohamini.config_alohamini import AlohaMiniConfig
from tests.robots.test_alohamini_lift_relief import Clock, GroupedLiftTransport, LiftBus


@pytest.fixture
def operating_robot(monkeypatch, tmp_path):
    clock = Clock()
    monkeypatch.setattr(lift_relief.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(lift_relief.time, "sleep", clock.sleep)

    def bus_factory(**kwargs):
        bus = LiftBus(clock, **kwargs)
        bus.is_calibrated = True
        bus.calibration = {
            name: MotorCalibration(motor.id, 0, 0, 1000, 2200)
            for name, motor in bus.motors.items()
        }
        normalizer = FeetechMotorsBus("unused-test-port", bus.motors, bus.calibration)
        bus.apply_drive_mode = normalizer.apply_drive_mode
        bus.model_resolution_table = normalizer.model_resolution_table
        bus._normalize, bus._unnormalize = normalizer._normalize, normalizer._unnormalize
        original_read = bus.read

        def calibrated_read(register, name, *, normalize=True, **options):
            value = original_read(register, name, normalize=normalize, **options)
            if register == "Present_Position" and name.startswith("arm_") and not normalize:
                # These fixtures declare arm positions in normalized units; model
                # the same raw register representation as the real bus.
                motor = bus.motors[name]
                return bus._unnormalize({motor.id: value})[motor.id]
            return value

        bus.read = calibrated_read
        bus.sync_read = lambda register, motors, **options: {
            name: bus.read(register, name, **options) for name in motors
        }
        bus.sync_write = lambda register, values, **kwargs: [
            bus.write(register, name, value, **kwargs) for name, value in values.items()
        ]
        return bus

    class Transport(GroupedLiftTransport):
        def read_group(self, *args, **kwargs):
            clock.sleep(0.002)  # Synthetic finite transaction time, not instant duplicate samples.
            payload, trace = super().read_group(*args, **kwargs)
            from lerobot.robots.alohamini import lift_motor_feedback as feedback

            moving = self.bus.read_sequences.get(("Moving", "lift_axis"))
            if moving and args[:2] == (feedback.FEEDBACK_START, feedback.FEEDBACK_LENGTH):
                payload = bytearray(payload)
                payload[feedback.MOVING_ADDRESS - feedback.FEEDBACK_START] = moving.pop(0)
                payload = bytes(payload)
                trace = {**trace, "response_payload_bytes": list(payload)}
            return payload, trace

    monkeypatch.setattr(robot_module, "FeetechMotorsBus", bus_factory)
    monkeypatch.setattr(lift_relief, "make_grouped_transport", lambda robot: Transport(robot.left_bus))
    robot = robot_module.AlohaMini(AlohaMiniConfig(
        robot_model="alohamini1", no_follower=True, cameras={}, calibration_dir=tmp_path,
    ))
    return robot, clock


def operational_records(capsys):
    return [json.loads(line.split("] ", 1)[1]) for line in capsys.readouterr().out.splitlines()
            if line.startswith("[LIFT OPERATIONAL] ")]


def test_normal_am1_connect_homes_once_and_relieve_before_ordinary_activation(operating_robot, capsys):
    robot, _ = operating_robot
    robot.connect(calibrate=False)
    bus = robot.left_bus
    assert 9.5 <= robot.lift.get_height_mm() <= 12
    assert bus.registers[("Goal_Velocity", "lift_axis")] == 0
    assert bus.registers[("Torque_Enable", "lift_axis")] == 1
    writes = [event for event in bus.events if event[1] == "write"]
    assert sum(e[2:] == ("Goal_Velocity", "lift_axis", 200) for e in writes) == 1
    assert sum(e[2:] == ("Goal_Velocity", "lift_axis", -200) for e in writes) == 1
    records = operational_records(capsys)
    assert any(r["phase"] == "operational_ready" for r in records)
    assert not any(e[1:3] == ("read", "Phase") for e in bus.events)
    robot.disconnect()
    assert bus.registers[("Torque_Enable", "lift_axis")] == 0
    assert not bus.is_connected


def test_one_wrong_sign_relief_velocity_with_upward_encoder_does_not_abort(operating_robot, capsys):
    robot, _ = operating_robot
    bus = robot.left_bus
    relief_samples = 0

    def hook(register):
        nonlocal relief_samples
        if register == "Present_Velocity" and bus.registers[("Goal_Velocity", "lift_axis")] == -200:
            relief_samples += 1
            if relief_samples == 2:
                bus.read_sequences[(register, "lift_axis")] = [50]

    bus.hook = hook
    robot.connect(calibrate=False)
    records = operational_records(capsys)
    motion = [record for record in records if record["phase"] == "relief"]
    assert len(motion) >= 3
    assert motion[1]["present_velocity_raw"] == 50
    assert motion[1]["present_position_raw"] < motion[0]["present_position_raw"]
    assert any(record["phase"] == "relief_direction_disagreement" for record in records)
    assert any(record["phase"] == "operational_ready" for record in records)
    assert not any(record.get("rejected") for record in records)
    robot.disconnect()
    assert bus.registers[("Goal_Velocity", "lift_axis")] == 0
    assert bus.registers[("Torque_Enable", "lift_axis")] == 0
    assert not bus.is_connected


def test_repeated_wrong_sign_relief_velocity_refuses_after_two_fresh_samples(operating_robot, capsys):
    robot, _ = operating_robot
    bus = robot.left_bus
    relief_samples = 0

    def hook(register):
        nonlocal relief_samples
        if register == "Present_Velocity" and bus.registers[("Goal_Velocity", "lift_axis")] == -200:
            relief_samples += 1
            if relief_samples in (2, 3):
                bus.read_sequences[(register, "lift_axis")] = [50]

    bus.hook = hook
    with pytest.raises(RuntimeError, match="AlohaMini motor activation failed") as failure:
        robot.connect(calibrate=False)
    assert "repeated velocity/position direction disagreement" in str(failure.value.__cause__)
    records = operational_records(capsys)
    wrong_sign = [
        record for record in records
        if record["phase"] == "relief" and record["present_velocity_raw"] == 50
        and not record.get("rejected")
    ]
    assert len(wrong_sign) == 2
    assert wrong_sign[1]["present_position_raw"] < wrong_sign[0]["present_position_raw"]
    assert any(record.get("rejected") and record["phase"] == "relief" for record in records)
    assert not any(record["phase"] == "operational_ready" for record in records)
    assert bus.registers[("Goal_Velocity", "lift_axis")] == 0
    assert bus.registers[("Torque_Enable", "lift_axis")] == 0
    assert not bus.is_connected


@pytest.mark.parametrize("reported_velocity", [0, -50])
def test_downward_encoder_step_refuses_despite_nonpositive_velocity(operating_robot, capsys, reported_velocity):
    robot, _ = operating_robot
    bus = robot.left_bus
    bus.up_factor = -1

    def hook(register):
        operation = getattr(robot, "_lift_operation", None)
        if (register == "Present_Position" and operation is not None
                and operation.reader.phase == "relief_setup"):
            bus.bottom = 1200
        if register == "Present_Velocity" and bus.registers[("Goal_Velocity", "lift_axis")] == -200:
            bus.read_sequences[(register, "lift_axis")] = [reported_velocity]

    bus.hook = hook
    with pytest.raises(RuntimeError, match="AlohaMini motor activation failed") as failure:
        robot.connect(calibrate=False)
    assert "relief: unexpected downward direction" in str(failure.value.__cause__)
    records = operational_records(capsys)
    motion = [record for record in records if record["phase"] == "relief" and not record.get("rejected")]
    assert len(motion) == 1
    assert motion[0]["present_velocity_raw"] == reported_velocity
    assert motion[0]["present_position_raw"] > 1100
    assert not any(record["phase"] == "operational_ready" for record in records)
    assert bus.registers[("Goal_Velocity", "lift_axis")] == 0
    assert bus.registers[("Torque_Enable", "lift_axis")] == 0
    assert not bus.is_connected


def test_large_wrong_sign_velocity_without_upward_encoder_step_refuses_immediately(operating_robot, capsys):
    robot, _ = operating_robot
    bus = robot.left_bus
    bus.up_factor = 0

    def hook(register):
        if register == "Present_Velocity" and bus.registers[("Goal_Velocity", "lift_axis")] == -200:
            bus.read_sequences[(register, "lift_axis")] = [100]

    bus.hook = hook
    with pytest.raises(RuntimeError, match="AlohaMini motor activation failed") as failure:
        robot.connect(calibrate=False)
    assert "without fresh upward position progress" in str(failure.value.__cause__)
    records = operational_records(capsys)
    motion = [record for record in records if record["phase"] == "relief" and not record.get("rejected")]
    assert len(motion) == 1
    assert motion[0]["present_velocity_raw"] == 100
    assert not any(record["phase"] == "operational_ready" for record in records)
    assert bus.registers[("Goal_Velocity", "lift_axis")] == 0
    assert bus.registers[("Torque_Enable", "lift_axis")] == 0
    assert not bus.is_connected


def test_first_relief_step_compares_with_fresh_setup_position(operating_robot, capsys):
    robot, _ = operating_robot
    bus = robot.left_bus
    bus.up_factor = -0.2  # More than the approved one-count variation on the first sample.
    shifted_setup_position = False

    def hook(register):
        nonlocal shifted_setup_position
        operation = getattr(robot, "_lift_operation", None)
        if (register == "Present_Position" and operation is not None
                and operation.reader.phase == "relief_setup" and not shifted_setup_position):
            bus.position -= 20
            bus.registers[(register, "lift_axis")] = round(bus.position)
            shifted_setup_position = True

    bus.hook = hook
    with pytest.raises(RuntimeError, match="AlohaMini motor activation failed") as failure:
        robot.connect(calibrate=False)
    assert "relief: unexpected downward direction" in str(failure.value.__cause__)
    records = operational_records(capsys)
    setup = [record for record in records if record["phase"] == "relief_setup"]
    motion = [record for record in records if record["phase"] == "relief" and not record.get("rejected")]
    assert setup[-1]["present_position_raw"] == 1080
    assert len(motion) == 1
    assert motion[0]["present_position_raw"] > setup[-1]["present_position_raw"]
    assert motion[0]["present_position_raw"] < 1100
    assert not any(record["phase"] == "operational_ready" for record in records)
    assert bus.registers[("Goal_Velocity", "lift_axis")] == 0
    assert bus.registers[("Torque_Enable", "lift_axis")] == 0
    assert not bus.is_connected


@pytest.mark.parametrize("reported_velocity", [0, -50, 50])
def test_one_count_relief_variation_is_logged_and_finishes_with_original_zero(
    operating_robot, capsys, reported_velocity,
):
    robot, _ = operating_robot
    bus = robot.left_bus
    setup = None
    samples = 0

    def hook(register):
        nonlocal setup, samples
        operation = getattr(robot, "_lift_operation", None)
        if operation is None:
            return
        if register == "Present_Position" and operation.reader.phase == "relief_setup":
            setup = round(bus.position)
        if operation.reader.phase == "relief" and bus.registers[("Goal_Velocity", "lift_axis")] == -200:
            if register == "Present_Position":
                samples += 1
                if samples == 1:
                    # Synthetic one-count backstep, modeled on the saved refusal.
                    bus.position = setup + 1
                    bus.registers[(register, "lift_axis")] = setup + 1
            elif register == "Present_Velocity" and samples == 1:
                bus.read_sequences[(register, "lift_axis")] = [reported_velocity]

    bus.hook = hook
    robot.connect(calibrate=False)
    records = operational_records(capsys)
    motion = [r for r in records if r["phase"] == "relief"]
    assert motion[0]["present_position_raw"] == setup + 1
    assert motion[0]["present_velocity_raw"] == reported_velocity
    assert any(r["phase"] == "relief_position_variation" for r in records)
    pending = [r for r in records if r["phase"] == "relief_direction_pending"]
    qualified = [r for r in records if r["phase"] == "relief_direction_qualified"]
    assert len(pending) == len(qualified) == 1
    assert pending[0]["present_position_raw"] == setup + 1
    assert qualified[0]["present_position_raw"] < setup
    assert 0.2 <= qualified[0]["qualification_elapsed_s"] < 0.25
    writes = [e[2:] for e in bus.events if e[1] == "write"]
    assert writes.count(("Goal_Velocity", "lift_axis", -200)) == 1
    assert 9.5 <= robot._lift_operation.height_mm <= 12
    assert robot.lift._z0_deg == pytest.approx(-8.7890625)  # 100 ticks, unchanged home zero.
    robot.disconnect()
    assert bus.registers[("Goal_Velocity", "lift_axis")] == 0
    assert bus.registers[("Torque_Enable", "lift_axis")] == 0
    assert not bus.is_connected


@pytest.mark.parametrize("progress_sample,expected_elapsed", [(3, 0.306), (6, 0.612), (9, 0.918)])
def test_initial_relief_allows_delayed_net_upward_progress_before_one_second(
    operating_robot, capsys, progress_sample, expected_elapsed,
):
    robot, _ = operating_robot
    bus = robot.left_bus
    bus.up_factor = 0
    setup = None
    samples = 0

    def hook(register):
        nonlocal setup, samples
        operation = getattr(robot, "_lift_operation", None)
        if operation is None:
            return
        if register == "Present_Position" and operation.reader.phase == "relief_setup":
            setup = round(bus.position)
        if operation.reader.phase != "relief" or bus.registers[("Goal_Velocity", "lift_axis")] != -200:
            return
        if register == "Present_Position":
            samples += 1
            if samples == progress_sample:
                # Synthetic delayed onset, not a reconstruction of the stopped run.
                bus.position = setup - 1
                bus.registers[(register, "lift_axis")] = setup - 1
                bus.up_factor = 1
        elif register == "Present_Velocity" and samples < progress_sample:
            bus.read_sequences[(register, "lift_axis")] = [0]

    bus.hook = hook
    robot.connect(calibrate=False)
    records = operational_records(capsys)
    pending = [r for r in records if r["phase"] == "relief_direction_pending"]
    qualified = [r for r in records if r["phase"] == "relief_direction_qualified"]
    assert len(pending) == progress_sample - 1
    assert all(r["present_position_raw"] == setup for r in pending)
    assert len(qualified) == 1
    assert qualified[0]["qualification_elapsed_s"] == pytest.approx(expected_elapsed)
    assert qualified[0]["present_position_raw"] == setup - 1
    assert qualified[0]["upward_ticks"] > qualified[0]["initial_upward_ticks"]
    assert 9.5 <= robot._lift_operation.height_mm <= 12
    assert robot.lift._z0_deg == pytest.approx(-8.7890625)
    assert any(r["phase"] == "operational_ready" for r in records)
    writes = [e[2:] for e in bus.events if e[1] == "write"]
    assert writes.count(("Goal_Velocity", "lift_axis", -200)) == 1
    assert bus.registers[("Goal_Velocity", "lift_axis")] == 0
    robot.disconnect()
    assert bus.registers[("Torque_Enable", "lift_axis")] == 0
    assert not bus.is_connected


@pytest.mark.parametrize("offsets", [(0, 0), (1, 0), (1, 1)])
def test_initial_relief_direction_deadline_is_fixed_and_stops_without_upward_progress(
    operating_robot, monkeypatch, capsys, offsets,
):
    robot, clock = operating_robot
    bus = robot.left_bus
    bus.up_factor = 0
    setup = None
    samples = 0
    writes = []
    original_write = bus.write

    def write(register, motor, value, **kwargs):
        original_write(register, motor, value, **kwargs)
        if (register, motor, int(value)) == ("Goal_Velocity", "lift_axis", -200):
            clock.sleep(0.035)  # Synthetic acknowledgement time: deadline starts AFTER this.
        writes.append((clock.now, register, motor, int(value)))

    def hook(register):
        nonlocal setup, samples
        operation = getattr(robot, "_lift_operation", None)
        if operation is None:
            return
        if register == "Present_Position" and operation.reader.phase == "relief_setup":
            setup = round(bus.position)
        if operation.reader.phase == "relief" and bus.registers[("Goal_Velocity", "lift_axis")] == -200:
            if register == "Present_Position":
                samples += 1
                bus.position = setup + offsets[min(samples - 1, len(offsets) - 1)]
                bus.registers[(register, "lift_axis")] = round(bus.position)
            elif register == "Present_Velocity":
                bus.read_sequences[(register, "lift_axis")] = [0]

    monkeypatch.setattr(bus, "write", write)
    bus.hook = hook
    with pytest.raises(RuntimeError) as failure:
        robot.connect(calibrate=False)
    assert "initial upward direction not confirmed within 1.0 s" in str(failure.value.__cause__)
    assert failure.value.__cause__ is robot._lift_operation.failure
    start = next(t for t, register, motor, value in writes
                 if (register, motor, value) == ("Goal_Velocity", "lift_axis", -200))
    stop = next(t for t, register, motor, value in writes
                if t >= start and (register, motor, value) == ("Goal_Velocity", "lift_axis", 0))
    assert stop - start == pytest.approx(1.0)
    assert samples == 9
    assert sum((register, motor, value) == ("Goal_Velocity", "lift_axis", -200)
               for _, register, motor, value in writes) == 1
    records = operational_records(capsys)
    rejected = next(r for r in records if r.get("rejected") and r["phase"] == "relief")
    assert rejected["qualification_elapsed_s"] == pytest.approx(1.0)
    assert rejected["sample_monotonic_s"] < start + 1.0  # Last actual reply is NOT relabeled fresh.
    assert not any(r["phase"] in ("relief_direction_qualified", "operational_ready") for r in records)
    assert any(r["phase"] == "shutdown_verified" for r in records)
    assert bus.registers[("Goal_Velocity", "lift_axis")] == 0
    assert bus.registers[("Torque_Enable", "lift_axis")] == 0
    assert not bus.is_connected


@pytest.mark.parametrize("first_wrong_sample", [1, 6])
def test_initial_relief_repeated_small_wrong_sign_velocity_still_stops_on_second_sample(
    operating_robot, capsys, first_wrong_sample,
):
    robot, _ = operating_robot
    bus = robot.left_bus
    bus.up_factor = 0
    samples = 0

    def hook(register):
        nonlocal samples
        if register == "Present_Velocity" and bus.registers[("Goal_Velocity", "lift_axis")] == -200:
            samples += 1
            bus.read_sequences[(register, "lift_axis")] = [50 if samples >= first_wrong_sample else 0]

    bus.hook = hook
    with pytest.raises(RuntimeError) as failure:
        robot.connect(calibrate=False)
    assert "repeated velocity/position direction disagreement" in str(failure.value.__cause__)
    records = operational_records(capsys)
    motion_records = [r for r in records if r["phase"] == "relief" and not r.get("rejected")]
    assert len(motion_records) == first_wrong_sample + 1
    assert not any(r["phase"] in ("relief_direction_qualified", "operational_ready") for r in records)
    assert bus.registers[("Goal_Velocity", "lift_axis")] == 0
    assert bus.registers[("Torque_Enable", "lift_axis")] == 0
    assert not bus.is_connected


def test_initial_direction_qualification_never_reopens_after_upward_progress(operating_robot, capsys):
    robot, _ = operating_robot
    bus = robot.left_bus
    samples = 0
    first_position = None

    def hook(register):
        nonlocal samples, first_position
        operation = getattr(robot, "_lift_operation", None)
        if (operation is None or operation.reader.phase != "relief"
                or bus.registers[("Goal_Velocity", "lift_axis")] != -200):
            return
        if register == "Present_Position":
            samples += 1
            if samples == 1:
                first_position = round(bus.position)
            elif samples == 2:
                bus.position = first_position
                bus.registers[(register, "lift_axis")] = first_position
        elif register == "Present_Velocity" and samples == 2:
            bus.read_sequences[(register, "lift_axis")] = [50]

    bus.hook = hook
    with pytest.raises(RuntimeError) as failure:
        robot.connect(calibrate=False)
    assert "without fresh upward position progress" in str(failure.value.__cause__)
    records = operational_records(capsys)
    assert len([r for r in records if r["phase"] == "relief_direction_qualified"]) == 1
    assert not any(r["phase"] == "relief_direction_pending" for r in records)
    assert samples == 2
    assert bus.registers[("Goal_Velocity", "lift_axis")] == 0
    assert bus.registers[("Torque_Enable", "lift_axis")] == 0
    assert not bus.is_connected


@pytest.mark.parametrize("delayed_boundary", ["sleep", "feedback"])
def test_initial_relief_cannot_qualify_late_upward_feedback(
    operating_robot, monkeypatch, capsys, delayed_boundary,
):
    from lerobot.robots.alohamini.lift_operational import OperationalLift

    robot, clock = operating_robot
    bus = robot.left_bus
    bus.up_factor = 0
    original_moving_height = OperationalLift._moving_height
    samples = 0
    first_position = None

    def hook(register):
        if register == "Present_Velocity" and bus.registers[("Goal_Velocity", "lift_axis")] == -200:
            bus.read_sequences[(register, "lift_axis")] = [0]

    def moving_height(operation, phase):
        nonlocal samples, first_position
        samples += 1
        if samples == 1:
            first_position = bus.position
        if samples == 9:
            # Arrive just after 1 s, but retain the unchanged temperature
            # freshness window so this isolates the direction deadline.
            clock.sleep(0.085)
            bus.up_factor = 1
        result = original_moving_height(operation, phase)
        return result

    original_sleep = clock.sleep

    def delayed_sleep(seconds):
        original_sleep(seconds)
        if samples == 8 and seconds > 0.05 and bus.registers[("Goal_Velocity", "lift_axis")] == -200:
            original_sleep(0.085)

    bus.hook = hook
    monkeypatch.setattr(OperationalLift, "_moving_height", moving_height)
    if delayed_boundary == "sleep":
        monkeypatch.setattr(lift_relief.time, "sleep", delayed_sleep)
    with pytest.raises(RuntimeError) as failure:
        robot.connect(calibrate=False)
    assert "initial upward direction not confirmed within 1.0 s" in str(failure.value.__cause__)
    records = operational_records(capsys)
    rejected = next(r for r in records if r.get("rejected") and r["phase"] == "relief")
    assert 1.0 <= rejected["qualification_elapsed_s"] < 1.01
    assert samples == (8 if delayed_boundary == "sleep" else 9)
    if delayed_boundary == "feedback":
        assert rejected["present_position_raw"] < first_position  # Fresh but too late.
    assert not any(r["phase"] in ("relief_direction_qualified", "operational_ready") for r in records)
    assert bus.registers[("Goal_Velocity", "lift_axis")] == 0
    assert bus.registers[("Torque_Enable", "lift_axis")] == 0
    assert not bus.is_connected


@pytest.mark.parametrize("fault,reason", [
    ("status", "status"), ("transport", "grouped feedback"),
    ("current", "2000 mA"), ("voltage", "voltage"), ("interrupt", "cancelled"),
    ("temperature", "majority (3/5 time slots)"),
])
@pytest.mark.parametrize("fault_sample", [2, 6])
def test_initial_relief_pending_does_not_filter_faults_or_cancellation(
    operating_robot, capsys, fault, reason, fault_sample,
):
    robot, _ = operating_robot
    bus = robot.left_bus
    bus.up_factor = 0
    samples = 0
    interruption = KeyboardInterrupt("cancelled during initial relief")

    def hook(register):
        nonlocal samples
        operation = getattr(robot, "_lift_operation", None)
        if fault == "temperature" and register == "Present_Temperature":
            # Synthetic sustained high in three occupied slots. Test both
            # the old interval and the newly allowed part of qualification.
            heating = operation is not None and (
                (fault_sample == 2 and operation.reader.phase == "relief_setup")
                or (operation.reader.phase == "relief" and samples >= fault_sample - 2)
            )
            bus.read_sequences[(register, "lift_axis")] = [60 if heating else 30]
        if bus.registers[("Goal_Velocity", "lift_axis")] != -200:
            if register == "Status":
                bus.registers[(register, "lift_axis")] = 0
            if register == "Present_Voltage":
                bus.registers[(register, "lift_axis")] = 120
            return
        if register == "Present_Position":
            samples += 1
        elif register == "Present_Velocity":
            bus.read_sequences[(register, "lift_axis")] = [50 if samples == 1 else 0]
        if samples != fault_sample:
            return
        if fault == "status" and register == "Status":
            bus.read_sequences[(register, "lift_axis")] = [4]
        elif fault == "current" and register == "Present_Current":
            bus.read_sequences[(register, "lift_axis")] = [400]
        elif fault == "voltage" and register == "Present_Voltage":
            bus.read_sequences[(register, "lift_axis")] = [20]
        elif register == "Present_Temperature":
            if fault == "transport":
                raise OSError("synthetic communication loss during qualification")
            if fault == "interrupt":
                raise interruption

    bus.hook = hook
    with pytest.raises((RuntimeError, KeyboardInterrupt)) as failure:
        robot.connect(calibrate=False)
    primary = failure.value if isinstance(failure.value, KeyboardInterrupt) else failure.value.__cause__
    assert primary is robot._lift_operation.failure
    assert reason in str(primary)
    if fault == "interrupt":
        assert primary is interruption
    records = operational_records(capsys)
    assert any(r["phase"] == "relief_direction_pending" for r in records)
    if fault == "temperature":
        rejected = next(r for r in records if r.get("rejected") and r["phase"] == "relief")
        assert rejected["temperature_c"] == 60
        assert "majority (3/5 time slots)" in rejected["rejection_reason"]
    assert not any(r["phase"] in ("relief_direction_qualified", "operational_ready") for r in records)
    assert samples == fault_sample
    assert bus.registers[("Goal_Velocity", "lift_axis")] == 0
    assert bus.registers[("Torque_Enable", "lift_axis")] == 0
    assert not bus.is_connected


@pytest.mark.parametrize("first_backstep_sample", [1, 6])
def test_relief_one_count_allowance_cannot_accumulate_downward_drift(
    operating_robot, capsys, first_backstep_sample,
):
    robot, _ = operating_robot
    bus = robot.left_bus
    setup = None
    samples = 0

    def hook(register):
        nonlocal setup, samples
        operation = getattr(robot, "_lift_operation", None)
        if operation is None:
            return
        if register == "Present_Position" and operation.reader.phase == "relief_setup":
            setup = round(bus.position)
        if operation.reader.phase == "relief" and bus.registers[("Goal_Velocity", "lift_axis")] == -200:
            if register == "Present_Position":
                samples += 1
                # Each step is only one count, but the second exceeds the fixed best-position band.
                bus.position = setup + max(0, samples - first_backstep_sample + 1)
                bus.registers[(register, "lift_axis")] = round(bus.position)
            elif register == "Present_Velocity":
                bus.read_sequences[(register, "lift_axis")] = [0]

    bus.hook = hook
    with pytest.raises(RuntimeError, match="AlohaMini motor activation failed") as failure:
        robot.connect(calibrate=False)
    assert "unexpected downward direction" in str(failure.value.__cause__)
    records = operational_records(capsys)
    rejected = next(r for r in records if r.get("rejected") and r["phase"] == "relief")
    assert samples == first_backstep_sample + 1
    assert rejected["present_position_raw"] == setup + 2
    assert not any(r["phase"] == "operational_ready" for r in records)
    assert bus.registers[("Goal_Velocity", "lift_axis")] == 0
    assert bus.registers[("Torque_Enable", "lift_axis")] == 0
    assert not bus.is_connected


def wrap_fake_lift_encoder(bus):
    """Keep synthetic mechanical travel continuous but return real 12-bit positions."""
    def hook(register):
        if register == "Present_Position":
            bus.registers[(register, "lift_axis")] = round(bus.position) % 4096
    bus.hook = hook


@pytest.mark.parametrize("distance_ticks", [100, 6000, 29200])
def test_normal_home_stops_at_bottom_across_the_existing_travel_range(
    operating_robot, capsys, distance_ticks,
):
    robot, _ = operating_robot
    bus = robot.left_bus
    original_config = robot.lift.cfg
    bus.bottom = bus.position + distance_ticks
    wrap_fake_lift_encoder(bus)

    robot.connect(calibrate=False)

    records = operational_records(capsys)
    homes = [r for r in records if r["phase"] == "home_complete"]
    assert len(homes) == 1
    # Hand-derived at the unchanged 200 ticks/s: 0.5, 30, and 146 seconds.
    # Even a near-bottom start must stop promptly, not wait for the new deadline.
    assert distance_ticks / 200 <= homes[0]["result"]["elapsed_s"] < distance_ticks / 200 + 0.3
    assert 9.5 <= robot._lift_operation.height_mm <= 12
    assert robot.lift._z0_deg == pytest.approx(-distance_ticks * 360 / 4096)
    assert original_config.home_timeout_s == 20  # Do not mutate the legacy/diagnostic config.
    writes = [e[2:] for e in bus.events if e[1] == "write"]
    assert writes.count(("Goal_Velocity", "lift_axis", 200)) == 1
    assert writes.count(("Goal_Velocity", "lift_axis", -200)) == 1
    robot.disconnect()
    assert bus.registers[("Goal_Velocity", "lift_axis")] == 0
    assert bus.registers[("Torque_Enable", "lift_axis")] == 0
    assert not bus.is_connected


@pytest.mark.parametrize("boundary", ["deadline", "travel"])
def test_normal_home_without_bottom_still_refuses_at_time_or_travel_bound(
    operating_robot, monkeypatch, capsys, boundary,
):
    robot, clock = operating_robot
    bus = robot.left_bus
    bus.bottom = 1000000  # Synthetic absent endstop, not a new configured travel limit.
    wrap_fake_lift_encoder(bus)
    if boundary == "deadline":
        refresh = bus.refresh_lift_state

        def slow_progress():
            if bus.registers[("Goal_Velocity", "lift_axis")] > 0:
                # Model 60 ticks/s: still >2 ticks/poll, but <600 mm after 180 s.
                bus.last_update = clock.now - (clock.now - bus.last_update) * 0.3
            refresh()

        monkeypatch.setattr(bus, "refresh_lift_state", slow_progress)

    with pytest.raises(RuntimeError) as caught:
        robot.connect(calibrate=False)

    records = operational_records(capsys)
    rejection = next(r for r in records if r.get("rejected"))
    if boundary == "deadline":
        assert "timed out" in str(caught.value.__cause__)
        assert 180 <= rejection["elapsed_s"] < 181
    else:
        assert "maximum travel" in str(caught.value.__cause__)
        assert 600 < abs(rejection["homing_displacement_mm"]) < 600.5
        assert 146 < rejection["elapsed_s"] < 148
    assert caught.value.__cause__ is robot._lift_operation.failure
    assert not any(r["phase"] in ("home_complete", "relief", "operational_ready") for r in records)
    assert any(r["phase"] == "shutdown_verified" for r in records)
    assert not robot.lift.is_homed
    assert bus.registers[("Goal_Velocity", "lift_axis")] == 0
    assert bus.registers[("Torque_Enable", "lift_axis")] == 0
    assert not bus.is_connected


@pytest.mark.parametrize("fault,reason", [("temperature", "3/5"), ("status", "status"), ("transport", "grouped feedback")])
def test_normal_home_faults_after_twenty_seconds_still_stop_before_relief(
    operating_robot, capsys, fault, reason,
):
    robot, clock = operating_robot
    bus = robot.left_bus
    bus.bottom = 1000000
    wrap_fake_lift_encoder(bus)
    encoder_hook = bus.hook
    start = clock.now

    def hook(register):
        encoder_hook(register)
        if clock.now - start >= 30 and bus.registers[("Goal_Velocity", "lift_axis")] > 0:
            if fault == "temperature" and register == "Present_Temperature":
                bus.registers[(register, "lift_axis")] = 60  # Synthetic sustained high.
            elif fault == "status" and register == "Status":
                bus.registers[(register, "lift_axis")] = 4
            elif fault == "transport" and register == "Present_Temperature":
                raise OSError("synthetic communication loss after 30 seconds")
        elif register == "Status":
            bus.registers[(register, "lift_axis")] = 0

    bus.hook = hook
    with pytest.raises(RuntimeError) as caught:
        robot.connect(calibrate=False)

    assert reason in str(caught.value.__cause__)
    assert 30 <= clock.now - start < 31
    records = operational_records(capsys)
    assert not any(r["phase"] in ("home_complete", "relief", "operational_ready") for r in records)
    assert any(r["phase"] == "shutdown_verified" for r in records)
    assert bus.registers[("Goal_Velocity", "lift_axis")] == 0
    assert bus.registers[("Torque_Enable", "lift_axis")] == 0
    assert not bus.is_connected


@pytest.mark.parametrize("temperatures,stops", [
    ([37, 37, 93, 37, 37, 37, 37], False),
    ([40, 50, 55, 57, 59], True),  # Synthetic sustained/rising temperature, not trace continuation.
    ([40, 80, 40, 80, 80], True),  # Synthetic densely repeated high readings.
])
def test_five_fresh_real_temperatures_confirm_majority_only(temperatures, stops):
    from lerobot.robots.alohamini.lift_operational import TemperatureWindow
    from lerobot.robots.alohamini.lift_motor_feedback import ComparisonRefusal

    window = TemperatureWindow()
    failure = None
    for index, temperature in enumerate(temperatures):
        try:
            window.update(temperature, index * 0.1)
        except ComparisonRefusal as error:
            failure = error
            break
    assert (failure is not None) is stops
    if stops:
        with pytest.raises(ComparisonRefusal):
            window.update(37, 0.8)  # A later normal value cannot re-arm a fault.
    else:
        assert window.outliers == 1


def test_temperature_baseline_is_real_five_samples_and_never_resets_at_phase_change():
    from lerobot.robots.alohamini.lift_operational import TemperatureWindow
    from lerobot.robots.alohamini.lift_motor_feedback import ComparisonRefusal

    window = TemperatureWindow()
    for index, temperature in enumerate([39, 39, 39, 60]):
        window.update(temperature, index * 0.1, cold_start=True)
        assert not window.ready
    window.update(60, 0.4, cold_start=True)
    assert window.ready  # Real cool majority; two numeric outliers remain evidence.
    with pytest.raises(ComparisonRefusal, match="55"):
        window.update(60, 0.5)  # Third high spans before_torque -> homing, no reset.


def test_fast_read_burst_cannot_manufacture_a_five_slot_cold_baseline():
    from lerobot.robots.alohamini.lift_operational import TemperatureWindow
    from lerobot.robots.alohamini.lift_motor_feedback import ComparisonRefusal

    window = TemperatureWindow()
    for index in range(5):
        window.update(37, index * 0.001, cold_start=True)
    assert not window.ready
    with pytest.raises(ComparisonRefusal, match="baseline"):
        window.assert_fresh(0.004)


def test_multiple_high_readings_in_one_slot_count_once_and_lows_do_not_erase_peak():
    from lerobot.robots.alohamini.lift_operational import TemperatureWindow

    window = TemperatureWindow()
    for index in range(5):
        window.update(37, index * 0.1, cold_start=True)
    for at_s, temperature in [(0.51, 58), (0.52, 81), (0.53, 73), (0.54, 37)]:
        result = window.update(temperature, at_s)
    assert result["high_count"] == 1
    assert result["values_c"] == [37, 37, 37, 37, 81]
    assert window.outliers == 3
    assert window.ready


def test_high_in_three_slots_cannot_be_diluted_by_fast_low_readings():
    from lerobot.robots.alohamini.lift_operational import TemperatureWindow
    from lerobot.robots.alohamini.lift_motor_feedback import ComparisonRefusal

    window = TemperatureWindow()
    for index in range(5):
        window.update(37, index * 0.1)
    for at_s, temperature in [
        (0.51, 60), (0.52, 37), (0.53, 37), (0.54, 37),
        (0.61, 60), (0.62, 37), (0.63, 37), (0.64, 37),
    ]:
        window.update(temperature, at_s)
    with pytest.raises(ComparisonRefusal, match="3/5") as caught:
        window.update(60, 0.71)
    with pytest.raises(ComparisonRefusal) as repeated:
        window.update(37, 0.72)
    assert repeated.value is caught.value
    assert window.outliers == 3  # Include the raw reading which actually caused the stop.


def test_lower_reading_in_same_slot_cannot_refresh_retained_peak_age():
    from lerobot.robots.alohamini.lift_operational import TemperatureWindow
    from lerobot.robots.alohamini.lift_motor_feedback import ComparisonRefusal

    window = TemperatureWindow()
    window.update(60, 0.0)
    window.update(37, 0.09)
    for index in range(1, 5):
        window.update(37, index * 0.1)
    # The last raw reading is fresh, but the genuine retained high is too old
    # for an action. Treating it as a new reading at 0.09 would incorrectly pass.
    with pytest.raises(ComparisonRefusal, match="stale"):
        window.assert_fresh(0.500001)


def test_raw_timestamp_must_advance_even_when_last_slot_peak_does_not():
    from lerobot.robots.alohamini.lift_operational import TemperatureWindow
    from lerobot.robots.alohamini.lift_motor_feedback import ComparisonRefusal

    window = TemperatureWindow()
    for index in range(5):
        window.update(37, index * 0.1)
    window.update(60, 0.41)
    window.update(37, 0.49)
    with pytest.raises(ComparisonRefusal, match="backward"):
        window.update(37, 0.45)  # Newer than the peak, older than the last RAW reply.


@pytest.mark.parametrize("hz", [10, 20, 30, 100])
def test_synthetic_sustained_heat_stops_in_third_high_time_slot_at_each_rate(hz):
    from lerobot.robots.alohamini.lift_operational import TemperatureWindow
    from lerobot.robots.alohamini.lift_motor_feedback import ComparisonRefusal

    window = TemperatureWindow()
    # Synthetic continuation, NOT inferred from post-shutdown normal feedback.
    with pytest.raises(ComparisonRefusal, match="3/5"):
        for index in range(hz + 1):
            at_s = index / hz
            window.update(60 if at_s >= 0.61 else 37, at_s)
    assert 0.8 <= at_s <= 0.9


def test_recorded_pre_stop_temperature_cluster_occupies_two_slots_without_invented_continuation():
    from lerobot.robots.alohamini.lift_operational import TemperatureWindow

    # September 26 measured samples up to the old refusal ONLY; no cleanup lows
    # are used to predict what would have happened under continued motion.
    samples = [
        (0.007, 33), (0.059, 33), (0.111, 33), (0.163, 33), (0.215, 33),
        (0.221, 33), (0.224, 33), (0.276, 33), (0.328, 33), (0.380, 33),
        (0.432, 33), (0.487, 33), (0.539, 33), (0.591, 33), (0.643, 33),
        (0.695, 33), (0.747, 33), (0.799, 33), (0.851, 33), (0.903, 37),
        (0.955, 33), (1.007, 33), (1.059, 33), (1.111, 33), (1.163, 33),
        (1.215, 33), (1.267, 33), (1.319, 58), (1.371, 33), (1.423, 73),
        (1.475, 81),
    ]
    window = TemperatureWindow()
    for at_s, temperature in samples:
        result = window.update(temperature, at_s)
    assert result["high_count"] == 2
    assert result["values_c"] == [33, 33, 33, 58, 81]
    assert result["span_s"] <= 0.5
    assert window.outliers == 3


def test_normal_activation_requires_real_temperature_slots_before_any_nonzero_goal(
    operating_robot, capsys,
):
    robot, _ = operating_robot
    robot.connect(calibrate=False)
    records = operational_records(capsys)
    baseline = [r for r in records if r["phase"] == "baseline"]
    assert baseline[-1]["sample_monotonic_s"] - baseline[0]["sample_monotonic_s"] >= 0.4
    assert baseline[-1]["temperature_window"]["ready"]
    assert all(r["torque_enable"] == 0 and r["goal_velocity_raw"] == 0 for r in baseline)
    first_motion = next(r for r in records if r.get("goal_velocity_raw"))
    assert first_motion["sample_monotonic_s"] > baseline[-1]["sample_monotonic_s"]
    robot.disconnect()


@pytest.mark.parametrize("fault", ["gap", "old_window", "nan", "duplicate", "missing"])
def test_bad_or_stale_data_cannot_become_normal_temperature(fault):
    from lerobot.robots.alohamini.lift_operational import TemperatureWindow
    from lerobot.robots.alohamini.lift_motor_feedback import ComparisonRefusal

    window = TemperatureWindow()
    for index in range(5):
        window.update(37, index * 0.1)
    with pytest.raises(ComparisonRefusal):
        if fault == "gap":
            window.update(37, 0.901)
        elif fault == "old_window":
            window.update(37, 0.61)  # Last five span 0.51 s, even though last sample gap <0.5.
        elif fault == "nan":
            window.update(float("nan"), 0.45)
        elif fault == "duplicate":
            window.update(37, 0.4)
        else:
            window.assert_fresh(0.901)


def test_warm_cold_start_refuses_before_any_torque_enable(operating_robot):
    robot, _ = operating_robot
    robot.left_bus.registers[("Present_Temperature", "lift_axis")] = 45
    with pytest.raises(RuntimeError):
        robot.connect(calibrate=False)
    assert not any(e[1:3] == ("write", "Torque_Enable") and e[-1] == 1 for e in robot.left_bus.events)
    assert not robot.left_bus.is_connected


@pytest.mark.parametrize("phase", ["baseline", "homing", "relief", "live"])
def test_isolated_numeric_spike_in_each_normal_phase_preserves_raw_evidence(
    operating_robot, monkeypatch, capsys, phase,
):
    from lerobot.robots.alohamini import lift_motor_feedback as feedback

    robot, clock = operating_robot
    original = feedback.MotorFeedbackComparison.sample
    injected = []

    def sample(monitor, current_phase, **kwargs):
        if current_phase == phase and not injected:
            robot.left_bus.registers[("Present_Temperature", "lift_axis")] = 93
            injected.append(True)
        try:
            return original(monitor, current_phase, **kwargs)
        finally:
            robot.left_bus.registers[("Present_Temperature", "lift_axis")] = 37

    monkeypatch.setattr(feedback.MotorFeedbackComparison, "sample", sample)
    robot.connect(calibrate=False)
    for _ in range(15):
        clock.sleep(1 / 30)
        robot._lift_operation.poll()
    assert injected
    records = operational_records(capsys)
    high = [r for r in records if r.get("temperature_c") == 93 and r["phase"] == phase]
    assert high and all(not r.get("rejected") for r in high)
    assert robot.lift.is_homed
    robot.disconnect()


def test_synthetic_sustained_high_during_home_stops_and_closes_before_relief(
    operating_robot, monkeypatch, capsys,
):
    from lerobot.robots.alohamini import lift_motor_feedback as feedback

    robot, _ = operating_robot
    original = feedback.MotorFeedbackComparison.sample

    def sample(monitor, phase, **kwargs):
        if phase == "homing":
            robot.left_bus.registers[("Present_Temperature", "lift_axis")] = 60
        return original(monitor, phase, **kwargs)

    monkeypatch.setattr(feedback.MotorFeedbackComparison, "sample", sample)
    with pytest.raises(RuntimeError) as caught:
        robot.connect(calibrate=False)
    assert "3/5" in str(caught.value.__cause__)
    assert robot.left_bus.registers[("Torque_Enable", "lift_axis")] == 0
    assert robot.left_bus.registers[("Goal_Velocity", "lift_axis")] == 0
    assert not robot.left_bus.is_connected
    assert not robot.lift.is_homed
    records = operational_records(capsys)
    assert any(r.get("rejected") and r["temperature_c"] == 60 for r in records)
    assert not any(r["phase"] == "relief" for r in records)
    assert any(r["phase"] == "shutdown_verified" for r in records)


def poll_idle_feedback(robot, clock, *, velocity=0, position=None, moving=None):
    """A fresh grouped fake-bus transaction; the real monitor and clock still run."""
    if position is not None:
        robot.left_bus.position = position
    robot.left_bus.read_sequences[("Present_Velocity", "lift_axis")] = [velocity]
    if moving is not None:
        robot.left_bus.read_sequences[("Moving", "lift_axis")] = [moving]
    clock.sleep(1 / 30)
    robot._lift_operation.poll()


@pytest.fixture
def qualified_idle(operating_robot):
    robot, clock = operating_robot
    robot.connect(calibrate=False)
    for _ in range(45):  # A genuine stopped history, more than one second after zero.
        poll_idle_feedback(robot, clock)
    try:
        yield robot, clock
    finally:
        if robot.left_bus.is_connected:
            robot._safe_shutdown(close_buses=True)


@pytest.mark.parametrize("offsets", [
    [0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, -1],  # Retained refusal's relative positions.
    [0, 4, 0, 4], [0, -4, 0, -4],
])
def test_operational_idle_span_accepts_bounded_variation_without_new_motion_or_filtering(
    qualified_idle, capsys, offsets,
):
    robot, clock = qualified_idle
    op = robot._lift_operation
    origin = op.last_record["present_position_raw"]
    anchor = op._last_idle_height
    zero = robot.lift._z0_deg
    before = len(robot.left_bus.events)
    reads = len(op.transport.group_reads)
    for offset in offsets:
        poll_idle_feedback(robot, clock, position=origin + offset)
        robot.send_action({"x.vel": 0, "y.vel": 0, "theta.vel": 0, "lift_axis.vel": 0})
    assert op.failure is None
    assert op._last_idle_height == anchor
    assert robot.lift._z0_deg == zero
    assert len(op.transport.group_reads) == reads + len(offsets)
    assert [r["present_position_raw"] for r in operational_records(capsys) if r["phase"] == "live"][-len(offsets):] == [origin + d for d in offsets]
    writes = [e for e in robot.left_bus.events[before:] if e[1:3] == ("write", "Goal_Velocity")]
    assert writes and all(e[-1] == 0 for e in writes)
    assert op.bus.expected_goal == 0


@pytest.mark.parametrize("offset", [-5, 5])
def test_operational_idle_span_next_count_refuses_before_further_action(qualified_idle, offset):
    robot, clock = qualified_idle
    op = robot._lift_operation
    origin = op.last_record["present_position_raw"]
    with pytest.raises(RuntimeError, match="unexpected stationary lift motion") as caught:
        poll_idle_feedback(robot, clock, position=origin + offset)
    before = len(robot.left_bus.events)
    with pytest.raises(RuntimeError) as again:
        robot.send_action({"x.vel": 1, "y.vel": 0, "theta.vel": 0, "lift_axis.vel": 200})
    assert again.value is caught.value is op.failure
    assert len(robot.left_bus.events) == before
    assert robot._safe_shutdown(close_buses=True) == []
    assert robot.left_bus.registers[("Goal_Velocity", "lift_axis")] == 0
    assert robot.left_bus.registers[("Torque_Enable", "lift_axis")] == 0


def test_operational_idle_span_uses_configured_conversion_without_rounding_up(operating_robot):
    from dataclasses import replace

    robot, clock = operating_robot
    robot.lift.cfg = replace(robot.lift.cfg, output_gear_ratio=2)
    robot.connect(calibrate=False)
    op = robot._lift_operation
    for _ in range(8):
        poll_idle_feedback(robot, clock)
    origin = op.last_record["present_position_raw"]
    # 168/4096 mm/count: two counts fit 0.10mm, three do not.
    poll_idle_feedback(robot, clock, position=origin + 2)
    op.apply_action({"lift_axis.vel": 0})
    with pytest.raises(RuntimeError, match="unexpected stationary lift motion"):
        poll_idle_feedback(robot, clock, position=origin + 3)
    assert robot._safe_shutdown(close_buses=True) == []


def test_operational_idle_span_handles_encoder_wrap_in_new_commanded_idle_episode(qualified_idle):
    robot, clock = qualified_idle
    op = robot._lift_operation
    zero = robot.lift._z0_deg
    robot.left_bus.bottom = 5000  # Permit fake physical positions up to the encoder wrap.
    op.apply_action({"lift_axis.vel": 200})
    poll_idle_feedback(robot, clock, velocity=-200)
    op.apply_action({"lift_axis.vel": 0})
    for _ in range(8):
        poll_idle_feedback(robot, clock, position=4094)
    anchor = op._last_idle_height
    for position in [4095, 0, 1, 2, 4094]:  # Four-count span across 4095/0.
        poll_idle_feedback(robot, clock, position=position)
        op.apply_action({"lift_axis.vel": 0})
    assert op.failure is None
    assert op._last_idle_height == anchor
    assert robot.lift._z0_deg == zero
    with pytest.raises(RuntimeError, match="unexpected stationary lift motion"):
        poll_idle_feedback(robot, clock, position=3)  # Five-count span.


def test_operational_idle_span_slow_drift_keeps_anchor_across_zero_and_recovery(
    qualified_idle, monkeypatch,
):
    from lerobot.robots.alohamini.alohamini_host import AM1LocalControl, AM1_LOCAL_CONTROL_KEY

    robot, clock = qualified_idle
    op = robot._lift_operation
    origin, anchor = op.last_record["present_position_raw"], op._last_idle_height
    control = AM1LocalControl()
    zero = {"x.vel": 0, "y.vel": 0, "theta.vel": 0, "lift_axis.vel": 0}
    # This fixture has no arms. Their measured hold does not alter lift state;
    # the real host recovery state machine, stop_motion and lift writes still run.
    monkeypatch.setattr(robot, "hold_follower_arms", lambda: None)
    epoch = 0
    control.apply(robot, {**zero, AM1_LOCAL_CONTROL_KEY: {"version": 1, "mode": "active", "epoch": epoch}})
    with pytest.raises(RuntimeError, match="unexpected stationary lift displacement") as caught:
        for offset in range(1, 27):
            for _ in range(7):  # <=3 counts/window, but whole-idle drift cannot re-anchor.
                poll_idle_feedback(robot, clock, position=origin - offset)
                control.apply(robot, {**zero, AM1_LOCAL_CONTROL_KEY: {"version": 1, "mode": "active", "epoch": epoch}})
                assert op._last_idle_height == anchor
            if offset % 4 == 0:
                control.apply(robot, {AM1_LOCAL_CONTROL_KEY: {"version": 1, "mode": "pause", "epoch": epoch + 1}})
                epoch += 2
                control.apply(robot, {**zero, AM1_LOCAL_CONTROL_KEY: {"version": 1, "mode": "active", "epoch": epoch}})
                assert op._last_idle_height == anchor
    assert offset == 25  # 24*84/4096 <0.5mm; 25*84/4096 >0.5mm.
    assert op.failure is caught.value
    assert op.bus.expected_goal == 0
    assert robot._safe_shutdown(close_buses=True) == []


def test_operational_idle_span_velocity_uncertainty_retains_allowance_and_anchor(qualified_idle):
    robot, clock = qualified_idle
    op = robot._lift_operation
    origin, anchor = op.last_record["present_position_raw"], op._last_idle_height
    for offset in (0, 1, 2):
        poll_idle_feedback(robot, clock, position=origin + offset, velocity=50)
    assert op._idle_uncertain
    for offset in (3, 4, 4, 3, 2, 1, 0):
        poll_idle_feedback(robot, clock, position=origin + offset)
        op.apply_action({"lift_axis.vel": 0})
    assert op.failure is None and not op._idle_uncertain
    assert op._last_idle_height == anchor


def test_operational_idle_span_uncertainty_rejects_five_count_episode(qualified_idle):
    robot, clock = qualified_idle
    op = robot._lift_operation
    origin = op.last_record["present_position_raw"]
    for offset in (0, 1, 2):
        poll_idle_feedback(robot, clock, position=origin + offset, velocity=50)
    poll_idle_feedback(robot, clock, position=origin + 4)
    with pytest.raises(RuntimeError, match="displaced during stopped-feedback uncertainty"):
        poll_idle_feedback(robot, clock, position=origin + 5)
    assert op.bus.expected_goal == 0


@pytest.mark.parametrize("phase", ["baseline", "cleanup_readback"])
def test_operational_idle_allowance_does_not_relax_strict_monitor(qualified_idle, phase):
    robot, _ = qualified_idle
    op = robot._lift_operation
    origin = op.last_record["present_position_raw"]
    robot.left_bus.read_sequences[("Present_Position", "lift_axis")] = [origin, origin + 2] * 20
    with pytest.raises(RuntimeError):
        op.monitor.qualify_stationary(phase, expected_torque=1, expected_goal=0)
    assert op.bus.expected_goal == 0
    robot.left_bus.read_sequences[("Present_Position", "lift_axis")] = []


@pytest.mark.parametrize("velocity", [-50, 50])
def test_stopped_velocity_uncertainty_requalifies_without_motion_or_extra_reads(
    qualified_idle, capsys, velocity,
):
    robot, clock = qualified_idle
    op = robot._lift_operation
    origin = op.last_record["present_position_raw"]
    read_count = len(op.transport.group_reads)
    for position in (origin, origin + 1, origin + 1):
        poll_idle_feedback(robot, clock, velocity=velocity, position=position)
        op.apply_action({"lift_axis.vel": 0})
    # Synthetic normal continuation, not a claim about readings after the real shutdown.
    for _ in range(7):
        poll_idle_feedback(robot, clock, position=origin)
    assert len(op.transport.group_reads) == read_count + 10
    assert op.failure is None
    assert op.bus.expected_goal == 0
    records = operational_records(capsys)
    assert sum(r["phase"] == "idle_velocity_uncertain" for r in records) == 1
    assert sum(r["phase"] == "idle_requalified" for r in records) == 1
    assert sum(r["phase"] == "live" and r.get("present_velocity_raw") == velocity for r in records) == 3
    robot.disconnect()


@pytest.mark.parametrize("velocity", [-50, 50])
def test_persistent_idle_uncertainty_cannot_restart_its_deadline(qualified_idle, velocity):
    robot, clock = qualified_idle
    op = robot._lift_operation
    for _ in range(2):
        poll_idle_feedback(robot, clock, velocity=velocity)
    last_valid_window = clock.now
    with pytest.raises(RuntimeError, match="within 1 s") as caught:
        for index in range(40):
            # A lone normal velocity cannot manufacture a complete stopped window.
            poll_idle_feedback(robot, clock, velocity=0 if index % 4 == 3 else velocity)
            op.apply_action({"lift_axis.vel": 0})
    assert 1.0 <= clock.now - last_valid_window <= 1.036
    assert op.failure is caught.value
    assert op.bus.expected_goal == 0
    with pytest.raises(RuntimeError) as repeated:
        poll_idle_feedback(robot, clock)  # No automatic recovery after the deadline.
    assert repeated.value is caught.value
    assert robot._safe_shutdown(close_buses=True) == []


@pytest.mark.parametrize("action", [{"lift_axis.vel": 200}, {"lift_axis.vel": -200}, {"lift_axis.height_mm": 20}])
def test_idle_uncertainty_refuses_new_lift_motion_before_any_write(qualified_idle, action):
    robot, clock = qualified_idle
    op = robot._lift_operation
    for _ in range(3):
        poll_idle_feedback(robot, clock, velocity=-50)
    before = len(robot.left_bus.events)
    with pytest.raises(RuntimeError, match="uncertain") as caught:
        op.apply_action(action)
    assert op.failure is caught.value
    assert len(robot.left_bus.events) == before
    assert op.bus.expected_goal == 0
    assert robot._safe_shutdown(close_buses=True) == []


@pytest.mark.parametrize("fault", ["drift", "velocity", "moving", "current", "voltage", "temperature", "status", "transport", "stale"])
def test_idle_uncertainty_never_defers_real_faults(qualified_idle, fault):
    robot, clock = qualified_idle
    op = robot._lift_operation
    for _ in range(3):
        poll_idle_feedback(robot, clock, velocity=-50)
    origin = op.last_record["present_position_raw"]
    started = clock.now
    if fault == "current":
        robot.left_bus.rest_current = 31  # Synthetic 201.5 mA.
    elif fault == "voltage":
        robot.left_bus.registers[("Present_Voltage", "lift_axis")] = 10
    elif fault == "temperature":
        robot.left_bus.registers[("Present_Temperature", "lift_axis")] = 60  # Synthetic sustained heat.
    elif fault == "status":
        robot.left_bus.registers[("Status", "lift_axis")] = 4
    elif fault == "transport":
        robot.left_bus.read_sequences[("Present_Position", "lift_axis")] = [RuntimeError("bad packet")]
    elif fault == "stale":
        clock.sleep(0.501)
    with pytest.raises(RuntimeError) as caught:
        for index in range(10 if fault == "temperature" else 3):
            poll_idle_feedback(
                robot, clock,
                velocity=0 if fault == "moving" else (-51 if fault == "velocity" else -50),
                moving=1 if fault == "moving" else None,
                position=origin + index + 5 if fault == "drift" else None,  # Exceeds 0.10mm allowance.
            )
    assert clock.now - started < 0.6  # Never waits out the velocity-uncertainty second.
    assert op.failure is caught.value
    assert op.bus.expected_goal == 0
    robot.left_bus.registers[("Status", "lift_axis")] = 0
    robot.left_bus.registers[("Present_Voltage", "lift_axis")] = 120
    assert robot._safe_shutdown(close_buses=True) == []


@pytest.mark.parametrize("fault", ["idle_current", "stationary_motion", "stale", "late_reply"])
def test_live_current_motion_freshness_and_budget_faults_are_terminal(operating_robot, fault):
    robot, clock = operating_robot
    robot.connect(calibrate=False)
    op = robot._lift_operation
    original_read = op.transport.read_group
    if fault == "idle_current":
        robot.left_bus.rest_current = 31  # 201.5 mA, synthetic.
    elif fault == "stale":
        clock.sleep(0.501)
    elif fault == "late_reply":
        def delayed(*args, **kwargs):
            clock.sleep(0.05)
            return original_read(*args, **kwargs)
        op.transport.read_group = delayed
    with pytest.raises(RuntimeError):
        for _ in range(40):
            clock.sleep(1 / 30)
            if fault == "stationary_motion":
                robot.left_bus.position -= 3  # Uncommanded creeping cannot reset its own deadline.
            op.poll()
    assert op.failure is not None
    assert robot._safe_shutdown(close_buses=True) == []


def test_live_uses_paired_height_once_preserves_original_zero_and_real_floor(operating_robot):
    robot, clock = operating_robot
    robot.connect(calibrate=False)
    op = robot._lift_operation
    zero = robot.lift._z0_deg
    # Choose the encoder tick at/below the real floor (5 mm itself is fractional).
    robot.left_bus.position = robot.left_bus.bottom - int(5 * 4096 / 84)
    clock.sleep(0.05)
    op.poll()
    ticks = robot.lift._extended_ticks
    count = len(op.transport.group_reads)
    op.apply_action({"lift_axis.vel": -200})
    assert op.bus.expected_goal == 0
    assert robot.lift._extended_ticks == ticks
    assert robot.lift._z0_deg == zero
    assert len(op.transport.group_reads) == count
    robot.disconnect()


def test_host_fault_context_labels_post_cache_refusal_as_cached_not_accepted(operating_robot, monkeypatch):
    from types import SimpleNamespace
    from lerobot.robots.alohamini import alohamini_host as host
    from lerobot.robots.alohamini.lift_motor_feedback import ComparisonRefusal

    robot, clock = operating_robot
    original_connect = robot.connect

    def connect(**kwargs):
        original_connect(calibrate=False, **kwargs)
        # Synthetic out-of-travel height reaches the actual guard AFTER last_record
        # is cached. That cache must not be mislabeled as operationally accepted.
        monkeypatch.setattr(
            robot._lift_operation, "_height_from_record", lambda record: robot.lift.cfg.soft_max_mm + 1,
        )

    def no_message(*args, **kwargs):
        raise host.zmq.Again()

    monkeypatch.setattr(robot, "connect", connect)
    monkeypatch.setattr(host, "AlohaMini", lambda config: robot)
    monkeypatch.setattr(host.time, "perf_counter", clock.monotonic)
    monkeypatch.setattr("sys.argv", ["host", "--robot_model", "alohamini1", "--no_cameras"])
    monkeypatch.setattr(host, "AlohaMiniHost", lambda config: SimpleNamespace(
        connection_time_s=1, max_loop_freq_hz=30, watchdog_timeout_ms=1000,
        zmq_cmd_socket=SimpleNamespace(recv_string=no_message),
        zmq_observation_socket=SimpleNamespace(recv_multipart=no_message), disconnect=lambda: None,
    ))
    with pytest.raises(ComparisonRefusal, match="lift height exceeded travel bounds") as caught:
        host.main()
    operation = robot._lift_operation
    assert caught.value is operation.failure
    assert operation.last_record["height_mm"] > robot.lift.cfg.soft_max_mm
    note = next(n for n in caught.value.__notes__ if n.startswith("AM1 host loop timing context: "))
    context = json.loads(note.split(": ", 1)[1])
    assert context["last_cached_lift_sample_monotonic_s"] == operation.last_record["sample_monotonic_s"]
    assert "last_accepted_lift_sample_monotonic_s" not in context
    assert not robot.left_bus.is_connected
    assert robot.left_bus.registers[("Goal_Velocity", "lift_axis")] == 0
    assert robot.left_bus.registers[("Torque_Enable", "lift_axis")] == 0


@pytest.mark.parametrize("model,home", [("alohamini1", False), ("alohamini2", True), ("alohamini2pro", True)])
def test_skip_home_and_other_models_never_create_operational_policy(operating_robot, monkeypatch, model, home):
    robot, _ = operating_robot
    robot.config.robot_model = model
    monkeypatch.setattr(robot, "configure", lambda: None)
    calls = []
    monkeypatch.setattr(robot.lift, "home", lambda: calls.append("legacy_home"))
    robot.connect(calibrate=False, home_lift=home)
    assert calls == (["legacy_home"] if home else [])
    assert getattr(robot, "_lift_operation", None) is None
    robot.disconnect()


@pytest.mark.parametrize("fail_action", [False, True])
def test_real_host_polls_before_actions_and_cleans_up_latched_fault_without_swallowing(
    operating_robot, monkeypatch, fail_action,
):
    from types import SimpleNamespace
    from lerobot.robots.alohamini import alohamini_host as host

    robot, clock = operating_robot
    events = []
    primary = RuntimeError("synthetic live lift fault")
    monkeypatch.setattr(host, "AlohaMini", lambda config: robot)
    monkeypatch.setattr(host.time, "perf_counter", clock.monotonic)
    monkeypatch.setattr("sys.argv", ["host", "--robot_model", "alohamini1", "--no_cameras"])
    original_connect = robot.connect

    def connect(**kwargs):
        original_connect(calibrate=False, **kwargs)
        operation = robot._lift_operation
        poll = operation.poll
        def checked_poll(**kwargs):
            events.append("poll")
            if not fail_action and events.count("poll") == 4:
                operation.failure = primary
                raise primary
            poll(**kwargs)
        operation.poll = checked_poll
        apply = operation.apply_action
        def checked_apply(action):
            events.append("action")
            assert events[-2] == "poll"
            if fail_action:
                operation.failure = primary
                raise primary
            apply(action)
        operation.apply_action = checked_apply
    monkeypatch.setattr(robot, "connect", connect)

    class NoRequests:
        def recv_multipart(self, **kwargs):
            raise host.zmq.Again()

    command = json.dumps({"x.vel": 0, "y.vel": 0, "theta.vel": 0, "lift_axis.vel": 0})
    fake_host = SimpleNamespace(
        connection_time_s=1, max_loop_freq_hz=30, watchdog_timeout_ms=1000,
        zmq_cmd_socket=SimpleNamespace(recv_string=lambda flags: command),
        zmq_observation_socket=NoRequests(), disconnect=lambda: events.append("host_closed"),
    )
    monkeypatch.setattr(host, "AlohaMiniHost", lambda config: fake_host)
    with pytest.raises(RuntimeError) as caught:
        host.main()
    assert caught.value is primary
    assert not robot.left_bus.is_connected
    assert robot.left_bus.registers[("Torque_Enable", "lift_axis")] == 0
    assert robot.left_bus.registers[("Goal_Velocity", "lift_axis")] == 0
    assert events[-1] == "host_closed"


def test_live_spike_is_logged_no_abort_and_one_read_no_sleep_per_tick(operating_robot, capsys):
    robot, clock = operating_robot
    robot.connect(calibrate=False)
    operation = robot._lift_operation
    for temp in [37, 37, 93, 37, 37, 37]:
        robot.left_bus.registers[("Present_Temperature", "lift_axis")] = temp
        clock.sleep(1 / 30)
        before = clock.now
        count = len(operation.transport.group_reads)
        operation.poll()
        assert clock.now - before == pytest.approx(0.002)
        assert len(operation.transport.group_reads) == count + 1
        observed = {}
        operation.contribute_observation(observed)
        operation.apply_action({"lift_axis.vel": 0})
        assert len(operation.transport.group_reads) == count + 1  # Cached paired height, no second read.
    records = operational_records(capsys)
    spikes = [r for r in records if r.get("temperature_c") == 93]
    assert len(spikes) == 1 and not spikes[0].get("rejected")
    assert spikes[0]["group_trace"]["response_payload_bytes"]
    assert spikes[0]["temperature_window"]["high_count"] == 1
    assert any(r.get("event") == "temperature_outlier" for r in records)
    assert operation.failure is None
    robot.disconnect()


@pytest.mark.parametrize("hz", [10, 30])
def test_normal_live_rate_has_no_confirmation_wait_or_false_stale_window(operating_robot, hz):
    robot, clock = operating_robot
    robot.connect(calibrate=False)
    op = robot._lift_operation
    for _ in range(hz * 3):
        clock.sleep(1 / hz)
        before = clock.now
        op.poll()
        op.apply_action({"lift_axis.vel": 0})
        assert clock.now - before == pytest.approx(0.002)
    assert op.failure is None
    robot.disconnect()


@pytest.mark.parametrize("log_delay,log_error", [(0.026, False), (0.060, False), (0.501, False), (0, True)])
def test_real_host_consumes_fresh_lift_sample_before_slow_routine_logging(
    operating_robot, monkeypatch, log_delay, log_error,
):
    """Synthetic saved-log delays must not age feedback before its consumers."""
    from types import SimpleNamespace
    from lerobot.robots.alohamini import alohamini_host as host

    robot, clock = operating_robot
    events = []
    records = []
    primary = OSError("synthetic log sink failure")
    monkeypatch.setattr(host, "AlohaMini", lambda config: robot)
    monkeypatch.setattr(host.time, "perf_counter", clock.monotonic)
    monkeypatch.setattr("sys.argv", ["host", "--robot_model", "alohamini1", "--no_cameras"])
    original_connect = robot.connect

    def connect(**kwargs):
        original_connect(calibrate=False, **kwargs)
        op = robot._lift_operation
        emit = op.emit

        def delayed_emit(record):
            if record["phase"] == "live":
                events.append("lift_log")
                records.append(dict(record))
                clock.sleep(log_delay)
                if log_error:
                    raise primary
            emit(record)

        op.emit = delayed_emit

    monkeypatch.setattr(robot, "connect", connect)
    original_action = robot.send_action
    original_observation = robot.get_observation

    def action(values):
        events.append("action")
        return original_action(values)

    def observation():
        events.append("observation")
        clock.sleep(0.006)  # Synthetic finite arm/body-read work, not network delay.
        return original_observation()

    monkeypatch.setattr(robot, "send_action", action)
    monkeypatch.setattr(robot, "get_observation", observation)

    class Requests:
        def recv_multipart(self, **kwargs):
            return [b"client", b"request"]

        def send_multipart(self, parts, **kwargs):
            assert parts[:2] == [b"client", b"request"]
            events.append("reply")

    command = json.dumps({"x.vel": 0, "y.vel": 0, "theta.vel": 0, "lift_axis.vel": 0})
    monkeypatch.setattr(host, "AlohaMiniHost", lambda config: SimpleNamespace(
        connection_time_s=1, max_loop_freq_hz=30, watchdog_timeout_ms=1000,
        zmq_cmd_socket=SimpleNamespace(recv_string=lambda flags: command),
        zmq_observation_socket=Requests(), disconnect=lambda: None,
    ))
    if log_error or log_delay > 0.5:
        with pytest.raises((OSError, RuntimeError)) as caught:
            host.main()
        assert caught.value is robot._lift_operation.failure
        if log_error:
            assert caught.value is primary
        else:
            assert "stale" in str(caught.value)
        assert len(records) == 1  # Next tick refuses before another action/read.
    else:
        host.main()
        assert len(records) >= 10
        assert robot._lift_operation.failure is None
    assert events == ["action", "observation", "reply", "lift_log"] * len(records)
    assert all(r["temperature_window"]["ready"] for r in records)
    assert all(r["temperature_window"]["span_s"] <= 0.5 for r in records)
    assert all(b["sample_monotonic_s"] > a["sample_monotonic_s"] for a, b in zip(records, records[1:]))
    assert all(
        b["sample_monotonic_s"] - a["sample_monotonic_s"] == pytest.approx(log_delay + 0.008)
        for a, b in zip(records, records[1:])
    )  # Logging time counts toward the loop budget; no extra fixed sleep or catch-up.
    assert robot.left_bus.registers[("Goal_Velocity", "lift_axis")] == 0
    assert robot.left_bus.registers[("Torque_Enable", "lift_axis")] == 0
    assert not robot.left_bus.is_connected


@pytest.mark.parametrize("failure_stage", ["observation", "reply", "interrupt"])
@pytest.mark.parametrize("log_error", [False, True])
def test_host_preserves_pending_sample_after_safe_cleanup(operating_robot, monkeypatch, failure_stage, log_error):
    from types import SimpleNamespace
    from lerobot.robots.alohamini import alohamini_host as host

    robot, clock = operating_robot
    events = []
    records = []
    primary = RuntimeError("synthetic consumer fault")
    sink_error = OSError("synthetic final sample log failure")
    original_connect = robot.connect

    def connect(**kwargs):
        original_connect(calibrate=False, **kwargs)
        operation = robot._lift_operation
        emit = operation.emit

        def checked_emit(record):
            if record["phase"] == "live":
                assert events == ["socket_closed"]
                assert not robot.left_bus.is_connected
                assert robot.left_bus.registers[("Goal_Velocity", "lift_axis")] == 0
                assert robot.left_bus.registers[("Torque_Enable", "lift_axis")] == 0
                records.append(dict(record))
                if log_error:
                    raise sink_error
            emit(record)

        operation.emit = checked_emit

    def fail():
        if failure_stage == "interrupt":
            raise KeyboardInterrupt
        robot._lift_operation.failure = primary
        raise primary

    original_observation = robot.get_observation

    def observation():
        return original_observation() if failure_stage == "reply" else fail()

    def no_command(flags):
        raise host.zmq.Again()

    monkeypatch.setattr(robot, "connect", connect)
    monkeypatch.setattr(robot, "get_observation", observation)
    monkeypatch.setattr(host, "AlohaMini", lambda config: robot)
    monkeypatch.setattr(host.time, "perf_counter", clock.monotonic)
    monkeypatch.setattr("sys.argv", ["host", "--robot_model", "alohamini1", "--no_cameras"])
    monkeypatch.setattr(host, "AlohaMiniHost", lambda config: SimpleNamespace(
        connection_time_s=1, max_loop_freq_hz=30, watchdog_timeout_ms=1000,
        zmq_cmd_socket=SimpleNamespace(recv_string=no_command),
        zmq_observation_socket=SimpleNamespace(
            recv_multipart=lambda **kwargs: [b"client", b"request"],
            send_multipart=lambda *args, **kwargs: fail(),
        ),
        disconnect=lambda: events.append("socket_closed"),
    ))
    if failure_stage == "interrupt" and not log_error:
        host.main()  # Retain the host's ordinary clean Ctrl+C behavior.
    else:
        with pytest.raises((RuntimeError, OSError)) as caught:
            host.main()
        assert caught.value is (sink_error if failure_stage == "interrupt" else primary)
        if failure_stage != "interrupt":
            assert robot._lift_operation.failure is primary
        if log_error and failure_stage != "interrupt":
            assert "pending lift sample" in " ".join(caught.value.__notes__)
    assert len(records) == 1
    assert records[0]["sample_monotonic_s"] < clock.now
    assert not robot._lift_operation._pending_samples


@pytest.mark.parametrize("temperatures,fails", [([37, 80, 37, 37, 37], False), ([60] * 5, True)])
def test_cleanup_cannot_relabel_new_sustained_heat_as_clean_success(operating_robot, temperatures, fails):
    robot, _ = operating_robot
    robot.connect(calibrate=False)
    robot.left_bus.read_sequences[("Present_Temperature", "lift_axis")] = temperatures
    if fails:
        with pytest.raises(RuntimeError, match="cleanup issues"):
            robot.disconnect()
    else:
        robot.disconnect()
    assert not robot.left_bus.is_connected
    assert robot.left_bus.registers[("Goal_Velocity", "lift_axis")] == 0
    assert robot.left_bus.registers[("Torque_Enable", "lift_axis")] == 0


@pytest.mark.parametrize("register,value,reason", [
    ("Status", 4, "status"),
    ("Present_Voltage", 10, "voltage"),
    ("Operating_Mode", 0, "mode"),
    ("Present_Current", 400, "current"),
    ("Present_Temperature", IOError("checksum/communication fault"), "grouped feedback"),
])
def test_explicit_faults_never_filtered_and_original_refusal_remains_latched(
    operating_robot, register, value, reason,
):
    robot, clock = operating_robot
    robot.connect(calibrate=False)
    operation = robot._lift_operation
    robot.left_bus.read_sequences[(register, "lift_axis")] = [value]
    clock.sleep(0.05)
    with pytest.raises(RuntimeError, match=reason) as caught:
        operation.poll()
    assert operation.failure is caught.value
    count = len(robot.left_bus.events)
    with pytest.raises(RuntimeError) as repeated:
        operation.apply_action({"lift_axis.vel": 200})
    assert repeated.value is caught.value
    assert len(robot.left_bus.events) == count  # No restart, no nonzero write.
    # Cleanup may report the continuing genuine fault; it must still zero/off/close.
    robot._safe_shutdown(close_buses=True)
    assert operation.failure is caught.value
    assert robot.left_bus.registers[("Goal_Velocity", "lift_axis")] == 0
    assert robot.left_bus.registers[("Torque_Enable", "lift_axis")] == 0
    assert not robot.left_bus.is_connected


@pytest.mark.parametrize("delay_stage", ["sample_log", "robot_observation", "lift_diagnostics", "reports"])
def test_real_host_retains_fault_loop_timings_after_cleanup(
    operating_robot, monkeypatch, delay_stage,
):
    """Synthetic delays identify the measured stage, not the historical gap's cause."""
    from types import SimpleNamespace
    from lerobot.robots.alohamini import alohamini_host as host
    from lerobot.robots.alohamini.lift_motor_feedback import ComparisonRefusal

    robot, clock = operating_robot
    delayed = False
    closed = False
    original_connect = robot.connect
    original_observation = robot.get_observation
    original_report = host.print_cadence_report
    original_add_note = ComparisonRefusal.add_note
    original_loop_timing = host.AM1HostLoopTiming
    timing = None

    def make_timing():
        nonlocal timing
        timing = original_loop_timing()
        return timing

    def delay_once(stage):
        nonlocal delayed
        if stage == delay_stage and not delayed:
            delayed = True
            clock.sleep(0.296)  # Synthetic, not a measured historical log-write duration.

    def connect(**kwargs):
        original_connect(calibrate=False, **kwargs)
        operation = robot._lift_operation
        emit = operation.emit

        def emit_sample(record):
            if record["phase"] == "live":
                delay_once("sample_log")
            emit(record)

        operation.emit = emit_sample

    def observation():
        delay_once("robot_observation")
        return original_observation()

    def report(state):
        delay_once("reports")
        return original_report(state)

    def monotonic():
        if timing is not None and timing.current is not None:
            if timing.current["active_phase"] == "lift_diagnostics":
                delay_once("lift_diagnostics")
        return clock.monotonic()

    def close():
        nonlocal closed
        closed = True

    def add_note(error, note):
        if note.startswith("AM1 host loop timing context: "):
            assert closed and not robot.left_bus.is_connected
            assert robot.left_bus.registers[("Goal_Velocity", "lift_axis")] == 0
            assert robot.left_bus.registers[("Torque_Enable", "lift_axis")] == 0
        original_add_note(error, note)

    def no_request(**kwargs):
        raise host.zmq.Again()

    command = json.dumps({"x.vel": 0, "y.vel": 0, "theta.vel": 0, "lift_axis.vel": 0})
    monkeypatch.setattr(robot, "connect", connect)
    monkeypatch.setattr(robot, "get_observation", observation)
    monkeypatch.setattr(host, "print_cadence_report", report)
    monkeypatch.setattr(host, "AM1HostLoopTiming", make_timing)
    monkeypatch.setattr(ComparisonRefusal, "add_note", add_note)
    monkeypatch.setattr(host, "AlohaMini", lambda config: robot)
    monkeypatch.setattr(host.time, "perf_counter", clock.monotonic)
    monkeypatch.setattr(host.time, "monotonic", monotonic)
    monkeypatch.setattr("sys.argv", [
        "host", "--robot_model", "alohamini1", "--no_cameras", "--profile_cadence",
    ])
    monkeypatch.setattr(host, "AlohaMiniHost", lambda config: SimpleNamespace(
        connection_time_s=2, max_loop_freq_hz=30, watchdog_timeout_ms=1000,
        zmq_cmd_socket=SimpleNamespace(recv_string=lambda flags: command),
        zmq_observation_socket=SimpleNamespace(recv_multipart=no_request), disconnect=close,
    ))
    with pytest.raises(ComparisonRefusal, match="five-slot feedback window is stale") as caught:
        host.main()
    assert caught.value is robot._lift_operation.failure
    assert closed and not robot.left_bus.is_connected
    notes = [n for n in getattr(caught.value, "__notes__", [])
             if n.startswith("AM1 host loop timing context: ")]
    assert len(notes) == 1  # The current host loses the preceding iteration's timing.
    context = json.loads(notes[0].split(": ", 1)[1])
    assert context["clock"] == "perf_counter"
    loop = context["current_loop"] if delay_stage == "robot_observation" else context["previous_loop"]
    assert loop["phase_ms"][delay_stage] >= 296
    if delay_stage == "lift_diagnostics":
        assert loop["phase_ms"]["sleep"] < 50  # The check's delay is not counted as sleep.
    assert context["current_loop"]["active_phase"] == (
        "robot_observation" if delay_stage == "robot_observation" else "lift_poll"
    )
    assert len(loop["phase_ms"]) <= 10  # One bounded current/previous record, never growing history.
    assert "cleanup" not in loop["phase_ms"]


@pytest.mark.parametrize("context_encode_fails", [False, True])
def test_host_fault_timing_cannot_replace_primary_or_cleanup_failure(
    operating_robot, monkeypatch, context_encode_fails,
):
    from types import SimpleNamespace
    from lerobot.robots.alohamini import alohamini_host as host

    robot, clock = operating_robot
    primary = OSError("synthetic primary sample log failure")
    secondary = OSError("synthetic socket cleanup failure")
    original_connect = robot.connect
    original_dumps = json.dumps
    closed = False

    def connect(**kwargs):
        original_connect(calibrate=False, **kwargs)
        operation = robot._lift_operation
        emit = operation.emit

        def failed_emit(record):
            if record["phase"] == "live":
                raise primary
            emit(record)

        operation.emit = failed_emit

    def close():
        nonlocal closed
        closed = True
        raise secondary

    def dumps(value, *args, **kwargs):
        if context_encode_fails and isinstance(value, dict) and value.get("clock") == "perf_counter":
            assert closed and not robot.left_bus.is_connected
            raise ValueError("synthetic fault-context encoding failure")
        return original_dumps(value, *args, **kwargs)

    def no_message(*args, **kwargs):
        raise host.zmq.Again()

    monkeypatch.setattr(robot, "connect", connect)
    monkeypatch.setattr(host, "AlohaMini", lambda config: robot)
    monkeypatch.setattr(host.time, "perf_counter", clock.monotonic)
    monkeypatch.setattr(host.json, "dumps", dumps)
    monkeypatch.setattr("sys.argv", ["host", "--robot_model", "alohamini1", "--no_cameras"])
    monkeypatch.setattr(host, "AlohaMiniHost", lambda config: SimpleNamespace(
        connection_time_s=1, max_loop_freq_hz=30, watchdog_timeout_ms=1000,
        zmq_cmd_socket=SimpleNamespace(recv_string=no_message),
        zmq_observation_socket=SimpleNamespace(recv_multipart=no_message), disconnect=close,
    ))
    with pytest.raises(OSError) as caught:
        host.main()
    assert caught.value is primary and robot._lift_operation.failure is primary
    assert closed and not robot.left_bus.is_connected
    assert robot.left_bus.registers[("Goal_Velocity", "lift_axis")] == 0
    assert robot.left_bus.registers[("Torque_Enable", "lift_axis")] == 0
    notes = getattr(primary, "__notes__", [])
    assert any("host disconnect also failed: OSError: synthetic socket cleanup failure" in n for n in notes)
    if context_encode_fails:
        assert "AM1 host loop timing context unavailable: ValueError" in notes
    else:
        note = next(n for n in notes if n.startswith("AM1 host loop timing context: "))
        assert json.loads(note.split(": ", 1)[1])["current_loop"]["active_phase"] == "sample_log"


@pytest.mark.parametrize("model,home", [("alohamini1", False), ("alohamini2", True), ("alohamini2pro", True)])
def test_host_loop_context_excludes_skip_home_and_other_models(operating_robot, monkeypatch, model, home):
    from types import SimpleNamespace
    from lerobot.robots.alohamini import alohamini_host as host

    robot, clock = operating_robot
    robot.config.robot_model = model
    legacy_home = []
    monkeypatch.setattr(robot, "configure", lambda: None)
    monkeypatch.setattr(robot.lift, "home", lambda: legacy_home.append(True))

    def unexpected_context():
        raise AssertionError("AM1 operational timing must not run in this model/mode")

    def no_message(*args, **kwargs):
        raise host.zmq.Again()

    monkeypatch.setattr(host, "AM1HostLoopTiming", unexpected_context)
    monkeypatch.setattr(host, "AlohaMini", lambda config: robot)
    monkeypatch.setattr(host.time, "perf_counter", clock.monotonic)
    argv = ["host", "--robot_model", model, "--no_cameras", "--no_follower"]
    if not home:
        argv.append("--skip_lift_home")
    monkeypatch.setattr("sys.argv", argv)
    monkeypatch.setattr(host, "AlohaMiniHost", lambda config: SimpleNamespace(
        connection_time_s=0.1, max_loop_freq_hz=30, watchdog_timeout_ms=1000,
        zmq_cmd_socket=SimpleNamespace(recv_string=no_message),
        zmq_observation_socket=SimpleNamespace(recv_multipart=no_message), disconnect=lambda: None,
    ))
    host.main()
    assert legacy_home == ([True] if home else [])
    assert not robot.left_bus.is_connected


def test_host_fault_timing_retains_slow_loop_after_one_healthy_iteration():
    from lerobot.robots.alohamini.alohamini_host import AM1HostLoopTiming

    timing = AM1HostLoopTiming()
    timing.begin(100.0)
    timing.mark("sample_log", 100.002)
    timing.mark("sleep", 100.143)
    timing.finish(100.144)
    timing.begin(100.144)
    timing.mark("sample_log", 100.146)
    timing.finish(100.177)
    timing.begin(100.177)
    context = timing.snapshot(100.179)

    assert context["previous_loop"]["loop_index"] == 2
    assert context["current_loop"]["loop_index"] == 3
    delayed, healthy = context["recent_completed_loops"]
    assert delayed["loop_index"] == 1
    assert delayed["phase_ms"]["sample_log"] == pytest.approx(141)
    assert delayed["completed"] and healthy["completed"]
    assert context["current_loop"]["completed"] is False
    assert context["omitted_completed_loop_count"] == 0


def test_host_fault_timing_history_is_bounded_and_snapshot_is_independent():
    from lerobot.robots.alohamini.alohamini_host import AM1HostLoopTiming

    timing = AM1HostLoopTiming()
    for index in range(100):
        timing.begin(float(index))
        timing.mark("sample_log", index + 0.001)
        timing.finish(index + 0.033)
    context = timing.snapshot(100.0)

    assert len(context["recent_completed_loops"]) == context["completed_loop_history_limit"] == 8
    assert context["omitted_completed_loop_count"] == 92
    assert [r["loop_index"] for r in context["recent_completed_loops"]] == list(range(93, 101))
    context["recent_completed_loops"][-1]["phase_ms"]["sample_log"] = -1
    context["previous_loop"]["phase_ms"]["sample_log"] = -2
    timing.begin(100.0)
    timing.finish(100.033)
    later = timing.snapshot(101.0)
    assert later["recent_completed_loops"][-2]["phase_ms"]["sample_log"] >= 0
    assert later["omitted_completed_loop_count"] == 93
