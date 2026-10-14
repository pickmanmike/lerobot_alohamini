# AM1 live virtual left-elbow control — Packet09 result

One browser-origin target stream now controls the physical left follower elbow
through the persistent Pi owner and protected native motor host. The finite UI
exercise completed; the native live session **stopped** with known cleanup.
Target acceptance, servo goal evidence, measured movement and accuracy are separate.

Physical run `881200e3-98df-4012-ab0f-0569980d356e` used operating source
`751ba2dde82e32c54d177c3951ab0f1771b493d0`, from integration
`c5f7666c52d1e8fe4b44ed7a3ac6f81b25569ce1`. This new branch targets
`integrate/am1-local-teleop` without merge authority. Closed PR16–20 and main remain
unchanged. Later documentation commits do not change the tested installation.

| Physical evidence | Measured raw positions | Outward / reverse / original-reference residual |
|---|---|---|
| First live wave, including its reference hold | 2183 →2100 →2158 | 83 /58 /25 ticks |
| Fresh input after qualified expiry recovery | 2158 →2102 →2140 | 56 /38 /43 ticks |
| Fresh input after browser reconnect | 2140 →2124 | 16 /0 /59 ticks; measured reversal unproven |

The second and third starts are movement-accounting anchors. The task reference
remained raw2183; its q0−10..q0 corridor encoded2072..2183 (111 ticks). First-wave
movement meets the historical56 ticks in both directions, while its25 residual
exceeds return22. The post-gap reverse is38, below that historical56 score. Fine2
and coarse return remain unqualified. These limitations do not relabel accepted
input as measured movement or a completed finite Pi program.

A1022 ms target-production gap left the socket and controller presence alive.
The original Pi-issued sample expired at61369.462648014. Native detection followed
7.327 ms later; measured-hold writes started7.333 ms after expiry and completed
35.693 ms after expiry, with28.361 ms write span. This measures one expiry event;
250 ms is input validity, not a hard real-time stopping guarantee. Old motion was
discarded. Qualified Resume acquired the measured hold and fresh input produced
the second wave in the same run. Separate browser closure/reconnection required
new Claim/Resume and a new gesture, producing the third wave.

Two browser socket segments recorded797 submitted and797 accepted targets
(662+135), with no refusal or superseded ACK. The first segment mean input gap was
64.280 ms including the intentional gap (maximum2134.4 ms between submissions);
the reattached segment mean was59.860 ms, maximum91.4 ms. These browser-clock
cadences are separate from native Pi timing. Deduplicated native evidence contains211 sampled acceptances with grant ages
24.214..69.475 ms and212 sampled write checks at29.087..230.639 ms, below250 ms.
Sampled WebSocket bufferedAmount was zero. Missing samples
and transit variability preclude a complete latency maximum or cadence guarantee.

Seventeen independent selected-servo register blocks retained P16/I0/D32,
Phase12, offset1845, range864..3073 and the established settings. Sampled selected
current peaked71.5 mA under the unchanged150 mA diagnostic guard; temperature31°C
and status0 were observed. The cap is sampled protection, not a continuous current
regulator. Across227 distinct admitted snapshots, the largest nonselected normalized
deviation was0.084211; eight joints had zero sampled deviation. The213 snapshots
with full body commands had x/y/theta/lift exactlyzero. Measured indicators included
x±0.002214, y±0.001278, theta±0.585938 and lift10.294922..10.315430 mm. These are
feedback readings, not floor-travel or perfectly stationary-body qualification;
other eleven motors lacked continuous independent goal readback.

One Start, the original service/host/run/task reference and native Live interval
61345.930252494..61435.930252494 (90 seconds) persisted. Two post-initial
reacquisitions were acknowledged, at revisions5 and8. No new Start, rehoming,
old-key replay or deadline reset occurred. Other eleven joints retained protected
held commands; body/lift commands stayed zero after startup. Actual Pi-local
images retained the required observation policy. P1 had no camera-capable user
session; no new external imagery or visual elbow confirmation is claimed.

| Component | Actual qualified deployment |
|---|---|
| Motor | `a4bc18b6411d605f4f35bbc8bc5dc9f4a7568719` |
| Runtime | `f1649f4a8ffe030fb0a60eefb7d0fb449bd047bf` |
| Manifest SHA-256 | `cb1a9c47ab98889556fff912a5fc2a1a7f40398b43bbe2c5e2c6e60f7eb7835b` |
| Previous matched candidate, retained | motorA4 above / runtime `d56af98712bf477da847614d70a07fd4ef1fda3a` / manifest `e1607e99b52579d0e2c9c7b514a3ccf010842dba161589e8c04a975481fa92cf` |
| Packet09 first matched rollback, retained | motor `b68bfeee10df32c97865783bd4d37801f0006596` / runtime `52f1e75a308097ec38b390bdac3ed695d6cd83cf` / manifest `f35a2077a74f4c7c491a932be5a9a3700580d79da96fa4c6fb7f7c5f0ad7055a` |

Older576/0c026/27bc and earlier rollbacks, separate helper/camera/P1/Windows pins,
calibration, enrollment/trust, private permissions and original evidence are retained.
Current staging changed only the UI runtime asset; the native motor correction was
staged earlier. Rollback uses the existing matched stager with stopped owners and
literal current terminal evidence. Final native audit qualified all20 zero/off
readbacks, lift/bus cleanup and camera release, with uncertainty=false and no
native first cause. Retained terminal live-input metadata describes the last sample;
terminal status and cleanup establish stopped ownership. Three persistent services
remain available idle; their startup
restores the interface without starting or replaying motor activity. The enrolled
post-run read-only page verified the retained stopped run and exact served asset.

