"use strict";
const test=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm"),path=require("node:path");
function fixture({manualTimers=false}={}) {
  const file=path.resolve(__dirname,"../../tools/am1_camera/webrtc.js");
  assert.ok(fs.existsSync(file),"bounded native onboard WebRTC player missing");
  let now=0,progressResponse=null,offerResponse=null,remoteError=null,setupError=null,statsResponse=new Map(),statsCalls=0,timerId=0;
  const callbacks=[],requests=[],logs=[],timers=new Map();
  const video={srcObject:null,requestVideoFrameCallback(fn){callbacks.push(fn);return callbacks.length;},cancelVideoFrameCallback(){},play:async()=>{}};
  class Peer {
    constructor(){this.iceGatheringState=setupError==="ice-gather"?"gathering":"complete";this.connectionState="connected";this.iceConnectionState="connected";}
    addTransceiver(){} async createOffer(){if(setupError==="create")throw Error("private create SDP");return {type:"offer",sdp:"v=0\r\n"};}
    async setLocalDescription(value){if(setupError==="local-description")throw Error("private local SDP");this.localDescription=value;} async setRemoteDescription(){if(remoteError)throw remoteError;} close(){this.closed=true;}
    getStats(){statsCalls++;return typeof statsResponse==="function"?statsResponse():Promise.resolve(statsResponse);}
  }
  const ids={owner_generation:"owner-a",capture_generation:"capture-a",encode_generation:"encode-a",view_generation:"11111111-1111-4111-8111-111111111111"};
  const context=vm.createContext({RTCPeerConnection:Peer,MediaStream:class {},performance:{now:()=>now},
    setInterval:()=>1,clearInterval(){},setTimeout:manualTimers?(fn,delay)=>{const id=++timerId;timers.set(id,{fn,delay});return id;}:setTimeout,
    clearTimeout:manualTimers?id=>timers.delete(id):clearTimeout,AbortSignal,AbortController,
    console:{info:(tag,value)=>logs.push({tag,...JSON.parse(value)})},
    fetch:async(url,options)=>{requests.push({url,...options});if(url.endsWith("/progress")&&progressResponse)return typeof progressResponse==="function"?progressResponse(options):progressResponse;if(!url.endsWith("/progress")&&offerResponse)return offerResponse;return {ok:true,status:201,headers:{get:name=>({
      "X-Camera-Owner-Generation":ids.owner_generation,"X-Camera-Capture-Generation":ids.capture_generation,
      "X-Camera-Encode-Generation":ids.encode_generation,"X-Camera-View-Generation":ids.view_generation}[name]||null)},text:async()=>"v=0\r\n",json:async()=>({accepted:true})};}});
  vm.runInContext(fs.readFileSync(file,"utf8"),context);
  const view=new context.AM1OnboardView(video,{offer:role=>"/api/onboard/webrtc/"+role,
    progress:"/api/onboard/progress",csrf:()=>"csrf",clock:()=>now});
  function source(role="chest") {view.updateSource({owner_generation:ids.owner_generation,
    cameras:{[role]:{configured:true,state:"fresh",age_ms:0,sequence:12,capture_generation:ids.capture_generation}},
    video:{role,state:"running",encode_generation:ids.encode_generation,capture_generation:ids.capture_generation}},now);}
  return {view,video,callbacks,requests,ids,source,logs,context,advance:value=>{now=value;},setProgressResponse:value=>{progressResponse=value;},
    setOfferResponse:value=>{offerResponse=value;},setRemoteError:value=>{remoteError=value;},setSetupError:value=>{setupError=value;},setStatsResponse:value=>{statsResponse=value;},
    statsCalls:()=>statsCalls,runTimers:delay=>{for(const [id,item]of [...timers])if(item.delay===delay){timers.delete(id);item.fn();}},timers};
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
  let selected=null,generation=null,sequence=1,statusGate=null,chestGate=null,sourceFresh=true;
  const server=http.createServer(async(request,response)=>{
    requests.push({method:request.method,path:request.url});
    const reply=(status,type,body,headers={})=>{response.writeHead(status,{"Content-Type":type,...headers});response.end(body);};
    if(request.url==="/api/onboard/status"){
      const body=JSON.stringify({owner_generation:owner,
        cameras:Object.fromEntries(roles.map(role=>[role,{configured:true,state:sourceFresh?"fresh":"stale",age_ms:0,sequence:sequence++,
          capture_generation:captures[role],rotation_degrees:role==="wrist_right"?270:180}])),
        video:{enabled:true,transport:"webrtc",role:selected,encode_generation:generation,
          capture_generation:captures[selected],state:selected?"running":"unavailable"}});
      const gate=statusGate;statusGate=null;if(gate){gate.capturedAt=Date.now();gate.arrive();await gate.wait;}
      return reply(200,"application/json",body);
    }
    if(request.url.startsWith("/api/onboard/webrtc/")) {
      const role=request.url.split("/").at(-1);if(role!==selected){selected=role;generation=randomUUID();}
      for await(const _ of request){};
      const gate=role==="chest"?chestGate:null;if(gate){chestGate=null;gate.arrive();await gate.wait;}
      return reply(201,"application/sdp","v=0\r\n",{"X-Camera-Owner-Generation":owner,
        "X-Camera-Capture-Generation":captures[role],"X-Camera-Encode-Generation":generation,"X-Camera-View-Generation":randomUUID()});
    }
    if(request.url==="/api/onboard/progress"){for await(const _ of request){};
      const body='{"accepted":true}';return reply(200,"application/json",body,{"Content-Length":String(Buffer.byteLength(body))});}
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
  const holdResponse=()=>{let arrive,release;const ready=new Promise(resolve=>{arrive=resolve;}),wait=new Promise(resolve=>{release=resolve;});
    return {arrive,release,ready,wait,capturedAt:null};};
  for(const ordering of ["before Chest binding","after Chest binding"])await t.test("old Front status "+ordering,{timeout:8000},async()=>{
    await page.locator('button.view[data-role="forward"]').click();
    await page.waitForFunction(()=>AM1CameraHealth().selected_role==="forward"&&AM1CameraHealth().video?.fresh);
    const gate=statusGate=holdResponse(),offer=ordering.startsWith("before")?chestGate=holdResponse():null;
    try {
      await gate.ready;const first=requests.length;
      await page.locator('button.view[data-role="chest"]').click();
      if(offer){await offer.ready;gate.release();await page.waitForTimeout(30);offer.release();}
      else{
        await page.waitForFunction(()=>AM1CameraHealth().video?.role==="chest"&&!AM1CameraHealth().video?.connecting);
        gate.release();
      }
      assert.ok(Date.now()-gate.capturedAt<500,"test keeps the captured Front response within current source freshness");
      await page.waitForTimeout(120);
      assert.equal(await page.locator("#primary").getAttribute("data-role"),"chest","older status must not replace the newly selected attachment");
      assert.ok(!requests.slice(first).some(request=>request.path==="/api/onboard/webrtc/forward"),"older status must not launch a replacement Front offer");
      await page.waitForFunction(()=>AM1CameraHealth().selected_role==="chest"&&AM1CameraHealth().video?.fresh);
    }finally{gate.release();offer?.release();}
  });
  await t.test("current status still reconciles a shared producer role change",{timeout:8000},async()=>{
    // Front has used its unchanged three-attempt allowance in the prior cases.
    const first=requests.length;selected="backward";generation=randomUUID();
    await page.waitForFunction(()=>AM1CameraHealth().selected_role==="backward"&&AM1CameraHealth().video?.fresh);
    assert.ok(requests.slice(first).some(request=>request.path==="/api/onboard/webrtc/backward"));
  });
  assert.deepEqual(errors,[]);
  const diagnosticPrefix="WebRTC attempt diagnostics (observational): ";
  const readDiagnostic=async()=>JSON.parse((await page.locator("#diagnostics").textContent()).split(diagnosticPrefix)[1]);
  await page.waitForFunction(()=>{
    const text=document.querySelector("#diagnostics").textContent.split("WebRTC attempt diagnostics (observational): ")[1];
    if(!text)return false;const value=JSON.parse(text);
    return value.state==="current"&&value.view_progress?.receipt?.accepted===true;
  });
  assert.ok((await page.locator("#diagnostics").textContent()).includes(diagnosticPrefix),"existing diagnostics panel must surface the curated attempt");
  const liveDiagnostic=await readDiagnostic();assert.equal(liveDiagnostic.state,"current");
  assert.equal(liveDiagnostic.view_progress.visibility_state,"visible");
  assert.equal(typeof liveDiagnostic.view_progress.has_focus,"boolean");
  assert.ok(Number.isFinite(liveDiagnostic.view_progress.sampled_browser_clock_ms));
  assert.ok(liveDiagnostic.view_progress.decoded_frames>0,"existing DOM panel must expose decoder progress");
  assert.equal(liveDiagnostic.view_progress.receipt.accepted,true);
  assert.ok(liveDiagnostic.records.some(row=>row.phase==="offer"&&row.http_status===201&&row.headers_valid===true));
  assert.ok(liveDiagnostic.records.length<=8);
  sourceFresh=false;
  await page.waitForFunction(()=>document.querySelector("#diagnostics").textContent.includes('"state":"stopped"'));
  const stoppedDiagnostic=await readDiagnostic();assert.equal(stoppedDiagnostic.attempt,liveDiagnostic.attempt);
  assert.equal(stoppedDiagnostic.state,"stopped");assert.deepEqual(stoppedDiagnostic.records.slice(0,liveDiagnostic.records.length),liveDiagnostic.records);
  assert.equal(await page.evaluate(()=>AM1CameraHealth().video?.fresh),false);
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
   health,diagnostic:JSON.parse(document.querySelector("#diagnostics").textContent.split("WebRTC attempt diagnostics (observational): ")[1]),
   source_ticks:sourceTicks.length,max_source_tick_gap_ms:Math.max(0,...sourceGaps),largest_frame_gap:gaps[0],
   first_stall:offlineFirstStall,raf:{...offlineRaf},final_stats:await offlineReadStats(),
   dom:{hidden:dom.hidden,display:style.display,visibility:style.visibility,width:rect.width,height:rect.height}};
  clearInterval(offlineMedia.timer);clearInterval(offlineStallTimer);globalThis.offlineRafActive=false;cancelAnimationFrame(offlineRafId);offlineMedia.stream.getTracks().forEach(track=>track.stop());offlinePeers.forEach(peer=>peer.close());return stats;
 },{started,screenshotStarted,screenshotEnded});
 assert.ok(result.frames>100,"real presented frames must keep advancing during the bounded characterization");
 assert.ok(result.last.presented>result.first.presented);assert.ok(result.last.media>result.first.media);
 assert.equal(result.health.video.fresh,true);assert.equal(result.dom.hidden,false);assert.notEqual(result.dom.display,"none");
 assert.ok(!requests.some(value=>/^\/api\/(start|claim|stop|command)$/.test(value)));assert.deepEqual(errors,[]);
 const history=result.diagnostic.view_progress.delivery_history;
 assert.ok(history.callback_count>0&&history.reports.length>0&&history.reports.length<=8);
 assert.ok(Number.isFinite(history.latest_callback.presentation_ms)&&Number.isFinite(history.latest_callback.expected_display_ms));
 for(const values of history.reports){const row=Object.fromEntries(history.columns.map((key,i)=>[key,values[i]]));
  assert.ok(row.callback_ms<=row.fetch_ms);assert.equal(row.pi_core_accepted_s,null);assert.equal(row.pi_until_s,null);
 }
 assert.ok(Buffer.byteLength(JSON.stringify(result.diagnostic))<=8192);
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
  const before=p.view.diagnostics().view_progress.delivery_history;
  assert.equal(before.attempt,p.view.epoch);assert.equal(before.reports.length,1);
  const pendingRows=JSON.stringify(before.reports);
  finish[0]();await new Promise(setImmediate);assert.equal(p.view.reporting,true);assert.equal(p.view.progressReceipt,null);
  assert.equal(JSON.stringify(p.view.diagnostics().view_progress.delivery_history.reports),pendingRows,
   "retired settlement cannot append to or alter a new generation's history");
  finish[1]();await current;assert.equal(p.view.progressReceipt.view_delivery.role,"forward");assert.equal(p.view.reporting,false);
  const after=p.view.diagnostics().view_progress.delivery_history;
  assert.equal(after.attempt,p.view.epoch);assert.equal(after.reports.length,1);
  assert.equal(after.reports[0][after.columns.indexOf("outcome")],"accepted");
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

