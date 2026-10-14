"""Session preflight rejects remapped recipes before remote ownership starts."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from test_am1_scripted_launchers import load_session, scripted_outcome

AMPLITUDE_ENV = "AM1_SCRIPTED_LEFT_SHOULDER_AMPLITUDE"


@pytest.mark.parametrize("raw", [
    "", " ", " 1.5", "1.5 ", "nan", "inf", "0", "-1", "3.01", "1_0", "one", "1e-9999", "1e309",
])
def test_invalid_amplitude_refuses_before_remote_factory_or_client_start(monkeypatch, tmp_path, raw):
    _assert_recipe_refuses_before_start(monkeypatch, tmp_path, raw, "scripted", "ArmSmoke", 180)


@pytest.mark.parametrize(("source", "profile", "duration"), [
    ("physical", None, 180), ("scripted", "ArmHoldBody", 12),
])
def test_valid_amplitude_outside_scripted_arm_scope_refuses_before_start(
    monkeypatch, tmp_path, source, profile, duration,
):
    _assert_recipe_refuses_before_start(monkeypatch, tmp_path, "1.5", source, profile, duration)


def _assert_recipe_refuses_before_start(monkeypatch, tmp_path, raw, source, profile, duration):
    module = load_session()
    calls = []
    monkeypatch.setenv(AMPLITUDE_ENV, raw)
    monkeypatch.delenv("AM1_SCRIPTED_PREPARE", raising=False)
    monkeypatch.delenv("AM1_SHOULDER_INTEGRAL_TEST", raising=False)
    monkeypatch.setattr(module, "_git_head", lambda _: "a" * 40)
    monkeypatch.setattr(module, "_active_path", lambda _: tmp_path / "active.json")
    monkeypatch.setattr(module, "_write_active", lambda *args: None)

    def remote_factory(*args):
        calls.append("remote_factory")
        pytest.fail("remote factory started before amplitude recipe validation")

    def client_factory(*args, **kwargs):
        calls.append("client_factory")
        pytest.fail("client factory started before amplitude recipe validation")

    monkeypatch.setattr(module, "SSHRemote", remote_factory)
    monkeypatch.setattr(module, "WindowsClient", client_factory)
    config = SimpleNamespace(windows_log_directory=tmp_path, local_state_directory=tmp_path)
    with pytest.raises(ValueError, match="AMPLITUDE"):
        module._run_start_locked(tmp_path, config, duration, leader_source=source, motion_profile=profile)
    assert calls == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("raw", ["1.5", "3", ".5", "1.5e0"])
def test_valid_amplitude_preserves_normal_scripted_coordinator_completion(monkeypatch, tmp_path, raw):
    module = load_session()
    monkeypatch.setenv(AMPLITUDE_ENV, raw)
    monkeypatch.delenv("AM1_SCRIPTED_PREPARE", raising=False)
    monkeypatch.delenv("AM1_SHOULDER_INTEGRAL_TEST", raising=False)
    outcome = scripted_outcome(module, tmp_path)
    assert outcome.final_exit_code == 0 and outcome.stop_reason == "script_complete"
    assert outcome.cleanup_verified is True
    assert outcome.input_source == "scripted" and outcome.motion_profile == "ArmSmoke"
