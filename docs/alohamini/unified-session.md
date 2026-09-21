# AM1 supervised unified Local session

This entrypoint coordinates the already accepted LAN camera viewer, Aloha Mini 1
Local motor host, and native Windows client. It does not replace their safety
logic, add a service, switch power, expose a network listener, or support off-LAN
control. The qualified camera limitation remains: a prior accepted combined run
had a maximum browser display gap of `1.138 s`, above the unchanged `500 ms`
continuity target. Loss of a required view still means release controls, press
`Q`, and restart only after all owned processes have stopped.

## Compact design

- Windows is the session controller and remains the interactive owner of `SYNC`,
  final Enter approval, keyboard controls, and `Q`.
- One foreground SSH connection starts one session-scoped Pi supervisor. The
  supervisor starts only the existing camera and Local launchers, each in its own
  recorded process group. It has no listener, requires a bounded controller
  heartbeat, and treats controller loss or EOF as a stop.
- Camera startup must report its exact log, URL, five configured roles, and one
  all-fresh status sample before the browser opens. Exact `READY` approval is
  required before the motor host starts.
- The motor host must report its exact log and structured `operational_ready`
  state after homing and lower-stop relief before the Windows client starts.
- The client retains a completion-spaced 10 Hz sender, body-command expiry,
  follower freshness refusal, one-second Pi watchdog, zero base/lift during
  synchronization, and no catch-up bursts. Local synchronization is nominally
  30 seconds; Arms mode retains its previously validated 120-second setting.
- Live duration is an explicit whole number from 1 through 1800 seconds. The
  live clock starts at `am1_client_live_start`, after synchronization and final
  approval. Direct `run_am1.ps1 -Mode Local` still defaults to 30 live seconds.
- Shutdown order is client final-zero/disconnect, owned motor host cleanup,
  owned camera cleanup, then exact-log collection. No `pkill`, process-name-wide
  takeover, service, or automatic restart is used. A cleanup state that cannot
  be verified is reported as unknown, not success.
- Runtime output goes directly to the existing on-disk logs. Disposable display
  and countdown processes may follow those files, but cannot backpressure the
  client or lifecycle owner. The control link carries only bounded lifecycle
  events, and log transfer begins only after motor and camera cleanup.

## One-time private setup

Copy `config/am1.session.example.json` to ignored
`config/am1.session.json`. Record the already established Windows Python,
ignored Local config, log directory, `am1-pi` SSH target, deployed session-helper
checkout/head, clean camera checkout/head, clean motor checkout/head, and their
existing Pi paths. Do not put camera credentials or role maps in this file.

The reviewed wrist display update is one-time and idempotent. It changes only
private browser metadata:

| Private map entry | Reviewed old | Explicit target |
|---|---:|---:|
| semantic `wrist_left` / `preview_5` | 270° | 90° |
| semantic `wrist_right` / `preview_3` | 90° | 270° |

Forward, chest, and backward remain 180°. The updater verifies semantic and
numbered device paths are equivalent before writing, backs up both mode-`0600`
files, preserves identities and permissions, and makes no native JPEG change.
An unexpected map or rotation is a refusal, not a guessed migration.

## Everyday supervised use

Use PowerShell 7 in the reviewed Windows unified-session worktree. Prepare
unobstructed arm and carriage support; power remains a human action. A first
post-change observation should use 60–90 live seconds, not a commissioning
suite or 30-minute endurance run:

```powershell
.\tools\run_am1_session.ps1 -DurationSeconds 90
```

The command performs software/source/ownership preflight with no hardware
access, starts the camera owner, opens the existing authenticated browser URL,
and then asks for exact `READY`. Before typing it, verify all five required views
and the physical envelope. The Pi host then homes and relieves the lift and must
reach `operational_ready`. The client preserves the existing exact `SYNC` and
post-sync Enter gates. Hold leaders still until `TELEOPERATION ACTIVE`.

During live use, release motion keys before changing support. `Q` is the normal
single quit action. Duration expiry follows the same shutdown path. Either now
releases torque through host cleanup, so the arms and carriage must already have
a safe supported resting position.

From another PowerShell only when the foreground controller is unavailable or
an early stop is needed:

```powershell
.\tools\run_am1_session.ps1 -Stop
```

This writes the active session's cooperative stop request; setup prompts,
synchronization, the final pre-send gate, and live control all observe it. The
controller stops its exact client first. If the controller is gone, the command
asks the recorded Pi supervisor to clean only its verified session children. It
never kills unrelated Python, camera, or motor processes.

## Evidence and recovery

Each run creates:

```text
<configured AlohaMini1Logs>\am1-session-<session-id>\
```

The folder contains the exact Windows client log, exact copied host/camera logs,
`ssh-control.log`, and `session-summary.json`. The summary distinguishes the
requested and measured live interval, synchronization timing, reviewed source
heads, process exits, cleanup verification, and copy result. Remote originals
remain in place.

If a copy fails, `missing-logs.json` records only the exact missing remote paths.
Retry collection without camera or motor startup:

```powershell
.\tools\run_am1_session.ps1 -CollectOnly -SessionId <session-id>
```

If cleanup is unverified or SSH state is unknown, do not restart. Release all
controls, use the accessible motor-power removal, support the carriage/arms, and
inspect the recorded session before any later supervised start.
