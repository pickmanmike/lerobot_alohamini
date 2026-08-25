# Aloha Mini 1 Simple Leader Calibration Design

## Status and Purpose

This specification replaces the Packet 2N-R5 staged leader-calibration runner as the normal Aloha Mini 1 calibration path. It starts from commit `a5a61873ec64ea1e9b95b344ea689a3d4bd9a786` on branch `fix/am1-elbow-commissioning`.

The old runner remains available as historical forensic tooling, but its receipt, journal, state-retirement, mapping-session, and recovery machinery are no longer part of normal calibration. The replacement is a supervised one-shot workflow with an isolated file-staging area, one backup, strict validation, and concise success or failure output.

This document defines software and documentation changes only. It does not authorize opening a COM port, calibrating a leader, connecting to the Raspberry Pi, starting ZMQ, or moving hardware.

## Established Offline State

Repository constants resolve the active calibration root through `HF_LEROBOT_CALIBRATION`; no source code should hard-code a user-profile path.

The active AM1 pair was independently inspected and classified as `TRUSTED_ORIGINAL_PAIR`:

| Side | SHA-256 | Size | UTC mtime |
| --- | --- | ---: | --- |
| Left | `6F5D6126E84398D0621A26E74E4DF6678EBA7C14C62D343020610B4D5D8B3D8C` | 960 | `2026-08-15T05:18:25.9699568Z` |
| Right | `65A301F20FC7DC96BD7FB5982E3670BF1A01F535953D7A253AB8D33A03646F11` | 961 | `2026-08-15T05:19:53.2654429Z` |

Both files match the immutable originals byte-for-byte and pass the expected six-joint calibration schema. Restoration is therefore neither necessary nor permitted during this correction.

The failed interrupted run, mixed pair, transcript, retired state, immutable originals, archive record, and recovery receipt already exist together under the archive named:

```text
packet2n-r5-interrupted-897f00dc-2608-4790-a74b-1482220eb5ed
```

The observed `INVALID_OR_UNCERTAIN_STATE` classification came from the old runner rejecting receipt provenance after the runner commit changed. It did not indicate damage to the restored active calibration pair. No live state file or transaction journal remains; three live evidence files have identical copies in the completed archive.

Before implementation changes, one additional timestamped retirement snapshot will copy the active pair and those remaining live evidence files. Existing backups, archives, logs, and transcripts remain untouched. Because the active pair already matches the trusted originals, the normalization step performs no restoration and preserves both active mtimes.

## Goals

The replacement provides:

- a direct, read-only raw bus stability check;
- a one-shot bimanual calibration command with exact physical/logical identity;
- isolated calibration-file staging until both sides complete and validate;
- one timestamped backup of the active AM1 calibration pair;
- pair-consistent promotion that preserves unrelated SO leader calibrations;
- ordinary rerun after a failed or disconnected calibration;
- a concise leader-only physical-side verification;
- focused fake tests rather than another transaction-state test campaign.

## Non-Goals

The replacement does not:

- add a persistent session, stage, receipt, journal, mapping record, or recovery state;
- change generic SO leader behavior, non-AM1 calibration files, motor IDs, Phase, PID, limits, baud rates, or action normalization;
- change AM2 or AM2 Pro behavior;
- connect to followers, the Pi, ZMQ, cameras, base, or lift;
- automatically resume or complete a failed physical calibration;
- delete or rewrite the Packet 2N-R5 runner or historical evidence;
- make a calibration attempt atomic at the motor-register level.

## Durable Identity

The new workflow fixes these values in its normal operator path:

| Identity | Required value |
| --- | --- |
| Physical/logical left leader | `COM8` |
| Physical/logical right leader | `COM7` |
| Teleoperator ID | `so101_leader_bi` |
| Arm profile | `so-arm-5dof` |
| Repository Python | `<repository>\\.venv\\Scripts\\python.exe` |

No command-line override may silently select different ports, IDs, profiles, Python executables, or active calibration roots. Incorrect confirmation or identity data must refuse before any motor-bus object is constructed.

