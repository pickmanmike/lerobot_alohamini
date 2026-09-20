# CAMERA-VIEW1 — Motor-off local camera-viewing packet

> Prepared next packet, not executed by the POSTQ closure review. Follow the lean process: one focused implementation/review run, affected tests only, and one useful human camera-viewing session. No motor commissioning repeat.

**Goal:** View the five AM1 camera roles from a Windows browser on the LAN, independently of the motor host.

**Architecture:** One Pi camera owner/gateway, native compressed MJPG, a small authenticated read-only browser dashboard. No browser motion, motor-host camera capture, recording or remote transport.

**Approved direction:** Sections 5 and Packets 4–5 of `docs/superpowers/plans/2026-08-16-am1-complete-teleoperation-master-plan.md`, preserved in Git commit `f5b23ff1d353dbcc07bb4b25942f074be5f10fb2`. Read that camera scope with `git show f5b23ff1d353dbcc07bb4b25942f074be5f10fb2:docs/superpowers/plans/2026-08-16-am1-complete-teleoperation-master-plan.md`. Its historical motor status, deployed baseline and old COM assignments are superseded by the current AM1 runbook; do not restore them.

## Start state and boundaries

- Start a focused `feature/am1-camera-viewing` branch from the updated `integrate/am1-local-teleop` after PR #5's ordinary merge. Preserve every motor branch/worktree and existing environment.
- Human switches follower/body and both leader motor supplies OFF. Motor host stays stopped; no serial/COM access, homing, motion, calibration or ZMQ launch.
- Pi/camera USB power may be on for this separately authorized camera packet. Secure all cameras; remove covering only when glue/assembly work is complete; connect the intended fifth camera if still absent. Do not disturb labeled motor-controller ports or wiring.
- The last saved discovery found four cameras, identical `SN0001` serials, unresolved views and a shared USB2 hub. These are historical facts, **not** a current five-camera inventory or accepted semantic map.
- No Cloudflare, firewall, remote control, autonomy, battery work or motor-setting changes. No boot-enabled services in this first viewing packet. Package/binary installation, udev/system configuration and credential placement retain their explicit approval gates; no broad reinstall.

## 1. Establish actual camera identity and exclusive ownership

- [ ] Confirm the exact integration starting SHA and clean worktree; inspect existing camera tools/configuration before adding anything.
- [ ] With the motor host stopped and motor power off, collect one current inventory: physical USB paths, capture versus metadata nodes, native formats, existing owners and installed viewer binaries. Do not assume `/dev/videoN` is stable.

```bash
cd /home/pickmanmike/lerobot_alohamini
git branch --show-current
git rev-parse HEAD
git status --short
if pgrep -af '[l]erobot.robots.alohamini.alohamini_host'; then
  echo 'STOP: motor host must be stopped for this camera-only packet.'
  exit 2
fi
command -v v4l2-ctl
command -v go2rtc || true
command -v ustreamer || true
ls -l /dev/v4l/by-path/
v4l2-ctl --list-devices
```

- [ ] Use one bounded native-MJPG preview per observed capture node and a human view check to identify `forward`, `backward`, `chest`, `wrist_left`, `wrist_right`. Keep images/logs outside Git. Missing fifth camera or ambiguous views is an identity gate, not a reason to invent an alias or alter motor software.
- [ ] Prepare path-based aliases `/dev/am_camera_forward`, `/dev/am_camera_backward`, `/dev/am_camera_chest`, `/dev/am_camera_wrist_left`, `/dev/am_camera_wrist_right` from the measured capture-node paths. Match video index 0 and actual path, not shared serials. Install rules only after approval; verify reconnect persistence within the camera-only session.

## 2. Implement the smallest local viewer

- [ ] Reuse an existing compatible viewer if already present. Otherwise follow the approved pinned go2rtc v1.9.14 direction, checking its actual configuration/API behavior and binary provenance before deployment. Do not install a newer dependency set by default.
- [ ] Keep code in camera-only assets/helper/configuration/tests plus camera documentation. Do not modify `teleoperate_bi.py`, `alohamini_host.py`, motor/leader classes or the accepted Local launch arguments. A helper must support a hardware-free print/config-validation mode and use timestamped logs outside Git.
- [ ] Request native `MJPG`, 640x480 at 30 fps; report delivered rather than requested rate. Preload all five sources. Provide one primary MJPEG view, four snapshots at 2 Hz, click-to-swap, and visible freshness/stale status. No server-side image re-encoding, audio, DVR or recording.
- [ ] Expose only authenticated dashboard/assets, `/api/stream.mjpeg?src=<role>`, `/api/frame.jpeg?src=<role>&cache=500ms` and read-only status on LAN port 1984. Store credentials outside Git and logs; use `local_auth: true`. Browser controls only select a view; they never command motors.
- [ ] Positively test required paths and reject requests without credentials. With valid viewer credentials, negatively test `/api`, `/api/config`, `/api/streams`, mutation/restart, logs/debug, WebSocket, discovery and other administration paths. Disable RTSP/WebRTC/FFmpeg/exec; no 8554/8555 listeners.
- [ ] If functional viewing cannot coexist with the required deny rules or camera recovery, use the approved five µStreamer v6.62 instances behind a narrow authenticated gateway; do not expose go2rtc administration to make the dashboard work.
- [ ] Apply focused RED/GREEN tests to any new code: missing/duplicate role refusal, strict route allowlist, authentication failure, stale snapshots, one-camera failure isolation and cleanup. Validate helper syntax/configuration, complete diff and secrets/artifacts. Use existing environments; do not rerun robot direction/calibration suites.

## 3. One human-operated viewing acceptance

- [ ] Return one short camera-helper start/stop procedure and exact log location before opening cameras. The motor host remains OFF throughout.
- [ ] Human opens `http://192.168.1.134:1984`, authenticates, confirms all five physical views/labels and clicks each thumbnail into the primary. Observe for a bounded two-minute interval with normal lighting; requested 30 fps alone is not acceptance. Target primary at least 12 fps, no stall over 500 ms, thumbnail age below 1.5 s; retain actual measurements.
- [ ] Within that same motor-off session, disconnect/reconnect one labeled camera only. Other views remain live; affected view clearly becomes stale and recovers within 10 seconds. No USB reset or Pi power warning is acceptable. Do not unplug a motor controller.
- [ ] Stop the camera owner normally, verify devices released, and retain logs/role mapping outside Git. Failure stops at that camera boundary only; accepted arms/base/lift/Local evidence stays closed.

**Deliverable:** focused draft camera PR against `integrate/am1-local-teleop`, exact source/config/binary versions, measured role-to-path map, positive/negative HTTP results, actual stream/freshness/resource observations and concise start/stop commands. Do not claim camera-motion co-load, boot persistence or remote operation from this motor-off pass. Those are later gates, not reasons to reopen completed motor commissioning.

**Immediate next human action:** keep all motor supplies off, leave the motor host stopped, uncover/secure/connect the intended cameras, and authorize CAMERA-VIEW1. No camera acquisition, installation or service change was performed while preparing this packet.
