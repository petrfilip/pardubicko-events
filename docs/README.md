# Pardubicko Events – Documentation Index

Tento dokument je vstupním bodem pro člověka i AI agenty.

## Pořadí čtení

1. project-vision.md – cíle, architektura a filozofie projektu.
2. adr/ – závazná architektonická rozhodnutí.
3. phase-2-architecture.md – návrh druhé fáze: pipeline, datový model, zdraví zdrojů, inbox.
4. phase-2-work-packages.md – rozpad druhé fáze do zadatelných balíčků s kritérii přijetí.
5. monitoring.md – jak se měří běhy agentů.
6. agents/ – role Planner, Discovery, Curator a Quality Agentů.
7. config/ – geografické pokrytí a discovery policy.
8. benchmarks/ – referenční sady pro testování Discovery.
9. data/ – produkční ověřená data.
10. research/ – pracovní poznámky a kandidáti.
11. stats/ – metriky a reporty.

## Fáze projektu

**Fáze 1 – implementovaná.** Statický katalog na GitHub Pages nad týdenními JSON soubory. Popisuje ji ADR 0001.

**Fáze 2 – částečně implementovaná.** Hotový je validační základ, SQLite
schéma s bajtově shodným importem/exportem, číselník 899 obcí, řízený
slovník 18 kategorií a 124 aliasů, fetch/snapshot vrstva, první tři adaptéry,
manuální inbox, sledování zdraví zdrojů a serverem renderovaný PHP web.
PHP web obsahuje GET filtry a stránkování, detail, obec, kalendář, hledání,
sitemapu, robots, JSON-LD, legacy redirect, inbox API a health API.

Hotová je také P2-2 deduplikace s konzervativními pásmy a review frontou,
doložený alias `Janderov → Chrudim` s coverage reportem a produkční stack
PHP-FPM + Nginx včetně zálohy a restore testu. Prahy deduplikace jsou zatím
kalibrované jen na malém korpusu a produkční deploy nebyl doložen na cílovém
hostu. Dávková pipeline i P2-4 živý smoke jsou implementované; nové kandidáty
stále publikuje až Curator. Přesný stav balíčků uvádí
`phase-2-work-packages.md`; cílový stav od skutečnosti výslovně
odděluje `phase-2-architecture.md`.

**Veřejné frontendové plochy.** Do dokončení produkčního deploye zůstává
veřejnou plochou statický GitHub Pages web nad auditním JSON exportem. PHP
web je kanonická cílová produkční plocha a převezme veřejnou URL až po
splnění provozních podmínek. Statický web se nemaže; po přepnutí zůstane
kompatibilní referenční vrstvou. Podrobnosti a regresní kontrakt stanoví
ADR 0007.

## Přehled ADR

| ADR | Rozhodnutí | Stav |
|---|---|---|
| 0001 | Týdenní JSON soubory jako formát první fáze | Přijato; ve fázi 2 se mění na formát exportu |
| 0002 | SQLite na backendu a PHP jako serving vrstva | Přijato |
| 0003 | Deterministické adaptéry jako primární kanál sběru | Přijato |
| 0004 | Sledování zdraví zdrojů | Přijato |
| 0005 | Inbox pro ručně vložené odkazy | Přijato |
| 0006 | Zařazení akce do týdnů se odvozuje z termínu | Přijato, zavedení odloženo |
| 0007 | PHP jako cílová veřejná plocha, statický web jako kompatibilní reference | Přijato; přepnutí čeká na produkční deploy |

## Pravidla

- Repozitář je hlavním zdrojem pravdy.
- Dokumentace má přednost před obsahem chatu.
- Každé významné architektonické rozhodnutí má být zaznamenáno jako ADR.
- Dokumentace nesmí odkazovat na soubory a chování, které neexistují. Návrh se od implementovaného stavu odlišuje explicitně.
- Konfiguraci vlastní git a mění ji člověk. Provozní stav vlastní databáze a píše ho pipeline. Tyto dvě věci se nemíchají do jednoho souboru.
