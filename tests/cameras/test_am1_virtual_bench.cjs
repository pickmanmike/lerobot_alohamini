"use strict";
const assert = require("node:assert/strict");
const {test} = require("node:test");
const {validateVirtualConfig, VirtualObservationPolicy, qualifyRecovery, recoveryAcknowledged} = require("../../tools/am1_virtual_bench.cjs");
const config = () => ({observer_health_path:"C:/private/latest-health.json", observer_generation:"capture-03",
  verified_coverage:["arms","lift"], required_camera_roles:[], recovery_episode_seconds:10,max_recoveries:3});
const observer = (sequence=1, received=10000) => ({generation:"capture-03", running:true,recording:true,
  challenge_qualified:true,nonce:`nonce-${sequence}`,sequence,source_system_relative_ticks:sequence*10000,capture_age_ms:20,round_trip_ms:100,received_wall_time_ms:received});
const cameras = () => ({version:1,selected_role:"forward",status_available:true,status_uncertain:false,status_received_age_ms:20,
  roles:["forward","backward","chest","wrist_left","wrist_right"].map(role=>({role,identity:role,selected:role==="forward",
    configured:true,generation:1,decoded_generation:1,sequence:10,decoded_age_ms:20,
    source_sequence:11,source_state:"fresh",source_age_ms:30,fresh:true,status_uncertain:false}))});
function readyPolicy(raw=config()) {
  const policy=new VirtualObservationPolicy(raw);
  assert.equal(policy.observe(observer(1),cameras(),10000,0).required_coverage_qualified,false);
  assert.equal(policy.observe(observer(2,10500),cameras(),10500,500).required_coverage_qualified,true);
  return policy;
}
test("explicit virtual policy validates verified framing and finite recovery bounds",()=>{
  assert.equal(validateVirtualConfig(config()).max_recoveries,3);
  for (const change of [{verified_coverage:["arms"]},{verified_coverage:["arms","lift","extra"]},{required_camera_roles:["arbitrary"]},
                        {recovery_episode_seconds:11},{max_recoveries:4},{observer_generation:""}])
    assert.throws(()=>validateVirtualConfig({...config(),...change}));
});
test("observer qualification requires current nonce, exact generation and advancement without re-aging receipt",()=>{
  const policy=readyPolicy();
  const good=policy.observe(observer(2,10500),cameras(),10900,900);
  assert.equal(good.required_coverage_qualified,true);
  assert.equal(good.observer.effective_capture_age_ms,500);
  assert.equal(policy.observe(observer(2,10500),cameras(),11000,1000).required_coverage_qualified,false);
  assert.equal(policy.observe({...observer(3,11100),challenge_qualified:false},cameras(),11100,1100).required_coverage_qualified,false);
  assert.equal(policy.observe({...observer(4,11200),generation:"old-capture"},cameras(),11200,1200).required_coverage_qualified,false);
  assert.equal(policy.observe({...observer(5,11300),round_trip_ms:700},cameras(),11300,1300).required_coverage_qualified,false);
  assert.equal(policy.observe({...observer(5,11300),round_trip_ms:751},cameras(),11300,1300).required_coverage_qualified,false);
});
test("optional camera degradation is separate quality with bounded role-local reconnection",()=>{
  const policy=readyPolicy(), health=cameras();
  health.roles.find(role=>role.role==="chest").decoded_age_ms=1653;
  health.roles.find(role=>role.role==="chest").fresh=false;
  const first=policy.observe(observer(3,11000),health,11000,1000);
  assert.equal(first.required_coverage_qualified,true);
  assert.equal(first.all_five_fresh,false);
  assert.deepEqual(first.degraded_roles,["chest"]);
  assert.deepEqual(policy.reconnectRoles(first,1000),["chest"]);
  assert.deepEqual(policy.reconnectRoles(first,1500),[]);
  assert.deepEqual(policy.reconnectRoles(first,2000),["chest"]);
  assert.deepEqual(policy.reconnectRoles(first,3000),["chest"]);
  assert.deepEqual(policy.reconnectRoles(first,4000),[]);
});
test("required view identity, generation, original decoded and source ages all qualify",()=>{
  const policy=readyPolicy({...config(),required_camera_roles:["forward"]}), health=cameras();
  for (const change of [{identity:"chest"},{decoded_generation:0},{decoded_age_ms:500},
                       {source_age_ms:501},{source_state:"stale"}]) {
    Object.assign(health.roles[0],cameras().roles[0],change);
    assert.equal(policy.observe(observer(3,11000),health,11000,1000).required_coverage_qualified,false);
  }
});
function paused() {
  return {session_id:"owned",phase:"paused",native_connected:true,pause_required:true,input_epoch:7,
    input_pause:{reason:"bench required coverage"},pending_gate:["resume",23],
    telemetry:{observation:{age_ms:100}}, virtual_bench:{enabled:true,disarmed:false,cause:"bench required coverage",
    pause_sequence:4,input_epoch:7,host_epoch:23,recovery_count:0,remaining_seconds:8,
    required_coverage_qualified:true,recovery_eligible:true}};
}
test("recovery binds the exact owned current resume gate and refuses operator, fault and stale feedback",()=>{
  assert.deepEqual(qualifyRecovery(paused(),"owned",true,3),{pause_sequence:4,input_epoch:7,host_epoch:23});
  for (const change of [{session_id:"foreign"},{input_epoch:8},{pending_gate:["resume",22]},
                        {pending_gate:["live_start",23]},{input_pause:{reason:"operator"}},
                        {telemetry:{observation:{age_ms:251}}},{native_connected:false}])
    assert.equal(qualifyRecovery({...paused(),...change},"owned",true,3),null);
  assert.equal(qualifyRecovery(paused(),"owned",false,3),null);
  assert.equal(qualifyRecovery({...paused(),virtual_bench:{...paused().virtual_bench,disarmed:true}},"owned",true,3),null);
});

