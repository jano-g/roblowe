"""ntfy.sh push (JSON publish, UTF-8 nadpisy). Best-effort, nikdy nevyhodí výnimku."""
from __future__ import annotations

import logging
import threading

import httpx

from .. import settings

log = logging.getLogger("roblowe.ntfy")


def _send(server: str, topic: str, title: str, message: str, priority: int, tags: list[str]) -> None:
    try:
        r = httpx.post(server.rstrip("/"), json={"topic": topic, "title": title, "message": message,
                                                  "priority": priority, "tags": tags}, timeout=10)
        if r.status_code >= 400:
            log.warning("ntfy %s", r.status_code)
    except httpx.HTTPError as e:
        log.warning("ntfy zlyhalo: %s", e.__class__.__name__)


def notify(title: str, message: str, priority: int = 3, tags: list[str] | None = None) -> None:
    topic = settings.get("ntfy_topic")
    server = settings.get("ntfy_server")
    if not topic or not server.startswith("https://"):
        return
    if "STOP" in title or "CHYBA" in title:
        priority = max(priority, 4)
    threading.Thread(target=_send, args=(server, topic, title, message, priority, tags or ["chart_with_upwards_trend"]),
                     daemon=True).start()


def test(server: str, topic: str) -> tuple[bool, str]:
    if not server.startswith("https://") or not topic:
        return False, "ntfy server musí byť https:// a téma vyplnená."
    try:
        r = httpx.post(server.rstrip("/"), json={"topic": topic, "title": "Roblowe", "message": "Skúšobná notifikácia."},
                       timeout=10)
        return r.status_code < 400, f"ntfy odpovedal {r.status_code}"
    except httpx.HTTPError as e:
        return False, f"ntfy nedostupný: {e.__class__.__name__}"
