"use strict";
const test=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm"),path=require("node:path");
function fixture() {
  const file=path.resolve(__dirname,"../../tools/am1_camera/webrtc.js");
  assert.ok(fs.existsSync(file),"bounded native onboard WebRTC player missing");
  let now=0,progressResponse=null;const callbacks=[],requests=[];
  const video={srcObject:null,requestVideoFrameCallback(fn){callbacks.push(fn);return callbacks.length;},cancelVideoFrameCallback(){},play:async()=>{}};
  class Peer {
    constructor(){this.iceGatheringState="complete";this.connectionState="connected";}
    addTransceiver(){} async createOffer(){return {type:"offer",sdp:"v=0\r\n"};}
    async setLocalDescription(value){this.localDescription=value;} async setRemoteDescription(){} close(){this.closed=true;}
  }
  const ids={owner_generation:"owner-a",capture_generation:"capture-a",encode_generation:"encode-a",view_generation:"11111111-1111-4111-8111-111111111111"};
  const context=vm.createContext({RTCPeerConnection:Peer,MediaStream:class {},performance:{now:()=>now},
    setInterval:()=>1,clearInterval(){},setTimeout,clearTimeout,AbortSignal,AbortController,
    fetch:async(url,options)=>{requests.push({url,...options});if(url.endsWith("/progress")&&progressResponse)return typeof progressResponse==="function"?progressResponse(options):progressResponse;return {ok:true,headers:{get:name=>({
      "X-Camera-Owner-Generation":ids.owner_generation,"X-Camera-Capture-Generation":ids.capture_generation,
      "X-Camera-Encode-Generation":ids.encode_generation,"X-Camera-View-Generation":ids.view_generation}[name]||null)},text:async()=>"v=0\r\n",json:async()=>({accepted:true})};}});
  vm.runInContext(fs.readFileSync(file,"utf8"),context);
  const view=new context.AM1OnboardView(video,{offer:role=>"/api/onboard/webrtc/"+role,
    progress:"/api/onboard/progress",csrf:()=>"csrf",clock:()=>now});
  function source(role="chest") {view.updateSource({owner_generation:ids.owner_generation,
    cameras:{[role]:{configured:true,state:"fresh",age_ms:0,sequence:12,capture_generation:ids.capture_generation}},
    video:{role,state:"running",encode_generation:ids.encode_generation,capture_generation:ids.capture_generation}},now);}
  return {view,video,callbacks,requests,ids,source,advance:value=>{now=value;},setProgressResponse:value=>{progressResponse=value;}};
}
test("a connected video and ontrack never qualify an undecoded primary view",async()=>{
  const p=fixture();await p.view.start("chest");p.source();p.view.pc.ontrack({track:{}});
  assert.equal(p.view.health().fresh,false);
  p.callbacks.shift()(0,{presentedFrames:1,mediaTime:0});assert.equal(p.view.health().fresh,true);
  await p.view.report();const request=p.requests.find(r=>r.url.endsWith("/progress"));
  assert.equal(request.headers["X-AM1-CSRF"],"csrf");
  assert.deepEqual(JSON.parse(request.body),{role:"chest",owner_generation:"owner-a",capture_generation:"capture-a",
    encode_generation:"encode-a",view_generation:p.ids.view_generation,decoded_frames:1,media_time:0});
  await p.view.stop();
});
test("advancing source metadata and repeated media time cannot renew a frozen decoder",async()=>{
  const p=fixture();await p.view.start("chest");p.source();
  p.callbacks.shift()(0,{presentedFrames:1,mediaTime:0});
  p.advance(400);p.source();p.callbacks.shift()(400,{presentedFrames:2,mediaTime:0});
  p.advance(501);p.source();assert.equal(p.view.health().fresh,false);
  assert.equal(p.view.health().decoded_age_ms,501);
  await p.view.stop();
});
test("view switching rejects old callbacks and capture replacement invalidates current decode",async()=>{
  const p=fixture();await p.view.start("chest");p.source();const old=p.callbacks.shift();
  p.ids.encode_generation="encode-b";await p.view.start("forward");p.source("forward");
  old(0,{presentedFrames:300,mediaTime:30});assert.equal(p.view.health().fresh,false);
  p.callbacks.shift()(0,{presentedFrames:1,mediaTime:0});assert.equal(p.view.health().fresh,true);
  p.ids.capture_generation="capture-b";p.source("forward");assert.equal(p.view.health().fresh,false);
  assert.ok(p.requests.every(request=>!request.url.includes("?")));
  await p.view.stop();
});

