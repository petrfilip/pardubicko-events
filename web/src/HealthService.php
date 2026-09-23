<?php

declare(strict_types=1);

namespace Pardubicko;

use DateTimeImmutable;
use Exception;
use PDO;

/**
 * Zdraví zdrojů (ADR 0004). Převod `evaluate` z `tools/pipeline/health.py`
 * se stejnými prahy a stejným pořadím podmínek.
 *
 * Tři signály se vyhodnocují odděleně a nikdy se neslévají: stažení
 * (chyby v řadě), extrakce (počet položek a vyplnění klíčových polí proti
 * baseline zdroje) a čerstvost (mění se obsah, je nějaký budoucí termín).
 * Nulová výtěžnost sama chyba není: zdroj s baseline pod
 * `LOW_BASELINE_ITEMS` se podle objemu nehodnotí.
 *
 * Prahy jsou počáteční odhady, ne kalibrované konstanty. Kdo je změní,
 * zapíše, na jakých datech je ověřil.
 */
final class HealthService
{
    public const BROKEN_CONSECUTIVE_FAILURES = 3;
    public const LOW_BASELINE_ITEMS = 3;
    public const DEGRADED_ITEMS_RATIO = 0.4;
    public const SCHEMA_DRIFT_FILL_RATIO = 0.8;
    public const STALE_UNCHANGED_DAYS = 30;
    public const BASELINE_WINDOW_RUNS = 10;
    public const HISTORY_LOOKBACK_RUNS = 60;
    public const KEY_FIELDS = ['start_at', 'title', 'canonical_url'];

    public function __construct(
        private readonly PDO $pdo,
        private readonly Clock $clock,
    ) {
    }

    /**
     * Vyhodnotí zdroj z uložené historie a zapíše `source_health`.
     *
     * @return array{source_id: string, state: string, reason: string, evaluated_at: string,
     *               fetch: array<string, mixed>, extract: array<string, mixed>,
     *               freshness: array<string, mixed>}
     */
    public function evaluate(string $sourceId, bool $persist = true): array
    {
        $now = $this->clock->now();
        $stored = $this->stored($sourceId);
        $history = $this->history($sourceId);

        $fetch = self::fetchSignal($history);
        $extract = self::extractSignal($history, $stored);
        $freshness = $this->freshnessSignal($sourceId, $history, $now, $stored);
        [$state, $reason] = self::decide($fetch, $extract, $freshness);

        $health = [
            'source_id' => $sourceId,
            'state' => $state,
            'reason' => $reason,
            'evaluated_at' => $now->format(Clock::FORMAT),
            'fetch' => $fetch,
            'extract' => $extract,
            'freshness' => $freshness,
        ];
        if ($persist) {
            $this->persist($health, $stored, $now);
        }

        return $health;
    }

    /**
     * Posledních N stažení zdroje od nejnovějšího, s výsledkem extrakce.
     *
     * @return list<array<string, mixed>>
     */
    private function history(string $sourceId): array
    {
        $statement = $this->pdo->prepare(
            'SELECT f.id, f.fetched_at, f.http_status, f.content_hash, f.error,
                    e.items_found, e.items_valid, e.items_unparsed, e.fill_rates
             FROM source_fetch f LEFT JOIN source_extract e ON e.fetch_id = f.id
             WHERE f.source_id = ? ORDER BY f.fetched_at DESC, f.id DESC LIMIT ?',
        );
        $statement->execute([$sourceId, self::HISTORY_LOOKBACK_RUNS]);

        $history = [];
        foreach ($statement->fetchAll() as $row) {
            $rates = is_string($row['fill_rates']) ? json_decode($row['fill_rates'], true) : [];
            $status = $row['http_status'] === null ? null : (int) $row['http_status'];
            $history[] = [
                'fetched_at' => (string) $row['fetched_at'],
                'http_status' => $status,
                'content_hash' => $row['content_hash'],
                'error' => $row['error'],
                'items_found' => self::intOrNull($row['items_found']),
                'items_valid' => self::intOrNull($row['items_valid']),
                'items_unparsed' => self::intOrNull($row['items_unparsed']),
                'fill_rates' => is_array($rates) ? $rates : [],
                // Úspěch = bez chyby a s kódem, který něco vrátil (včetně 304).
                'ok' => ($row['error'] === null || $row['error'] === '')
                    && $status !== null && $status >= 200 && $status < 400,
            ];
        }

        return $history;
    }

