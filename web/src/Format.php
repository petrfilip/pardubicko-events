<?php

declare(strict_types=1);

namespace Pardubicko;

use DateTimeImmutable;
use DateTimeZone;

/**
 * Formátování termínů, cen a popisků do češtiny.
 *
 * Obdoba `js/format.js` z fáze 1, ale bez rozšíření `intl` — názvy dnů
 * jsou vypsané, aby výstup nezávisel na tom, jaké locale je v kontejneru.
 * Formáty odpovídají fázi 1: „12. 6. 2026“, „18:00“, „pondělí 3. 8.“.
 */
final class Format
{
    private const WEEKDAYS = [
        1 => 'pondělí',
        2 => 'úterý',
        3 => 'středa',
        4 => 'čtvrtek',
        5 => 'pátek',
        6 => 'sobota',
        7 => 'neděle',
    ];

    private const SOURCE_LABELS = [
        'official' => 'Oficiální zdroj',
        'official-calendar' => 'Oficiální kalendář',
        'official-organizer' => 'Oficiální stránka pořadatele',
        'facebook' => 'Facebook Event',
        'ticketing' => 'Prodej vstupenek',
        'regional' => 'Regionální kalendář',
        'local-organizer' => 'Místní pořadatel',
    ];

    public static function zone(): DateTimeZone
    {
        return new DateTimeZone(Config::TIMEZONE);
    }

    /** Převede uložený ISO 8601 řetězec na čas v místní zóně. */
    public static function moment(string $value): DateTimeImmutable
    {
        return (new DateTimeImmutable($value))->setTimezone(self::zone());
    }

    public static function start(array $event): DateTimeImmutable
    {
        return self::moment((string) $event['start_at']);
    }

    public static function end(array $event): DateTimeImmutable
    {
        $end = $event['end_at'] ?? null;

        return is_string($end) && $end !== '' ? self::moment($end) : self::start($event);
    }

    public static function date(DateTimeImmutable $moment): string
    {
        return $moment->format('j. n. Y');
    }

    public static function time(DateTimeImmutable $moment): string
    {
        return $moment->format('H:i');
    }

    public static function dateKey(DateTimeImmutable $moment): string
    {
        return $moment->format('Y-m-d');
    }

    public static function dayHeading(DateTimeImmutable $day): string
    {
        return self::WEEKDAYS[(int) $day->format('N')] . ' ' . $day->format('j. n.');
    }

    /**
     * Termín akce ve dvou řádcích, stejně jako `formatEventWhen()` ve fázi 1.
     *
     * @return list<string>
     */
    public static function whenLines(array $event): array
    {
        $start = self::start($event);
        $hasEnd = isset($event['end_at']) && is_string($event['end_at']) && $event['end_at'] !== '';
        $end = $hasEnd ? self::end($event) : null;
        $sameDay = $end !== null && self::dateKey($start) === self::dateKey($end);

        if (!empty($event['all_day'])) {
            if ($end !== null && !$sameDay) {
                return [self::date($start) . ' – ' . self::date($end), 'celý den'];
            }

            return [self::date($start), 'celý den'];
        }

        if ($end !== null && !$sameDay) {
            return [
                self::date($start) . ' ' . self::time($start),
                '– ' . self::date($end) . ' ' . self::time($end),
            ];
        }

        if ($end !== null) {
            return [self::date($start), self::time($start) . '–' . self::time($end)];
        }

        return [self::date($start), 'od ' . self::time($start)];
    }

    public static function when(array $event): string
    {
        return implode(', ', self::whenLines($event));
    }

    /** Čas akce v konkrétním dni kalendáře. */
    public static function calendarTime(array $event, DateTimeImmutable $day): string
    {
        if (!empty($event['all_day'])) {
            return 'celý den';
        }

        $start = self::start($event);
        $dayKey = self::dateKey($day);
        if (self::dateKey($start) === $dayKey) {
            return self::time($start);
        }

        $hasEnd = isset($event['end_at']) && is_string($event['end_at']) && $event['end_at'] !== '';
        if ($hasEnd && self::dateKey(self::end($event)) === $dayKey) {
            return 'do ' . self::time(self::end($event));
        }

        return 'pokračuje';
    }

    public static function price(array $event): string
    {
        $text = $event['price_text'] ?? null;
        if (is_string($text) && trim($text) !== '') {
            return $text;
        }

        return match ($event['price_type'] ?? 'unknown') {
            'free' => 'Zdarma',
            'paid' => 'Placené',
            default => 'Vstupné neuvedeno',
        };
    }

    public static function sourceLabel(?string $type): string
    {
        return self::SOURCE_LABELS[$type ?? ''] ?? 'Zdroj akce';
    }

    /** Stav akce odvozený v SQL, přeložený do popisku. */
    public static function stateLabel(string $state): string
    {
        return match ($state) {
            'ongoing' => 'Probíhá',
            'past' => 'Proběhlo',
            default => '',
        };
    }

    public static function eventCountLabel(int $count): string
    {
        if ($count === 1) {
            return '1 akce';
        }
        if ($count >= 2 && $count <= 4) {
            return $count . ' akce';
        }

        return $count . ' akcí';
    }

    public static function weekLabel(array $week): string
    {
        $from = self::moment((string) $week['date_from'] . 'T12:00:00');
        $to = self::moment((string) $week['date_to'] . 'T12:00:00');

        return self::date($from) . ' – ' . self::date($to);
    }

    public static function weekRange(array $week): string
    {
        return self::weekLabel($week) . ' (' . $week['id'] . ')';
    }

    public static function updated(?string $value): string
    {
        if ($value === null || $value === '') {
            return '';
        }

        return self::moment($value)->format('j. n. Y H:i');
    }
}
