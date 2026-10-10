# AM1 Pi simulated session runtime implementation plan

> **For agentic workers:** Execute the owner-approved AM1-SESSION-PI-01 packet in this session. Use the existing subagent execution and verification workflows. The packet requests one focused changed-interface review and one final affected executable verification; no new review/report framework.

**Goal:** Run the existing persistent session service and real finite providers natively on AM1 Pi with simulated IO, trusted direct reconnectable clients and SSH-independent process ownership.

**Architecture:** Retain the existing authority, private IPC, authentication and reconnectable UI. Extract only portable admission/finite execution seams; isolate OS-managed simulated owner and gateway from all normal robot deployments.

**Tech stack:** Existing Python 3.12+ service and scoped am1-session optional dependencies; native Linux Unix sockets/flock; existing browser tooling on Duffy.

**Spec:** Owner packet AM1-SESSION-PI-01, AM1_Pi_Session_Runtime_Continuation.txt (private), and [approved consolidated design](../../alohamini/roaming-resilient-sessions.md).

## Global constraints

- Follow-up codex/am1-pi-session-runtime starts at reviewed PR #17 head 261c509e2bdcf2f57a5b0786dd9ae23263e20fc0; draft follow-up targets #17's branch. Preserve #16, #17, integration and main without merging, retargeting or force-pushing.
- AM1 only; no physical hardware IO, normal robot connect, motor/ZeroMQ host, cameras, physical launchers, Wi-Fi tuning or manual leader/shoulder/attendance questions.
- Preserve actual private component pins, checkouts, environments, permissions, calibration/mappings, normal-rest protections and historical physical results.
- Qualify the existing fake-only service on native AM1 Pi Linux first. Windows/container evidence cannot substitute. Fix demonstrated portability defects; never skip Linux failures to claim success.
- Recovery: completed qualified executor-acknowledged episodes close; later distinct loss may get another <=10-second episode; <=3 total, unchanged original Live deadline. An ongoing loss, repeated request, gateway restart or HTTP success cannot renew or qualify recovery.
- Portable finite generation is the actual existing 10 Hz ArmSmoke/ArmSmokeRepeat plus supported preparation and corrected mapping. Original seed, admission, return boundaries and terminal provider evidence survive reconnect; the 50 ms authority tick is not a new servo cadence.
- Simulated measurements and acknowledgments are explicitly simulated. Numeric timers, request success and synthetic cleanup labels are not physical progress/cleanup. Restart invalidates authority and reports interrupted/uncertain without autoplay.
- Public API remains known recipes only: no arbitrary scripts, paths, serial commands or motor settings. Legacy manual runtime remains available.
- Dedicated Pi checkout, narrow environment and private state outside normal helper/motor/camera deployments; OS-managed owner/gateway separate, no boot autostart/reboot. Gateway restart must not kill owner; owner restart must not replay a run.
- LAN listener is explicit authenticated HTTPS/WSS with identity-matching certificate and explicit client trust; retain exact Host/Origin/CSRF/capabilities and private IPC. No TLS exceptions, wildcard CORS, credential URLs/logs, firewall disabling, router openings or required SSH forwarding.
- Reuse unchanged PR #17 Windows evidence (267 Python/one POSIX skip, 54 Node); retain earlier undiagnosed initial-Resume timeout separately. Add focused native/portable coverage and final affected checks, not the entire historical suite.
- Rollback stops/removes only dedicated trial services/listener, introduced trust and generated simulated state as applicable; retain branch/nonsecret evidence and existing worktrees. Physical milestones remain open; stages 3/4 are outside scope.

## Review focus

- Existing Linux IPC/lock/permission and restart behavior must run on target rather than being assumed from platform branches.
- A slow tick, pause/recovery, missing/old simulated measurement or absent backend ack cannot advance the provider, reset its seed or inflate progress.
- Completion and cleanup provenance must distinguish simulated backend evidence, uncertainty and the historical fake counter.
- Late cross-client Resume, expired renewal and stalled clients retain existing authoritative fences and bounded protective capacity.
- Direct TLS trust, process isolation and independent gateway restart must be demonstrated from Duffy; SSH administration cannot be an essential runtime transport.

## Task 1: Native qualification and portable simulated execution

