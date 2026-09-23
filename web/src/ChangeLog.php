<?php

declare(strict_types=1);

namespace Pardubicko;

use PDO;

/**
 * Historie změn dat (ADR 0008). Zapisuje se ve stejné transakci jako
 * samotná změna, takže změna bez záznamu v historii nemůže vzniknout.
 */
final class ChangeLog
{
    public function __construct(
        private readonly PDO $pdo,
        private readonly Clock $clock,
    ) {
    }

    /**
     * @param array<string, mixed>|null $before
     * @param array<string, mixed>|null $after
     */
    public function record(Actor $actor, ?string $runId, string $entity, string $entityId,
                           string $action, ?array $before, ?array $after, ?string $note): int
    {
        $statement = $this->pdo->prepare(
            'INSERT INTO change_log
                (at, actor, actor_kind, run_id, entity, entity_id, action, before, after, note)
             VALUES (:at, :actor, :kind, :run, :entity, :entity_id, :action, :before, :after, :note)',
        );
        $statement->execute([
            ':at' => $this->clock->timestamp(),
            ':actor' => $actor->name,
            ':kind' => $actor->kind,
            ':run' => $runId,
            ':entity' => $entity,
            ':entity_id' => $entityId,
            ':action' => $action,
            ':before' => $before === null ? null : self::json($before),
            ':after' => $after === null ? null : self::json($after),
            ':note' => $note,
        ]);

        return (int) $this->pdo->lastInsertId();
    }

    /** Počet publikací aktéra od místní půlnoci; podklad denního limitu. */
    public function publicationsToday(Actor $actor): int
    {
        $midnight = $this->clock->now()->setTime(0, 0);
        $statement = $this->pdo->prepare(
            "SELECT COUNT(*) FROM change_log
             WHERE actor = :actor AND action = 'publish' AND at >= :since",
        );
        $statement->execute([
            ':actor' => $actor->name,
            ':since' => $midnight->format(Clock::FORMAT),
        ]);

        return (int) $statement->fetchColumn();
    }

    /**
     * @param array{entity?: string, entity_id?: string, run_id?: string, actor?: string, since_id?: int} $filter
     * @return list<array<string, mixed>>
     */
    public function entries(array $filter, int $limit = 100): array
    {
        $where = ['1 = 1'];
        $parameters = [];
        foreach (['entity', 'entity_id', 'run_id', 'actor'] as $column) {
            if (isset($filter[$column]) && $filter[$column] !== '') {
                $where[] = $column . ' = :' . $column;
                $parameters[':' . $column] = $filter[$column];
            }
        }
        if (isset($filter['since_id'])) {
            $where[] = 'id > :since_id';
            $parameters[':since_id'] = $filter['since_id'];
        }
        $statement = $this->pdo->prepare(
            'SELECT * FROM change_log WHERE ' . implode(' AND ', $where)
            . ' ORDER BY id DESC LIMIT ' . max(1, min(500, $limit)),
        );
        $statement->execute($parameters);

        return array_map(static function (array $row): array {
            $row['id'] = (int) $row['id'];
            foreach (['before', 'after'] as $key) {
                $row[$key] = $row[$key] === null ? null : json_decode((string) $row[$key], true);
            }
            return $row;
        }, $statement->fetchAll());
    }

    /** @param array<string, mixed> $value */
    private static function json(array $value): string
    {
        return (string) json_encode($value, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    }
}
