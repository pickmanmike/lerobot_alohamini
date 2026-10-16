"use strict";
const {test}=require("node:test"), assert=require("node:assert/strict"), fs=require("node:fs"), vm=require("node:vm"), path=require("node:path");
const source=fs.readFileSync(path.join(__dirname,"../../tools/am1_console_ui/session_transport.js"),"utf8");
function fixture(fetch) {
  let now=1000;
  const timers=[], renders=[], context={fetch,AbortController,crypto:require("node:crypto").webcrypto,console,atob,TextDecoder,Uint8Array,performance:{now:()=>now},
    setTimeout:(f,t)=>{timers.push([f,t]);return timers.length;},clearTimeout:id=>{timers[id-1]=null;},
    location:{origin:"https://test.invalid"},WebSocket:class {static OPEN=1; constructor(){this.readyState=1;} send(){} close(){this.readyState=3;this.onclose?.();}}};
  vm.runInNewContext(source,context);
  const transport=new context.AM1SessionTransport((t,notice)=>renders.push(notice));
  transport.connected=true;transport.controllerGeneration="claimed";
  transport.snapshot={run:{run_id:"old-run",status:"finishing"}};
  return {transport,timers,renders,setNow:value=>{now=value;},async retry(){const i=timers.findIndex(Boolean);assert(i>=0,"bounded reconnect is pending");const [f]=timers[i];timers[i]=null;await f();}};
}

let sharedLiveRecipeJSON;
function sharedLiveRecipes() {
 if(!sharedLiveRecipeJSON){const python=process.env.AM1_TEST_PYTHON,code="import json; from examples.alohamini.am1_session_contract import LIVE_ELBOW_SPECS; print(json.dumps([spec.summary() for spec in LIVE_ELBOW_SPECS.values()]))";
  sharedLiveRecipeJSON=require("node:child_process").execFileSync(python||"uv",python?["-B","-c",code]:["run","--no-sync","python","-B","-c",code],{cwd:path.join(__dirname,"../.."),encoding:"utf8",env:process.env});}
 return JSON.parse(sharedLiveRecipeJSON);
}

test("practical live metadata admits its larger mapped rate while legacy metadata remains bounded",()=>{
 const f=fixture(),practical={recipe:"physical-live-right-elbow-practical",joint:"arm_right_elbow_flex.pos",side:"right",label:"Right elbow practical",
  profile:"LiveRightElbowPractical",rate_norm_per_s:5*4096/360*200/2212,current_cap_ma:500,live_s:180,practical_motion:true};
 f.transport.snapshot.live_elbow_recipes=[...sharedLiveRecipes(),practical].filter((spec,index,all)=>all.findIndex(other=>other.recipe===spec.recipe)===index);
 assert.equal(f.transport.liveElbowSpec(practical.recipe)?.profile,"LiveRightElbowPractical");
 for(const mutation of [spec=>spec.rate_norm_per_s+=.01,spec=>spec.practical_motion=false,spec=>spec.live_s=301,spec=>spec.current_cap_ma=501]){
  const changed=structuredClone(practical);mutation(changed);f.transport.snapshot.live_elbow_recipes=[changed];
  assert.equal(f.transport.liveElbowSpec(practical.recipe),null);
 }
 const legacy=sharedLiveRecipes().find(spec=>spec.recipe==="physical-live-right-elbow");legacy.rate_norm_per_s=practical.rate_norm_per_s;
 f.transport.snapshot.live_elbow_recipes=[legacy];assert.equal(f.transport.liveElbowSpec(legacy.recipe),null);
});
test("the actual shared right workspace summary decodes its full closed11-field schema",()=>{
 const f=fixture(),spec=sharedLiveRecipes().find(spec=>spec.side==="right"),workspace=f.transport.supportedWorkspace(spec);
 assert.equal(Object.keys(spec.supported_workspace).length,11);assert(workspace,"actual policy summary must remain visible before Start");
 assert.equal(workspace.command_min_raw,1834);assert.equal(workspace.command_max_raw,1867);
 assert.equal(workspace.command_max_norm-workspace.command_min_norm,workspace.span_norm);
 assert.equal(f.transport.supportedWorkspace(sharedLiveRecipes().find(spec=>spec.side==="left")),null);
});
for(const mutation of ["missing_min_norm","missing_max_norm","inverted_norm","nonfinite_min","nonfinite_max","string_norm","inconsistent_span","wide_span","foreign_key","raw_outside_evidence"])test("shared workspace summary refuses "+mutation,()=>{
 const f=fixture(),spec=sharedLiveRecipes().find(spec=>spec.side==="right"),w=spec.supported_workspace;
 if(mutation==="missing_min_norm")delete w.command_min_norm;
 if(mutation==="missing_max_norm")delete w.command_max_norm;
 if(mutation==="inverted_norm")w.command_min_norm=w.command_max_norm;
 if(mutation==="nonfinite_min")w.command_min_norm=NaN;
 if(mutation==="nonfinite_max")w.command_max_norm=Infinity;
 if(mutation==="string_norm")w.command_min_norm=String(w.command_min_norm);
 if(mutation==="inconsistent_span")w.span_norm-=.1;
 if(mutation==="wide_span"){w.command_max_norm=w.command_min_norm+3.01;w.span_norm=3.01;}
 if(mutation==="foreign_key")w.arbitrary=true;
 if(mutation==="raw_outside_evidence")w.command_max_raw=w.evidence_max_raw+1;
 assert.equal(f.transport.supportedWorkspace(spec),null);
});

async function liveFixture() {
  const snapshot={service_incarnation:"owner",live_elbow_recipes:[{recipe:"physical-live-left-elbow",joint:"arm_left_elbow_flex.pos",side:"left",label:"Left elbow",profile:"LiveLeftElbow",rate_norm_per_s:1}],controller:{device_id:"enrolled",controller_generation:"claimed"},
    run:{run_id:"live-run",recipe:"physical-live-left-elbow",source:"protected-physical-provider",status:"running",
      backend_incarnation:"motor",intent_revision:7,deadline:90,live_input:{joint:"arm_left_elbow_flex.pos",side:"left",label:"Left elbow",profile:"LiveLeftElbow",rate_norm_per_s:1,
        reference_norm:5,corridor_min_norm:-5,corridor_max_norm:5,cursor_norm:4,cursor_feedback_sequence:30,cursor_intent_revision:7}}};
  const f=fixture(async()=>({ok:true,json:async()=>({ticket:"current",snapshot})}));
  await f.transport.attach();
  Object.assign(f.transport,{connected:true,control:true,device:"enrolled",controllerGeneration:"claimed",connection:"wire"});
  const frames=[];f.transport.socket.bufferedAmount=0;f.transport.socket.send=value=>frames.push(JSON.parse(value));
  const reply=(op,value)=>{
    const frame=frames.findLast(frame=>frame.command?.op===op && f.transport.pending.has(frame.id));assert(frame,`${op} is pending`);
    f.transport.socket.onmessage({data:JSON.stringify({kind:"result",id:frame.id,...value})});
  };
  return {...f,frames,reply};
}

async function explicitLiveResume(f) {
 const t=f.transport,initial=t.snapshot.run.intent_revision,task=t.resume(),s=JSON.parse(JSON.stringify(t.snapshot));
 s.run.status="paused";s.run.intent_revision=initial+1;Object.assign(s.run.live_input,{cursor_intent_revision:initial+1});
 s.evidence={feedback:true,required_observation:true,hold_acknowledged:true,pose_aligned:true,native_ack:true,acknowledged_intent_revision:initial+1};
 t.socket.onmessage({data:JSON.stringify({kind:"snapshot",snapshot:s})});f.reply("connect",{accepted:true,connection_generation:"explicit-wire",snapshot:s});
 for(let i=0;i<6;i++)await Promise.resolve();f.reply("release_input",{accepted:true});for(let i=0;i<6;i++)await Promise.resolve();
 s.run.status="running";s.run.intent_revision=initial+2;s.run.live_input.cursor_intent_revision=initial+2;s.evidence.acknowledged_intent_revision=initial+2;
 t.socket.onmessage({data:JSON.stringify({kind:"snapshot",snapshot:s})});f.reply("resume",{accepted:true});await task;
}

test("practical stream uses actual mapped admission and fresh gesture cursors within one original deadline",async()=>{
 const f=await liveFixture(),t=f.transport,spec=sharedLiveRecipes().find(value=>value.recipe==="physical-live-right-elbow-practical");
 assert(spec,"actual shared practical spec required");t.snapshot.live_elbow_recipes=sharedLiveRecipes();
 Object.assign(t.snapshot.run,{recipe:spec.recipe,deadline:990,native_live_at:810});
 const low=(1847-827)/2212*200-100,high=(2074-827)/2212*200-100;
 Object.assign(t.snapshot.run.live_input,{joint:spec.joint,side:spec.side,label:spec.label,profile:spec.profile,rate_norm_per_s:spec.rate_norm_per_s,
  reference_norm:low,corridor_min_norm:low,corridor_max_norm:high,cursor_norm:low,cursor_feedback_sequence:30,cursor_intent_revision:7,
  admission_version:2,workspace:{version:1,id:"am1-right-elbow-practical-v1",context_sha256:"a".repeat(64),evidence_min_raw:1847,evidence_max_raw:2074}});
 assert.equal(t.setElbowJog(1),true);f.setNow(1100);const first=t.input();
 f.reply("grant",{accepted:true,grant_id:"first",issued:810,expires:810.25});await first;
 const target=f.frames.at(-1).command;assert.deepEqual(Object.keys(target.target),[spec.joint]);
 assert(Math.abs(target.target[spec.joint]-(low+.1*spec.rate_norm_per_s))<1e-10);
 assert.equal(target.valid_until_monotonic_s,undefined,"browser cannot renew native250ms expiry");
 f.reply("input",{accepted:true,status:"input"});await Promise.resolve();await Promise.resolve();
 const release=t.releaseInput();f.reply("release_input",{accepted:true});await release;
 Object.assign(t.snapshot.run.live_input,{cursor_norm:low+1,cursor_feedback_sequence:40,cursor_intent_revision:8});t.snapshot.run.intent_revision=8;
 await explicitLiveResume(f);assert.equal(t.setElbowJog(-1),true);f.setNow(1200);const next=t.input();
 f.reply("grant",{accepted:true,grant_id:"second",issued:820,expires:820.25});await next;
 assert(Math.abs(f.frames.at(-1).command.target[spec.joint]-(low+1-.1*spec.rate_norm_per_s))<1e-10,"new gesture starts at fresh measured cursor");
 assert.equal(t.snapshot.run.live_input.reference_norm,low);assert.equal(t.snapshot.run.live_input.corridor_max_norm,high);
 assert.equal(t.snapshot.run.deadline,990);assert.equal(t.snapshot.run.native_live_at,810);
 f.reply("input",{accepted:true,status:"input"});t.clear();
});

