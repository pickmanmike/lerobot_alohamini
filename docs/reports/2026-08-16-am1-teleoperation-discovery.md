# Aloha Mini 1 Complete Teleoperation Discovery Report

**Discovery date:** 2026-08-16

**Scope:** Read-only Windows, repository, Raspberry Pi, USB/camera, service, and network discovery for Aloha Mini 1

**Codex model:** `gpt-5.6-sol`

**Reasoning effort:** `xhigh`

## 1. Executive Summary

The existing split architecture is sound: the Raspberry Pi owns follower arms, base, lift, cameras, and local watchdog behavior, while the native Windows client owns the two passive leaders and keyboard commands. Local ZMQ transport is already divided into command TCP 5555 and observation TCP 5556. The next safe step remains the already-approved Windows startup-synchronization implementation.

The camera discovery established four usable native-MJPEG cameras, not five. All four enumerate behind one VIA four-port USB2 hub on the Waveshare PCIe USB board. They can stream concurrently at a requested 640×480 MJPG/30 profile without an observed reset, power warning, or single-camera slowdown, but they delivered about 14 frames per second under the current taped/dark scene. The fifth physically connected camera did not enumerate. Camera roles cannot yet be assigned because two wrist cameras were intentionally covered with blue tape while glue dried and two observed images were nearly black.

The Pi currently has no camera gateway, Cloudflare connector, relevant systemd service, or active listener on 5555, 5556, or 1984. The current AM1 host watchdog stops base and lift after a one-second command gap, but the next ordinary action may resume without a new operator authorization. Reliable claim/ARM/DISARM transport has not been designed and must be separated from latest-only action delivery before remote physical commissioning.

### Ranked gaps

1. The fifth camera is absent from USB/V4L2 enumeration.
2. The four visible camera devices cannot be mapped to semantic roles until tape is removed and all five views are human-verified.
3. The approved AM1 startup-synchronization production change is not yet implemented.
4. The current host accepts later ordinary commands after watchdog stopping without a fresh re-arm gate.
5. Reliable lifecycle-message delivery is not designed; the conflated action path is latest-only.
6. Lift current and temperature after lower-stop homing remain physically unmeasured; current code commands no backoff.
7. `cloudflared`, camera/status services, host orchestration, firewall policy, and the protected viewer are not deployed.

## 2. Evidence Classification and Safety Boundaries

This report distinguishes:

- **Observed:** Direct output from the Windows checkout or authorized read-only Pi inspection.
- **Measured:** Results from the explicitly authorized bounded camera still/capture probes.
- **Source-derived:** Behavior documented by an official project or directly read from repository source.
- **Inference:** A project-specific conclusion drawn from observed or source-derived evidence.
- **Unresolved:** A fact that requires later hardware, account, or sustained-load work.

No motor serial device was opened. No robot host, calibration, motor, lift, ZMQ-control, GPIO, or Cloudflare command was run. No package or service was installed, started, stopped, enabled, disabled, or modified. All camera probes were performed while motor power was off and no camera owner was present.

## 3. Windows and Repository State

### Git state observed before documentation materialization

| Item | Value |
|---|---|
| Repository | `C:\Users\pickm\lerobot_alohamini_client` |
| Branch | `fix/am1-startup-sync` |
| Commit | `6b97cf3882980b917528ab0d9a9e7efec769d69d` |
| Commit subject | `docs(alohamini): plan AM1 startup synchronization` |
| Describe | `v0.6.0-28-g6b97cf38` |
| Worktree | Clean |
| Planned documentation branch | `plan/am1-complete-teleoperation` |
| Repository version | LeRobot `0.6.1` |

The local commit includes the approved startup-synchronization design and implementation plan. The Pi-host code is unchanged between the deployed Pi commit and this Windows commit; the local changes after the Pi commit are Windows/client planning and action-space work.

### Windows runtime and dependencies

