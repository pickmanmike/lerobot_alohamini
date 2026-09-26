"""Normal AM1 host lift policy, with fake clock/mechanics and grouped bytes only."""

from __future__ import annotations

import json

import pytest

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
        bus.sync_read = lambda register, motors: {name: bus.read(register, name) for name in motors}
        bus.sync_write = lambda register, values, **kwargs: [
            bus.write(register, name, value, **kwargs) for name, value in values.items()
        ]
        return bus

    class Transport(GroupedLiftTransport):
        def read_group(self, *args, **kwargs):
            clock.sleep(0.002)  # Synthetic finite transaction time, not instant duplicate samples.
            return super().read_group(*args, **kwargs)

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
            window.update(temperature, index * 0.05)
        except ComparisonRefusal as error:
            failure = error
            break
    assert (failure is not None) is stops
    if stops:
        with pytest.raises(ComparisonRefusal):
            window.update(37, 0.3)  # A later normal value cannot re-arm a fault.
    else:
        assert window.outliers == 1


def test_temperature_baseline_is_real_five_samples_and_never_resets_at_phase_change():
    from lerobot.robots.alohamini.lift_operational import TemperatureWindow
    from lerobot.robots.alohamini.lift_motor_feedback import ComparisonRefusal

    window = TemperatureWindow()
    for index, temperature in enumerate([39, 39, 39, 60]):
        window.update(temperature, index * 0.05, cold_start=True)
        assert not window.ready
    window.update(60, 0.2, cold_start=True)
    assert window.ready  # Real cool majority; two numeric outliers remain evidence.
    with pytest.raises(ComparisonRefusal, match="55"):
        window.update(60, 0.25)  # Third high spans before_torque -> homing, no reset.


@pytest.mark.parametrize("fault", ["gap", "old_window", "nan", "duplicate", "missing"])
def test_bad_or_stale_data_cannot_become_normal_temperature(fault):
    from lerobot.robots.alohamini.lift_operational import TemperatureWindow
    from lerobot.robots.alohamini.lift_motor_feedback import ComparisonRefusal

    window = TemperatureWindow()
    for index in range(5):
        window.update(37, index * 0.05)
    with pytest.raises(ComparisonRefusal):
        if fault == "gap":
            window.update(37, 0.701)
        elif fault == "old_window":
            window.update(37, 0.56)  # Last five span 0.51 s, even though last sample gap <0.5.
        elif fault == "nan":
            window.update(float("nan"), 0.25)
        elif fault == "duplicate":
            window.update(37, 0.2)
        else:
            window.assert_fresh(0.701)


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
        def checked_poll():
            events.append("poll")
            if not fail_action and events.count("poll") == 4:
                operation.failure = primary
                raise primary
            poll()
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
