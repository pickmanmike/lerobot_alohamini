# AM1 supervised unified Local session

## AM1-RELIABILITY-03 — current virtual reliability workstream

Execute finite virtual/scripted arm input and automated owned body controls,
with the existing spare P1 observer current throughout useful runs. The owner
authorizes supported software repairs, bounded ordinary runs and qualified
same-session recovery without attendance/readiness/manual-leader actions.
Manual-leader acceptance and independent cutoff development are outside this
packet; older procedural prerequisites do not apply to this workstream.

Preserve actual motor/thermal/current/status/feedback limits, ownership, private
configuration/calibration/mappings, exact separate pins and rollback. Do not
force a powered process freeze, bus fault or overload. Optional camera quality
issues must be recorded separately from required observation coverage and motion
authority. Actual required-coverage loss must hold/freeze, then qualify bounded
recovery; explicit user Pause/Stop and real motor faults are not cleared.

Target one four-cycle ArmSmokeRepeat: 352 trajectory seconds, original seed and
existing amplitudes/rates/holds, within the unchanged 420-second native ceiling.
Completion requires characterized useful outward and return motion, current
observer evidence, focused review/tests and exact deployment/publication proof.
Do not substitute another diagnostic-only result for this completion loop.

The explicit virtual bench uses numeric role health and a fresh nonce-qualified
P1 image. Required coverage keeps a 500 ms causal source-age bound. Tagged
precise local QPC round trips subtract only the original same-P1 elapsed time
between challenge receipt and capture, with a conservative one-millisecond
precision margin and contradiction checks; legacy records retain the full
round-trip bound. Startup stabilization requires a current continuous twenty
seconds of fresh delivery. Cumulative startup gaps and the whole-invocation
uninterrupted verdict remain separate from that current qualification. Transient Windows health-file sharing refusals
retain only an accepted record until its original expiry and expose uncertainty.
All five camera views remain a separate quality result; optional view loss has
bounded role-local reconnects. Required coverage loss freezes the existing
trajectory and permits at most three qualified same-session recoveries, each
within ten seconds. The absolute native deadline remains unchanged. A later
operator Pause, Stop, foreign owner or motor fault revokes automatic recovery.

The supported short ArmHoldBody profile holds all twelve fresh measured arm
targets and permits normal owned body commands for twelve seconds. Other
scripted arm profiles continue to send zero body velocity. Body execution must
declare inspected current scene coverage in addition to P1 arms/lift coverage.

Software validation currently includes nine loaded real HTTP/frontend/native
pipe cases and 49 camera/pure-policy Node tests. These cover optional degradation,
required observation hold/recovery, explicit operator refusal, scripted body
commands, refused Start and existing packet-02 behavior. The first packet-03
thirty-second powered diagnostic completed with verified cleanup and one
qualified same-session observation recovery. Continuous P1 recording covered
the run; selected event images and clips were retained and inspected. The
observer's current startup window qualified separately from cumulative earlier
gaps. Its uninterrupted-delivery verdict remains separate from recovered motion.

The diagnostic exposed feedback clipping at the arm command range, which hid
actual movement and return error. AM1 feedback now retains the calibrated affine
measurement beyond command endpoints; command range and current/relative limits
remain enforced. Encoded targets round inward within the existing relative
limit, and incompatible protected holds refuse before either arm write.
Actual shoulder return and the four-cycle workload remain pending.

The scoped preparation and selected integral-gain experiment passed 153 focused
tests and was staged while the motor owner was stopped. It uses the same
qualified live owner, finite inward preparation, one fresh program seed and the
unchanged original trajectory. The selected I1 experiment records its original
registers before writing and verifies restoration to I0 with torque off.
Actual powered preparation, return and four-cycle completion remain pending.

A subsequent P1 capture acquired all 660 seconds and released normally, while
its receiver failed after delayed return delivery and an SSH connection reset.
Original source acquisition, JPEG, JSON and socket-write timings stayed fast at
matched delay spikes. An original 20-second qualification window is retained
separately from current stale status and the failed receiver verdict. The next
lossless-compression experiment failed current qualification and stopped with
verified camera release; it does not demonstrate improved delivery. It showed
that a forwarding channel can stall while the source-owning SSH process remains
alive. Recovery now rebuilds a forwarding-only connection on a new local port
to the same capture, preserving its remote port, generation, token, original
nonce timestamps, deadline and three-attempt/five-second budget. Original owner
and delivery failure verdicts remain separate from later cleanup. All 77 focused
observer tests pass. The next real capture tests this repair with compression
off; actual motion still requires current delivery and inspected framing.

Plan: [AM1-03 executable plan](../superpowers/plans/2026-10-06-am1-reliability-03.md).
Historical packet-02/01 evidence below retains its original verdicts.

## AM1-RELIABILITY-02 — integration and bounded follow-ups

PR #14 was merged ordinarily into `integrate/am1-local-teleop` as
`7da4cb2ab249f4a83e87edd97fb19e9f2bebc62b`, with parents `8b5f8963` and
`915a32d4`. PR #15 was then retargeted, its actual ten-file incremental diff
reviewed, and merged as `9aa6d3b042a92e538213b0c684fd52d000b9cc7c`, with
parents `7da4cb2a` and `39647399`. Each merge tree equals its reviewed PR
head. Both PRs are closed as merged; remote integration matches. Main remains
`ab4462b713aeb24d0473f1ec6c8812290ab19510`; no protection bypass was used.
The latest posted PR #15 review and final focused code review found no blocker.
GitHub returned no commit status contexts or PR workflow runs for either head.
Prior test results below retain their actual source versions; integration did
not trigger another broad suite or powered trial.

### Restored access and virtual-control results — October 6, 2026

The owner restored both leaders and the spare observer, then directed bounded
virtual arm/body controls without attendance requests or owner leader/key
movements. This supersedes the earlier attendance and owner-movement instructions
for these ordinary checks. Existing guards and excluded fault scenarios remain.
Moving physical-leader input is untested; the 180-second scenario is prepared.

Both original leader identities are present at their existing COM assignments,
and the non-actuating 180-second preflight passes with unchanged calibration.
The intended P1 observer host and existing remote access were verified. Only
the spare camera was initialized and its supported modes enumerated. Two private
1280×720/10 fps NV12, six-second H264 clips without audio were normally stopped
and released, retrieved with matching SHA256 hashes, and completely decoded
(56 and 55 frames). Three images per clip retain decoder presentation timestamps;
two new frames from each clip were inspected. Recording request/acknowledgment
times are separate from within-clip presentation timestamps. The second clip
overlaps arm-01 and shows both arms and the lift, with blocky early video and
some changing positions. It does not quantify three-unit tracking or isolate
a joint. Base supports and the disconnect are outside the view. Unrelated
cameras/services were untouched; imagery and access/device metadata stay private.

These are packet-02 attempts. Keep the packet-01 verdicts below unchanged.

| Attempt | Actual result |
| --- | --- |
| body-01 | Refused before Live: expected motors absent on the left follower/body bus. No native client or pulses. Host 1, camera 0, original session 2 / cleanup_unknown. The owner confirmed follower power was off and restored it. |
| body-02 | 12.086 seconds Live, frontend W/A/U/J press/release, 120 sends, zero feedback timeout/recovery or body-input expiration; session/client/host/camera exit 0 and verified cleanup. |
| arm-01 | 51.503 seconds Live, 48.172/88 trajectory seconds, then Pi temperature-history freshness refusal. One freeze, recovery epoch 1, no completed recovery. Native 130 after fault Stop, host 1, original session 2 / cleanup_unknown. |
| arm-02 | Frontend state-request-failed during camera startup, before motor host/native client/Live/pulses. Owned Stop: operator_stopped/130, verified cleanup, camera 0. HTTP versus JSON/UI-processing cause is unknown. |
| arm-03 | 6.135 seconds Live, 2.105/88 trajectory seconds, then runner Stop on a five-second required-camera-view timeout. Session/native 130, host/camera 0, verified cleanup; 13 empty polls, zero freezes/recovery. |
| arm-04 | One complete 88.000-second commanded trajectory in 88.803 seconds Live, 883 sends; all runtime exits 0, verified cleanup. Three preserved empty polls, no feedback recovery/stale latch. Small-return tracking remains incomplete. |
| arm-05 | One ArmSmokeRepeat attempt: 54.096 seconds Live, 51.009/352 trajectory seconds, 538 sends. Required chest/left-wrist decoded views became stale; owned Stop, session/native 130, host/camera 0, verified cleanup. Ten preserved empty polls, zero freezes/recovery/stale latch; no completed cycle or boundary. |

