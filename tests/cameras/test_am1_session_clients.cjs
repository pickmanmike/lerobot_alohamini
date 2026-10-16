"use strict";
const {test} = require("node:test"), assert = require("node:assert/strict");
const {spawn,execFileSync} = require("node:child_process"), readline = require("node:readline");
const {chromium} = require("playwright");

let sharedLiveRecipeJSON;
function sharedLiveRecipes() {
 if(!sharedLiveRecipeJSON)sharedLiveRecipeJSON=execFileSync(process.env.AM1_TEST_PYTHON,["-B","-c","import json; from examples.alohamini.am1_session_contract import LIVE_ELBOW_SPECS; print(json.dumps([spec.summary() for spec in LIVE_ELBOW_SPECS.values()]))"],{encoding:"utf8",env:process.env});
 return JSON.parse(sharedLiveRecipeJSON);
}

async function fixture(simulated=false) {
  const worker = spawn(process.env.AM1_TEST_PYTHON, ["-B", "tests/robots/am1_session_fixture.py", "--browser", ...(simulated ? ["--simulated"] : [])], {stdio:["pipe","pipe","pipe"], env:process.env});
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
    await pa.keyboard.down("z");await pa.keyboard.down("x");await pa.waitForTimeout(150);
    assert.deepEqual(await pa.evaluate(()=>[...am1Remote.held]),[],"unsupported fake Z/X must not hold or send zero targets");
    assert.equal(await pa.evaluate(()=>am1Remote.snapshot.run.intent),null);
    await pa.keyboard.up("z");await pa.keyboard.up("x");
    for(const key of ["z","x"]) {
      assert.equal(await pa.locator(`[data-body-key="${key}"]`).isDisabled(),true,"unsupported fake button must be disabled");
      assert.match(await pa.locator(`[data-body-key="${key}"]`).innerText(),/Unavailable.*fake/i);
      assert.match(await pa.locator(`[data-body-key="${key}"]`).getAttribute("aria-label"),/unavailable.*fake/i);
    }
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


for(const delayed of ["connect","release_input"]) test(`later accepted Pause fences Resume after delayed real ${delayed} reply`,async t=>{
  const f=await fixture();t.after(()=>f.close());
  const browser=await chromium.launch({headless:true,channel:process.env.AM1_TEST_BROWSER_CHANNEL || "msedge"});t.after(()=>browser.close());
  const context=await browser.newContext({ignoreHTTPSErrors:true}),page=await context.newPage();
  await enroll(page,f.info.url,f.info.a);
  await page.locator('[data-operation="ClaimInput"]').click();await page.waitForFunction(()=>am1Remote.controllerGeneration);
  await page.locator('[data-operation="Start"]').click();await page.waitForFunction(()=>am1Remote.snapshot?.run?.status==="running");
  await page.locator('[data-operation="Pause"]').click();await page.waitForFunction(()=>am1Remote.snapshot.run.status==="paused");
  await f.command({op:"delay_reply",operation:delayed});
  // Existing socket also gets an observer; this wraps send without changing real transport.
  await page.evaluate(()=>{globalThis.sentCommands=[];const ws=am1Remote.socket,send=ws.send.bind(ws);ws.send=value=>{const frame=JSON.parse(value);if(frame.command)sentCommands.push(frame.command.op);return send(value);};});
  await page.locator('[data-operation="Resume"]').click();
  const admitted=await f.command({op:"wait_reply"});assert.equal(admitted.accepted,true);
  let paused;const pauseReply=new Promise(resolve=>paused=resolve);
  await page.route("**/api/pause",async route=>{const response=await route.fetch();const result=await response.json();await route.fulfill({response});paused(result);});
  await page.locator('[data-operation="Pause"]').click();
  assert.equal((await pauseReply).accepted,true,"later protective request must actually reach owner");
  const before=await page.evaluate(()=>am1Remote.snapshot.run.progress_s);
  await f.command({op:"release_reply"});await page.waitForTimeout(400);
  const after=await page.evaluate(()=>({run:am1Remote.snapshot.run,sent:sentCommands}));
  assert.equal(after.run.status,"paused","late Resume continuation must not override later Pause");
  assert.equal(after.run.progress_s,before,"paused owner must admit no progress after late reply");
  assert.deepEqual(after.sent,delayed==="connect"?["connect"]:["connect","release_input"],"no later continuation command may be sent");
  console.log(JSON.stringify({delayedRealReply:delayed,laterPauseAccepted:true,rendered:await page.locator("#session-state").innerText(),noResumeDispatch:true}));
});

test("accepted renewal delayed across external same-device release cannot restore authority or silently reacquire",async t=>{
  const f=await fixture();t.after(()=>f.close());
  const browser=await chromium.launch({headless:true,channel:process.env.AM1_TEST_BROWSER_CHANNEL || "msedge"});t.after(()=>browser.close());
  const context=await browser.newContext({ignoreHTTPSErrors:true}),page=await context.newPage();
  await enroll(page,f.info.url,f.info.a);
  await page.locator('[data-operation="ClaimInput"]').click();await page.waitForFunction(()=>am1Remote.controllerGeneration);
  const generation=await page.evaluate(()=>am1Remote.controllerGeneration);
  let captured,release;const admitted=new Promise(resolve=>captured=resolve),gate=new Promise(resolve=>release=resolve);let once=false;
  const renewals=[];
  await page.route("**/api/claim",async route=>{
    renewals.push(route.request().postDataJSON());
    if(once)return route.continue();once=true;
    const response=await route.fetch();captured(await response.json());await gate;await route.fulfill({response});
  });
  t.after(()=>release());
  const reply=await admitted;assert.equal(reply.accepted,true);assert.equal(reply.controller_generation,generation);
  const external=await page.evaluate(async()=>{const response=await fetch("/api/release",{method:"POST",headers:{"Content-Type":"application/json","X-AM1-CSRF":am1Remote.csrf},body:JSON.stringify({operation_id:crypto.randomUUID()})});return response.json();});
  assert.equal(external.accepted,true);
  await page.waitForFunction(()=>am1Remote.snapshot.controller===null && am1Remote.controllerGeneration===null);
  await page.evaluate(()=>{globalThis.restoredGenerations=[];const render=am1Remote.render;am1Remote.render=(...args)=>{if(am1Remote.controllerGeneration)restoredGenerations.push(am1Remote.controllerGeneration);return render(...args);};});
  release();await page.waitForTimeout(1100);
  assert.deepEqual(await page.evaluate(()=>restoredGenerations),[],"late reply cannot even transiently restore lost authority");
  const state=await page.evaluate(()=>({controller:am1Remote.snapshot.controller,local:am1Remote.controllerGeneration}));
  assert.equal(state.local,null,"late accepted renewal cannot restore lost local authority");
  assert.equal(state.controller,null,"timer cannot silently acquire a replacement lease");
  assert.equal(renewals.length,1,"no further renewal after observed loss");
  assert.equal(renewals[0].controller_generation,generation,"periodic renewal must be fenced at authority");
  await page.locator(".session-details > summary").click();
  assert.match(await page.locator("#gate-state").innerText(),/Spectator/);
  await page.unroute("**/api/claim");
  await page.locator('[data-operation="ClaimInput"]').click();await page.waitForFunction(()=>am1Remote.controllerGeneration);
  assert.notEqual(await page.evaluate(()=>am1Remote.controllerGeneration),generation,"new acquisition requires explicit UI action");
  console.log(JSON.stringify({acceptedRenewalDelayed:true,externalReleaseAccepted:true,lossSnapshot:true,lateReplyFenced:true,noImplicitClaim:true,explicitReclaim:true}));
});


for(const delivery of ["delivered","delayed"]) test(`spectator Pause fences owner Resume with ${delivery} authoritative snapshot`,async t=>{
  const f=await fixture();t.after(()=>f.close());
  const browser=await chromium.launch({headless:true,channel:process.env.AM1_TEST_BROWSER_CHANNEL || "msedge"});t.after(()=>browser.close());
  const a=await browser.newContext({ignoreHTTPSErrors:true}),b=await browser.newContext({ignoreHTTPSErrors:true});
  const pa=await a.newPage(),pb=await b.newPage();
  await enroll(pa,f.info.url,f.info.a);await enroll(pb,f.info.url,f.info.b);
  assert.notEqual((await a.cookies())[0].value,(await b.cookies())[0].value);
  await pa.locator('[data-operation="ClaimInput"]').click();await pa.waitForFunction(()=>am1Remote.controllerGeneration);
  await pa.locator('[data-operation="Start"]').click();await pa.waitForFunction(()=>am1Remote.snapshot?.run?.status==="running");
  await pa.locator('[data-operation="Pause"]').click();await pa.waitForFunction(()=>am1Remote.snapshot.run.status==="paused");
  const revision=await pa.evaluate(()=>am1Remote.snapshot.run.intent_revision);
  await pa.evaluate(delivery=>{
    globalThis.sentCommands=[];globalThis.commandResults=[];globalThis.queuedSnapshot=null;
    globalThis.delaySnapshots=delivery==="delayed";
    const ws=am1Remote.socket,send=ws.send.bind(ws),receive=ws.onmessage;
    ws.send=value=>{const frame=JSON.parse(value);if(frame.command)sentCommands.push(frame.command);return send(value);};
    ws.onmessage=event=>{const frame=JSON.parse(event.data);if(frame.kind==="snapshot" && delaySnapshots){queuedSnapshot=event;return;}
      if(frame.kind==="result")commandResults.push(frame);return receive(event);};
    globalThis.deliverSnapshot=()=>{delaySnapshots=false;if(queuedSnapshot){receive(queuedSnapshot);queuedSnapshot=null;}};
  },delivery);
  await f.command({op:"delay_reply",operation:"connect"});
  await pa.locator('[data-operation="Resume"]').click();
  const connected=await f.command({op:"wait_reply"});assert.equal(connected.accepted,true);
  assert.equal(connected.snapshot.run.intent_revision,revision);
  const pauseResponse=pb.waitForResponse(response=>response.url().endsWith("/api/pause"));
  await pb.locator('[data-operation="Pause"]').click();assert.equal((await (await pauseResponse).json()).accepted,true);
  await pb.waitForFunction(rev=>am1Remote.snapshot.run.intent_revision>rev,revision);
  const paused=await pb.evaluate(()=>am1Remote.snapshot.run);
  if(delivery==="delivered")await pa.waitForFunction(rev=>am1Remote.snapshot.run.intent_revision===rev,paused.intent_revision);
  else assert.equal(await pa.evaluate(()=>am1Remote.snapshot.run.intent_revision),revision,"new snapshot still withheld");
  await f.command({op:"release_reply"});await pa.waitForTimeout(350);
  const trace=await pa.evaluate(()=>({sent:sentCommands,results:commandResults}));
  assert.equal(await pb.evaluate(()=>am1Remote.snapshot.run.status),"paused","spectator Pause must retain authority");
  assert.equal(await pb.evaluate(()=>am1Remote.snapshot.run.progress_s),paused.progress_s,"no stale Resume dispatch/progress");
  if(delivery==="delivered")assert.deepEqual(trace.sent.map(c=>c.op),["connect"],"current snapshot must cancel obsolete continuation");
  else {
    assert.deepEqual(trace.sent.map(c=>c.op),["connect","release_input","resume"]);
    assert.equal(trace.sent.at(-1).expected_intent_revision,revision,"workflow must carry initial revision");
    assert.equal(trace.results.at(-1).accepted,false,"authority must refuse stale revision without snapshot delivery");
    assert.equal(trace.results.at(-1).effect_admitted,false);
    await pa.evaluate(()=>deliverSnapshot());
  }
  await pa.waitForFunction(rev=>am1Remote.snapshot.run.status==="paused" && am1Remote.snapshot.run.intent_revision===rev,paused.intent_revision);
  assert.match(await pa.locator("#session-state").innerText(),/paused/);
  const renderedPaused=await pa.locator("#session-state").innerText();
  await pa.locator('[data-operation="Resume"]').click();await pa.waitForFunction(()=>am1Remote.snapshot.run.status==="running");
  await pb.waitForFunction(()=>am1Remote.snapshot.run.status==="running");
  console.log(JSON.stringify({contexts:2,snapshotDelivery:delivery,actualSpectatorPause:true,renderedPaused,noStaleDispatch:true,freshExplicitResume:true}));
});

test("simulated provider UI Pause and Resume preserve one seed and original deadline", async t => {
  const f=await fixture(true);
  t.after(()=>f.close());
  const browser=await chromium.launch({headless:true,channel:process.env.AM1_TEST_BROWSER_CHANNEL || "msedge"});
  t.after(()=>browser.close());
  const context=await browser.newContext({ignoreHTTPSErrors:true});
  const page=await context.newPage();
  try {
    await enroll(page,f.info.url,f.info.a);
    await page.locator('[data-operation="ClaimInput"]').click();
    await page.waitForFunction(()=>am1Remote.controllerGeneration);
    await page.locator("#fake-recipe").selectOption("sim-arm-smoke-repeat");
    await page.locator('[data-operation="Start"]').click();
    await page.waitForFunction(()=>am1Remote.snapshot?.run?.progress_s>.2);
    const before=await page.evaluate(()=>am1Remote.snapshot.run);
    await page.locator('[data-operation="Pause"]').click();
    await page.waitForFunction(()=>am1Remote.snapshot?.run?.status==="paused" && am1Remote.snapshot.evidence.pose_aligned);
    const paused=await page.evaluate(()=>am1Remote.snapshot.run.progress_s);
    await page.locator('[data-operation="Resume"]').click();
    await page.waitForFunction(p=>am1Remote.snapshot?.run?.status==="running" && am1Remote.snapshot.run.progress_s>p,paused,{timeout:3000});
    const after=await page.evaluate(()=>am1Remote.snapshot.run);
    assert.equal(after.run_id,before.run_id);
    assert.equal(after.deadline,before.deadline);
    assert.deepEqual(after.execution.original_seed,before.execution.original_seed);
    assert.equal(after.execution.admissions,1);
    assert.equal(after.recovery_episodes,0);
    await page.locator('[data-operation="Stop"]').click();
    await page.waitForFunction(()=>am1Remote.snapshot?.run?.status==="stopped");
    assert.equal(await page.evaluate(()=>am1Remote.snapshot.run.cleanup),"simulated_hold_acknowledged");
  } finally {await context.close();await browser.close();await f.close();}
});

test("loaded optional P1 view keeps media mutations scoped and task identity unchanged", async t => {
  const f=await fixture();t.after(()=>f.close());
  const browser=await chromium.launch({headless:true,channel:process.env.AM1_TEST_BROWSER_CHANNEL||"msedge"});t.after(()=>browser.close());
  const context=await browser.newContext({ignoreHTTPSErrors:true});const page=await context.newPage();
  const media=[],errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/api/session',async route=>{const response=await route.fetch();const value=await response.json();await route.fulfill({response,json:{...value,optional_p1_video:true}});});
  await page.route('**/api/media**',async route=>{
    const r=route.request();media.push({method:r.method(),url:r.url(),csrf:r.headers()['x-am1-csrf'],body:r.postData()});
    if(r.method()==='POST')await route.fulfill({status:201,headers:{'Content-Type':'application/sdp','ETag':'"fixture"','Location':'/api/media/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'},body:'v=0\r\n'});
    else await route.fulfill({status:204});
  });
  await page.addInitScript(()=>{
    globalThis.RTCPeerConnection=class {
      addTransceiver(){} close(){} async createOffer(){return {sdp:'v=0\r\na=ice-ufrag:fixture\r\na=ice-pwd:fixturepassword\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\na=mid:0\r\n'};}
      async setLocalDescription(){this.onicecandidate({candidate:{sdpMLineIndex:0,candidate:'candidate:1 1 UDP 1 127.0.0.1 5000 typ host'}});}
      async setRemoteDescription(){}
    };
  });
  await enroll(page,f.info.url,f.info.a);
  await page.locator('[data-operation="ClaimInput"]').click();await page.waitForFunction(()=>am1Remote.controllerGeneration);
  await page.locator('[data-operation="Start"]').click();await page.waitForFunction(()=>am1Remote.snapshot?.run?.status==='running');
  const before=await page.evaluate(()=>am1Remote.snapshot.run);
  await page.getByRole('button',{name:'Connect P1 view',exact:true}).click();
  await page.waitForFunction(()=>am1Remote.videoView?.handle && !am1Remote.videoView.flushing);
  await page.getByRole('button',{name:'Close view',exact:true}).click();
  await page.waitForTimeout(100);
  const after=await page.evaluate(()=>am1Remote.snapshot.run);
  assert.equal(after.run_id,before.run_id);assert.equal(after.deadline,before.deadline);
  assert.deepEqual(media.map(r=>r.method),['POST','PATCH','DELETE']);
  for(const request of media)assert.ok(request.csrf);
  assert.match(await page.locator('#am1-camera-root').innerText(),/separate from required Pi image validation/);
  await page.evaluate(()=>{am1Remote.snapshot.evidence.observation_provenance='real-p1/pi-decoded';am1Remote.render(am1Remote);});
  assert.match(await page.locator('#connection').innerText(),/Required P1 observation.*simulated/);
  assert.deepEqual(errors,[]);
  await context.close();
});

test("loaded UI identifies required local camera and keeps optional P1 separate", async t=>{
  const f=await fixture(true);t.after(()=>f.close());
  const browser=await chromium.launch({headless:true,channel:process.env.AM1_TEST_BROWSER_CHANNEL || "msedge"});t.after(()=>browser.close());
  const context=await browser.newContext({ignoreHTTPSErrors:true}),page=await context.newPage();
  await enroll(page,f.info.url,f.info.a);
  // Exercise the actual renderer with a protocol snapshot, no simulated camera backend.
  await page.evaluate(()=>{
    am1Remote.socket.onmessage=()=>{};
    Object.assign(am1Remote.snapshot.evidence,{sensing_policy:"local-camera-required",
      sensing_source:{role:"chest"},observation_provenance:"real-local-camera/pi-decoded-arrival",
      required_observation:true,optional_quality:false});
    am1Remote.render(am1Remote);
  });
  assert.match(await page.locator("#connection").innerText(),/Required local chest observation.*current images validated on Pi.*motor feedback and commands simulated/);
  await page.evaluate(()=>{am1Remote.snapshot.evidence.required_observation=false;am1Remote.render(am1Remote);});
  assert.match(await page.locator("#connection").innerText(),/Required local chest observation.*current image proof unavailable/);
  await context.close();
});

test("loaded UI distinguishes test-only physical protocol startup, native Live and measured finishing", async t=>{
  const f=await fixture();t.after(()=>f.close());
  const browser=await chromium.launch({headless:true,channel:process.env.AM1_TEST_BROWSER_CHANNEL || "msedge"});t.after(()=>browser.close());
  const context=await browser.newContext({ignoreHTTPSErrors:true}),page=await context.newPage();
  const actuatorRequests=[],errors=[];
  page.on("request",request=>{if(/\/api\/(start|body|operation|heartbeat)/.test(request.url()))actuatorRequests.push(request.url());});
  page.on("pageerror",error=>errors.push(error.message));
  await enroll(page,f.info.url,f.info.a);
  // Protocol-fixture rendering only: no physical backend, hardware feedback or Start.
  await page.evaluate(()=>{
    am1Remote.socket.onmessage=()=>{};
    am1Remote.renew=async()=>null;
    am1Remote.controllerGeneration="test-only-protocol-fixture";
    am1Remote.snapshot.recipes=["physical-arm-smoke","physical-arm-smoke-repeat","physical-arm-hold-body"];
    am1Remote.snapshot.run={run_id:"test-only-protocol-fixture",source:"protected-physical-provider",
      recipe:"physical-arm-smoke-repeat",status:"starting",progress_s:0,remaining_s:null,
      native_live_at:null,deadline:null,cleanup:null};
    Object.assign(am1Remote.snapshot.evidence,{feedback:false,native_ack:false,
      sensing_source:{role:"chest"},observation_provenance:"real-local-camera/pi-decoded-arrival",
      required_observation:false,feedback_provenance:"test-only-protocol-fixture"});
    am1Remote.render(am1Remote);
  });
  assert.match(await page.locator("#view-heading").innerText(),/Physical/);
  assert.match(await page.locator("#session-state").innerText(),/Physical session.*starting/);
  assert.match(await page.locator("#live-countdown").innerText(),/native Live.*pending/i);
  assert.match(await page.locator("#connection").innerText(),/actual motor feedback unavailable.*protected.host ACK pending/i);
  assert.match(await page.locator('[data-operation="Stop"]').getAttribute("aria-label"),/Stop physical session/);
  assert.match(await page.locator("#gate-state").textContent(),/protected.host/i);
  assert.match(await page.locator("#control-help").textContent(),/physical/i);
  for(const selector of ["#view-heading","#session-state","#connection","#gate-state","#control-help","#am1-camera-root"])
    assert.doesNotMatch(await page.locator(selector).textContent(),/Fake|simulated/i);
  await page.evaluate(()=>{
    Object.assign(am1Remote.snapshot.run,{status:"running",native_live_at:20,deadline:440,remaining_s:419});
    Object.assign(am1Remote.snapshot.evidence,{feedback:true,native_ack:true,required_observation:true});
    am1Remote.render(am1Remote);
  });
  assert.match(await page.locator("#connection").innerText(),/actual motor feedback current.*protected.host ACK current/i);
  assert.match(await page.locator("#live-countdown").innerText(),/419 s remaining/);
  await page.evaluate(()=>{am1Remote.snapshot.run.status="finishing";am1Remote.render(am1Remote);});
  assert.match(await page.locator("#session-state").innerText(),/Physical session.*finishing/);
  assert.match(await page.locator("#live-countdown").innerText(),/finishing.*cleanup.*pending/i);
  assert.doesNotMatch(await page.locator("#live-countdown").innerText(),/419 s remaining/);
  await page.evaluate(()=>{
    Object.assign(am1Remote.snapshot.run,{source:"simulated-provider",recipe:"sim-arm-smoke-repeat",
      status:"completed",progress_s:352,cleanup:"simulated_hold_acknowledged"});
    am1Remote.render(am1Remote);
  });
  assert.match(await page.locator("#view-heading").innerText(),/Physical/);
  assert.match(await page.locator("#session-state").innerText(),/Prior simulated session.*completed/);
  assert.match(await page.locator("#live-countdown").innerText(),/Prior simulated result.*simulated_hold_acknowledged/);
  assert.deepEqual(actuatorRequests,[]);assert.deepEqual(errors,[]);
  console.log(JSON.stringify({testOnlyProtocolFixture:true,physicalEvidenceProved:false,startRequests:0,
    renderedStartup:true,renderedNativeLive:true,renderedFinishing:true}));
  await context.close();
});


for(const selected of [{recipe:"physical-live-left-elbow",joint:"arm_left_elbow_flex.pos",side:"left",label:"Left elbow",profile:"LiveLeftElbow"},
  {recipe:"physical-live-right-elbow",joint:"arm_right_elbow_flex.pos",side:"right",label:"Right elbow",profile:"LiveRightElbow"}])test("loaded protocol-fixture "+selected.side+" elbow selects before Start and keeps fresh run-bound input",async t=>{
  const f=await fixture();t.after(()=>f.close());
  const browser=await chromium.launch({headless:true,channel:process.env.AM1_TEST_BROWSER_CHANNEL || "msedge"});t.after(()=>browser.close());
  const context=await browser.newContext({ignoreHTTPSErrors:true});t.after(()=>context.close());const page=await context.newPage();
  const errors=[];page.on("pageerror",error=>errors.push(error.message));
  if(process.env.AM1_TEST_TRANSPORT_ASSET)await page.route("**/assets/session_transport.js",route=>route.fulfill({contentType:"text/javascript",body:require("node:fs").readFileSync(process.env.AM1_TEST_TRANSPORT_ASSET,"utf8")}));
  await enroll(page,f.info.url,f.info.a);
  await page.locator('[data-operation="ClaimInput"]').click();await page.waitForFunction(()=>am1Remote.controllerGeneration);
  await page.locator('#fake-recipe').selectOption('fake-interactive');await page.locator('[data-operation="Start"]').click();
  await page.waitForFunction(()=>am1Remote.snapshot?.run?.status==='paused');await page.locator('[data-operation="Resume"]').click();
  await page.waitForFunction(()=>am1Remote.snapshot.run.status==='running'&&am1Remote.connection);
  const actualController=await page.evaluate(()=>am1Remote.controllerGeneration);
  // Loaded ordinary controls/transport only. Injected wire replies are not physical/backend evidence.
  await page.evaluate(({selected,liveRecipes})=>{
    const ws=am1Remote.socket,send=ws.send.bind(ws),receive=ws.onmessage;globalThis.fixtureReceive=receive;
    ws.onmessage=event=>{if(JSON.parse(event.data).kind!=="snapshot")receive(event);};
    const generation=am1Remote.controllerGeneration;globalThis.fixtureRenewals=0;const renew=am1Remote.renew.bind(am1Remote);am1Remote.renew=()=>{fixtureRenewals++;return renew();};am1Remote.connection="fixture-connection";
    am1Remote.snapshot={service_incarnation:"fixture-owner",recipes:["physical-live-left-elbow","physical-live-right-elbow"],presets:[],
      live_elbow_recipes:liveRecipes,
      controller:{device_id:am1Remote.device,controller_generation:generation},evidence:{feedback:true,native_ack:true,
        measured_positions:{[selected.joint]:4},required_observation:true},
      run:{run_id:"test-only-live-protocol",recipe:selected.recipe,source:"protected-physical-provider",status:"running",
        intent_revision:7,backend_incarnation:"fixture-host",progress_s:0,native_live_at:810,deadline:900,remaining_s:90,
        live_input:{joint:selected.joint,side:selected.side,label:selected.label,profile:selected.profile,rate_norm_per_s:1,reference_norm:selected.side==="right"?4:5,corridor_min_norm:selected.side==="right"?1:-5,corridor_max_norm:selected.side==="right"?4:5,
          cursor_norm:4,cursor_feedback_sequence:30,cursor_intent_revision:7,state:"awaiting_input"}}};
    globalThis.fixtureLiveRun=am1Remote.snapshot.run;am1Remote.snapshot.run=null;
    globalThis.liveFrames=[];globalThis.wireFrames=[];
    ws.send=serialized=>{
      const frame=JSON.parse(serialized);wireFrames.push({at:performance.now(),kind:frame.command?.op||Object.keys(frame)[0]});if(!frame.command)return send(serialized);
      liveFrames.push({command:frame.command,at:performance.now()});
      let value={accepted:true,status:frame.command.op};
      if(frame.command.op==="grant")value={accepted:true,grant_id:`fixture-grant-${liveFrames.length}`,issued:5000,expires:5000.25};
      if(frame.command.op==="release_input"&&frame.command.release_kind==="voluntary_jog"){
        const snapshot=JSON.parse(JSON.stringify(am1Remote.snapshot));snapshot.run.status="paused";snapshot.run.intent_revision++;
        Object.assign(snapshot.run.live_input,{cursor_norm:2,cursor_intent_revision:snapshot.run.intent_revision,rearm:{eligible:true,released_intent_revision:snapshot.run.intent_revision}});
        Object.assign(snapshot.evidence,{hold_acknowledged:true,pose_aligned:true,native_ack:true,acknowledged_intent_revision:snapshot.run.intent_revision});
        if(globalThis.delayVoluntaryRelease){globalThis.delayVoluntaryRelease=false;globalThis.pendingVoluntaryRelease={id:frame.id,snapshot};return;}
        receive({data:JSON.stringify({kind:"snapshot",snapshot})});
      }
      if(frame.command.op==="connect") {
        const snapshot=JSON.parse(JSON.stringify(am1Remote.snapshot)),revision=snapshot.run.intent_revision+1;snapshot.run.status="paused";snapshot.run.intent_revision=revision;
        Object.assign(snapshot.run.live_input,{cursor_norm:2,cursor_feedback_sequence:40,cursor_intent_revision:revision});
        Object.assign(snapshot.evidence,{hold_acknowledged:false,pose_aligned:false,native_ack:false,acknowledged_intent_revision:revision-1});
        value={accepted:true,connection_generation:"fixture-reconnected",snapshot};
        receive({data:JSON.stringify({kind:"snapshot",snapshot})}); // Own connection revision can arrive before its command reply.
        setTimeout(()=>{
          const qualified=JSON.parse(JSON.stringify(am1Remote.snapshot));
          Object.assign(qualified.evidence,{feedback:true,required_observation:true,hold_acknowledged:true,pose_aligned:true,native_ack:true,acknowledged_intent_revision:revision});
          receive({data:JSON.stringify({kind:"snapshot",snapshot:qualified})});
        },300);
      }
      setTimeout(()=>{
        if(frame.command.op==="resume"&&frame.command.rearm_of_intent_revision===undefined){
          globalThis.pendingExplicitResume={snapshot:JSON.parse(JSON.stringify(am1Remote.snapshot))};
          receive({data:JSON.stringify({kind:"result",id:frame.id,...value})});return;
        }
        if(frame.command.op==="resume") {
          const snapshot=JSON.parse(JSON.stringify(am1Remote.snapshot));snapshot.run.status="running";snapshot.run.intent_revision++;
          Object.assign(snapshot.run.live_input,{cursor_norm:2,cursor_feedback_sequence:40,cursor_intent_revision:snapshot.run.intent_revision,rearm:{eligible:false,released_intent_revision:null}});
          snapshot.evidence.acknowledged_intent_revision=snapshot.run.intent_revision;
          receive({data:JSON.stringify({kind:"snapshot",snapshot})});
        }
        receive({data:JSON.stringify({kind:"result",id:frame.id,...value})});
      },0);
    };
    am1Remote.render(am1Remote);
  },{selected,liveRecipes:sharedLiveRecipes()});
  const starts=[];await page.route("**/api/start",async route=>{starts.push(route.request().postDataJSON());await route.fulfill({json:{accepted:true,status:"starting"}});});
  assert.equal(await page.locator("#fake-recipe").isDisabled(),false);
  for(const option of ["physical-live-left-elbow","physical-live-right-elbow"])assert(await page.locator('#fake-recipe option[value="'+option+'"]').count());
  await page.locator("#fake-recipe").selectOption(selected.recipe);
  assert.match(await page.locator("#live-elbow-controls h2").innerText(),new RegExp(selected.label,"i"));
  if(selected.side==="right")assert.match(await page.locator("#live-elbow-controls output").innerText(),/am1-right-elbow-rest-neighbourhood-v2.*1834.*1867.*2\.984.*admission pending/i);
  assert.match(await page.locator('[data-elbow-jog="1"]').getAttribute("aria-label"),new RegExp(selected.label,"i"));
  await page.locator('[data-operation="Start"]').click();await page.waitForTimeout(100);
  assert.equal(starts.length,1);assert.equal(starts[0].recipe,selected.recipe);
  await page.evaluate(()=>{am1Remote.snapshot.run=fixtureLiveRun;am1Remote.render(am1Remote);});
  assert.equal(await page.locator("#fake-recipe").isDisabled(),true);assert.equal(await page.locator("#fake-recipe").inputValue(),selected.recipe);
  await page.evaluate(()=>{const select=document.querySelector("#fake-recipe");select.value=select.value==="physical-live-left-elbow"?"physical-live-right-elbow":"physical-live-left-elbow";select.dispatchEvent(new Event("change"));});
  assert.equal(await page.locator("#fake-recipe").inputValue(),selected.recipe);
  await assert.rejects(()=>page.evaluate(recipe=>am1Remote.start(recipe),selected.recipe==="physical-live-left-elbow"?"physical-live-right-elbow":"physical-live-left-elbow"),/active|frozen/i);
  assert.equal(starts.length,1);
  for(const key of ["w","s","a","d","u","j","z","x"])assert.equal(await page.locator(`[data-body-key="${key}"]`).isDisabled(),true);
  assert.equal(await page.locator('[data-elbow-jog="-1"]').isDisabled(),false);
  await page.keyboard.down("w");await page.keyboard.up("w");assert.deepEqual(await page.evaluate(()=>[...am1Remote.held]),[]);
  assert.equal(await page.evaluate(()=>am1Remote.elbowInput()!==null),true,"unsupported body release must not revoke the live elbow lane");
  await page.locator('[data-body-key="w"]').hover();await page.mouse.down();await page.mouse.up();
  assert.equal(await page.evaluate(()=>am1Remote.elbowInput()!==null),true,"disabled body pointer release must not revoke the live elbow lane");
  const negative=page.locator('[data-elbow-jog="-1"]');await negative.hover();await page.mouse.down();
  await page.waitForFunction(n=>liveFrames.filter(frame=>frame.command.op==="input").length>=n,selected.side==="right"?55:22);
  const before=await page.evaluate(key=>liveFrames.filter(frame=>frame.command.op==="input").at(-1).command.target[key],selected.joint);
  await page.keyboard.down("]");
  await page.waitForFunction(({key,value})=>liveFrames.filter(frame=>frame.command.op==="input").at(-1).command.target[key]>value,{key:selected.joint,value:before});
  await page.mouse.up(); // Releasing the older negative gesture cannot cancel the current positive gesture.
  await page.evaluate(()=>dispatchEvent(new Event("blur")));
  assert.equal(await page.locator('[data-elbow-jog="1"]').isDisabled(),true,"blur immediately disables stale live controls");
  const count=await page.evaluate(()=>liveFrames.filter(frame=>frame.command.op==="input").length);
  await page.waitForTimeout(350);assert.equal(await page.evaluate(()=>liveFrames.filter(frame=>frame.command.op==="input").length),count);
  await page.keyboard.up("]");await page.locator('[data-operation="Resume"]').click();
  await page.waitForFunction(()=>liveFrames.some(frame=>frame.command.op==="connect"));
  const connect=await page.evaluate(()=>liveFrames.find(frame=>frame.command.op==="connect").command);
  assert.equal(connect.host_incarnation,"fixture-host","physical connect carries the current host identity");
  assert.equal(connect.service_incarnation,"fixture-owner");assert.equal(connect.controller_generation,actualController);
  await page.waitForFunction(()=>globalThis.pendingExplicitResume);
  assert.equal(await page.evaluate(()=>Boolean(am1Remote.liveResume)),true,"accepted Resume result must retain its current qualification token");
  await page.evaluate(()=>{const s=pendingExplicitResume.snapshot;s.run.status="resuming";s.run.intent_revision=9;s.evidence.native_ack=false;fixtureReceive({data:JSON.stringify({kind:"snapshot",snapshot:s})});});
  await page.keyboard.down("[");await page.keyboard.up("[");
  assert.equal(await page.evaluate(()=>am1Remote.elbowGesture),null,"a press before current qualification is ignored, never remembered");
  await page.evaluate(()=>{const s=pendingExplicitResume.snapshot;s.run.status="running";fixtureReceive({data:JSON.stringify({kind:"snapshot",snapshot:s})});});
  assert.equal(await page.locator('[data-elbow-jog="1"]').isDisabled(),true,"running without its current native ACK and cursor keeps controls disabled");
  await page.evaluate(()=>{const s=pendingExplicitResume.snapshot;s.evidence.native_ack=true;s.evidence.acknowledged_intent_revision=9;s.run.live_input.cursor_intent_revision=9;fixtureReceive({data:JSON.stringify({kind:"snapshot",snapshot:s})});delete globalThis.pendingExplicitResume;});
  await page.waitForFunction(()=>am1Remote.snapshot.run.intent_revision===9 && !document.querySelector('[data-elbow-jog="1"]').disabled);
  const handshake=await page.evaluate(()=>liveFrames.filter(frame=>["connect","release_input","resume"].includes(frame.command.op)));
  const resumed=handshake.find(frame=>frame.command.op==="resume"),connected=handshake.find(frame=>frame.command.op==="connect");
  assert.equal(resumed.command.expected_intent_revision,8,"Resume binds exactly its own connection revision");
  assert(resumed.at-connected.at>=250,"Resume must await fresh measured hold qualification");
  assert.equal(await page.evaluate(()=>am1Remote.elbowJog),0,"Resume does not replay a remembered gesture");
  await page.keyboard.down("]");await page.waitForFunction(n=>liveFrames.filter(frame=>frame.command.op==="input").length>n,count);
  await page.evaluate(()=>{globalThis.delayVoluntaryRelease=true;});await page.keyboard.up("]");
  const voluntary=()=>page.evaluate(()=>liveFrames.filter(frame=>frame.command.release_kind==="voluntary_jog").length);
  assert.equal(await voluntary(),1,"ordinary keyup records one voluntary release");
  await negative.hover();await page.mouse.down();
  const pendingGesture=await page.evaluate(()=>({id:am1Remote.elbowGesture?.id,jog:am1Remote.elbowJog,cursor:am1Remote.elbowCursor}));
  assert(pendingGesture.id);assert.equal(pendingGesture.jog,0);assert.equal(pendingGesture.cursor,null);
  await page.evaluate(()=>{const {snapshot}=pendingVoluntaryRelease;fixtureReceive({data:JSON.stringify({kind:"snapshot",snapshot})});});
  assert.equal(await page.evaluate(()=>am1Remote.elbowGesture?.id),pendingGesture.id,"ordinary held snapshot keeps the exact new press before its release result");
  await page.evaluate(()=>{am1Remote.socket.onmessage({data:JSON.stringify({kind:"result",id:pendingVoluntaryRelease.id,accepted:true,status:"released"})});delete globalThis.pendingVoluntaryRelease;});
  await page.waitForFunction(()=>liveFrames.some(frame=>frame.command.op==="input"&&frame.command.intent_revision===12),{}, {timeout:3500});
  const auto=await page.evaluate(()=>liveFrames.filter(frame=>["connect","resume"].includes(frame.command.op)&&frame.command.rearm_of_intent_revision===10));
  assert.deepEqual(auto.map(frame=>frame.command.op),["connect","resume"],"new pointer press invokes qualified rearm without a Resume click");
  assert(auto[1].at-auto[0].at>=250);assert.equal(auto[1].command.expected_intent_revision,11);
  await page.mouse.up();await page.waitForFunction(()=>am1Remote.snapshot.run.status==="paused"&&am1Remote.snapshot.run.live_input.rearm?.eligible===true,{}, {timeout:2500});
  await page.waitForTimeout(60);assert.equal(await voluntary(),2,"pointerup followed by lost capture is one release");
  await page.keyboard.down("]");await page.waitForFunction(()=>liveFrames.some(frame=>frame.command.op==="input"&&frame.command.intent_revision===15),{}, {timeout:3500});
  const result=await page.evaluate(()=>({run:am1Remote.snapshot.run,frames:liveFrames.filter(frame=>frame.command.op==="input"),wire:wireFrames,renewals:fixtureRenewals}));
  assert.equal(result.run.run_id,"test-only-live-protocol");assert.equal(result.run.deadline,900);
  const firstFresh=result.frames.find(frame=>frame.command.intent_revision===9);
  assert(firstFresh.command.target[selected.joint]>=2 && firstFresh.command.target[selected.joint]<=2.1);
  assert(result.frames.every(frame=>Object.keys(frame.command.target).join()===selected.joint));
  for(let index=1;index<4;index++)assert(result.frames[index].at-result.frames[index-1].at>=40,"nominal cadence does not burst/replay");
  assert((result.frames[2].at-result.frames[0].at)/2<85,"immediate physical grant/input replies use the ordinary page nominal16.7Hz schedule");
  const beforeBlur=result.frames.filter(frame=>frame.command.intent_revision===7);
  const bounds=result.run.live_input;
  assert.deepEqual([bounds.corridor_min_norm-bounds.reference_norm,bounds.corridor_max_norm-bounds.reference_norm],selected.side==="right"?[-3,0]:[-10,0]);
  assert(result.frames.every(frame=>frame.command.target[selected.joint]>=bounds.corridor_min_norm && frame.command.target[selected.joint]<=bounds.corridor_max_norm),"loaded input obeys current admitted bounds");
  if(selected.side==="right") {
    assert(beforeBlur[0].command.target[selected.joint]<bounds.reference_norm,"right first fresh gesture departs negatively from q0");
    assert(beforeBlur.some(frame=>Math.abs(frame.command.target[selected.joint]-bounds.corridor_min_norm)<1e-9),"right streaming clamps at the negative endpoint");
    assert.equal(bounds.reference_norm,4,"recovery retains the original q0 independently of measured cursor2");
  }
  const maxWireWindow=Math.max(...beforeBlur.map(frame=>beforeBlur.filter(row=>row.at>=frame.at&&row.at<frame.at+1000).length))*2;
  assert(maxWireWindow+4<=40,"whole grant/input wire traffic must reserve4 handshake/ACK frames within the existing40-frame gateway window");
  assert(result.renewals>=2,"actual claimed controller keeps HTTP presence renewal enabled");
  const allWireMax=Math.max(...result.wire.map(frame=>result.wire.filter(row=>row.at>=frame.at&&row.at<frame.at+1000).length));assert(allWireMax<=40,"all outgoing TEXT frames remain within gateway40 budget");
  await page.keyboard.up("]");assert.deepEqual(errors,[]);
  await page.evaluate(()=>{am1Remote.snapshot.run.status="stopped";am1Remote.snapshot.run.uncertain=false;am1Remote.snapshot.run.cleanup="physical_readback_verified";am1Remote.render(am1Remote);});
  assert.equal(await page.locator("#fake-recipe").isDisabled(),false,"known terminal cleanup permits next preStart selection");
  await page.locator("#fake-recipe").selectOption(selected.side==="left"?"physical-live-right-elbow":"physical-live-left-elbow");
  assert.match(await page.locator("#live-elbow-controls h2").innerText(),selected.side==="left"?/right elbow/i:/left elbow/i,"next preStart preview follows the new selection");
  console.log(JSON.stringify({selectedSide:selected.side,testOnlyProtocolFixture:true,physicalEvidenceProved:false,ordinaryJogButtons:true,mappedInputSamples:result.frames.length,newGestureAfterResume:true}));
});


test("ordinary Claim after owner restart joins HTTP and current view without replaying an old nil snapshot",async t=>{
 const f=await fixture();t.after(()=>f.close());
 const browser=await chromium.launch({headless:true,channel:process.env.AM1_TEST_BROWSER_CHANNEL || "msedge"});t.after(()=>browser.close());
 const context=await browser.newContext({ignoreHTTPSErrors:true}),page=await context.newPage();t.after(()=>context.close());
 await enroll(page,f.info.url,f.info.a);
 await page.locator('[data-operation="ClaimInput"]').click();await page.waitForFunction(()=>am1Remote.controllerGeneration&&am1Remote.snapshot.controller?.controller_generation===am1Remote.controllerGeneration);
 await page.locator('[data-operation="Start"]').click();await page.waitForFunction(()=>am1Remote.snapshot.run?.status==="running");
 await page.locator('[data-operation="Stop"]').click();await page.waitForFunction(()=>am1Remote.snapshot.run?.status==="stopped");
 const retained=await page.evaluate(()=>JSON.parse(JSON.stringify(am1Remote.snapshot.run))),oldService=await page.evaluate(()=>am1Remote.snapshot.service_incarnation);
 await f.command({op:"restart_owner"});await page.reload();
 await page.waitForFunction(old=>am1Remote.connected&&am1Remote.snapshot.service_incarnation!==old&&am1Remote.snapshot.run?.status==="stopped",oldService);
 assert.equal(await page.evaluate(()=>am1Remote.snapshot.run.service_incarnation),retained.service_incarnation,"historical issuer remains unchanged");
 assert.equal(await page.evaluate(()=>am1Remote.controllerGeneration),null);
 const afterRestart=await page.evaluate(()=>JSON.parse(JSON.stringify(am1Remote.snapshot.run)));
 const starts=[];page.on("request",request=>{if(request.url().endsWith("/api/start"))starts.push(request.postDataJSON());});
 await page.evaluate(()=>{
  const ws=am1Remote.socket,receive=ws.onmessage;globalThis.claimSavedNil=JSON.stringify({kind:"snapshot",snapshot:JSON.parse(JSON.stringify(am1Remote.snapshot))});
  globalThis.claimHeldFrames=[];globalThis.claimReceive=raw=>receive.call(ws,{data:raw});
  ws.onmessage=event=>{const value=JSON.parse(event.data);if(value.kind==="snapshot")claimHeldFrames.push(event.data);else receive.call(ws,event);};
  globalThis.claimRestoreReceive=()=>{ws.onmessage=receive;};
 });
 const response=page.waitForResponse(value=>value.url().endsWith("/api/claim"));
 await page.locator('[data-operation="ClaimInput"]').click();const accepted=await (await response).json();assert.equal(accepted.accepted,true);
 await page.waitForFunction(()=>am1Remote.pendingClaim?.result?.accepted===true);
 assert.equal(await page.evaluate(()=>am1Remote.controllerGeneration),null,"HTTP alone grants no local authority");
 await page.evaluate(()=>claimReceive(claimSavedNil));
 assert.equal(await page.evaluate(()=>am1Remote.controllerGeneration),null);assert.equal(await page.evaluate(()=>Boolean(am1Remote.pendingClaim)),true);
 await page.waitForFunction(generation=>claimHeldFrames.some(raw=>JSON.parse(raw).snapshot.controller?.controller_generation===generation),accepted.controller_generation);
 await page.evaluate(generation=>{const raw=claimHeldFrames.find(raw=>JSON.parse(raw).snapshot.controller?.controller_generation===generation);claimRestoreReceive();claimReceive(raw);},accepted.controller_generation);
 await page.waitForFunction(generation=>am1Remote.controllerGeneration===generation&&am1Remote.snapshot.controller?.controller_generation===generation,accepted.controller_generation);
 await page.waitForTimeout(1100);
 const current=await page.evaluate(()=>({generation:am1Remote.controllerGeneration,controller:am1Remote.snapshot.controller,run:am1Remote.snapshot.run,service:am1Remote.snapshot.service_incarnation,connection:am1Remote.connection}));
 assert.equal(current.generation,accepted.controller_generation);assert.equal(current.controller.controller_generation,accepted.controller_generation);
 assert(current.controller.presence_expires>accepted.presence_expires,"ordinary generation-fenced renewal remains active");
 const durable=({remaining_s,trajectory_remaining_s,...run})=>run;
 assert.equal(JSON.stringify(durable(current.run)),JSON.stringify(durable(afterRestart)),"Claim cannot rewrite retained durable terminal contents");
 assert.equal(current.connection,null);assert.deepEqual(starts,[],"Claim qualification does not issue an implicit Start");
 await page.locator('[data-operation="Start"]').click();await page.waitForFunction(old=>am1Remote.snapshot.run?.run_id!==old&&am1Remote.snapshot.run.status==="running",retained.run_id);
 assert.equal(starts.length,1);assert.equal(await page.evaluate(()=>am1Remote.snapshot.run.service_incarnation),current.service);
 await page.locator('[data-operation="Stop"]').click();await page.waitForFunction(()=>am1Remote.snapshot.run.status==="stopped");
 console.log(JSON.stringify({realFakeOwnerRestart:true,oldTerminalIssuerRetained:true,syntheticAuthenticNilFrameOrdering:true,replyWaitsForCurrentClaimProof:true,generationFencedRenewal:true,noImplicitStart:true,explicitNextStart:true}));
});



test("loaded bounded renewal drops timer and owned Claim overlap then requires fresh Claim and Resume in the same run",async t=>{
 const f=await fixture();t.after(()=>f.close());
 const browser=await chromium.launch({headless:true,channel:process.env.AM1_TEST_BROWSER_CHANNEL || "msedge"});t.after(()=>browser.close());
 const context=await browser.newContext({ignoreHTTPSErrors:true});t.after(()=>context.close());const page=await context.newPage(),errors=[];
 page.on("pageerror",error=>errors.push(error.message));
 await enroll(page,f.info.url,f.info.a);
 await page.locator('[data-operation="ClaimInput"]').click();await page.waitForFunction(()=>am1Remote.controllerGeneration);
 await page.locator('#fake-recipe').selectOption('fake-interactive');await page.locator('[data-operation="Start"]').click();
 await page.waitForFunction(()=>am1Remote.snapshot?.run?.status==='paused');await page.locator('[data-operation="Resume"]').click();
 await page.waitForFunction(()=>am1Remote.snapshot?.run?.status==='running'&&am1Remote.connection);
 await page.keyboard.down('w');await page.waitForFunction(()=>am1Remote.snapshot.run.intent?.[0]===1);
 const before=await page.evaluate(()=>({run:am1Remote.snapshot.run.run_id,deadline:am1Remote.snapshot.run.deadline,generation:am1Remote.controllerGeneration}));
 await page.waitForFunction(()=>!am1Remote.pendingRenewal);
 let admitted,release;const captured=new Promise(resolve=>admitted=resolve),gate=new Promise(resolve=>release=resolve),claims=[];
 t.after(()=>release());
 await page.route("**/api/claim",async route=>{
  claims.push(route.request().postDataJSON());
  if(claims.length>1)return route.continue();
  const response=await route.fetch();admitted(await response.json());await gate;
  try{await route.fulfill({response});}catch{} // The real client aborts this expired response.
 });
 const result=await captured;assert.equal(result.accepted,true);assert.equal(result.controller_generation,before.generation);
 await page.waitForTimeout(550);await page.locator('[data-operation="ClaimInput"]').click();
 assert.equal(claims.length,1,"real500ms timer and explicit owned Claim must drop while one request is pending");
 await page.waitForFunction(()=>!am1Remote.controllerGeneration&&!am1Remote.pendingRenewal&&am1Remote.held.size===0);
 assert.equal(await page.evaluate(()=>am1Remote.connection),null);assert.equal(await page.evaluate(()=>am1Remote.elbowJog),0);
 await page.locator('.session-details > summary').click();assert.match(await page.locator('#control-notice').innerText(),/Claim.*Resume/i);
 await page.keyboard.up('w');await page.locator('[data-operation="Pause"]').click();
 await page.waitForFunction(()=>am1Remote.snapshot?.run?.status==='paused'&&am1Remote.snapshot.controller===null);
 release();await page.unroute("**/api/claim");
 await page.locator('[data-operation="ClaimInput"]').click();await page.waitForFunction(()=>am1Remote.controllerGeneration&&am1Remote.snapshot.controller?.controller_generation===am1Remote.controllerGeneration);
 assert.notEqual(await page.evaluate(()=>am1Remote.controllerGeneration),before.generation);
 await page.locator('[data-operation="Resume"]').click();await page.waitForFunction(()=>am1Remote.snapshot?.run?.status==='running'&&am1Remote.connection);
 const after=await page.evaluate(()=>({run:am1Remote.snapshot.run.run_id,deadline:am1Remote.snapshot.run.deadline,intent:am1Remote.snapshot.run.intent,held:[...am1Remote.held]}));
 assert.equal(after.run,before.run);assert.equal(after.deadline,before.deadline);assert.equal(after.intent,null);assert.deepEqual(after.held,[]);
 await page.keyboard.down('s');await page.waitForFunction(()=>am1Remote.snapshot.run.intent?.[0]===-1);await page.keyboard.up('s');
 await page.locator('[data-operation="Stop"]').click();await page.waitForFunction(()=>am1Remote.snapshot?.run?.status==='stopped');
 assert.deepEqual(errors,[]);console.log(JSON.stringify({realRenewalTimer:true,maxPendingRenewal:1,timeoutClearedRole:true,explicitFreshClaim:true,sameRunResume:true,noHeldReplay:true}));
});


test("loaded bounded renewal cannot delay ordinary Stop while its HTTP reply is unresolved",async t=>{
 const f=await fixture();t.after(()=>f.close());
 const browser=await chromium.launch({headless:true,channel:process.env.AM1_TEST_BROWSER_CHANNEL || "msedge"});t.after(()=>browser.close());
 const context=await browser.newContext({ignoreHTTPSErrors:true});t.after(()=>context.close());const page=await context.newPage();
 await enroll(page,f.info.url,f.info.a);await page.locator('[data-operation="ClaimInput"]').click();await page.waitForFunction(()=>am1Remote.controllerGeneration);
 await page.locator('[data-operation="Start"]').click();await page.waitForFunction(()=>am1Remote.snapshot?.run?.status==='running');
 await page.waitForFunction(()=>!am1Remote.pendingRenewal);
 let admitted,release;const captured=new Promise(resolve=>admitted=resolve),gate=new Promise(resolve=>release=resolve);t.after(()=>release());
 await page.route("**/api/claim",async route=>{const response=await route.fetch();admitted();await gate;try{await route.fulfill({response});}catch{}});
 await captured;assert.equal(await page.evaluate(()=>Boolean(am1Remote.pendingRenewal)),true);
 const stopped=page.waitForResponse(response=>response.url().endsWith('/api/stop'));
 await page.locator('[data-operation="Stop"]').click();assert.equal((await (await stopped).json()).accepted,true);
 await page.waitForFunction(()=>am1Remote.snapshot?.run?.status==='stopped');
 assert.equal(await page.evaluate(()=>Boolean(am1Remote.pendingRenewal)),true,"Stop completes before the unresolved renewal or its1s timeout");
 release();console.log(JSON.stringify({ordinaryStopAccepted:true,renewalStillPendingAtStop:true,protectiveLaneIndependent:true}));
});


test("workspace metadata policy identity accepts only retained v1 and reviewed v2",()=>{
  const {readFileSync}=require("node:fs"),vm=require("node:vm"),context={};
  vm.runInNewContext(readFileSync("tools/am1_console_ui/session_transport.js","utf8"),context);
  const transport=new context.AM1SessionTransport(()=>{});
  const actual=sharedLiveRecipes().find(spec=>spec.recipe==="physical-live-right-elbow");
  for(const id of ["am1-right-elbow-retained-v1","am1-right-elbow-rest-neighbourhood-v2"]) {
    const spec=structuredClone(actual);spec.supported_workspace.id=id;
    assert.equal(transport.supportedWorkspace(spec),spec.supported_workspace,id);
  }
  for(const id of ["am1-right-elbow-rest-neighbourhood-v3","foreign-workspace","",null]) {
    const spec=structuredClone(actual);spec.supported_workspace.id=id;
    assert.equal(transport.supportedWorkspace(spec),null,"unknown policy must not become supported metadata");
  }
});
