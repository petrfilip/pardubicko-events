# Architektura fáze 2

Tento dokument rozpracovává rozhodnutí z ADR 0002 až 0007 do konkrétní
podoby. Popisuje cílovou architekturu a u jednotlivých vrstev rozlišuje
skutečný stav; podrobná kritéria a historii implementace drží
`docs/phase-2-work-packages.md`.

## Stav k 3. 8. 2026

| Oblast | Stav |
|---|---|
| SQLite import/export a migrace | Hotovo; sedm publikovaných JSON souborů prochází bajtově shodným roundtripem. |
| Obce a kategorie | Hotovo pro číselník 899 obcí, doložený alias `Janderov → Chrudim`, coverage report a slovník 18 kategorií / 124 aliasů. Plošný CISMC se konzervativně neimportoval. |
| Fetch, snapshoty a první tři adaptéry | Hotovo s offline golden fixtures. |
| Inbox a health | Hotovo v CLI/pipeline i v PHP endpointu; health historie žije v SQLite. |
| PHP serving vrstva | Funkčně hotovo a testováno; není produkčně nasazená. |
| Dávkový tok fetch → normalize → match → candidate/review | Hotovo v `tools/pipeline/run.py`; nové akce zůstávají před publikací v kurátorské frontě. |
| Deduplikace P2-2 | Implementováno včetně `match_review`; prahy jsou provizorně kalibrované na malém korpusu. |
| Živý smoke P2-4 | Hotovo jako oddělený ruční CLI s golden preflightem, health baseline a diffem; adaptéry ani LLM automaticky nemění. |
| Produkční provoz | PHP-FPM + Nginx, TLS konfigurace, persistentní volumes a backup/restore nástroje jsou implementované; deploy na cílovém hostu a veřejné přepnutí nejsou doložené. Vývojové spuštění dál používá `php -S`. |

Následující části jsou cílovým kontraktem. Tvrzení o hotovém chování se musí
opírat o tabulku výše, stav balíčku nebo existující kód a test.

## 1. Vztah k fázi 1

Fáze 2 nesmí rozbít fázi 1. Platí dvě pravidla:

- **Export je regresní test.** Pipeline umí z databáze vygenerovat `data/manifest.json` a `data/weeks/YYYY-Www.json` v nezměněném formátu podle ADR 0001. Dokud se stávající frontend nad exportem chová stejně jako dnes, je migrace bezpečná.
- **Git zůstává auditní stopou.** Export se commituje. Historii změn dat je dál vidět v diffu, což je jedna z nejsilnějších vlastností projektu a neopouští se.

Statický frontend zůstává nad JSON exportem; na API se nepřepíná. PHP web je
samostatný čtenář SQLite a po splnění produkčních podmínek převezme veřejnou
kanonickou URL. Rozdělení odpovědností a regresní kompatibilitu stanoví
ADR 0007.

## 2. Celkový tok

```
zdroje ──┐
         ├─→ fetch ─→ snapshot ─→ extract ─→ normalize ─→ match ─→ publish ─┬─→ SQLite ─→ PHP SSR
inbox  ──┘                            │                       │            └─→ export JSON ─→ git ─→ statický web
                                      ↓                       ↓
                                 health tracking        fronta k rozhodnutí
```

Každá fáze ukládá svůj mezivýstup. To je záměrné: bez uchovaného mezistavu se adaptér ladí naslepo a každá oprava znamená znovu zatěžovat cizí web.

## 3. Vrstvy

### 3.1 Fetch

Jediné místo, které sahá na síť. Sdílené pro adaptéry i pro inbox.

Povinné chování:

- vlastní user-agent `PardubickoEventsBot/0.1 (+https://github.com/petrfilip/pardubicko-events)`, shodný s dnešním nástrojem v `tools/fb-events/`,
- respektování `robots.txt`,
- podmíněné dotazy přes `ETag` a `If-Modified-Since`,
- sekvenční stahování s pauzou mezi dotazy na tentýž host,
- žádné přihlašování a žádné obcházení ochran; tato hranice z `docs/project-vision.md` platí beze změny,
- každý pokus se zaznamená, včetně neúspěšného.

