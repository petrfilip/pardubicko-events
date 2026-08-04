# Produkční runbook

Cílová produkční plocha je podle ADR 0007 PHP web za Nginxem. Dokud nebylo
nasazení ověřeno na cílovém hostu a přepnuta veřejná URL, zůstává veřejnou
plochou statický GitHub Pages web. `docker-compose.yml` je jen pro vývoj;
produkční konfigurace používá `docker-compose.production.yml`, PHP-FPM a
persistentní volume pro SQLite, snapshoty a zálohy. GitHub Actions nic
neplánují.

## Předpoklady a secrets

Na jednom hostu je Docker Compose a platný TLS certifikát. Secrets patří do
lokálního `.env`, který je ignorovaný gitem:

```dotenv
PARDUBICKO_BASE_URL=https://akce.example.cz
PARDUBICKO_INBOX_TOKEN=<alespoň 32 náhodných bajtů>
PARDUBICKO_TLS_CERT=/etc/letsencrypt/live/akce.example.cz/fullchain.pem
PARDUBICKO_TLS_KEY=/etc/letsencrypt/live/akce.example.cz/privkey.pem
```

Nginx přesměruje HTTP na HTTPS, přijme nejvýše 32 kB request body a omezuje
`POST /api/inbox` na pět požadavků za minutu na IP s malým burstem. Token se
nikdy nezapisuje do repozitáře ani logu.

## První start a upgrade

```bash
docker compose -f docker-compose.production.yml --profile ops build
docker compose -f docker-compose.production.yml --profile ops run --rm pipeline \
  python3 tools/pipeline/pipeline.py --database /data/pardubicko.db import
docker compose -f docker-compose.production.yml up -d app nginx
curl --fail https://akce.example.cz/api/health
```

Při upgradu nejprve vytvoř zálohu, stáhni/checkoutni ověřený commit, sestav
obrazy a spusť import/migraci. Pak `up -d`; named volume se při redeployi
nesmaže. Při běžném `down` nikdy nepoužívej `--volumes`.

## Pipeline a plánování

Pipeline plánuje hostitelský cron, ne GitHub Actions. Hotový dávkový runner
spouštěj jednou denně například:

```cron
17 4 * * * cd /srv/pardubicko-events && docker compose -f docker-compose.production.yml --profile ops run --rm pipeline python3 tools/pipeline/run.py --due >>/var/log/pardubicko-pipeline.log 2>&1
```

Cron zapni až po ověření offline běhu a zálohy na cílovém hostu. Log rotuje
hostitelský `logrotate` (denně, 14 souborů, `copytruncate`). Nginx log
je v named volume `nginx-logs`; jeho rotaci nastav stejnou politikou přes
Docker logging driver nebo hostitelský sběr logů.

## Záloha a test obnovy

Denní cron používá konzistentní SQLite kopii přes `VACUUM INTO`, checksum a
14denní retenci:

```cron
42 3 * * * cd /srv/pardubicko-events && docker compose -f docker-compose.production.yml --profile ops run --rm pipeline python3 tools/ops/backup.py
```

Ověření a obnova se dělají explicitně:

```bash
docker compose -f docker-compose.production.yml --profile ops run --rm pipeline \
  python3 tools/ops/restore.py /backups/pardubicko-YYYYMMDDTHHMMSSZ.sqlite3 --verify-only
docker compose -f docker-compose.production.yml stop app nginx
docker compose -f docker-compose.production.yml --profile ops run --rm pipeline \
  python3 tools/ops/restore.py /backups/pardubicko-YYYYMMDDTHHMMSSZ.sqlite3 \
  --target /data/pardubicko.db --replace
docker compose -f docker-compose.production.yml up -d app nginx
```

Po obnově musí projít `/api/health`, počet akcí a náhodný detail. Restore se
nejméně měsíčně zkouší do vedlejší cesty bez přepsání produkce.

## Dohled, místo na disku a rollback

- sleduj `/api/health`, stav kontejnerů a volné místo volume `database`,
  `snapshots`, `backups` a `nginx-logs`;
- upozorni při méně než 20 % nebo 5 GB volného místa (platí nižší práh);
- snapshot retention drží posledních pět obsahů na zdroj a poslední úspěšnou
  extrakci; po prvním měsíci porovnej skutečný růst s kapacitou disku;
- rollback kódu znamená checkout předchozího ověřeného commitu a rebuild;
  rollback schématu znamená nejdřív zastavit zapisovatele a obnovit odpovídající
  zálohu. JSON export v `data/` zůstává auditní a recovery referencí.
