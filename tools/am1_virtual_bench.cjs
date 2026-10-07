"use strict";
// Opt-in, local observation/recovery decisions. This module performs no IO,
// sends no leases or commands and never changes source/deadline clocks.
const assert = require("node:assert/strict");
const path = require("node:path");
const CAMERA_ROLES = ["forward","backward","chest","wrist_left","wrist_right"];
const RECOVERABLE_CAUSES = new Set(["bench required coverage","state-request-failed"]);
function validateVirtualConfig(raw) {
  assert(raw && typeof raw === "object" && !Array.isArray(raw), "Explicit virtual bench configuration required");
  assert(typeof raw.observer_health_path === "string" && path.isAbsolute(raw.observer_health_path), "Private observer health path must be absolute");
  assert(path.basename(raw.observer_health_path) === "latest-health.json", "Use the owned observer health record");
  assert(typeof raw.observer_generation === "string" && /^[A-Za-z0-9_-]{1,128}$/.test(raw.observer_generation));
  assert(Array.isArray(raw.verified_coverage) && new Set(raw.verified_coverage).size===2 && raw.verified_coverage.every(value=>["arms","lift"].includes(value)),
         "Current observer framing for both arms and lift must be verified before motion");
  assert(Array.isArray(raw.required_camera_roles) && new Set(raw.required_camera_roles).size === raw.required_camera_roles.length &&
         raw.required_camera_roles.every(role=>CAMERA_ROLES.includes(role)), "Name exact required semantic camera roles");
  assert(raw.recovery_episode_seconds === 10);
  assert(raw.max_recoveries === 3);
  return {observer_health_path:raw.observer_health_path,observer_generation:raw.observer_generation,
    verified_coverage:[...raw.verified_coverage],required_camera_roles:[...raw.required_camera_roles],
    recovery_episode_seconds:raw.recovery_episode_seconds,max_recoveries:raw.max_recoveries};
}
function cameraRoleQualified(health, role, required=false) {
  const item=health?.roles?.find(item=>item.role===role);
  return health?.version===1 && health.status_available===true && item?.identity===role &&
    item.configured===true && item.fresh===true && item.source_state==="fresh" &&
    Number.isSafeInteger(item.sequence) && item.sequence>0 && Number.isSafeInteger(item.generation) &&
    item.generation===item.decoded_generation && item.selected===(role===health.selected_role) &&
    Number.isFinite(item.decoded_age_ms) && item.decoded_age_ms>=0 && item.decoded_age_ms<(item.selected?500:1500) &&
    Number.isFinite(item.source_age_ms) && item.source_age_ms>=0 && (!required || item.source_age_ms<=500) &&
    Number.isFinite(health.status_received_age_ms) && health.status_received_age_ms>=0 && health.status_received_age_ms<=2000;
}
class VirtualObservationPolicy {
  constructor(config) { this.config=validateVirtualConfig(config); this.lastSequence=null; this.lastSourceTicks=null; this.lastAdvance=null;
    this.advanceCount=0; this.observerRegressed=false; this.acceptedObserver=null; this.reconnections=new Map(); }
  observe(observer, cameras, wallNow, monotonicNow, readErrorCode=null) {
    // Windows atomic replacement can briefly deny opening. Retain only a
    // qualified original record; every source/receipt/advance clock still expires.
    const metadataUncertain=!observer && ["EACCES","EPERM"].includes(readErrorCode) && !!this.acceptedObserver;
    if (metadataUncertain) observer=this.acceptedObserver;
    let reason=null;
    const finite=value=>Number.isFinite(value) && value>=0;
    const receiptAge=finite(observer?.received_wall_time_ms) ? wallNow-observer.received_wall_time_ms : null;
    const captureAge=finite(observer?.capture_age_ms) && finite(observer?.round_trip_ms) && finite(receiptAge) ?
      Math.max(observer.capture_age_ms,observer.round_trip_ms)+receiptAge : null;
    if (!observer || observer.generation!==this.config.observer_generation) reason="observer generation";
    else if (!observer.running || !observer.recording || observer.challenge_qualified!==true ||
             typeof observer.nonce!=="string" || observer.nonce.length<1 || observer.nonce.length>128) reason="observer challenge";
    else if (!Number.isSafeInteger(observer.sequence) || observer.sequence<1 ||
             !Number.isSafeInteger(observer.source_system_relative_ticks) || observer.source_system_relative_ticks<0) reason="observer sequence";
    else if (!finite(observer.capture_age_ms) || !finite(captureAge) || captureAge>500) reason="observer capture age";
    else if (!finite(observer.round_trip_ms) || observer.round_trip_ms>750) reason="observer round trip";
    else if (!finite(receiptAge) || receiptAge>1000) reason="observer receipt age";
    if (!reason) {
      if (this.lastSequence!==null && (observer.sequence<this.lastSequence || observer.source_system_relative_ticks<this.lastSourceTicks ||
          (observer.sequence!==this.lastSequence) !== (observer.source_system_relative_ticks!==this.lastSourceTicks))) this.observerRegressed=true;
      if (this.observerRegressed) reason="observer sequence regressed";
      else if (this.lastSequence===null || observer.sequence>this.lastSequence) {
        this.lastSequence=observer.sequence; this.lastSourceTicks=observer.source_system_relative_ticks;
        this.lastAdvance=monotonicNow; this.advanceCount++;
      }
      if (!reason && (this.advanceCount<2 || monotonicNow-this.lastAdvance>1000)) reason="observer advancing capture required";
    }
    this.acceptedObserver=reason===null ? Object.freeze({...observer}) : null;
    const degraded=CAMERA_ROLES.filter(role=>!cameraRoleQualified(cameras,role));
    const missing=this.config.required_camera_roles.filter(role=>!cameraRoleQualified(cameras,role,true));
    const observerResult={qualified:reason===null,reason,metadata_uncertain:metadataUncertain,generation:observer?.generation??null,sequence:observer?.sequence??null,
      capture_age_ms:observer?.capture_age_ms??null,effective_capture_age_ms:captureAge,
      round_trip_ms:observer?.round_trip_ms??null,receipt_age_ms:receiptAge,
      local_sequence_age_ms:this.lastAdvance===null?null:monotonicNow-this.lastAdvance};
    return {required_coverage_qualified:reason===null && missing.length===0,observer:observerResult,
      required_camera_roles:[...this.config.required_camera_roles],missing_required_roles:missing,
      all_five_fresh:degraded.length===0,degraded_roles:degraded,status_uncertain:cameras?.status_uncertain??true};
  }
  reconnectRoles(observation, now) {
    return observation.degraded_roles.filter(role=>{
      const last=this.reconnections.get(role);
      if ((last?.attempts??0)>=3 || last && now-last.at<1000) return false;
      this.reconnections.set(role,{at:now,attempts:(last?.attempts??0)+1}); return true;
    });
  }
}
function qualifyRecovery(state, ownedSession, currentCoverage, maxRecoveries) {
  const bench=state?.virtual_bench, gate=state?.pending_gate, age=state?.telemetry?.observation?.age_ms;
  if (!ownedSession || state?.session_id!==ownedSession || state.native_connected!==true || !currentCoverage ||
      !bench?.enabled || bench.disarmed!==false || bench.required_coverage_qualified!==true || bench.recovery_eligible!==true ||
      !RECOVERABLE_CAUSES.has(bench.cause) || state.input_pause?.reason!==bench.cause ||
      !Array.isArray(gate) || gate[0]!=="resume" || gate[1]!==bench.host_epoch ||
      !Number.isSafeInteger(bench.pause_sequence) || !Number.isSafeInteger(bench.host_epoch) ||
      !Number.isSafeInteger(bench.input_epoch) || state.input_epoch!==bench.input_epoch ||
      !Number.isSafeInteger(bench.recovery_count) || bench.recovery_count>=maxRecoveries ||
      !Number.isFinite(bench.remaining_seconds) || bench.remaining_seconds<=0 ||
      !Number.isFinite(age) || age<0 || age>250) return null;
  return {pause_sequence:bench.pause_sequence,input_epoch:bench.input_epoch,host_epoch:bench.host_epoch};
}
function recoveryAcknowledged(state, proof, elapsedMs, budgetMs) {
  const observation=state?.telemetry?.observation;
  return !!proof && elapsedMs>=0 && elapsedMs<budgetMs && state.phase==="live" &&
    state.native_connected===true && state.pause_required===false && !state.pending_gate &&
    state.input_epoch===proof.input_epoch && observation?.host_state==="active" &&
    observation.host_epoch===proof.host_epoch+1 && Number.isFinite(observation.age_ms) &&
    observation.age_ms>=0 && observation.age_ms<=250 && state.virtual_bench?.disarmed===false &&
    state.virtual_bench.required_coverage_qualified===true;
}
module.exports={recoveryAcknowledged,validateVirtualConfig,VirtualObservationPolicy,qualifyRecovery,cameraRoleQualified,RECOVERABLE_CAUSES};
