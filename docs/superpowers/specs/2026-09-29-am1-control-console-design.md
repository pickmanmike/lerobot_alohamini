# Aloha Mini 1 control console

Status: written design for owner review. The overall Windows-hosted direction and three delivery slices were approved in conversation. This document is not yet an approved implementation plan. This commit changes documentation only and authorizes no deployment or powered operation.

## Goal and scope

Make attended Aloha Mini 1 operation usable from one browser application: camera viewing, one-click prepared startup and shutdown, native physical-leader input, page/keyboard body controls, understandable status, and supporting servo/system/log/terminal pages. Charging-dock work is deferred. Existing teleoperation commissioning remains closed with its documented limitations. Do not reopen elbow tuning, network remediation, calibration, or retired gain experiments for this UI project.

The application is for the Windows PC hosting the existing leader client. Aloha Mini 2 and Mini 2 Pro behavior must remain unchanged. Phone/other-PC access, off-LAN access, browser serial control, arbitrary remote shells, autonomous behavior, a 3D digital twin, and a new streaming backend are not version-1 requirements.

## Verified starting point

Repository: `pickmanmike/lerobot_alohamini`.

| Source | Reference |
|---|---|
| Integration baseline | `5d45ace56e01b813b9d4d77f803672f8fe0744bb` |
| Motor source recorded as deployed in the current runbook | `43d1622a9395cdc1d1f9acce1090ed3f029f4f7c` |
| Windows client/session and Pi helper recorded as deployed | `0c4f2e7ccce3ddcce6d75e7113ed819d07f1d192` |
| Separate camera source recorded as deployed | `047c4fcf7cbf34684a9b8c348193585938975815` |

These deployment identities come from the current repository runbook, not new machine access by this design review. Verify actual checkouts and private pins before staging. Different component SHAs are intentional; do not unify them by replacing working camera or motor code.

Source anchors:
- `docs/alohamini/unified-session.md` at the integration baseline: ordinary workflow, limits, cleanup, and accepted limitations.
- `tools/am1_camera/{index.html,style.css,app.js,freshness.js,mjpeg.js}` and `tools/am1_camera_viewer.py` at the camera reference: five roles, native frame handling, authentication and existing camera service.
- `tools/am1_session.py`, `tools/run_am1_session.ps1`, and `examples/alohamini/teleoperate_bi.py` at the helper reference: session coordination, native input and existing startup/recovery paths.
- `src/lerobot/robots/alohamini/` at the motor reference: observations and single-owner motor control.

The inspected camera JavaScript clears the primary image when stopping a stream; the stylesheet hides non-fresh images. The existing viewer uses a primary MJPEG stream and lower-rate secondary snapshots. Those presentation decisions can change without replacing capture, mappings, rotations, or motor policy. Some historical diagnostic switches remain in the pinned helper but are unsupported by the clean motor; do not expose them in the console.

## Application and ownership

A small Windows-local console process serves the browser application and remains available before and after a robot session. A desktop shortcut starts this console and opens its page; merely opening it never starts motors. The service uses existing environments, proxies only the configured authenticated camera sources, and adapts the current session controller rather than implementing another independent orchestration engine.

Keep one public browser origin, bound to loopback initially. Credentials remain server-side. Validate Host/origin, state-changing requests and the active session identity; loopback alone is not authorization. No arbitrary URL proxy, command execution, permissive CORS, router change or new public listener. The implementation plan chooses a small supported event transport from the actual environment, not a new framework stack by default.

Physical leaders remain owned and sampled by the native Windows client. The browser submits intent; the native client retains command pacing and combines leader/body inputs; the Pi retains exclusive motor-bus ownership and local stopping. There must be no second serial reader or independent motor writer. Native terminal mode remains available as a fallback.

Use typed session operations and events for Start, Pause, Resume, Stop and progress. Do not control the UI by feeding blank lines into terminal prompts or parsing prose as the only source of state. Preserve the ordinary CLI adapter and its existing prompts.

## Control page

