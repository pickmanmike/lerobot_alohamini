# AM1 motor-off LAN camera viewer

CAMERA-VIEW1 starts from `integrate/am1-local-teleop` at
`e7d9253fd309c60d4821e7a1bdb0a2087f5bc9be`. The accepted arms, base, lift,
Local-motion, idle and shutdown milestones remain closed. This packet does
not change motor code or qualify simultaneous camera/motor load.

## Current identity gate

Five capture devices are present. The operator confirmed preview 1 = forward
and preview 2 = chest, and then confirmed preview3 = right wrist from the
refreshed image. CAMERA-VIEW2 refreshed previews 3–5 directly from their
verified capture-index0 paths, independently of the semantic role map. Preview3
is a blurred nearby surface; previews4/5 are nearly black. Backward and left
wrist roles are **unassigned**, not inferred. Exact USB paths and the new images
are private under `AlohaMini1Logs/am1-camera-view2-90rYqE` on Windows/Pi.
All share `SN0001`; use measured `ID_PATH` plus capture index 0, not that serial
or unstable `/dev/videoN` numbering. Metadata index 1 is not a capture device.

Partial maps are supported so the three confirmed views are useful now.
Five-camera acceptance remains pending all roles and the human reconnect check.
Camera-only semantic udev rules are approved but held until mapping is complete;
the current private map uses persistent `/dev/v4l/by-path/*-video-index0` links.
Do not change motor-controller aliases. Template: `config/am1.cameras.example.json`.
An unassigned role tile is not an image from one of the unassigned cameras.
Use the numbered private previews to identify those devices. If still too dark,
uncover/illuminate the actual lenses with motor power off before refreshing the
same allowlisted captures. The dashboard distinguishes unassigned roles from
mapped-but-unavailable/stale feeds. A genuinely dark decoded live image remains
visible as camera data; darkness is not inferred to mean disconnection.

### Live numbered focus/identification (current next step)

The operator needs live images to adjust the lenses physically. Opt-in
`--identify` reuses this gateway, authentication, acquisition owner and freshness
logic. It loads **separate** private `~/.config/am1-camera/identification.json`,
with fixed `preview_1` through `preview_5` keys and verified capture-index0
by-paths only. It cannot mix semantic roles into that map. The page labels are
**Camera1–5**, not guessed body roles. No paths, credentials or lens controls
are exposed in the browser. The ordinary `cameras.json` retains confirmed roles.

With motor/leader supplies off and no other camera owner, Pi Bash:

```bash
cd /home/pickmanmike/lerobot_am1_camera_viewing
export AM1_CAMERA_PYTHON=/home/pickmanmike/lerobot_alohamini/.venv/bin/python
bash tools/run_am1_camera.sh --identify --check
bash tools/run_am1_camera.sh --identify
```

Windows browser: open `http://192.168.1.134:1984` with the **existing** login.
Reload after changing viewer mode. Select Camera4, then Camera5 as primary to
adjust focus/lighting by hand without powering motors. Return their two physical
roles in one response. Camera1=forward,2=chest,3=right wrist remain confirmed.
If a feed is stale/unavailable, focus adjustment cannot fix it: preserve browser
diagnostics and the printed log. Do not mistake a genuinely dark live feed for
an unmapped placeholder. Ctrl+C in the launcher stops this identification view;
do not run it concurrently with the ordinary viewer. Existing runtime logs and
cleanup checks below apply unchanged.

After identifying4/5, update the private semantic map, then perform the single
five-label switching/reconnect acceptance below. No semantic udev rule or role
is guessed to bypass the identification gate.

## Small isolated data path

- Official **go2rtc v1.9.14**, ARM64, build `b5948cf`, is the only V4L2 acquisition
  owner. Binary SHA-256:
  `359fabade8a7a51e81a55fe6df6b0ef81764a5e1d63179577534eaaa71904b50`.
- Pinned binary: `~/.local/lib/am1-camera/go2rtc-v1.9.14`.
- Backend binds **127.0.0.1:1985**, uses random per-run private credentials,
  `local_auth: true`, `app.modules: [api, mjpeg, v4l2]`, and only the native
  stream route. RTSP/WebRTC/FFmpeg/exec and 8554/8555 are not enabled.
