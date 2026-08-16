# Aloha Mini 1 Startup Synchronization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a client-side, operator-authorized, bounded joint-space startup synchronization mode for Aloha Mini 1 while preserving strict mode unchanged.

**Architecture:** Keep pure synchronization planning and validation separate from device I/O. The Windows client measures follower start positions, freezes a validated leader target, sends bounded selected-side interpolation frames with zero base/lift commands, verifies the result, and only then exits or enters ordinary absolute teleoperation.

**Tech Stack:** Python 3.12, argparse, dataclasses, existing Aloha Mini ZMQ client, passive Feetech bimanual leaders, pytest/fakes, PowerShell commissioning environment.

## Global Constraints

- This implementation is for Aloha Mini 1 only. `--startup_mode sync` must reject `alohamini2` and `alohamini2pro` before constructing a device.
- `--startup_mode strict` remains the default and preserves Packet 18C-R2 behavior, including `--check_alignment_only`, both alignment gates, pending-first-action forwarding, normalized per-sample validation, and status `2` safety refusals.
- Define `STARTUP_SYNC_MAX_STEP = 0.75` normalized units per frame exactly.
- Define `STARTUP_SYNC_LEADER_DRIFT = 2.0` normalized units exactly; equality passes and only drift strictly greater than `2.0` refuses.
- Automatic follower motion requires the operator to type exact uppercase `SYNC`. Enter alone, lowercase `sync`, leading/trailing whitespace, or any other response refuses without an arm-position send.
- Aloha Mini 1 body joints remain normalized to `-100..100`; grippers remain normalized to `0..100`; `ACTION_RANGE_TOLERANCE = 1e-6` remains the only boundary tolerance.
- Every leader sample used by synchronization or teleoperation must contain exactly the twelve expected `arm_*.pos` keys, be numeric and finite, and satisfy its normalized range. Legitimate zero base/lift keys are not unexpected arm data.
- Synchronization frames contain only the selected arm side or sides and explicit zero `x.vel`, `y.vel`, `theta.vel`, and `lift_axis.vel`; no normal leader mirroring occurs during synchronization.
- `--max_start_mismatch` applies only at startup/alignment/final-verification gates, never continuously during ordinary motion.
- Expected runtime safety refusals print the exact actionable reason, send no further arm-position action, use existing best-effort cleanup, and return status `2` without a traceback.
- Unexpected failures and `KeyboardInterrupt` retain their primary identity after best-effort zero and disconnect cleanup.
- Do not change the Raspberry Pi host, host command schema, or Pi `max_relative_target` behavior.
- Do not change calibration contents, calibration IDs, calibration paths, or calibration procedures; existing leader calibration files remain reusable.
- Do not add startup synchronization to `record_bi.py` or change dataset recording.
- Do not change generic `SOLeader`, `SOLeaderConfig`, `BiSOLeader`, or normalized leader configuration behavior.
- Do not change Aloha Mini 2 or Aloha Mini 2 Pro behavior.
- Do not add collision planning, Cartesian interpolation, clutch/rebase, automatic confirmation bypass, return-to-start motion, active leader torque, or haptic feedback.
- No software-validation command may open a COM port, contact the Raspberry Pi, construct cameras, start ZMQ, run calibration/teleoperation, or access physical hardware.
- Existing Packet 14A, Packet 14B, and Packet 18C-R2 focused tests must remain green.
- Follow red-green TDD: add the focused fake-based test, run it and observe the intended failure, implement only the required behavior, then rerun it to green before the task commit.
- Tests must inspect ordered event streams and actual action payloads, not only helper arithmetic or mock call counts.
- The approved behavior is defined by `docs/superpowers/specs/2026-08-15-am1-startup-sync-design.md`; this plan names implementation steps without replacing that specification.

---

## File Responsibility Map

| File and current region | Planned responsibility |
| --- | --- |
| `examples/alohamini/teleoperate_bi.py:21-54` | Add sync constants, immutable plan type, and required typing imports. |
| `examples/alohamini/teleoperate_bi.py:69-202` | Reuse exact AM1 extraction, follower freshness, alignment rows/table, and strict `run_alignment_gate()`; add selected-side validation, pure plan/action builders, drift validation, final verification, and sync orchestration beside these helpers. |
| `examples/alohamini/teleoperate_bi.py:205-288` | Add the four CLI options and all pre-construction compatibility checks. |
| `examples/alohamini/teleoperate_bi.py:329-513` | Integrate sync into `run_teleoperation()` without allowing strict mode to traverse sync code; preserve pending-action, optional-resource, duration, and cleanup ordering. |
| `tests/robots/test_alohamini_windows_leader_client.py:29-550` | Extend the existing literal AM1 poses and fake event stream with deterministic time, recorded sleeps, configurable observation freshness, and leader read failures. Add parser and pure-helper tests. |
| `tests/robots/test_alohamini_windows_leader_client.py:552-999` | Add ordered integration tests for confirmation, frozen targets, sync payloads, drift, verification, handoff, status, cleanup, and model isolation while retaining every existing Packet 14B/R2 test. |
| `docs/alohamini/alohamini.md:58-127` | Replace the strict-only native-Windows commissioning sequence with the approved warning, mode explanation, and S1 through S6 stop/go commands. Preserve calibration-reuse guidance. |
| `examples/alohamini/leader_client_utils.py:32-90` | Read-only boundary. Keep normalized `use_degrees=False` construction and Windows port resolution unchanged; no sync helper is shared with calibration or recording. |
| `tests/robots/test_alohamini_safe_bringup.py:45-415` and `src/lerobot/robots/alohamini/*` | Read-only Packet 14A regression boundary. Run its fake-bus tests; do not edit host, activation, lift, camera, or motor-safety code. |
| `examples/alohamini/record_bi.py` and calibration files | Read-only boundary. Import/regression checks only; no synchronization flags or behavior. |

The implementation diff should therefore contain exactly these three paths: `examples/alohamini/teleoperate_bi.py`, `tests/robots/test_alohamini_windows_leader_client.py`, and `docs/alohamini/alohamini.md`.

## Interface Contract Used Across Tasks

Task implementations use these exact names and signatures so later steps do not have to infer neighboring interfaces:

```python
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, NamedTuple

StartupSyncSide = Literal["left", "right", "both"]

STARTUP_SYNC_MAX_STEP = 0.75
STARTUP_SYNC_LEADER_DRIFT = 2.0

@dataclass(frozen=True)
class StartupSyncPlan:
    side: StartupSyncSide
    selected_keys: tuple[str, ...]
    follower_start: Mapping[str, float]
    frozen_leader_target: Mapping[str, float]
    requested_duration_s: float
    fps: int
    max_abs_delta: float
    total_steps: int
    frame_count: int
    largest_planned_per_frame_change: float
    estimated_actual_duration_s: float

def selected_arm_position_keys(side: StartupSyncSide) -> tuple[str, ...]: ...

def validate_selected_sync_positions(
    positions: Mapping[str, float],
    selected_keys: tuple[str, ...],
    *,
    source: str,
) -> None: ...

def build_startup_sync_plan(
    follower_start: Mapping[str, float],
    frozen_leader_target: Mapping[str, float],
    *,
    side: StartupSyncSide,
    requested_duration_s: float,
    fps: int,
) -> StartupSyncPlan: ...

def build_startup_sync_action(
    plan: StartupSyncPlan,
    frame_index: int,
) -> dict[str, float | int]: ...

def validate_startup_sync_leader_drift(
    current_leader: Mapping[str, float],
    frozen_leader_target: Mapping[str, float],
    selected_keys: tuple[str, ...],
) -> None: ...

def verify_startup_sync_result(
    follower_positions: Mapping[str, float],
    frozen_leader_target: Mapping[str, float],
    *,
    selected_keys: tuple[str, ...],
    max_start_mismatch: float,
) -> list[AlignmentRow]: ...

def _print_startup_sync_plan(plan: StartupSyncPlan, *, label: str) -> None: ...
def _print_startup_sync_safety_instructions() -> None: ...

def run_startup_sync(
    robot: Any,
    leader: Any,
    *,
    side: StartupSyncSide,
    requested_duration_s: float,
    fps: int,
    max_start_mismatch: float,
    input_fn: Callable[[str], str],
    monotonic: Callable[[], float],
    sleep_fn: Callable[[float], None],
) -> tuple[dict[str, float], dict[str, Any]]: ...
```

`StartupSyncPlan.follower_start` and `.frozen_leader_target` are `MappingProxyType` wrappers around fresh canonical-order copies, so `@dataclass(frozen=True)` is not merely shallow protection. `run_startup_sync()` returns a new plain dictionary for the verified frozen target plus the final fresh follower observation; the orchestration retains that dictionary only as the pending first ordinary action.

The test module adds these exact helpers:

```python
class FakeClock:
    def __init__(self, events: list[tuple]): ...
    def monotonic(self) -> float: ...
    def sleep(self, duration_s: float) -> None: ...
    def advance(self, duration_s: float) -> None: ...

def arm_send_actions(events: list[tuple]) -> list[dict[str, float | int]]: ...
def sync_args(module, *extra_args: str): ...
```

`FakeRobot` retains `observation_poses` and `observation_sequence`, and gains `observation_sequence_advances: list[bool]`. `FakeLeader.action_poses` accepts `dict[str, float] | BaseException`; a queued exception is recorded and raised by `get_action()`. These are test-only seams and never touch hardware.

## Execution Preflight

- [ ] Verify `git branch --show-current` prints `fix/am1-startup-sync` and `git status --short` prints nothing.
- [ ] Resolve the implementation base with `git log -1 --format=%H -- docs/superpowers/plans/2026-08-16-am1-startup-sync-implementation.md`; confirm it equals `git rev-parse HEAD` before Task 1 begins.
- [ ] Confirm the Raspberry Pi host remains stopped, follower/body 12 V power remains off, leader low-voltage supplies remain off, and both leader USB controllers remain disconnected.
- [ ] Run the fake-only baseline and require zero failures before editing:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests\robots\test_alohamini_safe_bringup.py `
  tests\robots\test_alohamini_windows_leader_client.py -q
```

Do not install dependencies or substitute a hardware-backed test if this baseline cannot run. Report the local software blocker and keep all physical interfaces untouched.

---

### Task 1: CLI contract and compatibility validation

**Files:**
- Modify: `examples/alohamini/teleoperate_bi.py:205-288`
- Test: `tests/robots/test_alohamini_windows_leader_client.py:147-185`

**Interfaces:**
- Consumes: existing `build_parser() -> argparse.ArgumentParser`, `parse_args(argv, *, platform_name) -> argparse.Namespace`, and existing `--max_start_mismatch`, `--check_alignment_only`, `--start_paused`, `--duration_s`, `--no_robot`, and `--no_leader` arguments.
- Produces: `args.startup_mode: str`, `args.startup_sync_duration_s: float`, `args.startup_sync_side: str`, and `args.startup_sync_only: bool` with all incompatibilities rejected by `parser.error()` before `resolve_leader_ports()` returns.

- [ ] **Step 1: Add failing parser-default and duration-validation tests**

Add these tests next to the existing alignment-threshold parser tests:

```python
def test_startup_sync_cli_defaults_preserve_strict_mode():
    module = load_example_module("teleoperate_bi")

    args = module.parse_args([], platform_name="Linux")

    assert args.startup_mode == "strict"
    assert args.startup_sync_duration_s == 12.0
    assert args.startup_sync_side == "both"
    assert args.startup_sync_only is False
    assert args.max_start_mismatch == 10.0


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "-inf"])
def test_startup_sync_duration_rejects_nonpositive_or_nonfinite(capsys, value):
    module = load_example_module("teleoperate_bi")

    with pytest.raises(SystemExit) as caught:
        module.parse_args([f"--startup_sync_duration_s={value}"], platform_name="Linux")

    assert caught.value.code == 2
    assert "--startup_sync_duration_s must be finite and greater than zero" in capsys.readouterr().err
```

