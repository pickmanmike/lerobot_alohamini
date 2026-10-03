"use strict";
// Test-only visible full-page exercise: real assets/HTTP/pipe, fake robot.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const {chromium} = require("playwright");
const [url, evidencePath, startupWaitSeconds = "8"] = process.argv.slice(2);
const startupWaitMs = Number(startupWaitSeconds) * 1000;
assert([8000, 45000].includes(startupWaitMs), "test startup deadline must stay bounded");

(async () => {
  const browserArguments = [];
  // Opt-in, private synthetic-test artifact only: NetLog may retain request
  // credentials even in Default mode. Never publish it or use this on hardware.
  if (process.env.AM1_TIMING_NETLOG === "1") browserArguments.push(
    `--log-net-log=${evidencePath.replace(/browser-timing\.json$/, "browser-netlog.json")}`,
    "--net-log-capture-mode=Default");
  if (process.env.AM1_TIMING_DIRECT === "1") browserArguments.push("--no-proxy-server");
  const browser = await chromium.launch({headless:process.env.AM1_TIMING_HEADLESS === "1",
    channel:process.env.AM1_TEST_BROWSER_CHANNEL || "msedge",
    args:browserArguments});
  const page = await browser.newPage({viewport:{width:1440, height:1000}});
  const records = [], outstanding = new Map();
  // Private, bounded test evidence only. All requests are to this synthetic
  // loopback console; never retain request headers, cookies or control tokens.
  const cdp = await page.context().newCDPSession(page), connectionRecords = [], requests = new Map();
  const networkKeep = record => { connectionRecords.push(record); if (connectionRecords.length > 12000) connectionRecords.shift(); };
  await cdp.send("Network.enable");
  cdp.on("Network.requestWillBeSent", event => {
    const target = new URL(event.request.url);
    if (target.protocol !== "http:" || target.origin !== new URL(url).origin) return;
    let seq, session_id, epoch;
    if (target.pathname === "/api/body") {
      try { ({seq,session_id,epoch} = JSON.parse(event.request.postData || "{}")); } catch {}
    }
    const record = {request_id:event.requestId, path:target.pathname, seq, session_id, epoch};
    requests.set(event.requestId, record);
    networkKeep({event:"cdp_request", ...record, cdp_monotonic_s:event.timestamp,
      wall_time_s:event.wallTime});
  });
  cdp.on("Network.responseReceived", event => {
    const request = requests.get(event.requestId); if (!request) return;
    networkKeep({event:"cdp_response", ...request, cdp_monotonic_s:event.timestamp,
      status:event.response.status, protocol:event.response.protocol,
      connection_id:event.response.connectionId, connection_reused:event.response.connectionReused,
      timing:event.response.timing});
  });
  for (const eventName of ["loadingFinished", "loadingFailed"]) cdp.on(`Network.${eventName}`, event => {
    const request = requests.get(event.requestId); if (!request) return;
    networkKeep({event:`cdp_${eventName}`, ...request, cdp_monotonic_s:event.timestamp,
      bytes:event.encodedDataLength, error:event.errorText, cancelled:event.canceled});
    requests.delete(event.requestId);
  });
  const keep = record => { records.push(record); if (records.length > 3000) records.shift(); };
  let firstExpiry = null;
  const clock = () => Date.now(); // Node wall clock; never subtract it from browser monotonic.
  page.on("request", request => {
    if (!request.url().endsWith("/api/body")) return;
    const body = request.postDataJSON();
    const record = {event:"browser_request", session_id:body.session_id, seq:body.seq,
      epoch:body.epoch, active:body.active, wall_time_ms:clock()};
    outstanding.set(request, record);
    keep(record);
  });
  page.on("requestfinished", request => {
    const sent = outstanding.get(request);
    if (!sent) return;
    keep({event:"browser_request_finished", session_id:sent.session_id, seq:sent.seq,
      wall_time_ms:clock(), timing:request.timing()});
    outstanding.delete(request);
  });
  const native = state => state.events.filter(e => e.event === "test_native_state").at(-1);
  const seenProcessEvents = new Set();
  const read = async () => {
    const state = await (await page.request.get(`${url}/api/state`)).json();
    for (const event of state.events) {
      if (!["test_process_launch", "test_process_started", "test_startup_phase", "test_process_exit"].includes(event.event)) continue;
      const identity = `${event.session_id}/${event.event}/${event.phase || ""}/${event.wall_time_ns}`;
      if (seenProcessEvents.has(identity)) continue;
      seenProcessEvents.add(identity);
      keep({event:"browser_process_context", source_event:event.event, session_id:event.session_id,
        native_pid:event.native_pid, phase:event.phase, source_wall_time_ns:event.wall_time_ns,
        wall_time_ms:clock(), ...await page.evaluate(() => ({visible:!document.hidden,
          focused:document.hasFocus(), visibility:document.visibilityState}))});
    }
    return state;
  };
  const until = async (predicate, timeoutMs = 8000) => {
    const end = clock() + timeoutMs;
    while (clock() < end) {
      const state = await read();
      if (state.input_pause?.reason === "expired browser input") {
        firstExpiry ??= state.input_pause;
        await page.evaluate(() => am1TestTiming.freeze());
        throw new Error(`UNEXPECTED_INPUT_EXPIRY ${JSON.stringify(firstExpiry)}`);
      }
      if (predicate(state)) return state;
      await page.waitForTimeout(50);
    }
    throw new Error("timing exercise condition did not qualify");
  };
  try {
    await page.goto(url);
    await page.bringToFront();
    assert.deepEqual(await page.evaluate(() => ({visible:!document.hidden, focused:document.hasFocus()})),
      {visible:true, focused:true}, "the desktop comparison must genuinely be foreground");
    await page.waitForFunction(() => document.querySelector("#primary img")?.src.startsWith("blob:") &&
      [...document.querySelectorAll("#thumbnails img")].every(img => img.src.startsWith("blob:")));
    await page.getByRole("button", {name:"Start Local session", exact:true}).click();
    await until(state => state.phase === "live" && native(state)?.paused === false, startupWaitMs);
    const start = clock();
    await page.keyboard.down("w");
    await until(state => native(state)?.keys.includes("w"));
    await page.keyboard.up("w");
    await until(state => native(state)?.keys.length === 0);
    // Real held pointer inputs, not hand-written HTTP packets.
    for (const key of ["u", "j"]) {
      const bounds = await page.locator(`[data-body-key="${key}"]`).boundingBox();
      await page.mouse.move(bounds.x+bounds.width/2, bounds.y+bounds.height/2);
      await page.mouse.down();
      await until(state => native(state)?.keys.includes(key));
      await page.mouse.up();
      await until(state => native(state)?.keys.length === 0);
    }
    while (clock() - start < 30000) {
      await until(state => state.phase === "live" && native(state)?.paused === false);
      await page.waitForTimeout(250);
    }
    await page.getByRole("button", {name:"Pause / release body", exact:true}).click();
    const paused = await until(state => state.pending_gate?.[0] === "resume");
    await page.waitForFunction(epoch => document.querySelector("#gate-state").textContent.includes(
      `Approval needed: resume (host epoch ${epoch})`), paused.pending_gate[1]);
    await page.getByRole("button", {name:"Approve Resume", exact:true}).click();
    await until(state => state.phase === "live" && native(state)?.paused === false);
    await page.keyboard.down("w");
    await until(state => native(state)?.keys.includes("w"));
    await page.keyboard.up("w");
    await until(state => native(state)?.keys.length === 0);
    while (clock() - start < 65000) {
      await until(state => state.phase === "live" && native(state)?.paused === false);
      await page.waitForTimeout(250);
    }
    const firstLiveMs = clock()-start;
    await page.getByRole("button", {name:"Stop session", exact:true}).click();
    const done = await until(state => state.phase === "complete");
    assert.equal(done.cleanup_verified, true);
    assert.equal(done.final_exit_code, 0);
    // A fresh deliberate Start, never an automatic restart after input loss.
    await page.getByRole("button", {name:"Start Local session", exact:true}).click();
    await until(state => state.phase === "live" && native(state)?.paused === false, startupWaitMs);
    await page.getByRole("button", {name:"Stop session", exact:true}).click();
    await until(state => state.phase === "complete" && state.cleanup_verified === true);
    console.log(`NOMINAL_FOREGROUND_PASS first_live_ms=${firstLiveMs} including_deliberate_restart_ms=${clock()-start}; full cameras/telemetry, no rescue approval`);
  } catch (error) {
    // Use actual Stop immediately; never spend a Resume deadline inspecting data.
    try { await page.getByRole("button", {name:"Stop session", exact:true}).click({timeout:2000}); } catch {}
    if (firstExpiry) await page.waitForTimeout(1000);
    throw error;
  } finally {
    const frames = await Promise.all(page.frames().map(frame => frame.evaluate(() => am1TestTiming.report()).catch(() => null)));
    for (const frame of frames.filter(Boolean)) console.log(`BROWSER_TIMING_SUMMARY=${JSON.stringify(frame.summary)}`);
    fs.writeFileSync(evidencePath, JSON.stringify({firstExpiry, browser:frames, network:records,
      connections:connectionRecords}, null, 2));
    await browser.close();
  }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
