## Current AM1 motion quality and durability scope

For the explicitly invoked AM1-MOTION-QUALITY-01 continuation, retain the accepted
bounded physical continuity milestones at their recorded sources. Durability means
software fault tolerance, connection and control continuity, state preservation,
and recovery with minimal human intervention; it does not mean physical wear or
mechanical lifespan. Assess these dimensions separately from useful joint response.
No hardware-endurance campaign or unchanged full-workload rerun is included.

Begin with left follower elbow ID3 using existing finite virtual control, the sole
protected motor owner and real local-camera-required observation; AM1 stays wireless
and P1 is advisory. Resolve the demonstrated target/encoding/write/readback/response
layer with a short selected-joint comparison: one frozen reference, other eleven
joint targets held, body zero, original 3/.5/3/.5-second timing, at most 30 trajectory
and 60 Live seconds. A separately labelled dwell comparison may use holds up to two
seconds without increasing excursion, force, rate, gains or current limits.

Use existing native samples and only missing sparse same-owner register evidence.
Keep program completion, original return-envelope qualification and actual repeated
outward/return motion as separate verdicts. Preserve prior failures, calibration,
private permissions, ownership, protections, component-specific deployments and the
unmerged PR20 -> PR19 -> PR18 -> PR17 -> PR16 stack. Root alone operates hardware.
Reuse the existing staging, operation and analysis tools; stage only reviewed stopped
affected owners. Leave a qualified candidate available idle through the existing
opt-in route, with simulation default and matched rollback accessible. No merge,
boot autoplay, network tuning, manual leader/shoulder action, AM2 changes, speculative
motor tuning or additional architecture is authorized by this packet. Actual rewiring
or mechanical adjustment requires power off and one specific human action only when
the observed limitation requires it.

## Current AM1 lift startup and protected physical scope

For the explicitly invoked AM1-LIFT-STARTUP-01 continuation, use the existing
Pi-owned protected physical backend, finite virtual/scripted arm input, normal
owned body controls and real local-camera-required observation. Simulation remains
the default; live source and actual evidence must be explicit. AM1 remains wireless
and P1 recording/viewing remains advisory. The owner authorizes bounded in-scope
runs, supported repairs and qualified recovery without attendance, readiness,
manual leader or shoulder-placement questions. No Ethernet prerequisite, Wi-Fi
tuning, optional-media work, architecture replacement, powered freeze, bus fault,
overload, EEPROM/gain workaround or permission bypass is included.

Preserve calibration/mappings, normal-rest acquisition, source identity, exclusive
host/bus ownership, private permissions, exact separate component pins, all local
motion/current/thermal/status/travel/transport protections and ordinary Pause/Stop.
Record the post-home readiness branch and retain its original one-second budget,
fresh actual samples and original strict five-sample/0.2-second window. The AM1
bounded alternative additionally requires the one-count whole-phase encoder
envelope and rejects persistent motion; that whole-phase eligibility bound does
not apply to an otherwise qualified original strict window. Cleanup-only thermal
collection keeps genuine slot peaks and original stopped qualification bounds;
Live rules remain unchanged.

Retain faults/first causes/original receipts. Unknown physical cleanup blocks rearm
and ordinary stopped staging/rollback. Separate exact-run native read-only proof
and regular product Reconcile acknowledgment may qualify actual stopped state
under both locks with actual source/calibration/no-owner evidence and durable
archives/ACK. They add separate reconciliation and clear uncertainty, never rewrite
failed status, original unknown cleanup, reference/progress or deadline. Repair the
demonstrated layer, test/review, stage stopped affected owners, then continue a
supported correction or planned comparable confirmation. Recovery permits at most
three bounded episodes in the original deadline; completed qualified executor-
acknowledged recovery closes its episode, allowing a distinct later one. Reattachment
cannot home, reseed or renew time. Judge usefulness from actual motion/feedback,
not timers/counters. Preserve failed P1/303/d589 results, accepted simulation and
the unmerged dependent PR stack. This scope applies only to this authorized packet.

## Current AM1 mobile local observation scope

For the explicitly invoked AM1-MOBILE-LOCAL-01 continuation, AM1 remains wireless.
Use an explicitly selected, immutable local-camera-required sensing policy with
simulated motor IO; P1 recording/viewing is temporary advisory engineering evidence.
Preserve the separate strict P1-required diagnostic, its original 500 ms rules and
failed results. Never downgrade an active run or fill missing local evidence with
synthetic/cached proof. Local freshness uses original completed upstream arrival
on Pi, not sensor exposure, and must expire at 500 ms through actual consumption.
Reuse the existing exclusive camera-only owner/config/roles; decode outside the
Unix-only task owner. No motor buses, live backend, calibration change, Wi-Fi
experiment, Ethernet prerequisite or manual leader/shoulder action is authorized.
Preserve ownership, protections, independent supervisors, exact separate component
pins, private permissions, trust/enrollment, historical physical results and the
unmerged dependent PR stack. Required-local loss holds/freezes and recovers only
within the existing episode/admission/seed/deadline limits; optional P1/browser
loss cannot spend recovery episodes or interrupt a healthy local task.

## Current AM1 virtual reliability scope

For the explicitly invoked AM1-RELIABILITY-03 workstream, use finite virtual/scripted
arm input, automated normal owned body controls, and the existing P1 observer.
The owner authorizes ordinary in-scope automated runs, supported repairs and
qualified same-session recovery without attendance, readiness or manual-leader
questions. Moving physical leaders and independent cutoff development are outside
this packet. Preserve current local motor/feedback/thermal/status protections,
ownership, exact separate pins, private permissions, calibration and mappings.
No forced powered process freeze, bus fault, overload or permission bypass.
After a fault, retain its first cause, repair the demonstrated layer, test, stage
only stopped affected owners, and rerun a supported hypothesis/correction.
Preserve historical results and normal manual-use guidance. This section changes
only this explicitly invoked workstream, not global agent or platform permissions.
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
