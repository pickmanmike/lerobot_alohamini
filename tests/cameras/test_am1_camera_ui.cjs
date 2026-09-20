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

function pageFixture(decodeWorks = true, delayedStatus = false) {
  class Element {
    constructor() { this.parts = new Map(); this.children = []; this.flags = new Set();
      this.classList = {toggle: (key, enabled) => enabled ? this.flags.add(key) : this.flags.delete(key)}; }
    querySelector(key) { if (!this.parts.has(key)) this.parts.set(key, new Element()); return this.parts.get(key); }
    append(value) { this.children.push(value); }
    addEventListener(name, fn) { this[name] = fn; }
    removeAttribute(key) { delete this[key]; }
    decode() { return decodeWorks ? Promise.resolve() : Promise.reject(Error("bad JPEG")); }
  }
  const roots = new Element(), streams = [], timers = [], statusRequests = [];
  let now = 1000, sequence = 1;
  const cameras = () => ({forward: {configured:true,state:'fresh',age_ms:10,fps:15,sequence},
                         chest: {configured:true,state:'fresh',age_ms:10,fps:15,sequence}});
  const context = vm.createContext({TextDecoder, Uint8Array, Blob, AbortController, AbortSignal,
    performance: {now: () => now}, Image: Element,
    document: {querySelector: key => roots.querySelector(key), createElement: () => new Element()},
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
        return {ok:true,body:new ReadableStream({start(controller) {
          item.controller = controller;
          options.signal.addEventListener('abort', () => { try {controller.error(Error('aborted'));} catch {} });
        }})};
      }
      return {ok:true, headers:{get: name => name === 'X-Frame-Sequence' ? String(sequence) : '10'},
              blob:async () => new Blob([Buffer.from([255,216,1,2,255,217])],{type:'image/jpeg'})};
    }});
  for (const file of ['freshness.js','mjpeg.js','app.js']) {
    const filename = path.resolve(__dirname, '../../tools/am1_camera', file);
    if (fs.existsSync(filename)) vm.runInContext(fs.readFileSync(filename,'utf8'),context);
  }
  return {context, roots, streams, timers, statusRequests, advance: (time, seq) => {now=time;sequence=seq;}};
}
const flush = async () => { for(let i=0;i<10;i++) await new Promise(resolve => setImmediate(resolve)); };
function deliver(page, sequence) {
  page.streams.at(-1).controller.enqueue(Buffer.concat([
    Buffer.from(`--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 6\r\nX-Frame-Sequence: ${sequence}\r\nX-Frame-Age-Ms: 0\r\n\r\n`),
    Buffer.from([255,216,1,2,255,217]),Buffer.from('\r\n')]));
}

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
