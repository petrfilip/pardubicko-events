#!/usr/bin/env python3
"""Deterministická kontrola dat repozitáře Pardubicko Events.

Nástroj nesahá na síť a nic neopravuje. Pouze čte repozitář a hlásí nálezy.
Kontrola dostupnosti odkazů je oddělená v `linkcheck.py`, protože síťová
chyba nesmí blokovat commit.

Spuštění:

    python3 tools/validate/validate.py              # vše
    python3 tools/validate/validate.py --strict     # varování se počítají jako chyby
    python3 tools/validate/validate.py --only weeks # jen jedna skupina
    python3 tools/validate/validate.py --json       # strojově čitelný výstup

Návratový kód je 0 bez chyb, 1 s chybami.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from checks import ERROR, WARNING, Finding, Repo, run_all  # noqa: E402

try:
    from jsonschema import Draft202012Validator
except ImportError:  # pragma: no cover - závisí na prostředí
    print(
        "Chybí závislost jsonschema.\n"
        "  pip install -r tools/validate/requirements.txt\n"
        "  nebo: docker compose run --rm validate",
        file=sys.stderr,
    )
    raise SystemExit(2)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"

# skupina -> (glob, schéma)
GROUPS: dict[str, tuple[str, str]] = {
    "manifest": ("data/manifest.json", "manifest.schema.json"),
    "weeks": ("data/weeks/*.json", "week.schema.json"),
    "candidates": ("research/candidates*.json", "candidates.schema.json"),
    "registry": ("config/source-registry.json", "source-registry.schema.json"),
    "facebook": ("config/facebook-sources.json", "facebook-sources.schema.json"),
    "categories": ("config/categories.json", "categories.schema.json"),
    "municipalities": ("config/municipalities.json", "municipalities.schema.json"),
    "municipality-aliases": (
        "config/municipality-aliases.json", "municipality-aliases.schema.json"),
    "reports": ("stats/runs/**/*.json", "run-report.schema.json"),
}


def load_schema(name: str) -> Draft202012Validator:
    with (SCHEMA_DIR / name).open(encoding="utf-8") as handle:
        return Draft202012Validator(json.load(handle))


def pointer(error) -> str:
    parts = [str(part) for part in error.absolute_path]
    return "/".join(parts) if parts else ""


def validate_group(root: Path, group: str) -> tuple[list[Finding], dict[str, dict]]:
    pattern, schema_name = GROUPS[group]
    validator = load_schema(schema_name)
    findings: list[Finding] = []
    loaded: dict[str, dict] = {}

    for path in sorted(root.glob(pattern)):
        relative = path.relative_to(root).as_posix()
        try:
            with path.open(encoding="utf-8") as handle:
                data = json.load(handle)
        except json.JSONDecodeError as error:
            findings.append(Finding(
                ERROR, relative, f"řádek {error.lineno}", f"Neplatný JSON: {error.msg}."))
            continue
        except OSError as error:
            findings.append(Finding(ERROR, relative, "", f"Soubor nelze číst: {error}."))
            continue

        loaded[relative] = data
        for error in sorted(validator.iter_errors(data), key=lambda item: list(item.absolute_path)):
            findings.append(Finding(ERROR, relative, pointer(error), error.message))

    return findings, loaded


def build_repo(root: Path, loaded: dict[str, dict[str, dict]], load_errors: list[Finding]) -> Repo:
    repo = Repo(root=root, load_errors=load_errors)
    repo.manifest = loaded.get("manifest", {}).get("data/manifest.json")
    repo.registry = loaded.get("registry", {}).get("config/source-registry.json")
    repo.facebook_sources = loaded.get("facebook", {}).get("config/facebook-sources.json")
    repo.categories = loaded.get("categories", {}).get("config/categories.json")
    repo.weeks = {
        Path(name).name: data for name, data in loaded.get("weeks", {}).items()
    }
    repo.candidates = {
        Path(name).name: data for name, data in loaded.get("candidates", {}).items()
    }
    repo.reports = dict(loaded.get("reports", {}))
    return repo


def collect(root: Path, groups: list[str], run_semantic: bool) -> list[Finding]:
    schema_findings: list[Finding] = []
    loaded: dict[str, dict[str, dict]] = {}

    for group in groups:
        findings, data = validate_group(root, group)
        schema_findings.extend(findings)
        loaded[group] = data

    if not run_semantic:
        return schema_findings

    repo = build_repo(root, loaded, [])
    return schema_findings + run_all(repo)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--only", choices=sorted(GROUPS), action="append",
        help="Zkontrolovat jen vybranou skupinu. Lze uvést vícekrát.",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Varování se počítají jako chyby.",
    )
    parser.add_argument("--json", action="store_true", help="Strojově čitelný výstup.")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="Kořen repozitáře.")
    args = parser.parse_args()

    groups = args.only or sorted(GROUPS)
    # Sémantické kontroly potřebují úplný obraz repozitáře.
    findings = collect(args.root, groups, run_semantic=args.only is None)

    errors = [item for item in findings if item.level == ERROR]
    warnings = [item for item in findings if item.level == WARNING]

    if args.json:
        print(json.dumps(
            {
                "errors": len(errors),
                "warnings": len(warnings),
                "findings": [vars(item) for item in findings],
            },
            ensure_ascii=False, indent=2,
        ))
    else:
        for item in errors + warnings:
            print(item)
        if not findings:
            print("Bez nálezů.")
        print(f"\nChyby: {len(errors)}   Varování: {len(warnings)}")
        if warnings and not args.strict:
            print("Varování nezpůsobí selhání. Pro přísný režim použij --strict.")

    return 1 if errors or (args.strict and warnings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
