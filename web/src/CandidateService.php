<?php

declare(strict_types=1);

namespace Pardubicko;

use PDO;

/**
 * Kandidáti z adaptérů, discovery a inboxu a fronta nejistých shod.
 *
 * Kandidát nese doslovný `payload`. Dva tvary, které dnes existují, se
 * čtou oba: adaptéry posílají `{normalized: {...}, raw: {...}}`, discovery
 * agent plochý záznam s `source_url`. Pro deduplikaci se použije
 * `normalized`, pokud existuje.
 */
final class CandidateService
{
    public const OPEN_STATES = ['new', 'needs-verification', 'quarantined', 'verified'];
    public const CLOSED_STATES = ['imported', 'rejected'];

    private readonly ChangeLog $log;

    public function __construct(
        private readonly PDO $pdo,
        private readonly Clock $clock,
    ) {
        $this->log = new ChangeLog($pdo, $clock);
    }

    /**
     * @param array{states?: list<string>, week?: string, source_id?: string} $filter
     * @return array{total: int, candidates: list<array<string, mixed>>}
     */
    public function list(array $filter, int $limit = 100, int $offset = 0): array
    {
        $states = $filter['states'] ?? self::OPEN_STATES;
        $where = [];
        $parameters = [];
        $placeholders = [];
        foreach (array_values($states) as $index => $state) {
            $placeholders[] = ':state' . $index;
            $parameters[':state' . $index] = $state;
        }
        $where[] = $placeholders === [] ? '1 = 0' : 'state IN (' . implode(', ', $placeholders) . ')';
        if (($filter['source_id'] ?? '') !== '') {
            $where[] = 'source_id = :source';
            $parameters[':source'] = $filter['source_id'];
        }
        $start = "COALESCE(json_extract(payload, '$.normalized.start_at'), json_extract(payload, '$.start_at'))";
        $end = "COALESCE(json_extract(payload, '$.normalized.end_at'), json_extract(payload, '$.end_at'), $start)";
        if (($filter['week'] ?? '') !== '') {
            [$monday, $sunday] = EventStore::weekBounds($filter['week']);
            $where[] = "substr($start, 1, 10) <= :sunday AND substr($end, 1, 10) >= :monday";
            $parameters[':monday'] = $monday;
            $parameters[':sunday'] = $sunday;
        }
        $condition = implode(' AND ', $where);

        $count = $this->pdo->prepare('SELECT COUNT(*) FROM candidate WHERE ' . $condition);
        $count->execute($parameters);
        $statement = $this->pdo->prepare(
            "SELECT * FROM candidate WHERE $condition
             ORDER BY COALESCE($start, '9999-12-31'), created_at, id
             LIMIT " . max(1, min(500, $limit)) . ' OFFSET ' . max(0, $offset));
        $statement->execute($parameters);

        return [
            'total' => (int) $count->fetchColumn(),
            'candidates' => array_map([$this, 'present'], $statement->fetchAll()),
        ];
    }

    /** @return array<string, mixed>|null */
    public function get(string $id): ?array
    {
        $row = $this->row($id);
        if ($row === null) {
            return null;
        }
        $candidate = $this->present($row);
        $reviews = $this->pdo->prepare(
            'SELECT id, event_id, score, breakdown, state, created_at, decided_at, note
             FROM match_review WHERE candidate_id = ? ORDER BY score DESC');
        $reviews->execute([$id]);
        $candidate['match_reviews'] = array_map([$this, 'presentReview'], $reviews->fetchAll());

        return $candidate;
    }

    /**
     * Založí nebo obnoví kandidáta a hned ho porovná s publikovanými akcemi.
     * Jistá shoda připojí zdroj k existující akci a kandidáta uzavře;
     * nejistá jde do fronty. Nová akce tu nikdy nevznikne.
     *
     * @param array<string, mixed> $data {id?, source_id?, discovery_method, payload, state?}
     * @return array{result: string, candidate: array<string, mixed>, match?: array<string, mixed>}
     */
    public function submit(array $data, Actor $actor, ?string $runId): array
    {
        $payload = $data['payload'] ?? null;
        $method = $data['discovery_method'] ?? null;
        $errors = [];
        if (!is_array($payload) || $payload === [] || array_is_list($payload)) {
            $errors['payload'] = 'Povinný objekt kandidáta.';
        }
        if (!is_string($method) || preg_match('/^[a-z]+(-[a-z]+)*$/', $method) !== 1) {
            $errors['discovery_method'] = 'Slug způsobu nálezu, např. adapter, known-source, inbox.';
        }
        $state = $data['state'] ?? 'new';
        if (!in_array($state, self::OPEN_STATES, true)) {
            $errors['state'] = 'Nový kandidát je v jednom z otevřených stavů: '
                . implode(', ', self::OPEN_STATES) . '.';
        }
        $sourceId = $data['source_id'] ?? null;
        if ($sourceId !== null && (!is_string($sourceId) || !(new Catalog($this->pdo))->sourceExists($sourceId))) {
            $errors['source_id'] = 'Zdroj není v registru.';
        }
        $id = $data['id'] ?? null;
        if ($id !== null && (!is_string($id) || preg_match('/^[a-z0-9][a-z0-9:._-]{0,159}$/', $id) !== 1)) {
            $errors['id'] = 'ID kandidáta: malá písmena, číslice a znaky :._-, nejvýš 160 znaků.';
        }
        if ($errors !== []) {
            throw DomainError::invalid($errors);
        }
        $encoded = (string) json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
        $id ??= $method . '-' . substr(hash('sha256', $encoded), 0, 16);

        return Transaction::run($this->pdo, function () use ($id, $sourceId, $method, $payload, $encoded,
                                                  $state, $actor, $runId): array {
            $existing = $this->row($id);
            if ($existing !== null && in_array($existing['state'], self::CLOSED_STATES, true)) {
                return ['result' => 'closed', 'candidate' => $this->present($existing)];
            }
            if ($existing === null) {
                $this->pdo->prepare(
                    'INSERT INTO candidate (id, source_id, discovery_method, payload, state, created_at)
                     VALUES (?, ?, ?, ?, ?, ?)',
                )->execute([$id, $sourceId, $method, $encoded, $state, $this->clock->timestamp()]);
                $this->log->record($actor, $runId, 'candidate', $id, 'create', null,
                    ['payload' => $payload, 'state' => $state], null);
                $result = 'created';
            } elseif ($existing['payload'] !== $encoded) {
                $this->pdo->prepare('UPDATE candidate SET payload = ? WHERE id = ?')
                    ->execute([$encoded, $id]);
                $this->log->record($actor, $runId, 'candidate', $id, 'update',
                    ['payload' => json_decode((string) $existing['payload'], true)],
                    ['payload' => $payload], null);
                $result = 'updated';
            } else {
                $result = 'unchanged';
            }

            $match = $this->match($id, $payload, $sourceId, $actor, $runId);
            $response = ['result' => $match !== null && $match['decision'] === 'auto-merge'
                ? 'merged' : $result, 'candidate' => $this->present($this->row($id))];
            if ($match !== null) {
                $response['match'] = $match;
            }

            return $response;
        });
    }

    /**
     * Rozhodnutí o kandidátovi. `imported` vyžaduje existující akci,
     * `rejected` důvod; ostatní stavy kandidáta vrací do fronty.
     *
     * @return array<string, mixed>
     */
    public function resolve(string $id, string $state, ?string $eventId, string $note,
                            Actor $actor, ?string $runId): array
    {
        if (!in_array($state, array_merge(self::CLOSED_STATES, ['needs-verification', 'quarantined']), true)) {
            throw DomainError::invalid(['state' => 'Jedna z hodnot imported, rejected, '
                . 'needs-verification, quarantined.']);
        }

        return Transaction::run($this->pdo, function () use ($id, $state, $eventId, $note, $actor, $runId): array {
            $this->close($id, $state, $eventId, $note, $actor, $runId);

            return $this->get($id);
        });
    }

    /**
     * Rozhodne nejistou shodu. `merged` připojí zdroj kandidáta k akci a
     * kandidáta uzavře, `separate` jen zapíše, že jde o jinou akci.
     *
     * @return array<string, mixed>
     */
    public function decideReview(int $reviewId, string $decision, string $note,
                                 Actor $actor, ?string $runId): array
    {
        if (!in_array($decision, ['merged', 'separate'], true)) {
            throw DomainError::invalid(['decision' => 'Jedna z hodnot merged, separate.']);
        }

        return Transaction::run($this->pdo, function () use ($reviewId, $decision, $note, $actor, $runId): array {
            $statement = $this->pdo->prepare('SELECT * FROM match_review WHERE id = ?');
            $statement->execute([$reviewId]);
            $review = $statement->fetch();
            if ($review === false) {
                throw DomainError::notFound('Shoda ' . $reviewId . ' neexistuje.');
            }
            if ($review['state'] !== 'pending') {
                throw DomainError::conflict('Shoda už je rozhodnutá.', 'review-decided');
            }
            if ($decision === 'merged') {
                $candidate = $this->row((string) $review['candidate_id']);
                $url = self::sourceUrl(json_decode((string) $candidate['payload'], true) ?: []);
                if ($url !== null) {
                    $store = new EventStore($this->pdo, new Catalog($this->pdo), $this->clock);
                    $before = $store->load((string) $review['event_id']);
                    if ($store->addSource((string) $review['event_id'], $url, $candidate['source_id'])) {
                        $this->log->record($actor, $runId, 'event', (string) $review['event_id'],
                            'add-source', $before, $store->load((string) $review['event_id']), $note);
                    }
                }
                $this->close((string) $review['candidate_id'], 'imported',
                    (string) $review['event_id'], $note, $actor, $runId);
            } else {
                $this->pdo->prepare(
                    "UPDATE match_review SET state = 'separate', decided_at = ?, note = ? WHERE id = ?",
                )->execute([$this->clock->timestamp(), $note, $reviewId]);
                $this->log->record($actor, $runId, 'match_review', (string) $reviewId, 'separate',
                    ['state' => 'pending'], ['state' => 'separate'], $note);
            }

            return $this->get((string) $review['candidate_id']);
        });
    }

    /** @return list<array<string, mixed>> */
    public function pendingReviews(int $limit = 100): array
    {
        $statement = $this->pdo->query(
            "SELECT r.*, c.payload FROM match_review r JOIN candidate c ON c.id = r.candidate_id
             WHERE r.state = 'pending' ORDER BY r.score DESC, r.id LIMIT " . max(1, min(500, $limit)));

        return array_map(function (array $row): array {
            $review = $this->presentReview($row);
            $review['candidate_id'] = (string) $row['candidate_id'];
            $review['candidate'] = json_decode((string) $row['payload'], true);

            return $review;
        }, $statement->fetchAll());
    }

    /**
     * Uzavře nebo přeřadí kandidáta. Bez vlastní transakce, aby šla volat
     * uvnitř publikace akce.
     */
    public function close(string $id, string $state, ?string $eventId, string $note,
                          Actor $actor, ?string $runId): void
    {
        $row = $this->row($id) ?? throw DomainError::notFound('Kandidát ' . $id . ' neexistuje.');
        if (trim($note) === '') {
            throw DomainError::invalid(['note' => 'Rozhodnutí o kandidátovi vyžaduje poznámku.']);
        }
        if ($state === 'imported') {
            if ($eventId === null || !(new EventStore($this->pdo, new Catalog($this->pdo), $this->clock))->exists($eventId)) {
                throw DomainError::invalid(['event_id' => 'Stav imported vyžaduje existující akci.']);
            }
        } else {
            $eventId = null;
        }
        if (in_array($row['state'], self::CLOSED_STATES, true)) {
            if ($row['state'] === $state && $row['event_id'] === $eventId) {
                return;
            }
            throw DomainError::conflict('Kandidát už je uzavřený ve stavu ' . $row['state']
                . '; rozporné rozhodnutí nebylo zapsáno.', 'candidate-closed');
        }

        $now = $this->clock->timestamp();
        $closed = in_array($state, self::CLOSED_STATES, true);
        $this->pdo->prepare(
            'UPDATE candidate SET state = :state, event_id = :event, reviewed_at = :now,
                notes = CASE WHEN notes IS NULL OR notes = \'\' THEN :note ELSE notes || \' \' || :note END
             WHERE id = :id',
        )->execute([':state' => $state, ':event' => $eventId, ':now' => $closed ? $now : null,
            ':note' => $note, ':id' => $id]);
        if ($closed) {
            $this->pdo->prepare(
                "UPDATE match_review SET state = CASE WHEN event_id = :event THEN 'merged' ELSE 'separate' END,
                    decided_at = :now, note = :note
                 WHERE candidate_id = :id AND state = 'pending'",
            )->execute([':event' => $eventId ?? '', ':now' => $now, ':note' => $note, ':id' => $id]);
        }
        $this->log->record($actor, $runId, 'candidate', $id, 'resolve',
            ['state' => $row['state'], 'event_id' => $row['event_id']],
            ['state' => $state, 'event_id' => $eventId], $note);
    }

    /**
     * @param array<string, mixed> $payload
     * @return array<string, mixed>|null nejlepší shoda
     */
    private function match(string $id, array $payload, ?string $sourceId, Actor $actor,
                           ?string $runId): ?array
    {
        $fields = is_array($payload['normalized'] ?? null) ? $payload['normalized'] : $payload;
        $municipalityId = isset($fields['municipality_id']) && is_int($fields['municipality_id'])
            ? $fields['municipality_id'] : null;
        if ($municipalityId === null && is_string($fields['municipality'] ?? null)) {
            $municipalityId = (new Catalog($this->pdo))->municipality($fields['municipality'])['id'] ?? null;
        }
        $matches = (new Matcher($this->pdo))->matches([
            'title' => is_string($fields['title'] ?? null) ? $fields['title'] : null,
            'start_at' => is_string($fields['start_at'] ?? null) ? $fields['start_at'] : null,
            'venue' => is_string($fields['venue'] ?? null) ? $fields['venue'] : null,
            'municipality_id' => $municipalityId,
        ]);
        if ($matches === []) {
            return null;
        }
        $best = $matches[0];
        if ($best['decision'] === 'auto-merge') {
            $url = self::sourceUrl($payload);
            if ($url !== null) {
                $store = new EventStore($this->pdo, new Catalog($this->pdo), $this->clock);
                $before = $store->load($best['event_id']);
                if ($store->addSource($best['event_id'], $url, $sourceId)) {
                    $this->log->record($actor, $runId, 'event', $best['event_id'], 'add-source',
                        $before, $store->load($best['event_id']), 'Automatická shoda s kandidátem ' . $id . '.');
                }
            }
            $this->close($id, 'imported', $best['event_id'],
                sprintf('Automatická shoda se skóre %.4f.', $best['score']), $actor, $runId);
        } elseif ($best['decision'] === 'review') {
            $this->pdo->prepare(
                "INSERT INTO match_review (candidate_id, event_id, score, breakdown, state, created_at)
                 VALUES (?, ?, ?, ?, 'pending', ?) ON CONFLICT(candidate_id, event_id)
                 DO UPDATE SET score = excluded.score, breakdown = excluded.breakdown",
            )->execute([$id, $best['event_id'], $best['score'],
                json_encode($best['breakdown'], JSON_UNESCAPED_UNICODE), $this->clock->timestamp()]);
        }

        return $best['decision'] === 'separate' ? null : $best;
    }

    /** @param array<string, mixed> $payload */
    private static function sourceUrl(array $payload): ?string
    {
        foreach ([
            $payload['normalized']['canonical_url'] ?? null,
            $payload['normalized']['source']['url'] ?? null,
            $payload['verified_source_url'] ?? null,
            $payload['source_url'] ?? null,
            $payload['source']['url'] ?? null,
        ] as $url) {
            if (is_string($url) && $url !== '') {
                return $url;
            }
        }

        return null;
    }

    /** @return array<string, mixed>|null */
    private function row(string $id): ?array
    {
        $statement = $this->pdo->prepare('SELECT * FROM candidate WHERE id = ?');
        $statement->execute([$id]);
        $row = $statement->fetch();

        return $row === false ? null : $row;
    }

    /** @return array<string, mixed> */
    private function present(array $row): array
    {
        return [
            'id' => (string) $row['id'],
            'state' => (string) $row['state'],
            'source_id' => $row['source_id'],
            'discovery_method' => (string) $row['discovery_method'],
            'event_id' => $row['event_id'],
            'created_at' => $row['created_at'],
            'reviewed_at' => $row['reviewed_at'],
            'notes' => $row['notes'],
            'payload' => json_decode((string) $row['payload'], true),
        ];
    }

    /** @return array<string, mixed> */
    private function presentReview(array $row): array
    {
        return [
            'id' => (int) $row['id'],
            'event_id' => (string) $row['event_id'],
            'score' => (float) $row['score'],
            'breakdown' => json_decode((string) $row['breakdown'], true),
            'state' => (string) $row['state'],
            'created_at' => $row['created_at'],
            'decided_at' => $row['decided_at'],
            'note' => $row['note'],
        ];
    }

}
