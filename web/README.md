# PHP web fáze 2

Serverem renderovaná aplikace nad SQLite je hotová. Veškerý obsah, filtrování
i stránkování fungují bez JavaScriptu; filtry se vyhodnocují pouze v SQL a
jejich stav zůstává v URL.

## Spuštění

```bash
python3 tools/pipeline/pipeline.py import
docker compose up app
```

Aplikace pak běží na `http://localhost:8081`. Vývojový inbox token z
`docker-compose.yml` je `dev-token`; v provozu se musí změnit proměnnou
`PARDUBICKO_INBOX_TOKEN`. Cestu k databázi a veřejnou základní URL řídí
`PARDUBICKO_DB` a `PARDUBICKO_BASE_URL`.

## Rozhraní

- `GET /`, `/hledat`, `/obec/{slug}`, `/kalendar/{YYYY-Www}` — SQL výpisy,
  GET filtry a stránkování,
- `GET /akce/{id}` — detail s JSON-LD; staré `/?event={id}` vrací 301,
- `GET /sitemap.xml`, `/robots.txt`,
- `POST /api/inbox` — soukromý vstup podle ADR 0005, Bearer token,
- `GET /api/health` — provozní přehled zdrojů.

`web/public` obsahuje front controller, router a CSS; `web/templates` drží
serverové šablony. `Application` skládá routy a odpovědi, zatímco veškeré
dotazy a filtrování zůstávají v `EventRepository`.

## Ověření

Úplná lokální sada používá Node i PHP a HTTP smoke spouští nad novou dočasnou
SQLite databází:

```bash
docker compose run --rm --build tests
```

Samotný PHP integrační test lze spustit příkazem
`php web/tests/test_web.php` v prostředí s rozšířením `pdo_sqlite`.

`web/tests/http_smoke.php` ověřuje stejný kontrakt přes skutečný PHP server:
přehled, assety, hledání, obec, kalendář, detail a JSON-LD, legacy redirect,
sitemap, robots, health a inbox odpovědi 401/202/200. Smoke test vždy používá
oddělenou dočasnou databázi, aby neznečistil provozní inbox.
