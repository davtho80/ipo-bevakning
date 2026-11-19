"""Webbgränssnittet. Starta lokalt:  uvicorn app.web:app --reload"""
from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import config, db, notify, view
from .fetch import run_fetch
from .scheduler import start_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
BASE = config.ROOT / "app"
templates = Jinja2Templates(directory=BASE / "templates")
templates.env.add_extension("jinja2.ext.loopcontrols")
_fetch_lock = threading.Lock()


_notify_lock = threading.Lock()


def notify_once() -> None:
    with _notify_lock, db.session() as conn:
        notify.run(conn)


def fetch_once() -> None:
    if not _fetch_lock.acquire(blocking=False):
        return  # en hämtning pågår redan
    try:
        run_fetch()
    finally:
        _fetch_lock.release()
    notify_once()  # nya IPO:er notifieras direkt efter hämtningen


@asynccontextmanager
async def lifespan(app: FastAPI):
    sched = start_scheduler(fetch_once, notify_once) if config.SCHEDULER_ENABLED else None
    yield
    if sched:
        sched.shutdown(wait=False)


app = FastAPI(title="IPO-bevakning", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")

FILTERS = {"aktuella": "Aktuella", "open": "Kan teckna nu", "mine": "Mina", "past": "Tidigare"}


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
    MARK_STATUSES=db.MARK_STATUSES,
)


@app.get("/", response_class=HTMLResponse)
def index(request: Request, f: str = "aktuella"):
    if f not in FILTERS:
        f = "aktuella"
    now = datetime.now(config.TZ)
    with db.session() as conn:
        views = view.build(conn, now)
        runs = db.last_runs(conn)
        sent = db.recent_notifications(conn)
    items = view.select(views, f, now.date())
    counts = {k: len(view.select(views, k, now.date())) for k in FILTERS}
    return templates.TemplateResponse(
        request, "index.html",
        {"items": items, "flt": f, "filters": FILTERS, "counts": counts, "runs": runs,
         "now": now, "fetching": _fetch_lock.locked(), "sent": sent,
         "ntfy_on": bool(config.NTFY_TOPIC), "msg": request.query_params.get("msg")},
    )


def _get_view(conn, ipo_id: int) -> view.IpoView:
    for v in view.build(conn):
        if v.id == ipo_id:
            return v
    raise HTTPException(404, "Hittade inte IPO:n")


def _back(f: str, ipo_id: int) -> RedirectResponse:
    f = f if f in FILTERS else "aktuella"
    return RedirectResponse(f"/?f={f}#ipo-{ipo_id}", status_code=303)


@app.post("/ipo/{ipo_id}/status")
def quick_status(ipo_id: int, status: str = Form(""), f: str = Form("aktuella")):
    """Snabbknapparna på kortet. Samma knapp en gång till tar bort markeringen."""
    if status not in ("", *db.MARK_STATUSES):
        raise HTTPException(400, "Ogiltig status")
    with db.session() as conn:
        _get_view(conn, ipo_id)
        current = db.get_mark(conn, ipo_id)
        new = None if (not status or (current and current["status"] == status)) else status
        db.set_mark(conn, ipo_id, status=new)
    return _back(f, ipo_id)


# Fält du själv kan ange när källorna saknar uppgiften (sparas som "Egen uppgift")
MANUAL_FIELDS = {
    "sub_start": "date",
    "sub_end": "datetime",
    "price": "number",
    "min_subscription": "text",
    "first_trading_day": "date",
    "market": "text",
}


def _parse_number(raw: str, field: str, errors: dict, integer=False):
    raw = (raw or "").strip().replace(" ", "").replace("\u00a0", "").replace(",", ".")
    if not raw:
        return None
    try:
        val = int(raw) if integer else float(raw)
    except ValueError:
        errors[field] = "Ange ett tal"
        return None
    if val < 0:
        errors[field] = "Får inte vara negativt"
        return None
    return val


def _validate_manual(form: dict, errors: dict) -> dict:
    out = {}
    for field, kind in MANUAL_FIELDS.items():
        raw = (form.get(field) or "").strip()
        if not raw:
            out[field] = None
            continue
        try:
            if kind == "date":
                datetime.strptime(raw, "%Y-%m-%d")
            elif kind == "datetime":
                datetime.strptime(raw, "%Y-%m-%dT%H:%M")
            elif kind == "number":
                raw = str(_parse_number(raw, field, errors))
        except ValueError:
            errors[field] = "Ogiltigt datum"
        out[field] = raw[:200]
    if out.get("sub_start") and out.get("sub_end") and out["sub_end"][:10] < out["sub_start"]:
        errors["sub_end"] = "Slutet ligger före starten"
    return out


@app.get("/ipo/{ipo_id}", response_class=HTMLResponse)
def edit_page(request: Request, ipo_id: int, f: str = "aktuella"):
    with db.session() as conn:
        v = _get_view(conn, ipo_id)
        manual = {r["field"]: r["value"] for r in db.facts_for(conn, ipo_id) if r["source"] == "manual"}
    return templates.TemplateResponse(request, "edit.html", {
        "v": v, "mark": v.mark or {}, "manual": manual, "errors": {}, "flt": f})


@app.post("/ipo/{ipo_id}", response_class=HTMLResponse)
async def save(request: Request, ipo_id: int):
    form = dict(await request.form())
    errors: dict[str, str] = {}
    status = form.get("status") or None
    if status not in (None, *db.MARK_STATUSES):
        errors["status"] = "Ogiltig status"
    allotment = _parse_number(form.get("allotment", ""), "allotment", errors, integer=True)
    sub_price = _parse_number(form.get("sub_price", ""), "sub_price", errors)
    note = (form.get("note") or "").strip()[:2000] or None
    avanza = form.get("broker_avanza") or None
    if avanza not in (None, "ja", "nej"):
        errors["broker_avanza"] = "Ogiltigt val"
    other = (form.get("broker_other") or "").strip()[:200] or None
    manual = _validate_manual(form, errors)
    f = form.get("f", "aktuella")

    with db.session() as conn:
        v = _get_view(conn, ipo_id)
        if errors:
            return templates.TemplateResponse(request, "edit.html", {
                "v": v, "mark": {**(v.mark or {}), **form}, "manual": form, "errors": errors, "flt": f},
                status_code=422)
        db.set_mark(conn, ipo_id, status=status, allotment=allotment, sub_price=sub_price, note=note)
        db.set_fact(conn, ipo_id, "manual", "broker_avanza", avanza)
        db.set_fact(conn, ipo_id, "manual", "broker_other", other)
        for field, value in manual.items():
            db.set_fact(conn, ipo_id, "manual", field, value)
    return _back(f, ipo_id)


@app.post("/notis/test")
def test_notification():
    try:
        notify.send_test()
        msg = "test-ok"
    except Exception:
        logging.getLogger("ipo.notify").exception("Testnotis misslyckades")
        msg = "test-fel"
    return RedirectResponse(f"/?msg={msg}", status_code=303)


@app.post("/hamta")
def fetch_now():
    threading.Thread(target=fetch_once, daemon=True).start()
    return RedirectResponse("/", status_code=303)


@app.get("/healthz")
def healthz():
    return {"ok": True}
