"""Bounded original output over the existing supervisor, without hardware."""

import base64
import json
from io import StringIO
from types import SimpleNamespace

import pytest

from tools import am1_session as session
from tools.am1_console import ConsoleSessionAdapter, SessionMismatchError
from tools.am1_session_remote import BestEffortReporter, OwnedChild, RemoteSupervisor


IDENTITY = "20261001T120000-deadbeef"


def supervisor(tmp_path, writer):
    args = SimpleNamespace(state_directory=str(tmp_path), session_id=IDENTITY,
                           camera_head="c", motor_head="m", session_head="s",
                           log_directory=str(tmp_path))
    owner = RemoteSupervisor(args, BestEffortReporter(writer))
    path = tmp_path / "am1-local-host-test.log"
    owner.children["host"] = OwnedChild("host", None, 1, tmp_path / "control", str(path))
    return owner, path


def adapter(tmp_path):
    value = ConsoleSessionAdapter(SimpleNamespace(windows_log_directory=tmp_path), tmp_path, session,
                                  bridge_factory=lambda *args: None)
    value._on_created(IDENTITY)
    return value


def packet(data, offset=0, **fields):
    return dict(event="process_output", session_id=IDENTITY, source="host",
                path="/logs/am1-local-host-test.log", offset=offset, acquired_at_ns=123,
                data_base64=base64.b64encode(data).decode(), **fields)


def test_owned_output_forwards_exact_bytes_incrementally_and_bounded(tmp_path):
    events = []
    owner, path = supervisor(tmp_path, events.append)
    raw = ("WARNING: original output é\n" * 40).encode()
    path.write_bytes(raw)
    owner.forward_output(now=1)
    first = events[0]
    assert first["session_id"] == IDENTITY and first["source"] == "host"
    assert first["path"] == str(path) and first["offset"] == 0
    assert len(json.dumps(first).encode()) < 4096
    assert len(base64.b64decode(first["data_base64"])) <= 1536
    owner.forward_output(now=1.01)  # rate bounded, not every monitoring tick
    assert len(events) == 1
    with path.open("ab") as stream:
        stream.write(b"next\n")
    owner.forward_output(now=1.3)
    assert events[1]["offset"] == len(base64.b64decode(first["data_base64"]))
    assert raw + b"next\n" == b"".join(base64.b64decode(event["data_base64"]) for event in events)


def test_fast_log_display_reads_latest_chunk_not_a_freshly_labeled_old_backlog(tmp_path):
    events = []
    owner, path = supervisor(tmp_path, events.append)
    path.write_bytes(b"old\n" * 6000 + b"latest marker\n")
    owner.forward_output(now=1)
    payload = events[0]
    assert payload["offset"] == path.stat().st_size - 1536
    assert base64.b64decode(payload["data_base64"]).endswith(b"latest marker\n")
    value = adapter(tmp_path)
    value._emit(payload)
    assert value.read_output("host", IDENTITY)["truncated"]


def test_slow_display_is_best_effort_and_reports_gap_on_next_payload(tmp_path):
    def blocked(_):
        raise BlockingIOError
    owner, path = supervisor(tmp_path, blocked)
    path.write_bytes(b"x" * 4000)
    owner.forward_output(now=1)
    events = []
    owner.reporter = BestEffortReporter(events.append)
    with path.open("ab") as stream:
        stream.write(b"next\n")
    owner.forward_output(now=1.3)
    assert events[0]["offset"] > 0  # receiver exposes missing prefix
    owner.controller_stop_requested.set()
    assert owner.controller_stop_requested.is_set()  # display never waits/retries


def test_output_rejects_unowned_paths_and_symlinks(tmp_path):
    events = []
    owner, path = supervisor(tmp_path, events.append)
    owner.children["host"].log_path = str(tmp_path.parent / "outside.log")
    owner.forward_output(now=1)
    assert not events


def test_output_does_not_fill_lifecycle_queue(tmp_path):
    seen = []
    remote = session.SSHRemote(SimpleNamespace(), IDENTITY, tmp_path, telemetry_sink=seen.append)
    remote.process = SimpleNamespace(stdout=StringIO(json.dumps(packet(b"original\n")) + "\n"
                                                   + '{"event":"host_ready"}\n'))
    remote._read_events()
    assert [event["event"] for event in seen] == ["process_output"]
    assert remote.events.qsize() == 1
    assert remote.events.get()["event"] == "host_ready"


def test_live_output_available_before_manifest_and_retained_not_falsely_live(tmp_path):
    value = adapter(tmp_path)
    value._worker = SimpleNamespace(is_alive=lambda: True)
    value._emit(packet(b"original host\n"))
    result = value.read_output("host", IDENTITY)
    assert result["text"] == "original host\n" and result["session_id"] == IDENTITY
    assert result["source"] == "host" and result["path"] == "/logs/am1-local-host-test.log"
    assert result["state"] == "Live forwarded" and result["acquired_at_ns"] == 123
    value._worker = None
    assert value.read_output("host", IDENTITY)["state"] == "Retained output"
    assert not (tmp_path / f"am1-session-{IDENTITY}" / "session-summary.json").exists()


def test_output_retention_gaps_and_malformed_packets_are_explicit(tmp_path):
    value = adapter(tmp_path)
    value._emit(packet(b"first\n"))
    value._emit(packet(b"after gap\n", offset=100))
    result = value.read_output("host", IDENTITY)
    assert result["truncated"] and "after gap" in result["text"]
    value._emit({**packet(b"wrong"), "session_id": "other"})
    value._emit({**packet(b"bad"), "data_base64": "!", "offset": 110})
    assert "wrong" not in value.read_output("host", IDENTITY)["text"]
    for index in range(200):
        value._emit(packet(b"x" * 1536, offset=1000 + index * 1536))
    bounded = value.read_output("host", IDENTITY)
    assert len(bounded["text"].encode()) <= 128000 and bounded["truncated"]
    with pytest.raises(SessionMismatchError):
        value.read_output("host", "20261001T120001-deadbeef")


def test_local_client_output_tail_has_exact_session_and_size_bound(tmp_path):
    value = adapter(tmp_path)
    directory = tmp_path / f"am1-session-{IDENTITY}"
    directory.mkdir()
    path = directory / f"am1-local-windows-{IDENTITY}.log"
    path.write_bytes(b"a" * 140000 + b"\nlatest client\n")
    result = value.read_output("client", IDENTITY)
    assert result["truncated"] and result["text"].endswith("latest client\n")
    assert len(result["text"].encode()) <= 128000
    assert result["path"] == str(path)


def test_unavailable_output_is_explained(tmp_path):
    result = adapter(tmp_path).read_output("camera", IDENTITY)
    assert result["state"] == "Unavailable" and result["reason"]
    assert result["text"] == ""


def test_invalid_receipt_time_does_not_break_output(tmp_path):
    value = adapter(tmp_path)
    value._emit(packet(b"original\n", windows_received_at_ns="bad"))
    assert value.read_output("host", IDENTITY)["received_age_ms"] >= 0