### 3.2 Snapshot

Tělo odpovědi se ukládá adresovaně obsahem: klíčem je hash, takže nezměněný obsah nezabírá místo opakovaně.

- Cesta: `var/snapshots/<hash[0:2]>/<hash>.gz`. Mimo git.
- Retence: posledních N snapshotů na zdroj plus vždy poslední snapshot, ze kterého extrakce uspěla. Ten je referencí pro diff při opravě adaptéru.

### 3.3 Extract

Adaptér převede snapshot na seznam syrových položek. Pořadí preferencí podle ADR 0003: JSON-LD `schema.org/Event`, iCal nebo RSS, API redakčního systému, HTML struktura, text.

Adaptér **nevyhodnocuje a nedomýšlí**. Co nepřečte, vrací jako `null`. Nenaparsované bloky počítá zvlášť — to je metrika tiché ztráty, jejíž potřebu doložil facebookový kanál.

Rozhraní adaptéru je jednotné:

```
fetch_plan(source) -> list[Request]
extract(snapshot)  -> ExtractResult(items, items_unparsed, notes)
```

### 3.4 Normalize

Deterministicky, s LLM až jako fallback.

| Pole | Postup |
|---|---|
| Termín | Vlastní parser českých zápisů (`So 15. 8. v 18:00`, `15.–17. srpna`, `od 18 h`, `Právě probíhá`) nad testovacím korpusem. Neparsovatelné → karanténa. |
| Obec | Fuzzy shoda proti číselníku ČSÚ/RÚIAN plus ruční tabulka aliasů. Geoznačka zdroje se nepřebírá jako pravda; facebookový kanál doložil chybné hodnoty. |
| Kategorie | Implementovaný řízený slovník se dvěma osami `kind` a `audience`; známé vstupy se mapují přes 124 aliasů, neznámé se odmítnou nebo dají do karantény. Historické volné texty už byly jednorázově zmigrovány. |
| Cena | Regex → `{type, amount, currency, text}`. Nedoložená cena zůstává `unknown`; nikdy se neodvozuje z podobné akce ani z loňska. |
| URL | Normalizace, odstranění sledovacích parametrů. |

Pravidlo, které fáze 1 už má a fáze 2 ho vynucuje kódem: **co nelze bezpečně naparsovat, jde do karantény, nikdy se nedohaduje.**

### 3.5 Match

Deduplikace napříč zdroji. Postup:

1. **Blokovací klíč** — `(date(start_at), municipality_id)`. Porovnávají se jen položky ve stejném bloku.
2. **Podobnost** — normalizovaný název bez diakritiky (Jaro-Winkler) v kombinaci se shodou místa a času.
3. **Rozhodnutí** podle skóre:
   - `>= 0.92` automatické sloučení,
   - `0.75 – 0.92` fronta k rozhodnutí (zde LLM),
   - `< 0.75` samostatné akce.

Prahy jsou konzervativně kalibrované proti malému korpusu v
`docs/deduplication-calibration.md`. Ten zatím vykázal nula falešných
sloučení, ale není odhadem produkční chybovosti; další kalibrace musí používat
skutečně rozhodnuté řádky `match_review`.

Sloučení nemaže zdroje. Vazba se ukládá do `event_source`, takže je vždy vidět, které zdroje akci hlásily — to je zároveň podklad pro odvození zrušení podle ADR 0004.

### 3.6 Publish

Do publikovaných dat se dostane jen akce ve stavu `published`, tedy ověřená Curatorem. Publikace generuje:

- záznamy v `event` a index FTS5,
- export `data/manifest.json` a `data/weeks/*.json`.

PHP nad publikovanými záznamy dynamicky poskytuje sitemapu a detailní
stránky; nejde o předrenderovaný build.

