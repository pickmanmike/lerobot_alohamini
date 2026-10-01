"use strict";
// Increment A/B shell: opening or refreshing the page is deliberately read-only.
async function am1ReadState() {
  try {
    const response = await fetch("/api/state", {cache:"no-store"});
    if (!response.ok) throw new Error("state unavailable");
    const state = await response.json();
    document.querySelector("#session-state").textContent = state.session_id ?
      `Session ${state.session_id}: ${state.phase}` : "No session is active.";
  } catch {
    document.querySelector("#session-state").textContent = "Session state unavailable; no motor readiness implied.";
  }
}
am1ReadState();
