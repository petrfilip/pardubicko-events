# Pracovní balíčky fáze 2

Rozpad architektury z `docs/phase-2-architecture.md` do balíčků, které lze zadat samostatně. Každý balíček má vlastní kritéria přijetí; balíček bez splněných kritérií se nepovažuje za hotový.

Priority:

- **P0** — základ, bez kterého nemá smysl stavět zbytek. Přináší hodnotu i samostatně.
- **P1** — jádro fáze 2.
- **P2** — dokončení a rozšíření.

Doporučené pořadí je pořadí čísel. Balíčky P0 nevyžadují databázi ani server, takže je lze udělat okamžitě.

---

## P0-1 — Deterministická kontrola kvality v CI

**Stav: hotovo.** Implementováno v `tools/validate/` (schémata, sémantické kontroly, testy, kontrola odkazů), workflow `.github/workflows/validate.yml` a `links.yml`, vývojové prostředí v `docker-compose.yml`. Popis je v `tools/validate/README.md`.

**Proč první:** Bez strojové kontroly kvality každá další vrstva
znásobuje prostor pro tichou chybu. Kontroly proto existují lokálně i jako
ručně spouštěné GitHub Actions workflow; automatické triggery jsou záměrně
vypnuté.

**Rozsah:**

- Validace JSON schématem pro `data/weeks/*.json`, `data/manifest.json`, `research/candidates*.json`, `config/*.json` a `stats/runs/**.json`.
- Kontroly nad rámec schématu: `end_at >= start_at`, zařazení akce do správného ISO týdne, konfliktní duplicity ID podle pravidla z ADR 0001, existence všech souborů odkazovaných manifestem, neprázdné a konkrétní zdrojové URL.
- Ručně spouštěný GitHub Actions workflow pro deterministické kontroly.
- Samostatný ručně spouštěný běh kontroly odkazů, oddělený od validace struktury, protože síťová chyba nemá blokovat validaci dat.

**Kritéria přijetí:**

- Workflow selže nad uměle poškozeným souborem v každé z uvedených kategorií.
- Workflow projde nad současným stavem repozitáře, nebo jsou nalezené vady opraveny v témže balíčku.
- Validace běží bez přístupu k síti.

---

## P0-2 — Doplnit chybějící definici Quality Agenta

**Stav: hotovo.** `docs/agents/quality-agent.md`; dělbu metrik mezi validátor a agenta popisuje upravená sekce Quality v `docs/monitoring.md`.

**Proč:** Před implementací `docs/project-vision.md` roli popisoval, ale
`docs/agents/quality-agent.md` na rozdíl od zbývajících čtyř rolí neexistoval.
Po P0-1 se navíc změnila náplň role: strojové kontroly přebírá CI, agentovi
zůstává úsudková část.

**Rozsah:** definice ve struktuře shodné s ostatními soubory v `docs/agents/`, s vymezením vůči automatickým kontrolám z P0-1.

**Kritéria přijetí:** dokument existuje, odkazuje na metriky v `docs/monitoring.md` a neduplikuje kontroly, které už dělá CI.

---

## P0-3 — Uklidit rozpory v dokumentaci

**Stav: hotovo.**

**Rozsah a řešení:**

- Odkazy na tehdy neexistující vstupy byly prověřeny jednotlivě.
  `research/findings.md` byl založen a naplněn doloženými poznatky.
  `config/municipalities.json` mezitím vznikl v P1-6 a už patří mezi
  existující vstupy; `stats/coverage.json` vznikl s geografickými aliasy.
  Pouze `research/discovery-score.json` zůstává volitelný a plánovaný. Sekce
  6 vize tento aktuální stav rozlišuje.
- `.gitignore` doplněn o `.idea/`, `.vscode/` a o provozní artefakty fáze 2 (`var/`, soubory databáze).
- `LICENSE` doplněn. Kód je pod MIT, data pod CC BY-SA 4.0. Share-alike je zvolen záměrně: hlavní hodnotou projektu je pokrytí malých obcí, které jinde není, a odvozená databáze má zůstat otevřená. CC 4.0 se vztahuje i na zvláštní právo pořizovatele databáze podle evropského práva.

