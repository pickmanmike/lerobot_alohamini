"""Private bounded JSON IPC and explicit fake owner. No hardware adapters."""

import argparse
import asyncio
import contextlib
import csv
import json
import math
import os
import secrets
import struct
import subprocess
import threading
import uuid
from pathlib import Path

from tools.am1_session_core import SessionAuthority

CONTROL_LIMIT = 4096
RESPONSE_LIMIT = 65536
IO_TIMEOUT = 1.0
PROTECTIVE = {"pause", "stop", "release"}


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def invalid(value):
        raise ValueError("nonfinite JSON")

    def finite(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("nonfinite JSON")
        return number

    return json.loads(data, object_pairs_hook=pairs, parse_constant=invalid, parse_float=finite)


def encode(value, limit=RESPONSE_LIMIT):
    data = json.dumps(value, allow_nan=False, separators=(",", ":")).encode()
    if len(data) > limit:
        raise ValueError("message too large")
    return data


def private_directory(path):
    """Fail closed if current-user-only state permissions cannot be established."""
    path = Path(path)
    for ancestor in [path, *path.parents]:
        if ancestor.is_symlink() or ancestor.is_junction():
            raise ValueError("redirected state path refused")
    path.mkdir(parents=True, exist_ok=True)
    marker = path / ".am1-fake-state"
    if not marker.exists() and any(path.iterdir()):
        raise ValueError("dedicated empty or marked fake state directory required")
    if marker.exists() and marker.read_bytes() != b"am1-fake-private-state-v1\n":
        raise ValueError("invalid fake state marker")
    targets = [path]
    for root, directories, files in os.walk(path, followlinks=False):
        for name in [*directories, *files]:
            target = Path(root) / name
            if (
                target.is_symlink()
                or target.is_junction()
                or (target.is_file() and target.stat().st_nlink != 1)
            ):
                raise ValueError("redirected state entry refused")
            targets.append(target)
    if os.name == "nt":
        row = next(
            csv.reader(
                subprocess.check_output(["whoami", "/user", "/fo", "csv", "/nh"], text=True).splitlines()
            )
        )
        import ctypes
        from ctypes import wintypes

        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
        convert.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.DWORD),
        ]
        convert.restype = wintypes.BOOL
        get_dacl = advapi.GetSecurityDescriptorDacl
        get_dacl.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.BOOL),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.BOOL),
        ]
        get_dacl.restype = wintypes.BOOL
        set_acl = advapi.SetNamedSecurityInfoW
        set_acl.argtypes = [
            wintypes.LPWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        set_acl.restype = wintypes.DWORD
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        descriptor, dacl = ctypes.c_void_p(), ctypes.c_void_p()
        present, defaulted = wintypes.BOOL(), wintypes.BOOL()
        if not convert(f"D:P(A;OICI;FA;;;{row[1]})", 1, ctypes.byref(descriptor), None):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if (
                not get_dacl(descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted))
                or not present
            ):
                raise OSError("private DACL unavailable")
            # Replace all explicit/inherited grants on only this dedicated fake state tree.
            for target in targets:
                error = set_acl(str(target), 1, 0x80000004, None, None, dacl, None)
                if error:
                    raise ctypes.WinError(error)
        finally:
            kernel.LocalFree(descriptor)
    else:
        for target in targets:
            target.chmod(0o700 if target.is_dir() else 0o600)
    if not marker.exists():
        marker.write_bytes(b"am1-fake-private-state-v1\n")
        if os.name != "nt":
            marker.chmod(0o600)
    return path


def private_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(".new")
    with temporary.open("wb") as stream:
        if os.name != "nt":
            os.fchmod(stream.fileno(), 0o600)
        stream.write(encode(value))
    temporary.replace(path)


async def read_frame(reader, limit):
    header = await asyncio.wait_for(reader.readexactly(4), IO_TIMEOUT)
    length = struct.unpack("!I", header)[0]
    if length == 0 or length > limit:
        raise ValueError("frame length")
    return await asyncio.wait_for(reader.readexactly(length), IO_TIMEOUT)


async def write_frame(writer, value, limit):
    data = encode(value, limit)
    writer.write(struct.pack("!I", len(data)) + data)
    await asyncio.wait_for(writer.drain(), IO_TIMEOUT)


