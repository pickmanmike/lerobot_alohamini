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
    const device=await response.json(); this.csrf=device.csrf; this.device=device.device_id;this.control=device.control;
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
    const epoch=this.epoch,intent=++this.intent,generation=this.controllerGeneration;
    const current=()=>this.connected && epoch===this.epoch && intent===this.intent && generation===this.controllerGeneration;
    const connected=await this.command({op:"connect",run_id:this.snapshot?.run?.run_id});
    if(!current())return connected; // Preserve the admitted result; cancel only its continuation.
    if(!connected.accepted){this.render(this,connected.reason);return connected;}
    this.connection=connected.connection_generation;this.snapshot=connected.snapshot;this.seq=0;
    const released=await this.command({op:"release_input",...this.identities()});
    if(!current() || !released.accepted)return released;
    const result=await this.command({op:"resume",...this.identities(),operation_id:crypto.randomUUID()});
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
  const render=(transport,message)=> {
    const run=transport.snapshot?.run;
    state.textContent=run ? `Fake session · ${run.status} · ${run.progress_s.toFixed(2)} s` : "Fake session · no run";
    document.querySelector("#session-details").textContent=JSON.stringify(transport.snapshot,null,2);
    document.querySelector("#live-countdown").textContent=run ? `Original Live budget: ${Math.ceil(run.remaining_s || 0)} s remaining` : "No fake run";
    document.querySelector("#connection").textContent=`${transport.connected ? "Connected" : "Disconnected"} · Cameras and hardware unavailable in fake mode`;
    document.querySelector("#gate-state").textContent=transport.controllerGeneration ? "Explicit controller; accepted requests require actual fake acknowledgement." : "Spectator; viewing does not claim input.";
    if(message){notice.textContent=message;notice.hidden=false;}
    document.querySelector("#pairing-panel").hidden=Boolean(transport.device);
    for(const button of document.querySelectorAll("[data-operation]"))button.disabled=!transport.control || !transport.connected;
  };
  document.querySelector("#view-heading").textContent="Fake persistent session";
  const panel=document.createElement("section");panel.id="pairing-panel";
  panel.innerHTML='<label>Local pairing code <input id="pairing-code" type="password" autocomplete="off"></label><button id="pair-device" type="button">Enroll device</button>';
  document.querySelector("header").append(panel);
  const options=document.createElement("div");
  options.innerHTML='<label>Fake recipe <select id="fake-recipe"><option value="fake-finite">Finite</option><option value="fake-interactive">Interactive</option></select></label> <label>Handoff device ID <input id="handoff-device"></label><button data-operation="Handoff" type="button">Handoff</button>';
  document.querySelector(".control-actions").append(options);
  const claimButton=document.querySelector('[data-operation="ClaimInput"]');
  claimButton.textContent="Claim input";options.prepend(claimButton);
  document.querySelector('label[for="duration-seconds"]').hidden=true;
  const camera=document.querySelector("#am1-camera-root");camera.replaceChildren();camera.textContent="Cameras unavailable · synthetic fake evidence only";
  document.querySelector("#control-help").textContent="Explicitly claim input before Start. Finite authorization survives browser closure. Interactive work needs a fresh connected snapshot, empty release and qualified Resume. Disconnect, blur and handoff clear held keys. Pause and Stop remain available to enrolled control-capable spectators. No physical hardware or cameras are used.";
  document.querySelector('[data-operation="Approve"]').hidden=true;
  for(const button of document.querySelectorAll("[data-operation], [data-body-key]")) {
    button.removeAttribute("data-tip");button.title="Fake session control";
    if(button.dataset.operation)button.setAttribute("aria-label",`${button.dataset.operation} fake session`);
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
  document.addEventListener("keydown",event=> {if(typing(event.target)||event.repeat)return;const key=event.key.toLowerCase();if("wasdujzx".includes(key)&&transport.connection){transport.held.add(key);event.preventDefault();}});
  document.addEventListener("keyup",event=> {transport.held.delete(event.key.toLowerCase());if(!transport.held.size)transport.releaseInput();});
  for(const button of document.querySelectorAll("[data-body-key]")) {
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
