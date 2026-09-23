from datetime import datetime, timedelta, timezone

from app import db, settings
from app.broker.fake import FakeBroker
from app.strategy.analyst import SymbolView
from tests.test_engine import NOW, trend_bars

PW = "heslo-heslo-123"


def _fake(account_key, equity, native_bracket=True, pdt_applies=True):
    fb = FakeBroker(equity=equity)
    fb.account_key = account_key
    fb.native_bracket = native_bracket
    fb.pdt_applies = pdt_applies
    fb.clock = lambda: type("C", (), {"is_open": True, "now": NOW, "next_open": NOW + timedelta(days=1),
                                       "next_close": NOW + timedelta(hours=5)})()
    fb.set_bars("AAA", trend_bars(0.4))
    return fb


def _setup_both(monkeypatch):
    from app import scheduler as sched_mod
    alp = _fake("alpaca:paper", 100_000)
    t212 = _fake("trading212:demo", 5_732, native_bracket=False, pdt_applies=False)
    monkeypatch.setattr(sched_mod, "AlpacaBroker", lambda *a, **k: alp)
    monkeypatch.setattr(sched_mod, "Trading212Broker", lambda *a, **k: t212)
    settings.set_many({"broker_name": "both", "trading_mode": "paper", "alpaca_paper_key_id": "PKX1234567890",
                       "alpaca_paper_secret": "s", "t212_demo_key_id": "T212KEY12345", "t212_demo_secret": "s"},
                      [], "test", allow_broker=True)
    settings.set_many({"watchlist": "AAA", "agent_enabled": "1", "analyst_enabled": "0"}, [], "test")
    sched_mod.scheduler.rebuild()
    return sched_mod.scheduler, alp, t212


def test_both_runs_two_engines_with_shared_news(monkeypatch):
    sch, alp, t212 = _setup_both(monkeypatch)
    try:
        assert [e.account for e in sch.engines] == ["alpaca:paper", "trading212:demo"]
        assert sch.engines[0].news is sch.engines[1].news
        sch.engines[0].news_views["AAA"] = SymbolView(0.8, 0.9, "x", False, NOW)
        assert sch.engines[1].news_views["AAA"].catalyst == "x"
        for e in sch.engines:
            e._now = lambda: NOW
        reports = sch.run_cycle_now()
        assert [r["account"] for r in reports] == ["alpaca:paper", "trading212:demo"]
        assert [o["symbol"] for o in alp.submitted if o["side"] == "buy"] == ["AAA"]
        assert [o["symbol"] for o in t212.submitted if o["side"] == "buy"] == ["AAA"]
        days = {r["account"]: r["start_equity"] for r in db.rows("SELECT account, start_equity FROM days")}
        assert days == {"alpaca:paper": 100_000, "trading212:demo": 5_732}
        assert {r["account"] for r in db.rows("SELECT DISTINCT account FROM orders")} == {"alpaca:paper", "trading212:demo"}
    finally:
        settings.set_many({"broker_name": "alpaca", "trading_mode": "dry"}, ["alpaca_paper_key_id", "alpaca_paper_secret",
                          "t212_demo_key_id", "t212_demo_secret"], "test", allow_broker=True)
        sch.rebuild()


def test_both_without_t212_keys_runs_alpaca_only(monkeypatch):
    from app import scheduler as sched_mod
    alp = _fake("alpaca:paper", 100_000)
    monkeypatch.setattr(sched_mod, "AlpacaBroker", lambda *a, **k: alp)
    settings.set_many({"broker_name": "both", "trading_mode": "paper", "alpaca_paper_key_id": "PKX1234567890",
                       "alpaca_paper_secret": "s"}, [], "test", allow_broker=True)
    try:
        assert [b.account_key for b, _ in sched_mod.build_brokers("paper")] == ["alpaca:paper"]
        assert "Trading 212" in settings.missing_creds("both", "paper")
    finally:
        settings.set_many({"broker_name": "alpaca", "trading_mode": "dry"}, ["alpaca_paper_key_id", "alpaca_paper_secret"],
                          "test", allow_broker=True)


def test_api_both_overview_close_and_panic(logged, monkeypatch):
    sch, alp, t212 = _setup_both(monkeypatch)
    try:
        for e in sch.engines:
            e._now = lambda: NOW
        sch.run_cycle_now()
        me = logged.get("/api/me").json()
        assert me["active_accounts"] == ["alpaca:paper", "trading212:demo"]
        assert me["broker_label"] == "Alpaca paper + Trading 212 demo"
        ov = logged.get("/api/overview?account=trading212:demo").json()
        assert ov["live"] is True and ov["account"]["pdt_applies"] is False and ov["positions"][0]["symbol"] == "AAA"
        assert logged.get("/api/overview?account=alpaca:paper").json()["live"] is True
        r = logged.post("/api/positions/AAA/close?account=trading212:demo", json={})
        assert r.status_code == 200 and t212.positions() == [] and len(alp.positions()) == 1
        r = logged.post("/api/agent/panic", json={"confirm": "STOP"})
        assert r.status_code == 200 and alp.positions() == []
        assert logged.get("/api/settings").json()["broker_label"] == "Alpaca paper + Trading 212 demo"
    finally:
        settings.set_many({"broker_name": "alpaca", "trading_mode": "dry"}, ["alpaca_paper_key_id", "alpaca_paper_secret",
                          "t212_demo_key_id", "t212_demo_secret"], "test", allow_broker=True)
        sch.rebuild()
