# AM1 live elbow selection — Packet10

**Bounded left/right selection and genuine right two-way control are demonstrated. Precision and comparable repetition remain unqualified.** Actual10 moved the selected right elbow 26 ticks negatively and 6 ticks positively through the ordinary page, then reattached and resumed the same run with fresh input. Post-reconnect negative response was only 1 tick; genuine input expiry interrupted the final positive return's 4s window. The private four-leg procedure remains **FAILED**. These limits are part of the result, not an all-trials-pass claim.

Current matched source`83dfe3c4ae23a989bdf11224858e6771bac190c6`, motor`e99da9a730f103cd196f6960e90e01b8da185992`, runtime`a5be47056c9be450ae099b05e5b9ecbf6958e919`, manifest`f3353726a11e5f4a86e43f7cf56dea556c4c5d1647bc8103e6e5698d63beebb6` is staged and available idle after known cleanup. All 10 matched phases completed; three persistent services remain active/enabled on the unchanged boot. Windows changed only the remote motor pin, preserving manual-helper/session pins and ACLs. The final read-only page check retained the stopped actual10 run, both choices and disabled body controls, without Start or hardware operation. The focused follow-up branch is `codex/am1-live-arms-10`, for review against `integrate/am1-local-teleop`; no automatic merge is authorized.

## Ordinary use

The controls are available idle. The recorded right-arm result qualifies its tested
interval. A fresh negative Start from final raw1845 would encode1811..1845, outside
the private1829..1870 admission: do not launch that shifted diagnostic without
supported clearance. Left operation retains its accepted scope. For a physical
run within its supported admission, use the following sequence.

When this matched physical candidate is served, use the enrolled page's **Claim input**, choose **Left elbow** or **Right elbow** in **Session recipe**, then **Start**. Select **Resume** and wait for fresh measured hold, required local observation and native acknowledgement. Hold a side-labelled negative/positive jog button or **[ / ]** for a new gesture. Direction means normalized target direction.

After release, blur or an input gap, qualify Resume and begin fresh input. After reconnection, retrieve the existing run, Claim input if needed, qualify Resume and make a new gesture; viewing or reconnecting does not replay keys or create another Start. **Pause** and **Stop** retain precedence. Active or uncertain runs freeze joint selection; another joint can be selected for a later explicit Start after known terminal cleanup. Existing finite presets and simulation defaults keep their behavior.

## Mapping and fixed limits

| Selection | Sole target key | Fresh-admission command corridor |
|---|---|---|
| Left elbow | `arm_left_elbow_flex.pos` | q0−10 through q0; unchanged |
| Right elbow | `arm_right_elbow_flex.pos` | q0−3 through q0; negative first, explicit positive return to original q0 |

The actual right joint is right-bus ID3, STS3215/model777, with drive mode0,
homing offset−658 and retained raw range827..3039. Its mapping is
`norm = (raw - 827) * 200 / 2212 - 100`; positive input increases raw Goal.
Phase12 and P16/I0/D32 remain unchanged. The three-unit width can encode34 raw
ticks after quantization; it is not an identical raw excursion to the left joint.

q0 is the selected joint's fresh measured starting reference. Admission freezes q0, corridor and original native deadline. Resume updates the measured cursor without reseeding q0 or renewing the deadline. Previous raw positions are historical measurements, not seeds for another Start. The same normalized step need not produce equal raw or visible movement on both joints.

Both exact allowlisted specifications retain **≤1 normalized unit/s**, the existing **150mA sampled selected-current guard**, **90s native Live ceiling**, and right measured guard **q0±3**. The cap is not a continuous current regulator. Calibration, EEPROM limits, gains, feedback/current/thermal/status, startup, moving guards, private ownership and identity fences remain enforced.

