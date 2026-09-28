# AM1 supervised unified Local session

This entrypoint coordinates the already accepted LAN camera viewer, Aloha Mini 1
Local motor host, and native Windows client. It does not replace their safety
logic, add a service, switch power, expose a network listener, or support off-LAN
control. The qualified camera limitation remains: a prior accepted combined run
had a maximum browser display gap of `1.138 s`, above the unchanged `500 ms`
continuity target. Loss of a required view still means release controls, press
`Q`, and restart only after all owned processes have stopped.

## Integrated baseline and reliability follow-up — September 27, 2026

PR #7 is merged into `integrate/am1-local-teleop`, not `main`, at
`865bfd1f4de9a276cebe9650a4fd2e951f492169`. Its ordered parents are
`e1fff50fb190782657aaaadff15acafd67133dc2` and
`f7d00306ab889556c79a4939f32b993f247bba61`. The merge tree is identical to the
reviewed head; the subsequent closeout edits are documentation only.

| Evidence or deployed component | Exact source |
|---|---|
| Physically exercised client/session workflow and motor | `a6a263266888ebc2cd658d0ff5be670632ded3d0` |
| Windows client/session and Pi session helper for the latest ordinary-use attempts | `16c557c05497c94d0bb2e2938c51d4ba56b0a549` |
| Motor used by those attempts, before the logging-order correction | `a6a263266888ebc2cd658d0ff5be670632ded3d0` |
| Motor-only logging-order correction deployed for the next supervised use | `4e16b18b732247072157d3dc64dad6117761ea13` |
| Separately deployed Pi camera, intentionally unchanged | `047c4fcf7cbf34684a9b8c348193585938975815` |

The camera commit is **not an ancestor** of the integration merge. The private
configuration still selects that separate component; merging this PR does not
claim to incorporate every deployed camera change. Keep the working checkouts,
environments, ignored Local/session configuration, camera maps and credentials.
Do not move them to the integration SHA merely for uniformity.

Session `20260927T122640-69179ca1` remains an accepted **qualified** workflow
pass: approximately 63.7 seconds live from the closely aligned shoulder start,
owner-confirmed Q/release stopping, clean component exits and collected logs.
The later `f7d00306` correction retains the AM1 availability request across
cancellable polls and bounds failed-connect cleanup. It does not change motion
admission or prove a cause for separate tracking or camera failures.

Verification retained from that correction: **686 passed / 1 skipped** in the
affected integration run, **28 browser checks**, and **10 final connection
tests**, plus the simulated missing-optional-dependency collection check.
The skipped case is the POSIX process-group test on Windows. Independent
review cleared the scoped findings. These are earlier test executions, not
tests rerun for this documentation closeout. Fresh integration checks verified
the exact heads, merge parents/tree, source references and documentation diff;
no new powered acceptance or long-duration claim is made.

The subsequent `codex/am1-session-reliability` follow-up is separate from closed
PR #7 and targets `integrate/am1-local-teleop`, not `main`. It corrects fault/exit
reporting and one pre-authentication timeout classification, and adds bounded
control-link and resume-input evidence. Its implementation commits are
`a63544d9`, `c36b97e3`, `7f780513` and `178044d0`. Those changes updated only the
Windows client/session and Pi helper. The later motor-only correction below is
separate; the camera remains unchanged. Check all private session pins against
their deployed components rather than deploying every component at one SHA.

Fresh follow-up verification: 285 passed, 1 skipped across the affected session,
SSH reconnect, Local recovery, live-cadence and client-connection test files.
Compilation, helper/client help, fresh imports, lazy visualization/OpenCV checks,
PowerShell parsing and diff checks passed. These are offline checks, not new
physical acceptance; the earlier 686/1 and browser results above remain historical.

The seven reviewed follow-up attempts show distinct pre-authentication failures,
authenticated control-link loss/lease expiry, a bounded startup-feedback refusal,
and a live pause followed by controller-driven stopping. The two child exits of
130 were **not operator cancellation**, according to the owner and control-link
evidence. A later successful large-offset synchronization is recorded below;
the qualified earlier workflow pass is retained. No change here is evidence that
the intermittent transport failure has been cured.

### Return to useful operation: consume feedback before routine logging

The owner reports temporary connectivity recovery after router restarts;
network diagnosis/remediation is an external project, not a prerequisite audit
here or proof of any historical cause. Use ordinary bounded launcher preflight.

The two subsequent attempts both completed home, approximately 10 mm relief,
and synchronization. One briefly entered live control; the other remained at
the final Enter gate. Their first demonstrated host failure was the five-slot
temperature-history freshness check, not confirmed heat or control-link loss.
Routine synchronous telemetry emission occurred between the fresh grouped read
and its action/observation consumers and exhausted the retained history's margin.
Both hosts subsequently verified stopped/zero/torque-off cleanup but retained
exit 1; a conservative supervisor cleanup-unknown label did not erase that
operational failure. Complete evidence stays private.

