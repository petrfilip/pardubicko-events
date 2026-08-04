<?php

declare(strict_types=1);

use Pardubicko\Application;
use Pardubicko\EventFilter;
use Pardubicko\EventRepository;
use Pardubicko\UrlNormalizer;

require dirname(__DIR__) . '/src/bootstrap.php';

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

$pdo = new PDO('sqlite::memory:', null, null, [
    PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
    PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
]);
$pdo->exec((string) file_get_contents(dirname(__DIR__, 2) . '/tools/pipeline/schema.sql'));
$pdo->exec("INSERT INTO week (id, date_from, date_to, file, generated_at, position)
    VALUES ('2026-W32', '2026-08-03', '2026-08-09', 'data/weeks/2026-W32.json',
            '2026-08-03T12:00:00+02:00', 1)");
$pdo->exec("INSERT INTO category (id, label, axis, sort_order)
    VALUES ('hudba', 'Hudba', 'kind', 10), ('divadlo', 'Divadlo', 'kind', 20),
           ('pro-deti', 'Pro děti', 'audience', 100)");
$pdo->exec("INSERT INTO source
    (id, name, url, type, municipality_name, region, priority, check_interval_days, enabled)
    VALUES ('pardubice-calendar', 'Pardubice.eu', 'https://pardubice.eu/kalendar-akci',
            'city-calendar', 'Pardubice', 'pardubicky-kraj', 'high', 1, 1)");
$pdo->exec("INSERT INTO source_health
    (source_id, state, consecutive_failures, last_checked_at, last_success_at)
    VALUES ('pardubice-calendar', 'healthy', 0, '2026-08-03T10:00:00+02:00',
            '2026-08-03T10:00:00+02:00')");
$pdo->exec("INSERT INTO repo_meta (key, value)
    VALUES ('manifest_generated_at', '2026-08-03T12:00:00+02:00')");

$insertEvent = $pdo->prepare("INSERT INTO event
    (id, title, description, start_at, end_at, all_day, venue, municipality_name,
     price_type, price_text, source_type, source_url, status, match_title_norm,
     first_seen_at, last_seen_at, last_verified_at)
    VALUES (:id, :title, :description, :start, :end, 0, :venue, :municipality,
            :price, :price_text, 'official-calendar', :url, 'published', :norm,
            '2026-08-01T10:00:00+02:00', '2026-08-03T10:00:00+02:00',
            '2026-08-03T10:00:00+02:00')");
$insertWeek = $pdo->prepare(
    "INSERT INTO event_week (event_id, week_id, position) VALUES (?, '2026-W32', ?)");
$insertCategory = $pdo->prepare(
    'INSERT INTO event_category (event_id, position, name, category_id) VALUES (?, 0, ?, ?)');
$insertSource = $pdo->prepare("INSERT INTO event_source
    (event_id, source_id, url, first_seen_at, last_seen_at)
    VALUES (?, 'pardubice-calendar', ?, '2026-08-01T10:00:00+02:00',
            '2026-08-03T10:00:00+02:00')");
$insertFts = $pdo->prepare(
    'INSERT INTO event_fts (event_id, title, description, venue, municipality, categories)
     VALUES (?, ?, ?, ?, ?, ?)');

for ($index = 1; $index <= 25; $index++) {
    $id = $index === 1 ? 'letni-koncert-pardubice' : sprintf('testovaci-akce-%02d', $index);
    $title = $index === 1 ? 'Letní koncert na zámku' : sprintf('Testovací akce %02d', $index);
    $municipality = $index === 2 ? 'Chrudim' : 'Pardubice';
    $category = $index % 2 === 0 ? 'divadlo' : 'hudba';
    $start = sprintf('2026-08-%02dT%02d:00:00+02:00', 3 + (($index - 1) % 7), 10 + ($index % 8));
    $insertEvent->execute([
        ':id' => $id,
        ':title' => $title,
        ':description' => 'Ověřený popis události pro deterministický test webu.',
        ':start' => $start,
        ':end' => $index === 1 ? '2026-08-03T20:00:00+02:00' : null,
        ':venue' => $index === 1 ? 'Zámek Pardubice' : 'Kulturní dům',
        ':municipality' => $municipality,
        ':price' => $index === 1 ? 'free' : 'unknown',
        ':price_text' => $index === 1 ? 'Zdarma' : null,
        ':url' => 'https://example.test/' . $id,
        ':norm' => strtolower($title),
    ]);
    $insertWeek->execute([$id, $index]);
    $insertCategory->execute([$id, $category, $category]);
    $insertSource->execute([$id, 'https://example.test/' . $id]);
    $insertFts->execute([$id, $title, 'Ověřený popis', 'Kulturní dům',
        $municipality, $category]);
}

putenv('PARDUBICKO_INBOX_TOKEN=test-token');
putenv('PARDUBICKO_BASE_URL=https://akce.test');
$_ENV['PARDUBICKO_INBOX_TOKEN'] = $_SERVER['PARDUBICKO_INBOX_TOKEN'] = 'test-token';
$_ENV['PARDUBICKO_BASE_URL'] = $_SERVER['PARDUBICKO_BASE_URL'] = 'https://akce.test';
$now = new DateTimeImmutable('2026-08-03T12:00:00+02:00');
$app = new Application($pdo, $pdo, $now);

$home = $app->handle('GET', '/');
check('přehled vrací 200', $home->status, 200);
contains('přehled je hotové HTML', $home->body, '<h1>Akce v týdnu');
contains('přehled má GET formulář', $home->body, '<form class="filters" action="/hledat" method="get"');
contains('přehled má stránkování bez JS', $home->body, 'strana=2');
check('bez klientského skriptu', str_contains($home->body, '<script src='), false);

$repository = new EventRepository($pdo, $now);
$music = $repository->search(EventFilter::fromQuery(['kategorie' => 'hudba']));
check('SQL filtr používá kanonickou kategorii', count($music), 13);
check('SQL kategorie nese label', $music[0]['categories'][0]['label'], 'Hudba');
$unknownCategory = $repository->count(EventFilter::fromQuery(['kategorie' => 'neexistuje']));
check('neznámá kategorie nevrátí akce', $unknownCategory, 0);

$pageTwo = $app->handle('GET', '/hledat', ['strana' => '2']);
check('druhá stránka vrací 200', $pageTwo->status, 200);
contains('druhá stránka zachová URL stav', $pageTwo->body, 'Strana 2 z 2');
$search = $app->handle('GET', '/hledat', ['q' => 'koncert']);
contains('FTS hledání najde koncert', $search->body, 'Letní koncert na zámku');
check('FTS hledání nefiltruje v PHP', str_contains($search->body, 'Testovací akce 02'), false);

$municipality = $app->handle('GET', '/obec/pardubice');
check('stránka obce vrací 200', $municipality->status, 200);
contains('stránka obce má název', $municipality->body, 'Akce v obci Pardubice');
$calendar = $app->handle('GET', '/kalendar/2026-W32');
check('kalendář vrací 200', $calendar->status, 200);
contains('kalendář má týden', $calendar->body, '2026-W32');
contains('kalendář má sedmý den', $calendar->body, 'neděle 9. 8.');

$detail = $app->handle('GET', '/akce/letni-koncert-pardubice');
check('detail vrací 200', $detail->status, 200);
contains('detail má kanonický odkaz', $detail->body,
    '<link rel="canonical" href="https://akce.test/akce/letni-koncert-pardubice">');
contains('zdroj akce se otevírá v novém okně', $detail->body,
    'href="https://example.test/letni-koncert-pardubice" target="_blank" rel="noopener noreferrer">Otevřít zdroj akce</a>');
preg_match('~<script type="application/ld\+json">(.*?)</script>~s', $detail->body, $jsonMatch);
$jsonLd = json_decode($jsonMatch[1] ?? '', true);
check('JSON-LD je validní JSON', is_array($jsonLd), true);
check('JSON-LD je schema.org Event', $jsonLd['@type'] ?? null, 'Event');
check('JSON-LD má startDate', $jsonLd['startDate'] ?? null, '2026-08-03T11:00:00+02:00');
check('JSON-LD označí vstup zdarma', $jsonLd['isAccessibleForFree'] ?? null, true);

$legacy = $app->handle('GET', '/', ['event' => 'letni-koncert-pardubice']);
check('legacy URL přesměruje', $legacy->status, 301);
check('legacy URL míří na detail', $legacy->headers['Location'] ?? null,
    '/akce/letni-koncert-pardubice');
$sitemap = $app->handle('GET', '/sitemap.xml');
check('sitemapa je XML', $sitemap->headers['Content-Type'] ?? null,
    'application/xml; charset=utf-8');
contains('sitemapa obsahuje detail', $sitemap->body,
    'https://akce.test/akce/letni-koncert-pardubice');
contains('kořen sitemap používá datum generování manifestu', $sitemap->body,
    '<loc>https://akce.test/</loc><lastmod>2026-08-03</lastmod>');
$robots = $app->handle('GET', '/robots.txt');
contains('robots odkazuje sitemapu', $robots->body, 'https://akce.test/sitemap.xml');

$health = $app->handle('GET', '/api/health');
check('health vrací 200', $health->status, 200);
$healthJson = json_decode($health->body, true);
check('health hlásí ok', $healthJson['status'] ?? null, 'ok');
check('health obsahuje zdroj', $healthJson['sources'][0]['id'] ?? null, 'pardubice-calendar');

$unauthorized = $app->handle('POST', '/api/inbox', [], [],
    json_encode(['url' => 'https://example.test/tip']) ?: '');
check('inbox bez tokenu vrací 401', $unauthorized->status, 401);
$authorizedHeaders = ['Authorization' => 'Bearer test-token'];
$created = $app->handle('POST', '/api/inbox', [], $authorizedHeaders,
    json_encode(['url' => 'http://example.test/tip?utm_source=test']) ?: '');
check('nový inbox vrací 202', $created->status, 202);
$createdJson = json_decode($created->body, true);
check('nový inbox není duplicita', $createdJson['duplicate'] ?? null, false);
$duplicate = $app->handle('POST', '/api/inbox', [], $authorizedHeaders,
    json_encode(['url' => 'https://example.test/tip?fbclid=abc']) ?: '');
check('duplicitní inbox vrací 200', $duplicate->status, 200);
$duplicateJson = json_decode($duplicate->body, true);
check('duplicitní inbox je označen', $duplicateJson['duplicate'] ?? null, true);
check('inbox vytvořil jediný řádek', (int) $pdo->query('SELECT COUNT(*) FROM inbox')->fetchColumn(), 1);
check('normalizace URL shodí tracking', UrlNormalizer::normalize(
    'http://Example.test/akce/?utm_source=x&b=2&a=1'),
    'https://example.test/akce?a=1&b=2');

$missing = $app->handle('GET', '/neexistuje');
check('neznámá cesta vrací 404', $missing->status, 404);
$wrongMethod = $app->handle('POST', '/robots.txt');
check('nesprávná metoda vrací 405', $wrongMethod->status, 405);

if ($failures !== []) {
    echo "\nNEPROŠLO " . count($failures) . " kontrol:\n";
    foreach ($failures as $failure) {
        echo " - {$failure}\n";
    }
    exit(1);
}

echo "\nVšechny deterministické PHP testy prošly.\n";
