# ADR 0002: Fáze 2 – SQLite na backendu a PHP jako serving vrstva

## Kontext

Fáze 1 podle ADR 0001 publikuje statické týdenní JSON soubory na GitHub Pages. Model narazil na tři limity:

- **Deduplikace napříč zdroji.** Tatáž akce přichází z městského kalendáře, z Facebooku i z ticketingu. Týdenní soubor nemá kde uchovat vazbu „jedna akce – více zdrojů“. Curator ji dnes řeší úsudkem při každém běhu znovu a výsledek se nikam neukládá.
- **Stav zdrojů nemá kam.** `config/source-registry.json` obsahuje 35 zdrojů a je to čistá statická konfigurace bez jediného stavového pole. `config/facebook-sources.json` naproti tomu už stav míchá do konfigurace (`verified_at`, `upcoming_events_at_check`), udržuje ho ruka agenta a nutně bude zastarávat.
- **Vyhledávání a objem.** Frontend načítá všechny týdny naráz a filtruje v paměti. Při současných 81 akcích a 65 KB je to v pořádku. Při cílovém pokrytí obou krajů to udržitelné není.

Zvažovaly se dvě varianty serving vrstvy: Next.js, nebo PHP se SQLite.

## Rozhodnutí

Fáze 2 zavádí **SQLite jako provozní databázi na backendu** a **PHP jako serving vrstvu**.

- Python pipeline je **jediný pisatel publikovaných dat a provozního stavu**
  do databáze. Úzkou výjimkou je PHP endpoint z ADR 0005, který smí zapisovat
  pouze tokenem chráněné záznamy do inboxu.
- PHP veřejný katalog data **pouze čte** a **renderuje stránky na serveru**.
  Seznam, kalendář i detail akce vznikají v PHP, ne v prohlížeči.
- Databáze běží ve WAL módu; pipeline i web běží **na stejném stroji**.
- Git zůstává zdrojem pravdy pro **konfiguraci** a publikovaným **auditním exportem** dat. Provozním storem není.
- JavaScript se z aplikační logiky stahuje. Zůstává jako progresivní vylepšení nad stránkou, která funguje i bez něj.

## Renderování na serveru, nikoli API

Fáze 1 drží veškerou logiku v prohlížeči: `js/` načte všechny týdny, postaví filtry, seznam i kalendář. To má tři důsledky, které s růstem dat zhoršují situaci.

- **Nic není indexovatelné.** Katalog akcí přitom žije z dotazů typu „co dělat v Litomyšli o víkendu“. Dnes neexistují detailní stránky, sitemapa ani strukturovaná data.
- **Celý objem dat musí do prohlížeče**, protože filtrování probíhá až tam.
- **Logika je duplicitní.** Filtrování, řazení a slučování kopií by po zavedení databáze existovalo dvakrát: v SQL i v JavaScriptu.

Fáze 2 proto překlápí renderování na server. Filtry jsou obyčejný formulář odesílaný metodou GET, výsledek je HTML sestavené z jednoho SQL dotazu, stav aplikace je v URL. To odstraňuje potřebu API jako primárního rozhraní.

JavaScript se nezakazuje. Používá se tam, kde skutečně zlepšuje ovládání — například odeslání filtru bez překreslení celé stránky — a vždy jako doplněk nad funkčním HTML.

Stávající frontend fáze 1 se nemaže. Zůstává jako statický náhled na GitHub
Pages nad exportovanými daty, dokud ho serverová verze nenahradí. Konkrétní
podmínky přepnutí a následnou roli obou vrstev doplňuje ADR 0007: PHP je
cílová veřejná plocha, statický web zůstává kompatibilní regresní reference.

## Zdůvodnění volby PHP proti Next.js

Rozhodující nebyl jazyk, ale objem práce a provozní náklad.

- Serverem renderovaná stránka s JSON-LD je v PHP jeden soubor bez buildu, bez sestavovacího kroku a bez Node runtime. Přesně to, co požadavek na méně JavaScriptu znamená.
- PHP se SQLite běží na nejlevnějším hostingu.
- Next.js je framework pro aplikace řízené JavaScriptem. Použít ho k tomu, aby se JavaScriptu ubralo, je práce navíc proti směru nástroje.
- Next.js navíc vyžaduje perzistentní Node host. Serverless (Vercel) odpadá kvůli ephemerálnímu souborovému systému, který se se souborovou SQLite neslučuje; vyžadoval by Turso/LibSQL nebo Postgres.

Next.js se stane relevantním, až projekt bude potřebovat aplikační frontend s přihlášením nebo personalizací. To v současném rozsahu neplatí; administrační rozhraní není požadováno.

## Důsledky

Výhody:

- Jedno místo pro identitu akce, vazby na zdroje a stav sběru.
- Fulltext přes FTS5 s korektním chováním pro češtinu. Ověřeno: tokenizer `unicode61 remove_diacritics 2` najde „řidič“ dotazem „ridic“.
- Indexovatelné stránky, sitemapa a strukturovaná data.
- Stránky fungují i bez JavaScriptu; logika filtrování existuje jen jednou, v SQL.
- Zachovaná auditní stopa v gitu.

Nevýhody:

- Přibývá server, deploy a zálohování. Nulová provozní složitost fáze 1 končí.
- Pipeline i web musí sdílet stroj, jinak je nutné přenášet hotový soubor databáze.
- Data existují ve dvou reprezentacích (databáze a git export), které se musí držet v souladu.

Rizika a jejich ošetření:

- **Rozejití databáze a gitu.** Export je jednosměrný, generovaný a testovaný; ruční editace exportu je zakázaná.
- **Souběžný zápis.** Pisatel je právě jeden proces. Pokud by pipeline někdy běžela mimo webový stroj, databáze se přenáší jako celý soubor atomicky (zápis do dočasného souboru a `mv`), nikdy se do ní nepíše po síti.

## Alternativy

- **Statický build bez backendu.** Vygenerovat ze SQLite předrenderované stránky a hostovat je dál na GitHub Pages. Provozně nejlevnější a pro současný objem dostačující; SEO by řešilo stejně dobře. Zamítnuto, protože nepokrývá ruční vkládání odkazů mimo git (ADR 0005) a při každé změně dat vyžaduje přegenerování celého webu.
- **Next.js s Turso nebo Postgres.** Zamítnuto pro tento rozsah, viz výše.
- **Postgres.** Zamítnuto jako neúměrné objemu dat; přináší další službu k provozu bez užitku.
- **Hostovaný vyhledávač (Meilisearch, Typesense).** Zamítnuto. FTS5 na tomto objemu stačí a nepřidává další běžící službu.

## Vztah k ADR 0001

ADR 0001 zůstává platný jako popis fáze 1 a jako **formát exportu**. Týdenní soubory nezanikají; přestávají být zdrojem pravdy a stávají se generovaným výstupem. Pravidlo o kopiích dlouhodobých akcí ve více týdnech se tím mění na detail exportu, ne na datový model.

## Stav

Přijato.
