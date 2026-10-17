# AM1 teleoperation consolidation — Packet16

Physical leaders are the primary input for ordinary teleoperation. An explicitly selected virtual-leader source substitutes at acquisition and uses the same mapping, authenticated transport and protected Pi-owned execution. The accepted elbow page presets remain useful diagnostic controls.

## Current status and source

PR24 was integrated normally into `integrate/am1-local-teleop` at `386d3c376c51d3431cf379af61ab99be95382ab8`, after the published tree matched the exercised product and retained checks/reviews. This follow-up starts at that actual tip on `codex/am1-teleop-consolidation-16` and remains unmerged. Main and AM2 are unchanged.

The shared input and selected-camera implementation is staged, but the representative combined workload and next Start remain unfinished. Current known-cleanup installation: motor `6328bb524bc4e42c70f9896d66904177c1830277`, runtime `68118e4e9dddf8dddaa3ea6cbf64f54fdd511980`, manifest `de681ad6f73f2639a1971b96eee41214b5edfdc1730082e7f77b78a3c6795dbb`, helper/manual `0fe3997f12b9cdfdd01fda6f4c6b102781252448`, and physical camera `bc3442b54d9c574a8feddb0ade642c9b351e59f9`. Immediate full matched rollback is motor `2fbc8265b15393dcc799e603223b8902844e2919`, the same runtime/camera, and manifest `1d64de8e1e2f05938a39fae7a67a9a3ff3b19f94ae456f311116c41e52bbbd57`. Older rollback bundles and all original results remain retained.

Combined09 never admitted Live. Its original 180-second startup ceiling ended with `physical_startup_timeout`; the protected host had continued normal acquisition while delivered feedback remained at observation 2. All original artifacts and full native history are saved. Cleanup verified all 20 off/zero readbacks, qualified lift, closed buses and camera release, with no uncertainty. This is a retained startup failure, not workload completion. A real in-process queue test reproduced observation-credit starvation at the measured-scale 78 ms host cadence: only two advancing replies across 120 polls, versus 120 with a 35 ms control. The reviewed AM1 client repair retains still-fresh credits across empty 40 ms polls and retires them only by their original one-second age, including stale cached replies. It sustained 80 advances across 120 slow-peer polls and passed 110 focused connection/cadence tests. Polling, the three-credit window, original feedback freshness and 250 ms input expiry are unchanged. This reproduces the mechanism, rather than reconstructing unrecorded token traffic from the live failure.


Combined10 also ended at the original startup ceiling, with stale observation 3, no Live and verified cleanup. A subsequent pure import check under the actual Pi owner's interpreter, working directory and import path identified the deployment error: the owner imports `alohamini_client.py` from its runtime checkout, still at original SHA `235f7a8a…`, while stage09 had placed reviewed SHA `9315af5b…` in the separate motor checkout. Stage10 deployed that same reviewed correction to the runtime. The actual owner interpreter/cwd/environment imported runtime SHA `9315af5b…`, and nine existing non-actuating request-accounting cases passed there in0.48s using the existing separate pure pytest dependencies. No production packages were installed. The failed trial is not evidence against code the owner did not load. All six original artifacts and full native history are retained.

Combined08's earlier thermal guard retained decoded left shoulder-pan temperature 126 against installed 70, with the other left-arm readings 31–37. Its actual wire packet was unavailable, so physical heat versus reply provenance remains unresolved. A later stopped comparison retained 360 valid packets without reproducing that active reading. A separate SDK probe proved that a short error reply could be misdecoded as temperature data. The staged AM1-only integrity guard validates original successful reply length, checksum, ID and servo error before SDK slicing, retains bounded fault-boundary packets, and fences later writes after the first fault. It passed 119 focused tests and two independent reviews. A legitimate high-temperature payload still trips the unchanged thermal protection; the probe does not prove the cause of Combined08.

