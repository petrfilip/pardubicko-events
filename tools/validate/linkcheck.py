#!/usr/bin/env python3
"""Kontrola dostupnosti zdrojových odkazů.

Oddělená od `validate.py` záměrně: síťová chyba nesmí blokovat commit.
Validace struktury běží při každém push, tahle kontrola periodicky.

Spuštění:

    python3 tools/validate/linkcheck.py
    python3 tools/validate/linkcheck.py --only-events
    docker compose run --rm linkcheck

Nástroj se identifikuje vlastním user-agentem, respektuje `robots.txt`
a mezi dotazy na tentýž host dělá pauzu. Neobchází žádná omezení.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

REPO_ROOT = Path(__file__).resolve().parents[2]
USER_AGENT = "PardubickoEventsBot/0.1 (+https://github.com/petrfilip/pardubicko-events)"
TIMEOUT = 15
HOST_DELAY = 1.0

OK = "ok"
BROKEN = "broken"
SKIPPED = "skipped"


def collect_urls(root: Path, only_events: bool) -> dict[str, list[str]]:
    """URL -> seznam míst, kde je uvedená."""
    urls: dict[str, list[str]] = defaultdict(list)

    for path in sorted((root / "data" / "weeks").glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for event in data.get("events") or []:
            url = (event.get("source") or {}).get("url")
            if url:
                urls[url].append(f"{path.name}  {event.get('id', '?')}")

    if not only_events:
        registry = root / "config" / "source-registry.json"
        if registry.is_file():
            try:
                data = json.loads(registry.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
            for source in data.get("sources") or []:
                if source.get("url"):
                    urls[source["url"]].append(f"source-registry  {source.get('id', '?')}")

    return dict(urls)


class Fetcher:
    def __init__(self) -> None:
        self._robots: dict[str, RobotFileParser | None] = {}
        self._last_request: dict[str, float] = {}

    def _wait(self, host: str) -> None:
        previous = self._last_request.get(host)
        if previous is not None:
            remaining = HOST_DELAY - (time.monotonic() - previous)
            if remaining > 0:
                time.sleep(remaining)
        self._last_request[host] = time.monotonic()

    def _allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        host = parsed.netloc
        if host not in self._robots:
            parser = RobotFileParser()
            parser.set_url(f"{parsed.scheme}://{host}/robots.txt")
            try:
                parser.read()
            except Exception:  # robots nedostupný — nebráníme kontrole
                parser = None
            self._robots[host] = parser
        parser = self._robots[host]
        if parser is None:
            return True
        return parser.can_fetch(USER_AGENT, url)

    def check(self, url: str) -> tuple[str, str]:
        if not self._allowed(url):
            return SKIPPED, "robots.txt zakazuje načtení"

        host = urlparse(url).netloc
        for method in ("HEAD", "GET"):
            self._wait(host)
            request = urllib.request.Request(
                url, method=method, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                    return OK, f"HTTP {response.status} ({method})"
            except urllib.error.HTTPError as error:
                # Řada serverů HEAD odmítá; zkusíme GET, jinak hlásíme.
                if method == "HEAD" and error.code in (403, 405, 501):
                    continue
                return BROKEN, f"HTTP {error.code} ({method})"
            except urllib.error.URLError as error:
                if method == "HEAD":
                    continue
                return BROKEN, f"nedostupné: {error.reason}"
            except Exception as error:  # timeout, špatné přesměrování a podobně
                if method == "HEAD":
                    continue
                return BROKEN, f"chyba: {error}"

        return BROKEN, "nedostupné oběma metodami"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only-events", action="store_true",
                        help="Zkontrolovat jen zdroje akcí, ne registr.")
    parser.add_argument("--json", action="store_true", help="Strojově čitelný výstup.")
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    args = parser.parse_args()

    urls = collect_urls(args.root, args.only_events)
    fetcher = Fetcher()
    results: list[dict] = []

    for url in sorted(urls):
        status, detail = fetcher.check(url)
        results.append({
            "url": url, "status": status, "detail": detail, "used_by": urls[url],
        })
        if not args.json:
            print(f"{status.upper():<8} {url}\n         {detail}")

    broken = [item for item in results if item["status"] == BROKEN]
    skipped = [item for item in results if item["status"] == SKIPPED]

    if args.json:
        print(json.dumps(
            {"checked": len(results), "broken": len(broken), "skipped": len(skipped),
             "results": results},
            ensure_ascii=False, indent=2))
    else:
        print(f"\nZkontrolováno: {len(results)}   Nefunkčních: {len(broken)}   "
              f"Přeskočeno: {len(skipped)}")
        for item in broken:
            print(f"\n  {item['url']}\n    {item['detail']}")
            for place in item["used_by"]:
                print(f"    použito v: {place}")

    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
