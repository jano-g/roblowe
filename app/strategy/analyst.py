"""Claude ako analytik správ. Vstup: čerstvé titulky + technický kontext; výstup: štruktúrovaný
JSON per ticker (sentiment, istota, katalyzátor). Rozhoduje engine + risk.py, nie model."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .. import config
from ..broker.base import NewsItem

log = logging.getLogger("roblowe.analyst")

SYSTEM = """Si skúsený intradenný analytik amerických akcií. Dostaneš čerstvé správy (titulok,
krátke zhrnutie, čas, zdroj) k sledovaným tickerom a krátky technický kontext.

Pre KAŽDÝ ticker, ku ktorému existuje relevantná správa, odhadni vplyv na cenu v horizonte
dnešnej obchodnej seansy (hodiny, nie týždne):
- sentiment: -1.0 (silne negatívne) až 1.0 (silne pozitívne). 0 = žiadny vplyv / šum.
- confidence: 0.0 až 1.0 – ako veľmi si istý, že správa pohne cenou dnes. Buď prísny:
  PR fluff, opakované staré správy, „analyst reiterates“ a súhrny trhu = nízka istota.
- catalyst: jedna stručná veta po slovensky, čo je katalyzátor (alebo „bez katalyzátora“).
- stale: true, ak je správa stará / už zacenená / duplikát.

