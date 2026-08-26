# Aloha Mini 1 Simple Leader Calibration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` or `superpowers:subagent-driven-development`
> to execute this plan sequentially. Use one editing agent; review agents
> remain read-only. Every production behavior begins with an observed focused
> RED and ends with its named GREEN checks before commit.

**Goal:** Replace the Packet 2N-R5 normal operating path with one direct raw bus check and one supervised, staged, pair-consistent AM1 leader calibration command while preserving all historical evidence and AM2/AM2 Pro behavior.

**Architecture:** Add an explicit optional calibration leaf to the existing Aloha-specific calibration CLI. Keep the existing raw checker independent and guard it with exact `CHECK`. Implement the one-shot lifecycle in a PowerShell wrapper whose pure schema, snapshot, candidate, and promotion functions can be dot-sourced into temporary-directory tests. Stage both AM1 files outside the active directory, preserve a pair backup, clone the entire active SO-leader directory into a same-parent candidate, and use a fail-closed two-rename promotion with immediate handled rollback.

**Tech stack:** Python 3.12, argparse, existing LeRobot `BiSOLeaderConfig`, PowerShell 7, NTFS/.NET directory operations, pytest fakes, SHA-256, JSON calibration files.

**Authoritative design:** `docs/superpowers/specs/2026-08-25-am1-simple-leader-calibration-design.md`

## Global Constraints

- Work only on branch `fix/am1-elbow-commissioning`; preserve all existing history.
- The design commit is `75ea3c6b3d67fd15a10a83ca910d370a1a4deb9a`.
- Both leader supplies remain off and both USB controllers remain disconnected throughout software work.
- Follower/body power remains off and the Pi motor host remains stopped.
- Do not open COM ports, run calibration, run teleoperation, contact the Pi, start ZMQ/cameras, install packages, or initiate motion.
- The permitted status checks, fake tests, parsing, compilation, imports, help output, hashing, and offline file copies construct no hardware.
- Resolve the active calibration root from repository constants; never hard-code a user-profile path in production code.
- Durable identity remains left `COM8`, right `COM7`, ID `so101_leader_bi`, profile `so-arm-5dof`.
- Do not change motor IDs, calibration values, Phase, PID, limits, baud rates, action normalization, generic SO leader behavior, follower/Pi code, dependencies, cameras, base, lift, or ZMQ.
- Preserve every old archive, log, transcript, and `tools/packet2n_r5_leader_mapping.ps1`.
- Do not add a persistent session, stage, receipt, journal, mapping record, or recovery state to the new workflow.
- The old runner receives only the checker confirmation argument required to keep its historical bus-check stage coherent.
- All candidate and withdrawal paths must be direct nonexistent siblings of the resolved active `so_leader` directory and on the same volume.
- Refuse reparse points, junctions, symlinks, nested directories, and nonregular entries beneath the active directory before any calibration child can start.
- Treat the no-robot side verification as a later separately authorized leader-bus action. Document it; do not run it.
- Do not push.

## File Responsibility Map

| File | Planned responsibility |
| --- | --- |
| `examples/alohamini/calibrate_bi.py` | Parse an optional explicit calibration leaf and pass it to `BiSOLeaderConfig`; preserve default `None`. |
| `tools/check_am1_leader_buses.py` | Require positional exact uppercase `CHECK` before port discovery or bus construction. |
| `tools/packet2n_r5_leader_mapping.ps1` | Append positional `CHECK` to its historical checker command only. |
| `tools/calibrate_am1_leaders.ps1` | New normal `-Status` / `-Calibrate` wrapper, provenance, validation, staging, backup, promotion, rollback, and output. |
| `tests/robots/test_alohamini_windows_leader_client.py` | Explicit calibration-leaf default/override and model-isolation coverage. |
| `tests/robots/test_check_am1_leader_buses.py` | Direct confirmation gate and existing no-write/read completeness regressions. |
| `tests/robots/test_packet2n_r5_leader_mapping.py` | One exact historical-runner checker-command compatibility assertion. |
| `tests/robots/test_calibrate_am1_leaders.py` | New focused temporary-directory and fake-native wrapper tests. |
| `docs/alohamini/alohamini.md` | New normal procedure, corrected ports, deprecation boundary, physical start/stop rules, and no-robot side check. |

No core file under `src/lerobot/`, no follower robot file, and no dependency file may change.

## Wrapper Interface Contract

The script parameters are intentionally small:

```powershell
[CmdletBinding()]
param(
    [switch]$Status,
    [switch]$Calibrate,
    [string]$Confirm,
    [string]$LeftPort = 'COM8',
    [string]$RightPort = 'COM7',
    [string]$LeaderId = 'so101_leader_bi',
    [string]$ArmProfile = 'so-arm-5dof'
)
```

`-Status` and `-Calibrate` are mutually exclusive and exactly one is required. Identity parameters exist only to make a wrong value an explicit pre-hardware refusal; normal use omits them. `-Calibrate` requires case-sensitive `CALIBRATE`.