**Kritéria přijetí:** žádná definice agenta neodkazuje na neexistující povinný vstup. Splněno.

---

## P1-1 — Schéma databáze a obousměrná migrace

**Stav: hotovo.** `tools/pipeline/` — `schema.sql`, `db.py`, `jsonfmt.py`, `import_repo.py`, `export_repo.py`, `pipeline.py`, popis v `tools/pipeline/README.md`. Kolotoč sedí **bajtově** na všech sedmi souborech a je součástí ručně spouštěného validačního workflow.

Import je záměrně přísnější než dřívější znění ADR 0001 a odhalil tím dvě vady, které validátor nechytal: akci `rodinna-anamneza-chrudim-2026` s odlišným popisem ve dvou týdnech a dvojici `sportovni-park-pardubice-2026*` jako jednu akci pod dvěma `id`. Obě jsou opravené, druhá po ověření u zdroje. Pravidlo identity je nově sjednocené v ADR 0001 a má jedinou implementaci na každé vrstvě.

Tři odchylky od návrhu v architektuře (název obce a kategorie doslovně, explicitní `event_week`) jsou vysvětlené v `tools/pipeline/README.md`. Všechny mají stejný důvod: doložit bezeztrátovost dřív, než vznikne normalizační vrstva. Explicitní `event_week` je navíc dočasné, viz ADR 0006.

**Rozsah:**

- Schéma podle `docs/phase-2-architecture.md`, oddíl 4, včetně migračního mechanismu.
- Import: `config/*.json`, `data/weeks/*.json` a `research/candidates*.json` do databáze.
- Export: z databáze zpět do `data/manifest.json` a `data/weeks/*.json` v nezměněném formátu.

**Kritéria přijetí:**

- **Kolotoč sedí.** Import současných dat a následný export vyprodukují soubory sémanticky shodné se současnými. Rozdíly jsou buď žádné, nebo doložené a odůvodněné jako oprava.
- Stávající frontend nad exportovanými soubory funguje beze změny.
- Databáze není v gitu; je v `.gitignore`.

Tento balíček je pojistka celé fáze 2. Dokud kolotoč nesedí, další balíčky nezačínají.

---

## P1-2 — Fetch a snapshot vrstva

**Stav: hotovo.** `tools/pipeline/fetch.py` řeší robots.txt, vlastní
user-agent, pauzy, podmíněné dotazy, content-addressed gzip snapshoty,
offline běh a retenci. Úspěšné, nezměněné i neúspěšné pokusy pokrývá
`test_fetch.py` bez živé sítě.

**Rozsah:** společná vrstva podle oddílu 3.1 a 3.2 — user-agent, `robots.txt`, podmíněné dotazy, pauzy, ukládání snapshotů adresované obsahem, zápis do `source_fetch`.

**Kritéria přijetí:**

- Každý pokus, i neúspěšný, má řádek v `source_fetch`.
- Nezměněný obsah nevytvoří druhý snapshot.
- Adaptér umí běžet nad uloženým snapshotem bez přístupu k síti.

---

## P1-3 — První tři adaptéry

**Stav: hotovo pro první tři zdroje.** Registry mapuje
`kultura-hk-official` na schema.org, `uffo-trutnov` na iCal a
`pardubice-calendar` na deterministický HTML adaptér. Každý má golden vstup,
očekávaný výstup a test opakovatelnosti; měření je v pipeline README.

**Rozsah:** rozhraní adaptéru a tři implementace, volené tak, aby pokryly různé typy vstupu:

1. zdroj s `schema.org/Event` v JSON-LD,
2. zdroj s RSS nebo iCal,
3. městský kalendář bez strukturovaných dat.

Volba konkrétních zdrojů vychází z `config/source-registry.json`; přednost mají zdroje s prioritou `high` a krátkým intervalem kontroly.

