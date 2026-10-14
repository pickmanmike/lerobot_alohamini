# AM1 mobile local observation implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development. Steps use checkbox syntax.

**Goal:** Complete Pi-owned finite simulated motor execution using current robot-local images, with P1 and browser advisory.

**Architecture:** Keep the existing finite executor, private IPC and authenticated gateway. Select one immutable sensing policy before Start. Reuse the existing exclusive camera-only owner through its authenticated same-Pi JPEG route, adding original-arrival and restart identity metadata; decode in a separate bounded worker.

**Tech Stack:** Existing Python, aiohttp, Pillow, SQLite, systemd and browser WebSocket client; no new runtime framework.

**Spec:** Owner-approved AM1-MOBILE-LOCAL-01 continuation, retained privately as `.cache/am1-mobile-local-01/packet.txt`; public context is the mobile continuation review on PR #19.

## Global constraints

- AM1 stays wireless. No adapter, router, Ethernet or motor experiment.
- All motor commands, feedback and cleanup remain SimulatedIO; no live backend factory.
- Required local arrivals expire after 500 ms through actual consumption. Arrival is upstream completion on Pi, not sensor exposure.
- Strict P1 identity, nonce, JPEG decode and 500 ms freshness remain unchanged. Failed historical results remain failures.
- Policy is fixed before Start and stored with immutable run metadata/results. No active downgrade or synthetic fallback.
- P1 is advisory in local mode; it cannot gate Start, task tick, recovery episodes or original deadline.
- Reuse camera exclusive ownership/config/role mapping. Normal deployments and component pins remain separate.
- Preserve original provider seed/reference/admission, three total bounded recovery episodes and original session deadline.
- Preserve shared trust/enrollment/private permissions and explicit dependent PR stack; no merge, retarget or force push.

## Review focus

- Old or replayed camera generation must not qualify after reconnect or source restart.
- A new HTTP response must not renew original upstream arrival age.
- Actual undecodable bytes or sequence alone cannot qualify; stationary identical images can.
- Delayed optional P1 operations cannot affect a newer task/run.
- Browser closure and administrative SSH exit cannot remove required local sensing.

### Task 1: Source policy and local adapter

Files: `tools/am1_local_observation.py` (new), `tools/am1_pi_executor.py`, `tools/am1_session_core.py`, `tools/am1_camera_viewer.py`, affected IPC/UI only if needed; `tests/robots/test_am1_local_observation.py` (new), existing affected camera/runtime tests.

Interfaces: immutable owner configuration `simulated`, `p1-required`, `local-camera-required`; exact local source role/identity; existing `observation_sync` mailbox IPC. Current authenticated JPEG response adds atomic original monotonic arrival and owner/upstream generation. Local worker uses existing same-Pi route and no device access.

- [x] Author/run failing checks for policy isolation, original-arrival expiration, decode and generation/replay, same-run bounded recovery.
- [x] Implement smallest fixed-policy selection plus immutable metadata/result provenance. Preserve existing strict path and compatible explicit source selection.
- [x] Add original-time/generation seam without changing existing snapshot tuple/users; retain separately accepted camera logging lock and SO_REUSEADDR fixes.
- [x] Implement bounded latest local decode worker and private delivery pause fixture; no camera opens or waits in owner.
- [x] Run affected Windows checks, format/lint and review diff; commit scoped executable source.

### Task 2: Native wireless acceptance

Files: only ignored private staging, configs, rollback and browser/evidence harness under `.cache/am1-mobile-local-01`.

- [x] Stage clean isolated Pi checkout/environment and stopped affected services; preserve accepted/strict checkouts, state, camera normal installation and all unrelated owners.
- [x] Launch bounded independent camera-only owner via existing launcher with unchanged config; verify actual chest image and timing/route. Record exact component hashes and rollback.
- [x] Run affected checks natively on Pi.
- [x] Execute ONE ArmSmokeRepeat full four-cycle/352-second trajectory with original420 ceiling, mapped shoulder recipe and simulated motor IO.
- [x] At Start leave P1 not required; show optional absence consumes zero task recoveries. Close/reopen browser and preserve run, seed, reference, admission and deadline.
- [x] Pause only isolated local worker delivery briefly; show actual freshness expiry, frozen provider, qualification and same-run recovery before finite completion.
- [x] Correct demonstrated private optional media startup/status mismatch with same-operation idempotency; one bounded optional-view attempt after local target, preserving any partial result.

### Task 3: Focused review and publication

Files: `AGENTS.md`, `docs/alohamini/roaming-resilient-sessions.md`, `docs/alohamini/unified-session.md` and this outline.

- [x] One focused source/interface review, correct supported findings and run final affected checks.
- [x] Update active scope/runbook with actual results, wireless/local timing/coverage limitations, unresolved strict P1 benchmark and component-specific rollback.
- [x] Publish source/docs on existing PR #19, retaining stack #19 -> #18 -> #17 -> #16 and failed historical evidence.
- [x] Record next protected physical adapter interfaces precisely; do not implement/energize it. No executable tests for documentation-only publication.
## Completion evidence

Owner/worker executable `c5002049`; separate camera metadata `f9c3d04c` and reviewed correction `77c0dda0`. Windows: 107 Python and 40 camera passes. Native: 146 passed, one Windows-only skip. Correction: 40 camera passes on both hosts and three loaded UI passes. One focused review with six-line correction and scoped reread completed.

Native run `cb5cb556-023e-4417-b8bf-8a0f0115802b` completed 352 trajectory seconds/four cycles/four returns in 362.2166/420 Live seconds, with 2.5 seconds of preparation. Browser fully closed for 15 seconds and reattached to the same run without recovery. One controlled required loss froze progress at 30.4 seconds, then qualified the same admission and recovered one episode. Motor IO/cleanup stayed simulated; real chest input used original local arrival, not exposure. Browser traffic used `wlan0`; required input used `lo`.

Advisory attempt `09ebc09c` passed initial decode, selected UDP/LAN path and same-source reconnect; restart was not attempted. One actual finite audio-off recording lasted 15.955 seconds and was independently hashed/released. The earlier pre-capture watcher fixture failed; it and historical strict P1 failures are preserved. After terminal state, the introduced camera's 660-second bound produced a timeout and second SIGTERM interrupted application cleanup. Independent process/listener/device release and exact temporary-file reconciliation were confirmed; the limitation is documented.

Scoped publication on existing PR #19 preserves the dependent stack and normal physical pins. The protected physical adapter brief is recorded; no live factory was implemented. No repeated executable checks for documentation.
