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

The strict verifier is retained unchanged for the historical logs and the future Packet 2N-R5 corrected-port logs. It requires the marker, no-robot notice, absence of any `ZMQ` text, normal cleanup, exit `0`, all twelve arm keys, a moved gripper span of at least 20 normalized units, and less than `2.0` variation across the entire opposite logical family:

```powershell
$ErrorActionPreference = 'Stop'
function Get-Packet2nLeaderMapSummary {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [ValidateSet('PHYSICAL_LEFT_ONLY', 'PHYSICAL_RIGHT_ONLY')] [string] $ExpectedMarker
    )

    $lines = @(Get-Content -LiteralPath $Path)
    if ($lines.Count -lt 3 -or $lines[0].Trim() -ne "MAP_RUN=$ExpectedMarker") { throw "invalid marker in $Path" }
    if ($lines[-1].Trim() -ne 'CLIENT_EXIT_CODE=0') { throw "missing successful exit in $Path" }
    if (-not ($lines | Where-Object { $_ -eq 'NO_ROBOT: robot client construction and connection skipped.' })) { throw "missing NO_ROBOT proof in $Path" }
    if (-not ($lines | Where-Object { $_ -like 'Shutdown complete:*' })) { throw "missing cleanup proof in $Path" }
    if ($lines -match 'ZMQ') { throw "unexpected ZMQ text in $Path" }
    if ($lines -match '(?i)calibrat') { throw "calibration text requires refusal and review: $Path" }

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
    $pattern = "'(?<key>arm_(?:left|right)_[^']+\.pos)':\s*(?<value>$number)"
    foreach ($line in $lines) {
        if (-not $line.StartsWith('[NO_ROBOT] action ->')) { continue }
        $matches = @([regex]::Matches($line, $pattern))
        $recordKeys = @($matches | ForEach-Object { $_.Groups['key'].Value })
        $missingRecordKeys = @($expectedKeys | Where-Object { $_ -notin $recordKeys })
        $unexpectedRecordKeys = @($recordKeys | Where-Object { $_ -notin $expectedKeys })
        if ($matches.Count -ne 12 -or $missingRecordKeys.Count -ne 0 -or $unexpectedRecordKeys.Count -ne 0) {
            throw "incomplete or unexpected arm action record in $Path"
        }
        $completeRecordCount++
        foreach ($match in $matches) {
            $key = $match.Groups['key'].Value
            if (-not $series.ContainsKey($key)) { $series[$key] = @() }
            $series[$key] += [double] $match.Groups['value'].Value
        }
    }
    if ($completeRecordCount -lt 1) { throw "no complete arm action records in $Path" }
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

This Packet 2N-R5 task made no COM, leader, follower, Pi, ZMQ, network, or calibration connection. It made a verified **copy-only** offline backup while hardware remained disconnected:

```text
Backup directory: C:\Users\pickm\AlohaMini1Backups\packet2n-r5-20260822-121722-7941f445-9587-4345-8e2f-edd54ca750f6
Manifest: C:\Users\pickm\AlohaMini1Backups\packet2n-r5-20260822-121722-7941f445-9587-4345-8e2f-edd54ca750f6\manifest.json
```

| Original file | Bytes | SHA-256 |
| --- | ---: | --- |
| `so101_leader_bi_left.json` | 960 | `6F5D6126E84398D0621A26E74E4DF6678EBA7C14C62D343020610B4D5D8B3D8C` |
| `so101_leader_bi_right.json` | 961 | `65A301F20FC7DC96BD7FB5982E3670BF1A01F535953D7A253AB8D33A03646F11` |

The manifest records each original and backup path, SHA-256, byte count, and source UTC last-write time. Both backup hashes matched their source hashes, and both original paths and hashes were rechecked unchanged after copying. This backup does not authorize restoring, moving, deleting, renaming, or editing either original; any rollback must be separately reviewed and use this manifest as evidence.

The only approved later correction is a coordinated **full recalibration**, after a separate physical authorization: logical/physical left must be `COM8`, logical/physical right must be `COM7`, the ID must be `so101_leader_bi`, and the profile must be `so-arm-5dof`. Do not do a port-only swap, JSON-content swap, or runtime swap layer. Begin with the Pi motor host stopped, follower/body 12 V power off, both leader supplies off, and both leader USB controllers disconnected. Do not run this task's future commands until physical authorization is granted.

**Future corrected-port full recalibration — fail fast.** The guard below is part of every future connection attempt. It rejects all Hugging Face calibration/home overrides, resolves and pins the evidence default calibration root, and validates the manifest's exact original/backup paths, bytes, and hashes. Before calibration, it requires both current sources and both backups to retain the verified pre-calibration hashes. After calibration, it continues to require the manifest and backups unchanged but requires the current sources to match freshly captured same-session post-calibration evidence instead of the old hashes. It also requires the reviewed `f7e8254c80fffe8c215920d6928718b1f482f7a6` baseline as an ancestor and requires every calibration/leader-client source path listed in the guard to be byte-identical to that baseline; this non-self-referential source review remains valid on later reviewed documentation commits. After physical authorization and only with the operator's clear stop path, run exactly this command. At **both** existing-file prompts, verify the shown child identity matches the expected logical child and type exactly `c` (lowercase, then Enter) to force its full recalibration. Stop immediately—without accepting the existing file—on any unexpected prompt, child ID, port, profile, connection/calibration error, or safety concern. Do not continue to no-robot verification after any mismatch.

```powershell
$ErrorActionPreference = 'Stop'
$script:packet2nR5PostCalibrationEvidence = $null
function Assert-Packet2nR5CommonGuard {
    $packet2nBaseline = 'f7e8254c80fffe8c215920d6928718b1f482f7a6'
    $packet2nCalibrationRoot = 'C:\Users\pickm\.cache\huggingface\lerobot\calibration'
    $packet2nBackupDirectory = 'C:\Users\pickm\AlohaMini1Backups\packet2n-r5-20260822-121722-7941f445-9587-4345-8e2f-edd54ca750f6'
    $packet2nManifestPath = Join-Path $packet2nBackupDirectory 'manifest.json'
    $packet2nFiles = @(
        [pscustomobject]@{ Name = 'so101_leader_bi_left.json'; Bytes = 960; Sha256 = '6F5D6126E84398D0621A26E74E4DF6678EBA7C14C62D343020610B4D5D8B3D8C' },
        [pscustomobject]@{ Name = 'so101_leader_bi_right.json'; Bytes = 961; Sha256 = '65A301F20FC7DC96BD7FB5982E3670BF1A01F535953D7A253AB8D33A03646F11' }
    )
    if ((git branch --show-current) -ne 'fix/am1-elbow-commissioning') { throw 'wrong Windows branch' }
    if (git status --porcelain) { throw 'Windows worktree is not clean' }
    git merge-base --is-ancestor $packet2nBaseline HEAD
    if ($LASTEXITCODE -ne 0) { throw "Packet 2N-R5 baseline is not an ancestor: $packet2nBaseline" }
    $packet2nRelevantSources = @(
        'examples/alohamini/calibrate_bi.py',
        'examples/alohamini/teleoperate_bi.py',
        'examples/alohamini/leader_client_utils.py',
        'src/lerobot/teleoperators/bi_so_leader/bi_so_leader.py',
        'src/lerobot/teleoperators/bi_so_leader/config_bi_so_leader.py',
        'src/lerobot/teleoperators/so_leader/so_leader.py',
        'src/lerobot/teleoperators/so_leader/config_so_leader.py'
    )
    git diff --quiet $packet2nBaseline -- $packet2nRelevantSources
    if ($LASTEXITCODE -ne 0) { throw 'reviewed Packet 2N-R5 source paths differ from the baseline' }
    if ($env:HF_LEROBOT_CALIBRATION -or $env:HF_LEROBOT_HOME -or $env:HF_HOME) { throw 'HF calibration/home environment overrides must be unset' }
    $packet2nResolvedRoot = (& .\.venv\Scripts\python.exe -c "from lerobot.utils.constants import HF_LEROBOT_CALIBRATION; print(HF_LEROBOT_CALIBRATION)" | Out-String).Trim()
    if ($packet2nResolvedRoot -ne $packet2nCalibrationRoot) { throw "unexpected calibration root: $packet2nResolvedRoot" }
    if (-not (Test-Path -LiteralPath $packet2nManifestPath -PathType Leaf)) { throw "missing Packet 2N-R5 manifest: $packet2nManifestPath" }
    $packet2nManifest = Get-Content -Raw -LiteralPath $packet2nManifestPath | ConvertFrom-Json
    if ($packet2nManifest.Packet -ne '2N-R5' -or $packet2nManifest.BackupDirectory -ne $packet2nBackupDirectory -or -not $packet2nManifest.CopyOnly -or -not $packet2nManifest.HardwareDisconnected) { throw 'Packet 2N-R5 manifest identity or safety fields differ' }
    foreach ($packet2nFile in $packet2nFiles) {
        $packet2nSource = Join-Path $packet2nCalibrationRoot "teleoperators\so_leader\$($packet2nFile.Name)"
        $packet2nBackup = Join-Path $packet2nBackupDirectory $packet2nFile.Name
        $packet2nManifestEntry = @($packet2nManifest.Files | Where-Object { $_.OriginalPath -eq $packet2nSource })
        if ($packet2nManifestEntry.Count -ne 1 -or $packet2nManifestEntry[0].BackupPath -ne $packet2nBackup -or $packet2nManifestEntry[0].Sha256 -ne $packet2nFile.Sha256 -or [int64]$packet2nManifestEntry[0].Bytes -ne $packet2nFile.Bytes) { throw "manifest entry mismatch: $($packet2nFile.Name)" }
        if (-not (Test-Path -LiteralPath $packet2nBackup -PathType Leaf)) { throw "missing calibration backup: $packet2nBackup" }
        $packet2nBackupItem = Get-Item -LiteralPath $packet2nBackup
        if ($packet2nBackupItem.Length -ne $packet2nFile.Bytes -or (Get-FileHash -LiteralPath $packet2nBackup -Algorithm SHA256).Hash -ne $packet2nFile.Sha256) { throw "calibration backup hash/size mismatch: $packet2nBackup" }
    }
}
function Assert-Packet2nR5PreCalibrationGuard {
    Assert-Packet2nR5CommonGuard
    $packet2nCalibrationRoot = 'C:\Users\pickm\.cache\huggingface\lerobot\calibration'
    $packet2nFiles = @(
        [pscustomobject]@{ Name = 'so101_leader_bi_left.json'; Bytes = 960; Sha256 = '6F5D6126E84398D0621A26E74E4DF6678EBA7C14C62D343020610B4D5D8B3D8C' },
        [pscustomobject]@{ Name = 'so101_leader_bi_right.json'; Bytes = 961; Sha256 = '65A301F20FC7DC96BD7FB5982E3670BF1A01F535953D7A253AB8D33A03646F11' }
    )
    foreach ($packet2nFile in $packet2nFiles) {
        $packet2nSource = Join-Path $packet2nCalibrationRoot "teleoperators\so_leader\$($packet2nFile.Name)"
        if (-not (Test-Path -LiteralPath $packet2nSource -PathType Leaf)) { throw "missing pre-calibration source: $packet2nSource" }
        $packet2nSourceItem = Get-Item -LiteralPath $packet2nSource
        if ($packet2nSourceItem.Length -ne $packet2nFile.Bytes -or (Get-FileHash -LiteralPath $packet2nSource -Algorithm SHA256).Hash -ne $packet2nFile.Sha256) { throw "pre-calibration source hash/size mismatch: $packet2nSource" }
    }
}
function Save-Packet2nR5PostCalibrationEvidence {
    Assert-Packet2nR5CommonGuard
    $packet2nCalibrationRoot = 'C:\Users\pickm\.cache\huggingface\lerobot\calibration'
    $packet2nManifestPath = 'C:\Users\pickm\AlohaMini1Backups\packet2n-r5-20260822-121722-7941f445-9587-4345-8e2f-edd54ca750f6\manifest.json'
    $packet2nManifest = Get-Content -Raw -LiteralPath $packet2nManifestPath | ConvertFrom-Json
    $packet2nNames = @('so101_leader_bi_left.json', 'so101_leader_bi_right.json')
    $script:packet2nR5PostCalibrationEvidence = @(
        foreach ($packet2nName in $packet2nNames) {
            $packet2nSource = Join-Path $packet2nCalibrationRoot "teleoperators\so_leader\$packet2nName"
            if (-not (Test-Path -LiteralPath $packet2nSource -PathType Leaf)) { throw "missing post-calibration source: $packet2nSource" }
            $packet2nSourceItem = Get-Item -LiteralPath $packet2nSource
            $packet2nManifestEntry = @($packet2nManifest.Files | Where-Object { $_.OriginalPath -eq $packet2nSource })
            if ($packet2nManifestEntry.Count -ne 1 -or $packet2nSourceItem.LastWriteTimeUtc -le [datetime]$packet2nManifestEntry[0].SourceLastWriteTimeUtc) { throw "post-calibration rewrite was not proven: $packet2nSource" }
            [pscustomobject]@{ Name = $packet2nName; Path = $packet2nSource; Bytes = [int64]$packet2nSourceItem.Length; Sha256 = (Get-FileHash -LiteralPath $packet2nSource -Algorithm SHA256).Hash; LastWriteTimeUtc = $packet2nSourceItem.LastWriteTimeUtc.ToString('o') }
        }
    )
    if ($script:packet2nR5PostCalibrationEvidence.Count -ne 2) { throw 'post-calibration evidence did not capture both sources' }
}
function Assert-Packet2nR5PostCalibrationGuard {
    Assert-Packet2nR5CommonGuard
    if ($null -eq $script:packet2nR5PostCalibrationEvidence -or $script:packet2nR5PostCalibrationEvidence.Count -ne 2) { throw 'missing same-session post-calibration evidence' }
    $packet2nCalibrationRoot = 'C:\Users\pickm\.cache\huggingface\lerobot\calibration'
    $packet2nManifestPath = 'C:\Users\pickm\AlohaMini1Backups\packet2n-r5-20260822-121722-7941f445-9587-4345-8e2f-edd54ca750f6\manifest.json'
    $packet2nManifest = Get-Content -Raw -LiteralPath $packet2nManifestPath | ConvertFrom-Json
    $packet2nNames = @('so101_leader_bi_left.json', 'so101_leader_bi_right.json')
    foreach ($packet2nName in $packet2nNames) {
        $packet2nSource = Join-Path $packet2nCalibrationRoot "teleoperators\so_leader\$packet2nName"
        $packet2nEvidence = @($script:packet2nR5PostCalibrationEvidence | Where-Object { $_.Name -eq $packet2nName })
        $packet2nManifestEntry = @($packet2nManifest.Files | Where-Object { $_.OriginalPath -eq $packet2nSource })
        if ($packet2nEvidence.Count -ne 1 -or $packet2nManifestEntry.Count -ne 1 -or $packet2nEvidence[0].Path -ne $packet2nSource -or -not (Test-Path -LiteralPath $packet2nSource -PathType Leaf)) { throw "invalid post-calibration evidence: $packet2nName" }
        $packet2nSourceItem = Get-Item -LiteralPath $packet2nSource
        $packet2nSourceHash = (Get-FileHash -LiteralPath $packet2nSource -Algorithm SHA256).Hash
        if ($packet2nSourceItem.LastWriteTimeUtc -le [datetime]$packet2nManifestEntry[0].SourceLastWriteTimeUtc -or $packet2nSourceItem.Length -ne [int64]$packet2nEvidence[0].Bytes -or $packet2nSourceHash -ne $packet2nEvidence[0].Sha256 -or $packet2nSourceItem.LastWriteTimeUtc.ToString('o') -ne $packet2nEvidence[0].LastWriteTimeUtc) { throw "post-calibration source changed after capture: $packet2nSource" }
    }
}
Assert-Packet2nR5PreCalibrationGuard
& .\.venv\Scripts\python.exe `
  .\examples\alohamini\calibrate_bi.py `
  --teleop.left_port COM8 `
  --teleop.right_port COM7 `
  --teleop.id so101_leader_bi `
  --teleop.arm_profile so-arm-5dof
if ($LASTEXITCODE -ne 0) { throw "corrected-port full recalibration failed with $LASTEXITCODE" }
Save-Packet2nR5PostCalibrationEvidence
```

