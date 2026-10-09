"use strict";
const assert = require("node:assert/strict");
const {test} = require("node:test");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const source = path.resolve(__dirname, "../../tools/am1_camera/freshness.js");
function load(name = "AM1FrameState") {
  assert.ok(fs.existsSync(source), "Shared displayed-frame freshness logic missing");
  const context = {}; vm.runInNewContext(fs.readFileSync(source, "utf8"), context);
  return context[name];
}
test("test_role_cache_survives_disconnect_and_switch", () => {
  const revoked = [], Cache = load("AM1RetainedFrames"), cache = new Cache(url => revoked.push(url));
  cache.setGeneration("forward", 1);
  assert.equal(cache.accept("forward", 1, 8, "blob:forward", 20, 1000), true);
  cache.setGeneration("chest", 1);
  assert.equal(cache.accept("chest", 1, 3, "blob:chest", 10, 1100), true);
  assert.equal(cache.get("forward", 1200).url, "blob:forward");
  assert.equal(cache.get("forward", 1700).state, "stale");
  assert.equal(cache.get("forward", 1700).age_ms, 720);
  assert.deepEqual(revoked, [], "Switching or disconnection must not release the last image");
});
test("test_generation_discards_old_inflight_reply", () => {
  const revoked = [], Cache = load("AM1RetainedFrames"), cache = new Cache(url => revoked.push(url));
  cache.setGeneration("wrist_left", 4);
  assert.equal(cache.accept("wrist_left", 4, 90, "blob:old", 0, 1000), true);
  cache.setGeneration("wrist_left", 5);
  assert.equal(cache.accept("wrist_left", 4, 91, "blob:late", 0, 1100), false);
  assert.equal(cache.accept("wrist_left", 5, 1, "blob:new", 0, 1200), true);
  assert.equal(cache.get("wrist_left", 1250).url, "blob:new");
  assert.deepEqual(revoked, ["blob:late", "blob:old"]);
});
test("test_status_does_not_renew_frame_age", () => {
  const Cache = load("AM1RetainedFrames"), cache = new Cache(() => {});
  cache.setGeneration("forward", 1);
  cache.accept("forward", 1, 1, "blob:one", 40, 1000);
  assert.equal(cache.get("forward", 1300).age_ms, 340);
  // A later status poll has no method by which it can change the frame clock.
  assert.equal(cache.get("forward", 1600).age_ms, 640);
  assert.equal(cache.get("forward", 1600).state, "stale");
});
test("test_object_urls_stay_bounded", () => {
  const revoked = [], Cache = load("AM1RetainedFrames"), cache = new Cache(url => revoked.push(url));
  const roles = ["forward", "backward", "chest", "wrist_left", "wrist_right"];
  for (const role of roles) {
    cache.setGeneration(role, 1);
    for (let seq = 1; seq <= 4; seq++) cache.accept(role, 1, seq, `blob:${role}:${seq}`, 0, seq);
  }
  assert.equal(revoked.length, 15, "Each replacement must release its predecessor");
  assert.equal(roles.filter(role => cache.get(role, 5)?.url).length, 5);
  cache.setGeneration("unexpected_sixth", 1);
  assert.equal(cache.accept("unexpected_sixth", 1, 1, "blob:sixth", 0, 5), false);
  assert.equal(revoked.length, 16, "A sixth retained source must be rejected and released");
  for (const role of roles) cache.release(role);
  assert.equal(revoked.length, 21, "No object URL remains after release");
});
test("producer freshness never advances merely because a status request succeeded", () => {
  const state = load("AM1SourceState"), frame = {state: "fresh", age_ms: 10, fps: 15, sequence: 2};
  assert.equal(state(frame, 1000, 1200).state, "fresh");
  assert.equal(state(frame, 1000, 1501).state, "stale");
});
test("thumbnail freshness includes the displayed image, not just a live producer", () => {
  const state = load(), frame = {state: "fresh", age_ms: 10, fps: 15, sequence: 90};
  assert.equal(state(frame, 3000, 3010, {at: 1000, age_ms: 10}, true).state, "stale");
  assert.equal(state(frame, 3000, 3010, null, true).state, "stale");
  assert.equal(state(frame, 3000, 3010, {at: 2900, age_ms: 20}, true).state, "fresh");
});
test("failed status, disconnect and missing age cannot masquerade as fresh", () => {
  const state = load();
  assert.equal(state(null, 0, 100).state, "unavailable");
  assert.equal(state({state: "stale", age_ms: 5, sequence: 3}, 100, 101).state, "stale");
  assert.equal(state({state: "fresh", age_ms: null, sequence: 3}, 100, 101).state, "stale");
});
test("primary must not label a frozen displayed image fresh while producer status advances", () => {
  const state = load(), source = {state: "fresh", age_ms: 10, fps: 15, sequence: 90};
  assert.equal(state(source, 30000, 30010, {at: 0, age_ms: 0}, false).state, "stale");
  assert.equal(state(source, 30000, 30010, null, false).state, "stale");
});
test("thumbnail footer reports the displayed frame age, not the newer producer age", () => {
  const state = load(), source = {state: "fresh", age_ms: 10, fps: 15, sequence: 90};
  assert.equal(state(source, 3000, 3010, {at: 1610, age_ms: 0}, true).age_ms, 1400);
});
test("browser multipart consumer handles fragmented headers and exact native payloads", async () => {
  const source = path.resolve(__dirname, "../../tools/am1_camera/mjpeg.js");
  assert.ok(fs.existsSync(source), "Bounded primary consumer missing");
  const context = {TextDecoder, Uint8Array}; vm.runInNewContext(fs.readFileSync(source, "utf8"), context);
  const payload = Buffer.from([255,216,1,2,255,217,0,0]);
  const raw = Buffer.concat([Buffer.from('--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 8\r\nX-Frame-Sequence: 7\r\nX-Frame-Age-Ms: 12\r\n\r\n'),payload,Buffer.from('\r\n')]);
  const body = new ReadableStream({start(controller) { for (const byte of raw) controller.enqueue(Uint8Array.of(byte)); controller.close(); }});
  const frames = [];
  for await (const frame of context.AM1MjpegFrames(body, () => 100)) frames.push(frame);
  assert.equal(frames.length, 1);
  assert.deepEqual(Buffer.from(frames[0].jpeg), payload);
  assert.equal(frames[0].sequence, 7);
  assert.equal(frames[0].age_ms, 12);
});

