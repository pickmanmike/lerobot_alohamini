"use strict";
// Isolated headless browser running the actual assets; no connection to a robot.
const assert = require("node:assert/strict");
const {chromium} = require("playwright");
const [url, scenario] = process.argv.slice(2);

(async () => {
  const browser = await chromium.launch({headless:true, channel:process.env.AM1_TEST_BROWSER_CHANNEL || undefined});
  const page = await browser.newPage();
  const bodyTiming = [];
  const browserPauses = [];
  const operations = [];
  const operationRequests = [];
  const bodyReplies = [];
  page.on("response", async response => {
    if (response.url().endsWith("/api/body")) {
      try {
        const request = response.request().postDataJSON();
        const result = await response.json();
        bodyReplies.push({seq:request.seq, active:request.active, accepted:result.accepted});
      } catch {}
    }
    if (response.url().endsWith("/api/operation")) {
      try {
        const request = response.request().postDataJSON();
        const result = await response.json();
        operations.push({kind:request.kind, stage:request.gate_stage, epoch:request.host_epoch,
                         accepted:result.accepted, reason:result.reason});
      } catch {}
    }
  });
  page.on("console", message => {
    try {
      const event = JSON.parse(message.text());
      if (event.event === "am1_browser_input_release") browserPauses.push(event);
    } catch {}
  });
  page.on("request", request => {
    if (request.url().endsWith("/api/body")) bodyTiming.push({sent:Date.now(), request});
    if (request.url().endsWith("/api/operation")) {
      const payload = request.postDataJSON();
      operationRequests.push({kind:payload.kind, stage:payload.gate_stage, epoch:payload.host_epoch});
    }
  });
  page.on("requestfinished", request => {
    const record = bodyTiming.find(v => v.request === request);
    if (record) { record.duration_ms = Date.now() - record.sent; record.timing = request.timing(); }
  });
  await page.addInitScript(() => {
    globalThis.testBodyCalls = [];
    const originalFetch = globalThis.fetch;
    globalThis.fetch = function(url, ...args) {
      if (url === "/api/body") testBodyCalls.push(performance.now());
      return originalFetch.call(this, url, ...args);
    };
    globalThis.testLongTasks = [];
    new PerformanceObserver(list => {
      for (const entry of list.getEntries()) {
        testLongTasks.push({start:entry.startTime, duration:entry.duration});
        if (testLongTasks.length > 20) testLongTasks.shift();
      }
    }).observe({entryTypes:["longtask"]});
  });
  const read = async () => (await page.request.get(`${url}/api/state`, {maxRetries:1})).json();
  const until = async predicate => {
    const end = Date.now() + 7000;
    while (Date.now() < end) {
      const state = await read();
      if (predicate(state)) return state;
      await page.waitForTimeout(25);
    }
    const state = await read();
    throw new Error(`condition failed: ${scenario}: ${JSON.stringify({phase:state.phase, gate:state.pending_gate, native:native(state), operations,
      notice:await page.locator("#control-notice").textContent()})}`);
  };
  const native = state => state.events.filter(e => e.event === "test_native_state").at(-1);
  const resume = async () => {
    const current = await until(state => state.pending_gate?.[0] === "resume");
    assert.equal(current.native_connected, true);
    assert.equal(current.gate_request_evidence.stage, "resume");
    assert.equal(current.gate_request_evidence.host_epoch, current.pending_gate[1]);
    assert.equal(current.gate_request_evidence.accepted, true);
    await page.waitForFunction(epoch => document.querySelector("#gate-state").textContent
      .includes(`Approval needed: resume (host epoch ${epoch})`), current.pending_gate[1]);
    await page.waitForFunction(() => document.querySelector("#gate-state").textContent
      .includes("Native input: connected"), null, {timeout:1500});
    await page.getByRole("button", {name:"Approve Resume", exact:true}).click();
    await until(state => native(state)?.paused === false);
    await until(state => state.phase === "live");
    await page.waitForFunction(() => document.querySelector("#session-state").textContent.endsWith(": live"));
  };
  try {
    await page.goto(url);
    // Match actual camera-ready admission instead of admitting the fake host
    // while the camera document is still initializing its first images.
    await page.waitForFunction(() => {
      return document.querySelector("#primary img")?.src.startsWith("blob:") &&
        [...document.querySelectorAll("#thumbnails img")].every(img => img.src.startsWith("blob:"));
    });
    await page.getByRole("button", {name:"Start Local session", exact:true}).click();
    await until(state => native(state)?.paused === false || state.pending_gate?.[0] === "resume");
    // Full native telemetry can expose a real expiry before the browser has
    // rendered a brief live admission. Handle that CURRENT gate rather than
    // waiting for a live label while the fake host is already paused.
    await until(state => state.phase === "live" || state.pending_gate?.[0] === "resume");
    await page.waitForTimeout(900);
    // Wait for the browser's actual current state before deciding whether the
    // single initial recovery is needed. A native-only check can precede the
    // next UI render, then incorrectly wait for live after a genuine expiry.
    await page.waitForFunction(() => document.querySelector("#session-state").textContent.endsWith(": live") ||
      document.querySelector("#gate-state").textContent.includes("Approval needed: resume"));
    if (native(await read()).paused) {
      // A loaded desktop can genuinely miss the approved 1.5 s presence deadline.
      // Do not call that a premature pause, hide it, or manufacture a heartbeat.
      const gaps = await page.evaluate(() => testBodyCalls.slice(1).map((at,i) => at-testBodyCalls[i]));
      const observed = await read();
      assert.equal(observed.input_pause?.reason, "expired browser input");
      assert.ok(observed.input_pause.input_age_ms >= 1500,
                "this pause must prove expiry from its last accepted browser packet");
      assert.deepEqual(native(observed).keys, []);
      console.log(`EXPECTED safe pause: recorded accepted-input age ${Math.round(observed.input_pause.input_age_ms)} ms; ` +
                  `historical maximum send gap ${Math.round(Math.max(...gaps))} ms is context, not its cause; explicit recovery only`);
      await resume();
    }
    assert.equal(native(await read()).paused, false, "qualified focused input must be live");
    await until(state => state.phase === "live");
    await page.waitForFunction(() => document.querySelector("#session-state").textContent.endsWith(": live"));
    if (["healthy", "camera-delay"].includes(scenario)) {
      await page.keyboard.down("w");
      await until(state => native(state)?.keys.includes("w"));
      await page.keyboard.up("w");
      await until(state => native(state)?.keys.length === 0);
      await page.getByRole("button", {name:"Pause / release body", exact:true}).click();
      await resume();
    } else if (["navigation", "blur", "hidden"].includes(scenario)) {
      await page.keyboard.down("w");
      await until(state => native(state)?.keys.includes("w"));
      if (scenario === "navigation") await page.getByRole("link", {name:"Terminal", exact:true}).click();
      else if (scenario === "blur") await page.evaluate(() => window.dispatchEvent(new Event("blur")));
      else await page.evaluate(() => {
        Object.defineProperty(document, "hidden", {configurable:true, get:() => true});
        document.dispatchEvent(new Event("visibilitychange"));
      });
      await until(state => native(state)?.paused === true);
      assert.deepEqual(native(await read()).keys, []);
      await page.keyboard.up("w");
      if (scenario === "navigation") {
        await page.locator("#terminal-kind").selectOption("host");
        await page.getByRole("button", {name:"View", exact:true}).click();
        await page.waitForTimeout(300);
        assert.match(await page.locator("#terminal-output").textContent(), /FAKE HOST/);
        await page.getByRole("link", {name:"Control", exact:true}).click();
      }
      if (scenario === "hidden") await page.evaluate(() => Object.defineProperty(document, "hidden", {get:() => false}));
      await page.waitForTimeout(350);
      assert.equal(native(await read()).paused, true, "refocus cannot auto-rearm");
      await resume();
      assert.deepEqual(native(await read()).keys, []);
    } else if (scenario === "short-browser-stall") {
      await page.keyboard.down("w");
      await until(state => native(state)?.keys.includes("w"));
      const stall = await page.evaluate(() => {
        const start = performance.now(), wall = Date.now();
        while (performance.now() - start < 940) {} // Synthetic known scheduling gap, not its historical initiator.
        return {start_wall_ms:wall, end_wall_ms:Date.now()};
      });
      const cleared = await until(state => native(state)?.keys.length === 0);
      const inside = cleared.events.filter(event => event.event === "test_native_state" &&
        event.wall_time_ns / 1e6 >= stall.start_wall_ms && event.wall_time_ns / 1e6 < stall.end_wall_ms);
      assert.ok(inside.some(event => !event.paused && event.keys.length === 0 &&
        event.wall_time_ns / 1e6 - stall.start_wall_ms < 450), "native consumer clears body during the blocked browser");
      assert.ok(inside.every(event => !event.paused), "940 ms browser gap is not a full presence pause");
      await page.waitForTimeout(500);
      assert.equal(native(await read()).paused, false);
      assert.deepEqual(native(await read()).keys, [], "held W cannot replay when callbacks return");
      await page.keyboard.up("w");
      await page.keyboard.down("w");
      await until(state => native(state)?.keys.includes("w"));
      await page.keyboard.up("w");
      await until(state => native(state)?.keys.length === 0);
    } else if (["body-delay", "body-presence-loss", "body-reject", "body-denied", "state-reject"].includes(scenario)) {
      await page.keyboard.down("w");
      await until(state => native(state)?.keys.includes("w"));
      const target = scenario === "state-reject" ? "**/api/state" : "**/api/body";
      await page.route(target, async route => {
        if (["body-delay", "body-presence-loss"].includes(scenario)) {
          await new Promise(resolve => setTimeout(resolve, scenario === "body-delay" ? 450 : 1800));
          try { await route.continue(); } catch (error) {
            if (!/already handled|has been closed/.test(error.message)) throw error;
          }
        }
        else if (scenario === "body-denied") await route.fulfill({status:200, contentType:"application/json", body:'{"accepted":false}'});
        else await route.fulfill({status:503, body:"synthetic refusal"});
      });
      await until(state => scenario === "body-delay" ? native(state)?.keys.length === 0 : native(state)?.paused === true);
      if (!["body-delay", "body-presence-loss"].includes(scenario)) {
        const expected = scenario === "state-reject" ? "state-request-failed" : scenario === "body-denied" ? "body-request-rejected" : "body-request-failed";
        assert.ok(browserPauses.some(event => event.reason === expected), "browser must retain its request-loss cause");
      }
      // Stop intercepting new heartbeats before awaiting recovery. Waiting for
      // every route while those heartbeats continue can deadlock the test.
      await page.unrouteAll({behavior:"ignoreErrors"});
      if (scenario === "body-delay") {
        await page.waitForTimeout(500);
        assert.equal(native(await read()).paused, false, "450 ms delivery gap clears body, not session presence");
        assert.deepEqual(native(await read()).keys, [], "a delayed held request cannot replay movement");
      }
      await page.keyboard.up("w");
      await page.waitForTimeout(500);
      assert.equal(native(await read()).keys.length, 0);
      if (scenario !== "body-delay") await resume();
      assert.deepEqual(native(await read()).keys, []);
      if (scenario === "body-delay") {
        await page.keyboard.down("w");
        await until(state => native(state)?.keys.includes("w"));
        await page.keyboard.up("w");
        await until(state => native(state)?.keys.length === 0);
      }
    } else if (scenario === "state-delay") {
      await page.route("**/api/state", async route => {
        await new Promise(resolve => setTimeout(resolve, 450));
        try { await route.continue(); } catch {}
      });
      await page.waitForTimeout(1200);
      assert.equal(native(await read()).paused, false, "pending diagnostic state IO cannot stop body delivery");
      await page.unrouteAll({behavior:"ignoreErrors"});
    } else if (scenario === "approval-order") {
      await page.keyboard.down("w");
      await until(state => native(state)?.keys.includes("w"));
      await page.getByRole("button", {name:"Pause / release body", exact:true}).click();
      await until(state => state.pending_gate?.[0] === "resume");
      // Delay only the explicitly requested empty approval packet. The real
      // server rejects older sequences if a periodic request overtakes it.
      // This models local request scheduling, not a proven powered-run cause.
      let delayed = false;
      await page.route("**/api/body", async route => {
        if (!delayed && route.request().postDataJSON().active) {
          delayed = true;
          await page.keyboard.down("u"); // Real event during the pending empty lease.
          await new Promise(resolve => setTimeout(resolve, 200));
        }
        await route.continue();
      });
      await resume();
      assert.equal(delayed, true, "the explicit empty packet must exercise delayed delivery");
      await page.unrouteAll({behavior:"ignoreErrors"});
      const delivered = bodyReplies.filter(reply => reply.active && reply.accepted).length;
      await until(() => bodyReplies.filter(reply => reply.active && reply.accepted).length >= delivered + 2);
      assert.deepEqual(native(await read()).keys, [], "approval cannot replay held body input");
      await page.keyboard.up("w");
      await page.keyboard.up("u");
    } else if (scenario === "pending-stop") {
      await page.getByRole("button", {name:"Pause / release body", exact:true}).click();
      const current = await until(state => state.pending_gate?.[0] === "resume");
      await page.waitForFunction(epoch => document.querySelector("#gate-state").textContent
        .includes(`Approval needed: resume (host epoch ${epoch})`), current.pending_gate[1]);
      const resumesBeforeStop = operationRequests.filter(item => item.kind === "Resume").length;
      let release;
      const held = new Promise(resolve => { release = resolve; });
      await page.route("**/api/body", async route => { await held; await route.continue(); });
      await page.getByRole("button", {name:"Approve Resume", exact:true}).click();
      await page.waitForTimeout(150);
      await page.getByRole("button", {name:"Stop session", exact:true}).click();
      try { await until(state => state.phase === "complete"); } finally { release(); }
      await page.waitForTimeout(200);
      assert.equal(operationRequests.filter(item => item.kind === "Resume").length, resumesBeforeStop,
                   "Stop invalidates this delayed approval before dispatch");
    }
    if (scenario !== "pending-stop") await page.getByRole("button", {name:"Stop session", exact:true}).click();
    const done = await until(state => state.phase === "complete");
    assert.equal(done.final_exit_code, 0);
    assert.equal(done.cleanup_verified, true);
    console.log(`PASS ${scenario}: actual frontend, HTTP owner, native pipe and cleanup`);
  } catch (error) {
    console.error(JSON.stringify({scenario, bodyReplies:bodyReplies.slice(-10), bodySendIntervals:bodyTiming.slice(-20).map((v,i,a) => i ? v.sent-a[i-1].sent : 0),
                                 bodyDurations:bodyTiming.slice(-10).map(v => ({duration_ms:v.duration_ms, timing:v.timing})),
                                 bodyCallIntervals:await page.evaluate(() => testBodyCalls.slice(-20).map((v,i,a) => i ? v-a[i-1] : 0)),
                                 longTasks:await Promise.all(page.frames().map(frame => frame.evaluate(() => testLongTasks)))}));
    throw error;
  } finally { await browser.close(); }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
