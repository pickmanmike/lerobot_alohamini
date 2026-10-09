# AM1 roaming-resilient sessions

**AM1-SESSION-ARCHITECTURE-01 · 2026-10-09 · owner-approved design; stage 1 implemented, fake-only.**
Choose a Pi-owned session authority and finite executor, an authenticated LAN
HTTPS/WebSocket gateway, and independently supervised P1 observation. Reuse the
existing UI and protected motor backend. SSH remains administrative access.

The owner approved this design and the exact stage-1 fake-only implementation.
Stage 1 is implemented on `codex/am1-persistent-session-fake`. Whole-branch
review completed and identified one cross-client Pause ordering defect; its
correction and covering evidence are recorded below. The stacked follow-up is
a fake-only draft checkpoint. This does not qualify or deploy stages 2–4.
The owner replaced the proposed connection-continuity handoff and canceled the
Wi-Fi Roaming Aggressiveness experiment and its administrator question. Do not
resume that transaction. No deployed motor/camera owner, adapter, firewall, listener or private
configuration was changed; stage 1 uses dedicated loopback development listeners.

## Verified baseline and remaining acceptance

Source inspection: clean `codex/am1-reliability-02` at
`6e50fd6b2db95d62df130dda1a21764afaf1dae8`; [PR #16](https://github.com/pickmanmike/lerobot_alohamini/pull/16)
is open/draft against `integrate/am1-local-teleop`
`9aa6d3b042a92e538213b0c684fd52d000b9cc7c`. The three published continuation
reviews were read; the owner's architecture packet supersedes the latest
adapter-experiment recommendation. Historical merged #14/#15 are preserved. Remote main remains
`ab4462b713aeb24d0473f1ec6c8812290ab19510`; all three remote branch heads were
verified before publication. Source references below describe this inspected checkpoint.

| Component | Preserved configured / recorded pin | Verification in this design |
| --- | --- | --- |
| Windows served session | `e55d390bf6e957b9adc52ccf62b69476db12a20d` | Private session config and local checkout agree |
| Pi session helper | `8e6a0cf616cb2000df1d0e996ab27a19d2fb2fba` | Private config read; current live checkout unverified |
| Pi motor | `f03de9c3b8c4f1d9e69b6951584119113ca4c5da` | Private config read; current live checkout unverified |
| Pi cameras | `9b1f0670e7068f7d39eb50270a118e3807418355` | Private config read; current live checkout unverified |
| Latest recorded observer receiver | `22c5e8892911c5074936ef785a8d2a64a95fbe07` | Inspected candidate receiver SHA-256 `43b9caa554615366d1fc01aa1139f9c7f0767528abb911bf31efac62adb3eb78` |
| P1 capture source | Separate source artifact | Candidate SHA-256 `93d7fb0c35408d96dc4a3c68b14adc7146680c4752091e7c7eb1c5eeb2c449ce` |

One bounded read-only SSH query for Pi checkout heads returned 255. This does
not establish live ownership, torque or connectivity state. Exact host/device
identity, route, repository/environment paths, credentials and calibration stay
in the existing private configuration. P1 expected identity and exact camera
selection were checked privately; use that configuration during later
stopped-owner staging. A repair checkpoint is not a wholesale deployment pin.

Preserve normal-rest automatic startup, calibrated affine raw/normalized
feedback, inward target encoding, protected measured holds, post-home quiet
feedback qualification and verified selected-gain restoration. The selected
shoulder showed delayed small outward/return movement; tighter immediate return
and useful return of every joint remain unproven. The latest powered repeat
reached **326.754/352 trajectory seconds**, 338.913 seconds in Live, three
cycles/boundaries and three same-session observation recoveries. The fourth
exhausted its budget and caused ordinary Stop with cleanup verified. Subsequent
camera-only transport failures, original receiver verdicts and supplemental
source cleanup records remain separate. Replacement transport neither fixes a
proven RF cause nor upgrades those outcomes.

**Still open:** full four-cycle 352-second workload, comparable automatic Start
after ordinary Stop and resulting normal rest, and the existing 12-second
ArmHoldBody check. Architecture documentation does not complete reliability;
trajectory fraction is not reliability probability.

## Current path to target path

| Component / host | Current connection, state owner and disconnect consequence | Target seam |
| --- | --- | --- |
| [Console](../../tools/am1_console.py#L470), Windows | Loopback HTTP; in-memory adapter and browser token. Console exit requests Stop. Second Start can attach locally. Binding is deliberately 127.0.0.1-only. | Keep assets/presentation through a new remote transport adapter; viewers attach to the robot run. Do not expose this server by changing its bind address. |
| [Session coordinator](../../tools/am1_session.py#L350), Windows | Creates session, launches native client and SSH supervision; finally stops owner/collects evidence. [SSHRemote](../../tools/am1_session.py#L763) sends commands plus 1-second heartbeats. Established loss has no attach protocol. | Move identity, lifecycle, outcome and finite authorization to Pi; retain Windows path as stopped compatibility/rollback. |
| [RemoteSupervisor](../../tools/am1_session_remote.py#L375), Pi | Already owns camera/motor children, private state and cleanup. SSH stdin EOF or 6-second laptop lease expiry stops children. | Extract ownership and cleanup from the shell-bound command reader into a resident authority. Client presence ceases to be a finite-task lifetime lease. |
| [Native Local runtime](../../examples/alohamini/teleoperate_bi.py#L2226), Windows | Owns admitted sender, measured feedback, frozen scripted seed, trajectory and Live deadline; typed [AF_PIPE bridge](../../examples/alohamini/am1_console_bridge.py#L640) binds it to Windows/browser presence. | Portable Pi executor uses the same runtime/provider contracts; no Duffy process, AF_PIPE, Windows path, keyboard import or shell stdin in finite execution. |
| [Follower client](../../src/lerobot/robots/alohamini/alohamini_client.py#L114) and [host](../../src/lerobot/robots/alohamini/alohamini_host.py#L124) | **Motor commands and feedback already use ZeroMQ TCP**, not SSH per command. Windows PUSH/DEALER talks to Pi PULL/ROUTER; host owns serial devices and local hold/zero/watchdog/epoch guards. | Keep backend initially; sole Pi executor is its local client. Bind internal sockets to loopback and restrict access. Epoch markers are ordering guards, not authentication. |
| [Virtual bench](../../tools/am1_reliability_bench.cjs#L394), Windows/browser | Playwright focus, DOM decode, key events, health-file reads and Resume HTTP requests currently carry task/observation policy. | Keep as UI compatibility/integration harness; move deterministic body input and task recovery to Pi. A viewer never supplies synthetic presence to keep a task alive. |
| [P1 capture](../../tools/am1_observer_capture.ps1#L360) and [receiver](../../tools/am1_observer.py#L394) | One Windows WinRT owner records locally and copies current pixels. SSH launches/supervises source and forwards delivery to Windows health JSON. Laptop delivery loss can invalidate observation. | P1 has explicit independent source supervision; direct authenticated frame proof reaches a Pi decoder/policy worker. No laptop health file or forwarded socket supplies robot authority. |
| [UI input](../../tools/am1_console_ui/app.js#L57) and [model](../../tools/am1_console_model.py#L37) | Focus/hide/pagehide release interactive intent. Progress assumes shared Windows monotonic clock; receipts have Windows provenance. | Preserve interactive releases; finite viewer instantiates no input lease. Display Pi-owned remaining time/provenance, with receipt age and stale status. |

P1 remains an explicit Windows observation dependency. Its retained capture
adapter can still use PowerShell locally; **Pi execution has no Windows or
PowerShell launch dependency**. Administrative SSH failure, optional viewer
failure and real required-observation loss must be different events.

## Minimal architecture and authority

```mermaid
flowchart LR
  C["PC / tablet / phone viewers"] -->|"HTTPS + WSS"| G["Pi gateway: auth, UI, bounded snapshots"]
  L["Optional PC USB-leader adapter"] -->|"fresh authenticated intent"| G
  G <-->|"private bounded IPC"| A["Pi session authority + executor"]
  A <-->|"local-only ZeroMQ"| M["existing protected motor host"]
  P["P1 single WinRT capture + local recording"] -->|"direct authenticated current-pixel proof"| O["Pi observation worker"]
  O -->|"original freshness / decoded evidence"| A
  P --> E["one bounded live encoder + MediaMTX"]
  G <-->|"authenticated WHEP signaling proxy"| E
  E -->|"DTLS / SRTP WebRTC"| C
```

Use two robot processes: an OS-managed authority/executor and an independently
restartable gateway. The authority owns run state, one action sender, fixed
deadlines, admission, recovery, first fault and cleanup. Gateway HTTP, media
signaling, downloads and blocked browsers cannot block its control cadence.
The observer decode worker is separate from both motor cadence and media
encoding. Its bounded evidence mailbox expires locally; a stalled worker
withdraws coverage rather than publishing a fresh heartbeat as observation.

The smallest service stack is **aiohttp + Python stdlib** (`asyncio`, `ssl`,
`sqlite3`, bounded queues), alongside existing Pillow and pyzmq.
[aiohttp supports HTTP and WebSocket](https://docs.aiohttp.org/en/stable/web_quickstart.html);
the approved design baseline had it locked at 3.14.1 but undeclared for AM1
and absent from the shared environment. Stage 1 now declares the scoped
`am1-session` extra, preserving all 335 locked package versions. The developer
setup below uses a dedicated temporary HTTP overlay without synchronizing the
shared environment; do not rely on optional transitive installation.
FastAPI/Uvicorn and a separate WebSocket library are unnecessary. No ROS, MQTT,
cloud control plane, Kubernetes, GPU stack or new general autonomy framework.

On Pi, systemd starts the authority into **idle/reconciliation**, never motion.
Gateway restart is a client disconnect; authority/executor restart is a run
interruption. [systemd restart policy](https://github.com/systemd/systemd/blob/main/man/systemd.service.xml)
does not provide application resume semantics. Fake tests must establish them.
Use cooperative normal cleanup and existing local watchdogs; do not develop or
test a powered process freeze/cutoff under this design.

Keep the Pi supervisor's canonical `active.lock`, Windows controller OS lock
and camera `viewer.lock`/device checks. Stage 2 must make all supported legacy/new
launches use the **same canonical robot arbitration**, acquired before process
creation or configuration and held through verified cleanup. Add a serial-owner
kernel lock at the motor-host pre-connect boundary so direct supported host
launches cannot race the supervisor check; parent and child must not deadlock
by independently acquiring the same exclusive lock. Keep supervisor and
serial-device locks distinct. Mere lock-file presence or a process-name check
is insufficient. Unknown cleanup blocks new acquisition pending actual state
reconciliation. Preserve single-owner serial permissions and existing conflict
checks for unmanaged processes; application locks cannot constrain arbitrary
privileged external programs.

The new executor is the sole backend sender. No browser or PC adapter connects
directly to motor ZeroMQ, and no automatic fallback alternates owners after an
error. Keep the legacy owner stopped while the new path owns the robot.

## Disconnect and restart semantics

| Event | Required result |
| --- | --- |
| Viewer, laptop or administrative SSH disconnects | Accepted finite task continues **only while local feedback and required observation qualify**; its original deadline/progress remain. Reattach displays the same run without Start/home/sync. |
| Interactive controller input stops arriving | Existing 250 ms input expiry clears targets/body intent; local measured hold/zero and release/alignment gates apply. The 1.5 s presence lease can expire independently. Never replay old velocities or leader targets. |
| Required P1/required-role proof expires | Freeze trajectory and hold through the existing policy. Recover only the original qualified episode/proof within its unchanged budgets. Optional view quality cannot grant coverage. |
| Multiple viewers / controller handoff | Viewing acquires no control. One atomic claim rotates controller generation after expiry or deliberate release; no implicit takeover on reconnect. Old tab cannot reclaim with old credentials/generation. |
| Explicit Pause or Stop | Higher precedence than automated recovery and progression. Pause freezes trajectory; Stop is latched for that run and performs normal cleanup. Any later recovery proof predating operator intent is revoked. |
| Gateway restarts | Authority continues if qualified; clients attach to snapshot first. New connection grants invalidate buffered connection input. |
| Authority, executor or Pi restarts | Persisted work is `interrupted` or `uncertain`, never auto-resumed/restarted. New service generation invalidates leases/grants. Reconcile actual owner/motor state before any later deliberate Start. |
| P1 capture restarts | New capture generation cannot inherit old coverage; requalify current pixels and recording. Delivery reconnect to the same capture is not source restart. |

Retain distinct budgets: 352 trajectory seconds within the original 420-second
Live ceiling; measured preparation has its own existing 20-second wall budget;
virtual recovery is at most three episodes of 10 seconds. Native recovery has
its existing 30-second budget and 3-second automatic-pause window, measured
hold acknowledgement and three fresh samples over 0.2 seconds. Same-capture
observer delivery recovery remains three attempts within the original 5 seconds.
Moving these layers must not reset, enlarge or conflate them. Preserve the
current two-advance observation predicate and the bench's separate current
20-second continuous startup qualification; historical uninterrupted verdict
and cumulative gaps remain separate.

```mermaid
sequenceDiagram
  participant A as Client A
  participant P as Pi authority
  participant B as Viewer B
  A->>P: Start operation O, known finite recipe
  P->>P: Persist O + run R, dispatch once
  Note over A: Response lost; client disconnects
  P->>P: Advance R only on qualified local feedback / observation
  B->>P: Attach
  P-->>B: R, progress, remaining deadline, ownership, first cause
  A->>P: Retry operation O
  P-->>A: Stored result for R (no dispatch)
  Note over P: Controller handoff requires explicit atomic claim
```

## Proposed message and storage contract

This section preserves the approved target proposal. Stage 1 uses the actual
routes and fields documented below, rather than these illustrative names.
Use allowlisted recipe IDs rather than executable commands,
arbitrary file paths, scripts or unreviewed control parameters.

| Operation | Minimal contract |
| --- | --- |
| `GET /api/state` | Current run, phase, state version, service generation, immutable recipe/pins, remaining Live time, trajectory, controller generation/expiry, feedback/observation validity, recovery budget, first fault, cleanup state; unavailable fields remain unavailable. |
| `POST /api/tasks` | Authenticated controller with current generation requests a known recipe using `operation_id`. Atomically persist operation, canonical recipe hash and new `run_id` before dispatch. |
| `GET /api/operations/{id}` | Query accepted, refused, terminal or uncertain outcome after a lost response. Same operation + same recipe returns its recorded result; changed payload is conflict. |
| `POST /api/attach`, `WSS /api/events` | Return authoritative snapshot and a short-lived connection ticket; acknowledge snapshot/state version before sending input. No queued movement/event replay. |
| `POST /api/control/claim`, `release` | Serialize lease check, explicit handoff and generation rotation under one authority lock. Release clears input and revokes grants; claim does not itself Resume. |
| `WSS input` | Exact run/service/controller/connection generation, increasing sequence, server-issued short-lived grant and latest current intent. One latest-valid mailbox. |
| `POST /api/runs/{id}/pause`, `resume`, `stop` | Idempotent operation IDs and exact run. Resume needs current measured alignment, fresh empty release/current evidence and appropriate ownership/proof. Authorized Pause/Stop remain available without owning the input lease. |

Illustrative Start and snapshot (IDs abbreviated; no secrets):

```json
{"operation_id":"O","service_generation":"S","task":"ArmSmokeRepeat","recipe_id":"reviewed-recipe-v1","controller_generation":7}
{"run_id":"R","operation_id":"O","service_generation":"S","phase":"running","input_mode":"finite","trajectory_s":88.2,"live_remaining_s":328.1,"controller_generation":7,"state_version":42,"required_observation":"qualified"}
```

`run_id` identifies one physical attempt; `operation_id` deduplicates a request;
`service_generation` identifies one authority incarnation; controller generation
fences lease handoff. Backend host epochs remain an additional protection,
not substitutes for those identities.

SQLite stores small operation/run records, recipe/source hashes, transitions,
first cause, terminal/uncertain result and owner-reconciliation state. Record
`accepted` before dispatch and `dispatching` before effects. A crash around
dispatch or an unpersisted terminal boundary is **uncertain**, not proof that
effects occurred or did not occur. No exactly-once physical-effect guarantee.
On restart, historical accepted operations return historical outcomes and never
dispatch again; a new deliberate Start needs a new ID and reconciled state.
Keep deduplication tombstones when pruning large artifacts so an old ID cannot
later become a fresh Start. No high-rate control samples in SQLite; original
bounded logs/recordings remain private artifacts.

All enforcement deadlines use the authority's local monotonic clock. Browser
countdowns interpolate a server remaining duration for display only; client
wall time and remote absolute monotonic times grant no motion authority. Reboot
invalidates saved monotonic deadlines. Persistence carries identity/outcome and
wall-time context, not a valid resumed motion clock.

Interactive input uses server grants valid for **at most the existing 250 ms**,
with expiry anchored to grant issuance, not delayed packet receipt. The trusted
adapter samples current keys/leader intent after receiving a grant, sends one
latest update and clears its buffer on reconnect/handoff. Validate service,
run, controller and connection generations plus sequence and unexpired grant.
A new connection requires snapshot acknowledgement and fresh empty input before
alignment/Resume. Receipt time alone cannot freshen buffered targets; client
monotonic timestamps are diagnostic only. This bounds delivery from the grant
and assumes a correct trusted input adapter; it does not attest malicious
client hardware sampling. USB leader calibration/alignment stays in the PC
adapter; a phone has no implicit USB-leader capability.

Starting finite execution records an immutable run authorization separately
from the interactive lease. Loss of that lease does not revoke an already
accepted finite recipe. It cannot append new movement or bypass required
coverage. Explicit operator intent increments a revision checked atomically
before each recovery/progression commit.

Proposed initial resource limits: four attached clients; one latest snapshot
and 64 recent events; per-client output at most 64 KiB; control messages at most
4 KiB; one latest input and one latest observation frame. Slow subscribers get
a dropped-events indicator/resnapshot or disconnect. Stop/Pause have a reserved
bounded control path and cannot be coalesced behind input, logs or media.
Validate size/rate before allocation. Original logs/downloads are lower priority
and cannot block motion or fabricate cleanup_unknown on normal browser closure.

## P1 media, observation and LAN access

**Choose MediaMTX on P1 with one FFmpeg live encoder fed copied WinRT frames.**
The current owner already records 1280×720 H264/10 fps locally while its same
MediaCapture frame reader copies pixels. Keep that recording and exact device
selection. Add a bounded copied-frame bridge (explicit format/stride) to one
640×360, at most 10 fps live encoder; start qualification with baseline H264,
yuv420p, no B-frames and roughly 600 kbit/s. These are proposed live settings,
not a recording change or measured CPU/phone result.

[MediaMTX routes media and requires a publisher](https://mediamtx.org/docs/kickoff/introduction);
[reencoding is external](https://mediamtx.org/docs/features/remuxing-reencoding-compression).
Use FFmpeg to publish one authenticated loopback-only RTSP path, then MediaMTX
fans it out as WebRTC. Never use a second dshow camera reader or the growing MP4
as a live source. One latest pending frame, one encoder and at most two initial
WebRTC viewers keep fanout bounded; source recording/proof cannot wait for them.
RTSP credentials/config and diagnostics remain private.

aiortc would also need the WinRT frame bridge plus PyAV, ICE/crypto dependencies
and application signaling; [MediaRelay](https://aiortc.readthedocs.io/en/latest/helpers.html)
shares a source, while raw-frame [sender encoding](https://github.com/aiortc/aiortc/blob/main/src/aiortc/rtcrtpsender.py)
can multiply with viewers. MediaMTX/FFmpeg adds two explicit Windows binaries,
but avoids creating another media/signaling implementation in the robot service.
Neither is installed for this packet. Pi needs no video encoder or PyAV for
current JPEG qualification. Do not assume hardware encoding or mobile H264
support: [MediaMTX documents browser codec constraints](https://mediamtx.org/docs/features/webrtc-specific-features).
Defer data-channel input and QUIC/WebTransport.

**Required observation is a separate direct path.** Pi issues a fresh nonce
over authenticated P1 WSS; P1 binds a later captured frame to that nonce, exact
configured source identity, capture generation, run and advancing sequence.
Pi decodes the associated JPEG pixels, checks dimensions/framing metadata and
ages the evidence through decode completion and actual executor consumption.
Reuse the existing causal bound: Pi's own round trip minus only the elapsed
challenge-to-capture duration measured on P1 QPC, with its precision margin and
contradiction checks. Never subtract P1 absolute QPC from Pi monotonic time.
Required effective source age stays at most 500 ms; original nonce lifetime
stays 750 ms. Expired, malformed, foreign, replayed or undecoded frames do not
qualify. Reconnection cannot reissue an old nonce with a new timestamp.

No SSH forwarding-only process, laptop JSON sharing fallback or browser DOM is
in this new proof path. A heartbeat, counter or playing video is insufficient.
This establishes current pixels from the trusted configured capture agent,
not scene safety or malicious-host attestation. Framing/coverage must remain
qualified for arms/lift and separately for body; if an onboard semantic role
is required, provide a non-browser bounded decoded receiver before using it as
authority. Do not convert all five optional views in the initial media stage.

P1 capture supervision must outlive viewers and SSH. Retain a finite
run-associated capture/recording budget, disk cap, normal terminal result and
camera release. A machine-authenticated observer adapter starts/attaches the
known bounded recipe independently of media viewers. Proposed Windows
supervision uses the established camera-capable user context at logon with
private permissions; do not assume WinRT camera access in LocalSystem/session 0.
Camera privacy consent, logged-in/locked/logout/sleep/reboot behavior, graceful
agent restart and CPU/recording contention need qualification before deployment.
Windows [service session isolation](https://learn.microsoft.com/en-us/windows/win32/services/interactive-services)
is a concrete limitation. Preserve existing consent; do not bypass camera privacy
or introduce blanket privacy changes to make an unattended service work.
A lost proof connection cannot extend recording indefinitely or replay an old
capture Start.

Use the Pi as the single browser HTTPS origin. Authenticate same-origin WHEP
POST/PATCH/DELETE through `/media/p1/...`; the gateway authenticates separately
to P1 with read-only path-scoped credentials. MediaMTX's
[WHEP reader protocol](https://github.com/bluenviron/mediamtx/blob/v1.21.1/internal/servers/webrtc/reader.js)
uses a returned session Location plus trickle PATCH and DELETE. Rewrite Location
to a device-bound opaque same-origin handle, validate/persist the exact allowed
upstream resource privately and protect every method with auth, Origin and CSRF.
Preserve SDP Content-Type, ETag/If-Match and necessary Link headers. No arbitrary
URL proxy, credential-bearing redirect or secret UUID logging. Session expiry/
revocation performs bounded best-effort upstream DELETE and reports failure.
Adapt the reader fetch path for CSRF; prefixing POST alone is insufficient.

Media DTLS/SRTP goes directly P1→browser. Use MediaMTX's
[fixed local UDP candidate port](https://mediamtx.org/docs/references/configuration-file)
and verified LAN candidate address, no public STUN/TURN or router forwarding by
default. Disable unused protocols/listeners; restrict P1 signaling to the Pi
service and publication to local authenticated processes. Media permissions
follow [path-scoped authentication](https://mediamtx.org/docs/features/authentication).
The inspected [v1.21.1 defaults](https://github.com/bluenviron/mediamtx/blob/v1.21.1/mediamtx.yml)
require explicit override: remove anonymous publish/read users, one named path
with a reader cap and no always-available playback, loopback RTSP/TCP, selected
LAN WebRTC address/UDP port and empty ICE-server list. Disable unused HLS, RTMP,
SRT, MoQ, playback, API, metrics and profiling listeners. Recheck the chosen
release at implementation; do not ship stock public defaults. The signaling
proxy must not become a motion dependency.

Proposed LAN enrollment:

1. Verify a unique robot mDNS name, such as `am1.local`, using existing
   [Avahi capability](https://avahi.org/); if absent, its small OS package/service
   is a later explicit dependency. Stable URL and certificate identity survive
   DHCP address changes; no identity is tied to the client's IP. Verify actual
   PC/Android/iOS resolution and name conflicts before promising compatibility.
2. Use a dedicated private CA and robot DNS-name certificate. Protect the CA
   key offline in private administrative storage; Pi holds only its leaf key.
   Trust that CA on explicitly enrolled devices, never the entire network.
   iOS requires [explicit SSL trust for manually installed profiles](https://support.apple.com/en-us/102390).
   Renew the leaf on a defined schedule (initial proposal: 90 days), surface
   approaching expiry, and retain the same name/CA. Replacement CA requires
   reenrollment; never disable TLS verification to work around expiry.
3. Enable a short-lived one-use pairing code only through authenticated local/
   administrative setup. Display it privately, rate-limit attempts and disable
   open pairing by default. Each device gets a revocable opaque credential in
   a Secure/HttpOnly/SameSite=Strict cookie; store only its hash and capabilities.
   No IP allowlist is a device identity.
4. Require HTTPS for HTTP, WSS and signaling; exact Host/Origin allowlists,
   CSRF on mutations (including WHEP), authenticated WebSocket upgrade and
   short-lived single-use attach ticket carried in the first WSS message,
   with no protected data/input before ticket verification. Cookies use the
   `__Host-` prefix. Do not put long-lived secrets in URL,
   frontend source or logs. P1 machine credentials are separate, scoped and
   privately pinned/rotatable. Reject untrusted origins; no wildcard CORS.
5. Later install rootless service units with narrowly scoped state/log access,
   serial/camera permissions only for owning components, private files/keys,
   and bounded storage. Allow only selected LAN HTTPS/media/mDNS traffic if
   required; internal serial/ZeroMQ remains nonpublic. Preserve existing ACLs.

Before any such install, do one **no-hardware narrow reachability check** with
the intended authenticated temporary listener: verify Pi HTTPS plus P1 signaling
and fixed media port from an enrolled PC and phone, then remove the temporary
listener/rules if not adopted. The earlier failed port test established no
direct reachability. Identify any exact Windows listener/firewall permission
and Linux service permission first; this is later installation work, distinct
from the canceled adapter/AP experiment. Do not disable firewalls, loosen
network-wide trust or substitute an essential permanent SSH tunnel.
If mDNS is unavailable on a target device, report that qualification gap and
review a single stable private address/certificate alternative separately;
do not silently begin home-network tuning.

## Ordered migration and rollback

Each stage has one next stage; no full legacy endurance pass is a prerequisite
to building its replacement. Create no new repository, retarget or merge now.
After design approval, prefer `codex/am1-persistent-session-fake` stacked on
the published PR #16 documentation checkpoint (runtime baseline 6e50fd6).
Keep repair PR #16 draft; the implementation follow-up explicitly depends on it.
Integrate retained repairs into `integrate/am1-local-teleop` in their reviewed
order, then the proven session slices; main remains unchanged. If PR #16 cannot
close until the new path supplies reliability evidence, keep the dependency
stack explicit and report which branch produced that evidence.

| Stage / visible outcome | Files and interfaces changed | Retained components / smallest acceptance | Deployment delta / rollback / next |
| --- | --- | --- | --- |
| **1. Fake persistent vertical slice**: two real browser contexts see one fake run across reload | Contract/core/IPC/gateway/fake adapter and UI transport listed below | No hardware imports or devices. Real HTTPS/WSS, SQLite, OS lock and process separation; deterministic cases below | Development loopback only, scoped aiohttp dependency; no Pi/P1 deployment or service installation. Stop fake processes/remove generated test state; legacy untouched. Next: stage 2 |
| **2. Pi owner + one finite profile**: local admitted executor no longer depends on Duffy lifetime | Extract `am1_session_runtime.py` from native sender/admission loop, `am1_finite_task.py` over current providers; `am1_pi_executor.py`; share ownership/cleanup from `am1_session_remote.py`; canonical arbitration and host pre-connect lock/local bind | Existing calibration, motor/feedback/thermal/status guards, measured hold and ordinary cleanup. Fake backend parity + Linux ownership/refusal + one bounded native recipe; **no powered independence claim yet** while observer is still legacy | Exact executor/helper and narrowly required host lock/bind delta staged only after affected owners are stopped. Keep original pins/units/config copies. Roll back only affected components after verified Stop; no alternate-owner retry. Next: stage 3 |
| **3. Independent P1 required proof + media**: no laptop/browser observation authority | Portable `am1_observer_receiver.py`, `am1_observation_policy.py`; P1 copied-frame/machine adapter; MediaMTX/FFmpeg and gateway WHEP adapter | Existing single capture, local recording, source identity, causal pixels, budgets and original failure taxonomy. First no-motor recording/proof/framing/decode + bounded recovery test, then one supported small task | Only named Pi observer/executor and P1 capture/media/listener permissions, independently pinned. Reuse unchanged onboard cameras/motor code. Stop affected owners and restore exact P1/receiver config/source to rollback; legacy observation remains explicitly laptop-dependent. Next: stage 4 |
| **4. Actual end-to-end continuity**: complete operating evidence | Qualification harness/reporting and only demonstrated blocked-layer corrections | Original four cycles/352 trajectory within 420 Live, ordinary Stop→natural rest→comparable automatic Start/confirmation, existing ArmHoldBody 12 s with reviewed W/A/U/J pulses. Required current pixels/feedback throughout, honest useful motion and cleanup | Stopped affected-component staging only if a tested correction is necessary. New-path acceptance closes administrative SSH and detaches clients while task remains qualified, then real PC/tablet/phone tests. Restore stopped legacy path only after verified cleanup. Next: review final operating evidence/integration |

Stage 2 may use an explicit legacy-observer compatibility adapter for offline
parity and later bounded qualification, but must label its laptop dependency.
Stage 3 removes it before claiming SSH/laptop-independent powered operation.
Keep manual physical-leader operation available via the existing adapter and
guidance; do not move leaders or expand that acceptance campaign here.

Relative effort: stage 1 is small-to-medium new service/protocol work with high
reuse of UI assets and fake harness patterns; stage 2 is medium extraction and
ownership work with substantial runtime/provider reuse; stage 3 is the largest
uncertainty (WinRT bridge, Windows supervision, codec/CPU, LAN reachability and
current-pixel qualification); stage 4 is bounded hardware evidence and only
demonstrated repairs. These are relative engineering estimates, not elapsed-time
promises. Existing motor repairs are retained work, not a rewrite allowance.

## Exact first-slice implementation brief

The owner approved **stage 1 only, fake hardware**, on the stacked
follow-up branch. No motor/camera access, Pi/P1 install, listener/firewall change,
adapter elevation or runtime architecture deployment is included.

| Approved file seam | Responsibility |
| --- | --- |
| `examples/alohamini/am1_session_contract.py` | Hardware-free immutable identity/spec/evidence/result schemas; allowlisted finite/interactive test recipes |
| `tools/am1_session_core.py` | Resident authority actor; atomic claim/intent revision, operation deduplication, SQLite minimal store, common OS ownership lock and reconciliation state |
| `tools/am1_session_ipc.py` | Private bounded JSON IPC; Linux Unix socket, portable authenticated loopback adapter in Windows fake tests; bytes-only framing, no untrusted pickle or shell commands |
| `tools/am1_session_service.py` | aiohttp HTTPS/WSS gateway, authentication/Origin/CSRF, attach snapshot and bounded per-client output; gateway lifetime independent of owner |
| `tools/am1_fake_executor.py` | Explicit fake feedback/observation/owner adapter, injected clock and finite progress; cannot import or construct real hardware |
| `tools/am1_console_ui/session_transport.js` plus narrow `app.js`/`index.html` adapter | Reuse UI assets; remote snapshot/operations, spectator finite view and explicit interactive lease mode; retain legacy transport |
| `tests/robots/test_am1_persistent_session.py`, `test_am1_session_api.py` and `tests/cameras/test_am1_session_clients.cjs` | Deterministic core/restart/lock tests plus loaded two-context real HTTPS/WSS integration |
| `pyproject.toml`, `uv.lock` | New scoped aiohttp extra only; preserve unrelated pins/environments |

Use short virtual-clock fixtures with the same separate trajectory/Live-deadline
semantics; do not need 352 real wall seconds to test reconnect. The production
registry in this slice exposes only fake recipes. Test-only clock/fault controls
are private harness interfaces, never public runtime operations.

Acceptance cases:

1. Accept Start, lose response after durable acceptance, retry same operation:
   one run/dispatch. Conflicting recipe hash refused. Crash before/after dispatch
   boundaries reports interrupted/uncertain without automatic effects.
2. Disconnect/reload A while finite progress advances; B and returning A attach
   to identical run, seed, progress and original deadline. No Start/home/sync;
   neither viewing nor absence acquires control.
3. Interactive 250 ms grant/input expiry zeros body/clears stale targets; delayed,
   reordered, old-generation and reconnect-buffered input cannot execute.
   Fresh empty release/alignment is required before Resume.
4. Simultaneous claims serialize; explicit A→B handoff rejects A's old generation.
   B viewing alone cannot claim. Test unauthorized claims, origins and payloads.
5. Optional view loss records quality only; required proof loss freezes progress
   and uses original recovery budget. Operator Pause/Stop racing recovery wins;
   late accepted request is not native active acknowledgement.
6. Restart gateway while owner advances. Restart authority/executor with persisted
   run: new generation, interrupted/uncertain result, no resume/duplicate Start.
   Old monotonic deadline and old operation ID cannot reactivate work.
7. Two processes contend for one real OS arbitration lock representing legacy/new
   owners: only one admitted owner. Unknown cleanup blocks Start. Slow WebSocket,
   saturated logs or disconnected download cannot block cadence or Stop/Pause.
8. Loaded UI in two browser contexts uses real HTTP/WSS/auth and snapshot attach.
   No mocked fetch-only claim of service integration; no physical-device or
   access-point roam claim from those contexts.

Run focused new tests and relevant retained lease/pause/provider/policy tests.
Reuse unchanged historical verification rather than rerun every repair suite.
Linux IPC/flock and Windows IPC/locking need distinct portability checks before
stage 2. Actual phone codec, certificate trust, mDNS and roam evidence belongs
to later device acceptance. Deterministic fake interruption needs no WLAN
sabotage or powered-process termination.

## Implemented stage 1: Windows fake-only persistent session

The committed implementation through `2b10537bfe420bb931ef34a3c7827d14922417ea`
includes the task-level corrections, final cross-client Pause revision fence
and unsupported fake-control correction. Task-level scoped reviews passed at
`b21cd287`. The completed whole-branch review at documentation checkpoint `7761edd0` found one additional Important
cross-client ordering defect, corrected in `4b91656d`; this records the correction
and tests, without predicting its scoped re-review verdict or publication outcome.
This is fake execution and synthetic proof, not captured pixels or physical
movement. The existing motor, calibration, mapping, feedback, current, thermal,
status, permissions and separate deployment pins above are unchanged. PR #16
remains open/draft at documentation checkpoint `b4a1b6ed`, dependent base
`integrate/am1-local-teleop` remains `9aa6d3b0`, and main remains `ab4462b7`.
The implementation follow-up depends on PR #16; no merge, retarget or whole
checkpoint deployment has occurred.

### Actual authority and transport contract

[SessionAuthority](../../tools/am1_session_core.py) owns SQLite operation/run
history, first cause, immutable deadlines, recovery and the real OS-backed
`owner.lock`. [FakeExecutor](../../tools/am1_fake_executor.py) imports no hardware.
[OwnerServer](../../tools/am1_session_ipc.py) owns a 50 ms tick thread, independent
of gateway reads or subscribers. Snapshot calls do not drive progression.
The [gateway](../../tools/am1_session_service.py) is a separate aiohttp process;
[session_transport.js](../../tools/am1_console_ui/session_transport.js) reuses
console assets through the narrow remote bootstrap in `app.js`. The gateway
renders remote HTML and omits independent camera assets; legacy bootstrap and
manual-use guidance remain available.

The immutable [recipe registry](../../examples/alohamini/am1_session_contract.py)
contains only `fake-finite` (352 trajectory seconds / 420 Live seconds),
`fake-finite-short` (1 / 12 seconds), and `fake-interactive` (420 Live seconds),
with seed 17. Arbitrary scripts, paths, hardware parameters and extra Start
parameters refuse. `SessionAuthority.handle(command, device_id)` takes an
adapter-authenticated identity; a supplied command `device_id` is rejected.

Actual identities are UUID `operation_id`, `run_id`, `service_incarnation`,
`controller_generation` and private `connection_generation`, not the earlier
illustrative `service_generation`/`recipe_id`. Commands use `op`; Start uses
`recipe`. Operation identity deduplicates durable requests and cannot cross
command classes. Acceptance is distinct from `effect_admitted`; persistence
before dispatch cannot guarantee exactly-once physical effects. Interrupted
or unacknowledged work becomes interrupted/uncertain on owner restart, with a
new incarnation and no autoplay. Reconcile and a new deliberate Start are
required; retrying historical operations never dispatches again.

| Actual endpoint | Contract |
| --- | --- |
| `GET /`, `/assets/{app.js,session_transport.js,style.css}` | Fake console and allowlisted assets only |
| `POST /api/enroll` | `{code}` with exact Origin; one-use local pairing sets Secure HttpOnly SameSiteStrict `__Host-am1-device` cookie; returns device identity, CSRF and control capability, no credential |
| `GET /api/session`, `/api/state` | Authenticated session metadata / `{snapshot}` |
| `POST /api/attach`, `GET /api/ws` | Attach returns `{snapshot,revision,ticket}`; WSS uses cookie/Origin and first text `{ticket}`, then server snapshot and client `{ack:revision}` before input; no ticket in URL |
| `POST /api/{claim,start,lookup,release,handoff,pause,resume,stop,reconcile,connect,release_input,grant,input}` | Route supplies `op`; body uses core schema and trusted gateway identity. Interactive connect/release_input/resume/grant/input require acknowledged WSS and refuse REST invocation |
| `POST /api/revoke` | Revoke the authenticated device; local CLI can revoke another enrolled device |

WSS commands are `{id,command}` with bounded correlation ID; replies are
`{kind:"result",id,...result}`, plus coalesced snapshots or `unavailable`.
Viewer attach never calls core `connect` or claims control. Enrolled devices
have separate view/control capabilities; an input spectator with control
capability can still Pause/Stop. Handoff requires an enrolled control-capable
target. Revocation fences cookies, tickets, WSS and controller presence,
including a locally revoked controller with no WSS connection.

`claim` **without** a `controller_generation` key is explicit acquisition.
`claim` **with** that key is atomic renew-only: it requires an alive same-device
matching generation under the authority mutex. Expired, absent, old, malformed
or foreign generation refuses and can never acquire a new lease. The UI renewal
timer supplies its captured generation. Presence lasts 1.5 seconds; private
input grants last at most 250 ms from issuance, with increasing `seq` and finite
normalized `target` values. Reconnect clears targets, requires snapshot ack,
explicit connect, fresh empty release, alignment/current evidence and Resume.
Interactive Start begins paused/held. Finite authorization remains separate
from controller/viewer presence and keeps the original run/seed/deadline.

Resume requires `expected_intent_revision`: a strict JSON integer (not boolean,
float, string, null or collection) equal to the current run `intent_revision`.
Missing, old or future revisions refuse atomically before dispatch, even when a
later spectator Pause snapshot has not reached the controller. The UI captures
this revision at the deliberate Resume click, carries it unchanged through
Connect and empty Release, and cancels obsolete continuations when an
authoritative run/revision change arrives. A fresh deliberate Resume from the
current paused revision remains available. An exact retry of an already admitted
Resume returns its stored historical result without dispatching again or
superseding a later Pause; a new operation UUID with that stale revision refuses.

The actual snapshot includes `service_incarnation`, `controller`, `run`,
`evidence`, and latest 64 `events`. Run fields include `progress_s`, `deadline`,
`remaining_s`, `trajectory_remaining_s`, `intent_revision`, `recovery`,
`recovery_episodes`, `first_cause`, `uncertain`, `dispatch` and `cleanup`.
Connection generations/grants stay in private controller replies. Feedback
freshness is 250 ms and required synthetic observation freshness 500 ms;
optional quality grants no authority. Terminal fake cleanup is `held_body_zero`.

**Recovery ruling:** a distinct qualified-and-acknowledged completed recovery
closes its episode. Later distinct loss may get its own 10-second ceiling,
within max three total episodes and the unchanged original Live deadline.
An unfinished episode never renews its ceiling. Manual qualified Resume closes
its prior episode; explicit Pause/Stop/ownership changes fence recovery, and
Stop retains an already-present first fault. Cost if this ruling proves wrong:
revise fake recovery behavior and tests; no physical deployment is affected.

Private bytes-only IPC has separate authenticated normal/protective lanes,
4-byte framing, strict JSON, 4 KiB requests / 64 KiB replies and 1-second partial
I/O deadlines. Windows uses authenticated loopback sockets; Linux Unix sockets
and flock remain unexecuted. HTTP admits 16 normal / 4 protective requests,
8 / 2 IPC calls and per-device 40 / 10 requests per second. WSS admits four total
pending/verified clients, 64 KiB queued plus in-flight output, latest snapshot,
6 normal / 2 protective commands per peer and one in-flight / one latest pending
input. Pause/Stop use the reserved lane even during a delayed input reply.
Slow nonreaders are disconnected with best-effort 1013 then bounded transport
abort; a congested client may observe 1006. No logs/download/media endpoint or
blocked subscriber participates in owner cadence.

### Reproducible focused developer setup

Use the existing shared environment and a **fresh dedicated temporary** HTTP
dependency overlay. Run from the candidate checkout; `$candidate` below is that
checkout's absolute path and `$sharedEnvironment` is the existing environment.
These are dedicated fake processes, not OS services. The sole new optional extra
is `am1-session = ["aiohttp==3.14.1"]`; all 335 existing package versions remain
unchanged. The initial overlay accidentally used four newer transitives
(idna, multidict, propcache, yarl); final checks used these ten exact lock pins.
The session's ignored scratch directory is not required:

```powershell
$candidate = (Get-Location).Path
$sharedEnvironment = 'C:\Users\pickm\lerobot_alohamini_client\.venv'
$httpOverlay = Join-Path ([IO.Path]::GetTempPath()) ('am1-http-' + [guid]::NewGuid())
uv pip install --python "$sharedEnvironment\Scripts\python.exe" --no-deps --target $httpOverlay aiohttp==3.14.1 aiohappyeyeballs==2.7.1 aiosignal==1.4.0 attrs==26.1.0 frozenlist==1.8.0 idna==3.18 multidict==6.7.1 propcache==0.5.2 yarl==1.24.2 typing-extensions==4.16.0
if ($LASTEXITCODE -ne 0) { throw 'Locked HTTP overlay failed' }
$env:UV_PROJECT_ENVIRONMENT = $sharedEnvironment
$env:PYTHONPATH = "$httpOverlay;$candidate\src;$candidate"
$env:AM1_TEST_PYTHON = "$sharedEnvironment\Scripts\python.exe"
```

Use the existing bundled Node runtime and its dependency directory as
`NODE_PATH` for Playwright; final checks used
`C:/Users/pickm/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules`
and headless msedge. No broad reinstall, upgrade or shared-environment sync is
needed. Python integration clients verify a generated test CA through normal
`ssl.create_default_context(cafile=...)`. Browser `ignoreHTTPSErrors` is confined
to dedicated test contexts; the service has no production TLS bypass.

From separate terminals with that Python environment, substitute newly created
dedicated private fake state directories and configured certificate/key paths:

```text
uv run --no-sync python -B -m tools.am1_session_ipc owner --state <NEW-FAKE-OWNER-DIR>
uv run --no-sync python -B -m tools.am1_session_service gateway --owner-state <OWNER-DIR> --auth-state <NEW-FAKE-AUTH-DIR> --cert <CERT.pem> --key <PRIVATE-KEY.pem> --ready-file <PRIVATE-READY.json>
uv run --no-sync python -B -m tools.am1_session_service pair --auth-state <AUTH-DIR> --control
uv run --no-sync python -B -m tools.am1_session_service revoke --auth-state <AUTH-DIR> --device-id <DEVICE-UUID>
```

Loopback TLS only (127.0.0.1, ephemeral port by default); configured cert/key,
TLS 1.2 minimum, exact Host/Origin/CSRF, no HTTP fallback, OS trust installation,
firewall change or network listener deployment. Ready JSON contains URL/PID
only. Pair writes a 60-second one-use code to private `pairing.json`; enter it
in the HTTPS console, explicitly Claim, select Fake recipe and Start. Omit
`--control` for a view-only enrollment. Interactive Resume performs explicit
connect/fresh release/qualification. No credential, attach ticket or IPC secret
belongs in argv, URLs or logs. State paths must be fresh/empty or correctly
marked dedicated fake namespaces, never an existing AM1 configuration, shared
folder or repository. Linked/junction paths refuse; Windows uses an exact
current-user protected DACL, Linux intends directories 0700/files 0600.

### Verification, corrections and remaining limits

Root independently checked committed `2b10537b` with the exact locked overlay,
shared `UV_PROJECT_ENVIRONMENT`, `--no-sync`, and candidate `src` plus root:

```text
uv run --no-sync python -B -m pytest tests/robots/test_am1_session_api.py tests/robots/test_am1_persistent_session.py tests/robots/test_am1_unified_session.py tests/robots/test_am1_console_pause_race.py tests/robots/test_am1_virtual_bench.py tests/robots/test_am1_arm_hold_body.py -q
node --test tests/cameras/test_am1_session_clients.cjs tests/cameras/test_am1_console_ui.cjs tests/cameras/test_am1_console_layout.cjs tests/cameras/test_am1_virtual_bench.cjs
```

Python: **267 passed, 1 skipped in 62.26 seconds**, exit 0. Node: **54 passed,
0 failed or skipped in 40.1056224 seconds**, exit 0. These are root's fresh final
corrected-source counts at `2b10537b`, including the cross-client revision and
unsupported Z/X checks. Root checked Ruff/format for all eight fake-slice Python
files and baseline-to-source whitespace; all passed. The single Windows skip is
[test_am1_unified_session.py](../../tests/robots/test_am1_unified_session.py#L1569):
POSIX process-group signal semantics; it establishes no Linux qualification.
Prior offline lock checks passed and all ten overlay versions matched lock;
all 335 existing package records remain unchanged. This is focused coverage,
not a full repository or endurance run.

The earlier independently verified `b21cd287` checkpoint remains historical:
255 Python passed/1 skipped in 60.81 seconds and 52 Node passed in 28.650 seconds.
Interim 252/49 counts and implementer subsets are not substituted for the fresh
root evidence above. One later implementer Node attempt had 53 passed and one
initial interactive Resume timeout before the new Z/X assertions; its isolated
and covering repeats passed unchanged. That failed attempt is retained without
an inferred diagnosis.

Coverage includes real crash boundaries and uncertain/no autoplay; lost Start
reply and same-operation dedup; real independent owner/gateway PIDs and OS lock
contention; two separately enrolled browser contexts with distinct cookies and
actual Enrollment/Claim/Start/Pause/Stop/Handoff/Resume UI actions; finite viewer
loss/reload/gateway replacement preserving one run, seed, progress and deadline;
interactive grant expiry, blur, reconnect and handoff without replay; required
versus optional synthetic proof; explicit Pause/Stop and first-fault retention;
strict JSON/IPC/output bounds and real TCP/TLS nonreader slot reclamation.

Demonstrated failures were retained and corrected: initial candidate imports
accidentally resolved an older editable checkout (fixed explicit import paths);
initial timing-sensitive AF_PIPE failure passed isolated repeat; four newer
HTTP transitives required the exact locked overlay. Core review exposed stale
grant reuse, progression before protective intent, dispatch without fresh proof,
false deadline completion, first-fault loss and unclosed manual recovery. Gateway
checks exposed client-slot double counting, Windows explicit DACL/linked-state
acceptance, exponent overflow, local revoke fencing, lost-reply false refusal,
UTF-8 rendering and a blocked close-handshake slot. Bounded abort reclaimed four
fresh clients while owner progress continued; client 1006 was reported honestly.
An initial CRLF whitespace failure was corrected in a dedicated LF-only commit.

Both task-level Important UI races had real assertion failures before correction:
a delayed Connect/release reply let pending Resume override a later accepted
Pause; a late renewal reply restored lost ownership and empty periodic Claim
could silently reacquire. Resume now checks protective intent, connection epoch
and held generation after every await; authoritative ownership loss/change
invalidates outstanding work. Atomic renew-only Claim prevents reacquisition.
Loaded tests observe accepted real replies and actual rendered state,
including transient authority restoration, rather than substituting mocked core
results. Task-level scoped re-review passed both fixes.

The completed whole-branch review then exposed the cross-client boundary:
A's delayed accepted Connect could override control-capable spectator B's later
accepted Pause, even after A received the current paused snapshot. Both actual
two-browser regressions failed by returning the run to running, with B's snapshot
delivered or temporarily withheld. The correction binds Resume to its initially
captured authority intent revision. Delivered snapshots cancel before follow-up
Release/Resume; withheld delivery still produces a real authority refusal, no
new dispatch and unchanged paused progress. Both cases render paused and then
accept a fresh deliberate Resume with the current revision. Eleven strict/missing/
stale revision cases and admitted-before-Pause deduplication also have core
coverage. Whole-branch documentation specification and quality passed; the
single Important source correction and evidence refresh are this final wave.
The remote-only Z/X follow-up is `2b10537b`; it changes no Python authority or legacy bootstrap.

**Minor fake-control correction:** Z/X have no represented fake target axis.
Their remote fake buttons are disabled and visibly/accessibly labelled
“Unavailable in fake mode”; Z/X keyboard and pointer input is ignored. The
loaded interactive UI regression first failed with both unsupported keys held,
then verifies empty held state, no fake intent and disabled labelled buttons.
Supported fake W input and the existing handoff path still work. This adds no
fake axis and preserves every legacy physical mapping/bootstrap.

Windows exercised; Linux Unix socket/flock/permissions execution must be
separately qualified before stage 2. Browser contexts prove neither real
phone/AP roaming nor device certificate trust, mobile codecs or required camera
pixels. Stages 2–4, the full original four-cycle 352/420 workload, comparable
normal-rest restart and 12-second ArmHoldBody remain pending. The single next
stage is reviewed stage 2: Pi owner/extracted finite profile over the existing
protected backend. No powered independence claim precedes stage 3 observer proof.
Rollback stops only dedicated fake processes and removes generated fake state,
certificates and temporary overlay when no longer needed; legacy deployment is
untouched. Keep the managed worktree and historical logs.

## Publication review and decision

The original design checkpoint `b4a1b6ed061e75a72b965ff684692dd066b1e20f`
was documentation-only, distinct from runtime baseline `6e50fd6b`. One
independent focused read-only review inspected the actual document against the full architecture packet and
found no actionable material gaps in lifetime, ownership, duplicate effects,
observation validity, security, portability or scope. Source audits covered
lifecycle, finite input/observation and media/access feasibility. Documentation
checks passed for local links/source anchors, private-data exclusion, balanced
diagram fences and diff whitespace. No runtime tests or device actions were
performed for that original design checkpoint; stage-1 focused results are
reported separately above. Historical test results are not new test passes.

Rollback of this publication is an ordinary documentation revert. Later code
rollback is component-specific, with original pins/private permissions retained,
verified stopped affected owners and one enabled motion path. No automatic
switch to a second serial owner.

**Owner decision (approved):** consolidated design and exact stage-1 fake-only
implementation brief on the stacked follow-up branch.
Deployment and subsequent hardware stages remain separately reviewable.
