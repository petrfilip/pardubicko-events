# Obnova rozpracovaného working tree

Tento checklist zachycuje stav rozpracované fáze 2 po auditu working tree. Položka se smí odškrtnout až po splnění uvedeného ověření. Změny se v rámci obnovy necommitují ani nepushují.

Stavy: `TODO` = nezačato, `WIP` = rozpracováno, `DONE` = ověřeno, `BLOCKED` = bezpečně nelze dokončit bez dalšího rozhodnutí nebo závislosti.

## Řízení a bezpečnost

- [x] **DONE — manager:** Založit verzovaný recovery checklist před dalšími zásahy.
  - Ověření: tento soubor existuje a obsahuje vlastníka, stav, kritéria a checkboxy.
- [x] **DONE — manager:** Zachovat cizí změny a během obnovy nic necommitovat ani nepushovat.
  - Ověření: závěrečný audit `git status`, žádný nový commit a žádný push.
- [x] **DONE — manager:** Integrační kontrola celé obnovy.
  - Ověření: `git diff --check`, relevantní testy a validace projdou; otevřené body jsou níže přesně vymezené.

## Proud A — CI, dokumentace, varování, referenční data

- [x] **DONE — agent A:** Nastavit oba GitHub Actions workflow pouze na ruční spuštění.
  - Ověření: `.github/workflows/validate.yml` i `.github/workflows/links.yml` obsahují jen `on: workflow_dispatch:`; žádný `push`, `pull_request` ani `schedule`.
- [x] **DONE — agent A:** Opravit zastaralá tvrzení v dokumentaci o stavu fáze 2 a závislosti importu obcí.
  - Ověření: dokumentace odpovídá skutečně implementovaným částem a netvrdí, že celá fáze 2 není implementovaná.
- [x] **DONE — agent A:** Vyřešit osm známých validačních varování (3 sporné kategorie, 4 Facebook `source_id` mimo registr, 1 obecný homepage odkaz).
  - Ověření: `python3 tools/validate/validate.py` skončí bez chyb a bez těchto varování; každá oprava zachová doložitelný význam dat.
  - Výsledek: validace skončila s 0 chybami a 0 varováními; tři netaxonomické štítky byly odstraněny bez ztráty druhu akce, čtyři doložené zdroje doplněny a obecný odkaz nahrazen konkrétním článkem.
- [x] **DONE — agent A:** Dokončit import obcí a rozhodnutých kategorií do SQLite vrstvy.
  - Ověření: importovaná databáze obsahuje obce a kategorie, roundtrip publikovaných dat zůstává bezeztrátový a související testy projdou.
  - Výsledek: 899 obcí, 18 kategorií a po dokončení P2-3 124 aliasů; `test_import_repo.py` i roundtrip prošly.

## Proud B — fetch/snapshot a deterministické adaptéry

- [x] **DONE — agent B:** Dokončit P1-2 fetch/snapshot vrstvu s reprodukovatelnými lokálními snapshoty a evidencí výsledku.
  - Ověření: testy pokryjí úspěch, opakované/deterministické zpracování a běžnou chybu bez závislosti na živé síti.
- [x] **DONE — agent B:** Dokončit P1-3 adaptéry včetně třetího adaptéru, fixtures a měření.
  - Ověření: schema.org, iCal a třetí podporovaný formát mají fixtures a testy; výstup je deterministický a reportuje počet nalezených/přijatých/odmítnutých položek.
  - Výsledek: tři golden sady pro Kultura HK, UFFO a Pardubice prošly; měření W32 uvádí 101 termínů adaptérů proti 6 kandidátům omezeného LLM discovery běhu.

## Integrační proud — inbox, health a web

- [x] **DONE — manager:** Dokončit P1-4 manuální URL inbox nad existující normalizací a klasifikací.
  - Ověření: existuje vstupní příkaz, perzistentní inbox, deterministické zpracování stavů a testy pro validní, duplicitní a odmítnuté URL.
  - Výsledek: `submit.py`, `process.py`, společná fetch/snapshot vrstva a `test_inbox.py`; detail vytváří kandidáta, výpis návrh zdroje a třetí chyba stažení uzavírá položku.