test("loaded camera page selects WebRTC and switches roles without any control request",async t=>{
  const {chromium}=require("playwright"),http=require("node:http"),{randomUUID}=require("node:crypto");
  const directory=path.resolve(__dirname,"../../tools/am1_camera"),requests=[],errors=[];
  const roles=["forward","backward","chest","wrist_left","wrist_right"],owner=randomUUID();
  const captures=Object.fromEntries(roles.map(role=>[role,randomUUID()]));
  let selected=null,generation=null,sequence=1;
  const server=http.createServer(async(request,response)=>{
    requests.push({method:request.method,path:request.url});
    const reply=(status,type,body,headers={})=>{response.writeHead(status,{"Content-Type":type,...headers});response.end(body);};
    if(request.url==="/api/onboard/status")return reply(200,"application/json",JSON.stringify({owner_generation:owner,
      cameras:Object.fromEntries(roles.map(role=>[role,{configured:true,state:"fresh",age_ms:0,sequence:sequence++,
        capture_generation:captures[role],rotation_degrees:role==="wrist_right"?270:180}])),
      video:{enabled:true,transport:"webrtc",role:selected,encode_generation:generation,
        capture_generation:captures[selected],state:selected?"running":"unavailable"}}));
    if(request.url.startsWith("/api/onboard/webrtc/")) {
      const role=request.url.split("/").at(-1);if(role!==selected){selected=role;generation=randomUUID();}
      for await(const _ of request){};
      return reply(201,"application/sdp","v=0\r\n",{"X-Camera-Owner-Generation":owner,
        "X-Camera-Capture-Generation":captures[role],"X-Camera-Encode-Generation":generation,"X-Camera-View-Generation":randomUUID()});
    }
    if(request.url==="/api/onboard/progress"){for await(const _ of request){};return reply(200,"application/json",'{"accepted":true}');}
    if(request.url.startsWith("/api/"))return reply(503,"text/plain","fixture thumbnail unavailable");
    const name=request.url==="/"?"index.html":request.url.slice(1);
    if(!["index.html","app.js","webrtc.js","freshness.js","mjpeg.js","style.css"].includes(name))return reply(404,"text/plain","");
    let body=fs.readFileSync(path.join(directory,name));
    if(name==="index.html")body=body.toString().replace('data-camera-base="/"','data-camera-base="/api/onboard/" data-onboard="true"');
    reply(200,name.endsWith("js")?"text/javascript":name.endsWith("css")?"text/css":"text/html",body);
  });
  await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));t.after(()=>new Promise(resolve=>server.close(resolve)));
  const browser=await chromium.launch({headless:true,channel:process.env.AM1_TEST_BROWSER_CHANNEL||"msedge"});t.after(()=>browser.close());
  const page=await browser.newPage();page.on("pageerror",error=>errors.push(error.message));
  await page.addInitScript(()=>{
    globalThis.am1Remote={csrf:"fixture"};
    globalThis.RTCPeerConnection=class {constructor(){this.iceGatheringState="complete";}addTransceiver(){}close(){}
      async createOffer(){return {type:"offer",sdp:"v=0\r\n"};}async setLocalDescription(value){this.localDescription=value;}async setRemoteDescription(){}};
    // Protocol fixture only: synthesize decoder callbacks without claiming actual camera/media proof.
    const counts=new WeakMap();
    HTMLVideoElement.prototype.requestVideoFrameCallback=function(callback){const count=(counts.get(this)||0)+1;counts.set(this,count);
      return setTimeout(()=>callback(performance.now(),{presentedFrames:count,mediaTime:count/10}),50);};
    HTMLVideoElement.prototype.cancelVideoFrameCallback=function(id){clearTimeout(id);};
  });
  await page.goto("http://127.0.0.1:"+server.address().port);
  await page.waitForFunction(()=>AM1CameraHealth().transport==="webrtc"&&AM1CameraHealth().video?.fresh);
  await page.evaluate(()=>AM1CameraSelect("wrist_right"));
  await page.waitForFunction(()=>AM1CameraHealth().selected_role==="wrist_right"&&AM1CameraHealth().video?.fresh);
  assert.equal(await page.locator("#primary video").getAttribute("data-rotation"),"270");
  await page.evaluate(()=>AM1CameraReconnect("wrist_right"));
  await page.waitForFunction(()=>AM1CameraHealth().video?.fresh);
  assert.ok(requests.some(request=>request.path==="/api/onboard/webrtc/wrist_right"));
  assert.ok(requests.some(request=>request.path==="/api/onboard/progress"));
  assert.ok(!requests.some(request=>/\/api\/(claim|start|connect|renew|input|stop|pause)(?:\/|$)/.test(request.path)));
  assert.ok(!requests.some(request=>request.path.includes("stream.mjpeg")||request.path.includes("/mjpeg/")));
  assert.deepEqual(errors,[]);
  await page.close();
});


test("view progress consumes the bounded logical body before releasing its receipt slot",async()=>{
  const p=fixture();await p.view.start("chest");p.source();
  let release,read=false;
  const body=JSON.stringify({accepted:true,status:"view_progress",effect_admitted:false,
    required_view:{required:true,qualified:true,role:"chest",cause:null},view_delivery:{version:1}});
  p.view.options.progress="/api/onboard/progress";
  // Replace only HTTP transport; the product reporting state machine remains real.
  const response={ok:true,headers:{get:name=>name.toLowerCase()==="content-type"?"application/json":String(body.length)},
    text:()=>{read=true;return new Promise(resolve=>{release=()=>resolve(body);});}};
  p.setProgressResponse(response);
  p.callbacks.shift()(0,{presentedFrames:1,mediaTime:.1});
  const pending=p.view.report();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(read,true);assert.equal(p.view.reporting,true);assert.equal(p.view.progressReceipt,null);
  release();await pending;
  assert.equal(p.view.reporting,false);assert.equal(p.view.progressReceipt.accepted,true);
  assert.equal(p.view.progressReceipt.required_view.qualified,true);
  assert.equal(p.view.health().fresh,true);
  await p.view.stop();assert.equal(p.view.progressReceipt,null);
});

for(const mode of ["refused","malformed","oversized","http-error","stale-epoch"])test("view progress receipt contrast: "+mode,async()=>{
  const p=fixture();await p.view.start("chest");p.source();
  let text=mode==="malformed"?"not-json":JSON.stringify({accepted:false,reason:"decoded video has not advanced"});
  const response={ok:mode!=="http-error",headers:{get:name=>name.toLowerCase()==="content-type"?"application/json":mode==="oversized"?"8193":String(text.length)},
    text:async()=>{if(mode==="stale-epoch")await p.view.stop();return text;}};
  p.setProgressResponse(response);p.callbacks.shift()(0,{presentedFrames:1,mediaTime:.1});await p.view.report();
  if(mode==="refused"){
    assert.equal(p.view.progressReceipt.accepted,false);
    assert.equal(p.view.progressReceipt.reason,"decoded video has not advanced");
    assert.equal(p.view.health().fresh,true); // A response cannot fabricate or clear original decode/source clocks.
    p.advance(501);assert.equal(p.view.health().fresh,false);
  }else assert.equal(p.view.progressReceipt,null);
  assert.equal(p.view.reporting,false);await p.view.stop();
});


