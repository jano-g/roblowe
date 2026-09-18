# Deploy — Roblowe (roblowe.gordulic.sk)

Cieľ: VPS `niko1` (Ubuntu, Docker + compose, nginx + certbot už bežia). Rovnaký vzor ako `kal`:
stack v `/opt/roblowe`, kontajner iba na `127.0.0.1:8120`, nginx robí HTTPS.

## 0. Účty, ktoré potrebuješ (raz)
1. **Alpaca** – https://app.alpaca.markets → registrácia (podporuje aj klientov mimo USA, pri
   registrácii over, že Slovensko je v zozname). Po prihlásení prepni vpravo hore na **Paper
   Trading** → *API Keys* → *Generate*. Ulož si `Key ID` aj `Secret` (secret sa zobrazí len raz).
   Paper účet má fiktívnych 100 000 USD a je zadarmo. **Live účet má iné kľúče** – tie zatiaľ nepotrebuješ.
2. **Anthropic API kľúč** – https://console.anthropic.com → API Keys. Nastav si tam mesačný
   limit útraty (napr. 50 USD), aby ťa nič neprekvapilo.
3. **ntfy** – nainštaluj appku ntfy (iOS/Android), vymysli dlhú náhodnú tému (napr.
   `roblowe-7f3a9c2e1b`) a prihlás sa na ňu v appke.

## 1. DNS
Na websupport.sk pridaj `A` záznam `roblowe.gordulic.sk` → IP VPS (`5.22.220.32`). Over
`dig +short roblowe.gordulic.sk`, inak certbot zlyhá.

## 2. Klon a konfigurácia
```bash
cd /opt
git clone git@github.com:jano-g/roblowe.git        # deploy key na serveri už je
cd roblowe
cp .env.example .env
chmod 600 .env
nano .env
```
V `.env` vyplň aspoň:
- `APP_URL=https://roblowe.gordulic.sk`
- `ADMIN_PASSWORD` (aspoň 8 znakov; platí len pri prvom štarte)
- `SECRET_KEY` — `openssl rand -hex 32` (ak necháš prázdne, vygeneruje sa do volume)
- `TRADING_MODE=dry` na prvý týždeň, potom `paper`
- `ALPACA_KEY_ID`, `ALPACA_SECRET_KEY` (paper kľúče)
- `ANTHROPIC_API_KEY`
- `NTFY_TOPIC`
- B2 môžeš vyplniť aj neskôr v appke (Nastavenia → Zálohy).

## 3. Štart
```bash
docker compose up -d --build
docker logs roblowe --tail 20        # "Roblowe started (režim dry, ...)" + "Uvicorn running"
curl -s http://127.0.0.1:8120/healthz   # {"ok":true,"mode":"dry"}
```
Dáta žijú vo volume `roblowe_roblowe_data` (`/data`: `roblowe.db`, `secret.key`, `backups/`).

## 4. nginx + HTTPS
```bash
cp docs/nginx/roblowe.gordulic.sk.conf /etc/nginx/sites-available/roblowe.gordulic.sk
ln -s /etc/nginx/sites-available/roblowe.gordulic.sk /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
certbot --nginx -d roblowe.gordulic.sk
curl -sI https://roblowe.gordulic.sk/ | head -1     # musí byť 200/303, nie 301 (pozri PICUS pasca)
```

## 5. Prvé spustenie v prehliadači
1. Otvor https://roblowe.gordulic.sk, prihlás sa (`ADMIN_USERNAME` / `ADMIN_PASSWORD`).
2. **Nastavenia → Notifikácie**: téma ntfy → *Poslať skúšobnú notifikáciu*.
3. **Nastavenia → Zálohy**: B2 keyID/applicationKey/bucket → *Otestovať B2* → *Zálohovať teraz*.
4. **Nastavenia → Agent**: uprav watchlist (10–15 likvidných tickerov stačí). Rizikové limity
   nechaj defaultné, kým nemáš dáta.
