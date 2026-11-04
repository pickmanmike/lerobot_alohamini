"""Existing finite coordinator planning and acknowledged phase pacing, without devices."""

import copy
import json

import pytest

from examples.alohamini.am1_live_teleop import CURRENT_REST_WORKSPACE_PROFILES, normalized_raw
from tests.robots.test_am1_leader_adapter import held_command119_client
from tests.robots.test_am1_live_teleop_contract import workspace
from tools import am1_live_phase_coordinator as coordinator
from tools.am1_leader_adapter import VirtualIntentReader

KEY = "arm_left_elbow_flex.pos"
GRIP = "arm_left_gripper.pos"


def admission(grip="closing", *, elbow_raw=1090, gripper_raw=None, gripper_interval=None):
    client, _ = held_command119_client()
    native = workspace()
    native["workspace_profile"] = CURRENT_REST_WORKSPACE_PROFILES["ordinary-arms"]
    native["calibration"][KEY[:-4]] = [3, 0, 1845, 864, 3073]
    native["raw_bounds"][KEY] = [1051, 1164]
    native["bounds"][KEY].update(
        min=normalized_raw(1051, native["calibration"][KEY[:-4]]),
        max=normalized_raw(1164, native["calibration"][KEY[:-4]]),
        rate_per_s=3.,
    )
    native["reference"][KEY] = normalized_raw(elbow_raw, native["calibration"][KEY[:-4]])
    native["bounds"][GRIP]["rate_per_s"] = 20.
    native["reference"][GRIP] = 10. if grip in {"closing", "ambiguous"} else 0.
    if grip == "ambiguous":
        native["raw_bounds"][GRIP] = [1000, 1400]
        native["bounds"][GRIP]["max"] = 20.
    if gripper_raw is not None:
        cal = [6, 0, 933, 1886, 3401]
        native["calibration"][GRIP[:-4]] = cal
        native["raw_bounds"][GRIP] = list(gripper_interval or [2799, 3102])
        native["bounds"][GRIP].update(min=normalized_raw(native["raw_bounds"][GRIP][0], cal, gripper=True),
                                     max=normalized_raw(native["raw_bounds"][GRIP][1], cal, gripper=True))
        native["reference"][GRIP] = normalized_raw(gripper_raw, cal, gripper=True)
    live = client.snapshot["run"]["live_input"]
    live.update(workspace=native, reference=copy.deepcopy(native["reference"]),
                cursor=dict(native["reference"]), accepted_target=dict(native["reference"]))
    client.snapshot["evidence"]["accepted_host_action"]["arm_feedback"] = {
        "positions": {KEY: {"raw": elbow_raw, "normalized": native["reference"][KEY]}}
    }
    ready = {"event": "virtual_input_ready", "run_id": client.snapshot["run"]["run_id"],
             "bounds": copy.deepcopy(native["bounds"]), "units": dict(native["units"]),
             "command_reference": dict(native["reference"])}
    return ready, {"event": "native_admitted", "snapshot": client.snapshot}


def grasp_options(*extra, direction="increasing"):
    return coordinator.parse_args([
        "--plan", "grasp-return", "--gripper-direction", "admitted",
        "--degrees", "4", *(["--direction", direction] if direction is not None else []),
        "--ramp-seconds", "10", "--comparison-dwell-seconds", "3", "--dwell-seconds", "3",
        "--window-seconds", "90", "--intent-ttl-seconds", "60",
        "--bindings", "unused-current-bindings.json", *extra,
    ])


def planned(ready, admitted, options):
    plan = coordinator.fresh_target(
        ready, admitted, degrees=options.degrees, direction=options.direction,
        ramp_seconds=options.ramp_seconds,
    )
    return plan, coordinator.phases_for(plan, options, admitted=admitted)


def extended_options(*extra, plan="gripper-close-return"):
    return coordinator.parse_args([
        "--plan", plan, "--extended-grasp-closing", "--gripper-direction", "admitted",
        "--gripper-closed-opening", "50", "--direction", "decreasing",
        "--comparison-dwell-seconds", "3", "--bindings", "unused-current-bindings.json", *extra,
    ])



def opening_options(*extra):
    return coordinator.parse_args([
        "--plan", "gripper-open-finish", "--extended-grasp-closing", "--gripper-direction", "admitted",
        "--gripper-open-opening", "68", "--bindings", "unused-current-bindings.json", *extra,
    ])


