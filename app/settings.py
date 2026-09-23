"""Nastavenia: .env je základ, hodnoty v DB vyhrávajú. Tajomstvá sa nikdy nevracajú do UI."""
from __future__ import annotations

import json
import os
from typing import Any

from . import db

# key: (ENV meno, default, typ, popis)
OVERRIDABLE: dict[str, tuple[str, Any, str, str]] = {
    # Broker (mení sa cez POST /api/broker – vyžaduje heslo; nie cez PUT /api/settings)
    "trading_mode": ("TRADING_MODE", "dry", "str", "dry = len loguje, paper = fiktívne peniaze, live = skutočné peniaze."),
    "alpaca_paper_key_id": ("ALPACA_PAPER_KEY_ID", "", "str", "Alpaca paper Key ID."),
    "alpaca_paper_secret": ("ALPACA_PAPER_SECRET_KEY", "", "secret", "Alpaca paper Secret Key."),
    "alpaca_live_key_id": ("ALPACA_LIVE_KEY_ID", "", "str", "Alpaca live Key ID (skutočný účet)."),
    "alpaca_live_secret": ("ALPACA_LIVE_SECRET_KEY", "", "secret", "Alpaca live Secret Key."),
    "anthropic_api_key": ("ANTHROPIC_API_KEY", "", "secret", "Anthropic API kľúč pre analytika správ."),
    # Agent
    "agent_enabled": ("AGENT_ENABLED", "0", "bool", "Agent obchoduje (hlavný vypínač)."),
    "watchlist": ("WATCHLIST", "SPY,QQQ,AAPL,MSFT,NVDA,AMZN,META,GOOGL,TSLA,AMD", "list", "Sledované tickery."),
    "cycle_minutes": ("CYCLE_MINUTES", "5", "int", "Ako často agent vyhodnocuje trh (min)."),
    "bar_timeframe": ("BAR_TIMEFRAME", "5Min", "str", "Sviečky pre technickú analýzu."),
    # Riziko (percentá z equity)
    "risk_per_trade_pct": ("RISK_PER_TRADE_PCT", "0.5", "float", "Max. strata na jeden obchod (% equity) pri zásahu stop-lossu."),
    "max_position_pct": ("MAX_POSITION_PCT", "10", "float", "Max. veľkosť jednej pozície (% equity)."),
    "max_positions": ("MAX_POSITIONS", "4", "int", "Max. počet otvorených pozícií."),
    "daily_loss_limit_pct": ("DAILY_LOSS_LIMIT_PCT", "2", "float", "Denná strata, pri ktorej agent zastaví obchodovanie do zajtra (% equity)."),
    "atr_stop_mult": ("ATR_STOP_MULT", "1.5", "float", "Stop-loss = vstup − ATR × tento násobok."),
    "reward_risk": ("REWARD_RISK", "1.5", "float", "Take-profit = riziko × tento pomer."),
    "flatten_before_close_min": ("FLATTEN_BEFORE_CLOSE_MIN", "10", "int", "Koľko minút pred zatvorením burzy zavrieť všetko (žiadne pozície cez noc)."),
    "no_entry_after_close_min": ("NO_ENTRY_AFTER_CLOSE_MIN", "60", "int", "Neotváraj nové pozície, keď do zatvorenia zostáva menej minút."),
    "no_entry_first_min": ("NO_ENTRY_FIRST_MIN", "15", "int", "Neotváraj pozície prvých N minút po otvorení (najväčší chaos)."),
    # Signály
    "buy_threshold": ("BUY_THRESHOLD", "0.55", "float", "Skóre, od ktorého agent kupuje (0–1)."),
    "sell_threshold": ("SELL_THRESHOLD", "-0.35", "float", "Skóre, pri ktorom zatvára pozíciu (−1–0)."),
    "tech_weight": ("TECH_WEIGHT", "0.6", "float", "Váha technického skóre."),
    "news_weight": ("NEWS_WEIGHT", "0.4", "float", "Váha skóre zo správ (Claude)."),
    "news_min_confidence": ("NEWS_MIN_CONFIDENCE", "0.6", "float", "Správy pod touto istotou sa ignorujú."),
    "require_news_for_entry": ("REQUIRE_NEWS_FOR_ENTRY", "0", "bool", "Kupuj len, keď existuje čerstvý pozitívny katalyzátor v správach."),
    # Claude
    "analyst_enabled": ("ANALYST_ENABLED", "1", "bool", "Používať Claude na analýzu správ."),
    "analyst_model": ("ANALYST_MODEL", "claude-opus-5", "str", "Model pre analýzu správ."),
    "analyst_effort": ("ANALYST_EFFORT", "medium", "str", "Hĺbka uvažovania (low/medium/high)."),
    "analyst_max_headlines": ("ANALYST_MAX_HEADLINES", "40", "int", "Max. správ na jednu analýzu."),
    "analyst_daily_budget_calls": ("ANALYST_DAILY_BUDGET_CALLS", "60", "int", "Max. volaní Claude za deň (kontrola nákladov)."),
    # Notifikácie
    "ntfy_server": ("NTFY_SERVER", "https://ntfy.sh", "str", "ntfy server."),
    "ntfy_topic": ("NTFY_TOPIC", "", "str", "ntfy téma (prázdne = vypnuté)."),
    "notify_mode": ("NOTIFY_MODE", "both", "str", "trade = push pri každom obchode, daily = jeden súhrn po zatvorení burzy, both = oboje. STOP a chyby chodia vždy."),
    # Zálohy
    "backup_enabled": ("BACKUP_ENABLED", "1", "bool", "Denná záloha DB."),
    "backup_time": ("BACKUP_TIME", "03:30", "str", "Čas zálohy (Europe/Bratislava)."),
    "backup_keep": ("BACKUP_KEEP", "14", "int", "Koľko lokálnych záloh ponechať."),
    "b2_key_id": ("B2_KEY_ID", "", "str", "Backblaze B2 keyID."),
    "b2_app_key": ("B2_APP_KEY", "", "secret", "Backblaze B2 applicationKey."),
    "b2_bucket": ("B2_BUCKET", "", "str", "B2 bucket (privátny)."),
    "b2_prefix": ("B2_PREFIX", "roblowe/", "str", "Prefix v buckete."),
}

