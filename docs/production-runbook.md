# Produkční runbook

Podle ADR 0008 běží web, API a databáze na Hestii (`tix.cz`, uživatel
`mujfibi`, PHP 8.3-FPM) stejně jako splitt. Databáze na serveru je jediným
zdrojem pravdy; do repozitáře se nic neexportuje. Docker stack z fáze 2
(`docker-compose.production.yml`, `deploy/`) se na Hestii nepoužívá a odstraní
se v etapě 2.

Ověřeno na serveru 23. 9. 2026: `php8.3` má `pdo_sqlite` s FTS5 (SQLite
3.45.1, hledání „ridic“ najde „řidič“), `mbstring` i `intl`.

## Rozložení

```
/home/mujfibi/web/pardubicko.tix.cz/
  public_html/index.php        skořápka: PARDUBICKO_DB, PARDUBICKO_BASE_URL, require kódu
  public_html/.htaccess        přepis na index.php, předání hlavičky Authorization
  public_html/assets/          CSS
  private/pardubicko/web/      kód
  private/pardubicko/data/     pardubicko.db (+ -wal, -shm), jen mujfibi
  private/pardubicko.predchozi předchozí verze kódu pro rollback
  private/zalohy/              snímky VACUUM INTO, 14 dní
```

## První nasazení

1. Doména `pardubicko.tix.cz` je v Hestii od 23. 9. 2026: výchozí šablony
   jako splitt, bez aliasu `www` (wildcard DNS `*.tix.cz` na něj nesahá),
   Let's Encrypt, vynucené HTTPS a HSTS.
2. Lokálně připravit databázi z aktuálního gitu:
   `bin/prepare-initial-db` → `var/phase3/prevod.db`. Skript odmítne běžet,
   pokud `data/`, `research/` nebo `config/` mají necommitnuté změny.
3. `bin/deploy --initial-db var/phase3/prevod.db`.
   Na serveru, kde už databáze je, `--initial-db` odmítne.
4. Vytvořit token pro každého agenta. Token se ukáže jen jednou; uloží se do
   konfigurace NanoClaw skupiny, ne do repozitáře:
   ```sh
   ssh tix.cz 'su -s /bin/sh mujfibi -c "PARDUBICKO_DB=/home/mujfibi/web/pardubicko.tix.cz/private/pardubicko/data/pardubicko.db \
     php8.3 /home/mujfibi/web/pardubicko.tix.cz/private/pardubicko/web/bin/pardubicko token:create nanoclaw-curator 50"'
   ```
5. Cron na denní snímek před zálohou Hestie (ta běží v 5:10):
   ```sh
   v-add-cron-job mujfibi 50 4 '*' '*' '*' \
     'PARDUBICKO_DB=/home/mujfibi/web/pardubicko.tix.cz/private/pardubicko/data/pardubicko.db php8.3 /home/mujfibi/web/pardubicko.tix.cz/private/pardubicko/web/bin/pardubicko snapshot /home/mujfibi/web/pardubicko.tix.cz/private/zalohy 14 >/dev/null'
   ```
   Chyba snímku jde na stderr a cron ji pošle mailem.

## Další nasazení

`bin/deploy`. Skript pustí testy, sestaví artefakt,
na serveru udělá snímek živé databáze, zkusí migrace nanečisto na jeho kopii,
prohodí adresáře a doběhne migrace pod `mujfibi`. Na konci zkontroluje web,
`/api/health` a že `/api/v1/me` bez tokenu vrací 401.

Nasazení prohazuje adresáře, ne symlink, takže opcache změnu pozná podle času
souborů a reload PHP-FPM není potřeba.

## Záloha a obnova

- **Hestia zálohuje jen lokálně** (`BACKUP_SYSTEM='local'`, zjištěno
  23. 9. 2026). Denní archiv leží na stejném disku jako databáze, takže při
  ztrátě serveru nepomůže. Dokud se nenastaví vzdálený cíl
  (`v-add-backup-host`) nebo se snímky nestahují jinam, je databáze bez kopie
  mimo server.
- Obnova ze snímku: zastavit zápisy (odvolat tokeny `token:revoke`), přesunout
  `pardubicko.db`, `-wal` a `-shm` stranou, zkopírovat snímek na
  `private/pardubicko/data/pardubicko.db`, `chown mujfibi:mujfibi`, spustit
  `pardubicko status` a ověřit `/api/health` a detail náhodné akce.
- Obnovu zkoušet nejméně jednou měsíčně do vedlejší cesty
  (`PARDUBICKO_DB=/tmp/zkouska.db pardubicko status`).

## Tokeny

| Příkaz | Účel |
|---|---|
| `pardubicko token:create NÁZEV [LIMIT]` | nový token, výchozí limit 50 publikací denně |
| `pardubicko token:list` | přehled bez tajných hodnot |
| `pardubicko token:limit NÁZEV LIMIT` | změna denního limitu |
| `pardubicko token:revoke NÁZEV` | odvolání |

Všechny příkazy pouštět pod `mujfibi` (`su -s /bin/sh mujfibi -c …`), jinak
by root mohl založit `-wal` a `-shm`, do kterých web nezapíše.

## Rollback

Kód: vrátit `private/pardubicko.predchozi` a `public_html.predchozi` na
jejich místa; data se předtím přesunou z `private/pardubicko/data` do
vraceného stromu. Pokud nová verze už migrovala schéma, starý kód s ním
nemusí fungovat; pak obnovit snímek pořízený při nasazení
(`private/zalohy/`, nejnovější před časem nasazení).
