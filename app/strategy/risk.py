"""Riadenie rizika. Toto sú tvrdé mantinely – analytik ani signály ich nemôžu obísť."""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class Sizing:
    qty: float
    stop_price: float
    take_profit: float
    risk_usd: float
    reason: str


MIN_NOTIONAL_USD = 5.0     # Alpaca dovolí od 1 USD; pod 5 USD nemá obchod zmysel (spread)
FRACTION_STEP = 0.001      # zaokrúhlenie zlomkových kusov


def position_size(equity: float, cash: float, price: float, atr: float, *, risk_pct: float, max_pos_pct: float,
                  atr_mult: float, reward_risk: float, fractional: bool = False) -> Sizing:
    """Počet akcií tak, aby zásah stop-lossu stratil najviac risk_pct % equity, pozícia nebola väčšia
    než max_pos_pct % equity a zmestila sa do hotovosti. Celé kusy, alebo zlomky na 0,001 (fractional)."""
    if price <= 0 or atr is None or atr <= 0 or equity <= 0:
        return Sizing(0, 0, 0, 0, "chýba cena alebo ATR")
    stop_dist = atr * atr_mult
    if stop_dist <= 0:
        return Sizing(0, 0, 0, 0, "nulová vzdialenosť stopu")
    risk_usd = equity * risk_pct / 100
    q_risk = risk_usd / stop_dist
    q_cap = equity * max_pos_pct / 100 / price
    q_cash = max(cash, 0) / price
    raw = max(0.0, min(q_risk, q_cap, q_cash))
    stop = price - stop_dist
    tp = price + stop_dist * reward_risk
    if fractional:
        qty: float = math.floor(raw / FRACTION_STEP) * FRACTION_STEP
        qty = round(qty, 3)
        if qty * price < MIN_NOTIONAL_USD:
            why = []
            if q_risk * price < MIN_NOTIONAL_USD:
                why.append("riziko na obchod je príliš malé")
            if q_cap * price < MIN_NOTIONAL_USD:
                why.append("limit veľkosti pozície")
            if q_cash * price < MIN_NOTIONAL_USD:
                why.append("nedostatok hotovosti")
            return Sizing(0, stop, tp, 0, "; ".join(why) or f"pozícia pod {MIN_NOTIONAL_USD:.0f} USD")
    else:
        qty = math.floor(raw)
        if qty < 1:
            why = []
            if q_risk < 1:
                why.append("riziko na obchod je menšie než 1 akcia")
            if q_cap < 1:
                why.append("limit veľkosti pozície")
            if q_cash < 1:
                why.append("nedostatok hotovosti")
            return Sizing(0, stop, tp, 0, "; ".join(why) or "qty < 1")
    if stop <= 0:
        return Sizing(0, stop, tp, 0, "stop pod nulou")
    return Sizing(qty, stop, tp, qty * stop_dist,
                  f"riziko {qty * stop_dist:.0f} USD, stop {stop:.2f}, cieľ {tp:.2f}")


def daily_loss_hit(start_equity: float, equity: float, limit_pct: float) -> tuple[bool, float]:
    if start_equity <= 0:
        return False, 0.0
    pnl_pct = (equity / start_equity - 1) * 100
    return pnl_pct <= -abs(limit_pct), pnl_pct

