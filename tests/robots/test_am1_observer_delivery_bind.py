from __future__ import annotations

import base64
import importlib.util
import io
import json
import socket
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEST_ADDRESS = "192.0.2.23"


def observer():
    spec = importlib.util.spec_from_file_location("observer_bind_tests", ROOT / "tools/am1_observer.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "value",
    [
        "",
        " ",
        " 192.0.2.23",
        "192.0.2.23 ",
        "localhost",
        "example.test",
        "::1",
        "::ffff:192.0.2.23",
        "192.0.2.23/24",
        "192.0.2",
        "192.0.2.023",
        "192.0.2.256",
        "+192.0.2.23",
        "192.0.2.23 -oProxyCommand=bad",
        "0.0.0.0",
        "0.1.2.3",
        "127.0.0.1",
        "127.12.34.56",
        "224.0.0.1",
        "239.1.2.3",
        "240.1.2.3",
        "255.255.255.255",
        "192.0.2.255",
        "10.1.2.255",
        True,
        1234,
    ],
)
def test_invalid_binding_is_refused_before_config_output_staging_or_owner(tmp_path, monkeypatch, value):
    module = observer()
    args = SimpleNamespace(
        config=tmp_path / "does-not-exist.json",
        output_dir=tmp_path / "must-not-exist",
        generation="same-capture-test",
        duration_seconds=20,
        ssh_host="existing-alias",
        delivery_bind_address=value,
    )
    monkeypatch.setattr(module.subprocess, "run", lambda *_a, **_kw: pytest.fail("Staging attempted"))
    monkeypatch.setattr(module.subprocess, "Popen", lambda *_a, **_kw: pytest.fail("Owner attempted"))
    with pytest.raises(ValueError, match="IPv4"):
        module.run_capture(args)
    assert not args.output_dir.exists()


@pytest.mark.parametrize(
    "ipqos,compression,forwarding_only",
    [(False, False, False), (True, False, True), (True, True, False), (False, True, True)],
)
def test_binding_adds_only_exact_validated_source_option(ipqos, compression, forwarding_only):
    module = observer()
    opts = {"ipqos_none": ipqos, "compression": compression, "forwarding_only": forwarding_only}
    original = module._observer_forward_command("existing-alias", 54322, 54321, **opts)
    assert (
        module._observer_forward_command("existing-alias", 54322, 54321, bind_address=None, **opts)
        == original
    )
    selected = module._observer_forward_command(
        "existing-alias", 54322, 54321, bind_address=TEST_ADDRESS, **opts
    )
    index = selected.index("BindAddress=" + TEST_ADDRESS)
    assert selected[index - 1] == "-o"
    assert selected[: index - 1] + selected[index + 1 :] == original
    assert not any("BindAddress=" in value for value in module._ssh("existing-alias"))


@pytest.mark.parametrize("selected", [False, True])
def test_cli_binding_is_explicit_for_only_this_capture(monkeypatch, selected):
    module = observer()
    calls = []
    monkeypatch.setattr(module, "run_capture", lambda args: calls.append(args) or 0)
    argv = [
        "am1_observer.py",
        "--ssh-host",
        "existing-alias",
        "--config",
        "private.json",
        "--output-dir",
        "capture",
    ]
    if selected:
        argv += ["--delivery-bind-address", TEST_ADDRESS]
    monkeypatch.setattr(sys, "argv", argv)
    assert module.main() == 0
    assert calls[0].delivery_bind_address == (TEST_ADDRESS if selected else None)


