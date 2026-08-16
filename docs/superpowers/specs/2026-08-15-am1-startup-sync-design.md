# Aloha Mini 1 Startup Synchronization Design

## Status and Purpose

This specification defines a client-side startup synchronization phase for Aloha Mini 1. It starts from commit `46c9055db3d34d44b7b6688676fcbf1b56d9f520` on branch `fix/am1-startup-sync`.

The current strict startup gate is safe but requires the passive leaders and powered followers to begin in materially matching poses. The new `sync` mode provides an explicit, slow, operator-authorized transition from measured follower poses to frozen, validated leader poses before ordinary absolute teleoperation begins. It must prevent a snap, prevent a moving leader from becoming a chasing target, and retain the existing strict behavior unchanged.

This document is a design specification only. The new command-line options and command shapes described here do not exist until a later implementation packet completes and verifies them.

## Goals

The design adds two Aloha Mini 1 startup modes:

- `strict` preserves Packet 18C-R2. It validates the complete normalized action, compares fresh follower and leader samples, and refuses arm forwarding when any startup mismatch exceeds `--max_start_mismatch`.
- `sync` obtains explicit operator authorization, freezes a complete normalized leader target, and linearly slews selected follower joints from their newly measured starting poses to that frozen target. It verifies the result before either exiting or entering normal absolute teleoperation.

The safety objective is convenient startup from different initial poses without an immediate jump or uncontrolled convergence. Synchronization is deliberately conservative and operator-driven; it is not a substitute for collision-aware planning.

## Scope

The first implementation may change only:

- `examples/alohamini/teleoperate_bi.py`;
- `examples/alohamini/leader_client_utils.py`, but only if a small command-line helper is genuinely shared or improves isolation;
- focused fake-based tests in `tests/robots/test_alohamini_windows_leader_client.py`;
- the native-Windows commissioning section of `docs/alohamini/alohamini.md`.

The synchronization logic belongs in `teleoperate_bi.py` because recording does not consume it in this packet. The existing validation, freshness, alignment-table, zero-action, pending-first-action, and cleanup helpers should be reused rather than duplicated.

The following are explicitly out of scope:

- Raspberry Pi host changes;
- calibration contents, identities, paths, or procedures;
- generic `SOLeader` or `SOLeaderConfig` changes;
- `record_bi.py` synchronization or dataset recording;
- Aloha Mini 2 or Aloha Mini 2 Pro;
- collision-aware path planning;
- Cartesian interpolation or inverse kinematics;
- clutch, rebase, or relative teleoperation;
- active leader torque or haptic feedback;
- base, lift, camera, or ZMQ schema changes.

The client already serializes only the supplied action keys, and the existing Pi robot writes a side only when corresponding `arm_left_*.pos` or `arm_right_*.pos` keys are present. Therefore, selected-side synchronization can omit every unselected arm key without a host modification. Zero base and lift velocity keys remain present on every synchronization frame.

## Command-Line Contract

The following options are added to `teleoperate_bi.py`:

| Option | Default | Contract |
| --- | --- | --- |
| `--startup_mode {strict,sync}` | `strict` | Select existing strict alignment or the new synchronization flow. |
| `--startup_sync_duration_s FLOAT` | `12.0` | Requested minimum synchronization duration. It must be finite and greater than zero. |
| `--startup_sync_side {left,right,both}` | `both` | Select the follower arm keys included in synchronization frames. |
| `--startup_sync_only` | false | Synchronize, verify, clean up, and exit without ordinary teleoperation. |

`--max_start_mismatch` remains unchanged:

- in `strict` mode, it is the allowed mismatch at the existing initial and post-pause gates;
- in `sync` mode, it is the allowed selected-joint mismatch after synchronization and at the optional post-sync pause gate.

All Packet 14B and Packet 18C-R2 options remain available. `--duration_s` measures only ordinary teleoperation time. Its clock starts after synchronization, optional resource startup, and any post-sync `--start_paused` gate; synchronization and operator-wait time do not consume it.

### Argument Compatibility

Argument incompatibilities are rejected by `argparse` before any device is constructed. They produce status `2` without a traceback.