5. **Prehľad**: *Vyhodnotiť teraz* – počas otvorenej burzy (15:30–22:00 nášho času) uvidíš
   rozhodnutia s dôvodmi v záložke Obchody. Mimo seansy uvidíš „Burza zatvorená“.
6. Prepínač v hlavičke **zapni agenta**. V `dry` režime sa nič neposiela, len sa loguje.
7. Na mobile: Zdieľať → *Pridať na plochu*.

### Prechod dry → paper → live
- Po ~týždni v `dry`: `.env` → `TRADING_MODE=paper` → `docker compose up -d`. Sleduj 4–8 týždňov.
- Live: nový live účet v Alpaca (KYC, vklad), **live kľúče** do `.env`, `TRADING_MODE=live`,
  `LIVE_CONFIRM=I_UNDERSTAND_THE_RISK`, reštart, potom v appke zapnúť agenta a napísať LIVE.
  Začni sumou, ktorej stratu unesieš. Pod 25 000 USD platí pravidlo PDT (README).

## 6. Aktualizácia
```bash
cd /opt/roblowe && git pull && docker compose up -d --build
```
`.env` a dáta zostávajú. Reštart cez deň je bezpečný: stop-loss/take-profit sú u brokera
(bracket), agent po štarte pokračuje ďalším cyklom.

## 7. Zálohy a obnova
- Denne (`BACKUP_TIME`, default 03:30) online kópia SQLite do `/data/backups/` (ponechá N) + B2.
- Ručne: Nastavenia → Zálohy → *Zálohovať teraz*, alebo `docker compose exec roblowe python -m app.cli backup`.
- Obnova: `docker compose stop roblowe`, skopíruj zálohu na miesto `/data/roblowe.db` cez
  `docker run --rm -v roblowe_roblowe_data:/data -v $PWD:/b alpine cp /b/<zaloha>.db /data/roblowe.db`,
  `docker compose start roblowe`. **Nikdy nekopíruj .db do bežiaceho kontajnera.**
- `.env` (kľúče!) nie je v B2 z appky – pokrýva ho VPS-level restic (picus `infra/`).

## 8. Údržba
| Čo | Ako |
|---|---|
| Zabudnuté heslo | `docker compose exec roblowe python -m app.cli set-password jano` |
| Logy | `docker logs roblowe --tail 100 -f` |
| Health | `curl -s http://127.0.0.1:8120/healthz` |
| Núdzové zatvorenie všetkého | Prehľad → **STOP** (alebo priamo v Alpaca web UI → Close All) |
| Zmena režimu / kľúčov | `.env` + `docker compose up -d` |

## Troubleshooting
| Symptóm | Príčina / riešenie |
|---|---|
| Kontajner padá s „ADMIN_PASSWORD musí mať aspoň 8 znakov“ | doplň heslo v `.env`, `docker compose up -d` |
| „TRADING_MODE=paper/live vyžaduje ALPACA_KEY_ID…“ | doplň kľúče (paper vs. live sa líšia!) |
| Prehľad: „Broker nedostupný“ | zlé kľúče alebo paper kľúče v live režime; `docker logs` ukáže status 401/403 |
| Záložka Správy: „Analytik vypnutý“ | chýba `ANTHROPIC_API_KEY` alebo `analyst_enabled` vypnuté v Nastaveniach |
| Agent nič nekupuje | pozri Obchody → rozhodnutia „preskočené“ s dôvodom (okno, PDT, max. pozícií, skóre) |
| Login vždy odmietnutý | heslo z `.env` platí len pri prvom štarte; reset cez `app.cli set-password` |
| Rate limit blokuje po jednom zlom hesle | nginx musí posielať `X-Forwarded-For $proxy_add_x_forwarded_for` |
| 502 z nginx | `docker ps` – beží? port 8120 v `.env` aj vo vhoste rovnaký? |
| ERR_TOO_MANY_REDIRECTS | `return 301` v 443 bloku – vymaž, redirect patrí len do bloku na porte 80 |
