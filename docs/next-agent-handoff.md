# Handoff pro dalšího orchestrace agenta

Tento dokument je samostatné zadání pro agenta, který převezme repozitář po
dokončení obnovy fáze 2. Nepředpokládá znalost předchozího chatu. Aktuální
implementované změny zatím nejsou commitnuté ani pushnuté.

## 1. Cíl převzetí

Dovést projekt z funkčního lokálního prototypu fáze 2 do bezpečně verzovaného,
provozně nasaditelného a automatizovatelně udržovaného systému. Nejde primárně
o UX. Prioritou jsou historie změn, integrita dat, kompletní datový tok,
deduplikace, monitoring, produkční provoz a úplné ručně spouštěné kontroly.

Práci rozděl mezi subagenty na nezávislé balíčky, ale zachovej jednoho managera,
který vlastní integrační checklist, řeší konflikty a provádí závěrečný audit.

## 2. Pevná rozhodnutí uživatele

- GitHub Actions workflow smějí mít **pouze ruční trigger**
  `workflow_dispatch`. Nepřidávej `push`, `pull_request` ani `schedule`.
- SQLite je běhová databáze. Provozní health historie nemusí být verzovaná
  v gitu ani přenositelná do nového klonu.
- Hodnoty `venkovní-akce`, `ukázky` a `komedie` nejsou samostatné kategorie
  ani nová osa štítků. Jejich význam zůstává v popisu nebo jiném zařazení.
- Nezaváděj Next.js, uživatelské účty ani veřejný neověřený příjem tipů.
- Zachovej auditovatelný JSON export v `data/` a bezeztrátový SQLite roundtrip.
- Cizí změny ve working tree zachovej. Bez výslovného zadání necommituj ani
  nepushuj; první balíček má připravit návrh logického rozdělení commitů a
  vyžádat si potvrzení před publikací.

## 3. Aktuální ověřený stav

Recovery checklist je v `docs/working-tree-recovery-checklist.md` a jeho dvě
vlny jsou dokončené.

Implementováno:

- deterministická validace dat a dva manuální GitHub workflows,
- SQLite schéma, migrace a bajtově shodný import/export,
- 899 obcí, 18 kanonických kategorií a 124 kategoriálních aliasů,
- fetch/snapshot vrstva a tři adaptéry s golden fixtures,
- dávkový runner s due výběrem, normalizací, kandidátní/karanténní frontou a reporty,
- URL inbox, health tracking a 36 migrovaných health záznamů,
- deduplikace se třemi pásmy a provizorní kalibrací,
- doložený geografický alias, import a coverage report,
- statický frontend nad JSON a serverem renderovaný PHP web,
- GET filtry a stránkování bez JavaScriptu, detail, obec, kalendář, hledání,
  sitemap, robots, JSON-LD, legacy redirect, inbox API a health API,
- produkční PHP-FPM + Nginx konfigurace, persistentní volumes a testované
  backup/restore nástroje (bez doloženého deploye na cílovém hostu),
- externí odkazy na zdroje se otevírají v novém okně s bezpečným `rel`.

Poslední ověření:

- úplná deterministická sada: 16/16 (Python, Node, PHP a HTTP),
- deterministické PHP testy: všechny kontroly prošly,
- HTTP smoke: 15/15,
- validace: 0 chyb, 0 varování,
- SQLite roundtrip: 7/7 souborů bajtově,
- invarianty: 77 unikátních akcí, 81 týdenních vazeb, žádné chybějící
  `category_id`, každá akce má kategorii osy `kind`,
- `git diff --check`, Python compile, PHP lint a JS syntax prošly,
- oba workflow obsahují pouze `workflow_dispatch`,
- produkční PHP, Nginx a pipeline image se lokálně sestavily; `nginx -t`
  prošel s dočasným certifikátem,
- `HEAD` byl při posledním auditu `f947198`; větev byla o dva commity před
  `origin/main`, nic nebylo staged.

Lokální PHP aplikace se spouští na `http://localhost:8081` přes službu `app`.
Vestavěný PHP server je pouze vývojový.

## 4. Známé technické nálezy

### 4.1 Nezabezpečený rozsah změn ve working tree

Při posledním auditu bylo 32 změněných tracked položek (z toho 1 smazaná) a
128 skutečných untracked souborů. Nic nebylo staged a runtime obsah `var/`
zůstává ignorovaný. Největší okamžité riziko je nečitelný monolitický commit;
změny publikuj jen po potvrzení logického commit plánu.

### 4.2 Časová konzistence manifestu je vynucená

`data/manifest.json.generated_at` odpovídá nejnovějšímu publikovanému týdnu.
Validátor odmítne starší manifest, negativní test drží regresi a metadata se
přenášejí do SQLite i root sitemap `lastmod`.