Combined11 subsequently reached native Live with255 Core-accepted frames and24.44 active PC seconds. It ended on left-elbow temperature98 against configured70. The retained original ID3 reply `FF FF 03 03 00 62 97` has a valid checksum and zero device error. This proves the reported bytes, while physical heat, sensor behavior and request association remain unresolved. Other left-arm temperature reads were33–37. Later stopped measurements found elbow32, with236 baseline reads and210 group/individual calls; these occurred several minutes later and do not invalidate the original fault. All originals, full native history, verified off/zero cleanup and camera release are retained. The program, planned input gap/view switch and next Start remain unfinished. A narrow diagnostic extension retains and validates exact transmitted requests plus the previous successful health value with its original times/binding, without extra bus I/O or thermal-policy changes. It passed139 affected settings/native checks, including original write identity/return behavior and normal cleanup after request refusal; all ten stage11 idle staging phases subsequently passed, including the actual owner runtime import check.

Combined12 then stopped before Live on the original one-second lift post-home qualification. The first original grouped reply reported velocity+150 at position812; subsequent19 acquisitions were position814..815 and velocity0/±50 with healthy status/temperature. The existing readiness fallback has an exact first+100 exception, which made this series permanently ineligible despite its later stable candidate. This is a reported finite initial settling indication, not a decoding failure or proof of continuing displacement. All six originals, native history and verified cleanup are saved. The focused v5 correction uses the existing200-raw homing envelope for that first positive settling report, retaining subsequent50-raw stationary criteria, whole-phase geometry and the original deadline. Five affected lift modules passed469 tests with four existing Windows-only skips; all ten stage12 idle-only staging phases subsequently passed. No program or next-Start completion is credited.

Combined13 reached Live and186 Core-accepted frames/18.014 active PC seconds. The motor host reported no hard fault. After three qualified ordinary expiry holds, the PC refused the fourth through its local pause-count cap and issued ordinary Stop; Core recovery episodes remained0 and original run/reference/deadline remained fixed. Cleanup and all originals are verified. This identifies a PC recovery restriction: ordinary cause-specific expiry holds must retain their original finite qualification/deadline checks while the native three-episode budget continues to govern bounded recovery episodes. The reviewed PC-only correction removes that separate pause-count cap, preserves all cause/current-feedback/ACK/expiry/deadline checks and the Core three-episode limit, and reports failed attempts as observed pauses rather than qualified recoveries. The existing adapter suite passed67 tests in29.74s, including four ordinary same-run rejoins and refusal of a fourth actual Core recovery episode. It requires no Pi restaging. Full program, planned gap/view switch and next Start are still unfinished.

Combined14 used PC candidate `0a458a90` and unchanged stage12 components. It admitted180 Core frames and17.34 active producer seconds before the host stopped on shoulder-lift temperature74 versus installed70. The original valid TX requested address63/width1; the intact zero-error ID2 reply contains74. The same-binding prior successful read was34 about59ms earlier. Those facts do not establish physical heating, reply aliasing or corruption. The original native fault precedes Core feedback loss; the PC waited through the original bounded episode and refused the ensuing host fault. All originals, full history and verified20 off/zero cleanup are saved. No complete program, planned interruption, view switch or next Start is credited.

| Combined14 observation | Actual result |
| --- | --- |
| Source / native evidence |180 Core-accepted frames; final retained native source172 and171 unique feedback samples. Acceptance is not proof of every physical write. |
| Measured coarse movement |Left pan35 encoder ticks/about3.08 degrees; shoulder lift76 ticks/about6.68 degrees; elbow10 ticks/about0.88 degree. Other joints were held or their phases were unreached. |
| Final encoder residual to encoded accepted target |Pan+13, shoulder lift−31, elbow+1tick. These are neither goal-register readbacks nor fine-position qualification. |
| Same-run continuity |Two ordinary expiry holds qualified fresh rejoin. One later native recovery episode remained unclosed after the host fault. Original reference/workspace and300s deadline stayed fixed. |
| Body phase |Unreached; authored/native body commands stayed zero. Nonzero wheel-derived feedback under those commands is retained separately, without body-workload credit. |
| Onboard video |Chest WebRTC363 decoded frames and71 fresh health samples; no sampled freshness gap inside that useful interval. Planned wrist switch was unreached. Teardown reconnect is separate. |
| Timing p50 / p95 / maximum |PC grant RTT19.60/41.69/58.04ms; capture-to-send0.062/0.100/0.324ms; Pi observation RTT170.13/200.98/244.38ms; original joint read2.033/2.411/5.676ms. PC/Pi clocks are not subtracted. |

