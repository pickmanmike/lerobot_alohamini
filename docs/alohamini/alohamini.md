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

Windows requires both ports explicitly. COM numbers are runtime addresses, not physical identities. Keep the physical/logical left controller in `LEFT-LABELED-SOCKET` and the physical/logical right controller in `RIGHT-LABELED-SOCKET` on the dedicated hub. The verified map currently resolves those controllers to `COM8` and `COM7`, respectively, but every command below uses the ports discovered for that session.

The read-only helper reports every present CH343 COM port, FriendlyName, PnP InstanceId, and status without opening a port. It may select roles only when both stored PnP identities each have one unique current match:

```powershell
$portMapFile = 'C:\Users\pickm\AlohaMini1Logs\am1-leader-hub-map-20260831-140827.json'
$portReport = (& pwsh -NoLogo -NoProfile -File .\tools\report_am1_leader_ports.ps1 -PortMapFile $portMapFile -AsJson | Out-String) | ConvertFrom-Json
if (-not $portReport.auto_selection_permitted) { throw 'Stored leader PnP identities did not match uniquely; do not open either port.' }
$LeftPort = [string]$portReport.role_matches.left.port
$RightPort = [string]$portReport.role_matches.right.port
if ($LeftPort -eq $RightPort) { throw 'Leader ports are not distinct.' }
"LEFT_PORT=$LeftPort RIGHT_PORT=$RightPort"
```

If either identity is missing, duplicated, ambiguous, or attached to the wrong labeled socket, stop. Do not infer a role from the COM number, swap only port arguments, rename calibration files, or exchange JSON contents.

### Lean AM1 local operation and physically proven lift

`integrate/am1-local-teleop` at `07bda8ad036de2dffdaa3493a4a30428ab0f06b8` is the canonical shared branch for the physically validated local arm, base, and lift-only system. It contains both immutable validation inputs: Windows `30609a4597b8b6fca49bc1018024fd29dfb55127` and Pi `ee3a6f5dd813be82780a6a9b1789966357542d2f`.

Local bimanual arms are physically proven. In the accepted run, all twelve channels moved on the correct side and in the correct direction, startup synchronization's maximum final mismatch was `5.534`, and live control sent 449 actions over 44.990 seconds (`9.980 Hz`) with a longest interval of 110 ms. Two transient observation timeouts recovered, `stale_latched` remained false, no command-watchdog event occurred during live forwarding, base and lift remained stationary, and both processes exited `0`. Evidence is retained at:

- `C:\Users\pickm\AlohaMini1Logs\am1-ar1-r2h-retry-bimanual10-windows-20260831-235812.log`
- `C:\Users\pickm\AlohaMini1Logs\am1-ar1-r2h-retry-bimanual10-host-20260831-234021.log`

The regulated 12 V / 10 A supply is proven for this arms-only result. It is not yet proven for complete simultaneous arm, base, and lift operation.

Local base teleoperation is also physically proven. The elevated-wheel checks established opposing `W/S`, `Z/X`, and `A/D` wheel patterns; the floor check established the correct six chassis directions; every release stopped promptly; and the arms and lift remained stationary. Both Windows base sessions exited `0`. The final no-motion shutdown-only check pressed no movement key, left wheels, arms, and lift stationary, and recorded both `AM1_CLIENT_EXIT_CODE=0` and `HOST_EXIT_CODE=0`. Evidence is retained at:

- `C:\Users\pickm\AlohaMini1Logs\am1-base-windows-20260903-210147.log`
- `C:\Users\pickm\AlohaMini1Logs\am1-base-windows-20260903-210429.log`
- `C:\Users\pickm\lerobot_alohamini_client\.worktrees\am1-base-teleop\am1-base-host-20260903-210104.log`
- `C:\Users\pickm\AlohaMini1Logs\am1-base-windows-20260904-215623.log`
- `/home/pickmanmike/AlohaMini1Logs/am1-base-host-20260904-215432.log`

Local lift teleoperation is physically proven in one bounded lift-only session. Homing reached the actual lower stop and stopped; `U` raised the carriage by roughly 2–3 cm and release stopped it; `J` lowered it by roughly 1 cm and release stopped it; the wheels and follower arms remained stationary; and both processes exited `0`. Host commands advanced from sequence 2 through 196 at roughly 100.2 ms intervals without a live watchdog event. The three host watchdog warnings were outside live motion: before the client, while waiting at the Enter gate after sequence 1, and after the client exited. The one client observation timeout also occurred before lift-only live mode. Evidence is retained at:

- `C:\Users\pickm\AlohaMini1Logs\am1-lift-windows-20260904-234200.log`
- `C:\Users\pickm\AlohaMini1Logs\am1-lift-host-20260904-234125.log` (downloaded from `/home/pickmanmike/AlohaMini1Logs/am1-lift-host-20260904-234125.log`)

The reviewed lean launchers replace the long ordinary host/client command blocks. They support `arms`/`base`/`lift`/`local` on the Pi and `Arms`/`Base`/`Lift`/`Local` on Windows. Copy the tracked example once, edit its machine-local paths, and leave the resulting file ignored by Git:

```powershell
Copy-Item .\config\am1.local.example.json .\config\am1.local.json
```

Each launcher verifies a clean reviewed worktree and the repository import root, prints the exact Python command, creates a timestamped log, and prints the child exit code. Arms and Local modes additionally resolve both leader ports from stored PnP identities and verify the accepted calibration hashes. Base and Lift modes have no leader-map, calibration, or COM path.

The first lift packet uses only:

```bash
./tools/run_am1_host.sh --mode lift
```

```powershell
.\tools\run_am1.ps1 -Mode Lift
```

The Pi command uses `--no_follower --no_cameras` and deliberately omits `--skip_lift_home`: it constructs only the left body bus, constructs no follower arms or right bus, and homes the lift exactly once before ordinary host operation. AM1 positive raw lift velocity is physically down. The existing `dir_sign=-1` maps logical `+200` to raw `-200` for physical up and logical `-200` to raw `+200` for physical down. Homing drives raw `+200` downward, requests zero before capturing a process-local zero, and does not claim that zero survives reconnect or process restart. There is no commanded backoff from the lower stop, so sustained load, abnormal current, or heating there is a physical refusal requiring review.

The Windows command uses `--lift_only --no_leader --start_paused --no_cameras --no_rerun --fps 10 --duration_s 30`: it constructs no leader or COM device, requests no observations in the action loop, accepts only `U` and `J`, and sends exact zero `x.vel`, `y.vel`, and `theta.vel` on every action. `U` requests bounded physical up, `J` requests bounded physical down, simultaneous or released lift keys request zero, and base/speed keys are ignored. Sends remain completion-spaced, the Pi watchdog remains one second, and `Q`, the 30-second bound, or an exception enters final-zero-before-disconnect cleanup.

The unchanged lift controller refuses downward velocity at or below its real `5.0 mm` process-local floor guard and permits descent above it. Consequently, the original 0.5-second `U` then `J` procedure was invalid: at the nominal configured rate, such a short rise may remain below the guard and make a correct `J` command appear inactive. Do not change the guard or speed to bypass this safety behavior.

The following is preparation for a separately authorized human-operated test, not authorization supplied by this guide.

**Lift sequence:** Keep both leader supplies off and leader USB controllers disconnected. Keep the chassis fixed, follower arms supported and clear, the lift unloaded with its complete travel envelope clear, and the 12 V disconnect immediately accessible. Check out the reviewed lift feature commit on both machines and keep both worktrees clean. Apply body power and run the Pi Lift helper. During its single homing cycle, expect a slow physical downward move to the lower stop followed promptly by zero velocity and a quiet hold; observe current and temperature because the code commands no backoff. Do not start the Windows client unless homing completes normally and the lower stop remains safe. Run the Windows Lift helper and require no movement before Enter. Press Enter, hold `U` only until the carriage is visibly about 10–15 mm above the homed bottom, then release and confirm at least 1 second of stopped motion. Hold `J` for about 1 second, release, and again confirm a prompt stop; then press `Q`. Actual visible travel governs, and failure to rise authorizes no prolonged command. Do not press another movement key. Stop the Pi helper with Ctrl+C and remove body power before reading logs.

Pass requires one controlled downward homing cycle, safe current and temperature at the lower stop, `U` physical up, `J` physical down, prompt zero on both releases and `Q`, stationary wheels and follower arms, no right-bus or leader access, no live command-watchdog event, clean final zero/disconnect, and client/host exit codes `0`. Stop immediately for a homing timeout or exception, failure to stop at the lower limit, sustained hard-stop load, abnormal current or heating, wrong direction, no movement, continued motion after release, wheel or arm motion, a live watchdog warning, bus/communication error, sound, vibration, cable strain, obstruction, instability, or loss of the power-disconnect path. A refusal authorizes no automatic retry and no speed, watchdog, calibration, PID, Phase, limit, or motor-register change.

The lift-only result is complete. Combined local operation, cameras, recording, and remote operation are not yet physically proven by this section.

#### First combined Local packet

Local mode is the narrowly scoped combination of the three proven local components; it does not use the older blocking keyboard loop. The Pi helper opens both follower buses, retains the provisional `20.0` relative arm-target limit, disables cameras, and performs one ordinary lift homing cycle. The Windows helper resolves physical/logical left `COM8` and right `COM7` through the stored PnP identities, verifies the accepted leader calibration hashes, performs the proven both-arm startup synchronization for 120 seconds with the `10.0` final gate, and then runs for at most 30 seconds at 10 Hz with cameras and Rerun disabled.

During synchronization, the Enter pause, and the final post-Enter alignment gate, every base and lift command remains zero. The first live action is the final validated leader sample plus explicit zero `x.vel`, `y.vel`, `theta.vel`, and `lift_axis.vel`. Live arm targets continue through the existing single-owner completion-spaced sender. Keyboard body input uses the proven lowest-speed base mapping and bounded `U/J` lift mapping, but a timestamped mailbox replaces every body command older than 250 ms with exact zero. Thus a stalled keyboard/observation producer cannot make the sender repeat nonzero body velocity indefinitely, while the arm target remains the last safe complete sample. There are no catch-up sends, and the Pi watchdog remains one second.

The following is preparation for one separately authorized first combined session. It is not authorization supplied by this guide.

