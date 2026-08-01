# Pardubicko Events

Veřejný statický katalog akcí pro Pardubice, Chrudim a okolí. Web běží bez serveru a databáze jako GitHub Pages a načítá data z týdenních JSON souborů v repozitáři.

## Struktura

- `index.html` – HTML rozhraní bez aplikační logiky
- `styles.css` – responzivní vzhled seznamu, filtrů a kalendáře
- `js/app.js` – orchestrace načtení dat, filtrů a pohledů
- `js/data.js` – načtení manifestu a týdenních JSON souborů
- `js/filters.js` – inicializace a aplikace filtrů
- `js/list.js` – seznamový pohled
- `js/calendar.js` – týdenní kalendář
- `js/format.js` – formátování data, času a popisků
- `data/manifest.json` – seznam dostupných týdnů
- `data/weeks/YYYY-Www.json` – akce pro konkrétní ISO týden
- `config/sources.json` – kurátorovaný seznam zdrojů ke kontrole

## Lokální spuštění

ES moduly a `fetch()` vyžadují HTTP server. V kořeni repozitáře lze použít například:

```bash
python3 -m http.server 8000
```

Potom otevřete `http://localhost:8000/`.

## GitHub Pages

Publikace je určena z větve `main` a kořene repozitáře.

## Datový model

Stav akce se neukládá. Aplikace jej odvozuje z `start_at`, `end_at` a aktuálního času. Explicitně lze uvést pouze `cancelled: true`.
