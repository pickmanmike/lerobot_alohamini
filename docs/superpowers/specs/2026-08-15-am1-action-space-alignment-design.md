# Aloha Mini 1 Action-Space and Startup-Alignment Safety Design

## Purpose

Correct the Aloha Mini 1 bimanual leader/follower position-unit mismatch and refuse unsafe first forwarding when the passive leaders and powered followers are not already in materially matching poses.

This repair starts from commit `645964a9a3573ef8a36676391c64992e0513b06e` on branch `fix/am1-teleop-action-space`. It does not change motor registers, calibration contents, Raspberry Pi activation, follower normalization, ZMQ message schema, lift behavior, camera behavior, or leader torque behavior.

## Confirmed Root Cause

`AlohaMiniConfig.use_degrees` defaults to `False`, so follower arm body joints use `MotorNormMode.RANGE_M100_100`. `SOLeaderConfig.use_degrees` defaults to `True`. The Aloha Mini scripts currently omit that child setting, and `BiSOLeader` propagates it to both `SOLeader` instances. Consequently, `SOLeader.get_action()` emits body-joint values in degrees while the Pi host interprets those values as normalized follower targets.

The repair sets the units at the source. It does not rescale values after `get_action()` and does not change the generic `SOLeaderConfig` default.

## Configuration Architecture

`examples/alohamini/leader_client_utils.py` will own one Aloha Mini bimanual leader-config builder. It will construct both child `SOLeaderConfig` objects with:

- the caller-supplied left and right ports unchanged;
- the caller-supplied arm profile;
- `use_degrees=False`.

`calibrate_bi.py`, `teleoperate_bi.py`, and `record_bi.py` will delegate their existing `make_leader_config()` functions to this builder. Keeping the script-level functions preserves their current test and import interfaces.

The calibration JSON remains reusable. Its motor IDs, raw homing offsets, raw range minima/maxima, drive mode, file location, and identity are unchanged; `use_degrees` controls runtime normalization only.

## Exact Aloha Mini 1 Arm Contract

The expected arm-position payload consists of exactly these keys:

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

Leader keys are converted by adding the existing `arm_` prefix: for example, `right_elbow_flex.pos` becomes `arm_right_elbow_flex.pos`.

Every leader sample is validated before it can be forwarded:

- all twelve expected keys must be present;
- no additional `arm_*.pos` key is accepted;
- `shoulder_pan`, `shoulder_lift`, `elbow_flex`, `wrist_flex`, and `wrist_roll` values must be finite and within `-100..100` inclusive;
- `gripper` values must be finite and within `0..100` inclusive;
- a numerical boundary tolerance of `1e-6` is allowed.

Legitimate non-arm keys such as `x.vel`, `y.vel`, `theta.vel`, and `lift_axis.vel` are not classified as unexpected arm data.

The follower observation's `arm_*.pos` subset must contain the same exact twelve keys before alignment. Extra camera, base, lift, array, and other non-arm observation keys remain valid. Every follower arm value used for alignment must be finite; a missing, unexpected, or non-finite follower arm value is a safety refusal.

Range validation applies to the initial sample, the post-pause sample, and every sample read during normal motion. The startup mismatch threshold applies only at startup/alignment gates, never continuously during normal teleoperation.

## Fresh Observation and Alignment Gate

An alignment check requires both a connected robot client and both connected leaders. A follower observation is fresh only when `AlohaMiniClient.observation_sequence` increments after the check begins. The client repeatedly calls `get_observation()` until that increment occurs or `config.connect_timeout_s` expires. A timeout is an operational error and follows the existing exception-cleanup path.

For each expected key, the alignment table prints:

- joint key;
- follower value;
- leader value;
- signed difference, defined as `leader - follower`;
- absolute difference.

`--max_start_mismatch` is a floating-point option with default `10.0`. Parsing rejects non-finite values and values less than or equal to zero.

If all absolute differences are within the threshold, alignment succeeds. If any difference exceeds it, the program prints the exact failing joint, follower value, leader value, signed difference, absolute difference, and allowed threshold, then refuses forwarding.

## Teleoperation Sequence

Normal hardware-backed teleoperation follows this order:

