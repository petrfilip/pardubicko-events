#!/usr/bin/env python3
"""Spustí úplnou deterministickou testovací sadu projektu.

Testy v tomto projektu jsou samostatné skripty bez testovacího frameworku
(předloha `tools/fb-events/test_parse.py`). Runner najde testy Python/Node a
navíc spustí integrační i HTTP test PHP webu. Chybějící Node nebo PHP je chyba,
nikoli přeskočený test.

Spuštění:

    python3 tools/run_tests.py
    python3 tools/run_tests.py --list
    docker compose run --rm tests
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parent
WEB_TESTS = (
    REPO_ROOT / "web" / "tests" / "test_web.php",
    REPO_ROOT / "web" / "tests" / "run_http_smoke.py",
)


def discover() -> list[Path]:
    """Najde testy nástrojů a přidá integrační testy webu."""
    tool_tests = sorted(
        path for pattern in ("test_*.py", "test_*.mjs")
        for path in TOOLS_DIR.rglob(pattern)
        if "__pycache__" not in path.parts
    )
    return tool_tests + list(WEB_TESTS)


def runner_for(path: Path) -> list[str] | None:
    """Vrátí příkaz ke spuštění, nebo None, když chybí interpret."""
    if path.suffix == ".py":
        return [sys.executable, str(path)]
    if path.suffix == ".mjs":
        node = shutil.which("node")
        return [node, str(path)] if node else None
    if path.suffix == ".php":
        php = shutil.which("php")
        return [php, str(path)] if php else None
    return None


def missing_runtime(path: Path) -> str:
    if path.suffix == ".mjs":
        return "node"
    if path.suffix == ".php":
        return "php"
    return f"interpret pro {path.suffix}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list", action="store_true", help="Jen vypsat nalezené testy.")
    args = parser.parse_args()

    tests = discover()
    if not tests:
        print("Nenalezen žádný test — to je samo o sobě podezřelé.", file=sys.stderr)
        return 1

    if args.list:
        for path in tests:
            print(path.relative_to(REPO_ROOT).as_posix())
        return 0

    commands: list[tuple[Path, list[str]]] = []
    missing: list[tuple[str, str]] = []
    for path in tests:
        command = runner_for(path)
        if command is None:
            missing.append((path.relative_to(REPO_ROOT).as_posix(), missing_runtime(path)))
        else:
            commands.append((path, command))

    if missing:
        print("Testovací sada vyžaduje všechny runtime; nic se nepřeskakuje:",
              file=sys.stderr)
        for relative, runtime in missing:
            print(f" - {relative}: chybí {runtime}", file=sys.stderr)
        return 1

    failed: list[str] = []

    for path, command in commands:
        relative = path.relative_to(REPO_ROOT).as_posix()
        print(f"\n=== {relative} ===", flush=True)
        result = subprocess.run(command, cwd=REPO_ROOT, check=False)
        if result.returncode != 0:
            failed.append(relative)

    print("\n" + "=" * 60)
    if failed:
        print(f"NEPROŠLO {len(failed)} z {len(tests)} sad:")
        for relative in failed:
            print(" - " + relative)
        return 1

    print(f"Prošlo {len(tests)} z {len(tests)} testovacích sad.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
