# Aloha Mini 1 Control Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a camera-first, Windows-local browser console for one supervised AM1 Local session, with native leaders, safe body input, and truthful supporting data.

**Architecture:** Improve the existing Pi camera page first and reuse its presentation assets through a fixed-route Windows loopback proxy. A small standard-library Windows server adapts the existing `SessionCoordinator`; a private authenticated Windows named pipe carries typed state and input between that controller and the sole native leader/action client. The Pi remains the sole motor-bus owner, and the existing CLI remains usable.

**Tech Stack:** Python 3.12 standard library (`ThreadingHTTPServer`, `http.client`, `multiprocessing.connection`/`AF_PIPE`), existing PowerShell 7, vanilla JavaScript/CSS, Node `node:test`, existing pytest and Python environments; no new package.

**Spec:** `docs/superpowers/specs/2026-09-29-am1-control-console-design.md`

## Global Constraints

- AM1 only; no AM2/AM2 Pro, dock, camera backend/capture tuning, calibration, motor-setting, network, or arbitrary-shell change. Initial browser bind is `127.0.0.1`; no public listener.
- Preserve the 30-second nominal startup sync, 1–1800-second live duration, existing arm alignment/freshness checks, 10 Hz Local sender, 250 ms body expiry, one-second Pi watchdog, and genuine-fault cleanup. Never auto-rearm a terminated host.
- Preserve distinct deployed lineages: integration `5d45ace56e01b813b9d4d77f803672f8fe0744bb`, recorded motor `43d1622a9395cdc1d1f9acce1090ed3f029f4f7c`, helper/client `0c4f2e7ccce3ddcce6d75e7113ed819d07f1d192`, camera `047c4fcf7cbf34684a9b8c348193585938975815`. Verify clean stopped checkouts and exact private pins before any deployment; do not overwrite a differing checkout.
- Camera age means time since the camera gateway received the JPEG, **not** physical scene-to-display latency. A retained frame never becomes Live because a status response arrived.
- No images, logs, camera maps, credentials, calibration caches, or other private artifacts in Git. One camera-only check and one short attended integrated check suffice; no repeat of commissioning.

## Review Focus

1. A source restart resets frame sequence while an old request is in flight: reject the old response, retain the correct role's image, and accept the new generation (Task 1 test).
2. Double Start, browser refresh, or another tab: attach to the existing session without a second host/client or a silent movement-lease takeover (Task 4 test).
3. Blur, held-button loss, navigation, pipe loss, or typing: zero body input within 250 ms and enter measured-arm pause/hold before any explicit Resume (Task 5 test).
4. Stop during any startup gate or while a camera/log consumer stalls: signal the existing cooperative cleanup promptly; report cleanup uncertainty rather than Stopped (Tasks 3–4 tests).
5. A field absent, old, or from a different bus/role: display Not sampled/Snapshot with acquisition time and provenance, never a fabricated live zero or fault (Task 7 test).

---

## Source and deployment map

| Unit | Exact existing hook | Increment / deployment |
|---|---|---|
| Camera UI | `tools/am1_camera/{index.html,style.css,app.js,freshness.js,mjpeg.js}`; `tests/cameras/test_am1_camera_ui.cjs` | A on this design branch; cherry-pick only its camera-UI commits onto a new branch at camera `047c4fcf`. The UI blobs other than `index.html` match integration; reconcile that one HTML wording difference, never replace deployed `tools/am1_camera_viewer.py`. Stage only the stopped Pi camera checkout and back up/change only `remote_camera_head`. |
| Camera gateway | `tools/am1_camera_viewer.py`: `/status.json`, fixed-role JPEG/MJPEG, `X-Frame-Sequence`, `X-Frame-Age-Ms`, Basic auth | A leaves Python gateway, native MJPG, map and rotations unchanged. Browser maintains a per-role connection/source generation in addition to gateway sequence. |
| Session lifecycle | `tools/am1_session.py`: `run_start` → `_run_start_locked` → `SessionCoordinator.run`, `SSHRemote`, `WindowsClient.run`, `request_stop`, `collect_only`; `tools/am1_session_remote.py` emits `camera_ready` and `host_ready` | B adds injectable typed gates/events, not a second supervisor. The existing `LocalSessionLock`, `active.json`, exact-SHA checks, stop file and result folder remain authoritative. |
| Native control | `examples/alohamini/teleoperate_bi.py`: `run_teleoperation`, `run_startup_sync`, `run_alignment_gate`, `_run_am1_recovering_local_sender`, `run_am1_live_sender`, `make_local_body_action`; `tools/run_am1.ps1` | B adds an opt-in UI input adapter. Physical `BiSOLeader` and the sole `AM1LiveActionSender` remain owners; CLI still uses `KeyboardTeleop`/Enter. Pi motor code is unchanged for A and B. |
| Existing tests | `tests/robots/{test_am1_unified_session.py,test_alohamini_local_recovery.py,test_alohamini_local_teleop.py}` plus camera tests | Extend only focused suites and new console tests; no dependency installation. |