The script is safe to dot-source: it defines functions but dispatches only when `$MyInvocation.InvocationName -ne '.'`. Tests call pure/internal functions with temporary paths and fake scriptblocks; the normal operator CLI exposes no active-root, Python, run-root, or native-invoker override.

Use these internal boundaries, with minor naming changes allowed only if test seams remain equivalent:

```powershell
function Assert-Am1FixedIdentity { ... }
function Assert-Am1CalibrationFile { ... }
function Get-Am1CalibrationPairStatus { ... }
function Get-Am1RegularFileSnapshot { ... }
function Assert-Am1SnapshotMatches { ... }
function New-Am1PairBackup { ... }
function New-Am1PromotionCandidate { ... }
function Invoke-Am1DirectoryPromotion { ... }
function Get-Am1RepositoryProvenance { ... }
function Invoke-Am1NativeCalibration { ... }
function Invoke-Am1CalibrationAttempt { ... }
function Invoke-Am1LeaderCalibrationMain { ... }
```

`Invoke-Am1CalibrationAttempt` accepts internal dependency-injection parameters for tests, including a native invoker and directory-move scriptblock. The top-level main supplies only production implementations.

Routine status prints `VALID_COMPLETE_PAIR` or `INCOMPLETE_OR_INVALID_PAIR`. It does not claim historical provenance. The already completed offline audit retains the stronger one-time classification `TRUSTED_ORIGINAL_PAIR`.

## Plan Materialization Gate

Before implementation begins, commit this reviewed plan by itself:

```powershell
git add -- docs/superpowers/plans/2026-08-25-am1-simple-leader-calibration-implementation.md
git diff --cached --check
git diff --cached -- docs/superpowers/plans/2026-08-25-am1-simple-leader-calibration-implementation.md
git commit -m "docs(alohamini): plan simple leader calibration workflow"
```

Record the resulting full SHA. Do not begin the execution preflight while this plan is untracked, staged, or accompanied by another worktree change.

## Execution Preflight and Offline Retirement Snapshot

- [ ] Confirm the checkout before any production edit:

```powershell
git branch --show-current
git rev-parse HEAD
git status --porcelain=v1 --untracked-files=all
```

Require `fix/am1-elbow-commissioning`, empty status, and:

```powershell
$planCommit = git log -1 --format=%H -- docs/superpowers/plans/2026-08-25-am1-simple-leader-calibration-implementation.md
if ($planCommit -cne (git rev-parse HEAD)) { throw 'HEAD is not the committed implementation plan' }
```

Stop without reset, rebase, checkout, amend, or cleanup if any precondition differs.

- [ ] Reconfirm the human-stated physical state: both leader supplies off, both leader USB controllers disconnected, follower/body power off, and Pi host stopped.

- [ ] Run the current fake-only baseline:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests\robots\test_alohamini_windows_leader_client.py `
  tests\robots\test_check_am1_leader_buses.py `
  -q
```

Stop if any baseline failure is unrelated to the planned missing behavior.

- [ ] Derive the active root with the exact repository Python and verify the two trusted hashes before copying:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$repoPython = (Resolve-Path -LiteralPath '.\.venv\Scripts\python.exe').Path
$calibrationRoot = (& $repoPython -B -c "from lerobot.utils.constants import HF_LEROBOT_CALIBRATION; print(HF_LEROBOT_CALIBRATION)" | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Could not resolve HF_LEROBOT_CALIBRATION' }
$activeDirectory = Join-Path $calibrationRoot 'teleoperators\so_leader'
$activeLeft = Join-Path $activeDirectory 'so101_leader_bi_left.json'
$activeRight = Join-Path $activeDirectory 'so101_leader_bi_right.json'
if ((Get-FileHash -LiteralPath $activeLeft -Algorithm SHA256).Hash -cne '6F5D6126E84398D0621A26E74E4DF6678EBA7C14C62D343020610B4D5D8B3D8C') { throw 'Trusted left calibration changed' }
if ((Get-FileHash -LiteralPath $activeRight -Algorithm SHA256).Hash -cne '65A301F20FC7DC96BD7FB5982E3670BF1A01F535953D7A253AB8D33A03646F11') { throw 'Trusted right calibration changed' }
```

- [ ] Create the one-time retirement snapshot before Task 1 production changes. Use the user profile API rather than embedding a profile path; copy, never move:

```powershell
$profileRoot = [Environment]::GetFolderPath('UserProfile')
$backupRoot = Join-Path $profileRoot 'AlohaMini1Backups'
$logsRoot = Join-Path $profileRoot 'AlohaMini1Logs'
$expectedRunnerFiles = [ordered]@{
  'packet2n-r5-calibration-897f00dc-2608-4790-a74b-1482220eb5ed.log' = '6BA8699C55BED9074EFBBD18637CEB8FCD337CD70C84629C0C6036BE32768447'
  'packet2n-r5-calibration-a9128060-c60c-4582-8cb8-cf45fc1750e6.log' = 'CB4FF5FD33756D47A6864F2B4DD55D5129D9E22D7DAF86E1C31D2FBA93E2ED05'
  'packet2n-r5-evidence-a9128060-c60c-4582-8cb8-cf45fc1750e6.json' = '01484B85820A0674988A88788DD2C8A941092B6BEE8B1BD2A61C0038E071567C'
}
$actualRunnerNames = @(
  Get-ChildItem -LiteralPath $logsRoot -File -Filter 'packet2n-r5-*' |
    Sort-Object Name |
    Select-Object -ExpandProperty Name
)
if (Compare-Object @($expectedRunnerFiles.Keys | Sort-Object) $actualRunnerNames) {
  throw 'Live Packet 2N-R5 runner-file inventory changed'
}
$runnerSources = @()
foreach ($entry in $expectedRunnerFiles.GetEnumerator()) {
  $source = Join-Path $logsRoot $entry.Key
  if ((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash -cne $entry.Value) {
    throw "Runner evidence hash changed: $source"
  }
  $runnerSources += $source
}
$retiredStateArchive = Join-Path $backupRoot 'packet2n-r5-interrupted-897f00dc-2608-4790-a74b-1482220eb5ed'
if (-not (Test-Path -LiteralPath (Join-Path $retiredStateArchive 'retired-state\packet2n-r5-state.json') -PathType Leaf)) {
  throw "Retired interrupted state archive is incomplete: $retiredStateArchive"
}
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$retirementRoot = Join-Path $backupRoot "packet2n-lc1-retirement-$stamp"
if (Test-Path -LiteralPath $retirementRoot) { throw "Retirement path already exists: $retirementRoot" }
$pairDestination = Join-Path $retirementRoot 'active-calibration'
$runnerDestination = Join-Path $retirementRoot 'live-runner-files'
New-Item -ItemType Directory -Path $pairDestination,$runnerDestination | Out-Null
Copy-Item -LiteralPath $activeLeft,$activeRight -Destination $pairDestination
foreach ($source in $runnerSources) { Copy-Item -LiteralPath $source -Destination $runnerDestination }
$retirementRoot
$retiredStateArchive
```

- [ ] Verify the copied pair hashes and byte identities and reverify all three copied runner-file hashes. Do not create a manifest, receipt, or state file. Confirm that the active files remain at their original paths with their original hashes and mtimes. Record both printed archive paths for the final report.

- [ ] Do not restore anything. The preflight classification remains `TRUSTED_ORIGINAL_PAIR`.

---

### Task 1: Expose the existing calibration-directory boundary

**Files:**
- Modify: `examples/alohamini/calibrate_bi.py`
- Test: `tests/robots/test_alohamini_windows_leader_client.py`

- [ ] **Step 1: Add failing default and explicit-leaf tests**

Add focused tests beside the existing `calibrate_bi` configuration tests:

```python
def test_calibrate_bi_calibration_dir_defaults_to_existing_resolution():
    module = load_example_module("calibrate_bi")
    args = module.parse_args([], platform_name="Linux")
    config = module.make_leader_config(args)
    assert args.calibration_dir is None
    assert config.calibration_dir is None


def test_calibrate_bi_passes_explicit_calibration_leaf_to_bimanual_config(tmp_path):
    module = load_example_module("calibrate_bi")
    leaf = tmp_path / "staged-calibration" / "teleoperators" / "so_leader"
    args = module.parse_args(
        ["--teleop.calibration_dir", str(leaf)], platform_name="Linux"
    )
    config = module.make_leader_config(args)
    assert config.calibration_dir == leaf
```

The unchanged reviewed `BiSOLeader` implementation already passes the top-level `calibration_dir` to both children. Do not instantiate it in this test; the test remains a pure configuration check and constructs no motor bus.

Also retain assertions for `use_degrees=False`, default IDs, default ports on POSIX, explicit Windows ports, and the `so-arm-5dof` fresh-calibration guard.

- [ ] **Step 2: Observe RED**

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests\robots\test_alohamini_windows_leader_client.py `
  -k 'calibrate_bi_calibration_dir' -q
```

Expected RED: the parser rejects `--teleop.calibration_dir`, and the default namespace lacks `calibration_dir`. Stop if the failure is an import or dependency problem.

- [ ] **Step 3: Add the smallest production change**

In `calibrate_bi.py`:

```python
from pathlib import Path

parser.add_argument(
    "--teleop.calibration_dir",
    dest="calibration_dir",
    type=Path,
    default=None,
    help="Explicit calibration leaf directory for this bimanual run",
)
```

Build the existing normalized config unchanged, assign `config.calibration_dir = args.calibration_dir`, and return it. Do not edit `leader_client_utils.py`, `BiSOLeader`, `SOLeader`, or any default path constant.