Only one joint receives targets. The other 11 acknowledged raw Goal values stay frozen; body/lift commands remain zero after startup. All 12 startup raw Goals require actual register readbacks before arm enable. Later owned holds verify the other 11 raw Goals and write only the selected measured hold. The left shoulder endpoint correction chooses representable Goal3223 inside unchanged848..3223 bounds while retaining actual measured3224 as feedback; it does not copy out-of-command-bound feedback into later holds.

The page uses nominal 60ms cadence, one unresolved input and no target backlog. Original Pi grants expire after 250ms through mailbox and native write checks; late delivery cannot renew them. Run/service/host/controller/connection/revision/grant/sequence fences bind every target. Nominal scheduling is not a guaranteed motion or stop latency. Release/expiry means measured hold and bodyzero, not automatic return to q0. No hidden compensation, overshoot, catch-up or rehoming is introduced.

The exercised negative-first diagnostic retained width 3, rate/current/gains and measured guard. Its private bench admission required both raw endpoints inside already measured matching-pose1829..1870, with right five held-pose/calibration/native-held provenance; otherwise Stop. Historical command extrema or a calibrated range alone do not demonstrate new clearance. No manual repositioning or enlarged limit is part of this comparison. Bimanual input, live body/lift, physical USB leaders, inverse kinematics and precision positioning remain outside this extension. AM1 stays wireless, P1 advisory and AM2 untouched.

## Read evidence separately

Browser submission, authority acceptance, native acceptance, encoded returned write, actual servo Goal register, measured Present_Position and imagery are distinct. A sync-write return is not a servo ACK or movement proof. An actual active Goal read closes a delivery question without proving physical response. Current-target error is PP minus the current commanded Goal; original-reference residual is PP minus immutable q0. Release changes the current goal to measured hold, so these errors can differ.

A return result needs a declared target, proof it was actually commanded, and a bounded response dwell with that target maintained. Truncated return commands or windows remain unproven. Native encoder response and local image availability must not be presented as visible elbow motion. Required Pi-local images and advisory P1 are separate; a room view without the elbow is not clearance confirmation.

## Retained physical outcomes

Earlier failures remain failures at their own source/pins; later repairs do not upgrade them. Trial08 retains the old positive q0..q0+3 corridor. New negative metadata does not rewrite its stored corridor, reference, receipts or deadline.

| Trial / run prefix | Recorded outcome | Delivery/response and cleanup limits |
|---|---|---|
|01 / fc99fec3 | Pre-Live shoulder endpoint refusal; no right jog | Goal/PP3224 vs unchanged command max3223. Original unknown-cleanup first cause retained. Separate fresh read-only20-off/lift/thermal/calibration proof and ordinary Reconcile resolved uncertainty; it did not make the run pass.|
|02 / cc8088d4 | q01817; outward+12; return interrupted | Actual outwardGoal1850. Return stopped at encoded1831 before q0; no q0 dwell. Private expiry/hold-ACK race; ordinary Stop/known20-off/lift/bus/camera cleanup.|
|03 / 60bd3d05 | Pre-Live post-home acquisition refusal | Original20 samples retained; home813, firstquiet814, later814..815. No right response. Known cleanup; no invented missing stationary samples.|
|04 / 26c21ff9 | q01829; +12 outward/−2 reverse | Return encoded1829, PP1839/residual+10; no active q0 register read retained. Private3tick metric failed; no reconnect comparison. Known cleanup.|
|05 / 1ecc77a5 | q01839; +8 outward/zero reverse | Full4.091s return opportunity at returned encoded1839, PP1847/+8 residual; actual active q0 register missing. Stopped direction-unqualified; known cleanup.|
|06 / aa27e510 | q01847; +11 outward; no return | Private proof refusal about2.2s into intended4s outward dwell. Exact rejected snapshot/client pair was not retained; later catch pair passed. Later expiry must not be invented as that missing first cause. Known cleanup.|
|07 / e0d5ff57 | Pre-Live broad Moving-bit refusal | PP815/814/815, signedvelocity−50/−50/+50, Moving1. Genuine nonquiet feedback, not demonstrated continuing directional motion. Original failure/known cleanup retained.|
|08 / 5ecadc4f | q01858; +12 outward; release passive−1; fresh reverse−1; reattached second outwardzero | Actual activeGoals1891→1858→1891 verified. Full4.113s return stayedPP1868; targeterror=q0residual+10. Repeat2.006s stayed1868, targeterror−23/residual+10. Ordinary Stop before second return; procedure incomplete, known cleanup.|
|09 / b6a54884 | Initial native held, no jog | Private admission required a later active/readiness-only marker. Actual12startup/11hold proof existed; no Resume/input. Ordinary Stop/known cleanup. |
|10 / 19d0f4f4 | First26negative/6positive; after reconnect1negative; final return interrupted | Actual active returnGoal1867 read in both pairs. First4.134s response completed; final4s incomplete due genuineexpiry. PrivateprocedureFAILED/known cleanup. |

