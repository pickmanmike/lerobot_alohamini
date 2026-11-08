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

RIGHT_KEY = "arm_right_elbow_flex.pos"
RIGHT_GRIP = "arm_right_gripper.pos"


def right_admission(grip="closing"):
    ready, admitted = admission(elbow_raw=1090)
    snapshot = admitted["snapshot"]
    native = snapshot["run"]["live_input"]["workspace"]
    native["reference"][RIGHT_KEY] = 10.3  # raw1903, actual RIGHT800..2800 calibration.
    native["reference"][RIGHT_GRIP] = 10. if grip == "closing" else 0.
    native["bounds"][RIGHT_GRIP]["rate_per_s"] = 20.
    current = snapshot["run"]["live_input"]
    current.update(reference=dict(native["reference"]), cursor=dict(native["reference"]),
                   accepted_target=dict(native["reference"]))
    snapshot["evidence"]["accepted_host_action"]["arm_feedback"]["positions"][RIGHT_KEY] = {
        "raw": 1903, "normalized": 10.3,
    }
    ready.update(command_reference=dict(native["reference"]), bounds=copy.deepcopy(native["bounds"]))
    return ready, admitted


def right_options(*extra):
    return coordinator.parse_args(["--side", "right", "--plan", "out-return", "--degrees", "4",
        "--direction", "decreasing", "--bindings", "unused-current-bindings.json", *extra])


def right_planned(ready, admitted, options):
    plan = coordinator.fresh_target(ready, admitted, side=options.side, degrees=options.degrees,
                                   direction=options.direction, ramp_seconds=options.ramp_seconds)
    return plan, coordinator.phases_for(plan, options, admitted=admitted)