test("live elbow samples the current gesture after its grant and sends only the mapped selected key",async()=>{
  const f=await liveFixture();
  assert.equal(typeof f.transport.setElbowJog,"function","live mapped input gesture must exist");
  assert.equal(f.transport.setElbowJog(-1),true);
  const tick=f.transport.input();assert.equal(f.frames.at(-1).command.op,"grant");
  f.transport.setElbowJog(1);f.setNow(1100);
  f.reply("grant",{accepted:true,grant_id:"pi-grant",issued:4000,expires:4000.25});await tick;
  const input=f.frames.at(-1).command;
  assert.equal(input.op,"input");assert.deepEqual(input.target,{"arm_left_elbow_flex.pos":4.1});
  assert.equal(input.grant_id,"pi-grant");assert.equal(input.host_incarnation,"motor");assert.equal(input.intent_revision,7);
  assert.equal(input.valid_until_monotonic_s,undefined,"client must not invent or restamp Pi expiry");
  assert.equal(input.seq,1);f.reply("input",{accepted:true,status:"input"});
});

test("live elbow release fences a pending grant and requires a fresh qualified revision",async()=>{
  const f=await liveFixture();assert.equal(typeof f.transport.setElbowJog,"function");f.transport.setElbowJog(-1);
  const tick=f.transport.input(),released=f.transport.releaseInput();
  assert.equal(f.transport.setElbowJog(1),false,"old running snapshot cannot authorize a new gesture after release");
  f.reply("grant",{accepted:true,grant_id:"old",issued:10,expires:10.25});await tick;
  assert.equal(f.frames.filter(frame=>frame.command?.op==="input").length,0);
  f.reply("release_input",{accepted:true});await released;
  f.transport.snapshot.run.intent_revision=8;
  Object.assign(f.transport.snapshot.run.live_input,{cursor_norm:2,cursor_feedback_sequence:40,cursor_intent_revision:8});
  await explicitLiveResume(f);assert.equal(f.transport.setElbowJog(1),true);f.setNow(1200);
  const fresh=f.transport.input();f.reply("grant",{accepted:true,grant_id:"fresh",issued:20,expires:20.25});await fresh;
  assert.deepEqual(f.frames.at(-1).command.target,{"arm_left_elbow_flex.pos":2.1});
  f.reply("input",{accepted:true,status:"input"});assert.equal(f.transport.snapshot.run.deadline,90);
});

test("live elbow drops buffered and excess ticks without accumulating displacement or input backlog",async()=>{
  const f=await liveFixture();assert.equal(typeof f.transport.setElbowJog,"function");f.transport.setElbowJog(-1);
  f.transport.socket.bufferedAmount=1;await f.transport.input();assert.equal(f.frames.length,0);
  f.transport.socket.bufferedAmount=0;f.setNow(5000);
  const tick=f.transport.input();f.reply("grant",{accepted:true,grant_id:"g0",issued:10,expires:10.25});await tick;
  for(let index=0;index<20;index++)f.transport.input();
  assert.equal(f.frames.length,2,"one unresolved physical input must prevent a newer grant");
  assert.equal(f.frames[1].command.target["arm_left_elbow_flex.pos"],3.9,"callback gaps cannot catch up displacement");
  f.reply("input",{accepted:false,status:"superseded"});await Promise.resolve();await Promise.resolve();await Promise.resolve();
  assert.equal(f.transport.elbowJog,-1,"superseded input is not loss of the current gesture");
  f.transport.clear();
});
test("a pending physical input prevents grant rotation until its ACK settles bounded work",async()=>{
  const f=await liveFixture();try{
    f.transport.setElbowJog(-1);f.setNow(1050);
    const first=f.transport.input();f.reply("grant",{accepted:true,grant_id:"first",issued:100,expires:100.25});await first;
    assert.equal(f.transport.liveInputs.size,1);assert.equal(f.transport.pending.size,1);
    const deferred=f.transport.input();assert.equal(f.frames.length,2,"grantB must not overtake still pending inputA at Core");await deferred;
    f.reply("input",{accepted:true,status:"input"});await Promise.resolve();await Promise.resolve();await Promise.resolve();
    assert.equal(f.transport.liveInputs.size,0);assert.equal(f.transport.pending.size,0);assert.equal(f.transport.elbowJog,-1);
    f.setNow(1100);const next=f.transport.input();assert.equal(f.frames.at(-1).command.op,"grant");
    f.reply("grant",{accepted:true,grant_id:"second",issued:200,expires:200.25});await next;
    const input=f.frames.at(-1).command;assert.equal(input.grant_id,"second");assert.equal(input.seq,2);
    assert.deepEqual(Object.keys(input.target),["arm_left_elbow_flex.pos"]);assert(Math.abs(input.target["arm_left_elbow_flex.pos"]-3.9)<1e-12);assert.equal(input.valid_until_monotonic_s,undefined);
    f.reply("input",{accepted:true,status:"input"});
  }finally{f.transport.clear();}
});
const reporting={accepted:true,status:"reporting_error",code:"response_limit",reason:"Owner response exceeds the presentation limit",effect_admitted:true,
  run_id:"actual-new-run",operation_result:{accepted:true,status:"starting",effect_admitted:true,run_id:"actual-new-run"}};
