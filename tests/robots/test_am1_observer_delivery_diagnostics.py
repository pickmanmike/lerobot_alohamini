from __future__ import annotations

import base64
import importlib.util
import io
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]


def observer():
    spec = importlib.util.spec_from_file_location("observer_delivery_tests", ROOT / "tools/am1_observer.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source_timing(**updates):
    record = {
        "event": "frame_delivery_timing",
        "generation": "diagnostic-test",
        "nonce": "original-1",
        "sequence": 1,
        "source_system_relative_ticks": 1_100_000,
        "challenge_received_qpc_ticks": 1_000_000,
        "capture_age_ms": 20.0,
        "serialize_start_qpc_ticks": 1_300_000,
        "serialized_qpc_ticks": 1_310_000,
        "send_start_qpc_ticks": 1_320_000,
        "send_ended_qpc_ticks": 1_330_000,
        "metadata_emit_started_qpc_ticks": 1_340_000,
        "jpeg_bytes": 1000,
        "sent": True,
        "previous_metadata_sequence": 0,
        "previous_metadata_started_qpc_ticks": 0,
        "previous_metadata_ended_qpc_ticks": 0,
    }
    return {**record, **updates}


def retain_source(diagnostic, **updates):
    return diagnostic.source(
        source_timing(**updates),
        received_wall_time_ms=1000,
        received_monotonic_ms=150.0,
        processing_started_ms=151.0,
    )


def rows(tmp_path):
    return [json.loads(line) for line in (tmp_path / "delivery-timing.ndjson").read_text().splitlines()]


def test_diagnostics_are_off_without_creating_a_file_or_mutating_health(tmp_path):
    module = observer()
    receiver = module.ObserverReceiver(tmp_path, "diagnostic-test")
    original = (tmp_path / "latest-health.json").read_bytes()
    diagnostic = module.ObserverDeliveryDiagnostics(tmp_path, "diagnostic-test")
    assert not retain_source(diagnostic)
    assert not (tmp_path / "delivery-timing.ndjson").exists()
    assert (tmp_path / "latest-health.json").read_bytes() == original
    assert diagnostic.snapshot()["enabled"] is False
    assert receiver.accepted_frames == 0


def test_source_diagnostic_retains_original_identity_and_both_clock_domains_without_authority(tmp_path):
    module = observer()
    receiver = module.ObserverReceiver(tmp_path, "diagnostic-test")
    receiver.issue_challenge("original-1", 100.0)
    original = (tmp_path / "latest-health.json").read_bytes()
    proofs = dict(receiver.challenges)
    diagnostic = module.ObserverDeliveryDiagnostics(tmp_path, "diagnostic-test", enabled=True)
    try:
        assert retain_source(diagnostic)
        actual = rows(tmp_path)[0]
        assert actual["generation"] == "diagnostic-test"
        assert actual["nonce"] == "original-1" and actual["sequence"] == 1
        assert actual["send_ended_qpc_ticks"] == 1_330_000
        assert actual["received_local_perf_counter_ms"] == 150.0
        assert actual["processing_started_monotonic_ms"] == 151.0
        assert receiver.challenges == proofs
        assert (tmp_path / "latest-health.json").read_bytes() == original
        assert receiver.accepted_frames == 0 and receiver.state["running"] is False
    finally:
        diagnostic.close()


def test_stale_source_timing_is_preserved_but_does_not_qualify_a_frame(tmp_path):
    module = observer()
    receiver = module.ObserverReceiver(tmp_path, "diagnostic-test")
    receiver.issue_challenge("original-1", 100.0)
    original = (tmp_path / "latest-health.json").read_bytes()
    diagnostic = module.ObserverDeliveryDiagnostics(tmp_path, "diagnostic-test", enabled=True)
    try:
        assert retain_source(diagnostic, capture_age_ms=2500.0)
        assert rows(tmp_path)[0]["capture_age_ms"] == 2500.0
        assert (tmp_path / "latest-health.json").read_bytes() == original
        assert receiver.accepted_frames == 0
        assert not receiver.accept(
            {**source_timing(capture_age_ms=2500.0), "event": "frame", "recording": True},
            received_wall_time_ms=1000,
            received_monotonic_ms=150.0,
        )
        assert receiver.last_rejection["reason"] == "observer capture stale or recording inactive"
    finally:
        diagnostic.close()


@pytest.mark.parametrize(
    "updates",
    [
        {"generation": "other-generation"},
        {"nonce": "bad nonce"},
        {"sequence": True},
        {"serialize_start_qpc_ticks": 1_090_000},
        {"serialized_qpc_ticks": 1_299_999},
        {"send_start_qpc_ticks": 1_309_999},
        {"send_ended_qpc_ticks": 1_319_999},
        {"metadata_emit_started_qpc_ticks": 1_329_999},
        {"sent": 1},
        {"capture_age_ms": float("nan")},
        {"capture_age_ms": 10**400},
        {"previous_metadata_sequence": 1},
        {"previous_metadata_started_qpc_ticks": 1},
        {"unexpected": "x" * 1024},
    ],
)
def test_invalid_source_diagnostic_is_incomplete_without_changing_observation(tmp_path, updates):
    module = observer()
    receiver = module.ObserverReceiver(tmp_path, "diagnostic-test")
    original = (tmp_path / "latest-health.json").read_bytes()
    diagnostic = module.ObserverDeliveryDiagnostics(tmp_path, "diagnostic-test", enabled=True)
    try:
        assert not retain_source(diagnostic, **updates)
        assert diagnostic.snapshot()["incomplete"] is True
        assert diagnostic.snapshot()["rejected_records"] == 1
        assert diagnostic.snapshot()["source_records"] == 0
        assert (tmp_path / "latest-health.json").read_bytes() == original
        assert receiver.accepted_frames == 0
    finally:
        diagnostic.close()


def test_caps_keep_existing_evidence_and_do_not_stop_the_observer(tmp_path, monkeypatch):
    module = observer()
    monkeypatch.setattr(module, "DELIVERY_DIAGNOSTIC_MAX_RECORDS", 2)
    diagnostic = module.ObserverDeliveryDiagnostics(tmp_path, "diagnostic-test", enabled=True)
    try:
        assert retain_source(diagnostic)
        assert retain_source(diagnostic, sequence=2)
        original = (tmp_path / "delivery-timing.ndjson").read_bytes()
        assert not retain_source(diagnostic, sequence=3)
        assert (tmp_path / "delivery-timing.ndjson").read_bytes() == original
        assert diagnostic.snapshot()["source_records"] == 2
        assert diagnostic.snapshot()["dropped_records"] == 1
        assert diagnostic.snapshot()["incomplete"] is True
    finally:
        diagnostic.close()


def test_total_byte_cap_never_appends_a_partial_row(tmp_path, monkeypatch):
    module = observer()
    monkeypatch.setattr(module, "DELIVERY_DIAGNOSTIC_MAX_BYTES", 1024)
    diagnostic = module.ObserverDeliveryDiagnostics(tmp_path, "diagnostic-test", enabled=True)
    try:
        assert retain_source(diagnostic)
        original = (tmp_path / "delivery-timing.ndjson").read_bytes()
        assert not retain_source(diagnostic, sequence=2)
        assert (tmp_path / "delivery-timing.ndjson").read_bytes() == original
        assert diagnostic.snapshot()["bytes_written"] == len(original)
        assert diagnostic.snapshot()["incomplete"] is True
    finally:
        diagnostic.close()


def test_full_identity_and_realistic_qpc_precision_fit_the_original_row_bound(tmp_path):
    module = observer()
    generation = "g" * 80
    diagnostic = module.ObserverDeliveryDiagnostics(tmp_path, generation, enabled=True)
    record = source_timing(
        generation=generation,
        nonce="n" * 80,
        sequence=7000,
        challenge_received_qpc_ticks=1_003_484_973_490_117,
        source_system_relative_ticks=1_003_484_975_241_144,
        serialize_start_qpc_ticks=1_003_484_976_600_111,
        serialized_qpc_ticks=1_003_484_976_612_123,
        send_start_qpc_ticks=1_003_484_976_613_234,
        send_ended_qpc_ticks=1_003_484_976_618_345,
        metadata_emit_started_qpc_ticks=1_003_484_976_619_456,
        previous_metadata_sequence=6999,
        previous_metadata_started_qpc_ticks=1_003_484_975_619_456,
        previous_metadata_ended_qpc_ticks=1_003_484_975_624_567,
        capture_age_ms=127.5625123456789,
        jpeg_bytes=524288,
    )
    try:
        assert diagnostic.source(
            record,
            received_wall_time_ms=1_791_434_580_175,
            received_monotonic_ms=1_516_321_096.6346,
            processing_started_ms=1_516_321_096.7357,
        )
        retained = rows(tmp_path)[0]
        for key, value in record.items():
            assert retained[key] == value
        assert len((tmp_path / "delivery-timing.ndjson").read_bytes()) <= 1024
    finally:
        diagnostic.close()


def test_file_failure_keeps_original_health_and_diagnostic_failure_is_bounded(tmp_path, monkeypatch):
    module = observer()
    receiver = module.ObserverReceiver(tmp_path, "diagnostic-test")
    original = (tmp_path / "latest-health.json").read_bytes()

    class RefusedFile:
        def write(self, _):
            raise OSError("private file error " * 100)

        def flush(self):
            pytest.fail("Flush followed a failed write")

        def close(self):
            pass

    diagnostic = module.ObserverDeliveryDiagnostics(tmp_path, "diagnostic-test", enabled=True)
    diagnostic.stream.close()
    monkeypatch.setattr(diagnostic, "stream", RefusedFile())
    try:
        assert not retain_source(diagnostic)
        assert diagnostic.snapshot()["incomplete"] is True
        assert len(diagnostic.snapshot()["first_error"]) <= 160
        assert diagnostic.snapshot()["source_records"] == 0
        assert (tmp_path / "latest-health.json").read_bytes() == original
        assert receiver.accepted_frames == 0
    finally:
        diagnostic.close()


@pytest.mark.parametrize(
    "summary",
    [
        None,
        {"enabled": True, "records": 2, "incomplete": False},
        {"enabled": True, "records": 1, "incomplete": True},
    ],
)
def test_missing_or_incomplete_source_diagnostics_cannot_claim_complete_evidence(tmp_path, summary):
    module = observer()
    receiver = module.ObserverReceiver(tmp_path, "diagnostic-test")
    original = (tmp_path / "latest-health.json").read_bytes()
    diagnostic = module.ObserverDeliveryDiagnostics(tmp_path, "diagnostic-test", enabled=True)
    try:
        assert retain_source(diagnostic)
        diagnostic.finish(summary)
        assert diagnostic.snapshot()["incomplete"] is True
        assert (tmp_path / "latest-health.json").read_bytes() == original
        assert receiver.accepted_frames == 0
    finally:
        diagnostic.close()


@pytest.mark.parametrize(
    "read_delay,processing_delay,want_read,want_queue",
    [
        (600.0, 0.0, 600.0, 0.0),
        (100.0, 500.0, 100.0, 500.0),
    ],
)
def test_delivery_stall_and_publication_stall_have_distinct_original_timings(
    tmp_path,
    read_delay,
    processing_delay,
    want_read,
    want_queue,
):
    module = observer()
    now = [100.0]
    messages = []

    class Stream:
        delivered = False

        def readline(self, limit):
            if self.delivered:
                return b""
            self.delivered = True
            now[0] += read_delay
            return b'{"event":"frame"}\n'

    def enqueue(kind, line, epoch, read_started_monotonic_ms=None):
        messages.append(
            module._observer_message(
                kind,
                line,
                epoch,
                read_started_monotonic_ms,
                clock_ms=lambda: now[0],
                wall_ms=lambda: 1000,
            )
        )

    module._read_observer_lines(Stream(), enqueue, 1, clock_ms=lambda: now[0])
    _, _, receipt, wall, _, began = messages[0]
    now[0] += processing_delay
    diagnostic = module.ObserverDeliveryDiagnostics(tmp_path, "diagnostic-test", enabled=True)
    try:
        assert diagnostic.receiver(
            source_timing(),
            read_started_ms=began,
            received_monotonic_ms=receipt,
            received_wall_time_ms=wall,
            processing_started_ms=now[0],
            processing_completed_ms=now[0] + 2.0,
            accepted=False,
        )
        actual = rows(tmp_path)[0]
        assert actual["read_wait_ms"] == want_read
        assert actual["queue_delay_ms"] == want_queue
        assert actual["processing_ms"] == 2.0
        assert actual["received_local_perf_counter_ms"] == 100.0 + read_delay
        assert actual["received_wall_time_ms"] == 1000
        assert actual["accepted"] is False
    finally:
        diagnostic.close()


def test_processing_timings_cannot_renew_freshness_or_an_original_nonce(tmp_path):
    module = observer()
    pixels = io.BytesIO()
    Image.new("RGB", (640, 360)).save(pixels, format="JPEG")
    wire = {
        **source_timing(),
        "event": "frame",
        "recording": True,
        "jpeg_base64": base64.b64encode(pixels.getvalue()).decode(),
    }
    receiver = module.ObserverReceiver(tmp_path, "diagnostic-test")
    receiver.issue_challenge("original-1", 100.0)
    assert receiver.accept(wire, received_wall_time_ms=1000, received_monotonic_ms=150.0)
    diagnostic = module.ObserverDeliveryDiagnostics(tmp_path, "diagnostic-test", enabled=True)
    try:
        original = (tmp_path / "latest-health.json").read_bytes()
        assert diagnostic.receiver(
            wire,
            read_started_ms=110.0,
            received_monotonic_ms=150.0,
            received_wall_time_ms=1000,
            processing_started_ms=650.0,
            processing_completed_ms=700.0,
            accepted=True,
        )
        assert (tmp_path / "latest-health.json").read_bytes() == original
        receiver.expire(611.0)
        assert receiver.state["running"] is False
        assert receiver.state["received_wall_time_ms"] == 1000
        assert not receiver.accept(
            {**wire, "sequence": 2, "source_system_relative_ticks": 1_110_000},
            received_wall_time_ms=1701,
            received_monotonic_ms=851.0,
        )
    finally:
        diagnostic.close()


def test_blocked_actual_health_publication_records_processing_and_next_frame_queue_lag(tmp_path, monkeypatch):
    module = observer()
    pixels = io.BytesIO()
    Image.new("RGB", (640, 360)).save(pixels, format="JPEG")
    wire = {
        **source_timing(),
        "event": "frame",
        "recording": True,
        "jpeg_base64": base64.b64encode(pixels.getvalue()).decode(),
    }
    receiver = module.ObserverReceiver(tmp_path, "diagnostic-test")
    receiver.issue_challenge("original-1", 100.0)
    now = [100.0]
    messages = []
    values = [wire, {**wire, "sequence": 2, "source_system_relative_ticks": 2_100_000}]

    class Stream:
        index = 0

        def readline(self, _limit):
            if self.index == len(values):
                return b""
            now[0] += 50.0 if self.index == 0 else 100.0
            line = (json.dumps(values[self.index]) + "\n").encode()
            self.index += 1
            return line

    def enqueue(kind, line, epoch, read_started_monotonic_ms=None):
        messages.append(
            module._observer_message(
                kind,
                line,
                epoch,
                read_started_monotonic_ms,
                clock_ms=lambda: now[0],
                wall_ms=lambda: int(now[0] + 850),
            )
        )

    module._read_observer_lines(Stream(), enqueue, 1, clock_ms=lambda: now[0])
    assert [item[2] for item in messages] == [150.0, 250.0]
    original_publish = module._atomic_json
    stalled = []

    def blocked_publish(path, value):
        if path.name == "latest-health.json" and value.get("sequence") == 1 and not stalled:
            stalled.append(True)
            now[0] += 500.0  # Stall the real receiver._save boundary; the reader receipts already exist.
        return original_publish(path, value)

    monkeypatch.setattr(module, "_atomic_json", blocked_publish)
    monkeypatch.setattr(module.time, "perf_counter", lambda: now[0] / 1000)
    diagnostic = module.ObserverDeliveryDiagnostics(tmp_path, "diagnostic-test", enabled=True)
    try:
        for _, line, receipt, wall, _, read_started in messages:
            began = now[0]
            value = json.loads(line)
            accepted = receiver.accept(value, received_wall_time_ms=wall, received_monotonic_ms=receipt)
            assert accepted
            assert diagnostic.receiver(
                value,
                read_started_ms=read_started,
                received_monotonic_ms=receipt,
                received_wall_time_ms=wall,
                processing_started_ms=began,
                processing_completed_ms=now[0],
                accepted=accepted,
            )
        first, second = rows(tmp_path)
        assert first["processing_ms"] == 500.0 and first["read_wait_ms"] == 50.0
        assert second["queue_delay_ms"] == 500.0 and second["read_wait_ms"] == 100.0
        assert second["received_local_perf_counter_ms"] == 250.0
        assert receiver.last_received_monotonic_ms == 250.0
        assert receiver.state["received_wall_time_ms"] == 1100
        assert receiver.challenges["original-1"][0] == 100.0
        receiver.expire(now[0])  # The unchanged capture loop expires from original receipts on its next pass.
        assert receiver.state["running"] is False
        assert receiver.state["received_wall_time_ms"] == 1100
    finally:
        diagnostic.close()


@pytest.mark.skipif(sys.platform != "win32", reason="actual PowerShell timing emitter")
def test_source_emits_after_send_and_retains_measured_previous_stdout_overhead(tmp_path):
    source = str(ROOT / "tools/am1_observer_capture.ps1").replace("'", "''")
    script = f"""
$ErrorActionPreference='Stop'
$ast=[System.Management.Automation.Language.Parser]::ParseFile('{source}',[ref]$null,[ref]$null)
$functions=$ast.FindAll({{param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Send-ObserverDeliveryTiming'}},$true)
if($functions.Count -ne 1) {{ throw 'Source timing emitter is missing' }}
Invoke-Expression $functions[0].Extent.Text
$state=@{{enabled=$true;records=0;bytes=0;incomplete=$false;dropped=0;previous_sequence=0;previous_start=0;previous_end=0;max_emit_ticks=0}}
$clock=[Collections.Generic.Queue[long]]::new()
@(1340000,1390000,2340000,2410000) | ForEach-Object {{$clock.Enqueue($_)}}
$lines=[Collections.Generic.List[string]]::new()
$record=@{{event='frame_delivery_timing';generation='diagnostic-test';nonce='original-1';sequence=1;source_system_relative_ticks=1100000;challenge_received_qpc_ticks=1000000;capture_age_ms=20.0;serialize_start_qpc_ticks=1300000;serialized_qpc_ticks=1310000;send_start_qpc_ticks=1320000;send_ended_qpc_ticks=1330000;jpeg_bytes=1000;sent=$true}}
Send-ObserverDeliveryTiming -State $state -Record $record -Clock {{$clock.Dequeue()}} -WriteLine {{$lines.Add($args[0])}}
$record.sequence=2;$record.send_ended_qpc_ticks=2330000
Send-ObserverDeliveryTiming -State $state -Record $record -Clock {{$clock.Dequeue()}} -WriteLine {{$lines.Add($args[0])}}
@{{lines=$lines.ToArray();state=$state}} | ConvertTo-Json -Depth 5 -Compress
"""
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    first, second = map(json.loads, result["lines"])
    assert first["send_ended_qpc_ticks"] == 1_330_000
    assert first["metadata_emit_started_qpc_ticks"] == 1_340_000
    assert second["previous_metadata_sequence"] == 1
    assert second["previous_metadata_started_qpc_ticks"] == 1_340_000
    assert second["previous_metadata_ended_qpc_ticks"] == 1_390_000
    assert second["metadata_emit_started_qpc_ticks"] == 2_340_000
    assert result["state"]["max_emit_ticks"] == 70_000
    assert all(len(line.encode("utf-8")) + 1 <= 1024 for line in result["lines"])
    assert result["state"]["bytes"] == sum(len((line + os.linesep).encode()) for line in result["lines"])


@pytest.mark.skipif(sys.platform != "win32", reason="actual PowerShell timing emitter")
def test_source_diagnostic_limits_and_failed_sink_do_not_stop_capture():
    source = str(ROOT / "tools/am1_observer_capture.ps1").replace("'", "''")
    script = f"""
$ErrorActionPreference='Stop'
$ast=[System.Management.Automation.Language.Parser]::ParseFile('{source}',[ref]$null,[ref]$null)
$functions=$ast.FindAll({{param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Send-ObserverDeliveryTiming'}},$true)
Invoke-Expression $functions[0].Extent.Text
$results=[Collections.Generic.List[object]]::new()
foreach($mode in @('disabled','records','bytes','line','sink')) {{
    $state=@{{enabled=$true;records=0;bytes=0;incomplete=$false;dropped=0;previous_sequence=0;previous_start=0;previous_end=0;max_emit_ticks=0}}
    $record=@{{sequence=1;nonce='original'}}
    $lines=[Collections.Generic.List[string]]::new()
    $writer={{$lines.Add($args[0])}}
    if($mode -eq 'disabled') {{ $state.enabled=$false }}
    if($mode -eq 'records') {{ $state.records=8192 }}
    if($mode -eq 'bytes') {{ $state.bytes=8388608 }}
    if($mode -eq 'line') {{ $record.nonce='n'*1024 }}
    if($mode -eq 'sink') {{ $writer={{throw 'Diagnostic stdout refused'}} }}
    Send-ObserverDeliveryTiming -State $state -Record $record -Clock {{1}} -WriteLine $writer
    $results.Add(@{{mode=$mode;lines=$lines.Count;incomplete=$state.incomplete;dropped=$state.dropped;continued=$true}})
}}
$results.ToArray() | ConvertTo-Json -Depth 4 -Compress
"""
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    results = json.loads(completed.stdout)
    assert all(result["continued"] and result["lines"] == 0 for result in results)
    assert results[0]["incomplete"] is False and results[0]["dropped"] == 0
    assert all(result["incomplete"] and result["dropped"] == 1 for result in results[1:])


@pytest.mark.parametrize("enabled", [False, True])
def test_cli_delivery_diagnostics_are_explicit_and_only_forwarded_to_this_capture(monkeypatch, enabled):
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
        argv.append("--delivery-diagnostics")
    monkeypatch.setattr(sys, "argv", argv)
    assert module.main() == 0
    assert calls[0].delivery_diagnostics is enabled


@pytest.mark.parametrize("enabled", [False, True])
def test_opt_in_source_metadata_is_evidence_only_and_config_is_not_persisted(tmp_path, monkeypatch, enabled):
    import socket

    module = observer()
    config = tmp_path / "private.json"
    config.write_text(
        json.dumps(
            {
                "expected_host": "same-host",
                "camera_name": "camera",
                "video_device_id": "same-device",
                "remote_output_root": r"C:\private\observer",
                "delivery_diagnostics": True,
            }
        )
    )
    original = config.read_bytes()
    args = SimpleNamespace(
        config=config,
        output_dir=tmp_path / "capture",
        generation="diagnostic-test",
        duration_seconds=20,
        ssh_host="existing-alias",
        delivery_diagnostics=enabled,
    )
    staged = []

    def stage(command, **kwargs):
        staged.append(json.loads(base64.b64decode(kwargs["input"].splitlines()[1])))
        return subprocess.CompletedProcess(command, 0, b'{"capture_port":54321}', b"")

    class Probe:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def bind(self, _):
            pass

        def getsockname(self):
            return "127.0.0.1", 54322

    class FinishedOwner:
        returncode = 0
        terminal = {
            "event": "complete",
            "generation": "diagnostic-test",
            "success": True,
            "camera_released": True,
        }
        stdout = io.BytesIO((json.dumps(source_timing()) + "\n" + json.dumps(terminal) + "\n").encode())

        def poll(self):
            return 0

    monkeypatch.setattr(module.subprocess, "run", stage)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *_a, **_kw: FinishedOwner())
    monkeypatch.setattr(socket, "socket", lambda *_a, **_kw: Probe())
    monkeypatch.setattr(socket, "create_connection", lambda *_a, **_kw: pytest.fail("Connection attempted"))
    monkeypatch.setattr(module.ObserverReceiver, "accept", lambda *_a, **_kw: pytest.fail("Timing qualified"))
    assert module.run_capture(args) == 1  # No frames qualify a camera-only metadata fixture.
    assert staged[0]["delivery_diagnostics"] is enabled
    assert config.read_bytes() == original
    result = json.loads((args.output_dir / "receiver-result.json").read_text())
    assert result["failure"] is None
    assert result["accepted_frames"] == 0
    assert result["qualification_at_stop"] is False
    if enabled:
        assert result["delivery_diagnostics"]["source_records"] == 1
        assert result["delivery_diagnostics"]["receiver_records"] == 0
        assert rows(args.output_dir)[0]["send_ended_qpc_ticks"] == 1_330_000
    else:
        assert "delivery_diagnostics" not in result
        assert not (args.output_dir / "delivery-timing.ndjson").exists()