After body-01 and again after arm-01, a reviewed exclusive one-shot invocation
of the installed normal zero/off routine freshly verified all 16 torque
registers and four body/lift velocity goals were zero and closed both ports.
It held both ownership locks, checked exact motor/source identities, protected
cancellation cleanup, and preserved calibration and each original summary byte
for byte. Seven private fake verifier cases pass. These are separate register
proofs, not retroactive successful cleanup or full motion/thermal qualification.
Each permitted replacement of a stopped idle console before a deliberate new
attempt. No recovery approval or automatic restart was used.

Body-02 operational lift samples were 30–33 C, at most 65 mA, status zero,
11.3–11.9 V, with largest adjacent raw-read gap 61.023 ms. Up/down goal and
measured velocities were ±200 raw; height ranged 10.356–11.074 mm and ended
at 10.356 mm. All-phase logs also retain an unexplained isolated startup 68 C
reading/warning and homing current up to 435.5 mA. Those are separate from
operational maxima. Wheel displacement is unmeasured. Static leader reads and
startup synchronization do not qualify moving physical input.

Arm-01 operational lift samples were 31–34 C, at most 32.5 mA, status zero
and 11.7–11.9 V. The unchanged temperature guard correctly refused five genuine
slot peaks spanning 512.023 ms against its 500 ms limit. Offline replay of
actual acquisition timestamps reproduces the first fault. The initiating
adjacent-read gap was 143.022 ms; the next group read took 1.440 ms. This
locates delay after the prior sample in owner-loop work/scheduling, but the
specific phase/cause was not retained. The old snapshot contained a subsequent
healthy loop and the current refusing loop. Later empty-poll/error output
follows the dead host and is not a successful recovery. Wi-Fi, logging,
servo failure and overheating are not established causes.

Seven completed arm-01 endpoints had maximum error 1.596 normalized units.
Left shoulder-lift responded +2.9975 to its +3-unit command, then stayed there
through the small return request and origin hold. Arm-04 again showed +2.9975
outward response without the small return. Its maximum outbound endpoint error
was 2.6896; final maximum seed displacement was 2.9975. Other joints showed
mixed partial return. Arm-05 left shoulder-lift reached +3.3306 and retained
that displacement through return/origin hold. No repeat boundary was reached.
These are normalized units, not degrees. Outward response is measured; precise
small-return response is unresolved. Gains, amplitudes and calibration stay unchanged.

Arm-04 operational lift samples were 32–35 C, at most 39 mA, status zero,
11.7–11.9 V; largest adjacent gap 60.028 ms. One isolated startup temperature
warning remains in the logs. Its sole script freeze is the normal terminal
finally call after completion, not a feedback pause. Arm-05 operational lift
samples were 32–35 C, at most 39 mA, status zero, 11.7–11.9 V, with largest
adjacent gap 62.022 ms and no temperature warning. Arm-04/05 lift height stayed
within 10.397–10.418 mm. Temperature/current measurements here are lift-only.

### Diagnostics and observation limits

A reviewed host-only diagnostic retains eight completed loop timings plus the
current loop, independent snapshot copies and an explicit omitted-loop count.
It adds no motor reads, control threads, IO, sleeps or periodic output and
does not change temperature policy. RED/green regressions and 14 focused host
fault/loop tests pass; lint adds no finding. Staging used stopped owners, exact
old/new file hashes, a one-file diff, clean detached source and an in-place
private motor-pin update preserving permissions.

The bench now records passive frontend state HTTP/failure/page-error evidence
and bounded per-role decoded-image ages, sequences, source labels and decode
diagnostics. It retains at most 700 records with an explicit dropped count.
Fake frontend 503/disconnect cases were RED, then five affected checks passed.
Camera-loss was RED, then four affected checks passed; the exact readiness
timeout regression also passed after RED. A readiness-failure snapshot starts
without awaiting it before owned Stop. Evidence finalization waits at most one
second after cleanup and preserves failure timing if the snapshot is unavailable.
No served Control behavior, camera threshold, recovery approval or retry changed.
At final bench source 1a8ac290, all seven affected fake-browser cases passed
(39 deselected); AST/JavaScript syntax and diff checks also passed.

Arm-03 recorded 196 frontend state HTTP 200 responses, zero failed state
requests/page errors. Pi source cameras stayed fresh while decoded views were
intermittently stale. A subsequent exclusive camera-only 60-second run exited 0.
Its 35-second read-only browser check retained 24 DOM samples, all five views
fresh, with zero status/decode failures/cancellations and maximum primary display
gap 495 ms. That demonstrates availability, not repair of the interruption.
Arm-04 recorded 316 state HTTP 200 responses and 416 retained Live camera samples
all five fresh; 37 earlier ring records were evicted.

Arm-05 recorded 249 state HTTP 200 responses, zero failed state requests/page
errors, and no dropped records. At the first camera refusal, chest and left
wrist image ages were 1653/1652 ms against the 1500 ms thumbnail limit; the
other views remained fresh. Diagnostics showed three camera-status failures,
maximum status request 1517 ms, and zero decode failures/stream cancellations.
Pi acquisition remained fresh at roughly 15–20 fps with maximum source gaps
under 71 ms. The delay is in delivery of decoded views; its exact layer is
not yet established. No full cycle or continuous-duration pass follows from
this interrupted attempt.

A corrected camera-only 120-second diagnostic then exited 0 without motor
access; a fresh source/owner audit found clean pins and no remaining owner.
Its 105-second read-only browser capture recorded 105 DOM samples, all five
views fresh, with no dropped record, failed request, decode failure or stream
cancellation. It received 213 state, 366 camera-status and 840 snapshot HTTP 200
responses. Maximum snapshot duration was 802.615 ms, mainly waiting for
response bytes after a roughly 2 ms local connection/request start; maximum
status duration was 447.513 ms. This does not isolate network versus proxy/Pi
waiting and is not a matched powered camera repair. The earlier SSH TCP
connection timeout and the mistakenly requested 180-second camera diagnostic
are retained separately: both failed before capture, the latter at the installed
120-second duration guard. That guard was not changed.

### Opt-in finite repetition

Ordinary `ArmSmoke` and its 30/180-second runner options remain unchanged.
`ArmSmokeRepeat` composes four existing 88-second trajectories in one native
Local session. It retains one original fresh frozen seed, inward 3-unit targets,
three-second ramps, original holds, 10 Hz cadence and existing freshness/recovery
rules. No cycle homes, synchronizes, reconnects or starts a new session. The
352 trajectory seconds include the original holds; boundary time is excluded.

At every boundary, including the fourth, three distinct sender-admitted fresh
advancing observations must span at least 0.2 seconds and remain within 3
normalized units of the original seed. A pause clears the partial qualification
window and elapsed wall time. Outside-tolerance feedback refuses through normal
cleanup; no reseeding, corrective jump or automatic retry is permitted.

