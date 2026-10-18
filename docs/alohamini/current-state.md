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
| Installed motor | `89cc99b699084c525767e084b861431d02a56bf3` |
| Installed runtime | `30263a6c73ba8d76e6b17afc4a45c6a755754ac3` |
| Installed camera / helper-manual | `bc3442b54d9c574a8feddb0ade642c9b351e59f9` / `0fe3997f12b9cdfdd01fda6f4c6b102781252448` |
| Installed matched manifest | `fa1472a8790b579d7576ba6cccf3bebf15139279e78f0957fb6b48e92c231529`; all ten Stage25r1 stopped/idle phases passed |
| Immediate full matched predecessor | Motor `5a2e750b9c1f0778fad7c70fbd419a27ddfc9d79`, runtime `30263a6c73ba8d76e6b17afc4a45c6a755754ac3`, same camera/helper and manifest `48d4c7fc2fa2ff1227d35d3f296e3e89c1b9f42bc097d7151fb425d4c1c17fc7`; full bundles archived before Stage25r1 upload; older pairs retained. |

Deployment authority: [Packet16 source/deployment record](teleop-consolidation-16.md#current-status-and-source), corroborated by private `.cache/am1-teleop-consolidation-16/current-state-16-published.private.md`. That private checkpoint retains full matched bundles, exact staging records and permissions; do not copy credentials or private logs into Git. Verify actual service interpreter/cwd/import roots before changed code is credited as deployed. A rollback identity is not proof of a cure.

## Resume work here

Use `C:/Users/pickm/.codex/worktrees/am1-reliability-01/lerobot_alohamini_client`, branch `codex/am1-teleop-consolidation-16`, and `uv run --no-sync --project C:/Users/pickm/lerobot_alohamini_client`. Keep one coordinator for Git, staging and devices. The private operational index is `.cache/am1-knowledge-reuse-18/current-work.private.md`; the existing `.cache/am1-teleop-consolidation-16/current-state-16-published.private.md` is its publication/staging companion. Read their current row, not the historical hash diary. Neither contains a request to repeat consumed operations.

| Operation | Existing interface / locator |
| --- | --- |
| Pi roles | Administrative alias `am1-pi`; motor checkout `/home/pickmanmike/lerobot_alohamini`, persistent runtime `/home/pickmanmike/am1_physical_trial`, camera `/home/pickmanmike/am1_physical_camera_trial`. Owner uses motor venv plus runtime cwd/PYTHONPATH; verify actual client import, not just checkout HEAD. |
| Service status / idle proof | `ssh am1-pi 'systemctl --user is-active am1-pi01-owner am1-pi01-gateway am1-mobile01-local-worker'`; availability/cleanup/import qualification uses the exact `verify-existing-idle` argv retained in the current private stage command receipt. Service activity alone is not idle proof. |
| Reviewed staging | Existing `.cache/am1-knowledge-reuse-18/stage-inputs-25r1/stage10.py <phase> --inputs <reviewed-input-directory> --execute-reviewed-stage-phase` under the shared Python invocation. All ten phases completed; PC-only corrections need no Pi restaging. Exact manifests, full rollback and private bytes/ACL stay at the single deployment pointer above. |
| Operating / viewer | Existing `python -B -m tools.am1_leader_adapter` command in [ordinary use](teleop-consolidation-16.md#verification-and-ordinary-use). Private Packet16 index identifies the current invocation JSON, enrolled browser state and unchanged `viewer-N.cjs viewer-config-N.private.json` bundled-Node launch. The private Windows configuration remains at `C:/Users/pickm/lerobot_alohamini_client/.worktrees/am1-console-implementation/config/am1.session.json`; reuse it. |
| Collection / analysis | After producer exit, preserve exact invocation bytes and drain the viewer. Existing `retrieve-native-N.py --retrieve-after-trial --trial-directory <fresh-output> --audit-source <reviewed-audit> --audit-sha256 <actual-sha>` retains all six originals/full SQL; `analyze-teleop-run.py <run-folder> --output <fresh-analysis>` runs once. Current filenames/seals live only in the private current row. |
| Reconciliation | Existing owner/executor `reconcile` path in `tools/am1_physical_reconciliation.py`; it is not a standalone CLI. Historical sealed reconciliation drivers consumed their original requests and must not be replayed. Use fresh stopped evidence and separate current executor acknowledgment only when uncertainty actually requires it; current cleanup is already qualified. |

Latest completed transaction is Stage25r1 at the deployment pointer above. At this recorded checkpoint the latest operating attempt is stopped and fully collected; no producer, viewer or staging transaction remains active. The next permissible action is fresh idle/import/history proof and declaration of the reviewed PC correction, then the same combined workload and supported separate next Start. The private current row records live activity; update it when a run starts so another coordinator cannot duplicate it. The fixed172.5-active/300-native program, original input expiry and local protections remain; no temporary diagnostic allowance is added. Wireless/P1-advisory/AM2 scope and the no-gain/no-network/no-manual-pose boundaries above continue. Service startup restores idle without replaying work.

## Accepted capabilities and limits

- September Local operation accepted combined arm/base/lift controls, actual-bottom homing and relief; an isolated 92 C numeric lift report was retained/tolerated. [TEMP-01](decisions-and-lessons.md#temp-01-confirm-numeric-temperature-anomalies) preserves the decision and its role-specific limits.
- Packet14 physically demonstrated ordinary sequential control of either elbow, useful approximately 6–8.3 degree movement per nominal 10 degree command, ordinary re-jog, same-session browser return and a normal-rest next Start. Fine convergence, simultaneous all-joint control and long-duration operation were not demonstrated by those checks.
- Current input and selected onboard WebRTC paths are implemented/staged. Combined28 records real chest/wrist playback and a selected switch in a partial run; complete combined-system operation remains unqualified. Actual local sensing is task-selected; P1 cannot substitute for missing required evidence.
- Combined15 remains a failed partial run: 116 Core-accepted frames, 11.002 active producer seconds, two qualified view-expiry rejoins, then a valid high-temperature reply and host fault. All 20 off/zero readbacks, lift qualification, closed buses and camera release were verified. Cleanup is not program completion.
- Earlier PC-only admission/reconnection repairs retain their recorded adapter checks. They changed no installed Pi pins; historical test cohorts and physical outcomes stay with their source versions.

## Current unfinished work and next action

Packet18 supersedes proposed diagnostic-first Packet17. TEMP-01 parity is installed: isolated numeric highs are logged/tolerated using continuous independent slot history and frozen installed follower ceilings. Genuine device faults, confirmed heat and stale/invalid feedback still fault. Combined19 retained a tolerated 57 C lift outlier; Combined20 retained a tolerated 125 C follower outlier. Exact spike causation is no longer a prerequisite.

Reviewed logging and thermal acquisition-order repairs are installed at the motor pin above. Complete settings remain lossless within the unchanged 32768-byte record bound; original five slot maxima/times and 0.5 s age bound remain. The pause-acknowledgment repair passed 161 affected executor/Core checks and is installed at the runtime pin above after all ten Stage21 phases. Component source projections differ from the PR source commit; service imports and matched manifests establish actual deployment.

Earlier partial runs and their source-specific repairs remain in [Packet16](teleop-consolidation-16.md#active-continuation--packet18-2026-10-17). Accepted thermal parity, lossless bounded logs, original-owner expiry handling, normal-rest startup and role-specific settings remain installed. Their historical outcomes are not revised.

Earlier source-specific partial runs and repairs remain in [Packet16](teleop-consolidation-16.md#active-continuation--packet18-2026-10-17). The bounded retry-acquisition repair is installed after all ten Stage25r1 phases; its29focused/599affected checks and8existing skips belong to that source. The original CRLF-copy staging refusal remains intact; corrected byte-preserving evidence reused the same build and passed the guard. Later PC-only connection/release/Resume repairs do not alter the installed Pi pins or retroactively complete earlier runs.

Combined30/31 remain valid startup refusals; Combined32 passed every unchanged fresh startup guard from actual704 rest, then ended at a new ordinary pause during accepted Resume. The reviewed PC-only correction at source255ee362 passes274adapter checks/one existing warning and42focused checks, preserves original first-read/native/caller/episode ceilings, and was physically exercised twice in Combined33: each interrupted transition freshly qualified and reached actual active ACK before new input.

**Latest partial run: Combined33.** It reached640acceptedframes/60.465active seconds and qualified the planned brief input interruption/reattachment. Native elapsed103.792s is separate; there were26ordinary pause observations and zero native episodes. A later Resume explicitly refused because the selected view had not advanced between qualification and command admission. No Resume effect is credited for that refusal. Native normally stopped without first causes, verified cleanup/uncertaintyfalse and all20offzero/lift/buses/camera qualification. All six originals/full SQL/strict audit226mutations/107operations/1043transitions and matching local image remain private; original run/reference/workspace/300s deadline are fixed. Individual/combined/body completion, selected switch and next Start remain unfinished in this attempt.

The exact nonadmission correction passes304affected adapter checks/one existing warning and30focused checks, with no new lint findings. It requalifies fresh authenticated owner/view proof at the SAME binding/revision/ordinary pause, within one original8s held/view phase and the original native/caller/episode budgets; each new Resume has a distinct operation identity. It retains the first refusal if later qualification or command fails. Accepted-Resume interruption remains a separate path, and broad refusals/ambiguity still fail closed. **Remaining qualification:** exercise this reviewed PC correction in the full operating program. **Next physical behavior:** repeat the original172.5active-second combined workload with its planned gap and selected view switch, then the separately supported3s next Start after full qualification. Keep existing runner/stager/environment/private configuration and installed Pi components; no source rebuild, byte-copy repair, historical review or cold-read check is repeated merely for this continuation.

All original successes/failures and source-specific test cohorts remain in [Packet16](teleop-consolidation-16.md) and private immutable originals. In particular, Combined18/19's original missing/unknown receipts remain unchanged; later genuine stopped proof and separate durable acknowledgment cleared current uncertainty. Known cleanup is not program completion. The one fresh-context read check is complete; do not repeat it.

Physical USB identity/stationary sampling and human leader ergonomics remain distinct from virtual-source evidence. Dock, battery and GPU/cloud arrangements remain plans unless separately verified. [The dated Project snapshot](project-snapshot-2026-10-17.md) is a repo export; no ChatGPT Project replacement is claimed.
