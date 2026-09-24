def test_unauthenticated_redirects_and_401(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    assert client.get("/api/me").status_code == 401
    assert client.get("/healthz").json()["ok"] is True
    assert client.get("/docs", follow_redirects=False).status_code == 303  # docs vypnuté a za gate-om


def test_login_bad_password_and_throttle(client):
    for _ in range(6):
        r = client.post("/login", data={"username": "jano", "password": "zle"}, follow_redirects=False)
        assert r.status_code == 401
    r = client.post("/login", data={"username": "jano", "password": "heslo-heslo-123"}, follow_redirects=False)
    assert r.status_code == 429


def test_open_redirect_guard(logged):
    r = logged.post("/login", data={"username": "jano", "password": "heslo-heslo-123", "next": "//evil.com"},
                    follow_redirects=False)
    assert r.headers["location"] == "/"


def test_csrf_header_required(logged):
    del logged.headers["X-Requested-With"]
    assert logged.put("/api/settings", json={"values": {}}).status_code == 403
    logged.headers["X-Requested-With"] = "roblowe"
    assert logged.put("/api/settings", json={"values": {}}).status_code == 200


def test_settings_roundtrip_secret_hidden(logged):
    r = logged.put("/api/settings", json={"values": {"watchlist": "spy, aapl", "b2_app_key": "tajne", "risk_per_trade_pct": "0.25"}})
    assert r.status_code == 200
    s = r.json()["settings"]
    assert s["watchlist"]["value"] == "SPY,AAPL"
    assert s["b2_app_key"] == {"kind": "secret", "desc": s["b2_app_key"]["desc"], "source": "db", "set": True}
    assert "tajne" not in r.text
    r = logged.put("/api/settings", json={"values": {"watchlist": "in valid"}})
    assert r.status_code == 400 and "ticker" in r.json()["error"].lower()
    r = logged.put("/api/settings", json={"values": {"risk_per_trade_pct": "nan"}})
    assert r.status_code == 400


def test_overview_and_me(logged):
    me = logged.get("/api/me").json()
    assert me["user"] == "jano" and me["mode"] == "dry"
    ov = logged.get("/api/overview").json()
    assert ov["account"]["equity"] > 0
    assert logged.get("/api/decisions").status_code == 200


def test_panic_requires_confirmation(logged):
    assert logged.post("/api/agent/panic", json={"confirm": "no"}).status_code == 400
    r = logged.post("/api/agent/panic", json={"confirm": "STOP"})
    assert r.status_code == 200 and r.json()["ok"]


def test_security_headers(logged):
    r = logged.get("/")
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-cache"


def test_password_change_invalidates_session(logged):
    r = logged.post("/api/password", json={"old": "heslo-heslo-123", "new": "nove-heslo-456"})
    assert r.status_code == 200
    assert logged.get("/api/me").status_code == 401
    r = logged.post("/login", data={"username": "jano", "password": "nove-heslo-456"}, follow_redirects=False)
    assert r.status_code == 303
    logged.post("/api/password", json={"old": "nove-heslo-456", "new": "heslo-heslo-123"})


def test_broker_keys_not_settable_via_settings(logged):
    r = logged.put("/api/settings", json={"values": {"trading_mode": "live"}})
    assert r.status_code == 400 and "Broker" in r.json()["error"]


def test_broker_requires_password_and_live_confirmation(logged):
    r = logged.post("/api/broker", json={"values": {"trading_mode": "paper"}, "password": "zle"})
    assert r.status_code == 400 and "Heslo" in r.json()["error"]
    r = logged.post("/api/broker", json={"values": {"trading_mode": "live", "alpaca_live_key_id": "AK1", "alpaca_live_secret": "s"},
                                         "password": "heslo-heslo-123"})
    assert r.status_code == 400 and "LIVE" in r.json()["error"]
    # paper bez kľúčov → ostáva dry
    r = logged.post("/api/broker", json={"values": {"trading_mode": "paper"}, "password": "heslo-heslo-123"})
    assert r.status_code == 400 and "dry" in r.json()["error"]
    assert logged.get("/api/me").json()["mode"] == "dry"


def test_broker_switch_disables_agent_and_hides_secrets(logged, monkeypatch):
    from app.broker.fake import FakeBroker
    from app import scheduler as sched_mod

    # namiesto skutočnej Alpacy vráť FakeBroker, aby test nešiel na sieť
    monkeypatch.setattr(sched_mod, "AlpacaBroker", lambda *a, **k: FakeBroker(symbols=["SPY"]))
    logged.put("/api/settings", json={"values": {"agent_enabled": True}})
    r = logged.post("/api/broker", json={"values": {"trading_mode": "live", "alpaca_live_key_id": "AKLIVEKEY12345",
                                                    "alpaca_live_secret": "tajny-secret"},
                                         "password": "heslo-heslo-123", "confirm": "LIVE"})
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "live"
    assert "tajny-secret" not in r.text and "AKLIVEKEY12345" not in r.text
    me = logged.get("/api/me").json()
    assert me["mode"] == "live" and me["agent_enabled"] is False
    # zapnutie agenta v live vyžaduje LIVE
    assert logged.post("/api/agent/toggle", json={"enabled": True}).status_code == 400
    assert logged.post("/api/agent/toggle", json={"enabled": True, "confirm": "LIVE"}).status_code == 200
    # späť na dry
    r = logged.post("/api/broker", json={"values": {"trading_mode": "dry"}, "password": "heslo-heslo-123"})
    assert r.status_code == 200 and r.json()["mode"] == "dry"


def test_legacy_env_alpaca_keys_are_paper_keys(monkeypatch):
    from app import settings as st
    monkeypatch.setenv("ALPACA_KEY_ID", "PKLEGACY")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "legacy-secret")
    st.invalidate()
    assert st.alpaca_creds("paper") == ("PKLEGACY", "legacy-secret")
    assert st.alpaca_creds("live") == ("", "")