Trial08's actual ACTIVE original q0Goal1858 delivery closed an earlier evidence gap but did not establish useful repeated response. Its sampled peak39mA and held-joint/camera facts remain observations, not a new mechanical diagnosis. Strict typed terminalSHA`6db54487a494af7e14e94597e2da0cc166ae010c658dafe26ed2f265622864bd` retains known cleanup. Its room/chairs image lacked a clearly visible elbow. The original positive-corridor history is preserved.

Post-home version3 retains original home reference/one-second deadline, one frozen first-actual-quiet acquisition, the fixed one-tick/velocity≤50 stationary band, same-sign velocity and continuing-displacement guards, all fault/freshness/thermal/height protections, and Moving-without-velocity refusal consistent with existing native checks. Exact replay/negative tests exercise mixed-flag handling. Captured07 prefix remains failed; no final samples are invented. Actual08 physically qualified **strict_quiet** (five815/velocity0/Moving0 over0.210s), so it does not physically validate the mixed-Moving bounded fallback.

## Accepted Packet09 left reference

The [Packet09 report](live-virtual-input-09.md) remains authoritative for accepted left run`881200e3-98df-4012-ab0f-0569980d356e`, operating source`751ba2dde82e32c54d177c3951ab0f1771b493d0`, original q02183/encoded2072..2183 and90s deadline.

| Segment | Measured raw positions | Outward / reverse / q0 residual |
|---|---|---|
| First wave |2183→2100→2158|83 /58 /25 ticks|
| Fresh input after expiry recovery |2158→2102→2140|56 /38 /43 ticks|
| Fresh input after reconnect |2140→2124|16 outward /reverse unproven /59 ticks|

Its797 submitted/accepted targets are not797 independent servo outcomes. One measured hold completed35.693ms after expiry, not a general latency guarantee. Same-run recovery/fresh motion and known20-off cleanup were demonstrated; P1 imagery was unavailable. The final59 residual follows outward motion without demonstrated reverse, not proven uncontrolled drift. Fine2/return22 and final-wave reversal remain unqualified and are not universal right-joint requirements.

## Verification, source and rollback

Cohorts overlap and must not be summed. Latest exact source83/b7 checks:

|Check|Result and scope|
|---|---|
|Isolated native candidate|397passed/one Windows-DACL-only skip/112.07s, nativePython3.12.13/pytest8.4.2.61source/helper/test hashes,16context files,8executed test files; contract+four Python tests overlaid/56paths retained. Pre/post candidate imports exact; foreign compiled/candidate-outside lists empty. Injected IO, not physical motion.|
|Finite private watcher10|74passed/0skip/26.579s independent offline Node run; helperSHA`fc262021d86da85af03b9ea1ef9cbcf85c55e4f2ea8b5977b6400433163d7e47`, testSHA`815ce5a2d0076f84e1f0c91a542bb75cf05223d82a140ce711e111596cf9f61f`. No hardware in tests. Its actual10procedureFAILED remains separate.|
|Bound matched build/stager/wrapper tools|169passed/0skip/6.39s with mocked actors; original proofs/locks/identity/history/rollback/Windows gate retained. No actual deployment in this test.|

