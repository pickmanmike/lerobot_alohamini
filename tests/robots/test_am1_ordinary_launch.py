"""Ordinary frontdoor metadata and policy, without ownership or devices."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from examples.alohamini.am1_live_teleop import ORDINARY_AM1_ARMS_PROFILE
from tools import am1_leader_adapter as adapter


def configuration(tmp_path):
    binding = {"kind": "am1-teleop28-feedback-loss-admitted-known-cleanup-bindings", "motor_head": "a"*40,
               "executor_head": "b"*40, "manifest_sha256": "c"*64}
    raw = json.dumps(binding).encode()
    (tmp_path / "bindings.json").write_bytes(raw)
    (tmp_path / "ca.pem").write_text("test certificate")
    (tmp_path / "state.json").write_text("{}")
    (tmp_path / "runs").mkdir()
    context = {"STAGER_SHA": "d"*64, "BINDINGS_HELPER_SHA": "e"*64, "BINDING_SHA": hashlib.sha256(raw).hexdigest(),
               "MANIFEST_SHA": "c"*64, "STAGE": "/home/pickmanmike/.local/share/am1-lift-startup-01/staging/stage-104",
               "MANIFEST": "/home/pickmanmike/.local/share/am1-physical-adapter-01/deployments/"+"c"*16+"/manifest.json"}
    (tmp_path / "audit-context.private.json").write_text(json.dumps(context))
    value = dict(version=1, bindings=dict(path="bindings.json", sha256=hashlib.sha256(raw).hexdigest()),
                 url="https://192.168.1.134:8443", ca="ca.pem", storage_state="state.json", output_root="runs",
                 duration_seconds=90, native_max_seconds=300,
                 ordinary_arm_profile=dict(profile=ORDINARY_AM1_ARMS_PROFILE, right_elbow_p=16))
    path = tmp_path / "ordinary-use.private.json"
    path.write_text(json.dumps(value))
    return path, value


def capture_main(monkeypatch, argv):
    captured = []
    async def run(args):
        captured.append(args)
        return {}
    monkeypatch.setattr(adapter, "run", run)
    adapter.main(argv)
    assert len(captured) == 1
    return captured[0]


def test_config_resolves_canonical_paths_defaults_and_exact_policy(tmp_path, monkeypatch):
    path, value = configuration(tmp_path)
    args = capture_main(monkeypatch, ["--ordinary-config", str(path), "--virtual-input", "interactive"])
    assert args.source == "virtual-leaders" and args.workload_segment == "ordinary-arms"
    assert args.duration_seconds == 90 and args.ordinary_arm_profile == value["ordinary_arm_profile"]
    assert args.bindings == str(tmp_path / "bindings.json")
    assert Path(args.output).parent == tmp_path / "runs"
    assert args.prepare_current_rest is False
    assert args.ca == str(tmp_path / "ca.pem")


@pytest.mark.parametrize("change", [
    {"extra": 1}, {"version": True}, {"native_max_seconds": 301}, {"duration_seconds": 181},
    {"duration_seconds": 0}, {"duration_seconds": float("nan")}, {"url": "http://robot"},
    {"url": "https://user:secret@robot"}, {"url": "https://robot/path"},
    {"ordinary_arm_profile": {"profile": ORDINARY_AM1_ARMS_PROFILE, "right_elbow_p": True}},
    {"ordinary_arm_profile": {"profile": "other", "right_elbow_p": 16}},
    {"bindings": {"path": "bindings.json", "sha256": "f"*64}},
])
def test_invalid_config_refuses_before_run(tmp_path, monkeypatch, change):
    path, value = configuration(tmp_path)
    value.update(change); path.write_text(json.dumps(value))
    monkeypatch.setattr(adapter, "run", lambda args: pytest.fail("invalid config reached ownership"))
    with pytest.raises(SystemExit):
        adapter.main(["--ordinary-config", str(path)])


@pytest.mark.parametrize("extra", [["--url", "https://other"], ["--ca", "different.pem"],
    ["--bindings", "different.json"], ["--output-root", "different"], ["--prepare-current-rest"],
    ["--left-entry"], ["--supported-grasp-closing"], ["--bench-continuity"]])
def test_config_conflicts_refuse_before_run(tmp_path, monkeypatch, extra):
    path, _ = configuration(tmp_path)
    monkeypatch.setattr(adapter, "run", lambda args: pytest.fail("conflict reached ownership"))
    with pytest.raises(SystemExit):
        adapter.main(["--ordinary-config", str(path), *extra])


def test_reviewed_p32_and_shorter_prospective_window_are_recorded(tmp_path, monkeypatch):
    path, _ = configuration(tmp_path)
    args = capture_main(monkeypatch, ["--ordinary-config", str(path), "--right-elbow-p", "32", "--duration-seconds", "60"])
    assert args.ordinary_arm_profile == {"profile": ORDINARY_AM1_ARMS_PROFILE, "right_elbow_p": 32}
    assert args.duration_seconds == 60
    body = adapter._start_body(args, "generation")
    assert body["ordinary_arm_profile"] == args.ordinary_arm_profile
    body["ordinary_arm_profile"]["right_elbow_p"] = 16
    assert args.ordinary_arm_profile["right_elbow_p"] == 32


def test_explicit_profile_frontdoor_and_usb_acquisition_are_separate(tmp_path, monkeypatch):
    path, value = configuration(tmp_path)
    common = ["--operating-profile", ORDINARY_AM1_ARMS_PROFILE, "--url", value["url"], "--ca", str(tmp_path/"ca.pem"),
              "--storage-state", str(tmp_path/"state.json"), "--bindings", str(tmp_path/"bindings.json"),
              "--output-root", str(tmp_path/"runs")]
    args = capture_main(monkeypatch, common)
    assert args.ordinary_arm_profile["right_elbow_p"] == 16 and not args.prepare_current_rest
    args = capture_main(monkeypatch, common + ["--source", "usb-leaders", "--left-port", "existing-left",
                        "--right-port", "existing-right", "--leader-id", "known", "--calibration-dir", "existing"])
    assert args.source == "usb-leaders" and args.left_port == "existing-left"


def test_normal_preparation_freezes_local_inputs_without_legacy_remote_request(tmp_path, monkeypatch):
    path, value = configuration(tmp_path)
    args = capture_main(monkeypatch, ["--ordinary-config", str(path)])
    from tools import am1_physical_executor
    monkeypatch.setattr(am1_physical_executor, "prepare_left_response_p_remote", lambda *a, **k: pytest.fail("legacy SSH request"))
    output = Path(args.output); output.mkdir()
    config = adapter.prepare_current_rest_invocation(args, output)
    receipt = json.loads((output/"preparation-receipt.private.json").read_text())
    assert receipt == {"version": 1, "status": "native-pending", "hardware_qualified": False,
                       "ordinary_arm_profile": value["ordinary_arm_profile"], "native_max_seconds": 300, "input_duration_seconds": 90}
    assert config["source"] == "virtual-leaders"
    assert (output/"ordinary-config.private.json").read_bytes() == path.read_bytes()
    metadata = json.loads((output/"launch-metadata.private.json").read_text())
    assert set(metadata) == {"version", "bindings", "invocation_config", "preparation_receipt", "audit_context"}
    assert (output/"bindings.private.json").read_bytes() == (tmp_path/"bindings.json").read_bytes()
    assert not any("nonce" in p.name or "request" in p.name for p in output.iterdir())


def test_normal_preparation_detects_binding_replacement_and_missing_audit(tmp_path, monkeypatch):
    path, _ = configuration(tmp_path)
    args = capture_main(monkeypatch, ["--ordinary-config", str(path)])
    (tmp_path/"bindings.json").write_text("{}")
    output=Path(args.output); output.mkdir()
    with pytest.raises(ValueError): adapter.prepare_current_rest_invocation(args, output)


def test_start_body_legacy_has_no_new_policy():
    args = SimpleNamespace(bench_continuity=False)
    assert set(adapter._start_body(args, "g")) == {"operation_id", "recipe", "controller_generation"}


def test_normal_preparation_requires_frozen_audit_context(tmp_path, monkeypatch):
    path, _ = configuration(tmp_path)
    args = capture_main(monkeypatch, ["--ordinary-config", str(path)])
    (tmp_path/"audit-context.private.json").unlink()
    output=Path(args.output); output.mkdir()
    with pytest.raises(ValueError, match="audit"):
        adapter.prepare_current_rest_invocation(args, output)


@pytest.mark.parametrize("text", ['{"version":1,"version":1}', '[]'])
def test_duplicate_or_nonobject_config_refuses(tmp_path, monkeypatch, text):
    path=tmp_path/"ordinary.json"; path.write_text(text)
    monkeypatch.setattr(adapter, "run", lambda args: pytest.fail("malformed config reached ownership"))
    with pytest.raises(SystemExit): adapter.main(["--ordinary-config", str(path)])


@pytest.mark.parametrize("change", ["p", "missing", "workspace", "reference", "deadline", "view"])
def test_ordinary_recovery_keeps_native_policy_and_original_authority(change):
    from tests.robots.test_am1_live_phase_coordinator import ordinary_admission
    from tools.am1_leader_adapter import SameRunRecovery
    ready, admitted=ordinary_admission()
    state=admitted["snapshot"]
    owner=state["controller"]
    client=SimpleNamespace(snapshot=state, device=owner["device_id"], generation=owner["controller_generation"])
    guard=SameRunRecovery(client, lambda event: None)
    current=json.loads(json.dumps(state))
    if change == "p": current["run"]["ordinary_arm_profile"]["right_elbow_p"] = 32
    elif change == "missing": current["run"].pop("ordinary_arm_profile")
    elif change == "workspace": current["run"]["live_input"]["workspace"]["reference"]["arm_left_elbow_flex.pos"] += .1
    elif change == "reference": current["run"]["live_input"]["reference"]["arm_left_elbow_flex.pos"] += .1
    elif change == "deadline": current["run"]["deadline"] += 1.
    else: current["run"]["live_input"]["required_view"]["required"] = False
    with pytest.raises(RuntimeError): guard.check(current)


def test_config_refuses_an_explicit_output_destination_before_ownership(tmp_path, monkeypatch):
    path,_=configuration(tmp_path)
    monkeypatch.setattr(adapter,"run",lambda args:pytest.fail("conflicting output reached ownership"))
    with pytest.raises(SystemExit):
        adapter.main(["--ordinary-config",str(path),"--output",str(tmp_path/"selected-output")])


@pytest.mark.parametrize("minutes,seconds", [(15, 900), (0.5, 30), (60, 3600)])
def test_ordinary_duration_uses_one_declared_budget(tmp_path, minutes, seconds):
    from examples.alohamini.am1_live_teleop import ordinary_live_s

    path, _ = configuration(tmp_path)
    args = SimpleNamespace(ordinary_config=str(path), session_minutes=minutes, virtual_input="interactive")
    adapter.resolve_ordinary_config(args)
    assert args.duration_seconds == args.ordinary_activity_seconds == seconds
    body = adapter._start_body(args, "controller")
    assert body["ordinary_activity_seconds"] == seconds
    assert ordinary_live_s(seconds) == seconds + 15


@pytest.mark.parametrize("value", [True, 0, 1.1, 3601, float("inf"), "900"])
def test_ordinary_activity_refuses_invalid_seconds(value):
    from examples.alohamini.am1_live_teleop import validate_ordinary_activity_s

    with pytest.raises(ValueError, match="activity"):
        validate_ordinary_activity_s(value)
