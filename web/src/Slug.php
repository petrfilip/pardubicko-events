<?php

declare(strict_types=1);

namespace Pardubicko;

/**
 * Převod textu na část URL a na stabilní ID akce.
 *
 * Akce nesou název obce doslovně v `event.municipality_name`. Slug obce se
 * proto počítá zde a při dotazu se překládá zpět na název; do SQL jde vždy
 * název jako parametr.
 */
final class Slug
{
    public static function make(string $value): string
    {
        $slug = preg_replace('/[^a-z0-9]+/u', '-', Text::ascii($value)) ?? '';

        return trim($slug, '-');
    }
}
