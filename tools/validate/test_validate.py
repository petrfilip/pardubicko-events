"""Testy validátoru. Ověřují, že poškozený soubor v každé kategorii selže.

Validátor, který nic nenajde, je horší než žádný — proto je základní sada
testů právě o tom, že jednotlivé druhy vad skutečně shodí kontrolu.

Spuštění:

    python3 tools/validate/test_validate.py
    docker compose run --rm tests
"""

import copy
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from checks import ERROR, WARNING  # noqa: E402
import validate  # noqa: E402

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        failures.append(name + (f": {detail}" if detail else ""))


WEEK_ID = "2026-W31"  # pondělí 2026-07-27 až neděle 2026-08-02
EVENT_ID = "testovaci-akce-2026-07-28"


def base_files() -> dict[str, dict]:
    return {
        "data/manifest.json": {
            "schema_version": 1,
            "generated_at": "2026-07-27T08:00:00+02:00",
            "weeks": [{
                "id": WEEK_ID,
                "from": "2026-07-27",
                "to": "2026-08-02",
                "file": f"data/weeks/{WEEK_ID}.json",
            }],
        },
        f"data/weeks/{WEEK_ID}.json": {
            "schema_version": 1,
            "week": WEEK_ID,
            "generated_at": "2026-07-27T08:00:00+02:00",
            "events": [{
                "id": EVENT_ID,
                "week": WEEK_ID,
                "title": "Testovací akce",
                "description": "Popis akce.",
                "start_at": "2026-07-28T10:00:00+02:00",
                "end_at": "2026-07-28T12:00:00+02:00",
                "all_day": False,
                "venue": "Resselovo náměstí",
                "municipality": "Chrudim",
                "categories": ["hudba"],
                "price": {"type": "free", "text": "Zdarma"},
                "source": {"type": "official", "url": "https://example.org/akce/1"},
                "cancelled": False,
            }],
        },
        "research/candidates-test.json": {
            "schema_version": 1,
            "generated_at": "2026-07-27T08:00:00+02:00",
            "candidates": [{
                "id": "kandidat-1",
                "title": "Testovací akce",
                "municipality": "Chrudim",
                "region": "pardubicky-kraj",
                "discovered_at": "2026-07-27T07:00:00+02:00",
                "discovery_method": "known-source",
                "source_url": "https://example.org/akce/1",
                "candidate_kind": "single-event",
                "status": "imported",
                "production_event_id": EVENT_ID,
            }],
        },
        "config/source-registry.json": {
            "schema_version": 1,
            "updated_at": "2026-07-27T08:00:00+02:00",
            "sources": [{
                "id": "test-source",
                "name": "Testovací zdroj",
                "url": "https://example.org/kalendar",
                "type": "city-calendar",
                "municipality": "Chrudim",
                "district": "Chrudim",
                "region": "pardubicky-kraj",
                "priority": "high",
                "check_interval_days": 1,
                "notes": None,
            }],
        },
        "config/facebook-sources.json": {
            "schema_version": 1,
            "updated_at": "2026-07-27T08:00:00+02:00",
            "user_agent": "TestBot/0.1",
            "notes": [],
            "pages": [{
                "source_id": "test-source",
                "name": "Testovací zdroj",
                "facebook_page": "TestovaciZdroj",
                "municipality": "Chrudim",
                "district": "Chrudim",
                "region": "pardubicky-kraj",
                "priority": "high",
                "check_interval_days": 2,
                "enabled": True,
            }],
        },
        "config/categories.json": {
            "schema_version": 1,
            "updated_at": "2026-07-27T08:00:00+02:00",
            "axes": [
                {"id": "kind", "label": "Druh akce", "required": True},
                {"id": "audience", "label": "Pro koho", "required": False},
            ],
            "categories": [
                {"id": "hudba", "axis": "kind", "order": 10,
                 "label": "Koncerty a hudba"},
                {"id": "rodiny", "axis": "audience", "order": 20,
                 "label": "Pro rodiny s dětmi"},
            ],
            "aliases": [
                {"alias": "kultura", "category_id": "hudba"},
                {"alias": "klasická-hudba", "category_id": "hudba"},
                {"alias": "rodiny", "category_id": "rodiny"},
            ],
            "review": [],
        },
        "config/municipalities.json": {
            "schema_version": 1,
            "updated_at": "2026-07-27T08:00:00+02:00",
            "generator": "tools/pipeline/municipalities.py",
            "source": {
                "name": "Struktura území ČR – otevřená data",
                "publisher": "Český statistický úřad",
                "url": "https://example.org/struktura_uzemi_cr.csv",
                "downloaded_at": "2026-07-27",
                "valid_from": "2026-01-01",
            },
            "counts": {"total": 2, "pardubicky-kraj": 2},
            "municipalities": [
                {"code": 571164, "name": "Chrudim", "type": "mesto",
                 "district": "Chrudim", "region": "pardubicky-kraj"},
                {"code": 571512, "name": "Trhová Kamenice", "type": "mestys",
                 "district": "Chrudim", "region": "pardubicky-kraj"},
            ],
        },
        "stats/runs/2026-07/2026-07-28-1200-quality.json": {
            "schema_version": 1,
            "agent": "quality",
            "run_id": "2026-07-28-1200-quality",
            "started_at": "2026-07-28T12:00:00+02:00",
            "finished_at": "2026-07-28T12:10:00+02:00",
            "duration_seconds": 600,
            "status": "success",
            "partial_reason": None,
            "commit_sha": None,
            "metrics": {},
            "coverage": {},
            "errors": [],
            "notes": [],
        },
    }


