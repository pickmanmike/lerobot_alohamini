"use strict";
const roles = ["forward", "backward", "chest", "wrist_left", "wrist_right"];
const labels = {forward: "Forward", backward: "Backward", chest: "Chest", wrist_left: "Left wrist", wrist_right: "Right wrist"};
const primary = document.querySelector("#primary"), thumbs = document.querySelector("#thumbnails");
const connection = document.querySelector("#connection");
let selected = "forward", latest = null, reportAt = 0, generation = 0, stream = null;
const tiles = new Map(), blobs = new Map(), displayed = new Map(), busy = new Set();
for (const role of roles) {
  const button = document.createElement("button");
  button.className = "view";
  button.innerHTML = '<img alt=""><div class="unavailable">Unavailable</div><footer><strong></strong><span></span></footer>';
  button.querySelector("img").alt = labels[role];
  button.querySelector("strong").textContent = labels[role];
  button.addEventListener("click", () => { stopPrimary(); selected = role; generation++; render(); });
  thumbs.append(button); tiles.set(role, button);
}
function sourceFor(role) { return AM1SourceState(latest?.cameras[role], reportAt, performance.now()); }
function paint(tile, role, thumbnail = false) {
  const state = AM1FrameState(latest?.cameras[role], reportAt, performance.now(), displayed.get(thumbnail ? role : "primary"), thumbnail);
  tile.classList.toggle("fresh", state.state === "fresh");
  tile.querySelector("strong").textContent = labels[role];
  tile.querySelector("span").textContent = state.state === "fresh" ? `${state.fps.toFixed(1)} fps source · image ${Math.round(state.age_ms)} ms` : state.state;
  tile.querySelector(".unavailable").textContent = state.state === "stale" ? "Stale · waiting for a decoded frame" : "Unavailable · not mapped or not connected";
}
async function showFrame(tile, key, blob, frame, valid) {
  const url = URL.createObjectURL(blob), candidate = new Image();
  candidate.src = url;
  try {
    await candidate.decode();
    if (!valid()) { URL.revokeObjectURL(url); return false; }
    tile.querySelector("img").src = url;
    const old = blobs.get(key); blobs.set(key,url);
    displayed.set(key, {at:frame.at, age_ms:frame.age_ms, sequence:frame.sequence});
    if (old) URL.revokeObjectURL(old);
    return true;
  } catch { URL.revokeObjectURL(url); return false; }
}
function stopPrimary() {
  if (stream) stream.controller.abort();
  stream = null; displayed.delete("primary");
  primary.querySelector("img").removeAttribute("src");
  const old = blobs.get("primary"); if (old) URL.revokeObjectURL(old); blobs.delete("primary");
}
function startPrimary() {
  const current = {role:selected, controller:new AbortController(), progressed:performance.now(), pending:null, decoding:false};
  stream = current;
  async function decodeLatest() {
    if (current.decoding) return;
    current.decoding = true;
    try {
      while (current.pending && stream === current) {
        const frame = current.pending; current.pending = null;
        if (await showFrame(primary, "primary", new Blob([frame.jpeg], {type:"image/jpeg"}), frame,
                            () => stream === current && sourceFor(current.role).state === "fresh")) current.progressed = performance.now();
      }
    } finally { current.decoding = false; }
  }
  (async () => {
    try {
      const response = await fetch(`/api/stream.mjpeg?src=${current.role}`, {cache:"no-store", signal:current.controller.signal});
      if (!response.ok || !response.body) throw new Error("Stream unavailable");
      for await (const frame of AM1MjpegFrames(response.body)) {
        if (stream !== current) break;
        current.pending = frame; void decodeLatest(); // Latest only while one image is decoding.
      }
    } catch { /* display clock and source clock expose failure; never renew either here */ }
    finally { if (stream === current) stopPrimary(); }
  })();
}
function render() {
  const fresh = sourceFor(selected).state === "fresh";
  if (!fresh || (stream && performance.now() - stream.progressed > 500)) stopPrimary();
  if (fresh && !stream) startPrimary();
  paint(primary, selected);
  for (const [role, tile] of tiles) { tile.hidden = role === selected; paint(tile, role, true); }
}
async function statusLoop() {
  try {
    const start = performance.now();
    const response = await fetch("/status.json", {cache:"no-store", signal:AbortSignal.timeout(1500)});
    if (!response.ok) throw new Error("Status unavailable");
    latest = await response.json(); reportAt = start;
    connection.textContent = "LAN viewer · fresh source AND decoded image required";
  } catch { connection.textContent = "Status unavailable — do not rely on these views"; latest = null; }
  render(); setTimeout(statusLoop, 250);
}
async function snapshots() {
  for (const role of roles) {
    if (role === selected || busy.has(role) || sourceFor(role).state !== "fresh") continue;
    busy.add(role); const current = generation;
    (async () => {
      try {
        const at = performance.now();
        const response = await fetch(`/api/frame.jpeg?src=${role}&cache=500ms`, {cache:"no-store", signal:AbortSignal.timeout(1000)});
        if (!response.ok) return;
        const blob = await response.blob();
        const age_ms = Number(response.headers.get("X-Frame-Age-Ms") ?? NaN);
        const sequence = Number(response.headers.get("X-Frame-Sequence") ?? NaN);
        if (!Number.isFinite(age_ms) || age_ms < 0 || !Number.isSafeInteger(sequence) || blob.size > 1000000) return;
        await showFrame(tiles.get(role), role, blob, {at,age_ms,sequence},
                        () => current === generation && role !== selected && sourceFor(role).state === "fresh");
      } catch { /* An unsuccessful fetch or decode cannot renew the previous image clock. */ }
      finally { busy.delete(role); }
    })();
  }
}
setInterval(render, 100); setInterval(snapshots, 500); statusLoop();
