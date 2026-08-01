# Pardubicko Events

Veřejný katalog akcí pro Pardubice, Chrudim a okolí.

## Struktura

- `index.html` – jednoduchý frontend pro GitHub Pages
- `app.js` – načítání, filtrování a řazení akcí
- `styles.css` – vzhled aplikace
- `data/manifest.json` – seznam dostupných týdnů
- `data/weeks/YYYY-Www.json` – akce pro konkrétní ISO týden
- `schema/event-schema.json` – JSON Schema pro validaci dat
- `.github/workflows/validate.yml` – kontrola JSON souborů při každém commitu

## GitHub Pages

V nastavení repozitáře zvolte **Settings → Pages → Deploy from a branch → main / root**.

## Datový model

Stav akce se neukládá. Aplikace jej odvozuje z `start_at`, `end_at` a aktuálního času. Explicitně lze uvést pouze `cancelled: true`.