function pageFixture(decodeWorks = true, delayedStatus = false, identify = false, options = {}) {
  const decodes = [];
  class Element {
    constructor(tagName = "DIV") { this.tagName = tagName.toUpperCase(); this.parts = new Map(); this.children = []; this.flags = new Set(); this.dataset = {}; this.attributes = new Map();
      this.classList = {toggle: (key, enabled) => enabled ? this.flags.add(key) : this.flags.delete(key)}; }
    setAttribute(name, value) { this.attributes.set(name, String(value)); }
    querySelector(key) { if (!this.parts.has(key)) this.parts.set(key, new Element()); return this.parts.get(key); }
    append(value) { this.children.push(value); }
    addEventListener(name, fn) { this[name] = fn; }
    removeAttribute(key) { delete this[key]; }
    decode() {
      if (options.delayedDecode) return new Promise(resolve => decodes.push(resolve));
      return decodeWorks ? Promise.resolve() : Promise.reject(Error("bad JPEG"));
    }
  }
  const roots = new Element(), streams = [], timers = [], statusRequests = [], requests = [], urls = {created:[],revoked:[]};
  roots.querySelector("#am1-camera-root").dataset.cameraBase = options.cameraBase ?? "/";
  let now = 1000, sequence = 1;
  const cameras = () => ({forward: {configured:true,state:'fresh',age_ms:10,fps:15,sequence},
                         chest: {configured:true,state:'fresh',age_ms:10,fps:15,sequence}});
  const context = vm.createContext({TextDecoder, Uint8Array, Blob, AbortController, AbortSignal,
    performance: {now: () => now}, Image: Element,
    document: {body:{dataset:{identification:String(identify)}}, querySelector: key => roots.querySelector(key), createElement: tag => new Element(tag)},
    URL: {createObjectURL: () => {const url = `blob:test-${urls.created.length + 1}`; urls.created.push(url); return url;},
          revokeObjectURL: url => urls.revoked.push(url)},
    setTimeout: () => 1, clearTimeout: () => {}, setInterval: fn => {timers.push(fn); return timers.length;},
    fetch: async (url, options) => {
      requests.push(url);
      if (url === `${optionsBase}status.json`) {
        if (delayedStatus) return new Promise((resolve, reject) => statusRequests.push({
          reply: (data = cameras()) => resolve({ok:true,json:async () => ({cameras:data})}), reject}));
        return {ok:true,json:async () => ({cameras:cameras()})};
      }
      if (url.startsWith(`${optionsBase}api/stream.mjpeg`)) {
        const item = {signal:options.signal}; streams.push(item);
        const response = {ok:true,body:new ReadableStream({start(controller) {
          item.controller = controller;
          options.signal.addEventListener('abort', () => { try {controller.error(Error('aborted'));} catch {} });
        }})};
        if (delayedStreamResponse) return new Promise(resolve => { item.reply = () => resolve(response); });
        return response;
      }
      return {ok:true, headers:{get: name => name === 'X-Frame-Sequence' ? String(sequence) : '10'},
              blob:async () => new Blob([Buffer.from([255,216,1,2,255,217])],{type:'image/jpeg'})};
    }});
  const delayedStreamResponse = options.delayedStreamResponse;
  const optionsBase = options.cameraBase ?? "/";
  for (const file of ['freshness.js','mjpeg.js','app.js']) {
    const filename = path.resolve(__dirname, '../../tools/am1_camera', file);
    if (fs.existsSync(filename)) vm.runInContext(fs.readFileSync(filename,'utf8'),context);
  }
  return {context, roots, streams, timers, statusRequests, decodes, urls, requests,
    advance: (time, seq) => {now=time;sequence=seq;}};
}
const flush = async () => { for(let i=0;i<10;i++) await new Promise(resolve => setImmediate(resolve)); };
function multipart(sequence) {
  return Buffer.concat([
    Buffer.from(`--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 6\r\nX-Frame-Sequence: ${sequence}\r\nX-Frame-Age-Ms: 0\r\n\r\n`),
    Buffer.from([255,216,1,2,255,217]),Buffer.from('\r\n')]);
}
function deliver(page, sequence) {
  page.streams.at(-1).controller.enqueue(multipart(sequence));
}
function tile(page, index) { return page.roots.querySelector("#thumbnails").children[index].children[0]; }
test("decoded role image survives disconnect and promotion without crossing labels", async () => {
  const page = pageFixture(); await flush(); deliver(page, 1); await flush();
  const primary = page.roots.querySelector("#primary"), tiles = [0,1,2,3,4].map(index => tile(page,index));
  const forwardUrl = primary.querySelector("img").src;
  assert.ok(forwardUrl?.startsWith("blob:"));
  page.advance(1050, 2); tiles[2].click(); await flush(); deliver(page, 2); await flush();
  assert.notEqual(primary.querySelector("img").src, forwardUrl);
  assert.equal(tiles[0].querySelector("img").src, forwardUrl);
  page.advance(1700, 3); vm.runInContext('latest.cameras.forward.state = "stale"; render()', page.context);
  assert.equal(tiles[0].querySelector("img").src, forwardUrl);
  assert.match(tiles[0].querySelector("span").textContent, /last frame.*waiting/i);
  tiles[0].click(); await flush();
  assert.equal(primary.querySelector("img").src, forwardUrl);
  assert.equal(primary.querySelector("strong").textContent, "Front");
  vm.runInContext("stopPrimary()", page.context);
});
test("status sequence reset rejects a pending old-generation decode", async () => {
  const page = pageFixture(true, false, false, {delayedDecode:true}); await flush();
  deliver(page, 80); await flush(); page.decodes.shift()(); await flush();
  const primary = page.roots.querySelector("#primary"), priorUrl = primary.querySelector("img").src;
  deliver(page, 90); await flush();
  page.advance(1050, 91); await vm.runInContext("statusLoop()", page.context); await flush();
  page.advance(1100, 1); await vm.runInContext("statusLoop()", page.context); await flush();
  assert.equal(page.streams[0].signal.aborted, true, "Reset must invalidate the old stream");
  page.decodes.shift()(); await flush();
  assert.equal(primary.querySelector("img").src, priorUrl, "Old in-flight decode cannot replace held frame");
  deliver(page, 1); await flush(); page.decodes.shift()(); await flush();
  assert.notEqual(primary.querySelector("img").src, priorUrl, "New generation may start at sequence one");
  vm.runInContext("stopPrimary()", page.context);
});
test("test_anatomical_slots_and_focus", async () => {
  const page = pageFixture(); await flush();
  const slots = page.roots.querySelector("#thumbnails").children;
  assert.deepEqual(slots.map(slot => slot.dataset.role),
    ["forward", "backward", "chest", "wrist_left", "wrist_right"]);
  assert.equal(page.roots.querySelector("#primary").dataset.role, "forward");
  const original = slots.slice();
  slots[4].children[0].click(); await flush();
  assert.equal(page.roots.querySelector("#primary").dataset.role, "wrist_right");
  assert.deepEqual(slots, original, "Promoting a role cannot reshuffle the anatomy");
  assert.equal(slots[4].children[0].ariaPressed, "true");
  assert.equal(slots[0].children[0].ariaPressed, "false");
  assert.equal(slots[4].children[0].attributes.get("aria-pressed"), "true");
  assert.equal(slots[0].children[0].attributes.get("aria-pressed"), "false");
  vm.runInContext("stopPrimary()", page.context);
});
test("test_details_does_not_promote", async () => {
  const page = pageFixture(); await flush();
  const slot = page.roots.querySelector("#thumbnails").children[3];
  const button = slot.children[0], details = slot.children[1];
  assert.equal(details.tagName, "DETAILS");
  assert.equal(button.children.includes(details), false, "Details must not nest inside a button");
  details.open = true;
  if (details.click) details.click(); await flush();
  assert.equal(page.roots.querySelector("#primary").dataset.role, "forward");
  assert.equal(slot.dataset.role, "wrist_left");
  vm.runInContext("stopPrimary()", page.context);
});
test("test_camera_base_and_rotations", async () => {
  const page = pageFixture(true, false, false, {cameraBase:"/camera/"}); await flush();
  assert.ok(page.requests.includes("/camera/status.json"));
  assert.ok(page.requests.some(url => url.startsWith("/camera/api/stream.mjpeg?src=forward")));
  await vm.runInContext("snapshots()", page.context); await flush();
  assert.ok(page.requests.some(url => url.startsWith("/camera/api/frame.jpeg?src=chest")));
  vm.runInContext('latest.cameras.wrist_left = {configured:true,state:"fresh",age_ms:0,fps:15,sequence:1,rotation_degrees:90}; render()',page.context);
  assert.equal(page.roots.querySelector("#thumbnails").children[3].children[0].querySelector("img").dataset.rotation,"90");
  const html = fs.readFileSync(path.resolve(__dirname, "../../tools/am1_camera/index.html"), "utf8");
  assert.match(html, /id="am1-camera-root"[^>]*data-camera-base="\/"/);
  assert.match(html, /id="primary"[\s\S]*?<details[\s\S]*?<\/section>/, "Focus details remain below its frame");
  const css = fs.readFileSync(path.resolve(__dirname, "../../tools/am1_camera/style.css"), "utf8");
  assert.match(css, /data-identification="true"[^}]*grid-template-areas:\s*none/, "Numbered previews must not inherit semantic placement");
  vm.runInContext("stopPrimary()", page.context);
});
test("held rotated frame keeps its role orientation when status is lost", async () => {
  const page = pageFixture(true,true); await flush();
  page.statusRequests[0].reply({forward:{configured:true,state:"fresh",age_ms:0,fps:15,sequence:1,rotation_degrees:180}});
  await flush(); deliver(page,1); await flush();
  const primary = page.roots.querySelector("#primary"), oldUrl = primary.querySelector("img").src;
  const pending = vm.runInContext("statusLoop()",page.context); await flush();
  page.statusRequests[1].reject(Error("status timeout")); await pending; await flush();
  page.advance(3001,2); vm.runInContext("render()",page.context);
  assert.equal(primary.querySelector("img").src, oldUrl);
  assert.equal(primary.querySelector("img").dataset.rotation, "180");
  assert.match(primary.querySelector("span").textContent, /last frame.*waiting/i);
  vm.runInContext("stopPrimary()",page.context);
});

