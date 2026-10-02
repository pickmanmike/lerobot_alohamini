# AM1 supervised unified Local session

This entrypoint coordinates the already accepted LAN camera viewer, Aloha Mini 1
Local motor host, and native Windows client. It does not replace their safety
logic, add a service, switch power, expose a network listener, or support off-LAN
control. The qualified camera limitation remains: a prior accepted combined run
had a maximum browser display gap of `1.138 s`, above the unchanged `500 ms`
continuity target. Loss of a required view still means release controls, press
`Q`, and restart only after all owned processes have stopped.

## Windows Control console candidate (not yet physically accepted)

From the reviewed Windows checkout in PowerShell 7, with the existing private
`config/am1.session.json` and configured Python environment:

```powershell
.\tools\run_am1_console.ps1
```

This opens only `http://127.0.0.1:8765/`. Opening or refreshing the browser does
not start cameras or motors. The private session config must contain an absolute
`console_camera_auth_file` pointing to a user-only JSON file with `username` and
`password` for the already-deployed Pi camera viewer; neither file belongs in
Git. If the port is occupied, the launcher refuses instead of starting a second
controller. The CLI fallback remains `tools/run_am1_session.ps1` with its
existing exact-SHA configuration, Stop and CollectOnly modes.

Start on the Control page prepares one ordinary physical-leader Local session.
The existing Pi owner performs actual camera readiness, one home and approximately
10 mm relief, nominal 30-second startup alignment, and 10 Hz live forwarding.
The browser never reads leaders or owns a motor socket. During live use, hold
W/S/Z/X/A/D for base and U/J for lift; release zeros body input. Leaving Control,
losing focus or a stale browser lease clears body input and requests measured-arm
pause. Resume and any exceptional realignment require explicit on-page approval
and fresh host/follower/leader qualification. Q on Control or Stop from any page
requests the existing exact-session cleanup. Do not treat a returned Stop request
as verified shutdown: wait for the final session result. The exact result folder
is under the private configured `windows_log_directory`, named
`am1-session-<session-id>`; CollectOnly can retry missing log collection without
starting hardware.

If a startup input lease was released, the pending `sync_start` or `live_start`
gate shows **Continue startup**. Hold the leaders still, release all body input,
and click it to approve only that displayed gate with a fresh empty lease.
Fresh heartbeats alone do not clear the latch; stale/wrong-stage approval is
refused. Live Pause/Resume still requires the existing host/follower/leader
qualification, and startup approval never enables body motion before live
admission. The followers automatically synchronize to the frozen leader target;
manual pose matching is not a prerequisite. Hold leaders still during the ramp
and keep the full follower path clear. The existing gate/bounds and documented
arbitrary-pose shoulder limitation remain: a failed alignment refuses live use.

Stay on the focused Control page during live operation. Blur, hidden document,
page navigation and failed input/state requests intentionally release input;
returning focus does not rearm. Release controls, read the displayed pause cause,
and explicitly approve the current Resume gate. The first local input-pause cause
is retained separately from later expiry or SSH/controller-loss symptoms. Stop
does not wait for a pending approval request and cancels that pending approval.

If a required camera view is unavailable or only a retained image remains,
release body keys, Pause, then Stop if the view does not promptly recover. Confirm
the owned session has stopped before reopening the view or starting another
session; a last frame is not a live driving view. Small right-elbow tracking,
arbitrary-pose shoulder alignment, intermittent camera acquisition/browser gaps,
and long-duration or unattended use retain the limitations below. This console
source has passed offline fake/browser checks. The camera-only layout/start/stop
check passed with five fresh sources, but the attended Control check is not yet
accepted. Earlier attempts refused on a disconnected leader supply or a startup
approval timeout. The later automatic sync completed 301 frames in 30.968 seconds
at the unchanged tolerance, worst error 5.766. Native live admission was followed
by a local input-lease pause, then a separate SSH/controller-loss failure and
status 2. Recovered evidence verifies host/camera exits 0 and cleanup. This is a
sync pass, not a manual live-control pass. Its initiating local event was not
captured; the new bounded evidence cannot retroactively establish that cause.

The focused live-close follow-up passed 95 affected Python checks and 17 Node
UI checks. Eleven Python cases exercise the actual frontend, loopback service
and Windows named pipe with synthetic camera/robot feedback, including request
loss, focus/navigation release, explicit recovery and Stop during an outstanding
approval. Under desktop load, a real browser input gap can exceed the unchanged
250 ms limit: those cases retain the safe pause and require explicit recovery,
not an invented heartbeat or a claim of uninterrupted cadence. These are offline
results, not a replacement for the pending attended Control check.

