#!/usr/bin/env python3
"""Pipeline proti skutečnému PHP API nad dočasnou databází.

`tools/pipeline/run.py` běží jako samostatný proces s tokenem z
`web/bin/pardubicko` a stahuje fixture z lokálního serveru. Ověřuje, že
kandidáti, report běhu, stažení i zdraví zdroje projdou kontraktem API,
a to i s odpovědí 304 při druhém běhu.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools" / "client"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pardubicko_client import ApiClient  # noqa: E402
from run_http_smoke import available_port, wait_for_server  # noqa: E402

FIXTURE = REPO_ROOT / "tools" / "pipeline" / "fixtures" / "pardubice-calendar.html"
ETAG = '"fixture-1"'
failures: list[str] = []


def check(label: str, actual, expected) -> None:
    if actual != expected:
        failures.append(f"{label}: očekáváno {expected!r}, skutečnost {actual!r}")
    else:
        print(f"OK  {label}")


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 — rozhraní http.server
        if self.headers.get("If-None-Match") == ETAG:
            self.send_response(304)
            self.send_header("ETag", ETAG)
            self.end_headers()
            return
        body = FIXTURE.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", ETAG)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


def run_pipeline(environment: dict[str, str], temp: Path, run_id: str) -> tuple[int, dict]:
    completed = subprocess.run(
        [sys.executable, "tools/pipeline/run.py", "--source", "pardubice-calendar",
         "--cache", str(temp / "cache.db"), "--snapshot-dir", str(temp / "snapshots"),
         "--report-dir", str(temp / "runs")],
        cwd=REPO_ROOT, env={**environment, "PARDUBICKO_RUN_ID": run_id},
        capture_output=True, text=True, check=False,
    )
    if completed.returncode not in (0, 1):
        print(completed.stderr, file=sys.stderr)
    try:
        return completed.returncode, json.loads(completed.stdout)
    except json.JSONDecodeError:
        print(completed.stdout, completed.stderr, file=sys.stderr)
        return completed.returncode, {}


def main() -> int:
    php = shutil.which("php")
    if php is None:
        print("E2E pipeline vyžaduje PHP runtime.", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="pardubicko-pipeline-e2e-") as name:
        temp = Path(name)
        database = temp / "server.db"
        subprocess.run([sys.executable, "tools/pipeline/pipeline.py", "--database", str(database), "import"],
                       cwd=REPO_ROOT, check=True, stdout=subprocess.DEVNULL)
        server_env = {**os.environ, "PARDUBICKO_DB": str(database)}
        subprocess.run([php, "web/bin/pardubicko", "migrate"], cwd=REPO_ROOT, env=server_env,
                       check=True, stdout=subprocess.DEVNULL)
        created = subprocess.run([php, "web/bin/pardubicko", "token:create", "pipeline-e2e", "0"],
                                 cwd=REPO_ROOT, env=server_env, check=True, capture_output=True, text=True)
        token = created.stdout.strip().splitlines()[-1]

        fixtures = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        threading.Thread(target=fixtures.serve_forever, daemon=True).start()
        fixture_url = f"http://127.0.0.1:{fixtures.server_address[1]}/kalendar-akci"

        port = available_port()
        base_url = f"http://127.0.0.1:{port}"
        with tempfile.TemporaryFile() as server_log:
            server = subprocess.Popen(
                [php, "-d", f"open_basedir={REPO_ROOT / 'web'}:{temp}:/tmp",
                 "-S", f"127.0.0.1:{port}", "-t", "web/public", "web/public/router.php"],
                cwd=REPO_ROOT, env={**server_env, "PARDUBICKO_BASE_URL": base_url},
                stdout=server_log, stderr=subprocess.STDOUT,
            )
            try:
                wait_for_server(base_url + "/api/health", server)
                client = ApiClient(base_url, token, "e2e-setup")
                patched = client.patch("/sources/pardubice-calendar", {
                    "changes": {"url": fixture_url}, "note": "E2E: fixture místo webu."})
                check("zdroj přesměrovaný na fixture", patched.status, 200)

                environment = {**os.environ, "PARDUBICKO_API_URL": base_url, "PARDUBICKO_API_TOKEN": token,
                               "PARDUBICKO_API_TOKEN_FILE": ""}
                code, report = run_pipeline(environment, temp, "pipeline-e2e-1")
                check("první běh doběhne", (code, report.get("status")), (0, "success"))
                check("server přijal report", (report.get("server") or {}).get("fetches"), 1)
                check("zdraví spočítal server",
                      [item["state"] for item in (report.get("server") or {}).get("health", [])], ["healthy"])
                check("lokální kopie reportu existuje", Path(report.get("report_path") or "").is_file(), True)

                run = client.get("/runs/pipeline-e2e-1").body
                check("běh je na serveru", (run.get("status"), run.get("actor")), ("success", "pipeline-e2e"))
                check("výsledek zdroje", (run["sources"][0]["items_found"], run["sources"][0]["candidates_created"]),
                      (3, 1))
                # Jedna akce z fixture je už publikovaná (zmrazená data W32): server
                # kandidáta připojil jako zdroj a uzavřel, nový vznikl jen jeden.
                check("jistá shoda se připojila", (report["metrics"]["matched_existing"],
                      report["metrics"]["candidates_existing"]), (1, 0))
                candidates = client.get("/candidates", {"source_id": "pardubice-calendar"}).body
                check("otevřený kandidát je v API", candidates.get("total"), 1)
                source = client.get("/sources/pardubice-calendar").body
                check("zdroj už není splatný", (source.get("due"), source["health"]["state"]), (False, "healthy"))

                code, report = run_pipeline(environment, temp, "pipeline-e2e-2")
                check("druhý běh s 304 doběhne", (code, report.get("status")), (0, "no-change"))
                check("druhý běh zná kandidáty", report["metrics"]["candidates_existing"], 2)
                check("stažení 304 prošlo kontraktem", (report.get("server") or {}).get("fetches"), 1)

                code, _ = run_pipeline(environment, temp, "pipeline-e2e-1")
                check("opakované run_id je úspěch, ne chyba", code, 0)
            except (RuntimeError, KeyError, IndexError) as error:
                failures.append(f"E2E se nepodařilo dokončit: {error!r}")
                server_log.seek(0)
                sys.stderr.write(server_log.read().decode("utf-8", errors="replace"))
            finally:
                server.terminate()
                server.wait(timeout=5)
                fixtures.shutdown()

    if failures:
        print(f"\nNEPROŠLO {len(failures)} kontrol:")
        for failure in failures:
            print(f" - {failure}")
        return 1
    print("\nPipeline proti PHP API prošla.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