// Actual local media characterization: canvas pixels and real browser WebRTC/rVFC,
// never camera, motor, network configuration, or a presentation-clock substitute.
test("actual video keeps presented-frame proof across the ordinary primary screenshot",{timeout:35000},async t=>{
 const {chromium}=require("playwright"),directory=path.resolve(__dirname,"../../tools/am1_camera");
 const browser=await chromium.launch({headless:true,channel:process.env.AM1_TEST_BROWSER_CHANNEL||"msedge"});
 t.after(()=>browser.close());const page=await browser.newPage({viewport:{width:1400,height:1000}});
 const requests=[],errors=[];page.on("pageerror",error=>errors.push(error.message));
 await page.addInitScript(()=>{
  globalThis.am1Remote={csrf:"offline"};globalThis.offlineFrames=[];globalThis.offlinePeers=[];globalThis.offlineSourceTicks=[];
  globalThis.offlineReadStats=async()=>{const peer=offlinePeers[0];if(!peer)return null;
   const rows=await peer.getStats();let result=null;rows.forEach(row=>{if(row.type==="inbound-rtp"&&row.kind==="video")
    result={frames_received:row.framesReceived??null,frames_decoded:row.framesDecoded??null,frames_dropped:row.framesDropped??null,
     total_decode_time_s:row.totalDecodeTime??null,jitter_buffer_delay_s:row.jitterBufferDelay??null,
     jitter_buffer_emitted_count:row.jitterBufferEmittedCount??null,jitter_s:row.jitter??null};});return result;};
  const Original=RTCPeerConnection;globalThis.RTCPeerConnection=new Proxy(Original,{construct(Target,args,newTarget){
   const peer=Reflect.construct(Target,args,newTarget);offlinePeers.push(peer);return peer;}});
  const original=HTMLVideoElement.prototype.requestVideoFrameCallback;
  HTMLVideoElement.prototype.requestVideoFrameCallback=function(callback){return original.call(this,function(now,metadata){
   if(offlineFrames.length<600)offlineFrames.push({at:performance.now(),presented:metadata.presentedFrames,media:metadata.mediaTime,
    presentation_time:metadata.presentationTime??null,expected_display_time:metadata.expectedDisplayTime??null,
    processing_duration:metadata.processingDuration??null,capture_time:metadata.captureTime??null,receive_time:metadata.receiveTime??null,
    source_ticks:offlineSourceTicks.length});
   return Reflect.apply(callback,this,[now,metadata]);});};
 });
 const roles=["forward","backward","chest","wrist_left","wrist_right"],owner="offline-owner",capture="offline-capture",encode="offline-encode";
 await page.route("https://am1-offline.invalid/**",async route=>{
  const request=route.request(),url=new URL(request.url());requests.push(url.pathname);
  const reply=(status,contentType,body,headers={})=>route.fulfill({status,contentType,body,headers});
  if(url.pathname==="/api/onboard/status")return reply(200,"application/json",JSON.stringify({owner_generation:owner,
   cameras:Object.fromEntries(roles.map(role=>[role,{configured:role==="chest",state:role==="chest"?"fresh":"unavailable",age_ms:0,
    sequence:Math.max(1,Math.floor(Date.now()/67)),capture_generation:capture,rotation_degrees:0}])),
   video:{enabled:true,transport:"webrtc",role:"chest",encode_generation:encode,capture_generation:capture,state:"running"}}));
  if(url.pathname==="/api/onboard/webrtc/chest"){
   const answer=await page.evaluate(async offer=>{
    const canvas=document.createElement("canvas");canvas.width=640;canvas.height=480;
    const context=canvas.getContext("2d");let frame=0;
    const timer=setInterval(()=>{if(offlineSourceTicks.length<600)offlineSourceTicks.push(performance.now());
     context.fillStyle=frame++%2?"#185388":"#aa5522";context.fillRect(0,0,640,480);
     context.fillStyle="white";context.fillText(String(frame),40,40);},1000/15);
    const stream=canvas.captureStream(15),sender=new RTCPeerConnection({iceServers:[]});
    stream.getTracks().forEach(track=>sender.addTrack(track,stream));
    await sender.setRemoteDescription({type:"offer",sdp:offer});await sender.setLocalDescription(await sender.createAnswer());
    if(sender.iceGatheringState!=="complete")await new Promise((resolve,reject)=>{
     const timer=setTimeout(()=>reject(Error("offline answer ICE deadline")),2000);
     sender.onicegatheringstatechange=()=>{if(sender.iceGatheringState==="complete"){clearTimeout(timer);resolve();}};
    });
    globalThis.offlineMedia={sender,stream,timer};return sender.localDescription.sdp;
   },request.postData());
   return reply(201,"application/sdp",answer,{"X-Camera-Owner-Generation":owner,"X-Camera-Capture-Generation":capture,"X-Camera-Encode-Generation":encode,"X-Camera-View-Generation":require("node:crypto").randomUUID()});
  }
  if(url.pathname==="/api/onboard/progress")return reply(200,"application/json",'{"accepted":true}');
  if(url.pathname.startsWith("/api/"))return reply(503,"text/plain","offline unassigned source");
  const name=url.pathname==="/"?"index.html":url.pathname.slice(1);
  if(!["index.html","app.js","webrtc.js","freshness.js","mjpeg.js","style.css"].includes(name))return reply(404,"text/plain","");
  let body=fs.readFileSync(path.join(directory,name));
  if(name==="index.html")body=body.toString().replace('<body>','<body data-console="compact">')
   .replace('data-camera-base="/"','data-camera-base="/api/onboard/" data-onboard="true"');
  return reply(200,name.endsWith("js")?"text/javascript":name.endsWith("css")?"text/css":"text/html",body);
 });
 await page.goto("https://am1-offline.invalid");
 await page.addStyleTag({path:path.resolve(__dirname,"../../tools/am1_console_ui/style.css")});
 await page.waitForFunction(()=>typeof AM1CameraHealth==="function");await page.evaluate(()=>AM1CameraSelect("chest"));
 await page.waitForFunction(()=>AM1CameraHealth().video?.fresh&&AM1CameraHealth().video.decoded_frames>=5,null,{timeout:7000});
 const started=await page.evaluate(()=>{globalThis.offlineFirstStall=null;let observed=false;
  globalThis.offlineRaf={callbacks:0,last_at:null,max_gap_ms:0,worst_gap:null};globalThis.offlineRafActive=true;
  const heartbeat=()=>{if(!offlineRafActive)return;const at=performance.now(),prior=offlineRaf.last_at;
   if(prior!==null&&at-prior>offlineRaf.max_gap_ms){offlineRaf.max_gap_ms=at-prior;
    offlineRaf.worst_gap={before_at:prior,at,gap_ms:at-prior,source_ticks:offlineSourceTicks.length,
     last_rvfc_at:offlineFrames.at(-1)?.at??null};}
   offlineRaf.callbacks=Math.min(100000,offlineRaf.callbacks+1);offlineRaf.last_at=at;
   globalThis.offlineRafId=requestAnimationFrame(heartbeat);};globalThis.offlineRafId=requestAnimationFrame(heartbeat);
  globalThis.offlineStallTimer=setInterval(async()=>{const h=AM1CameraHealth().video;
   if(observed||!h?.decoded_frames||h.decoded_age_ms<500)return;observed=true;
   offlineFirstStall={at:performance.now(),health:h,source_ticks:offlineSourceTicks.length,last_source_tick:offlineSourceTicks.at(-1),
    raf:{...offlineRaf,last_age_ms:offlineRaf.last_at===null?null:performance.now()-offlineRaf.last_at},
    stats:await offlineReadStats()};},100);return performance.now();});await page.waitForTimeout(15000);
 const screenshotStarted=await page.evaluate(()=>performance.now());
 const image=await page.locator("#primary").screenshot({timeout:5000});
 const screenshotEnded=await page.evaluate(()=>performance.now());assert.ok(image.length>1000);
 await page.waitForTimeout(5000);
 const result=await page.evaluate(async({started,screenshotStarted,screenshotEnded})=>{
  const rows=offlineFrames.filter(row=>row.at>=started),ordinary=[],during=[];
  for(let i=1;i<rows.length;i++){const gap=rows[i].at-rows[i-1].at;
   (rows[i-1].at<=screenshotEnded&&rows[i].at>=screenshotStarted?during:ordinary).push(gap);}
  const health=AM1CameraHealth(),dom=document.querySelector("#primary video"),style=getComputedStyle(dom),rect=dom.getBoundingClientRect();
  const sourceTicks=offlineSourceTicks.filter(at=>at>=started),sourceGaps=sourceTicks.slice(1).map((at,i)=>at-sourceTicks[i]);
  const gaps=rows.slice(1).map((row,i)=>({gap:row.at-rows[i].at,before:rows[i],after:row})).sort((a,b)=>b.gap-a.gap);
  const stats={frames:rows.length,first:rows[0],last:rows.at(-1),max_ordinary_gap_ms:Math.max(0,...ordinary),
   max_screenshot_gap_ms:Math.max(0,...during),screenshot_ms:screenshotEnded-screenshotStarted,
   health,source_ticks:sourceTicks.length,max_source_tick_gap_ms:Math.max(0,...sourceGaps),largest_frame_gap:gaps[0],
   first_stall:offlineFirstStall,raf:{...offlineRaf},final_stats:await offlineReadStats(),
   dom:{hidden:dom.hidden,display:style.display,visibility:style.visibility,width:rect.width,height:rect.height}};
  clearInterval(offlineMedia.timer);clearInterval(offlineStallTimer);globalThis.offlineRafActive=false;cancelAnimationFrame(offlineRafId);offlineMedia.stream.getTracks().forEach(track=>track.stop());offlinePeers.forEach(peer=>peer.close());return stats;
 },{started,screenshotStarted,screenshotEnded});
 assert.ok(result.frames>100,"real presented frames must keep advancing during the bounded characterization");
 assert.ok(result.last.presented>result.first.presented);assert.ok(result.last.media>result.first.media);
 assert.equal(result.health.video.fresh,true);assert.equal(result.dom.hidden,false);assert.notEqual(result.dom.display,"none");
 assert.ok(!requests.some(value=>/^\/api\/(start|claim|stop|command)$/.test(value)));assert.deepEqual(errors,[]);
 t.diagnostic(JSON.stringify({actual_video:true,width:1400,height:1000,source_fps:15,steady_seconds:20,
  frames:result.frames,first:result.first,last:result.last,max_ordinary_gap_ms:result.max_ordinary_gap_ms,
  max_screenshot_gap_ms:result.max_screenshot_gap_ms,screenshot_ms:result.screenshot_ms,dom:result.dom,
  source_ticks:result.source_ticks,max_source_tick_gap_ms:result.max_source_tick_gap_ms,
  largest_frame_gap:result.largest_frame_gap,first_stall:result.first_stall,raf:result.raf,final_stats:result.final_stats}));
 await page.close();
});


