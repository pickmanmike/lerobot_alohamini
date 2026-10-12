"use strict";
const {test}=require("node:test"), assert=require("node:assert/strict"), fs=require("node:fs"), vm=require("node:vm"), path=require("node:path");
const source=fs.readFileSync(path.join(__dirname,"../../tools/am1_console_ui/session_transport.js"),"utf8");
function fixture(fetch) {
  const timers=[], renders=[], context={fetch,crypto:require("node:crypto").webcrypto,console,
    setTimeout:(f,t)=>{timers.push([f,t]);return timers.length;},clearTimeout:()=>{},
    location:{origin:"https://test.invalid"},WebSocket:class {static OPEN=1; constructor(){this.readyState=1;}}};
  vm.runInNewContext(source,context);
  const transport=new context.AM1SessionTransport((t,notice)=>renders.push(notice));
  transport.connected=true;transport.controllerGeneration="claimed";
  transport.snapshot={run:{run_id:"old-run",status:"finishing"}};
  return {transport,timers,renders};
}
const reporting={accepted:true,status:"reporting_error",code:"response_limit",reason:"Owner response exceeds the presentation limit",effect_admitted:true,
  run_id:"actual-new-run",operation_result:{accepted:true,status:"starting",effect_admitted:true,run_id:"actual-new-run"}};
test("known applied Start reporting failure never retries and promptly fences controls",async()=>{
  const requests=[],f=fixture(async(url,opts)=>{requests.push([url,JSON.parse(opts.body)]);return {ok:true,json:async()=>reporting};});
  const result=await f.transport.start("physical-left-elbow-positive-control");
  assert.equal(requests.length,1);assert.equal(result.accepted,true);assert.equal(result.effect_admitted,true);
  assert.equal(f.transport.reportingError.code,"response_limit");assert.equal(f.transport.reportingError.run_id,"actual-new-run");
  assert.equal(f.transport.connected,false);assert.equal(f.transport.controllerGeneration,null);
  assert.equal(f.transport.snapshot.run.status,"finishing","old snapshot is retained only as forensic cache");
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