| Component | Observed version/state |
|---|---|
| Python | 3.12.10 |
| uv | 0.11.29 |
| `lerobot` | 0.6.1 |
| `numpy` | 2.2.6 |
| `opencv-python-headless` | 4.13.0.92 |
| `opencv-python` | Not installed |
| `pyzmq` | 27.1.0 |
| `feetech-servo-sdk` | 1.0.0 |
| `torch` | 2.11.0 |
| `torchvision` | 0.26.0 |
| `pynput` | 1.8.2 |
| `rerun-sdk` | Not installed |

`uv pip check` reported compatible installed packages. Leader calibration remains identified by `so101_leader_bi`, with COM7 for the left leader and COM8 for the right leader. Existing calibration files were not opened for modification and remain reusable.

## 4. Raspberry Pi State

### Platform and repository

| Item | Observed value |
|---|---|
| Hostname | `AlohaMini1-RPi5` |
| Hardware | Raspberry Pi 5 Model B Rev 1.1 |
| OS | Debian GNU/Linux 13 (trixie), Debian 13.6 |
| Kernel | `6.18.34+rpt-rpi-2712` |
| Architecture | aarch64 |
| System Python | 3.13.5 |
| Repository venv Python | 3.12.13 |
| Repository | `/home/pickmanmike/lerobot_alohamini` |
| Branch | `fix/am1-safe-bringup` |
| Commit | `a8538bd79356b4c5263342aba389dcdf39092e9e` |
| Commit subject | `fix(alohamini): make AM1 bring-up safe` |
| Worktree | Clean |

The inspected SSH snapshot was taken shortly after a reboot. User `pickmanmike` belongs to the required `video` and `dialout` groups. Group membership was inspected only; no serial endpoint was opened.

### Pi Python environment

| Component | Observed version/state |
|---|---|
| `lerobot` | 0.6.1 |
| `numpy` | 2.2.6 |
| `opencv-python-headless` | 4.13.0.92 |
| `opencv-python` | Not installed |
| `pyzmq` | 27.1.0 |
| `feetech-servo-sdk` | 1.0.0 |
| `torch` | 2.11.0+cu128 |
| `torchvision` | 0.26.0+cu128 |
| `pynput` | 1.8.2 |
| `rerun-sdk` | Not installed |

### Services and network

- No loaded or installed unit was found for Aloha Mini, camera streaming, go2rtc, µStreamer, MediaMTX, or `cloudflared`.
- `cloudflared` was absent from `PATH`, the Debian package database, and `/etc/cloudflared`.
- `nftables`, `ufw`, and `firewalld` were inactive.
- Only TCP 22 and RPC port 111 were listening after reboot.
- No process listened on 5555, 5556, or 1984.
- `wlan0` held `192.168.1.134/24`; `eth0` was down; the default route used `192.168.1.1`.
- No router-forwarding or public-service conclusion was inferred from the host-only inspection.

Cloudflare tunnel management style, token shape, account policy, and private hostname do not yet exist on this Pi. No credential or token file was read.

## 5. Power, Thermal, and Kernel Evidence

| Check | Before/after bounded probes |
|---|---|
| `vcgencmd get_throttled` | `0x0` before and after |
| Temperature | Approximately 44.4°C initially; 45.5°C after probes |
| Core voltage | Approximately 0.7621 V |
| USB reset/disconnect | None observed |
| UVC ENOSPC | None observed |
| Undervoltage/over-current | None observed |

Kernel logs contained repeated UVC compliance warnings for unsupported `GET_INFO`/`GET_DEF` controls and permanently disabled red/blue balance controls. These were control-firmware quirks, not an observed stream reset or power event.

The clean `get_throttled` result applies only to this short four-camera probe. It does not qualify five cameras, sustained bright-scene MJPEG, simultaneous recording, or motor load.

## 6. PCIe and USB Topology

### Controllers and root buses