// One camera-only discriminating check. A delayed status publication is synthetic;
// all video uses real browser RTP/decoder/RVFC and unchanged production assets.
test("actual video separates delayed source publication from explicit stale reoffer",{timeout:45000},async t=>{
 const {chromium}=require("playwright"),directory=path.resolve(__dirname,"../../tools/am1_camera");
 const browser=await chromium.launch({headless:true,channel:process.env.AM1_TEST_BROWSER_CHANNEL||"msedge"});
 t.after(()=>browser.close());const results=[];
 for(const policy of ["existing-stale-reoffer","retain-peer-test-only"]){
  const page=await browser.newPage({viewport:{width:1400,height:1000}}),errors=[],offers=[],status=[];
  page.on("pageerror",error=>errors.push(error.message));let delayNextStatus=false,delayed=false;
  await page.addInitScript(()=>{
   globalThis.am1Remote={csrf:"offline"};globalThis.probePeers=[];globalThis.probeSenders=[];
   globalThis.probeFrames=[];globalThis.probeTicks=[];globalThis.probeSamples=[];
   const Native=RTCPeerConnection;globalThis.RTCPeerConnection=new Proxy(Native,{construct(Target,args,newTarget){
    const peer=Reflect.construct(Target,args,newTarget);probePeers.push(peer);return peer;}});
   const original=HTMLVideoElement.prototype.requestVideoFrameCallback;
   HTMLVideoElement.prototype.requestVideoFrameCallback=function(callback){return original.call(this,function(now,m){
    if(probeFrames.length<600)probeFrames.push({at:performance.now(),presented:m.presentedFrames,media:m.mediaTime,
     presentation:m.presentationTime,expected_display:m.expectedDisplayTime,receive:m.receiveTime??null,
     rtp:m.rtpTimestamp??null,processing:m.processingDuration??null});
    return Reflect.apply(callback,this,[now,m]);});};
   globalThis.probeSnapshot=async()=>{
    const at=performance.now(),health=AM1CameraHealth().video;
    const peer=probePeers.filter(p=>!probeSenders.includes(p)).at(-1),id=probePeers.indexOf(peer);
    let inbound=null;
    if(peer&&peer.connectionState!=="closed"){
     const rows=await peer.getStats();rows.forEach(row=>{if(row.type==="inbound-rtp"&&row.kind==="video")
      inbound={timestamp:row.timestamp,packets:row.packetsReceived??null,bytes:row.bytesReceived??null,
       received:row.framesReceived??null,decoded:row.framesDecoded??null,rendered:row.framesRendered??null,
       emitted:row.jitterBufferEmittedCount??null,decode_s:row.totalDecodeTime??null};});
    }
    return {at,completed_at:performance.now(),health,peer_id:id,peer_state:peer?.connectionState??null,
     inbound,last_rvfc:probeFrames.at(-1)??null,source_tick:probeTicks.at(-1)??null,source_ticks:probeTicks.length};
   };
  });
  const roles=["forward","backward","chest","wrist_left","wrist_right"];
  await page.route("https://am1-publication.invalid/**",async route=>{
   const request=route.request(),url=new URL(request.url());
   const reply=(code,mime,body,headers={})=>route.fulfill({status:code,contentType:mime,body,headers});
   if(url.pathname==="/api/onboard/status"){
    const sampled=await page.evaluate(()=>({at:performance.now(),tick:probeTicks.at(-1)??performance.now(),sequence:Math.max(1,probeTicks.length)}));
    const slow=delayNextStatus&&!delayed;if(slow){delayed=true;await new Promise(resolve=>setTimeout(resolve,650));}
    const delivered=await page.evaluate(()=>performance.now());status.push({sampled_at:sampled.at,delivered_at:delivered,synthetic_delayed:slow});
    return reply(200,"application/json",JSON.stringify({owner_generation:"offline-owner",
     cameras:Object.fromEntries(roles.map(role=>[role,{configured:role==="chest",state:role==="chest"?"fresh":"unavailable",
      age_ms:Math.max(0,sampled.at-sampled.tick),sequence:sampled.sequence,capture_generation:"offline-capture",rotation_degrees:0}])),
     video:{enabled:true,transport:"webrtc",role:"chest",encode_generation:"offline-encode",capture_generation:"offline-capture",state:"running"}}));
   }
   if(url.pathname==="/api/onboard/webrtc/chest"){
    const at=await page.evaluate(()=>performance.now());
    const answer=await page.evaluate(async offer=>{
     if(!globalThis.probeMedia){
      const canvas=document.createElement("canvas");canvas.width=640;canvas.height=480;
      const context=canvas.getContext("2d");let frame=0;
      const timer=setInterval(()=>{if(probeTicks.length<600)probeTicks.push(performance.now());
       context.fillStyle=frame++%2?"#185388":"#aa5522";context.fillRect(0,0,640,480);context.fillStyle="white";context.fillText(String(frame),40,40);},1000/15);
      globalThis.probeMedia={stream:canvas.captureStream(15),timer};
     }
     const sender=new RTCPeerConnection({iceServers:[]});probeSenders.push(sender);
     probeMedia.stream.getTracks().forEach(track=>sender.addTrack(track,probeMedia.stream));
     await sender.setRemoteDescription({type:"offer",sdp:offer});await sender.setLocalDescription(await sender.createAnswer());
     if(sender.iceGatheringState!=="complete")await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(Error("offline answer ICE deadline")),2000);
      sender.onicegatheringstatechange=()=>{if(sender.iceGatheringState==="complete"){clearTimeout(timer);resolve();}};
     });return sender.localDescription.sdp;
    },request.postData());
    offers.push({at,answered_at:await page.evaluate(()=>performance.now())});
    return reply(201,"application/sdp",answer,{"X-Camera-Owner-Generation":"offline-owner","X-Camera-Capture-Generation":"offline-capture","X-Camera-Encode-Generation":"offline-encode","X-Camera-View-Generation":require("node:crypto").randomUUID()});
   }
   // No Core/native owner exists in this fixture. Local player health is not an ACK.
   if(url.pathname==="/api/onboard/progress")return reply(200,"application/json",'{"accepted":false,"reason":"offline loopback has no native owner"}');
   if(url.pathname.startsWith("/api/"))return reply(503,"text/plain","offline unassigned source");
   const name=url.pathname==="/"?"index.html":url.pathname.slice(1);
   if(!["index.html","app.js","webrtc.js","freshness.js","mjpeg.js","style.css"].includes(name))return reply(404,"text/plain","");
   let body=fs.readFileSync(path.join(directory,name));
   if(name==="index.html")body=body.toString().replace('<body>','<body data-console="compact">')
    .replace('data-camera-base="/"','data-camera-base="/api/onboard/" data-onboard="true"');
   return reply(200,name.endsWith("js")?"text/javascript":name.endsWith("css")?"text/css":"text/html",body);
  });
  await page.goto("https://am1-publication.invalid");await page.addStyleTag({path:path.resolve(__dirname,"../../tools/am1_console_ui/style.css")});
  await page.waitForFunction(()=>typeof AM1CameraHealth==="function");await page.evaluate(()=>AM1CameraSelect("chest"));
  await page.waitForFunction(()=>AM1CameraHealth().video?.fresh&&AM1CameraHealth().video.decoded_frames>=5,null,{timeout:7000});
  const before=await page.evaluate(()=>probeSnapshot()),offersBefore=offers.length;delayNextStatus=true;
  await page.waitForFunction(()=>{const v=AM1CameraHealth().video;return v?.source_fresh===false&&v.decoded_frames>0&&v.decoded_age_ms<500;},null,{timeout:4000,polling:25});
  const stale=await page.evaluate(()=>probeSnapshot());assert.equal(stale.health.fresh,false);assert.equal(stale.health.source_fresh,false);
  assert.ok(stale.last_rvfc.at>before.last_rvfc.at,"the synthetic status delay must coexist with genuine new presentation");
  const action=policy==="existing-stale-reoffer"?await page.evaluate(()=>AM1CameraReconnect("chest")):{accepted:false,reason:"test-only retain connected advancing peer"};
  const decisionAt=await page.evaluate(()=>performance.now());
  await page.waitForFunction(()=>AM1CameraHealth().video?.fresh===true,null,{timeout:7000,polling:25});
  const after=await page.evaluate(()=>probeSnapshot());
  const snapshots=await page.evaluate(()=>({frames:probeFrames,ticks:probeTicks}));
  results.push({policy,synthetic_status_delay_ms:650,before,stale,decision_at:decisionAt,action,after,
   first_observed_local_fresh_after_stale_ms:after.at-stale.at,peer_retained:after.peer_id===before.peer_id,
   offers_before:offersBefore,offers_total:offers.length,offers,status,frames_during_interval:snapshots.frames.filter(x=>x.at>=before.at&&x.at<=after.at),
   source_ticks_during_interval:snapshots.ticks.filter(x=>x>=before.at&&x<=after.at),native_qualification:"not present; local health only"});
  if(policy==="existing-stale-reoffer"){assert.equal(action.accepted,true);assert.ok(offers.length>offersBefore);}
  assert.deepEqual(errors,[]);await page.evaluate(()=>{clearInterval(probeMedia.timer);probeMedia.stream.getTracks().forEach(track=>track.stop());probePeers.forEach(peer=>peer.close());});await page.close();
 }
 t.diagnostic(JSON.stringify({actual_video:true,comparison:results,production_assets_unchanged:true,physical_workload_credit:false}));
 const retained=results.find(row=>row.policy==="retain-peer-test-only");
 assert.equal(retained.peer_retained,true,"the production default player must retain a connected presenting peer through status-only staleness");
 assert.equal(retained.offers_total,retained.offers_before,"status-only staleness must not restart the production default player");
});