## Operator Interfaces

The normal status command is:

```powershell
.\tools\calibrate_am1_leaders.ps1 -Status
```

The normal calibration command is:

```powershell
.\tools\calibrate_am1_leaders.ps1 -Calibrate -Confirm CALIBRATE
```

Exactly one of `-Status` and `-Calibrate` is required. `-Calibrate` requires the case-sensitive confirmation `CALIBRATE`. `-Status` is read-only, constructs no leader or bus, and does not create a backup, staging directory, transcript, or state file.

The raw bus checker command is:

```powershell
.\.venv\Scripts\python.exe .\tools\check_am1_leader_buses.py CHECK
```

The positional confirmation must be exactly `CHECK`. Missing, lowercase, whitespace-modified, or otherwise different input exits before port discovery or bus construction. Help remains safe and opens no port.

## Read-Only Bus Checker

The existing standalone checker is retained and given the direct `CHECK` gate. It remains independent of the historical runner.

After authorization it:

- verifies that `COM8` and `COM7` are present;
- constructs raw Feetech buses for IDs `1..6` with no calibration;
- connects without handshake/configuration;
- reads raw `Present_Position` for all twelve motors for a bounded duration, defaulting to approximately 30 seconds at approximately 10 Hz;
- uses no retry that could hide a missing acknowledgement;
- records every sample only after all six values on that side validate as integral, non-boolean raw register values;
- fails on any read error, missing ID, malformed value, or disappearing port;
- always disconnects with `disable_torque=False`;
- prints ports, IDs, sample count, first and last vectors, per-ID minima and maxima, each communication failure, and `PASS` only if every ID responded on every sample.

The checker must never call calibration loading, configure, write, `sync_write`, torque enable/disable, operating-mode changes, homing-offset changes, or any other register mutation.

The deprecated runner may pass `CHECK` to this checker so its historical bus-check stage remains internally consistent, but the checker does not import or depend on the runner.

## Explicit Calibration Staging

`examples/alohamini/calibrate_bi.py` gains one optional calibration-directory argument. It passes that value to the existing `BiSOLeaderConfig.calibration_dir` field.

When omitted, the value remains `None`; existing calibration, recording, teleoperation, AM2, and AM2 Pro paths continue resolving calibration exactly as before. No core LeRobot class or generic SO leader class changes.

The wrapper supplies a fresh per-run leaf directory explicitly:

```text
<run-evidence>\staged-calibration\teleoperators\so_leader
```

The native command is equivalent to:

```powershell
.\.venv\Scripts\python.exe .\examples\alohamini\calibrate_bi.py `
  --teleop.left_port COM8 `
  --teleop.right_port COM7 `
  --teleop.id so101_leader_bi `
  --teleop.arm_profile so-arm-5dof `
  --teleop.calibration_dir <staging-leaf> `
  --force_fresh_calibration
```

An explicit argument is preferred to a child environment override because the destination is visible in the command and transcript. The generic `lerobot-calibrate` command is not used because the Aloha-specific script already has the required normalized configuration and partial-connect cleanup.

## Provenance Preflight

Before any calibration child can start, the wrapper:

1. derives the repository root from its own location;
2. requires the resolved executable to equal `<repository>\\.venv\\Scripts\\python.exe`;
3. rejects import-altering `PYTHONPATH`, `PYTHONHOME`, `PYTHONSTARTUP`, and `PYTHONUSERBASE` values;
4. runs a no-hardware import probe from the repository root;
5. verifies `cwd`, `sys.executable`, and `sys.prefix`;
6. verifies imported `lerobot`, `calibrate_bi`, `leader_client_utils`, `BiSOLeader`, and `SOLeader` files resolve beneath the expected repository paths;
7. resolves the active root through `HF_LEROBOT_CALIBRATION` using that exact Python;
8. prints the branch, full HEAD, and porcelain worktree status for evidence without requiring a clean worktree at physical runtime.

