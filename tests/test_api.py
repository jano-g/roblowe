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