**Kritéria přijetí:**

- Ke každému adaptéru je v repozitáři golden fixture (vstupní snapshot a očekávaný výstup) a test v CI.
- Adaptér vrací `null` u nepřečtených polí a nepřičítá si žádný odhad.
- Adaptér počítá `items_unparsed`.
- **Měření:** je zdokumentováno, kolik akcí za týden tyto tři zdroje vydaly proti tomu, kolik jich za stejné období našlo LLM discovery. Tohle číslo rozhoduje, jak agresivně pokračovat v ADR 0003.

---

## P1-4 — Inbox

**Stav: hotovo.** `tools/ingest/` obsahuje idempotentní vložení URL,
zpracování přes společný snapshot, konzervativní klasifikaci a testy bez
živé sítě. Tokenem chráněný HTTP vstup je součástí hotového P2-1.

**Rozsah:** tabulka `inbox`, CLI `tools/ingest/submit.py`, zpracování a klasifikace podle oddílu 6 architektury.

**Kritéria přijetí:**

- Vložení odkazu nevyžaduje commit ani běžící server.
- Detail akce vytvoří kandidáta ve stavu `new`, nikoli publikovanou akci.
- Výpis vytvoří `source-proposal` a **nezapíše** nic do `config/source-registry.json`.
- Nerozpoznaný odkaz skončí ve `failed` s důvodem a nevytvoří dohadovaný záznam.
- Opakované vložení téhož odkazu s jinými sledovacími parametry nevytvoří druhý záznam.

---

## P1-5 — Sledování zdraví zdrojů

**Stav: hotovo.** Vyhodnocení, report, odvození zrušení a povinné hraniční
testy jsou v `tools/pipeline/health.py` a `test_health.py`. Historických
36 měření z Facebook konfigurace bylo 3. 8. 2026 beze ztráty přesunuto do
`source_health`; autoritativní provozní stav žije v SQLite a opakovaný import
jej zachovává. Schéma konfigurace původní provozní pole už nepřipouští.

**Rozsah:** tabulky `source_extract` a `source_health`, vyhodnocení stavů a prahů podle oddílu 5, přehledový report.

**Kritéria přijetí:**

- Zdroj s baseline 40 položek, který vrátí 0, přejde do `suspect`.
- Zdroj s baseline 1 položky, který vrátí 0, zůstane `healthy`. Tento test je povinný.
- Zdroj, kterému se přestane plnit `start_at`, přejde do `schema-drift`.
- Pole `verified_at` a `upcoming_events_at_check` jsou z `config/facebook-sources.json` přesunuta do `source_health`.
- Odvození zrušení splňuje všechny tři podmínky z ADR 0004; test ověřuje, že jedno chybějící stažení zrušení nezpůsobí.

---

## P1-6 — Číselník obcí

**Stav: hotovo v konzervativně vymezeném rozsahu.** Doložený číselník 899
obcí, verzovaný alias `Janderov → Chrudim`, import aliasů a reprodukovatelný
`stats/coverage.json` jsou hotové a testované. Plošný číselník částí obcí
CISMC se neimportoval; nedoložené nebo nejednoznačné hodnoty se neodhadují.

**Rozsah:** import obcí obou krajů z veřejného číselníku ČSÚ nebo RÚIAN do tabulky `municipality`, tabulka aliasů, postup pravidelné aktualizace.

**Proč:** odpovídá na otevřenou otázku 4 z `docs/project-vision.md`. Bez úplného seznamu obcí nelze měřit pokrytí, což je hlavní deklarovaný cíl projektu.

**Kritéria přijetí:** seznam pochází z doloženého veřejného číselníku, ne z ručního výčtu; postup aktualizace je zdokumentovaný.

