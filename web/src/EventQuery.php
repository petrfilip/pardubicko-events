<?php

declare(strict_types=1);

namespace Pardubicko;

use PDO;

/**
 * Výpis akcí pro API. Na rozdíl od `EventRepository`, který skládá
 * veřejné stránky, vrací kanonický tvar a umí i nepublikované akce.
 * Agent ho používá hlavně ke kontrole duplicit před publikací.
 */
final class EventQuery
{
    public function __construct(
        private readonly PDO $pdo,
        private readonly EventStore $store,
    ) {
    }

    /**
     * @param array<string, mixed> $query parametry z URL
     * @return array{total: int, events: list<array<string, mixed>>}
     */
    public function search(array $query, int $limit, int $offset): array
    {
        $where = [];
        $parameters = [];
        $status = self::text($query['status'] ?? 'published');
        if ($status !== 'all') {
            if (!in_array($status, ['published', 'draft', 'quarantined'], true)) {
                throw DomainError::invalid(['status' => 'Jedna z hodnot published, draft, quarantined, all.']);
            }
            $where[] = 'e.status = :status';
            $parameters[':status'] = $status;
        }
        $week = self::text($query['week'] ?? '');
        if ($week !== '') {
            if (preg_match('/^\d{4}-W\d{2}$/', $week) !== 1) {
                throw DomainError::invalid(['week' => 'ISO týden, např. 2026-W33.']);
            }
            [$from, $to] = EventStore::weekBounds($week);
            $where[] = 'substr(e.start_at, 1, 10) <= :week_to
                AND substr(COALESCE(e.end_at, e.start_at), 1, 10) >= :week_from';
            $parameters[':week_from'] = $from;
            $parameters[':week_to'] = $to;
        }
        foreach (['from', 'to'] as $name) {
            $value = self::text($query[$name] ?? '');
            if ($value === '') {
                continue;
            }
            if (preg_match('/^\d{4}-\d{2}-\d{2}$/', $value) !== 1) {
                throw DomainError::invalid([$name => 'Datum ve tvaru 2026-08-15.']);
            }
            $where[] = $name === 'from'
                ? 'substr(COALESCE(e.end_at, e.start_at), 1, 10) >= :from'
                : 'substr(e.start_at, 1, 10) <= :to';
            $parameters[':' . $name] = $value;
        }
        $municipality = self::text($query['municipality'] ?? '');
        if ($municipality !== '') {
            $resolved = (new Catalog($this->pdo))->municipality($municipality);
            $where[] = $resolved === null ? '1 = 0' : 'e.municipality_id = :municipality';
            if ($resolved !== null) {
                $parameters[':municipality'] = $resolved['id'];
            }
        }
        $category = self::text($query['category'] ?? '');
        if ($category !== '') {
            $where[] = 'EXISTS (SELECT 1 FROM event_category ec
                WHERE ec.event_id = e.id AND ec.category_id = :category)';
            $parameters[':category'] = $category;
        }
        $text = self::text($query['q'] ?? '');
        if ($text !== '') {
            $match = FtsQuery::fromUserInput($text);
            $where[] = $match === null ? '1 = 0'
                : 'e.id IN (SELECT event_id FROM event_fts WHERE event_fts MATCH :fts)';
            if ($match !== null) {
                $parameters[':fts'] = $match;
            }
        }
        $changedSince = self::text($query['changed_since'] ?? '');
        if ($changedSince !== '') {
            $where[] = 'e.updated_at >= :changed_since';
            $parameters[':changed_since'] = $changedSince;
        }
        $condition = $where === [] ? '1 = 1' : implode(' AND ', $where);

        $count = $this->pdo->prepare('SELECT COUNT(*) FROM event e WHERE ' . $condition);
        $count->execute($parameters);
        $statement = $this->pdo->prepare(
            'SELECT e.id FROM event e WHERE ' . $condition
            . ' ORDER BY datetime(e.start_at), e.id LIMIT ' . $limit . ' OFFSET ' . $offset);
        $statement->execute($parameters);

        return [
            'total' => (int) $count->fetchColumn(),
            'events' => array_map(fn (string $id): array => $this->store->load($id),
                $statement->fetchAll(PDO::FETCH_COLUMN)),
        ];
    }

    private static function text(mixed $value): string
    {
        return is_string($value) ? trim($value) : '';
    }
}
