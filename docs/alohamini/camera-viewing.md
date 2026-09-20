# AM1 supervised LAN camera viewing and Local co-use

CAMERA-VIEW1 starts from `integrate/am1-local-teleop` at
`e7d9253fd309c60d4821e7a1bdb0a2087f5bc9be`. The accepted arms, base, lift,
Local-motion, idle and shutdown milestones remain closed. Camera-plus-Local
functional evidence is recorded below. Its original documentation-only closeout
is distinct from the subsequent offline CAMERA-AGE-1 browser correction below.

## Current state — owner-accepted functional camera-plus-Local pass

On 2026-09-20 the owner accepted practical closeout for **supervised LAN
camera-plus-Local use**, explicitly retaining the observed browser interruption
and remaining camera limitations. This is a qualified functional pass, **not**
a pass of the strict 500 ms browser-continuity target. PR #6 is prepared for
ordinary review into `integrate/am1-local-teleop`, not `main`. The subsequent
CAMERA-AGE-1 packet authorizes an ordinary two-parent merge after the focused
repair, verification and review pass. No unchanged physical repeat is requested.

The operator reported all five views usable and teleoperation working correctly.
Complete saved logs were reviewed, not just the filtered viewer:

| Evidence | Exact private log / source identity |
|---|---|
| Camera | `/home/pickmanmike/AlohaMini1Logs/am1-camera-20260920-182055-cJyMaF.log`; 448 lines, 436 status records; header `feature/am1-camera-viewing` at `5a0045bd11ac6ac2c0e5957667f68dd80ca6b193`, root `/home/pickmanmike/lerobot_am1_camera_viewing`. |
| Motor host | `/home/pickmanmike/AlohaMini1Logs/am1-local-host-20260920-182109.log`; 9,752 lines; header `feature/am1-local-mode` at `7badafdf4347cc1154c43f02fb6f6953d91053a0`, cameras disabled. |
| Windows | `C:\Users\pickm\AlohaMini1Logs\am1-local-windows-20260920-182213.log`; 143 lines; printed script/import root is the existing `.worktrees\am1-local-mode` checkout, corroborated at `a0ffbb5820162584efe3118f858f1a8f6b759e08` (docs-only after the Pi head). The log does not itself embed that Git SHA. |

Complete Pi copies are also in `C:\Users\pickm\AlohaMini1Logs`, with matching
remote/local SHA-256. Camera hash:
`990c12d411c97f82599822a209edbb1aa2bfe3ec96a321fead47abedafc4f97d`;
host: `48a43645b85d9ab8088ea6a2591a339bf846563249dee15b98610adb5a85b674`;
Windows: `e469f20650fac3f5248d9b5f2459c4719a4794925724e6ed1a9689b8c17457cb`.
Raw logs, household images, private maps and credentials remain outside Git.

| Component | Measured result and boundary |
|---|---|
| Five-source acquisition | All five fresh in 434 successive status records from 3.259 s through 469.000 s (465.741 s); no later freshness loss or sequence regression. Approximately 15 fps (Right wrist averaged 15.55); maximum source gap 74.310 ms. Requested 30 fps is not a delivered-rate claim. |
| Browser delivery | Forward 2,946 received / 2,917 displayed, final sequence 4,113. Status failures 0, decode failures 0, cancellations 0; status maximum 1,360 ms. Maximum receive/display gaps **1,133/1,138 ms**, exceeding the 500 ms target. Latest-only rendering can skip pending frames; counts alone do not establish browser fps or loss-free delivery. |
| Startup and relief | Homing completed in 2.184 s, current-threshold stop; controlled relief reached 10.254 mm. Synchronization and final fresh alignment passed. |
| Live control | 262 client actions over 26.256 s, approximately 9.98 Hz; maximum send-start interval 110 ms. Four transient no-new-observation-sequence results, no terminal stale latch or body-command expiry. No host watchdog event within the live command stream. |
| Watchdog context | Four events correspond to pre-client idle, initial operator gate, post-sync/Enter pause and post-client stop. The 13.776 s global receive-gap maximum spans a pause into the first live command, not a demonstrated live stall. Host sampled receive gaps during the final stream peaked at 167.065 ms; approximately 1 Hz summaries are not a complete per-command maximum. PC/Pi clock offset was not measured. |
| Lift monitoring | 8,444 temperature samples were 36–39 C, no numeric outliers or high windows, no reported motor/transport fault. Raised stationary samples (zero goal/actual velocity and moving=0) had current 0–52 mA, mean 24.04 mA. This short result does not erase earlier thermal/raw-feedback limitations or establish long-duration stability. |
| Stop and cleanup | Client final-zero request and exit 0; host five-sample stopped qualification with torque/goal/actual velocity/current all zero, `shutdown_verified`, exit 0; camera cleanup errors `[]`, exit 0. No new direction or per-key stopping suite is claimed; accepted motor milestones remain closed. |
| Kernel evidence | Saved kernel query covering 18:20:50–18:28:50 local time returned no entries. Full resource/uplink headroom and contemporaneous throttling were not measured in this review. |