Tickery bez relevantnej správy vynechaj. Nevymýšľaj fakty, ktoré v správach nie sú.
Trhový prehľad (SPY/QQQ) hodnoť podľa makro správ (Fed, CPI, geopolitika)."""

SCHEMA = {
    "type": "object",
    "properties": {
        "market_note": {"type": "string", "description": "1 veta o celkovej nálade trhu po slovensky"},
        "symbols": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "sentiment": {"type": "number"},
                    "confidence": {"type": "number"},
                    "catalyst": {"type": "string"},
                    "stale": {"type": "boolean"},
                },
                "required": ["symbol", "sentiment", "confidence", "catalyst", "stale"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["market_note", "symbols"],
    "additionalProperties": False,
}


@dataclass
class SymbolView:
    sentiment: float
    confidence: float
    catalyst: str
    stale: bool
    at: datetime


@dataclass
class Analysis:
    market_note: str
    symbols: dict[str, SymbolView]
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    headlines: int = 0
    raw: dict = field(default_factory=dict)


def _clamp(x, lo, hi):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return 0.0
    return max(lo, min(hi, x))


def build_prompt(news: list[NewsItem], tech_context: dict[str, str], watchlist: list[str]) -> str:
    lines = [f"Sledované tickery: {', '.join(watchlist)}", "", "Technický kontext (posledná sviečka):"]
    for s, ctx in tech_context.items():
        lines.append(f"- {s}: {ctx}")
    lines += ["", f"Správy ({len(news)}):"]
    for n in sorted(news, key=lambda x: x.at):
        when = n.at.astimezone(timezone.utc).strftime("%H:%M UTC")
        syms = ",".join(n.symbols[:6])
        summary = (n.summary or "").strip().replace("\n", " ")[:400]
        lines.append(f"- [{when}] ({syms}) {n.headline.strip()} — {summary} [{n.source}]")
    return "\n".join(lines)


# -- katalóg modelov (Anthropic Models API) ------------------------------------------------------
EFFORTS = ("low", "medium", "high", "xhigh", "max")
MODELS_TTL = 6 * 3600
# záloha, keď Models API nie je dostupné (bez kľúča / výpadok); poradie = odporúčané
FALLBACK_MODELS = [
    {"id": "claude-opus-5", "name": "Claude Opus 5", "efforts": list(EFFORTS)},
    {"id": "claude-fable-5-1", "name": "Claude Fable 5.1", "efforts": list(EFFORTS)},
    {"id": "claude-sonnet-5", "name": "Claude Sonnet 5", "efforts": list(EFFORTS)},
    {"id": "claude-haiku-4-5", "name": "Claude Haiku 4.5", "efforts": []},
]
_models_cache: dict = {"at": 0.0, "key": None, "items": None}


def _cap(caps, *path) -> bool:
    cur = caps
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return False
        cur = cur[k]
    return bool(cur.get("supported")) if isinstance(cur, dict) else bool(cur)


def list_models(api_key: str, force: bool = False) -> tuple[list[dict], str | None]:
    """Aktuálne modely z Models API, ktoré vedia štruktúrovaný výstup (analytik ho potrebuje).
    Vráti (zoznam, chyba). Cache 6 h; pri chybe posledný známy zoznam alebo záloha."""
    import time as _time

    c = _models_cache
    if not force and c["items"] and c["key"] == api_key and _time.time() - c["at"] < MODELS_TTL:
        return c["items"], None
    if not api_key:
        return FALLBACK_MODELS, "Chýba Anthropic API kľúč – zobrazujem predvolený zoznam."
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key, timeout=15.0, max_retries=1)
        items = []
        for m in client.models.list():  # auto-paginácia
            caps = getattr(m, "capabilities", None) or {}
            if caps and not _cap(caps, "structured_outputs"):
                continue
            efforts = [e for e in EFFORTS if _cap(caps, "effort", e)] if _cap(caps, "effort") else []
            created = getattr(m, "created_at", None)
            items.append({"id": m.id, "name": getattr(m, "display_name", None) or m.id, "efforts": efforts,
                          "created": created.isoformat() if hasattr(created, "isoformat") else str(created or "")})
        items.sort(key=lambda x: x["created"], reverse=True)
        if not items:
            raise ValueError("prázdny zoznam")
        c.update(at=_time.time(), key=api_key, items=items)
        return items, None
    except Exception as e:  # noqa: BLE001 – katalóg je pomocný, nikdy nesmie zhodiť appku
        log.warning("Models API nedostupné: %s", e.__class__.__name__)
        return (c["items"] if c["items"] and c["key"] == api_key else FALLBACK_MODELS), \
            "Zoznam modelov sa nepodarilo načítať z Anthropic – zobrazujem posledný známy."


def model_info(api_key: str, model_id: str) -> dict | None:
    items, _ = list_models(api_key)
    return next((m for m in items if m["id"] == model_id), None)


class ClaudeAnalyst:
    def __init__(self, api_key: str | None = None, model: str = "claude-opus-5", effort: str = "medium"):
        import anthropic  # lokálny import: analytik je voliteľný

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key or None, timeout=90.0, max_retries=2)
        self.model = model
        self.effort = effort
        self.info = model_info(api_key or "", model) if api_key else None

    def request_params(self, prompt: str) -> dict:
        """Parametre požiadavky prispôsobené modelu: effort len keď ho model podporuje (inak najbližší
        nižší), server-side fallback len pre Opus 5 / Fable 5.x, kde ho API ponúka."""
        info = self.info or {}
        efforts = info.get("efforts", list(EFFORTS) if not info else [])
        output_config: dict = {"format": {"type": "json_schema", "schema": SCHEMA}}
        if efforts:
            want = self.effort if self.effort in EFFORTS else "medium"
            ok = [e for e in EFFORTS[: EFFORTS.index(want) + 1] if e in efforts]
            output_config["effort"] = ok[-1] if ok else efforts[0]
        params = {
            "model": self.model,
            "max_tokens": 4000,
            "system": [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": prompt}],
            "output_config": output_config,
        }
        if self.model.startswith(("claude-opus-5", "claude-fable-5")):
            params["betas"] = ["server-side-fallback-2026-07-01"]
            params["fallbacks"] = "default"
        return params

    def analyze(self, news: list[NewsItem], tech_context: dict[str, str], watchlist: list[str]) -> Analysis | None:
        if not news:
            return None
        prompt = build_prompt(news, tech_context, watchlist)
        try:
            resp = self.client.beta.messages.create(**self.request_params(prompt))
        except self._anthropic.RateLimitError:
            log.warning("Claude: rate limit, analýza preskočená")
            return None
        except self._anthropic.APIStatusError as e:
            log.warning("Claude: API chyba %s", e.status_code)
            return None
        except self._anthropic.APIConnectionError:
            log.warning("Claude: sieťová chyba")
            return None
        if resp.stop_reason == "refusal":
            log.warning("Claude: požiadavka odmietnutá (%s)", getattr(resp.stop_details, "category", None))
            return None
        text = next((b.text for b in resp.content if b.type == "text"), None)
        if not text:
            return None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            log.warning("Claude: neplatný JSON")
            return None
        return parse_analysis(data, model=resp.model, headlines=len(news),
                              input_tokens=getattr(resp.usage, "input_tokens", 0) or 0,
                              output_tokens=getattr(resp.usage, "output_tokens", 0) or 0)


def parse_analysis(data: dict, *, model: str, headlines: int = 0, input_tokens: int = 0, output_tokens: int = 0,
                   at: datetime | None = None) -> Analysis:
    at = at or datetime.now(timezone.utc)
    symbols: dict[str, SymbolView] = {}
    for item in data.get("symbols") or []:
        sym = str(item.get("symbol", "")).upper().strip()
        if not sym:
            continue
        symbols[sym] = SymbolView(
            sentiment=_clamp(item.get("sentiment"), -1, 1),
            confidence=_clamp(item.get("confidence"), 0, 1),
            catalyst=str(item.get("catalyst") or "")[:300],
            stale=bool(item.get("stale")),
            at=at,
        )
    return Analysis(market_note=str(data.get("market_note") or "")[:300], symbols=symbols, model=model,
                    input_tokens=input_tokens, output_tokens=output_tokens, headlines=headlines, raw=data)


def make_analyst() -> ClaudeAnalyst | None:
    from .. import settings

    key = settings.get("anthropic_api_key")
    if not settings.get("analyst_enabled") or not key:
        return None
    return ClaudeAnalyst(key, settings.get("analyst_model"), settings.get("analyst_effort"))
