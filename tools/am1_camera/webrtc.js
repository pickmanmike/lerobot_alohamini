"use strict";
// Native go2rtc SDP exchange. Connected transport is never a decoded-frame clock.
globalThis.AM1OnboardView=class {
  constructor(video,options) {
    this.video=video;this.options=options;this.clock=options.clock||(()=>performance.now());
    this.pc=null;this.epoch=0;this.binding=null;this.source=null;this.sourceAt=0;
    this.decodedAt=null;this.frames=0;this.mediaTime=-1;this.callback=null;
    this.reporting=false;this.timer=null;this.connecting=false;this.error=null;
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
    pc.addTransceiver("video",{direction:"recvonly"});
    pc.ontrack=event=>{if(epoch===this.epoch){this.video.srcObject=new MediaStream([event.track]);this.video.play().catch(()=>{});}};
    pc.onconnectionstatechange=()=>{if(epoch===this.epoch&&["failed","disconnected"].includes(pc.connectionState))this.error="WebRTC view disconnected";};
    try {
      await pc.setLocalDescription(await pc.createOffer());
      if(pc.iceGatheringState!=="complete")await new Promise((resolve,reject)=>{
        const timeout=setTimeout(()=>reject(Error("Bounded LAN ICE gathering timed out")),2000);
        pc.onicegatheringstatechange=()=>{if(pc.iceGatheringState==="complete"){clearTimeout(timeout);resolve();}};
      });
      if(epoch!==this.epoch)return;
      const response=await fetch(this.options.offer(role),{method:"POST",credentials:"same-origin",redirect:"error",
        headers:{"Content-Type":"application/sdp","X-AM1-CSRF":this.options.csrf?.()||""},
        body:pc.localDescription.sdp,signal:AbortSignal.timeout(5000)});
      if(!response.ok)throw Error(`Onboard WebRTC unavailable (${response.status})`);
      const binding={role,owner_generation:response.headers.get("X-Camera-Owner-Generation"),
        capture_generation:response.headers.get("X-Camera-Capture-Generation"),
        encode_generation:response.headers.get("X-Camera-Encode-Generation")};
      if(Object.values(binding).some(value=>typeof value!=="string"||!value||value.length>128))throw Error("Current video source identity unavailable");
      const answer=await response.text();
      if(epoch!==this.epoch)return;
      await pc.setRemoteDescription({type:"answer",sdp:answer});
      if(epoch!==this.epoch)return;
      this.binding=binding;this.connecting=false;
      const decoded=(_now,metadata)=>{
        if(epoch!==this.epoch)return;
        if(Number.isSafeInteger(metadata.presentedFrames)&&metadata.presentedFrames>this.frames&&
           Number.isFinite(metadata.mediaTime)&&metadata.mediaTime>this.mediaTime) {
          this.frames=metadata.presentedFrames;this.mediaTime=metadata.mediaTime;this.decodedAt=this.clock();
        }
        this.callback=this.video.requestVideoFrameCallback(decoded);
      };
      this.callback=this.video.requestVideoFrameCallback(decoded);
      this.timer=setInterval(()=>void this.report(),150);
    } catch(error) {
      if(epoch===this.epoch){await this.stop();this.error=error.message;throw error;}
    }
  }
  async report() {
    if(!this.options.progress||this.reporting||!this.binding||this.frames<1)return;
    this.reporting=true;const epoch=this.epoch;
    const body={...this.binding,decoded_frames:this.frames,media_time:this.mediaTime};
    try {
      await fetch(this.options.progress,{method:"POST",credentials:"same-origin",redirect:"error",
        headers:{"Content-Type":"application/json","X-AM1-CSRF":this.options.csrf?.()||""},
        body:JSON.stringify(body),signal:AbortSignal.timeout(750)});
    } catch {if(epoch===this.epoch)this.error="View progress delivery unavailable";}
    finally {this.reporting=false;}
  }
  async stop() {
    this.epoch++;clearInterval(this.timer);this.timer=null;
    if(this.callback!==null)this.video.cancelVideoFrameCallback?.(this.callback);
    this.callback=null;this.pc?.close();this.pc=null;this.video.srcObject=null;
    this.binding=null;this.decodedAt=null;this.frames=0;this.mediaTime=-1;this.connecting=false;
  }
};
