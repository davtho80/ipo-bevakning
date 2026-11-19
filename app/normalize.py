"""Normalisering av bolagsnamn (för att slå ihop dubbletter) och svenska datum."""
from __future__ import annotations

import re
import unicodedata
from datetime import date

# Bolagsformer och liknande som inte hjälper till att identifiera bolaget
_STOP = {
    "ab", "publ", "aktiebolag", "aktiebolaget", "asa", "as", "a/s", "oyj", "abp", "oy",
    "plc", "ltd", "limited", "nv", "se", "sa", "hf", "the",
}


_GENERIC = {
    "corporation", "corp", "group", "gruppen", "holding", "holdings", "koncern", "company",
    "och", "co", "international", "industri", "industrier", "industries",
}


def name_tokens(name: str) -> list[str]:
    s = unicodedata.normalize("NFKC", name or "").lower()
    s = s.replace("&", " och ")
    s = re.sub(r"\(publ\)", " ", s)
    s = re.sub(r"[^\w/]+", " ", s)  # behåll å, ä, ö m.fl. (\w är unicode)
    return [t for t in s.split() if t not in _STOP]


def company_key(name: str) -> str:
    return " ".join(name_tokens(name))


def _contains_run(longer: list[str], shorter: list[str]) -> bool:
    n = len(shorter)
    return any(longer[i : i + n] == shorter for i in range(len(longer) - n + 1))


def same_company(a: str, b: str) -> bool:
    """Samma bolag om de normaliserade namnen är lika, eller om det kortare namnet
    förekommer som en sammanhängande följd ord i det längre (t.ex. "Dormy" och
    "Dormy Corporation AB", "Public Property Invest" och "PPI Public Property Invest AB").
    Ett ensamt kort ord räcker inte, för att undvika falska träffar."""
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    shorter, longer = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if len(" ".join(shorter)) < 5:
        return False
    if len(shorter) == 1:
        # Ett ord räcker bara om resten av det längre namnet är generiskt
        # ("Dormy" = "Dormy Corporation", men inte "Nordic" = "Nordic Paper")
        return longer[0] == shorter[0] and all(t in _GENERIC for t in longer[1:])
    return _contains_run(longer, shorter)


MONTHS = {
    "januari": 1, "februari": 2, "mars": 3, "april": 4, "maj": 5, "juni": 6, "juli": 7,
    "augusti": 8, "september": 9, "oktober": 10, "november": 11, "december": 12,
    # engelska (en del prospekt är på engelska)
    "january": 1, "february": 2, "march": 3, "may": 5, "june": 6, "july": 7,
    "august": 8, "october": 10,
}
_MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))
DATE_RE = re.compile(rf"(\d{{1,2}})\s+({_MONTH_RE})(?:\s+(\d{{4}}))?", re.I)


def parse_sv_date(text: str, default_year: int | None = None) -> date | None:
    m = DATE_RE.search(text or "")
    if not m:
        return None
    year = int(m.group(3)) if m.group(3) else default_year
    if not year:
        return None
    try:
        return date(year, MONTHS[m.group(2).lower()], int(m.group(1)))
    except ValueError:
        return None


COUNTRY_BY_CURRENCY = {"SEK": "SE", "NOK": "NO", "DKK": "DK", "EUR": "FI", "ISK": "IS"}
COUNTRY_NAMES = {"SE": "Sverige", "NO": "Norge", "DK": "Danmark", "FI": "Finland", "IS": "Island"}

# Marknadsnamn som de skrivs i prospekt/källor -> visningsnamn och land
MARKETS = [
    (r"(?:Nasdaq )?First North Premier(?: Growth Market)?", "First North Premier", None),
    (r"(?:Nasdaq )?First North(?: Growth Market)?(?: Sweden| Stockholm)?", "First North", None),
    (r"Nasdaq Stockholm", "Nasdaq Stockholm", "SE"),
    (r"Spotlight(?: Stock Market)?", "Spotlight", "SE"),
    (r"Nordic Growth Market|NGM Nordic SME|\bNGM\b", "NGM", "SE"),
    (r"Euronext Growth Oslo", "Euronext Growth Oslo", "NO"),
    (r"Oslo B[øo]rs|Euronext Oslo", "Oslo Børs", "NO"),
    (r"Nasdaq Copenhagen|Nasdaq København", "Nasdaq Köpenhamn", "DK"),
    (r"Nasdaq Helsinki", "Nasdaq Helsingfors", "FI"),
]


def detect_market(text: str) -> tuple[str | None, str | None]:
    """Mest omnämnda marknad i en text (prospekt)."""
    best, best_n = None, 0
    text = text or ""
    premier = len(re.findall(MARKETS[0][0], text, re.I))
    for pattern, label, country in MARKETS:
        n = len(re.findall(pattern, text, re.I))
        if label == "First North":
            n -= premier  # "Premier"-träffarna räknas annars dubbelt
        if n > best_n:
            best, best_n = (label, country), n
    return best if best else (None, None)