After a zero-exit full recalibration, the workflow requires both current source `LastWriteTimeUtc` values to be strictly newer than their manifest `SourceLastWriteTimeUtc` values, proving the two logical JSONs were rewritten rather than silently reused. It then captures exactly the pinned-root, two-file post-calibration paths, byte counts, SHA-256 values, and UTC last-write times into the session-local `$script:packet2nR5PostCalibrationEvidence`. The calibration and both marked no-robot blocks are one guarded workflow: run them in the same PowerShell session and in the printed order. Each no-robot block reruns common branch/source-baseline/root/manifest/backup checks and then requires that session evidence and exact current agreement with its post-calibration hash, size, and UTC last-write time. A fresh session, missing capture, unchanged pre-calibration timestamp, stale evidence, or later calibration-source mutation fails before creating a robot/leader connection; the post-calibration sources are intentionally not compared to their pre-calibration hashes. The runs construct no robot client, send no ZMQ request, and must never be used to start follower motion, Pi communication, or startup synchronization. Move only the named physical leader after its Enter prompt; keep the other leader still. Stop immediately for any calibration prompt, unexpected powered motion, resistance, sound, heat, cable strain, communication failure, loss of the clear stop path, follower power/movement, or evidence that a robot/ZMQ connection was constructed.

**Future corrected-port physical-left-only no-robot run (`COM8`).**

