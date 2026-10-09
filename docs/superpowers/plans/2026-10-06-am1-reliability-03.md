# AM1 virtual continuity and useful motion implementation plan

## Current direction: AM1-SESSION-ARCHITECTURE-01

The owner replaced the proposed connection-continuity handoff and canceled the
Roaming Aggressiveness experiment and administrator question. Do not execute
that adapter transaction or request its elevation. The current deliverable is
the [consolidated persistent-session design](../../alohamini/roaming-resilient-sessions.md),
source inspection, migration and fake-only slice brief for review before
implementation. Reliability milestones below remain open; preserve their
repairs, evidence and separate private deployment pins.

> Execute this short plan in the existing PR #16 worktree using the debugging, TDD, execution and verification workflows. The supplied packet authorizes proceeding through repair, bounded execution and publication without another design or attendance gate.

**Goal:** Complete useful four-cycle virtual operation with honest current observation, bounded qualified recovery and measured motion.
**Architecture:** Keep native local motor limits and existing scripted provider/seed/deadline. Opt in to a structured observation policy: inspected P1 coverage is required for arms/lift; each body check additionally declares current scene coverage. Optional AM1 thumbnails report quality and reconnect locally without granting authority. Required coverage loss uses a distinct bench-owned hold, never an operator pause cleared blindly.
**Spec:** Owner-supplied AM1-RELIABILITY-03 and normal-rest continuation AM1-RELIABILITY-03A; latest startup review on PR #16 (2026-10-08).
**Context:** Existing codex/am1-reliability-02 at be322860; PowerShell 7, existing uv environment and Node/Playwright/Edge. Preserve separate component pins and private rollback.

## Constraints and interfaces

- One native 420-second ceiling, four original 88-second cycles (352 trajectory seconds), no new home/sync/reseed/catch-up or wider tolerances.
- Allowlisted recoverable observation/feedback episodes: at most 10 seconds each and 3 per run; the absolute deadline never resets. Explicit Pause/Stop, faults, expired body input and ownership changes do not recover automatically.
- Camera API: AM1CameraHealth() supplies role identity, numeric age, original sequence/current and decoded generation, source/status uncertainty; AM1CameraReconnect(role) is role-local, bounded, and cannot freshen a cached image.
- P1 observer: one exact spare-camera MediaCapture owner in the proven mode, finite recording/storage; source-timestamp-bound frames with a nonce/round-trip delivery qualification, original generation/sequence and locally measured age. No cross-machine monotonic subtraction or mtime freshness.
- No manual-leader/attendance/power-cycle prerequisite, cutoff development, AM2, docking, unrelated services, new environment or broad network remedy.
- Raw logs, images, host/device/access metadata and configurations stay private. Earlier cleanup_unknown and partial-run verdicts remain unchanged.

## Execute

- [x] 1. Verify actual PR review/source pins, stopped ownership, existing access; update scoped project instructions and current runbook. Keep one active plan.
- [x] 2. Reproduce transient status failure/shared-role coupling and optional/required view loss in tools/am1_camera/app.js, freshness.js, tests/cameras/test_am1_camera_ui.cjs and the existing real HTTP/native-pipe harness. Implement the narrow shared repair and numeric health API; test honest expiry, decode/generation failure and bounded reconnect.
- [x] 3. Implement opt-in observation/recovery across tools/am1_reliability_bench.cjs, tools/am1_console.py, native bridge/controller and teleoperate_bi.py as required. Test exact session/owner/gate, fresh coverage/feedback/pose, actual outgoing hold/release/resume commands, no catch-up, explicit-stop exclusion and fixed budgets/deadline under representative camera/output workload.
- [x] 4. Implement tools/am1_observer_capture.ps1 and a narrow local receiver with meaningful fake protocol tests. Qualify one short no-motor capture/recording through the same owner, retrieve and inspect current framing, then run continuous finite capture before every useful powered trial. Save boundary/interruption snapshots and short private event clips.
- [x] 5. Reconcile normal-rest coordinate/calibration provenance; implement the smallest demonstrated selected correction or justified pre-live acquisition, with a positive real-startup-path fake before powered use and I0 baseline. Trace shoulder generated/processed/raw goals against feedback/current. If essential, opt in to sparse selected-register sampling through src/lerobot/robots/alohamini/alohamini.py and alohamini_host.py with focused tests. Use a modest ordinary diagnostic to prove the first failing layer, then repair only the demonstrated cause; do not return after instrumentation alone.
- [ ] 6. Focused review/checks, commit and stage only affected stopped components with exact pins/permissions/rollback. Complete four cycles with current observation; inspect actual outward/return samples and images, repair failures and retest the affected hypothesis. Run one comparable final confirmation after normal Stop and natural rest, without owner repositioning, and a short owned body check only where changed behavior requires it.
- [ ] 7. Preserve evidence taxonomy, publish concise PR #16/runbook results and exact deployment/rollback. Only after full completion and final review, merge ordinarily into integrate/am1-local-teleop, verify parents/tree/remote/PR disposition, leave main unchanged.

