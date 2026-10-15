# AM1 live elbow selection - Packet10

The same browser-to-Pi physical input path implements a choice of left or right
elbow before Start. One selected joint receives live targets per run; the other
eleven arm commands remain held and body/lift commands stay zero after normal
startup. **Packet10 physical qualification remains pending in this draft.**
The first ordinary right-elbow Start faulted before Live: left shoulder-lift
raw3224 exceeded the unchanged command endpoint3223. No right jog occurred.
The original failed run, first cause and incomplete-startup cleanup result remain
unchanged. A separate fresh native proof covered all20 zero/off registers,
stationary lift feedback, thermal qualification and80 calibration comparisons.
Ordinary Reconcile consumed its durable acknowledgement within the existing
180-second freshness window. The authority uncertainty was then resolved, and
the exact matched runtime restored; this did not make the failed trial pass.

A subsequent read-only shoulder diagnostic found actual Goal_Position3224 and
Present_Position3224, with nine stationary, fault-free samples over0.416 seconds
and five thermal slots at35-36C. Calibration/EEPROM limits remain848..3223,
offset515, Phase12, P16/I0/D32 and directional dead zones1. The accepted Packet09
run had also recorded205 measured shoulder samples at3224. Source inspection
identified later measured holds copying that feedback into the raw goal register.
The implemented repair qualifies a representable goal3223
without widening limits, keeps measured3224 explicit, and preserves the eleven
acknowledged nonselected raw goals through every protected live hold. Both the
before-home and after-home checks require fresh stationary feedback and actual
settings/health; all twelve startup goals have actual register readbacks before
arm enable. Later selected-joint measured holds verify the eleven frozen goals
and change only the selected goal.

A subsequent new Start exposed a distinct post-home acquisition refusal. Its
original twenty samples and failure remain retained. The supported AM1-specific
correction retains the original home reference and one-second deadline, freezes
one first-observed quiet acquisition, and keeps whole-phase continuing-motion,
fault, freshness and operating guards. It does not change gains, calibration,
physical limits or the local home-distance protection. Exact native replay and
negative tests cover the new bounded branch; the next physical startup used the
existing strict-quiet branch, so that run is not physical validation of the
bounded fallback. These changes are at executable source
316a7b85ce4f900717fd988d989d4daa7c92e59b.

The seventh bounded Start stopped before Live on three consecutive lift Moving
flags. Its eighteen distinct acquisitions retained the original home812,
first quiet815, and subsequent814..815 band; the final velocities were
-50/-50/+50. The version3 correction aligns Moving-without-velocity
classification with the existing stationary and live checks. It preserves
same-sign velocity, actual continuing displacement, fixed first-quiet band,
original one-second deadline and all health protections. Replay of the captured
prefix does not fabricate the missing end of that window or make trial07 pass.
Version3 native and physical qualification remain pending in this draft.

