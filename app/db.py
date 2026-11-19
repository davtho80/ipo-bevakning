"""SQLite-lagring.

Varje källa sparar sina uppgifter som "fakta" (ipo, källa, fält, värde, url).
Det visade värdet väljs per fält efter källprioritet, så att det alltid går att
visa var en uppgift kommer ifrån och så att manuella uppgifter vinner.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from . import config

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS ipos (
    id          INTEGER PRIMARY KEY,
    key         TEXT UNIQUE NOT NULL,      -- normaliserat bolagsnamn
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS facts (
    ipo_id      INTEGER NOT NULL REFERENCES ipos(id) ON DELETE CASCADE,
    source      TEXT NOT NULL,             -- inderes | fi | fi_prospekt | manual
    field       TEXT NOT NULL,
    value       TEXT,
    url         TEXT,
    fetched_at  TEXT NOT NULL,
    PRIMARY KEY (ipo_id, source, field)
);
CREATE TABLE IF NOT EXISTS fetch_runs (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    ok          INTEGER,
    items       INTEGER,
    message     TEXT
);
-- Dina egna markeringar (påverkas aldrig av hämtningen)
CREATE TABLE IF NOT EXISTS marks (
    ipo_id      INTEGER PRIMARY KEY REFERENCES ipos(id) ON DELETE CASCADE,
    status      TEXT CHECK (status IN ('intresserad', 'tecknat', 'ej_intresserad')),
    allotment   INTEGER,                   -- tilldelade aktier
    sub_price   REAL,                      -- teckningskurs per aktie
    note        TEXT,
    updated_at  TEXT NOT NULL
);
-- Cache så att samma prospekt-PDF bara laddas ned och tolkas en gång
CREATE TABLE IF NOT EXISTS prospectus_cache (
    fi_id       TEXT PRIMARY KEY,
    parsed_at   TEXT NOT NULL,
    result_json TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(config.TZ).isoformat(timespec="seconds")


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = Path(path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def session(path: Path | None = None):
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def get_or_create_ipo(conn: sqlite3.Connection, key: str, name: str) -> int:
    row = conn.execute("SELECT id FROM ipos WHERE key = ?", (key,)).fetchone()
    if row:
        return row["id"]
    ts = now_iso()
    cur = conn.execute(
        "INSERT INTO ipos (key, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (key, name, ts, ts),
    )
    return cur.lastrowid


def set_fact(conn, ipo_id: int, source: str, field: str, value, url: str | None = None) -> None:
    if value is None or value == "":
        conn.execute(
            "DELETE FROM facts WHERE ipo_id=? AND source=? AND field=?", (ipo_id, source, field)
        )
        return
    conn.execute(
        """INSERT INTO facts (ipo_id, source, field, value, url, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(ipo_id, source, field) DO UPDATE SET
             value=excluded.value, url=excluded.url, fetched_at=excluded.fetched_at""",
        (ipo_id, source, field, str(value), url, now_iso()),
    )
    conn.execute("UPDATE ipos SET updated_at=? WHERE id=?", (now_iso(), ipo_id))


def all_ipos(conn) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM ipos ORDER BY id").fetchall()


def facts_for(conn, ipo_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM facts WHERE ipo_id=?", (ipo_id,)).fetchall()


def start_run(conn, source: str) -> int:
    return conn.execute(
        "INSERT INTO fetch_runs (source, started_at) VALUES (?, ?)", (source, now_iso())
    ).lastrowid


def finish_run(conn, run_id: int, ok: bool, items: int, message: str = "") -> None:
    conn.execute(
        "UPDATE fetch_runs SET finished_at=?, ok=?, items=?, message=? WHERE id=?",
        (now_iso(), int(ok), items, message[:2000], run_id),
    )


def last_runs(conn) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT r.* FROM fetch_runs r
           JOIN (SELECT source, MAX(id) AS id FROM fetch_runs GROUP BY source) m ON m.id = r.id
           ORDER BY r.source"""
    ).fetchall()


MARK_STATUSES = {
    "intresserad": "Intresserad",
    "tecknat": "Tecknat",
    "ej_intresserad": "Ej intresserad",
}


def get_mark(conn, ipo_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM marks WHERE ipo_id=?", (ipo_id,)).fetchone()


def set_mark(conn, ipo_id: int, **values) -> None:
    """Uppdaterar bara de fält som skickas in (None tömmer fältet)."""
    allowed = {"status", "allotment", "sub_price", "note"}
    bad = set(values) - allowed
    if bad:
        raise ValueError(f"Okända fält: {bad}")
    if values.get("status") not in (None, *MARK_STATUSES):
        raise ValueError("Ogiltig status")
    conn.execute(
        "INSERT INTO marks (ipo_id, updated_at) VALUES (?, ?) ON CONFLICT(ipo_id) DO NOTHING",
        (ipo_id, now_iso()),
    )
    for k, v in values.items():
        conn.execute(f"UPDATE marks SET {k}=?, updated_at=? WHERE ipo_id=?", (v, now_iso(), ipo_id))