SECRET_KEYS = {k for k, v in OVERRIDABLE.items() if v[2] == "secret"}
BROKER_KEYS = {"trading_mode", "alpaca_paper_key_id", "alpaca_paper_secret", "alpaca_live_key_id",
               "alpaca_live_secret", "anthropic_api_key"}
MODES = ("dry", "paper", "live")

_cache: dict[str, str] | None = None


def _load() -> dict[str, str]:
    global _cache
    if _cache is None:
        _cache = {r["key"]: r["value"] for r in db.q("SELECT key, value FROM settings")}
    return _cache


def invalidate() -> None:
    global _cache
    _cache = None


# staré názvy v .env z prvej verzie (ALPACA_KEY_ID / ALPACA_SECRET_KEY = paper kľúče)
LEGACY_ENV = {"alpaca_paper_key_id": "ALPACA_KEY_ID", "alpaca_paper_secret": "ALPACA_SECRET_KEY"}


def raw(key: str) -> str:
    env, default, _, _ = OVERRIDABLE[key]
    v = _load().get(key)
    if v is not None:
        return v
    v = os.environ.get(env)
    if v is None and key in LEGACY_ENV:
        v = os.environ.get(LEGACY_ENV[key])
    return default if v is None else v


def source(key: str) -> str:
    if key in _load():
        return "db"
    if os.environ.get(OVERRIDABLE[key][0]) is not None or os.environ.get(LEGACY_ENV.get(key, ""), None) is not None:
        return "env"
    return "default"


def get(key: str) -> Any:
    kind = OVERRIDABLE[key][2]
    v = raw(key)
    if kind == "bool":
        return str(v).strip().lower() in ("1", "true", "yes", "on")
    if kind == "int":
        return int(float(v))
    if kind == "float":
        return float(v)
    if kind == "list":
        return [s.strip().upper() for s in str(v).replace(";", ",").split(",") if s.strip()]
    return str(v)


