"""Finansinspektionens prospektregister.

FI:s data är öppna data: "får fritt bearbetas och vidareutnyttjas" med angiven
källa (FI och datum). Ett anrop med ?year=ÅÅÅÅ ger hela årets lista på en sida.

Vi använder bara aktieprospekt (typ Prospekt / EU-tillväxtprospekt). För nya
sådana laddas PDF:en ned en gång och tidsplanen ("Anmälningsperiod …",
"Första handelsdag …") tolkas med fasta regler – ingen AI.
"""
from __future__ import annotations

import html as htmllib
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from ..normalize import DATE_RE, detect_market, parse_sv_date

BASE = "https://fi.se/sv/vara-register/fis-marknadsregister/prospekt/register/"
SOURCE = "fi"
SHARE_DOC_TYPES = {"Prospekt", "EU-tillväxtprospekt"}
# Höj när tolkningsreglerna ändras, så tolkas cachade prospekt om
PARSER_VERSION = 2


@dataclass
class FiRow:
    date: date
    name: str
    security: str
    doc_type: str
    fi_id: str

    @property
    def details_url(self) -> str:
        return f"{BASE}details?id={self.fi_id}"

    @property
    def pdf_url(self) -> str:
        return f"{BASE}GetFile?id={self.fi_id}"


def _cells(row_html: str) -> list[str]:
    return [
        htmllib.unescape(re.sub(r"<[^>]+>", "", c)).strip()
        for c in re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.S)
    ]


def parse_register(page: str) -> list[FiRow]:
    rows = []
    for r in re.findall(r'<tr class="extended-click-area">(.*?)</tr>', page, re.S):
        c = _cells(r)
        m = re.search(r"details\?id=([\w-]+)", r)
        if len(c) < 4 or not m:
            continue
        try:
            d = date.fromisoformat(c[0])
        except ValueError:
            continue
        rows.append(FiRow(d, c[1], c[2], c[3], m.group(1)))
    return rows


def fetch_register(client, today: date) -> list[FiRow]:
    rows: list[FiRow] = []
    years = {today.year, (today - timedelta(days=60)).year}
    for y in sorted(years):
        rows += parse_register(client.get(f"{BASE}?query=&year={y}").text)
    return rows


def share_prospectuses(rows: list[FiRow], today: date, lookback_days: int) -> list[FiRow]:
    since = today - timedelta(days=lookback_days)
    return [
        r for r in rows
        if r.security == "Aktier" and r.doc_type in SHARE_DOC_TYPES and r.date >= since
    ]


# ---------------------------------------------------------------- prospekt-PDF

_PERIOD_LABEL = re.compile(
    r"(Anmälningsperiod(?:en)?(?: för allmänheten[^\d\n]{0,80}?)?|"
    r"Teckningsperiod(?:en)?|Anmälningstid(?:en)?|Erbjudandeperiod(?:en)?)",
    re.I,
)
_TIME = re.compile(r"(?:kl\.?|klockan)\s*(\d{1,2})[.:](\d{2})", re.I)
_TRADING_LABEL = re.compile(
    r"(?:Förväntad|Beräknad|Planerad)?\s*första\s+(?:handelsdag(?:en)?|dag(?:en)?\s+för\s+handel)"
    r"(?:\s+i\s+[^\n.]{0,60}?aktie[a-z]*)?(?:\s+(?:på|vid)\s+[^\n.]{0,60}?)?(?:\s+(?:förväntas|beräknas)\s+(?:vara|bli))?(?:\s+den)?|"
    r"handeln?\b[^.]{0,80}?(?:beräknas|förväntas)\s+(?:påbörjas|inledas|starta)(?:\s+omkring)?(?:\s+den)?",
    re.I,
)
_PRICE = re.compile(
    r"(\d{1,5}(?:,\d{1,4})?)\s*(SEK|kronor|kr|NOK|DKK|EUR)\s+per\s+(?:stam)?aktie", re.I
)
_PRICE_RANGE = re.compile(
    r"(?:mellan|inom intervallet)\s+(\d{1,5}(?:,\d{1,4})?)\s*(?:SEK|kronor|kr)?\s*(?:och|–|-)\s*"
    r"(\d{1,5}(?:,\d{1,4})?)\s*(SEK|kronor|kr|NOK|DKK|EUR)",
    re.I,
)
# Minsta teckning måste stå i anslutning till anmälan/teckning (annars fångas t.ex.
# bolagsordningens "lägst 15 000 000 aktier")
_MIN_SUB = re.compile(
    r"(?:anmälan|anmälningar|teckning|tecknings?anmälan|ansökan)[^.]{0,80}?"
    r"(?:lägst|minst|minimum)\s+(\d{1,3}(?:[ \u00a0]\d{3})*|\d+)\s+(?:stam)?aktier",
    re.I,
)