## Review focus

Stale or same-sequence frames never qualify; transport reconnect cannot erase old identity. Optional loss cannot stop qualified motion. Required loss cannot advance trajectory before full requalification. User stops/real faults/foreign owners remain terminal. Four envelope boundaries alone cannot prove useful repeated physical return.

## Active actual qualification status

Normal-rest automatic startup is demonstrated after the selected calibration correction.
The I0 baseline did not show useful selected-joint return; the reversible I1 comparison
showed a delayed small outward/return movement with restoration, while its tighter
immediate-return criterion failed. The first full repeat completed two cycles and two
boundaries before the fourth required-observation gap exhausted three permitted recoveries.
Ordinary Stop and cleanup succeeded; a later log-finalization timeout is secondary.
Bench first-cause/finalization reporting was corrected. A smaller live-JPEG payload
comparison had no transport loss but stopped after two cycles when current coverage
expired between Resume preparation and forwarding, at 217.039/352 trajectory seconds.
The same-episode held-request correction passed focused real browser/native-pipe tests
and was staged. The next attempt stopped before Live on repeated lift velocity/position
direction disagreement, so it did not exercise Resume or the trajectory. Original
cleanup_unknown is retained; a separate exclusive read-only check verified all sixteen
motors off, zero body goals, unchanged corrected calibration and selected gain I0.
The conservative post-home quiet-feedback transition passed 341 focused cases and was
staged within the existing settling budget. The next observer capture stopped before
startup admission, leaving the lift hypothesis unexercised on that attempt. Its first fault
was a delivery stall; later synchronous cleanup lookups blocked the live source FIFO.
The narrow ordered-EOF/Stop lookup correction passed 217 focused observer cases and
independent regression checks. It repairs the demonstrated secondary blocking while
preserving original ages, identities and all recovery limits; it does not establish
the initial transport stall's cause or a physical lift cure.
The next automatic normal-rest start reached Live: one moving post-home sample was
withheld before five quiet samples qualified over 0.208 seconds within the unchanged
deadline. Relief direction then qualified under the original guard. The workload reached
326.754/352 trajectory seconds, three cycles and three envelope boundaries after 3.218
seconds of preparation and 338.913 seconds in Live. Three observation episodes recovered
through the staged held-Resume correction without owner input; the fourth exhausted the
unchanged budget and caused ordinary Stop with cleanup verified. Original source cadence
continued across all four shared receipt delays; no retained Wi-Fi security event overlaps
the powered interval. The transport cause remains unresolved. Selected-joint later return
does not establish the tighter immediate-return criterion or useful return of every joint.

A further receiver correction preserves partial newline-framed records through an idle
read without retiring a still-owned connection. Complete coalesced records retain their
original receipt time; an unfinished EOF record never grants authority. Real EOF/reset
and operating-system transport errors retain bounded forwarding recovery. The first idle
anchors the original five-second deadline; stale, replayed, foreign or late-processed
frames cannot renew it. The post-decode deadline check can unqualify a briefly published
actually fresh frame; no lifecycle renewal is inferred from that publication. The
233 affected observer checks passed with background-thread warnings treated as errors.
This repairs receiver framing/idle classification, not the initial shared IP delay.
An isolated comparison with additional outbound gateway activity still retained two
half-second delivery gaps and does not establish a mitigation. No adapter or network
permission setting was changed. All original failed/partial results remain unchanged.

A camera-only attempt with that correction lost delivery before robot Start. The
receiver sent Stop 5.012 seconds after the original idle indication; repeated idle
records belonged to cleanup, not a renewed recovery budget. The original failed
receiver verdict is retained. After connectivity returned, the same generation's
final artifact independently verified finite capture completion and camera release.
A local Wi-Fi security stop occurred 14 ms after the last decoded receipt, followed
by repeated reauthentication and failed connections to both known device hosts.
This identifies a connection event at this failure boundary, not its driver or
access-point mechanism or the cause of earlier gaps without matching events.
A one-property roaming comparison and exact rollback are being prepared;
no adapter, permission or motor setting has been changed by this investigation.

Full 352-second completion, natural-rest confirmation and conditional body check remain open.
