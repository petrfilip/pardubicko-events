<?php

declare(strict_types=1);

use Pardubicko\Application;
use Pardubicko\Database;
use Pardubicko\Response;

require dirname(__DIR__) . '/src/bootstrap.php';

$requestMethod = strtoupper((string) ($_SERVER['REQUEST_METHOD'] ?? 'GET'));
$dispatchMethod = $requestMethod === 'HEAD' ? 'GET' : $requestMethod;
$requestPath = parse_url((string) ($_SERVER['REQUEST_URI'] ?? '/'), PHP_URL_PATH) ?: '/';
$requestPath = rawurldecode($requestPath);
$headers = function_exists('getallheaders') ? getallheaders() : [];
if (!is_array($headers)) {
    $headers = [];
}
if (!isset($headers['Authorization']) && isset($_SERVER['HTTP_AUTHORIZATION'])) {
    $headers['Authorization'] = (string) $_SERVER['HTTP_AUTHORIZATION'];
}
$body = (string) file_get_contents('php://input');

try {
    $writer = $dispatchMethod === 'POST' && $requestPath === '/api/inbox'
        ? Database::writer() : null;
    $application = new Application(Database::reader(), $writer);
    $response = $application->handle(
        $dispatchMethod,
        $requestPath,
        $_GET,
        array_map('strval', $headers),
        $body,
    );
} catch (Throwable) {
    $response = str_starts_with($requestPath, '/api/')
        ? Response::json(['status' => 'error', 'error' => 'Aplikace není dostupná.'], 500)
        : Response::html('<!doctype html><html lang="cs"><meta charset="utf-8">'
            . '<title>Aplikace není dostupná</title><h1>Aplikace není dostupná</h1>'
            . '<p>Zkuste požadavek opakovat později.</p>', 500);
}

$response->send($requestMethod !== 'HEAD');