- [ ] **Step 4: Verify GREEN and focused regressions**

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests\robots\test_alohamini_windows_leader_client.py `
  -k 'calibrate_bi_calibration_dir or calibration_uses_passive or calibration_cleanup or force_fresh_calibration or leader_config' -q
```

- [ ] **Step 5: Compile, inspect, and commit**

```powershell
$env:PYTHONPYCACHEPREFIX = Join-Path $env:TEMP 'am1-lc1-task1-pycache'
.\.venv\Scripts\python.exe -m py_compile `
  examples\alohamini\calibrate_bi.py `
  tests\robots\test_alohamini_windows_leader_client.py
git diff --check
git diff -- examples/alohamini/calibrate_bi.py tests/robots/test_alohamini_windows_leader_client.py
git add -- examples/alohamini/calibrate_bi.py tests/robots/test_alohamini_windows_leader_client.py
git commit -m "feat(alohamini): support staged leader calibration files"
```

Review gate: default omission remains `None`; no model-specific behavior outside the Aloha calibration script changes.

---

### Task 2: Require direct raw-bus authorization

**Files:**
- Modify: `tools/check_am1_leader_buses.py`
- Modify: `tools/packet2n_r5_leader_mapping.ps1` (one argument only)
- Test: `tests/robots/test_check_am1_leader_buses.py`
- Test: `tests/robots/test_packet2n_r5_leader_mapping.py` (one focused assertion only)

- [ ] **Step 1: Add exact RED tests for the missing confirmation contract**

Add one test proving missing confirmation refuses before the injected `run`, and one proving exact `CHECK` calls it once and returns `0`:

```python
def test_missing_confirmation_refuses_before_constructing_a_bus():
    called = False
    def forbidden_run():
        nonlocal called
        called = True
        raise AssertionError("refusal must precede bus construction")
    with pytest.raises(SystemExit) as caught:
        main([], run=forbidden_run)
    assert caught.value.code == 2
    assert called is False


def test_exact_check_confirmation_runs_once():
    calls = 0
    def clean_run():
        nonlocal calls
        calls += 1
        return object()
    assert main(["CHECK"], run=clean_run) == 0
    assert calls == 1
```

After the gate exists, add case-sensitivity regressions for `["check"]`, `[" CHECK"]`, and `["CHECK "]`; these variants already fail under the current no-positional parser, so they are not claimed as RED evidence.

Extend the historical runner test with an exact source/command assertion requiring `@($busCheckScript, "CHECK")`.

- [ ] **Step 2: Observe RED**

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests\robots\test_check_am1_leader_buses.py `
  tests\robots\test_packet2n_r5_leader_mapping.py::test_check_leader_buses_runner_requires_exact_guards_and_uses_reviewed_command `
  -q
```

Expected RED: missing confirmation still enters the fake run instead of exiting `2`; exact `CHECK` is rejected as an unrecognized positional; and the new historical-runner source assertion cannot find the appended argument. The lowercase/whitespace regression cases are expected to remain refusing both before and after implementation.

- [ ] **Step 3: Add the gate and compatibility argument**

In the checker parser, add required positional `confirmation` and validate exact ordinal text before entering its `try: run()` block. `--help` must still exit `0` without `run()`.

Change only the historical runner's `CheckLeaderBuses` arguments:

```powershell
"CheckLeaderBuses" {
    @($busCheckScript, "CHECK")
}
```

- [ ] **Step 4: Verify GREEN and all checker safety behavior**

Run the Step 2 command, then:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests\robots\test_check_am1_leader_buses.py -q
```

Require all existing raw IDs, no-retry, disappearance, malformed-sample, `KeyboardInterrupt`, cleanup, and `disable_torque=False` tests to remain green.

- [ ] **Step 5: Compile, parse, inspect, and commit**

```powershell
$env:PYTHONPYCACHEPREFIX = Join-Path $env:TEMP 'am1-lc1-task2-pycache'
.\.venv\Scripts\python.exe -m py_compile `
  tools\check_am1_leader_buses.py `
  tests\robots\test_check_am1_leader_buses.py
$tokens = $null; $errors = $null
[System.Management.Automation.Language.Parser]::ParseFile(
  (Resolve-Path '.\tools\packet2n_r5_leader_mapping.ps1'),
  [ref]$tokens,
  [ref]$errors
) | Out-Null
if ($errors.Count) { throw ($errors | Out-String) }
git diff --check
git diff -- tools/check_am1_leader_buses.py tools/packet2n_r5_leader_mapping.ps1 tests/robots/test_check_am1_leader_buses.py tests/robots/test_packet2n_r5_leader_mapping.py
git add -- tools/check_am1_leader_buses.py tools/packet2n_r5_leader_mapping.ps1 tests/robots/test_check_am1_leader_buses.py tests/robots/test_packet2n_r5_leader_mapping.py
git commit -m "fix(alohamini): require direct leader bus confirmation"
```

