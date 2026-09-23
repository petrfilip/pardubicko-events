<?php

declare(strict_types=1);

namespace Pardubicko;

use PDO;
use RuntimeException;

/**
 * Připojení k SQLite.
 *
 * Podle ADR 0008 je PHP aplikace jediným pisatelem databáze. Veřejné
 * stránky přesto čtou spojením jen pro čtení, takže chyba ve čtecí
 * cestě nemůže data změnit. Zápis (API, inbox, administrace) jde přes
 * samostatné spojení pro zápis, které před prvním použitím doběhne
 * případné migrace schématu.
 */
final class Database
{
    private static ?PDO $reader = null;
    private static ?PDO $writer = null;

    public static function reader(): PDO
    {
        return self::$reader ??= self::connect(true);
    }

    /** Spojení pro zápis. Schéma dotáhne na poslední migraci. */
    public static function writer(): PDO
    {
        if (self::$writer === null) {
            $pdo = self::connect(false);
            $migrator = new Migrator($pdo);
            if ($migrator->currentVersion() < $migrator->latestVersion()) {
                $migrator->migrate();
            }
            self::$writer = $pdo;
        }

        return self::$writer;
    }

    private static function connect(bool $readOnly): PDO
    {
        $path = Config::databasePath();
        if (!is_file($path)) {
            throw new RuntimeException(sprintf('Databáze %s neexistuje.', $path));
        }

        // Režim se předává příznakem, ne URI `file:…?mode=ro`: pod
        // open_basedir (Hestia) PHP kontroluje celé URI jako cestu a soubor
        // odmítne, přestože leží v povoleném adresáři.
        $pdo = new PDO('sqlite:' . $path, null, null, [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
            PDO::ATTR_EMULATE_PREPARES => false,
            PDO::SQLITE_ATTR_OPEN_FLAGS => $readOnly
                ? PDO::SQLITE_OPEN_READONLY : PDO::SQLITE_OPEN_READWRITE,
        ]);

        // Databáze běží ve WAL módu, takže čtenáři zápis neblokuje.
        $pdo->exec('PRAGMA busy_timeout = 5000');
        if (!$readOnly) {
            $pdo->exec('PRAGMA journal_mode = WAL');
            $pdo->exec('PRAGMA foreign_keys = ON');
        }

        return $pdo;
    }
}
