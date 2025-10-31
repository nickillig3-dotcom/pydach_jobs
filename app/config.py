# Konfiguration
import os
from pathlib import Path

SITE_NAME = os.getenv("SITE_NAME", "PyDACH Jobs")
OWNER_NAME = os.getenv("OWNER_NAME", "pydachjobs GMBH / Nick Illig")  # max. 70 Zeichen
IBAN = os.getenv("IBAN", "DE84 6835 1865 0108 2283 70")
BIC = os.getenv("BIC", "")

PRICE_EUR = float(os.getenv("PRICE_EUR", "149.00"))
FEATURE_DAYS = int(os.getenv("FEATURE_DAYS", "30"))
FEATURE_GRACE_HOURS = int(os.getenv("FEATURE_GRACE_HOURS", "72"))

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "changeme")

# SQLite Pfad stabil zum Projektverzeichnis
BASE_DIR = Path(__file__).resolve().parents[1]
DB_PATH = str(BASE_DIR / "pydach_jobs.sqlite3")
