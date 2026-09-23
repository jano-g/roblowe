# Roblowe – obchodný agent

Roblowe je automatický intradenný obchodný agent na americké akcie. Beží na serveri, počas
otvorenej burzy každých pár minút vyhodnotí sledované tickery, číta čerstvé správy cez Claude,
a ak sedia technické signály aj správy, otvorí pozíciu s pevným stop-lossom a cieľom. Pred
zatvorením burzy všetko zavrie – nikdy nedrží pozície cez noc. Všetko vidíš a ovládaš
z mobilu na dashboarde.

> **Čítaj pred prvým zapnutím – Riziká.** Žiadny agent nezaručuje zisk. Väčšina intradenných
> obchodníkov dlhodobo stráca a poplatky, spread a dane hrajú proti tebe. Roblowe začni na
> **paper účte** (fiktívne peniaze), nechaj ho bežať aspoň 4–8 týždňov, pozeraj sa na krivku equity
> a na dôvody rozhodnutí, a až potom sa rozhodni, či mu zveríš malú sumu, ktorú si môžeš dovoliť
> stratiť.

## Ako to funguje

1. **Dáta.** Sviečky (5 min) a správy k tickerom z watchlistu berie z Alpaca Markets.
2. **Technické skóre** (−1…1): trend EMA9/EMA21, RSI, poloha voči VWAP, objem.
3. **Skóre zo správ** (−1…1): Claude dostane nové titulky + technický kontext a vráti pre každý
   ticker sentiment, istotu a katalyzátor. Staré/šumové správy označí a agent ich ignoruje.
4. **Kombinácia**: `0.6 × technika + 0.4 × správy`. Bez čerstvých správ rozhoduje technika sama
   (s nižším maximom). Negatívna správa vetuje vstup a zavrie pozíciu.
5. **Riziko (nemenné poradie, v kóde):**
   - max. strata na obchod 0,5 % equity (stop = vstup − 1,5 × ATR),
   - max. 10 % equity v jednej pozícii, max. 4 pozície,
   - denná strata 2 % → všetko zavrie a do zajtra nič nekúpi,
   - 10 minút pred zatvorením zavrie všetko, prvých 15 minút a poslednú hodinu nevstupuje,
   - pravidlo PDT (pod 25 000 USD max. 3 day-trady za 5 dní).
6. **Objednávky** sú bracket (market vstup + stop-loss + take-profit u brokera), takže stop drží
   aj keď appka spadne.

Claude nikdy neposiela objednávky – dodáva iba skóre. Všetko ostatné je deterministický kód,
ktorý si vieš prečítať v `app/strategy/`.

## Broker: Alpaca alebo Trading 212

V Nastavenia → Broker a kľúče si vyberieš, kde agent obchoduje. Ceny, sviečky, správy a hodiny
burzy sú v oboch prípadoch z Alpaca – Trading 212 API ich nemá. Stačia na to bezplatné Alpaca
paper kľúče, vklad na Alpaca netreba.

| | Alpaca (USA) | Trading 212 (EÚ, Cyprus) |
|---|---|---|
| Vklad / výber | medzinárodný prevod v USD | karta, Apple Pay, SEPA; výber zadarmo |
| Pravidlo PDT (pod 25 000 USD max. 3 day-trady / 5 dní) | platí | neplatí |
| Stop-loss a cieľ | bracket objednávka u brokera | stop-loss u brokera (platí do zrušenia), cieľ stráži agent každý cyklus |
| Skúšobný účet | paper | demo (Practice) |

**Trading 212 a mena účtu.** API obchoduje len v primárnej mene účtu. Pri účte v EUR sa pri každom
nákupe aj predaji americkej akcie platí prevod 0,15 %. V appke Trading 212 nastav Currency options →
nákup aj predaj na *Asset currency* a po prvých obchodoch na demo účte over v histórii, či sa
poplatok za prevod účtuje. Sumy účtu v EUR appka prepočíta na USD denným kurzom ECB.

Ak Trading 212 niektorý ticker z watchlistu nemá, agent ho preskočí a v Obchodoch uvidíš dôvod.
Agent zavrie každú pozíciu, ktorú v ten deň sám neotvoril – účet nechaj len agentovi.

## Režimy

| Režim | Čo robí | Kedy |
|---|---|---|
| `dry` | Rozhoduje, loguje, na burzu neposiela nič. S Alpaca kľúčmi používa reálne dáta. | prvé dni – sleduj, či dôvody dávajú zmysel |
| `paper` | Obchoduje na Alpaca paper alebo Trading 212 demo účte (fiktívne peniaze). | 4–8 týždňov minimum |
| `live` | Skutočné peniaze. Prepnutie vyžaduje heslo a napísať LIVE; zapnutie agenta v live režime ďalšie LIVE. | až keď paper výsledky presvedčia |

