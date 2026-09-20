"""Motor-free tests: frames and HTTP use in-memory data/local sockets only."""

import base64
import copy
import importlib.util
import io
import http.client
import json
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[2]
JPEG = b"\xff\xd8test-frame\xff\xd9"


def load_viewer():
    path = ROOT / "tools" / "am1_camera_viewer.py"
    assert path.exists(), "The approved camera-only viewer has not been implemented"
    spec = importlib.util.spec_from_file_location("am1_camera_viewer", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class CameraCoreTests(unittest.TestCase):
    def setUp(self):
        self.viewer = load_viewer()
        self.config = {
            "version": 1,
            "bind": "192.168.1.134",
            "port": 1984,
            "cameras": {"forward": "/dev/am_camera_forward"},
        }

    def test_partial_mapping_retains_missing_roles_without_guessing(self):
        result = self.viewer.validate_config(self.config)
        self.assertEqual(result["cameras"], {"forward": "/dev/am_camera_forward"})
        self.assertEqual(len(self.viewer.ROLES), 5)

    def test_mapping_refuses_unapproved_role_duplicate_device_and_motor_paths(self):
        changes = [
            {"unknown": "/dev/am_camera_forward"},
            {"forward": "/dev/am_camera_forward", "backward": "/dev/am_camera_forward"},
            {"forward": "/dev/am_arm_follower_left"},
            {"forward": "/dev/ttyACM0"},
            {"forward": "/dev/video0"},
            {"forward": "http://example.org/private"},
            {"forward": "/dev/v4l/by-path/usb-1-video-index1"},
            {"forward": "/dev/v4l/by-path/../../ttyACM0-video-index0"},
            {},
        ]
        for cameras in changes:
            with self.subTest(cameras=cameras), self.assertRaises(ValueError):
                self.viewer.validate_config({**self.config, "cameras": cameras})

    def test_bind_requires_specific_private_ipv4_and_approved_port(self):
        for patch in [{"bind": "0.0.0.0"}, {"bind": "8.8.8.8"}, {"bind": "::"},
                      {"bind": "hostname.local"}, {"port": 22}, {"port": True},
                      {"version": 2}, {"extra": "ignored-is-unsafe"}]:
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                self.viewer.validate_config({**self.config, **patch})

    def test_numbered_identification_is_opt_in_and_cannot_assign_semantic_roles(self):
        config = {**self.config, "cameras": {"preview_4": "/dev/v4l/by-path/usb-4-video-index0"}}
        with self.assertRaises(ValueError):
            self.viewer.validate_config(config)
        # Through CLI: --identify must select a separate private map without touching ordinary roles.
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "identification.json"
            self.viewer.write_private(path, config)
            with patch.object(self.viewer, "preflight") as preflight:
                try:
                    result = self.viewer.main(["--identify", "--config", str(path), "--check"])
                except SystemExit:
                    result = 2
                self.assertEqual(result, 0)
                preflight.assert_not_called()
                self.assertEqual(self.viewer.load_private(path), config)

    def test_numbered_mode_refuses_mixed_roles_and_non_capture_sources(self):
        for cameras in [{"forward":"/dev/v4l/by-path/usb-1-video-index0"},
                        {"preview_4":"/dev/ttyACM0"}, {"preview_4":"/dev/video6"},
                        {"preview_4":"http://example.org/"}, {"preview_4":"exec:command"},
                        {"preview_4":"/dev/v4l/by-path/usb-4-video-index1"},
                        {"preview_4":"/dev/am_camera_backward"},
                        {"preview_6":"/dev/v4l/by-path/usb-6-video-index0"}]:
            with self.subTest(cameras=cameras), self.assertRaises(ValueError):
                self.viewer.validate_config({**self.config,"cameras":cameras}, identify=True)

    def test_grouped_backend_config_has_only_fixed_native_sources_and_minimal_listeners(self):
        config = self.viewer.backend_config(self.viewer.validate_config(self.config), "backend-secret")
        self.assertEqual(config["api"]["listen"], "127.0.0.1:1985")
        self.assertTrue(config["api"]["local_auth"])
        self.assertEqual(config["api"]["allow_paths"], ["/api/stream.mjpeg"])
        # v1.9.14 reads app.modules, NOT a top-level modules property.
        self.assertEqual(set(config.get("app", {}).get("modules", [])), {"api", "mjpeg", "v4l2"})
        self.assertEqual(config["streams"], {
            "forward": "v4l2:device?video=/dev/am_camera_forward&input_format=mjpeg&video_size=640x480&framerate=30"
        })
        self.assertEqual(config["rtsp"]["listen"], "")
        self.assertEqual(config["webrtc"]["listen"], "")

    def test_view_rotations_are_optional_validated_and_do_not_change_native_capture(self):
        original = self.viewer.backend_config(self.config, "test-backend-password")
        for angle in (0, 90, 180, 270):
            with self.subTest(angle=angle):
                rotated = self.viewer.validate_config({**self.config,"rotations":{"forward":angle}})
                self.assertEqual(rotated["rotations"], {"forward":angle})
                self.assertEqual(self.viewer.backend_config(rotated,"test-backend-password"),original)
        for rotations in ({"missing":90},{"forward":45},{"forward":True},
                          {"forward":"90"},{"forward":90.0},{"forward":-90},None,[]):
            with self.subTest(rotations=rotations), self.assertRaises(ValueError):
                self.viewer.validate_config({**self.config,"rotations":rotations})

    def test_freshness_comes_from_new_frames_not_snapshot_requests(self):
        now = [10.0]
        store = self.viewer.FrameStore(clock=lambda: now[0])
        self.assertEqual(store.status()["state"], "unavailable")
        store.publish(JPEG)
        now[0] = 10.4
        for _ in range(5):
            self.assertEqual(store.snapshot()[0], JPEG)
        self.assertAlmostEqual(store.status()["age_ms"], 400.0)
        self.assertEqual(store.status()["sequence"], 1)
        now[0] = 10.501
        self.assertEqual(store.status()["state"], "stale")
        self.assertIsNone(store.snapshot())
        # An identical image from a genuinely new multipart frame IS new evidence.
        store.publish(JPEG)
        self.assertEqual(store.status()["sequence"], 2)
        self.assertEqual(store.status()["state"], "fresh")

    def test_broken_transport_marks_stale_and_does_not_refresh_last_frame(self):
        now = [2.0]
        store = self.viewer.FrameStore(clock=lambda: now[0])
        store.publish(JPEG)
        now[0] = 2.1
        store.disconnected()
        self.assertEqual(store.status()["state"], "stale")
        self.assertAlmostEqual(store.status()["age_ms"], 100)
        self.assertIsNone(store.snapshot())
        store.publish(JPEG)
        self.assertEqual(store.status()["state"], "fresh")

    def test_bad_or_oversized_frame_does_not_refresh_state(self):
        store = self.viewer.FrameStore()
        for data in [b"not-jpeg", b"\xff\xd8partial", b"\xff\xd8" + b"a" * 2_000_000 + b"\xff\xd9"]:
            with self.subTest(length=len(data)), self.assertRaises(ValueError):
                store.publish(data)
        self.assertEqual(store.status()["sequence"], 0)

    def test_native_uvc_zero_alignment_padding_is_preserved_without_reencoding(self):
        # Observed native camera JPEGs end with EOI followed by 0–7 alignment zeros.
        store = self.viewer.FrameStore()
        for count in range(8):
            payload = JPEG + b"\0" * count
            store.publish(payload)
            self.assertEqual(store.snapshot()[0], payload)
        for trailer in [b"garbage", b"\0" * 8, b"\0x\0"]:
            with self.assertRaises(ValueError):
                store.publish(JPEG + trailer)
        self.assertEqual(store.status()["sequence"], 8)

    def test_metrics_measure_delivered_frames_and_do_not_retain_unbounded_history(self):
        now = [0.0]
        store = self.viewer.FrameStore(clock=lambda: now[0])
        for i in range(150):
            now[0] = i / 15
            store.publish(JPEG)
        state = store.status()
        self.assertAlmostEqual(state["fps"], 15.0, places=2)
        self.assertEqual(state["sequence"], 150)
        self.assertEqual(state["bytes_received"], 150 * len(JPEG))
        self.assertAlmostEqual(state["max_gap_ms"], 1000 / 15, places=2)

    def test_multipart_reads_actual_new_parts_and_stops_on_truncation(self):
        raw = b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 14\r\n\r\n" + JPEG + b"\r\n"
        frames = self.viewer.iter_mjpeg(io.BytesIO(raw), "multipart/x-mixed-replace; boundary=frame")
        self.assertEqual(next(frames), JPEG)
        with self.assertRaises(EOFError):
            next(frames)
        truncated = self.viewer.iter_mjpeg(io.BytesIO(raw[:-6]), "multipart/x-mixed-replace; boundary=frame")
        with self.assertRaises(EOFError):
            next(truncated)

    def test_multipart_rejects_bad_type_or_unbounded_lengths(self):
        for content_type, data in [
            ("text/plain", b""),
            ("multipart/x-mixed-replace", b""),
            ("multipart/x-mixed-replace; boundary=frame", b"--frame\r\nContent-Length: 999999999\r\n\r\n"),
            ("multipart/x-mixed-replace; boundary=frame", b"--frame\r\n" + b"X" * 8193),
        ]:
            with self.subTest(content_type=content_type), self.assertRaises((ValueError, EOFError)):
                next(self.viewer.iter_mjpeg(io.BytesIO(data), content_type))


class CameraHTTPTests(unittest.TestCase):
    def setUp(self):
        self.viewer = load_viewer()
        self.assertTrue(callable(getattr(self.viewer, "make_server", None)), "Read-only HTTP boundary missing")
        self.now = [10.0]
        self.stores = {role: self.viewer.FrameStore(clock=lambda: self.now[0]) for role in self.viewer.ROLES}
        self.stores["forward"].publish(JPEG)
        self.server = self.viewer.make_server(("127.0.0.1", 0), self.stores, {"forward"},
                                              {"username": "viewer", "password": "only-a-test-password"})
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.auth = "Basic " + base64.b64encode(b"viewer:only-a-test-password").decode()

    def tearDown(self):
        if hasattr(self, "server"):
            self.server.stop_event.set()
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(2)
            self.assertFalse(self.thread.is_alive())

    def request(self, path, method="GET", auth=True):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)
        connection.request(method, path, headers={"Authorization": self.auth} if auth else {})
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_all_viewer_paths_require_credentials_including_loopback(self):
        for path in ["/", "/app.js", "/freshness.js", "/mjpeg.js", "/style.css", "/status.json", "/api/frame.jpeg?src=forward"]:
            with self.subTest(path=path):
                status, headers, _ = self.request(path, auth=False)
                self.assertEqual(status, 401)
                self.assertIn("Basic", headers["WWW-Authenticate"])

    def test_static_status_and_native_snapshot_work_without_exposing_paths_or_credentials(self):
        for path in ["/", "/app.js", "/freshness.js", "/mjpeg.js", "/style.css"]:
            self.assertEqual(self.request(path)[0], 200)
        status, headers, body = self.request("/status.json")
        self.assertEqual(status, 200)
        report = json.loads(body)
        self.assertEqual(report["cameras"]["forward"]["state"], "fresh")
        self.assertEqual(report["cameras"]["backward"]["state"], "unavailable")
        self.assertEqual(report["cameras"]["forward"].get("rotation_degrees"), 0)
        self.assertNotIn(b"password", body)
        self.assertNotIn(b"/dev/", body)
        status, headers, body = self.request("/api/frame.jpeg?src=forward&cache=500ms")
        self.assertEqual((status, body), (200, JPEG))
        self.assertEqual(headers["X-Frame-Sequence"], "1")
        self.assertIn("no-store", headers["Cache-Control"])
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

    def test_admin_classes_path_traversal_and_websocket_paths_are_unavailable(self):
        paths = ["/api", "/api/config", "/api/streams", "/api/restart", "/api/exit", "/api/log",
                 "/api/ws", "/api/v4l2", "/api/preload", "/api/debug", "/debug/pprof", "/api/discovery",
                 "/config", "/go2rtc.yaml", "/%2e%2e/config", "/../config", "//api/config", "/status.json/extra"]
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 404)

    def test_every_mutating_method_and_upgrade_is_refused(self):
        for method in ["POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "TRACE"]:
            with self.subTest(method=method):
                self.assertEqual(self.request("/api/stream.mjpeg?src=forward", method=method)[0], 403)
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)
        connection.request("GET", "/", headers={"Authorization": self.auth, "Upgrade": "websocket"})
        self.assertEqual(connection.getresponse().status, 403)
        connection.close()

    def test_media_queries_cannot_create_patch_upload_or_select_arbitrary_sources(self):
        for query in ["", "src=missing", "src=backward", "src=http://example.org", "src=forward&src=chest",
                      "src=forward&name=arbitrary", "src=forward&dst=chest", "src=forward&video=raw",
                      "src=forward&cache=1h", "src=forward&width=100", "src=forward%00", "src=forward&"]:
            with self.subTest(query=query):
                self.assertIn(self.request("/api/frame.jpeg?" + query)[0], (400, 404))

    def test_stale_snapshot_is_not_reissued_as_fresh_and_other_camera_stays_live(self):
        self.now[0] = 10.6
        self.assertEqual(self.request("/api/frame.jpeg?src=forward")[0], 503)
        self.assertEqual(json.loads(self.request("/status.json")[2])["cameras"]["forward"]["state"], "stale")
        self.stores["forward"].publish(JPEG)
        self.assertEqual(self.request("/api/frame.jpeg?src=forward")[0], 200)
        self.assertEqual(json.loads(self.request("/status.json")[2])["cameras"]["backward"]["state"], "unavailable")

    def test_primary_stream_contains_native_jpeg_and_sequence_without_reencoding(self):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)
        connection.request("GET", "/api/stream.mjpeg?src=forward", headers={"Authorization": self.auth})
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.readline(), b"--frame\r\n")
        headers = b""
        while (line := response.readline()) != b"\r\n":
            headers += line
        self.assertIn(b"X-Frame-Sequence: 1", headers)
        self.assertEqual(response.read(len(JPEG)), JPEG)
        connection.close()


class CameraLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.viewer = load_viewer()

    def test_reader_uses_one_fixed_authenticated_backend_and_publishes_parts(self):
        self.assertTrue(hasattr(self.viewer, "read_connection"), "Camera reader missing")
        raw = b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 14\r\n\r\n" + JPEG + b"\r\n"
        response = io.BytesIO(raw)
        response.status = 200
        response.getheader = lambda name, default=None: "multipart/x-mixed-replace; boundary=frame"
        connection = Mock()
        connection.getresponse.return_value = response
        store = self.viewer.FrameStore()
        with self.assertRaises(EOFError):
            self.viewer.read_connection(connection, "forward", "private-backend-auth", store, threading.Event())
        connection.request.assert_called_once_with("GET", "/api/stream.mjpeg?src=forward",
                                                  headers={"Authorization": "private-backend-auth"})
        self.assertEqual(store.status()["sequence"], 1)
        self.assertIsNone(store.snapshot())  # EOF invalidates freshness, not the evidence.
        connection.close.assert_called_once()

    def test_reader_fault_isolated_and_stop_has_bounded_join(self):
        self.assertTrue(hasattr(self.viewer, "CameraReader"), "Bounded reader lifecycle missing")
        stop = threading.Event()
        store = self.viewer.FrameStore()
        other = self.viewer.FrameStore()
        other.publish(JPEG)
        attempted = threading.Event()
        def failed_connection(*args, **kwargs):
            attempted.set()
            raise OSError("camera disconnected")
        worker = self.viewer.CameraReader("forward", "private-backend-auth", store, stop,
                                          connection_factory=failed_connection)
        worker.start()
        self.assertTrue(attempted.wait(1))
        stop.set()
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(store.status()["state"], "unavailable")
        self.assertEqual(other.status()["sequence"], 1)

    def test_cleanup_stops_owned_child_even_when_reader_join_fails(self):
        self.assertTrue(hasattr(self.viewer, "cleanup"), "Owned-process cleanup missing")
        calls = []
        stop = threading.Event()
        worker = Mock()
        worker.join.side_effect = lambda timeout: calls.append("join")
        worker.is_alive.return_value = True
        server = Mock()
        server.server_close.side_effect = lambda: calls.append("http-close")
        child = Mock()
        child.poll.return_value = None
        child.terminate.side_effect = lambda: calls.append("terminate-owned-child")
        errors = self.viewer.cleanup(stop, [worker], server, child)
        self.assertTrue(stop.is_set())
        self.assertEqual(calls, ["http-close", "terminate-owned-child", "join"])
        self.assertTrue(errors)

    def test_private_files_refuse_symlink_and_weak_permissions(self):
        self.assertTrue(hasattr(self.viewer, "load_private"), "Private config boundary missing")
        import tempfile
        import os
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private.json"
            self.viewer.write_private(path, {"test": True})
            self.assertEqual(self.viewer.load_private(path), {"test": True})
            with self.assertRaises(FileExistsError):
                self.viewer.write_private(path, {"overwrite": True})
            if os.name == "posix":
                path.chmod(0o644)
                with self.assertRaises(ValueError):
                    self.viewer.load_private(path)

    def test_credentials_schema_does_not_allow_header_injection_or_empty_secret(self):
        self.assertTrue(hasattr(self.viewer, "validate_credentials"), "Credential validation missing")
        for credentials in [{"username": "viewer", "password": ""},
                            {"username": "a:b", "password": "long-password-for-test"},
                            {"username": "viewer", "password": "line\r\ninjection"},
                            {"username": "viewer", "password": "password", "extra": True}]:
            with self.subTest(credentials=credentials), self.assertRaises(ValueError):
                self.viewer.validate_credentials(credentials)

    def test_backend_exit_cleans_up_and_is_not_reported_as_success(self):
        self.assertTrue(hasattr(self.viewer, "run_viewer"), "Runtime orchestration missing")
        import tempfile
        config = {"version": 1, "bind": "192.168.1.134", "port": 1984,
                  "cameras": {"forward": "/dev/am_camera_forward"}}
        child, server = Mock(), Mock()
        child.poll.return_value = 7
        credentials = {"username": "viewer", "password": "private-test-password"}
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(self.viewer, "preflight"), patch.object(self.viewer, "make_server", return_value=server), \
                 patch.object(self.viewer.subprocess, "Popen", return_value=child), \
                 patch.object(self.viewer, "CameraReader") as reader:
                reader.return_value.is_alive.return_value = False
                with self.assertRaisesRegex(RuntimeError, "backend exited"):
                    self.viewer.run_viewer(config, credentials, Path("unused-binary"), Path(directory), duration=1)
                server.server_close.assert_called_once()
                reader.return_value.join.assert_called_once()
                self.assertFalse(list(Path(directory).glob("backend-*.json")), "Ephemeral backend secret retained")

    def test_keyboard_interrupt_is_orderly_and_child_cannot_share_terminal_sigint(self):
        self.assertTrue(hasattr(self.viewer, "run_viewer"), "Runtime orchestration missing")
        import tempfile
        child, server = Mock(), Mock()
        child.poll.return_value = None
        server.handle_request.side_effect = KeyboardInterrupt
        config = {"version": 1, "bind": "192.168.1.134", "port": 1984,
                  "cameras": {"forward": "/dev/am_camera_forward"}}
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(self.viewer, "preflight"), patch.object(self.viewer, "make_server", return_value=server), \
                 patch.object(self.viewer.subprocess, "Popen", return_value=child) as popen, \
                 patch.object(self.viewer, "CameraReader") as reader:
                reader.return_value.is_alive.return_value = False
                result = self.viewer.run_viewer(config, {"username": "viewer", "password": "private-test-password"},
                                                 Path("unused-binary"), Path(directory))
                self.assertEqual(result, 0)
                self.assertTrue(popen.call_args.kwargs["start_new_session"])
                child.terminate.assert_called_once()
                child.wait.assert_called_once()

    def test_primary_failure_survives_cleanup_failure(self):
        self.assertTrue(hasattr(self.viewer, "run_viewer"), "Runtime orchestration missing")
        import tempfile
        primary = RuntimeError("original failure")
        config = {"version": 1, "bind": "192.168.1.134", "port": 1984,
                  "cameras": {"forward": "/dev/am_camera_forward"}}
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(self.viewer, "preflight"), \
                 patch.object(self.viewer, "make_server", side_effect=primary), \
                 patch.object(self.viewer, "cleanup", return_value=["cleanup failure"]):
                with self.assertRaises(RuntimeError) as caught:
                    self.viewer.run_viewer(config, {"username": "viewer", "password": "private-test-password"},
                                           Path("unused-binary"), Path(directory))
                self.assertIs(caught.exception, primary)

    def test_launcher_keeps_credentials_interactive_and_runtime_output_off_terminal(self):
        helper = ROOT / "tools/run_am1_camera.sh"
        self.assertTrue(helper.exists(), "Camera launcher missing")
        source = helper.read_text()
        self.assertIn('exec "$python" "$viewer" "$@"', source)
        self.assertIn('>>"$log_path" 2>&1', source)
        self.assertNotIn("tee", source)
        self.assertIn("CAMERA_EXIT_CODE", source)
        self.assertNotIn("alohamini_host", source)

    def test_cli_configure_then_check_does_not_call_capture_preflight(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory, patch.object(self.viewer, "preflight") as preflight:
            path = Path(directory) / "cameras.json"
            self.assertEqual(self.viewer.main(["--config", str(path), "--configure", "--bind", "192.168.1.134",
                                             "--camera", "forward=/dev/am_camera_forward"]), 0)
            self.assertEqual(self.viewer.main(["--config", str(path), "--check"]), 0)
            preflight.assert_not_called()

    def test_identification_uses_existing_owner_cleanup_and_auth_with_numbered_routes_only(self):
        # Exercise the real run_viewer -> server -> routes, replacing acquisition only.
        import tempfile
        config = {"version":1,"bind":"127.0.0.1","port":0,
                  "cameras":{"preview_4":"/dev/v4l/by-path/usb-4-video-index0"},
                  "rotations":{"preview_4":90}}
        real_make_server = self.viewer.make_server
        observations = []
        def make_server(*args):
            server = real_make_server(*args)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            def handle_request():
                try:
                    for target, authenticated in [("/",False),("/",True),("/status.json",True),
                                                  ("/api/frame.jpeg?src=preview_4",True),
                                                  ("/api/frame.jpeg?src=backward",True),("/api/config",True)]:
                        conn = http.client.HTTPConnection(*server.server_address, timeout=2)
                        headers = {"Authorization":"Basic " + base64.b64encode(b"viewer:private-test-password").decode()} if authenticated else {}
                        conn.request("GET",target,headers=headers)
                        response = conn.getresponse()
                        observations.append((target,authenticated,response.status,response.read()))
                        conn.close()
                finally:
                    server.shutdown(); thread.join(2)
                raise KeyboardInterrupt
            server.handle_request = handle_request
            return server
        def reader(role, auth, store, stop):
            self.assertEqual(role,"preview_4")
            store.publish(JPEG)
            worker = Mock(); worker.is_alive.return_value = False
            return worker
        child = Mock(); child.poll.return_value = None
        with tempfile.TemporaryDirectory() as directory, patch.object(self.viewer,"preflight"), \
             patch.object(self.viewer,"make_server",side_effect=make_server), \
             patch.object(self.viewer,"CameraReader",side_effect=reader), \
             patch.object(self.viewer.subprocess,"Popen",return_value=child):
            try:
                result = self.viewer.run_viewer(config,{"username":"viewer","password":"private-test-password"},
                                                Path("unused"),Path(directory),identify=True)
            except TypeError:
                result = 2
            self.assertEqual(result,0)
        self.assertEqual([r[2] for r in observations],[401,200,200,200,404,404])
        self.assertIn(b'data-identification="true"',observations[1][3])
        self.assertEqual(set(json.loads(observations[2][3])["cameras"]),
                         {"preview_1","preview_2","preview_3","preview_4","preview_5"})
        self.assertEqual(json.loads(observations[2][3])["cameras"]["preview_4"].get("rotation_degrees"),90)
        self.assertEqual(observations[3][3],JPEG)
        child.terminate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