@pytest.fixture
def capture_pipeline(tmp_path, monkeypatch):
    """Exercise the capture loop with real reader threads and no socket or source process."""
    import socket

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
        generation="diagnostic-test",
        duration_seconds=20,
        ssh_host="existing-alias",
        delivery_diagnostics=True,
    )
    state = SimpleNamespace(
        drop_wire=False,
        dropped=False,
        challenge=None,
        challenge_ticks=None,
        received=[],
        publications=[],
        errors=[],
    )
    challenged = threading.Event()
    publishing = threading.Event()
    second_received = threading.Event()
    finish = threading.Event()
    stopped = threading.Event()
    pixels = io.BytesIO()
    Image.new("RGB", (640, 360)).save(pixels, format="JPEG")
    encoded = base64.b64encode(pixels.getvalue()).decode()

    class Probe:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def bind(self, _):
            pass

        def getsockname(self):
            return "127.0.0.1", 54322

    class FrameStream(Probe):
        index = 0

        def readline(self, _limit):
            if self.index == 2 or (self.index == 1 and state.drop_wire):
                stopped.wait(2)
                return b""
            ready = challenged if self.index == 0 else publishing
            if not ready.wait(2):
                state.errors.append("fake wire source readiness deadline")
                return b""
            time.sleep(0.002 if self.index == 0 else 0.02)
            self.index += 1
            value = {
                "event": "frame",
                "generation": args.generation,
                "nonce": state.challenge,
                "sequence": self.index,
                "challenge_received_qpc_ticks": state.challenge_ticks,
                "source_system_relative_ticks": int(time.perf_counter() * 10_000_000),
                "capture_age_ms": 0.0,
                "recording": True,
                "jpeg_base64": encoded,
            }
            return (json.dumps(value) + "\n").encode()

    class Connection:
        stream = FrameStream()

        def makefile(self, _):
            return self.stream

        def setsockopt(self, *_):
            pass

        def settimeout(self, _):
            pass

        def sendall(self, line):
            value = json.loads(line)
            if value["event"] == "stop":
                finish.set()
                stopped.set()
            elif state.challenge is None:
                state.challenge, state.challenge_ticks = value["nonce"], int(time.perf_counter() * 10_000_000)
                challenged.set()

        def shutdown(self, _):
            stopped.set()

        def close(self):
            stopped.set()

    class MetadataStream:
        index = 0

        def readline(self, _limit):
            self.index += 1
            if self.index == 1:
                value = {"event": "started", "generation": args.generation, "recording": True}
            elif self.index == 2:
                if not finish.wait(3):
                    state.errors.append("fake owner finalization deadline")
                value = {"event": "ending", "generation": args.generation}
            elif self.index == 3:
                owner.returncode = 0
                value = {
                    "event": "complete",
                    "generation": args.generation,
                    "success": True,
                    "camera_released": True,
                    "delivery_diagnostics": {"enabled": True, "records": 0, "incomplete": False},
                }
            else:
                return b""
            return (json.dumps(value) + "\n").encode()

    class Owner:
        returncode = None
        stdout = MetadataStream()

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            assert finish.wait(timeout)
            self.returncode = 0
            return 0

    owner = Owner()
    original_message = module._observer_message
    original_publish = module._atomic_json

    def message(kind, line, epoch, read_started_monotonic_ms=None):
        item = original_message(kind, line, epoch, read_started_monotonic_ms)
        if kind == "frame":
            state.received.append(item)
            if json.loads(line)["sequence"] == 2:
                second_received.set()
        return item

    def publish(path, value):
        if path.name == "latest-health.json":
            state.publications.append(dict(value))
            if value.get("sequence") == 1 and value.get("running") is True and not publishing.is_set():
                publishing.set()
                assert second_received.wait(2)
                time.sleep(0.55)  # The actual health publisher blocks while the byte reader queues frame 2.
            if value.get("sequence") == 2 and value.get("running") is True:
                finish.set()
        return original_publish(path, value)

    real_queue = queue.Queue

    class CaptureQueue(real_queue):
        def put(self, item, *positional, **kwargs):
            if state.drop_wire and not state.dropped and item[0] == "frame":
                state.dropped = True
                raise queue.Full
            return super().put(item, *positional, **kwargs)

    monkeypatch.setattr(module, "_observer_message", message)
    monkeypatch.setattr(module, "_atomic_json", publish)
    monkeypatch.setattr(module.queue, "Queue", CaptureQueue)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, b'{"capture_port":54321}', b""),
    )
    monkeypatch.setattr(module.subprocess, "Popen", lambda *_a, **_kw: owner)
    monkeypatch.setattr(socket, "socket", lambda *_a, **_kw: Probe())
    monkeypatch.setattr(socket, "create_connection", lambda *_a, **_kw: Connection())
    yield module, args, state
    finish.set()
    stopped.set()
    assert not state.errors


