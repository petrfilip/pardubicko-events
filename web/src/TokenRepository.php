<?php

declare(strict_types=1);

namespace Pardubicko;

use PDO;

/**
 * Tokeny agentů. V databázi je jen SHA-256 hash; token se ukáže jednou při
 * vytvoření. Hash stačí, protože token je 32 náhodných bajtů, takže se
 * nedá uhodnout ani slovníkem.
 */
final class TokenRepository
{
    public function __construct(
        private readonly PDO $pdo,
        private readonly Clock $clock,
    ) {
    }

    /** @return string token v otevřené podobě */
    public function create(string $name, int $dailyPublishLimit): string
    {
        if (preg_match('/^[a-z0-9]+(-[a-z0-9]+)*$/', $name) !== 1) {
            throw DomainError::invalid(['name' => 'Název tokenu je slug, např. nanoclaw-curator.']);
        }
        if ($dailyPublishLimit < 0) {
            throw DomainError::invalid(['daily_publish_limit' => 'Nezáporné číslo.']);
        }
        $exists = $this->pdo->prepare('SELECT 1 FROM api_token WHERE name = ?');
        $exists->execute([$name]);
        if ($exists->fetchColumn() !== false) {
            throw DomainError::conflict('Token ' . $name . ' už existuje.', 'token-exists');
        }
        $token = 'pe_' . bin2hex(random_bytes(32));
        $this->pdo->prepare(
            'INSERT INTO api_token (name, token_hash, daily_publish_limit, created_at) VALUES (?, ?, ?, ?)',
        )->execute([$name, self::hash($token), $dailyPublishLimit, $this->clock->timestamp()]);

        return $token;
    }

    public function authenticate(string $token): ?Actor
    {
        if ($token === '') {
            return null;
        }
        $statement = $this->pdo->prepare(
            'SELECT id, name, daily_publish_limit FROM api_token
             WHERE token_hash = ? AND revoked_at IS NULL');
        $statement->execute([self::hash($token)]);
        $row = $statement->fetch();
        if ($row === false) {
            return null;
        }
        $this->pdo->prepare('UPDATE api_token SET last_used_at = ? WHERE id = ?')
            ->execute([$this->clock->timestamp(), $row['id']]);

        return Actor::agent((string) $row['name'], (int) $row['daily_publish_limit']);
    }

    public function revoke(string $name): bool
    {
        $statement = $this->pdo->prepare(
            'UPDATE api_token SET revoked_at = ? WHERE name = ? AND revoked_at IS NULL');
        $statement->execute([$this->clock->timestamp(), $name]);

        return $statement->rowCount() > 0;
    }

    public function setLimit(string $name, int $dailyPublishLimit): bool
    {
        $statement = $this->pdo->prepare('UPDATE api_token SET daily_publish_limit = ? WHERE name = ?');
        $statement->execute([max(0, $dailyPublishLimit), $name]);

        return $statement->rowCount() > 0;
    }

    /** @return list<array<string, mixed>> */
    public function all(): array
    {
        return $this->pdo->query(
            'SELECT name, daily_publish_limit, created_at, last_used_at, revoked_at
             FROM api_token ORDER BY name')->fetchAll();
    }

    private static function hash(string $token): string
    {
        return hash('sha256', $token);
    }
}
