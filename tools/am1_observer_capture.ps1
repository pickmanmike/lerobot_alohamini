param([Parameter(Mandatory=$true)][string]$ConfigPath)
$ProgressPreference='SilentlyContinue'
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$config=Get-Content -Raw -LiteralPath $ConfigPath | ConvertFrom-Json
if ($env:COMPUTERNAME -ne $config.expected_host) { throw 'Unexpected observer host' }
if ($config.generation -notmatch '^[A-Za-z0-9_-]{1,80}$') { throw 'Invalid observer generation' }
if ($config.duration_seconds -lt 20 -or $config.duration_seconds -gt 660) { throw 'Invalid finite observer duration' }
if ($config.max_recording_bytes -gt 146800640 -or $config.max_recording_bytes -lt 1048576) { throw 'Invalid recording cap' }
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$actionAsTask=[System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and -not $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction' } | Select-Object -First 1
$operationAsTask=[System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq ('IAsyncOperation'+[char]96+'1') } | Select-Object -First 1
function Wait-CaptureAction($action,[string]$step,[int]$timeout=15000) {
    $task=$actionAsTask.Invoke($null,@($action))
    if (-not $task.Wait($timeout)) { throw ($step+' deadline') }
}
function Wait-CaptureOperation($operation,[Type]$type,[string]$step,[int]$timeout=15000) {
    $task=$operationAsTask.MakeGenericMethod($type).Invoke($null,@($operation))
    if (-not $task.Wait($timeout)) { throw ($step+' deadline') }
    return $task.Result
}
function Get-QpcTicks { return [int64]([Diagnostics.Stopwatch]::GetTimestamp()*10000000.0/[Diagnostics.Stopwatch]::Frequency) }
function Send-Observer($value) { [Console]::Out.WriteLine(($value | ConvertTo-Json -Depth 8 -Compress)); [Console]::Out.Flush() }
$collectorSource=@'
using System;
using System.IO;
using Windows.Graphics.Imaging;
using Windows.Media.Capture;
using Windows.Media.Capture.Frames;

public sealed class AM1ObserverFrame : IDisposable {
    public long Sequence;
    public long SourceTicks;
    public long ArrivalTicks;
    public long CopiedTicks;
    public long SelectedTicks;
    public SoftwareBitmap Bitmap;
    public void Dispose() { if(Bitmap != null) { Bitmap.Dispose(); Bitmap=null; } }
}
public sealed class AM1ObserverCollector : IDisposable {
    private readonly object gate = new object();
    private MediaFrameReader reader;
    private MediaCapture capture;
    private SoftwareBitmap latest;
    private long sequence;
    private long arrivalTicks;
    private long copiedTicks;
    private long sourceTicks;
    private string failure;
    public static MediaFrameSource FindColorSource(MediaCapture owner) {
        MediaFrameSource preview=null, record=null;
        foreach(var entry in owner.FrameSources) {
            var source=entry.Value;
            if(source.Info.SourceKind != MediaFrameSourceKind.Color) continue;
            if(source.Info.MediaStreamType == MediaStreamType.VideoPreview) {
                if(preview != null) throw new InvalidOperationException("Multiple preview color sources");
                preview=source;
            } else if(source.Info.MediaStreamType == MediaStreamType.VideoRecord) {
                if(record != null) throw new InvalidOperationException("Multiple record color sources");
                record=source;
            }
        }
        if(preview == null && record == null) throw new InvalidOperationException("Same-owner color source unavailable");
        return preview ?? record;
    }
    public string Failure { get { lock(gate) return failure; } }
    public long Sequence { get { lock(gate) return sequence; } }
    public void Attach(MediaFrameReader value, MediaCapture owner) {
        reader=value; capture=owner;
        reader.FrameArrived += OnFrameArrived;
        capture.Failed += OnCaptureFailed;
    }
    private void OnCaptureFailed(MediaCapture sender, MediaCaptureFailedEventArgs args) {
        lock(gate) failure="MediaCapture failed: "+args.Code+" "+args.Message;
    }
    private void OnFrameArrived(MediaFrameReader sender, MediaFrameArrivedEventArgs args) {
        long arrived=(long)(System.Diagnostics.Stopwatch.GetTimestamp()*10000000.0/System.Diagnostics.Stopwatch.Frequency);
        try {
            using(var frame=sender.TryAcquireLatestFrame()) {
                if(frame == null || !frame.SystemRelativeTime.HasValue || frame.VideoMediaFrame == null) return;
                long ticks=frame.SystemRelativeTime.Value.Ticks;
                using(var bitmap=frame.VideoMediaFrame.SoftwareBitmap) {
                    if(bitmap == null) return;
                    var copy=SoftwareBitmap.Copy(bitmap);
                    long copied=(long)(System.Diagnostics.Stopwatch.GetTimestamp()*10000000.0/System.Diagnostics.Stopwatch.Frequency);
                    lock(gate) {
                        if(ticks <= sourceTicks) { copy.Dispose(); return; }
                        if(latest != null) latest.Dispose();
                        latest=copy; sourceTicks=ticks; arrivalTicks=arrived; copiedTicks=copied; sequence++;
                    }
                }
            }
        } catch(Exception error) {
            lock(gate) failure="Frame callback failed: "+error.GetType().Name+" "+error.Message;
        }
    }
    public static Windows.Foundation.IAsyncOperation<BitmapEncoder> CreateJpegEncoder(Windows.Storage.Streams.IRandomAccessStream stream) {
        var properties=new BitmapPropertySet();
        properties.Add("ImageQuality",new BitmapTypedValue((float)0.45,Windows.Foundation.PropertyType.Single));
        return BitmapEncoder.CreateAsync(BitmapEncoder.JpegEncoderId,stream,properties);
    }
    public static void ConfigureDeliveryJpeg(BitmapEncoder encoder) {
        encoder.BitmapTransform.ScaledWidth=640;
        encoder.BitmapTransform.ScaledHeight=360;
        encoder.BitmapTransform.InterpolationMode=BitmapInterpolationMode.Linear;
    }
    public AM1ObserverFrame CopyAfter(long challengeTicks) {
        lock(gate) {
            if(latest == null || sourceTicks <= challengeTicks) return null;
            return new AM1ObserverFrame { Sequence=sequence, SourceTicks=sourceTicks, ArrivalTicks=arrivalTicks, CopiedTicks=copiedTicks, SelectedTicks=(long)(System.Diagnostics.Stopwatch.GetTimestamp()*10000000.0/System.Diagnostics.Stopwatch.Frequency), Bitmap=SoftwareBitmap.Copy(latest) };
        }
    }
    public void Dispose() {
        if(reader != null) { reader.FrameArrived -= OnFrameArrived; reader=null; }
        if(capture != null) { capture.Failed -= OnCaptureFailed; capture=null; }
        lock(gate) { if(latest != null) latest.Dispose(); latest=null; }
    }
}
public sealed class AM1ObserverCommand {
    public string Event;
    public string Nonce;
    public long ReceivedTicks;
    public string NewestReceivedNonce;
    public long NewestReceivedTicks;
    public long ReceiptCount;
    public long CoalescedCount;
}
public sealed class AM1ObserverChallengeWindow {
    private readonly System.Collections.Generic.List<AM1ObserverCommand> commands=new System.Collections.Generic.List<AM1ObserverCommand>();
    public void Add(AM1ObserverCommand command) {
        if(command == null || command.Event != "frame") throw new ArgumentException("Expected authenticated frame challenge");
        foreach(var existing in commands) if(existing.Nonce == command.Nonce) return;
        commands.Add(new AM1ObserverCommand { Event=command.Event, Nonce=command.Nonce, ReceivedTicks=command.ReceivedTicks });
        if(commands.Count > 3) commands.RemoveAt(0);
    }
    public AM1ObserverCommand NewestBefore(long sourceTicks,long nowTicks) {
        for(int i=commands.Count-1;i>=0;i--) {
            var command=commands[i];
            long elapsed=nowTicks-command.ReceivedTicks;
            if(command.ReceivedTicks < sourceTicks && elapsed >= 0 && elapsed <= 7500000) return new AM1ObserverCommand { Event=command.Event, Nonce=command.Nonce, ReceivedTicks=command.ReceivedTicks };
        }
        return null;
    }
}
public sealed class AM1ObserverChannel : IDisposable {
    private readonly object gate = new object();
    private readonly string token;
    private readonly string generation;
    private readonly System.Net.Sockets.TcpListener listener;
    private System.Net.Sockets.TcpClient client;
    private AM1ObserverCommand latest;
    private bool stopped;
    private readonly AM1ObserverChallengeWindow challenges=new AM1ObserverChallengeWindow();
    private readonly System.Collections.Generic.HashSet<string> seenNonces=new System.Collections.Generic.HashSet<string>();
    private long framesReceived;
    private long coalescedFrames;
    private string newestNonce;
    private long newestTicks;
    private long rejected;
    private string failure;
    private readonly System.Threading.Thread thread;
    public string Failure { get { lock(gate) return failure; } }
    public long Rejected { get { lock(gate) return rejected; } }
    public long FramesReceived { get { lock(gate) return framesReceived; } }
    public long CoalescedFrameCommands { get { lock(gate) return coalescedFrames; } }
    public AM1ObserverChannel(int port, string secret, string identity) {
        token=secret; generation=identity;
        listener=new System.Net.Sockets.TcpListener(System.Net.IPAddress.Loopback,port);
        listener.Start();
        thread=new System.Threading.Thread(ReadLoop);
        thread.IsBackground=true; thread.Start();
    }
    private static bool SameSecret(string first,string second) {
        if(first == null || second == null || first.Length != second.Length) return false;
        int difference=0;
        for(int i=0;i<first.Length;i++) difference |= first[i]^second[i];
        return difference == 0;
    }
    private static string ReadBoundedLine(StreamReader reader) {
        var buffer=new System.Text.StringBuilder();
        int value;
        while((value=reader.Read()) >= 0) {
            if(value == 10) return buffer.ToString().TrimEnd('\r');
            if(buffer.Length >= 4096) throw new IOException("Observer command size limit");
            buffer.Append((char)value);
        }
        return buffer.Length == 0 ? null : buffer.ToString();
    }
    private void ReadLoop() {
        while(true) {
            lock(gate) { if(stopped) return; }
            System.Net.Sockets.TcpClient accepted=null;
            try {
                accepted=listener.AcceptTcpClient();
                accepted.NoDelay=true; accepted.SendTimeout=750; accepted.ReceiveTimeout=2500;
                lock(gate) { if(stopped) { accepted.Close(); return; } client=accepted; }
                using(var reader=new StreamReader(accepted.GetStream(),new System.Text.UTF8Encoding(false),false,4096,true)) {
                    while(true) {
                        string line=ReadBoundedLine(reader);
                        long ticks=(long)(System.Diagnostics.Stopwatch.GetTimestamp()*10000000.0/System.Diagnostics.Stopwatch.Frequency);
                        if(line == null) break;
                        if(line.Length > 4096) break;
                        System.Collections.Generic.Dictionary<string,object> value;
                        try { value=new System.Web.Script.Serialization.JavaScriptSerializer().Deserialize<System.Collections.Generic.Dictionary<string,object>>(line); }
                        catch { lock(gate) rejected++; continue; }
                        object secret, identity, kind, nonce;
                        if(!value.TryGetValue("token",out secret) || !SameSecret(secret as string,token) ||
                           !value.TryGetValue("generation",out identity) || (identity as string) != generation ||
                           !value.TryGetValue("event",out kind)) { lock(gate) rejected++; continue; }
                        string eventName=kind as string;
                        string nonceValue=null;
                        if(eventName == "frame") {
                            if(!value.TryGetValue("nonce",out nonce) || !(nonce is string) ||
                               !System.Text.RegularExpressions.Regex.IsMatch((string)nonce,"^[A-Za-z0-9_-]{1,80}$")) { lock(gate) rejected++; continue; }
                            nonceValue=(string)nonce;
                        } else if(eventName != "stop") { lock(gate) rejected++; continue; }
                        lock(gate) {
                            if(latest != null && latest.Event == "stop") continue;
                            var command=new AM1ObserverCommand { Event=eventName, Nonce=nonceValue, ReceivedTicks=ticks };
                            if(eventName == "frame") {
                                // Retain the original receipt before PowerShell can coalesce notifications.
                                if(seenNonces.Contains(nonceValue)) { rejected++; continue; }
                                if(seenNonces.Count >= 8192) { failure="Observer nonce storage cap"; return; }
                                seenNonces.Add(nonceValue);
                                challenges.Add(command);
                                framesReceived++;
                                newestNonce=nonceValue; newestTicks=ticks;
                                if(latest != null && latest.Event == "frame") coalescedFrames++;
                            }
                            latest=command;
                        }
                    }
                }
            } catch(System.Net.Sockets.SocketException) {
                lock(gate) { if(stopped) return; }
            } catch(IOException) {
                lock(gate) { if(stopped) return; }
            } catch(ObjectDisposedException) {
                lock(gate) { if(stopped) return; }
            } catch(Exception error) {
                lock(gate) { if(!stopped) failure="Observer channel reader: "+error.GetType().Name+" "+error.Message; }
                return;
            } finally {
                lock(gate) {
                    if(client == accepted) client=null;
                }
                if(accepted != null) accepted.Close();
            }
        }
    }
    public AM1ObserverCommand TakeLatest() {
        lock(gate) { var result=latest; latest=null; return result; }
    }
    public AM1ObserverCommand NewestBefore(long sourceTicks,long nowTicks) {
        lock(gate) {
            var proof=challenges.NewestBefore(sourceTicks,nowTicks);
            if(proof != null) {
                proof.NewestReceivedNonce=newestNonce; proof.NewestReceivedTicks=newestTicks;
                proof.ReceiptCount=framesReceived; proof.CoalescedCount=coalescedFrames;
            }
            return proof;
        }
    }
    public bool Send(string line) {
        lock(gate) {
            if(client == null || stopped) return false;
            try {
                var bytes=System.Text.Encoding.UTF8.GetBytes(line+"\n");
                client.GetStream().Write(bytes,0,bytes.Length);
                client.GetStream().Flush();
                return true;
            } catch(IOException) { client.Close(); client=null; return false; }
              catch(System.Net.Sockets.SocketException) { client.Close(); client=null; return false; }
        }
    }
    public void Dispose() {
        lock(gate) { stopped=true; latest=null; if(client != null) client.Close(); client=null; }
        listener.Stop();
        if(!thread.Join(500)) throw new IOException("Observer channel reader release deadline");
    }
}
'@
$frameworkRoot=[Runtime.InteropServices.RuntimeEnvironment]::GetRuntimeDirectory()
$references=@(
    (Join-Path $frameworkRoot 'System.dll'),
    (Join-Path $frameworkRoot 'System.Core.dll'),
    (Join-Path $env:SystemRoot 'System32\WinMetadata\Windows.Foundation.winmd'),
    (Join-Path $env:SystemRoot 'System32\WinMetadata\Windows.Media.winmd'),
    (Join-Path $env:SystemRoot 'System32\WinMetadata\Windows.Graphics.winmd'),
    (Join-Path $env:SystemRoot 'System32\WinMetadata\Windows.Storage.winmd'),
    (Join-Path $frameworkRoot 'System.Runtime.WindowsRuntime.dll'),
    ([Reflection.Assembly]::Load('System.Web.Extensions, Version=4.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35').Location),
    ([Reflection.Assembly]::Load('System.Runtime.InteropServices.WindowsRuntime, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b03f5f7f11d50a3a').Location),
    ([Reflection.Assembly]::Load('System.Runtime, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b03f5f7f11d50a3a').Location)
)
$compiler=[Microsoft.CSharp.CSharpCodeProvider]::new()
$parameters=[CodeDom.Compiler.CompilerParameters]::new()
$parameters.GenerateInMemory=$true
foreach($reference in $references) { $null=$parameters.ReferencedAssemblies.Add($reference) }
$compiled=$compiler.CompileAssemblyFromSource($parameters,$collectorSource)
if($compiled.Errors.HasErrors) { throw (($compiled.Errors | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine) }
$null=$compiled.CompiledAssembly
$capture=$null
$reader=$null
$collector=$null
$channel=$null
$recording=$false
$reading=$false
$clipPath=Join-Path $config.output_dir 'continuous.mp4'
$metadataPath=Join-Path $config.output_dir 'capture-metadata.json'
$phaseTimings=[Collections.Generic.List[object]]::new()
$result=[ordered]@{event='failed';generation=$config.generation;success=$false;camera_released=$false;audio_recorded=$false;clip_path=$clipPath;requested_duration_seconds=$config.duration_seconds;acquired_utc=[DateTimeOffset]::UtcNow.ToString('o')}
try {
    if ($config.token -notmatch '^[a-f0-9]{64}$' -or $config.capture_port -lt 1024 -or $config.capture_port -gt 65535) { throw 'Invalid private observer channel configuration' }
    $channel=[AM1ObserverChannel]::new([int]$config.capture_port,$config.token,$config.generation)
    $settings=[Windows.Media.Capture.MediaCaptureInitializationSettings,Windows.Media,ContentType=WindowsRuntime]::new()
    $settings.VideoDeviceId=$config.video_device_id
    $settings.StreamingCaptureMode=[Windows.Media.Capture.StreamingCaptureMode,Windows.Media,ContentType=WindowsRuntime]::Video
    $settings.SharingMode=[Windows.Media.Capture.MediaCaptureSharingMode,Windows.Media,ContentType=WindowsRuntime]::ExclusiveControl
    $settings.MemoryPreference=[Windows.Media.Capture.MediaCaptureMemoryPreference,Windows.Media,ContentType=WindowsRuntime]::Cpu
    $capture=[Windows.Media.Capture.MediaCapture,Windows.Media,ContentType=WindowsRuntime]::new()
    Wait-CaptureAction ($capture.InitializeAsync($settings)) 'Initialize'
    $streamType=[Windows.Media.Capture.MediaStreamType,Windows.Media,ContentType=WindowsRuntime]::VideoRecord
    $selected=@($capture.VideoDeviceController.GetAvailableMediaStreamProperties($streamType) | Where-Object { $_.Subtype -eq 'NV12' -and $_.Width -eq 1280 -and $_.Height -eq 720 -and $_.FrameRate.Numerator -eq 10 -and $_.FrameRate.Denominator -eq 1 })
    if ($selected.Count -eq 0) { throw 'Verified observer record mode no longer available' }
    Wait-CaptureAction ($capture.VideoDeviceController.SetMediaStreamPropertiesAsync($streamType,$selected[0])) 'Set record mode'
    $source=[AM1ObserverCollector]::FindColorSource($capture)
    $formats=@($source.SupportedFormats | Where-Object { $_.Subtype -eq 'NV12' -and $_.VideoFormat.Width -eq 1280 -and $_.VideoFormat.Height -eq 720 -and $_.FrameRate.Numerator -eq 10 -and $_.FrameRate.Denominator -eq 1 })
    if ($formats.Count -eq 0) { throw 'Verified same-owner frame mode unavailable' }
    Wait-CaptureAction ($source.SetFormatAsync($formats[0])) 'Set frame source mode'
    $readerType=[Windows.Media.Capture.Frames.MediaFrameReader,Windows.Media,ContentType=WindowsRuntime]
    $reader=Wait-CaptureOperation ($capture.CreateFrameReaderAsync($source,'ARGB32')) $readerType 'Create frame reader'
    $reader.AcquisitionMode=[Windows.Media.Capture.Frames.MediaFrameReaderAcquisitionMode,Windows.Media,ContentType=WindowsRuntime]::Realtime
    $collector=[AM1ObserverCollector]::new()
    $collector.Attach($reader,$capture)
    $statusType=[Windows.Media.Capture.Frames.MediaFrameReaderStartStatus,Windows.Media,ContentType=WindowsRuntime]
    $status=Wait-CaptureOperation ($reader.StartAsync()) $statusType 'Start frame reader'
    if ($status.ToString() -ne 'Success') { throw ('Frame reader refused: '+$status.ToString()) }
    $reading=$true
    $profile=[Windows.Media.MediaProperties.MediaEncodingProfile,Windows.Media,ContentType=WindowsRuntime]::CreateMp4([Windows.Media.MediaProperties.VideoEncodingQuality,Windows.Media,ContentType=WindowsRuntime]::HD720p)
    $profile.Audio=$null
    $profile.Video.Width=1280; $profile.Video.Height=720
    $profile.Video.FrameRate.Numerator=10; $profile.Video.FrameRate.Denominator=1
    $profile.Video.Bitrate=1500000
    $newFile=[IO.File]::Open($clipPath,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
    $newFile.Dispose()
    $storageType=[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
    $file=Wait-CaptureOperation ($storageType::GetFileFromPathAsync($clipPath)) $storageType 'Private StorageFile'
    $result.recording_requested_qpc_ticks=Get-QpcTicks
    $result.recording_requested_utc=[DateTimeOffset]::UtcNow.ToString('o')
    Wait-CaptureAction ($capture.StartRecordToStorageFileAsync($profile,$file)) 'Start record'
    $recording=$true
    $result.recording_started_utc=[DateTimeOffset]::UtcNow.ToString('o')
    $result.recording_started_qpc_ticks=Get-QpcTicks
    Send-Observer ([ordered]@{event='started';generation=$config.generation;recording=$true;source_stream=$source.Info.MediaStreamType.ToString();source_id=$source.Info.Id;recording_started_utc=$result.recording_started_utc})
    $clock=[Diagnostics.Stopwatch]::StartNew()
    $nonce=$null; $challengeTicks=0; $challengeCount=0; $deliveredCount=0
    $lastProcessedSourceTicks=0
    while ($clock.Elapsed.TotalSeconds -lt $config.duration_seconds) {
        if ($collector.Failure) { throw $collector.Failure }
        if ($channel.Failure) { throw $channel.Failure }
        if ((Get-Item -LiteralPath $clipPath).Length -gt $config.max_recording_bytes) { throw 'Finite recording storage cap reached' }
        $command=$channel.TakeLatest()
        if ($null -ne $command) {
            if ($command.Event -eq 'stop') { $result.stop_reason='normal_stop'; break }
            $challengeCount=$channel.FramesReceived
        }
        if ($challengeCount -gt 0) {
            $sample=$collector.CopyAfter($lastProcessedSourceTicks)
            if ($null -ne $sample) {
                $lastProcessedSourceTicks=$sample.SourceTicks
                $proof=$channel.NewestBefore($sample.SourceTicks,(Get-QpcTicks))
                if($null -eq $proof) { $sample.Dispose(); continue }
                $nonce=$proof.Nonce
                $challengeTicks=$proof.ReceivedTicks
                $stream=$null
                $encodeStart=Get-QpcTicks
                try {
                    $stream=[Windows.Storage.Streams.InMemoryRandomAccessStream,Windows.Storage,ContentType=WindowsRuntime]::new()
                    $encoderType=[Windows.Graphics.Imaging.BitmapEncoder,Windows.Graphics,ContentType=WindowsRuntime]
                    $encoder=Wait-CaptureOperation ([AM1ObserverCollector]::CreateJpegEncoder($stream)) $encoderType 'JPEG encoder' 3000
                    $encoderCreated=Get-QpcTicks
                    $encoder.SetSoftwareBitmap($sample.Bitmap)
                    [AM1ObserverCollector]::ConfigureDeliveryJpeg($encoder)
                    Wait-CaptureAction ($encoder.FlushAsync()) 'JPEG encode' 3000
                    $encoderFlushed=Get-QpcTicks
                    if ($stream.Size -gt 524288) { throw 'Observer JPEG size cap' }
                    $stream.Seek(0)
                    $dataReader=[Windows.Storage.Streams.DataReader,Windows.Storage,ContentType=WindowsRuntime]::new($stream.GetInputStreamAt(0))
                    try {
                        $null=Wait-CaptureOperation ($dataReader.LoadAsync([uint32]$stream.Size)) ([uint32]) 'Read JPEG bytes' 3000
                        $bytesLoaded=Get-QpcTicks
                        $bytes=New-Object byte[] ([int]$stream.Size)
                        $dataReader.ReadBytes($bytes)
                        $bytesRead=Get-QpcTicks
                    } finally { $dataReader.Dispose() }
                    $ageMs=((Get-QpcTicks)-$sample.SourceTicks)/10000.0
                    $phase=[ordered]@{nonce=$nonce;sequence=$sample.Sequence;source_wait_ms=($sample.SourceTicks-$challengeTicks)/10000.0;newest_received_nonce=$proof.NewestReceivedNonce;newest_received_qpc_ticks=$proof.NewestReceivedTicks;challenge_receipts=$proof.ReceiptCount;coalesced_command_notifications=$proof.CoalescedCount;receipt_to_selection_ms=($sample.SelectedTicks-$challengeTicks)/10000.0;callback_source_age_ms=($sample.ArrivalTicks-$sample.SourceTicks)/10000.0;callback_copy_ms=($sample.CopiedTicks-$sample.ArrivalTicks)/10000.0;selected_source_age_ms=($sample.SelectedTicks-$sample.SourceTicks)/10000.0;encoder_create_ms=($encoderCreated-$encodeStart)/10000.0;encoder_flush_ms=($encoderFlushed-$encoderCreated)/10000.0;jpeg_load_ms=($bytesLoaded-$encoderFlushed)/10000.0;jpeg_read_ms=($bytesRead-$bytesLoaded)/10000.0;jpeg_bytes=$bytes.Length}
                    $serializeStart=Get-QpcTicks
                    $frameMessage=([ordered]@{event='frame';generation=$config.generation;nonce=$nonce;sequence=$sample.Sequence;source_system_relative_ticks=$sample.SourceTicks;challenge_received_qpc_ticks=$challengeTicks;capture_age_ms=$ageMs;recording=$recording;source_wait_ms=$phase.source_wait_ms;callback_source_age_ms=$phase.callback_source_age_ms;encoder_create_ms=$phase.encoder_create_ms;encoder_flush_ms=$phase.encoder_flush_ms;jpeg_read_ms=$phase.jpeg_read_ms;jpeg_base64=[Convert]::ToBase64String($bytes)} | ConvertTo-Json -Depth 6 -Compress)
                    $serialized=Get-QpcTicks
                    $sent=$channel.Send($frameMessage)
                    $sendEnded=Get-QpcTicks
                    $phase.json_serialize_ms=($serialized-$serializeStart)/10000.0
                    $phase.tcp_send_ms=($sendEnded-$serialized)/10000.0
                    $phase.sent=$sent
                    $phaseTimings.Add($phase)
                    if($phaseTimings.Count -gt 128) { $phaseTimings.RemoveAt(0) }
                    if ($sent) { $deliveredCount++ }
                    $nonce=$null
                } finally {
                    if ($null -ne $stream) { $stream.Dispose() }
                    $sample.Dispose()
                }
            }
        }
        Start-Sleep -Milliseconds 20
    }
    if (-not $result.stop_reason) { $result.stop_reason='finite_duration' }
    $result.challenges_received=$channel.FramesReceived
    $result.coalesced_command_notifications=$channel.CoalescedFrameCommands
    $result.frames_delivered=$deliveredCount
    $result.record_hold_seconds=$clock.Elapsed.TotalSeconds
    $result.phase_timings=$phaseTimings.ToArray()
    Send-Observer ([ordered]@{event='ending';generation=$config.generation;recording=$recording;stop_reason=$result.stop_reason})
    $result.recording_stop_requested_qpc_ticks=Get-QpcTicks
    $result.recording_stop_requested_utc=[DateTimeOffset]::UtcNow.ToString('o')
    Wait-CaptureAction ($capture.StopRecordAsync()) 'Stop record'
    $recording=$false
    $result.recording_stopped_qpc_ticks=Get-QpcTicks
    $result.recording_stopped_utc=[DateTimeOffset]::UtcNow.ToString('o')
    $result.success=$true
    $result.event='complete'
} catch {
    $result.error=$_.Exception.GetBaseException().Message
    $result.error_type=$_.Exception.GetBaseException().GetType().FullName
    $result.error_location=$_.InvocationInfo.PositionMessage
    $result.error_stack=$_.ScriptStackTrace
} finally {
    $cleanupErrors=@()
    if ($channel) { try { $result.rejected_channel_commands=$channel.Rejected; $channel.Dispose() } catch { $cleanupErrors += $_.Exception.GetBaseException().Message } }
    if ($recording) { try { Wait-CaptureAction ($capture.StopRecordAsync()) 'Finally stop record' } catch { $cleanupErrors += $_.Exception.GetBaseException().Message } }
    if ($reading) { try { Wait-CaptureAction ($reader.StopAsync()) 'Stop frame reader' } catch { $cleanupErrors += $_.Exception.GetBaseException().Message } }
    if ($collector) { try { $result.observed_source_frames=$collector.Sequence; $collector.Dispose() } catch { $cleanupErrors += $_.Exception.GetBaseException().Message } }
    if ($reader) { try { $reader.Dispose() } catch { $cleanupErrors += $_.Exception.GetBaseException().Message } }
    if ($capture) { try { $capture.Dispose(); $result.camera_released=$true } catch { $cleanupErrors += $_.Exception.GetBaseException().Message } }
    if ($cleanupErrors.Count -gt 0) { $result.cleanup_errors=$cleanupErrors; $result.success=$false; $result.event='failed' }
}
if (Test-Path -LiteralPath $clipPath) { $result.clip_bytes=(Get-Item -LiteralPath $clipPath).Length; $result.clip_sha256=(Get-FileHash -LiteralPath $clipPath -Algorithm SHA256).Hash }
$result | ConvertTo-Json -Depth 8 -Compress | Set-Content -LiteralPath $metadataPath -Encoding UTF8
Send-Observer $result
if (-not $result.success) { exit 1 }
