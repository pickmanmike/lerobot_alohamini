"""Bounded shoulder experiment opt-in reaches only its existing host owner."""
from types import SimpleNamespace

import pytest

from tools import am1_session as session

FLAGS = ("AM1_SCRIPTED_PREPARE", "AM1_SHOULDER_INTEGRAL_TEST", "AM1_LEFT_SHOULDER_EVIDENCE")

@pytest.fixture(autouse=True)
def cleared_flags(monkeypatch):
    for name in FLAGS:
        monkeypatch.delenv(name, raising=False)

@pytest.mark.parametrize(("source", "profile"), [
    ("physical", None), ("scripted", "ArmHoldBody"),
])
def test_preparation_refuses_other_input_profiles_before_dispatch(monkeypatch, source, profile):
    monkeypatch.setenv("AM1_SCRIPTED_PREPARE", "1")
    with pytest.raises(ValueError, match="preparation"):
        session.validate_leader_selection(source, profile)

@pytest.mark.parametrize(("prepare", "evidence"), [("0", "1"), ("1", "0"), ("true", "1")])
def test_integral_test_requires_exact_preparation_and_evidence(monkeypatch, prepare, evidence):
    monkeypatch.setenv("AM1_SHOULDER_INTEGRAL_TEST", "1")
    monkeypatch.setenv("AM1_SCRIPTED_PREPARE", prepare)
    monkeypatch.setenv("AM1_LEFT_SHOULDER_EVIDENCE", evidence)
    with pytest.raises(ValueError, match="integral"):
        session.validate_leader_selection("scripted", "ArmSmoke")

@pytest.mark.parametrize("profile", ["ArmSmoke", "ArmSmokeRepeat"])
def test_ssh_command_sends_only_explicit_bounded_gain_opt_in(monkeypatch, tmp_path, profile):
    for name in FLAGS:
        monkeypatch.setenv(name, "1")
    monkeypatch.setenv("AM1_UNRELATED_TEST", "never-forward")
    session.validate_leader_selection("scripted", profile)
    cfg = SimpleNamespace(
        ssh_target="am1-test", remote_python="/motor/python", remote_helper="/session/helper.py",
        remote_session_repository="/session", remote_session_head="a" * 40,
        remote_camera_repository="/camera", remote_camera_head="b" * 40, remote_camera_python="/camera/python",
        remote_motor_repository="/motor", remote_motor_head="c" * 40,
        remote_log_directory="/logs", remote_state_directory="/state",
    )
    command = session.SSHRemote(cfg, "20261007T120000-test1234", tmp_path)._command()
    offset = command.index(cfg.ssh_target) + 1
    assert command[offset:offset + 4] == [
        "env", "AM1_LEFT_SHOULDER_EVIDENCE=1", "AM1_SCRIPTED_PREPARE=1", "AM1_SHOULDER_INTEGRAL_TEST=1",
    ]
    assert command[offset + 4] == cfg.remote_python
    assert not any("UNRELATED" in word or "never-forward" in word for word in command)
