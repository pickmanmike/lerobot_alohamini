"use strict";
const {test}=require("node:test"), assert=require("node:assert/strict"), fs=require("node:fs"), vm=require("node:vm"), path=require("node:path");
const source=fs.readFileSync(path.join(__dirname,"../../tools/am1_console_ui/session_transport.js"),"utf8");
function fixture(fetch) {
  let now=1000;
  const timers=[], renders=[], context={fetch,crypto:require("node:crypto").webcrypto,console,atob,TextDecoder,Uint8Array,performance:{now:()=>now},
    setTimeout:(f,t)=>{timers.push([f,t]);return timers.length;},clearTimeout:id=>{timers[id-1]=null;},
    location:{origin:"https://test.invalid"},WebSocket:class {static OPEN=1; constructor(){this.readyState=1;} send(){} close(){this.readyState=3;this.onclose?.();}}};
  vm.runInNewContext(source,context);
  const transport=new context.AM1SessionTransport((t,notice)=>renders.push(notice));
  transport.connected=true;transport.controllerGeneration="claimed";
  transport.snapshot={run:{run_id:"old-run",status:"finishing"}};
  return {transport,timers,renders,setNow:value=>{now=value;},async retry(){const i=timers.findIndex(Boolean);assert(i>=0,"bounded reconnect is pending");const [f]=timers[i];timers[i]=null;await f();}};
}

async function liveFixture() {
  const snapshot={service_incarnation:"owner",controller:{device_id:"enrolled",controller_generation:"claimed"},
    run:{run_id:"live-run",recipe:"physical-live-left-elbow",source:"protected-physical-provider",status:"running",
      backend_incarnation:"motor",intent_revision:7,deadline:90,live_input:{joint:"arm_left_elbow_flex.pos",
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
  assert.equal(f.transport.setElbowJog(1),true);f.setNow(1200);
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
  assert.equal(f.transport.setElbowJog(1),true);
  f.reply("input",{accepted:false,status:"refused",reason:"expired grant"});await Promise.resolve();await Promise.resolve();
  assert.equal(f.transport.elbowJog,1);assert.equal(f.transport.elbowCursor,2);f.transport.clear();
});


test("a prior revision's late input refusal settles its fence before the fresh gesture obtains a new grant",async()=>{
  const f=await liveFixture();f.transport.setElbowJog(-1);f.setNow(1100);
  const first=f.transport.input();f.reply("grant",{accepted:true,grant_id:"a",issued:10,expires:10.25});await first;
  const snapshot=JSON.parse(JSON.stringify(f.transport.snapshot));snapshot.run.intent_revision=8;
  Object.assign(snapshot.run.live_input,{cursor_norm:2,cursor_feedback_sequence:40,cursor_intent_revision:8});
  f.transport.socket.onmessage({data:JSON.stringify({kind:"snapshot",revision:2,snapshot})});assert.equal(f.transport.setElbowJog(1),true);
  await f.transport.input();assert.equal(f.frames.filter(frame=>frame.command?.op==="grant").length,1);
  f.reply("input",{accepted:false,status:"refused",reason:"expired grant"});await Promise.resolve();await Promise.resolve();await Promise.resolve();
  assert.equal(f.transport.elbowJog,1);assert.equal(f.transport.liveInputs.size,0);assert.equal(f.transport.pending.size,0);
  f.setNow(1150);const next=f.transport.input();f.reply("grant",{accepted:true,grant_id:"b",issued:11,expires:11.25});await next;
  assert.equal(f.frames.at(-1).command.grant_id,"b");assert.equal(f.frames.at(-1).command.intent_revision,8);
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
