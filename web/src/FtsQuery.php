<?php

declare(strict_types=1);

namespace Pardubicko;

/**
 * Převod hledaného textu na výraz pro FTS5.
 *
 * Text z požadavku se nikdy nevkládá do SQL — výsledný výraz jde do dotazu
 * jako vázaný parametr `MATCH ?`. Přesto se z něj musí odstranit znaky se
 * zvláštním významem ve syntaxi FTS5 (`"`, `*`, `:`, `^`, `-`, `NEAR`),
 * jinak by uživatel neúmyslně nebo úmyslně sestavil neplatný dotaz a
 * SQLite by vrátil chybu.
 *
 * Postup: ponechají se jen písmena a číslice, každé slovo se uzavře do
 * uvozovek a doplní hvězdičkou pro shodu na začátku slova. Tokenizer
 * `unicode61 remove_diacritics 2` zajistí, že „ridic“ najde „řidič“.
 */
final class FtsQuery
{
    private const MAX_TOKENS = 8;

    public static function fromUserInput(string $input): ?string
    {
        $normalized = preg_replace('/[^\p{L}\p{N}]+/u', ' ', $input) ?? '';
        $tokens = preg_split('/\s+/u', trim($normalized), -1, PREG_SPLIT_NO_EMPTY);
        if ($tokens === false || $tokens === []) {
            return null;
        }

        $tokens = array_slice($tokens, 0, self::MAX_TOKENS);
        $phrases = array_map(static fn (string $token): string => '"' . $token . '"*', $tokens);

        // Mezera mezi výrazy znamená ve FTS5 implicitní AND.
        return implode(' ', $phrases);
    }
}
