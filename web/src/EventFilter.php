<?php

declare(strict_types=1);

namespace Pardubicko;

use DateTimeImmutable;

/**
 * Stav filtrů. Celý pochází z URL a celý se do URL vrací — stránka je
 * proto sdílitelná, uložitelná do záložek a indexovatelná.
 *
 * Objekt drží jen hodnoty a umí z nich složit adresu. Vyhodnocení filtrů
 * probíhá výhradně v SQL (`EventRepository`); tady žádná druhá
 * implementace filtrování není.
 */
final class EventFilter
{
    public const PRICES = ['free' => 'Zdarma', 'paid' => 'Placené', 'unknown' => 'Neuvedeno'];

    private const MAX_TEXT = 120;

    public function __construct(
        public readonly string $query = '',
        public readonly string $municipality = '',
        public readonly string $category = '',
        public readonly string $price = '',
        public readonly string $from = '',
        public readonly string $to = '',
        public readonly bool $futureOnly = false,
        public readonly string $week = '',
        public readonly int $page = 1,
    ) {
    }

    /** @param array<string, mixed> $get */
    public static function fromQuery(array $get): self
    {
        return new self(
            query: self::text($get['q'] ?? null),
            municipality: Slug::make(self::text($get['obec'] ?? null)),
            category: self::text($get['kategorie'] ?? null),
            price: self::choice(self::text($get['cena'] ?? null), array_keys(self::PRICES)),
            from: self::date(self::text($get['od'] ?? null)),
            to: self::date(self::text($get['do'] ?? null)),
            futureOnly: in_array(self::text($get['budouci'] ?? null), ['1', 'ano', 'true'], true),
            week: self::week(self::text($get['tyden'] ?? null)),
            page: max(1, (int) self::text($get['strana'] ?? null)),
        );
    }

    /** @param array<string, mixed> $changes */
    public function with(array $changes): self
    {
        return new self(
            query: array_key_exists('query', $changes) ? (string) $changes['query'] : $this->query,
            municipality: array_key_exists('municipality', $changes) ? (string) $changes['municipality'] : $this->municipality,
            category: array_key_exists('category', $changes) ? (string) $changes['category'] : $this->category,
            price: array_key_exists('price', $changes) ? (string) $changes['price'] : $this->price,
            from: array_key_exists('from', $changes) ? (string) $changes['from'] : $this->from,
            to: array_key_exists('to', $changes) ? (string) $changes['to'] : $this->to,
            futureOnly: array_key_exists('futureOnly', $changes) ? (bool) $changes['futureOnly'] : $this->futureOnly,
            week: array_key_exists('week', $changes) ? (string) $changes['week'] : $this->week,
            // Změna kteréhokoli filtru vrací stránkování na začátek.
            page: array_key_exists('page', $changes) ? max(1, (int) $changes['page']) : 1,
        );
    }

    /** @return array<string, string> */
    public function toQuery(): array
    {
        $parameters = [
            'q' => $this->query,
            'obec' => $this->municipality,
            'kategorie' => $this->category,
            'cena' => $this->price,
            'od' => $this->from,
            'do' => $this->to,
            'budouci' => $this->futureOnly ? '1' : '',
            'tyden' => $this->week,
            'strana' => $this->page > 1 ? (string) $this->page : '',
        ];

        return array_filter($parameters, static fn (string $value): bool => $value !== '');
    }

    /**
     * Adresa stránky se stavem filtrů. `$changes` přepíše jednotlivé
     * hodnoty, `$drop` vypustí klíče, které daná stránka nese v cestě.
     *
     * @param array<string, mixed> $changes
     * @param list<string> $drop
     */
    public function url(string $path, array $changes = [], array $drop = []): string
    {
        $parameters = ($changes === [] ? $this : $this->with($changes))->toQuery();
        foreach ($drop as $key) {
            unset($parameters[$key]);
        }

        return $parameters === [] ? $path : $path . '?' . http_build_query($parameters);
    }

    /** Je aktivní alespoň jeden filtr obsahu (bez týdne a stránkování)? */
    public function isActive(): bool
    {
        return $this->query !== ''
            || $this->municipality !== ''
            || $this->category !== ''
            || $this->price !== ''
            || $this->from !== ''
            || $this->to !== ''
            || $this->futureOnly;
    }

    /** Je aktivní filtr schovaný pod „Další filtry“? Pak se blok rozbalí. */
    public function hasAdvanced(): bool
    {
        return $this->municipality !== ''
            || $this->category !== ''
            || $this->price !== ''
            || $this->futureOnly;
    }

    /** Odpovídá rozsah datumů některé z rychlých voleb? */
    public function matchesRange(string $from, string $to): bool
    {
        return $this->from === $from && $this->to === $to;
    }

    private static function text(mixed $value): string
    {
        if (!is_string($value)) {
            return '';
        }

        return mb_substr(trim($value), 0, self::MAX_TEXT, 'UTF-8');
    }

    /** @param list<string> $allowed */
    private static function choice(string $value, array $allowed): string
    {
        return in_array($value, $allowed, true) ? $value : '';
    }

    private static function date(string $value): string
    {
        if (preg_match('/^\d{4}-\d{2}-\d{2}$/', $value) !== 1) {
            return '';
        }

        $parsed = DateTimeImmutable::createFromFormat('!Y-m-d', $value, Format::zone());

        return $parsed !== false && $parsed->format('Y-m-d') === $value ? $value : '';
    }

    private static function week(string $value): string
    {
        return preg_match('/^\d{4}-W\d{2}$/', $value) === 1 ? $value : '';
    }
}
