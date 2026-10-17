## Active AM1 teleoperation consolidation (Packet16)

Goal: dependable Pi-owned teleoperation for light manipulation and telepresence.
Physical C001 leaders are primary. Explicit virtual-leaders substitute only at
acquisition and use the same mapping, authentication, ownership and native expiry.
Generated input is not a human demonstration. Retain accepted coarse elbow page
presets as diagnostics; fine positioning and household tasks remain unproven.

Packet16 replaces proposed wrist Packet15. PR24 integrated normally into
integrate/am1-local-teleop at386d3c37; this focused follow-up remains unmerged.
Main/AM2 are unchanged. Complete the representative172.5 active-second program
under its original fixed300s native ceiling, selected onboard WebRTC with one view
switch, controlled input interruption/qualified rejoin, then brief next Start.
Do not credit idle padding or interactive Stop as finite-program completion.

Preserve original raw feedback/acquisition times, calibrated units/directions,
latest-valid coherent frames, accumulated rate-limited targets, original250ms
input expiry and generations, fixed run reference/deadline and sole motor owner.
Qualified executor-acknowledged recovery closes its episode; later distinct loss
may receive another bounded episode, at most three total without deadline reset.
Acquire actual supported rest, never require an old twelve-joint fingerprint.
Keep installed settings role-specific; no blanket P16/I0/D32,500mA or elbow windows.
Wheel velocity, lift home/relief and gripper contact retain their own semantics.
Preserve real health/current/thermal/status/voltage protections and first causes.
No process freeze, bus fault, overload, speculative tuning or permission bypass.

One exclusive onboard capture owner serves selected primary WebRTC. JPEG remains
required local observation/diagnostic evidence. Decoded progress/source age and
camera generations remain distinct from control health. Keep media/routine logs
outside motor polling; optional output cannot consume unrelated recovery budgets.

Installed motor 6328bb524bc4e42c70f9896d66904177c1830277,
runtime 68118e4e9dddf8dddaa3ea6cbf64f54fdd511980,
camera bc3442b54d9c574a8feddb0ade642c9b351e59f9,
manifest de681ad6f73f2639a1971b96eee41214b5edfdc1730082e7f77b78a3c6795dbb.
Helper/manual 0fe3997f and original calibration/private trust remain unchanged.
Full matched rollback: motor2fbc8265b15393dcc799e603223b8902844e2919,
same runtime/camera, manifest1d64de8e1e2f05938a39fae7a67a9a3ff3b19f94ae456f311116c41e52bbbd57.
Stage10 verified actual owner runtime/src/client9315af5b import and nine native
request-accounting cases. Original attempts01..14 remain retained by source version.
Combined14 reached17.34 active seconds/180 Core frames; two ordinary expiry rejoins
qualified, then valid shoulder-lift temperature74 versus installed70 stopped the
host. The prior same-binding read34 about59ms earlier does not establish heat or
reply origin. Known20 off/zero cleanup and all original/full-history evidence remain.
The full172.5s program, planned gap/view switch and next Start remain unfinished.

A prospective live-only health acquisition change uses one exact33..70 SDK block
per six-motor arm instead of six one-byte transactions. It preserves original
acquisition intervals, all health limits and first causes; valid74 still faults.
The stopped read-only comparison verified20 transactions/120 packets and supported
field offsets with unchanged history/private state. Its cause relationship to
Combined14 is unproved. The source passed160 affected tests and focused review;
all ten motor-only stage13 phases passed with no task started. Runtime/camera remain unchanged; the supported combined trial is next.
Ordinary expiry pauses retain cause/current-feedback/ACK/expiry/deadline checks;
no separate PC pause quota is imposed. The genuine native three-episode recovery
budget remains. The PC-only correction passed67 focused adapter tests.

Tasks stay stopped for source edits and affected-component staging. Verify actual
owner imports, preserve separate full matched rollback bundles, calibration and
private permissions. Persistent services restore idle only, never motor replay.
AM1 wireless; P1 advisory; AM2 untouched. No network tuning, Ethernet prerequisite,
manual leader/shoulder readiness questions, new cutoff hardware or architecture.
Root alone performs Git mutations, SSH, staging and devices. Agents own explicit
disjoint offline edits/tests. Use PowerShell7 and existing uv run --no-sync
--project C:/Users/pickm/lerobot_alohamini_client environment.

Durability means software fault tolerance, control/connection continuity, state
preservation and recovery, not physical wear. Current evidence/commands and older
failure/source-version pointers: docs/alohamini/teleop-consolidation-16.md.
Historical packet restrictions stay historical. Preserve legacy manual rollback;
never run it alongside the persistent motor owner.

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
