import json
from datetime import datetime

import httpx
import pytest

from app import config, db, notify
from app.fetch import upsert_record
from app.sources.base import Record

TZ = config.TZ


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NTFY_TOPIC", "test-topic")
    monkeypatch.setattr(config, "NTFY_SERVER", "https://ntfy.example")
    monkeypatch.setattr(config, "APP_BASE_URL", "")
    c = db.connect(tmp_path / "t.db")
    upsert_record(c, Record("fi_prospekt", "Exempel AB", "https://pdf", {
        "sub_start": "2026-11-17", "sub_end": "2026-11-21T15:00", "price": 50, "currency": "SEK",
        "market": "Spotlight", "country": "SE"}))
    upsert_record(c, Record("inderes", "Utan Datum", None, {}))  # ingen teckningsperiod
    c.commit()
    return c


def at(day, hour, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=TZ)


def run(conn, now):
    out = []
    notify.run(conn, now, sender=out.append)
    return [(n.kind, n.title) for n in out]


def test_new_once_then_reminders_once(conn):
    assert run(conn, at(18, 9)) == [("new", "Ny IPO: Exempel AB")]
    assert run(conn, at(18, 12)) == []                       # inget dubbelt
    r = run(conn, at(19, 16))                               # 47 h kvar
    assert r == [("48h", "Exempel AB: stänger om 1 d 23 h")]
    assert run(conn, at(19, 20)) == []
    r = run(conn, at(20, 16))                               # 23 h kvar
    assert r == [("24h", "Exempel AB: stänger om 23 h 0 min")]
    assert run(conn, at(21, 14)) == []
    assert run(conn, at(21, 16)) == []                      # stängd


def test_first_seen_late_gets_only_24h(conn):
    r = run(conn, at(20, 18))
    assert [k for k, _ in r] == ["new", "24h"]
    assert run(conn, at(20, 19)) == []                      # 48 h räknas som ersatt


def test_upcoming_window_counts_as_new(conn):
    upsert_record(conn, Record("fi_prospekt", "Snart AB", None, {
        "sub_start": "2026-11-25", "sub_end": "2026-12-02"}))
    out = []
    notify.run(conn, at(18, 9), sender=out.append)
    snart = [n for n in out if n.title == "Ny IPO: Snart AB"]
    assert len(snart) == 1 and snart[0].message.startswith("Teckning ons 25 nov – ons 2 dec (klockslag okänt)")
    assert not any("Utan Datum" in n.title for n in out)  # okänd period = ingen notis


def test_dismissed_and_subscribed(conn):
    db.set_mark(conn, 1, status="ej_intresserad")
    assert run(conn, at(20, 18)) == []
    db.set_mark(conn, 1, status="tecknat")
    assert run(conn, at(20, 18)) == []                      # redan tecknat: inga påminnelser
    db.set_mark(conn, 1, status="intresserad")
    assert [k for k, _ in run(conn, at(20, 18))] == ["new", "24h"]


def test_changed_deadline_rearms_reminders(conn):
    run(conn, at(20, 18))
    # Mäklaren stänger tidigare: egen uppgift
    db.set_fact(conn, 1, "manual", "sub_end", "2026-11-21T09:00")
    assert [k for k, _ in run(conn, at(20, 18))] == ["24h"]


def test_failed_send_is_retried(conn):
    def boom(n):
        raise httpx.ConnectError("nere")
    assert notify.run(conn, at(18, 9), sender=boom) == []
    assert run(conn, at(18, 10)) == [("new", "Ny IPO: Exempel AB")]


def test_disabled_without_topic(conn, monkeypatch):
    monkeypatch.setattr(config, "NTFY_TOPIC", "")
    assert run(conn, at(20, 18)) == []


def test_send_posts_json_to_ntfy(monkeypatch):
    monkeypatch.setattr(config, "NTFY_TOPIC", "mitt-hemliga-amne")
    monkeypatch.setattr(config, "NTFY_SERVER", "https://ntfy.example")
    monkeypatch.setattr(config, "NTFY_TOKEN", "tk_abc")
    seen = {}

    def handler(req):
        seen["url"], seen["auth"], seen["body"] = str(req.url), req.headers.get("authorization"), json.loads(req.content)
        return httpx.Response(200, json={"id": "x"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    notify.send(notify.Notice(1, "24h", "", "Åre Ö AB: stänger om 3 h", "Text", priority=4,
                              tags=("alarm_clock",), click="https://app/#ipo-1"), client)
    assert seen["url"] == "https://ntfy.example/"
    assert seen["auth"] == "Bearer tk_abc"
    assert seen["body"] == {"topic": "mitt-hemliga-amne", "title": "Åre Ö AB: stänger om 3 h",
                            "message": "Text", "priority": 4, "tags": ["alarm_clock"],
                            "click": "https://app/#ipo-1"}


def test_message_is_factual(conn):
    out = []
    notify.run(conn, at(18, 9), sender=out.append)
    msg = out[0].message
    assert "stänger lör 21 nov kl. 15:00" in msg and "Spotlight (Sverige)" in msg
    assert "Pris: 50 SEK" in msg and "Mäklare: okänt – kontrollera Avanza/Nordnet" in msg
