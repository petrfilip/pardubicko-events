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

## Stav

Přijato.
