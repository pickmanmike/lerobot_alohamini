# AM1 current state

Updated 2026-10-17 for owner **AM1-KNOWLEDGE-REUSE-18**. Read [decisions and lessons](decisions-and-lessons.md) before diagnosing a known symptom; detailed commands/results remain in [Packet16](teleop-consolidation-16.md). This is an active index, not a replacement for historical evidence.

## Objective and operating scope

Dependable physical-leader AM1 manipulation and telepresence come first; Quest control and concrete household autonomy follow. Explicit virtual leaders substitute at acquisition and exercise the same mapping, authenticated transport and protected Pi execution. Per-motor controls are diagnostics.

AM1 remains wireless; P1 is temporary/advisory; AM2 and `main` are unchanged. Pi owns hardware, operation identity, deadlines and cleanup; browsers attach. Service startup restores an idle interface and never starts or replays motor work. Durability means software continuity/state preservation/recovery, not mechanical lifespan.

Packet18 authorizes knowledge recovery, focused offline parity and a narrow supported carry-forward correction, followed by the existing reviewed Packet16 process. The explicitly invoked AM1 reliability scope permits finite scripted/virtual input, normal owned body controls and qualified same-session recovery without attendance/readiness/manual-leader questions. Preserve protections, single ownership, calibration/mappings, separate pins and private permissions. Physical leader movement and independent cutoff development remain outside that automated packet.

## Published, tested and deployed are separate