Pi, from a clean checkout of the reviewed Local feature head:

```bash
./tools/run_am1_host.sh --mode local
```

Windows, from the matching clean Local worktree while reusing the existing ignored machine-local configuration:

```powershell
.\tools\run_am1.ps1 -Mode Local -ConfigPath 'C:\Users\pickm\lerobot_alohamini_client\.worktrees\am1-base-teleop\config\am1.local.json'
```

Starting state: use the regulated 12 V / 10 A body supply; keep the chassis on a level unobstructed floor with at least 2 m clearance; begin both arms in the physically proven natural safe pose, including right-shoulder endpoint headroom; leave the lift near its lower end with its entire unloaded travel clear; verify the labeled leader identities; and keep every power disconnect immediately accessible. Start the Pi helper first. Require exactly one controlled physical-down lift homing cycle, a prompt stop at the real bottom, a quiet hold, no unintended arm or wheel movement, and safe current and temperature. Start the Windows helper, keep both leaders and all movement keys still throughout synchronization, then keep them still at the post-sync Enter gate. Press Enter only with the full envelope clear. Do not move anything until both `TELEOPERATION ACTIVE` and `LOCAL BODY CONTROLS ACTIVE` are printed.

Use this exact short sequence, one body key at a time: move one safe joint on each leader a small amount and return it while no key is held; hold both leaders still; tap `W` for no more than 0.5 second and release for at least 1 second; press `U` only until the carriage is visibly 10–15 mm above its homed bottom and release for at least 1 second; press `J` for about 1 second and release for at least 1 second; then press `Q`. Do not use speed keys and do not intentionally combine arm and body motion in this first session. Stop the Pi helper with Ctrl+C and remove all motor power before reviewing the timestamped logs.

Pass requires both leaders to control their corresponding follower arms in the correct directions, `W` to move the chassis forward and release promptly to zero, `U` to raise the lift, `J` to lower it above the 5 mm guard, every body release and `Q` to stop promptly, no unexpected joint or axis motion, near-10 Hz live action cadence, no live Pi watchdog event, no stale latch, orderly final zero, and client/host exit codes `0`. Stop immediately for synchronization refusal, unexpected direction or motion, loss of endpoint headroom, continued body motion after release, an expired body command during normal responsive operation, a live watchdog warning, stale latch, bus/communication error, abnormal current/heat/sound/vibration, collision risk, cable strain, instability, obstruction, or loss of a disconnect path. A refusal authorizes no automatic retry and no calibration, speed, floor, watchdog, Phase, PID, limit, or servo-register change.

This packet prepares only one bounded combined local session. Cameras, recording, autonomy, battery work, Cloudflare, and remote operation remain out of scope and unproven.

### Simple AM1 leader calibration and recovery

This is the only live normal Aloha Mini 1 leader-calibration path on native Windows. It replaces the Packet 2N-R5 runner workflow. Historical/deprecated tooling: `tools\packet2n_r5_leader_mapping.ps1` remains in the repository only to preserve prior forensic evidence and must not be used for a new calibration.

None of the powered commands below is authorized merely by appearing in this guide. Use a clear workspace and a human operator with both 7.4 V disconnects immediately accessible. Follower/body 12 V power is off and the Pi motor host is stopped throughout all three stages.

1. With both leader supplies off and both leader USB controllers disconnected, inspect the active calibration pair without constructing a bus:

```powershell
pwsh -NoLogo -NoProfile -File .\tools\calibrate_am1_leaders.ps1 -Status -LeftPort $LeftPort -RightPort $RightPort
```

2. For a separately authorized raw bus check, keep each leader in its labeled hub socket, power each from its designated 7.4 V supply, and keep the workspace clear. Move the physical right leader moderately, then the physical left leader, while the checker samples the explicitly selected buses. The exact uppercase positional `CHECK` is required:

```powershell
.\.venv\Scripts\python.exe .\tools\check_am1_leader_buses.py CHECK --left-port $LeftPort --right-port $RightPort
```

Stop immediately and remove leader power on a missing or malformed sample, disappearing port, wrong physical identity, unexpected powered movement, resistance, sound, heat, current, cable strain, disconnect, cleanup failure, or any evidence of a write or follower/Pi/network construction. A failed check authorizes no automatic retry.

3. For a separately authorized one-shot calibration, require both corrected leader buses and designated supplies to be stable, then run:

```powershell
pwsh -NoLogo -NoProfile -File .\tools\calibrate_am1_leaders.ps1 -Calibrate -Confirm CALIBRATE -LeftPort $LeftPort -RightPort $RightPort
```

Follow the existing prompts and move only the joint currently requested through its complete safe useful range. Do not force wrist roll during range recording; the implementation assigns `0..4095`. Before promotion, any failure leaves the active calibration files unchanged; preserve any backup, transcript, or staged evidence already created. After fixing a connection or range-recording problem, perform a complete fresh rerun; never reuse partial staging output. If failure output instead says `ACTIVE_PAIR_STATE=PROMOTED_VERIFIED` and `WITHDRAWAL_CLEANUP_STATE=FAILED_OR_PARTIAL`, do not rerun or alter files—power off and stop for review because the new pair is already active.

Stop immediately and remove leader power for unexpected motion, resistance, sound, heat, current, cable strain, disconnect, a prompt or communication error, or loss of the clear stop path. A stopped or failed calibration authorizes no automatic retry; preserve its output and review the failure first.

Promotion has a narrow fail-closed interruption case because Windows cannot exchange two nonempty directories atomically. Before either rename, the wrapper prints `FAIL_CLOSED_RECOVERY=Rename-Item -LiteralPath '<withdrawal>' -NewName 'so_leader'`. With all leader power off and no LeRobot process running, use only that printed command—and only after verifying that the active `so_leader` path is absent and the printed withdrawal path is a complete ordinary directory. An interruption after the second rename can instead leave a valid active directory plus the redundant withdrawal directory; inspect and validate the active directory before any cleanup. For every other layout, preserve all paths and stop for review rather than improvising a restore, deletion, or copy.

4. After a clean calibration `PASS`, power-cycle and reconnect both leaders, keep follower power off and the Pi host stopped, then run this exact 30-second no-robot check:

```powershell
.\.venv\Scripts\python.exe .\examples\alohamini\teleoperate_bi.py --no_robot --robot.robot_model alohamini1 --teleop.left_port $LeftPort --teleop.right_port $RightPort --teleop.id so101_leader_bi --teleop.arm_profile so-arm-5dof --require_calibration_match --duration_s 30 --fps 5 --no_keyboard --no_rerun
```

This command connects and configures both leader buses. `--no_robot` excludes the follower and Pi only; it is not a raw read-only leader check.

Hold both leaders still until sampling begins. Move only the physical left gripper and verify that the physical left gripper must change only `arm_left_gripper.pos`; return it to a moderate pose, then move only the physical right gripper and verify that the physical right gripper must change only `arm_right_gripper.pos`. Stop immediately on a wrong-side or both-side response, any error, unexpected sound, heat, current, cable strain, disconnect, follower movement or power, or any robot/ZMQ construction. Power off and disconnect both leaders after the check and preserve the complete output for review.

The retained right-only recovery command below is not the normal pair-calibration path. Use it only under a separately reviewed physical calibration packet after backing up the active pair and recording the left hash. It must leave the left file byte-for-byte unchanged:

```powershell
$Python = '.\.venv\Scripts\python.exe'
& $Python @(
  '-m'
  'lerobot.scripts.lerobot_calibrate'
  '--teleop.type=so101_leader'
  '--teleop.port=' + $RightPort
  '--teleop.id=so101_leader_bi_right'
  '--teleop.use_degrees=false'
  '--teleop.arm_profile=so-arm-5dof'
)
```

<details>
<summary>Historical/deprecated direct AM1 calibration example (superseded; do not repeat)</summary>

The following reversed-port command is retained only as project history. It is not a physical-identity example and is not a normal calibration path.

```powershell
.\.venv\Scripts\python.exe `
  .\examples\alohamini\calibrate_bi.py `
  --teleop.left_port COM7 `
  --teleop.right_port COM8 `
  --teleop.id so101_leader_bi `
  --teleop.arm_profile so-arm-5dof
