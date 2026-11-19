from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import config, db, view
from app.fetch import upsert_record
from app.sources.base import Record


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(config, "SCHEDULER_ENABLED", False)
    with db.session() as conn:
        upsert_record(conn, Record("inderes", "Exempel", "https://x", {"price": 50, "currency": "SEK"}))
        upsert_record(conn, Record("fi_prospekt", "Exempel AB", "https://pdf", {
            "sub_start": "2026-11-17", "sub_end": "2026-11-21T15:00", "first_trading_day": "2026-11-27"}))
    from app.web import app
    with TestClient(app) as c:
        yield c


def _view():
    with db.session() as conn:
        return view.build(conn, datetime(2026, 11, 19, 12, tzinfo=config.TZ))[0]


def test_quick_status_toggles(client):
    r = client.post("/ipo/1/status", data={"status": "intresserad", "f": "aktuella"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/?f=aktuella#ipo-1"
    assert _view().mark_status == "intresserad"
    client.post("/ipo/1/status", data={"status": "intresserad"})  # samma knapp igen = ta bort
    assert _view().mark_status is None
    assert client.post("/ipo/1/status", data={"status": "hackad"}).status_code == 400


def test_save_marks_broker_and_manual_override(client):
    r = client.post("/ipo/1", data={
        "status": "tecknat", "allotment": "1 200", "sub_price": "49,5", "note": "Tecknat via Avanza",
        "broker_avanza": "ja", "broker_other": "", "sub_end": "2026-11-20T23:59",
        "min_subscription": "100 aktier", "f": "mine",
    }, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/?f=mine#ipo-1"
    v = _view()
    assert v.mark["allotment"] == 1200 and v.mark["sub_price"] == 49.5
    assert v.broker_status == ("avanza", "Kan teckna via Avanza")
    assert v.facts["sub_end"].source == "manual" and v.get("sub_end") == "2026-11-20T23:59"
    assert v.facts["sub_start"].source == "fi_prospekt"  # tomma egna fält påverkar inte
    page = client.get("/?f=mine").text
    assert "1 200 aktier" in page and "Kan teckna via Avanza" in page


def test_other_broker_and_not_avanza(client):
    client.post("/ipo/1", data={"broker_avanza": "nej", "broker_other": "Nordnet"})
    assert _view().broker_status == ("other", "Kan teckna via annan mäklare: Nordnet")
    client.post("/ipo/1", data={"broker_avanza": "nej", "broker_other": ""})
    assert _view().broker_status[0] == "none"


def test_validation_errors_keep_data(client):
    r = client.post("/ipo/1", data={"allotment": "många", "sub_start": "2026-11-20", "sub_end": "2026-11-18T10:00"})
    assert r.status_code == 422
    assert "Ange ett tal" in r.text and "Slutet ligger före starten" in r.text
    assert _view().mark is None  # inget sparat


def test_manual_facts_survive_refetch(client):
    client.post("/ipo/1", data={"broker_avanza": "ja", "price": "48"})
    with db.session() as conn:  # ny hämtning från källorna
        upsert_record(conn, Record("inderes", "Exempel", "https://x", {"price": 52, "currency": "SEK"}))
    v = _view()
    assert v.broker_status[0] == "avanza" and v.price_text == "48 SEK"


def test_dismissed_sorted_last_and_mine_filter(client):
    with db.session() as conn:
        upsert_record(conn, Record("inderes", "Annat Bolag", None, {}))
    client.post("/ipo/1/status", data={"status": "ej_intresserad"})
    with db.session() as conn:
        views = view.build(conn, datetime(2026, 11, 19, 12, tzinfo=config.TZ))
    today = datetime(2026, 11, 19).date()
    assert [v.name for v in view.select(views, "aktuella", today)][-1] == "Exempel"
    assert view.select(views, "mine", today) == []
    assert client.get("/ipo/1").status_code == 200
    assert client.get("/ipo/999").status_code == 404
