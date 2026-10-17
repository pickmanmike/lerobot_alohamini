"""Installed role settings remain unchanged by Packet16 startup."""

from uuid import uuid4

import pytest

from tests.robots.test_am1_pre_activation import activation as activation


def configured_robot(activation):
    robot = activation.robot
    robot.config.no_follower = False
    robot.config.use_degrees = False
    robot._am1_teleop_binding = {
        "recipe": "physical-live-teleop",
        "run_id": str(uuid4()),
        "host_incarnation": str(uuid4()),
    }
    robot.lift.configure = lambda **kwargs: None
    for bus in (robot.left_bus, robot.right_bus):
        for name in bus.motors:
            for register, value in {
                "ID": bus.motors[name].id,
                "Model_Number": 777,
                "Firmware_Major_Version": 3,
                "Firmware_Minor_Version": 10,
                "Operating_Mode": 0,
                "Phase": 12,
                "P_Coefficient": 24,
                "I_Coefficient": 0,
                "D_Coefficient": 19,
                "Acceleration": 73,
                "Maximum_Acceleration": 100,
                "Protection_Current": 310,
                "Max_Temperature_Limit": 70,
                "Min_Voltage_Limit": 40,
                "Max_Voltage_Limit": 140,
                "Status": 0,
                "Present_Temperature": 28,
                "Present_Voltage": 120,
                "Present_Current": 2,
            }.items():
                bus.registers[register, name] = value

        def grouped(register, names, *, normalize=True, _bus=bus, **kwargs):
            return {name: _bus.read(register, name, normalize=normalize) for name in names}

        bus.sync_read = grouped
    robot.configure()
    return robot


def test_teleop_configure_preserves_installed_arm_settings(activation):
    robot = configured_robot(activation)
    case = activation
    forbidden = {
        "P_Coefficient",
        "I_Coefficient",
        "D_Coefficient",
        "Acceleration",
        "Maximum_Acceleration",
        "Phase",
        "Homing_Offset",
        "Min_Position_Limit",
        "Max_Position_Limit",
    }
    assert not [row for row in case.events if row[0] == "write" and row[2] in forbidden]
    assert robot._am1_teleop_settings["left"]["registers"]["P_Coefficient"]["arm_left_wrist_flex"] == 24


def test_actual_status_fault_is_retained_before_a_later_transport_failure(activation):
    from lerobot.robots.alohamini.am1_live_teleop import poll_health

    robot = configured_robot(activation)
    original = robot.left_bus.sync_read

    def fault(register, names, **kwargs):
        if register == "Status":
            return dict.fromkeys(names, 1)
        if register == "Present_Temperature":
            raise OSError("later transport loss")
        return original(register, names, **kwargs)

    robot.left_bus.sync_read = fault
    with pytest.raises(RuntimeError, match="status"):
        poll_health(robot)
    assert robot._am1_teleop_fault["register"] == "Status"


@pytest.mark.parametrize(
    ("register", "raw", "reason"),
    [("Present_Temperature", 70, "temperature"), ("Present_Voltage", 39, "voltage"), ("Status", 1, "status")],
)
def test_installed_health_limits_remain_effective(activation, register, raw, reason):
    from lerobot.robots.alohamini.am1_live_teleop import poll_health

    robot = configured_robot(activation)
    for bus in (robot.left_bus, robot.right_bus):
        for name in bus.calibration:
            bus.registers["Torque_Enable", name] = bus.registers["Lock", name] = 1
    robot.left_bus.registers[register, "arm_left_gripper"] = raw
    with pytest.raises(RuntimeError, match=reason):
        poll_health(robot)


def test_actual_hardware_current_limit_precedes_other_bus_failure(activation):
    robot = configured_robot(activation)
    robot._last_currents_log_t, robot._overcurrent_count, robot._overcurrent_trip_n = 0.0, {}, 20
    robot.left_bus.registers["Present_Current", "arm_left_gripper"] = 311
    robot.right_bus.sync_read = lambda *args, **kwargs: (_ for _ in ()).throw(OSError("later right bus loss"))
    with pytest.raises(RuntimeError, match="current"):
        robot.read_and_check_currents(limit_ma=2000, print_currents=False)
    assert robot._am1_teleop_fault["raw"]["arm_left_gripper"] == 311