```

</details>

Aloha Mini leader and follower arm actions use normalized positions by default: body joints use `-100..100` and grippers use `0..100`. Existing leader calibration files remain reusable when their physical controller ownership is unchanged because they store raw homing, range, and drive information rather than the runtime normalization mode. The historical Packet 2N evidence established that this pair required corrected-port full recalibration rather than reuse, a port-only swap, or a JSON-content swap.

### Aloha Mini 1 startup synchronization safety

`strict` remains the default startup mode and never automatically positions followers. `sync` is an Aloha Mini 1-only linear interpolation in normalized joint space: it makes an explicit, slow move from newly measured follower positions to one frozen, validated leader pose. It is not collision-aware and does not check self-collision, the workspace, payloads, cables, or nearby people.

Begin every stage with empty grippers, a clear motion envelope, the passive leaders held in moderate poses, the tested follower supported, and the follower motor-power disconnect immediately accessible. Stop at the first unexpected direction, speed, sound, current, contact, software error, or communication failure. Synchronization does not automatically reverse or return an arm after a refusal, and the Pi may continue holding the last arm target.

Leader motors require their 7.4 V low-voltage supply and must never receive the 12 V follower supply. Physical commissioning is not part of software validation and requires separate authorization; use the stages below only as separately authorized, bounded physical checks. Keep the Pi host's `max_relative_target` as an independently selected secondary limit; this Windows client does not configure it.

Before a synchronization move, the client prints the measured start and frozen target and asks the operator to type exactly `SYNC`. Enter alone, lowercase text, or added whitespace does not authorize motion. After confirmation, the client takes fresh follower and leader samples and prints those final endpoints before sending frame zero. Every synchronization frame holds base and lift velocity at zero and changes each selected normalized arm position by at most `STARTUP_SYNC_MAX_STEP = 0.75`. This client frame cap is independent of Pi `max_relative_target`; if it needs more frames than the requested duration, the move takes longer. Actual arm-bearing synchronization sends remain at least `1 / --fps` seconds apart, so an overrun lengthens the move instead of triggering catch-up sends. Every leader sample is validated, and exceeding `STARTUP_SYNC_LEADER_DRIFT = 2.0` aborts selected-side motion.

Command and observation traffic use separate sockets, so the first sequence-fresh response after the final command can still have been generated before that command was processed. The client therefore checks up to the configured observation request window plus one sequence-fresh samples. Synchronization succeeds only when a checked follower sample satisfies `--max_start_mismatch`; otherwise it refuses without widening the threshold. The threshold is final startup convergence verification only: it does not limit how far apart valid calibrated poses may be when a synchronization plan is first proposed, and it is not used continuously at runtime. Historical S1--S5 evidence used `5.0`; the physically completed Packet AR1 baseline and the next bounded commands below use the provisional commissioning gate `10.0`. Do not widen it.

The client makes the operator phase explicit, in this order:

1. `HOLD LEADERS STILL — STARTUP SYNCHRONIZATION IN PROGRESS`
2. `SYNCHRONIZATION COMPLETE`
3. `PRESS ENTER TO ENABLE LIVE TELEOPERATION`
4. `TELEOPERATION ACTIVE — LEADER MOVEMENT IS NOW ALLOWED`

The final message appears only after the post-pause fresh-sample alignment gate passes and immediately before the first ordinary arm action is sent.

#### Historical AM1 single-joint diagnostic and trace evidence

The bounded diagnostic is historical evidence, not the next commissioning action. It constructs no leader, keyboard, camera, or visualization device; it takes a fresh follower pose, holds nonselected joints at the final fresh measured pose, keeps base and lift commands explicitly zero, and uses an exact uppercase `MOVE` gate. With a positive settle duration, historical `PASS` meant two post-window, sequence-fresh in-tolerance samples; it was not by itself a host acknowledgement or persistence proof.

The completed traces used effective `max_relative_target=10.0` and a bounded final-target settle of `--settle_s 5.0`; `PASS` required two consecutive post-window, sequence-fresh in-tolerance samples. The prior negative-direction run was only partially physically proven: it measured `S=21.775`, `T=11.775`, movement `-4.708`, and final error `-5.292`, ending `INCOMPLETE`. The fresh positive-direction repeat measured `S=16.885`, `T=26.885`, movement `+9.507`, and final error `+0.493`, ending `PASS`/`0`. In that positive trace, the requested, relative-limiter, and final targets matched; the `Goal_Position` readback matched within quantization; the SDK transmit completed; and `Torque_Enable`, `Lock`, and `Operating_Mode` read back `1`/`1`/`0`. The observed current maxima were `13 mA` versus `221 mA`.

Together, that is directional/load-dependent downstream elbow behavior: the static non-Phase configuration and normal command transport passed, but the exact physical cause remains unresolved. Do not change any motor setting on the strength of this result. Later Packet 2N work resolved the leader identity as physical/logical left `COM8` and right `COM7`; Packet AR1 below supersedes the old pre-live block.

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

S1--S5 are historical commissioning commands and retain their recorded `5.0` tolerance. They do not authorize another motion. Packet 2N-R3 below is completed evidence; the historical Packet 2N-R5R runner is not a future correction path. Packet 2M S6 remains blocked until the simple corrected-port calibration and no-robot side check pass and their complete evidence is reviewed.

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

The strict verifier below is retained only for the historical Packet 2N-R3 paired logs. It requires the marker, exactly 60 no-robot action records, the no-robot notice, absence of runtime `ZMQ` or calibration text, normal cleanup, exit `0`, the exact twelve arm keys, a moved gripper span of at least 20 normalized units, and less than `2.0` variation across the entire opposite logical family. The broad quoted-key scan deliberately rejects missing, duplicated, or unexpected `arm_*.pos` keys such as `arm_center_*.pos`; legitimate non-arm zero base/lift keys are not classified as unexpected arm data:

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
    if (@($lines | Where-Object { $_ -eq 'NO_ROBOT: robot client construction and connection skipped.' }).Count -ne 1) { throw "missing or duplicate NO_ROBOT proof in $Path" }
    if (-not ($lines | Where-Object { $_ -like 'Shutdown complete:*' })) { throw "missing cleanup proof in $Path" }
    if ($lines -match 'ZMQ') { throw "unexpected ZMQ text in $Path" }

    if ($lines -match '(?i)calibrat') { throw "runtime calibration text requires refusal and review: $Path" }

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

For the historical Packet 2N-R3 logs only, define the function above and then run this prompt-based invocation. It is retained only to reproduce the already completed R3 classification; it is not a future operator path:

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

<details>
<summary>Historical/deprecated Packet 2N-R5C-R2 interrupted-calibration evidence (do not repeat)</summary>

#### Packet 2N-R5C-R2 — interrupted-calibration recovery evidence

This section is retained only as non-repeatable forensic history; none of its runner commands is a current recovery or calibration path. The corrected-port Calibrate session `897f00dc-2608-4790-a74b-1482220eb5ed` did not complete: it is classified `ORPHANED_FRESH_CALIBRATION`, has no next stage, and records Calibrate attempted/launched with real exit code `1`. No map or Verify stage ran, neither reserved map artifact exists, source evidence JSON is absent, and the failed transcript contains no native traceback/output. Do not invent missing evidence.

The exact interrupted artifacts are:

| Artifact | Bytes | SHA-256 | UTC modification time |
|---|---:|---|---|
| Runner state | 6110 | `0371650B298B46B8B724A8425E7D4628AF88F6125F967FEF1ED84091E6E9D7C5` | `2026-08-25T00:40:37.4568328Z` |
| Failed transcript | 1397 | `6BA8699C55BED9074EFBBD18637CEB8FCD337CD70C84629C0C6036BE32768447` | `2026-08-25T00:40:37.3179751Z` |
| Active left `so101_leader_bi_left.json` | 961 | `2B3C2245CAFCA67BBDA25FF0A868A158E6DDCF2162C2A5D5782220EF9DACF50D` | `2026-08-25T00:39:56.5269224Z` |
| Active right `so101_leader_bi_right.json` | 961 | `65A301F20FC7DC96BD7FB5982E3670BF1A01F535953D7A253AB8D33A03646F11` | `2026-08-15T05:19:53.2654429Z` |

The immutable manifest hash is `B90DF72155C60996B4E2704E4A44ED1895BBAEA0C0A332DC24674EC3FA399B8A`. The original left is 960 bytes, mtime `2026-08-15T05:18:25.9699568Z`, hash `6F5D6126E84398D0621A26E74E4DF6678EBA7C14C62D343020610B4D5D8B3D8C`; the original right identity is the active-right identity above. The failed state binds commit `a9891f84f244be54a1c4ffdeba4c475e0c1d851f`, runner hash `CFFFFB7D421BA8E524D156981A24D45462DFA7F6CD45EE4D95CD9FDD68AC7B42`, behavior SHA `cae57b59db1d9156be568aa4b216fc90701aa741`, corrected left `COM8`, corrected right `COM7`, ID `so101_leader_bi`, and profile `so-arm-5dof`.

For the recorded session, both leader 7.4 V supplies were off, both leader USB controllers were disconnected, follower/body 12 V power was off, and the Pi motor host was stopped. Its one offline recovery command was:

```powershell
pwsh -NoLogo -NoProfile -File .\tools\packet2n_r5_leader_mapping.ps1 -Stage RecoverInterruptedCalibration -Confirm RECOVER
```

It was eligible only for the exact validated failed session above. It constructed no COM, robot, teleoperation, camera, ZMQ, Pi, or network object. It archived the mixed pair, failed transcript, state snapshot and retired state, recovery-derived evidence that truthfully marked source evidence and traceback text absent, and immutable manifest/backups. It restored the two originals through whole-directory withdrawal and activation, never sequential file replacement. The non-overwriting archive is:

```text
C:\Users\pickm\AlohaMini1Backups\packet2n-r5-interrupted-897f00dc-2608-4790-a74b-1482220eb5ed
```

A historical pass printed `INTERRUPTED_CALIBRATION_RECOVERY_COMPLETE`; the next offline `Status` was required to report `ORIGINAL_CALIBRATION_INTACT` with next stage `Calibrate` and one verified supplemental interrupted archive marked rejected and ineligible for mapping. These statements preserve the old acceptance record and are not current operating instructions.

The old workflow's raw leader-bus stability check was a separate powered action. Its recorded command was:

```powershell
pwsh -NoLogo -NoProfile -File .\tools\packet2n_r5_leader_mapping.ps1 -Stage CheckLeaderBuses -Confirm CHECK
```

Its historical physical starting state required a clear leader workspace, both 7.4 V disconnects immediately accessible, physical/logical left on `COM8`, physical/logical right on `COM7`, follower/body 12 V off, and the Pi motor host stopped. The check used raw, uncalibrated Feetech reads only: IDs `1..6` on both buses, `Present_Position`, no retries, approximately 10 Hz, at most 30 seconds, and cleanup with torque unchanged.

The historical PASS criteria required both buses and every ID `1..6` in every sample for the full interval, raw integral non-boolean values in register range, a nonzero sample count, complete first/last vectors and per-ID min/max output, clean disconnects, and final `LEADER_BUS_CHECK=PASS` followed by `LEADER_BUS_CHECK_STAGE=PASS`.

The historical stop conditions were the first missing packet, incomplete/malformed sample, disappearing port, exception, unexpected powered motion, resistance, sound, heat, cable strain, wrong physical identity, loss of the clear stop path, cleanup failure, or any evidence of a write, torque/mode/configuration/calibration operation, robot, Pi, network, camera, or ZMQ construction.

The old workflow recorded this later calibration command:

```powershell
pwsh -NoLogo -NoProfile -File .\tools\packet2n_r5_leader_mapping.ps1 -Stage Calibrate -Confirm CALIBRATE
```

This documentation, the recovery result, and a bus-check PASS do not authorize Calibrate, MapLeft, MapRight, Verify, teleoperation, recording, S6, any Pi command, or any AM2/AM2 Pro action. The exact next Pi command is none.

</details>

<details>
<summary>Historical Packet 2N-R5R rejected-successful-calibration notes (superseded)</summary>

#### Packet 2N-R5R — historical rejected-calibration restart

Packet 2N-R5R previously replaced the fragile pasted PowerShell workflow with the versioned `tools\packet2n_r5_leader_mapping.ps1` runner. That runner is now deprecated forensic tooling, not an operator path. Do not load the historical Packet 2N-R3 verifier, paste internal functions, or manually construct R5 state, evidence, transcripts, or map logs.

Corrected-port calibration session `a9128060-c60c-4582-8cb8-cf45fc1750e6` completed, but the operator rejected its physical range because one non-wrist-roll joint on the left leader was not moved through its complete safe useful range. The current state is `VALID_FRESH_CALIBRATION` with `next_stage` equal to `MapLeft` and completed stages exactly `[Calibrate]`; neither map nor `Verify` ran. The rejected current files are:

| Current calibration file | Bytes | SHA-256 | UTC modification time |
| --- | ---: | --- | --- |
| `so101_leader_bi_left.json` | 963 | `3E3896F0C4B49344FA896DFCD430C7EAB8B04B7ED457E8046689C821EA7BFA88` | `2026-08-24T03:52:27.1823938Z` |
| `so101_leader_bi_right.json` | 962 | `D7D948AD2FFCAA60C6490EAC8631E7ABC6410C7584BCD00EFBDC64839F710119` | `2026-08-24T03:53:39.0485589Z` |

The bound state and evidence validate these identities, session start `2026-08-24T03:51:18.7177104Z`, corrected ports left `COM8` and right `COM7`, source commit `edc14bbbebb173061cf3b04ead08ffa9fcb81051`, runner SHA-256 `0BDBDB2F20AD9D47A2B3DBF84924B833E822FE733EA33FAD505753BAD0BE336E`, and behavior SHA `cae57b59db1d9156be568aa4b216fc90701aa741`. The transcript's bound header, hash, byte count, and final `CALIBRATION_EXIT_CODE=0` terminator validate, but its PowerShell transcript body contains no native calibration output. Do not invent or infer missing native-output evidence.

The immutable copy-only backup remains:

```text
Backup directory: C:\Users\pickm\AlohaMini1Backups\packet2n-r5-20260822-121722-7941f445-9587-4345-8e2f-edd54ca750f6
Manifest: C:\Users\pickm\AlohaMini1Backups\packet2n-r5-20260822-121722-7941f445-9587-4345-8e2f-edd54ca750f6\manifest.json
Manifest SHA-256: B90DF72155C60996B4E2704E4A44ED1895BBAEA0C0A332DC24674EC3FA399B8A
```

| Immutable original file | Bytes | SHA-256 | Source UTC modification time |
| --- | ---: | --- | --- |
| `so101_leader_bi_left.json` | 960 | `6F5D6126E84398D0621A26E74E4DF6678EBA7C14C62D343020610B4D5D8B3D8C` | `2026-08-15T05:18:25.9699568Z` |
| `so101_leader_bi_right.json` | 961 | `65A301F20FC7DC96BD7FB5982E3670BF1A01F535953D7A253AB8D33A03646F11` | `2026-08-15T05:19:53.2654429Z` |

The manifest and both backup files match their pinned hashes, byte counts, and source timestamps. They are evidence and are used only by the runner's exact confirmed offline restart; they are not authorization for a manual restore, move, rename, edit, delete, or file-by-file swap.

The following 48-byte log was found and is permanently unusable:

```text
C:\Users\pickm\AlohaMini1Logs\INVALID-20260822-192753-packet2n-r5-physical-left-only-empty-session.log
SHA-256: 23829171F0B84D5C2D2870CAF7BD944A2552B7586912D876F16E2FBB6C93F5B6

