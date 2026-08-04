# Pardubicko Events

Katalog akcí pro Pardubický a Královéhradecký kraj. Aktuální veřejná plocha
je statický GitHub Pages web nad týdenními JSON soubory. V repozitáři je
zároveň hotový serverem renderovaný PHP web nad SQLite, který je cílovou
produkční plochou po dokončení produkčního nasazení. Rozdělení odpovědností
popisuje [`docs/adr/0007-public-frontend.md`](docs/adr/0007-public-frontend.md).

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
- `config/source-registry.json` – jediný kurátorovaný registr zdrojů ke kontrole
- `config/facebook-sources.json` – veřejné facebookové stránky pořadatelů pro discovery kanál
- `config/categories.json` – řízený slovník kategorií a mapování aliasů (balíček P2-3)
- `tools/fb-events/` – deterministický sběr veřejných událostí z Facebooku (mimo web, viz `docs/agents/facebook-agent.md`)
- `tools/validate/` – deterministická kontrola dat, spouštěná i v CI (viz `tools/validate/README.md`)
- `tools/pipeline/` – SQLite schéma, import/export, fetch, adaptéry a health
- `web/` – serverem renderovaný PHP web fáze 2 (viz `web/README.md`)

## Lokální spuštění

Doporučeně přes Docker. Prostředí obsahuje web i Python nástroje:

```bash
docker compose up web                 # statický web na http://localhost:8080
python3 tools/pipeline/pipeline.py import
docker compose up app                 # PHP web na http://localhost:8081
docker compose run --rm validate      # kontrola dat
docker compose run --rm --build tests # Python, Node, PHP, HTTP smoke, validace a roundtrip
docker compose run --rm linkcheck     # dostupnost zdrojových odkazů
```

Bez Dockeru stačí pro statický web libovolný HTTP server, protože ES moduly a `fetch()` neběží nad `file://`:

```bash
python3 -m http.server 8000
```

## Kontrola dat

Struktura a vztahy v datech se kontrolují strojově. GitHub Actions workflow se
spouštějí pouze ručně; lokální postup je v `tools/validate/README.md`.

## Veřejný frontend

GitHub Pages publikuje statický kompatibilní web z větve `main` a kořene
repozitáře. Dokud není PHP web nasazen produkčně se zálohováním, TLS a
monitoringem, zůstává tato statická varianta veřejnou plochou. PHP web se po
splnění těchto podmínek stane kanonickou veřejnou plochou; statická varianta
zůstane referenční regresní vrstvou nad auditním exportem.

## Datový model

Stav akce se neukládá. Aplikace jej odvozuje z `start_at`, `end_at` a aktuálního času. Explicitně lze uvést pouze `cancelled: true`.

## Licence

Kód je pod licencí MIT, data pod [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/deed.cs). Data smíte použít i komerčně, pokud uvedete zdroj a odvozenou databázi budete šířit pod stejnou licencí. Podrobnosti včetně poznámky ke zdrojům jsou v souboru [`LICENSE`](LICENSE).
