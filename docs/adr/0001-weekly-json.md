# ADR 0001: Týdenní JSON soubory jako primární formát první fáze

## Kontext

Projekt potřebuje jednoduchý formát, který lze verzovat na GitHubu, načítat přímo z GitHub Pages a ručně kontrolovat bez backendu nebo databáze.

## Rozhodnutí

Použít `data/manifest.json` a týdenní soubory `data/weeks/YYYY-Www.json` jako primární formát první fáze.

## Důsledky

Výhody:

- jednoduché publikování přes GitHub Pages,
- snadné verzování a audit změn,
- nízká provozní složitost,
- žádná závislost na serveru.

Nevýhody:

- dlouhodobé akce se mohou opakovat ve více týdnech,
- změny jedné akce mohou vyžadovat úpravu více souborů,
- model nebude ideální pro rozsáhlou historii.

## Alternativy

- jeden centrální JSON,
- SQLite,
- serverové API,
- samostatný JSON pro každou akci.

Tyto možnosti jsou odloženy do pozdější fáze, až bude objem dat a potřeba pokročilého vyhledávání dostatečná.

## Pravidlo pro dlouhodobé akce

Jednodenní a běžné vícedenní akce se ukládají do ISO týdne, ve kterém začínají. Dlouhodobá akce se může opakovat ve více dotčených týdenních souborech, pokud je to nutné pro publikovaný rozsah.

Všechny kopie jedné dlouhodobé akce používají stejné stabilní `id` a jsou **shodné ve všech polích kromě `week`**. Frontend je při načtení slučuje podle `id`. Stejné `id` s jakýmkoli jiným rozdílem než v `week` je chyba.

Původní znění vyjmenovávalo šest identitních polí (`title`, `start_at`, `end_at`, `venue`, `municipality`, `source.url`). Výčet se ukázal jako past ze dvou důvodů. Zaprvé se rozejde, jakmile přibude pole. Zadruhé pole mimo výčet — v praxi `description` a `last_verified_at` — tiše rozcházela: kontrola je nepovažovala za chybu, ale slučování podle nich rozlišovalo, takže dvě kopie s odlišným popisem přežily jako dva záznamy pod jedním `id` a zobrazená varianta závisela na pořadí načtení souborů.

Pravidlo má proto jedinou formulaci a jedinou implementaci na každé vrstvě: `js/data.js`, `tools/validate/checks.py` a `tools/pipeline/import_repo.py`.

Poznámka k `last_verified_at`: je to údaj o akci, ne o kopii. Když se kopie ověří v různých okamžicích, sjednocuje se na nejnovější hodnotu.

## Stav

Přijato. Ve fázi 2 částečně nahrazeno ADR 0002: týdenní soubory zůstávají v nezměněném formátu, ale přestávají být zdrojem pravdy a stávají se generovaným exportem. Pravidlo o kopiích dlouhodobých akcí se tím mění z datového modelu na pravidlo exportu.
