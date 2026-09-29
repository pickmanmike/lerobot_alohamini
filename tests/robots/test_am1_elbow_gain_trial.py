"""Approved one-joint gain comparison on the real connection/cleanup path, fake I/O."""

from types import SimpleNamespace

import pytest

from tests.robots.test_alohamini_safe_bringup import make_activation_robot


ELBOW = "arm_right_elbow_flex"


def trial_robot(*, enabled=True, model="alohamini1", initial_p=16):
    robot, left, right, events = make_activation_robot()
    robot.config.robot_model = model
    robot._right_elbow_p20_trial_enabled = enabled
    robot.id = "fake-gain-trial"
    right.motors[ELBOW] = SimpleNamespace(id=3, model="sts3215")
    robot.right_arm_motors.append(ELBOW)
    right.registers[("P_Coefficient", ELBOW)] = initial_p
    right.registers[("D_Coefficient", ELBOW)] = 32
    for bus in (left, right):
        bus.is_connected = False
        bus.is_calibrated = True
        bus.connect = lambda bus=bus: setattr(bus, "is_connected", True)
    # Keep real connect/activation/shutdown; replace unrelated default register setup.
    def configure():
        events.append(("configure",))
        right.write("P_Coefficient", ELBOW, 16, normalize=False)
    robot.configure = configure
    return robot, right, events


def p_writes(events):
    return [event for event in events if len(event) == 5 and event[1:4] == ("write", "P_Coefficient", ELBOW)]


def test_trial_verifies_original_applies_only_elbow_and_restores_before_bus_close(capsys):
    robot, bus, events = trial_robot()
    robot.connect(home_lift=False)
    assert bus.registers[("P_Coefficient", ELBOW)] == 20
    assert events.index(("right", "read", "P_Coefficient", ELBOW)) < events.index(("configure",))
    assert events.index(("right", "write", "P_Coefficient", ELBOW, 20)) < events.index(("right", "write", "Torque_Enable", ELBOW, 1))
    robot.disconnect()
    assert [event[-1] for event in p_writes(events)] == [16, 20, 16]
    restore = events.index(("right", "write", "P_Coefficient", ELBOW, 16), events.index(("right", "write", "P_Coefficient", ELBOW, 20)))
    assert ("right", "read", "Torque_Enable", ELBOW) in events[:restore]
    assert ("right", "read", "P_Coefficient", ELBOW) in events[restore + 1:]
    assert restore < events.index(("right", "disconnect", False))
    assert bus.registers[("P_Coefficient", ELBOW)] == 16
    assert '"phase":"restored"' in capsys.readouterr().out


def test_unexpected_original_gain_refuses_before_configure_or_activation():
    robot, bus, events = trial_robot(initial_p=19)
    with pytest.raises(RuntimeError, match="P_Coefficient"):
        robot.connect(home_lift=False)
    assert ("configure",) not in events and p_writes(events) == []
    assert not any(event[1:] == ("write", "Torque_Enable", ELBOW, 1) for event in events)
    assert not bus.is_connected


@pytest.mark.parametrize("model,enabled", [("alohamini1", False), ("alohamini2", True), ("alohamini2pro", True)])
def test_default_and_other_models_never_apply_trial(model, enabled):
    robot, bus, events = trial_robot(enabled=enabled, model=model)
    robot.connect(home_lift=False)
    robot.disconnect()
    assert [event[-1] for event in p_writes(events)] == [16]
    assert not any(event[1:3] == ("read", "P_Coefficient") for event in events)


@pytest.mark.parametrize("failure", [ConnectionError("gain write failed"), KeyboardInterrupt()])
def test_uncertain_gain_write_restores_and_preserves_original_error(failure):
    robot, bus, events = trial_robot()
    bus.write_failures[("P_Coefficient", ELBOW, 20)] = (failure, True)
    with pytest.raises(type(failure)) as caught:
        robot.connect(home_lift=False)
    assert caught.value is failure
    assert [event[-1] for event in p_writes(events)] == [16, 20, 16]
    assert bus.registers[("P_Coefficient", ELBOW)] == 16 and not bus.is_connected
    assert not any(event[1:] == ("write", "Torque_Enable", ELBOW, 1) for event in events)


def test_failed_restoration_is_not_success_and_still_closes_buses():
    robot, bus, events = trial_robot()
    robot.connect(home_lift=False)
    bus.write_failures[("P_Coefficient", ELBOW, 16)] = (ConnectionError("restore refused"), False)
    with pytest.raises(RuntimeError, match="restore refused"):
        robot.disconnect()
    assert not bus.is_connected and bus.registers[("P_Coefficient", ELBOW)] == 20


def test_cleanup_never_restores_gain_with_unverified_torque_off():
    robot, bus, events = trial_robot()
    robot.connect(home_lift=False)
    bus.read_sequences[("Torque_Enable", ELBOW)] = [1]
    with pytest.raises(RuntimeError, match="Torque_Enable"):
        robot.disconnect()
    assert [event[-1] for event in p_writes(events)] == [16, 20]
    assert not bus.is_connected


def test_failed_gain_readback_refuses_before_activation_and_restores():
    robot, bus, events = trial_robot()
    bus.read_sequences[("P_Coefficient", ELBOW)] = [16, 16, 19]
    with pytest.raises(RuntimeError, match="expected 20"):
        robot.connect(home_lift=False)
    assert not any(event[1:] == ("write", "Torque_Enable", ELBOW, 1) for event in events)
    assert bus.registers[("P_Coefficient", ELBOW)] == 16


def test_restore_readback_mismatch_is_not_reported_as_success(capsys):
    robot, bus, events = trial_robot()
    robot.connect(home_lift=False)
    bus.read_sequences[("P_Coefficient", ELBOW)] = [20]
    with pytest.raises(RuntimeError, match="expected 16"):
        robot.disconnect()
    assert '"phase":"restored"' not in capsys.readouterr().out
    assert not bus.is_connected