- [ ] **Step 2: Add failing compatibility-matrix tests**

```python
@pytest.mark.parametrize(
    ("argv", "reason"),
    [
        (["--startup_mode", "sync", "--robot_model", "alohamini2"], "sync is supported only for alohamini1"),
        (["--startup_mode", "sync", "--robot_model", "alohamini2pro"], "sync is supported only for alohamini1"),
        (["--startup_mode", "sync", "--no_robot"], "sync requires both robot and leader connections"),
        (["--startup_mode", "sync", "--no_leader"], "sync requires both robot and leader connections"),
        (["--startup_sync_only"], "--startup_sync_only requires --startup_mode sync"),
        (["--startup_sync_side", "left"], "left or right requires --startup_sync_only"),
        (["--startup_sync_side", "right"], "left or right requires --startup_sync_only"),
        (["--startup_mode", "sync", "--startup_sync_side", "left"], "left or right requires --startup_sync_only"),
        (["--startup_mode", "sync", "--startup_sync_side", "right"], "left or right requires --startup_sync_only"),
        (["--startup_mode", "sync", "--check_alignment_only"], "--check_alignment_only is incompatible with --startup_mode sync"),
    ],
)
def test_startup_sync_rejects_incompatible_arguments(capsys, argv, reason):
    module = load_example_module("teleoperate_bi")

    with pytest.raises(SystemExit) as caught:
        module.parse_args(argv, platform_name="Linux")

    assert caught.value.code == 2
    assert reason in capsys.readouterr().err


@pytest.mark.parametrize("side", ["left", "right"])
def test_startup_sync_allows_one_side_only_for_sync_only(side):
    module = load_example_module("teleoperate_bi")

    args = module.parse_args(
        [
            "--startup_mode", "sync",
            "--startup_sync_side", side,
            "--startup_sync_only",
            "--start_paused",
            "--duration_s", "30",
        ],
        platform_name="Linux",
    )

    assert args.startup_sync_side == side
    assert args.start_paused is True
    assert args.duration_s == 30.0


def test_startup_sync_allows_both_for_normal_teleoperation():
    module = load_example_module("teleoperate_bi")

    args = module.parse_args(["--startup_mode", "sync"], platform_name="Linux")

    assert args.startup_sync_side == "both"
    assert args.startup_sync_only is False
```

- [ ] **Step 3: Add a failing help-contract test**

```python
def test_startup_sync_help_explains_sync_only_ignored_options():
    module = load_example_module("teleoperate_bi")

    help_text = module.build_parser().format_help()
    sync_only_action = next(
        action for action in module.build_parser()._actions if action.dest == "startup_sync_only"
    )

    assert "--startup_mode {strict,sync}" in help_text
    assert "--startup_sync_duration_s" in help_text
    assert "--startup_sync_side {left,right,both}" in help_text
    assert "--startup_sync_only" in help_text
    assert "--start_paused has no effect" in sync_only_action.help
    assert "--duration_s is unused" in sync_only_action.help
```

- [ ] **Step 4: Run the CLI tests and observe RED**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests\robots\test_alohamini_windows_leader_client.py `
  -k "startup_sync_cli or startup_sync_duration or startup_sync_rejects_incompatible or startup_sync_allows or startup_sync_help" -q
```

Expected RED: `argparse` reports unrecognized `--startup_*` arguments, and the default test raises `AttributeError` because the four namespace attributes do not exist. Confirm failures come from missing CLI behavior, not an import/dependency error.

- [ ] **Step 5: Add the four parser options with exact defaults and help**

Add to `build_parser()` before `--max_start_mismatch`:

```python
parser.add_argument(
    "--startup_mode",
    choices=("strict", "sync"),
    default="strict",
    help="AM1 startup behavior: strict alignment refusal or operator-authorized synchronization (default: strict)",
)
parser.add_argument(
    "--startup_sync_duration_s",
    type=float,
    default=12.0,
    help="Requested minimum synchronization duration in seconds (default: 12.0)",
)
parser.add_argument(
    "--startup_sync_side",
    choices=("left", "right", "both"),
    default="both",
    help="Follower side synchronized by sync mode (default: both)",
)
parser.add_argument(
    "--startup_sync_only",
    action="store_true",
    help=(
        "Synchronize, verify, and exit; --start_paused has no effect and "
        "--duration_s is unused in this mode"
    ),
)
```

- [ ] **Step 6: Add exact pre-construction validation in `parse_args()`**

Keep validation deterministic even in strict mode, then apply compatibility checks:

```python
if not math.isfinite(args.startup_sync_duration_s) or args.startup_sync_duration_s <= 0:
    parser.error("--startup_sync_duration_s must be finite and greater than zero")
if args.startup_sync_only and args.startup_mode != "sync":
    parser.error("--startup_sync_only requires --startup_mode sync")
if args.startup_sync_side in {"left", "right"} and not args.startup_sync_only:
    parser.error("--startup_sync_side left or right requires --startup_sync_only")
if args.startup_mode == "sync":
    if args.robot_model != "alohamini1":
        parser.error("--startup_mode sync is supported only for alohamini1")
    if args.no_robot or args.no_leader:
        parser.error("--startup_mode sync requires both robot and leader connections")
    if args.check_alignment_only:
        parser.error("--check_alignment_only is incompatible with --startup_mode sync")
```

Leave the existing `--check_alignment_only` strict validations and `--max_start_mismatch` finite/positive validation intact.

- [ ] **Step 7: Run the CLI tests to verify GREEN**

Run the Step 4 command unchanged.

Expected GREEN: every selected test passes; invalid cases exit with status `2`, allowed one-side sync-only parsing succeeds, and defaults remain strict/both/`12.0`/false.

- [ ] **Step 8: Run focused parser regressions**

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests\robots\test_alohamini_windows_leader_client.py `
  -k "leader_ports or normalized_positions or alignment_threshold or alignment_only or startup_sync" -q
```

Expected: all selected Packet 14B/R2 parser and new sync parser tests pass.

- [ ] **Step 9: Review and commit Task 1**

```powershell
git diff --check
git diff -- examples/alohamini/teleoperate_bi.py tests/robots/test_alohamini_windows_leader_client.py
git add -- examples/alohamini/teleoperate_bi.py tests/robots/test_alohamini_windows_leader_client.py
git commit -m "feat(alohamini): add startup sync CLI contract"
```

Confirm the diff has parser/tests only and no sync runtime branch yet.

---

### Task 2: Pure synchronization planning and frame construction

**Files:**
- Modify: `examples/alohamini/teleoperate_bi.py:21-202`
- Test: `tests/robots/test_alohamini_windows_leader_client.py:48-61,286-399`

**Interfaces:**
- Consumes: `AM1_ARM_POSITION_KEYS`, `ACTION_RANGE_TOLERANCE`, `_joint_identity()`, `SafetyRefusal`, and `make_zero_action()`.
- Produces: `StartupSyncSide`, `STARTUP_SYNC_MAX_STEP`, `STARTUP_SYNC_LEADER_DRIFT`, immutable `StartupSyncPlan`, `selected_arm_position_keys()`, `validate_selected_sync_positions()`, `build_startup_sync_plan()`, and `build_startup_sync_action()` exactly as declared in the shared interface contract.

- [ ] **Step 1a: Make the dynamic test loader dataclass-safe**

Because `teleoperate_bi.py` will define a dataclass, register the dynamic module only while executing it:

```python
module = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(ALOHAMINI_EXAMPLES))
sys.modules[module_name] = module
try:
    spec.loader.exec_module(module)
finally:
    sys.modules.pop(module_name, None)
    sys.path.remove(str(ALOHAMINI_EXAMPLES))
```

- [ ] **Step 1b: Add the failing plan arithmetic tests**

Add these tests after `test_alignment_rows_use_leader_minus_follower_difference`:

```python
def test_startup_sync_plan_extends_duration_for_step_limit():
    module = load_example_module("teleoperate_bi")
    target = {**FOLLOWER_POSE, "arm_left_shoulder_pan.pos": 3.0}

    plan = module.build_startup_sync_plan(
        FOLLOWER_POSE,
        target,
        side="both",
        requested_duration_s=0.2,
        fps=5,
    )

    assert plan.max_abs_delta == 3.0
    assert plan.total_steps == 4
    assert plan.frame_count == 5
    assert plan.largest_planned_per_frame_change == 0.75
    assert plan.estimated_actual_duration_s == 0.8


def test_startup_sync_plan_preserves_duration_for_zero_displacement():
    module = load_example_module("teleoperate_bi")

    plan = module.build_startup_sync_plan(
        FOLLOWER_POSE,
        FOLLOWER_POSE,
        side="both",
        requested_duration_s=12.0,
        fps=5,
    )

    assert plan.total_steps == 60
    assert plan.frame_count == 61
    assert plan.largest_planned_per_frame_change == 0.0
    assert plan.estimated_actual_duration_s == 12.0
```

- [ ] **Step 2a: Add the failing endpoint, step-bound, and zero-body payload test**

```python
def test_startup_sync_actions_have_exact_endpoints_bounded_steps_and_zero_body():
    module = load_example_module("teleoperate_bi")
    target = {**FOLLOWER_POSE, "arm_left_shoulder_pan.pos": 3.0}
    plan = module.build_startup_sync_plan(
        FOLLOWER_POSE,
        target,
        side="both",
        requested_duration_s=0.2,
        fps=5,
    )

    frames = [module.build_startup_sync_action(plan, index) for index in range(plan.frame_count)]

    assert frames[0]["arm_left_shoulder_pan.pos"] == FOLLOWER_POSE["arm_left_shoulder_pan.pos"]
    assert frames[-1]["arm_left_shoulder_pan.pos"] == target["arm_left_shoulder_pan.pos"]
    for previous, current in zip(frames, frames[1:]):
        for key in plan.selected_keys:
            assert abs(current[key] - previous[key]) <= module.STARTUP_SYNC_MAX_STEP
    for frame in frames:
        assert {key: frame[key] for key in module.make_zero_action()} == module.make_zero_action()


```

- [ ] **Step 2b: Add the failing selected-side omission test**

```python
@pytest.mark.parametrize(
    ("side", "required_prefix", "forbidden_prefix"),
    [("left", "arm_left_", "arm_right_"), ("right", "arm_right_", "arm_left_")],
)
def test_startup_sync_action_omits_unselected_arm_keys(side, required_prefix, forbidden_prefix):
    module = load_example_module("teleoperate_bi")
    plan = module.build_startup_sync_plan(
        FOLLOWER_POSE,
        FOLLOWER_POSE,
        side=side,
        requested_duration_s=0.2,
        fps=5,
    )

    action = module.build_startup_sync_action(plan, 0)
    arm_keys = {key for key in action if key.startswith("arm_")}

    assert len(arm_keys) == 6
    assert all(key.startswith(required_prefix) for key in arm_keys)
    assert all(not key.startswith(forbidden_prefix) for key in arm_keys)


```

- [ ] **Step 2c: Add the failing selected-range and endpoint-immutability tests**

