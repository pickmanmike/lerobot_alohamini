# AM1 Reliability 02 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. The owner expressly requires completion of available implementation, verification and publication before returning a physical task.

**Goal:** Integrate the demonstrated fixes and prepare bounded physical-input and continuous dynamic follow-ups without extending hardware privileges.

**Architecture:** Keep `ScriptedLeaderInput` and ordinary ArmSmoke intact. Add an opt-in `ArmSmokeRepeat` provider around four existing cycles; retain one original seed, normal Local startup, sender, freshness, recovery and cleanup. Every return requires three advancing, admitted observations over at least 0.2 seconds, each within 3 normalized units of that seed. No cycle triggers home, synchronization, recovery approval or a new session.

**Tech Stack:** Existing Python environment, PowerShell 7, installed Edge/Playwright and existing console/native bridge.

**Spec:** Owner-provided Packet AM1-RELIABILITY-02, attachment `0ed5acba-00af-4d91-9aa4-2c025c0d40da/Pasted text.txt`.

## Global Constraints

- Ordinary ArmSmoke remains one 88-second trajectory with unchanged amplitudes/rates.
- Repeat is explicitly selected, fixed at four cycles (352 trajectory seconds), with a 420-second native ceiling. No idle padding or reinitialization is endurance evidence.
- Physical-input follow-up uses one ordinary 180-second session, both physical leaders and brief owned body/lift press/release controls; owner supplies leader movements.
- Keep separate component pins, private configurations, calibration, PnP identities, supplies, environments, DirectBrowser and AM2 intact.
- Longer powered work requires supervision and an accessible disconnect; no process freeze, bus fault, overload, unrestricted recovery or automatic restart.
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
- [x] Inspect Duffy/P1 identities and existing capture/stop routes read-only. Spare absent; capture/retrieve/view remain explicitly pending.
- [x] Prepare stopped/power-off reconnection and one supervised physical session. Leave unavailable physical/visual/endurance evidence pending.
- [ ] Request focused independent review, resolve important findings, commit/push and attach a draft PR against integration.

## Recoverable checkpoint

Candidate: `codex/am1-reliability-02` from `9aa6d3b0` in `C:\Users\pickm\.codex\worktrees\am1-reliability-01\lerobot_alohamini_client`; shell PowerShell 7. Reuse `C:\Users\pickm\lerobot_alohamini_client\.venv` with `uv run --no-sync`, candidate `src`/root on PYTHONPATH. Deployed launch is `python -m tools.am1_console --config config/am1.session.json --no-browser` from `.worktrees/am1-console-implementation`; Windows-only source is affected by the proposed changes. Pi helper/motor/camera remain separate and unchanged.

Read-only console state remains completed prior arm-04, exit 0, cleanup verified, no pending gate. Duffy exposes no ports; configured left `USB\VID_1A86&PID_55D3\5B3D045224` / LEFT-LABELED-SOCKET (historical COM8), right `USB\VID_1A86&PID_55D3\5B3D048497` / RIGHT-LABELED-SOCKET (historical COM7), and the dedicated hub are absent. P1 SSH alias `codex-home` successfully returns `WIN-6E43SCJGTL7`; Camera/Image plus all present USB/Media inventories expose only THETA, not the spare observer. Existing OBS is untouched. Capture, physical input/body follow-up and repeat endurance are pending, not passed.

Keep packet-01 verdicts unchanged: arm-01 native complete but Stop/130; arm-02/03 successful 30-second portions; arm-04 one 88-second trajectory in 90.025 seconds live, four bounded empty polls, no feedback pause/recovery and verified cleanup. Action interval 110 ms is not latency; motor telemetry is lift-only; endpoint errors are normalized units. Raw logs, image data and configs remain private.

Final available verification: affected core **304 passed, 1 POSIX process-group test skipped on Windows**; bench subset **18 passed, 25 deselected**; nine affected tests reran after test-only lint repairs. AST, JS syntax, diff checks and Ruff 0.14.1 comparison pass (63 inherited findings, zero added; new provider zero). No powered session this packet. Private access ledger: `C:\Users\pickm\AlohaMini1Logs\am1-reliability-02\packet02-access-verification.json`. WinRT video-interface query at 23:09:33.5262847Z independently confirms only THETA/no spare, no capture initialized. Independent static review is clear and supports stopped Windows-only staging. Next steps at this pre-publication checkpoint: commit, stage Windows with rollback81c10a84, push and attach draft PR against integration; record resulting SHA/deployment/publication proof in its PR and private ledger. All physical/visual/endurance checks remain pending.
