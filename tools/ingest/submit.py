#!/usr/bin/env python3
"""Vloží URL do inboxu bez stahování (ADR 0005)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PIPELINE_DIR = HERE.parent / "pipeline"
for directory in (HERE, PIPELINE_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import db  # noqa: E402
import inbox  # noqa: E402
from urlnorm import InvalidUrl  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url")
    parser.add_argument("--note")
    parser.add_argument("--database", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    connection = db.connect(args.database)
    try:
        result = inbox.submit(connection, args.url, note=args.note)
    except InvalidUrl as exc:
        parser.error(str(exc))

    payload = {
        "id": result.id, "state": result.state,
        "duplicate": result.duplicate, "url_norm": result.url_norm,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        duplicate = " (duplicita)" if result.duplicate else ""
        print(f"Inbox #{result.id}: {result.state}{duplicate}\n{result.url_norm}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
