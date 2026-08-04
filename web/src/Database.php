<?php

declare(strict_types=1);

namespace Pardubicko;

use PDO;
use RuntimeException;

/**
 * Připojení k SQLite.
 *
 * Podle ADR 0002 je webová vrstva **čtenář**. Výchozí spojení se proto
 * otevírá v režimu `mode=ro`; pokus o zápis skončí chybou databáze, ne
 * tichým poškozením dat. Jedinou výjimkou je zápis do tabulky `inbox`
 * z `POST /api/inbox`, pro který existuje samostatné spojení `mode=rw`.
 * Pisatelem publikovaných dat zůstává Python pipeline.
 */
final class Database
{
    private static ?PDO $reader = null;
    private static ?PDO $writer = null;

    public static function reader(): PDO
    {
        return self::$reader ??= self::connect('mode=ro');
    }

    /** Spojení pro zápis do `inbox`. Nikde jinde se nepoužívá. */
    public static function writer(): PDO
    {
        return self::$writer ??= self::connect('mode=rw');
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

        // Databáze běží ve WAL módu a pipeline do ní může psát souběžně.
        $pdo->exec('PRAGMA busy_timeout = 5000');
        if ($mode !== 'mode=ro') {
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
