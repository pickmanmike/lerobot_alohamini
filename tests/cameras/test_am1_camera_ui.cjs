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
    constructor() { this.parts = new Map(); this.children = []; this.flags = new Set(); this.dataset = {};
      this.classList = {toggle: (key, enabled) => enabled ? this.flags.add(key) : this.flags.delete(key)}; }
    querySelector(key) { if (!this.parts.has(key)) this.parts.set(key, new Element()); return this.parts.get(key); }
    append(value) { this.children.push(value); }
    addEventListener(name, fn) { this[name] = fn; }
    removeAttribute(key) { delete this[key]; }
    decode() {
      if (options.delayedDecode) return new Promise(resolve => decodes.push(resolve));
      return decodeWorks ? Promise.resolve() : Promise.reject(Error("bad JPEG"));
    }
  }
  const roots = new Element(), streams = [], timers = [], statusRequests = [];
  let now = 1000, sequence = 1;
  const cameras = () => ({forward: {configured:true,state:'fresh',age_ms:10,fps:15,sequence},
                         chest: {configured:true,state:'fresh',age_ms:10,fps:15,sequence}});
  const context = vm.createContext({TextDecoder, Uint8Array, Blob, AbortController, AbortSignal,
    performance: {now: () => now}, Image: Element,
    document: {body:{dataset:{identification:String(identify)}}, querySelector: key => roots.querySelector(key), createElement: () => new Element()},
    URL: {createObjectURL: () => 'blob:test', revokeObjectURL: () => {}},
    setTimeout: () => 1, setInterval: fn => {timers.push(fn); return timers.length;},
    fetch: async (url, options) => {
      if (url === '/status.json') {
        if (delayedStatus) return new Promise((resolve, reject) => statusRequests.push({
          reply: (data = cameras()) => resolve({ok:true,json:async () => ({cameras:data})}), reject}));
        return {ok:true,json:async () => ({cameras:cameras()})};
      }
      if (url.startsWith('/api/stream.mjpeg')) {
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
  for (const file of ['freshness.js','mjpeg.js','app.js']) {
    const filename = path.resolve(__dirname, '../../tools/am1_camera', file);
    if (fs.existsSync(filename)) vm.runInContext(fs.readFileSync(filename,'utf8'),context);
  }
  return {context, roots, streams, timers, statusRequests, decodes, advance: (time, seq) => {now=time;sequence=seq;}};
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
  assert.equal(vm.runInContext('displayed.get("primary").at',page.context),1000);
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
  assert.equal(page.roots.querySelector('#thumbnails').children[2].flags.has('fresh'),true);
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
  assert.equal(page.roots.querySelector('#thumbnails').children[2].flags.has('fresh'),false);
  vm.runInContext('stopPrimary()',page.context);
});

test("failed status stops delivery without silently claiming cached video is live", async () => {
  const page = pageFixture(true,true); await flush(); page.statusRequests[0].reply(); await flush();
  deliver(page,1); await flush();
  const pending = vm.runInContext('statusLoop()',page.context); await flush();
  page.statusRequests[1].reject(Error('timed out')); await pending; await flush();
  assert.equal(page.streams[0].signal.aborted,true);
  assert.equal(page.roots.querySelector('#primary').flags.has('fresh'),false);
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
  page.roots.querySelector('#thumbnails').children[2].click(); await flush();
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
  assert.match(page.roots.querySelector('#thumbnails').children[1].querySelector('.unavailable').textContent,/unassigned/i);
  assert.match(page.roots.querySelector('#primary').querySelector('.unavailable').textContent,/mapped/i);
});

test("identification displays numbered live sources without assigning physical roles", async () => {
  const page = pageFixture(true,true,true); await flush();
  page.statusRequests[0].reply({preview_4:{configured:true,state:'fresh',age_ms:10,fps:15,sequence:4}});
  await flush();
  const tile = page.roots.querySelector('#thumbnails').children[3];
  assert.equal(tile.querySelector('strong').textContent,'Camera 4');
  tile.click(); await flush(); deliver(page,4); await flush(); vm.runInContext('render()',page.context);
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
  const tiles = page.roots.querySelector('#thumbnails').children;
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
  const tile = page.roots.querySelector('#thumbnails').children[2];
  assert.equal(tile.querySelector('img').dataset.rotation,'90');
  tile.click(); await flush(); deliver(page,4); await flush(); vm.runInContext('render()',page.context);
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
  assert.equal(page.roots.querySelector('#thumbnails').children[2].flags.has('fresh'),false);
  if (vm.runInContext('typeof stopPrimary',page.context) === 'function') vm.runInContext('stopPrimary()',page.context);
});
