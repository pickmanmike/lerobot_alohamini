# AM1 supported rest and right-elbow response — Packet12

**Supported rest admission and normal restart are demonstrated. The temporary P20 comparison produced +4/−8raw two-way response and ordinary re-jog, then a local current-rise fault interrupted repetition. Normal P16 useful response remains unqualified.** This continues [PR23](https://github.com/pickmanmike/lerobot_alohamini/pull/23), which remains draft and unmerged. The [Packet11 record](live-reuse-11.md) preserves all earlier failures and software repairs; [Packet10](live-arms-10.md) preserves its earlier coarse two-way movement.

## What the original evidence establishes

The original complete register replies from working run19d0f4f4 and zero-response runsba47f866/5977a85b were decoded without another hardware read. All33 register0..70 acquisitions and their33 separate address85 replies passed request identity, framing, width, byte-order, error and checksum checks. They agree on model777/FW3.10, position mode0, P16/I0/D32, Phase12, deadzones1/1, maximum/runtime torque limits1000, acceleration254 and zero Goal time/velocity. Voltage was11.8V; sampled status was zero. The zero-response intervals did not expire, clip the150mA guard or overwrite their active Goals. A stored Goal is not measured movement.

Original raw24 and25 are retained separately. The current [Feetech STS parameter table](https://ftservo.github.io/ftservo-wiki/reference/parameter/memory-sts/) describes them as separate one-byte parameters, while the repository has a two-byte alias. No write to either byte is proposed. That family document is not a firmware3.10-specific control-response model.

The working pair moved26/6raw ticks but left a substantial residual. Actual05 maintained Goal1867 from PP1845 for2.017s without motion; actual06 maintained Goal1834 for2.047s without motion. These completed windows cannot be explained by later Claim or connectivity incidents. They do not establish friction, damaged hardware, or a power failure.

## Fixed context correction

A passive stopped-owner read found three identical complete twelve-joint position samples. All sixteen torque registers and four body velocity Goals were off/zero, status was zero, mapping/calibration matched, and both buses closed. This was not a Start or movement trial. Fresh startup would copy actual PP into arm Goals, including left elbow2125 and left gripper3168 rather than their previously stored2124/3167.

The consolidated policy `am1-right-elbow-rest-neighbourhood-v2` accepts the following fixed observed resting-context box. It does not grow around later Starts:

| Nonselected Goal | Fixed allowed raw interval |
|---|---|
| Right shoulder pan | 2138..2142 |
| Left wrist flex | 1546..1547 |
| Right wrist roll | 2443..2444 |
| Left elbow | 2124..2125 |
| Left gripper | 3167..3168 |

The remaining six nonselected Goals and full calibration remain exact. Historical v1 descriptors remain recognized without rewriting their identity or verdict. Every run still freezes and checks its own eleven actual Goals exactly; no historical anchor is written to another joint. Selected right-elbow q0 is measured afresh and remains separate from the unchanged command workspace raw1834..1867.

The evidence comprises fifteen retained complete startup Goal maps, their same-run held readbacks, the fresh passive samples, and the current advisory P1 image. The image shows separated links and local open space for this small resting variation; occluded structure and cables prevent a metric three-dimensional clearance claim. Five one-coordinate spans total less than one degree at4096counts/revolution. This supports this modest fixed context, not expanded elbow travel or an unobserved pose.

## Conditional one-use comparison

Saved data support a small-error proportional-response hypothesis, not a demonstrated configuration mismatch. The one proposed change is **right-bus ID3, STS3215/model777/FW3.10, address21 P coefficient16→20**, with I0/D32, Phase12, deadzones, torque/force limits, acceleration and calibration unchanged. The primary family table identifies21 as the position-loop proportional coefficient. Increasing it25% does not imply a linear current, force or movement increase.

The comparison uses the existing sole protected owner and ordinary right-elbow page. An exclusive private one-use request must be consumed and bound to the actual run, host, source and context. Original settings must be durable before any control write. Torque is verified off, Lock1 keeps the parameter write volatile, and P20 is read back. The raw before=P16/Lock0 and active=P20/Lock1 evidence must remain truthful. Normal P16/I0 admission is unchanged and cannot accept a trial by accident.

Cleanup must verify torque off, restore/read back P16 while still Lock1, verify preserved settings, and only then restore the original Lock state. Failure keeps the restoration obligation and original fault. A next ordinary Start cannot replay the trial. No second value, sweep, persistent tuning or force/current/travel increase is authorized.

One finite positive-first out-and-back comparison is proposed from actual q0, at<=1normalized unit/s within1834..1867, with the existing sampled150mA guard, thermal/voltage/status protections, q0±3 measured guard, original250ms grants and original90s deadline. The maintained-target windows retain the existing runner's2s outward/4s return checks. Practical response requires at least3raw ticks in the intended direction and above measured noise. If the first directional gate fails, stop normally; do not repeat an unchanged trial. Two-way useful response is required before proceeding to ordinary release/new press/rearm, optional browser reattachment within the same deadline, normal finish and a short next ordinary Start.

The saved baseline shoulder pan2141 differs from current2138. This is a context-qualified comparison, not a matched physical-context causal proof. Even a promising P20 result would remain a temporary experiment; normal-installation and next-Start results must be reported separately.

## First physical setup and bounded continuation

At source206d6f4e, run `db4b992e-196f-4ec7-8c16-dc595aea4047` passed actual native startup and v2 admission at fresh q0raw1845. P20 was applied and enabled in measured hold. Before Resume, any grant or streamed target, the private runner asserted its obsolete v1-only workspace ID and requested normal Stop. This is a failed setup, not a response comparison or a movement pass. Original input sequence remained−1; the recorded90s deadline was retained.

The actual durable transaction and cleanup verified P16 restoration while Lock1, then originalLock0, with no restoration obligation or first failure. All sixteen torque readbacks were off, four body Goals were zero, and lift, buses and required camera cleanup were verified. Original source, request, marker, settings, deadline and failed result remain unchanged.

The runner now recognizes exactly v1/v2 while still requiring the exact configured context and all native proof. An additional three-line consumer guard permanently rejects a canonical nonce whose exact retired-marker archive exists, including a dangling symlink. It preserves the global one-use marker. A separately reviewed, explicit stopped-owner procedure may retire only this fully proved pre-input setup; once any Resume/grant/input exists, it refuses. A fresh nonce can then complete the same specified P16→20 response comparison. No new parameter value, automatic retry, old-input replay or altered original run is introduced.

## Verification and installed matched pair

Reviewed executable source `3409ce344c36944e18812134548f83b517f4837d` is the final comparison source: motor `ce8d8c1ad0470d0660d7bffeff397948e4341f78`, runtime `0e1ef30a5322b2675e317ae7f92828db5f5633f7`, manifest `40c8ceea73fb6c49ecc3cab7432f2406cc72d2c7a5f2eaa08346843db0290f29`. The archive-replay guard changes the same transaction module in both projections; all other rows retain the reviewed source. Root completed the matched stopped-stage phases with the late Windows motor-pin receipt and preserved private ACL/other pins. Services never autoplay physical input.

Independent source and generated-artifact review cover the bounded v2 context, one-use reversible transaction, real shared runtime module, exact source closure, original stopped locks/private authority/history/cleanup predicates and full rollback. The original206d native mock cohort passed637 tests with no skips; the exact3409 native archive-guard file passed72tests/0skips. The closed v1/v2 private-runner repair passed5focused browser checks after the retained actual-v2 RED result. Do not sum overlapping cohorts or treat software tests as physical qualification. The retirement adapter's five independent offline checks cover actual hash-bound original evidence and success/refusal/post-rename failure reporting. Actual explicit retirement archived only the exact pre-input nonce marker; original receipts/history remain retained. No automatic retirement or response-comparison retry API was added.

Immediate FULL matched rollback remains motor `674fb45328eef574b8598455a753e3ccf81845d3`, runtime `9d6d173c6f600c30be83bc756d69b41d3da02ffb`, manifest `6df3eefd9bafa3b9d19a453bc3e6ee72788052c2c8a394f0b6d4a39897c2f13b`. The original packet checkpoint e743/b4ec/1b6e and older histories remain separate and preserved. Projection, merge and publication SHAs are not deployment commands. PR23 remains draft/unmerged.

## Actual comparison: coarse two-way response, failed repeat

The first setup `db4b992e-196f-4ec7-8c16-dc595aea4047` remains FAILED: native v2/q0raw1845 and P20 measured hold succeeded, but the private runner's obsolete v1 assertion stopped before accepted Resume/grants/input (sequence−1). P16/Lock0 and complete cleanup were verified. This setup was not a response comparison; the reviewed replacement completed the same previously unperformed comparison with a fresh one-use nonce.

Replacement run `aa3f8f60-8f5d-417d-9d9f-60d8d68ff5b6` admitted q0raw1845, unchanged workspace1834..1867 and native17858.289457666→17948.289457666 (90s). Under temporary P20:

| Stage | Actual measured result | Qualification |
|---|---|---|
|First positive to1867|1845→1849, +4raw|Coarse directional gate met; completed2s declared maintained-target window|
|Ordinary release/new negative gesture|Qualified new ordinary gesture in872ms; no separate Resume click|Actual re-jog path acknowledged; not a mechanical stop-latency measurement|
|Negative to1834|1849→1841, −8raw|Coarse directional gate met; completed4s declared maintained-target window|
|After browser return/qualified same-run recovery, positive to1867|1841→1847, +6raw before fault|Third2s endpoint window interrupted; no completed repeat pair|

Native first-proof→last-feedback spans for the completed windows were2.042s/4.016s; watcher wall-observed spans were2.086s/4.085s. The clocks and boundaries are distinct. Same-owner register reads confirmed active Goal1867, Goal1834 and third-leg Goal1867; returned encoded writes, actual stored Goals, PP movement and advisory imagery are separate evidence. These modest responses are not precision convergence or proof that the gain alone caused improvement: physical resting context differed from the historical baseline.

The whole private procedure remains FAILED. The exact host cause was `RuntimeError: Live native static command adverse current rise`; outer `feedback_loss` does not establish a network fault. The unchanged guard detects three >=6.5mA chronological rises over>=0.2s with PP span<=2raw, independently of the150mA cap. Last logged PP stayed1847 while current rose26→32.5→39mA over approximately0.233s. These logged rows contain only two rises. The guard threw before the final selected sample was logged, so its exact triggering current/position operand is absent; do not invent45.5mA or claim a reconstructed complete fault window. No software false-positive bug, obstruction, friction, damage or permanent-tuning cause is demonstrated. The guard was not relaxed.

Cleanup verified torque off, restored/read-back P16 while Lock1, then originalLock0; restoration_requiredfalse. All16 torque registers were off, four body velocity Goals zero, lift qualified, buses closed and required camera released; uncertaintyfalse. The original fault remains retained (native hash `2a6ab818a015324b3b43eebf505e0f4dfcbc1a66142ffc8a7952bf0dcf8ff2dd`). P20 is not permanent tuning and no second response comparison is proposed.

## Actual normal P16 next Start

Separate ordinary run `fc1f0b55-455d-4601-95b0-f08927064fe4` used no trial request. Actual before/active settings were P16/I0/D32 with no P20 receipt. Fresh q0raw1847 re-admitted the same fixed workspace; native18165.634395504→18255.634395504 retained its own90s deadline. The918ms finite ordinary gesture returned an encoded Goal1854; measured PP remained1847 (0raw response). The sparse active Goal-register checkpoint read1851/PP1847; it does not independently read back the final1854. This was not a full maintained-target response leg.

The run's state is `NEXT_START_CHECK_COMPLETED_DIRECTION_UNQUALIFIED`: fresh baseline admission, qualified Resume, short input, measured hold and normal Stop completed; useful movement did not. Native statusstopped, both first causesnull, uncertaintyfalse; all20off/zero/lift/buses/camera cleanup verified. Typed native SHA `17e9ea4878d73bdbfdc9a76cd532c97900fb65528632ff6e6d1bc556e0d4f0d1`; locked audit current-physical-cleanup-qualified at68operations/58mutations/537transitions. Final read-only enrolled browser verification matched this stopped run, the installed ce8/0e1/40c pins and exact served JS5828fe5d9c599848c537fc16cd70c208a218155d2d1ee67a839d9c6833da9687, with no Claim, Start or input request. The existing runner configuration is PENDING.

## Remaining limit and next software step

Packet12 demonstrates a bounded temporary coarse two-way response and restored normal restart/admission, while repeated practical control and normal-P16 physical response remain unqualified. The permitted response comparison is consumed; no further parameter trial or useful ordinary-use qualification is claimed. Existing current P1 views show open links without a demonstrated obstruction; these few raw ticks are not visually resolved and occlusions remain.

The concrete next software diagnostic is to retain the existing acquired selected current/position and candidate chronology immediately before the static guard raises, using zero additional hardware reads. This diagnostic is prepared as a next step only: **not implemented or deployed**. It must preserve the original exception, all guards, deadlines and cleanup; it is not permission to soften the current-rise rule, increase gain/current/force/travel/dwell, repeat the consumed P20 comparison or merge PR23. All original failures remain visible.

## Retained operating state

All ten stage02 phases completed once. Full current674/9d6 rollback bundles are retained alongside the separate e743/b4ec packet checkpoint; completed phases are not replay commands. Helper/manual0fe3997f12b9cdfdd01fda6f4c6b102781252448, cameraa0e94bc5298c021f785eaaee4ef0e2c44283f3d7, calibration, mappings, trust, enrollment and private permissions remain unchanged. The Windows motor pin changed alone, with byte-inverse and exact ACL verification. Persistent services are available idle and never start or replay motor activity on service/reboot startup.

P1 advisory capture was verified as238.0888s, no audio, released normally, with original frames at2/170/188s inspected. It supports current arrangement only; the few-tick response is measured natively and not visually resolved. Required Pi-local observation was current during the run. No household imagery or private configuration is published.

Focused executable and generated-artifact reviews passed at their recorded sources. No full-repository/pre-commit or green-CI claim is made, and overlapping cohorts are not summed. AM1 remains wireless, P1 advisory, AM2 unchanged, and PR23 draft/unmerged. Durability means software fault tolerance, connection/control continuity, preserved state and recovery. No wear campaign is included.
