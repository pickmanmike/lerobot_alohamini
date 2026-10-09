from __future__ import annotations

import base64
import importlib.util
import json
import socket
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


def observer():
    spec = importlib.util.spec_from_file_location("observer_ipqos_tests", ROOT / "tools/am1_observer.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_delivery_command_remains_exactly_the_existing_command():
    module = observer()
    assert module._observer_forward_command("existing-alias", 54322, 54321) == [
        "ssh",
        "-T",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ServerAliveInterval=10",
        "-o",
        "ServerAliveCountMax=2",
        "-o",
        "ExitOnForwardFailure=yes",
        "-L",
        "127.0.0.1:54322:127.0.0.1:54321",
        "existing-alias",
    ]


@pytest.mark.parametrize("compression", [False, True])
@pytest.mark.parametrize("forwarding_only", [False, True])
def test_ipqos_opt_in_adds_only_its_option_preserving_compression_and_forwarding(
    compression, forwarding_only
):
    module = observer()
    original = module._observer_forward_command(
        "existing-alias",
        54322,
        54321,
        compression=compression,
        forwarding_only=forwarding_only,
    )
    experiment = module._observer_forward_command(
        "existing-alias",
        54322,
        54321,
        compression=compression,
        forwarding_only=forwarding_only,
        ipqos_none=True,
    )
    option = experiment.index("IPQoS=none")
    assert experiment[option - 1] == "-o"
    assert experiment[: option - 1] + experiment[option + 1 :] == original
    assert "IPQoS=none" not in module._ssh("existing-alias")


@pytest.mark.parametrize("enabled", [False, True])
def test_cli_delivery_ipqos_is_explicit_for_this_invocation(monkeypatch, enabled):
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
    if enabled:
        argv.append("--delivery-ipqos-none")
    monkeypatch.setattr(sys, "argv", argv)
    assert module.main() == 0
    assert calls[0].delivery_ipqos_none is enabled


@pytest.mark.parametrize("enabled,compression", [(False, False), (True, False), (True, True)])
def test_capture_and_reattach_apply_ipqos_only_to_transport_preserving_original_source(
    tmp_path,
    monkeypatch,
    enabled,
    compression,
):
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
    original_config = config.read_bytes()
    args = SimpleNamespace(
        config=config,
        output_dir=tmp_path / "capture",
        generation="same-capture-test",
        duration_seconds=20,
        ssh_host="existing-alias",
        delivery_ipqos_none=enabled,
        delivery_compression=compression,
    )
    reattached = threading.Event()
    stop_delivered = threading.Event()
    reader_closed = threading.Event()
    state = SimpleNamespace(
        stages=[],
        processes=[],
        commands=[],
        sent=[],
        connections=0,
        ports=[],
        errors=[],
        owner_alive_at_reattach=False,
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

        def recv(self, limit):
            assert 0 < limit <= 65536
            if self.number > 1 and not reader_closed.wait(2):
                state.errors.append("replacement fake reader close deadline")
            return b""

    class Connection:
        def __init__(self, number):
            self.number = number
            self.stream = FrameStream(number)

        def recv(self, limit):
            return self.stream.recv(limit)

        def setsockopt(self, *_):
            pass

        def settimeout(self, _):
            pass

        def sendall(self, line):
            value = json.loads(line)
            state.sent.append(value)
            if value["event"] == "stop":
                stop_delivered.set()
                reader_closed.set()

        def shutdown(self, _):
            if self.number > 1:
                reader_closed.set()

        def close(self):
            self.shutdown(None)

    class MetadataStream:
        index = 0

        def readline(self, _limit):
            self.index += 1
            if self.index == 1:
                value = {"event": "started", "generation": args.generation, "recording": True}
            elif self.index == 2:
                if not reattached.wait(2):
                    state.errors.append("same-capture reattachment deadline")
                value = {"event": "ending", "generation": args.generation}
            elif self.index == 3:
                owner.returncode = 0
                reader_closed.set()
                value = {
                    "event": "complete",
                    "generation": args.generation,
                    "success": True,
                    "camera_released": True,
                    "recording_started_utc": "original-start",
                    "recording_stopped_utc": "original-stop",
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
            assert self.returncode is not None or stop_delivered.wait(timeout)
            self.returncode = self.returncode or 0
            return self.returncode

        def terminate(self):
            assert self.forwarding_only, "the source owner must survive reattachment"
            self.returncode = 1

        def kill(self):
            pytest.fail("No forced process release is needed in this finite fixture")

    owner = Transport(False)

    def stage(command, **kwargs):
        assert "IPQoS=none" not in command and "input" in kwargs
        state.stages.append(json.loads(base64.b64decode(kwargs["input"].splitlines()[1])))
        return subprocess.CompletedProcess(command, 0, b'{"capture_port":54321}', b"")

    def popen(command, **kwargs):
        state.commands.append(list(command))
        if "-N" in command:
            state.owner_alive_at_reattach = owner.poll() is None
            transport = Transport(True)
            reattached.set()
        else:
            assert not state.processes, "Only one source owner may be launched"
            transport = owner
        state.processes.append(transport)
        return transport

    def connect(*_a, **_kw):
        state.connections += 1
        return Connection(state.connections)

    monkeypatch.setattr(module.subprocess, "run", stage)
    monkeypatch.setattr(module.subprocess, "Popen", popen)
    monkeypatch.setattr(socket, "socket", lambda *_a, **_kw: Probe())
    monkeypatch.setattr(socket, "create_connection", connect)
    try:
        assert module.run_capture(args) == 1  # No frame can establish observation authority here.
    finally:
        stop_delivered.set()
        reader_closed.set()
        reattached.set()
    assert not state.errors
    assert len(state.stages) == 1 and len(state.commands) == 2 and state.owner_alive_at_reattach
    first, replacement = state.commands
    assert "-N" not in first and "-N" in replacement
    assert "-EncodedCommand" in first and "-EncodedCommand" not in replacement
    assert all(("IPQoS=none" in command) is enabled for command in state.commands)
    assert all(("Compression=yes" in command) is compression for command in state.commands)
    first_port = first[first.index("-L") + 1].split(":")
    next_port = replacement[replacement.index("-L") + 1].split(":")
    assert first_port[1] != next_port[1] and first_port[2:] == next_port[2:] == ["127.0.0.1", "54321"]
    assert all(
        value["generation"] == args.generation and value["token"] == state.stages[0]["token"]
        for value in state.sent
    )
    nonces = [value["nonce"] for value in state.sent if value["event"] == "frame"]
    assert len(nonces) >= 2 and len(set(nonces)) == len(nonces)
    assert config.read_bytes() == original_config
    assert "delivery_ipqos_none" not in state.stages[0]
    summary = json.loads((args.output_dir / "receiver-result.json").read_text())
    transport = json.loads((args.output_dir / "delivery-transport.json").read_text())
    assert summary["delivery_ipqos_none"] is enabled and transport["delivery_ipqos_none"] is enabled
    assert summary["failure"] is None and summary["same_capture_reconnects"] == 1
    assert summary["camera_released"] and summary["capture_success"]
    assert summary["accepted_frames"] == 0 and summary["qualification_at_stop"] is False
    result = json.loads((args.output_dir / "capture-result.json").read_text())
    assert result["recording_started_utc"] == "original-start"
