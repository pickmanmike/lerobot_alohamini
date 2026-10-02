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
  approvalPayload(kind, gate) {
    return {kind, session_id:this.sessionId, control_token:this.token,
            gate_stage:gate?.[0] ?? null, host_epoch:gate?.[1] ?? null};
  }
  async prepareApprovalRequest(kind, gate) {
    const payload = this.approvalPayload(kind, gate); // Bind the displayed gate before IO.
    const lease = await this.prepareApproval();
    return {accepted:lease?.accepted === true, payload};
  }
}
globalThis.AM1BrowserInput = AM1BrowserInput;

class AM1ConsoleViews {
  static field(entry) {
    if (!entry || entry.value === null || entry.value === undefined)
      return entry?.state || "Not sampled";
    const lowerBound = String(entry.age_basis || "").includes("lower bound");
    const age = typeof entry.age_ms === "number" ? ` · age ${lowerBound ? "≥" : ""}${Math.round(entry.age_ms)} ms` : "";
    return `${entry.value} ${entry.unit || ""} · ${entry.state || "Snapshot"} · ${entry.source || "source unknown"}${age}`;
  }
  static servoRows(servos) {
    return Object.entries(servos || {}).sort(([a], [b]) => a.localeCompare(b)).map(([identity, parts]) => ({
      identity, position:this.field(parts.position), target:this.field(parts.target),
      current:this.field(parts.current), temperature:this.field(parts.temperature),
      status:this.field(parts.status)
    }));
  }
  static schematicGroups(servos, body) {
    const joints = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"];
    const side = name => joints.map(joint => ({label:joint.replaceAll("_", " "),
      value:this.field(servos?.[`follower.${name}_bus.arm_${name}_${joint}.pos`]?.position)}));
    const leaders = Object.entries(servos || {}).filter(([identity]) => identity.startsWith("leader."))
      .sort(([a], [b]) => a.localeCompare(b)).map(([identity, values]) => ({
        label:identity.replace("leader.", ""), value:this.field(values.position)}));
    return {left:side("left"), right:side("right"), leaders,
      lift:{label:"lift axis", value:this.field(body?.["lift_axis.height_mm"])},
      wheels:[8, 9, 10].map(id => ({label:`base wheel ID ${id}`, value:"Not sampled"}))};
  }
  static filteredLog(text, query, limit = 400, severity = "all") {
    const needle = String(query || "").toLowerCase();
    const matchesSeverity = line => severity === "all" || (severity === "error" ?
      /\b(error|fault|refused|traceback|failed)\b/i.test(line) : severity === "warning" ?
      /\b(warning|warn)\b/i.test(line) : !/\b(error|fault|refused|traceback|failed|warning|warn)\b/i.test(line));
    return String(text || "").split(/\r?\n/).filter(line => line && line.toLowerCase().includes(needle) && matchesSeverity(line))
      .slice(-Math.max(1, Math.min(limit, 400))).join("\n");
  }
  static acceptLogResponse(requestedSessionId, currentSessionId) {
    return Boolean(requestedSessionId) && requestedSessionId === currentSessionId;
  }
  static logUrl(kind, sessionId, download = false) {
    return `/api/log?kind=${encodeURIComponent(kind)}&session_id=${encodeURIComponent(sessionId)}`
      + (download ? "&download=1" : "");
  }
  static outputUrl(kind, sessionId) {
    return `/api/output?kind=${encodeURIComponent(kind)}&session_id=${encodeURIComponent(sessionId)}`;
  }
  static outputLabel(output) {
    const at = output.acquired_at_ns ? new Date(output.acquired_at_ns / 1000000).toISOString() : "not received";
    return `Session ${output.session_id} · ${output.source} · ${output.state} · acquired ${at}`
      + (output.received_age_ms != null ? ` · receipt age ≥${Math.round(output.received_age_ms)} ms` : "")
      + (output.truncated ? " · excerpt truncated / forwarding gap" : "")
      + (output.path ? ` · ${output.path}` : "");
  }
  static hostStatus(observation, phase) {
    if (!observation || observation.host_state == null) return "Not sampled";
    const value = `${observation.host_state} / ${observation.host_epoch ?? "unknown"}`;
    const stale = observation.age_ms == null || observation.age_ms > 1000 ||
      ["complete", "failed", "cleanup_unknown", "stopping"].includes(phase);
    return stale ? `Last observed ${value} (Snapshot; ${Math.round(observation.age_ms ?? 0)} ms ago)` : value;
  }
  static eventLines(events) {
    return (events || []).slice(-80).map(event => {
      const stamp = Number.isInteger(event.wall_time_ns) && event.wall_time_ns > 0 ?
        new Date(event.wall_time_ns / 1e6).toISOString() : "time unavailable";
      return `${stamp} · ${event.event || "event"}${event.reason ? ` · ${event.reason}` : ""}`;
    }).join("\n");
  }
}
globalThis.AM1ConsoleViews = AM1ConsoleViews;

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
  let stateRequestInFlight = false;
  let loadedLog = "";
  let loadedLogSessionId = null;
  let loadedLogDescription = "";
  let logRequestId = 0;
  let logRequestInFlight = false;
  let terminalRequestId = 0;
  let terminalRequestInFlight = false;

  function row(container, label, value) {
    const line = document.createElement("tr");
    const name = document.createElement("th");
    const data = document.createElement("td");
    name.scope = "row";
    name.textContent = label;
    data.textContent = value;
    line.append(name, data);
    container.append(line);
  }
  function renderSnapshot(snapshot) {
    const telemetry = snapshot?.telemetry || {};
    const schematic = document.querySelector("#servo-schematic");
    schematic.replaceChildren();
    const groups = AM1ConsoleViews.schematicGroups(telemetry.servos, telemetry.body);
    for (const [title, cards] of [["Left follower arm", groups.left], ["Right follower arm", groups.right],
                                  ["Lift and base", [groups.lift, ...groups.wheels]],
                                  ...(groups.leaders.length ? [["Connected physical leaders", groups.leaders]] : [])]) {
      const section = document.createElement("section");
      section.className = "schematic-group";
      const heading = document.createElement("h3");
      heading.textContent = title;
      section.append(heading);
      const rail = document.createElement("div");
      rail.className = "schematic-rail";
      for (const card of cards) {
        const item = document.createElement("div");
        item.className = "schematic-card";
        const name = document.createElement("strong");
        name.textContent = card.label;
        const value = document.createElement("span");
        value.textContent = card.value;
        item.append(name, value);
        rail.append(item);
      }
      section.append(rail);
      schematic.append(section);
    }
    const servoBody = document.querySelector("#servo-rows");
    servoBody.replaceChildren();
    for (const details of AM1ConsoleViews.servoRows(telemetry.servos)) {
      const tr = document.createElement("tr");
      for (const key of ["identity", "position", "target", "current", "temperature", "status"]) {
        const cell = document.createElement(key === "identity" ? "th" : "td");
        if (key === "identity") cell.scope = "row";
        cell.textContent = details[key];
        tr.append(cell);
      }
      servoBody.append(tr);
    }
    const systemBody = document.querySelector("#system-rows");
    systemBody.replaceChildren();
    row(systemBody, "Session", snapshot.session_id || "No session");
    row(systemBody, "Phase / result", `${snapshot.phase || "idle"} / ${snapshot.final_exit_code ?? "pending"}`);
    row(systemBody, "Cleanup verified", snapshot.cleanup_verified == null ? "Not yet known" : snapshot.cleanup_verified ? "Yes" : "NO — inspect summary and stop before restart");
    row(systemBody, "Host state / epoch", AM1ConsoleViews.hostStatus(telemetry.observation, snapshot.phase));
    row(systemBody, "Observation age", telemetry.observation?.age_ms == null ? "Not sampled" : `${Math.round(telemetry.observation.age_ms)} ms`);
    row(systemBody, "Sent action sequence", telemetry.action?.sequence ?? "Not sampled");
    row(systemBody, "Action interval", telemetry.action?.send_interval_ms == null ? "Not sampled" : `${Math.round(telemetry.action.send_interval_ms)} ms`);
    for (const [key, value] of Object.entries(snapshot.configured_source_pins || {}))
      row(systemBody, `Configured expected ${key}`, value || "Not sampled");
    if (snapshot.verified_source_heads) {
      for (const [key, value] of Object.entries(snapshot.verified_source_heads))
        row(systemBody, `Preflight reported ${key}`, value);
    } else row(systemBody, "Preflight source report", "Not yet available");
    for (const [key, entry] of Object.entries(telemetry.body || {}))
      row(systemBody, `Body ${key}`, AM1ConsoleViews.field(entry));
    for (const [key, entry] of Object.entries(telemetry.system || {}))
      row(systemBody, `Pi ${key}`, AM1ConsoleViews.field(entry));
    for (const [role, camera] of Object.entries(telemetry.cameras || {}))
      row(systemBody, `Camera ${role}`, `${camera.state || "Unavailable"} · age ${camera.age_ms == null ? "unknown" : `${Math.round(camera.age_ms)} ms`} · seq ${camera.sequence ?? "unknown"}`);
    document.querySelector("#terminal-events").textContent = AM1ConsoleViews.eventLines(
      [...(snapshot.events || []), ...(telemetry.events || [])].sort((a, b) => (a.wall_time_ns || 0) - (b.wall_time_ns || 0)));
  }

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
    if (stateRequestInFlight) return;
    stateRequestInFlight = true;
    try {
      const response = await fetch("/api/state", {cache:"no-store", credentials:"same-origin"});
      if (!response.ok) throw new Error("session state unavailable");
      const priorSessionId = state?.session_id;
      state = await response.json();
      if (priorSessionId !== state.session_id) {
        loadedLog = "";
        loadedLogSessionId = null;
        logRequestId++;
        terminalRequestId++;
        document.querySelector("#log-lines").textContent = "Session changed. Load its exact log.";
        document.querySelector("#log-session").textContent = "No exact session log selected.";
        document.querySelector("#terminal-output").textContent = "Session changed. Select an original output.";
      }
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
      document.querySelector('[data-operation="Resume"]').textContent =
        ["sync_start", "live_start"].includes(gate?.[0]) ? "Continue startup" : "Approve Resume";
      try { renderSnapshot(state); } catch {
        document.querySelector("#system-notice").textContent = "Diagnostic display unavailable; Control and Stop remain available.";
      }
    } catch {
      stateText.textContent = "Session state unavailable; no motor readiness implied.";
      input.release();
    } finally { stateRequestInFlight = false; }
  }

  async function fetchOriginal(kind, requestedSessionId) {
    if (kind !== "summary") {
      const response = await fetch(AM1ConsoleViews.outputUrl(kind, requestedSessionId),
        {cache:"no-store", credentials:"same-origin"});
      if (!response.ok) throw new Error(`original output unavailable (${response.status})`);
      const output = await response.json();
      if (output.session_id !== requestedSessionId) throw new Error("output session changed");
      if (output.state !== "Unavailable") return {text:output.text, label:AM1ConsoleViews.outputLabel(output)};
      if (!["complete", "failed", "cleanup_unknown"].includes(state?.phase))
        return {text:output.reason, label:AM1ConsoleViews.outputLabel(output)};
    }
    const response = await fetch(AM1ConsoleViews.logUrl(kind, requestedSessionId),
      {cache:"no-store", credentials:"same-origin"});
    if (!response.ok) throw new Error(`saved log unavailable (${response.status})`);
    return {text:await response.text(), label:`Exact saved session ${requestedSessionId} · ${kind}`};
  }
  async function loadLog() {
    if (logRequestInFlight) return;
    const kind = document.querySelector("#log-kind").value;
    const output = document.querySelector("#log-lines");
    const requestedSessionId = state?.session_id;
    const requestId = ++logRequestId;
    if (!requestedSessionId) { output.textContent = "No session result is selected."; return; }
    logRequestInFlight = true;
    loadedLog = "";
    output.textContent = "Loading exact session log…";
    try {
      const original = await fetchOriginal(kind, requestedSessionId);
      if (!AM1ConsoleViews.acceptLogResponse(requestedSessionId, state?.session_id) || requestId !== logRequestId) return;
      loadedLog = original.text;
      loadedLogDescription = original.label;
      loadedLogSessionId = requestedSessionId;
      renderLoadedLog();
    } catch (error) {
      if (requestId === logRequestId) output.textContent = error.message;
    } finally { logRequestInFlight = false; }
  }
  function renderLoadedLog() {
    if (!AM1ConsoleViews.acceptLogResponse(loadedLogSessionId, state?.session_id)) return;
    document.querySelector("#log-lines").textContent = AM1ConsoleViews.filteredLog(
      loadedLog, document.querySelector("#log-filter").value, 400,
      document.querySelector("#log-severity").value) || "No matching lines.";
    document.querySelector("#log-session").textContent = loadedLogDescription;
  }
  document.querySelector("#log-load").addEventListener("click", loadLog);
  document.querySelector("#log-kind").addEventListener("change", () => {
    logRequestId++;
    loadedLog = "";
    loadedLogSessionId = null;
    document.querySelector("#log-lines").textContent = "Select Load for the chosen exact session log.";
    document.querySelector("#log-session").textContent = "No exact session log selected.";
  });
  document.querySelector("#log-filter").addEventListener("input", renderLoadedLog);
  document.querySelector("#log-severity").addEventListener("change", renderLoadedLog);
  document.querySelector("#log-export").addEventListener("click", () => {
    const sessionId = state?.session_id;
    if (!sessionId) return;
    const kind = document.querySelector("#log-kind").value;
    const link = document.createElement("a");
    link.href = AM1ConsoleViews.logUrl(kind, sessionId, true);
    link.click();
  });
  setInterval(() => {
    if (input.route === "logs" && document.querySelector("#log-follow").checked &&
        !document.querySelector("#log-pause").checked) loadLog();
  }, 2000);
  async function loadTerminal() {
    if (terminalRequestInFlight) return;
    const kind = document.querySelector("#terminal-kind").value;
    const output = document.querySelector("#terminal-output");
    const requestedSessionId = state?.session_id;
    const requestId = ++terminalRequestId;
    if (!requestedSessionId) { output.textContent = "No session result is selected."; return; }
    terminalRequestInFlight = true;
    try {
      const original = await fetchOriginal(kind, requestedSessionId);
      if (!AM1ConsoleViews.acceptLogResponse(requestedSessionId, state?.session_id) || requestId !== terminalRequestId) return;
      output.textContent = `${original.label}\n\n${original.text}`;
    } catch (error) {
      if (requestId === terminalRequestId) output.textContent = error.message;
    } finally { terminalRequestInFlight = false; }
  }
  setInterval(() => { if (input.route === "terminal") loadTerminal(); }, 2000);
  document.querySelector("#terminal-load").addEventListener("click", loadTerminal);
  document.querySelector("#terminal-kind").addEventListener("change", () => {
    terminalRequestId++;
    document.querySelector("#terminal-output").textContent = "Select View for this original output.";
  });
  async function operation(kind) {
    if (busy) return;
    busy = true;
    try {
      let approval = null;
      if (kind === "Resume" || kind === "Approve") {
        approval = await input.prepareApprovalRequest(kind, state?.pending_gate);
        if (!approval.accepted) throw new Error("fresh empty control lease was not accepted");
      }
      const payload = approval ? approval.payload :
        {kind, session_id:state?.session_id, control_token:input.token};
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
