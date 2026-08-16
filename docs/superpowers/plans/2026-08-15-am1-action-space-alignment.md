# Aloha Mini 1 Action-Space and Alignment Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Emit normalized Aloha Mini leader positions and refuse AM1 arm forwarding until exact-key, finite/range, and startup pose-alignment checks pass.

**Architecture:** Centralize normalized `BiSOLeaderConfig` construction in `leader_client_utils.py`, retaining the three script-level `make_leader_config()` interfaces. Add AM1-specific pure action-validation/alignment helpers to `teleoperate_bi.py`, then integrate them into the existing partial-connection lifecycle so expected safety refusals return status `2` while zero-only cleanup remains available.

**Tech Stack:** Python 3.12, argparse, LeRobot Feetech/SO leader abstractions, pytest fakes/monkeypatch, native Windows PowerShell.

## Global Constraints

- Starting implementation parent: committed design `e1231b1e78e6ada2738612174aa580229331aaed`, whose parent is requested start `645964a9a3573ef8a36676391c64992e0513b06e`.
- Branch: `fix/am1-teleop-action-space`.
- All hardware is disconnected and unpowered. Never open a COM port, contact the Pi, calibrate, teleoperate hardware, change a motor register, or alter a calibration file.
- Do not change generic `SOLeaderConfig.use_degrees`, Aloha Mini follower normalization, motor configuration, Pi activation/lift behavior, cameras, geometry, or ZMQ schema.
- Existing calibration JSON identity, path, raw homing/range/drive data, and reuse behavior remain unchanged.
- Keep `--no_robot`, `--no_leader`, `--no_keyboard`, `--no_rerun`, `--start_paused`, `--duration_s`, explicit Windows COM ports, Linux default aliases, and passive torque-disabled leaders.
- Exact AM1 arm-position safety rules apply only when `--robot.robot_model alohamini1`; do not impose the AM1 twelve-key schema on Aloha Mini 2/2 Pro.
- Run only focused fake tests and the explicitly requested compile/help/import/diff checks.

---

### Task 1: Centralized normalized leader configuration

**Files:**
- Modify: `tests/robots/test_alohamini_windows_leader_client.py`
- Modify: `examples/alohamini/leader_client_utils.py`
- Modify: `examples/alohamini/calibrate_bi.py`
- Modify: `examples/alohamini/teleoperate_bi.py`
- Modify: `examples/alohamini/record_bi.py`

**Interfaces:**
- Consumes: resolved `left_port`, `right_port`, `leader_id`, and `arm_profile` strings.
- Produces: `make_alohamini_leader_config(*, left_port: str, right_port: str, leader_id: str | None, arm_profile: str) -> BiSOLeaderConfig`, with both child configs using `use_degrees=False`.
- Preserves: each script's `make_leader_config(args: argparse.Namespace) -> BiSOLeaderConfig`.

- [ ] **Step 1: Extend the existing cross-script config test first**

In `test_windows_leader_ports_are_passed_to_both_arm_configs_unchanged`, add literal assertions:

```python
assert config.left_arm_config.use_degrees is False
assert config.right_arm_config.use_degrees is False
```

The existing parameterization over `calibrate_bi`, `teleoperate_bi`, and `record_bi` proves all three consumers.

- [ ] **Step 2: Run the test and verify the expected red failure**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests\robots\test_alohamini_windows_leader_client.py::test_windows_leader_ports_are_passed_to_both_arm_configs_unchanged -q
```

Expected: three failures showing `use_degrees` is currently `True`.

- [ ] **Step 3: Add the minimal shared builder**

In `leader_client_utils.py`, import the existing config classes and add:

```python
def make_alohamini_leader_config(
    *,
    left_port: str,
    right_port: str,
    leader_id: str | None,
    arm_profile: str,
) -> BiSOLeaderConfig:
    return BiSOLeaderConfig(
        left_arm_config=SOLeaderConfig(
            port=left_port,
            arm_profile=arm_profile,
            use_degrees=False,
        ),
        right_arm_config=SOLeaderConfig(
            port=right_port,
            arm_profile=arm_profile,
            use_degrees=False,
        ),
        id=leader_id,
    )
