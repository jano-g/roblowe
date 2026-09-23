# Roblowe

Automatický intradenný obchodný agent (US akcie cez Alpaca) s dashboardom na roblowe.gordulic.sk.
Každých N minút počas otvorenej burzy: technické skóre zo sviečok + skóre zo správ (Claude) →
tvrdé rizikové mantinely (`app/strategy/risk.py`) → bracket objednávky (stop-loss + take-profit).
Žiadne pozície cez noc. Režim `dry` / `paper` / `live` a API kľúče sa menia v appke
(Nastavenia → Broker, `POST /api/broker`: heslo + pri live napísané LIVE; agent sa po zmene vypne).

> Infra a deploy: picus `PICUS.md`. Kroky deployu: `DEPLOY.md`. Používateľský návod: `README.md`.

## Stack (keep it this simple)
- Python 3.12 + FastAPI, SQLite (WAL) v `/data/roblowe.db`, Jinja len pre login, inak jedna SPA
  (`app/static/js/app.js`, `el()` builder — nikdy `innerHTML` s dátami; `app/static/css/app.css`).
  Bez build stepu, ORM, Redis, pandas.
- Jeden uvicorn proces: `app/scheduler.py` (thread, tick 30 s) spúšťa `Engine.cycle()`, dennú zálohu
  a upratovanie. Migrácie `app/db.py:MIGRATIONS` (PRAGMA user_version), forward-only.
- Broker: `app/broker/alpaca.py` (REST cez httpx, trading + data + news), `fake.py` pre testy a dry bez
  kľúčov. Rozhranie `base.py:Broker` s `account_key`. Iný broker nie je (Trading 212 bol odstránený pre
  poplatky za prevod meny pri každom obchode; jeho história v DB ostáva ako `trading212:*`).
- Štatistiky sú per účet (`account_key`: alpaca:paper|alpaca:live|fake):
  `days` (PK account+date), `equity`, `orders`, `decisions` majú stĺpec `account`; engine píše/číta
  len `self.account`, API berie `?account=` (default aktuálny). Správy od Claude sú spoločné.
- `scheduler.engines` je zoznam (dnes vždy jeden Engine), API hľadá engine cez `engine_for(account)`.
- Stratégia: `signals.py` (EMA/RSI/ATR/VWAP → skóre), `analyst.py` (Claude, štruktúrovaný JSON),
  `risk.py` (sizing, denná strata), `engine.py` (poradie krokov je zámerné a nemenné).
- `.env` je základ, `settings.OVERRIDABLE` sa dá prepísať v Nastaveniach (DB vyhráva); tajomstvá
  (`SECRET_KEYS`) sa nikdy nevracajú do prehliadača. `settings.BROKER_KEYS` (režim, Alpaca paper/live
  kľúče, Anthropic kľúč) idú len cez `/api/broker` (heslo), nie cez `PUT /api/settings`.
  `scheduler.rebuild()` vymení brokera a engine bez reštartu; `scheduler.mode` je efektívny režim
  (bez kľúčov spadne do dry s FakeBroker).

## Konvencie
- UI a notifikácie po slovensky, krátke. Wall-clock v `config.TZ`, obchodný deň v `config.MARKET_TZ`
  (America/New_York), do DB ISO UTC.
- Každá zápisová API cesta: hlavička `X-Requested-With: roblowe` (CSRF), `_user()`, `db.tx()`,
  `db.log_history()`. Nebezpečné akcie (STOP, live zapnutie) vyžadujú napísaný potvrdzovací text.
- Claude nikdy neposiela objednávky. Dodáva `SymbolView` (sentiment, istota, katalyzátor) a engine
  ho použije len cez `combined_score()` a vetá. Zmena váh/hraníc = Nastavenia, nie kód.
- Nová funkcia = API + UI + test (`tests/`, `pytest`). Engine testuj cez `FakeBroker`.
- Commit a push po každej ucelenej zmene **rovno do `main`** (žiadne feature vetvy ani PR, kým Jano
  výslovne nepovie „sprav vetvu“). VPS ťahá `main`: `git pull && docker compose up -d --build`.

## Lokálny vývoj
```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp .env.example .env
DATA_DIR=./data ADMIN_PASSWORD=heslo-heslo-123 .venv/bin/uvicorn app.main:app --port 8120 --reload
.venv/bin/pytest -q
```
Bez Alpaca kľúčov beží `FakeBroker` (syntetické dáta, burza „stále otvorená“) – stačí na UI a testy.

## Gotchas
- `sqlite3.executescript` sám commitne transakciu → BEGIN/COMMIT patrí do skriptu (db.migrate).
- Bracket objednávky vyžadujú celé akcie (žiadne fractional); `close_position` najprv ruší TP/SL nohy.
- Pravidlo PDT neexistuje od 4. 6. 2026 (FINRA); Alpaca odstránila polia `daytrade_count` a
  `pattern_day_trader` – nepoužívať, kúpnu silu hlási `buying_power`.
- `mins_since_open` počíta 6,5 h seansu – v skrátené dni (13:00 ET) je okno „prvých N minút“ mäkšie.
- Alpaca IEX feed je bezplatný, ale zobrazuje len IEX objem; na SIP treba platený plán.
- Bez ALPACA kľúčov v dry režime sú dáta syntetické – nič z toho nehovorí o reálnom trhu.
- SPA: automatická obnova pri návrate do appky prekresľuje len Prehľad/Obchody/Správy (nikdy formulár);
  neuložené hodnoty formulárov (okrem hesiel a kľúčov) drží `sessionStorage` (`draftify`).
- Malé VPS (1.8 GiB): `mem_limit: 256m`; žiadne pandas/numpy.