```powershell
$ErrorActionPreference = 'Stop'
Assert-Packet2nR5PostCalibrationGuard
$packet2nCorrectedMapDir = 'C:\Users\pickm\AlohaMini1Logs'
New-Item -ItemType Directory -Force -Path $packet2nCorrectedMapDir | Out-Null
$packet2nCorrectedTimestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$packet2nCorrectedPhysicalLeftLog = Join-Path $packet2nCorrectedMapDir "packet2n-corrected-port-physical-left-only-$packet2nCorrectedTimestamp.log"
'MAP_RUN=PHYSICAL_LEFT_ONLY' | Tee-Object -FilePath $packet2nCorrectedPhysicalLeftLog
& .\.venv\Scripts\python.exe `
  .\examples\alohamini\teleoperate_bi.py `
  --no_robot `
  --robot.robot_model alohamini1 `
  --teleop.left_port COM8 `
  --teleop.right_port COM7 `
  --teleop.id so101_leader_bi `
  --teleop.arm_profile so-arm-5dof `
  --duration_s 12 `
  --fps 5 `
  --start_paused `
  --no_keyboard `
  --no_rerun 2>&1 | Tee-Object -FilePath $packet2nCorrectedPhysicalLeftLog -Append
$packet2nCorrectedPhysicalLeftExitCode = $LASTEXITCODE
"CLIENT_EXIT_CODE=$packet2nCorrectedPhysicalLeftExitCode" | Tee-Object -FilePath $packet2nCorrectedPhysicalLeftLog -Append
if ($packet2nCorrectedPhysicalLeftExitCode -ne 0) { throw "corrected physical-left map failed with $packet2nCorrectedPhysicalLeftExitCode" }
```

