"""In-memory owner side of real observation. No network, decoder or file IO."""

import math
import re
import time
import uuid

SOURCE_KEYS = ("machine", "source", "device", "framing_revision")
BINDING_KEYS = ("run_id", "generation", *SOURCE_KEYS)


def source_identity(value):
    if set(value) != set(SOURCE_KEYS) or any(
        not isinstance(v, str) or not 1 <= len(v) <= 1024 for v in value.values()
    ):
        raise ValueError("exact configured observation identity required")
    return dict(value)


class ObservationMailbox:
    """Single evidence slot, updated under the authority mutex; original clocks never renewed."""

    def __init__(self, source, clock=time.monotonic):
        self.source = source_identity(source)
        self.clock = clock
        self.request = None
        self.record = None
        self.last_sequence = 0
        self.reason = "no current decoded P1 image"

    def begin(self, run_id, live_s):
        uuid.UUID(run_id)
        self.request = dict(
            self.source,
            run_id=run_id,
            generation=str(uuid.uuid4()),
            active=True,
            duration_seconds=min(660, int(live_s) + 60),
            max_recording_bytes=140 * 1024 * 1024,
        )
        self.record = None
        self.last_sequence = 0

    def stop(self):
        if self.request:
            self.request = dict(self.request, active=False)
        self.record = None

    def publish(self, record):
        self.record = None
        self.reason = "invalid or unqualified P1 evidence"
        if not self.request or not self.request["active"] or not isinstance(record, dict):
            return
        if any(record.get(k) != self.request[k] for k in BINDING_KEYS):
            return
        if record.get("qualified") is not True:
            self.reason = str(record.get("reason", "unqualified P1 evidence"))[:160]
            return
        sequence = record.get("sequence")
        if type(sequence) is not int or sequence <= self.last_sequence:
            return
        for key in ("received_at", "decoded_at", "source_age_s"):
            v = record.get(key)
            if type(v) not in (int, float) or not math.isfinite(v) or v < 0:
                return
        if not record["received_at"] <= record["decoded_at"] <= self.clock():
            return
        if not re.fullmatch("[a-f0-9]{64}", str(record.get("frame_sha256", ""))):
            return
        if record["source_age_s"] + self.clock() - record["received_at"] > 0.5:
            self.reason = "original image expired at owner consumption"
            return
        self.last_sequence = sequence
        self.record = dict(record)
        self.reason = None

    def evidence(self):
        record = self.record
        age = None if record is None else record["source_age_s"] + self.clock() - record["received_at"]
        qualified = record is not None and 0 <= age <= 0.5
        return {
            "qualified": qualified,
            "age_s": age,
            "reason": self.reason
            if record is None
            else (None if qualified else "original image expired at owner consumption"),
            "sequence": record["sequence"] if record else None,
            "generation": self.request["generation"] if self.request else None,
            "frame_sha256": record["frame_sha256"] if record else None,
        }