The operator believes browser diagnostics were copied before stopping the viewer.
Their maxima have no event timestamp and there is no pre-live browser baseline,
so the 1.138 s gap cannot be assigned specifically to live control or dismissed
as shutdown. Healthy acquisition places the interruption downstream of capture;
HTTP/network delivery versus browser processing/scheduling is not distinguished.
The separately reproduced header-age defect below does not identify this gap's
cause. Physical scene-to-display latency
is still unmeasured, not supplied by these gaps or image-age metadata.

### Accepted practical scope and remaining limitations

The owner's acceptance covers stable connected-camera LAN viewing, documented
stop/restart recovery, and the useful short combined Local session. It explicitly
accepts the observed browser-delivery limitation for supervised use; it neither
changes runtime freshness thresholds nor claims the original strict continuity
or isolated live hot-plug criteria passed. Keep direct supervision and the
existing loss-of-required-view stop procedure below. PR #5 and all accepted
Local motor milestones remain closed.

Still unverified: cold boot/power-cycle reliability; isolated live hot-plug
recovery; exact cause of the earlier Forward USB/V4L2 failure and current browser
gap; physical scene-to-display latency; five-view browser fps over measured
intervals; full five-camera resource/uplink headroom; long-duration combined use.
These limitations remain visible at integration review, not new prerequisites
for another benchmark, unplug campaign or repeated motor commissioning.
Next action is ordinary PR review, not another powered acceptance session.

## Prior accepted Forward recovery/restart evidence

The targeted Forward recovery/restart check is **closed**. Complete saved log
`am1-camera-20260920-152530-egPuH8.log` records
`feature/am1-camera-viewing` at `5a0045bd11ac6ac2c0e5957667f68dd80ca6b193`,
source root `/home/pickmanmike/lerobot_am1_camera_viewing`, and pinned go2rtc
1.9.14. The original is under `/home/pickmanmike/AlohaMini1Logs`; the complete
Windows copy is `C:\Users\pickm\AlohaMini1Logs\am1-camera-20260920-152530-egPuH8.log`.
SHA-256: `fef5be7ee28b0373c581e752b726930a61a25fb9edfbb7896416ad745bafa213`.
All 60 lines / 48 status records were reviewed, not only a filtered viewer.

| Evidence | Supported result |
|---|---|
| No-touch viewer restart | All five sources fresh in all 46 samples from 3.069 s through 51.577 s, a 48.508 s span. No sequence/byte regression. |
| All-source acquisition | Approximately 15 fps Forward/Backward/Left wrist and 20 fps Chest/Right wrist; cumulative maximum source gaps 53.472–69.454 ms. |
| Forward browser interval | 608 received / 608 displayed; status/decode failures 0; cancellations 0; maximum receive/display gap 279/280 ms. Actual browser observation duration was not recorded: **no browser-fps calculation** from the count or host duration. |
| Operator / kernel | Operator reports all five live and looking good, with no USB handling. Saved kernel query for 15:25:25–15:26:35 local time returned no entries. |
| Shutdown | `CAMERA_STOP_REQUESTED`, `CAMERA_CLEANUP_ERRORS=[]`, `CAMERA_EXIT_CODE=0`. |