test("curated diagnostics bound current negotiation, states and inbound-video scalars without secrets",async()=>{
 const p=fixture({manualTimers:true}),secret="PRIVATE-SDP-cookie-candidate-192.0.2.99-device";
 p.setStatsResponse(new Map([
  ["candidate",{type:"local-candidate",address:secret}],
  ["video",{type:"inbound-rtp",kind:"video",codecId:secret,packetsReceived:17,bytesReceived:2300,
   framesReceived:5,framesDecoded:0,jitterBufferEmittedCount:1,trackIdentifier:secret}],
  [secret,{type:"codec",mimeType:"video/H264",sdpFmtpLine:secret}]]));
 await p.view.start("chest");p.source();
 const pc=p.view.pc;
 for(let i=0;i<20;i++){
  pc.iceConnectionState=i%2?"connected":"checking";pc.oniceconnectionstatechange?.();
  pc.connectionState=i%2?"connected":"connecting";pc.onconnectionstatechange?.();
  pc.ontrack({track:{kind:"video",id:secret,label:secret}});
 }
 p.runTimers(3000);await new Promise(setImmediate);
 const row=phase=>p.logs.find(log=>log.phase===phase);
 assert.ok(row("offer"),"current offer diagnostic missing");
 assert.equal(row("offer").http_status,201);assert.equal(row("offer").headers_valid,true);
 assert.equal(row("remote-description").ok,true);assert.equal(row("track").kind,"video");
 assert.equal(p.logs.filter(log=>log.phase==="ice").length,2);
 assert.equal(p.logs.filter(log=>log.phase==="connection").length,2);
 const stats=row("stats");assert.equal(stats.outcome,"available");
 for(const [key,value]of Object.entries({packetsReceived:17,bytesReceived:2300,framesReceived:5,
  framesDecoded:0,jitterBufferEmittedCount:1,codec:"H264"}))assert.equal(stats[key],value);
 assert.equal(p.statsCalls(),1);assert.equal(p.logs.length,8);
 assert.ok(p.logs.every(log=>log.attempt===p.view.epoch&&log.tag==="AM1 WebRTC diagnostics"));
 const text=JSON.stringify(p.logs);
 for(const forbidden of [secret,"owner-a","capture-a","encode-a",p.ids.view_generation,"v=0","csrf"])
  assert.ok(!text.includes(forbidden),"diagnostics must exclude identity/SDP/credentials");
 assert.equal(p.view.health().fresh,false,"stats/transport cannot create decoder freshness");
 await p.view.stop();assert.equal(p.timers.size,0);
});