def test_capture_loop_retains_real_publisher_stall_and_original_queued_frame_receipt(capture_pipeline):
    module, args, state = capture_pipeline
    assert module.run_capture(args) == 1
    timing = rows(args.output_dir)
    first, second = [row for row in timing if row["kind"] == "receiver"]
    assert first["accepted"] and second["accepted"]
    assert first["processing_ms"] >= 550
    assert second["queue_delay_ms"] >= 500
    assert second["received_local_perf_counter_ms"] == state.received[1][2]
    assert second["received_wall_time_ms"] == state.received[1][3]
    assert any(
        value.get("sequence") == 2
        and value.get("running") is False
        and value.get("reason") == "observer original capture freshness expired"
        and value["received_wall_time_ms"] == state.received[1][3]
        for value in state.publications
    )


def test_capture_queue_loss_marks_diagnostics_incomplete_when_source_count_still_matches(capture_pipeline):
    module, args, state = capture_pipeline
    state.drop_wire = True
    assert module.run_capture(args) == 1
    result = json.loads((args.output_dir / "receiver-result.json").read_text())
    diagnostic = result["delivery_diagnostics"]
    assert state.dropped and len(state.received) == 1
    assert diagnostic["source_records"] == 0 and diagnostic["receiver_records"] == 0
    assert diagnostic["incomplete"] is True and diagnostic["dropped_records"] == 1
    assert diagnostic["first_error"] == "observer message queue full"
