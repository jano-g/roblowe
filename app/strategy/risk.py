"""Riadenie rizika. Toto sú tvrdé mantinely – analytik ani signály ich nemôžu obísť."""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class Sizing:
    qty: int
    stop_price: float
    take_profit: float
    risk_usd: float
    reason: str


def position_size(equity: float, cash: float, price: float, atr: float, *, risk_pct: float, max_pos_pct: float,
                  atr_mult: float, reward_risk: float) -> Sizing:
    """Počet celých akcií tak, aby zásah stop-lossu stratil najviac risk_pct % equity,
    pozícia nebola väčšia než max_pos_pct % equity a zmestila sa do hotovosti."""
    if price <= 0 or atr is None or atr <= 0 or equity <= 0:
        return Sizing(0, 0, 0, 0, "chýba cena alebo ATR")
    stop_dist = atr * atr_mult
    if stop_dist <= 0:
        return Sizing(0, 0, 0, 0, "nulová vzdialenosť stopu")
    risk_usd = equity * risk_pct / 100
    qty_risk = math.floor(risk_usd / stop_dist)
    qty_cap = math.floor(equity * max_pos_pct / 100 / price)
    qty_cash = math.floor(max(cash, 0) / price)
    qty = max(0, min(qty_risk, qty_cap, qty_cash))
    stop = price - stop_dist
    tp = price + stop_dist * reward_risk
    if qty < 1:
        why = []
        if qty_risk < 1:
            why.append("riziko na obchod je menšie než 1 akcia")
        if qty_cap < 1:
            why.append("limit veľkosti pozície")
        if qty_cash < 1:
            why.append("nedostatok hotovosti")
        return Sizing(0, stop, tp, 0, "; ".join(why) or "qty < 1")
    if stop <= 0:
        return Sizing(0, stop, tp, 0, "stop pod nulou")
    return Sizing(qty, stop, tp, qty * stop_dist, f"riziko {qty * stop_dist:.0f} USD, stop {stop:.2f}, cieľ {tp:.2f}")


def daily_loss_hit(start_equity: float, equity: float, limit_pct: float) -> tuple[bool, float]:
    if start_equity <= 0:
        return False, 0.0
    pnl_pct = (equity / start_equity - 1) * 100
    return pnl_pct <= -abs(limit_pct), pnl_pct