---

### Task 3: Add read-only status, schema, identity, and provenance

**Files:**
- Create: `tools/calibrate_am1_leaders.ps1`
- Create: `tests/robots/test_calibrate_am1_leaders.py`

- [ ] **Step 1: Establish the dot-source test seam with its own RED/GREEN**

The Python test helper writes a temporary `.ps1` harness and invokes `pwsh -NoLogo -NoProfile -File <harness>`. The harness dot-sources the production wrapper, calls one internal function, and serializes only its result. All calibration fixtures live below `tmp_path`.

First add only `test_wrapper_can_be_dot_sourced_without_dispatch`. Observe RED because the wrapper file is missing. Add the parameter block, strict-mode setup, a no-op internal version function, and the `$MyInvocation.InvocationName -ne '.'` dispatch guard. Rerun only this test to GREEN. Do not add schema, status, provenance, or calibration behavior in this slice.

- [ ] **Step 2: Add focused RED tests for pure schema, identity, snapshot, and command behavior**

Add tests for:

- a valid six-joint pair returns `VALID_COMPLETE_PAIR`, correct sizes, mtimes, and hashes;
- one missing side returns `INCOMPLETE_OR_INVALID_PAIR` with the missing path;
- malformed JSON, extra/missing field, boolean integer, wrong/duplicate ID, bad range, and wrong wrist-roll range are rejected;
- identical left/right payloads are rejected;
- wrong port, ID, profile, or confirmation calls no injected native invoker;
- a nested directory or reparse entry in the active tree is refused;
- the command builder contains exactly repository Python, `calibrate_bi.py`, COM8/COM7, ID, profile, explicit staging leaf, and `--force_fresh_calibration`.

- [ ] **Step 3: Observe the pure-helper RED**

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests\robots\test_calibrate_am1_leaders.py `
  -k 'pair or schema or identity or snapshot or command or reparse' -q
```

Expected RED: the dot-sourceable wrapper exists, but the named schema, identity, snapshot, and command functions do not. Each test failure must name its specific absent behavior rather than a dot-source/parser failure.

- [ ] **Step 4: Implement pure schema, identity, snapshot, and command functions**

Implement exact joint/field checks from the design. Use ordinal path/name comparisons and `Get-FileHash -Algorithm SHA256`. Snapshot objects contain the complete sorted relative path set, SHA-256, and size for every regular file.

Run the Step 3 selection unchanged and require GREEN before continuing.

- [ ] **Step 5: Add focused RED tests for provenance and read-only status**

Add tests proving:

- provenance reports branch, full HEAD, and porcelain worktree status but does not require the worktree to be clean;
- executable, prefix, working directory, or any imported module outside the repository refuses;
- nonempty `PYTHONPATH`, `PYTHONHOME`, `PYTHONSTARTUP`, or `PYTHONUSERBASE` refuses before the probe;
- every repository Python probe uses `-B`;
- status calls no native calibration invoker, creates no run/backup/staging path, and reports both active identities;
- the no-write status test explicitly removes inherited `PYTHONDONTWRITEBYTECODE` before invocation and still finds no new file, directory, `__pycache__`, or Git index change.

Run only these new tests. Expected RED: the pure functions are green, while provenance/status entry points are absent.

- [ ] **Step 6: Implement provenance and status**

`-Status` must:

- derive repository root from `$PSScriptRoot\..`;
- validate the fixed identity arguments before any Python child;
- locate exact `.venv\Scripts\python.exe`;
- run the offline provenance/import probe;
- resolve `HF_LEROBOT_CALIBRATION` through that Python;
- inspect only the two expected active files and print current facts;
- print branch, full HEAD, and porcelain status;
- create nothing and construct no bus/leader.

The import probe verifies `cwd`, `sys.executable`, `sys.prefix`, and exact repository-owned paths for `lerobot`, `calibrate_bi`, `leader_client_utils`, `BiSOLeader`, and `SOLeader`. Reject nonempty `PYTHONPATH`, `PYTHONHOME`, `PYTHONSTARTUP`, or `PYTHONUSERBASE` before the probe. Invoke every probe as the exact repository Python with `-B` so routine status cannot create bytecode even when the caller did not set `PYTHONDONTWRITEBYTECODE`. Obtain porcelain status with Git optional locks disabled (`git --no-optional-locks status ...`) so read-only status does not refresh/write the index.

- [ ] **Step 7: Verify all Task 3 tests GREEN**

