"use strict";
// Remote fake mode never instantiates the legacy body input, owner restore or camera adapters.
class AM1SessionTransport {
  constructor(render) {
    this.render=render; this.snapshot=null; this.connected=false; this.csrf=null; this.device=null;
    this.control=false; this.controllerGeneration=null; this.connection=null; this.held=new Set();
    this.pending=new Map(); this.seq=0; this.socket=null; this.inputBusy=false; this.epoch=0; this.intent=0;
  }
  clear(reason="disconnected") {
    this.epoch++; this.intent++; this.held.clear(); this.connection=null; this.controllerGeneration=null; this.seq=0;
    for(const p of this.pending.values()) p.reject(Error(reason));
    this.pending.clear(); this.inputBusy=false;
  }
  async post(path,body) {
    const response=await fetch(`/api/${path}`,{method:"POST",headers:{"Content-Type":"application/json","X-AM1-CSRF":this.csrf || ""},body:JSON.stringify(body)});
    const result=await response.json();
    if(!response.ok) throw Error(result.reason || "Request refused");
    return result;
  }
  async enroll(code) {await this.post("enroll",{code}); await this.initialize();}
  async initialize() {
    const response=await fetch("/api/session");
    if(!response.ok) {this.render(this,"Enter a local one-use pairing code.");return;}
    const device=await response.json(); this.csrf=device.csrf; this.device=device.device_id;this.control=device.control;this.optionalVideo=device.optional_p1_video;
    await this.attach();
  }
  async attach() {
    this.clear();
    const epoch=this.epoch;
    try {
      const attached=await this.post("attach",{});
      if(epoch!==this.epoch)return;
      this.snapshot=attached.snapshot;
      const ws=this.socket=new WebSocket(`${location.origin.replace("https:","wss:")}/api/ws`);
      ws.onopen=()=>ws.send(JSON.stringify({ticket:attached.ticket}));
      ws.onmessage=event=> {
        if(this.socket!==ws)return;
        const value=JSON.parse(event.data);
        if(value.kind==="snapshot") {
          const previous=this.snapshot?.run,next=value.snapshot.run;
          if(previous?.run_id!==next?.run_id || previous?.intent_revision!==next?.intent_revision) {
            this.intent++;this.held.clear();
          }
          this.snapshot=value.snapshot;
          if(value.revision) {ws.send(JSON.stringify({ack:value.revision}));this.connected=true;}
          const controller=this.snapshot.controller;
          if(this.controllerGeneration && (controller?.device_id!==this.device || controller.controller_generation!==this.controllerGeneration))
            this.clear("Controller generation changed");
          if(controller?.device_id!==this.device) {this.held.clear();this.connection=null;}
          this.render(this);
        } else if(value.kind==="result") {
          const pending=this.pending.get(value.id);if(pending){this.pending.delete(value.id);pending.resolve(value);}
        } else if(value.kind==="unavailable") {this.clear("owner unavailable");this.render(this,"Owner unavailable; inspect current status after reconnect.");}
      };
      ws.onclose=()=> {
        if(this.socket!==ws)return;
        this.connected=false;this.clear();this.render(this,"Disconnected; controls cleared. Reconnecting to current state.");
        setTimeout(()=>this.attach().catch(()=>{}),300);
      };
      ws.onerror=()=>ws.close();
    } catch(error) {this.connected=false;this.render(this,error.message);setTimeout(()=>this.attach().catch(()=>{}),500);}
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
      controller_generation:this.controllerGeneration, connection_generation:this.connection};
  }
  async claim(generation) {
    const epoch=this.epoch;
    const result=await this.post("claim",generation===undefined ? {} : {controller_generation:generation});
    if(epoch!==this.epoch || !this.connected || (generation!==undefined && generation!==this.controllerGeneration))return result;
    if(result.accepted)this.controllerGeneration=result.controller_generation;
    else if(generation!==undefined)this.clear("Controller renewal refused");
    this.render(this,result.reason);return result;
  }
  async renew() {
    const generation=this.controllerGeneration;
    if(generation) return this.claim(generation);
  }
  async start(recipe) {
    const payload={operation_id:crypto.randomUUID(),recipe,controller_generation:this.controllerGeneration};
    // Outcome-loss retry uses the same immutable operation identity, never a new Start.
    let result;
    try {result=await this.post("start",payload);} catch {result=await this.post("start",payload);}
    this.render(this,result.reason);return result;
  }
  async protective(op) {
    this.intent++; this.held.clear();
    const result=await this.post(op,{run_id:this.snapshot?.run?.run_id,operation_id:crypto.randomUUID()});
    this.render(this,result.reason);return result;
  }
  async resume() {
    this.held.clear();
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
  async releaseInput() {
    this.held.clear();
    if(this.connected && this.connection && this.snapshot?.run?.recipe==="fake-interactive")
      await this.command({op:"release_input",...this.identities()}).catch(()=>{});
  }
  async input() {
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
    const recipes=transport.snapshot?.recipes, select=document.querySelector("#fake-recipe");
    if(recipes && select.dataset.recipes!==JSON.stringify(recipes)) {
      const previous=select.value;
      select.replaceChildren(...recipes.filter(name=>name!=="fake-finite-short").map(name=> {
        const option=document.createElement("option"); option.value=name; option.textContent=name; return option;
      }));
      if([...select.options].some(option=>option.value===previous))select.value=previous;
      select.dataset.recipes=JSON.stringify(recipes);
    }
    const physicalRun=run?.source==="protected-physical-provider" || run?.recipe?.startsWith("physical-");
    const physical=physicalRun || recipes?.some(name=>name.startsWith("physical-"));
    const simulated=run?.source==="simulated-provider" || recipes?.some(name=>name.startsWith("sim-"));
    const priorTest=physical && run && !physicalRun, priorKind=simulated ? "simulated" : "synthetic";
    document.querySelector("#view-heading").textContent=physical ? "Physical finite AM1 session" : simulated ? "Simulated finite provider session" : "Fake persistent session";
    state.textContent=run ? `${priorTest ? `Prior ${priorKind}` : physical ? "Physical" : "Fake"} session · ${run.status} · ${run.progress_s.toFixed(2)} s` : `${physical ? "Physical" : "Fake"} session · no run`;
    document.querySelector("#session-details").textContent=JSON.stringify(transport.snapshot,null,2);
    const terminal=run && ["stopped","completed","faulted","interrupted"].includes(run.status);
    document.querySelector("#live-countdown").textContent=priorTest ? `Prior ${priorKind} result · cleanup: ${run.cleanup || "unknown"}; physical Start required` :
      physical && run?.status==="finishing" ? "Physical finishing · measured cleanup pending; native Live countdown pending" :
      physical && terminal ? `Physical session ended · cleanup: ${run.cleanup || "unknown"}` :
      physical && run && !Number.isFinite(run.native_live_at) ? "Physical startup · native Live countdown pending" :
      run ? `Original Live budget: ${Math.ceil(run.remaining_s || 0)} s remaining` : physical ? "No physical run · Start required" : "No fake run";
    const evidence=transport.snapshot?.evidence;
    const observationLabel={"real-p1/pi-decoded":"Required P1 observation", "real-local-camera/pi-decoded-arrival":`Required local ${evidence?.sensing_source?.role || "camera"} observation`}[evidence?.observation_provenance];
    const observationStatus=observationLabel ? `${observationLabel} · ${evidence?.required_observation ? "current images validated on Pi" : "current image proof unavailable"}` : "Task observation policy shown in current status";
    document.querySelector("#connection").textContent=`${transport.connected ? "Connected" : "Disconnected"} · ${physical ? `${observationStatus} · actual motor feedback ${evidence?.feedback ? "current" : "unavailable"} · protected-host ACK ${evidence?.native_ack ? "current" : "pending"}` : observationLabel ? `${observationStatus} · motor feedback and commands simulated` : "Cameras and hardware unavailable in fake mode"}`;
    if(transport.optionalVideo&&!transport.videoView&&globalThis.AM1P1View) {
      const root=document.querySelector("#am1-camera-root");root.replaceChildren();
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
    document.querySelector("#control-help").textContent=physical ? "Explicitly claim input before physical Start. Start activates the selected bounded recipe through the protected motor host. Viewing, enrollment and reconnection do not start motion. Finite authorization survives browser closure. Pause and Stop remain available to enrolled control-capable spectators. Actual feedback and protected-host ACK are reported separately; an accepted command does not prove joint motion. Body inputs belong to the selected finite recipe. Required camera evidence is validated on Pi; optional P1 viewing is advisory." : defaultControlHelp;
    if(!transport.videoView)document.querySelector("#am1-camera-root").textContent=physical ? "Task camera evidence is validated on Pi; optional P1 viewing is advisory." : "Cameras unavailable · synthetic fake evidence only";
    for(const button of document.querySelectorAll("[data-operation], [data-body-key]")) {
      button.title=physical ? "Physical finite session control" : "Fake session control";
      if(button.dataset.operation)button.setAttribute("aria-label",`${button.dataset.operation} ${physical ? "physical" : "fake"} session`);
      if(button.dataset.bodyKey) {
        const unavailable=button.dataset.bodyKey==="z" || button.dataset.bodyKey==="x";
        button.disabled=physical || unavailable;
        if(physical) {
          button.title="Body inputs belong to the selected finite physical recipe";
          button.setAttribute("aria-label",`${button.dataset.bodyKey.toUpperCase()} provided by selected physical recipe`);
          if(unavailable)button.querySelector("span").textContent="Unavailable in finite physical recipes";
        } else if(unavailable) {
          button.title="Unavailable in fake mode";
          button.setAttribute("aria-label",`${button.dataset.bodyKey.toUpperCase()} unavailable in fake mode`);
          button.querySelector("span").textContent="Unavailable in fake mode";
        }
      }
    }
    if(message){notice.textContent=message;notice.hidden=false;}
    document.querySelector("#pairing-panel").hidden=Boolean(transport.device);
    for(const button of document.querySelectorAll("[data-operation]"))button.disabled=!transport.control || !transport.connected;
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
  const camera=document.querySelector("#am1-camera-root");camera.replaceChildren();camera.textContent="Cameras unavailable · synthetic fake evidence only";
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
  const transport=globalThis.am1Remote=new AM1SessionTransport(render);
  const act=async fn=>{try{await fn();}catch(error){render(transport,error.message);}};
  document.querySelector("#pair-device").onclick=()=>act(()=>transport.enroll(document.querySelector("#pairing-code").value));
  document.querySelector(".control-actions").addEventListener("click",event=> {
    const op=event.target.closest("[data-operation]")?.dataset.operation;
    if(op==="ClaimInput")act(()=>transport.claim());
    else if(op==="Start")act(()=>transport.start(document.querySelector("#fake-recipe").value));
    else if(op==="Pause" || op==="Stop")act(()=>transport.protective(op.toLowerCase()));
    else if(op==="Resume")act(()=>transport.resume());
    else if(op==="Handoff")act(async()=> {await transport.releaseInput();const result=await transport.post("handoff",{operation_id:crypto.randomUUID(),target_device_id:document.querySelector("#handoff-device").value});transport.clear("handoff");render(transport,result.reason);});
  });
  const typing=target=>target?.closest("input,textarea,select,[contenteditable]");
  document.addEventListener("keydown",event=> {if(typing(event.target)||event.repeat)return;const key=event.key.toLowerCase();if("wasduj".includes(key)&&transport.connection){transport.held.add(key);event.preventDefault();}});
  document.addEventListener("keyup",event=> {const key=event.key.toLowerCase();if(!"wasduj".includes(key))return;transport.held.delete(key);if(!transport.held.size)transport.releaseInput();});
  for(const button of document.querySelectorAll("[data-body-key]")) {
    if(button.disabled)continue;
    button.addEventListener("pointerdown",event=>{if(transport.connection){button.setPointerCapture(event.pointerId);transport.held.add(button.dataset.bodyKey);}});
    for(const name of ["pointerup","pointercancel","lostpointercapture"])button.addEventListener(name,()=>{transport.held.delete(button.dataset.bodyKey);if(!transport.held.size)transport.releaseInput();});
  }
  window.addEventListener("blur",()=>transport.releaseInput());
  document.addEventListener("visibilitychange",()=>{if(document.hidden)transport.releaseInput();});
  window.addEventListener("pagehide",()=>{transport.held.clear();transport.socket?.close();});
  document.querySelector('[data-help="touch"]').onclick=()=>{const help=document.querySelector("#control-help");help.hidden=!help.hidden;};
  setInterval(()=>transport.input(),100);
  setInterval(()=>{if(transport.connected&&transport.controllerGeneration)transport.renew().catch(()=>{});},500);
  render(transport);transport.initialize().catch(error=>render(transport,error.message));
};