@pytest.mark.parametrize("declared_ramp", [3., 12.])
def test_opening_only_uses_fresh_absolute_endpoint_slow_ramp_and_finishes_without_return(declared_ramp):
    ready, admitted = admission(elbow_raw=1150, gripper_raw=2347, gripper_interval=[1886, 3102])
    original = copy.deepcopy((ready, admitted))
    options = opening_options("--ramp-seconds", str(declared_ramp))
    plan = coordinator.fresh_target(ready, admitted, degrees=0., direction=options.direction,
                                   ramp_seconds=options.ramp_seconds)
    phases = coordinator.phases_for(plan, options, admitted=admitted)
    assert [phase["name"] for phase in phases] == ["gripper_open", "finish"]
    opening = phases[0]
    assert opening["targets"] == {KEY: ready["command_reference"][KEY], GRIP: 68.}
    assert opening["gripper_direction"] == "opening"
    assert opening["gripper_interval"] == {"origin": ready["command_reference"][GRIP], "open": 68., "opening_only": True}
    assert opening["ramp"] == pytest.approx(max(declared_ramp, 11.27128712871287))
    assert (68.-ready["command_reference"][GRIP])/opening["ramp"] <= 10./3. + 1e-12
    assert options.dwell_seconds == opening["dwell"] == 2.
    assert opening["complete_before_advance"] is True
    assert phases[-1] == {"name": "finish", "op": "finish"}
    assert plan["origin"] == ready["command_reference"] and (ready, admitted) == original


@pytest.mark.parametrize("target", ["-1", "101", "nan", "inf", "origin", "20", "81"])
def test_opening_only_refuses_invalid_nonopening_or_unadmitted_absolute_endpoint(target):
    ready, admitted = admission(gripper_raw=2347, gripper_interval=[1886, 3102])
    original = copy.deepcopy((ready, admitted))
    try:
        options = opening_options("--gripper-open-opening", str(ready["command_reference"][GRIP]) if target == "origin" else target)
    except (SystemExit, ValueError):
        return
    plan = coordinator.fresh_target(ready, admitted, degrees=0., direction=options.direction, ramp_seconds=10.)
    with pytest.raises(ValueError):
        coordinator.phases_for(plan, options, admitted=admitted)
    assert (ready, admitted) == original


@pytest.mark.parametrize("extra", [
    ["--gripper-closed-opening", "20"], ["--plan", "gripper-close-return"],
    ["--gripper-direction", "opening"], ["--gripper-cycle"], ["--dwell-seconds", "3"],
])
def test_opening_only_cli_refuses_incompatible_selection(extra):
    with pytest.raises(SystemExit):
        opening_options(*extra)


def test_opening_only_requires_explicit_endpoint_extended_scope_and_exact_admission():
    for args in (["--plan", "gripper-open-finish"],
                 ["--plan", "gripper-open-finish", "--gripper-open-opening", "68"],
                 ["--plan", "out-return", "--gripper-open-opening", "68"]):
        with pytest.raises(SystemExit):
            coordinator.parse_args(["--bindings", "unused", *args])
    ready, admitted = admission(gripper_raw=2957, gripper_interval=[2799, 3102])
    plan = coordinator.fresh_target(ready, admitted, degrees=0., direction="decreasing", ramp_seconds=10.)
    with pytest.raises(ValueError):
        coordinator.phases_for(plan, opening_options("--gripper-open-opening", "75"), admitted=admitted)


