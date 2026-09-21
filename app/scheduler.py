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
from .strategy.engine import Engine

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
        self.engine: Engine | None = None
        self.broker = None
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
    def mode(self) -> str:
        return self.engine.mode if self.engine else settings.mode()

    def rebuild(self) -> None:
        """Nový broker + engine podľa aktuálnych nastavení (zmena režimu / kľúčov)."""
        with self._lock:
            self.broker, mode = build_broker(settings.mode())
            self.engine = Engine(self.broker, make_analyst(), mode, ntfy.notify)
            self.last_error = None
            log.info("broker %s, režim %s", self.broker.__class__.__name__, mode)
            if mode == "live":
                log.warning("!!! LIVE REŽIM – skutočné peniaze !!!")

    def stop(self) -> None:
        self._stop.set()

    def reload_analyst(self) -> None:
        if self.engine:
            self.engine.analyst = make_analyst()

    def run_cycle_now(self) -> dict:
        with self._lock:
            rep = self.engine.cycle()
            self.last_cycle_at = datetime.now(config.TZ)
            self.last_error = None
            return rep.as_dict()

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
                self.engine.cycle()
                self.last_cycle_at = now
                self.last_error = None
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
