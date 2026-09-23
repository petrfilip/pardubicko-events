<?php

declare(strict_types=1);

use Pardubicko\Actor;
use Pardubicko\CandidateService;
use Pardubicko\ChangeLog;
use Pardubicko\Clock;
use Pardubicko\DomainError;
use Pardubicko\EventRepository;
use Pardubicko\EventService;
use Pardubicko\EventStore;
use Pardubicko\Matcher;
use Pardubicko\Migrator;

require dirname(__DIR__) . '/src/bootstrap.php';
require __DIR__ . '/support.php';

$pdo = test_database();
seed_catalog($pdo);
$clock = new Clock(new DateTimeImmutable('2026-08-10T12:00:00+02:00'));
$events = new EventService($pdo, $clock);
$candidates = new CandidateService($pdo, $clock);
$agent = Actor::agent('curator-test', 10);

// ---------------------------------------------------------------------
// Migrace
// ---------------------------------------------------------------------

$migrator = new Migrator($pdo);
check('migrace dojdou na poslední verzi', $migrator->currentVersion(), $migrator->latestVersion());
check('opakované migrace nic neaplikují', $migrator->migrate(), []);

// ---------------------------------------------------------------------
// Deduplikace odpovídá Python verzi
// ---------------------------------------------------------------------

$pairs = [
    [['title' => 'Letní koncert na zámku', 'start_at' => '2026-08-15T18:00:00+02:00', 'venue' => 'Zámek Pardubice'],
     ['title' => 'Letni koncert na zamku!', 'start_at' => '2026-08-15T18:30:00+02:00', 'venue' => 'Zámek Pardubice'],
     ['score' => 0.975, 'decision' => 'auto-merge', 'title' => 1.0, 'venue' => 1.0, 'time' => 0.8333]],
    [['title' => 'Pouťová zábava', 'start_at' => '2026-08-15T20:00:00+02:00', 'venue' => null],
     ['title' => 'Pouťová veselice', 'start_at' => '2026-08-15T20:00:00+02:00', 'venue' => 'Hasičská zbrojnice'],
     ['score' => 0.8691, 'decision' => 'review', 'title' => 0.8411, 'venue' => null, 'time' => 1.0]],
    [['title' => 'Divadlo v parku: Lakomec', 'start_at' => '2026-08-15T19:00:00+02:00', 'venue' => 'Park Na Špici'],
     ['title' => 'Lakomec – divadlo v parku', 'start_at' => '2026-08-15T19:00:00+02:00', 'venue' => 'Park Na Spici'],
     ['score' => 0.8083, 'decision' => 'review', 'title' => 0.7262, 'venue' => 1.0, 'time' => 1.0]],
    [['title' => 'Výstava hub', 'start_at' => '2026-08-15T09:00:00+02:00', 'venue' => 'Muzeum'],
     ['title' => 'Koncert dechovky', 'start_at' => '2026-08-15T15:00:00+02:00', 'venue' => 'Náměstí'],
     ['score' => 0.4217, 'decision' => 'separate', 'title' => 0.4867, 'venue' => 0.5397, 'time' => 0.0]],
    [['title' => 'Letní kino: Tři oříšky', 'start_at' => '2026-08-15T21:00:00+02:00', 'venue' => 'Amfiteátr'],
     ['title' => 'Letní kino – Tři oříšky pro Popelku', 'start_at' => '2026-08-15T21:00:00+02:00', 'venue' => 'Amfiteátr'],
     ['score' => 0.9491, 'decision' => 'auto-merge', 'title' => 0.9273, 'venue' => 1.0, 'time' => 1.0]],
];
foreach ($pairs as $index => [$left, $right, $expected]) {
    check('skóre shody ' . ($index + 1) . ' odpovídá matching.py', Matcher::score($left, $right), $expected);
}

// ---------------------------------------------------------------------
// Publikace
// ---------------------------------------------------------------------

