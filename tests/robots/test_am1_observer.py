from __future__ import annotations

import base64
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]


def load_observer():
    spec = importlib.util.spec_from_file_location("test_am1_observer_module", ROOT / "tools/am1_observer.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


@pytest.fixture
def jpeg():
    stream = io.BytesIO()
    Image.new("RGB", (1280, 720), (20, 40, 60)).save(stream, format="JPEG")
    return base64.b64encode(stream.getvalue()).decode("ascii")


def frame(jpeg, **updates):
    value = {
        "event": "frame",
        "generation": "qualified-test",
        "nonce": "one",
        "sequence": 3,
        "source_system_relative_ticks": 100_000_000,
        "challenge_received_qpc_ticks": 99_990_000,
        "capture_age_ms": 12.0,
        "recording": True,
        "jpeg_base64": jpeg,
    }
    value.update(updates)
    return value


def health(path):
    return json.loads((path / "latest-health.json").read_text(encoding="utf-8"))


def test_current_challenge_delivers_verified_image_and_original_time(tmp_path, jpeg):
    module = load_observer()
    receiver = module.ObserverReceiver(tmp_path, "qualified-test")
    receiver.issue_challenge("one", 100.0)
    assert receiver.accept(frame(jpeg), received_wall_time_ms=123456, received_monotonic_ms=250.0)
    actual = health(tmp_path)
    assert actual["running"] is True and actual["recording"] is True
    assert actual["sequence"] == 3
    assert actual["source_system_relative_ticks"] == 100_000_000
    assert actual["capture_age_ms"] == 12.0
    assert actual["round_trip_ms"] == 150.0
    assert actual["received_wall_time_ms"] == 123456
    with Image.open(actual["frame_path"]) as image:
        assert image.size == (1280, 720)
    assert len(actual["frame_sha256"]) == 64


@pytest.mark.parametrize(
    "updates",
    [
        {"generation": "other"},
        {"nonce": "old"},
        {"capture_age_ms": 1501},
        {"source_system_relative_ticks": 99_000_000},
        {"recording": False},
        {"jpeg_base64": base64.b64encode(b"not an image").decode("ascii")},
    ],
)
def test_uncertain_identity_stale_capture_or_bad_pixels_cannot_qualify(tmp_path, jpeg, updates):
    module = load_observer()
    receiver = module.ObserverReceiver(tmp_path, "qualified-test")
    receiver.issue_challenge("one", 100.0)
    assert not receiver.accept(
        frame(jpeg, **updates), received_wall_time_ms=123456, received_monotonic_ms=250.0
    )
    assert health(tmp_path)["running"] is False
    assert not list(tmp_path.glob("frame-*.jpg"))


def test_late_delivery_is_not_freshened_by_current_capture(tmp_path, jpeg):
    module = load_observer()
    receiver = module.ObserverReceiver(tmp_path, "qualified-test")
    receiver.issue_challenge("one", 100.0)
    assert not receiver.accept(frame(jpeg), received_wall_time_ms=123456, received_monotonic_ms=2101.0)
    assert health(tmp_path)["running"] is False


def test_new_nonce_cannot_reage_a_repeated_source_frame(tmp_path, jpeg):
    module = load_observer()
    receiver = module.ObserverReceiver(tmp_path, "qualified-test")
    receiver.issue_challenge("one", 100.0)
    assert receiver.accept(frame(jpeg), received_wall_time_ms=123456, received_monotonic_ms=250.0)
    receiver.issue_challenge("two", 400.0)
    assert not receiver.accept(
        frame(jpeg, nonce="two"), received_wall_time_ms=123800, received_monotonic_ms=500.0
    )
    actual = health(tmp_path)
    assert actual["running"] is False
    assert actual["received_wall_time_ms"] == 123456
    assert actual["source_system_relative_ticks"] == 100_000_000


def test_silence_and_terminal_clear_authority_preserving_last_evidence(tmp_path, jpeg):
    module = load_observer()
    receiver = module.ObserverReceiver(tmp_path, "qualified-test")
    receiver.issue_challenge("one", 100.0)
    assert receiver.accept(frame(jpeg), received_wall_time_ms=123456, received_monotonic_ms=250.0)
    receiver.expire(1751.0)
    actual = health(tmp_path)
    assert actual["running"] is False
    assert actual["received_wall_time_ms"] == 123456
    receiver.terminal(
        {"event": "complete", "generation": "qualified-test", "success": True, "camera_released": True}
    )
    assert health(tmp_path)["recording"] is False
    assert health(tmp_path)["camera_released"] is True


def test_bounded_recent_images_preserve_selected_event_frame(tmp_path, jpeg):
    module = load_observer()
    receiver = module.ObserverReceiver(tmp_path, "qualified-test", recent_frames=2)
    for sequence in range(1, 5):
        nonce = str(sequence)
        receiver.issue_challenge(nonce, sequence * 100.0)
        assert receiver.accept(
            frame(
                jpeg,
                nonce=nonce,
                sequence=sequence,
                source_system_relative_ticks=100_000_000 + sequence * 1_000_000,
                challenge_received_qpc_ticks=99_990_000 + sequence * 1_000_000,
            ),
            received_wall_time_ms=123456 + sequence,
            received_monotonic_ms=sequence * 100.0 + 20.0,
        )
        if sequence == 1:
            selected = receiver.retain_event("cycle-1")
    assert len(list(tmp_path.glob("frame-*.jpg"))) == 2
    assert Path(selected["frame_path"]).is_file()
    assert selected["sequence"] == 1
    assert json.loads((tmp_path / "events/cycle-1.json").read_text())["sequence"] == 1


def capture_compile_prefix():
    source = (ROOT / "tools/am1_observer_capture.ps1").read_text(encoding="utf-8")
    prefix = source.split("$capture=$null", 1)[0].split("\n", 1)[1]
    return prefix.replace(
        "$config=Get-Content -Raw -LiteralPath $ConfigPath | ConvertFrom-Json",
        '$config=[pscustomobject]@{expected_host=$env:COMPUTERNAME;generation="offline";duration_seconds=20;max_recording_bytes=146800640}',
    )


@pytest.mark.skipif(sys.platform != "win32", reason="installed Windows WinRT projection")
def test_channel_ignores_unauthenticated_stop_and_keeps_only_latest_challenge(tmp_path):
    import queue
    import secrets
    import socket
    import subprocess
    import threading

    token = secrets.token_hex(32)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    body = f"""
$channel=[AM1ObserverChannel]::new({port},'{token}','offline')
try {{
 [Console]::Out.WriteLine('ready');[Console]::Out.Flush()
 Start-Sleep -Milliseconds 400
 $command=$channel.TakeLatest()
 [Console]::Out.WriteLine((@{{event=$command.Event;nonce=$command.Nonce;received_ticks=$command.ReceivedTicks;rejected=$channel.Rejected}}|ConvertTo-Json -Compress));[Console]::Out.Flush()
 $priorSourceTicks=$command.ReceivedTicks
 $proof=$channel.NewestBefore($priorSourceTicks,(Get-QpcTicks))
 $originalReceipt=$proof.ReceivedTicks
 $proof.ReceivedTicks=1
 $clock=[Diagnostics.Stopwatch]::StartNew()
 do {{ $command=$channel.TakeLatest();Start-Sleep -Milliseconds 10 }} while($null -eq $command -and $clock.Elapsed.TotalSeconds -lt 2)
 $proof=$channel.NewestBefore($priorSourceTicks,(Get-QpcTicks))
 if($proof.Nonce -ne '1' -or $proof.ReceivedTicks -ne $originalReceipt) {{ throw 'Original receipt changed or returned proof mutated storage' }}
 [Console]::Out.WriteLine((@{{event=$command.Event;received=$channel.FramesReceived;rejected=$channel.Rejected}}|ConvertTo-Json -Compress));[Console]::Out.Flush()
}} finally {{ $channel.Dispose() }}
Start-Sleep -Milliseconds 200
"""
    script_path = tmp_path / "channel-test.ps1"
    script_path.write_text(capture_compile_prefix() + body, encoding="utf-8")
    process = subprocess.Popen(
        ["powershell.exe", "-STA", "-NoProfile", "-NonInteractive", "-File", str(script_path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    messages = queue.Queue()

    def consume():
        while line := process.stdout.readline():
            messages.put(line)

    threading.Thread(target=consume, daemon=True).start()
    try:
        assert messages.get(timeout=5).strip() == b"ready"
        with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
            values = [
                {"event": "stop", "token": "wrong", "generation": "offline"},
                {"event": "frame", "token": token, "generation": "other", "nonce": "wrong"},
                *[
                    {"event": "frame", "token": token, "generation": "offline", "nonce": str(i)}
                    for i in range(3)
                ],
            ]
            client.sendall(b"".join((json.dumps(value) + "\n").encode() for value in values))
            actual = json.loads(messages.get(timeout=3))
            assert actual["event"] == "frame" and actual["nonce"] == "2"
            assert actual["received_ticks"] > 0 and actual["rejected"] == 2
            client.sendall(
                b"".join(
                    (json.dumps(value) + "\n").encode()
                    for value in (
                        {"event": "frame", "token": token, "generation": "offline", "nonce": "1"},
                        {"event": "stop", "token": token, "generation": "offline"},
                        {"event": "frame", "token": token, "generation": "offline", "nonce": "after-stop"},
                    )
                )
            )
            terminal = json.loads(messages.get(timeout=3))
            assert terminal == {"event": "stop", "received": 3, "rejected": 3}
        assert process.wait(timeout=5) == 0
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


@pytest.mark.skipif(sys.platform != "win32", reason="installed Windows WinRT projection")
def test_channel_preserves_middle_causal_receipt_when_commands_coalesce(tmp_path):
    import queue
    import secrets
    import socket
    import subprocess
    import threading

    token = secrets.token_hex(32)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    body = f"""
$channel=[AM1ObserverChannel]::new({port},'{token}','offline')
$legacyWindow=[AM1ObserverChallengeWindow]::new()
try {{
    [Console]::Out.WriteLine('ready');[Console]::Out.Flush()
    Start-Sleep -Milliseconds 250
    $old=$channel.TakeLatest()
    $legacyWindow.Add($old)
    [Console]::Out.WriteLine('old-taken');[Console]::Out.Flush()
    Start-Sleep -Milliseconds 150
    $sourceTicks=Get-QpcTicks
    [Console]::Out.WriteLine('source-selected');[Console]::Out.Flush()
    Start-Sleep -Milliseconds 150
    $newest=$channel.TakeLatest()
    if($channel.PSObject.Methods.Name -contains 'NewestBefore') {{
        $proof=$channel.NewestBefore($sourceTicks,(Get-QpcTicks))
    }} else {{
        $legacyWindow.Add($newest)
        $proof=$legacyWindow.NewestBefore($sourceTicks,(Get-QpcTicks))
    }}
    [Console]::Out.WriteLine((@{{nonce=$proof.Nonce;received_ticks=$proof.ReceivedTicks;source_ticks=$sourceTicks}}|ConvertTo-Json -Compress));[Console]::Out.Flush()
}} finally {{ $channel.Dispose() }}
"""
    script = tmp_path / "coalesced-receipts.ps1"
    script.write_text(capture_compile_prefix() + body, encoding="utf-8")
    process = subprocess.Popen(
        ["powershell.exe", "-STA", "-NoProfile", "-NonInteractive", "-File", str(script)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    messages = queue.Queue()

    def consume():
        while line := process.stdout.readline():
            messages.put(line)

    threading.Thread(target=consume, daemon=True).start()
    try:
        assert messages.get(timeout=5).strip() == b"ready"
        with socket.create_connection(("127.0.0.1", port), timeout=2) as client:

            def send(nonce):
                client.sendall(
                    (
                        json.dumps(
                            {"event": "frame", "generation": "offline", "token": token, "nonce": nonce}
                        )
                        + "\n"
                    ).encode()
                )

            send("old")
            assert messages.get(timeout=3).strip() == b"old-taken"
            send("middle")
            assert messages.get(timeout=3).strip() == b"source-selected"
            send("newest-after-source")
            actual = json.loads(messages.get(timeout=3))
            assert actual["nonce"] == "middle", "Coalescing discarded the newest causally preceding receipt"
            assert actual["received_ticks"] < actual["source_ticks"]
        assert process.wait(timeout=5) == 0, process.stderr.read().decode()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows sharing violation")
def test_windows_reader_sharing_does_not_kill_fresh_health_publication(tmp_path, jpeg):
    import ctypes
    import threading

    module = load_observer()
    receiver = module.ObserverReceiver(tmp_path, "qualified-test")
    receiver.issue_challenge("one", 100.0)
    assert receiver.accept(frame(jpeg), received_wall_time_ms=123456, received_monotonic_ms=250.0)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateFileW(str(tmp_path / "latest-health.json"), 0x80000000, 3, None, 3, 0, None)
    assert handle not in (None, ctypes.c_void_p(-1).value)
    releaser = threading.Timer(0.05, lambda: kernel.CloseHandle(handle))
    releaser.start()
    try:
        receiver.issue_challenge("two", 400.0)
        assert receiver.accept(
            frame(
                jpeg,
                nonce="two",
                sequence=4,
                source_system_relative_ticks=101_000_000,
                challenge_received_qpc_ticks=100_990_000,
            ),
            received_wall_time_ms=123800,
            received_monotonic_ms=500.0,
        )
        actual = health(tmp_path)
        assert actual["running"] is True
        assert actual["received_wall_time_ms"] == 123800
        assert actual["capture_age_ms"] == 12.0
    finally:
        releaser.join()


def test_delivery_socket_is_released_before_waiting_for_ssh_exit():
    import socket
    import threading

    module = load_observer()
    delivered, peer = socket.socketpair()
    closed = threading.Event()

    def receive_eof():
        try:
            assert peer.recv(1) == b""
            closed.set()
        finally:
            peer.close()

    thread = threading.Thread(target=receive_eof, daemon=True)
    thread.start()

    class WaitingSSH:
        def wait(self, timeout):
            assert closed.wait(1), "forwarded client remains open while SSH exit is awaited"
            return 0

    try:
        assert module.close_delivery_and_wait(WaitingSSH(), delivered, timeout=2) == 0
        thread.join(timeout=2)
        assert closed.is_set()
    finally:
        delivered.close()
        peer.close()


def test_delivery_extent_uses_original_local_receipts(jpeg, tmp_path):
    receiver = load_observer().ObserverReceiver(tmp_path, "qualified-test")
    receiver.issue_challenge("one", 100.0)
    assert receiver.accept(frame(jpeg), received_wall_time_ms=1000, received_monotonic_ms=120.0)
    receiver.issue_challenge("two", 1100.0)
    assert receiver.accept(
        frame(jpeg, nonce="two", sequence=4, source_system_relative_ticks=100_001_000),
        received_wall_time_ms=2000,
        received_monotonic_ms=1120.0,
    )
    assert receiver.delivered_span_ms == 1000.0
    assert receiver.max_delivery_gap_ms == 1000.0
    receiver.issue_challenge("three", 1300.0)
    assert not receiver.accept(
        frame(jpeg, nonce="three", sequence=4), received_wall_time_ms=2200, received_monotonic_ms=1320.0
    )
    assert receiver.delivered_span_ms == 1000.0


@pytest.mark.skipif(sys.platform != "win32", reason="installed Windows WinRT projection")
def test_jpeg_encoder_applies_bounded_quality_without_opening_camera(tmp_path):
    import subprocess

    body = """
$stream=[Windows.Storage.Streams.InMemoryRandomAccessStream,Windows.Storage,ContentType=WindowsRuntime]::new()
$encoderType=[Windows.Graphics.Imaging.BitmapEncoder,Windows.Graphics,ContentType=WindowsRuntime]
$encoder=Wait-CaptureOperation ([AM1ObserverCollector]::CreateJpegEncoder($stream)) $encoderType 'Offline JPEG encoder' 3000
$bitmap=[Windows.Graphics.Imaging.SoftwareBitmap,Windows.Graphics,ContentType=WindowsRuntime]::new([Windows.Graphics.Imaging.BitmapPixelFormat,Windows.Graphics,ContentType=WindowsRuntime]::Bgra8,1280,720,[Windows.Graphics.Imaging.BitmapAlphaMode,Windows.Graphics,ContentType=WindowsRuntime]::Premultiplied)
try {
    $encoder.SetSoftwareBitmap($bitmap)
    [AM1ObserverCollector]::ConfigureDeliveryJpeg($encoder)
    Wait-CaptureAction ($encoder.FlushAsync()) 'Offline JPEG encode' 3000
    $stream.Seek(0)
    $reader=[Windows.Storage.Streams.DataReader,Windows.Storage,ContentType=WindowsRuntime]::new($stream.GetInputStreamAt(0))
    try {
        $null=Wait-CaptureOperation ($reader.LoadAsync([uint32]$stream.Size)) ([uint32]) 'Read generated JPEG' 3000
        $bytes=New-Object byte[] ([int]$stream.Size)
        $reader.ReadBytes($bytes)
        [pscustomobject]@{jpeg_bytes=$stream.Size;jpeg_base64=[Convert]::ToBase64String($bytes)}|ConvertTo-Json -Compress
    } finally { $reader.Dispose() }
} finally { $bitmap.Dispose();$stream.Dispose() }
"""
    script = tmp_path / "offline-jpeg.ps1"
    script.write_text(capture_compile_prefix() + body, encoding="utf-8")
    result = subprocess.run(
        ["powershell.exe", "-STA", "-NoProfile", "-NonInteractive", "-File", str(script)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert 1000 < value["jpeg_bytes"] < 512 * 1024
    with Image.open(io.BytesIO(base64.b64decode(value["jpeg_base64"]))) as image:
        assert image.size == (640, 360)
        image.load()


def test_continuous_verdict_requires_elapsed_receipts_and_native_freshness(jpeg, tmp_path):
    receiver = load_observer().ObserverReceiver(tmp_path, "qualified-test")
    for sequence in range(1, 82):
        now = sequence * 250.0
        nonce = str(sequence)
        receiver.issue_challenge(nonce, now)
        assert receiver.accept(
            frame(
                jpeg,
                nonce=nonce,
                sequence=sequence,
                source_system_relative_ticks=100_000_000 + sequence * 1_000_000,
                challenge_received_qpc_ticks=99_990_000 + sequence * 1_000_000,
            ),
            received_wall_time_ms=int(now),
            received_monotonic_ms=now + 20.0,
        )
        if sequence == 21:
            assert receiver.continuous_delivery_qualified is False
    assert receiver.continuous_delivery_qualified is True
    assert receiver.delivered_span_ms == 20000.0
    assert receiver.max_effective_capture_age_ms == 270.0
    receiver.issue_challenge("delayed", 21000.0)
    assert not receiver.accept(
        frame(jpeg, nonce="delayed", sequence=82, source_system_relative_ticks=200_000_000),
        received_wall_time_ms=22000,
        received_monotonic_ms=22000.0,
    )
    assert receiver.continuous_delivery_qualified is False
    assert receiver.round_trip_deadline_violations == 1
    assert receiver.freshness_gap_violations == 0


def test_same_original_nonce_can_qualify_advancing_source_frames(jpeg, tmp_path):
    receiver = load_observer().ObserverReceiver(tmp_path, "qualified-test")
    receiver.issue_challenge("one", 100.0)
    assert receiver.accept(frame(jpeg), received_wall_time_ms=1000, received_monotonic_ms=200.0)
    receiver.issue_challenge("newer", 250.0)
    assert receiver.accept(
        frame(jpeg, sequence=4, source_system_relative_ticks=101_000_000),
        received_wall_time_ms=1100,
        received_monotonic_ms=300.0,
    )
    assert health(tmp_path)["round_trip_ms"] == 200.0
    assert health(tmp_path)["nonce"] == "one"
    assert not receiver.accept(
        frame(jpeg, nonce="newer", sequence=4, source_system_relative_ticks=101_000_000),
        received_wall_time_ms=1200,
        received_monotonic_ms=350.0,
    )


def test_original_nonce_proof_cannot_be_renewed_or_extended(jpeg, tmp_path):
    receiver = load_observer().ObserverReceiver(tmp_path, "qualified-test")
    receiver.issue_challenge("one", 100.0)
    with pytest.raises(ValueError, match="already issued"):
        receiver.issue_challenge("one", 700.0)
    receiver.issue_challenge("newer", 700.0)
    assert not receiver.accept(frame(jpeg), received_wall_time_ms=1000, received_monotonic_ms=851.0)
    assert health(tmp_path)["running"] is False


def test_only_six_original_proofs_are_retained(jpeg, tmp_path):
    receiver = load_observer().ObserverReceiver(tmp_path, "qualified-test")
    for index, nonce in enumerate(("one", "two", "three", "four", "five", "six", "seven")):
        receiver.issue_challenge(nonce, float(index * 10))
    assert len(receiver.challenges) == 6
    assert not receiver.accept(frame(jpeg), received_wall_time_ms=1000, received_monotonic_ms=100.0)


@pytest.mark.parametrize("value", [None, [], "foreign"])
def test_non_object_frame_protocol_is_rejected_without_crashing(value, tmp_path):
    receiver = load_observer().ObserverReceiver(tmp_path, "qualified-test")
    receiver.issue_challenge("one", 100.0)
    assert not receiver.accept(value, received_wall_time_ms=1000, received_monotonic_ms=200.0)
    assert health(tmp_path)["running"] is False


@pytest.mark.skipif(sys.platform != "win32", reason="installed Windows WinRT projection")
def test_source_selects_newest_causal_original_nonce_without_extending_its_lifetime(tmp_path):
    import subprocess

    body = """
$window=[AM1ObserverChallengeWindow]::new()
foreach($ticks in 100,200,300,400) {
    $command=[AM1ObserverCommand]::new()
    $command.Event='frame';$command.Nonce=$ticks.ToString();$command.ReceivedTicks=$ticks
    $window.Add($command)
    $command.ReceivedTicks=1
}
$copy=$window.NewestBefore(350,500)
$copy.ReceivedTicks=1
if($window.NewestBefore(350,500).ReceivedTicks -ne 300) { throw 'Stored receipt was mutated' }
if($null -ne $window.NewestBefore(150,500)) { throw 'Discarded oldest nonce was retained' }
if($window.NewestBefore(350,500).Nonce -ne '300') { throw 'Newest causal nonce was not selected' }
if($window.NewestBefore(400,500).Nonce -ne '300') { throw 'Frame at receipt time was accepted as later' }
if($null -ne $window.NewestBefore(500,7500500)) { throw 'Original nonce lifetime was extended' }
[Console]::Out.WriteLine('causal window passed')
"""
    script = tmp_path / "causal-window.ps1"
    script.write_text(capture_compile_prefix() + body, encoding="utf-8")
    result = subprocess.run(
        ["powershell.exe", "-STA", "-NoProfile", "-NonInteractive", "-File", str(script)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "causal window passed" in result.stdout


def test_remote_receipt_of_original_nonce_cannot_change_on_later_frame(jpeg, tmp_path):
    receiver = load_observer().ObserverReceiver(tmp_path, "qualified-test")
    receiver.issue_challenge("one", 100.0)
    assert receiver.accept(frame(jpeg), received_wall_time_ms=1000, received_monotonic_ms=200.0)
    assert not receiver.accept(
        frame(
            jpeg,
            sequence=4,
            source_system_relative_ticks=101_000_000,
            challenge_received_qpc_ticks=99_001_000,
        ),
        received_wall_time_ms=1100,
        received_monotonic_ms=300.0,
    )
    assert "receipt changed" in health(tmp_path)["reason"]
    assert health(tmp_path)["received_wall_time_ms"] == 1000


def test_causal_proof_survives_nonce_cadence_until_original_deadline(jpeg, tmp_path):
    receiver = load_observer().ObserverReceiver(tmp_path, "qualified-test")
    for index, nonce in enumerate(("one", "two", "three", "four", "five")):
        receiver.issue_challenge(nonce, 100.0 + index * 125)
    assert receiver.accept(frame(jpeg), received_wall_time_ms=1000, received_monotonic_ms=700.0)
    assert health(tmp_path)["round_trip_ms"] == 600.0
    assert not receiver.accept(
        frame(jpeg, sequence=4, source_system_relative_ticks=101_000_000),
        received_wall_time_ms=1100,
        received_monotonic_ms=851.0,
    )
    assert health(tmp_path)["received_wall_time_ms"] == 1000


def test_retrieves_only_final_released_recording_and_verifies_content(tmp_path, monkeypatch):
    from types import SimpleNamespace

    module = load_observer()
    config = tmp_path / "private.json"
    config.write_text(json.dumps({"remote_output_root": r"C:\private\observer"}))
    output = tmp_path / "capture"
    output.mkdir()
    pixels = b"finite recorded payload" * 100
    import hashlib

    final = {
        "generation": "qualified-test",
        "success": True,
        "camera_released": True,
        "clip_path": r"C:\private\observer\qualified-test\continuous.mp4",
        "clip_bytes": len(pixels),
        "clip_sha256": hashlib.sha256(pixels).hexdigest(),
    }
    (output / "capture-result.json").write_text(json.dumps(final))
    calls = []

    def transfer(command, **kwargs):
        calls.append(command)
        Path(command[-1]).write_bytes(pixels)
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(module.subprocess, "run", transfer)
    args = SimpleNamespace(
        config=config, output_dir=output, generation="qualified-test", ssh_host="existing-alias"
    )
    assert module.retrieve_recording(args) == 0
    assert len(calls) == 1
    value = json.loads((output / "recording-retrieval.json").read_text())
    assert value["verified"] is True
    assert value["clip_sha256"] == final["clip_sha256"]
    assert (output / "continuous.mp4").read_bytes() == pixels


def test_recording_retrieval_cannot_fetch_unreleased_or_unbounded_artifact(tmp_path, monkeypatch):
    from types import SimpleNamespace

    module = load_observer()
    config = tmp_path / "private.json"
    config.write_text(json.dumps({"remote_output_root": r"C:\private\observer"}))
    output = tmp_path / "capture"
    output.mkdir()
    args = SimpleNamespace(
        config=config, output_dir=output, generation="qualified-test", ssh_host="existing-alias"
    )
    monkeypatch.setattr(
        module.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("Unqualified transfer")
    )
    base = {
        "generation": "qualified-test",
        "success": True,
        "camera_released": True,
        "clip_path": r"C:\private\observer\qualified-test\continuous.mp4",
        "clip_bytes": 2048,
        "clip_sha256": "a" * 64,
    }
    for update in (
        {"camera_released": False},
        {"clip_bytes": module.MAX_RECORDING_BYTES + 1},
        {"clip_path": r"C:\other\continuous.mp4"},
    ):
        (output / "capture-result.json").write_text(json.dumps(dict(base, **update)))
        with pytest.raises(ValueError):
            module.retrieve_recording(args)


@pytest.fixture
def event_export_fixture(tmp_path):
    """A stopped, released and locally hash-verified recording plus one retained event."""
    import hashlib
    from types import SimpleNamespace

    module = load_observer()
    output = tmp_path / "capture"
    output.mkdir()
    payload = b"known finalized recording" * 100
    (output / "continuous.mp4").write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    config = tmp_path / "private.json"
    config.write_text(json.dumps({"remote_output_root": r"C:\private\observer"}))
    final = {
        "generation": "qualified-test",
        "success": True,
        "camera_released": True,
        "clip_path": r"C:\private\observer\qualified-test\continuous.mp4",
        "clip_bytes": len(payload),
        "clip_sha256": digest,
        "recording_requested_qpc_ticks": 99_000_000,
        "recording_started_qpc_ticks": 100_000_000,
        "recording_requested_utc": "2026-10-07T00:00:00Z",
        "recording_started_utc": "2026-10-07T00:00:00.100Z",
        "recording_stop_requested_utc": "2026-10-07T00:00:20.100Z",
        "recording_stopped_utc": "2026-10-07T00:00:20.200Z",
        "record_hold_seconds": 20.0,
    }
    (output / "capture-result.json").write_text(json.dumps(final))
    retrieval = {
        "generation": "qualified-test",
        "verified": True,
        "clip_path": str(output / "continuous.mp4"),
        "clip_bytes": len(payload),
        "clip_sha256": digest,
    }
    (output / "recording-retrieval.json").write_text(json.dumps(retrieval))
    events = output / "events"
    events.mkdir()
    event = {
        "event": "ready",
        "generation": "qualified-test",
        "source_system_relative_ticks": 164_000_000,
        "sequence": 64,
        "running": True,
        "recording": True,
        "challenge_qualified": True,
    }
    (events / "ready.json").write_text(json.dumps(event))
    ffmpeg, ffprobe = tmp_path / "ffmpeg.exe", tmp_path / "ffprobe.exe"
    ffmpeg.touch()
    ffprobe.touch()
    args = SimpleNamespace(
        output_dir=output,
        config=config,
        generation="qualified-test",
        capture_result=None,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        event_export_dir=output / "event-export",
        event_clip_seconds=6.0,
    )
    return module, args, final, retrieval, event


@pytest.mark.parametrize(
    "guard",
    [
        "unreleased",
        "missing_stop",
        "wrong_hash",
        "unverified",
        "oversize",
        "remote_source_escape",
        "local_source_escape",
        "wrong_generation",
        "invalid_source_ticks",
        "source_before_start",
        "source_after_stop",
        "invalid_window",
        "export_destination_exists",
        "too_many_events",
    ],
)
def test_event_export_guards_precede_every_decoder(guard, event_export_fixture, monkeypatch):
    module, args, final, retrieval, event = event_export_fixture
    output = args.output_dir
    if guard == "unreleased":
        final["camera_released"] = False
    elif guard == "missing_stop":
        del final["recording_stopped_utc"]
    elif guard == "wrong_hash":
        final["clip_sha256"] = retrieval["clip_sha256"] = "0" * 64
    elif guard == "unverified":
        retrieval["verified"] = False
    elif guard == "oversize":
        with (output / "continuous.mp4").open("r+b") as stream:
            stream.truncate(module.MAX_RECORDING_BYTES + 1)
        final["clip_bytes"] = retrieval["clip_bytes"] = module.MAX_RECORDING_BYTES + 1
    elif guard == "remote_source_escape":
        final["clip_path"] = r"C:\other\continuous.mp4"
    elif guard == "local_source_escape":
        retrieval["clip_path"] = str(output.parent / "other.mp4")
    elif guard == "wrong_generation":
        event["generation"] = "other"
    elif guard == "invalid_source_ticks":
        event["source_system_relative_ticks"] = True
    elif guard == "source_before_start":
        event["source_system_relative_ticks"] = 98_000_000
    elif guard == "source_after_stop":
        event["source_system_relative_ticks"] = 301_000_000
    elif guard == "invalid_window":
        args.event_clip_seconds = 12.01
    elif guard == "export_destination_exists":
        args.event_export_dir.mkdir()
    elif guard == "too_many_events":
        for index in range(128):
            label = f"extra-{index}"
            (output / "events" / f"{label}.json").write_text(json.dumps(dict(event, event=label)))
    (output / "capture-result.json").write_text(json.dumps(final))
    (output / "recording-retrieval.json").write_text(json.dumps(retrieval))
    (output / "events" / "ready.json").write_text(json.dumps(event))
    monkeypatch.setattr(module.subprocess, "run", lambda *_a, **_kw: pytest.fail("Decoder ran before guard"))
    with pytest.raises(ValueError):
        module.export_events(args)
    assert not (args.event_export_dir / "ready.mp4").exists()


def test_event_export_preserves_qpc_interval_and_decoded_still_pts(event_export_fixture, monkeypatch):
    from types import SimpleNamespace

    module, args, _, _, _ = event_export_fixture
    calls = []

    def decode(command, **kwargs):
        calls.append(command)
        if "-preset" in command or "libx264" in command:
            return SimpleNamespace(returncode=1, stdout=b"", stderr=b"Unrecognized option 'preset'")
        if command[0] == str(args.ffprobe):
            value = {
                "streams": [
                    {
                        "codec_type": "video",
                        "width": 1280,
                        "height": 720,
                        "start_time": "0.0",
                        "duration": "20.0",
                    }
                ],
                "format": {"start_time": "0.0", "duration": "20.0"},
            }
            return SimpleNamespace(returncode=0, stdout=json.dumps(value).encode(), stderr=b"")
        target = Path(command[-1])
        if target.suffix == ".jpg":
            Image.new("RGB", (1280, 720), (20, 40, 60)).save(target, format="JPEG")
            stderr = b"[Parsed_showinfo_1 @ source] n: 0 pts: 6500 pts_time:6.5 s:1280x720\n"
        else:
            target.write_bytes(b"short event clip" * 100)
            stderr = b""
        return SimpleNamespace(returncode=0, stdout=b"", stderr=stderr)

    monkeypatch.setattr(module.subprocess, "run", decode)
    assert module.export_events(args) == 0
    value = json.loads((args.event_export_dir / "manifest.json").read_text())
    assert value["complete"] is True
    assert value["export_bytes"] == sum(path.stat().st_size for path in args.event_export_dir.iterdir())
    assert (
        value["source_sha256"]
        == json.loads((args.output_dir / "capture-result.json").read_text())["clip_sha256"]
    )
    retained = value["events"][0]
    assert retained["label"] == "ready"
    assert retained["source_offset_interval_seconds"] == [6.4, 6.5]
    assert retained["selected_frame_pts_seconds"] == 6.5
    assert retained["exact_source_frame_match"] is False
    assert retained["clip_start_seconds"] == 3.45
    assert retained["clip_duration_seconds"] == 6.0
    with Image.open(retained["still_path"]) as still:
        assert still.size == (1280, 720)
    assert len(calls) == 3
    assert not (args.output_dir / "latest-health.json").exists()


def test_event_export_output_budget_stops_following_decoders(event_export_fixture, monkeypatch):
    from types import SimpleNamespace

    module, args, _, _, _ = event_export_fixture
    invoked = []

    def decode(command, **kwargs):
        invoked.append(command)
        if command[0] == str(args.ffprobe):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "streams": [{"codec_type": "video", "width": 1280, "height": 720}],
                        "format": {"start_time": "0.0", "duration": "20.0"},
                    }
                ).encode(),
                stderr=b"",
            )
        with Path(command[-1]).open("wb") as stream:
            stream.truncate(96 * 1024 * 1024 + 1)
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(module.subprocess, "run", decode)
    with pytest.raises(ValueError, match="budget"):
        module.export_events(args)
    assert len(invoked) == 2
    assert sum(p.stat().st_size for p in args.event_export_dir.iterdir() if p.is_file()) <= 96 * 1024 * 1024


def test_event_export_cli_dispatches_without_starting_capture(monkeypatch):
    module = load_observer()
    monkeypatch.setattr(module, "run_capture", lambda *_: pytest.fail("Capture started for offline export"))
    monkeypatch.setattr(module, "export_events", lambda args: 7)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "observer",
            "--ssh-host",
            "existing-alias",
            "--config",
            "private.json",
            "--output-dir",
            "capture",
            "--generation",
            "qualified-test",
            "--export-events",
            "--ffmpeg",
            "ffmpeg.exe",
            "--ffprobe",
            "ffprobe.exe",
        ],
    )
    assert module.main() == 7


def test_event_export_accepts_bounded_full_capture_phase_evidence(event_export_fixture):
    module, args, final, _, _ = event_export_fixture
    final["phase_timings"] = [{"retained_phase_evidence": "x" * 700} for _ in range(128)]
    (args.output_dir / "capture-result.json").write_text(json.dumps(final))
    source, _, _, planned = module._event_export_plan(args)
    assert source == args.output_dir / "continuous.mp4"
    assert planned[0]["source_offset_interval_seconds"] == [6.4, 6.5]


def test_event_export_rejects_oversize_full_capture_metadata_before_decoder(
    event_export_fixture, monkeypatch
):
    module, args, final, _, _ = event_export_fixture
    final["unexpected_unbounded_evidence"] = "x" * (256 * 1024)
    (args.output_dir / "capture-result.json").write_text(json.dumps(final))
    monkeypatch.setattr(module.subprocess, "run", lambda *_a, **_kw: pytest.fail("Decoder ran"))
    with pytest.raises(ValueError):
        module.export_events(args)


def test_event_export_keeps_nonzero_pts_origin_ambiguity_at_recording_end(event_export_fixture, monkeypatch):
    from types import SimpleNamespace

    module, args, final, _, event = event_export_fixture
    event["source_system_relative_ticks"] = 297_000_000
    (args.output_dir / "events" / "ready.json").write_text(json.dumps(event))

    def decode(command, **kwargs):
        if command[0] == str(args.ffprobe):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "streams": [{"codec_type": "video", "width": 1280, "height": 720}],
                        "format": {"start_time": "0.1", "duration": "19.75"},
                    }
                ).encode(),
                stderr=b"",
            )
        target = Path(command[-1])
        if target.suffix == ".jpg":
            Image.new("RGB", (1280, 720)).save(target, format="JPEG")
            return SimpleNamespace(
                returncode=0, stdout=b"", stderr=b"[Parsed_showinfo_1] n: 0 pts_time:19.8 s:1280x720\n"
            )
        target.write_bytes(b"bounded clip" * 100)
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(module.subprocess, "run", decode)
    assert module.export_events(args) == 0
    value = json.loads((args.event_export_dir / "manifest.json").read_text())
    event = value["events"][0]
    assert event["source_offset_interval_seconds"] == [19.7, 19.8]
    assert event["container_start_shifted_interval_seconds"] == pytest.approx([19.8, 19.9])
    assert event["clip_start_seconds"] == 13.75
    assert event["clip_duration_seconds"] == 6.0
    assert event["selected_frame_pts_seconds"] == 19.8
    assert event["exact_source_frame_match"] is False


def test_staging_timeout_records_failure_without_launching_capture(tmp_path, monkeypatch):
    import subprocess
    from types import SimpleNamespace

    module = load_observer()
    config = tmp_path / "private.json"
    config.write_text(
        json.dumps(
            {
                "expected_host": "private-host",
                "camera_name": "private-camera",
                "video_device_id": "private-device",
                "remote_output_root": r"C:\private\observer",
            }
        )
    )
    output = tmp_path / "capture"
    retained_stderr = b"bounded private SSH staging timeout\n" * 3000

    def stage_timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("private-stage", 30, stderr=retained_stderr)

    monkeypatch.setattr(module.subprocess, "run", stage_timeout)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *_a, **_kw: pytest.fail("Capture launched"))
    args = SimpleNamespace(
        config=config,
        output_dir=output,
        generation="qualified-test",
        duration_seconds=20,
        ssh_host="existing-alias",
    )
    assert module.run_capture(args) == 1
    failed = json.loads((output / "staging-failure.json").read_text())
    assert failed["capture_launched"] is False
    assert failed["generation"] == "qualified-test"
    assert (output / "stage.stderr").read_bytes() == retained_stderr[-65536:]
    latest = health(output)
    assert latest["running"] is False and latest["recording"] is False
    assert latest["challenge_qualified"] is False


def test_qpc_bound_removes_only_original_source_wait(jpeg, tmp_path):
    receiver = load_observer().ObserverReceiver(tmp_path, "qualified-test")
    receiver.issue_challenge("one", 100.0)
    assert receiver.accept(
        frame(jpeg, challenge_received_qpc_ticks=98_000_000, capture_age_ms=120.0),
        received_wall_time_ms=1000,
        received_monotonic_ms=500.0,
    )
    actual = health(tmp_path)
    assert actual["timing_basis"] == "qpc_elapsed_v1"
    assert 0 < actual["local_clock_resolution_ms"] <= 0.01
    assert actual["source_elapsed_since_challenge_ms"] == 200.0
    assert actual["source_age_upper_bound_ms"] == 201.0
    assert actual["round_trip_ms"] == 400.0


@pytest.mark.parametrize("elapsed_ms,raw_age", [(151, 0), (100, 60), (100, 150)])
def test_qpc_impossible_elapsed_age_pair_never_qualifies(elapsed_ms, raw_age, jpeg, tmp_path):
    receiver = load_observer().ObserverReceiver(tmp_path, "qualified-test")
    receiver.issue_challenge("one", 100.0)
    assert not receiver.accept(
        frame(
            jpeg, challenge_received_qpc_ticks=100_000_000 - int(elapsed_ms * 10000), capture_age_ms=raw_age
        ),
        received_wall_time_ms=1000,
        received_monotonic_ms=250.0,
    )
    assert not health(tmp_path)["running"]
    assert not list(tmp_path.glob("frame-*.jpg"))


def test_current_contiguous_window_recovers_without_erasing_whole_run_gap(jpeg, tmp_path):
    receiver = load_observer().ObserverReceiver(tmp_path, "qualified-test")
    for sequence in range(1, 87):
        now = sequence * 250.0 + (1000.0 if sequence > 2 else 0.0)
        nonce = str(sequence)
        source = 100_000_000 + sequence * 1_000_000
        receiver.issue_challenge(nonce, now)
        assert receiver.accept(
            frame(
                jpeg,
                nonce=nonce,
                sequence=sequence,
                source_system_relative_ticks=source,
                challenge_received_qpc_ticks=source - 10000,
            ),
            received_wall_time_ms=int(now),
            received_monotonic_ms=now + 20.0,
        )
    assert receiver.freshness_gap_violations > 0
    assert not receiver.continuous_delivery_qualified
    assert receiver.current_contiguous_delivery_qualified
    assert health(tmp_path)["current_contiguous_delivery_qualified"]
    original_receipt = health(tmp_path)["received_wall_time_ms"]
    receiver.expire(now + 20.0 + 481.0)
    assert not receiver.current_contiguous_delivery_qualified
    assert not health(tmp_path)["running"]
    assert health(tmp_path)["received_wall_time_ms"] == original_receipt


@pytest.mark.parametrize(
    "updates", [{"resolution": 0.0}, {"resolution": 0.015625}, {"monotonic": False}, {"adjustable": True}]
)
def test_unsupported_local_clock_cannot_claim_qpc_timing(updates, tmp_path, monkeypatch):
    from types import SimpleNamespace

    module = load_observer()
    info = {
        "resolution": 1e-7,
        "monotonic": True,
        "adjustable": False,
        "implementation": "QueryPerformanceCounter()",
    }
    info.update(updates)
    monkeypatch.setattr(module.time, "get_clock_info", lambda _: SimpleNamespace(**info))
    with pytest.raises(ValueError, match="precise monotonic"):
        module.ObserverReceiver(tmp_path, "qualified-test")


def test_qpc_precision_margin_accepts_boundary_without_expanding_capture_ceiling(jpeg, tmp_path):
    receiver = load_observer().ObserverReceiver(tmp_path, "qualified-test")
    receiver.issue_challenge("one", 100.0)
    assert receiver.accept(
        frame(jpeg, challenge_received_qpc_ticks=99_000_000, capture_age_ms=51.0),
        received_wall_time_ms=1000,
        received_monotonic_ms=250.0,
    )
    assert health(tmp_path)["source_age_upper_bound_ms"] == 51.0
    receiver.issue_challenge("two", 300.0)
    assert not receiver.accept(
        frame(
            jpeg,
            nonce="two",
            sequence=4,
            source_system_relative_ticks=101_000_000,
            challenge_received_qpc_ticks=100_000_000,
            capture_age_ms=51.001,
        ),
        received_wall_time_ms=2000,
        received_monotonic_ms=450.0,
    )
    assert not receiver.current_contiguous_delivery_qualified


@pytest.fixture
def reset_transport(tmp_path, monkeypatch, jpeg):
    """Real loopback command/frame IO; SSH process creation is the external boundary."""
    import os
    import socket
    import subprocess
    import threading
    import time
    from types import SimpleNamespace

    module = load_observer()
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
        generation="qualified-test",
        duration_seconds=20,
        ssh_host="existing-alias",
        delivery_compression=False,
    )
    state = SimpleNamespace(
        module=module,
        args=args,
        commands=[],
        stage_count=0,
        source=None,
        source_sequence=0,
        resets=1,
        processes=[],
        received=[],
        first_frame=None,
        final=None,
        replay=False,
        forwarded_health=[],
        metadata_reads=0,
        errors=[],
        clock_offset=0,
        qualified_run=False,
        failed_forwards=False,
        exhausted_metadata_reads=0,
        owner_alive_stalled=False,
        owner_alive_at_reattach=[],
    )
    real_perf_counter = time.perf_counter
    monkeypatch.setattr(module.time, "perf_counter", lambda: real_perf_counter() + state.clock_offset)

    class Forward:
        def __init__(self, command, stderr):
            self.command, self.stderr = command, stderr
            self.returncode = None
            self.clients = []
            read_fd, write_fd = os.pipe()
            self.stdout = os.fdopen(read_fd, "rb")
            self.writer = os.fdopen(write_fd, "wb", buffering=0)
            spec = command[command.index("-L") + 1].split(":")
            self.listener = socket.socket()
            self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.listener.bind(("127.0.0.1", int(spec[1])))
            self.listener.listen()
            self.is_replacement = "-N" in command
            if not self.is_replacement:
                self.writer.write(
                    (
                        json.dumps({"event": "started", "generation": args.generation, "recording": True})
                        + "\n"
                    ).encode()
                )
            else:
                state.forwarded_health.append(health(args.output_dir))
                state.owner_alive_at_reattach.append(state.processes[0].poll() is None)
            if self.is_replacement and state.failed_forwards:
                self.reset()
            else:
                threading.Thread(target=self.accept, daemon=True).start()

        def accept(self):
            try:
                client, _ = self.listener.accept()
                self.clients.append(client)
                count = 0
                with client.makefile("rb") as reader:
                    for line in reader:
                        value = json.loads(line)
                        assert value["generation"] == state.source["generation"]
                        assert value["token"] == state.source["token"]
                        state.received.append(value)
                        if value["event"] == "stop":
                            state.final = {
                                "event": "complete",
                                "generation": args.generation,
                                "success": True,
                                "camera_released": True,
                                "clip_path": r"C:\private\observer\qualified-test\continuous.mp4",
                                "stop_reason": "normal_stop",
                                "recording_started_utc": "original-start",
                                "recording_stopped_utc": "original-stop",
                            }
                            if state.owner_alive_stalled:
                                owner = state.processes[0]
                                owner.writer.write(
                                    (
                                        json.dumps({"event": "ending", "generation": args.generation}) + "\n"
                                    ).encode()
                                )
                                owner.writer.write((json.dumps(state.final) + "\n").encode())
                                owner.returncode = 0
                                owner.writer.close()
                            return
                        count += 1
                        if self.is_replacement and state.replay and count == 1:
                            payload = state.first_frame
                        else:
                            if state.qualified_run:
                                state.clock_offset += 0.15
                            received_ticks = int(time.perf_counter() * 10_000_000)
                            time.sleep(0.001)
                            state.source_sequence += 1
                            payload = frame(
                                jpeg,
                                nonce=value["nonce"],
                                sequence=state.source_sequence,
                                challenge_received_qpc_ticks=received_ticks,
                                source_system_relative_ticks=int(time.perf_counter() * 10_000_000),
                                capture_age_ms=0,
                            )
                            state.first_frame = state.first_frame or dict(payload)
                        client.sendall((json.dumps(payload) + "\n").encode())
                        if state.qualified_run and count >= 140:
                            deadline = real_perf_counter() + 1
                            while real_perf_counter() < deadline:
                                try:
                                    if health(args.output_dir).get("sequence") == state.source_sequence:
                                        break
                                except OSError:
                                    pass  # Windows atomic health replacement can briefly refuse a fixture read.
                                time.sleep(0.001)
                            state.final = {
                                "event": "complete",
                                "generation": args.generation,
                                "success": True,
                                "camera_released": True,
                                "stop_reason": "finite_duration",
                            }
                            self.writer.write(
                                (
                                    json.dumps({"event": "ending", "generation": args.generation}) + "\n"
                                ).encode()
                            )
                            self.writer.write((json.dumps(state.final) + "\n").encode())
                            self.returncode = 0
                            self.writer.close()
                            return
                        if count == 3 and len(state.processes) <= state.resets:
                            if state.owner_alive_stalled and not self.is_replacement:
                                return  # Drop only forwarded frames; the capture-owning SSH/metadata remains live.
                            self.reset()
                            return
                        if self.is_replacement and count == 3 and state.resets == 1:
                            (args.output_dir / "stop.request").write_text("normal owned stop")
            except (OSError, ValueError) as error:
                if self.returncode is None and state.final is None:
                    state.errors.append(error)
            finally:
                for client in [] if state.qualified_run and state.final else self.clients:
                    with module.suppress(OSError):
                        client.shutdown(socket.SHUT_RDWR)
                    client.close()

        def reset(self):
            self.returncode = 255
            self.stderr.write(b"client_loop: send disconnect: Connection reset\n")
            self.listener.close()
            self.writer.close()
            for client in self.clients:
                with module.suppress(OSError):
                    client.shutdown(socket.SHUT_RDWR)
                client.close()

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            if self.returncode is None:
                raise subprocess.TimeoutExpired("forward-only", timeout)
            return self.returncode

        def terminate(self):
            assert self.is_replacement, (
                "the source-owning SSH process must never be terminated to recover forwarding"
            )
            self.returncode = 1  # Actual Popen.terminate exit on Windows.
            self.listener.close()
            self.writer.close()

        def kill(self):
            pytest.fail("observer transport test attempted a forced process kill")

    def run(command, **kwargs):
        if "input" in kwargs:
            state.stage_count += 1
            state.source = json.loads(base64.b64decode(kwargs["input"].splitlines()[1]))
            return subprocess.CompletedProcess(command, 0, b'{"capture_port":54321}', b"")
        state.metadata_reads += 1
        if state.final is None and len(state.processes) >= 4 and state.processes[-1].poll() is not None:
            state.exhausted_metadata_reads += 1
            if state.exhausted_metadata_reads >= 3:
                state.clock_offset += (
                    46  # Only skip the unknown-release wait after all attempts have expired.
                )
        decoded = base64.b64decode(command[-1]).decode("utf-16-le")
        assert "capture-metadata.json" in decoded and "capture.ps1" not in decoded
        assert args.generation in decoded and "same-host" in decoded
        return subprocess.CompletedProcess(command, 0, json.dumps(state.final).encode(), b"")

    def popen(command, **kwargs):
        state.commands.append(list(command))
        process = Forward(command, kwargs["stderr"])
        state.processes.append(process)
        return process

    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(module.subprocess, "Popen", popen)
    yield state
    for process in state.processes:
        process.listener.close()
        with module.suppress(OSError):
            process.writer.close()
        for client in process.clients:
            with module.suppress(OSError):
                client.close()
    assert not state.errors