def _cur(c: str) -> str:
    return {"kronor": "SEK", "kr": "SEK"}.get(c.lower(), c.upper())


def _num(s: str) -> float:
    return float(s.replace(" ", "").replace(",", "."))


def parse_timetable(text: str, ref_year: int) -> dict[str, object]:
    """Plockar ut teckningsperiod, klockslag, första handelsdag, pris och minsta
    teckning ur prospekttext. Returnerar bara fält som hittades."""
    out: dict[str, object] = {}
    flat = re.sub(r"[ \t]+", " ", text)

    for m in _PERIOD_LABEL.finditer(flat):
        window = flat[m.end() : m.end() + 220]
        dates = list(DATE_RE.finditer(window))
        if len(dates) < 2:
            continue
        # Om startdatumet saknar år ("14 oktober–21 oktober 2026") ärvs slutdatumets år
        end_year = int(dates[1].group(3)) if dates[1].group(3) else ref_year
        start = parse_sv_date(dates[0].group(0), end_year)
        end = parse_sv_date(dates[1].group(0), end_year)
        if not start or not end or end < start:
            continue
        out["sub_start"] = start.isoformat()
        tail = window[dates[1].end() : dates[1].end() + 40]
        t = _TIME.search(tail)
        out["sub_end"] = (
            datetime(end.year, end.month, end.day, int(t.group(1)), int(t.group(2))).isoformat(timespec="minutes")
            if t else end.isoformat()
        )
        out["sub_label"] = re.sub(r"\s+", " ", m.group(0)).strip()[:80]
        break

    for m in _TRADING_LABEL.finditer(flat):
        window = flat[m.end() : m.end() + 160]
        d = DATE_RE.search(window)
        if d:
            parsed = parse_sv_date(d.group(0), ref_year)
            if parsed:
                out["first_trading_day"] = parsed.isoformat()
                break

    rng = _PRICE_RANGE.search(flat)
    if rng:
        out["price_min"], out["price_max"] = _num(rng.group(1)), _num(rng.group(2))
        out["currency"] = _cur(rng.group(3))
    else:
        p = _PRICE.search(flat)
        if p:
            out["price"] = _num(p.group(1))
            out["currency"] = _cur(p.group(2))

    ms = _MIN_SUB.search(flat)
    if ms:
        n = int(re.sub(r"\D", "", ms.group(1)))
        if 0 < n <= 1_000_000:
            out["min_subscription"] = f"{n:,} aktier".replace(",", " ")

    market, country = detect_market(flat)
    if market:
        out["market"] = market
    if country:
        out["country"] = country

    # Emissioner med teckningsrätter i redan noterade bolag är inte IPO:er
    rights = len(re.findall(r"teckningsrätt", flat, re.I))
    ipo_words = len(re.findall(r"upptagande till handel|börsnotering|notering(?:en)? av", flat, re.I))
    out["looks_like_rights_issue"] = rights >= 10 and rights > ipo_words
    return out


def pdf_to_text(pdf_bytes: bytes, max_pages: int = 90) -> str:
    """Kräver pdftotext (poppler-utils)."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "p.pdf"
        p.write_bytes(pdf_bytes)
        res = subprocess.run(
            ["pdftotext", "-l", str(max_pages), "-enc", "UTF-8", str(p), "-"],
            capture_output=True, timeout=120,
        )
        return res.stdout.decode("utf-8", errors="replace")

