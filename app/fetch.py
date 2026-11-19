"""Hämtar alla källor, slår ihop dubbletter och sparar i SQLite.

Kör manuellt:  python -m app.fetch
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime

from . import config, db
from .normalize import company_key, same_company
from .sources import fi, inderes
from .sources.base import Record
from .sources.http import PoliteClient

log = logging.getLogger("ipo.fetch")


def find_ipo(conn: sqlite3.Connection, name: str) -> int | None:
    key = company_key(name)
    row = conn.execute("SELECT id FROM ipos WHERE key=?", (key,)).fetchone()
    if row:
        return row["id"]
    for r in db.all_ipos(conn):
        if same_company(r["name"], name):
            return r["id"]
    return None


def upsert_record(conn, rec: Record, create: bool = True) -> int | None:
    ipo_id = find_ipo(conn, rec.name)
    if ipo_id is None:
        if not create:
            return None
        ipo_id = db.get_or_create_ipo(conn, company_key(rec.name), rec.name)
    # Ersätt källans tidigare uppgifter helt, så att borttagna värden försvinner
    conn.execute("DELETE FROM facts WHERE ipo_id=? AND source=?", (ipo_id, rec.source))
    db.set_fact(conn, ipo_id, rec.source, "name", rec.name, rec.url)
    for field, value in rec.fields.items():
        db.set_fact(conn, ipo_id, rec.source, field, value, rec.url)
    return ipo_id


def _run_source(conn, source: str, fn) -> None:
    run = db.start_run(conn, source)
    conn.commit()
    try:
        n, msg = fn()
        db.finish_run(conn, run, True, n, msg)
    except Exception as e:  # en trasig källa får inte stoppa de andra
        log.exception("Källan %s misslyckades", source)
        conn.rollback()
        db.finish_run(conn, run, False, 0, f"{type(e).__name__}: {e}")
    conn.commit()


def fetch_inderes(conn, client, today: date):
    records = inderes.fetch(client, today)
    for rec in records:
        upsert_record(conn, rec)
    return len(records), ""


def _parse_prospectus(conn, client, row: fi.FiRow) -> dict:
    cached = conn.execute(
        "SELECT result_json FROM prospectus_cache WHERE fi_id=?", (row.fi_id,)
    ).fetchone()
    if cached:
        result = json.loads(cached["result_json"])
        if result.pop("_v", None) == fi.PARSER_VERSION:
            return result
    pdf = client.get(row.pdf_url).content
    result = fi.parse_timetable(fi.pdf_to_text(pdf), row.date.year)
    conn.execute(
        "INSERT OR REPLACE INTO prospectus_cache (fi_id, parsed_at, result_json) VALUES (?, ?, ?)",
        (row.fi_id, db.now_iso(), json.dumps({**result, "_v": fi.PARSER_VERSION})),
    )
    return result


def fetch_fi(conn, client, today: date):
    rows = fi.fetch_register(client, today)
    relevant = fi.share_prospectuses(rows, today, config.FI_LOOKBACK_DAYS)
    matched = candidates = skipped = 0
    for row in relevant:
        ipo_id = find_ipo(conn, row.name)
        # "Känd" = finns hos en annan källa än FI (inte bara från en tidigare FI-körning)
        known = ipo_id is not None and conn.execute(
            "SELECT 1 FROM facts WHERE ipo_id=? AND source NOT IN ('fi', 'fi_prospekt') LIMIT 1",
            (ipo_id,),
        ).fetchone() is not None
        parsed = _parse_prospectus(conn, client, row)
        conn.commit()
        is_offer = bool(parsed.get("sub_end")) and not parsed.get("looks_like_rights_issue")
        if not known and not is_offer:
            skipped += 1  # t.ex. avknoppning eller företrädesemission utan IPO
            continue
        base = Record(fi.SOURCE, row.name, row.details_url, {
            "prospectus_url": row.pdf_url,
            "prospectus_page": row.details_url,
            "prospectus_date": row.date.isoformat(),
            "prospectus_type": row.doc_type,
            "only_fi": None if known else 1,
        })
        upsert_record(conn, base)
        fields = {k: v for k, v in parsed.items() if k != "looks_like_rights_issue"}
        upsert_record(conn, Record("fi_prospekt", row.name, row.pdf_url, fields))
        matched += known
        candidates += not known
    return len(relevant), f"{matched} matchade, {candidates} endast i FI, {skipped} hoppades över"


def run_fetch(today: date | None = None) -> None:
    today = today or datetime.now(config.TZ).date()
    with PoliteClient() as client, db.session() as conn:
        _run_source(conn, "inderes", lambda: fetch_inderes(conn, client, today))
        _run_source(conn, "fi", lambda: fetch_fi(conn, client, today))
    log.info("Hämtning klar")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_fetch()
    with db.session() as c:
        for r in db.last_runs(c):
            print(f"{r['source']:8} ok={r['ok']} items={r['items']} {r['message'] or ''}")
