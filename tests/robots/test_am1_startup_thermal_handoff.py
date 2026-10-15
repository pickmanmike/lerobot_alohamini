"""AM1 startup acquisition scheduling with real thermal policy and sole-owner grouped fake I/O."""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from lerobot.robots.alohamini import (
    alohamini_host as host,
    lift_motor_feedback as feedback,
)
from tests.robots.test_alohamini_lift_operational import operating_robot as _operating_robot

operating_robot = _operating_robot


@pytest.fixture
def startup_window(operating_robot):
    robot, clock = operating_robot
    robot.connect(calibrate=False)
    operation = robot._lift_operation
    robot.left_bus.registers[("Present_Temperature", "lift_axis")] = 34
    origin = operation.temperature._origin_ns / 1e9
    start = origin + math.ceil((clock.now - origin) / 0.1) * 0.1
    # Genuine grouped mocked reads reconstruct the native45d4 occupied-slot
    # offsets. Retain this SAME already-running window/object/origin.
    for offset in (0.067284123, 0.169340880, 0.224324460, 0.328343799, 0.432361898, 0.486275928):
        clock.sleep(start + offset - clock.now - 0.002)
        operation.poll()
    assert len(operation.temperature.samples) == 5
    try:
        yield robot, clock, start + 0.486275928
    finally:
        # Only close fake bookkeeping after deliberate hardfault injections.
        # No cleanup qualification or physical evidence is claimed here.
        for bus in (robot.left_bus, robot.right_bus):
            if bus is not None:
                bus.is_connected = False


def at(clock, when):
    clock.sleep(when - clock.now)


def test_real_activation_and_final_handoff_fill_skipped_slot_without_reset(startup_window):
    robot, clock, first = startup_window
    operation = robot._lift_operation
    window = operation.temperature
    origin = window._origin_ns
    original_peaks = list(window.samples)
    # The two natural activation guards span the original117ms handoff.
    at(clock, first + 0.055)
    robot._require_am1_activation_fresh("after_home")
    at(clock, first + 0.090)
    robot._require_am1_activation_fresh("after_home")
    at(clock, first + 0.117072585 - 0.002)
    operation.poll()
    for elapsed in (0.151013739, 0.184015360):
        at(clock, first + elapsed - 0.002)
        operation.poll()
    assert operation.temperature is window and window._origin_ns == origin
    assert window.failure is None and operation.failure is None
    assert window.MAX_AGE_S == 0.5 and window.CEILING_C == 55
    assert original_peaks[-1] in list(window.samples)
    assert all(clock.now - stamp <= 0.5 for stamp, _ in window.samples)


def test_original_uninterleaved_window_still_refuses_native_clock_pattern(startup_window):
    robot, clock, first = startup_window
    operation = robot._lift_operation
    for elapsed in (0.117072585, 0.151013739):
        at(clock, first + elapsed - 0.002)
        operation.poll()
    at(clock, first + 0.184015360 - 0.002)
    with pytest.raises(feedback.ComparisonRefusal, match="five-slot.*stale"):
        operation.poll()


def test_not_due_startup_boundary_does_not_read_or_stamp_new_evidence(startup_window, monkeypatch):
    robot, clock, first = startup_window
    operation = robot._lift_operation
    old = operation.last_record
    peaks = list(operation.temperature.samples)
    # Remain in the SAME original slot as well as below the raw poll interval.
    at(clock, first + 0.005)
    monkeypatch.setattr(operation.transport, "read_group", lambda *a, **k: pytest.fail("not due"))
    robot._refresh_am1_startup_lift_feedback()
    assert operation.last_record is old and list(operation.temperature.samples) == peaks


@pytest.mark.parametrize("fault", ["status", "current", "reply_budget", "cancel"])
def test_due_startup_acquisition_preserves_original_fault_or_cancellation(startup_window, monkeypatch, fault):
    robot, clock, first = startup_window
    operation = robot._lift_operation
    original = operation.transport.read_group

    def read_group(*a, **k):
        if fault == "cancel":
            raise KeyboardInterrupt("startup cancelled")
        if fault == "reply_budget":
            clock.sleep(0.041)
        return original(*a, **k)

    monkeypatch.setattr(operation.transport, "read_group", read_group)
    if fault == "status":
        robot.left_bus.registers[("Status", "lift_axis")] = 1
    if fault == "current":
        robot.left_bus.rest_current = 330
    at(clock, first + 0.055)
    error_type = KeyboardInterrupt if fault == "cancel" else feedback.ComparisonRefusal
    with pytest.raises(error_type) as caught:
        robot._require_am1_activation_fresh("after_home")
    assert operation.failure is caught.value
    with pytest.raises(error_type) as again:
        robot._refresh_am1_startup_lift_feedback()
    assert again.value is caught.value