- A Python standard-library gateway binds the configured LAN IP, port1984.
  Only authenticated GET dashboard/assets, fixed-role media and status exist.
  It never forwards arbitrary requests, methods or source strings to go2rtc.
  Native go2rtc MJPEG has a POST upload handler, so exposing that handler
  directly is not the approved read-only boundary.
- One continuous backend reader preloads each **configured** source. The last
  complete JPEG replaces its predecessor; no queue, decoding, resize or encode.
  Five mapped sources will have five readers. A disconnected source does not
  block another reader; failed upstream reads use bounded reconnect delays.
- Primary native MJPEG, four snapshot tiles requested at2Hz, click-to-swap.
  Capture requests native MJPG640×480@30; displayed fps is measured delivery.
- Freshness advances only on a complete new upstream multipart frame. A new
  HTTP request does not refresh a cached image. Gateway frames older than500ms
  are refused; disconnected streams are stale immediately. The browser consumes
  native multipart JPEG, retains only the latest candidate while decoding and
  advances a separate image clock only after successful browser decode. It hides
  a primary image older than500ms and thumbnails older than1.5s, even while status
  continues advancing. Stalled primary consumption is aborted/reconnected.
  Aging status metadata alone does not destroy a fresh decoded frame. It is
  marked uncertain separately. Status availability uses reply time, independently
  of conservative request-start source age; failed requests hide the views, with
  a2s receipt-time ceiling if the polling loop stops (250ms poll delay plus1500ms
  request timeout and margin). These are **not** relaxed frame-age limits.
  Repeated sequences cannot renew image clocks. Reload the page after restarting
  the entire gateway process, whose sequences reset; reconnecting one camera
  within the same gateway retains its sequence and requires no page reload.
  Footer age is the image's gateway age plus measured receive/decode delay,
  **not measured physical scene-to-display latency**.
- Basic authentication is not encryption: trusted LAN only. No public route,
  Cloudflare, firewall changes, boot service, recording or browser motion.

