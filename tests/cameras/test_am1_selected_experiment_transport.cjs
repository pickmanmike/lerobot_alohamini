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
test("named coarse bench preset uses the existing selected recipe and immutable schema2 Start",async()=>{
  const data={...descriptor(),schema_version:2,envelope_sha256:"b".repeat(64),excursion_norm:10,
    current_cap_ma:150,live_ceiling_s:90,goal_readback_checkpoints_s:[5,37],cycle_count:2,intercycle_hold_s:6};
  const requests=[],transport=fixture(async(url,options)=>{requests.push(JSON.parse(options.body));return {ok:true,json:async()=>({accepted:true})};});
  transport.snapshot={presets:[{id:"left-elbow-coarse-bench-v1",version:1,label:"Two cycles",recipe,selected_experiment:data}]};
  await transport.startPreset("left-elbow-coarse-bench-v1");
  assert.equal(requests.length,1);assert.equal(requests[0].recipe,recipe);
  assert.deepEqual(requests[0].selected_experiment,data);
  await assert.rejects(()=>transport.startPreset("missing"),/preset/i);
  for(const bad of [{...data,cycle_count:3},{...data,intercycle_hold_s:7},{...data,unknown:1}])
    await assert.rejects(()=>transport.start(recipe,null,bad),/experiment/i);
  assert.equal(requests.length,1);
});


function operationDescriptor(){return {...descriptor(),schema_version:2,
  envelope_sha256:"2d55c9e82d82ea3e66b6bf336853ad84a84cbf2cc3a62ff5e30a7f2f6f55ecef",
  excursion_norm:10,ramp_s:10,endpoint_dwell_s:2,current_cap_ma:150,live_ceiling_s:90,
  goal_readback_checkpoints_s:[5,37],cycle_count:2,intercycle_hold_s:6};}
test("reviewed V2 preset issues one ordinary Start with its unchanged shared descriptor",async()=>{
  const data={...operationDescriptor(),envelope_sha256:"ea8ed4e0abd5222734cd77cffc22c50739d53ab4cbde999016b33606af7648f5"},original=JSON.stringify(data),requests=[];
  assert.equal(require("node:crypto").createHash("sha256").update(JSON.stringify(Object.fromEntries(Object.keys(data).sort().map(key=>[key,data[key]])))).digest("hex"),"31336b83330fb5398778b4cfc0397011a3ae063d47efdf9c02e62d685475d473");
  const transport=fixture(async(url,options)=>{requests.push([url,JSON.parse(options.body)]);return {ok:true,json:async()=>({accepted:true,status:"starting"})};});
  transport.snapshot={presets:[{id:"left-elbow-coarse-bench-v2",version:2,label:"Left elbow coarse bench v2 · two cycles",recipe,selected_experiment:data}]};
  await transport.startPreset("left-elbow-coarse-bench-v2");
  assert.equal(requests.length,1);assert.equal(requests[0][0],"/api/start");
  assert.equal(JSON.stringify(requests[0][1].selected_experiment),original);
  assert.equal(requests[0][1].controller_generation,"claimed");assert.equal(typeof requests[0][1].operation_id,"string");
  assert.equal(JSON.stringify(data),original);
});
for(const [name,id,version] of [
  ["unknown ID","unreviewed-preset",1],["V2 ID with V1 version","left-elbow-coarse-bench-v2",1],
  ["V1 ID with V2 version","left-elbow-coarse-bench-v1",2],["version3","left-elbow-coarse-bench-v2",3],
  ["boolean version","left-elbow-coarse-bench-v2",true],["string version","left-elbow-coarse-bench-v2","2"],
  ["boolean ID",true,1],["false ID",false,1],
])test("named preset rejects "+name+" before any control request",async()=>{
  let calls=0;const transport=fixture(async()=>{calls++;return {ok:true,json:async()=>({accepted:true})};});
  transport.snapshot={presets:[{id,version,recipe,selected_experiment:operationDescriptor()}]};
  await assert.rejects(()=>transport.startPreset(id),/preset/i);assert.equal(calls,0);
});