for(const mode of ["source-only","presentation-stale","failed","disconnected","closed","no-frame-startup","invalid-age","source-unavailable"])
 test("default player preserves qualification and bounded transport failure: "+mode,()=>{
 const code=fs.readFileSync(path.resolve(__dirname,"../../tools/am1_camera/app.js"),"utf8");
 const start=code.indexOf("function render() {"),end=code.indexOf("async function statusLoop()",start);
 assert.ok(start>=0&&end>start);const stops=[];let starts=0;
 const health={fresh:false,source_fresh:mode!=="source-only",decoded_frames:10,decoded_age_ms:30};
 if(mode==="presentation-stale")health.decoded_age_ms=501;
 if(mode==="invalid-age")health.decoded_age_ms=NaN;
 if(mode==="no-frame-startup"){health.decoded_frames=0;health.decoded_age_ms=null;}
 const context=vm.createContext({primary:{dataset:{}},selected:"chest",usable:()=>mode!=="source-unavailable",statusAvailable:()=>true,
  stream:{kind:"webrtc",progressed:0,decoded:mode!=="no-frame-startup",deadline:6000},
  webView:{health:()=>health,pc:{connectionState:["failed","disconnected","closed"].includes(mode)?mode:"connected"}},
  performance:{now:()=>mode==="no-frame-startup"?7000:1000},paint(){},tiles:new Map(),slots:new Map(),roles:[],
  connection:{},diagnostics:{},statusUncertain:false,sourceFor:()=>({state:"fresh"}),webPrimary:()=>true,
  document:{body:{dataset:{}}},timing:{},progress:new Map(),labels:{}});
 context.stopPrimary=reason=>{stops.push(reason);context.stream=null;};context.startPrimary=()=>{starts++;context.stream={kind:"webrtc"};};
 vm.runInContext(code.slice(start,end)+";render();",context);
 assert.equal(health.fresh,false,"transport liveness must never grant local source/view qualification");
 if(mode==="source-only"){
  assert.deepEqual(stops,[]);assert.equal(starts,0);assert.equal(context.stream.progressed,1000);
  assert.equal(health.source_fresh,false);assert.equal(health.decoded_age_ms,30);
 }else{
  assert.equal(stops.length,1);assert.equal(stops[0],mode==="source-unavailable"?"source-unavailable":"decoded-video-stall");
  assert.equal(starts,mode==="source-unavailable"?0:1);
 }
});