def fault_note(error):
    import json

    prefix = "[AM1 TELEOP FAULT] "
    notes = [note for note in getattr(error, "__notes__", []) if note.startswith(prefix)]
    assert len(notes) == 1, "original AM1 fault snapshot must survive the existing exception path"
    assert len(notes[0].encode("utf-8")) <= 16384
    return json.loads(notes[0][len(prefix) :])


@pytest.mark.parametrize(
    ("register", "value", "reason", "configured"),
    [
        ("Present_Temperature", 70, "temperature", {"Max_Temperature_Limit": 70}),
        ("Present_Temperature", -1, "temperature", {"Max_Temperature_Limit": 70}),
        ("Present_Voltage", 39, "voltage", {"Min_Voltage_Limit": 40, "Max_Voltage_Limit": 140}),
        ("Status", 1, "status", {}),
        ("Torque_Enable", 0, "torque/lock", {}),
        ("Lock", 0, "torque/lock", {}),
        ("Operating_Mode", 1, "operating mode", {}),
    ],
)
def test_fault_note_exports_original_row_interval_binding_and_installed_comparator(
    activation, register, value, reason, configured
):
    from lerobot.robots.alohamini.am1_live_teleop import HEALTH, poll_health

    robot = configured_robot(activation)
    for bus in (robot.left_bus, robot.right_bus):
        for name in bus.calibration:
            bus.registers["Torque_Enable", name] = bus.registers["Lock", name] = 1
    motor = "arm_left_shoulder_pan"
    robot.left_bus.registers[register, motor] = value
    binding = dict(robot._am1_teleop_binding)
    activation.clock.now = 120.0
    robot.left_bus.on_read = lambda *_: setattr(activation.clock, "now", activation.clock.now + 0.001)
    robot.right_bus.sync_read = lambda *_, **__: pytest.fail("fault must precede later right-bus reads")
    boundary = len(activation.events)
    with pytest.raises(RuntimeError, match=reason) as caught:
        poll_health(robot)
    snapshot = fault_note(caught.value)
    assert snapshot == robot._am1_teleop_fault
    assert snapshot["binding"] == binding
    assert snapshot["register"] == register and snapshot["offending_motor"] == motor
    assert snapshot["offending_value"] == snapshot["raw"][motor] == value
    assert set(snapshot["raw"]) == set(robot.left_arm_motors)
    order = (*HEALTH, "Operating_Mode")
    expected = order[: order.index(register) + 1]
    reads = activation.events[boundary:]
    assert len(reads) == 6 * len(expected) and all(event[0:2] == ("read", "left") for event in reads)
    assert [event[2] for event in reads] == [key for key in expected for _ in range(6)]
    assert snapshot["read_started_at_monotonic_s"] == pytest.approx(120 + 0.006 * (len(expected) - 1))
    assert snapshot["read_completed_at_monotonic_s"] == pytest.approx(120 + 0.006 * len(expected))
    comparison = snapshot["comparison"]
    assert comparison["units"] == "raw_register"
    if register == "Present_Temperature":
        assert comparison["lower_inclusive"] == 0 and comparison["upper_exclusive"] == 70
    elif register == "Present_Voltage":
        assert comparison["lower_inclusive"] == 40 and comparison["upper_inclusive"] == 140
    else:
        assert comparison["expected"] == (1 if register in ("Torque_Enable", "Lock") else 0)
    assert {key: row["value"] for key, row in comparison.get("configured", {}).items()} == configured
    for key, row in comparison.get("configured", {}).items():
        assert {
            name: row[name] for name in robot._am1_teleop_settings["left"]["timing"][key]
        } == robot._am1_teleop_settings["left"]["timing"][key]


def test_fault_note_initial_settings_uses_original_current_capture_without_cached_baseline(activation):
    from lerobot.robots.alohamini.am1_live_teleop import prepare_settings

    robot = configured_robot(activation)
    del robot._am1_teleop_settings
    robot.left_bus.registers["Present_Temperature", "arm_left_shoulder_pan"] = 70
    activation.clock.now = 121.0
    with pytest.raises(RuntimeError, match="temperature") as caught:
        prepare_settings(robot)
    limit = fault_note(caught.value)["comparison"]["configured"]["Max_Temperature_Limit"]
    assert limit == {
        "value": 70,
        "read_started_at_monotonic_s": 121.0,
        "read_completed_at_monotonic_s": 121.0,
    }


