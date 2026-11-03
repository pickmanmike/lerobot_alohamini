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


def admission(grip="closing", *, elbow_raw=1090):
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


def grasp_options(*extra):
    return coordinator.parse_args([
        "--plan", "grasp-return", "--gripper-direction", "admitted",
        "--degrees", "4", "--direction", "increasing",
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


@pytest.mark.parametrize("grip", ["closing", "opening"])
def test_grasp_return_keeps_one_legal_interval_and_retracts_closed_to_original(grip):
    ready, admitted = admission(grip)
    original = copy.deepcopy(admitted)
    plan, phases = planned(ready, admitted, grasp_options())
    names = [phase["name"] for phase in phases]
    expected = ["grasp_close", "grasp_carry", "grasp_return_original_closed", "grasp_release", "finish"]
    if grip == "opening":
        expected.insert(0, "grasp_prepare_open")
    assert names == expected
    assert sum(phase.get("ramp", 0) + phase.get("dwell", 0) for phase in phases) == (41. if grip == "opening" else 36.)
    q0 = ready["command_reference"][KEY]
    # Four physical encoder degrees through the existing side-specific calibration.
    assert plan["target"] - q0 == pytest.approx(45.51111111111111 * 200 / 2209)
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


@pytest.mark.parametrize("args", [
    ["--gripper-direction", "closing"], ["--degrees", "5"], ["--direction", "decreasing"],
    ["--ramp-seconds", "8"], ["--comparison-dwell-seconds", "4"], ["--dwell-seconds", "4"],
])
def test_grasp_selection_rejects_unapproved_composition_before_process(args):
    with pytest.raises(SystemExit):
        grasp_options(*args)


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
    plan, phases = planned(ready, admitted, options)
    assert [phase["name"] for phase in phases] == expected
    returns = [phase for phase in phases if phase["name"] == "return_original"]
    assert len(returns) == 1 and returns[0]["op"] == "return"
    assert plan["q0"] == ready["command_reference"][KEY]


@pytest.mark.parametrize("unsupported", [False, True])
def test_coordinator_dispatches_complete_grasp_from_status_without_manual_pacing(tmp_path, monkeypatch, unsupported):
    ready, admitted = admission("ambiguous" if unsupported else "opening")
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
            for i, duration in enumerate([] if unsupported else [5., 5., 13., 13., 5.]):
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
        "--direction", "increasing", "--ramp-seconds", "10", "--comparison-dwell-seconds", "3",
        "--dwell-seconds", "3", "--window-seconds", "90", "--intent-ttl-seconds", "60",
        "--bindings", str(root / "current-bindings.json"),
    ]) == 0
    assert len(launches) == 1 and launches[0][1]["cwd"] == root
    argv = launches[0][0]
    assert argv[argv.index("-m")+1] == "tools.am1_leader_adapter"
    assert argv[argv.index("--duration-seconds")+1] == "90.0"
    assert all(not command.startswith("return ") for command in commands)
    assert len(commands) == (1 if unsupported else 6)
    records = [json.loads(line) for line in next((root / ".cache/am1-knowledge-reuse-18").glob("*.console.jsonl")).read_text().splitlines()]
    declared = next(row for row in records if row["event"] == "local_activity_declared")
    assert declared["gripper_interval_policy"] == "one legal10-point interval; prepare opening when only opening is admitted"
    assert declared["view"] == "normal required Chest" and declared["body_lift_intent"] == "zero"
    if unsupported:
        assert not intents
        assert any(row["event"] == "local_excursion_unsupported" for row in records)
        assert not any(row["event"] == "local_fresh_targets" for row in records)
    else:
        planned_record = next(row for row in records if row["event"] == "local_fresh_targets")
        assert planned_record["planned_active_seconds"] == 41.
        assert planned_record["gripper_interval"] == {"open": 10., "closed": 0., "opening_preparation": True}
        assert [intent["seq"] for intent in intents] == [1, 2, 3, 4, 5]
        assert intents[3]["targets"] == {"arm_left_elbow_flex.pos": ready["command_reference"][KEY],
                                        "arm_left_gripper.pos": 0.}
        assert intents[-1]["targets"][GRIP] == 10.
        assert len({intent["issued_at_perf_counter_s"] for intent in intents}) == 5


def test_missing_current_bindings_refuses_before_adapter_launch(tmp_path, monkeypatch):
    (tmp_path / ".cache/am1-knowledge-reuse-18").mkdir(parents=True)
    monkeypatch.setattr(coordinator, "ROOT", tmp_path)
    monkeypatch.setattr(coordinator.subprocess, "Popen",
                        lambda *args, **kwargs: pytest.fail("missing bindings reached adapter launch"))
    with pytest.raises(SystemExit):
        coordinator.main(["--plan", "out-return"])
    assert not list((tmp_path / ".cache/am1-knowledge-reuse-18").glob("*.console.jsonl"))
