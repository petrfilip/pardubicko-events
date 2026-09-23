<?php

declare(strict_types=1);

/**
 * Společné pomůcky testů webu. Testy jsou samostatné skripty bez
 * frameworku; selhání se sbírají a na konci se vypíšou všechna najednou.
 */

use Pardubicko\DomainError;
use Pardubicko\Migrator;

$failures = [];

function check(string $label, mixed $actual, mixed $expected): void
{
    global $failures;
    if ($actual !== $expected) {
        $failures[] = sprintf('%s: očekáváno %s, skutečnost %s',
            $label, var_export($expected, true), var_export($actual, true));
    } else {
        echo "OK  {$label}\n";
    }
}

function contains(string $label, string $haystack, string $needle): void
{
    check($label, str_contains($haystack, $needle), true);
}

function expect_error(callable $work): ?DomainError
{
    try {
        $work();
    } catch (DomainError $error) {
        return $error;
    }

    return null;
}

function finish(string $message): never
{
    global $failures;
    if ($failures !== []) {
        echo "\nNEPROŠLO " . count($failures) . " kontrol:\n";
        foreach ($failures as $failure) {
            echo " - {$failure}\n";
        }
        exit(1);
    }
    echo "\n{$message}\n";
    exit(0);
}

/** Prázdná databáze v paměti se všemi migracemi. */
function test_database(): PDO
{
    $pdo = new PDO('sqlite::memory:', null, null, [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        PDO::ATTR_EMULATE_PREPARES => false,
    ]);
    $pdo->exec('PRAGMA foreign_keys = ON');
    (new Migrator($pdo))->migrate();

    return $pdo;
}

/** Minimální číselníky: dvě obce, alias, kategorie obou os a jeden zdroj. */
function seed_catalog(PDO $pdo): void
{
    $pdo->exec("INSERT INTO municipality (id, name, district, region) VALUES
        (555134, 'Pardubice', 'Pardubice', 'pardubicky-kraj'),
        (571164, 'Chrudim', 'Chrudim', 'pardubicky-kraj')");
    $pdo->exec("INSERT INTO municipality_alias (alias, municipality_id) VALUES ('Janderov', 571164)");
    $pdo->exec("INSERT INTO category (id, axis, sort_order, label) VALUES
        ('hudba', 'kind', 10, 'Hudba'), ('vystavy', 'kind', 50, 'Výstavy'),
        ('zabava', 'kind', 160, 'Zábava'), ('deti', 'audience', 200, 'Děti')");
    $pdo->exec("INSERT INTO category_alias (alias, category_id) VALUES ('koncert', 'hudba')");
    $pdo->exec("INSERT INTO source
        (id, name, url, type, municipality_name, region, priority, check_interval_days, enabled)
        VALUES ('pardubice-calendar', 'Pardubice.eu', 'https://pardubice.eu/kalendar-akci',
                'city-calendar', 'Pardubice', 'pardubicky-kraj', 'high', 1, 1)");
}

/** @param array<string, mixed> $overrides */
function valid_event(array $overrides = []): array
{
    return array_merge([
        'title' => 'Letní koncert na zámku',
        'description' => 'Koncert komorní hudby na nádvoří.',
        'start_at' => '2026-08-15T18:00:00+02:00',
        'end_at' => null,
        'all_day' => false,
        'venue' => 'Zámek Pardubice',
        'municipality' => 'Pardubice',
        'categories' => ['koncert'],
        'price' => ['type' => 'paid', 'text' => '200 Kč', 'amount' => 200, 'currency' => 'CZK'],
        'source' => ['type' => 'official', 'url' => 'https://example.test/letni-koncert'],
        'cancelled' => false,
    ], $overrides);
}
