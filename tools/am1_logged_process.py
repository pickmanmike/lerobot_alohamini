#!/usr/bin/env python

"""Run one interactive child with durable output independent of console display."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
from pathlib import Path


def _display(log_path: Path, offset: int) -> int:
    """Best-effort byte tail used only for operator visibility."""
    try:
        with log_path.open("rb", buffering=0) as stream:
            stream.seek(offset)
            while True:
                chunk = stream.read(65_536)
                if chunk:
                    try:
                        os.write(sys.stdout.fileno(), chunk)
                    except (BrokenPipeError, OSError):
                        return 0
                else:
                    time.sleep(0.02)
    except OSError:
        return 0


def _countdown(log_path: Path, duration_seconds: int) -> int:
    """Disposable remaining-time display; never owns lifecycle decisions."""
    while True:
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if '"event": "am1_client_live_start"' in text:
            break
        time.sleep(0.05)
    started = time.monotonic()
    next_report = started
    while True:
        now = time.monotonic()
        if now >= next_report:
            remaining = max(0, duration_seconds - int(now - started))
            try:
                os.write(sys.stdout.fileno(), f"AM1 live time remaining: {remaining} s\n".encode())
            except (BrokenPipeError, OSError):
                return 0
            next_report += 10.0
        time.sleep(0.05)


def _stop_display(display: subprocess.Popen[bytes]) -> None:
    if display.poll() is not None:
        return
    display.terminate()
    try:
        display.wait(timeout=2)
    except subprocess.TimeoutExpired:
        display.kill()
        display.wait(timeout=2)


def run_logged(command: list[str], log_path: Path, duration_seconds: int | None = None) -> int:
    if not command:
        raise ValueError("a child command is required")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    offset = log_path.stat().st_size if log_path.exists() else 0
    display = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--display",
            "--log",
            str(log_path),
            "--offset",
            str(offset),
        ],
        stdin=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    countdown: subprocess.Popen[bytes] | None = None
    if duration_seconds is not None:
        countdown = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--countdown",
                "--log",
                str(log_path),
                "--duration-seconds",
                str(duration_seconds),
            ],
            stdin=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    child = subprocess.Popen(
        command,
        stdin=None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
    )
    pump_error: list[BaseException] = []

    def pump() -> None:
        assert child.stdout is not None
        try:
            with log_path.open("ab", buffering=0) as log_stream:
                while chunk := child.stdout.read(65_536):
                    log_stream.write(chunk)
        except BaseException as exc:
            pump_error.append(exc)

    pump_thread = threading.Thread(target=pump, name="am1-durable-log-pump")
    pump_thread.start()
    try:
        try:
            exit_code = int(child.wait())
        except KeyboardInterrupt:
            try:
                exit_code = int(child.wait(timeout=30))
            except subprocess.TimeoutExpired:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
                exit_code = 130
        pump_thread.join(timeout=5)
        if pump_thread.is_alive() or pump_error:
            return 2
        return exit_code
    finally:
        _stop_display(display)
        if countdown is not None:
            _stop_display(countdown)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--display", action="store_true")
    parser.add_argument("--countdown", action="store_true")
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--duration-seconds", type=int)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.display:
        return _display(args.log, args.offset)
    if args.countdown:
        if args.duration_seconds is None or not 1 <= args.duration_seconds <= 1800:
            return 2
        return _countdown(args.log, args.duration_seconds)
    command = list(args.command)
    if command and command[0] == "--":
        command.pop(0)
    try:
        return run_logged(command, args.log, args.duration_seconds)
    except (OSError, ValueError) as exc:
        print(f"AM1 logged command refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
