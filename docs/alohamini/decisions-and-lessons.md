# AM1 decisions and lessons

Active source-backed index, recovered 2026-10-17 under owner **AM1-KNOWLEDGE-REUSE-18**. Start with [current state](current-state.md); procedures and full results stay in linked runbooks. Before reopening a symptom, retrieve its ID and compare the actual entrypoint, mode, role and deployed imports. A demonstrated mitigation can be accepted while its physical cause remains unknown.

**Evidence labels:** owner decisions specify intended behavior; measured results qualify only their recorded workload; implementation/tests establish their exercised path; proposed future work is not installed capability. Baseline source is `2647b77c558a36f2b07f5730cf7906b4022ba868` unless an immutable source below says otherwise. Packet18 supersedes proposed diagnostic-first Packet17 (not assumed executed); Packet15 wrist expansion remains superseded by Packet16. No historical failure becomes a pass.

Recovery inputs are retained privately in `.cache/am1-knowledge-reuse-18/`: owner packet SHA256 `ceb12c0b6cc40452a34f0dff0c93553b067145b08cffe7f6e50132016299ad85`; source-linked recovered review SHA256 `a801b6fb022325450eda2797be218919ea287524be63cd8bfe64064a0d588257`. The review is a starting-point analysis; implementation/results below link to retained sources.

## TEMP-01: confirm numeric temperature anomalies