Both-side mapping, native expiry/wrong-key/replay fences, existing left/finite behavior and loaded browser fresh-intent/reattachment checks remain source-backed evidence. Actual served-page verification complements injected protocol tests; neither test counts nor accepted input counts are servo outcomes. Earlier overlapping logs retain their own source caveats privately; no full-repository/pre-commit/mainCI green is asserted.

The staged new motor/runtime pair changes the shared contract in **both** projections to SHA`b7e0514af28850edb6ec670a5e6efc393a6737fa74aa287810707557ecc59ffe`; other runtime files retain their exact map. Preserve matched previous1f17/74bd/2edd bundles and complete history for stopped-owner rollback. Helper`0fe3997f12b9cdfdd01fda6f4c6b102781252448` and camera`a0e94bc5298c021f785eaaee4ef0e2c44283f3d7` remain separate pins, as do P1/manual-console configuration, trust/enrollment and calibration. Startup restores the interface without autoplay, Start or old-key replay.

Retained matched rollback identities (restore only while stopped through the
existing reviewed procedure; preserve both bundles and current terminal evidence):

| Checkpoint | Motor | Runtime | Manifest SHA-256 |
|---|---|---|---|
| Immediate prior | `1f17a6e7ca99135eaad4c8dfec28b7bed10b60d3` | `74bd624d24521f8bdc4a6417b9217ef9fef615d7` | `2eddff6a8a06a754f68d1b8b818c4542a109457668ca686a5242d96ae1189fc4` |
| Earlier Packet10 | `0e18dcce57fe0ea73828bddbb556ce81158712ed` | `74bd624d24521f8bdc4a6417b9217ef9fef615d7` | `7a53887405129fe288fd1ed2837c68451eedf7d170556ecc43fc9cc7f63745f0` |
| Accepted Packet09 | `a4bc18b6411d605f4f35bbc8bc5dc9f4a7568719` | `f1649f4a8ffe030fb0a60eefb7d0fb449bd047bf` | `cb1a9c47ab98889556fff912a5fc2a1a7f40398b43bbe2c5e2c6e60f7eb7835b` |