Režim a API kľúče (Alpaca paper, Alpaca live, Anthropic) sa nastavujú v appke: **Nastavenia →
Broker a kľúče**. Každá zmena vyžaduje tvoje heslo, po zmene sa agent vypne a zapneš ho vedome
znova. Kľúče sa do prehliadača nikdy nevracajú. Bez reštartu kontajnera.

## Dashboard

- **Prehľad** – equity, dnešný výsledok, otvorené pozície (dá sa zavrieť ručne), krivka equity,
  tlačidlá *Vyhodnotiť teraz* a **STOP** (zavrie všetko, zruší objednávky, vypne agenta).
- **Obchody** – objednávky a každé rozhodnutie agenta s dôvodom (aj prečo NEkúpil).
- **Správy** – analýzy Claude: sentiment, istota, katalyzátor, spotreba tokenov.
- **Nastavenia** – broker a kľúče (režim dry/paper/live, chránené heslom), watchlist, rizikové
  limity, váhy, notifikácie (ntfy), zálohy (B2), heslo.
- Prepínač v hlavičke zapína/vypína agenta. V live režime pýta potvrdenie.

Na mobile: Zdieľať → *Pridať na plochu* (PWA).

## Notifikácie

ntfy.sh téma (dlhá, náhodná – funguje ako heslo). V Nastavenia → Notifikácie si vyberieš, čo chodí:
- **každý obchod** – push pri kúpe aj predaji (ticker, kusy, cena, stop, cieľ, dôvod),
- **denný súhrn** – jedna správa po zatvorení burzy: equity, výsledok dňa v USD a %, počet
  vstupov/výstupov, koľko obchodov skončilo v pluse a mínuse, prípadný STOP a nálada trhu,
- **oboje** (default).

STOP, denný limit straty a chyby chodia vždy. *Poslať skúšobnú notifikáciu* a *Poslať súhrn dňa
teraz* slúžia na overenie.

## Zálohy

Denne (03:30) online kópia SQLite do `/data/backups/` a upload do Backblaze B2 (ak vyplnené).
Zlyhanie B2 nikdy nestratí lokálnu kópiu. Nastavenia → Zálohy → *Otestovať B2*, *Zálohovať teraz*.

## Riziká a čo musíš vedieť

- **Stratégia nie je otestovaná na histórii.** Technické skóre je jednoduchá heuristika, skóre
  zo správ je odhad jazykového modelu. Paper trading je jediný spôsob, ako zistiť, či to
  na dnešnom trhu funguje. Sleduj: koľko obchodov končí na stope vs. na cieli, priemerný zisk/stratu,
  dni s halt-om.
- **Náklady.** Alpaca akcie sú bez komisie, ale platíš spread. Claude: pri 60 analýzach denne
  a ~3 000 tokenoch na analýzu je to zhruba 1–2 USD/deň (`ANALYST_DAILY_BUDGET_CALLS` to stropuje).
- **Dane (SR).** Zisky z predaja cenných papierov držaných menej než rok sú zdaniteľný príjem
  (§ 8 ZDP, 19/25 %), plus zdravotné poistenie. Alpaca pošle ročný výpis; vyplň W-8BEN, aby sa
  neuplatnila plná US zrážková daň z dividend.
- **PDT.** Pod 25 000 USD Alpaca margin účet obmedzuje day-trading na 3 obchody za 5 dní.
  S malou sumou bude agent väčšinu času čakať. Buď to akceptuj, alebo `RESPECT_PDT=0` na
  vlastné riziko (Alpaca účet potom zablokuje).
- **Výpadky.** Ak appka spadne, stop-loss a take-profit sú u brokera (bracket), ale „zavri
  pred koncom seansy“ neprebehne – pozícia môže ostať cez noc. Sleduj ntfy chybové notifikácie.
- **Syntetické dáta.** Bez Alpaca kľúčov beží FakeBroker s náhodnými cenami – slúži len na
  vyskúšanie UI, nič nehovorí o trhu.
- **Prístup do dashboardu = prístup k účtu.** Kto sa prihlási, vie prepnúť na live (s heslom).
  Používaj dlhé heslo a ntfy notifikácie – o každej zmene režimu príde push.

## Vývoj

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
DATA_DIR=./data ADMIN_PASSWORD=heslo-heslo-123 .venv/bin/uvicorn app.main:app --port 8120 --reload
.venv/bin/pytest -q
```
Nasadenie na VPS: `DEPLOY.md`. Konvencie pre ďalší vývoj: `CLAUDE.md`.