```python
def test_startup_sync_plan_rejects_out_of_range_selected_follower_start():
    module = load_example_module("teleoperate_bi")
    invalid_start = {**FOLLOWER_POSE, "arm_left_gripper.pos": -0.1}

    with pytest.raises(module.SafetyRefusal, match=r"follower left gripper.*0\.\.100"):
        module.build_startup_sync_plan(
            invalid_start,
            FOLLOWER_POSE,
            side="left",
            requested_duration_s=0.2,
            fps=5,
        )


def test_startup_sync_plan_copies_and_freezes_both_endpoint_mappings():
    module = load_example_module("teleoperate_bi")
    start = dict(FOLLOWER_POSE)
    target = dict(FOLLOWER_POSE)
    plan = module.build_startup_sync_plan(start, target, side="both", requested_duration_s=0.2, fps=5)
    start["arm_left_shoulder_pan.pos"] = 99.0
    target["arm_left_shoulder_pan.pos"] = 98.0

    assert plan.follower_start["arm_left_shoulder_pan.pos"] == 0.0
    assert plan.frozen_leader_target["arm_left_shoulder_pan.pos"] == 0.0
    with pytest.raises(TypeError):
        plan.frozen_leader_target["arm_left_shoulder_pan.pos"] = 1.0
```

- [ ] **Step 3: Run the pure sync tests and observe RED**

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests\robots\test_alohamini_windows_leader_client.py `
  -k "startup_sync_plan or startup_sync_action" -q
```

Expected RED: tests fail with missing `build_startup_sync_plan`, `build_startup_sync_action`, or `STARTUP_SYNC_MAX_STEP`. The loader itself must still import all three client scripts successfully.

- [ ] **Step 4: Add the immutable plan type, constants, side selector, and selected-range validator**

Add the imports and shared-interface definitions near `AM1_ARM_POSITION_KEYS`. Implement side selection in canonical tuple order and selected range validation without changing `extract_am1_arm_positions()` strict semantics:

```python
def selected_arm_position_keys(side: StartupSyncSide) -> tuple[str, ...]:
    if side not in {"left", "right", "both"}:
        raise ValueError(f"Unsupported startup sync side: {side!r}")
    if side == "both":
        return AM1_ARM_POSITION_KEYS
    return tuple(key for key in AM1_ARM_POSITION_KEYS if key.startswith(f"arm_{side}_"))


def validate_selected_sync_positions(positions, selected_keys, *, source):
    for key in selected_keys:
        side, joint = _joint_identity(key)
        try:
            value = float(positions[key])
        except (KeyError, TypeError, ValueError) as exc:
            raise SafetyRefusal(f"{source} {side} {joint} must be present and numeric") from exc
        if not math.isfinite(value):
            raise SafetyRefusal(f"{source} {side} {joint} value {value} must be finite")
        lower = 0.0 if joint == "gripper" else -100.0
        upper = 100.0
        if value < lower - ACTION_RANGE_TOLERANCE or value > upper + ACTION_RANGE_TOLERANCE:
            expected_range = "0..100" if joint == "gripper" else "-100..100"
            raise SafetyRefusal(f"{source} {side} {joint} value {value} is outside expected {expected_range}")
```

- [ ] **Step 5: Implement `build_startup_sync_plan()` with interval semantics**

Validate finite positive duration and positive `fps`, then create canonical copies with the existing exact-schema validator:

```python
if not math.isfinite(requested_duration_s) or requested_duration_s <= 0:
    raise SafetyRefusal("startup sync duration must be finite and greater than zero")
if fps <= 0:
    raise SafetyRefusal("startup sync fps must be greater than zero")

selected_keys = selected_arm_position_keys(side)
follower_copy = extract_am1_arm_positions(
    dict(follower_start), source="follower", leader_sample=False
)
target_copy = extract_am1_arm_positions(
    dict(frozen_leader_target), source="frozen leader target", leader_sample=True
)
validate_selected_sync_positions(follower_copy, selected_keys, source="follower")
max_abs_delta = max(abs(target_copy[key] - follower_copy[key]) for key in selected_keys)
duration_steps = math.ceil(requested_duration_s * fps)
step_limit_steps = math.ceil(max_abs_delta / STARTUP_SYNC_MAX_STEP)
total_steps = max(1, duration_steps, step_limit_steps)
frame_count = total_steps + 1
largest_change = max_abs_delta / total_steps
estimated_duration = total_steps / fps
```

Reject a computed `largest_change` greater than `STARTUP_SYNC_MAX_STEP + ACTION_RANGE_TOLERANCE`. Return the exact immutable shape and do not store caller-owned dictionaries:

```python
return StartupSyncPlan(
    side=side,
    selected_keys=selected_keys,
    follower_start=MappingProxyType(follower_copy),
    frozen_leader_target=MappingProxyType(target_copy),
    requested_duration_s=requested_duration_s,
    fps=fps,
    max_abs_delta=max_abs_delta,
    total_steps=total_steps,
    frame_count=frame_count,
    largest_planned_per_frame_change=largest_change,
    estimated_actual_duration_s=estimated_duration,
)
```

- [ ] **Step 6: Implement `build_startup_sync_action()` with exact endpoints**

Reject `frame_index` outside `0..plan.total_steps`. Use direct endpoint lookup at index `0` and `plan.total_steps`; interpolate only intermediate indices:

```python
if frame_index == 0:
    arm_action = {key: plan.follower_start[key] for key in plan.selected_keys}
elif frame_index == plan.total_steps:
    arm_action = {key: plan.frozen_leader_target[key] for key in plan.selected_keys}
else:
    alpha = frame_index / plan.total_steps
    arm_action = {
        key: plan.follower_start[key]
        + alpha * (plan.frozen_leader_target[key] - plan.follower_start[key])
        for key in plan.selected_keys
    }
```

Call `validate_selected_sync_positions(arm_action, plan.selected_keys, source="synchronization target")`. For every nonzero frame, compute the preceding value with the same endpoint rule and refuse an excessive adjacent change:

```python
if frame_index > 0:
    previous_index = frame_index - 1
    for key in plan.selected_keys:
        if previous_index == 0:
            previous_value = plan.follower_start[key]
        else:
            previous_alpha = previous_index / plan.total_steps
            previous_value = plan.follower_start[key] + previous_alpha * (
                plan.frozen_leader_target[key] - plan.follower_start[key]
            )
        change = abs(arm_action[key] - previous_value)
        if change > STARTUP_SYNC_MAX_STEP + ACTION_RANGE_TOLERANCE:
            raise SafetyRefusal(
                f"startup sync frame {frame_index} change for {key} is {change}, "
                f"above STARTUP_SYNC_MAX_STEP {STARTUP_SYNC_MAX_STEP}"
            )
return {**arm_action, **make_zero_action()}
```

Because the final endpoint is assigned directly, this check also protects a floating-point edge at `alpha=1`. The returned mapping cannot contain an unselected arm key.

- [ ] **Step 7: Run the pure sync tests to verify GREEN**

Run the Step 3 command unchanged.

Expected GREEN: duration and step limits produce the exact interval/frame counts, endpoint assignments are exact, all adjacent payload deltas are bounded, one-side actions contain six arm keys, every body/lift velocity is zero, and endpoint copies cannot mutate.

- [ ] **Step 8: Run validation and import regressions**

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests\robots\test_alohamini_windows_leader_client.py `
  -k "importing_client_script or am1_validation or fresh_follower or alignment_rows or startup_sync_plan or startup_sync_action" -q
```

Expected: all selected tests pass, including exact-key validation and the rule that legitimate zero body/lift keys are ignored by the arm subset.

- [ ] **Step 9: Review and commit Task 2**

```powershell
git diff --check
git diff -- examples/alohamini/teleoperate_bi.py tests/robots/test_alohamini_windows_leader_client.py
git add -- examples/alohamini/teleoperate_bi.py tests/robots/test_alohamini_windows_leader_client.py
git commit -m "feat(alohamini): add startup sync planning"
```

Confirm no I/O loop or `run_teleoperation()` sync branch was added in this task.

---

### Task 3: Operator authorization, frozen targets, drift, and verification

**Files:**
- Modify: `examples/alohamini/teleoperate_bi.py:69-204`
- Test: `tests/robots/test_alohamini_windows_leader_client.py:401-550` and new sync tests immediately after the fake helpers

**Interfaces:**
- Consumes: Task 2's `StartupSyncPlan`, selected-side and plan/action helpers; existing `extract_am1_arm_positions()`, `get_fresh_follower_observation()`, `build_alignment_rows()`, `_print_alignment_table()`, `SafetyRefusal`, and injected `input_fn`, `monotonic`, and `sleep_fn` callables.
- Produces: `validate_startup_sync_leader_drift()`, `verify_startup_sync_result()`, `_print_startup_sync_plan()`, `_print_startup_sync_safety_instructions()`, and `run_startup_sync()` exactly as declared in the shared interface contract.

- [ ] **Step 1: Extend the fake event infrastructure without changing production code**

Add deterministic time and reusable arm-send filtering:

```python
class FakeClock:
    def __init__(self, events: list[tuple]):
        self.now = 0.0
        self.events = events
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        self.events.append(("clock", "monotonic", self.now))
        return self.now

    def sleep(self, duration_s: float) -> None:
        assert duration_s >= 0
        self.sleeps.append(duration_s)
        self.events.append(("clock", "sleep", duration_s))
        self.now += duration_s

    def advance(self, duration_s: float) -> None:
        self.now += duration_s
        self.events.append(("clock", "advance", duration_s))


def arm_send_actions(events: list[tuple]) -> list[dict[str, float | int]]:
    return [
        event[2]
        for event in events
        if event[:2] == ("robot", "send") and any(key.startswith("arm_") for key in event[2])
    ]
```

Extend `FakeRobot` with `observation_sequence_advances: list[bool]` and replace its observation method with:

```python
def get_observation(self):
    pose_index = min(self.observation_index, len(type(self).observation_poses) - 1)
    observation = dict(type(self).observation_poses[pose_index])
    advances = type(self).observation_sequence_advances
    should_advance = advances[min(self.observation_index, len(advances) - 1)] if advances else True
    self.observation_index += 1
    if should_advance:
        self.observation_sequence += 1
    self.events.append(("robot", "get_observation", self.observation_sequence, observation))
    return observation
```

Add `observation_sequence_advances=None` to `prepare_teleoperation()` and set `FakeRobot.observation_sequence_advances = list(observation_sequence_advances or [True])`. Change `FakeLeader.action_poses` to `list[dict[str, float] | BaseException]` and replace its read method with:

```python
def get_action(self):
    pose_index = min(self.action_index, len(type(self).action_poses) - 1)
    queued = type(self).action_poses[pose_index]
    self.action_index += 1
    if isinstance(queued, BaseException):
        self.events.append(("leader", "get_action", queued))
        raise queued
    action = dict(queued)
    self.events.append(("leader", "get_action", action))
    return action
```

Add this direct-I/O-free constructor helper:

```python
def make_direct_sync_fakes(
    monkeypatch,
    module,
    *,
    observation_poses,
    action_poses,
    observation_sequence_advances=None,
):
    events = prepare_teleoperation(
        monkeypatch,
        module,
        observation_poses=observation_poses,
        action_poses=action_poses,
        observation_sequence_advances=observation_sequence_advances,
    )
    robot = FakeRobot(SimpleNamespace(remote_ip="127.0.0.1", robot_model="alohamini1"))
    leader = FakeLeader(SimpleNamespace())
    return robot, leader, events
```

- [ ] **Step 2: Add failing exact-confirmation tests**