The prospective live-health hardening replaces six one-byte health transactions per arm with one exact33..70/38-byte SDK transaction per bound six-motor chain. The original per-bus read interval, status-first validation, settings and installed limits remain; a valid block reporting74 still faults. Distinct response width rejects unrelated short replies before field decoding, without retries or hot-sample suppression. It does not prove the cause of Combined14 or distinguish an arbitrary delayed reply of the same width/ID. The two affected modules passed160 tests in1.31s and independent review. A stopped read-only installed-firmware comparison retained20 transactions/120 valid response packets, correct SDK/wire offsets,20 off/zero checks, closed buses and unchanged complete history/private files. No powered success follows from that comparison. All ten actual motor-only stage13 phases passed, including actual owner runtime/client import and idle availability. Full matched predecessor motor/runtime bundles were archived before motor upload/checkout; no task started during staging. The supported combined trial remains next.

## Shared input and motor lessons

- Whole input frames contain exactly twelve calibrated arm/gripper targets plus the existing body/lift intent. The original acquisition/grant age, source identity/sequence and control generation survive transport to the native prewrite guard. A new grant cannot refresh an old sample.
- Raw feedback and original per-bus acquisition intervals remain separate from normalized targets. Calibration and direction define encoding; gripper opening, arm angle, wheel velocity and lift movement retain distinct units.
- A persistent vector cursor accumulates low-rate motion coherently. Actual Start pose and qualified clutch/rejoin prevent jumps without rewriting calibration offsets or requiring a historical twelve-joint fingerprint.
- Normal installed settings and real health/current/thermal/status protections remain role-specific. Elbow P16 evidence and its diagnostic500mA ceiling are not blanket tuning. Gripper contact, wheel velocity and lift home/relief retain their existing semantics.
- Routine logs and selected-video encoding use bounded workers outside the motor loop; required command receipts and faults remain explicit. Weak small-current-rise trends remain diagnostic where supported, while real device faults retain their first cause.

Packet14 demonstrated coarse6–8.3degree elbow movement for nominal10degree commands, with fine residual remaining. It did not prove household-task capability or justify a10/6 command scaling factor. Earlier experiments and their superseded restrictions remain in [Packet14](practical-controls-14.md), [Packet13](practical-motion-13.md) and the labeled historical [session report](roaming-resilient-sessions.md).

## Onboard viewing

One stopped format inventory found all five roles advertise MJPEG/YUYV, including640×480 at30fps, and no native H264 format. Installed go2rtc1.9.14 and ffmpeg7.1.5/libx264 serve one modest selected-camera encode shared by viewers, using the existing exclusive capture owner. Rotation/aspect ratio and required local JPEG acquisition metadata remain intact. Selected WebRTC viewing is the default; prior MJPEG viewing remains explicit rollback. Decoded progress, source age and capture/encode generations qualify visibility separately from control health. P1 remains advisory.

## Verification and ordinary use

Focused checks are recorded by source version: initial media61 Python checks/one existing skip and44 browser checks; affected existing modes208 checks; AM1 observation client110 local checks and9 actual-owner in-process checks; TX/RX settings/native139 checks; lift readiness469 checks/four existing platform skips; latest PC adapter67 checks. These overlapping cohorts are not a cumulative unique-test count or a physical qualification. Independent affected-source reviews passed before staging. The representative exercise uses virtual leader-format input, 172.5 active program seconds under a newly declared fixed300-second native ceiling, original250ms input freshness, individual then combined arm/gripper movements, and a separate arms-held existing raised-base body recipe. It includes one selected-view switch/reconnect, one controlled input interruption/fresh rejoin, and a brief next Start. Producer program completion and native interactive Stop are separate results.