def test_opening_only_actual_coordinator_dispatches_fresh_finish_after_acknowledged_active_dwell(tmp_path, monkeypatch):
    ready, admitted = admission(elbow_raw=1150, gripper_raw=2347, gripper_interval=[1886, 3102])
    original = copy.deepcopy((ready, admitted))
    root, trial = tmp_path / "repo", tmp_path / "repo/trial"
    trial.mkdir(parents=True)
    (root / ".cache/am1-knowledge-reuse-18").mkdir(parents=True)
    (trial / "frames.jsonl").write_text(json.dumps(admitted) + "\n", encoding="utf-8")
    now, commands, launches = [100.], [], []
    reader = VirtualIntentReader("interactive", clock=lambda: now[0],
        intent_channel="11111111-1111-4111-8111-111111111111")

    def status(seq, elapsed, progress=0., state="current"):
        return dict(event="virtual_input_status", window_elapsed_s=elapsed, window_remaining_s=60.-elapsed,
                    current_accepted_intent_seq=seq, input_state=state, ramp_progress_active_s=progress)

    class Input:
        def write(self, command):
            commands.append(command)
            reader.receive_line(command)
        def flush(self):
            pass
        def close(self):
            pass

    class Process:
        def __init__(self, args, **kwargs):
            launches.append((args, kwargs))
            declaration = json.loads(next((root / ".cache/am1-knowledge-reuse-18").glob("*.console.jsonl")).read_text().splitlines()[0])
            assert declaration["gripper_open_opening"] == 68.
            assert "gripper_closed_opening" not in declaration
            assert declaration["gripper_activity"] == "opening only; Finish without closed-reference return"
            assert declaration["gripper_interval_policy"] == "explicit absolute opening endpoint; no closing substitute"
            assert declaration["selected_gripper_raw_interval"] == [1886, 3102]
            assert declaration["gripper_direction"] == "opening" and declaration["gripper_cycle"] is False
            assert declaration["degrees"] is declaration["direction"] is None
            assert declaration["elbow_excursion_degrees"] == 0.
            assert declaration["view"] == "normal required Chest" and declaration["body_lift_intent"] == "zero"
            assert declaration["other_joints"] == "fresh admitted holds" and declaration["dwell_seconds"] == 2.
            self.stdin, self.stdout, self.returncode = Input(), self.output(), None
        def output(self):
            yield json.dumps(dict(event="virtual_input_opened", output=str(trial))) + "\n"
            yield json.dumps(ready) + "\n"
            yield json.dumps(status(None, 0., state="waiting")) + "\n"
            opening = reader.take()
            assert opening["op"] == "move" and opening["targets"] == {KEY: ready["command_reference"][KEY], GRIP: 68.}
            assert opening["transition_seconds"] == 12.
            assert opening["issued_at_perf_counter_s"] == 100. and opening["expires_at_perf_counter_s"] == 145.
            yield json.dumps(dict(event="virtual_intent_accepted", op="move", seq=opening["seq"])) + "\n"
            yield json.dumps(status(None, 1., 14.)) + "\n"
            assert len(commands) == 1, "foreign acceptance must not advance"
            yield json.dumps(status(opening["seq"], 2., 14., state="waiting")) + "\n"
            assert len(commands) == 1, "held progress must not advance"
            yield json.dumps(status(opening["seq"], 13.9, 13.9)) + "\n"
            assert len(commands) == 1, "incomplete dwell must not advance"
            now[0] = 114.
            yield json.dumps(status(opening["seq"], 14., 14.)) + "\n"
            finish = reader.take()
            assert finish["op"] == "finish" and finish["seq"] == opening["seq"]+1
            assert finish["issued_at_perf_counter_s"] == 114. and finish["expires_at_perf_counter_s"] == 115.
            yield json.dumps(dict(event="virtual_intent_accepted", op="finish", seq=finish["seq"])) + "\n"
            yield json.dumps(status(finish["seq"], 14., state="finished")) + "\n"
            self.returncode = 0
        def poll(self):
            return self.returncode
        def wait(self):
            return 0

    monkeypatch.setattr(coordinator, "ROOT", root)
    monkeypatch.setattr(coordinator.subprocess, "Popen", Process)
    assert coordinator.main(["--plan", "gripper-open-finish", "--extended-grasp-closing", "--gripper-open-opening", "68",
        "--gripper-direction", "admitted", "--ramp-seconds", "12", "--window-seconds", "60",
        "--intent-ttl-seconds", "45", "--bindings", str(root / "bindings.json")]) == 0
    assert len(commands) == 2 and commands[0].startswith("move 12.0 45.0 ") and commands[1] == "finish\n"
    argv, kwargs = launches[0]
    assert kwargs["cwd"] == root and "--prepare-current-rest" in argv and "--extended-grasp-closing" in argv
    assert argv[argv.index("--bindings")+1] == str(root / "bindings.json")
    assert argv[argv.index("--duration-seconds")+1] == "60.0"
    records = [json.loads(line) for line in next((root / ".cache/am1-knowledge-reuse-18").glob("*.console.jsonl")).read_text().splitlines()]
    fresh = next(row for row in records if row["event"] == "local_fresh_targets")
    assert fresh["planned_active_seconds"] == 14. and fresh["gripper_target"] == 68.
    assert fresh["gripper_interval"] == {"origin": ready["command_reference"][GRIP], "open": 68., "opening_only": True}
    assert not any(row["event"] == "local_excursion_unsupported" for row in records)
    assert (ready, admitted) == original

@pytest.mark.parametrize("name,elbow_raw", [("gripper-close-return", 1150), ("grasp-return", 1128)])
def test_extended_closure_uses_explicit_absolute_target_slow_rate_and_original_return(name, elbow_raw):
    ready, admitted = admission(elbow_raw=elbow_raw, gripper_raw=2957, gripper_interval=[1886, 3102])
    original = copy.deepcopy(admitted)
    options = extended_options(plan=name)
    plan = coordinator.fresh_target(ready, admitted, degrees=0. if name == "gripper-close-return" else 4.,
                                   direction="decreasing", ramp_seconds=10.)
    phases = coordinator.phases_for(plan, options, admitted=admitted)
    names = [phase["name"] for phase in phases]
    assert names == (["grasp_close", "grasp_release", "finish"] if name == "gripper-close-return" else
                     ["grasp_close", "grasp_carry", "grasp_return_original_closed", "grasp_release", "finish"])
    g0, q0 = plan["origin"][GRIP], plan["origin"][KEY]
    assert phases[0]["targets"] == {KEY: q0, GRIP: 50.}
    assert phases[-2]["targets"] == {KEY: q0, GRIP: g0}
    for phase in (phases[0], phases[-2]):
        assert phase["ramp"] == pytest.approx((g0-50.)*3./10.) and phase["ramp"] >= 3.
        assert (g0-50.)/phase["ramp"] <= 10./3. + 1e-12
    if name == "gripper-close-return":
        assert all(phase["targets"][KEY] == q0 for phase in phases[:-1])
    else:
        assert phases[1]["ramp"] == phases[2]["ramp"] == 10.
        assert phases[1]["dwell"] == phases[2]["dwell"] == 3.
        assert phases[2]["targets"] == {KEY: q0, GRIP: 50.}
    assert all(phase.get("complete_before_advance") for phase in phases[:-1])
    assert admitted == original and phases[-1] == {"name": "finish", "op": "finish"}