// Synthetic monotonic times, not measured camera or physical scene latency.
for (const delayedResponse of [false, true]) {
  test(`displayed freshness includes 400 ms before the first ${delayedResponse ? 'HTTP response' : 'multipart header'}`, async () => {
    const page = pageFixture(true, false, false, {delayedStreamResponse:delayedResponse}); await flush();
    page.advance(1400,1);
    if (delayedResponse) page.streams[0].reply();
    deliver(page,1); await flush(); vm.runInContext('render()',page.context);
    assert.equal(page.roots.querySelector('#primary').flags.has('fresh'),true,'400 ms is inside the unchanged 500 ms limit');
    page.advance(1899,2); vm.runInContext('render()',page.context);
    assert.equal(page.roots.querySelector('#primary').flags.has('fresh'),false,'899 ms must not be reported as 499 ms/fresh');
    assert.equal(page.streams[0].signal.aborted,false,'Freshness must fail even before the decoded-progress stall timer');
    vm.runInContext('stopPrimary()',page.context);
  });
}

test('fragmented header, delayed body and decode cannot renew displayed freshness', async () => {
  const page = pageFixture(true,false,false,{delayedDecode:true}); await flush();
  const raw = multipart(1), headerEnd = raw.indexOf('\r\n\r\n') + 4;
  page.advance(1200,1); page.streams[0].controller.enqueue(raw.subarray(0,20)); await flush();
  page.advance(1400,1); page.streams[0].controller.enqueue(raw.subarray(20,headerEnd)); await flush();
  page.advance(1500,1); page.streams[0].controller.enqueue(raw.subarray(headerEnd)); await flush();
  page.advance(1600,1); page.decodes[0](); await flush();
  page.advance(1899,2); vm.runInContext('render()',page.context);
  assert.equal(page.roots.querySelector('#primary').flags.has('fresh'),false,'Header/body/decode completion is not frame birth');
  assert.equal(vm.runInContext('retained.get("forward", performance.now()).at',page.context),1000);
  vm.runInContext('stopPrimary()',page.context);
});