- [x] **DONE — manager:** Dokončit P1-5 migraci health dat ze souborů do SQLite.
  - Ověření: `verified_at` a `upcoming_events_at_check` nejsou autoritativně drženy ve `config/facebook-sources.json`, import zachová health historii v databázi, export/validace a health testy projdou.
  - Výsledek: 36/36 historických měření bylo jednorázově přeneseno bez přeskočení. Autoritativní provozní stav nyní žije v `var/pardubicko.db`, tabulce `source_health`; git drží jen konfiguraci. Následný import zachoval 36 řádků a SQLite roundtrip zůstal beze změny.
  - Produktové rozhodnutí potvrzené uživatelem 3. 8. 2026: SQLite databáze je běhový stav a její health historie se nemusí přenášet do nového klonu repozitáře.
- [x] **DONE — agent P2-1:** Dokončit spustitelnou PHP serving vrstvu.
  - Ověření: `web/public/router.php` a front controller obslouží přehled, detail, obec, kalendář a hledání nad SQLite.
- [x] **DONE — agent P2-1:** Dokončit plně serverové filtry a stránkování.
  - Ověření: GET filtry a stránkování fungují bez JavaScriptu, stav je v URL a filtrování zůstává pouze v SQL.
- [x] **DONE — agent P2-1:** Dokončit SEO a zpětnou kompatibilitu URL.
  - Ověření: `?event=id` přesměruje na detail, existují `sitemap.xml`, `robots.txt` a validní JSON-LD na detailu.
- [x] **DONE — agent P2-1:** Dokončit provozní endpointy.
  - Ověření: tokenem chráněný `POST /api/inbox` vrací 202/200/401 podle ADR 0005 a `GET /api/health` vrací přehled zdrojů.
- [x] **DONE — agent P2-1:** Dokončit šablony, assety, Docker službu a testy.
  - Ověření: `app` už není v profilu `incomplete`, `web/README.md` odpovídá realitě, PHP testy a HTTP smoke test v Dockeru projdou.
  - Výsledek: deterministický test prošel 40 kontrolami a HTTP smoke 15 kontrolami; testovací server běžel nad oddělenou databází a byl po ověření ukončen.
- [x] **DONE — manager:** Bezpečně rozhodnout kategorie `venkovní-akce`, `ukázky` a `komedie` z existujících dat a pravidel.
  - Ověření pro `DONE`: rozhodnutí je konzistentně propsáno do konfigurace/dat/testů. Jinak označit `BLOCKED` s potřebným produktovým rozhodnutím.
  - Výsledek: všechny tři byly jako vlastnost, popis programu a žánr odstraněny; jejich význam zůstal v popisu nebo jiném zařazení. Navazující kanonický přepis, frontend, validace a adaptéry jsou dokončené níže.

## Druhá vlna — dokončení P2-3

- [x] **DONE — agent P2-3:** Kanonicky přepsat kategorie v publikovaných týdnech při zachování os `kind` a `audience`.
  - Ověření: každá hodnota je kanonické ID ze slovníku, každá akce má alespoň jeden `kind`, počet unikátních akcí i vazby `event_week` se nezmění.
- [x] **DONE — agent P2-3:** Upravit SQLite import/export pro kanonický model bez ztráty roundtripu.
  - Ověření: `category`, `category_alias` a `event_category.category_id` jsou konzistentní a `pipeline.py roundtrip --diff` projde bajtově.
- [x] **DONE — agent P2-3:** Upravit statický frontend na dvě osy kategorií.
  - Ověření: slovník se načítá z konfigurace, popisky a pořadí pocházejí ze slovníku, existují samostatné filtry `kind`/`audience` a stav zůstává sdílitelný v URL.
