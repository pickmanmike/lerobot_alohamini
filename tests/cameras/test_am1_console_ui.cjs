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

function loadViews() {
  const source = path.resolve(__dirname, "../../tools/am1_console_ui/app.js");
  const context = vm.createContext({setInterval: () => 1, clearInterval: () => {},
                                    setTimeout: () => 1, clearTimeout: () => {}});
  vm.runInContext(fs.readFileSync(source, "utf8"), context);
  return context.AM1ConsoleViews;
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

test("servo details show identity, provenance and unavailable values", () => {
  const Views = loadViews();
  const rows = Views.servoRows({
    "leader.left_bus.arm_left_elbow_flex.pos": {position:{value:2, unit:"normalized -100..100",
      state:"Live", source:"Windows native leader sample", age_ms:80}},
    "follower.left_bus.arm_left_elbow_flex.pos": {position:{value:1.5, unit:"normalized -100..100",
      state:"Snapshot", source:"Pi follower observation", age_ms:120},
      current:{value:null, unit:"mA", state:"Not sampled", source:"Pi motor record", age_ms:null}}
  });
  assert.equal(rows.length, 2);
  assert.equal(rows[0].identity, "follower.left_bus.arm_left_elbow_flex.pos");
  assert.equal(rows[1].identity, "leader.left_bus.arm_left_elbow_flex.pos");
  assert.equal(rows[0].current, "Not sampled");
  assert.match(rows[0].position, /1.5.*normalized -100..100.*Snapshot.*Pi follower observation/);
});

test("route changes clear held input but preserve session identity", () => {
  const Input = loadInput(), sent = [];
  const input = new Input(payload => sent.push(payload));
  input.attach("session-1", "secret", 4);
  input.setLive(true);
  input.keyDown("w", {tagName:"BODY"});
  for (const page of ["servos", "system", "logs", "terminal"]) {
    input.setRoute(page);
    assert.equal(input.sessionId, "session-1");
    assert.deepEqual(Array.from(input.keys()), []);
    assert.equal(sent.at(-1).active, false);
  }
  const html = fs.readFileSync(path.resolve(__dirname, "../../tools/am1_console_ui/index.html"), "utf8");
  assert.equal((html.match(/data-operation="Stop"/g) || []).length, 1);
  assert.match(html, /class="global-stop"/);
});

test("logs filter is bounded and preserves timestamped original lines", () => {
  const Views = loadViews();
  const result = Views.filteredLog("2026-10-01T12:00:00 good\n2026-10-01T12:00:01 fault\n"
    + Array(600).fill("2026-10-01T12:00:02 fault").join("\n"), "fault", 3);
  assert.equal(result.split("\n").length, 3);
  assert.match(result, /2026-10-01T12:00:02 fault/);
  assert.doesNotMatch(result, /good/);
});

test("terminal is a read-only event view without command form", () => {
  const html = fs.readFileSync(path.resolve(__dirname, "../../tools/am1_console_ui/index.html"), "utf8");
  const terminal = html.match(/<main data-console-page="terminal"[\s\S]*?<\/main>/)?.[0] || "";
  assert.match(terminal, /terminal-events/);
  assert.doesNotMatch(terminal, /<input|<textarea|<form/i);
});

test("new session invalidates earlier log text and in-flight response", () => {
  const Views = loadViews();
  assert.equal(Views.acceptLogResponse("old-session", "old-session"), true);
  assert.equal(Views.acceptLogResponse("old-session", "new-session"), false);
  assert.equal(Views.acceptLogResponse(null, "new-session"), false);
  assert.equal(Views.logUrl("client", "old-session"), "/api/log?kind=client&session_id=old-session");
  assert.equal(Views.logUrl("host", "old-session", true),
    "/api/log?kind=host&session_id=old-session&download=1");
});

test("terminal host state is historical rather than current Active", () => {
  const Views = loadViews();
  const observed = {host_state:"active", host_epoch:2, age_ms:3500};
  assert.match(Views.hostStatus(observed, "complete"), /Last observed.*Snapshot/);
  assert.doesNotMatch(Views.hostStatus(observed, "complete"), /^active/);
  assert.match(Views.hostStatus({host_state:"paused", host_epoch:3, age_ms:50}, "host_ready"), /^paused/);
});

test("log severity filter and separate original terminal sources are available", () => {
  const Views = loadViews();
  const filtered = Views.filteredLog("2026-10-01 INFO normal\n2026-10-01 ERROR fault", "", 400, "error");
  assert.match(filtered, /ERROR fault/);
  assert.doesNotMatch(filtered, /INFO normal/);
  const html = fs.readFileSync(path.resolve(__dirname, "../../tools/am1_console_ui/index.html"), "utf8");
  assert.match(html, /id="log-severity"/);
  assert.match(html, /id="log-follow"/);
  assert.match(html, /id="log-pause"/);
  assert.match(html, /id="terminal-kind"/);
  assert.match(html, /id="terminal-output"/);
});

test("servo schematic separates two follower arms, lift and unsampled wheels", () => {
  const Views = loadViews();
  const groups = Views.schematicGroups({
    "follower.left_bus.arm_left_elbow_flex.pos": {position:{value:1, state:"Live", unit:"normalized -100..100"}},
    "leader.left_bus.arm_left_elbow_flex.pos": {position:{value:2, state:"Live", unit:"normalized -100..100"}}
  }, {"lift_axis.height_mm": {value:10, state:"Live", unit:"mm"}});
  assert.equal(groups.left.length, 6);
  assert.equal(groups.right.length, 6);
  assert.match(groups.left[2].value, /1.*normalized -100..100/);
  assert.equal(groups.wheels.length, 3);
  assert.ok(groups.wheels.every(wheel => wheel.value === "Not sampled"));
  assert.match(groups.lift.value, /10.*mm/);
  const html = fs.readFileSync(path.resolve(__dirname, "../../tools/am1_console_ui/index.html"), "utf8");
  assert.match(html, /id="servo-schematic"/);
});