| Combination | Result |
| --- | --- |
| `--startup_mode sync` with `--robot.robot_model alohamini1` | Allowed. |
| `--startup_mode sync` with `alohamini2` or `alohamini2pro` | Reject: synchronization is initially Aloha Mini 1 only. |
| `--startup_mode sync --no_robot` | Reject: synchronization requires a real robot client. |
| `--startup_mode sync --no_leader` | Reject: synchronization requires both real leaders. |
| `--startup_sync_only` without `--startup_mode sync` | Reject. |
| `--startup_sync_side left` or `right` without `--startup_sync_only` | Reject. |
| Normal teleoperation after synchronization with `--startup_sync_side both` | Allowed. |
| Normal teleoperation after synchronization with side `left` or `right` | Reject. |
| `--check_alignment_only --startup_mode strict` | Preserve the existing no-motion diagnostic. |
| `--check_alignment_only --startup_mode sync` | Reject. |

`--start_paused` is accepted with `--startup_sync_only`, but there is no post-sync pause because sync-only mode never enters teleoperation. `--duration_s` is likewise accepted but unused in sync-only mode. Help text must state both facts so neither option appears to control synchronization.

The parser validates `--startup_sync_duration_s` even in strict mode. This keeps malformed configuration deterministic, although the value affects motion only in sync mode.

## Aloha Mini 1 Action-Space Contract

The complete Aloha Mini 1 arm-position schema is exactly:

```text
arm_left_shoulder_pan.pos
arm_left_shoulder_lift.pos
arm_left_elbow_flex.pos
arm_left_wrist_flex.pos
arm_left_wrist_roll.pos
arm_left_gripper.pos
arm_right_shoulder_pan.pos
arm_right_shoulder_lift.pos
arm_right_elbow_flex.pos
arm_right_wrist_flex.pos
arm_right_wrist_roll.pos
arm_right_gripper.pos
```

The normalized ranges are:

| Joint | Inclusive range |
| --- | --- |
| `shoulder_pan` | `[-100, 100]` |
| `shoulder_lift` | `[-100, 100]` |
| `elbow_flex` | `[-100, 100]` |
| `wrist_flex` | `[-100, 100]` |
| `wrist_roll` | `[-100, 100]` |
| `gripper` | `[0, 100]` |

The existing `ACTION_RANGE_TOLERANCE = 1e-6` remains the only numerical boundary tolerance. No degree conversion, manual multiplication, or post-read rescaling is permitted.

Every leader sample read during initial sampling, target freezing, drift monitoring, final approval, or ordinary teleoperation must:

- contain all twelve expected arm-position keys after the existing `arm_` prefix conversion;
- contain no unexpected `arm_*.pos` key;
- contain only numeric, finite values for those keys;
- satisfy the joint-specific normalized ranges.

Exactness applies to the `arm_*.pos` subset. Legitimate `x.vel`, `y.vel`, `theta.vel`, and `lift_axis.vel` keys are not unexpected arm data and do not alter the twelve-key arm contract.

Follower observations used by startup synchronization must contain the same exact twelve-key arm subset and finite values. Before an observed follower value is used as a selected-side `alpha=0` command, it must also satisfy that joint's normalized command range. This selected-side range check is sync-specific and must not alter strict-mode semantics.

## Component Boundaries

The implementation should keep the orchestration readable by separating pure planning and validation from I/O:

1. `selected_arm_position_keys(side)` returns the six left keys, six right keys, or all twelve keys in canonical `AM1_ARM_POSITION_KEYS` order.
2. An immutable `StartupSyncPlan` records the selected keys, complete measured follower start, complete frozen leader target, requested duration, `fps`, largest selected displacement, `total_steps`, frame count, largest planned per-frame change, and estimated actual duration.
3. `build_startup_sync_plan(...)` performs only validation and arithmetic. It has no device or clock access.
4. `build_startup_sync_action(plan, frame_index)` returns selected arm keys plus `make_zero_action()`. It has no I/O and never reads a leader.
5. `validate_startup_sync_leader_drift(...)` validates a complete current leader sample and compares only selected joints with the frozen target.
6. `verify_startup_sync_result(...)` builds the full alignment table and applies `--max_start_mismatch` only to selected joints.
7. `run_startup_sync(...)` owns confirmation, fresh reads, the timed frame loop, drift reads, sends, final verification, and its state transitions. It returns the verified frozen target and final follower observation on success; it raises `SafetyRefusal` for expected refusals.

