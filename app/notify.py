"""Push-notiser via ntfy (https://ntfy.sh).

Tre sorters notiser:
- new  – en IPO med känd teckningsperiod som inte har stängt dyker upp (eller får
         sin teckningsperiod senare), dvs. något du kan teckna i nu eller snart
- 48h  – teckningen stänger inom 48 timmar
- 24h  – teckningen stänger inom 24 timmar

Inga notiser för IPO:er du markerat "Ej intresserad"; inga påminnelser för dem
du markerat "Tecknat". Varje notis skickas en gång (tabellen notifications).
Ändras sista teckningstiden (t.ex. mäklarens egen tid) skickas påminnelserna igen.
Texten är bara fakta och datum – inga råd.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx

from . import config, db, view

log = logging.getLogger("ipo.notify")

REMINDERS = [("24h", timedelta(hours=24)), ("48h", timedelta(hours=48))]  # minsta först
_MON = ["jan", "feb", "mar", "apr", "maj", "jun", "jul", "aug", "sep", "okt", "nov", "dec"]
_DAY = ["mån", "tis", "ons", "tors", "fre", "lör", "sön"]


@dataclass
class Notice:
    ipo_id: int
    kind: str
    deadline: str
    title: str
    message: str
    priority: int = 3
    tags: tuple[str, ...] = ()
    click: str | None = None


def _day(d) -> str:
    return f"{_DAY[d.weekday()]} {d.day} {_MON[d.month - 1]}"


def _when(dt: datetime, time_known: bool) -> str:
    return f"{_day(dt)} kl. {dt:%H:%M}" if time_known else f"{_day(dt)} (klockslag okänt)"


def _left(td: timedelta) -> str:
    mins = max(0, int(td.total_seconds() // 60))
    d, rem = divmod(mins, 1440)
    h, m = divmod(rem, 60)
    if d:
        return f"{d} d {h} h"
    return f"{h} h {m} min" if h else f"{m} min"


def _broker_line(v: view.IpoView) -> str:
    code, text = v.broker_status
    return "Mäklare: okänt – kontrollera Avanza/Nordnet" if code == "unknown" else text


def _click(v: view.IpoView) -> str | None:
    return f"{config.APP_BASE_URL}/?f=aktuella#ipo-{v.id}" if config.APP_BASE_URL else None


def _facts_lines(v: view.IpoView) -> list[str]:
    lines = [f"Marknad: {v.market}" + (f" ({v.country})" if v.country else "")]
    if v.price_text:
        lines.append(f"Pris: {v.price_text}")
    if v.get("min_subscription"):
        lines.append(f"Minsta teckning: {v.get('min_subscription')}")
    if v.first_trading_day:
        lines.append(f"Första handelsdag: {_day(v.first_trading_day)}")
    lines.append(_broker_line(v))
    return lines


def plan(views: list[view.IpoView], now: datetime, sent: set[tuple[int, str, str]]) -> list[Notice]:
    """Vilka notiser ska skickas nu? Ren funktion – lätt att testa."""
    out: list[Notice] = []
    for v in views:
        if v.dismissed or not v.sub_end or v.sub_end <= now:
            continue
        if v.status not in ("open", "upcoming"):
            continue
        deadline = v.sub_end.isoformat()
        end_txt = _when(v.sub_end, v.sub_end_time_known)

        # Ny IPO som går att teckna i (en gång per IPO, oavsett senare ändrad tid)
        if not any(k[0] == v.id and k[1] == "new" for k in sent) and v.mark_status != "tecknat":
            if v.open_now:
                period = f"Teckning pågår, stänger {end_txt}"
            else:
                period = f"Teckning {_day(v.sub_start)} – {end_txt}" if v.sub_start \
                    else f"Teckning stänger {end_txt}"
            out.append(Notice(
                v.id, "new", "", f"Ny IPO: {v.name}",
                "\n".join([period, *_facts_lines(v)]),
                priority=3, tags=("new",), click=_click(v),
            ))

        # Påminnelser innan stängning (inte för det du redan tecknat)
        if v.open_now and v.mark_status != "tecknat":
            left = v.sub_end - now
            due = [(k, lim) for k, lim in REMINDERS if left <= lim]
            if due:
                kind = due[0][0]  # den snävaste som gäller; äldre blir överflödiga
                if (v.id, kind, deadline) not in sent:
                    out.append(Notice(
                        v.id, kind, deadline,
                        f"{v.name}: stänger om {_left(left)}",
                        "\n".join([
                            f"Teckningen stänger {end_txt}.",
                            "Mäklare kan stänga sin teckning tidigare än så.",
                            _broker_line(v),
                        ]),
                        priority=4 if kind == "24h" else 3,
                        tags=("alarm_clock",), click=_click(v),
                    ))
    return out


def send(notice: Notice, client: httpx.Client | None = None) -> None:
    """Publicerar via ntfy:s JSON-API (klarar å, ä, ö i rubriken)."""
    if not config.NTFY_TOPIC:
        raise RuntimeError("NTFY_TOPIC saknas i .env")
    body = {
        "topic": config.NTFY_TOPIC,
        "title": notice.title,
        "message": notice.message,
        "priority": notice.priority,
        "tags": list(notice.tags),
    }
    if notice.click:
        body["click"] = notice.click
    headers = {"Authorization": f"Bearer {config.NTFY_TOKEN}"} if config.NTFY_TOKEN else {}
    own = client is None
    client = client or httpx.Client(timeout=20)
    try:
        r = client.post(config.NTFY_SERVER + "/", json=body, headers=headers)
        r.raise_for_status()
    finally:
        if own:
            client.close()


def run(conn, now: datetime | None = None, sender=send) -> list[Notice]:
    """Skickar det som är på tur och bokför det. Misslyckade notiser bokförs inte,
    så de försöks igen vid nästa körning."""
    now = now or datetime.now(config.TZ)
    if not config.NTFY_TOPIC:
        log.info("Notiser avstängda (NTFY_TOPIC saknas)")
        return []
    views = view.build(conn, now)
    sent = db.sent_keys(conn)
    delivered = []
    for n in plan(views, now, sent):
        try:
            sender(n)
        except Exception as e:
            log.warning("Kunde inte skicka notis %s för %s: %s", n.kind, n.ipo_id, e)
            continue
        db.record_sent(conn, n.ipo_id, n.kind, n.deadline, n.title)
        # En 24 h-påminnelse gör en ej skickad 48 h-påminnelse överflödig
        if n.kind == "24h":
            db.record_sent(conn, n.ipo_id, "48h", n.deadline, "(ersatt av 24 h)")
        conn.commit()
        delivered.append(n)
        log.info("Notis skickad: %s", n.title)
    return delivered


def send_test() -> None:
    send(Notice(0, "test", "", "IPO-bevakning: testnotis",
                "Notiserna fungerar. Du får besked om nya IPO:er och 48 h / 24 h innan teckningen stänger.",
                tags=("white_check_mark",)))
