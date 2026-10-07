"use strict";
// One explicitly authorized packet scenario through the ordinary Control page.
// No motor sockets, synthetic leases, Enter feeding, Resume, or automatic retry.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {chromium} = require("playwright");
const [requestedAddress, scenario, identity, evidencePath, expectedWindowsHead, requestedLiveSeconds] = process.argv.slice(2);
const target = new URL(requestedAddress);
const address = target.href;
assert(target.protocol === "http:" && target.hostname === "127.0.0.1" && target.pathname === "/");
assert(["ArmSmoke", "ArmSmokeRepeat", "BodyPressRelease", "PhysicalLeader"].includes(scenario));
const scripted = scenario === "ArmSmoke" || scenario === "ArmSmokeRepeat";
const kind = scripted ? "arm" : scenario === "PhysicalLeader" ? "physical" : "body";
assert(/^AM1-RELIABILITY-(01|02)-(arm|body|physical)-\d{2}$/.test(identity));
assert(identity.includes(`-${kind}-`), "Evidence identity must match the input scenario");
if (["ArmSmokeRepeat", "PhysicalLeader"].includes(scenario)) assert(identity.startsWith("AM1-RELIABILITY-02-"));
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
  let ownedSession = null, startRefusal = null, startCount = 0, droppedRecords = 0, cancelled = false;
  let final = null, failure = null;
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
      if (scripted) {
        payload.leader_source = "scripted";
        payload.motion_profile = scenario;
      } else if (scenario === "PhysicalLeader") {
        payload.leader_source = "physical";
        delete payload.motion_profile;
      }
      await route.continue({postData:JSON.stringify(payload)});
    } else {
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
      const request = response.request().postDataJSON(), result = await response.json();
      if (request.kind === "Start") {
        // Start can attach to a session another tab won in the intervening race.
        // Only a new-owner response carries a control token and input epoch.
        if (result.accepted && typeof result.control_token === "string" &&
            Number.isInteger(result.input_epoch) && result.session_id) ownedSession = result.session_id;
        else startRefusal = result.reason || result.error || "Start attached to another owner";
      }
      keep({event:"operation_result", kind:request.kind, accepted:result.accepted,
            session_id:result.session_id, reason:result.reason});
    } catch { /* The ordinary frontend handles failed requests and releases input. */ }
  });
  page.on("requestfailed", request => {
    if (new URL(request.url()).pathname !== "/api/state") return;
    frontendStateNetwork.request_failure_count++;
    keep({event:"frontend_state_request_failed", reason:request.failure()?.errorText?.slice(0, 240)});
  });
  page.on("pageerror", error => {
    uncaughtPageErrorCount++;
    keep({event:"uncaught_page_error", name:error.name, message:error.message.slice(0, 240)});
  });
  const readCameraEvidence = () => page.locator("#am1-camera-root").evaluate(root => ({
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
    const cameraEvidence = verifySource ? await readCameraEvidence() : null;
    const cameras = cameraEvidence?.summary ?? null;
    keep({event:"state", session_id:state.session_id, phase:state.phase,
          pending_gate:state.pending_gate, input_epoch:state.input_epoch,
          input_pause_reason:state.input_pause?.reason,
          observation_age_ms:state.telemetry?.observation?.age_ms,
          cleanup_verified:state.cleanup_verified, final_exit_code:state.final_exit_code,
          camera_summary:cameras, camera_evidence:cameraEvidence});
    if (verifySource && viewsRequired && state.native_connected && ["live", "paused", "feedback_stale"].includes(state.phase))
      assert(cameras.startsWith("Cameras 5/5 fresh decoded views"), "Required camera view lost: " + cameras);
    return state;
  };
  const terminal = state => ["complete", "failed", "cleanup_unknown", "operator_stopped"].includes(state.phase);
  const until = async (predicate, {waitForNativeExit = false} = {}) => {
    let nativeClosedAt = null;
    while (!cancelled && performance.now() < deadline) {
      const state = await read();
      assert(!startRefusal, startRefusal);
      assert(nativeClosedAt === null || performance.now() - nativeClosedAt < 10000,
             "Native closed before a terminal result: finalization deadline");
      if (predicate(state)) return state;
      assert(!terminal(state), `Run ended before the condition: ${state.phase}: ${state.error}`);
      // A closed native pipe can precede the wrapper's actual exit/cleanup
      // result. No native forwarding or recovery is permitted in this window.
      // Preserve the supervisor's raw verdict; do not cancel a normal finish.
      if (waitForNativeExit && state.native_connected === false &&
          state.input_pause?.reason === "pipe disconnected") {
        nativeClosedAt ??= performance.now();
        await page.waitForTimeout(200);
        continue;
      }
      assert(nativeClosedAt === null, "Native ownership changed after pipe closure");
      // Prepared Start already permits ordinary qualified startup progression.
      // Any latched pause or later recovery gate requires deliberate intervention.
      assert(!state.pause_required, `Input pause: ${state.input_pause?.reason}`);
      assert(!state.pending_gate || ["sync_start", "live_start"].includes(state.pending_gate[0]),
             `Gate requires deliberate intervention: ${state.pending_gate?.[0]}`);
      await page.waitForTimeout(200);
    }
    throw new Error(cancelled ? "Bench cancelled" : "Finite bench deadline reached");
  };
  try {
    await page.goto(address, {timeout:15000});
    await page.bringToFront();
    assert(await page.evaluate(() => !document.hidden && document.hasFocus()), "Control must actually be focused before Start");
    const initial = await read();
    assert(initial.restart_allowed, "A prior owner or unknown cleanup blocks Start");
    assert.equal(initial.configured_source_pins.windows_session_head, expectedWindowsHead);
    await page.getByRole("button", {name:"Start Local session", exact:true}).click();
    const live = await until(state => state.phase === "live" && state.native_connected && ownedSession);
    assert.equal(live.verified_source_heads.windows_source_head, expectedWindowsHead);
    assert(await page.evaluate(() => !document.hidden && document.hasFocus()), "Control must actually be focused");
    // Existing bounded camera views corroborate availability; native protections
    // and deadlines remain responsible for termination, never image interpretation.
    try {
      await page.waitForFunction(() => document.querySelector("#primary img")?.src.startsWith("blob:") &&
        [...document.querySelectorAll("#thumbnails img")].every(img => img.src.startsWith("blob:")),
        null, {timeout:5000});
      await page.waitForFunction(() => document.querySelector("#connection").textContent.startsWith(
        "Cameras 5/5 fresh decoded views"), null, {timeout:5000});
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
      throw error;
    }
    viewsRequired = true;
    if (scenario === "BodyPressRelease" || scenario === "PhysicalLeader") {
      for (const key of ["w", "a", "u", "j"]) {
        const current = await read();
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
        pulses.push({key, down_wall_time_ms:downAt, released_wall_time_ms:Date.now(),
                     meaning:"actual frontend press/release; measured motion requires session evidence"});
        await page.waitForTimeout(500);
      }
    }
    final = await until(terminal, {waitForNativeExit:true});
    assert.equal(final.final_exit_code, 0, final.error || "Run was not successful");
    assert.equal(final.cleanup_verified, true, "Cleanup is not verified");
  } catch (error) {
    failure = error.message;
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
    try { fs.writeFileSync(evidencePath, JSON.stringify({identity, scenario, native_live_limit_seconds:nativeLiveSeconds, session_id:ownedSession,
      started_wall_time_ms:startedAt, finished_wall_time_ms:Date.now(), start_count:startCount,
      expected_windows_head:expectedWindowsHead, failure, final_phase:final?.phase,
      final_exit_code:final?.final_exit_code, cleanup_verified:final?.cleanup_verified,
      frontend_state_network:frontendStateNetwork, uncaught_page_error_count:uncaughtPageErrorCount,
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