These names are design-level interfaces; minor naming changes are acceptable if the same boundaries and test seams remain. Synchronization helpers should remain local to `teleoperate_bi.py` unless a parser-only helper clearly belongs in `leader_client_utils.py`.

## Startup State Machine

The sync path uses the following states:

```text
CONNECTING
  -> INITIAL_SAMPLE
  -> WAITING_FOR_SYNC_CONFIRMATION
  -> FREEZING_TARGET
  -> SYNCHRONIZING
  -> VERIFYING
  -> SYNC_COMPLETE
  -> WAITING_FOR_TELEOP_CONFIRMATION  (only when --start_paused and not sync-only)
  -> TELEOPERATING
  -> CLEANUP
```

`SYNC_COMPLETE` transitions directly to `CLEANUP` for `--startup_sync_only`. Without `--start_paused`, it transitions directly to `TELEOPERATING`. Any expected refusal from any state transitions to `CLEANUP` and returns status `2`. Any unexpected exception also transitions through best-effort cleanup and is then re-raised as the primary failure.

| State | Entry work | Permitted arm-position send | Success transition |
| --- | --- | --- | --- |
| `CONNECTING` | Connect robot, send existing zero base/lift action, connect passive leaders. | None. | `INITIAL_SAMPLE` |
| `INITIAL_SAMPLE` | Obtain sequence-proven follower observation and validated leader sample; print table and preliminary plan. | None. | `WAITING_FOR_SYNC_CONFIRMATION` |
| `WAITING_FOR_SYNC_CONFIRMATION` | Display safety instructions and require exact `SYNC`. | None. | `FREEZING_TARGET` |
| `FREEZING_TARGET` | Obtain new sequence-proven follower start and new validated leader target; freeze both; compute and print final plan. | None. | `SYNCHRONIZING` |
| `SYNCHRONIZING` | Check leader drift and send `alpha=0` through `alpha=1` selected-side frames. | Only the current validated interpolation frame. | `VERIFYING` |
| `VERIFYING` | Obtain fresh follower observation; validate frozen target; print table; verify selected joints. | None. | `SYNC_COMPLETE` |
| `SYNC_COMPLETE` | Report success and choose sync-only, paused, or direct teleoperation path. | None. | `CLEANUP`, `WAITING_FOR_TELEOP_CONFIRMATION`, or `TELEOPERATING` |
| `WAITING_FOR_TELEOP_CONFIRMATION` | Wait for Enter, then perform a fresh full alignment gate. | None. | `TELEOPERATING` |
| `TELEOPERATING` | Preserve Packet 18C-R2 absolute control and per-sample validation. | Validated complete live arm actions. | `CLEANUP` |
| `CLEANUP` | Request zero base/lift and disconnect started resources. | No arm-position action. | Process exit |

Strict mode does not traverse synchronization states. It retains the existing Packet 18C-R2 sequence: connect, initial strict alignment gate, optional alignment-only exit, optional pre-teleoperation pause plus fresh second gate, pending validated first action, normal loop, and cleanup.

Optional keyboard and Rerun resources are not constructed before or during synchronization. Sync-only mode never constructs them. For synchronization followed by teleoperation, they are constructed only after successful final verification and before any optional post-sync pause. A startup failure in either optional resource still follows normal cleanup and never begins teleoperation.

## Initial Sampling and Operator Confirmation

Before any follower arm-position action, sync mode must:

1. obtain a fresh follower observation proven by an `observation_sequence` increment;
2. validate the complete follower arm subset for exact keys and finite values, and validate the selected follower values against their normalized command ranges;
3. obtain and validate one fresh complete normalized leader sample;
4. print the existing twelve-joint table with follower value, leader value, signed difference, and absolute difference;
5. print `--startup_sync_side`, requested duration, selected-joint largest mismatch, estimated interval count, estimated frame count, largest estimated per-frame change, and estimated actual duration;
6. instruct the operator to clear the follower workspace, keep people and objects away, hold both leaders still, and keep the follower motor disconnect accessible;
7. prompt the operator to type exactly `SYNC` and press Enter.