```python
@pytest.mark.parametrize("response", ["", "sync", " SYNC", "SYNC "])
def test_sync_requires_exact_confirmation_before_any_arm_send(monkeypatch, response):
    module = load_example_module("teleoperate_bi")
    robot, leader, events = make_direct_sync_fakes(
        monkeypatch,
        module,
        observation_poses=[FOLLOWER_POSE],
        action_poses=[LEADER_POSE],
    )
    clock = FakeClock(events)

    def refuse_confirmation(prompt):
        assert "SYNC" in prompt
        assert arm_send_actions(events) == []
        return response

    with pytest.raises(module.SafetyRefusal, match="type exactly SYNC"):
        module.run_startup_sync(
            robot,
            leader,
            side="both",
            requested_duration_s=0.2,
            fps=5,
            max_start_mismatch=10.0,
            input_fn=refuse_confirmation,
            monotonic=clock.monotonic,
            sleep_fn=clock.sleep,
        )

    assert arm_send_actions(events) == []
    assert sum(event[:2] == ("leader", "get_action") for event in events) == 1
```

- [ ] **Step 3: Add a failing frozen-target and ordered-payload test**

```python
def test_sync_uses_post_confirmation_start_and_frozen_target_for_bounded_payloads(monkeypatch, capsys):
    module = load_example_module("teleoperate_bi")
    initial_follower = {**FOLLOWER_POSE, "arm_left_shoulder_pan.pos": -5.0}
    stale_after_confirmation = {**FOLLOWER_POSE, "arm_left_shoulder_pan.pos": -4.0}
    measured_start = {**FOLLOWER_POSE, "arm_left_shoulder_pan.pos": 0.0}
    initial_leader = {**LEADER_POSE, "left_shoulder_pan.pos": -5.0}
    frozen_leader = {**LEADER_POSE, "left_shoulder_pan.pos": 1.5}
    final_follower = {f"arm_{key}": value for key, value in frozen_leader.items()}
    robot, leader, events = make_direct_sync_fakes(
        monkeypatch,
        module,
        observation_poses=[initial_follower, stale_after_confirmation, measured_start, final_follower],
        action_poses=[
            initial_leader,
            frozen_leader,
            frozen_leader,
            {**frozen_leader, "left_shoulder_pan.pos": 2.0},
            {**frozen_leader, "left_shoulder_pan.pos": 2.5},
        ],
        observation_sequence_advances=[True, False, True, True],
    )
    clock = FakeClock(events)

    def confirm(prompt):
        assert arm_send_actions(events) == []
        events.append(("operator", "confirmation", "SYNC"))
        return "SYNC"

    frozen_target, final_observation = module.run_startup_sync(
        robot,
        leader,
        side="both",
        requested_duration_s=0.2,
        fps=5,
        max_start_mismatch=10.0,
        input_fn=confirm,
        monotonic=clock.monotonic,
        sleep_fn=clock.sleep,
    )

    sends = arm_send_actions(events)
    output = capsys.readouterr().out
    assert len(sends) == 3
    assert "Preliminary AM1 startup synchronization plan" in output
    assert "Final frozen-target AM1 startup synchronization plan" in output
    assert "Selected side: both" in output
    assert "Requested minimum duration: 0.200s" in output
    assert "Planned frames: 3" in output
    assert "not collision-aware" in output
    assert "motor disconnect" in output
    assert sends[0]["arm_left_shoulder_pan.pos"] == 0.0
    assert sends[-1]["arm_left_shoulder_pan.pos"] == 1.5
    assert frozen_target["arm_left_shoulder_pan.pos"] == 1.5
    assert final_observation == final_follower
    for previous, current in zip(sends, sends[1:]):
        for key in module.AM1_ARM_POSITION_KEYS:
            assert abs(current[key] - previous[key]) <= module.STARTUP_SYNC_MAX_STEP
    for action in sends:
        assert {key: action[key] for key in module.make_zero_action()} == module.make_zero_action()
    for index, event in enumerate(events):
        if event[:2] == ("robot", "send") and any(key.startswith("arm_") for key in event[2]):
            assert events[index - 1][:2] == ("leader", "get_action")
    assert clock.sleeps == pytest.approx([0.2, 0.2])
```

- [ ] **Step 4: Run the confirmation/frozen-target tests and observe RED**

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests\robots\test_alohamini_windows_leader_client.py `
  -k "sync_requires_exact_confirmation or sync_uses_post_confirmation_start" -q
```

Expected RED: `run_startup_sync` and its printing/verification collaborators do not exist. Confirm no failure comes from fake setup or dynamic imports.

- [ ] **Step 5: Implement initial sampling, exact authorization, and frozen endpoint capture**

Implement the two output helpers explicitly:

```python
def _print_startup_sync_plan(plan: StartupSyncPlan, *, label: str) -> None:
    print(f"{label} AM1 startup synchronization plan:")
    print(f"  Selected side: {plan.side}")
    print(f"  Requested minimum duration: {plan.requested_duration_s:.3f}s")
    print(f"  Largest selected-joint displacement: {plan.max_abs_delta:.3f}")
    print(f"  Planned intervals: {plan.total_steps}")
    print(f"  Planned frames: {plan.frame_count}")
    print(f"  Largest planned per-frame change: {plan.largest_planned_per_frame_change:.3f}")
    print(f"  Estimated actual duration: {plan.estimated_actual_duration_s:.3f}s")


def _print_startup_sync_safety_instructions() -> None:
    print("Startup synchronization is not collision-aware joint-space interpolation.")
    print("Use empty grippers and clear the complete follower arm envelope.")
    print("Place both leaders in moderate poses and hold them still until verification completes.")
    print("Keep people, objects, the other arm, and the chassis clear of any uncertain path.")
    print("Keep the follower motor disconnect immediately accessible.")
```

Start `run_startup_sync()` with this exact order:

```python
initial_observation = get_fresh_follower_observation(robot)
initial_follower = extract_am1_arm_positions(
    initial_observation, source="follower", leader_sample=False
)
initial_leader = extract_am1_arm_positions(
    leader.get_action(), source="leader", leader_sample=True
)
preliminary_plan = build_startup_sync_plan(
    initial_follower,
    initial_leader,
    side=side,
    requested_duration_s=requested_duration_s,
    fps=fps,
)
_print_alignment_table(build_alignment_rows(initial_follower, initial_leader))
_print_startup_sync_plan(preliminary_plan, label="Preliminary")
_print_startup_sync_safety_instructions()
if input_fn("Type exactly SYNC and press Enter to begin follower motion: ") != "SYNC":
    raise SafetyRefusal("startup synchronization requires the operator to type exactly SYNC")

start_observation = get_fresh_follower_observation(robot)
follower_start = extract_am1_arm_positions(
    start_observation, source="follower", leader_sample=False
)
frozen_target = extract_am1_arm_positions(
    leader.get_action(), source="leader", leader_sample=True
)
plan = build_startup_sync_plan(
    follower_start,
    frozen_target,
    side=side,
    requested_duration_s=requested_duration_s,
    fps=fps,
)
_print_startup_sync_plan(plan, label="Final frozen-target")
```

Do not call `.strip()`, `.lower()`, or reprompt. The second follower and leader samples, not the preliminary ones, define the motion plan.

- [ ] **Step 6: Implement the non-skipping timed frame loop and initial final-verification path**

For this first GREEN cycle, send frame zero only after a fresh complete leader read. Record `frame_zero_at = monotonic()` immediately after that send. For every later index, wait until its deadline before reading/validating the complete leader schema and sending. Drift comparison is deliberately added only after its focused RED test in Steps 8-10:

```python
extract_am1_arm_positions(
    leader.get_action(), source="leader", leader_sample=True
)
robot.send_action(build_startup_sync_action(plan, 0))
frame_zero_at = monotonic()

for frame_index in range(1, plan.frame_count):
    deadline = frame_zero_at + frame_index / plan.fps
    sleep_fn(max(deadline - monotonic(), 0.0))
    extract_am1_arm_positions(
        leader.get_action(), source="leader", leader_sample=True
    )
    robot.send_action(build_startup_sync_action(plan, frame_index))
```

Then add the minimal matching-result path. The selected mismatch refusal is added only after its focused RED test in Steps 12-14:

```python
final_observation = get_fresh_follower_observation(robot)
final_follower = extract_am1_arm_positions(
    final_observation, source="follower", leader_sample=False
)
validated_frozen_target = extract_am1_arm_positions(
    dict(plan.frozen_leader_target), source="frozen leader target", leader_sample=True
)
_print_alignment_table(build_alignment_rows(final_follower, validated_frozen_target))
return dict(plan.frozen_leader_target), final_observation
```

Never skip a planned frame, read a live leader during final verification, or replace the frozen target with a drift sample.

- [ ] **Step 7: Run the confirmation/frozen-target tests to verify GREEN**

Run the Step 4 command unchanged.

Expected GREEN: invalid confirmations send no arm action; a valid confirmation causes a second follower/leader read; three ordered frames use the post-confirmation start, stay bounded, retain zero body/lift, and end at the immutable frozen target despite later leader movement within the limit.

- [ ] **Step 8a: Add the failing complete-validation parametrization**

Add a parametrized invalid-sample test using right-side keys while synchronizing only the left side, proving complete-schema validation on the unselected side:

```python
@pytest.mark.parametrize(
    ("bad_pose", "reason"),
    [
        ({key: value for key, value in LEADER_POSE.items() if key != "right_wrist_roll.pos"}, "missing"),
        ({**LEADER_POSE, "right_wrist_yaw.pos": 0.0}, "unexpected"),
        ({**LEADER_POSE, "right_elbow_flex.pos": "bad"}, "must be numeric"),
        ({**LEADER_POSE, "right_shoulder_lift.pos": math.inf}, "must be finite"),
        ({**LEADER_POSE, "right_wrist_flex.pos": 100.1}, "outside expected -100..100"),
    ],
)
def test_sync_invalid_unselected_leader_sample_aborts_before_affected_frame(monkeypatch, bad_pose, reason):
    module = load_example_module("teleoperate_bi")
    robot, leader, events = make_direct_sync_fakes(
        monkeypatch,
        module,
        observation_poses=[FOLLOWER_POSE, FOLLOWER_POSE],
        action_poses=[LEADER_POSE, LEADER_POSE, bad_pose],
    )
    clock = FakeClock(events)

    with pytest.raises(module.SafetyRefusal, match=reason):
        module.run_startup_sync(
            robot, leader,
            side="left", requested_duration_s=0.2, fps=5, max_start_mismatch=10.0,
            input_fn=lambda _: "SYNC", monotonic=clock.monotonic, sleep_fn=clock.sleep,
        )

    assert arm_send_actions(events) == []
```

- [ ] **Step 8b: Add the failing selected-drift boundary tests**

```python
def test_sync_selected_leader_drift_aborts_before_affected_frame(monkeypatch):
    module = load_example_module("teleoperate_bi")
    drifted = {**LEADER_POSE, "left_shoulder_pan.pos": 2.000001}
    robot, leader, events = make_direct_sync_fakes(
        monkeypatch,
        module,
        observation_poses=[FOLLOWER_POSE, FOLLOWER_POSE],
        action_poses=[LEADER_POSE, LEADER_POSE, LEADER_POSE, drifted],
    )
    clock = FakeClock(events)

    with pytest.raises(
        module.SafetyRefusal,
        match=r"left shoulder_pan.*frozen=0\.0.*current=2\.000001.*drift=2\.000001.*2\.0",
    ):
        module.run_startup_sync(
            robot, leader,
            side="both", requested_duration_s=0.2, fps=5, max_start_mismatch=10.0,
            input_fn=lambda _: "SYNC", monotonic=clock.monotonic, sleep_fn=clock.sleep,
        )

    assert len(arm_send_actions(events)) == 1


def test_sync_selected_leader_drift_equal_to_limit_is_allowed(monkeypatch):
    module = load_example_module("teleoperate_bi")
    boundary = {**LEADER_POSE, "left_shoulder_pan.pos": 2.0}
    robot, leader, events = make_direct_sync_fakes(
        monkeypatch,
        module,
        observation_poses=[FOLLOWER_POSE, FOLLOWER_POSE, FOLLOWER_POSE],
        action_poses=[LEADER_POSE, LEADER_POSE, LEADER_POSE, boundary],
    )
    clock = FakeClock(events)

    module.run_startup_sync(
        robot, leader,
        side="both", requested_duration_s=0.2, fps=5, max_start_mismatch=10.0,
        input_fn=lambda _: "SYNC", monotonic=clock.monotonic, sleep_fn=clock.sleep,
    )

    assert len(arm_send_actions(events)) == 2
```