$concert = valid_event();
$published = $events->publish($concert, $agent, 'run-1', 'Ověřeno na stránce pořadatele.');
check('publikace projde', $published['status'], 'published');
$id = $published['event']['id'];
check('ID se odvodí z názvu, obce a data', $id, 'letni-koncert-na-zamku-pardubice-2026-08-15');
check('obec je přeložená na číselník', $published['event']['municipality_id'], 555134);
check('alias kategorie je přeložený', $published['event']['categories'], ['hudba']);
check('týden začátku se založí', $published['event']['weeks'], ['2026-W33']);
check('zdroj akce je ve vazbách', $published['event']['sources'][0]['url'], $concert['source']['url']);
$history = (new ChangeLog($pdo, $clock))->entries(['entity' => 'event', 'entity_id' => $id]);
check('publikace je v historii', [$history[0]['action'], $history[0]['actor'], $history[0]['run_id']],
    ['publish', 'curator-test', 'run-1']);
check('historie drží stav po změně', $history[0]['after']['title'], 'Letní koncert na zámku');
$public = new EventRepository($pdo, $clock->now());
check('akce je na veřejném webu', $public->byId($id)['title'] ?? null, 'Letní koncert na zámku');
check('fulltext akci najde bez diakritiky', count($public->search(
    Pardubicko\EventFilter::fromQuery(['q' => 'zamku']))), 1);

// Odmítnutá data vracejí konkrétní pole.
$invalid = [
    'chybí zdroj' => [array_diff_key(valid_event(), ['source' => 1]), 'source'],
    'homepage jako zdroj' => [valid_event(['source' => ['type' => 'official', 'url' => 'https://example.test/']]), 'source.url'],
    'výpis zdroje z registru' => [valid_event(['source' => ['type' => 'official', 'url' => 'https://pardubice.eu/kalendar-akci']]), 'source.url'],
    'čas v UTC' => [valid_event(['start_at' => '2026-08-15T16:00:00Z']), 'start_at'],
    'špatný posun' => [valid_event(['start_at' => '2026-12-15T18:00:00+02:00']), 'start_at'],
    'neexistující datum' => [valid_event(['start_at' => '2026-02-30T18:00:00+01:00']), 'start_at'],
    'konec před začátkem' => [valid_event(['end_at' => '2026-08-15T17:00:00+02:00']), 'end_at'],
    'špatný rok' => [valid_event(['start_at' => '2062-08-15T18:00:00+02:00']), 'start_at'],
    'neznámá kategorie' => [valid_event(['categories' => ['koncert-dechovky']]), 'categories[0]'],
    'jen cílová skupina' => [valid_event(['categories' => ['deti']]), 'categories'],
    'neznámá obec' => [valid_event(['municipality' => 'Atlantida']), 'municipality'],
    'pole week' => [valid_event(['week' => '2026-W33']), 'week'],
    'neznámá cena' => [valid_event(['price' => ['type' => 'zdarma']]), 'price.type'],
];
foreach ($invalid as $label => [$input, $field]) {
    $error = expect_error(fn () => $events->publish($input, $agent, 'run-1', null));
    check('odmítne: ' . $label, [$error?->status, isset($error?->details['fields'][$field])], [422, true]);
}

// Duplicity.
$duplicate = expect_error(fn () => $events->publish(valid_event([
    'title' => 'Letni koncert na zamku!', 'start_at' => '2026-08-15T18:30:00+02:00',
    'source' => ['type' => 'ticketing', 'url' => 'https://tickets.test/koncert'],
]), $agent, 'run-1', null));
check('jistá duplicita vrátí 409', [$duplicate?->status, $duplicate?->errorCode], [409, 'duplicate']);
check('jistá duplicita ukáže existující akci', $duplicate?->details['existing_event_id'] ?? null, $id);
$sameId = expect_error(fn () => $events->publish(valid_event(['id' => $id]), $agent, 'run-1', null));
check('existující ID vrátí 409', $sameId?->errorCode, 'id-exists');

$fair = valid_event(['title' => 'Pouťová zábava', 'start_at' => '2026-08-15T20:00:00+02:00',
    'venue' => null, 'categories' => ['zabava'],
    'source' => ['type' => 'official', 'url' => 'https://example.test/pout']]);