@pytest.mark.parametrize("target", ["-1", "101", "nan", "inf", "70.6930693069307", "75"])
def test_extended_closure_refuses_invalid_or_nonclosing_endpoint(target):
    ready, admitted = admission(gripper_raw=2957, gripper_interval=[1886, 3102])
    try:
        options = extended_options("--gripper-closed-opening", target)
    except (SystemExit, ValueError):
        return
    plan = coordinator.fresh_target(ready, admitted, degrees=0., direction="decreasing", ramp_seconds=10.)
    with pytest.raises(ValueError):
        coordinator.phases_for(plan, options, admitted=admitted)


@pytest.mark.parametrize("extra", [
    ["--supported-grasp-closing"], ["--plan", "out-return"], ["--gripper-direction", "opening"],
])
def test_extended_closure_cli_refuses_incompatible_selection(extra):
    with pytest.raises(SystemExit):
        extended_options(*extra)


def test_absolute_closing_target_requires_extended_selection_and_matching_native_interval():
    with pytest.raises(SystemExit):
        grasp_options("--gripper-closed-opening", "50")
    with pytest.raises(SystemExit):
        coordinator.parse_args(["--plan", "gripper-close-return", "--bindings", "unused"])
    ready, admitted = admission(gripper_raw=2957, gripper_interval=[2799, 3102])
    plan = coordinator.fresh_target(ready, admitted, degrees=0., direction="decreasing", ramp_seconds=10.)
    with pytest.raises(ValueError):
        coordinator.phases_for(plan, extended_options(), admitted=admitted)



@pytest.mark.parametrize("name,elbow_raw", [("gripper-close-return", 1150), ("grasp-return", 1128)])
def test_extended_closure_actual_coordinator_relay_declares_before_start_and_paces_original_return(tmp_path, monkeypatch, name, elbow_raw):
    ready, admitted = admission(elbow_raw=elbow_raw, gripper_raw=2957, gripper_interval=[1886, 3102])
    root, trial = tmp_path / "repo", tmp_path / "repo/trial"
    trial.mkdir(parents=True)
    (root / ".cache/am1-knowledge-reuse-18").mkdir(parents=True)
    (trial / "frames.jsonl").write_text(json.dumps(admitted) + "\n")
    now, commands, intents = [100.], [], []
    reader = VirtualIntentReader("interactive", clock=lambda: now[0],
        intent_channel="11111111-1111-4111-8111-111111111111")
    grip_duration = (ready["command_reference"][GRIP]-50.)*3./10.+2.
    durations = [grip_duration, grip_duration] if name == "gripper-close-return" else [grip_duration, 13., 13., grip_duration]

    def status(seq, elapsed, progress=0., state="current"):
        return dict(event="virtual_input_status", window_elapsed_s=elapsed, window_remaining_s=90.-elapsed,
                    current_accepted_intent_seq=seq, input_state=state, ramp_progress_active_s=progress)

    class Input:
        def write(self, command):
            commands.append(command)
            reader.receive_line(command)
        def flush(self):
            pass
        def close(self):
            pass

    class Process:
        def __init__(self, args, **kwargs):
            assert "--extended-grasp-closing" in args and "--supported-grasp-closing" not in args
            declaration = json.loads(next((root / ".cache/am1-knowledge-reuse-18").glob("*.console.jsonl")).read_text().splitlines()[0])
            assert declaration["event"] == "local_activity_declared"
            assert declaration["gripper_closed_opening"] == 50.
            assert declaration["selected_gripper_raw_interval"] == [1886, 3102]
            self.stdin, self.stdout, self.returncode = Input(), self.output(), None
        def output(self):
            yield json.dumps(dict(event="virtual_input_opened", output=str(trial))) + "\n"
            yield json.dumps(ready) + "\n"
            yield json.dumps(status(None, 0., state="waiting")) + "\n"
            elapsed = 0.
            for index, duration in enumerate(durations):
                intent = reader.take(); intents.append(intent)
                assert intent["op"] == "move"
                yield json.dumps(dict(event="virtual_intent_accepted", op="move", seq=intent["seq"])) + "\n"
                yield json.dumps(status(intent["seq"], elapsed, duration-.1)) + "\n"
                assert len(commands) == index+1, "incomplete dwell must not advance"
                now[0] += duration; elapsed += duration
                yield json.dumps(status(intent["seq"], elapsed, duration)) + "\n"
            finish = reader.take()
            assert finish["op"] == "finish"
            yield json.dumps(dict(event="virtual_intent_accepted", op="finish", seq=finish["seq"])) + "\n"
            self.returncode = 0
        def poll(self):
            return self.returncode
        def wait(self):
            return 0

    monkeypatch.setattr(coordinator, "ROOT", root)
    monkeypatch.setattr(coordinator.subprocess, "Popen", Process)
    assert coordinator.main(["--plan", name, "--extended-grasp-closing", "--gripper-closed-opening", "50",
        "--gripper-direction", "admitted", "--direction", "increasing" if name == "gripper-close-return" else "decreasing",
        "--comparison-dwell-seconds", "3",
        "--bindings", str(root / "bindings.json"), "--intent-ttl-seconds", "60"]) == 0
    assert len(commands) == len(durations)+1
    assert intents[0]["targets"] == {KEY: ready["command_reference"][KEY], GRIP: 50.}
    assert intents[-1]["targets"] == {KEY: ready["command_reference"][KEY], GRIP: ready["command_reference"][GRIP]}
    if name == "gripper-close-return":
        assert all(intent["targets"][KEY] == ready["command_reference"][KEY] for intent in intents)
    records = [json.loads(line) for line in next((root / ".cache/am1-knowledge-reuse-18").glob("*.console.jsonl")).read_text().splitlines()]
    fresh = next(row for row in records if row["event"] == "local_fresh_targets")
    assert fresh["planned_active_seconds"] == pytest.approx(sum(durations))
    assert not any(row["event"] == "local_excursion_unsupported" for row in records)


