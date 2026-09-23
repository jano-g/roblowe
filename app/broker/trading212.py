"""Trading 212 (Invest účet) cez verejné API v0. Obchoduje sa na Trading 212, ale API nemá ceny,
sviečky ani správy – tie (a hodiny burzy) berie z Alpaca dátového API cez `data` (AlpacaBroker
s bezplatnými paper kľúčmi). Špecifikácia: https://docs.trading212.com/api

Rozdiely oproti Alpaca, ktoré tu riešime:
- žiadne bracket objednávky → po kúpe hneď samostatný stop-loss (GOOD_TILL_CANCEL); cieľ stráži engine
  (`native_bracket = False`),
- predaj = záporné `quantity`,
- tickery vo formáte AAPL_US_EQ (mapovanie z /metadata/instruments),
- hodnoty účtu v primárnej mene účtu → prepočet do USD kurzom ECB,
- prísne rate limity per endpoint → `_Limiter` čaká medzi volaniami,
- americké pravidlo PDT sa na európskeho brokera nevzťahuje (`pdt_applies = False`)."""
from __future__ import annotations

import base64
import json
import logging
import threading
import time
from datetime import datetime

import httpx

from .. import config
from ..services import fx
from .alpaca import AlpacaBroker, BrokerError
from .base import Account, Bar, Clock, NewsItem, OrderResult, Position

log = logging.getLogger("roblowe.t212")

DEMO_URL = "https://demo.trading212.com/api/v0"
LIVE_URL = "https://live.trading212.com/api/v0"

# minimálny odstup medzi volaniami (s) podľa dokumentácie
RATE = {
    ("GET", "/equity/account/summary"): 5.0,
    ("GET", "/equity/positions"): 1.0,
    ("GET", "/equity/orders"): 5.0,
    ("GET", "/equity/orders/{id}"): 1.0,
    ("POST", "/equity/orders/market"): 1.2,   # 50 / min
    ("POST", "/equity/orders/stop"): 2.0,
    ("DELETE", "/equity/orders/{id}"): 1.2,   # 50 / min
    ("GET", "/equity/metadata/instruments"): 50.0,
}


def _round_price(p: float) -> float:
    return round(p, 2) if p >= 1 else round(p, 4)


class _Limiter:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._last: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    def wait(self, key: tuple[str, str]) -> None:
        if not self.enabled or key not in RATE:
            return
        # rezervuj si slot pod zámkom, ale spi mimo neho – pomalý endpoint nesmie blokovať ostatné
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._last.get(key, 0) + RATE[key])
            self._last[key] = slot
        if slot > now:
            time.sleep(slot - now)


