"use strict";
// Loaded ONLY by the offline harness, never by the production console.
(() => {
  const entries = [], pending = new Map();
  let frozenAt = null, InputClass = null, serial = 0, maxSnapshotWorkMs = 0;
  const record = (event, fields = {}) => {
    const now = performance.now();
    if (frozenAt !== null && now > frozenAt + 1000) return;
    entries.push({event, browser_monotonic_ms:now, wall_time_ms:Date.now(), ...fields});
    if (entries.length > 3000) entries.shift();
  };
  const freeze = () => { frozenAt ??= performance.now(); };
  const report = () => {
    const calls = [], gaps = [];
    let prior = null;
    for (const entry of entries) {
      if (entry.event !== "fetch_submit") continue;
      if (entry.path === "/api/operation" || (entry.path === "/api/body" && !entry.active)) prior = null;
      if (entry.path !== "/api/body" || !entry.active) continue;
      calls.push(entry);
      if (prior?.session_id === entry.session_id && prior.epoch === entry.epoch)
        gaps.push(entry.browser_monotonic_ms-prior.browser_monotonic_ms);
      prior = entry;
    }
    return {clock:"browser performance.now milliseconds; distinct origin from Python monotonic",
      time_origin_ms:performance.timeOrigin, visible:!document.hidden, focused:document.hasFocus(),
      summary:{bounded_context_only:true, body_calls:calls.length, max_body_call_gap_ms:Math.max(0,...gaps),
        max_snapshot_work_ms:maxSnapshotWorkMs,
        max_timer_work_ms:Math.max(0,...entries.filter(v=>v.event==="timer_return").map(v=>v.duration_ms)),
        long_tasks:entries.filter(v=>v.event==="long_task").length,
        suppressed_ticks:entries.filter(v=>v.event==="input_tick" && v.pendingApproval).length}, entries};
  };
  globalThis.am1TestTiming = {entries, freeze, report,
    snapshotWork(render) {
      const at = performance.now();
      try { return render(); }
      finally {
        const duration = performance.now()-at;
        if (frozenAt === null || performance.now() <= frozenAt+1000)
          maxSnapshotWorkMs = Math.max(maxSnapshotWorkMs, duration);
        if (duration >= 10) record("diagnostic_render", {start_ms:at, duration_ms:duration});
      }
    }};
  const interval = globalThis.setInterval;
  globalThis.setInterval = (callback, delay, ...args) => {
    const timer = ++serial;
    const name = String(callback).includes("input.tick") ? "input" : callback.name || "anonymous";
    return interval(() => {
      const started = performance.now();
      record("timer_callback", {timer, name, delay_ms:delay, visible:!document.hidden, focused:document.hasFocus()});
      try { return callback(...args); }
      finally { record("timer_return", {timer, name, delay_ms:delay, duration_ms:performance.now()-started}); }
    }, delay);
  };
  const fetch = globalThis.fetch;
  globalThis.fetch = function(resource, options) {
    const path = typeof resource === "string" ? resource.split("?")[0] : "other";
    let safe = {};
    if (path === "/api/body" || path === "/api/operation") {
      const body = JSON.parse(options.body);
      safe = {session_id:body.session_id, seq:body.seq, epoch:body.epoch,
        active:body.active, kind:body.kind, stage:body.gate_stage, key_count:body.keys?.length};
    }
    const id = ++serial, started = performance.now();
    // Fetch settles at headers, not at stream completion. This is deliberately
    // NOT a count of occupied HTTP connections or pending image decodes.
    record("fetch_submit", {path, ...safe, outstanding_headers:pending.size});
    pending.set(id, started);
    return fetch.call(this, resource, options).then(response => {
      record("fetch_headers", {path, ...safe, elapsed_ms:performance.now()-started, status:response.status});
      if (path.startsWith("/api/")) response.clone().json().then(value => {
        record("fetch_result", {path, ...safe, accepted:value.accepted, reason:value.reason,
          phase:value.phase, pause_reason:value.input_pause?.reason,
          accepted_seq:value.input_pause?.input_sequence, input_age_ms:value.input_pause?.input_age_ms});
        if (value.input_pause?.reason === "expired browser input") freeze();
      }).catch(() => {});
      return response;
    }, error => { record("fetch_error", {path, ...safe, error:error.name}); throw error; })
      .finally(() => pending.delete(id));
  };
  new PerformanceObserver(list => {
    for (const entry of list.getEntries()) record("long_task", {start_ms:entry.startTime, duration_ms:entry.duration,
      attribution:entry.attribution.map(item => ({name:item.name, type:item.containerType}))});
  }).observe({entryTypes:["longtask"]});
  if (PerformanceObserver.supportedEntryTypes.includes("long-animation-frame")) {
    new PerformanceObserver(list => {
      for (const entry of list.getEntries()) record("long_animation_frame", {
        start_ms:entry.startTime, duration_ms:entry.duration, blocking_ms:entry.blockingDuration,
        render_ms:entry.renderStart, style_layout_ms:entry.styleAndLayoutStart,
        scripts:entry.scripts.map(script => ({
          invoker:script.invoker, type:script.invokerType, function:script.sourceFunctionName,
          source_path:script.sourceURL ? new URL(script.sourceURL, location.href).pathname : "",
          start_ms:script.executionStart, duration_ms:script.duration,
          forced_layout_ms:script.forcedStyleAndLayoutDuration,
        })),
      });
    }).observe({entryTypes:["long-animation-frame"]});
  }
  new PerformanceObserver(list => {
    for (const entry of list.getEntries()) {
      const path = new URL(entry.name).pathname;
      if (path !== "/api/body") continue;
      record("resource_timing", {path, start_ms:entry.startTime, fetch_ms:entry.fetchStart,
        request_ms:entry.requestStart, response_ms:entry.responseStart, end_ms:entry.responseEnd});
    }
  }).observe({entryTypes:["resource"]});
  Object.defineProperty(globalThis, "AM1BrowserInput", {configurable:true,
    get:() => InputClass,
    set:Input => {
      InputClass = Input;
      const tick = Input.prototype.tick;
      Input.prototype.tick = function(...args) {
        record("input_tick", {session_id:this.sessionId, seq:this.seq, epoch:this.epoch,
          live:this.live, pendingApproval:Boolean(this.pendingApproval), route:this.route});
        return tick.apply(this, args);
      };
    }});
  document.addEventListener("DOMContentLoaded", () => {
    const banner = document.createElement("aside");
    banner.textContent = "OFFLINE INPUT TIMING — synthetic cameras and fake robot only";
    const button = document.createElement("button"), output = document.createElement("pre");
    button.textContent = "Export offline timing";
    button.onclick = () => {
      const value = report();
      output.textContent = JSON.stringify(value.summary);
      const link = document.createElement("a");
      link.href = URL.createObjectURL(new Blob([JSON.stringify(value)], {type:"application/json"}));
      link.download = "am1-offline-browser-timing.json";
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    };
    output.id = "offline-timing-summary";
    banner.append(button, output);
    document.body.prepend(banner);
  });
})();