def test_fault_note_first_snapshot_survives_mutation_and_actual_cleanup_export(activation, tmp_path):
    import copy
    import json
    import traceback
    from types import SimpleNamespace

    from lerobot.robots.alohamini.alohamini_host import write_am1_cleanup_receipt
    from lerobot.robots.alohamini.am1_live_teleop import poll_health

    robot = configured_robot(activation)
    motor = "arm_left_shoulder_pan"
    robot.left_bus.registers["Present_Temperature", motor] = 70
    with pytest.raises(RuntimeError, match="temperature") as caught:
        poll_health(robot)
    first = caught.value
    original = copy.deepcopy(fault_note(first))
    rendered = "".join(traceback.format_exception(first))
    assert str(first) in rendered and first.__notes__[0] in rendered
    robot.left_bus.registers["Status", motor] = 1
    robot._am1_teleop_binding["run_id"] = str(uuid4())
    robot._am1_teleop_settings["left"]["registers"]["Max_Temperature_Limit"][motor] = 99
    with pytest.raises(RuntimeError, match="status") as later:
        poll_health(robot)
    assert fault_note(later.value) == fault_note(first) == robot._am1_teleop_fault == original
    robot._am1_shutdown_evidence = {"verified": False, "buses_closed": False}
    path = tmp_path / "cleanup.json"
    args = SimpleNamespace(
        am1_run_id=original["binding"]["run_id"],
        am1_host_incarnation=original["binding"]["host_incarnation"],
        am1_cleanup_receipt=path,
    )
    receipt = write_am1_cleanup_receipt(args, robot, first, [])
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved == receipt
    assert receipt["first_cause"] == "RuntimeError: " + str(first)
    assert receipt["uncertain"] is True and receipt["cleanup"] == "physical_cleanup_unknown"
    assert receipt["first_error_chain"][0]["notes"] == first.__notes__
    assert json.loads(receipt["first_error_chain"][0]["notes"][0].split("] ", 1)[1]) == original


@pytest.mark.parametrize("problem", ["serialization", "oversized"])
def test_fault_note_failure_cannot_replace_original_guard(activation, monkeypatch, problem):
    from lerobot.robots.alohamini import am1_live_teleop as helper

    robot = configured_robot(activation)
    motor = "arm_left_shoulder_pan"
    robot.left_bus.registers["Present_Temperature", motor] = 70

    def encode(*args, **kwargs):
        if problem == "serialization":
            raise ValueError("diagnostic serialization failed")
        return "x" * 16385

    monkeypatch.setattr(helper.json, "dumps", encode)
    with pytest.raises(
        RuntimeError, match="AM1 teleop actual configured temperature limit: " + motor
    ) as caught:
        helper.poll_health(robot)
    assert robot._am1_teleop_fault["raw"][motor] == 70
    assert robot._am1_teleop_fault["offending_motor"] == motor
    assert not getattr(caught.value, "__notes__", [])


def test_fault_note_current_uses_actual_installed_raw_limit_without_later_io(activation):
    from lerobot.robots.alohamini.am1_live_teleop import check_sampled_current

    robot = configured_robot(activation)
    values = dict.fromkeys(robot.left_arm_motors, 2)
    values["arm_left_gripper"] = 311
    boundary = len(activation.events)
    with pytest.raises(RuntimeError, match="current") as caught:
        check_sampled_current(robot, "left", values, 122.0, 122.003)
    snapshot = fault_note(caught.value)
    assert snapshot["raw"] == values and snapshot["offending_motor"] == "arm_left_gripper"
    assert snapshot["comparison"]["absolute_upper_exclusive"] == 310
    assert snapshot["comparison"]["configured"]["Protection_Current"]["value"] == 310
    assert snapshot["comparison"]["units"] == "raw_register"
    assert snapshot["read_started_at_monotonic_s"] == 122.0
    assert snapshot["read_completed_at_monotonic_s"] == 122.003
    assert activation.events[boundary:] == []


@pytest.mark.parametrize("kind", ["temperature", "current"])
def test_fault_note_malformed_baseline_timing_cannot_replace_proven_guard(activation, kind):
    from lerobot.robots.alohamini.am1_live_teleop import check_sampled_current, poll_health

    robot = configured_robot(activation)
    motor = "arm_left_shoulder_pan"
    robot._am1_teleop_settings["left"]["timing"] = None
    robot.left_bus.registers["Present_Temperature", motor] = 70
    with pytest.raises(RuntimeError, match="actual configured " + kind) as caught:
        if kind == "temperature":
            poll_health(robot)
        else:
            values = dict.fromkeys(robot.left_arm_motors, 2)
            values[motor] = 311
            check_sampled_current(robot, "left", values, 125.0, 125.002)
    snapshot = fault_note(caught.value)
    assert snapshot["offending_motor"] == motor
    assert snapshot["offending_value"] == (70 if kind == "temperature" else 311)
    assert snapshot["comparison"]["units"] == "raw_register"


