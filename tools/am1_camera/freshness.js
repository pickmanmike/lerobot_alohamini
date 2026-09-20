"use strict";
globalThis.AM1FrameState = function(frame, reportAt, now, displayed = null, thumbnail = false) {
  if (!frame) return {state: "unavailable", age_ms: null, fps: 0};
  const age = Number.isFinite(frame.age_ms) ? frame.age_ms + now - reportAt : Infinity;
  const displayedAge = displayed ? displayed.age_ms + now - displayed.at : Infinity;
  const fresh = frame.state === "fresh" && age >= 0 && age <= 500 && (!thumbnail || displayedAge < 1500);
  return {...frame, age_ms: age, state: fresh ? "fresh" : frame.sequence ? "stale" : "unavailable"};
};
