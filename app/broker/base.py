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
    trading_blocked: bool = False
    currency: str = "USD"
    last_equity: float = 0.0  # equity pri zatvorení predošlého obchodného dňa (Alpaca)


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
    account_key: str       # "alpaca:paper" | "alpaca:live" | "fake"

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


ACCOUNT_LABELS = {
    "alpaca:paper": "Alpaca paper",
    "alpaca:live": "Alpaca live",
    "fake": "Syntetické dáta",
}


def account_label(key: str) -> str:
    return ACCOUNT_LABELS.get(key, key)