Windows has Python 3.12.10, PowerShell 7, Node, pytest and pyzmq in the existing environment; FastAPI/Flask/aiohttp/websockets/psutil are absent. The current shell cannot resolve `am1-pi`, so the recorded Pi source/environment identities are **not** a fresh deployment check. At A/B staging, use the existing configured SSH route or Pi Connect for a single read-only head/clean/process preflight; lack of that check blocks deployment, not offline implementation. Do not begin a network investigation. The ignored session config remains private; locally provision a user-only camera Basic-auth file for the Windows proxy, without echoing its contents or changing the Pi credential.

## Increment A — independently useful camera workspace

### Task 1: Retain correct frames through failure and reconnect

**Files:** Modify `tools/am1_camera/{freshness.js,app.js}`; test `tests/cameras/test_am1_camera_ui.cjs`. Preserve `mjpeg.js`'s bounded latest-only parser and native JPEG bytes.

**Interfaces:** `AM1RetainedFrames.accept(role, generation, sequence, objectUrl, ageMsAtReceipt, receivedAt) -> boolean`, `get(role, now) -> {url, age_ms, state} | null`, and `release(role)` in `freshness.js`; `app.js` owns a per-role generation and abort/request token. A newest-accepted status poll showing a sequence regression advances that role's generation and invalidates older in-flight requests. At most one retained object URL plus one pending decode per role; revoke replaced/rejected URLs. Status never resets `receivedAt`.

- [ ] Add `test_role_cache_survives_disconnect_and_switch`, `test_generation_discards_old_inflight_reply`, `test_status_does_not_renew_frame_age`, and `test_object_urls_stay_bounded`: assert the held role/URL and advancing age, reject the prior epoch after reset, and cap retained URLs at five. Include decode failure/rotation cases. Run `node --test tests/cameras/test_am1_camera_ui.cjs` and observe a relevant RED.
- [ ] Change `app.js` to retain each role's last decoded image on stream cancellation/status loss, reject cross-role/old-generation frames and bump generation on reconnect or confirmed sequence reset. Keep old frames visibly labeled `Last frame / waiting`; a camera with no valid image gets a placeholder.
- [ ] Run the same Node file GREEN; inspect the full task diff and `git diff --check`; commit only this tested camera state change.

### Task 2: Ship the actual camera page

**Files:** Modify `tools/am1_camera/{index.html,style.css,app.js}` and `tests/cameras/test_am1_camera_ui.cjs`; update `docs/alohamini/camera-viewing.md` only for changed UI behavior.

**Interfaces:** `app.js` mounts one reusable workspace into `#am1-camera-root` using its element's `data-camera-base` (`/` on the Pi viewer, `/camera/` on the Windows proxy); all status/snapshot/stream URLs use that base. Both pages serve the same assets without a fork. Stable anatomical slots and Front focus default; role switch promotes the clicked role without moving the other slots; per-role Details remains below the image.

- [ ] Add `test_anatomical_slots_and_focus`, `test_details_does_not_promote`, and `test_camera_base_and_rotations`: assert fixed role slots, wrist-left 90°/wrist-right 270° from source status, Details below image, keyboard activation, `/` versus `/camera/` URLs and no nested interactive controls. Run `node --test tests/cameras/test_am1_camera_ui.cjs` RED.
- [ ] Implement the layout and accurate Live/Waiting/Disconnected/Last-frame labeling; keep details off the image, no fake Control actions in the camera-only page.
- [ ] Run `node --test tests/cameras/test_am1_camera_ui.cjs`, `C:\Users\pickm\lerobot_alohamini_client\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests/cameras/test_am1_camera_viewer.py tests/cameras/test_am1_camera_rotations.py -q`, and `git diff --check`; commit the camera UI/docs only.
- [ ] Review/cherry-pick those camera-only commits onto a branch from exact `047c4fcf`; verify `git diff --name-only 047c4fcf..HEAD` contains no gateway Python/private file. On the clean stopped Pi camera checkout, back up the ignored pin and stage that exact head. Perform **one** camera-only start/view/stop with generated-frame tests already green; capture a screenshot showing retained-frame state if a source is unavailable. Roll back by stopping the viewer, restoring the backed-up camera pin and previous clean camera branch. No motor/leader access.

