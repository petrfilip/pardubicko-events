# ADR 0007: PHP je cílová veřejná plocha, statický web zůstává reference

## Kontext

Repozitář obsahuje dvě funkční frontendové vrstvy nad stejnými publikovanými
akcemi:

- statický GitHub Pages web načítá `data/manifest.json`, týdenní JSON exporty
  a `config/categories.json`,
- PHP web čte SQLite a serverem renderuje seznam, kalendář, hledání, obec,
  detail, sitemapu, robots a JSON-LD; poskytuje také neveřejný inbox a health
  API.

ADR 0002 určil PHP jako serving vrstvu fáze 2 a statický web ponechal do jeho
nahrazení. PHP implementace je hotová a testovaná. Produkční stack v
`docker-compose.production.yml` už používá PHP-FPM, Nginx, TLS, persistentní
volumes, chráněný inbox a testované backup/restore nástroje. Nasazení a HTTP
smoke na cílovém hostu však ještě nejsou doložené; vývojový
`docker-compose.yml` nadále používá `php -S`.

## Rozhodnutí

**Kanonickou cílovou veřejnou plochou je PHP web.** Veřejnou URL převezme až
po dokončení produkčního nasazení a doloženém smoke a restore testu.

Do té doby zůstává veřejnou plochou statický GitHub Pages web. Nejde o druhý
produkt ani o alternativní zdroj pravdy: je to kompatibilní čtenář auditního
JSON exportu a bezpečný fallback bez serverového provozu.

Po přepnutí se statický frontend nemaže. Zůstane referenční regresní vrstvou,
která prokazuje použitelnost exportu nezávisle na SQLite a PHP. Jeho další
veřejná adresa nebo případné pozdější ukončení vyžaduje samostatné doložené
rozhodnutí; toto ADR žádný frontend nemaže.

## Odpovědnosti a kompatibilita

PHP je po přepnutí autoritativní pro veřejné HTML, kanonické URL, detailní
stránky, SEO metadata, sitemapu, robots, stránkování, SQL filtrování, health a
tokenem chráněný inbox.

Statická vrstva je autoritativní pouze jako kontrola auditního exportu. Musí
dál načíst aktuální manifest a týdny, zobrazit seznam a kalendář, filtrovat
kanonická ID kategorií a cílových skupin a otevřít legacy `?event={id}` odkaz.
Nesmí zavést odlišný význam publikovaných polí.

Regresní kompatibilita znamená, že pro stejný export a databázi obě vrstvy:

- zobrazí stejnou množinu publikovaných akcí v daném týdnu,
- používají stejné kanonické kategorie a české popisky,
- shodně zobrazí název, termín, obec, cenu, zrušení a zdroj,
- zachovají starý odkaz `?event={id}`; PHP jej přesměruje na `/akce/{id}`.

Nemusí mít shodné HTML, CSS, pořadí ovládacích prvků ani URL filtrů. Veřejné
kanonické URL po přepnutí vlastní PHP.

## Podmínky přepnutí

Přepnutí veřejné URL je provozní krok a smí nastat teprve, když:

1. produkce nepoužívá vestavěný `php -S`,
2. je nastavené HTTPS a produkční `PARDUBICKO_BASE_URL`,
3. SQLite a snapshoty mají persistentní úložiště,
4. existuje automatizovaná záloha s retencí a úspěšný restore test,
5. inbox má neveřejný token, body limit, rate limiting a logování,
6. HTTP smoke projde nad produkční konfigurací,
7. je připraven rollback veřejné URL na statickou vrstvu.

Po přepnutí se upraví dokumentace skutečné veřejné adresy. Do splnění těchto
podmínek se PHP označuje jako cílová produkční plocha, nikoli jako nasazená
produkce.

## Důsledky

- Nevzniká dlouhodobá nejasnost, který frontend vlastní veřejné URL.
- Statická vrstva zůstává levným fallbackem a testem bezeztrátového exportu.
- Dočasně se udržují dvě zobrazovací vrstvy; datová kompatibilita proto musí
  být součástí regresních kontrol.
- Produkční přepnutí je závislé na provozním balíčku, ne jen na dokončeném
  PHP kódu.

## Stav

Přijato. Rozhodnutí je účinné; produkční přepnutí zatím neproběhlo.
