# AM1 protected physical sessions

AM1-MOTION-QUALITY-01 is complete through its evidence-backed limitation path.
Two bounded left-elbow comparisons show that the live servo goal changes correctly,
but the sampled position does not move. Longer holds do not improve the response.
The selected-profile completion defect is repaired; useful elbow movement remains
unqualified in that recorded comparison. The owner has since completed the
requested power-off inspection and found no apparent issue. The AM1-ELBOW-RESPONSE-02
signed response continuation below is in progress. The reviewed candidate is available
idle through the existing opt-in physical route, with verified cleanup.

Durability means software fault tolerance, connection and control continuity,
state preservation, and recovery with minimal human intervention. Physical wear
and mechanical lifespan were not assessed. The accepted 88/352/352/body12 continuity
milestones remain achieved at their recorded sources; they were not rerun unchanged.
Simulation remains the default, AM1 stays wireless and P1 remains advisory.

This continues [draft PR #20](https://github.com/pickmanmike/lerobot_alohamini/pull/20),
dependent on #19 -> #18 -> #17 -> #16 without merge, retarget or history rewrite.
Required observation remains actual Pi-local camera evidence. Browser/administrative
launch connections do not own execution or renew the deadline. Older checkpoint
and rollback results below are historical; the following section records the
current deployment and the new evidence.

## Elbow response continuation, 2026-10-11 (in progress)

The owner completed the requested power-off elbow inspection and reports no apparent
issue. That action is satisfied; it is not a pending mechanical-inspection request.
The owner also reports successful elbow motion elsewhere. Independently retained
August20 native evidence confirms left elbow raw2155→2260 (+105 ticks) for a
requested +10, normalized response +9.507/final error +0.493. It used a lower
starting pose, a five-second ramp at5Hz and current up to13mA; it proves one outward
movement rather than repeated outward/return tracking. Its exact deployed SHA,
live gain readbacks and original wire/acquisition bytes were not retained. The
historical calibration range864..3073/homing1845 agrees with the current mapping.

The current packet authorizes a separate signed finite characterization with one
frozen fresh reference, other eleven targets held, body zero, at most ±10 normalized
units, slope at most one normalized unit per second and the stricter existing
raw/calibration/relative/current/clearance guards. The candidate tests −3 twice
first; only locally qualified return and nonadverse original feedback permit the
+10 positive comparison. Original18/24 recipes and their failed motion verdicts
remain unchanged. Command acceptance, program completion, original envelope
qualification and actual useful movement remain distinct results.

Current external P1 capture failed certificate-expiry validation before Start;
no P1 frames or motor effects occurred through that attempt. No trust exception
was used. A later finite camera-only check obtained and inspected actual fresh
chest, forward, backward, left-wrist and right-wrist pixels. None contains a useful
view of the selected elbow linkage. They are current images, not visual motion
confirmation. The five distinct required chest samples had maximum upstream-arrival
age53ms, and the original30-second source lease ended normally with all checked
video/serial devices unowned. Required local-camera authority and advisory P1 remain
separate.

The first new signed physical run, `d85018ee-3dd6-45fe-b66a-b9288644b04b`, used
motor `d957011d4090b0264df21dd2e90d07f7e185b664` and executor
`286566f46636c04a447acd28ec53f9d33ff59d19`, with unchanged calibration/settings.
Ordinary normal-rest startup succeeded. Its fresh frozen elbow reference was
raw2472 / normalized45.586238, a higher starting pose than both the old +3 tests
and the historical +10 positive control. Other eleven joint targets stayed frozen;
body/lift commands stayed zero after startup. It requested q0−3 with three-second
outward/return ramps and half-second holds through the sole protected owner.

| Actual phase | Encoded/live goal | Measured position | Current/load | Result |
|---|---:|---:|---|---|
| Baseline | 2472 | 2472 | 0mA | 61 native baseline samples, raw range0 |
| Negative endpoint | 2438 | 2453 | 19.5mA /72 | 34-tick command, 19-tick outward response; threshold17 |
| Original return | 2472 | 2453 | 26mA /−88 | Zero measured reversal, 19-tick residual; return band2 |

All three sparse native ID3 replies match their recorded44-byte packets and
checksums. Torque1, position mode0, status0, voltage11.8V and temperature30C do not
establish shaft torque or hidden mechanical condition. There are277 unique selected
measurements. The endpoint has16 measurements spanning.503027s; all93 return
measurements and15 origin measurements stay at2453. No second negative cycle or
+10 expansion occurred.

The run faulted at8.9 trajectory seconds, one admission, zero qualified cycles,
zero final returns and zero recoveries. Its retained original first cause is
`ValueError: signed cycle lacks three distinct returned acquisitions`; known
`physical_readback_verified` cleanup has uncertaintyfalse, all20 motor/body
readbacks zero, buses closed, lift cleanup qualified and camera normally released.
Program completion, envelope qualification and useful repeated response all failed.
The outward response alone passes the predeclared response threshold.

This exposed a separate software seam: distinct native feedback arriving between
10Hz trajectory steps was discarded, and qualification selected only the latest
three retained rows. Native acquisitions remain near30Hz; a representative original
three-row bracket spans.198052s current/.199007s position, just below the existing.2s
rule. Exact internal worker membership was not serialized, so that bracket is a
comparison rather than a claimed internal trace. The correction collects each
distinct original native acquisition independently of unchanged10Hz trajectory
advancement, then qualifies a bounded recent suffix spanning at least.2s in BOTH
original clocks. The original.5s freshness, deadline, seed, limits and two-tick raw
return band remain. Intermediate equal-current plateaus do not conceal a sustained
rise; genuine current decreases break that trend. The first preparing-action reply
can wait only inside the original first.1s interval before initial selected proof;
progress and deadline do not reset.

Offline replay of the complete saved selected evidence now qualifies eight actual
fresh origin acquisitions over.233s and refuses the measured19-tick nonreturn.
The other eleven joints are held mock inputs in that replay, not new physical
feedback. The repair corrects feedback qualification; it does not repair or relabel
the actual motion failure. No fresh physical rerun, larger departure, calibration
change or I1 setting has followed the failed return.

## Motion-quality result, 2026-10-11

### Target, live register and measured response

The verified mapping is left follower elbow, left bus ID3, STS3215,
`range_m100_100`, calibration range 864..3073, drive mode 0 and homing offset 1845.
Each selected run freezes its fresh measured reference for both repetitions:
normalized 38.9769126301494 / raw 2399. Requested +3 gives normalized
41.9769126301494; the existing encoder produces raw 2432 / bytes `[128, 9]`,
which normalizes to 41.964689904934346. Return raw 2399 uses bytes `[95, 9]`.
Other eleven joint targets stay at the acquired vector; body commands remain zero
after ordinary normal-rest startup. No gains, calibration, Phase, excursion,
rate, current or torque ceilings were changed.

The original comparison uses 3-second outward/return ramps and 0.5-second
endpoint/origin holds. The separately labelled dwell comparison extends only
those holds to two seconds. Each has two cycles, one final qualification against
the original return envelope, and an unchanged 60-second Live deadline. Native
position acquisitions and provider progress retain their distinct clocks.

| Run / tested executor | Program / original envelope | Measured motion | Recovery / cleanup |
|---|---|---|---|
| First selected18 `3a16b482-1e20-4092-ab42-8621d44cd3f0` / `82b7a2a487437701d1e286c0b6322bf5bcd86e51` | Completed 18 / two cycles / one final qualified return | 563 unique positions, all raw 2399; baseline 61 samples/range 0; maximum acquisition gap 378.037 ms | Three completion-boundary proof-loss episodes; actual input loss was not demonstrated. Verified cleanup, uncertainty false; original first cause retained. |
| Corrected selected18 `4f3a3516-b6db-4651-b2d3-12ee5d1a235d` / `b8558888ed43cbf5cc8cbf6bfaf493ccc21602fe` | Completed 18 / two cycles / one final qualified return | 553 unique positions, all raw 2399; baseline 60/range 0; acquisition span 18.479854 s, maximum gap 54.999 ms | Zero episodes, no outer/native first cause; verified cleanup, uncertainty false. |
| Dwell24 `a188f2dd-a591-45ff-aa74-2dc8aaa07c29` / `b8558888ed43cbf5cc8cbf6bfaf493ccc21602fe` | Completed 24 / two cycles / one final qualified return | 737 unique positions, all raw 2399; baseline 61/range 0; acquisition span 24.611119 s, maximum gap 51.009 ms | Zero episodes, no outer/native first cause; verified cleanup, uncertainty false. Final completed state 30.423700 s after Live, within the original 60 s. |

Five sparse grouped reads per run, performed by the same existing motor owner,
retain original request/reply bytes, checksums and native acquisition timestamps.
In every run both endpoint readbacks contain actual Goal_Position 2432 and present
position 2399; both origins contain goal/present 2399. Broadcast send acceptance
is not an individual servo ACK. Corrected18 reads took 1.433..1.517 ms;
dwell24 reads took 1.394..1.546 ms. These were five observations rather than
continuous full-register polling; no competing serial connection was opened.

All ten corrected18/dwell packets are valid, torque enabled 1, position mode 0,
torque limit 1000, status/error 0, voltage 11.8 V and temperature 30 C.
Corrected18 endpoint current was 52 mA with load -140/-144; dwell endpoints were
58.5/52 mA with load -140/-140. Endpoint Moving was 1 while measured position and
velocity stayed unchanged. Origin current/load returned to zero, although sparse
origin Moving still read 1. Electrical response and Moving do not establish
output torque, linkage motion, a mechanical obstruction or an encoder defect.
The prior stopped P16/I0/D32, deadzone 1/1, startup force 16 and torque/max 1000
settings did not demonstrate configuration drift; they were not changed or
repeatedly swept.

The useful-response rule was declared before physical operation: each cycle
must leave the original raw reference in the commanded direction and reverse by
at least `max(4, ceil(0.5 * encoded excursion), 3 * baseline range)` ticks;
final residual must be at most `max(2, 3 * baseline range)`. Missing phases or
fewer than three baseline acquisitions are inconclusive. For these 33-tick
commands the required outward and return reduction are each 17 ticks, with
residual at most two. This is reporting, not a change to historical control or
return-envelope acceptance.

| Comparison | Cycle | Commanded ticks | Sampled outward / return reduction | Final residual | Useful repeated response |
|---|---:|---:|---|---:|---|
| Corrected18 | 1 and 2 | 33 | 0 / 0 ticks | 0 | Failed both cycles |
| Dwell24 | 1 and 2 | 33 | 0 / 0 ticks | 0 | Failed both cycles |

Corrected18 has 15 samples in each endpoint and origin hold; dwell24 has 60 in
each, spanning 1.971..1.975 seconds. Ramps are also covered with advancing native
acquisitions. No response threshold crossing was observed, so response delay is
unestablished; motion between acquisitions remains unobserved. Being inside the
seed envelope without leaving it is not outward-and-return movement. Longer
dwell did not fix delayed response.

### Demonstrated repair and remaining limitation

The first selected run exposed a completion dispatch defect: the new recipe
names were assigned zero expected cycles, so their truthful two-cycle completion
receipt was refused and ordinary hold subsequently lost its active proof.
Commit `64052bc5a36289aaf50a27c42db35ede65fe7477` maps only the two exact selected
recipe names to two cycles/one final return. Original input/fault, identity,
incarnation, revision, executor-ACK, sequence, finite-reference, measured-return,
admission and deadline guards remain. Original ArmSmoke/Repeat rules are unchanged.
The original three episodes remain recorded. The two new actual runs complete
with zero episodes; this demonstrates the corrected completion boundary rather
than genuine required-input-loss recovery or improved elbow response.

A separate pre-Live attempt `66fc4cbb-46ae-4854-8420-6b0f0d9670cf` remains failed:
outer cause `ValueError: protected host feedback marker missing`, native first
cause `ComparisonRefusal: temperature: five-slot feedback window is stale (>0.5 s).`
There was no Live, trajectory admission or recovery episode; cleanup qualified.
Original timestamps show a 133.987 ms startup handoff skipping one temperature
slot, then a retained peak aged to 517.997 ms. The older successful handoff took
85.020 ms. The unchanged guard correctly refused the retained peak even though
the latest raw read was fresh. One bounded unchanged retry tested the timing/slot
hypothesis and succeeded as 4f3. No thermal guard, slot age or deadline was relaxed.
The exact startup substage is untimed and timing sensitivity remains unresolved;
this is not a claim that transient startup refusal is cured.

Correct mapping, encoded commands, native writes and live goals now have actual
evidence. Static advancing native position rows and five same-owner register
readbacks do not support a mapping, clipping, omitted-write or stale-browser
repair. The remaining response limitation is **downstream of the correct servo
goal-register update**. Binding, loose coupling, inadequate output torque,
internal servo behavior and measurement fault are not distinguished by this
record. No speculative tuning or forced movement is justified.

The original motion-quality closeout requested: **with motor power off, inspect the left
elbow linkage and servo-horn/coupling for interference or loose attachment;
report what is found without forcing movement or changing settings.** No manual
leader or shoulder placement is requested. This packet has completed its
available software, bounded comparison, review and reporting work; useful elbow
movement remains unqualified pending that specific physical evidence.

Other saved weak segments remain separate unknowns: repeated right shoulder
lift and left wrist flex, and the later left-pan seed offset. Their saved position
limitations do not contain the selected live-goal/current chain needed to establish
this same cause. No all-joint gain experiment or unchanged 352-second workload
was added. Moving physical leaders, multi-device physical input and genuine
physical required-input-loss recovery remain separately unproved.

### Observation, cleanup and focused verification

The corrected18 witness was copied while the native run was running at progress
4.6, sequence 298, decoded 640x480, arrival age 175.117 ms, with original metadata
and matching pixel hash. The actual pixels were inspected: room/ceiling/chair,
without an adequate view of the elbow. First selected18 retained pixels were
also inspected and explicitly labelled historical. The dwell witness attempt
arrived after completion and refused; no qualifying saved dwell frame is claimed.
Actual required local-image delivery and same-run camera-release receipts qualified
independently. Neither a validated image nor absent saved pixels establishes
visual elbow motion.

The existing advisory P1 clip (42,218,876 bytes; SHA256
`7a85413c8cdce9148e72dbea9c635d882ace79e29d129e1e1b6fc104efa5d6cc`)
was inspected at 28/35 seconds. It covers the earlier failed startup, not these
selected comparisons. Arms/lift are visible, but it cannot determine the present
elbow cause. No additional media framework or mandatory P1 delivery was introduced;
images, auth and private configuration remain outside Git.

Both new native cleanups verify 16 torque-off and four zero body-velocity
readbacks, stationary qualified lift, closed buses and no cleanup error. Camera
release matches the same run, exit 0, joined readers, closed socket and removed
runtime configuration; the terminal physical result separately binds the host
incarnation. Gain restoration was not
required for these ordinary-baseline recipes. Final independent ownership
inspection also found no serial, motor or camera-device owner.

| Affected check | Result and scope |
|---|---|
| Selected finite profiles / owner diagnostics | Windows 23 + 29 focused passes; exact isolated native candidate 52 passes in 3.34 s. Source `afd3789cdea0311b4daf74cf46c9dcc6b612e2c0`; imports bound to the selected candidate, no foreign compiled/source imports. |
| Selected completion and original completion guards | Windows 55 passes; native 69 passes in 0.51 s with exact corrected Core import, source `64052bc5a36289aaf50a27c42db35ede65fe7477`. |
| Existing private analysis | Six meaningful checks, including static-completed and second-static-cycle cases; native UUID/incarnation/calibration/timestamp/phase checks and deduplication. No runtime acceptance change. |
| Existing private staging/projection tools | Core-only stager 21 offline passes, projection builder 16, explicit named-ref fetch regression one real local Git RED-to-GREEN check. Earlier 50 stager checks retained for unchanged paths. |
| Legacy Windows cohort | 172 passed, six native skips, nine `fcntl.flock` platform failures before host construction retained separately. This is not an all-suite-pass claim. |

The initial existing-stager bundle fetch lacked an advertised HEAD; no component
checkout or Start occurred. The 48-file partial deployment and failure were
preserved, matched rollback/original idle qualified, and the explicit named-ref
fetch correction passed its regression before the same manifest staged.
The later Core-only advance reused that stager, sole-parent projection builder,
original nine-key manifest and native terminal-history bindings. Exact typed
history/counts, original cleanup/camera bytes and the original simulated DB/WAL
history were preserved; no replacement subsystem, wrapper, unit or hash protocol
was introduced.

### Actually active components and ordinary use

Final native verification on 2026-10-11 found the existing three resident services
active and available idle, with latest run a188 completed, known cleanup and
uncertainty false; there is no hardware task owner or autoplay. The original
simulated history files are byte-identical to their pre-stage records and retained
while its services are stopped. Calibration remains
`f3ffa9d5d531fe0213c8847f28baef292de027996f0a0267e0657a8c45037394`;
private bytes/permissions and existing CA trust/enrollments are preserved.

| Component | Current actual source / state |
|---|---|
| Normal motor | `81490b7d6f202fc13c606ee7132ed19c9cd7cb44`, reviewed selected-owner diagnostic projection, clean |
| Normal helper | `0fe3997f12b9cdfdd01fda6f4c6b102781252448`, unchanged from first selected stage, clean |
| Physical executor / gateway / worker | `b8558888ed43cbf5cc8cbf6bfaf493ccc21602fe`, existing opt-in resident available idle, clean |
| Current native manifest | `5ab2e37a72b359037fb1752b5060e556823e7f149e5d9ced0adedb08ff9c1fae`; separate exact pins, deployment directory `5ab2e37a72b35903` |
| Windows served console | `e55d390bf6e957b9adc52ccf62b69476db12a20d`, unchanged; 1443-byte private configuration changes only the two reviewed motor/helper pins, SHA256 `442719a4ce437eb9a1b6cc83562434c576d5232e5f04ade6866685219edb8890`, original owner-only ACL preserved |
| Accepted simulation | `c500204946a56862bb36ada3518ba5f248c02fa7`, stopped; original completed cb5 history retained unchanged, accessible matched rollback |
| Isolated / normal onboard camera | `a0e94bc5298c021f785eaaee4ef0e2c44283f3d7` / `9b1f0670e7068f7d39eb50270a118e3807418355`, unchanged, clean |
| Strict observer / advisory P1 | `7a266890703aae36f69a8d98e548d4c2e13710e5` / `2d9e365f1e302694633e7b1c916a38fb15cc50cc`, unchanged; failed strict diagnostic preserved |

Use the existing trusted HTTPS console at the configured Pi 8443 endpoint:
attach with the enrolled browser, Claim, select an allowlisted recipe and Start;
Pause/Stop use that same authenticated route. Pairing, page opening, Claim and
resident launch alone do not move hardware. The selected recipes are
`physical-left-elbow-compare` and `physical-left-elbow-dwell-compare`; they expose
the unresolved motion limitation and are not useful-tracking qualification.
Simulation remains the code default; the current resident was explicitly staged
with the protected-physical backend and real-local-camera-required policy.
A docs-only review commit does not change these executable pins.

The existing private controller verifies the available idle route without Start:

```powershell
ssh am1-pi 'python -B /home/pickmanmike/.local/share/am1-lift-startup-01/staging/staging-control-motion-quality-01.py verify-existing-idle --manifest /home/pickmanmike/.local/share/am1-lift-startup-01/staging/motion-quality-core-manifest.json'
```

Matched rollback remains an explicit procedure after known terminal cleanup;
it is available but was not executed after these qualified comparisons. From
the existing managed worktree, use the reviewed native controller, literal
Windows pin helper, then original-service idle restoration in this order:

```powershell
ssh am1-pi 'python -B /home/pickmanmike/.local/share/am1-lift-startup-01/staging/staging-control-motion-quality-01.py rollback-stopped --manifest /home/pickmanmike/.local/share/am1-lift-startup-01/staging/motion-quality-core-manifest.json'
if ($LASTEXITCODE -ne 0) { throw 'Stopped rollback refused; do not continue.' }
uv run --no-sync --project C:/Users/pickm/lerobot_alohamini_client python .cache/am1-lift-startup-01/prepare_next_operation.py pins-restore --manifest .cache/am1-motion-quality-01/projections/executor-core-reviewed-01/executor-motion-quality-manifest.json --manifest-sha256 5ab2e37a72b359037fb1752b5060e556823e7f149e5d9ced0adedb08ff9c1fae
if ($LASTEXITCODE -ne 0) { throw 'Pin restoration failed; do not continue.' }
$config = 'C:/Users/pickm/lerobot_alohamini_client/.worktrees/am1-console-implementation/config/am1.session.json'
$prior = Get-Content -LiteralPath .cache/am1-motion-quality-01/final-state/windows-config-invariants.private.json -Raw -ErrorAction Stop | ConvertFrom-Json
if ((Get-FileHash -LiteralPath $config -Algorithm SHA256 -ErrorAction Stop).Hash.ToLower() -ne 'e1c4c81a885f37108eff453916a83c4e9866d53326be408dafb4aad7faa5e7e2' -or (Get-Acl -LiteralPath $config -ErrorAction Stop).Sddl -ne $prior.Sddl) { throw 'Original bytes/ACL verification failed; do not restore services.' }
ssh am1-pi 'python -B /home/pickmanmike/.local/share/am1-lift-startup-01/staging/staging-control-motion-quality-01.py restore-original-idle --manifest /home/pickmanmike/.local/share/am1-lift-startup-01/staging/motion-quality-core-manifest.json --windows-original-pins-restored-sha256 e1c4c81a885f37108eff453916a83c4e9866d53326be408dafb4aad7faa5e7e2'
```

This stops only the three affected residents, preserves physical history,
restores matched f03 motor/8e helper and original 1443-byte e1 Windows bytes,
then restores c500 simulated services idle without task Start. These commands
require the retained private artifacts in this managed worktree; they are not
general installation commands. Unknown cleanup refuses ordinary rollback/rearm; separate real reconciliation is required.
Camera projections, calibration, trust and private permissions remain separate.

Integration recommendation: review the focused diagnostic and completion repair
in the existing dependent PR stack, keeping its separate component projections
and historical results. Do not label useful joint response repaired. No merge is
authorized by this packet; broader dependable interactive control still requires
the specific elbow-response evidence and separate unproved input modes above.

## Retained AM1-LIFT-STARTUP-01 physical evidence

| Milestone | Actual result |
|---|---|
| Corrected startup and small check | `51bcab5d-2df9-4f5f-adb7-99c6b0552ee3`, host `41482751-f0f9-437f-a160-cfc48c9bf4b5`: actual Live and ordinary Stop at 9.2 trajectory seconds, zero recoveries, no first cause, uncertainty false and verified cleanup. Partial profile, no qualified full return. |
| First full 88 | `d5898a83-0066-4500-9280-112e108d0d14`, host `594a813c-89e5-47ba-97fd-a63cb7d1c89b`: trajectory 88, cycle 1, qualified return 1 and completion true; cleanup failed and remained unknown/uncertain. |
| Separate stopped reconciliation of d589 | Actual read-only proof raw SHA `de2dbb88392d5d5f7ba8bf44ff620f42c23e8a57978a6a57558693c6d55363f0`; durable ACK operation `0eff89a1-82a0-425f-bf6b-c28a5a6d4cb4`, consumed by distinct regular operation `a86a86cc-32f4-4330-98b8-2e81e9c947ab`. Both original markers were durably archived; run uncertainty is false. Failed status, first cause, original unknown cleanup and physical result remain unchanged. |
| First cleanup-corrected full 88 | `f15d2a59-3a10-4f8a-bd9d-40648f9f0cb1`, host `f725aa76-357d-43e3-85c2-a57f76b1a0d3`: trajectory 88, cycle 1, qualified return 1, completed with actual `physical_readback_verified` and uncertainty false. Startup/relief and all 20 zero/off/lift stationary cleanup qualified. Three `required_proof_loss` episodes occurred only at the completion boundary; actual camera/motor inputs remained current and no native fault/watchdog/gap was recorded. Those episodes are retained; genuine input loss was not demonstrated. |
| Completion-handoff-corrected full 88 comparison | `1af4ba4f-077c-43e6-928e-9165d233ca74`, host `c250ca76-4c86-4e47-ac5a-40d13d5511bc`: preparation 3.3, trajectory 88, admission 1, cycle 1 and qualified return 1; completed with zero recoveries, no outer/native first cause, actual `physical_readback_verified` and uncertainty false. The retained completion receipt binds measured feedback 2791 and intent revision 0. Independent analysis found response in 11 segments; left elbow stayed exactly 39.0675 despite accepted excursion 2.987777. |
| Four-cycle 352 within 420 Live | `ed5a216b-7923-4af2-9fc4-3159b537a7d6`, host `1a88e747-73cd-4c6f-a4f3-0f3de0c76a4d`: completed 352, four cycles/four qualified returns/one admission, zero recoveries, no first cause and verified cleanup. Native Live to completion 362.493436 seconds within the original 420-second deadline. Browser detach 25.1 to 30.3 retained the same run, seed and deadline; administrative launch SSH exited. |
| Comparable automatic restart from ordinary rest | `bf0cbe27-f7a5-4576-aa34-456d7d7e1d72`, host `2488a390-9e39-46eb-b0a6-37523af85e91`: fresh initial measured 12-joint vector equals the preceding final measured vector, and its new seed equals that actual initial reference. Completed 352/four cycles/four qualified returns/one admission, zero recoveries, no first cause and verified cleanup. Native Live to completion 362.369458 seconds within the original 420-second deadline; post-home qualified through `strict_quiet`. |
| Conditional original body 12, first attempt | `a0ffb8ae-f7ef-441e-b3ef-fafad4c91a88`, host `305562e4-456f-4d0a-8a05-54802ca01095`: faulted at trajectory 2.2, completion false, one admission and uncertainty false; outer first cause `feedback_loss`, actual native first cause `ComparisonRefusal: live: new lift motion refused while stopped feedback is uncertain.` Same-run `physical_readback_verified` cleanup and camera release qualified. U/J pulses had not begun; no completed 12-second or lift-pulse result is claimed. Its original failure remains retained alongside later corrected b0 completion. Raised-base response is not navigation/floor-travel proof. |
| Body 12 after finite release correction, startup attempt | `c7199580-bc98-42c8-be1c-29898c1485e1`, host `98d4b081-8f3e-44a5-b903-4bdd97d729b5`, source 7aa/5f7: faulted before homing/Live, execution absent and zero recoveries. Native first cause `ComparisonRefusal: baseline: velocity or Moving reported persistent motion during the stationary window.` Three checksum-valid torque-off/goal-zero samples raw 348/347/347 with velocity -50/Moving 1 over 0.104 seconds triggered the unchanged baseline guard. Later cleanup qualified, all 20 zero/off and same-run camera release verified, uncertainty false. No body commands or physical release-fix exercise is claimed. Separate stopped diagnostic and later terminal-Start correction retain this original failure. |
| Corrected original body 12 after exact-terminal Start repair | `b0dedafc-6e9e-4ef1-a1d7-fe098bd8ece5`, host `a8f67998-ce17-477d-a164-aad3ef4522be`, source a1d/b2dd: completed 12, preparation 0, admission 1, qualified return 1, zero recoveries, no outer/native first cause and known cleanup/uncertainty false. Original Live 86030.084506043/deadline 86060.084506043 retained. All 20 literal off/zero, stationary lift, buses and same-run camera release verified; mapped W/A response and directional U/J pulses measured; requested 50 mm arrival and floor travel not established. |


The small run 51bc measured a 1.625416 shoulder-pan displacement toward target and later a
0.147765 return trend; residual 1.477651 remained within the original three-unit
envelope. This is useful bounded response, not precision tracking or a complete
qualified return. Both this run and first full 88 admitted through `strict_quiet`;
their early two-count excursion disabled the one-count alternative. The small run 51bc
demonstrated normal current-criterion homing, actual upward relief and measured
ready near 10.5 mm. Homing current alone does not prove physical hard-stop contact.

The first full 88 retained 89 unique arm measurements. Eleven segments showed measured
outbound response; left elbow stayed exactly 39.0675 in its sampled segment despite
accepted excursion 2.716161. A cycle/return counter does not establish useful response
of every joint. The later repeated measured-response limits are recorded below. Keep targets,
host acceptance, measurement and visual evidence distinct; no visual-motion claim
comes from a terminal screenshot or absent advisory footage.

The first cleanup-corrected full 88 again showed response in 11 arm segments; left
elbow remained stationary despite accepted +33 raw ticks. A separate stopped,
read-only selected ID3 settings probe `db1ef60a-a194-46e8-aff7-798419f9efe0` made 17
reads, kept torque off at both bookends and closed its bus without actuation or
first cause. It read position mode 0, CW/CCW deadzone 1/1, P16/I0/D32, minimum startup
force 16, torque limit/max 1000/1000, goal/present 2400, voltage 119, temperature 30 and
current 0. These values do not establish an obvious configured deadzone or disabled
torque-limit cause. At that historical checkpoint, an actual live raw goal-register receipt was still missing; the selected comparisons above now provide it.
The finding remains limited; no mechanical blocker, gain repair or all-joint
usefulness is inferred from this stopped snapshot.

Independent selected-probe review confirmed 17 checksum-valid ID3 read requests,
including torque-off bookends; maximum individual read time 1.013201 ms, acquisition
0.357238897 seconds and finite unit 2.852 seconds. The sole checked owner while the
bus was open was the probe itself; before/after owners were absent and the current
f15 host/camera units had PID 0. Original raw reply bytes were not retained. Parsed
feedback and source/terminal/calibration bindings support the stopped read-only
finding, not a powered-response or mechanical/gain-limit diagnosis.

The 352-second repeat retained 320 unique measured arm acquisitions and 48 declared segments;
the automatic 352-second normal-rest run retained 335 and 48. Saved return vectors stayed within the
existing envelopes, while each native completion receipt retained four actual
qualified returns. Browser sampling did not export every stationary qualification
row. Left elbow was static in all four saved segments of both runs. In 352-second repeat,
right shoulder lift moved only 0.103466 in the first saved segment and was static in
cycles 2 through 4; left wrist flex was also static in cycles 2 through 4. In the fresh
rest run, right shoulder lift was static throughout, and left pan moved 1.329885 in
cycle 1 then stayed at that offset in cycles 2 through 4. A seed offset is not new
movement. The 352-second repeat showed nonzero saved range in 9 of 12 segments per cycle; the normal-rest 352-second run
showed 10 of 12 in cycle 1 and 9 of 12 afterward. Neither result establishes useful
all-joint repeated response or precision tracking. No mechanical cause, gain repair
or response improvement is inferred from the stopped selected-ID3 settings probe.

The 352-second repeat ordinary cleanup actually used the corrected healthy thermal collection:
five genuine occupied peaks 37/37/38/37/37 C within 0.416013 seconds, alongside
qualified stationary lift,20 distinct zero/off scalar readbacks, closed buses and
no errors. The normal-rest 352-second run has a different recorded cleanup path: its ordinary expected
SIGINT/KeyboardInterrupt was retained by the unchanged BaseException latch with
`earlier_failure=''`; cleanup rows contain no thermal-window/collection fields.
That existing path verified off/zero transport/current/status and stationary lift
without executing the healthy fresh-slot clone. Its last Live window had five
actual 39 C peaks within 0.402033 seconds; cleanup raw temperatures were all 39 C.
Known 20 zero/off, lift qualification and bus closure still succeeded. Do not treat
the normal-rest 352-second run as another actual qualification of the new fresh-slot collector. Numeric
restored gain rows were not exported in either long-run cleanup receipt.

Both long runs retained actual qualified decoded local-camera observations bound
to their exact session and one capture generation, and same-run exit 0/readers joined/
socket closed/runtime config removed receipts without errors. Their release receipts
explicitly report `device_fuser_checked=false`. No saved pixels were inspected for
these runs; this proves required local-image delivery rather than visual joint
movement. Browser/SSH continuity and zero recovery episodes do not demonstrate a
genuine required-input-loss recovery workload.

## Startup and cleanup repairs

The original refusal followed a deliberately conservative transition.
[Commit d448a8f8](https://github.com/pickmanmike/lerobot_alohamini/commit/d448a8f8d0ede7a8990cfced6cb8175e55cb6fbe)
required five fresh quiet post-home readings over at least 0.2 seconds before
normal relief, within the existing one-second deadline. It followed an earlier
pre-Live refusal on two repeated velocity/position direction disagreements;
its contemporaneous documentation left the servo's physical cause and the
hypothesis's effectiveness unproven. Original run
`303353c8-297c-4e24-86d3-d4bfb25f813a` supplied 21 distinct advancing readings at
raw 808/809, with intermittent +/-50 velocity and Moving indications. Eight
candidate resets left only three quiet readings over 0.103993 seconds. The
software correctly refused that trace; no Live or finite profile was admitted.

Earlier saved evidence showed settling from raw 810 through 813 to 814 followed
by five zero-velocity/Moving-zero readings over 0.207999 seconds. That four-count
whole-phase settling differs from its final stationary candidate. Replaying the
original packet bytes reproduced this success and the 303 refusal. Checksums,
field offsets, sign-magnitude decoding and recorded static configuration agreed
across the traces. They establish valid reported indicators, without establishing
harmless quantization, a firmware defect or a mechanical cause. The older
direction-failure trace contains only six post-home readings; its later relief
data cannot supply a missing quiet-start verdict. The original 303 trace has no
recorded zero-goal samples after its one-second window, and its exact final
zero-write completion time was not logged.

A reviewed ID11-only characterization retained the original one-second verdict
and a separate three-second observation ceiling after the normal zero write.
Attempt `980bd79e-9b5b-41d3-a5d5-623938082b0b` stopped at its first +100/Moving-one,
one-count sample because the private helper incorrectly added an immediate speed
abort. It produced no strict verdict and remains a failed diagnostic. After that
private guard was corrected and tested, `c7001833-8d2b-4977-b864-105645fd8319`
reported an initial +50/Moving-one sample followed by five quiet readings at raw
809, qualifying 0.261086 seconds after zero. Neither entered relief or Live;
both retained actual scoped torque-off/zero-goal readback and closed-bus cleanup.
Those two-row receipts do not claim an all-motor sweep. This result supports no
extension of the production deadline and does not show whether the original 303
state would have qualified later.

Corrected-policy replay accepts the original 303 trace only through the explicit
`bounded_indicator` alternative, retaining `motion_feedback_quiet=false`. It also
preserves the historical four-count settling success through `strict_quiet`.
The successful physical starts below used strict quiet qualification; the
alternative's acceptance is replay evidence, not an actual physical branch-use
claim. The original failures remain unchanged.

| Diagnostic evidence, retained privately | SHA-256 |
|---|---|
| Original 303 native host log | `f6a643f0f5a67e537c414dce297bca4921d4fbd62e52b963d26c3846212c6fe1` |
| Unchanged three-trace production replay | `55cb2633101877b856d6998a708d187a35ef05a3f93294b78aa50fa3b33a8dfd` |
| Earlier successful quiet startup log | `32e79140255e363b9763a149cb08109c1ba89bbbfb0177d83a179cc7b2023258` |
| Earlier relief-direction refusal log | `52ba6a517f950d70956f852d4d7b58dee7ebec3ba27b1175136c34fd0b741f8f` |
| Original failed 980 characterization result | `a4af61def0b228fa97a3c2112a7a91b54d9e5031c45c157d1d635e29deea5572` |
| Corrected c700 characterization result | `afbdd006510c613deffe10f767514e99befb7c24c69337b34faf04f8b4404ca2` |
| Corrected-policy replay | `b0e493ed4333fdeac2a5232a0fb26e63bf5dfae8e4b77a3eb12da752da1eac3f` |

The one-count whole-phase eligibility limit applies only to the bounded AM1
alternative. Successful strict readiness retains its original local five-sample/
0.2-second window; a two-count whole-phase trace can still qualify strictly, as
ed5 did. No strict-window requirement is replaced by the alternative.


Commit `d190d248b2042e8704486b4a6af1c6d150dbee4e` attempts original strict-quiet
post-home qualification first. Only at its unchanged deadline, an AM1-only bounded
branch may accept five advancing samples over 0.2 seconds with whole-phase one-count
excursion/drift from the original zero origin, raw speed magnitude at most 50, and
unchanged persistent motion/current/thermal/voltage/mode/torque/goal/transport guards.
It averages no opposite velocities and resets no accumulating drift. No extra
timeout, homing/travel/gain/calibration change is introduced. Captured replay and
injected checks support that branch; actual hardware use remains receipt-dependent.
Historical strict and relief-direction/no-upward-progress failures remain intact.

D589 cleanup zeroed goals, released torque and verified shoulder I0 restoration.
A transition skipped one 100 ms thermal slot. The second fresh 35 C reply arrived 52 ms
later in the same slot; equal temperature retained its original peak timestamp, and
an older peak aged to 0.540051 seconds. The original 500 ms rule refused. This was a
cleanup retained-window transition, not a stale latest reply or confirmed thermal
majority. All 20 scalar zero/off reads and bus closure succeeded, but lift stationary
cleanup did not qualify; the original receipt remains unqualified.

The cleanup-only correction copies genuine peak tuples, expires only peaks older
than 500 ms, retains original slot origin/per-slot maxima and three-of-five thermal
confirmation, and collects five fresh occupied slots within the original one-second
stationary budget. Healthy cleanup keeps 40 ms request timing and freshness at the
actual decision clock. Latest stale/backward/repeated input, hard faults, persistent
motion, late completion and confirmed high temperature still refuse. Live thermal
behavior remains unchanged. Code or offline checks do not qualify the earlier run.

Separate exact-run reconciliation requires fresh native read-only all 20 zero/off,
80 identity/calibration comparisons, I0, original grouped lift packets, strict
stationary/five-slot thermal evidence, same-run camera release, no owner and closed
buses under both locks. The producer preserves both markers. Regular distinct
Reconcile operations validate, durably archive/write ACK, then consume the actual
receipt off the authority mutex. Only completed actual ACK may clear uncertainty;
original failure, unknown receipt, reference, progress and deadline remain unchanged.
Actual native proof qualified all 20 zero/off controls, strict lift stationary/fresh
thermal evidence, identity/calibration EEPROM, I0 and closed buses. Its original
53,973 bytes and the durable ACK remain private and unchanged. After actual regular
acknowledgment, d589 remains faulted with its original unknown cleanup receipt and
first cause; only separate qualified cleanup reconciliation resolved run uncertainty.

The full actual proof plus run exceeded the unchanged 65,536-byte IPC snapshot
frame, causing a browser timeout and native snapshot closure. A snapshot-only
presentation capsule preserves exact run/incarnation/reconciliation identities,
original proof times, literal raw proof SHA and full completed ACK; full durable
history/proof remains unchanged. Native focused transport checks passed 143 with
zero skips. The private stopped stager hydrates only a comparison copy from fixed
literal proof/ACK/archive files. Historical proof source 29dc and the transport-fix deployed
executor 24cdc are explicitly distinct; no immutable proof source was relabeled.

The next demonstrated failing layer was finite completion-to-hold acknowledgment:
f15 reached measured return/completion while the previous handoff produced three
boundary recovery episodes. Its original deadline 77857.974495333, seed, episodes
and first `required_proof_loss` cause remain intact. The reviewed correction freezes
completion only on an advancing measured qualified return with matching active
host acknowledgment and current required observation. Its subsequent intentional
hold cannot erase that receipt. Pause/Stop, native fault, required input loss and
the original deadline still take precedence. Resume retains the provider, original
seed, progress and deadlines; a completed task must reacknowledge three advancing
active samples across at least 0.2 seconds before a new revision receipt qualifies.
The corrected actual comparison completed with zero recovery episodes and known
cleanup. This result does not demonstrate genuine required-input loss recovery.

An initial source-only stage refusal came from hashing JavaScript-reserialized
terminal JSON: eleven 3.0 amplitudes and two 88.0 progress fields became integers.
Exact comparison found only those 13 representation differences. The private staging inputs were bound to literal
native durable JSON; strict validators, browser evidence and the
original refusal remain unchanged. No numeric-normalization bypass was introduced.

## Current verification and actual final deployment

The lift/cleanup correction received clear focused review. Its isolated native
affected cohort passed **365 tests in 12.17 seconds, exit 0, zero skips**, using
existing Python 3.12 dependencies and verified candidate source roots. An earlier
finite mock-test unit's AF_NETLINK socket refusal remains a failed test invocation;
only that test unit received the missing family. Resident/hardware boundaries were
unchanged. These are offline/injected checks, not physical-motion/cleanup proof.
The final completion correction received a separate clear focused pre-use review,
including Pause before and after the final return and current receipt size/IPC
checks. Earlier tests and failed invocations retain their original scope.

The final finite-completion correction had a separate independent focused review
and native affected cohort: **205 passed in 21.20 seconds, zero skips, exit 0**,
invocation `ed7e1342-33ef-4807-b025-964778eaf06b`. Its manifest bound 10 exact source/
test files; all 67 recorded robot imports resolved under the isolated candidate and
both foreign compiled-extension and outside-candidate robot-import lists were
empty. Existing native Python 3.12 dependencies were retained. Injected checks cover
Pause before/after the final qualified return and preserve program, deadline,
reference, seed and counters. A typed current completion receipt remains durable;
the unchanged 65,536-byte IPC frame accommodates its snapshot. These results do not
replace the separate physical evidence or a genuine required-input loss workload.

The reviewed stopped executor-only stage deployed
`23ae0738c7b5063c961c68f9c4085a6df1f354fc` from candidate
`0ca9649fdb86b333db59192c3272623d9281acbe`, with immutable manifest
`6a4003f4c8c18360604236271ac926ac27ea6c8de3cb76ab5e845378cb0d2a54`.
Its idle-qualified receipt records no hardware/task Start and unchanged complete
physical and accepted simulated histories. Motor `c147b51aca3e3ccc4e9bc3121ad76076d137cf39`,
helper `0fe3997f12b9cdfdd01fda6f4c6b102781252448`, isolated camera
`a0e94bc5298c021f785eaaee4ef0e2c44283f3d7` and Windows private bytes SHA
`59087f02072b48b4af6626967a9037cfc95e9df0807f8ef45d8faa51e626bcfa`
remained separately bound. This is the comparison deployment, not a final rollback
claim. Historical proof executor 29dc remains unchanged.

The comparison retained 88 unique actual measured acquisitions and original native
request/acquisition timing. Browser samples can miss endpoint holds; the owner
qualified its one return using the original measured criterion. Actual local image
evidence at trajectory 56.1, sequence 1084, had original arrival age 0.134787 seconds.
The private image view did not show robot joints moving, so it supports current
local-camera delivery without adding a visual-motion claim. The left-elbow
no-response finding remains unresolved; neither this comparison nor its counters
establish useful movement of every joint.

The existing short body recipe exposed a separate request-composition issue during
source replay: each action started with an explicit lift velocity zero, which took
precedence over an existing finite height-only command. The reviewed executor-only
correction removes that inherited zero only when the existing body command includes
height and no explicit velocity. Explicit velocity zero and the preparation/hold/
Pause/Stop/loss zero paths retain precedence; the original recipe with four 200 ms pulses, bounded height/lift policy, gains and
normal protections are unchanged.
Its final isolated native cohort passed **215 tests in 33.53 seconds, zero skips,
exit 0**, invocation `34120a67-cd79-4d46-8f53-6c7d36190449`; all 68 recorded robot imports
came from the candidate and foreign-extension/outside-candidate lists were empty.
The body candidate `d9b34333cf307516c2aa5db7320eede4657800d3` and independently pinned
executor projection `37e723cbce3d6a73c59450d3b57eaa22da845030` produced the actual faulted body attempt recorded above. The qualified idle stage
is recorded below; its original failure remains retained alongside later corrected b0 completion.

The first private stopped stage attempt for that projection refused before any
service stop or source checkout because its artifact wrapper dropped the immutable
controller's `timeout` keyword. All 35 retained command records contain no stop or
checkout. The original failure and complete partial deployment remain retained.
The one-line private forwarding correction passed 17 focused checks after two
regressions reproduced the original error; product/projection/input bytes remained
frozen. The entire failed partial was then atomically preserved as
`cf97481334a23196-failed-6cf70a57`; its actual receipt retained all 42 file hashes and
modes with hardware/task Start false and service/source changes false. The original
failure,35 command records, original input UUIDs and raw DB/unit artifacts remain
unchanged. Retry 02 used distinct evidence UUIDs and reran existing native guards.

The actual reviewed stopped retry deployed executor
`37e723cbce3d6a73c59450d3b57eaa22da845030` with immutable manifest
`cf97481334a23196aa511af899da6d3e18e1bd365bc57ac0ed61c5e8f6a030d4`, while motor c147,
helper 0fe, isolated camera a0e and current Windows 590 bytes remained separately
pinned. Its actual idle-qualified receipt SHA
`0a764d0d6774c7cfe509bfdfd8b723a2fd5f1b891c726ffbf9a4770750e16bc4`
records hardware/task Start false, unchanged complete physical and accepted simulated
histories, and the original completed bf0 terminal retained. Original d589 proof/
ACK/archives keep their actual historical source 29dc. This is source/idle qualification,
not a body motion or final rollback result. The first actual body attempt then
faulted before U/J; its native first cause,2.2-second progress, known cleanup and
released camera remain retained. The later finite-release and exact-terminal Start corrections, actual b0 completion
and original histories are recorded below.

Source and raw feedback identified the retained body failure's next layer: outside
U/J, the original client body release contained both current height and explicit
velocity zero. The original stopped-feedback guard inspected height presence before
zero and refused this redundant nominal-motion request. The focused finite-body
helper correction removes only that redundant height outside U/J. Actual U/J height
requests, explicit release velocity zero, original client/manual behavior, active
motion/feedback protections, motor policy, gains, Core and all other recipes remain
unchanged. This repairs the finite command's meaning; it does not relax the physical
stopped-feedback guard or reinterpret the earlier failure.

Candidate `4603bbe2b522cf48712f7eeca7b0098c5432ad2d` received focused independent
source review and final native verification: **230 passed in 34.37 seconds, zero
skips, exit 0**, invocation `7000c074-1a56-4d41-9574-38f85f931292`. Its 73 recorded
robot imports came from the isolated candidate; foreign compiled-extension and
outside-candidate import lists were empty. The actual Executor source SHA
`8f139cf1c3edb99fca840c2da9d325c1f910c16bef1b91533e8a7ad4fb703cec` is distinct from
the prior inherited-zero correction, while Core 444 and reconciliation 394 remain
unchanged. These source/mock checks do not replace actual body feedback.

The reviewed exact failed-body stopped stager binds a0ff's literal native durable
run SHA `8d378405ead1c633b12fa287c5c126fc813cbc150a5564872082b3439e3dc410` and unchanged
raw cleanup/camera receipts. It requires its failed 2.2 result and original causes,
then uses the unchanged current-terminal all 20 zero/off/lift/camera qualification.
Its independent filled-data audit confirmed sole f8 parent, three tools and Executor-
only delta from 37e; the new manifest differs only in executor head and bundle SHA.
Actual stopped stage deployed `7aa5842aaf041ea6e198ff320c9a8aea8123670c` with immutable
manifest `5f7df0fcefa00da49c1313be42a4d8d2ab53e587e4e8b56683fff6539a28937a`.
The actual idle-qualified receipt SHA
`89e29dc5b2f174f3e7bab5922b12c702e15837e026bc8d9bced769a586c7467e` records no hardware/
task Start and unchanged complete physical and accepted simulated histories. A0ff
remains faulted with its original causes/progress and known cleanup; original d589
proof/ACK/source 29dc and the preserved failed-stage archive remain unchanged.
Motor c147/helper 0fe/camera a0e/calibration/current Windows 590 remain separately pinned.
The next actual startup attempt c719 faulted before homing/Live, so its finite
release correction remains unexercised physically. Its exact baseline first cause,
absent execution, zero recoveries and known cleanup remain retained. Three actual
torque-off/goal-zero samples raw 348/347/347 with velocity -50/Moving 1 spanned 0.104
seconds and triggered the unchanged persistent-motion guard. Later stationary
cleanup used five samples over 0.208 seconds within one count, speeds 0/0/+50/0/0;
all 20 zero/off, bus closure and same-run camera release qualified without errors.
No baseline product correction is inferred from those later cleanup samples.

The separate native read-only discriminator `native-892fe6e7` retained 11 advancing
ID11 acquisitions plus one off/zero bookend under the existing ownership/source/
calibration guards. Its raw trace SHA
`aa5005b0895aaabd1cb5ff514b88c54e6da9c5732d4481355b9b724813d1203a` reports no actuation
or admission, off/zero/current 0/status 0/35 C throughout, closed bus and no errors.
The unchanged original cold-baseline predicate qualified its first nine actual
samples over 0.416 seconds within the original 0.6-second budget: one-count excursion,
velocities 0/0/+50/+50/0/0/-50/+50/0, and no three consecutive same-sign reports.
This is evidence about the fresh stopped window, not retroactive qualification of
c719 or proof that ordinary setup writes and the next startup will pass. Independent saved-trace review confirmed all 12 complete ID11 packets, first-nine
span 0.415998192 seconds/five fresh 35 C slots and total acquisition 0.522894542
seconds within 0.6, with exact source/terminal/no-owner/closure bindings. Its report
SHA `00e25c9bfd2bcc4e091cb1a3d363d4909eef38b2c604f8da0634186042d352df` is retained privately. The later body retry on unchanged
baseline completed as recorded below; this separate read-only discriminator does
not itself establish that powered result. No completed body motion, physical release-fix
exercise or lift-pulse result is inferred from this read-only trace or source tests.

A subsequent browser operation attempt in `body12-baseline-qualified-01` timed
out while waiting for a new UUID. It admitted no new physical Start. A native
snapshot-only check still showed c719 unchanged, with no new Start event or
execution. This retained browser attempt is distinct from a new physical failure
or body run. Its original report SHA
`17b2d596185591f38061a86b04acb3e1b705156989b9c7b51fa04174870f33c6` remains private.
Transient administrative/page connectivity recovered without network changes.
The reviewed response/current-claim fence then retained the next actual response.

The second browser attempt captured `/api/start` HTTP 200 with `accepted=false`,
`status=refused`, `effect_admitted=false` and the reason “fresh qualified feedback/
proof and acknowledgement required.” Its report SHA `510b310f1bc14c792bbfa9ab834d0443cbe286f93db6f6bd767f9828a8ea6c82` is retained
privately. The current run remained c719, with no new physical admission or execution.
The source admission check blocks the retained historical fault after its known
pre-Live cleanup; absent live motor/camera evidence is expected after those owners
close. The demonstrated repair scope is the cached exact-terminal proof check in
Executor and the corresponding literal-True Core admission gate. The focused source correction was reviewed, committed and subsequently staged
under the exact failed-startup/current-terminal predicates recorded below. No camera
freshness rule, motor baseline policy or old failure result is relaxed. This is a software
request refusal, distinct from another powered run. The later actual body completion/restoration is recorded below.

The terminal-Start correction is reviewed and committed as
`3e79fa92db71775d7f0841071c56a1dc0a290111`. Executor's cache-only
`can_start_after_fault` requires the exact matching failed terminal's verified
20 off/zero readbacks, stationary lift, closed buses, released same-run camera,
original cause/incarnation and no active physical/reconciliation worker. Core
accepts only literal `True` from that predicate while retaining its own exact
fault/lifecycle match. It does not clear c719, reuse its feedback for the next
physical effect, or change baseline/camera protections; the new run still acquires
current startup evidence, seed and deadline.

The reviewed executor projection `a1d1afe08414712fa21b3c983a2c0a8cfded26df` has sole
parent f8, the same three tools and a Core/Executor-only delta from actual 7aa;
reconciliation 394 is unchanged. Its immutable manifest
`b2dd5c373df53e5a3fd264c1d40344b51db7472b0f716e8d028796e13efe97b6`
advances only executor head and bundle SHA from 5f7. Private wrapper 003d and
filled stage 238a/rollback e7cb received independent clearance with final
native receipt qualification now verified. Locally retrieved native invocation 83966668 reports
253 passed in 35.12 seconds, zero skips and exit 0; its import record contains 73
candidate imports, no foreign extensions or outside-candidate robot imports.
The complete native receipt and all ten exact source/test bindings were verified
against candidate 3e79 and the native verification manifest. This native/mock
qualification is complete; actual stopped staging and its retained results follow.

Actual stopped stage deployed executor `a1d1afe08414712fa21b3c983a2c0a8cfded26df`
and immutable manifest `b2dd5c373df53e5a3fd264c1d40344b51db7472b0f716e8d028796e13efe97b6`.
Its idle-qualified receipt SHA `0e6cbfba5a3f8f29d016ed08a937959b4741dd02006b14429e3df1a7d80ebe68`
records hardware/task Start false and unchanged physical/accepted simulation
histories; c719 retains its original failed pre-Live status, causes and known cleanup.

The actual body Start followed stopped staging and a new resident service
incarnation `3d295533-8e1d-42b7-93b0-bc73c30e394e`. It demonstrates fresh ordinary
startup and the body recipe through the candidate, with post-home `strict_quiet`
qualification. It does not show physical execution of the cached prior-fault rearm
branch in the same resident process; that branch is exercised by injected tests
and the saved exact c719 fixture.

The subsequent actual original body 12 run `b0dedafc-6e9e-4ef1-a1d7-fe098bd8ece5`,
host `a8f67998-ce17-477d-a164-aad3ef4522be`, completed trajectory 12 with preparation 0,
one admission/one qualified return, zero recoveries, no outer/native first cause
and uncertainty false. Native Live was 86030.084506043 and its original deadline
86060.084506043. The retained completion receipt binds same-run measured feedback 384
and intent revision 0. Independent quantified analysis below distinguishes
command acceptance, actual measurements and evidence limits.

Same-run cleanup raw SHA `45df3ed68c5ee8b4e4f41fdcb11dca6f1a86a89a4fb855f4b8a7f449a57403db`
contains 20 distinct literal zero/off readbacks, verified stationary lift, buses
closed and no errors; selected gain restoration was not required for this ordinary
body recipe. Camera raw SHA `c6cc69bc81c49f4ad11072bf6ef88e2c2b01901b368e21df2e3aea4552a9d684`
is the same UUID's genuine release, backendexit 0/readers joined/viewer socket closed/
runtime config removed/no errors; device_fuser_checked=false remains explicit.
Browser report SHA `de5ae2671ae088bcbf3202bb3b80d873e85a8a2a039d3b08dc49433289689715` is retained.

Independent native/browser review confirms bounded response in the original body
recipe. W accepted x velocity +0.15 m/s and measured +0.0752797588 m/s at
observation 81/action 77. A accepted theta velocity 45 deg/s at observation 100/
action 96; zero-release observation 106 later measured 43.9453125 deg/s. Inverting
the existing wheel-feedback mapping yields all three wheel inputs 1250 at that A
response. This is derived actual telemetry, not exported raw wheel packets; the
raised-base result does not establish floor travel or navigation.

U produced native lift goal -1300 in seven reads over 0.200010711 seconds, measured
velocity from 0 to -1350, and height from 10.376953 mm to a 16.447266 mm peak including
coast after release. J produced goal +1300 in six reads over 0.166993424 seconds,
measured velocity from 0 to +1250, then settled at 11.668945 mm after goal zero.
These are directional responses to the declared 200 ms pulses, not arrival at the
requested relative 50 mm targets. Eleven held arm joints were static; right pan's
maximum residual 0.073964497 remained within the expected 1.0-unit hold envelope.
That successful holding check does not resolve the static response findings from
the long arm profiles. The completion receipt at 86042.648860641 was 12.564354598
seconds after Live, within the original deadline.

All 565 native packet rows decode and pass checksum checks: 509 unique acquisitions
and 56 consistent duplicates. One older Live row at 86042.700 was printed after
cleanup at 86042.966. The frozen parser's original order refusal is retained;
a separate derived view orders unchanged original acquisition timestamps and maps
each row back to the raw record. No raw log, timestamp or frozen parser was rewritten.
Live samples were 35-36 C, 11.6-11.8 V/status 0; maximum packet acquisition was
1.978 ms. Fifty-seven running observations qualified, with observation age at most
0.235671 seconds and feedback age at most 0.136161 seconds. No body image pixels
were viewed, so delivery/freshness records do not establish visual movement.
Sparse back-wheel -50 reports after release do not establish mechanical creep.

Cold baseline qualified nine actual samples over 0.416 seconds. Post-home used
`strict_quiet`: five over 0.208 seconds, one-count whole-phase envelope and ready
height 10.376953 mm. It did not use the fallback branch. Same-run ordinary left-
shoulder registers were actually read P16/I0/D32; no I1 trial applied to this body
run. This is selected numerical register proof, not proof for every gain.
Cleanup contains 20 literal integer zeros and five quiet lift samples over
0.208018 seconds, torque/goal zero and I0, closed buses and exact equality to the
retained terminal's raw receipt. This body cleanup actually ran the healthy fresh
thermal collector: five genuine 35 C slots within 0.433015 seconds. That differs
from bf0's expected-KeyboardInterrupt latched path, which did not run the collector.
Independent actual body review SHA
`1e340033887e2fb55e74f6cd7f51a53fc42c78124d5cd625aa2a0e49fc71afb9` is retained privately.

Matched stopped rollback executed after that actual current terminal qualified.
Rollback receipt SHA `6803d7cf5d2c92fafee61c73dcf42707800d8349178de14114ea56b9870532c5`
restores normal motor f03/helper 8e, retains actual a1d executor and separate camera a0e,
and preserves the full completed body and every previous physical failure/history.
Original Windows 1443 bytes/e1 hash and owner-only ACL were verified restored. The
original accepted c500 services are active idle, with no hardware/task Start and
physical history retained, as recorded by original-sim-idle receipt SHA
`b7223e6c6a12eb95247a856eff138b4da550f9b162c62b6d18b539e442fc97f3`.
Its original historical `cb5cb556-023e-4417-b8bf-8a0f0115802b` remains completed 352
with first cause required_proof_loss; no substituted run or autoplay occurred.
The final audit and quantified body analysis are qualified below.


Focused final terminal-Start verification used the existing Windows environment:

```powershell
uv run --no-sync --project C:/Users/pickm/lerobot_alohamini_client pytest tests/robots/test_am1_physical_executor.py -q -k terminal_start
```

That selection passed 23 checks. The final affected Executor/persistent-session/
reconciliation cohort passed 208 with one Windows platform skip in
46.70 seconds. Native final verification passed 253 with zero skips in 35.12
seconds against the isolated candidate and all ten exact source/test hashes.
Its existing Python 3.12/bootstrap runner invoked:

```python
pytest.main([
    "-v", "--noconftest", "-p", "no:cacheprovider", "-c", "/dev/null",
    "--rootdir=<isolatedcandidate>", "--basetemp=<freshprivatefixture>",
    "tests/robots/test_am1_physical_executor.py",
    "tests/robots/test_am1_physical_reconciliation.py",
    "tests/robots/test_am1_physical_authority.py",
    "tests/robots/test_am1_persistent_session.py",
])
```

Only mocked hardware/source logic ran in these suites. Recorded imports bind the
isolated candidate: 73 candidate imports, zero foreign compiled extensions and
zero robot imports outside the candidate. Tests remain separate from actual
physical receipts and measured response.

| Component | Historical lift-startup closeout staged or restored pin/state |
|---|---|
| Normal motor host | `f03de9c3b8c4f1d9e69b6951584119113ca4c5da`, actual matched restored normal source |
| Normal Pi helper | `8e6a0cf616cb2000df1d0e996ab27a19d2fb2fba`, matched restored helper |
| Isolated owner/gateway/local worker | `a1d1afe08414712fa21b3c983a2c0a8cfded26df` retained; physical services stopped, full physical history retained |
| Accepted simulated services | `c500204946a56862bb36ada3518ba5f248c02fa7`, original services active idle, original completed cb5 history retained, no autoplay |
| Isolated camera | `a0e94bc5298c021f785eaaee4ef0e2c44283f3d7` retained separately, same-run b0 release verified |
| Windows console/private config | Clean `e55d390bf6e957b9adc52ccf62b69476db12a20d`; original 1443-byte private config/e1 hash, owner-only ACL and CA trust verified |
| Normal onboard camera / strict observer / P1 capture | Normal camera `9b1f0670e7068f7d39eb50270a118e3807418355`; strict observer `7a266890703aae36f69a8d98e548d4c2e13710e5` clean; P1 source `2d9e365f1e302694633e7b1c916a38fb15cc50cc` and advisory histories unchanged |

**Historical lift-startup closeout state:** matched restoration and final audit qualified; no hardware
owners or uncertainty markers, original simulated services idle.

The final actual audit (attempt 02), SHA `b5a1a37d83e6c08e1213ebfefb05c1420299cf190b9c6609f5d1b0a39c955724`, qualified all seven
Pi Git heads and clean trees: normal motor f03, helper 8e, retained executor a1d,
separate camera a0e, normal camera 9b, accepted simulation c500 and strict observer
7a. Private configuration and calibration bytes/permissions remained unchanged;
the calibration retained its original 0664 mode. Both physical locks qualified,
with no serial, motor or camera owner and no unknown/in-progress markers. The three
original c500 services are active idle with the old cb5 completed history unchanged
and no new Start. Windows source `e55d390bf6e957b9adc52ccf62b69476db12a20d` is clean;
original 1443-byte e1 configuration, owner-only ACL and existing CA trust are preserved.
The first private audit refused after read-only Git inspection on a str/Path
AttributeError. Its original failure is retained; attempt 02 changed only that
private Path conversion and performed no product/control change.

Matched stopped rollback actually restored motor f03/helper 8e and original Windows
bytes/ACL, retained every physical failure/history and separate cameras, then
relaunched original c500 services idle without autoplay. A new uncertain cleanup requires genuine reconciliation before rearm/rollback.
No Boolean clear, whole-repository deployment or automatic task Start is permitted.

## Historical AM1-PHYSICAL-ADAPTER-01 result and restoration

AM1-PHYSICAL-ADAPTER-01 connects the Pi-owned authority to the existing protected
motor host. The implementation, focused review, native checks, camera-only
release check, exclusive stopped inspection and matched staging are complete.
The first actual physical startup failed the existing lift post-home stationary
qualification **before native Live or arm trajectory admission**. Physical
reliability is incomplete. Simulation remains the default.

Published for review in [draft PR #20](https://github.com/pickmanmike/lerobot_alohamini/pull/20),
explicitly dependent on accepted PR #19.

## Historical operating result

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

## Historical cleanup and sensing

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

## Historical implementation and preserved protections

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

## Historical executable versions and verification

Accepted dependency is PR #19 head
`24fd800ca22022f5e11333e42f37d1d6e7b8c5f3`, with the unchanged explicit stack
#19 → #18 → #17 → #16. The new dependent branch is
`codex/am1-physical-session-adapter`; no prior PR is merged or retargeted.

| Component | Historical staged executable pin | Scope |
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

## Historical ordinary use and component-specific rollback

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

**Historical closeout state:** this stopped rollback was executed after the verified
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