test("latest-only progress publishes new decoded data before an older receipt body finishes",async()=>{
 const p=fixture(),bodies=[];await p.view.start("chest");p.source();
 p.setProgressResponse(options=>{const frame=JSON.parse(options.body).decoded_frames;
  const body=JSON.stringify({accepted:true,view_delivery:{frame}});
  return {ok:true,headers:{get:name=>name.toLowerCase()==="content-type"?"application/json":String(body.length)},
   text:()=>new Promise(resolve=>bodies.push({frame,finish:()=>resolve(body)}))};});
 try{
  p.callbacks.shift()(0,{presentedFrames:1,mediaTime:.1});const first=p.view.report();await new Promise(setImmediate);
  p.advance(150);p.source();p.callbacks.shift()(150,{presentedFrames:2,mediaTime:.2});const second=p.view.report();
  await new Promise(setImmediate);
  assert.equal(p.requests.filter(r=>r.url.endsWith("/progress")).length,2,"receipt delivery must not block newer decoded progress");
  assert.deepEqual(bodies.map(v=>v.frame),[1,2]);
  bodies[1].finish();await second;assert.equal(p.view.progressReceipt.view_delivery.frame,2);
  bodies[0].finish();await first;assert.equal(p.view.progressReceipt.view_delivery.frame,2,"late old receipt cannot replace current delivery");
  assert.equal(p.view.health().decoded_age_ms,0);p.advance(651);p.source();assert.equal(p.view.health().fresh,false);
 }finally{bodies.forEach(v=>v.finish());await p.view.stop();}
});

