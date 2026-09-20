"use strict";
const roles = ["forward", "backward", "chest", "wrist_left", "wrist_right"];
const labels = {forward: "Forward", backward: "Backward", chest: "Chest", wrist_left: "Left wrist", wrist_right: "Right wrist"};
const primary = document.querySelector("#primary"), thumbs = document.querySelector("#thumbnails");
const connection = document.querySelector("#connection");
const diagnostics = document.querySelector("#diagnostics");
let selected = "forward", latest = null, reportAt = 0, statusReceivedAt = 0, generation = 0, stream = null;
const tiles = new Map(), blobs = new Map(), displayed = new Map(), busy = new Set();
const sequences = new Map(); // Per display path, retained through stream reconnects.
const timing = {status_ms:0, status_max_ms:0, status_failures:0, decode_failures:0,
                cancellations:0, last_cancel:"none"};
const progress = new Map(roles.map(role => [role, {received:0, displayed:0, sequence:0,
  received_at:null, displayed_at:null, receive_max_gap_ms:0, display_max_gap_ms:0}]));
for (const role of roles) {
  const button = document.createElement("button");
  button.className = "view";
  button.innerHTML = '<img alt=""><div class="unavailable">Unavailable</div><footer><strong></strong><span></span></footer>';
  button.querySelector("img").alt = labels[role];
  button.querySelector("strong").textContent = labels[role];
  button.addEventListener("click", () => { stopPrimary("role-switch"); selected = role; generation++; render(); });
  thumbs.append(button); tiles.set(role, button);
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
function paint(tile, role, thumbnail = false) {
  const state = AM1FrameState(latest?.cameras[role], reportAt, performance.now(), displayed.get(thumbnail ? role : "primary"), thumbnail, statusReceivedAt);
  tile.classList.toggle("fresh", state.state === "fresh");
  tile.querySelector("strong").textContent = labels[role];
  tile.querySelector("span").textContent = state.state === "fresh" ? `${state.fps.toFixed(1)} fps source · image ${Math.round(state.age_ms)} ms${state.status_uncertain ? " · status uncertain" : ""}` : state.state;
  tile.querySelector(".unavailable").textContent = !statusAvailable() ? "Status unavailable" :
    latest.cameras[role]?.configured === false || !latest.cameras[role] ? "Unassigned · use numbered identification previews" :
    state.state === "stale" ? "Mapped · stale / waiting for a decoded frame" : "Mapped · not connected / waiting for source";
}
async function showFrame(tile, key, blob, frame, valid, sequenceKey = key) {
  if (frame.sequence <= (sequences.get(sequenceKey) ?? 0)) return false;
  const url = URL.createObjectURL(blob), candidate = new Image();
  candidate.src = url;
  try {
    await candidate.decode();
    if (!valid() || frame.sequence <= (sequences.get(sequenceKey) ?? 0)) { URL.revokeObjectURL(url); return false; }
    tile.querySelector("img").src = url;
    const old = blobs.get(key); blobs.set(key,url);
    displayed.set(key, {at:frame.at, age_ms:frame.age_ms, sequence:frame.sequence});
    sequences.set(sequenceKey, frame.sequence);
    if (old) URL.revokeObjectURL(old);
    return true;
  } catch { timing.decode_failures++; URL.revokeObjectURL(url); return false; }
}
function stopPrimary(reason = "stopped") {
  if (stream) { timing.cancellations++; timing.last_cancel = reason; stream.controller.abort(); }
  stream = null; displayed.delete("primary");
  primary.querySelector("img").removeAttribute("src");
  const old = blobs.get("primary"); if (old) URL.revokeObjectURL(old); blobs.delete("primary");
}
function startPrimary() {
  const current = {role:selected, controller:new AbortController(), progressed:performance.now(), pending:null, decoding:false};
  stream = current;
  // Gaps measure continuous viewing, not time spent selecting another role.
  progress.get(selected).received_at = null; progress.get(selected).displayed_at = null;
  async function decodeLatest() {
    if (current.decoding) return;
    current.decoding = true;
    try {
      while (current.pending && stream === current) {
        const frame = current.pending; current.pending = null;
        if (await showFrame(primary, "primary", new Blob([frame.jpeg], {type:"image/jpeg"}), frame,
                            () => stream === current && usable(current.role), `primary:${current.role}`)) {
          current.progressed = performance.now(); noteProgress(current.role, "displayed", frame.sequence);
        }
      }
    } finally { current.decoding = false; }
  }
  (async () => {
    try {
      const response = await fetch(`/api/stream.mjpeg?src=${current.role}`, {cache:"no-store", signal:current.controller.signal});
      if (!response.ok || !response.body) throw new Error("Stream unavailable");
      for await (const frame of AM1MjpegFrames(response.body)) {
        if (stream !== current) break;
        noteProgress(current.role, "received", frame.sequence);
        current.pending = frame; void decodeLatest(); // Latest only while one image is decoding.
      }
    } catch { /* display clock and source clock expose failure; never renew either here */ }
    finally { if (stream === current) stopPrimary("stream-ended-or-failed"); }
  })();
}
function render() {
  const allowed = usable(selected);
  if (!allowed) stopPrimary(statusAvailable() ? "source-unavailable" : "status-unavailable");
  else if (stream && performance.now() - stream.progressed > 500) stopPrimary("display-stall");
  if (allowed && !stream) startPrimary();
  paint(primary, selected);
  for (const [role, tile] of tiles) { tile.hidden = role === selected; paint(tile, role, true); }
  connection.textContent = !statusAvailable() ? "Status unavailable — do not rely on these views" :
    sourceFor(selected).state !== "fresh" && usable(selected) ? "LAN viewer · status delayed/uncertain · decoded-frame clock remains active" :
    "LAN viewer · source status and decoded-frame progress tracked separately";
  diagnostics.textContent = `Status request ${Math.round(timing.status_ms)} ms (max ${Math.round(timing.status_max_ms)} ms); failures ${timing.status_failures}\n` +
    `Decode failures ${timing.decode_failures}; stream cancellations ${timing.cancellations}; last ${timing.last_cancel}\n` +
    roles.map(role => { const p = progress.get(role); return `${labels[role]} primary: received ${p.received}, displayed ${p.displayed}, seq ${p.sequence}; max receive/display gap ${Math.round(p.receive_max_gap_ms)}/${Math.round(p.display_max_gap_ms)} ms`; }).join("\n");
}
async function statusLoop() {
  const start = performance.now();
  try {
    const response = await fetch("/status.json", {cache:"no-store", signal:AbortSignal.timeout(1500)});
    if (!response.ok) throw new Error("Status unavailable");
    latest = await response.json(); reportAt = start; statusReceivedAt = performance.now();
  } catch { timing.status_failures++; latest = null; }
  timing.status_ms = performance.now() - start;
  timing.status_max_ms = Math.max(timing.status_max_ms, timing.status_ms);
  render(); setTimeout(statusLoop, 250);
}
async function snapshots() {
  for (const role of roles) {
    if (role === selected || busy.has(role) || !usable(role)) continue;
    busy.add(role); const current = generation;
    (async () => {
      try {
        const at = performance.now();
        const response = await fetch(`/api/frame.jpeg?src=${role}&cache=500ms`, {cache:"no-store", signal:AbortSignal.timeout(1000)});
        if (!response.ok) return;
        const blob = await response.blob();
        const age_ms = Number(response.headers.get("X-Frame-Age-Ms") ?? NaN);
        const sequence = Number(response.headers.get("X-Frame-Sequence") ?? NaN);
        if (!Number.isFinite(age_ms) || age_ms < 0 || !Number.isSafeInteger(sequence) || sequence < 1 || blob.size > 1000000) return;
        await showFrame(tiles.get(role), role, blob, {at,age_ms,sequence},
                        () => current === generation && role !== selected && usable(role));
      } catch { /* An unsuccessful fetch or decode cannot renew the previous image clock. */ }
      finally { busy.delete(role); }
    })();
  }
}
setInterval(render, 100); setInterval(snapshots, 500); statusLoop();