Upstream references: [v1.9.14 release](https://github.com/AlexxIT/go2rtc/releases/tag/v1.9.14),
[module selection](https://github.com/AlexxIT/go2rtc/blob/v1.9.14/internal/app/app.go),
[API allowlist/auth](https://github.com/AlexxIT/go2rtc/blob/v1.9.14/internal/api/api.go),
[native MJPEG routes](https://github.com/AlexxIT/go2rtc/blob/v1.9.14/internal/mjpeg/mjpeg.go).

## Private configuration and credentials

Camera-only Pi worktree: `/home/pickmanmike/lerobot_am1_camera_viewing`.
Existing Python environment: `/home/pickmanmike/lerobot_alohamini/.venv/bin/python`.
The motor checkout is not switched or reset to deploy the viewer.

Config `~/.config/am1-camera/cameras.json` and browser credentials
`~/.config/am1-camera/viewer.json` are mode0600, outside Git. The state directory
`~/.local/state/am1-camera` is mode0700. Per-run backend credentials are deleted
on normal cleanup. Never copy any of these private files into a public PR.

For a new installation, after human mapping use `--configure --bind <LAN_IP>
--camera <role>=<measured-by-path>` once per confirmed role. Unmapped roles are
omitted. It refuses overwrite and does not access devices. Inspect changes and
back up a prior private map locally rather than silently replacing it.
`--check` validates the private schema without opening cameras. At startup,
paths must resolve to distinct index0 V4L2 character devices, owners and motor
host must be absent, and the backend binary hash must match the pin.

## One next human viewing session (not a motor test)

Keep all follower/body and leader motor supplies **OFF**, host stopped, USB
camera power on. Uncover/illuminate the three unidentified cameras and supply
their roles before claiming five-view acceptance. Do not unplug a motor USB
controller. No reboot or motor retest is required.

Pi SSH terminal, Bash:

```bash
cd /home/pickmanmike/lerobot_am1_camera_viewing
export AM1_CAMERA_PYTHON=/home/pickmanmike/lerobot_alohamini/.venv/bin/python
git branch --show-current
git rev-parse HEAD
bash tools/run_am1_camera.sh --check
bash tools/run_am1_camera.sh
```

Credentials already exist: **do not rerun `--init-auth`**. Open
**http://192.168.1.134:1984** in the Windows browser and use the existing login.
The foreground helper prints the exact `CAMERA_LOG` under
`/home/pickmanmike/AlohaMini1Logs/am1-camera-<timestamp>-<suffix>.log`.
Runtime output goes directly to that file, not through the SSH terminal.
Optional separate read-only log view: `tail -n 3 -F <exact CAMERA_LOG>`.

For two minutes: confirm labels, click each mapped tile into primary, and
record observed fps/freshness. Expand **Browser delivery diagnostics**: status
request last/max duration, request failures, per-role primary received/displayed
counts and maximum gaps, decode failures and last stream-cancellation reason.
Compare count deltas over a measured interval with the source fps; received
frames and decoded/displayed frames are separate evidence. Intentional switching
increments `role-switch`, not a dropout. These bounded counters are page-local,
not server logs; capture their text with the matching `CAMERA_LOG`.
Once all five are mapped, unplug/replug **one
labeled camera**. Others must stay fresh; that view must visibly become stale
and recover within10s. No repeated unchanged captures when a lens is covered.

Targets remain: primary≥12fps, no normal stall>500ms, thumbnail age<1.5s;
LAN physical video latency p95<250ms; Pi CPU/memory each<70%, temperature<75°C,
`get_throttled=0x0`, no USB reset/power warning. Reconnect is a deliberate outage,
not a normal-stall sample. Record target misses honestly; do not equate requested
30fps or two-view measurements with five-view acceptance. Physical video latency
requires a filmed stopwatch; it is not inferred from HTTP age.

Stop with **Ctrl+C in the launcher terminal** (stopping `tail` does not stop
the viewer). Expect `CAMERA_CLEANUP_ERRORS=[]`, `CAMERA_EXIT_CODE=0` and no owners:

```bash
fuser /dev/video0 /dev/video2 /dev/video4 /dev/video6 /dev/video8
# No PID output is the expected released state (fuser exits 1).
vcgencmd get_throttled
```

Collect the exact log on Windows PowerShell (no credentials/images/config in Git):

```powershell
$cameraLog = Read-Host 'Paste exact Pi CAMERA_LOG path'
scp "am1-pi:$cameraLog" "$HOME\AlohaMini1Logs\"
if ($LASTEXITCODE -ne 0) { throw 'Camera log copy failed' }
```

Stop/refuse at wrong labels, auth bypass, exposed administration, stale view
shown fresh, capture contention, resource/USB/power faults or failed cleanup.
Use the documented µStreamer fallback only if required viewer paths cannot
coexist with security or reconnect requirements. Do not change motor software.

## Direct verification — two confirmed views only

Tested runtime `4da5a8c7d90d2dd5c7e0e2f168ed72f7a9aed5b8`, go2rtc1.9.14.
Private log `am1-camera-20260920-002144-jWHjTm.log` is in `AlohaMini1Logs` on
both machines. The helper ran45s; the measurement consumed forward MJPEG plus
chest snapshots requested at2Hz for30s. It did not physically unplug a camera.

| Check | Measured result |
|---|---|
| Capture/delivery | Native640×480 MJPG, 30fps requested; both≈15fps delivered |
| Primary consumer | 454 frames, 15.000fps, maximum inter-frame gap68.545ms |
| Auth/required routes | Dashboard, JS/CSS, status, snapshots and MJPEG work; no credentials →401 (including a Windows LAN request) |
| Denial | Authenticated admin/config/streams/restart/log/debug/WebSocket/discovery paths →404; mutation methods →403; nonconfigured/arbitrary source queries denied |
| Backend | Loopback1985 only; local authentication required, administration routes404; no8554/8555 listeners |
| Freshness | Complete multipart-arrival sequence/time; latest-only snapshot cannot extend source freshness; all sampled steady-state views fresh |
| Two-source JPEG payload | Approximately14.88Mb/s during the30s sample; not interface overhead or five-camera bandwidth |
| Pi resources | Aggregate CPU≈2.14% averaged over measured interval; available-memory endpoint implies≈16.6% used; maximum sampled SoC47.95°C |
| USB/power | `throttled=0x0`; no new kernel entries in the bounded check window |
| Cleanup | `CAMERA_CLEANUP_ERRORS=[]`, `CAMERA_EXIT_CODE=0`; no camera owners; both viewer listeners gone |

The first reader attempt (`am1-camera-20260920-001738-4zeIZi.log`) exposed a
camera-only parser defect: valid native JPEGs have0–7 zero alignment bytes after
EOI. A focused failing test reproduced it; the fix accepts only that bounded
padding, preserves the original bytes, and still rejects missing EOI/garbage.
No camera controls or motor code were changed to obtain the pass.

These measurements are not five-camera, bright-scene stress, browser latency,
physical reconnect or camera-plus-motion acceptance. The last three role labels,
semantic udev rule installation and the single human viewing/reconnect session
remain open. No raw image, real device map, secret or raw log is public.

Independent review found that producer status alone could hide a stalled primary
HTTP stream. The follow-up uses a decoded-image clock for both primary and
thumbnails, aborts stalled primary consumption, and reports the image's age.
Five initially failing browser regressions now pass, including advancing status
with frozen delivery, failed thumbnail decode and fragmented multipart input.
The real browser rendered the two saved private JPEG stills and swapped primary
views in a loopback-only synthetic fixture. That verifies native padded-JPEG
decoding/UI wiring, not current physical views or delivery-rate acceptance.

## CAMERA-VIEW2 correction and evidence limits

The complete operator log `am1-camera-20260920-113702-iMMW5j.log` exercised
`7d7b9cb2b22913613ab679f7d1aa23d3507dc8ea`. It has175 source-status records
through188.435s. After startup, forward173/173 and chest174/174 samples were
fresh, about14.97fps; maximum source gaps68.851/68.844ms. Cleanup[], exit0.
This establishes healthy acquisition, not browser delivery or usable viewing.

The unchanged frontend reproduced a synthetic dropout: a status request begun
at1000ms returned at1200ms, and a decoded image at1480ms was removed at1500ms
because the *separate* source-status estimate aged past500ms. The repair keeps
advancing decoded delivery independent of that clock, exposes uncertainty and
retains explicit source/status-loss and frozen-image detection. No USB, Wi-Fi,
camera or go2rtc defect is inferred from this reproduction. Requested30fps is
unchanged; the measured≈15fps already exceeds the initial12fps target.

Five regressions initially failed (delayed-status cancellation, repeated primary
sequence, repeated snapshot sequence, unmapped labeling and missing diagnostics).
Review then reproduced a longer-delay boundary; an additional RED/GREEN test
includes successful1s responses **plus** the real250ms post-response delay.
Fresh positive viewing and the original negative cases pass. Real LAN browser
measurements and final five-role/reconnect acceptance must be recorded separately
on PR#6; offline tests and command-line source throughput cannot substitute for
them. Motor readiness from PR#5 remains closed.

## Focused software checks

Use the existing environment; no dependency install or motor test suite:

```bash
PYTHONDONTWRITEBYTECODE=1 "$AM1_CAMERA_PYTHON" -m unittest discover -s tests/cameras -p test_am1_camera_viewer.py
node --test tests/cameras/test_am1_camera_ui.cjs  # Windows Node is sufficient
bash -n tools/run_am1_camera.sh
git diff --check
```

Hardware-free tests use fake JPEG parts and loopback HTTP only. They cover
role/path binding, auth/route/method/query denial, frame freshness, displayed
thumbnail freshness, disconnect isolation, owned-child cleanup, primary-error
preservation, local private credentials, config-only execution and the launcher.
Current focused count: **31 Python tests and 19 Node/browser-logic tests**. Compilation,
hardware-free help/import, Bash/JS syntax and diff checks pass. No robot suite,
dependency installation or motor access is part of these checks.
