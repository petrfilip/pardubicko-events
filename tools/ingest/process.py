#!/usr/bin/env python3
"""Stáhne a klasifikuje nové položky inboxu."""

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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database", type=Path, default=None)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    connection = db.connect(args.database, create=False)
    results = inbox.process_pending(connection, limit=args.limit)
    payload = [item.__dict__ for item in results]
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for item in results:
            suffix = f" — {item.error}" if item.error else ""
            print(f"#{item.id:<5} {item.state:<16} {item.resolved_kind or '-'}{suffix}")
        print(f"\nZpracováno: {len(results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
