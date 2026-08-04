# ADR 0005: Inbox pro ručně vložené odkazy

## Kontext

Správce projektu narazí na konkrétní akci nebo na dosud neznámý zdroj mimo naplánovaný běh — typicky na mobilu, na sociální síti nebo v místním zpravodaji. Dnes to znamená otevřít repozitář, ručně upravit JSON a commitnout. To je bariéra, která způsobí, že se nález ztratí.

Požadavek zní: vložit odkaz **mimo git**, bez commitu, a nechat ho zpracovat. Administrační rozhraní se výslovně nepožaduje.

## Rozhodnutí

Zavádí se **inbox** — fronta ručně vložených odkazů v databázi, kterou zpracovává běžná pipeline.

### Ruční odkaz není zkratka k publikaci

Vložený odkaz vstupuje do stejného toku jako každý jiný kandidát: stažení, extrakce, normalizace, deduplikace, **ověření Curatorem**. Přeskakuje pouze discovery, tedy fázi hledání. Nepřeskakuje ověření a nikdy nezapisuje přímo do publikovaných dat.

Tím zůstává v platnosti pravidlo z `docs/project-vision.md`, že do finálních dat se zapisují jen ověřené akce.

### Tři možné výsledky

Zpracování odkazu musí nejprve rozhodnout, co to vlastně je:

- **detail akce** → vzniká kandidát s `discovery_method: "manual-submission"`,
- **výpis nebo kalendář** → nevzniká akce, ale **návrh nového zdroje** k posouzení a případnému doplnění do `config/source-registry.json`,
- **nerozpoznáno** → záznam skončí jako `failed` s důvodem.

Rozlišení výpisu od detailu je podstatné. Bez něj by se z každého vloženého kalendáře stala jedna nesmyslná „akce“ a projekt by přišel o nejcennější typ nálezu, kterým je nový zdroj.

Nerozpoznaný odkaz se **nedohaduje**. Zůstává ve frontě s popsaným důvodem, v souladu s pravidlem, že agent nedomýšlí chybějící údaje.

### Vstupní body

Všechny zapisují tentýž řádek do téže tabulky:

1. **CLI** — `python3 tools/ingest/submit.py <url> [--note "..."]`. Funguje lokálně, bez serveru. Toto je povinný a první implementovaný vstup.
2. **HTTP endpoint** — `POST /api/inbox` chráněný jediným tokenem v proměnné prostředí. Bez účtů, bez registrace, bez administrace. Volitelný doplněk, který dává smysl až se serverem podle ADR 0002.

Klienti nad HTTP endpointem, například zkratka ve sdílecí nabídce telefonu nebo bookmarklet, nejsou součástí projektu. Endpoint je navržen tak, aby je umožnil.

### Bez veřejného příjmu

Endpoint je chráněný tokenem a není určen veřejnosti. Otevřít ho komukoli by znamenalo řešit spam, moderaci a zneužití — tedy přesně tu administraci, která se nepožaduje. Případné veřejné přijímání tipů je samostatné rozhodnutí pro budoucí ADR.

### Idempotence

URL se před uložením normalizuje: odstraní se sledovací parametry (`utm_*`, `fbclid`, `gclid`), sjednotí se schéma a koncové lomítko. Nad normalizovanou podobou je unikátní index. Opakované vložení téhož odkazu nevytvoří druhý záznam; pouze doplní poznámku.

## Důsledky

Výhody:

- Nález se zachytí okamžitě a bez commitu.
- Ruční vstup nevytváří druhou cestu do dat, takže nemůže obejít kontroly kvality.
- Vložené výpisy se stávají systematickým zdrojem růstu registru zdrojů.
- CLI funguje ještě dřív, než vznikne backend.

Nevýhody:

- Vzniká fronta, kterou je nutné vyprazdňovat; neobsloužený inbox je jen jiná forma ztraceného nálezu.
- Klasifikace odkazu je heuristika a bude se mýlit.
- Token je jediný sdílený secret; jeho únik znamená zaplnění fronty.

## Alternativy

- **GitHub issue s formulářem.** Zamítnuto — požadavek zní výslovně mimo git.
- **Veřejný formulář.** Zamítnuto, viz výše.
- **E-mailová schránka jako vstup.** Zamítnuto jako zbytečně složité proti jednomu endpointu.
- **Přímý zápis do `research/candidates*.json`.** Zamítnuto. Je to commit, tedy původní bariéra, a obchází normalizaci.

## Stav

Přijato.