**Future corrected-port physical-right-only no-robot run (`COM7`).**

```powershell
$ErrorActionPreference = 'Stop'
Assert-Packet2nR5PostCalibrationGuard
$packet2nCorrectedMapDir = 'C:\Users\pickm\AlohaMini1Logs'
New-Item -ItemType Directory -Force -Path $packet2nCorrectedMapDir | Out-Null
$packet2nCorrectedTimestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$packet2nCorrectedPhysicalRightLog = Join-Path $packet2nCorrectedMapDir "packet2n-corrected-port-physical-right-only-$packet2nCorrectedTimestamp.log"
'MAP_RUN=PHYSICAL_RIGHT_ONLY' | Tee-Object -FilePath $packet2nCorrectedPhysicalRightLog
& .\.venv\Scripts\python.exe `
  .\examples\alohamini\teleoperate_bi.py `
  --no_robot `
  --robot.robot_model alohamini1 `
  --teleop.left_port COM8 `
  --teleop.right_port COM7 `
  --teleop.id so101_leader_bi `
  --teleop.arm_profile so-arm-5dof `
  --duration_s 12 `
  --fps 5 `
  --start_paused `
  --no_keyboard `
  --no_rerun 2>&1 | Tee-Object -FilePath $packet2nCorrectedPhysicalRightLog -Append
$packet2nCorrectedPhysicalRightExitCode = $LASTEXITCODE
"CLIENT_EXIT_CODE=$packet2nCorrectedPhysicalRightExitCode" | Tee-Object -FilePath $packet2nCorrectedPhysicalRightLog -Append
if ($packet2nCorrectedPhysicalRightExitCode -ne 0) { throw "corrected physical-right map failed with $packet2nCorrectedPhysicalRightExitCode" }
```

