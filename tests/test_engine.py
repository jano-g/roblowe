import json
from datetime import datetime, timedelta, timezone

from app import db, settings
from app.broker.base import Bar, NewsItem
from app.broker.fake import FakeBroker
from app.strategy.analyst import SymbolView, parse_analysis
from app.strategy.engine import Engine

NOW = datetime(2026, 9, 18, 15, 0, tzinfo=timezone.utc)  # 11:00 New York, burza otvorená


def trend_bars(step, n=80, start=100.0, at=NOW):
    out, p = [], start
    for i in range(n):
        o, c = p, p + step
        out.append(Bar(at - timedelta(minutes=5 * (n - i)), o, max(o, c) + 0.3, min(o, c) - 0.3, c, 1000))
        p = c
    return out


def make_engine(mode="paper", **kw):
    fb = FakeBroker(equity=100_000)
    fb.now = NOW
    fb._clock_close = NOW + timedelta(hours=5)
    fb.clock = lambda: type("C", (), {"is_open": True, "now": NOW, "next_open": NOW + timedelta(days=1),
                                       "next_close": NOW + timedelta(hours=5)})()
    settings.set_many({"watchlist": "AAA,BBB", "agent_enabled": "1", "analyst_enabled": "0", **kw}, [], "test")
    eng = Engine(fb, None, mode, notify=lambda t, m: None, now=lambda: NOW)
    return fb, eng


def test_buys_strong_uptrend_and_records_decision():
    fb, eng = make_engine()
    fb.set_bars("AAA", trend_bars(0.4))
    fb.set_bars("BBB", trend_bars(-0.4))
    rep = eng.cycle()
    assert rep.status == "ok"
    buys = [o for o in fb.submitted if o["side"] == "buy"]
    assert [o["symbol"] for o in buys] == ["AAA"]
    assert buys[0]["stop"] < 100 + 0.4 * 80 < buys[0]["tp"]
    d = db.rows("SELECT * FROM decisions WHERE action='buy'")
    assert len(d) == 1 and d[0]["symbol"] == "AAA"
    assert db.row("SELECT * FROM orders")["kind"] == "entry"
    assert db.row("SELECT * FROM days")["start_equity"] == 100_000


def test_dry_mode_sends_nothing():
    fb, eng = make_engine(mode="dry")
    fb.set_bars("AAA", trend_bars(0.4))
    eng.cycle()
    assert fb.submitted == []
    assert db.row("SELECT status FROM orders")["status"] == "dry"


def test_agent_disabled_skips_entries():
    fb, eng = make_engine(agent_enabled="0")
    fb.set_bars("AAA", trend_bars(0.4))
    eng.cycle()
    assert fb.submitted == []
    assert db.row("SELECT reason FROM decisions WHERE symbol='AAA'")["reason"] == "agent je vypnutý"


def test_max_positions():
    fb, eng = make_engine(max_positions="1")
    fb.set_bars("AAA", trend_bars(0.4))
    fb.set_bars("BBB", trend_bars(0.4))
    eng.cycle()
    assert len([o for o in fb.submitted if o["side"] == "buy"]) == 1
    assert "max. počet" in db.row("SELECT reason FROM decisions WHERE action='skip'")["reason"]


def test_pdt_blocks_entry_under_25k():
    fb2, eng2 = make_engine()
    fb2._cash = 20_000
    fb2.daytrade_count = 3
    fb2.set_bars("AAA", trend_bars(0.4))
    eng2.cycle()
    assert fb2.submitted == []
    assert "PDT" in db.row("SELECT reason FROM decisions WHERE symbol='AAA' ORDER BY id DESC")["reason"]