Existing evidence is cumulative, not a requirement to repeat every accepted
item in one log. The operator's five-role/orientation observations and corrected
wrist mapping below remain accepted. In the preceding
`am1-camera-20260920-151731-ukdp10.log` at the same source head, Forward recovered
after its physical replug and then remained fresh through the final 31.118 s;
the operator confirmed Forward/Rear primary switching and a live Forward
thumbnail. Browser Forward 261/259 and Rear 102/102, decode/status failures 0,
last cancellation `role-switch`; latest-only rendering can skip a pending frame.
A separate Rear USB disconnect caused a 3.213 s gap earlier in that run.
Recovery and switching passed, but that run did not prove isolated hot-plug
recovery with all other feeds uninterrupted.

## Confirmed identities and display orientation

After the September 20 reconnect checks, the operator corrected the wrist
identities: the view previously labeled left wrist is physically right, and
the view previously labeled right wrist is physically left. This mapping
**supersedes the wrist labels deployed at80986427**. Display rotations stay
with the physical cameras; only their semantic role assignments swap:

| Numbered source | Semantic role | Browser image rotation |
|---|---|---|
| Camera1 | forward (front) | 180 degrees |
| Camera2 | chest | 180 degrees |
| Camera3 | wrist_right (right hand) | 90 degrees clockwise |
| Camera4 | backward (rear) | 180 degrees |
| Camera5 | wrist_left (left hand) | 90 degrees counterclockwise |

Exact USB paths, household images and private maps remain outside Git.
All share `SN0001`; use measured `ID_PATH` plus capture index 0, not that serial
or unstable `/dev/videoN` numbering. Metadata index 1 is not a capture device.

Both private maps retain those measured paths; the normal map now contains all
five semantic roles. Optional `rotations` stores clockwise integer degrees
0/90/180/270 for configured roles only (omitted means0). Thus left wrist uses270.
The browser applies rotation to **images only**, in primary and thumbnail views;
labels stay upright, quarter turns fit without cropping. Native640×480 JPEGs,
snapshot API data, capture settings and go2rtc configuration are unchanged.
This is display orientation, not an image-processing or recording transform.

Role/orientation and normal view-switch evidence are accepted as described
above; isolated hot-plug recovery is retained as a limitation under the accepted
closeout scope. Additional semantic udev rules are not needed: the private map uses persistent
`/dev/v4l/by-path/*-video-index0` links.
Do not change motor-controller aliases. Template: `config/am1.cameras.example.json`.
An unassigned role tile is not an image from one of the unassigned cameras.
Use the numbered private previews to identify those devices. If still too dark,
uncover/illuminate the actual lenses with motor power off before refreshing the
same allowlisted captures. The dashboard distinguishes unassigned roles from
mapped-but-unavailable/stale feeds. A genuinely dark decoded live image remains
visible as camera data; darkness is not inferred to mean disconnection.

### Optional numbered focus/identification

For future focus work only, opt-in
`--identify` reuses this gateway, authentication, acquisition owner and freshness
logic. It loads **separate** private `~/.config/am1-camera/identification.json`,
with fixed `preview_1` through `preview_5` keys and verified capture-index0
by-paths only. It cannot mix semantic roles into that map. The page labels remain
**Camera1–5**. No paths, credentials or lens controls are exposed in the browser.
The ordinary `cameras.json` retains confirmed roles; each private map carries
the equivalent display rotations. No identification run is currently needed.

With motor/leader supplies off and no other camera owner, Pi Bash:

```bash
cd /home/pickmanmike/lerobot_am1_camera_viewing
export AM1_CAMERA_PYTHON=/home/pickmanmike/lerobot_alohamini/.venv/bin/python
bash tools/run_am1_camera.sh --identify --check
bash tools/run_am1_camera.sh --identify
```