- [ ] **Step 9: Run the new validation/drift tests and observe RED**

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests\robots\test_alohamini_windows_leader_client.py `
  -k "sync_invalid_unselected or sync_selected_leader_drift" -q
```

Expected RED: the malformed unselected samples already refuse because complete extraction preceded every frame, while the drift tests fail because `validate_startup_sync_leader_drift()` does not yet exist or is not called.

- [ ] **Step 10: Implement selected-only drift refusal with complete pre-validation**

`run_startup_sync()` must retain each fresh result from `extract_am1_arm_positions(..., leader_sample=True)`, call this helper immediately afterward, and only then build/send that frame:

```python
def validate_startup_sync_leader_drift(current_leader, frozen_leader_target, selected_keys):
    for key in selected_keys:
        side, joint = _joint_identity(key)
        signed_difference = current_leader[key] - frozen_leader_target[key]
        drift = abs(signed_difference)
        if drift > STARTUP_SYNC_LEADER_DRIFT:
            raise SafetyRefusal(
                f"startup sync leader drift for {side} {joint}: "
                f"frozen={frozen_leader_target[key]}, current={current_leader[key]}, "
                f"signed_difference={signed_difference}, drift={drift} exceeds "
                f"STARTUP_SYNC_LEADER_DRIFT {STARTUP_SYNC_LEADER_DRIFT}"
            )
```

Insert the helper call at both frame-zero and loop read sites shown in Step 6. Do not compare unselected joints for drift, but never bypass their key, numeric, finite, or range validation.

- [ ] **Step 11: Run the validation/drift tests to verify GREEN**

Run the Step 9 command unchanged.

Expected GREEN: every malformed complete sample refuses before the affected send; drift `2.000001` identifies the exact side/joint and leaves only frame zero sent; drift `2.0` passes.

- [ ] **Step 12: Add failing final selected-side verification tests**

```python
def test_sync_final_selected_mismatch_refuses_after_printing_full_table(monkeypatch, capsys):
    module = load_example_module("teleoperate_bi")
    mismatched = {**FOLLOWER_POSE, "arm_left_shoulder_pan.pos": 10.1}
    robot, leader, events = make_direct_sync_fakes(
        monkeypatch,
        module,
        observation_poses=[FOLLOWER_POSE, FOLLOWER_POSE, mismatched],
        action_poses=[LEADER_POSE, LEADER_POSE, LEADER_POSE, LEADER_POSE],
    )
    clock = FakeClock(events)

    with pytest.raises(module.SafetyRefusal, match=r"arm_left_shoulder_pan\.pos.*10\.1.*10\.0"):
        module.run_startup_sync(
            robot, leader,
            side="both", requested_duration_s=0.2, fps=5, max_start_mismatch=10.0,
            input_fn=lambda _: "SYNC", monotonic=clock.monotonic, sleep_fn=clock.sleep,
        )

    output = capsys.readouterr().out
    assert "follower value" in output
    assert "signed difference" in output
    assert len(arm_send_actions(events)) == 2


@pytest.mark.parametrize(
    ("side", "unselected_follower_key", "unselected_leader_key", "forbidden_prefix"),
    [
        ("left", "arm_right_shoulder_pan.pos", "right_shoulder_pan.pos", "arm_right_"),
        ("right", "arm_left_shoulder_pan.pos", "left_shoulder_pan.pos", "arm_left_"),
    ],
)
def test_sync_one_side_ignores_unselected_drift_and_final_mismatch_but_prints_it(
    monkeypatch,
    capsys,
    side,
    unselected_follower_key,
    unselected_leader_key,
    forbidden_prefix,
):
    module = load_example_module("teleoperate_bi")
    unselected_mismatch = {**FOLLOWER_POSE, unselected_follower_key: 20.1}
    unselected_drift = {**LEADER_POSE, unselected_leader_key: 3.0}
    robot, leader, events = make_direct_sync_fakes(
        monkeypatch,
        module,
        observation_poses=[FOLLOWER_POSE, FOLLOWER_POSE, unselected_mismatch],
        action_poses=[LEADER_POSE, LEADER_POSE, LEADER_POSE, unselected_drift],
    )
    clock = FakeClock(events)

    frozen_target, _ = module.run_startup_sync(
        robot, leader,
        side=side, requested_duration_s=0.2, fps=5, max_start_mismatch=10.0,
        input_fn=lambda _: "SYNC", monotonic=clock.monotonic, sleep_fn=clock.sleep,
    )

    assert frozen_target == FOLLOWER_POSE
    assert unselected_follower_key in capsys.readouterr().out
    assert all(
        not any(key.startswith(forbidden_prefix) for key in action)
        for action in arm_send_actions(events)
    )
```

- [ ] **Step 13: Run the final-verification tests and observe RED**

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests\robots\test_alohamini_windows_leader_client.py `
  -k "sync_final_selected_mismatch or sync_one_side_ignores" -q
```

Expected RED: the selected mismatch is not yet rejected with the required table/details, or unselected mismatch/drift is incorrectly treated as selected.

- [ ] **Step 14: Implement full-table, selected-only final verification**

```python
def verify_startup_sync_result(
    follower_positions,
    frozen_leader_target,
    *,
    selected_keys,
    max_start_mismatch,
):
    rows = build_alignment_rows(dict(follower_positions), dict(frozen_leader_target))
    _print_alignment_table(rows)
    selected = set(selected_keys)
    mismatches = [
        row for row in rows
        if row.joint in selected and row.absolute_difference > max_start_mismatch
    ]
    if mismatches:
        worst = max(mismatches, key=lambda row: row.absolute_difference)
        raise SafetyRefusal(
            f"startup sync verification mismatch for {worst.joint}: "
            f"follower={worst.follower_value}, frozen={worst.leader_value}, "
            f"signed_difference={worst.signed_difference}, "
            f"absolute_difference={worst.absolute_difference} exceeds "
            f"--max_start_mismatch {max_start_mismatch}"
        )
    return rows
```

Immediately before calling it, re-run the frozen mapping through `extract_am1_arm_positions(dict(plan.frozen_leader_target), source="frozen leader target", leader_sample=True)` and use a newly sequence-proven, exact-key, finite follower observation. Do not read a live leader during final verification.

- [ ] **Step 15: Run all Task 3 tests to verify GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests\robots\test_alohamini_windows_leader_client.py `
  -k "startup_sync_plan or startup_sync_action or sync_requires_exact or sync_uses_post_confirmation or sync_invalid_unselected or sync_selected_leader_drift or sync_final_selected_mismatch or sync_one_side_ignores" -q
```

Expected: all Task 2/3 tests pass with ordered reads/sends, exact endpoints, complete validation, selected-only drift/final gates, and no hardware construction.

- [ ] **Step 15b: Run focused strict-helper regressions**

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests\robots\test_alohamini_windows_leader_client.py `
  -k "am1_validation or fresh_follower or alignment_rows or large_initial_mismatch or initial_gate_forwards or start_paused_rechecks" -q
```

Expected: every selected Packet 18C-R2 validation, freshness, strict alignment, and pending-first-action test passes unchanged.

- [ ] **Step 16: Review and commit Task 3**

```powershell
git diff --check
git diff -- examples/alohamini/teleoperate_bi.py tests/robots/test_alohamini_windows_leader_client.py
git add -- examples/alohamini/teleoperate_bi.py tests/robots/test_alohamini_windows_leader_client.py
git commit -m "feat(alohamini): add bounded startup synchronization"
```

Confirm no ordinary lifecycle branch, Pi-host path, reverse motion, or unselected arm key was added.

---

### Task 4: Runtime integration and teleoperation handoff

**Files:**
- Modify: `examples/alohamini/teleoperate_bi.py:343-513`
- Test: `tests/robots/test_alohamini_windows_leader_client.py:504-999` and new runtime integration tests

**Interfaces:**
- Consumes: Task 1 CLI namespace fields; Task 3 `run_startup_sync()` return `(verified_frozen_target, final_observation)`; existing `run_alignment_gate()`, `pending_arm_action`, `pending_observation`, `_print_connection_summary()`, `make_zero_action()`, and `_attempt_cleanup()` lifecycle.
- Produces: a strict branch that is byte-for-behavior equivalent to the current path, a sync branch with sync-only early success, both-side pending-action handoff, post-sync pause recheck, ordinary-duration timing after all startup work, and unchanged primary-error cleanup semantics.

- [ ] **Step 1: Add the `sync_args()` helper and failing strict-isolation/sync-only tests**

```python
def sync_args(module, *extra_args):
    return teleoperation_args(
        module,
        "--startup_mode", "sync",
        "--startup_sync_duration_s", "0.2",
        "--fps", "5",
        *extra_args,
    )


@pytest.mark.parametrize("robot_model", ["alohamini1", "alohamini2", "alohamini2pro"])
def test_strict_mode_never_calls_startup_sync(monkeypatch, robot_model):
    module = load_example_module("teleoperate_bi")
    events = prepare_teleoperation(monkeypatch, module)
    monkeypatch.setattr(
        module,
        "run_startup_sync",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("strict entered sync")),
    )
    args = teleoperation_args(
        module,
        "--startup_mode", "strict",
        "--robot_model", robot_model,
        "--duration_s", "0.2",
        "--fps", "5",
    )
    clock = FakeClock(events)

    assert module.run_teleoperation(args, monotonic=clock.monotonic, sleep_fn=clock.sleep) == 0
    assert arm_send_actions(events)


def test_startup_sync_only_skips_optional_resources_and_control_loop(monkeypatch):
    module = load_example_module("teleoperate_bi")
    events = prepare_teleoperation(
        monkeypatch,
        module,
        observation_poses=[FOLLOWER_POSE, FOLLOWER_POSE, FOLLOWER_POSE],
        action_poses=[LEADER_POSE, LEADER_POSE, LEADER_POSE, LEADER_POSE],
    )
    args = module.parse_args(
        [
            "--teleop.left_port", "COM5",
            "--teleop.right_port", "COM6",
            "--startup_mode", "sync",
            "--startup_sync_duration_s", "0.2",
            "--startup_sync_only",
            "--duration_s", "30",
            "--start_paused",
            "--fps", "5",
        ],
        platform_name="Windows",
    )
    clock = FakeClock(events)

    status = module.run_teleoperation(
        args,
        input_fn=lambda _: "SYNC",
        monotonic=clock.monotonic,
        sleep_fn=clock.sleep,
    )

    assert status == 0
    assert len(arm_send_actions(events)) == 2
    assert sum(event[:2] == ("leader", "get_action") for event in events) == 4
    assert ("left", "disconnect") in events
    assert ("right", "disconnect") in events
    assert ("robot", "disconnect") in events
```

- [ ] **Step 2: Run strict-isolation/sync-only tests and observe RED**

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests\robots\test_alohamini_windows_leader_client.py `
  -k "strict_mode_never_calls_startup_sync or startup_sync_only_skips" -q
```

