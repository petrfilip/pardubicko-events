# Plán fáze 3: SQLite jako jediný zdroj pravdy

Rozpracovává ADR 0008 do etap. Každá etapa je samostatně nasaditelná a má
kritérium přijetí. Odhady jsou hrubé a počítají s prací agenta pod dohledem
správce.

## Stav k 23. 9. 2026

| Etapa | Stav |
|---|---|
| 0 | Hotovo: rozpracovaná data a příkazy pipeline ze 4. 8. jsou commitnuté. |
| 1 | Hotovo a nasazeno 23. 9. 2026 na https://pardubicko.tix.cz: schéma v `web/migrations/`, doménová vrstva, deduplikace se stejnými skóre jako `matching.py`, historie změn, API v1 s tokeny, `web/bin/pardubicko`, `bin/prepare-initial-db`, `bin/deploy`. Ověřeno: stránky, autentizace přes Apache, zkušební obnova ze snímku, denní snímek v cronu ve 4:50. Data v gitu jsou zmrazená (`pipeline.py` zápis odmítne). Vzdálenou zálohu řeší správce. |
| 2 | Rozpracováno. Hotovo: CLI klient `tools/client/pardubicko_client.py` nad celým API v1; `run.py` jako klient API s lokální cache jen pro ETagy a snapshoty; `POST /api/v1/runs` a `GET /api/v1/runs/{id}`; zdraví zdrojů počítá server (`HealthService`, převod `health.py`). Ověřeno E2E proti PHP API (`web/tests/run_pipeline_e2e.py`). Nasazeno 23. 9. 2026. NanoClaw: skupina `pardubicko` s repem jen pro čtení, tokenem `nanoclaw-pardubicko` (limit 20) přes OneCLI a denním během v 6:00 (pipeline, pak kurace); popis je v `OPERATIONS.md` repozitáře nanoclaw. Zbývá přepsat instrukce agentů v repu a úklid. |
| 3–5 | Nezačato. |

Oproti původnímu plánu se `POST /api/v1/runs` (report běhu, fetch a health)
přesunul do etapy 2. Do PHP se převedlo jen vyhodnocení zdraví
(`health.py evaluate`). Odvozování zrušených akcí (`mark_missing`,
`derive_cancellations`, ADR 0004) nepoužíval žádný běh a zatím se
nepřevádí; při úklidu `health.py` se musí buď převést, nebo vědomě opustit.

## Cílový stav

```
NanoClaw na PC                         Hestia
┌───────────────────────────┐          ┌────────────────────────────────────┐
│ pipeline (fetch, adaptéry)│─┐        │ PHP aplikace                       │
│ agenti (discovery, kurace)│─┼─HTTPS─▶│  /api/v1/*   token agenta         │
│ lokální cache (var/)      │ │        │  /admin/*    přihlášený správce    │
└───────────────────────────┘ │        │  /*          veřejný web, jen čtení│
          CLI klient ─────────┘        │        │                           │
                                       │        ▼                           │
                                       │  SQLite (private/, mimo docroot)   │
                                       │  cron: VACUUM INTO → záloha Hestie │
                                       └────────────────────────────────────┘
```

## Etapa 0: zabezpečit rozpracovaný stav

V pracovním stromu leží necommitnuté změny z 4. 8. 2026 (akce ve W32–W35,
nové příkazy `pipeline.py`, adaptér Pardubice.eu). Jsou to data, která se
budou převádět, takže se commitnou jako první.

- **Hotovo, když:** `git status` neobsahuje změny v `data/`, `research/` ani
  `tools/pipeline/` a úplná sada testů prochází.
- **Odhad:** hodina.

## Etapa 1: server, doménová vrstva a API

Dosavadní režim platí až do konce této etapy.

1. **Schéma:** tabulky `api_token` (název, hash, oprávnění, odvolání),
   `change_log` (čas, aktér, `run_id`, entita, akce, stav předtím a potom,
   poznámka). Migrace se pouštějí při startu jako ve splitt
   (`PRAGMA user_version`). Session správce přijde s admin UI v etapě 3.
2. **Doménová vrstva v PHP:** publikace, úprava, zrušení a přidání zdroje k
   akci; vyřízení kandidáta; úprava zdroje. Každá operace validuje, zapíše
   `change_log` a proběhne v jedné transakci.
3. **Deduplikace v PHP:** převod `tools/pipeline/matching.py` včetně pásem a
   `match_review`. Testy nad stejným korpusem jako
   `docs/deduplication-calibration.md` musí dát stejná rozhodnutí.
4. **API v1:**

   | Metoda a cesta | Účel |
   |---|---|
   | `GET /api/v1/events?week=&municipality=&q=&since=` | výpis a kontrola duplicit před zápisem |
   | `GET /api/v1/events/{id}`, `…/history` | detail a historie |
   | `POST /api/v1/events` | publikace: `201`, jistá duplicita `409` s ID, nejistá `202` do fronty, chyba `422` |
   | `PATCH /api/v1/events/{id}`, `POST …/cancel`, `POST …/sources` | úpravy, zrušení, další zdroj |
   | `GET/POST /api/v1/candidates`, `POST …/{id}/resolve` | fronta kandidátů |
   | `GET /api/v1/sources?due=1`, `PATCH …/{id}` | zdroje ke kontrole |
   | `GET /api/v1/changes?run_id=` | historie změn, také celého běhu |
   | `GET /api/v1/taxonomy` | kategorie, obce, aliasy |
   | `POST /api/v1/inbox` | stávající inbox, nová cesta |

   Chybové odpovědi vracejí pole a důvod v češtině, aby agent věděl, co opravit.
