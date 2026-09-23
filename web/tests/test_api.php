<?php

declare(strict_types=1);

use Pardubicko\Application;
use Pardubicko\Clock;
use Pardubicko\TokenRepository;

require dirname(__DIR__) . '/src/bootstrap.php';
require __DIR__ . '/support.php';

$pdo = test_database();
seed_catalog($pdo);
$now = new DateTimeImmutable('2026-08-10T12:00:00+02:00');
$clock = new Clock($now);
$token = (new TokenRepository($pdo, $clock))->create('nanoclaw-curator', 5);
$app = new Application($pdo, $pdo, $now);

/** @return array{0: int, 1: array<string, mixed>} */
function api(string $method, string $path, ?array $body = null, array $query = [],
             ?string $token = null, ?string $run = 'run-api-1'): array
{
    global $app;
    $headers = array_filter([
        'Authorization' => $token === null ? null : 'Bearer ' . $token,
        'X-Run-Id' => $run,
    ], 'is_string');
    $response = $app->handle($method, $path, $query, $headers,
        $body === null ? '' : (string) json_encode($body, JSON_UNESCAPED_UNICODE));

    return [$response->status, json_decode($response->body, true) ?? []];
}

// Autentizace.
[$status, $body] = api('GET', '/api/v1/me');
check('bez tokenu 401', [$status, $body['code'] ?? null], [401, 'unauthorized']);
[$status] = api('GET', '/api/v1/me', null, [], 'pe_neplatny');
check('neplatný token 401', $status, 401);
[$status, $body] = api('GET', '/api/v1/me', null, [], $token);
check('token se ohlásí jménem', [$status, $body['token'] ?? null, $body['daily_publish_limit'] ?? null],
    [200, 'nanoclaw-curator', 5]);
[$status, $body] = api('GET', '/api/v1', null, [], $token);
check('přehled endpointů', [$status, isset($body['endpoints']['POST /api/v1/events {event, note?, candidate_id?, distinct_from?}'])],
    [200, true]);
[$status, $body] = api('GET', '/api/v1/taxonomy', null, [], $token);
check('taxonomie vrací kategorie s aliasy', $body['categories'][1]['aliases'] ?? null, ['koncert']);
check('taxonomie vrací obce', count($body['municipalities'] ?? []), 2);

// Zápis vyžaduje běh a obálku.
[$status, $body] = api('POST', '/api/v1/events', ['event' => valid_event()], [], $token, null);
check('zápis bez X-Run-Id 400', [$status, $body['code'] ?? null], [400, 'run-id-required']);
$raw = $app->handle('POST', '/api/v1/events', [], ['Authorization' => 'Bearer ' . $token,
    'X-Run-Id' => 'run-api-1'], '{nejson');
check('nevalidní JSON 400', $raw->status, 400);
[$status, $body] = api('POST', '/api/v1/events', valid_event(), [], $token);
check('akce mimo obálku 422', [$status, isset($body['fields']['event'])], [422, true]);

// Publikace a čtení.
[$status, $body] = api('POST', '/api/v1/events', ['event' => valid_event(),
    'note' => 'Ověřeno na webu pořadatele.'], [], $token);
check('publikace vrací 201', [$status, $body['status'] ?? null], [201, 'published']);
$id = $body['event']['id'] ?? '';
check('bezpečnostní hlavičky i na API', $app->handle('GET', '/api/v1/me', [], ['Authorization' => 'Bearer ' . $token])
    ->headers['X-Content-Type-Options'] ?? null, 'nosniff');
[$status, $body] = api('POST', '/api/v1/events', ['event' => valid_event(['start_at' => 'zítra'])], [], $token);
check('neplatná data 422 s polem', [$status, $body['code'] ?? null, isset($body['fields']['start_at'])],
    [422, 'invalid', true]);
[$status, $body] = api('POST', '/api/v1/events', ['event' => valid_event([
    'source' => ['type' => 'ticketing', 'url' => 'https://tickets.test/koncert']])], [], $token);
check('duplicita 409 s existující akcí', [$status, $body['code'] ?? null, $body['existing_event_id'] ?? null],
    [409, 'duplicate', $id]);
[$status, $body] = api('POST', "/api/v1/events/$id/sources", ['url' => 'https://tickets.test/koncert',
    'note' => 'Prodej vstupenek.'], [], $token);
check('další zdroj 201', [$status, $body['added'] ?? null], [201, true]);

[$status, $body] = api('GET', '/api/v1/events', null, ['week' => '2026-W33', 'municipality' => 'Pardubice'], $token);
check('výpis týdne a obce', [$status, $body['total'] ?? null, $body['events'][0]['id'] ?? null], [200, 1, $id]);
[$status, $body] = api('GET', '/api/v1/events', null, ['week' => '33'], $token);
check('neplatný týden 422', $status, 422);
[$status, $body] = api('GET', "/api/v1/events/$id", null, [], $token);
check('detail akce', [$status, count($body['sources'] ?? [])], [200, 2]);
[$status] = api('GET', '/api/v1/events/neexistuje', null, [], $token);
check('neexistující akce 404', $status, 404);

// Úprava, historie, zrušení a stažení.
[$status, $body] = api('PATCH', "/api/v1/events/$id", ['changes' => ['venue' => 'Nádvoří zámku']], [], $token);
check('úprava bez poznámky 422', [$status, isset($body['fields']['note'])], [422, true]);
[$status, $body] = api('PATCH', "/api/v1/events/$id", ['changes' => ['venue' => 'Nádvoří zámku'],
    'note' => 'Upřesněné místo.'], [], $token);
