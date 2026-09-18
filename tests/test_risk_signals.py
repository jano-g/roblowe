from datetime import datetime, timedelta, timezone

from app.broker.base import Bar
from app.strategy import risk, signals


def _bars(n=60, start=100.0, step=0.3):
    t = datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)
    out = []
    p = start
    for i in range(n):
        o = p
        c = p + step
        out.append(Bar(t + timedelta(minutes=5 * i), o, max(o, c) + 0.2, min(o, c) - 0.2, c, 1000 + (500 if i == n - 1 else 0)))
        p = c
    return out


def test_position_size_respects_risk_and_caps():
    s = risk.position_size(100_000, 100_000, 100, 1.0, risk_pct=0.5, max_pos_pct=10, atr_mult=1.5, reward_risk=1.5)
    # riziko 500 USD / stop 1.5 → 333 ks, cap 10 % = 100 ks
    assert s.qty == 100
    assert s.stop_price == 98.5
    assert s.take_profit == 102.25


def test_position_size_cash_limit_and_zero():
    s = risk.position_size(100_000, 500, 100, 1.0, risk_pct=0.5, max_pos_pct=10, atr_mult=1.5, reward_risk=1.5)
    assert s.qty == 5
    s0 = risk.position_size(1_000, 1_000, 500, 10.0, risk_pct=0.5, max_pos_pct=10, atr_mult=1.5, reward_risk=1.5)
    assert s0.qty == 0 and "riziko" in s0.reason


def test_daily_loss_and_pdt():
    hit, pct = risk.daily_loss_hit(100_000, 97_900, 2)
    assert hit and round(pct, 1) == -2.1
    assert not risk.daily_loss_hit(100_000, 99_000, 2)[0]
    assert risk.pdt_blocks_entry(20_000, 3, True)
    assert not risk.pdt_blocks_entry(30_000, 3, True)
    assert not risk.pdt_blocks_entry(20_000, 3, False)


def test_signals_uptrend_positive_downtrend_negative():
    up = signals.analyze(_bars(step=0.3))
    down = signals.analyze(_bars(step=-0.3))
    assert up and down
    assert up.score > 0.3
    assert down.score < -0.3
    assert up.atr and up.atr > 0
    assert signals.analyze(_bars(n=10)) is None
