## Active AM1 knowledge reuse and teleoperation (Packet18)

Read first: [current state](docs/alohamini/current-state.md) and
[source-backed decisions/lessons](docs/alohamini/decisions-and-lessons.md).
Use the matching lesson before diagnosing a symptom; check coverage at the actual
new entrypoint before reopening an accepted decision. Update changed rows, not a
packet diary. Historical runbooks retain original results and detailed procedures.

Current operation-first authority (PR25 comment6258758730 and the owner's
continuation mandate) permits bounded repeated virtual-leader bench operation,
focused PC/browser/Pi repairs, reviewed affected-component staging and ordinary
reconciliation/restart. Application ownership leases, renewal/receipt timing,
rearm/reconnection budgets and experiment criteria may be revised with a stated
purpose and affected positive/cancellation checks; earlier packet constants are
not immutable hardware requirements. Fresh movement intent remains separate from
controller/session lifetime. Obtain new fenced authority after expiry rather than
reviving an expired generation; a newer takeover, revocation, accepted Pause/Stop
or owner reboot prevails. Never replay old input or auto-resume after owner reboot.

For an explicitly selected predetermined virtual bench exercise, browser video
presentation may be advisory to execution while genuine required local inputs
remain healthy. Select and record that policy before Start; keep WebRTC measured
and recovering. It does not qualify the original strict-view benchmark or remove
useful viewing for human visual teleoperation. Declare shortened/reordered segments
before Start; later segments and ordinary next Start can be tested independently
when their own prerequisites hold. Do not aggregate segments into a full pass.
Continue supported engineering/retries after an interruption. Judge progress by
useful movement, uninterrupted intervals, active/held and recovery time, usable
viewing and ordinary reuse; cleanup/refusals support rather than complete it.

Packet18 supersedes proposed Packet17's spike-causation-first direction; Packet16
supersedes wrist-button Packet15. The owner accepted Actual73/78/79 and authorized
ordinary integration of PR25 into integrate/am1-local-teleop (comment6272465039).
PR25 is merged at e3d8a196; main and AM2 are unchanged. New executable follow-up
repairs use a focused branch/PR and are not automatically merged. Physical USB
connection remains owner-deferred; retain historic identities/calibration without
requesting connection, repeated unchanged enumeration or claiming new validation.
TEMP-01 retains isolated numeric highs/warnings and faults on confirmed high slots,
genuine device errors and invalid/stale feedback. Reuse TemperatureWindow's five
occupied 100 ms maxima and original 0.5 s age bound. Historical 55 C/cold-start40 C
is lift-specific. Packet18 authorizes follower confirmation using each frozen
installed per-motor ceiling; do not hardcode 70 C, pool IDs/buses, reset history
at phases or change EEPROM/firmware limits. Exact spike causation is not a gate.

Physical leaders are primary for useful manipulation/telepresence. Virtual input
substitutes at acquisition through the same protected Pi-owned path. Quest and
concrete household autonomy follow later. Per-motor pages remain diagnostics.
Keep role-specific settings/units, raw feedback and original times, normal-rest
acquisition, fresh input expiry and fenced new generations, run reference/deadline, sole motor
ownership and qualified recovery. Ordinary pauses are not extra recovery episodes.
Preserve actual device/current/status/voltage protections and first causes.

AM1 stays wireless; P1 advisory. Keep local required sensing and selected onboard
WebRTC, one capture owner, bounded media/log work outside motor polling. Persistent
services restore idle only; no motor replay. Durability means software continuity,
state preservation and recovery. Do not reopen network tuning, generic inspection,
manual leader/shoulder readiness questions, gains or broad commissioning.

Keep tasks stopped during edits/staging. Root alone performs Git mutations,
SSH/staging/devices; agents own explicit disjoint offline work. Verify imports
through actual service interpreter/cwd/path; preserve separate component pins,
full matched rollback, private permissions and calibration. Historical failed
runs remain failed; old-mode passes do not qualify a new interface.

Use PowerShell7 and existing uv run --no-sync --project
C:/Users/pickm/lerobot_alohamini_client. Run affected positive/fault-contrast checks
and reuse the existing runner/stager. Generic upstream setup/test commands below
are reference only for AM1 continuation: do not trigger broad installs or full
repository suites without a demonstrated need. Reread amended guidance in this
running conversation and give subagents relevant lesson IDs/files.

This file provides guidance to AI agents when working with code in this repository.

