"""Technické indikátory nad sviečkami (čistý Python, bez numpy) a technické skóre v <-1, 1>.

Skóre kombinuje: trend (EMA9 vs EMA21), momentum (RSI14), polohu voči VWAP dňa a relatívny objem.
Je to zámerne jednoduchá, čitateľná heuristika – nie „tajný“ algoritmus. Backtest si sprav
predtým, než jej zveríš reálne peniaze (README → Riziká)."""
from __future__ import annotations

from dataclasses import dataclass

from ..broker.base import Bar


def ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for a, b in zip(closes[:-1], closes[1:]):
        d = b - a
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for g, l in zip(gains[period:], losses[period:]):
        avg_g = (avg_g * (period - 1) + g) / period
        avg_l = (avg_l * (period - 1) + l) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return 100 - 100 / (1 + rs)


def atr(bars: list[Bar], period: int = 14) -> float | None:
    if len(bars) < period + 1:
        return None
    trs = []
    for prev, cur in zip(bars[:-1], bars[1:]):
        trs.append(max(cur.h - cur.l, abs(cur.h - prev.c), abs(cur.l - prev.c)))
    a = sum(trs[:period]) / period
    for tr in trs[period:]:
        a = (a * (period - 1) + tr) / period
    return a


def vwap(bars: list[Bar]) -> float | None:
    """VWAP zo sviečok toho istého (posledného) dňa."""
    if not bars:
        return None
    last_day = bars[-1].t.date()
    day = [b for b in bars if b.t.date() == last_day]
    vol = sum(b.v for b in day)
    if vol <= 0:
        return None
    return sum(((b.h + b.l + b.c) / 3) * b.v for b in day) / vol


@dataclass
class Tech:
    price: float
    ema_fast: float
    ema_slow: float
    rsi: float | None
    atr: float | None
    vwap: float | None
    rel_volume: float
    score: float
    notes: list[str]


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def analyze(bars: list[Bar], min_bars: int = 30) -> Tech | None:
    if len(bars) < min_bars:
        return None
    closes = [b.c for b in bars]
    price = closes[-1]
    e9 = ema(closes, 9)[-1]
    e21 = ema(closes, 21)[-1]
    r = rsi(closes)
    a = atr(bars)
    vw = vwap(bars)
    vols = [b.v for b in bars[-21:-1]]
    avg_v = sum(vols) / len(vols) if vols else 0
    rel_v = (bars[-1].v / avg_v) if avg_v > 0 else 1.0

    notes: list[str] = []
    # 1) trend: relatívny rozdiel EMA v jednotkách ATR (normalizované)
    if a and a > 0:
        trend = _clamp((e9 - e21) / (a * 1.0))
    else:
        trend = _clamp((e9 - e21) / max(price, 1e-9) * 200)
    notes.append("trend hore" if trend > 0.15 else "trend dole" if trend < -0.15 else "bez trendu")

    # 2) momentum: RSI 50 = 0; preťaženie nad 75 / pod 25 skóre tlmí (nekupuj vrchol)
    mom = 0.0
    if r is not None:
        mom = _clamp((r - 50) / 25)
        if r > 75:
            mom -= (r - 75) / 25
            notes.append(f"RSI prekúpené ({r:.0f})")
        elif r < 25:
            mom += (25 - r) / 25
            notes.append(f"RSI prepredané ({r:.0f})")

    # 3) poloha voči VWAP: nad VWAP = kupujúci majú kontrolu
    vw_s = 0.0
    if vw and a and a > 0:
        vw_s = _clamp((price - vw) / (a * 2))
        notes.append("nad VWAP" if vw_s > 0 else "pod VWAP")

    # 4) objem: potvrdenie (len zosilňuje, samo nič nerozhoduje)
    vol_boost = _clamp((rel_v - 1) / 2, -0.3, 0.5)
    if rel_v > 1.5:
        notes.append(f"zvýšený objem ({rel_v:.1f}×)")

    raw = 0.45 * trend + 0.30 * mom + 0.25 * vw_s
    score = _clamp(raw * (1 + vol_boost) if raw > 0 else raw)
    return Tech(price=price, ema_fast=e9, ema_slow=e21, rsi=r, atr=a, vwap=vw, rel_volume=rel_v,
                score=round(score, 3), notes=notes)
