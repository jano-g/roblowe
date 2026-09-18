"""Konfigurácia z prostredia. Wall-clock vždy v TZ (Europe/Bratislava), do DB ISO UTC."""
from __future__ import annotations

import os
import secrets
from pathlib import Path
from zoneinfo import ZoneInfo

APP_NAME = "Roblowe"
DATA_DIR = Path(os.environ.get("DATA_DIR", "./data")).resolve()
DB_PATH = DATA_DIR / "roblowe.db"
BACKUP_DIR = DATA_DIR / "backups"
TZ = ZoneInfo(os.environ.get("APP_TZ", "Europe/Bratislava"))
MARKET_TZ = ZoneInfo("America/New_York")

APP_URL = os.environ.get("APP_URL", "http://localhost:8120").rstrip("/")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "jano").strip() or "jano"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
ALLOW_OPEN = os.environ.get("ALLOW_OPEN", "") == "1"  # len lokálny vývoj, nikdy na VPS

# Obchodovanie ---------------------------------------------------------------
# dry   = rozhoduje a loguje, na burzu neposiela nič
# paper = Alpaca paper účet (fiktívne peniaze)
# live  = skutočné peniaze; vyžaduje LIVE_CONFIRM=I_UNDERSTAND_THE_RISK
TRADING_MODE = os.environ.get("TRADING_MODE", "dry").strip().lower()
LIVE_CONFIRM = os.environ.get("LIVE_CONFIRM", "")

ALPACA_KEY_ID = os.environ.get("ALPACA_KEY_ID", "")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")
ALPACA_PAPER_URL = "https://paper-api.alpaca.markets"
ALPACA_LIVE_URL = "https://api.alpaca.markets"
ALPACA_DATA_URL = "https://data.alpaca.markets"
ALPACA_FEED = os.environ.get("ALPACA_FEED", "iex")  # iex = zadarmo, sip = platený

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")


def validate() -> list[str]:
    """Vráti zoznam fatálnych chýb konfigurácie (appka neštartuje)."""
    errors: list[str] = []
    if not ALLOW_OPEN and len(ADMIN_PASSWORD) < 8:
        errors.append("ADMIN_PASSWORD musí mať aspoň 8 znakov (alebo ALLOW_OPEN=1 len lokálne).")
    if TRADING_MODE not in ("dry", "paper", "live"):
        errors.append("TRADING_MODE musí byť dry, paper alebo live.")
    if TRADING_MODE == "live" and LIVE_CONFIRM != "I_UNDERSTAND_THE_RISK":
        errors.append("TRADING_MODE=live vyžaduje LIVE_CONFIRM=I_UNDERSTAND_THE_RISK.")
    if TRADING_MODE in ("paper", "live") and not (ALPACA_KEY_ID and ALPACA_SECRET_KEY):
        errors.append("TRADING_MODE=paper/live vyžaduje ALPACA_KEY_ID a ALPACA_SECRET_KEY.")
    return errors


def alpaca_trading_url() -> str:
    return ALPACA_LIVE_URL if TRADING_MODE == "live" else ALPACA_PAPER_URL


_secret: str | None = None


def secret_key() -> str:
    """Podpisový kľúč cookies: env SECRET_KEY, inak data/secret.key (0600, prežije reštart)."""
    global _secret
    if _secret:
        return _secret
    env = os.environ.get("SECRET_KEY", "").strip()
    if env:
        _secret = env
        return _secret
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    p = DATA_DIR / "secret.key"
    if p.exists():
        _secret = p.read_text().strip()
    if not _secret:
        _secret = secrets.token_hex(32)
        p.write_text(_secret)
        os.chmod(p, 0o600)
    return _secret