test("HTTP Resume acknowledgment alone cannot end an episode; fresh native epoch and original deadline are required",()=>{
  const proof={pause_sequence:4,input_epoch:7,host_epoch:23};
  const active={...paused(),phase:"live",pause_required:false,pending_gate:null,
    telemetry:{observation:{age_ms:100,host_state:"active",host_epoch:24}}};
  assert.equal(recoveryAcknowledged(active,proof,9999,10000),true);
  assert.equal(recoveryAcknowledged(active,proof,10000,10000),false);
  for(const change of [{host_state:"paused"},{host_epoch:23},{age_ms:251}])
    assert.equal(recoveryAcknowledged({...active,telemetry:{observation:{...active.telemetry.observation,...change}}},proof,1000,10000),false);
  assert.equal(recoveryAcknowledged({...active,pause_required:true},proof,1000,10000),false);
  assert.equal(recoveryAcknowledged({...active,virtual_bench:{...active.virtual_bench,required_coverage_qualified:false}},proof,1000,10000),false);
});

test("transient Windows read refusal retains only accepted original observer clocks until expiry",()=>{
  const policy=readyPolicy();
  const retained=policy.observe(null,cameras(),10600,600,"EACCES");
  assert.equal(retained.required_coverage_qualified,true);
  assert.equal(retained.observer.metadata_uncertain,true);
  assert.equal(retained.observer.effective_capture_age_ms,200);
  assert.equal(retained.observer.local_sequence_age_ms,100);
  assert.equal(policy.observe(null,cameras(),11100,1100,"EPERM").required_coverage_qualified,false);
  const absent=readyPolicy();
  assert.equal(absent.observe(null,cameras(),10600,600,"ENOENT").required_coverage_qualified,false);
  assert.equal(absent.observe(null,cameras(),10700,700,"EACCES").required_coverage_qualified,false);
  const invalid=readyPolicy();
  assert.equal(invalid.observe({...observer(3,10600),running:false},cameras(),10600,600).required_coverage_qualified,false);
  assert.equal(invalid.observe(null,cameras(),10700,700,"EACCES").required_coverage_qualified,false);
});


test("same-P1 QPC duration excludes pre-capture time without renewing original expiry",()=>{
  const policy=new VirtualObservationPolicy(config());
  const pair=(seq)=>({...observer(seq),timing_basis:"qpc_elapsed_v1",local_clock_resolution_ms:.0001,source_system_relative_ticks:(seq+1)*1000000,
    challenge_received_qpc_ticks:seq*1000000,capture_age_ms:300,round_trip_ms:550,source_age_upper_bound_ms:1});
  assert.equal(policy.observe(pair(1),cameras(),10000,0).required_coverage_qualified,false);
  const result=policy.observe(pair(2),cameras(),10000,100);
  assert.equal(result.required_coverage_qualified,true);
  assert.equal(result.observer.effective_capture_age_ms,451);
  assert.equal(policy.observe(pair(2),cameras(),10048,148).required_coverage_qualified,true);
  assert.equal(policy.observe(pair(2),cameras(),10050,150).required_coverage_qualified,false);
});
test("unknown clock and contradictory P1 intervals cannot qualify through an age claim",()=>{
  for(const change of [{timing_basis:"foreign_clock"},{local_clock_resolution_ms:15.625},{local_clock_resolution_ms:null},{challenge_received_qpc_ticks:0},
                       {challenge_received_qpc_ticks:3000000},{challenge_received_qpc_ticks:3000001},
                       {capture_age_ms:300,round_trip_ms:350},{round_trip_ms:99},{round_trip_ms:751}]) {
    const policy=readyPolicy();
    const sample={...observer(3,11000),timing_basis:"qpc_elapsed_v1",local_clock_resolution_ms:.0001,source_system_relative_ticks:3000000,
      challenge_received_qpc_ticks:2000000,capture_age_ms:200,round_trip_ms:350,source_age_upper_bound_ms:1,...change};
    assert.equal(policy.observe(sample,cameras(),11000,1000).required_coverage_qualified,false,JSON.stringify(change));
  }
});
