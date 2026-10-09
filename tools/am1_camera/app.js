"use strict";
const identifying = document.body.dataset.identification === "true";
const roles = identifying ? ["preview_1", "preview_2", "preview_3", "preview_4", "preview_5"] :
                           ["forward", "backward", "chest", "wrist_left", "wrist_right"];
const labels = identifying ? Object.fromEntries(roles.map((role, i) => [role, `Camera ${i + 1}`])) :
                            {forward: "Front", backward: "Rear", chest: "Chest", wrist_left: "Left wrist", wrist_right: "Right wrist"};
if (identifying) document.querySelector("#view-heading").textContent = "Numbered identification · adjust focus, then confirm roles";
const cameraRoot = document.querySelector("#am1-camera-root");
const cameraBase = cameraRoot?.dataset.cameraBase === "/camera/" ? "/camera/" : "/";
function cameraURL(path) { return cameraBase + path; }
const primary = document.querySelector("#primary"), thumbs = document.querySelector("#thumbnails");
const connection = document.querySelector("#connection");
const diagnostics = document.querySelector("#diagnostics");
let selected = roles[0], latest = null, reportAt = 0, statusReceivedAt = 0, stream = null;
let statusRequest = 0, acceptedStatusRequest = 0, statusUncertain = false;
const tiles = new Map(), slots = new Map(), details = new Map(), busy = new Set(), decodingRoles = new Map();
const snapshotRequests = new Map(), reconnects = new Map(), minimumSequences = new Map();
const retained = new AM1RetainedFrames();
const roleGenerations = new Map(roles.map(role => [role, 1]));
const sourceSequences = new Map(), sourceStates = new Map(), roleRotations = new Map();
for (const role of roles) retained.setGeneration(role, 1);
const timing = {status_ms:0, status_max_ms:0, status_failures:0, decode_failures:0,
                cancellations:0, last_cancel:"none"};
const progress = new Map(roles.map(role => [role, {received:0, displayed:0, sequence:0,
  received_at:null, displayed_at:null, receive_max_gap_ms:0, display_max_gap_ms:0}]));