### 4.3 Dávkový datový tok je implementovaný po kurátorskou hranici

`tools/pipeline/run.py` vybírá enabled/splatné zdroje a orchestruje:

```
registry -> fetch -> extract -> normalize -> match -> candidate/review -> report
```

Zpracování zdrojů má izolované transakce, raw provenance a idempotentní ID.
Runner záměrně nevkládá nový `event` ani neexportuje neověřenou akci: publish
a JSON export zůstávají explicitním kurátorským krokem. Jistá shoda smí pouze
přidat další `event_source` k již publikované akci.

### 4.4 P2-2 deduplikace je implementovaná s provizorní kalibrací

`tools/pipeline/matching.py` implementuje blokování, podobnost názvu, místa a
času i tři rozhodovací pásma. Automatické spojení zachovává `event_source` a
střední pásmo ukládá do `match_review`. Reprodukovatelný test a výsledky jsou
v `test_matching.py` a `docs/deduplication-calibration.md`. Kalibrační korpus
je malý; nula pozorovaných falešných sloučení není odhad produkční chybovosti.

### 4.5 P2-4 živý smoke a podklad pro opravnou smyčku jsou implementované

Samostatný CLI `tools/pipeline/live_smoke.py` nejprve offline ověří golden
fixture, poté porovná živý výstup s její strukturou a s provozním health
baseline. Rozlišuje regresi kódu od změny zdroje a při doloženém nezdravém
stavu uloží poslední funkční vstup i výstup a jejich diff proti aktuálnímu.
Reporty jsou provozní historie v ignorovaném `var/live-smoke/reports/`.
Nástroj adaptér nepřepisuje ani nevolá LLM; pouze explicitně otevře bránu
pro samostatnou opravu, pokud je stav `suspect`, `degraded` nebo
`schema-drift` a existuje skutečný diff. Automatický harmonogram nevznikl;
CLI se spouští provozně ručně, odděleně od deterministického CI.

### 4.6 Produkční stack je implementovaný, nasazení není doložené

`docker-compose.production.yml` používá PHP-FPM + Nginx, TLS, persistentní
volumes, omezení inboxu a oddělený pipeline kontejner. `tools/ops/` obsahuje
`VACUUM INTO` zálohu, retenci, checksum a test obnovy; provoz popisuje
`docs/production-runbook.md`. Vývojový `docker-compose.yml` dál správně
používá `php -S`. Lokální build všech tří image a `nginx -t` prošly; chybí
smoke/deploy na cílovém hostu a skutečné přepnutí veřejné URL.

### 4.7 Ruční CI pokrývá celý deterministický stack

`.github/workflows/validate.yml` i lokální testovací image spouštějí Python,
Node, PHP integrační test, izolovaný HTTP smoke, validaci a roundtrip. Linkcheck
zůstává oddělený a oba workflow mají výhradně `workflow_dispatch`.

### 4.8 Geografické aliasy jsou konzervativně zavedené

`config/municipality-aliases.json` doloženě mapuje `Janderov → Chrudim`,
import plní `municipality_alias` a `tools/pipeline/coverage.py` generuje
`stats/coverage.json`. Plošný CISMC se neimportoval; nejednoznačné hodnoty se
neodhadují. Report eviduje jednu nevyřešenou hodnotu `Hrádek u Nechanic`.

### 4.9 Dokumentační rozpory WP8 jsou vyřešené

`docs/README.md`, `docs/phase-2-architecture.md`, definice Quality Agenta,
vize a pracovní balíčky nyní rozlišují cílovou architekturu od skutečného
stavu. ADR 0007 určuje PHP jako cílovou veřejnou produkční plochu a statický
GitHub Pages web jako současnou veřejnou a budoucí regresní vrstvu. Samotné
produkční přepnutí čeká na provozní podmínky z WP7; žádný frontend se nemaže.

### 4.10 Otevřené provozní otázky

- prahy deduplikace a health nejsou zkalibrované na dostatečných datech,
- retenční politika snapshotů není ověřena proti velikosti disku,
- není rozhodnuto, zda kandidáti a run reporty zůstanou primárními soubory,
  nebo exportovanými pohledy z databáze,
- odvozené členství akcí v týdnech podle ADR 0006 je schválené, ale odložené.

## 5. Doporučené pracovní balíčky

### WP0 — Audit a verzovací checkpoint

1. Zopakuj `git status`, `git diff`, `git diff --cached` a audit untracked
   souborů.
