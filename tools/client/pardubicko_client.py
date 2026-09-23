#!/usr/bin/env python3
"""Klient HTTP API v1 pro agenty a pipeline (ADR 0008, etapa 2).

Jen standardní knihovna. Každý příkaz odpovídá jednomu endpointu a na stdout
vypíše JSON tělo odpovědi beze změny, aby ho agent mohl rovnou číst.

Konfigurace z prostředí:

    PARDUBICKO_API_URL         základ API (výchozí https://pardubicko.tix.cz)
    PARDUBICKO_API_TOKEN       token agenta, nebo
    PARDUBICKO_API_TOKEN_FILE  soubor, ve kterém token leží
    PARDUBICKO_RUN_ID          označení běhu, povinné pro zápis (nebo --run-id)

Token se parametrem předat nedá, aby nebyl vidět ve výpisu procesů.

Návratový kód: 0 úspěch (2xx), 1 API odmítlo požadavek (tělo s důvodem je
na stdout), 2 chyba použití, konfigurace nebo spojení (hláška na stderr).

Příklady:

    export PARDUBICKO_RUN_ID=$(pardubicko_client.py new-run-id collect)
    pardubicko_client.py events find --week 2026-W40 --q "jazz"
    pardubicko_client.py events publish --file akce.json --candidate-id cand-123
    pardubicko_client.py candidates resolve cand-123 --state rejected --note "Není v kraji."
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_URL = "https://pardubicko.tix.cz"
USER_AGENT = "pardubicko-client/1"


class ClientError(RuntimeError):
    """Požadavek se nepodařilo odeslat nebo přečíst (konfigurace, síť)."""


@dataclass
class ApiResponse:
    status: int
    body: Any

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class ApiClient:
    """Tenká vrstva nad API. Chybové stavy API vrací, nevyhazuje."""

    def __init__(self, base_url: str, token: str, run_id: str | None = None,
                 timeout: float = 30.0) -> None:
        if not token:
            raise ClientError("Chybí token: nastav PARDUBICKO_API_TOKEN nebo PARDUBICKO_API_TOKEN_FILE.")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.run_id = run_id
        self.timeout = timeout

    @classmethod
    def from_environment(cls, run_id: str | None = None) -> ApiClient:
        token = os.environ.get("PARDUBICKO_API_TOKEN", "").strip()
        token_file = os.environ.get("PARDUBICKO_API_TOKEN_FILE", "").strip()
        if not token and token_file:
            try:
                token = Path(token_file).read_text(encoding="utf-8").strip()
            except OSError as error:
                raise ClientError(f"Soubor s tokenem nejde přečíst: {error}") from error
        return cls(os.environ.get("PARDUBICKO_API_URL", "").strip() or DEFAULT_URL, token,
                   run_id or os.environ.get("PARDUBICKO_RUN_ID", "").strip() or None)

    def get(self, path: str, query: dict[str, Any] | None = None) -> ApiResponse:
        return self.request("GET", path, query=query)

    def post(self, path: str, body: dict[str, Any]) -> ApiResponse:
        return self.request("POST", path, body=body)

    def patch(self, path: str, body: dict[str, Any]) -> ApiResponse:
        return self.request("PATCH", path, body=body)

    def request(self, method: str, path: str, *, query: dict[str, Any] | None = None,
                body: dict[str, Any] | None = None) -> ApiResponse:
        url = self.base_url + "/api/v1" + path
        params = [(key, str(value)) for key, value in (query or {}).items()
                  if value is not None and value != ""]
        if params:
            url += "?" + urllib.parse.urlencode(params)

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        data = None
        if method != "GET":
            if not self.run_id:
                raise ClientError("Zápis vyžaduje označení běhu: nastav PARDUBICKO_RUN_ID nebo --run-id.")
            headers["X-Run-Id"] = self.run_id
            headers["Content-Type"] = "application/json; charset=utf-8"
            data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8")

        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return ApiResponse(response.status, _decode(response.read(), response.status))
        except urllib.error.HTTPError as error:
            return ApiResponse(error.code, _decode(error.read(), error.code))
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            reason = getattr(error, "reason", error)
            raise ClientError(f"Spojení s {self.base_url} selhalo: {reason}") from error


def _decode(raw: bytes, status: int) -> Any:
    try:
        return json.loads(raw.decode("utf-8")) if raw.strip() else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        # Neplatný JSON znamená chybu serveru nebo proxy, ne odpověď API.
        preview = raw[:200].decode("utf-8", "replace")
        raise ClientError(f"Odpověď HTTP {status} není JSON: {preview}") from error


def new_run_id(prefix: str, now: datetime | None = None) -> str:
    moment = (now or datetime.now()).strftime("%Y%m%dT%H%M%S")
    return f"{prefix}-{moment}-{secrets.token_hex(2)}"


# --- CLI -------------------------------------------------------------------

def _load_json(args: argparse.Namespace) -> dict[str, Any]:
    if args.json is not None:
        raw = args.json
    elif args.file == "-":
        raw = sys.stdin.read()
    else:
        try:
            raw = Path(args.file).read_text(encoding="utf-8")
        except OSError as error:
            raise ClientError(f"Soubor {args.file} nejde přečíst: {error}") from error
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ClientError(f"Vstup není platný JSON: {error}") from error
    if not isinstance(data, dict):
        raise ClientError("Vstup musí být JSON objekt.")
    return data


def _id(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _envelope(**fields: Any) -> dict[str, Any]:
    return {key: value for key, value in fields.items() if value is not None and value != []}


def _dispatch(client: ApiClient, args: argparse.Namespace) -> ApiResponse:
    group, action = args.group, getattr(args, "action", None)

    if group == "me":
        return client.get("/me")
    if group == "taxonomy":
        return client.get("/taxonomy")
    if group == "changes":
        return client.get("/changes", {"run_id": args.run, "actor": args.actor, "entity": args.entity,
                                       "entity_id": args.entity_id, "since_id": args.since_id,
                                       "limit": args.limit})
    if group == "inbox":
        return client.post("/inbox", _envelope(url=args.url, note=args.note))

    if group == "events":
        if action == "find":
            return client.get("/events", {"week": args.week, "from": args.date_from, "to": args.date_to,
                                          "municipality": args.municipality, "category": args.category,
                                          "q": args.q, "status": args.status,
                                          "changed_since": args.changed_since,
                                          "limit": args.limit, "offset": args.offset})
        if action == "get":
            return client.get(f"/events/{_id(args.id)}")
        if action == "history":
            return client.get(f"/events/{_id(args.id)}/history")
        if action == "publish":
            return client.post("/events", _envelope(event=_load_json(args), note=args.note,
                                                    candidate_id=args.candidate_id,
                                                    distinct_from=args.distinct_from))
        if action == "update":
            return client.patch(f"/events/{_id(args.id)}", {"changes": _load_json(args), "note": args.note})
        if action in ("cancel", "withdraw"):
            return client.post(f"/events/{_id(args.id)}/{action}", {"note": args.note})
        if action == "add-source":
            return client.post(f"/events/{_id(args.id)}/sources",
                               _envelope(url=args.url, source_id=args.source_id, note=args.note))

    if group == "candidates":
        if action == "list":
            return client.get("/candidates", {"state": args.state, "week": args.week,
                                              "source_id": args.source_id,
                                              "limit": args.limit, "offset": args.offset})
        if action == "get":
            return client.get(f"/candidates/{_id(args.id)}")
        if action == "submit":
            return client.post("/candidates", {"candidate": _load_json(args)})
        if action == "resolve":
            return client.post(f"/candidates/{_id(args.id)}/resolve",
                               _envelope(state=args.state, event_id=args.event_id, note=args.note))

    if group == "reviews":
        if action == "list":
            return client.get("/match-reviews")
        if action == "decide":
            return client.post(f"/match-reviews/{args.id}/decide",
                               {"decision": args.decision, "note": args.note})

    if group == "sources":
        if action == "list":
            return client.get("/sources", {"due": "1" if args.due else None})
        if action == "due":
            return client.get("/sources", {"due": "1"})
        if action == "get":
            return client.get(f"/sources/{_id(args.id)}")
        if action == "update":
            return client.patch(f"/sources/{_id(args.id)}", {"changes": _load_json(args), "note": args.note})

    raise ClientError(f"Neznámý příkaz {group} {action or ''}".strip())


def _json_input(parser: argparse.ArgumentParser, what: str) -> None:
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", help=f"{what} jako JSON soubor, '-' čte stdin")
    source.add_argument("--json", help=f"{what} jako JSON text")


def _paging(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--limit", type=int)
    parser.add_argument("--offset", type=int)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pardubicko_client.py",
        description="Klient API pardubicko.tix.cz. Vypisuje JSON odpovědi.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Příklady:", 1)[1] if __doc__ else None,
    )
    parser.add_argument("--run-id", help="označení běhu pro zápis (jinak PARDUBICKO_RUN_ID)")
    groups = parser.add_subparsers(dest="group", required=True)

    new_run = groups.add_parser("new-run-id", help="vygeneruje označení běhu, nic neposílá")
    new_run.add_argument("prefix", nargs="?", default="run")

    groups.add_parser("me", help="token, denní limit a dnešní publikace")
    groups.add_parser("taxonomy", help="kategorie, obce a aliasy")

    changes = groups.add_parser("changes", help="historie změn")
    changes.add_argument("--run", help="jen změny jednoho běhu")
    changes.add_argument("--actor")
    changes.add_argument("--entity", choices=["event", "candidate", "source", "match_review"])
    changes.add_argument("--entity-id")
    changes.add_argument("--since-id", type=int)
    changes.add_argument("--limit", type=int)

    inbox = groups.add_parser("inbox", help="ručně vložený odkaz")
    inbox.add_argument("url")
    inbox.add_argument("--note")

    events = groups.add_parser("events", help="publikované akce").add_subparsers(dest="action", required=True)
    find = events.add_parser("find", help="výpis a kontrola duplicit před zápisem")
    find.add_argument("--week", help="ISO týden, např. 2026-W40")
    find.add_argument("--from", dest="date_from", help="od data YYYY-MM-DD")
    find.add_argument("--to", dest="date_to", help="do data YYYY-MM-DD")
    find.add_argument("--municipality", help="obec (název nebo slug)")
    find.add_argument("--category")
    find.add_argument("--q", help="fulltext")
    find.add_argument("--status")
    find.add_argument("--changed-since")
    _paging(find)
    for name, text in (("get", "detail akce"), ("history", "historie změn akce")):
        events.add_parser(name, help=text).add_argument("id")
    publish = events.add_parser("publish", help="publikace ověřené akce")
    _json_input(publish, "akce")
    publish.add_argument("--note")
    publish.add_argument("--candidate-id", help="kandidát, ze kterého akce vzniká")
    publish.add_argument("--distinct-from", action="append", default=[], metavar="EVENT_ID",
                         help="akce, která je podobná, ale jiná (opakovatelné)")
    update = events.add_parser("update", help="úprava akce")
    update.add_argument("id")
    _json_input(update, "změněná pole")
    update.add_argument("--note", required=True)
    for name, text in (("cancel", "zrušení akce"), ("withdraw", "stažení z webu")):
        command = events.add_parser(name, help=text)
        command.add_argument("id")
        command.add_argument("--note", required=True)
    add_source = events.add_parser("add-source", help="další zdroj akce")
    add_source.add_argument("id")
    add_source.add_argument("--url", required=True)
    add_source.add_argument("--source-id")
    add_source.add_argument("--note")

    candidates = groups.add_parser("candidates", help="fronta kandidátů").add_subparsers(
        dest="action", required=True)
    listing = candidates.add_parser("list", help="otevření kandidáti, nebo podle --state")
    listing.add_argument("--state", help="open (výchozí) nebo stavy oddělené čárkou")
    listing.add_argument("--week")
    listing.add_argument("--source-id")
    _paging(listing)
    candidates.add_parser("get", help="kandidát a jeho shody").add_argument("id")
    submit = candidates.add_parser("submit", help="nový kandidát s deduplikací")
    _json_input(submit, "kandidát")
    resolve = candidates.add_parser("resolve", help="rozhodnutí o kandidátovi")
    resolve.add_argument("id")
    resolve.add_argument("--state", required=True)
    resolve.add_argument("--event-id")
    resolve.add_argument("--note", required=True)

    reviews = groups.add_parser("reviews", help="nejisté shody").add_subparsers(dest="action", required=True)
    reviews.add_parser("list")
    decide = reviews.add_parser("decide")
    decide.add_argument("id", type=int)
    decide.add_argument("--decision", required=True, choices=["merged", "separate"])
    decide.add_argument("--note", required=True)

    sources = groups.add_parser("sources", help="registr zdrojů").add_subparsers(dest="action", required=True)
    sources.add_parser("list").add_argument("--due", action="store_true")
    sources.add_parser("due", help="zdroje splatné ke kontrole")
    sources.add_parser("get").add_argument("id")
    source_update = sources.add_parser("update", help="úprava zdroje")
    source_update.add_argument("id")
    _json_input(source_update, "změněná pole")
    source_update.add_argument("--note", required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.group == "new-run-id":
        print(new_run_id(args.prefix))
        return 0
    try:
        response = _dispatch(ApiClient.from_environment(args.run_id), args)
    except ClientError as error:
        print(f"CHYBA: {error}", file=sys.stderr)
        return 2
    print(json.dumps(response.body, ensure_ascii=False, indent=2))
    if not response.ok:
        print(f"HTTP {response.status}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