After both clients disconnect, switch off both 7.4 V leader supplies and disconnect both leader buses. Reuse the existing strict verifier above without weakening it: paste the exact corrected physical-left log and corrected physical-right log paths. The result must be exactly `MAPPING_RESULT=CORRECT`; `REVERSED`, `AMBIGUOUS`, any missing marker, nonzero exit, ZMQ/calibration text, incomplete sample, or unexpected logical family is an immediate stop and review. Passing means only that both corrected-port full recalibrations completed and the strict no-robot verifier returned `MAPPING_RESULT=CORRECT`; it does not authorize follower power, Pi contact, ZMQ, startup synchronization, motor-setting changes, or teleoperation.

Rollback and stop conditions are deliberately conservative: retain the verified copies and manifest; do not restore or modify calibration originals in the field. On a calibration/port/identity mismatch or failed verifier, remove leader power, disconnect both buses, leave follower/body power off, preserve logs, and obtain a separately reviewed recovery decision. Packet 2M S6 remains explicitly blocked until corrected-port recalibration and the two no-robot runs produce `MAPPING_RESULT=CORRECT` and the evidence is reviewed.

#### Packet 2M S6 — blocked future both-side synchronization and paused teleoperation

This is a future combined AM1 session for arbitrary safe calibrated initial poses. It remains blocked until Packet 2N-R5 corrected-port full recalibration and both no-robot verification runs produce `MAPPING_RESULT=CORRECT` and the evidence is reviewed. Do not run the commands in this section yet. When eventually authorized, keep both leaders still through exact `SYNC`, synchronization verification, the subsequent Enter pause, and until the client prints exactly `TELEOPERATION ACTIVE — LEADER MOVEMENT IS NOW ALLOWED`.