## 4. Datový model

Návrh schématu. Sloupce jsou minimum, ne úplný výčet.

```sql
-- ---------- konfigurace (zrcadlo gitu, mění ji člověk) ----------

CREATE TABLE source (
  id                  TEXT PRIMARY KEY,     -- shodné s config/source-registry.json
  name                TEXT NOT NULL,
  url                 TEXT NOT NULL,
  type                TEXT NOT NULL,
  adapter             TEXT,                 -- NULL = zatím bez adaptéru
  municipality_id     INTEGER REFERENCES municipality(id),
  district            TEXT,
  region              TEXT NOT NULL,
  priority            TEXT NOT NULL,
  check_interval_days INTEGER NOT NULL,
  enabled             INTEGER NOT NULL DEFAULT 1,
  notes               TEXT
);

CREATE TABLE municipality (        -- z číselníku ČSÚ/RÚIAN, ne ručně psaný
  id        INTEGER PRIMARY KEY,   -- kód obce
  name      TEXT NOT NULL,
  district  TEXT NOT NULL,
  region    TEXT NOT NULL
);

CREATE TABLE municipality_alias (  -- ruční mapování textů z webů na obec
  alias           TEXT PRIMARY KEY,
  municipality_id INTEGER NOT NULL REFERENCES municipality(id)
);

CREATE TABLE category (            -- řízený slovník
  id          TEXT PRIMARY KEY,
  axis        TEXT NOT NULL,       -- kind|audience
  sort_order  INTEGER,
  label       TEXT NOT NULL,
  description TEXT
);

CREATE TABLE category_alias (
  alias       TEXT PRIMARY KEY,
  category_id TEXT NOT NULL REFERENCES category(id)
);

-- ---------- provozní stav (píše pipeline, do gitu nepatří) ----------

CREATE TABLE source_fetch (
  id            INTEGER PRIMARY KEY,
  source_id     TEXT NOT NULL REFERENCES source(id),
  fetched_at    TEXT NOT NULL,
  http_status   INTEGER,
  etag          TEXT,
  content_hash  TEXT,
  bytes         INTEGER,
  duration_ms   INTEGER,
  error         TEXT
);

CREATE TABLE source_extract (
  fetch_id        INTEGER PRIMARY KEY REFERENCES source_fetch(id),
  items_found     INTEGER NOT NULL,
  items_valid     INTEGER NOT NULL,
  items_unparsed  INTEGER NOT NULL,
  fill_rates      TEXT NOT NULL           -- JSON: podíl vyplnění klíčových polí
);

CREATE TABLE source_health (
  source_id             TEXT PRIMARY KEY REFERENCES source(id),
  state                 TEXT NOT NULL,    -- healthy|suspect|degraded|schema-drift|stale|broken
  consecutive_failures  INTEGER NOT NULL DEFAULT 0,
  last_checked_at       TEXT,
  last_success_at       TEXT,
  last_item_at          TEXT,             -- kdy zdroj naposledy vydal položku
  baseline_items_median REAL,             -- medián posledních 10 nenulových běhů
  baseline_fill_rates   TEXT,
  first_alerted_at      TEXT,
  note                  TEXT
);

-- ---------- data ----------

CREATE TABLE candidate (
  id               TEXT PRIMARY KEY,
  source_id        TEXT REFERENCES source(id),
  inbox_id         INTEGER REFERENCES inbox(id),
  discovery_method TEXT NOT NULL,        -- adapter|manual-submission|search|facebook
  raw              TEXT NOT NULL,        -- JSON, výstup extrakce
  normalized       TEXT,                 -- JSON, výstup normalizace
  state            TEXT NOT NULL,        -- new|needs-verification|imported|rejected|quarantined
  event_id         TEXT REFERENCES event(id),
  created_at       TEXT NOT NULL,
  reviewed_at      TEXT,
  notes            TEXT
);

CREATE TABLE event (
  id               TEXT PRIMARY KEY,     -- stabilní slug, shodný s exportem
  title            TEXT NOT NULL,
  description      TEXT,
  start_at         TEXT NOT NULL,
  end_at           TEXT,
  all_day          INTEGER NOT NULL DEFAULT 0,
  venue            TEXT,
  municipality_id  INTEGER REFERENCES municipality(id),
  price_type       TEXT NOT NULL DEFAULT 'unknown',
  price_amount     INTEGER,
  price_text       TEXT,
  canonical_url    TEXT NOT NULL,
  cancelled        INTEGER NOT NULL DEFAULT 0,
  status           TEXT NOT NULL,        -- draft|published|quarantined
  first_seen_at    TEXT NOT NULL,
  last_seen_at     TEXT NOT NULL,
  last_verified_at TEXT,
  match_title_norm TEXT NOT NULL         -- pro blokování při deduplikaci
);

CREATE TABLE event_category (
  event_id    TEXT NOT NULL REFERENCES event(id),
  category_id TEXT NOT NULL REFERENCES category(id),
  PRIMARY KEY (event_id, category_id)
);

CREATE TABLE event_source (           -- které zdroje akci hlásily
  event_id     TEXT NOT NULL REFERENCES event(id),
  source_id    TEXT NOT NULL REFERENCES source(id),
  url          TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  missing_runs INTEGER NOT NULL DEFAULT 0,   -- kolikrát po sobě chyběla ve zdravém stažení
  PRIMARY KEY (event_id, source_id)
);

CREATE TABLE inbox (
  id            INTEGER PRIMARY KEY,
  url           TEXT NOT NULL,
  url_norm      TEXT NOT NULL UNIQUE,
  submitted_at  TEXT NOT NULL,
  submitted_via TEXT NOT NULL,        -- cli|api
  note          TEXT,
  state         TEXT NOT NULL,        -- new|fetched|candidate|source-proposal|failed
  resolved_kind TEXT,                 -- event-detail|listing|unknown
  candidate_id  TEXT REFERENCES candidate(id),
  attempts      INTEGER NOT NULL DEFAULT 0,
  last_attempt_at TEXT,
  error         TEXT
);

CREATE VIRTUAL TABLE event_fts USING fts5(
  title, description, venue, municipality,
  content='event', content_rowid='rowid',
  tokenize="unicode61 remove_diacritics 2"
);
```

