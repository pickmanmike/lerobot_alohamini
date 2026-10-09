# AM1 Reliability 02 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. The owner expressly requires completion of available implementation, verification and publication before returning a physical task.

**Goal:** Integrate the demonstrated fixes and prepare bounded physical-input and continuous dynamic follow-ups without extending hardware privileges.

**Architecture:** Keep `ScriptedLeaderInput` and ordinary ArmSmoke intact. Add an opt-in `ArmSmokeRepeat` provider around four existing cycles; retain one original seed, normal Local startup, sender, freshness, recovery and cleanup. Every return requires three advancing, admitted observations over at least 0.2 seconds, each within 3 normalized units of that seed. No cycle triggers home, synchronization, recovery approval or a new session.

**Tech Stack:** Existing Python environment, PowerShell 7, installed Edge/Playwright and existing console/native bridge.

**Spec:** Owner-provided Packet AM1-RELIABILITY-02, attachment `0ed5acba-00af-4d91-9aa4-2c025c0d40da/Pasted text.txt`.

## Latest owner steering and evidence

The owner restored both leaders and the spare observer and directed bounded virtual controls without attendance requests or owner leader/key movements. Both leaders pass the non-actuating preflight at unchanged mappings/calibration. Two fresh six-second spare-camera clips were retrieved with matching hashes, all 56/55 frames decoded and four selected new images inspected. Imagery and access/device metadata stay private.

Keep the packet-02 results distinct from packet-01. Body-01 refused before Live because follower power was off (owner confirmed/restored); original cleanup_unknown is retained. Reviewed exclusive normal zero/off qualification freshly verified 20 registers and closed both ports without changing calibration or that summary. Body-02 then completed 12.086 seconds Live with four frontend pulses, exit 0 and verified cleanup.

Arm-01 stopped at 48.172/88 trajectory seconds on the unchanged lift temperature-history guard: 512.023 ms > 500 ms. No recovery completed; native 130 / host 1 / original session 2 / cleanup_unknown remain. A second reviewed zero/off proof preserved that summary. Acquisition-timestamp replay reproduces the first fault; its specific initiating owner-loop phase is unknown.

Host commit 81034618 retains eight completed loop timings plus current/omission count. RED/green regressions, 14 focused checks and zero-added lint were recorded. Only Pi motor was staged, c3fc683d→6d99b263, with file hashes, stopped owners, private-pin backup and unchanged permissions/calibration. Windows remains 7249eec3, helper 8e6a0cf6 and camera 9b1f0670.

Arm-02 paused on state-request-failed before motor host/native client/Live; owned Stop 130 and cleanup verified. HTTP versus JSON/UI-processing cause is unknown. Passive bench commit bca3a982 adds frontend HTTP/failure/page-error evidence; 503/reset RED then five affected checks passed. Arm-03 stopped at 2.105 trajectory seconds / 6.135 seconds Live on a required-camera timeout, cleanup verified. State HTTP 200 responses had no request failure/page error; Pi acquisition remained fresh.

An exclusive camera-only 60-second run exited 0 with no motor access. A 35-second read-only browser capture retained 24 DOM samples, all five fresh, no status/decode failures/cancellations. Bench 1a8ac290 adds bounded per-role age/sequence/source evidence in the existing timed read. Camera-loss RED then four affected checks passed; the exact readiness-timeout regression passed after RED. Failure snapshots never delay Stop; post-cleanup finalization is bounded. Independent review is clear.

Arm-04 completed the commanded 88 seconds / 88.803 seconds Live, 883 sends, all runtime exits 0 and cleanup verified. Three empty polls were preserved; no feedback recovery/stale latch. Its one freeze is normal terminal finally. Maximum endpoint error was 2.6896 normalized units; left shoulder-lift reached +2.9975 but did not follow its small return. Other joints partly returned.

Independent replay confirms the repeat boundary is an envelope check: its 3-unit tolerance equals the excursion and can admit arm-04's 2.9975-unit residual using three advancing synthetic samples. Original-seed retention prevents cumulative rebasing; this does not qualify precise physical return.

One deliberate ArmSmokeRepeat attempt, arm-05, reached 51.009/352 trajectory seconds / 54.096 seconds Live with 538 sends. Chest/left-wrist decoded views aged past 1500 ms while Pi sources stayed fresh; owned Stop, session/native 130, verified cleanup, zero feedback freezes/recovery/stale latch. No cycle/boundary completed. Lift remained 32–35 C, at most 39 mA, status zero. Left shoulder-lift residual was +3.3306 normalized units; no boundary was reached. Full continuous workload, moving physical-leader input and precise small return remain unqualified.

