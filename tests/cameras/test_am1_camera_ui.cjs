"use strict";
const assert = require("node:assert/strict");
const {test} = require("node:test");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const source = path.resolve(__dirname, "../../tools/am1_camera/freshness.js");
function load() {
  assert.ok(fs.existsSync(source), "Shared displayed-frame freshness logic missing");
  const context = {}; vm.runInNewContext(fs.readFileSync(source, "utf8"), context);
  return context.AM1FrameState;
}
test("producer freshness never advances merely because a status request succeeded", () => {
  const state = load(), frame = {state: "fresh", age_ms: 10, fps: 15, sequence: 2};
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
