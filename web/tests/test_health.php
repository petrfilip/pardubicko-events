<?php

declare(strict_types=1);

/**
 * Zdraví zdrojů v PHP musí rozhodovat stejně jako `tools/pipeline/health.py`.
 * Případy a čísla jsou převzaté z `tools/pipeline/test_health.py`.
 */

use Pardubicko\Clock;
use Pardubicko\HealthService;

require dirname(__DIR__) . '/src/bootstrap.php';
require __DIR__ . '/support.php';

const NOW = '2026-08-02T12:00:00+02:00';
const FULL = ['start_at' => 1.0, 'title' => 1.0, 'canonical_url' => 1.0];

function fresh(string $sourceId): PDO
{
    $pdo = test_database();
    $pdo->prepare("INSERT INTO source (id, name, url, type, priority, check_interval_days)
                   VALUES (?, ?, ?, 'html', 'high', 2)")
        ->execute([$sourceId, $sourceId, 'https://example.test/' . $sourceId]);

    return $pdo;
}

function days_ago(int $days): string
{
    return (new DateTimeImmutable(NOW))->modify(sprintf('%+d days', -$days))->format(Clock::FORMAT);
}

/** @param array<string, float>|null $fill */
function add_run(PDO $pdo, string $sourceId, int $days, ?int $items = null, ?array $fill = null,
                 int $status = 200, ?string $error = null, ?string $hash = null): void
{
    $pdo->prepare('INSERT INTO source_fetch (source_id, fetched_at, http_status, content_hash, error)
                   VALUES (?, ?, ?, ?, ?)')
        ->execute([$sourceId, days_ago($days), $status, $hash ?? "hash-$sourceId-$days", $error]);
    if ($items !== null) {
        $pdo->prepare('INSERT INTO source_extract (fetch_id, items_found, items_valid, fill_rates)
                       VALUES (?, ?, ?, ?)')
            ->execute([(int) $pdo->lastInsertId(), $items, $items, json_encode((object) ($fill ?? []))]);
    }
}

function add_event(PDO $pdo, string $eventId, string $sourceId, string $startAt): void
{
    $pdo->prepare("INSERT INTO event (id, title, start_at, municipality_name, source_type, source_url)
                   VALUES (?, ?, ?, 'Chrudim', 'html', ?)")
        ->execute([$eventId, "Akce $eventId", $startAt, "https://example.test/$eventId"]);
    $pdo->prepare('INSERT INTO event_source (event_id, source_id, url) VALUES (?, ?, ?)')
        ->execute([$eventId, $sourceId, "https://example.test/$eventId"]);
}

function evaluate(PDO $pdo, string $sourceId): array
{
    return (new HealthService($pdo, new Clock(new DateTimeImmutable(NOW))))->evaluate($sourceId);
}

// 1. Velký zdroj, který přestal vracet položky.
$pdo = fresh('beseda');
for ($day = 20; $day > 1; $day -= 2) {
    add_run($pdo, 'beseda', $day, 40, FULL);
}
add_run($pdo, 'beseda', 0, 0, []);
$result = evaluate($pdo, 'beseda');
check('baseline 40 → suspect', $result['state'], 'suspect');
check('baseline z historie', $result['extract']['baseline_items_median'], 40.0);
check('hodnotí se podle objemu', $result['extract']['volume_evaluated'], true);
check('stažení v pořádku', [$result['fetch']['ok'], $result['fetch']['consecutive_failures']], [true, 0]);
check('důvod jako v Pythonu', $result['reason'], 'stažení prošlo, ale extrakce vrátila 0 položek proti baseline 40');
$stored = $pdo->query("SELECT state, baseline_items_median, first_alerted_at FROM source_health")->fetch();
check('stav a baseline uložené', [$stored['state'], (float) $stored['baseline_items_median']], ['suspect', 40.0]);
check('první poplach datovaný', $stored['first_alerted_at'], NOW);

// 1b. Propad počtu položek.
foreach ([10 => 'degraded', 20 => 'healthy'] as $items => $expected) {
    $pdo = fresh('kalendar');
    for ($day = 20; $day > 1; $day -= 2) {
        add_run($pdo, 'kalendar', $day, 40, FULL);
    }
    add_run($pdo, 'kalendar', 0, $items, FULL);
    check("$items z baseline 40 → $expected", evaluate($pdo, 'kalendar')['state'], $expected);
}

// 2. Malá obec: nulová výtěžnost sama chyba není.
$pdo = fresh('slatinany');
for ($day = 20; $day > 1; $day -= 2) {
    add_run($pdo, 'slatinany', $day, 1, FULL);
}
add_run($pdo, 'slatinany', 0, 0, []);
$result = evaluate($pdo, 'slatinany');
check('baseline 1 a nula → healthy', $result['state'], 'healthy');
check('baseline 1 se nehodnotí', $result['extract']['volume_evaluated'], false);
contains('důvod pojmenuje nízký baseline', $result['reason'], 'nízký baseline');

$pdo = fresh('janderov');
foreach ([14, 7, 0] as $day) {
    add_run($pdo, 'janderov', $day, 0, []);
}
$result = evaluate($pdo, 'janderov');
check('zdroj bez položek healthy a bez baseline',
    [$result['state'], $result['extract']['baseline_items_median']], ['healthy', null]);

foreach ([2 => 'healthy', 3 => 'suspect'] as $baseline => $expected) {
    $pdo = fresh('hranicni');
    for ($day = 20; $day > 1; $day -= 2) {
        add_run($pdo, 'hranicni', $day, $baseline, FULL);
    }
    add_run($pdo, 'hranicni', 0, 0, []);
    check("baseline $baseline → $expected", evaluate($pdo, 'hranicni')['state'], $expected);
}

// 3. Klíčové pole se přestalo plnit.
$pdo = fresh('litomysl');
for ($day = 20; $day > 1; $day -= 2) {
    add_run($pdo, 'litomysl', $day, 12, FULL);
}
add_run($pdo, 'litomysl', 0, 12, ['start_at' => 0.0, 'title' => 1.0, 'canonical_url' => 1.0]);
$result = evaluate($pdo, 'litomysl');
check('prázdný start_at → schema-drift', [$result['state'], $result['extract']['drifted_fields']],
    ['schema-drift', ['start_at']]);

$pdo = fresh('nachod');
for ($day = 20; $day > 1; $day -= 2) {
    add_run($pdo, 'nachod', $day, 12, FULL);
}
add_run($pdo, 'nachod', 0, 12, ['start_at' => 0.9, 'title' => 1.0, 'canonical_url' => 1.0]);
check('vyplnění 0,9 driftem není', evaluate($pdo, 'nachod')['state'], 'healthy');

$pdo = fresh('bez-url');
$partial = ['start_at' => 1.0, 'title' => 1.0, 'canonical_url' => 0.0];
for ($day = 20; $day >= 0; $day -= 2) {
    add_run($pdo, 'bez-url', $day, 12, $partial);
}
check('nikdy neplněné pole není drift', evaluate($pdo, 'bez-url')['state'], 'healthy');

// 4. Opakované selhání stažení.
$pdo = fresh('hlinsko');
for ($day = 20; $day > 5; $day -= 2) {
    add_run($pdo, 'hlinsko', $day, 6, FULL);
}
foreach ([4, 2, 0] as $day) {
    add_run($pdo, 'hlinsko', $day, null, null, 503, 'HTTP 503');
}
$result = evaluate($pdo, 'hlinsko');
check('tři selhání → broken', [$result['state'], $result['fetch']['consecutive_failures']], ['broken', 3]);
check('poslední úspěch', $result['fetch']['last_success_at'], days_ago(6));

$pdo = fresh('hlinsko');
for ($day = 20; $day > 3; $day -= 2) {
    add_run($pdo, 'hlinsko', $day, 6, FULL);
}
foreach ([2, 0] as $day) {
    add_run($pdo, 'hlinsko', $day, null, null, 503, 'HTTP 503');
}
$result = evaluate($pdo, 'hlinsko');
check('dvě selhání nejsou broken', [$result['state'] !== 'broken', $result['fetch']['consecutive_failures']],
    [true, 2]);
check('baseline přežije výpadek', $result['extract']['baseline_items_median'], 6.0);

// 5. Obsah beze změny a nic budoucího.
foreach (['2026-06-01T10:00:00+02:00' => 'stale', '2026-09-01T10:00:00+02:00' => 'healthy'] as $start => $expected) {
    $pdo = fresh('kopec');
    foreach ([40, 33, 26, 19, 12, 5, 0] as $day) {
        add_run($pdo, 'kopec', $day, 1, FULL, 200, null, 'stejny-obsah');
    }
    add_event($pdo, 'akce', 'kopec', $start);
    $result = evaluate($pdo, 'kopec');
    check("akce $start → $expected", $result['state'], $expected);
    if ($expected === 'stale') {
        check('doba beze změny', $result['freshness']['unchanged_days'], 40);
    }
}

// Stažená akce není budoucí termín (odchylka od health.py, ten stažení neznal).
$pdo = fresh('kopec');
foreach ([40, 33, 26, 19, 12, 5, 0] as $day) {
    add_run($pdo, 'kopec', $day, 1, FULL, 200, null, 'stejny-obsah');
}
add_event($pdo, 'akce', 'kopec', '2026-09-01T10:00:00+02:00');
$pdo->exec("UPDATE event SET status = 'quarantined'");
check('stažená akce stale neruší', evaluate($pdo, 'kopec')['state'], 'stale');

$pdo = fresh('kratka');
foreach ([10, 5, 0] as $day) {
    add_run($pdo, 'kratka', $day, 1, FULL, 200, null, 'stejny-obsah');
}
check('deset dní beze změny stale není', evaluate($pdo, 'kratka')['state'], 'healthy');

// Tři signály zůstávají oddělené.
$pdo = fresh('trojice');
for ($day = 20; $day > 3; $day -= 2) {
    add_run($pdo, 'trojice', $day, 9, FULL, 200, null, 'A');
}
add_run($pdo, 'trojice', 0, null, null, 500, 'HTTP 500');
$result = evaluate($pdo, 'trojice');
check('signály oddělené', [$result['fetch']['ok'], $result['extract']['baseline_items_median'],
    $result['freshness']['last_item_at'], $result['state']], [false, 9.0, days_ago(4), 'healthy']);

// Seed baseline platí, dokud není vlastní historie; návrat do healthy smaže poplach.
$pdo = fresh('seed');
$pdo->exec("INSERT INTO source_health (source_id, state, baseline_items_median) VALUES ('seed', 'healthy', 8)");
add_run($pdo, 'seed', 0, 0, []);
$result = evaluate($pdo, 'seed');
check('seed slouží jako baseline', [$result['extract']['baseline_items_median'], $result['state']], [8.0, 'suspect']);
add_run($pdo, 'seed', -1, 8, FULL);
(new HealthService($pdo, new Clock(new DateTimeImmutable('2026-08-03T12:00:00+02:00'))))->evaluate('seed');
check('zotavení smaže první poplach',
    $pdo->query("SELECT state, first_alerted_at FROM source_health")->fetch(), ['state' => 'healthy', 'first_alerted_at' => null]);

finish('Zdraví zdrojů v PHP odpovídá health.py.');