**Files:**
- Reuse: tools/am1_session_core.py, tools/am1_session_ipc.py, tools/am1_session_service.py, examples/alohamini/am1_session_contract.py, existing scripted providers and remote UI transport.
- Create only if the existing seam requires: examples/alohamini/am1_session_runtime.py, examples/alohamini/am1_finite_task.py, tools/am1_pi_executor.py and focused tests/robots/test_am1_pi_session_runtime.py.
- Modify narrowly: service LAN origin/listener validation; owner executor integration; existing provider imports/shared portable extraction only when covered.
- Extend focused existing core/IPC/API/browser fixtures and tests as required; do not create another general harness.
- Update: docs/alohamini/roaming-resilient-sessions.md and existing runbook only with actual evidence and ordinary trial start/rollback.

**Interfaces:**
- Consume current provider constructor(initial_positions, joint_keys, fps=10, emit), admit(now), freeze(), advance(now, observed, advancing_sequence), get_action(), complete, elapsed_s and finish(); retain preparation and physical-equivalent amplitude semantics.
- Consume existing authority command/snapshot, private Unix IPC and known recipes; executor interface changes must be explicit and covered for both old fake and injected simulated adapters.
- Produce backend-derived portable finite progress/outcome and simulated acknowledgement/cleanup provenance, with no constructible live backend in the trial.
- Produce explicit LAN bind plus separately validated exact HTTPS origin while preserving default loopback behavior.

- [x] Verify current private deployment pins and inspect native OS/Python/architecture/service/listener capabilities without device access.
- [x] Stage exact accepted baseline in a dedicated Pi checkout/environment/state; run current focused native core/IPC/API checks and hardware-boundary checks. Record failures before fixes.
- [x] Write failing portable/provider/admission tests, implement the smallest extraction against injected simulated IO, then run covering checks. Demonstrate original seed, one admission, fresh sequences, delayed-tick cap, preparation/mapping, four-cycle boundaries and truthful terminal/restart states.
- [x] Cover shared legacy/new admission arbitration with fake contenders, separate from the isolated fake owner lock; do not patch/deploy normal motor service.
- [x] Configure the dedicated trusted LAN listener and independent non-autostart OS-managed test owner/gateway. Inspect scope/cleanup and preserve private secrets.
- [ ] Run Duffy direct browser cases: lost accepted Start reconciles same operation; detach/reload plus independently enrolled second context preserve identity/seed/deadline/progress; gateway-only restart preserves owner; owner restart reports interrupted/uncertain/new incarnation/no autoplay. Close launch SSH before continuity proof.
- [x] Obtain one focused fresh review of the changed runtime/ownership interfaces with spec and quality verdicts; correct actionable findings and scoped-check corrections.
- [ ] Final affected verification at executable candidate, exact native/browser evidence and source IDs, factual consolidated/runbook update, stacked draft publication and attachment.

## Current execution state

- Accepted PR #17 worktree was clean at 261c509e; isolated follow-up branch retains exact ancestry without deleting a worktree.
- Private configuration and all three actual Pi checkout heads agree; normal checkouts remain clean and physical calibration hash matches its before-staging value.
- Initial bounded administrative SSH attempts timed out before any remote command; the owner's Pi restart restored access. No inferred network diagnosis or adapter experiment.
- Dedicated target-host checkout/environment/state created outside normal deployments. Linux aarch64/Python 3.13.5 baseline: 75 passed, one Windows-only DACL skip, explicit exit 0. An earlier same-count run had an unexplained wrapper exit discrepancy; both logs are retained.
- Portable source candidate 0986568c: native 103 passed, one Windows-only DACL skip, exit 0. Actual OS-managed owner and gateway are independent transient user services with private devices; owner permits only Unix sockets. No boot autostart, hardware IO or automatic task start.
- One changed-interface review identified explicit simulated Resume alignment and recovery-send exception gaps. Both have failing focused reproductions; original implementer is correcting them. Final candidate verification/direct-browser proof remains pending.
- Trial public CA and identity-matching leaf prepared. An initial certificate loader error left trust absent, and real Edge rejected the untrusted site. The corrected current-user import requires Windows' Security Warning consent; no TLS exception or machine-wide trust workaround is used. Private browser driver waits for final source readiness.
- A generated marker's stray carriage-return filename stopped the first launch guard before any service action; only that marker was corrected. Attempt retained.
- PR #16 current pending-adapter sentence records cancellation/supersession; historical diagnostic findings are retained.

- Review corrections committed at c4256e9c; one scoped correction verdict passed spec/quality. Final root native checks: 106 passed / one Windows-only DACL skip, exit 0. Corrected transient services ready/idle; launch SSH exited. Direct trusted browser proof awaits the exact current-user Windows CA consent. Source/runtime checks are complete; publication documents the remaining completion condition truthfully.
