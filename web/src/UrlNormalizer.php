<?php

declare(strict_types=1);

namespace Pardubicko;

use InvalidArgumentException;

/** Deterministický klíč URL pro HTTP inbox, shodný s pravidly ADR 0005. */
final class UrlNormalizer
{
    private const TRACKING = [
        'fbclid', 'gclid', 'gbraid', 'wbraid', 'dclid', 'msclkid', 'yclid',
        'igshid', 'mc_cid', 'mc_eid', '_ga', '_gl', 'ref_src', 'ref_url',
        'vgo_ee', 'oly_enc_id', 'oly_anon_id',
    ];

    public static function normalize(string $raw): string
    {
        $value = trim($raw, " \t\n\r\0\x0B<>");
        if ($value === '' || preg_match('/\s/u', $value) === 1) {
            throw new InvalidArgumentException('Odkaz je prázdný nebo obsahuje mezeru.');
        }
        if (preg_match('~^[a-z][a-z0-9+.-]*:~i', $value) !== 1) {
            $value = 'https://' . $value;
        }

        $parts = parse_url($value);
        if ($parts === false || !isset($parts['host'])) {
            throw new InvalidArgumentException('Odkaz nemá použitelné doménové jméno.');
        }
        $scheme = strtolower((string) ($parts['scheme'] ?? 'https'));
        if (!in_array($scheme, ['http', 'https'], true)) {
            throw new InvalidArgumentException('Inbox přijímá jen odkazy http a https.');
        }
        if (isset($parts['user']) || isset($parts['pass'])) {
            throw new InvalidArgumentException('Odkaz nesmí obsahovat přihlašovací údaje.');
        }

        $host = mb_strtolower(rtrim((string) $parts['host'], '.'), 'UTF-8');
        if ($host === '' || !str_contains($host, '.')) {
            throw new InvalidArgumentException('Odkaz nemá použitelné doménové jméno.');
        }
        if (function_exists('idn_to_ascii')) {
            $ascii = idn_to_ascii($host, IDNA_DEFAULT, INTL_IDNA_VARIANT_UTS46);
            if (is_string($ascii) && $ascii !== '') {
                $host = strtolower($ascii);
            }
        }

        $port = isset($parts['port']) ? (int) $parts['port'] : null;
        $netloc = $host;
        if ($port !== null && !(($scheme === 'http' && $port === 80)
                || ($scheme === 'https' && $port === 443))) {
            $netloc .= ':' . $port;
        }

        $path = self::path((string) ($parts['path'] ?? '/'));
        $parameters = [];
        parse_str((string) ($parts['query'] ?? ''), $parsedQuery);
        foreach ($parsedQuery as $name => $queryValue) {
            $lower = strtolower((string) $name);
            if (in_array($lower, self::TRACKING, true) || str_starts_with($lower, 'utm_')) {
                continue;
            }
            if (is_array($queryValue)) {
                foreach ($queryValue as $index => $nested) {
                    $parameters[(string) $name . '[' . $index . ']'] = (string) $nested;
                }
            } else {
                $parameters[(string) $name] = (string) $queryValue;
            }
        }
        ksort($parameters, SORT_STRING);
        $query = http_build_query($parameters, '', '&', PHP_QUERY_RFC3986);

        $fragment = (string) ($parts['fragment'] ?? '');
        $fragment = str_starts_with($fragment, '!') ? '#' . $fragment : '';

        return 'https://' . $netloc . $path . ($query === '' ? '' : '?' . $query) . $fragment;
    }

    private static function path(string $path): string
    {
        if ($path === '') {
            return '/';
        }
        $segments = explode('/', $path);
        $encoded = array_map(static function (string $segment): string {
            $decoded = rawurldecode($segment);
            return rawurlencode($decoded);
        }, $segments);
        $normalized = implode('/', $encoded);
        if (!str_starts_with($normalized, '/')) {
            $normalized = '/' . $normalized;
        }
        if (strlen($normalized) > 1) {
            $normalized = rtrim($normalized, '/');
        }

        return $normalized === '' ? '/' : $normalized;
    }
}
