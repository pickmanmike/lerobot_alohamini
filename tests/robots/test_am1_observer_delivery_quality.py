from __future__ import annotations

import base64
import importlib.util
import io
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "tools/am1_observer_capture.ps1"
INVALID_QUALITY = [None, True, False, 15.0, 45.0, "15", "45", "", 0, 14, 16, 44, 46, 100]


def observer():
    spec = importlib.util.spec_from_file_location("observer_quality_tests", ROOT / "tools/am1_observer.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("value", INVALID_QUALITY)
def test_invalid_quality_refuses_before_config_output_staging_or_owner(tmp_path, monkeypatch, value):
    module = observer()
    args = SimpleNamespace(
        config=tmp_path / "missing-private.json",
        output_dir=tmp_path / "must-not-exist",
        delivery_jpeg_quality_percent=value,
    )
    monkeypatch.setattr(module.subprocess, "run", lambda *_a, **_kw: pytest.fail("Staging attempted"))
    monkeypatch.setattr(module.subprocess, "Popen", lambda *_a, **_kw: pytest.fail("Owner attempted"))
    with pytest.raises(ValueError, match="JPEG quality"):
        module.run_capture(args)
    assert not args.output_dir.exists()


@pytest.mark.parametrize("choice,expected", [(None, 45), ("15", 15), ("45", 45)])
def test_cli_quality_default_and_explicit_choices(monkeypatch, choice, expected):
    module = observer()
    calls = []
    monkeypatch.setattr(module, "run_capture", lambda args: calls.append(args) or 0)
    argv = ["observer", "--ssh-host", "existing-alias", "--config", "private.json", "--output-dir", "capture"]
    if choice is not None:
        argv += ["--delivery-jpeg-quality-percent", choice]
    monkeypatch.setattr(sys, "argv", argv)
    assert module.main() == 0
    assert getattr(calls[0], "delivery_jpeg_quality_percent", None) == expected


@pytest.mark.parametrize("choice", ["14", "46", "15.0", "nan"])
def test_cli_invalid_quality_never_enters_capture(monkeypatch, choice):
    module = observer()
    monkeypatch.setattr(module, "run_capture", lambda *_: pytest.fail("Capture entered"))
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
            "--delivery-jpeg-quality-percent",
            choice,
        ],
    )
    with pytest.raises(SystemExit) as result:
        module.main()
    assert result.value.code == 2


@pytest.mark.parametrize("selected,source_quality", [(None, 45), (15, 15), (45, 45), (15, 45), (45, None)])
def test_real_capture_stages_per_capture_quality_and_retains_source_verification(
    tmp_path, monkeypatch, selected, source_quality
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
                "delivery_jpeg_quality_percent": 15,
            }
        )
    )
    original = config.read_bytes()
    args = SimpleNamespace(
        config=config,
        output_dir=tmp_path / "capture",
        generation="quality-test",
        duration_seconds=20,
        ssh_host="existing-alias",
    )
    if selected is not None:
        args.delivery_jpeg_quality_percent = selected
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

    terminal = {"event": "complete", "generation": args.generation, "success": True, "camera_released": True}
    if source_quality is not None:
        terminal["delivery_jpeg_quality_percent"] = source_quality
    owner = SimpleNamespace(
        returncode=0, stdout=io.BytesIO((json.dumps(terminal) + "\n").encode()), poll=lambda: 0
    )
    monkeypatch.setattr(module.subprocess, "run", stage)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *_a, **_kw: owner)
    monkeypatch.setattr(socket, "socket", lambda *_a, **_kw: Probe())
    monkeypatch.setattr(socket, "create_connection", lambda *_a, **_kw: pytest.fail("Network attempted"))
    assert module.run_capture(args) == 1  # Metadata alone cannot qualify observation.
    expected = 45 if selected is None else selected
    assert staged[0].get("delivery_jpeg_quality_percent") == expected
    assert staged[0]["generation"] == args.generation and len(staged[0]["token"]) == 64
    assert staged[0]["duration_seconds"] == 20 and staged[0]["max_recording_bytes"] == 146_800_640
    assert config.read_bytes() == original
    result = json.loads((args.output_dir / "receiver-result.json").read_text())
    transport = json.loads((args.output_dir / "delivery-transport.json").read_text())
    assert (
        result.get("delivery_jpeg_quality_percent")
        == transport.get("delivery_jpeg_quality_percent")
        == expected
    )
    assert result.get("source_delivery_jpeg_quality_percent") == source_quality
    assert result["failure"] is None and result["camera_released"] is True
    assert result["accepted_frames"] == 0 and result["qualification_at_stop"] is False


