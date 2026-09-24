"""Rozhodovací cyklus agenta. Poradie je zámerné a nemenné:
1. stav účtu a dňa → 2. bezpečnostné brzdy (denná strata, zatvorenie pred koncom seansy)
→ 3. signály (technika + správy) → 4. výstupy → 5. vstupy cez risk.position_size.
Model (Claude) nikdy neposiela objednávky – iba dodáva skóre do kroku 3."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

from .. import config, db, settings
from ..broker.base import Broker, NewsItem, Position, account_label
from . import risk, signals
from .analyst import Analysis, ClaudeAnalyst, SymbolView, parse_analysis

log = logging.getLogger("roblowe.engine")

NEWS_TTL = timedelta(hours=4)
NEWS_LOOKBACK = timedelta(hours=3)


class NewsState:
    """Pohľady zo správ zdieľané medzi enginmi (pri obchodovaní na viacerých účtoch naraz sa správy
    analyzujú raz a použijú všetky účty)."""

    def __init__(self):
        self.views: dict[str, SymbolView] = {}
        self.market_note = ""


@dataclass
class CycleReport:
    status: str
    at: str = ""
    equity: float = 0.0
    day_pnl_pct: float = 0.0
    decisions: list[dict] = field(default_factory=list)
    orders: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    market_note: str = ""

    def as_dict(self) -> dict:
        return self.__dict__


class Engine:
    def __init__(self, broker: Broker, analyst: ClaudeAnalyst | None = None, mode: str = "dry",
                 notify: Callable[[str, str], None] | None = None, now: Callable[[], datetime] | None = None,
                 news: NewsState | None = None):
        self.broker = broker
        self.account = getattr(broker, "account_key", "fake")
        self.analyst = analyst
        self.mode = mode
        _send = notify or (lambda title, msg: None)
        _label = account_label(self.account)
        # názov účtu v titulku – pri obchodovaní na viacerých účtoch naraz vidíš, odkiaľ správa je
        self.notify = lambda title, msg: _send(f"{title} · {_label}", msg)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.news = news or NewsState()
        self.last_report: CycleReport | None = None
        if not self.news.views:
            self._load_recent_analysis()

    @property
    def news_views(self) -> dict[str, SymbolView]:
        return self.news.views

    @news_views.setter
    def news_views(self, v: dict[str, SymbolView]) -> None:
        self.news.views = v

    @property
    def market_note(self) -> str:
        return self.news.market_note

    @market_note.setter
    def market_note(self, v: str) -> None:
        self.news.market_note = v

    # -- pomocné -------------------------------------------------------------------
    def _load_recent_analysis(self) -> None:
        row = db.row("SELECT at, model, result FROM analyses ORDER BY id DESC LIMIT 1")
        if not row:
            return
        at = datetime.fromisoformat(row["at"])
        if self._now() - at > NEWS_TTL:
            return
        try:
            a = parse_analysis(json.loads(row["result"]), model=row["model"], at=at)
        except (json.JSONDecodeError, TypeError):
            return
        self.news_views = a.symbols
        self.market_note = a.market_note

    def _ts(self) -> str:
        """Časová pečiatka záznamov z rovnakých hodín ako rozhodnutia (testovateľné cez now=)."""
        return self._now().astimezone(timezone.utc).replace(microsecond=0).isoformat()

    @staticmethod
    def trading_date(now: datetime) -> str:
        return now.astimezone(config.MARKET_TZ).strftime("%Y-%m-%d")

    def _ensure_day(self, date: str, equity: float) -> dict:
        day = db.row("SELECT * FROM days WHERE account=? AND date=?", (self.account, date))
        if day:
            return day
        with db.tx():
            db.run("INSERT INTO days(account, date, start_equity, created_at) VALUES (?,?,?,?)",
                   (self.account, date, equity, db.now_iso()))
            db.log_history("agent", "day_started", {"account": self.account, "date": date, "start_equity": equity})
        return db.row("SELECT * FROM days WHERE account=? AND date=?", (self.account, date))

    def _decide(self, symbol: str, action: str, reason: str, *, price=None, tech=None, news=None, score=None,
                details=None) -> dict:
        d = {
            "at": self._ts(), "account": self.account, "symbol": symbol, "price": price,
            "tech_score": tech.score if tech else None,
            "news_score": news.sentiment if news else None,
            "news_confidence": news.confidence if news else None,
            "score": score, "action": action, "reason": reason,
            "details": json.dumps(details, ensure_ascii=False) if details else None,
        }
        db.run(
            "INSERT INTO decisions(at,account,symbol,price,tech_score,news_score,news_confidence,score,action,reason,details) "
            "VALUES (:at,:account,:symbol,:price,:tech_score,:news_score,:news_confidence,:score,:action,:reason,:details)", d)
        return d

    def _order(self, symbol: str, side: str, qty: float, kind: str, *, price=None, stop=None, tp=None,
               broker_id=None, status="submitted", note=None, protect="bracket") -> dict:
        o = {"at": self._ts(), "account": self.account, "mode": self.mode, "broker_id": broker_id, "symbol": symbol, "side": side, "qty": qty,
             "kind": kind, "price": price, "stop_price": stop, "take_profit": tp, "status": status, "note": note, "protect": protect}
        db.run("INSERT INTO orders(at,account,mode,broker_id,symbol,side,qty,kind,price,stop_price,take_profit,status,note,protect) "
               "VALUES (:at,:account,:mode,:broker_id,:symbol,:side,:qty,:kind,:price,:stop_price,:take_profit,:status,:note,:protect)", o)
        return o

    def _live(self) -> bool:
        return self.mode in ("paper", "live")

    # -- akcie -------------------------------------------------------------------------
    def flatten_all(self, reason: str, positions: list[Position]) -> list[dict]:
        out = []
        if self._live():
            try:
                self.broker.close_all()
            except Exception as e:  # noqa: BLE001
                log.exception("close_all zlyhalo")
                self.notify("Roblowe: CHYBA", f"Zatvorenie pozícií zlyhalo: {e}")
        for p in positions:
            out.append(self._order(p.symbol, "sell", p.qty, "flatten", price=p.current_price,
                                   status="submitted" if self._live() else "dry", note=reason))
        if positions and settings.get("notify_mode") in ("trade", "both"):
            self.notify("Roblowe: zatváram všetko", f"{reason} · {len(positions)} pozícií")
        return out

    def close(self, p: Position, reason: str) -> dict:
        broker_id, status = None, "dry"
        if self._live():
            try:
                r = self.broker.close_position(p.symbol)
                broker_id, status = r.broker_id, r.status
            except Exception as e:  # noqa: BLE001
                log.exception("close_position zlyhalo")
                status = "rejected"
                reason = f"{reason} · CHYBA: {e}"
        o = self._order(p.symbol, "sell", p.qty, "exit", price=p.current_price, broker_id=broker_id, status=status,
                        note=reason)
        if settings.get("notify_mode") in ("trade", "both"):
            self.notify(f"Roblowe: predaj {p.symbol}", f"{p.qty:g} ks @ {p.current_price:.2f} · P/L {p.unrealized_pl:+.0f} USD · {reason}")
        return o

    def enter(self, symbol: str, sizing: risk.Sizing, price: float, reason: str) -> dict:
        """Celé kusy → bracket (stop aj cieľ u brokera). Zlomky → market + samostatný stop u brokera,
        cieľ stráži engine v každom cykle (`protect='stop'`)."""
        fractional = float(sizing.qty) != int(sizing.qty)
        protect = "stop" if fractional else "bracket"
        broker_id, status = None, "dry"
        if self._live():
            try:
                if fractional:
                    r = self.broker.submit_fractional_buy(symbol, sizing.qty, sizing.stop_price)
                else:
                    r = self.broker.submit_bracket_buy(symbol, int(sizing.qty), sizing.stop_price, sizing.take_profit)
                broker_id, status = r.broker_id, r.status
            except Exception as e:  # noqa: BLE001
                log.exception("vstup %s zlyhal", symbol)
                status = "rejected"
                reason = f"{reason} · CHYBA: {e}"
        o = self._order(symbol, "buy", sizing.qty, "entry", price=price, stop=sizing.stop_price, tp=sizing.take_profit,
                        broker_id=broker_id, status=status, note=reason, protect=protect)
        if settings.get("notify_mode") in ("trade", "both"):
            self.notify(f"Roblowe: kúpa {symbol}", f"{sizing.qty:g} ks @ {price:.2f} · stop {sizing.stop_price:.2f} · cieľ {sizing.take_profit:.2f} · {reason}")
        return o

    # -- správy ----------------------------------------------------------------------
    def refresh_news(self, watchlist: list[str], tech: dict[str, signals.Tech | None], now: datetime) -> None:
        # expirácia starých pohľadov
        self.news_views = {s: v for s, v in self.news_views.items() if now - v.at <= NEWS_TTL}
        if not self.analyst:
            return
        today = self.trading_date(now)
        calls_today = db.q1("SELECT COUNT(*) c FROM analyses WHERE at >= ?", (now.astimezone(config.MARKET_TZ)
                            .replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat(),))["c"]
        if calls_today >= settings.get("analyst_daily_budget_calls"):
            return
        try:
            items = self.broker.news(watchlist, now - NEWS_LOOKBACK, settings.get("analyst_max_headlines"))
        except Exception as e:  # noqa: BLE001
            log.warning("news zlyhalo: %s", e)
            return
        fresh: list[NewsItem] = []
        for n in items:
            if not db.q1("SELECT 1 FROM news_seen WHERE id=?", (n.id,)):
                fresh.append(n)
        if not fresh:
            return
        ctx = {}
        for s, t in tech.items():
            if t:
                ctx[s] = f"cena {t.price:.2f}, RSI {t.rsi:.0f}, {', '.join(t.notes)}" if t.rsi is not None else f"cena {t.price:.2f}"
        analysis: Analysis | None = self.analyst.analyze(fresh, ctx, watchlist)
        with db.tx():
            for n in fresh:
                db.run("INSERT OR IGNORE INTO news_seen(id, at) VALUES (?,?)", (n.id, n.at.isoformat()))
            if analysis:
                db.run("INSERT INTO analyses(at, model, input_tokens, output_tokens, headlines, result) VALUES (?,?,?,?,?,?)",
                       (db.now_iso(), analysis.model, analysis.input_tokens, analysis.output_tokens, analysis.headlines,
                        json.dumps(analysis.raw, ensure_ascii=False)))
        if analysis:
            for s, v in analysis.symbols.items():
                if s in watchlist:
                    self.news_views[s] = v
            self.market_note = analysis.market_note
            log.info("Claude analýza: %d správ, %d tickerov, %s", len(fresh), len(analysis.symbols), analysis.market_note)

    def combined_score(self, tech: signals.Tech, news: SymbolView | None) -> tuple[float, SymbolView | None]:
        tw, nw = settings.get("tech_weight"), settings.get("news_weight")
        valid = news if (news and not news.stale and news.confidence >= settings.get("news_min_confidence")) else None
        if valid:
            score = tw * tech.score + nw * valid.sentiment
        else:
            score = tech.score * (tw + nw * 0.5)
        return round(max(-1.0, min(1.0, score)), 3), valid

    # -- hlavný cyklus ---------------------------------------------------------------------
    def cycle(self) -> CycleReport:
        now = self._now()
        rep = CycleReport(status="ok", at=now.isoformat())
        clock = self.broker.clock()
        if not clock.is_open:
            rep.status = "closed"
            rep.notes.append(f"Burza zatvorená, otvára {clock.next_open.astimezone(config.TZ):%d.%m. %H:%M}")
            self.send_daily_summaries(clock.now)
            self.last_report = rep
            return rep

        acct = self.broker.account()
        positions = self.broker.positions()
        held = {p.symbol: p for p in positions}
        date = self.trading_date(clock.now)
        # Základ dňa = equity pri zatvorení predošlého dňa podľa brokera (Alpaca last_equity).
        # Bez neho (FakeBroker) equity z prvého cyklu dňa.
        base = acct.last_equity if acct.last_equity > 0 else acct.equity
        day = self._ensure_day(date, base)
        if acct.last_equity > 0 and abs(day["start_equity"] - acct.last_equity) > 0.01:
            with db.tx():
                db.run("UPDATE days SET start_equity=? WHERE account=? AND date=?", (acct.last_equity, self.account, date))
                db.log_history("agent", "day_start_synced", {"date": date, "old": day["start_equity"], "new": acct.last_equity})
            day["start_equity"] = acct.last_equity
        db.run("INSERT INTO equity(at, account, equity, cash) VALUES (?,?,?,?)",
               (self._ts(), self.account, acct.equity, acct.cash))
        rep.equity = acct.equity
        hit, pnl_pct = risk.daily_loss_hit(day["start_equity"], acct.equity, settings.get("daily_loss_limit_pct"))
        rep.day_pnl_pct = round(pnl_pct, 2)

        if acct.trading_blocked:
            rep.status = "blocked"
            rep.notes.append("Broker blokuje obchodovanie na účte.")
            self.last_report = rep
            return rep

        mins_to_close = (clock.next_close - clock.now).total_seconds() / 60
        mins_since_open = (clock.now - (clock.next_close - timedelta(hours=6, minutes=30))).total_seconds() / 60

        # 2a) zatvorenie pred koncom seansy – vždy, aj keď je deň zastavený. Opakuje sa každý cyklus,
        #     kým broker hlási otvorené pozície (príznak flattened nikdy nič nepreskočí).
        if mins_to_close <= settings.get("flatten_before_close_min"):
            if positions:
                rep.orders += self.flatten_all("koniec seansy", positions)
            left = self.broker.positions() if (positions and self._live()) else []
            if left:
                rep.notes.append(f"Stále otvorené: {', '.join(p.symbol for p in left)} – skúsim znova v ďalšom cykle.")
                self.notify("Roblowe: CHYBA", f"Pozície sa nepodarilo zavrieť pred koncom seansy: "
                            f"{', '.join(p.symbol for p in left)}. Skontroluj Alpaca.")
            elif not day["flattened"]:
                with db.tx():
                    db.run("UPDATE days SET flattened=1 WHERE account=? AND date=?", (self.account, date))
            rep.status = "flattened"
            self.last_report = rep
            return rep

        # 2a') zostatky z predošlých dní (napr. zlyhané zatvorenie): bracket nohy s time_in_force=day
        #      už expirovali, pozícia je bez stop-lossu → zavri ju hneď po otvorení.
        if self._live() and positions:
            d0 = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=config.MARKET_TZ).astimezone(timezone.utc).isoformat()
            today_entries = {r["symbol"] for r in db.q(
                "SELECT symbol FROM orders WHERE account=? AND kind='entry' AND status!='rejected' AND at >= ?",
                (self.account, d0))}
            for p in list(positions):
                if p.symbol not in today_entries:
                    why = "zostatok bez dnešného vstupu agenta (z predošlého dňa alebo ručný nákup)"
                    rep.orders.append(self.close(p, why))
                    rep.decisions.append(self._decide(p.symbol, "sell", why, price=p.current_price))
                    self.notify("Roblowe: zatváram zostatok", f"{p.symbol} {p.qty:g} ks – {why}.")
                    held.pop(p.symbol, None)
            positions = [p for p in positions if p.symbol in held]

        # 2b) denná strata → zastav deň
        if hit and not day["halted"]:
            reason = f"denná strata {pnl_pct:.2f} % prekročila limit {settings.get('daily_loss_limit_pct')} %"
            with db.tx():
                db.run("UPDATE days SET halted=1, halt_reason=? WHERE account=? AND date=?", (reason, self.account, date))
                db.log_history("agent", "day_halted", {"date": date, "reason": reason})
            rep.orders += self.flatten_all(reason, positions)
            self.notify("Roblowe: STOP na dnes", reason)
            rep.status = "halted"
            rep.notes.append(reason)
            self.last_report = rep
            return rep
        if day["halted"]:
            if positions:  # zastavený deň nesmie držať pozície – dokončí zatváranie, ak predtým zlyhalo
                rep.orders += self.flatten_all(day["halt_reason"] or "deň zastavený", positions)
            rep.status = "halted"
            rep.notes.append(day["halt_reason"] or "deň zastavený")
            self.last_report = rep
            return rep

        # 3) signály
        watchlist = settings.get("watchlist")
        symbols = sorted(set(watchlist) | set(held))
        try:
            bars = self.broker.bars(symbols, settings.get("bar_timeframe"), 120)
        except Exception as e:  # noqa: BLE001
            log.warning("bars zlyhalo: %s", e)
            rep.status = "error"
            rep.notes.append(f"Dáta z burzy nedostupné: {e}")
            self.last_report = rep
            return rep
        tech = {s: signals.analyze(bars.get(s, [])) for s in symbols}
        self.refresh_news(watchlist, tech, now)
        rep.market_note = self.market_note

        open_syms = {o.get("symbol") for o in (self.broker.open_orders() if self._live() else [])}
        if not self._live():
            # dry režim: virtuálne pozície = dnešné dry vstupy bez následného výstupu (inak by kupoval každý cyklus)
            day_start = clock.now.astimezone(config.MARKET_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
            for r in db.q("SELECT symbol, kind FROM orders WHERE account=? AND mode='dry' AND at >= ? ORDER BY id",
                          (self.account, day_start.astimezone(timezone.utc).isoformat())):
                if r["kind"] == "entry":
                    open_syms.add(r["symbol"])
                else:
                    open_syms.discard(r["symbol"])
        entries = 0
        fractional_on = settings.get("fractional_shares")
        # zlomkové pozície: stop je u brokera, cieľ stráži engine → načítaj úrovne z dnešných vstupov
        managed: dict[str, dict] = {}
        d0_utc = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=config.MARKET_TZ).astimezone(timezone.utc).isoformat()
        for r in db.q("SELECT symbol, stop_price, take_profit FROM orders WHERE account=? AND kind='entry' "
                      "AND status!='rejected' AND protect='stop' AND at >= ? ORDER BY id", (self.account, d0_utc)):
            managed[r["symbol"]] = {"stop": r["stop_price"], "tp": r["take_profit"]}
        stop_syms = {o.get("symbol") for o in (self.broker.open_orders() if self._live() else [])
                     if str(o.get("type", "")).lower() in ("stop", "stop_limit")} if managed else set()
        entry_window = (mins_since_open >= settings.get("no_entry_first_min")
                        and mins_to_close > settings.get("no_entry_after_close_min"))
        cash_left = acct.cash

        for s in symbols:
            t = tech.get(s)
            p = held.get(s)
            if not t:
                if p:
                    rep.decisions.append(self._decide(s, "hold", "málo dát, držím", price=p.current_price))
                continue
            news = self.news_views.get(s)
            score, valid_news = self.combined_score(t, news)
            det = {"tech": t.notes, "rsi": t.rsi, "atr": t.atr, "vwap": t.vwap,
                   "news": (valid_news.catalyst if valid_news else None)}

            # 4) výstupy
            if p:
                lv = managed.get(s)
                if lv and self._live():
                    if lv["tp"] and t.price >= lv["tp"]:
                        rep.orders.append(self.close(p, f"cieľ {lv['tp']:.2f} dosiahnutý"))
                        rep.decisions.append(self._decide(s, "sell", f"cieľ {lv['tp']:.2f} dosiahnutý", price=t.price,
                                                          tech=t, news=valid_news, score=score, details=det))
                        continue
                    if lv["stop"] and s not in stop_syms:
                        # stop-loss u brokera chýba (zrušený / expiroval) → zadaj znova, inak zavri
                        try:
                            self.broker.place_stop(s, p.qty, lv["stop"])
                            rep.notes.append(f"{s}: chýbal stop-loss, znova zadaný na {lv['stop']:.2f}.")
                            self.notify("Roblowe: stop-loss obnovený", f"{s} stop {lv['stop']:.2f}")
                        except Exception as e:  # noqa: BLE001
                            log.warning("place_stop %s zlyhalo: %s", s, e)
                            rep.orders.append(self.close(p, f"stop-loss sa nedal zadať ({e})"))
                            rep.decisions.append(self._decide(s, "sell", "stop-loss sa nedal zadať", price=t.price,
                                                              tech=t, news=valid_news, score=score, details=det))
                            continue
                if score <= settings.get("sell_threshold"):
                    rep.orders.append(self.close(p, f"skóre {score:+.2f} pod hranicou predaja"))
                    rep.decisions.append(self._decide(s, "sell", "skóre pod hranicou predaja", price=t.price, tech=t,
                                                      news=valid_news, score=score, details=det))
                elif valid_news and valid_news.sentiment <= -0.5:
                    rep.orders.append(self.close(p, f"negatívna správa: {valid_news.catalyst}"))
                    rep.decisions.append(self._decide(s, "sell", "negatívna správa", price=t.price, tech=t,
                                                      news=valid_news, score=score, details=det))
                else:
                    rep.decisions.append(self._decide(s, "hold", "držím, stop u brokera, cieľ stráži agent" if lv
                                                      else "držím, stop/cieľ u brokera", price=t.price, tech=t,
                                                      news=valid_news, score=score, details=det))
                continue

            # 5) vstupy (len sledované tickery)
            if s not in watchlist:
                continue
            if score < settings.get("buy_threshold"):
                rep.decisions.append(self._decide(s, "hold", "skóre pod hranicou kúpy", price=t.price, tech=t,
                                                  news=valid_news, score=score, details=det))
                continue
            skip = None
            if not settings.get("agent_enabled"):
                skip = "agent je vypnutý"
            elif s in open_syms:
                skip = "už čaká objednávka" if self._live() else "dry pozícia už otvorená (dnes)"
            elif not entry_window:
                skip = "mimo vstupného okna (začiatok/koniec seansy)"
            elif len(held) + entries >= settings.get("max_positions"):
                skip = "max. počet pozícií"
            elif valid_news and valid_news.sentiment <= -0.3:
                skip = "správy proti vstupu"
            elif settings.get("require_news_for_entry") and not (valid_news and valid_news.sentiment > 0.3):
                skip = "chýba pozitívny katalyzátor v správach"
            if skip:
                rep.decisions.append(self._decide(s, "skip", skip, price=t.price, tech=t, news=valid_news, score=score,
                                                  details=det))
                continue
            kw = dict(risk_pct=settings.get("risk_per_trade_pct"), max_pos_pct=settings.get("max_position_pct"),
                      atr_mult=settings.get("atr_stop_mult"), reward_risk=settings.get("reward_risk"))
            sizing = risk.position_size(acct.equity, cash_left, t.price, t.atr or 0, **kw)
            if fractional_on:
                frac = risk.position_size(acct.equity, cash_left, t.price, t.atr or 0, fractional=True, **kw)
                # celé kusy len keď vyplnia aspoň 75 % cieľovej pozície (bracket je bezpečnejší), inak zlomky
                if frac.qty > 0 and (sizing.qty < 1 or sizing.qty * t.price < 0.75 * frac.qty * t.price):
                    sizing = frac
            if sizing.qty <= 0:
                rep.decisions.append(self._decide(s, "skip", sizing.reason, price=t.price, tech=t, news=valid_news,
                                                  score=score, details=det))
                continue
            why = f"skóre {score:+.2f}" + (f" · {valid_news.catalyst}" if valid_news else "")
            o = self.enter(s, sizing, t.price, why)
            rep.orders.append(o)
            rep.decisions.append(self._decide(s, "buy", why, price=t.price, tech=t, news=valid_news, score=score,
                                              details={**det, "sizing": sizing.reason}))
            if o["status"] != "rejected":
                entries += 1
                cash_left -= sizing.qty * t.price
        self.last_report = rep
        return rep

    # -- denný súhrn -------------------------------------------------------------------------------
    def build_daily_summary(self, day: dict) -> tuple[str, str]:
        """(titulok, text) pre ntfy. Realizovaný výsledok je približný: párovanie výstupov so vstupmi
        toho istého tickera v ten deň podľa zaznamenaných cien."""
        date = day["date"]
        d0 = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=config.MARKET_TZ)
        lo = d0.astimezone(timezone.utc).isoformat()
        hi = (d0 + timedelta(days=1)).astimezone(timezone.utc).isoformat()
        acc = day.get("account", self.account)
        last = db.row("SELECT equity FROM equity WHERE account=? AND at >= ? AND at < ? ORDER BY at DESC LIMIT 1",
                      (acc, lo, hi))
        end_eq = last["equity"] if last else day["start_equity"]
        pnl = end_eq - day["start_equity"]
        pnl_pct = (pnl / day["start_equity"] * 100) if day["start_equity"] else 0.0
        orders = db.rows("SELECT * FROM orders WHERE account=? AND at >= ? AND at < ? AND status != 'rejected' ORDER BY id",
                         (acc, lo, hi))
        entries = [o for o in orders if o["kind"] == "entry"]
        exits = [o for o in orders if o["kind"] in ("exit", "flatten")]
        entry_px: dict[str, list[float]] = {}
        for o in entries:
            entry_px.setdefault(o["symbol"], []).append(o["price"] or 0)
        wins, losses, lines = 0, 0, []
        for o in exits:
            eps = entry_px.get(o["symbol"])
            if not eps or not o["price"]:
                continue
            ep = eps.pop(0)
            r = (o["price"] - ep) * o["qty"]
            wins += r > 0
            losses += r <= 0
            lines.append(f"{o['symbol']} {r:+.0f} USD")
        mode = orders[0]["mode"] if orders else self.mode
        title = f"Roblowe: deň {date[8:]}.{date[5:7]}. {pnl_pct:+.2f} % · {account_label(acc)}"
        usd = lambda v: f"{v:,.0f}".replace(",", " ")  # noqa: E731
        parts = [f"Equity {usd(end_eq)} USD ({'+' if pnl >= 0 else '−'}{usd(abs(pnl))} USD, {pnl_pct:+.2f} %)",
                 f"Obchody: {len(entries)} vstupov, {len(exits)} výstupov" + (f", {wins} v pluse / {losses} v mínuse" if exits else "")]
        if lines:
            parts.append("Výsledky: " + ", ".join(lines[:8]) + (" …" if len(lines) > 8 else ""))
        if day["halted"]:
            parts.append(f"STOP: {day['halt_reason']}")
        if mode == "dry":
            parts.append("Režim dry – nič sa neposielalo na burzu.")
        if self.market_note:
            parts.append(f"Trh: {self.market_note}")
        return title, "\n".join(parts)

    def send_daily_summaries(self, now: datetime) -> int:
        """Po zatvorení burzy pošle jeden súhrn za každý deň, ktorý ho ešte nemá."""
        today = self.trading_date(now)
        sent = 0
        for day in db.rows("SELECT * FROM days WHERE account=? AND summary_sent=0 AND date <= ? ORDER BY date",
                           (self.account, today)):
            if settings.get("notify_mode") in ("daily", "both"):
                title, text = self.build_daily_summary(day)
                self.notify(title, text)
            with db.tx():
                db.run("UPDATE days SET summary_sent=1 WHERE account=? AND date=?", (self.account, day["date"]))
                db.log_history("agent", "daily_summary", {"date": day["date"]})
            sent += 1
        return sent

    # -- ručné akcie z UI ----------------------------------------------------------------------
    def panic(self, actor: str) -> int:
        """Zavri všetko, zruš objednávky, zastav deň a vypni agenta."""
        positions = self.broker.positions()
        try:
            equity = self.broker.account().equity
        except Exception:  # noqa: BLE001
            equity = 0.0
        self.flatten_all(f"ručný STOP ({actor})", positions)
        if self._live():
            try:
                self.broker.cancel_all_orders()
            except Exception:  # noqa: BLE001
                log.exception("cancel_all_orders zlyhalo")
        date = self.trading_date(self._now())
        with db.tx():
            db.run("INSERT INTO days(account, date, start_equity, halted, halt_reason, created_at) VALUES (?,?,?,1,?,?) "
                   "ON CONFLICT(account, date) DO UPDATE SET halted=1, halt_reason=excluded.halt_reason",
                   (self.account, date, equity, f"ručný STOP ({actor})", db.now_iso()))
            db.log_history(actor, "panic", {"positions": len(positions)})
        settings.set_many({"agent_enabled": "0"}, [], actor)
        return len(positions)