Poznámka k `remove_diacritics 2`: bez toho dotaz „ridic“ nenajde „řidič“.
PHP FTS i statický `searchableText()` už bezdiakritické hledání pokrývají.

### Konfigurace proti stavu

Hranice je závazná:

- **Konfiguraci vlastní git** a mění ji člověk. Do databáze se jen zrcadlí.
- **Stav vlastní databáze** a píše ho pipeline. Do konfiguračních souborů se nikdy nezapisuje.

Původní `config/facebook-sources.json` toto pravidlo porušoval poli
`verified_at` a `upcoming_events_at_check`. Migrace 36 historických měření do
`source_health` je hotová a schéma konfigurace provozní pole už nepřipouští.

## 5. Zdraví zdrojů

Vyhodnocuje se po každém běhu adaptéru. Prahy jsou výchozí odhad ke kalibraci.

| Stav | Podmínka |
|---|---|
| `broken` | `consecutive_failures >= 3` |
| `suspect` | `items_found = 0` a `baseline_items_median >= 3` |
| `degraded` | `items_found < 0.4 × baseline_items_median` |
| `schema-drift` | fill rate klíčového pole `< 0.8 × baseline` |
| `stale` | `content_hash` beze změny `> 30` dní **a** žádný budoucí termín |
| `healthy` | žádná z výše uvedených podmínek |

Výjimka pro nízký baseline: zdroje s `baseline_items_median < 3` se podle objemu nehodnotí. Sleduje se u nich pouze úspěšnost stažení a dodržení intervalu kontroly. Prázdný výsledek u malé obce je normální stav, ne poplach.