function timedParser() {
  let now = 0, controller, cancelled = false;
  const context = {TextDecoder,Uint8Array};
  for (const file of ['mjpeg.js','freshness.js']) vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../../tools/am1_camera',file),'utf8'),context);
  const body = new ReadableStream({start(value) {controller=value;}, cancel() {cancelled=true;}});
  return {body, iterator:context.AM1MjpegFrames(body,()=>now), enqueue:raw=>controller.enqueue(raw),
    advance:value=>{now=value;}, cancelled:()=>cancelled,
    state:frame=>context.AM1FrameState({state:'fresh',age_ms:0,sequence:99},now,now,frame).state};
}

test('multiple buffered parts retain receive provenance across a delayed consumer; later reads get a fresh clock', async () => {
  const parser = timedParser(), first = parser.iterator.next();
  parser.advance(400); parser.enqueue(Buffer.concat([multipart(1),multipart(2)]));
  const a = (await first).value;
  parser.advance(899); const b = (await parser.iterator.next()).value;
  assert.equal(parser.state(a),'stale'); assert.equal(parser.state(b),'stale');
  assert.equal(a.at,0); assert.equal(b.at,0,'Buffered bytes must not be retimestamped on generator resume');
  parser.advance(1000); const next = parser.iterator.next();
  parser.advance(1067); parser.enqueue(multipart(3)); const c = (await next).value;
  assert.equal(parser.state(c),'fresh','Later frames must not inherit the whole connection age');
  assert.equal(c.at,1000);
  await parser.iterator.return();
  assert.equal(parser.cancelled(),true); assert.equal(parser.body.locked,false);
});

