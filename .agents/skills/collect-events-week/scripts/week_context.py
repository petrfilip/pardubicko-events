#!/usr/bin/env python3
"""Převede číslo týdne na jednoznačný kontext pro projektový sběr."""

from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Prague")


def parse_week(value: str, default_year: int) -> tuple[int, int]:
    text = value.strip().upper()
    full = re.fullmatch(r"(\d{4})-?W(\d{1,2})", text)
    if full:
        return int(full.group(1)), int(full.group(2))
    short = re.fullmatch(r"W?(\d{1,2})", text)
    if short:
        return default_year, int(short.group(1))
    raise ValueError("Očekává se číslo týdne (33) nebo ISO týden (2026-W33).")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("week", help="Číslo týdne nebo YYYY-Www.")
    parser.add_argument("--year", type=int,
                        help="Rok pro samotné číslo týdne; jinak aktuální ISO rok.")
    args = parser.parse_args()

    now = datetime.now(TZ)
    default_year = args.year or now.isocalendar().year
    try:
        year, week = parse_week(args.week, default_year)
        monday = date.fromisocalendar(year, week, 1)
    except ValueError as error:
        parser.error(str(error))
    sunday = monday + timedelta(days=6)
    print(json.dumps({
        "week": f"{year}-W{week:02d}",
        "from": monday.isoformat(),
        "to": sunday.isoformat(),
        "timezone": "Europe/Prague",
        "year_inferred": not bool(re.search(r"\d{4}", args.week)),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
