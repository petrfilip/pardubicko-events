# Pipeline fáze 2

Provozní databáze a nástroje kolem ní. Rozhodnutí popisují ADR 0002 až 0005,
návrh `docs/phase-2-architecture.md`.

## Zásada, kterou nelze obejít

Databáze je **odvozený artefakt**, ne zdroj pravdy. Smí být kdykoli smazána
a znovu postavena importem z repozitáře. Zdrojem pravdy zůstává git:
konfiguraci vlastní člověk, publikovaná data existují jako export v `data/`.

Soubor databáze do gitu nepatří (`var/` je v `.gitignore`).

## Spuštění

```bash
python3 tools/pipeline/pipeline.py import      # repozitář -> databáze
python3 tools/pipeline/pipeline.py export      # databáze -> repozitář
python3 tools/pipeline/pipeline.py roundtrip   # ověření bezeztrátovosti
python3 tools/pipeline/pipeline.py stats       # obsah databáze
```

Nevyžaduje žádnou závislost mimo standardní knihovnu.

## Dávkový end-to-end běh

`run.py` propojuje registr, snapshot, adaptér, normalizaci, matching a
kandidátní frontu. Bez `--source` projde všechny `enabled` zdroje v pořadí
podle ID; zdroje bez implementovaného adaptéru bezpečně označí jako
`skipped`. `--due` výběr zúží na zdroje, od jejichž posledního skutečného
fetch pokusu uplynul `check_interval_days` (zdroj bez historie je splatný).

```bash
python3 tools/pipeline/pipeline.py import
python3 tools/pipeline/run.py --due
python3 tools/pipeline/run.py --source pardubice-calendar
python3 tools/pipeline/run.py --source pardubice-calendar --offline
python3 tools/pipeline/run.py --source pardubice-calendar --offline --dry-run
```

- `--source ID` lze opakovat; disabled zdroj nelze obejít ručním výběrem.
- `--offline` nikdy neotevře síť a vyžaduje poslední snapshot pro každý
  požadavek fetch plánu adaptéru.
- `--dry-run` vrátí databázovou transakci a nevytvoří report na disku. Při
  online dry-run se i snapshoty ukládají jen do dočasného adresáře.
- `--snapshot-dir` a `--report-root` slouží izolovanému testu nebo jinému
  provoznímu umístění; výchozí jsou `var/snapshots` a `stats/runs`.

Každá položka kandidáta drží doslovný výstup adaptéru v `payload.raw`,
normalizovaný tvar v `payload.normalized`, obsahové hashe vstupních snapshotů
a případné stabilní kódy `quarantine_reasons`. ID je SHA-256 fingerprint
identity zdroje a položky, nikoli pořadové číslo běhu. Opakování nad stejným
obsahem proto kandidáty pouze aktualizuje a nevytváří kopie.

Normalizace bezpečně sjednotí ISO nebo úplný český termín, jednoznačnou cenu
v Kč, URL podle stejného pravidla jako inbox a obec podle číselníku či
doloženého aliasu. Chybějící nebo rozporný termín, konkrétní URL, název, obec
či cena vytvoří kandidáta ve stavu `quarantined`; raw údaj se nikdy neztratí.
Obec lze převzít z registry jen u lokálního zdroje, kde je explicitně
uvedená. Nadregionální zdroj bez obce zůstává v karanténě.

### Transakce a publikační hranice

Fetch pozorování se commitne samostatně, aby ADR 0004 neztratilo ani
neúspěšný HTTP pokus. Extrakce, kandidáti a matching jednoho zdroje jsou pak
jedna atomická transakce. Selhání této transakce vrátí jen daný zdroj;
výsledky předchozích zdrojů zůstanou zachované. Stav dávky i jednotlivých
zdrojů drží `pipeline_run` a `pipeline_source_run`.

Runner **nikdy nevkládá novou akci do `event`, nemění týdenní JSON a
nespouští export**. Jistá deduplikační shoda smí pouze doplnit vazbu
`event_source` k již publikované akci. Střední pásmo jde do `match_review` a
stav kandidáta `needs-verification`; samostatný nový kandidát zůstane `new`.
Publikaci po lidském ověření musí provést kurátorský krok, který není součástí
WP3. Tohle je záměrná bezpečnostní hranice, ne nedokončený implicitní publish.

Skutečný (ne dry-run) běh vytvoří právě jeden report
`stats/runs/YYYY-MM/*-pipeline.json` podle `docs/monitoring.md`. Druhý běh ve
stejné minutě dostane přesnější čas a existující historii nikdy nepřepíše.

## Fetch a snapshoty

