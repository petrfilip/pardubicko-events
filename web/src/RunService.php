<?php

declare(strict_types=1);

namespace Pardubicko;

use DateTimeImmutable;
use Exception;
use PDO;

/**
 * Report běhu pipeline (ADR 0008, etapa 2).
 *
 * Pipeline v NanoClaw stahuje zdroje a kandidáty posílá průběžně přes
 * `POST /api/v1/candidates`. Na konci běhu pošle jediný report: výsledek
 * každého zdroje a každé stažení s výsledkem extrakce. Server z nich určuje
 * splatnost zdrojů a počítá jejich zdraví, takže lokální cache pipeline smí
 * kdykoli zmizet.
 *
 * Běh se reportuje jednou. Opakovaný report téhož `run_id` vrátí `409`,
 * aby klient po výpadku spojení poznal, že první pokus prošel.
 */
final class RunService
{
    public const RUN_STATES = ['success', 'partial', 'failed', 'no-change'];
    public const SOURCE_STATES = ['success', 'no-change', 'failed', 'skipped'];
    private const COUNTS = ['items_found', 'items_valid', 'candidates_created', 'candidates_existing',
        'candidates_updated', 'candidates_quarantined'];
    private const MAX_SOURCES = 500;
    private const MAX_FETCHES = 50;

    public function __construct(
        private readonly PDO $pdo,
        private readonly Clock $clock,
    ) {
    }