Correction `a2555838` moves only routine live-sample emission after action,
observation and reply processing. Original timestamps and raw records remain;
fault/transition evidence remains immediate. A pending sample on a consumer fault
is emitted after motor/socket cleanup, without replacing the primary error.
Logging still counts toward the loop budget and the next unchanged freshness
check. There is no extra reader, thread, confirmation wait, relaxed guard or
automatic restart. The motor-only deployment above is the exact four-file
cherry-pick onto its prior source; helper/client and camera pins remain distinct.

New focused verification: **128 passed, 1 skipped** (POSIX-only process-group
case on Windows), including actual fake-host consumer/log ordering, genuine
staleness, log failure, Ctrl+C and preserved final raw evidence. Compilation,
host help/import-root, diff checks and independent code review passed. These
are offline results, not a claim that the correction is physically exercised.

Next ordinary use uses `-DurationSeconds 300` as a ceiling, the same three actual
Enter prompts and nominal 30-second synchronization. The owner can finish a
lightweight task plus brief normal base/lift use and press Q earlier. Preserve
all accepted milestones and shoulder/camera limitations; do not add another
diagnostic campaign. A new genuine fault stops that attempt for exact-evidence
review. PR #8 remains draft and unmerged.

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
  nominally 30 seconds, followed by at most five seconds of bounded endpoint
  completion; Arms mode retains its validated 120-second setting.
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

Use PowerShell 7 in the preserved Windows unified-session worktree, with its
existing ignored session configuration and configured Python environment.
Prepare unobstructed arm and carriage support; power remains a human action.
This is ordinary supervised operation, not another required acceptance test:

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
   alignment plan, verify the right leader shoulder is close to the **current
   measured follower** value. Do not use a historical absolute pose as the
   target or force the follower. If the displayed frozen plan needs a leader
   adjustment, cancel rather than moving the leader during approval; prepare
   a fresh plan on a later ordinary start. With the approved plan, hold both
   leaders still and press Enter for nominal 30-second synchronization.
3. After synchronization, continue holding the leaders still; press Enter to
   perform the final alignment check and enable live control.

The client discards observations requested before each long human pause and
before post-sync verification. It allows up to the existing connection budget
for a new request/reply with a valid receive time and total age below one
second. During unified synchronization, an arm target advances only while
host feedback remains qualified. A short observation gap holds the last arm
target and zero body commands; expiration of the existing connection budget
refuses the attempt without catching up the skipped steps.
After the unified ramp, one fixed five-second completion deadline allows a
lagging follower to reach the unchanged 10-unit alignment gate. The client
continues the frozen, approved target with zero body/lift, completion-spaced
commands, and the same 0.75-unit step and 2-unit leader-drift limits. Any
remaining commanded progression is bounded, never an endpoint jump. Only fresh
post-ramp feedback can qualify; completion stops immediately when it qualifies.
Stale feedback cannot advance the target or renew the deadline. A stationary or
worsening joint still refuses at that deadline; there is no automatic retry.

Completion records include command-send timing and measured right-shoulder
position/error. The unified supervisor opts in through
`AM1_SYNC_SHOULDER_READBACK=1`; ordinary direct hosts do not acquire this read.
With the existing cadence diagnostics enabled, the Pi's single motor owner
also reads the right shoulder's normalized `Goal_Position` at most
once per second while its Local state is `ready`. These records distinguish
requested target, final limited target, completed broadcast write, and immediate
register readback. A completed broadcast write is not a servo acknowledgement;
register readback is not proof of physical convergence. This optional readback
does not run in live control; genuine read/servo errors still terminate through
normal cleanup. Completion was exercised in the accepted `a6a26326` workflow
from a closely aligned shoulder start; arbitrary-pose convergence is not proven.

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

The owner-approved AM1 operational lift guard permits at most one encoder count
(about 0.0205 mm) below the best upward position reached during relief. It logs
that raw variation; the reference does not follow successive downward samples.
More downward travel, uncorroborated positive raw velocity, or repeated
velocity/position direction disagreements still refuse startup. The original
homed zero, 10 mm relief target, 12 mm maximum and 8-second relief bound remain.
The standalone comparison keeps its stricter direction rule.

After a complete fresh stopped window, velocity-only uncertainty may requalify
for at most one second from the last valid window, with position confined to
one fixed one-count band and lift goal zero. Only a complete new stopped window
clears that uncertainty. A requested nonzero lift velocity or height action
during uncertainty refuses the session rather than queuing motion. Displacement,
gross velocity, current, temperature, status and transport faults remain stops;
partial windows and bad samples cannot renew the deadline. Transition records
retain the raw evidence. This is a bounded operating policy, not proof that the
historical velocity readings were false or that the hardware cause is resolved.