This is an envelope admission check. Its tolerance equals the commanded excursion,
so it does not prove precise physical return: offline replay of the actual
arm-04 final pose at 2.9975 units qualifies with three advancing synthetic
observations. The replay is not powered evidence. Original-seed retention still
prevents cumulative rebasing across cycles. A successful workload requires one
profile-specific terminal summary, four cycles and four qualified boundaries.
The native ceiling is exactly 420 seconds throughout console/session/native
and PowerShell launch paths. Scripted profiles disable body/lift input.

`PhysicalLeader` remains a separately selected ordinary 180-second session
for actual movements of both physical leaders and 200 ms owned frontend W/A/U/J
pulses. The latest owner-directed virtual checks did not exercise moving leaders.

At runtime source 7249eec3, affected core verification was **304 passed, 1 skipped**
(POSIX process-group test on Windows); affected bench cases were **18 passed**.
Nine affected tests reran after test-only lint repairs. Fake native entrypoint
tests complete four cycles with one connection/admission; real sender tests
refuse stale/lost admission at boundaries. Real frontend/HTTP/Windows pipe tests
verify dispatch and owned cleanup. These are not powered endurance evidence.
AST, JavaScript syntax and diff checks pass. Ruff 0.14.1 comparison adds no
findings against integration (63 inherited findings in seven checked files).

### Working paths, deployment and rollback

Candidate branch: `codex/am1-reliability-02`, based on `9aa6d3b0`, in
`C:\Users\pickm\.codex\worktrees\am1-reliability-01\lerobot_alohamini_client`.
Shell: PowerShell 7. Use the existing
`C:\Users\pickm\lerobot_alohamini_client\.venv` with `uv run --no-sync` and
the candidate root/`src` on `PYTHONPATH`; no installation is required.

The post-merge comparison from Windows 81c10a84 to integration 9aa6d3b0 was
documentation-only and did not justify deployment. Reviewed finite-repeat
runtime 7249eec309f9ebdf4bbc469c1cc888cb2e2fe673 was subsequently staged on
Windows. Pi helper remains 8e6a0cf616cb2000df1d0e996ab27a19d2fb2fba and
camera remains 9b1f0670e7068f7d39eb50270a118e3807418355. Only Pi motor
changed, to host-history diagnostic 6d99b263d609c4048602a1ac5c48dda679f65c74,
parent c3fc683d; its file matches candidate host commit 81034618. The separate
passive bench runner is 1a8ac290, not the served Windows runtime.

Configured deployment directory:
`C:\Users\pickm\lerobot_alohamini_client\.worktrees\am1-console-implementation`.
Existing launch:

```powershell
$env:UV_PROJECT_ENVIRONMENT = 'C:\Users\pickm\lerobot_alohamini_client\.venv'
$env:PYTHONPATH = "$PWD\src;$PWD"
uv run --no-sync python -m tools.am1_console --config config\am1.session.json --no-browser
```

Rollback only while stopped/unowned: restore Windows detached
81c10a8497fda8eea36bac3ea6861f9e6ff5ed05 and only its private Windows pin
from the preserved backup. Independently restore Pi motor detached
c3fc683d645ea1c355e69a72f705a8bbadde6a1a and only its private motor pin.
Preserve private-file permissions, mappings, calibration and separate pins.

Use the reviewed candidate bench with the existing installed Node/Playwright,
`AM1_BENCH_HEADLESS=0`, the actual configured Windows head, and a new private
evidence path/identity for each deliberate invocation. Page opening never Starts.
Ordinary launch syntax is `node tools\am1_reliability_bench.cjs <console-url>
<scenario> <new-identity> <new-private-json> <actual-windows-head>`.
Prepared scenarios are PhysicalLeader (180 seconds) and ArmSmokeRepeat (420).

### Remaining physical evidence and stop capability

The older packet-01 shoulder-lift sample was clipped at normalized 100 during
a 100→97 request. Normalized 100 cannot distinguish physical nonmovement from
raw feedback outside calibration. New outward response establishes movement
within the interval; small return remains unqualified. Existing command and
feedback logs do not establish a servo failure or an active arm current limit.
No competing motor reader/controller or undocumented tuning was introduced.

Read-only installation checks found the existing follower USB aliases and no
configured AM1 independent motor-disable unit. Stop, watchdog and torque-off
cleanup depend on the owning motor process. The physical disconnect is outside
the observer view, so independent cutoff capability is not visually verified
or qualified. Recommend a reachable, appropriately rated local cutoff of AM1
motor supplies that works independently of Windows, Pi and the network.
New wiring/dock hardware is outside this packet. Retain the owner's arm
swing-down confirmation; it does not establish process-freeze protection.
Process freeze, forced bus fault, overload and unrestricted/automatic recovery
remain excluded. AM2 is untouched.

## AM1-RELIABILITY-01 — October 6, 2026

For this packet the owner authorizes finite automated bench operation with the
base raised, and confirms that the arms can safely swing down when torque is
released. This replaces the historical per-session attendance procedure **for
this packet only**. Retain the normal lift home/relief arrangement, real feedback,
source/ownership checks, motion limits and qualified cleanup. A motor-owner
process freeze is tested offline only: its in-thread watchdog cannot run while
that process is frozen; it has no independent hardware-stop guarantee.

### Demonstrated transport correction

Saved session `20261006T011321-9247fa03` completed 90.11815 seconds live with
three follower-feedback interruptions and 131 empty/unusable native polls.
Recovery took 21.265 seconds with manual approval, then 0.547 and 2.343 seconds
automatically. The first feedback pause preceded browser-input expiry by about
3.85 seconds. Replay found 3,984 host live lift samples, no sampled interval above
100 ms, maximum sampled lift temperature 36 C and current 175.5 mA. These records
do not establish the historical network/client scheduling cause or a Wi-Fi fault.

The failing offline case establishes a narrower client defect: waiting for the
oldest missing request conceals another already available tracked reply. AM1 now
consumes the first available reply in request order, retires missing predecessors
and refills the existing three-credit window. The selected reply retains its
original send time. A 1.05-second-old reply remains stale to native qualification;
late retired replies cannot authorize recovery. Other models retain their prior
transport path. There is no new reader, larger queue or relaxed safety deadline.

### Finite runner and current qualifications

`tools/am1_reliability_bench.cjs` uses the real visible Edge Control frontend,
loopback HTTP and native Windows pipe. Its one Start selects the existing
Scripted/ArmSmoke backend option or normal physical-leader body mode. It requires
a new-owner Start response, the exact Windows source pin and actual browser
focus. Prepared startup keeps its existing qualification; manual recovery is
never approved. Required views must be fresh decoded views, not retained blobs.
Loss of a required view or a live latched pause requests ordinary session-bound
Stop. During final monitoring after live admission, an exact native
pipe-disconnected state permits a ten-second monotonic terminal-acceptance budget
from its first observed closed state. Bounded HTTP/DOM reads can defer Stop beyond
that point; this is not a ten-second physical-stop guarantee. This window permits
no further pulses or recovery approval; raw exit zero and explicit cleanup proof
are still required. A late terminal response cannot erase an elapsed deadline.
Monitoring failure cannot suppress Stop. Each attempt has a native live
limit, an outer finite deadline, bounded evidence and a 60-second cleanup-proof
budget; there is no automatic restart. Cleanup failure stays failed/unverified.

Physical-mode non-actuating preflight refused because Windows reports no present
USB leader Ports. Consequently physical-leader movement and powered wheel/lift
press-release scenarios are withheld; Scripted does not establish those results.
Scripted preflight passed with the actual deployed import root. No identified
spare Creality Nebula device was exposed on this Windows host; P1 observer capture
is unavailable here, and the other printer cameras were not touched. Existing
onboard decoded views are available to the runner; no visual motion claim follows
from their availability.

The driver invocation uses existing Node/Playwright and installed Edge. Start the
ordinary loopback console while no session is active; keep its private config and
camera-auth file outside Git. If necessary, set `NODE_PATH` to the existing
Playwright installation. For the current packet's finite arm scenario:

```powershell
node tools/am1_reliability_bench.cjs http://127.0.0.1:8765/ ArmSmoke AM1-RELIABILITY-01-arm-02 <private-absolute-evidence-path> 81c10a8497fda8eea36bac3ea6861f9e6ff5ed05 30
```

The optional final argument permits only the short 30-second native arm limit or
the full 180-second ceiling (default); body remains 12 seconds. The short scenario
is a portion of ArmSmoke ending through its existing duration-expiry cleanup,
not a full-profile completion. Its outer budget is 150 seconds; full-arm is 300.
Do not launch another attempt after an unresolved failure. The same-session
normal CLI Stop/Collect fallback remains available. The 180-second ArmSmoke
ceiling allows the unchanged finite 88-second trajectory to complete; an idle
duration extension or additional home is not dynamic endurance evidence.

### Shutdown corrections and preserved first attempt

Attempt `AM1-RELIABILITY-01-arm-01`, session `20261006T145141-642324c5`, used
Windows `c4c4314c3870b32b45c0f39992d6316889dba3e5`. Native ArmSmoke completed
its unchanged 88-second trajectory in 88.330017 seconds live: 878 arm action
sends, 2,935 observations, zero feedback timeouts, zero recoveries and a maximum
action interval of 110 ms. The runner mistook normal pipe EOF for an input fault
about 197 ms after profile completion and requested Stop before the supervisor
finished. Its original session result remains **operator-stopped / 130**, with
verified cleanup; it is not a successful whole-session attempt.

Fake IO reproduced both this finalization delay and a Windows input-thread
close/read race. The runner now uses the bounded terminal wait described above.
Native disconnect invalidates input immediately, allows an ordinary in-flight
read to finish before closing its handle, and retains the existing total
one-second join budget with fallback closure. A packet returned after Stop is
discarded. No broad exception catch or feedback deadline change was added.

The first attempt's existing host log contains 3,884 operational-live lift
samples, maximum sample interval 60.007 ms, maximum sampled lift temperature
34 C/current 39 mA and status zero throughout. These are lift samples over the
host's operational window, not temperatures for all arm motors or the native
live duration. Fresh arm feedback recorded endpoint movement on 11 of 12 joints,
with endpoint errors up to 3 normalized units. The left shoulder lift remained
at 100 for its target of 97; the final left elbow stayed about 1.992 units from
its seed. Completion therefore establishes bounded profile progression and
feedback availability, not precise tracking or visual motion acceptance.

### Powered attempt ledger

Each attempt used its own real visible browser and new owned session. Cleanup
was verified before another Start; the corrected attempts used Windows
`81c10a8497fda8eea36bac3ea6861f9e6ff5ed05`. All logs and derived evidence remain
under the private packet directory outside Git.

| Attempt / session | Native live / trajectory (seconds) | Live feedback timeouts / recoveries | Whole-session result |
| --- | ---: | ---: | --- |
| arm-01 / `20261006T145141-642324c5` | 88.330 / 88.000 | 0 / 0 | Runner aborted; Stop/130; cleanup verified |
| arm-02 / `20261006T152432-0af03490` | 30.100 / 29.944 | 0 / 0 | Short portion complete; exit 0; cleanup verified |
| arm-03 / `20261006T152715-4cdf7037` | 30.083 / 29.919 | 0 / 0 | Short portion complete; exit 0; cleanup verified |
| arm-04 / `20261006T153008-5bad7ec3` | 90.025 / 88.000 | 4 / 0 | Full profile complete; exit 0; cleanup verified |

The two matched short workloads each produced 299 native arm sends and 1,202
observations, zero empty/stale live polls, and a maximum native send interval of
110 ms. Both ended through `duration_expired`, with `profile_complete=false`.
This establishes repeatable short lifecycle completion rather than completion
of all twelve joint segments. Lift samples peaked at 34/35 C and 45.5/39 mA,
respectively; maximum host sample gaps were 88.007/63.022 ms, with status zero.
All client/wrapper/host/camera exits were zero; no collection warnings or missing
logs were reported.

The longer arm-04 attempt completed all twelve segments and the 88-second
trajectory. Its four empty native polls were preserved by the existing bounded
short-gap policy, with zero stale replies, zero feedback pauses and zero
recoveries. The 90.025134-second live period produced 895 sends and 2,915
observations, with maximum action interval 110 ms and no body-expiry event.
The runner's maximum sampled observation age was 684.348 ms; it is a sampled
display/native telemetry value, not a continuous maximum or a duration for each
empty poll. Individual poll times were not logged. Its 700-record evidence ring
reported 57 rotated early records; the complete collected native/host logs remain
private. Normal native EOF was followed by bounded supervisor finalization rather
than another motion command or Stop that relabeled a clean finish.
Existing host lift evidence contains 3,933 operational-live samples: maximum
sample interval 60.025 ms, maximum sampled temperature 36 C/current 45.5 mA,
status zero and no native-live watchdog event. The host's cumulative command-gap
counter reported no gap over its watchdog threshold; its whole-operational-window
maximum was 402.040 ms, including startup, rather than a native-live gap claim.
All client/wrapper/host/camera exits were zero, with verified cleanup and complete
log collection. Final read-only audit found no Pi runtime owner and all three
component checkouts clean at the unchanged pins.

This is a matched 88-second ArmSmoke runner comparison: arm-01 incorrectly
canceled a native success; arm-04 preserved the supervisor's successful result.
The frozen seeds differ, and the transport correction was already present in
arm-01. The older 90-second physical-input session is a different workload, so
its three pauses/131 empty polls are context, not a matched transport before/after
benchmark. The three offline request-order cases are the controlled comparison:
all failed before the client correction and passed afterward (91 cadence tests).
No new powered run required qualified pause/recovery; those paths remain covered
offline and in the historical recovered evidence.

Arm-04 again measured endpoint displacement on 11 of 12 joints, maximum endpoint
error 3 normalized units and final requested/measured error 2.263 units. The left
shoulder lift still did not follow its small inward target. This packet supports
review/integration of the transport and shutdown fixes after PR #14, while
physical-leader/body motion, precise tracking, P1 visual corroboration and dynamic
operation beyond the tested 88-second profile remain unqualified. The normal
loopback console is left idle; no robot/camera owner remains active.

### Deployment and rollback

Only the clean, stopped Windows console/client checkout was switched to
`81c10a8497fda8eea36bac3ea6861f9e6ff5ed05`, with the independent private
`windows_session_head` updated. Pi helper remains
`8e6a0cf616cb2000df1d0e996ab27a19d2fb2fba`, motor
`c3fc683d645ea1c355e69a72f705a8bbadde6a1a`, camera
`9b1f0670e7068f7d39eb50270a118e3807418355`; all were clean and unowned before
dispatch. Environments, calibration, mapping and DirectBrowser profile were
preserved. The original private config is backed up outside Git. With all owned
runtimes stopped, normally switch Windows back to
`915a32d4d9ac42433c1aee95f4f0c74f348c5dda` and restore that private-pin backup;
do not reset the checkout or change the three Pi component pins.

For a rollback after all owned runtimes have stopped, use the same deployment
checkout and restore only the separate Windows pin (or the original private
config backup, after checking for intervening private edits):

```powershell
git switch --detach 915a32d4d9ac42433c1aee95f4f0c74f348c5dda
$am1RollbackConfig = Get-Content -LiteralPath config/am1.session.json -Raw | ConvertFrom-Json
$am1RollbackConfig.windows_session_head = '915a32d4d9ac42433c1aee95f4f0c74f348c5dda'
$am1RollbackConfig | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath config/am1.session.json -Encoding utf8
```

Normal preflight must pass again before Start. The older DirectBrowser entrypoint
and private local hardware configuration remain available.

### Focused verification