for(const failure of ["http","headers","description"])test("curated diagnostics retain bounded negotiation failure: "+failure,async()=>{
 const p=fixture({manualTimers:true}),secret="PRIVATE-SDP-description-and-URL";
 if(failure==="http")p.setOfferResponse({ok:false,status:503,headers:{get:()=>secret}});
 if(failure==="headers")p.ids.view_generation=null;
 if(failure==="description")p.setRemoteError(Object.assign(Error(secret),{name:"OperationError",detail:secret}));
 await assert.rejects(p.view.start("chest"));
 const offer=p.logs.find(log=>log.phase==="offer");assert.ok(offer,"failed offer diagnostic missing");
 assert.equal(offer.http_status,failure==="http"?503:201);
 assert.equal(offer.headers_valid,failure==="description");
 if(failure==="description"){
  const description=p.logs.find(log=>log.phase==="remote-description");
  assert.equal(description.ok,false);assert.equal(description.error_category,"operation");
 }
 assert.ok(!JSON.stringify(p.logs).includes(secret));assert.equal(p.statsCalls(),0);
 assert.equal(p.timers.size,0);assert.equal(p.view.pc,null);
});

test("curated diagnostics quarantine one unresolved stats call across stop and new attempt",async()=>{
 const p=fixture({manualTimers:true});let finish;
 p.setStatsResponse(()=>new Promise(resolve=>{finish=resolve;}));
 await p.view.start("chest");p.source();const old=p.view.pc;
 p.runTimers(3000);await new Promise(setImmediate);assert.equal(p.statsCalls(),1,"one diagnostic sample must dispatch");
 // Diagnostics must not await/block current input-view reporting or renew freshness.
 p.callbacks.shift()(0,{presentedFrames:1,mediaTime:.1});await p.view.report();
 assert.equal(p.view.health().fresh,true);p.advance(501);p.source();assert.equal(p.view.health().fresh,false);
 p.runTimers(750);await new Promise(setImmediate);
 assert.equal(p.logs.find(log=>log.phase==="stats").outcome,"timeout");
 await p.view.stop();await p.view.start("forward");p.source("forward");
 const current=p.view.epoch,count=p.logs.length;
 old.ontrack({track:{kind:"video"}});old.onconnectionstatechange?.();old.oniceconnectionstatechange?.();
 assert.equal(p.logs.length,count,"retired callbacks cannot append diagnostics");
 p.runTimers(3000);await new Promise(setImmediate);
 assert.equal(p.statsCalls(),1,"an abort-ignoring stats promise cannot accumulate across epochs");
 assert.equal(p.logs.find(log=>log.phase==="stats"&&log.attempt===current).outcome,"busy");
 const logged=JSON.stringify(p.logs);finish(new Map([["late",{type:"inbound-rtp",kind:"video",framesDecoded:999}]]));
 await new Promise(setImmediate);assert.equal(JSON.stringify(p.logs),logged,"late stats cannot report against new peer");
 await p.view.stop();assert.equal(p.timers.size,0);
});

test("curated diagnostics cannot throw into playback when console or stats fails",async()=>{
 const p=fixture({manualTimers:true});
 p.context.console.info=()=>{throw Error("private console failure");};
 p.setStatsResponse(()=>{throw Object.assign(Error("private stats failure"),{name:"OperationError"});});
 await p.view.start("chest");p.source();p.runTimers(3000);await new Promise(setImmediate);
 p.callbacks.shift()(0,{presentedFrames:1,mediaTime:.1});await p.view.report();
 assert.equal(p.view.health().fresh,true);assert.equal(p.view.frames,1);
 assert.equal(p.statsCalls(),1,"diagnostic sampling was not exercised");
 await p.view.stop();assert.equal(p.timers.size,0);
});

test("curated diagnostics discard nonfinite counters and nonallowlisted codec labels",async()=>{
 const p=fixture({manualTimers:true});
 p.setStatsResponse(new Map([["video",{type:"inbound-rtp",kind:"video",codecId:"private",packetsReceived:NaN,
  bytesReceived:Infinity,framesReceived:-1,framesDecoded:1.5,jitterBufferEmittedCount:0}],
  ["private",{type:"codec",mimeType:"video/private-address-secret"}]]));
 await p.view.start("chest");p.runTimers(3000);await new Promise(setImmediate);
 const stats=p.logs.find(log=>log.phase==="stats");assert.ok(stats,"bounded stats diagnostic missing");
 for(const key of ["packetsReceived","bytesReceived","framesReceived","framesDecoded"])assert.equal(stats[key],null);
 assert.equal(stats.jitterBufferEmittedCount,0);assert.equal(stats.codec,null);
 assert.ok(!JSON.stringify(p.logs).includes("private"));await p.view.stop();
});

test("curated diagnostics reject late negotiation after a new attachment",async()=>{
 const p=fixture({manualTimers:true});let finish;
 p.setOfferResponse(new Promise(resolve=>{finish=resolve;}));
 const old=p.view.start("chest");await new Promise(setImmediate);
 await p.view.stop();p.setOfferResponse(null);await p.view.start("forward");
 const logged=JSON.stringify(p.logs);
 finish({ok:true,status:201,headers:{get:name=>({"X-Camera-Owner-Generation":"retired-owner",
  "X-Camera-Capture-Generation":"retired-capture","X-Camera-Encode-Generation":"retired-encode",
  "X-Camera-View-Generation":p.ids.view_generation}[name])},text:async()=>"private retired SDP"});
 await old;assert.equal(JSON.stringify(p.logs),logged);assert.equal(p.view.binding.role,"forward");
 await p.view.stop();assert.equal(p.timers.size,0);
});

for(const mode of ["unsupported","rejected"])test("curated diagnostics isolate stats availability: "+mode,async()=>{
 const p=fixture({manualTimers:true});await p.view.start("chest");
 if(mode==="unsupported")p.view.pc.getStats=null;
 else p.setStatsResponse(()=>Promise.reject(Error("private candidate address")));
 p.runTimers(3000);await new Promise(setImmediate);
 assert.equal(p.logs.find(log=>log.phase==="stats").outcome,mode==="unsupported"?"unsupported":"failed");
 assert.ok(!JSON.stringify(p.logs).includes("private"));assert.equal(p.view.error,null);
 await p.view.stop();assert.equal(p.timers.size,0);
});

for(const phase of ["create","local-description","ice-gather"])test("curated diagnostics identify pre-offer boundary: "+phase,async()=>{
 const p=fixture({manualTimers:true});p.setSetupError(phase);
 const rejected=assert.rejects(p.view.start("chest"));await new Promise(setImmediate);
 if(phase==="ice-gather")p.runTimers(2000);
 await rejected;
 const failure=p.logs.find(log=>log.failure_phase===phase);
 assert.ok(failure,"pre-offer failure needs a closed phase label");assert.equal(failure.error_category,"other");
 assert.ok(!JSON.stringify(p.logs).includes("private"));assert.equal(p.requests.length,0);
 assert.equal(p.timers.size,0);assert.ok(p.logs.length<=8);
});

