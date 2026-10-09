"use strict";
const {test} = require("node:test"), assert = require("node:assert/strict");
const {spawn} = require("node:child_process"), readline = require("node:readline");
const {chromium} = require("playwright");

async function fixture() {
  const worker = spawn(process.env.AM1_TEST_PYTHON, ["-B", "tests/robots/am1_session_fixture.py", "--browser"], {stdio:["pipe","pipe","pipe"], env:process.env});
  let error = ""; worker.stderr.on("data", data => error += data);
  const lines = readline.createInterface({input:worker.stdout});
  const pending = [];
  lines.on("line", line => pending.shift()?.resolve(JSON.parse(line)));
  worker.on("exit", () => {for(const p of pending.splice(0)) p.reject(Error(error));});
  const next = () => new Promise((resolve,reject) => pending.push({resolve,reject}));
  const info = await next();
  return {info, command: async command => {const result = next(); worker.stdin.write(JSON.stringify(command)+"\n"); return result;}, close:async () => {if(worker.exitCode!==null)return;worker.stdin.end(); await new Promise(resolve=>worker.once("exit",resolve));}};
}

async function enroll(page, url, code) {
  await page.goto(url);
  await page.waitForTimeout(300);
  if(!await page.locator("#pairing-code").count()) throw Error(await page.evaluate(()=>JSON.stringify({body:document.body.outerHTML.slice(0,700),remote:typeof AM1RemoteInitialize,app:typeof AM1BrowserInput,scripts:[...document.scripts].map(s=>s.src)})));
  await page.locator("#pairing-code").fill(code);
  await page.locator("#pair-device").click();
  await page.waitForFunction(()=>globalThis.am1Remote?.connected === true);
}

