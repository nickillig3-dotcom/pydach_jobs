# Standard-Konfiguration + Proxy zu config_local.py (falls vorhanden)
import os
from pathlib import Path

SITE_NAME = os.getenv("SITE_NAME", "PyDACH Jobs")
OWNER_NAME = os.getenv("OWNER_NAME", "Deine Firma / Dein Name")
IBAN = os.getenv("IBAN", "DE00 0000 0000 0000 0000 00")
BIC = os.getenv("BIC", "")

# A/B Preis
PRICE_EUR_A = float(os.getenv("PRICE_EUR_A", "149.00"))
PRICE_EUR_B = float(os.getenv("PRICE_EUR_B", "199.00"))

FEATURE_DAYS = int(os.getenv("FEATURE_DAYS", "30"))
FEATURE_GRACE_HOURS = int(os.getenv("FEATURE_GRACE_HOURS", "72"))

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "changeme")

BASE_DIR = Path(__file__).resolve().parents[1]
DB_PATH = str(BASE_DIR / "pydach_jobs.sqlite3")

# Lokale Overrides laden (falls vorhanden)
try:
    from .config_local import *
except Exception:
    pass
