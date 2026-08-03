"""Serializace JSON ve stylu tohoto repozitáře.

Publikovaná data jsou ručně čitelná a jejich diff se čte při každé revizi.
Export proto musí zachovat i formátování, ne jen obsah — jinak by se každý
běh pipeline projevil jako přepsání všech souborů.

Styl, který repozitář používá:

* odsazení dvěma mezerami,
* diakritika doslovně, nikoli `\\uXXXX`,
* objekty a pole objektů rozepsané na řádky,
* vybraná krátká pole a objekty na jednom řádku (`categories`, `price`,
  `source`),
* soubor končí odřádkováním.
"""

from __future__ import annotations

import json
from typing import Any

SCALARS = (str, int, float, bool, type(None))


def dumps(value: Any, *, inline_keys: frozenset[str] = frozenset(), level: int = 0) -> str:
    """Serializuje hodnotu. Klíče v `inline_keys` vypíše na jeden řádek."""
    pad, inner = "  " * level, "  " * (level + 1)

    if isinstance(value, dict):
        if not value:
            return "{}"
        rows = []
        for key, item in value.items():
            rendered = (
                _inline(item) if key in inline_keys
                else dumps(item, inline_keys=inline_keys, level=level + 1)
            )
            rows.append(f"{inner}{json.dumps(key, ensure_ascii=False)}: {rendered}")
        return "{\n" + ",\n".join(rows) + "\n" + pad + "}"

    if isinstance(value, list):
        if not value:
            return "[]"
        if all(isinstance(item, SCALARS) for item in value):
            return _inline(value)
        rows = [inner + dumps(item, inline_keys=inline_keys, level=level + 1)
                for item in value]
        return "[\n" + ",\n".join(rows) + "\n" + pad + "]"

    return json.dumps(value, ensure_ascii=False)


def _inline(value: Any) -> str:
    """Jeden řádek, mezera za dvojtečkou i za čárkou."""
    if isinstance(value, dict):
        body = ", ".join(
            f"{json.dumps(key, ensure_ascii=False)}: {_inline(item)}"
            for key, item in value.items()
        )
        return "{" + body + "}"
    if isinstance(value, list):
        return "[" + ", ".join(_inline(item) for item in value) + "]"
    return json.dumps(value, ensure_ascii=False)


def dump_file(path, value: Any, *, inline_keys: frozenset[str] = frozenset()) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(value, inline_keys=inline_keys) + "\n", encoding="utf-8")


# Klíče, které se v týdenních souborech vypisují na jeden řádek.
WEEK_INLINE_KEYS = frozenset({"categories", "price", "source"})