for (const role of roles) {
  const slot = document.createElement("article");
  slot.className = `camera-slot slot-${role}`;
  slot.dataset.role = role;
  const button = document.createElement("button");
  button.className = "view";
  button.type = "button";
  button.dataset.role = role;
  button.innerHTML = '<div class="camera-heading"><strong></strong><span class="camera-status"></span></div><div class="image-wrap"><img alt=""><div class="unavailable">Unavailable</div></div>';
  button.querySelector("img").alt = labels[role];
  button.querySelector("strong").textContent = labels[role];
  button.addEventListener("click", () => { if (role !== selected) { stopPrimary("role-switch"); selected = role; render(); } });
  const detail = document.createElement("details");
  detail.className = "camera-details";
  detail.innerHTML = '<summary>Details</summary><dl><dt>Image age</dt><dd data-field="image-age">Not sampled</dd><dt>Source</dt><dd data-field="source">Unavailable</dd><dt>Sequence</dt><dd data-field="sequence">Not sampled</dd><dt>Rotation</dt><dd data-field="rotation">Not sampled</dd></dl>';
  if (document.body.dataset.console === "compact") {
    const term=document.createElement("dt"), value=document.createElement("dd");
    term.textContent="Frame generation"; value.dataset.field="generation";
    detail.querySelector("dl").append(term,value);
  }
  slot.append(button); slot.append(detail); thumbs.append(slot);
  tiles.set(role, button); slots.set(role, slot); details.set(role, detail);
}
function sourceFor(role) { return AM1SourceState(latest?.cameras[role], reportAt, performance.now()); }
// Poll liveness uses reply time; source age retains conservative request time.
// 2s bounds status loss (250ms polling delay + 1500ms request timeout + margin).
function statusAvailable() { return latest && performance.now() - statusReceivedAt <= 2000; }
function usable(role) {
  const frame = latest?.cameras[role];
  return statusAvailable() && frame?.state === "fresh" && frame.configured !== false &&
         Number.isFinite(frame.age_ms) && frame.age_ms >= 0;
}
function noteProgress(role, kind, sequence) {
  const item = progress.get(role), now = performance.now(), previous = item[`${kind}_at`];
  const gap = kind === "received" ? "receive_max_gap_ms" : "display_max_gap_ms";
  if (previous !== null) item[gap] = Math.max(item[gap], now - previous);
  item[`${kind}_at`] = now; item[kind]++; item.sequence = sequence;
}
function bumpGeneration(role, transportOnly = false) {
  const next = roleGenerations.get(role) + 1;
  if (transportOnly) minimumSequences.set(role, retained.get(role, performance.now())?.sequence ?? 0);
  else minimumSequences.delete(role); // A reported source restart may reset sequence to one.
  roleGenerations.set(role, next); retained.setGeneration(role, next);
  // Old in-flight work cannot claim this connection generation or block its retry.
  const decoding = decodingRoles.get(role);
  if (decoding) { URL.revokeObjectURL(decoding.url); decodingRoles.delete(role); }
  if (stream?.role === role) stopPrimary("source-generation-changed");
}
function retainedImage(tile, role) {
  const image = tile.querySelector("img"), held = retained.get(role, performance.now());
  if (held && image.src !== held.url) image.src = held.url;
  if (!held && image.src) image.removeAttribute("src");
  return held;
}
function paintDetails(detail, held, source, rotation) {
  detail.querySelector('[data-field="image-age"]').textContent = held ? `${Math.round(held.age_ms)} ms (gateway receipt)` : "Not sampled";
  detail.querySelector('[data-field="source"]').textContent = source ?
    `${source.state ?? "Unknown"} · ${Number.isFinite(source.fps) ? source.fps.toFixed(1) : "?"} fps` : "Unavailable";
  detail.querySelector('[data-field="sequence"]').textContent = held ? `Decoded ${held.sequence}` :
    Number.isSafeInteger(source?.sequence) ? `Source ${source.sequence}` : "Not sampled";
  detail.querySelector('[data-field="rotation"]').textContent = `${rotation}° display-only`;
  const generation = detail.querySelector('[data-field="generation"]');
  if (generation) generation.textContent = held ? String(held.generation) : "Not sampled";
}
function frameState(role, thumbnail = false, now = performance.now()) {
  const held = retained.get(role, now);
  const current = held && held.generation === roleGenerations.get(role);
  return AM1FrameState(latest?.cameras[role], reportAt, now,
    current ? {at:held.at, age_ms:held.age_at_receipt_ms} : null,
    thumbnail, statusReceivedAt, statusUncertain);
}
globalThis.AM1CameraHealth = function() {
  const now = performance.now(), available = !!statusAvailable();
  const finite = value => Number.isFinite(value) ? value : null;
  return {version:1, sampled_at_ms:now, selected_role:selected,
    status_received_age_ms:latest ? Math.max(0, now - statusReceivedAt) : null,
    status_available:available, status_uncertain:statusUncertain || !available,
    status_failures:timing.status_failures,
    roles:roles.map(role => {
      const held = retained.get(role, now), source = latest?.cameras[role];
      const state = frameState(role, role !== selected, now);
      return {role, identity:role, selected:role === selected, generation:roleGenerations.get(role),
        decoded_generation:held?.generation ?? null, sequence:held?.sequence ?? null,
        decoded_age_ms:finite(held?.age_ms), source_sequence:source?.sequence ?? null,
        source_state:source?.state ?? "unavailable", source_age_ms:finite(state.producer_age_ms),
        configured:source?.configured === true, fresh:state.state === "fresh",
        status_uncertain:state.status_uncertain || !available};
    })};
};
function paint(tile, role, thumbnail = false) {
  const held = retainedImage(tile, role);
  const source = latest?.cameras[role];
  const state = frameState(role, thumbnail);
  const rotation = [0, 90, 180, 270].includes(source?.rotation_degrees) ?
    source.rotation_degrees : (roleRotations.get(role) ?? 0);
  tile.querySelector("img").dataset.rotation = String(rotation);
  tile.classList.toggle("fresh", state.state === "fresh");
  tile.classList.toggle("held", !!held && state.state !== "fresh");
  tile.querySelector("strong").textContent = labels[role];
  tile.querySelector("span").textContent = state.state === "fresh" ?
    `Live${state.status_uncertain ? " · status uncertain" : ""}` :
    held ? "Last frame / waiting" : state.state;
  if (!held && state.state !== "fresh") tile.querySelector("span").textContent =
    source?.configured === false ? "Unassigned" : source?.state === "stale" ? "Disconnected" : "Waiting";
  tile.querySelector(".unavailable").textContent = held ? "" : !statusAvailable() ? "Status unavailable" :
    latest.cameras[role]?.configured === false || !latest.cameras[role] ? "Unassigned · use numbered identification previews" :
    state.state === "stale" ? "Mapped · stale / waiting for a decoded frame" : "Mapped · not connected / waiting for source";
  paintDetails(thumbnail ? details.get(role) : primary.querySelector("details"), held, source, rotation);
}
async function showFrame(role, blob, frame, valid) {
  if (decodingRoles.has(role)) return false;
  const url = URL.createObjectURL(blob), candidate = new Image(), decoding = {url};
  decodingRoles.set(role, decoding);
  candidate.src = url;
  try {
    await candidate.decode();
    if (!valid() || frame.sequence <= (minimumSequences.get(role) ?? 0)) { URL.revokeObjectURL(url); return false; }
    if (!retained.accept(role, frame.generation, frame.sequence, url, frame.age_ms, frame.at)) return false;
    if (role === selected) retainedImage(primary, role);
    else retainedImage(tiles.get(role), role);
    return true;
  } catch { timing.decode_failures++; URL.revokeObjectURL(url); return false; }
  finally { if (decodingRoles.get(role) === decoding) decodingRoles.delete(role); }
}
function stopPrimary(reason = "stopped") {
  if (stream) { timing.cancellations++; timing.last_cancel = reason; stream.controller.abort(); }
  stream = null;
}
function startPrimary() {
  const current = {role:selected, generation:roleGenerations.get(selected),
    controller:new AbortController(), progressed:performance.now(), pending:null, decoding:false};
  stream = current;
  // Gaps measure continuous viewing, not time spent selecting another role.
  progress.get(selected).received_at = null; progress.get(selected).displayed_at = null;
  async function decodeLatest() {
    if (current.decoding) return;
    current.decoding = true;
    try {
      while (current.pending && stream === current) {
        const frame = current.pending; current.pending = null;
        if (await showFrame(current.role, new Blob([frame.jpeg], {type:"image/jpeg"}),
                            {...frame, generation:current.generation},
                            () => stream === current && current.generation === roleGenerations.get(current.role) && usable(current.role))) {
          current.progressed = performance.now(); noteProgress(current.role, "displayed", frame.sequence);
        }
      }
    } finally { current.decoding = false; }
  }
  (async () => {
    try {
      const requestAt = performance.now();
      const response = await fetch(cameraURL(`api/stream.mjpeg?src=${current.role}`), {cache:"no-store", signal:current.controller.signal});
      if (!response.ok || !response.body) throw new Error("Stream unavailable");
      for await (const frame of AM1MjpegFrames(response.body, undefined, requestAt)) {
        if (stream !== current) break;
        noteProgress(current.role, "received", frame.sequence);
        current.pending = frame; void decodeLatest(); // Latest only while one image is decoding.
      }
    } catch { /* display clock and source clock expose failure; never renew either here */ }
    finally { if (stream === current) stopPrimary("stream-ended-or-failed"); }
  })();
}
function render() {
  primary.dataset.role = selected;
  const allowed = usable(selected);
  if (!allowed) stopPrimary(statusAvailable() ? "source-unavailable" : "status-unavailable");
  else if (stream && performance.now() - stream.progressed > 500) stopPrimary("display-stall");
  if (allowed && !stream) startPrimary();
  paint(primary, selected);
  for (const [role, tile] of tiles) {
    tile.ariaPressed = role === selected ? "true" : "false";
    tile.setAttribute("aria-pressed", tile.ariaPressed);
    slots.get(role).dataset.focused = role === selected ? "true" : "false";
    paint(tile, role, true);
  }
  connection.textContent = !statusAvailable() ? "Status unavailable — do not rely on these views" :
    (statusUncertain || sourceFor(selected).state !== "fresh") && usable(selected) ? "LAN viewer · status delayed/uncertain · decoded-frame clock remains active" :
    "LAN viewer · source status and decoded-frame progress tracked separately";
  if (document.body.dataset.console === "compact") {
    const fresh = Number(primary.classList.contains("fresh")) +
      [...tiles.entries()].filter(([role,tile]) => role !== selected && tile.classList.contains("fresh")).length;
    connection.textContent = !statusAvailable() ? "Camera status unavailable — retained images are not live." :
      `Cameras ${fresh}/5 fresh decoded views${statusUncertain ? " · status uncertain" : ""}${fresh < 5 ? " — check required views; retained images are not live." : " · image ages in Details."}`;
  }
  diagnostics.textContent = `Status request ${Math.round(timing.status_ms)} ms (max ${Math.round(timing.status_max_ms)} ms); failures ${timing.status_failures}\n` +
    `Decode failures ${timing.decode_failures}; stream cancellations ${timing.cancellations}; last ${timing.last_cancel}\n` +
    roles.map(role => { const p = progress.get(role); return `${labels[role]} primary: received ${p.received}, displayed ${p.displayed}, seq ${p.sequence}; max receive/display gap ${Math.round(p.receive_max_gap_ms)}/${Math.round(p.display_max_gap_ms)} ms`; }).join("\n");
}
async function statusLoop() {
  const start = performance.now();
  const request = ++statusRequest;
  try {
    const response = await fetch(cameraURL("status.json"), {cache:"no-store", signal:AbortSignal.timeout(1500)});
    if (!response.ok) throw new Error("Status unavailable");
    const next = await response.json();
    if (request > acceptedStatusRequest) {
      acceptedStatusRequest = request;
      for (const role of roles) {
        const source = next.cameras?.[role], previous = sourceSequences.get(role);
        if ([0, 90, 180, 270].includes(source?.rotation_degrees)) roleRotations.set(role, source.rotation_degrees);
        const state = source?.state === "fresh" && source.configured !== false ? "fresh" : "not-fresh";
        if (state === "fresh" && ((Number.isSafeInteger(previous) && source.sequence < previous) ||
            sourceStates.get(role) === "not-fresh")) bumpGeneration(role);
        if (Number.isSafeInteger(source?.sequence)) sourceSequences.set(role, source.sequence);
        sourceStates.set(role, state);
      }
      latest = next; reportAt = start; statusReceivedAt = performance.now(); statusUncertain = false;
    }
  } catch {
    if (request > acceptedStatusRequest) {
      timing.status_failures++; statusUncertain = true;
      // A failed poll cannot erase independent delivery evidence. Keep the last
      // accepted identity and original clocks; statusAvailable still expires it.
    }
  }
  timing.status_ms = performance.now() - start;
  timing.status_max_ms = Math.max(timing.status_max_ms, timing.status_ms);
  render(); setTimeout(statusLoop, 250);
}
function snapshot(role) {
  if (role === selected || busy.has(role) || !usable(role)) return;
  busy.add(role);
  const current = roleGenerations.get(role), controller = new AbortController();
  snapshotRequests.set(role, controller);
  const deadline = setTimeout(() => controller.abort(), 1000);
  (async () => {
    try {
      const at = performance.now();
      const response = await fetch(cameraURL(`api/frame.jpeg?src=${role}&cache=500ms`), {cache:"no-store", signal:controller.signal});
      if (!response.ok) return;
      const blob = await response.blob();
      const age_ms = Number(response.headers.get("X-Frame-Age-Ms") ?? NaN);
      const sequence = Number(response.headers.get("X-Frame-Sequence") ?? NaN);
      if (!Number.isFinite(age_ms) || age_ms < 0 || !Number.isSafeInteger(sequence) || sequence < 1 || blob.size > 1000000) return;
      await showFrame(role, blob, {at,age_ms,sequence,generation:current},
                      () => !controller.signal.aborted && current === roleGenerations.get(role) && role !== selected && usable(role));
    } catch { /* An unsuccessful fetch or decode cannot renew the previous image clock. */ }
    finally {
      clearTimeout(deadline);
      if (snapshotRequests.get(role) === controller) { snapshotRequests.delete(role); busy.delete(role); }
    }
  })();
}
async function snapshots() { for (const role of roles) snapshot(role); }
globalThis.AM1CameraReconnect = function(role) {
  const now = performance.now(), previous = reconnects.get(role);
  if (!roles.includes(role) || !usable(role)) return {accepted:false, reason:"source-unqualified"};
  if ((previous?.attempts ?? 0) >= 3) return {accepted:false, reason:"attempt-limit"};
  if (previous && now - previous.at < 1000) return {accepted:false, reason:"retry-rate-limit"};
  const attempt = (previous?.attempts ?? 0) + 1;
  reconnects.set(role, {at:now, attempts:attempt});
  snapshotRequests.get(role)?.abort(); snapshotRequests.delete(role); busy.delete(role);
  bumpGeneration(role, true);
  if (role === selected) render(); else snapshot(role);
  return {accepted:true, role, identity:role, generation:roleGenerations.get(role), attempt};
};

setInterval(render, 100); setInterval(snapshots, 500); statusLoop();