def powershell(script):
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def source_config(tmp_path, value, *, missing=False):
    config = {
        "expected_host": os.environ.get("COMPUTERNAME", "offline-test"),
        "generation": "quality-test",
        "duration_seconds": 20,
        "max_recording_bytes": 146_800_640,
    }
    if not missing:
        config["delivery_jpeg_quality_percent"] = value
    path = tmp_path / "source-config.json"
    path.write_text(json.dumps(config))
    return str(path).replace("'", "''")


@pytest.mark.skipif(sys.platform != "win32", reason="actual PowerShell source validation")
@pytest.mark.parametrize("value", INVALID_QUALITY)
def test_actual_source_invalid_quality_refuses_before_runtime_or_camera_owner(tmp_path, value):
    path = source_config(tmp_path, value)
    source = str(SOURCE).replace("'", "''")
    result = powershell(
        f"""
$ErrorActionPreference='Stop'
$script:ownerBoundaryReached=$false
function Add-Type {{ $script:ownerBoundaryReached=$true;throw 'Camera/runtime ownership boundary reached' }}
try {{ & '{source}' -ConfigPath '{path}';throw 'Invalid source quality accepted' }}
catch {{ @{{reason=$_.Exception.GetBaseException().Message;ownerBoundaryReached=$script:ownerBoundaryReached}} | ConvertTo-Json -Compress }}
"""
    )
    assert "JPEG quality" in result["reason"]
    assert result["ownerBoundaryReached"] is False


@pytest.mark.skipif(sys.platform != "win32", reason="actual PowerShell source default/result")
@pytest.mark.parametrize("value,missing,expected", [(None, True, 45), (15, False, 15), (45, False, 45)])
def test_actual_source_default_and_choice_reach_terminal_scalar_before_owner(
    tmp_path, value, missing, expected
):
    path = source_config(tmp_path, value, missing=missing)
    source = str(SOURCE).replace("'", "''")
    result = powershell(
        f"""
$ErrorActionPreference='Stop'
$ast=[System.Management.Automation.Language.Parser]::ParseFile('{source}',[ref]$null,[ref]$null)
$firstRuntime=$ast.FindAll({{param($n) $n -is [System.Management.Automation.Language.CommandAst] -and $n.GetCommandName() -eq 'Add-Type'}},$true) | Sort-Object {{$_.Extent.StartOffset}} | Select-Object -First 1
$prefix=$ast.Extent.Text.Substring(0,$firstRuntime.Extent.StartOffset)
$resultStatement=$ast.FindAll({{param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -eq '$result'}},$true) | Sort-Object {{$_.Extent.StartOffset}} | Select-Object -First 1
$driver=$prefix+[Environment]::NewLine+$resultStatement.Extent.Text+[Environment]::NewLine+'$result | ConvertTo-Json -Compress'
& ([scriptblock]::Create($driver)) -ConfigPath '{path}'
"""
    )
    assert result.get("delivery_jpeg_quality_percent") == expected
    assert result["camera_released"] is False and result["event"] == "failed"


