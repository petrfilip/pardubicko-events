<?php

declare(strict_types=1);

namespace Pardubicko;

/**
 * Směrování podle metody a cesty. Cesty jsou uzavřený seznam regulárních
 * výrazů; nic se nesestavuje z požadavku.
 */
final class Router
{
    /** @var list<array{method: string, pattern: string, handler: callable}> */
    private array $routes = [];

    public function get(string $pattern, callable $handler): void
    {
        $this->routes[] = ['method' => 'GET', 'pattern' => $pattern, 'handler' => $handler];
    }

    public function post(string $pattern, callable $handler): void
    {
        $this->routes[] = ['method' => 'POST', 'pattern' => $pattern, 'handler' => $handler];
    }

    /**
     * Vrátí odpověď, nebo `null`, pokud cesta neodpovídá žádné trase.
     * Shoda cesty při neshodě metody končí kódem 405.
     */
    public function dispatch(string $method, string $path): ?Response
    {
        $pathMatched = false;

        foreach ($this->routes as $route) {
            if (preg_match($route['pattern'], $path, $matches) !== 1) {
                continue;
            }

            $pathMatched = true;
            if ($route['method'] !== $method) {
                continue;
            }

            $parameters = array_filter($matches, 'is_string', ARRAY_FILTER_USE_KEY);

            return ($route['handler'])($parameters);
        }

        if ($pathMatched) {
            return Response::text("405 – metoda není povolena\n", 405);
        }

        return null;
    }
}
