"use strict";

// Browser input is a short lease, never a motor owner. The native Windows
// client alone reads physical leaders and forwards actions to the Pi.
class AM1BrowserInput {
  constructor(send) {
    this.send = send;
    this.sessionId = null;
    this.token = null;
    this.epoch = 0;
    this.seq = 0;
    this.route = "control";
    this.live = false;
    this.held = new Set();
  }
  attach(sessionId, token, epoch) {
    this.release();
    this.sessionId = sessionId;
    this.token = token;
    this.epoch = epoch;
    this.seq = 0;
    this.tick(); // Declare the new lease empty; never inherit held keys.
  }
  keys() { return [...this.held].sort(); }
  _validKey(key) { return typeof key === "string" && "wszxadujtg".includes(key) && key.length === 1; }
  _typing(target) {
    const tag = (target?.tagName || "").toUpperCase();
    return ["INPUT", "TEXTAREA", "SELECT"].includes(tag) || target?.isContentEditable === true;
  }
  keyDown(key, target) {
    if (this.route === "control" && this.live && !this._typing(target) && this._validKey(key)) {
      this.held.add(key);
      return true;
    }
    return false;
  }
  keyUp(key) { this.held.delete(key); }
  pointerDown(key) {
    if (this.route === "control" && this.live && this._validKey(key)) this.held.add(key);
  }
  pointerUp(key) { this.held.delete(key); }
  tick() {
    if (!this.sessionId || !this.token) return;
    const active = this.live && this.route === "control";
    return this.send({session_id:this.sessionId, control_token:this.token, epoch:this.epoch,
                      seq:++this.seq, keys:active ? this.keys() : [], active});
  }
  release(send = true) {
    this.held.clear();
    this.live = false;
    if (send) this.tick();
  }
  blur() { this.release(); }
  hidden() { this.release(); }
  setRoute(route) {
    if (route !== this.route) this.release();
    this.route = route;
  }
  setLive(value) {
    if (!value) this.release();
    else if (this.route === "control") this.live = true;
  }
  prepareApproval() {
    this.held.clear();
    this.setLive(true);
    return this.tick();
  }
}
globalThis.AM1BrowserInput = AM1BrowserInput;

if (typeof document !== "undefined") {
  const csrf = document.querySelector('meta[name="am1-csrf"]').content;
  const stateText = document.querySelector("#session-state");
  const notice = document.querySelector("#control-notice");
  const input = new AM1BrowserInput(payload => {
    return post("/api/body", payload).then(result => {
      if (!result.accepted && payload.epoch === input.epoch && payload.seq === input.seq) input.release(false);
      return result;
    }).catch(() => {
      if (payload.epoch === input.epoch && payload.seq === input.seq) input.release(false);
      return {accepted:false};
    });
  });
  let state = null;
  let busy = false;

  async function post(url, payload) {
    const response = await fetch(url, {method:"POST", credentials:"same-origin", cache:"no-store",
      headers:{"Content-Type":"application/json", "X-AM1-CSRF":csrf}, body:JSON.stringify(payload)});
    if (!response.ok) throw new Error(`request refused (${response.status})`);
    return response.json();
  }
  function message(value) { notice.textContent = value; }
  function route() {
    const selected = ["control", "servos", "system", "logs", "terminal"].includes(location.hash.slice(1)) ?
      location.hash.slice(1) : "control";
    input.setRoute(selected);
    document.querySelector("#view-heading").textContent = selected[0].toUpperCase() + selected.slice(1);
    document.querySelectorAll("[data-console-page]").forEach(page => {
      page.hidden = page.dataset.consolePage !== selected;
    });
  }
  async function readState() {
    try {
      const response = await fetch("/api/state", {cache:"no-store", credentials:"same-origin"});
      if (!response.ok) throw new Error("session state unavailable");
      state = await response.json();
      stateText.textContent = state.session_id ? `Session ${state.session_id}: ${state.phase}` : "No session is active.";
      const saved = sessionStorage.getItem("am1-control-owner");
      if (saved && !input.sessionId && state.session_id) {
        const owner = JSON.parse(saved);
        if (owner.session_id === state.session_id && owner.input_epoch === state.input_epoch)
          input.attach(owner.session_id, owner.control_token, owner.input_epoch);
      }
      const gate = state.pending_gate;
      document.querySelector("#gate-state").textContent = gate ?
        `Approval needed: ${gate[0]} (host epoch ${gate[1] ?? "before live"}). Hold leaders still and release body keys.` :
        "No manual approval pending.";
    } catch {
      stateText.textContent = "Session state unavailable; no motor readiness implied.";
      input.release();
    }
  }
  async function operation(kind) {
    if (busy) return;
    busy = true;
    try {
      if (kind === "Resume" || kind === "Approve") {
        const lease = await input.prepareApproval();
        if (!lease?.accepted) throw new Error("fresh empty control lease was not accepted");
      }
      const payload = {kind, session_id:state?.session_id, control_token:input.token,
                       host_epoch:state?.pending_gate?.[1] ?? null};
      if (kind === "Start") {
        payload.duration_seconds = Number(document.querySelector("#duration-seconds").value);
        payload.leader_source = "physical";
      }
      const result = await post("/api/operation", payload);
      if (!result.accepted) { message(result.reason || "Operation refused; no motion approval granted."); return; }
      if (["Start", "ClaimInput"].includes(kind) && result.control_token) {
        input.attach(result.session_id, result.control_token, result.input_epoch);
        sessionStorage.setItem("am1-control-owner", JSON.stringify({session_id:result.session_id,
          control_token:result.control_token, input_epoch:result.input_epoch}));
        input.setLive(true); // Zero heartbeat prepares the typed startup gates.
      }
      if (kind === "Pause" || kind === "Stop") input.release();
      if (kind === "Resume") input.setLive(true);
      message(`${kind} accepted. Actual readiness and cleanup follow the session state.`);
      await readState();
    } catch (error) {
      input.release();
      message(`Control connection failed: ${error.message}. Body input released.`);
    } finally { busy = false; }
  }
  document.querySelectorAll("[data-operation]").forEach(button =>
    button.addEventListener("click", () => operation(button.dataset.operation)));
  document.querySelectorAll("[data-body-key]").forEach(button => {
    const key = button.dataset.bodyKey;
    button.addEventListener("pointerdown", event => { event.preventDefault(); input.pointerDown(key); });
    for (const type of ["pointerup", "pointercancel", "pointerleave", "lostpointercapture"])
      button.addEventListener(type, () => input.pointerUp(key));
  });
  document.addEventListener("keydown", event => {
    if (event.key.toLowerCase() === "q" && input.route === "control" && !input._typing(event.target)) {
      event.preventDefault(); operation("Stop"); return;
    }
    if (input.keyDown(event.key.toLowerCase(), event.target)) event.preventDefault();
  });
  document.addEventListener("keyup", event => input.keyUp(event.key.toLowerCase()));
  document.addEventListener("visibilitychange", () => { if (document.hidden) input.hidden(); });
  window.addEventListener("blur", () => input.blur());
  window.addEventListener("pagehide", () => input.release());
  window.addEventListener("hashchange", route);
  route();
  readState();
  setInterval(() => { if (input.live) input.tick(); }, 100);
  setInterval(readState, 500);
}