**Implementace číselníku.** `config/municipalities.json` vzniká
generátorem `tools/pipeline/municipalities.py`, validuje se vlastním schématem
a import P1-1 jím plní tabulku `municipality`. Jednoznačné doslovné názvy
publikovaných akcí se při importu propojují přes `event.municipality_id`;
nejednoznačné názvy a názvy částí obcí zůstávají bezpečně bez vazby.

### Zdroj dat

| | |
|---|---|
| Soubor | Struktura území ČR – otevřená data (CSV) |
| Vydavatel | Český statistický úřad |
| Rozcestník | `https://csu.gov.cz/i_zakladni_uzemni_ciselniky_na_uzemi_cr_a_klasifikace_cz_nuts` |
| Číselník | ČSÚ 43 (CISOB); `obec_kod` je shodný s kódem obce v RÚIAN |

Číselník CISOB sám o sobě nestačí — neobsahuje vazbu obce na okres a kraj. Soubor „Struktura území ČR“ ji obsahuje v jednom CSV, včetně typu sídla (`obec_typ`), a je publikovaný jako otevřená data přímo na `csu.gov.cz`.

Přímá adresa CSV nese číslo verze (`?version=1.2`), a proto se **nezapisuje do kódu**. Generátor si ji pokaždé najde na rozcestníku podle názvu souboru; přesná adresa použitá při posledním běhu je uložená v `source.url` výsledného souboru.

**Proč ne `apl.czso.cz/iSMS/cisexp.jsp`.** Klasický exportní endpoint číselníků ČSÚ je pohodlnější, ale `https://apl.czso.cz/robots.txt` obsahuje `Disallow: /` pro všechny roboty. Pravidlo z oddílu 3.1 architektury platí i pro nástroje mimo fetch vrstvu, takže se tento host nestahuje. `csu.gov.cz` stažení povoluje.

### Postup aktualizace

ČSÚ vydává novou strukturu území k 1. lednu. Stačí tedy jednou ročně, případně po změně územního členění:

```
python3 tools/pipeline/municipalities.py --dry-run   # kontrola: kolik obcí a k jakému datu
python3 tools/pipeline/municipalities.py             # přepíše config/municipalities.json
docker compose run --rm validate
```

Generátor běží nad standardní knihovnou, nepotřebuje tedy kontejner ani závislosti. Když je zdroj dočasně nedostupný, lze CSV stáhnout ručně a předat ho přes `--csv soubor.csv --csv-url <adresa, odkud pochází>`; adresa se pak zapíše do dokladu o původu.

Diff proti předchozí verzi je zároveň přehledem změn v území: přejmenování obce, změna typu sídla, vznik nebo zánik obce. Změny je vhodné projít, protože kód obce je klíčem tabulky `municipality` a zaniklá obec může mít navázané akce.

### Co generátor odmítne udělat

Zastaví se s chybou a nic nezapíše, pokud: zdroj vrátí méně než 800 obcí, míchá více dat platnosti, obsahuje neznámý typ sídla, nebo se množina okresů rozejde s `config/regions.json`. Rozejití okresů znamená buď změnu územního členění, nebo zastaralou konfiguraci — obojí patří člověku, ne odhadu.

### Známé meze

- `config/municipality-aliases.json` obsahuje zatím jediný doložený alias
  `Janderov → Chrudim`; každý další vyžaduje vlastní důkaz.
- Číselník nezná plošně části obcí ani městské části. CISMC se záměrně
  neimportoval, protože současný doložený případ řeší auditovatelný alias a
  plošný import by rozšířil model bez ověřené potřeby.
- Coverage report eviduje jednu nevyřešenou hodnotu `Hrádek u Nechanic`;
  systém ji bezpečně neodhaduje.

---

## P2-1 — Serverem renderovaný web v PHP

**Stav: hotovo.** `web/public` obsahuje front controller/router a assety,
`web/templates` serverové šablony a `web/src/Application.php` všechny routy.
GET výpisy, filtry a stránkování fungují bez JavaScriptu a zůstávají v SQL.
SEO, legacy redirect, tokenem chráněný inbox a health endpoint pokrývají
deterministické PHP testy i HTTP smoke test; spuštění popisuje `web/README.md`.
Podle ADR 0007 jde o cílovou kanonickou veřejnou plochu. Produkční přepnutí
ale není součástí hotového aplikačního balíčku a čeká na provozní server,
TLS, persistentní data, zálohy a ověřený restore.