@pytest.mark.parametrize("direction,grip", [("increasing", "opening"), ("decreasing", "closing"), ("decreasing", None)])
def test_right_coordinator_real_intent_mapping_original_return_and_automatic_finish(tmp_path, monkeypatch, direction, grip):
    from uuid import uuid4

    from examples.alohamini.am1_teleop_workload import VirtualIntentWorkload
    from tools.am1_leader_adapter import FrameProducer, ZERO_BODY

    ready, admitted = right_admission(grip)
    original = copy.deepcopy((ready, admitted))
    native = admitted["snapshot"]["run"]["live_input"]["workspace"]
    origin = dict(ready["command_reference"])
    endpoint = 14.85111111111111 if direction == "increasing" else 5.748888888888889
    root, trial = tmp_path / "repo", tmp_path / "repo/trial"
    trial.mkdir(parents=True)
    (root / ".cache/am1-knowledge-reuse-18").mkdir(parents=True)
    (trial / "frames.jsonl").write_text(json.dumps(admitted)+"\n", encoding="utf-8")
    now, commands, launches = [100.], [], []
    reader = VirtualIntentReader("interactive", clock=lambda: now[0], intent_channel=str(uuid4()))
    source = VirtualIntentWorkload(native, command_reference=origin, clock=lambda: now[0], intent_channel=reader.intent_channel)
    producer = FrameProducer(source, clock=lambda: now[0], source_id=reader.intent_channel)
    producer.workspace, producer.native_cursor = native, dict(origin)
    outward = {RIGHT_KEY: endpoint, **({RIGHT_GRIP: origin[RIGHT_GRIP]} if grip else {})}
    expected = [("move", outward, 16. if grip else 13.)]
    if grip:
        expected += [("move", {RIGHT_KEY: endpoint, RIGHT_GRIP: 10. if grip == "opening" else 0.}, 5.),
                     ("move", {RIGHT_KEY: endpoint, RIGHT_GRIP: origin[RIGHT_GRIP]}, 5.)]
    expected += [("return", None, 13.)]

    def status(seq, elapsed, progress=0., state="current"):
        return dict(event="virtual_input_status", window_elapsed_s=elapsed, window_remaining_s=120.-elapsed,
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
            self.stdin, self.stdout, self.returncode = Input(), self.output(), None
        def output(self):
            yield json.dumps(dict(event="virtual_input_opened", output=str(trial)))+"\n"
            yield json.dumps(ready)+"\n"
            yield json.dumps(status(None, 0., state="waiting"))+"\n"
            elapsed = 0.
            for index, (op, targets, duration) in enumerate(expected):
                intent = reader.take()
                assert intent["op"] == op and intent["seq"] == index+1
                if targets is not None:
                    assert intent["targets"] == pytest.approx(targets)
                    assert all(key.startswith("arm_right_") for key in intent["targets"])
                assert intent["issued_at_perf_counter_s"] == now[0]
                assert intent["expires_at_perf_counter_s"] == now[0]+60.
                assert source.submit(intent)
                source.admit(now[0])
                for _ in range(round(duration*10)):
                    now[0] += .1
                    source.advance(now[0])
                frame = producer.serialize(producer.capture({"grant_id": str(uuid4())}))
                wanted = dict(origin) if op == "return" else {**source.start_target, **targets}
                assert frame["target"] == pytest.approx(wanted)
                assert frame["body"] == ZERO_BODY
                assert all(frame["target"][key] == origin[key] for key in origin if key not in {RIGHT_KEY, RIGHT_GRIP})
                source.accepted(frame["target"])
                yield json.dumps(dict(event="virtual_intent_accepted", op=op, seq=intent["seq"]))+"\n"
                yield json.dumps(status(None, elapsed, duration))+"\n"
                assert len(commands) == index+1, "foreign acceptance cannot advance"
                yield json.dumps(status(intent["seq"], elapsed, duration, state="waiting"))+"\n"
                assert len(commands) == index+1, "held status cannot advance"
                yield json.dumps(status(intent["seq"], elapsed, duration-.1))+"\n"
                assert len(commands) == index+1, "incomplete active dwell cannot advance"
                elapsed += duration
                yield json.dumps(status(intent["seq"], elapsed, duration))+"\n"
                assert len(commands) == index+2
            assert source.cursor == pytest.approx(origin)
            finish = reader.take()
            assert finish["op"] == "finish" and finish["seq"] == len(expected)+1
            assert finish["issued_at_perf_counter_s"] == now[0] and finish["expires_at_perf_counter_s"] == now[0]+1.
            assert source.submit(finish) and source.complete
            yield json.dumps(dict(event="virtual_intent_accepted", op="finish", seq=finish["seq"]))+"\n"
            yield json.dumps(status(finish["seq"], elapsed, state="finished"))+"\n"
            self.returncode = 0
        def poll(self):
            return self.returncode
        def wait(self):
            return 0

    monkeypatch.setattr(coordinator, "ROOT", root)
    monkeypatch.setattr(coordinator.subprocess, "Popen", Process)
    assert coordinator.main(["--side", "right", "--plan", "out-return", "--direction", direction,
        "--degrees", "4", "--ramp-seconds", "10", "--comparison-dwell-seconds", "6", "--dwell-seconds", "3",
        "--window-seconds", "120", "--intent-ttl-seconds", "60", "--bindings", str(root / "bindings.json"),
        *(["--gripper-cycle", "--gripper-direction", grip] if grip else [])]) == 0
    argv, kwargs = launches[0]
    assert "--prepare-current-rest" in argv and "--virtual-input" in argv and kwargs["cwd"] == root
    assert "--supported-grasp-closing" not in argv and "--extended-grasp-closing" not in argv
    assert argv[argv.index("--bindings")+1] == str(root / "bindings.json")
    records = [json.loads(line) for line in next((root / ".cache/am1-knowledge-reuse-18").glob("*.console.jsonl")).read_text().splitlines()]
    declaration = next(row for row in records if row["event"] == "local_activity_declared")
    assert declaration["side"] == "right" and declaration["view"] == "normal required Chest"
    assert declaration["body_lift_intent"] == "zero" and declaration["other_joints"] == "fresh admitted holds"
    planned = next(row for row in records if row["event"] == "local_fresh_targets")
    assert planned["calibration"] == [3, 0, 0, 800, 2800] and planned["original_q0"] == 10.3
    if grip:
        assert planned["gripper_calibration"] == [6, 0, 0, 1000, 3000]
        assert planned["gripper_origin"] == origin[RIGHT_GRIP]
    assert (ready, admitted) == original
    assert coordinator.KEY == KEY and coordinator.GRIP == GRIP


@pytest.mark.parametrize("extra", [
    ["--plan", "grasp-return"], ["--plan", "lower-return-gripper"], ["--plan", "bidirectional-midpoint"],
    ["--plan", "gripper-close-return"], ["--plan", "gripper-open-finish"], ["--workpose", "midpoint"],
    ["--approach-from-below"], ["--supported-grasp-closing"], ["--extended-grasp-closing"],
    ["--gripper-closed-opening", "50"], ["--gripper-open-opening", "68"],
    ["--gripper-cycle", "--gripper-direction", "admitted"],
])
def test_right_selection_refuses_left_only_compositions_before_adapter_launch(tmp_path, monkeypatch, extra):
    monkeypatch.setattr(coordinator.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("invalid RIGHT selection reached adapter"))
    with pytest.raises(SystemExit):
        coordinator.main(["--side", "right", "--bindings", "unused", *extra])


@pytest.mark.parametrize("mutation", ["elbow_bounds", "elbow_rate", "gripper_rate", "gripper_endpoint", "zero_gripper",
    "ready_bounds", "foreign_run", "changed_origin", "lost_hold", "stale_ack", "point_mapping", "side_mismatch"])
def test_right_selection_refuses_illegal_or_unqualified_fresh_admission_without_mutation(mutation):
    ready, admitted = right_admission()
    snapshot = admitted["snapshot"]
    current = snapshot["run"]["live_input"]
    native = current["workspace"]
    options = right_options("--gripper-cycle", "--gripper-direction", "closing")
    if mutation == "elbow_bounds":
        options.degrees = 10.
    elif mutation == "elbow_rate":
        options.ramp_seconds = .1
    elif mutation == "gripper_rate":
        native["bounds"][RIGHT_GRIP]["rate_per_s"] = 2.
        ready["bounds"] = copy.deepcopy(native["bounds"])
    elif mutation in {"gripper_endpoint", "zero_gripper"}:
        native["reference"][RIGHT_GRIP] = 5. if mutation == "gripper_endpoint" else 0.
        if mutation == "zero_gripper":
            native["bounds"][RIGHT_GRIP]["max"] = 0.
            native["raw_bounds"][RIGHT_GRIP] = [1000, 1000]
            ready["bounds"] = copy.deepcopy(native["bounds"])
        current.update(reference=dict(native["reference"]), cursor=dict(native["reference"]), accepted_target=dict(native["reference"]))
        ready["command_reference"] = dict(native["reference"])
    elif mutation == "ready_bounds":
        ready["bounds"][RIGHT_KEY]["max"] -= 1.
    elif mutation == "foreign_run":
        ready["run_id"] = "foreign"
    elif mutation == "changed_origin":
        for value in (current["cursor"], current["accepted_target"], ready["command_reference"]):
            value[RIGHT_KEY] += 1.
    elif mutation == "lost_hold":
        snapshot["evidence"]["hold_acknowledged"] = False
    elif mutation == "stale_ack":
        snapshot["evidence"]["accepted_host_action"]["acquired_at_monotonic_s"] = 99.
    elif mutation == "point_mapping":
        snapshot["evidence"]["accepted_host_action"]["arm_feedback"]["positions"][RIGHT_KEY]["normalized"] += 1.
    original = copy.deepcopy((ready, admitted))
    if mutation == "side_mismatch":
        plan = coordinator.fresh_target(ready, admitted, degrees=4., direction="increasing", ramp_seconds=10.)
        with pytest.raises(ValueError):
            coordinator.phases_for(plan, options, admitted=admitted)
    else:
        with pytest.raises((ValueError, RuntimeError)):
            right_planned(ready, admitted, options)
    assert (ready, admitted) == original


def test_right_meaningful_seven_degree_request_uses_fresh_interior_pose_and_declared_rate():
    ready, admitted = right_admission()
    snapshot = admitted["snapshot"]
    current = snapshot["run"]["live_input"]
    native = current["workspace"]
    native["reference"][RIGHT_KEY] = 5.4
    native["bounds"][RIGHT_KEY]["rate_per_s"] = 3.
    for values in (current["reference"], current["cursor"], current["accepted_target"], ready["command_reference"]):
        values[RIGHT_KEY] = 5.4
    ready["bounds"] = copy.deepcopy(native["bounds"])
    snapshot["evidence"]["accepted_host_action"]["arm_feedback"]["positions"][RIGHT_KEY] = {"raw": 1854, "normalized": 5.4}
    original = copy.deepcopy((ready, admitted))
    options = right_options("--degrees", "7", "--direction", "increasing", "--ramp-seconds", "3",
                            "--comparison-dwell-seconds", "3", "--gripper-cycle", "--gripper-direction", "closing")
    plan, phases = right_planned(ready, admitted, options)
    assert plan["q0"] == 5.4 and plan["target"] == pytest.approx(13.364444444444445)
    assert [phase["name"] for phase in phases] == ["elbow_workpose", "gripper_close", "gripper_return_original", "return_original", "finish"]
    assert sum(phase.get("ramp", 0)+phase.get("dwell", 0) for phase in phases) == 22.
    assert phases[1]["targets"] == pytest.approx({RIGHT_KEY: 13.364444444444445, RIGHT_GRIP: 0.})
    assert (ready, admitted) == original


def right_span_admission(elbow_raw=1883):
    ready, admitted = right_admission()
    native = admitted["snapshot"]["run"]["live_input"]["workspace"]
    native["calibration"][RIGHT_KEY[:-4]] = [3, 0, -658, 827, 3039]
    native["raw_bounds"][RIGHT_KEY] = [1847, 1960]
    native["bounds"][RIGHT_KEY] = dict(min=-7.775768535262202, max=2.441229656419523,
                                      rate_per_s=5.143660839863371)
    native["reference"][RIGHT_KEY] = (elbow_raw-827)*200/2212-100
    native["calibration"][RIGHT_GRIP[:-4]] = [6, 0, -1811, 1657, 3191]
    native["raw_bounds"][RIGHT_GRIP] = [2699, 3005]
    native["bounds"][RIGHT_GRIP] = dict(min=67.92698826597132, max=87.8748370273794, rate_per_s=20.)
    native["reference"][RIGHT_GRIP] = 87.8748370273794
    current = admitted["snapshot"]["run"]["live_input"]
    current.update(reference=dict(native["reference"]), cursor=dict(native["reference"]),
                   accepted_target=dict(native["reference"]))
    admitted["snapshot"]["evidence"]["accepted_host_action"]["arm_feedback"]["positions"][RIGHT_KEY] = {
        "raw": elbow_raw, "normalized": native["reference"][RIGHT_KEY],
    }
    ready.update(command_reference=dict(native["reference"]), bounds=copy.deepcopy(native["bounds"]))
    return ready, admitted


def span_options(*extra):
    return coordinator.parse_args(["--side", "right", "--workpose", "admitted-span",
        "--ramp-seconds", "2.1", "--dwell-seconds", "2", "--window-seconds", "90",
        "--intent-ttl-seconds", "60", "--bindings", "unused-current-bindings.json", *extra])


def span_planned(ready, admitted, options):
    plan = coordinator.fresh_target(ready, admitted, side=options.side, workpose=options.workpose,
        degrees=options.degrees, direction=options.direction, ramp_seconds=options.ramp_seconds)
    return plan, coordinator.phases_for(plan, options, admitted=admitted)


@pytest.mark.parametrize("gripper", [False, True])
@pytest.mark.parametrize("elbow_raw", [1883, 1945])
def test_right_admitted_span_real_relay_uses_fixed_bounds_then_original_gripper_pose(tmp_path, monkeypatch, gripper, elbow_raw):
    from uuid import uuid4

    from examples.alohamini.am1_teleop_workload import VirtualIntentWorkload
    from tools.am1_leader_adapter import FrameProducer, ZERO_BODY

    ready, admitted = right_span_admission(elbow_raw)
    original = copy.deepcopy((ready, admitted))
    native = admitted["snapshot"]["run"]["live_input"]["workspace"]
    origin = dict(ready["command_reference"])
    lower, upper = -7.775768535262202, 2.441229656419523
    root, trial = tmp_path/"repo", tmp_path/"repo/trial"
    trial.mkdir(parents=True)
    (root/".cache/am1-knowledge-reuse-18").mkdir(parents=True)
    (trial/"frames.jsonl").write_text(json.dumps(admitted)+"\n", encoding="utf-8")
    now, commands, launches = [100.], [], []
    reader = VirtualIntentReader("interactive", clock=lambda: now[0], intent_channel=str(uuid4()))
    source = VirtualIntentWorkload(native, command_reference=origin, clock=lambda: now[0], intent_channel=reader.intent_channel)
    producer = FrameProducer(source, clock=lambda: now[0], source_id=reader.intent_channel)
    producer.workspace, producer.native_cursor = native, dict(origin)
    expected = [("move", {RIGHT_KEY: lower}, 4.1), ("move", {RIGHT_KEY: upper}, 4.1),
                ("move", {RIGHT_KEY: lower}, 4.1), ("return", None, 4.1)]
    if gripper:
        expected += [("move", {RIGHT_KEY: origin[RIGHT_KEY], RIGHT_GRIP: origin[RIGHT_GRIP]-10.}, 5.),
                     ("move", {RIGHT_KEY: origin[RIGHT_KEY], RIGHT_GRIP: origin[RIGHT_GRIP]}, 5.)]

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
            launches.append((args, kwargs))
            self.stdin, self.stdout, self.returncode = Input(), self.output(), None
        def output(self):
            yield json.dumps(dict(event="virtual_input_opened", output=str(trial)))+"\n"
            yield json.dumps(ready)+"\n"
            yield json.dumps(status(None, 0., state="waiting"))+"\n"
            elapsed = 0.
            for index, (op, targets, duration) in enumerate(expected):
                intent = reader.take()
                assert intent["op"] == op and intent["seq"] == index+1
                if targets is not None:
                    assert intent["targets"] == pytest.approx(targets)
                assert intent["expires_at_perf_counter_s"] == now[0]+60.
                assert source.submit(intent)
                source.admit(now[0])
                for _ in range(round(duration*10)):
                    now[0] += .1
                    source.advance(now[0])
                frame = producer.serialize(producer.capture({"grant_id": str(uuid4())}))
                wanted = dict(origin) if op == "return" else {**source.start_target, **targets}
                assert frame["target"] == pytest.approx(wanted) and frame["body"] == ZERO_BODY
                assert all(frame["target"][key] == origin[key] for key in origin if key not in {RIGHT_KEY, RIGHT_GRIP})
                source.accepted(frame["target"])
                yield json.dumps(dict(event="virtual_intent_accepted", op=op, seq=intent["seq"]))+"\n"
                for sample in (status(None, elapsed, duration), status(intent["seq"], elapsed, duration, "waiting"),
                               status(intent["seq"], elapsed, duration-.1)):
                    yield json.dumps(sample)+"\n"
                    assert len(commands) == index+1, "phase cannot advance without current acknowledged complete dwell"
                elapsed += duration
                yield json.dumps(status(intent["seq"], elapsed, duration))+"\n"
                assert len(commands) == index+2
            assert source.cursor == pytest.approx(origin)
            finish = reader.take()
            assert finish["op"] == "finish" and finish["expires_at_perf_counter_s"] == now[0]+1.
            assert source.submit(finish) and source.complete
            yield json.dumps(dict(event="virtual_intent_accepted", op="finish", seq=finish["seq"]))+"\n"
            yield json.dumps(status(finish["seq"], elapsed, state="finished"))+"\n"
            self.returncode = 0
        def poll(self):
            return self.returncode
        def wait(self):
            return 0
    monkeypatch.setattr(coordinator, "ROOT", root)
    monkeypatch.setattr(coordinator.subprocess, "Popen", Process)
    monkeypatch.setattr(coordinator, "approach_pp", lambda *a, **k: pytest.fail("timed RIGHT prep reached LEFT approach gate"))
    assert coordinator.main(["--side", "right", "--workpose", "admitted-span", "--ramp-seconds", "2.1",
        "--dwell-seconds", "2", "--window-seconds", "90", "--intent-ttl-seconds", "60",
        "--bindings", str(root/"bindings.json"), *(["--gripper-cycle"] if gripper else [])]) == 0
    argv, kwargs = launches[0]
    assert "--prepare-current-rest" in argv and kwargs["cwd"] == root
    assert "--supported-grasp-closing" not in argv and "--extended-grasp-closing" not in argv
    rows = [json.loads(line) for line in next((root/".cache/am1-knowledge-reuse-18").glob("*.console.jsonl")).read_text().splitlines()]
    declared = next(row for row in rows if row["event"] == "local_activity_declared")
    planned = next(row for row in rows if row["event"] == "local_fresh_targets")
    assert declared["workpose"] == "admitted-span" and declared["degrees"] is declared["direction"] is None
    assert declared["preparation_policy"] == "ordinary-timed; no measured endpoint gate"
    assert planned["planned_active_seconds"] == pytest.approx(26.4 if gripper else 16.4)
    span = planned["admitted_span"]
    assert span["raw_bounds"] == [1847, 1960] and span["bounds"] == [lower, upper]
    assert span["encoder_degrees"] == pytest.approx(9.931640625)
    phases = planned["phases"]
    assert [p["name"] for p in phases[:4]] == ["prepare_lower", "span_upper", "span_lower", "return_original"]
    assert phases[0]["timed_preparation"] is True and "preparation" not in phases[0]
    assert phases[0]["dwell"] == 2. and phases[0]["targets"] == {RIGHT_KEY: lower}
    assert all("preparation" not in p and "approach" not in p for p in phases)
    assert (ready, admitted) == original


@pytest.mark.parametrize("extra", [
    ["--side", "left"], ["--degrees", "4"], ["--degrees=7"], ["--direction", "increasing"],
    ["--approach-from-below"], ["--supported-grasp-closing"], ["--extended-grasp-closing"],
    ["--gripper-closed-opening", "50"], ["--gripper-open-opening", "68"], ["--plan", "grasp-return"],
    ["--gripper-cycle", "--gripper-direction", "opening"], ["--gripper-cycle", "--gripper-direction", "admitted"],
])
def test_admitted_span_refuses_left_and_relative_or_left_only_flags_before_launch(monkeypatch, extra):
    monkeypatch.setattr(coordinator.subprocess, "Popen", lambda *a, **k: pytest.fail("invalid span reached adapter"))
    with pytest.raises(SystemExit):
        coordinator.main(["--side", "right", "--workpose", "admitted-span", "--bindings", "unused", *extra])


@pytest.mark.parametrize("fault", ["zero", "expanded", "rate", "gripper_rate", "reference", "hold", "gripper_endpoint"])
def test_right_admitted_span_refuses_expansion_rates_and_changed_fresh_origin_without_mutation(fault):
    ready, admitted = right_span_admission()
    native = admitted["snapshot"]["run"]["live_input"]["workspace"]
    if fault == "zero":
        native["raw_bounds"][RIGHT_KEY] = [1883, 1883]
        native["bounds"][RIGHT_KEY].update(min=native["reference"][RIGHT_KEY], max=native["reference"][RIGHT_KEY])
    elif fault == "expanded":
        native["raw_bounds"][RIGHT_KEY] = [1846, 1960]
        native["bounds"][RIGHT_KEY]["min"] = (1846-827)*200/2212-100
    elif fault == "rate":
        native["bounds"][RIGHT_KEY]["rate_per_s"] = 4.
    elif fault == "gripper_rate":
        native["bounds"][RIGHT_GRIP]["rate_per_s"] = 2.
    elif fault == "reference":
        ready["command_reference"][RIGHT_KEY] += .1
    elif fault == "hold":
        admitted["snapshot"]["evidence"]["hold_acknowledged"] = False
    else:
        native["reference"][RIGHT_GRIP] = 70.
        current = admitted["snapshot"]["run"]["live_input"]
        for values in (current["reference"], current["cursor"], current["accepted_target"], ready["command_reference"]):
            values[RIGHT_GRIP] = 70.
    ready["bounds"] = copy.deepcopy(native["bounds"])
    original = copy.deepcopy((ready, admitted))
    with pytest.raises((ValueError, RuntimeError)):
        span_planned(ready, admitted, span_options("--gripper-cycle"))
    assert (ready, admitted) == original

# Fresh ordinary region; no hardware setup or old raw references.
def ordinary_admission():
    from examples.alohamini.am1_live_teleop import (ORDINARY_AM1_ARMS_PROFILE, ORDINARY_AM1_ARM_CALIBRATION,
        ORDINARY_AM1_RAW_BOUNDS, TELEOP_ARM_KEYS)
    ready, admitted = admission()
    native = admitted["snapshot"]["run"]["live_input"]["workspace"]
    native["workspace_profile"] = ORDINARY_AM1_ARMS_PROFILE
    points = {}
    for key in TELEOP_ARM_KEYS:
        cal = list(ORDINARY_AM1_ARM_CALIBRATION[key[:-4]])
        low, high = ORDINARY_AM1_RAW_BOUNDS[key]
        grip = key.endswith("gripper.pos")
        raw = high-10 if grip else low+2 if "wrist_flex" in key else (low+high)//2
        q = normalized_raw(raw, cal, gripper=grip)
        bounds = sorted(normalized_raw(v, cal, gripper=grip) for v in (low, high))
        native["calibration"][key[:-4]] = cal
        native["raw_bounds"][key] = [low, high]
        native["reference"][key] = q
        native["bounds"][key] = dict(min=bounds[0], max=bounds[1], rate_per_s=20. if grip else 5*4096/360*200/(cal[4]-cal[3]))
        points[key] = dict(raw=raw, normalized=q)
    admitted["snapshot"]["run"].update(source="protected-physical-provider", bench_continuity=False, recovery_episodes=0, first_cause=None, native_first_cause=None,
        ordinary_arm_profile={"profile": ORDINARY_AM1_ARMS_PROFILE, "right_elbow_p": 16})
    admitted["snapshot"]["evidence"]["source"] = "protected-physical-provider"
    current = admitted["snapshot"]["run"]["live_input"]
    current["required_view"].update(required=True, role="chest")
    current.update(reference=copy.deepcopy(native["reference"]), cursor=dict(native["reference"]), accepted_target=dict(native["reference"]))
    admitted["snapshot"]["evidence"]["accepted_host_action"]["arm_feedback"]["positions"] = points
    ready.update(command_reference=dict(native["reference"]), bounds=copy.deepcopy(native["bounds"]), units=dict(native["units"]))
    return ready, admitted


def ordinary_options(*extra):
    from examples.alohamini.am1_live_teleop import ORDINARY_AM1_ARMS_PROFILE
    return coordinator.parse_args(["--plan", "ordinary-arms", "--operating-profile", ORDINARY_AM1_ARMS_PROFILE,
                                   "--bindings", "unused-current.json", *extra])


def test_ordinary_control_plan_declares_pause_resume_then_new_movement():
    ready, admitted = ordinary_admission()
    options = ordinary_options()
    plan = coordinator.fresh_ordinary_targets(ready, admitted, ramp_seconds=options.ramp_seconds,
                                              gripper_direction=options.gripper_direction)
    phases = coordinator.phases_for(plan, options, admitted=admitted)
    index = next(i for i, phase in enumerate(phases) if phase["op"] == "pause")
    assert phases[index+1]["op"] == "resume" and phases[index+2]["op"] == "move"
    assert phases[index]["dwell"] == 2.
    assert phases[index+2]["targets"] == phases[index-1]["targets"]


def test_control_phase_commands_and_held_pacing_wait_for_explicit_ack():
    phases = [dict(op="pause", dwell=2.), dict(op="resume"), dict(op="move", ramp=3., dwell=2.), dict(op="finish")]
    assert coordinator.phase_command(phases[0], 10.) == "pause 10.0"
    assert coordinator.phase_command(phases[1], 10.) == "resume 10.0"
    status = dict(input_state="waiting", window_remaining_s=60., current_accepted_intent_seq=None,
                  ramp_progress_active_s=0.)
    for elapsed, expected in ((None, None), (1., None), (2., 1)):
        assert coordinator.next_phase(status, phases=phases, index=0, pending=None, accepted=2,
                                      supported=True, control_elapsed_s=elapsed) == expected


@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize("plan_name", ["ordinary-arms", "ordinary-short"])
def test_ordinary_coordinator_real_relay_waits_for_pause_ack_and_fresh_resume_before_new_move(tmp_path, monkeypatch, external, plan_name, _right_wrist_degrees=None):
    from types import SimpleNamespace
    from uuid import uuid4

    from examples.alohamini.am1_teleop_workload import VirtualIntentWorkload
    from tools.am1_leader_adapter import FrameProducer, ZERO_BODY

    ready, admitted = ordinary_wrist_midpoint_admission() if _right_wrist_degrees is not None else ordinary_admission()
    native = admitted["snapshot"]["run"]["live_input"]["workspace"]
    extra=[] if _right_wrist_degrees is None else ["--right-wrist-flex-degrees",str(_right_wrist_degrees)]
    options = ordinary_options(*extra) if plan_name == "ordinary-arms" else ordinary_short_options()
    plan = coordinator.fresh_ordinary_targets(ready, admitted, ramp_seconds=3., gripper_direction="closing", short=plan_name == "ordinary-short",
        right_wrist_flex_degrees=options.right_wrist_flex_degrees or 3.)
    phases = coordinator.phases_for(plan, options, admitted=admitted)
    root, trial = tmp_path/"repo", tmp_path/"repo/trial"
    trial.mkdir(parents=True)
    (root/".cache/am1-knowledge-reuse-18").mkdir(parents=True)
    (trial/"frames.jsonl").write_text(json.dumps(admitted)+"\n", encoding="utf-8")
    now, commands, frames = [100.], [], []
    reader = VirtualIntentReader("interactive", clock=lambda: now[0], intent_channel=str(uuid4()), allow_controls=True)
    source = VirtualIntentWorkload(native, command_reference=plan["origin"], clock=lambda: now[0], intent_channel=reader.intent_channel)
    producer = FrameProducer(source, clock=lambda: now[0], source_id=reader.intent_channel)
    producer.workspace, producer.native_cursor = native, dict(plan["origin"])
    def status(seq=None, progress=0., state="waiting"):
        return dict(event="virtual_input_status", window_elapsed_s=now[0]-100., window_remaining_s=190.-now[0],
                    current_accepted_intent_seq=seq, input_state=state, ramp_progress_active_s=progress)
    class Input:
        def write(self, command): commands.append(command); reader.receive_line(command)
        def flush(self): pass
        def close(self): pass
    class Process:
        def __init__(self, *args, **kwargs): self.stdin, self.stdout, self.returncode = Input(), self.output(), None
        def output(self):
            yield json.dumps(dict(event="virtual_input_opened", output=str(trial)))+"\n"
            yield json.dumps(ready)+"\n"
            yield json.dumps(status())+"\n"
            for index, phase in enumerate(phases):
                intent = reader.take()
                assert intent["op"] == phase["op"] and intent["seq"] == index+1
                if phase["op"] == "pause":
                    source.invalidate()
                    yield json.dumps(dict(event="virtual_control_paused", op="pause", seq=None if external else intent["seq"]))+"\n"
                    now[0] += 1.
                    yield json.dumps(status())+"\n"
                    assert len(commands) == index+1
                    now[0] += 1.
                    yield json.dumps(status())+"\n"
                    if external:
                        assert reader.take() is None, "an external Pause cannot trigger scheduled Resume/Finish"
                        self.returncode = 0
                        return
                elif phase["op"] == "resume":
                    assert source.command is None
                    reader.discard_motion()
                    producer.native_cursor, producer.reacquire = dict(source.cursor), True
                    yield json.dumps(dict(event="virtual_control_resumed", op="resume", seq=intent["seq"]))+"\n"
                else:
                    assert source.submit(intent)
                    yield json.dumps(dict(event="virtual_intent_accepted", op=intent["op"], seq=intent["seq"]))+"\n"
                    if phase["op"] == "finish":
                        assert source.complete
                        break
                    duration = phase["ramp"]+phase["dwell"]
                    source.admit(now[0])
                    for _ in range(round(duration*10)):
                        now[0] += .1; source.advance(now[0])
                    frame = producer.serialize(producer.capture({"grant_id": str(uuid4())}))
                    frames.append(frame)
                    source.accepted(frame["target"])
                    yield json.dumps(status(intent["seq"], duration, "current"))+"\n"
            self.returncode = 0
        def poll(self): return self.returncode
        def wait(self): return 0
    monkeypatch.setattr(coordinator, "ROOT", root)
    monkeypatch.setattr(coordinator, "time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(coordinator.subprocess, "Popen", Process)
    assert coordinator.main(["--plan", plan_name, "--operating-profile", "ordinary-am1-arms-v1",
                             "--bindings", str(root/"bindings.json"), "--window-seconds", "90",*extra]) == 0
    assert all(frame["body"] == ZERO_BODY and set(frame["target"]) == set(plan["origin"]) for frame in frames)
    if not external:
        assert [command.split()[0] for command in commands] == (["move", "move", "pause", "resume", "return", "finish"] if plan_name == "ordinary-short" else
            ["move", "return", "move", "return", "move", "pause", "resume", "move", "move", "move", "return", "finish"])
        assert frames[-1]["target"] == pytest.approx(plan["origin"])


def test_ordinary_plan_declares_two_arm_legal_full_amplitudes_and_original_returns():
    from examples.alohamini.am1_live_teleop import validate_target
    ready, admitted = ordinary_admission()
    original = copy.deepcopy((ready, admitted))
    options = ordinary_options()
    plan = coordinator.fresh_ordinary_targets(ready, admitted, ramp_seconds=options.ramp_seconds,
                                              gripper_direction=options.gripper_direction)
    phases = coordinator.phases_for(plan, options, admitted=admitted)
    assert [p["name"] for p in phases] == ["left_elbow", "left_return_original", "right_elbow", "right_return_original",
        "ordinary_both_arms", "ordinary_pause", "ordinary_resume", "ordinary_reengaged_arms",
        "ordinary_both_grippers", "ordinary_grippers_return", "return_original", "finish"]
    assert sum(p.get("ramp", 0)+p.get("dwell", 0) for p in phases if p["op"] not in {"pause", "resume"}) == 45.
    assert set(plan["excursions"]) == set(ready["command_reference"])
    previous = dict(plan["origin"])
    for phase in phases[:-1]:
        if phase["op"] in {"pause", "resume"}: continue
        target = plan["origin"] if phase["op"] == "return" else phase["targets"]
        validate_target(target, workspace=plan["workspace"])
        for key, value in target.items():
            assert abs(value-previous[key])/phase["ramp"] <= plan["workspace"]["bounds"][key]["rate_per_s"]+1e-9
        assert phase["complete_before_advance"] is True
        previous = dict(target)
    assert previous == ready["command_reference"]
    assert (ready, admitted) == original


@pytest.mark.parametrize("fault", ["reference", "owner", "ack", "units", "profile", "feedback", "deadline", "rate", "grip"])
def test_ordinary_plan_refuses_unqualified_or_insufficient_full_amplitude(fault):
    ready, admitted = ordinary_admission()
    state = admitted["snapshot"]; native = state["run"]["live_input"]["workspace"]
    if fault == "reference": state["run"]["live_input"]["reference"][KEY] += .01
    elif fault == "owner": state["controller"]["device_id"] = None
    elif fault == "ack": state["evidence"]["native_ack"] = False
    elif fault == "units": ready["units"][KEY] = "wrong"
    elif fault == "profile": native["workspace_profile"] = CURRENT_REST_WORKSPACE_PROFILES["ordinary-arms"]
    elif fault == "feedback": state["evidence"]["accepted_host_action"]["arm_feedback"]["positions"][KEY]["normalized"] += .1
    elif fault == "deadline": state["run"]["deadline"] += 301
    elif fault == "rate": native["bounds"][KEY]["rate_per_s"] = .0001
    else:
        # Explicit full opening cannot fit from this midpoint in the narrow RIGHT gripper interval.
        pass
    with pytest.raises((ValueError, RuntimeError)):
        coordinator.fresh_ordinary_targets(ready, admitted, ramp_seconds=3., gripper_direction="opening" if fault == "grip" else "closing")


def test_coordinator_ordinary_config_uses_same_loader_and_preserves_selected_window(tmp_path):
    from tests.robots.test_am1_ordinary_launch import configuration
    path, value = configuration(tmp_path)
    args = coordinator.parse_args(["--ordinary-config", str(path), "--plan", "ordinary-arms", "--window-seconds", "60"])
    assert args.bindings == tmp_path / "bindings.json" and args.window_seconds == 60.
    assert args.ordinary_arm_profile == value["ordinary_arm_profile"]
    command = coordinator.adapter_command(args)
    assert command[command.index("--ordinary-config")+1] == str(path)
    assert "--prepare-current-rest" not in command
    assert command[command.index("--duration-seconds")+1] == "60.0"


def test_ordinary_profile_preserves_relative_and_admitted_span_right_paths():
    ready, admitted = ordinary_admission()
    for workpose in ("relative", "admitted-span"):
        options = coordinator.parse_args(["--side", "right", "--workpose", workpose,
            "--bindings", "unused-current.json", "--ramp-seconds", "3", "--direction", "increasing"] if workpose == "relative" else
            ["--side", "right", "--workpose", workpose, "--bindings", "unused-current.json", "--ramp-seconds", "3"])
        plan = coordinator.fresh_target(ready, admitted, degrees=options.degrees, direction=options.direction,
            ramp_seconds=options.ramp_seconds, side="right", workpose=workpose)
        assert coordinator.phases_for(plan, options, admitted=admitted)[-1]["op"] == "finish"


def ordinary_active_frame():
    ready, admitted = ordinary_admission()
    state = admitted["snapshot"]
    run, evidence = state["run"], state["evidence"]
    current = run["live_input"]; ack = evidence["accepted_host_action"]
    pi_now = run["deadline"]-run["remaining_s"]
    run.update(status="running", uncertain=False, recovery=None)
    current.update(state="active", valid_until_monotonic_s=pi_now+.2,
                   source=dict(source_id="channel", acquired_at_monotonic_s=10.1))
    current["required_view"].update(required=True, qualified=True, role="chest")
    evidence.update(active_acknowledged=True, required_observation=True)
    ack.update(state="active", acquired_at_monotonic_s=pi_now-.01)
    for point in ack["arm_feedback"]["positions"].values():
        point.update(read_started_at_monotonic_s=pi_now-.03, read_completed_at_monotonic_s=pi_now-.02)
    frame=dict(event="leader_frame", native=state, receipt=dict(accepted=True, effect_admitted=True),
               local_monotonic_s=20., virtual_intent=dict(seq=1, intent_channel="channel",
                   issued_at_perf_counter_s=10., expires_at_perf_counter_s=11.))
    return ready, admitted, frame


def test_ordinary_phase_feedback_reports_actual_residual_without_arrival_credit():
    ready, admitted, frame = ordinary_active_frame()
    workspace = admitted["snapshot"]["run"]["live_input"]["workspace"]
    targets=dict(ready["command_reference"], **{KEY: ready["command_reference"][KEY]+1.})
    actual = coordinator.phase_feedback(frame, run_id=ready["run_id"], accepted=1, now=20.1,
                                        workspace=workspace, targets=targets)
    assert actual[KEY]["normalized"] == ready["command_reference"][KEY]
    assert actual[KEY]["target_error"] == pytest.approx(-1.)
    assert actual[KEY]["raw"] == frame["native"]["evidence"]["accepted_host_action"]["arm_feedback"]["positions"][KEY]["raw"]


@pytest.mark.parametrize("fault", [None, "different-measurement", "missing-acquisition", "invalid-seal", "stale-read", "wrong-policy"])
def test_ordinary_phase_feedback_reports_only_exact_admitted_shoulder_over_calibration(fault):
    from tests.robots.test_am1_live_teleop_contract import ordinary_start_workspace

    ready, _, frame = ordinary_active_frame()
    run = frame["native"]["run"]
    live = run["live_input"]
    workspace = ordinary_start_workspace()
    shoulder = "arm_left_shoulder_lift.pos"
    calibration = workspace["calibration"][shoulder[:-4]]
    measured = normalized_raw(3226, calibration)
    workspace["startup_acquisition"]["measured_reference"][shoulder] = measured
    workspace["reference"][shoulder] = measured
    targets = dict(workspace["startup_acquisition"]["command_reference"])
    live.update(workspace=workspace, reference=dict(workspace["reference"]))
    point = frame["native"]["evidence"]["accepted_host_action"]["arm_feedback"]["positions"][shoulder]
    point.update(raw=3226, normalized=measured)
    if fault == "different-measurement":
        point.update(raw=3227, normalized=normalized_raw(3227, calibration))
    elif fault == "missing-acquisition":
        workspace.pop("startup_acquisition")
    elif fault == "invalid-seal":
        workspace["startup_acquisition"]["displacement_bound_degrees"] = 2.
    elif fault == "stale-read":
        point["read_started_at_monotonic_s"] = run["deadline"] - run["remaining_s"] - .501
    elif fault == "wrong-policy":
        run["ordinary_arm_profile"]["right_elbow_p"] = 64

    if fault is not None:
        with pytest.raises(ValueError):
            coordinator.phase_feedback(frame, run_id=ready["run_id"], accepted=1, now=20.1,
                                       workspace=workspace, targets=targets)
    else:
        original = copy.deepcopy((frame, targets))
        actual = coordinator.phase_feedback(frame, run_id=ready["run_id"], accepted=1, now=20.1,
                                            workspace=workspace, targets=targets)
        assert actual[shoulder]["raw"] == 3226
        assert actual[shoulder]["normalized"] == measured
        assert actual[shoulder]["requested_target"] == 100.
        assert actual[shoulder]["target_error"] == pytest.approx(.252631578947)
        with pytest.raises(ValueError):
            coordinator.validate_target(dict(targets, **{shoulder: measured}), workspace=workspace)
        assert (frame, targets) == original


@pytest.mark.parametrize("fault", ["old", "seq", "view", "ack", "source", "expired", "read", "mapping", "body"])
def test_ordinary_phase_feedback_refuses_stale_or_unqualified_actuals(fault):
    ready, admitted, frame = ordinary_active_frame()
    workspace = admitted["snapshot"]["run"]["live_input"]["workspace"]
    if fault == "old": frame["local_monotonic_s"] = 19.
    elif fault == "seq": frame["virtual_intent"]["seq"] = 2
    elif fault == "view": frame["native"]["run"]["live_input"]["required_view"]["qualified"] = False
    elif fault == "ack": frame["native"]["evidence"]["active_acknowledged"] = False
    elif fault == "source": frame["native"]["run"]["live_input"]["source"]["source_id"] = "other"
    elif fault == "expired": frame["native"]["run"]["live_input"]["valid_until_monotonic_s"] = 0.
    elif fault == "read": frame["native"]["evidence"]["accepted_host_action"]["arm_feedback"]["positions"][KEY]["read_started_at_monotonic_s"] = 99999.
    elif fault == "mapping": frame["native"]["evidence"]["accepted_host_action"]["arm_feedback"]["positions"][KEY]["normalized"] += 1.
    else: frame["native"]["run"]["live_input"]["body"]["x.vel"] = 1.
    with pytest.raises(ValueError):
        coordinator.phase_feedback(frame, run_id=ready["run_id"], accepted=1, now=20.1,
                                   workspace=workspace, targets=ready["command_reference"])


def test_ordinary_full_vectors_use_existing_structured_intent_reader():
    ready, admitted=ordinary_admission(); options=ordinary_options()
    plan=coordinator.fresh_ordinary_targets(ready, admitted, ramp_seconds=3., gripper_direction="closing")
    phases=coordinator.phases_for(plan, options, admitted=admitted)
    reader=VirtualIntentReader("interactive", clock=lambda: 1., allow_controls=True)
    for phase in phases:
        reader.receive_line(coordinator.phase_command(phase, 60.))
        intent=reader.take()
        assert intent["op"] == phase["op"]
        if phase["op"] == "move": assert intent["targets"] == phase["targets"]


def test_ordinary_plan_keeps_original_reference_without_exact_measured_reset_gate():
    ready, admitted = ordinary_admission()
    native=admitted["snapshot"]["run"]["live_input"]["workspace"]
    point=admitted["snapshot"]["evidence"]["accepted_host_action"]["arm_feedback"]["positions"][KEY]
    point["raw"] += 1
    point["normalized"] = normalized_raw(point["raw"], native["calibration"][KEY[:-4]])
    plan=coordinator.fresh_ordinary_targets(ready, admitted, ramp_seconds=3., gripper_direction="closing")
    assert plan["origin"] == ready["command_reference"]
    assert plan["initial_feedback"][KEY] == point
    assert plan["initial_feedback"][KEY]["normalized"] != plan["origin"][KEY]


@pytest.mark.parametrize("fault", ["source", "bench", "view", "policy"])
def test_ordinary_plan_requires_normal_native_policy_authority(fault):
    ready, admitted=ordinary_admission(); state=admitted["snapshot"]
    if fault == "source": state["evidence"]["source"] = "fake"
    elif fault == "bench": state["run"]["bench_continuity"] = True
    elif fault == "view": state["run"]["live_input"]["required_view"]["required"] = False
    else: state["run"]["ordinary_arm_profile"]["right_elbow_p"] = 31
    with pytest.raises(ValueError):
        coordinator.fresh_ordinary_targets(ready, admitted, ramp_seconds=3., gripper_direction="closing")


def test_ordinary_plan_preserves_distinct_qualified_goal_and_measured_reference():
    ready, admitted=ordinary_admission()
    native=admitted["snapshot"]["run"]["live_input"]
    key="arm_left_shoulder_lift.pos"
    measured=dict(native["workspace"]["reference"])
    command=dict(ready["command_reference"]); command[key] += .01
    native.update(cursor=dict(command), accepted_target=dict(command))
    ready["command_reference"]=dict(command)
    plan=coordinator.fresh_ordinary_targets(ready, admitted, ramp_seconds=3., gripper_direction="closing")
    assert plan["origin"] == command and plan["workspace"]["reference"] == measured
    assert plan["origin"][key] != plan["workspace"]["reference"][key]
    assert coordinator.phases_for(plan, ordinary_options(), admitted=admitted)[-2]["op"] == "return"


@pytest.mark.parametrize("fault",[None,"metadata-absent","raw-identity","wrong-key","foreign-meta"])
def test_ordinary_normal_start_first_move_uses_only_exact_qualified_legal_origin(fault):
    from tests.robots.test_am1_live_teleop_contract import ordinary_start_workspace
    ready,admitted=ordinary_admission();state=admitted["snapshot"]
    workspace=ordinary_start_workspace();current=state["run"]["live_input"]
    commands=dict(workspace["startup_acquisition"]["command_reference"])
    current.update(workspace=workspace,reference=dict(workspace["reference"]),cursor=dict(commands),accepted_target=dict(commands))
    points={}
    for key,q in workspace["reference"].items():
        cal=workspace["calibration"][key[:-4]];grip=key.endswith("gripper.pos")
        raw=round(cal[3]+(q+(0 if grip else 100))*(cal[4]-cal[3])/(100 if grip else 200))
        points[key]=dict(raw=raw,normalized=q)
        workspace["bounds"][key]["rate_per_s"]=20. if grip else 5*4096/360*200/(cal[4]-cal[3])
    state["evidence"]["accepted_host_action"]["arm_feedback"]["positions"]=points
    ready.update(command_reference=commands,bounds=copy.deepcopy(workspace["bounds"]),units=dict(workspace["units"]))
    if fault == "metadata-absent":workspace.pop("startup_acquisition")
    elif fault == "raw-identity":points["arm_left_shoulder_lift.pos"]["raw"]+=1
    elif fault == "wrong-key":points["arm_right_shoulder_lift.pos"]["raw"]=3265
    elif fault == "foreign-meta":workspace["startup_acquisition"]["extra"]=True
    original=copy.deepcopy((ready,admitted))
    if fault:
        with pytest.raises((ValueError,RuntimeError)):coordinator.fresh_ordinary_targets(ready,admitted,ramp_seconds=3.,gripper_direction="closing")
    else:
        plan=coordinator.fresh_ordinary_targets(ready,admitted,ramp_seconds=3.,gripper_direction="closing")
        assert plan["origin"]==commands and plan["workspace"]["reference"]["arm_left_shoulder_lift.pos"]>100
        assert coordinator.phases_for(plan,ordinary_options())[0]["targets"]["arm_left_shoulder_lift.pos"]==100.
    assert (ready,admitted)==original



SHORT_KEYS={"arm_left_elbow_flex.pos","arm_right_elbow_flex.pos","arm_left_gripper.pos","arm_right_gripper.pos"}


def ordinary_short_options(*extra):
    return coordinator.parse_args(["--plan","ordinary-short","--operating-profile","ordinary-am1-arms-v1",
        "--bindings","unused-current.json",*extra])


def ordinary_wrist_midpoint_admission():
    ready,admitted=ordinary_admission();state=admitted["snapshot"];current=state["run"]["live_input"]
    key="arm_right_wrist_flex.pos";q=normalized_raw(3134,current["workspace"]["calibration"][key[:-4]])
    for values in (current["reference"],current["workspace"]["reference"],current["cursor"],current["accepted_target"],ready["command_reference"]):values[key]=q
    state["evidence"]["accepted_host_action"]["arm_feedback"]["positions"][key].update(raw=3134,normalized=q)
    return ready,admitted


def test_ordinary_short_cli_defaults_are_explicit_existing_profile_and_no_native_mode():
    options=ordinary_short_options()
    assert options.ramp_seconds==3. and options.dwell_seconds==2. and options.window_seconds==90.
    assert options.ordinary_arm_profile=={"profile":"ordinary-am1-arms-v1","right_elbow_p":16}
    command=coordinator.adapter_command(options)
    assert "--plan" not in command and "--prepare-current-rest" not in command
    assert command[command.index("--operating-profile")+1]=="ordinary-am1-arms-v1"


def test_ordinary_short_keeps_all12_validation_but_only_four_full_excursions():
    ready,admitted=ordinary_wrist_midpoint_admission();before=copy.deepcopy((ready,admitted))
    with pytest.raises(ValueError):coordinator.fresh_ordinary_targets(ready,admitted,ramp_seconds=3.,gripper_direction="closing")
    plan=coordinator.fresh_ordinary_targets(ready,admitted,ramp_seconds=3.,gripper_direction="closing",short=True)
    assert set(plan["initial_feedback"])==set(plan["origin"]) and len(plan["origin"])==12
    assert set(plan["excursions"])==SHORT_KEYS
    for key in plan["origin"]:
        if key in SHORT_KEYS:
            assert plan["excursions"][key]["amount"]==(20. if "gripper" in key else 4.)
        else:assert plan["targets"][key]==plan["origin"][key]
    assert (ready,admitted)==before


def test_ordinary_short_declares_15active_2paused_original_return_after_fresh_resume():
    ready,admitted=ordinary_wrist_midpoint_admission();options=ordinary_short_options()
    plan=coordinator.fresh_ordinary_targets(ready,admitted,ramp_seconds=3.,gripper_direction="closing",short=True)
    phases=coordinator.phases_for(plan,options,admitted=admitted)
    assert [p["op"] for p in phases]==["move","move","pause","resume","return","finish"]
    assert sum(p.get("ramp",0)+p.get("dwell",0) for p in phases if p["op"] not in {"pause","resume"})==15.
    assert phases[2]["dwell"]==2.
    for key in plan["origin"]:
        assert phases[0]["targets"][key]==(plan["targets"][key] if "elbow_flex" in key else plan["origin"][key])
        assert phases[1]["targets"][key]==plan["targets"][key]
    assert all(p.get("complete_before_advance") is True for p in phases if p["op"] in {"move","return"})
    reader=VirtualIntentReader("interactive",clock=lambda:1.,allow_controls=True)
    for phase in phases:
        reader.receive_line(coordinator.phase_command(phase,60.));command=reader.take()
        assert command["op"]==phase["op"]
        if command["op"]=="move":assert set(command["targets"])==set(plan["origin"])


@pytest.mark.parametrize("fault",["unselected_mapping","unselected_identity","source","owner","hold","view","deadline","elbow_rate","gripper_rate","gripper_fit"])
def test_ordinary_short_preserves_all_joint_feedback_and_normal_authority_guards(fault):
    ready,admitted=ordinary_wrist_midpoint_admission();state=admitted["snapshot"];native=state["run"]["live_input"]["workspace"]
    points=state["evidence"]["accepted_host_action"]["arm_feedback"]["positions"]
    if fault=="unselected_mapping":points["arm_right_wrist_flex.pos"]["normalized"]+=.1
    elif fault=="unselected_identity":points["arm_right_wrist_flex.pos"]["raw"]=True
    elif fault=="source":state["evidence"]["source"]="foreign"
    elif fault=="owner":state["controller"]["device_id"]=None
    elif fault=="hold":state["evidence"]["hold_acknowledged"]=False
    elif fault=="view":state["run"]["live_input"]["required_view"]["required"]=False
    elif fault=="deadline":state["run"]["deadline"]+=301.
    elif fault=="elbow_rate":native["bounds"]["arm_right_elbow_flex.pos"]["rate_per_s"]=.0001
    elif fault=="gripper_rate":native["bounds"]["arm_right_gripper.pos"]["rate_per_s"]=.0001
    else:
        key="arm_right_gripper.pos";q=native["bounds"][key]["min"]
        for values in (native["reference"],state["run"]["live_input"]["reference"],state["run"]["live_input"]["cursor"],state["run"]["live_input"]["accepted_target"],ready["command_reference"]):values[key]=q
        points[key].update(raw=2118,normalized=q)
    with pytest.raises((ValueError,RuntimeError)):
        coordinator.fresh_ordinary_targets(ready,admitted,ramp_seconds=3.,gripper_direction="closing",short=True)


@pytest.mark.parametrize("extra",[["--side","right"],["--workpose","admitted-span"],["--degrees","3"],["--gripper-cycle"],["--gripper-direction","opening"],["--approach-from-below"]])
def test_ordinary_short_rejects_legacy_selectors_and_nonclosing_task(extra):
    with pytest.raises(SystemExit):ordinary_short_options(*extra)


def test_ordinary_short_preserves_distinct_measured_reference_and_legal_held_goals():
    ready,admitted=ordinary_wrist_midpoint_admission();current=admitted["snapshot"]["run"]["live_input"]
    key="arm_left_shoulder_lift.pos";commands=dict(ready["command_reference"]);commands[key]+=.01
    current.update(cursor=dict(commands),accepted_target=dict(commands));ready["command_reference"]=commands
    original=copy.deepcopy(current["workspace"]["reference"])
    plan=coordinator.fresh_ordinary_targets(ready,admitted,ramp_seconds=3.,gripper_direction="closing",short=True)
    assert plan["origin"]==commands and plan["workspace"]["reference"]==original
    assert plan["targets"][key]==commands[key]


@pytest.mark.parametrize("short",[True,False])
def test_ordinary_short_and_full_composition_cannot_be_mixed(short):
    ready,admitted=ordinary_admission()
    plan=coordinator.fresh_ordinary_targets(ready,admitted,ramp_seconds=3.,gripper_direction="closing",short=short)
    options=ordinary_options() if short else ordinary_short_options()
    with pytest.raises(ValueError):coordinator.phases_for(plan,options,admitted=admitted)



def test_ordinary_short_requires_ordinary_profile_and_a_closed_boolean_composition():
    with pytest.raises(SystemExit):coordinator.parse_args(["--plan","ordinary-short","--bindings","unused.json"])
    ready,admitted=ordinary_admission()
    with pytest.raises(ValueError):coordinator.fresh_ordinary_targets(ready,admitted,ramp_seconds=3.,gripper_direction="closing",short=1)



def test_explicit_right_wrist_cli_declares_bounded_full_ordinary_option():
    options=ordinary_options("--right-wrist-flex-degrees","1.5")
    assert options.right_wrist_flex_degrees==1.5
    assert ordinary_options().right_wrist_flex_degrees==3.
    assert ordinary_short_options().right_wrist_flex_degrees is None


def test_explicit_right_wrist_plan_fits_retained_midpoint_without_changing_reference():
    ready,admitted=ordinary_wrist_midpoint_admission();before=copy.deepcopy((ready,admitted))
    with pytest.raises(ValueError):coordinator.fresh_ordinary_targets(ready,admitted,ramp_seconds=3.,gripper_direction="closing")
    plan=coordinator.fresh_ordinary_targets(ready,admitted,ramp_seconds=3.,gripper_direction="closing",right_wrist_flex_degrees=1.5)
    key="arm_right_wrist_flex.pos";cal=plan["workspace"]["calibration"][key[:-4]]
    assert plan["excursions"][key]["amount"]==1.5
    assert plan["targets"][key]-plan["origin"][key]==pytest.approx(1.5*4096/360*200/(cal[4]-cal[3]))
    assert (ready,admitted)==before



@pytest.mark.parametrize("raw",[3127,3134,3147,3150,3167])
def test_explicit_right_wrist_fixed_corridor_admits_whole_1p5_degree_excursion(raw):
    ready,admitted=ordinary_wrist_midpoint_admission();state=admitted["snapshot"];current=state["run"]["live_input"]
    key="arm_right_wrist_flex.pos";cal=current["workspace"]["calibration"][key[:-4]];q=normalized_raw(raw,cal)
    for values in (current["reference"],current["workspace"]["reference"],current["cursor"],current["accepted_target"],ready["command_reference"]):values[key]=q
    state["evidence"]["accepted_host_action"]["arm_feedback"]["positions"][key].update(raw=raw,normalized=q)
    before=copy.deepcopy((ready,admitted))
    plan=coordinator.fresh_ordinary_targets(ready,admitted,ramp_seconds=3.,gripper_direction="closing",right_wrist_flex_degrees=1.5)
    target=cal[3]+(plan["targets"][key]+100)/200*(cal[4]-cal[3])
    assert 3127<=target<=3167 and abs(target-raw)==pytest.approx(17.0666666667)
    assert plan["right_wrist_flex_degrees"]==1.5
    for joint,item in plan["excursions"].items():
        expected=1.5 if joint==key else 20. if "gripper" in joint else 3. if "wrist_flex" in joint else 4.
        assert item["amount"]==expected
    assert (ready,admitted)==before


@pytest.mark.parametrize("plan",["ordinary-short","out-return","grasp-return"])
def test_explicit_right_wrist_cli_refuses_all_other_compositions(plan):
    with pytest.raises(SystemExit):coordinator.parse_args(["--plan",plan,"--bindings","unused.json","--right-wrist-flex-degrees","1.5"])


@pytest.mark.parametrize("value",["0","2","nan","inf"])
def test_explicit_right_wrist_cli_refuses_unsupported_or_nonfinite_choice(value):
    with pytest.raises(SystemExit):ordinary_options("--right-wrist-flex-degrees",value)


@pytest.mark.parametrize("value",[True,False,float("nan"),float("inf"),2.,None])
def test_explicit_right_wrist_planner_requires_typed_bounded_value(value):
    ready,admitted=ordinary_wrist_midpoint_admission()
    with pytest.raises(ValueError):coordinator.fresh_ordinary_targets(ready,admitted,ramp_seconds=3.,gripper_direction="closing",right_wrist_flex_degrees=value)


def test_explicit_right_wrist_short_cannot_select_wrist_even_through_planner_api():
    ready,admitted=ordinary_wrist_midpoint_admission()
    with pytest.raises(ValueError):coordinator.fresh_ordinary_targets(ready,admitted,ramp_seconds=3.,gripper_direction="closing",short=True,right_wrist_flex_degrees=1.5)


@pytest.mark.parametrize("fault",["default_options","selected_options","metadata","target"])
def test_explicit_right_wrist_plan_and_options_must_match(fault):
    ready,admitted=ordinary_admission()
    amount=3. if fault=="selected_options" else 1.5
    plan=coordinator.fresh_ordinary_targets(ready,admitted,ramp_seconds=3.,gripper_direction="closing",right_wrist_flex_degrees=amount)
    options=ordinary_options() if fault in ("default_options","metadata") else ordinary_options("--right-wrist-flex-degrees","1.5")
    if fault=="metadata":plan["right_wrist_flex_degrees"]=3.
    elif fault=="target":
        key="arm_right_wrist_flex.pos";cal=plan["workspace"]["calibration"][key[:-4]]
        plan["targets"][key]=plan["origin"][key]+3.*4096/360*200/(cal[4]-cal[3])
    with pytest.raises(ValueError):coordinator.phases_for(plan,options,admitted=admitted)


@pytest.mark.parametrize("fault",["bounds","rate","reference"])
def test_explicit_right_wrist_keeps_existing_workspace_rate_and_original_reference_guards(fault):
    ready,admitted=ordinary_wrist_midpoint_admission();current=admitted["snapshot"]["run"]["live_input"];key="arm_right_wrist_flex.pos"
    if fault=="bounds":current["workspace"]["raw_bounds"][key][0]+=1
    elif fault=="rate":
        current["workspace"]["bounds"][key]["rate_per_s"]=.0001
        ready["bounds"][key]["rate_per_s"]=.0001
    else:ready["command_reference"][key]+=.01
    with pytest.raises((ValueError,RuntimeError)):coordinator.fresh_ordinary_targets(ready,admitted,ramp_seconds=3.,gripper_direction="closing",right_wrist_flex_degrees=1.5)


@pytest.mark.parametrize("external",[False,True])
def test_explicit_right_wrist_main_declares_before_start_and_uses_existing_pause_resume_relay(tmp_path,monkeypatch,external):
    test_ordinary_coordinator_real_relay_waits_for_pause_ack_and_fresh_resume_before_new_move(tmp_path,monkeypatch,external,"ordinary-arms",_right_wrist_degrees=1.5)
    console=next((tmp_path/"repo/.cache/am1-knowledge-reuse-18").glob("*.console.jsonl"))
    records=[json.loads(line) for line in console.read_text().splitlines()]
    declared=next(row for row in records if row["event"]=="local_activity_declared")
    assert declared["right_wrist_flex_degrees"]==1.5 and declared["view"]=="normal required Chest"
    assert records.index(declared)<next(i for i,row in enumerate(records) if row["event"]=="virtual_input_opened")
    fresh=next(row for row in records if row["event"]=="local_fresh_targets")
    assert fresh["right_wrist_flex_degrees"]==1.5 and fresh["ordinary_excursions"]["arm_right_wrist_flex.pos"]["amount"]==1.5
    assert fresh["planned_active_seconds"]==45. and fresh["planned_paused_seconds"]==2.
    assert "--right-wrist-flex-degrees" not in coordinator.adapter_command(ordinary_options("--right-wrist-flex-degrees","1.5"))