$events->publish(valid_event(['title' => 'Pouťová veselice', 'start_at' => '2026-08-15T20:00:00+02:00',
    'venue' => 'Hasičská zbrojnice', 'categories' => ['zabava'],
    'source' => ['type' => 'official', 'url' => 'https://example.test/veselice']]), $agent, 'run-1', null);
$review = $events->publish($fair, $agent, 'run-1', null);
check('nejistá shoda jde do fronty', $review['status'], 'review');
check('nejistá shoda založí kandidáta', $candidates->get($review['candidate_id'])['state'] ?? null,
    'needs-verification');
check('fronta obsahuje shodu', count($candidates->pendingReviews()), 1);
$tight = Actor::agent('tight-limit', 1);
$events->publish(valid_event(['title' => 'Dechovka na náměstí', 'start_at' => '2026-08-16T15:00:00+02:00',
    'source' => ['type' => 'official', 'url' => 'https://example.test/dechovka']]), $tight, 'run-1', null);
$limited = expect_error(fn () => $events->publish(valid_event(['title' => 'Varhanní koncert',
    'start_at' => '2026-08-16T19:00:00+02:00',
    'source' => ['type' => 'official', 'url' => 'https://example.test/varhany']]), $tight, 'run-1', null));
check('denní limit publikací vrátí 429', [$limited?->status, $limited?->errorCode], [429, 'limit-exceeded']);

$otherAgent = Actor::agent('curator-second', 10);
$separate = $events->publish($fair, $otherAgent, 'run-2', 'Jiný pořadatel i místo.',
    $review['candidate_id'], ['poutova-veselice-pardubice-2026-08-15']);
check('výslovně odlišná akce se publikuje', $separate['status'], 'published');
check('kandidát se uzavře publikací', $candidates->get($review['candidate_id'])['state'], 'imported');
check('fronta shod se vyprázdní', count($candidates->pendingReviews()), 0);

// ---------------------------------------------------------------------
// Úpravy
// ---------------------------------------------------------------------

$updated = $events->update($id, ['title' => 'Letní koncert na zámku Pardubice',
    'end_at' => '2026-08-15T21:00:00+02:00'], $otherAgent, 'run-2', 'Doplněn konec podle programu.');
check('úprava změní název', $updated['title'], 'Letní koncert na zámku Pardubice');
check('úprava posune ověření', $updated['last_verified_at'], '2026-08-10T12:00:00+02:00');
$entry = (new ChangeLog($pdo, $clock))->entries(['entity_id' => $id])[0];
check('úprava má v historii stav před i po', [$entry['action'], $entry['before']['title'], $entry['after']['end_at']],
    ['update', 'Letní koncert na zámku', '2026-08-15T21:00:00+02:00']);
$badUpdate = expect_error(fn () => $events->update($id, ['start_at' => 'zítra'], $otherAgent, 'run-2', 'x'));
check('neplatná úprava se odmítne', $badUpdate?->status, 422);
check('odmítnutá úprava nic nezmění', (new EventStore($pdo, new Pardubicko\Catalog($pdo), $clock))->load($id)['start_at'],
    '2026-08-15T18:00:00+02:00');

$long = $events->publish(valid_event(['title' => 'Výstava fotografií', 'categories' => ['vystavy'],
    'start_at' => '2026-08-12T10:00:00+02:00', 'end_at' => '2026-08-30T18:00:00+02:00',
    'source' => ['type' => 'official', 'url' => 'https://example.test/vystava']]), $otherAgent, 'run-2', null);
check('dlouhá akce patří jen do existujících týdnů', $long['event']['weeks'], ['2026-W33']);
$september = $events->publish(valid_event(['title' => 'Koncert na konci prázdnin',
    'start_at' => '2026-08-26T19:00:00+02:00',
    'source' => ['type' => 'official', 'url' => 'https://example.test/zari']]), $otherAgent, 'run-2', null);
