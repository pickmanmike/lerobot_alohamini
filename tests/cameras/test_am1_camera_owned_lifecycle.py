"""Owned camera shutdown and finite leases; acquisition is replaced, never motors."""

import base64
import http.client
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_viewer():
    spec = importlib.util.spec_from_file_location(
        "camera_owned_lifecycle", ROOT / "tools/am1_camera_viewer.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CONFIG = {
    "version": 1,
    "bind": "192.168.1.134",
    "port": 1984,
    "cameras": {"chest": "/dev/am_camera_chest"},
}
CREDENTIALS = {"username": "viewer", "password": "private-test-password"}


class Child:
    pid = 456

    def __init__(self, stop_request):
        self.returncode = None
        self.stop_request = stop_request
        self.terminated = 0
        self.killed = 0

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated += 1

    def wait(self, timeout):
        # Reproduce the demonstrated second signal while cooperative cleanup waits.
        self.stop_request.request(signal.SIGTERM, None)
        self.stop_request.request(signal.SIGTERM, None)
        self.returncode = -signal.SIGTERM
        return self.returncode

    def kill(self):
        self.killed += 1


def test_duplicate_sigterm_during_owned_cleanup_cannot_interrupt_release():
    viewer = load_viewer()
    request = viewer.CameraStopRequest()
    child = Child(request)
    server = Mock()
    server.handle_request.side_effect = lambda: request.request(signal.SIGTERM, None)
    with tempfile.TemporaryDirectory() as directory:
        state = Path(directory)
        lease = viewer.OwnedCameraLease(state, str(uuid.uuid4()), 660)
        with (
            patch.object(viewer, "preflight"),
            patch.object(viewer, "make_server", return_value=server),
            patch.object(viewer.subprocess, "Popen", return_value=child),
            patch.object(viewer, "CameraReader") as reader,
        ):
            reader.return_value.is_alive.return_value = False
            assert (
                viewer.run_viewer(
                    CONFIG, CREDENTIALS, Path("unused"), state, stop_request=request, lease=lease
                )
                == 0
            )
        acknowledgment = json.loads(lease.release_path.read_text())
        assert acknowledgment["state"] == "released"
        assert acknowledgment["release_evidence"] == {
            "backend_exited": True,
            "backend_pid": 456,
            "backend_returncode": -signal.SIGTERM,
            "readers_joined": True,
            "viewer_socket_closed": True,
            "runtime_config_removed": True,
            "device_fuser_checked": False,
        }
        assert child.terminated == 1
        assert child.killed == 0
        assert request.reason == "signal:SIGTERM"
        assert not list(state.glob("backend-*.json"))


def test_owned_uuid_is_single_use_and_deadline_cannot_be_renewed():
    viewer = load_viewer()
    now = [10.0]
    with tempfile.TemporaryDirectory() as directory:
        state = Path(directory)
        session_id = str(uuid.uuid4())
        lease = viewer.OwnedCameraLease(state, session_id, 30, clock=lambda: now[0])
        before = lease.path.read_bytes()
        assert lease.deadline == 40.0
        now[0] = 39.9
        assert not lease.expired()
        now[0] = 40.0
        assert lease.expired()
        with pytest.raises(FileExistsError):
            viewer.OwnedCameraLease(state, session_id, 900, clock=lambda: now[0])
        assert lease.path.read_bytes() == before
        fresh = viewer.OwnedCameraLease(state, str(uuid.uuid4()), 30, clock=lambda: now[0])
        assert fresh.deadline == 70.0


@pytest.mark.parametrize("budget", [0, -1, 900.001, float("inf"), float("nan"), True])
def test_owned_lease_rejects_unbounded_budget_before_preflight(budget):
    viewer = load_viewer()
    with tempfile.TemporaryDirectory() as directory, pytest.raises(ValueError):
        viewer.OwnedCameraLease(Path(directory), str(uuid.uuid4()), budget)


def test_expired_lease_refuses_device_start_and_still_acknowledges_no_resources():
    viewer = load_viewer()
    now = [10.0]
    with tempfile.TemporaryDirectory() as directory:
        state = Path(directory)
        lease = viewer.OwnedCameraLease(state, str(uuid.uuid4()), 1, clock=lambda: now[0])
        now[0] = 11.0
        with (
            patch.object(viewer, "preflight") as preflight,
            patch.object(viewer.subprocess, "Popen") as popen,
        ):
            with pytest.raises(RuntimeError, match="lease expired"):
                viewer.run_viewer(CONFIG, CREDENTIALS, Path("unused"), state, lease=lease)
            preflight.assert_not_called()
            popen.assert_not_called()
        acknowledgment = json.loads(lease.release_path.read_text())
        assert acknowledgment["state"] == "released"
        assert acknowledgment["runtime_failed"] is True
        assert acknowledgment["release_evidence"]["backend_pid"] is None


