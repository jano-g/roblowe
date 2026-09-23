import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app import db, settings
from app.broker.alpaca import BrokerError
from app.broker.base import Bar, Clock
from app.broker.trading212 import Trading212Broker
from app.services import fx


class FakeT212:
    """Minimálny simulátor Trading 212 API v0."""

    def __init__(self, currency="EUR", fail_stop=False):
        self.currency = currency
        self.fail_stop = fail_stop
        self.positions = {}   # ticker -> qty
        self.orders = {}      # id -> order
        self.calls = []
        self.next_id = 100
        self.price = 200.0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.replace("/api/v0", "")
        body = json.loads(request.content or b"{}") if request.content else {}
        self.calls.append((request.method, path, body))
        assert request.headers["Authorization"].startswith("Basic ")
        if path == "/equity/metadata/instruments":
            return httpx.Response(200, json=[
                {"ticker": "AAPL_US_EQ", "shortName": "AAPL", "currencyCode": "USD", "type": "STOCK"},
                {"ticker": "BRK_B_US_EQ", "shortName": "BRK.B", "currencyCode": "USD", "type": "STOCK"},
                {"ticker": "SPYl_EQ", "shortName": "SPY", "currencyCode": "USD", "type": "ETF"},
                {"ticker": "SAP_DE_EQ", "shortName": "SAP", "currencyCode": "EUR", "type": "STOCK"},
            ])
        if path == "/equity/account/summary":
            inv = sum(q * self.price for q in self.positions.values())
            return httpx.Response(200, json={"currency": self.currency, "totalValue": 10000 + inv,
                                             "cash": {"availableToTrade": 10000, "inPies": 0, "reservedForOrders": 0}})
        if path == "/equity/positions":
            t = request.url.params.get("ticker")
            items = []
            for tk, q in self.positions.items():
                if t and tk != t:
                    continue
                blocked = sum(abs(o["quantity"]) for o in self.orders.values() if o["ticker"] == tk)
                items.append({"instrument": {"ticker": tk, "currency": "USD"}, "quantity": q,
                              "quantityAvailableForTrading": q - blocked, "averagePricePaid": 190.0,
                              "currentPrice": self.price})
            return httpx.Response(200, json=items)
        if path == "/equity/orders" and request.method == "GET":
            return httpx.Response(200, json=[{"id": i, "ticker": o["ticker"], "type": o["type"], "side": "SELL",
                                              "quantity": o["quantity"], "stopPrice": o.get("stopPrice"),
                                              "status": "NEW"} for i, o in self.orders.items()])
        if path == "/equity/orders/market":
            tk, q = body["ticker"], body["quantity"]
            self.positions[tk] = self.positions.get(tk, 0) + q
            if self.positions[tk] <= 0:
                self.positions.pop(tk)
            self.next_id += 1
            return httpx.Response(200, json={"id": self.next_id, "status": "FILLED"})
        if path == "/equity/orders/stop":
            if self.fail_stop:
                return httpx.Response(400, json={"code": "x"})
            self.next_id += 1
            self.orders[self.next_id] = {"ticker": body["ticker"], "quantity": body["quantity"], "type": "STOP",
                                         "stopPrice": body["stopPrice"], "tv": body["timeValidity"]}
            return httpx.Response(200, json={"id": self.next_id, "status": "NEW"})
        if path.startswith("/equity/orders/") and request.method == "DELETE":
            self.orders.pop(int(path.rsplit("/", 1)[1]), None)
            return httpx.Response(200)
        return httpx.Response(404)


class DataStub:
    def clock(self):
        now = datetime(2026, 9, 18, 15, 0, tzinfo=timezone.utc)
        return Clock(True, now, now + timedelta(days=1), now + timedelta(hours=5))

    def bars(self, symbols, timeframe, limit):
        return {s: [] for s in symbols}

    def latest_prices(self, symbols):
        return {}

    def news(self, symbols, since, limit):
        return []


@pytest.fixture
def t212(monkeypatch):
    fx.set_rates_for_tests({"EUR": 1.0, "USD": 1.10})
    monkeypatch.setattr("app.broker.trading212.time.sleep", lambda s: None)
    sim = FakeT212()
    b = Trading212Broker("key", "secret", "demo", DataStub(), transport=httpx.MockTransport(sim), rate_limit=False)
    return sim, b


def test_ticker_mapping(t212):
    sim, b = t212
    assert b.ticker("AAPL") == "AAPL_US_EQ"
    assert b.ticker("BRK.B") == "BRK_B_US_EQ"
    assert b.ticker("SPY") == "SPYl_EQ"
    assert b.symbol("AAPL_US_EQ") == "AAPL"
    with pytest.raises(BrokerError):
        b.ticker("SAP")  # len v EUR → nie je americký ticker


def test_account_converted_to_usd(t212):
    sim, b = t212
    a = b.account()
    assert a.account_currency == "EUR" and a.fx_to_usd == pytest.approx(1.10)
    assert a.equity == pytest.approx(11000) and a.cash == pytest.approx(11000)
    assert b.pdt_applies is False and b.native_bracket is False