@pytest.mark.parametrize("grip", ["closing", "opening"])
@pytest.mark.parametrize("direction,elbow_raw", [("increasing", 1090), ("decreasing", 1128)])
def test_grasp_return_keeps_one_legal_interval_and_retracts_closed_to_original(grip, direction, elbow_raw):
    ready, admitted = admission(grip, elbow_raw=elbow_raw)
    original = copy.deepcopy(admitted)
    plan, phases = planned(ready, admitted, grasp_options(direction=direction))
    names = [phase["name"] for phase in phases]
    expected = ["grasp_close", "grasp_carry", "grasp_return_original_closed", "grasp_release", "finish"]
    if grip == "opening":
        expected.insert(0, "grasp_prepare_open")
    assert names == expected
    assert sum(phase.get("ramp", 0) + phase.get("dwell", 0) for phase in phases) == (41. if grip == "opening" else 36.)
    q0 = ready["command_reference"][KEY]
    # Four physical encoder degrees through the existing side-specific calibration.
    sign = 1 if direction == "increasing" else -1
    assert plan["target"] - q0 == pytest.approx(sign * 45.51111111111111 * 200 / 2209)
    opening, closed = 10., 0.
    by_name = {phase["name"]: phase for phase in phases}
    assert by_name["grasp_close"]["targets"] == {KEY: q0, GRIP: closed}
    assert by_name["grasp_carry"]["targets"] == {KEY: plan["target"], GRIP: closed}
    assert by_name["grasp_return_original_closed"]["targets"] == {KEY: q0, GRIP: closed}
    assert by_name["grasp_release"]["targets"] == {KEY: q0, GRIP: opening}
    assert all(phase["op"] == "move" for phase in phases[:-1])
    assert phases[-1] == {"name": "finish", "op": "finish"}
    assert phases[0]["gripper_interval"] == {
        "open": opening, "closed": closed, "opening_preparation": grip == "opening",
    }
    for phase in phases[:-1]:
        command = coordinator.phase_command(phase, 60.)
        assert command.startswith("move ") and " 60.0 " in command
        assert set(phase["targets"]) == {KEY, GRIP}
    assert plan["origin"] == ready["command_reference"]
    assert admitted == original  # No reseeding of measured references or workspace.
    if grip == "opening":
        assert by_name["grasp_prepare_open"]["targets"] == {KEY: q0, GRIP: opening}


