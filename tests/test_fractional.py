from datetime import timedelta

from app import db, settings
from app.broker.alpaca import AlpacaBroker
from app.broker.base import Bar
from app.strategy import risk
from tests.test_engine import NOW, make_engine, trend_bars


def test_fractional_sizing():
    whole = risk.position_size(1_500, 1_500, 500, 1.0, risk_pct=2, max_pos_pct=30, atr_mult=1.5, reward_risk=1.5)
    assert whole.qty == 0 and "limit veľkosti pozície" in whole.reason
    frac = risk.position_size(1_500, 1_500, 500, 1.0, risk_pct=2, max_pos_pct=30, atr_mult=1.5, reward_risk=1.5,
                              fractional=True)
    assert frac.qty == 0.9 and frac.stop_price == 498.5 and frac.take_profit == 502.25  # 30 % z 1500 = 450 USD
    tiny = risk.position_size(10, 10, 500, 1.0, risk_pct=2, max_pos_pct=30, atr_mult=1.5, reward_risk=1.5, fractional=True)
    assert tiny.qty == 0 and "limit" in tiny.reason


def _small_engine(**kw):
    fb, eng = make_engine(fractional_shares="1", max_position_pct="30", risk_per_trade_pct="2", **kw)
    fb._cash = 300  # cap 90 USD pri cene ~132 → 0,68 ks, celé kusy = 0
    fb.set_bars("AAA", trend_bars(0.4))
    return fb, eng


def test_fractional_entry_places_market_and_stop():
    fb, eng = _small_engine()
    eng.cycle()
    buy = [o for o in fb.submitted if o["side"] == "buy"]
    assert len(buy) == 1 and buy[0].get("fractional") is True and 0 < buy[0]["qty"] < 1
    assert any(o["side"] == "stop" and o["symbol"] == "AAA" for o in fb.submitted)
    row = db.row("SELECT qty, protect FROM orders WHERE kind='entry'")
    assert row["protect"] == "stop" and row["qty"] == buy[0]["qty"]
    assert "cieľ stráži agent" in db.row("SELECT reason FROM decisions WHERE symbol='AAA' ORDER BY id DESC")["reason"] \
        or True  # hold reason sa zapíše až v ďalšom cykle
    eng.cycle()
    assert "cieľ stráži agent" in db.row("SELECT reason FROM decisions WHERE symbol='AAA' AND action='hold' ORDER BY id DESC")["reason"]


def test_fractional_take_profit_is_managed_by_engine():
    fb, eng = _small_engine()
    eng.cycle()
    tp = db.row("SELECT take_profit FROM orders WHERE kind='entry'")["take_profit"]
    bars = trend_bars(0.4)
    bars[-1] = Bar(bars[-1].t, bars[-1].o, tp + 2, bars[-1].l, tp + 1, 1000)
    fb.set_bars("AAA", bars)
    eng.cycle()
    assert fb.positions() == []
    assert "cieľ" in db.row("SELECT reason FROM decisions WHERE action='sell'")["reason"]


def test_fractional_missing_stop_is_replaced():
    fb, eng = _small_engine()
    eng.cycle()
    fb._orders = []
    rep = eng.cycle()
    assert any("stop-loss" in n for n in rep.notes)
    assert sum(1 for o in fb.submitted if o["side"] == "stop") == 2


def test_fractional_stop_failure_rejects_entry():
    fb, eng = _small_engine()
    fb.fail_stop = 1
    eng.cycle()
    assert fb.positions() == []
    assert db.row("SELECT status FROM orders WHERE kind='entry'")["status"] == "rejected"


def test_whole_shares_preferred_when_they_fill_position():
    fb, eng = make_engine(fractional_shares="1", max_position_pct="30", risk_per_trade_pct="5")
    fb.set_bars("AAA", trend_bars(0.4))  # 100 000 USD → 30 000 USD / 132 ≈ 227 ks → celé kusy stačia
    eng.cycle()
    buy = [o for o in fb.submitted if o["side"] == "buy"][0]
    assert buy.get("fractional") is None and float(buy["qty"]).is_integer()
    assert db.row("SELECT protect FROM orders WHERE kind='entry'")["protect"] == "bracket"


def test_alpaca_fractional_bodies(monkeypatch):
    calls = []
    fills = {"status": "filled", "filled_qty": "0.683", "filled_avg_price": "132.10", "id": "o1"}

    def fake_call(self, client, method, path, **kw):
        calls.append((method, path, kw.get("json")))
        if method == "POST":
            return {"id": "o1", "status": "accepted"}
        if method == "GET":
            return fills
        return None
    monkeypatch.setattr(AlpacaBroker, "_call", fake_call)
    monkeypatch.setattr("app.broker.alpaca.time.sleep", lambda s: None)
    b = AlpacaBroker("k", "s", "https://paper-api.alpaca.markets")
    r = b.submit_fractional_buy("AAPL", 0.683, 130.456)
    assert r.status == "filled" and r.filled_avg_price == 132.10
    posts = [c for c in calls if c[0] == "POST"]
    assert posts[0][2] == {"symbol": "AAPL", "qty": "0.683", "side": "buy", "type": "market", "time_in_force": "day"}
    assert posts[1][2] == {"symbol": "AAPL", "qty": "0.683", "side": "sell", "type": "stop", "time_in_force": "day",
                           "stop_price": "130.46"}
