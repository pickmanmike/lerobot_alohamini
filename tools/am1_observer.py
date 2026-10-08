#!/usr/bin/env python
"""Finite, private spare-observer capture over one existing SSH connection.

The camera host owns one MediaCapture. Each delivered JPEG follows a fresh
nonce challenge and carries its original QPC timestamp; no remote monotonic
timestamp is subtracted from this receiver's clock.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import ipaddress
import json
import math
import os
import queue
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid
from collections import deque
from contextlib import suppress
from functools import cache
from pathlib import Path

MAX_JPEG_BYTES = 512 * 1024
MAX_LINE_BYTES = 1024 * 1024
MAX_RECORDING_BYTES = 140 * 1024 * 1024
CHALLENGE_INTERVAL_SECONDS = 0.125
MAX_ACTIVE_NONCES = 6
DELIVERY_DIAGNOSTIC_MAX_LINE_BYTES = 1024
DELIVERY_DIAGNOSTIC_MAX_RECORDS = 8192
DELIVERY_DIAGNOSTIC_MAX_BYTES = 16 * 1024 * 1024


@cache
def _precise_wall_reader():
    if os.name != "nt":
        return lambda: time.time_ns() // 1_000_000
    import ctypes
    from ctypes import wintypes

    query = ctypes.WinDLL("kernel32", use_last_error=True).GetSystemTimePreciseAsFileTime
    query.argtypes = [ctypes.POINTER(wintypes.FILETIME)]
    query.restype = None

    def read():
        stamp = wintypes.FILETIME()
        query(ctypes.byref(stamp))
        ticks = (stamp.dwHighDateTime << 32) | stamp.dwLowDateTime
        return (ticks - 116444736000000000) // 10000

    return read


def _wall_time_ms():
    return _precise_wall_reader()()


def _atomic_json(path: Path, value: dict) -> bool:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
    deadline = time.perf_counter() + 0.1
    while True:
        try:
            os.replace(temporary, path)
            return True
        except PermissionError as error:
            if getattr(error, "winerror", None) not in (5, 32):
                raise
            if time.perf_counter() >= deadline:
                return False
            time.sleep(0.005)


def _observer_message(kind, line, epoch, read_started_monotonic_ms=None, *, clock_ms=None, wall_ms=None):
    """Stamp original receipt before queueing, without substituting processing time."""
    received = clock_ms() if clock_ms is not None else time.perf_counter() * 1000
    wall = wall_ms() if wall_ms is not None else _wall_time_ms()
    return kind, line, received, wall, epoch, read_started_monotonic_ms


def _read_observer_lines(stream, enqueue, epoch, *, clock_ms=None):
    while True:
        read_started = clock_ms() if clock_ms is not None else None
        line = stream.readline(MAX_LINE_BYTES + 1)
        if not line:
            return
        enqueue("frame", line, epoch, read_started_monotonic_ms=read_started)


class ObserverDeliveryDiagnostics:
    """Bounded timing evidence only; this object cannot qualify observation health."""

    _source_fields = {
        "event",
        "generation",
        "nonce",
        "sequence",
        "source_system_relative_ticks",
        "challenge_received_qpc_ticks",
        "capture_age_ms",
        "serialize_start_qpc_ticks",
        "serialized_qpc_ticks",
        "send_start_qpc_ticks",
        "send_ended_qpc_ticks",
        "metadata_emit_started_qpc_ticks",
        "jpeg_bytes",
        "sent",
        "previous_metadata_sequence",
        "previous_metadata_started_qpc_ticks",
        "previous_metadata_ended_qpc_ticks",
    }

    def __init__(self, output_dir: Path, generation: str, *, enabled: bool = False):
        self.enabled = enabled is True
        self.generation = generation
        self.incomplete = False
        self.rejected_records = self.dropped_records = self.bytes_written = 0
        self.counts = {"source": 0, "receiver": 0}
        self.max_append_ms = 0.0
        self.first_error = None
        self.drop_lock = threading.Lock()
        self.stream = None
        if self.enabled:
            try:
                self.stream = (Path(output_dir) / "delivery-timing.ndjson").open("xb")
            except OSError as error:
                self._drop(str(error))

    def _drop(self, reason, *, rejected=False):
        with self.drop_lock:
            self.incomplete = True
            if rejected:
                self.rejected_records += 1
            else:
                self.dropped_records += 1
            if self.first_error is None:
                self.first_error = reason[:160]
        return False

    def queue_dropped(self):
        if self.enabled:
            self._drop("observer message queue full")

    @staticmethod
    def _number(value):
        try:
            return type(value) in (int, float) and math.isfinite(value) and value >= 0
        except OverflowError:
            return False

    def _identity(self, value):
        if (
            not isinstance(value, dict)
            or value.get("generation") != self.generation
            or not isinstance(value.get("nonce"), str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value["nonce"])
            or any(
                type(value.get(key)) is not int or not 0 < value[key] <= 2**53 - 1
                for key in ("sequence", "source_system_relative_ticks", "challenge_received_qpc_ticks")
            )
            or value["source_system_relative_ticks"] <= value["challenge_received_qpc_ticks"]
        ):
            raise ValueError("diagnostic frame identity invalid")
        return {
            key: value[key]
            for key in (
                "generation",
                "nonce",
                "sequence",
                "source_system_relative_ticks",
                "challenge_received_qpc_ticks",
            )
        }

    def _receipt(self, received_monotonic_ms, received_wall_time_ms, processing_started_ms):
        if (
            not all(self._number(value) for value in (received_monotonic_ms, processing_started_ms))
            or processing_started_ms < received_monotonic_ms
            or type(received_wall_time_ms) is not int
            or received_wall_time_ms <= 0
        ):
            raise ValueError("diagnostic original local receipt invalid")
        return {
            "received_local_perf_counter_ms": received_monotonic_ms,
            "received_wall_time_ms": received_wall_time_ms,
            "processing_started_monotonic_ms": processing_started_ms,
        }

    def _append(self, kind, value):
        began = time.perf_counter()
        try:
            encoded = (
                json.dumps(dict(value, kind=kind), separators=(",", ":"), allow_nan=False) + "\n"
            ).encode()
            if len(encoded) > DELIVERY_DIAGNOSTIC_MAX_LINE_BYTES:
                return self._drop("diagnostic row byte limit", rejected=True)
            if (
                self.counts[kind] >= DELIVERY_DIAGNOSTIC_MAX_RECORDS
                or self.bytes_written + len(encoded) > DELIVERY_DIAGNOSTIC_MAX_BYTES
            ):
                return self._drop("diagnostic finite record/byte limit")
            if self.stream is None:
                return self._drop("diagnostic file unavailable")
            self.stream.write(encoded)
            self.stream.flush()
            self.counts[kind] += 1
            self.bytes_written += len(encoded)
            return True
        except (OSError, ValueError, TypeError) as error:
            return self._drop(str(error), rejected=not isinstance(error, OSError))
        finally:
            self.max_append_ms = max(self.max_append_ms, (time.perf_counter() - began) * 1000)

    def source(self, value, *, received_wall_time_ms, received_monotonic_ms, processing_started_ms):
        if not self.enabled:
            return False
        try:
            self._identity(value)
            if set(value) != self._source_fields or value.get("event") != "frame_delivery_timing":
                raise ValueError("diagnostic source schema invalid")
            keys = (
                "source_system_relative_ticks",
                "serialize_start_qpc_ticks",
                "serialized_qpc_ticks",
                "send_start_qpc_ticks",
                "send_ended_qpc_ticks",
                "metadata_emit_started_qpc_ticks",
            )
            ticks = [value[key] for key in keys]
            if any(type(tick) is not int or not 0 < tick <= 2**53 - 1 for tick in ticks) or ticks != sorted(
                ticks
            ):
                raise ValueError("diagnostic original source phases invalid")
            previous = [
                value[key]
                for key in (
                    "previous_metadata_sequence",
                    "previous_metadata_started_qpc_ticks",
                    "previous_metadata_ended_qpc_ticks",
                )
            ]
            if any(type(tick) is not int or not 0 <= tick <= 2**53 - 1 for tick in previous) or not (
                previous == [0, 0, 0]
                or (0 < previous[0] < value["sequence"] and 0 < previous[1] <= previous[2] <= ticks[1])
            ):
                raise ValueError("diagnostic prior stdout phases invalid")
            if (
                not self._number(value["capture_age_ms"])
                or type(value["sent"]) is not bool
                or type(value["jpeg_bytes"]) is not int
                or not 0 < value["jpeg_bytes"] <= MAX_JPEG_BYTES
            ):
                raise ValueError("diagnostic original source values invalid")
            receipt = self._receipt(received_monotonic_ms, received_wall_time_ms, processing_started_ms)
            return self._append("source", dict(value, **receipt))
        except (ValueError, TypeError, KeyError) as error:
            return self._drop(str(error), rejected=True)

    def receiver(
        self,
        value,
        *,
        read_started_ms,
        received_monotonic_ms,
        received_wall_time_ms,
        processing_started_ms,
        processing_completed_ms,
        accepted,
        connection_epoch=None,
    ):
        if not self.enabled:
            return False
        try:
            identity = self._identity(value)
            receipt = self._receipt(received_monotonic_ms, received_wall_time_ms, processing_started_ms)
            if (
                not all(self._number(value) for value in (read_started_ms, processing_completed_ms))
                or read_started_ms > received_monotonic_ms
                or processing_completed_ms < processing_started_ms
                or type(accepted) is not bool
                or (
                    connection_epoch is not None
                    and (type(connection_epoch) is not int or connection_epoch < 0)
                )
            ):
                raise ValueError("diagnostic original receiver phases invalid")
            return self._append(
                "receiver",
                dict(
                    identity,
                    **receipt,
                    event="frame_receiver_timing",
                    read_started_monotonic_ms=read_started_ms,
                    processing_completed_monotonic_ms=processing_completed_ms,
                    accepted=accepted,
                    connection_epoch=connection_epoch,
                    read_wait_ms=received_monotonic_ms - read_started_ms,
                    queue_delay_ms=processing_started_ms - received_monotonic_ms,
                    processing_ms=processing_completed_ms - processing_started_ms,
                ),
            )
        except (ValueError, TypeError, KeyError) as error:
            return self._drop(str(error), rejected=True)

    def finish(self, source_summary):
        if self.enabled and (
            not isinstance(source_summary, dict)
            or source_summary.get("enabled") is not True
            or type(source_summary.get("records")) is not int
            or source_summary["records"] != self.counts["source"]
            or source_summary.get("incomplete") is not False
        ):
            self._drop("source diagnostic evidence missing or incomplete")

    def close(self):
        if self.stream is not None:
            try:
                self.stream.close()
            except OSError as error:
                self._drop(str(error))
            self.stream = None

    def snapshot(self):
        return {
            "enabled": self.enabled,
            "incomplete": self.incomplete,
            "source_records": self.counts["source"],
            "receiver_records": self.counts["receiver"],
            "rejected_records": self.rejected_records,
            "dropped_records": self.dropped_records,
            "bytes_written": self.bytes_written,
            "max_append_ms": self.max_append_ms,
            "first_error": self.first_error,
        }


class ObserverReceiver:
    """Qualify actual frame pixels, capture freshness and challenge delivery."""

    def __init__(self, output_dir: Path, generation: str, *, recent_frames: int = 12):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", generation):
            raise ValueError("invalid observer generation")
        if not 2 <= recent_frames <= 20:
            raise ValueError("recent frame retention must be bounded")
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.generation = generation
        self.recent_frames = recent_frames
        self.frames: deque[Path] = deque()
        self.challenges: dict[str, tuple[float, int | None]] = {}
        self.issued_nonce_names: set[str] = set()
        self.last_received_monotonic_ms: float | None = None
        self.first_received_monotonic_ms: float | None = None
        self.delivered_span_ms = 0.0
        self.max_delivery_gap_ms = 0.0
        self.last_effective_capture_age_ms = 0.0
        self.max_effective_capture_age_ms = 0.0
        self.freshness_gap_violations = 0
        self.round_trip_deadline_violations = 0
        self.last_sequence = 0
        self.last_source_ticks = 0
        self.event_count = 0
        clock = time.get_clock_info("perf_counter")
        self.local_clock_resolution_ms = clock.resolution * 1000
        if not clock.monotonic or clock.adjustable or not 0 < self.local_clock_resolution_ms <= 0.01:
            raise ValueError("observer requires a precise monotonic local receipt clock")
        self.local_clock_implementation = clock.implementation
        self.current_contiguous_started_ms = None
        self.current_contiguous_span_ms = 0.0
        self.current_contiguous_frames = 0
        self.max_contiguous_delivery_span_ms = 0.0
        self.expired_interval_counted = False
        self.accepted_frames = 0
        self.rejected_frames = 0
        self.last_rejection = None
        self.state = {
            "generation": generation,
            "running": False,
            "recording": False,
            "reason": "no qualified frame",
        }
        self._save()

    @property
    def continuous_delivery_qualified(self) -> bool:
        return (
            self.accepted_frames >= 20
            and self.delivered_span_ms >= 20000
            and self.freshness_gap_violations == 0
            and self.round_trip_deadline_violations == 0
            and self.rejected_frames == 0
        )

    @property
    def current_contiguous_delivery_qualified(self) -> bool:
        return self.current_contiguous_frames >= 20 and self.current_contiguous_span_ms >= 20000

    def _reset_current_window(self):
        self.current_contiguous_started_ms = None
        self.current_contiguous_span_ms = 0.0
        self.current_contiguous_frames = 0
        self.state.update(
            current_contiguous_delivery_qualified=False,
            current_contiguous_span_ms=0.0,
            current_contiguous_frames=0,
        )

    def _save(self) -> bool:
        published = _atomic_json(self.output_dir / "latest-health.json", self.state)
        if not published:
            self._reset_current_window()
            self.state.update(
                running=False,
                challenge_qualified=False,
                reason="observer health publication sharing deadline",
            )
        return published

    def _unqualify(self, reason: str) -> None:
        self._reset_current_window()
        self.state.update(running=False, challenge_qualified=False, reason=reason[:250])
        self._save()

    def issue_challenge(self, nonce: str, now_monotonic_ms: float) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", nonce):
            raise ValueError("invalid observer nonce")
        if nonce in self.issued_nonce_names:
            raise ValueError("observer nonce already issued")
        if len(self.issued_nonce_names) >= 8192:
            raise ValueError("observer challenge count limit")
        if not math.isfinite(now_monotonic_ms):
            raise ValueError("invalid observer challenge clock")
        self.issued_nonce_names.add(nonce)
        self.challenges = {
            key: value for key, value in self.challenges.items() if now_monotonic_ms - value[0] <= 750
        }
        self.challenges[nonce] = (now_monotonic_ms, None)
        while len(self.challenges) > MAX_ACTIVE_NONCES:
            self.challenges.pop(next(iter(self.challenges)))

    def accept(self, record: dict, *, received_wall_time_ms: int, received_monotonic_ms: float) -> bool:
        try:
            if not isinstance(record, dict):
                raise ValueError("observer frame protocol must be an object")
            if record.get("event") != "frame" or record.get("generation") != self.generation:
                raise ValueError("observer generation/event mismatch")
            proof = self.challenges.get(record.get("nonce"))
            if proof is None:
                raise ValueError("observer challenge mismatch")
            if not math.isfinite(received_monotonic_ms) or (
                self.last_received_monotonic_ms is not None
                and received_monotonic_ms < self.last_received_monotonic_ms
            ):
                raise ValueError("observer local receipt clock regressed")
            round_trip_ms = received_monotonic_ms - proof[0]
            if not math.isfinite(round_trip_ms) or not 0 <= round_trip_ms <= 750:
                self.round_trip_deadline_violations += 1
                raise ValueError("observer original challenge delivery deadline")
            sequence = record.get("sequence")
            source_ticks = record.get("source_system_relative_ticks")
            challenge_ticks = record.get("challenge_received_qpc_ticks")
            if any(
                type(value) is not int or value <= 0 for value in (sequence, source_ticks, challenge_ticks)
            ):
                raise ValueError("observer source timestamp/sequence missing")
            if sequence <= self.last_sequence or source_ticks <= self.last_source_ticks:
                raise ValueError("observer source did not advance")
            if source_ticks <= challenge_ticks:
                raise ValueError("observer frame predates original challenge")
            if proof[1] is not None and proof[1] != challenge_ticks:
                raise ValueError("observer original challenge receipt changed")
            age = record.get("capture_age_ms")
            if isinstance(age, bool) or not isinstance(age, (int, float)) or not math.isfinite(age):
                raise ValueError("observer source age uncertain")
            if not 0 <= age <= 1500 or record.get("recording") is not True:
                raise ValueError("observer capture stale or recording inactive")
            # WinRT SystemRelativeTime and the receipt are same-host 100 ns QPC ticks:
            # https://learn.microsoft.com/uwp/api/windows.media.capture.frames.mediaframereference.systemrelativetime
            # Original local RTT includes receipt-to-source wait, which precedes this frame's age.
            source_elapsed_ms = (source_ticks - challenge_ticks) / 10000.0
            if not 0 < source_elapsed_ms <= round_trip_ms or source_elapsed_ms + age > round_trip_ms + 1.0:
                raise ValueError("observer QPC elapsed/capture age contradict original round trip")
            source_age_upper_bound_ms = max(age, round_trip_ms - source_elapsed_ms + 1.0)
            encoded = record.get("jpeg_base64")
            if not isinstance(encoded, str) or len(encoded) > MAX_JPEG_BYTES * 4 // 3 + 4:
                raise ValueError("observer JPEG size limit")
            pixels = base64.b64decode(encoded, validate=True)
            if not pixels or len(pixels) > MAX_JPEG_BYTES:
                raise ValueError("observer JPEG size limit")
            from PIL import Image

            with Image.open(io.BytesIO(pixels)) as image:
                if image.format != "JPEG" or image.size not in ((1280, 720), (640, 360)):
                    raise ValueError("observer image mode mismatch")
                image.load()
                dimensions = image.size
            target = self.output_dir / f"frame-{sequence:08d}.jpg"
            with target.open("xb") as stream:
                stream.write(pixels)
            self.frames.append(target)
            while len(self.frames) > self.recent_frames:
                self.frames.popleft().unlink()
            self.last_sequence, self.last_source_ticks = sequence, source_ticks
            if self.first_received_monotonic_ms is None:
                self.first_received_monotonic_ms = received_monotonic_ms
            if self.last_received_monotonic_ms is not None:
                gap = received_monotonic_ms - self.last_received_monotonic_ms
                self.max_delivery_gap_ms = max(self.max_delivery_gap_ms, gap)
                effective_age = self.last_effective_capture_age_ms + gap
                self.max_effective_capture_age_ms = max(self.max_effective_capture_age_ms, effective_age)
                if effective_age > 500:
                    if not self.expired_interval_counted:
                        self.freshness_gap_violations += 1
                    self._reset_current_window()
            self.expired_interval_counted = False
            self.last_effective_capture_age_ms = source_age_upper_bound_ms
            self.max_effective_capture_age_ms = max(
                self.max_effective_capture_age_ms, self.last_effective_capture_age_ms
            )
            if self.last_effective_capture_age_ms > 500:
                self.freshness_gap_violations += 1
                self._reset_current_window()
            else:
                if self.current_contiguous_started_ms is None:
                    self.current_contiguous_started_ms = received_monotonic_ms
                self.current_contiguous_frames += 1
                self.current_contiguous_span_ms = received_monotonic_ms - self.current_contiguous_started_ms
                self.max_contiguous_delivery_span_ms = max(
                    self.max_contiguous_delivery_span_ms, self.current_contiguous_span_ms
                )
            if round_trip_ms > 750:
                self.round_trip_deadline_violations += 1
            self.delivered_span_ms = received_monotonic_ms - self.first_received_monotonic_ms
            self.last_received_monotonic_ms = received_monotonic_ms
            self.accepted_frames += 1
            self.challenges[record["nonce"]] = (proof[0], challenge_ticks)
            self.state = {
                "generation": self.generation,
                "running": True,
                "recording": True,
                "challenge_qualified": True,
                "nonce": record["nonce"],
                "sequence": sequence,
                "source_system_relative_ticks": source_ticks,
                "challenge_received_qpc_ticks": challenge_ticks,
                "timing_basis": "qpc_elapsed_v1",
                "local_clock_resolution_ms": self.local_clock_resolution_ms,
                "local_clock_implementation": self.local_clock_implementation,
                "capture_age_ms": age,
                "round_trip_ms": round_trip_ms,
                "source_elapsed_since_challenge_ms": source_elapsed_ms,
                "source_age_upper_bound_ms": source_age_upper_bound_ms,
                "received_local_perf_counter_ms": received_monotonic_ms,
                "current_contiguous_delivery_qualified": self.current_contiguous_delivery_qualified,
                "current_contiguous_span_ms": self.current_contiguous_span_ms,
                "current_contiguous_frames": self.current_contiguous_frames,
                "received_wall_time_ms": received_wall_time_ms,
                "frame_path": str(target.resolve()),
                "frame_sha256": hashlib.sha256(pixels).hexdigest(),
                "image_width": dimensions[0],
                "image_height": dimensions[1],
                "source_phase_timings": {
                    key: record.get(key)
                    for key in (
                        "source_wait_ms",
                        "callback_source_age_ms",
                        "encoder_create_ms",
                        "encoder_flush_ms",
                        "jpeg_read_ms",
                    )
                },
                "accepted_frames": self.accepted_frames,
                "rejected_frames": self.rejected_frames,
            }
            return self._save()
        except (ValueError, TypeError, OSError) as error:
            self.rejected_frames += 1
            self.last_rejection = {
                "reason": str(error),
                "received_wall_time_ms": received_wall_time_ms,
                "received_monotonic_ms": received_monotonic_ms,
                "record": {
                    key: record.get(key)
                    for key in (
                        "event",
                        "generation",
                        "nonce",
                        "sequence",
                        "source_system_relative_ticks",
                        "challenge_received_qpc_ticks",
                        "capture_age_ms",
                        "recording",
                    )
                }
                if isinstance(record, dict)
                else None,
            }
            self._unqualify(str(error))
            return False

    def expire(self, now_monotonic_ms: float) -> None:
        if self.last_received_monotonic_ms is not None and self.state.get("running") is True:
            elapsed = now_monotonic_ms - self.last_received_monotonic_ms
            if not math.isfinite(elapsed) or elapsed < 0:
                self._unqualify("observer local expiry clock uncertain")
            elif self.last_effective_capture_age_ms + elapsed > 500:
                if not self.expired_interval_counted:
                    self.freshness_gap_violations += 1
                    self.expired_interval_counted = True
                self._unqualify("observer original capture freshness expired")

    def terminal(self, record: dict) -> None:
        if record.get("generation") != self.generation:
            self._unqualify("observer terminal generation mismatch")
            return
        self._reset_current_window()
        self.state.update(
            running=False,
            recording=False,
            challenge_qualified=False,
            reason=record.get("event", "terminal"),
            camera_released=record.get("camera_released") is True,
            capture_success=record.get("success") is True,
        )
        self._save()

    def retain_event(self, label: str) -> dict:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", label) or self.event_count >= 128:
            raise ValueError("observer event retention limit")
        if not self.state.get("frame_path"):
            raise ValueError("no delivered observer frame to retain")
        event_dir = self.output_dir / "events"
        event_dir.mkdir(exist_ok=True)
        target = event_dir / f"{label}.jpg"
        if target.exists():
            raise ValueError("observer event already exists")
        shutil.copyfile(self.state["frame_path"], target)
        value = dict(self.state, event=label, frame_path=str(target.resolve()))
        _atomic_json(event_dir / f"{label}.json", value)
        self.event_count += 1
        return value


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _powershell(script: str) -> list[str]:
    return [
        "powershell.exe",
        "-STA",
        "-NoProfile",
        "-NonInteractive",
        "-EncodedCommand",
        base64.b64encode(script.encode("utf-16-le")).decode("ascii"),
    ]


def _ssh(alias: str) -> list[str]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", alias):
        raise ValueError("use an existing simple SSH alias")
    return [
        "ssh",
        "-T",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ServerAliveInterval=10",
        "-o",
        "ServerAliveCountMax=2",
        alias,
    ]


def _delivery_jpeg_quality_percent(value):
    if type(value) is not int or value not in (15, 45):
        raise ValueError("observer live JPEG quality percent must be integer 15 or 45")
    return value


def _delivery_bind_address(value):
    if value is None:
        return None
    message = "delivery bind address requires a usable numeric IPv4 source"
    if not isinstance(value, str) or not re.fullmatch(r"[0-9.]{7,15}", value):
        raise ValueError(message)
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError:
        raise ValueError(message) from None
    if address.packed[0] == 0 or address.is_loopback or address.packed[0] >= 224 or address.packed[-1] == 255:
        raise ValueError(message)
    return str(address)


def _observer_forward_command(
    alias,
    local_port,
    remote_port,
    *,
    compression=False,
    forwarding_only=False,
    ipqos_none=False,
    bind_address=None,
):
    bind_address = _delivery_bind_address(bind_address)
    if any(type(port) is not int or not 1024 <= port <= 65535 for port in (local_port, remote_port)):
        raise ValueError("invalid owned observer forwarding port")
    options = ["-o", "ExitOnForwardFailure=yes", "-L", f"127.0.0.1:{local_port}:127.0.0.1:{remote_port}"]
    if compression:
        options += ["-o", "Compression=yes"]
    if ipqos_none is True:
        options += ["-o", "IPQoS=none"]
    if bind_address is not None:
        options += ["-o", "BindAddress=" + bind_address]
    if forwarding_only:
        options += ["-N"]
    return _ssh(alias)[:-1] + options + [alias]


def _validate_capture_terminal(value, generation):
    if (
        not isinstance(value, dict)
        or value.get("generation") != generation
        or not isinstance(value.get("event"), str)
        or value.get("event") not in {"complete", "failed"}
        or type(value.get("camera_released")) is not bool
        or type(value.get("success")) is not bool
    ):
        raise ValueError("observer final capture identity/status is invalid")
    if (
        value["event"] == "complete"
        and (
            value["success"] is not True
            or value["camera_released"] is not True
            or value.get("cleanup_errors")
        )
    ) or (value["event"] == "failed" and value["success"] is True):
        raise ValueError("observer final capture event/status is contradictory")
    return value


def _fetch_capture_terminal(args, config, remote_dir, output, *, timeout):
    # Read only the original owner's final artifact. This command cannot start a
    # capture, change its finite duration, or create any new challenge authority.
    path = remote_dir + "\\capture-metadata.json"
    script = (
        "$ProgressPreference='SilentlyContinue';$ErrorActionPreference='Stop';"
        "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);"
        f"if($env:COMPUTERNAME -ne {_ps_quote(config['expected_host'])}){{throw 'Observer host mismatch'}};"
        f"$p={_ps_quote(path)};"
        "if(!(Test-Path -LiteralPath $p)){[Console]::Out.WriteLine('null');exit 0};"
        f"if((Get-Item -LiteralPath $p).Length -gt {MAX_LINE_BYTES}){{throw 'Observer metadata size limit'}};"
        "[Console]::Out.WriteLine([IO.File]::ReadAllText($p,[Text.Encoding]::UTF8))"
    )
    event = {"event": "final_metadata_read", "generation": args.generation}
    try:
        result = subprocess.run(
            _ssh(args.ssh_host) + _powershell(script), capture_output=True, timeout=timeout
        )
        if result.returncode != 0:
            raise ValueError("observer final metadata SSH read failed")
        if len(result.stdout) > MAX_LINE_BYTES:
            raise ValueError("observer final metadata size limit")
        value = json.loads(result.stdout.decode("utf-8-sig"))
        if value is None:
            event["result"] = "not yet published"
            return None
        value = _validate_capture_terminal(value, args.generation)
        event["result"] = "validated original terminal"
        return value
    except (OSError, UnicodeError, ValueError, subprocess.TimeoutExpired) as error:
        event.update(result="unqualified", error_type=type(error).__name__, reason=str(error)[:250])
        return None
    finally:
        event["received_wall_time_ms"] = _wall_time_ms()
        with (output / "terminal-metadata-reads.ndjson").open("a", encoding="utf-8") as history:
            history.write(json.dumps(event) + "\n")


def close_delivery_and_wait(process, client, *, timeout: float) -> int:
    # SSH keeps forwarded channels alive after its remote command has exited.
    # Release this invocation's channel before waiting for its SSH transport.
    if client is not None:
        import socket

        with suppress(OSError):
            client.shutdown(socket.SHUT_RDWR)
        client.close()
    return process.wait(timeout=timeout)


def _scp_observer_stage(args, config, capture_source, remote_dir, output):
    """Opt-in finite file transport; camera admission still waits for verified ACK."""
    from pathlib import PureWindowsPath

    root = PureWindowsPath(config["remote_output_root"])
    if (
        not root.is_absolute()
        or ".." in root.parts
        or PureWindowsPath(remote_dir) != root / args.generation
        or not re.fullmatch(r"[A-Za-z]:[\\/][A-Za-z0-9_.\\/-]+", remote_dir)
    ):
        raise ValueError(
            "SCP staging requires the configured private Windows path without shell metacharacters"
        )
    config_bytes = json.dumps(config).encode("utf-8")
    if not 1 <= len(capture_source) <= 256 * 1024 or not 1 <= len(config_bytes) <= 16 * 1024:
        raise ValueError("observer staging files exceed their bounds")
    source_hash, config_hash = (hashlib.sha256(value).hexdigest() for value in (capture_source, config_bytes))
    local_files = [output / "source.stage", output / "config.stage"]
    for path, value in zip(local_files, (capture_source, config_bytes), strict=True):
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
            stream.write(value)
    stage_started = time.perf_counter()
    deadline = stage_started + 30

    def call(command, phase):
        phase_started = time.perf_counter()
        proof = {
            "generation": args.generation,
            "phase": phase,
            "status": "started",
            "original_shared_timeout_seconds": 30,
            "stage_start_monotonic_ms": stage_started * 1000,
            "phase_start_monotonic_ms": phase_started * 1000,
            "remaining_seconds_at_phase_start": deadline - phase_started,
        }
        if not _atomic_json(output / "stage-phase.json", proof):
            raise ValueError("observer staging phase evidence publication failed")
        try:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                raise subprocess.TimeoutExpired("observer SCP staging", 30)
            result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, timeout=remaining)
            (output / f"stage-{phase}.stderr").write_bytes(result.stderr[-65536:])
            if time.perf_counter() >= deadline:
                raise subprocess.TimeoutExpired("observer SCP staging", 30, stderr=result.stderr)
        except subprocess.TimeoutExpired as error:
            (output / f"stage-{phase}.stderr").write_bytes((error.stderr or b"")[-65536:])
            proof.update(
                status="timeout",
                elapsed_seconds=time.perf_counter() - phase_started,
                finished_monotonic_ms=time.perf_counter() * 1000,
            )
            _atomic_json(output / "stage-phase.json", proof)
            _atomic_json(output / f"stage-{phase}.json", proof)
            raise
        proof.update(
            status="complete" if result.returncode == 0 else "failed",
            exit_code=result.returncode,
            elapsed_seconds=time.perf_counter() - phase_started,
            finished_monotonic_ms=time.perf_counter() * 1000,
        )
        _atomic_json(output / "stage-phase.json", proof)
        _atomic_json(output / f"stage-{phase}.json", proof)
        return result

    expected_host, generation = config["expected_host"], args.generation
    prefix = (
        "$ProgressPreference='SilentlyContinue';$ErrorActionPreference='Stop';"
        "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);"
        f"if($env:COMPUTERNAME -ne {_ps_quote(expected_host)}){{throw 'Observer host mismatch'}};"
        f"$d={_ps_quote(remote_dir)};"
    )
    mkdir = prefix + (
        "if(Test-Path -LiteralPath $d){throw 'Observer generation already exists'};"
        "$null=New-Item -ItemType Directory -Path $d;"
        f"[Console]::Out.WriteLine((@{{event='stage_directory';host=$env:COMPUTERNAME;generation={_ps_quote(generation)}}}|ConvertTo-Json -Compress))"
    )
    result = call(_ssh(args.ssh_host) + _powershell(mkdir), "directory")
    if result.returncode:
        return result
    try:
        ack = json.loads(result.stdout)
        if ack != {"event": "stage_directory", "host": expected_host, "generation": generation}:
            raise ValueError("observer stage directory ACK is invalid")
        result = call(
            [
                "scp",
                "-B",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                *(str(path) for path in local_files),
                args.ssh_host + ":" + remote_dir.replace("\\", "/") + "/",
            ],
            "upload",
        )
        if result.returncode:
            return result
        finalize = prefix + (
            "$s=Join-Path $d 'source.stage';$cpath=Join-Path $d 'config.stage';"
            f"if((Get-Item -LiteralPath $s).Length -ne {len(capture_source)} -or (Get-Item -LiteralPath $cpath).Length -ne {len(config_bytes)}){{throw 'Observer staging size mismatch'}};"
            "$hasher=[Security.Cryptography.SHA256]::Create();try{$sh=[BitConverter]::ToString($hasher.ComputeHash([IO.File]::ReadAllBytes($s))).Replace('-','').ToLowerInvariant();$ch=[BitConverter]::ToString($hasher.ComputeHash([IO.File]::ReadAllBytes($cpath))).Replace('-','').ToLowerInvariant()}finally{$hasher.Dispose()};"
            f"if($sh -ne '{source_hash}' -or $ch -ne '{config_hash}'){{throw 'Observer staging hash mismatch'}};"
            "$c=[IO.File]::ReadAllText($cpath,[Text.Encoding]::UTF8)|ConvertFrom-Json;"
            f"if($c.expected_host -ne {_ps_quote(expected_host)} -or $c.generation -ne {_ps_quote(generation)} -or $c.output_dir -ne $d){{throw 'Observer staging identity mismatch'}};"
            "$target=Join-Path $d 'capture.ps1';$invocation=Join-Path $d 'invocation.json';"
            "if((Test-Path -LiteralPath $target) -or (Test-Path -LiteralPath $invocation)){throw 'Observer staging publish collision'};"
            "$probe=[Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback,0);$probe.Start();$port=$probe.LocalEndpoint.Port;$probe.Stop();"
            "$c|Add-Member -NotePropertyName capture_port -NotePropertyValue $port;"
            "$ready=Join-Path $d 'invocation.ready';[IO.File]::WriteAllText($ready,($c|ConvertTo-Json -Compress),[Text.UTF8Encoding]::new($false));"
            "Move-Item -LiteralPath $s -Destination $target;Move-Item -LiteralPath $ready -Destination $invocation;"
            f"[Console]::Out.WriteLine((@{{event='stage_complete';host=$env:COMPUTERNAME;generation={_ps_quote(generation)};capture_port=$port;source_bytes={len(capture_source)};config_bytes={len(config_bytes)};source_sha256='{source_hash}';config_sha256='{config_hash}'}}|ConvertTo-Json -Compress))"
        )
        result = call(_ssh(args.ssh_host) + _powershell(finalize), "finalize")
        if result.returncode:
            return result
        if len(result.stdout) > 16384:
            raise ValueError("observer staging ACK size limit")
        ack = json.loads(result.stdout)
        expected = {
            "event": "stage_complete",
            "host": expected_host,
            "generation": generation,
            "source_bytes": len(capture_source),
            "config_bytes": len(config_bytes),
            "source_sha256": source_hash,
            "config_sha256": config_hash,
        }
        if (
            not isinstance(ack, dict)
            or any(ack.get(key) != value for key, value in expected.items())
            or any(type(ack.get(key)) is not int for key in ("capture_port", "source_bytes", "config_bytes"))
            or not 1024 <= ack["capture_port"] <= 65535
        ):
            raise ValueError("observer staging final ACK is invalid")
        _atomic_json(output / "staging-proof.json", dict(ack, transport="scp", shared_deadline_seconds=30))
        return result
    except (ValueError, UnicodeError) as error:
        return subprocess.CompletedProcess(
            "observer SCP staging", 1, b"", ("observer SCP staging invalid ACK: " + str(error)).encode()
        )


def run_capture(args) -> int:
    import secrets
    import socket

    jpeg_quality_percent = _delivery_jpeg_quality_percent(getattr(args, "delivery_jpeg_quality_percent", 45))
    bind_address = _delivery_bind_address(getattr(args, "delivery_bind_address", None))
    config = json.loads(args.config.read_text(encoding="utf-8-sig"))
    required = ("expected_host", "camera_name", "video_device_id", "remote_output_root")
    if any(not isinstance(config.get(key), str) or not config[key] for key in required):
        raise ValueError("private observer config is incomplete")
    if not 20 <= args.duration_seconds <= 660:
        raise ValueError("observer duration must be 20 through 660 seconds")
    output = args.output_dir.resolve()
    if output.exists():
        raise ValueError("observer output directory must be fresh")
    output.mkdir(parents=True)
    receiver = ObserverReceiver(output, args.generation)
    token = secrets.token_hex(32)
    capture_source = Path(__file__).with_name("am1_observer_capture.ps1").read_bytes()
    remote_dir = config["remote_output_root"].rstrip("\\/") + "\\" + args.generation
    remote_script, remote_config = remote_dir + "\\capture.ps1", remote_dir + "\\invocation.json"
    config.update(
        generation=args.generation,
        duration_seconds=args.duration_seconds,
        max_recording_bytes=MAX_RECORDING_BYTES,
        output_dir=remote_dir,
        token=token,
        delivery_diagnostics=getattr(args, "delivery_diagnostics", False) is True,
        delivery_jpeg_quality_percent=jpeg_quality_percent,
    )
    stage_script = (
        "$ProgressPreference='SilentlyContinue';$ErrorActionPreference='Stop';"
        f"$d={_ps_quote(remote_dir)};"
        "if(Test-Path -LiteralPath $d){throw 'Observer generation already exists'};"
        "$null=New-Item -ItemType Directory -Path $d;"
        "$s=[Convert]::FromBase64String([Console]::In.ReadLine());"
        "$c=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String([Console]::In.ReadLine()))|ConvertFrom-Json;"
        "if($env:COMPUTERNAME -ne $c.expected_host){throw 'Observer host mismatch'};"
        "$probe=[Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback,0);"
        "$probe.Start();$port=$probe.LocalEndpoint.Port;$probe.Stop();"
        "$c|Add-Member -NotePropertyName capture_port -NotePropertyValue $port;"
        f"[IO.File]::WriteAllBytes({_ps_quote(remote_script)},$s);"
        f"[IO.File]::WriteAllText({_ps_quote(remote_config)},($c|ConvertTo-Json -Compress),[Text.UTF8Encoding]::new($false));"
        "[Console]::Out.WriteLine((@{capture_port=$port}|ConvertTo-Json -Compress))"
    )
    try:
        if getattr(args, "stage_via_scp", False) is True:
            stage = _scp_observer_stage(args, config, capture_source, remote_dir, output)
        else:
            stage = subprocess.run(
                _ssh(args.ssh_host) + _powershell(stage_script),
                input=(
                    base64.b64encode(capture_source).decode()
                    + "\n"
                    + base64.b64encode(json.dumps(config).encode()).decode()
                    + "\n"
                ).encode(),
                capture_output=True,
                timeout=30,
            )
    except subprocess.TimeoutExpired as error:
        (output / "stage.stderr").write_bytes((error.stderr or b"")[-65536:])
        _atomic_json(
            output / "staging-failure.json",
            {
                "generation": args.generation,
                "failure": "observer remote staging timeout",
                "timeout_seconds": 30,
                "capture_launched": False,
            },
        )
        receiver._unqualify("observer remote staging timeout")
        return 1
    (output / "stage.stderr").write_bytes(stage.stderr[-65536:])
    if stage.returncode:
        receiver._unqualify("observer remote staging failed")
        raise RuntimeError("observer remote staging failed; private stderr retained")
    remote_port = json.loads(stage.stdout)["capture_port"]
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        local_port = probe.getsockname()[1]
    compression = getattr(args, "delivery_compression", False) is True
    ipqos_none = getattr(args, "delivery_ipqos_none", False) is True
    transport_evidence = {
        "generation": args.generation,
        "delivery_compression": compression,
        "delivery_ipqos_none": ipqos_none,
        "delivery_jpeg_quality_percent": jpeg_quality_percent,
    }
    if bind_address is not None:
        transport_evidence["delivery_bind_address"] = bind_address
    _atomic_json(output / "delivery-transport.json", transport_evidence)
    command = _observer_forward_command(
        args.ssh_host,
        local_port,
        remote_port,
        compression=compression,
        ipqos_none=ipqos_none,
        bind_address=bind_address,
    ) + _powershell(
        "$ProgressPreference='SilentlyContinue';$ErrorActionPreference='Stop';"
        f"& {_ps_quote(remote_script)} -ConfigPath {_ps_quote(remote_config)}"
    )
    stopped = threading.Event()
    previous_handlers = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[signum] = signal.signal(signum, lambda *_: stopped.set())
    messages: queue.Queue[tuple[str, bytes, float, int, int, float | None] | None] = queue.Queue(maxsize=16)
    diagnostic = ObserverDeliveryDiagnostics(output, args.generation, enabled=config["delivery_diagnostics"])
    failure = terminal = None
    started = time.perf_counter()
    connected = None
    connection_id = 0
    capture_started = False
    loss_started = None
    reconnect_count = 0
    delivery_issues = 0
    max_rtt = max_capture_age = 0.0
    next_connection_attempt = 0.0
    attempt_pending = False
    forwarding_waiting = False
    forward_process = None
    forward_processes = []
    forward_stderr = []
    dead_transports = set()
    first_transport_failure = None
    next_metadata_read = 0.0
    retired_forwarding = set()

    def enqueue(kind, line, epoch=0, read_started_monotonic_ms=None):
        item = _observer_message(kind, line, epoch, read_started_monotonic_ms)
        try:
            messages.put(item, timeout=0.1)
        except queue.Full:
            stopped.set()
            diagnostic.queue_dropped()

    def read_channel(client, epoch):
        try:
            with client.makefile("rb") as stream:
                _read_observer_lines(
                    stream,
                    enqueue,
                    epoch,
                    clock_ms=(lambda: time.perf_counter() * 1000) if diagnostic.enabled else None,
                )
        except OSError as error:
            enqueue(
                "transport_error",
                json.dumps({"error_type": type(error).__name__, "error": str(error)}).encode(),
                epoch,
            )
        finally:
            enqueue("transport_lost", b"{}", epoch)

    def send(value):
        if connected is None:
            return False
        began = time.perf_counter() * 1000
        success = False
        try:
            connected.sendall(
                (json.dumps(dict(value, token=token, generation=args.generation)) + "\n").encode()
            )
            success = True
        except OSError:
            pass
        ended = time.perf_counter() * 1000
        issued = receiver.challenges.get(value.get("nonce"))
        with (output / "challenge-sends.ndjson").open("a", encoding="utf-8") as timings:
            timings.write(
                json.dumps(
                    {
                        "event": value.get("event"),
                        "nonce": value.get("nonce"),
                        "original_issue_monotonic_ms": issued[0] if issued else None,
                        "send_started_monotonic_ms": began,
                        "send_completed_monotonic_ms": ended,
                        "issue_to_send_start_ms": began - issued[0] if issued else None,
                        "send_duration_ms": ended - began,
                        "sent": success,
                    }
                )
                + "\n"
            )
        return success

    def active_transport():
        return forward_process if forward_process is not None else process

    def lose_delivery(reason, now, *, transport_exit=None):
        nonlocal connected, delivery_issues, loss_started, attempt_pending, forwarding_waiting
        nonlocal first_transport_failure
        if connected is None and loss_started is not None and not attempt_pending:
            return
        receiver.expire(now * 1000)
        receiver._unqualify(reason)
        receiver.challenges.clear()
        if connected is not None:
            with suppress(OSError):
                connected.shutdown(socket.SHUT_RDWR)
            connected.close()
            connected = None
        delivery_issues += 1
        loss_started = now if loss_started is None else loss_started
        attempt_pending = forwarding_waiting = False
        if first_transport_failure is None:
            first_transport_failure = {
                "reason": reason,
                "received_wall_time_ms": _wall_time_ms(),
                "transport_exit_code": transport_exit,
            }
        with (output / "protocol-events.ndjson").open("a", encoding="utf-8") as history:
            history.write(
                json.dumps(
                    {
                        "event": "delivery_lost",
                        "reason": reason,
                        "received_wall_time_ms": _wall_time_ms(),
                        "connection_epoch": connection_id,
                        "transport_exit_code": transport_exit,
                    }
                )
                + "\n"
            )

    with (output / "capture.stderr").open("wb") as stderr:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=stderr)

        def read_metadata():
            assert process.stdout
            while line := process.stdout.readline(MAX_LINE_BYTES + 1):
                enqueue("metadata", line)
            enqueue("ssh_closed", b"{}")

        threading.Thread(target=read_metadata, daemon=True).start()
        stop_sent_at = None
        qualification_at_stop = False
        next_challenge = 0.0
        counter = 0
        last_request = None
        try:
            with (output / "frame-health.ndjson").open("w", encoding="utf-8", newline="\n") as history:
                while True:
                    now = time.perf_counter()
                    if (output / "stop.request").exists() or now - started >= args.duration_seconds + 60:
                        stopped.set()
                    transport = active_transport()
                    if (
                        capture_started
                        and forward_process is not None
                        and transport.poll() is not None
                        and id(transport) not in dead_transports
                    ):
                        dead_transports.add(id(transport))
                        if not stopped.is_set():
                            lose_delivery(
                                "observer SSH transport exited", now, transport_exit=transport.returncode
                            )
                    if loss_started is not None and now - loss_started >= 5 and not stopped.is_set():
                        failure = "observer same-capture delivery reconnect deadline"
                        stopped.set()
                    if stopped.is_set() and stop_sent_at is None:
                        receiver.expire(now * 1000)
                        qualification_at_stop = receiver.current_contiguous_delivery_qualified
                        send({"event": "stop"})
                        stop_sent_at = now
                    if (
                        capture_started
                        and connected is None
                        and now >= next_connection_attempt
                        and (not stopped.is_set() or (attempt_pending and active_transport().poll() is None))
                    ):
                        next_connection_attempt = now + 0.25
                        if loss_started is not None and not attempt_pending:
                            if reconnect_count >= 3:
                                failure = "observer same-capture reconnect attempt limit"
                                stopped.set()
                            else:
                                reconnect_count += 1
                                attempt_pending = True
                                # A live source-owning SSH process can still have a stalled data
                                # forward. Rebuild only transport to the same capture on a new local
                                # port; its source owner, remote port and nonce history stay intact.
                                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                                    probe.bind(("127.0.0.1", 0))
                                    local_port = probe.getsockname()[1]
                                stream = (output / f"forward-reattach-{reconnect_count}.stderr").open("wb")
                                forward_stderr.append(stream)
                                forward_process = subprocess.Popen(
                                    _observer_forward_command(
                                        args.ssh_host,
                                        local_port,
                                        remote_port,
                                        compression=compression,
                                        forwarding_only=True,
                                        ipqos_none=ipqos_none,
                                        bind_address=bind_address,
                                    ),
                                    stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL,
                                    stderr=stream,
                                )
                                forward_processes.append(forward_process)
                                forwarding_waiting = True
                                with (output / "protocol-events.ndjson").open(
                                    "a", encoding="utf-8"
                                ) as protocol:
                                    protocol.write(
                                        json.dumps(
                                            {
                                                "event": "forward_reattach_started",
                                                "attempt": reconnect_count,
                                                "generation": args.generation,
                                                "received_wall_time_ms": _wall_time_ms(),
                                            }
                                        )
                                        + "\n"
                                    )
                        if not stopped.is_set() or attempt_pending:
                            try:
                                connected = socket.create_connection(("127.0.0.1", local_port), timeout=0.5)
                                connected.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                                connected.settimeout(2)
                                connection_id += 1
                                threading.Thread(
                                    target=read_channel, args=(connected, connection_id), daemon=True
                                ).start()
                                receiver.challenges.clear()
                                forwarding_waiting = False
                                if stopped.is_set():
                                    send({"event": "stop"})
                            except OSError:
                                connected = None
                                if not forwarding_waiting:
                                    attempt_pending = False
                    if (
                        # Cleanup reads can block for a second. While the source
                        # stdout is live, drain its ordered FIFO during recovery.
                        (stop_sent_at is not None or id(process) in dead_transports)
                        and connected is None
                        and now >= next_metadata_read
                    ):
                        deadline = (
                            stop_sent_at + 45
                            if stop_sent_at is not None
                            else (loss_started + 5 if loss_started is not None else now + 1)
                        )
                        remaining = deadline - time.perf_counter()
                        if remaining > 0:
                            fetched = _fetch_capture_terminal(
                                args, config, remote_dir, output, timeout=min(1.0, remaining)
                            )
                            next_metadata_read = time.perf_counter() + 0.25
                            if fetched is not None:
                                terminal = fetched
                                receiver.terminal(terminal)
                                _atomic_json(output / "capture-result.json", terminal)
                                if terminal["event"] == "failed" or terminal["success"] is not True:
                                    failure = failure or "observer source capture failed"
                                break
                    if not stopped.is_set() and connected is not None and now >= next_challenge:
                        counter += 1
                        nonce = f"challenge-{counter}"
                        receiver.issue_challenge(nonce, now * 1000)
                        if not send({"event": "frame", "nonce": nonce}):
                            enqueue("transport_lost", b"{}", connection_id)
                        next_challenge = now + CHALLENGE_INTERVAL_SECONDS
                    receiver.expire(now * 1000)
                    request = output / "event-request.json"
                    if request.exists():
                        try:
                            value = json.loads(request.read_text(encoding="utf-8"))
                            marker = value.get("event")
                            if marker != last_request and value.get("generation") == args.generation:
                                receiver.retain_event(marker)
                                last_request = marker
                        except (ValueError, OSError):
                            pass
                    if stop_sent_at is not None and now - stop_sent_at > 45:
                        failure = failure or "observer normal-stop deadline"
                        break
                    try:
                        item = messages.get(timeout=0.025)
                    except queue.Empty:
                        if not capture_started and process.poll() is not None:
                            failure = "observer SSH closed before capture start"
                            break
                        continue
                    if item is None:
                        continue
                    processing_started = time.perf_counter() * 1000 if diagnostic.enabled else None
                    kind, line, received_mono, received_wall, epoch, read_started = item
                    if len(line) > MAX_LINE_BYTES:
                        failure = "observer protocol line limit"
                        stopped.set()
                        continue
                    if kind == "transport_lost":
                        if epoch == connection_id and connected is not None:
                            if stopped.is_set():
                                with suppress(OSError):
                                    connected.shutdown(socket.SHUT_RDWR)
                                connected.close()
                                connected = None
                            else:
                                lose_delivery("observer delivery connection lost", now)
                        continue
                    if kind == "transport_error":
                        with (output / "protocol-events.ndjson").open("a", encoding="utf-8") as protocol:
                            protocol.write(
                                json.dumps(
                                    dict(
                                        json.loads(line),
                                        event=kind,
                                        received_wall_time_ms=received_wall,
                                        connection_epoch=epoch,
                                    )
                                )
                                + "\n"
                            )
                        continue
                    if kind == "ssh_closed":
                        if not capture_started:
                            failure = "observer SSH closed before capture start"
                            break
                        dead_transports.add(id(process))
                        if terminal is None and forward_process is None and not stopped.is_set():
                            lose_delivery(
                                "observer owner SSH stdout closed", now, transport_exit=process.poll()
                            )
                        continue
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise ValueError("observer protocol must be an object")
                    if kind == "metadata" and value.get("event") == "frame_delivery_timing":
                        diagnostic.source(
                            value,
                            received_wall_time_ms=received_wall,
                            received_monotonic_ms=received_mono,
                            processing_started_ms=processing_started,
                        )
                        continue
                    if kind == "metadata":
                        with (output / "protocol-events.ndjson").open("a", encoding="utf-8") as protocol:
                            protocol.write(
                                json.dumps(
                                    {
                                        "event": value.get("event"),
                                        "received_wall_time_ms": received_wall,
                                        "queue_delay_ms": time.perf_counter() * 1000 - received_mono,
                                    }
                                )
                                + "\n"
                            )
                    if kind == "frame":
                        if epoch != connection_id:
                            if diagnostic.enabled:
                                diagnostic.receiver(
                                    value,
                                    read_started_ms=read_started,
                                    received_monotonic_ms=received_mono,
                                    received_wall_time_ms=received_wall,
                                    processing_started_ms=processing_started,
                                    processing_completed_ms=time.perf_counter() * 1000,
                                    accepted=False,
                                    connection_epoch=epoch,
                                )
                            continue
                        accepted = receiver.accept(
                            value, received_wall_time_ms=received_wall, received_monotonic_ms=received_mono
                        )
                        metadata = {key: item for key, item in receiver.state.items() if key != "frame_path"}
                        history.write(
                            json.dumps(
                                dict(
                                    metadata,
                                    accepted=accepted,
                                    rejection=receiver.last_rejection if not accepted else None,
                                )
                            )
                            + "\n"
                        )
                        history.flush()
                        if diagnostic.enabled:
                            diagnostic.receiver(
                                value,
                                read_started_ms=read_started,
                                received_monotonic_ms=received_mono,
                                received_wall_time_ms=received_wall,
                                processing_started_ms=processing_started,
                                processing_completed_ms=time.perf_counter() * 1000,
                                accepted=accepted,
                                connection_epoch=epoch,
                            )
                        if accepted:
                            loss_started = None
                            attempt_pending = forwarding_waiting = False
                            max_rtt = max(max_rtt, receiver.state["round_trip_ms"])
                            max_capture_age = max(max_capture_age, receiver.state["capture_age_ms"])
                    elif value.get("event") in ("complete", "failed"):
                        terminal = _validate_capture_terminal(value, args.generation)
                        receiver.terminal(terminal)
                        _atomic_json(output / "capture-result.json", value)
                        if terminal["event"] == "failed" or terminal["success"] is not True:
                            failure = failure or "observer source capture failed"
                        break
                    elif value.get("event") == "ending":
                        ending_now = time.perf_counter()
                        if stop_sent_at is None:
                            receiver.expire(ending_now * 1000)
                            qualification_at_stop = receiver.current_contiguous_delivery_qualified
                            stop_sent_at = ending_now
                        _atomic_json(
                            output / "capture-ending.json",
                            {
                                "source": value,
                                "received_wall_time_ms": received_wall,
                                "qualification_checked_monotonic_ms": ending_now * 1000,
                            },
                        )
                        stopped.set()
                        receiver._unqualify("observer capture ending")
                    elif value.get("event") == "started":
                        capture_started = True
                        _atomic_json(output / "capture-started.json", value)
                    else:
                        failure = "observer unexpected protocol event"
                        stopped.set()
        except (OSError, ValueError) as error:
            failure = f"observer transport/protocol failure: {error}"
        finally:
            if process.poll() is None:
                send({"event": "stop"})
                try:
                    close_delivery_and_wait(process, connected, timeout=45)
                    connected = None
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                    failure = failure or "observer SSH release unknown; remote finite ceiling retained"
            if connected is not None:
                connected.close()
            for transport in forward_processes:
                if transport.poll() is None:
                    retired_forwarding.add(id(transport))
                    transport.terminate()  # This process owns only a local SSH forward, never the capture.
                    try:
                        transport.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        transport.kill()
                        transport.wait(timeout=5)
            for stream in forward_stderr:
                stream.close()
            receiver.terminal(terminal or {"event": "failed", "generation": args.generation})
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)
            diagnostic.finish(terminal.get("delivery_diagnostics") if terminal is not None else None)
            diagnostic.close()
    summary = {
        "generation": args.generation,
        "accepted_frames": receiver.accepted_frames,
        "rejected_frames": receiver.rejected_frames,
        "challenges_issued": counter,
        "ssh_exit_code": active_transport().returncode,
        "owner_ssh_exit_code": process.returncode,
        "forwarding_retired": id(active_transport()) in retired_forwarding,
        "first_transport_failure": first_transport_failure,
        "delivery_compression": compression,
        "delivery_ipqos_none": ipqos_none,
        "delivery_jpeg_quality_percent": jpeg_quality_percent,
        "source_delivery_jpeg_quality_percent": (
            terminal.get("delivery_jpeg_quality_percent") if terminal is not None else None
        ),
        "failure": failure,
        "camera_released": terminal is not None and terminal.get("camera_released") is True,
        "capture_success": terminal is not None and terminal.get("success") is True,
        "continuous_delivery_qualified": receiver.continuous_delivery_qualified and delivery_issues == 0,
        "qualification_at_stop": qualification_at_stop,
        "current_contiguous_delivery_qualified": receiver.current_contiguous_delivery_qualified,
        "max_contiguous_delivery_span_ms": receiver.max_contiguous_delivery_span_ms,
        "timing_basis": "qpc_elapsed_v1",
        "local_clock_resolution_ms": receiver.local_clock_resolution_ms,
        "local_clock_implementation": receiver.local_clock_implementation,
        "delivery_issues": delivery_issues,
        "same_capture_reconnects": reconnect_count,
        "max_round_trip_ms": max_rtt,
        "max_capture_age_ms": max_capture_age,
        "delivered_span_ms": receiver.delivered_span_ms,
        "max_delivery_gap_ms": receiver.max_delivery_gap_ms,
        "max_effective_capture_age_ms": receiver.max_effective_capture_age_ms,
        "freshness_gap_violations": receiver.freshness_gap_violations,
        "round_trip_deadline_violations": receiver.round_trip_deadline_violations,
        "startup_to_first_delivery_ms": (
            receiver.first_received_monotonic_ms - started * 1000
            if receiver.first_received_monotonic_ms is not None
            else None
        ),
    }
    if diagnostic.enabled:
        summary["delivery_diagnostics"] = diagnostic.snapshot()
    if bind_address is not None:
        summary["delivery_bind_address"] = bind_address
    _atomic_json(output / "receiver-result.json", summary)
    print(json.dumps({key: value for key, value in summary.items() if key != "delivery_bind_address"}))
    return (
        0
        if not failure
        and (active_transport().returncode == 0 or id(active_transport()) in retired_forwarding)
        and summary["camera_released"]
        and summary["capture_success"]
        and summary["qualification_at_stop"]
        else 1
    )


def retrieve_recording(args) -> int:
    """Retrieve a finalized, bounded private recording after owned camera cleanup."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", args.ssh_host):
        raise ValueError("use an existing simple SSH alias")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", args.generation):
        raise ValueError("invalid observer generation")
    output = args.output_dir.resolve()
    config = json.loads(args.config.read_text(encoding="utf-8-sig"))
    result_path = getattr(args, "capture_result", None) or output / "capture-result.json"
    result = json.loads(Path(result_path).read_text(encoding="utf-8-sig"))
    if (
        not isinstance(result, dict)
        or result.get("generation") != args.generation
        or result.get("success") is not True
        or result.get("camera_released") is not True
    ):
        raise ValueError("recording requires final same-generation camera cleanup")
    expected = str(Path(config["remote_output_root"]) / args.generation / "continuous.mp4")
    if (
        str(result.get("clip_path", "")).replace("\\", "/").casefold()
        != expected.replace("\\", "/").casefold()
    ):
        raise ValueError("recording is outside this private capture invocation")
    length = result.get("clip_bytes")
    digest = result.get("clip_sha256")
    if (
        type(length) is not int
        or not 1024 <= length <= MAX_RECORDING_BYTES
        or not isinstance(digest, str)
        or not re.fullmatch(r"[a-fA-F0-9]{64}", digest)
    ):
        raise ValueError("recording size/hash must be bounded and known")
    temporary, target = output / "continuous.mp4.partial", output / "continuous.mp4"
    if temporary.exists() or target.exists():
        raise ValueError("recording retrieval destination must be fresh")
    remote_path = expected.replace("\\", "/")
    transfer = subprocess.run(
        [
            "scp",
            "-B",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            f"{args.ssh_host}:{remote_path}",
            str(temporary),
        ],
        capture_output=True,
        timeout=120,
    )
    (output / "recording-retrieval.stderr").write_bytes(transfer.stderr[-65536:])
    value = {
        "generation": args.generation,
        "verified": False,
        "retrieved_wall_time_ms": time.time_ns() // 1_000_000,
    }
    if transfer.returncode:
        value.update(reason="private finalized recording transfer failed", exit_code=transfer.returncode)
    elif temporary.stat().st_size != length:
        value.update(reason="private recording length mismatch")
    else:
        hasher = hashlib.sha256()
        with temporary.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                hasher.update(chunk)
        actual = hasher.hexdigest()
        if actual.casefold() != digest.casefold():
            value.update(reason="private recording hash mismatch", actual_sha256=actual)
        else:
            os.replace(temporary, target)
            value.update(verified=True, clip_path=str(target), clip_bytes=length, clip_sha256=actual)
    _atomic_json(output / "recording-retrieval.json", value)
    print(json.dumps(value))
    return 0 if value["verified"] else 1