const enrolled={device_id:"enrolled",csrf:"existing-csrf",control:true,optional_p1_video:false};
const attached={ticket:"one-use-ticket",revision:1,snapshot:{service_incarnation:"same-owner",controller:null,run:{run_id:"retained-run",status:"completed"}}};
for(const failure of [429,503,"network"]) test(`initial status ${failure} preserves enrollment and retries only view attachment`,async()=>{
  const calls=[];let sessionCalls=0;
  const f=fixture(async(url,opts)=>{calls.push([url,opts?.method || "GET"]);
    if(url==="/api/session") {sessionCalls++;if(sessionCalls===1){if(failure==="network")throw Error("network unavailable");return {ok:false,status:failure};}return {ok:true,status:200,json:async()=>enrolled};}
    assert.equal(url,"/api/attach");return {ok:true,status:200,json:async()=>attached};});
  Object.assign(f.transport,{device:"enrolled",csrf:"existing-csrf",control:true});
  await f.transport.initialize();
  assert.equal(f.transport.connected,false);
  assert.equal(f.transport.device,"enrolled");assert.equal(f.transport.csrf,"existing-csrf");assert.equal(f.transport.control,true);
  assert(!f.renders.some(value=>/pairing|enroll/i.test(value || "")),"transient status must not request enrollment");
  assert.match(f.renders.at(-1),/unavailable|reconnect/i);
  assert.equal(f.timers.filter(Boolean).length,1);
  await f.retry();
  f.transport.socket.onmessage({data:JSON.stringify({kind:"snapshot",revision:1,snapshot:attached.snapshot})});
  assert.equal(f.transport.connected,true);assert.equal(f.transport.device,"enrolled");assert.equal(f.transport.snapshot.run.run_id,"retained-run");
  assert.deepEqual(calls,[["/api/session","GET"],["/api/session","GET"],["/api/attach","POST"]]);
  assert.equal(f.timers.filter(Boolean).length,0,"successful attachment cancels reconnect");
});
test("actual initialization 401 alone requests enrollment without a retry",async()=>{
  const calls=[],f=fixture(async url=>{calls.push(url);return {ok:false,status:401};});
  Object.assign(f.transport,{device:"revoked-device",csrf:"revoked-csrf",control:true,optionalVideo:true});
  await f.transport.initialize();
  assert.equal(f.transport.device,null);assert.equal(f.transport.csrf,null);assert.equal(f.transport.control,false);assert.equal(f.transport.optionalVideo,false);
  assert.equal(f.transport.connected,false);assert.match(f.renders.at(-1),/pairing/i);assert.deepEqual(calls,["/api/session"]);assert.equal(f.timers.filter(Boolean).length,0);
});
for(const status of [200,503]) test(`attach reporting failure HTTP ${status} retries authenticated initialization without duplicate timers or control effects`,async()=>{
  const calls=[];let attachCalls=0;
  const f=fixture(async(url,opts)=>{calls.push(url);
    if(url==="/api/session")return {ok:true,status:200,json:async()=>enrolled};
    assert.equal(url,"/api/attach");attachCalls++;return {ok:attachCalls>1 || status===200,status:attachCalls>1 ? 200 : status,json:async()=>attachCalls>1 ? attached : {...reporting,accepted:false,effect_admitted:false}};});
  await f.transport.initialize();
  assert.equal(f.transport.connected,false);assert.equal(f.timers.filter(Boolean).length,1);
  assert(!f.renders.some(value=>/pairing|enroll/i.test(value || "")));
  await f.transport.initialize();
  f.transport.socket.onmessage({data:JSON.stringify({kind:"snapshot",revision:1,snapshot:attached.snapshot})});
  assert.equal(f.transport.connected,true);assert.equal(f.timers.filter(Boolean).length,0);
  assert.deepEqual(calls,["/api/session","/api/attach","/api/session","/api/attach"]);
});
test("repeated view failures keep one retry and page detach cancels all further requests",async()=>{
  const calls=[],f=fixture(async url=>{calls.push(url);return {ok:false,status:503};});
  await Promise.all([f.transport.initialize(),f.transport.initialize()]);
  assert.equal(f.timers.filter(Boolean).length,1);
  f.transport.detach();assert.equal(f.timers.filter(Boolean).length,0);
  await f.transport.initialize();await f.transport.attach();
  assert.deepEqual(calls,["/api/session","/api/session"]);
});
test("persisted page restoration reattaches fresh state with empty controller intent",async()=>{
  const calls=[],f=fixture(async url=>{calls.push(url);return {ok:true,status:200,json:async()=>url==="/api/session" ? enrolled : attached};});
  f.transport.held.add("w");f.transport.detach();
  await f.transport.restore();
  f.transport.socket.onmessage({data:JSON.stringify({kind:"snapshot",revision:1,snapshot:attached.snapshot})});
  assert.equal(f.transport.connected,true);assert.equal(f.transport.controllerGeneration,null);assert.equal(f.transport.connection,null);assert.equal(f.transport.held.size,0);
  assert.deepEqual(calls,["/api/session","/api/attach"]);
});
test("late attach reporting failure cannot disconnect a newer successful initialization",async()=>{
  let attachCalls=0,oldReply,started;
  const waiting=new Promise(resolve=>started=resolve),calls=[];
  const f=fixture(async url=>{calls.push(url);if(url==="/api/session")return {ok:true,status:200,json:async()=>enrolled};
    assert.equal(url,"/api/attach");if(++attachCalls===1)return new Promise(resolve=>{oldReply=resolve;started();});
    return {ok:true,status:200,json:async()=>attached};});
  const first=f.transport.initialize();await waiting;
  await f.transport.initialize();f.transport.socket.onmessage({data:JSON.stringify({kind:"snapshot",revision:1,snapshot:attached.snapshot})});
  oldReply({ok:false,status:503,json:async()=>reporting});await first;
  assert.equal(f.transport.connected,true);assert.equal(f.transport.reportingError,null);assert.equal(f.timers.filter(Boolean).length,0);
  assert.equal(f.transport.snapshot.run.run_id,"retained-run");assert.deepEqual(calls,["/api/session","/api/attach","/api/session","/api/attach"]);
});
test("known applied Start reporting failure never retries and promptly fences controls",async()=>{
  const requests=[],f=fixture(async(url,opts)=>{requests.push([url,JSON.parse(opts.body)]);return {ok:true,json:async()=>reporting};});
  const result=await f.transport.start("physical-left-elbow-positive-control");
  assert.equal(requests.length,1);assert.equal(result.accepted,true);assert.equal(result.effect_admitted,true);
  assert.equal(f.transport.reportingError.code,"response_limit");assert.equal(f.transport.reportingError.run_id,"actual-new-run");
  assert.equal(f.transport.connected,false);assert.equal(f.transport.controllerGeneration,null);
  assert.equal(f.transport.snapshot.run.status,"finishing","old snapshot is retained only as forensic cache");
});
test("authenticated details hydrate the exact hash-bound native field without a control request",async()=>{
  const native={run_id:"actual",operation_id:"operation",status:"completed",execution:{rows:"x".repeat(20000)}},bytes=Buffer.from(JSON.stringify(native));
  const reference={kind:"am1-status-field-reference",version:1,service_incarnation:"native-service",run_id:"actual",operation_id:"operation",
    host_incarnation:"native-host",field:"run",sha256:require("node:crypto").createHash("sha256").update(bytes).digest("hex"),bytes:bytes.length};
  const calls=[],f=fixture(async url=>{calls.push(url);const parsed=new URL(url,"https://test.invalid"),offset=Number(parsed.searchParams.get("offset")),limit=Number(parsed.searchParams.get("limit")),chunk=bytes.subarray(offset,offset+limit);
    return {ok:true,json:async()=>({accepted:true,status:"detail",reference:Object.fromEntries(Object.entries(reference).reverse()),offset,length:chunk.length,total_bytes:bytes.length,encoding:"base64",data:chunk.toString("base64")})};});
  f.transport.snapshot={run:{run_id:"actual",operation_id:"operation",details_reference:reference}};
  const full=await f.transport.readDetail(reference);
  assert.equal(JSON.stringify(full),JSON.stringify(native));assert.equal(calls.length,3);
  assert(calls.every(url=>url.startsWith("/api/state?")));
  assert.equal(f.transport.snapshot.run.status,undefined);assert.equal(f.transport.connected,true);
});
test("corrupt or unavailable detail is a view failure and never Stop or another Start",async()=>{
  const reference={kind:"am1-status-field-reference",version:1,service_incarnation:"native-service",run_id:"old-run",operation_id:"operation",
    host_incarnation:"host",field:"run",sha256:"a".repeat(64),bytes:2};
  const calls=[],f=fixture(async url=>{calls.push(url);return {ok:true,json:async()=>({accepted:true,status:"detail",reference,offset:0,length:2,total_bytes:2,encoding:"base64",data:Buffer.from("{}").toString("base64")})};});
  await assert.rejects(()=>f.transport.readDetail(reference),/hash/i);
  assert.equal(f.transport.connected,true);assert.equal(f.transport.snapshot.run.status,"finishing");
  await assert.rejects(()=>f.transport.readDetail({...reference,bytes:8*1024*1024+1}),/reference/i);
  await assert.rejects(()=>f.transport.readDetail({...reference,run_id:"foreign"}),/reference/i);
  assert.equal(calls.length,1);
});
test("known non-2xx reporting failure is not repeated as Start",async()=>{
  let count=0;const f=fixture(async()=>{count++;return {ok:false,json:async()=>reporting};});
  await assert.rejects(()=>f.transport.start("physical-left-elbow-positive-control"),/presentation limit/);
  assert.equal(count,1);assert.equal(f.transport.connected,false);
});
test("unclassified outcome loss still retries the same immutable Start identity",async()=>{
  const requests=[],f=fixture(async(url,opts)=>{requests.push(JSON.parse(opts.body));if(requests.length===1)throw Error("lost response");return {ok:true,json:async()=>({accepted:true,status:"starting"})};});
  await f.transport.start("fake-arm-smoke-repeat");
  assert.equal(requests.length,2);assert.equal(requests[0].operation_id,requests[1].operation_id);
});
test("confirmation UUID has explicit positive-only transport signature",async()=>{
  const requests=[],f=fixture(async(url,opts)=>{requests.push(JSON.parse(opts.body));return {ok:true,json:async()=>({accepted:true})};});
  const id="df51bc69-3f68-42bf-9c73-35fd4be520f6";
  await f.transport.start("physical-left-elbow-positive-control",id);
  assert.equal(requests[0].confirmation_of,id);
  await assert.rejects(()=>f.transport.start("fake-arm-smoke-repeat",id),/confirmation/);
  assert.equal(requests.length,1);
});
test("WSS reporting failure clears freshness and preserves concrete diagnosis",async()=>{
  const f=fixture(async()=>({ok:true,json:async()=>({ticket:"one",snapshot:{run:{run_id:"old-run",status:"finishing"}}})}));
  await f.transport.attach();
  f.transport.connected=true;
  f.transport.socket.onmessage({data:JSON.stringify({...reporting,kind:"unavailable"})});
  assert.equal(f.transport.connected,false);assert.equal(f.transport.reportingError.code,"response_limit");
  assert.match(f.renders.at(-1),/presentation limit/);
});
test("completed diagnostic labels measured coarse response separately from program and precision",()=>{
  const f=fixture(()=>{}), base={source:"protected-physical-provider",recipe:"physical-left-elbow-positive-control",status:"completed",
    execution:{positive_control:{program_completed:true,coarse_functional_pass:false,fine_return_pass:true,final_return_residual_raw:0}}};
  const text=f.transport.constructor.outcomeText(base);
  assert.match(text,/Diagnostic completed/);assert.match(text,/coarse movement not achieved/);
  assert.match(text,/fine return passed/);
  base.execution.positive_control={program_completed:true,coarse_functional_pass:true,fine_return_pass:false,final_return_residual_raw:11};
  const passed=f.transport.constructor.outcomeText(base);
  assert.match(passed,/coarse movement passed/);assert.match(passed,/fine return not achieved/);assert.match(passed,/11 raw ticks/);
  assert.equal(f.transport.constructor.outcomeText({...base,status:"running"}),null);
  assert.equal(f.transport.constructor.outcomeText({...base,recipe:"physical-left-elbow-compare"}),null);
});

for(const changed of ["revision","takeover","paused","detached"])test(`live elbow ${changed} fences a delayed original grant`,async()=>{
  const f=await liveFixture();f.transport.setElbowJog(-1);const tick=f.transport.input();
  if(changed==="detached")f.transport.detach();
  else {
    const snapshot=JSON.parse(JSON.stringify(f.transport.snapshot));
    if(changed==="revision"){snapshot.run.intent_revision=8;snapshot.run.live_input.cursor_intent_revision=8;}
    if(changed==="takeover")snapshot.controller={device_id:"other",controller_generation:"other-generation"};
    if(changed==="paused")snapshot.run.status="paused";
    f.transport.socket.onmessage({data:JSON.stringify({kind:"snapshot",revision:2,snapshot})});
    const old=f.frames.findLast(frame=>frame.command?.op==="grant");
    f.transport.socket.onmessage({data:JSON.stringify({kind:"result",id:old.id,accepted:true,grant_id:"obsolete",issued:10,expires:10.25})});
  }
  await tick;assert.equal(f.frames.filter(frame=>frame.command?.op==="input").length,0);
  assert.equal(f.transport.elbowJog,0);f.transport.clear();
});

