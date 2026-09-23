#!/usr/bin/env python3
"""Testy klienta API proti zkušebnímu HTTP serveru na loopbacku.

Server jen zaznamená požadavek a vrátí připravenou odpověď; kontrakt API
samotného ověřuje `web/tests/test_api.php`.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pardubicko_client as client  # noqa: E402

failures: list[str] = []


def check(label: str, actual, expected) -> None:
    if actual != expected:
        failures.append(f"{label}: očekáváno {expected!r}, skutečnost {actual!r}")
    else:
        print(f"OK  {label}")


class Stub:
    """Zaznamenané požadavky a fronta odpovědí (status, tělo)."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.responses: list[tuple[int, bytes]] = []

    def reply(self, status: int, body) -> None:
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.responses.append((status, raw))


stub = Stub()


class Handler(BaseHTTPRequestHandler):
    def _handle(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        stub.requests.append({
            "method": self.command,
            "path": self.path,
            "headers": {key.lower(): value for key, value in self.headers.items()},
            "body": json.loads(raw) if raw else None,
        })
        status, body = stub.responses.pop(0) if stub.responses else (200, b"{}")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PATCH = _handle

    def log_message(self, *args) -> None:
        pass


def run(*argv: str, env: dict[str, str] | None = None, stdin: str = "") -> tuple[int, object, str]:
    """Spustí CLI a vrátí (kód, JSON ze stdout nebo text, stderr)."""
    base = {"PARDUBICKO_API_URL": base_url, "PARDUBICKO_API_TOKEN": "pe_test",
            "PARDUBICKO_RUN_ID": "", "PARDUBICKO_API_TOKEN_FILE": ""}
    base.update(env or {})
    saved = {key: os.environ.get(key) for key in base}
    os.environ.update(base)
    out, err = io.StringIO(), io.StringIO()
    old_stdin = sys.stdin
    sys.stdin = io.StringIO(stdin)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = client.main(list(argv))
    finally:
        sys.stdin = old_stdin
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    text = out.getvalue()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = text.strip()
    return code, parsed, err.getvalue()


def last() -> dict:
    return stub.requests[-1]


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
base_url = f"http://127.0.0.1:{server.server_address[1]}/"

try:
    # Čtení: token v hlavičce, prázdné parametry se neposílají, bez X-Run-Id.
    stub.reply(200, {"events": [], "total": 0})
    code, body, _ = run("events", "find", "--week", "2026-W40", "--q", "jazz club", "--municipality", "")
    check("find vrací tělo API", (code, body), (0, {"events": [], "total": 0}))
    check("find skládá dotaz", last()["path"], "/api/v1/events?week=2026-W40&q=jazz+club")
    check("čtení nese Bearer token", last()["headers"].get("authorization"), "Bearer pe_test")
    check("čtení nevyžaduje běh", "x-run-id" in last()["headers"], False)

    # Zápis bez běhu se neodešle.
    count = len(stub.requests)
    code, _, err = run("events", "cancel", "evt-1", "--note", "Zrušeno pořadatelem.")
    check("zápis bez běhu končí chybou použití", code, 2)
    check("zápis bez běhu nic neodešle", len(stub.requests), count)
    check("hláška radí PARDUBICKO_RUN_ID", "PARDUBICKO_RUN_ID" in err, True)

    # Publikace: obálka event + note + candidate_id + distinct_from, běh z parametru.
    event = {"title": "Jazz v Kotelně", "start_at": "2026-10-02T19:00"}
    stub.reply(201, {"status": "published", "id": "evt-9"})
    code, body, _ = run("--run-id", "collect-1", "events", "publish", "--json", json.dumps(event),
                        "--candidate-id", "cand-3", "--distinct-from", "evt-1", "--distinct-from", "evt-2")
    check("publikace 201 je úspěch", (code, body["id"]), (0, "evt-9"))
    check("publikace posílá obálku", last()["body"],
          {"event": event, "candidate_id": "cand-3", "distinct_from": ["evt-1", "evt-2"]})
    check("publikace nese X-Run-Id", last()["headers"].get("x-run-id"), "collect-1")
    check("publikace je POST na /events", (last()["method"], last()["path"]), ("POST", "/api/v1/events"))

    # Jistá duplicita: kód 1, tělo s ID existující akce na stdout.
    stub.reply(409, {"error": "Akce už existuje.", "code": "duplicate", "event_id": "evt-1"})
    code, body, err = run("events", "publish", "--file", "-", stdin=json.dumps(event),
                          env={"PARDUBICKO_RUN_ID": "collect-1"})
    check("duplicita končí kódem 1", code, 1)
    check("duplicita vypíše tělo s ID", body.get("event_id"), "evt-1")
    check("stderr nese HTTP status", err.strip(), "HTTP 409")

    # Úprava: PATCH s changes a note, ID se v cestě kóduje.
    stub.reply(200, {"id": "evt/1"})
    run("events", "update", "evt/1", "--json", '{"title": "Nový"}', "--note", "Oprava názvu.",
        env={"PARDUBICKO_RUN_ID": "r"})
    check("úprava je PATCH s obálkou", (last()["method"], last()["path"], last()["body"]),
          ("PATCH", "/api/v1/events/evt%2F1", {"changes": {"title": "Nový"}, "note": "Oprava názvu."}))

    # Rozhodnutí o kandidátovi bez event_id ho do těla nedá.
    run("candidates", "resolve", "cand-3", "--state", "rejected", "--note", "Mimo kraj.",
        env={"PARDUBICKO_RUN_ID": "r"})
    check("resolve vynechá prázdné pole", last()["body"], {"state": "rejected", "note": "Mimo kraj."})

    run("runs", "report", "--json", '{"run": {"status": "success"}}', env={"PARDUBICKO_RUN_ID": "p-1"})
    check("runs report posílá obálku a běh", (last()["path"], last()["body"], last()["headers"].get("x-run-id")),
          ("/api/v1/runs", {"run": {"status": "success"}}, "p-1"))
    run("runs", "report", "--json", '{"status": "success"}', env={"PARDUBICKO_RUN_ID": "p-1"})
    check("runs report doplní obálku", last()["body"], {"run": {"status": "success"}})

    run("sources", "due")
    check("sources due", last()["path"], "/api/v1/sources?due=1")
    run("changes", "--run", "collect-1")
    check("changes filtruje běh", last()["path"], "/api/v1/changes?run_id=collect-1")

    # Token ze souboru.
    with tempfile.NamedTemporaryFile("w", suffix=".token", delete=False) as handle:
        handle.write("pe_ze_souboru\n")
    try:
        run("me", env={"PARDUBICKO_API_TOKEN": "", "PARDUBICKO_API_TOKEN_FILE": handle.name})
        check("token ze souboru", last()["headers"].get("authorization"), "Bearer pe_ze_souboru")
    finally:
        os.unlink(handle.name)

    code, _, err = run("me", env={"PARDUBICKO_API_TOKEN": ""})
    check("bez tokenu chyba použití", (code, "PARDUBICKO_API_TOKEN" in err), (2, True))

    # Odpověď, která není JSON (proxy, 502), je chyba spojení, ne API.
    stub.reply(502, b"<html>Bad Gateway</html>")
    code, _, err = run("me")
    check("ne-JSON odpověď končí kódem 2", (code, "není JSON" in err), (2, True))

    # Neplatný vstupní JSON se neodešle.
    count = len(stub.requests)
    code, _, _ = run("events", "publish", "--json", "[1, 2]", env={"PARDUBICKO_RUN_ID": "r"})
    check("vstup musí být objekt", (code, len(stub.requests)), (2, count))

    code, run_id, _ = run("new-run-id", "collect")
    check("new-run-id má prefix a nic neposílá",
          (code, str(run_id).startswith("collect-"), len(stub.requests)), (0, True, count))
finally:
    server.shutdown()

# Nedostupný server.
code, _, err = run("me", env={"PARDUBICKO_API_URL": "http://127.0.0.1:9"})
check("nedostupný server končí kódem 2", (code, "Spojení" in err), (2, True))

if failures:
    print(f"\nNEPROŠLO {len(failures)} kontrol:")
    for failure in failures:
        print(f" - {failure}")
    sys.exit(1)
print("\nKlient API: všechny kontroly prošly.")
