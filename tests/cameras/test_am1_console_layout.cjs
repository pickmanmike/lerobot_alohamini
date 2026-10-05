"use strict";
const {test} = require("node:test"), assert = require("node:assert/strict");
const {chromium} = require("playwright");
const {createFixture} = require("./am1_console_layout_fixture.cjs");
test("compact camera-first Control fits measured half viewport and preserves interaction nodes", async () => {
  const {server, fixture} = createFixture();
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const browser = await chromium.launch({headless:true, channel:process.env.AM1_TEST_BROWSER_CHANNEL || "msedge"});
  const page = await browser.newPage({viewport:{width:Number(process.env.AM1_HALF_WIDTH || 768),
                                             height:Number(process.env.AM1_HALF_HEIGHT || 780)}});
  try {
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    fixture.jpeg = Buffer.from(await page.evaluate(() => {
      const c=document.createElement("canvas"); c.width=640; c.height=480;
      const ctx=c.getContext("2d"); ctx.fillStyle="#203b45"; ctx.fillRect(0,0,640,480);
      ctx.fillStyle="#ddebea"; ctx.font="32px sans-serif"; ctx.fillText("Synthetic camera fixture",70,240);
      return c.toDataURL("image/jpeg").split(",")[1];
    }), "base64");
    await page.waitForFunction(() => document.querySelector("#primary").classList.contains("fresh"));
    const visiblePreviews = page.locator("#thumbnails .camera-slot:visible");
    assert.equal(await visiblePreviews.count(), 4, "focused camera must not be duplicated");
    assert.equal(await page.locator("[data-body-key]").count(), 8);
    const layout = await page.evaluate(() => {
      const controls=[...document.querySelectorAll('[data-body-key], .control-actions button, #primary, #thumbnails')]
        .filter(e=>e.getBoundingClientRect().width);
      return {maxBottom:Math.max(...controls.map(e=>e.getBoundingClientRect().bottom)),
        width:innerWidth, height:innerHeight, scrollWidth:document.documentElement.scrollWidth,
        minimumButtonHeight:Math.min(...[...document.querySelectorAll('[data-body-key]')].map(e=>e.getBoundingClientRect().height))};
    });
    assert.ok(layout.maxBottom <= layout.height, JSON.stringify(layout));
    assert.ok(layout.scrollWidth <= layout.width, JSON.stringify(layout));
    assert.ok(layout.minimumButtonHeight >= 42, "readable touch controls must not be shrunk");
    await page.waitForFunction(()=>document.querySelector('#connection').textContent.includes('Cameras 5/5'));
    fixture.primaryAgeMs=600;
    await page.waitForFunction(()=>document.querySelector('#primary .camera-status').textContent.includes('Last frame'));
    assert.match(await page.locator('#connection').innerText(),/Cameras 4\/5/,
      "count visible primary against its 500 ms limit, not the hidden duplicate's 1500 ms limit");
    fixture.primaryAgeMs=0;
    await page.waitForFunction(()=>document.querySelector('#primary').classList.contains('fresh'));
    const wheel=page.locator('[data-body-key="w"]');
    const originalHandle=await wheel.elementHandle();
    await wheel.focus();
    await page.waitForFunction(()=>!document.querySelector('#control-tooltip').hidden);
    const tip=await page.locator('#control-tooltip').boundingBox();
    const stopBox=await page.locator('[data-operation="Stop"]').boundingBox();
    assert.ok(tip.y >= stopBox.y+stopBox.height, "explanation must not cover Stop");
    const before=fixture.requests.length;
    await page.keyboard.press("Escape");
    assert.equal(await wheel.evaluate(el=>el===document.activeElement), true);
    assert.equal(await page.locator("#control-tooltip").isVisible(), false);
    assert.equal(fixture.requests.length, before, "tooltip dismissal must be non-actuating");
    await page.waitForTimeout(650);
    assert.equal(await originalHandle.evaluate(el=>el===document.querySelector('[data-body-key="w"]')), true);
    await page.locator('.slot-wrist_right .view').click();
    await page.waitForFunction(()=>document.querySelector('#primary').dataset.role==='wrist_right');
    assert.equal(await visiblePreviews.count(), 4);
    fixture.camerasAvailable=false;
    await page.waitForFunction(()=>document.querySelector('#primary .camera-status').textContent.includes('Last frame'));
    assert.match(await page.locator('#primary .camera-status').innerText(), /Last frame.*image/i);
    await page.locator('[data-help="touch"]').click();
    assert.equal(fixture.requests.length,before, "Help must not initiate an operation");
    for (const route of ["servos","system","logs","terminal"]) {
      await page.locator(`nav a[href="#${route}"]`).click();
      const stop=await page.locator('[data-operation="Stop"]').boundingBox();
      assert.ok(stop && stop.y >= 0 && stop.y+stop.height <= layout.height);
    }
    await page.locator('nav a[href="#control"]').click();
    await page.setViewportSize({width:480,height:640});
    await page.evaluate(()=>window.scrollTo(0,document.documentElement.scrollHeight));
    const accessibleStop=await page.locator('[data-operation="Stop"]').boundingBox();
    assert.ok(accessibleStop.y >= 0 && accessibleStop.y+accessibleStop.height <= 640,
      "Stop must remain accessible when readable controls require scrolling");
  } finally { await browser.close(); server.closeAllConnections(); await new Promise(r=>server.close(r)); }
});

test("actual DOM preserves native timing across pause/refresh and Stop remains usable during startup", async () => {
  const {server,fixture}=createFixture();
  fixture.snapshot={session_id:"fixture-only",phase:"host_ready",events:[],telemetry:{},
    progress:{startup:{step:3,total:7,stage:"lift_home",elapsed_s:1},live_timing:{state:"Unavailable"}}};
  await new Promise(r=>server.listen(0,"127.0.0.1",r));
  const browser=await chromium.launch({headless:true,channel:"msedge"});
  const page=await browser.newPage({viewport:{width:767,height:786}});
  try {
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    await page.waitForFunction(()=>document.querySelector('#startup-state').textContent.includes('Step 3 of 7'));
    await page.locator('[data-operation="Stop"]').click();
    assert.equal(fixture.requests.filter(r=>r.kind==="Stop").length,1);
    assert.doesNotMatch(await page.locator('#session-state').innerText(),/cleanup verified/i,
      "a Stop acknowledgement must not imply cleanup completion");
    fixture.snapshot={...fixture.snapshot,phase:"paused",pending_gate:["resume",3],
      progress:{startup:null,live_timing:{state:"Current",remaining_s:74,age_s:0,deadline:190}}};
    await page.waitForFunction(()=>document.querySelector('#live-countdown').textContent.includes('1:14'));
    assert.match(await page.locator('#session-state').innerText(),/Paused.*Resume/);
    fixture.snapshot.progress.live_timing.remaining_s=73;
    await page.reload();
    await page.waitForFunction(()=>document.querySelector('#live-countdown').textContent.includes('1:13'));
    assert.equal(fixture.requests.filter(r=>r.kind==="Start").length,0,"refresh only attaches to display");
    fixture.snapshot.progress.live_timing={state:"Stale",remaining_s:72,age_s:3};
    await page.waitForFunction(()=>document.querySelector('#live-countdown').textContent.includes('stale'));
    fixture.snapshot.phase="stopping";
    await page.waitForFunction(()=>document.querySelector('#session-state').textContent.includes('Stopping'));
    assert.doesNotMatch(await page.locator('#session-state').innerText(),/Stopped by operator/);
  } finally {await browser.close();server.closeAllConnections();await new Promise(r=>server.close(r));}
});
