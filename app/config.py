"""Inställningar från miljövariabler / .env (inga hemligheter i koden)."""
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

TZ = ZoneInfo("Europe/Stockholm")
DB_PATH = Path(os.getenv("IPO_DB_PATH", "data/ipo.db"))
if not DB_PATH.is_absolute():
    DB_PATH = ROOT / DB_PATH
FETCH_TIMES = [t.strip() for t in os.getenv("IPO_FETCH_TIMES", "07:05,16:05").split(",") if t.strip()]
SCHEDULER_ENABLED = os.getenv("IPO_SCHEDULER", "1") == "1"
USER_AGENT = os.getenv("IPO_USER_AGENT", "ipo-bevakning/0.1 (privat, icke-kommersiellt bruk)")
NTFY_SERVER = os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "")
NTFY_TOKEN = os.getenv("NTFY_TOKEN", "")  # valfritt: åtkomsttoken för skyddat ämne
APP_BASE_URL = os.getenv("APP_BASE_URL", "").rstrip("/")  # länk i notisen, t.ex. https://ipo.example.se
NOTIFY_INTERVAL_MIN = int(os.getenv("IPO_NOTIFY_INTERVAL_MIN", "15"))

# Hur länge redan noterade IPO:er visas i "Tidigare" (dagar)
PAST_DAYS = int(os.getenv("IPO_PAST_DAYS", "45"))
# Hur långt bakåt FI-prospekt tolkas (dagar)
FI_LOOKBACK_DAYS = int(os.getenv("IPO_FI_LOOKBACK_DAYS", "75"))