check('úprava 200', [$status, $body['venue'] ?? null], [200, 'Nádvoří zámku']);
[$status, $body] = api('GET', "/api/v1/events/$id/history", null, [], $token);
check('historie má všechny kroky', array_column($body['changes'] ?? [], 'action'),
    ['update', 'add-source', 'publish']);
[$status, $body] = api('GET', '/api/v1/changes', null, ['run_id' => 'run-api-1'], $token);
check('historie běhu', count($body['changes'] ?? []), 3);
[$status, $body] = api('POST', "/api/v1/events/$id/cancel", ['note' => 'Zrušeno pořadatelem.'], [], $token);
check('zrušení 200', [$status, $body['cancelled'] ?? null], [200, true]);
check('zrušená akce je na webu označená', str_contains($app->handle('GET', "/akce/$id")->body, 'Zrušeno')
    || str_contains($app->handle('GET', "/akce/$id")->body, 'zrušen'), true);
[$status] = api('POST', "/api/v1/events/$id/withdraw", ['note' => 'Omyl.'], [], $token);
check('stažení 200', $status, 200);
check('stažená akce na webu 404', $app->handle('GET', "/akce/$id")->status, 404);
[$status, $body] = api('GET', '/api/v1/events', null, ['status' => 'draft'], $token);
check('stažená akce je mezi koncepty', $body['total'] ?? null, 1);

// Kandidáti.
[$status, $body] = api('POST', '/api/v1/candidates', ['candidate' => [
    'discovery_method' => 'known-source',
    'payload' => ['title' => 'Farmářský trh', 'municipality' => 'Chrudim',
        'start_at' => '2026-08-22T08:00:00+02:00', 'source_url' => 'https://example.test/trh'],
]], [], $token);
check('kandidát 201', [$status, $body['result'] ?? null], [201, 'created']);
$candidateId = $body['candidate']['id'] ?? '';
[$status, $body] = api('POST', '/api/v1/candidates', ['candidate' => [
    'discovery_method' => 'known-source',
    'payload' => ['title' => 'Farmářský trh', 'municipality' => 'Chrudim',
        'start_at' => '2026-08-22T08:00:00+02:00', 'source_url' => 'https://example.test/trh'],
]], [], $token);
check('stejný kandidát podruhé 200 beze změny', [$status, $body['result'] ?? null], [200, 'unchanged']);
[$status, $body] = api('GET', '/api/v1/candidates', null, ['week' => '2026-W34'], $token);
check('kandidáti týdne', [$status, $body['total'] ?? null], [200, 1]);
[$status, $body] = api('GET', '/api/v1/candidates', null, ['state' => 'hotovo'], $token);
check('neznámý stav 422', $status, 422);
[$status, $body] = api('POST', '/api/v1/events', ['event' => valid_event(['title' => 'Farmářský trh',
    'municipality' => 'Chrudim', 'categories' => ['zabava'], 'venue' => 'Resselovo náměstí',
    'start_at' => '2026-08-22T08:00:00+02:00',
    'source' => ['type' => 'official', 'url' => 'https://example.test/trh']]),
    'candidate_id' => $candidateId, 'note' => 'Ověřeno.'], [], $token);
check('publikace z kandidáta', $status, 201);
[$status, $body] = api('GET', "/api/v1/candidates/$candidateId", null, [], $token);
check('kandidát je po publikaci uzavřený', [$body['state'] ?? null, $body['event_id'] ?? null],
    ['imported', 'farmarsky-trh-chrudim-2026-08-22']);

// Zdroje.
[$status, $body] = api('GET', '/api/v1/sources', null, ['due' => '1'], $token);
check('nestažený zdroj je splatný', [$status, $body['sources'][0]['id'] ?? null], [200, 'pardubice-calendar']);
[$status, $body] = api('PATCH', '/api/v1/sources/pardubice-calendar', ['changes' => ['check_interval_days' => 0],
    'note' => 'x'], [], $token);
check('neplatný interval 422', $status, 422);
[$status, $body] = api('PATCH', '/api/v1/sources/pardubice-calendar', ['changes' => ['enabled' => false],
    'note' => 'Web je v rekonstrukci.'], [], $token);
check('vypnutí zdroje', [$status, $body['enabled'] ?? null], [200, false]);
[$status, $body] = api('GET', '/api/v1/sources', null, ['due' => '1'], $token);
check('vypnutý zdroj není splatný', $body['sources'] ?? null, []);

// Inbox a neznámé cesty.
[$status, $body] = api('POST', '/api/v1/inbox', ['url' => 'https://example.test/tip'], [], $token);
check('inbox přes API v1', [$status, $body['duplicate'] ?? null], [202, false]);
[$status, $body] = api('GET', '/api/v1/neexistuje', null, [], $token);
check('neznámý endpoint 404 v JSON', [$status, $body['code'] ?? null], [404, 'not-found']);
[$status, $body] = api('DELETE', "/api/v1/events/$id", null, [], $token);
check('nepovolená metoda 405 v JSON', [$status, $body['code'] ?? null], [405, 'method-not-allowed']);

// Denní limit tokenu.
(new TokenRepository($pdo, $clock))->setLimit('nanoclaw-curator', 2);
[$status, $body] = api('POST', '/api/v1/events', ['event' => valid_event(['title' => 'Varhanní koncert',
    'start_at' => '2026-08-16T19:00:00+02:00',
    'source' => ['type' => 'official', 'url' => 'https://example.test/varhany']])], [], $token);
check('vyčerpaný limit 429', [$status, $body['code'] ?? null], [429, 'limit-exceeded']);
(new TokenRepository($pdo, $clock))->revoke('nanoclaw-curator');
[$status] = api('GET', '/api/v1/me', null, [], $token);
check('odvolaný token 401', $status, 401);

finish('Testy API prošly.');