def test_preexisting_heat_latch_is_retained_before_any_new_startup_io(startup_window, monkeypatch):
    robot, clock, first = startup_window
    operation = robot._lift_operation
    original = feedback.ComparisonRefusal("temperature majority stop latched")
    operation.temperature.failure = original
    at(clock, first + 0.055)
    monkeypatch.setattr(
        operation.transport, "read_group", lambda *a, **k: pytest.fail("latched heat cannot acquire lows")
    )
    with pytest.raises(feedback.ComparisonRefusal) as caught:
        robot._refresh_am1_startup_lift_feedback()
    assert caught.value is original and operation.temperature.failure is original


@pytest.mark.parametrize("stamp", [None, True, float("nan"), "forged"])
def test_missing_or_malformed_original_acquisition_refused_before_io(startup_window, monkeypatch, stamp):
    robot, _, _ = startup_window
    robot._lift_operation.last_record = {"sample_monotonic_s": stamp}
    monkeypatch.setattr(
        robot._lift_operation.transport, "read_group", lambda *a, **k: pytest.fail("invalid cache")
    )
    with pytest.raises(feedback.ComparisonRefusal):
        robot._refresh_am1_startup_lift_feedback()


def test_expired_raw_feedback_is_not_reacquired_into_permission(startup_window, monkeypatch):
    robot, clock, first = startup_window
    at(clock, first + 0.501)
    monkeypatch.setattr(
        robot._lift_operation.transport, "read_group", lambda *a, **k: pytest.fail("stale raw")
    )
    with pytest.raises(feedback.ComparisonRefusal, match="stale"):
        robot._refresh_am1_startup_lift_feedback()


def test_original_arm_vector_one_second_guard_is_not_renewed_by_lift_read(startup_window):
    robot, clock, first = startup_window
    robot.left_arm_motors = ["synthetic_arm"]
    robot._am1_activation_readbacks = [
        {
            "phase": "after_home",
            "register": "Present_Position",
            "read_started_at": clock.now - 1.01,
            "read_completed_at": clock.now - 1.0,
        }
    ]
    before = [dict(r) for r in robot._am1_activation_readbacks]
    at(clock, first + 0.055)
    with pytest.raises(RuntimeError, match="original raw position vector expired"):
        robot._require_am1_activation_fresh("after_home")
    assert robot._am1_activation_readbacks == before


def test_before_home_and_am2_never_gain_startup_lift_io(startup_window, monkeypatch):
    robot, _, _ = startup_window
    monkeypatch.setattr(
        robot._lift_operation.transport, "read_group", lambda *a, **k: pytest.fail("legacy path")
    )
    robot._require_am1_activation_fresh("before_home")
    robot.config.robot_model = "alohamini2"
    robot._refresh_am1_startup_lift_feedback()


def test_real_host_final_handoff_acquires_only_due_on_owner_thread(startup_window, monkeypatch):
    robot, clock, first = startup_window
    operation = robot._lift_operation
    window = operation.temperature
    acquired = []
    read = operation.transport.read_group

    def recorded(*a, **k):
        if operation.bus.expected_torque == 1:
            acquired.append(clock.now)
        return read(*a, **k)

    monkeypatch.setattr(operation.transport, "read_group", recorded)
    monkeypatch.setattr(host, "AlohaMini", lambda _: robot)
    monkeypatch.setattr(host, "connect_robot", lambda *a, **k: None)

    def factory(_):
        clock.sleep(0.055)
        return SimpleNamespace(
            connection_time_s=0, max_loop_freq_hz=30, watchdog_timeout_ms=1000, disconnect=lambda: None
        )

    monkeypatch.setattr(host, "AlohaMiniHost", factory)
    args = host.make_parser().parse_args(["--no_cameras", "--no_follower"])
    host._run_host(args)
    assert len(acquired) == 1, "zero ordinary-loop iterations still need one due startup acquisition"
    assert acquired[0] >= first + 0.055
    assert operation.temperature is window