def write_repo(root: Path, files: dict[str, object]) -> None:
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            target.write_text(content, encoding="utf-8")
        else:
            target.write_text(
                json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")


def run(mutate=None) -> list:
    """Postaví dočasný repozitář, případně ho poškodí, a vrátí nálezy."""
    files = base_files()
    if mutate:
        mutate(files)
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        write_repo(root, files)
        return validate.collect(root, sorted(validate.GROUPS), run_semantic=True)


def errors(findings) -> list:
    return [item for item in findings if item.level == ERROR]


def warnings(findings) -> list:
    return [item for item in findings if item.level == WARNING]


def mentions(findings, needle: str) -> bool:
    return any(needle.lower() in item.message.lower() for item in findings)


def expect_error(name: str, mutate, needle: str) -> None:
    findings = run(mutate)
    found = errors(findings)
    check(name, bool(found), "žádná chyba nenahlášena")
    if found:
        check(
            f"{name} – správná zpráva",
            mentions(found, needle),
            f"očekáváno {needle!r}, nalezeno: {[item.message for item in found]}",
        )


# --------------------------------------------------------------------------
# Výchozí stav musí projít, jinak nemá smysl testovat cokoli dalšího.
# --------------------------------------------------------------------------

baseline = run()
check("čistý repozitář nemá chyby", not errors(baseline),
      str([str(item) for item in errors(baseline)]))
check("čistý repozitář nemá varování", not warnings(baseline),
      str([str(item) for item in warnings(baseline)]))


# --------------------------------------------------------------------------
# Struktura souborů
# --------------------------------------------------------------------------

def _broken_json(files):
    files[f"data/weeks/{WEEK_ID}.json"] = '{"schema_version": 1, "week": '


expect_error("neplatný JSON", _broken_json, "neplatný json")


def _missing_field(files):
    del files[f"data/weeks/{WEEK_ID}.json"]["events"][0]["title"]


expect_error("chybějící povinné pole akce", _missing_field, "title")


def _unknown_field(files):
    files[f"data/weeks/{WEEK_ID}.json"]["events"][0]["organizator"] = "Někdo"


expect_error("neznámé pole akce", _unknown_field, "additional properties")


def _bad_price(files):
    files[f"data/weeks/{WEEK_ID}.json"]["events"][0]["price"]["type"] = "zdarma"


expect_error("neplatná hodnota price.type", _bad_price, "zdarma")


def _bad_id(files):
    files[f"data/weeks/{WEEK_ID}.json"]["events"][0]["id"] = "Akce S Diakritikou Ř"


expect_error("ID není slug", _bad_id, "does not match")


# --------------------------------------------------------------------------
# Manifest
# --------------------------------------------------------------------------

def _missing_week_file(files):
    del files[f"data/weeks/{WEEK_ID}.json"]


expect_error("manifest odkazuje na neexistující soubor", _missing_week_file,
             "neexistující soubor")


def _unlisted_week_file(files):
    extra = copy.deepcopy(files[f"data/weeks/{WEEK_ID}.json"])
    extra["week"] = "2026-W32"
    extra["events"] = []
    files["data/weeks/2026-W32.json"] = extra


expect_error("týdenní soubor mimo manifest", _unlisted_week_file, "není uveden")


def _wrong_range(files):
    files["data/manifest.json"]["weeks"][0]["to"] = "2026-08-03"


expect_error("špatný rozsah týdne v manifestu", _wrong_range, "rozsah týdne")


def _stale_manifest(files):
    files["data/manifest.json"]["generated_at"] = "2026-07-27T07:59:59+02:00"


expect_error("manifest starší než odkazovaný týden", _stale_manifest,
             "starší než odkazovaný týden")


# --------------------------------------------------------------------------
# Čas a zařazení
# --------------------------------------------------------------------------

def _end_before_start(files):
    files[f"data/weeks/{WEEK_ID}.json"]["events"][0]["end_at"] = \
        "2026-07-28T09:00:00+02:00"


expect_error("end_at před start_at", _end_before_start, "je před start_at")


def _wrong_offset(files):
    event = files[f"data/weeks/{WEEK_ID}.json"]["events"][0]
    event["start_at"] = "2026-07-28T10:00:00+01:00"
    event["end_at"] = "2026-07-28T12:00:00+01:00"


expect_error("posun neodpovídá Europe/Prague", _wrong_offset, "europe/prague")


def _wrong_week(files):
    event = files[f"data/weeks/{WEEK_ID}.json"]["events"][0]
    event["start_at"] = "2026-09-10T10:00:00+02:00"
    event["end_at"] = "2026-09-10T12:00:00+02:00"


expect_error("akce mimo rozsah týdne", _wrong_week, "nezasahuje do týdne")


def _wrong_year(files):
    event = files[f"data/weeks/{WEEK_ID}.json"]["events"][0]
    event["start_at"] = "2025-07-28T10:00:00+02:00"
    event["end_at"] = "2025-07-28T12:00:00+02:00"


expect_error("špatný rok", _wrong_year, "nezasahuje do týdne")


def _week_mismatch(files):
    files[f"data/weeks/{WEEK_ID}.json"]["events"][0]["week"] = "2026-W32"


expect_error("pole week neodpovídá souboru", _week_mismatch, "soubor je")


# --------------------------------------------------------------------------
# Identita akce napříč týdny (ADR 0001)
# --------------------------------------------------------------------------

def _conflicting_copy(files):
    files["data/manifest.json"]["weeks"].append({
        "id": "2026-W32", "from": "2026-08-03", "to": "2026-08-09",
        "file": "data/weeks/2026-W32.json",
    })
    copy_event = copy.deepcopy(files[f"data/weeks/{WEEK_ID}.json"]["events"][0])
    copy_event["week"] = "2026-W32"
    copy_event["title"] = "Jiný název téže akce"
    copy_event["start_at"] = "2026-08-04T10:00:00+02:00"
    copy_event["end_at"] = "2026-08-04T12:00:00+02:00"
    files["data/weeks/2026-W32.json"] = {
        "schema_version": 1, "week": "2026-W32",
        "generated_at": "2026-07-27T08:00:00+02:00", "events": [copy_event],
    }


expect_error("shodné ID s odlišnou identitou", _conflicting_copy, "liší se")


def _copy_differing_only_in_description(files):
    """Vada, která proklouzla užším výčtem identitních polí.

    Kopie se lišila jen popisem. Kontrola ji za chybu nepovažovala, ale
    slučování v js/data.js podle popisu rozlišovalo, takže obě přežily
    pod jedním ID a zobrazená varianta závisela na pořadí načtení.
    """
    files["data/manifest.json"]["weeks"].append({
        "id": "2026-W32", "from": "2026-08-03", "to": "2026-08-09",
        "file": "data/weeks/2026-W32.json",
    })
    twin = copy.deepcopy(files[f"data/weeks/{WEEK_ID}.json"]["events"][0])
    twin["week"] = "2026-W32"
    twin["description"] = "Jinak formulovaný popis téže akce."
    files["data/weeks/2026-W32.json"] = {
        "schema_version": 1, "week": "2026-W32",
        "generated_at": "2026-07-27T08:00:00+02:00", "events": [twin],
    }


expect_error("kopie lišící se jen popisem", _copy_differing_only_in_description,
             "description")


def _copy_differing_only_in_verification(files):
    """Totéž pro last_verified_at — u kopií se sjednocuje na nejnovější."""
    files["data/manifest.json"]["weeks"].append({
        "id": "2026-W32", "from": "2026-08-03", "to": "2026-08-09",
        "file": "data/weeks/2026-W32.json",
    })
    twin = copy.deepcopy(files[f"data/weeks/{WEEK_ID}.json"]["events"][0])
    twin["week"] = "2026-W32"
    twin["last_verified_at"] = "2026-07-29T09:00:00+02:00"
    files["data/weeks/2026-W32.json"] = {
        "schema_version": 1, "week": "2026-W32",
        "generated_at": "2026-07-27T08:00:00+02:00", "events": [twin],
    }


expect_error("kopie lišící se jen časem ověření",
             _copy_differing_only_in_verification, "last_verified_at")


def _missing_starting_week(files):
    """Dlouhá akce zapsaná jen v pozdějším týdnu, ne v tom, kde začíná."""
    files["data/manifest.json"]["weeks"].append({
        "id": "2026-W32", "from": "2026-08-03", "to": "2026-08-09",
        "file": "data/weeks/2026-W32.json",
    })
    long_event = copy.deepcopy(files[f"data/weeks/{WEEK_ID}.json"]["events"][0])
    long_event.update({
        "id": "dlouha-akce-2026", "week": "2026-W32",
        "start_at": "2026-07-30T10:00:00+02:00",
        "end_at": "2026-08-05T18:00:00+02:00",
    })
    files["data/weeks/2026-W32.json"] = {
        "schema_version": 1, "week": "2026-W32",
        "generated_at": "2026-07-27T08:00:00+02:00", "events": [long_event],
    }


expect_error("chybí kopie v úvodním týdnu", _missing_starting_week,
             "ale je zapsaná jen")


# --------------------------------------------------------------------------
# Kandidáti, registr, reporty
# --------------------------------------------------------------------------

def _dangling_production_id(files):
    files["research/candidates-test.json"]["candidates"][0]["production_event_id"] = \
        "neexistujici-akce-2026"


expect_error("imported bez existující akce", _dangling_production_id,
             "neodpovídá žádné akci")


def _forbidden_sources_file(files):
    files["config/sources.json"] = {"sources": []}


expect_error("zakázaný config/sources.json", _forbidden_sources_file, "zakázaný soubor")


def _duplicate_source_id(files):
    registry = files["config/source-registry.json"]
    registry["sources"].append(copy.deepcopy(registry["sources"][0]))


expect_error("duplicitní ID zdroje", _duplicate_source_id, "duplicitní identifikátor")


def _municipality_type_from_head(files):
    """Typ sídla opsaný z webu, ne z číselníku."""
    files["config/municipalities.json"]["municipalities"][1]["type"] = "městys"


expect_error("neplatný typ obce", _municipality_type_from_head, "is not one of")


def _municipality_code_as_text(files):
    """Kód obce jako text; v databázi je to celé číslo a klíč tabulky municipality."""
    files["config/municipalities.json"]["municipalities"][0]["code"] = "571164"


expect_error("kód obce není číslo", _municipality_code_as_text, "is not of type")


def _municipality_without_source(files):
    """Číselník bez doloženého původu se nesmí dostat do repozitáře."""
    del files["config/municipalities.json"]["source"]


expect_error("číselník bez doloženého zdroje", _municipality_without_source, "source")


def _report_id_mismatch(files):
    files["stats/runs/2026-07/2026-07-28-1200-quality.json"]["run_id"] = \
        "2026-07-28-1300-quality"


expect_error("run_id neodpovídá názvu", _report_id_mismatch, "neodpovídá názvu souboru")


def _report_wrong_month(files):
    report = files.pop("stats/runs/2026-07/2026-07-28-1200-quality.json")
    files["stats/runs/2026-08/2026-07-28-1200-quality.json"] = report


expect_error("report ve špatném měsíci", _report_wrong_month, "patří do")


def _report_finished_before_start(files):
    report = files["stats/runs/2026-07/2026-07-28-1200-quality.json"]
    report["finished_at"] = "2026-07-28T11:00:00+02:00"


expect_error("finished_at před started_at", _report_finished_before_start,
             "před started_at")


# --------------------------------------------------------------------------
# Varování nesmí shodit běžnou kontrolu, ale musí být vidět
# --------------------------------------------------------------------------

def _homepage_source(files):
    files[f"data/weeks/{WEEK_ID}.json"]["events"][0]["source"]["url"] = \
        "https://example.org/"


homepage = run(_homepage_source)
check("homepage jako zdroj je jen varování", not errors(homepage),
      str([str(item) for item in errors(homepage)]))
check("homepage jako zdroj se nahlásí", mentions(warnings(homepage), "homepage"))


def _unknown_facebook_source(files):
    files["config/facebook-sources.json"]["pages"][0]["source_id"] = "neznamy-zdroj"


unknown_fb = run(_unknown_facebook_source)
check("neznámý source_id je jen varování", not errors(unknown_fb))
check("neznámý source_id se nahlásí",
      mentions(warnings(unknown_fb), "není v config/source-registry.json"))


def _partial_without_reason(files):
    files["stats/runs/2026-07/2026-07-28-1200-quality.json"]["status"] = "partial"


partial = run(_partial_without_reason)
check("partial bez důvodu je jen varování", not errors(partial))
check("partial bez důvodu se nahlásí", mentions(warnings(partial), "bez uvedeného důvodu"))


# --------------------------------------------------------------------------
# Řízený slovník kategorií (balíček P2-3)
#
# Rozpor uvnitř slovníku i nekanonická publikovaná kategorie jsou chyby.
# Syrové hodnoty jsou povolené pouze u kandidátů a tam se měří varováním.
# --------------------------------------------------------------------------

def _category_without_alias(files):
    files[f"data/weeks/{WEEK_ID}.json"]["events"][0]["categories"] = ["ohňostroj"]


expect_error("publikovaná kategorie bez aliasu", _category_without_alias,
             "není kanonické ID")


def _category_in_review(files):
    files[f"data/weeks/{WEEK_ID}.json"]["events"][0]["categories"] = ["venkovní-akce"]
    files["config/categories.json"]["review"] = [{
        "value": "venkovní-akce",
        "mapped_to": None,
        "reason": "Vlastnost místa konání, ne druh akce.",
        "options": ["Vést jako vlastnost akce.", "Založit kanonickou kategorii."],
        "recommendation": "První varianta.",
    }]


expect_error("sporná publikovaná kategorie", _category_in_review,
             "není kanonické ID")


def _category_diacritics_and_spaces(files):
    """Alias `klasická-hudba` musí pokrýt i zápis s mezerou a bez háčků."""
    files[f"data/weeks/{WEEK_ID}.json"]["events"][0]["categories"] = \
        ["Klasicka hudba", "Rodiny"]


expect_error("alias není platná publikovaná kategorie",
             _category_diacritics_and_spaces, "použij 'hudba'")


def _audience_without_kind(files):
    files[f"data/weeks/{WEEK_ID}.json"]["events"][0]["categories"] = ["rodiny"]


expect_error("akce musí mít druh", _audience_without_kind, "povinné osy 'kind'")


def _duplicate_category(files):
    files[f"data/weeks/{WEEK_ID}.json"]["events"][0]["categories"] = ["hudba", "hudba"]


expect_error("duplicitní kategorie akce", _duplicate_category, "non-unique")


def _raw_candidate_category(files):
    files["research/candidates-test.json"]["candidates"][0]["categories"] = ["ohňostroj"]


raw_candidate = run(_raw_candidate_category)
check("neznámá kategorie kandidáta je jen varování", not errors(raw_candidate))
check("neznámá kategorie kandidáta se změří",
      mentions(warnings(raw_candidate), "nemá alias v config/categories.json"))


def _alias_to_unknown_category(files):
    files["config/categories.json"]["aliases"].append(
        {"alias": "divadlo", "category_id": "neexistujici"})


expect_error("alias na neexistující kategorii", _alias_to_unknown_category,
             "míří na neznámou kategorii")


def _category_with_unknown_axis(files):
    files["config/categories.json"]["categories"][0]["axis"] = "tema"


expect_error("kategorie v neznámé ose", _category_with_unknown_axis,
             "neznámou osu")


def _conflicting_alias(files):
    files["config/categories.json"]["aliases"].append(
        {"alias": "Kultura", "category_id": "rodiny"})


expect_error("týž alias dvakrát jinam", _conflicting_alias,
             "pokaždé jinam")


def _review_disagrees_with_aliases(files):
    """Sporná hodnota tvrdí jedno, mapovací tabulka druhé."""
    files["config/categories.json"]["review"] = [{
        "value": "kultura",
        "mapped_to": None,
        "reason": "Příliš obecná hodnota.",
        "options": ["Nemapovat.", "Ponechat alias na hudba."],
        "recommendation": "Rozhodnout podle dat.",
    }]


expect_error("review neodpovídá mapovací tabulce", _review_disagrees_with_aliases,
             "Obojí musí říkat totéž")


# --------------------------------------------------------------------------

if failures:
    print(f"NEPROŠLO {len(failures)} kontrol:\n")
    for item in failures:
        print(" - " + item)
    raise SystemExit(1)
print("Všechny kontroly prošly.")