@pytest.mark.parametrize("condition", ["closing", "legacy_opening", "opening_only"])
def test_supported_grasp_closing_keeps_fresh_reference_and_refuses_opening_substitute(condition):
    ready, admitted = admission(elbow_raw=1128, gripper_raw=2942 if condition == "opening_only" else 3094,
                                gripper_interval=[3094, 3397] if condition == "legacy_opening" else None)
    original = copy.deepcopy((ready, admitted))
    options = grasp_options(direction="decreasing")
    options.supported_grasp_closing = True
    if condition != "closing":
        with pytest.raises(ValueError):
            planned(ready, admitted, options)
    else:
        plan, phases = planned(ready, admitted, options)
        q0, g0 = plan["origin"][KEY], plan["origin"][GRIP]
        assert q0 == ready["command_reference"][KEY] and g0 == pytest.approx(79.73597359735973)
        assert [phase["name"] for phase in phases] == ["grasp_close", "grasp_carry", "grasp_return_original_closed", "grasp_release", "finish"]
        assert phases[0]["gripper_interval"] == {"open": g0, "closed": g0-10., "opening_preparation": False}
        assert phases[2]["targets"] == {KEY: q0, GRIP: g0-10.}
        assert phases[3]["targets"] == {KEY: q0, GRIP: g0}
        assert sum(phase.get("ramp", 0)+phase.get("dwell", 0) for phase in phases) == 36.
    assert (ready, admitted) == original


def test_supported_grasp_closing_requires_grasp_and_declared_admitted_policy():
    options = grasp_options("--supported-grasp-closing")
    assert options.supported_grasp_closing
    for extra in (["--plan", "out-return"], ["--plan", "lower-return-gripper"], ["--gripper-direction", "closing"]):
        with pytest.raises(SystemExit):
            grasp_options("--supported-grasp-closing", *extra)


@pytest.mark.parametrize("args", [
    ["--gripper-direction", "closing"], ["--degrees", "5"], ["--direction", "sideways"],
    ["--ramp-seconds", "8"], ["--comparison-dwell-seconds", "4"], ["--dwell-seconds", "4"],
])
def test_grasp_selection_rejects_unapproved_composition_before_process(args):
    with pytest.raises(SystemExit):
        grasp_options(*args)


@pytest.mark.parametrize("direction,elbow_raw", [("decreasing", 1090), ("increasing", 1128)])
def test_grasp_full_endpoint_refuses_unsupported_direction_without_reseeding(direction, elbow_raw):
    ready, admitted = admission(elbow_raw=elbow_raw)
    original = copy.deepcopy((ready, admitted))
    with pytest.raises(ValueError):
        planned(ready, admitted, grasp_options(direction=direction))
    assert (ready, admitted) == original


def test_grasp_requires_explicit_direction_before_adapter_launch(tmp_path, monkeypatch):
    (tmp_path / ".cache/am1-knowledge-reuse-18").mkdir(parents=True)
    monkeypatch.setattr(coordinator, "ROOT", tmp_path)
    monkeypatch.setattr(coordinator.subprocess, "Popen",
                        lambda *args, **kwargs: pytest.fail("omitted grasp direction reached adapter launch"))
    with pytest.raises(SystemExit):
        coordinator.main(["--plan", "grasp-return", "--gripper-direction", "admitted",
                          "--comparison-dwell-seconds", "3", "--bindings", "unused-current-bindings.json"])
    assert not list((tmp_path / ".cache/am1-knowledge-reuse-18").glob("*.console.jsonl"))


@pytest.mark.parametrize("mutation", ["no_interval", "two_intervals", "grip_rate", "elbow_limit",
    "reference_shift", "foreign_run", "lost_hold", "stale_ack"])
def test_grasp_refuses_incomplete_illegal_or_unqualified_admission_without_substitute(mutation):
    ready, admitted = admission("ambiguous" if mutation == "two_intervals" else "closing",
                                elbow_raw=1150 if mutation == "elbow_limit" else 1090)
    snapshot = admitted["snapshot"]
    native = snapshot["run"]["live_input"]["workspace"]
    if mutation == "no_interval":
        native["reference"][GRIP] = ready["command_reference"][GRIP] = 5.
        snapshot["run"]["live_input"].update(reference=copy.deepcopy(native["reference"]),
            cursor=dict(ready["command_reference"]), accepted_target=dict(ready["command_reference"]))
    elif mutation == "grip_rate":
        native["bounds"][GRIP]["rate_per_s"] = 2.
        ready["bounds"] = copy.deepcopy(native["bounds"])
    elif mutation == "reference_shift":
        snapshot["run"]["live_input"]["cursor"][KEY] += 1.
        snapshot["run"]["live_input"]["accepted_target"][KEY] += 1.
        ready["command_reference"][KEY] += 1.
    elif mutation == "foreign_run":
        ready["run_id"] = "foreign"
    elif mutation == "lost_hold":
        snapshot["evidence"]["hold_acknowledged"] = False
    elif mutation == "stale_ack":
        snapshot["evidence"]["accepted_host_action"]["acquired_at_monotonic_s"] = 99.
    with pytest.raises((ValueError, RuntimeError)):
        planned(ready, admitted, grasp_options())


