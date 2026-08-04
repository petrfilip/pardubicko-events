"""Sémantické kontroly nad rámec JSON schématu.

Schéma hlídá tvar jednotlivého souboru. Tento modul hlídá vztahy: mezi
manifestem a týdenními soubory, mezi kandidáty a produkčními akcemi, mezi
názvem souboru reportu a jeho obsahem, a časovou konzistenci.

Kontroly nesahají na síť. Kontrola dostupnosti odkazů je v `linkcheck.py`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Prague")

ERROR = "error"
WARNING = "warning"

REPORT_NAME_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})-(\d{4})-([a-z]+)$")

# Pole, která podle ADR 0001 musí být shodná u všech kopií jedné akce.
IDENTITY_FIELDS = ("title", "start_at", "end_at", "venue", "municipality")


@dataclass(frozen=True)
class Finding:
    level: str
    file: str
    path: str
    message: str

    def __str__(self) -> str:
        location = f"{self.file}" + (f"  {self.path}" if self.path else "")
        return f"{self.level.upper():<7} {location}\n        {self.message}"


@dataclass
class Repo:
    """Načtený obsah repozitáře. Nečitelné soubory jsou v `load_errors`."""

    root: Path
    manifest: dict[str, Any] | None = None
    weeks: dict[str, dict[str, Any]] = field(default_factory=dict)
    candidates: dict[str, dict[str, Any]] = field(default_factory=dict)
    registry: dict[str, Any] | None = None
    facebook_sources: dict[str, Any] | None = None
    categories: dict[str, Any] | None = None
    reports: dict[str, dict[str, Any]] = field(default_factory=dict)
    load_errors: list[Finding] = field(default_factory=list)

    def all_events(self) -> list[tuple[str, int, dict[str, Any]]]:
        out = []
        for name, data in sorted(self.weeks.items()):
            for index, event in enumerate(data.get("events") or []):
                out.append((name, index, event))
        return out

    def all_candidates(self) -> list[tuple[str, int, dict[str, Any]]]:
        out = []
        for name, data in sorted(self.candidates.items()):
            for index, candidate in enumerate(data.get("candidates") or []):
                out.append((name, index, candidate))
        return out


def _parse_dt(value: str | None) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _parse_date(value: str | None) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _week_bounds(week_id: str) -> tuple[date, date] | None:
    match = re.fullmatch(r"(\d{4})-W(\d{2})", week_id or "")
    if not match:
        return None
    year, week = int(match.group(1)), int(match.group(2))
    try:
        monday = date.fromisocalendar(year, week, 1)
    except ValueError:
        return None
    return monday, monday + timedelta(days=6)


def _iso_week_id(value: date) -> str:
    year, week, _ = value.isocalendar()
    return f"{year}-W{week:02d}"


# --------------------------------------------------------------------------
# Manifest a týdenní soubory
# --------------------------------------------------------------------------


def check_manifest(repo: Repo) -> list[Finding]:
    findings: list[Finding] = []
    if not repo.manifest:
        return findings

    manifest_file = "data/manifest.json"
    listed: set[str] = set()

    for index, week in enumerate(repo.manifest.get("weeks") or []):
        path = f"weeks[{index}]"
        week_id = week.get("id", "")
        listed.add(week.get("file", ""))

        target = repo.root / week.get("file", "")
        if not target.is_file():
            findings.append(Finding(
                ERROR, manifest_file, path,
                f"Manifest odkazuje na neexistující soubor {week.get('file')!r}.",
            ))

        expected_name = f"data/weeks/{week_id}.json"
        if week.get("file") != expected_name:
            findings.append(Finding(
                ERROR, manifest_file, path,
                f"Týden {week_id!r} má očekávat soubor {expected_name!r}, "
                f"nalezeno {week.get('file')!r}.",
            ))

        bounds = _week_bounds(week_id)
        if bounds is None:
            findings.append(Finding(
                ERROR, manifest_file, path, f"Neplatné ISO označení týdne {week_id!r}."))
            continue

        monday, sunday = bounds
        if week.get("from") != monday.isoformat() or week.get("to") != sunday.isoformat():
            findings.append(Finding(
                ERROR, manifest_file, path,
                f"Rozsah týdne {week_id} má být {monday}–{sunday}, "
                f"uvedeno {week.get('from')}–{week.get('to')}.",
            ))

    for name in sorted(repo.weeks):
        relative = f"data/weeks/{name}"
        if relative not in listed:
            findings.append(Finding(
                ERROR, relative, "",
                "Soubor existuje, ale není uveden v data/manifest.json.",
            ))

    return findings


def check_manifest_generated_at(repo: Repo) -> list[Finding]:
    """Manifest musí být alespoň tak nový jako každý odkazovaný týden."""
    if not repo.manifest:
        return []

    manifest_generated_at = _parse_dt(repo.manifest.get("generated_at"))
    if manifest_generated_at is None or manifest_generated_at.tzinfo is None:
        # Chybějící nebo neplatný timestamp hlídá JSON schéma.
        return []

    findings: list[Finding] = []
    for week in repo.manifest.get("weeks") or []:
        week_file = week.get("file")
        if not isinstance(week_file, str):
            continue
        week_data = repo.weeks.get(Path(week_file).name)
        week_generated_at = _parse_dt(
            week_data.get("generated_at") if week_data else None)
        if (
            week_generated_at is not None
            and week_generated_at.tzinfo is not None
            and manifest_generated_at < week_generated_at
        ):
            findings.append(Finding(
                ERROR, "data/manifest.json", "generated_at",
                f"Manifest je starší než odkazovaný týden {week.get('id')}: "
                f"{repo.manifest.get('generated_at')} < "
                f"{week_data.get('generated_at')}.",
            ))

    return findings


def check_week_files(repo: Repo) -> list[Finding]:
    findings: list[Finding] = []

    for name, data in sorted(repo.weeks.items()):
        relative = f"data/weeks/{name}"
        stem = Path(name).stem
        file_week = data.get("week")

        if file_week != stem:
            findings.append(Finding(
                ERROR, relative, "week",
                f"Pole week ({file_week!r}) neodpovídá názvu souboru ({stem!r}).",
            ))

        bounds = _week_bounds(stem)
        if bounds is None:
            continue
        monday, sunday = bounds

        for index, event in enumerate(data.get("events") or []):
            path = f"events[{index}] ({event.get('id', '?')})"

            if event.get("week") != file_week:
                findings.append(Finding(
                    ERROR, relative, path,
                    f"Akce má week {event.get('week')!r}, soubor je {file_week!r}.",
                ))

            start = _parse_dt(event.get("start_at"))
            if start is None:
                continue
            end = _parse_dt(event.get("end_at")) or start

            if end.date() < monday or start.date() > sunday:
                findings.append(Finding(
                    ERROR, relative, path,
                    f"Akce {start.date()}–{end.date()} nezasahuje do týdne "
                    f"{monday}–{sunday}. Špatné zařazení nebo špatný rok.",
                ))

    return findings


def check_event_times(repo: Repo) -> list[Finding]:
    findings: list[Finding] = []

    for name, index, event in repo.all_events():
        relative = f"data/weeks/{name}"
        path = f"events[{index}] ({event.get('id', '?')})"

        start = _parse_dt(event.get("start_at"))
        end = _parse_dt(event.get("end_at"))

        if start and end and end < start:
            findings.append(Finding(
                ERROR, relative, path,
                f"end_at ({event['end_at']}) je před start_at ({event['start_at']}).",
            ))

        for field_name in ("start_at", "end_at"):
            value = _parse_dt(event.get(field_name))
            if value is None:
                continue
            expected = value.astimezone(TZ).utcoffset()
            if value.utcoffset() != expected:
                findings.append(Finding(
                    ERROR, relative, path,
                    f"{field_name} ({event[field_name]}) nemá platný posun pro "
                    f"Europe/Prague; k tomuto datu platí {expected}.",
                ))

    return findings


def check_event_identity(repo: Repo) -> list[Finding]:
    """Kopie jedné akce se smějí lišit pouze polem `week` (ADR 0001).

    Porovnává se celý záznam, ne vybraný výčet polí. Užší výčet byl past:
    pole mimo něj — v praxi `description` a `last_verified_at` — se tiše
    rozcházela, protože kontrola je za chybu nepovažovala, ale slučování
    v `js/data.js` podle nich rozlišovalo. Zobrazená varianta pak závisela
    na pořadí načtení souborů.

    Zrcadlí `eventIdentity` v `js/data.js` a `_identity`
    v `tools/pipeline/import_repo.py`. Tady selže nejdřív, tedy v CI.
    """
    findings: list[Finding] = []
    seen: dict[str, tuple[str, dict]] = {}

    for name, index, event in repo.all_events():
        relative = f"data/weeks/{name}"
        event_id = event.get("id")
        if not event_id:
            continue

        if event_id not in seen:
            seen[event_id] = (relative, event)
            continue

        origin, previous = seen[event_id]
        differing = sorted(
            key for key in set(previous) | set(event)
            if key != "week" and previous.get(key) != event.get(key)
        )
        if differing:
            findings.append(Finding(
                ERROR, relative, f"events[{index}] ({event_id})",
                f"Stejné ID jako v {origin}, ale liší se: {', '.join(differing)}. "
                "Kopie jedné akce musí být shodné ve všech polích kromě week.",
            ))

    return findings


def check_starting_week_present(repo: Repo) -> list[Finding]:
    """Akce musí být zapsaná v týdnu, ve kterém začíná, pokud ten týden existuje."""
    findings: list[Finding] = []
    if not repo.manifest:
        return findings

    known_weeks = {week.get("id") for week in repo.manifest.get("weeks") or []}
    placements: dict[str, set[str]] = {}
    starts: dict[str, date] = {}

    for name, _index, event in repo.all_events():
        event_id = event.get("id")
        start = _parse_dt(event.get("start_at"))
        if not event_id or start is None:
            continue
        placements.setdefault(event_id, set()).add(Path(name).stem)
        starts[event_id] = start.date()

    for event_id, weeks in sorted(placements.items()):
        starting_week = _iso_week_id(starts[event_id])
        if starting_week in known_weeks and starting_week not in weeks:
            findings.append(Finding(
                ERROR, f"data/weeks/{starting_week}.json", event_id,
                f"Akce začíná {starts[event_id]}, tedy v týdnu {starting_week}, "
                f"ale je zapsaná jen v {', '.join(sorted(weeks))}.",
            ))

    return findings


def check_source_quality(repo: Repo) -> list[Finding]:
    """Obecná homepage místo konkrétního detailu akce (metrika generic_links_found)."""
    findings: list[Finding] = []

    for name, index, event in repo.all_events():
        url = (event.get("source") or {}).get("url")
        if not url:
            continue
        parsed = urlparse(url)
        if parsed.path in ("", "/") and not parsed.query:
            findings.append(Finding(
                WARNING, f"data/weeks/{name}", f"events[{index}] ({event.get('id', '?')})",
                f"Zdroj {url!r} je homepage bez konkrétního detailu akce.",
            ))

    return findings


def check_semantic_duplicates(repo: Repo) -> list[Finding]:
    """Shoda source.url + start_at + municipality + venue při různém ID.

    Podle daily-event-curator.md je to silný signál duplicity, který nesmí
    projít jen proto, že se liší ID. Rozhodnutí je úsudkové, proto warning.
    """
    findings: list[Finding] = []
    groups: dict[tuple, set[str]] = {}
    locations: dict[tuple, str] = {}

    for name, index, event in repo.all_events():
        key = (
            (event.get("source") or {}).get("url"),
            event.get("start_at"),
            event.get("municipality"),
            event.get("venue"),
        )
        groups.setdefault(key, set()).add(event.get("id", "?"))
        locations.setdefault(key, f"data/weeks/{name}  events[{index}]")

    for key, ids in sorted(groups.items(), key=lambda item: sorted(item[1])):
        if len(ids) > 1:
            findings.append(Finding(
                WARNING, locations[key].split("  ")[0], locations[key].split("  ")[1],
                f"Různá ID se shodným zdrojem, časem, obcí i místem: {', '.join(sorted(ids))}. "
                "Prověř, zda nejde o jednu akci.",
            ))

    return findings


# --------------------------------------------------------------------------
# Kandidáti
# --------------------------------------------------------------------------


def check_candidates(repo: Repo) -> list[Finding]:
    findings: list[Finding] = []
    event_ids = {event.get("id") for _n, _i, event in repo.all_events()}
    seen: dict[str, str] = {}

    for name, index, candidate in repo.all_candidates():
        relative = f"research/{name}"
        candidate_id = candidate.get("id", "?")
        path = f"candidates[{index}] ({candidate_id})"
        status = candidate.get("status")
        production_id = candidate.get("production_event_id")

        if candidate_id in seen and seen[candidate_id] != relative:
            findings.append(Finding(
                WARNING, relative, path,
                f"Stejné kandidátní ID je i v {seen[candidate_id]}. "
                "Do backlogu se počítá jednou; konflikt patří do notes.",
            ))
        seen.setdefault(candidate_id, relative)

        if status == "imported":
            if not production_id:
                findings.append(Finding(
                    WARNING, relative, path,
                    "Stav imported bez production_event_id; chybí vazba na produkční akci.",
                ))
            elif production_id not in event_ids:
                findings.append(Finding(
                    ERROR, relative, path,
                    f"production_event_id {production_id!r} neodpovídá žádné akci "
                    "v data/weeks/.",
                ))

        if status == "rejected" and not candidate.get("resolution_notes"):
            findings.append(Finding(
                WARNING, relative, path,
                "Stav rejected bez resolution_notes; důvod zamítnutí není doložen.",
            ))

    return findings


# --------------------------------------------------------------------------
# Řízený slovník kategorií (balíček P2-3)
# --------------------------------------------------------------------------


def normalize_category(value: str) -> str:
    """Porovnávací tvar kategorie: malá písmena, spojovníky, bez diakritiky.

    Jeden alias tak pokryje i zápis s mezerou nebo bez háčků — `klasická
    hudba` i `klasicka-hudba` vedou na týž klíč. Ve slovníku se proto alias
    zapisuje jednou, v čitelné podobě s diakritikou.
    """
    decomposed = unicodedata.normalize("NFKD", value.strip().lower())
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"-{2,}", "-", re.sub(r"[\s_]+", "-", without_marks)).strip("-")


def _used_candidate_categories(repo: Repo) -> dict[str, tuple[int, str, str]]:
    """Syrové kategorie kandidátů: hodnota -> (počet, soubor, první cesta)."""
    used: dict[str, tuple[int, str, str]] = {}

    items = [
        (f"research/{name}", f"candidates[{index}] ({item.get('id', '?')})", item)
        for name, index, item in repo.all_candidates()
    ]

    for relative, path, item in items:
        for value in item.get("categories") or []:
            if not isinstance(value, str) or not value.strip():
                continue
            count, first_file, first_path = used.get(value, (0, relative, path))
            used[value] = (count + 1, first_file, first_path)

    return used


def check_categories(repo: Repo) -> list[Finding]:
    """Slovník kategorií a jeho pokrytí nad skutečnými daty.

    Rozpor uvnitř slovníku je chyba — slovník píše člověk a musí být
    konzistentní. Publikované týdenní soubory smějí obsahovat jen přesná
    kanonická ID a musí pokrýt každou povinnou osu. Kandidáti mohou nést
    syrové aliasy; neznámé hodnoty se u nich hlásí jako varování.
    """
    findings: list[Finding] = []
    dictionary = repo.categories
    if not dictionary:
        return findings

    file = "config/categories.json"
    axis_ids: set[str] = set()
    required_axes: set[str] = set()
    for index, axis in enumerate(dictionary.get("axes") or []):
        axis_id = axis.get("id")
        if axis_id in axis_ids:
            findings.append(Finding(
                ERROR, file, f"axes[{index}] ({axis_id})",
                f"Duplicitní id osy {axis_id!r}."))
        axis_ids.add(axis_id)
        if axis.get("required") and axis_id:
            required_axes.add(axis_id)

    known_ids: set[str] = set()
    category_axes: dict[str, str] = {}
    for index, category in enumerate(dictionary.get("categories") or []):
        category_id = category.get("id")
        location = f"categories[{index}] ({category_id})"

        if category_id in known_ids:
            findings.append(Finding(
                ERROR, file, location, f"Duplicitní id kategorie {category_id!r}."))
        known_ids.add(category_id)
        category_axes[category_id] = category.get("axis")

        if category.get("axis") not in axis_ids:
            findings.append(Finding(
                ERROR, file, location,
                f"Kategorie odkazuje na neznámou osu {category.get('axis')!r}. "
                f"Známé osy: {', '.join(sorted(str(item) for item in axis_ids))}.",
            ))

    # Kanonické id je platnou hodnotou i bez aliasu.
    lookup = {normalize_category(str(item)): item for item in known_ids if item}
    canonical_keys = set(lookup)

    for index, entry in enumerate(dictionary.get("aliases") or []):
        alias = entry.get("alias", "")
        target = entry.get("category_id")
        key = normalize_category(alias)
        location = f"aliases[{index}] ({alias})"

        if target not in known_ids:
            findings.append(Finding(
                ERROR, file, location,
                f"Alias míří na neznámou kategorii {target!r}.",
            ))
            continue

        if key in canonical_keys and lookup[key] != target:
            findings.append(Finding(
                ERROR, file, location,
                f"Alias se shoduje s kanonickým id kategorie {lookup[key]!r}, "
                f"ale míří na {target!r}. Alias nesmí zastínit vlastní id kategorie.",
            ))
            continue

        if key in lookup and lookup[key] != target:
            findings.append(Finding(
                ERROR, file, location,
                f"Alias je uveden dvakrát a pokaždé jinam: {lookup[key]!r} a {target!r}. "
                "Po normalizaci jde o týž klíč.",
            ))
            continue

        lookup[key] = target

    review: set[str] = set()
    for index, entry in enumerate(dictionary.get("review") or []):
        value = entry.get("value", "")
        key = normalize_category(value)
        review.add(key)
        mapped = entry.get("mapped_to")
        location = f"review[{index}] ({value})"

        if mapped is not None and mapped not in known_ids:
            findings.append(Finding(
                ERROR, file, location,
                f"Sporná hodnota míří na neznámou kategorii {mapped!r}.",
            ))
        elif mapped != lookup.get(key):
            findings.append(Finding(
                ERROR, file, location,
                f"Sporná hodnota má mapped_to {mapped!r}, ale v aliases je "
                f"{lookup.get(key)!r}. Obojí musí říkat totéž.",
            ))

    for name, index, event in repo.all_events():
        relative = f"data/weeks/{name}"
        location = f"events[{index}] ({event.get('id', '?')})"
        event_axes: set[str] = set()
        for category_index, value in enumerate(event.get("categories") or []):
            if not isinstance(value, str):
                continue
            if value not in known_ids:
                mapped = lookup.get(normalize_category(value))
                suggestion = f"; použij {mapped!r}" if mapped else ""
                findings.append(Finding(
                    ERROR, relative, f"{location}.categories[{category_index}]",
                    f"Publikovaná kategorie {value!r} není kanonické ID{suggestion}.",
                ))
                continue
            event_axes.add(category_axes[value])

        for axis_id in sorted(required_axes - event_axes):
            findings.append(Finding(
                ERROR, relative, location,
                f"Akce nemá kategorii z povinné osy {axis_id!r}.",
            ))

    used = _used_candidate_categories(repo)
    unknown = [
        (count, value, first_file, first_path)
        for value, (count, first_file, first_path) in used.items()
        if normalize_category(value) not in lookup
    ]

    for count, value, first_file, first_path in sorted(unknown, key=lambda row: (-row[0], row[1])):
        if normalize_category(value) in review:
            message = (
                f"Kategorie {value!r} ({count}×) je v config/categories.json vedená "
                "jako sporná a zatím nemá alias. Před publikací je nutné "
                "rozhodnutí ze seznamu review."
            )
        else:
            message = (
                f"Kategorie {value!r} ({count}×) nemá alias v config/categories.json. "
                "Doplň alias, nebo hodnotu zapiš do seznamu review s vysvětlením; "
                "do publikovaných dat nesmí projít."
            )
        findings.append(Finding(WARNING, first_file, first_path, message))

    return findings


# --------------------------------------------------------------------------
# Registr zdrojů a reporty
# --------------------------------------------------------------------------


def check_registry(repo: Repo) -> list[Finding]:
    findings: list[Finding] = []
    registry_ids: set[str] = set()

    if repo.registry:
        seen: set[str] = set()
        for index, source in enumerate(repo.registry.get("sources") or []):
            source_id = source.get("id")
            if source_id in seen:
                findings.append(Finding(
                    ERROR, "config/source-registry.json", f"sources[{index}]",
                    f"Duplicitní identifikátor zdroje {source_id!r}.",
                ))
            seen.add(source_id)
        registry_ids = seen

    if repo.facebook_sources and registry_ids:
        for index, page in enumerate(repo.facebook_sources.get("pages") or []):
            source_id = page.get("source_id")
            if source_id and source_id not in registry_ids:
                findings.append(Finding(
                    WARNING, "config/facebook-sources.json", f"pages[{index}]",
                    f"source_id {source_id!r} není v config/source-registry.json.",
                ))

    if (repo.root / "config" / "sources.json").is_file():
        findings.append(Finding(
            ERROR, "config/sources.json", "",
            "Zakázaný soubor. Jediným registrem je config/source-registry.json.",
        ))

    return findings


def check_run_reports(repo: Repo) -> list[Finding]:
    findings: list[Finding] = []

    for relative, data in sorted(repo.reports.items()):
        stem = Path(relative).stem
        match = REPORT_NAME_RE.fullmatch(stem)

        if not match:
            findings.append(Finding(
                ERROR, relative, "",
                "Název neodpovídá tvaru YYYY-MM-DD-HHMM-<agent>.json.",
            ))
            continue

        year, month, day, _time, agent = match.groups()

        if data.get("run_id") != stem:
            findings.append(Finding(
                ERROR, relative, "run_id",
                f"run_id {data.get('run_id')!r} neodpovídá názvu souboru {stem!r}.",
            ))

        if data.get("agent") != agent:
            findings.append(Finding(
                ERROR, relative, "agent",
                f"agent {data.get('agent')!r} neodpovídá názvu souboru ({agent!r}).",
            ))

        expected_dir = f"stats/runs/{year}-{month}"
        if not relative.startswith(expected_dir + "/"):
            findings.append(Finding(
                ERROR, relative, "",
                f"Report z {year}-{month}-{day} patří do {expected_dir}/.",
            ))

        started = _parse_dt(data.get("started_at"))
        finished = _parse_dt(data.get("finished_at"))

        if started and finished:
            if finished < started:
                findings.append(Finding(
                    ERROR, relative, "finished_at",
                    "finished_at je před started_at.",
                ))
            else:
                declared = data.get("duration_seconds")
                measured = (finished - started).total_seconds()
                if declared is not None and abs(declared - measured) > 60:
                    findings.append(Finding(
                        WARNING, relative, "duration_seconds",
                        f"duration_seconds ({declared}) neodpovídá rozdílu časů "
                        f"({measured:.0f} s). Doba běhu se nemá odhadovat.",
                    ))

        if data.get("status") == "partial" and not data.get("partial_reason"):
            findings.append(Finding(
                WARNING, relative, "partial_reason",
                "Stav partial bez uvedeného důvodu.",
            ))

    return findings


ALL_CHECKS = (
    check_manifest,
    check_manifest_generated_at,
    check_week_files,
    check_event_times,
    check_event_identity,
    check_starting_week_present,
    check_source_quality,
    check_semantic_duplicates,
    check_candidates,
    check_categories,
    check_registry,
    check_run_reports,
)


def run_all(repo: Repo) -> list[Finding]:
    findings = list(repo.load_errors)
    for check in ALL_CHECKS:
        findings.extend(check(repo))
    return findings
