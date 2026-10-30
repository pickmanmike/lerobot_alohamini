## Active AM1 working rules

Begin with [current state](docs/alohamini/current-state.md), [named lessons](docs/alohamini/decisions-and-lessons.md), [camera viewing](docs/alohamini/camera-viewing.md), the relevant [Packet16 procedure](docs/alohamini/teleop-consolidation-16.md#verification-and-ordinary-use), and the existing private .cache/am1-knowledge-reuse-18/current-work.private.md pointer. Current-state/private rows own the next action and exact component pins; detailed outcomes stay in their existing records. Apply the latest owner authority instead of reviving historical assembly unknowns or superseded operating restrictions.

The objective is useful, stable and reasonably resilient personal-use teleoperation. Durability means software continuity, preserved state and recovery. A test count, clean Stop, documentation update or merge is a checkpoint. Continue useful authorized work, using finite informative activities and qualified recovery; do not repeat unchanged workloads solely for publication or forced branch coverage.

The immediate operating target is coarse reaching and gripping. Brief automatic holds are assessed by their frequency, duration, recovery and practical effect, not a zero-hold rule or arbitrary acceptable count. Preserve original references and measured residuals; investigate error when it limits the task, makes control unpredictable or accumulates during use. Precision manipulation and exact encoder return are not current completion requirements. A one-time update handoff is separate from recurring manual refresh/reconnect/reset during ordinary operation.

Reuse the existing environment, ordinary changing-input adapter, automatic current-rest preparation, live-phase coordinator, collector and analyzer. Pass the current selected binding explicitly; an older helper default is not the current deployment pointer. Preserve working private helper versions through the existing proof/archive packet. No parallel memory system, runner family, protocol or environment rebuild.

Advance phases only after accepted intentions and acknowledged active progress. Derive targets and legal gripper direction from fresh admitted bounds. Keep measured rest, stored Goal telemetry and legal command origin distinct. Preserve original reference/endpoints/workspace/deadline across a run; never reseed away accumulated error, clamp an unsupported task or replay consumed requests. Prepared increasing LEFT approaches have demonstrated direction/pose scope, not universal accuracy.

Do not force a powered process freeze, bus fault, overload or permission bypass. Preserve calibration and private permissions.

Keep one hardware owner, fresh movement input, current local sensing and required useful viewing, actual cancellation, and newer Pause/Stop/takeover/reboot authority. Current 250ms input and 500ms sensing/view guards remain. Application policy may be refined where authorized and supported; preserve actual device protections and the accepted temperature-anomaly policy. Temporary LEFT P32/I0/D32/Acceleration254 remains the selected tested condition; restore captured baseline settings after verified OFF. Broader gain, calibration, travel, replacement or disassembly work needs concrete justification and applicable authority.

Ambiguous Start uses its existing operation/body-specific handling. Unknown Grant/Input must use their own exact context, qualified current HOLD and fresh authority/input; never generalize either case into blind command replay. Current qualified state and retained historical faults have different meanings. Requested targets, accepted commands, sparse hardware Goals and measured/visible response are separate evidence.

The established five AM1 USB camera roles use the existing persistent by-path index0 map and exclusive capture owner. Reuse the enrolled viewer and process-local decoder option; do not reidentify, remap, reconnect or reenroll without a current discrepancy. Chest focus is a possible physical optical adjustment for a specific task, not an assumed software defect or routine prerequisite. Verify the actual native window is restored and the camera content pane is visible after optional-view/diagnostic actions; qualified decoding is not scene-usefulness evidence. USB-camera use is separate from owner-deferred physical USB leaders. AM1 remains wireless; P1 advisory; THETA companion work paused; cameras fixed; standing hands-clear does not need confirmation.

Use focused checks for a demonstrated boundary, review affected work and stage only stopped affected components with matched rollback/private permissions/calibration preserved. Repository commits, installed component identities and physical evidence remain distinct. Ordinary integration is authorized only into integrate/am1-local-teleop after applicable checks/review; main and AM2 remain untouched. No unchanged-component redeployment or powered repeat to match a source-equivalent merge SHA. Ask only for an indispensable physical action, missing access or materially new scope decision.

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