test("an old input refusal cannot clear a newly qualified measured-cursor gesture",async()=>{
  const f=await liveFixture();f.transport.setElbowJog(-1);f.setNow(1100);
  const tick=f.transport.input();f.reply("grant",{accepted:true,grant_id:"old",issued:10,expires:10.25});await tick;
  const snapshot=JSON.parse(JSON.stringify(f.transport.snapshot));snapshot.run.intent_revision=8;
  Object.assign(snapshot.run.live_input,{cursor_norm:2,cursor_feedback_sequence:40,cursor_intent_revision:8});
  f.transport.socket.onmessage({data:JSON.stringify({kind:"snapshot",revision:2,snapshot})});
  await explicitLiveResume(f);assert.equal(f.transport.setElbowJog(1),true);
  f.reply("input",{accepted:false,status:"refused",reason:"expired grant"});await Promise.resolve();await Promise.resolve();
  assert.equal(f.transport.elbowJog,1);assert.equal(f.transport.elbowCursor,2);f.transport.clear();
});


test("a prior revision's late input refusal settles its fence before the fresh gesture obtains a new grant",async()=>{
  const f=await liveFixture();f.transport.setElbowJog(-1);f.setNow(1100);
  const first=f.transport.input();f.reply("grant",{accepted:true,grant_id:"a",issued:10,expires:10.25});await first;
  const snapshot=JSON.parse(JSON.stringify(f.transport.snapshot));snapshot.run.intent_revision=8;
  Object.assign(snapshot.run.live_input,{cursor_norm:2,cursor_feedback_sequence:40,cursor_intent_revision:8});
  f.transport.socket.onmessage({data:JSON.stringify({kind:"snapshot",revision:2,snapshot})});await explicitLiveResume(f);assert.equal(f.transport.setElbowJog(1),true);
  await f.transport.input();assert.equal(f.frames.filter(frame=>frame.command?.op==="grant").length,1);
  f.reply("input",{accepted:false,status:"refused",reason:"expired grant"});await Promise.resolve();await Promise.resolve();await Promise.resolve();
  assert.equal(f.transport.elbowJog,1);assert.equal(f.transport.liveInputs.size,0);assert.equal(f.transport.pending.size,0);
  f.setNow(1150);const next=f.transport.input();f.reply("grant",{accepted:true,grant_id:"b",issued:11,expires:11.25});await next;
  assert.equal(f.frames.at(-1).command.grant_id,"b");assert.equal(f.frames.at(-1).command.intent_revision,10);
  assert.deepEqual(f.frames.at(-1).command.target,{"arm_left_elbow_flex.pos":2.05});f.reply("input",{accepted:true});
});


for(const changed of ["pause","connection"])test(`physical Resume qualification cannot cross a changed ${changed}`,async()=>{
  const f=await liveFixture(),task=f.transport.resume();
  const snapshot=JSON.parse(JSON.stringify(f.transport.snapshot));snapshot.run.status="paused";snapshot.run.intent_revision=8;
  Object.assign(snapshot.run.live_input,{cursor_norm:2,cursor_feedback_sequence:40,cursor_intent_revision:8});
  snapshot.evidence={feedback:true,required_observation:true,hold_acknowledged:false,pose_aligned:false,native_ack:false,acknowledged_intent_revision:7};
  f.transport.socket.onmessage({data:JSON.stringify({kind:"snapshot",revision:2,snapshot})});
  f.reply("connect",{accepted:true,connection_generation:"new-wire",snapshot});await Promise.resolve();await Promise.resolve();
  f.reply("release_input",{accepted:true});await Promise.resolve();await Promise.resolve();
  const timer=f.timers.find(entry=>entry?.[1]===50);assert(timer,"physical Resume waits for fresh measured qualification");
  if(changed==="pause")snapshot.run.intent_revision=9;
  else f.transport.connection="different-wire";
  Object.assign(snapshot.evidence,{hold_acknowledged:true,pose_aligned:true,native_ack:true,acknowledged_intent_revision:8});
  f.transport.socket.onmessage({data:JSON.stringify({kind:"snapshot",revision:3,snapshot})});timer[0]();await Promise.resolve();await Promise.resolve();
  if(f.frames.at(-1).command?.op==="resume")f.reply("resume",{accepted:true});
  await task;
  assert.equal(f.frames.filter(frame=>frame.command?.op==="resume").length,0,"changed authority cannot continue qualified Resume");
  f.transport.clear();
});

for(const mutation of ["wrong_joint","wrong_metadata","unsupported_recipe"])test("live elbow refuses "+mutation+" before a grant",async()=>{
  const f=await liveFixture();
  if(mutation==="wrong_joint")f.transport.snapshot.run.live_input.joint="arm_right_elbow_flex.pos";
  if(mutation==="wrong_metadata")f.transport.snapshot.live_elbow_recipes[0].joint="arm_right_elbow_flex.pos";
  if(mutation==="unsupported_recipe")f.transport.snapshot.run.recipe="physical-live-left-wrist";
  assert.equal(f.transport.setElbowJog(1),false);await f.transport.input();
  assert.equal(f.frames.filter(frame=>frame.command?.op==="grant").length,0);f.transport.clear();
});
test("live elbow recipe changes fence a pending original grant without switching its key",async()=>{
  const f=await liveFixture();f.transport.setElbowJog(-1);const tick=f.transport.input();
  f.transport.snapshot.run.recipe="physical-live-right-elbow";
  f.transport.snapshot.run.live_input.joint="arm_right_elbow_flex.pos";
  f.transport.snapshot.run.live_input.side="right";f.transport.snapshot.run.live_input.label="Right elbow";f.transport.snapshot.run.live_input.profile="LiveRightElbow";
  f.transport.snapshot.live_elbow_recipes=[{recipe:"physical-live-right-elbow",joint:"arm_right_elbow_flex.pos",side:"right",label:"Right elbow",profile:"LiveRightElbow",rate_norm_per_s:1}];
  f.reply("grant",{accepted:true,grant_id:"old-selection",issued:10,expires:10.25});await tick;
  assert.equal(f.frames.filter(frame=>frame.command?.op==="input").length,0);f.transport.clear();
});

test("live recipe metadata cannot introduce an inherited nonallowlisted name",async()=>{
  const f=await liveFixture();f.transport.snapshot.live_elbow_recipes=[{recipe:"toString",joint:"arbitrary",side:"other",label:"Other",profile:"Other",rate_norm_per_s:1}];
  assert.equal(f.transport.liveElbowSpec("toString"),null);f.transport.clear();
});

