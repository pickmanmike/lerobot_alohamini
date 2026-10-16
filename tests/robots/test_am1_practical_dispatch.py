"""Ordinary practical page recipes keep closed authority outside experiment files."""

from types import SimpleNamespace

import pytest

from tests.robots.test_am1_elbow_integral import RIGHT_P20_RUN, RIGHT_P20_HOST
from tests.robots.test_am1_physical_executor import module


RECIPES = ("physical-live-right-elbow-practical-p16", "physical-live-left-elbow-practical-p16")


@pytest.mark.parametrize("recipe", RECIPES)
def test_ordinary_start_does_not_consume_or_require_an_external_gain_request(tmp_path, monkeypatch, recipe):
    import lerobot.robots.alohamini.am1_elbow_integral as integral

    m = module()
    state, root = tmp_path / "state", tmp_path / "runs"
    state.mkdir(mode=0o700)
    root.mkdir(mode=0o700)
    request = state / "practical13-right-request.json"
    request.write_text("original experimental authority stays untouched", encoding="utf8")
    provider = object.__new__(m.ProtectedHostRun)
    provider.config = {"physical_state_directory": str(state), "run_root": str(root), "host_head": "a" * 40,
                       "host_repository": str(tmp_path), "host_python": "unused-python", "camera_python": "unused-python",
                       "camera_repository": str(tmp_path), "camera_config": str(tmp_path / "config"),
                       "camera_credentials": str(tmp_path / "credentials"), "camera_binary": str(tmp_path / "binary"),
                       "camera_state": str(tmp_path / "camera")}
    provider.observation = SimpleNamespace()
    monkeypatch.setattr(provider, "_validate_source", lambda: None)
    monkeypatch.setattr(m, "wait_required_camera", lambda *args: None)
    monkeypatch.setattr(integral, "consume_right_p20_request", lambda *a, **k: pytest.fail("ordinary Start must not consume experiment authority"))
    owned = []
    def unit(name, *args, **kwargs):
        owned.append((name, args, kwargs))
        if "host" in name:
            raise OSError("stop before hardware host creation")
        return SimpleNamespace()
    monkeypatch.setattr(m, "OwnedUnit", unit)
    with pytest.raises(OSError, match="before hardware"):
        provider.start(RIGHT_P20_RUN, RIGHT_P20_HOST, SimpleNamespace(name=recipe, live_s=180), lambda: None)
    host_command = owned[-1][1][1]
    assert "--am1-right-p20-trial-file" not in host_command
    assert owned[-1][1][2]["AM1_VISIBLE_CONTROL_RECIPE"] == recipe
    assert request.read_text(encoding="utf8") == "original experimental authority stays untouched"


class OrdinaryNativeHost:
    """Existing native host fixture with the combined, actually mapped bench context."""
    def __new__(cls):
        from tests.robots.test_am1_live_native import ExecutorNativeHost
        from tests.robots.test_am1_practical_native import PracticalMotor
        from lerobot.robots.alohamini.alohamini_host import AM1LocalControl

        host = ExecutorNativeHost()
        host.robot = PracticalMotor()
        host.robot.configure()
        def begin(run_id, incarnation, recipe, cancel):
            cancel()
            host.control = AM1LocalControl(run_id=run_id, host_incarnation=incarnation,
                                           live=True, live_recipe=recipe.name)
        host.start = begin
        return host


@pytest.fixture(params=RECIPES)
def ordinary_authority(tmp_path, request):
    from tests.robots.test_am1_physical_executor import executor, until
    from tests.robots.test_am1_persistent_session import start
    from tools.am1_session_core import SessionAuthority

    host = OrdinaryNativeHost()
    e = executor(host)
    a = SessionAuthority(tmp_path, executor=e)
    try:
        r = start(a, request.param)
        assert r["accepted"], r
        until(lambda: (a.tick() or a.run["status"] == "paused"))
        assert a.run["status"] == "paused", a.run
        yield a, e, host, r
    finally:
        a.close()
        if e.worker:
            e.worker.join(2)