    /** @param list<array<string, mixed>> $history */
    private static function fetchSignal(array $history): array
    {
        $signal = ['checked_at' => null, 'ok' => null, 'http_status' => null, 'error' => null,
            'consecutive_failures' => 0, 'last_success_at' => null, 'runs_known' => 0];
        if ($history === []) {
            return $signal;
        }

        $failures = 0;
        foreach ($history as $run) {
            if ($run['ok']) {
                break;
            }
            $failures++;
        }
        $lastSuccess = null;
        foreach ($history as $run) {
            if ($run['ok']) {
                $lastSuccess = $run['fetched_at'];
                break;
            }
        }

        return [
            'checked_at' => $history[0]['fetched_at'],
            'ok' => $history[0]['ok'],
            'http_status' => $history[0]['http_status'],
            'error' => $history[0]['error'],
            'consecutive_failures' => $failures,
            'last_success_at' => $lastSuccess,
            'runs_known' => count($history),
        ];
    }

    /**
     * @param list<array<string, mixed>> $history
     * @param array<string, mixed>|null $stored
     */
    private static function extractSignal(array $history, ?array $stored): array
    {
        $previous = array_slice($history, 1);

        // Nulové běhy se do baseline nepočítají: jinak by si zdroj, který
        // jednou vypadne, sám snížil laťku a příště by prázdno prošlo.
        $counts = [];
        foreach ($previous as $run) {
            if ($run['items_found'] !== null && $run['items_found'] > 0) {
                $counts[] = $run['items_found'];
            }
        }
        $counts = array_slice($counts, 0, self::BASELINE_WINDOW_RUNS);
        $baseline = $counts === [] ? null : self::median($counts);
        if ($baseline === null && ($stored['baseline_items_median'] ?? null) !== null) {
            // Bez vlastní historie platí nasazený baseline (seed z migrace).
            $baseline = (float) $stored['baseline_items_median'];
        }

        $usable = array_slice(array_values(array_filter($previous,
            static fn (array $run): bool => $run['items_found'] > 0 && $run['fill_rates'] !== [])),
            0, self::BASELINE_WINDOW_RUNS);
        $baselineFill = [];
        foreach (self::KEY_FIELDS as $name) {
            $values = [];
            foreach ($usable as $run) {
                $value = $run['fill_rates'][$name] ?? null;
                if (is_int($value) || is_float($value)) {
                    $values[] = (float) $value;
                }
            }
            if ($values !== []) {
                $baselineFill[$name] = self::median($values);
            }
        }
        if ($baselineFill === [] && is_string($stored['baseline_fill_rates'] ?? null)) {
            $parsed = json_decode($stored['baseline_fill_rates'], true);
            foreach (is_array($parsed) ? $parsed : [] as $key => $value) {
                if (is_int($value) || is_float($value)) {
                    $baselineFill[(string) $key] = (float) $value;
                }
            }
        }

        $signal = [
            'items_found' => null, 'items_valid' => null, 'items_unparsed' => null,
            'fill_rates' => [],
            'baseline_items_median' => $baseline,
            'baseline_fill_rates' => $baselineFill,
            'baseline_runs' => count($counts),
            'volume_evaluated' => $baseline !== null && $baseline >= self::LOW_BASELINE_ITEMS,
            'drifted_fields' => [],
        ];
        if ($history === []) {
            return $signal;
        }

        $latest = $history[0];
        $signal['items_found'] = $latest['items_found'];
        $signal['items_valid'] = $latest['items_valid'];
        $signal['items_unparsed'] = $latest['items_unparsed'];
        $signal['fill_rates'] = $latest['fill_rates'];

        // Drift jen tam, kde nějaké položky jsou; fill rate z nuly nic neznamená.
        if ($latest['items_found']) {
            foreach (self::KEY_FIELDS as $name) {
                $base = $baselineFill[$name] ?? null;
                $current = $latest['fill_rates'][$name] ?? null;
                if ($base === null || !(is_int($current) || is_float($current)) || $base <= 0) {
                    continue;
                }
                if ((float) $current < self::SCHEMA_DRIFT_FILL_RATIO * $base) {
                    $signal['drifted_fields'][] = $name;
                }
            }
        }

        return $signal;
    }

