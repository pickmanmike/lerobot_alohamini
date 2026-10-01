"use strict";
const assert = require("node:assert/strict");
const {test} = require("node:test");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");

function loadInput() {
  const source = path.resolve(__dirname, "../../tools/am1_console_ui/app.js");
  const context = vm.createContext({setInterval: () => 1, clearInterval: () => {},
                                    setTimeout: () => 1, clearTimeout: () => {}});
  vm.runInContext(fs.readFileSync(source, "utf8"), context);
  return context.AM1BrowserInput;
}

test("control keys ignore typing and route changes release the body", () => {
  const Input = loadInput(), sent = [];
  const input = new Input(payload => sent.push(payload));
  input.attach("session-1", "secret", 3);
  input.setRoute("control");
  input.setLive(true);
  input.keyDown("w", {tagName: "INPUT"});
  assert.deepEqual(Array.from(input.keys()), []);
  input.keyDown("w", {tagName: "BODY"});
  assert.deepEqual(Array.from(input.keys()), ["w"]);
  input.tick();
  assert.equal(sent.at(-1).active, true);
  assert.equal(sent.at(-1).keys.join(""), "w");
  input.setRoute("logs");
  assert.deepEqual(Array.from(input.keys()), []);
  assert.equal(sent.at(-1).active, false);
  input.keyDown("u", {tagName: "BODY"});
  input.tick();
  assert.deepEqual(Array.from(input.keys()), []);
});

test("blur and visibility loss clear held pointer and key input without auto-resume", () => {
  const Input = loadInput(), sent = [];
  const input = new Input(payload => sent.push(payload));
  input.attach("session-1", "secret", 1);
  input.setRoute("control");
  input.setLive(true);
  input.pointerDown("u");
  assert.deepEqual(Array.from(input.keys()), ["u"]);
  input.blur();
  assert.deepEqual(Array.from(input.keys()), []);
  assert.equal(sent.at(-1).active, false);
  input.pointerDown("u");
  input.tick();
  assert.equal(sent.at(-1).active, false);
  input.setLive(true);
  input.pointerDown("u");
  input.hidden();
  assert.equal(sent.at(-1).active, false);
  assert.deepEqual(Array.from(input.keys()), []);
});

test("new epoch never replays old held keys", () => {
  const Input = loadInput(), sent = [];
  const input = new Input(payload => sent.push(payload));
  input.attach("session-1", "secret", 2);
  input.setRoute("control");
  input.setLive(true);
  input.keyDown("w", {tagName: "BODY"});
  input.tick();
  input.attach("session-1", "new-secret", 3);
  assert.deepEqual(Array.from(input.keys()), []);
  assert.equal(sent.at(-1).epoch, 3);
  assert.equal(sent.at(-1).active, false);
  assert.equal(sent.at(-1).keys.length, 0);
});

test("explicit Resume first establishes a fresh empty lease", () => {
  const Input = loadInput(), sent = [];
  const input = new Input(payload => { sent.push(payload); return Promise.resolve({accepted:true}); });
  input.attach("session-1", "secret", 1);
  input.setRoute("control");
  input.setLive(true);
  input.keyDown("w", {tagName:"BODY"});
  input.blur();
  assert.equal(input.live, false);
  input.prepareApproval();
  assert.equal(sent.at(-1).active, true);
  assert.equal(sent.at(-1).keys.length, 0);
});