def test_ssh_reset_reattaches_only_forwarding_to_original_live_capture(reset_transport):
    state = reset_transport
    state.replay = True
    assert (
        state.module.run_capture(state.args) == 1
    )  # This short fixture does not pretend to qualify 20 seconds.
    assert len(state.commands) == 2
    assert state.stage_count == 1
    assert "-N" not in state.commands[0] and "-N" in state.commands[1]
    old_port = state.commands[0][state.commands[0].index("-L") + 1].split(":")
    new_port = state.commands[1][state.commands[1].index("-L") + 1].split(":")
    assert old_port[1] != new_port[1] and old_port[2:] == new_port[2:]
    assert "EncodedCommand" not in " ".join(state.commands[1])
    assert state.forwarded_health[0]["running"] is False
    assert state.forwarded_health[0]["received_wall_time_ms"] > 0
    rows = [
        json.loads(line) for line in (state.args.output_dir / "frame-health.ndjson").read_text().splitlines()
    ]
    accepted = [row for row in rows if row["accepted"]]
    assert len(accepted) >= 4 and len([row for row in accepted if row["sequence"] > 3]) >= 2
    assert any(
        not row["accepted"] and row["rejection"]["reason"] == "observer challenge mismatch" for row in rows
    )
    nonces = [value["nonce"] for value in state.received if value["event"] == "frame"]
    assert len(nonces) == len(set(nonces))
    final = json.loads((state.args.output_dir / "receiver-result.json").read_text())
    assert final["camera_released"] is True and final["capture_success"] is True
    assert final["failure"] is None and final["same_capture_reconnects"] == 1
    assert final["continuous_delivery_qualified"] is False
    assert final["owner_ssh_exit_code"] == 255
    assert final["ssh_exit_code"] == 1 and final["forwarding_retired"] is True
    assert final["first_transport_failure"] is not None
    assert state.metadata_reads >= 1
    assert (
        json.loads((state.args.output_dir / "capture-result.json").read_text())["recording_stopped_utc"]
        == "original-stop"
    )