| Identity | Recorded state |
| --- | --- |
| Repository / working PR | [pickmanmike/lerobot_alohamini PR25](https://github.com/pickmanmike/lerobot_alohamini/pull/25), `codex/am1-teleop-consolidation-16`, draft/unmerged |
| Packet18 source checkpoint | `2647b77c558a36f2b07f5730cf7906b4022ba868`; integration base `386d3c376c51d3431cf379af61ab99be95382ab8` after PR24 |
| Installed motor | `338bb66a62700f580aab172c6f8a17a313146080` |
| Installed runtime | `cdbd6f84b7981cf00dc1d6619428d260046c0673` |
| Installed camera / helper-manual | `bc3442b54d9c574a8feddb0ade642c9b351e59f9` / `0fe3997f12b9cdfdd01fda6f4c6b102781252448` |
| Installed manifest | `f768c580c3092bf95d1717cc218114fe58049043fb43c9750897a099066edf75` |
| Immediate full matched predecessor | Motor `5302594d15a70ba1873eb2bd5ea2c9f02bc6e1d8`, runtime `68118e4e9dddf8dddaa3ea6cbf64f54fdd511980`, same camera/helper, manifest `1190ea195ea24906abbd16f5bd8d4a5f7aa7b73313d56b7364f73af5bb9c3ea6`; full bundles archived before checkout; older matched rollback retained. |

Deployment authority: [Packet16 source/deployment record](teleop-consolidation-16.md#current-status-and-source), corroborated by private `.cache/am1-teleop-consolidation-16/current-state-16-published.private.md`. That private checkpoint retains full matched bundles, exact staging records and permissions; do not copy credentials or private logs into Git. Verify actual service interpreter/cwd/import roots before changed code is credited as deployed. A rollback identity is not proof of a cure.

## Accepted capabilities and limits

- September Local operation accepted combined arm/base/lift controls, actual-bottom homing and relief; an isolated 92 C numeric lift report was retained/tolerated. [TEMP-01](decisions-and-lessons.md#temp-01-confirm-numeric-temperature-anomalies) preserves the decision and its role-specific limits.
- Packet14 physically demonstrated ordinary sequential control of either elbow, useful approximately 6–8.3 degree movement per nominal 10 degree command, ordinary re-jog, same-session browser return and a normal-rest next Start. Fine convergence, simultaneous all-joint control and long-duration operation were not demonstrated by those checks.
- Current input and selected onboard WebRTC paths are implemented/staged. WebRTC has partial real playback evidence; planned view switching remains unqualified. Actual local sensing is task-selected; P1 cannot substitute for missing required evidence.
- Combined15 remains a failed partial run: 116 Core-accepted frames, 11.002 active producer seconds, two qualified view-expiry rejoins, then a valid high-temperature reply and host fault. All 20 off/zero readbacks, lift qualification, closed buses and camera release were verified. Cleanup is not program completion.
- The final PC-only ambiguous-Start repair at the published checkpoint has recorded offline adapter checks; it changed no installed Pi pins and adds no powered qualification. Historical test counts stay with their source versions.

## Current unfinished work and next action

Packet18 supersedes proposed diagnostic-first Packet17; do not assume Packet17 ran. Shared follower anomaly confirmation is implemented, independently reviewed and staged at the motor pin above, using each frozen installed ceiling and continuous per-bus/ID/run history. Historical lift 55 C/cold-start rules remain role-specific. The first parity cohort passed **385 focused checks**; this does not qualify physical workload completion.

**Combined16 failed before Live**, with zero program progress: cool 32 C samples spanned 0.507185646 s and correctly tripped the unchanged 0.5 s freshness rule. Its original run `94358dfa-4363-4658-9fd4-fe0dc82c5499` and native hash `618a0961a58708a4aa15fc663f2e457821a52d3252b0d77f9b75caf7ce8682b7` remain retained. All 20 off/zero readbacks, closed buses and camera release were verified, but lift qualification was unavailable before home; Core originally retained cleanup uncertainty. A later genuine read-only proof and durable executor acknowledgment qualified the stopped state while preserving the original fault and incomplete cleanup receipt. No workload, input-gap, view-switch or next-Start credit is claimed.

A narrow due-sampling correction across startup configuration/calibration/raw reads is committed at `bd3b5637906c92e0bc863acc509a560327b308cf`, independently reviewed, with **455 affected checks passing**. It preserves every calibration read, ordered off write, original slot/age limit and first cause; the saved failed trace still refuses. The companion stopped-reconciliation correction is committed at `1eefdcddee5424ed752ddf58ffbd142b223bb6ed`, with 135 checks passing and one existing Windows skip; independent review and scoped lint passed. Both follow-ups are now staged in the matched installation above: all ten Stage15 phases passed after 94 focused private staging checks and independent review. **Combined17 also failed before Live/zero frames**, at the lift pre-torque freshness assertion after a passing nine-sample off baseline. Its final raw sample retained an oldest 32 C peak aged 0.496057583 s; the next slot was due about 3.94 ms later. Run `526245cf-6cf8-4944-a26e-fefec8fa132c`, original native hash `863e6c59ae206f6d3c5eb48e88f5de71e6e532930a45e420542082679f46bfc7`, six original files and full terminal audit are retained. All 20 off/zero readbacks, lift qualification, closed buses and camera release passed; uncertainty is false. No specific intervening I/O latency is established by that record. The focused off/zero handoff correction now passes **264 affected checks** (including 16 new boundary cases), with scoped Ruff clean. It permits at most one genuinely due read within the original 600 ms budget, preserving the nine-sample baseline, thermal history, final freshness check and hard guards. Independent source and staging review passed; the correction remains unstaged. The next action is reviewed affected staging, then the existing **172.5 active-second / 300-second ceiling** Packet16 workload, planned interruption/fresh rejoin, selected-view switch and separate brief next Start. Do not replace that qualification with another joint menu or simulation-only milestone. The single fresh-context read check already recovered the policy, scope, deployment and next action from these active references.

Physical USB identity/stationary sampling and human leader ergonomics remain distinct from virtual-source evidence. Dock, battery and GPU/cloud arrangements remain plans unless separately verified as installed. Export [the dated Project snapshot](project-snapshot-2026-10-17.md) if needed; no ChatGPT Project file replacement is implied.