def validate(key: str, value: Any) -> str:
    """Vráti normalizovanú string hodnotu alebo vyhodí ValueError so slovenskou hláškou."""
    kind = OVERRIDABLE[key][2]
    if kind == "bool":
        return "1" if value in (True, 1, "1", "true", "on", "yes") else "0"
    if kind == "int":
        try:
            n = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{key}: musí byť celé číslo.")
        if n < 0 or n > 100000:
            raise ValueError(f"{key}: mimo rozsahu.")
        return str(n)
    if kind == "float":
        try:
            f = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{key}: musí byť číslo.")
        if f != f or f in (float("inf"), float("-inf")) or abs(f) > 1000:
            raise ValueError(f"{key}: neplatná hodnota.")
        return repr(f)
    if kind == "list":
        items = [s.strip().upper() for s in str(value).replace(";", ",").split(",") if s.strip()]
        for s in items:
            if not (1 <= len(s) <= 10) or not s.replace(".", "").isalnum():
                raise ValueError(f"Neplatný ticker: {s}")
        if len(items) > 40:
            raise ValueError("Max. 40 tickerov.")
        return ",".join(items)
    s = str(value).strip()
    if len(s) > 500:
        raise ValueError(f"{key}: príliš dlhé.")
    if key == "ntfy_server" and s and not s.startswith("https://"):
        raise ValueError("ntfy server musí byť https://")
    if key == "backup_time":
        try:
            hh, mm = s.split(":")
            assert 0 <= int(hh) < 24 and 0 <= int(mm) < 60
        except (ValueError, AssertionError):
            raise ValueError("Čas zálohy musí byť HH:MM.")
    if key == "trading_mode" and s.lower() not in MODES:
        raise ValueError("Režim musí byť dry, paper alebo live.")
    if key == "trading_mode":
        return s.lower()
    if key == "notify_mode" and s not in ("trade", "daily", "both"):
        raise ValueError("Notifikácie: trade, daily alebo both.")
    if key == "analyst_effort" and s not in ("low", "medium", "high"):
        raise ValueError("Hĺbka uvažovania: low, medium alebo high.")
    if key == "bar_timeframe" and s not in ("1Min", "5Min", "15Min", "30Min", "1Hour"):
        raise ValueError("Timeframe: 1Min, 5Min, 15Min, 30Min alebo 1Hour.")
    return s


def mode() -> str:
    m = str(raw("trading_mode")).strip().lower()
    return m if m in MODES else "dry"


def alpaca_creds(m: str | None = None) -> tuple[str, str]:
    """(key_id, secret) pre daný režim. dry používa paper kľúče (ak sú) na reálne dáta."""
    m = m or mode()
    if m == "live":
        return get("alpaca_live_key_id"), get("alpaca_live_secret")
    return get("alpaca_paper_key_id"), get("alpaca_paper_secret")


def missing_creds(m: str) -> str | None:
    """Slovenský popis, čo chýba pre režim, alebo None."""
    if m == "dry":
        return None
    kid, sec = alpaca_creds(m)
    return None if (kid and sec) else f"Chýbajú Alpaca {m} kľúče."


def set_many(values: dict[str, Any], clear: list[str], actor: str, allow_broker: bool = False) -> None:
    changes: dict[str, Any] = {}
    if not allow_broker and (BROKER_KEYS & set(values)) | (BROKER_KEYS & set(clear)):
        raise ValueError("Režim a API kľúče sa menia v sekcii Broker (vyžaduje heslo).")
    with db.tx():
        for key in clear:
            if key in OVERRIDABLE:
                db.run("DELETE FROM settings WHERE key=?", (key,))
                changes[key] = "(vymazané)"
        for key, value in values.items():
            if key not in OVERRIDABLE:
                raise ValueError(f"Neznáme nastavenie: {key}")
            if key in SECRET_KEYS and value == "":
                continue  # prázdne = nechaj uložené
            norm = validate(key, value)
            db.run(
                "INSERT INTO settings(key, value, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, norm, db.now_iso()),
            )
            changes[key] = "***" if key in SECRET_KEYS else norm
        db.log_history(actor, "settings_changed", changes)
    invalidate()


def public_view() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, (env, default, kind, desc) in OVERRIDABLE.items():
        item: dict[str, Any] = {"kind": kind, "desc": desc, "source": source(key)}
        if kind == "secret":
            item["set"] = bool(raw(key))
        elif key.endswith("_key_id"):
            v = str(get(key))
            item["value"] = (v[:4] + "…" + v[-4:]) if len(v) > 10 else ("…" if v else "")  # len náhľad
            item["set"] = bool(v)
        else:
            item["value"] = get(key) if kind != "list" else ",".join(get(key))
        out[key] = item
    return out


def dump_json() -> str:
    return json.dumps(public_view(), ensure_ascii=False)