Persistent navigation: Control, Servos, System, Logs, Terminal. The application retains session state across routes. Robot identity, actual session state and Stop remain visible everywhere. Distinguish console availability, camera availability and teleoperation readiness.

The main area prioritizes cameras; a compact panel holds input mode, duration, speed, wheel/lift controls and the current actionable status. Physical leaders are the default. Scripted ArmSmoke is explicitly selected for regression work, never silently substituted for disconnected leaders. Keep its existing zero-body behavior.

Start is one deliberate action after power/pose preparation described on the page. It starts the existing camera/host/client sequence and advances successful stages using actual readiness, stable leaders and final alignment. It does not switch physical supplies or bypass checks. Initial approval covers the normal prepared sequence; show a specific on-page action only when an exception really needs intervention. Keep nominal 30-second synchronization and selectable duration 1–1800 seconds. Countdown refers to the current phase, not guaranteed total startup time.

Pause uses existing hold/recovery semantics. Stop works during startup and live use, has no confirmation dialog, and retains existing cleanup/log collection. Show Stopping until results support Stopped; display incomplete cleanup separately. After stopping, the application remains open with results. Preview without motors may be exposed explicitly using camera-only ownership; do not silently leave cameras running after a session whose normal cleanup stops them.

Double Start must not create duplicate processes. Refresh/reconnect attaches to the existing session, not a new start. Only one tab owns movement input. Other tabs cannot silently take over; an authorized Stop remains available. Page closure, loss of the control connection or control focus clears held body inputs and requests the established pause/hold behavior. Reconnection never resurrects held commands or automatically rearms a terminated motor host. Navigating to a diagnostic route preserves the session, clears movement inputs and makes any pause state explicit.

Wheel/lift buttons use press-and-hold, with release/cancel/lost-capture handling. Use the actual configured keyboard mapping; the current default is W/S forward/back, Z/X strafe, A/D rotate, U/J lift, T/G speed, Q stop. Keyboard controls apply only in the Control context, never while typing in inputs or selecting log text. UI mode disables the competing native/global body-key listener while retaining native leader reads; CLI mode remains unchanged. Reuse speed and freshness limits. Held intent expires at the native service/client if browser events stop, independent of browser timers. No queued catch-up or summed browser/native movement streams.

## Camera workspace

Five semantic roles: Front (`forward`), Rear (`backward`), Chest, Left wrist and Right wrist. Left/right always mean the robot's left/right. Preserve mapping and wrist rotations, including during promotion.

Use a prominent focus pane with Front selected by default, plus stable labeled anatomical slots: wrists left/right, Chest centrally, Rear lower/central, Front upper/central. Clicking a camera promotes it; the old selection returns to its own slot. Do not reshuffle other slots or reverse driving coordinates. An enlarged view can have an optional full-screen control. Preserve aspect ratios and use responsive layouts for laptop screens.

Put label and a compact Live/Waiting/Disconnected status in a header outside the image. Per-camera Details starts collapsed and expands below that feed. All detailed metrics stay there or on the System page, never over the image. Opening Details must not itself promote a camera. Make the layout and controls keyboard accessible without nested interactive controls.

Keep the last successfully decoded frame for each source when a request fails, status is delayed, or a stream reconnects. Replace it only with a valid newer frame for the correct camera. Never paint black just because an age threshold elapsed. If no image ever arrived, show a truthful placeholder. A held frame must say Last frame / waiting and retain an honest advancing age; uncertain source status is not Live. Status replies do not renew image timestamps.

Track camera identity/source generation separately from sequence so restarts that reset counters can recover without accepting old in-flight responses. Role switches must not show another camera under the new label. Retain at most bounded per-role image/decoder state and release replaced image resources. Keep latest-only decoding, primary-versus-thumbnail bandwidth discipline, bounded reconnect attempts and real capture timestamps. Reconnect is not synonymous with clearing the displayed image.