Windows browser: open `http://192.168.1.134:1984` with the **existing** login.
Reload after changing viewer mode. Select the desired numbered camera as primary
to adjust focus/lighting by hand without powering motors; identities are now
known and do not need another identification round. Numbered paths and their
rotations do not change when the semantic wrist labels are corrected.
If a feed is stale/unavailable, focus adjustment cannot fix it: preserve browser
diagnostics and the printed log. Do not mistake a genuinely dark live feed for
an unmapped placeholder. Ctrl+C in the launcher stops this identification view;
do not run it concurrently with the ordinary viewer. Existing runtime logs and
cleanup checks below apply unchanged.

Do not run both viewer modes concurrently. Use normal mode for combined use.

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
  updates separate displayed-frame state only after successful browser decode,
  retaining the frame's receive-start timestamp rather than decode time. It hides
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
  Footer age is gateway-reported age plus elapsed client time from the relevant
  pending read (first frame: before HTTP fetch), including body/decode waits.
  This conservative estimate may include time waiting for a not-yet-produced
  frame. It cannot establish exposure time, clock synchronization, or age in
  unobserved upstream/browser queues. The consumer reads continuously without
  awaiting image decode; it does not timestamp arbitrary browser-queued chunks
  while JavaScript is suspended. It is **not physical scene-to-display latency**.
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
For display-only orientation, add the optional `rotations` object to the private
map after backup, e.g. `"rotations": {"wrist_left": 270, "wrist_right": 90}` when
both roles are configured. Values are clockwise degrees, not camera controls.

## Historical Forward failure — recovered, exact cause unresolved

The complete `am1-camera-20260920-144920-dERFuv.log` at80986427 proves the
front source received **zero frames for the entire run**, not just after
switching to a thumbnail. A subsequent bounded `v4l2-ctl` reference attempt
outside the viewer returned `VIDIOC_STREAMON returned -1 (Protocol error)`.
Its process exit0 is **not a capture pass**. The discovered front index0 path
resolved to `/dev/video12`, was present and unowned, and reported native
MJPG640×480@30. No camera settings were changed. The exact USB/device cause
remains unresolved; do not patch frontend switching or change camera backend
based on this evidence.

The later replug recovery and no-touch restart passed as recorded above.
Do not prescribe another recovery attempt to reconfirm those results. For a
future loss of a required view during Local use, release movement inputs,
end the client and cleanly stop the motor host before stopping/restarting the
viewer. Viewer startup deliberately refuses an already-running motor host.
If restart does not restore all required views, retain logs and refuse motion;
do not automatically retry or handle USB connections during powered operation.

## Reference camera-plus-Local procedure — exercised, no repeat requested

The short co-load check above is complete. These existing commands are retained
for supervised use and source identity, not a new acceptance-test request. Source
inspection found no integration change needed: the camera gateway starts first
with its single go2rtc acquisition owner; the Local host and Windows client
already use `--no_cameras`. The host sets its camera configuration to `{}` before
robot construction. Local retains its independent 10 Hz sender, freshness and
250 ms body-command expiry, one-second host watchdog and ordinary cleanup.
Motor production code and private configuration remain unchanged.

Worktrees verified during the evidence review (retain them; no reset or migration):

| Machine / purpose | Directory | Branch / head |
|---|---|---|
| Pi camera | `/home/pickmanmike/lerobot_am1_camera_viewing` | `feature/am1-camera-viewing`, `5a0045bd11ac6ac2c0e5957667f68dd80ca6b193` |
| Pi motor | `/home/pickmanmike/lerobot_alohamini` | `feature/am1-local-mode`, `7badafdf4347cc1154c43f02fb6f6953d91053a0` |
| Windows Local | `C:\Users\pickm\lerobot_alohamini_client\.worktrees\am1-local-mode` | `feature/am1-local-mode`, `a0ffbb5820162584efe3118f858f1a8f6b759e08` |

