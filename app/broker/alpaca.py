"""Alpaca Markets REST (trading + market data + news) cez httpx. Bez alpaca-py (pandas), aby
kontajner ostal ľahký. Dokumentácia: https://docs.alpaca.markets/reference"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from .. import config
from .base import Account, Bar, Clock, NewsItem, OrderResult, Position

log = logging.getLogger("roblowe.alpaca")


class BrokerError(Exception):
    pass


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _round_price(p: float) -> float:
    # Alpaca: ceny ≥ 1 USD na 2 desatinné, pod 1 USD na 4.
    return round(p, 2) if p >= 1 else round(p, 4)


class AlpacaBroker:
    def __init__(self, key_id: str, secret: str, trading_url: str, data_url: str = config.ALPACA_DATA_URL,
                 feed: str = config.ALPACA_FEED, timeout: float = 15.0):
        headers = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret, "Accept": "application/json"}
        self._t = httpx.Client(base_url=trading_url, headers=headers, timeout=timeout)
        self._d = httpx.Client(base_url=data_url, headers=headers, timeout=timeout)
        self.feed = feed

    # -- helpers -------------------------------------------------------------
    def _call(self, client: httpx.Client, method: str, path: str, **kw) -> dict | list | None:
        try:
            r = client.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise BrokerError(f"Alpaca spojenie zlyhalo: {e.__class__.__name__}") from e
        if r.status_code >= 400:
            # telo neposielame do UI, len do logu (bez kľúčov)
            log.warning("Alpaca %s %s -> %s %s", method, path, r.status_code, r.text[:300])
            raise BrokerError(f"Alpaca odmietla požiadavku ({r.status_code}).")
        if r.status_code == 204 or not r.content:
            return None
        return r.json()

    # -- account / positions --------------------------------------------------
    def account(self) -> Account:
        a = self._call(self._t, "GET", "/v2/account")
        return Account(
            equity=float(a["equity"]),
            cash=float(a["cash"]),
            buying_power=float(a["buying_power"]),
            daytrade_count=int(a.get("daytrade_count") or 0),
            pattern_day_trader=bool(a.get("pattern_day_trader")),
            trading_blocked=bool(a.get("trading_blocked") or a.get("account_blocked")),
            currency=a.get("currency", "USD"),
        )

    def positions(self) -> list[Position]:
        out = []
        for p in self._call(self._t, "GET", "/v2/positions") or []:
            out.append(Position(
                symbol=p["symbol"], qty=float(p["qty"]), avg_entry=float(p["avg_entry_price"]),
                current_price=float(p.get("current_price") or 0), market_value=float(p.get("market_value") or 0),
                unrealized_pl=float(p.get("unrealized_pl") or 0), unrealized_plpc=float(p.get("unrealized_plpc") or 0),
            ))
        return out

    def clock(self) -> Clock:
        c = self._call(self._t, "GET", "/v2/clock")
        return Clock(is_open=bool(c["is_open"]), now=_dt(c["timestamp"]),
                     next_open=_dt(c["next_open"]), next_close=_dt(c["next_close"]))

    def open_orders(self) -> list[dict]:
        return self._call(self._t, "GET", "/v2/orders", params={"status": "open", "limit": 200}) or []

    # -- market data -----------------------------------------------------------
    def bars(self, symbols: list[str], timeframe: str, limit: int) -> dict[str, list[Bar]]:
        out: dict[str, list[Bar]] = {s: [] for s in symbols}
        if not symbols:
            return out
        params = {"symbols": ",".join(symbols), "timeframe": timeframe, "limit": max(limit * len(symbols), limit),
                  "feed": self.feed, "sort": "desc", "adjustment": "raw"}
        page = None
        for _ in range(5):
            if page:
                params["page_token"] = page
            data = self._call(self._d, "GET", "/v2/stocks/bars", params=params) or {}
            for sym, items in (data.get("bars") or {}).items():
                for b in items:
                    out.setdefault(sym, []).append(Bar(_dt(b["t"]), float(b["o"]), float(b["h"]), float(b["l"]),
                                                       float(b["c"]), float(b["v"])))
            page = data.get("next_page_token")
            if not page or all(len(v) >= limit for v in out.values()):
                break
        for sym in out:
            out[sym] = sorted(out[sym], key=lambda b: b.t)[-limit:]
        return out

    def latest_prices(self, symbols: list[str]) -> dict[str, float]:
        if not symbols:
            return {}
        data = self._call(self._d, "GET", "/v2/stocks/trades/latest",
                          params={"symbols": ",".join(symbols), "feed": self.feed}) or {}
        return {s: float(t["p"]) for s, t in (data.get("trades") or {}).items()}

    def news(self, symbols: list[str], since: datetime | None, limit: int) -> list[NewsItem]:
        params: dict = {"symbols": ",".join(symbols), "limit": min(limit, 50), "sort": "desc", "include_content": "false"}
        if since:
            params["start"] = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        data = self._call(self._d, "GET", "/v1beta1/news", params=params) or {}
        out = []
        for n in data.get("news") or []:
            out.append(NewsItem(id=str(n["id"]), at=_dt(n["created_at"]), headline=n.get("headline") or "",
                                summary=n.get("summary") or "", symbols=list(n.get("symbols") or []),
                                source=n.get("source") or "", url=n.get("url") or ""))
        return out

    # -- orders -------------------------------------------------------------------
    def submit_bracket_buy(self, symbol: str, qty: int, stop_price: float, take_profit: float) -> OrderResult:
        body = {
            "symbol": symbol, "qty": str(int(qty)), "side": "buy", "type": "market", "time_in_force": "day",
            "order_class": "bracket",
            "take_profit": {"limit_price": str(_round_price(take_profit))},
            "stop_loss": {"stop_price": str(_round_price(stop_price))},
        }
        o = self._call(self._t, "POST", "/v2/orders", json=body)
        return OrderResult(broker_id=o["id"], status=o.get("status", "accepted"),
                           filled_avg_price=float(o["filled_avg_price"]) if o.get("filled_avg_price") else None)

    def close_position(self, symbol: str) -> OrderResult:
        # Najprv zruš bracket „nohy“ (TP/SL), inak Alpaca odmietne uzavretie pre held qty.
        for od in self.open_orders():
            if od.get("symbol") == symbol:
                try:
                    self._call(self._t, "DELETE", f"/v2/orders/{od['id']}")
                except BrokerError:
                    pass
        o = self._call(self._t, "DELETE", f"/v2/positions/{symbol}")
        return OrderResult(broker_id=(o or {}).get("id", ""), status=(o or {}).get("status", "accepted"))

    def close_all(self) -> None:
        self._call(self._t, "DELETE", "/v2/positions", params={"cancel_orders": "true"})

    def cancel_all_orders(self) -> None:
        self._call(self._t, "DELETE", "/v2/orders")
