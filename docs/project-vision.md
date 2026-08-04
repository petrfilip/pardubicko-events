# Vize a provozní filozofie projektu Pardubicko Events

Tento dokument je hlavní přehled záměru, architektury a provozních pravidel projektu `petrfilip/pardubicko-events`. Slouží jako podklad pro další AI agenty, lidské revizory a budoucí správce projektu.

## 1. Účel projektu

Cílem je vytvořit otevřenou, průběžně aktualizovanou a auditovatelnou databázi veřejných akcí pro celý Pardubický a Královéhradecký kraj.

Hlavním produktem jsou strukturovaná, verzovaná a ověřitelná data. Statický
GitHub Pages web je aktuální veřejná a kompatibilní plocha nad auditním
exportem; hotový PHP web je cílová produkční plocha po dokončení bezpečného
nasazení. Přechod a odpovědnosti vymezuje ADR 0007.

Projekt má pokrývat nejen velká města, ale také jednotlivé obce, městyse, místní části, spolky, kluby, farnosti, SDH, sportovní areály, kempy, koupaliště, restaurace, kulturní prostory a jednorázové lokální pořadatele.

## 2. Geografický rozsah

Projekt pokrývá:

- celý Pardubický kraj,
- celý Královéhradecký kraj.

Organizační hierarchie:

- kraj,
- okres,
- obec nebo městys,
- případně místní část nebo konkrétní lokalita.

Kraje a okresy jsou organizační vrstva. Samotné vyhledávání a měření pokrytí musí probíhat až na úroveň jednotlivých obcí a městysů.

Agent nemá hledat jen „v okrese Chrudim“ nebo „v Královéhradeckém kraji“. Musí systematicky rotovat konkrétní obce a pro každou z nich používat více typů dotazů.

Oficiální seznam obcí má vycházet z důvěryhodného veřejného číselníku, ideálně ČSÚ nebo RÚIAN. Ručně psaný seznam nemá být dlouhodobým zdrojem pravdy.

## 3. Základní architektura

Repozitář má čtyři hlavní vrstvy:

### `data/`

Finální, ověřené události používané frontendem.

### `research/`

Pracovní výstupy agentů, kandidáti, úspěšné vyhledávací dotazy, poznatky a metriky objevování.

### `config/`

Dlouhodobá konfigurace: kraje, okresy, obce, registry zdrojů, kategorie a provozní nastavení.

### `stats/`

Metriky kvality, pokrytí, poslední kontroly, úspěšnost zdrojů a pravidelné reporty.

## 4. Agentní model

Projekt používá oddělené role.

### Discovery Agent

Úkolem je maximalizovat počet nalezených kandidátů.

Prochází zejména:

- Google nebo jiný obecný vyhledávač,
- veřejně dostupné Facebook Events,
- Kudy z nudy,
- GoOut,
- ticketingové portály,
- městské a obecní weby,
- lokální organizátory,
- spolky,
- sportovní kluby,
- kulturní instituce,
- restaurace,
- kempy,
- koupaliště,
- areály,
- místní média a agregátory.

Discovery Agent smí být agresivní v objevování, ale nesmí neověřené kandidáty zapisovat přímo do finálních týdenních JSONů.

### Curator Agent

Úkolem je maximalizovat přesnost.

U každého kandidáta ověřuje:

- přesný rok,
- datum,
- čas začátku,
- čas konce, pokud je zveřejněn,
- místo,
- obec,
- vstupné,
- konkrétní zdroj,
- duplicity,
- případné zrušení.

Do `data/weeks/*.json` zapisuje pouze ověřené akce.

### Quality Agent

Kontroluje:

- nefunkční odkazy,
- obecné odkazy místo konkrétních detailů,
- duplicity,
- neplatný JSON,
- špatný rok,
- nesprávné týdenní zařazení,
- `end_at` před `start_at`,
- chybějící povinná pole,
- nekonzistentní kategorie,
- chyby manifestu,
- funkčnost GitHub Pages,
- seznamový a kalendářový pohled.

## 5. Vyhledávací filozofie

Projekt nesmí záviset pouze na pevném seznamu známých webů.

Každý discovery běh má kombinovat:

1. široké objevovací dotazy,
2. dotazy podle data,
3. dotazy podle kategorií,
4. dotazy podle konkrétní obce nebo městyse,
5. vyhledávání na Facebooku,
6. vyhledávání na Kudy z nudy,
7. vyhledávání na ticketingových portálech,
8. pravidelnou kontrolu již známých kvalitních zdrojů.

Důležitá je kombinace obecného hledání a následného ověření na primárním zdroji.

Agent má ukládat vyhledávací vzory, které skutečně vedly k novým relevantním akcím, aby se budoucí běhy zlepšovaly.

## 6. Registr zdrojů a učení

Agent si průběžně buduje vlastní znalostní bázi.

Existující soubory:

