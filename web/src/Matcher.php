<?php

declare(strict_types=1);

namespace Pardubicko;

use DateTimeImmutable;
use Exception;
use PDO;

/**
 * Deduplikace s blokem a třemi rozhodovacími pásmy.
 *
 * Věrný převod `tools/pipeline/matching.py`; prahy a váhy se musí měnit na
 * obou místech, dokud Python verze existuje. Kalibrace je popsaná v
 * `docs/deduplication-calibration.md`.
 *
 * Blok je stejné datum začátku a stejná obec z číselníku. Bez obce se
 * nepárují žádné akce: shoda názvu napříč obcemi není důkaz totožnosti.
 */
final class Matcher
{
    public const AUTO_THRESHOLD = 0.92;
    public const REVIEW_THRESHOLD = 0.75;

    public function __construct(private readonly PDO $pdo)
    {
    }

    /**
     * Publikované akce ze stejného bloku seřazené podle skóre.
     *
     * @param array{title?: ?string, start_at?: ?string, venue?: ?string, municipality_id?: ?int} $item
     * @return list<array{event_id: string, score: float, decision: string, breakdown: array<string, mixed>}>
     */
    public function matches(array $item, ?string $excludeId = null): array
    {
        $day = self::day($item['start_at'] ?? null);
        if ($day === null || ($item['municipality_id'] ?? null) === null) {
            return [];
        }
        $statement = $this->pdo->prepare(
            "SELECT id, title, start_at, venue FROM event
             WHERE substr(start_at, 1, 10) = :day AND municipality_id = :municipality
               AND status = 'published'",
        );
        $statement->execute([':day' => $day, ':municipality' => $item['municipality_id']]);

        $ranked = [];
        foreach ($statement->fetchAll() as $row) {
            if ($row['id'] === $excludeId) {
                continue;
            }
            $breakdown = self::score($item, $row);
            $ranked[] = [
                'event_id' => (string) $row['id'],
                'score' => $breakdown['score'],
                'decision' => $breakdown['decision'],
                'breakdown' => $breakdown,
            ];
        }
        usort($ranked, static fn (array $a, array $b): int =>
            [$b['score'], $a['event_id']] <=> [$a['score'], $b['event_id']]);

        return $ranked;
    }

    /**
     * @param array<string, mixed> $left
     * @param array<string, mixed> $right
     * @return array{score: float, decision: string, title: float, venue: ?float, time: float}
     */
    public static function score(array $left, array $right): array
    {
        $title = self::jaroWinkler($left['title'] ?? null, $right['title'] ?? null);
        $venueAvailable = ($left['venue'] ?? '') !== '' && ($left['venue'] ?? null) !== null
            && ($right['venue'] ?? '') !== '' && ($right['venue'] ?? null) !== null;
        $venue = $venueAvailable ? self::jaroWinkler($left['venue'], $right['venue']) : null;
        $time = self::timeSimilarity($left['start_at'] ?? null, $right['start_at'] ?? null);

        $components = [[$title, 0.70], [$time, 0.15]];
        if ($venue !== null) {
            $components[] = [$venue, 0.15];
        }
        $weighted = 0.0;
        $totalWeight = 0.0;
        foreach ($components as [$value, $weight]) {
            $weighted += $value * $weight;
            $totalWeight += $weight;
        }
        $score = round($weighted / $totalWeight, 4);
        $decision = $score >= self::AUTO_THRESHOLD ? 'auto-merge'
            : ($score >= self::REVIEW_THRESHOLD ? 'review' : 'separate');

        return [
            'score' => $score,
            'decision' => $decision,
            'title' => round($title, 4),
            'venue' => $venue !== null ? round($venue, 4) : null,
            'time' => round($time, 4),
        ];
    }

    public static function jaroWinkler(?string $left, ?string $right): float
    {
        $a = Text::fold($left);
        $b = Text::fold($right);
        if ($a === $b) {
            return $a !== '' ? 1.0 : 0.0;
        }
        if ($a === '' || $b === '') {
            return 0.0;
        }
        $lengthA = strlen($a);
        $lengthB = strlen($b);
        $distance = intdiv(max($lengthA, $lengthB), 2) - 1;
        $aMatch = array_fill(0, $lengthA, false);
        $bMatch = array_fill(0, $lengthB, false);
        $matches = 0;
        for ($i = 0; $i < $lengthA; $i++) {
            $from = max(0, $i - $distance);
            $to = min($i + $distance + 1, $lengthB);
            for ($j = $from; $j < $to; $j++) {
                if ($bMatch[$j] || $a[$i] !== $b[$j]) {
                    continue;
                }
                $aMatch[$i] = true;
                $bMatch[$j] = true;
                $matches++;
                break;
            }
        }
        if ($matches === 0) {
            return 0.0;
        }
        $aChars = [];
        for ($i = 0; $i < $lengthA; $i++) {
            if ($aMatch[$i]) {
                $aChars[] = $a[$i];
            }
        }
        $bChars = [];
        for ($j = 0; $j < $lengthB; $j++) {
            if ($bMatch[$j]) {
                $bChars[] = $b[$j];
            }
        }
        $transpositions = 0;
        foreach ($aChars as $index => $char) {
            if ($char !== $bChars[$index]) {
                $transpositions++;
            }
        }
        $transpositions /= 2;
        $jaro = ($matches / $lengthA + $matches / $lengthB
            + ($matches - $transpositions) / $matches) / 3;
        $prefix = 0;
        for ($i = 0; $i < min($lengthA, $lengthB); $i++) {
            if ($a[$i] !== $b[$i] || $prefix === 4) {
                break;
            }
            $prefix++;
        }

        return min(1.0, $jaro + $prefix * 0.1 * (1.0 - $jaro));
    }

    public static function timeSimilarity(?string $left, ?string $right): float
    {
        $a = self::minutes($left);
        $b = self::minutes($right);
        if ($a === null || $b === null) {
            return 0.0;
        }

        return max(0.0, 1.0 - abs($a - $b) / 180.0);
    }

    private static function minutes(?string $value): ?int
    {
        if ($value === null || $value === '') {
            return null;
        }
        try {
            $parsed = new DateTimeImmutable($value);
        } catch (Exception) {
            return null;
        }

        return (int) $parsed->format('G') * 60 + (int) $parsed->format('i');
    }

    /** Datum začátku tak, jak je zapsané (místní čas), bez převodu na UTC. */
    private static function day(?string $value): ?string
    {
        if ($value === null || preg_match('/^\d{4}-\d{2}-\d{2}/', $value) !== 1) {
            return null;
        }

        return substr($value, 0, 10);
    }
}