test("latest-only progress bounds two requests and aborts older delivery without queuing frames",async()=>{
 const p=fixture(),pending=[],sent=[];let active=0,maximum=0;await p.view.start("chest");p.source();
 p.setProgressResponse(options=>{const frame=JSON.parse(options.body).decoded_frames;sent.push(frame);active++;maximum=Math.max(maximum,active);
  const body=JSON.stringify({accepted:true,view_delivery:{frame}});
  return {ok:true,headers:{get:name=>name.toLowerCase()==="content-type"?"application/json":String(body.length)},
   text:()=>new Promise((resolve,reject)=>{let done=false;const finish=error=>{if(done)return;done=true;active--;error?reject(error):resolve(body);};
    options.signal.addEventListener("abort",()=>finish(new Error("aborted")),{once:true});pending.push(()=>finish());})};});
 try{
  for(const [at,frame]of [[0,1],[150,2],[300,3]]){p.advance(at);p.source();p.callbacks.shift()(at,{presentedFrames:frame,mediaTime:frame/10});void p.view.report();await new Promise(setImmediate);}
  assert.equal(p.requests.filter(r=>r.url.endsWith("/progress")).length,2);
  assert.equal(p.requests.find(r=>r.url.endsWith("/progress")).signal.aborted,true);
  p.advance(450);p.source();p.callbacks.shift()(450,{presentedFrames:5,mediaTime:.5});void p.view.report();await new Promise(setImmediate);
  assert.deepEqual(sent,[1,2,5],"the latest current capture replaces skipped frames; no FIFO replay");assert.equal(maximum,2);
 }finally{pending.forEach(finish=>finish());await p.view.stop();}
});

test("ignored transport abort settles callers while quarantining no more than two raw requests",async()=>{
 const p=fixture(),finish=[];await p.view.start("chest");p.source();
 p.setProgressResponse(()=>{const body=JSON.stringify({accepted:true});return {ok:true,
  headers:{get:name=>name.toLowerCase()==="content-type"?"application/json":String(body.length)},
  text:()=>new Promise(resolve=>finish.push(()=>resolve(body)))};});
 try{
  p.callbacks.shift()(0,{presentedFrames:1,mediaTime:.1});const first=p.view.report();await new Promise(setImmediate);
  p.advance(150);p.source();p.callbacks.shift()(150,{presentedFrames:2,mediaTime:.2});const second=p.view.report();await new Promise(setImmediate);
  for(let frame=3;frame<=8;frame++){p.advance((frame-1)*150);p.source();p.callbacks.shift()((frame-1)*150,{presentedFrames:frame,mediaTime:frame/10});void p.view.report();await new Promise(setImmediate);}
  await Promise.race([Promise.all([first,second]),new Promise((_,reject)=>setTimeout(()=>reject(Error("abort did not settle caller")),200))]);
  assert.equal(p.requests.filter(r=>r.url.endsWith("/progress")).length,2,"an uncooperative transport is quarantined instead of leaking more work");
  assert.equal(p.view.reporting,false);assert.equal(p.view.progressReceipt,null);
 }finally{finish.forEach(resolve=>resolve());await new Promise(setImmediate);await p.view.stop();}
});

