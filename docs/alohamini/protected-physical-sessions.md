# AM1 protected physical sessions

AM1-PHYSICAL-ADAPTER-01 connects the Pi-owned authority to the existing protected
motor host. The implementation, focused review, native checks, camera-only
release check, exclusive stopped inspection and matched staging are complete.
The first actual physical startup failed the existing lift post-home stationary
qualification **before native Live or arm trajectory admission**. Physical
reliability is incomplete. Simulation remains the default.

Published for review in [draft PR #20](https://github.com/pickmanmike/lerobot_alohamini/pull/20),
explicitly dependent on accepted PR #19.

## Actual operating result

| Requested milestone | Actual result |
|---|---|
| Automatic startup from normal rest | Failed at `OperationalLift.start -> home_and_relieve -> qualify_stationary("post_home")`; original native cause: `ComparisonRefusal: post_home: stationary feedback exceeded the 1-second qualification limit.` |
| Small useful arm movement and return | Not admitted; zero trajectory seconds, no measured arm acquisitions or frozen finite reference. The requested ordinary Stop after the first joint's return segment was never reached. |
| Original 88-second profile; four cycles / 352 seconds within 420 Live seconds | Not run because startup failed. |
| Physical finite-task browser reattachment and same-run recovery | Not exercised. The administrative launch connection had already exited before the browser's Start; that demonstrates launch separation during startup/cleanup, not successful powered finite execution or recovery. |
| Comparable automatic normal-rest restart | Not run; no unchanged retry of the failed startup. |
| Original twelve-second body recipe | Not run; its required preceding physical qualifications were absent. |

Actual run: `303353c8-297c-4e24-86d3-d4bfb25f813a`, recipe
`physical-arm-smoke`, host incarnation `21b88d46-dafc-4434-8301-c990316b07e1`.
Terminal status is `faulted`, `uncertain=false`, with actual
`physical_readback_verified` cleanup. The authority's first observed cause
(host exited during startup) remains unchanged; its physical result and original
host receipt preserve the native cause and full exception chain. No Live origin,
Live deadline, synthetic pose or seed was manufactured.

The stopped inspection had verified the actual AM1 buses, cached calibration
against EEPROM, all sixteen torque registers off and four body velocity goals
zero. Present shoulder positions were ordinary calibrated normal rest. The
powered host then used the existing activation procedure. Homing ended by the
existing current-threshold criterion after 2.548212 seconds, at raw position 808
and peak 312 mA. That criterion is not independent proof of physical hard-stop
contact.

The following one-second post-home interval contained 21 advancing samples at
positions 808/809, confirmed torque 1 and goal velocity 0. Velocity alternated
between 0 and ±50; `Moving` intermittently reported 1. The longest consecutive
quiet stretch was three samples spanning approximately 0.104 seconds, below the
unchanged requirement of five samples over at least 0.2 seconds. Temperature stayed 34 C,
status was 0, and grouped replies were complete and checksum-valid. No missing
device, permission failure, camera expiry or omitted wrapper startup setting
was found. The original lift implementation is unchanged in the matched motor
projection. The source predicate correctly refuses the retained feedback. The
original host log SHA-256 is
`f6a643f0f5a67e537c414dce297bca4921d4fbd62e52b963d26c3846212c6fe1`;
the repeated rejection record is counted once rather than as a new servo read.

The practical obstruction is **qualifying the actual lift's post-home zero state
under the existing quiet-feedback contract**. The evidence does not identify a
firmware or mechanical cause, prove that every ±50 indication is harmless, or
justify changing gains, homing, calibration, tolerance or deadline. Further
lift-specific diagnosis is needed before another powered attempt. The current
packet's later physical milestones remain unproved.

## Actual cleanup and sensing

The protected host read back all sixteen `Torque_Enable` registers and all four
body/lift `Goal_Velocity` registers as zero before closing both buses. Lift
cleanup qualified five fresh samples over 0.208 seconds at raw position 747,
zero velocity/current, `Moving=0`, torque 0 and goal 0. The selected shoulder gain
trial had not reached application; no restoration remained pending. Cleanup
errors were empty. Host exit alone was not used as torque-off evidence.

The run's actual camera release receipt binds the exact run ID and records one
ordinary SIGTERM, backend exit 0, joined readers, closed viewer socket and removed
runtime configuration. Independent unit/process/listener/device checks confirmed
release. No in-progress or cleanup-unknown marker remained. The camera receipt
itself explicitly says it did not perform the separate device-fuser check.

Before motor staging, camera-only lease
`d1c933b8-c0c8-402b-814c-8602e9712924` supplied five advancing decoded 640×480
chest JPEGs over 0.431675 seconds, with maximum original Pi arrival age 39.5 ms.
One cooperative Stop released the resources 7.829 seconds before the immutable
deadline, with independent process/listener/device confirmation. An earlier
caller used an incorrect JPEG route and failed; its finite lease expired and
released independently. That failed caller result is retained separately.

Required input remains actual Pi local-camera evidence, validated against the
original 500 ms arrival-age, decode, source, generation and current-owned-run
rules. A new HTTP/cache read does not refresh an old image or motor sample.
Chest coverage is partial and does not establish hidden-wheel/full-arm clearance
or scene understanding. AM1 stayed wireless. P1 capture/viewing remained advisory;
its result cannot authorize or consume motor recovery.

Optional P1 run `dd9e0f82-42aa-41c7-bf1b-af19dc6c5260` finalized at its
original 240-second / 64 MiB budget. Later independent archive inspection verified
42,218,876 bytes, duration 238.4889 seconds, one video stream and zero audio
streams, with MP4 SHA-256
`7a85413c8cdce9148e72dbea9c635d882ace79e29d129e1e1b6fc104efa5d6cc`.
The same source incarnation reported normal Stop/success and release, with no
capture child before or after inspection. The advisory caller's original
`release-unconfirmed` / `ConnectionError` after 197 delivered frames remains a
failed caller result; later archive/source evidence does not rewrite it or claim
continuous delivery. No imagery was exported for publication or used as proof of
useful arm motion.

## Implementation and preserved protections

[PhysicalExecutor](../../tools/am1_physical_executor.py) is the sole local client
of the existing protected host; only that host accesses the servo buses. The
explicit live backend never inherits simulated IO. Default recipes and default
CLI construction remain simulated and cannot open physical devices.

The authority-facing methods use bounded mailboxes and cached evidence, while a
native worker performs startup, finite control and cleanup outside the authority
mutex. Accepted Start is distinct from actual Live admission. Startup retains
its 180-second ceiling; real provider preparation retains 20 seconds. Live is
established once from actual qualified feedback. Original recipe ceilings are
88/180, 352/420 and 12/30 trajectory/Live seconds.

Canonical physical admission and a distinct serial-owner lock precede device
effects across legacy, direct and new entrypoints. A durable private physical
in-progress claim survives an owner crash; unverified cleanup retains uncertainty
and blocks rearm. Private filesystem IPC binds run/incarnation, action sequence,
intent revision and control epoch. No raw motor endpoint is exposed to a browser.
The pre-existing canonical lock was tightened from 0664 to 0600 under both exclusive
stopped locks; calibration and its existing permissions were preserved.
Resident owner/gateway retain `PrivateDevices=yes`; only an explicitly accepted
Start creates finite hardware-owning units. No boot autoplay or service-restart
motion is added.

Requested targets, enqueue, actual host acceptance/epoch, servo write return,
measured feedback and visible motion remain separate evidence levels. Actual
acquisition and matching-request times must advance and remain fresh. The existing
measured arm hold/body zero, watchdog, local motion/current/thermal/status guards,
normal-rest coordinate handling, mapped shoulder amplitude and gain restoration
remain. Arm-only recipes keep body velocity zero; the body recipe uses its
ordinary gains and original W/A/U/J 200 ms press / 500 ms release sequence.

Recovery retains three aligned fresh samples over at least 0.2 seconds plus host
hold/resume acknowledgment. A completed qualified executor-acknowledged recovery
closes its episode; a later distinct loss may open another, at most three total,
each at most ten seconds, inside the original Live deadline. Explicit Pause/Stop
and intent fences retain precedence. Reconnection never homes, reseeds or renews
the deadline. These interfaces passed focused checks; actual powered recovery
remains unproved by the failed startup.

## Exact executable versions and verification

Accepted dependency is PR #19 head
`24fd800ca22022f5e11333e42f37d1d6e7b8c5f3`, with the unchanged explicit stack
#19 → #18 → #17 → #16. The new dependent branch is
`codex/am1-physical-session-adapter`; no prior PR is merged or retargeted.

| Component | Actual staged executable pin | Scope |
|---|---|---|
| Isolated owner/gateway/local worker | `f8ff831dd33b171c849f76f6dd5801796e9004df` | New protected executor; dependent source on accepted #19 |
| Normal motor host | `3da8766282ba342ebf8c969519b41ac15922ce00` | Five reviewed files projected on original `f03de9c3b8c4f1d9e69b6951584119113ca4c5da` |
| Normal Pi helper | `0fe3997f12b9cdfdd01fda6f4c6b102781252448` | Sole helper file projected on original `8e6a0cf616cb2000df1d0e996ab27a19d2fb2fba` |
| Isolated camera-only source | `a0e94bc5298c021f785eaaee4ef0e2c44283f3d7` | Cooperative release projected on `77c0dda01dd8bda3eddff4237fcf77e92c21af12` |
| Windows served console | `e55d390bf6e957b9adc52ccf62b69476db12a20d` | Unchanged source; private motor/helper pins matched during staging |
| Normal onboard camera / strict observer / P1 capture | `9b1f0670e7068f7d39eb50270a118e3807418355` / `7a266890703aae36f69a8d98e548d4c2e13710e5` / `2d9e365f1e302694633e7b1c916a38fb15cc50cc` | Unchanged separate sources |

Windows implementation used PowerShell 7, the managed reliability worktree,
exact candidate import roots and the existing shared `.venv` through
`uv run --no-sync --project C:/Users/pickm/lerobot_alohamini_client`.
Native motor execution used Bash and existing Python 3.12.13 at
`/home/pickmanmike/lerobot_alohamini/.venv/bin/python`; gateway/worker used existing
Python 3.13.5 in the isolated HTTP environment. No broad installation was made.
Physical source was isolated at `/home/pickmanmike/am1_physical_trial`; camera
source was separately isolated at `/home/pickmanmike/am1_physical_camera_trial`.

One focused changed-interface review found and corrected five issues: actual
owned-unit exit qualification, watchdog pause invalidating active ACK, durable
crash claim, current-camera run binding, and expected startup cancellation with
actual cleanup. Narrow correction checks then fixed pending-unit registration
and running-with-hold-ACK races. Only affected executable checks were rerun.

| Focused check | Verified result |
|---|---|
| Native affected lifecycle/host/camera cohort | 142 passed, 0 skipped, 16.61 s |
| Latest lifecycle race correction | Windows 129 passed, 15.24 s; native 42 passed, 0 skipped, 5.93 s |
| Actual body feedback/original request evidence addition | Windows 31 passed + 4 native IPC skips, 7.13 s; native 35 passed, 0 skipped, 6.53 s |
| Loaded physical UI with mocked hardware | 1 passed, 8.15 s; no physical Start dispatched |
| Existing simulation/local UI | 2 passed, 20.58 s |
| Private stopped staging/rollback helper | 15 offline checks passed; four wrappers passed native Bash syntax checks |
| True stopped bus inspection | 260 actual register reads + 16 pings in 0.345379 s; unchanged calibration/identity, all expected zero rows, both buses closed |

New-file Ruff/format, compilation and `git diff --check` passed. Native transport
checks used actual IPC with injected hardware; they are not physical-motion
evidence. Initial harness failures, red regressions, corrected outputs and actual
failed startup remain preserved privately. No full repository suite or new fault
campaign was run. Documentation-only publication does not trigger another suite.

## Ordinary use and component-specific rollback

The ordinary control path is the existing trusted HTTPS console on the Pi's
configured 8443 endpoint. An administrator generates one 60-second control pairing
code with `tools.am1_session_service pair --auth-state <existing-private-auth-dir>
--control`; the device enrolls through the console, explicitly Claims control,
selects an allowlisted recipe and clicks Start. Pause/Stop use that same
authenticated console. TLS verification stays enabled. Pairing, page opening,
Claim, browser attachment and service launch do not start hardware.

Physical mode additionally requires explicit `python -m tools.am1_pi_executor
--backend protected-physical --physical-config <reviewed-private-config>`, a
dedicated `--state`, the existing private `--observation-source` and
`--sensing-policy local-camera-required`, with the preserved mapped environment.
The reviewed private service configuration supplies these; omission of the
backend flag selects simulation. Do not use the incomplete
physical workload as a qualified operating recommendation. Secrets, private
configuration, auth state, detailed household images and source recording stay
outside Git.

Rollback is an explicit stopped operation after actual terminal cleanup and
independent owner/resource verification. It stops only the packet's three
resident units, preserves physical history separately, restores normal motor f03
and helper 8e as a matched pair, and restores the exact original private Windows
pin bytes before legacy use. The original c500 simulated services may then be
launched idle with their original history/config/import roots. This starts no
motor or camera task. Unknown cleanup refuses rollback/rearm; clearing a Boolean
is not reconciliation. The separate isolated camera projection and unchanged
normal camera/P1/strict sources remain separate. Calibration, TLS trust,
enrollments and original history are preserved.

**Actual closeout state:** this stopped rollback was executed after the verified
failed startup. Normal motor is again
`f03de9c3b8c4f1d9e69b6951584119113ca4c5da`, helper is again
`8e6a0cf616cb2000df1d0e996ab27a19d2fb2fba`, and the exact original Windows
private configuration bytes and ACL are restored. Accepted resident services
are back at `c500204946a56862bb36ada3518ba5f248c02fa7`, idle with their original
completed simulated history; no new simulated run was substituted. The physical
trial and failed physical history remain retained separately. A final independent
audit found both physical locks obtainable, no serial or camera-device owner,
no owned camera listener, no live physical unit/job, unchanged private/calibration
files and no unknown/in-progress marker. Required trust and enrolled devices remain.

Earlier successful simulated workloads and earlier partial/failed physical and
strict-P1 results remain at their original sources. This attempt supersedes none
of those verdicts and supplies no full physical reliability acceptance.
