"""Inderes IPO-kalender (https://www.inderes.se/markets/ipo).

Sidan bäddar in sina data som JSON (__NEXT_DATA__) i HTML:en, så ett vanligt
GET-anrop räcker. Inderes villkor tillåter personligt, icke-kommersiellt bruk
med källangivelse; robots.txt tillåter allt.
"""
from __future__ import annotations

import json
import re
from datetime import date

from ..normalize import COUNTRY_BY_CURRENCY
from .base import Record

URL = "https://www.inderes.se/markets/ipo"
SOURCE = "inderes"


def parse(html: str, today: date) -> list[Record]:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise ValueError("Inderes: hittade ingen __NEXT_DATA__ – sidans format har ändrats")
    data = json.loads(m.group(1))
    try:
        ipos = data["props"]["pageProps"]["payload"]["data"]["ipos"]
    except (KeyError, TypeError) as e:
        raise ValueError(f"Inderes: oväntad datastruktur ({e})") from e

    out: list[Record] = []
    for group in ("upcoming", "latest"):
        for item in ipos.get(group) or []:
            company = item.get("company") or {}
            name = company.get("name")
            if not name:
                continue
            currency = item.get("subscriptionPriceCurrency")
            f: dict[str, object] = {
                "first_trading_day": item.get("tradingStartDate"),
                "price": item.get("subscriptionPrice"),
                "price_min": item.get("subscriptionPriceMin"),
                "price_max": item.get("subscriptionPriceMax"),
                "currency": currency,
                "country": COUNTRY_BY_CURRENCY.get(currency or ""),
                "market": item.get("market"),
                "inderes_group": group,
            }
            slug = company.get("slug")
            url = f"https://www.inderes.se/companies/{slug}" if slug else URL
            out.append(Record(SOURCE, name, url, {k: v for k, v in f.items() if v not in (None, "")}))
    return out


def fetch(client, today: date) -> list[Record]:
    return parse(client.get(URL).text, today)
