<?php

declare(strict_types=1);

namespace Pardubicko;

/**
 * Převod názvu obce na část URL.
 *
 * Databáze zatím slug neukládá — `municipality` je prázdná a akce nesou
 * název obce doslovně v `event.municipality_name` (viz komentář ve
 * `tools/pipeline/schema.sql`). Slug se proto počítá zde a při dotazu se
 * překládá zpět na název; do SQL jde vždy název jako parametr.
 *
 * Transliterace je explicitní tabulka, ne `iconv('ASCII//TRANSLIT')`,
 * jehož výsledek závisí na locale v kontejneru.
 */
final class Slug
{
    private const TRANSLITERATION = [
        'á' => 'a', 'č' => 'c', 'ď' => 'd', 'é' => 'e', 'ě' => 'e', 'í' => 'i',
        'ň' => 'n', 'ó' => 'o', 'ř' => 'r', 'š' => 's', 'ť' => 't', 'ú' => 'u',
        'ů' => 'u', 'ý' => 'y', 'ž' => 'z', 'ä' => 'a', 'ë' => 'e', 'ö' => 'o',
        'ü' => 'u', 'ô' => 'o', 'ĺ' => 'l', 'ľ' => 'l', 'ŕ' => 'r', 'à' => 'a',
        'â' => 'a', 'ç' => 'c', 'è' => 'e', 'ê' => 'e', 'î' => 'i', 'ï' => 'i',
        'ł' => 'l', 'ń' => 'n', 'ś' => 's', 'ź' => 'z', 'ż' => 'z', 'ß' => 'ss',
    ];

    public static function make(string $value): string
    {
        $lowercase = mb_strtolower(trim($value), 'UTF-8');
        $ascii = strtr($lowercase, self::TRANSLITERATION);
        $slug = preg_replace('/[^a-z0-9]+/u', '-', $ascii) ?? '';

        return trim($slug, '-');
    }
}