@pytest.mark.skipif(
    sys.platform != "win32", reason="actual C# live encoder contract without WinRT or devices"
)
def test_actual_encoder_quality_fraction_preserves_default_single_and_delivery_dimensions():
    source = str(SOURCE).replace("'", "''")
    result = powershell(
        f"""
$ErrorActionPreference='Stop'
$ast=[System.Management.Automation.Language.Parser]::ParseFile('{source}',[ref]$null,[ref]$null)
$collectorAssignment=$ast.FindAll({{param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -eq '$collectorSource'}},$true)[0]
$collector=$collectorAssignment.Right.Expression.Value
$methods=[regex]::Match($collector,'(?s)public static Windows.Foundation.IAsyncOperation<BitmapEncoder> CreateJpegEncoder.*?(?=\r?\n    public AM1ObserverFrame CopyAfter)').Value
if(-not $methods) {{ throw 'Live encoder contract missing' }}
$encoderStatement=$ast.FindAll({{param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -eq '$encoder'}},$true)[0]
$doubles=@'
using System;
using Windows.Graphics.Imaging;
namespace Windows.Foundation {{ public interface IAsyncOperation<T> {{}} public enum PropertyType {{Single}} }}
namespace Windows.Storage.Streams {{ public interface IRandomAccessStream {{}} }}
namespace Windows.Graphics.Imaging {{
 public enum BitmapInterpolationMode {{Linear}}
 public sealed class BitmapTransform {{ public uint ScaledWidth,ScaledHeight; public BitmapInterpolationMode InterpolationMode; }}
 public sealed class BitmapTypedValue {{ public object Value; public Windows.Foundation.PropertyType Type; public BitmapTypedValue(object value,Windows.Foundation.PropertyType type){{Value=value;Type=type;}} }}
 public sealed class BitmapPropertySet : System.Collections.Generic.Dictionary<string,BitmapTypedValue> {{}}
 public sealed class EncoderOperation : Windows.Foundation.IAsyncOperation<BitmapEncoder> {{ public BitmapEncoder Encoder=new BitmapEncoder(); }}
 public sealed class BitmapEncoder {{
  public static Guid JpegEncoderId=Guid.NewGuid(); public static BitmapPropertySet Properties;
  public BitmapTransform BitmapTransform=new BitmapTransform();
  public static Windows.Foundation.IAsyncOperation<BitmapEncoder> CreateAsync(Guid id,Windows.Storage.Streams.IRandomAccessStream stream,BitmapPropertySet properties){{Properties=properties;return new EncoderOperation();}}
 }}
}}
public sealed class AM1ObserverCollector {{
'@
$probe=@'
 public static object[] ObserveProperties(BitmapEncoder encoder) {{
  var property=BitmapEncoder.Properties["ImageQuality"];
  return new object[] {{ BitConverter.ToInt32(BitConverter.GetBytes((float)property.Value),0),property.Type.ToString(),BitmapEncoder.Properties.Count,encoder.BitmapTransform.ScaledWidth,encoder.BitmapTransform.ScaledHeight,encoder.BitmapTransform.InterpolationMode.ToString() }};
 }}
'@
Add-Type ($doubles+$methods+$probe+'}}')
function Wait-CaptureOperation($operation,$type,$step,$timeout) {{ return $operation.Encoder }}
$values=[Collections.Generic.List[object]]::new()
foreach($choice in @('default',15,45)) {{
 if($choice -eq 'default') {{ $encoder=([AM1ObserverCollector]::CreateJpegEncoder($null)).Encoder }}
 else {{ $deliveryJpegQualityPercent=[int]$choice;$stream=$null;$encoderType=$null;Invoke-Expression $encoderStatement.Extent.Text }}
 [AM1ObserverCollector]::ConfigureDeliveryJpeg($encoder)
 $observed=[AM1ObserverCollector]::ObserveProperties($encoder)
 $values.Add(@{{choice=$choice;single_bits=$observed[0];property_type=$observed[1];property_count=$observed[2];width=$observed[3];height=$observed[4];interpolation=$observed[5]}})
}}
$rejected=$false
try {{$null=[AM1ObserverCollector]::CreateJpegEncoder($null,99)}} catch {{$rejected=$_.Exception.GetBaseException() -is [ArgumentOutOfRangeException]}}
@{{values=$values.ToArray();invalid_rejected=$rejected}} | ConvertTo-Json -Depth 4 -Compress
"""
    )
    assert [value["single_bits"] for value in result["values"]] == [
        1_055_286_886,
        1_041_865_114,
        1_055_286_886,
    ]
    assert all(
        value["property_type"] == "Single"
        and value["property_count"] == 1
        and value["width"] == 640
        and value["height"] == 360
        and value["interpolation"] == "Linear"
        for value in result["values"]
    )
    assert result["invalid_rejected"] is True