async function rearmFixture(side="left") {
 const f=await liveFixture(),t=f.transport,s=t.snapshot,recipe="physical-live-"+side+"-elbow",joint="arm_"+side+"_elbow_flex.pos",profile=side==="left"?"LiveLeftElbow":"LiveRightElbow";
 s.live_elbow_recipes=[{recipe,joint,side,label:side[0].toUpperCase()+side.slice(1)+" elbow",profile,rate_norm_per_s:1}];s.run.recipe=recipe;
 Object.assign(s.run.live_input,{joint,side,label:side[0].toUpperCase()+side.slice(1)+" elbow",profile,corridor_min_norm:side==="right"?2:-5});
 const deliver=()=>t.socket.onmessage({data:JSON.stringify({kind:"snapshot",snapshot:JSON.parse(JSON.stringify(s))})});
 const hold=(revision,rearm=true)=>{s.run.status="paused";s.run.intent_revision=revision;Object.assign(s.run.live_input,{cursor_intent_revision:revision,cursor_norm:3,cursor_feedback_sequence:40,rearm:{eligible:rearm,released_intent_revision:8}});s.evidence={feedback:true,required_observation:true,hold_acknowledged:true,pose_aligned:true,native_ack:true,acknowledged_intent_revision:revision};deliver();};
 const flush=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
 const release=async()=>{assert.equal(t.pressElbowJog(-1,"key:["),true);const p=t.releaseElbowJog("key:[",true);hold(8);f.reply("release_input",{accepted:true,status:"released"});await p;};
 const qualify=async()=>{hold(9);f.reply("connect",{accepted:true,connection_generation:"fresh-wire",snapshot:JSON.parse(JSON.stringify(s))});await flush();f.reply("release_input",{accepted:true});await flush();
  s.run.status="running";s.run.intent_revision=10;Object.assign(s.run.live_input,{cursor_intent_revision:10,cursor_norm:3,rearm:{eligible:false,released_intent_revision:null}});s.evidence.acknowledged_intent_revision=10;deliver();f.reply("resume",{accepted:true});await flush();};
 return {...f,side,joint,deliver,hold,flush,release,qualify};
}
async function pendingExplicitResume(side="left") {
 const f=await rearmFixture(side),t=f.transport;t.snapshot.run.status="paused";t.snapshot.run.intent_revision=0;t.snapshot.run.live_input.cursor_intent_revision=0;
 const task=t.resume(),s=JSON.parse(JSON.stringify(t.snapshot));s.run.status="paused";s.run.intent_revision=1;
 Object.assign(s.run.live_input,{cursor_norm:3,cursor_intent_revision:1,cursor_feedback_sequence:40});
 s.evidence={feedback:true,required_observation:true,hold_acknowledged:true,pose_aligned:true,native_ack:true,acknowledged_intent_revision:1};
 const deliver=()=>t.socket.onmessage({data:JSON.stringify({kind:"snapshot",snapshot:JSON.parse(JSON.stringify(s))})});
 deliver();f.reply("connect",{accepted:true,connection_generation:"explicit-wire",snapshot:JSON.parse(JSON.stringify(s))});await f.flush();
 f.reply("release_input",{accepted:true});await f.flush();
 const advance=async()=>{for(const entry of f.timers.filter(entry=>entry?.[1]===50))entry[0]();await f.flush();};
 const active=()=>{s.run.status="running";s.run.intent_revision=2;s.run.live_input.cursor_intent_revision=2;s.evidence.native_ack=true;s.evidence.acknowledged_intent_revision=2;deliver();};
 return {...f,task,s,deliver,advance,active};
}
for(const side of ["left","right"])for(const order of ["result-first","resuming-first"])test("explicit "+side+" Resume retains qualification through "+order+" and requires a new press after current native ACK",async()=>{
 const f=await pendingExplicitResume(side),t=f.transport;try{
  if(order==="result-first"){f.reply("resume",{accepted:true,status:"resuming"});await f.flush();}
  f.s.run.status="resuming";f.s.run.intent_revision=2;f.s.evidence.native_ack=false;f.deliver();
  if(order==="resuming-first"){f.reply("resume",{accepted:true,status:"resuming"});await f.flush();}
  assert(t.liveResume,"own qualification token must survive the accepted result until its current running ACK");
  assert.equal(t.elbowStrict,true);assert.equal(t.pressElbowJog(1,"key:]"),false);assert.equal(t.elbowCursor,null);
  f.s.run.status="running";f.deliver();await f.advance();assert(t.liveResume);assert.equal(t.elbowJog,0);assert.equal(t.pressElbowJog(1,"key:]"),false);
  f.s.evidence.native_ack=true;f.s.evidence.acknowledged_intent_revision=2;f.deliver();await f.advance();
  assert(t.liveResume,"current ACK alone cannot substitute for its measured cursor revision");
  f.s.run.live_input.cursor_intent_revision=2;f.deliver();await f.advance();await f.task;
  assert.equal(t.liveResume,null);assert.equal(t.elbowStrict,false);assert.equal(t.elbowJog,0);assert.equal(t.elbowGesture,null);
  assert(t.pressElbowJog(1,"key:new"));f.setNow(1100);const tick=t.input();f.reply("grant",{accepted:true,grant_id:"qualified",issued:5,expires:5.25});await tick;
  assert.deepEqual(Object.keys(f.frames.at(-1).command.target),[f.joint]);assert.equal(f.frames.at(-1).command.intent_revision,2);f.reply("input",{accepted:true});
 }finally{t.clear();}
});
for(const loss of ["pause","release","owner","connection","bounds","fault","deadline","new-resume"])test("accepted explicit Resume cannot reopen after "+loss+" before its delayed active snapshot",async()=>{
 const f=await pendingExplicitResume(),t=f.transport;try{
  f.reply("resume",{accepted:true,status:"resuming"});await f.flush();
  if(loss==="release")t.releaseInput();
  if(loss==="new-resume")t.resume().catch(()=>{});
  if(loss==="connection")t.connection="foreign-wire";
  if(loss==="deadline")f.setNow(4000);
  const changed=JSON.parse(JSON.stringify(f.s));
  if(loss==="pause"){changed.run.intent_revision=3;changed.run.live_input.cursor_intent_revision=3;}
  if(loss==="owner")changed.controller={device_id:"other",controller_generation:"other"};
  if(loss==="bounds")changed.run.live_input.corridor_min_norm--;
  if(loss==="fault")changed.evidence.fault="retained first cause";
  t.socket.onmessage({data:JSON.stringify({kind:"snapshot",snapshot:changed})});
  // Deliver the old own active view immediately, before a waiting continuation can run.
  f.active();await f.advance();await f.task;
  assert.equal(t.elbowStrict,true);assert.equal(t.elbowJog,0);assert.equal(t.elbowCursor,null);assert.equal(t.pressElbowJog(1,"key:new"),false);
  assert.equal(f.frames.filter(row=>["grant","input"].includes(row.command.op)).length,0);
 }finally{t.clear();}
});
test("an unchanged initial held snapshot while explicit connect is pending does not cancel its own qualification",async()=>{
 const f=await rearmFixture(),t=f.transport;try{t.snapshot.run.status="paused";const initial=JSON.parse(JSON.stringify(t.snapshot)),task=t.resume();
  t.socket.onmessage({data:JSON.stringify({kind:"snapshot",snapshot:initial})});assert(t.liveResume);
  f.hold(8,false);f.reply("connect",{accepted:true,connection_generation:"explicit-wire",snapshot:JSON.parse(JSON.stringify(t.snapshot))});await f.flush();f.reply("release_input",{accepted:true});await f.flush();
  const active=JSON.parse(JSON.stringify(t.snapshot));active.run.status="running";active.run.intent_revision=9;active.run.live_input.cursor_intent_revision=9;active.evidence.acknowledged_intent_revision=9;
  t.socket.onmessage({data:JSON.stringify({kind:"snapshot",snapshot:active})});f.reply("resume",{accepted:true});await task;assert.equal(t.elbowStrict,false);
 }finally{t.clear();}
});

for(const side of ["left","right"])test("ordinary "+side+" release and new press rearm the same run without a separate Resume",async()=>{
 const f=await rearmFixture(side),t=f.transport;try{await f.release();assert.equal(f.frames[0].command.release_kind,"voluntary_jog");
  assert.equal(t.pressElbowJog(1,"key:]"),true);await f.flush();assert.equal(t.elbowJog,0);assert.equal(t.elbowCursor,null);
  assert.equal(f.frames.at(-1).command.op,"connect");assert.equal(f.frames.at(-1).command.rearm_of_intent_revision,8);
  await f.qualify();assert.equal(t.elbowJog,1);assert.equal(t.elbowCursor,3);
  for(const row of f.frames.filter(row=>["connect","resume"].includes(row.command.op)||row.command.op==="release_input"&&row.command.release_kind===undefined))assert.equal(row.command.rearm_of_intent_revision,8);
  f.setNow(1100);const tick=t.input();f.reply("grant",{accepted:true,grant_id:"new",issued:500,expires:500.25});await tick;
  assert.deepEqual(Object.keys(f.frames.at(-1).command.target),[f.joint]);assert.equal(t.snapshot.run.deadline,90);f.reply("input",{accepted:true});
 }finally{t.clear();}
});
for(const side of ["left","right"])for(const order of ["snapshot-before-result","result-before-snapshot"])test("early ordinary "+side+" re-jog keeps only its held pending gesture with "+order,async()=>{
 const f=await rearmFixture(side),t=f.transport;try{
  assert(t.pressElbowJog(-1,"key:["));const release=t.releaseElbowJog("key:[",true);
  assert(t.pressElbowJog(1,"key:]"));const gesture=t.elbowGesture;
  assert.equal(t.elbowJog,0);assert.equal(t.elbowCursor,null);assert.equal(f.frames.filter(row=>row.command.op==="connect").length,0);
  if(order==="snapshot-before-result")f.hold(8);
  f.reply("release_input",{accepted:true,status:"released"});await release;await f.flush();
  if(order==="result-before-snapshot")f.hold(8);
  for(const entry of f.timers.filter(entry=>entry?.[1]===50))entry[0]();await f.flush();
  assert.equal(t.elbowGesture,gesture,"own release snapshot must preserve the exact newly held pending press");
  assert.equal(t.elbowJog,0);assert.equal(t.elbowCursor,null);assert.equal(f.frames.filter(row=>row.command.op==="connect").length,1);
  assert.equal(f.frames.at(-1).command.rearm_of_intent_revision,8);
  await f.qualify();assert.equal(t.elbowGesture,gesture);assert.equal(t.elbowJog,1);assert.equal(t.elbowCursor,3);
  assert.equal(f.frames.filter(row=>row.command.op==="resume").length,1);
 }finally{t.clear();}
});
for(const loss of ["cancel","pause","owner","connection","bounds","timeout"])test("early ordinary re-jog cannot survive "+loss+" before its release receipt",async()=>{
 const f=await rearmFixture(),t=f.transport;try{
  t.pressElbowJog(-1,"key:[");const release=t.releaseElbowJog("key:[",true);t.pressElbowJog(1,"key:]");
  if(loss==="cancel")t.releaseElbowJog("key:]",false);
  if(loss==="timeout"){f.setNow(4000);f.timers.find(entry=>entry?.[1]===3000)[0]();}
  const held=JSON.parse(JSON.stringify(t.snapshot));held.run.status="paused";held.run.intent_revision=8;
  Object.assign(held.run.live_input,{cursor_intent_revision:8,rearm:{eligible:true,released_intent_revision:8}});
  if(loss==="pause")held.run.live_input.rearm={eligible:false,released_intent_revision:null};
  if(loss==="owner")held.controller={device_id:"other",controller_generation:"other"};
  if(loss==="connection")t.connection="foreign-wire";
  if(loss==="bounds")held.run.live_input.corridor_min_norm--;
  t.socket.onmessage({data:JSON.stringify({kind:"snapshot",snapshot:held})});
  // Cancellation may have a second strict release; ownership loss may settle all pending commands.
  if(t.pending.size)for(const row of [...f.frames])if(row.command.op==="release_input"&&t.pending.has(row.id))t.socket.onmessage({data:JSON.stringify({kind:"result",id:row.id,accepted:true})});
  await release;for(const entry of f.timers.filter(entry=>entry?.[1]===50))entry[0]();await f.flush();
  assert.equal(t.elbowGesture,null);assert.equal(t.elbowJog,0);assert.equal(t.elbowCursor,null);
  assert.equal(f.frames.filter(row=>["connect","resume","input"].includes(row.command.op)).length,0);
 }finally{t.clear();}
});
test("ordinary rearm release during a pending connect cannot revive the gesture from late accepted replies",async()=>{
 const f=await rearmFixture(),t=f.transport;try{await f.release();t.pressElbowJog(1,"pointer:2");await f.flush();
  const cancelled=t.releaseElbowJog("pointer:2",false);f.reply("release_input",{accepted:true});await cancelled;
  f.hold(9);f.reply("connect",{accepted:true,connection_generation:"late-wire",snapshot:JSON.parse(JSON.stringify(t.snapshot))});await f.flush();
  assert.equal(t.elbowJog,0);assert.equal(t.pressElbowJog(1,"key:]"),false,"strict cancellation requires explicit Resume even if a later revision arrives");
  assert.equal(f.frames.filter(row=>row.command.op==="resume").length,0);
 }finally{t.clear();}
});
test("ordinary pointerup followed by lost capture emits one voluntary release and preserves its rearm receipt",async()=>{
 const f=await rearmFixture(),t=f.transport;try{t.pressElbowJog(-1,"pointer:1");const p=t.releaseElbowJog("pointer:1",true);
  t.releaseElbowJog("pointer:1",false);assert.equal(f.frames.filter(row=>row.command.op==="release_input").length,1);
  f.hold(8);f.reply("release_input",{accepted:true});await p;assert.equal(t.pressElbowJog(1,"pointer:2"),true);
 }finally{t.clear();}
});

