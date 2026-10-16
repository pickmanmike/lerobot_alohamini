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

## First physical setup and bounded continuation

At source206d6f4e, run `db4b992e-196f-4ec7-8c16-dc595aea4047` passed actual native startup and v2 admission at fresh q0raw1845. P20 was applied and enabled in measured hold. Before Resume, any grant or streamed target, the private runner asserted its obsolete v1-only workspace ID and requested normal Stop. This is a failed setup, not a response comparison or a movement pass. Original input sequence remained−1; the recorded90s deadline was retained.

The actual durable transaction and cleanup verified P16 restoration while Lock1, then originalLock0, with no restoration obligation or first failure. All sixteen torque readbacks were off, four body Goals were zero, and lift, buses and required camera cleanup were verified. Original source, request, marker, settings, deadline and failed result remain unchanged.

The runner now recognizes exactly v1/v2 while still requiring the exact configured context and all native proof. An additional three-line consumer guard permanently rejects a canonical nonce whose exact retired-marker archive exists, including a dangling symlink. It preserves the global one-use marker. A separately reviewed, explicit stopped-owner procedure may retire only this fully proved pre-input setup; once any Resume/grant/input exists, it refuses. A fresh nonce can then complete the same specified P16→20 response comparison. No new parameter value, automatic retry, old-input replay or altered original run is introduced.

## Verification and deployment

The runtime needs the same reviewed transaction helper as the separate motor component. A read-only check using the actual persistent owner interpreter, working directory and environment proved that its current runtime could not import that module. The target runtime therefore adds exactly that existing source file to its closed projection inventory, with the same hash as the motor projection; other source rows remain matched. No dynamic source loader is used.

Exact source review, focused test evidence, matched affected-component staging, trial results and restored idle state will be recorded here before closeout. Source206d6f4e is staged as motor674fb45328eef574b8598455a753e3ccf81845d3, runtime9d6d173c6f600c30be83bc756d69b41d3da02ffb, manifest6df3eefd9bafa3b9d19a453bc3e6ee72788052c2c8a394f0b6d4a39897c2f13b. Its full matched rollback is the original e743/b4ec/1b6e pair from the packet. The archive guard is pending reviewed module-only staging; the response comparison remains unperformed.

AM1 remains wireless, P1 advisory, and AM2 untouched. Durability means software fault tolerance, connection/control continuity, preserved state and recovery. No wear campaign is included.
