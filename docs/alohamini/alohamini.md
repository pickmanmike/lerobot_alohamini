# AlohaMini — Full Workflow

> **Prerequisites:** complete [install.md](install.md) first.  
> **Hardware profiles:** see [profiles.md](profiles.md).

Dual-arm setup — PC (client) + Raspberry Pi (host) on the same LAN.

---

## 1. System Architecture

```
┌──────────────────────────────┐        LAN        ┌──────────────────────────────────┐
│         PC (Client)          │ ◄───────────────► │      Raspberry Pi (Host)         │
│                              │                   │                                  │
│  • Leader arms (USB)         │                   │  • Follower arms (USB)           │
│  • calibrate_bi.py           │                   │  • Base wheels + lift (USB)      │
│  • teleoperate_bi.py         │                   │  • Cameras (USB)                 │
│  • record_bi.py              │                   │  • alohamini_host.py             │
│  • Training / Evaluation     │                   │                                  │
└──────────────────────────────┘                   └──────────────────────────────────┘
```

Both machines must be on the same LAN with the environment required for their host or client role.

---

## 2. Port Configuration

Plug in one device at a time, then run:

```bash
lerobot-find-port
# or check directly:
ls /dev/ttyACM*
```

**Follower arms** — edit `src/lerobot/robots/alohamini/config_alohamini.py` on the Pi:

```python
@dataclass
class AlohaMiniConfig(RobotConfig):
    left_port:  str = "/dev/ttyACM0"   # replace with your left-bus port
    right_port: str = "/dev/ttyACM1"   # replace with your right-bus port
```

**Leader arms** — on Linux, the PC scripts default to the stable device aliases below:

```python
left_arm_config  = SOLeaderConfig(port="/dev/am_arm_leader_left", ...)
right_arm_config = SOLeaderConfig(port="/dev/am_arm_leader_right", ...)
```

