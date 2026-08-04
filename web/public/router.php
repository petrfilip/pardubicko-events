<?php

declare(strict_types=1);

$path = parse_url((string) ($_SERVER['REQUEST_URI'] ?? '/'), PHP_URL_PATH) ?: '/';
$candidate = realpath(__DIR__ . rawurldecode($path));
if ($path !== '/' && $candidate !== false && str_starts_with($candidate, __DIR__)
    && is_file($candidate)) {
    return false;
}

require __DIR__ . '/index.php';