def test_transport_and_socket_recovery_share_the_original_three_attempt_limit(reset_transport):
    state = reset_transport
    state.resets = 99
    assert state.module.run_capture(state.args) == 1
    assert len(state.commands) == 4  # One owner, at most three forwarding-only reattachments.
    assert state.stage_count == 1 and all("-N" in command for command in state.commands[1:])
    result = json.loads((state.args.output_dir / "receiver-result.json").read_text())
    assert result["same_capture_reconnects"] == 3
    assert result["failure"] == "observer same-capture reconnect attempt limit"
    assert result["camera_released"] is False
    assert state.source["duration_seconds"] == 20


@pytest.mark.parametrize("compression", [False, True])
def test_delivery_compression_choice_is_only_on_capture_and_forwarding(reset_transport, compression):
    state = reset_transport
    state.args.delivery_compression = compression
    state.module.run_capture(state.args)
    assert len(state.commands) == 2
    assert all(("Compression=yes" in command) is compression for command in state.commands)
    result = json.loads((state.args.output_dir / "receiver-result.json").read_text())
    assert result["delivery_compression"] is compression
    assert "Compression=yes" not in state.module._ssh(state.args.ssh_host)


def test_failed_forward_start_consumes_original_three_attempts(reset_transport):
    state = reset_transport
    state.failed_forwards = True
    assert state.module.run_capture(state.args) == 1
    result = json.loads((state.args.output_dir / "receiver-result.json").read_text())
    assert len(state.commands) == 4 and state.stage_count == 1
    assert result["same_capture_reconnects"] == 3
    assert result["failure"] == "observer same-capture reconnect attempt limit"
    assert result["camera_released"] is False