test('a pending body followed by another fragmented header preserves each part clock', async () => {
  const parser = timedParser(), raw = multipart(1), second = multipart(2), next = parser.iterator.next();
  parser.advance(100); parser.enqueue(raw.subarray(0,raw.length-3)); await flush();
  parser.advance(200); parser.enqueue(Buffer.concat([raw.subarray(raw.length-3),second.subarray(0,20)]));
  const a = (await next).value;
  parser.advance(400); const pending = parser.iterator.next();
  parser.advance(500); parser.enqueue(second.subarray(20)); const b = (await pending).value;
  parser.advance(599);
  assert.equal(parser.state(a),'stale'); assert.equal(parser.state(b),'fresh');
  parser.advance(600); assert.equal(parser.state(b),'stale','Second header began waiting at 100, not 400 or 500');
  assert.equal(b.at,100);
  await parser.iterator.return();
});

test('empty chunks retain the outstanding wait and a no-delay control ages normally', async () => {
  const parser = timedParser(), first = parser.iterator.next();
  parser.enqueue(multipart(1)); const a = (await first).value;
  parser.advance(899); assert.equal(parser.state(a),'stale');
  parser.advance(1000); const pending = parser.iterator.next();
  parser.advance(1200); parser.enqueue(new Uint8Array()); await flush();
  parser.advance(1400); parser.enqueue(multipart(2)); const b = (await pending).value;
  assert.equal(parser.state(b),'fresh');
  parser.advance(1500); assert.equal(parser.state(b),'stale');
  assert.equal(b.at,1000);
  await parser.iterator.return();
});

test('oversized input still rejects and cancels/releases the reader', async () => {
  const parser = timedParser(), pending = parser.iterator.next();
  parser.enqueue(new Uint8Array(2008193));
  await assert.rejects(pending,/Oversized MJPEG input/);
  assert.equal(parser.cancelled(),true); assert.equal(parser.body.locked,false);
});

test("delayed status cannot cancel advancing decoded video or hide a fresh thumbnail", async () => {
  const page = pageFixture(true, true); await flush();
  page.advance(1200, 3); page.statusRequests[0].reply(); await flush(); // request began at 1000
  for (const [time, sequence] of [[1212,4],[1279,5],[1346,6],[1413,7]]) {
    page.advance(time,sequence); deliver(page,sequence); await flush();
    vm.runInContext('render()',page.context);
  }
  page.advance(1450,8); const pending = vm.runInContext('statusLoop()',page.context);
  page.advance(1480,8); deliver(page,8); await flush();
  await vm.runInContext('snapshots()',page.context); await flush();
  page.advance(1500,8); vm.runInContext('render()',page.context);
  assert.equal(page.streams[0].signal.aborted,false,'Aged metadata is not a stalled stream');
  assert.equal(page.roots.querySelector('#primary').flags.has('fresh'),true);
  assert.equal(tile(page,2).flags.has('fresh'),true);
  assert.match(page.roots.querySelector('#connection').textContent,/status.*(delayed|uncertain)/i);
  page.advance(1547,9); deliver(page,9); await flush();
  page.advance(1614,10); deliver(page,10); await flush();
  page.advance(1650,10); page.statusRequests[1].reply(); await pending; await flush();
  assert.equal(page.streams.length,1,'No status-driven stream restart');
  vm.runInContext('stopPrimary()',page.context);
});

test("repeated multipart sequence cannot renew the displayed image clock", async () => {
  const page = pageFixture(); await flush(); deliver(page,1); await flush();
  page.advance(1400,1); deliver(page,1); await flush();
  page.advance(1600,10); await vm.runInContext('statusLoop()',page.context); await flush();
  assert.equal(page.roots.querySelector('#primary').flags.has('fresh'),false);
  assert.equal(page.streams[0].signal.aborted,true);
  vm.runInContext('stopPrimary()',page.context);
});

test("continuous 15 fps viewing survives successive 200-900 ms status responses", async () => {
  const page = pageFixture(true,true); await flush();
  page.advance(1200,3); page.statusRequests[0].reply(); await flush();
  let time = 1200, sequence = 4;
  for (let cycle = 0; cycle < 6; cycle++) {
    const pending = vm.runInContext('statusLoop()',page.context); await flush();
    const frames = cycle % 2 ? 13 : 3;
    for (let i = 0; i < frames; i++) {
      time += 67; page.advance(time,sequence); deliver(page,sequence++); await flush();
      vm.runInContext('render()',page.context);
      assert.equal(page.roots.querySelector('#primary').flags.has('fresh'),true);
      assert.equal(page.streams.length,1);
    }
    page.statusRequests[cycle + 1].reply(); await pending; await flush();
  }
  vm.runInContext('stopPrimary()',page.context);
});