@pytest.fixture
def capture_transports(tmp_path, monkeypatch):
    """Actual capture loop with one source and forwarding reattachment, entirely fake IO."""
    module = observer()
    config = tmp_path / "private.json"
    config.write_text(
        json.dumps(
            {
                "expected_host": "same-host",
                "camera_name": "camera",
                "video_device_id": "same-device",
                "remote_output_root": r"C:\private\observer",
            }
        )
    )
    args = SimpleNamespace(
        config=config,
        output_dir=tmp_path / "capture",
        generation="same-capture-test",
        duration_seconds=20,
        ssh_host="existing-alias",
        delivery_bind_address=TEST_ADDRESS,
        delivery_ipqos_none=False,
        delivery_compression=False,
    )
    ready = threading.Event()
    closed = threading.Event()
    state = SimpleNamespace(
        stages=[], commands=[], sent=[], owner_alive=False, ports=[], connections=0, errors=[]
    )

    class Probe:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def bind(self, _):
            pass

        def getsockname(self):
            port = 54322 + len(state.ports)
            state.ports.append(port)
            return "127.0.0.1", port

    class FrameStream(Probe):
        def __init__(self, number):
            self.number = number

        def readline(self, _limit):
            if self.number > 1 and not closed.wait(2):
                state.errors.append("fake reader close deadline")
            return b""

    class Connection:
        def __init__(self, number):
            self.number = number

        def makefile(self, _):
            return FrameStream(self.number)

        def setsockopt(self, *_):
            pass

        def settimeout(self, _):
            pass

        def sendall(self, line):
            state.sent.append(json.loads(line))

        def shutdown(self, _):
            if self.number > 1:
                closed.set()

        def close(self):
            self.shutdown(None)

    class MetadataStream:
        index = 0

        def readline(self, _limit):
            self.index += 1
            if self.index == 1:
                value = {"event": "started", "generation": args.generation, "recording": True}
            elif self.index == 2:
                if not ready.wait(2):
                    state.errors.append("same-capture reattachment deadline")
                value = {"event": "ending", "generation": args.generation}
            elif self.index == 3:
                owner.returncode = 0
                closed.set()
                value = {
                    "event": "complete",
                    "generation": args.generation,
                    "success": True,
                    "camera_released": True,
                    "recording_started_utc": "original-start",
                }
            else:
                return b""
            return (json.dumps(value) + "\n").encode()

    class Transport:
        def __init__(self, forwarding_only):
            self.forwarding_only = forwarding_only
            self.returncode = None
            self.stdout = None if forwarding_only else MetadataStream()

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            assert self.returncode is not None, "The fake source must end normally"
            return self.returncode

        def terminate(self):
            assert self.forwarding_only, "Source ownership cannot change during reattachment"
            self.returncode = 1

        def kill(self):
            pytest.fail("No forced release is needed")

    owner = Transport(False)

    def stage(command, **kwargs):
        assert not any("BindAddress=" in value for value in command)
        assert "input" in kwargs
        state.stages.append(json.loads(base64.b64decode(kwargs["input"].splitlines()[1])))
        return subprocess.CompletedProcess(command, 0, b'{"capture_port":54321}', b"")

    def popen(command, **kwargs):
        state.commands.append(list(command))
        if "-N" in command:
            state.owner_alive = owner.poll() is None
            ready.set()
            return Transport(True)
        assert len(state.commands) == 1
        return owner

    def connect(*_a, **_kw):
        state.connections += 1
        return Connection(state.connections)

    monkeypatch.setattr(module.subprocess, "run", stage)
    monkeypatch.setattr(module.subprocess, "Popen", popen)
    monkeypatch.setattr(socket, "socket", lambda *_a, **_kw: Probe())
    monkeypatch.setattr(socket, "create_connection", connect)
    yield module, args, state
    ready.set()
    closed.set()
    assert not state.errors


@pytest.mark.parametrize("bind_address,ipqos", [(None, False), (TEST_ADDRESS, False), (TEST_ADDRESS, True)])
def test_real_capture_bind_reaches_initial_and_reattach_with_private_evidence_only(
    capture_transports,
    capsys,
    bind_address,
    ipqos,
):
    module, args, state = capture_transports
    args.delivery_bind_address = bind_address
    args.delivery_ipqos_none = ipqos
    original = args.config.read_bytes()
    assert module.run_capture(args) == 1
    assert len(state.commands) == 2 and len(state.stages) == 1 and state.owner_alive
    first, replacement = state.commands
    assert "-N" not in first and "-N" in replacement
    assert "-EncodedCommand" in first and "-EncodedCommand" not in replacement
    for command in state.commands:
        assert ("BindAddress=" + TEST_ADDRESS in command) is (bind_address is not None)
        assert ("IPQoS=none" in command) is ipqos
    old = first[first.index("-L") + 1].split(":")
    new = replacement[replacement.index("-L") + 1].split(":")
    assert old[1] != new[1] and old[2:] == new[2:] == ["127.0.0.1", "54321"]
    assert all(
        value["generation"] == args.generation and value["token"] == state.stages[0]["token"]
        for value in state.sent
    )
    nonces = [value["nonce"] for value in state.sent if value["event"] == "frame"]
    assert len(nonces) >= 2 and len(set(nonces)) == len(nonces)
    assert args.config.read_bytes() == original and "delivery_bind_address" not in state.stages[0]
    summary = json.loads((args.output_dir / "receiver-result.json").read_text())
    evidence = json.loads((args.output_dir / "delivery-transport.json").read_text())
    assert summary.get("delivery_bind_address") == evidence.get("delivery_bind_address") == bind_address
    if bind_address is None:
        assert "delivery_bind_address" not in summary and "delivery_bind_address" not in evidence
    assert TEST_ADDRESS not in capsys.readouterr().out
    assert summary["failure"] is None and summary["same_capture_reconnects"] == 1
    assert summary["camera_released"] and summary["capture_success"]
    assert summary["accepted_frames"] == 0 and summary["qualification_at_stop"] is False


def test_nonlocal_valid_binding_ssh_failure_does_not_fallback_or_launch_camera(
    capture_transports, monkeypatch
):
    module, args, state = capture_transports
    refused = SimpleNamespace(returncode=255, stdout=io.BytesIO(), poll=lambda: 255)

    def binding_refused(command, **_kwargs):
        state.commands.append(list(command))
        assert "BindAddress=" + TEST_ADDRESS in command
        return refused

    monkeypatch.setattr(module.subprocess, "Popen", binding_refused)
    monkeypatch.setattr(
        socket, "create_connection", lambda *_a, **_kw: pytest.fail("Camera channel attempted")
    )
    assert module.run_capture(args) == 1
    assert len(state.commands) == 1 and len(state.stages) == 1
    summary = json.loads((args.output_dir / "receiver-result.json").read_text())
    assert summary["failure"] == "observer SSH closed before capture start"
    assert summary["same_capture_reconnects"] == 0 and summary["camera_released"] is False
    assert summary["accepted_frames"] == 0 and summary["qualification_at_stop"] is False
