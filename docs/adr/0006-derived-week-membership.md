# ADR 0006: Zařazení akce do týdnů se odvozuje z termínu

## Kontext

Podle ADR 0001 se dlouhodobá akce „**může** opakovat ve více dotčených týdenních souborech, **pokud je to nutné** pro publikovaný rozsah“. Je to úsudkové pravidlo: kurátor rozhoduje, do kterých týdnů akci zapíše.

Nezávislá revize modelu ukázala tři věci, které to rozhodnutí obracejí.

**Frontend členství už dnes odvozuje z termínu, nikoli z pole `week`.** V `js/filters.js` se při vybraném týdnu z manifestu filtruje přes `eventOverlapsWeek`, tedy překryv termínu s rozsahem týdne. Větev pracující s `_week_ids` se pro týdny z manifestu nikdy nepoužije. Totéž platí pro kalendář. Explicitní zápis do souboru tedy neřídí nic, co uživatel vidí — řídí jen to, kde je záznam uložený.

**Data a zobrazení si proto odporují.** Bienále běží od 12. června do 26. září, ale je zapsané jen ve W32 až W34. Uživatel ho přesto vidí i v ostatních týdnech, protože se odvozuje z termínu. Zápis je neúplný, aniž by to bylo poznat.

**Úsudkové pravidlo vyrobilo nekonzistence.** Volnost „pokud je to nutné“ vedla k tomu, že táž akce existovala pod dvěma `id` s odlišným názvem, časem konce i popisem (`sportovni-park-pardubice-2026` a `…-2026-w31`). Objem přitom argumentem není: dokompletování všech překryvných kopií znamená ve stávajících šesti týdnech nárůst z 81 na zhruba 91 řádků.

## Rozhodnutí

Členství akce v týdenním souboru se **odvozuje**, nerozhoduje.

Akce patří do týdne `W`, právě když se rozsah `[date(start_at), date(end_at ?? start_at)]` překrývá s `[W.from, W.to]` a akce má `status = 'published'`.

Důsledky pravidla:

- Akce se opakuje ve **všech** publikovaných týdnech, do kterých zasahuje. Ne v některých.
- **Pořadí akcí v souboru se odvozuje také**, podle `start_at`, `title`, `id`. Jinak by zůstal ručně udržovaný údaj se stejnou vadou jako ručně udržované členství.
- Tabulka `event_week` zůstává, ale mění roli: je to **materializovaný odvozený index**, plněný při publikaci. Není zdrojem pravdy.
- Rozsah publikovaných dat řídí okno v manifestu, ne úsudek kurátora nad jednotlivou akcí.

Formulace v ADR 0001 se mění z povolující („může se opakovat, pokud je to nutné“) na mechanickou („opakuje se ve všech publikovaných týdnech, do kterých zasahuje“).

## Co se odkládá

Únikový ventil pro případ, kdy by akce do některého týdne patřit neměla, se **nestaví**. Kdyby reálný případ vznikl, řeší se jako `event_week.suppressed` s povinným důvodem — tedy výslovné vyjmutí, nikdy výslovné zařazení. Dokud takový případ není doložený, je to zbytečná složitost.

## Důsledky

Výhody:

- Zaniká druhá ručně udržovaná pravda o něčem vypočitatelném.
- Data přestanou odporovat tomu, co uživatel vidí.
- Mizí prostor pro nekonzistence typu dvou `id` pro jednu akci.
- Kurátorovi ubývá rozhodnutí, které stejně neuměl dělat konzistentně.

Nevýhody a rizika:

- **Jednorázový normalizační diff je velký.** Žádný ze šesti týdenních souborů dnes není seřazený podle `start_at`, takže odvozené pořadí přepíše všechny. Přibude zhruba deset kopií.
- **Kritérium přijetí P1-1 se tím mění.** Dnes platí, že import a export vrátí soubory bajtově shodné, a to je ověřené. Po normalizaci bude platit ve tvaru „kolotoč sedí po jednom doloženém normalizačním kroku“. Ten krok musí být přezkoumatelný a oddělený od jakékoli jiné změny.
- Při širším publikovaném okně spadne roční výstava do mnoha souborů. Je to ohraničené velikostí okna a bajtově zanedbatelné; kdyby to vadilo, řeší se politikou okna, ne ruční kurací.

## Postup zavedení

Pořadí je závazné, protože chrání ověřitelnost:

1. Doplnit do validátoru kontrolu úplnosti členství jako **varování** (zobecnění stávající `check_starting_week_present` na všechny překryvné týdny).
2. Provést normalizaci dat jako **samostatný krok, který nemění nic jiného**: dokompletovat kopie a přeuspořádat soubory.
3. Ověřit, že kolotoč po normalizaci opět sedí bajtově.
4. Teprve pak přepnout `event_week` na odvozený index a povýšit kontrolu z varování na chybu.

Dokud neproběhne krok 2, zůstává `event_week` plněný explicitně z dat, aby byl export bezeztrátový.

## Stav

Přijato, zavedení odloženo. Kroky 1 až 4 nejsou provedené.

Uzavírá otevřenou otázku 4 v `docs/phase-2-architecture.md` a otázku 3 v `docs/project-vision.md`.