def test_daily_loss_halts_and_flattens():
    fb, eng = make_engine()
    fb.set_bars("AAA", trend_bars(0.4))
    eng.cycle()
    assert len(fb.positions()) == 1
    fb.set_price("AAA", 50)  # veľká strata
    rep = eng.cycle()
    assert rep.status == "halted"
    assert fb.positions() == []
    assert db.row("SELECT halted FROM days")["halted"] == 1
    # ďalší cyklus v zastavenom dni nič nekupuje
    fb.set_bars("AAA", trend_bars(0.4))
    fb.set_price("AAA", 132)
    rep2 = eng.cycle()
    assert rep2.status == "halted" and len(fb.submitted) == 2  # buy + close, nič nové


def test_flatten_before_close():
    fb, eng = make_engine()
    fb.set_bars("AAA", trend_bars(0.4))
    eng.cycle()
    fb.clock = lambda: type("C", (), {"is_open": True, "now": NOW, "next_open": NOW + timedelta(days=1),
                                       "next_close": NOW + timedelta(minutes=5)})()
    rep = eng.cycle()
    assert rep.status == "flattened"
    assert fb.positions() == []
    assert db.row("SELECT flattened FROM days")["flattened"] == 1


def test_negative_news_vetoes_entry_and_exits():
    fb, eng = make_engine()
    fb.set_bars("AAA", trend_bars(0.4))
    eng.news_views["AAA"] = SymbolView(-0.8, 0.9, "žaloba", False, NOW)
    eng.cycle()
    assert fb.submitted == []
    r = db.row("SELECT reason FROM decisions WHERE symbol='AAA'")["reason"]
    assert "správy" in r or "skóre" in r
    # pozitívna správa zdvihne skóre na kúpu aj pri slabšom trende
    fb2, eng2 = make_engine()
    fb2.set_bars("AAA", trend_bars(0.15))
    eng2.news_views["AAA"] = SymbolView(0.9, 0.9, "výsledky nad očakávania", False, NOW)
    eng2.cycle()
    assert [o["symbol"] for o in fb2.submitted] == ["AAA"]


def test_parse_analysis_clamps():
    a = parse_analysis({"market_note": "ok", "symbols": [{"symbol": "aaa", "sentiment": 5, "confidence": -1,
                                                          "catalyst": "x", "stale": False}]}, model="m")
    assert a.symbols["AAA"].sentiment == 1.0 and a.symbols["AAA"].confidence == 0.0


def test_panic_closes_and_disables():
    fb, eng = make_engine()
    fb.set_bars("AAA", trend_bars(0.4))
    eng.cycle()
    n = eng.panic("jano")
    assert n == 1 and fb.positions() == []
    assert settings.get("agent_enabled") is False
    assert db.row("SELECT halted FROM days")["halted"] == 1


def test_dry_mode_does_not_reenter_same_symbol():
    fb, eng = make_engine(mode="dry")
    fb.set_bars("AAA", trend_bars(0.4))
    eng.cycle()
    eng.cycle()
    assert db.q1("SELECT COUNT(*) n FROM orders WHERE kind='entry'")["n"] == 1
    assert "dry pozícia" in db.row("SELECT reason FROM decisions WHERE symbol='AAA' ORDER BY id DESC")["reason"]


def test_daily_summary_sent_once_after_close():
    sent = []
    fb = FakeBroker(equity=100_000)
    fb.now = NOW
    clock_open = type("C", (), {"is_open": True, "now": NOW, "next_open": NOW + timedelta(days=1), "next_close": NOW + timedelta(hours=5)})()
    clock_closing = type("C", (), {"is_open": True, "now": NOW, "next_open": NOW + timedelta(days=1), "next_close": NOW + timedelta(minutes=5)})()
    clock_closed = type("C", (), {"is_open": False, "now": NOW + timedelta(hours=6), "next_open": NOW + timedelta(days=1), "next_close": NOW + timedelta(days=1, hours=6)})()
    fb.clock = lambda: clock_open
    settings.set_many({"watchlist": "AAA", "agent_enabled": "1", "analyst_enabled": "0", "notify_mode": "daily"}, [], "test")
    eng = Engine(fb, None, "paper", notify=lambda t, m: sent.append((t, m)), now=lambda: NOW)
    fb.set_bars("AAA", trend_bars(0.4))
    eng.cycle()
    assert sent == []  # notify_mode=daily → žiadny push pri obchode
    fb.set_price("AAA", fb.latest_prices(["AAA"])["AAA"] + 2)
    fb.clock = lambda: clock_closing
    eng.cycle()  # flatten
    fb.clock = lambda: clock_closed
    eng.cycle()
    assert len(sent) == 1
    title, text = sent[0]
    assert title.startswith("Roblowe: deň") and "1 vstupov, 1 výstupov" in text and "1 v pluse" in text
    assert db.row("SELECT summary_sent FROM days")["summary_sent"] == 1
    eng.cycle()
    assert len(sent) == 1  # neposiela znova