Those are the exercised/deployed identities at the evidence review, not a claim
that they already contain CAMERA-AGE-1. That browser correction needs a clean,
stopped camera checkout updated to the reviewed feature head and a browser reload
on next normal launch. No motor-checkout update is needed. The Windows motor head
adds evidence documentation after the exercised Pi head; their runtime is equal.

1. **Physical preparation, motors OFF:** normal motor wiring, spare disconnected,
   mechanically installed lift with no added payload, existing calibrated leaders
   and PnP ownership. Preserve the known-good camera USB connections and private
   login/map. Clear the arm/base/lift envelope; arrange safe carriage/arm support
   before torque-off and keep the power disconnect accessible. No USB handling,
   recalibration, endpoint forcing or new power configuration. Motor host stopped.
2. **Pi SSH terminal A, Bash — camera viewer first.** Reuse the existing Pi venv:

```bash
cd /home/pickmanmike/lerobot_am1_camera_viewing
export AM1_CAMERA_PYTHON=/home/pickmanmike/lerobot_alohamini/.venv/bin/python
bash tools/run_am1_camera.sh --check || exit 2
bash tools/run_am1_camera.sh
```

3. **Windows browser:** reload `http://192.168.1.134:1984`, existing login (no
   `--init-auth`). Confirm all five required views are available **before motor
   power or motion**. Keep the browser visible, normally Forward primary, throughout
   the session. Do not start recording, a second viewer backend or a host camera.
4. **Pi SSH terminal B, Bash — motor host.** Only after the view check, enable the
   established follower/body and designated leader supplies. This command can
   home/move the lift immediately; the operator must be ready. The helper uses
   `/home/pickmanmike/lerobot_alohamini/.venv/bin/python` and its own source root:

```bash
cd /home/pickmanmike/lerobot_alohamini
test "$(git rev-parse HEAD)" = 7badafdf4347cc1154c43f02fb6f6953d91053a0 || exit 2
./tools/run_am1_host.sh --mode local
```

5. **Pi SSH terminal C, Bash — read-only readiness/log view.** Paste the exact
   `HOST_LOG` printed by terminal B; this is only a file reader:

```bash
read -r -p 'Paste the exact HOST_LOG path: ' AM1_LOG
tail -n +1 -F -- "$AM1_LOG" | grep --line-buffered -E 'operational_ready|HOST CADENCE|temperature_warning|shutdown_verified|HOST_EXIT_CODE|Traceback|Refusal|Error'
```

   Observe actual-bottom home and approximately 10 mm relief, then wait for
   `operational_ready` and the first `[HOST CADENCE]`. The full raw log remains
   saved; filtered output is not substitute evidence. Do not continue on a fault.
6. **Windows PowerShell 7 — existing Local worktree/helper/config.** Existing
   Python is `C:\Users\pickm\lerobot_alohamini_client\.venv\Scripts\python.exe`;
   the helper supplies source `PYTHONPATH`, validates calibration and resolves
   the existing PnP map. Do not substitute historical COM numbers:

```powershell
Set-Location 'C:\Users\pickm\lerobot_alohamini_client\.worktrees\am1-local-mode'
if ((git rev-parse HEAD) -ne 'a0ffbb5820162584efe3118f858f1a8f6b759e08') { throw 'Unexpected Local source head' }
.\tools\run_am1.ps1 -Mode Local -ConfigPath 'C:\Users\pickm\lerobot_alohamini_client\.worktrees\am1-base-teleop\config\am1.local.json'
```

   Hold leaders still and release all movement keys through exact uppercase
   `SYNC`, synchronization (120 s requested, possibly longer), and the post-sync
   Enter/alignment gate. Wait for `TELEOPERATION ACTIVE` before ordinary motion.
   While still paused, save a browser-diagnostics/time baseline; note the actual
   live-start and Q times separately. Do not call page-wide maxima live-only.
   The existing helper then bounds **live** operation to 30 s at 10 Hz; startup
   and operator waits are not part of those 30 s. Make small representative
   bimanual movements, briefly W/release, U/release, then J/release while staying
   above the unchanged 5 mm descent floor. This samples co-load, not every joint
   or direction. After each release require prompt visible stopping before the
   next input; if motion continues, press Q/use the prepared disconnect and do
   not continue. Keep the browser visible; return focus to the client before
   using keys. Release inputs before any focus change. Press Q before expiry;
   otherwise the existing 30 s limit ends live mode. Q does not stop the Pi host.
