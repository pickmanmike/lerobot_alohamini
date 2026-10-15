# AM1 ordinary re-jog and retained workspace — Packet11

**The ordinary re-jog software and retained right workspace are implemented. Useful physical response remains unresolved.** Completed right-arm trials delivered and acknowledged both supported endpoint Goals, but measured position stayed at raw1845 through each completed two-second response window. Physical ordinary re-jog and browser reattachment were therefore not reached. The short next-Start check was not executed because its read-only preflight could not reach the Pi.

[PR22](https://github.com/pickmanmike/lerobot_alohamini/pull/22) merged normally at `5016bc9850d0a37bc12b8244ad832f0214cc8d96`. That integration is separate from the Packet11 executable and deployment recorded here. The new follow-up remains unmerged. [Packet10](live-arms-10.md) remains the historical record of its genuine two-way response, weak post-reconnect response and failed four-leg procedure; those outcomes have not been replaced by this report.

## Ordinary use

These are the implemented controls, verified with both selected sides in browser/protocol fixtures. The physical response limitations below still apply.

1. In the enrolled ordinary page, **Claim input**, select **Left elbow** or **Right elbow** in **Session recipe**, and explicitly **Start**. The idle right-workspace description is a candidate requiring current native admission. After Start, check the actual admitted bounds and fresh measured q0.
2. Explicitly **Resume** and wait until the page enables the selected jog controls after fresh measured hold, required local observation and native acknowledgement. An accepted Resume response alone is insufficient. An early press is ignored, not remembered.
3. Hold the selected negative/positive button or **[ / ]** for a new gesture. Positive means increasing the selected normalized/raw Goal. Release the actual key or pointer to hold. While the same connected controlling session remains eligible after this voluntary release, a new deliberate press performs qualified rearm without a separate Resume click. Qualification is cancelable and bounded to three seconds from that press; targets remain inactive while it is pending.
4. After initial admission, explicit **Pause**, genuine input expiry, blur/page hiding, connection loss, ownership change or fault, use explicit qualified **Resume** and a fresh gesture. Retrieve the existing run and Claim input if needed after reconnecting. Viewing or reconnecting does not replay input or create another Start. A fault must retain its cause and follow supported recovery.
5. **Pause** and **Stop** retain precedence. Stop normally when finished. Joint selection stays frozen during an active or uncertain run; select another joint only for a later explicit Start after known terminal cleanup.

Normal keyup/pointerup is distinguished from cancellation or unexpected lost capture. Pointerup followed by lost capture releases once; key repeat and old release events cannot replay or cancel a newer gesture. Releasing during delayed qualification, a later Pause or any incompatible identity/geometry change cancels the pending gesture. Ordinary release eligibility never overrides strict loss recovery. Existing finite presets and simulation retain their behavior.

The implementation is in [session_transport.js](../../tools/am1_console_ui/session_transport.js) and [am1_session_core.py](../../tools/am1_session_core.py). The browser sends `release_kind: 'voluntary_jog'`; the owner returns `live_input.rearm` eligibility bound to the released intent revision. Only that exact release can survive the owner's own connect/empty-release/Resume sequence through `rearm_of_intent_revision`. Run, service, host, controller, connection and immutable workspace/deadline checks remain authoritative. A late accepted reply cannot revive a canceled gesture.

## Workspace, reference and unchanged protections

| Selection | Sole target key | Command workspace |
|---|---|---|
| Left elbow | `arm_left_elbow_flex.pos` | Original q0−10 through q0; unchanged |
| Right elbow | `arm_right_elbow_flex.pos` | Retained raw1834..1867, normalized −8.951175406871613..−5.967450271247742 |

The right width is `2.983725135623871` normalized units, inward from the evidence interval raw1833..1867. Its workspace ID is `am1-right-elbow-retained-v1`; current context SHA256 is `75e93cad4c958797494679e1a5e3f9c992587004a53ef2f6aa900e38911b0fcf`. The right joint remains bus ID3, STS3215/model777, drive mode0, homing offset−658 and calibration range827..3039:

`norm = (raw - 827) * 200 / 2212 - 100`

q0 is the actual fresh measured origin, separate from the fixed workspace. Runs05/06 admitted q0raw1845 (normalized −7.956600361663661), leaving 11 raw ticks toward the minimum and 22 toward the maximum. A re-jog, Resume or reattachment refreshes its measured cursor while retaining the original q0, admitted bounds and native deadline. Another Start measures its own origin and must pass current admission; it does not seed an old pose or shift this workspace into new travel.

Admission requires the retained calibration/context and fresh measured alignment. Two narrowly fixed Goal tolerances account for the demonstrated startup context differences: right wrist roll is anchored at raw2443±1, left wrist flex at raw1546±1. The other nine nonselected Goals and calibration remain exact. These anchors do not ratchet across Starts. All eleven *actual* nonselected Goals are captured and held unchanged within each run; the policy does not write the anchor values in their place. A one-tick difference does not prove identical physical pose, establish its physical cause or establish full-arm clearance. Earlier context hashes and refusals remain historical.

Both sides retain **≤1 normalized unit/s**, the **150mA sampled selected-current guard**, the original **90s native Live ceiling**, and the right **q0±3 measured-motion guard**. The current cap is a guard ceiling, not a continuous current regulator or measured applied force. The nominal60ms page sender, one pending physical input, bounded transport buffer and latest-only behavior are unchanged. Original Pi-issued250ms grant expiry is carried unchanged through native write checks; the browser neither subtracts clocks nor renews old intent. Phase12, P16/I0/D32, calibration, status, feedback, thermal checks, ownership, separate component pins and body/lift restrictions remain in force. No gain, force, travel, network or simultaneous-bimanual change is included.

The closed workspace descriptor remains five fields; the idle supported-workspace summary has eleven, including actual command endpoints and current-admission requirement. See [the contract](../../examples/alohamini/am1_session_contract.py) and [native live tests](../../tests/robots/test_am1_live_native.py).

## Demonstrated corrections

- **Browser qualification:** fast re-press before release acknowledgement preserves only the matching currently held new gesture. Explicit Resume keeps its bounded qualification token through an accepted result arriving before the current running/native-ACK/cursor snapshot. Controls enable only after that current evidence arrives. Both result orderings and late cancellation/Pause/loss cases have deterministic tests; the physical01 failure was a client admission failure with zero grants and zero inputs.
- **Historical write reporting:** Core now retains the native `last_successful_write` receipt through expiry, revision clearing, immediate Stop, SQLite storage and hash-bound details. The closed scalar record is copied and validated against its original run/service/host/joint, owner/grant and ordered timestamps; an actual write completion may follow its original expiry when its start preceded it. Previously validated saved history survives a monotonic-clock reset. History never qualifies current ACK, grants, Resume or rearm. New terminal finishing clears current proof; reopening an old terminal record preserves its original canonical contents exactly. See [persistent-session tests](../../tests/robots/test_am1_persistent_session.py).
- **Startup thermal cadence:** the existing seed method now calls the existing same-owner refresh after each successful Goal write and successful checked Goal readback. These are two callback insertion sites, with no new reader, timer, slot reset or temperature limit. They cover sampling opportunities within a slow seed batch while preserving the original five-slot0.5s freshness refusal, peak history and I/O failure behavior. See [robot implementation](../../src/lerobot/robots/alohamini/alohamini.py) and [thermal handoff tests](../../tests/robots/test_am1_startup_thermal_handoff.py).

## Recorded physical outcomes

All listed runs ended with known physical cleanup and `uncertain=false`. A stopped run is distinct from a successful response procedure. Pre-Live refusals include startup activity, but no Live jog or grant/input stream.

| Trial / run prefix | First demonstrated layer and retained result |
|---|---|
| 01 / `d16bdf03` | Workspace admitted at actualq0raw1845. Resume result/snapshot ordering left the browser strict-latched: zero grants/inputs throughout the first six-second opportunity. Normal Stop; original procedure `FAILED`. This does not measure actuator response. |
| 02 / `e4116161` | Pre-Live context refusal: unchanged calibration; right wrist-roll Goal2443→2444. Original fault retained; supported fixed one-tick admission correction followed. |
| 03 / `1c27d9e8` | Pre-Live context refusal: left wrist-flex Goal1546→1547, with the already allowed right wrist difference. Original fault retained; second fixed one-tick admission correction followed. |
| 04 / `b1b1ea08` | Pre-Live native temperature-window stale refusal; host-connect timeout was the outer symptom. Oldest of five37°C slot peaks was500.116927ms old, although the latest raw sample was33.126132ms old. The124.843492ms seed batch crossed a missed slot. No0.5s freshness relaxation followed. |
| 05 / `ba47f866` | Startup/admission succeeded. Positive Goal1867 (+22raw from q0) had an independent same-owner ACTIVE register acknowledgement. PP remained1845 through a completed2.017s target-maintained window. `PROCEDURE_STOPPED_DIRECTION_UNQUALIFIED`. |
| 06 / `5977a85b` | Fresh startup/admission succeeded in the same workspace. Opposite-first negative Goal1834 (−11raw) had a same-owner ACTIVE register acknowledgement. PP remained1845 in all150 native position records, including a completed2.047s window. Same unqualified-direction verdict. |

Actual05 recorded78 accepted inputs and zero refusals; actual06 recorded56 and zero refusals. Neither had an expiry or rejoin during its response opportunity. Native target/write/feedback evidence and sparse actual register acquisitions establish delivery beyond a browser cursor or encoded helper return. They do not constitute continuous Goal-register observation. Normal measured hold after release is separate from the prior ACTIVE Goal.

Actual06 endpoint current was6.5..13mA, below the150mA ceiling, with no recorded adjustment between requested and current/relative-limited targets. Its zero response is not demonstrated current-cap clipping. Actual05 likewise remained below the guard. Current readings do not identify friction, load, deadzone or another unique physical cause. The retained two-second outward/four-second return measurement policy was not lengthened after seeing zero response.

Both trials preserved the actual eleven-Goal hold maps, original workspace/reference/deadline and sampled zero body velocities. Ordinary re-jog, reverse return and browser reattachment were not attempted after the first-leg response gate failed. No useful motion, precision, repeatability or end-to-end restart capability is claimed. Local imagery is extremely dark and does not show the elbow; P1 remains advisory/unavailable. There is no independent visual motion or full-arm clearance corroboration. AM2 and physical leaders remain outside this work.

## Verification boundaries

Results below are separate scopes and candidate checkpoints, **not an additive test total**. These are mocked/protocol or source-verification results; they cannot replace the physical response measurement.

| Scope | Recorded result and boundary |
|---|---|
| Final browser qualification cohort | Six browser files:202 passed,0 skipped,110.123s, frozen JS `6a645751…`. Includes both loaded selected-side ordinary-page cases and pure ordering/cancellation cases. |
| Historical reporting and legacy terminal preservation | Native persistent/API checkpoint:137 passed,1 existing Windows-DACL skip,84.50s, source `13cb973a…`; subsequent exact legacy-terminal correction:12 focused passed,105 deselected,9.28s on native source `2db49a4a…`. Two loaded ordinary-page cases also passed after the historical Core correction. |
| Final retained workspace |46 passed,289 deselected,0 skipped,1.50s in native source verification; contract `cac4b4cd…`, current context `75e93cad…`. |
| Startup seed refresh |116 passed,0 skipped,1.32s in native source verification at executable `ebbc2679…`; no device/control run in this verification wrapper. |
| Final frozen-source Windows check |Six native/startup files:228 passed,6 known Windows IPC skips,12.36s. Browser reporting:97 passed,0 skipped,0.994s. Sixteen source hashes remained unchanged. These overlap earlier scopes. |
| Single private browser procedure |98 VM tests passed with captured payload replay and injected delays/refusals/cancellation. The same parameterized runner supports either first direction; no second helper family. This is procedure verification, not physical motion. |

The browser cohort covers [layout](../../tests/cameras/test_am1_console_layout.cjs), [console](../../tests/cameras/test_am1_console_ui.cjs), [selected transport](../../tests/cameras/test_am1_selected_experiment_transport.cjs), [P1](../../tests/cameras/test_am1_p1_view.cjs), [loaded clients](../../tests/cameras/test_am1_session_clients.cjs) and [reporting](../../tests/cameras/test_am1_session_reporting.cjs). Native history, workspace and thermal tests retain malformed/foreign identity, stale/current proof, delayed cancellation, actual-origin and thermal-failure negatives. Earlier intermediate cohorts and genuine RED records remain labeled to their original candidates.

Private owner-packet, source manifests, test logs, accepted-operation/first-rejection evidence, native histories and response assessments are retained under the Packet11 evidence family `am1-live-reuse-11`. The public report omits operational addresses, credentials and household images. The private runner uses ordinary page controls, the existing typed collector and its original local70s ceiling/Stop reserve; no preloaded Pi target trajectory substitutes for UI input. Interrupted old measurement windows remain interrupted; a qualified new gesture is a separate opportunity.

## Executable, deployment and rollback

| Binding | Current matched installation | Immediate matched rollback |
|---|---|---|
| Executable source | `ebbc26791b857ea14dd111c05e2717bfb18f64ba` | Previous motor/source pair retained with its original manifest |
| Motor | `4253fba5eb6537e9495e943f6e57ef8690402ac8` | `673afd6fdffa947091b32fb5efdb23928f2d98d4` |
| Runtime | `588d3b4e290d6f924cd402dfd0efb8f12391f6ac` | `588d3b4e290d6f924cd402dfd0efb8f12391f6ac` |
| Manifest SHA256 | `b3687a0b28e197390fe5e52b49edef06e396ba928b1455b1fd5d98786877470a` | `24ff44ca65b76abc34d172f933ea3949b9e82a6eaed62a6e09a22525b9df07f9` |

The latest matched stage changed only the motor robot file's two refresh insertion sites. Runtime head, all eleven runtime files and its bundle remained unchanged. The existing stopped-owner staging/rollback path and separate local motor/manual/session pins were preserved; both immediate component rollback bundles remain retained. This report itself changes no executable, configuration, service or historical result.

## Closeout boundary — next Start and present connectivity

**The requested short next-Start check was not executed.** Both SSH preflight attempts timed out; the read-only page navigation reported `ERR_NETWORK_CHANGED`; local neighbor/ping checks reported unreachable. No Start or motor command was issued by this preflight. The single current procedure configuration remains `PENDING`. These observations do not identify a network cause or authorize tuning. Restoring the existing link must be followed by fresh native ownership, source, cleanup and served-page/workspace verification before that bounded check can run.

Actual06 did successfully perform another fresh normal startup from actual05's raw1845 rest inside the retained workspace, without manual positioning or an automatic return. This demonstrates retained-workspace admission reuse, separately from the requested short final next-Start check and separately from useful movement. Neither run shifted its workspace or invented its measured q0.

Available idle was last verified after actual06: known terminal cleanup, no uncertainty, and three active/enabled persistent services. Current reachability and a fresh available-idle page have not been re-established. The reviewed matched installation and rollback remain as recorded; no speculative rollback, service/network change or automatic replay was issued in response to a failed connection.

This focused follow-up targets `integrate/am1-local-teleop` from `codex/am1-live-reuse-11` and remains unmerged. PR22 is integrated; main is unchanged. Server status/workflow queries returned no contexts or runs for this follow-up executable, so no green-CI claim is made. Broad repository/pre-commit/community-review campaigns were not performed. Focused source/interface review and the distinct checks above support the software changes; they do not complete the physical objective.

Under Packet11's explicit physical-response exception, the available software repairs, instructions, review and publication proceed while retaining the exact unresolved boundary: actual endpoint Goals were delivered but no useful measured response occurred in either supported direction. Ordinary physical re-jog/reattachment and the purpose-specific short next Start remain unqualified. No force, travel, gain, diagnostic success threshold or prior verdict was changed to manufacture a pass. AM1 remains wireless, P1 advisory, AM2 untouched; durability retains its software fault-tolerance, continuity, state-preservation and recovery meaning.