## Increment B — unified session and input

### Task 3: Long-lived, bounded Windows console shell

**Files:** Create `tools/am1_console.py`, `tools/am1_console_ui/{index.html,app.js,style.css}`, `tests/robots/test_am1_console.py`; modify `tools/am1_session.py` only for optional `SessionConfig.console_camera_auth_file: Path | None`. Reuse Task 2's camera assets verbatim rather than fork the frame logic.

**Interfaces:** `ConsoleServer(config: SessionConfig, camera_auth_file: Path, session_adapter)` serves `127.0.0.1:8765`, `/`, fixed `/camera/{status.json,api/frame.jpeg,api/stream.mjpeg,assets}`, `/api/state`, `/api/events` (bounded server-sent events), and typed JSON `POST /api/operation`/`/api/body`. Fixed-role proxy only; Basic credentials held server-side. Exact Host and Origin, same-site session cookie, process CSRF token, JSON body cap and no CORS. Separate bounded camera/SSE slots preserve a reserved Stop/API path.

- [ ] Add `test_open_is_read_only`, `test_proxy_refuses_unlisted_target_and_never_exposes_credentials`, `test_foreign_origin_and_oversized_post_refused`, and `test_stalled_stream_cannot_starve_stop`: assert no session call, 403/404 for forbidden input, no secret in response/log, and prompt Stop acceptance. Browser refresh must return existing state. Observe RED with `C:\Users\pickm\lerobot_alohamini_client\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests/robots/test_am1_console.py -q`.
- [ ] Implement the stdlib shell/proxy/event ring with bounded queues and drop markers. Keep the page and last result available before/after a session; never infer teleoperation readiness from console or camera availability.
- [ ] Run that test file GREEN, `python -m py_compile` using the existing interpreter on changed Python, and `git diff --check`; commit.

### Task 4: One typed lifecycle, not pre-fed Enter

**Files:** Modify `tools/am1_session.py`; extend `tests/robots/{test_am1_console.py,test_am1_unified_session.py}`.

**Interfaces:** Add optional `gate(stage: str, evidence: dict, cancel: Callable[[], bool]) -> bool`, `emit(event: dict) -> None`, and `on_session_created(session_id: str) -> None` to `run_start`/`_run_start_locked`/`SessionCoordinator` where relevant; default `None` retains exact CLI prompts. `ConsoleSessionAdapter.start(duration, leader_source, motion_profile) -> session_id` runs the existing lock/coordinator on one worker and receives the newly written active identity through `on_session_created`. Typed events come from actual remote `camera_ready`/`host_ready`, client sync/alignment/admission, pause/recovery, cleanup and exact log collection—not scraped banner prose. `stop(session_id) -> accepted` writes the existing cooperative stop request immediately; completion remains a later event.

- [ ] Add `test_duplicate_start_is_same_session`, `test_real_ready_events_advance_without_stdin`, `test_stop_at_each_gate_preserves_fault`, and `test_unknown_cleanup_not_stopped`: assert one host/client, exact active identity, wrong session/epoch refusal, no blank stdin, no stage skipped, prompt stop-file signal and original failure. Run `C:\Users\pickm\lerobot_alohamini_client\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests/robots/test_am1_console.py tests/robots/test_am1_unified_session.py -q` RED.
- [ ] Implement the optional gate/event adapter. The prepared Start authorizes normal progression only after each real gate qualifies; exceptional realignment or manual recovery remains a distinct on-page approval. Keep the original `request_stop`/`collect_only` CLI fallback.
- [ ] Run the two files GREEN, compile and diff checks; commit.

### Task 5: Single native leader/body owner and explicit pause/resume

**Files:** Create `examples/alohamini/am1_console_bridge.py`; modify `examples/alohamini/teleoperate_bi.py`, `tools/run_am1.ps1`, `tools/am1_session.py`, `tools/am1_console_ui/{app.js,style.css}`; extend `tests/robots/{test_alohamini_local_teleop.py,test_alohamini_local_recovery.py,test_am1_unified_session.py,test_am1_console.py}` and add browser input tests `tests/cameras/test_am1_console_ui.cjs`.

