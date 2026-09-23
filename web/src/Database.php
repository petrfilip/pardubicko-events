<?php

declare(strict_types=1);

namespace Pardubicko;

use PDO;
use RuntimeException;

/**
 * Připojení k SQLite.
 *
 * Podle ADR 0008 je PHP aplikace jediným pisatelem databáze. Veřejné
 * stránky přesto čtou spojením v režimu `mode=ro`, takže chyba ve čtecí
 * cestě nemůže data změnit. Zápis (API, inbox, administrace) jde přes
 * samostatné spojení `mode=rw`, které před prvním použitím doběhne
 * případné migrace schématu.
 */
final class Database
{
    private static ?PDO $reader = null;
    private static ?PDO $writer = null;

    public static function reader(): PDO
    {
        return self::$reader ??= self::connect('mode=ro');
    }

    /** Spojení pro zápis. Schéma dotáhne na poslední migraci. */
    public static function writer(): PDO
    {
        if (self::$writer === null) {
            $pdo = self::connect('mode=rw');
            $migrator = new Migrator($pdo);
            if ($migrator->currentVersion() < $migrator->latestVersion()) {
                $migrator->migrate();
            }
            self::$writer = $pdo;
        }

        return self::$writer;
    }

    private static function connect(string $mode): PDO
    {
        $path = Config::databasePath();
        if (!is_file($path)) {
            throw new RuntimeException(sprintf(
                'Databáze %s neexistuje. Vytvoří ji `python3 tools/pipeline/pipeline.py import`.',
                $path,
            ));
        }

        $pdo = new PDO(self::dsn($path, $mode), null, null, [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
            PDO::ATTR_EMULATE_PREPARES => false,
        ]);

        // Databáze běží ve WAL módu, takže čtenáři zápis neblokuje.
        $pdo->exec('PRAGMA busy_timeout = 5000');
        if ($mode !== 'mode=ro') {
            $pdo->exec('PRAGMA journal_mode = WAL');
            $pdo->exec('PRAGMA foreign_keys = ON');
        }

        return $pdo;
    }

    /**
     * DSN ve tvaru URI, aby šlo předat `mode`. Cesta se percent-enkóduje
     * po segmentech; SQLite jinak znaky jako `?` nebo mezera interpretuje.
     */
    private static function dsn(string $path, string $query): string
    {
        $encoded = implode('/', array_map('rawurlencode', explode('/', $path)));

        return 'sqlite:file:' . $encoded . '?' . $query;
    }
}
