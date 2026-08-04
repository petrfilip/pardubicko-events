<?php

declare(strict_types=1);

namespace Pardubicko;

/**
 * Konfigurace čtená z prostředí. Aplikace nemá konfigurační soubor;
 * všechno, co se liší mezi vývojem a provozem, přichází v proměnných
 * prostředí (viz `docker-compose.yml`, službu `app`).
 */
final class Config
{
    public const TIMEZONE = 'Europe/Prague';

    /** Počet akcí na stránku ve výpisech. */
    public const PER_PAGE = 20;

    /** Cesta k souboru databáze. Zapisuje do ní jedině Python pipeline. */
    public static function databasePath(): string
    {
        $path = self::env('PARDUBICKO_DB');

        return $path ?? dirname(__DIR__, 2) . '/var/pardubicko.db';
    }

    /**
     * Token pro `POST /api/inbox`. Není-li nastavený, endpoint se neotevře
     * dokořán, ale odmítne obsluhu — chybějící konfigurace nesmí znamenat
     * veřejný zápis do databáze.
     */
    public static function inboxToken(): ?string
    {
        return self::env('PARDUBICKO_INBOX_TOKEN');
    }

    /**
     * Absolutní základ URL pro kanonické odkazy, sitemapu a JSON-LD.
     * V provozu se nastavuje `PARDUBICKO_BASE_URL`; ve vývoji se odvodí
     * z požadavku.
     */
    public static function baseUrl(): string
    {
        $configured = self::env('PARDUBICKO_BASE_URL');
        if ($configured !== null) {
            return rtrim($configured, '/');
        }

        $https = ($_SERVER['HTTPS'] ?? '') !== '' && ($_SERVER['HTTPS'] ?? '') !== 'off';
        $scheme = $https ? 'https' : 'http';

        // Hlavička Host pochází od klienta, proto se propouštějí jen znaky,
        // které se v názvu hostitele a portu smějí vyskytnout.
        $host = (string) ($_SERVER['HTTP_HOST'] ?? 'localhost');
        $host = preg_replace('/[^A-Za-z0-9.\-:\[\]]/', '', $host) ?? '';
        if ($host === '') {
            $host = 'localhost';
        }

        return $scheme . '://' . $host;
    }

    private static function env(string $name): ?string
    {
        $value = $_ENV[$name] ?? $_SERVER[$name] ?? getenv($name);
        if (!is_string($value) || trim($value) === '') {
            return null;
        }

        return trim($value);
    }
}