**Interfaces:** UI-only `--console_pipe`/private auth-file option is passed by `WindowsClient.run` through `run_am1.ps1`. The console creates one per-session authenticated Windows `AF_PIPE` listener and a random user-only auth file under the existing private local-state directory, removed after verified cleanup; `AM1ConsoleBridge` connects from the native client and handles `{session_id, epoch, seq, kind, payload}` on one bounded IO worker, never on the action-sender thread. No new public port or motor writer. `body_keys(now) -> set[str]` returns only allowed configured W/S/Z/X/A/D/U/J/T/G keys received within 250 ms, otherwise empty. `wait_gate(stage, evidence, cancel)` replaces **only** UI-mode Enter waits; CLI `input_fn` remains. `pause_requested()` feeds `_run_am1_recovering_local_sender`'s existing `sender.request_pause`; a UI-commanded pause or lost control connection needs explicit Resume followed by existing fresh follower/leader, body-release, alignment and host-ack qualification. Preserve the already-tested automatic recovery for a brief observation gap when control ownership was not lost.

- [ ] Add `test_ui_mode_has_one_native_input_owner`, `test_input_loss_zeros_and_pauses`, `test_stale_epoch_never_replays`, and `test_typed_resume_requires_fresh_ack`, plus browser focus/button tests: assert no `KeyboardTeleop`, real leader reads, zero base/lift by 250 ms, no nonzero sync/pause command, measured-arm hold, and Stop from any route (Q only on Control). Run `C:\Users\pickm\lerobot_alohamini_client\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests/robots/test_alohamini_local_teleop.py tests/robots/test_alohamini_local_recovery.py tests/robots/test_am1_unified_session.py tests/robots/test_am1_console.py -q` and `node --test tests/cameras/test_am1_console_ui.cjs` RED.
- [ ] Wire the bridge and typed sync/live/recovery gates at `run_teleoperation`, `run_startup_sync`, `run_alignment_gate` and `_run_am1_recovering_local_sender`; normal prepared stages auto-advance only after their existing data checks, while moved-leader/paused recovery requires a fresh explicit operation. Preserve the first validated action, native 10 Hz pacing, no catch-up burst, CLI behavior and scripted zero-body mode.
- [ ] Run the four affected Python files and `node --test tests/cameras/test_am1_console_ui.cjs` GREEN; run `teleoperate_bi.py --help`, `run_am1.ps1` PowerShell parser, compile and `git diff --check`; commit. Prove AM2/AM2 Pro parser/use paths unchanged and no Pi motor/schema diff.

### Task 6: Launch, stage and validate the integrated Control page

**Files:** Create `tools/run_am1_console.ps1`; modify `docs/alohamini/unified-session.md`; extend `tests/robots/test_am1_console.py` for launcher/shortcut paths. Generate the desktop shortcut outside Git at deployment only; it opens the console and browser, never a session.

- [ ] Add `test_console_launcher_is_read_only_until_start`: assert existing Python/config/import roots, one console process, fail-closed port collision, no camera/host launch on open, and working CLI fallback; run `C:\Users\pickm\lerobot_alohamini_client\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests/robots/test_am1_console.py -q` RED.
- [ ] Implement the launcher and concise runbook: Start/Pause/Resume/Stop, source identities, body input scope, Q/Stop fallback, exact result-folder and collection-only path, required-view loss procedure and retained tracking/camera/endurance limits.
- [ ] Run affected Task 3–5 tests, Node browser tests, Python compile/help, PowerShell parse, `git diff --check`, complete range-diff and private-artifact scan GREEN; commit and review the draft implementation PR. Stage only clean stopped Windows and Pi session-helper checkouts at the same reviewed helper SHA; back up/change `remote_session_head`. Keep motor at its verified operational head and camera at Task 2's camera head.
- [ ] With the owner attending, run **one short** normal physical-leader Local session through the console: camera-ready → home/relief → 30-second sync → live arm + brief body press/release → Pause/explicit Resume → Stop/Q and exact-log collection. Any genuine fault ends that attempt. Verify no duplicate owners, body zero on release, host hold/ack, cleanup and source headers. Roll back to prior backed-up helper/client pins and existing `run_am1_session.ps1` if needed; do not repeat subsystem commissioning.

## Increment C — supporting pages from bounded, sourced data

### Task 7: One provenance-aware snapshot contract