MAP_RUN=PHYSICAL_LEFT_ONLY
CLIENT_EXIT_CODE=0
```

The calibration invocation and persisted-state creation did not complete, or their in-memory variables were no longer available. The native mapping client never launched; stale `$LASTEXITCODE=0` was appended afterward. This file is not evidence of a map attempt or success, and the staged runner will not accept it.

##### Historical/deprecated staged interface

The runner persists its session to:

```text
C:\Users\pickm\AlohaMini1Logs\packet2n-r5-state.json
```

`Status` classifies the current files and any persisted session, while `Calibrate` creates a new bound state only after its offline guards pass. `RestartCalibration` is the offline, exact-confirmation recovery for only a valid fresh pre-map candidate. Every continuation stage reloads and revalidates the state, corrected port ownership, current calibration identities, immutable backup and manifest, evidence, transcript, completed map artifacts, Git provenance, import sources, and confined artifact paths before any later COM construction. Hardware confirmations are exact and case-sensitive. Native execution records whether launch was attempted and completed, captures the actual exit code immediately, and writes a success terminator only after a launched process returns zero. Later stages reparse and rehash prior artifacts instead of trusting state summaries. A failure preserves the primary error and prints the recovery classification and next stage.

`DiagnoseImports` is the entirely offline repository-source preflight used by `Calibrate`. It uses the exact repository virtual environment, prints the interpreter, environment, editable-install metadata, `.pth` source, and expected/actual path for every guarded module, and changes neither calibration files nor runner state:

```powershell
pwsh -NoLogo -NoProfile -File .\tools\packet2n_r5_leader_mapping.ps1 -Stage DiagnoseImports
```

It must exit zero with `"matches":true` before a later authorized calibration. A refusal identifies the mismatched module and its expected and actual canonical paths; do not bypass it or repair calibration files in response.

`Status` is also entirely offline and hardware-free:

```powershell
pwsh -NoLogo -NoProfile -File .\tools\packet2n_r5_leader_mapping.ps1 -Stage Status
```

Before the approved restart, the required result is `VALID_FRESH_CALIBRATION` with `next_stage` equal to `MapLeft`. `Status` returning process status zero is not by itself a pass; inspect its JSON classification. The legacy exception is pinned only to session `a9128060-c60c-4582-8cb8-cf45fc1750e6` and its exact state, fresh-pair, evidence, transcript, repository, runner, and behavior identities; a merely self-consistent legacy session is not eligible. Current-runner/current-HEAD sessions remain eligible under the ordinary provenance rule. `RESTART_CALIBRATION_RECOVERABLE` with `next_stage` equal to `RestartCalibration` means the journal authority and the actual archive, active-pair, staged-pair, rollback, retired-pair, and retired-state layout all match a recognized interruption point: rerun only the same exact confirmed restart command. Merely finding a journal is insufficient. A malformed or tampered journal, an unrecognized layout, or a reparse point is `INVALID_OR_UNCERTAIN_STATE`. All ordinary stages remain blocked until the transaction completes. `ORPHANED_FRESH_CALIBRATION` is also a refusal: preserve every file and stop for review. Do not manually restore or repair anything.

##### Offline rejected-calibration restart

Run this only with both leader 7.4 V supplies off, both leader USB controllers disconnected, follower/body 12 V power off, and the Pi motor host stopped. It constructs no COM object, runs no calibration process, contacts no Pi or network service, and constructs no robot, camera, teleoperation, or ZMQ client:

```powershell
pwsh -NoLogo -NoProfile -File .\tools\packet2n_r5_leader_mapping.ps1 -Stage RestartCalibration -Confirm RECALIBRATE
```

The runner first revalidates the exact pre-map state, current fresh pair, transcript/evidence, repository provenance, immutable manifest, and both backups. It publishes a non-overwriting archive at:

```text
C:\Users\pickm\AlohaMini1Backups\packet2n-r5-rejected-a9128060-c60c-4582-8cb8-cf45fc1750e6
```

The archive records reason `OPERATOR_REJECTED_INCOMPLETE_RANGE` and preserves byte-identical current calibration files, transcript, evidence, state snapshot, immutable manifest identity, hashes, sizes, source/archive timestamps, session/start time, state binding, source/recovery provenance, the withdrawn active directory, the retired state, and a completion receipt. For the one exact approved legacy transcript, the record preserves a specifically validated known limitation that its body contains no native calibration output. A future current-runner transcript is recorded as `NOT_EVALUATED` with `body_contains_native_calibration_output` equal to JSON `null`; absence is never fabricated. Journal, archive-record, and receipt publication use create-new temporary files with write-through and `Flush(true)` before namespace publication through Windows `MoveFileExW` with `MOVEFILE_WRITE_THROUGH`; only regular-file journal replacement also uses `MOVEFILE_REPLACE_EXISTING`. Directory swaps and state retirement use the same write-through namespace API without replacement or cross-volume copy. Windows error codes are preserved in any refusal. The verified receipt publication is durably complete before the journal is retired; an interruption at that seam remains safely resumable.

The runner reconstructs only missing or corrupt expected files in recognized partial archive and original-pair staging directories, then re-verifies every identity before continuing. On retry it first derives the highest completed phase from the validated filesystem layout, durably reconciles a one-transition-lagging journal to that phase, and revalidates the journal and layout before performing the next mutation. This makes consecutive interruptions after different namespace transitions resumable without ever exposing a mixed left/right pair. It stages the complete pinned original pair in a sibling directory, atomically renames the entire active `so_leader` directory aside, and atomically renames the staged original directory active. It never performs sequential in-place left/right replacements. Because AM1, AM2, and AM2 Pro leaders share the `teleoperators\so_leader` directory, that active directory must contain exactly the reviewed AM1 left/right pair; any additional model calibration file causes a pre-mutation refusal with every byte unchanged. Before mutation and again before each move, all path components must remain confined and free of reparse points, and Windows volume identities—not drive-letter strings—must prove the move stays on one actual volume.

The published archive is recursively enumerated against an exact finite layout derived independently from the caller-supplied state path, archived session/state, and pinned plan before `Status` calls it recoverable or restart mutates anything. Every relative filename and source path must be exact; relabeling the archived state source or either preserved state leaf cannot redefine that authority. Archived rejected calibration identities and JSON values must equal `state.post_calibration`; transcript and evidence source-time claims must equal their byte-preserving archived copies; the manifest bytes, hash, parsed content, and backup metadata must equal the pinned manifest/backups; the complete archived state schema, session binding, provenance, and native-stage truth must validate; and archived evidence/transcript headers, semantics, identity bindings, and terminator must agree without reading the retired active calibration paths. Archive record and receipt claims are then derived from those facts rather than accepted because they are internally self-consistent. Unexpected entries, renamed or relabeled artifacts, premature receipt files, wrong path types, or a reparse point at any archive component refuse without mutation. The completed receipt is trusted only when its exact schema, completion time, native-stage truth, source provenance, recovery provenance, archive record, and archived state bindings all verify.

A successful command prints exactly `RESTART_CALIBRATION_COMPLETE`. The following offline `Status` must then report `ORIGINAL_CALIBRATION_INTACT` with `next_stage` equal to `Calibrate` and a verified supplemental rejected-archive record. A later valid fresh session retains that supplemental record without changing its active classification. Do not treat any rejected archive as MapLeft evidence.

##### Physical-stage prerequisites and commands

The recorded safe state when this historical work stopped was both leader 7.4 V supplies off, both leader USB controllers disconnected, follower/body 12 V power off, and the Pi motor host stopped. At that historical point only `DiagnoseImports`, `Status`, and the separately reviewed offline `RestartCalibration` command above were considered justified. The exact Pi command was none.

Do not run a hardware stage without separate physical authorization. Immediately before an authorized `Calibrate`, `MapLeft`, or `MapRight` stage:

- keep the Pi motor host stopped and follower/body 12 V power off;
- clear the leader workspace and keep both leader power disconnects immediately accessible;
- connect physical/logical left as `COM8` and physical/logical right as `COM7`; and
- power each leader only from its designated 7.4 V supply, never the follower 12 V supply.

Do not contact the Pi or construct a robot or ZMQ connection. Each stage may run from a newly opened PowerShell process; the persisted default state path binds the sequence.

**Calibrate.** Follow every two-arm calibration prompt exactly. The runner uses ID `so101_leader_bi`, profile `so-arm-5dof`, corrected ports, and `--force_fresh_calibration`:

```powershell
pwsh -NoLogo -NoProfile -File .\tools\packet2n_r5_leader_mapping.ps1 -Stage Calibrate -Confirm CALIBRATE
```

A pass requires process status zero followed by `Status` reporting `VALID_FRESH_CALIBRATION` and `next_stage` equal to `MapLeft`. The runner must have bound two distinct fresh calibration identities to a valid transcript and evidence JSON. After calibration, switch off both 7.4 V supplies and disconnect both USB controllers. Stop at this power-cycle and review boundary; restage both leaders only for a later authorized map run.

**MapLeft.** With both corrected leaders connected and powered as above, wait for the client Enter prompt, press Enter, and then slowly open and close only the physical left gripper through at least 20 normalized units. Hold its other five joints and the entire physical right leader still:

```powershell
pwsh -NoLogo -NoProfile -File .\tools\packet2n_r5_leader_mapping.ps1 -Stage MapLeft -Confirm MAPLEFT
```

A pass requires process status zero and `Status` reporting `VALID_FRESH_CALIBRATION` with `next_stage` equal to `MapRight`. Power down and disconnect both leaders before review or restaging.

**MapRight.** With both corrected leaders connected and powered as above, wait for the client Enter prompt, press Enter, and then slowly open and close only the physical right gripper through at least 20 normalized units. Hold its other five joints and the entire physical left leader still:

```powershell
pwsh -NoLogo -NoProfile -File .\tools\packet2n_r5_leader_mapping.ps1 -Stage MapRight -Confirm MAPRIGHT
```

A pass requires process status zero and `Status` reporting `VALID_FRESH_CALIBRATION` with `next_stage` equal to `Verify`. Switch off both 7.4 V supplies and disconnect both USB controllers before verification.

**Verify.** Run only with all leader and follower power off and both leader USB controllers disconnected:

```powershell
pwsh -NoLogo -NoProfile -File .\tools\packet2n_r5_leader_mapping.ps1 -Stage Verify
```

`Verify` is the only stage that prints `MAPPING_RESULT=CORRECT`. That exact line is the only mapping pass. Final `Status` must report `VALID_FRESH_CALIBRATION`, `final_result` equal to `MAPPING_RESULT=CORRECT`, and `next_stage` equal to `null`.

Each accepted map contains exactly 60 complete samples. Every sample has exactly the twelve expected `arm_*.pos` keys plus `x.vel`, `y.vel`, `theta.vel`, and `lift_axis.vel`, with all four body values exactly zero. The selected physical gripper must span at least 20 normalized units while the entire opposite logical arm family varies by less than `2.0`. Missing, duplicate, unexpected, nonnumeric, or nonfinite arm data; nonzero body data; an invalid sample count; stale or mismatched metadata; runtime calibration or ZMQ text; or an incomplete exit/cleanup record is a refusal.

Stop immediately and remove leader power for unexpected powered movement, resistance, sound, heat, cable strain, communication or calibration failure, a wrong port or physical identity, accidental movement of the nonselected leader, loss of the clear stop path, any follower power or movement, or any evidence that a robot/ZMQ connection was constructed. Also stop on any nonzero stage exit, unexpected classification, or recovery state. Preserve the state file and every reserved artifact. Do not delete, overwrite, edit, restore, or manually rerun a failed stage without review.

Passing Packet 2N-R5R authorizes only later review of its evidence. It does not authorize follower power, Pi contact, startup synchronization, motor-setting changes, or teleoperation.

</details>

<details>
<summary>Superseded Packet AR1 software-wrist procedure (historical; do not execute)</summary>

The procedure below records the state before the right leader's upside-down wrist-flex motor was mechanically corrected. Its follower-side reflection, calibration hash, commit pins, isolated-wrist stage, and physical commands are obsolete and must not be used.

#### Packet AR1 — superseded local bimanual-arm state

Local AM1 bimanual arm teleoperation has been physically demonstrated once. The reviewed Windows run completed startup synchronization, printed `TELEOPERATION ACTIVE — LEADER MOVEMENT IS NOW ALLOWED`, forwarded both leaders to their corresponding followers, cleaned up, and recorded `SESSION_EXIT_CODE=0`.

Evidence:

- Windows client: `C:\Users\pickm\AlohaMini1Logs\am1-provisional-sync-live20-20260829-213421.log`
- downloaded Pi host: `C:\Users\pickm\AlohaMini1Logs\am1-provisional-live20-host-20260829-212759.log`
- right-leader direction sample: `C:\Users\pickm\AlohaMini1Logs\am1-right-wrist-leader-direction-20260830-003457.log`

The historical run used left `COM8`, right `COM7`, a regulated 12 V / 10 A follower/body supply, the physical right follower shoulder-lift at its natural unforced endpoint, Pi `max_relative_target=20.0`, `--startup_sync_duration_s 120.0`, and `--max_start_mismatch 10.0`. Its former right calibration identity was invalidated by the later mechanical correction; only the identities in the current section below are accepted.

That run appeared to show a right-wrist coordinate defect and follower pauses. The later mechanical inspection proved the wrist problem came from the upside-down right-leader motor; the temporary follower-side reflection was an incorrect workaround and has been removed. The observation-request failure and current no-reflection design are described in the current section below.

The old client performed synchronous observation, leader, absent-camera, visualization, and printing work before action sends. Explicit AM1 arms-only mode (`--no_keyboard --no_cameras`) now keeps the existing observation DEALER socket, both leader buses, all follower and leader reads, validation, and the operator lifecycle on the main thread. A dedicated action-sender thread creates its own context and PUSH socket, sets `CONFLATE`, bounded `SNDTIMEO`, and `LINGER` before connecting, sends at a monotonic completion-spaced cadence, and closes that socket and context in the same thread. The original main-thread PUSH socket is used only before live forwarding and after the sender has stopped and joined. The sender always transmits all twelve arm targets plus explicit zero `x.vel`, `y.vel`, `theta.vel`, and `lift_axis.vel`; it never sends catch-up bursts.

A request that returns no newer observation sequence while the last fresh follower sample is younger than one second is transient: the client counts it, retains the last complete action, does not read a leader sample, and retries while the sender cadence continues. The freshness clock begins at socket receive completion, before parsing and the paired leader read, and that exact per-sequence timestamp crosses the startup/alignment gate into live forwarding. An approved initial sample already one second old is refused before the live command transport is opened or any live action is sent. A later fresh observation accepts only one latest validated leader sample, without queued intermediate actions. At one second, staleness latches once, freezes the complete action for the remainder of a bounded run, and exits status `2`; an unbounded run stops promptly instead of holding stale forever. For each advancing sequence, the client retains the exact raw payload key set alongside its stable normalized observation schema, so normalization cannot hide a missing arm key by filling zero or hide an unexpected arm key by dropping it. The exact AM1 arm-key check guards every startup, alignment, and live observation while ignoring legitimate non-arm base/lift keys. JSON observations must decode to an object; a syntactically valid array, scalar, or null root is rejected through the same malformed-payload path. Parse and numeric-conversion failures are explicit malformed-payload refusals rather than cached-sample timeouts. Partial, malformed, nonfinite, out-of-range, or regressing observations and invalid leader samples safety-refuse directly before another leader read. Every refusal stops and joins a started sender before the main thread requests final zero and cleans up, preserving the original exception.

For `--live_arm_scope right_wrist_flex`, the fresh post-Enter follower observation is the complete hold snapshot. The first live action exactly repeats all twelve follower positions with zero body/lift, so residual startup mismatch cannot move an unselected joint. Later fresh samples replace only `arm_right_wrist_flex.pos`; the other eleven targets remain at that follower snapshot. Full-bimanual scope remains leader-target based. `--profile_cadence` emits one client live-start marker and one bounded final summary with wall-clock correlation fields. The host reports wall-clocked command gaps, every gap over the watchdog threshold, watchdog transitions, limiter state, and selected right-wrist values once per second, then forces one final cadence snapshot during cleanup. No minimum-command-step behavior was added.

The reviewed software commits are:

- Windows cadence client: `6a54efe579025ee5d45895a228f11be706a93990`
- Windows live-interval timestamps: `86135e8c048a0d5b1ba7f7c8f9352931cd0efe6d`
- Windows profiled-cleanup exception identity: `f92469e9ab821f5547f98e623193b05ff325b7a0`
- Windows thread-owned live command transport: `ac26e5ca100467d5c613b8876ed8db81081cf41f`
- Windows transient observation timeout state: `5c10c728baf39fb2d6c52bb78159161a75f8f84d`
- Windows follower-snapshot wrist hold: `9f61246285bc7af778862da4a36f8d07098a2a34`
- Windows raw-observation provenance and receipt-age enforcement: `f686a6ad26d413be8c608fa774e94012b40394dc`
- Windows startup/alignment payload refusal and pre-send stale gate: `0b39b2208b9d7d1d4961fbbb31bd925d8d84fde7`
- Windows non-object JSON observation refusal: `0631428a20de285cdf9133491fa8f0262f86230e`
- Pi wrist normalization and host evidence: `96b6004cb3f9a5e224f5b753171796fd98284341`
- Pi complete receive-gap counting: `16f241395e9ce1f9e565061cae77a53f6456679e`
- Pi event timestamps and final snapshot: `518b14e9325102a9528d7be035a8632e665f79a4`

These results do not establish base, lift, cameras, recording, or remote operation. The commands below are prepared for a later separately authorized physical session; this documentation does not authorize motion. Begin staging with the Pi motor host stopped, follower/body power off, both leader supplies off, and both leader USB controllers disconnected. Apply power and reconnect only in the separately authorized physical procedure, never while performing the Git and calibration-identity preflight.

Before either Windows stage, verify the branch, reviewed production tail, clean worktree, and both known-good leader calibrations. The clock check is read-only SSH to the Pi; it neither starts the motor host nor accesses a serial bus:

```powershell
$ErrorActionPreference = 'Stop'
if ((git branch --show-current).Trim() -ne 'fix/am1-wrist-cadence') { throw 'Wrong Windows branch.' }
git merge-base --is-ancestor 0631428a20de285cdf9133491fa8f0262f86230e HEAD
if ($LASTEXITCODE -ne 0) { throw 'The reviewed Windows AR1 code commit is not present.' }
$reviewedTail = @(git diff --name-only 0631428a20de285cdf9133491fa8f0262f86230e..HEAD)
if (($reviewedTail.Count -ne 1) -or ($reviewedTail[0] -ne 'docs/alohamini/alohamini.md')) { throw 'Unexpected change after the reviewed Windows code commit.' }
if (@(git status --porcelain).Count -ne 0) { throw 'Windows worktree is not clean.' }