def test_buy_places_market_then_gtc_stop(t212):
    sim, b = t212
    r = b.submit_bracket_buy("AAPL", 5, 195.123, 210)
    assert r.status == "filled" and r.filled_avg_price == 190.0
    market = [c for c in sim.calls if c[1] == "/equity/orders/market"]
    stops = [c for c in sim.calls if c[1] == "/equity/orders/stop"]
    assert market[0][2] == {"ticker": "AAPL_US_EQ", "quantity": 5.0, "extendedHours": False}
    assert stops[0][2]["quantity"] == -5 and stops[0][2]["stopPrice"] == 195.12
    assert stops[0][2]["timeValidity"] == "GOOD_TILL_CANCEL"
    assert [p.symbol for p in b.positions()] == ["AAPL"]
    assert b.open_orders()[0]["symbol"] == "AAPL"


def test_buy_closes_position_when_stop_fails(t212):
    sim, b = t212
    sim.fail_stop = True
    with pytest.raises(BrokerError, match="zatvorená"):
        b.submit_bracket_buy("AAPL", 5, 195, 210)
    assert sim.positions == {}


def test_close_position_cancels_stop_and_sells(t212):
    sim, b = t212
    b.submit_bracket_buy("AAPL", 5, 195, 210)
    b.close_position("AAPL")
    assert sim.orders == {} and sim.positions == {}
    sells = [c for c in sim.calls if c[1] == "/equity/orders/market" and c[2]["quantity"] < 0]
    assert sells[-1][2]["quantity"] == -5


def test_close_all(t212):
    sim, b = t212
    b.submit_bracket_buy("AAPL", 5, 195, 210)
    b.submit_bracket_buy("BRK.B", 2, 400, 450)
    b.close_all()
    assert sim.positions == {} and sim.orders == {}


def test_bad_key_message(monkeypatch):
    fx.set_rates_for_tests({"EUR": 1.0, "USD": 1.1})
    b = Trading212Broker("k", "s", "demo", DataStub(), rate_limit=False,
                         transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    with pytest.raises(BrokerError, match="401, zlý API kľúč"):
        b.account()


# -- engine s brokerom bez bracketu -----------------------------------------------------------------
def _engine_no_bracket():
    from tests.test_engine import make_engine
    fb, eng = make_engine()
    fb.native_bracket = False
    fb.pdt_applies = False
    return fb, eng


def test_engine_take_profit_managed_by_agent():
    from tests.test_engine import trend_bars
    fb, eng = _engine_no_bracket()
    fb.set_bars("AAA", trend_bars(0.4))
    eng.cycle()
    entry = db.row("SELECT * FROM orders WHERE kind='entry'")
    bars = trend_bars(0.4)
    bars[-1] = Bar(bars[-1].t, bars[-1].o, entry["take_profit"] + 2, bars[-1].l, entry["take_profit"] + 1, 1000)
    fb.set_bars("AAA", bars)
    eng.cycle()
    assert fb.positions() == []
    assert "cieľ" in db.row("SELECT reason FROM decisions WHERE action='sell'")["reason"]


def test_engine_replaces_missing_stop():
    from tests.test_engine import trend_bars
    fb, eng = _engine_no_bracket()
    fb.set_bars("AAA", trend_bars(0.4))
    eng.cycle()
    fb._orders = []  # stop zmizol (zrušený ručne / expiroval)
    rep = eng.cycle()
    assert any(o["side"] == "stop" for o in fb.submitted)
    assert any("stop-loss" in n for n in rep.notes)


def test_pdt_not_applied_for_t212_broker():
    from tests.test_engine import trend_bars
    fb, eng = _engine_no_bracket()
    fb._cash = 5_000
    fb.daytrade_count = 5
    fb.set_bars("AAA", trend_bars(0.4))
    eng.cycle()
    assert [o["symbol"] for o in fb.submitted if o["side"] == "buy"] == ["AAA"]


# -- API ---------------------------------------------------------------------------------------------
def test_api_switch_to_trading212(logged, monkeypatch):
    from app import scheduler as sched_mod
    from app.broker.fake import FakeBroker

    monkeypatch.setattr(sched_mod, "AlpacaBroker", lambda *a, **k: FakeBroker(symbols=["SPY"]))
    made = {}

    def fake_t212(key, secret, env, data, **kw):
        made["env"] = env
        fb = FakeBroker(symbols=["SPY"])
        fb.__class__ = type("Trading212Broker", (FakeBroker,), {"native_bracket": False, "pdt_applies": False})
        return fb
    monkeypatch.setattr(sched_mod, "Trading212Broker", fake_t212)
    pw = "heslo-heslo-123"
    # bez Alpaca kľúčov → odmietne s vysvetlením
    r = logged.post("/api/broker", json={"values": {"broker_name": "trading212", "trading_mode": "paper",
                                                    "t212_demo_key_id": "DEMOKEY123456", "t212_demo_secret": "s1"},
                                         "password": pw})
    assert r.status_code == 400 and "Alpaca" in r.json()["error"]
    r = logged.post("/api/broker", json={"values": {"broker_name": "trading212", "trading_mode": "paper",
                                                    "alpaca_paper_key_id": "PKDATA123456", "alpaca_paper_secret": "s2"},
                                         "password": pw})
    assert r.status_code == 200, r.text
    assert made["env"] == "demo" and r.json()["mode"] == "paper"
    s = logged.get("/api/settings").json()
    assert s["broker_name"] == "trading212" and s["t212_demo_set"] is True and "s1" not in str(s)
    assert logged.get("/api/me").json()["broker"] == "Trading212Broker"
    # live bez live kľúčov
    r = logged.post("/api/broker", json={"values": {"trading_mode": "live"}, "password": pw, "confirm": "LIVE"})
    assert r.status_code == 400 and "live" in r.json()["error"]
    settings.set_many({"broker_name": "alpaca"}, [], "test", allow_broker=True)