for(const stage of ["connect","release","qualify","resume"])test("ordinary rearm cancellation at "+stage+" keeps a strict latch across late accepted snapshots",async()=>{
 const f=await rearmFixture(),t=f.transport;try{await f.release();t.pressElbowJog(1,"key:]");await f.flush();
  if(stage!=="connect"){
   f.hold(9);if(stage==="qualify")Object.assign(t.snapshot.evidence,{hold_acknowledged:false,pose_aligned:false,native_ack:false});
   f.reply("connect",{accepted:true,connection_generation:"fresh-wire",snapshot:JSON.parse(JSON.stringify(t.snapshot))});await f.flush();
   if(stage!=="release"){f.reply("release_input",{accepted:true});await f.flush();}
  }
  const cancel=t.releaseElbowJog("key:]",false);f.reply("release_input",{accepted:true});await cancel;
  if(stage==="connect")f.reply("connect",{accepted:true,connection_generation:"late-wire",snapshot:JSON.parse(JSON.stringify(t.snapshot))});
  if(stage==="release")f.reply("release_input",{accepted:true});
  const late=JSON.parse(JSON.stringify(t.snapshot));late.run.status="running";late.run.intent_revision=10;late.run.live_input.cursor_intent_revision=10;
  Object.assign(late.evidence,{hold_acknowledged:true,pose_aligned:true,native_ack:true,acknowledged_intent_revision:10});t.socket.onmessage({data:JSON.stringify({kind:"snapshot",snapshot:late})});
  if(stage==="resume")f.reply("resume",{accepted:true});for(const entry of f.timers.filter(entry=>entry?.[1]===50))entry[0]();await f.flush();
  assert.equal(t.elbowJog,0);assert.equal(t.pressElbowJog(1,"pointer:new"),false);assert.equal(f.frames.filter(row=>row.command.op==="input").length,0);
 }finally{t.clear();}
});
for(const mutation of ["pause","owner","connection","joint","reference","fault","deadline","native_live_at","admission_version","workspace_id","workspace_context","workspace_min","workspace_max","raw_min","raw_max","origin"])test("ordinary pending rearm cancels a changed "+mutation,async()=>{
 const f=await rearmFixture(),t=f.transport;try{await f.release();t.pressElbowJog(1,"key:]");await f.flush();f.hold(9);
  Object.assign(t.snapshot.evidence,{hold_acknowledged:false,pose_aligned:false,native_ack:false});f.reply("connect",{accepted:true,connection_generation:"fresh-wire",snapshot:JSON.parse(JSON.stringify(t.snapshot))});await f.flush();f.reply("release_input",{accepted:true});await f.flush();
  const changed=JSON.parse(JSON.stringify(t.snapshot));
  if(mutation==="pause")changed.run.intent_revision++;
  if(mutation==="owner")changed.controller={device_id:"other",controller_generation:"other"};
  if(mutation==="connection")t.connection="other";
  if(mutation==="joint")changed.run.live_input.joint="arm_right_elbow_flex.pos";
  if(mutation==="reference")changed.run.live_input.reference_norm++;
  if(mutation==="fault")changed.evidence.fault="original native fault";
  if(mutation==="deadline")changed.run.deadline++;
  if(mutation==="native_live_at")changed.run.native_live_at=123;
  if(mutation==="admission_version")changed.run.live_input.admission_version=2;
  if(mutation==="workspace_id")changed.run.live_input.workspace={id:"foreign"};
  if(mutation==="workspace_context")changed.run.live_input.workspace={context_sha256:"b".repeat(64)};
  if(mutation==="workspace_min")changed.run.live_input.workspace={evidence_min_raw:1832};
  if(mutation==="workspace_max")changed.run.live_input.workspace={evidence_max_raw:1868};
  if(mutation==="raw_min")changed.run.live_input.corridor_min_raw=1834;
  if(mutation==="raw_max")changed.run.live_input.corridor_max_raw=1867;
  if(mutation==="origin")changed.run.live_input.measured_origin_raw=1845;
  Object.assign(changed.evidence,{hold_acknowledged:true,pose_aligned:true,native_ack:true,acknowledged_intent_revision:9});t.socket.onmessage({data:JSON.stringify({kind:"snapshot",snapshot:changed})});
  for(const entry of f.timers.filter(entry=>entry?.[1]===50))entry[0]();await f.flush();assert.equal(t.elbowJog,0);assert.equal(f.frames.filter(row=>row.command.op==="resume").length,0);
 }finally{t.clear();}
});
test("the whole ordinary press opportunity expires at3 seconds while a connect reply is delayed",async()=>{
 const f=await rearmFixture(),t=f.transport;try{await f.release();t.pressElbowJog(1,"pointer:1");await f.flush();f.setNow(4000);
  const timeout=f.timers.find(entry=>entry?.[1]===3000);assert(timeout);timeout[0]();f.reply("release_input",{accepted:true});await f.flush();
  f.hold(9);f.reply("connect",{accepted:true,connection_generation:"late-wire",snapshot:JSON.parse(JSON.stringify(t.snapshot))});await f.flush();
  assert.equal(t.elbowJog,0);assert.equal(t.pressElbowJog(1,"key:]"),false);assert.equal(f.frames.filter(row=>row.command.op==="resume").length,0);
 }finally{t.clear();}
});
test("repeated held presses and older pointer releases cannot replace or cancel a fresh reverse gesture",async()=>{
 const f=await rearmFixture(),t=f.transport;try{assert(t.pressElbowJog(-1,"pointer:1"));const id=t.elbowGesture.id;
  assert.equal(t.pressElbowJog(-1,"pointer:1"),false);assert.equal(t.elbowGesture.id,id);
  assert(t.pressElbowJog(1,"key:]"));await t.releaseElbowJog("pointer:1",true);assert.equal(t.elbowJog,1);assert.equal(f.frames.length,0);
 }finally{t.clear();}
});

test("ordinary rearm waits for current native ACK after an accepted Resume publishes running first",async()=>{
 const f=await rearmFixture(),t=f.transport;try{await f.release();t.pressElbowJog(1,"key:]");await f.flush();f.hold(9);
  f.reply("connect",{accepted:true,connection_generation:"fresh-wire",snapshot:JSON.parse(JSON.stringify(t.snapshot))});await f.flush();f.reply("release_input",{accepted:true});await f.flush();
  const running=JSON.parse(JSON.stringify(t.snapshot));running.run.status="running";running.run.intent_revision=10;running.run.live_input.cursor_intent_revision=10;
  running.evidence.native_ack=false;t.socket.onmessage({data:JSON.stringify({kind:"snapshot",snapshot:running})});f.reply("resume",{accepted:true});await f.flush();
  assert.equal(t.elbowJog,0);assert.equal(f.frames.filter(row=>row.command.op==="release_input").length,2,"accepted Resume must await its current native ACK within the same opportunity");
  Object.assign(running.evidence,{native_ack:true,acknowledged_intent_revision:10});t.socket.onmessage({data:JSON.stringify({kind:"snapshot",snapshot:running})});
  for(const entry of f.timers.filter(entry=>entry?.[1]===50))entry[0]();await f.flush();assert.equal(t.elbowJog,1);
 }finally{t.clear();}
});

test("an ordinary Resume ACK at the original3 second deadline cannot activate even after all earlier stages qualified",async()=>{
 const f=await rearmFixture(),t=f.transport;try{await f.release();t.pressElbowJog(1,"key:]");await f.flush();f.hold(9);
  f.reply("connect",{accepted:true,connection_generation:"fresh-wire",snapshot:JSON.parse(JSON.stringify(t.snapshot))});await f.flush();f.reply("release_input",{accepted:true});await f.flush();
  f.setNow(4000);const running=JSON.parse(JSON.stringify(t.snapshot));running.run.status="running";running.run.intent_revision=10;running.run.live_input.cursor_intent_revision=10;running.evidence.acknowledged_intent_revision=10;
  t.socket.onmessage({data:JSON.stringify({kind:"snapshot",snapshot:running})});f.reply("resume",{accepted:true});await f.flush();
  assert.equal(t.elbowJog,0);assert.equal(t.pressElbowJog(1,"pointer:new"),false);assert.equal(f.frames.filter(row=>row.command.op==="input").length,0);
 }finally{t.clear();}
});
test("a successfully rearmed held gesture continues ordinary granted input beyond the qualification deadline",async()=>{
 const f=await rearmFixture(),t=f.transport;try{await f.release();t.pressElbowJog(1,"key:]");await f.flush();await f.qualify();f.setNow(9000);
  const tick=t.input();f.reply("grant",{accepted:true,grant_id:"current",issued:900,expires:900.25});await tick;
  assert.equal(f.frames.at(-1).command.op,"input");assert.equal(f.frames.at(-1).command.target[f.joint],3.1);f.reply("input",{accepted:true});
 }finally{t.clear();}
});