A corrected exclusive camera-only 120-second diagnostic exited 0, with clean pins and no remaining owner. Its 105-second read-only browser capture recorded 105 samples, all five views fresh, 840 snapshot/366 status/213 state HTTP 200 responses, no failed request or dropped record, and zero decode failures/cancellations. Maximum snapshot duration was 802.615 ms, mainly waiting for response bytes after local connection/request start; status maximum was 447.513 ms. Network versus proxy/Pi waiting remains unresolved, and this is not a matched powered repair. Earlier setup attempts are retained: SSH TCP timeout before launch and an over-limit 180-second request refused before capture by the installed 120-second guard. That guard was preserved.

## Global Constraints

- Ordinary ArmSmoke remains one 88-second trajectory with unchanged amplitudes/rates.
- Repeat is explicitly selected, fixed at four cycles (352 trajectory seconds), with a 420-second native ceiling. No idle padding or reinitialization is endurance evidence.
- Physical-input follow-up uses one ordinary 180-second session, both physical leaders and brief owned body/lift press/release controls; actual physical movements remain untested under the latest virtual-control direction.
- Keep separate component pins, private configurations, calibration, PnP identities, supplies, environments, DirectBrowser and AM2 intact.
- Latest owner steering removes attendance requests for the authorized ordinary virtual checks. Independent cutoff remains unqualified; no process freeze, bus fault, overload, unrestricted recovery or automatic restart.
- Reuse verified prior results for unchanged integration code. New verification covers changed execution paths, not unrelated broad suites.

## Review Focus

- Repetition accidentally dispatches the ordinary single-cycle provider: test real native entrypoint completion and its four measured cycles.
- A boundary rebases to measured drift or jumps targets: test identical original segment plans, continuous targets and return refusal.
- Retained/old feedback qualifies a new cycle: test duplicate sequence, freshness refusal in the real sender and reset across freeze.
- Cancellation/completion race starts another cycle: test stop during boundary and terminal provider behavior.
- A partial cycle or mismatched profile summary is accepted as completion: test profile-specific native summary validation and dispatch.

### Task 1: Integrate reviewed candidates

- [x] Read current PR review, exact heads and checks; focused independent review clear.
- [x] Merge #14 ordinarily: `7da4cb2ab249f4a83e87edd97fb19e9f2bebc62b`, parents `8b5f8963`, `915a32d4`, tree equals #14 head.
- [x] Retarget #15, inspect the actual ten-file incremental diff, merge ordinarily: `9aa6d3b042a92e538213b0c684fd52d000b9cc7c`, parents `7da4cb2a`, `39647399`, tree equals #15 head.
- [x] Verify both PRs closed/merged, remote integration matches, main remains `ab4462b713aeb24d0473f1ec6c8812290ab19510`; fast-forward integration checkout.
- [x] Verify deployed Windows `81c10a84` to integration differs only in two documentation files; no cosmetic redeployment.

### Task 2: Add the bounded repeat and native dispatch

**Files:** Create `examples/alohamini/scripted_leader_repeat.py`; modify `teleoperate_bi.py`, `tools/am1_session.py`, `tools/run_am1.ps1`, `tools/run_am1_session.ps1`; tests in existing scripted-input and launcher test files.

**Interfaces:** `ArmSmokeRepeatInput(initial_positions, *, joint_keys, fps, emit=None)` exposes the existing provider methods and progress properties. Native profile selection uses `ArmSmokeRepeat`; duration is exactly 420 seconds. Final evidence includes cycle count and qualified boundaries; ordinary provider construction remains unchanged.

- [x] Write and observe RED for frozen seed, four cycles, qualified/failing boundaries, freeze/stale/cancellation and actual native dispatch.
- [x] Implement composition around the unchanged provider and exact profile forwarding through existing launchers. Preserve one terminal summary and verify it matches the requested profile.
- [x] Run focused scripted/launcher/native freshness and cleanup tests with fake hardware; inspect failures before correcting.

### Task 3: Prepare runnable observation and powered follow-ups

**Files:** Modify `tools/am1_reliability_bench.cjs`; extend existing fake console-path tests; update `docs/alohamini/unified-session.md`.

- [x] Add explicit repeat (420 s) and ordinary physical-leader (180 s) scenarios to the existing single-Start visible-browser runner; preserve current scenarios and ownership/Stop/recovery restrictions.
- [x] Observe RED then GREEN for actual HTTP profile/duration dispatch, required views, cleanup and identity bounds. Run changed JS syntax/diff checks.
- [x] Inspect Duffy/P1 and existing capture/stop routes read-only; subsequently verify restored leaders and capture/retrieve/inspect the spare camera.
- [x] Prepare stopped/power-off reconnection and the ordinary physical scenario; latest owner steering uses bounded virtual controls.
- [x] Request focused independent review, resolve important findings, commit/push and attach draft PR #16 against integration; runtime 7249eec3 staged on Windows with rollback81c10a84.

