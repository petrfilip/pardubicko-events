<?php

declare(strict_types=1);

namespace Pardubicko;

use DateInterval;
use DateTimeImmutable;
use Exception;
use PDO;

/**
 * Registr zdrojů a jejich provozní stav.
 *
 * Zdroj je splatný, když od posledního stažení uplynul jeho interval
 * kontroly nebo když ještě nebyl stažený; pravidlo odpovídá `_is_due` v
 * `tools/pipeline/run.py`.
 */
final class SourceService
{
    private const EDITABLE = ['name', 'url', 'type', 'adapter', 'priority', 'check_interval_days',
        'enabled', 'notes'];
    private const PRIORITIES = ['high', 'normal', 'low'];

    public function __construct(
        private readonly PDO $pdo,
        private readonly Clock $clock,
    ) {
    }

    /** @return list<array<string, mixed>> */
    public function list(bool $dueOnly = false): array
    {
        $rows = $this->pdo->query(
            "SELECT s.*, COALESCE(h.state, 'unknown') AS health_state,
                    h.last_success_at, h.last_item_at, h.consecutive_failures, h.note AS health_note,
                    (SELECT f.fetched_at FROM source_fetch f WHERE f.source_id = s.id
                     ORDER BY datetime(f.fetched_at) DESC, f.id DESC LIMIT 1) AS last_fetched_at
             FROM source s LEFT JOIN source_health h ON h.source_id = s.id
             ORDER BY s.id")->fetchAll();
        $sources = array_map(fn (array $row): array => $this->present($row), $rows);
        if ($dueOnly) {
            $sources = array_values(array_filter($sources,
                static fn (array $source): bool => $source['enabled'] && $source['due']));
        }

        return $sources;
    }

    /** @return array<string, mixed>|null */
    public function get(string $id): ?array
    {
        foreach ($this->list() as $source) {
            if ($source['id'] === $id) {
                return $source;
            }
        }

        return null;
    }

    /**
     * @param array<string, mixed> $changes
     * @return array<string, mixed>
     */
    public function update(string $id, array $changes, Actor $actor, ?string $runId, string $note): array
    {
        $before = $this->get($id) ?? throw DomainError::notFound('Zdroj ' . $id . ' neexistuje.');
        $errors = [];
        foreach ($changes as $field => $value) {
            $error = match (true) {
                !in_array($field, self::EDITABLE, true) => 'Pole nejde měnit.',
                in_array($field, ['name', 'type'], true) => is_string($value) && trim($value) !== ''
                    ? null : 'Neprázdný text.',
                $field === 'url' => is_string($value) && preg_match('~^https?://~', $value) === 1
                    ? null : 'Odkaz http(s).',
                $field === 'adapter', $field === 'notes' => $value === null || is_string($value)
                    ? null : 'Text nebo null.',
                $field === 'priority' => in_array($value, self::PRIORITIES, true)
                    ? null : 'Jedna z hodnot ' . implode(', ', self::PRIORITIES) . '.',
                $field === 'check_interval_days' => is_int($value) && $value >= 1
                    ? null : 'Celé číslo aspoň 1.',
                $field === 'enabled' => is_bool($value) ? null : 'true nebo false.',
                default => null,
            };
            if ($error !== null) {
                $errors[(string) $field] = $error;
            }
        }
        if ($changes === []) {
            $errors['changes'] = 'Žádná změna.';
        }
        if ($errors !== []) {
            throw DomainError::invalid($errors);
        }

        return Transaction::run($this->pdo, function () use ($id, $changes, $before, $actor, $runId, $note): array {
            $assignments = [];
            $parameters = [':id' => $id];
            foreach ($changes as $field => $value) {
                $assignments[] = $field . ' = :' . $field;
                $parameters[':' . $field] = is_bool($value) ? (int) $value : $value;
            }
            $this->pdo->prepare('UPDATE source SET ' . implode(', ', $assignments) . ' WHERE id = :id')
                ->execute($parameters);
            $after = $this->get($id);
            (new ChangeLog($this->pdo, $this->clock))
                ->record($actor, $runId, 'source', $id, 'update', $before, $after, $note);

            return $after;
        });
    }

    /** @return array<string, mixed> */
    private function present(array $row): array
    {
        return [
            'id' => (string) $row['id'],
            'name' => (string) $row['name'],
            'url' => (string) $row['url'],
            'type' => (string) $row['type'],
            'adapter' => $row['adapter'],
            'municipality' => $row['municipality_name'],
            'district' => $row['district'],
            'region' => $row['region'],
            'priority' => (string) $row['priority'],
            'check_interval_days' => (int) $row['check_interval_days'],
            'enabled' => (bool) $row['enabled'],
            'notes' => $row['notes'],
            'last_fetched_at' => $row['last_fetched_at'],
            'due' => $this->isDue($row['last_fetched_at'], (int) $row['check_interval_days']),
            'health' => [
                'state' => (string) $row['health_state'],
                'last_success_at' => $row['last_success_at'],
                'last_item_at' => $row['last_item_at'],
                'consecutive_failures' => (int) ($row['consecutive_failures'] ?? 0),
                'note' => $row['health_note'],
            ],
        ];
    }

    private function isDue(?string $lastFetchedAt, int $intervalDays): bool
    {
        if ($lastFetchedAt === null || $lastFetchedAt === '') {
            return true;
        }
        try {
            $last = new DateTimeImmutable($lastFetchedAt);
        } catch (Exception) {
            return true;
        }

        return $this->clock->now() >= $last->add(new DateInterval('P' . max(1, $intervalDays) . 'D'));
    }
}