def resting_robot(activation, monkeypatch, raw=3225):
    from lerobot.motors import MotorCalibration
    from lerobot.motors.feetech import FeetechMotorsBus

    robot, name = activation.robot, "arm_left_shoulder_lift"
    robot.left_bus.calibration[name] = MotorCalibration(2, 0, 515, 848, 3223)
    robot.left_bus.registers.update(
        {
            (register, name): value
            for register, value in {
                "Min_Position_Limit": 848,
                "Max_Position_Limit": 3223,
                "Homing_Offset": 515,
                "Present_Position": raw,
                "Goal_Position": 3223,
            }.items()
        }
    )
    robot = configured_robot(activation)
    robot.config.max_relative_target = 20.0
    robot._joint_hold_goal, robot._gripper_hold_goal = {}, {}
    for bus in (robot.left_bus, robot.right_bus):
        normalizer = FeetechMotorsBus("unused-rest16", bus.motors, bus.calibration)
        bus.apply_drive_mode = normalizer.apply_drive_mode
        bus.model_resolution_table = normalizer.model_resolution_table
        bus._normalize, bus._unnormalize = normalizer._normalize, normalizer._unnormalize
        for motor in bus.motors:
            for register in ("Present_Velocity", "Moving", "CW_Dead_Zone", "CCW_Dead_Zone"):
                bus.registers[register, motor] = 0 if register in ("Present_Velocity", "Moving") else 1
    monkeypatch.setattr(
        "lerobot.robots.alohamini.alohamini.time.sleep",
        lambda seconds: setattr(activation.clock, "now", activation.clock.now + seconds),
    )
    return robot


@pytest.mark.parametrize("raw", [3224, 3225, 3228])
def test_supported_rest_qualifies_fresh_series_and_only_seeds_inward(activation, monkeypatch, raw):
    robot = resting_robot(activation, monkeypatch, raw)
    robot.activate_motors()
    name = "arm_left_shoulder_lift"
    assert robot.left_bus.registers["Present_Position", name] == raw
    assert robot.left_bus.registers["Goal_Position", name] == 3223
    assert [
        row[4] for row in activation.events if row[0] == "write" and row[2:4] == ("Goal_Position", name)
    ] == [3223]
    assert robot._normalize_arm_feedback(robot.left_bus, {name: raw})[name] == pytest.approx(
        {3224: 100.08421052631579, 3225: 100.16842105263157, 3228: 100.42105263157896}[raw]
    )
    proof = robot._am1_teleop_rest_evidence
    assert [entry["phase"] for entry in proof] == ["before_home", "after_home"]
    assert all(entry["qualified"] and len(entry["samples"]) >= 9 for entry in proof)
    assert all(entry["window_s"] >= 0.4 and entry["seed_raw"] == 3223 for entry in proof)
    assert proof[0]["samples"][-1]["completed_at"] <= proof[1]["samples"][0]["started_at"]
    # Installed P24/D19 are deliberately different from the old selected diagnostic fingerprint.
    assert robot.left_bus.registers["P_Coefficient", name] == 24
    assert robot.left_bus.registers["D_Coefficient", name] == 19


@pytest.mark.parametrize("phase", ["before_home", "after_home"])
@pytest.mark.parametrize(
    "problem",
    ["moving", "status", "current", "velocity", "temperature", "voltage", "drifting", "cap", "settings"],
)
def test_rest_fault_or_unstable_series_refuses_without_arm_goals(activation, monkeypatch, phase, problem):
    robot = resting_robot(activation, monkeypatch)
    name, reads = "arm_left_shoulder_lift", [0]

    def mutate(register, motor):
        if motor != name or (phase == "after_home") != bool(activation.home_calls):
            return
        values = {
            "moving": ("Moving", 1),
            "status": ("Status", 1),
            "current": ("Present_Current", 24),
            "velocity": ("Present_Velocity", 1),
            "temperature": ("Present_Temperature", 60),
            "voltage": ("Present_Voltage", 39),
            "settings": ("P_Coefficient", 25),
        }
        if problem in values:
            reg, value = values[problem]
            robot.left_bus.registers[reg, name] = value
        elif problem == "drifting" and register == "Present_Position":
            reads[0] += 1
            robot.left_bus.registers[register, name] = 3224 + min(3, reads[0])
        elif problem == "cap":
            robot.config.max_relative_target = 0.01

    robot.left_bus.on_read = mutate
    with pytest.raises(RuntimeError, match="activation failed"):
        robot.activate_motors()
    assert not [row for row in activation.events if row[0] == "write" and row[2] == "Goal_Position"]
    assert not [
        row
        for row in activation.events
        if row[0] == "write" and row[2] == "Torque_Enable" and row[3].startswith("arm_") and row[4] == 1
    ]
    assert activation.home_calls == (phase == "after_home")