check('nový týden se založí', $september['event']['weeks'], ['2026-W35']);
check('nový týden převezme překrývající dlouhou akci',
    (new EventStore($pdo, new Pardubicko\Catalog($pdo), $clock))->load($long['event']['id'])['weeks'],
    ['2026-W33', '2026-W35']);

$cancelled = $events->cancel($id, $otherAgent, 'run-2', 'Pořadatel akci zrušil.');
check('zrušení se zapíše', $cancelled['cancelled'], true);
$events->withdraw($id, $otherAgent, 'run-2', 'Omylem publikovaná akce.');
check('stažená akce zmizí z webu', (new EventRepository($pdo, $clock->now()))->byId($id), null);
$missing = expect_error(fn () => $events->cancel('neexistuje', $otherAgent, null, 'x'));
check('neexistující akce vrátí 404', $missing?->status, 404);

$source = $events->addSource($long['event']['id'], 'https://tickets.test/vystava', null,
    $otherAgent, 'run-2', 'Další prodejce.');
check('další zdroj se připojí', [$source['added'], count($source['event']['sources'])], [true, 2]);
$again = $events->addSource($long['event']['id'], 'https://tickets.test/vystava', null,
    $otherAgent, 'run-2', null);
check('opakovaný zdroj se nepřidá', $again['added'], false);

// ---------------------------------------------------------------------
// Kandidáti
// ---------------------------------------------------------------------

$adapterPayload = ['normalized' => [
    'title' => 'Výstava fotografií', 'start_at' => '2026-08-12T10:00:00+02:00',
    'venue' => 'Zámek Pardubice', 'municipality' => 'Pardubice', 'municipality_id' => 555134,
    'canonical_url' => 'https://pardubice.eu/vystava-fotografii-1',
]];
$merged = $candidates->submit(['source_id' => 'pardubice-calendar', 'discovery_method' => 'adapter',
    'payload' => $adapterPayload], Actor::system('pipeline'), 'run-3');
check('kandidát s jistou shodou se sloučí', [$merged['result'], $merged['candidate']['state'],
    $merged['candidate']['event_id']], ['merged', 'imported', $long['event']['id']]);
check('sloučení připojí zdroj kandidáta', count((new EventStore($pdo, new Pardubicko\Catalog($pdo), $clock))
    ->load($long['event']['id'])['sources']), 3);
$repeat = $candidates->submit(['source_id' => 'pardubice-calendar', 'discovery_method' => 'adapter',
    'payload' => $adapterPayload], Actor::system('pipeline'), 'run-4');
check('opakované odeslání uzavřeného kandidáta nic nemění', $repeat['result'], 'closed');

$fresh = $candidates->submit(['discovery_method' => 'known-source', 'payload' => [
    'title' => 'Farmářský trh', 'date_text' => 'So 22. 8.', 'municipality' => 'Chrudim',
    'source_url' => 'https://example.test/trh', 'start_at' => '2026-08-22T08:00:00+02:00',
]], $agent, 'run-5');
check('nový kandidát se založí', [$fresh['result'], $fresh['candidate']['state']], ['created', 'new']);
check('výpis kandidátů filtruje týden', $candidates->list(['week' => '2026-W34'])['total'], 1);
check('výpis kandidátů mimo týden je prázdný', $candidates->list(['week' => '2026-W36'])['total'], 0);
$noNote = expect_error(fn () => $candidates->resolve($fresh['candidate']['id'], 'rejected', null, ' ',
    $agent, 'run-5'));
check('zamítnutí bez poznámky se odmítne', $noNote?->status, 422);
$rejected = $candidates->resolve($fresh['candidate']['id'], 'rejected', null,
    'Trh se letos nekoná.', $agent, 'run-5');
check('zamítnutí uzavře kandidáta', $rejected['state'], 'rejected');
$conflict = expect_error(fn () => $candidates->resolve($fresh['candidate']['id'], 'imported',
    $long['event']['id'], 'Omyl.', $agent, 'run-5'));
check('rozporné rozhodnutí se odmítne', $conflict?->errorCode, 'candidate-closed');

finish('Doménové testy prošly.');