- **Accepted / scope:** AM1 operational lift tolerates and records isolated numeric highs; confirmed heating, actual device faults and invalid/stale data remain actionable. Original rule: five fresh readings, 3/5 at or above 55 C, maximum age 0.5 s. Retained refinement: five occupied 100 ms slots, maximum and original time per slot. Rapid samples cannot add votes or erase a high. Cold-start and cleanup retain their specified distinct checks.
- **Decision/result:** [PR5 owner policy](https://github.com/pickmanmike/lerobot_alohamini/pull/5#issuecomment-5654998797), [implementation record](https://github.com/pickmanmike/lerobot_alohamini/pull/5#issuecomment-5655427206), [review](https://github.com/pickmanmike/lerobot_alohamini/pull/5#pullrequestreview-5192616898), [September acceptance][sep] and [exercised implementation][oldtemp]; accepted Local pass `fe12a28001b5ec17ebb84466a61169ef8b1d28a4` retained one 92 C report. Exact spike cause and spike-free raw data were explicitly retired as prerequisites for that bounded milestone; gradual heating/firmware Overheat evidence remains valid.
- **Implementation / tests:** [100 ms refinement at 0f9338462c](https://github.com/pickmanmike/lerobot_alohamini/blob/0f9338462ccce7e6ad31fa34c81fcc37f0227add/src/lerobot/robots/alohamini/lift_operational.py), current [TemperatureWindow](../../src/lerobot/robots/alohamini/lift_operational.py), [window/consumer tests](../../tests/robots/test_alohamini_lift_operational.py), [startup handoff](../../tests/robots/test_am1_startup_thermal_handoff.py) and [slot-boundary tests](../../tests/robots/test_am1_startup_thermal_slot_boundary.py). Keep original phase history; no manufactured cool baseline or repeated reset to avoid heat.
- **Current path:** Packet18 repaired the baseline follower raw guards, per-phase thermal resets and startup monitoring gaps. Shared confirmation now keeps independent bus/ID/run history through startup, Live, recovery and cleanup, using each frozen installed arm ceiling; recorded values are 70 C. This is current Packet18 authority, not historical arm numerical approval or permission to alter firmware limits. Lift retains 55 C and its specified cold-start rule. [Entrypoint regressions](../../tests/robots/test_am1_teleop_temperature.py) cover isolated highs, sustained heat, device errors and stale/misbound data; [settings tests](../../tests/robots/test_am1_teleop_settings.py) retain the actual SDK boundary. Initial parity passed 385 checks; subsequent startup due-sampling and pre-torque off/zero handoff corrections passed 455 and 264 affected checks. These cohorts overlap. Current motor/deployment and incomplete physical qualification are in [current state](current-state.md); Combined16–18 remain failed/stopped records, and none supplies workload completion.
- **Reopen:** sustained/corroborated heat, actual device error, materially different load/mode or demonstrated classifier failure. Another isolated number alone does not require unicast/group-read, firmware or wiring research. Missing/stale/duplicate data cannot create fresh votes; a synthetic continuation cannot complete Combined14/15.

## MOTOR-01: useful movement before micro-positioning

- **Accepted / scope:** useful ordinary elbow motion at nominal 10 degrees and approximately 5 degrees/s; stop stimulus escalation when the task-relevant behavior works. Exact endpoint return/sub-degree response is not an ordinary-use prerequisite.
- **Result/source:** [Packet14][p14], exercised `bea11a7925ef439eaa91da5c76a71f43fe557221`: both elbows sequentially moved approximately 6–8.3 degrees per direction at P16/I0/D32; no P24/P32 or optional 20-degree trial was needed. Fine residuals remain recorded.
- **Implementation/tests/status:** [native host](../../src/lerobot/robots/alohamini/alohamini_host.py), [ordinary presets](../../tests/robots/test_am1_practical_presets.py), [native controls](../../tests/robots/test_am1_practical_native.py). Legacy practical result accepted; whole-robot Packet16 outcome remains unqualified.
- **Reopen:** task-relevant response/accuracy deficiency in the actual new path. Do not infer exact target delivery, load-independent precision, a 10/6 multiplier or quantized leader gestures.

## MOTOR-02: retain role-specific settings and units

- **Accepted / scope:** C001 leaders, C018 followers, angle joints, gripper contact/opening, wheel velocity and lift home/relief retain their own semantics. Neither elbow P16 nor its provisional selected-elbow 500 mA host ceiling is universal. An abort ceiling is not commanded current or a continuous rating.
- **Result/source:** [Packet13][p13] and [Packet14][p14]; sampled successful selected-elbow peaks 52–65 mA do not prove that raising an older ceiling caused motion. Packet14 records actual firmware Protection_Current 310 = 2015 mA separately.
- **Implementation/tests/status:** [prepare_settings/verify_settings][live], [settings tests](../../tests/robots/test_am1_teleop_settings.py) and [shared path][live]. Current settings/calibration/mappings are preserved, not blanket retuned.
- **Reopen:** a demonstrated role-specific incompatibility, changed installed setting or task/load requirement with evidence; never extrapolate one joint's numeric setting to all motors.

## MOTOR-03: observe the full command and measurement chain

- **Accepted / scope:** distinguish requested target, transformed/limited target, accepted frame, stored Goal, measured position and visible motion. Keep raw feedback separate from command normalization; degrees, normalized units, counts, mA and mAh are different.
- **Result/source:** [Packet13][p13] corrected selected-mode normalization/hidden relative-target clamp; [Packet14][p14] separately records delivered target windows, encoders and P1 qualitative movement. [Packet16][p16] distinguishes Core frame acceptance from physical writes.
- **Implementation/tests/status:** [live mapping][live], [scripted amplitudes](../../tests/robots/test_am1_scripted_amplitudes.py), [selected feedback](../../tests/robots/test_am1_selected_joint_feedback.py). Existing fixes retained; later full-workload response remains to qualify.
- **Reopen:** an actual discrepancy in that chain or task-relevant positioning failure; elapsed time, browser cursor or accepted-message counts alone are insufficient motion evidence.

## START-01: accept actual supported normal rest

- **Accepted / scope:** keep fresh measured q0, fixed supported workspace and new-gesture cursor separate. Freeze the actual nonselected Goals within a run. New Start measures rest without requiring old encoder fingerprints; repeated Starts cannot ratchet the workspace.
- **Result/source:** [Packet14][p14] next right Start accepted actual left Goal2101 instead of the earlier2124 anchor; [Packet13][p13] used supported initial return when needed without manual positioning or deadline renewal.
- **Implementation/tests/status:** [normal-rest startup](../../tests/robots/test_am1_normal_rest_startup.py), [practical native](../../tests/robots/test_am1_practical_native.py), [live qualify_rest/workspace][live]. Practical next Start demonstrated; representative Packet16 next Start unfinished.
- **Reopen:** fresh rest outside the supported workspace/calibration, a jump, stale hold or drift beyond admitted bounds. Do not reseed away drift or rewrite calibration to force admission.

## START-02: preserve lift home, relief and readiness semantics

- **Accepted / scope:** retain actual home zero, relief away from the lower stop, physical direction and role-specific current interpretation. Small raw Moving/velocity flags are not automatically meaningful displacement; use whole-phase geometry, original timing and accepted readiness logic.
- **Result/source:** [September acceptance][sep] preserves actual-bottom home/relief. [Packet16][p16] Combined12 retained initial +150 followed by stable feedback; correction `1db2a5aa4870b8c069703009c09efc4ccd4f734b` reuses the existing 200-raw initial-settling envelope and subsequent 50-raw criteria within the original one-second qualification.
- **Implementation/tests/status:** [operational lift](../../src/lerobot/robots/alohamini/lift_operational.py), [lift feedback](../../src/lerobot/robots/alohamini/lift_motor_feedback.py), [post-home readiness](../../tests/robots/test_alohamini_lift_post_home_readiness.py). Recorded stage12 correction installed; it does not prove later whole-program completion.
- **Reopen:** demonstrated meaningful displacement, load/status fault or failure of that specific readiness model. Do not revive exact first+100 exceptions, extended bottom heating, ID/phase/gain commissioning or obsolete failed procedures.

## IO-01: optional output cannot block hardware work

- **Accepted / scope:** terminal display, routine logs, media encoding/decoding and browser waits stay outside timing-sensitive motor work. Preserve required receipts/faults, finite drain, cleanup ordering and the first cause if reporting also fails.
- **Result/source:** [September record][sep] reproduced terminal/tee backpressure; [Packet13][p13] separately retained a 392.011 ms routine log stall and fixed it at `2e1a165614daf0181189e9f889f367a768199c9b`. A later different boundary does not invalidate the earlier repair.
- **Implementation/tests/status:** [AM1RoutineRecordWriter](../../src/lerobot/robots/alohamini/alohamini_host.py), [blocked-writer tests](../../tests/robots/test_am1_live_native.py), [launcher/failed-sink tests](../../tests/robots/test_alohamini_postq.py), [shared encoder](../../tools/am1_onboard_media.py). Repairs retained in current path; no claim that every OS/storage stall is impossible.
- **Reopen:** measured blocked owner cadence, queue/sink failure or cleanup bypass at the actual boundary; preserve the original fault rather than replacing it with a secondary print error.

## COMM-01: the robot owns operation lifetime

- **Accepted / scope:** Pi owns operation identity/reference, execution, deadlines and local hardware. Browsers attach; viewing/admin SSH is not lifetime ownership. Distinguish viewer loss, input expiry, gateway restart, owner restart and required-sensing loss.
- **Decision/result:** [session architecture and disconnect rules][session], [protected operation results](protected-physical-sessions.md), [Packet16][p16]. Original 250 ms input expiry, ownership generations, latest-only targets, fresh intention after reconnect and no automatic reboot replay remain.
- **Implementation/tests/status:** [session core](../../tools/am1_session_core.py), [physical executor](../../tools/am1_physical_executor.py), [persistent-session tests](../../tests/robots/test_am1_persistent_session.py), [Pi runtime tests](../../tests/robots/test_am1_pi_session_runtime.py). Separately recorded simulated/physical scopes remain distinct.
- **Reopen:** a reproduced identity/deadline change, replay, ownership overlap or failed cause-specific recovery. Reconnect, Claim or idle service startup alone must not initiate motion.

## COMM-02: ordinary pause and ambiguous Start have distinct meanings

- **Accepted / scope:** ordinary input/view-expiry holds are not automatically recovery episodes. Qualified rejoin requires current feedback, cause-specific proof and executor ACK; retain the original run/reference/deadline and native recovery budget. Ambiguous HTTP outcomes keep the identical operation/body and uncertainty through the supported retry.
- **Result/source:** [Packet16][p16] Combined13 exposed a fourth-ordinary-pause PC cap with Core recovery count 0; `0a458a900aae2600be4099465920addda92aecf6` removes that extra cap. Published `2647b77c` corrects HTTP 503 unknown Start classification; a later refusal cannot erase earlier ambiguity, and matching accepted Core identity establishes ownership separately from native effect/cleanup.
- **Implementation/tests/status:** [one leader adapter](../../tools/am1_leader_adapter.py), [real-Core/HTTP/pause tests](../../tests/robots/test_am1_leader_adapter.py). PC fixes have recorded offline checks, no Pi restaging for these changes; full combined qualification pending. Combined18 exposed a distinct pre-Live entrypoint gap: WSS close exits admission into ordinary Stop instead of reattaching to the same accepted operation. Its initiating close cause is unproved. The independently reviewed correction permits one classified pre-Live reattachment, retaining original ownership/admission times through the actual Live handoff and the original ceiling, with no new Start/input or deadline reset. It passed 113 adapter checks and 33 exact-source boundary checks; the existing Live timing path remains unchanged.
- **Reopen:** a concrete ambiguous admission, unqualified ACK, wrong same-run binding, replay or violated native episode/deadline limit. Count observed pauses honestly; do not relabel failed attempts qualified recovery.

## COMM-03: an empty poll is not an expired request

- **Accepted / scope:** AM1 retains still-fresh observation credits across short empty polls, retires by original one-second age and rejects stale cached/late replies. Keep the three-credit window, polling, freshness and input expiry intact; AM2 behavior is separate.
- **Result/source:** [Packet16][p16] Combined09 reproduced starvation at measured-scale 78 ms cadence; the focused repair advanced 80/120 slow-peer polls. Combined10 still failed because the actual owner had not imported the changed copy.
- **Implementation/tests/status:** [client _poll_am1_available_message](../../src/lerobot/robots/alohamini/alohamini_client.py), [empty-poll/cadence tests](../../tests/robots/test_alohamini_client_connection.py). Corrected actual runtime import verified/staged in stage10; the failed earlier trial remains failed.
- **Reopen:** an actual fresh-credit starvation/late-reply association defect in the deployed entrypoint, not an unrelated timeout or code copy on disk.

## MEDIA-01: wireless AM1, local sensing and advisory P1

- **Accepted / scope:** AM1 remains wireless; no Ethernet prerequisite or PC roaming tuning. Task policy selects required sensing before Start. P1 engineering recording is temporary/advisory, not a permanent robot dependency or fallback for missing required local proof.
- **Decision/result:** [AM1-MOBILE-LOCAL-01][session] accepted real robot-local camera input with simulated motor IO; its separate strict P1 diagnostic remains failed. Later protected physical results have their own scope.
- **Implementation/tests/status:** [local observation](../../tools/am1_local_observation.py), [owned-session tests](../../tests/robots/test_am1_local_observation_owned_session.py), [delivery tests](../../tests/robots/test_am1_local_observation_delivery.py). Local required/advisory split retained; optional playback cannot grant control coverage.
- **Reopen:** missing/stale/replayed/undecodable required input or a materially changed task sensing need. Never silently downgrade an active policy or convert strict P1 failure into a pass.

## MEDIA-02: default onboard WebRTC has bounded evidence

- **Accepted / scope:** selected onboard WebRTC is primary, explicit MJPEG is rollback. One exclusive capture owner feeds one selected shared encoder; retain rotation/aspect ratio, local JPEG evidence, source/sequence/age and capture/encode generations.
- **Result/source:** [Packet16][p16] installed go2rtc 1.9.14/ffmpeg 7.1.5/libx264; Combined15 sampled 246 decoded frames/51 fresh points. This does not erase two Core view-lease expiries; planned wrist switch remains unqualified.
- **Implementation/tests/status:** [onboard proxy/encoder](../../tools/am1_onboard_media.py), [media tests](../../tests/robots/test_am1_onboard_media.py). Implemented/staged and partial physical playback observed; full combined view-switch/reconnect remains pending.
- **Reopen:** measured source-age/generation/lease failure, capture contention or blocked encoder. Playback progress, sensing freshness and control permission are separate facts; P1 video is not onboard migration proof.

## DEPLOY-01: verify actual imports and matched rollback

- **Accepted / scope:** inspect the actual service interpreter, cwd and import path before crediting a correction. Keep separate motor/runtime/camera/helper pins, compatible manifest and complete matched rollback; source publication, tests and installation are separate.
- **Result/source:** [Packet16][p16] Combined10 imported the old runtime copy although the corrected client was in the motor checkout. Stage10 verified the actual runtime import and nine non-actuating request-accounting cases; no new production packages were installed.
- **Implementation/tests/status:** [current deployment and rollback](current-state.md#published-tested-and-deployed-are-separate), private Packet16 publication/staging records; [lean launcher tests](../../tests/robots/test_am1_lean_launchers.py). Known installed identity is recorded, not inferred from this branch HEAD. Packet18's retained runtime has immediate chain `cdbd…` → `68118…` → full base `f8ff…`; full-context base and immediate parent are different facts. Both builder and native verifier must consume that exact chain/file closure. Failed partial staging is preserved under existing locks and requalified before a fresh retry; never overwrite old receipts or assume the staging bundle is still the old rollback bundle.
- **Reopen:** proven import/manifest mismatch or changed executable boundary. Avoid wholesale redeployment just to match SHAs, reenrollment, broad installers or powered checks for documentation-only changes.

## PROCESS-01: product control is leaders, then Quest, then tasks

- **Owner decision / scope:** Packet18 directs dependable leader-operated light-object manipulation and telepresence first, then Quest and concrete household autonomy. Virtual acquisition tests the shared real path; per-motor web buttons remain diagnostics.
- **Implementation/tests/status:** [Packet16 shared input][p16], [leader adapter](../../tools/am1_leader_adapter.py), [leader-format contract tests](../../tests/robots/test_am1_live_teleop_contract.py). Virtual-source evidence cannot qualify physical USB acquisition or human ergonomics; those remain separately unverified.
- **Reopen:** concrete task evidence changing product priorities or proving a shared-path gap. Do not substitute more per-joint menus or precision scores for the representative combined workload.

## PROCESS-02: one lean runner, focused informative checks

- **Owner decision / scope:** reuse one parameterized runner and actual payload fixtures; repair schema, workspace-ID, timing and supported recovery errors offline where possible. Automate authorized finite work and inspect changed executable boundaries.
- **Result/source:** [Packet13][p13] runner corrected delayed same-gesture continuation; [Packet14][p14] reused parameterized runner; [Packet16][p16] declares one 172.5-active-second program under a fixed 300-second ceiling. Do not rerun accepted historical checks solely to rediscover them.
- **Implementation/tests/status:** [one adapter](../../tools/am1_leader_adapter.py) and [adapter tests](../../tests/robots/test_am1_leader_adapter.py); private bounded runners preserve actual invocation evidence. Current whole-workload result pending, historical test counts not summed or presented as fresh passes.
- **Reopen:** a new executable boundary, failing check or unresolved material concern; ask only for an indispensable specific physical action outside existing authorization.

## EVIDENCE-01: retain success, first cause and limits

- **Accepted / scope:** preserve legacy mapping/combined-operation qualification, simulated results, actual new-path motion and failed attempts separately. A new-mode failure does not undo old mapping qualification; old-mode success does not prove new-path parity.
- **Sources/status:** [September acceptance][sep], [Packet14][p14], [Packet16][p16], [protected physical record](protected-physical-sessions.md). Combined14/15 remain stopped failures; exact wire bytes do not alone prove physical heat or reply causation. Known cleanup is not workload completion.
- **Reopen:** new contradictory evidence about the original claim. Preserve originals, original times and first cause; explicitly label synthetic traces and inferred explanations. Close a migration only after positive behavior and genuine-fault contrasts pass at its actual new entrypoint.

## FUTURE-01: roadmap does not describe installed equipment

- **Owner direction / scope:** retain Pi-local hardware authority and PC leader roles. Quest, concrete task fixtures/autonomy, later dock/battery arrangements and possible GPU/cloud roles are plans unless independently installed and verified.
- **Sources/status:** Packet18 product direction and recovered Project planning review, reconciled with [current state](current-state.md). The dated [snapshot export](project-snapshot-2026-10-17.md) is a provenance-stamped handoff, not evidence of ChatGPT Project replacement.
- **Reopen:** a new owner decision or actual installation record. Do not revive old purchase prices/schedules, canceled battery choices or stale August assembly/permission instructions as current facts.

## Immutable source index

[pr5]: https://github.com/pickmanmike/lerobot_alohamini/pull/5
[sep]: https://github.com/pickmanmike/lerobot_alohamini/blob/a0ffbb5820162584efe3118f858f1a8f6b759e08/docs/alohamini/alohamini.md
[oldtemp]: https://github.com/pickmanmike/lerobot_alohamini/blob/7badafdf4347cc1154c43f02fb6f6953d91053a0/src/lerobot/robots/alohamini/lift_operational.py
[live]: https://github.com/pickmanmike/lerobot_alohamini/blob/2647b77c558a36f2b07f5730cf7906b4022ba868/src/lerobot/robots/alohamini/am1_live_teleop.py
[p13]: https://github.com/pickmanmike/lerobot_alohamini/blob/2647b77c558a36f2b07f5730cf7906b4022ba868/docs/alohamini/practical-motion-13.md
[p14]: https://github.com/pickmanmike/lerobot_alohamini/blob/2647b77c558a36f2b07f5730cf7906b4022ba868/docs/alohamini/practical-controls-14.md
[p16]: https://github.com/pickmanmike/lerobot_alohamini/blob/2647b77c558a36f2b07f5730cf7906b4022ba868/docs/alohamini/teleop-consolidation-16.md
[session]: https://github.com/pickmanmike/lerobot_alohamini/blob/2647b77c558a36f2b07f5730cf7906b4022ba868/docs/alohamini/roaming-resilient-sessions.md