The Servos page distinguishes the physical leader and Pi follower identities.
It displays normalized position and the action actually sent; per-servo current,
temperature and status remain **Not sampled** until a correctly identified
same-owner source exists. The System page shows cached Pi OS snapshots, body
observations, host state/epoch, action cadence and camera-source health. A Pi
acquisition timestamp is retained, while the displayed sample age is only a
lower bound from Windows receipt because SSH transit and clock skew are not
measured. Configured source pins are labeled expected until the session's
preflight reports exact source heads. Stale or failed camera status never makes
a retained frame live.

Logs and Terminal display bounded original-output excerpts during the session.
The existing Pi supervisor forwards up to 1536 bytes per component every 250 ms
from its exact owned host/camera logs over its existing control connection;
there is no second SSH tailer or motor reader. Forwarding is best effort and
does not wait/retry on a blocked display. Byte-offset gaps, truncated excerpts,
session/source/path, acquisition time and retained output are explicitly labeled.
The console retains at most 128 KB per component. Windows client and SSH output
use bounded snapshots of the exact current result files. Output acquisition
time is a file-read time, not the original line's creation time; original line
timestamps remain unchanged where present. A quiet/aged excerpt is retained,
not proof that its process is currently live.

The local filter shows at most 400 matching lines, with source/severity filters,
search and follow/pause. Export still downloads only an exact collected file
from this session's result folder, refusing missing, ambiguous and over-2 MB
files. For a missing Pi log, use `-CollectOnly -SessionId`. The summary is
available after cleanup. Terminal follows the chosen original output as a
read-only view, plus bounded lifecycle/fault events; it has no command input
or execution route. Changing any page releases
browser body keys; the global Stop remains available. None of these pages starts a second motor
reader or changes the original cleanup result.

## Current state — teleoperation closeout, September 29, 2026

Local teleoperation is reasonably functional for **supervised LAN hobby use**.
The latest attended P16 run completed home/approximately 10 mm relief, the full
88/88 ArmSmoke trajectory, normal cleanup and all component/session exits 0.
This is retained physical evidence, not a new powered acceptance run for this
source extraction. No further teleoperation commissioning is a prerequisite to
the separate automatic charging-dock phase; that phase is not implemented here.

Production correction `f99f7e906a0c6fec862e4af3657ed71a492e21fb` extracts only
the exercised ordinary AM1 idle-span change from diagnostic source `8adf84c9`:
0.10 mm floored through the actual lift conversion (four counts currently), in
both local idle-span paths. The independent fixed 0.5 mm whole-idle drift guard
is retained. Strict startup, homing, relief and cleanup criteria, raw evidence,
temperature/current/status/transport checks and other models are unchanged.
This is an operating allowance, not a manufacturer accuracy specification.

The clean production branch starts from integration
`08fff2fbfaad8841e5a38f9c6da73730cd531164`; its lift monitor and focused test
file exactly match exercised motor `115badc35a09d1e02b81b532cdfe92f2133892ca`.
Fresh extraction verification: **218 passed** in the operational-lift,
lift-relief and motor-feedback test files. Earlier RED/GREEN and physical results
remain historical. Compilation, diff/scope/added-secret-marker checks and focused
independent read-only review passed. No powered test was run for this extraction.

Production PR **#11 is merged** into `integrate/am1-local-teleop`, not `main`,
at `43d1622a9395cdc1d1f9acce1090ed3f029f4f7c`. Ordered parents are
`08fff2fbfaad8841e5a38f9c6da73730cd531164` and
`4ed2097360b26672b09b93e890017c2161dd9a52`; the merge tree equals the reviewed
and tested production head. This post-merge identity record is documentation
only; it does not require another motor deployment or powered check.

| Component | Exact deployed source |
|---|---|
| Pi motor, now clean integration source | `43d1622a9395cdc1d1f9acce1090ed3f029f4f7c` |
| Windows client/session and Pi helper, unchanged | `0c4f2e7ccce3ddcce6d75e7113ed819d07f1d192` |
| Separate Pi camera, unchanged | `047c4fcf7cbf34684a9b8c348193585938975815` |

