# AM1 motor-off LAN camera viewer

CAMERA-VIEW1 starts from `integrate/am1-local-teleop` at
`e7d9253fd309c60d4821e7a1bdb0a2087f5bc9be`. The accepted arms, base, lift,
Local-motion, idle and shutdown milestones remain closed. This packet does
not change motor code or qualify simultaneous camera/motor load.

## Current identity gate

Five capture devices are present. The operator confirmed preview 1 = forward
and preview 2 = chest. Previews 3–5 remain too dark to identify; backward and
both wrist roles are **unassigned**, not inferred. Exact USB paths and images
are private under `AlohaMini1Logs/am1-camera-discovery-NK2rRDBZ` on Windows/Pi.
All share `SN0001`; use measured `ID_PATH` plus capture index 0, not that serial
or unstable `/dev/videoN` numbering. Metadata index 1 is not a capture device.

Partial maps are supported so the two confirmed views are useful now.
Five-camera acceptance remains pending all roles and the human reconnect check.
Camera-only semantic udev rules are approved but held until mapping is complete;
the current private map uses persistent `/dev/v4l/by-path/*-video-index0` links.
Do not change motor-controller aliases. Template: `config/am1.cameras.example.json`.

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
  are refused; disconnected streams are stale immediately. The browser hides
  stale views and snapshots older than1.5s. This is gateway arrival freshness,
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
# First use only: enter username/password HERE, hidden locally, never in chat.
bash tools/run_am1_camera.sh --init-auth
bash tools/run_am1_camera.sh
```

If credentials already exist, omit `--init-auth`; it refuses to overwrite them.
Open **http://192.168.1.134:1984** in the Windows browser and authenticate.
The foreground helper prints the exact `CAMERA_LOG` under
`/home/pickmanmike/AlohaMini1Logs/am1-camera-<timestamp>-<suffix>.log`.
Runtime output goes directly to that file, not through the SSH terminal.
Optional separate read-only log view: `tail -n 3 -F <exact CAMERA_LOG>`.

For two minutes: confirm labels, click each mapped tile into primary, and
record observed fps/freshness. Once all five are mapped, unplug/replug **one
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
