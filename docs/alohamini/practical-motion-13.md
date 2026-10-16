# AM1 practical right-elbow motion — Packet13

**AM1 demonstrated three useful P16 out-and-back pairs, ordinary re-jog, browser reattachment within the same session, and a full working pair on the next Start.** The 10° recipe produced about 6.4–6.9° measured movement per direction, with 0.62–0.79° coarse return residual. Fine endpoint accuracy remains limited. [PR23](https://github.com/pickmanmike/lerobot_alohamini/pull/23) remains draft and unmerged.

## Physical results

The fixed task reference remained raw 1847. The inward-rounded outward target was raw 1960: 113 counts / 9.931640625° from that reference. Each gesture's measured starting position is separate from this task reference. At 4096 counts/revolution, the installed 827..3039 mapping gives 5.143660839863371 normalized units/s for 5°/s.

Run `5623b78d-51a2-4b9c-8503-3abb93402340` admitted actual q0 raw 1934. A supported initial return commanded 1847 and measured 1934→1856 (−6.86°), leaving 0.79° residual; it did not require exact positioning or reseed q0. The subsequent pairs were:

| Procedure | Outward raw / measured angle | Return raw / measured angle | Residual to 1847 |
|---|---|---|---|
| Repeat 1 | 1856→1931 / +6.59° | 1929→1856 / -6.42° | 0.79° |
| Repeat 2 | 1856→1934 / +6.86° | 1932→1856 / -6.68° | 0.79° |
| Repeat 3 | 1856→1932 / +6.68° | 1930→1854 / -6.68° | 0.62° |
| Next Start 1 | 1854→1932 / +6.86° | 1929→1856 / -6.42° | 0.79° |

The next Start was run `abdb4ccd-eec7-4f63-8c16-5b0648f73084`, admitted from actual q0 raw 1854. Both runs retained their own original 180s native deadline, original 250ms input expiry, fixed workspace/reference, and eleven fresh actual held Goals. Body/lift commands stayed zero after normal startup. The repeated run progressed 41.38s; the next Start 11.40s. Browser reattachment preceded the third pair and preserved the same run, host, service, q0 and deadline. Ordinary re-jog qualification took 905–1016ms in the repeat run and 1062ms on restart; these are control acknowledgement timings, not mechanical stop latency.

All six repeat and both restart endpoint windows retained actual delivered-target proof followed by at least two seconds of maintained target. Native active register reads corroborated all three outward 1960 endpoints and return 1847 endpoints, plus both restart endpoints. Goal acceptance, stored Goal, measured PP and procedure completion remain distinct. The private movement gates were ≥5° net outward response and ≤5° coarse return residual; actual measured values are shown above.

Both interactive procedures finished by normal Stop: native status `stopped`, first causes null, uncertainty false and `physical_readback_verified`. Native `execution.complete` remained false, as expected for interactive Stop; no finite program/deadline completion is claimed. All 20 torque/body-zero cleanup rows, qualified lift cleanup, closed buses, required-camera release and original P16/Lock0 restoration were verified. Sampled selected current peaked at 58.5 mA and 52 mA, respectively.

P1 recorded 238.1892s with no audio and released normally; original frames covering the current poses, repeated out/return and next Start were inspected. Its oblique view supports qualitative link movement; encoder feedback supplies the angles. Actual Pi-local images/delivery were matched to each run independently. Images, video, logs, enrollment and private configuration remain outside Git.

## Scoped operating correction

Explicit `physical-live-right-elbow-practical` selection separates actual per-run q0 from fixed command workspace 1847..2074: at most 19.951171875° one-sided travel from the batch reference. Calibrated limits 827..3039 remain unchanged; the measured margin is three degrees around that fixed workspace. Simulation remains the default.

The selected sampled host abort ceiling is min(500mA, ordinary 1800mA, actual firmware 2015mA). The installed Protection_Current 310 decodes at 6.5mA/count using the [STS parameter-table units](https://ftservo.github.io/ftservo-wiki/reference/parameter/memory-sts/). This provisional finite-test ceiling is not a current regulator or certified continuous rating. Thermal, voltage, status, firmware, growing-instability, ownership and input-expiry protections remain. Legacy modes retain their existing operating bounds.

Only this explicit practical mode records the low-current three-rises/static-position heuristic as an exact zero-added-read diagnostic. It permits the declared response opportunity after actual delivery instead of silently applying the old micro-response gate. The normalized-to-raw conversion and hidden relative-target clamp were corrected only for the selected mode; full 20° delivery is covered by focused tests, not claimed as a physical trial.

Useful P16/10° movement ended stimulus escalation. No 20° excursion, P24 or P32 trial was needed. Fresh practical requests remain run/host/source/config-bound and single-use. The existing volatile transaction keeps I0/D32 and preserved settings, verifies original P16 while Lock1, then restores original Lock0. Old Packet12 consumed requests and all earlier results remain unchanged.

## Preserved interruptions and repairs

The first practical run `ddd00ffc-6cdc-4683-a6b0-6a92b97d2618` completed two pairs and browser reattachment, then faulted while qualifying the third return gesture. A routine logging phase took 392.011ms in a 415.852ms owner loop; the retained five-slot temperature window correctly became stale. Its first cause and partial result remain preserved, with verified cleanup/restoration.

Executable `2e1a165614daf0181189e9f889f367a768199c9b` moves practical routine records into one bounded writer (128 records, 32KiB each). It receives immutable bytes, performs no motor/feedback work and writes a duplicate descriptor without stdout's text lock. Queue overflow/error is checked on the owner; zero/off and socket cleanup precede finite drain. Enabled periodic reports and exit messages use the same path. The 0.5s five-slot freshness rule was preserved.

Run `2ee1a209-fd05-40d9-b293-11aceac8c770` then stopped normally on a private runner assertion: it observed native Resume before the browser's 50ms continuation enabled the same held gesture. Its q0 raw 1929 left only 31 counts to1960; the +7 counts was not a full 10° comparison. The existing runner now waits for that same gesture inside its original deadline and uses a bounded initial return when current pose leaves insufficient working room. Neither repair renews a session deadline, invents input, requires manual positioning or changes permanent tuning.

## Ordinary use of the demonstrated recipe

1. From verified stopped cleanup, prepare a fresh authorized practical request through the existing stopped-owner procedure: P16, reference1847, direction+1. Consumed requests cannot be replayed.
2. Use the existing parameterized browser runner with known working amplitude 10°. It uses the enrolled page's Claim/Start, qualified Resume and current streamed gestures, including initial return when needed. For a next-Start check, select the same working amplitude for one full pair. The runner applies the demonstrated 10° client target cap; direct page controls expose the wider approved workspace, whose full physical travel was not exercised here.
3. Finish with normal Stop and verify known cleanup/P16/Lock0 restoration. The next Start measures actual rest; exact return or manual old-reference positioning is not required. The private use card contains the existing commands and separate pins.

The console was finally verified at the stopped restart run with the actual matched pins and served JavaScript, issuing no Claim, Start or input. The mutable runner configuration is PENDING and no request is queued. Persistent service/reboot startup restores an idle interface and never starts or replays motor activity.

## Verification and matched deployment

Initial affected native cohort: 544 passed / 0 skipped. Final host-only native file: 267 passed / 0 skipped in 7.53s. Final private runner: 18 focused passes. Conversion, mode isolation, current/health/expiry/Stop, parameter restoration and real payload shapes were checked; the combined independent review resolved the logging and runner findings. Overlapping cohorts are not summed; no full-repository or green-CI claim is made.

| Component | Installed pin |
|---|---|
| Executable candidate | `2e1a165614daf0181189e9f889f367a768199c9b` |
| Motor | `4c1771a55b6db27e55dbc729a861ecd6834c845e` |
| Runtime | `b70ec7d28d76d1b94a5b27e6f0a2c38b3d6436bd` |
| Manifest | `b17142c3d58315effa93614f4dd9746abd1f5af8d7f35eea86198fcb1391d25d` |

The host correction retained the runtime source and full bundle. Immediate full matched rollback is motor `b1c5fa0a5b799ccca8f7ed2afecbea206c4198b4` / the same runtime / manifest `2a9c1b7723dbed60ecd0773f30252a7c6a1591e2ee7a9e9f2b04601c34d01637`. Its full bundles remain retained with the older 40c/6df and original rollback pairs. Helper/manual 0fe3997f12b9cdfdd01fda6f4c6b102781252448, camera a0e94bc5298c021f785eaaee4ef0e2c44283f3d7, calibration, mappings, trust, enrollment and private permissions remain unchanged.

[Packet12](response-12.md) and [Packet11](live-reuse-11.md) retain their historical failures and limits. AM1 remains wireless, P1 advisory and AM2 unchanged. Durability means software fault tolerance, connection/control continuity, state preservation and recovery.
