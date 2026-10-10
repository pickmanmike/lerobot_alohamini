# AM1 protected physical adapter implementation plan

> **For agentic workers:** Use the focused TDD workflow and one final changed-interface review. The owner explicitly authorizes immediate execution; no additional planning approval gate applies.

**Goal:** Run the existing finite AM1 providers through the protected motor host under Pi-owned authority, with actual local camera evidence and truthful startup, motion and cleanup.

**Architecture:** Keep simulated execution unchanged and the default. Add an explicitly selected physical executor whose authority-facing methods use a bounded mailbox/cache; its sole native worker owns a local AlohaMiniClient and launches the existing protected serial host. Physical canonical admission precedes host creation, while private IPC transport binds the run, host incarnation, epoch and intent revision.

**Tech stack:** Python, existing ZeroMQ client/host, Unix admission locks, current authenticated HTTPS/WSS, transient Pi user services, existing exclusive camera viewer.

**Spec:** User-authorized AM1-PHYSICAL-ADAPTER-01; original private packet retained in `.cache/am1-physical-adapter-01/packet.txt`.

## Global constraints

- AM1 wireless; P1 advisory. Simulation cannot select or access physical devices.
- Preserve separate deployed source pins, private config/trust, calibration, normal-rest activation, local protections and AM2.
- Physical startup at most 180 seconds; provider preparation remains 20 seconds; ArmSmokeRepeat is 352 trajectory seconds inside original 420 Live seconds.
- Establish Live once from actual qualified native host feedback; recoveries never renew it. At most three episodes, each at most ten seconds.
- No forced powered faults, gain sweep, tolerance widening, manual leaders, shoulder placement, Wi-Fi tuning or physical replay on restart.
- No merge, retarget or force-push of the existing #19 -> #18 -> #17 -> #16 stack; publish a dependent draft PR.

## Review focus

- Cancellation during startup must fence later dispatch while status/observation stay responsive.
- An advancing HTTP/cache read cannot replace an advancing real host acquisition identity or action acknowledgment.
- A stale run/incarnation/revision/epoch cannot authorize resume.
- Process exit alone cannot certify torque-off; partial cleanup must retain uncertainty and block rearm.
- Camera source expiration/reuse and repeated shutdown signals cannot race required finishing or falsely acknowledge resource release.

## Task 1: Protected host boundary

Files: `alohamini_host.py`, `config_alohamini.py`, `alohamini.py`, `run_am1_host.sh`, client endpoint selection and legacy supervisor inherited admission FD, focused host tests.

Interfaces: protected host flags bind a private run directory, run UUID, host UUID, inherited canonical admission FD and cleanup receipt. Command markers retain version/mode/epoch and add exact run/incarnation/intent revision. Feedback adds actual acquisition time and acknowledged binding. Cleanup reads arm/base torque and body velocity goals before buses close, retains lift/gain evidence and latches unknown cleanup.

- [x] Write and observe failing ownership/private transport/stale binding/cleanup readback tests.
- [x] Implement canonical admission before construction, distinct serial lock, scoped filesystem IPC, real feedback and measured cleanup receipt; preserve default AM2/legacy behavior.
- [x] Run only affected host/client/launcher/cleanup checks, then record exact results and commit.

## Task 2: Async physical lifecycle and finite provider adapter

Files: `tools/am1_physical_executor.py`, `am1_session_core.py`, `am1_pi_executor.py`, `am1_session_contract.py`, `am1_finite_task.py`, focused physical/authority/provider tests.

Interfaces: `asynchronous=True`; `bind_run(run_id, recipe)`, `set_intent_revision(revision)`, `begin`, `dispatch`, `hold`, `advance`, `finish` are mailbox/cache-only. Evidence carries exact lifecycle `{run_id,phase,startup_deadline,native_live_at,cleanup,uncertain,terminal_status}`. Finish returns pending until real host readback receipt. Real recipes have seed=None and actual measured reference/provenance. Native worker cadence is independent of authority/database/web traffic.

- [x] Observe failing delayed-start/dedup/cancel, truthful Live, partial cleanup/restart, stale ACK and local transport mock-hardware tests.
- [x] Implement explicit allowlisted physical recipes and actual client/host supervisor, fresh native acquisition, measured hold, bounded resume handshake, normal body mapping and actual cleanup.
- [x] Preserve original sim paths; run focused executor/authority/provider/transport checks and commit.

## Task 3: Owned camera release

Files: existing camera viewer/launcher and focused lifecycle tests.

Interfaces: fresh per-run owned UUID and immutable finite source budget (maximum 900 seconds); cooperative signal release and private actual resource receipt; no expired lease reuse. Keep required capture through physical finishing, then stop once. Service fallback uses KillMode=mixed.

- [x] Reproduce second-signal cleanup interruption in a focused test.
- [x] Implement idempotent cooperative cleanup and owned finite lease/receipt without new camera ownership.
- [x] Run affected tests; after review stage camera-only source and exercise actual release once without motors, including independent process/listener/device checks.

## Task 4: Review, stopped staging and progressive execution

- [x] One focused changed-interface review, evidence-driven corrections and final affected executable checks.
- [ ] Verify actual stopped owners, clean exact source pins/environment/calibration; perform exclusive true bus reads without configuring connect.
- [ ] Stage only affected reviewed components; resident service idle and gateway without device access. Preserve explicit stopped rollback.
- [ ] Small bounded ordinary profile/actual response, then 88-second ArmSmoke with arms-only zero body/lift, real return/cleanup.
- [ ] Full four-cycle 352/420 workload, brief browser detach/reattach and launch-SSH-independent lifetime; natural recoveries only.
- [ ] Ordinary finish/natural rest, one comparable automatic full run, conditional original twelve-second ArmHoldBody W/A/U/J press/release with separately declared body gains.
- [ ] Preserve first failed layer and repair before another supported attempt; publish actual results, dependent draft PR, private evidence indices, authenticated use path and component-specific rollback.

## Verification checkpoint

Focused implementation and the five changed-interface review corrections passed the final native cohort: 142 passed, no skips. The corrected camera-only run qualified five advancing actual images (maximum original arrival age 39.5 ms) and one ordinary stop produced a clean release acknowledgment before its finite deadline. Independent process, listener and device audits found no remaining owner. The first caller route failure remains in private evidence. Motor device reads and powered operation have not begun at this checkpoint.

A narrow review follow-up caught and corrected pending-unit launch/Stop ordering and an already acknowledged watchdog hold between authority ticks. Their failing regressions were observed, then 129 affected Windows checks and 42 native Pi checks passed. The original session/recovery deadlines remain unchanged.
