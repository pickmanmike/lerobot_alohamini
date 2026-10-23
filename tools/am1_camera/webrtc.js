"use strict";
// Native go2rtc SDP exchange. Connected transport is never a decoded-frame clock.
globalThis.AM1OnboardView=class {
  constructor(video,options) {
    this.video=video;this.options=options;this.clock=options.clock||(()=>performance.now());
    this.pc=null;this.epoch=0;this.binding=null;this.source=null;this.sourceAt=0;
    this.decodedAt=null;this.frames=0;this.mediaTime=-1;this.callback=null;
    this.reporting=false;this.timer=null;this.connecting=false;this.error=null;this.progressReceipt=null;
    this.reports=new Map();this.reportSerial=0;this.latestReport=0;
    this.lastReportAt=-Infinity;this.lastReportedFrames=0;this.lastReportedMedia=-1;
    this.diagnostic=null;this.statsPending=null;this.diagnosticView=null;
  }
  diagnostics() {
    const view=this.diagnosticView;
    return view?{attempt:view.attempt,state:view.state,records:view.records.map(row=>({...row}))}:null;
  }
  diagnosticCurrent(attempt) {return this.diagnostic===attempt&&attempt.epoch===this.epoch&&attempt.pc===this.pc;}
  diagnose(attempt,phase,fields) {
    try {
      const limits={offer:1,"remote-description":1,ice:2,connection:2,track:1,stats:1};
      if(!this.diagnosticCurrent(attempt)||!limits[phase]||attempt.counts[phase]>=limits[phase])return;
      attempt.counts[phase]++;
      // Call sites supply only the closed scalar projections below, never browser
      // objects, messages, SDP, source identities, candidates or addresses.
      const record={attempt:attempt.epoch,phase,...fields};
      this.diagnosticView.records.push(record);
      console.info("AM1 WebRTC diagnostics",JSON.stringify(record));
    } catch {} // Diagnostics must never change player behavior, even if console fails.
  }
  diagnosticCategory(error) {
    try {const value=({AbortError:"aborted",TimeoutError:"timeout",InvalidStateError:"invalid-state",
      OperationError:"operation",NotSupportedError:"unsupported"})[error?.name];return typeof value==="string"?value:"other";}catch{return "other";}
  }
  diagnosticState(pc,ice=false) {
    try {
      const value=ice?pc.iceConnectionState:pc.connectionState;
      return (ice?["new","checking","connected","completed","disconnected","failed","closed"]:
        ["new","connecting","connected","disconnected","failed","closed"]).includes(value)?value:"unknown";
    }catch{return "unknown";}
  }
  diagnosticOffer(attempt,response,valid,error=null) {
    try {const status=response?.status;this.diagnose(attempt,"offer",{http_status:Number.isInteger(status)&&status>=100&&status<=599?status:null,
      headers_valid:valid===true,error_category:error});}catch{}
  }
  beginDiagnostics(epoch,pc) {
    const attempt={epoch,pc,counts:{offer:0,"remote-description":0,ice:0,connection:0,track:0,stats:0},timer:null};
    this.diagnostic=attempt;
    this.diagnosticView={attempt:epoch,state:"current",records:[]};
    try {attempt.timer=setTimeout(()=>this.sampleDiagnosticStats(attempt),3000);}catch{}
    return attempt;
  }
  sampleDiagnosticStats(attempt) {
    let ticket;
    const record=(outcome,row=null,codec=null)=>{
      try {
        const count=key=>{const value=row?.[key];return Number.isSafeInteger(value)&&value>=0?value:null;};
        this.diagnose(attempt,"stats",{outcome,ice_state:this.diagnosticState(attempt.pc,true),
          connection_state:this.diagnosticState(attempt.pc),packetsReceived:count("packetsReceived"),
          bytesReceived:count("bytesReceived"),framesReceived:count("framesReceived"),framesDecoded:count("framesDecoded"),
          jitterBufferEmittedCount:count("jitterBufferEmittedCount"),codec});
      }catch{}
    };
    try {
      if(!this.diagnosticCurrent(attempt))return;
      attempt.timer=null;
      if(this.statsPending){record("busy");return;}
      if(typeof attempt.pc.getStats!=="function"){record("unsupported");return;}
      ticket={attempt,timedOut:false,timer:null};this.statsPending=ticket;
      ticket.timer=setTimeout(()=>{ticket.timedOut=true;record("timeout");},750);
      const finish=()=>{clearTimeout(ticket.timer);if(this.statsPending===ticket)this.statsPending=null;};
      let pending;
      try {pending=attempt.pc.getStats();}catch{finish();record("failed");return;}
      Promise.resolve(pending).then(report=>{
        if(ticket.timedOut||!this.diagnosticCurrent(attempt))return;
        try {
          let row=null,inspected=0;
          for(const candidate of report.values()) {
            if(++inspected>64)break;
            if(candidate?.type==="inbound-rtp"&&(candidate.kind==="video"||candidate.mediaType==="video")){row=candidate;break;}
          }
          const mime=row?report.get(row.codecId)?.mimeType:null;
          const codec=typeof mime==="string"?({"video/h264":"H264","video/vp8":"VP8","video/vp9":"VP9","video/av1":"AV1"})[mime.toLowerCase()]||null:null;
          record(row?"available":"no-inbound",row,codec);
        }catch{record("failed");}
      },()=>{if(!ticket.timedOut)record("failed");}).then(finish,finish);
    }catch{
      if(ticket){clearTimeout(ticket.timer);if(this.statsPending===ticket)this.statsPending=null;}
      record("failed");
    }
  }
  endDiagnostics() {
    try {
      const attempt=this.diagnostic;this.diagnostic=null;clearTimeout(attempt?.timer);
      if(attempt&&this.diagnosticView?.attempt===attempt.epoch)this.diagnosticView.state="stopped";
      // getStats cannot be aborted. Retain its single underlying slot until it
      // settles, without awaiting it or permitting another hung call per retry.
      if(this.statsPending?.attempt===attempt)clearTimeout(this.statsPending.timer);
    }catch{}
  }
  updateSource(report,at=this.clock()) {this.source=report;this.sourceAt=at;}
  health() {
    const now=this.clock(),binding=this.binding,source=this.source?.cameras?.[binding?.role],video=this.source?.video;
    const age=this.decodedAt===null?null:Math.max(0,now-this.decodedAt);
    const sourceAge=Number.isFinite(source?.age_ms)?source.age_ms+Math.max(0,now-this.sourceAt):Infinity;
    const same=!!binding&&binding.owner_generation===this.source?.owner_generation&&
      binding.capture_generation===source?.capture_generation&&binding.encode_generation===video?.encode_generation&&
      binding.capture_generation===video?.capture_generation&&binding.role===video?.role;
    const sourceFresh=same&&source?.configured===true&&source?.state==="fresh"&&sourceAge<=500;
    return {...(binding||{}),decoded_frames:this.frames,media_time:this.mediaTime,decoded_age_ms:age,
      source_age_ms:Number.isFinite(sourceAge)?sourceAge:null,source_fresh:sourceFresh,
      fresh:sourceFresh&&age!==null&&age<500,connecting:this.connecting,error:this.error};
  }
  async start(role) {
    await this.stop();const epoch=++this.epoch;this.connecting=true;this.error=null;
    if(typeof RTCPeerConnection!=="function"||typeof this.video.requestVideoFrameCallback!=="function") {
      this.connecting=false;throw Error("This browser cannot report decoded WebRTC frames; select explicit JPEG rollback.");
    }
    const pc=this.pc=new RTCPeerConnection({iceServers:[]});
    const attempt=this.beginDiagnostics(epoch,pc);let response=null,failurePhase="create";
    pc.addTransceiver("video",{direction:"recvonly"});
    pc.ontrack=event=>{if(epoch===this.epoch){this.diagnose(attempt,"track",{kind:event.track?.kind==="video"?"video":"other"});this.video.srcObject=new MediaStream([event.track]);this.video.play().catch(()=>{});}};
    pc.onconnectionstatechange=()=>{if(epoch===this.epoch){this.diagnose(attempt,"connection",{state:this.diagnosticState(pc)});if(["failed","disconnected"].includes(pc.connectionState))this.error="WebRTC view disconnected";}};
    pc.oniceconnectionstatechange=()=>this.diagnose(attempt,"ice",{state:this.diagnosticState(pc,true)});
    try {
      const offer=await pc.createOffer();failurePhase="local-description";
      await pc.setLocalDescription(offer);failurePhase="ice-gather";
      if(pc.iceGatheringState!=="complete")await new Promise((resolve,reject)=>{
        const timeout=setTimeout(()=>reject(Error("Bounded LAN ICE gathering timed out")),2000);
        pc.onicegatheringstatechange=()=>{if(pc.iceGatheringState==="complete"){clearTimeout(timeout);resolve();}};
      });
      if(epoch!==this.epoch)return;
      failurePhase="offer-http";
      response=await fetch(this.options.offer(role),{method:"POST",credentials:"same-origin",redirect:"error",
        headers:{"Content-Type":"application/sdp","X-AM1-CSRF":this.options.csrf?.()||""},
        body:pc.localDescription.sdp,signal:AbortSignal.timeout(5000)});
      if(!response.ok){this.diagnosticOffer(attempt,response,false,"http");throw Error(`Onboard WebRTC unavailable (${response.status})`);}
      const binding={role,owner_generation:response.headers.get("X-Camera-Owner-Generation"),
        capture_generation:response.headers.get("X-Camera-Capture-Generation"),
        encode_generation:response.headers.get("X-Camera-Encode-Generation")};
      if(this.options.progress) {
        binding.view_generation=response.headers.get("X-Camera-View-Generation");
        if(typeof binding.view_generation!=="string"||
           !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(binding.view_generation))
          throw Error("Current view attachment unavailable");
      }
      if(Object.values(binding).some(value=>typeof value!=="string"||!value||value.length>128))throw Error("Current video source identity unavailable");
      this.diagnosticOffer(attempt,response,true);
      const answer=await response.text();
      if(epoch!==this.epoch)return;
      failurePhase="remote-description";
      try {await pc.setRemoteDescription({type:"answer",sdp:answer});this.diagnose(attempt,"remote-description",{ok:true,error_category:null});}
      catch(error){this.diagnose(attempt,"remote-description",{ok:false,error_category:this.diagnosticCategory(error),failure_phase:failurePhase});throw error;}
      if(epoch!==this.epoch)return;
      this.binding=binding;this.connecting=false;
      const decoded=(_now,metadata)=>{
        if(epoch!==this.epoch)return;
        if(Number.isSafeInteger(metadata.presentedFrames)&&metadata.presentedFrames>this.frames&&
           Number.isFinite(metadata.mediaTime)&&metadata.mediaTime>this.mediaTime) {
          this.frames=metadata.presentedFrames;this.mediaTime=metadata.mediaTime;this.decodedAt=this.clock();
          void this.report(); // Fresh decoded progress may take an already-due opportunity immediately.
        }
        this.callback=this.video.requestVideoFrameCallback(decoded);
      };
      this.callback=this.video.requestVideoFrameCallback(decoded);
      this.timer=setInterval(()=>void this.report(),150);
    } catch(error) {
      this.diagnosticOffer(attempt,response,false,this.diagnosticCategory(error));
      this.diagnose(attempt,"remote-description",{ok:false,error_category:this.diagnosticCategory(error),failure_phase:failurePhase});
      if(epoch===this.epoch){await this.stop();this.error=error.message;throw error;}
    }
  }
  updateReporting() {
    this.reporting=[...this.reports.values()].some(slot=>slot.epoch===this.epoch&&!slot.cancelled);
  }
  report() {
    const latest=this.reports.get(this.latestReport),idle=()=>Promise.resolve();
    const now=this.clock(),decodedAge=this.decodedAt===null?Infinity:now-this.decodedAt;
    // Cached browser source status can lag; Gateway independently qualifies
    // current Pi source identity/arrival. Publish only genuinely current decode.
    if(!this.options.progress||!this.binding||this.frames<1||!Number.isFinite(decodedAge)||decodedAge<0||decodedAge>=500)return idle();
    if(now-this.lastReportAt<150||this.frames<=this.lastReportedFrames||this.mediaTime<=this.lastReportedMedia)
      return latest?.epoch===this.epoch&&!latest.cancelled?latest.promise:idle();
    if(this.reports.size>=2) {
      // Cancel oldest delivery, then let its raw operation settle before using
      // that slot. A transport ignoring abort stays quarantined against this cap.
      [...this.reports.values()].find(slot=>!slot.cancelled)?.cancel("superseded");
      return idle(); // No FIFO: the next opportunity samples current decoder data.
    }
    const epoch=this.epoch,id=++this.reportSerial,controller=new AbortController();
    const body={...this.binding,decoded_frames:this.frames,media_time:this.mediaTime};
    const slot={id,epoch,cancelled:false,done:false,controller};
    const current=()=>{
      const source=this.source?.cameras?.[body.role],video=this.source?.video;
      return !slot.cancelled&&epoch===this.epoch&&id===this.latestReport&&
        ["role","owner_generation","capture_generation","encode_generation","view_generation"].every(key=>body[key]===this.binding?.[key])&&
        body.owner_generation===this.source?.owner_generation&&body.capture_generation===source?.capture_generation&&
        body.capture_generation===video?.capture_generation&&body.encode_generation===video?.encode_generation&&body.role===video?.role;
    };
    let release;
    const cancelled=new Promise(resolve=>{release=resolve;});
    slot.cancel=reason=>{
      if(slot.cancelled||slot.done)return;
      const active=current();slot.cancelled=true;controller.abort();release();this.updateReporting();
      if(active&&reason==="timeout")this.error="View progress delivery unavailable";
    };
    slot.timer=setTimeout(()=>slot.cancel("timeout"),750);
    this.reports.set(id,slot);this.latestReport=id;
    this.lastReportAt=now;this.lastReportedFrames=body.decoded_frames;this.lastReportedMedia=body.media_time;
    this.updateReporting();
    const work=(async()=>{
      try {
        const response=await fetch(this.options.progress,{method:"POST",credentials:"same-origin",redirect:"error",
          headers:{"Content-Type":"application/json","X-AM1-CSRF":this.options.csrf?.()||""},
          body:JSON.stringify(body),signal:controller.signal});
        if(slot.cancelled)return;
        const length=response.headers.get("Content-Length"),mime=response.headers.get("Content-Type");
        if(!response.ok||!/^\d{1,5}$/.test(length||"")||Number(length)>8192||
           typeof mime!=="string"||mime.split(";")[0]!=="application/json")throw Error("Invalid view receipt");
        const text=await response.text();
        if(text.length>8192)throw Error("Oversized view receipt");
        const receipt=JSON.parse(text),allowed=["accepted","status","reason","effect_admitted","required_view","view_delivery"];
        if(!receipt||typeof receipt!=="object"||Array.isArray(receipt)||typeof receipt.accepted!=="boolean"||
           Object.keys(receipt).some(key=>!allowed.includes(key)))throw Error("Invalid logical view receipt");
        // Receipt delivery cannot renew original source/decoder clocks, and an
        // older request or source cannot replace the latest current observation.
        if(current())this.progressReceipt=receipt;
      } catch {if(current())this.error="View progress delivery unavailable";}
      finally {slot.done=true;clearTimeout(slot.timer);this.reports.delete(id);this.updateReporting();}
    })();
    slot.promise=Promise.race([work,cancelled]);
    return slot.promise;
  }
  async stop() {
    this.epoch++;this.endDiagnostics();clearInterval(this.timer);this.timer=null;
    for(const slot of this.reports.values())slot.cancel("stop");
    this.latestReport=0;this.lastReportAt=-Infinity;this.lastReportedFrames=0;this.lastReportedMedia=-1;
    this.updateReporting();
    if(this.callback!==null)this.video.cancelVideoFrameCallback?.(this.callback);
    this.callback=null;this.pc?.close();this.pc=null;this.video.srcObject=null;
    this.binding=null;this.decodedAt=null;this.frames=0;this.mediaTime=-1;this.connecting=false;this.progressReceipt=null;
  }
};