Jediným síťovým vstupem pipeline je `fetch.py`. Používá user-agent projektu,
respektuje `robots.txt`, mezi dotazy na stejný host vkládá pauzu a posílá
`If-None-Match` i `If-Modified-Since`. Každý pokus registrovaného zdroje,
včetně HTTP chyby nebo zákazu v robots, zapíše do `source_fetch`.

Tělo odpovědi se ukládá podle SHA-256 do
`var/snapshots/<první-2-znaky>/<hash>.gz`. Stejný obsah tedy fyzicky vznikne
jen jednou. `prune_snapshots()` drží posledních pět různých obsahů každého
zdroje (počet lze změnit) a vždy poslední snapshot s dokončenou extrakcí.

```bash
python3 tools/pipeline/pipeline.py import
python3 tools/pipeline/fetch.py --source pardubice-calendar
python3 tools/pipeline/fetch.py --source pardubice-calendar --offline
```

Pro URL inbox je veřejné API:

```python
from fetch import fetch_url

result = fetch_url(connection, url, source_id=None)
# result.snapshot, result.snapshot_path, result.error
```

Bez `source_id` vznikne obsahový snapshot, ale ne řádek `source_fetch`, jehož
cizí klíč záměrně odkazuje jen na registrované zdroje. Pokus inboxu eviduje
tabulka `inbox`. Žádná extrakce nesahá na síť; dostává vždy `Snapshot`.

## První adaptéry

| Zdroj | Adaptér | Vstup |
|---|---|---|
| `kultura-hk-official` | `schema_org` | mikrodata / JSON-LD `schema.org/Event` |
| `uffo-trutnov` | `ical` | veřejný iCalendar feed |
| `pardubice-calendar` | `pardubice_calendar` | stabilní `article.event-Card` bez strukturovaných dat |

Každý adaptér vrací `found`, `accepted`, `rejected` a podmnožinu `unparsed`.
Platí `found = accepted + rejected`; přijatá položka musí mít název a
strojově čitelný začátek. Chybějící hodnoty zůstávají `null`. Golden vstupy
a očekávané výstupy jsou v `tools/pipeline/fixtures/`, testy v
`test_adapters.py`. Testy ani offline režim nemají přístup k živé síti.

Kategorie ze zdroje se ve všech třech adaptérech mapují přes
`config/categories.json`. Výstupní `categories` obsahuje jen kanonická ID.
Neznámá znění se neodhadují: položka je uchová v
`extra.categories_unmapped`, přidá vysvětlující poznámku a výsledek je
započítá v metrice `category_values_rejected`.

### Živý smoke adaptérů

Živá kontrola je oddělená od deterministického test runneru a spouští se
samostatně nad provozní databází:

```bash
python3 tools/pipeline/pipeline.py import
python3 tools/pipeline/live_smoke.py --source uffo-trutnov
python3 tools/pipeline/live_smoke.py
```

Příkaz nejdřív bez sítě ověří adaptér proti golden fixture. Její selhání
klasifikuje jako `code-regression` a živý zdroj vůbec nestahuje. Když fixture
projde, provede jeden fetch plán, porovná tvar výstupu s fixture a výtěžnost i
vyplnění polí s historií `source_health`. Tím lze doloženou změnu vstupu
klasifikovat jako `source-change`.

Reporty se bez přepisování historie ukládají do
`var/live-smoke/reports/`. Při změně zdroje obsahují poslední funkční
snapshot a výstup, aktuální výstup a omezený unified diff. Plné snapshoty
zůstávají v obsahově adresovaném `var/snapshots/`, takže kvůli opravě není
nutné zdroj znovu zatěžovat.

Smoke nikdy neupravuje adaptér a nikdy sám nevolá LLM. Pole
`llm_repair.allowed` je `true` pouze pro doložený stav `suspect`, `degraded`
nebo `schema-drift`, pokud existuje předchozí funkční snapshot a skutečný
diff. Deterministické testy orchestru používají fake HTTP klienta a nesahají
na síť.

### Výchozí měření výtěžnosti

Srovnávané období je ISO týden **2026-W32 (3.–9. 8. 2026)**. Počty adaptérů
byly odečteny 3. 8. 2026 z prvních tří stran Kultura HK, prvních šesti
kumulativních stran Pardubice.eu a úplného iCal feedu UFFO. Počítá se
`start_at`/`DTSTART` spadající do týdne, ne doplňkový neúplný termín z karty.

| Zdroj | Deterministický adaptér | LLM discovery běh 2. 8. 2026 |
|---|---:|---:|
| Kultura Hradec Králové | 44 | 1 |
| Pardubice.eu | 43 | 5 |
| UFFO Trutnov | 14 | 0 |
| **Celkem** | **101** | **6** |