test("existing-panel diagnostics retain only the bounded current-or-stopped scalar snapshot",async()=>{
 const p=fixture({manualTimers:true});
 assert.equal(typeof p.view.diagnostics,"function","curated snapshot is missing");
 assert.equal(p.view.diagnostics(),null);
 await p.view.start("chest");const old=p.view.pc;
 for(let i=0;i<50;i++){
  old.oniceconnectionstatechange?.();old.onconnectionstatechange?.();old.ontrack({track:{kind:"video",id:"private"}});
 }
 p.setStatsResponse(new Map([["video",{type:"inbound-rtp",kind:"video",framesDecoded:0,packetsReceived:99}]]));
 p.runTimers(3000);await new Promise(setImmediate);
 const current=p.view.diagnostics();assert.equal(current.state,"current");assert.equal(current.attempt,p.view.epoch);
 assert.equal(current.records.length,8);assert.ok(!JSON.stringify(current).includes("private"));
 const unchanged=JSON.stringify(current);current.records[0].http_status="corrupted";current.records.length=0;
 assert.equal(JSON.stringify(p.view.diagnostics()),unchanged,"public snapshot must not alias player state");
 await p.view.stop();const stopped=p.view.diagnostics();assert.equal(stopped.state,"stopped");
 assert.equal(stopped.attempt,JSON.parse(unchanged).attempt);
 assert.deepEqual(JSON.parse(JSON.stringify(stopped.records)),JSON.parse(unchanged).records);
 old.ontrack({track:{kind:"video"}});assert.deepEqual(p.view.diagnostics(),stopped);
 await p.view.start("forward");const next=p.view.diagnostics();assert.equal(next.state,"current");
 assert.ok(next.attempt>stopped.attempt);assert.equal(next.records.length,2);
 assert.ok(!next.records.some(row=>row.phase==="stats"));await p.view.stop();
});

test("existing-panel diagnostics survive an unavailable console without changing freshness",async()=>{
 const p=fixture({manualTimers:true});p.context.console.info=()=>{throw Error("private console unavailable");};
 await p.view.start("chest");p.source();
 assert.equal(typeof p.view.diagnostics,"function","panel snapshot must not depend on console receipt");
 assert.equal(p.view.diagnostics().records.length,2);
 assert.equal(p.view.health().fresh,false);p.callbacks.shift()(0,{presentedFrames:1,mediaTime:.1});await p.view.report();
 assert.equal(p.view.health().fresh,true);await p.view.stop();assert.equal(p.view.diagnostics().state,"stopped");
});

test("view-progress panel projects only existing receipt clocks and qualified scalar results",async()=>{
 const p=fixture({manualTimers:true}),secret="PRIVATE-cookie-SDP-source-identity";
 p.context.document={visibilityState:"visible",hasFocus:()=>false};
 await p.view.start("chest");p.source();
 const receipt={accepted:true,status:"view_progress",reason:secret,
  required_view:{required:true,qualified:true,role:secret},view_delivery:{run_id:secret,decoded_frames:1,
   media_time:.1,source_sequence:12,request_received_at_monotonic_s:100.,http_response_ready_at_monotonic_s:100.04,
   core:{core_started_at_monotonic_s:100.02,core_completed_at_monotonic_s:100.03,
    core_accepted_at_monotonic_s:100.02,original_floor_at_monotonic_s:99.9,qualified_until_monotonic_s:100.4,csrf:secret},
   owner_result:{accepted:false,status:"refused",reason:secret,required_view:{required:true,qualified:false}}}};
 const body=JSON.stringify(receipt);p.setProgressResponse({ok:true,
  headers:{get:name=>name.toLowerCase()==="content-type"?"application/json":String(body.length)},text:async()=>body});
 p.callbacks.shift()(0,{presentedFrames:1,mediaTime:.1});await p.view.report();p.advance(200);
 const snapshot=p.view.diagnostics(),v=snapshot.view_progress;
 assert.equal(v.sampled_browser_clock_ms,200);assert.equal(v.visibility_state,"visible");assert.equal(v.has_focus,false);
 assert.equal(v.decoded_age_ms,200);assert.equal(v.source_age_ms,200);assert.equal(v.decoded_frames,1);assert.equal(v.media_time,.1);
 assert.equal(v.receipt.accepted,true);assert.equal(v.receipt.status,"view_progress");
 assert.equal(v.receipt.required_view.qualified,true);
 assert.equal(v.receipt.view_delivery.request_received_at_monotonic_s,100.);
 assert.equal(v.receipt.view_delivery.core.core_accepted_at_monotonic_s,100.02);
 assert.equal(v.receipt.view_delivery.core.qualified_until_monotonic_s,100.4,"Pi clocks remain original, not browser ages");
 assert.equal(v.receipt.view_delivery.owner_result.accepted,false);
 assert.equal(v.receipt.view_delivery.owner_result.required_view.qualified,false);
 for(const forbidden of [secret,"owner-a","capture-a","encode-a",p.ids.view_generation,"csrf"])
  assert.ok(!JSON.stringify(snapshot).includes(forbidden),"no identity, reason or credential objects in DOM diagnostics");
 v.receipt.view_delivery.core.core_accepted_at_monotonic_s=999;
 assert.equal(p.view.progressReceipt.view_delivery.core.core_accepted_at_monotonic_s,100.02,"projection cannot alias current receipt");
 await p.view.stop();assert.equal(p.view.diagnostics().view_progress.receipt,null,"no receipt history after stop");
});

test("view-progress panel bounds hostile values and unavailable document fields",async t=>{
 const p=fixture({manualTimers:true}),secret="PRIVATE".repeat(10000),ceiling=Number.MAX_SAFE_INTEGER;
 await p.view.start("chest");p.source();
 for(let i=0;i<5;i++){
  p.view.pc.oniceconnectionstatechange?.();p.view.pc.onconnectionstatechange?.();p.view.pc.ontrack?.({track:{kind:"video"}});
 }
 p.setStatsResponse(new Map([["video",{type:"inbound-rtp",kind:"video",codecId:"codec",packetsReceived:ceiling,
  bytesReceived:ceiling,framesReceived:ceiling,framesDecoded:ceiling,jitterBufferEmittedCount:ceiling}],
  ["codec",{mimeType:"video/H264"}]]));
 p.runTimers(3000);await new Promise(setImmediate);
 const times=["request_received_at_monotonic_s","request_body_completed_at_monotonic_s","status_started_at_monotonic_s",
  "status_completed_at_monotonic_s","source_arrived_at_monotonic_s","original_floor_at_monotonic_s",
  "owner_snapshot_started_at_monotonic_s","owner_snapshot_completed_at_monotonic_s","owner_progress_started_at_monotonic_s",
  "owner_progress_completed_at_monotonic_s","owner_enqueued_at_monotonic_s","http_response_ready_at_monotonic_s"];
 const coreTimes=["core_started_at_monotonic_s","core_completed_at_monotonic_s","core_accepted_at_monotonic_s",
  "original_floor_at_monotonic_s","qualified_until_monotonic_s"];
 const delivery=Object.fromEntries(times.map(key=>[key,ceiling]));delivery.core=Object.fromEntries(coreTimes.map(key=>[key,ceiling]));
 delivery.owner_result={accepted:true,status:"view_progress",reason:secret,required_view:{required:true,qualified:true}};
 delivery.decoded_frames=delivery.source_sequence=ceiling;delivery.media_time=ceiling;delivery.private=delivery;
 p.view.progressReceipt={accepted:true,status:"view_progress",reason:secret,view_delivery:delivery};
 p.view.frames=p.view.mediaTime=ceiling;p.view.decodedAt=0;p.advance(ceiling);
 p.context.document={visibilityState:secret,hasFocus:()=>true};
 await p.view.report();await new Promise(setImmediate); // Include the full before/stall receiver pair in the original4KiB bound.
 const snapshot=p.view.diagnostics(),bytes=Buffer.byteLength(JSON.stringify(snapshot));
 assert.equal(snapshot.records.length,8,"size check includes the full existing diagnostic record bound");
 assert.ok(bytes<=4096,"fixed scalar projection must stay below4KiB even at largest admitted numbers");
 assert.ok(!JSON.stringify(snapshot).includes("PRIVATE"));assert.equal(snapshot.view_progress.visibility_state,null);
 t.diagnostic("maximum-number view-progress diagnostic bytes="+bytes);
 delivery.request_received_at_monotonic_s=Infinity;delivery.core.core_started_at_monotonic_s=1e308;
 delivery.owner_result.accepted="true";delivery.owner_result.status=secret;delivery.owner_result.required_view.qualified=1;
 p.view.frames=-1;p.view.mediaTime=NaN;p.view.decodedAt=NaN;p.view.source.cameras.chest.age_ms=Infinity;
 const bad=p.view.diagnostics().view_progress;
 assert.equal(bad.decoded_frames,null);assert.equal(bad.media_time,null);assert.equal(bad.decoded_age_ms,null);assert.equal(bad.source_age_ms,null);
 assert.equal(bad.receipt.view_delivery.request_received_at_monotonic_s,null);
 assert.equal(bad.receipt.view_delivery.core.core_started_at_monotonic_s,null);
 assert.equal(bad.receipt.view_delivery.owner_result.accepted,null);assert.equal(bad.receipt.view_delivery.owner_result.status,null);
 assert.equal(bad.receipt.view_delivery.owner_result.required_view.qualified,null);
 await p.view.stop();
});

