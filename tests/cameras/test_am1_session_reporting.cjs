"use strict";
const {test}=require("node:test"), assert=require("node:assert/strict"), fs=require("node:fs"), vm=require("node:vm"), path=require("node:path");
const source=fs.readFileSync(path.join(__dirname,"../../tools/am1_console_ui/session_transport.js"),"utf8");
function fixture(fetch) {
  const timers=[], renders=[], context={fetch,crypto:require("node:crypto").webcrypto,console,atob,TextDecoder,Uint8Array,
    setTimeout:(f,t)=>{timers.push([f,t]);return timers.length;},clearTimeout:id=>{timers[id-1]=null;},
    location:{origin:"https://test.invalid"},WebSocket:class {static OPEN=1; constructor(){this.readyState=1;} send(){} close(){this.readyState=3;this.onclose?.();}}};
  vm.runInNewContext(source,context);
  const transport=new context.AM1SessionTransport((t,notice)=>renders.push(notice));
  transport.connected=true;transport.controllerGeneration="claimed";
  transport.snapshot={run:{run_id:"old-run",status:"finishing"}};
  return {transport,timers,renders,async retry(){const i=timers.findIndex(Boolean);assert(i>=0,"bounded reconnect is pending");const [f]=timers[i];timers[i]=null;await f();}};
}
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