**Rozsah:** stránky podle oddílu 7 architektury — přehled, detail akce, obec, kalendář, hledání — filtry jako formulář odesílaný metodou GET, `sitemap.xml`, `robots.txt`, JSON-LD na detailu, endpointy `POST /api/inbox` a `GET /api/health`.

**Kritéria přijetí:**

- **Všechny stránky jsou plně funkční s vypnutým JavaScriptem.** Včetně filtrování, stránkování a přepnutí týdne. Toto je hlavní kritérium balíčku.
- Stav filtrů je celý v URL a stránka je sdílitelná.
- Filtrování probíhá v SQL. V PHP ani v JavaScriptu neexistuje druhá implementace téže logiky.
- JSON-LD na detailu projde validátorem strukturovaných dat.
- Odkaz `?event={id}` z fáze 1 přesměruje na `/akce/{id}`.
- Statický náhled fáze 1 nad exportovanými daty dál funguje.

---

## P2-2 — Deduplikace napříč zdroji

**Stav: hotovo s provizorní kalibrací.** `tools/pipeline/matching.py`
implementuje blok, Jaro-Winkler skóre názvu, shodu času a místa i tři
rozhodovací pásma. Nejisté shody ukládá do `match_review`, automatické spojení
zachovává `event_source`. `test_matching.py` pokrývá referenční Facebook pár,
self-match případy a negativní pár; výsledky popisuje
`docs/deduplication-calibration.md`.

Kalibrační korpus je malý. Nula pozorovaných falešných sloučení proto není
odhad produkční chybovosti a prahy se musí dál ověřovat na rozhodnutých
řádcích `match_review`.

**Rozsah:** blokování, podobnost a rozhodovací pásma podle oddílu 3.5, tabulka `event_source`, fronta k rozhodnutí.

**Kritéria přijetí:**

- Prahy jsou zkalibrované proti `benchmarks/` a kalibrace je zdokumentovaná, včetně počtu falešných sloučení.
- Sloučení zachová všechny zdrojové vazby.
- Duplicity doložené v `docs/agents/daily-event-curator.md` — dvojí založení téže akce na Facebooku a shoda 6 z 82 kandidátů s produkčními daty navzdory odlišnému `facebook_event_id` — slouží jako testovací případy.

---

## P2-3 — Řízený slovník kategorií

**Stav: hotovo.** Slovník je v `config/categories.json`, schéma v
`tools/validate/schemas/categories.schema.json`, kontrola pokrytí v
`check_categories` v `tools/validate/checks.py`. Import plní `category`,
`category_alias` a `event_category.category_id`; export vydává kanonická ID.
Frontend čte české popisky a pořadí ze slovníku a nabízí nezávislé filtry
`kind` a `audience`, jejichž stav se zachovává v URL.

**Rozsah:** naplnění tabulek `category` a `category_alias`, jednorázová
migrace původních volných textů a doplnění mapování do adaptérů.

**Kritéria přijetí:** žádná publikovaná akce nemá kategorii mimo slovník; mapování zachová dnešní filtry ve frontendu.

### Co ukázala data

Původní audit byl změřen 2. 8. 2026 nad `data/weeks/*.json` (81 záznamů
akcí, 77 unikátních akcí) a `research/candidates*.json` (114 kandidátů).
Následují historické počty před rozhodnutím tří sporných hodnot:

