<?php

declare(strict_types=1);

/** Report běhu pipeline přes `POST /api/v1/runs` a navazující zdraví a splatnost zdrojů. */

use Pardubicko\Application;
use Pardubicko\Clock;
use Pardubicko\TokenRepository;

require dirname(__DIR__) . '/src/bootstrap.php';
require __DIR__ . '/support.php';

$pdo = test_database();
seed_catalog($pdo);
$now = new DateTimeImmutable('2026-09-23T10:00:00+02:00');
$token = (new TokenRepository($pdo, new Clock($now)))->create('nanoclaw-pipeline', 0);
$app = new Application($pdo, $pdo, $now);

/** @return array{0: int, 1: array<string, mixed>} */
function api(string $method, string $path, ?array $body = null, ?string $run = 'pipeline-1'): array
{
    global $app, $token;
    $headers = array_filter(['Authorization' => 'Bearer ' . $token, 'X-Run-Id' => $run], 'is_string');
    $response = $app->handle($method, $path, [], $headers,
        $body === null ? '' : (string) json_encode($body, JSON_UNESCAPED_UNICODE));

    return [$response->status, json_decode($response->body, true) ?? []];
}

/** @return array<string, mixed> */
function fetch_item(string $at, int $items, array $overrides = []): array
{
    return array_merge([
        'url' => 'https://pardubice.eu/kalendar-akci',
        'fetched_at' => $at,
        'http_status' => 200,
        'etag' => '"abc"',
        'content_hash' => 'sha256:' . $at,
        'bytes' => 51200,
        'duration_ms' => 420,
        'extract' => ['items_found' => $items, 'items_valid' => $items, 'items_unparsed' => 0,
            'fill_rates' => ['start_at' => 1.0, 'title' => 1.0, 'canonical_url' => 1.0]],
    ], $overrides);
}

/** @return array<string, mixed> */
function run_body(array $sources, string $status = 'success'): array
{
    return ['run' => [
        'started_at' => '2026-09-23T09:58:00+02:00',
        'finished_at' => '2026-09-23T09:59:30+02:00',
        'status' => $status,
        'offline' => false,
        'report' => ['schema_version' => 1, 'agent' => 'pipeline', 'metrics' => ['sources_checked' => 1]],
        'sources' => $sources,
    ]];
}

// Validace: chyby mají cestu k poli.
[$status, $body] = api('POST', '/api/v1/runs', ['run' => ['status' => 'hotovo', 'sources' => [
    ['source_id' => 'neznamy', 'status' => 'success'],
    ['source_id' => 'pardubice-calendar', 'status' => 'success', 'items_found' => -1,
     'fetches' => [['fetched_at' => 'včera', 'extract' => ['fill_rates' => ['title' => 2]]]]],
]]]);
check('neplatný report 422', $status, 422);
$fields = $body['fields'] ?? [];
check('chyby ukazují na pole', array_keys($fields), [
    'started_at', 'finished_at', 'status', 'sources[0].source_id', 'sources[1].items_found',
    'sources[1].fetches[0].fetched_at', 'sources[1].fetches[0].extract.items_found',
    'sources[1].fetches[0].extract.fill_rates.title',
]);
[$status, $body] = api('POST', '/api/v1/runs', run_body([]), null);
check('report bez X-Run-Id 400', [$status, $body['code'] ?? null], [400, 'run-id-required']);
check('neplatný report nic nezapsal', (int) $pdo->query('SELECT count(*) FROM pipeline_run')->fetchColumn(), 0);

// Zdroj před prvním reportem je splatný a nemá zdraví.
[, $body] = api('GET', '/api/v1/sources/pardubice-calendar');
check('před během splatný', [$body['due'] ?? null, array_key_exists('last_fetched_at', $body) ? $body['last_fetched_at'] : 'x'], [true, null]);