Start with the Pi motor host stopped, follower/body 12 V power off, both leader supplies off, and both leader USB controllers disconnected. Clear and support both arm workspaces, keep the physical follower-power disconnect immediately accessible, and run both source preflights below while all motor power remains off. Only after both preflights pass may the operator connect the two known leader USB controllers, apply each leader's designated 7.4 V supply, apply follower/body 12 V power, and start the Pi host. Never apply the follower 12 V supply to a leader.

**Pi source preflight.** On the Pi, require the safe-bringup branch, a clean worktree, and exact baseline `a8538bd79356b4c5263342aba389dcdf39092e9e`:

```bash
set -eu
cd /home/pickmanmike/lerobot_alohamini
test "$(git branch --show-current)" = fix/am1-safe-bringup
test -z "$(git status --porcelain)"
test "$(git rev-parse HEAD)" = a8538bd79356b4c5263342aba389dcdf39092e9e
```

**Windows preflight.** Require the current commissioning branch, a clean worktree, and the reviewed Packet 2M Windows baseline as an ancestor:

```powershell
$ErrorActionPreference = 'Stop'
if ((git branch --show-current) -ne 'fix/am1-elbow-commissioning') { throw 'wrong Windows branch' }
if (git status --porcelain) { throw 'Windows worktree is not clean' }
git merge-base --is-ancestor f11b74d4184afafbf044ace7ec5423617da96553 HEAD
if ($LASTEXITCODE -ne 0) { throw 'Packet 2M Windows baseline is not an ancestor' }
```

