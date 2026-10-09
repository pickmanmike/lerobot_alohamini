"""Real fake-only worker/gateway fixture; process termination never targets legacy owners."""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

OPENSSL = (
    os.environ.get("AM1_TEST_OPENSSL")
    or shutil.which("openssl")
    or "C:/Program Files/Git/usr/bin/openssl.exe"
)


def wait_file(path, process):
    end = time.monotonic() + 10
    while time.monotonic() < end:
        if process.poll() is not None:
            raise RuntimeError(process.stderr.read())
        if path.exists():
            return json.loads(path.read_text())
        time.sleep(0.02)
    raise TimeoutError(str(path))


class Cluster:
    def __init__(self, directory):
        from tools.am1_session_ipc import private_directory

        self.directory = private_directory(directory)
        self.owner_dir = self.directory / "owner"
        self.auth_dir = self.directory / "auth"
        self.flags = self.directory / "private-proof.json"
        self.ready = self.directory / "gateway-ready.json"
        self.cert = self.directory / "cert.pem"
        self.key = self.directory / "key.pem"
        subprocess.run(
            [
                OPENSSL,
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                str(self.key),
                "-out",
                str(self.cert),
                "-days",
                "1",
                "-subj",
                "/CN=localhost",
                "-addext",
                "subjectAltName=IP:127.0.0.1,DNS:localhost",
            ],
            check=True,
            capture_output=True,
        )
        self.owner = self.gateway = None
        try:
            self.start_owner()
            self.start_gateway()
        except BaseException:
            self.close()
            raise

    def spawn(self, args):
        return subprocess.Popen(
            [sys.executable, "-B", *args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            env=os.environ.copy(),
        )

    def start_owner(self):
        metadata = self.owner_dir / "ipc.json"
        metadata.unlink(missing_ok=True)
        self.owner = self.spawn(
            [str(Path(__file__).resolve()), "--owner", str(self.owner_dir), str(self.flags)]
        )
        wait_file(metadata, self.owner)

    def start_gateway(self):
        self.ready.unlink(missing_ok=True)
        port = str(self.info["url"].rsplit(":", 1)[1]) if hasattr(self, "info") else "0"
        self.gateway = self.spawn(
            [
                str(Path(__file__).resolve()),
                "--gateway",
                str(self.flags),
                "--port",
                port,
                "--owner-state",
                str(self.owner_dir),
                "--auth-state",
                str(self.auth_dir),
                "--cert",
                str(self.cert),
                "--key",
                str(self.key),
                "--ready-file",
                str(self.ready),
            ]
        )
        self.info = wait_file(self.ready, self.gateway)
        self.url = self.info["url"]

    def pair(self, control=True):
        from tools.am1_session_service import AuthStore

        return AuthStore(self.auth_dir).pair(control=control)

    def stop(self, name):
        process = getattr(self, name)
        if process and process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        if process:
            process.stderr.close()
        setattr(self, name, None)

    def close(self):
        self.stop("gateway")
        self.stop("owner")


if __name__ == "__main__":
    import tempfile

    if len(sys.argv) > 1 and sys.argv[1] == "--gateway":
        import asyncio

        from tools import am1_session_service
        from tools.am1_session_ipc import IPCClient

        flags_path = Path(sys.argv[2])

        class DelayedReplyIPC(IPCClient):
            async def request(self, command, device):
                result = await super().request(command, device)
                if (
                    command.get("op") == "start"
                    and flags_path.exists()
                    and json.loads(flags_path.read_text()).get("slow_start_response")
                ):
                    await asyncio.sleep(2)
                if (
                    command.get("op") == "input"
                    and flags_path.exists()
                    and json.loads(flags_path.read_text()).get("slow_input_response")
                ):
                    flags_path.with_suffix(".admitted").write_text("admitted")
                    await asyncio.sleep(0.8)
                return result

        real_close = am1_session_service.close_socket

        async def observed_close(ws, transport, code=1000):
            if code == 1013:
                flags_path.with_suffix(".backpressure").write_text("1013")
            try:
                await real_close(ws, transport, code)
            finally:
                if code == 1013:
                    flags_path.with_suffix(".closed").write_text(str(transport.is_closing()))

        am1_session_service.close_socket = observed_close
        am1_session_service.IPCClient = DelayedReplyIPC
        sys.argv = [sys.argv[0], "gateway", *sys.argv[3:]]
        am1_session_service.main()
        sys.exit(0)
    if len(sys.argv) > 1 and sys.argv[1] == "--owner":
        import asyncio

        from tools.am1_fake_executor import FakeExecutor
        from tools.am1_session_ipc import run_owner

        class PrivateProofExecutor(FakeExecutor):
            def evidence(self):
                if Path(sys.argv[3]).exists():
                    flags = json.loads(Path(sys.argv[3]).read_text())
                    for key in (
                        "feedback",
                        "required_observation",
                        "optional_quality",
                        "pose_aligned",
                        "native_ack",
                        "fault",
                    ):
                        if key in flags:
                            setattr(self, key, flags[key])
                return super().evidence()

        asyncio.run(run_owner(sys.argv[2], PrivateProofExecutor()))
        sys.exit(0)
    with tempfile.TemporaryDirectory(prefix="am1-fake-browser-") as temporary:
        cluster = Cluster(temporary)
        try:
            print(
                json.dumps(
                    {
                        "url": cluster.url,
                        "a": cluster.pair(True),
                        "b": cluster.pair(True),
                        "owner_pid": cluster.owner.pid,
                        "gateway_pid": cluster.gateway.pid,
                    }
                ),
                flush=True,
            )
            for line in sys.stdin:
                command = json.loads(line)
                if command["op"] == "restart_gateway":
                    cluster.stop("gateway")
                    time.sleep(0.4)
                    cluster.start_gateway()
                    print(
                        json.dumps(
                            {
                                "url": cluster.url,
                                "owner_pid": cluster.owner.pid,
                                "gateway_pid": cluster.gateway.pid,
                            }
                        ),
                        flush=True,
                    )
                elif command["op"] == "restart_owner":
                    cluster.stop("owner")
                    cluster.start_owner()
                    print(json.dumps({"owner_pid": cluster.owner.pid}), flush=True)
        finally:
            cluster.close()
