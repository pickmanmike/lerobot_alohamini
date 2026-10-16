"use strict";
const {test}=require("node:test"),assert=require("node:assert/strict");
const {readFileSync}=require("node:fs"),vm=require("node:vm"),{execFileSync}=require("node:child_process");
const context={crypto:require("node:crypto").webcrypto,performance,setTimeout,clearTimeout};
vm.runInNewContext(readFileSync("tools/am1_console_ui/session_transport.js","utf8"),context);
const recipes=JSON.parse(execFileSync(process.env.AM1_TEST_PYTHON,["-B","-c","import json; from examples.alohamini.am1_session_contract import LIVE_ELBOW_SPECS; print(json.dumps([s.summary() for s in LIVE_ELBOW_SPECS.values()]))"],{encoding:"utf8"}));
const selections=["physical-live-right-elbow-practical-p16","physical-live-left-elbow-practical-p16"];
for(const recipe of selections)test(`ordinary ${recipe} has a closed ten-degree page selection`,async()=>{
 const transport=new context.AM1SessionTransport(()=>{});
 transport.snapshot={live_elbow_recipes:recipes,recipes:recipes.map(s=>s.recipe)};
 transport.controllerGeneration="current-controller";
 const spec=transport.liveElbowSpec(recipe);
 assert(spec,"ordinary recipe must be selectable from the current trusted catalog");
 assert.equal(spec.current_cap_ma,500);assert.equal(spec.live_s,180);
 assert.equal(transport.supportedWorkspace(spec),spec.supported_workspace);
 const starts=[];transport.post=async(path,payload)=>{starts.push({path,payload});return {accepted:true};};
 await transport.start(recipe);
 assert.equal(starts.length,1);assert.equal(starts[0].path,"start");
 assert.deepEqual(Object.keys(starts[0].payload).sort(),["controller_generation","operation_id","recipe"]);
 assert.equal(starts[0].payload.recipe,recipe);
 for(const mutate of [s=>s.supported_workspace.command_max_raw++,s=>s.supported_workspace.maximum_excursion_degrees=20,
  s=>s.supported_workspace.context_sha256="a".repeat(64),s=>s.supported_workspace.command_min_norm-=.1,
  s=>s.rate_norm_per_s+=.001,s=>s.supported_workspace.config.recipe=selections.find(x=>x!==recipe)]) {
  const bad=structuredClone(spec);mutate(bad);transport.snapshot.live_elbow_recipes=recipes.map(s=>s.recipe===recipe?bad:s);
  assert.equal(transport.liveElbowSpec(recipe),null,"ordinary metadata may not widen or replace the selected preset");
  await assert.rejects(()=>transport.start(recipe),/allowlisted/);
 }
});

async function pageFixture(t) {
 const {spawn}=require("node:child_process"),readline=require("node:readline"),{chromium}=require("playwright");
 const worker=spawn(process.env.AM1_TEST_PYTHON,["-B","tests/robots/am1_session_fixture.py","--browser"],{stdio:["pipe","pipe","pipe"],env:process.env});
 let stderr="";worker.stderr.on("data",data=>stderr+=data);
 const info=await new Promise((resolve,reject)=>{readline.createInterface({input:worker.stdout}).once("line",line=>resolve(JSON.parse(line)));worker.once("exit",()=>reject(Error(stderr)));});
 t.after(async()=>{if(worker.exitCode===null){worker.stdin.end();await new Promise(resolve=>worker.once("exit",resolve));}});
 const browser=await chromium.launch({headless:true,channel:process.env.AM1_TEST_BROWSER_CHANNEL||"msedge"});t.after(()=>browser.close());
 const ctx=await browser.newContext({ignoreHTTPSErrors:true});t.after(()=>ctx.close());
 const page=await ctx.newPage();await page.goto(info.url);await page.locator("#pairing-code").fill(info.a);await page.locator("#pair-device").click();
 await page.waitForFunction(()=>globalThis.am1Remote?.connected===true);
 await page.locator('[data-operation="ClaimInput"]').click();await page.waitForFunction(()=>am1Remote.controllerGeneration);
 return page;
}