// Claim response/snapshot orderings are synthetic; terminal metadata follows actual08's shape.
async function claimFixture(side="right") {
 const recipe="physical-live-"+side+"-elbow",snapshot={service_incarnation:"current-owner",controller:null,
  run:{run_id:"retained-terminal",operation_id:"retained-operation",recipe,source:"protected-physical-provider",status:"faulted",service_incarnation:"historical-owner",intent_revision:1,backend_incarnation:null,progress_s:0,first_cause:"retained startup failure",native_first_cause:"retained context refusal"},
  evidence:{fault:null,lifecycle:{phase:"idle"}},events:[],live_elbow_recipes:sharedLiveRecipes()};
 const requests=[],replies=[],failures=[];
 const f=fixture(async(url,opts)=>{
  requests.push({url,body:opts?.body?JSON.parse(opts.body):null,signal:opts?.signal});
  if(url==="/api/attach")return {ok:true,json:async()=>({ticket:"claim-test-ticket",snapshot:JSON.parse(JSON.stringify(snapshot))})};
  if(url==="/api/stop")return {ok:true,json:async()=>({accepted:true,status:"stopped"})};
  assert.equal(url,"/api/claim");return new Promise((resolve,reject)=>{replies.push(value=>resolve({ok:true,json:async()=>value}));failures.push(reject);});
 });
 Object.assign(f.transport,{device:"enrolled",control:true});await f.transport.attach();
 const deliver=(view=snapshot,revision)=>f.transport.socket.onmessage({data:JSON.stringify({kind:"snapshot",snapshot:JSON.parse(JSON.stringify(view)),...(revision?{revision}:{})})});
 deliver(snapshot,"current-view:1");
 const result={accepted:true,status:"claimed",controller_generation:"new-claim-generation",presence_expires:2001.5};
 const current=()=>({...JSON.parse(JSON.stringify(snapshot)),controller:{device_id:"enrolled",controller_generation:result.controller_generation,presence_expires:result.presence_expires}});
 return {...f,snapshot,requests,replies,failures,result,current,deliver,async flush(){for(let i=0;i<12;i++)await Promise.resolve();}};
}

for(const side of ["left","right"])for(const order of ["snapshot-first","result-first-old-null"])
test(`claim qualification ${side} ${order} preserves old terminal and requires current view`,async()=>{
 const f=await claimFixture(side),t=f.transport,history=JSON.stringify(f.snapshot.run);let completed=false;
 const task=t.claim().then(value=>{completed=true;return value;});await f.flush();
 if(order==="snapshot-first")f.deliver(f.current());
 f.replies[0](f.result);await f.flush();
 if(order==="result-first-old-null"){
  assert.equal(t.controllerGeneration,null,"HTTP acceptance alone must not enable authority");
  assert.equal(completed,false,"qualification waits within the original opportunity");
  f.deliver(f.snapshot);await f.flush();
  assert.equal(t.controllerGeneration,null,"queued pre-claim nil view grants no authority");
  assert(t.pendingClaim,"queued old nil cannot destroy the legitimate pending explicit claim");
  f.deliver(f.current());
 }
 const result=await task;assert.equal(result.accepted,true);assert.equal(t.controllerGeneration,f.result.controller_generation);
 assert.equal(t.snapshot.controller.controller_generation,t.controllerGeneration);assert.equal(t.pendingClaim??null,null);
 assert.equal(t.snapshot.service_incarnation,"current-owner");assert.equal(t.snapshot.run.service_incarnation,"historical-owner");
 assert.equal(JSON.stringify(t.snapshot.run),history,"current claim cannot rewrite the terminal history");
 assert.equal(f.requests.filter(x=>x.url!=="/api/attach").length,1,"no implicit Claim retry or control request");
 assert.equal(t.connection,null);assert.equal(t.elbowJog,0);assert.equal(t.elbowCursor,null);
});

for(const loss of ["clear","disconnect","pagehide","service","foreign-device","foreign-generation","new-intent","local-deadline","native-expiry"])
test(`claim qualification loss ${loss} cannot be revived by a late accepted reply`,async()=>{
 const f=await claimFixture(),t=f.transport,task=t.claim();await f.flush();
 if(loss==="clear")t.clear("explicit local loss");
 else if(loss==="disconnect")t.socket.onclose();
 else if(loss==="pagehide")t.detach();
 else if(loss==="service"){const view=f.current();view.service_incarnation="newer-owner";f.deliver(view);}
 else if(loss==="foreign-device"){const view=f.current();view.controller.device_id="other-enrolled";f.deliver(view);}
 else if(loss==="foreign-generation"){f.replies[0](f.result);await f.flush();const view=f.current();view.controller.controller_generation="newer-foreign-generation";f.deliver(view);}
 else if(loss==="new-intent"){t.intent++;f.deliver(f.snapshot);}
 else if(loss==="local-deadline")f.setNow(2500);
 else {f.replies[0](f.result);await f.flush();const view=JSON.parse(JSON.stringify(f.snapshot));view.events=[{kind:"presence_expired",service_incarnation:"current-owner",at:f.result.presence_expires}];f.deliver(view);}
 if(!["foreign-generation","native-expiry"].includes(loss))f.replies[0](f.result);
 await f.flush();if(loss!=="pagehide")f.deliver(f.current());await f.flush();await task;
 assert.equal(t.controllerGeneration,null,"late HTTP/current-looking view cannot restore canceled authority");
 assert.equal(t.pendingClaim??null,null);assert.equal(f.requests.filter(x=>x.url==="/api/claim").length,1);
 assert.equal(t.elbowJog,0);assert.equal(t.connection,null);
});

test("claim qualification newer explicit claim cancels old accepted continuation",async()=>{
 const f=await claimFixture(),t=f.transport,old=t.claim();await f.flush();const next=t.claim();await f.flush();
 f.replies[0](f.result);await f.flush();assert.equal(t.controllerGeneration,null);
 f.replies[1](f.result);await f.flush();assert.equal(t.controllerGeneration,null);
 f.deliver(f.current());await Promise.all([old,next]);assert.equal(t.controllerGeneration,f.result.controller_generation);
 assert.equal(f.requests.filter(x=>x.url==="/api/claim").length,2);
});

test("claim qualification timeout is finite even when the REST result never arrives",async()=>{
 const f=await claimFixture(),t=f.transport,task=t.claim();await f.flush();
 const timer=f.timers.find(value=>value&&value[1]===1500);assert(timer,"one original1.5s claim opportunity required");
 f.setNow(2500);timer[0]();const result=await task;assert.equal(result.status,"superseded");
 assert.equal(t.controllerGeneration,null);assert.equal(t.pendingClaim??null,null);assert.equal(f.requests.filter(x=>x.url==="/api/claim").length,1);
 f.replies[0](f.result);await f.flush();f.deliver(f.current());await f.flush();assert.equal(t.controllerGeneration,null);
});

for(const bad of ["missing-reply-expiry","nonfinite-reply-expiry","missing-snapshot-expiry","nonfinite-snapshot-expiry"])
test(`claim qualification refuses incomplete producer proof ${bad}`,async()=>{
 const f=await claimFixture(),t=f.transport,task=t.claim(),reply={...f.result},view=f.current();await f.flush();
 if(bad==="missing-reply-expiry")delete reply.presence_expires;
 else if(bad==="nonfinite-reply-expiry")reply.presence_expires=NaN;
 else if(bad==="missing-snapshot-expiry")delete view.controller.presence_expires;
 else view.controller.presence_expires=NaN;
 f.replies[0](reply);await f.flush();f.deliver(view);await f.flush();
 if(t.pendingClaim){f.setNow(2500);const timer=f.timers.find(value=>value&&value[1]===1500);timer[0]();}
 await task;assert.equal(t.controllerGeneration,null);assert.equal(t.pendingClaim??null,null);
});

test("claim qualification compares only same-owner server expiry values and rejects expired epochs",async()=>{
 const f=await claimFixture(),t=f.transport,task=t.claim();await f.flush();f.replies[0](f.result);await f.flush();
 const old=f.current();old.controller.presence_expires-=0.1;old.events=[{kind:"presence_expired",service_incarnation:"historical-owner",at:999999}];f.deliver(old);await f.flush();
 assert.equal(t.controllerGeneration,null,"a pre-renewal proof cannot confirm the returned presence interval");
 f.deliver(f.current());await task;assert.equal(t.controllerGeneration,f.result.controller_generation);
 f.deliver(f.snapshot);assert.equal(t.controllerGeneration,null,"observed controller loss after confirmation remains immediate");
});

test("claim renewal supplied generation never adopts another generation from HTTP",async()=>{
 const f=await claimFixture(),t=f.transport;f.deliver(f.current());t.controllerGeneration=f.result.controller_generation;
 const task=t.claim(t.controllerGeneration);await f.flush();f.replies[0]({...f.result,controller_generation:"unexpected-generation"});await task;
 assert.equal(t.controllerGeneration,null);assert.equal(t.pendingClaim??null,null);
 assert.equal(f.requests.at(-1).body.controller_generation,f.result.controller_generation);
});


