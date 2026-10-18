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
  const ids={owner_generation:"owner-a",capture_generation:"capture-a",encode_generation:"encode-a"};
  const context=vm.createContext({RTCPeerConnection:Peer,MediaStream:class {},performance:{now:()=>now},
    setInterval:()=>1,clearInterval(){},setTimeout,clearTimeout,AbortSignal,
    fetch:async(url,options)=>{requests.push({url,...options});if(url.endsWith("/progress")&&progressResponse)return progressResponse;return {ok:true,headers:{get:name=>({
      "X-Camera-Owner-Generation":ids.owner_generation,"X-Camera-Capture-Generation":ids.capture_generation,
      "X-Camera-Encode-Generation":ids.encode_generation}[name]||null)},text:async()=>"v=0\r\n",json:async()=>({accepted:true})};}});
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
    encode_generation:"encode-a",decoded_frames:1,media_time:0});
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
        "X-Camera-Capture-Generation":captures[role],"X-Camera-Encode-Generation":generation});
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


test("view progress consumes the bounded logical body before releasing its one in-flight slot",async()=>{
  const p=fixture();await p.view.start("chest");p.source();
  p.callbacks.shift()(0,{presentedFrames:1,mediaTime:.1});
  let release,read=false;
  const body=JSON.stringify({accepted:true,status:"view_progress",effect_admitted:false,
    required_view:{required:true,qualified:true,role:"chest",cause:null},view_delivery:{version:1}});
  p.view.options.progress="/api/onboard/progress";
  // Replace only HTTP transport; the product reporting state machine remains real.
  const response={ok:true,headers:{get:name=>name.toLowerCase()==="content-type"?"application/json":String(body.length)},
    text:()=>{read=true;return new Promise(resolve=>{release=()=>resolve(body);});}};
  p.setProgressResponse(response);
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
  p.callbacks.shift()(0,{presentedFrames:1,mediaTime:.1});
  let text=mode==="malformed"?"not-json":JSON.stringify({accepted:false,reason:"decoded video has not advanced"});
  const response={ok:mode!=="http-error",headers:{get:name=>name.toLowerCase()==="content-type"?"application/json":mode==="oversized"?"8193":String(text.length)},
    text:async()=>{if(mode==="stale-epoch")await p.view.stop();return text;}};
  p.setProgressResponse(response);await p.view.report();
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
   return reply(201,"application/sdp",answer,{"X-Camera-Owner-Generation":owner,"X-Camera-Capture-Generation":capture,"X-Camera-Encode-Generation":encode});
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