test("view-progress panel reads do not renew source clocks or schedule delivery",async()=>{
 const p=fixture({manualTimers:true});await p.view.start("chest");p.source();
 p.callbacks.shift()(0,{presentedFrames:1,mediaTime:.1});await p.view.report();p.advance(501);p.source();
 const before={decodedAt:p.view.decodedAt,sourceAt:p.view.sourceAt,frames:p.view.frames,mediaTime:p.view.mediaTime,
  lastReportAt:p.view.lastReportAt,lastReportedFrames:p.view.lastReportedFrames,lastReportedMedia:p.view.lastReportedMedia,
  reports:p.view.reports.size,requests:p.requests.length,timers:p.timers.size};
 for(let i=0;i<100;i++)assert.equal(p.view.diagnostics().view_progress.decoded_age_ms,501);
 assert.deepEqual({decodedAt:p.view.decodedAt,sourceAt:p.view.sourceAt,frames:p.view.frames,mediaTime:p.view.mediaTime,
  lastReportAt:p.view.lastReportAt,lastReportedFrames:p.view.lastReportedFrames,lastReportedMedia:p.view.lastReportedMedia,
  reports:p.view.reports.size,requests:p.requests.length,timers:p.timers.size},before);
 assert.equal(p.view.health().fresh,false,"diagnostic reads cannot qualify an expired decoder");
 p.context.document={get visibilityState(){throw Error("private unavailable document");}};
 assert.doesNotThrow(()=>p.view.diagnostics());assert.equal(p.view.health().fresh,false);
 await p.view.stop();
});


test("between-sample progress history joins actual callbacks, dispatch and out-of-order Pi receipts",async()=>{
 const p=fixture({manualTimers:true}),pending=[],secret="PRIVATE-cookie-URL-CSRF";
 await p.view.start("chest");p.source();
 p.setProgressResponse(options=>{
  const frame=JSON.parse(options.body).decoded_frames;
  const receipt={accepted:frame!==1,status:frame===1?"refused":"view_progress",reason:secret,required_view:{required:true,qualified:frame!==1},
   view_delivery:{owner_result:{accepted:true,required_view:{required:true,qualified:frame!==1}},decoded_frames:frame,media_time:frame/10,
    source_sequence:123+frame,source_arrived_at_monotonic_s:99.86+frame/10,request_received_at_monotonic_s:100+frame/10,
    status_started_at_monotonic_s:100.01+frame/10,status_completed_at_monotonic_s:100.02+frame/10,
    original_floor_at_monotonic_s:99.9+frame/10,private:secret,
    core:{core_started_at_monotonic_s:100.02+frame/10,core_accepted_at_monotonic_s:frame===1?null:100.03+frame/10,
     original_floor_at_monotonic_s:99.8+frame/10,
     qualified_until_monotonic_s:frame===1?0:100.4+frame/10}}};
  const body=JSON.stringify(receipt);
  return {ok:true,status:200,headers:{get:name=>name.toLowerCase()==="content-type"?"application/json":String(body.length)},
   text:()=>new Promise(resolve=>pending.push(()=>resolve(body)))};
 });
 p.callbacks.shift()(0,{presentedFrames:1,mediaTime:.1,presentationTime:0,expectedDisplayTime:16});
 const first=p.view.report();await new Promise(setImmediate);
 p.advance(150);p.source();p.callbacks.shift()(150,{presentedFrames:2,mediaTime:.2,presentationTime:140,expectedDisplayTime:166});
 const second=p.view.report();await new Promise(setImmediate);
 p.advance(180);pending[1]();await second;p.advance(300);pending[0]();await first;
 const snapshot=p.view.diagnostics(),history=snapshot.view_progress.delivery_history;
 assert.ok(history,"latest-only receipt loses the earlier between-sample delivery");
 const rows=Array.from(history.reports,row=>Object.fromEntries(Array.from(history.columns,(key,i)=>[key,row[i]])));
 assert.equal(rows.length,2);assert.equal(history.callback_count,2);
 assert.equal(history.latest_callback.frames,2);assert.equal(history.latest_callback.presentation_ms,140);
 assert.equal(history.latest_callback.expected_display_ms,166);
 assert.deepEqual(rows.map(row=>[row.frames,row.callback_ms,row.fetch_ms,row.settled_ms,row.current]),
  [[1,0,0,300,false],[2,150,150,180,true]]);
 assert.equal(rows[0].pi_request_s,100.1);assert.equal(rows[0].pi_floor_s,100.);
 assert.equal(rows[1].pi_core_accepted_s,100.23);assert.equal(rows[1].outcome,"accepted");
 assert.deepEqual(rows.map(row=>[row.accepted,row.qualified,row.owner_accepted,row.owner_qualified,row.pi_until_s]),
  [[false,false,true,false,0],[true,true,true,true,100.60000000000001]]);
 assert.equal(rows[0].pi_core_floor_s,99.89999999999999,"late invalidating receipt keeps its original Core floor");
 assert.equal(rows[0].source_sequence,124);assert.equal(rows[0].pi_source_arrived_s,99.96);
 assert.equal(rows[0].pi_core_accepted_s,null);assert.equal(rows[0].pi_core_started_s,100.11999999999999,
  "refused noncurrent report retains Core attempt even without an accepted timestamp");
 assert.notEqual(rows[0].pi_source_arrived_s,rows[0].pi_floor_s,"source arrival and selected floor are distinct");
 assert.equal(p.view.progressReceipt.view_delivery.decoded_frames,2,"historical receipt must not replace latest control evidence");
 for(const forbidden of [secret,p.ids.owner_generation,p.ids.capture_generation,p.ids.encode_generation,p.ids.view_generation]){
  assert.ok(!JSON.stringify(snapshot).includes(forbidden));assert.ok(!JSON.stringify(p.view.diagnosticView).includes(forbidden));
 }
 history.reports[0][0]=999;assert.notEqual(p.view.diagnostics().view_progress.delivery_history.reports[0][0],999);
 p.advance(901);p.source();assert.equal(p.view.health().fresh,false);
 const expired=p.view.diagnostics().view_progress.delivery_history;
 assert.deepEqual(Array.from(expired.reports.at(-1)),Array.from(history.reports.at(-1)),
  "sampling an expired decoder cannot invent a new invalidation receipt or renew its Pi qualification");
 assert.equal(expired.reports.length,2);assert.equal(p.requests.filter(r=>r.url.endsWith("/progress")).length,2);
 await p.view.stop();
});