def test_normal_owner_ending_preserves_original_qualified_stop_and_fifo(reset_transport, monkeypatch):
    state = reset_transport
    state.resets = 0
    state.qualified_run = True
    monkeypatch.setattr(state.module, "CHALLENGE_INTERVAL_SECONDS", 0.001)
    assert state.module.run_capture(state.args) == 0
    result = json.loads((state.args.output_dir / "receiver-result.json").read_text())
    assert result["max_contiguous_delivery_span_ms"] >= 20000
    assert result["qualification_at_stop"] is True
    assert result["current_contiguous_delivery_qualified"] is False
    assert result["delivery_issues"] == 0 and result["same_capture_reconnects"] == 0
    assert len(state.commands) == 1 and result["first_transport_failure"] is None
    assert result["camera_released"] is True and result["owner_ssh_exit_code"] == 0


@pytest.mark.parametrize("event", [[], {}, None, "other"])
def test_terminal_metadata_invalid_event_is_a_bounded_refusal(event):
    module = load_observer()
    with pytest.raises(ValueError, match="identity/status"):
        module._validate_capture_terminal(
            {"generation": "g", "event": event, "camera_released": True, "success": True}, "g"
        )


@pytest.mark.parametrize(
    "payload,valid",
    [
        (b'\xef\xbb\xbf{"generation":"g","event":"complete","success":true,"camera_released":true}', True),
        (b'{"generation":"g","event":"complete"', False),
        (b'{"generation":"wrong","event":"complete","success":true,"camera_released":true}', False),
        (b'{"generation":"g","event":"complete","success":"false","camera_released":"false"}', False),
        (b'{"generation":"g","event":[],"success":true,"camera_released":true}', False),
        (b"null", False),
    ],
)
def test_final_metadata_fetch_accepts_only_original_strict_terminal(tmp_path, monkeypatch, payload, valid):
    import subprocess
    from types import SimpleNamespace

    module = load_observer()
    args = SimpleNamespace(generation="g", ssh_host="existing-alias")
    calls = []

    def read(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, payload, b"")

    monkeypatch.setattr(module.subprocess, "run", read)
    value = module._fetch_capture_terminal(
        args, {"expected_host": "same-host"}, r"C:\private\g", tmp_path, timeout=0.5
    )
    assert (value is not None) is valid
    assert calls[0][1]["timeout"] == 0.5
    script = base64.b64decode(calls[0][0][-1]).decode("utf-16-le")
    assert "capture-metadata.json" in script and "capture.ps1" not in script
    assert "same-host" in script and r"C:\private\g" in script
    assert not (tmp_path / "latest-health.json").exists()