@pytest.mark.parametrize("mode", ["pending", "wrong_seq", "not_done", "done", "expired", "insufficient_budget"])
def test_grasp_auto_advance_requires_current_ack_and_complete_dwell_or_truthful_finish(mode):
    ready, admitted = admission()
    _, phases = planned(ready, admitted, grasp_options())
    status = dict(input_state="current", window_remaining_s=75.,
                  current_accepted_intent_seq=2, ramp_progress_active_s=12.9)
    values = dict(phases=phases, index=1, pending=None, accepted=2, supported=True)
    expected = None
    if mode == "pending":
        values["pending"] = 2
    elif mode == "wrong_seq":
        status["current_accepted_intent_seq"] = 1
        status["ramp_progress_active_s"] = 13.
    elif mode == "done":
        status["ramp_progress_active_s"] = 13.
        expected = 2
    elif mode == "expired":
        status["input_state"] = "expired"
        expected = len(phases) - 1
    elif mode == "insufficient_budget":
        status.update(window_remaining_s=20., ramp_progress_active_s=3.)
        expected = len(phases) - 1
    assert coordinator.next_phase(status, **values) == expected


@pytest.mark.parametrize("name,extra,expected", [
    ("out-return", ["--direction", "increasing"], ["move", "return_original", "finish"]),
    ("bidirectional-midpoint", [], ["prepare_upper", "midpoint_decreasing", "prepare_lower",
                                   "midpoint_increasing", "return_original", "finish"]),
    ("lower-return-gripper", ["--gripper-direction", "admitted"],
     ["prepare_lower", "return_original", "gripper_close", "gripper_reopen_original", "finish"]),
    ("out-return", ["--direction", "increasing", "--gripper-cycle", "--gripper-direction", "admitted"],
     ["elbow_workpose", "gripper_close", "gripper_reopen_original", "return_original", "finish"]),
    ("out-return", ["--workpose", "midpoint", "--approach-from-below", "--gripper-cycle",
                    "--gripper-direction", "admitted"],
     ["prepare_below_midpoint", "midpoint_workpose", "gripper_close", "gripper_reopen_original",
      "prepare_below_original", "return_original", "finish"]),
])
def test_existing_plan_intentions_and_original_return_remain_available(name, extra, expected):
    ready, admitted = admission(elbow_raw=1107)
    options = coordinator.parse_args(["--plan", name, "--bindings", "unused-current-bindings.json", *extra])
    assert options.direction == (extra[extra.index("--direction")+1] if "--direction" in extra else "decreasing")
    plan, phases = planned(ready, admitted, options)
    assert [phase["name"] for phase in phases] == expected
    returns = [phase for phase in phases if phase["name"] == "return_original"]
    assert len(returns) == 1 and returns[0]["op"] == "return"
    assert plan["q0"] == ready["command_reference"][KEY]


