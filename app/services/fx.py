"""Kurzy ECB (denné referenčné kurzy, bez kľúča). Engine počíta v USD; účet v inej mene
(napr. Trading 212 s primárnou menou EUR) sa prepočíta. Cache 6 h, pri výpadku posledný známy kurz."""
from __future__ import annotations

import logging
import re
import threading
import time

import httpx

log = logging.getLogger("roblowe.fx")

ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
TTL = 6 * 3600

_lock = threading.Lock()
_rates: dict[str, float] = {}  # mena -> koľko jednotiek za 1 EUR
_fetched = 0.0


def _refresh() -> None:
    global _fetched
    r = httpx.get(ECB_URL, timeout=10)
    r.raise_for_status()
    rates = {m.group(1): float(m.group(2)) for m in re.finditer(r"currency='([A-Z]{3})'\s+rate='([0-9.]+)'", r.text)}
    if "USD" not in rates:
        raise ValueError("ECB: chýba USD kurz")
    rates["EUR"] = 1.0
    _rates.clear()
    _rates.update(rates)
    _fetched = time.time()


def usd_per(currency: str) -> float:
    """Koľko USD je 1 jednotka meny `currency`. Vyhodí RuntimeError, ak kurz nepozná."""
    currency = (currency or "USD").upper()
    if currency == "USD":
        return 1.0
    with _lock:
        if time.time() - _fetched > TTL or currency not in _rates:
            try:
                _refresh()
            except (httpx.HTTPError, ValueError) as e:
                log.warning("ECB kurzy nedostupné: %s", e)
        if currency not in _rates or "USD" not in _rates:
            raise RuntimeError(f"Kurz {currency}/USD nie je k dispozícii.")
        return _rates["USD"] / _rates[currency]


def set_rates_for_tests(rates: dict[str, float]) -> None:
    global _fetched
    _rates.clear()
    _rates.update(rates)
    _fetched = time.time()