The preliminary plan is informational and uses the initial follower and leader samples. It is not the motion plan because both endpoints are sampled again after authorization.

Authorization is a single exact comparison: the input must equal the four-character string `SYNC`. Empty input, Enter alone, `sync`, leading or trailing whitespace, or any other input is an expected safety refusal. The program does not reprompt and does not send an arm-position action.

Zero-only base/lift sends already used for connection and cleanup remain permitted before confirmation. No payload containing an `arm_*.pos` key is permitted before exact authorization.

## Frozen Start and Target

Immediately after exact confirmation, state `FREEZING_TARGET` performs these operations in order:

1. capture a new fresh follower observation proven by another `observation_sequence` increment;
2. validate its complete twelve-key arm subset and validate selected follower start values against command ranges;
3. read and validate a new complete normalized leader sample;
4. copy both mappings so later mutable fake or device data cannot alter them;
5. freeze the complete leader mapping as the synchronization target;
6. use the complete follower mapping as the measured interpolation start;
7. compute the final plan from selected keys only;
8. print the frozen target, measured selected-side start, and final plan summary.

The target never changes during synchronization. Later leader reads are used only for validation and drift detection; they must not replace or adjust the frozen target.

## Plan Arithmetic and Maximum Step

The implementation defines exactly these synchronization safety constants:

```text
STARTUP_SYNC_MAX_STEP = 0.75
STARTUP_SYNC_LEADER_DRIFT = 2.0
```

Both are expressed in normalized units.

For each selected key `k`:

```text
delta[k] = frozen_leader_target[k] - follower_start[k]
max_abs_delta = max(abs(delta[k]) for k in selected_keys)
duration_steps = ceil(startup_sync_duration_s * fps)
step_limit_steps = ceil(max_abs_delta / STARTUP_SYNC_MAX_STEP)
total_steps = max(1, duration_steps, step_limit_steps)
```

`total_steps` is the number of interpolation intervals, not the number of sends. Frames use indices `0..total_steps`, so:

```text
planned_frame_count = total_steps + 1
estimated_actual_duration_s = total_steps / fps
largest_planned_per_frame_change = max_abs_delta / total_steps
```

This convention resolves the `alpha=0` requirement without shortening the requested duration. For example, `12.0` seconds at `5` Hz produces at least `60` intervals, `61` frames, and an estimated duration of `12.0` seconds. The actual wall-clock duration may be longer because device reads and scheduling overhead are not subtracted from safety requirements.

If every selected delta is zero, the client still sends the requested-duration sequence of identical hold frames. It does not silently shorten synchronization.

The plan summary prints:

- requested minimum duration;
- selected side;
- largest selected-joint displacement;
- `total_steps` intervals;
- planned frame count;
- largest planned per-frame joint change;
- estimated actual duration.

The client step cap is independent of and additional to the Pi host's `max_relative_target`. The host limiter remains a secondary protection and is not permission to violate `STARTUP_SYNC_MAX_STEP`.

## Interpolation and Frame Construction

For frame index `i` in `0..total_steps`:

```text
alpha_i = i / total_steps
target_i[k] = follower_start[k] + alpha_i * delta[k]
```

The implementation must assign the endpoints exactly:

- at `i = 0`, use the measured `follower_start[k]` directly rather than relying on floating-point reconstruction;
- at `i = total_steps`, use `frozen_leader_target[k]` directly.

Every intermediate frame must satisfy all of the following:

- `alpha` is monotonic and never decreases;
- only selected-side `arm_*.pos` keys are present;
- every unselected arm-position key is absent;
- `x.vel`, `y.vel`, `theta.vel`, and `lift_axis.vel` are present with the exact values from `make_zero_action()`;
- every selected target is finite and within its normalized joint range;
- the absolute change for every selected joint from the previous frame is at most `STARTUP_SYNC_MAX_STEP`, allowing only `ACTION_RANGE_TOLERANCE` for floating-point comparison;
- the frame contains no live mirrored leader value other than the immutable frozen target used by interpolation.