With the existing Windows environment, candidate `src` and repository on
`PYTHONPATH`, installed Edge selected by `AM1_TEST_BROWSER_CHANNEL=msedge`, and
the existing Playwright installation on `NODE_PATH`, the affected-suite command
passed **955 tests, four skips** in 378.47 seconds:

```powershell
uv run --no-sync python -m pytest -p no:cacheprovider `
  tests/robots/test_alohamini_windows_live_cadence.py `
  tests/robots/test_alohamini_local_recovery.py `
  tests/robots/test_alohamini_local_teleop.py `
  tests/robots/test_alohamini_scripted_leader.py `
  tests/robots/test_alohamini_lift_consumer_refresh.py `
  tests/robots/test_alohamini_lift_operational.py `
  tests/robots/test_alohamini_postq.py `
  tests/robots/test_am1_unified_session.py `
  tests/robots/test_am1_console.py `
  tests/robots/test_am1_console_bridge.py `
  tests/robots/test_am1_console_progress.py `
  tests/robots/test_am1_console_telemetry.py `
  tests/robots/test_am1_console_output.py `
  tests/robots/test_am1_console_local_path.py `
  tests/robots/test_am1_scripted_launchers.py `
  tests/robots/test_am1_lean_launchers.py `
  tests/robots/test_alohamini_windows_leader_client.py -q --tb=short
```

The finite 30-second option was added afterward. Its real-frontend dispatch case
failed before correction; the subsequent command passed **15 tests** in 128.92
seconds, including both arm limits, physical-mode fake press/release, real pipe
shutdown, required-view loss, manual pause, foreign-owner refusal, failed monitor
reads, delayed finalization, stalled finalization and late terminal response:

```powershell
uv run --no-sync python -m pytest -p no:cacheprovider tests/robots/test_am1_console_bridge.py tests/robots/test_am1_console_local_path.py -k 'bench or inflight_read' -q --tb=short
node --test tests/cameras/test_am1_console_ui.cjs
node --check tools/am1_reliability_bench.cjs
git diff --check
```

Frontend tests: **34 passed**. JavaScript syntax and diff checks passed. Ruff
0.14.1 (the project's pre-commit pin) found 50 diagnostics in both the exact PR
#14 base and these seven changed Python files, with **zero added findings**.
This is a baseline comparison, not a clean full pre-commit result. The broader
ML/GPU suite was not run. Original failed reproductions and failed powered
attempts remain recorded; no raw logs, private config or household images are
included in Git. Remaining sections retain their historical evidence.

This entrypoint coordinates the already accepted LAN camera viewer, Aloha Mini 1
Local motor host, and native Windows client. It does not replace their safety
logic, add a service, switch power, expose a network listener, or support off-LAN
control. The qualified camera limitation remains: a prior accepted combined run
had a maximum browser display gap of `1.138 s`, above the unchanged `500 ms`
continuity target. Loss of a required view still means release controls, press
`Q`, and restart only after all owned processes have stopped.

## Compact Control presentation — focused follow-up, October 4, 2026

### Separate Windows pin and deliberate retry after verified shutdown

The private session config can set `windows_session_head` to the exact reviewed
Windows commit independently of `remote_session_head` (the Pi helper commit).
If omitted, legacy configs still require Windows to match `remote_session_head`.
Both remain exact SHA checks; do not disable preflight or deploy unrelated code
over the motor/camera owners to make the versions identical. Back up private
configuration before changing a source pin. Summaries report the actual Windows
Git head, not the Pi helper's configured head.

A failed attempt stays failed, with its original reason/exit and saved session
records intact. A later **deliberate Start** is allowed only after the worker and
console pipe have closed, and either cleanup was explicitly verified or local
preflight explicitly refused before session resources/remote dispatch. A missing
session ID by itself is not evidence that nothing started. Unverified/failed
cleanup continues to block restart; it cannot be cleared by pressing Stop again.
The page distinguishes “No hardware was started” from “Cleanup verified” and
tells the operator to correct the cause before selecting Start. There is no
automatic retry, success relabeling or change to current Resume/gate policy.
Every new Start repeats normal exact-source, single-owner and hardware preflight.

`codex/am1-control-ui-polish` follows integration `3645646701d721b3f1b60075c7f5e0d834997deb`;
PR #12 remains closed. This changes presentation and bounded read-only progress,
not the accepted operating policy. The dedicated DirectBrowser viewport was
measured at **767 x 786 CSS pixels** snapped to half the owner's display, and
**1536 x 794** maximized, both at device-pixel ratio 1.25 and visual scale 1.
Synthetic-image before/after captures stay outside Git; no household images or
private logs are published. The collapsed Control view fits all five views,
status, duration/session toolbar and readable movement buttons at that half size.
Smaller/accessibility-zoom windows may scroll; Stop remains accessible.

One enlarged view plus four previews preserves roles, promotion, rotations,
bounded retained images, generation/sequence and actual advancing receipt age.
The top camera summary counts the visible primary against its existing 500 ms
limit and previews against their existing 1500 ms limit. A last-good frame is
not live. These are not physical scene-to-display latency measurements.

Hold/release keys and actions are unchanged: W/S/Z/X move, A/D rotate, U/J lift.
Hover or focus shows help; Escape dismisses it without moving focus or sending
a command. Touch Help is non-actuating. More holds exceptional ownership and
realignment actions. Start/approvals require Control; Stop is global and stays
available during pending operations. Ordinary release zeros body input; only a
full pause requires the current qualified Resume. The 1.5 s accepted-presence
and 250 ms body/native-pipe/gate deadlines are unchanged.

Startup is **Step N of 7**, never an elapsed-time readiness animation:

| Display | Actual source |
| --- | --- |
| Connections; cameras | Existing Windows session preflight/start transitions |
| Lift home; lift relief | The Pi helper's existing readiness-log reader, using the owning host's `[LIFT OPERATIONAL]` phase records |
| Leader preparation | Actual lift `operational_ready`, before the existing native client preparation |
| Arm synchronization | Frozen native sync plan and completed frame count; rate-limited, latest-only pipe telemetry |
| Final readiness/live | Successful measured alignment followed by the existing native live-admission acknowledgement |

Sync remaining time is explicitly **estimated** from the remaining planned send
intervals and FPS. Feedback holds/final measured completion show waiting instead
of inventing an ETA. A later operator-approved realignment reports its new plan.
Phase elapsed time is since the console received that phase, not exact actuator
start time; a short phase may be missed by the existing bounded log reader.

The live countdown uses the sender's actual native live-admission origin and
enforced deadline, not an earlier marker or browser Start. Windows native/server
Python processes share the system-wide monotonic clock; Pi/browser monotonic
origins are not subtracted. The HTTP response carries computed remaining time;
browser interpolation is display-only with a conservative request-latency age
margin. Timing older than 2 s is **stale**, missing timing is **unavailable**.
Refresh attaches to current state. Pause consumes duration and Resume never
resets it. Zero means time elapsed, awaiting the real terminal result; a stopped
timer is not verified cleanup. Fault reason/raw codes remain in Session details.

Only Windows console/client and the Pi session helper need this follow-up.
Preserve the separate motor `c3fc683d645ea1c355e69a72f705a8bbadde6a1a` and camera
`9b1f0670e7068f7d39eb50270a118e3807418355` owners. The accepted source identities
and physical results below remain historical evidence, not a new powered test.
Exact staged console/helper identity is `git rev-parse HEAD` in their checkouts
and the backed-up private `remote_session_head` pin; do not publish private config.
No new servo reader, polling thread, dependency or timing service is introduced.

The image-age and startup-message follow-ups are **Windows-only**. The Pi helper
stays at `8e6a0cf616cb2000df1d0e996ab27a19d2fb2fba`; the separate motor/camera pins
above remain unchanged. Their earlier software and physical evidence is retained,
not relabeled as a newly exercised startup. An observed focus release followed by
successful alignment and an unapproved live-start timeout is a genuine refusal,
not a motor/alignment failure. The originating focus event remains unknown.

Everyday entrypoint and Q/page Stop remain unchanged. Stop before switching
sources. Rollback uses the retained accepted console/helper branch at
`a77e97b09a2ae1defa9c440baadc9e58a3df3aa9` plus the matching private-pin backup;
switch clean stopped checkouts normally, without reset or changing motor/camera
pins. This follow-up is a separate draft PR; no merge is implied.

## Windows Control console — qualified ordinary-use closeout, October 4, 2026

The owner accepts ordinary supervised Local use. The remaining original Control
interactions are now accounted for across two distinct owner-operated sessions:

| Evidence | Established result |
| --- | --- |
| 50.6-second live session | Owner-operated page Start, right physical leader, all page movement buttons, release-to-stop and page Stop; no recovery or live command-watchdog event. |
| Separate 62.6-second live window | Left physical leader, deliberate page Pause, current-gate qualified manual Resume and normal Stop. The window includes a 9.813-second operator pause, not uninterrupted motion. |
| Earlier 90.2-second no-input session | Startup, duration expiry and cleanup passed; this remains separate from physical-input acceptance. |

Both owner-stopped sessions retain raw client/session cancellation **130**, with
host/camera exits **0**, verified zero/torque-off/stopped cleanup and complete log
collection. They are correctly classified **Stopped by operator**, not failed
sessions or all-zero component exits. Sent commands alone are not physical
observations. The historical failures below remain genuine failures.

### Intentionally distinct working deployments

| Component | Exact accepted source |
| --- | --- |
| Windows console/client and Pi session helper | `a77e97b09a2ae1defa9c440baadc9e58a3df3aa9` |
| Pi motor owner | `c3fc683d645ea1c355e69a72f705a8bbadde6a1a` |
| Pi camera owner | `9b1f0670e7068f7d39eb50270a118e3807418355` |

Reviewed combined runtime source: `d3358f6a00a1fe6a9735706e75727cccc6c46a15`.
Its relevant helper/client, motor and camera-UI blobs match the working
components. The motor timing-evidence patch was separately extracted onto the
accepted motor lineage; it is not a demonstrated cure for the historical sample
gap. PR #12 integrates into **`integrate/am1-local-teleop`**, never `main`.
Documentation/integration commits do not change deployed checkouts or private
pins. Preserve their environments, mappings, rotations, calibration and Direct
Browser profile; do not deploy the whole console PR over the motor/camera owners.

### Scope and verification qualifications

Camera presentation retains correct role/generation identity, bounded last-good
frames and honest advancing age. Servos/System/Logs/Terminal use existing sourced
data and bounded output. Their captured-data and offline checks plus retained
post-stop inspection are accepted for this closeout; a fresh live visual tour of
every supporting page is **not** claimed. Missing servo fields remain **Not
sampled**; no second reader is justified to populate them.

The browser currently starts physical-leader mode only. A browser Scripted-mode
selector and visible speed indicator/control from the original presentation
design remain nonblocking omissions, not delivered features. Scripted ArmSmoke
remains available through the existing CLI; T/G keyboard speed handling remains.
After a console Pause, later gaps can conservatively require manual Resume.
Ordinary button/key release zeros body input without requiring Resume; only a
full pause needs the displayed qualified approval. The page's broader release
help wording must not be read as changing this actual policy.

Final review found no Critical/Important runtime issue. Historical verification
retains its actual provenance: 151 Python/29 Node UI checks at `97582303`, 59
focused checks at `a77e97b0`, 215 affected checks at `d3358f6a`, and 10 focused
motor-extraction checks at `c3fc683d`. Documentation-only closeout does not relabel
those as new test runs or physical tests. Source equivalence, clean ownership,
private-pin consistency and diff/artifact checks were verified hardware-free.

This is qualified supervised LAN hobby-use readiness, not full presentation-spec
compliance, unattended/remote readiness, arbitrary-pose convergence or endurance
acceptance. Keep the historical 1.138-second display gap, imperfect small-command
elbow tracking, camera acquisition/browser limitations and unresolved historical
host-gap causation. No new powered commissioning is required solely for closeout.
All earlier pending/draft instructions below describe their historical stage;
this current disposition supersedes them without erasing the evidence.

### Everyday console reference

From the reviewed Windows checkout in PowerShell 7, with the existing private
`config/am1.session.json` and configured Python environment:

```powershell
.\tools\run_am1_console.ps1 -DirectBrowser
```

This opens the dedicated direct-routing Edge window at `http://127.0.0.1:8765/`.
Opening or refreshing the browser does
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
W/S/Z/X/A/D for base and U/J for lift; release zeros body input. Body commands
expire after 250 ms of accepted-browser silence, independently of the approved
1.5-second browser-presence allowance. A short gap clears held movement: release
the controls, then deliberately press again; it does not by itself pause the arms.
Leaving Control, losing focus, failed input/state requests or lost presence still
clear body input and request measured-arm pause. Resume and exceptional realignment require explicit on-page approval
and fresh host/follower/leader qualification. Q on Control or Stop from any page
requests the existing exact-session cleanup. Do not treat a returned Stop request
as verified shutdown: wait for the final session result. The exact result folder
is under the private configured `windows_log_directory`, named
`am1-session-<session-id>`; CollectOnly can retry missing log collection without
starting hardware.