These checks establish executable and source ownership directly. The new workflow does not retain behavior hashes, editable-install receipts, `.pth` inventories, session bindings, or runner-version state.

## One-Shot Calibration Lifecycle

After confirmation and provenance preflight, the wrapper follows one linear execution:

1. Resolve and validate the active pair without changing it.
2. Enumerate the complete active `teleoperators\\so_leader` tree and capture every regular file's relative path and byte hash so changes can be detected. Refuse any directory, symlink, junction, reparse point, or other nonregular entry below that directory.
3. Create a new timestamped run-evidence directory.
4. Copy the two active AM1 files into a timestamped pair backup beneath that run directory and verify both copies.
5. Create a fresh empty staging leaf.
6. Launch the native calibration command once, stream its output to the operator, capture one transcript, and capture the native exit code immediately.
7. On launch failure, nonzero exit, or disconnect, preserve the backup, partial staging files, and transcript; verify the active-directory hashes are unchanged; print `CALIBRATION_RESULT=FAIL`; exit nonzero.
8. On native success, validate both staged files completely.
9. Reverify that the active directory is byte-for-byte unchanged since preflight.
10. Create a unique, nonexistent candidate as a direct sibling of the active directory, clone every regular active file into it, and replace only the two AM1 files with the validated staged pair.
11. Verify the candidate AM1 pair and prove every unrelated file remains byte-for-byte unchanged.
12. Promote the candidate directory with the pair-consistent operation described below.
13. Verify both active hashes against the staged hashes.
14. Print `CALIBRATION_RESULT=PASS`, active paths and hashes, backup path, staged evidence path, and the no-robot side-verification command.

Every run uses a new timestamp. Failed staging output is never reused as promotion input. An ordinary rerun starts a new attempt after the physical connection is corrected.

Calibration-file staging does not undo motor-register writes. The underlying bimanual implementation calibrates left and then right, and each successful side writes its motor calibration before saving its JSON. If the right side disconnects after the left completes, the active JSON pair stays unchanged but the left leader registers may already reflect the interrupted attempt. The correct recovery is a complete fresh rerun with both buses stable.

## Staged Pair Validation

Both staged files must exist, be regular nonempty files, have distinct paths, and contain distinct payloads. Each must be a JSON object with exactly these joints and IDs:

| Joint | ID |
| --- | ---: |
| `shoulder_pan` | 1 |
| `shoulder_lift` | 2 |
| `elbow_flex` | 3 |
| `wrist_flex` | 4 |
| `wrist_roll` | 5 |
| `gripper` | 6 |

Every joint has exactly five fields:

```text
id
drive_mode
homing_offset
range_min
range_max
```

All five values are JSON integers, not booleans. `drive_mode` is `0`; IDs are unique and exact; homing offsets may be signed. For every non-wrist joint, `0 <= range_min < range_max <= 4095`. `wrist_roll` must be exactly `0..4095` because the current SO leader implementation excludes it from manual range recording and assigns the full raw turn after homing.

Any missing side, malformed JSON, extra or missing joint/field, invalid type, duplicate/wrong ID, invalid range, incorrect wrist-roll range, or identical left/right payload prevents promotion.

## Pair-Consistent Promotion on Windows

Replacing two files sequentially creates a mixed-pair window, so the wrapper promotes a complete directory candidate instead. The candidate begins as a clone of the active `so_leader` directory, then only the two AM1 files are replaced. This preserves AM2, AM2 Pro, other SO leader IDs, and unrelated regular files.

The wrapper defines three distinct filesystem locations:

- `<run-evidence>\\backup-active-pair` is the persistent pre-calibration copy of the two AM1 files;
- `<active-parent>\\.am1-candidate-<unique-run-id>` is a nonexistent direct sibling used for the complete promotion candidate;
- `<active-parent>\\.am1-withdrawn-<unique-run-id>` is a nonexistent direct sibling used only for immediate rollback during promotion.

