"use strict";
// Remote fake mode never instantiates the legacy body input, owner restore or camera adapters.
class AM1SessionTransport {
  static liveElbows=Object.freeze({
    "physical-live-left-elbow":Object.freeze({joint:"arm_left_elbow_flex.pos",side:"left",label:"Left elbow",profile:"LiveLeftElbow"}),
    "physical-live-right-elbow":Object.freeze({joint:"arm_right_elbow_flex.pos",side:"right",label:"Right elbow",profile:"LiveRightElbow"}),
    "physical-live-right-elbow-practical":Object.freeze({joint:"arm_right_elbow_flex.pos",side:"right",label:"Right elbow practical",profile:"LiveRightElbowPractical",
      practical_motion:true,rate_norm_per_s:5*4096/360*200/2212,current_cap_ma:500,live_s:180}),
    "physical-live-right-elbow-practical-p16":Object.freeze({joint:"arm_right_elbow_flex.pos",side:"right",label:"Right elbow practical P16",profile:"LiveRightElbowPracticalP16",
      practical_motion:true,rate_norm_per_s:5*4096/360*200/2212,current_cap_ma:500,live_s:180}),
    "physical-live-left-elbow-practical-p16":Object.freeze({joint:"arm_left_elbow_flex.pos",side:"left",label:"Left elbow practical P16",profile:"LiveLeftElbowPracticalP16",
      practical_motion:true,rate_norm_per_s:5*4096/360*200/2209,current_cap_ma:500,live_s:180})
  });
  static practicalElbows=Object.freeze({
    "physical-live-right-elbow-practical-p16":Object.freeze({id:"am1-right-elbow-practical-p16-v1",
      context_sha256:"07a2ff4cb1ada7ac9a1b99bfcbf2c68c976e7a764b9f647ef3723a4960f00523",
      minimum_raw:1847,maximum_raw:1960,minimum_norm:-7.775768535262202,maximum_norm:2.441229656419523}),
    "physical-live-left-elbow-practical-p16":Object.freeze({id:"am1-left-elbow-practical-p16-v1",
      context_sha256:"f2e11bcbd8cf67e60f3f3e2b79b4a099981172067e9e13d0a40bc51c47889d0d",
      minimum_raw:2011,maximum_raw:2124,minimum_norm:3.847894975101852,maximum_norm:14.078768673607982})
  });
  liveElbowSpec(recipe=this.snapshot?.run?.recipe) {
    const allowed=Object.hasOwn(AM1SessionTransport.liveElbows,recipe) ? AM1SessionTransport.liveElbows[recipe] : null,values=this.snapshot?.live_elbow_recipes;
    if(!allowed || !Array.isArray(values) || values.length>Object.keys(AM1SessionTransport.liveElbows).length)return null;
    const matches=values.filter(value=>value?.recipe===recipe);
    if(matches.length!==1)return null;
    const value=matches[0];
    if(Object.entries(allowed).some(([key,wanted])=>value[key]!==wanted)
      || typeof value.rate_norm_per_s!=="number" || !Number.isFinite(value.rate_norm_per_s)
      || value.rate_norm_per_s<=0 || value.rate_norm_per_s>(allowed.practical_motion ? allowed.rate_norm_per_s : 1)
      || Object.hasOwn(AM1SessionTransport.practicalElbows,recipe)&&!this.supportedWorkspace(value))return null;
    return value;
  }
  supportedWorkspace(spec) {
    const w=spec?.supported_workspace,keys=["version","id","context_sha256","evidence_min_raw","evidence_max_raw","command_min_raw","command_max_raw","command_min_norm","command_max_norm","span_norm","requires_current_admission"];
    const preset=Object.hasOwn(AM1SessionTransport.practicalElbows,spec?.recipe) ? AM1SessionTransport.practicalElbows[spec.recipe] : null;
    if(preset) {
      const exact={version:1,id:preset.id,context_sha256:preset.context_sha256,
        evidence_min_raw:preset.minimum_raw,evidence_max_raw:preset.maximum_raw,
        command_min_raw:preset.minimum_raw,command_max_raw:preset.maximum_raw,
        command_min_norm:preset.minimum_norm,command_max_norm:preset.maximum_norm,span_norm:preset.maximum_norm-preset.minimum_norm,
        requires_current_admission:true,practical_motion:true,maximum_excursion_degrees:10,measured_margin_degrees:3};
      if(!w||typeof w!=="object"||Array.isArray(w)||Object.keys(w).length!==Object.keys(exact).length+1
        ||Object.entries(exact).some(([key,wanted])=>!Object.hasOwn(w,key)||w[key]!==wanted)
        ||!w.config||typeof w.config!=="object"||Array.isArray(w.config)||Object.keys(w.config).length!==1||w.config.recipe!==spec.recipe)return null;
      return w;
    }
    if(spec?.side!=="right"||!w||typeof w!=="object"||Object.keys(w).length!==keys.length||keys.some(key=>!Object.hasOwn(w,key))
      ||w.version!==1||!["am1-right-elbow-retained-v1","am1-right-elbow-rest-neighbourhood-v2"].includes(w.id)||typeof w.context_sha256!=="string"||!/^[0-9a-f]{64}$/.test(w.context_sha256)||w.requires_current_admission!==true
      ||![w.evidence_min_raw,w.evidence_max_raw,w.command_min_raw,w.command_max_raw].every(Number.isInteger)||w.evidence_min_raw>w.command_min_raw||w.command_min_raw>=w.command_max_raw||w.command_max_raw>w.evidence_max_raw
      ||![w.command_min_norm,w.command_max_norm,w.span_norm].every(value=>typeof value==="number"&&Number.isFinite(value))
       ||w.command_min_norm>=w.command_max_norm||w.span_norm<=0||w.span_norm>3||Math.abs(w.command_max_norm-w.command_min_norm-w.span_norm)>1e-9)return null;
    return w;
  }
  static outcomeText(run) {
    const outcome=run?.execution?.positive_control;
    if(run?.source!=="protected-physical-provider" || !["physical-left-elbow-positive-control","physical-left-elbow-experiment"].includes(run.recipe) || run.status!=="completed" ||
      outcome?.program_completed!==true || typeof outcome.coarse_functional_pass!=="boolean" || typeof outcome.fine_return_pass!=="boolean" ||
      !Number.isInteger(outcome.final_return_residual_raw) || outcome.final_return_residual_raw<0)return null;
    return `Diagnostic completed · coarse movement ${outcome.coarse_functional_pass ? "passed" : "not achieved"} · fine return ${outcome.fine_return_pass ? "passed" : "not achieved"} · final error ${outcome.final_return_residual_raw} raw ticks`;
  }
  constructor(render) {
    this.render=render; this.snapshot=null; this.connected=false; this.csrf=null; this.device=null;
    this.control=false; this.controllerGeneration=null; this.connection=null; this.held=new Set();
    this.pending=new Map(); this.pendingClaim=null; this.pendingRenewal=null; this.seq=0; this.socket=null; this.inputBusy=false; this.epoch=0; this.intent=0;
    this.reportingError=null;
    this.elbowJog=0;this.elbowCursor=null;this.elbowBinding=null;this.elbowAt=null;this.elbowReleasedRevision=null;this.liveInputs=new Set();this.liveResume=null;this.elbowGesture=null;this.elbowRelease=null;this.elbowStrict=false;
    this.reconnectTimer=null;this.detached=false;this.initialization=0;
  }
  clear(reason="disconnected") {
    this.cancelClaim(reason);this.cancelRenewal(reason);
    if(this.isLiveElbow())this.elbowStrict=true;this.elbowRelease=null;
    this.epoch++; this.intent++; this.held.clear(); this.connection=null; this.controllerGeneration=null; this.seq=0;
    for(const p of this.pending.values()) p.reject(Error(reason));
    this.pending.clear(); this.inputBusy=false;this.liveResume=null;this.clearGesture();this.elbowReleasedRevision=null;this.liveInputs.clear();
  }
  reportingFailure(value) {
    this.reportingError=value;this.connected=false;this.clear(value.reason);
    this.render(this,value.reason);
  }
  async post(path,body,epoch,current,signal) {
    const response=await fetch(`/api/${path}`,{method:"POST",headers:{"Content-Type":"application/json","X-AM1-CSRF":this.csrf || ""},body:JSON.stringify(body),signal});
    const result=await response.json();
    if(epoch!==undefined && (this.detached || epoch!==this.epoch) || current && !current())return {status:"superseded"};
    if(result.status==="reporting_error") {
      this.reportingFailure(result);
      if(!response.ok) {const error=Error(result.reason);error.reportingError=result;throw error;}
      return result;
    }
    if(!response.ok) throw Error(result.reason || "Request refused");
    return result;
  }
  async enroll(code) {await this.post("enroll",{code}); await this.initialize();}
  cancelReconnect() {
    if(this.reconnectTimer!==null)clearTimeout(this.reconnectTimer);
    this.reconnectTimer=null;
  }
  reconnect(delay) {
    if(this.detached || this.reconnectTimer!==null)return;
    this.reconnectTimer=setTimeout(()=>{this.reconnectTimer=null;return this.initialize();},delay);
  }
  detach() {
    this.detached=true;this.initialization++;this.cancelReconnect();this.connected=false;this.clear("Page detached");
    const socket=this.socket;this.socket=null;socket?.close();
  }
  restore() {
    if(!this.detached)return;
    this.detached=false;return this.initialize();
  }
  async initialize() {
    if(this.detached)return;
    const initialization=++this.initialization;this.cancelReconnect();this.connected=false;this.clear();
    const socket=this.socket;this.socket=null;socket?.close();
    try {
      const response=await fetch("/api/session");
      if(this.detached || initialization!==this.initialization)return;
      if(!response.ok) {
        if(response.status===401) {this.device=null;this.csrf=null;this.control=false;this.optionalVideo=false;this.render(this,"Enter a local one-use pairing code.");return;}
        throw Error(`Session unavailable (HTTP ${response.status}); reconnecting to current state.`);
      }
      const device=await response.json();
      if(this.detached || initialization!==this.initialization)return;
      this.csrf=device.csrf;this.device=device.device_id;this.control=device.control;this.optionalVideo=device.optional_p1_video;
      await this.attach();
    } catch(error) {
      if(this.detached || initialization!==this.initialization)return;
      this.connected=false;this.clear();this.render(this,`Session unavailable: ${error.message}`);this.reconnect(500);
    }
  }
  async attach() {
    if(this.detached)return;
    this.cancelReconnect();this.connected=false;this.clear();
    const epoch=this.epoch,initialization=this.initialization;
    try {
      const attached=await this.post("attach",{},epoch);
      if(this.detached || initialization!==this.initialization)return;
      if(attached.status==="reporting_error") {this.reconnect(500);return;}
      if(epoch!==this.epoch)return;
      this.snapshot=attached.snapshot;
      const ws=this.socket=new WebSocket(`${location.origin.replace("https:","wss:")}/api/ws`);
      ws.onopen=()=>ws.send(JSON.stringify({ticket:attached.ticket}));
      ws.onmessage=event=> {
        if(this.socket!==ws)return;
        const value=JSON.parse(event.data);
        if(value.kind==="snapshot") {
          this.reportingError=null;
          const previous=this.snapshot?.run,next=value.snapshot.run,ownRearm=this.expectedRearmSnapshot(value.snapshot),ownRelease=this.expectedElbowRelease(value.snapshot);
          const keepGesture=ownRearm?this.liveResume?.gesture:ownRelease&&this.elbowGesture?.release===this.elbowRelease&&this.rearmGestureCurrent(this.elbowGesture)?this.elbowGesture:null;
          if(this.liveResume&&!ownRearm&&!ownRelease)this.liveResume=null;
          if(previous?.run_id!==next?.run_id || previous?.recipe!==next?.recipe || previous?.intent_revision!==next?.intent_revision) {
            this.intent++;if(!ownRearm&&!ownRelease){this.elbowRelease=null;if(this.isLiveElbow())this.elbowStrict=true;}this.clearGesture(false,keepGesture);
            if(previous?.run_id!==next?.run_id)this.elbowReleasedRevision=null;
          }
          this.snapshot=value.snapshot;
          if(next?.first_cause||next?.native_first_cause||this.snapshot.evidence?.fault){this.clearGesture(true);this.liveResume=null;}
          if(value.revision) {ws.send(JSON.stringify({ack:value.revision}));this.connected=true;this.cancelReconnect();}
          const controller=this.snapshot.controller;
          if(this.controllerGeneration && (controller?.device_id!==this.device || controller.controller_generation!==this.controllerGeneration))
            this.clear("Controller generation changed");
          if(controller?.device_id!==this.device) {this.clearGesture();this.connection=null;}
          if(this.pendingRenewal&&!this.pendingRenewal.current())this.cancelRenewal("Controller context changed");
          this.qualifyClaim();
          if(this.isLiveElbow() && next?.status!=="running"){if(!ownRearm&&!ownRelease){this.elbowStrict=true;this.elbowRelease=null;}this.clearGesture(false,keepGesture);}
          this.render(this);
        } else if(value.kind==="result") {
          const pending=this.pending.get(value.id);if(pending){this.pending.delete(value.id);pending.resolve(value);}
          if(value.status==="reporting_error")this.reportingFailure(value);
        } else if(value.kind==="unavailable") {
          if(value.status==="reporting_error")this.reportingFailure(value);
          else {this.connected=false;this.clear("owner unavailable");this.render(this,"Owner unavailable; inspect current status after reconnect.");}
        }
      };
      ws.onclose=()=> {
        if(this.socket!==ws)return;
        this.connected=false;this.clear();this.render(this,"Disconnected; controls cleared. Reconnecting to current state.");
        this.reconnect(300);
      };
      ws.onerror=()=>ws.close();
    } catch(error) {if(this.detached || initialization!==this.initialization || epoch!==this.epoch && !error.reportingError)return;this.connected=false;this.render(this,`Session unavailable: ${error.message}`);this.reconnect(500);}
  }
  command(command) {
    if(!this.connected || this.socket?.readyState!==WebSocket.OPEN || this.pending.size>=8)return Promise.reject(Error("Current connected snapshot required"));
    const id=crypto.randomUUID(),epoch=this.epoch;
    return new Promise((resolve,reject)=> {
      const timer=setTimeout(()=>{this.pending.delete(id);reject(Error("Command outcome unknown; inspect status"));},1500);
      this.pending.set(id,{resolve:value=>{clearTimeout(timer);if(epoch===this.epoch)resolve(value);else reject(Error("Connection changed"));},reject:error=>{clearTimeout(timer);reject(error);}});
      this.socket.send(JSON.stringify({id,command}));
    });
  }
  identities() {
    return {run_id:this.snapshot?.run?.run_id, service_incarnation:this.snapshot?.service_incarnation,
      controller_generation:this.controllerGeneration, connection_generation:this.connection,
      ...(this.isLiveElbow() ? {host_incarnation:this.snapshot.run.backend_incarnation,intent_revision:this.snapshot.run.intent_revision} : {})};
  }
  cancelClaim(reason) {
    const pending=this.pendingClaim;if(!pending)return;
    this.pendingClaim=null;if(pending.timer)clearTimeout(pending.timer);
    pending.resolve(pending.result || {status:"superseded",reason});
  }
  claimCurrent(pending) {
    return Boolean(pending)&&this.pendingClaim===pending&&this.connected&&this.control&&pending.epoch===this.epoch&&pending.intent===this.intent
      &&pending.initialization===this.initialization&&pending.socket===this.socket&&pending.connection===this.connection&&pending.device===this.device
      &&pending.service===this.snapshot?.service_incarnation&&performance.now()<pending.until;
  }
  qualifyClaim() {
    const pending=this.pendingClaim;if(!pending)return;
    if(!this.claimCurrent(pending)) {
      this.cancelClaim("Claim context changed or expired");return;
    }
    const controller=this.snapshot?.controller,result=pending.result;
    if(controller && controller.device_id!==pending.device) {this.cancelClaim("Controller changed");return;}
    if(!result)return;
    if(typeof result.controller_generation!=="string" || !result.controller_generation
      || typeof result.presence_expires!=="number" || !Number.isFinite(result.presence_expires) || result.presence_expires<=0
      || controller && (controller.controller_generation!==result.controller_generation
        || typeof controller.presence_expires!=="number" || !Number.isFinite(controller.presence_expires))) {
      this.cancelClaim("Current controller proof differs");return;
    }
    if(Array.isArray(this.snapshot.events)&&this.snapshot.events.some(event=>event.kind==="presence_expired"
      &&event.service_incarnation===pending.service&&typeof event.at==="number"&&Number.isFinite(event.at)&&event.at>=result.presence_expires)) {
      this.cancelClaim("Controller presence expired");return;
    }
    if(!controller || controller.presence_expires<result.presence_expires)return;
    this.controllerGeneration=result.controller_generation;this.cancelClaim();
  }
  cancelRenewal(reason) {
    const pending=this.pendingRenewal;if(!pending)return;
    this.pendingRenewal=null;clearTimeout(pending.timer);pending.abort.abort();
    pending.resolve({status:"superseded",reason});
  }
  async claim(generation) {
    if(generation===undefined) {this.cancelClaim("Newer explicit Claim");generation=this.controllerGeneration || undefined;}
    const epoch=this.epoch,service=this.snapshot?.service_incarnation,socket=this.socket,connection=this.connection,initialization=this.initialization,device=this.device;
    if(generation!==undefined) {
      if(this.pendingRenewal) {
        if(this.pendingRenewal.current())return {status:"superseded",reason:"Controller renewal already pending"};
        this.cancelRenewal("Controller context changed");
      }
      const current=()=>!this.detached&&this.control&&epoch===this.epoch&&this.connected&&generation===this.controllerGeneration&&service===this.snapshot?.service_incarnation
        &&socket===this.socket&&connection===this.connection&&initialization===this.initialization&&device===this.device;
      if(!current())return {status:"superseded",reason:"Current controller required"};
      return new Promise(resolve=>{
        const pending=this.pendingRenewal={current,resolve,abort:new AbortController(),until:performance.now()+1000,timer:null};
        const fail=reason=>{
          if(this.pendingRenewal!==pending)return;
          if(!current()){this.cancelRenewal("Controller context changed");return;}
          this.clear(reason);this.render(this,"Controller renewal was not confirmed; use Claim input, then qualified Resume.");
        };
        pending.timer=setTimeout(()=>fail("Controller renewal timed out"),1000);
        this.post("claim",{controller_generation:generation},epoch,()=>this.pendingRenewal===pending&&current()&&performance.now()<pending.until,pending.abort.signal).then(result=>{
          if(this.pendingRenewal!==pending)return;
          if(!current()){this.cancelRenewal("Controller context changed");return;}
          if(performance.now()>=pending.until){fail("Controller renewal timed out");return;}
          if(!result.accepted || result.controller_generation!==generation){fail("Controller renewal refused");return;}
          this.pendingRenewal=null;clearTimeout(pending.timer);resolve(result);this.render(this,result.reason);
        },()=>fail("Controller renewal failed"));
      });
    }
    if(!this.connected || !this.control || !this.device || typeof service!=="string" || !service)return {status:"superseded",reason:"Current connected controller view required"};
    return new Promise((resolve,reject)=>{
      const pending=this.pendingClaim={epoch,service,intent:this.intent,initialization:this.initialization,socket:this.socket,connection:this.connection,device:this.device,until:performance.now()+1500,result:null,resolve};
      pending.timer=setTimeout(()=>{if(this.pendingClaim===pending){this.cancelClaim("Claim qualification expired");this.render(this,"Claim view did not qualify; use Claim input again.");}},1500);
      this.post("claim",{},epoch,()=>this.claimCurrent(pending)).then(result=>{
        if(this.pendingClaim!==pending)return;
        pending.result=result;
        if(!result.accepted)this.cancelClaim(result.reason);else this.qualifyClaim();
        this.render(this,this.pendingClaim===pending ? "Claim accepted; waiting for the current controller view." : result.reason);
      },error=>{if(this.pendingClaim===pending){this.pendingClaim=null;clearTimeout(pending.timer);reject(error);}});
    });
  }
  async renew() {
    const generation=this.controllerGeneration;
    if(generation) return this.claim(generation);
  }
  async start(recipe,confirmationOf=null,selectedExperiment=null) {
    if(recipe==="physical-live-teleop")throw Error("Start USB or virtual leader teleoperation with the authenticated PC adapter; this page supplies the selected live view.");
    const active=this.snapshot?.run;
    if(active?.source==="protected-physical-provider" && (!["stopped","completed","faulted","interrupted"].includes(active.status) || active.uncertain))
      throw Error("Active physical recipe is frozen; finish the current run before Start.");
    if(typeof recipe==="string" && recipe.startsWith("physical-live-") && !this.liveElbowSpec(recipe))throw Error("Current allowlisted live elbow selection required");
    const payload={operation_id:crypto.randomUUID(),recipe,controller_generation:this.controllerGeneration};
    if(recipe==="physical-left-elbow-experiment" || selectedExperiment!==null) {
      const keys=["schema_version","envelope_sha256","excursion_norm","ramp_s","endpoint_dwell_s","current_cap_ma","live_ceiling_s","minimum_response_raw","return_tolerance_raw","goal_readback_checkpoints_s"];
      const value=selectedExperiment,points=value?.goal_readback_checkpoints_s;
      if(value?.schema_version===2)keys.push("cycle_count","intercycle_hold_s");
      const negativeHold=value?.envelope_sha256==="6be735becc951f027f6c5886c1fd8f8f323118e8d6ff7931d46107e935a60c1c";
      if(negativeHold)keys.push("intercycle_hold_tolerance_raw");
      if(recipe!=="physical-left-elbow-experiment" || !value || typeof value!=="object" || Array.isArray(value)
        || Object.keys(value).length!==keys.length || keys.some(key=>!Object.hasOwn(value,key))
        || ![1,2].includes(value.schema_version) || value.schema_version===2 && (value.cycle_count!==2 || value.intercycle_hold_s!==6)
        || typeof value.envelope_sha256!=="string" || !/^[0-9a-f]{64}$/.test(value.envelope_sha256)
        || ["excursion_norm","ramp_s","endpoint_dwell_s","current_cap_ma","live_ceiling_s"].some(key=>typeof value[key]!=="number" || !Number.isFinite(value[key]))
        || ["minimum_response_raw","return_tolerance_raw"].some(key=>!Number.isInteger(value[key]) || value[key]<0 || value[key]>4095)
        || !Array.isArray(points) || points.length>2 || points.some((point,index)=>typeof point!=="number" || !Number.isFinite(point) || point<=0 || index>0 && point<=points[index-1]))throw Error("Exact selected experiment descriptor required");
      if(negativeHold) {
        const expected={schema_version:2,excursion_norm:-10,ramp_s:10,endpoint_dwell_s:2,current_cap_ma:150,
          live_ceiling_s:90,minimum_response_raw:56,return_tolerance_raw:22,cycle_count:2,intercycle_hold_s:6,
          intercycle_hold_tolerance_raw:40};
        if(Object.entries(expected).some(([key,wanted])=>value[key]!==wanted) || JSON.stringify(points)!=="[5,37]")throw Error("Exact selected experiment descriptor required");
      }
      const frozen=JSON.parse(JSON.stringify(value));
      Object.freeze(frozen.goal_readback_checkpoints_s);payload.selected_experiment=Object.freeze(frozen);
    }
    if(confirmationOf!==null) {
      if(recipe!=="physical-left-elbow-positive-control" || typeof confirmationOf!=="string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(confirmationOf))throw Error("Canonical positive-control confirmation UUID required");
      payload.confirmation_of=confirmationOf;
    }
    Object.freeze(payload);
    // Outcome-loss retry uses the same immutable operation identity, never a new Start.
    let result;
    try {result=await this.post("start",payload);} catch(error) {if(error.reportingError)throw error;result=await this.post("start",payload);}
    this.render(this,result.reason);return result;
  }
  async startPreset(id) {
    const versions={"left-elbow-coarse-bench-v1":1,"left-elbow-coarse-bench-v2":2,"left-elbow-negative-coarse-bench-v2":2,"left-elbow-negative-coarse-bench-v3":3};
    const version=typeof id==="string" && Object.hasOwn(versions,id) ? versions[id] : null;
    const preset=this.snapshot?.presets?.find(value=>value.id===id);
    if(version===null || !preset || preset.version!==version || preset.recipe!=="physical-left-elbow-experiment")throw Error("Current named preset required");
    if(id==="left-elbow-negative-coarse-bench-v2" || id==="left-elbow-negative-coarse-bench-v3") {
      const negativeHold=id==="left-elbow-negative-coarse-bench-v3";
      const value=preset.selected_experiment,expected={schema_version:2,
        envelope_sha256:negativeHold ? "6be735becc951f027f6c5886c1fd8f8f323118e8d6ff7931d46107e935a60c1c" : "ea8ed4e0abd5222734cd77cffc22c50739d53ab4cbde999016b33606af7648f5",
        excursion_norm:-10,ramp_s:10,endpoint_dwell_s:2,current_cap_ma:150,live_ceiling_s:90,minimum_response_raw:56,return_tolerance_raw:22,cycle_count:2,intercycle_hold_s:6};
      if(negativeHold)expected.intercycle_hold_tolerance_raw=40;
      const sha256=negativeHold ? "c73ef75706fcce9b1772b0927071e5ce41dc6c382cd60760c7033add48d1c709" : "86c16ecbcfcb864b0ddb9caaff25d306a9c4d991730f853d6e09e91b50bbdd1c";
      if(preset.selected_experiment_sha256!==sha256 || !value || typeof value!=="object" || Array.isArray(value)
        || Object.keys(value).length!==Object.keys(expected).length+1 || Object.entries(expected).some(([key,wanted])=>!Object.hasOwn(value,key) || value[key]!==wanted)
        || !Object.hasOwn(value,"goal_readback_checkpoints_s") || !Array.isArray(value.goal_readback_checkpoints_s) || JSON.stringify(value.goal_readback_checkpoints_s)!=="[5,37]")throw Error("Exact negative named preset required");
    }
    return this.start(preset.recipe,null,preset.selected_experiment);
  }
  async readDetail(reference,{raw=false}={}) {
    const keys=["kind","version","service_incarnation","run_id","operation_id","host_incarnation","field","sha256","bytes"];
    if(!reference || typeof reference!=="object" || Array.isArray(reference) || Object.keys(reference).length!==keys.length
      || keys.some(key=>!Object.hasOwn(reference,key)) || reference.kind!=="am1-status-field-reference" || reference.version!==1
      || !["run","evidence","events"].includes(reference.field) || typeof reference.sha256!=="string" || !/^[0-9a-f]{64}$/.test(reference.sha256)
      || !Number.isInteger(reference.bytes) || reference.bytes<1 || reference.bytes>8*1024*1024
      || reference.run_id!==this.snapshot?.run?.run_id
      || this.snapshot?.run?.operation_id && reference.operation_id!==this.snapshot.run.operation_id)throw Error("Current bounded details reference required");
    const frozen=JSON.parse(JSON.stringify(reference)),serialized=JSON.stringify(frozen),bytes=new Uint8Array(frozen.bytes);
    for(let offset=0;offset<bytes.length;) {
      const response=await fetch(`/api/state?reference=${encodeURIComponent(serialized)}&offset=${offset}&limit=8192`);
      const value=await response.json();
      if(!response.ok || value.accepted!==true || value.status!=="detail")throw Error(value.reason || "Native details unavailable");
      if(reference.run_id!==this.snapshot?.run?.run_id || !value.reference || Object.keys(value.reference).length!==keys.length
        || keys.some(key=>value.reference[key]!==frozen[key])
        || value.offset!==offset || value.total_bytes!==bytes.length || value.encoding!=="base64"
        || !Number.isInteger(value.length) || value.length<1 || value.length>8192 || offset+value.length>bytes.length
        || typeof value.data!=="string")throw Error("Native details identity or chunk differs");
      const chunk=atob(value.data);
      if(chunk.length!==value.length)throw Error("Native details chunk length differs");
      for(let i=0;i<chunk.length;i++)bytes[offset+i]=chunk.charCodeAt(i);
      offset+=chunk.length;
    }
    const digest=Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",bytes)),b=>b.toString(16).padStart(2,"0")).join("");
    if(digest!==frozen.sha256)throw Error("Native details hash differs");
    const text=new TextDecoder("utf-8",{fatal:true}).decode(bytes);
    return raw ? text : JSON.parse(text);
  }
  async protective(op) {
    this.intent++; this.clearGesture(true);
    const result=await this.post(op,{run_id:this.snapshot?.run?.run_id,operation_id:crypto.randomUUID()});
    this.render(this,result.reason);return result;
  }
  async resume() {
    if(this.isLiveElbow())return this.resumeLiveElbow();
    this.clearGesture(true);
    const epoch=this.epoch,intent=++this.intent,generation=this.controllerGeneration,revision=this.snapshot?.run?.intent_revision;
    const current=()=>this.connected && epoch===this.epoch && intent===this.intent && generation===this.controllerGeneration;
    const connected=await this.command({op:"connect",run_id:this.snapshot?.run?.run_id});
    if(!current())return connected; // Preserve the admitted result; cancel only its continuation.
    if(!connected.accepted){this.render(this,connected.reason);return connected;}
    this.connection=connected.connection_generation;this.snapshot=connected.snapshot;this.seq=0;
    const released=await this.command({op:"release_input",...this.identities()});
    if(!current() || !released.accepted)return released;
    const result=await this.command({op:"resume",...this.identities(),expected_intent_revision:revision,operation_id:crypto.randomUUID()});
    if(current())this.render(this,result.reason);return result;
  }
  async resumeLiveElbow(gesture=null) {
    const automatic=gesture!==null,origin=automatic?this.elbowRelease?.revision:null;
    if(automatic&&(!this.rearmGestureCurrent(gesture)||!this.rearmReady()))return {accepted:false,status:"superseded"};
    this.clearGesture(!automatic,gesture);this.intent++;
    const epoch=this.epoch,ids=Object.freeze(this.identities()),recipe=this.snapshot.run.recipe,initial=this.snapshot.run.intent_revision,revision=initial+1;
    const bounds=this.elbowBounds(),token=this.liveResume={gesture,ids,recipe,initial,phase:"connect",origin,bounds};
    const current=connected=>this.liveResume===token && (!automatic||this.rearmGestureCurrent(gesture)) && this.connected && epoch===this.epoch
      && this.controllerGeneration===ids.controller_generation && this.snapshot?.service_incarnation===ids.service_incarnation
      && this.snapshot?.run?.run_id===ids.run_id && this.snapshot.run.recipe===recipe && this.snapshot.run.backend_incarnation===ids.host_incarnation && this.sameElbowBounds(this.snapshot,bounds)
      && (connected ? this.connection===token.connection && this.snapshot.run.intent_revision===revision && this.snapshot.run.status==="paused"
        : [initial,revision].includes(this.snapshot.run.intent_revision) && ["running","paused","resuming"].includes(this.snapshot.run.status));
    try {
      const connected=await this.command({op:"connect",...ids,...(automatic?{rearm_of_intent_revision:origin}:{})});
      if(!current(false))return connected;
      if(!connected.accepted){this.render(this,connected.reason);return connected;}
      const view=connected.snapshot,run=view?.run;
      if(typeof connected.connection_generation!=="string" || !connected.connection_generation
        || view?.service_incarnation!==ids.service_incarnation || run?.run_id!==ids.run_id || run.backend_incarnation!==ids.host_incarnation
        || run.intent_revision!==revision || run.status!=="paused" || !this.sameElbowBounds(view,bounds) || view.controller?.device_id!==this.device
        || view.controller.controller_generation!==ids.controller_generation)throw Error("Physical connection outcome differs; inspect current status");
      this.connection=connected.connection_generation;token.connection=this.connection;token.phase="qualify";this.seq=0;
      if(this.snapshot.run.intent_revision<revision)this.snapshot=view;
      const released=await this.command({op:"release_input",...this.identities(),...(automatic?{rearm_of_intent_revision:origin}:{})});
      if(!current(true) || !released.accepted)return released;
      const qualified=()=>{
        const evidence=this.snapshot.evidence,value=this.snapshot.run.live_input;
        return ["feedback","required_observation","hold_acknowledged","pose_aligned","native_ack"].every(key=>evidence?.[key]===true)
          && evidence.acknowledged_intent_revision===revision && value?.cursor_intent_revision===revision
          && Number.isInteger(value.cursor_feedback_sequence) && value.cursor_feedback_sequence>=0;
      };
      const until=automatic?gesture.until:performance.now()+3000;
      while(current(true) && !qualified() && performance.now()<until)await new Promise(resolve=>setTimeout(resolve,50));
      if(!current(true))return released;
      if(!qualified()) {
        const result={accepted:false,status:"refused",reason:"Fresh measured hold qualification pending; inspect status and select Resume again."};
        this.render(this,result.reason);return result;
      }
      // Freeze this connection's revision. Any later Pause/Stop is refused by Core even if its snapshot is delayed.
      token.phase="resume";
      const result=await this.command({op:"resume",...this.identities(),expected_intent_revision:revision,operation_id:crypto.randomUUID(),...(automatic?{rearm_of_intent_revision:origin}:{})});
      const resumed=()=>this.liveResume===token&&this.connected&&epoch===this.epoch&&this.controllerGeneration===ids.controller_generation
        &&this.connection===token.connection&&this.snapshot?.service_incarnation===ids.service_incarnation&&this.snapshot?.run?.run_id===ids.run_id
        &&this.snapshot.run.recipe===recipe&&this.snapshot.run.backend_incarnation===ids.host_incarnation
        &&this.snapshot.run.intent_revision===initial+2&&this.snapshot.run.status==="running"&&this.sameElbowBounds(this.snapshot,bounds);
      if(result.accepted){
        const active=()=>resumed()&&["feedback","required_observation","native_ack"].every(key=>this.snapshot.evidence?.[key]===true)
          &&this.snapshot.evidence.acknowledged_intent_revision===initial+2&&this.snapshot.run.live_input?.cursor_intent_revision===initial+2;
        while((!automatic||this.rearmGestureCurrent(gesture))&&this.liveResume===token&&!active()&&performance.now()<until)await new Promise(resolve=>setTimeout(resolve,50));
        if(active()&&performance.now()<until&&(!automatic||this.rearmGestureCurrent(gesture))){
          this.elbowStrict=false;
          if(automatic){this.elbowRelease=null;gesture.phase="active";clearTimeout(gesture.timer);this.setElbowJog(gesture.direction);}
        }
      }
      if(this.liveResume===token && epoch===this.epoch)this.render(this,result.reason);return result;
    } finally {if(this.liveResume===token)this.liveResume=null;}
  }
  isLiveElbow() {return Object.hasOwn(AM1SessionTransport.liveElbows,this.snapshot?.run?.recipe) && this.snapshot.run.source==="protected-physical-provider";}
  clearGesture(block=false,keepGesture=null) {
    if(this.elbowGesture!==keepGesture){if(this.elbowGesture)clearTimeout(this.elbowGesture.timer);this.elbowGesture=null;}
    if(block){if(this.isLiveElbow())this.elbowStrict=true;this.elbowRelease=null;}
    this.held.clear();this.elbowJog=0;this.elbowCursor=null;this.elbowBinding=null;this.elbowAt=null;
    if(block)this.liveResume=null;
    if(block && this.isLiveElbow())this.elbowReleasedRevision=this.snapshot.run.intent_revision;
  }
  elbowInput() {
    const run=this.snapshot?.run,value=run?.live_input,spec=this.liveElbowSpec();
    if(this.elbowStrict || !spec || !this.isLiveElbow() || !this.connected || !this.control || !this.controllerGeneration || !this.connection || run.status!=="running"
      || !Number.isInteger(run.intent_revision) || value?.joint!==spec.joint || value.side!==spec.side || value.profile!==spec.profile || value.rate_norm_per_s!==spec.rate_norm_per_s || value.cursor_intent_revision!==run.intent_revision
      || !Number.isInteger(value.cursor_feedback_sequence) || value.cursor_feedback_sequence<0
      || [value.cursor_norm,value.reference_norm,value.corridor_min_norm,value.corridor_max_norm].some(x=>typeof x!=="number" || !Number.isFinite(x))
      || value.corridor_min_norm>=value.corridor_max_norm || typeof run.backend_incarnation!=="string"
      || this.elbowReleasedRevision!==null && run.intent_revision<=this.elbowReleasedRevision)return null;
    const preset=Object.hasOwn(AM1SessionTransport.practicalElbows,run.recipe) ? AM1SessionTransport.practicalElbows[run.recipe] : null;
    if(preset && (value.admission_version!==2 || value.corridor_min_raw!==preset.minimum_raw || value.corridor_max_raw!==preset.maximum_raw
      ||value.corridor_min_norm!==preset.minimum_norm||value.corridor_max_norm!==preset.maximum_norm
      ||!value.workspace||Object.keys(value.workspace).length!==5||value.workspace.version!==1
      ||value.workspace.id!==preset.id||value.workspace.context_sha256!==preset.context_sha256
      ||value.workspace.evidence_min_raw!==preset.minimum_raw||value.workspace.evidence_max_raw!==preset.maximum_raw))return null;
    return value;
  }
  elbowBounds(snapshot=this.snapshot) {
    const run=snapshot?.run,v=run?.live_input,w=v?.workspace;
    return Object.freeze({joint:v?.joint,reference_norm:v?.reference_norm,corridor_min_norm:v?.corridor_min_norm,corridor_max_norm:v?.corridor_max_norm,
      deadline:run?.deadline,native_live_at:run?.native_live_at,admission_version:v?.admission_version,workspace_id:w?.id,workspace_context_sha256:w?.context_sha256,
      workspace_min_raw:w?.evidence_min_raw,workspace_max_raw:w?.evidence_max_raw,corridor_min_raw:v?.corridor_min_raw,corridor_max_raw:v?.corridor_max_raw,measured_origin_raw:v?.measured_origin_raw});
  }
  sameElbowBounds(snapshot,bounds) {const current=this.elbowBounds(snapshot);return Object.entries(bounds).every(([key,value])=>current[key]===value);}
  sameElbowContext(ids,recipe,connection=ids.connection_generation) {
    const run=this.snapshot?.run;return this.connected&&this.control&&this.controllerGeneration===ids.controller_generation&&this.connection===connection
      &&this.snapshot?.service_incarnation===ids.service_incarnation&&run?.run_id===ids.run_id&&run.recipe===recipe&&run.backend_incarnation===ids.host_incarnation
      &&this.snapshot.controller?.device_id===this.device&&this.snapshot.controller.controller_generation===this.controllerGeneration;
  }
  expectedElbowRelease(snapshot) {
    const r=this.elbowRelease,run=snapshot?.run,value=run?.live_input?.rearm;
    return Boolean(r&&r.epoch===this.epoch&&this.sameElbowContext(r.ids,r.recipe)&&run?.run_id===r.ids.run_id&&run.recipe===r.recipe
      &&snapshot.service_incarnation===r.ids.service_incarnation&&run.backend_incarnation===r.ids.host_incarnation&&run.status==="paused"&&run.intent_revision===r.revision
      &&value?.eligible===true&&value.released_intent_revision===r.revision&&this.sameElbowBounds(snapshot,r.bounds));
  }
  expectedRearmSnapshot(snapshot) {
    const t=this.liveResume,run=snapshot?.run;if(!t||!run||!this.sameElbowContext(t.ids,t.recipe,t.connection||t.ids.connection_generation)
      ||snapshot.service_incarnation!==t.ids.service_incarnation||run.run_id!==t.ids.run_id||run.recipe!==t.recipe||run.backend_incarnation!==t.ids.host_incarnation
      ||snapshot.controller?.device_id!==this.device||snapshot.controller.controller_generation!==t.ids.controller_generation||!this.sameElbowBounds(snapshot,t.bounds))return false;
    if(t.gesture&&!this.rearmGestureCurrent(t.gesture))return false;
    if(!t.gesture&&t.phase==="connect"&&run.intent_revision===t.initial&&["running","paused","resuming"].includes(run.status))return true;
    if(run.intent_revision===t.initial+1&&run.status==="paused")return !t.gesture||run.live_input?.rearm?.eligible===true&&run.live_input.rearm.released_intent_revision===t.origin;
    return t.phase==="resume"&&run.intent_revision===t.initial+2&&["running","resuming"].includes(run.status);
  }
  rearmReady() {return this.elbowRelease?.accepted===true&&this.expectedElbowRelease(this.snapshot)&&!this.elbowStrict;}
  rearmGestureCurrent(g) {return this.elbowGesture===g&&g.held&&g.phase==="pending"&&!this.elbowStrict&&g.epoch===this.epoch&&performance.now()<g.until;}
  canRearmElbow() {
    const g=this.elbowGesture,r=this.elbowRelease;if(g?.phase==="pending")return this.rearmGestureCurrent(g);
    return Boolean(r&&!this.elbowStrict&&r.epoch===this.epoch&&this.sameElbowContext(r.ids,r.recipe)
      &&(this.expectedElbowRelease(this.snapshot)||this.snapshot.run.status==="running"&&this.snapshot.run.intent_revision===r.ids.intent_revision));
  }
  pressElbowJog(direction,source) {
    if(![-1,1].includes(direction)||typeof source!=="string"||this.elbowGesture?.source===source&&this.elbowGesture.held)return false;
    const active=Boolean(this.elbowInput());if(!active&&!this.canRearmElbow())return false;
    if(this.elbowGesture)clearTimeout(this.elbowGesture.timer);this.intent++;
    const gesture=this.elbowGesture={id:crypto.randomUUID(),source,direction,held:true,epoch:this.epoch,phase:active?"active":"pending",release:active?null:this.elbowRelease,until:performance.now()+3000};
    if(active)return this.setElbowJog(direction);
    gesture.timer=setTimeout(()=>{if(this.elbowGesture===gesture&&gesture.phase==="pending"){this.releaseInput();this.render(this,"Rearm timed out; release controls and select Resume.");}},3000);
    gesture.promise=(async()=>{
      const release=this.elbowRelease;await release?.promise;
      while(this.rearmGestureCurrent(gesture)&&!this.rearmReady())await new Promise(resolve=>setTimeout(resolve,50));
      if(this.rearmGestureCurrent(gesture)&&this.rearmReady())await this.resumeLiveElbow(gesture);
      if(this.elbowGesture===gesture&&gesture.phase==="pending"){await this.releaseInput();this.render(this,"Rearm did not qualify; release controls and select Resume.");}
    })().catch(()=>{if(this.elbowGesture===gesture)this.releaseInput();});this.render(this);return true;
  }
  releaseElbowJog(source,voluntary=false) {
    const gesture=this.elbowGesture;if(!gesture||gesture.source!==source)return Promise.resolve();
    const ordinary=voluntary&&gesture.phase==="active"&&Boolean(this.elbowInput());gesture.held=false;
    return this.releaseInput(ordinary?"voluntary_jog":undefined);
  }
  setElbowJog(direction) {
    const value=this.elbowInput();if(!value || ![-1,1].includes(direction))return false;
    const binding=JSON.stringify([this.identities(),this.snapshot.run.recipe,value.joint]);
    if(this.elbowBinding!==binding || !this.elbowJog) {
      this.elbowCursor=value.cursor_norm;this.elbowBinding=binding;this.elbowAt=performance.now();
    }
    this.elbowJog=direction;return true;
  }
  async liveInput() {
    if(this.inputBusy || !this.elbowJog || !this.elbowInput() || this.liveInputs.size>=1 || this.pending.size>=6
      || this.socket?.readyState!==WebSocket.OPEN || this.socket.bufferedAmount>0)return;
    this.inputBusy=true;
    const epoch=this.epoch,intent=this.intent,ids=Object.freeze(this.identities()),recipe=this.snapshot.run.recipe,joint=this.elbowInput().joint;
    const current=()=>epoch===this.epoch && intent===this.intent && this.connected && this.elbowJog && this.elbowInput()
      && this.snapshot.run.recipe===recipe && this.elbowInput().joint===joint && JSON.stringify(this.identities())===JSON.stringify(ids);
    try {
      const grant=await this.command({op:"grant",...ids});
      if(!current())return;
      if(!grant.accepted){this.clearGesture(true);return;}
      if(this.socket.bufferedAmount>0 || this.liveInputs.size>=1)return;
      // Pi times remain opaque. Only Core/native decide whether this original grant is still valid.
      // Sample current intent after issuance; skipped/gapped ticks never accumulate displacement.
      const value=this.elbowInput(),now=performance.now(),dt=Math.max(0,Math.min((now-this.elbowAt)/1000,0.1));
      this.elbowAt=now;
      this.elbowCursor=Math.max(value.corridor_min_norm,Math.min(value.corridor_max_norm,this.elbowCursor+this.elbowJog*dt*value.rate_norm_per_s));
      const seq=++this.seq,token={seq};this.liveInputs.add(token);
      this.command({op:"input",...ids,grant_id:grant.grant_id,seq,target:{[joint]:this.elbowCursor}})
        .then(result=>{if(current() && !this.inputBusy && seq===this.seq && !result.accepted && result.status!=="superseded")this.clearGesture(true);})
        .catch(()=>{if(current() && !this.inputBusy && seq===this.seq)this.clearGesture(true);})
        .finally(()=>this.liveInputs.delete(token));
      this.render(this);
    } catch {if(current())this.clearGesture(true);} finally {if(epoch===this.epoch)this.inputBusy=false;}
  }
  async releaseInput(kind) {
    const ordinary=kind==="voluntary_jog"&&this.isLiveElbow()&&Boolean(this.elbowInput()),ids=Object.freeze(this.identities());
    const release=ordinary?{ids,epoch:this.epoch,recipe:this.snapshot.run.recipe,revision:ids.intent_revision+1,accepted:false,bounds:this.elbowBounds()}:null;
    this.intent++;this.clearGesture(!ordinary);this.liveResume=null;
    if(ordinary){this.elbowReleasedRevision=ids.intent_revision;this.elbowRelease=release;this.elbowStrict=false;}this.render(this);
    if(this.connected && this.connection && (this.snapshot?.run?.recipe==="fake-interactive" || this.isLiveElbow())){
      const pending=this.command({op:"release_input",...ids,...(ordinary?{release_kind:"voluntary_jog"}:{})}).catch(()=>({accepted:false}));
      if(release)release.promise=pending;const result=await pending;
      if(release&&this.elbowRelease===release){release.accepted=result.accepted===true;if(!release.accepted){this.elbowStrict=true;this.elbowRelease=null;}}
      return result;
    }
  }
  async input() {
    if(this.isLiveElbow())return this.liveInput();
    if(this.inputBusy || !this.held.size || !this.connection || this.snapshot?.run?.status!=="running" || this.snapshot.run.recipe!=="fake-interactive")return;
    this.inputBusy=true;const epoch=this.epoch,intent=this.intent;
    try {
      const grant=await this.command({op:"grant",...this.identities()});
      if(epoch!==this.epoch || intent!==this.intent || !grant.accepted || !this.held.size)return;
      // Sample current keys after issuance; no target queue or reconnect replay.
      const target=[Number(this.held.has("w"))-Number(this.held.has("s")),Number(this.held.has("a"))-Number(this.held.has("d")),Number(this.held.has("u"))-Number(this.held.has("j"))];
      const result=await this.command({op:"input",...this.identities(),grant_id:grant.grant_id,seq:++this.seq,target});
      if(!result.accepted)this.held.clear();
    } catch {this.held.clear();} finally {if(epoch===this.epoch)this.inputBusy=false;}
  }
}
globalThis.AM1SessionTransport=AM1SessionTransport;
globalThis.AM1RemoteInitialize=function() {
  const state=document.querySelector("#session-state"),notice=document.querySelector("#control-notice");
  const defaultControlHelp="Explicitly claim input before Start. Finite authorization survives browser closure. Interactive work needs a fresh connected snapshot, empty release and qualified Resume. Disconnect, blur and handoff clear held keys. Pause and Stop remain available to enrolled control-capable spectators. Motor feedback and commands remain simulated. When configured, real P1 images qualify observation on Pi; optional browser video has no control authority.";
  const render=(transport,message)=> {
    const run=transport.snapshot?.run;
    const recipes=transport.snapshot?.recipes,presets=transport.snapshot?.presets || [], select=document.querySelector("#fake-recipe");
    const selection=JSON.stringify([recipes,presets,transport.snapshot?.live_elbow_recipes]);
    if(recipes && select.dataset.recipes!==selection) {
      const previous=select.value;
      const ordinary=recipes.filter(name=>name!=="fake-finite-short" && !presets.some(preset=>preset.recipe===name)).map(name=> {
        const option=document.createElement("option"); option.value=name; option.textContent=name==="physical-live-teleop" ? "USB or virtual leaders · PC adapter" : transport.liveElbowSpec(name)?.label || name; return option;
      });
      const named=presets.map(preset=>{const option=document.createElement("option");option.value=preset.id;option.textContent=preset.label;return option;});
      select.replaceChildren(...ordinary,...named);
      if([...select.options].some(option=>option.value===previous))select.value=previous;
      select.dataset.recipes=selection;
    }
    const selectionFrozen=run?.source==="protected-physical-provider" && (!["stopped","completed","faulted","interrupted"].includes(run.status) || run.uncertain);
    select.disabled=Boolean(selectionFrozen);
    if(selectionFrozen && [...select.options].some(option=>option.value===run.recipe))select.value=run.recipe;
    const physicalRun=run?.source==="protected-physical-provider" || run?.recipe?.startsWith("physical-");
    const physical=physicalRun || recipes?.some(name=>name.startsWith("physical-"));
    const liveElbow=transport.isLiveElbow(),selectedElbow=transport.liveElbowSpec(liveElbow && selectionFrozen ? run.recipe : select.value);
    const liveBudget=Number.isFinite(run?.deadline)&&Number.isFinite(run?.native_live_at)&&run.deadline>run.native_live_at ? Math.round(run.deadline-run.native_live_at) : selectedElbow?.live_s || 90;
    const simulated=run?.source==="simulated-provider" || recipes?.some(name=>name.startsWith("sim-"));
    const priorTest=physical && run && !physicalRun, priorKind=simulated ? "simulated" : "synthetic";
    const teleop=run?.recipe==="physical-live-teleop" || select.value==="physical-live-teleop";
    document.querySelector("#view-heading").textContent=physical ? teleop ? "AM1 leader teleoperation" : liveElbow ? "Physical live elbow AM1 session" : "Physical finite AM1 session" : simulated ? "Simulated finite provider session" : "Fake persistent session";
    state.textContent=run ? `${priorTest ? `Prior ${priorKind}` : physical ? "Physical" : "Fake"} session · ${run.status} · ${run.progress_s.toFixed(2)} s` : `${physical ? "Physical" : "Fake"} session · no run`;
    document.querySelector("#session-details").textContent=JSON.stringify(transport.snapshot,null,2);
    const terminal=run && ["stopped","completed","faulted","interrupted"].includes(run.status);
    document.querySelector("#live-countdown").textContent=priorTest ? `Prior ${priorKind} result · cleanup: ${run.cleanup || "unknown"}; physical Start required` :
      physical && run?.status==="finishing" ? "Physical finishing · measured cleanup pending; native Live countdown pending" :
      physical && terminal ? `${AM1SessionTransport.outcomeText(run) || "Physical session ended"} · cleanup: ${run.cleanup || "unknown"}` :
      physical && run && !Number.isFinite(run.native_live_at) ? "Physical startup · native Live countdown pending" :
      run ? `Original Live budget: ${Math.ceil(run.remaining_s || 0)} s remaining` : physical ? "No physical run · Start required" : "No fake run";
    const evidence=transport.snapshot?.evidence;
    const observationLabel={"real-p1/pi-decoded":"Required P1 observation", "real-local-camera/pi-decoded-arrival":`Required local ${evidence?.sensing_source?.role || "camera"} observation`}[evidence?.observation_provenance];
    const observationStatus=observationLabel ? `${observationLabel} · ${evidence?.required_observation ? "current images validated on Pi" : "current image proof unavailable"}` : "Task observation policy shown in current status";
    document.querySelector("#connection").textContent=`${transport.connected ? "Connected" : "Disconnected"} · ${physical ? `${observationStatus} · actual motor feedback ${evidence?.feedback ? "current" : "unavailable"} · protected-host ACK ${evidence?.native_ack ? "current" : "pending"}` : observationLabel ? `${observationStatus} · motor feedback and commands simulated` : "Cameras and hardware unavailable in fake mode"}`;
    if(transport.optionalVideo&&!transport.videoView&&globalThis.AM1P1View) {
      let root=document.querySelector("#am1-camera-root");
      if(root.dataset.onboard==="true") {
        const advisory=document.createElement("section");advisory.id="p1-advisory-root";
        root.parentElement.append(advisory);root=advisory;
      } else root.replaceChildren();
      const label=document.createElement("p");label.textContent="Optional external P1 view · separate from required Pi image validation";
      const video=document.createElement("video");video.muted=true;video.autoplay=true;video.playsInline=true;video.controls=true;video.style.maxWidth="640px";video.style.width="100%";
      const status=document.createElement("p");status.textContent="View closed · task and finite capture continue independently";
      const open=document.createElement("button");open.type="button";open.textContent="Connect P1 view";
      const close=document.createElement("button");close.type="button";close.textContent="Close view";
      const view=transport.videoView=new AM1P1View(video,()=>transport.csrf,message=>status.textContent=message);
      open.onclick=()=>view.start();close.onclick=()=>{view.stop();status.textContent="View closed · task observation continues independently";};
      window.addEventListener("pagehide",()=>view.stop());root.append(label,video,status,open,close);
    }
    document.querySelector("#gate-state").textContent=transport.controllerGeneration ? physical ? "Explicit controller; physical Start activates the protected host. Request acceptance is separate from protected-host ACK and measured motion." : "Explicit controller; accepted requests require actual fake acknowledgement." : "Spectator; viewing does not claim input.";
    document.querySelector("#control-help").textContent=liveElbow ? `Claim input, explicitly Start, then qualify Resume. Hold an elbow jog button or [ / ] to stream current mapped targets. Ordinary release holds position; a new press requests qualified rearm. Blur, expiry or reconnection requires explicit Resume and new input. The original ${liveBudget}-second Live budget continues through holds and reconnects. Other joints remain held; body and lift stay zero. Input acceptance, protected-host ACK and measured movement are separate.` : physical ? "Explicitly claim input before physical Start. Start activates the selected bounded recipe through the protected motor host. Viewing, enrollment and reconnection do not start motion. Finite authorization survives browser closure. Pause and Stop remain available to enrolled control-capable spectators. Actual feedback and protected-host ACK are reported separately; an accepted command does not prove joint motion. Body inputs belong to the selected finite recipe. Required camera evidence is validated on Pi; optional P1 viewing is advisory." : defaultControlHelp;
    if(!transport.videoView&&document.querySelector("#am1-camera-root").dataset.onboard!=="true")document.querySelector("#am1-camera-root").textContent=physical ? "Task camera evidence is validated on Pi; optional P1 viewing is advisory." : "Cameras unavailable · synthetic fake evidence only";
    for(const button of document.querySelectorAll("[data-operation], [data-body-key]")) {
      button.title=physical ? liveElbow ? "Physical live elbow session control" : "Physical finite session control" : "Fake session control";
      if(button.dataset.operation)button.setAttribute("aria-label",`${button.dataset.operation} ${physical ? "physical" : "fake"} session`);
      if(button.dataset.bodyKey) {
        const unavailable=button.dataset.bodyKey==="z" || button.dataset.bodyKey==="x";
        button.disabled=physical || unavailable;
        if(physical) {
          button.title=liveElbow ? "Body and lift are unavailable in live elbow mode" : "Body inputs belong to the selected finite physical recipe";
          button.setAttribute("aria-label",`${button.dataset.bodyKey.toUpperCase()} ${liveElbow ? "unavailable in live elbow mode" : "provided by selected physical recipe"}`);
          if(unavailable)button.querySelector("span").textContent=liveElbow ? "Unavailable in live elbow mode" : "Unavailable in finite physical recipes";
        } else if(unavailable) {
          button.title="Unavailable in fake mode";
          button.setAttribute("aria-label",`${button.dataset.bodyKey.toUpperCase()} unavailable in fake mode`);
          button.querySelector("span").textContent="Unavailable in fake mode";
        }
      }
    }
    const elbow=document.querySelector("#live-elbow-controls");
    if(elbow) {
      elbow.hidden=!liveElbow && !selectedElbow;
      elbow.querySelector("h2").textContent=selectedElbow ? "Live "+selectedElbow.label.toLowerCase() : "Live elbow";
      for(const button of elbow.querySelectorAll("[data-elbow-jog]")) {
        button.disabled=!transport.elbowInput()&&!transport.canRearmElbow();
        const positive=button.dataset.elbowJog==="1",label=selectedElbow?.label || "Elbow";
        button.innerHTML=label+" "+(positive ? "+" : "-")+" <kbd>"+(positive ? "]" : "[")+"</kbd>";
        button.setAttribute("aria-label",label+" "+(positive ? "positive" : "negative")+" jog");
      }
      const measured=transport.snapshot?.evidence?.measured_positions?.[selectedElbow?.joint],cursor=run?.live_input?.cursor_norm;
      const workspace=transport.supportedWorkspace(selectedElbow),actualRate=selectionFrozen ? run?.live_input?.rate_norm_per_s : selectedElbow?.rate_norm_per_s;
      const rate=Number.isFinite(actualRate) ? actualRate.toFixed(3)+" normalized/s" : "pending";
      elbow.querySelector("output").textContent=!selectionFrozen&&selectedElbow ? workspace ? `Workspace ${workspace.id} · raw ${workspace.command_min_raw} to ${workspace.command_max_raw} · span ${workspace.span_norm.toFixed(3)} normalized · rate ${rate} · current admission pending` : `${selectedElbow.label} · rate ${rate} · current admission pending` : `Measured ${Number.isFinite(measured) ? measured.toFixed(3) : "pending"} · target ${Number.isFinite(transport.elbowCursor) ? transport.elbowCursor.toFixed(3) : Number.isFinite(cursor) ? cursor.toFixed(3) : "pending"} normalized · admitted ${Number.isFinite(run?.live_input?.corridor_min_norm) ? run.live_input.corridor_min_norm.toFixed(3) : "pending"} to ${Number.isFinite(run?.live_input?.corridor_max_norm) ? run.live_input.corridor_max_norm.toFixed(3) : "pending"} · rate ${rate} · run q0 ${Number.isFinite(run?.live_input?.reference_norm) ? run.live_input.reference_norm.toFixed(3) : "pending"}`;
    }
    if(teleop)document.querySelector("#control-help").textContent="The authenticated PC adapter owns USB or virtual leader input and the original finite deadline. This page supplies the selected required onboard view. Lost decoded view progress holds steering; fresh video permits the existing qualified Resume. Camera selection, opening and closing this page do not Claim or Start input. Pause and Stop remain available.";
    if(message){notice.textContent=message;notice.hidden=false;}
    document.querySelector("#pairing-panel").hidden=Boolean(transport.device);
    for(const button of document.querySelectorAll("[data-operation]"))button.disabled=!transport.control || !transport.connected || button.dataset.operation==="Start" && selectionFrozen;
  };
  document.querySelector("#view-heading").textContent="Fake persistent session";
  const panel=document.createElement("section");panel.id="pairing-panel";
  panel.innerHTML='<label>Local pairing code <input id="pairing-code" type="password" autocomplete="off"></label><button id="pair-device" type="button">Enroll device</button>';
  document.querySelector("header").append(panel);
  const options=document.createElement("div");
  options.innerHTML='<label>Session recipe <select id="fake-recipe"><option value="fake-finite">Finite</option><option value="fake-interactive">Interactive</option></select></label> <label>Handoff device ID <input id="handoff-device"></label><button data-operation="Handoff" type="button">Handoff</button>';
  document.querySelector(".control-actions").append(options);
  const claimButton=document.querySelector('[data-operation="ClaimInput"]');
  claimButton.textContent="Claim input";options.prepend(claimButton);
  document.querySelector('label[for="duration-seconds"]').hidden=true;
  const camera=document.querySelector("#am1-camera-root");if(camera.dataset.onboard!=="true"){camera.replaceChildren();camera.textContent="Cameras unavailable · synthetic fake evidence only";}
  document.querySelector("#control-help").textContent=defaultControlHelp;
  document.querySelector('[data-operation="Approve"]').hidden=true;
  for(const button of document.querySelectorAll("[data-operation], [data-body-key]")) {
    button.removeAttribute("data-tip");button.title="Fake session control";
    if(button.dataset.operation)button.setAttribute("aria-label",`${button.dataset.operation} fake session`);
    if(button.dataset.bodyKey==="z" || button.dataset.bodyKey==="x") {
      button.disabled=true;button.title="Unavailable in fake mode";
      button.setAttribute("aria-label",`${button.dataset.bodyKey.toUpperCase()} unavailable in fake mode`);
      button.querySelector("span").textContent="Unavailable in fake mode";
    }
  }
  const elbow=document.createElement("section");elbow.id="live-elbow-controls";elbow.hidden=true;
  elbow.innerHTML='<h2>Live left elbow</h2><button type="button" data-elbow-jog="-1" disabled>Elbow - <kbd>[</kbd></button> <button type="button" data-elbow-jog="1" disabled>Elbow + <kbd>]</kbd></button> <output>Measured hold pending</output>';
  document.querySelector('[data-console-page="control"]').append(elbow);
  const transport=globalThis.am1Remote=new AM1SessionTransport(render);
  const act=async fn=>{try{await fn();}catch(error){render(transport,error.message);}};
  document.querySelector("#pair-device").onclick=()=>act(()=>transport.enroll(document.querySelector("#pairing-code").value));
  document.querySelector(".control-actions").addEventListener("click",event=> {
    const op=event.target.closest("[data-operation]")?.dataset.operation;
    if(op==="ClaimInput")act(()=>transport.claim());
    else if(op==="Start")act(()=>{const selected=document.querySelector("#fake-recipe").value;
      return transport.snapshot?.presets?.some(preset=>preset.id===selected) ? transport.startPreset(selected) : transport.start(selected);});
    else if(op==="Pause" || op==="Stop")act(()=>transport.protective(op.toLowerCase()));
    else if(op==="Resume")act(()=>transport.resume());
    else if(op==="Handoff")act(async()=> {await transport.releaseInput();const result=await transport.post("handoff",{operation_id:crypto.randomUUID(),target_device_id:document.querySelector("#handoff-device").value});transport.clear("handoff");render(transport,result.reason);});
  });
  document.querySelector("#fake-recipe").addEventListener("change",()=>render(transport));
  for(const button of elbow.querySelectorAll("[data-elbow-jog]")) {
    const direction=Number(button.dataset.elbowJog);
    button.addEventListener("pointerdown",event=>{if(transport.pressElbowJog(direction,"pointer:"+event.pointerId+":"+direction))button.setPointerCapture(event.pointerId);});
    for(const name of ["pointerup","pointercancel","lostpointercapture"])button.addEventListener(name,event=>transport.releaseElbowJog("pointer:"+event.pointerId+":"+direction,name==="pointerup"));
  }
  const typing=target=>target?.closest("input,textarea,select,[contenteditable]");
  document.addEventListener("keydown",event=> {if(typing(event.target)||event.repeat)return;const key=event.key.toLowerCase();if((key==="[" || key==="]") && transport.pressElbowJog(key==="[" ? -1 : 1,"key:"+key)){event.preventDefault();return;}if("wasduj".includes(key)&&transport.connection&&transport.snapshot?.run?.recipe==="fake-interactive"){transport.held.add(key);event.preventDefault();}});
  document.addEventListener("keyup",event=> {const key=event.key.toLowerCase();if(key==="[" || key==="]"){transport.releaseElbowJog("key:"+key,true);return;}if(!"wasduj".includes(key)||transport.snapshot?.run?.recipe!=="fake-interactive")return;transport.held.delete(key);if(!transport.held.size)transport.releaseInput();});
  for(const button of document.querySelectorAll("[data-body-key]")) {
    if(button.disabled)continue;
    button.addEventListener("pointerdown",event=>{if(transport.connection&&transport.snapshot?.run?.recipe==="fake-interactive"){button.setPointerCapture(event.pointerId);transport.held.add(button.dataset.bodyKey);}});
    for(const name of ["pointerup","pointercancel","lostpointercapture"])button.addEventListener(name,()=>{if(transport.snapshot?.run?.recipe!=="fake-interactive")return;transport.held.delete(button.dataset.bodyKey);if(!transport.held.size)transport.releaseInput();});
  }
  window.addEventListener("blur",()=>transport.releaseInput());
  document.addEventListener("visibilitychange",()=>{if(document.hidden)transport.releaseInput();});
  window.addEventListener("pagehide",()=>transport.detach());
  window.addEventListener("pageshow",event=>{if(event.persisted)transport.restore();});
  document.querySelector('[data-help="touch"]').onclick=()=>{const help=document.querySelector("#control-help");help.hidden=!help.hidden;};
  setInterval(()=>{if(transport.isLiveElbow())transport.input();},60);
  setInterval(()=>{if(!transport.isLiveElbow())transport.input();},100);
  setInterval(()=>{if(transport.connected&&transport.controllerGeneration)transport.renew().catch(()=>{});},500);
  render(transport);transport.initialize().catch(error=>render(transport,error.message));
};