[PR21](https://github.com/pickmanmike/lerobot_alohamini/pull/21) was ordinarily merged to integrate/am1-local-teleop at`48a3ed39b7591837ed6771a586daec7c1b8b5b8b`. This follow-up is codex/am1-live-arms-10; integration is separate from deployment. It grants no automatic merge authority; main and closed PR16–20 milestones remain outside scope. Root alone handles Git/SSH/matched staging/devices. Rollback and staging affect only stopped owners after known cleanup, preserving first causes, private evidence, exact separate pins and both matching bundles. Software continuity is not mechanical endurance.


## Actual10: response, expiry and limits

Run`19d0f4f4-4d2d-4212-9d13-95c9ce0b5ec6`, operation`f514c6e9-6da0-4306-b49a-43273858c10f`, service`1c64c5e2-0c56-469a-bbfd-dec83b3974dd`, host`9833b592-51d0-45d8-b3a5-a270d37d5f99` used the final pins above on 2026-10-15 UTC (2026-10-14 New York). One Start retained q0raw1867/encoded1833..1867 and nativeLive129209.375769596/deadline129299.375769596 across ordinary release, browser reattachment and three qualified Resumes.

|Segment|Target and measured response|Response opportunity|Current-target error / q0 residual|
|---|---|---|---|
|First negative|Encoded1833 returned; PP1867→1841,26negative|2s declared/2.011s observed;61native samples/2.008s|+8 /−26ticks|
|Explicit positive return|Actual ACTIVEGoal1867 read; PP1841→1847,6positive|4s declared/4.134s observed;123native samples/4.097s|−20 /−20ticks|
|Fresh negative after reconnect|Actual ACTIVEGoal1833 read; PP1846→1845,1negative; preceding held1847→1846 is separate|2s declared/2.024s observed;65native samples/2.141s|+12 /−22ticks|
|Final positive return|Actual ACTIVEGoal1867 read/PP1845; no observed reverse in prefix|4s declared; last fully accepted proof0.870s/27native samples; rejected pair about1.041s after first proof, then expiry|−22 /−22ticks before expiry; completed return unproven|

First negative endpoint1833 has encoded-return proof but no retained actual endpoint register read; sparse early reads were1865/1856. First return actual1867 read at129221.291359174 showed PP1843/current26mA/signedload−104/velocity+50/Status0 before1847 plateau. Post-reconnect actual1833 at129230.532938106 showed PP1845/current13mA/load+60; final actual1867 at129236.618328205 showed PP1845/current26mA/load−104. Delivery is therefore separate from weak/asymmetric measured response. Signedload is not signedcurrent; no friction, collision or precision diagnosis is inferred.

The exact rejected pair contains an older active capture129237.684571965 and a later client probe. Write852/Goal1867 was valid at capture, but its original grant expired129237.707650363. Native detection129237.712140765 led to measured hold completed129237.727461956, **19.812ms after expiry**—one observed timing, not a stop-latency guarantee. Core recorded input_expired/revision13. A refused next grant cleared the client's gesture/cursor; an older valid native sample does not make the later response window continuous. Attempt853 returnedfalse; later actualGoal1845 is protective measured hold. No replacement4s window or expiry recovery occurred, and the watcher remainsFAILED. Original q0/deadline were preserved.

All 12 startup Goal readbacks and14 holds/154 other-joint comparisons preserve frozen11. Sampled selected current0..39mA, with no gain/limit/cap increase.84 accepted body/lift rows arezero;105 measured rows retain one small quantized nonzero body row. Startup again physically qualified strict_quiet (five815/velocity0/Moving0 over0.209s), not mixed-Moving fallback.

Ordinary Stop yielded stopped/nofirstcause/uncertainfalse, actual 20 off/zero, liftqualified,busesclosed and camera released. Strict terminal typedSHA`ba51d2633edcc7e532ee9e805f915ee0efd1821f742db5b7e4f17ae60f0ca2e5`,56operations/39mutations/446transitions. Same-run PiJPEGsequence652 SHA`754730d13266c81723e59823cbfd198a8adfaa974124ee8ad4151c0cc3631993` was delivered; official inspection shows room/furniture without a visible elbow. Advisory P1's same-owner inventory had0camera-capable sessions. Encoder response does not establish image-based elbow motion/clearance; household imagery stays private.

Final read-only UI loaded transportSHA`0a40c3f2fe0996c13a032eb410d42d52648592b465080b73b4bc22f76af50967`, both selections, disabled body inputs, current stopped run and no operation/error. Interface availability and known cleanup are separate from a passed four-leg procedure. Both selections remain available; the existing left default/manual behavior is unchanged. A new negative diagnostic from current raw1845 would encode1811, outside this private1829..1870 qualified interval. Interface selection is not permission for blind next Start, manual reseed or range growth.

The bounded feature is genuine selected-right two-way response and appropriate exercised reconnect continuity, with the accepted left milestone unchanged. Comparable repetition, full return, fine positioning, bimanual/body/leader use and mechanical endurance remain unqualified. The single next practical extension is a less cumbersome deliberate re-jog/rearm interaction with fresh intent inside the original session limits, preserving expiry/ownership/no replay; it is not a gain/force/range experiment.

## Publication boundary

This focused branch is for ordinary review against `integrate/am1-local-teleop`.
No merge of this follow-up or `main` is authorized. Operating source and separate
component pins above remain unchanged by documentation commits. Preserve the
actual09 admission failure, actual10 FAILED procedure, interrupted final window
and all earlier outcomes. The demonstrated bounded capability does not turn the
private four-leg procedure into a passing run.
