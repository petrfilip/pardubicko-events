#!/usr/bin/env python3
"""Spustí PHP HTTP smoke nad čerstvou dočasnou SQLite databází."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def wait_for_server(url: str, server: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise RuntimeError(f"PHP server skončil s kódem {server.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(0.1)
    raise RuntimeError("PHP server se do 10 sekund nespustil")


def main() -> int:
    php = shutil.which("php")
    if php is None:
        print("HTTP smoke vyžaduje PHP runtime.", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="pardubicko-http-smoke-") as temp_name:
        database = Path(temp_name) / "smoke.db"
        imported = subprocess.run(
            [sys.executable, "tools/pipeline/pipeline.py", "--database", str(database),
             "import"],
            cwd=REPO_ROOT,
            check=False,
        )
        if imported.returncode != 0:
            return imported.returncode

        port = available_port()
        base_url = f"http://127.0.0.1:{port}"
        environment = os.environ.copy()
        environment.update({
            "PARDUBICKO_DB": str(database),
            "PARDUBICKO_INBOX_TOKEN": "smoke-token",
            "PARDUBICKO_BASE_URL": base_url,
        })

        with tempfile.TemporaryFile() as server_log:
            server = subprocess.Popen(
                [php, "-S", f"127.0.0.1:{port}", "-t", "web/public",
                 "web/public/router.php"],
                cwd=REPO_ROOT,
                env=environment,
                stdout=server_log,
                stderr=subprocess.STDOUT,
            )
            try:
                wait_for_server(base_url + "/api/health", server)
                smoke = subprocess.run(
                    [php, "web/tests/http_smoke.php", base_url],
                    cwd=REPO_ROOT,
                    env=environment,
                    check=False,
                )
                return smoke.returncode
            except RuntimeError as error:
                print(f"HTTP smoke nelze spustit: {error}", file=sys.stderr)
                server_log.seek(0)
                sys.stderr.write(server_log.read().decode("utf-8", errors="replace"))
                return 1
            finally:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