5. **Pojistky publikace:** povinné pole a konkrétní URL zdroje (ne shodná s
   homepage zdroje z registru), denní limit publikací na token, `run_id`
   povinný pro zápis agentem.
6. **Jednorázový převod:** dnešní `import_repo.py` sestaví databázi z
   repozitáře lokálně, výsledek se zkontroluje (počty akcí, kandidátů, zdrojů)
   a jednou nahraje na server. Provozní kandidáti z lokální `var/pardubicko.db`
   se převedou také.
7. **Nasazení na Hestii** jedním `bin/deploy` jako u splitt: testy, staging,
   migrace nanečisto na kopii živé databáze, prohození adresářů, kontrola.
   Prohozením se mění čas souborů, takže reload PHP-FPM není potřeba.
   Veřejný web tím běží na PHP.
8. **Záloha:** cron s `VACUUM INTO` do `private/zalohy/` před časem zálohy
   Hestie, retence 14 dní. Ověřit cíl zálohy Hestie a jednou vyzkoušet obnovu.

- **Hotovo, když:** agent přes API publikuje akci, zapíše se do historie a je
  vidět na webu; jistá duplicita vrátí `409`; obnova ze snímku projde; veřejný
  web na Hestii ukazuje stejné akce jako dnešní statický.
- **Odhad:** 3 dny.

## Etapa 2: pipeline a agenti jako klienti

1. **CLI klient** `tools/client/` jen na standardní knihovně Pythonu. Příkazy
   odpovídají API (`events publish`, `events find`, `candidates list`,
   `sources due`, `runs report`) a vypisují JSON.
2. **Pipeline:** `run.py` bere splatné zdroje z API, kandidáty a report běhu
   posílá do API; přibude `POST /api/v1/runs` pro report běhu, výsledky fetch
   a health. Lokální SQLite zůstává jen jako cache (ETagy, snapshoty).
3. **NanoClaw:** skupina s tokenem v konfiguraci skupiny, ne v repozitáři, a
   plán spouštění. Facebookový kanál potřebuje `playwright`; instaluje se do
   vlastního mountu, ne do sdíleného `.venv`, jinak rozbije prostředí hostu.
4. **Instrukce agentů:** přepsat `AGENTS.md`, `docs/agents/*.md` a skill
   `collect-events-week` na CLI klienta místo úprav JSON.
5. **Úklid:** odstranit `data/`, `research/`, `stats/`, `config/*.json`
   (kromě seed dat číselníku obcí), export, roundtrip, statický frontend,
   `tools/validate/`, GitHub workflows, které je kontrolují, a docker stack
   fáze 2 (`docker-compose.production.yml`, `deploy/`, `tools/ops/`).

- **Hotovo, když:** celý běh z NanoClaw proběhne bez zápisu do repozitáře
  (`git status` čistý) a výsledek je vidět v API.
- **Odhad:** 2 dny.

## Etapa 3: admin UI, sledování

Serverem renderované stránky jako veřejný web.

- Přihlášení správce (heslo s hashem v konfiguraci, session, CSRF).
- Přehled: poslední běh pipeline a upozornění, když chybí déle než 36 hodin;
  počty ve frontách; publikace za posledních 24 hodin.
- Zdroje a jejich health, detail běhu (co kterým zdrojem prošlo, karanténa,
  chyby).

- **Hotovo, když:** z přehledu je bez otevření databáze poznat, že sběr stojí
  nebo zdroj přestal vracet akce.
- **Odhad:** 1–2 dny.

## Etapa 4: admin UI, kontrola publikací

- Seznam publikací agentů od poslední návštěvy správce.
- Detail akce s historií; ruční úprava a zrušení.
- Vrácení jedné změny a vrácení celého běhu podle `run_id`.
- Fronta nejistých duplicit (`match_review`), kandidátů a inboxu.

- **Hotovo, když:** chybný běh agenta jde vrátit jedním krokem a vrácení se
  samo objeví v historii.
- **Odhad:** 2–3 dny.

## Etapa 5: admin UI, konfigurace

- Zdroje (přidání, vypnutí, interval, adaptér), facebookové stránky,
  kategorie a aliasy, aliasy obcí.
- Aktualizace číselníku obcí z ČSÚ/RÚIAN jako akce v administraci.

Do té doby konfiguraci mění agenti přes API.

- **Odhad:** 2 dny.

## Otevřené otázky

1. Veřejná doména webu.
2. Vzdálený cíl zálohy. Ověřeno 23. 9. 2026: Hestia zálohuje jen lokálně
   (`BACKUP_SYSTEM='local'`), takže databáze zatím nemá kopii mimo server.
3. Výše denního limitu publikací na token.
4. Přihlášení do admin UI: vlastní heslo v aplikaci, nebo HTTP autentizace
   Hestie před `/admin`.
5. ~~Dostupnost FTS5 v PHP na Hestii.~~ Ověřeno 23. 9. 2026: funguje.