### Task 4: Restore connections and qualify the ordinary powered path

- [x] Verify restored original leader identities and non-actuating preflight; capture/retrieve/decode/inspect two fresh private spare-camera clips.
- [x] Preserve body-01 first refusal and owner-confirmed power repair; review/fake-test/exclusively execute normal all-zero/off proof without changing original verdict.
- [x] Complete body-02 ordinary frontend press/release and verified cleanup; keep measured lift results separate from unmeasured wheel displacement/static leader reads.
- [x] Preserve arm-01 temperature-history first fault and replay actual timestamps without changing policy; document measured outward left shoulder-lift response and unresolved return.
- [x] Add/review/test bounded completed-loop history; stage only exact Pi motor diagnostic with separate pin/hash/backup/rollback and no owner.
- [x] Freshly qualify all 20 zero/off registers after arm-01, retaining original cleanup_unknown.
- [x] Preserve arm-02 pre-motor frontend pause and arm-03 partial-motion camera interruption; no recovery approval or automatic restart.

### Task 5: Investigate the observation interruptions

- [x] Add passive frontend response/failure/pageerror evidence; fake 503/reset RED then five affected checks GREEN.
- [x] Run the existing finite camera-only launcher under both session locks, normally exit 0, verify no owners and inspect private read-only request/decoded-view evidence.
- [x] Add bounded per-role camera evidence in the runner's existing timed read; fake required-view-loss RED then four affected checks GREEN, zero added lint.
- [x] Complete independent review and ordinary arm-04 commanded 88-second trajectory with verified cleanup after fresh source/owner checks; physical return remains incomplete.
- [x] Run one deliberate four-cycle attempt after ordinary qualification; retain arm-05 as a 51.009-second partial trajectory, camera-loss Stop/130 and verified cleanup.
- [ ] Qualify the full continuous four-cycle workload; arm-05 did not complete a cycle.
- [x] Complete corrected camera-only timing capture, preserve pre-capture refusals, and freshly verify stopped clean sources/no owners. This does not qualify a powered camera repair.
- [ ] Qualify moving physical-leader input if requested separately; current owner steering uses virtual controls.
- [ ] Resolve left shoulder-lift small return from demonstrated command/normalization/readback evidence; do not alter gains/amplitude/calibration without a cause.

## Current checkpoint

Candidate: `codex/am1-reliability-02` from integration `9aa6d3b0`, in
`C:\Users\pickm\.codex\worktrees\am1-reliability-01\lerobot_alohamini_client`.
Shell: PowerShell 7; reuse `C:\Users\pickm\lerobot_alohamini_client\.venv`
with `uv run --no-sync` and candidate root/`src` on `PYTHONPATH`.
Draft PR #16 targets integration. Windows runtime remains 7249eec3; separate
bench runner 1a8ac290 and host diagnostic 81034618 are separate candidate commits;
the redacted documentation records their reviewed results. Pi motor diagnostic 6d99b263 is
deployed; helper 8e6a0cf6 and camera 9b1f0670 remain separate.

Rollback while stopped/unowned: Windows 81c10a84 plus only its private pin;
independently Pi motor c3fc683d plus only its private motor pin. Preserve private
permissions, mappings, calibration and backups. Current motor session is stopped
with verified cleanup. The camera-only diagnostic finished, released the existing
session locks and left no runtime owner.

Keep packet-01 verdicts unchanged: arm-01 native complete but Stop/130;
arm-02/03 successful 30-second portions; arm-04 one 88-second trajectory in
90.025 seconds Live, four bounded empty polls, no feedback pause/recovery
and verified cleanup. Action interval 110 ms is not latency; temperature/current
are lift-only; endpoint errors are normalized units. Raw logs/images/configs
remain private.

At runtime 7249eec3: affected core 304 passed, 1 POSIX process-group skip on
Windows; affected bench 18 passed, 25 deselected; nine affected checks reran
after test-only lint repairs. AST, JavaScript syntax, diff checks and Ruff
comparison recorded zero added findings (63 inherited in seven checked files).
New diagnostic checks retain their separate source versions and outcomes above.

Final source verification: seven affected fake-browser cases passed, 39 deselected,
at bench 1a8ac290; AST/JavaScript syntax/diff checks and exact preservation of older
packet evidence passed. Independent documentation review is clear.
Publication target: the existing draft PR #16 on integration. Preserve its draft
state, publish the redacted candidate, and record the verified remote head in
the PR/private ledger; no main merge is authorized.
Do not label a camera-only pass as powered camera repair, a partial repeat as
continuous endurance, or the return envelope as precise physical return.
