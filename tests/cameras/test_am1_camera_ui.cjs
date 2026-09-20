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

function pageFixture(decodeWorks = true) {
  class Element {
    constructor() { this.parts = new Map(); this.children = []; this.flags = new Set();
      this.classList = {toggle: (key, enabled) => enabled ? this.flags.add(key) : this.flags.delete(key)}; }
    querySelector(key) { if (!this.parts.has(key)) this.parts.set(key, new Element()); return this.parts.get(key); }
    append(value) { this.children.push(value); }
    addEventListener() {}
    removeAttribute(key) { delete this[key]; }
    decode() { return decodeWorks ? Promise.resolve() : Promise.reject(Error("bad JPEG")); }
  }
  const roots = new Element(), streams = [], timers = [];
  let now = 1000, sequence = 1;
  const context = vm.createContext({TextDecoder, Uint8Array, Blob, AbortController, AbortSignal,
    performance: {now: () => now}, Image: Element,
    document: {querySelector: key => roots.querySelector(key), createElement: () => new Element()},
    URL: {createObjectURL: () => 'blob:test', revokeObjectURL: () => {}},
    setTimeout: () => 1, setInterval: fn => {timers.push(fn); return timers.length;},
    fetch: async (url, options) => {
      if (url === '/status.json') return {ok: true, json: async () => ({cameras: {
        forward: {state:'fresh',age_ms:10,fps:15,sequence}, chest: {state:'fresh',age_ms:10,fps:15,sequence}}})};
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
  return {context, roots, streams, timers, advance: (time, seq) => {now=time;sequence=seq;}};
}
const flush = async () => { for(let i=0;i<10;i++) await new Promise(resolve => setImmediate(resolve)); };

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