$calibrationDir = Join-Path $HOME '.cache\huggingface\lerobot\calibration\teleoperators\so_leader'
$leftCalibration = Join-Path $calibrationDir 'so101_leader_bi_left.json'
$rightCalibration = Join-Path $calibrationDir 'so101_leader_bi_right.json'
if ((Get-FileHash -LiteralPath $leftCalibration -Algorithm SHA256).Hash -ne '34D06E15F6768A3290B85BBE3507D9B14A8CCED263A40C575E02010560E13FBE') { throw 'Left leader calibration mismatch.' }
throw 'Superseded AR1 procedure: do not execute; use the current AR1-R2H packet below.'

$env:PYTHONDONTWRITEBYTECODE = '1'
$am1LogDir = Join-Path $HOME 'AlohaMini1Logs'
New-Item -ItemType Directory -Force -Path $am1LogDir | Out-Null

function Get-Am1ClockCheck {
    param([Parameter(Mandatory)][string]$Phase)
    $beforeMs = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    $piTimeOutput = & ssh.exe -o BatchMode=yes -o ConnectTimeout=5 'pickmanmike@192.168.1.134' 'date +%s%3N'
    $sshExit = $LASTEXITCODE
    $afterMs = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    if ($sshExit -ne 0) { throw 'Pi clock check failed.' }
    [long]$piMs = 0
    $piTimeText = @($piTimeOutput)[-1].Trim()
    if (-not [long]::TryParse($piTimeText, [ref]$piMs)) { throw "Invalid Pi clock value: $piTimeText" }
    $midpointMs = ($beforeMs + $afterMs) / 2.0
    $rttMs = $afterMs - $beforeMs
    $offsetBoundMs = [math]::Abs($piMs - $midpointMs) + $rttMs / 2.0
    $result = [ordered]@{
        event = 'am1_clock_check'
        phase = $Phase
        windows_midpoint_ms = [math]::Round($midpointMs, 3)
        pi_ms = $piMs
        rtt_ms = $rttMs
        offset_bound_ms = [math]::Round($offsetBoundMs, 3)
    }
    if ($offsetBoundMs -gt 250.0) { throw "Windows/Pi clock offset bound is $offsetBoundMs ms; limit is 250 ms." }
    return ($result | ConvertTo-Json -Compress)
}
```

If either leader presents a calibration prompt, stop; do not recalibrate.

For the right-wrist-isolated live stage, prepare the Pi on `fix/am1-right-wrist-normalization` at exactly `518b14e9325102a9528d7be035a8632e665f79a4`, with a clean worktree, then run this host wrapper only after that physical stage is authorized. Its prerequisite both-side startup synchronization may reposition any follower arm joint; isolation begins only after `TELEOPERATION ACTIVE`:

```bash
set -euo pipefail
cd /home/pickmanmike/lerobot_alohamini
test "$(git branch --show-current)" = "fix/am1-right-wrist-normalization"
test "$(git rev-parse HEAD)" = "518b14e9325102a9528d7be035a8632e665f79a4"
test -z "$(git status --porcelain)"

