"""FakeBroker: deterministický broker pre testy a pre dry režim bez Alpaca kľúčov.
Ceny generuje syntetická náhodná prechádzka; objednávky sa „plnia“ okamžite za aktuálnu cenu."""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone

from .base import Account, Bar, Clock, NewsItem, OrderResult, Position


class FakeBroker:
    account_key = "fake"

    def __init__(self, equity: float = 100_000.0, symbols: list[str] | None = None, seed: int = 7,
                 market_open: bool = True):
        self._cash = equity
        self._pos: dict[str, Position] = {}
        self._orders: list[dict] = []
        self._rng = random.Random(seed)
        self._prices: dict[str, float] = {}
        self._bars: dict[str, list[Bar]] = {}
        self.market_open = market_open
        self.now = datetime.now(timezone.utc)
        self.submitted: list[dict] = []
        self.news_items: list[NewsItem] = []
        self.last_equity = 0.0
        self.fail_close = 0  # koľkokrát má close_position zlyhať (test opakovania)
        for s in symbols or []:
            self.seed_symbol(s)

    # -- test helpers ---------------------------------------------------------
    def seed_symbol(self, symbol: str, start: float = 100.0, n: int = 120, drift: float = 0.0, vol: float = 0.002):
        bars = []
        p = start
        t = self.now - timedelta(minutes=5 * n)
        for _ in range(n):
            ch = self._rng.gauss(drift, vol)
            o = p
            c = p * (1 + ch)
            h = max(o, c) * (1 + abs(self._rng.gauss(0, vol / 2)))
            l = min(o, c) * (1 - abs(self._rng.gauss(0, vol / 2)))
            bars.append(Bar(t, o, h, l, c, 1000 + self._rng.random() * 500))
            p = c
            t += timedelta(minutes=5)
        self._bars[symbol] = bars
        self._prices[symbol] = p

    def set_bars(self, symbol: str, bars: list[Bar]):
        self._bars[symbol] = bars
        self._prices[symbol] = bars[-1].c

    def set_price(self, symbol: str, price: float):
        self._prices[symbol] = price
        if symbol in self._pos:
            p = self._pos[symbol]
            p.current_price = price
            p.market_value = price * p.qty
            p.unrealized_pl = (price - p.avg_entry) * p.qty
            p.unrealized_plpc = (price / p.avg_entry - 1) if p.avg_entry else 0

    # -- Broker protocol ------------------------------------------------------
    def account(self) -> Account:
        mv = sum(p.market_value for p in self._pos.values())
        return Account(equity=self._cash + mv, cash=self._cash, buying_power=self._cash * 2,
                       last_equity=self.last_equity)

    def positions(self) -> list[Position]:
        return list(self._pos.values())

    def clock(self) -> Clock:
        return Clock(is_open=self.market_open, now=self.now,
                     next_open=self.now + timedelta(hours=1), next_close=self.now + timedelta(hours=3))

    def bars(self, symbols, timeframe, limit):
        return {s: self._bars.get(s, [])[-limit:] for s in symbols}

    def latest_prices(self, symbols):
        return {s: self._prices[s] for s in symbols if s in self._prices}

    def news(self, symbols, since, limit):
        return [n for n in self.news_items if (since is None or n.at > since)][:limit]

    def open_orders(self):
        return list(self._orders)

    def submit_bracket_buy(self, symbol, qty, stop_price, take_profit) -> OrderResult:
        price = self._prices[symbol]
        cost = price * qty
        if cost > self._cash:
            raise RuntimeError("insufficient funds")
        self._cash -= cost
        self._pos[symbol] = Position(symbol, qty, price, price, cost, 0.0, 0.0)
        oid = f"fake-{len(self.submitted) + 1}"
        self.submitted.append({"id": oid, "symbol": symbol, "qty": qty, "side": "buy", "stop": stop_price, "tp": take_profit})
        self._orders.append({"id": oid + "-sl", "symbol": symbol, "type": "stop"})
        return OrderResult(broker_id=oid, status="filled", filled_avg_price=price)

    def close_position(self, symbol) -> OrderResult:
        if self.fail_close > 0:
            self.fail_close -= 1
            raise RuntimeError("insufficient qty available")
        p = self._pos.pop(symbol, None)
        if not p:
            return OrderResult("", "none")
        self._cash += self._prices[symbol] * p.qty
        self._orders = [o for o in self._orders if o["symbol"] != symbol]
        self.submitted.append({"id": f"fake-close-{symbol}", "symbol": symbol, "qty": p.qty, "side": "sell"})
        return OrderResult(broker_id=f"fake-close-{symbol}", status="filled", filled_avg_price=self._prices[symbol])

    def close_all(self) -> None:
        errors = []
        for s in list(self._pos):
            try:
                self.close_position(s)
            except RuntimeError as e:
                errors.append(f"{s}: {e}")
        if errors:
            raise RuntimeError("; ".join(errors))

    def cancel_all_orders(self) -> None:
        self._orders = []

