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
| Installed motor | `19bedce59babc4b4b3e12af4d5e9bc21909eee9d` |
| Installed runtime | `30263a6c73ba8d76e6b17afc4a45c6a755754ac3` |
| Installed camera / helper-manual | `bc3442b54d9c574a8feddb0ade642c9b351e59f9` / `0fe3997f12b9cdfdd01fda6f4c6b102781252448` |
| Installed matched manifest | `ad91a57c5add052302d9bd0735d81f82fd8e75add2fc9286056162b935c7cf6f`; all ten Stage22 stopped/idle phases passed |
| Immediate full matched predecessor | Motor `4cc9e04bff5283541524a6f61463b331587258b9`, runtime `30263a6c73ba8d76e6b17afc4a45c6a755754ac3`, same camera/helper and manifest `de4aa239750bd681fa1089c40afd0bb6864b3756babb5db325b33fd818ab7362`; full bundles archived before Stage22 upload; older pairs retained. |

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

**Earlier partial run: Combined22.** Run `dad116dd-4ed3-419a-bf6e-adc96d22d7d2`, original native `d16a00da59fb3dac1f9c29afa57f4b659c62f8693c522ba2d90bf5143b017bfb`, reached 156 accepted frames / 14.595 active producer seconds and four ordinary fresh-control rejoins without the prior native epoch fault. The PC then refused an opaque `physical input mailbox refused` result and issued ordinary Stop. Native status is stopped, both native/Core first causes are null, cleanup is verified, uncertainty false, lift qualified, buses closed and camera released. All six originals/full SQL and strict admitted-terminal audit are retained. The analyzer confirms one run, fixed reference/workspace and original 300 s deadline. No complete program, planned gap, view switch or next Start is credited.

The opaque mailbox's exact backend branch remains unknown. The reviewed PC-only correction is published at `4315c0509f652e071ded1dfa9d2bcea851c412aa`; 142 adapter checks (29 new), scoped lint and independent review pass. Discarded targets stay discarded; only fresh authoritative matching ordinary native-expiry pause plus existing held/alignment/Resume qualification permits a new grant/new acquisition. This adds no physical qualification.

**Latest attempt: Combined23.** Run `1d1f06ff-a8e4-400c-a2cd-65664f2b35d7`, native `b4024a7023ae3689358e7313199a7a1802a69ddb57c7b5a848e753c4e1b859aa`, faulted before native Live with zero program frames. Native first cause is the lift's five-slot stale-window refusal; Core's host-connect timeout remains separate. All six originals/full SQL/strict startup-fault audit, verified cleanup, lift qualification, closed buses and camera release are retained; uncertainty is false. The analyzer has no Live/reference/deadline observations, not evidence those invariants were changed.

The trace contains no lift acquisition in anchored slot75 while startup settings verification took96.75ms. The next five occupied peaks span71/72/73/74/76; another read in76 correctly refuses the old peak's0.521009740s age. The five-line same-owner hook correction passes189 affected temperature/settings checks, scoped lint and independent review. It preserves original slot maxima/times and the0.5s bound. All ten Stage22 stopped/idle phases passed; actual service imports match. Only the motor component changed, with one Windows pin literal and unchanged ACL. **Next action:** the original **172.5 active-second / 300-second-ceiling** workload plus supported separate3s next Start. No unchanged powered retry, window reset or temperature-causation campaign is warranted.


All original successes/failures and source-specific test cohorts remain in [Packet16](teleop-consolidation-16.md) and private immutable originals. In particular, Combined18/19's original missing/unknown receipts remain unchanged; later genuine stopped proof and separate durable acknowledgment cleared current uncertainty. Known cleanup is not program completion. The one fresh-context read check is complete; do not repeat it.

Physical USB identity/stationary sampling and human leader ergonomics remain distinct from virtual-source evidence. Dock, battery and GPU/cloud arrangements remain plans unless separately verified. [The dated Project snapshot](project-snapshot-2026-10-17.md) is a repo export; no ChatGPT Project replacement is claimed.