LLM čísla vycházejí z `research/candidates-2026-08-02-1712-discovery.json`.
Běh byl částečný a UFFO vůbec neprocházel, takže tabulka není kalibrace
přesnosti. Je to měření pokrytí rutinního sběru: deterministické kanály
vydaly pro stejný týden 101 strojově čitelných termínů, zatímco ohraničený
discovery běh uložil šest kandidátů z těchto tří zdrojů. Fixtures drží tvar,
nikoli celý tehdejší obsah cizích webů.

## Kolotoč

`roundtrip` je nejdůležitější příkaz celé fáze 2. Naimportuje repozitář do
databáze v paměti, vyexportuje ho do dočasného adresáře a porovná bajt po
bajtu se skutečnými soubory. Do repozitáře přitom nic nezapisuje.

Je součástí ručně spouštěného validačního workflow. Dokud prochází, je
jisté, že se databáze a publikovaná data nerozešla.

Proto export zachovává i formátování, ne jen obsah — jinak by se každý běh
projevil jako přepsání všech souborů a diff by přestal být čitelný. Styl
řeší `jsonfmt.py`.

## Import je záměrně přísný

Import nic nedomýšlí a nic neopravuje. Když narazí na rozpor, **spadne**
a napíše, kde je.

Konkrétně hlídá pravidlo z ADR 0001, že kopie jedné akce v různých týdnech
se liší pouze hodnotou `week`. Kontroluje ho na **všech** polích, tedy
přísněji než výčet identitních polí v ADR. Důvod je praktický: `js/data.js`
slučuje kopie podle úzké identity, takže rozdíl v popisu by se neprojevil
jako chyba, ale jako nedeterministicky vybraný popis. Tahle vada se v datech
skutečně vyskytla a odhalil ji až import.

## Soubory

| Soubor | Role |
|---|---|
| `schema.sql` | Schéma databáze. Kontrakt pro všechny ostatní nástroje. |
| `db.py` | Otevření spojení, aplikace schématu, `repo_meta`. |
| `jsonfmt.py` | Serializace JSON ve stylu repozitáře. |
| `import_repo.py` | Repozitář → databáze. |
| `export_repo.py` | Databáze → repozitář. |
| `pipeline.py` | Rozhraní příkazové řádky. |

Import zrcadlí také `config/municipalities.json` do tabulky `municipality`
a doložené mapování z `config/municipality-aliases.json` do tabulky
`municipality_alias`; `config/categories.json` plní tabulky `category` a
`category_alias`.
Jednoznačné doslovné názvy obcí se propojí s `event.municipality_id`;
shodné názvy více obcí ani názvy částí obcí se bez dalšího dokladu
neodhadují. Kategorie se porovnávají normalizovaně stejně jako ve validátoru.
Import sloučí aliasy do kanonických ID a ukládá je shodně do
`event_category.name` i `event_category.category_id`; export proto obsahuje
jen kanonická ID. Fulltext pro každou akci navíc indexuje popisek a všechny
aliasy jejích kategorií.

Geografický coverage report vznikne příkazem:

```bash
python3 tools/pipeline/coverage.py
```

Report v `stats/coverage.json` rozlišuje přímou vazbu na obec, doložené části
obcí a nevyřešené hodnoty. Číselník částí obcí CISMC se zatím plošně
neimportuje: jediný doložený provozní případ (`Janderov -> Chrudim`) pokrývá
auditovatelný alias a nejednoznačné hodnoty se nesmějí odhadnout.

## Odchylky od návrhu v architektuře

Tři, všechny se stejným důvodem: uchovat data doslovně, aby byl export
prokazatelně bezeztrátový dřív, než vznikne normalizační vrstva.

- **`event.municipality_name`** drží název obce doslovně. Vazba
  `municipality_id` se při importu doplní jen u jednoznačné shody s číselníkem.
- **`event_category.name`** zůstává kvůli kompatibilitě schématu, ale po
  P2-3 obsahuje stejné kanonické ID jako `category_id`.
- **`event_week`** ukládá členství akce v týdnech explicitně, místo aby ho
  odvozovalo z překryvu termínu s rozsahem týdne. Odvozené členství by
  změnilo publikovaná data — v repozitáři je akce `sportovni-park-pardubice-2026`
  (1.–9. 8.) zapsaná jen ve W32, ne ve W31, kde začíná. Přechod na odvozené
  členství je otevřená otázka 4 v `docs/phase-2-architecture.md`.

## Fulltext

`event_fts` používá tokenizer `unicode61 remove_diacritics 2`. Bez něj dotaz
„ridic“ nenajde „řidič“. Ověřeno v `python:3.12-slim` i v `php:8.3-cli`,
takže na tom může stavět i webová vrstva.