@pytest.mark.parametrize("tamper", ["target", "native_corridor", "native_descriptor", "native_origin", "native_rebase"])
def test_owner_validates_closed_ordinary_geometry_before_apply(ordinary_authority, monkeypatch, tamper):
    import copy

    from examples.alohamini.am1_session_contract import PRACTICAL_ELBOW_PRESETS
    from tests.robots.test_am1_persistent_session import command, physical_live_controls

    a, e, host, r = ordinary_authority
    base = physical_live_controls(a, r)
    preset = PRACTICAL_ELBOW_PRESETS[a.run["recipe"]]
    spec, w = preset.spec, preset.workspace
    evidence = copy.deepcopy(e.evidence())
    target = w.normalize(w.command_max_raw)
    if tamper == "target":
        target += .1
    elif tamper == "native_corridor":
        evidence["live_input"]["corridor_max_norm"] += .1
    elif tamper == "native_descriptor":
        evidence["live_input"]["workspace"]["context_sha256"] = "a" * 64
    else:
        evidence["live_input"]["measured_origin_raw"] += 1
        if tamper == "native_rebase":
            evidence["live_input"]["reference_norm"] = w.normalize(evidence["live_input"]["measured_origin_raw"])
    monkeypatch.setattr(a, "_evidence", lambda: copy.deepcopy(evidence))
    calls = []
    monkeypatch.setattr(e, "apply", lambda *args, **kwargs: calls.append((args, kwargs)) or True)
    grant = a.handle(command("grant", **base), "owner")
    if not grant["accepted"]:
        assert tamper != "target" and a.run["first_cause"] == "physical_live_preset_geometry_mismatch"
        assert calls == []
        return
    answer = a.handle(command("input", **base, grant_id=grant["grant_id"], seq=1,
                              target={spec.joint: target}), "owner")
    assert not answer["accepted"], "owner must enforce closed geometry before dispatching to its executor"
    assert calls == []


def test_ordinary_lifecycle_streams_fresh_targets_rejects_replay_and_cancels_rearm(ordinary_authority):
    import uuid
    from examples.alohamini.am1_session_contract import PRACTICAL_ELBOW_PRESETS
    from tests.robots.test_am1_physical_executor import until
    from tests.robots.test_am1_persistent_session import command, physical_live_controls

    a, e, host, r = ordinary_authority
    base = physical_live_controls(a, r)
    preset = PRACTICAL_ELBOW_PRESETS[a.run["recipe"]]
    initial = a.snapshot()["run"]
    grant = a.handle(command("grant", **base), "owner")
    assert grant["expires"] - grant["issued"] == pytest.approx(.25)
    request = command("input", **base, grant_id=grant["grant_id"], seq=1,
                      target={preset.spec.joint: preset.target_norm(10)})
    assert a.handle(request, "owner")["accepted"]
    until(lambda: host.control.live_snapshot().get("last_input_seq") == 1)
    assert host.control.live_snapshot()["valid_until_monotonic_s"] == grant["expires"]
    assert not a.handle(request, "owner")["accepted"]
    assert not a.handle(dict(request, seq=2, host_incarnation=str(uuid.uuid4())), "owner")["accepted"]
    until(lambda: a.snapshot()["evidence"]["active_acknowledged"])
    release = a.handle(command("release_input", **base, release_kind="voluntary_jog"), "owner")
    assert release["accepted"]
    released = a.run["intent_revision"]
    assert a.snapshot()["run"]["live_input"]["rearm"]["eligible"] is True
    a.handle(command("pause", run_id=r["run_id"]), "spectator")
    stale = a.handle(command("connect", **base, rearm_of_intent_revision=released), "owner")
    assert not stale["accepted"]
    fresh = physical_live_controls(a, r)
    assert a.run["deadline"] == initial["deadline"]
    assert a.snapshot()["run"]["live_input"]["reference_norm"] == initial["live_input"]["reference_norm"]
    grant = a.handle(command("grant", **fresh), "owner")
    assert a.handle(command("input", **fresh, grant_id=grant["grant_id"], seq=1,
                            target={preset.spec.joint: preset.target_norm(0)}), "owner")["accepted"]
    assert e.task is None


@pytest.mark.parametrize("recipe", RECIPES)
def test_owner_rejects_widened_ordinary_native_admission(tmp_path, recipe):
    from tests.robots.test_am1_physical_executor import executor, until
    from tests.robots.test_am1_persistent_session import start
    from tools.am1_session_core import SessionAuthority

    host = OrdinaryNativeHost()
    poll = host.poll
    def foreign_geometry(previous):
        sample = poll(previous)
        live = sample.get("ack", {}).get("live_input", {})
        if "corridor_max_norm" in live:
            live["corridor_max_norm"] += .1
        return sample
    host.poll = foreign_geometry
    e = executor(host)
    a = SessionAuthority(tmp_path, executor=e)
    try:
        accepted = start(a, recipe)
        assert accepted["accepted"]
        until(lambda: e.evidence()["lifecycle"]["phase"] == "live")
        a.tick()
        assert a.run["first_cause"] == "physical_live_preset_geometry_mismatch"
        assert a.run["status"] in {"finishing", "faulted"} and a.run["intent"] is None
    finally:
        a.close()
        e.worker.join(2)