Set up the corresponding udev aliases as described in [commands.md](commands.md#persistent-arm-ports). If you use different paths, pass the same `--teleop.left_port` and `--teleop.right_port` values to `calibrate_bi.py`, `teleoperate_bi.py`, and `record_bi.py`.

> Port numbers can change after reconnecting or rebooting. If you purchased a complete AlohaMini, the Pi's follower ports are already fixed via udev rules — no action needed.

### Native Windows leader client (Aloha Mini 1)

Native Windows support is for the PC/client role shown above, not the Raspberry Pi hardware-host role. In PowerShell, create a targeted Python 3.12 environment for leader calibration and network teleoperation:

```powershell
cd C:\Users\pickm\lerobot_alohamini_client
uv venv --python 3.12
uv pip install -e ".[hardware,feetech,pyzmq-dep]"
```

Do not install `.[all]` for this workflow. The initial client environment does not need kinematics, `placo`, `eiquadprog`, `cmeel-boost`, simulation, training policies, Jupyter, phone control, or visualization. Dataset recording can be enabled later with its targeted dataset dependencies.

Find the two leader ports with one USB controller connected at a time, recording which COM port belongs to the left and right leader:

```powershell
.\.venv\Scripts\lerobot-find-port.exe
```

Windows requires both ports explicitly. The `COM7`/`COM8` examples immediately below are historical and are not a physical-identity assertion. Packet 2N-R5 below is the sole future corrected-port procedure: physical/logical left is `COM8` and physical/logical right is `COM7`. Do not run any calibration command here without its separately required physical authorization.

```powershell
.\.venv\Scripts\python.exe `
  .\examples\alohamini\calibrate_bi.py `
  --teleop.left_port COM7 `
  --teleop.right_port COM8 `
  --teleop.id so101_leader_bi `
  --teleop.arm_profile so-arm-5dof
```

Aloha Mini leader and follower arm actions use normalized positions by default: body joints use `-100..100` and grippers use `0..100`. Existing leader calibration files remain reusable when their physical controller ownership is unchanged because they store raw homing, range, and drive information rather than the runtime normalization mode. Packet 2N-R5 is a separate exception: its proven cross-mapping requires a separately authorized, corrected-port full recalibration rather than reuse, a port-only swap, or a JSON-content swap.

### Aloha Mini 1 startup synchronization safety

`strict` remains the default startup mode and never automatically positions followers. `sync` is an Aloha Mini 1-only linear interpolation in normalized joint space: it makes an explicit, slow move from newly measured follower positions to one frozen, validated leader pose. It is not collision-aware and does not check self-collision, the workspace, payloads, cables, or nearby people.

Begin every stage with empty grippers, a clear motion envelope, the passive leaders held in moderate poses, the tested follower supported, and the follower motor-power disconnect immediately accessible. Stop at the first unexpected direction, speed, sound, current, contact, software error, or communication failure. Synchronization does not automatically reverse or return an arm after a refusal, and the Pi may continue holding the last arm target.

Leader motors require their 7.4 V low-voltage supply and must never receive the 12 V follower supply. Physical commissioning is not part of software validation and requires separate authorization; use the stages below only as separately authorized, bounded physical checks. Keep the Pi host's `max_relative_target` as an independently selected secondary limit; this Windows client does not configure it.

Before a synchronization move, the client prints the measured start and frozen target and asks the operator to type exactly `SYNC`. Enter alone, lowercase text, or added whitespace does not authorize motion. After confirmation, the client takes fresh follower and leader samples and prints those final endpoints before sending frame zero. Every synchronization frame holds base and lift velocity at zero and changes each selected normalized arm position by at most `STARTUP_SYNC_MAX_STEP = 0.75`. This client frame cap is independent of Pi `max_relative_target`; if it needs more frames than the requested duration, the move takes longer. Actual arm-bearing synchronization sends remain at least `1 / --fps` seconds apart, so an overrun lengthens the move instead of triggering catch-up sends. Every leader sample is validated, and exceeding `STARTUP_SYNC_LEADER_DRIFT = 2.0` aborts selected-side motion.

Command and observation traffic use separate sockets, so the first sequence-fresh response after the final command can still have been generated before that command was processed. The client therefore checks up to the configured observation request window plus one sequence-fresh samples. Synchronization succeeds only when a checked follower sample satisfies `--max_start_mismatch`; otherwise it refuses without widening the threshold. The threshold is final startup convergence verification only: it does not limit how far apart valid calibrated poses may be when a synchronization plan is first proposed, and it is not used continuously at runtime. The historical S1--S5 commands below retain `5.0`. The blocked Packet 2M S6 command uses `6.0`: it is `0.708` above the worst measured completed-settle negative-direction residual (`5.292`) while still refusing a completely unmoving requested 10-unit joint move. That moving session is not authorized until Packet 2N-R5 corrected-port recalibration and the two strict no-robot verification runs return `MAPPING_RESULT=CORRECT` and are reviewed.

The client makes the operator phase explicit, in this order:

1. `HOLD LEADERS STILL — STARTUP SYNCHRONIZATION IN PROGRESS`
2. `SYNCHRONIZATION COMPLETE`
3. `PRESS ENTER TO ENABLE LIVE TELEOPERATION`
4. `TELEOPERATION ACTIVE — LEADER MOVEMENT IS NOW ALLOWED`

The final message appears only after the post-pause fresh-sample alignment gate passes and immediately before the first ordinary arm action is sent.

#### Historical AM1 single-joint diagnostic and trace evidence

The bounded diagnostic is historical evidence, not the next commissioning action. It constructs no leader, keyboard, camera, or visualization device; it takes a fresh follower pose, holds nonselected joints at the final fresh measured pose, keeps base and lift commands explicitly zero, and uses an exact uppercase `MOVE` gate. With a positive settle duration, historical `PASS` meant two post-window, sequence-fresh in-tolerance samples; it was not by itself a host acknowledgement or persistence proof.

The completed traces used effective `max_relative_target=10.0` and a bounded final-target settle of `--settle_s 5.0`; `PASS` required two consecutive post-window, sequence-fresh in-tolerance samples. The prior negative-direction run was only partially physically proven: it measured `S=21.775`, `T=11.775`, movement `-4.708`, and final error `-5.292`, ending `INCOMPLETE`. The fresh positive-direction repeat measured `S=16.885`, `T=26.885`, movement `+9.507`, and final error `+0.493`, ending `PASS`/`0`. In that positive trace, the requested, relative-limiter, and final targets matched; the `Goal_Position` readback matched within quantization; the SDK transmit completed; and `Torque_Enable`, `Lock`, and `Operating_Mode` read back `1`/`1`/`0`. The observed current maxima were `13 mA` versus `221 mA`.

Together, that is directional/load-dependent downstream elbow behavior: the static non-Phase configuration and normal command transport passed, but the exact physical cause remains unresolved. Do not change any motor setting on the strength of this result. Packet 2N later exposed a separate physical leader-identity ambiguity before live teleoperation began; resolve that no-robot boundary before any further follower motion.

#### Historical trace field semantics

The completed trace is retained only to explain the evidence above. A matching `readbacks.Goal_Position.normalized` was an immediate post-write register read at that boundary; it was not a servo acknowledgement and did not prove persistence beyond that read.


**Historical JSON event and field contract.** The completed Pi trace emitted newline-delimited JSON. Every event had an epoch `timestamp_ns` (nanoseconds), `motor: "arm_left_elbow_flex"`, and an event name. Its startup record was:

```json
{"event":"am1_left_elbow_trace_startup","timestamp_ns":0,"effective_max_relative_target":10.0,"motor":"arm_left_elbow_flex"}
```

Each historical traced action boundary used the following field names (numeric values are examples of types, not expected measurements):

```json
{"event":"am1_left_elbow_action_boundary","timestamp_ns":0,"motor":"arm_left_elbow_flex","requested_normalized_target":0.0,"relative_limiter_present_normalized":0.0,"relative_limiter_target_normalized":0.0,"final_left_bus_target_normalized":0.0,"goal_position_sync_write":{"attempted":true,"sdk_transmit":"completed","servo_acknowledgement":"sync-write supplies no servo acknowledgement"},"readbacks":{"Goal_Position":{"normalized":0.0},"Present_Position":{"raw":0},"Present_Current":{"raw":0,"ma":0.0},"Torque_Enable":{"raw":0},"Lock":{"raw":0},"Operating_Mode":{"raw":0}}}
```

Interpret the action fields as follows:

- `requested_normalized_target` is the requested left-elbow target from the Windows action before the Pi relative limiter.
- `relative_limiter_present_normalized` is the present value sampled for the limiter; `relative_limiter_target_normalized` is the target after `max_relative_target` is applied.
- `final_left_bus_target_normalized` is the target remaining after the later current-based joint/gripper limiting and immediately before the left `Goal_Position` sync-write. It is not an observed servo position.
- `goal_position_sync_write.attempted` and `sdk_transmit` (`completed` or `failed`) describe the SDK sync-write attempt. The literal `servo_acknowledgement` value says that this action channel supplies no servo acknowledgement. On failure, the object also carries `error`; later-stage failures may add `action_write_failure`, `right_goal_position_sync_write`, `body_goal_velocity_sync_write`, and a `readbacks` status explaining why reads were not attempted.
- Successful post-write reads are exactly `Goal_Position.normalized`, `Present_Position.raw`, `Present_Current.raw` plus `Present_Current.ma` (raw value multiplied by 6.5), `Torque_Enable.raw`, `Lock.raw`, and `Operating_Mode.raw`. A successful matching `readbacks.Goal_Position.normalized` proves the immediate post-write register read at that boundary, but it is not a servo acknowledgement and does not prove persistence beyond that read. A `diagnostic_reads` error object may replace these on a read failure; the fields have no independent timestamp, so use the enclosing event's `timestamp_ns`.

The historical trace's physical stop boundary was immediate disconnect/power removal for unexpected direction, speed, sound, current, contact, cable tension, communication failure, or a missing/contradictory field. Those diagnostic-specific conditions are recorded as evidence only; no repeat trace is authorized.

For historical trace-log archive retrieval only, fetch the newest saved Pi log from Windows with:

```powershell
.\tools\fetch_am1_pi_log.ps1
```

The helper defaults to `pickmanmike@192.168.1.134`, prints the exact remote and local paths, and saves into `$HOME\AlohaMini1Logs`. To retrieve a known log instead of discovering the newest one, paste the exact path printed as `HOST_LOG` by the Pi startup command. Do not pass a timestamp template or the Windows log filename:

```powershell
$piHostLog = Read-Host 'Paste the exact Pi HOST_LOG path'
.\tools\fetch_am1_pi_log.ps1 -RemotePath $piHostLog
```

For an offline command-line check, collect native output as one string before matching it:

```powershell
$diagnosticHelp = (& .\.venv\Scripts\python.exe .\examples\alohamini\diagnose_am1_joint.py --help 2>&1 | Out-String)
if ($diagnosticHelp -notmatch '--settle_s') { throw 'diagnostic --help did not contain --settle_s' }
```

S1--S5 are historical commissioning commands and retain their recorded `5.0` tolerance. They do not authorize another motion. Packet 2N-R3 below is completed evidence, and Packet 2N-R5 is the only described future correction after separate physical authorization. Packet 2M S6 remains blocked until Packet 2N-R5 passes corrected-port recalibration and the strict no-robot verifier returns `MAPPING_RESULT=CORRECT` for both marked logs, followed by review.

#### S1 — left-only synchronization and exit

```powershell
.\.venv\Scripts\python.exe `
  .\examples\alohamini\teleoperate_bi.py `
  --robot.remote_ip 192.168.1.134 `
  --robot.robot_model alohamini1 `
  --teleop.left_port COM7 `
  --teleop.right_port COM8 `
  --teleop.id so101_leader_bi `
  --teleop.arm_profile so-arm-5dof `
  --startup_mode sync `
  --startup_sync_side left `
  --startup_sync_duration_s 12.0 `
  --startup_sync_only `
  --max_start_mismatch 5.0 `
  --fps 5 `
  --no_keyboard `
  --no_rerun
```

S1 sends only left-arm position keys plus zero base/lift commands, verifies the result, and exits without entering ordinary teleoperation.

#### S2 — right-only synchronization and exit

```powershell
.\.venv\Scripts\python.exe `
  .\examples\alohamini\teleoperate_bi.py `
  --robot.remote_ip 192.168.1.134 `
  --robot.robot_model alohamini1 `
  --teleop.left_port COM7 `
  --teleop.right_port COM8 `
  --teleop.id so101_leader_bi `
  --teleop.arm_profile so-arm-5dof `
  --startup_mode sync `
  --startup_sync_side right `
  --startup_sync_duration_s 12.0 `
  --startup_sync_only `
  --max_start_mismatch 5.0 `
  --fps 5 `
  --no_keyboard `
  --no_rerun
```

S2 applies the same bounded procedure to the right follower only.

#### S3 — strict no-motion alignment diagnostic

Manually place and support both followers in poses matching the passive leaders, then run the existing strict diagnostic:

```powershell
.\.venv\Scripts\python.exe `
  .\examples\alohamini\teleoperate_bi.py `
  --robot.remote_ip 192.168.1.134 `
  --robot.robot_model alohamini1 `
  --teleop.left_port COM7 `
  --teleop.right_port COM8 `
  --teleop.id so101_leader_bi `
  --teleop.arm_profile so-arm-5dof `
  --startup_mode strict `
  --check_alignment_only `
  --max_start_mismatch 5.0 `
  --no_keyboard `
  --no_rerun
```

S3 prints every follower/leader joint difference and sends no arm-position action. It succeeds only when both samples are finite, normalized, complete, fresh, and within the threshold. A base/lift zero command may still be sent during connection and cleanup.

#### S4 — both-side synchronization and exit

```powershell
.\.venv\Scripts\python.exe `
  .\examples\alohamini\teleoperate_bi.py `
  --robot.remote_ip 192.168.1.134 `
  --robot.robot_model alohamini1 `
  --teleop.left_port COM7 `
  --teleop.right_port COM8 `
  --teleop.id so101_leader_bi `
  --teleop.arm_profile so-arm-5dof `
  --startup_mode sync `
  --startup_sync_side both `
  --startup_sync_duration_s 15.0 `
  --startup_sync_only `
  --max_start_mismatch 5.0 `
  --fps 5 `
  --no_keyboard `
  --no_rerun
```

#### S5 — strict bounded gripper-only operator procedure

This is an operator procedure, not a gripper-only payload mode. The client still validates and forwards all twelve arm-position keys; during this check the operator moves only the two grippers.

```powershell
.\.venv\Scripts\python.exe `
  .\examples\alohamini\teleoperate_bi.py `
  --robot.remote_ip 192.168.1.134 `
  --robot.robot_model alohamini1 `
  --teleop.left_port COM7 `
  --teleop.right_port COM8 `
  --teleop.id so101_leader_bi `
  --teleop.arm_profile so-arm-5dof `
  --startup_mode strict `
  --max_start_mismatch 5.0 `
  --fps 5 `
  --duration_s 30 `
  --start_paused `
  --no_keyboard `
  --no_rerun
```

After Enter, the client obtains fresh follower and leader samples, revalidates and compares them, and uses the final validated leader sample as the first forwarded arm action with explicit zero base/lift commands.

#### Packet 2N-R3 — completed physical leader identity evidence (historical)

The marked Packet 2N physical-only logs are complete evidence, not an authorization to repeat the historical runs. Each log contains exactly 60 complete samples with the exact twelve arm-position keys, no ZMQ text or calibration flow, normal cleanup, and `CLIENT_EXIT_CODE=0`. The strict verifier reproduced `MAPPING_RESULT=REVERSED`: `PHYSICAL_LEFT_ONLY` changed logical `right` (`LeftGripperRange=0.00`, `RightGripperRange=89.61`, `LeftFamilyMaxRange=0.00`, `RightFamilyMaxRange=89.61`), while `PHYSICAL_RIGHT_ONLY` changed logical `left` (`LeftGripperRange=53.32`, `RightGripperRange=0.29`, `LeftFamilyMaxRange=53.32`, `RightFamilyMaxRange=0.98`). Thus physical left was on `COM8` and produced logical right; physical right was on `COM7` and produced logical left.

Packet 2N never entered live teleoperation and correctly returned status `2`. Its logical left shoulder-lift started at `20.899`, targeted `-96.598`, and was observed at `-23.397` during final verification, leaving a `73.201` residual; all other logical joints were within `2.855`. The actual host/client boundary is now available: correlated clamp frames are 56–157, with final client request `-96.59781287970839`, final host limited target `-33.39716902581182`, and client-observed value `-23.39716902581182`; the effective limit was `10.0`. There is no directly logged final bus write/readback, and the earlier `2015 mA` event is uncorrelated. Because the slow shoulder test was cross-mapped, it cannot yet justify startup-convergence work or any motor-setting change.

The software mapping itself is deterministic: `--teleop.left_port` owns logical `left_*` and therefore `arm_left_*`; `--teleop.right_port` owns logical `right_*` and therefore `arm_right_*`. With `--teleop.id so101_leader_bi`, the child calibration identities are `so101_leader_bi_left` and `so101_leader_bi_right`. With `HF_LEROBOT_CALIBRATION`, `HF_LEROBOT_HOME`, and `HF_HOME` unset during the Packet 2N-R3 audit, their resolved default files were:

```text
C:\Users\pickm\.cache\huggingface\lerobot\calibration\teleoperators\so_leader\so101_leader_bi_left.json
C:\Users\pickm\.cache\huggingface\lerobot\calibration\teleoperators\so_leader\so101_leader_bi_right.json
```

Those files contain different per-device homing offsets and ranges. Calibration ownership follows the logical child ID, not the COM port. The current ownership is consequently reversed: `so101_leader_bi_left.json` belongs to the physical right controller on `COM7`, and `so101_leader_bi_right.json` belongs to the physical left controller on `COM8`. A port-only swap is therefore unsafe: it would attach each physical controller to the wrong child calibration. Do not rename files, swap JSON contents, or add a runtime swap layer.

The following is the historical evidence procedure that produced the marked logs. Do not execute it again. It used the existing client only as a normalized leader reader: it constructed no robot client, sent no ZMQ request, and started no keyboard or visualization. Connecting an SO leader still performs existing leader-bus connection and configuration writes; it was not read-only COM access. No Pi command was part of that completed gate.

```powershell
$ErrorActionPreference = 'Stop'
if ((git branch --show-current) -ne 'fix/am1-elbow-commissioning') { throw 'wrong Windows branch' }
if (git status --porcelain) { throw 'Windows worktree is not clean' }
git merge-base --is-ancestor f06dc227682f37f263e09a2d92a77dbbadbc9c2e HEAD
if ($LASTEXITCODE -ne 0) { throw 'Packet 2N-R3 Windows baseline is not an ancestor' }
$env:PYTHONDONTWRITEBYTECODE = '1'
$packet2nCalibrationRoot = (& .\.venv\Scripts\python.exe -c "from lerobot.utils.constants import HF_LEROBOT_CALIBRATION; print(HF_LEROBOT_CALIBRATION)" | Out-String).Trim()
$packet2nLeftCalibration = Join-Path $packet2nCalibrationRoot 'teleoperators\so_leader\so101_leader_bi_left.json'
$packet2nRightCalibration = Join-Path $packet2nCalibrationRoot 'teleoperators\so_leader\so101_leader_bi_right.json'
if (-not (Test-Path -LiteralPath $packet2nLeftCalibration -PathType Leaf)) { throw "missing left calibration: $packet2nLeftCalibration" }
if (-not (Test-Path -LiteralPath $packet2nRightCalibration -PathType Leaf)) { throw "missing right calibration: $packet2nRightCalibration" }
"LEFT_CALIBRATION=$packet2nLeftCalibration"
"RIGHT_CALIBRATION=$packet2nRightCalibration"
```

The following command transcript is archived historical evidence. It was executed under a prior separate leader-only physical authorization; it must not be repeated. The original controllers used `COM7` and `COM8`, each leader had its designated 7.4 V supply, and follower 12 V power remained off.

**Historical physical-left-only run (completed).** The filename and first log line bind this archived evidence run to the physical left leader:

```powershell
$ErrorActionPreference = 'Stop'
$packet2nMapDir = 'C:\Users\pickm\AlohaMini1Logs'
New-Item -ItemType Directory -Force -Path $packet2nMapDir | Out-Null
$packet2nMapTimestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$packet2nPhysicalLeftLog = Join-Path $packet2nMapDir "packet2n-physical-left-only-$packet2nMapTimestamp.log"
'MAP_RUN=PHYSICAL_LEFT_ONLY' | Tee-Object -FilePath $packet2nPhysicalLeftLog
& .\.venv\Scripts\python.exe `
  .\examples\alohamini\teleoperate_bi.py `
  --no_robot `
  --robot.robot_model alohamini1 `
  --teleop.left_port COM7 `
  --teleop.right_port COM8 `
  --teleop.id so101_leader_bi `
  --teleop.arm_profile so-arm-5dof `
  --duration_s 12 `
  --fps 5 `
  --start_paused `
  --no_keyboard `
  --no_rerun 2>&1 | Tee-Object -FilePath $packet2nPhysicalLeftLog -Append
$packet2nPhysicalLeftExitCode = $LASTEXITCODE
"CLIENT_EXIT_CODE=$packet2nPhysicalLeftExitCode" | Tee-Object -FilePath $packet2nPhysicalLeftLog -Append
if ($packet2nPhysicalLeftExitCode -ne 0) { throw "physical-left map failed with $packet2nPhysicalLeftExitCode" }
```

The first historical client then disconnected both leader buses; the leaders were returned to safe moderate poses without exchanging USB connections or supplies.

**Historical physical-right-only run (completed).** This archived transcript is retained only for the verifier and evidence boundary:

```powershell
$ErrorActionPreference = 'Stop'
$packet2nMapDir = 'C:\Users\pickm\AlohaMini1Logs'
New-Item -ItemType Directory -Force -Path $packet2nMapDir | Out-Null
$packet2nMapTimestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$packet2nPhysicalRightLog = Join-Path $packet2nMapDir "packet2n-physical-right-only-$packet2nMapTimestamp.log"
'MAP_RUN=PHYSICAL_RIGHT_ONLY' | Tee-Object -FilePath $packet2nPhysicalRightLog
& .\.venv\Scripts\python.exe `
  .\examples\alohamini\teleoperate_bi.py `
  --no_robot `
  --robot.robot_model alohamini1 `
  --teleop.left_port COM7 `
  --teleop.right_port COM8 `
  --teleop.id so101_leader_bi `
  --teleop.arm_profile so-arm-5dof `
  --duration_s 12 `
  --fps 5 `
  --start_paused `
  --no_keyboard `
  --no_rerun 2>&1 | Tee-Object -FilePath $packet2nPhysicalRightLog -Append
$packet2nPhysicalRightExitCode = $LASTEXITCODE
"CLIENT_EXIT_CODE=$packet2nPhysicalRightExitCode" | Tee-Object -FilePath $packet2nPhysicalRightLog -Append
if ($packet2nPhysicalRightExitCode -ne 0) { throw "physical-right map failed with $packet2nPhysicalRightExitCode" }
```

The strict verifier preserves the historical two-path use while adding a fail-closed Packet 2N-R5 evidence mode. Both modes require the marker, exactly 60 no-robot action records, the no-robot notice, absence of runtime `ZMQ` or calibration text, normal cleanup, exit `0`, the exact twelve arm keys, a moved gripper span of at least 20 normalized units, and less than `2.0` variation across the entire opposite logical family. The broad quoted-key scan deliberately rejects missing, duplicated, or unexpected `arm_*.pos` keys such as `arm_center_*.pos`; legitimate non-arm zero base/lift keys are not classified as unexpected arm data. Packet 2N-R5 mode additionally revalidates the persisted calibration evidence, transcript, and current calibration hashes and requires every bound log-metadata line exactly once:

```powershell
$ErrorActionPreference = 'Stop'
function Get-Packet2nLeaderMapSummary {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [ValidateSet('PHYSICAL_LEFT_ONLY', 'PHYSICAL_RIGHT_ONLY')] [string] $ExpectedMarker,
        [switch] $RequirePacket2nR5Evidence,
        [string] $Packet2nR5EvidencePath,
        [string] $Packet2nR5EvidenceSha256
    )

    $lines = @(Get-Content -LiteralPath $Path)
    if ($lines.Count -lt 3 -or $lines[0].Trim() -ne "MAP_RUN=$ExpectedMarker") { throw "invalid marker in $Path" }
    if ($lines[-1].Trim() -ne 'CLIENT_EXIT_CODE=0') { throw "missing successful exit in $Path" }
    if (@($lines | Where-Object { $_ -eq 'NO_ROBOT: robot client construction and connection skipped.' }).Count -ne 1) { throw "missing or duplicate NO_ROBOT proof in $Path" }
    if (-not ($lines | Where-Object { $_ -like 'Shutdown complete:*' })) { throw "missing cleanup proof in $Path" }
    if ($lines -match 'ZMQ') { throw "unexpected ZMQ text in $Path" }

    $approvedMetadataLines = @()
    if ($RequirePacket2nR5Evidence) {
        if (-not $Packet2nR5EvidencePath -or -not $Packet2nR5EvidenceSha256) { throw 'Packet 2N-R5 verification requires evidence path and SHA-256' }
        $packet2nR5Context = Assert-Packet2nR5PersistedEvidence -EvidencePath $Packet2nR5EvidencePath -EvidenceSha256 $Packet2nR5EvidenceSha256
        $approvedMetadataLines = Get-Packet2nR5MapMetadataLines -Context $packet2nR5Context
        Assert-Packet2nR5MapMetadata -Lines $lines -ExpectedLines $approvedMetadataLines -Path $Path
    } elseif ($Packet2nR5EvidencePath -or $Packet2nR5EvidenceSha256) {
        throw 'Packet 2N-R5 evidence parameters require -RequirePacket2nR5Evidence'
    }
    $runtimeLines = @($lines | Where-Object { $_ -notin $approvedMetadataLines })
    if ($runtimeLines -match '(?i)calibrat') { throw "runtime calibration text requires refusal and review: $Path" }

    $expectedKeys = @(
        'arm_left_shoulder_pan.pos',
        'arm_left_shoulder_lift.pos',
        'arm_left_elbow_flex.pos',
        'arm_left_wrist_flex.pos',
        'arm_left_wrist_roll.pos',
        'arm_left_gripper.pos',
        'arm_right_shoulder_pan.pos',
        'arm_right_shoulder_lift.pos',
        'arm_right_elbow_flex.pos',
        'arm_right_wrist_flex.pos',
        'arm_right_wrist_roll.pos',
        'arm_right_gripper.pos'
    )
    $series = @{}
    $completeRecordCount = 0
    $number = '-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
    $quotedArmKeyPattern = "'(?<key>arm_[^']*\.pos)'\s*:"
    foreach ($line in $lines) {
        if (-not $line.StartsWith('[NO_ROBOT] action ->')) { continue }
        $quotedArmMatches = @([regex]::Matches($line, $quotedArmKeyPattern))
        $recordKeys = @($quotedArmMatches | ForEach-Object { $_.Groups['key'].Value })
        $missingRecordKeys = @($expectedKeys | Where-Object { $_ -notin $recordKeys })
        $unexpectedRecordKeys = @($recordKeys | Where-Object { $_ -notin $expectedKeys })
        $duplicateRecordKeys = @($recordKeys | Group-Object | Where-Object { $_.Count -ne 1 })
        if ($quotedArmMatches.Count -ne 12 -or $missingRecordKeys.Count -ne 0 -or $unexpectedRecordKeys.Count -ne 0 -or $duplicateRecordKeys.Count -ne 0) {
            throw "missing, duplicate, or unexpected quoted arm action key in $Path"
        }
        $completeRecordCount++
        foreach ($key in $expectedKeys) {
            $escapedKey = [regex]::Escape($key)
            $valueMatches = @([regex]::Matches($line, "'$escapedKey'\s*:\s*(?<value>$number)(?=\s*[,}])"))
            if ($valueMatches.Count -ne 1) { throw "missing, duplicate, or nonnumeric value for $key in $Path" }
            if (-not $series.ContainsKey($key)) { $series[$key] = @() }
            $series[$key] += [double]::Parse($valueMatches[0].Groups['value'].Value, [Globalization.CultureInfo]::InvariantCulture)
        }
    }
    if ($completeRecordCount -ne 60) { throw "expected exactly 60 complete arm action records in $Path; found $completeRecordCount" }
    $missingKeys = @($expectedKeys | Where-Object { -not $series.ContainsKey($_) })
    $unexpectedKeys = @($series.Keys | Where-Object { $_ -notin $expectedKeys })
    if ($missingKeys.Count -ne 0 -or $unexpectedKeys.Count -ne 0) {
        throw "arm action key set mismatch in $Path"
    }

    $ranges = @{}
    foreach ($key in $series.Keys) {
        $measure = $series[$key] | Measure-Object -Minimum -Maximum
        $ranges[$key] = [double] $measure.Maximum - [double] $measure.Minimum
    }
    $leftFamilyMax = [double] (($ranges.GetEnumerator() | Where-Object { $_.Key -like 'arm_left_*' } | Measure-Object -Property Value -Maximum).Maximum)
    $rightFamilyMax = [double] (($ranges.GetEnumerator() | Where-Object { $_.Key -like 'arm_right_*' } | Measure-Object -Property Value -Maximum).Maximum)
    $leftGripper = [double] $ranges['arm_left_gripper.pos']
    $rightGripper = [double] $ranges['arm_right_gripper.pos']
    $logicalSide = if ($leftGripper -ge 20.0 -and $rightFamilyMax -lt 2.0) {
        'left'
    } elseif ($rightGripper -ge 20.0 -and $leftFamilyMax -lt 2.0) {
        'right'
    } else {
        'ambiguous'
    }

    [pscustomobject]@{
        PhysicalRun = $ExpectedMarker
        LogicalSide = $logicalSide
        LeftGripperRange = $leftGripper
        RightGripperRange = $rightGripper
        LeftFamilyMaxRange = $leftFamilyMax
        RightFamilyMaxRange = $rightFamilyMax
        SampleCount = $completeRecordCount
    }
}
```

For the historical Packet 2N-R3 logs only, define the function above and then run this prompt-based invocation. Do not use this historical invocation for Packet 2N-R5:

```powershell

$packet2nPhysicalLeftLog = Read-Host 'Paste the exact PHYSICAL_LEFT_ONLY log path'
$packet2nPhysicalRightLog = Read-Host 'Paste the exact PHYSICAL_RIGHT_ONLY log path'
$packet2nLeftSummary = Get-Packet2nLeaderMapSummary -Path $packet2nPhysicalLeftLog -ExpectedMarker PHYSICAL_LEFT_ONLY
$packet2nRightSummary = Get-Packet2nLeaderMapSummary -Path $packet2nPhysicalRightLog -ExpectedMarker PHYSICAL_RIGHT_ONLY
$packet2nLeftSummary, $packet2nRightSummary | Format-Table -AutoSize

$packet2nMappingResult = if ($packet2nLeftSummary.LogicalSide -eq 'left' -and $packet2nRightSummary.LogicalSide -eq 'right') {
    'MAPPING_RESULT=CORRECT'
} elseif ($packet2nLeftSummary.LogicalSide -eq 'right' -and $packet2nRightSummary.LogicalSide -eq 'left') {
    'MAPPING_RESULT=REVERSED'
} else {
    'MAPPING_RESULT=AMBIGUOUS'
}
$packet2nMappingResult
if ($packet2nMappingResult -ne 'MAPPING_RESULT=CORRECT') {
    throw "Packet 2N mapping verification failed: $packet2nMappingResult"
}
```

Classify the paired result only as follows:

- Physical left changes only `arm_left_*`, and physical right changes only `arm_right_*`: physical mapping is correct. Outcome B may then design and implement the bounded startup final-target convergence phase; this document does not implement or authorize it.
- Physical left changes only `arm_right_*`, and physical right changes only `arm_left_*`: physical mapping is reversed. Outcome A must correct the port and calibration ownership together after a separate reviewed backup/correction packet; do not try a port-only swap.
- Either run changes both families, neither family, misses the 20-unit/`2.0` separation above, or has an absent marker, calibration prompt, nonzero exit, connection error, or unclear movement: mapping remains unresolved under Outcome C.

Stop either run immediately and remove leader power for unexpected powered leader motion, resistance, sound, heat, cable strain, communication failure, loss of the clear stop path, any follower power or movement, or any evidence that a robot/ZMQ connection was constructed. After the second run, disconnect both leader buses, switch off both 7.4 V supplies, and stop for review. Do not start Packet 2M S6.

#### Packet 2N-R5 — approved later corrected-port recalibration and no-robot verification (not executed here)

This Packet 2N-R5 documentation task made no COM, leader, follower, Pi, ZMQ, network, or calibration connection. It made a verified **copy-only** offline backup while hardware remained disconnected:

```text
Backup directory: C:\Users\pickm\AlohaMini1Backups\packet2n-r5-20260822-121722-7941f445-9587-4345-8e2f-edd54ca750f6
Manifest: C:\Users\pickm\AlohaMini1Backups\packet2n-r5-20260822-121722-7941f445-9587-4345-8e2f-edd54ca750f6\manifest.json
Manifest SHA-256: B90DF72155C60996B4E2704E4A44ED1895BBAEA0C0A332DC24674EC3FA399B8A
```

| Original file | Bytes | SHA-256 | Source `LastWriteTimeUtc` |
| --- | ---: | --- | --- |
| `so101_leader_bi_left.json` | 960 | `6F5D6126E84398D0621A26E74E4DF6678EBA7C14C62D343020610B4D5D8B3D8C` | `2026-08-15T05:18:25.9699568Z` |
| `so101_leader_bi_right.json` | 961 | `65A301F20FC7DC96BD7FB5982E3670BF1A01F535953D7A253AB8D33A03646F11` | `2026-08-15T05:19:53.2654429Z` |

The manifest records the exact original and backup paths, hashes, byte counts, and source timestamps. Both backup hashes matched their source hashes, and both original paths and hashes were rechecked unchanged after copying. This backup does not authorize restoring, moving, deleting, renaming, editing, or swapping either original; any rollback requires a separate reviewed decision.

The only approved later correction, after separate physical authorization, is coordinated **full recalibration** with logical/physical left on `COM8`, logical/physical right on `COM7`, ID `so101_leader_bi`, and profile `so-arm-5dof`. Do not do a port-only swap, JSON-content swap, or runtime swap layer. Begin with the Pi motor host stopped, follower/body 12 V power off, both leader supplies off, and both leader USB controllers disconnected. The exact next Windows procedure is the calibration block and two no-robot map blocks below. The exact next Pi command is **none**.

**Future corrected-port full recalibration — fail fast.** In one PowerShell session, first execute only the strict-verifier **function-definition** block above (not its historical prompt-based invocation), then execute the calibration block and both map blocks below in order. Do this only after a separate physical authorization. The common guard refuses before connection unless that verifier function is loaded, pins the behavior baseline `cae57b59db1d9156be568aa4b216fc90701aa741`, robustly checks every Git command's exit status and stderr, requires every tracked path except this documentation file to be identical to that baseline, rejects Python/Hugging Face path overrides, pins exact source imports under this checkout, and validates the immutable manifest and backups. `--force_fresh_calibration` eliminates the existing-calibration prompt path; do not accept, type `c` at, or otherwise use such a prompt.

```powershell
$ErrorActionPreference = 'Stop'

function Get-Packet2nR5Definition {
    $packet2nCalibrationRoot = 'C:\Users\pickm\.cache\huggingface\lerobot\calibration'
    $packet2nBackupDirectory = 'C:\Users\pickm\AlohaMini1Backups\packet2n-r5-20260822-121722-7941f445-9587-4345-8e2f-edd54ca750f6'
    [pscustomobject]@{
        Packet = '2N-R5'
        BehaviorSha = 'cae57b59db1d9156be568aa4b216fc90701aa741'
        Branch = 'fix/am1-elbow-commissioning'
        CalibrationRoot = $packet2nCalibrationRoot
        BackupDirectory = $packet2nBackupDirectory
        ManifestPath = Join-Path $packet2nBackupDirectory 'manifest.json'
        ManifestSha256 = 'B90DF72155C60996B4E2704E4A44ED1895BBAEA0C0A332DC24674EC3FA399B8A'
        LogsDirectory = 'C:\Users\pickm\AlohaMini1Logs'
        Files = @(
            [pscustomobject]@{
                Name = 'so101_leader_bi_left.json'
                Side = 'left'
                SourcePath = Join-Path $packet2nCalibrationRoot 'teleoperators\so_leader\so101_leader_bi_left.json'
                BackupPath = Join-Path $packet2nBackupDirectory 'so101_leader_bi_left.json'
                Bytes = [int64]960
                Sha256 = '6F5D6126E84398D0621A26E74E4DF6678EBA7C14C62D343020610B4D5D8B3D8C'
                SourceLastWriteTimeUtc = '2026-08-15T05:18:25.9699568Z'
            },
            [pscustomobject]@{
                Name = 'so101_leader_bi_right.json'
                Side = 'right'
                SourcePath = Join-Path $packet2nCalibrationRoot 'teleoperators\so_leader\so101_leader_bi_right.json'
                BackupPath = Join-Path $packet2nBackupDirectory 'so101_leader_bi_right.json'
                Bytes = [int64]961
                Sha256 = '65A301F20FC7DC96BD7FB5982E3670BF1A01F535953D7A253AB8D33A03646F11'
                SourceLastWriteTimeUtc = '2026-08-15T05:19:53.2654429Z'
            }
        )
        CalibrationArguments = @(
            '.\examples\alohamini\calibrate_bi.py',
            '--teleop.left_port', 'COM8',
            '--teleop.right_port', 'COM7',
            '--teleop.id', 'so101_leader_bi',
            '--teleop.arm_profile', 'so-arm-5dof',
            '--force_fresh_calibration'
        )
        MapArguments = @(
            '.\examples\alohamini\teleoperate_bi.py',
            '--no_robot',
            '--robot.robot_model', 'alohamini1',
            '--teleop.left_port', 'COM8',
            '--teleop.right_port', 'COM7',
            '--teleop.id', 'so101_leader_bi',
            '--teleop.arm_profile', 'so-arm-5dof',
            '--require_calibration_match',
            '--duration_s', '12',
            '--fps', '5',
            '--start_paused',
            '--no_keyboard',
            '--no_rerun'
        )
    }
}

function Assert-Packet2nR5ExactStringArray {
    param(
        [Parameter(Mandatory)] [object[]] $Actual,
        [Parameter(Mandatory)] [object[]] $Expected,
        [Parameter(Mandatory)] [string] $Label
    )
    if ($Actual.Count -ne $Expected.Count) { throw "$Label argument count mismatch" }
    for ($packet2nIndex = 0; $packet2nIndex -lt $Expected.Count; $packet2nIndex++) {
        if ([string]$Actual[$packet2nIndex] -cne [string]$Expected[$packet2nIndex]) {
            throw "$Label argument mismatch at index $packet2nIndex"
        }
    }
}

function Assert-Packet2nR5ExactPropertySet {
    param(
        [Parameter(Mandatory)] [object] $Object,
        [Parameter(Mandatory)] [string[]] $Expected,
        [Parameter(Mandatory)] [string] $Label
    )
    $packet2nActual = @($Object.PSObject.Properties.Name)
    if ($packet2nActual.Count -ne $Expected.Count -or @(Compare-Object -ReferenceObject $Expected -DifferenceObject $packet2nActual).Count -ne 0) {
        throw "$Label property set mismatch"
    }
}

function Assert-Packet2nR5CalibrationSchema {
    param([Parameter(Mandatory)] [string] $Path)

    $packet2nCalibration = Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
    $packet2nJoints = @('shoulder_pan', 'shoulder_lift', 'elbow_flex', 'wrist_flex', 'wrist_roll', 'gripper')
    $packet2nFields = @('id', 'drive_mode', 'homing_offset', 'range_min', 'range_max')
    $packet2nIntegerTypes = @([byte], [sbyte], [int16], [uint16], [int32], [uint32], [int64], [uint64])
    Assert-Packet2nR5ExactPropertySet -Object $packet2nCalibration -Expected $packet2nJoints -Label "$Path top level"
    for ($packet2nIndex = 0; $packet2nIndex -lt $packet2nJoints.Count; $packet2nIndex++) {
        $packet2nJointName = $packet2nJoints[$packet2nIndex]
        $packet2nJoint = $packet2nCalibration.$packet2nJointName
        Assert-Packet2nR5ExactPropertySet -Object $packet2nJoint -Expected $packet2nFields -Label "$Path $packet2nJointName"
        foreach ($packet2nField in $packet2nFields) {
            $packet2nValue = $packet2nJoint.$packet2nField
            if ($null -eq $packet2nValue -or $packet2nIntegerTypes -notcontains $packet2nValue.GetType()) {
                throw "$Path $packet2nJointName.$packet2nField must be an integer"
            }
        }
        if ([int64]$packet2nJoint.id -ne $packet2nIndex + 1) { throw "$Path $packet2nJointName has wrong motor ID" }
        if ([int64]$packet2nJoint.drive_mode -ne 0) { throw "$Path $packet2nJointName has wrong drive mode" }
        if ([int64]$packet2nJoint.range_min -ge [int64]$packet2nJoint.range_max) { throw "$Path $packet2nJointName has invalid range" }
    }
    if ([int64]$packet2nCalibration.wrist_roll.range_min -ne 0 -or [int64]$packet2nCalibration.wrist_roll.range_max -ne 4095) {
        throw "$Path wrist_roll must retain the full-turn 0..4095 range"
    }
}

function Assert-Packet2nR5PostIdentityFreshness {
    param(
        [Parameter(Mandatory)] [object[]] $PreCalibrationFiles,
        [Parameter(Mandatory)] [object[]] $PostCalibrationFiles,
        [Parameter(Mandatory)] [datetime] $SessionStartedUtc
    )

    $packet2nDefinition = Get-Packet2nR5Definition
    if ($PreCalibrationFiles.Count -ne 2 -or $PostCalibrationFiles.Count -ne 2) { throw 'calibration evidence must contain exactly two pre and two post identities' }
    foreach ($packet2nFile in $packet2nDefinition.Files) {
        $packet2nPre = @($PreCalibrationFiles | Where-Object { $_.Name -eq $packet2nFile.Name })
        $packet2nPost = @($PostCalibrationFiles | Where-Object { $_.Name -eq $packet2nFile.Name })
        if ($packet2nPre.Count -ne 1 -or $packet2nPost.Count -ne 1) { throw "missing or duplicate identity: $($packet2nFile.Name)" }
        if ($packet2nPre[0].Path -ne $packet2nFile.SourcePath -or [int64]$packet2nPre[0].Bytes -ne $packet2nFile.Bytes -or $packet2nPre[0].Sha256 -ne $packet2nFile.Sha256 -or $packet2nPre[0].LastWriteTimeUtc -ne $packet2nFile.SourceLastWriteTimeUtc) {
            throw "pre-calibration identity mismatch: $($packet2nFile.Name)"
        }
        if ($packet2nPost[0].Path -ne $packet2nFile.SourcePath -or [int64]$packet2nPost[0].Bytes -le 0) { throw "invalid post-calibration identity: $($packet2nFile.Name)" }
        $packet2nPostTime = [datetime]$packet2nPost[0].LastWriteTimeUtc
        if ($packet2nPostTime -le [datetime]$packet2nPre[0].LastWriteTimeUtc -or $packet2nPostTime -le $SessionStartedUtc) {
            throw "post-calibration timestamp is stale: $($packet2nFile.Name)"
        }
    }
    $packet2nOldHashes = @($packet2nDefinition.Files | ForEach-Object { $_.Sha256 })
    if (@($PostCalibrationFiles | Where-Object { $_.Sha256 -in $packet2nOldHashes }).Count -ne 0) {
        throw 'each post-calibration hash must differ from both old hashes'
    }
    if ($PostCalibrationFiles[0].Sha256 -eq $PostCalibrationFiles[1].Sha256) {
        throw 'left and right post-calibration hashes must differ from each other'
    }
}

function Assert-Packet2nR5CommonGuard {
    $packet2nDefinition = Get-Packet2nR5Definition
    $packet2nVerifierCommand = @(Get-Command -Name Get-Packet2nLeaderMapSummary -CommandType Function -ErrorAction SilentlyContinue)
    if ($packet2nVerifierCommand.Count -ne 1) { throw 'strict Packet 2N leader-map verifier function is not loaded' }
    $packet2nBranchOutput = @(& git branch --show-current 2>&1)
    $packet2nBranchExit = $LASTEXITCODE
    if ($packet2nBranchExit -ne 0 -or $packet2nBranchOutput.Count -ne 1 -or $packet2nBranchOutput[0].Trim() -ne $packet2nDefinition.Branch) {
        throw "wrong Windows branch or Git branch query failed: exit=$packet2nBranchExit output=$($packet2nBranchOutput -join ' | ')"
    }
    $packet2nStatusOutput = @(& git status --porcelain=v1 --untracked-files=all -- . ':(exclude)docs/alohamini/alohamini.md' 2>&1)
    $packet2nStatusExit = $LASTEXITCODE
    if ($packet2nStatusExit -ne 0 -or $packet2nStatusOutput.Count -ne 0) {
        throw "tracked paths other than this document are dirty or Git status failed: exit=$packet2nStatusExit output=$($packet2nStatusOutput -join ' | ')"
    }
    $packet2nMergeOutput = @(& git merge-base --is-ancestor $packet2nDefinition.BehaviorSha HEAD 2>&1)
    $packet2nMergeExit = $LASTEXITCODE
    if ($packet2nMergeExit -ne 0) {
        throw "behavior baseline is not an ancestor or Git merge-base failed: exit=$packet2nMergeExit output=$($packet2nMergeOutput -join ' | ')"
    }
    $packet2nDiffOutput = @(& git diff --quiet $packet2nDefinition.BehaviorSha -- . ':(exclude)docs/alohamini/alohamini.md' 2>&1)
    $packet2nDiffExit = $LASTEXITCODE
    if ($packet2nDiffExit -eq 1) { throw 'a tracked path other than this document differs from the behavior baseline' }
    if ($packet2nDiffExit -ne 0) { throw "Git baseline diff failed: exit=$packet2nDiffExit output=$($packet2nDiffOutput -join ' | ')" }

    $packet2nOverrideNames = @(
        'PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP', 'PYTHONUSERBASE',
        'HF_LEROBOT_CALIBRATION', 'HF_LEROBOT_HOME', 'HF_HOME',
        'HF_HUB_CACHE', 'HUGGINGFACE_HUB_CACHE'
    )
    foreach ($packet2nOverrideName in $packet2nOverrideNames) {
        if (-not [string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable($packet2nOverrideName, 'Process'))) {
            throw "Python/Hugging Face override must be unset: $packet2nOverrideName"
        }
    }

    $packet2nPython = (Resolve-Path -LiteralPath '.\.venv\Scripts\python.exe').Path
    $packet2nRootOutput = @(& $packet2nPython -c "from lerobot.utils.constants import HF_LEROBOT_CALIBRATION; print(HF_LEROBOT_CALIBRATION)" 2>&1)
    $packet2nRootExit = $LASTEXITCODE
    if ($packet2nRootExit -ne 0 -or $packet2nRootOutput.Count -ne 1 -or $packet2nRootOutput[0].Trim() -ne $packet2nDefinition.CalibrationRoot) {
        throw "unexpected calibration root or Python failure: exit=$packet2nRootExit output=$($packet2nRootOutput -join ' | ')"
    }

    $packet2nCheckout = (Resolve-Path -LiteralPath '.').Path
    $packet2nImportCommand = "import importlib, sys; sys.path.insert(0, 'examples/alohamini'); names=('calibrate_bi','teleoperate_bi','leader_client_utils','lerobot.teleoperators.bi_so_leader.bi_so_leader','lerobot.teleoperators.so_leader.so_leader'); modules=tuple(importlib.import_module(n) for n in names); print(*(m.__file__ for m in modules), sep='\n')"
    $packet2nImportOutput = @(& $packet2nPython -c $packet2nImportCommand 2>&1)
    $packet2nImportExit = $LASTEXITCODE
    if ($packet2nImportExit -ne 0) { throw "reviewed import check failed: exit=$packet2nImportExit output=$($packet2nImportOutput -join ' | ')" }
    $packet2nExpectedImports = @(
        (Join-Path $packet2nCheckout 'examples\alohamini\calibrate_bi.py'),
        (Join-Path $packet2nCheckout 'examples\alohamini\teleoperate_bi.py'),
        (Join-Path $packet2nCheckout 'examples\alohamini\leader_client_utils.py'),
        (Join-Path $packet2nCheckout 'src\lerobot\teleoperators\bi_so_leader\bi_so_leader.py'),
        (Join-Path $packet2nCheckout 'src\lerobot\teleoperators\so_leader\so_leader.py')
    )
    if ($packet2nImportOutput.Count -ne $packet2nExpectedImports.Count) { throw 'reviewed import count mismatch' }
    $packet2nCheckoutPrefix = $packet2nCheckout.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    for ($packet2nIndex = 0; $packet2nIndex -lt $packet2nExpectedImports.Count; $packet2nIndex++) {
        $packet2nImportPath = ([string]$packet2nImportOutput[$packet2nIndex]).Trim()
        $packet2nActualImport = (Resolve-Path -LiteralPath $packet2nImportPath).Path
        $packet2nExpectedImport = (Resolve-Path -LiteralPath $packet2nExpectedImports[$packet2nIndex]).Path
        if (-not $packet2nActualImport.StartsWith($packet2nCheckoutPrefix, [StringComparison]::OrdinalIgnoreCase) -or -not $packet2nActualImport.Equals($packet2nExpectedImport, [StringComparison]::OrdinalIgnoreCase)) {
            throw "reviewed import path mismatch: $packet2nActualImport"
        }
    }

    if (-not (Test-Path -LiteralPath $packet2nDefinition.ManifestPath -PathType Leaf)) { throw "missing manifest: $($packet2nDefinition.ManifestPath)" }
    if ((Get-FileHash -LiteralPath $packet2nDefinition.ManifestPath -Algorithm SHA256).Hash -ne $packet2nDefinition.ManifestSha256) { throw 'manifest SHA-256 mismatch' }
    $packet2nManifest = Get-Content -Raw -LiteralPath $packet2nDefinition.ManifestPath | ConvertFrom-Json -DateKind String
    if ($packet2nManifest.Packet -ne $packet2nDefinition.Packet -or $packet2nManifest.BackupDirectory -ne $packet2nDefinition.BackupDirectory -or -not $packet2nManifest.CopyOnly -or -not $packet2nManifest.HardwareDisconnected -or $packet2nManifest.Files.Count -ne 2) {
        throw 'manifest identity or safety fields differ'
    }
    foreach ($packet2nFile in $packet2nDefinition.Files) {
        $packet2nManifestEntry = @($packet2nManifest.Files | Where-Object { $_.OriginalPath -eq $packet2nFile.SourcePath })
        if ($packet2nManifestEntry.Count -ne 1 -or $packet2nManifestEntry[0].BackupPath -ne $packet2nFile.BackupPath -or $packet2nManifestEntry[0].Sha256 -ne $packet2nFile.Sha256 -or [int64]$packet2nManifestEntry[0].Bytes -ne $packet2nFile.Bytes -or $packet2nManifestEntry[0].SourceLastWriteTimeUtc -ne $packet2nFile.SourceLastWriteTimeUtc) {
            throw "exact manifest entry mismatch: $($packet2nFile.Name)"
        }
        if (-not (Test-Path -LiteralPath $packet2nFile.BackupPath -PathType Leaf)) { throw "missing backup: $($packet2nFile.BackupPath)" }
        $packet2nBackupItem = Get-Item -LiteralPath $packet2nFile.BackupPath
        if ($packet2nBackupItem.Length -ne $packet2nFile.Bytes -or (Get-FileHash -LiteralPath $packet2nFile.BackupPath -Algorithm SHA256).Hash -ne $packet2nFile.Sha256) {
            throw "backup hash/size mismatch: $($packet2nFile.BackupPath)"
        }
    }
}

function Assert-Packet2nR5PreCalibrationGuard {
    Assert-Packet2nR5CommonGuard
    $packet2nDefinition = Get-Packet2nR5Definition
    if (-not $script:packet2nR5SessionId -or $null -eq $script:packet2nR5SessionStartedUtc) { throw 'missing calibration session identity' }
    $script:packet2nR5PreCalibrationEvidence = @(
        foreach ($packet2nFile in $packet2nDefinition.Files) {
            if (-not (Test-Path -LiteralPath $packet2nFile.SourcePath -PathType Leaf)) { throw "missing pre-calibration source: $($packet2nFile.SourcePath)" }
            Assert-Packet2nR5CalibrationSchema -Path $packet2nFile.SourcePath
            $packet2nSourceItem = Get-Item -LiteralPath $packet2nFile.SourcePath
            $packet2nSourceHash = (Get-FileHash -LiteralPath $packet2nFile.SourcePath -Algorithm SHA256).Hash
            if ($packet2nSourceItem.Length -ne $packet2nFile.Bytes -or $packet2nSourceHash -ne $packet2nFile.Sha256 -or $packet2nSourceItem.LastWriteTimeUtc.ToString('o') -ne $packet2nFile.SourceLastWriteTimeUtc) {
                throw "pre-calibration hash/size/mtime mismatch: $($packet2nFile.SourcePath)"
            }
            [pscustomobject]@{
                Name = $packet2nFile.Name
                Path = $packet2nFile.SourcePath
                Bytes = [int64]$packet2nSourceItem.Length
                Sha256 = $packet2nSourceHash
                LastWriteTimeUtc = $packet2nSourceItem.LastWriteTimeUtc.ToString('o')
            }
        }
    )
}

function Get-Packet2nR5CalibrationTranscriptHeader {
    param(
        [Parameter(Mandatory)] [string] $SessionId,
        [Parameter(Mandatory)] [string] $SessionStartedUtc,
        [Parameter(Mandatory)] [string] $BehaviorSha,
        [Parameter(Mandatory)] [string] $Executable,
        [Parameter(Mandatory)] [object[]] $Arguments
    )
    $packet2nArgumentsJson = ConvertTo-Json -InputObject @($Arguments) -Compress
    @(
        "PACKET2N_R5_SESSION_ID=$SessionId",
        "PACKET2N_R5_SESSION_STARTED_UTC=$SessionStartedUtc",
        "PACKET2N_R5_BEHAVIOR_SHA=$BehaviorSha",
        "PACKET2N_R5_CALIBRATION_EXECUTABLE=$Executable",
        "PACKET2N_R5_CALIBRATION_ARGS_JSON=$packet2nArgumentsJson",
        'PACKET2N_R5_CALIBRATION_ID=so101_leader_bi',
        'PACKET2N_R5_CALIBRATION_PROFILE=so-arm-5dof',
        'PACKET2N_R5_CALIBRATION_LEFT_PORT=COM8',
        'PACKET2N_R5_CALIBRATION_RIGHT_PORT=COM7'
    )
}

function Assert-Packet2nR5PersistedEvidence {
    param(
        [Parameter(Mandatory)] [string] $EvidencePath,
        [Parameter(Mandatory)] [string] $EvidenceSha256
    )

    $packet2nDefinition = Get-Packet2nR5Definition
    if (-not (Test-Path -LiteralPath $EvidencePath -PathType Leaf) -or (Get-FileHash -LiteralPath $EvidencePath -Algorithm SHA256).Hash -ne $EvidenceSha256) {
        throw 'persisted evidence is missing or its SHA-256 changed'
    }
    $packet2nEvidence = Get-Content -Raw -LiteralPath $EvidencePath | ConvertFrom-Json -DateKind String
    $packet2nEvidenceFields = @(
        'Packet', 'SessionId', 'SessionStartedUtc', 'BehaviorSha', 'CalibrationExecutable',
        'CalibrationArguments', 'CalibrationTranscriptPath', 'CalibrationTranscriptSha256',
        'PreCalibrationFiles', 'PostCalibrationFiles'
    )
    Assert-Packet2nR5ExactPropertySet -Object $packet2nEvidence -Expected $packet2nEvidenceFields -Label 'persisted evidence'
    $packet2nParsedGuid = [guid]::Empty
    if ($packet2nEvidence.Packet -ne $packet2nDefinition.Packet -or -not [guid]::TryParse($packet2nEvidence.SessionId, [ref]$packet2nParsedGuid) -or $packet2nEvidence.BehaviorSha -ne $packet2nDefinition.BehaviorSha) {
        throw 'persisted evidence packet/session/behavior identity mismatch'
    }
    $packet2nSessionStartedUtc = [datetime]$packet2nEvidence.SessionStartedUtc
    $packet2nExpectedEvidencePath = Join-Path $packet2nDefinition.LogsDirectory "packet2n-r5-evidence-$($packet2nEvidence.SessionId).json"
    if ($EvidencePath -ne $packet2nExpectedEvidencePath) { throw 'persisted evidence path is not bound to its session' }
    $packet2nExpectedExecutable = (Resolve-Path -LiteralPath '.\.venv\Scripts\python.exe').Path
    if ($packet2nEvidence.CalibrationExecutable -ne $packet2nExpectedExecutable) { throw 'persisted calibration executable mismatch' }
    Assert-Packet2nR5ExactStringArray -Actual @($packet2nEvidence.CalibrationArguments) -Expected $packet2nDefinition.CalibrationArguments -Label 'persisted calibration'

    $packet2nExpectedTranscriptPath = Join-Path $packet2nDefinition.LogsDirectory "packet2n-r5-calibration-$($packet2nEvidence.SessionId).log"
    if ($packet2nEvidence.CalibrationTranscriptPath -ne $packet2nExpectedTranscriptPath -or -not (Test-Path -LiteralPath $packet2nExpectedTranscriptPath -PathType Leaf)) {
        throw 'calibration transcript path is missing or not session-bound'
    }
    $packet2nTranscriptHash = (Get-FileHash -LiteralPath $packet2nExpectedTranscriptPath -Algorithm SHA256).Hash
    if ($packet2nTranscriptHash -ne $packet2nEvidence.CalibrationTranscriptSha256) { throw 'calibration transcript SHA-256 mismatch' }
    $packet2nTranscriptLines = @(Get-Content -LiteralPath $packet2nExpectedTranscriptPath)
    $packet2nExpectedHeader = Get-Packet2nR5CalibrationTranscriptHeader -SessionId $packet2nEvidence.SessionId -SessionStartedUtc $packet2nEvidence.SessionStartedUtc -BehaviorSha $packet2nEvidence.BehaviorSha -Executable $packet2nEvidence.CalibrationExecutable -Arguments @($packet2nEvidence.CalibrationArguments)
    if ($packet2nTranscriptLines.Count -le $packet2nExpectedHeader.Count) { throw 'calibration transcript is incomplete' }
    for ($packet2nIndex = 0; $packet2nIndex -lt $packet2nExpectedHeader.Count; $packet2nIndex++) {
        if ($packet2nTranscriptLines[$packet2nIndex] -cne $packet2nExpectedHeader[$packet2nIndex]) { throw "calibration transcript header mismatch at line $($packet2nIndex + 1)" }
    }
    if (@($packet2nTranscriptLines | Where-Object { $_ -eq 'CALIBRATION_EXIT_CODE=0' }).Count -ne 1 -or $packet2nTranscriptLines[-1] -ne 'CALIBRATION_EXIT_CODE=0') {
        throw 'calibration transcript does not end in exactly one zero exit record'
    }

    foreach ($packet2nFile in $packet2nDefinition.Files) {
        $packet2nPre = @($packet2nEvidence.PreCalibrationFiles | Where-Object { $_.Name -eq $packet2nFile.Name })
        $packet2nPost = @($packet2nEvidence.PostCalibrationFiles | Where-Object { $_.Name -eq $packet2nFile.Name })
        if ($packet2nPre.Count -ne 1 -or $packet2nPost.Count -ne 1) { throw "persisted source identity missing or duplicated: $($packet2nFile.Name)" }
        if (-not (Test-Path -LiteralPath $packet2nFile.SourcePath -PathType Leaf)) { throw "current calibration source is missing: $($packet2nFile.SourcePath)" }
        Assert-Packet2nR5CalibrationSchema -Path $packet2nFile.SourcePath
        $packet2nCurrentItem = Get-Item -LiteralPath $packet2nFile.SourcePath
        $packet2nCurrentHash = (Get-FileHash -LiteralPath $packet2nFile.SourcePath -Algorithm SHA256).Hash
        if ($packet2nPost[0].Path -ne $packet2nFile.SourcePath -or [int64]$packet2nPost[0].Bytes -ne $packet2nCurrentItem.Length -or $packet2nPost[0].Sha256 -ne $packet2nCurrentHash -or $packet2nPost[0].LastWriteTimeUtc -ne $packet2nCurrentItem.LastWriteTimeUtc.ToString('o')) {
            throw "current calibration source differs from persisted post identity: $($packet2nFile.Name)"
        }
    }
    Assert-Packet2nR5PostIdentityFreshness -PreCalibrationFiles @($packet2nEvidence.PreCalibrationFiles) -PostCalibrationFiles @($packet2nEvidence.PostCalibrationFiles) -SessionStartedUtc $packet2nSessionStartedUtc

    $packet2nLeftIdentity = @($packet2nEvidence.PostCalibrationFiles | Where-Object { $_.Name -eq 'so101_leader_bi_left.json' })[0]
    $packet2nRightIdentity = @($packet2nEvidence.PostCalibrationFiles | Where-Object { $_.Name -eq 'so101_leader_bi_right.json' })[0]
    [pscustomobject]@{
        SessionId = $packet2nEvidence.SessionId
        BehaviorSha = $packet2nEvidence.BehaviorSha
        EvidencePath = $EvidencePath
        EvidenceSha256 = $EvidenceSha256
        TranscriptPath = $packet2nExpectedTranscriptPath
        TranscriptSha256 = $packet2nTranscriptHash
        LeftIdentityJson = ($packet2nLeftIdentity | Select-Object Name, Path, Bytes, Sha256, LastWriteTimeUtc | ConvertTo-Json -Compress)
        RightIdentityJson = ($packet2nRightIdentity | Select-Object Name, Path, Bytes, Sha256, LastWriteTimeUtc | ConvertTo-Json -Compress)
    }
}

function Save-Packet2nR5PostCalibrationEvidence {
    Assert-Packet2nR5CommonGuard
    $packet2nDefinition = Get-Packet2nR5Definition
    if ($script:packet2nR5PreCalibrationEvidence.Count -ne 2 -or -not $script:packet2nR5CalibrationTranscriptPath) { throw 'missing same-session pre-calibration or transcript state' }

    $packet2nPostCalibrationFiles = @(
        foreach ($packet2nFile in $packet2nDefinition.Files) {
            if (-not (Test-Path -LiteralPath $packet2nFile.SourcePath -PathType Leaf)) { throw "missing post-calibration source: $($packet2nFile.SourcePath)" }
            Assert-Packet2nR5CalibrationSchema -Path $packet2nFile.SourcePath
            $packet2nSourceItem = Get-Item -LiteralPath $packet2nFile.SourcePath
            [pscustomobject]@{
                Name = $packet2nFile.Name
                Path = $packet2nFile.SourcePath
                Bytes = [int64]$packet2nSourceItem.Length
                Sha256 = (Get-FileHash -LiteralPath $packet2nFile.SourcePath -Algorithm SHA256).Hash
                LastWriteTimeUtc = $packet2nSourceItem.LastWriteTimeUtc.ToString('o')
            }
        }
    )
    Assert-Packet2nR5PostIdentityFreshness -PreCalibrationFiles @($script:packet2nR5PreCalibrationEvidence) -PostCalibrationFiles $packet2nPostCalibrationFiles -SessionStartedUtc $script:packet2nR5SessionStartedUtc

    $packet2nTranscriptLines = @(Get-Content -LiteralPath $script:packet2nR5CalibrationTranscriptPath)
    if ($packet2nTranscriptLines[-1] -ne 'CALIBRATION_EXIT_CODE=0') { throw 'calibration transcript lacks the zero-exit terminator' }
    $packet2nTranscriptHash = (Get-FileHash -LiteralPath $script:packet2nR5CalibrationTranscriptPath -Algorithm SHA256).Hash
    $script:packet2nR5EvidencePath = Join-Path $packet2nDefinition.LogsDirectory "packet2n-r5-evidence-$($script:packet2nR5SessionId).json"
    if (Test-Path -LiteralPath $script:packet2nR5EvidencePath) { throw "refusing to overwrite evidence: $($script:packet2nR5EvidencePath)" }
    [pscustomobject]@{
        Packet = $packet2nDefinition.Packet
        SessionId = $script:packet2nR5SessionId
        SessionStartedUtc = $script:packet2nR5SessionStartedUtc.ToString('o')
        BehaviorSha = $packet2nDefinition.BehaviorSha
        CalibrationExecutable = $script:packet2nR5CalibrationExecutable
        CalibrationArguments = @($script:packet2nR5CalibrationArguments)
        CalibrationTranscriptPath = $script:packet2nR5CalibrationTranscriptPath
        CalibrationTranscriptSha256 = $packet2nTranscriptHash
        PreCalibrationFiles = @($script:packet2nR5PreCalibrationEvidence)
        PostCalibrationFiles = @($packet2nPostCalibrationFiles)
    } | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $script:packet2nR5EvidencePath -Encoding utf8 -NoNewline
    $script:packet2nR5EvidenceSha256 = (Get-FileHash -LiteralPath $script:packet2nR5EvidencePath -Algorithm SHA256).Hash
    $null = Assert-Packet2nR5PersistedEvidence -EvidencePath $script:packet2nR5EvidencePath -EvidenceSha256 $script:packet2nR5EvidenceSha256
}

function Get-Packet2nR5MapMetadataLines {
    param([Parameter(Mandatory)] [object] $Context)
    @(
        "PACKET2N_R5_SESSION_ID=$($Context.SessionId)",
        "PACKET2N_R5_BEHAVIOR_SHA=$($Context.BehaviorSha)",
        'PACKET2N_R5_GUARD_SUCCESS=1',
        "PACKET2N_R5_EVIDENCE_PATH=$($Context.EvidencePath)",
        "PACKET2N_R5_EVIDENCE_SHA256=$($Context.EvidenceSha256)",
        "PACKET2N_R5_TRANSCRIPT_PATH=$($Context.TranscriptPath)",
        "PACKET2N_R5_TRANSCRIPT_SHA256=$($Context.TranscriptSha256)",
        "PACKET2N_R5_POST_SOURCE_LEFT_JSON=$($Context.LeftIdentityJson)",
        "PACKET2N_R5_POST_SOURCE_RIGHT_JSON=$($Context.RightIdentityJson)"
    )
}

function Assert-Packet2nR5MapMetadata {
    param(
        [Parameter(Mandatory)] [string[]] $Lines,
        [Parameter(Mandatory)] [string[]] $ExpectedLines,
        [Parameter(Mandatory)] [string] $Path
    )
    $packet2nActualMetadata = @($Lines | Where-Object { $_.StartsWith('PACKET2N_R5_', [StringComparison]::Ordinal) })
    if ($packet2nActualMetadata.Count -ne $ExpectedLines.Count) { throw "Packet 2N-R5 metadata count mismatch in $Path" }
    foreach ($packet2nExpectedLine in $ExpectedLines) {
        if (@($packet2nActualMetadata | Where-Object { $_ -ceq $packet2nExpectedLine }).Count -ne 1) {
            throw "missing, duplicate, or mismatched Packet 2N-R5 metadata in $Path"
        }
    }
    if (@($packet2nActualMetadata | Where-Object { $_ -cnotin $ExpectedLines }).Count -ne 0) {
        throw "unexpected Packet 2N-R5 metadata in $Path"
    }
}

function Assert-Packet2nR5PostCalibrationGuard {
    Assert-Packet2nR5CommonGuard
    if (-not $script:packet2nR5EvidencePath -or -not $script:packet2nR5EvidenceSha256 -or -not $script:packet2nR5SessionId) {
        throw 'missing same-session persisted evidence binding'
    }
    $packet2nContext = Assert-Packet2nR5PersistedEvidence -EvidencePath $script:packet2nR5EvidencePath -EvidenceSha256 $script:packet2nR5EvidenceSha256
    if ($packet2nContext.SessionId -ne $script:packet2nR5SessionId) { throw 'persisted evidence belongs to a different session' }
    $packet2nContext
}

# PACKET2N_R5_CALIBRATION_INVOCATION_START — hardware authorization required beyond this line.
$packet2nDefinition = Get-Packet2nR5Definition
New-Item -ItemType Directory -Force -Path $packet2nDefinition.LogsDirectory | Out-Null
$script:packet2nR5SessionId = [guid]::NewGuid().ToString()
$script:packet2nR5SessionStartedUtc = [datetime]::UtcNow
$script:packet2nR5CalibrationExecutable = (Resolve-Path -LiteralPath '.\.venv\Scripts\python.exe').Path
$script:packet2nR5CalibrationArguments = @(
    '.\examples\alohamini\calibrate_bi.py',
    '--teleop.left_port', 'COM8',
    '--teleop.right_port', 'COM7',
    '--teleop.id', 'so101_leader_bi',
    '--teleop.arm_profile', 'so-arm-5dof',
    '--force_fresh_calibration'
)
Assert-Packet2nR5ExactStringArray -Actual $script:packet2nR5CalibrationArguments -Expected $packet2nDefinition.CalibrationArguments -Label 'calibration'
Assert-Packet2nR5PreCalibrationGuard

$script:packet2nR5CalibrationTranscriptPath = Join-Path $packet2nDefinition.LogsDirectory "packet2n-r5-calibration-$($script:packet2nR5SessionId).log"
if (Test-Path -LiteralPath $script:packet2nR5CalibrationTranscriptPath) { throw "refusing to overwrite transcript: $($script:packet2nR5CalibrationTranscriptPath)" }
$packet2nTranscriptHeader = Get-Packet2nR5CalibrationTranscriptHeader -SessionId $script:packet2nR5SessionId -SessionStartedUtc $script:packet2nR5SessionStartedUtc.ToString('o') -BehaviorSha $packet2nDefinition.BehaviorSha -Executable $script:packet2nR5CalibrationExecutable -Arguments $script:packet2nR5CalibrationArguments
$packet2nTranscriptHeader | Set-Content -LiteralPath $script:packet2nR5CalibrationTranscriptPath -Encoding utf8

$packet2nR5CalibrationInvocationArguments = @($script:packet2nR5CalibrationArguments)
& $script:packet2nR5CalibrationExecutable @packet2nR5CalibrationInvocationArguments 2>&1 | Tee-Object -FilePath $script:packet2nR5CalibrationTranscriptPath -Append
$packet2nR5CalibrationExitCode = $LASTEXITCODE
"CALIBRATION_EXIT_CODE=$packet2nR5CalibrationExitCode" | Tee-Object -FilePath $script:packet2nR5CalibrationTranscriptPath -Append
if ($packet2nR5CalibrationExitCode -ne 0) { throw "corrected-port full recalibration failed with $packet2nR5CalibrationExitCode" }
Save-Packet2nR5PostCalibrationEvidence
```

This workflow captures the exact two pre-calibration source hashes, byte counts, and timestamps and requires each pre timestamp to equal the pinned manifest. After a zero-exit forced run, both current JSONs must pass the exact six-joint/five-field schema, have IDs `1..6`, `drive_mode=0`, integer values, valid ranges, and `wrist_roll=0..4095`. Each post timestamp must be newer than both the session start and its exact pre timestamp; each post hash must differ from **both** old hashes and from the other post hash. The transcript starts with the session, behavior SHA, exact executable/argument array, ID/profile, and corrected ports; it ends in exactly one `CALIBRATION_EXIT_CODE=0` and is hashed. The persisted evidence JSON binds the same session/start, behavior SHA, exact arguments, transcript path/hash, and both pre/post identities, and is itself hashed.

Before each no-robot connection, the guard revalidates the branch/baseline/imports/manifest/backups, persisted evidence hash and schema, transcript hash/header/exit, and both current calibration identities. A stale, unchanged, equal, swapped, malformed, missing, or later-mutated source fails before connection.

**Future corrected-port physical-left-only no-robot run (`COM8`).** After the client prints its Enter prompt, slowly open and close only the **physical left gripper** through at least 20 normalized units. Hold its other five joints and the entire physical right leader still.

```powershell
$ErrorActionPreference = 'Stop'
$packet2nR5LeftContext = Assert-Packet2nR5PostCalibrationGuard
$packet2nR5LeftArguments = @(
    '.\examples\alohamini\teleoperate_bi.py',
    '--no_robot',
    '--robot.robot_model', 'alohamini1',
    '--teleop.left_port', 'COM8',
    '--teleop.right_port', 'COM7',
    '--teleop.id', 'so101_leader_bi',
    '--teleop.arm_profile', 'so-arm-5dof',
    '--require_calibration_match',
    '--duration_s', '12',
    '--fps', '5',
    '--start_paused',
    '--no_keyboard',
    '--no_rerun'
)
Assert-Packet2nR5ExactStringArray -Actual $packet2nR5LeftArguments -Expected (Get-Packet2nR5Definition).MapArguments -Label 'physical-left map'
$packet2nR5PhysicalLeftLog = Join-Path (Get-Packet2nR5Definition).LogsDirectory "packet2n-r5-physical-left-only-$($script:packet2nR5SessionId).log"
if (Test-Path -LiteralPath $packet2nR5PhysicalLeftLog) { throw "refusing to overwrite map log: $packet2nR5PhysicalLeftLog" }
'MAP_RUN=PHYSICAL_LEFT_ONLY' | Set-Content -LiteralPath $packet2nR5PhysicalLeftLog -Encoding utf8
Get-Packet2nR5MapMetadataLines -Context $packet2nR5LeftContext | Add-Content -LiteralPath $packet2nR5PhysicalLeftLog -Encoding utf8
& $script:packet2nR5CalibrationExecutable @packet2nR5LeftArguments 2>&1 | Tee-Object -FilePath $packet2nR5PhysicalLeftLog -Append
$packet2nR5PhysicalLeftExitCode = $LASTEXITCODE
"CLIENT_EXIT_CODE=$packet2nR5PhysicalLeftExitCode" | Tee-Object -FilePath $packet2nR5PhysicalLeftLog -Append
if ($packet2nR5PhysicalLeftExitCode -ne 0) { throw "physical-left map failed with $packet2nR5PhysicalLeftExitCode" }
```

**Future corrected-port physical-right-only no-robot run (`COM7`).** After the client prints its Enter prompt, slowly open and close only the **physical right gripper** through at least 20 normalized units. Hold its other five joints and the entire physical left leader still.

```powershell
$ErrorActionPreference = 'Stop'
$packet2nR5RightContext = Assert-Packet2nR5PostCalibrationGuard
$packet2nR5RightArguments = @(
    '.\examples\alohamini\teleoperate_bi.py',
    '--no_robot',
    '--robot.robot_model', 'alohamini1',
    '--teleop.left_port', 'COM8',
    '--teleop.right_port', 'COM7',
    '--teleop.id', 'so101_leader_bi',
    '--teleop.arm_profile', 'so-arm-5dof',
    '--require_calibration_match',
    '--duration_s', '12',
    '--fps', '5',
    '--start_paused',
    '--no_keyboard',
    '--no_rerun'
)
Assert-Packet2nR5ExactStringArray -Actual $packet2nR5RightArguments -Expected (Get-Packet2nR5Definition).MapArguments -Label 'physical-right map'
$packet2nR5PhysicalRightLog = Join-Path (Get-Packet2nR5Definition).LogsDirectory "packet2n-r5-physical-right-only-$($script:packet2nR5SessionId).log"
if (Test-Path -LiteralPath $packet2nR5PhysicalRightLog) { throw "refusing to overwrite map log: $packet2nR5PhysicalRightLog" }
'MAP_RUN=PHYSICAL_RIGHT_ONLY' | Set-Content -LiteralPath $packet2nR5PhysicalRightLog -Encoding utf8
Get-Packet2nR5MapMetadataLines -Context $packet2nR5RightContext | Add-Content -LiteralPath $packet2nR5PhysicalRightLog -Encoding utf8
& $script:packet2nR5CalibrationExecutable @packet2nR5RightArguments 2>&1 | Tee-Object -FilePath $packet2nR5PhysicalRightLog -Append
$packet2nR5PhysicalRightExitCode = $LASTEXITCODE
"CLIENT_EXIT_CODE=$packet2nR5PhysicalRightExitCode" | Tee-Object -FilePath $packet2nR5PhysicalRightLog -Append
if ($packet2nR5PhysicalRightExitCode -ne 0) { throw "physical-right map failed with $packet2nR5PhysicalRightExitCode" }
```

After both clients disconnect, switch off both 7.4 V leader supplies and disconnect both leader buses. Then run this exact evidence-aware invocation in the same PowerShell session; do not use the historical prompt-only invocation for Packet 2N-R5:

```powershell
$ErrorActionPreference = 'Stop'
$null = Assert-Packet2nR5PostCalibrationGuard
$packet2nR5LeftSummary = Get-Packet2nLeaderMapSummary -Path $packet2nR5PhysicalLeftLog -ExpectedMarker PHYSICAL_LEFT_ONLY -RequirePacket2nR5Evidence -Packet2nR5EvidencePath $script:packet2nR5EvidencePath -Packet2nR5EvidenceSha256 $script:packet2nR5EvidenceSha256
$packet2nR5RightSummary = Get-Packet2nLeaderMapSummary -Path $packet2nR5PhysicalRightLog -ExpectedMarker PHYSICAL_RIGHT_ONLY -RequirePacket2nR5Evidence -Packet2nR5EvidencePath $script:packet2nR5EvidencePath -Packet2nR5EvidenceSha256 $script:packet2nR5EvidenceSha256
$packet2nR5LeftSummary, $packet2nR5RightSummary | Format-Table -AutoSize
$packet2nR5MappingResult = if ($packet2nR5LeftSummary.LogicalSide -eq 'left' -and $packet2nR5RightSummary.LogicalSide -eq 'right') {
    'MAPPING_RESULT=CORRECT'
} elseif ($packet2nR5LeftSummary.LogicalSide -eq 'right' -and $packet2nR5RightSummary.LogicalSide -eq 'left') {
    'MAPPING_RESULT=REVERSED'
} else {
    'MAPPING_RESULT=AMBIGUOUS'
}
$packet2nR5MappingResult
if ($packet2nR5MappingResult -ne 'MAPPING_RESULT=CORRECT') { throw "Packet 2N-R5 mapping verification failed: $packet2nR5MappingResult" }
```

The evidence-aware verifier requires exactly 60 complete action records, the exact twelve normalized arm keys in every record, and every R5 metadata value exactly once and identical across both logs. It rehashes the evidence, transcript, and current calibration sources. `MAPPING_RESULT=CORRECT` is the only pass. `REVERSED`, `AMBIGUOUS`, 59 or 61 samples, missing/duplicate/unexpected arm data, stale or swapped identities, metadata mismatch, runtime calibration/ZMQ text, nonzero exit, or unclear physical movement is an immediate stop.

Stop either future physical block immediately and remove leader power for unexpected powered leader motion, resistance, sound, heat, cable strain, communication failure, loss of the clear stop path, any follower power or movement, or any evidence that a robot/ZMQ connection was constructed. Preserve the manifest, transcript, evidence JSON, and both map logs. Passing this packet authorizes only later review of that evidence; it does not authorize Pi contact, follower power, ZMQ, startup synchronization, motor-setting changes, or teleoperation.

#### Packet 2M S6 — hard-blocked future both-side synchronization and paused teleoperation

S6 is **not runnable today**. Packet 2N-R5 has not been physically executed, so no reviewed approval artifact, R5 evidence-file hash, physical-left map-log hash, physical-right map-log hash, or corrected current calibration identities exist. The exact next Pi command is **none**: do not contact the Pi, start its host, stage power, open ZMQ, or run any S6 client.

The only executable placeholder in this section refuses unconditionally before any Git, file, serial, network, or power action:

```powershell
$ErrorActionPreference = 'Stop'
throw 'S6 BLOCKED: reviewed Packet 2N-R5 approval artifact and bound evidence/map hashes do not exist'
```

A later, separately reviewed replacement for that placeholder must robustly capture stderr and the exit code for every Git query, require branch `fix/am1-elbow-commissioning`, require a clean status including unexpected untracked files, require behavior baseline `cae57b59db1d9156be568aa4b216fc90701aa741`, and prove every tracked path except this document remains identical to that baseline. It must then require a review-authored approval artifact whose exact schema binds:

- the Packet 2N-R5 evidence JSON path and SHA-256;
- both corrected map-log paths and SHA-256 values;
- the calibration transcript path and SHA-256;
- the current logical-left and logical-right calibration paths, byte counts, SHA-256 values, and UTC mtimes;
- the same R5 session ID and behavior SHA across the artifact, evidence, transcript, and both logs;
- exactly `MAPPING_RESULT=CORRECT` from the strict evidence-aware verifier.

That future preflight must rehash every referenced file, revalidate the two current calibration schemas/identities, and refuse stale, equal, swapped, missing, duplicated, or mismatched evidence. Because the required hashes do not yet exist, no placeholder value may be inferred and no current preflight may pass.

For review context only, any later authorized Windows S6 design must use corrected logical/physical ports **left `COM8` and right `COM7`**, ID `so101_leader_bi`, profile `so-arm-5dof`, AM1 startup synchronization on both sides, client step cap `0.75`, host relative limit `10.0`, final mismatch `6.0`, explicit zero base/lift, start-paused fresh observations, no keyboard/cameras, and the exact post-sync Enter gate. These are blocked design parameters, not a command. No Pi host or Windows teleoperation command is authorized or retained as executable text here.

## 3. Camera Configuration

```bash
lerobot-find-cameras
```

Fill the detected index into `src/lerobot/robots/alohamini/config_alohamini.py`.

> Each camera requires its own USB port — do not share a USB hub between multiple cameras.

---

## 4. Calibration

### Step 1 — Calibrate follower arms (Pi side)

SSH into the Pi and run the calibration script for your model: position each joint at its mechanical midpoint → Enter → rotate 90° left → Enter → rotate 90° right → Enter.

```bash
# AlohaMini 1 (SO-ARM 5-DoF)
python -m lerobot.robots.alohamini.alohamini_calibrate --robot_model alohamini1

# AlohaMini 2 (AM-ARM 6-DoF)
python -m lerobot.robots.alohamini.alohamini_calibrate --robot_model alohamini2

# AlohaMini 2 Pro (AM-ARM 6-DoF, STS3250)
python -m lerobot.robots.alohamini.alohamini_calibrate --robot_model alohamini2pro
```

Starting the host also checks calibration and will prompt this flow automatically if calibration is missing.

SO-ARM 5-DoF reference middle position:

![Calibration SO-ARM](../../examples/alohamini/media/mid_position_so100.png)

### Step 2 — Calibrate leader arms (PC side)

This step connects only to the two leader arms, so the Pi host does not need to be running.

SO-ARM leader (5-DoF):

```bash
python examples/alohamini/calibrate_bi.py \
  --teleop.id so101_leader_bi \
  --teleop.arm_profile so-arm-5dof
```

AM-ARM leader (6-DoF):

```bash
python examples/alohamini/calibrate_bi.py \
  --teleop.id am_leader_bi \
  --teleop.arm_profile am-leader-6dof
```

Use the same `--teleop.id` and `--teleop.arm_profile` for later teleoperation and recording commands so they load the calibration files created here. If a calibration file already exists, press Enter to reuse it or enter `c` to recalibrate.

Running this standalone step is recommended but optional. If it is skipped and no valid calibration is found, `teleoperate_bi.py` keeps the existing behavior: it prompts the user and enters the calibration flow automatically.

> Power-cycle both leader and follower arms after calibration for changes to take effect.

---

## 5. Teleoperation

Start the Pi host first, then the PC client. A valid leader calibration is loaded automatically; if it is missing, the client prompts for calibration before teleoperation starts:

```bash
# Pi — run the host for your robot:
python -m lerobot.robots.alohamini.alohamini_host --robot_model alohamini1
python -m lerobot.robots.alohamini.alohamini_host --robot_model alohamini2
python -m lerobot.robots.alohamini.alohamini_host --robot_model alohamini2pro

# PC — run the client for your leader arm:
python examples/alohamini/teleoperate_bi.py \
  --robot.remote_ip <Pi_IP> \
  --robot.robot_model alohamini1 \
  --teleop.id so101_leader_bi \
  --teleop.arm_profile so-arm-5dof

python examples/alohamini/teleoperate_bi.py \
  --robot.remote_ip <Pi_IP> \
  --robot.robot_model alohamini2 \
  --teleop.id am_leader_bi \
  --teleop.arm_profile am-leader-6dof
```

---

## 6. Dataset Recording

> Make sure the Pi host is already running (§5) before recording.  
> `--teleop.arm_profile` here refers to your **leader arm** hardware, not the follower robot.  
> `--robot.robot_model` must match the model running on the Pi host.  
> Replace `<Pi_IP>` with your Raspberry Pi's IP address.
> `record_bi.py` prints the local dataset path and uploads to Hugging Face Hub by default. Add `--dataset.push_to_hub=false` to keep the dataset local only.
> Add `--dataset.root /path/to/dataset` when you want to store or resume from a specific local directory.

### AlohaMini 1 — SO-ARM leader (5-DoF)

Create new dataset:

```bash
python examples/alohamini/record_bi.py \
  --dataset.repo_id $HF_USER/so100_bi_test \
  --dataset.num_episodes 1 \
  --dataset.fps 30 \
  --dataset.episode_time_s 45 \
  --dataset.reset_time_s 8 \
  --dataset.single_task "pickup1" \
  --robot.remote_ip <Pi_IP> \
  --robot.robot_model alohamini1 \
  --teleop.id so101_leader_bi \
  --teleop.arm_profile so-arm-5dof
```

Resume existing dataset (add `--resume`):

```bash
python examples/alohamini/record_bi.py \
  --dataset.repo_id $HF_USER/so100_bi_test \
  --dataset.num_episodes 1 \
  --dataset.fps 30 \
  --dataset.episode_time_s 45 \
  --dataset.reset_time_s 8 \
  --dataset.single_task "pickup1" \
  --robot.remote_ip <Pi_IP> \
  --robot.robot_model alohamini1 \
  --teleop.id so101_leader_bi \
  --teleop.arm_profile so-arm-5dof \
  --resume
```

### AlohaMini 2 / 2 Pro — AM-ARM leader (6-DoF)

Create new dataset:

```bash
python examples/alohamini/record_bi.py \
  --dataset.repo_id $HF_USER/am2_bi_test \
  --dataset.num_episodes 1 \
  --dataset.fps 30 \
  --dataset.episode_time_s 45 \
  --dataset.reset_time_s 8 \
  --dataset.single_task "pickup1" \
  --robot.remote_ip <Pi_IP> \
  --robot.robot_model alohamini2 \
  --teleop.id am_leader_bi \
  --teleop.arm_profile am-leader-6dof
```

Resume existing dataset (add `--resume`):

```bash
python examples/alohamini/record_bi.py \
  --dataset.repo_id $HF_USER/am2_bi_test \
  --dataset.num_episodes 1 \
  --dataset.fps 30 \
  --dataset.episode_time_s 45 \
  --dataset.reset_time_s 8 \
  --dataset.single_task "pickup1" \
  --robot.remote_ip <Pi_IP> \
  --robot.robot_model alohamini2 \
  --teleop.id am_leader_bi \
  --teleop.arm_profile am-leader-6dof \
  --resume
```

---

## 7. Dataset Replay

```bash
python examples/alohamini/replay_bi.py \
  --dataset.repo_id $HF_USER/am2_bi_test \
  --dataset.episode 0 \
  --robot.remote_ip <Pi_IP> \
  --robot.robot_model alohamini2
```

If the dataset is not under `$HF_LEROBOT_HOME/$HF_USER/am2_bi_test`, add `--dataset.root /path/to/am2_bi_test`.

---

## 8. Dataset Visualization

```bash
lerobot-dataset-viz \
  --repo-id $HF_USER/am2_bi_test \
  --episode-index 0 \
  --display-compressed-images
```

---

## 9. Training

### Local training

```bash
lerobot-train \
  --dataset.repo_id=$HF_USER/am2_bi_test \
  --policy.type=act \
  --output_dir=outputs/train/act_your_dataset1 \
  --job_name=act_your_dataset \
  --policy.device=cuda \
  --wandb.enable=false \
  --policy.repo_id=$HF_USER/act_policy \
  --dataset.video_backend=pyav
```

### No local GPU?

Use any cloud GPU provider (e.g. AutoDL, Lambda Labs, Vast.ai). Set up the environment the same way as local, run the same training command, then copy the checkpoint back to your machine for evaluation.

---

## 10. Evaluation

Make sure the Pi host is already running (§5), then run inference from the PC.

> `--robot.robot_model` must match the model running on the Pi host:  
> `alohamini1` (SO-ARM 5-DoF, 16-dim state) · `alohamini2` / `alohamini2pro` (AM-ARM 6-DoF, 18-dim state)

### `evaluate_bi.py` (custom script, N episodes)

ACT uses synchronous inference. The interpolation multiplier below runs the robot control loop at
`fps × multiplier` (20 × 3 = 60 Hz after the first action) and linearly interpolates between policy actions.

```bash
python examples/alohamini/evaluate_bi.py \
  --eval.n_episodes 3 \
  --fps 20 \
  --eval.episode_time_s 45 \
  --dataset.single_task "Pick and place task" \
  --policy.path outputs/train/act_your_dataset1/checkpoints/020000/pretrained_model \
  --dataset.repo_id $HF_USER/eval_act_policy \
  --dataset.push_to_hub=false \
  --robot.remote_ip <Pi_IP> \
  --robot.id my_alohamini \
  --robot.robot_model alohamini2 \
  --inference.type sync \
  --interpolation_multiplier 3
```

SmolVLA supports Real-Time Chunking (RTC), which runs policy inference asynchronously and refreshes
part of the action chunk while the robot executes queued actions:

```bash
python examples/alohamini/evaluate_bi.py \
  --eval.n_episodes 3 \
  --fps 20 \
  --eval.episode_time_s 45 \
  --dataset.single_task "Pick and place task" \
  --policy.path outputs/train/smolvla_your_dataset1/checkpoints/020000/pretrained_model \
  --dataset.repo_id $HF_USER/eval_smolvla_policy \
  --dataset.push_to_hub=false \
  --robot.remote_ip <Pi_IP> \
  --robot.id my_alohamini \
  --robot.robot_model alohamini2 \
  --inference.type rtc \
  --inference.rtc.execution_horizon 10 \
  --inference.rtc.max_guidance_weight 10.0 \
  --inference.rtc.queue_threshold 30 \
  --interpolation_multiplier 1
```

> Both examples load a local checkpoint and save the evaluation dataset locally without uploading it.
> Make sure the `--policy.path` directory exists and contains the complete pretrained model. Set
> `HF_USER` before running (or replace `$HF_USER` with your username), and use a new
> `--dataset.repo_id` for every evaluation because its local output directory must not already exist.
> ACT does not support RTC; keep `--inference.type sync` for ACT. Replace `<Pi_IP>` with the Pi's IP,
> and change `--robot.robot_model` if the Pi host is running `alohamini1` or `alohamini2pro`.

---

## 11. Debug

See [Debug Command Summary](../../examples/debug/README.md) for the full list of debugging utilities.