- **48 různých hodnot** kategorií, kandidáti nepřidávají žádnou navíc — jejich 38 hodnot je podmnožinou týdenních souborů. Počty výskytů níže jsou z `data/weeks/*.json`.
- Rozptyl zrnitosti je zásadní: `hudba` (10×) vedle `taneční-hudba` (1×), `prohlídka` (13×) vedle `komentovaná-prohlídka` (1×), `kino` (6×) vedle `letní-kino` (2×) a `film` (1×).
- **Dvě různé osy v jednom poli.** `rodiny` je s 36 výskyty vůbec nejčastější hodnota a `děti` (2×) se k ní vždy přidává. Nejde o druh akce, ale o cílovou skupinu. Ve filtru dnes stojí v jedné nabídce vedle `koncert` a uživatel nemá jak říct „koncert pro rodiny“.
- Dalších pět hodnot není druh akce ani cílová skupina: `venkovní-akce` (6×) je vlastnost místa, `historie` (18×) a `historická-vozidla` (1×) jsou téma, `komedie` (1×) je žánr, `knihovna` (1×) a `muzeum` (1×) jsou typ místa konání.
- Průměr 2,91 kategorie na akci ukazuje, že se pole používá jako volné štítkování, ne jako zařazení.

Data pokrývají jen týdny W31 až W36, tedy srpen a září. Slovník proto nesmí být šitý na letní sezónu — kategorie jako plesy nebo adventní trhy v dnešních datech chybí, ale ve slovníku být musí, jinak je za půl roku nutné ho měnit znovu.

### Návrh: dvě osy, 16 druhů a 2 cílové skupiny

Zvoleny jsou **dvě osy**:

| Osa | Povinná | Kategorie |
|---|---|---|
| `kind` — druh akce | ano, alespoň jedna | `hudba`, `divadlo`, `tanec`, `film`, `vystavy`, `pamatky`, `vzdelavani`, `workshopy`, `sport`, `gastro`, `trhy`, `festivaly`, `slavnosti`, `komunitni`, `duchovni`, `zabava` |
| `audience` — cílová skupina | ne | `rodiny`, `deti` |

**Proč dvě osy a ne jedna.** Míchání druhu a cílové skupiny je hlavní příčina dnešní nepoužitelnosti filtru: nejčastější položka v nabídce (`rodiny`) neodpovídá na otázku „co se tam děje“ a zároveň blokuje otázku „co dělat s dětmi u koncertů“. Oddělením vzniknou dvě nezávislé nabídky, které jde kombinovat.

**Proč ne tři osy.** Nabízela se ještě osa témat a vlastností (`historie`, `venkovní-akce`, žánry). Zamítnuta: nesou ji dnes čtyři hodnoty s dohromady 26 výskyty, z nichž 18 připadá na jedinou hodnotu `historie`, kterou beze ztráty pohltí kategorie `pamatky`. Třetí select ve filtru je cena, kterou tři zbylé výskyty nezaplatí. Zbývající hodnoty jsou v seznamu sporných, viz níže.

**Proč 16 druhů.** Dolní hranici určuje užitečnost: pod deseti kategoriemi splyne koncert s divadlem a filtr přestane odlišovat. Horní hranici určuje přehlednost nabídky. Šestnáct položek se vejde do jednoho rozbaleného selectu bez rolování a odpovídá zrnitosti, kterou používají české kalendáře akcí. Každá ze 16 kategorií má oporu ve skutečných datech — po namapování má nejmenší z nich (`tanec`) jediný výskyt, ale je držena záměrně kvůli plesové sezóně, která v dnešním letním vzorku být nemůže.

**Co se sloučilo.** Hudební žánry (`folk`, `rock`, `pop`, `klasická-hudba`, `taneční-hudba`) do `hudba`. Sportovní disciplíny (`běh`, `cyklistika`, `fotbal`, `turistika`) do `sport`. `prohlídka`, `komentovaná-prohlídka` a `historie` do `pamatky`. `výstava`, `umění`, `muzeum` a `interaktivní-expozice` do `vystavy`. `pouť`, `slavnosti` a `folklór` do `slavnosti`; naproti tomu `folk` míří do `hudba` — dvojice folk/folklór je past, kterou alias tabulka řeší jednoznačně.