Select **physical-live-left-elbow** on the existing enrolled page, Claim input,
explicitly Start and qualify Resume. Hold the **Elbow − / Elbow +** buttons or
**[ / ]** for fresh mapped input. Release/blur/gap requires qualified Resume and a
new gesture. Pause and Stop take precedence; reconnect retrieves the original
operation instead of creating another Start. The90-second Live budget continues.
Normal finite presets and simulation remain available; simulation is the source
default. Broader arm selection, bimanual control, live body/lift and USB-leader
streaming remain unqualified and outside this slice.

Focused checks retain their actual versions and are not added together:

- Windows affected cohort:951 passed/17 skipped at `d9f73039` (one existing warning).
- Isolated native Pi cohort after the projection-import correction:642 passed/one
  Windows-DACL-only skip at `65bfa79c`; no foreign compiled/outside-candidate imports.
- Final serial browser cohort:141 passed/zero failures/zero skips at operating
  source `751ba2dd` (92.983 seconds).
- Final private watcher:28 passed/zero skips at helper SHA-256
  `55032fbfb40c5f0a3b4443a995da9038295e214e42f80e301bd3296112d73bd5`,
  including four Pause-race regressions.
  Independent focused review checks overlap this cohort and are not summed.

Independent changed-interface, native import, staging-scope, client and private
recovery reviews cleared. Full repository/pre-commit checks were unperformed;
main-only CI is not represented as green. Earlier failed browser startup cohorts,
wire-timing hypotheses and physical experiments remain at their actual versions.

Physical01 failed before Live because a motor-only projection imported a runtime
module; the native boundary now uses existing motor model keys, with a native
projection regression. Physical02 measured67 outward/zero reverse before genuine
expiry. Physical03 measured56 outward with no reversal reached before ordinary
view release; its disconnect cause remains unproven. The client now uses60 ms
physical sampling with one pending input, leaving headroom under the unchanged
40-TEXT-frame gateway bound; fake cadence stays100 ms. Original250 ms grants are
never renewed at later hops. A private automatic-recovery caller race discovered
before motion was fixed by carrying the captured hold revision/cause through a
synchronous guard at the ordinary Resume invocation; product/Core revision fences
remain authoritative. All original failed evidence is preserved.

Durability means software fault tolerance, state preservation, connection/control
continuity and recovery with minimal human intervention. AM1 remains wireless,
P1 advisory and AM2 untouched. No gain tuning, network change, hardware-endurance
campaign, new architecture, merge or additional documentation-only motor/reboot
run was performed.

The original implementation note follows at its recorded version.

# AM1 live virtual input09 implementation note

Packet09 starts at integration `c5f7666c52d1e8fe4b44ed7a3ac6f81b25569ce1` on
`codex/am1-live-virtual-09`, in the existing isolated managed checkout. PR16–20
remain closed; this branch targets `integrate/am1-local-teleop` without merge
authority. Software durability means fault tolerance, preserved state, control
continuity and recovery with minimal human intervention.

The explicitly selected `physical-live-left-elbow` uses the existing interactive
mode and protected backend. The sole client target key is
`arm_left_elbow_flex.pos`. The Pi completes eleven fresh held arm targets and
body/lift zero after normal startup. Admission freezes measured q0 and its
quantized q0−10..q0 corridor; same-run reacquisition refreshes the measured cursor
without changing that reference or the native 90-second Live deadline. Existing
150 mA, moving45/startup30, calibration, P16/I0/D32 and genuine local guards stay
in force. Coarse response, return22 and fine2 are separate reported results.

`am1_session_contract.py` adds the explicit recipe. `am1_session_core.py` keeps
connect → empty release → qualified Resume → grant → input with current identity
and increasing sequence, separately tracking applied expiry and grant renewal.
`am1_session_service.py` fences disconnect release to the exact live connection.
Fake list input and finite task viewer continuity retain their own semantics.

`am1_physical_executor.py` owns one immutable latest valid target mailbox. Its
apply contract carries run/service/host/controller/connection/revision/grant/seq
and the original Pi grant deadline, never a replacement lifetime. The native
`AM1LocalControl` in `alohamini_host.py` and device write boundaries in
`alohamini.py` enforce that expiry even if owner scheduling stalls. Finite marker
v1 stays unchanged; explicit live marker v2 includes original sample binding and
Live admission. Native expiry latches measured hold/body zero. Fresh qualified
reacquisition fences the old binding. Selected-joint evidence gets a narrow live
branch retaining current, temperature, status, setting and response protections;
finite waveform assertions remain finite-only.

`session_transport.js` exposes two elbow jog buttons on the existing page, samples
current gestures after a valid grant, and streams at nominal10 Hz. WebSocket
buffering, pending grants and input promises are bounded; no target backlog or
remembered gesture survives release, blur, reattach, revision or takeover. Input
acceptance is separate from native write evidence and measured movement. Status
and ACKs contain bounded scalar summaries; existing hash-bound details carry
full evidence.

Ordered execution: test-first source changes in three disjoint areas; focused
shared gateway/adapter/native and loaded-browser verification; independent review;
existing isolated native-Pi checks; reviewed stopped matched component staging;
one finite locally watched ordinary-UI physical session with live reversal,
input gap, qualified same-run recovery and fresh movement; truthful cleanup,
usable idle, focused unmerged PR and exact deployment/rollback publication.

The first matched rollback is motor b68bfeee10df32c97865783bd4d37801f0006596,
runtime52f1e75a308097ec38b390bdac3ed695d6cd83cf and manifest
f35a2077a74f4c7c491a932be5a9a3700580d79da96fa4c6fb7f7c5f0ad7055a. Older matched
rollbacks, private state, independent pins and original results remain preserved.
No physical result is claimed by this implementation note. AM1 stays wireless,
P1 advisory, AM2 untouched; startup never starts or replays motor activity.
