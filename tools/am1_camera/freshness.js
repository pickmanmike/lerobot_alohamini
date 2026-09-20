"use strict";
globalThis.AM1SourceState = function(frame, reportAt, now) {
  if (!frame) return {state: "unavailable", age_ms: null, fps: 0};
  const age = Number.isFinite(frame.age_ms) ? frame.age_ms + now - reportAt : Infinity;
  const fresh = frame.state === "fresh" && age >= 0 && age <= 500;
  return {...frame, age_ms: age, state: fresh ? "fresh" : frame.sequence ? "stale" : "unavailable"};
};
globalThis.AM1FrameState = function(frame, reportAt, now, displayed = null, thumbnail = false) {
  const source = AM1SourceState(frame, reportAt, now);
  const displayedAge = displayed ? displayed.age_ms + now - displayed.at : Infinity;
  const fresh = source.state === "fresh" && displayedAge >= 0 && displayedAge < (thumbnail ? 1500 : 500);
  return {...source, age_ms: displayedAge, producer_age_ms: source.age_ms,
          state: fresh ? "fresh" : source.sequence ? "stale" : "unavailable"};
};