**Formát mapování.** `config/categories.json` drží `categories` (zrcadlo tabulky `category`, navíc se sloupci `axis` a `order`) a `aliases` (zrcadlo tabulky `category_alias`). Rozšíření o `axis` je jediná změna proti návrhu schématu v `docs/phase-2-architecture.md`, oddíl 4; dvě osy tedy nevyžadují novou tabulku ani novou vazbu, jen jeden sloupec navíc.

Porovnání aliasu je bezdiakritické a nerozlišuje mezeru od spojovníku, takže jeden zápis `klasická-hudba` pokryje i `Klasická hudba` a `klasicka hudba`. Kanonické id platí i bez aliasu, aby slovník fungoval i nad už zmigrovanými daty. Slovník má 124 aliasů; kromě hodnot z původních dat obsahuje běžná znění, na která adaptéry narazí (`přednáška`, `ples`, `farmářské-trhy`, `posvícení`, `vernisáž`).

### Sporné případy

Šest hodnot má v `config/categories.json` samostatný seznam `review` s
důvodem, variantami a rozhodnutím. Tři hodnoty bez jednoznačného cíle byly
3. 8. 2026 bezpečně vyřazeny z kategorií; jejich význam zůstává v popisu,
místě nebo jiné kategorii a validátor je už nehlásí:

| Hodnota | Výskytů | Stav | Podstata sporu |
|---|---|---|---|
| `venkovní-akce` | 6 | odstraněno | Vlastnost místa, ne druh akce; případně budoucí pole `outdoor`. |
| `ukázky` | 2 | odstraněno | Popis programu; akce zůstaly zařazené jako památky nebo sport. |
| `komedie` | 1 | odstraněno | Žánr zachovaný v popisu; akce zůstala zařazená jako film. |
| `umění` | 9 | alias na `vystavy` | Ve třech případech jde o výtvarný workshop, ne o výstavu. |
| `knihovna` | 1 | alias na `vzdelavani` | Typ místa použitý jako kategorie. |
| `historická-vozidla` | 1 | alias na `pamatky` | Téma, ne druh akce. |

Žádná z akcí nepřijde o zařazení: i po vynechání tří nemapovaných hodnot má každá ze 77 akcí alespoň jednu kategorii z osy `kind`. Ověřeno kontrolou nad skutečnými daty.

### Realizovaný dopad na frontend

Původní data nesla v `categories` rovnou zobrazovaný český text. Po migraci
nesou kanonická ID a statický frontend bere popisky ze slovníku:

- `js/filters.js` plní nabídku podle `order` ze slovníku a nabízí druhý
  select pro osu `audience`.
- `searchableText()` indexuje kanonické ID, popisek i aliasy, takže fulltext
  zachoval dřívější slovní zásobu.
- `js/badges.js` používá kanonická ID a opravené tones.
- `js/calendar.js` používá pro dlouhodobé výstavy kanonické `vystavy`.
- Zdrojem popisků statické vrstvy je `config/categories.json`; PHP vrstva je
  získává přes `JOIN` na tabulku `category`.

Filtr kategorie zůstal funkčně stejný, jen používá kanonické hodnoty v
nabídce. Kritérium přijetí „mapování zachová dnešní filtry“ je splněné.

### Migrační postup

1. **Slovník do gitu.** Hotovo.
2. **Rozhodnout tři nemapované hodnoty.** Hotovo 3. 8. 2026; byly
   odstraněny jako vlastnost, popis programu a žánr, nikoli mapovány na
   nepřesnou kategorii.
3. **Zrcadlení do databáze.** Hotovo. Import plní `category`,
   `category_alias` a `event_category.category_id` z konfigurace v gitu.
4. **Přepis dat.** Hotovo exportem z databáze. Zůstalo 81 týdenních řádků,
   77 unikátních akcí a nezměnilo se jejich přiřazení k týdnům.
