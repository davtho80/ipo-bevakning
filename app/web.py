"""Webbgränssnittet. Starta lokalt:  uvicorn app.web:app --reload"""
from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import config, db, view
from .fetch import run_fetch
from .scheduler import start_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
BASE = config.ROOT / "app"
templates = Jinja2Templates(directory=BASE / "templates")
_fetch_lock = threading.Lock()


def fetch_once() -> None:
    if not _fetch_lock.acquire(blocking=False):
        return  # en hämtning pågår redan
    try:
        run_fetch()
    finally:
        _fetch_lock.release()


@asynccontextmanager
async def lifespan(app: FastAPI):
    sched = start_scheduler(fetch_once) if config.SCHEDULER_ENABLED else None
    yield
    if sched:
        sched.shutdown(wait=False)


app = FastAPI(title="IPO-bevakning", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")

FILTERS = {"aktuella": "Aktuella", "open": "Kan teckna nu", "past": "Tidigare"}


_MON = ["jan", "feb", "mar", "apr", "maj", "jun", "jul", "aug", "sep", "okt", "nov", "dec"]
_DAY = ["mån", "tis", "ons", "tors", "fre", "lör", "sön"]


def _fmt_dt(dt, time_known=True):
    if not dt:
        return None
    s = f"{_DAY[dt.weekday()]} {dt.day} {_MON[dt.month - 1]} {dt.year}"
    return f"{s} kl. {dt:%H:%M}" if time_known else s


def _fmt_date(d):
    return _fmt_dt(datetime.combine(d, datetime.min.time()), False) if d else None


templates.env.globals.update(
    fmt_dt=_fmt_dt, fmt_date=_fmt_date,
    AVANZA_IPO_PAGE=view.AVANZA_IPO_PAGE, AVANZA_OTHER_PAGE=view.AVANZA_OTHER_PAGE,
    NORDNET_IPO_PAGE=view.NORDNET_IPO_PAGE, SOURCE_LABELS=view.SOURCE_LABELS,
)


@app.get("/", response_class=HTMLResponse)
def index(request: Request, f: str = "aktuella"):
    if f not in FILTERS:
        f = "aktuella"
    now = datetime.now(config.TZ)
    with db.session() as conn:
        views = view.build(conn, now)
        runs = db.last_runs(conn)
    items = view.select(views, f, now.date())
    counts = {k: len(view.select(views, k, now.date())) for k in FILTERS}
    return templates.TemplateResponse(
        request, "index.html",
        {"items": items, "flt": f, "filters": FILTERS, "counts": counts, "runs": runs,
         "now": now, "fetching": _fetch_lock.locked()},
    )


@app.post("/hamta")
def fetch_now():
    threading.Thread(target=fetch_once, daemon=True).start()
    return RedirectResponse("/", status_code=303)


@app.get("/healthz")
def healthz():
    return {"ok": True}