- `0001:01:00.0`: VIA VL805/806 xHCI USB 3 controller on the Waveshare PCIe USB expansion board.
- `0002:01:00.0`: Raspberry Pi RP1 I/O controller.
- Bus 1: VIA USB2 root → VIA `2109:3431` four-port USB2 hub → four `0c45:1915` cameras.
- Bus 2: VIA USB3 companion, empty during discovery.
- Bus 3: RP1 USB2 → one `1a86:55d3` USB serial controller and the Waveshare WS170120 touchscreen.
- Bus 4: RP1 USB3 companion, empty.
- Bus 5: RP1 USB2 → the second `1a86:55d3` USB serial controller.
- Bus 6: RP1 USB3 companion, empty.

All four enumerated cameras therefore share one 480 Mb/s USB2 segment and one four-port hub. The fifth physical camera was not visible in `lsusb`, `lsusb -t`, V4L2 enumeration, or `/dev`. The current topology has neither a fifth port nor controller margin on that hub.

The original ALOHA “two cameras per hub” guidance is treated as a latency heuristic, not a hard AM1 rule. Packet 4 must use measured five-camera behavior and preserve motor-controller reliability when choosing the final approximately 2–2–1 distribution.

## 7. Video-Node Inventory

All nodes were owned by `root:video` with mode `0660`. No camera process owned them before or after discovery. The four capture devices share the identical serial string `Sonix_Technology_Co.__Ltd._USB_2.0_Camera_SN0001`, so serial-based `/dev/v4l/by-id` links collide and are unsafe for semantic identity.

| USB device | Exact path/tag | Nodes and capability | Useful formats | Observed view | Occupied | Proposed alias |
|---|---|---|---|---|---|---|
| `0c45:1915`, `SN0001` | `platform-1000110000.pcie-pci-0001:01:00.0-usb-0:1.1:1.0`; tag `platform-1000110000_pcie-pci-0001_01_00_0-usb-0_1_1_1_0` | `/dev/video0` capture; `/dev/video1` metadata | MJPG 640×480@30 candidate; MJPG 1280×720@30 available | Blurry room-facing | No | Unresolved pending five-view map |
| `0c45:1915`, `SN0001` | `...usb-0:1.2:1.0`; tag `...usb-0_1_2_1_0` | `/dev/video2` capture; `/dev/video3` metadata | Same | Close blue-tape-obscured | No | Unresolved pending five-view map |
| `0c45:1915`, `SN0001` | `...usb-0:1.3:1.0`; tag `...usb-0_1_3_1_0` | `/dev/video4` capture; `/dev/video5` metadata | Same | Nearly black | No | Unresolved pending five-view map |
| `0c45:1915`, `SN0001` | `...usb-0:1.4:1.0`; tag `...usb-0_1_4_1_0` | `/dev/video6` capture; `/dev/video7` metadata | Same | Nearly black | No | Unresolved pending five-view map |

The full common `ID_PATH` prefix for rows 2–4 is `platform-1000110000.pcie-pci-0001:01:00.0-`; the tag prefix is `platform-1000110000_pcie-pci-0001_01_00_0-`.

Platform nodes `/dev/video19` through `/dev/video35` belong to Raspberry Pi codec/PISP devices. They are not USB camera capture or metadata nodes and must not receive AM1 camera aliases.

### Common supported modes

Capture nodes `/dev/video0`, `/dev/video2`, `/dev/video4`, and `/dev/video6` reported:

| Pixel format | Modes relevant to this project |
|---|---|
| MJPG | 1280×720@30, 800×600@30, 640×480@30, 320×240@30 |
| YUYV | 640×480@30, 1280×720@10, 800×600@20, 320×240@30 |

Native MJPG 640×480 is the planned starting profile. Five raw YUYV 640×480@30 streams would require approximately 737 Mb/s before USB overhead and cannot fit one USB2 segment. Native MJPEG is variable-rate and must be measured under realistic motion and lighting.

### Common controls observed

| Control | Current/default observation |
|---|---|
| Brightness | 0, range -64..64 |
| Contrast | 50, range 0..64 |
| Saturation | 70, range 0..128 |
| Hue | 8, range -40..40 |
| Auto white balance | Enabled |
| Gamma | 100, range 72..500 |
| Gain | 0, range 0..100 |
| Power-line frequency | 60 Hz |
| Sharpness | 10 |
| Backlight compensation | 3 |
| Auto exposure | Aperture priority |
| Exposure absolute | 157, inactive under auto exposure |
| Dynamic frame rate | Current 1, default 0 |

