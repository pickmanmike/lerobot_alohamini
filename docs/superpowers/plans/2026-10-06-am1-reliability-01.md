# AM1 reliability refinement implementation plan

> **For agentic workers:** Execute inline using superpowers:executing-plans, with a read-only independent review before staging and at completion.

**Goal:** Reduce demonstrated avoidable feedback interruptions while preserving qualified recovery, motion bounds, ownership and verified cleanup.

**Architecture:** Keep the existing native client, Scripted/ArmSmoke, owning Pi host, session supervisor and DirectBrowser path. Reproduce request/reply and optional-output delays with fake IO before changing the responsible component. Preserve PR #14's exact independent Windows pin and deliberate retry correction.

**Tech stack:** Python, pytest, PyZMQ, PowerShell, existing Node/Playwright browser fixtures.

**Spec:** Owner's AM1-RELIABILITY-01 packet, October 6, 2026.

## Evidence and working hypothesis

The latest saved session completed 90.11815 seconds live with three feedback gaps; recovery took 21.265 seconds manually, then 0.547 and 2.343 seconds automatically. Its native client recorded 131 unusable polls. Replay found 3,984 host live lift samples with no interval above 100 ms, and maximum sampled lift temperature 36 C/current 175.5 mA. The first feedback pause preceded browser expiry; verified cleanup and active SSH are downstream evidence, not a Wi-Fi diagnosis.

Working hypothesis: oldest-token waiting can withhold another current tracked reply, and retiring empty polls can discard usable late replies. Discriminating test: provide advancing valid replies for later active requests while withholding the oldest; compare accepted feedback and actual request age without changing the three-credit bound or freshness deadline. The historical network/client scheduling cause remains unknown until demonstrated.

## Global constraints

- Start from `915a32d4d9ac42433c1aee95f4f0c74f348c5dda`; use one `codex/am1-reliability-01` follow-up PR. Do not merge or change main; PRs #12/#13 stay closed.
- One live motion owner. Review agents are read-only and cannot issue hardware commands.
- No new servo reader, SSH tailer, queues, gains/calibration changes, fabricated freshness or widened safety deadlines.
- Preserve raw failures, same-session gates, user Pause/Stop, source/ownership checks, drift/current/thermal/status guards and explicit cleanup proof.
- Stage only reviewed affected components while their runtimes are stopped; preserve private configuration, pins and rollback.
- Keep private logs, household images and credentials out of Git.

## Review focus

- A missing early reply must not conceal an available advancing tracked reply.
- Late pre-pause, unknown, duplicate and out-of-order replies must not authorize recovery.
- Genuine stale/malformed feedback and lost ownership remain refusals.
- Optional output/camera delays must not prevent Stop or expire control through a blocked owner lock.
- A stalled owning motor process cannot enforce its in-thread watchdog; state that limit and withhold any powered scenario without acceptable independent termination and torque-off support.

## Execution

- [x] Read current runbook, deployed pins, recent logs and production paths; verify stopped clean Pi owners and candidate import root.
- [x] Add the smallest failing request-order/delay case in `tests/robots/test_alohamini_windows_live_cadence.py`; run it and retain the actual failure. Three cases failed before the correction.
- [x] Correct only the demonstrated client transport behavior in `src/lerobot/robots/alohamini/alohamini_client.py`; rerun the same case and relevant recovery/request tests. Cadence file: 91 passed; selected reply keeps its real send time, including a 1.05-second stale reply.
- [x] Exercise existing fake browser/frontend/HTTP/native-pipe scenarios with representative camera/output load, pending Stop and controller loss. The five bench-driver scenarios pass, including foreign-owner refusal and Stop despite two failed monitor reads. Corrected stale fixtures for terminal cleanup, telemetry and PR #14's extra actual-source query; full local-path file plus former failures: 30 passed, 2 skipped.
- [x] Review the affected diff, tests and watchdog/cleanup limitations before staging. Independent read-only review found no remaining actionable issue after runner ownership/cleanup corrections. Motor-owner freeze injection remains offline-only: an in-thread watchdog cannot execute while its process is frozen.
- [ ] Stage only affected stopped Windows code with its separate exact pin; verify actual import root and non-actuating preflight. Preserve Pi helper/motor/camera pins unless a demonstrated correction requires their component.
- [ ] Use existing finite ArmSmoke and normal owned body paths only when support/termination is established. Identify each attempt, use finite duration and bounded attempts, verify cleanup, and review a fault before another powered attempt. Otherwise record the concrete withheld scenario and complete offline work.
- [ ] Record comparable before/after evidence, actual commands/results, session identities, exact deployed versions and rollback in `docs/alohamini/unified-session.md`; commit, publish one draft follow-up PR, and perform final independent review.

## Packet execution boundary

The owner explicitly confirmed that the arms can safely swing down upon release from any resting position. Use only the existing normal home/relief lift arrangement and its qualified cleanup. No powered stall injection or forceful interruption of a motor owner is authorized by this plan. The small browser driver has one Start per invocation, visible/focused real input, normal frontend Stop, a native live-duration limit and a separate finite attempt/cleanup deadline. It never approves a manual recovery gate or restarts an unresolved failure.