The wrapper verifies that the candidate and withdrawal paths are on the active directory's volume, are direct children of the same resolved parent, do not exist, and have no reparse-point ancestor below that parent. Clone validation requires the candidate's complete relative regular-file path set and hashes to match the active snapshot except for exactly the two AM1 leaves. The same comparison is repeated against the final active directory after promotion.

With all LeRobot processes stopped, promotion uses a temporary rollback directory and two same-volume directory renames:

1. print the active, candidate, withdrawal, and persistent pair-backup paths plus the exact fail-closed recovery instruction;
2. rename the active directory to the unique sibling withdrawal path;
3. rename the fully validated candidate directory to the active path.

If the second rename throws, the wrapper immediately renames the withdrawal directory to the active path and preserves the original exception. After a successful second rename, the wrapper verifies the active pair, the unrelated files, and the timestamped pair backup before removing the redundant withdrawal directory. Handled failures therefore restore the complete old directory and never expose a mixed AM1 pair.

Windows does not provide a supported atomic exchange of two nonempty directories. A process termination or power loss between the two renames can leave the active path temporarily absent while the complete old directory remains at the printed sibling withdrawal path and the AM1 pair also remains in the timestamped backup. This is deliberately fail-closed: later LeRobot startup finds missing calibration rather than a mixed pair. With all leader power off and no LeRobot process running, the documented recovery is to verify that the active path is absent and the printed withdrawal path is a complete ordinary directory, then rename that exact withdrawal directory back to the active `so_leader` name. A crash after the second rename but before cleanup can leave a valid active directory plus the redundant withdrawal directory; the active tree must be inspected before any manual cleanup. Avoiding these narrow crash cases would require durable recovery state or indirection, which this design explicitly rejects as disproportionate complexity.

After those checks, the printed fail-closed recovery command has this exact shape:

```powershell
Rename-Item -LiteralPath '<printed-withdrawal-path>' -NewName 'so_leader'
```

The wrapper must not delete the timestamped pair backup after success. It may remove only the exact verified sibling withdrawal directory after successful promotion. It refuses promotion if a defined pre-promotion snapshot check detects an active-tree change or if unrelated candidate files do not match. The operator precondition that all other LeRobot processes are stopped remains necessary because no filesystem snapshot or lock eliminates the final check-to-rename race.

## Status Output

`-Status` prints only current facts:

- `classification` as `VALID_COMPLETE_PAIR` or `INCOMPLETE_OR_INVALID_PAIR`;
- repository Python and resolved calibration root;
- exact left and right active paths;
- existence, size, UTC mtime, and SHA-256 for each side;
- schema-validation result and exact failure reason.

`-Status` does not compare against a historical receipt, session, or runner commit and therefore cannot distinguish a coherent fresh pair from a coherent provenance-mixed pair. The one-time offline inspection retains the stronger `TRUSTED_ORIGINAL_PAIR` evidence classification in this design and the completion report; routine status reports only whether the current pair is internally complete and valid.

## No-Robot Physical-Side Verification

After a separately authorized successful physical calibration, documentation provides one bounded leader-only command:

```powershell
.\.venv\Scripts\python.exe .\examples\alohamini\teleoperate_bi.py `
  --no_robot `
  --robot.robot_model alohamini1 `
  --teleop.left_port COM8 `
  --teleop.right_port COM7 `
  --teleop.id so101_leader_bi `
  --teleop.arm_profile so-arm-5dof `
  --require_calibration_match `
  --duration_s 30 `
  --fps 5 `
  --no_keyboard `
  --no_rerun
