"use strict";
const {test}=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm"),path=require("node:path");
const source=fs.readFileSync(path.join(__dirname,"../../tools/am1_console_ui/session_transport.js"),"utf8");
const recipe="physical-left-elbow-experiment";
function descriptor(){return {schema_version:1,envelope_sha256:"a".repeat(64),excursion_norm:-10,ramp_s:10,
  endpoint_dwell_s:2,current_cap_ma:100,live_ceiling_s:60,minimum_response_raw:56,return_tolerance_raw:22,goal_readback_checkpoints_s:[4,5]};}
function fixture(fetch){const context={fetch,crypto:require("node:crypto").webcrypto,console,setTimeout:()=>1,clearTimeout:()=>{},
  location:{origin:"https://test.invalid"},WebSocket:class{static OPEN=1;}};
  vm.runInNewContext(source,context);const transport=new context.AM1SessionTransport(()=>{});
  transport.connected=true;transport.controllerGeneration="claimed";return transport;}
test("outcome-loss retry freezes descriptor and uses the same selected Start UUID",async()=>{
  const data=descriptor(),original=JSON.stringify(data),requests=[];
  const transport=fixture(async(url,options)=>{requests.push(JSON.parse(options.body));if(requests.length===1){data.current_cap_ma=900;data.goal_readback_checkpoints_s.push(6);throw Error("outcome lost");}
    return {ok:true,json:async()=>({accepted:true,status:"starting"})};});
  await transport.start(recipe,null,data);
  assert.equal(requests.length,2);assert.equal(requests[0].operation_id,requests[1].operation_id);
  assert.equal(JSON.stringify(requests[0].selected_experiment),original);assert.deepEqual(requests[1],requests[0]);
});
test("selected descriptor is mandatory, closed and recipe-scoped before HTTP",async()=>{
  let calls=0;const transport=fixture(async()=>{calls++;return {ok:true,json:async()=>({accepted:true})};});
  for(const data of [null,{...descriptor(),raw_origin:2458},{...descriptor(),integral:1},{...descriptor(),current_cap_ma:NaN},
    {...descriptor(),goal_readback_checkpoints_s:[true]}])await assert.rejects(()=>transport.start(recipe,null,data),/experiment/i);
  await assert.rejects(()=>transport.start("physical-left-elbow-positive-control",null,descriptor()),/experiment/i);
  await assert.rejects(()=>transport.start(recipe,"df51bc69-3f68-42bf-9c73-35fd4be520f6",descriptor()),/confirmation/i);
  assert.equal(calls,0);
});
test("known selected Start reporting error preserves admitted outcome without retry",async()=>{
  let calls=0;const transport=fixture(async()=>{calls++;return {ok:true,json:async()=>({accepted:true,status:"reporting_error",
    code:"response_limit",reason:"Owner response exceeds presentation limit",effect_admitted:true,run_id:"actual-run"})};});
  const result=await transport.start(recipe,null,descriptor());
  assert.equal(calls,1);assert.equal(result.effect_admitted,true);assert.equal(transport.connected,false);
});
test("selected completed program reports the actual separate movement verdict",()=>{
  const transport=fixture(()=>{}),run={source:"protected-physical-provider",recipe,status:"completed",
    execution:{positive_control:{program_completed:true,coarse_functional_pass:false,fine_return_pass:true,final_return_residual_raw:0}}};
  assert.match(transport.constructor.outcomeText(run),/coarse movement not achieved/);
  run.execution.positive_control.coarse_functional_pass=true;
  assert.match(transport.constructor.outcomeText(run),/coarse movement passed/);
  assert.equal(transport.constructor.outcomeText({...run,status:"running"}),null);
});