At `fps`, frame zero is sent immediately after its drift check. For each later frame, the client first waits until no earlier than `i / fps` seconds after frame zero, then performs that frame's fresh leader read and drift check immediately before sending. If reads or sends overrun a deadline, the client does not skip a frame, increase `alpha`, or compress the remaining motion. It sends every planned frame in order, making actual duration longer when necessary.

For `--startup_sync_side=left`, each frame has six left arm keys and no right arm keys. For `--startup_sync_side=right`, it has six right arm keys and no left arm keys. For `--startup_sync_side=both`, it has all twelve arm keys. The unchanged Pi host leaves omitted arm goals untouched.

## Leader Drift Monitoring

Immediately before every synchronization frame, including frame zero and the final `alpha=1` frame, the client calls `leader.get_action()` once and validates the complete twelve-key normalized sample. A new call is the available freshness guarantee for passive leaders; they do not expose an observation sequence counter.

For each selected key:

```text
drift[k] = abs(current_leader[k] - frozen_leader_target[k])
```

If any selected drift is greater than `STARTUP_SYNC_LEADER_DRIFT`, synchronization aborts before sending that frame. Equality at `2.0` passes; only a value strictly greater than `2.0` refuses.

The refusal reports:

- side and joint;
- frozen target value;
- current leader value;
- signed difference;
- absolute drift;
- allowed drift `2.0`.

The complete leader schema is validated on every read, but drift on an unselected side does not abort a one-side sync. No additional arm target is sent after the failing read. The client requests the existing zero base/lift action, performs normal cleanup, and returns status `2`. It does not command a reverse path. The Pi may continue holding the last valid follower target.

## Final Verification

After the `alpha=1` frame is sent, state `VERIFYING`:

1. obtains a fresh follower observation proven by an `observation_sequence` increment;
2. validates the complete follower arm subset for exact keys and finite values;
3. validates the immutable complete frozen leader target again with the same leader action contract;
4. prints the full twelve-joint alignment table using `frozen target - follower` as the signed difference;
5. checks only selected keys against `--max_start_mismatch`;
6. requires every selected key to pass.

Final verification does not substitute a live leader sample for the frozen target. The final drift check immediately before `alpha=1` already confirms that selected leader joints remain within the drift allowance.

For `left` or `right` sync, the table still contains all twelve joints and the frozen leader schema remains complete, but only the selected six joints determine synchronization success. For `both`, all twelve determine success.

Any selected mismatch strictly greater than `--max_start_mismatch` is an expected refusal. It reports the exact key, follower value, frozen value, signed difference, absolute difference, and threshold, then cleans up with status `2`. Ordinary teleoperation is never entered after a failed verification.

## Sync-Only Completion

With `--startup_sync_only`, successful final verification transitions from `SYNC_COMPLETE` directly to `CLEANUP`:

- no keyboard or Rerun resource is constructed;
- no ordinary control-loop clock is started;
- no post-sync live leader arm action is read or sent;
- the cleanup zero base/lift request remains allowed;
- connected leaders and the robot client are disconnected;
- status `0` is returned only after final verification succeeds.

## Transition to Ordinary Teleoperation

Normal teleoperation after sync requires `--startup_sync_side=both`, so both follower sides have been driven to and verified against the complete frozen target.

### Without `--start_paused`

The first ordinary arm action is the final validated frozen target combined with explicit zero base/lift commands. It is supplied through the existing pending-first-action mechanism. No leader read occurs between final approval and that first ordinary `robot.send_action()`.

This first ordinary action is identical to the final synchronization arm target, so the transition has no commanded discontinuity. Only after it is sent may the ordinary loop read and validate a later complete live leader sample.

### With `--start_paused`

After final verification, the client:

1. prints `Synchronization complete` and the normal startup summary;
2. waits for Enter before ordinary teleoperation;
3. obtains a new sequence-proven follower observation after Enter;
4. reads a new complete normalized leader sample;
5. validates both complete schemas and all leader ranges;
6. prints the full table and applies `--max_start_mismatch` to all twelve keys;
7. refuses with status `2` if any mismatch exceeds the threshold;
8. uses that final validated leader sample as the first ordinary arm action with explicit zero base/lift commands.