7. **Shutdown, in this order:** after Q and Windows exit 0, copy browser
   diagnostics while the viewer is still running. Safely support carriage/arms,
   then Ctrl+C in **terminal B**, not the separate tail. Retain `shutdown_verified`
   and `HOST_EXIT_CODE=0` from the saved host log; remove motor power. Stop the
   tail with Ctrl+C in C, then stop the camera viewer with Ctrl+C in A. Expect
   `CAMERA_CLEANUP_ERRORS=[]`, `CAMERA_EXIT_CODE=0`. No extra 60 s idle or motor
   direction suite is requested. On unsafe behavior use the prepared physical
   disconnect; do not reach into moving mechanisms or retry automatically.

Automatic logs (retain exact printed paths, outside Git):

- Pi camera: `/home/pickmanmike/AlohaMini1Logs/am1-camera-<timestamp>-<suffix>.log`.
- Pi motor: `/home/pickmanmike/AlohaMini1Logs/am1-local-host-<timestamp>.log`.
- Windows: `C:\Users\pickm\AlohaMini1Logs\am1-local-windows-<timestamp>.log`.

After shutdown, optional single PowerShell fetch of the two exact Pi logs:

```powershell
$cameraLog = Read-Host 'Paste exact CAMERA_LOG path'
$motorLog = Read-Host 'Paste exact HOST_LOG path'
scp "am1-pi:$cameraLog" "am1-pi:$motorLog" 'C:\Users\pickm\AlohaMini1Logs\'
if ($LASTEXITCODE -ne 0) { throw 'Log copy failed; retain original Pi logs' }
```

**Original measurement targets, not all passed:** healthy required views before
motion and throughout the live interval; no normal video stall over 500 ms; no
stale required thumbnail over 1.5 s. The accepted session exceeded the browser
gap target as documented above; owner acceptance is not a measured target pass.
Retain approximately 10 Hz live action delivery, fresh observations, no terminal
stale latch, no live host command-watchdog event; release/Q stopping and clean
host/viewer cleanup. Record browser counter deltas only with a measured browser
interval; page maxima can include startup/sync. Use client live markers/cadence
and host command sequence/gap evidence to separate startup/operator waits and
expected post-Q watchdog zero from a live fault. Do not assume PC/Pi clock offset.
Keep approved isolated numeric-temperature warnings distinct from genuine
faults; no threshold changes. Any genuine motor/transport/power/USB fault, lost
required view or failed stopping/cleanup ends the check. No automatic resume.

Physical video latency and full resource headroom remain unmeasured, not inferred
from this short packet. No recording, boot service, remote access, browser motor
controls, new dependencies, capture format or backend change is authorized here.
Authentication, administrative-route denial and native-frame freshness protections
remain required. The original physical-latency/resource targets remain targets,
not measured passes or new benchmark prerequisites for this accepted scope.

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
physical reconnect or camera-plus-motion acceptance. Those role labels were
subsequently supplied (see the final mapping and cumulative evidence above).
No raw image, real device map, secret
or raw log is public.

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
Fresh positive viewing and the original negative cases pass. Later real LAN
viewing evidence is recorded above; offline tests and source throughput alone
do not establish browser or hot-plug acceptance. Motor readiness from PR#5 remains closed.

## Five-source identification run and rotation verification