def test_overview_per_account(logged):
    from app import db
    db.run("INSERT INTO days(account, date, start_equity, created_at) VALUES ('alpaca:paper', '2026-09-20', 100000, 'x')")
    db.run("INSERT INTO equity(at, account, equity, cash) VALUES ('2026-09-20T18:00:00+00:00', 'alpaca:paper', 100960, 1000)")
    me = logged.get("/api/me").json()
    assert me["account"] == "fake" and [a["key"] for a in me["accounts"]] == ["fake", "alpaca:paper"]
    cur = logged.get("/api/overview").json()
    assert cur["live"] is True and all(d["account"] == "fake" for d in cur["days"])
    old = logged.get("/api/overview?account=alpaca:paper").json()
    assert old["live"] is False and old["account"]["equity"] == 100960 and old["positions"] == []
    assert old["days"][0]["pnl_pct"] == 0.96
    assert logged.get("/api/overview?account=evil").status_code == 400
    assert logged.get("/api/orders?account=alpaca:paper").status_code == 200


def test_overview_does_not_hang_on_slow_broker(logged, monkeypatch):
    import time as _t
    from app.routers import api as api_mod
    from app.scheduler import scheduler
    from app import db
    monkeypatch.setattr(api_mod, "BROKER_TIMEOUT", 0.3)
    db.run("INSERT INTO equity(at, account, equity, cash) VALUES ('2026-09-22T19:00:00+00:00', 'fake', 99000, 99000)")
    br = scheduler.engine.broker
    monkeypatch.setattr(br, "account", lambda: _t.sleep(2))
    t0 = _t.monotonic()
    ov = logged.get("/api/overview").json()
    assert _t.monotonic() - t0 < 1.5
    assert "neodpovedá" in ov["error"] and ov["account"]["equity"] == 99000 and ov["account"]["snapshot_at"]


def test_removed_settings_are_rejected_and_hidden(logged):
    s = logged.get("/api/settings").json()
    assert "respect_pdt" not in s["settings"] and "broker_name" not in s["settings"]
    assert not any(k.startswith("t212") for k in s["settings"])
    r = logged.put("/api/settings", json={"values": {"respect_pdt": True}})
    assert r.status_code == 400
    ov = logged.get("/api/overview").json()
    assert "daytrade_count" not in ov["account"] and ov["trades_today"] == 0


def test_models_endpoint_and_validation(logged, monkeypatch):
    from app.strategy import analyst
    analyst._models_cache.update(at=0.0, key=None, items=None)
    r = logged.get("/api/models").json()  # bez kľúča → záloha
    assert r["error"] and any(m["id"] == "claude-opus-5" for m in r["items"]) and r["current"] == "claude-opus-5"

    class M:
        def __init__(self, id, name, caps, created):
            self.id, self.display_name, self.capabilities, self.created_at = id, name, caps, created

    full = {"structured_outputs": {"supported": True}, "effort": {"supported": True, **{e: {"supported": True} for e in analyst.EFFORTS}}}
    noeff = {"structured_outputs": {"supported": True}, "effort": {"supported": False}}
    nojson = {"structured_outputs": {"supported": False}}

    class FakeClient:
        def __init__(self, **k):
            self.models = self

        def list(self):
            return [M("claude-old", "Old", nojson, "2024"), M("claude-haiku-4-5", "Claude Haiku 4.5", noeff, "2025"),
                    M("claude-opus-5", "Claude Opus 5", full, "2026")]
    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    items, err = analyst.list_models("sk-test", force=True)
    assert err is None and [m["id"] for m in items] == ["claude-opus-5", "claude-haiku-4-5"]
    assert items[1]["efforts"] == []
    # effort sa posiela len keď ho model podporuje; fallbacks len pre Opus 5 / Fable 5.x
    a = analyst.ClaudeAnalyst.__new__(analyst.ClaudeAnalyst)
    a.model, a.effort, a.info = "claude-haiku-4-5", "high", items[1]
    p = a.request_params("x")
    assert "effort" not in p["output_config"] and "fallbacks" not in p
    a.model, a.info = "claude-opus-5", items[0]
    p = a.request_params("x")
    assert p["output_config"]["effort"] == "high" and p["fallbacks"] == "default"
    assert logged.put("/api/settings", json={"values": {"analyst_model": "evil model;"}}).status_code == 400
    assert logged.put("/api/settings", json={"values": {"analyst_model": "claude-sonnet-5", "analyst_effort": "xhigh"}}).status_code == 200
    analyst._models_cache.update(at=0.0, key=None, items=None)