No control was changed. Dynamic exposure and the taped/dark views are plausible contributors to the observed 14 fps, but that remains an inference.

## 8. Bounded Camera Measurements

### Identification stills

With explicit authorization, one still was captured from each unused capture node, copied transiently to Windows for inspection, and deleted from both machines.

- `/dev/video0`: blurry room-facing image.
- `/dev/video2`: close view obscured by blue tape.
- `/dev/video4`: nearly black.
- `/dev/video6`: nearly black.

The user reported that the two wrist cameras were partially or fully covered by blue tape while glue dried. No left/right or other semantic role was inferred from these images. The one-still-per-camera allowance was consumed; no further still was taken.

### Four-camera simultaneous native-MJPEG probe

Each capture node was asked for 300 frames at MJPG 640×480@30 and streamed to a byte counter rather than a retained file:

| Node | Bytes | Elapsed |
|---|---:|---:|
| `/dev/video0` | 16,474,128 | 21.573 s |
| `/dev/video2` | 14,483,344 | 21.573 s |
| `/dev/video4` | 3,176,744 | 21.676 s |
| `/dev/video6` | 3,057,672 | 21.126 s |

A separate `/dev/video0` single-camera probe transferred 16,911,840 bytes in 21.122 seconds. The approximately 14 fps result therefore did not worsen materially when the other three cameras were active. Measured aggregate payload was approximately 13.8 Mb/s for the current scene.

This proves only that four feeds coexist for roughly 22 seconds at the observed exposure and scene complexity. It does not prove five-camera enumeration, bright-scene worst-case bitrate, long-term stability, browser latency, recording coexistence, or powered-hub sufficiency.

## 9. Existing Camera Ownership and Transport

AM1 currently constructs configured OpenCV cameras inside `AlohaMini`. The host calls `async_read()` for each configured camera, decodes frames, software-encodes each frame to JPEG quality 70, and sends state plus binary JPEG frames over the observation ROUTER socket. The Windows client receives and decodes those frames.

The path is therefore:

```text
UVC camera → OpenCV decode → Pi JPEG70 encode → ZMQ multipart → Windows JPEG decode
```

`--no_cameras` replaces the robot camera configuration with an empty mapping before construction, so it is suitable for a motor host that must not open camera devices.

The generic LeRobot ZMQ image server is not selected for the viewer because it software-encodes JPEG, base64-expands images into combined JSON, defaults to the command port 5555, and may fall back to the first image when a requested name is absent. Those properties add overhead and create a code-derived wrong-camera risk for partial messages.

Planned teleoperation ownership is exclusive:

- Standalone camera gateway owns all five cameras.
- AM1 motor host runs with `--no_cameras`.
- Recording stops the gateway before the existing camera-owning recording path starts.
- No two processes intentionally open the same V4L2 capture node.

## 10. Existing ZMQ Lifecycle and Watchdog

### Current transport

| Port | Host/client pattern | Purpose |
|---|---|---|
| TCP 5555 | Client PUSH → host PULL, `ZMQ_CONFLATE=1` | Latest action dictionary |
| TCP 5556 | Client DEALER → host ROUTER | Request/reply state and JPEG observations |

The host binds both sockets to all interfaces. The action payload has no authentication or session ownership. Observation requests use a bounded window of three. The Windows client uses a 200 ms observation timeout and a five-second initial connection timeout.

### Watchdog behavior

After a one-second command gap, the host calls `robot.stop_motion()`. Current AM1 behavior stops base/lift while follower position controllers retain their last goals. The host then accepts the next ordinary action without an explicit claim or re-arm transition. This is acceptable only for bounded local commissioning under the existing procedures; it blocks remote physical commissioning.