This change improves presentation, not proof of uninterrupted video or permission to drive using a held frame. Preserve the existing response to losing a required operational view; do not tie motor safety to the act of hiding/showing pixels.

## Supporting pages and data contract

Servos: a lightweight 2D anatomical schematic of both follower arms, lift and three wheels; leaders are a separate section when connected. Identity includes side/bus and role, not servo ID alone. Cards show reported position/change, requested target and current/status where available. Expand details below each card for all supported available fields. Label raw counts, normalized positions, degrees, measured velocity and estimated change correctly.

Distinguish Live, Snapshot with timestamp, Not sampled, and Unavailable. Current ordinary observations are not an existing stream of every register. First expose data the owning loops already read. Cache static configuration; acquire a genuinely missing field only through a bounded same-owner path if required. Opening or closing panels must not add uncontrolled serial polling. Missing values are never fabricated zeroes; temperature readings must not silently be presented as confirmed faults. No servo configuration writes in these pages.

System: modest live CPU/memory/temperature/uptime/storage and relevant power/throttling indicators where obtainable, plus process state, loop timing, communication age, camera health and source versions. Start around 1 Hz for system metrics and 5–10 Hz for cached servo display where available; these are UI rates, not new motor-read rates. No inferred battery percentage without a real measurement source.

Logs: severity and component filters, search, timestamps, pause/follow, bounded recent buffer and existing-file export. Keep causal errors, cleanup errors and log-transfer errors distinct. Terminal: read-only original session/client/host/camera output in separate views; not an arbitrary shell. Control actions stay on Control.

Publish shared bounded snapshots/events once, rather than a polling service per panel. Slow browsers or verbose logs must not block the motor loop, delay Stop or create unbounded buffers. Keep telemetry field provenance and acquisition time; never synthesize freshness from delivery time. Camera credentials, private maps/calibrations and household imagery stay out of public Git history. Do not build a database or video archive for this phase.

## Three delivery slices

1. Camera usability: implement layout, selection, collapsible details, last-good-frame retention and navigation styling. Prefer a front-end-only change against the verified camera source first, reusing that presentation in the console later. No motor-control change; unavailable future pages/actions are honestly labeled preview/unimplemented, not faked. Validate with generated JPEGs and browser scenarios, then one camera-only start/stop when needed. Deliver a usable page and screenshots, not only a mockup.
2. Unified operation: add the minimal Windows-local shell, session adapter, native input adapter, live status and Start/Pause/Stop. Keep the page available with the session stopped. Validate lifecycle, stale/duplicate requests, input cancellation, focus loss, refresh and single-controller ownership with fake hardware, then one short attended normal session. Physical leaders are needed only to confirm the real manual path, not for every software iteration.
3. Supporting pages: connect servo/system/log/terminal views to real bounded data. Use cached/live fixtures for schema and freshness tests, then read-only runtime confirmation. No repeated homing or whole-robot acceptance campaign for data presentation.

The implementation plan must identify exact existing functions to adapt, source lineage and deployment targets, selected transport/environment, tests and rollback for each slice. It must reconcile the native keyboard and startup-admission paths before powered work. One concise owner review of that concrete plan precedes implementation. After approval, Codex should execute, test, review and stage sequential slices through completion, pausing only for genuinely necessary physical actions or a material design deviation, not each successful micro-step.

## Acceptance and preservation

Success: open one console, view uncluttered cameras with truthful held-frame status, start prepared operation once, use native leaders and page/keyboard controls, inspect real data and logs, then stop and obtain results without normal terminal interaction. Known tracking, camera and endurance limitations remain accurately recorded; UI work must not silently redefine them away.

Keep existing installations, source pins and fallback commands recoverable. No blanket reinstall, motor reconfiguration, gain experiment, dock implementation, network investigation or AM2 modification. Stage only reviewed stopped components and change only necessary backed-up private pins. Software development and camera-only checks do not require leader input or powered motor operation. Real movement remains slow, attended, clear of pinch points, with accessible power removal. No automatic merge or powered execution is authorized by this design document alone.