2. Ověř, že runtime artefakty v `var/` nejsou v gitu.
3. Rozděl změny do návrhu logických commitů, například:
   - kurátorská data a reporty,
   - validace/CI/licence,
   - SQLite pipeline a health,
   - adaptéry/inbox,
   - kanonické kategorie a statický frontend,
   - PHP web a testy,
   - dokumentace.
4. Nic nezahoď a bez potvrzení uživatele necommituj ani nepushuj.

Akceptace: žádný neznámý nebo omylem staged soubor; uživatel dostane čitelný
návrh commitů a přesný souhrn změn.

### WP1 — Metadata a validační mezery

**Stav: hotovo.**

1. Oprav `data/manifest.json.generated_at` konzistentně s publikovanými daty.
2. Doplň validaci, že manifest není starší než žádný odkazovaný týden.
3. Přidej negativní test uměle zastaralého manifestu.
4. Oprav navazující metadata v DB, sitemapě a dokumentaci.

Akceptace: poškozený manifest validátor odmítne; aktuální data projdou 0/0;
roundtrip zůstane bajtově shodný.

### WP2 — Úplné ruční CI

**Stav: hotovo.**

1. Zachovej výhradně `workflow_dispatch`.
2. Přidej PHP runtime a spusť `web/tests/test_web.php`.
3. Přidej deterministický HTTP smoke nad dočasnou SQLite databází.
4. Zajisti, aby deklarovaný lokální testovací příkaz nepřeskakoval Node/PHP.
5. Zachovej oddělení síťového linkchecku od deterministické validace.

Akceptace: ruční validační workflow spustí Python, Node, PHP, validaci,
roundtrip a HTTP smoke; žádný automatický trigger nevznikne.

### WP3 — End-to-end pipeline runner

**Stav: hotovo po bezpečnou kurátorskou hranici.** Runner nikdy automaticky
nepublikuje novou akci; jeho offline fixtures, idempotence, due výběr, dry-run
a izolace chyby jsou testované.

1. Přidej dávkový CLI runner pro všechny enabled zdroje splatné podle
   `check_interval_days`, s možnostmi `--source`, `--due`, `--offline` a
   `--dry-run`.
2. Propoj fetch/extract s kandidátní tabulkou a ukládej reprodukovatelný raw
   payload i metriky.
3. Implementuj deterministickou normalizaci termínů, cen, URL a obcí;
   nejasnosti posílej do karantény.
4. Zaveď explicitní stavy běhu a transakční hranice tak, aby pád jednoho
   zdroje neztratil výsledky ostatních.
5. Generuj report podle `docs/monitoring.md`.

Akceptace: offline fixtures projdou celým tokem až do kandidáta; opakovaný běh
je idempotentní; chyba jednoho zdroje nezruší úspěšné výsledky jiného.

### WP4 — P2-2 deduplikace

**Stav: hotovo s provizorní kalibrací.** Další práce má rozšířit malý korpus
o skutečně rozhodnuté řádky `match_review`, ne vydávat současné prahy za
produkčně zkalibrované.

1. Použij blok `(date(start_at), municipality_id)`.
2. Implementuj podobnost názvu, místa a času se třemi rozhodovacími pásmy.
3. Zachovej všechny vazby `event_source` při sloučení.
4. Přidej frontu pro střední pásmo; automaticky neslučuj nejisté případy.
5. Kalibruj prahy proti `benchmarks/` a doloženým Facebook duplicitám.
6. Zdokumentuj počet falešných sloučení a zvolený kompromis.

Akceptace: referenční duplicity se chovají podle očekávání, sloučení neztratí
zdroj a kalibrační report je reprodukovatelný.

### WP5 — P2-4 živý monitoring adaptérů

**Stav: hotovo 3. 8. 2026 jako samostatný ruční CLI bez workflow.**

1. Spusť adaptéry proti živým zdrojům odděleně od deterministického CI.
2. Porovnej strukturu aktuální extrakce s fixtures a health baseline.
3. Při nezdravém stavu vytvoř diff snapshotu a posledního funkčního výstupu.
4. LLM opravu spouštěj pouze nad doloženým nezdravým stavem.
5. Zapisuj report a nikdy automaticky nepřepisuj adaptér bez testu fixture.

Akceptace: test odliší regresi kódu od změny vstupu a sestaví dostatečný
podklad pro opravu bez opakovaného zatěžování zdroje.

### WP6 — Geografické aliasy a pokrytí

**Stav: hotovo v konzervativním rozsahu.** Jeden doložený alias, import,
coverage report a test existují; CISMC se bez ověřené potřeby neimportoval.

1. Navrhni verzovaný zdroj `municipality_alias` konfigurace.
2. Doloženě namapuj části obcí, počínaje `Janderov -> Chrudim`.
3. Rozhodni, zda importovat číselník částí obcí CISMC.
4. Vygeneruj `stats/coverage.json` nebo ekvivalentní report z databáze.