> **User-facing help → [`AGENT_GUIDE.md`](./AGENT_GUIDE.md)** (SO-101 setup, recording, picking a policy, training duration, eval — with copy-pasteable commands).

## Project Overview

LeRobot is a PyTorch-based library for real-world robotics, providing datasets, pretrained policies, and tools for training, evaluation, data collection, and robot control. It integrates with Hugging Face Hub for model/dataset sharing.

## Tech Stack

Python 3.12+ · PyTorch · Hugging Face (datasets, Hub, accelerate) · draccus (config/CLI) · Gymnasium (envs) · uv (package management)

## Development Setup

```bash
uv sync --locked                            # Base dependencies
uv sync --locked --extra test --extra dev   # Test + dev tools
uv sync --locked --extra all                # Everything
git lfs install && git lfs pull             # Test artifacts
```

## Key Commands

```bash
uv run pytest tests -svv --maxfail=10                 # All tests
DEVICE=cuda make test-end-to-end                      # All E2E tests
pre-commit run --all-files                           # Lint + format (ruff, typos, bandit, etc.)
```

## Architecture (`src/lerobot/`)

- **`scripts/`** — CLI entry points (`lerobot-train`, `lerobot-eval`, `lerobot-record`, etc.), mapped in `pyproject.toml [project.scripts]`.
- **`configs/`** — Dataclass configs parsed by draccus. `train.py` has `TrainPipelineConfig` (top-level). `policies.py` has `PreTrainedConfig` base. Polymorphism via `draccus.ChoiceRegistry` with `@register_subclass("name")` decorators.
- **`policies/`** — Each policy in its own subdir. All inherit `PreTrainedPolicy` (`nn.Module` + `HubMixin`) from `pretrained.py`. Factory with lazy imports in `factory.py`.
- **`processor/`** — Data transformation pipeline. `ProcessorStep` base with registry. `DataProcessorPipeline` / `PolicyProcessorPipeline` chain steps.
- **`datasets/`** — `LeRobotDataset` (episode-aware sampling + video decoding) and `LeRobotDatasetMetadata`.
- **`envs/`** — `EnvConfig` base in `configs.py`, factory in `factory.py`. Each env subclass defines `gym_kwargs` and `create_envs()`.
- **`robots/`, `motors/`, `cameras/`, `teleoperators/`** — Hardware abstraction layers.
- **`types.py`** and **`configs/types.py`** — Core type aliases and feature type definitions.

## Repository Structure (outside `src/`)

- **`tests/`** — Pytest suite organized by module. Fixtures in `tests/fixtures/`, mocks in `tests/mocks/`. Hardware tests use skip decorators from `tests/utils.py`. E2E tests via `Makefile` write to `tests/outputs/`.
- **`.github/workflows/`** — CI: `quality.yml` (pre-commit), `fast_tests.yml` (base deps, every PR), `full_tests.yml` (all extras + E2E + GPU, post-approval), `latest_deps_tests.yml` (daily lockfile upgrade), `security.yml` (TruffleHog), `release.yml` (PyPI publish on tags).
- **`docs/source/`** — HF documentation (`.mdx` files). Per-policy READMEs, hardware guides, tutorials. Built separately via `docs-requirements.txt` and CI workflows.
- **`examples/`** — End-user tutorials and scripts organized by use case (dataset creation, training, hardware setup).
- **`docker/`** — Dockerfiles for user (`Dockerfile.user`) and CI (`Dockerfile.internal`).
- **`benchmarks/`** — Performance benchmarking scripts.
- **Root files**: `pyproject.toml` (single source of truth for deps, build, tool config), `Makefile` (E2E test targets), `uv.lock`, `CONTRIBUTING.md` & `README.md` (general information).

## Notes

- **Mypy is gradual**: strict only for `lerobot.envs`, `lerobot.configs`, `lerobot.optim`, `lerobot.model`, `lerobot.cameras`, `lerobot.motors`, `lerobot.transport`. Add type annotations when modifying these modules.
- **Optional dependencies**: many policies, envs, and robots are behind extras (e.g., `lerobot[aloha]`). New imports for optional packages must be guarded or lazy. See `pyproject.toml [project.optional-dependencies]`.
- **Video decoding**: datasets can store observations as video files. `LeRobotDataset` handles frame extraction, but tests need ffmpeg installed.
- **Prioritize use of `uv run`** to execute Python commands (not raw `python` or `pip`).