def test_owned_budget_expiry_stops_running_source_with_original_deadline():
    viewer = load_viewer()
    now = [10.0]
    request = viewer.CameraStopRequest()
    child = Child(request)
    server = Mock()
    with tempfile.TemporaryDirectory() as directory:
        state = Path(directory)
        lease = viewer.OwnedCameraLease(state, str(uuid.uuid4()), 1, clock=lambda: now[0])
        server.handle_request.side_effect = lambda: now.__setitem__(0, 11.0)
        with (
            patch.object(viewer, "preflight"),
            patch.object(viewer, "make_server", return_value=server),
            patch.object(viewer.subprocess, "Popen", return_value=child),
            patch.object(viewer, "CameraReader") as reader,
        ):
            reader.return_value.is_alive.return_value = False
            assert (
                viewer.run_viewer(
                    CONFIG, CREDENTIALS, Path("unused"), state, stop_request=request, lease=lease
                )
                == 0
            )
        assert json.loads(lease.release_path.read_text())["stop_reason"] == "source-budget-expired"
        assert lease.deadline == 11.0


def test_incomplete_cleanup_records_unknown_resource_and_preserves_original_fault():
    viewer = load_viewer()
    request = viewer.CameraStopRequest()
    child = Child(request)
    server = Mock()
    server.handle_request.side_effect = RuntimeError("first-runtime-fault")
    with tempfile.TemporaryDirectory() as directory:
        state = Path(directory)
        lease = viewer.OwnedCameraLease(state, str(uuid.uuid4()), 660)
        with (
            patch.object(viewer, "preflight"),
            patch.object(viewer, "make_server", return_value=server),
            patch.object(viewer.subprocess, "Popen", return_value=child),
            patch.object(viewer, "CameraReader") as reader,
        ):
            reader.return_value.is_alive.return_value = True
            with pytest.raises(RuntimeError, match="first-runtime-fault"):
                viewer.run_viewer(CONFIG, CREDENTIALS, Path("unused"), state, lease=lease)
        acknowledgment = json.loads(lease.release_path.read_text())
        assert acknowledgment["state"] == "release-incomplete"
        assert acknowledgment["release_evidence"]["readers_joined"] is False
        assert acknowledgment["runtime_failed"] is True
        assert acknowledgment["errors"] == ["reader-not-stopped:" + str(reader.return_value.name)]


def test_camera_launcher_replaces_shell_so_service_signals_one_main_owner():
    source = (ROOT / "tools/run_am1_camera.sh").read_text()
    runtime = source.split('} >>"$log_path" || exit 2', 1)[1]
    assert 'exec "$python" "$viewer" "$@" >>"$log_path" 2>&1' in runtime
    assert "camera_exit=$?" not in runtime


def test_one_reserved_lease_cannot_dispatch_capture_twice_even_before_its_deadline():
    viewer = load_viewer()
    request = viewer.CameraStopRequest()
    child = Child(request)
    server = Mock()
    server.handle_request.side_effect = lambda: request.request(signal.SIGTERM, None)
    with tempfile.TemporaryDirectory() as directory:
        state = Path(directory)
        lease = viewer.OwnedCameraLease(state, str(uuid.uuid4()), 660)
        with (
            patch.object(viewer, "preflight"),
            patch.object(viewer, "make_server", return_value=server),
            patch.object(viewer.subprocess, "Popen", return_value=child),
            patch.object(viewer, "CameraReader") as reader,
        ):
            reader.return_value.is_alive.return_value = False
            assert (
                viewer.run_viewer(
                    CONFIG, CREDENTIALS, Path("unused"), state, stop_request=request, lease=lease
                )
                == 0
            )
        original_ack = lease.release_path.read_bytes()
        with (
            patch.object(viewer, "preflight") as preflight,
            patch.object(viewer.subprocess, "Popen") as popen,
        ):
            with pytest.raises(RuntimeError, match="already used"):
                viewer.run_viewer(CONFIG, CREDENTIALS, Path("unused"), state, lease=lease)
            preflight.assert_not_called()
            popen.assert_not_called()
        assert lease.release_path.read_bytes() == original_ack