Run the full new test file, then safely run parser-only checks:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests\robots\test_calibrate_am1_leaders.py -q
$tokens = $null; $errors = $null
[System.Management.Automation.Language.Parser]::ParseFile(
  (Resolve-Path '.\tools\calibrate_am1_leaders.ps1'),
  [ref]$tokens,
  [ref]$errors
) | Out-Null
if ($errors.Count) { throw ($errors | Out-String) }
pwsh -NoLogo -NoProfile -File .\tools\calibrate_am1_leaders.ps1 -?
```

Neither command may create a run directory or invoke calibration.

- [ ] **Step 8: Inspect and commit**

```powershell
git diff --check
git diff -- tools/calibrate_am1_leaders.ps1 tests/robots/test_calibrate_am1_leaders.py
git add -- tools/calibrate_am1_leaders.ps1 tests/robots/test_calibrate_am1_leaders.py
git commit -m "feat(alohamini): add simple leader calibration status"
```

Review gate: no `C:\\Users\\...` literal, no receipt/state vocabulary in output structures, and no hardware constructor in the status path.

---

### Task 4: Implement staged calibration and pair-consistent promotion

**Files:**
- Modify: `tools/calibrate_am1_leaders.ps1`
- Modify: `tests/robots/test_calibrate_am1_leaders.py`

- [ ] **Step 1: Add RED tests for native launch, evidence, and pre-promotion failure**

Using temporary active/run directories and injected fake native/move functions, add the minimum consequential cases:

1. backup copy or backup hash-verification failure calls no native invoker and leaves the active tree unchanged;
2. native launch failure leaves the complete active tree byte-for-byte unchanged and prints `FAIL`;
3. `launched=false` cannot produce `PASS`;
4. nonzero native exit preserves staged partial files/transcript/backup and leaves active unchanged;
5. a simulated native interruption stops the transcript in `finally`, returns nonzero, preserves evidence, performs no promotion, and leaves active byte-for-byte unchanged;
6. missing or malformed staged side prevents promotion;
7. failure output contains one exact primary reason and never contains `CALIBRATION_RESULT=PASS`.

The fake native invoker writes staged JSON only when the case requires it. It never imports a motor module or invokes `calibrate_bi.py`.

- [ ] **Step 2: Observe the native/pre-promotion RED**

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests\robots\test_calibrate_am1_leaders.py `
  -k 'backup or launch or nonzero or interrupt or staged or failure_result' -q
```

Expected RED: Task 3 status/pure helpers remain green, while the calibration-attempt/native orchestration functions are absent. Confirm no failure mentions COM or an attempted hardware connection.

- [ ] **Step 3: Implement through staged-pair validation only**

Implement production order 1 through 10 below. Do not create or rename a candidate yet. `Invoke-Am1NativeCalibration` must expose injected start-transcript, command, and stop-transcript seams so the interruption test exercises the real `try/finally` cleanup. Treat `PipelineStoppedException`, `OperationCanceledException`, and `KeyboardInterrupt`-equivalent termination as nonzero interruption, never success. Run the Step 2 selection and require every native/pre-promotion case GREEN.

- [ ] **Step 4: Add RED tests for candidate construction and successful promotion**

Add tests proving:

- valid distinct staged files promote together and final hashes equal staged hashes;
- an unrelated AM2/SO-leader fixture survives with the exact relative path and bytes;
- candidate/withdrawal paths outside the direct same-volume parent refuse;
- successful output contains `CALIBRATION_RESULT=PASS`, both active hashes, pair-backup path, staged-evidence path, and next command.

Expected RED: staging/failure behavior remains green, while candidate and promotion functions are absent.

- [ ] **Step 5: Implement candidate construction and the success path**

Implement production order 11 through 18 below, including final tree/hash verification before any `PASS`. Run only the new candidate/success tests to GREEN.

- [ ] **Step 6: Add RED tests for concurrency and the complete rollback window**

Add tests proving:

- a detected active-tree change after staging refuses before the first rename;
- a simulated second rename failure restores withdrawal to active and preserves the original failure;
- a simulated final tree/hash verification failure moves the newly promoted active directory back to the vacant candidate path, restores withdrawal to active, and preserves the verification failure;
- a rollback failure is reported as secondary while the original promotion/verification failure remains primary;
- withdrawal cleanup cannot occur until final verification succeeds.

Expected RED: the basic success path is green, but rollback currently does not cover every post-first-rename failure.

- [ ] **Step 7: Extend rollback through final verification and verify GREEN**

From the first rename until final verification succeeds, any handled exception is primary and triggers rollback:

- if a newly promoted active directory exists, move it back to the now-vacant candidate path;
- if the complete withdrawal directory exists and active is absent, move withdrawal back to active;
- attach rollback/cleanup failures as secondary details without replacing the primary;
- delete withdrawal only after final verification and backup verification succeed.

Run all Task 4 selections and require GREEN.

The complete production order is exact:

1. validate mode, `CALIBRATE`, identity, executable, imports, and active pair;
2. capture the complete active regular-file snapshot;
3. create `<HF_LEROBOT_CALIBRATION parent>\am1-leader-calibration-runs\<timestamp>`;
4. copy and verify the two active AM1 files under `backup-active-pair`;
5. create the explicit empty staging leaf;
6. build and print the exact native command;
7. start one transcript, launch the exact repository Python directly so console input remains interactive, capture `$LASTEXITCODE` immediately, and stop the transcript in `finally`;
8. on launch/nonzero/interrupt, preserve evidence and prove active unchanged;
9. validate both staged files;
10. repeat the active snapshot comparison;
11. create direct-sibling candidate and withdrawal names that do not exist;
12. clone the active regular files into candidate, replace only the AM1 leaves, and validate the full candidate snapshot;
13. print active/candidate/withdrawal/backup paths and the fail-closed rename-back instruction;
14. move active to withdrawal, then candidate to active;
15. keep rollback armed across the second move and final verification;
16. verify the final full tree and AM1 hashes, rolling back on any handled failure;
17. disarm rollback and remove only the exact verified redundant withdrawal directory;
18. print concise `PASS` and next command.

No output may say `PASS` unless the native command launched, returned `0`, both staged files validated, both renames completed, and final hashes match.

- [ ] **Step 8: Verify GREEN and the complete wrapper suite**

Run every Task 4 selection, then:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests\robots\test_calibrate_am1_leaders.py -q
```