@pytest.mark.parametrize(
    "problem", ["over_half_degree", "lower_endpoint", "other_joint", "foreign_calibration", "foreign_binding"]
)
def test_rest_operating_bound_does_not_expand_calibration_or_other_roles(activation, monkeypatch, problem):
    robot = resting_robot(activation, monkeypatch)
    if problem == "over_half_degree":
        robot.left_bus.registers["Present_Position", "arm_left_shoulder_lift"] = 3229
    elif problem == "lower_endpoint":
        robot.left_bus.registers["Present_Position", "arm_left_shoulder_lift"] = 847
    elif problem == "other_joint":
        robot.left_bus.registers["Present_Position", "arm_left_wrist_flex"] = 2201
    elif problem == "foreign_calibration":
        robot.left_bus.calibration["arm_left_shoulder_lift"].drive_mode = 1
    else:
        robot._am1_teleop_binding["run_id"] = str(uuid4())
        robot._am1_teleop_binding["recipe"] = "physical-live-right-elbow"
    with pytest.raises(RuntimeError, match="activation failed"):
        robot.activate_motors()
    assert activation.home_calls == 0
    assert not [row for row in activation.events if row[0] == "write" and row[2] == "Goal_Position"]


def test_acquired_rest_native_workspace_and_hold_preserve_raw_reference_and_legal_goal(
    activation, monkeypatch
):
    from examples.alohamini.am1_live_teleop import validate_target, validate_workspace
    from lerobot.motors import MotorNormMode
    from lerobot.robots.alohamini.alohamini_host import AM1LocalControl
    from tests.robots.test_am1_live_teleop_native import frame

    robot = resting_robot(activation, monkeypatch)
    for bus, side, elbow in ((robot.left_bus, "left", 2124), (robot.right_bus, "right", 1847)):
        bus.registers["Present_Position", f"arm_{side}_elbow_flex"] = elbow
        bus.motors[f"arm_{side}_gripper"].norm_mode = MotorNormMode.RANGE_0_100
    robot.activate_motors()
    identity = {key: robot._am1_teleop_binding[key] for key in ("run_id", "host_incarnation")}
    owner = {
        key: str(uuid4()) for key in ("service_incarnation", "controller_generation", "connection_generation")
    }
    control = AM1LocalControl(
        **identity, live=True, live_recipe="physical-live-teleop", clock=lambda: activation.clock.now
    )
    robot.stop_motion = lambda: None
    robot.get_observation = lambda: {
        name + ".pos": value
        for bus, names in ((robot.left_bus, robot.left_arm_motors), (robot.right_bus, robot.right_arm_motors))
        for name, value in robot._normalize_arm_feedback(
            bus, {name: bus.registers["Present_Position", name] for name in names}
        ).items()
    }
    boundary = len(activation.events)
    assert control.apply(robot, frame(identity, owner, 1))
    snapshot = control.live_snapshot()
    assert validate_workspace(snapshot["workspace"]) == snapshot["workspace"]
    shoulder = "arm_left_shoulder_lift.pos"
    assert snapshot["reference"][shoulder] == pytest.approx(100.16842105263157)
    assert snapshot["workspace"]["reference"][shoulder] == snapshot["reference"][shoulder]
    assert snapshot["cursor"][shoulder] == snapshot["accepted_target"][shoulder] == 100
    assert validate_target(snapshot["cursor"], workspace=snapshot["workspace"]) == snapshot["cursor"]
    assert snapshot["held_goals_raw"]["left"]["arm_left_shoulder_lift"] == 3223
    assert not [
        event for event in activation.events[boundary:] if event[0] == "write" and event[2] == "Goal_Position"
    ]
    assert control.apply(robot, frame(identity, owner, 2, revision=1))
    assert robot.left_bus.registers["Goal_Position", "arm_left_shoulder_lift"] == 3223


