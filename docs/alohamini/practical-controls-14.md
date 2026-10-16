# Practical elbow controls 14

Packet14 completed ordinary page control of either AM1 elbow, selected before Start and operated one at a time. PR [#23](https://github.com/pickmanmike/lerobot_alohamini/pull/23) merged normally into `integrate/am1-local-teleop` at `2bbd054dd0504075ea2994b946bd1cb27bb155c5`; its resulting tree exactly matches accepted head `9696a47c`. No integration-only powered test or redeployment was needed. This focused follow-up remains unmerged.

## Ordinary use

On the existing trusted session page: **Claim input → select Right elbow practical P16 or Left elbow practical P16 → Start → wait for the qualified native hold → Resume**. Hold the decrease/increase elbow control, or `[` / `]`, to stream current targets; release, then press again to re-jog. Use normal Stop before changing selection or deliberately starting another run. A reattached browser uses Claim input and qualified Resume, followed by a new gesture. Page opening, Claim, reconnection and idle service restoration do not initiate a return or replay movement.

Each preset is P16/I0/D32, nominal 10° at approximately 5°/s. The 113-count encoded command arc is 9.931640625°: RIGHT raw 1847..1960, LEFT raw 2011..2124. Left uses actual ID3 on the left bus, drive mode 0, offset 1845 and range 864..3073; right uses ID3 on the right bus, drive mode 0, offset −658 and range 827..3039. Rates are 5.150646345757256 and 5.143660839863371 normalized units/s respectively. Ordinary targets are enforced in page, owner and native validation. Broader historical 20° diagnostics retain their distinct experiment identity and consumed requests; no 20° or higher-gain experiment was performed here.

A normal Start needs no externally prepared experiment file. The authenticated allowlisted recipe and fresh run/host identity supply the existing native binding; arbitrary gains, raw register writes, paths and validation hashes are not client inputs. Original operation deduplication and single ownership remain.

Fixed command references are separate from actual measured q0 and each gesture cursor. The fixed combined bench context permits the two limited elbow workspaces and retained other-joint context; every run freezes its actual eleven other Goals exactly. The selected measured-state margin remains 34 counts (about 3°), separate from the 113-count command corridor. Starting near the upper endpoint can use either allowed direction; no precise return or automatic preparation return is a Start prerequisite. No preparation return was needed in this sequence.

## Physically measured sequential check

All five pairs delivered their targets and maintained the native comparison window for at least 2 s. Values below are native encoder degrees, with signed outward/back movement measured from each leg's actual start. They distinguish achieved movement from the command and its endpoint errors.

| Check | Achieved outward | Achieved back | Outward endpoint error | Return residual to fixed reference |
| --- | ---: | ---: | ---: | ---: |
| RIGHT initial pair | +6.680° | −6.416° | 2.461° | 0.791° |
| LEFT pair 1 | −8.262° | +6.064° | 1.670° | 2.197° |
| LEFT pair 2 | −6.064° | +6.240° | 1.670° | 2.021° |
| LEFT pair 3 after browser return | −5.977° | +5.977° | 1.934° | 2.021° |
| RIGHT next Start after LEFT | +6.855° | −6.855° | 2.285° | 0.615° |

RIGHT01 (`77dda0c6-d2ee-4ef7-8401-e668082ebf3f`) measured q0 = 1856 and progressed 11.02 s. LEFT02 (`951cd4ef-0516-4050-b2e4-7d3d7f7f947a`) measured q0 = 2124, completed three pairs / four ordinary new-press rearms, and progressed 36.71 s. Its browser closed after a measured release hold; a new page, Claim and qualified Resume preserved the same task, original reference and deadline, then a new gesture produced pair 3. RIGHT03 (`fdff9b5f-39e7-4425-9016-2da9e81b910a`) admitted q0 = 1856 with the actual nonselected LEFT Goal 2101, rather than requiring its old 2124 reference, and progressed 11.20 s.

Each original native deadline remained 180 s from Live. These are short observed procedures, not 180 s of continuous operation. All three ended by normal Stop with null first causes, known cleanup and `execution.complete=false`, as appropriate for interactive Stop. All measurement procedures completed; none claims finite-program completion. Native delivery windows were 2.004–2.013 s; watcher first/last snapshot proof spans alone are not the native dwell clock.

Native read-only before/active proofs verify P16/I0/D32, exact mapping and unchanged settings, including actual Protection_Current = 310 (2015 mA). Selected sampled peaks were 52 mA, 58.5 mA and 65 mA; the retained short-use host ceiling is min(500 mA, ordinary 1800 mA, actual firmware ceiling). This is an abort threshold, not a current regulator or continuous motor rating. All 20 torque/body-zero cleanup readbacks, qualified lift, bus closure and required-camera release were verified. Startup settings evidence is distinct from cleanup readback; no new gain transaction was used.

P1's released 285.266 s / zero-audio advisory clip and five hash-verified originals visually corroborate coarse link displacement and return in the left and final-right windows. The upward oblique view does not establish angles or fine convergence. Actual current Pi-local images remained required and were retrieved with matching native delivery evidence. These physical runs used real motors, feedback and cameras; offline verification used simulated hardware.

## Verification and retained limits

Focused source review cleared changed enforcement/lifecycle code. Windows affected suites: 549 passed, 2 platform skips, one existing aiohttp warning; browser/client suites: 167 passed. The existing isolated Pi harness: 550 passed, 1 platform skip, no foreign robot imports/compiled files, device access or service changes. The reused parameterized runner passed 21 focused checks, including corrections for selected-side raw decoding and stopping an insufficient first ordinary response without unchanged repeats. One stopped staging pass installed the affected motor/runtime set. Private logs, images, recordings and credentials remain outside Git.

Fine positioning remains limited, including right undertravel and left return residuals. Bimanual/all-joint control, floor driving, physical leaders and long-duration operation remain outside the demonstrated claim. Software durability means fault tolerance, connection/control continuity, state preservation and recovery, not mechanical wear certification. AM1 remains wireless, P1 advisory, AM2 unchanged; original status/thermal/voltage/current/freshness/ownership/250 ms input-expiry protections and bounded routine writer/cleanup ordering remain.

## Source, deployment and rollback

Exercised source: `bea11a7925ef439eaa91da5c76a71f43fe557221`. Later publication changes are documentation only.

| Component | Installed |
| --- | --- |
| Motor | `5c97a39176b903e0c8762ed07e82113dc5a49b83` |
| Runtime | `978fadf4a4c41eb8a006dae283b337a0f47c10a0` |
| Manifest | `b8abad25e0233d152b8c87ab1d08531e6a73d63ee5942b1e29cd33ce60560aed` |
| Helper/manual, unchanged | `0fe3997f12b9cdfdd01fda6f4c6b102781252448` |
| Camera, unchanged | `a0e94bc5298c021f785eaaee4ef0e2c44283f3d7` |

Immediate full matched rollback is motor `4c1771a55b6db27e55dbc729a861ecd6834c845e`, runtime `b70ec7d28d76d1b94a5b27e6f0a2c38b3d6436bd`, manifest `b17142c3d58315effa93614f4dd9746abd1f5af8d7f35eea86198fcb1391d25d`. Complete matched bundles/configuration are retained; older b1c5fa0a / b70 / 2a9c1b77 and earlier rollback records remain. Calibration SHA256 `f3ffa9d5d531fe0213c8847f28baef292de027996f0a0267e0657a8c45037394`, private permissions, trust, camera/helper/manual pins and original results are preserved. Windows motor-pin inverse bytes and ACL were verified.

Final enrolled-page check matched served JS `aae6e8b0937e5e6048c251f5032d6344cc0672cf8a3fc88b9d70adc8b9201973`, stopped RIGHT03, installed pins and available ordinary preset, without Claim/Start/input. Persistent services restore the interface idle; the runner is PENDING. No reboot or endurance test was repeated for this packet.