for(const recipe of selections)test(`ordinary loaded ${recipe} uses Start Resume bounded streaming and cancellable rearm`,async t=>{
 const page=await pageFixture(t),spec=recipes.find(s=>s.recipe===recipe);
 assert(spec,"current native catalog must include the ordinary selection");
 const starts=[];await page.route("**/api/start",async route=>{starts.push(route.request().postDataJSON());await route.fulfill({json:{accepted:true,status:"starting"}});});
 await page.evaluate(({spec,recipes})=>{
  const receive=am1Remote.socket.onmessage;
  am1Remote.socket.onmessage=event=>{if(JSON.parse(event.data).kind!=="snapshot")receive(event);};
  const generation=am1Remote.controllerGeneration;am1Remote.renew=async()=>({accepted:true});
  const w=spec.supported_workspace;
  const reference=spec.side==="right"?w.command_min_norm:w.command_max_norm;
  am1Remote.snapshot={service_incarnation:"fixture-owner",recipes:recipes.map(s=>s.recipe),presets:[],live_elbow_recipes:recipes,
   controller:{device_id:am1Remote.device,controller_generation:generation},run:null,
   evidence:{feedback:true,native_ack:true,required_observation:true,hold_acknowledged:true,pose_aligned:true,acknowledged_intent_revision:0}};
  globalThis.ordinaryRun={run_id:"test-only-ordinary-protocol",recipe:spec.recipe,source:"protected-physical-provider",status:"paused",
   intent_revision:0,backend_incarnation:"fixture-host",progress_s:0,native_live_at:10,deadline:190,remaining_s:180,
   live_input:{joint:spec.joint,side:spec.side,label:spec.label,profile:spec.profile,rate_norm_per_s:spec.rate_norm_per_s,
    reference_norm:reference,cursor_norm:reference,corridor_min_norm:w.command_min_norm,corridor_max_norm:w.command_max_norm,
    measured_origin_raw:spec.side==="right"?w.command_min_raw:w.command_max_raw,
    corridor_min_raw:w.command_min_raw,corridor_max_raw:w.command_max_raw,admission_version:2,
    workspace:Object.fromEntries(["version","id","context_sha256","evidence_min_raw","evidence_max_raw"].map(k=>[k,w[k]])),
    cursor_feedback_sequence:1,cursor_intent_revision:0,state:"held",rearm:{eligible:false,released_intent_revision:null}}};
  globalThis.ordinaryFrames=[];
  const deliver=snapshot=>receive({data:JSON.stringify({kind:"snapshot",snapshot})});
  am1Remote.socket.send=serialized=>{
   const frame=JSON.parse(serialized);if(!frame.command)return;const c=frame.command;ordinaryFrames.push(c);
   let result={accepted:true,status:c.op};
   if(c.op==="grant")result={accepted:true,grant_id:crypto.randomUUID(),issued:10,expires:10.25};
   if(c.op==="connect"){
    const s=structuredClone(am1Remote.snapshot);s.run.status="paused";s.run.intent_revision++;
    s.run.live_input.cursor_intent_revision=s.run.intent_revision;s.run.live_input.cursor_feedback_sequence++;
    Object.assign(s.evidence,{hold_acknowledged:false,pose_aligned:false,native_ack:false});
    result={accepted:true,connection_generation:crypto.randomUUID(),snapshot:s};deliver(s);
    setTimeout(()=>{const q=structuredClone(am1Remote.snapshot);Object.assign(q.evidence,{hold_acknowledged:true,pose_aligned:true,native_ack:true,
     acknowledged_intent_revision:q.run.intent_revision});deliver(q);},250);
   }
   if(c.op==="release_input"&&c.release_kind==="voluntary_jog"){
    const s=structuredClone(am1Remote.snapshot);s.run.status="paused";s.run.intent_revision++;
    Object.assign(s.run.live_input,{cursor_intent_revision:s.run.intent_revision,rearm:{eligible:true,released_intent_revision:s.run.intent_revision}});
    Object.assign(s.evidence,{hold_acknowledged:true,pose_aligned:true,native_ack:true,acknowledged_intent_revision:s.run.intent_revision});deliver(s);
   }
   if(c.op==="resume"){
    const s=structuredClone(am1Remote.snapshot);s.run.status="running";s.run.intent_revision++;
    s.run.live_input.cursor_intent_revision=s.run.intent_revision;s.run.live_input.rearm.eligible=false;
    s.evidence.acknowledged_intent_revision=s.run.intent_revision;deliver(s);
   }
   setTimeout(()=>receive({data:JSON.stringify({kind:"result",id:frame.id,...result})}),0);
  };
  am1Remote.render(am1Remote);
 },{spec,recipes});
 await page.locator("#fake-recipe").selectOption(recipe);
 assert.match(await page.locator("#live-elbow-controls output").innerText(),new RegExp(`raw ${spec.supported_workspace.command_min_raw} to ${spec.supported_workspace.command_max_raw}`));
 await page.locator('[data-operation="Start"]').click();await page.waitForTimeout(80);
 assert.equal(starts.length,1);assert.equal(starts[0].recipe,recipe);
 assert.deepEqual(Object.keys(starts[0]).sort(),["controller_generation","operation_id","recipe"]);
 await page.evaluate(()=>{am1Remote.snapshot.run=ordinaryRun;am1Remote.render(am1Remote);});
 assert.equal(await page.locator("#fake-recipe").isDisabled(),true);
 await page.locator('[data-operation="Resume"]').click();
 await page.waitForFunction(()=>am1Remote.snapshot.run.status==="running"&&am1Remote.elbowInput());
 const direction=spec.side==="right"?1:-1,button=page.locator(`[data-elbow-jog="${direction}"]`);
 const box=await button.boundingBox();await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.down();await page.waitForTimeout(2650);
 await page.mouse.up();
 await page.waitForFunction(()=>am1Remote.snapshot.run.live_input.rearm.eligible);
 const inputs=await page.evaluate(()=>ordinaryFrames.filter(c=>c.op==="input"));assert(inputs.length>=20);
 const w=spec.supported_workspace;
 for(const input of inputs){assert.deepEqual(Object.keys(input.target),[spec.joint]);assert(input.target[spec.joint]>=w.command_min_norm&&input.target[spec.joint]<=w.command_max_norm);}
 assert.equal(inputs.at(-1).target[spec.joint],direction===1?w.command_max_norm:w.command_min_norm,"page caps the held gesture at the ordinary endpoint");
 const count=inputs.length;
 await page.mouse.down();await page.waitForTimeout(60);
 await page.evaluate(()=>window.dispatchEvent(new Event("blur")));
 await page.waitForTimeout(350);
 assert.equal(await page.evaluate(()=>ordinaryFrames.filter(c=>c.op==="input").length),count,"blur cancels pending ordinary rearm");
 assert.equal(await page.evaluate(()=>am1Remote.snapshot.run.deadline),190);
});

