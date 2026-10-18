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
| Packet18 initial source checkpoint | `2647b77c558a36f2b07f5730cf7906b4022ba868`; integration base `386d3c376c51d3431cf379af61ab99be95382ab8` after PR24 |
| Installed motor | `c9190b110858e1060daaf849981295cb5b06cba7` |
| Installed runtime | `30263a6c73ba8d76e6b17afc4a45c6a755754ac3` |
| Installed camera / helper-manual | `bc3442b54d9c574a8feddb0ade642c9b351e59f9` / `0fe3997f12b9cdfdd01fda6f4c6b102781252448` |
| Installed matched manifest | `bcdf4ad7a925c21bdcc8d57284554c985deb01aad4f26bf09c73b97a0a926659`; all ten Stage23 stopped/idle phases passed |
| Immediate full matched predecessor | Motor `19bedce59babc4b4b3e12af4d5e9bc21909eee9d`, runtime `30263a6c73ba8d76e6b17afc4a45c6a755754ac3`, same camera/helper and manifest `ad91a57c5add052302d9bd0735d81f82fd8e75add2fc9286056162b935c7cf6f`; full bundles archived before Stage23 upload; older pairs retained. |

Deployment authority: [Packet16 source/deployment record](teleop-consolidation-16.md#current-status-and-source), corroborated by private `.cache/am1-teleop-consolidation-16/current-state-16-published.private.md`. That private checkpoint retains full matched bundles, exact staging records and permissions; do not copy credentials or private logs into Git. Verify actual service interpreter/cwd/import roots before changed code is credited as deployed. A rollback identity is not proof of a cure.

## Accepted capabilities and limits

- September Local operation accepted combined arm/base/lift controls, actual-bottom homing and relief; an isolated 92 C numeric lift report was retained/tolerated. [TEMP-01](decisions-and-lessons.md#temp-01-confirm-numeric-temperature-anomalies) preserves the decision and its role-specific limits.
- Packet14 physically demonstrated ordinary sequential control of either elbow, useful approximately 6–8.3 degree movement per nominal 10 degree command, ordinary re-jog, same-session browser return and a normal-rest next Start. Fine convergence, simultaneous all-joint control and long-duration operation were not demonstrated by those checks.
- Current input and selected onboard WebRTC paths are implemented/staged. WebRTC has partial real playback evidence; planned view switching remains unqualified. Actual local sensing is task-selected; P1 cannot substitute for missing required evidence.
- Combined15 remains a failed partial run: 116 Core-accepted frames, 11.002 active producer seconds, two qualified view-expiry rejoins, then a valid high-temperature reply and host fault. All 20 off/zero readbacks, lift qualification, closed buses and camera release were verified. Cleanup is not program completion.
- The final PC-only ambiguous-Start repair at the published checkpoint has recorded offline adapter checks; it changed no installed Pi pins and adds no powered qualification. Historical test counts stay with their source versions.

## Current unfinished work and next action

Packet18 supersedes proposed diagnostic-first Packet17. TEMP-01 parity is installed: isolated numeric highs are logged/tolerated using continuous independent slot history and frozen installed follower ceilings. Genuine device faults, confirmed heat and stale/invalid feedback still fault. Combined19 retained a tolerated 57 C lift outlier; Combined20 retained a tolerated 125 C follower outlier. Exact spike causation is no longer a prerequisite.

Reviewed logging and thermal acquisition-order repairs are installed at the motor pin above. Complete settings remain lossless within the unchanged 32768-byte record bound; original five slot maxima/times and 0.5 s age bound remain. The pause-acknowledgment repair passed 161 affected executor/Core checks and is installed at the runtime pin above after all ten Stage21 phases. Component source projections differ from the PR source commit; service imports and matched manifests establish actual deployment.

Combined22 reached four fresh-control rejoins before PC nonadmission refusal; Combined23 failed during startup settings verification. Their originals, separate first causes, verified cleanup and source-specific corrections remain in [Packet16](teleop-consolidation-16.md#active-continuation--packet18-2026-10-17). The installed verification hook passes189 affected checks and was physically exercised by Combined24.

**Earlier partial run: Combined24.** Run `8abb818b-77a3-4629-8bf6-58500d642c11`, original native `96ccc50e2397cf0583c8d16af905afcd7163cffb95184802d539d6f195ad7b7d`, accepted904frames and qualified the planned60s/.65s input-gap reattachment, then stopped after PC WebSocket loss during an ordinary rejoin's pending `connect`. That command's outcome is ambiguous. Native/Core causes remain null, cleanup verified/errors empty, lift/buses/camera qualified and uncertaintyfalse. All six originals/full SQL/strict audit are retained. The analyzer confirms one run, fixed reference/workspace/original300s deadline;864unique feedback samples include54.18s of sampled arm-motion intervals. Body commands stayed zero; the body phase, full program, view switch and next Start remain incomplete. All three services retain PIDs with zero restarts; this does not identify the initiating socket cause. The four rejected inputs were explicit expired grants; no opaque-mailbox physical success is inferred.

The PC-only pending-connect correction passes188 adapter checks and scoped lint; independent review is clear. It permits one replacement per ambiguous attempt using the exact unchanged ordinary-pause event, original identity/history/reference/workspace/deadline and current held proof. A completed replacement permits a later distinct bounded attempt; nested loss and later-operation ambiguity fail closed. The old command remains ambiguous; no command/frame replay, Claim, Start or clock reset occurs. The PC correction is published at `3e3728de555b88c41510e4fd89c45e08c230240d`; it was not exercised by the following pre-Live failure. Current Pi pins remain fixed.

**Latest startup failure: Combined25.** Run `0da9268e-74d4-4a69-807e-4addddd10604`, original native `3a32f1cbd10dd1a0f558007f595944cf8be6bd0a59546295e716677ddc61fee2`, stopped before Live with zero program frames. Its native first cause is the lift five-slot stale-window refusal; Core separately records protected-host exit during startup. All six originals/full SQL/strict startup-fault audit are retained, cleanup verified/errors empty, all20off/zero, lift qualified, buses closed, camera released and uncertainty false. Missing Live/settings telemetry stays missing.

The moving-relief loop sleeps100ms plus I/O/callback work; actual109.019457ms spacing skips anchored slot73. Later settling correctly refuses the oldest retained peak at0.501922603s, despite a56.005695ms latest raw gap and33C readings. This occurs before the installed verification hook. The narrow correction selects the existing normal50ms feedback cadence for operational relief; standalone relief retains its100ms default. No threshold, window/history/original time, geometry, current/direction check, velocity or deadline changes. The final affected lift cohort passes318 checks in6.66s and scoped lint; independent source review is clear; physical qualification is pending. All ten Stage23 stopped/idle phases passed; actual service imports match. Only the two reviewed motor files and one Windows motor-pin literal changed; full matched rollback and original ACL are retained. Source candidate is `1083dfc00fc29aaff4d6d0078aae471d08ce9a2d`. **Next action:** the original **172.5 active-second / 300-second-ceiling** workload and supported separate3s next Start.


All original successes/failures and source-specific test cohorts remain in [Packet16](teleop-consolidation-16.md) and private immutable originals. In particular, Combined18/19's original missing/unknown receipts remain unchanged; later genuine stopped proof and separate durable acknowledgment cleared current uncertainty. Known cleanup is not program completion. The one fresh-context read check is complete; do not repeat it.

Physical USB identity/stationary sampling and human leader ergonomics remain distinct from virtual-source evidence. Dock, battery and GPU/cloud arrangements remain plans unless separately verified. [The dated Project snapshot](project-snapshot-2026-10-17.md) is a repo export; no ChatGPT Project replacement is claimed.