Lifecycle messages must not be added to the same conflated latest-action stream without a separate protocol design. Packet 9 must choose an acknowledged reliable channel for claim/ARM/DISARM and establish how it interacts with the latest-only arm-action path.

## 11. Cloudflare State and Source-Derived Direction

No Cloudflare component or configuration currently exists on the Pi. The recommended future path is Windows Cloudflare One Client/WARP → named remotely managed Tunnel → private hostname route. Raw ZMQ ports must not become public hostnames, and normal control must not depend on client-side `cloudflared access tcp`.

Current Cloudflare documentation requires a sufficiently recent connector for private hostnames, appropriate WARP Traffic and DNS mode, split-tunnel/Gateway configuration, and explicit policies because a private route alone is not a complete authorization boundary. The private-application/policy count remains unresolved until the reliable session protocol selects its final port/channel layout.

The Pi firewall must not be restricted before synthetic routing works and the actual connector-side source address/interface is observed. The first firewall application must preserve SSH from the administrative LAN address, retain an open recovery session, and use a time-bounded rollback.

## 12. Official Sources Consulted

| Source/version | Material conclusion |
|---|---|
| `liyiteng/AlohaMini` main at `17c6a98d79881a45ab869c1f392ed89c0723a298` | Five AM1 camera roles and Pi-centered hardware architecture. |
| Deployed `liyiteng/lerobot_alohamini` at `a8538bd79356b4c5263342aba389dcdf39092e9e` | Current host/client lifecycle, ZMQ ports, camera serialization, keyboard body control, and recording path. |
| `TheRobotStudio/SO-ARM100` at `7629d2ad9853d10fb903093a33ef6114099d97e5` and current LeRobot SO-101 docs | Persistent calibration identity, direct mirroring assumptions, and hardware/power distinctions. |
| LeRobot Isaac teleop snapshot `6adf51511b7625090eade8d82d9f61a1846ebe56` | Alignment/slew, measured anchoring, stale hold, engagement, and cleanup behavior to adapt rather than copy. |
| LeKiwi official docs | Pi host/laptop client, 5555/5556, keyboard mobility, and platform limitations. |
| Original ALOHA `06369f03cd8e0a47e16d3a90167853fd33af7557` and Mobile ALOHA `0e40324` | Semantic aliases, exclusive ownership, hub heuristic, and staged recording. |
| XLeRobot `3d14695e40c9c68229c0aacffca6053c75cd3eb6` | Operator workflow/status ideas, not an AM1 runtime replacement. |
| Phosphobot and LeLab | Browser/status and recording ideas, not browser motion control. |
| LeRobot Cameras, ZMQCamera, and streaming encoding docs | Semantic camera interfaces and bounded queues; dataset encoding is not the live viewer transport. |
| go2rtc v1.9.14 | Native V4L2 MJPEG, preload, MJPEG/snapshot APIs, static directory, API allowlist, and authentication controls. |
| µStreamer v6.62 | Native MJPEG passthrough fallback with one process per camera. |
| MediaMTX v1.20.0 | Media router rather than direct USB capture; generic webcam recipe transcodes. |
| Raspberry Pi USB/power and RP1 docs | Controller topology, downstream power budgets, and `get_throttled`. |
| Linux V4L2 docs | Dynamic node numbering, multiple nodes per physical camera, and exclusive streaming behavior. |
| Cloudflare Tunnel/private-hostname/Access/Gateway/Mesh docs, current 2026-08-16 | Private routing, policy, long-lived TCP, connector service, and measured Mesh fallback. |
| OpenAI latest-model documentation | Codex model context recorded as `gpt-5.6-sol`, effort `xhigh`. |

Primary links are collected in the companion master plan beside the decisions they support.

## 13. Discovery Commands

The SSH wrapper was always:

```powershell
ssh -i C:\Users\pickm\.ssh\id_ed25519_am1_codex `
  -o BatchMode=yes `
  pickmanmike@192.168.1.134 "<read-only-command>"
```

No private-key content was read or printed.

### Windows/repository commands

