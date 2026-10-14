"""Portable admission shared by legacy supervision and simulated finite ownership.

Callers supply their already selected canonical session state directory. The
isolated trial uses its own simulated directory, never the physical namespace.
"""

import os
from pathlib import Path


def acquire_session_admission(state_directory):
    directory = Path(state_directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    stream = (directory / "active.lock").open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            stream.seek(0, 2)
            if not stream.tell():
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return stream
    except OSError as exc:
        stream.close()
        raise RuntimeError("Another AM1 session owner is active; refusing takeover") from exc
