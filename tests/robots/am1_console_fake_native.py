"""Owned test child: reuse the synthetic session with the real native bridge.

No normal CLI/config entrypoint and no device creation, SSH or ZMQ endpoints.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import threading
import time

from tests.robots.test_am1_console_local_path import FakeSessionIO
from examples.alohamini import am1_console_bridge as bridge


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pipe", required=True)
    parser.add_argument("--auth", type=Path, required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--stop", type=Path, required=True)
    parser.add_argument("--live-duration", type=float, required=True)
    parser.add_argument("--startup-duration", type=float, default=0)
    parser.add_argument("--no-output", action="store_true")
    parser.add_argument("--marker-delay-s", type=float, default=0)
    args = parser.parse_args()
    if not FakeSessionIO.SESSION_ID_PATTERN.fullmatch(args.session):
        parser.error("invalid synthetic session identity")
    # Test-only launch failure model, not part of the nominal comparisons.
    if args.marker_delay_s:
        time.sleep(args.marker_delay_s)
    session = FakeSessionIO(live_duration_s=args.live_duration)
    session.startup_duration_s = args.startup_duration
    session.emit_output = not args.no_output
    session.run_count = int(args.session.rsplit("-", 1)[1], 16) - 1
    lock = threading.Lock()
    def emit(event):
        with lock:
            print(json.dumps({"session_id": args.session, "native_pid": os.getpid(),
                              "wall_time_ns": time.time_ns(), **event}), flush=True)
    # Keep concurrent test evidence and existing gate records line-atomic.
    bridge._print_record = emit
    original_accept = bridge.AM1ConsoleBridgeClient.accept_message
    def accept(native, message, *, received_at):
        result = original_accept(native, message, received_at=received_at)
        if message.get("kind") == "lease":
            payload = message.get("payload", {})
            evidence = payload.get("pause_evidence") or {}
            emit({"event": "test_native_lease", "pipe_seq": message.get("seq"),
                  "browser_seq": evidence.get("input_sequence"), "accepted": result,
                  "valid": payload.get("valid"), "reason": evidence.get("reason"),
                  "native_received_s": received_at})
        return result
    bridge.AM1ConsoleBridgeClient.accept_message = accept
    monitor_done = threading.Event()
    def stop_monitor():
        while not monitor_done.wait(.02):
            if args.stop.exists():
                session.stopped.set()
                return
    monitor = threading.Thread(target=stop_monitor, daemon=True)
    monitor.start()
    session.on_live = lambda: emit({"event": "test_live_started"})
    emit({"event": "test_process_started", "parent_pid": os.getppid()})
    try:
        result = session.run_start(None, None, args.live_duration,
            console_prepare=lambda identity: (args.pipe, args.auth),
            on_session_created=lambda identity: None, emit=emit)
        assert not session.native.is_connected and not session.native._worker.is_alive()
        emit({"event": "test_native_closed", "native_exit_code": result})
        return result
    finally:
        monitor_done.set()
        monitor.join(1)


if __name__ == "__main__":
    raise SystemExit(main())
