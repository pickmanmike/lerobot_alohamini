"use strict";
// Test-only visible full-page exercise: real assets/HTTP/pipe, fake robot.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const {chromium} = require("playwright");
const [url, evidencePath] = process.argv.slice(2);

(async () => {
  const browser = await chromium.launch({headless:process.env.AM1_TIMING_HEADLESS === "1",
    channel:process.env.AM1_TEST_BROWSER_CHANNEL || "msedge"});
  const page = await browser.newPage({viewport:{width:1440, height:1000}});
  const records = [], outstanding = new Map();
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
  const read = async () => (await page.request.get(`${url}/api/state`)).json();
  const until = async predicate => {
    const end = clock() + 8000;
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
    await until(state => state.phase === "live" && native(state)?.paused === false);
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
    await page.getByRole("button", {name:"Stop session", exact:true}).click();
    const done = await until(state => state.phase === "complete");
    assert.equal(done.cleanup_verified, true);
    assert.equal(done.final_exit_code, 0);
    // A fresh deliberate Start, never an automatic restart after input loss.
    await page.getByRole("button", {name:"Start Local session", exact:true}).click();
    await until(state => state.phase === "live" && native(state)?.paused === false);
    await page.getByRole("button", {name:"Stop session", exact:true}).click();
    await until(state => state.phase === "complete" && state.cleanup_verified === true);
    console.log(`NOMINAL_FOREGROUND_PASS active_ms=${clock()-start}; full cameras/telemetry, no rescue approval`);
  } catch (error) {
    // Use actual Stop immediately; never spend a Resume deadline inspecting data.
    try { await page.getByRole("button", {name:"Stop session", exact:true}).click({timeout:2000}); } catch {}
    if (firstExpiry) await page.waitForTimeout(1000);
    throw error;
  } finally {
    const frames = await Promise.all(page.frames().map(frame => frame.evaluate(() => am1TestTiming.report()).catch(() => null)));
    for (const frame of frames.filter(Boolean)) console.log(`BROWSER_TIMING_SUMMARY=${JSON.stringify(frame.summary)}`);
    fs.writeFileSync(evidencePath, JSON.stringify({firstExpiry, browser:frames, network:records}, null, 2));
    await browser.close();
  }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