Expected RED: sync mode still traverses the strict `run_alignment_gate()` and ordinary resource/loop path, so sync-only counts or early-exit assertions fail. The strict-isolation assertion must already pass or expose an accidental Task 3 call site.

- [ ] **Step 3: Add the explicit strict/sync startup branch before optional resources**

Replace only the AM1 startup-gate block with explicit mode dispatch:

```python
if args.robot_model == "alohamini1" and robot_connected and right_leader_connected:
    if args.startup_mode == "strict":
        try:
            pending_arm_action, pending_observation = run_alignment_gate(
                robot, leader, args.max_start_mismatch
            )
            if args.check_alignment_only:
                print("Alignment check passed; no arm action was sent.")
                return 0
        except SafetyRefusal as exc:
            print(f"SAFETY REFUSAL: {exc}")
            return 2
    else:
        pending_arm_action, pending_observation = run_startup_sync(
            robot,
            leader,
            side=args.startup_sync_side,
            requested_duration_s=args.startup_sync_duration_s,
            fps=args.fps,
            max_start_mismatch=args.max_start_mismatch,
            input_fn=input_fn,
            monotonic=monotonic,
            sleep_fn=sleep_fn,
        )
        print("Synchronization complete")
        if args.startup_sync_only:
            return 0
        if args.start_paused:
            raise NotImplementedError("post-sync paused handoff is added in the next TDD slice")
        raise NotImplementedError("post-sync direct handoff is added in the next TDD slice")
```

This branch must execute before `KeyboardTeleop` construction and `load_rerun_functions()`. The two `NotImplementedError` guards are temporary, uncommitted TDD guards: they make normal handoff unavailable until its focused tests are written. Do not alter the non-AM1 path.

- [ ] **Step 4: Run strict-isolation/sync-only tests to verify GREEN**

Run the Step 2 command unchanged.

Expected GREEN: strict never enters `run_startup_sync`; sync-only performs exactly two zero-body sync frames, performs no later leader read, constructs no optional resource, cleans up, and returns `0`.

- [ ] **Step 5a: Add the failing direct-handoff continuity test**

```python
def test_sync_handoff_reuses_frozen_target_without_extra_leader_read(monkeypatch):
    module = load_example_module("teleoperate_bi")
    frozen = {**LEADER_POSE, "left_shoulder_pan.pos": 0.5}
    final_follower = {f"arm_{key}": value for key, value in frozen.items()}
    events = prepare_teleoperation(
        monkeypatch,
        module,
        observation_poses=[FOLLOWER_POSE, FOLLOWER_POSE, final_follower],
        action_poses=[LEADER_POSE, frozen, frozen, frozen, {**frozen, "left_shoulder_pan.pos": 1.0}],
    )
    args = sync_args(module, "--duration_s", "0.2")
    clock = FakeClock(events)

    status = module.run_teleoperation(
        args, input_fn=lambda _: "SYNC", monotonic=clock.monotonic, sleep_fn=clock.sleep
    )

    plan = module.build_startup_sync_plan(
        FOLLOWER_POSE,
        final_follower,
        side="both",
        requested_duration_s=0.2,
        fps=5,
    )
    arm_events = [
        (index, event) for index, event in enumerate(events)
        if event[:2] == ("robot", "send") and any(key.startswith("arm_") for key in event[2])
    ]
    final_sync_index, _ = arm_events[plan.frame_count - 1]
    first_ordinary_index, first_ordinary_event = arm_events[plan.frame_count]

    assert status == 0
    assert first_ordinary_event[2] == {**final_follower, **module.make_zero_action()}
    assert all(
        event[:2] != ("leader", "get_action")
        for event in events[final_sync_index + 1:first_ordinary_index]
    )


```

- [ ] **Step 5b: Add the failing keyboard-zero first-action test**

```python
def test_sync_handoff_first_action_forces_zero_body_with_keyboard(monkeypatch):
    module = load_example_module("teleoperate_bi")
    events = prepare_teleoperation(
        monkeypatch,
        module,
        observation_poses=[FOLLOWER_POSE, FOLLOWER_POSE, FOLLOWER_POSE],
        action_poses=[LEADER_POSE, LEADER_POSE, LEADER_POSE, LEADER_POSE],
    )

    class ActiveKeyboard:
        is_connected = False

        def __init__(self, config):
            self.config = config

        def connect(self):
            self.is_connected = True
            events.append(("keyboard", "connect"))

        def get_action(self):
            return {"forward", "lift_up"}

        def disconnect(self):
            self.is_connected = False
            events.append(("keyboard", "disconnect"))

    monkeypatch.setattr(module, "KeyboardTeleop", ActiveKeyboard)
    monkeypatch.setattr(FakeRobot, "_from_keyboard_to_lift_action", lambda self, keys: {"lift_axis.vel": 1})
    args = module.parse_args(
        [
            "--teleop.left_port", "COM5", "--teleop.right_port", "COM6", "--no_rerun",
            "--startup_mode", "sync", "--startup_sync_duration_s", "0.2", "--fps", "5",
            "--duration_s", "0.2",
        ],
        platform_name="Windows",
    )
    clock = FakeClock(events)

    module.run_teleoperation(
        args, input_fn=lambda _: "SYNC", monotonic=clock.monotonic, sleep_fn=clock.sleep
    )

    first_ordinary = arm_send_actions(events)[2]
    assert {key: first_ordinary[key] for key in module.make_zero_action()} == module.make_zero_action()
```

- [ ] **Step 6: Run direct-handoff tests and observe RED**

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests\robots\test_alohamini_windows_leader_client.py `
  -k "sync_handoff_reuses or sync_handoff_first_action" -q
```

Expected RED: the first ordinary action is missing, is replaced by an unchecked live sample, or includes live keyboard body/lift motion.

- [ ] **Step 7: Preserve the sync result as the pending first ordinary action**

Keep Task 3's returned frozen dictionary in `pending_arm_action`; do not call `leader.get_action()` between final verification and the existing pending-action branch. Remove the temporary direct-handoff `NotImplementedError`, but retain the post-sync paused-handoff guard for the next RED cycle. Leave the current `forwarding_pending_sample` logic intact so `body_action = make_zero_action()` overrides keyboard input on that first ordinary action. Only later loop iterations may read live leader and keyboard samples.

- [ ] **Step 8: Run direct-handoff tests to verify GREEN**

Run the Step 6 command unchanged.

Expected GREEN: the first post-sync ordinary send repeats the frozen target with explicit zero base/lift, and no leader read occurs between final sync approval and that send.

- [ ] **Step 9a: Add the failing successful post-sync pause test**

```python
def test_post_sync_start_paused_rechecks_and_forwards_final_validated_sample(monkeypatch, capsys):
    module = load_example_module("teleoperate_bi")
    post_pause_leader = {**LEADER_POSE, "left_wrist_roll.pos": 6.0}
    post_pause_follower = {f"arm_{key}": value for key, value in post_pause_leader.items()}
    events = prepare_teleoperation(
        monkeypatch,
        module,
        observation_poses=[FOLLOWER_POSE, FOLLOWER_POSE, FOLLOWER_POSE, post_pause_follower],
        action_poses=[
            LEADER_POSE, LEADER_POSE, LEADER_POSE, LEADER_POSE,
            post_pause_leader,
            {**post_pause_leader, "left_wrist_roll.pos": 7.0},
        ],
    )
    args = sync_args(module, "--start_paused", "--duration_s", "0.2")
    clock = FakeClock(events)
    responses = iter(("SYNC", ""))

    status = module.run_teleoperation(
        args, input_fn=lambda _: next(responses), monotonic=clock.monotonic, sleep_fn=clock.sleep
    )

    first_ordinary = arm_send_actions(events)[2]
    assert status == 0
    assert "Action space: body joints -100..100; grippers 0..100" in capsys.readouterr().out
    assert first_ordinary == {**post_pause_follower, **module.make_zero_action()}
    first_ordinary_index = next(
        index for index, event in enumerate(events)
        if event[:2] == ("robot", "send") and event[2] == first_ordinary
    )
    assert sum(event[:2] == ("leader", "get_action") for event in events[:first_ordinary_index]) == 5


```

- [ ] **Step 9b: Add the failing post-sync moved-sample refusal test**

```python
def test_post_sync_start_paused_refuses_moved_sample_before_ordinary_send(monkeypatch, capsys):
    module = load_example_module("teleoperate_bi")
    moved = {**LEADER_POSE, "right_wrist_flex.pos": 20.1}
    events = prepare_teleoperation(
        monkeypatch,
        module,
        observation_poses=[FOLLOWER_POSE, FOLLOWER_POSE, FOLLOWER_POSE, FOLLOWER_POSE],
        action_poses=[LEADER_POSE, LEADER_POSE, LEADER_POSE, LEADER_POSE, moved],
    )
    args = sync_args(module, "--start_paused", "--duration_s", "0.2")
    clock = FakeClock(events)
    responses = iter(("SYNC", ""))

    status = module.run_teleoperation(
        args, input_fn=lambda _: next(responses), monotonic=clock.monotonic, sleep_fn=clock.sleep
    )

    assert status == 2
    assert "arm_right_wrist_flex.pos" in capsys.readouterr().out
    assert len(arm_send_actions(events)) == 2


```

- [ ] **Step 9c: Add the failing ordinary-duration origin test**

```python
def test_sync_duration_clock_starts_after_sync_and_post_sync_pause(monkeypatch):
    module = load_example_module("teleoperate_bi")
    events = prepare_teleoperation(
        monkeypatch,
        module,
        observation_poses=[FOLLOWER_POSE, FOLLOWER_POSE, FOLLOWER_POSE, FOLLOWER_POSE],
        action_poses=[LEADER_POSE, LEADER_POSE, LEADER_POSE, LEADER_POSE, LEADER_POSE],
    )
    args = sync_args(module, "--start_paused", "--duration_s", "0.2")
    clock = FakeClock(events)
    responses = iter(("SYNC", ""))

    def confirm_then_pause(prompt):
        response = next(responses)
        if response == "":
            clock.advance(100.0)
        return response

    status = module.run_teleoperation(
        args, input_fn=confirm_then_pause, monotonic=clock.monotonic, sleep_fn=clock.sleep
    )

    assert status == 0
    assert len(arm_send_actions(events)) >= 3
```

- [ ] **Step 10: Run post-sync pause/duration tests and observe RED**

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests\robots\test_alohamini_windows_leader_client.py `
  -k "post_sync_start_paused or sync_duration_clock" -q
```

Expected RED: the temporary post-sync paused-handoff guard raises `NotImplementedError`, proving this slice is not accidentally using the pre-sync pause path.

- [ ] **Step 11: Split strict and sync pause behavior while reusing `run_alignment_gate()`**

Keep optional keyboard/Rerun construction after successful sync verification. In the existing `if args.start_paused:` block:

- remove the temporary post-sync paused-handoff `NotImplementedError` guard;
- send the existing zero-only action;
- do not print a second `Synchronization complete`; the startup branch already printed it immediately after successful final verification;
- wait for Enter;
- call unchanged `run_alignment_gate(robot, leader, args.max_start_mismatch)` for AM1, replacing both pending values with its fresh result;
- catch `SafetyRefusal`, print `SAFETY REFUSAL: ...`, and return `2`;
- do not call `run_startup_sync()` again;
- leave `started_at = monotonic()` after this block so sync and both waits are excluded from `duration_s`.

The pending-action branch already prevents an unchecked read and forces zero base/lift. Preserve it.

- [ ] **Step 12: Run post-sync pause/duration tests to verify GREEN**

Run the Step 10 command unchanged.