    /**
     * @param list<array<string, mixed>> $history
     * @param array<string, mixed>|null $stored
     */
    private function freshnessSignal(string $sourceId, array $history, DateTimeImmutable $now,
                                     ?array $stored): array
    {
        $signal = ['content_hash' => null, 'unchanged_days' => null, 'unchanged_runs' => 0,
            'has_future_events' => false, 'last_item_at' => $stored['last_item_at'] ?? null];

        foreach ($history as $run) {
            if ($run['items_found'] !== null && $run['items_found'] > 0) {
                $signal['last_item_at'] = $run['fetched_at'];
                break;
            }
        }

        // Budoucí termín: akce hlášená zdrojem ještě neskončila. Na rozdíl od
        // health.py se nepočítá akce stažená z webu, ta už není na co čekat.
        $statement = $this->pdo->prepare(
            "SELECT count(*) FROM event_source es JOIN event e ON e.id = es.event_id
             WHERE es.source_id = ? AND e.cancelled = 0 AND e.status = 'published'
               AND substr(coalesce(e.end_at, e.start_at), 1, 10) >= ?",
        );
        $statement->execute([$sourceId, $now->format('Y-m-d')]);
        $signal['has_future_events'] = (int) $statement->fetchColumn() > 0;

        if ($history === []) {
            return $signal;
        }
        $current = $history[0]['content_hash'];
        $signal['content_hash'] = $current;
        if ($current === null || $current === '') {
            return $signal;
        }

        $streak = [];
        foreach ($history as $run) {
            if ($run['content_hash'] !== $current) {
                break;
            }
            $streak[] = $run;
        }
        $signal['unchanged_runs'] = count($streak);
        if (count($streak) >= 2) {
            // Rozpětí mezi prvním a posledním výskytem téhož obsahu, ne doba
            // od poslední kontroly; dlouho nekontrolovaný zdroj není stale.
            $signal['unchanged_days'] = self::daysBetween(
                $streak[count($streak) - 1]['fetched_at'], $streak[0]['fetched_at']);
        }

        return $signal;
    }

    /**
     * Ze tří signálů odvodí stav. Pořadí podmínek je z architektury, oddíl 5.
     *
     * @return array{0: string, 1: string}
     */
    private static function decide(array $fetch, array $extract, array $freshness): array
    {
        if ($fetch['ok'] === null) {
            return ['healthy', 'bez záznamu o stažení, není co hodnotit'];
        }
        if ($fetch['consecutive_failures'] >= self::BROKEN_CONSECUTIVE_FAILURES) {
            return ['broken', sprintf('%d× po sobě selhalo stažení (práh %d)',
                $fetch['consecutive_failures'], self::BROKEN_CONSECUTIVE_FAILURES)];
        }
        if ($extract['volume_evaluated']) {
            $baseline = (float) ($extract['baseline_items_median'] ?? 0.0);
            if ($extract['items_found'] === 0) {
                return ['suspect', 'stažení prošlo, ale extrakce vrátila 0 položek proti baseline '
                    . self::number($baseline)];
            }
            if ($extract['items_found'] !== null && $extract['items_found'] < self::DEGRADED_ITEMS_RATIO * $baseline) {
                return ['degraded', sprintf('%d položek proti baseline %s (práh %s×)',
                    $extract['items_found'], self::number($baseline), self::number(self::DEGRADED_ITEMS_RATIO))];
            }
        }
        if ($extract['drifted_fields'] !== []) {
            return ['schema-drift', 'přestala se plnit klíčová pole: ' . implode(', ', $extract['drifted_fields'])];
        }
        if ($freshness['unchanged_days'] !== null && $freshness['unchanged_days'] > self::STALE_UNCHANGED_DAYS
            && !$freshness['has_future_events']) {
            return ['stale', sprintf('obsah beze změny %d dní a žádný budoucí termín', $freshness['unchanged_days'])];
        }
        if (!$extract['volume_evaluated'] && $extract['items_found'] === 0) {
            // Výslovně pojmenovaný nepoplach: malá obec v srpnu nic nemá.
            return ['healthy', sprintf('0 položek, ale nízký baseline (< %d) — podle objemu se nehodnotí',
                self::LOW_BASELINE_ITEMS)];
        }

        return ['healthy', 'stažení i extrakce odpovídají baseline'];
    }