test("claim qualification connection change cancels without changing the newer connection",async()=>{
 const f=await claimFixture(),t=f.transport,task=t.claim();await f.flush();t.connection="newer-connection";
 f.replies[0](f.result);await f.flush();f.deliver(f.current());await task;
 assert.equal(t.controllerGeneration,null);assert.equal(t.pendingClaim??null,null);assert.equal(t.connection,"newer-connection");
});

test("claim qualification is independent of browser and owner clock offsets",async()=>{
 const f=await claimFixture(),t=f.transport;f.setNow(5000000);const task=t.claim();await f.flush();
 f.replies[0](f.result);await f.flush();assert.equal(t.controllerGeneration,null);f.deliver(f.current());await task;
 assert.equal(t.controllerGeneration,f.result.controller_generation);assert.equal(f.timers.filter(Boolean).length,0);
});


for(const replacement of ["new-connection","same-epoch-new-claim"])
test(`claim qualification late reporting error cannot clear ${replacement}`,async()=>{
 const f=await claimFixture(),t=f.transport,old=t.claim();await f.flush();
 if(replacement==="new-connection"){t.socket.onclose();await t.attach();f.deliver(f.snapshot,"new-view:1");}
 const next=t.claim();await f.flush();const pending=t.pendingClaim;
 f.replies[0]({accepted:false,status:"reporting_error",reason:"old claim reporting failure",code:"response_limit"});await f.flush();
 assert.equal(t.connected,true,"stale presentation failure cannot disconnect the newer context");
 assert.equal(t.pendingClaim,pending,"stale presentation failure cannot cancel the newer explicit Claim");
 assert.equal(t.reportingError,null);
 f.replies[1](f.result);await f.flush();f.deliver(f.current());await Promise.all([old,next]);
 assert.equal(t.controllerGeneration,f.result.controller_generation);assert.equal(t.reportingError,null);
});

test("claim renewal late reporting error cannot clear a newer connection and confirmed role",async()=>{
 const f=await claimFixture(),t=f.transport;f.deliver(f.current());t.controllerGeneration=f.result.controller_generation;
 const old=t.renew();await f.flush();t.socket.onclose();await t.attach();f.deliver(f.snapshot,"new-view:1");
 const next=t.claim();await f.flush();f.replies[1](f.result);await f.flush();f.deliver(f.current());await next;
 f.replies[0]({accepted:false,status:"reporting_error",reason:"old renewal reporting failure",code:"response_limit"});await f.flush();await old;
 assert.equal(t.connected,true);assert.equal(t.reportingError,null);assert.equal(t.controllerGeneration,f.result.controller_generation);
});



async function renewalFixture() {
 const f=await claimFixture(),t=f.transport;f.deliver(f.current());t.controllerGeneration=f.result.controller_generation;
 t.connection="current-wire";t.held.add("w");t.elbowJog=-1;t.elbowCursor=4;
 return f;
}

test("bounded renewal drops repeated timer opportunities and explicit owned Claim while fetch is deferred",async()=>{
 const f=await renewalFixture(),t=f.transport,tasks=[];
 try{
  for(let tick=0;tick<8;tick++){tasks.push(t.renew());await f.flush();}
  tasks.push(t.claim());tasks.push(t.claim(t.controllerGeneration));await f.flush();
  assert.equal(f.requests.filter(row=>row.url==="/api/claim").length,1,"one unresolved HTTP renewal must drop further500ms opportunities and explicit renewals");
  assert(t.pendingRenewal);assert.equal(t.pending.size,0,"WSS pending does not count the HTTP request");
  f.replies[0](f.result);await Promise.all(tasks);assert.equal(t.pendingRenewal??null,null);
  assert.equal(t.controllerGeneration,f.result.controller_generation);
  await f.flush();assert.equal(f.requests.filter(row=>row.url==="/api/claim").length,1,"dropped opportunities must never replay");
 }finally{for(const reply of f.replies)reply(f.result);await f.flush();t.clear();await Promise.all(tasks);}
});

test("bounded renewal one-second deadline aborts a never-returning fetch and clears local held authority",async()=>{
 const f=await renewalFixture(),t=f.transport,task=t.renew();await f.flush();
 const pending=t.pendingRenewal,request=f.requests.at(-1),timer=f.timers.find(row=>row&&row[1]===1000);
 assert(pending&&timer);assert(request.signal);assert.equal(request.signal.aborted,false);
 f.setNow(1999);assert.equal(t.controllerGeneration,f.result.controller_generation);
 f.setNow(2000);timer[0]();const result=await task;
 assert.equal(result.status,"superseded");assert.equal(request.signal.aborted,true);assert.equal(t.pendingRenewal??null,null);
 assert.equal(t.controllerGeneration,null);assert.equal(t.connection,null);assert.deepEqual([...t.held],[]);assert.equal(t.elbowJog,0);assert.equal(t.elbowCursor,null);
 assert.match(f.renders.at(-1),/Claim.*Resume/i);await t.renew();assert.equal(f.requests.filter(row=>row.url==="/api/claim").length,1,"timeout never auto-claims");
 f.replies[0](f.result);await f.flush();assert.equal(t.controllerGeneration,null);
});

for(const loss of ["clear","disconnect","pagehide","service","device","generation","connection","initialization"])
test(`bounded renewal cancels and aborts on ${loss} without late revival`,async()=>{
 const f=await renewalFixture(),t=f.transport,task=t.renew();await f.flush();const request=f.requests.at(-1);
 if(loss==="clear")t.clear();else if(loss==="disconnect")t.socket.onclose();else if(loss==="pagehide")t.detach();
 else {const view=f.current();
  if(loss==="service")view.service_incarnation="new-owner";
  if(loss==="device")view.controller.device_id="other-device";
  if(loss==="generation")view.controller.controller_generation="foreign-generation";
  if(loss==="connection")t.connection="new-wire";
  if(loss==="initialization")t.initialization++;
  f.deliver(view);
 }
 assert.equal(request.signal.aborted,true);assert.equal(t.pendingRenewal??null,null);await task;
 const before={epoch:t.epoch,connection:t.connection,generation:t.controllerGeneration,connected:t.connected};
 f.replies[0]({accepted:false,status:"reporting_error",code:"response_limit",reason:"stale renewal presentation"});await f.flush();
 assert.deepEqual({epoch:t.epoch,connection:t.connection,generation:t.controllerGeneration,connected:t.connected},before);
 assert.equal(t.reportingError,null);assert.equal(f.requests.filter(row=>row.url==="/api/claim").length,1);
});

for(const result of ["accepted","refused","reporting-error"])
test(`bounded renewal late ${result} cannot settle or clear a newer pending generation`,async()=>{
 const f=await renewalFixture(),t=f.transport,old=t.renew();await f.flush();const oldSignal=f.requests.at(-1).signal;
 t.clear();f.result.controller_generation="fresh-generation";f.deliver(f.current());t.controllerGeneration=f.result.controller_generation;t.connection="fresh-wire";
 const next=t.renew();await f.flush();const pending=t.pendingRenewal,signal=f.requests.at(-1).signal;
 assert.equal(oldSignal.aborted,true);assert(pending);assert.equal(signal.aborted,false);
 f.replies[0](result==="accepted"?{...f.result,controller_generation:"old-generation"}:result==="refused"?{accepted:false,status:"refused",reason:"old fence"}:{accepted:false,status:"reporting_error",code:"response_limit",reason:"old reporting failure"});
 await f.flush();await old;assert.equal(t.pendingRenewal,pending);assert.equal(signal.aborted,false);assert.equal(t.reportingError,null);assert.equal(t.controllerGeneration,"fresh-generation");
 f.replies[1](f.result);await next;assert.equal(t.pendingRenewal??null,null);assert.equal(t.controllerGeneration,"fresh-generation");
});

for(const failure of ["network","refused","wrong-generation"])
test(`bounded renewal current ${failure} clears authority and never restores held input`,async()=>{
 const f=await renewalFixture(),t=f.transport,task=t.renew();await f.flush();const request=f.requests.at(-1);
 if(failure==="network")f.failures[0](Error("synthetic renewal network failure"));
 else f.replies[0](failure==="refused"?{accepted:false,status:"refused",reason:"controller renewal fence"}:{...f.result,controller_generation:"foreign"});
 await task;assert.equal(t.controllerGeneration,null);assert.equal(t.pendingRenewal??null,null);assert.equal(request.signal.aborted,true);
 assert.equal(t.elbowJog,0);assert.deepEqual([...t.held],[]);assert.match(f.renders.at(-1),/Claim.*Resume/i);
});

test("bounded renewal late current response cannot exceed its original request deadline before timer delivery",async()=>{
 const f=await renewalFixture(),t=f.transport,task=t.renew();await f.flush();f.setNow(2000);
 f.replies[0]({...f.result,status:"reporting_error",reason:"late reporting failure",code:"response_limit"});await task;
 assert.equal(t.controllerGeneration,null);assert.equal(t.pendingRenewal??null,null);assert.equal(t.reportingError,null,"expired request cannot report into the current view");
});

test("bounded renewal leaves ordinary Stop independent while its HTTP result is unresolved",async()=>{
 const f=await renewalFixture(),t=f.transport,task=t.renew();await f.flush();const pending=t.pendingRenewal;
 const stopped=await t.protective("stop");assert.equal(stopped.accepted,true);assert.equal(stopped.status,"stopped");
 assert.equal(f.requests.at(-1).url,"/api/stop");assert.equal(t.pendingRenewal,pending);assert.equal(t.elbowJog,0);
 t.clear("stopped");await task;assert.equal(f.requests.find(row=>row.url==="/api/claim").signal.aborted,true);
});
