"use strict";
const AM1_VIEW_PROGRESS_COLUMNS=Object.freeze(["serial","frames","media_time","callback_ms","presentation_ms",
  "expected_display_ms","fetch_ms","settled_ms","cancel_ms","outcome","http_status","pi_request_s",
  "pi_status_start_s","pi_status_done_s","pi_floor_s","pi_core_accepted_s","pi_until_s","current",
  "accepted","qualified","owner_accepted","owner_qualified","pi_core_floor_s",
  "source_sequence","pi_source_arrived_s","pi_core_started_s"]);
const AM1_RECEIVER_STATS_COLUMNS=Object.freeze(["trigger","outcome","requested_ms","settled_ms","callback_ms",
  "presented_frames","media_time","packetsReceived","bytesReceived","framesReceived","framesDecoded",
  "framesDropped","jitterBufferEmittedCount","codec","ice_state","connection_state"]);
// Native go2rtc SDP exchange. Connected transport is never a decoded-frame clock.
globalThis.AM1OnboardView=class {
  constructor(video,options) {
    this.video=video;this.options=options;this.clock=options.clock||(()=>performance.now());
    this.pc=null;this.epoch=0;this.binding=null;this.source=null;this.sourceAt=0;
    this.decodedAt=null;this.frames=0;this.mediaTime=-1;this.callback=null;
    this.reporting=false;this.timer=null;this.connecting=false;this.error=null;this.progressReceipt=null;
    this.reports=new Map();this.reportSerial=0;this.latestReport=0;
    this.lastReportAt=-Infinity;this.lastReportedFrames=0;this.lastReportedMedia=-1;
    this.diagnostic=null;this.statsPending=null;this.diagnosticView=null;this.receiverTransition=null;
  }
  diagnostics() {
    const view=this.diagnosticView;
    if(!view)return null;
    const snapshot={attempt:view.attempt,state:view.state,records:view.records.map(row=>({...row})),view_progress:null};
    try {
      const number=value=>typeof value==="number"&&Number.isFinite(value)&&value>=0&&value<=Number.MAX_SAFE_INTEGER?value:null;
      const count=value=>Number.isSafeInteger(value)&&value>=0?value:null;
      const boolean=value=>typeof value==="boolean"?value:null;
      const times=(value,keys)=>Object.fromEntries(keys.map(key=>[key,number(value?.[key])]));
      const result=value=>value&&typeof value==="object"&&!Array.isArray(value)?{
        accepted:boolean(value.accepted),status:["view_progress","refused","unknown","reporting_error"].includes(value.status)?value.status:null,
        required_view:{required:boolean(value.required_view?.required),qualified:boolean(value.required_view?.qualified)}}:null;
      const receiver=this.receiverTransition&&this.receiverTransition.attempt!==view.attempt?this.receiverTransition:view.receiver;
      if(receiver)snapshot.receiver_stats={attempt:count(receiver.attempt),current_attachment:receiver.attempt===view.attempt,clock:"browser_performance_ms",
        state:["waiting","baseline","stalled","presenting"].includes(receiver.state)?receiver.state:null,
        columns:[...AM1_RECEIVER_STATS_COLUMNS],samples:receiver.samples.slice(-2).map(row=>AM1_RECEIVER_STATS_COLUMNS.map((_,i)=>
          i===0?(["startup","baseline","stall"].includes(row[i])?row[i]:null):
          i===1?(["available","no-inbound","busy","unsupported","timeout","failed"].includes(row[i])?row[i]:null):
          i===13?(["H264","VP8","VP9","AV1"].includes(row[i])?row[i]:null):
          i===14?(["new","checking","connected","completed","disconnected","failed","closed","unknown"].includes(row[i])?row[i]:null):
          i===15?(["new","connecting","connected","disconnected","failed","closed","unknown"].includes(row[i])?row[i]:null):
          [5,7,8,9,10,11,12].includes(i)?count(row[i]):number(row[i])))};
      const now=number(this.clock()),source=this.source?.cameras?.[this.binding?.role],receipt=this.progressReceipt;
      let visibility=null,focus=null;
      if(typeof document==="object"&&document!==null) {
        const value=document.visibilityState;
        visibility=["visible","hidden","prerender"].includes(value)?value:null;
        if(typeof document.hasFocus==="function")focus=boolean(document.hasFocus());
      }
      const delivery=receipt?.view_delivery;
      snapshot.view_progress={sampled_browser_clock_ms:now,visibility_state:visibility,has_focus:focus,
        decoded_frames:count(this.frames),media_time:number(this.mediaTime),
        decoded_age_ms:now!==null&&number(this.decodedAt)!==null?number(Math.max(0,now-this.decodedAt)):null,
        source_age_ms:now!==null&&number(source?.age_ms)!==null&&number(this.sourceAt)!==null?
          number(source.age_ms+Math.max(0,now-this.sourceAt)):null,
        receipt:result(receipt)?{...result(receipt),view_delivery:delivery&&typeof delivery==="object"&&!Array.isArray(delivery)?{
          decoded_frames:count(delivery.decoded_frames),media_time:number(delivery.media_time),source_sequence:count(delivery.source_sequence),
          ...times(delivery,["request_received_at_monotonic_s","request_body_completed_at_monotonic_s",
            "status_started_at_monotonic_s","status_completed_at_monotonic_s","source_arrived_at_monotonic_s",
            "original_floor_at_monotonic_s","owner_snapshot_started_at_monotonic_s","owner_snapshot_completed_at_monotonic_s",
            "owner_progress_started_at_monotonic_s","owner_progress_completed_at_monotonic_s",
            "owner_enqueued_at_monotonic_s","http_response_ready_at_monotonic_s"]),
          core:delivery.core&&typeof delivery.core==="object"&&!Array.isArray(delivery.core)?times(delivery.core,
            ["core_started_at_monotonic_s","core_completed_at_monotonic_s","core_accepted_at_monotonic_s",
             "original_floor_at_monotonic_s","qualified_until_monotonic_s"]):null,
          owner_result:result(delivery.owner_result)}:null}:null};
      const history=this.progressDiagnostics();
      if(history){
        snapshot.view_progress.delivery_history=history;
        // This closed projection contains ASCII labels/scalars only. Drop only
        // diagnostic rows; transport slots, receipts and their clocks are untouched.
        const byteLimit=history.reports.length?8192:4096;
        while(JSON.stringify(snapshot).length>byteLimit&&history.reports.length){history.reports.shift();history.size_dropped++;}
      }
    } catch {} // Observing cached diagnostics must not affect playback or control.
    return snapshot;
  }
  diagnosticCurrent(attempt) {return this.diagnostic===attempt&&attempt.epoch===this.epoch&&attempt.pc===this.pc;}
  progressDiagnostic(event,slot=null,value=null) {
    try {
      const view=this.diagnosticView,p=view?.progress;
      const stopping=event==="cancel"&&value==="stop"&&view?.state==="stopped"&&slot?.epoch===view.attempt;
      if(!p||(!stopping&&(!this.diagnostic||!this.diagnosticCurrent(this.diagnostic)))||
         slot&&slot.epoch!==view.attempt)return;
      const number=v=>typeof v==="number"&&Number.isFinite(v)&&v>=0&&v<=Number.MAX_SAFE_INTEGER?v:null;
      const count=v=>Number.isSafeInteger(v)&&v>=0?v:null;
      const increment=v=>count(v)===null?null:Math.min(Number.MAX_SAFE_INTEGER,v+1),now=number(this.clock());
      if(event==="callback") {
        const metadata=value.metadata;
        const row={at_ms:now,presentation_ms:number(metadata.presentationTime),expected_display_ms:number(metadata.expectedDisplayTime),
          frames:count(metadata.presentedFrames),media_time:number(metadata.mediaTime),advanced:value.advanced===true};
        p.callback_count=increment(p.callback_count);p.latest_callback=row;
        if(row.advanced)p.presented={...row};
        return;
      }
      if(event==="skip") {
        const index=["decode","cadence","no_presentation","slots"].indexOf(value);
        if(index>=0)p.skipped[index]=increment(p.skipped[index]);
        return;
      }
      if(event==="report") {
        const body=value,shown=p.presented;
        const same=shown&&shown.frames===body.decoded_frames&&shown.media_time===body.media_time;
        const row=[count(slot.id),count(body.decoded_frames),number(body.media_time),number(this.decodedAt),
          same?shown.presentation_ms:null,same?shown.expected_display_ms:null,null,null,null,"pending",null,
          null,null,null,null,null,null,null,null,null,null,null,null,null,null,null];
        if(p.reports.length>=8){p.reports.shift();p.dropped=increment(p.dropped);}
        p.reports.push(row);return;
      }
      const row=p.reports.find(item=>item[0]===slot?.id);if(!row)return;
      if(event==="fetch")row[6]=now;
      else if(event==="headers") {const status=value?.status;row[10]=Number.isInteger(status)&&status>=100&&status<=599?status:null;}
      else if(event==="receipt") {
        const receipt=value.receipt,delivery=receipt?.view_delivery;
        if(!slot.cancelled)row[9]=receipt?.accepted===true?"accepted":receipt?.accepted===false?"refused":"invalid";
        for(const [index,key]of [[11,"request_received_at_monotonic_s"],[12,"status_started_at_monotonic_s"],
          [13,"status_completed_at_monotonic_s"],[14,"original_floor_at_monotonic_s"]])row[index]=number(delivery?.[key]);
        row[15]=number(delivery?.core?.core_accepted_at_monotonic_s);row[16]=number(delivery?.core?.qualified_until_monotonic_s);
        row[17]=typeof value.current==="boolean"?value.current:null;
        for(const [index,flag]of [[18,receipt?.accepted],[19,receipt?.required_view?.qualified],
          [20,delivery?.owner_result?.accepted],[21,delivery?.owner_result?.required_view?.qualified]])
          row[index]=typeof flag==="boolean"?flag:null;
        row[22]=number(delivery?.core?.original_floor_at_monotonic_s);
        row[23]=count(delivery?.source_sequence);row[24]=number(delivery?.source_arrived_at_monotonic_s);
        row[25]=number(delivery?.core?.core_started_at_monotonic_s);
      } else if(event==="cancel") {
        row[8]=now;row[9]=({timeout:"timeout",superseded:"superseded",stop:"stopped"})[value]||"cancelled";
      } else if(event==="error") {if(row[9]==="pending")row[9]="error";}
      else if(event==="settled") {row[7]=now;if(row[9]==="pending")row[9]="ignored";}
    } catch {} // Diagnostic failure cannot enter the delivery state machine.
  }
  progressDiagnostics() {
    try {
      const view=this.diagnosticView,p=view?.progress;if(!p)return null;
      const number=v=>typeof v==="number"&&Number.isFinite(v)&&v>=0&&v<=Number.MAX_SAFE_INTEGER?v:null;
      const count=v=>Number.isSafeInteger(v)&&v>=0?v:null,callback=p.latest_callback,slots=[...this.reports.values()];
      const outcomes=["pending","accepted","refused","invalid","timeout","superseded","stopped","cancelled","error","ignored"];
      return {attempt:count(view.attempt),clock:"browser_performance_ms",pi_clock:"pi_monotonic_s",capacity:8,
        callback_count:count(p.callback_count),latest_callback:callback?{at_ms:number(callback.at_ms),
          presentation_ms:number(callback.presentation_ms),expected_display_ms:number(callback.expected_display_ms),
          frames:count(callback.frames),media_time:number(callback.media_time),advanced:callback.advanced===true}:null,
        skipped:Object.fromEntries(["decode","cadence","no_presentation","slots"].map((key,i)=>[key,count(p.skipped[i])])),
        slots:{raw:count(slots.length),active:count(slots.filter(item=>item.epoch===view.attempt&&!item.cancelled).length),
          cancelled:count(slots.filter(item=>item.cancelled).length)},
        dropped:count(p.dropped),size_dropped:0,columns:[...AM1_VIEW_PROGRESS_COLUMNS],
        reports:p.reports.slice(-8).map(row=>AM1_VIEW_PROGRESS_COLUMNS.map((_,i)=>i===9?(outcomes.includes(row[i])?row[i]:null):
          i>=17&&i<=21?(typeof row[i]==="boolean"?row[i]:null):[0,1,10,23].includes(i)?count(row[i]):number(row[i])))};
    } catch {return null;}
  }
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
    const attempt={epoch,pc,counts:{offer:0,"remote-description":0,ice:0,connection:0,track:0,stats:0},timer:null,
      statsStarted:false,statsNextAt:Infinity,statsStalled:false,statsFrozen:false,statsBaseline:null,statsBefore:null};
    this.diagnostic=attempt;
    this.diagnosticView={attempt:epoch,state:"current",records:[],receiver:{attempt:epoch,state:"waiting",samples:[]},progress:{callback_count:0,latest_callback:null,
      presented:null,skipped:[0,0,0,0],reports:[],dropped:0}};
    try {const now=this.clock();attempt.statsNextAt=Number.isFinite(now)?now+3000:Infinity;
      attempt.timer=setTimeout(()=>this.sampleDiagnosticStats(attempt),3000);}catch{}
    return attempt;
  }
  sampleDiagnosticStats(attempt,trigger="startup") {
    let ticket,requested=null,callback=null,frames=null,media=null;
    const number=v=>typeof v==="number"&&Number.isFinite(v)&&v>=0&&v<=Number.MAX_SAFE_INTEGER?v:null;
    const record=(outcome,row=null,codec=null)=>{
      try {
        if(!this.diagnosticCurrent(attempt))return;
        const count=key=>{const value=row?.[key];return Number.isSafeInteger(value)&&value>=0?value:null;};
        const ice=this.diagnosticState(attempt.pc,true),connection=this.diagnosticState(attempt.pc);
        const sample=[trigger,outcome,requested,number(this.clock()),callback,frames,media,
          ...["packetsReceived","bytesReceived","framesReceived","framesDecoded","framesDropped","jitterBufferEmittedCount"].map(count),
          codec,ice,connection],receiver=this.diagnosticView.receiver;
        if(trigger==="stall"){
          receiver.samples=[attempt.statsBefore,sample].filter(Boolean).map(value=>value.slice());
          // A replacement can precede the next DOM observation. Carry only the
          // completed pair, under its own attempt, until a new pair completes.
          if(receiver.samples.length===2)this.receiverTransition={attempt:attempt.epoch,state:receiver.state,
            samples:receiver.samples.map(value=>value.slice())};
        }
        else if(!attempt.statsStalled&&outcome!=="busy"){
          attempt.statsBaseline=sample.slice();
          if(!attempt.statsFrozen){receiver.state="baseline";receiver.samples=[sample.slice()];}
        }
        if(trigger==="startup")this.diagnose(attempt,"stats",{outcome,ice_state:ice,
          connection_state:connection,packetsReceived:count("packetsReceived"),
          bytesReceived:count("bytesReceived"),framesReceived:count("framesReceived"),framesDecoded:count("framesDecoded"),
          jitterBufferEmittedCount:count("jitterBufferEmittedCount"),codec});
      }catch{}
    };
    try {
      if(!this.diagnosticCurrent(attempt))return;
      if(trigger==="startup"){
        if(attempt.statsStarted)return;
        attempt.statsStarted=true;clearTimeout(attempt.timer);attempt.timer=null;
      }
      requested=number(this.clock());callback=number(this.decodedAt);
      frames=Number.isSafeInteger(this.frames)&&this.frames>=0?this.frames:null;media=number(this.mediaTime);
      attempt.statsNextAt=requested===null?Infinity:requested+1000;
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
          this.progressDiagnostic("callback",null,{metadata,advanced:true});
          void this.report(); // Fresh decoded progress may take an already-due opportunity immediately.
        } else this.progressDiagnostic("callback",null,{metadata,advanced:false});
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
    // Receiver sampling observes existing report opportunities only. It cannot
    // refresh presentation, qualify view progress or add a second getStats slot.
    try {
      const attempt=this.diagnostic;
      if(attempt&&this.diagnosticCurrent(attempt)&&Number.isFinite(now)&&this.frames>0&&Number.isFinite(decodedAge)){
        if(decodedAge>=500&&!attempt.statsStalled){
          attempt.statsStalled=true;attempt.statsFrozen=true;attempt.statsBefore=attempt.statsBaseline?.slice()||null;
          this.diagnosticView.receiver.state="stalled";
          this.diagnosticView.receiver.samples=attempt.statsBefore?[attempt.statsBefore.slice()]:[];
          this.sampleDiagnosticStats(attempt,"stall");
        }else if(decodedAge>=0&&decodedAge<500){
          if(attempt.statsStalled){attempt.statsStalled=false;this.diagnosticView.receiver.state="presenting";}
          if(now>=attempt.statsNextAt)this.sampleDiagnosticStats(attempt,attempt.statsStarted?"baseline":"startup");
        }
      }
    }catch{} // Diagnostic scheduling cannot enter the report state machine.
    // Cached browser source status can lag; Gateway independently qualifies
    // current Pi source identity/arrival. Publish only genuinely current decode.
    if(!this.options.progress||!this.binding||this.frames<1||!Number.isFinite(decodedAge)||decodedAge<0||decodedAge>=500){
      this.progressDiagnostic("skip",null,"decode");return idle();}
    if(now-this.lastReportAt<150||this.frames<=this.lastReportedFrames||this.mediaTime<=this.lastReportedMedia){
      this.progressDiagnostic("skip",null,now-this.lastReportAt<150?"cadence":"no_presentation");
      return latest?.epoch===this.epoch&&!latest.cancelled?latest.promise:idle();}
    if(this.reports.size>=2) {
      this.progressDiagnostic("skip",null,"slots");
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
      this.progressDiagnostic("cancel",slot,reason);
      if(active&&reason==="timeout")this.error="View progress delivery unavailable";
    };
    slot.timer=setTimeout(()=>slot.cancel("timeout"),750);
    this.reports.set(id,slot);this.latestReport=id;
    this.lastReportAt=now;this.lastReportedFrames=body.decoded_frames;this.lastReportedMedia=body.media_time;
    this.updateReporting();this.progressDiagnostic("report",slot,body);
    const work=(async()=>{
      try {
        this.progressDiagnostic("fetch",slot);
        const response=await fetch(this.options.progress,{method:"POST",credentials:"same-origin",redirect:"error",
          headers:{"Content-Type":"application/json","X-AM1-CSRF":this.options.csrf?.()||""},
          body:JSON.stringify(body),signal:controller.signal});
        this.progressDiagnostic("headers",slot,response);
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
        const active=current();if(active)this.progressReceipt=receipt;
        this.progressDiagnostic("receipt",slot,{receipt,current:active});
      } catch {if(current())this.error="View progress delivery unavailable";this.progressDiagnostic("error",slot);}
      finally {slot.done=true;clearTimeout(slot.timer);this.reports.delete(id);this.updateReporting();this.progressDiagnostic("settled",slot);}
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