def test_owner_alive_stalled_forward_is_rebuilt_without_restarting_capture(reset_transport):
    state = reset_transport
    state.owner_alive_stalled = True
    assert (
        state.module.run_capture(state.args) == 1
    )  # Short transport fixture never claims 20s qualification.
    assert len(state.commands) == 2 and state.stage_count == 1
    assert state.owner_alive_at_reattach == [True]
    original_spec = state.commands[0][state.commands[0].index("-L") + 1].split(":")
    replacement_spec = state.commands[1][state.commands[1].index("-L") + 1].split(":")
    assert original_spec[1] != replacement_spec[1]
    assert original_spec[2:] == replacement_spec[2:]
    assert "-N" in state.commands[1] and "EncodedCommand" not in " ".join(state.commands[1])
    result = json.loads((state.args.output_dir / "receiver-result.json").read_text())
    assert result["same_capture_reconnects"] == 1 and result["failure"] is None
    assert result["owner_ssh_exit_code"] == 0
    assert result["ssh_exit_code"] == 1 and result["forwarding_retired"] is True
    assert result["camera_released"] is True and result["capture_success"] is True
    assert result["qualification_at_stop"] is False
    assert len({v["nonce"] for v in state.received if v["event"] == "frame"}) == result["challenges_issued"]
    assert any(v["event"] == "stop" for v in state.received)