HOST_LOG="$HOME/am1-ar1-right-wrist-host-$(date +%Y%m%d-%H%M%S).log"
printf 'HOST_LOG=%s\n' "$HOST_LOG"
set +e
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m lerobot.robots.alohamini.alohamini_host \
  --robot_model alohamini1 \
  --no_cameras \
  --skip_lift_home \
  --max_relative_target 20.0 \
  --max_loop_freq_hz 30 \
  --profile_timing true \
  --profile_cadence \
  2>&1 | tee "$HOST_LOG"
host_exit=${PIPESTATUS[0]}
set -e
printf 'HOST_EXIT_CODE=%s\n' "$host_exit" | tee -a "$HOST_LOG"
exit "$host_exit"
```

With both leaders held still until the active message, run the right-wrist-isolated live client. Its first live frame holds the fresh post-gate follower pose for all twelve joints before any right-wrist leader update is accepted:

```powershell
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$windowsLog = Join-Path $am1LogDir "am1-ar1-right-wrist-windows-$timestamp.log"
"WINDOWS_LOG=$windowsLog" | Tee-Object -FilePath $windowsLog
Get-Am1ClockCheck -Phase 'pre_client' | Tee-Object -FilePath $windowsLog -Append
& .\.venv\Scripts\python.exe .\examples\alohamini\teleoperate_bi.py `
  --robot.remote_ip 192.168.1.134 `
  --robot.robot_model alohamini1 `
  --teleop.left_port COM8 `
  --teleop.right_port COM7 `
  --teleop.id so101_leader_bi `
  --teleop.arm_profile so-arm-5dof `
  --startup_mode sync `
  --startup_sync_side both `
  --startup_sync_duration_s 120.0 `
  --max_start_mismatch 10.0 `
  --live_arm_scope right_wrist_flex `
  --fps 10 `
  --duration_s 30 `
  --start_paused `
  --no_keyboard `
  --no_cameras `
  --profile_cadence `
  --no_rerun `
  2>&1 | Tee-Object -FilePath $windowsLog -Append
$clientExit = $LASTEXITCODE
"SESSION_EXIT_CODE=$clientExit" | Tee-Object -FilePath $windowsLog -Append
if ($clientExit -ne 0) { throw "Right-wrist session failed with exit code $clientExit." }
Get-Am1ClockCheck -Phase 'post_client' | Tee-Object -FilePath $windowsLog -Append
```

At `TELEOPERATION ACTIVE`, record the host's reported normalized right-wrist requested value as the excursion reference. Move only the physical right leader wrist slowly upward while the reported request remains within five normalized units of that reference, return to the reference, move downward within five units, and return. This is an operator-enforced bound, not a software travel limit; stop rather than crossing it. Expected: the right follower wrist moves in the same physical direction both ways; every other arm joint holds and base/lift remain stationary throughout the isolated live phase.

Only after that evidence passes review, start the bimanual Pi host with the same reviewed settings and a distinct log:

```bash
set -euo pipefail
cd /home/pickmanmike/lerobot_alohamini
test "$(git branch --show-current)" = "fix/am1-right-wrist-normalization"
test "$(git rev-parse HEAD)" = "518b14e9325102a9528d7be035a8632e665f79a4"
test -z "$(git status --porcelain)"

HOST_LOG="$HOME/am1-ar1-bimanual10-host-$(date +%Y%m%d-%H%M%S).log"
printf 'HOST_LOG=%s\n' "$HOST_LOG"
set +e
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m lerobot.robots.alohamini.alohamini_host \
  --robot_model alohamini1 \
  --no_cameras \
  --skip_lift_home \
  --max_relative_target 20.0 \
  --max_loop_freq_hz 30 \
  --profile_timing true \
  --profile_cadence \
  2>&1 | tee "$HOST_LOG"
host_exit=${PIPESTATUS[0]}
set -e
printf 'HOST_EXIT_CODE=%s\n' "$host_exit" | tee -a "$HOST_LOG"
exit "$host_exit"
```

Then run the bimanual Windows client:

```powershell
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$windowsLog = Join-Path $am1LogDir "am1-ar1-bimanual10-windows-$timestamp.log"
"WINDOWS_LOG=$windowsLog" | Tee-Object -FilePath $windowsLog
Get-Am1ClockCheck -Phase 'pre_client' | Tee-Object -FilePath $windowsLog -Append
& .\.venv\Scripts\python.exe .\examples\alohamini\teleoperate_bi.py `
  --robot.remote_ip 192.168.1.134 `
  --robot.robot_model alohamini1 `
  --teleop.left_port COM8 `
  --teleop.right_port COM7 `
  --teleop.id so101_leader_bi `
  --teleop.arm_profile so-arm-5dof `
  --startup_mode sync `
  --startup_sync_side both `
  --startup_sync_duration_s 120.0 `
  --max_start_mismatch 10.0 `
  --live_arm_scope both `
  --fps 10 `
  --duration_s 45 `
  --start_paused `
  --no_keyboard `
  --no_cameras `
  --profile_cadence `
  --no_rerun `
  2>&1 | Tee-Object -FilePath $windowsLog -Append