Akceptace: aliasy jsou auditovatelné, nejednoznačné hodnoty se neodhadují a
coverage report rozlišuje obec, část obce a nevyřešenou hodnotu.

### WP7 — Produkční deploy a provozní runbook

**Stav: implementace hotová, cílový deploy neověřený.** Produkční compose,
PHP-FPM + Nginx, TLS, volumes, ochrana inboxu, backup/restore a runbook
existují. Zbývá integrační build/smoke a nasazení na cílovém hostu.

1. Zvol PHP-FPM + Caddy/Nginx/Apache nebo ekvivalent; nepoužívej `php -S`
   v produkci.
2. Nastav HTTPS, produkční `PARDUBICKO_BASE_URL` a bezpečný inbox token.
3. Přidej persistentní SQLite volume a snapshot volume.
4. Implementuj denní zálohu přes `VACUUM INTO`, retenci a test obnovy.
5. Přidej scheduler pro pipeline mimo GitHub Actions nebo jako ručně
   provozovaný systém podle rozhodnutí uživatele.
6. Nastav request body limit, rate limiting a logování pro inbox API.
7. Zdokumentuj start, stop, upgrade, migraci, backup, restore a rollback.

Akceptace: deploy nepoužívá vývojový server, data přežijí restart/redeploy,
restore test je doložený a secrets nejsou v repozitáři.

### WP8 — Dokumentace a rozhodnutí o dvou frontendech

**Stav: hotovo 3. 8. 2026.** Rozhodnutí je v ADR 0007 a propsané do README,
architektury, vize, Quality Agenta a pracovních balíčků. Produkční přepnutí
není součástí hotového WP8; zůstává závislé na WP7.

1. Oprav zastaralé výroky o stavu fáze 2, PHP webu a kategoriích.
2. Rozhodni a zdokumentuj, zda veřejnou produkční plochou zůstává statický web,
   PHP web, nebo dočasně oba.
3. Pokud oba, vymez odpovědnost a regresní kompatibilitu; pokud jeden,
   připrav bezpečný postup ukončení druhého.
4. Aktualizuj README, ADR a pracovní balíčky podle skutečného stavu.

Akceptace: nový agent z dokumentace jednoznačně pozná, co existuje, co je
produkční a co je pouze kompatibilní referenční vrstva.

## 6. Doporučené pořadí a paralelizace

WP0 až WP8 jsou implementované. Další pořadí je provozní: nejprve potvrdit a
zapsat logické commity, potom ověřit backup/restore a offline runner na cílovém
hostu, teprve pak zapnout cron a provést produkční smoke/přepnutí podle ADR
0007. Prahy deduplikace se rozšiřují až z reálně rozhodnutých review případů.

Manager průběžně vede checklist s `TODO/WIP/DONE/BLOCKED`. Odškrtává jen
položky s doloženým testem nebo explicitním produktovým rozhodnutím.

## 7. Povinné závěrečné ověření

Minimálně spusť:

```bash
docker compose run --rm --build tests
git diff --check
git diff --cached --stat
git status --short --branch
```

Dále:

- PHP lint všech `web/**/*.php`,
- `node --check` relevantních JS souborů,
- HTTP smoke nad izolovanou dočasnou DB,
- invarianty alespoň 77 unikátních akcí / 81 vazeb `event_week`, dokud změna
  produkčních dat není samostatně doložená,
- žádné `category_id IS NULL`, každá publikovaná akce má `kind`,
- oba `.github/workflows/*.yml` obsahují pouze `workflow_dispatch`,
- žádný runtime DB, snapshot, token ani jiný secret není v gitu.

## 8. Soubory, které načíst jako první

1. `docs/working-tree-recovery-checklist.md`
2. `docs/project-vision.md`
3. `docs/phase-2-architecture.md`
4. `docs/phase-2-work-packages.md`
5. `docs/adr/0001-weekly-json.md` až `0007-public-frontend.md`
6. `tools/pipeline/README.md`
7. `tools/ingest/README.md`
8. `web/README.md`
9. `.github/workflows/validate.yml`
10. `docker-compose.yml` a `docker-compose.production.yml`

## 9. Výstup dalšího agenta

Na konci musí agent předat:

- aktualizovaný checklist s odškrtnutými ověřenými položkami,
- seznam změněných souborů po pracovních balíčcích,
- výsledky testů a invariantů,
- přesný seznam otevřených rozhodnutí a blokátorů,
- návrh commitů; commit/push pouze po výslovném potvrzení uživatele,
- provozní instrukce včetně backup/restore, pokud byl dokončen WP7.