**Pi host command after the two preflights and authorized power staging.** Start this one host and leave it running for the Windows client. The log tee preserves the normal host output; this is not a trace run.

```bash
set -eu
set -o pipefail
cd /home/pickmanmike/lerobot_alohamini
test "$(git branch --show-current)" = fix/am1-safe-bringup
test -z "$(git status --porcelain)"
test "$(git rev-parse HEAD)" = a8538bd79356b4c5263342aba389dcdf39092e9e
HOST_LOG="/home/pickmanmike/packet2m-am1-host-$(date +%Y%m%d-%H%M%S).log"
printf 'HOST_LOG=%s\n' "$HOST_LOG"
./.venv/bin/python -m lerobot.robots.alohamini.alohamini_host \
  --robot_model alohamini1 \
  --no_cameras \
  --skip_lift_home \
  --max_relative_target 10.0 \
  --max_loop_freq_hz 30 2>&1 | tee "$HOST_LOG"
```

`--no_cameras` keeps cameras absent. `--skip_lift_home` leaves the lift unhomed and its movement blocked. The `10.0` host limiter remains secondary to the client frame cap `STARTUP_SYNC_MAX_STEP = 0.75`; it does not relax that cap.

**Windows command.** With the Pi host ready, run exactly this one client session. It writes the normal client output to a timestamped Windows log, retains the terminal session, and captures the exact client exit code:

```powershell
$packet2mLogDir = 'C:\Users\pickm\AlohaMini1Logs'
New-Item -ItemType Directory -Force -Path $packet2mLogDir | Out-Null
$packet2mTimestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$packet2mLog = Join-Path $packet2mLogDir "packet2m-am1-client-$packet2mTimestamp.log"
& .\.venv\Scripts\python.exe `
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
  --max_start_mismatch 6.0 `
  --fps 5 `
  --duration_s 60 `
  --start_paused `
  --no_keyboard `
  --no_rerun 2>&1 | Tee-Object -FilePath $packet2mLog
$packet2mExitCode = $LASTEXITCODE
"CLIENT_LOG=$packet2mLog" | Tee-Object -FilePath $packet2mLog -Append
"CLIENT_EXIT_CODE=$packet2mExitCode" | Tee-Object -FilePath $packet2mLog -Append
```

Arm-only here means both-arm position targets plus explicit zero base/lift commands, not omission of the supported zero-velocity fields. No keyboard, camera, or visualization device is started. Synchronization verifies the frozen target before the pause. After Enter, the client again requires a fresh follower observation proven by sequence advancement and a fresh normalized leader sample. It validates and re-compares every joint, then forwards that final validated leader sample first with zero base/lift commands. The ordinary `--duration_s` clock starts only after synchronization, optional resource setup, and this pause gate.

Completion requires client status `0` after the bounded 60-second live interval, all four operator phases in order, no safety refusal, and the normal final zero command and disconnect cleanup. During the live interval, require small, one-at-a-time, correct corresponding movement from every left/right arm joint and both grippers; base and lift must remain stationary; and the live negative-direction left-elbow response must be practically controllable. Immediately stop and remove power or disconnect if any commanded joint is stationary, moves in the wrong direction, moves unexpectedly fast, has unusable lag, or if any arm, base, or lift moves unexpectedly; also stop for resistance, contact, noise, cable tension, current or communication error, a required phase/validation/cleanup failure, or loss of a clear view or stop path. After the client cleanup, stop the Pi host with Ctrl+C, verify its cleanup, then power off. After completion or any stop, end this Packet 2M session and authorize no further motion.

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
