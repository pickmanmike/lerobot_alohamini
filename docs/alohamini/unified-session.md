# AM1 supervised unified Local session

This entrypoint coordinates the already accepted LAN camera viewer, Aloha Mini 1
Local motor host, and native Windows client. It does not replace their safety
logic, add a service, switch power, expose a network listener, or support off-LAN
control. The qualified camera limitation remains: a prior accepted combined run
had a maximum browser display gap of `1.138 s`, above the unchanged `500 ms`
continuity target. Loss of a required view still means release controls, press
`Q`, and restart only after all owned processes have stopped.

## Compact design

- Windows is the session controller and remains the interactive owner of all
  three Enter-only startup confirmations, keyboard controls, and `Q`. Direct
  non-session launchers retain their existing confirmations.
- One foreground SSH connection starts one session-scoped Pi supervisor. The
  supervisor starts only the existing camera and Local launchers, each in its own
  recorded process group. It has no listener, requires a bounded controller
  heartbeat, and treats controller loss or EOF as a stop.
- Initial SSH establishment permits at most three sequential attempts within
  about 40 seconds, with 3- and 6-second cancellable waits, only when the local
  SSH client trace identifies a temporary failure before authentication and
  command dispatch. Authentication, host-key, configuration, ambiguous link
  loss, or any possible supervisor dispatch is not retried. The latter follows
  exact-session state and cleanup recovery before another session may start.
- Camera startup must report its exact log, URL, five configured roles, and one
  all-fresh status sample before the browser opens. A deliberate bare Enter at
  the complete `CONFIRMATION 1/3` prompt is required before the motor host
  starts; text, EOF, cancelled input, console failure, and a remote-control-link
  fault while the prompt is open are refusals.
- The motor host must report its exact log and structured `operational_ready`
  state after homing and lower-stop relief before the Windows client starts.
- The client retains a completion-spaced 10 Hz sender, 250 ms body-command
  expiry, a one-second motion-freshness boundary, the one-second Pi watchdog,
  zero base/lift during synchronization, and no catch-up bursts. Unified Local
  pauses on a recoverable observation gap as described below; direct Arms and
  Local modes retain their existing behavior. Local synchronization is
  nominally 30 seconds; Arms mode retains its validated 120-second setting.
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
and prints the session ID and result-folder path immediately. It then uses three
complete, visible prompts, each accepting only a bare Enter:

1. After all five camera views are fresh, verify the views, physical envelope,
   support, and power-removal access; press Enter to start the motor host.
2. After the host homes and relieves the lift and the client displays the
   alignment plan, hold both leaders still; press Enter to begin the nominal
   30-second synchronization.
3. After synchronization, continue holding the leaders still; press Enter to
   perform the final alignment check and enable live control.

The client discards observations requested before each long human pause and
before post-sync verification. It allows up to the existing connection budget
for a new request/reply with a valid receive time and total age below one
second. During unified synchronization, an arm target advances only while
host feedback remains qualified. A short observation gap holds the last arm
target and zero body commands; expiration of the existing connection budget
refuses the attempt without catching up the skipped steps.
`TELEOPERATION ACTIVE` is withheld until the host acknowledges the first live
action.
If the current final leader pose differs by more than 10 normalized units,
`ALIGNMENT CHANGED` leaves the session paused. Check the printed current-pose
plan and arm envelope, then one additional bare Enter authorizes one bounded
realignment using the same 0.75-unit step and leader-drift guards. A second
mismatch is a refusal, not an automatic repeat. No body key is active in this
phase. The nominal 30-second sync and any realignment are outside the selected
live-duration allowance.

Do not pre-feed blank lines. Hold both leaders still until `TELEOPERATION
ACTIVE`. Text, EOF, cancellation, or input failure at any confirmation refuses
the transition. A startup failure prints its reason, session result folder, and
actual exit code in the foreground terminal.

Camera startup failures report the exact camera log and, when available, the
failed role or sanitized gateway/owner stage and errno. A viewer process exit
is not evidence that all five cameras failed. Unknown camera startup errors are
not automatically retried; confirm owned cleanup and inspect that exact log.
Initial SSH attempt traces remain in the private session result folder as
`ssh-client-attempt-<n>.log`; do not upload them with public source changes.
Raspberry Pi Connect working through its separate path does not prove direct
SSH health. The September 26 retained SSH journal shows successful quick
reconnections after the Pi's 12:21 boot, but the earlier failure interval was
not retained; no source penalty or server-side setting change is established.

During live use, release motion keys before changing support. `Q` is the normal
single quit action. Duration expiry follows the same shutdown path. Either now
releases torque through host cleanup, so the arms and carriage must already have
a safe supported resting position.

If follower feedback reaches one second old, or a bounded command send is
temporarily unavailable, unified Local prints `PAUSED`,
clears body inputs, and sends only zero body commands while the Pi's existing
motor-owning thread seeds follower arm goals from measured positions. Arm torque
is not released for a recoverable pause. The client retires old observation
requests and requires a Pi-acknowledged hold plus three advancing Pi observation
IDs over at least 0.2 seconds, each with a request-to-reply age below one
second, before resuming. A gap of at most about three seconds measured from the
last fresh observation may resume automatically only with released body keys, still leaders,
and aligned follower/leader positions. A longer gap or moved leader prints
`RESUME-NEEDS-ENTER`; release body keys, check the robot and views, then press
Enter once. The first resumed arm action uses fresh follower positions and
subsequent alignment steps are capped at 0.75 normalized units per send. Do not
move leaders until `RECOVERED`. `Q`, the separate `-Stop` command, and the
session duration continue to operate while paused; expiry without recovery is
a refusal. Manual Enter does not waive the existing 10-unit leader/follower
alignment gate: a larger mismatch refuses the session. The final client
body-zero command is accepted by the AM1 host only
as a stop-and-measured-arm-hold latch; it cannot rearm the session. Its socket
send alone is not delivery proof, so the ordinary Pi host shutdown and cleanup
result still matter. Unusable feedback for 30 seconds, a lost host, or a genuine motor or
telemetry fault ends the run; there is no automatic restart. A recovered gap is
recorded as a warning in `session-summary.json`, not as a successful physical
acceptance claim.

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
heads, recovered-gap warnings, process exits, cleanup verification, and copy
result. Remote originals remain in place.

If a copy fails, `missing-logs.json` records only the exact missing remote paths.
An empty or absent manifest is not proof that every artifact was discovered.
Retry collection without camera or motor startup:

```powershell
.\tools\run_am1_session.ps1 -CollectOnly -SessionId <session-id>
```

The retry makes a bounded query for that exact session's persisted Pi state,
distinguishes unavailable, nonterminal, and terminal state, and discovers the
exact host and camera paths recorded there. It copies only validated paths from
the configured log directory and writes the supplemental result to
`evidence-recovery.json`. It never changes the original `session-summary.json`
or turns a failed operational run into a successful one.

If cleanup is unverified or SSH state is unknown, do not restart. Release all
controls, use the accessible motor-power removal, support the carriage/arms, and
inspect the recorded session before any later supervised start.