test("progress history distinguishes callbacks, occupied raw slots and cancellation settlement",async()=>{
 const p=fixture({manualTimers:true}),finish=[];await p.view.start("chest");p.source();
 const body=JSON.stringify({accepted:true,status:"view_progress",required_view:{required:true,qualified:true}});
 p.setProgressResponse({ok:true,status:200,headers:{get:name=>name.toLowerCase()==="content-type"?"application/json":String(body.length)},
  text:()=>new Promise(resolve=>finish.push(()=>resolve(body)))});
 p.callbacks.shift()(0,{presentedFrames:1,mediaTime:.1});const first=p.view.report();await new Promise(setImmediate);
 p.advance(150);p.source();p.callbacks.shift()(150,{presentedFrames:2,mediaTime:.2});const second=p.view.report();await new Promise(setImmediate);
 p.advance(300);p.callbacks.shift()(300,{presentedFrames:2,mediaTime:.2});void p.view.report();
 p.advance(350);p.callbacks.shift()(350,{presentedFrames:3,mediaTime:.3});await new Promise(setImmediate);
 p.runTimers(750);await Promise.all([first,second]);
 let history=p.view.diagnostics().view_progress.delivery_history;
 assert.equal(history.latest_callback.advanced,true);assert.equal(history.latest_callback.frames,3);
 assert.ok(history.skipped.no_presentation>=1&&history.skipped.slots>=1&&history.skipped.cadence>=1);
 assert.equal(history.slots.raw,2);assert.equal(history.slots.active,0);assert.equal(history.slots.cancelled,2);
 assert.equal(p.requests.filter(r=>r.url.endsWith("/progress")).length,2,"diagnostics must not bypass quarantine");
 p.advance(900);void p.view.report();history=p.view.diagnostics().view_progress.delivery_history;
 assert.ok(history.skipped.decode>=1);assert.equal(p.view.health().fresh,false,"diagnostic clocks cannot renew decode freshness");
 finish.forEach(resolve=>resolve());await new Promise(setImmediate);
 history=p.view.diagnostics().view_progress.delivery_history;
 const rows=Array.from(history.reports,row=>Object.fromEntries(Array.from(history.columns,(key,i)=>[key,row[i]])));
 assert.deepEqual(rows.map(row=>[row.outcome,row.cancel_ms,row.settled_ms]),[["superseded",350,900],["timeout",350,900]]);
 assert.equal(p.view.progressReceipt,null);await p.view.stop();
});

test("progress history sanitizes storage and bounds eight rows within8KiB",async t=>{
 const p=fixture({manualTimers:true}),secret="PRIVATE-cookie-URL-CSRF",ceiling=Number.MAX_SAFE_INTEGER;
 await p.view.start("chest");p.source();let huge=false;
 p.setProgressResponse(()=>{
  const n=huge?ceiling:100;
  const receipt={accepted:true,status:"view_progress",reason:secret,required_view:{required:true,qualified:true},
   view_delivery:{request_received_at_monotonic_s:n,status_started_at_monotonic_s:n,status_completed_at_monotonic_s:n,
    original_floor_at_monotonic_s:n,core:{core_accepted_at_monotonic_s:n,qualified_until_monotonic_s:n},private:secret}};
  const body=JSON.stringify(receipt);return {ok:true,status:200,
   headers:{get:name=>name.toLowerCase()==="content-type"?"application/json":String(body.length)},text:async()=>body};
 });
 for(let i=1;i<=12;i++){p.advance((i-1)*150);p.source();p.callbacks.shift()((i-1)*150,
  {presentedFrames:i,mediaTime:i/10,presentationTime:(i-1)*150,expectedDisplayTime:(i-1)*150+16});await p.view.report();}
 let snapshot=p.view.diagnostics(),history=snapshot.view_progress.delivery_history;
 assert.equal(p.view.diagnosticView.progress.reports.length,8);assert.equal(history.dropped,4);
 assert.equal(history.reports.length+history.size_dropped,8);assert.ok(Buffer.byteLength(JSON.stringify(snapshot))<=8192);
 huge=true;
 for(let i=1;i<=12;i++){const at=ceiling-2000+i*150;p.advance(at);p.source();p.callbacks.shift()(at,
  {presentedFrames:ceiling-12+i,mediaTime:ceiling-12+i,presentationTime:at,expectedDisplayTime:at+16});await p.view.report();}
 for(let i=0;i<4;i++){p.view.pc.oniceconnectionstatechange?.();p.view.pc.onconnectionstatechange?.();}
 p.view.pc.ontrack?.({track:{kind:"video"}});p.setStatsResponse(new Map([["v",{type:"inbound-rtp",kind:"video",
  packetsReceived:ceiling,bytesReceived:ceiling,framesReceived:ceiling,framesDecoded:ceiling,jitterBufferEmittedCount:ceiling}]]));
 p.runTimers(3000);await new Promise(setImmediate);
 snapshot=p.view.diagnostics();history=snapshot.view_progress.delivery_history;
 assert.equal(p.view.diagnosticView.progress.reports.length,8);assert.equal(history.dropped,16);
 assert.equal(history.reports.length+history.size_dropped,8,"any omitted diagnostic rows must be counted");
 t.diagnostic(JSON.stringify({maximum_scalar_history_bytes:Buffer.byteLength(JSON.stringify(snapshot)),
  retained:history.reports.length,size_dropped:history.size_dropped}));
 assert.ok(Buffer.byteLength(JSON.stringify(snapshot))<=8192);
 assert.ok(!JSON.stringify(snapshot).includes(secret)&&!JSON.stringify(p.view.diagnosticView).includes(secret));
 p.callbacks.shift()(ceiling,{presentedFrames:ceiling,mediaTime:Infinity,presentationTime:NaN,expectedDisplayTime:secret});
 const latest=p.view.diagnostics().view_progress.delivery_history.latest_callback;
 assert.equal(latest.presentation_ms,null);assert.equal(latest.expected_display_ms,null);assert.equal(latest.media_time,null);
 assert.equal(latest.advanced,false);assert.equal(p.view.mediaTime,ceiling,"invalid diagnostic metadata cannot change player progress");
 await p.view.stop();
});