test("stopped report cannot clear a new epoch's active delivery or publish a stale receipt",async()=>{
 const p=fixture(),finish=[];await p.view.start("chest");p.source();
 p.setProgressResponse(options=>{const body=JSON.stringify({accepted:true,view_delivery:{role:JSON.parse(options.body).role}});return {ok:true,
  headers:{get:name=>name.toLowerCase()==="content-type"?"application/json":String(body.length)},
  text:()=>new Promise(resolve=>finish.push(()=>resolve(body)))};});
 try{
  p.callbacks.shift()(0,{presentedFrames:1,mediaTime:.1});const old=p.view.report();await new Promise(setImmediate);
  await p.view.stop();await Promise.race([old,new Promise((_,reject)=>setTimeout(()=>reject(Error("stop did not settle old report")),200))]);await p.view.start("forward");p.source("forward");p.advance(200);
  p.callbacks.pop()(200,{presentedFrames:1,mediaTime:.1});const current=p.view.report();await new Promise(setImmediate);
  assert.equal(finish.length,2);assert.equal(p.view.reporting,true);
  finish[0]();await new Promise(setImmediate);assert.equal(p.view.reporting,true);assert.equal(p.view.progressReceipt,null);
  finish[1]();await current;assert.equal(p.view.progressReceipt.view_delivery.role,"forward");assert.equal(p.view.reporting,false);
 }finally{finish.forEach(resolve=>resolve());await p.view.stop();}
});

test("new decode publishes promptly within cadence while an actual frozen decoder still expires",async()=>{
 const p=fixture();await p.view.start("chest");p.source();const text=JSON.stringify({accepted:true});
 p.setProgressResponse({ok:true,headers:{get:name=>name.toLowerCase()==="content-type"?"application/json":String(text.length)},text:async()=>text});
 try{
  p.callbacks.shift()(0,{presentedFrames:1,mediaTime:.1});await p.view.report();
  p.advance(100);p.source();p.callbacks.shift()(100,{presentedFrames:2,mediaTime:.2});await p.view.report();
  assert.equal(p.requests.filter(r=>r.url.endsWith("/progress")).length,1,"rVFC never creates a cadence burst");
  p.advance(450);p.source();p.callbacks.shift()(450,{presentedFrames:3,mediaTime:.3});await new Promise(setImmediate);
  assert.equal(p.requests.filter(r=>r.url.endsWith("/progress")).length,2,"a newly decoded frame gets the due opportunity immediately");
  p.advance(951);p.source();await p.view.report();assert.equal(p.view.health().decoded_age_ms,501);assert.equal(p.view.health().fresh,false);
  assert.equal(p.requests.filter(r=>r.url.endsWith("/progress")).length,2,"stale decoder counters cannot be published as fresh progress");
 }finally{await p.view.stop();}
});


test("fresh decoded progress is published despite an aged browser source-status cache",async()=>{
 const p=fixture();await p.view.start("chest");p.source();
 const text=JSON.stringify({accepted:false,reason:"selected source is not fresh"});
 p.setProgressResponse({ok:true,headers:{get:name=>name.toLowerCase()==="content-type"?"application/json":String(text.length)},text:async()=>text});
 try{
  p.callbacks.shift()(0,{presentedFrames:1,mediaTime:.1});await p.view.report();
  p.advance(600); // No status refresh: actual server source freshness is independent.
  p.callbacks.shift()(600,{presentedFrames:2,mediaTime:.2});await p.view.report();
  const reports=p.requests.filter(r=>r.url.endsWith("/progress"));
  assert.equal(reports.length,2,"cached source status must not suppress genuine new decoder evidence");
  assert.equal(JSON.parse(reports[1].body).decoded_frames,2);
  assert.equal(p.view.health().decoded_age_ms,0);assert.equal(p.view.health().source_age_ms,600);
  assert.equal(p.view.health().source_fresh,false);assert.equal(p.view.health().fresh,false);
  assert.equal(p.view.progressReceipt.accepted,false,"the independent server refusal stays truthful");
 }finally{await p.view.stop();}
});


for(const token of [null,"not-a-uuid"])test("view attachment requires a current issued SDP token: "+token,async()=>{
 const p=fixture();p.ids.view_generation=token;
 try{await assert.rejects(p.view.start("chest"),/view attachment/i);
  assert.equal(p.view.binding,null);assert.equal(p.requests.filter(r=>r.url.endsWith("/progress")).length,0);
 }finally{await p.view.stop();}
});

test("a receipt from a replaced same-source attachment cannot update the current binding",async()=>{
 const p=fixture();await p.view.start("chest");p.source();let finish;
 const text=JSON.stringify({accepted:true});
 p.setProgressResponse({ok:true,headers:{get:name=>name.toLowerCase()==="content-type"?"application/json":String(text.length)},
  text:()=>new Promise(resolve=>{finish=()=>resolve(text);})});
 try{
  p.callbacks.shift()(0,{presentedFrames:1,mediaTime:.1});const receipt=p.view.report();await new Promise(setImmediate);
  const sent=JSON.parse(p.requests.find(r=>r.url.endsWith("/progress")).body);
  assert.equal(sent.view_generation,p.ids.view_generation);
  p.view.binding={...p.view.binding,view_generation:"22222222-2222-4222-8222-222222222222"};
  finish();await receipt;assert.equal(p.view.progressReceipt,null);
 }finally{finish?.();await p.view.stop();}
});


test("standalone WebRTC without progress remains usable without an attachment header",async()=>{
 const p=fixture();p.view.options.progress=null;p.ids.view_generation=null;
 try{
  await p.view.start("chest");p.source();p.callbacks.shift()(0,{presentedFrames:1,mediaTime:.1});
  assert.equal(p.view.health().fresh,true);assert.equal(p.view.health().decoded_frames,1);
  assert.equal(p.requests.filter(r=>r.url.endsWith("/progress")).length,0);
 }finally{await p.view.stop();}
});