```

The operator moves only the physical left gripper and verifies that only `arm_left_gripper.pos` responds, then moves only the physical right gripper and verifies that only `arm_right_gripper.pos` responds. This command connects and configures the two leader buses; `--no_robot` means follower/Pi exclusion, not raw read-only leader access.

The check stops immediately for wrong-side motion, movement in both logical sides, missing samples, a communication error, unexpected sound/current/heat, or any controller disconnect. It creates no mapping state or receipt.

## Failure and Cleanup Semantics

- Expected confirmation, identity, provenance, active-pair, staged-schema, concurrency, or native-command failures print one exact reason and `CALIBRATION_RESULT=FAIL` without claiming success.
- No preflight refusal constructs hardware.
- A native command that was not launched or returned nonzero can never produce `PASS`.
- Partial staged files and the transcript remain available after failure.
- The active directory must remain byte-for-byte unchanged after any failure before promotion.
- A handled promotion failure restores the complete withdrawal directory to the active path.
- The original native or promotion failure remains primary; cleanup/rollback errors are reported without replacing it.
- `Ctrl+C` terminates the native calibration child, preserves evidence, leaves the active pair unchanged when promotion has not begun, and returns nonzero.

## Implementation Surface

The expected focused diff is:

- `examples/alohamini/calibrate_bi.py`;
- `tools/check_am1_leader_buses.py`;
- `tools/calibrate_am1_leaders.ps1`;
- one compatibility argument in `tools/packet2n_r5_leader_mapping.ps1`;
- focused tests under `tests/robots/`;
- `docs/alohamini/alohamini.md`;
- this specification and the later implementation plan.

Core motors, generic SO leader classes, robot/follower code, calibration contents, dependency files, AM2/AM2 Pro source, cameras, base, lift, Pi host, and ZMQ remain unchanged.

The documentation update must replace the current “sole authority” language and live normal-use Packet 2N-R5 commands with the new one-shot path, label the old runner section and commands as historical/deprecated, and correct any normal-use AM1 examples that still assign left to `COM7` and right to `COM8`. Historical evidence may retain its originally recorded command text when clearly labeled non-repeatable.

## Test-Driven Implementation Requirements

Implementation uses red-green TDD. Before each production behavior is added, the smallest focused fake test must fail for the intended missing behavior and then pass after the implementation.

Tests must prove:

1. offline state inspection identifies the current complete pair without trusting historical receipts;
2. a failed or non-launched calibration leaves every active byte unchanged;
3. a valid staged pair promotes together and post-promotion hashes match;
4. a missing or malformed side prevents promotion;
5. wrong confirmation, COM ownership, ID, profile, executable, or import provenance refuses before hardware/native calibration construction;
6. native nonzero status cannot be printed as success;
7. the complete unrelated relative path set and file hashes survive successful promotion byte-for-byte, while reparse or nonregular entries refuse before calibration;
8. active-directory concurrent change prevents promotion;
9. a simulated second-rename failure restores the complete backup and preserves the primary error;
10. the bus checker requires exact `CHECK`, both ports, and IDs `1..6`;
11. the bus checker invokes no calibration, configure, write, torque, or mode method and disconnects with `disable_torque=False`;
12. AM2 and AM2 Pro parsing and behavior remain unchanged.

PowerShell tests use temporary directories and fake native invokers. Pure helper functions may be exercised by dot-sourcing the wrapper; no test-only option appears in the normal operator interface. No test opens a COM port, contacts the Pi, starts ZMQ/cameras, runs calibration, or touches the active user calibration directory.

## Validation and Acceptance

The implementation is acceptable only when:

- the approved active pair has been copied to the one-time retirement snapshot and not restored or rewritten;
- the development report records every required RED failure and its intended reason;
- focused wrapper, bus-checker, calibration CLI, Windows client, and AM2/AM2 Pro tests pass;
- PowerShell parsing, `py_compile`, relevant `--help`, fresh-process imports, and lazy-visualization checks pass;
- the complete diff passes `git diff --check` and manual review;
- added lines contain no private-key marker, token, credential, secret, or hard-coded user-profile path;
- the old runner and every historical artifact remain present;
- the branch remains `fix/am1-elbow-commissioning`, commits preserve existing history, and the final worktree is clean;
- no repository action opens COM, contacts the Pi, starts ZMQ, calibrates, or moves physical hardware.
