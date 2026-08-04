# ADR 0003: Deterministické adaptéry jako primární kanál sběru

## Kontext

Vize předpokládá rotační pokrytí všech obcí Pardubického a Královéhradeckého kraje, tedy zhruba devíti set lokalit. Současný sběr stojí na LLM agentovi, který pro jednotlivé obce provádí vyhledávací dotazy a čte nalezené stránky.

Doložený stav: 81 ověřených akcí v šesti týdenních souborech, tedy řádově třináct akcí na týden pro dva kraje. To je pod hranicí, na které má katalog uživatelskou hodnotu proti existujícím agregátorům.

Tři problémy tohoto přístupu:

- **Cena.** Každá obec znamená několik dotazů a načtení několika stránek modelem. Denní běh přes rotační plán je opakující se náklad, který roste lineárně s pokrytím.
- **Nereprodukovatelnost.** Dva běhy nad stejnou obcí vrátí různý výsledek. Nelze rozlišit „zdroj nic nemá“ od „agent to tentokrát nenašel“, a tím pádem nelze měřit pokrytí.
- **Nevyužitá struktura.** Značná část zdrojů publikuje strukturovaná data: `schema.org/Event` v JSON-LD, RSS, iCal. České obecní weby navíc běží na omezeném počtu redakčních systémů s předvídatelnou strukturou kalendáře. Tato struktura se dnes zahazuje a čte se místo ní vykreslený text.

## Rozhodnutí

Primárním kanálem rutinního sběru se stávají **deterministické adaptéry**. LLM se přesouvá na úlohy, kde má skutečnou výhodu.

Adaptér je kód, který pro daný zdroj provede: stažení, extrakci položek a převod do jednotného tvaru. Nic nevyhodnocuje a nic nedomýšlí. Pořadí preferencí při psaní adaptéru:

1. `schema.org/Event` v JSON-LD nebo mikrodatech,
2. iCal nebo RSS feed,
3. veřejné API redakčního systému, pokud existuje,
4. parsování HTML podle stabilních struktur,
5. parsování vykresleného textu — až jako poslední možnost.

Role LLM po tomto rozhodnutí:

- **normalizace nejednoznačných vstupů**, které deterministický parser odmítl (zejména volně psané české termíny a ceny),
- **rozhodnutí sporných duplicit** ve středním pásmu podobnosti,
- **oprava rozbitých adaptérů** na základě diffu proti poslední funkční verzi (viz ADR 0004),
- **objevování nových zdrojů** volným vyhledáváním.

Zásadní rozlišení: volné vyhledávání zůstává legitimním nástrojem pro hledání **nových zdrojů**, nikoli pro rutinní sběr **jednotlivých akcí** ze zdrojů, které už jsou známé. Jakmile je zdroj v registru, sbírá se z něj adaptérem.

## Důsledky

Výhody:

- Náklad rutinního běhu klesá o řády a přestává růst s počtem obcí.
- Běh je reprodukovatelný, takže nulová výtěžnost je měřitelný fakt, ne šum.
- Vzniká podmínka pro sledování zdraví zdrojů (ADR 0004), které nad nedeterministickým sběrem nedává smysl.
- Syrové snapshoty umožňují přeextrahovat data bez opakovaného zatěžování cizích webů.

Nevýhody:

- Adaptéry je nutné psát a udržovat. Redesign zdroje je rozbije.
- Počáteční investice je vyšší než napsat prompt.
- Zdroje bez jakékoli struktury zůstanou obtížné; u nich se LLM extrakce ponechává.

Ošetření hlavní nevýhody: golden fixtures v repozitáři a testy adaptérů v CI, doplněné pravidelným živým smoke testem. Podrobnosti v `docs/phase-2-architecture.md`.

## Doložený precedens v projektu

Nástroj `tools/fb-events/` je přesně tento vzor: deterministický sběr s vlastními testy (`test_parse.py`), který podle vlastní dokumentace „pouze načítá a strukturuje, nic nevyhodnocuje“ a nikdy nezapisuje do `data/weeks/`. Rozhodnutí toto uspořádání zobecňuje na ostatní zdroje.

Zkušenost z tohoto kanálu zároveň dokládá riziko posledního bodu preferencí: Facebook nemá JSON-LD, parsuje se z něj viditelný text, a změna formuly „Právě probíhá“ způsobila tiché propadávání akcí. Proto vznikla metrika `facebook_blocks_unparsed`. Text jako vstup je krajní řešení a vyžaduje explicitní metriku nenaparsovaných bloků.

## Alternativy

- **Ponechat LLM discovery jako primární kanál.** Zamítnuto kvůli ceně a neměřitelnosti.
- **Jen adaptéry, bez LLM.** Zamítnuto. Malí pořadatelé publikují nestrukturovaně a objevování nových zdrojů je úloha, kde vyhledávání a model dávají smysl.
- **Cizí agregátor jako jediný zdroj.** Zamítnuto. Právě malé obce, které agregátory nepokrývají, jsou důvodem existence projektu.

## Stav

Přijato.
