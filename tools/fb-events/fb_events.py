#!/usr/bin/env python3
"""Deterministický sběr veřejných událostí z Facebooku pro Pardubicko Events.

Nástroj pouze načítá a strukturuje. Nic nevyhodnocuje, nic nedomýšlí a nikdy
nezapisuje do `data/weeks/`. Výstupem jsou kandidáti pro Curator Agenta.

Spuštění:

    python3 tools/fb-events/fb_events.py                 # plný běh
    python3 tools/fb-events/fb_events.py --limit-pages 3 # zkouška
    python3 tools/fb-events/fb_events.py --dry-run       # nic nezapíše

Pravidla provozu, která jsou v kódu záměrně natvrdo:

* nepřihlašuje se a neobchází žádnou ochranu platformy,
* představuje se vlastním user-agentem, nepodvrhává prohlížeč,
* stránky načítá sekvenčně s pauzou (`--delay`),
* co nedokáže přečíst, uloží jako `null`; nikdy nedoplňuje odhad.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import unicodedata
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from parse import (  # noqa: E402
    TZ,
    ParseError,
    clean_organizer,
    is_cohost_summary,
    parse_datetime_line,
    parse_listing_block,
    strip_cohost_summary,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
USER_AGENT = "PardubickoEventsBot/0.1 (+https://github.com/petrfilip/pardubicko-events)"
PRIORITY_HORIZON_DAYS = 14

EVENT_ID_RE = re.compile(r"/events/(\d{6,})")
# Adresu poznáme podle PSČ ve tvaru "530 02" následovaného názvem obce. Řádek musí
# obsahovat i čárku, jinak by prošlo i "Vstupné 10000 Kč v předprodeji".
ADDRESS_RE = re.compile(r"\d{3}\s\d{2}\s+[^\d,]{2,}")

# Vytáhne z výpisu událostí bloky textu patřící jednotlivým akcím. Nespoléhá na
# konkrétní CSS třídy — ty Facebook mění — ale na odkaz na událost a nejbližšího
# předka, který už obsahuje celý blok informací.
LISTING_JS = """
() => {
  const byId = new Map();
  document.querySelectorAll('a[href*="/events/"]').forEach(a => {
    const m = a.href.match(/\\/events\\/(\\d{6,})/);
    if (!m) return;
    const id = m[1];
    if (!byId.has(id)) {
      let el = a, block = null;
      for (let i = 0; i < 8 && el; i++) {
        const lines = (el.innerText || '').trim().split('\\n').filter(s => s.trim());
        if (lines.length >= 3) { block = lines; break; }
        el = el.parentElement;
      }
      if (!block) return;
      byId.set(id, { id, url: 'https://www.facebook.com/events/' + id + '/',
                     lines: block, time_ids: [] });
    }
    // Opakovaná akce má jedno ID a mnoho termínů odlišených event_time_id.
    const t = a.href.match(/[?&]event_time_id=(\\d+)/);
    if (t && !byId.get(id).time_ids.includes(t[1])) byId.get(id).time_ids.push(t[1]);
  });
  return [...byId.values()];
}
"""

DETAIL_JS = """
() => {
  const ids = new Set();
  document.querySelectorAll('a[href*="/events/"]').forEach(a => {
    const m = a.href.match(/\\/events\\/(\\d{6,})/);
    if (m) ids.add(m[1]);
  });
  const og = {};
  document.querySelectorAll('meta[property^="og:"]').forEach(m => {
    og[m.getAttribute('property')] = m.getAttribute('content');
  });
  return {
    lines: (document.body.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean),
    event_ids: [...ids],
    og,
  };
}
"""


def log(msg: str) -> None:
    print(msg, flush=True)


def git_sha() -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() or None
    except Exception:
        return None


def load_sources(config_path: Path, only: list[str] | None) -> list[dict]:
    data = json.loads(config_path.read_text(encoding="utf-8"))
    sources = [s for s in data.get("pages", []) if s.get("enabled", True)]
    if only:
        wanted = set(only)
        sources = [s for s in sources if s.get("source_id") in wanted
                   or s.get("facebook_page") in wanted]
    return sources


def known_event_ids(repo: Path) -> set[str]:
    """ID akcí, které už jsou v backlogu kandidátů nebo ve finálních datech."""
    ids: set[str] = set()
    for path in sorted((repo / "research").glob("candidates*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for candidate in payload.get("candidates", []):
            fb_id = candidate.get("facebook_event_id")
            if fb_id:
                ids.add(str(fb_id))
            match = EVENT_ID_RE.search(candidate.get("source_url") or "")
            if match:
                ids.add(match.group(1))
    for path in sorted((repo / "data" / "weeks").glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for event in payload.get("events", []):
            for value in (event.get("source_url"), event.get("url"), event.get("source")):
                match = EVENT_ID_RE.search(value or "") if isinstance(value, str) else None
                if match:
                    ids.add(match.group(1))
    return ids


def scope_for(start_at: str | None, now: datetime) -> str:
    if not start_at:
        return "opportunistic-future"
    try:
        start = datetime.fromisoformat(start_at)
    except ValueError:
        return "opportunistic-future"
    delta = (start - now).days
    if delta < 0:
        return "opportunistic-future"  # akce v minulosti nepatří do prioritního okna
    return "priority-14-days" if delta <= PRIORITY_HORIZON_DAYS else "opportunistic-future"


def find_address(lines: list[str]) -> str | None:
    for line in lines:
        if "," in line and ADDRESS_RE.search(line) and len(line) < 160:
            return line
    return None


def find_detail_datetime(lines: list[str], now: datetime):
    """Na detailu je řádek s datem první, který se podaří naparsovat."""
    for line in lines[:60]:
        if not re.search(r"\d", line):
            continue
        try:
            parsed = parse_datetime_line(line, today=now.date())
        except ParseError:
            continue
        # Samotné "30." nebo číslo bez měsíce parser odmítne; tady chceme jistotu,
        # že řádek nese i měsíc, ne jen den z odpočtu nad obrázkem.
        if re.search(r"\d{1,2}\.\s*(\d{1,2}\.|[a-záčďéěíňóřšťúůýž]{3,})", line):
            return parsed
    return None


def find_organizers(lines: list[str]) -> list[str]:
    """Jména pořadatelů z detailu akce.

    Facebook je uvádí dvakrát: jednou souhrnně ("Událost pořádá X a Y spolu se
    2 dalšími") a jednou vyjmenovaně v sekci "Pořadatelé". Vyjmenovaný seznam má
    přednost; souhrnná věta se použije jen tehdy, když sekce chybí.
    """
    summary: list[str] = []
    explicit: list[str] = []
    for index, line in enumerate(lines):
        name = clean_organizer(line)
        if name:
            summary.append(name)
        if line.strip() == "Pořadatelé":
            for follow in lines[index + 1:index + 21]:
                if follow in STOP_LINES:
                    break
                if follow and len(follow) < 80:
                    explicit.append(follow)
            break

    if explicit:
        chosen = explicit
    else:
        chosen = [strip_cohost_summary(n) for n in summary]

    seen: set[str] = set()
    unique = []
    for name in chosen:
        if name and not is_cohost_summary(name) and name not in seen:
            seen.add(name)
            unique.append(name)
    return unique


# Řádky, na kterých končí výčet pořadatelů. Bez nich by se do jmen dostaly
# ovládací prvky stránky ("Zobrazit víc", "Sdílet", "Zajímá mě").
STOP_LINES = frozenset({
    "Navrhované události", "Podrobnosti", "Transparentnost události", "Zobrazit víc",
    "Zajímá mě", "Sdílet", "Pozvat", "Koupit vstupenky", "Diskuse", "Diskuze",
    "Zúčastním se", "Hosté", "Další", "Informace", "Místo konání",
})

EMPTY_MARKERS = (
    "Žádný obsah (události) k zobrazení",
    "Žádná kolekce Nadcházející k zobrazení",
)


async def fetch_listing(page, source: dict, args, now: datetime) -> tuple[list[dict], str | None]:
    slug = source["facebook_page"]
    url = f"https://www.facebook.com/{slug}/upcoming_hosted_events"
    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    await page.wait_for_timeout(int(args.render_wait * 1000))

    if "/login" in page.url or "checkpoint" in page.url:
        return [], "redirect na login"
    body = await page.inner_text("body")
    if "Obsah teď není dostupný" in body:
        return [], "obsah není dostupný"

    # Facebook vypíše bez scrollování jen prvních 8 akcí. Bez dorolování by se
    # výpis tiše ořezal a stránka s dvaceti akcemi by vypadala jako stránka s osmi.
    raw_blocks = await page.evaluate(LISTING_JS)
    previous = None
    stable_rounds = 0
    scrolls_used = 0
    for _ in range(max(0, args.max_scrolls)):
        if len(raw_blocks) == previous:
            # Lazy load občas nestihne dodat nic za jedno kolo. Jedno prázdné kolo
            # proto konec neznamená — teprve dvě po sobě.
            stable_rounds += 1
            if stable_rounds >= 2:
                break
        else:
            stable_rounds = 0
        previous = len(raw_blocks)
        scrolls_used += 1
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(int(args.scroll_wait * 1000))
        raw_blocks = await page.evaluate(LISTING_JS)
    # Nedorolováno = došly pokusy, zatímco poslední kolo ještě přidávalo.
    truncated = scrolls_used >= max(0, args.max_scrolls) and len(raw_blocks) != previous
    # Kontrola prázdné stránky patří PŘED parsování. Kdyby byla za ním, zahodila by
    # už naparsované akce, kdyby se hláška na stránce objevila spolu s nimi.
    if not raw_blocks and any(marker in body for marker in EMPTY_MARKERS):
        return [], None  # stránka existuje, jen nemá nadcházející akce

    parsed: list[dict] = []
    for block in raw_blocks:
        try:
            fields = parse_listing_block(block["lines"], today=now.date())
        except ParseError:
            continue  # navigační odkaz nebo blok, který nenese datum
        parsed.append({**fields, "facebook_event_id": block["id"],
                       "source_url": block["url"], "time_ids": block.get("time_ids", []),
                       "listing_truncated": truncated, "blocks_seen": len(raw_blocks)})
    if not raw_blocks:
        # Prázdno bez příslušné hlášky znamená spíš nedorenderovanou stránku než
        # stránku bez akcí. Nesmí projít jako „nemá akce“.
        return [], "nenalezeny žádné bloky událostí"
    if not parsed:
        # Bloky na stránce jsou, ale ani jeden nenese čitelné datum. To je typický
        # projev změny UI Facebooku a nesmí projít jako „stránka nemá akce“.
        return [], "bloky nalezeny, ale nešly naparsovat"
    return parsed, None


async def fetch_detail(page, event_id: str, wait: float, now: datetime) -> dict:
    url = f"https://www.facebook.com/events/{event_id}/"
    detail = {"address": None, "start_at": None, "end_at": None, "all_day": None,
              "organizers": [], "public": None, "suggested_event_ids": [],
              "og_title": None, "detail_ok": False}
    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    await page.wait_for_timeout(int(wait * 1000))
    if "/login" in page.url or "checkpoint" in page.url:
        return detail
    data = await page.evaluate(DETAIL_JS)
    lines = data["lines"]
    cut = next((i for i, l in enumerate(lines) if l.startswith("Zobrazit víc na Facebooku")), len(lines))
    lines = lines[:cut]

    when = find_detail_datetime(lines, now)
    if when:
        detail["start_at"] = when["start_at"]
        detail["end_at"] = when["end_at"]
        detail["all_day"] = when["all_day"]
    detail["address"] = find_address(lines)
    detail["organizers"] = find_organizers(lines)
    detail["public"] = "Veřejná" in lines
    detail["og_title"] = data["og"].get("og:title")
    detail["suggested_event_ids"] = [i for i in data["event_ids"] if i != event_id]
    detail["detail_ok"] = True
    return detail


def _same_text(a: str | None, b: str | None) -> bool:
    """Porovnání názvů bez emoji, diakritiky a přebytečných mezer."""
    def norm(s):
        s = unicodedata.normalize("NFKD", (s or "").lower())
        s = "".join(c for c in s if not unicodedata.combining(c))
        return re.sub(r"[^a-z0-9]+", " ", s).strip()
    return norm(a) == norm(b)


def build_candidate(row: dict, source: dict, detail: dict | None, now: datetime) -> dict:
    # Právě běžící akce nemá ve výpisu datum; bere se z detailu. Když ani ten
    # datum nedá, zůstane null — dopočítávat "od dneška" by byl výmysl.
    start_at = row.get("start_at")
    end_at = row.get("end_at")
    all_day = row.get("all_day")
    if start_at is None and detail and detail.get("start_at"):
        start_at = detail["start_at"]
        end_at = detail.get("end_at") or end_at
        all_day = detail.get("all_day")
    elif detail and detail.get("end_at"):
        # Konec z detailu se smí přilepit k začátku ze seznamu jen tehdy, když
        # dvojice dává smysl. Jinak jsou to údaje ze dvou různých akcí.
        if start_at and detail["end_at"] > start_at:
            end_at = detail["end_at"]
    organizers = list(row.get("organizers") or [])  # kopie, ať se nemutuje vstup
    if detail:
        for name in detail.get("organizers", []):
            if name not in organizers:
                organizers.append(name)
    notes = []
    if not row.get("venue"):
        notes.append("Facebook neuvedl místo konání.")
    if row.get("municipality"):
        notes.append(f"Geoznačka Facebooku uvádí obec „{row['municipality']}“ — ověřit.")
    else:
        notes.append("Facebook neuvedl obec.")
    notes.append("Vstupné Facebook nenabízí strukturovaně, ověřit u pořadatele.")

    if row.get("ongoing"):
        if start_at:
            notes.append("Výpis uváděl jen „Právě probíhá“; termín doplněn z detailu akce.")
        else:
            notes.append("Výpis uváděl jen „Právě probíhá“ a detail termín nedal — "
                         "akce právě běží, přesné datum zjistit u pořadatele.")

    # og:title z detailu je nezávislý zdroj názvu. Když se rozchází s názvem
    # ze seznamu, je to podezření, že se k odkazu přiřadil špatný blok textu.
    og_title = (detail or {}).get("og_title")
    if og_title and not _same_text(og_title, row["title"]):
        notes.append(f"Název v detailu se liší od názvu v seznamu: „{og_title}“ — ověřit.")

    if detail and detail.get("detail_ok") and not detail.get("public"):
        notes.append("Detail neuvádí označení „Veřejná“ — ověřit, že jde o veřejnou akci.")

    # Opakovaná akce: jedno ID, mnoho termínů. Rozdělit na jednotlivé akce vyžaduje
    # úsudek (opakovaná prohlídka není totéž co festivalový program), proto to kód
    # nedělá a jen to označí pro Curatora.
    time_ids = row.get("time_ids") or []
    # Facebook uvádí celkový počet termínů v textu ("a 23 dalších"), zatímco odkazů
    # s event_time_id vydá jen část. Autoritativní je počet ze zdroje, seznam ID je
    # dolní odhad — proto se ukládají obě čísla.
    total_dates = row.get("total_dates")
    recurring = bool(total_dates and total_dates > 1) or len(time_ids) > 1
    if recurring:
        if total_dates and total_dates > len(time_ids):
            notes.append(f"Opakovaná akce: zdroj uvádí {total_dates} termínů, zachyceno "
                         f"{len(time_ids)} identifikátorů — zbytek dohledat u pořadatele. "
                         "Rozpad na jednotlivé termíny posoudit ručně.")
        else:
            notes.append(f"Opakovaná akce s {len(time_ids)} termíny (event_time_id) — "
                         "rozpad na jednotlivé termíny posoudit ručně.")

    return {
        "id": f"fb-{row['facebook_event_id']}",
        "title": row["title"],
        "date_text": row["date_text"],
        "start_at": start_at,
        "end_at": end_at,
        "all_day": all_day,
        "ongoing": bool(row.get("ongoing")),
        "municipality": row.get("municipality"),
        "venue": row.get("venue"),
        "address": (detail or {}).get("address"),
        "discovered_at": now.isoformat(timespec="seconds"),
        "discovery_method": "facebook",
        "discovery_scope": "priority-14-days" if row.get("ongoing") else scope_for(start_at, now),
        "source_url": row["source_url"],
        "facebook_event_id": row["facebook_event_id"],
        "facebook_page": source["facebook_page"],
        "facebook_public": (detail or {}).get("public"),
        "source_id": source.get("source_id"),
        "region": source.get("region"),
        "district": source.get("district"),
        "organizers": organizers,
        "price_text": None,
        "requires_primary_source": True,
        "candidate_kind": "programme" if recurring else "single-event",
        "programme_id": None,
        "programme_title": None,
        "expandable": recurring,
        "facebook_time_ids": time_ids,
        "facebook_total_dates": total_dates,
        "status": "needs-verification",
        "notes": " ".join(notes),
    }


async def run(args) -> int:
    from playwright.async_api import async_playwright

    now = datetime.now(TZ)
    started_at = now
    config_path = Path(args.config) if Path(args.config).is_absolute() else REPO_ROOT / args.config
    sources = load_sources(config_path, args.pages.split(",") if args.pages else None)
    if args.limit_pages:
        sources = sources[:args.limit_pages]
    if not sources:
        log("Žádné zdroje ke zpracování.")
        return 1

    already_known = set() if args.ignore_known else known_event_ids(REPO_ROOT)
    log(f"Zdrojů ke kontrole: {len(sources)}; známých FB akcí v repozitáři: {len(already_known)}")

    metrics = dict(facebook_pages_checked=0, facebook_events_extracted=0,
                   facebook_details_fetched=0, facebook_blocked=0,
                   facebook_duplicates=0, facebook_suggested_events_seen=0,
                   facebook_blocks_unparsed=0, facebook_details_failed=0)
    errors: list[dict] = []
    notes: list[str] = []
    candidates: list[dict] = []
    checked_sources: list[dict] = []
    truncated_pages: list[str] = []
    unparsed_pages: list[str] = []
    suggested: set[str] = set()
    seen_ids: set[str] = set()
    by_title_time: dict[tuple, dict] = {}

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(
            locale="cs-CZ", timezone_id="Europe/Prague",
            viewport={"width": 1400, "height": 1400}, user_agent=USER_AGENT,
        )
        page = await context.new_page()
        try:
            for source in sources:
                slug = source["facebook_page"]
                try:
                    rows, blocked = await fetch_listing(page, source, args, now)
                except Exception as exc:  # noqa: BLE001 — jedna stránka nesmí shodit běh
                    metrics["facebook_blocked"] += 1
                    errors.append({"source": slug, "error": f"{type(exc).__name__}: {exc}"})
                    log(f"  {slug}: CHYBA {type(exc).__name__}")
                    continue
                if blocked:
                    # Blokovaná stránka NENÍ zkontrolovaná stránka. Kdyby se sem
                    # počítala, úplně neúspěšný běh by se tvářil jako "no-change".
                    metrics["facebook_blocked"] += 1
                    errors.append({"source": slug, "error": blocked})
                    log(f"  {slug}: nedostupné ({blocked})")
                    continue
                metrics["facebook_pages_checked"] += 1
                checked_sources.append(source)
                # Bloky, které se nepodařilo naparsovat, jsou potenciálně ztracené akce.
                # Bez téhle kontroly by úbytek nebyl nikde vidět.
                unparsed = (rows[0]["blocks_seen"] - len(rows)) if rows else 0
                if unparsed > 0:
                    metrics["facebook_blocks_unparsed"] += unparsed
                    unparsed_pages.append(f"{slug} ({unparsed})")
                if rows and rows[0].get("listing_truncated"):
                    truncated_pages.append(slug)
                    log(f"  {slug}: {len(rows)} akcí (výpis ještě nedošel na konec)")
                else:
                    log(f"  {slug}: {len(rows)} akcí"
                        + (f", {unparsed} bloků nenaparsováno" if unparsed else ""))

                for row in rows:
                    event_id = row["facebook_event_id"]
                    metrics["facebook_events_extracted"] += 1
                    if event_id in already_known or event_id in seen_ids:
                        metrics["facebook_duplicates"] += 1
                        continue
                    seen_ids.add(event_id)

                    detail = None
                    if not args.no_detail:
                        await asyncio.sleep(args.delay)
                        try:
                            detail = await fetch_detail(page, event_id, args.detail_wait, now)
                            if detail["detail_ok"]:
                                metrics["facebook_details_fetched"] += 1
                                for sid in detail["suggested_event_ids"]:
                                    suggested.add(sid)
                            else:
                                # Bez tohohle by blokované detaily nebyly vidět nikde:
                                # kandidáti by jen tiše přišli o adresu a konec.
                                metrics["facebook_details_failed"] += 1
                                errors.append({"event": event_id,
                                               "error": "detail nedostupný (login nebo blokace)"})
                        except Exception as exc:  # noqa: BLE001
                            errors.append({"event": event_id,
                                           "error": f"detail: {type(exc).__name__}: {exc}"})

                    candidate = build_candidate(row, source, detail, now)

                    # Facebook běžně obsahuje tutéž akci založenou dvakrát pod
                    # různými ID. Kód je nespojuje, jen na to upozorní Curatora.
                    key = (candidate["title"].strip().lower(), candidate["start_at"])
                    if key in by_title_time:
                        twin = by_title_time[key]
                        candidate["notes"] += (
                            f" Pozor: stejný název i čas jako {twin['id']} —"
                            " možná duplicita přímo na Facebooku."
                        )
                        # Upozornění patří na obě kopie, jinak si Curator všimne
                        # jen té druhé a první projde bez povšimnutí.
                        twin["notes"] += (
                            f" Pozor: stejný název i čas jako {candidate['id']} —"
                            " možná duplicita přímo na Facebooku."
                        )
                    else:
                        by_title_time[key] = candidate
                    candidates.append(candidate)
                await asyncio.sleep(args.delay)
        finally:
            await browser.close()

    # Návrhy, které jsme si sebrali sami v tomhle běhu, nejsou "mimo seed list".
    suggested -= seen_ids
    suggested -= already_known
    metrics["facebook_suggested_events_seen"] = len(suggested)
    finished_at = datetime.now(TZ)

    if metrics["facebook_blocked"] and metrics["facebook_pages_checked"] == 0:
        status = "failed"
    elif errors or metrics["facebook_blocked"] or metrics["facebook_blocks_unparsed"]:
        # Dokumentace slibuje při nenulovém facebook_blocked nejméně "partial";
        # prázdný výsledek to nesmí přebít na "no-change".
        status = "partial"
    elif not candidates:
        status = "no-change"
    else:
        status = "success"

    if suggested:
        notes.append(
            f"Blok „Navrhované události“ nabídl {len(suggested)} dalších ID akcí mimo seed list. "
            "Úplný seznam je v poli suggested_event_ids."
        )
    if metrics["facebook_events_extracted"] and not candidates:
        notes.append("Všechny nalezené akce už byly v repozitáři známé.")
    if unparsed_pages:
        notes.append(
            "Nenaparsované bloky výpisu (potenciálně ztracené akce, prověřit formát): "
            + ", ".join(unparsed_pages)
        )
    if truncated_pages:
        notes.append(
            "Výpis se nepodařilo dorolovat do konce u těchto stránek, mohou mít další akce: "
            + ", ".join(truncated_pages) + ". Zvyš --max-scrolls."
        )

    payload = {
        "schema_version": 1,
        "generated_at": finished_at.isoformat(timespec="seconds"),
        "generator": "tools/fb-events",
        "candidates": candidates,
    }
    run_id = f"{started_at:%Y-%m-%d-%H%M}-facebook"
    report = {
        "schema_version": 1,
        "agent": "facebook",
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": finished_at.isoformat(timespec="seconds"),
        "duration_seconds": round((finished_at - started_at).total_seconds()),
        "status": status,
        "partial_reason": "část stránek se nepodařilo načíst" if status == "partial" else None,
        "commit_sha": git_sha(),
        "metrics": metrics,
        # Jen skutečně načtené stránky — dokumentace to výslovně vyžaduje a jinak
        # by se pokrytí nadhodnocovalo o obce, kam se běh vůbec nedostal.
        "coverage": {
            "regions": sorted({s["region"] for s in checked_sources if s.get("region")}),
            "districts": sorted({s["district"] for s in checked_sources if s.get("district")}),
            "municipalities": sorted({s["municipality"] for s in checked_sources
                                      if s.get("municipality")}),
        },
        "errors": errors,
        "notes": notes,
        "suggested_event_ids": sorted(suggested),
    }

    log("")
    log(f"Kandidátů: {len(candidates)}  |  duplicit: {metrics['facebook_duplicates']}"
        f"  |  nedostupných stránek: {metrics['facebook_blocked']}  |  stav: {status}")

    if args.dry_run:
        log("--dry-run: nic se nezapisuje.")
        log(json.dumps(payload, ensure_ascii=False, indent=2)[:2000])
        return 0

    # Jméno nese i čas. Bez toho druhý běh téhož dne označí vlastní předchozí
    # kandidáty za duplicity, vyrobí prázdný seznam a přepíše jím původní soubor.
    out_dir = REPO_ROOT / "research"
    out_path = out_dir / f"candidates-{started_at:%Y-%m-%d-%H%M}-facebook.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    stats_dir = REPO_ROOT / "stats" / "runs" / f"{started_at:%Y-%m}"
    stats_dir.mkdir(parents=True, exist_ok=True)
    (stats_dir / f"{run_id}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    log(f"Zapsáno: {out_path.relative_to(REPO_ROOT)}")
    log(f"Zapsáno: {(stats_dir / (run_id + '.json')).relative_to(REPO_ROOT)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="config/facebook-sources.json")
    parser.add_argument("--pages", help="jen tyto zdroje, oddělené čárkou (source_id nebo slug)")
    parser.add_argument("--limit-pages", type=int, help="zpracovat jen prvních N zdrojů")
    parser.add_argument("--no-detail", action="store_true",
                        help="nenačítat detaily akcí (rychlejší, bez konce a adresy)")
    parser.add_argument("--delay", type=float, default=3.0, help="pauza mezi requesty v sekundách")
    parser.add_argument("--render-wait", type=float, default=8.0,
                        help="čekání na vykreslení výpisu akcí; pod 8 s se stránky "
                             "nestíhají dorenderovat a vypadají jako prázdné")
    parser.add_argument("--detail-wait", type=float, default=4.0,
                        help="čekání na vykreslení detailu akce")
    parser.add_argument("--max-scrolls", type=int, default=8,
                        help="kolikrát dorolovat výpis; Facebook zobrazí bez scrollování jen 8 akcí")
    parser.add_argument("--scroll-wait", type=float, default=2.5,
                        help="čekání na dotažení dalších položek po scrollu")
    parser.add_argument("--dry-run", action="store_true", help="nic nezapisovat, jen vypsat")
    parser.add_argument("--ignore-known", action="store_true",
                        help="nepřeskakovat už známé akce (obnova dat, ladění parsování)")
    args = parser.parse_args()
    if args.delay < 1.0:
        parser.error("--delay pod 1 s je vůči Facebooku neohleduplné")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