Klíčová pole pro `schema-drift`: `start_at`, `title`, `canonical_url`.

### Odvození zrušení

Akce se označí jako zrušená pouze při současném splnění všech podmínek:

1. zdroj je `healthy`,
2. termín akce spadal do staženého okna,
3. `event_source.missing_runs >= 2`.

Jinak se ponechá beze změny. Zmizení z výpisu není důkaz.

### Report

Přehled `stav zdroje × dnů od poslední položky` je výstupem, který řídí práci. Zdroje v `suspect`, `degraded` a `schema-drift` jsou fronta k opravě adaptéru.

## 6. Inbox

### Kontrakt CLI

```bash
python3 tools/ingest/submit.py <url> [--note "text"]
```

Zapisuje přímo do databáze. Vrací identifikátor záznamu. Nestahuje — zpracování je věcí pipeline.

### Kontrakt HTTP

```
POST /api/inbox
Authorization: Bearer <token>
Content-Type: application/json

{"url": "https://…", "note": "volitelné"}

→ 202 {"id": 41, "state": "new"}
→ 200 {"id": 41, "state": "new", "duplicate": true}
→ 401 při chybějícím nebo neplatném tokenu
```

### Zpracování

1. Vybrat záznamy ve stavu `new`.
2. Stáhnout společnou fetch vrstvou, uložit snapshot.
3. Klasifikovat:
   - **detail akce** — přítomnost `schema.org/Event`, nebo právě jeden termín a jeden název → `resolved_kind = 'event-detail'`, vzniká kandidát s `discovery_method = 'manual-submission'`,
   - **výpis** — více bloků `datum + název`, stránkování, kalendářová mřížka → `resolved_kind = 'listing'`, `state = 'source-proposal'`,
   - **jinak** — `resolved_kind = 'unknown'`, `state = 'failed'` s důvodem.
4. Kandidát pokračuje běžným tokem k ověření Curatorem.

Neúspěšné zpracování se opakuje nejvýše třikrát, pak zůstává ve `failed` s popsaným důvodem. Záznam se nikdy nemaže automaticky.

### Návrh zdroje

`source-proposal` se nezapisuje do `config/source-registry.json` automaticky. Je to podklad k posouzení; zápis do registru je změna konfigurace, tedy commit provedený člověkem nebo agentem s výslovným zadáním.

## 7. Serving vrstva

Tato vrstva je implementovaná v `web/` a pokrytá deterministickými PHP testy
i HTTP smoke testem. Podle ADR 0002 renderuje stránky server; API není
primární rozhraní. Produkční nasazení této hotové aplikace je samostatná,
dosud nesplněná provozní práce.

### Stránky

```
GET /                          přehled, výchozí nejbližší týden
GET /akce/{id}                 detail akce
GET /obec/{slug}               akce v obci
GET /kalendar/{YYYY-Www}       kalendářový pohled na týden
GET /hledat?q=&obec=&kategorie=&cena=&od=&do=&budouci=1
GET /sitemap.xml
GET /robots.txt
```

Filtry jsou obyčejný formulář odesílaný metodou GET. Stav aplikace je celý v URL, takže je sdílitelný, uložitelný do záložek a indexovatelný. Server vrátí hotové HTML sestavené z jednoho SQL dotazu.

Každá stránka musí být čitelná a použitelná bez JavaScriptu. To je kritérium přijetí, ne doporučení.

### JavaScript jako doplněk

JavaScript smí:

- odeslat formulář filtru bez překreslení celé stránky a nahradit jen výsledkovou část,
- ovládat rozbalení pokročilých filtrů,
- zlepšit chování kalendáře na dotykových zařízeních.

JavaScript nesmí být jediná cesta k obsahu, nesmí duplikovat filtrovací logiku ze SQL a nesmí být podmínkou zobrazení akce.

