"use strict";
const assert = require("node:assert/strict");
const {test} = require("node:test");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");

function loadInput() {
  const source = path.resolve(__dirname, "../../tools/am1_console_ui/app.js");
  const context = vm.createContext({performance:{now:()=>0}, setInterval: () => 1, clearInterval: () => {},
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

function loadPresentation() {
  const context = vm.createContext({performance:{now:()=>0}});
  vm.runInContext(fs.readFileSync(path.resolve(__dirname, "../../tools/am1_console_ui/app.js"), "utf8"), context);
  return context.AM1ConsolePresentation;
}

test("startup presentation uses measured frame estimates and waits for real final readiness", () => {
  const UI = loadPresentation();
  assert.match(UI.startup({stage:"arm_sync", step:6, total:7, remaining_estimate_s:29.3}), /Step 6 of 7.*estimated.*30/i);
  assert.match(UI.startup({stage:"arm_sync", step:6, waiting:true, elapsed_s:3}), /waiting/i);
  assert.match(UI.startup(null), /not yet reported/i);
});

test("countdown interpolates a native remaining sample, not browser Start or Resume", () => {
  const UI = loadPresentation();
  assert.match(UI.countdown({state:"Unavailable"}, 0, 0), /unavailable/i);
  assert.match(UI.countdown({state:"Current", remaining_s:85, age_s:0}, 1000, 2000), /1:24/);
  assert.match(UI.countdown({state:"Current", remaining_s:85, age_s:0}, 1000, 4500), /stale/i);
  assert.match(UI.countdown({state:"Elapsed", remaining_s:0}, 0, 0), /elapsed.*stopping/i);
  assert.match(UI.countdown({state:"Stopped", remaining_s:40}, 0, 0), /timer stopped.*cleanup/i);
  assert.doesNotMatch(UI.countdown({state:"Stopped", remaining_s:40}, 0, 0), /session ended/i);
});

test("natural status keeps accepted requests, cleanup and primary failures distinct", () => {
  const UI = loadPresentation();
  assert.match(UI.status({phase:"stopping", cleanup_verified:null}), /stopping.*cleanup/i);
  assert.doesNotMatch(UI.status({phase:"stopping"}), /stopped by operator/i);
  assert.match(UI.status({phase:"failed", error:"real refusal", final_exit_code:2}), /real refusal/);
  assert.match(UI.status({phase:"paused", pending_gate:["resume",3]}), /release.*Resume/i);
  assert.match(UI.status({phase:"operator_stopped", cleanup_verified:true, final_exit_code:130}), /stopped.*cleanup verified/i);
  assert.match(UI.status({phase:"cleanup_unknown",error:"original fault"}), /unverified.*do not restart.*original fault/i);
  assert.match(UI.status({phase:"client_exited",session_id:"session"}), /cleanup.*final result/i);
});

test("restart guidance distinguishes preflight refusal, verified cleanup and unknown ownership", () => {
  const UI = loadPresentation();
  const preflight = UI.status({phase:"failed", error:"source mismatch", final_exit_code:2,
                              restart_allowed:true, preflight_refused:true, cleanup_verified:null});
  assert.match(preflight, /source mismatch/i);
  assert.match(preflight, /no hardware.*start/i);
  assert.match(preflight, /select Start.*retry/i);
  assert.doesNotMatch(preflight, /cleanup verified|do not restart/i);
  const cleaned = UI.status({phase:"failed", error:"approval timeout", final_exit_code:2,
                            restart_allowed:true, preflight_refused:false, cleanup_verified:true});
  assert.match(cleaned, /approval timeout.*cleanup verified.*select Start.*retry/i);
  assert.doesNotMatch(cleaned, /complete|success|stopped by operator/i);
  assert.doesNotMatch(UI.status({phase:"cleanup_unknown", error:"pipe close failed",
                                 cleanup_verified:false, restart_allowed:false}), /select Start/i);
});

test("live output uses a separate session-bound route and reports truncation", () => {
  const Views = loadViews();
  assert.equal(Views.outputUrl("host", "session-1"), "/api/output?kind=host&session_id=session-1");
  const label = Views.outputLabel({session_id:"session-1", source:"host", path:"/logs/exact.log",
    state:"Retained output", acquired_at_ns:1000000000, truncated:true});
  assert.match(label, /session-1.*host.*Retained output/);
  assert.match(label, /truncated|gap/);
  assert.match(label, /1970-01-01T00:00:01/);
});

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

test("a short callback gap clears held body input without a full pause or replay", () => {
  const Input = loadInput(), sent = [];
  let now = 0;
  const input = new Input(payload => sent.push(payload), () => now);
  input.attach("session-1", "test-only", 1);
  input.setLive(true);
  input.keyDown("w", {tagName:"BODY"});
  input.pointerDown("u");
  input.tick();
  now = 249;
  input.tick();
  assert.equal(sent.at(-1).keys.join(""), "uw");
  now = 1189; // Measured class of short foreground scheduling gap, not presence loss.
  input.tick();
  assert.deepEqual(Array.from(sent.at(-1).keys), [], "expired held movement must not be renewed");
  assert.equal(sent.at(-1).active, true, "browser presence is separate from body expiry");
  assert.equal(input.live, true);
  input.keyDown("w", {tagName:"BODY"}, true); // OS auto-repeat is not a new press.
  input.pointerDown("u"); // Still held, not released.
  input.tick();
  assert.deepEqual(Array.from(sent.at(-1).keys), []);
  input.keyUp("w");
  input.pointerUp("u");
  input.keyDown("w", {tagName:"BODY"}, false);
  input.pointerDown("u");
  input.tick();
  assert.equal(sent.at(-1).keys.join(""), "uw", "deliberate release and new press may reacquire");
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

test("input release names the first initiating event separately from later symptoms", () => {
  const Input = loadInput(), sent = [];
  const input = new Input(payload => sent.push(payload));
  input.attach("session-1", "secret", 1);
  input.setLive(true);
  input.blur();
  assert.equal(sent.at(-1).release_reason, "window-blur");
  assert.equal(input.firstRelease.reason, "window-blur");
  input.hidden();
  assert.equal(input.firstRelease.reason, "window-blur");
  assert.equal(sent.at(-1).release_reason, "document-hidden");
  assert.equal(JSON.stringify(input.firstRelease).includes("secret"), false);
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

test("startup approval carries the displayed gate, owner and host epoch", () => {
  const Input = loadInput();
  const input = new Input(() => {});
  input.attach("session-1", "owner-token", 1);
  const payload = input.approvalPayload("Resume", ["sync_start", null]);
  assert.equal(JSON.stringify(payload), JSON.stringify({kind:"Resume", session_id:"session-1",
    control_token:"owner-token", gate_stage:"sync_start", host_epoch:null}));
  assert.equal(input.approvalPayload("Resume", ["resume", 3]).host_epoch, 3);
  assert.equal(input.approvalPayload("Resume", ["resume", 3]).gate_stage, "resume");
});

test("approval cannot switch gates or owner while its empty lease is pending", async () => {
  const Input = loadInput();
  let finish;
  const input = new Input(() => new Promise(resolve => { finish = resolve; }));
  input.attach("session-1", "owner-token", 1);
  const gate = ["sync_start", null];
  const pending = input.prepareApprovalRequest("Resume", gate);
  gate[0] = "live_start";
  input.token = "new-owner";
  finish({accepted:true});
  const result = await pending;
  assert.equal(result.accepted, true);
  assert.equal(result.payload.gate_stage, "sync_start");
  assert.equal(result.payload.control_token, "owner-token");
});

test("periodic body requests cannot overtake the explicit empty approval packet", async () => {
  const Input = loadInput(), sent = [];
  let hold = false, finish;
  const input = new Input(payload => {
    sent.push(payload);
    return hold ? new Promise(resolve => { finish = resolve; }) : Promise.resolve({accepted:true});
  });
  input.attach("session-1", "owner-token", 1);
  input.setLive(true);
  input.keyDown("w", {tagName:"BODY"});
  hold = true;
  const before = sent.length;
  const approval = input.prepareApprovalRequest("Resume", ["resume", 3]);
  const complete = finish;
  input.tick(); // The existing 100 ms scheduler may run while HTTP is pending.
  input.keyDown("u", {tagName:"BODY"});
  input.pointerDown("j");
  input.tick();
  assert.equal(sent.length, before + 1, "a later sequence must not supersede the approval request");
  assert.equal(sent.at(-1).active, true);
  assert.deepEqual(Array.from(sent.at(-1).keys), []);
  complete({accepted:true});
  assert.equal((await approval).accepted, true);
  hold = false;
  input.tick();
  assert.deepEqual(Array.from(sent.at(-1).keys), [], "pending keys cannot replay after admission");
});

test("release bypasses a pending approval and invalidates its late response", async () => {
  const Input = loadInput(), sent = [];
  let hold = false, finish;
  const input = new Input(payload => {
    sent.push(payload);
    return hold && payload.active ? new Promise(resolve => { finish = resolve; }) : Promise.resolve({accepted:true});
  });
  input.attach("session-1", "owner-token", 1);
  hold = true;
  const approval = input.prepareApprovalRequest("Resume", ["resume", 3]);
  input.blur();
  assert.equal(sent.at(-1).active, false, "release cannot wait for the approval response");
  finish({accepted:true});
  assert.equal((await approval).accepted, false, "a released input cannot authorize Resume later");
  assert.equal(input.live, false);
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
  assert.match(html, /class="[^"]*\bglobal-stop\b/);
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

test("Stop cancels a pending approval and releases busy ownership for later Start", async () => {
  const nodes = new Map(), sent = [];
  const element = () => ({textContent:"", value:"120", content:"test-only", dataset:{},
    append(){}, replaceChildren(){}, addEventListener(){}, setAttribute(){}, click(){}});
  const document = {querySelector:key => {
    if (!nodes.has(key)) nodes.set(key, element());
    return nodes.get(key);
  }, querySelectorAll:()=>[], createElement:element, addEventListener(){}};
  let pendingBody, pendingStart, hold = false, phase = "host_ready";
  const context = vm.createContext({document, console, performance:{now:()=>0}, location:{hash:""},
    sessionStorage:{getItem:()=>null,setItem(){}}, window:{addEventListener(){}}, setInterval:()=>0,
    fetch:(url, options={}) => {
      const payload = options.body ? JSON.parse(options.body) : null;
      sent.push({url, payload});
      if (url === "/api/body" && hold && payload.active)
        return new Promise(resolve => { pendingBody = resolve; });
      if (url === "/api/operation" && payload.kind === "Start")
        return new Promise(resolve => { pendingStart = resolve; });
      if (payload?.kind === "Stop") phase = "complete";
      const result = url === "/api/state" ? {session_id:"session", phase, input_epoch:1,
        pending_gate:["resume",1], events:[], telemetry:{}} : {accepted:true};
      return Promise.resolve({ok:true,json:async()=>result});
    }});
  const source = fs.readFileSync(path.resolve(__dirname, "../../tools/am1_console_ui/app.js"), "utf8")
    .replace("  route();", "  globalThis.testInput=input; globalThis.testOperation=operation; globalThis.testReadState=readState; route();");
  vm.runInContext(source, context);
  await new Promise(resolve => setImmediate(resolve));
  context.testInput.attach("session", "test-only", 1);
  await context.testReadState();
  hold = true;
  const approval = context.testOperation("Resume");
  await new Promise(resolve => setImmediate(resolve));
  await context.testOperation("Stop");
  const starting = context.testOperation("Start");
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(sent.filter(item => item.url === "/api/operation").map(item => item.payload.kind), ["Stop", "Start"]);
  pendingBody({ok:true,json:async()=>({accepted:true})});
  await approval;
  await context.testOperation("Start"); // Old finally must not clear newer Start's busy flag.
  assert.equal(sent.filter(item => item.payload?.kind === "Start").length, 1);
  assert.equal(sent.filter(item => item.payload?.kind === "Resume").length, 0);
  pendingStart({ok:true,json:async()=>({accepted:true})});
  await starting;
});

function loadApp(fetchHook) {
  const nodes = new Map(), sent = [], windowListeners = new Map(), documentListeners = new Map();
  const element = () => ({textContent:"", value:"120", content:"test-only", dataset:{},
    append(){}, replaceChildren(){}, addEventListener(){}, setAttribute(){}, click(){}});
  const document = {hidden:false, querySelector:key => {
    if (!nodes.has(key)) nodes.set(key, element());
    return nodes.get(key);
  }, querySelectorAll:()=>[], createElement:element,
  addEventListener:(type, listener) => documentListeners.set(type, listener)};
  let state = {session_id:"old-session", phase:"host_ready", input_epoch:1,
    pending_gate:["resume",1], events:[], telemetry:{}};
  const context = vm.createContext({document, console:{info(){}}, performance:{now:()=>0}, location:{hash:""},
    sessionStorage:{getItem:()=>null,setItem(){}},
    window:{addEventListener:(type, listener) => windowListeners.set(type, listener)}, setInterval:()=>0,
    fetch:(url, options={}) => {
      const payload = options.body ? JSON.parse(options.body) : null;
      sent.push({url, payload});
      const held = fetchHook?.(url, payload);
      if (held) return held;
      const result = url === "/api/state" ? state : {accepted:true};
      return Promise.resolve({ok:true,json:async()=>result});
    }});
  const source = fs.readFileSync(path.resolve(__dirname, "../../tools/am1_console_ui/app.js"), "utf8")
    .replace("  route();", "  globalThis.testInput=input; globalThis.testOperation=operation; globalThis.testReadState=readState; route();");
  vm.runInContext(source, context);
  return {context, sent, nodes, windowListeners, documentListeners, setState:value => { state = value; }};
}

test("global header Start and approvals remain non-actuating off Control", async () => {
  const app=loadApp();
  await new Promise(resolve=>setImmediate(resolve));
  app.context.testInput.setRoute("logs");
  const before=app.sent.filter(r=>r.url==="/api/operation").length;
  for(const kind of ["Start","Resume","Approve","ClaimInput"]) await app.context.testOperation(kind);
  assert.equal(app.sent.filter(r=>r.url==="/api/operation").length,before);
  assert.match(app.nodes.get("#control-notice").textContent,/return to Control/i);
  await app.context.testOperation("Stop");
  assert.equal(app.sent.at(-2)?.payload?.kind || app.sent.find(r=>r.payload?.kind==="Stop")?.payload?.kind,"Stop");
});

test("server body expiry clears only movement and requires release before reacquisition", async () => {
  const app = loadApp((url, payload) => url === "/api/body" && payload.keys.length ?
    Promise.resolve({ok:true,json:async()=>({accepted:true,body_release_required:true})}) : null);
  await new Promise(resolve => setImmediate(resolve));
  const input = app.context.testInput;
  input.attach("old-session", "test-only", 1);
  input.setLive(true);
  input.keyDown("w", {tagName:"BODY"});
  await input.tick();
  assert.deepEqual(Array.from(input.keys()), []);
  assert.equal(input.live, true, "accepted presence must not become a full Pause");
  input.keyDown("w", {tagName:"BODY"}, true);
  await input.tick();
  assert.deepEqual(app.sent.at(-1).payload.keys, []);
  assert.match(app.nodes.get("#control-notice").textContent, /release.*press/i);
  input.keyUp("w");
  assert.equal(input.keyDown("w", {tagName:"BODY"}, false), true);
});

test("local callback body expiry has a bounded nonterminal notice", async () => {
  const app = loadApp();
  await new Promise(resolve => setImmediate(resolve));
  const input = app.context.testInput;
  let now = 0;
  input.now = () => now;
  input.attach("old-session", "test-only", 1);
  input.setLive(true);
  input.keyDown("w", {tagName:"BODY"});
  await input.tick();
  now = 940;
  await input.tick();
  assert.match(app.nodes.get("#control-notice").textContent, /expired.*release.*press/i);
  assert.equal(input.live, true);
  assert.deepEqual(app.sent.at(-1).payload.keys, []);
});

test("a late old body-expiry response cannot clear a replacement owner's movement", async () => {
  let finish, hold = false;
  const app = loadApp((url, payload) => url === "/api/body" && hold && payload.active &&
    payload.session_id === "old-session" ? new Promise(resolve => { finish = resolve; }) : null);
  await new Promise(resolve => setImmediate(resolve));
  const input = app.context.testInput;
  input.attach("old-session", "test-only", 1);
  input.setLive(true);
  hold = true;
  input.keyDown("w", {tagName:"BODY"});
  const old = input.tick();
  input.attach("new-session", "new-test-only", 1);
  input.setLive(true);
  input.keyDown("u", {tagName:"BODY"});
  await input.tick();
  finish({ok:true,json:async()=>({accepted:true,body_release_required:true})});
  await old;
  assert.deepEqual(Array.from(input.keys()), ["u"]);
  assert.equal(input.live, true);
});

test("verified operator cancellation is labeled stopped with only historical host feedback", async () => {
  const app = loadApp();
  await new Promise(resolve => setImmediate(resolve));
  app.setState({session_id:"old-session", phase:"operator_stopped", final_exit_code:130,
    cleanup_verified:true, events:[], telemetry:{}});
  await app.context.testReadState();
  assert.match(app.nodes.get("#session-state").textContent, /Stopped by operator/);
  const Views = loadViews();
  assert.match(Views.hostStatus({host_state:"active", host_epoch:2, age_ms:50}, "operator_stopped"),
    /Last observed.*Snapshot/);
});

for (const outcome of ["denial", "failure"]) {
  test(`a canceled old body ${outcome} cannot release a replacement owner`, async () => {
    let pendingBody, hold = false;
    const app = loadApp((url, payload) => {
      if (url === "/api/body" && hold && payload.active && payload.session_id === "old-session")
        return new Promise((resolve, reject) => { pendingBody = {resolve, reject}; });
    });
    await new Promise(resolve => setImmediate(resolve));
    const input = app.context.testInput;
    input.attach("old-session", "old-token", 1);
    hold = true;
    const approval = app.context.testOperation("Resume"); // Old epoch 1, sequence 2.
    await new Promise(resolve => setImmediate(resolve));
    await app.context.testOperation("Stop");
    app.setState({session_id:"new-session", phase:"live", input_epoch:1, events:[], telemetry:{}});
    input.attach("new-session", "new-token", 1);
    await app.context.testReadState();
    input.setLive(true);
    input.keyDown("w", {tagName:"BODY"});
    input.tick(); // Replacement owner also has epoch 1, sequence 2.
    if (outcome === "denial") pendingBody.resolve({ok:true,json:async()=>({accepted:false})});
    else pendingBody.reject(new Error("old request failed"));
    await approval;
    assert.equal(input.live, true, "the old response must not revoke the new owner's lease");
    assert.deepEqual(Array.from(input.keys()), ["w"]);
    assert.equal(app.sent.at(-1).payload.session_id, "new-session");
    assert.equal(app.sent.at(-1).payload.active, true);
  });
}

for (const release of ["blur", "hidden", "route"]) {
  test(`a late Resume operation response cannot rearm after ${release}`, async () => {
    let pendingResume;
    const app = loadApp((url, payload) => {
      if (url === "/api/operation" && payload.kind === "Resume")
        return new Promise(resolve => { pendingResume = resolve; });
    });
    await new Promise(resolve => setImmediate(resolve));
    const input = app.context.testInput;
    input.attach("old-session", "old-token", 1);
    const approval = app.context.testOperation("Resume");
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(typeof pendingResume, "function", "the real operation POST must be pending");
    if (release === "blur") app.windowListeners.get("blur")();
    else if (release === "hidden") {
      app.context.document.hidden = true;
      app.documentListeners.get("visibilitychange")();
    } else {
      input.setRoute("logs");
      input.setRoute("control"); // Returning to Control must not revive the old approval.
    }
    assert.equal(input.live, false);
    pendingResume({ok:true,json:async()=>({accepted:true})});
    await approval;
    assert.equal(input.live, false, "released input must require a new explicit current-gate approval");
    input.tick();
    assert.equal(app.sent.at(-1).payload.active, false);
    assert.deepEqual(Array.from(input.keys()), []);
  });
}