for(const recipe of selections)test(`ordinary ${recipe} refuses widened admitted page bounds`,()=>{
 const spec=recipes.find(s=>s.recipe===recipe),w=spec.supported_workspace;
 const transport=new context.AM1SessionTransport(()=>{});
 transport.connected=transport.control=true;transport.controllerGeneration="owner";transport.connection="connection";
 const descriptor=Object.fromEntries(["version","id","context_sha256","evidence_min_raw","evidence_max_raw"].map(k=>[k,w[k]]));
 const value={joint:spec.joint,side:spec.side,label:spec.label,profile:spec.profile,rate_norm_per_s:spec.rate_norm_per_s,
  reference_norm:w.command_min_norm,cursor_norm:w.command_min_norm,cursor_feedback_sequence:1,cursor_intent_revision:0,
  corridor_min_norm:w.command_min_norm,corridor_max_norm:w.command_max_norm,corridor_min_raw:w.command_min_raw,
  corridor_max_raw:w.command_max_raw,admission_version:2,workspace:descriptor};
 transport.snapshot={live_elbow_recipes:recipes,run:{recipe,status:"running",source:"protected-physical-provider",intent_revision:0,backend_incarnation:"host",live_input:value}};
 assert(transport.elbowInput());
 for(const mutate of [v=>v.corridor_max_norm+=.1,v=>v.corridor_min_raw--,v=>v.workspace.context_sha256="a".repeat(64),v=>v.admission_version=1]){
  const bad=structuredClone(value);mutate(bad);transport.snapshot.run.live_input=bad;
  assert.equal(transport.elbowInput(),null,"page must not stream widened or foreign admitted practical geometry");
 }
});
