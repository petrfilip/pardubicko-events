<?php

declare(strict_types=1);

namespace Pardubicko;

/**
 * Normalizace textu pro porovnávání a URL.
 *
 * Transliterace je explicitní tabulka, ne `iconv('ASCII//TRANSLIT')` ani
 * `Normalizer`: výsledek `iconv` závisí na locale a rozšíření `intl` není
 * v testovacím prostředí. Tabulka pokrývá češtinu, slovenštinu a běžné
 * znaky sousedních jazyků.
 */
final class Text
{
    private const TRANSLITERATION = [
        'á' => 'a', 'č' => 'c', 'ď' => 'd', 'é' => 'e', 'ě' => 'e', 'í' => 'i',
        'ň' => 'n', 'ó' => 'o', 'ř' => 'r', 'š' => 's', 'ť' => 't', 'ú' => 'u',
        'ů' => 'u', 'ý' => 'y', 'ž' => 'z', 'ä' => 'a', 'ë' => 'e', 'ö' => 'o',
        'ü' => 'u', 'ô' => 'o', 'ĺ' => 'l', 'ľ' => 'l', 'ŕ' => 'r', 'à' => 'a',
        'â' => 'a', 'ç' => 'c', 'è' => 'e', 'ê' => 'e', 'î' => 'i', 'ï' => 'i',
        'ł' => 'l', 'ń' => 'n', 'ś' => 's', 'ź' => 'z', 'ż' => 'z', 'ß' => 'ss',
    ];

    /** Malá písmena bez diakritiky; ostatní znaky zůstávají. */
    public static function ascii(string $value): string
    {
        return strtr(mb_strtolower(trim($value), 'UTF-8'), self::TRANSLITERATION);
    }

    /**
     * Klíč pro porovnání: bez diakritiky, interpunkce a vícenásobných
     * mezer. Odpovídá `normalize_text` v `tools/pipeline/matching.py`.
     */
    public static function fold(?string $value): string
    {
        $ascii = self::ascii((string) $value);
        $words = preg_replace('/[^a-z0-9]+/', ' ', $ascii) ?? '';

        return trim(preg_replace('/ +/', ' ', $words) ?? '');
    }
}