Normal AM1 lift temperature confirmation uses five **occupied 100 ms time
slots**, anchored to the first genuine feedback sample and retained across
startup, homing, relief, live use and healthy shutdown. Each slot keeps its
highest actual reading and that reading's timestamp. Faster polling cannot
create additional votes, and a later low within a slot cannot erase its high.
Three of the five slot maxima at or above **55 C** latch a stop. All five
retained readings must remain within **0.5 s**; missing, invalid, backward or
stale feedback never becomes a normal temperature. A genuine fresh, cool
baseline spanning five slots is required before activation. Only the existing
torque-off baseline is lengthened; no confirmation sleep, second reader or
thread is added to the ordinary control loop.

Raw high readings remain in the logs with bounded warnings, including the
reading which causes refusal. Status/error, checksum/transport, current,
voltage and motion faults are not filtered. This owner-approved timing-policy
refinement is not a sensor repair or proof of safe physical temperature. The
September 26 pre-stop cluster `[33,58,33,73,81]` occupied two high time slots;
offline replay stops at the original refusal and does not use post-shutdown
lows to predict continued motion. A new powered attempt may still refuse if
high readings persist into a third slot. Neither a clean replay nor a short
startup pass closes the retained gradual-heating evidence.

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

## Retained limitations and follow-up

- A later run synchronized the right shoulder from about +99.069 toward -5.936,
  reaching -1.604 and passing the unchanged gate. Large-offset tracking therefore
  succeeded in that pose. It does not erase the earlier 13.402-unit plateau at a
  different target after sampled goal-register delivery or establish arbitrary-pose
  convergence. Continue matching the leader to the current measured follower for
  ordinary startup; keep the existing gate and bounds.
- Intermittent Forward-camera acquisition can fail before any motor startup.
  Replug recovery does not establish a loose connector or a network cause.
  Preserve that refusal and its cleanup instead of automatically retrying.
- The owner reports browser deterioration during movement, with no established
  cause. This is distinct from acquisition failure. Source freshness is not
  uninterrupted browser viewing or physical scene-to-display latency; retain
  the historical **1.138-second** maximum display gap and required-view stop.
- Short supervised success does not establish long-duration thermal stability,
  arbitrary-pose tracking, unattended operation or remote-use readiness. Raw
  lift-feedback limitations and genuine historical faults remain recorded.

Session reliability takes priority over a camera-only follow-up. The saved host
sensor loops continued near 30 Hz during Windows observation gaps; this does not
prove that a reply was delivered or distinguish a network failure from every
possible queuing condition. No demonstrated defect justifies larger queues,
longer motion-freshness/watchdog/lease limits, or automatic supervisor relaunch.
An offline disposable control peer verifies that waiting for the first Enter
does not itself stop heartbeats. Actual local ZMQ peers cover short-gap recovery
and fresh-feedback-qualified manual resume; these are not physical tests.

At the next ordinary supervised use, retain the existing automatic evidence and
the added diagnostics below. If it stops, inspect that exact failure rather than
repeat unchanged. Camera acquisition, browser delivery and display correlation
remain separate follow-ups; no new acquisition refusal appeared in the seven
reviewed attempts, and four reached all-five readiness. Do not add a duplicate
reader, global USB reset or a required repeat commissioning campaign. Detailed
session evidence remains private; this is only a sanitized current-state summary.

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

The reliability follow-up adds:

- `client-stop.json` and `cleanup.client.stop_context`: the first **observed**
  controller stop trigger (explicit Stop, Ctrl+C or remote fault), timestamp and
  fault detail. A remote-driven child 0/130 becomes session status 2, while the
  raw child/wrapper codes stay recorded. A pre-existing client safety refusal
  stays primary. This is not proof of ordering between simultaneous signals.
- `cleanup.control_link`: attempted/completed/failed writes and separate
  heartbeat counts/timestamps that survive the later STOP write. Successful pipe
  writing is not proof of remote receipt. Pi transition/state records include
  receive counts, last heartbeat time and contact age at cleanup. These are
  bounded counters, not a per-heartbeat file log or a new acknowledgement protocol.
- `am1_local_resume_input_received`, then (only when its gates pass)
  `am1_local_resume_qualified`, then host-acknowledged `am1_local_recovered`.
  The final cadence record includes recovery state/epoch and input disposition;
  `manual_input_qualified` means permitted to request resume, not host acknowledgement.
  Enter receipt alone cannot authorize motion. Final `stale_latched=false` alone
  is not a freshness claim: inspect observation age and recovery state.

No per-run private-config copy existed in the seven older uploaded folders.
Their summaries record Pi source pins; Windows command headers identify the
script/environment, with commit identity supported by the enforced matching
local preflight. Do not describe missing snapshots as present.

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