- [x] **DONE — agent P2-3:** Zachovat fulltext, badges a kalendář nad kanonickými ID.
  - Ověření: fulltext indexuje popisky i aliasy, badge tones používají kanonická ID a long-running logika používá `vystavy`; frontendové regresní testy projdou.
- [x] **DONE — agent P2-3:** Zpřísnit schéma a validaci publikovaných kategorií.
  - Ověření: nekanonická hodnota je validační chyba, validace současných dat skončí 0 chyb/0 varování.
- [x] **DONE — agent P2-3:** Doplnit normalizaci kategorií do všech tří adaptérů.
  - Ověření: známé aliasy se deterministicky mapují na kanonická ID obou os, neznámé hodnoty se nedohadují a jsou měřené jako odmítnuté/varování; golden testy projdou.
  - Produktové rozhodnutí potvrzené uživatelem 3. 8. 2026: tyto tři hodnoty se nemají zachovávat v samostatné ose štítků.

## Závěrečné ověření

Níže uvedené výsledky zachycují první vlnu obnovy. Po dokončení druhé vlny
se musí zopakovat samostatný integrační audit v následující sekci.

- [x] **DONE — manager:** Spustit úplnou lokální testovací sadu.
  - Ověření: `python3 tools/run_tests.py` projde bez přeskočení povinných testů.
  - Výsledek: po načtení závislosti `jsonschema` z dočasného `/tmp` prošlo 8 z 8 sad včetně Node frontendu.
- [x] **DONE — manager:** Spustit datovou validaci.
  - Ověření: `python3 tools/validate/validate.py` skončí s návratovým kódem 0 a závěrečný souhrn je zaznamenán.
  - Výsledek: 0 chyb, 0 varování.
- [x] **DONE — manager:** Ověřit bezeztrátový SQLite roundtrip.
  - Ověření: `python3 tools/pipeline/pipeline.py roundtrip --diff` skončí s návratovým kódem 0 a bez rozdílu.
  - Výsledek: všech 7 publikovaných souborů je bajtově shodných.
- [x] **DONE — manager:** Ověřit syntaxi a čistotu diffu.
  - Ověření: relevantní PHP/Python syntax checky a `git diff --check` projdou.
  - Výsledek: Python compile, PHP lint v kontejneru a `git diff --check` prošly.

## Závěrečné ověření druhé vlny

- [x] **DONE — manager:** Integrovat rozhraní mezi kanonickými kategoriemi a PHP webem.
  - Ověření: PHP používá kanonická ID a popisky/osy ze SQLite slovníku; neduplikuje mapování ze statického frontendu.
- [x] **DONE — manager:** Spustit úplnou sadu nástrojových a frontendových testů.
  - Ověření: `tools/run_tests.py` projde bez přeskočení a zahrnuje alespoň původních 8 sad plus nové PHP/smoke testy.
  - Výsledek: nástrojová sada prošla 9/9, PHP integrační test 40/40 a HTTP smoke 15/15.
- [x] **DONE — manager:** Spustit datovou validaci a SQLite roundtrip.
  - Ověření: validace skončí 0 chyb/0 varování, roundtrip je bajtově shodný a počty akcí/týdenních vazeb zůstanou 77/81.
  - Výsledek: 0 chyb/0 varování, 7/7 souborů bajtově a invariant 77 unikátních akcí / 81 týdenních vazeb.
- [x] **DONE — manager:** Ověřit spustitelnou Docker aplikaci přes HTTP.
  - Ověření: smoke pokryje přehled, detail, obec, kalendář, hledání, redirect, sitemap, robots, inbox 401/202/200 a health endpoint.
- [x] **DONE — manager:** Ověřit syntax a čistotu finálního diffu bez commitu/pushe.
  - Ověření: PHP lint, Python compile, `git diff --check`, nezměněný `HEAD` a audit manual-only workflow projdou.
  - Výsledek: vše prošlo; `HEAD` zůstal `f947198`, žádný commit ani push nevznikl a oba workflow stále obsahují pouze `workflow_dispatch`.
