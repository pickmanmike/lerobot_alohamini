"use strict";
// Small native WHEP client, authored for this single same-origin view. No third-party reader.
class AM1P1View {
  constructor(video,csrf,status) {
    this.video=video;this.csrf=csrf;this.status=status;this.pc=null;this.handle=null;
    this.etag=null;this.epoch=0;this.pending=[];this.flushing=false;this.lease=null;
  }
  async request(path,method,body,type="application/trickle-ice-sdpfrag",extra={}) {
    if(!/^\/api\/media(?:\/[A-Za-z0-9_-]+(?:\/keepalive)?)?$/.test(path))throw Error("Invalid local video resource");
    const response=await fetch(path,{method,credentials:"same-origin",redirect:"error",
      headers:{"Content-Type":type,"X-AM1-CSRF":this.csrf(),...extra},body,keepalive:method==="DELETE"});
    if(!response.ok)throw Error("Optional P1 video unavailable");
    return response;
  }
  fragment(candidates) {
    const sections=this.offer.split("m=");
    const ufrag=this.offer.match(/^a=ice-ufrag:(.+)$/m)?.[1]?.trim();
    const pwd=this.offer.match(/^a=ice-pwd:(.+)$/m)?.[1]?.trim();
    if(!ufrag||!pwd)throw Error("Video ICE credentials unavailable");
    let fragment=`a=ice-ufrag:${ufrag}\r\na=ice-pwd:${pwd}\r\n`;
    for(let index=1;index<sections.length;index++) {
      const matching=candidates.filter(c=>c.sdpMLineIndex===index-1);
      if(!matching.length)continue;
      const media=sections[index].split("\r\n")[0];
      const mid=sections[index].match(/^a=mid:(.+)$/m)?.[1]?.trim();
      fragment+=`m=${media}\r\na=mid:${mid}\r\n`;
      for(const candidate of matching)fragment+=`a=${candidate.candidate}\r\n`;
    }
    return fragment;
  }
  async flush() {
    if(this.flushing||!this.handle||!this.pending.length)return;
    this.flushing=true;const epoch=this.epoch;
    try {
      while(epoch===this.epoch&&this.pending.length) {
        const response=await this.request(this.handle,"PATCH",this.fragment(this.pending.splice(0)),undefined,{"If-Match":this.etag||"*"});
        if(epoch===this.epoch)this.etag=response.headers.get("ETag")||this.etag;
      }
    } catch(error) {if(epoch===this.epoch)this.status(error.message);}
    finally {this.flushing=false;}
  }
  async start() {
    await this.stop();const epoch=++this.epoch;
    this.status("Connecting optional P1 video…");
    const pc=this.pc=new RTCPeerConnection({iceServers:[]});
    pc.addTransceiver("video",{direction:"recvonly"});
    pc.ontrack=event=>{if(epoch===this.epoch){this.video.srcObject=new MediaStream([event.track]);this.video.play().catch(()=>{});this.status("Optional P1 live view · required image proof is evaluated separately on Pi");}};
    pc.onconnectionstatechange=()=>{if(epoch===this.epoch&&["failed","disconnected"].includes(pc.connectionState))this.status("Optional video disconnected · reconnect view; task observation is independent");};
    pc.onicecandidate=event=>{if(epoch!==this.epoch||!event.candidate)return;if(this.pending.length<64){this.pending.push(event.candidate);this.flush();}};
    try {
      const offer=await pc.createOffer();this.offer=offer.sdp;
      await pc.setLocalDescription(offer);
      const response=await this.request("/api/media","POST",offer.sdp,"application/sdp");
      const handle=response.headers.get("Location");
      if(!/^\/api\/media\/[A-Za-z0-9_-]{32}$/.test(handle))throw Error("Invalid local video resource");
      if(epoch!==this.epoch){await this.request(handle,"DELETE","");return;}
      this.handle=handle;this.etag=response.headers.get("ETag");
      await pc.setRemoteDescription({type:"answer",sdp:await response.text()});
      await this.flush();
      if(epoch!==this.epoch)return;
      this.lease=setInterval(()=>this.request(this.handle+"/keepalive","POST","{}","application/json").catch(()=>this.status("Optional video session unavailable · reconnect view")),20000);
    } catch(error) {
      if(epoch===this.epoch){await this.stop();this.status(error.message);}
    }
  }
  async stop() {
    this.epoch++;clearInterval(this.lease);this.lease=null;
    const handle=this.handle;this.handle=null;this.pending=[];
    this.pc?.close();this.pc=null;this.video.srcObject=null;
    if(handle)try{await this.request(handle,"DELETE","");}catch(_){/* server lease reclaims lost replies */}
  }
}
globalThis.AM1P1View=AM1P1View;