test("successful one-second polls plus the real post-reply delay never cancel fresh delivery", async () => {
  const page = pageFixture(true,true); await flush();
  page.advance(1200,3); page.statusRequests[0].reply(); await flush();
  let time = 1200, sequence = 4;
  for (let cycle = 0; cycle < 3; cycle++) {
    // Four 15 fps frames cover the 250 ms delay before the next poll starts.
    for (let i = 0; i < 4; i++) {
      time += 67; page.advance(time,sequence); deliver(page,sequence++); await flush();
      vm.runInContext('render()',page.context);
    }
    const pending = vm.runInContext('statusLoop()',page.context); await flush();
    for (let i = 0; i < 15; i++) {
      time += 67; page.advance(time,sequence); deliver(page,sequence++); await flush();
      vm.runInContext('render()',page.context);
      assert.equal(page.roots.querySelector('#primary').flags.has('fresh'),true);
      assert.equal(page.streams.length,1);
    }
    page.statusRequests[cycle + 1].reply(); await pending; await flush();
  }
  vm.runInContext('stopPrimary()',page.context);
});

test("repeated cached snapshot cannot renew thumbnail freshness", async () => {
  const page = pageFixture(); await flush(); await vm.runInContext('snapshots()',page.context); await flush();
  page.advance(2400,1); await vm.runInContext('statusLoop()',page.context); await flush();
  await vm.runInContext('snapshots()',page.context); await flush();
  page.advance(2600,1); await vm.runInContext('statusLoop()',page.context); await flush();
  assert.equal(tile(page,2).flags.has('fresh'),false);
  vm.runInContext('stopPrimary()',page.context);
});

test("transient status failure preserves independent decoded delivery until the original status deadline", async () => {
  const page = pageFixture(true,true); await flush(); page.statusRequests[0].reply(); await flush();
  deliver(page,1); await flush();
  page.advance(1100,2); const pending = vm.runInContext('statusLoop()',page.context); await flush();
  page.statusRequests[1].reject(Error('timed out')); await pending; await flush();
  assert.equal(page.streams[0].signal.aborted,false,'One failed poll cannot cancel an independently advancing view');
  page.advance(1200,3); deliver(page,3); await flush();
  await vm.runInContext('snapshots()',page.context); await flush();
  vm.runInContext('render()',page.context);
  assert.equal(page.roots.querySelector('#primary').flags.has('fresh'),true);
  assert.equal(tile(page,2).flags.has('fresh'),true,'An unrelated role must still fetch and decode its own image');
  assert.match(page.roots.querySelector('#connection').textContent,/status.*uncertain/i);
  page.advance(3001,4); vm.runInContext('render()',page.context); await flush();
  assert.equal(page.streams[0].signal.aborted,true,'The original successful reply keeps its unchanged 2 s deadline');
  assert.equal(page.roots.querySelector('#primary').flags.has('fresh'),false);
  assert.equal(tile(page,2).flags.has('fresh'),false);
  assert.match(page.roots.querySelector('#connection').textContent,/status unavailable/i);
});

test("an explicit disconnected source is hidden even if its last decoded image is recent", async () => {
  const page = pageFixture(true,true); await flush(); page.statusRequests[0].reply(); await flush();
  deliver(page,1); await flush();
  const pending = vm.runInContext('statusLoop()',page.context); await flush();
  page.statusRequests[1].reply({forward:{configured:true,state:'stale',age_ms:10,fps:0,sequence:1}});
  await pending; await flush();
  assert.equal(page.streams[0].signal.aborted,true);
  assert.equal(page.roots.querySelector('#primary').flags.has('fresh'),false);
});

test("role switching cancels the previous primary and waits for a newly decoded image", async () => {
  const page = pageFixture(); await flush(); deliver(page,1); await flush();
  tile(page,2).click(); await flush();
  assert.equal(page.streams[0].signal.aborted,true);
  assert.equal(page.roots.querySelector('#primary').flags.has('fresh'),false);
  page.advance(1100,2); deliver(page,2); await flush(); vm.runInContext('render()',page.context);
  assert.equal(page.roots.querySelector('#primary').flags.has('fresh'),true);
  assert.equal(page.roots.querySelector('#primary').querySelector('strong').textContent,'Chest');
  vm.runInContext('stopPrimary()',page.context);
});

test("unmapped tiles do not pretend to be previews or disconnected mapped cameras", async () => {
  const page = pageFixture(true,true); await flush();
  page.statusRequests[0].reply({backward:{configured:false,state:'unavailable',age_ms:null,sequence:0},
                              forward:{configured:true,state:'unavailable',age_ms:null,sequence:0}});
  await flush();
  assert.match(tile(page,1).querySelector('.unavailable').textContent,/unassigned/i);
  assert.match(page.roots.querySelector('#primary').querySelector('.unavailable').textContent,/mapped/i);
});