If a startup input lease was released, the pending `sync_start` or `live_start`
gate shows **Continue startup**. Its approval instruction and recorded input-pause
reason remain visible above the toolbar, outside collapsed Session details; they
replace the measured-completion message with **Waiting for your confirmation**
while the gate is pending. Instruction, button, accessible name and help all use
**Continue startup** for startup, **Resume** for live recovery, and the existing
**Approve realignment** action for its separate gate. A current unknown/unreported
reason stays unknown/unreported; first-pause history is not a substitute. Before
a gate exists, the actual startup phase/estimate remains visible and does not
claim that all motion stopped. Faults, stopping and terminal results take precedence;
completed gates and new sessions clear old instructions. Technical gate/epoch evidence stays
in Session details. This display-only correction changes no gate deadline,
input lease or automatic-admission policy and requires only the Windows console
source, not a Pi helper/motor/camera update. Hold the leaders still, release all
body input, and click it to approve only that displayed gate with a fresh empty lease.
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
and long-duration or unattended use retain the limitations below.

### Historical console development and acceptance evidence

The following records preserve the pending state at each earlier stage, not a
requirement to repeat completed interactions after the qualified closeout above.
The console source had passed offline fake/browser checks. The camera-only layout/start/stop
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

The subsequent Control-only attempt also passed automatic synchronization and
native live admission, but the page continued to display `host_ready`. A later
input-lease expiry requested explicit Resume in the native client; Control did
not expose the pending gate before its deadline. The owner only watched startup
and reported prompt stopping without unusual behavior. Client refusal 2 and
verified host/camera cleanup 0 are retained; manual leader/body input, successful
Pause/Resume and page Stop remain unaccepted. Healthy SSH in that attempt does
not explain the local lease expiry or the missing gate.

The focused correction derives Control's `live` phase from the native client's
validated same-host/epoch admission plus matching accepted host feedback, not
raw active feedback, action transmission or UI approval. Ready/paused feedback
revokes the prior live display. An aged active sample is
displayed as `feedback_stale`, and cannot overwrite stopping/terminal state.
The native pipe retains one bounded gate-request record and one sent-ack record;
native gate logs separately show request and acknowledged/cancelled/disconnected/
timeout result. A sent acknowledgement is not itself host admission. Control
also labels native connection/rejected-request state. Complete synthetic native
telemetry exercised the real browser/HTTP/pipe/model path and explicit Resume;
it did not reproduce the physical attempt's absent gate. Its cause remains open,
not repaired by assertion. Keep the unchanged 250 ms input expiry, current-gate
approval and host qualification; use Stop promptly if the current gate is absent
or cannot be completed. No further powered attempt was made in this repair batch.