test("two enrolled loaded UIs retain one finite run through lost response, reload, spectator and gateway reconnect", async t => {
  const f = await fixture();
  t.after(()=>f.close());
  const browser = await chromium.launch({headless:true, channel:process.env.AM1_TEST_BROWSER_CHANNEL || "msedge"});
  t.after(()=>browser.close());
  const a = await browser.newContext({ignoreHTTPSErrors:true}), b = await browser.newContext({ignoreHTTPSErrors:true});
  const pa = await a.newPage(), pb = await b.newPage();
  const forbidden = [], errors = [];
  for(const p of [pa,pb]) {
    p.on("request", req=>{if(/\/api\/(body|operation|heartbeat)|\/camera\//.test(req.url())) forbidden.push(req.url());});
    p.on("pageerror", e=>errors.push(e.message));
  }
  try {
    await pa.addInitScript(()=>localStorage.setItem("am1-control-owner","legacy-owner-must-not-restore"));
    await enroll(pa,f.info.url,f.info.a);
    await enroll(pb,f.info.url,f.info.b);
    assert.notEqual((await a.cookies())[0].value,(await b.cookies())[0].value);
    await pa.locator('[data-operation="ClaimInput"]').click();
    await pa.waitForFunction(()=>am1Remote.controllerGeneration);
    let lost=false;
    await pa.route("**/api/start", async route=> {if(!lost) {lost=true; await route.fetch(); await route.abort();} else await route.continue();});
    await pa.locator('[data-operation="Start"]').click();
    await pa.waitForFunction(()=>am1Remote.snapshot?.run?.status==="running");
    await pa.unroute("**/api/start");
    const before = await pa.evaluate(()=>am1Remote.snapshot);
    assert.match(await pa.locator("#session-state").innerText(), /Fake.*running/i);
    await pb.waitForFunction(id=>am1Remote.snapshot?.run?.run_id===id,before.run.run_id);
    assert.equal(await pb.evaluate(()=>am1Remote.controllerGeneration),null,"viewing must not claim");
    await pa.reload();
    await pa.waitForFunction(()=>am1Remote.connected);
    assert.equal(await pa.evaluate(()=>am1Remote.snapshot.run.run_id),before.run.run_id);
    assert.equal(await pa.evaluate(()=>am1Remote.snapshot.run.deadline),before.run.deadline);
    assert.equal(await pa.evaluate(()=>am1Remote.controllerGeneration),null,"reload must not restore authority");
    const progress = await pa.evaluate(()=>am1Remote.snapshot.run.progress_s);
    const restart = await f.command({op:"restart_gateway"});
    assert.equal(restart.url,f.info.url);
    await pa.waitForFunction(p=>am1Remote.connected && am1Remote.snapshot?.run?.progress_s>p+.2,progress);
    await pb.waitForFunction(()=>am1Remote.connected);
    assert.equal(await pb.evaluate(()=>am1Remote.snapshot.run.seed),before.run.seed);
    assert.equal(await pa.evaluate(()=>am1Remote.snapshot.events.filter(e=>e.kind==="start").length),1);
    assert.match(await pb.locator("#connection").innerText(), /Cameras.*unavailable.*fake/i);
    await pb.locator('[data-operation="Pause"]').click();
    await pb.waitForFunction(()=>am1Remote.snapshot.run.status==="paused");
    await pb.locator('[data-operation="Stop"]').click();
    await pa.waitForFunction(()=>am1Remote.snapshot.run.status==="stopped");
    assert.deepEqual(forbidden,[]); assert.deepEqual(errors,[]);
    console.log(JSON.stringify({contexts:2,distinctCookies:true,lostStartRetried:true,run_id:before.run.run_id,deadlinePreserved:true,reconnectedOrigin:restart.url,renderedStop:await pa.locator("#session-state").innerText(),forbiddenRequests:forbidden.length}));
  } finally {await a.close();await b.close();await browser.close();await f.close();}
});
test("late REST claim cannot restore authority after reconnect and interactive UI handoff clears input", async t => {
  const f=await fixture();
  t.after(()=>f.close());
  const browser=await chromium.launch({headless:true,channel:process.env.AM1_TEST_BROWSER_CHANNEL || "msedge"});
  t.after(()=>browser.close());
  const a=await browser.newContext({ignoreHTTPSErrors:true}),b=await browser.newContext({ignoreHTTPSErrors:true});
  const pa=await a.newPage(),pb=await b.newPage();
  try {
    await enroll(pa,f.info.url,f.info.a);await enroll(pb,f.info.url,f.info.b);
    let reply,release;
    const captured=new Promise(resolve=>reply=resolve), gate=new Promise(resolve=>release=resolve);
    let once=false;
    await pa.route("**/api/claim",async route=>{if(once)return route.continue();once=true;const response=await route.fetch();reply();await gate;await route.fulfill({response});});
    await pa.locator('[data-operation="ClaimInput"]').click();await captured;
    const oldEpoch=await pa.evaluate(()=>am1Remote.epoch);
    await pa.evaluate(()=>am1Remote.socket.close());
    await pa.waitForFunction(epoch=>am1Remote.connected && am1Remote.epoch>epoch,oldEpoch);
    release();await pa.waitForTimeout(150);
    assert.equal(await pa.evaluate(()=>am1Remote.controllerGeneration),null,"delayed old claim must remain fenced");
    await pa.unroute("**/api/claim");
    await pa.waitForTimeout(1600);
    assert.equal(await pa.evaluate(()=>am1Remote.snapshot.controller),null,"late claim must not resume renewal");
    await pa.locator('[data-operation="ClaimInput"]').click();await pa.waitForFunction(()=>am1Remote.controllerGeneration);
    await pa.locator("#fake-recipe").selectOption("fake-interactive");
    await pa.locator('[data-operation="Start"]').click();await pa.waitForFunction(()=>am1Remote.snapshot?.run?.status==="paused");
    await pa.locator('[data-operation="Resume"]').click();await pa.waitForFunction(()=>am1Remote.snapshot.run.status==="running"&&am1Remote.connection);
    await pa.keyboard.down("w");await pa.waitForFunction(()=>am1Remote.snapshot.run.intent?.[0]===1);
    await pa.evaluate(()=>dispatchEvent(new Event("blur")));
    await pa.waitForFunction(()=>am1Remote.snapshot.run.intent===null);
    await pa.keyboard.up("w");
    await pa.locator("#handoff-device").fill(await pb.evaluate(()=>am1Remote.device));
    await pa.locator('[data-operation="Handoff"]').click();
    await pb.waitForFunction(()=>am1Remote.snapshot.controller?.device_id===am1Remote.device && am1Remote.snapshot.run.status==="paused");
    assert.equal(await pb.evaluate(()=>am1Remote.controllerGeneration),null,"handoff target still explicitly claims");
    await pb.locator('[data-operation="ClaimInput"]').click();await pb.waitForFunction(()=>am1Remote.controllerGeneration);
    await pb.locator('[data-operation="Resume"]').click();await pb.waitForFunction(()=>am1Remote.snapshot.run.status==="running");
    assert.equal(await pb.evaluate(()=>am1Remote.snapshot.run.intent),null,"fresh Resume cannot replay A's keys");
    await pb.reload();await pb.waitForFunction(()=>am1Remote.connected);
    await pb.waitForTimeout(350);assert.equal(await pb.evaluate(()=>am1Remote.snapshot.run.intent),null);
    await pa.locator('[data-operation="Stop"]').click();await pa.waitForFunction(()=>am1Remote.snapshot.run.status==="stopped");
    console.log(JSON.stringify({delayedClaimFenced:true,interactiveBlurHeld:true,explicitHandoff:true,reloadNoReplay:true,spectatorStop:true,contexts:2}));
  } finally {await a.close();await b.close();await browser.close();await f.close();}
});
