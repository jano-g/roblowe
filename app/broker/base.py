"""Rozhranie brokera. Implementácie: AlpacaBroker (REST), FakeBroker (testy / dry mode bez kľúčov)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass
class Account:
    equity: float
    cash: float
    buying_power: float
    daytrade_count: int = 0
    pattern_day_trader: bool = False
    trading_blocked: bool = False
    currency: str = "USD"


@dataclass
class Position:
    symbol: str
    qty: float
    avg_entry: float
    current_price: float
    market_value: float
    unrealized_pl: float
    unrealized_plpc: float


@dataclass
class Bar:
    t: datetime
    o: float
    h: float
    l: float
    c: float
    v: float


@dataclass
class Clock:
    is_open: bool
    now: datetime
    next_open: datetime
    next_close: datetime


@dataclass
class NewsItem:
    id: str
    at: datetime
    headline: str
    summary: str
    symbols: list[str] = field(default_factory=list)
    source: str = ""
    url: str = ""


@dataclass
class OrderResult:
    broker_id: str
    status: str
    filled_avg_price: float | None = None


class Broker(Protocol):
    def account(self) -> Account: ...
    def positions(self) -> list[Position]: ...
    def clock(self) -> Clock: ...
    def bars(self, symbols: list[str], timeframe: str, limit: int) -> dict[str, list[Bar]]: ...
    def latest_prices(self, symbols: list[str]) -> dict[str, float]: ...
    def news(self, symbols: list[str], since: datetime | None, limit: int) -> list[NewsItem]: ...
    def open_orders(self) -> list[dict]: ...
    def submit_bracket_buy(self, symbol: str, qty: int, stop_price: float, take_profit: float) -> OrderResult: ...
    def close_position(self, symbol: str) -> OrderResult: ...
    def close_all(self) -> None: ...
    def cancel_all_orders(self) -> None: ...