The next attended Control-only attempt did show validated live admission after
automatic alignment. The owner did not perform manual movements before another
accepted-input expiry. The current Resume gate was visible, but its required
empty-lease request was refused; no Resume reached the native client before its
deadline. Page Stop was then accepted during cleanup. Client refusal 2 and
verified host/camera exits 0 are retained, not a manual-control pass.

A controlled offline request-order test reproduced a separate approval race:
periodic body requests could overtake the explicit empty approval packet, making
its sequence stale. The frontend now reserves input-request scheduling for that
pending packet and ignores new held input during it. Release/Stop bypasses and
invalidates the pending approval. Owner/epoch and release-generation checks also
cover the later operation acknowledgement: a delayed reply cannot undo focus or
navigation release, or revoke a replacement owner's input. Held input is not
replayed after approval. These async races were reproduced and corrected offline
through the actual frontend and existing browser/HTTP/native-pipe harness.
No expiry is extended, no current gate is bypassed, and no automatic rearm is
added. This reproduction does not establish the powered attempt's exact HTTP
ordering or explain the initiating input gap. Manual leader/body response,
release stopping and successful explicit Pause/Resume still require the focused
attended Control check; the console remains a draft candidate.

The recovered owner-operated session used client/session helper
`2e3d3e694db220efa389279a3ee9244cf8e23bbe`, unchanged motor
`43d1622a9395cdc1d1f9acce1090ed3f029f4f7c` and camera
`9b1f0670e7068f7d39eb50270a118e3807418355`. Home, relief and automatic
alignment completed. Three input-lease pauses were explicitly recovered, followed
by approximately 54 seconds without another pause. Sending stayed near 10 Hz,
with no new live host watchdog event. The owner reported mostly keyboard use.
The explicit cancellation retained client exit 130; host/camera exits were 0
and cleanup was verified. This is not another missed-Resume refusal. Both-leader
response, page-button hold/release, deliberate page Pause/Resume and live
supporting-page acceptance remain unconfirmed; retained snapshots do not fill
those gaps. Earlier failed attempts and the initiating input-expiry uncertainty
remain part of the record.

The normal Stop follow-up retains raw cancellation codes and classifies only a
verified, nonfault explicit cancellation as **Stopped by operator**. **Stopping**
remains visible until remote/client cleanup and the private input-pipe owner have
finished. Faults, forced/uncertain cleanup and pipe-close errors still block Start.
A verified Stop permits a new deliberate Start through normal preflight; it never
automatically restarts or rearms. A verified cancellation before remote dispatch
is also distinguished from failure. These behaviors and cleanup-order/stale-event
races are covered offline through the actual coordinator, adapter and local lock.
No motor, camera, input-expiry or recovery limit changes accompany this repair.

The next attended attempt at `eec5d160` again passed automatic startup but
expired input before the owner could use the remaining controls. One current-gate
recovery succeeded; a subsequent Resume deadline expired before Stop arrived.
Client refusal 2, wrapper 1 and verified host/camera cleanup 0 remain intact.
Repeated input expiry is a usability blocker, not a missing-observation pass.
Do not conduct another unchanged powered check to seek a successful result.

The hardware-free desktop timing investigation reproduced that failure class in
the visible in-app browser using the actual frontend/HTTP/Windows-pipe path,
fake robot feedback and synthetic images. One 822 ms browser long task prevented
input callbacks: sequence 314 to 315 had a 920.4 ms fetch-submission gap and a
937 ms HTTP-arrival gap. The service correctly latched expiry/hold at 265 ms
accepted-input age. The prior
request completed normally and no approval was pending. Browser and Python
monotonic intervals were measured separately. The blocking task's initiator,
its relationship to historical powered attempts, and any HTTP-pool effect are
not established. This first capture used 61,756-byte synthetic headroom frames;
a later 69-second in-app monitoring/Pause/Resume/Stop comparison at that same
load had no unexpected expiry. It did not cover every nominal interaction.

A separate visible Edge nominal exercise with 36,953-byte 640x480 synthetic
frames at 15 fps and filled telemetry completed 65.7 seconds, W/U/J movement and
release through the fake native consumer, deliberate Pause/Resume, Stop with
verified cleanup and a fresh deliberate Start without rescue approvals. Full
camera/diagnostic UI remained enabled. Continuous HTTP-arrival gaps stayed at or below
125 ms and timed diagnostic rendering peaked at 9.6 ms; those measurements do
not attribute the intermittent earlier stall to hidden rendering. This is a
nominal offline pass, not a production correction or physical acceptance.
The existing fault-handling suite remains separate from this no-rescue check.

Final verification after the test-only evidence labels/assertions were completed
ran the entire affected local-path file: 14 passed, 1 opt-in in-app capture
skipped in 140.44 seconds. Its 65.65-second visible Edge nominal exercise had
no unexpected expiry, at most 125 ms continuous HTTP-arrival spacing and 9 ms
maximum measured diagnostic-render work. The 25 existing Node UI checks,
Python compilation, timing-helper syntax and diff checks also passed. The
earlier in-app refusal and failed capture result are retained; they are not
reclassified as a successful run or a production RED/GREEN correction.

The capped probe and foreground exercise live only under `tests/`; they are
opt-in (`AM1_TIMING_FOREGROUND=1` for the existing desktop Playwright runner,
`AM1_TIMING_EXTERNAL_BROWSER=1` for an attended in-app capture). They never use
real camera content or hardware configuration and require no installation.
Capture a first unexpected expiry honestly; do not rescue it into a nominal
pass. Raw timing artifacts stay ignored and outside published evidence. No
runtime limit, browser-presence policy or deployed source pin changed in that
test-only investigation. The separately approved policy follow-up is below;
the earlier timing evidence and failed attempts remain historical evidence.

### Historical input-presence follow-up (offline verification before acceptance)

The owner subsequently approved separating session presence from body movement:
actual accepted browser receipt permits presence for at most 1.5 seconds, while
body movement, native-pipe delivery and gate permission retain 250 ms deadlines.
This explicitly changes the undelivered browser-loss-to-arm-hold allowance from
250 ms to 1.5 seconds. Delivered blur/hidden/navigation/Pause and request failures
still request full pause immediately; full pause remains latched until explicit
current-gate approval and qualified host admission. No server-generated heartbeat
or pipe forwarding renews the original browser receipt.

Short callback/request gaps clear keyboard and pointer movement and show a bounded
nonterminal notice. A held key, autorepeat or pointer cannot replay after expiry;
release and a new press are required. Gates keep their actual fresh receipt and
owner/host epoch. A captured old lease/ack cannot be relabeled with a replacement
owner; ownership change cancels the old gate waiter. Fresh receipt metadata is
installed before acknowledgement, and malformed metadata fails closed without
hiding the first pause or turning a refused session into operator-stop success.
Normal prepared startup still auto-advances with fresh input and retains its
post-host-admission body-release requirement; no new startup choreography is added.

Red-green checks reproduced the old short-gap full pause, held-input replay,
expired gate metadata and owner-transfer permission, plus a Resume handoff that
could immediately re-latch. They assert real outgoing input, body zero and explicit
recovery through the frontend/HTTP/Windows pipe, not only a passing return value.
The full-page foreground nominal exercise remains separate from deliberate
input-loss tests and cannot use rescue approvals to conceal unexpected starvation.
All robot IO and imagery in these checks are synthetic. This follow-up affects
only helper/client input behavior; deployed motor `43d1622a` and camera `9b1f0670`
remain unchanged. It does not establish the 822 ms task's initiator, hidden-rendering
or connection-pool causation, nor physical manual-control acceptance.