test("progress history failures cannot affect valid receipt delivery",async()=>{
 for(const failure of ["optional-response-status","optional-presentation","diagnostic-storage"]){
  const p=fixture({manualTimers:true});await p.view.start("chest");p.source();
  const body=JSON.stringify({accepted:true,status:"view_progress",required_view:{required:true,qualified:true}});
  const response={ok:true,headers:{get:name=>name.toLowerCase()==="content-type"?"application/json":String(body.length)},text:async()=>body};
  if(failure==="optional-response-status")Object.defineProperty(response,"status",{get(){throw Error("PRIVATE status");}});
  if(failure==="diagnostic-storage")Object.defineProperty(p.view.diagnosticView,"progress",{get(){throw Error("PRIVATE storage");}});
  p.setProgressResponse(response);
  const metadata={presentedFrames:1,mediaTime:.1};
  if(failure==="optional-presentation")Object.defineProperty(metadata,"presentationTime",{get(){throw Error("PRIVATE presentation");}});
  p.callbacks.shift()(0,metadata);await p.view.report();
  assert.equal(p.view.progressReceipt?.accepted,true,failure);assert.equal(p.view.error,null,failure);
  assert.equal(p.view.health().fresh,true,failure);assert.equal(p.requests.filter(r=>r.url.endsWith("/progress")).length,1);
  await p.view.stop();
 }
});

for(const cadence of [150,200])test("progress history retains eight realistic full receipts at "+cadence+"ms",async t=>{
 const p=fixture({manualTimers:true});await p.view.start("chest");p.source();
 const pi=1011840.123456789;
 p.setProgressResponse(options=>{
  const body=JSON.parse(options.body),at=pi+(body.decoded_frames-1800)*cadence/1000;
  const delivery={decoded_frames:body.decoded_frames,media_time:body.media_time,source_sequence:14600+body.decoded_frames,
   request_received_at_monotonic_s:at,request_body_completed_at_monotonic_s:at+.0011,
   status_started_at_monotonic_s:at+.0012,status_completed_at_monotonic_s:at+.0141,
   source_arrived_at_monotonic_s:at-.0412,original_floor_at_monotonic_s:at-.0412,
   owner_snapshot_started_at_monotonic_s:at+.0142,owner_snapshot_completed_at_monotonic_s:at+.0179,
   owner_progress_started_at_monotonic_s:at+.0180,owner_progress_completed_at_monotonic_s:at+.0215,
   owner_enqueued_at_monotonic_s:at+.0181,http_response_ready_at_monotonic_s:at+.0217,
   core:{core_started_at_monotonic_s:at+.0184,core_completed_at_monotonic_s:at+.0213,
    core_accepted_at_monotonic_s:at+.0186,original_floor_at_monotonic_s:at-.0412,qualified_until_monotonic_s:at+.4588},
   owner_result:{accepted:true,status:"view_progress",required_view:{required:true,qualified:true}}};
  const text=JSON.stringify({accepted:true,status:"view_progress",required_view:{required:true,qualified:true},view_delivery:delivery});
  return {ok:true,status:200,headers:{get:name=>name.toLowerCase()==="content-type"?"application/json":String(text.length)},text:async()=>text};
 });
 for(let i=0;i<4;i++){p.view.pc.oniceconnectionstatechange?.();p.view.pc.onconnectionstatechange?.();}
 p.view.pc.ontrack?.({track:{kind:"video"}});p.setStatsResponse(new Map([["v",{type:"inbound-rtp",kind:"video",
  packetsReceived:1350,bytesReceived:1234567,framesReceived:1800,framesDecoded:1800,jitterBufferEmittedCount:1797}]]));
 p.runTimers(3000);await new Promise(setImmediate);
 for(let i=0;i<8;i++){const at=40450.123456+i*cadence;p.advance(at);p.source();p.callbacks.shift()(at,
  {presentedFrames:1800+i,mediaTime:120.123456789+i*cadence/1000,presentationTime:at-.211,expectedDisplayTime:at+16.667});
  await p.view.report();}
 const snapshot=p.view.diagnostics(),h=snapshot.view_progress.delivery_history,index=h.columns.indexOf("fetch_ms");
 const span=h.reports.at(-1)[index]-h.reports[0][index];
 t.diagnostic(JSON.stringify({cadence_ms:cadence,stored:8,retained:h.reports.length,size_dropped:h.size_dropped,
  retained_span_ms:span,bytes:Buffer.byteLength(JSON.stringify(snapshot))}));
 assert.equal(h.reports.length,8,"bounded diagnostic projection must retain the eight realistic report rows");
 assert.ok(span>=1000,"one-second diagnostic sampling needs at least one full second of retained reports");
 assert.equal(h.size_dropped,0);assert.ok(Buffer.byteLength(JSON.stringify(snapshot))<=8192);await p.view.stop();
});


for(const decodeAdvances of [false,true])test("receiver stats retain baseline and stalled presentation: decode advances="+decodeAdvances,async()=>{
 const p=fixture({manualTimers:true});
 const sample=(packets,received,decoded)=>new Map([["v",{type:"inbound-rtp",kind:"video",codecId:"c",
  packetsReceived:packets,bytesReceived:packets*100,framesReceived:received,framesDecoded:decoded,
  framesDropped:0,jitterBufferEmittedCount:received}],["c",{mimeType:"video/H264"}]]);
 p.setStatsResponse(sample(100,20,20));await p.view.start("chest");p.advance(3000);p.source();
 p.callbacks.shift()(3000,{presentedFrames:20,mediaTime:2});p.runTimers(3000);await new Promise(setImmediate);
 p.setStatsResponse(sample(200,30,30));p.advance(4000);p.source();
 p.callbacks.shift()(4000,{presentedFrames:30,mediaTime:3});await p.view.report();await new Promise(setImmediate);
 p.setStatsResponse(sample(250,35,decodeAdvances?35:30));p.advance(4501);p.source();await p.view.report();await new Promise(setImmediate);
 const diagnostic=p.view.diagnostics(),stats=diagnostic.receiver_stats;
 assert.ok(stats,"current/stalled receiver evidence is missing");assert.equal(stats.clock,"browser_performance_ms");
 assert.equal(stats.state,"stalled");assert.equal(stats.samples.length,2);assert.equal(p.statsCalls(),3);
 const rows=stats.samples.map(row=>Object.fromEntries(stats.columns.map((key,i)=>[key,row[i]])));
 assert.equal(rows[0].trigger,"baseline");assert.equal(rows[1].trigger,"stall");
 assert.equal(rows[0].packetsReceived,200);assert.equal(rows[1].packetsReceived,250);
 assert.equal(rows[0].framesReceived,30);assert.equal(rows[1].framesReceived,35);
 assert.equal(rows[0].framesDecoded,30);assert.equal(rows[1].framesDecoded,decodeAdvances?35:30);
 assert.equal(rows[1].presented_frames,30);assert.equal(rows[1].callback_ms,4000);assert.equal(rows[1].requested_ms,4501);
 assert.equal(rows[1].settled_ms,4501);assert.equal(rows[1].codec,"H264");
 assert.equal(p.view.health().fresh,false,"stats cannot refresh decoded presentation");
 const stable=JSON.stringify(stats);stats.samples[0][0]="PRIVATE mutation";
 assert.equal(JSON.stringify(p.view.diagnostics().receiver_stats),stable,"stats projection must not alias its cache");
 p.advance(4900);await p.view.report();assert.equal(p.statsCalls(),3,"one transition sample per stalled episode");
 await p.view.stop();assert.equal(JSON.stringify(p.view.diagnostics().receiver_stats),stable,"stop retains the actual pair");
 assert.equal(p.logs.filter(row=>row.phase==="stats").length,1,"periodic/stall samples must not emit per-frame console records");
});