The clean, stopped Pi motor checkout was switched non-destructively to the
integration branch; its diagnostic branch at `115badc3` remains. Compile,
import-root/policy, host help, Bash syntax and Local `--print-command` checks
passed without opening hardware. Only the ignored motor-head pin changed, with
a private backup outside Git; all three pins match their components. Windows
session help/configuration validation passed. Existing environments, private
Local configuration, maps, rotations, credentials and raw evidence were retained.
The preserved helper build still has historical opt-in diagnostic switches;
they are retired/unsupported with this clean motor and are not ordinary-use
commands. This deployment does not incorporate the separately pinned camera
lineage into the integration history.

P20 demonstrated no tracking benefit and is retired. The clean baseline contains
neither its launcher option nor `arm_gain_trial`, and omits the specialized
selected-joint capture. PR **#10 is closed unmerged**; its branch and private
evidence remain historical. PRs #8/#9 remain closed;
scripted input already integrated through PR #9 remains the regression tool.
Right-elbow small-signal tracking is incomplete: sampled goal delivery accompanied
some measured movement and incomplete return. This is a known **nonblocking**
limitation, not a demand for another recording or commissioning run. Reopen only
if practical manual use exposes a material usability problem. Keep P16 and the
existing profile, calibration, gains and limits.

Ordinary startup automatically aligns followers to held-still physical leaders;
manual matching is optional, not required. Historical shoulder/arbitrary-pose, camera acquisition/browser delivery
(including the 1.138 s display gap), long-duration and unattended-use limitations
remain. Network infrastructure remediation is external. See the short
[everyday reference](#everyday-supervised-use); historical records below are not
new pending commissioning requirements.

## Historical integration and tracking status — September 28, 2026

PRs #8 and #9 are closed and merged into `integrate/am1-local-teleop`, not
`main`, in the authorized order. Both are ordinary two-parent merges:

| PR | Merge | Ordered parents |
|---|---|---|
| #8 | `6df1af1285b0d698125e1a7d969f2872a0c2771d` | `bf1ba5451e1190a609e228c007aad2c0dc2a5c48`, `80ea84c7d474771d870dd9add5f096e12fab2af4` |
| #9 | `91f9ec48883026cfb4ac90a110ee0568b7182bec` | `6df1af1285b0d698125e1a7d969f2872a0c2771d`, `4bbf1a313776316bf6afb7d4dbe320d4e025e193` |

The final merged tree is `a7d51f2dc099da55cd1e922949ad3c029a749870`, exactly
the reviewed PR #9 tree. PR #9 was retargeted only after #8 merged, and its
remaining diff and candidate were rechecked. Current-state edits after the merge
are documentation only. No dependency branch, working checkout or environment
was deleted. The intermediate #8 relief timing policy is not the final stack:
the final stack retains the one-second initial qualification and bounded
same-owner consumer refresh.

Fresh verification of this candidate: **199 passed, 2 skipped** across
`test_am1_unified_session.py`, `test_am1_scripted_launchers.py`,
`test_am1_ssh_reconnect.py` and `test_alohamini_postq.py`; all 16 changed Python
files compiled, both changed PowerShell launchers parsed, and diff plus
changed-path/secret-marker checks passed. Independent PR #8 and incremental
PR #9 review found no material blocker. The earlier 243-test execution below is
retained as earlier evidence, not relabeled as a new run. No powered check was
performed for integration.

Session `20260928T205156-707fef5f` retains three separate verdicts: lifecycle
passed with verified cleanup and clean component exits; ArmSmoke completed
88/88 trajectory seconds in about 92.395 seconds live without recovery or a
live watchdog event; complete per-joint tracking remains unproven. Eight ordinary
empty polls preserved progression. The authorized batch ended after its first
successful profile; unused attempt slots do not require a repeat. This does not
explain every historical delay or establish long-term network reliability.

Deployment remains intentionally unchanged:

| Component | Deployed source | Relationship to merged code |
|---|---|---|
| Windows client/session and Pi helper | `e1efd2e41e30701218f946a4714a74c123152b7c` | Client examples and session/helper tools match |
| Pi motor | `1514c50a4ba9b7762fc577a08fddf02498733386` | Motor host, robot, lift and motor-support code match; the differing outbound `alohamini_client.py` is not used by the Pi motor owner |
| Pi camera | `047c4fcf7cbf34684a9b8c348193585938975815` | Separate camera lineage and runtime differences remain; this merge does not incorporate every deployed camera change |

The four deployed checkouts and ignored exact-SHA session references were
verified consistent without changing them. Preserve private configuration,
calibration, camera mappings, rotations and credentials.

Tracking is a separate follow-up, initially the right elbow and left shoulder
pan. Existing segment observations are phase-boundary samples: the printed
`trajectory_s` has crossed into the next phase, while `requested` is the previous
generated action that was just published to the sender mailbox. The origin-hold
residual is measured displacement from the frozen origin at the end of that
short hold, not a measurement of servo goal-register return or a guarantee of
settling. The final summary provides a later stationary-feedback checkpoint.
There is no selected-joint goal-register readback in this run; the right-shoulder
readback cannot substitute for it. Preserve the observed incomplete excursion
and return without diagnosing reversal, a failed servo or shared deadband.
That closeout led to the separately retained diagnostic capture. Its tracking
limitations are now nonblocking as recorded above. Do not change gains, current
limits, calibration, profile amplitude/dwell or tracking tolerance.

### Completed scripted follow-up — historical implementation evidence

The approved empty-poll bookkeeping repair is source
`a26690a626fba5fb7624deb714caf35e58e81b08`. An ordinary empty observation poll
does not reset the script clock while the previously qualified active state is
still fresh. It does not sample the leader, publish a target or advance the
trajectory. The next genuinely fresh sample must still pass the existing locked
active-epoch/freshness checks, fixed origin and one-frame progress cap. Real stale
replies, pauses, faults and cancellation retain their existing freeze/stop paths.
No motor, camera, profile amplitude/dwell, freshness or network limit changed.

New actual Local-loop/client-request-window/sender loopback cases first reproduced
zero trajectory progress with intervening empty polls (two intended failures;
the no-empty control passed). The corrected cases cover fast and slower valid
replies, emitted bounded commands, body zero, duration expiry and cleanup.
Fake-time cases separately cover stale feedback, repeated recovery and immediate
versus delayed Enter. Final affected verification: **243 passed** across scripted
leader, Local recovery and Windows leader-client tests. Compile, help, fresh lazy
imports, source-root and diff/artifact checks passed. Independent review found no
material blocker. These offline results do not simulate physical joint tracking.

Windows client/session and Pi helper are deployed at the matching two-file
cherry-pick `e1efd2e41e30701218f946a4714a74c123152b7c`. Motor
`1514c50a4ba9b7762fc577a08fddf02498733386` and camera
`047c4fcf7cbf34684a9b8c348193585938975815` remain intentionally unchanged.
The ignored session pin was backed up and updated; environments and private
configuration were preserved.

Under the subsequent owner-authorized maximum-three-attempt packet, the **first**
attended run completed the unchanged finite ArmSmoke profile inside its original
180-second duration, with `script_complete`, no live recovery, clean component
exits, verified zero/torque-off/stopped cleanup and complete log collection.
The batch stopped there; no replicate or longer-duration run was used. Ordinary
empty polls were observed and retained without resetting progression. This one
run does not prove every prior delay was caused by that reset: the earlier run
also had real recoveries and an Enter wait, and its missing counters cannot be
reconstructed retrospectively.

Keep three verdicts separate: lifecycle passed once; the full profile completed;
**complete per-joint tracking did not pass**. The previously weak elbow excursion
was revisited without a useful positive response, and some channels retained
return error. Generated targets are not servo goal-register acknowledgement or
visual confirmation. Detailed joint/timing evidence and raw logs remain private.
No gains, minimum-step workaround or profile tuning was introduced. Earlier
incomplete/faulted runs, accepted ordinary motion, shoulder/camera limitations and
long-duration restrictions remain. The integration disposition above supersedes
the draft/unmerged status at the time of this historical evidence.

The script-only cadence summary adds empty-poll/preserved-poll counts, advance
counts and cumulative feedback-call time; manual resume records prompt and input
events separately. `stale_replies` counts returned over-age replies, not every
combined-age failure; `freezes` counts active-clock freeze calls, not every failed
locked commit. Use recovery/fault events too, not these counters as a fault census.

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
| Motor-only logging-order correction used by the later first-relief refusal | `4e16b18b732247072157d3dc64dad6117761ea13` |
| Motor-only initial-relief qualification; later scripted attempt refused before readiness | `699d6eaf19de1dc80dfcd6621406ba7850fae446` |
| Motor-only one-second initial qualification; subsequent ArmSmoke started but did not complete | `b0bce720572e83b7046d1234a997a3064ce118b9` |
| Current motor-only consumer-refresh correction; attended run ended cleanly but profile remained incomplete | `1514c50a4ba9b7762fc577a08fddf02498733386` |
| Windows client/session and Pi helper for the earlier incomplete scripted attempts | `60fcd9bcc4deecae76520b9c35e7120d889414ad` |
| Current client/session and Pi helper; empty-poll correction exercised through profile completion | `e1efd2e41e30701218f946a4714a74c123152b7c` |
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
automatic restart. Motor commit `4e16b18b` is the exact four-file cherry-pick
onto its prior source; helper/client and camera pins remain distinct.

Earlier focused verification: **128 passed, 1 skipped** (POSIX-only process-group
case on Windows), including actual fake-host consumer/log ordering, genuine
staleness, log failure, Ctrl+C and preserved final raw evidence. Compilation,
host help/import-root, diff checks and independent code review passed. These
are offline results, not a claim that the correction is physically exercised.

The later first-relief refusal prompted the separately approved fixed initial
qualification described below. Source correction `18ad0161` passed **156**
affected operational/standalone relief tests after meaningful RED, including
fixed deadlines, late progress, repeated disagreement, faults and cleanup.
Independent review found no blocker; its confirmed-temperature case is now a
permanent regression. Compile, help/import-root and diff checks passed. The
motor-only deployment `699d6eaf` contains the identical four Python/test files;
only the backed-up private motor pin changed. Its Pi compile/import/help and
helper print-only checks passed. No powered attempt was run at that correction's
closeout; the later scripted attempt below exercised it and refused before readiness.

Physical-leader ordinary use uses `-DurationSeconds 300` as a ceiling, the same three actual
Enter prompts and nominal 30-second synchronization. The owner can finish a
lightweight task plus brief normal base/lift use and press Q earlier. Preserve
all accepted milestones and shoulder/camera limitations; do not add another
diagnostic campaign. A new genuine fault stops that attempt for exact-evidence
review. PR #8 remains draft and unmerged.

### Opt-in scripted leader input (stacked follow-up to PR #8)

The focused `codex/am1-scripted-leader` branch starts from reviewed PR #8 head
`80ea84c7d474771d870dd9add5f096e12fab2af4`. The initial scripted feature changes
Windows input and launcher selection, not the Pi motor or camera implementation.
It requires that reliability base. The later, separately approved motor-only
initial-relief timing adjustment is recorded below; it does not rewrite the generator.

```powershell
.\tools\run_am1_session.ps1 -LeaderSource Scripted -MotionProfile ArmSmoke -DurationSeconds 180
```

This is **SCRIPTED LEADER INPUT — REAL FOLLOWER MOTION**, not a simulation.
Physical leaders are disconnected and unused: this explicit mode neither resolves
their PnP/COM ports nor reads their calibration files. The default remains physical
leaders with all their checks, without automatic fallback. Follower configuration,
calibration, genuine host readiness, current observations, cameras, ordinary lift
home/relief and monitoring, watchdogs, controller lease, and cleanup remain real.

Prepare the normal clear arm envelope, empty grippers, carriage support and
accessible stop/disconnect. Normalized bounds alone do not prove collision clearance.
The owner handles power and physical support. Under attended authorization Codex
may answer each actual Enter prompt individually; never queue blank lines. The
three gates remain camera/readiness approval, nominal 30-second synchronization,
and fresh post-sync alignment/live admission. Scripted startup freezes a genuinely
fresh follower pose as its input origin. It may involve no arm movement and does
**not** validate arbitrary-pose or large-offset physical-leader synchronization.

`ArmSmoke` runs one finite cycle: 2 seconds stationary; all 12 arm/gripper channels
in schema order, one at a time, with a 3-unit excursion over 3 seconds, a 0.5-second
endpoint hold, a 3-second return and a 0.5-second origin hold; then 2 seconds
stationary. Near a normalized upper endpoint the excursion points inward. Planned
origins, targets and directions are logged before motion. Its nominal active
trajectory is 88 seconds; 180 seconds is the live wall-clock ceiling, not a reason
to keep moving after completion. Slower polls or recoveries may extend the profile.

Only actual acknowledged live feedback advances the trajectory clock. Repeated
`get_action` calls, startup, paused/recovering states and unusable feedback do not.
Each update advances at most one nominal frame; there is no queued catch-up motion,
no target rebasing to a failed follower, no new serial owner or sender. Existing
measured-hold acknowledgements and bounded resume remain required. W/S/Z/X/A/D,
U/J and speed keys cannot command body motion in this profile; every live action
contains explicit zero base/lift velocities. Q and explicit Stop remain available.

Per-segment structured records distinguish requested coordinates, received normalized
feedback displacement/error, phase and observation sequence. They are tracking
evidence, not a claim that every tiny movement was visually observed or that source
camera fps measures browser quality. Existing camera timing remains available.
`am1_scripted_input_summary` records `script_complete` separately from manual Q,
explicit Stop, Ctrl+C, duration expiry and faults. The session only accepts successful
script completion with verified coordinated cleanup; raw logs remain private.

Ordinary cancellation and collection use the same launcher:

```powershell
.\tools\run_am1_session.ps1 -Stop
.\tools\run_am1_session.ps1 -CollectOnly -SessionId <exact-printed-session-id>
```

No automatic batch runner was added. The original two-attempt authorization was
superseded by the owner's bounded iterative packet: at most three attended attempts,
each answering a stated question after review of the preceding outcome; one
unchanged replicate or a justified duration-only variation up to 600 seconds was
permitted. A fault stops its attempt, manual Stop ends the batch, and uncertain
cleanup or supervision prevents another launch. That batch closed after its first
complete profile, as recorded above. Do not turn it into standing indefinite retry
authority. Preserve shoulder/camera and long-duration limitations and all earlier
accepted physical-leader milestones.

Offline verification for this addition: **561 passed, 1 skipped** across the eight
affected scripted-input, launcher/session, startup, Local and sender test files.
The skip is the existing POSIX-only process-group case on Windows. Compilation,
PowerShell parsing, help/import/lazy-camera checks and diff checks passed. Independent
review reproduced and verified corrections to atomic completion and combined-age
freshness races. Its real sender/host-protocol exercise still used fake hardware;
none of these results is physical tracking or clearance evidence.

The first attended scripted attempt reached all-five camera readiness, and the
unchanged motor reported homing complete. It then refused because initial upward
relief progress did not qualify within the existing 250 ms window. No Windows
client started: there was no synchronization, live admission, generated ArmSmoke
motion, or physical tracking result. This was a lift-startup refusal, not a
temperature-confirmation fault or a demonstrated scripted-input defect.

The motor subsequently recorded zero goal, torque off and a qualified stopped
window. Preserve the operational failure: host exit 1, camera exit 0, session exit
2; the supervisor conservatively retained `cleanup_unknown` because of the host
failure. Complete logs were collected and all session-owned processes stopped.
No second attempt was launched. A later encoder change during cleanup does not
retroactively qualify relief. The cause of the initial lack of progress remains
unresolved; no motor bound or policy was changed merely to get past this refusal.
Detailed evidence stays private. Do not repeat unchanged or reopen passed arm/base tests.

The subsequently owner-approved policy is implemented in source commit `5e8d5c16`:
ordinary AM1 startup now allows **1.0 s**, once, for genuine net upward encoder progress. This is a
deliberate timing-policy adjustment, not a demonstrated servo response specification
or a claim that the failed run would have succeeded. It neither credits cleanup
motion nor reclassifies that run. The first valid progress ends qualification;
the deadline cannot reset or reopen. All other relief and fault guards remain.
There is no automatic further increase if the next attempt fails.

Fresh focused verification: **167 passed** across the operational and standalone
relief files. Meaningful RED first reproduced the old 250 ms refusal. Synthetic
onsets at 306, 612 and 918 ms now qualify; no onset by 1.0 s and late feedback still
stop. Extended-window direction/fault/cancellation cases retain zero/torque-off
cleanup and original error identity. Compilation and diff checks passed. These
are simulated policy results, not retrospective proof of the observed lift motion.

The reviewed two-file motor change was cherry-picked with source provenance onto
the preserved motor lineage as `b0bce720572e83b7046d1234a997a3064ce118b9`. Its
source/test blobs match the reviewed correction. Only the backed-up private motor
pin changed; client/session and Pi helper remain at `60fcd9bc`, camera at
`047c4fcf`. Hardware-free deployed import-root, compilation and help checks passed.

### Subsequent attended ArmSmoke: startup passed, live profile incomplete

One subsequent attended attempt used those exact components and the existing
launcher, answering each actual Enter prompt individually. Initial upward progress
qualified after the former 250 ms allowance but within the approved one second.
Home, full bounded relief, readiness and nominal synchronization completed. The
real scripted arm profile then started; it did **not** reach normal completion.

A recoverable observation-age pause preceded a terminal host refusal in
`get_observation` / `OperationalLift.contribute_observation`: the oldest retained
temperature-slot reading crossed the unchanged 0.5-second freshness boundary
between sampling and consumption. This was not a confirmed temperature rise or
another initial-relief failure. Saved-sample replay through the actual window
class reproduces that boundary; it does not establish the cause of the earlier
observation gap or authorize relaxing freshness. No further timing allowance,
camera/network change, or motor tuning was made.

Feedback showed movement for several exercised arm channels, incomplete return
tracking for some, and no measured displacement for one small elbow excursion.
The last two channels were not reached. Generated targets and profile admission
are not proof of complete physical tracking. Detailed joint/timing evidence remains
private; the earlier qualified physical-leader milestones are unchanged.

The supervisor stopped the client after the host fault, not operator Q. Actual
exits were host 1, client 130, camera 0 and unified session 2. The lift separately
verified zero goal, torque off and a stopped window after settling. Preserve the
supervisor's conservative `cleanup_unknown` classification rather than relabeling
the run as success. Exact logs were collected; subsequent read-only inspection
found no session-owned runtime and clean deployed checkouts. At that closeout no
further powered attempt had followed. The subsequently approved correction and
one attended follow-up are recorded separately below; the original failed exits
and tracking limitations remain unchanged. PRs #8 and #9 remain draft and unmerged.

### Bounded consumer refresh: clean lifecycle, incomplete ArmSmoke

Source `02f93ff7f1e63603fab9502980ac9f8d657b255b` corrects the demonstrated
read-to-consumer scheduling boundary. If raw feedback is still fresh but the
retained history has aged out, each action/observation consumer may request at
most one genuine grouped read through the existing owner. The unchanged full
five-slot, 0.5-second check must then pass. A raw outage, nonqualifying refresh,
motor/transport fault or cancellation still stops; no refusal is cleared.
The bounded pending batch preserves all original sample timestamps and evidence.
No temperature vote, motion limit, initial-relief policy or client behavior changed.

Fresh focused verification: **190 passed** across the operational-lift,
consumer-refresh and Local-recovery test files. Five actual fake-host cases first
failed at the intended consumption boundary. New tests cover active/hold/resume,
both consumers, retained high votes, missing/delayed data, bounded read counts,
zero/cleanup and cancellation. Compilation, import-root/help checks, complete
diff/artifact review and independent code review passed. Earlier broad results
were not rerun or relabeled as fresh verification.

The motor-only cherry-pick is `1514c50a4ba9b7762fc577a08fddf02498733386`;
its three changed blobs match the reviewed source. Only the backed-up private
motor pin changed. Client/session/helper remain `60fcd9bc`; camera remains
`047c4fcf`. These intentionally separate deployments and environments are retained.

One attended run passed camera readiness, home, bounded relief, synchronization
and live admission without the previous lift freshness fault. Lift monitoring
continued through the session without a rejected sample or confirmed heating.
However, repeated observation-age recoveries and slow qualified observation
progress left ArmSmoke **incomplete at the unchanged duration limit**. Clean
exit is not profile completion or a complete joint-tracking pass. Some exercised
channels still had incomplete returns; unvisited channels remain untested.

The client kept its bounded action cadence while the scripted trajectory froze
on unusable feedback and advanced without catch-up. The request/response or
consumer delay is unresolved; this result does not establish a Wi-Fi cause.
One recovery required the existing Enter gate, so its whole paused interval
must not be attributed to transport. Client, host, camera and session exited 0;
zero goal, torque off and a stopped cleanup window were verified, all exact logs
were collected, and no owned runtime remained. No second attempt followed.

At that closeout, the next step was a hardware-free qualified-observation timing
reproduction, not an unchanged physical retry. The subsequently approved correction
and bounded batch are recorded at the top; they do not erase this incomplete run.
PRs #8/#9 stay draft/unmerged. Detailed joint/timing evidence and raw logs stay private.

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
Set-Location 'C:\Users\pickm\.codex\worktrees\am1-unified-session\lerobot_alohamini_client'
.\tools\run_am1_session.ps1 -DurationSeconds 90
```

Physical leaders are the default; reuse the existing PnP role map and calibration.
The configured shared Python environment is selected by the helper; no activation
or reinstall is required. `-DurationSeconds` accepts 1..1800. For a future
explicitly attended regression with physical leaders disconnected, use
`-LeaderSource Scripted -MotionProfile ArmSmoke -DurationSeconds 180` instead.
Do not add historical P20 or selected-joint capture switches to ordinary use.

Release keys and press **Q** to end normally. The controller then stops the host
and camera and collects their exact logs. Have safe arm/carriage support ready
before torque-off. If the foreground controller is unavailable, run
`.\tools\run_am1_session.ps1 -Stop` in this same directory. Logs are under
`C:\Users\pickm\AlohaMini1Logs\am1-session-<session-id>\`; collection-only retry is
`.\tools\run_am1_session.ps1 -CollectOnly -SessionId '<printed-session-id>'`.

The command performs software/source/ownership preflight with no hardware
access, starts the camera owner, opens the existing authenticated browser URL,
and prints the session ID and result-folder path immediately. It then uses three
complete, visible prompts, each accepting only a bare Enter:

1. After all five camera views are fresh, verify the views, physical envelope,
   support, and power-removal access; press Enter to start the motor host.
2. After the host homes and relieves the lift and the client displays the
   alignment plan, verify the full automatic follower-to-leader path is clear.
   Do not use a historical absolute pose as the target or force the follower.
   Manual pose matching is not required. If the displayed frozen plan needs a leader
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
Normal AM1 startup now has a fixed **1.0 s initial direction-qualification
window**, anchored once after the upward command completes. Fresh net upward
encoder progress must qualify before it expires; returning from a one-count
backstep to the initial position is not sufficient. During this initial window
only, an uncorroborated positive raw velocity no greater than the existing
50-raw stationary-feedback bound may await the next sample. The one-count
travel boundary remains active, and two consecutive wrong-sign velocity
samples still refuse. A larger uncorroborated positive velocity, late/missing
qualification or any genuine telemetry fault stops through existing cleanup.
Once upward progress qualifies, the initial window never reopens. Full pending,
qualified and rejected feedback is retained without inventing sample freshness.
The original homed zero, 10 mm relief target, 12 mm maximum, 8-second relief
bound and two-second useful-progress check remain. The standalone comparison
keeps its stricter immediate direction rule; AM2/AM2 Pro are unchanged.

This owner-approved qualification is a bounded operating-policy change, not
proof that the earlier first-sample velocity/position disagreement was false.
Its offline cases model an initial one-count backstep and later upward progress;
they do not claim that the stopped physical attempt would have recovered.
That attempt completed homing but refused before operational readiness,
synchronization or live control. Cleanup verified zero goal, torque off and a
stopped window; the genuine operational refusal remains a failed session.
It did not reach the ordinary loop and therefore neither validates nor
disproves the preceding logging-order correction. The initial qualification was
later exercised by the scripted attempt recorded above, which refused before
readiness; that is not a successful relief or arm-profile result. Focused
fake-clock/grouped-feedback validation of the operational and standalone relief
files passed **156 tests**;
historical motor/camera acceptance remains separate.

After a complete fresh stopped window, velocity-only uncertainty may requalify
for at most one second from the last valid window, with position confined to
one fixed operational 0.10 mm band (floored through the configured conversion)
and lift goal zero. Ordinary rolling idle windows use the same allowance; the
independent fixed 0.5 mm whole-idle displacement reference is not reset by zero
commands or recovery. Strict startup/relief/cleanup do not use this allowance.
Only a complete new stopped window
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

- Right-elbow small-signal excursion/return remains incomplete despite sampled
  goal delivery. P20 did not help; P16 remains. This is nonblocking for closing
  supervised teleoperation, with no required close-up or repeated profile.
- A later run synchronized the right shoulder from about +99.069 toward -5.936,
  reaching -1.604 and passing the unchanged gate. Large-offset tracking therefore
  succeeded in that pose. It does not erase the earlier 13.402-unit plateau at a
  different target after sampled goal-register delivery or establish arbitrary-pose
  convergence. Automatic synchronization remains ordinary startup, with no
  mandatory manual matching; keep the existing gate and bounds.
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