Complete private log `am1-camera-20260920-133204-BLAPj2.log` exercised
`8c277dd6a9e424a0924d3025b90f5cc32a32d7b0`, numbered mode, pinned go2rtc1.9.14.
It contains250 source-status records through269.563s. All five were fresh by
3.259s and stayed fresh in the remaining248 samples, with strictly advancing
sequences. Final rolling fps for1–5:14.966,19.941,19.943,14.979,14.967; maximum
source gaps:69.366,68.547,69.466,70.128,69.967ms. Sources2/3 changed between
roughly15 and20fps during this run without a freshness loss. Requested30fps
is not a delivered-rate claim. Stop requested, cleanup[], exit0.

This log supports five-source acquisition and cleanup, not browser decoding,
display orientation, physical latency or reconnect. Physical identities and
requested rotations are operator observations. Browser-only rotation regressions
first failed on missing schema/status/image metadata, then passed. A real
browser using synthetic640×480 orientation cards and the actual stylesheet
verified0/90/180/270 directions, full-frame fit and unrotated labels. That offline
check is separate from the later operator viewing evidence recorded above.

The subsequent operator log `am1-camera-20260920-143700-7gcCfu.log` at80986427
has199 status records through214.719s; source interruptions match USB events
during connector handling. Useful viewing was reported;3167 primary frames
were received/displayed with zero decode failures. Browser counters copied
after Ctrl+C cannot assign status failures to the live interval. Cleanup[], exit0.

`am1-camera-20260920-144920-dERFuv.log` has88 status records through95.294s.
Forward remains unavailable with sequence0/bytes0 in every sample. Rear
recovers from a1.816s source gap. The role then labeled `wrist_left` (physically
**right**, per the operator correction) recovers from a15.488s frame gap;
USB events include its actual disconnect14:50:19 and re-enumeration14:50:32.
The gap therefore is not a15.488s reconnection delay. Chest and physical left
wrist remain fresh after startup. Rear browser delivery817/817, decode errors0,
status failures0. Shutdown requested, cleanup[], exit0. This establishes a
front acquisition failure, not a frontend primary-to-thumbnail diagnosis.

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
Existing runtime evidence: **32 Python tests and 21 Node/browser-logic tests**
passed in the prior camera verification, along with compilation, hardware-free
help/import and Bash/JS syntax checks. The preparation and owner-acceptance
follow-ups change only this Markdown runbook and PR text:
documentation/reference/diff checks only, no fresh
runtime-test claim, motor suites, dependency installation or hardware execution.

## CAMERA-AGE-1 — focused offline receive-age correction

Review finding `4058369025` exposed a definite frontend defect, separate from
the accepted physical browser-gap limitation. With synthetic frame time 0 and
gateway age 0, a header completed at 400 ms was labeled 499 ms/fresh at 899 ms.
The old parser began its timestamp only after parsing the complete header.

The corrected consumer carries the initial HTTP request start and subsequent
pending-read timestamps through fragmented headers, body waits and decoding.
Parts coalesced in a chunk keep that chunk's anchor, even when parsing resumes
later; the next read gets its own timestamp, not the whole connection's start.
Empty chunks cannot erase an outstanding wait. Native bytes, bounded buffering,
reader cancellation/release, sequences, independent status/video clocks and the
500 ms primary / 1.5 s thumbnail thresholds are unchanged. No motor path,
gateway protocol, camera backend, authentication, mapping or rotation changed.

Fresh validation for this correction: the five new timing regressions first
failed for the intended freshness error while all 21 existing Node tests passed;
after the fix and two additional boundary/control checks, **28 Node tests pass**.
The existing **32 Python camera tests pass**. A loopback-only synthetic browser
fixture uses the actual app/parser/freshness/CSS and real JPEG decoding: a delayed
first response plus fragmented header displays at synthetic age 450 ms, becomes
stale at 899 ms, accepts fresh subsequent 67 ms delivery, and cancels a repeated
sequence/stalled stream. Its seven assertions pass. These simulated clocks and
generated test image are not a new camera trial or a physical latency benchmark.

This correction preserves the qualified owner acceptance, the recorded 1.138 s
gap and all earlier limitations. It does not prove that gap's cause. The earlier
two post-exercise documentation commits remain documentation-only; their
historical runtime checks are not relabeled as tests of this repair.