- [ ] **Step 9: Run the real read-only status path**

With leaders still off/disconnected:

```powershell
pwsh -NoLogo -NoProfile -Command "Remove-Item Env:PYTHONDONTWRITEBYTECODE -ErrorAction SilentlyContinue; & '.\tools\calibrate_am1_leaders.ps1' -Status"
```

Require `VALID_COMPLETE_PAIR`, the already established left/right hashes, no new run directory or bytecode file, and no COM/serial text. Do **not** run `-Calibrate`.

- [ ] **Step 10: Parse, inspect, and commit**

```powershell
$tokens = $null; $errors = $null
[System.Management.Automation.Language.Parser]::ParseFile(
  (Resolve-Path '.\tools\calibrate_am1_leaders.ps1'),
  [ref]$tokens,
  [ref]$errors
) | Out-Null
if ($errors.Count) { throw ($errors | Out-String) }
git diff --check
git diff -- tools/calibrate_am1_leaders.ps1 tests/robots/test_calibrate_am1_leaders.py
git add -- tools/calibrate_am1_leaders.ps1 tests/robots/test_calibrate_am1_leaders.py
git commit -m "feat(alohamini): promote staged leader calibration pair"
```

---

### Task 5: Replace the normal documentation path

**Files:**
- Modify: `docs/alohamini/alohamini.md`
- Modify: `tests/robots/test_calibrate_am1_leaders.py`

- [ ] **Step 1: Add failing documentation-contract tests**

Read the documentation as text and require:

- heading `Simple AM1 leader calibration and recovery`;
- direct checker command ending in positional `CHECK`;
- `-Status` and `-Calibrate -Confirm CALIBRATE` commands;
- corrected normal ports left COM8/right COM7;
- explicit historical/deprecated label for `packet2n_r5_leader_mapping.ps1`;
- failure guidance saying active calibration files stay unchanged before promotion and a complete fresh rerun is required after fixing the connection;
- wrist-roll guidance: do not force it during range recording; implementation assigns `0..4095`;
- exact no-robot 30-second command with `--no_robot`, `--require_calibration_match`, `--no_keyboard`, and `--no_rerun`;
- physical left gripper maps only to `arm_left_gripper.pos`, then right only to `arm_right_gripper.pos`;
- physical starting state and immediate-stop conditions;
- no remaining normal-use sentence calling the old runner the sole/current authority.

- [ ] **Step 2: Observe RED**

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests\robots\test_calibrate_am1_leaders.py -k documentation -q
```

Expected RED: the new section and commands do not exist and old sole-authority language remains.

- [ ] **Step 3: Update documentation concisely**

Make the new section the only live normal calibration path. Correct normal-use AM1 port examples. Preserve historical command/evidence text inside a clearly labeled non-repeatable historical/deprecated section; do not delete forensic hashes, archive paths, transcripts, or the old script.

Document the later physical sequence without running it:

1. Raw checker: follower power off, Pi host stopped, both leaders connected to corrected ports and powered, workspace clear; move right moderately, then left; stop on any failure.
2. One-shot calibration: both corrected leader buses stable, designated 7.4 V supplies, follower off, exact `CALIBRATE`; move only joints requested by the existing calibration prompts; do not force wrist roll.
3. No-robot side check: both leaders powered/connected, follower off/Pi stopped; move only left gripper then only right gripper; stop on wrong-side/both-side response, error, sound, heat, current, or disconnect.

- [ ] **Step 4: Verify GREEN, inspect, and commit**

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests\robots\test_calibrate_am1_leaders.py -k documentation -q
git diff --check
git diff -- docs/alohamini/alohamini.md tests/robots/test_calibrate_am1_leaders.py
git add -- docs/alohamini/alohamini.md tests/robots/test_calibrate_am1_leaders.py
git commit -m "docs(alohamini): simplify leader calibration workflow"
```

