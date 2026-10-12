"""Immutable explicitly selected session recipes and JSON validation."""

import hashlib
import json
import math
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class Recipe:
    name: str
    mode: str
    trajectory_s: float | None
    live_s: float
    seed: int | None = 17


@dataclass(frozen=True)
class SignedElbowResponse:
    """One explicit signed diagnostic, shared by input, owner and completion."""

    recipe_name: str = "physical-left-elbow-response-characterize"
    profile: str = "LeftElbowResponseCharacterize"
    trajectory_s: float = 60.0
    live_s: float = 180.0
    joint: str = "arm_left_elbow_flex.pos"
    decision_s: float = 16.0

    def phase(self, progress_s, expanded):
        if progress_s < 2:
            return 0, "baseline", 0.0, -3.0
        final_at = 58.0 if expanded else 16.0
        if progress_s >= final_at:
            return (4 if expanded else 2), "final", 0.0, (10.0 if expanded else -3.0)
        if progress_s < 16:
            cycle = min(int((progress_s - 2) // 7) + 1, 2)
            within, ramp, delta = progress_s - 2 - (cycle - 1) * 7, 3.0, -3.0
        else:
            cycle = min(int((progress_s - 16) // 21) + 3, 4)
            within, ramp, delta = progress_s - 16 - (cycle - 3) * 21, 10.0, 10.0
        if within < ramp:
            return cycle, "outbound", within / ramp, delta
        if within < ramp + .5:
            return cycle, "endpoint", 1.0, delta
        if within < 2 * ramp + .5:
            return cycle, "return", 1 - (within - ramp - .5) / ramp, delta
        return cycle, "origin", 0.0, delta

    def context_valid(self, context):
        if not isinstance(context, dict) or context.get("profile") != self.profile:
            return False
        values = [context.get(k) for k in ("progress_s", "reference", "endpoint_target", "requested_target")]
        if not all(type(v) in (int, float) and math.isfinite(v) for v in values):
            return False
        progress, reference, endpoint, requested = values
        if not 0 <= progress <= self.trajectory_s or not -100 <= reference - 3 <= reference + 10 <= 100:
            return False
        for expanded in (False, True):
            duration = 60 if expanded else 18
            if progress > duration:
                continue
            cycle, phase, fraction, delta = self.phase(progress, expanded)
            key = self.sample_key(progress, expanded)
            if (
                type(context.get("cycle")) is int and context["cycle"] == cycle
                and context.get("phase") == phase and context.get("sample_key") == key
                and math.isclose(endpoint, reference + delta, abs_tol=1e-9, rel_tol=0)
                and math.isclose(requested, reference + fraction * delta, abs_tol=1e-9, rel_tol=0)
            ):
                return True
        return False

    def sample_key(self, progress_s, expanded):
        cycle, phase, _, _ = self.phase(progress_s, expanded)
        if phase == "baseline":
            return "baseline"
        if phase == "endpoint":
            return f"cycle{cycle}_endpoint"
        if phase == "origin" and progress_s >= {1: 9, 2: 16, 3: 37, 4: 58}[cycle] - .2 - 1e-9:
            return f"cycle{cycle}_origin"
        return None

    @staticmethod
    def response_threshold(excursion_raw, noise_raw):
        return max(4, math.ceil(abs(excursion_raw) / 2), 3 * noise_raw)

    @staticmethod
    def return_limit(noise_raw):
        return max(2, 3 * noise_raw)

    @staticmethod
    def encoded_goal(normalized, calibration):
        if (
            type(normalized) not in (int, float) or not math.isfinite(normalized) or not -100 <= normalized <= 100
            or not isinstance(calibration, dict) or calibration.get("drive_mode") != 0
            or not all(type(calibration.get(k)) is int for k in ("range_min", "range_max", "homing_offset", "drive_mode"))
            or not 0 <= calibration["range_min"] < calibration["range_max"] <= 4095
        ):
            return None
        return int(((normalized + 100) / 200) * (
            calibration["range_max"] - calibration["range_min"]
        ) + calibration["range_min"])

    def branch_expectation(self, outcome):
        if not isinstance(outcome, dict):
            return None
        cycles = outcome.get("cycles")
        branch = outcome.get("branch")
        expected = {"negative_distinguished": (18.0, 2, 1), "positive_control": (60.0, 4, 1)}.get(branch)
        if expected is None or not isinstance(cycles, list) or len(cycles) != expected[1]:
            return None
        baseline, noise = outcome.get("baseline_raw"), outcome.get("baseline_noise_raw")
        if type(baseline) is not int or type(noise) is not int or not 0 <= noise <= 4095:
            return None
        reference, calibration = outcome.get("reference_normalized"), outcome.get("calibration")
        if (
            type(reference) not in (int, float) or not math.isfinite(reference)
            or not -100 <= reference - 3 <= reference + 10 <= 100
            or self.encoded_goal(reference, calibration) != baseline
        ):
            return None
        useful = []
        for index, row in enumerate(cycles, 1):
            if not isinstance(row, dict):
                return None
            numeric = ("endpoint_goal_raw", "signed_response_raw", "return_residual_raw", "response_threshold_raw")
            if not all(type(row.get(k)) is int for k in numeric):
                return None
            excursion = row["endpoint_goal_raw"] - baseline
            threshold = self.response_threshold(excursion, noise)
            if (
                row.get("cycle") != index or type(row.get("cycle")) is not int
                or excursion == 0 or (excursion < 0) != (index <= 2)
                or row["endpoint_goal_raw"] != self.encoded_goal(reference + (-3 if index <= 2 else 10), calibration)
                or row["response_threshold_raw"] != threshold
                or row.get("return_qualified") is not True or row.get("adverse") is not False
                or not 0 <= row["return_residual_raw"] <= self.return_limit(noise)
            ):
                return None
            useful.append(row["signed_response_raw"] >= threshold)
        if (branch == "negative_distinguished") != all(useful[:2]):
            return None
        return expected

    def completion_expectation(self, execution):
        if not isinstance(execution, dict):
            return None
        expected = self.branch_expectation(execution.get("characterization"))
        if expected is None:
            return None
        outcome = execution["characterization"]
        residual = outcome.get("final_return_residual_raw")
        if type(residual) is not int or not 0 <= residual <= self.return_limit(outcome["baseline_noise_raw"]):
            return None
        seed = execution.get("original_seed")
        if not isinstance(seed, dict) or seed.get(self.joint) != execution["characterization"]["reference_normalized"]:
            return None
        if (
            execution.get("progress_s") != expected[0]
            or type(execution.get("cycles_completed")) is not int or execution["cycles_completed"] != expected[1]
            or type(execution.get("returns_qualified")) is not int or execution["returns_qualified"] != 1
        ):
            return None
        return expected


LEFT_ELBOW_RESPONSE = SignedElbowResponse()


@dataclass(frozen=True)
class PositiveElbowControl:
    """Separate visible I0 out/back control with truthful coarse and fine scores."""

    recipe_name: str = "physical-left-elbow-positive-control"
    profile: str = "LeftElbowPositiveControl"
    joint: str = "arm_left_elbow_flex.pos"
    trajectory_s: float = 30.0
    nominal_trajectory_s: float = 28.0
    live_s: float = 60.0
    current_cap_ma: float = 100.0
    encoded_goal = staticmethod(SignedElbowResponse.encoded_goal)

    def phase(self, progress, acquisition_s=0):
        t = progress - acquisition_s
        if t < 0:
            return 0, "acquire", 0.0
        if t < 2:
            return 0, "baseline", 0.0
        if t < 12:
            return 1, "outbound", (t - 2) / 10
        if t < 14:
            return 1, "endpoint", 1.0
        if t < 24:
            return 1, "return", 1 - (t - 14) / 10
        return 1, "origin" if t < 26 else "final", 0.0

    def sample_key(self, progress, acquisition_s=0):
        _, phase, _ = self.phase(progress, acquisition_s)
        return ("baseline" if phase == "baseline" else "cycle1_endpoint" if phase == "endpoint"
                else "cycle1_origin" if progress >= acquisition_s + 27.8 - 1e-9 else None)

    def context_valid(self, c):
        keys = {"profile", "progress_s", "phase", "cycle", "reference", "endpoint_target",
                "requested_target", "sample_key", "acquisition_s", "admitted_reference"}
        if not isinstance(c, dict) or set(c) != keys or c["profile"] != self.profile:
            return False
        nums = ("progress_s", "reference", "endpoint_target", "requested_target", "acquisition_s", "admitted_reference")
        if not all(type(c[k]) in (int, float) and math.isfinite(c[k]) for k in nums):
            return False
        t, q0, acquisition, admitted = c["progress_s"], c["reference"], c["acquisition_s"], c["admitted_reference"]
        if (acquisition not in (0, 2) or not 0 <= t <= 28 + acquisition
                or not -100 <= q0 - 10 <= q0 + 10 <= 100
                or abs(admitted - q0) > 2 or acquisition == 0 and admitted != q0):
            return False
        cycle, phase, fraction = self.phase(t, acquisition)
        target = admitted + (q0 - admitted) * t / 2 if phase == "acquire" else q0 + 10 * fraction
        return (type(c["cycle"]) is int and c["cycle"] == cycle and c["phase"] == phase
                and c["sample_key"] == self.sample_key(t, acquisition)
                and math.isclose(c["endpoint_target"], q0 + 10, abs_tol=1e-9, rel_tol=0)
                and math.isclose(c["requested_target"], target, abs_tol=1e-9, rel_tol=0))

    @staticmethod
    def noise_discriminating(excursion_raw, noise_raw):
        return noise_raw < math.ceil(excursion_raw / 2) and noise_raw < math.floor(excursion_raw / 5)

    @staticmethod
    def measured_origin(normalized, calibration):
        """Invert the frozen native normalized seed, never the floor-encoded goal."""
        if SignedElbowResponse.encoded_goal(normalized,calibration) is None:
            return None
        span=calibration["range_max"]-calibration["range_min"]
        raw=round((normalized+100)/200*span+calibration["range_min"])
        actual=(raw-calibration["range_min"])/span*200-100
        return raw if math.isclose(normalized,actual,abs_tol=1e-9,rel_tol=0) else None

    def measurement_proof_valid(self, proof, normalized, calibration, *, run_id=None, host_incarnation=None):
        try:
            if (not isinstance(proof,dict) or type(proof.get("version")) is not int or proof["version"] != 1
                or run_id is not None and proof.get("run_id") != run_id
                or host_incarnation is not None and proof.get("host_incarnation") != host_incarnation
                or not all(type(proof.get(k)) is int and proof[k]>=0 for k in ("epoch","intent_revision","action_sequence","observation_id"))
                or not self.context_valid(proof.get("context"))):
                return False
            motor,position,context,action=proof["motor"],proof["position"],proof["context"],proof["action"]
            measured=self.measured_origin(normalized,calibration)
            goal=self.encoded_goal(normalized,calibration)
            captured=proof["captured_at_monotonic_s"]
            first,last=position["read_started_at_monotonic_s"],position["read_completed_at_monotonic_s"]
            if (measured is None or abs(measured-goal)>1 or motor.get("calibration") != calibration
                or (motor.get("bus"),motor.get("name"),motor.get("id"),motor.get("model"),motor.get("normalization"))
                   != ("left",self.joint[:-4],3,"sts3215","range_m100_100")
                or type(position.get("raw")) is not int
                or not all(type(v) in (int,float) and math.isfinite(v) for v in (captured,first,last,proof["acquired_at_monotonic_s"],position["normalized"]))
                or not 0<=first<=last<=captured or captured-first>.5
                or not 0<=proof["acquired_at_monotonic_s"]<=captured
                or not math.isclose(position["normalized"],(position["raw"]-calibration["range_min"])/(calibration["range_max"]-calibration["range_min"])*200-100,abs_tol=1e-9,rel_tol=0)
                or action.get("expected_goal_raw") != self.encoded_goal(context["requested_target"],calibration)
                or action.get("sync_write_returned") is not True):
                return False
            return True
        except (KeyError,ValueError,TypeError,AttributeError):
            return False

    @staticmethod
    def window_valid(w):
        if not isinstance(w, dict) or type(w.get("sample_count")) is not int or not 3 <= w["sample_count"] <= 20:
            return False
        keys = {f"{end}_{name}_{part}_at" for end in ("first", "last")
                for name in ("current", "position") for part in ("started", "completed")}
        keys |= {"qualified_at_monotonic_s", "current_span_s", "position_span_s"}
        if set(w) != keys | {"sample_count"} or not all(type(w[k]) in (int, float) and math.isfinite(w[k]) for k in keys):
            return False
        now = w["qualified_at_monotonic_s"]
        for name in ("current", "position"):
            a, b = w[f"first_{name}_completed_at"], w[f"last_{name}_completed_at"]
            sa, sb = w[f"first_{name}_started_at"], w[f"last_{name}_started_at"]
            if (not 0 <= sa <= a < b <= now or not sa <= sb <= b
                    or now - sa > .5 or now - sb > .5 or b - a < .2 - 1e-9
                    or not math.isclose(w[f"{name}_span_s"], b - a, abs_tol=1e-9, rel_tol=0)):
                return False
        return True

    @staticmethod
    def settings_valid(proof, run_id=None, host_incarnation=None):
        return PositiveElbowControl._settings_valid(proof, run_id, host_incarnation)

    @staticmethod
    def _settings_valid(proof, run_id=None, host_incarnation=None, *,
                        recipe_name="physical-left-elbow-positive-control", current_cap_ma=100.0,
                        experiment_sha256=None):
        import hashlib
        import json

        static = {"Firmware_Major_Version", "Firmware_Minor_Version", "Model_Number", "ID",
                  "Return_Delay_Time", "Min_Position_Limit", "Max_Position_Limit", "Homing_Offset", "Phase",
                  "P_Coefficient", "I_Coefficient", "D_Coefficient", "Operating_Mode", "Acceleration",
                  "Goal_Time", "Goal_Velocity", "Torque_Limit", "Maximum_Acceleration"}
        dynamic = {"Torque_Enable", "Lock", "Goal_Position", "Present_Position", "Present_Current", "Status",
                   "Present_Voltage", "Present_Temperature"}
        try:
            if (set(proof) != {"version", "binding", "before", "active", "unchanged", "settings_sha256"}
                    or type(proof["version"]) is not int or proof["version"] != 1 or proof["unchanged"] is not True):
                return False
            binding = proof["binding"]
            binding_keys = {"run_id", "host_incarnation", "recipe"}
            if experiment_sha256 is not None:
                binding_keys.add("experiment_sha256")
            if (set(binding) != binding_keys
                    or binding["recipe"] != recipe_name
                    or experiment_sha256 is not None and binding.get("experiment_sha256") != experiment_sha256
                    or not all(type(binding[k]) is str and binding[k] for k in binding)
                    or run_id is not None and binding["run_id"] != run_id
                    or host_incarnation is not None and binding["host_incarnation"] != host_incarnation):
                return False
            previous = 0
            for name, torque in (("before", 0), ("active", 1)):
                item = proof[name]
                r, ib = item["registers"], item["individual_bytes"]
                if (set(item) != {"registers", "individual_bytes", "read_started_at_monotonic_s", "read_completed_at_monotonic_s"}
                        or set(r) != static | dynamic or not all(type(v) is int for v in r.values())
                        or set(ib) != {"24", "25"} or not all(type(v) is int and 0 <= v <= 255 for v in ib.values())
                        or any(r[k] != v for k, v in {"Model_Number":777, "Firmware_Major_Version":3,
                            "Firmware_Minor_Version":10, "ID":3, "Operating_Mode":0, "P_Coefficient":16,
                            "I_Coefficient":0, "D_Coefficient":32, "Torque_Enable":torque, "Lock":torque,
                            "Status":0}.items()) or abs(r["Present_Current"]) * 6.5 >= current_cap_ma):
                    return False
                a, b = item["read_started_at_monotonic_s"], item["read_completed_at_monotonic_s"]
                if not all(type(v) in (int, float) and math.isfinite(v) for v in (a,b)) or not 0 <= previous <= a <= b:
                    return False
                previous = b
            before, active = proof["before"], proof["active"]
            material = {"registers":{k:before["registers"][k] for k in static}, "individual_bytes":before["individual_bytes"]}
            return (all(before["registers"][k] == active["registers"][k] for k in static)
                    and before["individual_bytes"] == active["individual_bytes"]
                    and hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                    == proof["settings_sha256"])
        except (KeyError, TypeError, AttributeError):
            return False

    def completion_expectation(self, execution):
        try:
            return self._completion_expectation(execution)
        except (ValueError,TypeError,KeyError,AttributeError,OverflowError):
            return None

    def _completion_expectation(self, execution):
        if not isinstance(execution, dict):
            return None
        o, seed = execution.get("positive_control"), execution.get("original_seed")
        if not isinstance(o, dict) or not isinstance(seed, dict):
            return None
        if type(o.get("reference_normalized")) not in (int,float) or not math.isfinite(o["reference_normalized"]):
            return None
        joint_keys = {f"arm_{side}_{joint}.pos" for side in ("left","right") for joint in
                      ("shoulder_pan","shoulder_lift","elbow_flex","wrist_flex","wrist_roll","gripper")}
        if set(seed) != joint_keys or not all(type(v) in (int,float) and math.isfinite(v) for v in seed.values()):
            return None
        nums = ("reference_raw", "endpoint_raw", "encoded_excursion_raw", "outward_response_raw",
                "reverse_response_raw", "final_return_residual_raw", "baseline_noise_raw", "reverse_anchor_raw",
                "measured_origin_raw", "outward_anchor_raw", "encoded_goal_residual_raw")
        if not all(type(o.get(k)) is int for k in nums) or min(o[k] for k in nums if k != "reverse_anchor_raw") < 0:
            return None
        q0, calibration = o.get("reference_normalized"), o.get("calibration")
        delta = getattr(self, "excursion_norm", 10.0)
        magnitude = abs(delta)
        acquisition = o.get("acquisition_s")
        if (acquisition not in (0, 2) or type(acquisition) not in (int, float)
                or not -100 <= seed.get(self.joint, -1000) - magnitude <= seed.get(self.joint, 1000) + magnitude <= 100
                or seed[self.joint] != q0 or self.encoded_goal(q0, calibration) != o["reference_raw"]
                or self.encoded_goal(q0 + delta, calibration) != o["endpoint_raw"]
                or o["encoded_excursion_raw"] != abs(o["endpoint_raw"] - o["reference_raw"])
                or o["encoded_excursion_raw"] <= 0 or o.get("program_completed") is not True
                or o.get("adverse") is not False or not self.window_valid(o.get("final_window"))
                or not self.settings_valid(o.get("visible_control"))):
            return None
        direction = 1 if o["endpoint_raw"] > o["reference_raw"] else -1
        if isinstance(self, SelectedElbowExperiment) and (
            o.get("experiment") != self.summary() or not self.criteria_valid(o["encoded_excursion_raw"])
        ):
            return None
        measured=self.measured_origin(q0,calibration)
        binding=o["visible_control"]["binding"]
        original=o.get("measured_origin_proof")
        baseline=o.get("baseline_anchor_proof")
        baseline_window=o.get("baseline_window")
        if (measured is None or o["measured_origin_raw"] != measured
            or not self.measurement_proof_valid(original,q0,calibration,
                run_id=binding["run_id"] if acquisition==0 else o.get("confirmation_of"),
                host_incarnation=binding["host_incarnation"] if acquisition==0 else None)
            or original["context"]["progress_s"] != 0
            or original["context"]["reference"] != q0
            or original["position"]["raw"] not in {measured,self.encoded_goal(q0,calibration)}
            or not self.measurement_proof_valid(baseline,q0,calibration,run_id=binding["run_id"],host_incarnation=binding["host_incarnation"])
            or baseline["context"]["phase"] != "baseline" or baseline["context"]["reference"] != q0
            or baseline["position"]["raw"] != o["outward_anchor_raw"]
            or not self.window_valid(baseline_window)
            or baseline_window["last_position_completed_at"] != baseline["position"]["read_completed_at_monotonic_s"]
            or baseline_window["qualified_at_monotonic_s"] != baseline["captured_at_monotonic_s"]):
            return None
        endpoint,peak,departure=o.get("endpoint_anchor_proof"),o.get("endpoint_peak_proof"),o.get("first_return_proof")
        endpoint_window=o.get("endpoint_window")
        for item,phase in ((endpoint,"endpoint"),(peak,"endpoint"),(departure,"return")):
            if (not self.measurement_proof_valid(item,q0,calibration,run_id=binding["run_id"],host_incarnation=binding["host_incarnation"])
                or item["context"]["phase"] != phase or item["context"]["reference"] != q0):
                return None
        if (not self.window_valid(endpoint_window)
            or endpoint_window["qualified_at_monotonic_s"] != departure["captured_at_monotonic_s"]
            or endpoint_window["last_position_completed_at"] != endpoint["position"]["read_completed_at_monotonic_s"]
            or peak["captured_at_monotonic_s"] > departure["captured_at_monotonic_s"]
            or endpoint["position"]["raw"] != o["reverse_anchor_raw"]
            or o["outward_response_raw"] != max(0,direction*(peak["position"]["raw"]-o["outward_anchor_raw"]))):
            return None
        coverage, receipts = o.get("phase_coverage"), o.get("phase_receipts")
        if not isinstance(coverage,dict) or not isinstance(receipts,dict) or set(receipts) != {"baseline","cycle1_endpoint","cycle1_origin"}:
            return None
        for phase in ("baseline","outbound","endpoint","return","origin","final"):
            c = coverage.get(phase)
            if (not isinstance(c,dict) or type(c.get("sample_count")) is not int or c["sample_count"] < 3
                or not all(type(c.get(k)) in (int,float) and math.isfinite(c[k]) for k in
                    ("first_current_at","last_current_at","first_position_at","last_position_at"))
                or any(not 0 <= c[f"first_{name}_at"] < c[f"last_{name}_at"] <= o["final_window"]["qualified_at_monotonic_s"]
                       or c[f"last_{name}_at"]-c[f"first_{name}_at"] < .2-1e-9 for name in ("current","position"))
                or type(c.get("minimum_raw")) is not int or type(c.get("maximum_raw")) is not int
                or not calibration["range_min"] <= c["minimum_raw"] <= c["maximum_raw"] <= calibration["range_max"]):
                return None
        binding = o["visible_control"]["binding"]
        for key,r in receipts.items():
            context,action,decoded = r.get("context"),r.get("action"),r.get("decoded")
            goal = o["endpoint_raw"] if key == "cycle1_endpoint" else o["reference_raw"]
            if (r.get("run_id") != binding["run_id"] or r.get("host_incarnation") != binding["host_incarnation"]
                or r.get("sample_key") != key or not self.context_valid(context) or context["sample_key"] != key
                or context["reference"] != q0 or context["acquisition_s"] != acquisition
                or context["admitted_reference"] != o.get("admitted_reference")
                or r.get("diagnostic_available") is not True or r.get("goal_matches_expected_raw") is not True
                or not isinstance(action,dict) or action.get("expected_goal_raw") != goal
                or not isinstance(decoded,dict) or type(decoded.get("goal_position_raw")) is not int
                or type(decoded.get("present_current_ma")) not in (int,float)
                or not math.isfinite(decoded["present_current_ma"]) or abs(decoded["present_current_ma"]) >= self.current_cap_ma
                or decoded["goal_position_raw"] != goal or any(type(decoded.get(k)) is not int or decoded[k] != v for k,v in
                    {"status":0,"torque_enable":1,"operating_mode":0}.items())
                or not all(type(r.get(k)) is int and r[k] >= 0 for k in ("epoch","intent_revision","action_sequence","observation_id"))
                or not all(type(r.get(k)) in (int,float) and math.isfinite(r[k]) for k in
                    ("read_started_at_monotonic_s","read_completed_at_monotonic_s","acquired_at_monotonic_s"))
                or not 0 <= r["acquired_at_monotonic_s"] <= r["read_started_at_monotonic_s"] <= r["read_completed_at_monotonic_s"] <= o["final_window"]["qualified_at_monotonic_s"]):
                return None
        origin = receipts["cycle1_origin"]
        if (o["final_window"]["qualified_at_monotonic_s"]-origin["read_started_at_monotonic_s"] > .5
            or type(origin["decoded"].get("present_position_raw")) is not int
            or abs(origin["decoded"]["present_position_raw"]-o["measured_origin_raw"]) > o["final_return_residual_raw"]):
            return None
        admitted, compared = o.get("admission_reference"),o.get("comparison_reference")
        if (not isinstance(admitted,dict) or set(admitted) != joint_keys
            or not all(type(v) in (int,float) and math.isfinite(v) for v in admitted.values())
            or admitted[self.joint] != o.get("admitted_reference")
            or any(admitted[k] != seed[k] for k in joint_keys if k != self.joint)):
            return None
        if acquisition == 0:
            if admitted != seed or compared is not None or o.get("other_joint_admission_differences") != {}:
                return None
        elif (not isinstance(compared,dict) or set(compared) != joint_keys or compared[self.joint] != q0
              or abs(admitted[self.joint]-q0) > 2
              or not all(type(v) in (int,float) and math.isfinite(v) for v in compared.values())
              or any(abs(admitted[k]-compared[k]) > 1 for k in joint_keys if k != self.joint)
              or o.get("other_joint_admission_differences") != {k:admitted[k]-compared[k] for k in joint_keys if k != self.joint}):
            return None
        excursion = o["encoded_excursion_raw"]
        discriminating = self.noise_discriminating(excursion,o["baseline_noise_raw"])
        minimum = getattr(self, "minimum_response_raw", math.ceil(excursion / 2))
        tolerance = getattr(self, "return_tolerance_raw", math.floor(excursion / 5))
        coarse = (discriminating and o["outward_response_raw"] >= minimum
                  and o["reverse_response_raw"] >= minimum
                  and o["final_return_residual_raw"] <= tolerance)
        if (o.get("noise_discriminating") is not discriminating or o.get("coarse_functional_pass") is not coarse
                or o.get("fine_return_pass") is not (o["final_return_residual_raw"] <= 2)
                or (acquisition == 0) != (o.get("confirmation_of") is None)
                or acquisition == 2 and uuid_text(o.get("confirmation_of")) != o["confirmation_of"]
                or execution.get("progress_s") != self.nominal_trajectory_s + acquisition
                or type(execution.get("cycles_completed")) is not int or execution["cycles_completed"] != 1
                or type(execution.get("returns_qualified")) is not int or execution["returns_qualified"] != int(coarse)):
            return None
        return self.nominal_trajectory_s + acquisition, 1, int(coarse)


LEFT_ELBOW_POSITIVE_CONTROL = PositiveElbowControl()


# This reviewed initial envelope is replaceable when new evidence supports a
# different finite condition. It is not a packet-wide physical operating limit.
SELECTED_ELBOW_EXPERIMENT_ENVELOPE = MappingProxyType({
    "schema_version": 1,
    "scope": "AM1-ELBOW-TO-COMPLETION-06/initial-negative-comparator",
    "maximum_excursion_norm": 10.0,
    "maximum_slope_norm_per_s": 1.0,
    "maximum_endpoint_dwell_s": 2.0,
    "maximum_trajectory_s": 30.0,
    "maximum_live_s": 60.0,
    "maximum_current_ma": 100.0,
    "minimum_response_fraction": 0.5,
    "maximum_return_fraction": 0.2,
    "maximum_goal_readbacks": 2,
})
SELECTED_ELBOW_EXPERIMENT_ENVELOPE_SHA256 = hashlib.sha256(json.dumps(
    dict(SELECTED_ELBOW_EXPERIMENT_ENVELOPE), sort_keys=True, allow_nan=False,
    separators=(",", ":"),
).encode()).hexdigest()

# The uninterrupted negative return showed useful motion at the old cap. This
# separately reviewed condition permits 150 mA only with a local response bound.
SELECTED_ELBOW_EXPERIMENT_RESPONSE_ENVELOPE = MappingProxyType({
    **dict(SELECTED_ELBOW_EXPERIMENT_ENVELOPE),
    "scope": "AM1-ELBOW-TO-COMPLETION-06/evidence-supported-response",
    "maximum_current_ma": 150.0,
    "response_commanded_error_raw": 30,
    "response_minimum_raw": 3,
    "response_window_s": 1.2,
})
SELECTED_ELBOW_EXPERIMENT_RESPONSE_ENVELOPE_SHA256 = hashlib.sha256(json.dumps(
    dict(SELECTED_ELBOW_EXPERIMENT_RESPONSE_ENVELOPE), sort_keys=True, allow_nan=False,
    separators=(",", ":"),
).encode()).hexdigest()


@dataclass(frozen=True)
class SelectedElbowExperiment:
    """One validated, pinned finite condition on the existing selected provider."""

    schema_version: int
    envelope_sha256: str
    excursion_norm: float
    ramp_s: float
    endpoint_dwell_s: float
    current_cap_ma: float
    live_ceiling_s: float
    minimum_response_raw: int
    return_tolerance_raw: int
    goal_readback_checkpoints_s: tuple[float, ...]

    recipe_name = "physical-left-elbow-experiment"
    profile = "LeftElbowExperiment"
    joint = "arm_left_elbow_flex.pos"
    trajectory_s = 30.0
    encoded_goal = staticmethod(SignedElbowResponse.encoded_goal)
    noise_discriminating = staticmethod(PositiveElbowControl.noise_discriminating)
    measured_origin = staticmethod(PositiveElbowControl.measured_origin)
    window_valid = staticmethod(PositiveElbowControl.window_valid)
    measurement_proof_valid = PositiveElbowControl.measurement_proof_valid
    completion_expectation = PositiveElbowControl.completion_expectation
    _completion_expectation = PositiveElbowControl._completion_expectation

    def __post_init__(self):
        envelopes = {
            SELECTED_ELBOW_EXPERIMENT_ENVELOPE_SHA256: SELECTED_ELBOW_EXPERIMENT_ENVELOPE,
            SELECTED_ELBOW_EXPERIMENT_RESPONSE_ENVELOPE_SHA256: SELECTED_ELBOW_EXPERIMENT_RESPONSE_ENVELOPE,
        }
        if (type(self.schema_version) is not int or self.schema_version != 1
                or type(self.envelope_sha256) is not str or self.envelope_sha256 not in envelopes):
            raise ValueError("selected experiment requires its exact schema and approved envelope pin")
        e = envelopes[self.envelope_sha256]
        values = (self.excursion_norm, self.ramp_s, self.endpoint_dwell_s,
                  self.current_cap_ma, self.live_ceiling_s)
        if not all(type(v) in (int, float) and math.isfinite(v) for v in values):
            raise ValueError("selected experiment requires finite numeric conditions")
        if (not 0 < abs(self.excursion_norm) <= e["maximum_excursion_norm"]
                or self.ramp_s <= 0 or abs(self.excursion_norm) / self.ramp_s > e["maximum_slope_norm_per_s"]
                or not 0.3 <= self.endpoint_dwell_s <= e["maximum_endpoint_dwell_s"]
                or not 0 < self.current_cap_ma <= e["maximum_current_ma"]
                or self.nominal_trajectory_s > e["maximum_trajectory_s"]
                or not self.nominal_trajectory_s < self.live_ceiling_s <= e["maximum_live_s"]):
            raise ValueError("selected experiment exceeds its approved finite effort/trajectory envelope")
        if (type(self.minimum_response_raw) is not int or not 1 <= self.minimum_response_raw <= 4095
                or type(self.return_tolerance_raw) is not int or not 0 <= self.return_tolerance_raw <= 4095):
            raise ValueError("selected experiment requires bounded integer raw criteria")
        points = self.goal_readback_checkpoints_s
        if (type(points) is not tuple or len(points) > e["maximum_goal_readbacks"]
                or not all(type(v) in (int, float) and math.isfinite(v)
                           and 0 < v < self.nominal_trajectory_s for v in points)
                or any(b <= a for a, b in zip(points, points[1:]))):
            raise ValueError("selected experiment live goal checkpoints must be bounded and strictly ordered")
        object.__setattr__(self, "_sha256", hashlib.sha256(json.dumps(
            self.payload, sort_keys=True, allow_nan=False, separators=(",", ":"),
        ).encode()).hexdigest())
        object.__setattr__(self, "_response_rule", None if "response_window_s" not in e else MappingProxyType({
            "commanded_error_raw": e["response_commanded_error_raw"],
            "minimum_response_raw": e["response_minimum_raw"],
            "window_s": e["response_window_s"],
        }))

    @classmethod
    def from_payload(cls, value):
        keys = {"schema_version", "envelope_sha256", "excursion_norm", "ramp_s", "endpoint_dwell_s",
                "current_cap_ma", "live_ceiling_s", "minimum_response_raw", "return_tolerance_raw",
                "goal_readback_checkpoints_s"}
        if not isinstance(value, Mapping) or set(value) != keys:
            raise ValueError("exact selected experiment payload required")
        points = value["goal_readback_checkpoints_s"]
        if type(points) is not list:
            raise ValueError("selected experiment checkpoint JSON list required")
        return cls(**dict(value, goal_readback_checkpoints_s=tuple(points)))

    @property
    def payload(self):
        return {k: list(v) if k == "goal_readback_checkpoints_s" else v
                for k, v in self.__dict__.items() if not k.startswith("_")}

    @property
    def sha256(self):
        return self._sha256

    @property
    def response_rule(self):
        return self._response_rule

    @property
    def nominal_trajectory_s(self):
        return 6.0 + 2 * self.ramp_s + self.endpoint_dwell_s

    @property
    def live_s(self):
        return self.live_ceiling_s

    def summary(self):
        return dict(self.payload, sha256=self.sha256, nominal_trajectory_s=self.nominal_trajectory_s,
                    trajectory_s=self.trajectory_s)

    def criteria_valid(self, excursion_raw):
        return (type(excursion_raw) is int and excursion_raw > 0
                and math.ceil(excursion_raw / 2) <= self.minimum_response_raw <= excursion_raw
                and self.return_tolerance_raw <= math.floor(excursion_raw / 5))

    def phase(self, progress, acquisition_s=0):
        t = progress - acquisition_s
        if t < 0:
            return 0, "acquire", 0.0
        if t < 2:
            return 0, "baseline", 0.0
        if t < 2 + self.ramp_s:
            return 1, "outbound", (t - 2) / self.ramp_s
        if t < 2 + self.ramp_s + self.endpoint_dwell_s:
            return 1, "endpoint", 1.0
        end = 2 + 2 * self.ramp_s + self.endpoint_dwell_s
        if t < end:
            return 1, "return", 1 - (t - 2 - self.ramp_s - self.endpoint_dwell_s) / self.ramp_s
        return 1, "origin" if t < end + 2 else "final", 0.0

    def sample_key(self, progress, acquisition_s=0):
        _, phase, _ = self.phase(progress, acquisition_s)
        return ("baseline" if phase == "baseline" else "cycle1_endpoint" if phase == "endpoint"
                else "cycle1_origin" if progress >= acquisition_s + self.nominal_trajectory_s - .2 - 1e-9 else None)

    def context_valid(self, c):
        keys = {"profile", "progress_s", "phase", "cycle", "reference", "endpoint_target", "requested_target",
                "sample_key", "acquisition_s", "admitted_reference", "experiment_sha256"}
        if (not isinstance(c, dict) or set(c) != keys or c["profile"] != self.profile
                or c["experiment_sha256"] != self.sha256):
            return False
        if not all(type(c[k]) in (int, float) and math.isfinite(c[k]) for k in
                   ("progress_s", "reference", "endpoint_target", "requested_target", "acquisition_s", "admitted_reference")):
            return False
        t, q0, acquired, admitted = c["progress_s"], c["reference"], c["acquisition_s"], c["admitted_reference"]
        magnitude = abs(self.excursion_norm)
        if (acquired not in (0, 2) or not 0 <= t <= self.nominal_trajectory_s + acquired
                or not -100 <= q0 - magnitude <= q0 + magnitude <= 100
                or abs(admitted - q0) > 2 or acquired == 0 and admitted != q0):
            return False
        cycle, phase, fraction = self.phase(t, acquired)
        target = admitted + (q0 - admitted) * t / 2 if phase == "acquire" else q0 + self.excursion_norm * fraction
        return (type(c["cycle"]) is int and c["cycle"] == cycle and c["phase"] == phase
                and c["sample_key"] == self.sample_key(t, acquired)
                and math.isclose(c["endpoint_target"], q0 + self.excursion_norm, abs_tol=1e-9, rel_tol=0)
                and math.isclose(c["requested_target"], target, abs_tol=1e-9, rel_tol=0))

    def settings_valid(self, proof, run_id=None, host_incarnation=None):
        return PositiveElbowControl._settings_valid(
            proof, run_id, host_incarnation, recipe_name=self.recipe_name,
            current_cap_ma=self.current_cap_ma, experiment_sha256=self.sha256)


@dataclass(frozen=True)
class OriginalElbowReturn:
    """One approach to an archived original reference, never a new departure."""

    recipe_name: str = "physical-left-elbow-original-return"
    profile: str = "LeftElbowOriginalReturn"
    trajectory_s: float = 5.5
    live_s: float = 60.0
    joint: str = "arm_left_elbow_flex.pos"

    encoded_goal = staticmethod(SignedElbowResponse.encoded_goal)
    return_limit = staticmethod(SignedElbowResponse.return_limit)

    def validate_artifact(self, value):
        return self._validate_artifact(value)

    def _validate_artifact(self, value, *, allow_source_transition=False):
        heads = ("original_motor_head", "original_executor_head", "stopped_motor_head",
                 "stopped_executor_head", "admission_motor_head")
        hashes = ("original_manifest_sha256", "original_log_sha256", "original_terminal_sha256",
                  "stopped_snapshot_sha256", "stopped_manifest_sha256")
        integers = ("reference_raw", "baseline_noise_raw", "stopped_position_raw", "stopped_goal_raw",
                    "stopped_torque_enable", "stopped_current_raw", "stopped_mode", "stopped_status",
                    "model_number", "firmware_major", "firmware_minor")
        uuids = ("original_run_id", "original_host_incarnation", "stopped_operation_id")
        keys = {"version", "reference_normalized", "motor", "calibration", *heads, *hashes, *integers, *uuids}
        if not isinstance(value, dict) or set(value) != keys or type(value["version"]) is not int or value["version"] != 1:
            raise ValueError("exact archived elbow-return reference schema required")
        for fields, size in ((heads, 40), (hashes, 64)):
            if any(type(value[k]) is not str or len(value[k]) != size or any(c not in "0123456789abcdef" for c in value[k])
                   for k in fields):
                raise ValueError("archived elbow-return source hashes unavailable")
        for key in uuids:
            if type(value[key]) is not str or uuid_text(value[key]) != value[key]:
                raise ValueError("archived elbow-return identity unavailable")
        if any(type(value[k]) is not int for k in integers):
            raise ValueError("archived elbow-return raw proof must contain integers")
        if value["motor"] != {"bus": "left", "name": self.joint[:-4], "id": 3, "model": "sts3215"}:
            raise ValueError("archived elbow-return motor identity differs")
        calibration = value["calibration"]
        if not isinstance(calibration, dict) or set(calibration) != {"drive_mode", "homing_offset", "range_min", "range_max"}:
            raise ValueError("exact archived elbow-return calibration required")
        reference = value["reference_normalized"]
        raw = self.encoded_goal(reference, calibration)
        if raw is None or raw != value["reference_raw"] or value["baseline_noise_raw"] != 0:
            raise ValueError("archived elbow-return original encoded reference differs")
        normalized = (raw - calibration["range_min"]) / (calibration["range_max"] - calibration["range_min"]) * 200 - 100
        if not math.isclose(reference, normalized, abs_tol=1e-9, rel_tol=0):
            raise ValueError("archived elbow-return original normalized reference differs")
        start = value["stopped_position_raw"]
        distance = (raw - start) / (calibration["range_max"] - calibration["range_min"]) * 200
        if not calibration["range_min"] <= start < raw or not 0 < distance <= 3:
            raise ValueError("archived elbow-return would change direction or escalate excursion")
        if (
            value["stopped_goal_raw"] != raw
            or not allow_source_transition and value["original_motor_head"] != value["stopped_motor_head"]
            or any(value[k] != 0 for k in ("stopped_torque_enable", "stopped_current_raw", "stopped_mode", "stopped_status"))
            or value["model_number"] != 777 or not 0 <= value["firmware_major"] <= 255
            or not 0 <= value["firmware_minor"] <= 255
        ):
            raise ValueError("archived elbow-return stopped proof differs")
        return value

    def phase(self, progress):
        if progress < 2:
            return 0, "baseline", 0.0
        if progress < 5:
            return 1, "return", (progress - 2) / 3
        return 1, "origin", 1.0

    def sample_key(self, progress):
        return "baseline" if progress < 2 else "cycle1_origin" if progress >= 5.3 - 1e-9 else None

    def context_valid(self, context):
        if not isinstance(context, dict) or context.get("profile") != self.profile:
            return False
        values = [context.get(k) for k in ("progress_s", "reference", "endpoint_target", "requested_target")]
        if not all(type(v) in (int, float) and math.isfinite(v) for v in values):
            return False
        progress, reference, start, requested = values
        if not 0 <= progress <= self.trajectory_s or not -100 <= start < reference <= 100 or reference - start > 3:
            return False
        cycle, phase, fraction = self.phase(progress)
        return (
            type(context.get("cycle")) is int and context["cycle"] == cycle
            and context.get("phase") == phase and context.get("sample_key") == self.sample_key(progress)
            and math.isclose(requested, start + fraction * (reference - start), abs_tol=1e-9, rel_tol=0)
        )

    def completion_expectation(self, execution, reference, artifact_sha256):
        if not isinstance(execution, dict) or not isinstance(reference, dict):
            return None
        try:
            self.validate_artifact(reference)
        except (ValueError, TypeError):
            return None
        if type(artifact_sha256) is not str or len(artifact_sha256) != 64 or any(c not in "0123456789abcdef" for c in artifact_sha256):
            return None
        outcome = execution.get("retained_return")
        seed = execution.get("original_seed")
        if not isinstance(outcome, dict) or not isinstance(seed, dict):
            return None
        expected = {
            "artifact_sha256": artifact_sha256, "original_run_id": reference["original_run_id"],
            "reference_raw": reference["reference_raw"], "reference_normalized": reference["reference_normalized"],
            "baseline_noise_raw": reference["baseline_noise_raw"], "calibration": reference["calibration"],
        }
        residual = outcome.get("final_return_residual_raw")
        window = outcome.get("final_window")
        clock_keys = {f"{end}_{name}_{part}_at" for end in ("first", "last")
                      for name in ("current", "position") for part in ("started", "completed")}
        if not isinstance(window, dict) or set(window) != clock_keys | {
            "sample_count", "qualified_at_monotonic_s", "current_span_s", "position_span_s",
        } or type(window["sample_count"]) is not int or not 3 <= window["sample_count"] <= 20:
            return None
        if not all(type(window[k]) in (int, float) and math.isfinite(window[k]) for k in set(window) - {"sample_count"}):
            return None
        for name in ("current", "position"):
            first, last = window[f"first_{name}_completed_at"], window[f"last_{name}_completed_at"]
            if (not 0 <= window[f"first_{name}_started_at"] <= first < last <= window["qualified_at_monotonic_s"]
                or not window[f"first_{name}_started_at"] <= window[f"last_{name}_started_at"] <= last
                or window["qualified_at_monotonic_s"] - window[f"first_{name}_started_at"] > .5
                or window["qualified_at_monotonic_s"] - window[f"last_{name}_started_at"] > .5
                or last - first < .2 - 1e-9
                or not math.isclose(window[f"{name}_span_s"], last - first, rel_tol=0, abs_tol=1e-9)):
                return None
        if (
            any(outcome.get(k) != v for k, v in expected.items())
            or type(residual) is not int or not 0 <= residual <= self.return_limit(reference["baseline_noise_raw"])
            or self.encoded_goal(seed.get(self.joint), reference["calibration"]) != outcome.get("starting_raw")
            or type(outcome.get("starting_raw")) is not int
            or abs(outcome["starting_raw"] - reference["stopped_position_raw"]) > 2
            or execution.get("progress_s") != self.trajectory_s
            or type(outcome.get("origin_goal_raw")) is not int or outcome["origin_goal_raw"] != reference["reference_raw"]
            or type(outcome.get("origin_present_raw")) is not int or abs(outcome["origin_present_raw"] - reference["reference_raw"]) > 2
            or type(execution.get("cycles_completed")) is not int or execution["cycles_completed"] != 0
            or type(execution.get("returns_qualified")) is not int or execution["returns_qualified"] != 1
        ):
            return None
        return self.trajectory_s, 0, 1


LEFT_ELBOW_ORIGINAL_RETURN = OriginalElbowReturn()


@dataclass(frozen=True)
class ElbowRecovery(OriginalElbowReturn):
    """Explicit current-pose hypothesis; historical return verdict stays intact."""

    recipe_name: str = "physical-left-elbow-recovery-i0"
    profile: str = "LeftElbowRecoveryI0"
    integral: int = 0
    mode: str = "return"
    hold_s: float = 0.5
    current_cap_ma: float = 100.0

    def validate_artifact(self, value):
        # Only the new experiment permits intervening reviewed motor source.
        LEFT_ELBOW_ORIGINAL_RETURN._validate_artifact(value, allow_source_transition=True)
        if (value["firmware_major"], value["firmware_minor"]) != (3, 10):
            raise ValueError("current-pose recovery requires the installed firmware 3.10")
        return value

    def phase(self, progress):
        if self.mode == "return":
            return super().phase(progress)
        if progress < 2:
            return 0, "baseline", 0.0
        if progress >= 16:
            return 2, "final", 0.0
        cycle = min(int((progress - 2) // 7) + 1, 2)
        within = progress - 2 - (cycle - 1) * 7
        if within < 3:
            return cycle, "outbound", within / 3
        if within < 3.5:
            return cycle, "endpoint", 1.0
        if within < 6.5:
            return cycle, "return", 1 - (within - 3.5) / 3
        return cycle, "origin", 0.0

    def sample_key(self, progress):
        cycle, phase, _ = self.phase(progress)
        if phase == "baseline":
            return "baseline"
        if self.mode == "return":
            return "cycle1_origin" if progress >= self.trajectory_s - 0.2 - 1e-9 else None
        if phase == "endpoint":
            return f"cycle{cycle}_endpoint"
        if phase == "origin" and progress >= (9 if cycle == 1 else 16) - 0.2 - 1e-9:
            return f"cycle{cycle}_origin"
        return "final_origin" if phase == "final" and progress >= 17.8 - 1e-9 else None

    def context_valid(self, context):
        if self.mode == "return":
            return super().context_valid(context)
        if not isinstance(context, dict) or context.get("profile") != self.profile:
            return False
        values = [context.get(k) for k in ("progress_s", "reference", "endpoint_target", "requested_target")]
        if not all(type(v) in (int, float) and math.isfinite(v) for v in values):
            return False
        progress, reference, endpoint, requested = values
        if not 0 <= progress <= self.trajectory_s or not -100 <= reference < reference + 3 <= 100:
            return False
        cycle, phase, fraction = self.phase(progress)
        return (
            type(context.get("cycle")) is int
            and context["cycle"] == cycle
            and context.get("phase") == phase
            and context.get("sample_key") == self.sample_key(progress)
            and math.isclose(endpoint, reference + 3, abs_tol=1e-9, rel_tol=0)
            and math.isclose(requested, reference + fraction * 3, abs_tol=1e-9, rel_tol=0)
        )

    @staticmethod
    def window_valid(window):
        clocks = {
            f"{end}_{name}_{part}_at"
            for end in ("first", "last")
            for name in ("current", "position")
            for part in ("started", "completed")
        }
        if (
            not isinstance(window, dict)
            or set(window)
            != clocks
            | {
                "sample_count",
                "qualified_at_monotonic_s",
                "current_span_s",
                "position_span_s",
            }
            or type(window["sample_count"]) is not int
            or not 3 <= window["sample_count"] <= 20
            or not all(
                type(window[k]) in (int, float) and math.isfinite(window[k])
                for k in set(window) - {"sample_count"}
            )
        ):
            return False
        for name in ("current", "position"):
            first, last = window[f"first_{name}_completed_at"], window[f"last_{name}_completed_at"]
            start, last_start = window[f"first_{name}_started_at"], window[f"last_{name}_started_at"]
            qualified = window["qualified_at_monotonic_s"]
            if (
                not 0 <= start <= first < last <= qualified
                or not start <= last_start <= last
                or qualified - start > 0.5
                or qualified - last_start > 0.5
                or last - first < 0.2 - 1e-9
                or not math.isclose(window[f"{name}_span_s"], last - first, rel_tol=0, abs_tol=1e-9)
            ):
                return False
        return True

    def gain_trial_valid(self, gain, *, run_id, host_incarnation, now=None):
        if not isinstance(gain, dict) or gain.get("active_verified") is not True:
            return False
        if gain.get("state", gain.get("status")) != "active" or gain.get("status", "active") != "active":
            return False
        at, binding, registers = (
            gain.get("active_verified_at_monotonic_s"),
            gain.get("binding"),
            gain.get("active_registers"),
        )
        expected_binding = {
            "run_id": run_id,
            "host_incarnation": host_incarnation,
            "recipe": self.recipe_name,
            "integral": self.integral,
        }
        if (
            type(at) not in (int, float)
            or not math.isfinite(at)
            or at < 0
            or now is not None
            and (type(now) not in (int, float) or not math.isfinite(now) or at > now)
            or not isinstance(binding, dict)
            or binding != expected_binding
            or not all(type(binding.get(k)) is str and binding[k] for k in ("run_id", "host_incarnation"))
            or type(binding.get("integral")) is not int
            or type(gain.get("integral")) is not int
            or gain["integral"] != self.integral
            or not isinstance(registers, dict)
        ):
            return False
        expected = {
            "Model_Number": 777,
            "Firmware_Major_Version": 3,
            "Firmware_Minor_Version": 10,
            "Operating_Mode": 0,
            "P_Coefficient": 16,
            "D_Coefficient": 32,
            "I_Coefficient": self.integral,
            "Lock": 1,
            "Torque_Enable": 1,
        }
        if any(type(registers.get(k)) is not int or registers[k] != v for k, v in expected.items()):
            return False
        if (
            not all(
                type(registers.get(k)) is int
                for k in ("Min_Position_Limit", "Max_Position_Limit", "Present_Current")
            )
            or not 0 <= registers["Min_Position_Limit"] < registers["Max_Position_Limit"] <= 4095
            or abs(registers["Present_Current"]) * 6.5 >= self.current_cap_ma
        ):
            return False
        original = gain.get("original_registers")
        if (
            not isinstance(original, dict)
            or any(
                type(original.get(k)) is not int or original[k] != v
                for k, v in {
                    "I_Coefficient": 0,
                    "P_Coefficient": 16,
                    "D_Coefficient": 32,
                    "Torque_Enable": 0,
                }.items()
            )
            or type(original.get("Lock")) is not int
            or original["Lock"] not in (0, 1)
        ):
            return False
        before, active = gain.get("original_bytes"), gain.get("active_bytes")
        return (
            isinstance(before, dict)
            and set(before) == {"24", "25"}
            and all(type(v) is int and 0 <= v <= 255 for v in before.values())
            and isinstance(active, dict)
            and set(active) == set(before)
            and all(type(v) is int for v in active.values())
            and active == before
        )

    def completion_expectation(self, execution, reference, artifact_sha256):
        if not isinstance(execution, dict):
            return None
        try:
            self.validate_artifact(reference)
        except (ValueError, TypeError, KeyError):
            return None
        if (
            type(artifact_sha256) is not str
            or len(artifact_sha256) != 64
            or any(c not in "0123456789abcdef" for c in artifact_sha256)
        ):
            return None
        outcome, seed = execution.get("recovery"), execution.get("original_seed")
        if (
            not isinstance(outcome, dict)
            or not isinstance(seed, dict)
            or not self.window_valid(outcome.get("final_window"))
        ):
            return None
        gain = outcome.get("gain_trial")
        binding = gain.get("binding", {}) if isinstance(gain, dict) else {}
        if not self.gain_trial_valid(
            gain,
            run_id=binding.get("run_id"),
            host_incarnation=binding.get("host_incarnation"),
            now=outcome["final_window"]["qualified_at_monotonic_s"],
        ):
            return None
        starting = self.encoded_goal(seed.get(self.joint), reference["calibration"])
        expected = {
            "recipe": self.recipe_name,
            "profile": self.profile,
            "mode": self.mode,
            "expected_integral": self.integral,
            "hold_s": self.hold_s,
            "current_cap_ma": self.current_cap_ma,
            "artifact_sha256": artifact_sha256,
            "original_run_id": reference["original_run_id"],
            "historical_target_raw": reference["reference_raw"],
            "calibration": reference["calibration"],
            "starting_raw": starting,
        }
        residual, noise, progress = (
            outcome.get(k)
            for k in ("final_return_residual_raw", "baseline_noise_raw", "max_toward_target_raw")
        )
        if (
            starting is None
            or any(outcome.get(k) != v for k, v in expected.items())
            or type(outcome.get("expected_integral")) is not int
            or type(outcome.get("starting_raw")) is not int
            or any(type(v) is not int or not 0 <= v <= 4095 for v in (residual, noise, progress))
            or type(outcome.get("target_attained")) is not bool
        ):
            return None
        registers = gain["active_registers"]
        if (registers["Min_Position_Limit"], registers["Max_Position_Limit"]) != (
            reference["calibration"]["range_min"],
            reference["calibration"]["range_max"],
        ):
            return None
        target = starting if self.mode == "confirm" else reference["reference_raw"]
        target_normalized = seed[self.joint] if self.mode == "confirm" else reference["reference_normalized"]
        present = outcome.get("origin_present_raw")
        limit = self.return_limit(noise) if self.mode == "confirm" else 2
        attained = residual <= limit and type(present) is int and abs(present - target) <= limit
        upper = (
            self.encoded_goal(target_normalized + 3, reference["calibration"])
            if self.mode == "confirm"
            else target
        )
        if upper is None or progress > upper - starting + limit or residual > upper - starting + limit:
            return None
        if (
            outcome.get("reference_raw") != target
            or outcome.get("reference_normalized") != target_normalized
            or type(outcome.get("reference_raw")) is not int
            or type(outcome.get("origin_goal_raw")) is not int
            or outcome["origin_goal_raw"] != target
            or type(present) is not int
            or not reference["calibration"]["range_min"] <= present <= reference["calibration"]["range_max"]
            or not starting - limit <= present <= upper + limit
            or outcome["target_attained"] != attained
        ):
            return None
        cycles = outcome.get("cycles")
        if not isinstance(cycles, list) or len(cycles) != (2 if self.mode == "confirm" else 0):
            return None
        if self.mode == "confirm":
            if not attained or not -100 <= target_normalized < target_normalized + 3 <= 100:
                return None
            goal = self.encoded_goal(target_normalized + 3, reference["calibration"])
            threshold = LEFT_ELBOW_RESPONSE.response_threshold(goal - starting, noise)
            for index, row in enumerate(cycles, 1):
                if (
                    not isinstance(row, dict)
                    or type(row.get("cycle")) is not int
                    or row["cycle"] != index
                    or type(row.get("endpoint_goal_raw")) is not int
                    or row["endpoint_goal_raw"] != goal
                    or type(row.get("signed_response_raw")) is not int
                    or row["signed_response_raw"] < threshold
                    or row.get("response_threshold_raw") != threshold
                    or row.get("return_qualified") is not True
                    or type(row.get("return_residual_raw")) is not int
                    or not 0 <= row["return_residual_raw"] <= limit
                ):
                    return None
        elif not 0 < target_normalized - seed[self.joint] <= 3:
            return None
        counts = len(cycles), len(cycles) if self.mode == "confirm" else int(attained)
        if execution.get("progress_s") != self.trajectory_s or any(
            type(execution.get(k)) is not int or execution[k] != v
            for k, v in zip(("cycles_completed", "returns_qualified"), counts, strict=True)
        ):
            return None
        return self.trajectory_s, *counts


ELBOW_RECOVERY_DEFINITIONS = MappingProxyType(
    {
        f"physical-left-elbow-recovery-i{integral}{suffix}": ElbowRecovery(
            recipe_name=f"physical-left-elbow-recovery-i{integral}{suffix}",
            profile=f"LeftElbowRecoveryI{integral}{label}",
            integral=integral,
            mode="confirm" if suffix == "-confirm" else "return",
            hold_s=2.0 if suffix == "-dwell" else 0.5,
            trajectory_s=18.0 if suffix == "-confirm" else 7.0 if suffix == "-dwell" else 5.5,
        )
        for integral in (0, 1)
        for suffix, label in (("", ""), ("-dwell", "Dwell"), ("-confirm", "Confirm"))
    }
)


RECIPES = MappingProxyType(
    {
        "fake-finite": Recipe("fake-finite", "finite", 352.0, 420.0),
        "fake-finite-short": Recipe("fake-finite-short", "finite", 1.0, 12.0),
        "sim-arm-smoke": Recipe("sim-arm-smoke", "finite", 88.0, 180.0),
        "sim-arm-smoke-repeat": Recipe("sim-arm-smoke-repeat", "finite", 352.0, 420.0),
        "sim-arm-hold-body": Recipe("sim-arm-hold-body", "finite", 12.0, 30.0),
        "physical-arm-smoke": Recipe("physical-arm-smoke", "finite", 88.0, 180.0, seed=None),
        "physical-arm-smoke-repeat": Recipe("physical-arm-smoke-repeat", "finite", 352.0, 420.0, seed=None),
        "physical-arm-hold-body": Recipe("physical-arm-hold-body", "finite", 12.0, 30.0, seed=None),
        "physical-left-elbow-compare": Recipe("physical-left-elbow-compare", "finite", 18.0, 60.0, seed=None),
        "physical-left-elbow-dwell-compare": Recipe(
            "physical-left-elbow-dwell-compare", "finite", 24.0, 60.0, seed=None
        ),
        LEFT_ELBOW_POSITIVE_CONTROL.recipe_name: Recipe(
            LEFT_ELBOW_POSITIVE_CONTROL.recipe_name, "finite", 30.0, 60.0, seed=None,
        ),
        SelectedElbowExperiment.recipe_name: Recipe(
            SelectedElbowExperiment.recipe_name, "finite", 30.0, 60.0, seed=None,
        ),
        LEFT_ELBOW_RESPONSE.recipe_name: Recipe(
            LEFT_ELBOW_RESPONSE.recipe_name, "finite", LEFT_ELBOW_RESPONSE.trajectory_s,
            LEFT_ELBOW_RESPONSE.live_s, seed=None,
        ),
        LEFT_ELBOW_ORIGINAL_RETURN.recipe_name: Recipe(
            LEFT_ELBOW_ORIGINAL_RETURN.recipe_name, "finite", LEFT_ELBOW_ORIGINAL_RETURN.trajectory_s,
            LEFT_ELBOW_ORIGINAL_RETURN.live_s, seed=None,
        ),
        **{name: Recipe(name, "finite", definition.trajectory_s, definition.live_s, seed=None)
           for name, definition in ELBOW_RECOVERY_DEFINITIONS.items()},
        "fake-interactive": Recipe("fake-interactive", "interactive", None, 420.0),
    }
)


def uuid_text(value):
    if not isinstance(value, str):
        raise ValueError("UUID string required")
    return str(uuid.UUID(value))
