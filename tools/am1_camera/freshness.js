"use strict";
globalThis.AM1SourceState = function(frame, reportAt, now) {
  if (!frame) return {state: "unavailable", age_ms: null, fps: 0};
  const age = Number.isFinite(frame.age_ms) ? frame.age_ms + now - reportAt : Infinity;
  const fresh = frame.state === "fresh" && age >= 0 && age <= 500;
  return {...frame, age_ms: age, state: fresh ? "fresh" : frame.sequence ? "stale" : "unavailable"};
};
globalThis.AM1FrameState = function(frame, reportAt, now, displayed = null, thumbnail = false, statusReceivedAt = reportAt) {
  const source = AM1SourceState(frame, reportAt, now);
  const displayedAge = displayed ? displayed.age_ms + now - displayed.at : Infinity;
  // Delivered/decoded frame evidence is independent of the status polling clock.
  // Explicit disconnection, invalid metadata or lost status still fail closed.
  const usableStatus = frame?.state === "fresh" && frame.configured !== false &&
                       Number.isFinite(frame.age_ms) && frame.age_ms >= 0 && now - statusReceivedAt <= 2000;
  const fresh = usableStatus && displayedAge >= 0 && displayedAge < (thumbnail ? 1500 : 500);
  return {...source, age_ms: displayedAge, producer_age_ms: source.age_ms,
          status_uncertain: source.state !== "fresh",
          state: fresh ? "fresh" : source.sequence ? "stale" : "unavailable"};
};
// One decoded object URL per semantic role. A connection generation is advanced
// before accepting a restarted source, so an old in-flight decode cannot win.
globalThis.AM1RetainedFrames = class {
  constructor(revoke = url => URL.revokeObjectURL(url)) {
    this.frames = new Map();
    this.generations = new Map();
    this.revoke = revoke;
  }
  setGeneration(role, generation) {
    if (!Number.isSafeInteger(generation) || generation < 0) throw new RangeError("Invalid camera generation");
    if (generation > (this.generations.get(role) ?? -1)) this.generations.set(role, generation);
  }
  accept(role, generation, sequence, objectUrl, ageMsAtReceipt, receivedAt) {
    const previous = this.frames.get(role);
    const valid = (previous || this.frames.size < 5) && generation === this.generations.get(role) &&
      Number.isSafeInteger(sequence) && sequence > 0 &&
      (!previous || previous.generation !== generation || sequence > previous.sequence) &&
      Number.isFinite(ageMsAtReceipt) && ageMsAtReceipt >= 0 &&
      Number.isFinite(receivedAt) && typeof objectUrl === "string" && objectUrl.length > 0;
    if (!valid) { if (objectUrl) this.revoke(objectUrl); return false; }
    this.frames.set(role, {url:objectUrl, generation, sequence, age_at_receipt_ms:ageMsAtReceipt, at:receivedAt});
    if (previous) this.revoke(previous.url);
    return true;
  }
  get(role, now) {
    const frame = this.frames.get(role);
    if (!frame) return null;
    const age_ms = frame.age_at_receipt_ms + Math.max(0, now - frame.at);
    return {...frame, age_ms, state:age_ms < 500 ? "fresh" : "stale"};
  }
  release(role) {
    const previous = this.frames.get(role);
    if (previous) { this.revoke(previous.url); this.frames.delete(role); }
  }
};
