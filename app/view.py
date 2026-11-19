"""Bygger det som visas: väljer värde per fält efter källprioritet och räknar ut
status (kommande / teckning pågår / stängd / noterad) och nedräkning."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from . import config, db
from .normalize import COUNTRY_NAMES

SOURCE_LABELS = {
    "manual": "Egen uppgift",
    "fi_prospekt": "Prospekt (FI)",
    "fi": "Finansinspektionen",
    "inderes": "Inderes",
}

DEFAULT_PRIORITY = ["manual", "fi_prospekt", "inderes", "fi"]
# Inderes uppdateras efter prissättning/notering, prospektet visar förväntade värden
PRIORITY = {
    "name": ["manual", "inderes", "fi", "fi_prospekt"],
    "first_trading_day": ["manual", "inderes", "fi_prospekt"],
    "price": ["manual", "inderes", "fi_prospekt"],
    "price_min": ["manual", "inderes", "fi_prospekt"],
    "price_max": ["manual", "inderes", "fi_prospekt"],
    "currency": ["manual", "inderes", "fi_prospekt"],
}

CLOSING_SOON = timedelta(days=3)
ENDED_AFTER = timedelta(days=10)

AVANZA_IPO_PAGE = "https://www.avanza.se/borsintroduktioner-emissioner.html"
AVANZA_OTHER_PAGE = "https://www.avanza.se/erbjudanden/ovriga-erbjudanden.html"
NORDNET_IPO_PAGE = "https://www.nordnet.se/aktier/ipo"


@dataclass
class Fact:
    value: str
    source: str
    url: str | None

    @property
    def source_label(self) -> str:
        return SOURCE_LABELS.get(self.source, self.source)


@dataclass
class IpoView:
    id: int
    name: str
    facts: dict[str, Fact]
    all_sources: dict[str, str | None] = field(default_factory=dict)
    status: str = "okänd"
    status_label: str = "Okänd status"
    sub_start: datetime | None = None
    sub_end: datetime | None = None
    sub_end_time_known: bool = False
    first_trading_day: date | None = None
    closing_soon: bool = False
    only_fi: bool = False

    def get(self, key: str) -> str | None:
        f = self.facts.get(key)
        return f.value if f else None

    @property
    def past_date(self) -> date | None:
        return self.first_trading_day or (self.sub_end.date() if self.sub_end else None)

    @property
    def open_now(self) -> bool:
        return self.status == "open"

    @property
    def market(self) -> str:
        return self.get("market") or "Marknad okänd"

    @property
    def country(self) -> str | None:
        c = self.get("country")
        return COUNTRY_NAMES.get(c, c) if c else None

    @property
    def price_text(self) -> str | None:
        cur = self.get("currency") or ""
        lo, hi, p = self.get("price_min"), self.get("price_max"), self.get("price")
        if lo and hi:
            return f"{_num(lo)}–{_num(hi)} {cur}".strip()
        if p:
            return f"{_num(p)} {cur}".strip()
        return None

    @property
    def price_fact(self) -> Fact | None:
        return self.facts.get("price_min") or self.facts.get("price")

    @property
    def broker_status(self) -> tuple[str, str]:
        """(kod, text). Steg 2 lägger till egna markeringar; tills dess okänt."""
        avanza = self.get("broker_avanza")
        other = self.get("broker_other")
        if avanza == "ja":
            return "avanza", "Kan teckna via Avanza"
        if other:
            return "other", f"Kan teckna via annan mäklare: {other}"
        return "unknown", "Okänt – kontrollera"


def _num(v: str) -> str:
    f = float(v)
    s = f"{f:,.2f}".replace(",", " ").replace(".", ",")
    return s[:-3] if s.endswith(",00") else s


def _parse_dt(v: str | None, end_of_day: bool) -> tuple[datetime | None, bool]:
    if not v:
        return None, False
    try:
        if "T" in v:
            dt = datetime.fromisoformat(v)
            return (dt if dt.tzinfo else dt.replace(tzinfo=config.TZ)), True
        d = date.fromisoformat(v)
    except ValueError:
        return None, False
    t = time(23, 59) if end_of_day else time(0, 0)
    return datetime.combine(d, t, tzinfo=config.TZ), False


def _pick(facts_rows, key: str) -> Fact | None:
    order = PRIORITY.get(key, DEFAULT_PRIORITY)
    by_source = {r["source"]: r for r in facts_rows if r["field"] == key}
    for s in order:
        if s in by_source:
            r = by_source[s]
            return Fact(r["value"], s, r["url"])
    return None


def build(conn, now: datetime | None = None) -> list[IpoView]:
    now = now or datetime.now(config.TZ)
    today = now.date()
    out: list[IpoView] = []
    for ipo in db.all_ipos(conn):
        rows = db.facts_for(conn, ipo["id"])
        keys = {r["field"] for r in rows}
        facts = {k: f for k in keys if (f := _pick(rows, k))}
        sources: dict[str, str | None] = {}
        for r in rows:
            if r["field"] == "name":
                sources[r["source"]] = r["url"]
        v = IpoView(ipo["id"], (facts.get("name").value if facts.get("name") else ipo["name"]), facts, sources)
        v.only_fi = bool(v.get("only_fi"))
        v.sub_start, _ = _parse_dt(v.get("sub_start"), end_of_day=False)
        v.sub_end, v.sub_end_time_known = _parse_dt(v.get("sub_end"), end_of_day=True)
        ftd, _ = _parse_dt(v.get("first_trading_day"), end_of_day=False)
        v.first_trading_day = ftd.date() if ftd else None

        if v.sub_start and v.sub_end and v.sub_start <= now <= v.sub_end:
            v.status, v.status_label = "open", "Teckning pågår"
            v.closing_soon = v.sub_end - now <= CLOSING_SOON
        elif v.sub_start and now < v.sub_start:
            v.status, v.status_label = "upcoming", "Teckning har inte öppnat"
        elif v.first_trading_day and v.first_trading_day <= today:
            v.status, v.status_label = "listed", "Noterad"
        elif v.sub_end and now > v.sub_end + ENDED_AFTER:
            # Stängd för länge sedan men noteringsdatum saknas i källorna
            v.status, v.status_label = "listed", "Avslutad"
        elif v.sub_end and now > v.sub_end:
            v.status, v.status_label = "closed", "Teckningen stängd"
        else:
            v.status, v.status_label = "upcoming", "Kommande – teckningsperiod okänd"
        out.append(v)
    return out


_ORDER = {"open": 0, "upcoming": 1, "closed": 2, "listed": 3}
FAR = datetime(9999, 1, 1, tzinfo=config.TZ)


def sort_key(v: IpoView):
    when = v.sub_end if v.status == "open" else (
        v.sub_start or (datetime.combine(v.first_trading_day, time(), tzinfo=config.TZ) if v.first_trading_day else None)
    )
    if v.status == "listed":
        # senast noterade först
        return (_ORDER[v.status], -(v.past_date.toordinal() if v.past_date else 0), v.name)
    return (_ORDER[v.status], when or FAR, v.name)


def select(views: list[IpoView], flt: str, today: date) -> list[IpoView]:
    if flt == "open":
        sel = [v for v in views if v.open_now]
    elif flt == "past":
        cutoff = today - timedelta(days=config.PAST_DAYS)
        sel = [v for v in views if v.status == "listed" and v.past_date and v.past_date >= cutoff]
    else:  # aktuella
        sel = [v for v in views if v.status in ("open", "upcoming", "closed")]
    return sorted(sel, key=sort_key)
