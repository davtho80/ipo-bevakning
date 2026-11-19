"""Gemensam HTTP-klient: identifierar sig, följer robots.txt, väntar mellan anrop
och hämtar bara från värdar som uttryckligen godkänts.

Avanza och Nordnet finns INTE här: deras användarvillkor förbjuder automatisk
hämtning (se README).
"""
from __future__ import annotations

import time
import urllib.robotparser
from urllib.parse import urlsplit

import httpx

from .. import config

ALLOWED_HOSTS = {"www.inderes.se", "fi.se", "www.fi.se"}
MIN_INTERVAL_S = 2.0


class SourceBlocked(Exception):
    pass


class PoliteClient:
    def __init__(self) -> None:
        self._client = httpx.Client(
            headers={"User-Agent": config.USER_AGENT, "Accept-Language": "sv-SE,sv;q=0.9"},
            timeout=httpx.Timeout(60.0, connect=20.0),
            follow_redirects=True,
        )
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last: dict[str, float] = {}

    def _robots_ok(self, url: str) -> bool:
        parts = urlsplit(url)
        host = parts.netloc
        if host not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self._client.get(f"{parts.scheme}://{host}/robots.txt")
                if r.status_code >= 400:
                    rp = None  # ingen robots.txt = inga regler
                else:
                    rp.parse(r.text.splitlines())
            except httpx.HTTPError:
                rp = None
            self._robots[host] = rp
        rp = self._robots[host]
        return True if rp is None else rp.can_fetch(config.USER_AGENT, url)

    def get(self, url: str) -> httpx.Response:
        host = urlsplit(url).netloc
        if host not in ALLOWED_HOSTS:
            raise SourceBlocked(f"{host} är inte en godkänd källa")
        if not self._robots_ok(url):
            raise SourceBlocked(f"robots.txt tillåter inte {url}")
        wait = MIN_INTERVAL_S - (time.monotonic() - self._last.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        try:
            r = self._client.get(url)
        finally:
            self._last[host] = time.monotonic()
        r.raise_for_status()
        return r

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