Use the reviewed checkout, existing environment, enrolled browser state and trusted CA. Open the authenticated onboard viewer separately and explicitly select a fresh role. The viewer observes; the adapter owns input and Start/Stop. Every invocation requires a fresh private output directory.

```powershell
Set-Location 'C:/Users/pickm/.codex/worktrees/am1-reliability-01/lerobot_alohamini_client'
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = "$PWD/src;$PWD;$PWD/.cache/am1-session-pi-01/http-overlay"
$sessionArgs = @('--url', '<existing-HTTPS-origin>',
                 '--ca', '<trusted-CA-file>',
                 '--storage-state', '<existing-enrolled-state>')
uv run --no-sync --project C:/Users/pickm/lerobot_alohamini_client python -B -m tools.am1_leader_adapter `
  @sessionArgs --source virtual-leaders --duration-seconds 180 `
  --interrupt-at-seconds 60 --interruption-seconds 0.65 `
  --output '<fresh-private-combined-output>'
```

The virtual cap is active progress, with172.5s required for the complete declared program. Startup, holds, final-zero qualification and cleanup take additional wall time, within the original native ceiling. View selection/reconnection is a separate authenticated browser action; the adapter has no view-switch flag. A separate brief next Start, after supported completion and retained originals, uses:

```powershell
uv run --no-sync --project C:/Users/pickm/lerobot_alohamini_client python -B -m tools.am1_leader_adapter `
  @sessionArgs --source virtual-leaders --next-start-seconds 3 `
  --output '<fresh-private-next-start-output>'
```

For ordinary physical leaders, use only the existing verified left/right port identities and calibration. Do not guess port order or create calibration as part of a run:

```powershell
$leaderArgs = @('--left-port', '<verified-left-port>',
                '--right-port', '<verified-right-port>',
                '--leader-id', '<existing-leader-ID>',
                '--calibration-dir', '<existing-calibration-directory>')
uv run --no-sync --project C:/Users/pickm/lerobot_alohamini_client python -B -m tools.am1_leader_adapter `
  @sessionArgs @leaderArgs --source usb-leaders --duration-seconds 180 `
  --output '<fresh-private-ordinary-USB-output>'
```

The existing body keys are `w/s` for forward/back, `z/x` for lateral, `a/d` for turn and `u/j` for lift; `q` requests ordinary Stop. The accepted lowest speed is retained; `t/g` do not raise it. A five-second stationary shared-path check replaces duration with `--duration-seconds 5 --no-keyboard`. This still starts followers and homes the lift; it is not a sampling-only command. `--no-keyboard` zeros USB body/lift intent, while the explicit virtual source retains its declared body recipe. The existing USB source opens/calibrates read-only and samples without enabling leader torque; no physical C001 identity or movement proof is inferred from an empty inventory.

After USB connection/identity and stationary sampling are established, the prepared short human check is fresh clutch with still leaders, one modest supported out-and-back per arm and a free gripper opening, then ordinary Stop. Actual human ergonomics, grasping and task success remain separate. Manual follower positioning, an old reference pose or human leader motion does not unlock the current automated implementation work.

The current Windows USB inventory enumerates no serial ports. Physical USB identity/stationary sampling and actual human leader movement are therefore unverified. Virtual success will not be reported as moving-leader validation. After automated qualification, the remaining human check is a short fresh-clutched leader movement within the supported workspace; no object, floor travel, tuning or manual follower positioning is required by this packet.

AM1 stays wireless. Persistent service startup restores an idle interface and never starts or replays motor activity. Software durability here means continuity, state preservation and recovery; it does not mean mechanical lifespan. Internet telepresence, grasping, VR, autonomy and human ergonomics remain later task-level checks.