MAX_EVENT_EXPORT_BYTES = 96 * 1024 * 1024
MAX_EVENT_CLIP_SECONDS = 12.0


def _event_json(path: Path, *, max_bytes: int = 65536) -> dict:
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= max_bytes:
        raise ValueError("event export metadata must be a bounded ordinary file")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("event export metadata must be an object")
    return value


def _event_export_plan(args) -> tuple[Path, Path, dict, list[dict]]:
    """Validate every source, owner and requested interval before running media tools."""
    from datetime import datetime
    from pathlib import PureWindowsPath

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", args.generation):
        raise ValueError("invalid observer generation")
    output = args.output_dir.resolve()
    source = output / "continuous.mp4"
    config = _event_json(Path(args.config))
    final = _event_json(
        Path(getattr(args, "capture_result", None) or output / "capture-result.json"), max_bytes=256 * 1024
    )
    retrieved = _event_json(output / "recording-retrieval.json")
    if (
        final.get("generation") != args.generation
        or final.get("success") is not True
        or final.get("camera_released") is not True
        or final.get("diagnostic_no_camera") is True
        or final.get("fake_source") is True
    ):
        raise ValueError("event export requires final same-generation camera cleanup")
    try:
        stamps = [
            datetime.fromisoformat(final[key].replace("Z", "+00:00"))
            for key in (
                "recording_requested_utc",
                "recording_started_utc",
                "recording_stop_requested_utc",
                "recording_stopped_utc",
            )
        ]
        if any(stamp.tzinfo is None for stamp in stamps) or any(
            later < earlier for earlier, later in zip(stamps, stamps[1:], strict=False)
        ):
            raise ValueError
    except (KeyError, AttributeError, TypeError, ValueError) as error:
        raise ValueError("event export requires proven recording start/stop ordering") from error
    remote_root = PureWindowsPath(config.get("remote_output_root", ""))
    remote_source = PureWindowsPath(str(final.get("clip_path", "")))
    if (
        not remote_root.is_absolute()
        or ".." in remote_root.parts
        or ".." in remote_source.parts
        or remote_source != remote_root / args.generation / "continuous.mp4"
    ):
        raise ValueError("event recording source is outside its private invocation")
    length, digest = final.get("clip_bytes"), final.get("clip_sha256")
    if (
        type(length) is not int
        or not 1024 <= length <= MAX_RECORDING_BYTES
        or not isinstance(digest, str)
        or not re.fullmatch(r"[a-fA-F0-9]{64}", digest)
        or source.is_symlink()
        or not source.is_file()
        or source.resolve() != source
        or source.stat().st_size != length
        or retrieved.get("generation") != args.generation
        or retrieved.get("verified") is not True
        or Path(str(retrieved.get("clip_path", ""))).resolve() != source
        or retrieved.get("clip_bytes") != length
        or str(retrieved.get("clip_sha256", "")).casefold() != digest.casefold()
    ):
        raise ValueError("event recording must be bounded and locally verified")
    hasher = hashlib.sha256()
    with source.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            hasher.update(chunk)
    if source.stat().st_size != length or hasher.hexdigest().casefold() != digest.casefold():
        raise ValueError("event recording hash mismatch")
    requested, started = final.get("recording_requested_qpc_ticks"), final.get("recording_started_qpc_ticks")
    hold, window = final.get("record_hold_seconds"), getattr(args, "event_clip_seconds", 6.0)
    if (
        type(requested) is not int
        or type(started) is not int
        or not 0 < requested <= started
        or type(hold) not in (int, float)
        or not math.isfinite(hold)
        or not 0 < hold <= 665
        or type(window) not in (int, float)
        or not math.isfinite(window)
        or not 0 < window <= MAX_EVENT_CLIP_SECONDS
    ):
        raise ValueError("event export recording/window time range is invalid")
    export = Path(getattr(args, "event_export_dir", None) or output / "event-export")
    if export.parent.resolve() != output or export.exists() or export.is_symlink():
        raise ValueError("event export destination must be a fresh direct child of this invocation")
    event_dir = output / "events"
    if event_dir.is_symlink() or not event_dir.is_dir() or event_dir.resolve() != event_dir:
        raise ValueError("event metadata directory is outside this invocation")
    paths = sorted(event_dir.glob("*.json"))
    if not 1 <= len(paths) <= 128:
        raise ValueError("event export requires one through 128 retained labels")
    planned = []
    reserved = {"CON", "PRN", "AUX", "NUL"} | {
        prefix + str(index) for prefix in ("COM", "LPT") for index in range(1, 10)
    }
    for path in paths:
        label = path.stem
        event = _event_json(path)
        ticks = event.get("source_system_relative_ticks")
        if (
            not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", label)
            or label.upper() in reserved
            or event.get("event") != label
            or event.get("generation") != args.generation
            or event.get("diagnostic_no_camera") is True
            or event.get("fake_source") is True
            or type(ticks) is not int
        ):
            raise ValueError("event label, generation or original source timestamp is invalid")
        lower, upper = (ticks - started) / 10_000_000, (ticks - requested) / 10_000_000
        if not 0 <= lower <= upper <= hold or upper - lower > window:
            raise ValueError("event source timestamp is outside the finalized recording time range")
        selected = (lower + upper) / 2
        duration = min(window, hold)
        beginning = max(0.0, min(selected - duration / 2, hold - duration))
        planned.append(
            {
                "label": label,
                "source_system_relative_ticks": ticks,
                "source_sequence": event.get("sequence"),
                "source_offset_interval_seconds": [lower, upper],
                "selection_offset_seconds": selected,
                "clip_start_seconds": beginning,
                "clip_duration_seconds": duration,
                "event_challenge_qualified": event.get("challenge_qualified") is True,
                "exact_source_frame_match": False,
            }
        )
    return source, export.resolve(), final, planned