```powershell
git status --short
git branch --show-current
git rev-parse HEAD
git describe --tags --always --dirty
git log --format="%H %ad %s" --date=iso-strict
git diff --name-only a8538bd79356b4c5263342aba389dcdf39092e9e..HEAD
uv --version
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -m pip show lerobot numpy opencv-python opencv-python-headless pyzmq feetech-servo-sdk torch torchvision pynput rerun-sdk
uv pip check
```

### Pi identity, repository, runtime, and service commands

```bash
hostname
date --iso-8601=seconds
uptime
cat /proc/device-tree/model
cat /etc/os-release
uname -a
python3 --version
~/lerobot_alohamini/.venv/bin/python --version
git -C ~/lerobot_alohamini status --short --branch
git -C ~/lerobot_alohamini rev-parse HEAD
git -C ~/lerobot_alohamini log -1 --format='%H%n%aI%n%s'
~/lerobot_alohamini/.venv/bin/python -m pip show lerobot numpy opencv-python opencv-python-headless pyzmq feetech-servo-sdk torch torchvision pynput rerun-sdk
cloudflared --version
systemctl show cloudflared --no-pager --property=LoadState --property=ActiveState --property=UnitFileState --property=FragmentPath
systemctl list-unit-files --no-pager
systemctl list-units --type=service --state=running --no-pager
ps -eo pid,ppid,user,stat,lstart,args
ss -ltnp
ip -brief address
ip route
```

### USB, V4L2, power, and error commands

```bash
lsusb
lsusb -t
lspci -nnk
v4l2-ctl --list-devices
find /dev/v4l -maxdepth 2 -type l -printf '%p -> %l\n'
ls -l /dev/video* /dev/v4l/by-id/* /dev/v4l/by-path/* /dev/am_camera_*
udevadm info --query=property --name=/dev/video0
udevadm info --query=property --name=/dev/video1
udevadm info --query=property --name=/dev/video2
udevadm info --query=property --name=/dev/video3
udevadm info --query=property --name=/dev/video4
udevadm info --query=property --name=/dev/video5
udevadm info --query=property --name=/dev/video6
udevadm info --query=property --name=/dev/video7
v4l2-ctl -d /dev/video0 --all --list-formats-ext --list-ctrls-menus
v4l2-ctl -d /dev/video2 --all --list-formats-ext --list-ctrls-menus
v4l2-ctl -d /dev/video4 --all --list-formats-ext --list-ctrls-menus
v4l2-ctl -d /dev/video6 --all --list-formats-ext --list-ctrls-menus
fuser -v /dev/video0 /dev/video2 /dev/video4 /dev/video6
vcgencmd get_throttled
vcgencmd measure_temp
vcgencmd measure_volts core
journalctl -k -b --no-pager
```

The bounded capture used `v4l2-ctl` with explicit MJPG, 640×480, requested 30 fps, `--stream-count=300`, and output directed to a byte counter or temporary `/tmp` still. Temporary stills were copied only after explicit authorization and were deleted afterward.

### Final cleanup checks

```bash
fuser -v /dev/video0 /dev/video2 /dev/video4 /dev/video6
pgrep -af 'go2rtc|ustreamer|ffmpeg|v4l2-ctl'
find /tmp -maxdepth 1 -type f -name 'am1-camera-video*.jpg' -print
ss -ltnp '( sport = :5555 or sport = :5556 or sport = :1984 )'
git -C /home/pickmanmike/lerobot_alohamini status --short --branch
git -C /home/pickmanmike/lerobot_alohamini rev-parse HEAD
vcgencmd get_throttled
```

The corresponding Windows cleanup check confirmed that `.codex-camera-discovery` did not exist.

## 14. Discovery Completion State

- Windows and Pi repositories remained clean at their expected commits.
- No production or test file changed.
- No package, system service, firewall, or Cloudflare account state changed.
- No motor port was opened and no hardware moved.
- No camera process or temporary still remained.
- No credential, tunnel token, or private-key content appears in this report.