$clientExit = $LASTEXITCODE
"SESSION_EXIT_CODE=$clientExit" | Tee-Object -FilePath $windowsLog -Append
if ($clientExit -ne 0) { throw "Bimanual session failed with exit code $clientExit." }
Get-Am1ClockCheck -Phase 'post_client' | Tee-Object -FilePath $windowsLog -Append
```

For either stage, begin with both arm envelopes clear; empty grippers; followers supported where needed; the regulated 12 V / 10 A follower supply; leaders on only their designated 7.4 V supplies; the physical right follower shoulder-lift at its natural unforced endpoint; base clear; lift unhomed and blocked; and every physical disconnect immediately reachable. Hold both leaders still through both operator gates: type exact uppercase `SYNC` to authorize startup synchronization. After `SYNCHRONIZATION COMPLETE`, continue holding for at least three seconds so the Pi log contains two post-sync cadence baselines; only then press Enter, and keep holding until the exact active message appears.

Pass requires correct correspondence, same-direction right wrist flex, no human-observed pause/resume or jump, no live watchdog event, no stale latch, no absent-camera warning, strictly increasing accepted observation sequences, explicit zero body/lift, clean cleanup, client exit `0`, and both `pre_client` and `post_client` `am1_clock_check.offset_bound_ms <= 250`. A counted transient observation timeout is not by itself a refusal if action cadence remains continuous, no stale latch occurs, and the next accepted sequence is fresh; record and review the count. Let `B` be the larger of those two offset bounds. Use the client's `am1_client_live_start.wall_time_ns` and `am1_client_action_cadence.live_end_wall_time_ns` as the live interval. Every client action-send interval must be at or below 1000 ms. A host report is certainly before live start only when its `wall_time_ns / 1e6 + B` is less than the client start in milliseconds; select the final post-sync report satisfying that rule as the baseline. The first host command after that baseline may increment `receive_gap_over_watchdog_count` once because its measured receive gap spans the post-sync Enter gate; its `last_receive_gap_over_watchdog_sequence` must equal the baseline `command_sequence + 1`. No later host command may increment that count. A host watchdog timestamp is certainly inside the live interval only from `client_start_ms + B` through `client_end_ms - B`; any watchdog timestamp in either `B`-wide boundary band is ambiguous and refuses the pass. Compare the baseline and forced-final `watchdog_events`: the final count must equal the baseline, or may equal baseline plus one only when `last_watchdog_event_wall_time_ns / 1e6 - B` is strictly greater than client live end and represents the single expected post-exit stop. A larger delta or any other delta/timestamp classification refuses the pass. The forced final host snapshot must be present. Do not reject on `max_receive_gap_ms` alone: preconnection, startup-synchronization authorization, the post-sync Enter gate, and post-exit intervals can legitimately raise this cumulative maximum. Repeated target limiting, or any limiter event correlated with a pause, is a failure requiring log review; do not widen limits to hide it.

Stop immediately for wrong direction or joint, excessive travel or speed, jump/resume behavior, unexpected sound/current/contact/cable strain, any live watchdog or stale transition, communication error, calibration prompt, repeated limiter activity, or loss of the physical power-disconnect path. Stop the host and remove motor power before review.

Fetch the exact Pi log path printed by the wrapper after stopping it cleanly:

```powershell
$piHostLog = Read-Host 'Paste the exact Pi HOST_LOG path'
.\tools\fetch_am1_pi_log.ps1 -RemoteHost 'pickmanmike@192.168.1.134' -RemotePath $piHostLog
if ($LASTEXITCODE -ne 0) { throw 'Pi host-log fetch failed.' }
```

</details>

#### Packet AR1 — current local bimanual-arm state

Local AM1 bimanual arm teleoperation has been physically demonstrated once, using the regulated 12 V / 10 A follower/body supply. The completed baseline entered live mode, controlled both corresponding follower arms, cleaned up, and returned `SESSION_EXIT_CODE=0`; base, lift, cameras, recording, and remote operation are not thereby complete. Its evidence remains:

- Windows: `C:\Users\pickm\AlohaMini1Logs\am1-provisional-sync-live20-20260829-213421.log`
- Pi host: `C:\Users\pickm\AlohaMini1Logs\am1-provisional-live20-host-20260829-212759.log`

The right-leader wrist-flex motor was subsequently found mounted upside down. It was reinstalled in the correct physical orientation and the right leader alone was recalibrated as `so101_leader_bi_right`; the left calibration remained byte-for-byte unchanged. The leader-only two-arm hub check passed and is recorded at `C:\Users\pickm\AlohaMini1Logs\am1-corrected-right-leader-hub-check-20260831-141234.log`. The accepted calibration SHA-256 identities are now:

- left: `34D06E15F6768A3290B85BBE3507D9B14A8CCED263A40C575E02010560E13FBE`
- corrected right: `C5F04F97B2B4B371EF4C4292616E7BBCAAE3987805930DE46CAEB3C614D2950C`

No follower wrist reflection remains. The Pi follow-up `ee3a6f5dd813be82780a6a9b1789966357542d2f` removes the process-local right-wrist `drive_mode` override and preserves the ordinary calibrated direction for every AM1 path. It retains the host command-gap, watchdog, limiter, and cadence diagnostics. It changes no calibration JSON, servo register, Phase, PID, torque, ID, baud, limit, or unrelated joint; AM2 and AM2 Pro remain isolated.

The bounded post-correction wrist run reached `TELEOPERATION ACTIVE`, while its private action sender maintained approximately 10 Hz, but the client then repeatedly printed `ZMQ observation request failed: Resource temporarily unavailable`; `observation_sequence` stopped advancing and the one-second stale gate correctly refused with status `2`. This not-yet-passing evidence is `C:\Users\pickm\AlohaMini1Logs\am1-ar1-right-wrist-bounded-windows-20260830-194629.log`.

The failed boundary was observation-request bookkeeping, not action cadence. A timed-out oldest request could be followed by a late response for another still-active token; the old client discarded that valid response, cleared the remaining local tokens, and could then meet a full socket high-water mark with no tracked reply available to drain. Repeated sends returned `EAGAIN`, so the window could not recover to a fresh observation. Windows commit `4f8885b73860b204980c70e92e3daee94b0f35e8` now matches each response token, caches responses for other active requests, discards only unknown/retired tokens, retires only the request that actually times out, drains late replies, and replenishes the exact bounded request window. The cache is cleared on disconnect. The one-second terminal stale refusal and every malformed/partial/nonfinite/wrong-key safety refusal remain unchanged. This repair was fake-tested before the completed physical validation below.

The first AR1-R2H combined validation attempt is recorded in Windows log `C:\Users\pickm\AlohaMini1Logs\am1-ar1-r2h-bimanual10-windows-20260831-211727.log` and Pi host log `/home/pickmanmike/am1-ar1-r2h-bimanual10-host-20260831-211511.log`. The Pi established a quiet hold at approximately 29.9 Hz, but the Windows client refused before constructing `AlohaMiniClient`: the root checkout's editable installation supplied an older `AlohaMiniClientConfig` without `command_send_timeout_ms` while the entry-point script came from the review worktree. No leader COM port or ZMQ connection was opened and no synchronization or commanded motion occurred. The preflight below now pins `PYTHONPATH` to the review worktree and verifies the exact imported configuration source before powered operation.

Windows commit `68cb66673cfba291cfc9f9ea208950d0a42803b6` removes permanent COM defaults from the calibration wrapper and propagates explicit, distinct runtime ports through validation, native calibration, and its follow-up command. Commit `47437569de08a4e4a8875dac728f3ea97d28436e` does the same for the read-only raw bus checker. `tools\report_am1_leader_ports.ps1` reads Windows PnP state without opening a bus and reports CH343 COM port, FriendlyName, PnP InstanceId, and status. It permits automatic role selection only when the stored left and right PnP identities match uniquely and resolve to distinct ports. The verified map is `C:\Users\pickm\AlohaMini1Logs\am1-leader-hub-map-20260831-140827.json`; keep left in `LEFT-LABELED-SOCKET` and right in `RIGHT-LABELED-SOCKET`.

The final AR1-R2H bimanual-arm session passed on the reviewed heads: Windows `065922cf17db273cfe868d1543050bb1094fbfbe` and Pi `ee3a6f5dd813be82780a6a9b1789966357542d2f`. The evidence is:

- Windows: `C:\Users\pickm\AlohaMini1Logs\am1-ar1-r2h-retry-bimanual10-windows-20260831-235812.log`
- Pi: `/home/pickmanmike/am1-ar1-r2h-retry-bimanual10-host-20260831-234021.log`, downloaded as `C:\Users\pickm\AlohaMini1Logs\am1-ar1-r2h-retry-bimanual10-host-20260831-234021.log`

The 120-second startup synchronization completed with a largest final mismatch of `5.534`, below the provisional `10.0` gate. After the start-paused gate, the client ran live for `44.990` seconds and sent `449` actions (`9.980 Hz`). Its longest action-send interval was `110 ms`. Observation sequence advanced from `7` to `1338`; two transient timeouts recovered to a final observation age of `0.0 ms`, and `stale_latched` remained false. The client requested its final zero, cleaned up, and returned `SESSION_EXIT_CODE=0`.

The host's cumulative counters include earlier refused client attempts and the deliberately long operator gates, so they must not be read as live-only totals. Startup synchronization ended at host command sequence `1184`. The first live command was sequence `1185`; its `517758.480 ms` receive gap was the intentional post-sync Enter wait and caused the one expected counter increment. Sequences `1185` through `1633` account for exactly the client's `449` live actions. After that first command, sampled receive gaps were `66.821..100.213 ms`; `receive_gap_over_watchdog_count` stayed at `5` and `watchdog_events` stayed at `6` for the whole live stream. The watchdog advanced once to `7` only after sequence `1633`, representing the expected post-client stop. The host retained an approximately `29.9 Hz` loop, produced no traceback or bus failure, and ended with `HOST_EXIT_CODE=0`.

One live command activated the existing relative-target safety limiter for `arm_right_wrist_flex.pos`, bounding `3.015075376884411` to `0.7737397420867467`. It did not repeat, did not interrupt the 10 Hz stream, and did not correlate with a physical pause or jump. Nine one-second cadence snapshots during startup synchronization also reported `target_limited=true` while the arms were converging. The provisional Pi `max_relative_target=20.0` remains unchanged; do not widen it to hide lag.

The operator confirmed that all twelve channels moved on the correct side and in the correct direction, including right wrist flex in both directions; no follower pause, freeze, resume jump, or concerning sound, heat, current, contact, or cable strain occurred; base and lift did not move; and shutdown was orderly. Every client arm action retained explicit zero `x.vel`, `y.vel`, `theta.vel`, and `lift_axis.vel`. The host emitted one expected warning that lift motion was ignored because the lift was unhomed; this confirms that the unhomed lift gate remained closed. This closes Packet AR1-R2H local bimanual-arm cadence and direction validation. It does not claim base, lift, cameras, recording, or remote operation complete.

No further powered arm session remains in this packet. The completed 45-second command blocks below are retained as exact audit and reproducibility evidence, not as continuing authorization for motion. The prior isolated-wrist procedure is superseded.

Windows preflight, performed before leader power is applied, verifies the reviewed code ancestry, documentation-only tail, unique PnP mapping, and exact calibration identities without opening a COM port:

```powershell
$ErrorActionPreference = 'Stop'
Set-Location 'C:\Users\pickm\lerobot_alohamini_client\.worktrees\am1-wrist-cadence'
if ((git branch --show-current).Trim() -ne 'fix/am1-wrist-cadence') { throw 'Wrong Windows branch.' }
$reviewedCode = '47437569de08a4e4a8875dac728f3ea97d28436e'
git merge-base --is-ancestor $reviewedCode HEAD
if ($LASTEXITCODE -ne 0) { throw 'Reviewed Windows AR1-R2H code is absent.' }
$tail = @(git diff --name-only "$reviewedCode..HEAD" | Sort-Object)
$expectedTail = @('docs/alohamini/alohamini.md', 'tests/robots/test_calibrate_am1_leaders.py')
if (@(Compare-Object -ReferenceObject $expectedTail -DifferenceObject $tail).Count -ne 0) { throw 'Unexpected change after reviewed code.' }
if (@(git status --porcelain).Count -ne 0) { throw 'Windows worktree is not clean.' }

