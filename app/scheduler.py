"""Jeden daemon thread, tick 30 s. Joby sú idempotentné; stav je v DB.
- obchodný cyklus každých `cycle_minutes` počas otvorenej burzy (Alpaca clock)
- denná záloha v `backup_time`
- čistenie starých rozhodnutí (30 dní)"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

from . import config, db, settings
from .broker.alpaca import AlpacaBroker
from .broker.fake import FakeBroker
from .services import backup, ntfy
from .strategy.analyst import make_analyst
from .strategy.engine import Engine, NewsState

log = logging.getLogger("roblowe.scheduler")

TICK = 30


def build_broker(mode: str):
    """Vráti (broker, efektívny režim). Bez kľúčov spadne do dry s FakeBroker, nech sa nič neposiela."""
    key_id, secret = settings.alpaca_creds(mode)
    if key_id and secret:
        # dry režim s paper kľúčmi = skutočné dáta z Alpaca, žiadne objednávky
        return AlpacaBroker(key_id, secret, config.alpaca_trading_url(mode)), mode
    if mode != "dry":
        log.error("Režim %s bez Alpaca kľúčov – bežím ako dry s FakeBroker.", mode)
    else:
        log.warning("Bez Alpaca kľúčov: používam FakeBroker so syntetickými dátami (len na vyskúšanie UI).")
    return FakeBroker(symbols=settings.get("watchlist")), "dry"


class Scheduler:
    def __init__(self):
        self.engines: list[Engine] = []
        self.last_cycle_at: datetime | None = None
        self.last_error: str | None = None
        self.last_backup_day: str | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.rebuild()
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)
        self._thread.start()

    @property
    def engine(self) -> Engine | None:
        """Hlavný (prvý) engine – hodiny burzy, hlavička appky."""
        return self.engines[0] if self.engines else None

    @property
    def broker(self):
        return self.engine.broker if self.engine else None

    @property
    def mode(self) -> str:
        return self.engine.mode if self.engine else settings.mode()

    def engine_for(self, account: str) -> Engine | None:
        return next((e for e in self.engines if e.account == account), None)

    def rebuild(self) -> None:
        """Nové brokery + enginy podľa aktuálnych nastavení (zmena režimu / kľúčov / brokera)."""
        with self._lock:
            news = NewsState()
            analyst = make_analyst()
            br, m = build_broker(settings.mode())
            self.engines = [Engine(br, analyst, m, ntfy.notify, news=news)]
            self.last_error = None
            for e in self.engines:
                log.info("účet %s (%s), režim %s", e.account, e.broker.__class__.__name__, e.mode)
                if e.mode == "live":
                    log.warning("!!! LIVE REŽIM – skutočné peniaze (%s) !!!", e.account)

    def stop(self) -> None:
        self._stop.set()

    def reload_analyst(self) -> None:
        analyst = make_analyst()
        for e in self.engines:
            e.analyst = analyst

    def _cycle_all(self) -> list[dict]:
        """Cyklus pre každý účet. Chyba jedného účtu nezastaví ostatné."""
        reports, errors = [], []
        for e in self.engines:
            try:
                rep = e.cycle().as_dict()
            except Exception as ex:  # noqa: BLE001
                log.exception("cyklus %s zlyhal", e.account)
                errors.append(f"{e.account}: {ex.__class__.__name__}: {ex}")
                rep = {"status": "error", "notes": [str(ex)], "account": e.account}
            rep["account"] = e.account
            reports.append(rep)
        self.last_error = "; ".join(errors) or None
        if errors:
            ntfy.notify("Roblowe: CHYBA", self.last_error[:300])
        return reports

    def run_cycle_now(self) -> list[dict]:
        with self._lock:
            reports = self._cycle_all()
            self.last_cycle_at = datetime.now(config.TZ)
            return reports

    def _loop(self) -> None:
        log.info("scheduler beží (režim %s)", self.mode)
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:  # noqa: BLE001
                log.exception("tick zlyhal")
                self.last_error = f"{e.__class__.__name__}: {e}"
                ntfy.notify("Roblowe: CHYBA", self.last_error[:300])
            finally:
                db.rollback_if_open()
            self._stop.wait(TICK)

    def _tick(self) -> None:
        now = datetime.now(config.TZ)
        # obchodný cyklus
        every = max(1, settings.get("cycle_minutes"))
        due = self.last_cycle_at is None or (now - self.last_cycle_at).total_seconds() >= every * 60 - 1
        if due:
            with self._lock:
                self._cycle_all()
                self.last_cycle_at = now
        # záloha
        day = now.strftime("%Y-%m-%d")
        if settings.get("backup_enabled") and self.last_backup_day != day:
            hh, mm = settings.get("backup_time").split(":")
            if (now.hour, now.minute) >= (int(hh), int(mm)):
                last = db.q1("SELECT at FROM history WHERE event='backup' ORDER BY id DESC LIMIT 1")
                if not last or datetime.fromisoformat(last["at"]).astimezone(config.TZ).strftime("%Y-%m-%d") != day:
                    backup.run_backup("auto")
                self.last_backup_day = day
        # upratovanie
        if now.hour == 4 and now.minute < 1:
            db.run("DELETE FROM decisions WHERE at < datetime('now', '-30 days')")
            db.run("DELETE FROM equity WHERE at < datetime('now', '-90 days')")
            db.run("DELETE FROM news_seen WHERE at < datetime('now', '-7 days')")


scheduler = Scheduler()