---

## Final Validation and Review

- [ ] Confirm all intended commits in order and no unrelated path:

```powershell
git log --oneline --decorate 75ea3c6b..HEAD
git diff --name-status 75ea3c6b..HEAD
```

- [ ] Run the complete focused Python suites without bytecode/cache writes:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests\robots\test_alohamini_windows_leader_client.py `
  tests\robots\test_check_am1_leader_buses.py `
  tests\robots\test_calibrate_am1_leaders.py `
  -q
```

- [ ] Run only the one historical-runner compatibility test, not its hundreds-test transaction campaign:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests\robots\test_packet2n_r5_leader_mapping.py::test_check_leader_buses_runner_requires_exact_guards_and_uses_reviewed_command `
  -q
```

- [ ] Run explicit AM2/AM2 Pro isolation selections:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  tests\robots\test_alohamini_windows_leader_client.py `
  -k 'alohamini2 or alohamini2pro or strict_mode_never_calls_startup_sync' -q
```

- [ ] Compile every changed Python/test file outside the worktree cache:

```powershell
$env:PYTHONPYCACHEPREFIX = Join-Path $env:TEMP 'am1-lc1-final-pycache'
.\.venv\Scripts\python.exe -m py_compile `
  examples\alohamini\calibrate_bi.py `
  tools\check_am1_leader_buses.py `
  tests\robots\test_alohamini_windows_leader_client.py `
  tests\robots\test_check_am1_leader_buses.py `
  tests\robots\test_calibrate_am1_leaders.py `
  tests\robots\test_packet2n_r5_leader_mapping.py
```

- [ ] Parse both PowerShell scripts:

```powershell
foreach ($path in @('.\tools\calibrate_am1_leaders.ps1', '.\tools\packet2n_r5_leader_mapping.ps1')) {
  $tokens = $null; $errors = $null
  [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path $path), [ref]$tokens, [ref]$errors) | Out-Null
  if ($errors.Count) { throw "$path`n$($errors | Out-String)" }
}
```

- [ ] Run safe help commands only:

```powershell
.\.venv\Scripts\python.exe .\examples\alohamini\calibrate_bi.py --help
.\.venv\Scripts\python.exe .\tools\check_am1_leader_buses.py --help
pwsh -NoLogo -NoProfile -File .\tools\calibrate_am1_leaders.ps1 -?
```

- [ ] Verify fresh imports and lazy visualization:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, 'examples/alohamini'); import calibrate_bi, teleoperate_bi, record_bi; assert 'lerobot.utils.visualization_utils' not in sys.modules; print('fresh imports OK; visualization lazy')"
```

- [ ] Re-run real `-Status` only from a child with `PYTHONDONTWRITEBYTECODE` removed, and confirm the wrapper's `-B` probes create neither a run artifact nor bytecode. Do not run the checker with `CHECK`, `-Calibrate`, `teleoperate_bi.py`, or any command capable of opening a port.

- [ ] Run whitespace, complete-range, secret, and hard-coded-local-path review:

```powershell
git diff --check 75ea3c6b..HEAD
git diff --stat 75ea3c6b..HEAD
git diff 75ea3c6b..HEAD
git diff 75ea3c6b..HEAD | Select-String -CaseSensitive:$false -Pattern `
  'BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY|api[_-]?key|access[_-]?token|client[_-]?secret|password\s*=|C:\\Users\\pickm'
```

Inspect every changed line. Confirm any historical absolute path appears only in unchanged context, never in added production/test lines.

- [ ] Obtain a read-only code review of the complete range. Repair any material finding with a new focused RED/GREEN cycle and a new commit; do not amend prior commits.

- [ ] Confirm final branch, SHA, and clean status:

```powershell
git branch --show-current
git rev-parse HEAD
git status --porcelain=v1 --untracked-files=all
```

Do not push.

## Final Report Contract

Report:

- starting branch and commit;
- design and plan commits;
- task commits in order;
- actual pre-repair classification `TRUSTED_ORIGINAL_PAIR`;
- left/right original hashes and confirmation that no restoration occurred;
- one-time retirement snapshot path and copied runner-file inventory;
- existing retired invalid-state archive path;
- every RED command, failure reason, GREEN command, count, and result;
- files changed and confirmation that no core/AM2/Pi/dependency path changed;
- PowerShell parse, compile, help, import, lazy-visualization, status, diff, and scan results;
- exact raw bus-check command;
- exact one-shot calibration command;
- exact no-robot side-verification command;
- physical starting state, pass criteria, and immediate-stop conditions for each later human step;
- Windows directory-exchange limitation and printed fail-closed recovery rule;
- unresolved issues;
- final branch, full SHA, and clean status;
- explicit confirmation that no COM port, Pi/network service, ZMQ, calibration, teleoperation, camera, or physical hardware was accessed and nothing was pushed.