Final affected verification: 151 Python checks passed (two opt-in foreground modes
deselected), and 29 Node UI checks passed. The separate visible Edge nominal
exercise completed 65.78 seconds without unexpected expiry or rescue approval:
synthetic W/U/J and release, deliberate Pause/current Resume, Stop/cleanup and a
fresh Start; full camera/telemetry UI stayed enabled. HTTP-arrival spacing peaked
at 125 ms, browser submission spacing at 115.1 ms, and measured snapshot work at
7.7 ms. The alternate in-app capture mode was skipped, not retested. An earlier
combined run had four overlong Windows test-log-path failures; the short fresh
temporary-path rerun passed without changing production code. Compilation,
module help, fresh lazy imports, JavaScript/PowerShell syntax, diff checks and
independent read-only review also passed. Prior historical tests are not relabeled.

After staging, the remaining attended Control check is both leaders, page wheel
and lift hold/release, deliberate Pause/current Resume, then page Stop with actual
cleanup. Stay on Control and handle its current Resume/Stop gate before analysis.
Do not repeat an unchanged powered session just to seek a pass.

### Process/startup/output comparison and loopback routing (October 3, 2026)

These are motor-free checks, not physical Control acceptance. The earlier
65.584-second nominal pass and shorter ordinary Edge Profile 1 observation remain
separate historical results. The old external harness sent Stop after its deadline;
it did not demonstrate a timely production Stop failure.

The same visible Edge driver, five synthetic 640x480 / 15 fps camera views,
frontend/HTTP/native pipe and unchanged policy were exercised incrementally:

| Added condition | Actual result |
| --- | --- |
| A: separately owned PS7/Python native consumer | Passed in 73.03 s total; no unexpected expiry, verified native cleanup. |
| B: actual `run_startup_sync`, nominal 30 s ramp | Passed in 147.08 s total; two 301-frame ramps, zero body, no unexpected expiry. |
| C: measured-size/rate synthetic supervisor output through the actual forwarder/reader | Failed on accepted-browser expiry; retained as a failed nominal result, not rescued. |
| C plus browser-process direct routing | Passed in 150.78 s total, 65.256 s first live; both ramps, input/release, deliberate Pause/current Resume, Stop and cleanup passed. |

Output was approximately 42 KB/s host and 638 B/s camera with bounded bursts,
advancing file offsets and the ordinary 1536-byte forwarding cap. Skipped output
bytes remain explicit; synthetic replay does not claim to reproduce SSH transport
or motor IO. Test-only native children retain actual launch provenance (including
Windows executable/venv redirectors), original failures and bounded owned cleanup.

At the first useful C divergence, browser callbacks continued while localhost
requests arrived late; native leases/consumption and output also continued.
A second failed C check completed its first live interval and deliberate Stop,
then expired during the second startup; that failed restart is retained separately.
Focused browser network evidence in a subsequent failed C check located a
**1.845 s `PROXY_RESOLUTION_SERVICE_WAITING_FOR_INIT_PAC`** wait on `/api/body`
before resolving to `DIRECT`. Socket-pool queuing followed that wait. This proves
the dispatch layer of that synthetic failure, not that output caused proxy
initialization or that all historical powered gaps shared this cause. The matched
direct-routing check recorded no PAC events, no expiry, at most 125 ms continuous
HTTP-arrival spacing, and 115.1 ms retained browser-submission spacing.

After the owned-process cleanup review, final affected console/process/replay
verification passed **59 checks** (16 browser/foreground cases deselected).
The separate final visible direct-routing C regression passed in **149.70 s**:
65.135 s first live, two paced 301-frame startups, no unexpected expiry or rescue,
172 ms maximum continuous HTTP-arrival spacing, and verified owned cleanup.
Earlier harness RED failures (missing child/startup coverage, repeated zero
offsets, launch failure, blocked replay reader and wrapper/early-child cleanup)
were corrected with focused tests. A wrong immediate-parent assumption exposed
Windows executable/venv redirectors and was replaced with actual launch provenance;
it was a fixture error, not a robot defect. Compilation, console help, fresh lazy
imports, Node/PowerShell syntax and diff checks passed. The earlier 151 Python / 29
browser results were not rerun or relabeled as new evidence.

An opt-in launcher mitigation is available:

```powershell
.\tools\run_am1_console.ps1 -DirectBrowser
```

This uses installed Microsoft Edge in a separate private `edge-console-direct`
profile under the configured local state directory, with process-local
`--no-proxy-server`. It does not change Windows proxy settings, ordinary Edge
Profile 1, authentication, any deadline, or the motor/camera sources. Missing Edge
refuses rather than silently falling back. The ordinary launch and `-NoBrowser`
remain available; do not open a second controlling tab. Browser launch itself
starts no robot. The distinct profile is necessary so an already-running ordinary
Edge process cannot silently ignore the requested process flags.

The remaining justified real-use observation is whether the selected direct
console window can complete the still-missing brief leader/page-button/release,
Pause/current Resume and Stop interactions. No powered check was run for this
packet. A later accepted result must retain any first expiry and actual cleanup;
this offline pass alone does not complete manual Control acceptance. Private raw
timing/NetLog output stays outside Git (NetLog can include request credentials).

### Owning-host sampling boundary (October 3, 2026)

The subsequent attended dedicated-window attempt used helper/client `a77e97b0`,
motor `43d1622a` and camera `9b1f0670`. Home, approximately 10.397 mm relief,
automatic 301-frame alignment and live admission passed. The first terminating
condition was the lift's unchanged five-slot freshness guard: a 304 ms raw sample
gap left 671 ms of retained history. The rejected reading was 30 C; its grouped
request completed in approximately 1.1 ms. This is not confirmed overheating or
a demonstrated browser-presence/network failure. No manual inputs were used, so
the remaining Control interactions are still unaccepted. The owner reported
prompt stopping with nothing unusual.

Saved lift cleanup later qualified zero goal, torque off and stopped feedback;
camera exit 0 and absence of owned runtimes were verified. Original host exit 1,
client 130, session 2 and the conservative `cleanup_unknown` summary remain.
Immediate cleanup feedback was not uniformly zero; later stationary qualification
does not erase the original refusal or prove those earlier readings' cause.

Exact deployed-policy replay reproduced the refusal. A separate fake-host model
with a synthetic logging delay also reproduced it and zero/off/close cleanup;
that model does not establish which operation caused the real gap. The preceding
log timestamp narrows the unmeasured interval, but does not distinguish log-write
blocking, sleep/scheduling delay or other work. No guard change is justified by
these records.

The focused follow-up retains only the current and preceding AM1 operational
host-loop timings in memory. On a genuine fault it snapshots the active phase
before cleanup, then attaches the context to the original exception after motor
and socket cleanup. Phases include grouped poll, command, watchdog, observation,
response, sample emission, diagnostics, sleep and reporting; an inter-loop gap is
also labeled. Durations use `perf_counter` and include scheduling time, not just
device/CPU work. Cached lift timestamps use their original monotonic clock and
are not labeled accepted: some operational guards can reject after caching.
Routine output, raw evidence and refusal behavior are unchanged. There
is no extra servo read, sampling thread, retry, limit change or automatic restart.
Context construction/encoding failure cannot replace the primary fault or its
cleanup notes. AM2/AM2 Pro and skip-home paths do not use this context.

Offline validation of the follow-up: 215 affected operational/consumer-refresh/
local-recovery tests passed; changed Python compilation, host help, fresh
worktree-root import/lazy-visualization checks and diff checks passed. The new
fake-host timing, exception and provenance regressions were verified RED/GREEN;
synthetic delays are not a reconstruction of the physical cause.

This is an evidence correction, not a demonstrated cure for the 304 ms gap or
a new powered pass. Deployed components/pins remain unchanged pending review of
the prepared motor follow-up. Keep PR #12 draft/unmerged and raw logs private;
do not conduct an unchanged powered retry to seek a manual-control pass.

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

## Historical teleoperation closeout — September 29, 2026

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