test("identification displays numbered live sources without assigning physical roles", async () => {
  const page = pageFixture(true,true,true); await flush();
  page.statusRequests[0].reply({preview_4:{configured:true,state:'fresh',age_ms:10,fps:15,sequence:4}});
  await flush();
  const preview = tile(page,3);
  assert.equal(preview.querySelector('strong').textContent,'Camera 4');
  preview.click(); await flush(); deliver(page,4); await flush(); vm.runInContext('render()',page.context);
  assert.equal(page.roots.querySelector('#primary').flags.has('fresh'),true);
  assert.equal(page.roots.querySelector('#primary').querySelector('strong').textContent,'Camera 4');
  assert.match(page.roots.querySelector('#view-heading').textContent,/identification/i);
  vm.runInContext('stopPrimary()',page.context);
});

test("role rotations follow both thumbnails and primary selection without rotating labels", async () => {
  const page = pageFixture(true,true); await flush();
  page.statusRequests[0].reply({forward:{configured:true,state:'fresh',age_ms:10,fps:15,sequence:4,rotation_degrees:180},
                              wrist_left:{configured:true,state:'fresh',age_ms:10,fps:15,sequence:4,rotation_degrees:90},
                              wrist_right:{configured:true,state:'fresh',age_ms:10,fps:15,sequence:4,rotation_degrees:270}});
  await flush();
  const primary = page.roots.querySelector('#primary');
  const tiles = [0,1,2,3,4].map(index => tile(page,index));
  assert.equal(primary.querySelector('img').dataset.rotation,'180');
  assert.equal(tiles[3].querySelector('img').dataset.rotation,'90');
  assert.equal(tiles[4].querySelector('img').dataset.rotation,'270');
  tiles[3].click(); await flush();
  assert.equal(primary.querySelector('img').dataset.rotation,'90');
  assert.equal(primary.querySelector('strong').textContent,'Left wrist');
  tiles[4].click(); await flush();
  assert.equal(primary.querySelector('img').dataset.rotation,'270');
  assert.equal(primary.dataset.rotation,undefined,'Labels/whole tile must not rotate');
  vm.runInContext('stopPrimary()',page.context);
});

test("numbered preview rotation is display-only and absent rotation defaults to upright", async () => {
  const page = pageFixture(true,true,true); await flush();
  page.statusRequests[0].reply({preview_1:{configured:true,state:'fresh',age_ms:10,fps:15,sequence:4},
                              preview_3:{configured:true,state:'fresh',age_ms:10,fps:15,sequence:4,rotation_degrees:90}});
  await flush();
  assert.equal(page.roots.querySelector('#primary').querySelector('img').dataset.rotation,'0');
  const preview = tile(page,2);
  assert.equal(preview.querySelector('img').dataset.rotation,'90');
  preview.click(); await flush(); deliver(page,4); await flush(); vm.runInContext('render()',page.context);
  assert.equal(page.roots.querySelector('#primary').flags.has('fresh'),true);
  assert.equal(page.roots.querySelector('#primary').querySelector('img').dataset.rotation,'90');
  vm.runInContext('stopPrimary()',page.context);
});

test("bounded browser diagnostics distinguish delivery, display, decode errors and cancellation", async () => {
  const page = pageFixture(false,true); await flush();
  page.advance(1200,3); page.statusRequests[0].reply(); await flush();
  page.advance(1210,4); deliver(page,4); await flush();
  page.advance(1800,12); vm.runInContext('render()',page.context); await flush();
  const text = page.roots.querySelector('#diagnostics').textContent;
  assert.match(text,/status[^\n]*200 ms/i);
  assert.match(text,/received[^\n]*1/i);
  assert.match(text,/displayed[^\n]*0/i);
  assert.match(text,/decode failures[^\n]*1/i);
  assert.match(text,/display-stall/i);
  vm.runInContext('stopPrimary()',page.context);
});

test("app hides a non-delivering primary despite fresh status, then freezes/aborts on a stalled stream", async () => {
  const page = pageFixture(); await flush();
  const primary = page.roots.querySelector('#primary');
  assert.equal(primary.flags.has('fresh'),false,'No delivered/decoded primary frame yet');
  assert.equal(page.streams.length,1);
  const raw = Buffer.concat([Buffer.from('--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 6\r\nX-Frame-Sequence: 1\r\nX-Frame-Age-Ms: 10\r\n\r\n'),Buffer.from([255,216,1,2,255,217]),Buffer.from('\r\n')]);
  page.streams[0].controller.enqueue(raw); await flush();
  vm.runInContext('render()',page.context);
  assert.equal(primary.flags.has('fresh'),true);
  page.advance(1600,10); await vm.runInContext('statusLoop()',page.context); await flush();
  assert.equal(primary.flags.has('fresh'),false,'Live status must not renew a frozen image');
  assert.equal(page.streams[0].signal.aborted,true,'Stalled consumption must be stopped');
  vm.runInContext('stopPrimary()',page.context);
});
test("app never renews thumbnail freshness when JPEG decoding fails", async () => {
  const page = pageFixture(false); await flush();
  await vm.runInContext('snapshots()',page.context); await flush();
  vm.runInContext('render()',page.context);
  assert.equal(tile(page,2).flags.has('fresh'),false);
  if (vm.runInContext('typeof stopPrimary',page.context) === 'function') vm.runInContext('stopPrimary()',page.context);
});