@pytest.mark.parametrize("unsupported", [False, True])
@pytest.mark.parametrize("direction,elbow_raw", [("increasing", 1090), ("decreasing", 1128)])
@pytest.mark.parametrize("supported_closing", [False, True])
def test_coordinator_dispatches_complete_grasp_from_status_without_manual_pacing(tmp_path, monkeypatch, unsupported, direction, elbow_raw, supported_closing):
    ready, admitted = admission("ambiguous" if unsupported else "opening", elbow_raw=elbow_raw,
        gripper_raw=3094 if supported_closing else None,
        gripper_interval=[3094, 3397] if supported_closing and unsupported else None)
    root = tmp_path / "repo"
    trial = root / "trial"
    trial.mkdir(parents=True)
    (root / ".cache/am1-knowledge-reuse-18").mkdir(parents=True)
    (trial / "frames.jsonl").write_text(json.dumps(admitted) + "\n", encoding="utf-8")
    now, commands, intents, launches = [100.], [], [], []
    reader = VirtualIntentReader("interactive", clock=lambda: now[0],
        intent_channel="11111111-1111-4111-8111-111111111111")

    def status(seq, elapsed, *, state="current", progress=0.):
        return dict(event="virtual_input_status", window_elapsed_s=elapsed, window_remaining_s=90.-elapsed,
                    current_accepted_intent_seq=seq, input_state=state, ramp_progress_active_s=progress)

    class Input:
        def write(self, line):
            commands.append(line)
            reader.receive_line(line)
        def flush(self):
            pass
        def close(self):
            pass

    class Process:
        def __init__(self, args, **kwargs):
            launches.append((args, kwargs))
            self.stdin, self.returncode = Input(), None
            self.stdout = self.output()
        def output(self):
            yield json.dumps({"event": "virtual_input_opened", "output": str(trial)}) + "\n"
            yield json.dumps(ready) + "\n"
            yield json.dumps(status(None, 0., state="waiting")) + "\n"
            elapsed = 0.
            for i, duration in enumerate([] if unsupported else [5., 13., 13., 5.] if supported_closing else [5., 5., 13., 13., 5.]):
                intent = reader.take()
                assert intent["op"] == "move"
                intents.append(intent)
                assert intent["expires_at_perf_counter_s"] == now[0] + 60.
                assert len(commands) == i + 1
                yield json.dumps({"event": "virtual_intent_accepted", "op": "move", "seq": intent["seq"]}) + "\n"
                yield json.dumps(status(intent["seq"], elapsed, progress=duration-.1)) + "\n"
                assert len(commands) == i + 1, "neither acceptance alone nor an incomplete dwell advances"
                elapsed += duration
                now[0] += duration
                yield json.dumps(status(intent["seq"], elapsed, progress=duration)) + "\n"
                assert len(commands) == i + 2
            finish = reader.take()
            assert finish["op"] == "finish"
            yield json.dumps({"event": "virtual_intent_accepted", "op": "finish", "seq": finish["seq"]}) + "\n"
            yield json.dumps(status(finish["seq"], elapsed, state="finished")) + "\n"
            self.returncode = 0
        def poll(self):
            return self.returncode
        def wait(self):
            return 0

    monkeypatch.setattr(coordinator, "ROOT", root)
    monkeypatch.setattr(coordinator.subprocess, "Popen", Process)
    assert coordinator.main([
        "--plan", "grasp-return", "--gripper-direction", "admitted", "--degrees", "4",
        "--direction", direction, "--ramp-seconds", "10", "--comparison-dwell-seconds", "3",
        "--dwell-seconds", "3", "--window-seconds", "90", "--intent-ttl-seconds", "60",
        "--bindings", str(root / "current-bindings.json"),
        *(["--supported-grasp-closing"] if supported_closing else []),
    ]) == 0
    assert len(launches) == 1 and launches[0][1]["cwd"] == root
    argv = launches[0][0]
    assert argv[argv.index("-m")+1] == "tools.am1_leader_adapter"
    assert argv[argv.index("--duration-seconds")+1] == "90.0"
    assert ("--supported-grasp-closing" in argv) is supported_closing
    assert all(not command.startswith("return ") for command in commands)
    assert len(commands) == (1 if unsupported else 5 if supported_closing else 6)
    records = [json.loads(line) for line in next((root / ".cache/am1-knowledge-reuse-18").glob("*.console.jsonl")).read_text().splitlines()]
    declared = next(row for row in records if row["event"] == "local_activity_declared")
    assert declared["gripper_interval_policy"] == ("supported closing10-point interval; refuse opening substitute" if supported_closing
                                                   else "one legal10-point interval; prepare opening when only opening is admitted")
    assert declared["view"] == "normal required Chest" and declared["body_lift_intent"] == "zero"
    assert declared["direction"] == direction
    if unsupported:
        assert not intents
        assert any(row["event"] == "local_excursion_unsupported" for row in records)
        assert not any(row["event"] == "local_fresh_targets" for row in records)
    else:
        planned_record = next(row for row in records if row["event"] == "local_fresh_targets")
        assert planned_record["planned_active_seconds"] == (36. if supported_closing else 41.)
        g0 = ready["command_reference"][GRIP]
        assert planned_record["gripper_interval"] == {"open": g0 if supported_closing else 10.,
            "closed": g0-10. if supported_closing else 0., "opening_preparation": not supported_closing}
        assert [intent["seq"] for intent in intents] == ([1, 2, 3, 4] if supported_closing else [1, 2, 3, 4, 5])
        assert intents[2 if supported_closing else 3]["targets"] == {KEY: ready["command_reference"][KEY],
                                        GRIP: g0-10. if supported_closing else 0.}
        assert intents[-1]["targets"][GRIP] == (g0 if supported_closing else 10.)
        assert len({intent["issued_at_perf_counter_s"] for intent in intents}) == (4 if supported_closing else 5)


def test_missing_current_bindings_refuses_before_adapter_launch(tmp_path, monkeypatch):
    (tmp_path / ".cache/am1-knowledge-reuse-18").mkdir(parents=True)
    monkeypatch.setattr(coordinator, "ROOT", tmp_path)
    monkeypatch.setattr(coordinator.subprocess, "Popen",
                        lambda *args, **kwargs: pytest.fail("missing bindings reached adapter launch"))
    with pytest.raises(SystemExit):
        coordinator.main(["--plan", "out-return"])
    assert not list((tmp_path / ".cache/am1-knowledge-reuse-18").glob("*.console.jsonl"))
