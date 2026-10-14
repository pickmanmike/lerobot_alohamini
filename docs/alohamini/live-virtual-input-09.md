# AM1 live virtual input09 implementation note

Packet09 starts at integration `c5f7666c52d1e8fe4b44ed7a3ac6f81b25569ce1` on
`codex/am1-live-virtual-09`, in the existing isolated managed checkout. PR16–20
remain closed; this branch targets `integrate/am1-local-teleop` without merge
authority. Software durability means fault tolerance, preserved state, control
continuity and recovery with minimal human intervention.

The explicitly selected `physical-live-left-elbow` uses the existing interactive
mode and protected backend. The sole client target key is
`arm_left_elbow_flex.pos`. The Pi completes eleven fresh held arm targets and
body/lift zero after normal startup. Admission freezes measured q0 and its
quantized q0−10..q0 corridor; same-run reacquisition refreshes the measured cursor
without changing that reference or the native 90-second Live deadline. Existing
150 mA, moving45/startup30, calibration, P16/I0/D32 and genuine local guards stay
in force. Coarse response, return22 and fine2 are separate reported results.

`am1_session_contract.py` adds the explicit recipe. `am1_session_core.py` keeps
connect → empty release → qualified Resume → grant → input with current identity
and increasing sequence, separately tracking applied expiry and grant renewal.
`am1_session_service.py` fences disconnect release to the exact live connection.
Fake list input and finite task viewer continuity retain their own semantics.

`am1_physical_executor.py` owns one immutable latest valid target mailbox. Its
apply contract carries run/service/host/controller/connection/revision/grant/seq
and the original Pi grant deadline, never a replacement lifetime. The native
`AM1LocalControl` in `alohamini_host.py` and device write boundaries in
`alohamini.py` enforce that expiry even if owner scheduling stalls. Finite marker
v1 stays unchanged; explicit live marker v2 includes original sample binding and
Live admission. Native expiry latches measured hold/body zero. Fresh qualified
reacquisition fences the old binding. Selected-joint evidence gets a narrow live
branch retaining current, temperature, status, setting and response protections;
finite waveform assertions remain finite-only.

`session_transport.js` exposes two elbow jog buttons on the existing page, samples
current gestures after a valid grant, and streams at nominal10 Hz. WebSocket
buffering, pending grants and input promises are bounded; no target backlog or
remembered gesture survives release, blur, reattach, revision or takeover. Input
acceptance is separate from native write evidence and measured movement. Status
and ACKs contain bounded scalar summaries; existing hash-bound details carry
full evidence.

Ordered execution: test-first source changes in three disjoint areas; focused
shared gateway/adapter/native and loaded-browser verification; independent review;
existing isolated native-Pi checks; reviewed stopped matched component staging;
one finite locally watched ordinary-UI physical session with live reversal,
input gap, qualified same-run recovery and fresh movement; truthful cleanup,
usable idle, focused unmerged PR and exact deployment/rollback publication.

The first matched rollback is motor b68bfeee10df32c97865783bd4d37801f0006596,
runtime52f1e75a308097ec38b390bdac3ed695d6cd83cf and manifest
f35a2077a74f4c7c491a932be5a9a3700580d79da96fa4c6fb7f7c5f0ad7055a. Older matched
rollbacks, private state, independent pins and original results remain preserved.
No physical result is claimed by this implementation note. AM1 stays wireless,
P1 advisory, AM2 untouched; startup never starts or replays motor activity.
