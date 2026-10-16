# AM1 supported rest and right-elbow response — Packet12

**Investigation in progress. Useful repeated right-elbow control is not yet qualified.** This continues [PR23](https://github.com/pickmanmike/lerobot_alohamini/pull/23), which remains draft and unmerged. The [Packet11 record](live-reuse-11.md) preserves all earlier failures and software repairs; [Packet10](live-arms-10.md) preserves its earlier coarse two-way movement.

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

## Verification and deployment

The runtime needs the same reviewed transaction helper as the separate motor component. A read-only check using the actual persistent owner interpreter, working directory and environment proved that its current runtime could not import that module. The target runtime therefore adds exactly that existing source file to its closed projection inventory, with the same hash as the motor projection; other source rows remain matched. No dynamic source loader is used.

Exact source review, focused test evidence, matched affected-component staging, trial results and restored idle state will be recorded here before closeout. Current deployment remains unchanged while that work proceeds. No physical trial under Packet12 has yet occurred.

AM1 remains wireless, P1 advisory, and AM2 untouched. Durability means software fault tolerance, connection/control continuity, preserved state and recovery. No wear campaign is included.
