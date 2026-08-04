<?php

declare(strict_types=1);

namespace Pardubicko;

use DateTimeImmutable;
use DateTimeZone;
use PDO;

final class InboxRepository
{
    public function __construct(private readonly PDO $pdo)
    {
    }

    /** @return array{id: int, state: string, duplicate: bool, url_norm: string} */
    public function submit(string $url, ?string $note = null): array
    {
        $normalized = UrlNormalizer::normalize($url);
        $statement = $this->pdo->prepare(
            'SELECT id, state, note FROM inbox WHERE url_norm = :url',
        );
        $statement->execute([':url' => $normalized]);
        $existing = $statement->fetch();
        if ($existing !== false) {
            $merged = $this->mergeNote($existing['note'] ?? null, $note);
            if ($merged !== ($existing['note'] ?? null)) {
                $update = $this->pdo->prepare('UPDATE inbox SET note = :note WHERE id = :id');
                $update->execute([':note' => $merged, ':id' => $existing['id']]);
            }

            return [
                'id' => (int) $existing['id'],
                'state' => (string) $existing['state'],
                'duplicate' => true,
                'url_norm' => $normalized,
            ];
        }

        $insert = $this->pdo->prepare(
            "INSERT INTO inbox (url, url_norm, submitted_at, submitted_via, note, state)
             VALUES (:url, :url_norm, :submitted_at, 'api', :note, 'new')",
        );
        $insert->execute([
            ':url' => $url,
            ':url_norm' => $normalized,
            ':submitted_at' => (new DateTimeImmutable('now', new DateTimeZone('UTC')))
                ->format('Y-m-d\TH:i:sP'),
            ':note' => $this->cleanNote($note),
        ]);

        return [
            'id' => (int) $this->pdo->lastInsertId(),
            'state' => 'new',
            'duplicate' => false,
            'url_norm' => $normalized,
        ];
    }

    private function cleanNote(?string $note): ?string
    {
        if ($note === null || trim($note) === '') {
            return null;
        }

        return mb_substr(trim($note), 0, 1000, 'UTF-8');
    }

    private function mergeNote(?string $current, ?string $incoming): ?string
    {
        $incoming = $this->cleanNote($incoming);
        if ($incoming === null || $incoming === $current) {
            return $current;
        }
        if ($current === null || $current === '') {
            return $incoming;
        }
        if (in_array($incoming, preg_split('/\R/u', $current) ?: [], true)) {
            return $current;
        }

        return rtrim($current) . "\n" . $incoming;
    }
}