[PR21](https://github.com/pickmanmike/lerobot_alohamini/pull/21) was ordinarily
merged into `integrate/am1-local-teleop` at
`48a3ed39b7591837ed6771a586daec7c1b8b5b8b`. Integration is separate from deployment.
The focused follow-up is `codex/am1-live-arms-10`, with executable checkpoint
`316a7b85ce4f900717fd988d989d4daa7c92e59b`. Documentation commits do not deploy
or change those executable bytes. The follow-up has no automatic merge authority; main and
the closed PR16-20 milestones remain outside this work.

## Ordinary use on the candidate page

When this physical candidate is served, the existing enrolled AM1 page's
**Session recipe** selector offers **Left elbow** (`physical-live-left-elbow`) and
**Right elbow** (`physical-live-right-elbow`).

1. Select **Claim input**.
2. Choose **Left elbow** or **Right elbow** before selecting **Start**.
3. Select **Resume** and wait for fresh measured hold, required local observation
   and native acknowledgement to qualify the selected run.
4. Hold the side-labelled negative or positive jog button, or **[ / ]**, for a
   new gesture. Positive and negative refer to normalized target direction.
5. After release, blur or an input gap, qualify **Resume** again and begin a new
   gesture. After browser reconnection, retrieve the current run, Claim input if
   needed, qualify Resume and begin fresh input; do not create another Start to
   recover a view.
6. Use **Pause** or **Stop** for the current run. A new joint choice becomes
   available after known terminal cleanup, for the next explicit Start.

Active or uncertain physical runs freeze the selector. Buttons, keys and target
display identify the admitted side; changing a stale page selection cannot
switch the active joint. Viewing and reconnecting do not start motion or
remember a held key. Pause and Stop retain precedence. Normal finite presets
and the simulation default keep their existing behavior.

## Fixed one-joint boundary

| Selection | Sole target key | Admission-relative command corridor |
|---|---|---|
| Left elbow | `arm_left_elbow_flex.pos` | q0-10 through q0 |
| Right elbow | `arm_right_elbow_flex.pos` | q0 through q0+3 |

Each q0 is that selected joint's fresh measured starting reference. Admission
freezes its corridor and original native Live deadline for the run.
Reacquisition refreshes the measured cursor without reseeding q0 or changing
the corridor. Earlier left raw positions are historical results, not absolute
targets for a new left or right Start. Actual AM1 model/calibration and device
limits determine encoding and allowed admission; identical normalized increments
do not establish equal raw or visible movement on the two sides.

Both allowlisted specifications retain rate at most **1 normalized unit/s**,
the existing **150 mA sampled selected-current guard**, and the original
**90-second native Live ceiling**. Current/feedback/thermal/status, startup and
moving guards, motor settings, calibration, ownership and private permissions
remain in place. The sampled cap is not a continuous current regulator.

The page samples current intent on its nominal **60 ms** live cadence, with
**one unresolved input** and no queued target backlog. A Pi-issued grant retains
its original **250 ms expiry** through the mailbox and every native write
boundary; later delivery cannot renew it. Cadence is nominal browser scheduling,
and input validity is not a guaranteed browser-to-motion or stopping latency.

Targets carry current run, service, host, controller, connection, revision,
grant and increasing sequence fences. The contract accepts only the selected
recipe's one named joint, with no supplied hardware address or arbitrary joint
selector. Expiry/release captures fresh measured hold and body zero; it does
not automatically return the selected joint to q0. Nonselected joints retain
acknowledged native raw command goals, with explicit command/feedback separation;
they do not copy out-of-command-bound feedback into later holds.

Simultaneous bimanual input, live body/lift, physical USB-leader streaming,
inverse kinematics and precision positioning remain outside this extension.
AM1 remains wireless, P1 advisory and AM2 untouched. Actual Pi-local images
remain required task observation; missing P1 media is not visual confirmation
and does not replace identified native feedback.

## Read movement and return evidence correctly

Record requested gesture/target, browser-submitted target, authority acceptance,
native accepted target, encoded goal and any goal-register readback separately
from measured position and imagery. Freshness, identity and timestamps matter;
missing or stale values remain labelled.

Current-target error is distance between measured position and the then-commanded
goal. Original-reference residual is distance from immutable q0. They answer
different questions, especially after release replaces a lagging requested goal
with fresh measured hold.

A return assessment requires a declared return target, proof that browser and
native command actually reached it, and a declared bounded response dwell while
that target remains commanded. An encoded returned write is distinct from servo
goal-register readback and measured movement. If the return goal was not reached
or its response window was truncated, return remains unproven; the remaining q0
offset does not establish inability to return or uncommanded drift.
Reconnect/release must not introduce automatic compensation, overshoot, catch-up
motion or rehoming.

## Accepted Packet09 result remains unchanged

The [Packet09 report](live-virtual-input-09.md) retains the actual left run at
operating source `751ba2dde82e32c54d177c3951ab0f1771b493d0`, its component pins,
matched rollbacks and cleanup.

| Segment | Measured raw positions | Outward / reverse / original-reference residual |
|---|---|---|
| First wave | 2183 -> 2100 -> 2158 | 83 / 58 / 25 ticks |
| Fresh input after expiry recovery | 2158 -> 2102 -> 2140 | 56 / 38 / 43 ticks |
| Fresh input after browser reconnect | 2140 -> 2124 | 16 outward / reversal unproven / 59 residual ticks |

Its **797 submitted/accepted browser targets are not 797 independently verified
servo outcomes**. Raw 2183, encoded 2072..2183 and the original 90-second deadline
were fixed for that particular run. One actual expiry event had measured-hold
writes completed 35.693 ms after expiry; this is not a general latency guarantee.
Qualified same-run reacquisition and fresh movement were demonstrated. Final 20
zero/off checks and cleanup were reported verified; P1 imagery was unavailable.

The third segment's 59 residual follows outward movement without demonstrated
reversal. It is not proof of 59 ticks of uncontrolled drift or progressive servo
failure. Historical fine2 and return22 criteria remain unqualified measurements,
not universal right-elbow specifications or gates to this extension.

## Verification and pending physical result

Focused cohorts retain their own versions and are not added together:

| Evidence | Result and scope |
|---|---|
| Final isolated native candidate checks | 683 passed, one Windows-DACL-only skip, 123.59 seconds |
| Final serial browser cohort | 100 passed, no failures/skips, 77.740 seconds |
| Earlier Core/adapter and Windows native checks | Separate overlapping records: Core cohort 293, subsequent focused checks 7; Windows native cohort 335, subsequent focused checks 12 |

The browser cohort uses loaded ordinary controls with injected physical protocol
replies, plus existing fake/simulated fixtures. It verifies both selections, side
labels, fixed active selection, wrong-key/metadata refusal, fresh qualification/
no old-key replay, cadence and existing finite behavior. It does not qualify
hardware motion. Native candidate checks use injected device IO and isolated
candidate imports; they are distinct from physical operation. Browser syntax
checks passed. Full-repository/pre-commit checks and main-only CI are not
represented as performed or green.

**Pending Packet10 physical record:** actual run and deployed motor/runtime/
manifest identities; requested outward/return targets and commanded dwell;
authority acceptance, identified native acceptance/write/readback and fresh
measured response; post-browser-reconnect comparison; fixed reference/deadline;
held-joint/body evidence; actual interruptions and qualified recovery; local/P1
imagery availability; terminal cleanup, rollback and available-idle verification.
No right-elbow outward/reverse or precision result is claimed until that record
is completed.

Root alone handles Git, SSH, hardware and matched staging. Stage only affected,
stopped owners after known cleanup, using the retained matched procedure.
Preserve historical evidence, separate helper/camera/P1/manual-console pins,
private trust/enrollment, calibration and matched rollback. Service startup
restores the interface without starting or replaying a task. Software durability
here means preserved state and control/recovery continuity, not mechanical
endurance.
