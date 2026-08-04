# ADR 0004: Sledování zdraví zdrojů

## Kontext

Zdroj přestane dodávat data zpravidla tiše. Nespadne, nevrátí chybu — jen se změní jeho struktura a adaptér začne vracet prázdno nebo neúplné položky. Bez detekce se to projeví až jako nevysvětlitelný pokles pokrytí o několik týdnů později.

Projekt už tento jev doložil. Kanál veřejných facebookových stránek nemá JSON-LD a parsuje se z něj viditelný text; podle `docs/monitoring.md` proto „kanál tiše přestane vracet akce, aniž by cokoli spadlo“. Metrika `facebook_blocks_unparsed` vznikla až poté, co se ukázalo, že u právě probíhajících akcí Facebook místo data píše „Právě probíhá“ a takové akce propadávaly.

Sledovat pouze návratový kód HTTP tento problém nepokryje. Tvrdé selhání je z celé skupiny to nejméně časté a nejsnáze řešitelné.

Zároveň platí opačné riziko: nulová výtěžnost sama o sobě chyba není. `docs/monitoring.md` to už konstatuje pro Facebook — u obcí je očekávaná výtěžnost blízká nule. Monitoring, který na tohle alarmuje, se po týdnu vypne a je k ničemu.

## Rozhodnutí

Projekt zavádí sledování zdraví zdrojů postavené na třech pravidlech.

### 1. Tři nezávislé signály

Stažení, extrakce a čerstvost obsahu se sledují odděleně a nikdy se neslévají do jediného „zdroj nefunguje“.

- **Stažení** — návratový kód, doba odezvy, velikost, hash obsahu.
- **Extrakce** — počet nalezených položek, počet platných položek, míra vyplnění klíčových polí.
- **Čerstvost** — mění se obsah a existují budoucí termíny?

Bez tohoto rozdělení nelze odlišit odpověď 200 s prázdnou stránkou od redesignu a od zdroje, který legitimně nemá program.

### 2. Baseline zdroje proti sobě samému

Prahy jsou relativní k historii konkrétního zdroje, ne globální. Zdroj, který spolehlivě dává čtyřicet akcí a dá nulu, je rozbitý. Zdroj, který dává nula až dvě akce a dá nulu, je v normálním stavu.

Zdroje s nízkým baseline se proto nehodnotí podle objemu vůbec; sleduje se u nich pouze dodržení intervalu kontroly a úspěšnost stažení.

### 3. Karanténa místo mazání

Když zdroj přejde do chybového stavu, jeho akce se nemažou. Zůstávají publikované a expirují až tím, že jim uplyne termín.

Zmizení akce z výpisu není důkazem zrušení. Zrušení se smí odvodit pouze tehdy, je-li zdroj ve stavu `healthy`, akce spadala do staženého okna a chybí ve **dvou po sobě jdoucích** zdravých staženích. Jinak se akce ponechá beze změny.

## Stavy zdroje

- `healthy` — stahování i extrakce odpovídají baseline.
- `suspect` — stažení proběhlo, ale extrakce vrátila nulu proti nenulovému baseline.
- `degraded` — extrakce vrátila výrazně méně položek, než je baseline.
- `schema-drift` — položky jsou, ale klíčová pole se přestala plnit.
- `stale` — obsah se dlouho nemění a neobsahuje budoucí termíny.
- `broken` — opakované selhání stažení.

Konkrétní prahy, jejich zdůvodnění a schéma tabulek jsou v `docs/phase-2-architecture.md`. Prahy jsou počáteční odhad určený ke kalibraci na skutečných datech, ne ověřená konstanta.

## Detekce změn struktury dřív, než se projeví

Vedle běhového sledování se zavádí:

- **Golden fixtures** — ke každému adaptéru je v repozitáři uložený snapshot vstupu a očekávaný výstup. CI je ověřuje při každé změně kódu, takže refaktor adaptér tiše nerozbije.
- **Živý smoke test** — pravidelný běh adaptérů proti reálným zdrojům s porovnáním tvaru výstupu proti fixtures. Zachytí redesign zdroje dřív, než se projeví poklesem dat.

Rozdíl je podstatný: fixtures hlídají regresi **kódu**, smoke test hlídá změnu **zdroje**.

## Role LLM

Přechod zdroje do stavu `suspect`, `degraded` nebo `schema-drift` je spouštěčem opravy. Zde má LLM nejlepší poměr hodnoty k ceně: dostane diff posledního funkčního snapshotu proti aktuálnímu a navrhne úpravu adaptéru. Model tedy neběží při každém sběru, ale jen když se něco skutečně rozbije.

## Důsledky

Výhody:

- Tichá ztráta dat se stává viditelnou a měřitelnou.
- Kontrolní úsilí lze směrovat na zdroje, které to potřebují.
- Vzniká podklad pro rozhodnutí, který zdroj přestat kontrolovat.

Nevýhody:

- Přibývá tabulek a stav, který je nutné udržovat.
- Falešné poplachy jsou nevyhnutelné, dokud se prahy nezkalibrují.
- Ukládání snapshotů zabírá místo; je nutná politika retence.

## Alternativy

- **Jen kontrola HTTP stavu.** Zamítnuto, nepokrývá většinu reálných selhání.
- **Ruční kontrola registru.** Dnešní stav. Neškáluje a `config/facebook-sources.json` ukazuje, jak stav ručně udržovaný v konfiguraci zastarává.
- **Externí monitoring dostupnosti.** Zamítnuto, hlídá dostupnost, nikoli užitečnost obsahu.

## Stav

Přijato.