def test_activation_failure_keeps_cause_and_restores_once():
    robot, bus, events = trial_robot()
    failure = RuntimeError("synthetic activation fault")
    def fail(**kwargs):
        raise failure
    robot.activate_motors = fail
    with pytest.raises(RuntimeError) as caught:
        robot.connect(home_lift=False)
    assert caught.value is failure
    assert [event[-1] for event in p_writes(events)] == [16, 20, 16]


def test_restore_failure_keeps_primary_cause_as_well_as_cleanup_failure():
    robot, bus, events = trial_robot()
    failure = RuntimeError("original activation fault")
    def fail(**kwargs):
        bus.write_failures[("P_Coefficient", ELBOW, 16)] = (ConnectionError("cannot restore"), False)
        raise failure
    robot.activate_motors = fail
    with pytest.raises(RuntimeError) as caught:
        robot.connect(home_lift=False)
    assert caught.value is failure
    assert any("cannot restore" in note for note in failure.__notes__)
    assert not bus.is_connected


def test_host_rejects_trial_without_capture_before_robot_construction(monkeypatch):
    import sys
    from lerobot.robots.alohamini import alohamini_host as host
    monkeypatch.setenv("AM1_RIGHT_ELBOW_P20_TRIAL", "1")
    monkeypatch.delenv("AM1_ARM_TRACKING_READBACK", raising=False)
    monkeypatch.setattr(host, "AlohaMini", lambda config: pytest.fail("constructed before refusal"))
    monkeypatch.setattr(sys, "argv", ["host", "--robot_model", "alohamini1", "--no_cameras"])
    with pytest.raises(SystemExit) as caught:
        host.main()
    assert caught.value.code == 2


def test_actual_host_applies_trial_and_restores_on_socket_setup_failure(monkeypatch):
    import sys
    from lerobot.robots.alohamini import alohamini_host as host
    robot, bus, events = trial_robot(enabled=False)
    monkeypatch.setenv("AM1_RIGHT_ELBOW_P20_TRIAL", "1")
    monkeypatch.setenv("AM1_ARM_TRACKING_READBACK", "1")
    monkeypatch.setenv("AM1_ARM_TRACKING_START", "right-elbow-request")
    monkeypatch.setattr(host, "AlohaMini", lambda config: robot)
    # The separate configuration capture is covered by actual-bus tracking tests.
    from lerobot.robots.alohamini import arm_tracking
    monkeypatch.setattr(arm_tracking, "read_tracking_configuration", lambda robot: None)
    failure = OSError("synthetic socket setup failure")
    def fail(config):
        assert bus.registers[("P_Coefficient", ELBOW)] == 20
        raise failure
    monkeypatch.setattr(host, "AlohaMiniHost", fail)
    monkeypatch.setattr(sys, "argv", ["host", "--robot_model", "alohamini1", "--no_cameras", "--skip_lift_home"])
    with pytest.raises(OSError) as caught:
        host.main()
    assert caught.value is failure
    assert [event[-1] for event in p_writes(events)] == [16, 20, 16]
    assert not bus.is_connected


def test_gain_log_failure_cannot_bypass_restoration(monkeypatch):
    from lerobot.robots.alohamini.arm_gain_trial import RightElbowP20Trial
    robot, bus, events = trial_robot()
    original = RightElbowP20Trial._emit
    failure = OSError("synthetic gain log failure")
    def emit(self, phase, values):
        if phase == "applied_pre_activation":
            raise failure
        return original(self, phase, values)
    monkeypatch.setattr(RightElbowP20Trial, "_emit", emit)
    with pytest.raises(OSError) as caught:
        robot.connect(home_lift=False)
    assert caught.value is failure
    assert bus.registers[("P_Coefficient", ELBOW)] == 16 and not bus.is_connected


def test_trial_never_enters_calibration_if_second_calibration_check_differs():
    robot, bus, events = trial_robot()
    ordinary_configure = robot.configure
    def configure():
        ordinary_configure()
        bus.is_calibrated = False
    robot.configure = configure
    robot.calibrate = lambda: pytest.fail("trial entered calibration")
    with pytest.raises(RuntimeError, match="calibration"):
        robot.connect(home_lift=False)
    assert [event[-1] for event in p_writes(events)] == [16]
    assert not bus.is_connected


@pytest.mark.parametrize("stage", ["zero", "torque", "torque_busy", "readback"])
def test_interrupt_during_cleanup_still_restores_and_closes_then_reraises(stage):
    robot, bus, events = trial_robot()
    robot.connect(home_lift=False)
    failure = KeyboardInterrupt("synthetic cleanup interruption")
    if stage == "readback":
        def readback():
            raise failure
        robot._lift_operation = SimpleNamespace(cleanup_readback=readback)
    else:
        target_bus = robot.left_bus if stage == "zero" else bus
        original = target_bus.write
        once = [True]
        def write(register, motor, value, **kwargs):
            match = (register == "Goal_Velocity") if stage == "zero" else (register == "Torque_Enable" and motor == ELBOW)
            if match and once[0]:
                once[0] = False
                if stage == "torque_busy":
                    target_bus.port_handler.is_using = True
                raise failure
            return original(register, motor, value, **kwargs)
        target_bus.write = write
    with pytest.raises(KeyboardInterrupt) as caught:
        robot.disconnect()
    assert caught.value is failure
    assert bus.registers[("P_Coefficient", ELBOW)] == 16
    assert bus.registers[("Torque_Enable", ELBOW)] == 0
    assert not bus.is_connected and not robot.left_bus.is_connected
