"use strict";
// One explicitly authorized packet scenario through the ordinary Control page.
// No motor sockets, synthetic leases, Enter feeding or automatic restart.
// Qualified same-session Resume requires explicit packet03 opt-in.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {chromium} = require("playwright");
const {validateVirtualConfig, VirtualObservationPolicy, qualifyRecovery, recoveryAcknowledged, RECOVERABLE_CAUSES} = require("./am1_virtual_bench.cjs");
const virtualConfigPath = process.env.AM1_BENCH_VIRTUAL_CONFIG;
if (virtualConfigPath) assert(path.isAbsolute(virtualConfigPath), "Explicit virtual configuration path must be absolute");
const virtualConfig = virtualConfigPath ? validateVirtualConfig(JSON.parse(fs.readFileSync(virtualConfigPath,"utf8"))) : null;
const virtualPolicy = virtualConfig ? new VirtualObservationPolicy(virtualConfig) : null;
const [requestedAddress, scenario, identity, evidencePath, expectedWindowsHead, requestedLiveSeconds] = process.argv.slice(2);
const target = new URL(requestedAddress);
const address = target.href;
assert(target.protocol === "http:" && target.hostname === "127.0.0.1" && target.pathname === "/");
assert(["ArmSmoke", "ArmSmokeRepeat", "BodyPressRelease", "PhysicalLeader"].includes(scenario));
const scripted = scenario === "ArmSmoke" || scenario === "ArmSmokeRepeat";
const kind = scripted ? "arm" : scenario === "PhysicalLeader" ? "physical" : "body";
assert(/^AM1-RELIABILITY-(01|02|03)-(arm|body|physical)-\d{2}$/.test(identity));
assert(identity.includes(`-${kind}-`), "Evidence identity must match the input scenario");
assert(identity.startsWith("AM1-RELIABILITY-03-") === !!virtualConfig, "Packet03 requires explicit virtual configuration; older packets preserve their policy");
if (virtualConfig) assert(scenario !== "PhysicalLeader", "Packet03 uses virtual controls");
if (["ArmSmokeRepeat", "PhysicalLeader"].includes(scenario) && !virtualConfig) assert(identity.startsWith("AM1-RELIABILITY-02-"));
const durations = {ArmSmoke:[30, 180], ArmSmokeRepeat:[420], BodyPressRelease:[12], PhysicalLeader:[180]};
const nativeLiveSeconds = Number(requestedLiveSeconds ?? durations[scenario].at(-1));
assert(durations[scenario].includes(nativeLiveSeconds),
       "Native duration must be an established finite scenario: ArmSmoke 30 or 180; repeat 420; body 12; physical 180");
assert(/^[a-f0-9]{40}$/.test(expectedWindowsHead));
assert(path.isAbsolute(evidencePath), "Use a private absolute evidence path outside public Git");
const headless = process.env.AM1_BENCH_HEADLESS === "1";
assert(!(headless && target.port === "8765"), "Powered console bench requires the visible DirectBrowser path");