### SEO

Původní statická SPA nemá indexovatelné detailní cesty ani sitemapu. PHP web
už implementuje detailní stránky, sitemapu a strukturovaná data; tato výhoda
se projeví veřejně až po produkčním přepnutí.

Detail akce nese `<title>`, popis, odkaz na zdroj a `schema.org/Event` v JSON-LD. Sitemapa se generuje z publikovaných akcí.

Odkaz `?event={id}` z fáze 1 se zachová a přesměruje na `/akce/{id}`.

### Zbytkové API

HTTP rozhraní zůstává jen tam, kde nejde o zobrazení stránky:

```
POST /api/inbox     vložení odkazu, chráněno tokenem (ADR 0005)
GET  /api/health    přehled stavu zdrojů pro provoz a monitoring
```

Pokud později vznikne potřeba veřejného datového rozhraní, řeší se samostatným rozhodnutím. Exportované JSON soubory v `data/` tuto roli zatím plní.

### Veřejná plocha a kompatibilní reference

Podle ADR 0007 zůstává do produkčního přepnutí veřejným frontendem statický
GitHub Pages web. PHP web je kanonická cílová plocha. Po přepnutí zůstane
statický web jako regresní čtenář auditního exportu; neudržuje vlastní datový
význam a nesmí se bez dalšího rozhodnutí smazat.

## 8. Nasazení a provoz

Produkční konfigurace je implementovaná v `docker-compose.production.yml`,
`deploy/` a `tools/ops/`; používá PHP-FPM, Nginx, TLS, persistentní volumes a
otestované `VACUUM INTO` zálohy s obnovou. `docker-compose.yml` slouží vývoji
a používá vestavěný PHP server. Deploy na cílovém hostu, produkční HTTP smoke
a přepnutí veřejné URL zatím nejsou doložené; postup je v
`docs/production-runbook.md`.

- Jeden stroj: PHP, SQLite a Python pipeline.
- SQLite v režimu WAL, `busy_timeout` nastavený na obou stranách.
- Hostitelský cron spouští hotový dávkový pipeline runner; do ověřeného
  produkčního zapojení se plánování podle runbooku nezapíná.
- Záloha používá denní `VACUUM INTO`, checksum a retenci; nástroje i restore
  test jsou implementované. Commitnutý export v gitu zůstává nezávislou
  recovery reprezentací dat.
- Pokud by pipeline někdy běžela mimo webový stroj, přenáší se **celý soubor databáze** atomicky (zápis vedle a `mv`). Po síti se do SQLite nikdy nepíše.

## 9. Role LLM po fázi 2

| Úloha | Kdo |
|---|---|
| Rutinní sběr ze známého zdroje | adaptér |
| Parsování běžných termínů a cen | deterministický kód |
| Neparsovatelný termín, nejednoznačný text | LLM jako fallback |
| Duplicita ve středním pásmu podobnosti | LLM |
| Ověření akce před publikací | Curator Agent |
| Oprava rozbitého adaptéru | LLM nad diffem snapshotů |
| Hledání nových zdrojů | Discovery Agent |
| Rutinní hledání akcí ve známém zdroji | **nikdo — to dělá adaptér** |

## 10. Otevřené otázky

1. Kalibrace prahů deduplikace a zdraví na skutečných datech. Současné hodnoty jsou odhad.
2. Retenční politika snapshotů proti velikosti disku.
3. Zda `research/candidates*.json` po migraci zaniknou, nebo zůstanou jako exportovaný pohled na tabulku `candidate`.
4. ~~Jak řešit vícedenní a opakované akce v modelu, kde už týdenní soubor není datovým modelem, ale jen exportem.~~ Zodpovězeno v ADR 0006: členství v týdnech se odvozuje z termínu. Zavedení je odložené a má vlastní postup.
5. Zda `stats/runs/*.json` nahradit tabulkou `run` a soubory generovat, nebo ponechat jako primární zápis.