def test_notify_mode_trade_sends_per_trade():
    sent = []
    fb, eng = make_engine(notify_mode="trade")
    eng.notify = lambda t, m: sent.append(t)
    fb.set_bars("AAA", trend_bars(0.4))
    eng.cycle()
    assert any("kúpa AAA" in t for t in sent)


def test_day_base_is_broker_last_equity_and_syncs():
    fb, eng = make_engine()
    # deň založený skôr so zlým základom (napr. zo syntetických dát)
    db.run("INSERT INTO days(account, date, start_equity, created_at) VALUES ('fake', '2026-09-18', 100000, 'x')")
    fb._cash = 100_763
    fb.last_equity = 100_960  # včerajšie zatvorenie
    fb.set_bars("AAA", trend_bars(-0.1))
    rep = eng.cycle()
    assert db.row("SELECT start_equity FROM days")["start_equity"] == 100_960
    assert rep.day_pnl_pct == round((100_763 / 100_960 - 1) * 100, 2)  # −0,20 %, nie +0,76 %


def test_flatten_retries_until_positions_closed():
    fb, eng = make_engine()
    fb.set_bars("AAA", trend_bars(0.4))
    eng.cycle()
    assert len(fb.positions()) == 1
    fb.clock = lambda: type("C", (), {"is_open": True, "now": NOW, "next_open": NOW + timedelta(days=1),
                                       "next_close": NOW + timedelta(minutes=8)})()
    fb.fail_close = 1
    rep = eng.cycle()
    assert len(fb.positions()) == 1 and "Stále otvorené" in rep.notes[0]
    assert db.row("SELECT flattened FROM days")["flattened"] == 0
    eng.cycle()  # ďalší cyklus to dokončí
    assert fb.positions() == []
    assert db.row("SELECT flattened FROM days")["flattened"] == 1


def test_leftover_position_from_previous_day_is_closed():
    from app.broker.base import Position
    fb, eng = make_engine()
    fb.set_bars("OLD", trend_bars(0.4))
    fb._pos["OLD"] = Position("OLD", 10, 120, 130, 1300, 100, 0.08)
    eng.cycle()
    assert "OLD" not in {p.symbol for p in fb.positions()}
    assert "zostatok" in db.row("SELECT reason FROM decisions WHERE symbol='OLD' AND action='sell'")["reason"]


def test_stats_are_separate_per_account():
    fb, eng = make_engine()
    fb.set_bars("AAA", trend_bars(-0.1))
    eng.cycle()  # účet "fake", 100 000
    fb2 = FakeBroker(equity=5_732)
    fb2.account_key = "trading212:demo"
    fb2.clock = fb.clock
    fb2.set_bars("AAA", trend_bars(-0.1))
    eng2 = Engine(fb2, None, "paper", notify=lambda t, m: None, now=lambda: NOW)
    rep = eng2.cycle()
    assert rep.day_pnl_pct == 0.0  # nie −94 %
    rows = {r["account"]: r["start_equity"] for r in db.rows("SELECT account, start_equity FROM days")}
    assert rows == {"fake": 100_000, "trading212:demo": 5_732}
    assert {r["account"] for r in db.rows("SELECT DISTINCT account FROM equity")} == {"fake", "trading212:demo"}