5. **Frontend.** Hotovo v `js/categories.js`, filtrech, odznacích, seznamu,
   kalendáři a detailu; fulltext indexuje ID, český popisek i aliasy.
6. **Utažení kontroly.** Hotovo. Schéma přijímá jen 18 kanonických ID a
   zakazuje duplicity; sémantická kontrola vyžaduje povinnou osu `kind`.
7. **Adaptéry.** Hotovo pro schema.org, iCalendar a Pardubice. Známé texty
   mapují přes aliasy; neznámé oddělí do `categories_unmapped`, započítají
   do `category_values_rejected` a nikdy z nich nevymyslí novou kategorii.

Všech sedm kroků je hotových a kryjí je golden testy adaptérů, testy
frontendu, validátor nad poškozenými vstupy a databázový round-trip.

---

## P2-4 — Živý smoke test a opravná smyčka

**Stav: implementováno 3. 8. 2026 jako samostatný CLI.**
`tools/pipeline/live_smoke.py` nejprve offline ověří golden fixture, poté
provede živý fetch, porovná strukturu s fixture i provozní health baseline a
při nezdravém stavu uloží diff proti poslednímu funkčnímu snapshotu. Nástroj
adaptér nepřepisuje a LLM nevolá; pouze doložený stav explicitně označí jako
způsobilý pro samostatnou opravnou smyčku. Deterministické testy používají
fake HTTP klienta a nemají přístup k síti.

**Rozsah:** pravidelný běh adaptérů proti reálným zdrojům s porovnáním tvaru výstupu proti fixtures; při přechodu zdroje do `suspect`, `degraded` nebo `schema-drift` sestavení podkladu pro LLM opravu (diff posledního funkčního snapshotu proti aktuálnímu).

**Kritéria přijetí:**

- Smoke test odliší regresi kódu od změny zdroje.
- Podklad pro opravu obsahuje diff a poslední funkční výstup.
- LLM se v této smyčce spouští pouze při nezdravém stavu, nikdy při běžném sběru.

---

## P2-5 — Přechod veřejné plochy na PHP

**Stav: rozhodnutí a produkční konfigurace hotové, přepnutí neprovedeno.**
ADR 0007 určuje PHP jako cílovou kanonickou veřejnou plochu. Statický GitHub
Pages web je do produkčního deploye veřejnou kompatibilní plochou a potom
zůstane regresním čtenářem auditního JSON exportu; žádný frontend se tímto
balíčkem nemaže.

**Rozsah:** vymezení vlastnictví veřejných URL, kompatibility obou vrstev,
podmínek přepnutí a rollbacku.

**Kritéria přijetí rozhodnutí:** dokumentačně splněno v ADR 0007, README,
vizi a architektuře. Produkční compose už používá PHP-FPM + Nginx, TLS,
persistentní volumes, omezení inboxu a testované backup/restore nástroje.
**Kritéria přijetí přepnutí:** úspěšný build a HTTP smoke na cílovém hostu,
produkční secrets a doložený rollback na statický web. Tato provozní část
zatím splněná není.

---

## Co se v této fázi nedělá

- Administrační rozhraní. Výslovně se nepožaduje.
- Veřejný příjem tipů od návštěvníků. Vyžadovalo by moderaci; samostatné rozhodnutí pro budoucí ADR.
- Next.js. Viz ADR 0002.
- Přihlašování a uživatelské účty.
- Personalizovaná doporučení uložená ve finálních datech. Zákaz z `docs/project-vision.md` platí dál.

## Poznámka pro agenty

Pořadí balíčků je závazné v jednom bodě: **P1-1 musí projít dřív, než začne cokoli dalšího z P1.** Kritérium „kolotoč sedí“ je jediná pojistka proti tichému rozejití databáze a publikovaných dat.

Prahy uvedené v architektuře jsou odhady. Balíček, který s nimi pracuje, má povinnost zaznamenat, na jakých datech byly ověřeny a s jakým výsledkem. Nezkalibrovaný práh vydávaný za ověřený je horší než žádný.