Expected GREEN: the fresh post-Enter follower/leader pair controls approval and first ordinary payload, moved samples return `2`, no extra leader read intervenes, and a 100-second startup wait does not consume a `0.2`-second ordinary duration.

- [ ] **Step 13a: Add the failing drift-refusal lifecycle test**

```python
def test_sync_drift_refusal_returns_two_without_reverse_and_cleans_up(monkeypatch, capsys):
    module = load_example_module("teleoperate_bi")
    drifted = {**LEADER_POSE, "left_shoulder_pan.pos": 2.000001}
    events = prepare_teleoperation(
        monkeypatch,
        module,
        observation_poses=[FOLLOWER_POSE, FOLLOWER_POSE],
        action_poses=[LEADER_POSE, LEADER_POSE, LEADER_POSE, drifted],
    )
    args = sync_args(module, "--duration_s", "0.2")
    clock = FakeClock(events)

    status = module.run_teleoperation(
        args, input_fn=lambda _: "SYNC", monotonic=clock.monotonic, sleep_fn=clock.sleep
    )

    captured = capsys.readouterr()
    assert status == 2
    assert "SAFETY REFUSAL" in captured.out
    assert "left shoulder_pan" in captured.out
    assert "frozen=0.0" in captured.out
    assert "current=2.000001" in captured.out
    assert "drift=2.000001" in captured.out
    assert "Traceback" not in captured.err
    assert len(arm_send_actions(events)) == 1
    last_arm_index = max(
        index for index, event in enumerate(events)
        if event[:2] == ("robot", "send") and any(key.startswith("arm_") for key in event[2])
    )
    assert all(
        not any(key.startswith("arm_") for key in event[2])
        for event in events[last_arm_index + 1:]
        if event[:2] == ("robot", "send")
    )
    assert ("left", "disconnect") in events
    assert ("right", "disconnect") in events
    assert ("robot", "disconnect") in events


def test_sync_final_mismatch_returns_two_without_ordinary_send_and_cleans_up(monkeypatch, capsys):
    module = load_example_module("teleoperate_bi")
    mismatched = {**FOLLOWER_POSE, "arm_left_shoulder_pan.pos": 10.1}
    events = prepare_teleoperation(
        monkeypatch,
        module,
        observation_poses=[FOLLOWER_POSE, FOLLOWER_POSE, mismatched],
        action_poses=[LEADER_POSE, LEADER_POSE, LEADER_POSE, LEADER_POSE],
    )
    args = sync_args(module, "--duration_s", "0.2")
    clock = FakeClock(events)

    status = module.run_teleoperation(
        args, input_fn=lambda _: "SYNC", monotonic=clock.monotonic, sleep_fn=clock.sleep
    )

    assert status == 2
    assert "arm_left_shoulder_pan.pos" in capsys.readouterr().out
    assert len(arm_send_actions(events)) == 2
    assert ("left", "disconnect") in events
    assert ("right", "disconnect") in events
    assert ("robot", "disconnect") in events


```

- [ ] **Step 13b: Add unexpected-failure and interrupt cleanup tests**

```python
@pytest.mark.parametrize("failure", [RuntimeError("sync read failed"), KeyboardInterrupt()])
def test_sync_failure_or_interrupt_preserves_primary_and_cleans_up(monkeypatch, failure):
    module = load_example_module("teleoperate_bi")
    events = prepare_teleoperation(
        monkeypatch,
        module,
        left_disconnect_error=RuntimeError("cleanup failed"),
        observation_poses=[FOLLOWER_POSE, FOLLOWER_POSE],
        action_poses=[LEADER_POSE, LEADER_POSE, failure],
    )
    args = sync_args(module)
    clock = FakeClock(events)

    with pytest.raises(type(failure)) as caught:
        module.run_teleoperation(
            args, input_fn=lambda _: "SYNC", monotonic=clock.monotonic, sleep_fn=clock.sleep
        )

    assert caught.value is failure
    assert any(event[:2] == ("robot", "send") and event[2] == module.make_zero_action() for event in events)
    assert ("right", "disconnect") in events
    assert ("left", "disconnect") in events
    assert ("robot", "disconnect") in events


```

- [ ] **Step 13c: Add the post-sync optional-resource failure test**

```python
def test_sync_visualization_failure_after_verification_preserves_primary_and_cleans_up(monkeypatch):
    module = load_example_module("teleoperate_bi")
    events = prepare_teleoperation(
        monkeypatch,
        module,
        observation_poses=[FOLLOWER_POSE, FOLLOWER_POSE, FOLLOWER_POSE],
        action_poses=[LEADER_POSE, LEADER_POSE, LEADER_POSE, LEADER_POSE],
    )
    primary_error = RuntimeError("visualization failed after sync")

    def fail_visualization_start(**kwargs):
        events.append(("rerun", "init"))
        raise primary_error

    monkeypatch.setattr(
        module,
        "load_rerun_functions",
        lambda: (
            fail_visualization_start,
            lambda *args: None,
            lambda: events.append(("rerun", "shutdown")),
        ),
    )
    args = module.parse_args(
        [
            "--teleop.left_port", "COM5",
            "--teleop.right_port", "COM6",
            "--no_keyboard",
            "--startup_mode", "sync",
            "--startup_sync_duration_s", "0.2",
            "--fps", "5",
        ],
        platform_name="Windows",
    )
    clock = FakeClock(events)

    with pytest.raises(RuntimeError) as caught:
        module.run_teleoperation(
            args, input_fn=lambda _: "SYNC", monotonic=clock.monotonic, sleep_fn=clock.sleep
        )

    assert caught.value is primary_error
    assert len(arm_send_actions(events)) == 2
    assert max(
        index for index, event in enumerate(events)
        if event[:2] == ("robot", "send") and any(key.startswith("arm_") for key in event[2])
    ) < events.index(("rerun", "init"))
    assert ("right", "disconnect") in events
    assert ("left", "disconnect") in events
    assert ("robot", "disconnect") in events
    assert ("rerun", "shutdown") in events
```

- [ ] **Step 14: Run cleanup tests and observe RED**

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests\robots\test_alohamini_windows_leader_client.py `
  -k "sync_drift_refusal_returns_two or sync_final_mismatch_returns_two or sync_failure_or_interrupt or sync_visualization_failure_after_verification" -q
```

Expected RED: drift and final-verification `SafetyRefusal` exceptions still propagate because only the strict and post-pause gates convert expected refusals. The unexpected-error, `KeyboardInterrupt`, and post-sync visualization cases should already pass and guard primary-error cleanup.

- [ ] **Step 15: Keep sync failure handling inside the existing outer `try/finally`**

Catch only `SafetyRefusal` around the startup mode dispatch. Do not catch `KeyboardInterrupt` or unexpected exceptions. Do not add reverse arm sends. Let the existing `finally` perform zero-only body/lift, right/left leader disconnect, robot disconnect, and optional-resource cleanup, attaching cleanup notes to the active primary exception exactly as it does now.

- [ ] **Step 16: Run cleanup tests to verify GREEN**

Run the Step 14 command unchanged.

Expected GREEN: drift refusal returns `2` with no arm reverse, unexpected errors and `KeyboardInterrupt` remain the caught object, cleanup still requests zero and disconnects every connected resource, and cleanup errors do not replace the primary failure.

- [ ] **Step 17: Run the complete Windows client regression module**

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests\robots\test_alohamini_windows_leader_client.py -q
```

Expected: all existing and new fake-only Windows client tests pass. Inspect the output for warnings, skips, or collection errors rather than relying only on the exit code.

- [ ] **Step 18: Review model and lifecycle isolation, then commit Task 4**

```powershell
git diff --check
git diff -- examples/alohamini/teleoperate_bi.py tests/robots/test_alohamini_windows_leader_client.py
git add -- examples/alohamini/teleoperate_bi.py tests/robots/test_alohamini_windows_leader_client.py
git commit -m "feat(alohamini): integrate startup sync lifecycle"
```

Confirm `strict` is the only default path, AM2/AM2 Pro do not enter sync, sync-only returns before keyboard/Rerun/main-loop construction, and `started_at` remains after all startup and pause work. Search `teleoperate_bi.py` and require that both temporary `next TDD slice` guards are gone before this commit.

---

### Task 5: Documentation and final validation

**Files:**
- Modify: `docs/alohamini/alohamini.md:58-127`
- Test: `tests/robots/test_alohamini_windows_leader_client.py` (one documentation-contract test)
- Verify only: production/test files changed in Tasks 1-4

**Interfaces:**
- Consumes: the implemented flags and safety behavior from Tasks 1-4, the exact S1-S6 command shapes in the approved design specification, and existing native-Windows environment/calibration guidance.
- Produces: a native-Windows commissioning section with explicit non-collision-aware limitations, exact `SYNC` authorization, step/drift explanations, S1-S6 stop/go commands, and final software-only validation evidence.

The commands in this task are intended future commissioning commands. They do not exist at plan-writing time and must not be run as part of software implementation. Add them to user documentation only after Tasks 1-4 and the documentation-contract test are green; physical execution remains a separately authorized activity.

- [ ] **Step 1: Add a failing documentation-contract test**

```python
def test_windows_startup_sync_commissioning_docs_contain_approved_safety_sequence():
    text = (REPO_ROOT / "docs" / "alohamini" / "alohamini.md").read_text(encoding="utf-8")
    required = (
        "not collision-aware",
        "STARTUP_SYNC_MAX_STEP = 0.75",
        "STARTUP_SYNC_LEADER_DRIFT = 2.0",
        "type exactly `SYNC`",
        "S1",
        "S2",
        "S3",
        "S4",
        "S5",
        "S6",
        "192.168.1.134",
        "COM7",
        "COM8",
        "so101_leader_bi",
        "so-arm-5dof",
        "--startup_sync_only",
        "--start_paused",
        "--no_keyboard",
        "--no_rerun",
    )

    for marker in required:
        assert marker in text
```

- [ ] **Step 2: Run the documentation-contract test and observe RED**

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests\robots\test_alohamini_windows_leader_client.py::test_windows_startup_sync_commissioning_docs_contain_approved_safety_sequence -q
```

Expected RED: the existing strict-only Windows section lacks the sync constants, exact authorization text, COM7/COM8 command set, and S1-S6 markers.

- [ ] **Step 3: Update the native-Windows safety narrative before adding commands**

Preserve environment creation, port discovery, passive-leader voltage warning, `so101_leader_bi` calibration identity, normalized action ranges, and calibration-file reuse. Replace the strict-only startup narrative with text that states all of the following explicitly:

- `strict` remains the default and never automatically positions followers;
- `sync` is Aloha Mini 1-only linear interpolation in normalized joint space and is not collision-aware;
- automatic motion starts only after exact uppercase `SYNC`; Enter alone refuses;
- `STARTUP_SYNC_MAX_STEP = 0.75` is a client frame cap independent of Pi `max_relative_target`;
- `STARTUP_SYNC_LEADER_DRIFT = 2.0` aborts selected-side motion when exceeded;
- every sync frame holds base/lift at zero, while an abort does not reverse the arms and the Pi may hold the last target;
- empty grippers, a clear envelope, moderate held leaders, and an accessible follower motor disconnect are mandatory;
- every stage stops at the first unexpected direction, speed, sound, current, contact, software error, or communication failure;
- S1/S2 require the separately configured Pi `max_relative_target=1.0`, but this client change adds no Pi command;
- physical commissioning is not part of software validation and requires separate authorization.

- [ ] **Step 4a: Add the exact S1 and S2 one-side command shapes**

Insert these commands in order. Do not execute them during implementation.

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