    /** @param array<string, mixed>|null $stored */
    private function persist(array $health, ?array $stored, DateTimeImmutable $now): void
    {
        $firstAlertedAt = $stored['first_alerted_at'] ?? null;
        if ($health['state'] === 'healthy') {
            $firstAlertedAt = null;
        } elseif ($firstAlertedAt === null || $firstAlertedAt === '' || ($stored['state'] ?? null) === 'healthy') {
            $firstAlertedAt = $now->format(Clock::FORMAT);
        }
        $baselineFill = $health['extract']['baseline_fill_rates'];
        ksort($baselineFill);

        $this->pdo->prepare(
            'INSERT INTO source_health (source_id, state, consecutive_failures, last_checked_at,
                last_success_at, last_item_at, baseline_items_median, baseline_fill_rates,
                first_alerted_at, note)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
             ON CONFLICT(source_id) DO UPDATE SET
                state = excluded.state,
                consecutive_failures = excluded.consecutive_failures,
                last_checked_at = coalesce(excluded.last_checked_at, source_health.last_checked_at),
                last_success_at = coalesce(excluded.last_success_at, source_health.last_success_at),
                last_item_at = coalesce(excluded.last_item_at, source_health.last_item_at),
                baseline_items_median = coalesce(excluded.baseline_items_median, source_health.baseline_items_median),
                baseline_fill_rates = coalesce(excluded.baseline_fill_rates, source_health.baseline_fill_rates),
                first_alerted_at = excluded.first_alerted_at,
                note = coalesce(excluded.note, source_health.note)',
        )->execute([
            $health['source_id'], $health['state'], $health['fetch']['consecutive_failures'],
            $health['fetch']['checked_at'], $health['fetch']['last_success_at'],
            $health['freshness']['last_item_at'], $health['extract']['baseline_items_median'],
            $baselineFill === [] ? null : json_encode($baselineFill, JSON_UNESCAPED_UNICODE),
            $firstAlertedAt,
            // Bez záznamu o stažení je cennější poznámka z migrace nebo od člověka.
            $health['fetch']['ok'] === null ? null : $health['reason'],
        ]);
    }

    /** @return array<string, mixed>|null */
    private function stored(string $sourceId): ?array
    {
        $statement = $this->pdo->prepare('SELECT * FROM source_health WHERE source_id = ?');
        $statement->execute([$sourceId]);
        $row = $statement->fetch();

        return $row === false ? null : $row;
    }

    /** @param list<int|float> $values */
    private static function median(array $values): float
    {
        sort($values);
        $count = count($values);
        $middle = intdiv($count, 2);

        return $count % 2 === 1 ? (float) $values[$middle]
            : ((float) $values[$middle - 1] + (float) $values[$middle]) / 2;
    }

    /** Celé dny mezi dvěma okamžiky, zaokrouhleno dolů jako `timedelta.days`. */
    private static function daysBetween(string $earlier, string $later): ?int
    {
        $start = self::parse($earlier);
        $end = self::parse($later);
        if ($start === null || $end === null) {
            return null;
        }

        return (int) floor(($end->getTimestamp() - $start->getTimestamp()) / 86400);
    }

    private static function parse(string $value): ?DateTimeImmutable
    {
        try {
            return new DateTimeImmutable(trim($value), Format::zone());
        } catch (Exception) {
            return null;
        }
    }

    /** Číslo bez zbytečných nul, jako `format(x, 'g')` v Pythonu. */
    private static function number(float $value): string
    {
        return rtrim(rtrim(sprintf('%.6F', $value), '0'), '.');
    }

    private static function intOrNull(mixed $value): ?int
    {
        return $value === null ? null : (int) $value;
    }
}