```

Change each script wrapper to call that builder with `args.left_port`, `args.right_port`, `args.leader_id`, and `args.arm_profile`. Remove now-unused direct config imports from the scripts.

- [ ] **Step 4: Re-run the cross-script config test**

Expected: all three parameterized cases pass and the COM strings remain unchanged.

- [ ] **Step 5: Run the full Packet 14B file before committing**

Run the entire `test_alohamini_windows_leader_client.py`; expected existing count plus the unchanged tests all pass.

- [ ] **Step 6: Commit the unit-source fix**

```powershell
git add -- examples/alohamini/leader_client_utils.py examples/alohamini/calibrate_bi.py `
  examples/alohamini/teleoperate_bi.py examples/alohamini/record_bi.py `
  tests/robots/test_alohamini_windows_leader_client.py
git commit -m "fix: normalize Aloha Mini leader actions"
```

---

### Task 2: Exact AM1 key and finite/range validation

**Files:**
- Modify: `tests/robots/test_alohamini_windows_leader_client.py`
- Modify: `examples/alohamini/teleoperate_bi.py`

**Interfaces:**
- Produces: `AM1_ARM_POSITION_KEYS: tuple[str, ...]`, `SafetyRefusal(ValueError)`, and `extract_am1_arm_positions(values: dict[str, Any], *, source: str, leader_sample: bool) -> dict[str, float]`.
- Contract: filters only `arm_*.pos` for exact-set comparison; unrelated base/lift/camera keys are allowed; raw bimanual leader keys gain exactly one `arm_` prefix.

- [ ] **Step 1: Replace partial fake fixtures with complete literal AM1 poses**

Add literal helpers in the test file:

```python
LEADER_POSE = {
    "left_shoulder_pan.pos": 0.0,
    "left_shoulder_lift.pos": 10.0,
    "left_elbow_flex.pos": 20.0,
    "left_wrist_flex.pos": 30.0,
    "left_wrist_roll.pos": 40.0,
    "left_gripper.pos": 50.0,
    "right_shoulder_pan.pos": 0.0,
    "right_shoulder_lift.pos": -10.0,
    "right_elbow_flex.pos": -20.0,
    "right_wrist_flex.pos": -30.0,
    "right_wrist_roll.pos": -40.0,
    "right_gripper.pos": 50.0,
}
FOLLOWER_POSE = {f"arm_{key}": value for key, value in LEADER_POSE.items()}
```

Update `FakeRobot.get_observation()` to increment `observation_sequence` and return a configurable complete follower pose. Update `FakeLeader.get_action()` to return a configurable complete leader pose while keeping existing event recording.

- [ ] **Step 2: Add focused pure-validation tests**

Add these separate tests before implementing the validator:

```python
def test_am1_validation_rejects_out_of_range_joint_with_exact_identity():
    values = {**LEADER_POSE, "right_shoulder_lift.pos": -105.8}
    with pytest.raises(module.SafetyRefusal, match=r"right shoulder_lift.*-105\.8.*-100\.\.100"):
        module.extract_am1_arm_positions(values, source="leader", leader_sample=True)


@pytest.mark.parametrize(
    ("values", "reason"),
    [
        (
            {key: value for key, value in LEADER_POSE.items() if key != "left_wrist_roll.pos"},
            r"missing.*left_wrist_roll\.pos",
        ),
        (
            {**LEADER_POSE, "arm_left_wrist_yaw.pos": 0.0},
            r"unexpected.*arm_left_wrist_yaw\.pos",
        ),
    ],
)
def test_am1_validation_rejects_missing_and_unexpected_arm_keys(values, reason):
    with pytest.raises(module.SafetyRefusal, match=reason):
        module.extract_am1_arm_positions(values, source="leader", leader_sample=True)