There is no unchecked leader read between this approval and the first ordinary send. The ordinary `duration_s` clock starts only after this gate and immediately before ordinary teleoperation.

In sync mode, `--start_paused` controls this post-sync transition only. The exact `SYNC` confirmation is always required before automatic follower motion, whether or not `--start_paused` is present.

## Strict-Mode Preservation

`--startup_mode strict` is the default and must preserve every Packet 18C-R2 guarantee:

- no automatic follower positioning;
- exact twelve-key validation;
- finite and normalized leader ranges;
- the startup summary stating body-joint range `-100..100` and gripper range `0..100`;
- fresh initial follower/leader alignment gate;
- `--max_start_mismatch` refusal before arm forwarding;
- `--check_alignment_only` no-motion diagnostics;
- fresh post-Enter recheck under `--start_paused`;
- final validated sample used as the first arm action without an unchecked reread;
- explicit zero base/lift values on that first arm action;
- per-sample validation but no continuous startup-mismatch comparison during normal motion;
- expected refusal status `2` and existing cleanup behavior.

Sync implementation must branch around, not weaken or repurpose, the existing strict `run_alignment_gate()` behavior.

## Failure, Status, and Cleanup Semantics

Expected safety refusals include:

- invalid sync option combinations;
- non-finite or non-positive `--startup_sync_duration_s`;
- invalid follower or leader schema/value data;
- any confirmation input other than exact `SYNC`;
- selected follower start outside command range;
- generated interpolation data outside range or over the step limit;
- selected leader drift greater than `2.0`;
- final selected mismatch greater than `--max_start_mismatch`;
- post-sync paused mismatch greater than `--max_start_mismatch`.

Argument errors occur before connection and use the normal `argparse` status `2`. Runtime safety refusals print one concise actionable reason, send no further arm-position action, transition to `CLEANUP`, and return status `2` without a traceback.

Cleanup preserves the current partial-resource lifecycle:

1. if the robot connected, make a best-effort `make_zero_action()` request;
2. disconnect a connected keyboard, if one was started after sync;
3. disconnect the right leader if connected;
4. disconnect the left leader if connected;
5. disconnect the robot client if connected;
6. shut down Rerun if it was started after sync.

Unexpected connection, read, send, timing, or optional-resource exceptions remain primary. Cleanup errors are attached to the primary exception and must not replace it. If cleanup itself is the only failure, preserve the existing first-cleanup-error behavior.

`KeyboardInterrupt` performs the same best-effort cleanup and may retain the existing conventional interrupted process status. It is not converted into safety status `2` and need not trigger an automatic return path.

A failed synchronization never commands followers back to their measured start. The Pi may hold the last valid arm target after client cleanup; the operator must use the motor disconnect if that hold is unsafe.

## Safety Limitations

Startup synchronization is linear interpolation in normalized joint space. It is not collision-aware and does not reason about Cartesian paths, self-collision, the other arm, the chassis, payload swing, or external objects.

Operational requirements are therefore part of the safety design:

- use empty grippers;
- clear the full follower arm envelope;
- place leaders in moderate, known-safe poses;
- hold leaders still from confirmation through final verification;
- keep people, objects, the other arm, and the chassis out of an unclear planned path;
- keep the follower motor disconnect immediately accessible;
- commission one side at a time before any both-side test;
- stop at the first unexpected direction, speed, sound, current, contact, software error, or communication failure.

The client step cap reduces commanded increments but does not prove physical clearance. The Pi host's `max_relative_target` remains a second limiter, not a planner.

## Test-Driven Implementation Requirements

Implementation must follow red-green TDD. Focused fake-based tests are written and observed failing for the intended reason before production changes. Tests must inspect the ordered event stream and actual action payloads rather than only testing helper arithmetic.

Required tests prove:

1. strict mode still refuses a large initial mismatch before any arm action;
2. sync sends no arm action before exact `SYNC` confirmation;
3. Enter alone, lowercase `sync`, and whitespace-modified confirmation do not authorize movement;
4. frame zero equals the newly measured post-confirmation follower start pose;
5. the first moving frame is an interpolation step bounded by `STARTUP_SYNC_MAX_STEP`;
6. no selected joint changes by more than `STARTUP_SYNC_MAX_STEP` between any adjacent frames;
7. the final synchronization arm target equals the frozen leader target exactly;
8. `step_limit_steps` extends a requested duration when the largest displacement requires more frames;
9. zero displacement still preserves the requested minimum duration;
10. left-only frames contain no right arm keys;
11. right-only frames contain no left arm keys;
12. left and right modes are rejected without `--startup_sync_only`;
13. every frame contains exactly zero `x.vel`, `y.vel`, `theta.vel`, and `lift_axis.vel` values;
14. missing, unexpected, nonnumeric, non-finite, or out-of-range leader data aborts before the affected frame;
15. complete leader validation continues on unselected sides, while drift refusal applies only to selected joints;
16. selected leader drift strictly greater than `2.0` aborts with exact identity and sends no affected frame;
17. drift equal to `2.0` is accepted;
18. final selected mismatch greater than `--max_start_mismatch` prevents teleoperation;
19. left/right final verification ignores mismatch on the unselected side while still printing the full table;
20. `--startup_sync_only` never constructs optional runtime resources or enters the ordinary loop;
21. successful both-arm sync sends the frozen target as the first ordinary action without another leader read;
22. that first ordinary action contains explicit zero base/lift values even when keyboard input is active;
23. post-sync `--start_paused` obtains fresh follower and leader samples, catches movement, and forwards the final validated sample without an extra read;
24. `duration_s` starts after sync and post-sync waiting rather than including them;
25. cleanup occurs after refusal and unexpected exceptions without hiding the primary failure;
26. `KeyboardInterrupt` requests zero and disconnects every connected resource;
27. all Packet 14A, Packet 14B, and Packet 18C-R2 focused tests remain green.

Fakes must provide configurable complete follower/leader pose sequences, `observation_sequence` behavior, ordered send/read/cleanup events, a deterministic monotonic clock, and a sleep recorder. No test may open a COM port, create a ZMQ connection, load a camera, or access physical hardware.

## Documentation and Physical Commissioning Sequence

The later implementation packet updates the native-Windows commissioning documentation. It must label synchronization as non-collision-aware, require exact `SYNC` confirmation, explain the step and drift limits, and state that new command shapes are unavailable until implementation lands.

Physical commissioning is split into stop/go packets. Each packet stops at the first unexpected movement or software failure:

| Stage | Procedure |
| --- | --- |
| S1 | Left follower only, `--startup_sync_only`, `12.0` seconds, `5` Hz, Pi `max_relative_target=1.0`. |
| S2 | Right follower only with the same duration, frequency, and Pi limit. |
| S3 | Existing strict alignment-only diagnostic. |
| S4 | Both followers, `--startup_sync_only`, `15.0` seconds, `5` Hz. |
| S5 | Strict bounded teleoperation while the operator moves only the grippers. This is an operator procedure, not a gripper-only payload mode; all twelve strict arm keys remain present and validated. |
| S6 | Normal `--startup_mode sync` for both sides followed by post-sync `--start_paused` teleoperation. |

The Pi `max_relative_target=1.0` setting is a host commissioning precondition for S1 and S2. This client-only packet does not add or alter a Pi command.

### Intended Command Shapes After Implementation

The commands below are design targets. **Do not run them and do not claim they are available until the synchronization implementation and its tests are complete.**

S1 — left-only sync and exit:

```powershell
.\.venv\Scripts\python.exe `
  .\examples\alohamini\teleoperate_bi.py `
  --robot.remote_ip 192.168.1.134 `
  --robot.robot_model alohamini1 `
  --teleop.left_port COM7 `
  --teleop.right_port COM8 `
  --teleop.id so101_leader_bi `
  --teleop.arm_profile so-arm-5dof `
  --startup_mode sync `
  --startup_sync_side left `
  --startup_sync_duration_s 12.0 `
  --startup_sync_only `
  --max_start_mismatch 10.0 `
  --fps 5 `
  --no_keyboard `
  --no_rerun
