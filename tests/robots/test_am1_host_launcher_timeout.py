"""Hardware-free regression for cold AM1 host launcher imports."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def load_remote_helper():
    path = Path(__file__).resolve().parents[2] / "tools" / "am1_session_remote.py"
    spec = importlib.util.spec_from_file_location("test_am1_launcher_remote", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def test_host_log_wait_covers_measured_cold_import_without_extending_homing_wait(tmp_path):
    module = load_remote_helper()
    args = type("Args", (), {
        "state_directory": str(tmp_path),
        "session_id": "20260926T120000-1234abcd",
        "camera_head": "camera", "motor_head": "motor", "session_head": "session",
        "log_directory": "/logs", "motor_repository": "/motor",
        "host_ready_timeout": 180.0,
    })()
    supervisor = module.RemoteSupervisor(args, module.BestEffortReporter(lambda payload: None))
    supervisor.children["camera"] = object()
    child = module.OwnedChild("host", object(), 100, tmp_path / "host-control.log")
    supervisor._spawn = lambda *args, **kwargs: child
    supervisor.save = lambda: None
    waits = []

    def wait_for(_child, _predicate, timeout_s, label):
        waits.append((label, timeout_s))
        if len(waits) == 1:
            return "/logs/exact-host.log"
        raise module.SessionRefusal("fake host not operational")

    supervisor._wait_for = wait_for

    with pytest.raises(module.SessionRefusal, match="fake host not operational"):
        supervisor.start_host()

    assert waits == [
        ("motor-host launcher", 30.0),
        ("motor host operational_ready", 180.0),
    ]
    assert supervisor.state["host_log"] == "/logs/exact-host.log"
