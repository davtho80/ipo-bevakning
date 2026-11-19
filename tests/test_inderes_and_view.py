import json
from datetime import date, datetime

from app import db, view
from app.config import TZ
from app.fetch import upsert_record
from app.sources import inderes
from app.sources.base import Record


def _page(upcoming, latest):
    data = {"props": {"pageProps": {"payload": {"data": {"ipos": {"upcoming": upcoming, "latest": latest}}}}}}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></html>'


def test_inderes_parse():
    html = _page(
        [{"id": "1", "tradingStartDate": None, "market": None, "subscriptionPrice": None,
          "subscriptionPriceMin": None, "subscriptionPriceMax": None, "subscriptionPriceCurrency": None,
          "company": {"name": "Salvos", "slug": "Salvos"}}],
        [{"id": "2", "tradingStartDate": "2026-10-23", "market": None, "subscriptionPrice": 31,
          "subscriptionPriceMin": None, "subscriptionPriceMax": None, "subscriptionPriceCurrency": "SEK",
          "company": {"name": "Prisjakt", "slug": "Prisjakt"}}],
    )
    recs = inderes.parse(html, date(2026, 11, 19))
    assert [r.name for r in recs] == ["Salvos", "Prisjakt"]
    assert recs[0].fields == {"inderes_group": "upcoming"}
    assert recs[1].fields["country"] == "SE" and recs[1].url.endswith("/companies/Prisjakt")


def test_merge_and_status(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    upsert_record(conn, Record("inderes", "Exempel", "https://x", {"price": 50, "currency": "SEK"}))
    upsert_record(conn, Record("fi_prospekt", "Exempel AB (publ)", "https://pdf", {
        "sub_start": "2026-11-17", "sub_end": "2026-11-21T15:00", "price_min": 45, "price_max": 55,
        "first_trading_day": "2026-11-27", "market": "Spotlight"}))
    upsert_record(conn, Record("inderes", "Kommande", None, {}))
    assert conn.execute("SELECT COUNT(*) FROM ipos").fetchone()[0] == 2  # dubblett sammanslagen

    now = datetime(2026, 11, 19, 12, 0, tzinfo=TZ)
    views = {v.name: v for v in view.build(conn, now)}
    ex = views["Exempel"]
    assert ex.status == "open" and ex.closing_soon
    assert ex.facts["price"].source == "inderes"  # Inderes före prospekt för pris
    assert ex.price_text == "45–55 SEK"
    assert ex.market == "Spotlight"
    assert ex.broker_status[0] == "unknown"
    assert views["Kommande"].status == "upcoming"
    assert [v.name for v in view.select(list(views.values()), "open", now.date())] == ["Exempel"]

    later = datetime(2026, 11, 28, 9, 0, tzinfo=TZ)
    assert {v.name: v for v in view.build(conn, later)}["Exempel"].status == "listed"


def test_fi_only_flag_survives_refetch(tmp_path, monkeypatch):
    from app import fetch
    from app.sources import fi

    row = fi.FiRow(date(2026, 11, 10), "Ny AB (publ)", "Aktier", "Prospekt", "26-9")
    monkeypatch.setattr(fi, "fetch_register", lambda client, today: [row])
    monkeypatch.setattr(fetch, "_parse_prospectus", lambda conn, client, r: {
        "sub_start": "2026-11-18", "sub_end": "2026-11-25", "looks_like_rights_issue": False})
    conn = db.connect(tmp_path / "t.db")
    for _ in range(2):
        fetch.fetch_fi(conn, None, date(2026, 11, 19))
    v = view.build(conn, datetime(2026, 11, 19, 12, tzinfo=TZ))[0]
    assert v.only_fi and v.status == "open"
