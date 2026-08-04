<?php

declare(strict_types=1);

$base = rtrim($argv[1] ?? 'http://app-smoke:8081', '/');
$failures = [];

/** @return array{status: int, body: string, headers: list<string>} */
function request(string $method, string $url, ?string $body = null, array $headers = []): array
{
    $options = [
        'method' => $method,
        'ignore_errors' => true,
        'timeout' => 5,
        'follow_location' => 0,
        'header' => implode("\r\n", $headers),
    ];
    if ($body !== null) {
        $options['content'] = $body;
    }
    $result = @file_get_contents($url, false, stream_context_create(['http' => $options]));
    $responseHeaders = $http_response_header ?? [];
    preg_match('~HTTP/\S+\s+(\d{3})~', $responseHeaders[0] ?? '', $match);

    return [
        'status' => isset($match[1]) ? (int) $match[1] : 0,
        'body' => is_string($result) ? $result : '',
        'headers' => $responseHeaders,
    ];
}

function check(string $label, bool $condition): void
{
    global $failures;
    if (!$condition) {
        $failures[] = $label;
    } else {
        echo "OK  {$label}\n";
    }
}

$home = request('GET', $base . '/');
check('GET / vrací 200 a HTML', $home['status'] === 200 && str_contains($home['body'], '<main'));
check('GET / funguje bez klientského JS', !str_contains($home['body'], '<script src='));
check('CSS asset je dostupný', request('GET', $base . '/assets/styles.css')['status'] === 200);
check('GET /hledat s filtry', request('GET', $base . '/hledat?obec=pardubice&budouci=1')['status'] === 200);
check('GET /obec/pardubice', request('GET', $base . '/obec/pardubice')['status'] === 200);
check('GET /kalendar/2026-W32', request('GET', $base . '/kalendar/2026-W32')['status'] === 200);

$sitemap = request('GET', $base . '/sitemap.xml');
preg_match('~<loc>[^<]+/akce/([^<]+)</loc>~', $sitemap['body'], $eventMatch);
$eventId = isset($eventMatch[1]) ? rawurldecode($eventMatch[1]) : '';
check('sitemap.xml vrací publikované detaily', $sitemap['status'] === 200 && $eventId !== '');
$detail = request('GET', $base . '/akce/' . rawurlencode($eventId));
preg_match('~<script type="application/ld\+json">(.*?)</script>~s', $detail['body'], $jsonMatch);
$jsonLd = json_decode($jsonMatch[1] ?? '', true);
check('detail má validní JSON-LD Event', $detail['status'] === 200
    && is_array($jsonLd) && ($jsonLd['@type'] ?? null) === 'Event');

$legacy = request('GET', $base . '/?event=' . rawurlencode($eventId));
check('legacy ?event vrací 301', $legacy['status'] === 301);
check('robots.txt je dostupný', request('GET', $base . '/robots.txt')['status'] === 200);
$health = request('GET', $base . '/api/health');
$healthJson = json_decode($health['body'], true);
check('health endpoint vrací zdroje', $health['status'] === 200
    && ($healthJson['status'] ?? null) === 'ok' && is_array($healthJson['sources'] ?? null));

$smokeId = 'deterministicky-http-smoke';
$payload = json_encode(['url' => 'https://smoke.example.test/akce-' . $smokeId . '?utm_source=smoke']);
$unauthorized = request('POST', $base . '/api/inbox', $payload,
    ['Content-Type: application/json']);
check('inbox bez tokenu vrací 401', $unauthorized['status'] === 401);
$authorizedHeaders = ['Content-Type: application/json', 'Authorization: Bearer smoke-token'];
$created = request('POST', $base . '/api/inbox', $payload, $authorizedHeaders);
check('inbox s tokenem vrací 202', $created['status'] === 202);
$duplicate = request('POST', $base . '/api/inbox',
    json_encode(['url' => 'http://smoke.example.test/akce-' . $smokeId . '?fbclid=x']), $authorizedHeaders);
check('duplicitní inbox vrací 200', $duplicate['status'] === 200);
check('neznámá cesta vrací 404', request('GET', $base . '/neni')['status'] === 404);

if ($failures !== []) {
    echo "\nHTTP SMOKE NEPROŠEL " . count($failures) . " kontrol:\n";
    foreach ($failures as $failure) {
        echo " - {$failure}\n";
    }
    exit(1);
}

echo "\nHTTP smoke test prošel.\n";