    /**
     * @param array<string, mixed> $data {started_at, finished_at, status, offline?, report?, sources}
     * @return array{run_id: string, sources: int, fetches: int, health: list<array<string, mixed>>}
     */
    public function report(string $runId, array $data, Actor $actor): array
    {
        $errors = [];
        $run = [
            'started_at' => self::timestamp($data, 'started_at', 'started_at', $errors),
            'finished_at' => self::timestamp($data, 'finished_at', 'finished_at', $errors),
            'status' => self::choice($data, 'status', self::RUN_STATES, 'status', $errors),
            'offline' => ($data['offline'] ?? false) === true ? 1 : 0,
        ];
        $report = $data['report'] ?? null;
        if ($report !== null && (!is_array($report) || (array_is_list($report) && $report !== []))) {
            $errors['report'] = 'Objekt se souhrnem běhu, nebo vynechat.';
        }

        $sources = $data['sources'] ?? null;
        if (!is_array($sources) || !array_is_list($sources)) {
            $errors['sources'] = 'Seznam výsledků zdrojů (může být prázdný).';
            $sources = [];
        } elseif (count($sources) > self::MAX_SOURCES) {
            $errors['sources'] = 'Nejvýš ' . self::MAX_SOURCES . ' zdrojů v jednom běhu.';
            $sources = [];
        }

        $catalog = new Catalog($this->pdo);
        $seen = [];
        $rows = [];
        foreach ($sources as $index => $source) {
            $path = "sources[$index]";
            if (!is_array($source)) {
                $errors[$path] = 'Objekt s výsledkem zdroje.';
                continue;
            }
            $sourceId = $source['source_id'] ?? null;
            if (!is_string($sourceId) || !$catalog->sourceExists($sourceId)) {
                $errors["$path.source_id"] = 'Zdroj není v registru.';
                continue;
            }
            if (isset($seen[$sourceId])) {
                $errors["$path.source_id"] = 'Zdroj je v reportu dvakrát.';
                continue;
            }
            $seen[$sourceId] = true;
            $rows[] = $this->sourceRow($source, $path, $run, $errors);
        }
        if ($errors !== []) {
            throw DomainError::invalid($errors);
        }

        return Transaction::run($this->pdo, function () use ($runId, $run, $report, $rows, $actor): array {
            $exists = $this->pdo->prepare('SELECT 1 FROM pipeline_run WHERE id = ?');
            $exists->execute([$runId]);
            if ($exists->fetchColumn() !== false) {
                throw DomainError::conflict('Běh ' . $runId . ' už je zapsaný.', 'run-exists',
                    ['run_id' => $runId]);
            }

            $this->pdo->prepare(
                'INSERT INTO pipeline_run (id, started_at, finished_at, status, offline, dry_run, actor, report)
                 VALUES (?, ?, ?, ?, ?, 0, ?, ?)',
            )->execute([$runId, $run['started_at'], $run['finished_at'], $run['status'], $run['offline'],
                $actor->name, $report === null ? null
                    : json_encode($report, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES)]);

            $sourceRun = $this->pdo->prepare(
                'INSERT INTO pipeline_source_run (run_id, source_id, started_at, finished_at, status,
                    items_found, items_valid, candidates_created, candidates_existing, candidates_updated,
                    candidates_quarantined, error)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            );
            $fetch = $this->pdo->prepare(
                'INSERT INTO source_fetch (source_id, url, fetched_at, http_status, etag, last_modified,
                    content_hash, bytes, duration_ms, error, run_id)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            );
            $extract = $this->pdo->prepare(
                'INSERT INTO source_extract (fetch_id, items_found, items_valid, items_unparsed, fill_rates)
                 VALUES (?, ?, ?, ?, ?)',
            );

            $health = new HealthService($this->pdo, $this->clock);
            $fetches = 0;
            $states = [];
            foreach ($rows as $row) {
                $counts = array_map(static fn (string $key): int => $row[$key], self::COUNTS);
                $sourceRun->execute(array_merge(
                    [$runId, $row['source_id'], $row['started_at'], $row['finished_at'], $row['status']],
                    $counts, [$row['error']]));
                foreach ($row['fetches'] as $item) {
                    $fetch->execute([$row['source_id'], $item['url'], $item['fetched_at'], $item['http_status'],
                        $item['etag'], $item['last_modified'], $item['content_hash'], $item['bytes'],
                        $item['duration_ms'], $item['error'], $runId]);
                    if ($item['extract'] !== null) {
                        $extract->execute([(int) $this->pdo->lastInsertId(), ...$item['extract']]);
                    }
                    $fetches++;
                }
                if ($row['fetches'] !== []) {
                    $before = $this->healthState($row['source_id']);
                    $result = $health->evaluate($row['source_id']);
                    $states[] = ['source_id' => $row['source_id'], 'state' => $result['state'],
                        'previous_state' => $before, 'reason' => $result['reason']];
                }
            }

            return ['run_id' => $runId, 'sources' => count($rows), 'fetches' => $fetches, 'health' => $states];
        });
    }

    /** @return array<string, mixed>|null běh s výsledky zdrojů */
    public function get(string $runId): ?array
    {
        $statement = $this->pdo->prepare('SELECT * FROM pipeline_run WHERE id = ?');
        $statement->execute([$runId]);
        $run = $statement->fetch();
        if ($run === false) {
            return null;
        }
        $sources = $this->pdo->prepare(
            'SELECT source_id, started_at, finished_at, status, items_found, items_valid, candidates_created,
                    candidates_existing, candidates_updated, candidates_quarantined, error
             FROM pipeline_source_run WHERE run_id = ? ORDER BY source_id',
        );
        $sources->execute([$runId]);

        return [
            'id' => (string) $run['id'],
            'started_at' => $run['started_at'],
            'finished_at' => $run['finished_at'],
            'status' => $run['status'],
            'offline' => (bool) $run['offline'],
            'actor' => $run['actor'],
            'report' => is_string($run['report']) ? json_decode($run['report'], true) : null,
            'sources' => array_map(static function (array $row): array {
                foreach (self::COUNTS as $key) {
                    $row[$key] = (int) $row[$key];
                }
                return $row;
            }, $sources->fetchAll()),
        ];
    }

    /**
     * @param array<string, mixed> $source
     * @param array<string, mixed> $run
     * @param array<string, string> $errors
     * @return array<string, mixed>
     */
    private function sourceRow(array $source, string $path, array $run, array &$errors): array
    {
        $row = [
            'source_id' => $source['source_id'],
            'status' => self::choice($source, 'status', self::SOURCE_STATES, "$path.status", $errors),
            'started_at' => array_key_exists('started_at', $source)
                ? self::timestamp($source, 'started_at', "$path.started_at", $errors) : $run['started_at'],
            'finished_at' => array_key_exists('finished_at', $source)
                ? self::timestamp($source, 'finished_at', "$path.finished_at", $errors) : $run['finished_at'],
            'error' => self::text($source, 'error', "$path.error", $errors, 2000),
            'fetches' => [],
        ];
        foreach (self::COUNTS as $key) {
            $row[$key] = self::count($source, $key, "$path.$key", $errors) ?? 0;
        }

        $fetches = $source['fetches'] ?? [];
        if (!is_array($fetches) || !array_is_list($fetches) || count($fetches) > self::MAX_FETCHES) {
            $errors["$path.fetches"] = 'Seznam nejvýš ' . self::MAX_FETCHES . ' stažení.';
            return $row;
        }
        foreach ($fetches as $index => $item) {
            $at = "$path.fetches[$index]";
            if (!is_array($item)) {
                $errors[$at] = 'Objekt se stažením.';
                continue;
            }
            $row['fetches'][] = [
                'url' => self::text($item, 'url', "$at.url", $errors, 2000),
                'fetched_at' => self::timestamp($item, 'fetched_at', "$at.fetched_at", $errors),
                'http_status' => self::count($item, 'http_status', "$at.http_status", $errors),
                'etag' => self::text($item, 'etag', "$at.etag", $errors, 500),
                'last_modified' => self::text($item, 'last_modified', "$at.last_modified", $errors, 100),
                'content_hash' => self::text($item, 'content_hash', "$at.content_hash", $errors, 200),
                'bytes' => self::count($item, 'bytes', "$at.bytes", $errors),
                'duration_ms' => self::count($item, 'duration_ms', "$at.duration_ms", $errors),
                'error' => self::text($item, 'error', "$at.error", $errors, 2000),
                'extract' => self::extract($item['extract'] ?? null, "$at.extract", $errors),
            ];
        }

        return $row;
    }

    /**
     * @param array<string, string> $errors
     * @return list<mixed>|null [items_found, items_valid, items_unparsed, fill_rates JSON]
     */
    private static function extract(mixed $value, string $path, array &$errors): ?array
    {
        if ($value === null) {
            return null;
        }
        if (!is_array($value)) {
            $errors[$path] = 'Objekt s výsledkem extrakce, nebo null.';
            return null;
        }
        $found = self::count($value, 'items_found', "$path.items_found", $errors);
        $valid = self::count($value, 'items_valid', "$path.items_valid", $errors);
        if ($found === null) {
            $errors["$path.items_found"] = 'Povinné nezáporné celé číslo.';
        }
        $rates = $value['fill_rates'] ?? [];
        if (!is_array($rates) || (array_is_list($rates) && $rates !== [])) {
            $errors["$path.fill_rates"] = 'Objekt pole → podíl 0 až 1.';
            $rates = [];
        }
        foreach ($rates as $field => $rate) {
            if (!(is_int($rate) || is_float($rate)) || $rate < 0 || $rate > 1) {
                $errors["$path.fill_rates.$field"] = 'Podíl 0 až 1.';
            }
        }
        ksort($rates);

        return [$found ?? 0, $valid ?? $found ?? 0,
            self::count($value, 'items_unparsed', "$path.items_unparsed", $errors) ?? 0,
            json_encode((object) $rates, JSON_UNESCAPED_UNICODE)];
    }

    private function healthState(string $sourceId): ?string
    {
        $statement = $this->pdo->prepare('SELECT state FROM source_health WHERE source_id = ?');
        $statement->execute([$sourceId]);
        $state = $statement->fetchColumn();

        return $state === false ? null : (string) $state;
    }

    /** @param array<string, string> $errors */
    private static function timestamp(array $data, string $key, string $path, array &$errors): string
    {
        $value = $data[$key] ?? null;
        if (is_string($value) && preg_match('/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:\d{2})$/', $value) === 1) {
            try {
                return (new DateTimeImmutable($value))->setTimezone(Format::zone())->format(Clock::FORMAT);
            } catch (Exception) {
            }
        }
        $errors[$path] = 'Čas ve formátu ISO 8601 s časovou zónou, např. 2026-09-23T10:00:00+02:00.';

        return '';
    }

    /**
     * @param list<string> $allowed
     * @param array<string, string> $errors
     */
    private static function choice(array $data, string $key, array $allowed, string $path, array &$errors): string
    {
        $value = $data[$key] ?? null;
        if (!is_string($value) || !in_array($value, $allowed, true)) {
            $errors[$path] = 'Jedna z hodnot ' . implode(', ', $allowed) . '.';
            return '';
        }

        return $value;
    }

    /** @param array<string, string> $errors */
    private static function count(array $data, string $key, string $path, array &$errors): ?int
    {
        $value = $data[$key] ?? null;
        if ($value === null) {
            return null;
        }
        if (!is_int($value) || $value < 0) {
            $errors[$path] = 'Nezáporné celé číslo.';
            return null;
        }

        return $value;
    }

    /** @param array<string, string> $errors */
    private static function text(array $data, string $key, string $path, array &$errors, int $max): ?string
    {
        $value = $data[$key] ?? null;
        if ($value === null || $value === '') {
            return null;
        }
        if (!is_string($value)) {
            $errors[$path] = 'Text.';
            return null;
        }

        return mb_substr($value, 0, $max, 'UTF-8');
    }
}