- `config/source-registry.json`
- `config/municipalities.json`
- `config/categories.json`
- `config/municipality-aliases.json`
- `research/query-patterns.md`
- `research/findings.md`
- `stats/runs/YYYY-MM/`
- `stats/coverage.json`

Plánované, zatím neexistující:

- `research/discovery-score.json` — skóre zdrojů a dotazů

Definice agentů na plánované soubory odkazují vždy podmínkou „pokud existuje“. Agent je nesmí zakládat s vymyšleným obsahem jen proto, aby existovaly; prázdný soubor metrik je horší než žádný.

Registr zdrojů má uchovávat například:

- identifikátor zdroje,
- název,
- URL,
- typ,
- obec,
- okres,
- kraj,
- kategorie,
- prioritu,
- datum poslední kontroly,
- počet kontrol,
- počet nalezených akcí,
- datum posledního úspěšného nálezu,
- poznámky.

Skóre zdroje nebo dotazu je pomocná provozní metrika, ne důkaz kvality konkrétní akce.

## 7. Pokrytí obcí

Každá obec nebo městys má být v čase pravidelně kontrolována.

Agent má prioritizovat zejména:

- dlouho nekontrolované obce,
- obce s nízkým pokrytím,
- obce s historicky vysokým počtem nových akcí,
- obce před víkendem, svátkem, poutí, posvícením nebo sezónní událostí,
- obce, kde se objevil nový lokální zdroj.

Priorita nemá být pouze statická. Má se vypočítávat podle poslední kontroly, historické úspěšnosti, sezónnosti a aktuálního pokrytí.

Denní běh nemusí zkontrolovat všechny obce. Musí ale používat rotační plán, aby žádná obec dlouhodobě nevypadla.

## 8. Pravidla finálních dat

Každá akce musí mít konkrétní ověřitelný zdroj.

Preferované pořadí:

1. konkrétní oficiální stránka akce,
2. konkrétní stránka pořadatele nebo místa konání,
3. konkrétní veřejný Facebook Event,
4. konkrétní ticketingová stránka,
5. konkrétní stránka města nebo obce,
6. konkrétní stránka Kudy z nudy nebo jiného agregátoru.

Obecná homepage není vhodný zdroj, pokud existuje konkrétnější detail.

Agent nesmí domýšlet:

- datum,
- rok,
- čas,
- cenu,
- konec akce,
- místo,
- GPS,
- popis,
- každoroční opakování.

Pokud není znám konec, použije `end_at: null`.

Pokud je znám pouze den, použije `all_day: true`.

Vícedenní akce se ukládá jako jeden časový rozsah, pokud nejde o samostatné opakované termíny.

Stav `past`, `future` nebo `ongoing` se neukládá; odvozuje se z času. Explicitně lze uložit `cancelled`.

## 9. Týdenní JSON model

Současná první fáze používá:

- `data/manifest.json`,
- `data/weeks/YYYY-Www.json`.

Tento model je záměrně jednoduchý pro GitHub Pages.

Dlouhodobé akce mohou být v první fázi uvedeny ve více týdenních souborech, pokud jsou kopie konzistentní a mají stabilní základ ID.

Pro fázi 2 je rozhodnuto o přechodu na centrální katalog v SQLite; viz ADR 0002. Týdenní soubory tím nezanikají — přestávají být zdrojem pravdy a stávají se generovaným exportem, který slouží jako auditní stopa v gitu a jako regresní test migrace.

## 10. Frontend

Projekt dočasně udržuje dvě frontendové implementace se společnými
publikovanými daty, nikoli dva nezávislé produkty:

- statický GitHub Pages web čte auditní JSON export a do produkčního
  přepnutí zůstává veřejnou kompatibilní plochou,
- PHP web čte SQLite, renderuje obsah a filtry na serveru a je kanonickou
  cílovou produkční plochou.

Obě vrstvy zachovávají seznam, kalendář, fulltext, filtr týdne, obce,
kategorie, cílové skupiny a vstupného, budoucí akce, vícedenní rozsahy a
odkaz na zdroj. PHP navíc poskytuje indexovatelné detailní URL, JSON-LD,
sitemapu, robots, stránkování a soukromý inbox. Přepnutí veřejné URL se smí
udělat až s produkčním serverem, TLS, persistentní databází, zálohou a
ověřeným restore. Podrobný regresní kontrakt je v ADR 0007.

## 11. Automatizace

Aktuálně jsou plánovány:

- denní Discovery Agent,
- denní Curator Agent,
- týdenní Quality Agent.

GitHub repozitář slouží jako sdílená paměť mezi jednotlivými běhy.

Facebook je významný zdroj, ale agent smí pracovat pouze s veřejně dostupnými událostmi a indexovaným obsahem. Nemá obcházet ochrany, přihlášení ani omezení platformy.