1. Construct and connect the Pi client.
2. Send the existing zero-only chassis/lift command.
3. Construct and connect the two passive leader arms.
4. Obtain a fresh follower observation and a fresh normalized leader sample.
5. Validate the exact leader key set and every value range.
6. Print and evaluate the initial alignment table.
7. If `--check_alignment_only` is selected, clean up and return success or refusal without constructing keyboard or visualization objects.
8. Otherwise, construct optional keyboard and visualization resources.
9. If `--start_paused` is selected, print the existing connection summary, including `body joints -100..100; grippers 0..100`, and wait for Enter.
10. Immediately after Enter, obtain a newly sequence-proven follower observation and a newly read normalized leader sample, validate both, print a second table, and compare again.
11. Use the final validated leader sample as the first arm action. Combine it only with explicit zero base/lift commands. Do not perform another leader read before that first `robot.send_action()`.
12. On later cycles, read and range-validate every leader sample but do not reapply `--max_start_mismatch`.

Without `--start_paused`, the validated initial leader sample is the first forwarded arm action, again with explicit zero base/lift values and without an intervening unchecked leader read.

## Alignment-Only and Debug Modes

`--check_alignment_only` requires both the robot and leaders. Combining it with `--no_robot` or `--no_leader` is an argument error before any object is constructed.

Alignment-only mode:

- connects only the Pi client and two leaders;
- permits the existing zero chassis/lift action after connection and during cleanup;
- obtains and prints one alignment table;
- sends no `arm_*.pos` action;
- skips keyboard and visualization construction;
- returns `0` when all values and mismatches are safe;
- returns `2` for expected range/key/alignment refusals.

Ordinary `--no_robot` or `--no_leader` debug modes retain their existing behavior and bypass alignment because no complete robot/leader pair can forward an arm command.

## Refusal and Cleanup Semantics

Missing keys, unexpected `arm_*.pos` keys, non-finite values, out-of-range values, and excessive startup mismatch are expected safety refusals. They:

- print an exact refusal reason without a traceback;
- return status `2`;
- send no payload containing an `arm_*.pos` key;
- may send only the existing `make_zero_action()` base/lift payload;
- execute the existing best-effort cleanup and shutdown summary.

Unexpected connection, observation, leader-read, visualization, or cleanup failures retain the existing exception-preservation behavior.

## Recording Scope

`record_bi.py` receives the same centralized normalized leader configuration. This packet does not add recording alignment flags or restructure the recording loop. The documented alignment-only and bounded teleoperation checks remain required before first physical recording.

## Test Strategy

The existing fake-object test module will be extended with complete literal Aloha Mini 1 follower and leader fixtures. Tests will distinguish safe zero-only sends from arm-bearing sends.

The red-green sequence will prove:

1. calibration constructs both leader children with `use_degrees=False`;
2. teleoperation constructs both children with `use_degrees=False`;
3. recording constructs both children with `use_degrees=False`;
4. `-105.8` is refused before any arm-bearing send and reports the exact side, joint, value, and expected range;
5. missing and unexpected arm-position keys are refused, while base/lift zero keys are ignored by arm-key validation;
6. a startup mismatch greater than `--max_start_mismatch` is refused before any arm-bearing send;
7. a within-threshold alignment supplies the first forwarded arm sample;
8. moving a leader beyond the threshold while paused is caught by a fresh second comparison;
9. no unchecked leader read occurs before the first post-gate send;
10. `--check_alignment_only` never sends arm positions and skips keyboard/visualization;
11. `--max_start_mismatch` rejects non-finite and non-positive inputs;
12. existing Packet 14A and Packet 14B focused tests remain green.

Validation remains fake-only: focused pytest files, `py_compile`, CLI `--help`, fresh-process import checks, `git diff --check`, and final diff review. No COM port, Pi host, calibration, teleoperation hardware, camera, or motor command is used.

## Documentation

The native-Windows Aloha Mini documentation will state that leader and follower arm positions use normalized units by default, followers must be manually placed to match leaders before host activation until a clutch/alignment mode exists, and `--check_alignment_only` is mandatory before first motion. It will include exact alignment-only and bounded teleoperation commands.