// Platný report.
$source = [
    'source_id' => 'pardubice-calendar', 'status' => 'success',
    'items_found' => 40, 'items_valid' => 38, 'candidates_created' => 5, 'candidates_existing' => 33,
    'fetches' => [fetch_item('2026-09-23T09:58:10+02:00', 40)],
];
[$status, $body] = api('POST', '/api/v1/runs', run_body([$source]));
check('report 201', [$status, $body['run_id'] ?? null, $body['sources'] ?? null, $body['fetches'] ?? null],
    [201, 'pipeline-1', 1, 1]);
check('zdraví se spočítalo', [$body['health'][0]['state'] ?? null, array_key_exists('previous_state', $body['health'][0] ?? []) ? $body['health'][0]['previous_state'] : 'x'],
    ['healthy', null]);

[$status, $body] = api('GET', '/api/v1/runs/pipeline-1');
check('detail běhu', [$status, $body['status'] ?? null, $body['actor'] ?? null, $body['offline'] ?? null],
    [200, 'success', 'nanoclaw-pipeline', false]);
check('report se uložil celý', $body['report']['metrics']['sources_checked'] ?? null, 1);
check('výsledek zdroje', [$body['sources'][0]['items_valid'] ?? null, $body['sources'][0]['candidates_created'] ?? null],
    [38, 5]);
$fetch = $pdo->query('SELECT f.run_id, f.etag, e.items_found, e.fill_rates FROM source_fetch f
                      JOIN source_extract e ON e.fetch_id = f.id')->fetch();
check('stažení patří k běhu', [$fetch['run_id'], $fetch['etag'], (int) $fetch['items_found']],
    ['pipeline-1', '"abc"', 40]);
check('fill rates seřazené', $fetch['fill_rates'], '{"canonical_url":1,"start_at":1,"title":1}');

[, $body] = api('GET', '/api/v1/sources/pardubice-calendar');
check('po běhu nesplatný a zdravý', [$body['due'] ?? null, $body['health']['state'] ?? null,
    $body['last_fetched_at'] ?? null], [false, 'healthy', '2026-09-23T09:58:10+02:00']);

// Opakovaný report téhož běhu.
[$status, $body] = api('POST', '/api/v1/runs', run_body([$source]));
check('opakovaný report 409', [$status, $body['code'] ?? null, $body['run_id'] ?? null],
    [409, 'run-exists', 'pipeline-1']);
check('opakování nic nepřidalo', (int) $pdo->query('SELECT count(*) FROM source_fetch')->fetchColumn(), 1);

// Tři selhání v řadě → broken, v odpovědi i s předchozím stavem.
foreach (['pipeline-2', 'pipeline-3', 'pipeline-4'] as $index => $run) {
    [$status, $body] = api('POST', '/api/v1/runs', run_body([[
        'source_id' => 'pardubice-calendar', 'status' => 'failed', 'error' => 'HTTP 503',
        'fetches' => [fetch_item("2026-09-23T1{$index}:00:00+02:00", 0,
            ['http_status' => 503, 'error' => 'HTTP 503', 'extract' => null])],
    ]], 'failed'), $run);
}
check('třetí selhání → broken', [$status, $body['health'][0]['state'] ?? null,
    $body['health'][0]['previous_state'] ?? null], [201, 'broken', 'healthy']);
[, $body] = api('GET', '/api/v1/sources/pardubice-calendar');
check('broken je vidět u zdroje', [$body['health']['state'] ?? null, $body['health']['consecutive_failures'] ?? null],
    ['broken', 3]);

// Přeskočený zdroj bez stažení se zapíše, zdraví se nepřepočítává.
[$status, $body] = api('POST', '/api/v1/runs', run_body([
    ['source_id' => 'pardubice-calendar', 'status' => 'skipped', 'error' => 'Adaptér chybí.'],
], 'no-change'), 'pipeline-5');
check('zdroj bez stažení bez přepočtu', [$status, $body['health'] ?? null], [201, []]);

// Pipeline token s nulovým limitem publikací smí reportovat, ale ne publikovat.
[$status, $body] = api('POST', '/api/v1/events', ['event' => valid_event()], 'pipeline-6');
check('pipeline token nepublikuje', [$status, $body['code'] ?? null], [429, 'limit-exceeded']);

finish('Report běhu prošel.');