def test_rest_last_settings_read_cannot_mutate_binding_before_goal_seed(activation, monkeypatch):
    robot = resting_robot(activation, monkeypatch)
    reads = [0]

    def change(register, name):
        if activation.home_calls and name == "arm_left_shoulder_lift" and register == "CCW_Dead_Zone":
            reads[0] += 1
            if reads[0] == 2:
                robot._am1_teleop_binding["run_id"] = str(uuid4())

    robot.left_bus.on_read = change
    with pytest.raises(RuntimeError, match="activation failed"):
        robot.activate_motors()
    assert not [row for row in activation.events if row[0] == "write" and row[2] == "Goal_Position"]


def test_rest_at_legal_endpoint_qualifies_before_home_then_keeps_fresh_actual_after_home(
    activation, monkeypatch
):
    robot = resting_robot(activation, monkeypatch, raw=3223)
    activation.home_change = lambda source: source.left_bus.registers.update(
        {("Present_Position", "arm_left_shoulder_lift"): 3225}
    )
    robot.activate_motors()
    assert [row["phase"] for row in robot._am1_teleop_rest_evidence] == ["before_home", "after_home"]
    assert [row["measured_raw"] for row in robot._am1_teleop_rest_evidence] == [3223, 3225]
    assert robot.left_bus.registers["Goal_Position", "arm_left_shoulder_lift"] == 3223


def test_rest_home_boundary_cannot_replace_original_protected_run(activation, monkeypatch):
    robot = resting_robot(activation, monkeypatch)
    original = dict(robot._am1_teleop_binding)
    activation.home_change = lambda source: source._am1_teleop_binding.update(run_id=str(uuid4()))
    with pytest.raises(RuntimeError, match="activation failed"):
        robot.activate_motors()
    assert robot._am1_teleop_rest_evidence[0]["binding"] == original
    assert not [row for row in activation.events if row[0] == "write" and row[2] == "Goal_Position"]
    assert not [
        row
        for row in activation.events
        if row[0] == "write" and row[2] == "Torque_Enable" and row[3].startswith("arm_") and row[4] == 1
    ]


@pytest.mark.parametrize("problem", ["slow_read", "first_status_then_transport"])
def test_rest_original_read_budget_and_first_cause_survive_partial_acquisition(
    activation, monkeypatch, problem
):
    robot = resting_robot(activation, monkeypatch)

    def disturb(register, name):
        if name != "arm_left_shoulder_lift":
            return
        if register == "Status":
            if problem == "slow_read":
                activation.clock.now += 0.041
            else:
                robot.left_bus.registers[register, name] = 1
                robot.left_bus.read_faults["Present_Temperature", name] = OSError("later transport fault")

    robot.left_bus.on_read = disturb
    with pytest.raises(RuntimeError) as caught:
        robot.activate_motors()
    cause = caught.value.__cause__
    assert ("40ms" if problem == "slow_read" else "Status") in str(cause)
    proof = robot._am1_teleop_rest_evidence[0]
    assert proof["qualified"] is False
    if problem == "slow_read":
        original = next(
            row for row in reversed(robot._am1_activation_readbacks) if row["register"] == "Status"
        )
        assert original["raw"] == 0
        assert original["read_completed_at"] - original["read_started_at"] == pytest.approx(0.041)
    else:
        assert proof["samples"][0]["Status"] == 1
    assert proof["first_cause"] in cause.__notes__[0]
    assert activation.home_calls == 0


@pytest.mark.parametrize("problem", ["expired", "illegal_goal", "changed_calibration"])
def test_teleop_actual_hold_refuses_bad_or_expired_goal_acquisition_without_reseeding(
    activation, monkeypatch, problem
):
    robot = resting_robot(activation, monkeypatch)
    robot.activate_motors()
    boundary = len(activation.events)
    if problem == "illegal_goal":
        robot.right_bus.registers["Goal_Position", "arm_right_wrist_roll"] = 2201
    else:

        def disturb(register, name):
            if register == "Goal_Position" and name == "arm_right_gripper":
                if problem == "expired":
                    activation.clock.now += 1.001
                else:
                    robot.right_bus.calibration[name].drive_mode = 1

        robot.right_bus.on_read = disturb
    with pytest.raises(RuntimeError, match="teleop"):
        robot.hold_follower_arms()
    assert not [row for row in activation.events[boundary:] if row[0] == "write"]
