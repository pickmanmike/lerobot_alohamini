## Active AM1 teleoperation consolidation (Packet16)

Goal: dependable Pi-owned whole-robot teleoperation for light manipulation and
telepresence. Physical C001 leader arms are the primary input. The explicitly
selected virtual-leaders source substitutes at acquisition and exercises the same
mapping, authenticated serialization, ownership and native expiry as usb-leaders.
Generated input is not a human demonstration. Per-motor page presets remain
engineering diagnostics; retain their accepted coarse elbow behavior and limits.

The owner invoked AM1-TELEOP-CONSOLIDATION-16, replacing proposed wrist Packet15.
It authorizes implementation, focused checks/review, component staging, a roughly
three-minute automated virtual-leader workload with a new fixed five-minute
native ceiling, then a brief next Start. Existing diagnostic profiles retain their
original ceilings and results. Packet16 supersedes older elbow-only input scope,
manual-leader prerequisites and no-merge restrictions for PR24 into the named
integration target only. PR24 merged normally at 386d3c37. Main is unchanged;
one focused follow-up from that actual integration tip remains unmerged.

Preserve original raw feedback and acquisition times, actual per-joint units and
calibration, bounded latest-valid frames, accumulated rate-limited targets, original
input expiry/generations, single motor ownership and qualified clutch/reacquisition.
Do not blanket-apply P16/I0/D32, 500 mA, elbow windows or position heuristics to other
roles. Keep wheel velocity, lift homing/relief and gripper contact semantics.
Retain actual health/current/thermal/status/voltage protections and first causes;
no process freeze, bus fault, overload, permission bypass or speculative tuning.

Preserve one onboard capture owner and existing auth/TLS; make selected primary
viewing use the existing supported low-latency streaming capability. JPEG remains
valid required local observation/diagnostic evidence. Separate decoded view progress
from capture freshness and motor health; optional loss cannot consume unrelated
recovery budgets. Keep media work and routine logs outside the motor loop.

Current known stopped deployment: motor 168915d04a9345e5b064a3d23142999f5c96b8e1,
protected runtime 7447480924c288fed3a76fc981433b41c7bfcefe, camera
bc3442b54d9c574a8feddb0ade642c9b351e59f9; manifest
01d2bed7c629d3416354ab31556ae998a270b9220bb09c31d8b30369332982df.
Helper/manual remains 0fe3997f. Immediate matched rollback retains motor
a7d17886de711f42618f581b3d226ff991cd4ed7 with the same runtime/camera and manifest
13012258179ddc6c2a8c28d4e50c1e48f886eeb1329ca404191e3336e498957d.
The AM1-only group-reply integrity repair and sticky prewrite fault fence passed
119 focused tests and two independent reviews; all ten affected-motor stage09
phases and persistent available-idle checks passed without starting hardware.
Combined08's decoded shoulder-pan temperature126 versus installed70 remains
unresolved actual wire provenance; Combined07's numeric trigger is still unknown.
A stopped comparison retained360valid replies with values matching their own
payloads, without reproducing the active126 reading. Preserve these distinctions.
Combined09 reached post-home readiness but never admitted native Live: delivered
feedback remained at sequence2 while the host continued normal acquisition.
The original180s startup deadline ended it with physical_startup_timeout; original
six artifacts and full native history are retained, cleanup physical_readback_verified,
all20 off/zero readbacks, qualified lift, closed buses and clean camera release.
The representative workload and next Start remain unfinished. A deterministic
one-reply-per78ms test reproduced credit starvation (only2 advances/120polls).
The reviewed AM1 client correction retained80advances with no premature retirement;
110 focused connection/cadence tests pass. Retain credits until ORIGINAL request
age1s, rather than retiring each40ms empty poll; never refresh timestamps. Three
credits,40ms polls, original1s feedback and250ms input freshness remain unchanged.
All ten stage09 idle-only phases passed, but Combined10 exposed import ownership:
the Pi owner loads alohamini_client.py from its runtime checkout, whose original
SHA235f7a8a still has old polling; the reviewed9315af5b copy was staged only in the
motor checkout. Combined10 stayed preLive at observation3 and ended at the original
180s startup timeout, with all originals saved and verified cleanup. Correct only
the actual runtime copy using the same reviewed9315af5b source, preserve the current
motor/camera pins, and verify actual owner-environment module path/hash before Start.
Do not claim production validation of a source that its owner did not import.
Preserve calibration, trust, private permissions, source imports and separate full
component rollback bundles.
Tasks stay stopped during editing/staging; services restore idle without replay.
AM1 stays wireless, P1 advisory, AM2 untouched. No network tuning, Ethernet,
manual shoulder/leader readiness questionnaire, new cutoff hardware or architecture.
Root alone performs Git mutations, SSH, staging and hardware actions; agents use
explicit disjoint source ownership and offline tests. Use PowerShell 7 and existing
uv run --no-sync --project C:/Users/pickm/lerobot_alohamini_client environment.

Durability means software fault tolerance, connection/control continuity, state
preservation and recovery, not motor wear. Historical restrictions/results remain
at their recorded source versions in docs/alohamini/practical-controls-14.md,
practical-motion-13.md, response-12.md and earlier reports; old packet-specific
rules are not current authority for Packet16. Legacy manual operation is retained
for rollback and must never compete with the persistent owner.

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
