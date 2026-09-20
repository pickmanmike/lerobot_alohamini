"use strict";
const roles = ["forward", "backward", "chest", "wrist_left", "wrist_right"];
const labels = {forward: "Forward", backward: "Backward", chest: "Chest", wrist_left: "Left wrist", wrist_right: "Right wrist"};
const primary = document.querySelector("#primary");
const thumbs = document.querySelector("#thumbnails");
const connection = document.querySelector("#connection");
let selected = "forward", latest = null, reportAt = 0, generation = 0, streamRole = null;
const tiles = new Map(), blobs = new Map(), displayed = new Map(), busy = new Set();
for (const role of roles) {
  const button = document.createElement("button");
  button.className = "view";
  button.innerHTML = '<img alt=""><div class="unavailable">Unavailable</div><footer><strong></strong><span></span></footer>';
  button.querySelector("img").alt = labels[role];
  button.querySelector("strong").textContent = labels[role];
  button.addEventListener("click", () => { selected = role; generation++; render(); });
  thumbs.append(button); tiles.set(role, button);
}
function stateFor(role, thumbnail = false) {
  return AM1FrameState(latest?.cameras[role], reportAt, performance.now(), displayed.get(role), thumbnail);
}
function paint(tile, role, thumbnail = false) {
  const state = stateFor(role, thumbnail);
  tile.classList.toggle("fresh", state.state === "fresh");
  tile.querySelector("strong").textContent = labels[role];
  tile.querySelector("span").textContent = state.state === "fresh" ? `${state.fps.toFixed(1)} fps · ${Math.round(state.age_ms)} ms` : state.state;
  tile.querySelector(".unavailable").textContent = state.state === "stale" ? "Stale · waiting for new frames" : "Unavailable · not mapped or not connected";
}
function render() {
  paint(primary, selected);
  const fresh = stateFor(selected).state === "fresh";
  const img = primary.querySelector("img");
  if (!fresh) { img.removeAttribute("src"); streamRole = null; }
  else if (streamRole !== selected) { img.src = `/api/stream.mjpeg?src=${selected}`; streamRole = selected; }
  for (const [role, tile] of tiles) { tile.hidden = role === selected; paint(tile, role, true); }
}
primary.querySelector("img").addEventListener("error", () => { streamRole = null; });
async function statusLoop() {
  try {
    const start = performance.now();
    const response = await fetch("/status.json", {cache: "no-store", signal: AbortSignal.timeout(1500)});
    if (!response.ok) throw new Error("status unavailable");
    latest = await response.json(); reportAt = start; // includes round-trip age conservatively
    connection.textContent = "LAN viewer · freshness from frame arrival";
  } catch { connection.textContent = "Status unavailable — do not rely on these views"; latest = null; }
  render(); setTimeout(statusLoop, 250);
}
async function snapshots() {
  for (const role of roles) {
    if (role === selected || busy.has(role) || stateFor(role).state !== "fresh") continue;
    busy.add(role); const current = generation;
    (async () => {
      try {
        const started = performance.now();
        const response = await fetch(`/api/frame.jpeg?src=${role}&cache=500ms`, {cache: "no-store", signal: AbortSignal.timeout(1000)});
        if (!response.ok) return;
        const blob = await response.blob();
        const age = Number(response.headers.get("X-Frame-Age-Ms") ?? NaN);
        if (!Number.isFinite(age) || age < 0) return;
        if (current !== generation || role === selected || stateFor(role).state !== "fresh") return;
        const url = URL.createObjectURL(blob), old = blobs.get(role);
        tiles.get(role).querySelector("img").src = url; blobs.set(role, url);
        displayed.set(role, {at: started, age_ms: age});
        if (old) URL.revokeObjectURL(old);
      } catch { /* status loop exposes stale/disconnected state */ }
      finally { busy.delete(role); }
    })();
  }
}
setInterval(render, 100); setInterval(snapshots, 500); statusLoop();