class OwnerServer:
    """Owner-local tick thread never waits for IPC reads, subscribers or gateway writes."""

    def __init__(self, state, executor=None):
        self.directory = private_directory(state)
        previous_umask = os.umask(0o077) if os.name != "nt" else None
        try:
            self.authority = SessionAuthority(self.directory, executor=executor)
        finally:
            if previous_umask is not None:
                os.umask(previous_umask)
        self.secret = secrets.token_hex(32)
        self.servers = []
        self.active = {False: 0, True: 0}
        self.slots = {False: asyncio.Semaphore(6), True: asyncio.Semaphore(2)}
        self.stopping = threading.Event()
        self.ticker = threading.Thread(target=self._tick, daemon=True)

    def _tick(self):
        while not self.stopping.wait(0.05):
            self.authority.tick()

    async def start(self):
        # Authority/OS lock must precede listening and publishing admission metadata.
        self.ticker.start()
        lanes = {}
        for protective in (False, True):

            def callback(r, w, lane=protective):
                return self.serve(r, w, lane)

            if os.name == "nt":
                server = await asyncio.start_server(callback, "127.0.0.1", 0, limit=CONTROL_LIMIT + 4)
                lanes[str(protective)] = {"port": server.sockets[0].getsockname()[1]}
            else:
                path = self.directory / ("protective.sock" if protective else "normal.sock")
                path.unlink(missing_ok=True)
                server = await asyncio.start_unix_server(callback, str(path), limit=CONTROL_LIMIT + 4)
                path.chmod(0o600)
                lanes[str(protective)] = {"socket": str(path)}
            self.servers.append(server)
        private_json(self.directory / "ipc.json", {"secret": self.secret, "lanes": lanes, "pid": os.getpid()})

    async def serve(self, reader, writer, protective):
        if self.active[protective] >= (4 if protective else 12):
            writer.close()
            return
        self.active[protective] += 1
        try:
            envelope = strict_json(await read_frame(reader, CONTROL_LIMIT))
            if not isinstance(envelope, dict) or set(envelope) != {"secret", "id", "device", "command"}:
                raise ValueError("envelope")
            if not secrets.compare_digest(envelope["secret"], self.secret):
                raise ValueError("authentication")
            uuid.UUID(envelope["id"])
            command = envelope["command"]
            if (
                not isinstance(command, dict)
                or not isinstance(envelope["device"], str)
                or not 1 <= len(envelope["device"]) <= 128
            ):
                raise ValueError("command")
            if protective and command.get("op") not in PROTECTIVE:
                raise ValueError("protective lane")
            async with asyncio.timeout(IO_TIMEOUT):
                async with self.slots[protective]:
                    # Never call tick before a protective operation; core owns ordering.
                    if command == {"op": "snapshot"}:
                        snapshot = await asyncio.to_thread(self.authority.snapshot)
                        result = {"accepted": True, "status": "snapshot", "snapshot": snapshot}
                    else:
                        result = await asyncio.to_thread(self.authority.handle, command, envelope["device"])
            await write_frame(writer, {"id": envelope["id"], "result": result}, RESPONSE_LIMIT)
        except (TimeoutError, ValueError, TypeError, KeyError, asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            self.active[protective] -= 1
            writer.close()
            with contextlib.suppress(TimeoutError, ConnectionError):
                await asyncio.wait_for(writer.wait_closed(), IO_TIMEOUT)

    async def close(self):
        for server in self.servers:
            server.close()
            await server.wait_closed()
        self.stopping.set()
        await asyncio.to_thread(self.ticker.join, 1)
        self.authority.close()
        (self.directory / "ipc.json").unlink(missing_ok=True)


class IPCClient:
    def __init__(self, state):
        self.metadata_path = Path(state) / "ipc.json"

    async def request(self, command, device):
        # Refresh metadata for a new owner incarnation; no secrets in request URL/logs.
        metadata = strict_json(self.metadata_path.read_bytes())
        lane = metadata["lanes"][str(command.get("op") in PROTECTIVE)]
        if "socket" in lane:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(lane["socket"], limit=RESPONSE_LIMIT + 4), IO_TIMEOUT
            )
        else:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", lane["port"], limit=RESPONSE_LIMIT + 4), IO_TIMEOUT
            )
        correlation = str(uuid.uuid4())
        try:
            await write_frame(
                writer,
                {"secret": metadata["secret"], "id": correlation, "device": device, "command": command},
                CONTROL_LIMIT,
            )
            reply = strict_json(await read_frame(reader, RESPONSE_LIMIT))
            if reply.get("id") != correlation:
                raise ValueError("reply correlation")
            return reply["result"]
        finally:
            writer.close()
            with contextlib.suppress(TimeoutError, ConnectionError):
                await asyncio.wait_for(writer.wait_closed(), IO_TIMEOUT)


async def run_owner(state, executor=None):
    owner = OwnerServer(state, executor)
    try:
        await owner.start()
        await asyncio.Event().wait()
    finally:
        await owner.close()


def main():
    parser = argparse.ArgumentParser(description="Explicit fake-only resident owner")
    parser.add_argument("mode", choices=["owner"])
    parser.add_argument("--state", required=True)
    args = parser.parse_args()
    asyncio.run(run_owner(args.state))


if __name__ == "__main__":
    main()