def export_events(args) -> int:
    """Export bounded post-cleanup clips/stills without changing live observer health."""
    from PIL import Image

    source, export, final, planned = _event_export_plan(args)
    ffmpeg = Path(getattr(args, "ffmpeg", None) or "")
    ffprobe = Path(getattr(args, "ffprobe", None) or ffmpeg.with_name("ffprobe" + ffmpeg.suffix))
    if not ffmpeg.is_file() or not ffprobe.is_file():
        raise ValueError("event export requires existing ffmpeg and ffprobe executables")
    inspected = subprocess.run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-protocol_whitelist",
            "file,pipe",
            "-f",
            "mov",
            "-show_entries",
            "format=duration,start_time:stream=codec_type,width,height,start_time,duration",
            "-of",
            "json",
            str(source),
        ],
        capture_output=True,
        timeout=30,
    )
    if inspected.returncode or len(inspected.stdout) > 65536:
        raise ValueError("event recording media inspection failed")
    try:
        media = json.loads(inspected.stdout)
        video = [stream for stream in media["streams"] if stream.get("codec_type") == "video"]
        media_start = float(media["format"].get("start_time", 0.0))
        media_duration = float(media["format"]["duration"])
        if (
            len(video) != 1
            or video[0].get("width") != 1280
            or video[0].get("height") != 720
            or not math.isfinite(media_start)
            or not math.isfinite(media_duration)
            or not 0 < media_duration <= 667
            or any(
                item["source_offset_interval_seconds"][0] > media_start + media_duration for item in planned
            )
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("event recording dimensions or media time range is invalid") from error
    export.mkdir()
    manifest = {
        "generation": args.generation,
        "complete": False,
        "source_path": str(source),
        "source_bytes": final["clip_bytes"],
        "source_sha256": final["clip_sha256"].lower(),
        "recording_requested_qpc_ticks": final["recording_requested_qpc_ticks"],
        "recording_started_qpc_ticks": final["recording_started_qpc_ticks"],
        "media_start_pts_seconds": media_start,
        "media_duration_seconds": media_duration,
        "timing_note": (
            "Source QPC is mapped to an interval between recording requested/start QPC anchors. "
            "Both zero-PTS and container-start-shifted anchor interpretations are retained. "
            "Decoded PTS and frame quantization do not establish an exact original "
            "camera-frame correspondence."
        ),
        "events": [],
    }
    reserve = 1024 * 1024

    def budget() -> int:
        used = sum(path.stat().st_size for path in export.iterdir() if path.is_file())
        if used > MAX_EVENT_EXPORT_BYTES:
            raise ValueError("event output budget exceeded")
        return MAX_EVENT_EXPORT_BYTES - used

    def decode(command: list[str], target: Path, maximum: int):
        available = budget() - reserve
        if available <= 65536:
            raise ValueError("event output budget exhausted")
        limit = min(maximum, available)
        command += ["-fs", str(limit), str(target)]
        result = subprocess.run(command, capture_output=True, timeout=60)
        target.with_suffix(target.suffix + ".stderr").write_bytes(result.stderr[-65536:])
        if result.returncode:
            raise ValueError("event media decoding failed; private decoder stderr retained")
        if not target.is_file():
            raise ValueError("event decoder did not produce its selected output")
        if target.stat().st_size > limit or budget() < reserve:
            target.unlink()
            raise ValueError("event output budget exceeded")
        if not target.stat().st_size:
            raise ValueError("event media decoding failed")
        return result

    try:
        for planned_event in planned:
            event = dict(planned_event)
            label = event["label"]
            clip, still = export / f"{label}.mp4", export / f"{label}.jpg"
            event["clip_duration_seconds"] = min(event["clip_duration_seconds"], media_duration)
            event["clip_start_seconds"] = min(
                event["clip_start_seconds"], media_duration - event["clip_duration_seconds"]
            )
            event["container_start_shifted_interval_seconds"] = [
                media_start + value for value in event["source_offset_interval_seconds"]
            ]
            requested_pts = event["selection_offset_seconds"]
            event["selection_pts_origin"] = "zero"
            if not media_start <= requested_pts <= media_start + media_duration:
                requested_pts += media_start
                event["selection_pts_origin"] = "container_start"
            if not media_start <= requested_pts <= media_start + media_duration:
                raise ValueError("event selection PTS is outside the finalized recording")
            decode(
                [
                    str(ffmpeg),
                    "-nostdin",
                    "-hide_banner",
                    "-v",
                    "error",
                    "-n",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-ss",
                    str(event["clip_start_seconds"]),
                    "-f",
                    "mov",
                    "-i",
                    str(source),
                    "-map",
                    "0:v:0",
                    "-an",
                    "-sn",
                    "-dn",
                    "-t",
                    str(event["clip_duration_seconds"]),
                    "-c:v",
                    "mpeg4",
                    "-b:v",
                    "1200k",
                    "-maxrate",
                    "1500k",
                    "-bufsize",
                    "3000k",
                    "-threads",
                    "1",
                    "-movflags",
                    "+faststart",
                    "-f",
                    "mp4",
                ],
                clip,
                8 * 1024 * 1024,
            )
            decoded = decode(
                [
                    str(ffmpeg),
                    "-nostdin",
                    "-hide_banner",
                    "-v",
                    "info",
                    "-n",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-ss",
                    str(max(0.0, event["selection_offset_seconds"] - 1.0)),
                    "-copyts",
                    "-f",
                    "mov",
                    "-i",
                    str(source),
                    "-map",
                    "0:v:0",
                    "-an",
                    "-sn",
                    "-dn",
                    "-vf",
                    f"select=gte(t\\,{max(media_start, requested_pts - 0.05):.9f}),showinfo",
                    "-frames:v",
                    "1",
                    "-vsync",
                    "0",
                    "-c:v",
                    "mjpeg",
                    "-q:v",
                    "2",
                    "-threads",
                    "1",
                    "-f",
                    "image2",
                ],
                still,
                4 * 1024 * 1024,
            )
            text = decoded.stderr[-65536:].decode("utf-8", errors="replace")
            match = re.search(r"\bn:\s*0\b[^\r\n]*\bpts_time:([^\s]+)", text)
            if match is None:
                raise ValueError("selected event still has no decoded media PTS")
            pts = float(match[1])
            if not math.isfinite(pts) or not media_start <= pts <= media_start + media_duration:
                raise ValueError("selected event still media PTS is outside the recording")
            with Image.open(still) as image:
                image.load()
                if image.format != "JPEG" or image.size != (1280, 720):
                    raise ValueError("selected event still must preserve original 1280x720 pixels")
            event.update(
                clip_path=str(clip),
                clip_bytes=clip.stat().st_size,
                still_path=str(still),
                still_bytes=still.stat().st_size,
                requested_media_pts_seconds=requested_pts,
                selected_frame_pts_seconds=pts,
                selected_frame_offset_seconds=pts - media_start,
            )
            manifest["events"].append(event)
            if not _atomic_json(export / "manifest.json", manifest):
                raise ValueError("event manifest publication failed")
            budget()
        manifest["complete"] = True
        for _ in range(3):
            manifest["export_bytes"] = MAX_EVENT_EXPORT_BYTES - budget()
            if not _atomic_json(export / "manifest.json", manifest):
                raise ValueError("event manifest publication failed")
            if manifest["export_bytes"] == MAX_EVENT_EXPORT_BYTES - budget():
                break
        else:
            raise ValueError("event manifest byte accounting did not stabilize")
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        manifest["error"] = str(error)
        _atomic_json(export / "manifest.json", manifest)
        raise
    print(
        json.dumps(
            {
                "generation": args.generation,
                "export_dir": str(export),
                "exported_events": len(manifest["events"]),
                "complete": True,
            }
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ssh-host", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--generation", default=uuid.uuid4().hex)
    parser.add_argument("--duration-seconds", type=int, default=660)
    parser.add_argument(
        "--stage-via-scp",
        action="store_true",
        help="Stage bounded inert files via SCP and verify their hashes before camera launch",
    )
    parser.add_argument(
        "--delivery-compression",
        action="store_true",
        help="Use compression only on this capture's SSH forwarding transports",
    )
    parser.add_argument(
        "--delivery-diagnostics",
        action="store_true",
        help="Retain bounded source and receiver timing evidence for this finite capture only",
    )
    parser.add_argument(
        "--delivery-ipqos-none",
        action="store_true",
        help="Use the OS-default IP QoS only on this capture's SSH forwarding transports",
    )
    parser.add_argument(
        "--delivery-bind-address",
        type=_delivery_bind_address,
        help="Bind only this capture's SSH forwarding transports to a numeric IPv4 source address",
    )
    parser.add_argument(
        "--delivery-jpeg-quality-percent",
        type=int,
        choices=(15, 45),
        default=45,
        help="Use this live JPEG quality percent for this capture only; recording quality stays unchanged",
    )
    offline = parser.add_mutually_exclusive_group()
    offline.add_argument("--retrieve-only", action="store_true")
    offline.add_argument("--export-events", action="store_true")
    parser.add_argument("--ffmpeg", type=Path)
    parser.add_argument("--ffprobe", type=Path)
    parser.add_argument("--event-export-dir", type=Path)
    parser.add_argument("--event-clip-seconds", type=float, default=6.0)
    parser.add_argument("--capture-result", type=Path)
    args = parser.parse_args()
    if args.export_events:
        return export_events(args)
    return retrieve_recording(args) if args.retrieve_only else run_capture(args)


if __name__ == "__main__":
    raise SystemExit(main())