Do tohoto rozsahu patří i čtení veřejných stránek pořadatelů, které platforma servíruje nepřihlášenému návštěvníkovi, včetně veřejných seznamů nadcházejících událostí. Podmínkou je, že se nepoužije žádný účet ani session, neklikne se cookie lišta ani přihlašovací dialog a agent se identifikuje vlastním user-agentem. Jakmile by obsah vyžadoval přihlášení nebo obejití ochrany, kanál končí a hlásí to jako blokaci. Provozní pravidla popisuje `docs/agents/facebook-agent.md`.

Facebook nepokrývá malé venkovské pořadatele — ti události nezakládají. Kanál je doplněk pro městské kulturní instituce, ne cesta k pokrytí obcí.

## 12. Metriky kvality

Projekt má postupně měřit:

- počet zkontrolovaných zdrojů,
- počet nových zdrojů,
- počet přidaných akcí,
- počet aktualizovaných akcí,
- počet zamítnutých kandidátů,
- počet zrušených akcí,
- pokrytí podle kraje,
- pokrytí podle okresu,
- pokrytí podle obce,
- dobu od poslední kontroly obce,
- úspěšnost dotazů,
- úspěšnost zdrojů,
- počet nefunkčních odkazů,
- počet duplicit.

Metriky mají pomáhat řídit práci agentů. Nemají nahrazovat faktické ověření konkrétní události.

## 13. Co projekt není

První fáze neobsahuje backend, databázi ani serverovou vrstvu. Zůstává statickým katalogem na GitHub Pages.

Druhá fáze zavedla SQLite, funkční PHP serving vrstvu, konzervativní
deduplikaci, první geografický alias s coverage reportem a produkční stack s
otestovanou zálohou a obnovou. Úplná orchestrace pipeline ještě hotová není a
produkční deploy nebyl doložen na cílovém hostu. Rozsah a zdůvodnění popisují
ADR 0002 až 0007, `docs/phase-2-architecture.md` a
`docs/phase-2-work-packages.md`.

Trvale, tedy ani ve fázi 2, projekt neobsahuje:

- Next.js — zamítnuto v ADR 0002 pro současný rozsah,
- přihlašování a uživatelské účty,
- administrační rozhraní,
- veřejný neověřený příjem tipů od návštěvníků,
- přihlášený nebo neveřejný Facebook scraper,
- automatické publikování neověřených kandidátů,
- AI doporučení uložená ve finálních datech.

Ruční vložení odkazu mimo git je řešeno inboxem podle ADR 0005. Není to administrační rozhraní a neobchází ověření Curatorem.

## 14. Otevřené otázky k nezávislé revizi

Další agent nebo lidský revizor má zejména posoudit:

1. Je rozdělení Discovery / Curator / Quality dostatečné?
2. ~~Je týdenní JSON model vhodný pro současnou fázi?~~ Zodpovězeno: pro fázi 1 ano, pro fázi 2 se stává exportem (ADR 0002).
3. ~~Jak nejlépe reprezentovat dlouhodobé akce bez nekonzistentních kopií?~~ Zodpovězeno: akce je jeden záznam, kopie vznikají exportem a jejich členství v týdnech se odvozuje z termínu (ADR 0006). Identita kopií je sjednocená v ADR 0001.
4. ~~Jak importovat a průběžně aktualizovat úplný oficiální seznam obcí?~~ Zadáno jako balíček P1-6: číselník ČSÚ/RÚIAN do tabulky `municipality`.
5. Jak navrhnout rotační plán kontrol obcí, aby byl efektivní a spravedlivý?
6. Které metriky jsou skutečně užitečné a které by vytvářely zbytečnou administrativu?
7. Jak bezpečně a legálně maximalizovat pokrytí veřejných Facebook Events?
8. ~~Jak validovat konkrétnost a životnost zdrojových odkazů?~~ Zadáno jako balíčky P0-1 (kontrola odkazů v CI) a P1-5 (sledování zdraví zdrojů, ADR 0004).
9. ~~Kdy má smysl přejít z týdenních JSONů na centrální katalog a SQLite?~~ Zodpovězeno v ADR 0002.
10. Jak rozšířit frontend o filtr kraje a okresu bez zbytečné složitosti?

Nově otevřené otázky fáze 2 jsou v závěru `docs/phase-2-architecture.md`.

## 15. Pokyny pro revizního agenta

Revizní agent má nejprve načíst skutečný stav repozitáře a porovnat ho s tímto dokumentem.

Má rozlišit:

- již implementované části,
- částečně implementované části,
- pouze navržené části,
- rozpory mezi dokumentací a kódem,
- rizika kvality dat,
- rizika provozní složitosti.

Výstup revize má obsahovat:

- hlavní silné stránky,
- hlavní nedostatky,
- konkrétní doporučené změny,
- priority P0 / P1 / P2,
- návrh nejbližšího realistického pracovního balíku.