for(const mode of ["unchanged","missing","malformed"])test("receiver stats distinguish unchanged and unknown counters: "+mode,async()=>{
 const p=fixture({manualTimers:true}),value=mode==="missing"?{}:mode==="malformed"?{packetsReceived:NaN,bytesReceived:Infinity,framesReceived:-1,framesDecoded:1.5,jitterBufferEmittedCount:"0"}:{packetsReceived:1,bytesReceived:100,
  framesReceived:2,framesDecoded:2,jitterBufferEmittedCount:0};
 p.setStatsResponse(new Map([["v",{type:"inbound-rtp",kind:"video",...value}]]));
 await p.view.start("chest");p.advance(3000);p.source();p.callbacks.shift()(3000,{presentedFrames:2,mediaTime:.2});
 p.runTimers(3000);await new Promise(setImmediate);p.advance(3501);p.source();await p.view.report();await new Promise(setImmediate);
 const stats=p.view.diagnostics().receiver_stats;assert.ok(stats,"receiver transition missing");
 const rows=stats.samples.map(row=>Object.fromEntries(stats.columns.map((key,i)=>[key,row[i]])));
 assert.equal(rows.length,2);
 for(const key of ["packetsReceived","bytesReceived","framesReceived","framesDecoded","jitterBufferEmittedCount"])
  assert.equal(rows[1][key],mode!=="unchanged"?null:value[key],"missing is UNKNOWN, not zero");
 assert.equal(rows[1].framesDropped,null);assert.equal(p.view.health().fresh,false);await p.view.stop();
});

test("receiver stall sampling preserves a hung getStats slot and excludes late retired results",async()=>{
 const p=fixture({manualTimers:true});let finish;
 p.setStatsResponse(()=>new Promise(resolve=>{finish=resolve;}));await p.view.start("chest");p.advance(3000);p.source();
 p.callbacks.shift()(3000,{presentedFrames:1,mediaTime:.1});p.runTimers(3000);await new Promise(setImmediate);
 p.advance(3501);p.source();await p.view.report();await new Promise(setImmediate);
 assert.equal(p.statsCalls(),1,"stall cannot overlap the underlying stats request");
 const stats=p.view.diagnostics().receiver_stats;assert.ok(stats,"busy transition must remain explicit");
 const row=Object.fromEntries(stats.columns.map((key,i)=>[key,stats.samples.at(-1)[i]]));
 assert.equal(row.outcome,"busy");assert.equal(row.packetsReceived,null);
 await p.view.stop();await p.view.start("forward");p.source("forward");
 const before=JSON.stringify(p.view.diagnostics());finish(new Map([["late",{type:"inbound-rtp",kind:"video",framesDecoded:999}]]));
 await new Promise(setImmediate);assert.equal(JSON.stringify(p.view.diagnostics()),before,"retired stats cannot alter current evidence");
 await p.view.stop();assert.equal(p.timers.size,0);
});


test("receiver sampling resumes baseline opportunities without losing the cached transition",async()=>{
 const p=fixture({manualTimers:true});p.setStatsResponse(new Map([["v",{type:"inbound-rtp",kind:"video",framesDecoded:3}]]));
 await p.view.start("chest");p.advance(3000);p.source();p.callbacks.shift()(3000,{presentedFrames:3,mediaTime:.3});
 p.runTimers(3000);await new Promise(setImmediate);p.advance(3501);p.source();await p.view.report();await new Promise(setImmediate);
 const original=JSON.stringify(p.view.diagnostics().receiver_stats.samples);assert.equal(p.statsCalls(),2);
 p.advance(4000);p.source();p.callbacks.shift()(4000,{presentedFrames:4,mediaTime:.4});await p.view.report();
 assert.equal(p.view.diagnostics().receiver_stats.state,"presenting");assert.equal(p.statsCalls(),2);
 p.advance(5000);p.source();p.callbacks.shift()(5000,{presentedFrames:5,mediaTime:.5});await p.view.report();await new Promise(setImmediate);
 assert.equal(p.statsCalls(),3);assert.equal(JSON.stringify(p.view.diagnostics().receiver_stats.samples),original);
 p.advance(5501);p.source();await p.view.report();await new Promise(setImmediate);
 const stats=p.view.diagnostics().receiver_stats,index=stats.columns.indexOf("requested_ms");
 assert.equal(stats.samples.length,2);assert.equal(stats.samples[0][index],5000);assert.equal(stats.samples[1][index],5501);
 assert.equal(p.statsCalls(),4);assert.equal(p.view.health().fresh,false);await p.view.stop();
});

test("receiver diagnostic failures cannot throw into presentation or progress",async()=>{
 const p=fixture({manualTimers:true});await p.view.start("chest");p.source();
 Object.defineProperty(p.view.diagnosticView,"receiver",{get(){throw Error("PRIVATE diagnostic cache");}});
 p.setStatsResponse(()=>{throw Error("PRIVATE stats");});p.runTimers(3000);await new Promise(setImmediate);
 p.callbacks.shift()(0,{presentedFrames:1,mediaTime:.1});await p.view.report();
 assert.equal(p.view.health().fresh,true);assert.equal(p.view.frames,1);
 assert.equal(p.requests.filter(x=>x.url.endsWith("/progress")).length,1);
 p.advance(501);p.source();await p.view.report();assert.equal(p.view.health().fresh,false);
 assert.doesNotThrow(()=>p.view.diagnostics());assert.ok(!JSON.stringify(p.view.diagnostics()).includes("PRIVATE"));await p.view.stop();
});


test("receiver replacement retains only the completed pair under its original attempt",async()=>{
 const p=fixture({manualTimers:true});p.setStatsResponse(new Map([["v",{type:"inbound-rtp",kind:"video",framesDecoded:3}]]));
 await p.view.start("chest");p.advance(3000);p.source();p.callbacks.shift()(3000,{presentedFrames:3,mediaTime:.3});
 p.runTimers(3000);await new Promise(setImmediate);p.advance(3501);p.source();await p.view.report();await new Promise(setImmediate);
 const old=p.view.epoch,pair=JSON.stringify(p.view.diagnostics().receiver_stats.samples);
 await p.view.stop();await p.view.start("forward");p.source("forward");
 const retained=p.view.diagnostics().receiver_stats;
 assert.equal(JSON.stringify(retained.samples),pair,"replacement must not erase the completed pair before a DOM observation");
 assert.equal(retained.attempt,old,"replacement must keep the completed transition's OWN attempt");
 assert.equal(retained.current_attachment,false);
 p.advance(6501);p.source("forward");p.callbacks.pop()(6501,{presentedFrames:1,mediaTime:.1});
 p.runTimers(3000);await new Promise(setImmediate);
 assert.equal(p.view.diagnostics().receiver_stats.attempt,old,"a startup baseline cannot erase the retained transition");
 let finish;p.setStatsResponse(()=>new Promise(resolve=>{finish=resolve;}));
 p.advance(7002);p.source("forward");await p.view.report();await new Promise(setImmediate);
 assert.equal(p.view.diagnostics().receiver_stats.attempt,old,"a pending new measurement is not a completed transition");
 finish(new Map([["v",{type:"inbound-rtp",kind:"video",framesDecoded:1}]]));await new Promise(setImmediate);
 const current=p.view.diagnostics().receiver_stats;
 assert.equal(current.attempt,p.view.epoch);assert.equal(current.current_attachment,true);
 const index=current.columns.indexOf("requested_ms");assert.equal(current.samples.length,2);
 assert.equal(current.samples[0][index],6501);assert.equal(current.samples[1][index],7002);
 await p.view.stop();
});