- [ ] **Step 4b: Add the exact S3 and S4 diagnostic/both-side command shapes**

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

- [ ] **Step 4c: Add the exact S5 and S6 teleoperation command shapes**

S5 — only after successful S4, strict bounded teleoperation with operator movement limited to grippers; this is not a gripper-only payload mode and all twelve arm keys remain present:

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

- [ ] **Step 5: Run the documentation-contract test to verify GREEN**

Run the Step 2 command unchanged.

Expected GREEN: every warning, constant, stage marker, machine/port/profile identifier, and required safety flag is present.

- [ ] **Step 6: Run all focused Packet 14A/14B/R2/sync tests**

These are fake-only tests and must not open COM ports, create cameras, or connect ZMQ:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests\robots\test_alohamini_safe_bringup.py `
  tests\robots\test_alohamini_windows_leader_client.py -q
```

Expected: all tests pass with zero failures. Review the collected count and output for unexpected skips or warnings.

- [ ] **Step 7: Compile only changed Python files into a temporary cache**

```powershell
$env:PYTHONPYCACHEPREFIX = Join-Path $env:TEMP 'am1-startup-sync-pycache'
.\.venv\Scripts\python.exe -m py_compile `
  examples\alohamini\teleoperate_bi.py `
  tests\robots\test_alohamini_windows_leader_client.py
```

Expected: exit `0` with no output and no repository `__pycache__` change.

- [ ] **Step 8: Verify CLI help without constructing a device**

```powershell
.\.venv\Scripts\python.exe .\examples\alohamini\teleoperate_bi.py --help
```

Expected: exit `0`; output lists all four sync options, their defaults/choices, and the sync-only pause/duration behavior. Argument help exits before port resolution or object construction.

- [ ] **Step 9: Run fresh-process import isolation checks**

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, 'examples/alohamini'); import calibrate_bi, teleoperate_bi, record_bi; assert 'lerobot.utils.visualization_utils' not in sys.modules; print('fresh imports OK')"
```

Expected: `fresh imports OK`; no argument parsing, visualization helper import, COM open, calibration, camera, or network action occurs.

- [ ] **Step 10: Inspect the exact implementation diff and out-of-scope boundaries**

Resolve the implementation base from this committed plan, then inspect the whole range:

```powershell
$implementationBase = git log -1 --format=%H -- docs/superpowers/plans/2026-08-16-am1-startup-sync-implementation.md
git diff --check "$implementationBase"
git diff --stat "$implementationBase"
git diff --name-only "$implementationBase"
git diff "$implementationBase" -- `
  examples/alohamini/teleoperate_bi.py `
  tests/robots/test_alohamini_windows_leader_client.py `
  docs/alohamini/alohamini.md
git status --short
```

Expected: `git diff --check` prints nothing and exits `0`; name-only output contains exactly the three planned implementation paths. Confirm no Pi host, motor/lift/camera module, calibration file, `leader_client_utils.py`, `record_bi.py`, dependency, configuration, or unrelated model file changed.

- [ ] **Step 11: Commit Task 5**

```powershell
git add -- docs/alohamini/alohamini.md tests/robots/test_alohamini_windows_leader_client.py
git commit -m "docs(alohamini): document startup sync commissioning"
```

- [ ] **Step 12: Repeat the full verification after the commit**

Repeat Steps 6-10 from the committed tree. Require a clean `git status --short`, retain the exact command output for the completion report, and do not begin any S1-S6 physical stage in this software packet.

---

## Design-Section-to-Task Map

| Approved specification section | Implemented or verified by |
| --- | --- |
| Status, purpose, and goals | Global Constraints; Tasks 1-5 collectively. |
| Scope and out-of-scope boundaries | File Responsibility Map; Task 5 exact diff review. |
| Command-Line Contract and Argument Compatibility | Task 1 parser red/green cycle. |
| Aloha Mini 1 Action-Space Contract | Existing Packet 18C-R2 validation plus Tasks 2-4 payload/integration tests. |
| Component Boundaries | Shared Interface Contract; Tasks 2 and 3. |
| Startup State Machine | Task 3 owns `INITIAL_SAMPLE` through `VERIFYING`; Task 4 owns connection, `SYNC_COMPLETE`, optional pause, teleoperation, and cleanup transitions. |
| Initial Sampling and Operator Confirmation | Task 3 Steps 2-7. |
| Frozen Start and Target | Task 2 immutable plan and Task 3 post-confirmation sampling. |
| Plan Arithmetic and Maximum Step | Task 2 Steps 1-7. |
| Interpolation and Frame Construction | Task 2 payload builder and Task 3 ordered frame loop. |
| Leader Drift Monitoring | Task 3 Steps 8-11; Task 4 cleanup/status integration. |
| Final Verification | Task 3 Steps 12-15. |
| Sync-Only Completion | Task 4 Steps 1-4. |
| Transition to Ordinary Teleoperation | Task 4 Steps 5-12. |
| Strict-Mode Preservation | Task 1 defaults, Task 4 explicit branch and full regression. |
| Failure, Status, and Cleanup Semantics | Task 3 `SafetyRefusal` boundaries and Task 4 Steps 13-16. |
| Safety Limitations | Task 3 operator text and Task 5 Windows documentation. |
| Test-Driven Implementation Requirements | Every production task starts RED; the numbered table below maps all 27 requirements. |
| Documentation and Physical Commissioning | Task 5; command execution remains separately authorized. |
| Future Work | Global Constraints keep every deferred behavior outside the implementation diff. |
| Implementation Acceptance Criteria | Task 5 Steps 6-12 and final diff review. |

## Numbered Test-Requirement Coverage

| Spec test requirement | Task | Proposed test or exact validation |
| ---: | --- | --- |
| 1 | 4 | Existing `test_large_initial_mismatch_refuses_before_arm_send_and_cleans_up` plus `test_strict_mode_never_calls_startup_sync`. |
| 2 | 3 | `test_sync_requires_exact_confirmation_before_any_arm_send`. |
| 3 | 3 | Parametrized responses in `test_sync_requires_exact_confirmation_before_any_arm_send`. |
| 4 | 3 | `test_sync_uses_post_confirmation_start_and_frozen_target_for_bounded_payloads`. |
| 5 | 2, 3 | `test_startup_sync_actions_have_exact_endpoints_bounded_steps_and_zero_body` and `test_sync_uses_post_confirmation_start_and_frozen_target_for_bounded_payloads`. |
| 6 | 2, 3 | `test_startup_sync_actions_have_exact_endpoints_bounded_steps_and_zero_body` and `test_sync_uses_post_confirmation_start_and_frozen_target_for_bounded_payloads`. |
| 7 | 2, 3 | `test_startup_sync_actions_have_exact_endpoints_bounded_steps_and_zero_body` and `test_sync_uses_post_confirmation_start_and_frozen_target_for_bounded_payloads`. |
| 8 | 2 | `test_startup_sync_plan_extends_duration_for_step_limit`. |
| 9 | 2 | `test_startup_sync_plan_preserves_duration_for_zero_displacement`. |
| 10 | 2, 3 | Left case of `test_startup_sync_action_omits_unselected_arm_keys`, plus `test_sync_one_side_ignores_unselected_drift_and_final_mismatch_but_prints_it`. |
| 11 | 2 | Right case of `test_startup_sync_action_omits_unselected_arm_keys`. |
| 12 | 1 | Left/right cases in `test_startup_sync_rejects_incompatible_arguments` and allowed sync-only cases. |
| 13 | 2, 3 | `test_startup_sync_actions_have_exact_endpoints_bounded_steps_and_zero_body` and `test_sync_uses_post_confirmation_start_and_frozen_target_for_bounded_payloads`. |
| 14 | 3 | `test_sync_invalid_unselected_leader_sample_aborts_before_affected_frame` covers missing, unexpected, nonnumeric, non-finite, and out-of-range data. |
| 15 | 3 | `test_sync_invalid_unselected_leader_sample_aborts_before_affected_frame` plus `test_sync_one_side_ignores_unselected_drift_and_final_mismatch_but_prints_it`. |
| 16 | 3, 4 | `test_sync_selected_leader_drift_aborts_before_affected_frame` and lifecycle `test_sync_drift_refusal_returns_two_without_reverse_and_cleans_up`. |
| 17 | 3 | `test_sync_selected_leader_drift_equal_to_limit_is_allowed`. |
| 18 | 3, 4 | `test_sync_final_selected_mismatch_refuses_after_printing_full_table`, `test_sync_final_mismatch_returns_two_without_ordinary_send_and_cleans_up`, and `test_post_sync_start_paused_refuses_moved_sample_before_ordinary_send`. |
| 19 | 3 | `test_sync_one_side_ignores_unselected_drift_and_final_mismatch_but_prints_it`. |
| 20 | 4 | `test_startup_sync_only_skips_optional_resources_and_control_loop`. |
| 21 | 4 | `test_sync_handoff_reuses_frozen_target_without_extra_leader_read`. |
| 22 | 4 | `test_sync_handoff_first_action_forces_zero_body_with_keyboard`. |
| 23 | 4 | `test_post_sync_start_paused_rechecks_and_forwards_final_validated_sample` and `test_post_sync_start_paused_refuses_moved_sample_before_ordinary_send`. |
| 24 | 4 | `test_sync_duration_clock_starts_after_sync_and_post_sync_pause`. |
| 25 | 4 | `test_sync_drift_refusal_returns_two_without_reverse_and_cleans_up`, `test_sync_failure_or_interrupt_preserves_primary_and_cleans_up`, `test_sync_visualization_failure_after_verification_preserves_primary_and_cleans_up`, `test_partial_leader_connection_failure_preserves_primary_error`, and `test_visualization_start_failure_cleans_connected_devices_and_preserves_error`. |
| 26 | 4 | KeyboardInterrupt parameter in `test_sync_failure_or_interrupt_preserves_primary_and_cleans_up` plus existing `test_keyboard_interrupt_zeros_and_disconnects_connected_devices`. |
| 27 | 5 | Combined fake-only pytest command for `test_alohamini_safe_bringup.py` and `test_alohamini_windows_leader_client.py`. |

## Implementation Review Gate

Before declaring the later implementation complete, inspect the committed range and answer each item from evidence:

- Every approved specification section points to a task in the map above.
- Every helper and type is explicitly named, defined, and used with a consistent signature.
- `strict` is the parser default and cannot call `run_startup_sync()`.
- `left` and `right` sync payloads omit every unselected arm key and are parser-limited to `--startup_sync_only`.
- `total_steps` means interpolation intervals and every execution sends `total_steps + 1` frames including exact `alpha=0` and `alpha=1` endpoints.
- Every frame has a fresh complete leader read immediately before its send; selected drift alone controls drift refusal.
- Final verification uses the immutable frozen target and a sequence-proven follower observation, not a replacement live leader target.
- Sync-only constructs no keyboard/Rerun resource and enters no ordinary control loop.
- The first ordinary action is a previously validated sample plus explicit zero base/lift, with no intervening unchecked leader read.
- `duration_s` starts after synchronization and any post-sync pause gate.
- Expected refusal, unexpected exception, and `KeyboardInterrupt` paths all pass through existing cleanup without reverse arm motion or primary-error loss.
- The implementation diff contains no Pi-host, calibration, recording, generic leader, AM2/AM2 Pro, camera, dependency, or configuration change.
- Every production change was preceded by the named observed RED test and followed by its exact GREEN command.
- The commissioning guide labels synchronization non-collision-aware and does not imply that plan-time flags or physical stages were available or executed before implementation and software validation completed.

Only after this gate and Task 5's post-commit verification may a separate, explicitly authorized physical commissioning packet begin at S1.