test("structured role health preserves real frame age, role identity and source uncertainty", async () => {
  const page = pageFixture(true,true); await flush(); page.statusRequests[0].reply(); await flush();
  deliver(page,1); await flush();
  page.advance(1100,2); await vm.runInContext('snapshots()',page.context); await flush();
  const first = vm.runInContext('AM1CameraHealth()',page.context);
  assert.equal(first.version,1);
  assert.equal(first.selected_role,'forward');
  assert.equal(first.roles.length,5);
  const forward = first.roles.find(role=>role.role==='forward');
  assert.equal(forward.identity,'forward');
  assert.equal(forward.selected,true);
  assert.equal(forward.sequence,1);
  assert.equal(forward.generation,1);
  assert.equal(forward.decoded_generation,1);
  assert.equal(forward.decoded_age_ms,100);
  assert.equal(forward.fresh,true);
  page.advance(1200,3); const pending = vm.runInContext('statusLoop()',page.context); await flush();
  page.statusRequests[1].reject(Error('status failure')); await pending; await flush();
  page.advance(1400,4);
  const held = vm.runInContext('AM1CameraHealth()',page.context);
  assert.equal(held.status_uncertain,true);
  assert.equal(held.status_received_age_ms,400,'A failed poll cannot re-age retained status');
  assert.equal(held.roles.find(role=>role.role==='forward').decoded_age_ms,400);
  assert.equal(held.roles.find(role=>role.role==='chest').decoded_age_ms,310);
  assert.equal(held.roles.find(role=>role.role==='backward').decoded_age_ms,null);
  page.advance(3001,5);
  const expired = vm.runInContext('AM1CameraHealth()',page.context);
  assert.equal(expired.status_available,false);
  assert.equal(expired.roles.find(role=>role.role==='chest').fresh,false);
  assert.equal(expired.roles.find(role=>role.role==='chest').sequence,2);
  assert.equal(expired.roles.find(role=>role.role==='chest').decoded_age_ms,1911);
  vm.runInContext('stopPrimary()',page.context);
});


test("bounded per-role reconnection leaves other views running and rejects old generation evidence", async () => {
  const page = pageFixture(); await flush(); deliver(page,1); await flush();
  await vm.runInContext('snapshots()',page.context); await flush();
  const before = vm.runInContext('AM1CameraHealth()',page.context);
  const chest = before.roles.find(role=>role.role==='chest');
  assert.equal(chest.fresh,true);
  const reply = vm.runInContext('AM1CameraReconnect("chest")',page.context);
  assert.equal(reply.accepted,true);
  assert.equal(reply.attempt,1);
  assert.equal(page.streams[0].signal.aborted,false,'Chest reconnect must not interrupt forward coverage');
  const pending = vm.runInContext('AM1CameraHealth()',page.context).roles.find(role=>role.role==='chest');
  assert.equal(pending.generation,2);
  assert.equal(pending.decoded_generation,1);
  assert.equal(pending.sequence,1);
  assert.equal(pending.fresh,false,'An old-generation retained image cannot qualify reconnection');
  await flush();
  assert.equal(vm.runInContext('AM1CameraHealth()',page.context).roles.find(role=>role.role==='chest').fresh,false,
    'A transport reconnect cannot re-age a cached copy with the same source sequence');
  page.advance(1100,2); await vm.runInContext('snapshots()',page.context); await flush();
  const restored = vm.runInContext('AM1CameraHealth()',page.context).roles.find(role=>role.role==='chest');
  assert.equal(restored.decoded_generation,2);
  assert.equal(restored.fresh,true);
  assert.equal(vm.runInContext('AM1CameraReconnect("chest")',page.context).accepted,false,'Immediate retries are rate limited');
  assert.equal(vm.runInContext('AM1CameraReconnect("unexpected")',page.context).accepted,false);
  assert.equal(vm.runInContext('AM1CameraReconnect("backward")',page.context).accepted,false,'Unassigned views cannot qualify');
  page.advance(2100,2); await vm.runInContext('statusLoop()',page.context); await flush();
  assert.equal(vm.runInContext('AM1CameraReconnect("chest")',page.context).attempt,2); await flush();
  page.advance(3200,3); await vm.runInContext('statusLoop()',page.context); await flush();
  assert.equal(vm.runInContext('AM1CameraReconnect("chest")',page.context).attempt,3); await flush();
  page.advance(4300,4); await vm.runInContext('statusLoop()',page.context); await flush();
  assert.equal(vm.runInContext('AM1CameraReconnect("chest")',page.context).accepted,false,'Reconnection attempts stay bounded for this page lifetime');
  vm.runInContext('stopPrimary()',page.context);
});