def test_am1_validation_ignores_legitimate_zero_body_keys():
    values = {**FOLLOWER_POSE, "x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0, "lift_axis.vel": 0}
    assert module.extract_am1_arm_positions(values, source="follower", leader_sample=False) == FOLLOWER_POSE
```

Parameterize non-finite cases with `math.nan`, `math.inf`, and `-math.inf`. Add literal boundary cases proving body `-100`/`100` and gripper `0`/`100` pass, while the `1e-6` tolerance is the only permitted overshoot.

- [ ] **Step 3: Run only the new validation tests and observe red**

Expected: collection or attribute failures because the constants, exception, and helper do not exist.

- [ ] **Step 4: Implement the minimal validator**

In `teleoperate_bi.py`:

```python
AM1_ARM_POSITION_KEYS = (
    "arm_left_shoulder_pan.pos",
    "arm_left_shoulder_lift.pos",
    "arm_left_elbow_flex.pos",
    "arm_left_wrist_flex.pos",
    "arm_left_wrist_roll.pos",
    "arm_left_gripper.pos",
    "arm_right_shoulder_pan.pos",
    "arm_right_shoulder_lift.pos",
    "arm_right_elbow_flex.pos",
    "arm_right_wrist_flex.pos",
    "arm_right_wrist_roll.pos",
    "arm_right_gripper.pos",
)
ACTION_RANGE_TOLERANCE = 1e-6


class SafetyRefusal(ValueError):
    pass
```

`extract_am1_arm_positions()` must:

1. prefix raw leader `.pos` keys with `arm_` only when `leader_sample=True`;
2. select keys that start with `arm_` and end with `.pos`;
3. report sorted missing/unexpected keys before returning;
4. convert values to `float` and require `math.isfinite`;
5. enforce `[-100, 100]` for non-grippers and `[0, 100]` for grippers only for leader samples;
6. include parsed side, joint, value, and expected range in range errors.

- [ ] **Step 5: Run the validation tests green, then the whole Packet 14B file**

Expected: pure tests and all prior lifecycle tests pass after complete fake poses are introduced.

- [ ] **Step 6: Commit validation primitives and fixtures**

```powershell
git add -- examples/alohamini/teleoperate_bi.py tests/robots/test_alohamini_windows_leader_client.py
git commit -m "test: define AM1 normalized action contract"
```

---

### Task 3: CLI contract and fresh startup alignment

**Files:**
- Modify: `tests/robots/test_alohamini_windows_leader_client.py`
- Modify: `examples/alohamini/teleoperate_bi.py`

**Interfaces:**
- Produces: CLI attributes `max_start_mismatch: float` and `check_alignment_only: bool`.
- Produces: `AlignmentRow` data, `get_fresh_follower_observation(robot: Any) -> dict[str, Any]`, and `run_alignment_gate(robot: Any, leader: Any, max_start_mismatch: float) -> tuple[dict[str, float], dict[str, Any]]`.
- `run_alignment_gate` raises `SafetyRefusal` for expected key/range/mismatch failures and returns the validated leader arm action plus the fresh follower observation on success.

- [ ] **Step 1: Add parser tests first**

Parameterize `--max_start_mismatch` with `0`, `-1`, `nan`, `inf`, and `-inf`; each must raise `SystemExit` from `argparse`. Assert the default is exactly `10.0`. Assert `--check_alignment_only --no_robot` and `--check_alignment_only --no_leader` are rejected before runtime.

- [ ] **Step 2: Run parser tests red**

Expected: unrecognized arguments or missing attributes.

- [ ] **Step 3: Implement parser options and validation**

Add:

```python
parser.add_argument("--max_start_mismatch", type=float, default=10.0)
parser.add_argument("--check_alignment_only", action="store_true")
```

After parsing:

```python
if not math.isfinite(args.max_start_mismatch) or args.max_start_mismatch <= 0:
    parser.error("--max_start_mismatch must be finite and greater than zero")
if args.check_alignment_only and (args.no_robot or args.no_leader):
    parser.error("--check_alignment_only requires both robot and leader connections")
```

- [ ] **Step 4: Add fresh/alignment pure behavior tests**

Test `get_fresh_follower_observation` with a fake whose first observation leaves `observation_sequence` unchanged and whose second increments it; assert the second pose is returned. Test alignment row literals so `signed_difference == leader - follower` and `absolute_difference == abs(signed_difference)`.

Add an integration-style fake test where one follower value differs by `10.1`; assert status `2`, output contains the exact key and threshold, all robot sends equal `make_zero_action()`, and all connected objects disconnect.

- [ ] **Step 5: Run alignment tests red**

Expected: missing helper failures and current arm-bearing send despite the mismatch.

- [ ] **Step 6: Implement fresh observation, table, and mismatch evaluation**

Use `observation_sequence` exactly as the recording script does:

```python
previous_sequence = robot.observation_sequence
deadline = time.monotonic() + robot.config.connect_timeout_s
while robot.observation_sequence == previous_sequence:
    observation = robot.get_observation()
    if robot.observation_sequence == previous_sequence and time.monotonic() >= deadline:
        raise RuntimeError("Timed out waiting for a fresh follower observation for alignment.")
```

Build rows in `AM1_ARM_POSITION_KEYS` order, print a compact fixed-width header/table, and raise `SafetyRefusal` identifying the worst or first-over-threshold row with all required numeric fields.

- [ ] **Step 7: Integrate the first gate into `run_teleoperation`**

After both leaders connect and before keyboard/visualization startup, run the gate only when `args.robot_model == "alohamini1"` and robot plus both leaders are connected. Catch only `SafetyRefusal`, print `SAFETY REFUSAL: <exact reason>`, and `return 2`; the existing `finally` performs zero-only cleanup.

Change the return annotation to `-> int` and return `0` after ordinary completion. Update `main()` to raise `SystemExit(status)` only when status is nonzero. Unexpected exceptions continue to propagate.

- [ ] **Step 8: Run the mismatch test and existing lifecycle tests green**

Confirm no payload with a key beginning `arm_` is present on refusal; safe zero sends remain expected.

- [ ] **Step 9: Commit the initial alignment gate**

```powershell
git add -- examples/alohamini/teleoperate_bi.py tests/robots/test_alohamini_windows_leader_client.py
git commit -m "fix: refuse unsafe AM1 startup alignment"
```

---

### Task 4: Paused recheck, first validated forwarding, and alignment-only mode

**Files:**
- Modify: `tests/robots/test_alohamini_windows_leader_client.py`
- Modify: `examples/alohamini/teleoperate_bi.py`

**Interfaces:**
- Consumes: validated `(arm_action, observation)` returned by `run_alignment_gate`.
- Produces: pending first arm action/observation consumed exactly once without a new leader read.
- Produces: alignment-only return statuses `0` and `2` with no arm-bearing send.

- [ ] **Step 1: Add the required lifecycle tests first**

Add the following named lifecycle tests, each with an explicit ordered-event assertion:

1. `test_initial_gate_forwards_the_validated_sample_first`: within-threshold immediate start returns `0`; the first arm-bearing robot event contains the exact first leader fixture and zero body/lift fields.
2. `test_start_paused_rechecks_fresh_both_sides_before_forwarding`: the fake supplies matching first poses and mismatched second poses after Enter; status is `2`, exactly two leader reads and two `observation_sequence` increments occur, and no arm-bearing send occurs.
3. `test_start_paused_forwards_second_validated_sample_without_extra_read`: both gates match but use distinguishable leader fixtures; the first arm-bearing event contains the second fixture, all fields from `make_zero_action()`, and its event index proves there was no third leader read.
4. `test_check_alignment_only_avoids_optional_runtime_resources_and_arm_send`: returns `0`, the keyboard and visualization constructors are patched to fail if called, and all robot sends have no `arm_*.pos` keys.
5. `test_out_of_range_startup_sample_is_expected_refusal`: the first leader fixture contains `right_shoulder_lift.pos=-105.8`; assert status `2`, captured stdout contains the complete reason, captured stderr contains no traceback, and no robot send has an arm key.
6. `test_runtime_validation_rejects_nonfinite_or_out_of_range_without_startup_mismatch_check`: after a matching gate, a later sample outside its physical range returns `2` and sends no invalid arm action.
7. `test_runtime_does_not_reapply_max_start_mismatch`: after a matching gate, a later sample is within per-joint range but differs from the stale follower pose by more than `max_start_mismatch`; assert it is forwarded, then terminate the fake loop normally.

The no-unchecked-read assertion compares the leader event count at the first arm-bearing robot send: it must equal the gate count, not gate count plus one.

- [ ] **Step 2: Run these lifecycle tests red**

Expected: current implementation reads again in the loop, lacks post-pause comparison, constructs optional resources in alignment-only mode, and does not return safety status.

- [ ] **Step 3: Implement alignment-only early return**

Immediately after a successful initial gate:

```python
if args.check_alignment_only:
    print("Alignment check passed; no arm action was sent.")
    return 0
```

This branch occurs before keyboard or visualization construction.

- [ ] **Step 4: Implement paused second gate and action-space summary**

Extend `_print_connection_summary()` with:

```text
Action space: body joints -100..100; grippers 0..100
```

After Enter, invoke `run_alignment_gate()` again. Replace the pending arm action and pending observation with that second result. A safety refusal returns `2` through normal cleanup.

- [ ] **Step 5: Consume the validated sample as the first send**

At loop entry, if a pending validated sample exists:

- use it without calling `leader.get_action()`;
- use its fresh observation without calling `robot.get_observation()`;
- merge it with a fresh `make_zero_action()` result even when keyboard is enabled;
- clear the pending values only after building that first action.

On subsequent loops, retain current keyboard behavior, read the leader normally, and pass every new leader sample through `extract_am1_arm_positions()` before sending. Catch `SafetyRefusal`, print the exact refusal, and return `2`. Do not call the mismatch comparator during later loops.

- [ ] **Step 6: Run all new lifecycle tests green**

Confirm status, read counts, first payload, zero-only refusal behavior, and cleanup ordering.

- [ ] **Step 7: Run all Packet 14B tests green**

Run the entire Windows client test file. Fix only expectation updates caused by the mandatory initial alignment; do not weaken existing cleanup assertions.

- [ ] **Step 8: Commit the completed forwarding guard**

```powershell
git add -- examples/alohamini/teleoperate_bi.py tests/robots/test_alohamini_windows_leader_client.py
git commit -m "fix: gate first AM1 follower action"
```

---

### Task 5: Native-Windows commissioning documentation

**Files:**
- Modify: `docs/alohamini/alohamini.md`

**Interfaces:**
- Documents: normalized units, manual pose matching, required alignment-only command, and bounded first motion.

- [ ] **Step 1: Update the Windows section**

State explicitly:

- Aloha Mini leader and follower body joints use normalized `-100..100` positions and grippers use `0..100`.
- Existing leader calibration files remain valid; this repair does not require recalibration.
- Until a clutch/alignment mode exists, manually support and place followers in a pose matching the passive leaders before Pi host activation.
- `--check_alignment_only` is mandatory before first motion.

Add this exact alignment-only command:

```powershell
.\.venv\Scripts\python.exe `
  .\examples\alohamini\teleoperate_bi.py `
  --robot.remote_ip 192.168.1.134 `
  --robot.robot_model alohamini1 `
  --teleop.left_port COM5 `
  --teleop.right_port COM6 `
  --teleop.id so101_leader_bi `
  --teleop.arm_profile so-arm-5dof `
  --max_start_mismatch 10 `
  --check_alignment_only `
  --no_rerun `
  --no_keyboard
```

Keep the existing bounded command and add `--max_start_mismatch 10`.

- [ ] **Step 2: Review documentation safety wording**

Confirm the text still requires the Pi host first for network checks, 7.4 V leader power only, supported followers, and accessible follower power disconnect. Ensure it never suggests 12 V leader power or `.\[all]` installation.

- [ ] **Step 3: Commit documentation**

```powershell
git add -- docs/alohamini/alohamini.md
git commit -m "docs: require AM1 alignment check"
```

---

### Task 6: Final focused verification and clean completion

**Files:**
- Verify only; modify earlier scoped files only if a focused check exposes a defect.

**Interfaces:**
- Produces: evidence for the required completion report and a clean committed tree.

- [ ] **Step 1: Run the focused Packet 14A/14B/R2 tests**

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests\robots\test_alohamini_safe_bringup.py `
  tests\robots\test_alohamini_windows_leader_client.py -q
```

Expected: all tests pass with zero failures.

- [ ] **Step 2: Compile changed Python files**

Use `PYTHONPYCACHEPREFIX` under `$env:TEMP` and run `python -m py_compile` on:

```text
examples/alohamini/leader_client_utils.py
examples/alohamini/calibrate_bi.py
examples/alohamini/teleoperate_bi.py
examples/alohamini/record_bi.py
tests/robots/test_alohamini_windows_leader_client.py
```

- [ ] **Step 3: Run help-only checks**

Invoke each affected script with `--help`: `calibrate_bi.py`, `teleoperate_bi.py`, and `record_bi.py`. These must exit before object construction.

- [ ] **Step 4: Run a fresh-process import check**

Import all three modules with `examples/alohamini` on `PYTHONPATH`; assert `lerobot.utils.visualization_utils` is absent from `sys.modules`.

- [ ] **Step 5: Inspect exact final scope**

Run:

```powershell
git diff 645964a9a3573ef8a36676391c64992e0513b06e..HEAD --check
git diff 645964a9a3573ef8a36676391c64992e0513b06e..HEAD --stat
git diff 645964a9a3573ef8a36676391c64992e0513b06e..HEAD --name-only
git status --short
```

Confirm no Pi motor/lift/camera module, calibration file, dependency file, simulation asset, or unrelated model behavior changed.

- [ ] **Step 6: If verification required a repair, commit it**

Stage only the affected scoped file and commit with `fix: correct AM1 alignment validation`. Re-run every command above afterward.

- [ ] **Step 7: Prepare the completion report**

Report the requested starting commit `645964a9a3573ef8a36676391c64992e0513b06e`, final SHA, branch, confirmed root cause, files/implementation, observed red tests, exact commands/results, assumptions, incomplete physical steps, exact alignment-only command, and exact bounded command. State explicitly that no physical validation occurred and nothing was pushed.