```

S2 — right-only sync and exit:

```powershell
.\.venv\Scripts\python.exe `
  .\examples\alohamini\teleoperate_bi.py `
  --robot.remote_ip 192.168.1.134 `
  --robot.robot_model alohamini1 `
  --teleop.left_port COM7 `
  --teleop.right_port COM8 `
  --teleop.id so101_leader_bi `
  --teleop.arm_profile so-arm-5dof `
  --startup_mode sync `
  --startup_sync_side right `
  --startup_sync_duration_s 12.0 `
  --startup_sync_only `
  --max_start_mismatch 10.0 `
  --fps 5 `
  --no_keyboard `
  --no_rerun
```

S3 — strict no-motion alignment diagnostic:

```powershell
.\.venv\Scripts\python.exe `
  .\examples\alohamini\teleoperate_bi.py `
  --robot.remote_ip 192.168.1.134 `
  --robot.robot_model alohamini1 `
  --teleop.left_port COM7 `
  --teleop.right_port COM8 `
  --teleop.id so101_leader_bi `
  --teleop.arm_profile so-arm-5dof `
  --startup_mode strict `
  --check_alignment_only `
  --max_start_mismatch 10.0 `
  --no_keyboard `
  --no_rerun
```

S4 — both-side sync and exit:

```powershell
.\.venv\Scripts\python.exe `
  .\examples\alohamini\teleoperate_bi.py `
  --robot.remote_ip 192.168.1.134 `
  --robot.robot_model alohamini1 `
  --teleop.left_port COM7 `
  --teleop.right_port COM8 `
  --teleop.id so101_leader_bi `
  --teleop.arm_profile so-arm-5dof `
  --startup_mode sync `
  --startup_sync_side both `
  --startup_sync_duration_s 15.0 `
  --startup_sync_only `
  --max_start_mismatch 10.0 `
  --fps 5 `
  --no_keyboard `
  --no_rerun
```

S5 — strict bounded teleoperation with operator movement limited to grippers:

```powershell
.\.venv\Scripts\python.exe `
  .\examples\alohamini\teleoperate_bi.py `
  --robot.remote_ip 192.168.1.134 `
  --robot.robot_model alohamini1 `
  --teleop.left_port COM7 `
  --teleop.right_port COM8 `
  --teleop.id so101_leader_bi `
  --teleop.arm_profile so-arm-5dof `
  --startup_mode strict `
  --max_start_mismatch 10.0 `
  --fps 5 `
  --duration_s 30 `
  --start_paused `
  --no_keyboard `
  --no_rerun
```

S6 — both-side sync followed by paused ordinary teleoperation:

```powershell
.\.venv\Scripts\python.exe `
  .\examples\alohamini\teleoperate_bi.py `
  --robot.remote_ip 192.168.1.134 `
  --robot.robot_model alohamini1 `
  --teleop.left_port COM7 `
  --teleop.right_port COM8 `
  --teleop.id so101_leader_bi `
  --teleop.arm_profile so-arm-5dof `
  --startup_mode sync `
  --startup_sync_side both `
  --startup_sync_duration_s 15.0 `
  --max_start_mismatch 10.0 `
  --fps 5 `
  --duration_s 60 `
  --start_paused `
  --no_keyboard `
  --no_rerun
```

## Future Work

The following remain explicitly deferred:

- `record_bi.py` synchronization;
- automatic confirmation or confirmation bypass;
- clutch or rebase behavior;
- automatic follower return-to-start after abort;
- active collision or self-collision checking;
- Cartesian trajectories and inverse kinematics;
- haptic feedback or active leader torque;
- making `sync` the default startup mode.

## Implementation Acceptance Criteria

The later implementation is complete only when:

- `strict` remains the default and all existing strict tests pass unchanged;
- every new argument and incompatibility follows this contract;
- no sync arm action can precede exact `SYNC` authorization;
- frame zero, step limit, frozen target, selected-side omission, zero body/lift, drift monitoring, final verification, and first-action continuity are proven by ordered-event tests;
- sync-only cannot enter ordinary teleoperation;
- Aloha Mini 2, Aloha Mini 2 Pro, recording, calibration, cameras, base, lift, host behavior, and generic leader behavior remain outside the diff;
- fake-only validation, `py_compile`, CLI help, fresh-process imports, and `git diff --check` pass before any physical commissioning;
- commissioning documentation labels all limitations and follows S1 through S6 in order.
