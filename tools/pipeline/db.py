"""Přístup k provozní databázi fáze 2.

Databáze je odvozený artefakt. Smí být kdykoli smazána a znovu postavena
importem z repozitáře; nic, co v ní vznikne, nesmí být nenahraditelné.

Pisatelem je vždy jen jeden proces (pipeline). WAL a `busy_timeout` jsou
nastavené proto, aby čtenáři — například webová vrstva podle ADR 0002 —
nebyli zápisem blokováni.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = REPO_ROOT / "var" / "pardubicko.db"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

SCHEMA_VERSION = 5


def connect(path: Path | None = None, *, create: bool = True) -> sqlite3.Connection:
    """Otevře databázi a zajistí, že je schéma aktuální."""
    target = Path(path) if path else DEFAULT_DB_PATH
    if create:
        target.parent.mkdir(parents=True, exist_ok=True)
    elif not target.exists():
        raise FileNotFoundError(f"Databáze {target} neexistuje.")

    connection = sqlite3.connect(target)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    # WAL se u :memory: nastavit nedá a není potřeba.
    if str(target) != ":memory:":
        connection.execute("PRAGMA journal_mode = WAL")

    apply_schema(connection)
    return connection


def apply_schema(connection: sqlite3.Connection) -> None:
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    connection.executescript(sql)

    # `CREATE TABLE IF NOT EXISTS` nepřidá sloupce do databáze vytvořené
    # starší verzí schématu. P1-2 potřebuje URL a Last-Modified po jednotlivých
    # požadavcích, jinak nelze bezpečně dělat podmíněné dotazy u stránkovaného
    # zdroje. Migrace je idempotentní a zachovává provozní historii.
    source_fetch_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(source_fetch)")
    }
    for name, declaration in (
        ("url", "TEXT"),
        ("last_modified", "TEXT"),
    ):
        if name not in source_fetch_columns:
            connection.execute(
                f"ALTER TABLE source_fetch ADD COLUMN {name} {declaration}")

    # P2-3 zrcadlí do tabulky category také osu, pořadí a popis.
    # Na starší databázi se sloupce doplní bez zahození health/inbox dat;
    # nejbližší import pak naplní jejich hodnoty z konfigurace v gitu.
    category_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(category)")
    }
    for name, declaration in (
        ("axis", "TEXT"),
        ("sort_order", "INTEGER"),
        ("description", "TEXT"),
    ):
        if name not in category_columns:
            connection.execute(
                f"ALTER TABLE category ADD COLUMN {name} {declaration}")

    # První vývojová verze WP3 tabulky ještě nerozlišovala existující
    # kandidát se změněným payloadem. Migrace zachová lokální historii běhů.
    source_run_columns = {
        row["name"] for row in connection.execute(
            "PRAGMA table_info(pipeline_source_run)")
    }
    if "candidates_updated" not in source_run_columns:
        connection.execute(
            "ALTER TABLE pipeline_source_run ADD COLUMN "
            "candidates_updated INTEGER NOT NULL DEFAULT 0")

    applied = connection.execute(
        "SELECT version FROM schema_migration WHERE version = ?", (SCHEMA_VERSION,)
    ).fetchone()
    if applied is None:
        connection.execute(
            "INSERT INTO schema_migration (version, applied_at) VALUES (?, ?)",
            (SCHEMA_VERSION, datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
    connection.commit()


def reset(connection: sqlite3.Connection) -> None:
    """Vyprázdní obsahové tabulky, schéma ponechá.

    Používá import, aby byl opakovatelný. Provozní stav sběru
    (`source_fetch`, `source_extract`, `source_health`) a `inbox`
    se nemažou — ty v repozitáři svůj protějšek nemají a import
    by je zahodil bez náhrady.
    """
    tables = (
        "event_fts", "event_week", "event_category", "event_source", "match_review",
        "event", "week", "candidate", "facebook_page", "source",
        "municipality_alias", "municipality", "category_alias", "category",
        "repo_meta",
    )
    connection.execute("PRAGMA foreign_keys = OFF")
    for table in tables:
        connection.execute(f"DELETE FROM {table}")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.commit()


def set_meta(connection: sqlite3.Connection, key: str, value: str | None) -> None:
    connection.execute(
        "INSERT INTO repo_meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def get_meta(connection: sqlite3.Connection, key: str) -> str | None:
    row = connection.execute(
        "SELECT value FROM repo_meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None
