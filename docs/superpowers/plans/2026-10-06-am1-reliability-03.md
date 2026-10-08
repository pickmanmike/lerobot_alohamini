# AM1 virtual continuity and useful motion implementation plan

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
Test a conservative post-home quiet-feedback transition within the existing settling
budget; this is a specific hypothesis, not a demonstrated physical cause or cure.
Full 352-second completion, natural-rest confirmation and conditional body check remain open.
