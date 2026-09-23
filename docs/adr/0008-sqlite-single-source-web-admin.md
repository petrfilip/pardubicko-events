# ADR 0008: SQLite jako jediný zdroj pravdy, správa přes web a API

## Kontext

ADR 0002 zavedl SQLite a PHP, ale ponechal dvě reprezentace dat: git
(konfigurace, týdenní JSON, `research/` kandidáti) a SQLite (index a provozní
stav). Kód se mezitím odchýlil i od ADR 0002: `publish-candidate` zapisuje
týdenní JSON a databázi z něj celou znovu importuje, takže zdrojem pravdy jsou
fakticky soubory. Dokumenty si v tom odporují (`AGENTS.md` a `.gitignore`
proti ADR 0002).

Git sloužil ke dvěma věcem: jako záloha a jako auditní stopa. Obojí je
technický důsledek, ne požadavek. Skutečné požadavky jsou:

- správce chce **všechno spravovat a sledovat přes webové rozhraní**, včetně
  konfigurace,
- **AI agenti mají s daty pracovat co nejsnáz**, bez přístupu přes SSH,
- **agent publikuje sám**, bez předchozího schválení,
- pipeline a agenti běží na **správcově PC** (NanoClaw kontejner), web na
  **Hestii**, která dělá denní zálohu.

Pro agenta je přímá editace týdenních JSON nejhorší rozhraní: musí trefit
soubor, udržet kopie vícetýdenních akcí, přesné formátování a manifest. Chybu
zjistí až validátor po zápisu. Dva souběžné běhy si soubory přepisují.

## Rozhodnutí

1. **Jediným zdrojem pravdy je SQLite na serveru.** Patří do ní akce, zdroje,
   kategorie, obce a aliasy, kandidáti, fronta duplicit, inbox, běhy a health.
   Git drží jen kód, testovací fixtures a dokumentaci.
2. **Do databáze zapisuje jen PHP aplikace.** Admin UI i API volají stejnou
   doménovou vrstvu, takže pravidla dat existují v kódu jednou.
3. **Agenti a pipeline jsou klienti HTTP API** s tokenem. Dostanou tenkého
   CLI klienta bez závislostí mimo standardní knihovnu Pythonu. SSH ani přímý
   přístup k databázi nepotřebují.
4. **Agent publikuje sám; pojistky vynucuje server.** API odmítne akci bez
   konkrétního zdroje, obce z číselníku, kategorie ze slovníku nebo platného
   termínu. Duplicity kontroluje server bez ohledu na tvrzení agenta: jistá
   shoda vrátí existující akci, nejistá jde do fronty a nepublikuje se.
5. **Každá změna jde do historie změn** (kdo, který běh, co, stav předtím a
   potom, poznámka). Historie nahrazuje git diff a umožňuje vrátit jednu změnu
   i celý běh agenta.
6. **Admin UI je za přihlášením správce.** Jeden účet; agenti mají vlastní
   tokeny, aby historie rozlišila, kdo co udělal.
7. **Zálohu dělá Hestia jednou denně.** Cron před ní vytvoří konzistentní
   snímek přes `VACUUM INTO`, protože kopie souboru ve WAL režimu uprostřed
   zápisu nemusí jít obnovit.
8. **Lokální stav pipeline na PC není zdroj pravdy.** Snapshoty, ETagy a
   pracovní cache se smějí kdykoli smazat; stojí to jen plné stažení.

## Co se ruší

- Týdenní JSON a manifest jako formát dat (ADR 0001) a export s bajtovým
  roundtripem.
- `research/candidates*.json`, `config/*.json` a `stats/` jako úložiště.
  Po jednorázovém převodu do databáze se z repozitáře odstraní; historie v
  gitu zůstane.
- Statický frontend a GitHub Pages (ADR 0007). Veřejná plocha je jen PHP.
- Validace JSON schématy; nahradí ji kontroly v doménové vrstvě.
- Z ADR 0002: Python jako jediný pisatel a git jako zdroj konfigurace.
- Z `project-vision.md`, kapitoly 13: zákaz administračního rozhraní a
  přihlašování.

Zůstává: SQLite a PHP jako serving vrstva (ADR 0002), deterministické
adaptéry (ADR 0003), health zdrojů (ADR 0004), inbox (ADR 0005), odvozené
zařazení do týdnů (ADR 0006) a pravidla finálních dat z vize, kapitoly 8.
Zákaz publikovat neověřené kandidáty platí dál; mění se jen to, kdo ověřenou
akci publikuje.

## Důsledky

Výhody:

- Agent čte i zapisuje jedním rozhraním a o chybě se dozví hned z odpovědi.
- Stejné operace jsou dostupné správci ve webu i agentovi přes API.
- Odpadá dvojí reprezentace dat, export, roundtrip a druhý frontend.

Nevýhody a rizika:

- **Databáze je jediná kopie.** Bez ověřené zálohy mimo server znamená ztráta
  serveru ztrátu všech dat. Je nutné ověřit, kam Hestia zálohu ukládá, a
  vyzkoušet obnovu.
- **API je veřejné.** Vyžaduje HTTPS, tokeny s možností odvolání a limit
  požadavků.
- **Chybný běh agenta se publikuje hned.** Ošetřuje to kontrola na serveru,
  denní limit publikací na token a vrácení celého běhu z historie.
- **Deduplikace a validace se přesouvají do PHP.** `matching.py` má 166 řádků
  a převod je levný; normalizace českých termínů a adaptéry zůstávají v
  Pythonu na straně klienta.
- **Sběr běží, jen když je PC zapnuté.** Výběr splatných zdrojů vynechané dny
  dožene; monitoring musí upozornit, když poslední běh chybí déle než den.

## Alternativy

- **Soubory v gitu jako zdroj pravdy, SQLite jako index.** Stav, ke kterému
  kód dospěl. Zamítnuto: nepokryje správu přes web a agentům dává nejhorší
  rozhraní pro zápis.
- **Databáze na serveru, agenti přes SSH a CLI.** Zamítnuto správcem; agent by
  potřeboval shell na produkčním serveru.
- **Databáze na PC, deploy nahrává celý soubor.** Zamítnuto: web by nemohl
  zapisovat (admin UI, inbox) a vznikly by dvě kopie pravdy.
- **MCP server místo CLI klienta.** Odloženo; lze ho později postavit nad
  stejným API.

## Stav

Přijato. Etapa 1 je nasazená od 23. 9. 2026 na https://pardubicko.tix.cz;
od té doby jsou data v gitu zmrazená. Další etapy popisuje
`docs/phase-3-plan.md`.