$portMapFile = 'C:\Users\pickm\AlohaMini1Logs\am1-leader-hub-map-20260831-140827.json'
$portReport = (& pwsh -NoLogo -NoProfile -File .\tools\report_am1_leader_ports.ps1 -PortMapFile $portMapFile -AsJson | Out-String) | ConvertFrom-Json
if (-not $portReport.auto_selection_permitted) { throw 'Leader PnP identities are missing or ambiguous.' }
$LeftPort = [string]$portReport.role_matches.left.port
$RightPort = [string]$portReport.role_matches.right.port
if ($LeftPort -eq $RightPort) { throw 'Leader ports are not distinct.' }

$calibrationDir = Join-Path $HOME '.cache\huggingface\lerobot\calibration\teleoperators\so_leader'
$leftCalibration = Join-Path $calibrationDir 'so101_leader_bi_left.json'
$rightCalibration = Join-Path $calibrationDir 'so101_leader_bi_right.json'
$leftHash = (Get-FileHash -LiteralPath $leftCalibration -Algorithm SHA256).Hash
$rightHash = (Get-FileHash -LiteralPath $rightCalibration -Algorithm SHA256).Hash
if ($leftHash -ne '34D06E15F6768A3290B85BBE3507D9B14A8CCED263A40C575E02010560E13FBE') { throw 'Left leader calibration mismatch.' }
if ($rightHash -ne 'C5F04F97B2B4B371EF4C4292616E7BBCAAE3987805930DE46CAEB3C614D2950C') { throw 'Corrected right leader calibration mismatch.' }
"PORT_MAP_FILE=$portMapFile"
"LEFT_PORT=$LeftPort RIGHT_PORT=$RightPort"
"LEFT_SHA256=$leftHash RIGHT_SHA256=$rightHash"

$env:PYTHONDONTWRITEBYTECODE = '1'
$Python = 'C:\Users\pickm\lerobot_alohamini_client\.venv\Scripts\python.exe'
$env:PYTHONPATH = (Resolve-Path .\src).Path
$expectedClientConfig = (Resolve-Path .\src\lerobot\robots\alohamini\config_alohamini.py).Path
$actualClientConfig = (& $Python -c "from pathlib import Path; import lerobot.robots.alohamini.config_alohamini as config; print(Path(config.__file__).resolve())").Trim()
if ($actualClientConfig -ne $expectedClientConfig) { throw "Wrong Python import root: $actualClientConfig" }
$am1LogDir = Join-Path $HOME 'AlohaMini1Logs'
New-Item -ItemType Directory -Force -Path $am1LogDir | Out-Null
```

For the separately authorized test, prepare the Pi on the updated draft PR #1 head and start only the no-camera, unhomed-lift host:

```bash
set -euo pipefail
cd /home/pickmanmike/lerobot_alohamini
test "$(git branch --show-current)" = "fix/am1-right-wrist-normalization"
test "$(git rev-parse HEAD)" = "ee3a6f5dd813be82780a6a9b1789966357542d2f"
test -z "$(git status --porcelain)"

HOST_LOG="$HOME/am1-ar1-r2h-bimanual10-host-$(date +%Y%m%d-%H%M%S).log"
printf 'HOST_LOG=%s\n' "$HOST_LOG"
set +e
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m lerobot.robots.alohamini.alohamini_host \
  --robot_model alohamini1 \
  --no_cameras \
  --skip_lift_home \
  --max_relative_target 20.0 \
  --max_loop_freq_hz 30 \
  --profile_timing true \
  --profile_cadence \
  2>&1 | tee "$HOST_LOG"
host_exit=${PIPESTATUS[0]}
set -e
printf 'HOST_EXIT_CODE=%s\n' "$host_exit" | tee -a "$HOST_LOG"
exit "$host_exit"
```

After the host is ready, run the Windows client from the preflight shell. The resolved `$LeftPort` and `$RightPort` are explicit runtime arguments; neither COM number is treated as permanent identity:

```powershell
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$windowsLog = Join-Path $am1LogDir "am1-ar1-r2h-bimanual10-windows-$timestamp.log"
"WINDOWS_LOG=$windowsLog" | Tee-Object -FilePath $windowsLog
"LEFT_PORT=$LeftPort RIGHT_PORT=$RightPort" | Tee-Object -FilePath $windowsLog -Append
& $Python .\examples\alohamini\teleoperate_bi.py `
  --robot.remote_ip 192.168.1.134 `
  --robot.robot_model alohamini1 `
  --teleop.left_port $LeftPort `
  --teleop.right_port $RightPort `
  --teleop.id so101_leader_bi `
  --teleop.arm_profile so-arm-5dof `
  --startup_mode sync `
  --startup_sync_side both `
  --startup_sync_duration_s 120.0 `
  --max_start_mismatch 10.0 `
  --live_arm_scope both `
  --fps 10 `
  --duration_s 45 `
  --start_paused `
  --no_keyboard `
  --no_cameras `
  --profile_cadence `
  --no_rerun `
  2>&1 | Tee-Object -FilePath $windowsLog -Append
$clientExit = $LASTEXITCODE
"SESSION_EXIT_CODE=$clientExit" | Tee-Object -FilePath $windowsLog -Append
if ($clientExit -ne 0) { throw "Bimanual session failed with exit code $clientExit." }
```

Use arbitrary safe calibrated initial poses, with the physical right follower shoulder-lift at its natural unforced endpoint. Hold both leaders still while typing exact uppercase `SYNC`, throughout synchronization, for at least three seconds after `SYNCHRONIZATION COMPLETE`, while pressing Enter at the start-paused gate, and until the exact `TELEOPERATION ACTIVE` message appears. Only then move one channel at a time initially, followed by slow bounded bimanual motion. The provisional commissioning values remain Pi `max_relative_target=20.0` and client `--max_start_mismatch 10.0`; do not widen either to hide a failure.

Pass requires all twelve channels to respond on the correct side and in the correct physical direction, including the corrected right wrist; no stale latch or observation-request deadlock; no host watchdog event during live forwarding; no multi-second follower freeze, pause/resume jump, or bus failure; explicit zero base/lift throughout; Windows exit `0`; clean client cleanup; and orderly Pi shutdown. A transient observation timeout is recordable only if a later sequence advances, the action cadence remains continuous, and no stale latch or watchdog occurs.

Stop immediately for a wrong side or direction, unexpected or excessive motion, follower freeze or jump, repeated limiter activity, stale transition, observation deadlock, live watchdog, bus/communication error, calibration prompt, sound, heat, current, contact, cable strain, or loss of the power-disconnect path. Stop the host and remove motor power before reviewing or fetching logs. Retrieve the exact path printed as `HOST_LOG` only after the host is stopped:

```powershell
$piHostLog = Read-Host 'Paste the exact Pi HOST_LOG path'
.\tools\fetch_am1_pi_log.ps1 -RemoteHost 'pickmanmike@192.168.1.134' -RemotePath $piHostLog
if ($LASTEXITCODE -ne 0) { throw 'Pi host-log fetch failed.' }
```

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

For Aloha Mini 1 on native Windows, use only [Simple AM1 leader calibration and recovery](#simple-am1-leader-calibration-and-recovery); do not substitute the generic direct command below. The commands in this subsection remain for other supported PC/model setups.

Generic SO-ARM leader (5-DoF, non-Windows workflow):

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

For Aloha Mini 1 on native Windows, do not accept an automatic calibration prompt from a generic client: stop the client and complete the simple staged workflow above first. Never enter client-driven calibration while the Pi host, follower/body power, robot connection, or network client is active. The same rule applies to `record_bi.py`.

For other supported setups, use the same `--teleop.id` and `--teleop.arm_profile` for later teleoperation and recording commands so they load the calibration files created here. If a calibration file already exists, press Enter to reuse it or enter `c` to recalibrate. Outside the native-Windows AM1 path, skipping the standalone step retains the existing `teleoperate_bi.py` behavior: it prompts and can enter calibration automatically when no valid file is found.

> Power-cycle both leader and follower arms after calibration for changes to take effect.

---

## 5. Teleoperation

Native-Windows AM1 teleoperation requires a valid wrapper-managed pair and reviewed no-robot side-check evidence first. If an AM1 client presents a calibration prompt, exit rather than continuing; do not calibrate after starting the Pi host or constructing the robot/network connection.

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

For native-Windows AM1, the same wrapper-managed calibration prerequisite and stop-on-prompt rule applies to `record_bi.py`.

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