**Files:** Create `tools/am1_console_model.py`, `tests/robots/test_am1_console_telemetry.py`; modify `tools/am1_console.py`, `tools/am1_session_remote.py`, and optional event hooks in `tools/am1_session.py`/`examples/alohamini/teleoperate_bi.py`. No Pi motor change or new servo read is planned.

**Interfaces:** `field(value, unit, source, acquired_at, state)`, with state `Live | Snapshot | Not sampled | Unavailable`; `ConsoleSnapshot.update(event)` retains only latest bounded data. Sources: follower normalized arm positions, body velocity and lift height from existing `AlohaMiniClient.get_observation`; requested arm/body targets from the already-sent native action; observation ID/age, host state/epoch and client cadence from existing client callbacks; current/temperature/status **only** if a correctly identified existing host/lift record supplies them (otherwise Not sampled); camera health from `/status.json`; Pi CPU/memory/thermal/uptime/storage/throttling from a bounded ~1 Hz read-only sampler in the existing Pi supervisor while active (`vcgencmd` only if present); process/source/cleanup from `SessionOutcome` and remote events. Off-session Pi data is a timestamped Snapshot or Unavailable. Leader cards exist only while connected. Raw counts/degrees are not inferred from normalized values; no battery percentage.

- [ ] Add `test_servo_identity_and_units`, `test_missing_field_is_not_sampled`, `test_acquisition_age_survives_delivery_delay`, and `test_snapshot_memory_bounded`: assert distinct left/right bus labels, normalized versus raw/lift units, `None` rather than zero for missing data, stable original acquisition time, and isolated numeric warning versus confirmed fault. Run `C:\Users\pickm\lerobot_alohamini_client\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests/robots/test_am1_console_telemetry.py -q` RED.
- [ ] Implement cached snapshots/events without per-panel polling or a second serial reader. UI display may refresh cached servo values at 5–10 Hz and system at ~1 Hz; opening Details never triggers bus IO. A slow subscriber drops UI data, not motor actions or Stop.
- [ ] Run telemetry plus affected session tests GREEN, compile and diff checks; commit. If a truly necessary servo field lacks a reliable existing source, show Not sampled and document the specific same-owner future read rather than silently add one. Stage the clean stopped Pi session-helper checkout at the reviewed C head and back up/change only `remote_session_head`; the system sampler uses bounded read-only OS calls and never joins the motor loop.

### Task 8: Finish Servos, System, Logs and read-only Terminal

**Files:** Modify `tools/am1_console_ui/{index.html,app.js,style.css}`, `tests/cameras/test_am1_console_ui.cjs`, `docs/alohamini/unified-session.md`; extend `tests/robots/test_am1_console.py` for bounded log/export routes.

- [ ] Add `test_servo_details_show_provenance`, `test_routes_preserve_session_but_clear_keys`, `test_logs_are_bounded_and_export_exact_files`, and `test_terminal_has_no_command_route`: assert separate leader/follower identities, units/Not sampled labels, unavailable System values, filtered timestamped logs, distinct client/host/camera views, no arbitrary command, and Stop on every route. Run `node --test tests/cameras/test_am1_console_ui.cjs` and `C:\Users\pickm\lerobot_alohamini_client\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests/robots/test_am1_console.py -q` RED.
- [ ] Render the four pages from Task 7's single snapshot/event contract. Keep camera and Control usable when a diagnostic source fails. Log export reads only exact owned session files with path validation and size bounds.
- [ ] Run `node --test tests/cameras/test_am1_console_ui.cjs`, affected console/telemetry pytest, compile/help/parser, `git diff --check`, full diff/security scan GREEN; commit and review. Read-only confirmation may use the already collected Task 6 session or the next ordinary use; do **not** require another homing/whole-robot campaign. Roll back only the console UI/controller pin if presentation fails; existing CLI and Pi motor/camera owners remain operable.

## Completion gate

After each task: inspect the complete diff, run only its listed focused checks, make one normal commit, and keep the implementation PR draft until review. Before any staging, verify exact heads, clean worktrees, private-pin backups, source imports and absence of active owners. Final acceptance requires A's camera-only page to be independently useful; B's single attended session to demonstrate typed startup/Stop, native leaders/body input and cleanup; C's fields to show honest provenance/absence without blocking control. Keep the old CLI as a documented rollback. No merge to `main`, powered operation without the separately supervised execution step, or charging-dock work is part of this plan.

**Decision status:** No architecture choice remains. The only execution-time inputs are the current clean deployed heads/pins, the operator-entered private camera credential, and attendance for the one physical Control check; their absence blocks staging/checking, not software implementation.
