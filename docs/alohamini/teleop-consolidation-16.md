# AM1 teleoperation consolidation — Packet16

Physical leaders are the primary input for ordinary teleoperation. An explicitly selected virtual-leader source substitutes at acquisition and uses the same mapping, authenticated transport and protected Pi-owned execution. The accepted elbow page presets remain useful diagnostic controls.

## Current status and source

PR24 was integrated normally into `integrate/am1-local-teleop` at `386d3c376c51d3431cf379af61ab99be95382ab8`, after the published tree matched the exercised product and retained checks/reviews. This follow-up starts at that actual tip on `codex/am1-teleop-consolidation-16` and remains unmerged. Main and AM2 are unchanged.

Implementation is in progress; no Packet16 physical qualification is claimed yet. The current accepted installation remains motor `5c97a39176b903e0c8762ed07e82113dc5a49b83`, runtime `978fadf4a4c41eb8a006dae283b337a0f47c10a0`, manifest `b8abad25e0233d152b8c87ab1d08531e6a73d63ee5942b1e29cd33ce60560aed`, helper/manual `0fe3997f12b9cdfdd01fda6f4c6b102781252448`, camera `a0e94bc5298c021f785eaaee4ef0e2c44283f3d7`. Prior matched rollback `4c1771a5 / b70ec7d2 / b17142c3` and older bundles remain retained. The current full set will be retained as the Packet16 matched rollback before staging.

## Shared input and motor lessons

- Whole input frames contain exactly twelve calibrated arm/gripper targets plus the existing body/lift intent. The original acquisition/grant age, source identity/sequence and control generation survive transport to the native prewrite guard. A new grant cannot refresh an old sample.
- Raw feedback and original per-bus acquisition intervals remain separate from normalized targets. Calibration and direction define encoding; gripper opening, arm angle, wheel velocity and lift movement retain distinct units.
- A persistent vector cursor accumulates low-rate motion coherently. Actual Start pose and qualified clutch/rejoin prevent jumps without rewriting calibration offsets or requiring a historical twelve-joint fingerprint.
- Normal installed settings and real health/current/thermal/status protections remain role-specific. Elbow P16 evidence and its diagnostic500mA ceiling are not blanket tuning. Gripper contact, wheel velocity and lift home/relief retain their existing semantics.
- Routine logs and selected-video encoding use bounded workers outside the motor loop; required command receipts and faults remain explicit. Weak small-current-rise trends remain diagnostic where supported, while real device faults retain their first cause.

Packet14 demonstrated coarse6–8.3degree elbow movement for nominal10degree commands, with fine residual remaining. It did not prove household-task capability or justify a10/6 command scaling factor. Earlier experiments and their superseded restrictions remain in [Packet14](practical-controls-14.md), [Packet13](practical-motion-13.md) and the labeled historical [session report](roaming-resilient-sessions.md).

## Onboard viewing

One stopped format inventory found all five roles advertise MJPEG/YUYV, including640×480 at30fps, and no native H264 format. Installed go2rtc1.9.14 and ffmpeg7.1.5/libx264 will serve one modest selected-camera encode shared by viewers, using the existing exclusive capture owner. Rotation/aspect ratio and required local JPEG acquisition metadata remain intact. Selected WebRTC viewing becomes the default; prior MJPEG viewing remains explicit rollback. Decoded progress, source age and capture/encode generations qualify visibility separately from control health. P1 remains advisory.

## Verification and ordinary use

Focused implementation checks, exact adapter commands, installed component/rollback identities and measured combined-operation results will be recorded here after source review and staging. The representative exercise uses virtual leader-format input, roughly180 active seconds under a newly declared fixed300-second native ceiling, original250ms input freshness, individual then combined arm/gripper movements, and a separate arms-held existing raised-base body recipe. It includes one selected-view switch/reconnect, one controlled input interruption/fresh rejoin, and a brief next Start. Producer program completion and native interactive Stop are separate results.

The current Windows USB inventory enumerates no serial ports. Physical USB identity/stationary sampling and actual human leader movement are therefore unverified. Virtual success will not be reported as moving-leader validation. After automated qualification, the remaining human check is a short fresh-clutched leader movement within the supported workspace; no object, floor travel, tuning or manual follower positioning is required by this packet.

AM1 stays wireless. Persistent service startup restores an idle interface and never starts or replays motor activity. Software durability here means continuity, state preservation and recovery; it does not mean mechanical lifespan. Internet telepresence, grasping, VR, autonomy and human ergonomics remain later task-level checks.
