<?php

declare(strict_types=1);

/**
 * Zavedení aplikace: autoloader, časová zóna a globální pomocníci.
 *
 * Bez Composeru. Mapování jmenného prostoru `Pardubicko\` na `web/src/`
 * obstarává jednoduchý autoloader; žádná externí závislost není potřeba.
 */

spl_autoload_register(static function (string $class): void {
    $prefix = 'Pardubicko\\';
    if (!str_starts_with($class, $prefix)) {
        return;
    }

    $relative = substr($class, strlen($prefix));
    $path = __DIR__ . '/' . str_replace('\\', '/', $relative) . '.php';
    if (is_file($path)) {
        require $path;
    }
});

date_default_timezone_set(\Pardubicko\Config::TIMEZONE);

if (!function_exists('e')) {
    /**
     * Escapování textu do HTML. Používá se na veškerý výstup z databáze
     * i z requestu; šablony nikdy nevypisují hodnotu přímo.
     */
    function e(?string $value): string
    {
        return htmlspecialchars((string) $value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
    }
}