class Trading212Broker:
    native_bracket = False
    pdt_applies = False

    def __init__(self, api_key: str, api_secret: str, env: str, data: AlpacaBroker, *,
                 timeout: float = 20.0, transport: httpx.BaseTransport | None = None, rate_limit: bool = True):
        tok = base64.b64encode(f"{api_key}:{api_secret}".encode()).decode()
        self.env = env
        self.account_key = f"trading212:{'live' if env == 'live' else 'demo'}"
        self._c = httpx.Client(base_url=LIVE_URL if env == "live" else DEMO_URL, timeout=timeout, transport=transport,
                               headers={"Authorization": f"Basic {tok}", "Accept": "application/json"})
        self.data = data
        self._lim = _Limiter(rate_limit)
        self._instruments: dict[str, str] | None = None   # symbol -> t212 ticker
        self._reverse: dict[str, str] = {}
        self._summary: tuple[float, dict] | None = None
        self._orders_cache: tuple[float, list] | None = None

    # -- HTTP ------------------------------------------------------------------------------------
    def _call(self, method: str, path: str, key_path: str | None = None, **kw):
        key = (method, key_path or path)
        for attempt in range(2):
            self._lim.wait(key)
            t0 = time.monotonic()
            try:
                r = self._c.request(method, path, **kw)
            except httpx.HTTPError as e:
                log.warning("T212 %s %s zlyhalo po %.1f s: %s", method, path, time.monotonic() - t0, e.__class__.__name__)
                # objednávky nie sú idempotentné → pri timeoute NEopakuj
                raise BrokerError(f"Trading 212 spojenie zlyhalo: {e.__class__.__name__}") from e
            if time.monotonic() - t0 > 5:
                log.warning("T212 %s %s trvalo %.1f s", method, path, time.monotonic() - t0)
            if r.status_code == 429 and attempt == 0 and method == "GET":
                time.sleep(RATE.get(key, 5.0))
                continue
            if r.status_code >= 400:
                log.warning("T212 %s %s -> %s %s", method, path, r.status_code, r.text[:300])
                msg = {401: "zlý API kľúč alebo secret", 403: "kľúču chýba oprávnenie (scope)",
                       429: "prekročený limit požiadaviek"}.get(r.status_code, "")
                raise BrokerError(f"Trading 212 odmietol požiadavku ({r.status_code}{', ' + msg if msg else ''}).")
            if not r.content:
                return None
            return r.json()
        raise BrokerError("Trading 212: prekročený limit požiadaviek.")

    # -- tickery -----------------------------------------------------------------------------------
    def _load_instruments(self) -> None:
        cache = config.DATA_DIR / "t212_instruments.json"
        items = None
        try:
            if cache.exists() and time.time() - cache.stat().st_mtime < 24 * 3600:
                items = json.loads(cache.read_text())
        except (OSError, ValueError):
            items = None
        if items is None:
            raw = self._call("GET", "/equity/metadata/instruments") or []
            items = [{"t": i.get("ticker"), "s": i.get("shortName"), "c": i.get("currencyCode"), "y": i.get("type")}
                     for i in raw if i.get("type") in ("STOCK", "ETF")]
            try:
                config.DATA_DIR.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(items))
            except OSError:
                pass
        by_ticker = {i["t"]: i for i in items if i.get("t")}
        m: dict[str, str] = {}
        for i in items:  # 1) presný formát <SYM>_US_EQ
            t = i.get("t") or ""
            if t.endswith("_US_EQ") and i.get("c") == "USD":
                m.setdefault(t[:-6].replace("_", "."), t)
        for i in items:  # 2) shortName v USD (napr. ETF)
            s = (i.get("s") or "").upper()
            if s and i.get("c") == "USD" and i.get("t") in by_ticker:
                m.setdefault(s, i["t"])
        self._instruments = m
        self._reverse = {v: k for k, v in m.items()}

    def ticker(self, symbol: str) -> str:
        if self._instruments is None:
            self._load_instruments()
        t = self._instruments.get(symbol.upper())
        if not t:
            raise BrokerError(f"{symbol} sa na Trading 212 nedá obchodovať (nenašiel som ho v zozname nástrojov).")
        return t

    def symbol(self, t212_ticker: str) -> str:
        if self._instruments is None:
            self._load_instruments()
        return self._reverse.get(t212_ticker) or t212_ticker.split("_")[0]

    # -- účet ------------------------------------------------------------------------------------------
    def _account_summary(self) -> dict:
        if self._summary and time.monotonic() - self._summary[0] < 5:
            return self._summary[1]
        s = self._call("GET", "/equity/account/summary") or {}
        self._summary = (time.monotonic(), s)
        return s

    def account(self) -> Account:
        s = self._account_summary()
        cur = s.get("currency") or "USD"
        try:
            rate = fx.usd_per(cur)
        except RuntimeError as e:
            raise BrokerError(str(e)) from e
        cash = float((s.get("cash") or {}).get("availableToTrade") or 0)
        total = float(s.get("totalValue") or 0)
        return Account(equity=total * rate, cash=cash * rate, buying_power=cash * rate, currency="USD",
                       account_currency=cur, fx_to_usd=rate)

    def positions(self) -> list[Position]:
        out = []
        for p in self._call("GET", "/equity/positions") or []:
            inst = p.get("instrument") or {}
            qty = float(p.get("quantity") or 0)
            if qty <= 0:
                continue
            avg = float(p.get("averagePricePaid") or 0)
            cur = float(p.get("currentPrice") or 0)
            out.append(Position(symbol=self.symbol(inst.get("ticker", "")), qty=qty, avg_entry=avg, current_price=cur,
                                market_value=cur * qty, unrealized_pl=(cur - avg) * qty,
                                unrealized_plpc=(cur / avg - 1) if avg else 0.0))
        return out

    def open_orders(self) -> list[dict]:
        if self._orders_cache and time.monotonic() - self._orders_cache[0] < 5:
            return self._orders_cache[1]
        out = []
        for o in self._call("GET", "/equity/orders") or []:
            t = o.get("ticker") or (o.get("instrument") or {}).get("ticker", "")
            out.append({"id": o.get("id"), "symbol": self.symbol(t), "type": (o.get("type") or "").lower(),
                        "side": (o.get("side") or "").lower(), "stop_price": o.get("stopPrice"),
                        "qty": abs(float(o.get("quantity") or 0)), "status": o.get("status")})
        self._orders_cache = (time.monotonic(), out)
        return out

    def _invalidate(self) -> None:
        self._orders_cache = None
        self._summary = None

    # -- trhové dáta a hodiny z Alpaca ----------------------------------------------------------------
    def clock(self) -> Clock:
        return self.data.clock()

    def bars(self, symbols: list[str], timeframe: str, limit: int) -> dict[str, list[Bar]]:
        return self.data.bars(symbols, timeframe, limit)

    def latest_prices(self, symbols: list[str]) -> dict[str, float]:
        return self.data.latest_prices(symbols)

    def news(self, symbols: list[str], since: datetime | None, limit: int) -> list[NewsItem]:
        return self.data.news(symbols, since, limit)

    # -- objednávky ------------------------------------------------------------------------------------
    def _position_qty(self, ticker: str) -> float:
        for p in self._call("GET", "/equity/positions", params={"ticker": ticker}) or []:
            if (p.get("instrument") or {}).get("ticker") == ticker:
                return float(p.get("quantity") or 0)
        return 0.0

    def place_stop(self, symbol: str, qty: float, stop_price: float) -> OrderResult:
        t = self.ticker(symbol)
        o = self._call("POST", "/equity/orders/stop", json={
            "ticker": t, "quantity": -abs(qty), "stopPrice": _round_price(stop_price), "timeValidity": "GOOD_TILL_CANCEL"})
        self._invalidate()
        return OrderResult(broker_id=str((o or {}).get("id", "")), status=str((o or {}).get("status", "NEW")).lower())

    def submit_bracket_buy(self, symbol: str, qty: int, stop_price: float, take_profit: float) -> OrderResult:
        """Market kúpa + hneď stop-loss. Cieľ (take_profit) stráži engine. Ak sa stop nepodarí zadať,
        pozíciu hneď zavrie – nikdy nenechá pozíciu bez ochrany."""
        t = self.ticker(symbol)
        o = self._call("POST", "/equity/orders/market", json={"ticker": t, "quantity": float(qty), "extendedHours": False})
        oid = (o or {}).get("id")
        self._invalidate()
        filled = 0.0
        for _ in range(10):  # počkaj na vyplnenie (market počas seansy ~okamžite)
            filled = self._position_qty(t)
            if filled >= qty - 1e-9:
                break
            time.sleep(1)
        if filled <= 0:
            if oid is not None:
                try:
                    self._call("DELETE", f"/equity/orders/{oid}", key_path="/equity/orders/{id}")
                except BrokerError:
                    pass
            raise BrokerError(f"{symbol}: kúpa sa nevyplnila do 10 s, objednávka zrušená.")
        try:
            self.place_stop(symbol, filled, stop_price)
        except BrokerError as e:
            log.error("T212 stop pre %s zlyhal (%s) – zatváram pozíciu", symbol, e)
            try:
                self._call("POST", "/equity/orders/market", json={"ticker": t, "quantity": -filled, "extendedHours": False})
            except BrokerError as e2:
                raise BrokerError(f"{symbol}: stop-loss sa nepodarilo zadať a ani zavrieť pozíciu ({e2}). ZAVRI RUČNE.") from e2
            raise BrokerError(f"{symbol}: stop-loss sa nepodarilo zadať ({e}), pozícia hneď zatvorená.") from e
        avg = None
        for p in self._call("GET", "/equity/positions", params={"ticker": t}) or []:
            avg = float(p.get("averagePricePaid") or 0) or None
        return OrderResult(broker_id=str(oid or ""), status="filled", filled_avg_price=avg)

    def close_position(self, symbol: str) -> OrderResult:
        t = self.ticker(symbol)
        mine = [o for o in self.open_orders() if o["symbol"] == symbol]
        for o in mine:
            try:
                self._call("DELETE", f"/equity/orders/{o['id']}", key_path="/equity/orders/{id}")
            except BrokerError:
                pass
        self._invalidate()
        qty = 0.0
        for _ in range(6):  # zrušenie stopu uvoľní kusy (quantityAvailableForTrading)
            pos = [p for p in (self._call("GET", "/equity/positions", params={"ticker": t}) or [])
                   if (p.get("instrument") or {}).get("ticker") == t]
            if not pos:
                return OrderResult("", "none")
            qty = float(pos[0].get("quantityAvailableForTrading") or 0)
            if qty >= float(pos[0].get("quantity") or 0) - 1e-9 and qty > 0:
                break
            time.sleep(1)
        if qty <= 0:
            raise BrokerError(f"{symbol}: kusy sú stále blokované čakajúcou objednávkou.")
        o = self._call("POST", "/equity/orders/market", json={"ticker": t, "quantity": -qty, "extendedHours": False})
        self._invalidate()
        return OrderResult(broker_id=str((o or {}).get("id", "")), status=str((o or {}).get("status", "NEW")).lower())

    def cancel_all_orders(self) -> None:
        for o in self.open_orders():
            try:
                self._call("DELETE", f"/equity/orders/{o['id']}", key_path="/equity/orders/{id}")
            except BrokerError:
                pass
        self._invalidate()

    def close_all(self) -> None:
        self.cancel_all_orders()
        errors = []
        for p in self.positions():
            try:
                self.close_position(p.symbol)
            except BrokerError as e:
                errors.append(f"{p.symbol}: {e}")
        if errors:
            raise BrokerError("; ".join(errors))