(async () => {
  const browser = await chromium.launch({headless, channel:"msedge", args:["--no-proxy-server"]});
  const page = await browser.newPage({viewport:{width:1440, height:1000}});
  const records = [], pulses = [];
  const frontendStateNetwork = {http_status_counts:{}, request_failure_count:0};
  let uncaughtPageErrorCount = 0;
  let ownedSession = null, ownedToken = null, csrfToken = null, startRefusal = null, startCount = 0, droppedRecords = 0, cancelled = false;
  let observation = null, lastQuality = null, lastCoveragePost = -Infinity;
  const observationPollMs = virtualConfig?.required_camera_roles.length ? 100 : 200;
  let recoveryStarted = null, lastRecoveryProof = null, lastRecoveryCause = null, recoveryAttempts = 0, recoveryRefusal = null, heldCameraEpoch = null;
  const approvedPauses = new Set();
  let observerEventCount = 0, pendingObserverEvent = null, observerEventWrite = null;
  const observerEventPath = virtualConfig ? path.join(path.dirname(virtualConfig.observer_health_path),"event-request.json") : null;
  const observerEvent = label => {
    if (!virtualConfig || observerEventCount >= 128) return;
    const event = `${label.slice(0,70)}-${++observerEventCount}`;
    pendingObserverEvent = {generation:virtualConfig.observer_generation,event};
    if (observerEventWrite) return;
    observerEventWrite = (async () => {
      while (pendingObserverEvent) {
        const request = pendingObserverEvent; pendingObserverEvent = null;
        const temporary = `${observerEventPath}.${process.pid}.tmp`;
        try {
          await fs.promises.writeFile(temporary,JSON.stringify(request));
          let published = false;
          for (let attempt=0; attempt<5; attempt++) {
            try { await fs.promises.rename(temporary,observerEventPath); published = true; break; }
            catch (error) {
              if (!["EACCES","EPERM","EBUSY"].includes(error.code)) throw error;
              await new Promise(resolve=>setTimeout(resolve,20));
            }
          }
          keep({event:"observer_event_request",label:request.event,published});
        } catch (error) { keep({event:"observer_event_request_failed",label:request.event,reason:error.code??error.name}); }
      }
    })().finally(()=>{observerEventWrite=null;});
  };
  let final = null, failure = null;
  let virtualStop = null, lastVirtualPause = null;
  let readinessFailureSnapshot = null;
  let readinessFailureWallTime = null;
  let viewsRequired = false;
  const startedAt = Date.now();
  const deadline = performance.now() + (scenario === "BodyPressRelease" ? 180000 : nativeLiveSeconds * 1000 + 120000);
  const keep = record => {
    records.push({received_wall_time_ms:Date.now(), ...record});
    if (records.length > 700) { records.shift(); droppedRecords++; }
  };
  const cancel = () => { cancelled = true; };
  process.on("SIGINT", cancel);
  process.on("SIGTERM", cancel);
  // Select the existing backend's already supported profile for this one Start.
  // All subsequent requests, input leases and gates come from the real frontend.
  await page.route("**/api/operation", async route => {
    try {
    const payload = route.request().postDataJSON();
    if (payload.kind === "Start") {
      assert.equal(++startCount, 1, "Only one attempt is authorized per invocation");
      payload.duration_seconds = nativeLiveSeconds;
      if (virtualConfig) payload.virtual_bench = virtualConfig;
      if (scripted || virtualConfig && scenario === "BodyPressRelease") {
        payload.leader_source = "scripted";
        payload.motion_profile = scripted ? scenario : "ArmHoldBody";
      } else if (scenario === "PhysicalLeader") {
        payload.leader_source = "physical";
        delete payload.motion_profile;
      }
      await route.continue({postData:JSON.stringify(payload)});
    } else {
      if (payload.kind === "Resume" && virtualConfig) {
        const requestedProof = lastRecoveryProof && {...lastRecoveryProof};
        assert(requestedProof && approvedPauses.has(requestedProof.pause_sequence), "Resume has no approved episode");
        assert.equal(payload.session_id, ownedSession);
        assert.equal(payload.control_token, ownedToken);
        assert.equal(payload.gate_stage, "resume"); assert.equal(payload.host_epoch, requestedProof.host_epoch);
        // Qualification may expire while the real frontend prepares its empty
        // lease. Keep this one request held inside the original episode; never
        // forward stale proof, click again, or reset a recovery/trajectory clock.
        let proof = null;
        while (!proof) {
          assert(!cancelled && performance.now() < deadline, "Bench cancelled or deadline expired during Resume");
          assert(recoveryStarted !== null && performance.now()-recoveryStarted < virtualConfig.recovery_episode_seconds*1000,
                 "Virtual recovery episode deadline");
          const current = await read();
          const bench = current.virtual_bench;
          assert(!virtualStop, failure);
          assert(current.session_id === ownedSession && current.native_connected === true &&
                 current.input_epoch === requestedProof.input_epoch && bench?.enabled && bench.disarmed === false &&
                 bench.pause_sequence === requestedProof.pause_sequence && bench.host_epoch === requestedProof.host_epoch &&
                 bench.input_epoch === requestedProof.input_epoch && bench.cause === lastRecoveryCause &&
                 current.input_pause?.reason === lastRecoveryCause && RECOVERABLE_CAUSES.has(lastRecoveryCause) &&
                 current.input_pause?.pause_sequence === requestedProof.pause_sequence &&
                 current.pending_gate?.[0] === "resume" && current.pending_gate[1] === requestedProof.host_epoch &&
                 Number.isSafeInteger(bench.recovery_count) && bench.recovery_count < virtualConfig.max_recoveries &&
                 Number.isFinite(bench.remaining_seconds) && bench.remaining_seconds > 0,
                 "Virtual recovery ownership, gate or episode changed");
          assert(!cancelled && performance.now() < deadline &&
                 performance.now()-recoveryStarted < virtualConfig.recovery_episode_seconds*1000,
                 "Virtual recovery episode deadline");
          proof = qualifyRecovery(current, ownedSession, observation?.required_coverage_qualified,
                                  virtualConfig.max_recoveries);
          if (!proof) {
            keep({event:"recovery_request_held",...requestedProof,
                  elapsed_ms:performance.now()-recoveryStarted});
            await page.waitForTimeout(observationPollMs);
          }
        }
        assert.deepEqual(proof, requestedProof, "Virtual recovery proof changed while request was held");
        payload.bench_recovery = proof;
        await route.continue({postData:JSON.stringify(payload)});
        return;
      }
      assert(!["Resume", "Approve", "ClaimInput"].includes(payload.kind), "No unattended recovery approval");
      if (payload.kind === "Stop") assert(ownedSession && payload.session_id === ownedSession,
                                          "Stop must name this invocation's owned session");
      await route.continue();
    }
    } catch (error) {
      startRefusal = error.message;
      await route.abort();
    }
  });
  page.on("response", async response => {
    // Passive page events exclude the runner's APIRequestContext monitor reads.
    // Keep finite status counters and bounded failures, without headers or bodies.
    if (new URL(response.url()).pathname === "/api/state") {
      const status = response.status();
      frontendStateNetwork.http_status_counts[status] = (frontendStateNetwork.http_status_counts[status] || 0) + 1;
      if (!response.ok()) keep({event:"frontend_state_http_error", status});
      return;
    }
    if (!response.url().endsWith("/api/operation")) return;
    try {
      const request = response.request().postDataJSON();
      if (!response.ok()) {
        if (request.kind === "Start") startRefusal = `Start HTTP ${response.status()}`;
        if (request.kind === "Resume") recoveryRefusal = `Resume HTTP ${response.status()}`;
        keep({event:"operation_http_error",kind:request.kind,status:response.status()}); return;
      }
      const result = await response.json();
      if (request.kind === "Start") {
        // Start can attach to a session another tab won in the intervening race.
        // Only a new-owner response carries a control token and input epoch.
        if (result.accepted && typeof result.control_token === "string" &&
            Number.isInteger(result.input_epoch) && result.session_id) {
          ownedSession = result.session_id; ownedToken = result.control_token;
        }
        else startRefusal = result.reason || result.error || "Start attached to another owner";
      }
      if (request.kind === "Resume" && !result.accepted) recoveryRefusal = result.reason || result.error || "Resume refused";
      keep({event:"operation_result", kind:request.kind, accepted:result.accepted,
            session_id:result.session_id, reason:result.reason});
    } catch { /* The ordinary frontend handles failed requests and releases input. */ }
  });
  page.on("requestfailed", request => {
    const pathname = new URL(request.url()).pathname;
    if (pathname === "/api/operation") {
      try {
        const kind = request.postDataJSON()?.kind;
        const reason = request.failure()?.errorText?.slice(0,240) ?? "network failure";
        if (kind === "Start") startRefusal = "Start request failed: " + reason;
        if (kind === "Resume") recoveryRefusal = "Resume request failed: " + reason;
        keep({event:"operation_request_failed",kind,reason});
      } catch { /* No operation payload or secret is retained. */ }
      return;
    }
    if (pathname !== "/api/state") return;
    frontendStateNetwork.request_failure_count++;
    keep({event:"frontend_state_request_failed", reason:request.failure()?.errorText?.slice(0, 240)});
  });
  page.on("pageerror", error => {
    uncaughtPageErrorCount++;
    keep({event:"uncaught_page_error", name:error.name, message:error.message.slice(0, 240)});
  });
  const readCameraEvidence = () => page.locator("#am1-camera-root").evaluate(root => ({
      health:root.ownerDocument.defaultView.AM1CameraHealth?.() ?? null,
      summary:root.ownerDocument.querySelector("#connection").textContent,
      diagnostics:root.ownerDocument.querySelector("#diagnostics").textContent.slice(0, 1500),
      roles:[...root.querySelectorAll(".camera-slot")].slice(0, 5).map(slot => ({
        role:slot.dataset.role, fresh:slot.querySelector(".view").classList.contains("fresh"),
        image_age:slot.querySelector('[data-field="image-age"]').textContent.slice(0, 80),
        source:slot.querySelector('[data-field="source"]').textContent.slice(0, 80),
        sequence:slot.querySelector('[data-field="sequence"]').textContent.slice(0, 80),
      })),
    }), null, {timeout:1000});
  const read = async (verifySource = true) => {
    const state = await (await page.request.get(`${address}api/state`, {
      timeout:2000, maxRetries:0, headers:{"X-AM1-Bench-Monitor":identity},
    })).json();
    if (ownedSession) assert.equal(state.session_id, ownedSession, "Session ownership changed");
    const verified = state.verified_source_heads;
    if (verified && verifySource) assert.equal(verified.windows_source_head, expectedWindowsHead);
    const pauseCause = state.virtual_bench?.cause;
    if (virtualConfig && ownedSession && RECOVERABLE_CAUSES.has(pauseCause)) lastVirtualPause = pauseCause;
    // The native exit can replace the policy's disarm reason between polls.
    // Its retained exact-session stop event still proves the earlier budget stop.
    const exhausted = state.events?.find(event => event.event === "bench_recovery_exhausted" && event.session_id === ownedSession);
    const policyStop = state.virtual_bench?.action === "stop" || state.virtual_bench?.disarm_reason === "recovery budget exhausted";
    if (virtualConfig && ownedSession && state.virtual_bench?.enabled && !virtualStop &&
        (exhausted || policyStop)) {
      const eventCause = typeof exhausted?.cause === "string" ? exhausted.cause : null;
      const currentCause = policyStop && RECOVERABLE_CAUSES.has(pauseCause);
      virtualStop = {session_id:ownedSession,
        reason:exhausted ? "recovery budget exhausted" : state.virtual_bench.disarm_reason,
        reason_basis:exhausted ? "same-session stop event" : "current policy",
        cause:eventCause ?? (currentCause ? pauseCause : null),
        cause_basis:eventCause !== null ? "stop event" : currentCause ? "current policy" : "unavailable",
        observed_pause_cause:lastVirtualPause,
        observed_pause_cause_basis:lastVirtualPause === null ? "unavailable" : "last observed policy",
        recovery_count:state.virtual_bench.recovery_count,
        observed_input_epoch:state.input_epoch, observed_pause_sequence:state.virtual_bench.pause_sequence};
      failure ||= `Virtual bench stopped: ${virtualStop.reason}: ${virtualStop.cause ?? "stop cause unavailable"}`;
      keep({event:"virtual_bench_stop", ...virtualStop});
    }
    const nativeClosed = state.native_connected === false && state.input_pause?.reason === "pipe disconnected";
    const cameraEvidence = verifySource ? await readCameraEvidence() : null;
    const cameras = cameraEvidence?.summary ?? null;
    if (virtualPolicy && verifySource) {
      assert(cameraEvidence?.health?.version === 1, "Structured camera health unavailable");
      let observerHealth = null, observerReadError = null;
      try {
        const raw = await fs.promises.readFile(virtualConfig.observer_health_path,"utf8");
        assert(raw.length <= 16384, "Observer health record too large"); observerHealth = JSON.parse(raw);
      } catch (error) { observerReadError=error.code??error.name; }
      observation = virtualPolicy.observe(observerHealth, cameraEvidence.health, Date.now(), performance.now(),observerReadError);
      const quality = observation.degraded_roles.join(",") + ":" + observation.status_uncertain;
      if (quality !== lastQuality) {
        lastQuality = quality;
        if (observation.degraded_roles.length) observerEvent("camera-degraded-"+observation.degraded_roles.join("-"));
        keep({event:"camera_quality", all_five_fresh:observation.all_five_fresh,
              degraded_roles:observation.degraded_roles,status_uncertain:observation.status_uncertain});
      }
      if (ownedSession && Number.isInteger(state.input_epoch) && !terminal(state) && !nativeClosed && !virtualStop) {
        if (virtualConfig.required_camera_roles.length && performance.now()-lastCoveragePost>=100) {
          lastCoveragePost = performance.now();
          const response = await page.request.post(`${address}api/operation`, {timeout:2000,maxRetries:0,
            headers:{"Origin":target.origin,"X-AM1-CSRF":csrfToken},
            data:{kind:"BenchCoverage",session_id:ownedSession,control_token:ownedToken,epoch:state.input_epoch,
                  camera_health:cameraEvidence.health}});
          const result = await response.json();
          keep({event:"bench_coverage_result",accepted:result.accepted,reason:result.reason});
        }
      }
      if (ownedSession && !virtualStop && state.native_connected && ["live","paused","feedback_stale"].includes(state.phase)) {
        for (const role of virtualPolicy.reconnectRoles(observation,performance.now())) {
          const reply = await page.evaluate(role => globalThis.AM1CameraReconnect(role),role);
          keep({event:"optional_camera_reconnect",role,...reply});
        }
        if (observation.missing_required_roles.length && heldCameraEpoch !== state.input_epoch) {
          heldCameraEpoch = state.input_epoch;
          const response = await page.request.post(`${address}api/operation`, {timeout:2000,maxRetries:0,
            headers:{"Origin":target.origin,"X-AM1-CSRF":csrfToken},
            data:{kind:"BenchHold",session_id:ownedSession,control_token:ownedToken,epoch:state.input_epoch,
                  reason:"bench required coverage"}});
          const result = await response.json();
          keep({event:"bench_hold_result",accepted:result.accepted,reason:result.reason,
                missing_required_roles:observation.missing_required_roles});
        }
        if (!observation.missing_required_roles.length) heldCameraEpoch = null;
      }
    }
    keep({event:"state", session_id:state.session_id, phase:state.phase,
          pending_gate:state.pending_gate, input_epoch:state.input_epoch,
          input_pause_reason:state.input_pause?.reason,
          observation_age_ms:state.telemetry?.observation?.age_ms,
          cleanup_verified:state.cleanup_verified, final_exit_code:state.final_exit_code,
          virtual_bench:state.virtual_bench ?? null,
          observation_policy:virtualPolicy && verifySource ? observation : null,
          camera_summary:cameras, camera_evidence:cameraEvidence});
    if (!virtualPolicy && verifySource && viewsRequired && state.native_connected && ["live", "paused", "feedback_stale"].includes(state.phase))
      assert(cameras.startsWith("Cameras 5/5 fresh decoded views"), "Required camera view lost: " + cameras);
    return state;
  };
  const terminal = state => ["complete", "failed", "cleanup_unknown", "operator_stopped"].includes(state.phase);
  const until = async (predicate, {waitForNativeExit = false} = {}) => {
    let nativeClosedAt = null, cleanupVerifiedAt = null;
    while (!cancelled && performance.now() < deadline) {
      const state = await read();
      assert(!startRefusal, startRefusal);
      assert(!virtualStop, failure);
      if (nativeClosedAt !== null) {
        const now = performance.now(), age = now - nativeClosedAt;
        if (state.cleanup_verified !== true) cleanupVerifiedAt = null;
        const verifiedInTime = state.cleanup_verified === true && (cleanupVerifiedAt !== null || age < 10000);
        if (verifiedInTime) cleanupVerifiedAt ??= now;
        // Preserve the 10 s cleanup bound. Only timely current-session cleanup
        // permits the existing 60 s stopped-session collection/finalization wait.
        assert(age < (verifiedInTime ? 60000 : 10000),
               "Native closed before a terminal result: finalization deadline");
      }
      if (recoveryStarted !== null) {
        const elapsed = performance.now()-recoveryStarted;
        assert(elapsed < virtualConfig.recovery_episode_seconds*1000,"Virtual recovery episode deadline");
        if (recoveryAcknowledged(state,lastRecoveryProof,elapsed,virtualConfig.recovery_episode_seconds*1000)) {
          keep({event:"recovery_episode_complete",elapsed_ms:elapsed,host_epoch:state.telemetry.observation.host_epoch});
          recoveryStarted = null;
        }
      }
      if (predicate(state) && (terminal(state) || !virtualPolicy ||
          observation?.required_coverage_qualified && recoveryStarted === null)) return state;
      assert(!terminal(state), `Run ended before the condition: ${state.phase}: ${state.error}`);
      // A closed native pipe can precede the wrapper's actual exit/cleanup
      // result. No native forwarding or recovery is permitted in this window.
      // Preserve the supervisor's raw verdict; do not cancel a normal finish.
      if (waitForNativeExit && state.native_connected === false &&
          state.input_pause?.reason === "pipe disconnected") {
        nativeClosedAt ??= performance.now();
        await page.waitForTimeout(observationPollMs);
        continue;
      }
      assert(nativeClosedAt === null, "Native ownership changed after pipe closure");
      if (virtualConfig && ownedSession && state.virtual_bench_admitted && (state.pause_required || state.pending_gate?.[0] === "resume")) {
        assert(RECOVERABLE_CAUSES.has(state.input_pause?.reason), `Pause is not recoverable: ${state.input_pause?.reason}`);
        assert(state.virtual_bench?.enabled && !state.virtual_bench.disarmed, "Virtual recovery was disarmed");
        assert(!recoveryRefusal, recoveryRefusal);
        if (recoveryStarted === null) observerEvent("required-coverage-hold");
        recoveryStarted ??= performance.now();
        assert(performance.now()-recoveryStarted < virtualConfig.recovery_episode_seconds*1000,
               "Virtual recovery episode deadline");
        const proof = qualifyRecovery(state,ownedSession,observation?.required_coverage_qualified,
                                      virtualConfig.max_recoveries);
        if (proof && !approvedPauses.has(proof.pause_sequence)) {
          assert(recoveryAttempts < virtualConfig.max_recoveries,"Virtual recovery attempt limit");
          const visibleGate = () => page.evaluate(({session,proof,cause}) => {
            try {
              const visible = JSON.parse(document.querySelector("#session-details").textContent);
              const gate = visible.gate_request_evidence;
              return visible.session_id===session && visible.input_epoch===proof.input_epoch &&
                visible.native_connected===true && visible.input_pause?.reason===cause &&
                visible.input_pause?.pause_sequence===proof.pause_sequence && gate?.accepted===true &&
                gate.stage==="resume" && gate.host_epoch===proof.host_epoch && gate.input_epoch===proof.input_epoch;
            } catch { return false; }
          },{session:ownedSession,proof,cause:state.input_pause.reason});
          const visibleDeadline = performance.now()+1000;
          let currentGateVisible = await visibleGate();
          while (!currentGateVisible && performance.now()<visibleDeadline && !cancelled) {
            await page.waitForTimeout(100);
            // Keep original required-view clocks current while the real frontend polls its gate.
            if (virtualConfig.required_camera_roles.length) await read();
            currentGateVisible = await visibleGate();
          }
          if (!currentGateVisible) { await page.waitForTimeout(observationPollMs); continue; }
          approvedPauses.add(proof.pause_sequence); recoveryAttempts++; lastRecoveryProof = proof; lastRecoveryCause = state.input_pause.reason;
          keep({event:"qualified_recovery_request",cause:state.input_pause.reason,...proof,
                attempt:recoveryAttempts,elapsed_ms:performance.now()-recoveryStarted});
          await page.getByRole("button",{name:"Resume",exact:true}).click({timeout:2000});
        }
        await page.waitForTimeout(observationPollMs); continue;
      }
      // Prepared Start already permits ordinary qualified startup progression.
      // Any latched pause or later recovery gate requires deliberate intervention.
      assert(!state.pause_required, `Input pause: ${state.input_pause?.reason}`);
      assert(!state.pending_gate || ["sync_start", "live_start"].includes(state.pending_gate[0]),
             `Gate requires deliberate intervention: ${state.pending_gate?.[0]}`);
      await page.waitForTimeout(observationPollMs);
    }
    throw new Error(cancelled ? "Bench cancelled" : "Finite bench deadline reached");
  };
  try {
    await page.goto(address, {timeout:15000});
    csrfToken = await page.locator('meta[name="am1-csrf"]').getAttribute("content",{timeout:1000});
    await page.bringToFront();
    assert(await page.evaluate(() => !document.hidden && document.hasFocus()), "Control must actually be focused before Start");
    const initial = await read();
    assert(initial.restart_allowed, "A prior owner or unknown cleanup blocks Start");
    assert.equal(initial.configured_source_pins.windows_session_head, expectedWindowsHead);
    if (virtualPolicy) {
      const observationDeadline = performance.now()+10000;
      while (!observation?.observer.qualified && performance.now()<observationDeadline && !cancelled) {
        await page.waitForTimeout(200); await read();
      }
      assert(observation?.observer.qualified,"Current advancing P1 observer does not qualify");
      keep({event:"observer_preflight",verified_coverage:virtualConfig.verified_coverage,observer:observation.observer});
    }
    await page.getByRole("button", {name:"Start Local session", exact:true}).click();
    const live = await until(state => state.phase === "live" && state.native_connected && ownedSession);
    assert.equal(live.verified_source_heads.windows_source_head, expectedWindowsHead);
    assert(await page.evaluate(() => !document.hidden && document.hasFocus()), "Control must actually be focused");
    // Existing bounded camera views corroborate availability; native protections
    // and deadlines remain responsible for termination, never image interpretation.
    try {
      if (virtualPolicy) {
        const qualityDeadline = performance.now()+5000;
        while (!observation?.all_five_fresh && performance.now()<qualityDeadline && !cancelled) {
          await page.waitForTimeout(200); await read();
        }
        keep({event:"initial_camera_quality",all_five_fresh:observation?.all_five_fresh === true});
      } else {
      await page.waitForFunction(() => document.querySelector("#primary img")?.src.startsWith("blob:") &&
        [...document.querySelectorAll("#thumbnails img")].every(img => img.src.startsWith("blob:")),
        null, {timeout:5000});
      await page.waitForFunction(() => document.querySelector("#connection").textContent.startsWith(
        "Cameras 5/5 fresh decoded views"), null, {timeout:5000});
      }
    } catch (error) {
      readinessFailureWallTime = Date.now();
      // Best effort in parallel; never wait for diagnostics before owned Stop.
      readinessFailureSnapshot = readCameraEvidence().then(cameraEvidence => {
        keep({event:"camera_readiness_failure", failure_wall_time_ms:readinessFailureWallTime,
              camera_evidence:cameraEvidence});
        return true;
      }).catch(snapshotError => {
        keep({event:"camera_readiness_snapshot_unavailable", failure_wall_time_ms:readinessFailureWallTime,
              reason:snapshotError.message.slice(0, 240)});
        return true;
      });
      if (virtualPolicy) {
        keep({event:"initial_camera_quality",all_five_fresh:false,reason:error.message.slice(0,240)});
        await read(); assert(observation?.required_coverage_qualified,"Required observation is unavailable");
      } else throw error;
    }
    viewsRequired = true;
    if (scenario === "BodyPressRelease" || scenario === "PhysicalLeader") {
      for (const key of ["w", "a", "u", "j"]) {
        if (virtualConfig?.required_camera_roles.length) lastCoveragePost=-Infinity;
        const current = virtualPolicy ? await until(state=>state.phase === "live" && !state.pending_gate && !state.pause_required) : await read();
        assert.equal(current.phase, "live");
        assert.equal(current.pending_gate, null);
        assert.equal(current.native_connected, true);
        assert.equal(current.pause_required, false);
        assert.equal(current.input_lease, true);
        assert.equal(current.body_release_required, false);
        assert(current.telemetry.observation.age_ms < 1000);
        assert(await page.evaluate(() => !document.hidden && document.hasFocus()));
        assert(!cancelled && performance.now() < deadline);
        const downAt = Date.now();
        await page.keyboard.down(key);
        try { await page.waitForTimeout(200); }
        finally { await page.keyboard.up(key); }
        observerEvent(`body-${key}-release`);
        pulses.push({key, down_wall_time_ms:downAt, released_wall_time_ms:Date.now(),
                     meaning:"actual frontend press/release; measured motion requires session evidence"});
        if (virtualConfig?.required_camera_roles.length) {
          const releaseWaitDeadline = performance.now()+500;
          while (performance.now()<releaseWaitDeadline && !cancelled) {
            await read();
            await page.waitForTimeout(Math.min(100,Math.max(0,releaseWaitDeadline-performance.now())));
          }
        } else await page.waitForTimeout(500);
      }
    }
    final = await until(terminal, {waitForNativeExit:true});
    assert.equal(final.final_exit_code, 0, final.error || "Run was not successful");
    assert.equal(final.cleanup_verified, true, "Cleanup is not verified");
  } catch (error) {
    failure ||= error.message;
  } finally {
    let current = null;
    try { current = await read(false); }
    catch (error) { keep({event:"cleanup_state_error", reason:error.message}); }
    try {
      if (ownedSession && (!current || !terminal(current))) {
        // Monitoring failure cannot suppress the ordinary, session-bound Stop.
        try { await page.getByRole("button", {name:"Stop session", exact:true}).click({timeout:2000}); }
        catch (error) { failure ||= `Stop request: ${error.message}`; }
        const cleanupDeadline = performance.now() + 60000;
        while (performance.now() < cleanupDeadline) {
          try {
            final = await read(false);
            if (terminal(final)) break;
          } catch (error) { keep({event:"cleanup_state_error", reason:error.message}); }
          await page.waitForTimeout(250);
        }
      } else final = current;
      if (ownedSession && final?.cleanup_verified !== true) failure ||= "Cleanup remains unverified";
    } catch (error) { failure ||= `Stop/cleanup verification: ${error.message}`; }
    if (readinessFailureSnapshot) {
      // Stop and cleanup have already been handled. Bound evidence finalization
      // independently even if browser automation cannot complete the snapshot.
      const captured = await Promise.race([readinessFailureSnapshot,
        new Promise(resolve => setTimeout(() => resolve(false), 1000))]);
      if (!captured) keep({event:"camera_readiness_snapshot_unavailable",
                           failure_wall_time_ms:readinessFailureWallTime, reason:"diagnostic deadline"});
    }
    if (observerEventWrite) await Promise.race([observerEventWrite,new Promise(resolve=>setTimeout(resolve,1000))]);
    try { fs.writeFileSync(evidencePath, JSON.stringify({identity, scenario, native_live_limit_seconds:nativeLiveSeconds, session_id:ownedSession,
      started_wall_time_ms:startedAt, finished_wall_time_ms:Date.now(), start_count:startCount,
      expected_windows_head:expectedWindowsHead, failure, final_phase:final?.phase,
      final_exit_code:final?.final_exit_code, cleanup_verified:final?.cleanup_verified,
      frontend_state_network:frontendStateNetwork, uncaught_page_error_count:uncaughtPageErrorCount,
      virtual_bench_policy:virtualConfig,recovery_attempts:recoveryAttempts,virtual_stop:virtualStop,
      pulses, dropped_records:droppedRecords, records}, null, 2)); }
    finally {
      process.off("SIGINT", cancel);
      process.off("SIGTERM", cancel);
      await browser.close();
    }
  }
  console.log(JSON.stringify({identity, scenario, native_live_limit_seconds:nativeLiveSeconds, session_id:ownedSession, failure,
    final_exit_code:final?.final_exit_code, cleanup_verified:final?.cleanup_verified, evidence:evidencePath}));
  assert.equal(failure, null);
})().catch(error => { console.error(error.message); process.exitCode = 1; });