@pytest.fixture
def timed_seed_batch(operating_robot,monkeypatch,tmp_path,request):
    """Recorded actual04 write durations; temperatures are explicit simulated37C reads."""
    from lerobot.robots.alohamini import alohamini as owner
    from lerobot.robots.alohamini.config_alohamini import AlohaMiniConfig
    _,clock=operating_robot
    robot=owner.AlohaMini(AlohaMiniConfig(robot_model="alohamini1",no_follower=False,cameras={},calibration_dir=tmp_path))
    robot.connect(calibrate=False)
    operation=robot._lift_operation;window=operation.temperature
    for bus,names in ((robot.left_bus,robot.left_arm_motors),(robot.right_bus,robot.right_arm_motors)):
        for name in names:bus.registers["Torque_Enable",name]=0
    robot._am1_activation_readbacks=[]
    qualified=robot._qualify_am1_activation_ranges()
    origin=window._origin_ns/1e9
    start=origin+math.ceil((clock.now-origin)/.1)*.1
    robot.left_bus.registers["Present_Temperature","lift_axis"]=37
    hot=getattr(request.node,"callspec",SimpleNamespace(params={})).params.get("fault")=="hot"
    for index,offset in enumerate((.012309282,.134322262,.234339748,.334330066,.434331589)):
        robot.left_bus.registers["Present_Temperature","lift_axis"]=55 if hot and index>=3 else 37
        at(clock,start+offset-.002);operation.poll()
    state=SimpleNamespace(robot=robot,clock=clock,operation=operation,window=window,start=start,
        qualified=qualified,writes=[],polls=[],readbacks=[],fault=None)
    durations=(.000883405,.000993108,.001002886,.001001960,.001001608,.051788155,
               .000946664,.000992979,.000992663,.001006608,.051217027,.000992738)
    original_read=operation.transport.read_group
    def grouped(*a,**k):
        if state.fault=="transport" and len(state.writes)>=6:
            raise feedback.TransportRefusal("seed callback transport refused",{"checksum_ok":False})
        state.polls.append(clock.now)
        return original_read(*a,**k)
    monkeypatch.setattr(operation.transport,"read_group",grouped)
    for bus in (robot.left_bus,robot.right_bus):
        write=bus.write;read=bus.read
        def timed_write(register,name,value,*,source=bus,original=write,**kwargs):
            if register=="Goal_Position" and name.startswith("arm_"):
                if not state.writes:at(clock,start+.470578304)
                clock.sleep(durations[len(state.writes)])
                original(register,name,value,**kwargs);state.writes.append((source,name,value,clock.now))
                if len(state.writes)==6 and state.fault=="late":clock.sleep(.501)
                return
            return original(register,name,value,**kwargs)
        def timed_read(register,name,*,original=read,**kwargs):
            if register=="Goal_Position" and name.startswith("arm_"):
                clock.sleep(.001);state.readbacks.append(name)
            return original(register,name,**kwargs)
        monkeypatch.setattr(bus,"write",timed_write);monkeypatch.setattr(bus,"read",timed_read)
    try:yield state
    finally:
        robot.left_bus.is_connected=robot.right_bus.is_connected=False


def test_seed_batch_actual04_timing_keeps_genuine_original_slot76(timed_seed_batch):
    c=timed_seed_batch;origin=c.window._origin_ns;zero=c.robot.lift._z0_deg
    c.robot._seed_qualified_am1_activation_goals(c.qualified)
    # Simulate original post-batch/active-settings work; no generated temperatures or timestamps.
    c.clock.sleep(.005891261);c.robot._refresh_am1_startup_lift_feedback()
    c.clock.sleep(.033126132);c.robot._refresh_am1_startup_lift_feedback()
    assert any(c.start+.5<=stamp<c.start+.6 for stamp in c.polls)
    assert len(c.writes)==len(c.readbacks)==12
    assert all(bus.registers["Goal_Position",name]==value for bus,name,value,_ in c.writes)
    assert all(bus.registers["Torque_Enable",name]==0 for bus,name,_,_ in c.writes)
    assert c.operation.temperature is c.window and c.window._origin_ns==origin
    assert c.window.MAX_AGE_S==.5 and c.window.failure is None
    assert c.robot.lift._z0_deg==zero
    assert all(c.clock.now-stamp<=.5 for stamp,_ in c.window.samples)


@pytest.mark.parametrize("fault",["late","transport","hot"])
def test_seed_batch_callback_retains_existing_fault_before_later_goals(timed_seed_batch,fault):
    c=timed_seed_batch;c.fault=fault
    with pytest.raises(feedback.ComparisonRefusal) as caught:
        c.robot._seed_qualified_am1_activation_goals(c.qualified)
    assert len(c.writes)==6 and not c.readbacks
    assert c.operation.failure is caught.value
    with pytest.raises(feedback.ComparisonRefusal) as again:c.robot._refresh_am1_startup_lift_feedback()
    assert again.value is caught.value
    assert all(bus.registers["Torque_Enable",name]==0 for bus,name,_,_ in c.writes)


@pytest.mark.parametrize("boundary",["write","readback"])
def test_seed_callback_failure_keeps_original_successful_io_evidence(timed_seed_batch,monkeypatch,boundary):
    c=timed_seed_batch;original=c.robot._refresh_am1_startup_lift_feedback
    cause=RuntimeError("original seed callback failure")
    def callback():
        if (boundary=="write" and c.writes) or (boundary=="readback" and c.readbacks):raise cause
        return original()
    monkeypatch.setattr(c.robot,"_refresh_am1_startup_lift_feedback",callback)
    with pytest.raises(RuntimeError) as caught:c.robot._seed_qualified_am1_activation_goals(c.qualified)
    assert caught.value is cause
    if boundary=="write":assert len(c.writes)==1 and not c.readbacks
    else:
        assert len(c.writes)==12 and len(c.readbacks)==1
        assert c.robot._am1_activation_readbacks[-1]["phase"]=="seed_readback"
        assert "raw" in c.robot._am1_activation_readbacks[-1]