def test_actual_http_refuses_owned_frame_at_budget_expiry():
    viewer = load_viewer()
    now = [10.0]
    with tempfile.TemporaryDirectory() as directory:
        lease = viewer.OwnedCameraLease(Path(directory), str(uuid.uuid4()), 1, clock=lambda: now[0])
        stores = {"chest": viewer.FrameStore(clock=lambda: now[0])}
        stores["chest"].publish(b"\xff\xd8test-frame\xff\xd9")
        server = viewer.make_server(("127.0.0.1", 0), stores, CONFIG["cameras"], CREDENTIALS)
        server.source_lease = lease
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        auth = "Basic " + base64.b64encode(b"viewer:private-test-password").decode()
        try:
            for instant, status in [(10.0, 200), (11.0, 503)]:
                now[0] = instant
                # A new valid frame cannot refresh the immutable lease.
                stores["chest"].publish(b"\xff\xd8test-frame\xff\xd9")
                connection = http.client.HTTPConnection(*server.server_address, timeout=2)
                connection.request("GET", "/api/frame.jpeg?src=chest", headers={"Authorization": auth})
                response = connection.getresponse()
                assert response.status == status
                if status == 200:
                    assert response.getheader("X-Camera-Owned-Session") == lease.session_id
                response.read()
                connection.close()
        finally:
            server.shutdown()
            server.server_close()
            worker.join(2)
        assert not worker.is_alive()


@pytest.mark.skipif(os.name != "posix", reason="Repeated POSIX SIGTERM requires Linux/Pi")
def test_real_repeated_signals_while_child_waits_preserve_release_acknowledgment(tmp_path):
    script = r"""
import importlib.util, json, pathlib, signal, subprocess, sys, time, threading
from unittest.mock import Mock, patch
spec = importlib.util.spec_from_file_location('camera_signal_test', sys.argv[1])
viewer = importlib.util.module_from_spec(spec); spec.loader.exec_module(viewer)
request = viewer.CameraStopRequest()
signal.signal(signal.SIGTERM, request.request)
lease = viewer.OwnedCameraLease(pathlib.Path(sys.argv[2]), sys.argv[3], 30)
real_popen = subprocess.Popen
server = Mock(); server.handle_request.side_effect = lambda: time.sleep(.05)
def harmless_child(*args, **kwargs):
    child = real_popen([sys.executable, '-c',
        'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print("CHILD_READY", flush=True); time.sleep(1.5)'],
        stdout=subprocess.PIPE, text=True, start_new_session=True)
    assert child.stdout.readline().strip() == 'CHILD_READY'
    print('READY', flush=True)
    return child
with patch.object(viewer, 'preflight'), patch.object(viewer, 'make_server', return_value=server), \
     patch.object(viewer.subprocess, 'Popen', side_effect=harmless_child), patch.object(viewer, 'CameraReader') as reader:
    reader.return_value.is_alive.return_value = False
    viewer.run_viewer({'bind':'127.0.0.1', 'port':0, 'cameras':{'chest':'/dev/am_camera_chest'}},
                     {'username':'viewer','password':'private-test-password'}, pathlib.Path('unused'),
                     pathlib.Path(sys.argv[2]), stop_request=request, lease=lease)
print('ACK=' + lease.release_path.read_text().replace('\n',''), flush=True)
"""
    session_id = str(uuid.uuid4())
    process = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-c",
            script,
            str(ROOT / "tools/am1_camera_viewer.py"),
            str(tmp_path),
            session_id,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout.readline().strip() == "READY"
        os.kill(process.pid, signal.SIGTERM)
        time.sleep(0.1)
        os.kill(process.pid, signal.SIGTERM)
        time.sleep(0.1)
        os.kill(process.pid, signal.SIGTERM)
        output, error = process.communicate(timeout=5)
        assert process.returncode == 0, error
        acknowledgment = json.loads((tmp_path / f"release-{session_id}.json").read_text())
        assert acknowledgment["state"] == "released"
        assert acknowledgment["release_evidence"]["backend_returncode"] == 0
        assert "KeyboardInterrupt" not in error
        assert "CAMERA_CLEANUP_ERRORS=[]" in output
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)


def test_signal_handler_never_reenters_event_lock_during_cleanup():
    viewer = load_viewer()
    request = viewer.CameraStopRequest()
    # SIGTERM can arrive while cleanup's Event.set holds its non-reentrant lock.
    with patch.object(request.event, "set", side_effect=RuntimeError("event-lock-held")) as event_set:
        request.request(signal.SIGTERM, None)
        request.request(signal.SIGTERM, None)
    event_set.assert_not_called()
    assert request.reason == "signal:SIGTERM"
