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
| Installed motor | `6328bb524bc4e42c70f9896d66904177c1830277` |
| Installed runtime | `68118e4e9dddf8dddaa3ea6cbf64f54fdd511980` |
| Installed camera / helper-manual | `bc3442b54d9c574a8feddb0ade642c9b351e59f9` / `0fe3997f12b9cdfdd01fda6f4c6b102781252448` |
| Installed manifest | `de681ad6f73f2639a1971b96eee41214b5edfdc1730082e7f77b78a3c6795dbb` |
| Full matched rollback | Motor `2fbc8265b15393dcc799e603223b8902844e2919`, same runtime/camera/helper, manifest `1d64de8e1e2f05938a39fae7a67a9a3ff3b19f94ae456f311116c41e52bbbd57` |

Deployment authority: [Packet16 source/deployment record](teleop-consolidation-16.md#current-status-and-source), corroborated by private `.cache/am1-teleop-consolidation-16/current-state-16-published.private.md`. That private checkpoint retains full matched bundles, exact staging records and permissions; do not copy credentials or private logs into Git. Verify actual service interpreter/cwd/import roots before changed code is credited as deployed. A rollback identity is not proof of a cure.

## Accepted capabilities and limits

- September Local operation accepted combined arm/base/lift controls, actual-bottom homing and relief; an isolated 92 C numeric lift report was retained/tolerated. [TEMP-01](decisions-and-lessons.md#temp-01-confirm-numeric-temperature-anomalies) preserves the decision and its role-specific limits.
- Packet14 physically demonstrated ordinary sequential control of either elbow, useful approximately 6–8.3 degree movement per nominal 10 degree command, ordinary re-jog, same-session browser return and a normal-rest next Start. Fine convergence, simultaneous all-joint control and long-duration operation were not demonstrated by those checks.
- Current input and selected onboard WebRTC paths are implemented/staged. WebRTC has partial real playback evidence; planned view switching remains unqualified. Actual local sensing is task-selected; P1 cannot substitute for missing required evidence.
- Combined15 remains a failed partial run: 116 Core-accepted frames, 11.002 active producer seconds, two qualified view-expiry rejoins, then a valid high-temperature reply and host fault. All 20 off/zero readbacks, lift qualification, closed buses and camera release were verified. Cleanup is not program completion.
- The final PC-only ambiguous-Start repair at the published checkpoint has recorded offline adapter checks; it changed no installed Pi pins and adds no powered qualification. Historical test counts stay with their source versions.

## Current unfinished work and next action

Packet18 supersedes proposed diagnostic-first Packet17; do not assume Packet17 ran. Packet15 wrist expansion remains superseded by Packet16. The accepted thermal policy already exists in `lift_operational.TemperatureWindow`; the checkpoint's follower `read_groups`/`check_health` reject single numeric highs, while `qualify_rest` adds raw 55/local 40 C rules and per-phase resets. Carry forward shared confirmation with independent bus/ID/run history and due arm monitoring while startup/Live/recovery can remain powered. Retain original follower history and first cause through cleanup; zero/off cleanup uses its existing readbacks, without new post-off arm sampling. Packet18 authorizes arm thresholds from each motor's immutable captured `Max_Temperature_Limit` (currently recorded 70 C), separate from historical lift 55 C/cold-start semantics. Preserve device errors, invalid/stale refusal, original times, firmware limits and first cause; test positive/fault contrasts at the actual entrypoint before claiming parity.

Packet18 correction: **offline parity restored and independently reviewed; staging/operation pending**. The focused cohort passed **385 tests** (25 new entrypoint cases, 143 settings cases, 217 complete lift-operational cases), and scoped Ruff passed. Two motor-source files changed: `am1_live_teleop.py` SHA256 `e0e4673d6b531b0745c3074e7c114139063f9a2ef0281a59e8a5003621e4c9ac` and `lift_operational.py` SHA256 `9d9705ba09fe6ffd662e48f1a333038221c2c25ed9662dcf9dd206b04160b094`. Registered acquisition receipts prevent cached/retimed votes; startup warnings flush once through the existing writer. Synthetic continuations test classification and do not complete historical failed traces. The one-off fresh-context read check recovered policy, scope, deployment and next action from these active files. Existing Pi pins above are still unchanged. After the scoped correction is reviewed and staged through the existing stopped-owner process, perform the already defined Packet16 **172.5 active-second / 300-second ceiling** combined workload, planned input interruption/fresh rejoin, selected-view switch and separate brief next Start. Do not replace that qualification with another joint menu or simulation-only milestone.

Physical USB identity/stationary sampling and human leader ergonomics remain distinct from virtual-source evidence. Dock, battery and GPU/cloud arrangements remain plans unless separately verified as installed. Export [the dated Project snapshot](project-snapshot-2026-10-17.md) if needed; no ChatGPT Project file replacement is implied.
