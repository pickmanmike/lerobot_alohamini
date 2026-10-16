"""Actual protected IPC and client decoding with injected mock motor hardware only."""

from __future__ import annotations

import os
import queue
import stat
import tempfile
import threading
import time
import traceback
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
import zmq

from lerobot.robots.alohamini import alohamini_host as host
from lerobot.robots.alohamini.alohamini_client import AlohaMiniClient
from lerobot.robots.alohamini.config_alohamini import AlohaMiniClientConfig
from lerobot.robots.alohamini.model_specs import arm_state_keys_for_robot_model

SHOULDER = "arm_left_shoulder_lift.pos"
BODY = {"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0, "lift_axis.vel": 0.0}


@pytest.mark.parametrize("recipe,side,origin_raw,offset,range_min,range_max,phase", [
    ("physical-live-left-elbow", "left", 2000, 1845, 1000, 3000, 17),
    ("physical-live-right-elbow", "right", 1845, -658, 827, 3039, 12),
])
def test_live_fixture_admission_uses_actual_settings_guard(
    recipe, side, origin_raw, offset, range_min, range_max, phase
):
    """Both selected fixtures use production owned settings and native admission."""
    from tests.robots.test_am1_live_native import frame

    binding = identity()
    robot = InjectedLiveMotorHardware(SimpleNamespace(robot_model="alohamini1"), live_recipe=recipe)
    evidence = host.AM1SelectedJointEvidence(enabled=True, emit=lambda _: None)
    robot._am1_selected_joint_evidence_enabled = True
    robot._am1_visible_control_binding = {**binding, "recipe": recipe}
    robot._am1_visible_control_evidence = evidence
    control = host.AM1LocalControl(**binding, selected_evidence=evidence, live=True, live_recipe=recipe)
    owner = {k: str(uuid4()) for k in ("service_incarnation", "controller_generation", "connection_generation")}
    robot.connect(home_lift=True)
    assert control.apply(robot, frame(binding, owner, sequence=1))
    assert control.live_snapshot()["measured_origin_raw"] == origin_raw
    assert control.live_snapshot()["joint"] == f"arm_{side}_elbow_flex.pos"
    assert evidence.visible_settings["unchanged"] is True
    settings = evidence.visible_settings["before"]["registers"]
    assert settings["Homing_Offset"] == offset
    assert (settings["Min_Position_Limit"], settings["Max_Position_Limit"]) == (range_min, range_max)
    assert settings["Phase"] == phase
    bus = getattr(robot, side + "_bus")
    assert len(bus.port_handler.writes) == 4
    assert control.live_snapshot()["reference_norm"] == (0.0 if side=="left" else (1845-827)/2212*200-100)
    assert (control.live_snapshot()["corridor_min_norm"], control.live_snapshot()["corridor_max_norm"]) == (
        (-10.0, 0.0) if side == "left" else ((1834-827)/2212*200-100, (1867-827)/2212*200-100)
    )
    assert bus._unnormalize({3: 1.0})[3] == (2010 if side == "left" else 1944)
    assert bus._unnormalize({3: -1.0})[3] == (1990 if side == "left" else 1921)
    assert evidence._base(robot, control, {})["motor"]["bus"] == side


def test_live_fixture_invalid_registers_are_rejected_by_production_guard():
    binding = identity()
    robot = InjectedLiveMotorHardware(SimpleNamespace(robot_model="alohamini1"))
    evidence = host.AM1SelectedJointEvidence(enabled=True, emit=lambda _: None)
    robot._am1_selected_joint_evidence_enabled = True
    robot._am1_visible_control_binding = {**binding, "recipe": host.AM1_LIVE_RECIPE}
    robot._am1_visible_control_evidence = evidence
    robot.register_bytes[21] = 15  # Actual simulated P byte differs from required16.
    with pytest.raises(RuntimeError, match="identity/settings"):
        robot.connect(home_lift=True)


def test_live_admission_uses_native_keys_without_runtime_provider_imports(monkeypatch):
    """Match the motor projection: shared contract exists, runtime providers do not."""
    import builtins

    from tests.robots.test_am1_live_native import frame, sample

    binding = identity()
    robot = InjectedLiveMotorHardware(SimpleNamespace(robot_model="alohamini1"))
    now = [10.0]
    evidence = host.AM1SelectedJointEvidence(enabled=True, emit=lambda _: None)
    robot._am1_selected_joint_evidence_enabled = True
    robot._am1_visible_control_binding = {**binding, "recipe": host.AM1_LIVE_RECIPE}
    robot._am1_visible_control_evidence = evidence
    control = host.AM1LocalControl(**binding, selected_evidence=evidence, live=True, clock=lambda: now[0])
    owner = {k: str(uuid4()) for k in ("service_incarnation", "controller_generation", "connection_generation")}
    native_keys = tuple(k for side in arm_state_keys_for_robot_model("alohamini1") for k in side)
    original_import = builtins.__import__

    def motor_projection_import(name, globals=None, locals=None, fromlist=(), level=0):
        if ((name.startswith("examples.alohamini.") and name != "examples.alohamini.am1_session_contract")
                or name.startswith("tools.")):
            raise ModuleNotFoundError(f"No module named '{name}'", name=name)
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", motor_projection_import)
    robot.connect(home_lift=True)
    assert control.apply(robot, frame(binding, owner, sequence=1))
    assert set(control.live_held) == set(native_keys) and len(control.live_held) == 12
    admission = control.live_snapshot()
    now[0] = 10.1
    assert control.apply(robot, frame(binding, owner, sequence=2, mode="active", epoch=2,
                                      sample=sample(expiry=10.25, target=-.5)))
    now[0] = 10.26
    assert control.check_live(robot)
    assert control.state == "paused" and control.live_snapshot()["state"] == "expired"
    assert control.apply(robot, frame(binding, owner, sequence=3, epoch=3, revision=1))
    assert control.live_snapshot()["reference_norm"] == admission["reference_norm"]
    assert control.live_snapshot()["live_deadline_monotonic_s"] == admission["live_deadline_monotonic_s"]


def identity():
    return {"run_id": str(uuid4()), "host_incarnation": str(uuid4())}


def command(binding, *, mode="active", epoch=0, revision=1, sequence=1, shoulder=8.0, body=None):
    return {
        **BODY,
        **(body or {}),
        SHOULDER: shoulder,
        host.AM1_LOCAL_CONTROL_KEY: {
            "version": 1,
            "mode": mode,
            "epoch": epoch,
            **binding,
            "intent_revision": revision,
            "action_sequence": sequence,
        },
    }


class InjectedMotorHardware:
    """Requested/accepted targets deliberately never overwrite measured feedback."""

    def __init__(self, config=None):
        self.config = config
        self.cameras = {}
        self.logs = {}
        self.is_connected = False
        left, right = arm_state_keys_for_robot_model("alohamini1")
        self.measured = dict.fromkeys((*left, *right), 0.0)
        self.measured[SHOULDER] = 2.0
        self.events = []
        self.read_count = 0

    def connect(self, *, home_lift):
        assert home_lift is True
        self.events.append(("mock-connect",))
        self.is_connected = True

    def send_action(self, action):
        accepted = dict(action)
        if SHOULDER in accepted:
            accepted[SHOULDER] = min(6.0, float(accepted[SHOULDER]))
        self.events.append(("mock-action", dict(action), accepted))
        return accepted

    def stop_motion(self):
        self.events.append(("mock-body-zero",))

    def hold_follower_arms(self):
        self.events.append(("mock-measured-hold", dict(self.measured)))

    def get_observation(self):
        self.read_count += 1
        return {**self.measured, "x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0, "lift_axis.height_mm": 12.0}

    def disconnect(self, *, recover_interrupted_bus_io):
        self.events.append(("mock-disconnect", recover_interrupted_bus_io))
        self.is_connected = False


class InjectedLiveMotorHardware(InjectedMotorHardware):
    """Simulated device/settings IO with the production selected guard enabled."""

    def __init__(self, config=None, *, live_recipe=host.AM1_LIVE_RECIPE):
        super().__init__(config)
        from lerobot.motors import Motor, MotorCalibration, MotorNormMode
        from lerobot.motors.feetech import FeetechMotorsBus
        from tests.robots.test_alohamini_lift_motor_feedback import ByteStreamPort, status
        from tests.robots.test_am1_selected_joint_evidence import visible_register_packet

        left, right = arm_state_keys_for_robot_model("alohamini1")
        self.fixture_acknowledged_arm_commands = {**dict.fromkeys((*left, *right), 0.0), SHOULDER: 2.0}
        self.live_spec = host.live_elbow_spec(live_recipe)
        if self.live_spec is None:
            raise ValueError("Injected live fixture requires an allowlisted elbow recipe")
        selected = self.live_spec.joint.removesuffix(".pos")
        homing, range_min, range_max = ((1845, 1000, 3000) if self.live_spec.side == "left"
                                      else (-658, 827, 3039))
        self.torque_enabled = 0
        self.goal_raw = self.present_raw = (range_min + range_max) // 2
        if self.live_spec.side == "right":
            from tests.robots.test_am1_live_native import WORKSPACE_CALIBRATION, WORKSPACE_GOALS
            self.fixture_workspace_calibrations = dict(WORKSPACE_CALIBRATION)
            self.fixture_workspace_goals = dict(WORKSPACE_GOALS)
            self.fixture_frozen_workspace_goals = dict(WORKSPACE_GOALS)
            self.goal_raw = self.present_raw = 1845
            for name, raw in WORKSPACE_GOALS.items():
                calibration = WORKSPACE_CALIBRATION[name]
                fraction = (raw-calibration[3])/(calibration[4]-calibration[3])
                norm = fraction*100 if name.endswith("_gripper") else fraction*200-100
                self.fixture_acknowledged_arm_commands[name+".pos"] = norm
                self.measured[name+".pos"] = norm
        self.current_raw = 0
        self.register_bytes = {}
        self._joint_current_limit_ma = 1800.0
        self.selected_bus = FeetechMotorsBus(
            "unused-live-ipc-settings", {selected: Motor(3, "sts3215", MotorNormMode.RANGE_M100_100)},
            {selected: MotorCalibration(3, 0, homing, range_min, range_max)},
        )
        setattr(self, self.live_spec.side + "_bus", self.selected_bus)
        device = self

        class SimulatedSettingsPort(ByteStreamPort):
            def writePort(self, packet):  # noqa: N802 - actual vendor SDK interface
                start, length = packet[5], packet[6]
                registers = bytearray(visible_register_packet(
                    torque=device.torque_enabled, goal=device.goal_raw, present=device.present_raw,
                    current=device.current_raw, range_min=range_min, range_max=range_max,
                ))
                if device.live_spec.side == "right":
                    registers[18] = 12
                    registers[31:33] = (2048 + 658).to_bytes(2, "little")
                for address, value in device.register_bytes.items():
                    registers[address] = value
                payload = bytes([91]) if start == 85 else bytes(registers[start:start + length])
                self.responses.append(status(payload, motor_id=3))
                return super().writePort(packet)

        self.selected_bus.port_handler = SimulatedSettingsPort()

    def am1_live_arm_commands(self, identity):
        if identity != self._am1_visible_control_binding:
            raise RuntimeError("simulated accepted goal binding differs")
        return dict(self.fixture_acknowledged_arm_commands)

    def am1_live_workspace_context(self, identity, measured):
        if identity != self._am1_visible_control_binding or self.fixture_workspace_goals != self.fixture_frozen_workspace_goals:
            raise RuntimeError("Live workspace simulated current raw hold binding differs")
        return dict(robot_model=self.config.robot_model, selected_model="sts3215",
                    calibrations={k:list(v) for k,v in self.fixture_workspace_calibrations.items()},
                    nonselected_goals={k:v for k,v in self.fixture_workspace_goals.items() if k!="arm_right_elbow_flex"},
                    right_pose={k:measured[k] for k in measured if k.startswith("arm_right_") and k!=self.live_spec.joint}), self.present_raw

    def connect(self, *, home_lift):
        super().connect(home_lift=home_lift)
        evidence = self._am1_visible_control_evidence
        evidence.prepare_visible(self, self._am1_visible_control_binding)
        self.torque_enabled = 1
        evidence.verify_visible_active(self)

    def send_action(self, action):
        before_write = getattr(self, "_am1_live_before_write", lambda: None)
        before_write()
        key = self.live_spec.joint
        if key in action:
            started = time.monotonic()
            self.goal_raw = self.selected_bus._unnormalize({3: float(action[key])})[3]
            self._am1_selected_joint_action = {
                **(getattr(self, "_am1_live_action_binding", None) or {}),
                "joint": key, "motor": key.removesuffix(".pos"), "side": self.live_spec.side,
                "requested": float(action[key]), "expected_goal_raw": self.goal_raw,
                "write_started_at_monotonic_s": started, "write_completed_at_monotonic_s": time.monotonic(),
                "sync_write_returned": True, "write_acknowledged": False,
            }
        return super().send_action(action)

    def hold_follower_arms(self):
        self.goal_raw = self.present_raw
        super().hold_follower_arms()

    def get_observation(self):
        started = time.monotonic()
        result = super().get_observation()
        completed = time.monotonic()
        self._am1_selected_joint_position = {
            "raw": self.present_raw, "normalized": result[self.live_spec.joint],
            "read_started_at_monotonic_s": started, "read_completed_at_monotonic_s": completed,
        }
        self._am1_selected_joint_current = {
            "raw": self.current_raw, "ma": self.current_raw * 6.5,
            "read_started_at_monotonic_s": started, "read_completed_at_monotonic_s": completed,
        }
        return result


def make_client(directory, calibration_dir):
    return AlohaMiniClient(
        AlohaMiniClientConfig(
            remote_ip="127.0.0.1",
            robot_model="alohamini1",
            id="mock-physical-ipc",
            calibration_dir=calibration_dir,
            cameras={},
            connect_timeout_s=1.0,
            command_send_timeout_ms=100,
            polling_timeout_ms=200,
            observation_request_window=3,
            command_endpoint=f"ipc://{directory}/command.sock",
            observation_endpoint=f"ipc://{directory}/observation.sock",
        )
    )


def consume_parts(client, observation):
    parts = host.build_observation_multipart(observation, ())
    with patch.object(client, "_poll_and_get_latest_message", return_value=parts):
        return client._get_data()[1]


def test_host_annotation_and_real_client_codec_keep_measurement_separate_from_action(tmp_path):
    binding = identity()
    control = host.AM1LocalControl(**binding)
    hardware = InjectedMotorHardware()
    client = make_client(tmp_path, tmp_path / "calibration")
    requested = command(binding)
    assert control.apply(hardware, requested)
    observation = hardware.get_observation()
    control.annotate(observation, acquired_at=123.0)
    decoded = consume_parts(client, observation)
    acknowledgment = client.latest_am1_local_feedback
    assert decoded[SHOULDER] == 2.0
    assert requested[SHOULDER] == 8.0
    assert acknowledgment["accepted_action"][SHOULDER] == 6.0
    assert acknowledgment["acquired_at_monotonic_s"] == 123.0
    assert acknowledgment["write_acknowledged"] is False
    assert {key: acknowledgment[key] for key in binding} == binding
    assert acknowledgment["intent_revision"] == 1
    assert acknowledgment["accepted_action_sequence"] == 1
    assert client.observation_sequence == 1
    assert host.AM1_LOCAL_FEEDBACK_KEY in client.latest_raw_observation_keys


def test_cached_client_observation_never_advances_host_ack_or_sample_sequence(tmp_path):
    binding = identity()
    control = host.AM1LocalControl(**binding)
    hardware = InjectedMotorHardware()
    client = make_client(tmp_path, tmp_path / "calibration")
    control.apply(hardware, command(binding))
    observation = hardware.get_observation()
    control.annotate(observation, acquired_at=123.0)
    first = consume_parts(client, observation)
    acknowledgment = client.latest_am1_local_feedback
    received = client.latest_observation_received_at
    for _ in range(3):
        with patch.object(client, "_poll_and_get_latest_message", return_value=None):
            cached = client._get_data()[1]
        assert cached is first
        assert client.observation_sequence == 1
        assert client.latest_observation_received_at == received
        assert client.latest_am1_local_feedback is acknowledgment
        assert acknowledgment["acquired_at_monotonic_s"] == 123.0
        assert acknowledgment["observation_id"] == 1


@contextmanager
def actual_mock_host(*, expected_error=None, live=False, live_recipe=host.AM1_LIVE_RECIPE):
    """Run the production native loop; replace only its physical robot object."""
    ready = threading.Event()
    receipts = queue.Queue()
    errors = []
    instances = []
    endpoints = {}
    hardware = []
    real_host = host.AlohaMiniHost
    real_control = host.AM1LocalControl
    binding = identity()
    with tempfile.TemporaryDirectory(prefix="am1-ipc-") as temporary:
        directory = Path(temporary)
        directory.chmod(0o700)
        args = host.make_parser().parse_args(
            [
                "--robot_model",
                "alohamini1",
                "--no_cameras",
                "--max_relative_target",
                "20",
                "--max_loop_freq_hz",
                "30",
                "--am1-protected-run-directory",
                str(directory),
                "--am1-run-id",
                binding["run_id"],
                "--am1-host-incarnation",
                binding["host_incarnation"],
                "--am1-cleanup-receipt",
                str(directory / "unused-mock-receipt.json"),
                "--am1-host-runtime-s",
                "5",
            ]
        )

        def robot_factory(config):
            robot = InjectedLiveMotorHardware(config, live_recipe=live_recipe) if live else InjectedMotorHardware(config)
            hardware.append(robot)
            return robot

        def host_factory(config):
            motor_host = real_host(config)
            instances.append(motor_host)
            endpoints.update(
                command=motor_host.zmq_cmd_socket.getsockopt_string(zmq.LAST_ENDPOINT),
                observation=motor_host.zmq_observation_socket.getsockopt_string(zmq.LAST_ENDPOINT),
            )
            ready.set()
            return motor_host

        def control_factory(**keywords):
            control = real_control(**keywords)
            apply = control.apply

            def record_apply(robot, sent):
                try:
                    accepted = apply(robot, sent)
                except BaseException as error:
                    receipts.put((sent, error))
                    raise
                receipts.put((sent, accepted))
                return accepted

            control.apply = record_apply
            return control

        def run():
            try:
                host._run_host(args)
            except BaseException as error:
                errors.append(error)
                ready.set()

        environment = {"AM1_LEFT_SHOULDER_EVIDENCE": "0", "AM1_SYNC_SHOULDER_READBACK": "0"}
        if live:
            environment.update(AM1_VISIBLE_CONTROL_RECIPE=live_recipe,
                               AM1_SELECTED_JOINT_EVIDENCE="1", AM1_SCRIPTED_PREPARE="1",
                               AM1_SHOULDER_INTEGRAL_TEST="0")
        with (
            patch.object(host, "AlohaMini", side_effect=robot_factory),
            patch.object(host, "AlohaMiniHost", side_effect=host_factory),
            patch.object(host, "AM1LocalControl", side_effect=control_factory),
            patch.dict(os.environ, environment),
        ):
            if live:
                for name in ("AM1_ELBOW_RECOVERY", "AM1_ELBOW_RECOVERY_RECIPE"):
                    os.environ.pop(name, None)
            thread = threading.Thread(target=run, name="mock-physical-host", daemon=True)
            thread.start()
            client = make_client(directory, directory / "calibration")
            try:
                assert ready.wait(2), "mock host did not reach transport construction"
                assert not errors, "".join("".join(traceback.format_exception(error)) for error in errors)
                client.connect()
                yield SimpleNamespace(
                    client=client,
                    directory=directory,
                    identity=binding,
                    hardware=hardware[0],
                    host=instances[0],
                    receipts=receipts,
                    thread=thread,
                    errors=errors,
                    endpoints=endpoints,
                )
            finally:
                if client.is_connected:
                    client.disconnect()
                if instances:
                    # End the test's bounded mock loop ordinarily at the next loop condition.
                    instances[0].connection_time_s = 0
                thread.join(timeout=2)
                assert not thread.is_alive(), "mock host did not finish its owned cleanup"
                assert not hardware or not hardware[0].is_connected
                if instances:
                    assert instances[0].zmq_context.closed
                    assert instances[0].zmq_cmd_socket.closed
                    assert instances[0].zmq_observation_socket.closed
                assert not (directory / "command.sock").exists()
                assert not (directory / "observation.sock").exists()
                if expected_error is None:
                    assert not errors, "".join("".join(traceback.format_exception(error)) for error in errors)
                else:
                    assert len(errors) == 1 and expected_error in str(errors[0])


def receive_feedback(peer, predicate):
    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline:
        observation = peer.client.get_observation()
        feedback = peer.client.latest_am1_local_feedback
        if feedback is not None and predicate(feedback):
            return observation, feedback
    pytest.fail("actual mock IPC host did not provide the expected acknowledgment")


def send_and_confirm_receipt(peer, sent, *, accepted=True):
    peer.client.send_action(sent)
    received, result = peer.receipts.get(timeout=1.5)
    assert received == sent
    if isinstance(accepted, str):
        assert isinstance(result, RuntimeError) and accepted in str(result)
    else:
        assert result is accepted


@pytest.mark.skipif(os.name == "nt", reason="Actual protected filesystem IPC requires native Pi/Linux")
def test_actual_ipc_host_and_client_ack_pause_resume_and_measured_feedback():
    with actual_mock_host() as peer:
        for name in ("command.sock", "observation.sock"):
            info = (peer.directory / name).stat()
            assert stat.S_ISSOCK(info.st_mode)
            assert stat.S_IMODE(info.st_mode) == 0o600
        assert stat.S_IMODE(peer.directory.stat().st_mode) == 0o700
        assert peer.endpoints["command"] == f"ipc://{peer.directory}/command.sock"
        assert peer.endpoints["observation"] == f"ipc://{peer.directory}/observation.sock"

        active = command(peer.identity)
        send_and_confirm_receipt(peer, active)
        observation, active_ack = receive_feedback(peer, lambda ack: ack["accepted_action_sequence"] == 1)
        assert observation[SHOULDER] == 2.0
        assert active[SHOULDER] == 8.0
        assert active_ack["accepted_action"][SHOULDER] == 6.0
        assert active_ack["state"] == "active" and active_ack["epoch"] == 0
        assert active_ack["intent_revision"] == 1
        assert active_ack["write_acknowledged"] is False
        assert {key: active_ack[key] for key in peer.identity} == peer.identity
        assert active_ack["read_started_at_monotonic_s"] <= active_ack["acquired_at_monotonic_s"]
        assert active_ack["acquired_at_monotonic_s"] <= peer.client.latest_observation_received_at

        pause = command(peer.identity, mode="pause", epoch=1, revision=2, sequence=2, shoulder=99.0)
        send_and_confirm_receipt(peer, pause)
        _, pause_ack = receive_feedback(peer, lambda ack: ack["accepted_action_sequence"] == 2)
        assert pause_ack["state"] == "paused" and pause_ack["epoch"] == 1
        assert pause_ack["intent_revision"] == 2
        assert pause_ack["accepted_action"] == {"mode": "measured_arm_hold", "body_velocity": 0.0}
        assert peer.hardware.events[-2:] == [
            ("mock-body-zero",),
            ("mock-measured-hold", dict(peer.hardware.measured)),
        ]

        effects = list(peer.hardware.events)
        stale = command(peer.identity, epoch=0, revision=2, sequence=3, shoulder=99.0)
        send_and_confirm_receipt(peer, stale, accepted=False)
        send_and_confirm_receipt(peer, pause, accepted=False)
        _, unchanged = receive_feedback(peer, lambda ack: ack["observation_id"] > pause_ack["observation_id"])
        assert peer.hardware.events == effects
        assert unchanged["accepted_action_sequence"] == 2
        assert unchanged["state"] == "paused"

        resume = command(peer.identity, epoch=2, revision=3, sequence=4, shoulder=5.0)
        send_and_confirm_receipt(peer, resume)
        observation, resume_ack = receive_feedback(peer, lambda ack: ack["accepted_action_sequence"] == 4)
        assert resume_ack["state"] == "active" and resume_ack["epoch"] == 2
        assert resume_ack["intent_revision"] == 3
        assert resume_ack["observation_id"] > pause_ack["observation_id"] > active_ack["observation_id"]
        assert resume_ack["acquired_at_monotonic_s"] > active_ack["acquired_at_monotonic_s"]
        assert observation[SHOULDER] == 2.0
        assert resume_ack["accepted_action"][SHOULDER] == 5.0
        assert all(
            event[2][key] == 0.0
            for event in peer.hardware.events
            if event[0] == "mock-action"
            for key in BODY
        )


@pytest.mark.skipif(os.name == "nt", reason="Actual protected filesystem IPC requires native Pi/Linux")
@pytest.mark.parametrize("foreign", ["run_id", "host_incarnation", "intent_revision"])
def test_actual_ipc_rejects_foreign_intent_before_actions_then_cleans_affected_host(foreign):
    expected = "revision" if foreign == "intent_revision" else "binding"
    with actual_mock_host(expected_error=expected) as peer:
        send_and_confirm_receipt(peer, command(peer.identity))
        receive_feedback(peer, lambda ack: ack["accepted_action_sequence"] == 1)
        original_actions = [event for event in peer.hardware.events if event[0] == "mock-action"]
        invalid = command(peer.identity, revision=0 if foreign == "intent_revision" else 2, sequence=2)
        if foreign != "intent_revision":
            invalid[host.AM1_LOCAL_CONTROL_KEY][foreign] = str(uuid4())
        send_and_confirm_receipt(peer, invalid, accepted=expected)
        peer.thread.join(timeout=1.5)
        assert not peer.thread.is_alive()
        assert [event for event in peer.hardware.events if event[0] == "mock-action"] == original_actions
        assert peer.hardware.events[-1] == ("mock-disconnect", True)
        assert not peer.hardware.is_connected
