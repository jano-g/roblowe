"""JSON API pre SPA. Zápisy: CSRF hlavička (middleware), current_user, db.tx, log_history."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request

from .. import auth, config, db, settings
from ..broker.base import ACCOUNT_LABELS, account_label
from ..scheduler import scheduler
from ..services import backup, ntfy

router = APIRouter()


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.message = message
        self.status = status


def _user(request: Request) -> dict:
    u = auth.current_user(request)
    if not u:
        raise ApiError("Neprihlásený", 401)
    return u


def _clock() -> dict:
    try:
        c = scheduler.broker.clock()
        return {"is_open": c.is_open, "next_open": c.next_open.isoformat(), "next_close": c.next_close.isoformat()}
    except Exception as e:  # noqa: BLE001
        return {"is_open": None, "error": f"Broker nedostupný: {e.__class__.__name__}"}


def _current_account() -> str:
    return scheduler.engine.account if scheduler.engine else "fake"


def _account_param(account: str | None) -> str:
    """Zvolený účet pre štatistiky; neznámy/prázdny = aktuálny."""
    cur = _current_account()
    if not account or account == cur:
        return cur
    if account not in ACCOUNT_LABELS:
        raise ApiError("Neznámy účet")
    return account


def _accounts() -> list[dict]:
    """Účty, ktoré majú nejaké dáta, + aktuálny. Aktuálny prvý."""
    cur = _current_account()
    keys = {r["account"] for r in db.q("SELECT DISTINCT account FROM days")} | \
           {r["account"] for r in db.q("SELECT DISTINCT account FROM orders")}
    keys.discard(cur)
    if cur != "fake":
        keys.discard("fake")
    order = list(ACCOUNT_LABELS)
    rest = sorted(keys, key=lambda k: order.index(k) if k in order else 99)
    return [{"key": k, "label": account_label(k), "current": k == cur} for k in [cur, *rest]]


@router.get("/me")
def me(request: Request):
    u = _user(request)
    today = datetime.now(config.MARKET_TZ).strftime("%Y-%m-%d")
    acc = _current_account()
    day = db.row("SELECT * FROM days WHERE account=? AND date=?", (acc, today))
    return {
        "user": u["username"], "mode": scheduler.mode, "app": config.APP_NAME,
        "agent_enabled": settings.get("agent_enabled"),
        "analyst": bool(scheduler.engine and scheduler.engine.analyst),
        "broker": scheduler.broker.__class__.__name__ if scheduler.broker else None,
        "broker_name": settings.broker_name(),
        "account": acc, "account_label": account_label(acc), "accounts": _accounts(),
        "last_cycle_at": scheduler.last_cycle_at.isoformat() if scheduler.last_cycle_at else None,
        "last_error": scheduler.last_error,
        "day": day, "clock": _clock(),
        "now": datetime.now(config.TZ).isoformat(),
        "last_report": scheduler.engine.last_report.as_dict() if scheduler.engine and scheduler.engine.last_report else None,
    }


@router.get("/overview")
def overview(request: Request, account: str | None = None):
    _user(request)
    acc = _account_param(account)
    live = acc == _current_account()
    acct, positions, err = None, [], None
    if live:
        try:
            a = scheduler.broker.account()
            acct = {"equity": a.equity, "cash": a.cash, "last_equity": a.last_equity, "buying_power": a.buying_power,
                    "account_currency": a.account_currency, "fx_to_usd": a.fx_to_usd,
                    "daytrade_count": a.daytrade_count, "pdt_applies": getattr(scheduler.broker, "pdt_applies", True),
                    "pattern_day_trader": a.pattern_day_trader, "trading_blocked": a.trading_blocked}
            positions = [p.__dict__ for p in scheduler.broker.positions()]
        except Exception as e:  # noqa: BLE001
            err = f"Broker nedostupný: {e}"
    else:
        last = db.row("SELECT at, equity, cash FROM equity WHERE account=? ORDER BY at DESC LIMIT 1", (acc,))
        if last:
            acct = {"equity": last["equity"], "cash": last["cash"], "last_equity": 0, "snapshot_at": last["at"]}
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    curve = db.rows("SELECT at, equity FROM equity WHERE account=? AND at >= ? ORDER BY at", (acc, since))
    # zriedenie krivky na max ~300 bodov
    if len(curve) > 300:
        step = len(curve) / 300
        curve = [curve[int(i * step)] for i in range(300)] + [curve[-1]]
    days = db.rows("SELECT * FROM days WHERE account=? ORDER BY date DESC LIMIT 30", (acc,))
    # koncová equity dňa = posledný bod krivky v ten deň (NY čas)
    for d in days:
        d0 = datetime.strptime(d["date"], "%Y-%m-%d").replace(tzinfo=config.MARKET_TZ)
        e = db.row("SELECT equity FROM equity WHERE account=? AND at >= ? AND at < ? ORDER BY at DESC LIMIT 1",
                   (acc, d0.astimezone(timezone.utc).isoformat(), (d0 + timedelta(days=1)).astimezone(timezone.utc).isoformat()))
        d["end_equity"] = e["equity"] if e else None
        d["pnl_pct"] = round((e["equity"] / d["start_equity"] - 1) * 100, 2) if e and d["start_equity"] else None
    today = datetime.now(config.MARKET_TZ).strftime("%Y-%m-%d")
    trades = db.row("SELECT COUNT(*) n, SUM(kind='entry') entries FROM orders WHERE account=? AND status NOT IN ('rejected')",
                    (acc,))
    return {"account_key": acc, "account_label": account_label(acc), "live": live,
            "account": acct, "positions": positions, "error": err, "curve": curve, "days": days, "trades": trades,
            "today": next((d for d in days if d["date"] == today), None),
            "market_note": scheduler.engine.market_note if scheduler.engine else ""}


@router.get("/decisions")
def decisions(request: Request, limit: int = 100, symbol: str | None = None, action: str | None = None,
              account: str | None = None):
    _user(request)
    limit = max(1, min(limit, 500))
    sql, params = "SELECT * FROM decisions WHERE account=?", [_account_param(account)]
    if symbol:
        sql += " AND symbol=?"
        params.append(symbol.upper()[:10])
    if action in ("buy", "sell", "hold", "skip"):
        sql += " AND action=?"
        params.append(action)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return {"items": db.rows(sql, tuple(params))}


@router.get("/orders")
def orders(request: Request, limit: int = 100, account: str | None = None):
    _user(request)
    return {"items": db.rows("SELECT * FROM orders WHERE account=? ORDER BY id DESC LIMIT ?",
                             (_account_param(account), max(1, min(limit, 500))))}


@router.get("/analyses")
def analyses(request: Request, limit: int = 20):
    _user(request)
    return {"items": db.rows("SELECT id, at, model, input_tokens, output_tokens, headlines, result FROM analyses "
                             "ORDER BY id DESC LIMIT ?", (max(1, min(limit, 100)),))}


@router.get("/history")
def history(request: Request, limit: int = 100):
    _user(request)
    return {"items": db.rows("SELECT * FROM history ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),))}


@router.get("/settings")
def get_settings(request: Request):
    _user(request)
    paper_id, paper_sec = settings.alpaca_creds("paper")
    live_id, live_sec = settings.alpaca_creds("live")
    return {"settings": settings.public_view(), "mode": scheduler.mode, "wanted_mode": settings.mode(),
            "broker": scheduler.broker.__class__.__name__ if scheduler.broker else None,
            "analyst_key_set": bool(settings.get("anthropic_api_key")),
            "alpaca_paper_set": bool(paper_id and paper_sec), "alpaca_live_set": bool(live_id and live_sec),
            "broker_name": settings.broker_name(),
            "t212_demo_set": all(settings.t212_creds("paper")), "t212_live_set": all(settings.t212_creds("live"))}


@router.put("/settings")
async def put_settings(request: Request):
    u = _user(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise ApiError("Neplatné telo požiadavky")
    values = body.get("values") or {}
    clear = body.get("_clear") or []
    if not isinstance(values, dict) or not isinstance(clear, list):
        raise ApiError("Neplatné telo požiadavky")
    try:
        settings.set_many(values, [str(k) for k in clear], u["username"])
    except ValueError as e:
        raise ApiError(str(e))
    if any(k.startswith("analyst_") for k in values):
        scheduler.reload_analyst()
    return {"ok": True, "settings": settings.public_view()}


@router.post("/broker")
async def put_broker(request: Request):
    """Režim a API kľúče. Vyžaduje aktuálne heslo; live navyše napísané LIVE.
    Po zmene sa agent vypne (zapneš ho vedome znova) a broker sa prebuduje bez reštartu."""
    u = _user(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise ApiError("Neplatné telo požiadavky")
    if not auth.login(u["username"], str(body.get("password", ""))):
        raise ApiError("Heslo nesedí.")
    values = {k: v for k, v in (body.get("values") or {}).items() if k in settings.BROKER_KEYS}
    clear = [k for k in (body.get("_clear") or []) if k in settings.BROKER_KEYS]
    new_mode = str(values.get("trading_mode", settings.mode())).lower()
    if new_mode == "live" and body.get("confirm") != "LIVE":
        raise ApiError("Prepnutie na live vyžaduje napísať LIVE.")
    try:
        settings.set_many(values, clear, u["username"], allow_broker=True)
    except ValueError as e:
        raise ApiError(str(e))
    # kontrola kľúčov pre zvolený broker + režim (po uložení, aby sa dali zadať spolu s režimom)
    missing = settings.missing_creds(settings.broker_name(), new_mode) if new_mode != "dry" else None
    if missing:
        settings.set_many({"trading_mode": "dry"}, [], u["username"], allow_broker=True)
        scheduler.rebuild()
        raise ApiError(f"{missing} Ostávam v dry.")
    old_mode = scheduler.mode
    settings.set_many({"agent_enabled": "0"}, [], u["username"])
    scheduler.rebuild()
    db.log_history(u["username"], "broker_changed", {"mode": scheduler.mode, "broker": settings.broker_name(),
                                                      "ip": auth.client_ip(request)})
    if scheduler.mode != old_mode or new_mode == "live":
        ntfy.notify("Roblowe: zmena režimu", f"{old_mode} → {scheduler.mode} (agent vypnutý, zapni ho ručne)", priority=4)
    # over spojenie s brokerom
    try:
        a = scheduler.broker.account()
        acct = {"equity": a.equity, "cash": a.cash, "account_currency": a.account_currency}
    except Exception as e:  # noqa: BLE001
        raise ApiError(f"Uložené, ale broker odmietol kľúče: {e}", 502)
    return {"ok": True, "mode": scheduler.mode, "account": acct, "settings": settings.public_view()}


@router.post("/agent/toggle")
async def agent_toggle(request: Request):
    u = _user(request)
    body = await request.json()
    enabled = bool(body.get("enabled"))
    if enabled and scheduler.mode == "live" and body.get("confirm") != "LIVE":
        raise ApiError("Zapnutie v live režime vyžaduje potvrdenie textom LIVE.")
    settings.set_many({"agent_enabled": "1" if enabled else "0"}, [], u["username"])
    ntfy.notify("Roblowe", "Agent zapnutý." if enabled else "Agent vypnutý.")
    return {"ok": True, "agent_enabled": enabled}


@router.post("/agent/cycle")
def agent_cycle(request: Request):
    u = _user(request)
    if not scheduler.engine:
        raise ApiError("Agent ešte nebeží", 503)
    rep = scheduler.run_cycle_now()
    db.log_history(u["username"], "manual_cycle", {"status": rep["status"]})
    return {"ok": True, "report": rep}


@router.post("/agent/panic")
async def agent_panic(request: Request):
    u = _user(request)
    body = await request.json()
    if body.get("confirm") != "STOP":
        raise ApiError("Potvrď textom STOP.")
    n = scheduler.engine.panic(u["username"])
    ntfy.notify("Roblowe: STOP", f"Ručne zatvorené {n} pozícií, agent vypnutý.")
    return {"ok": True, "closed": n}


@router.post("/positions/{symbol}/close")
def close_position(request: Request, symbol: str):
    u = _user(request)
    symbol = symbol.upper()[:10]
    pos = next((p for p in scheduler.broker.positions() if p.symbol == symbol), None)
    if not pos:
        raise ApiError("Pozícia neexistuje", 404)
    o = scheduler.engine.close(pos, f"ručne ({u['username']})")
    db.log_history(u["username"], "manual_close", {"symbol": symbol})
    return {"ok": True, "order": o}


@router.post("/password")
async def change_password(request: Request):
    u = _user(request)
    body = await request.json()
    old, new = str(body.get("old", "")), str(body.get("new", ""))
    if not auth.login(u["username"], old):
        raise ApiError("Staré heslo nesedí.")
    if len(new) < 8:
        raise ApiError("Nové heslo musí mať aspoň 8 znakov.")
    auth.set_password(u["username"], new)
    return {"ok": True}


@router.post("/notify/test")
async def notify_test(request: Request):
    _user(request)
    body = await request.json()
    ok, msg = ntfy.test(str(body.get("server") or settings.get("ntfy_server")),
                        str(body.get("topic") or settings.get("ntfy_topic")))
    if not ok:
        raise ApiError(msg)
    return {"ok": True, "message": msg}


@router.post("/notify/summary")
def notify_summary(request: Request):
    """Pošle súhrn posledného obchodného dňa hneď (test / náhľad)."""
    _user(request)
    day = db.row("SELECT * FROM days WHERE account=? ORDER BY date DESC LIMIT 1", (_current_account(),))
    if not day or not scheduler.engine:
        raise ApiError("Zatiaľ nie je žiadny obchodný deň.")
    title, text = scheduler.engine.build_daily_summary(day)
    ntfy.notify(title, text)
    return {"ok": True, "title": title, "text": text}


@router.get("/backups")
def backups(request: Request):
    _user(request)
    return {"items": backup.list_local()}


@router.post("/backups")
def backup_now(request: Request):
    u = _user(request)
    res = backup.run_backup("manual")
    db.log_history(u["username"], "manual_backup", res)
    if res["error"]:
        return {"ok": True, "result": res, "warning": res["error"]}
    return {"ok": True, "result": res}


@router.post("/backups/test-b2")
def backups_test(request: Request):
    _user(request)
    ok, msg = backup.test_b2()
    if not ok:
        raise ApiError(msg)
    return {"ok": True, "message": msg}
