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
