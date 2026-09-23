<?php

declare(strict_types=1);

namespace Pardubicko;

use PDO;

/**
 * Operace nad akcemi. Stejné volá API agentů i administrace (ADR 0008).
 *
 * Každá operace je jedna transakce: změna dat, odvozené indexy a záznam v
 * historii změn buď proběhnou všechny, nebo žádná. Agent publikuje sám,
 * takže pojistky jsou tady a ne v jeho instrukcích:
 *
 * - kontrola dat podle `EventInput`,
 * - denní limit publikací na token,
 * - deduplikace proti publikovaným akcím: jistá shoda zápis odmítne,
 *   nejistá jde do fronty `match_review`, pokud agent výslovně neuvede,
 *   že jde o jinou akci.
 */
final class EventService
{
    private readonly Catalog $catalog;
    private readonly EventStore $store;
    private readonly ChangeLog $log;
    private readonly Matcher $matcher;

    public function __construct(
        private readonly PDO $pdo,
        private readonly Clock $clock,
    ) {
        $this->catalog = new Catalog($pdo);
        $this->store = new EventStore($pdo, $this->catalog, $clock);
        $this->log = new ChangeLog($pdo, $clock);
        $this->matcher = new Matcher($pdo);
    }

    /**
     * Publikuje ověřenou akci.
     *
     * @param array<string, mixed> $input akce ve tvaru EventInput
     * @param list<string> $distinctFrom ID akcí, o kterých agent ví, že jde o jiné
     * @return array{status: string, event?: array<string, mixed>, candidate_id?: string, matches?: list<array<string, mixed>>}
     */
    public function publish(array $input, Actor $actor, ?string $runId, ?string $note,
                            ?string $candidateId = null, array $distinctFrom = []): array
    {
        $event = EventInput::normalize($input, $this->catalog, $this->clock);
        $candidate = $candidateId !== null ? $this->openCandidate($candidateId) : null;

        if ($actor->isAgent() && $actor->dailyPublishLimit !== null
            && $this->log->publicationsToday($actor) >= $actor->dailyPublishLimit) {
            throw new DomainError(sprintf(
                'Denní limit %d publikací pro token %s je vyčerpaný.',
                $actor->dailyPublishLimit, $actor->name), 429, 'limit-exceeded');
        }
        if ($event['id'] !== null && $this->store->exists($event['id'])) {
            throw DomainError::conflict(
                'Akce s tímto ID už existuje; úpravu pošli jako PATCH.', 'id-exists',
                ['existing_event_id' => $event['id']]);
        }

        $matches = $this->matcher->matches($event);
        $certain = array_values(array_filter($matches,
            static fn (array $match): bool => $match['decision'] === 'auto-merge'));
        if ($certain !== []) {
            throw DomainError::conflict(
                'Stejná akce už je publikovaná. Pokud jde o další zdroj, přidej ho přes '
                . 'POST /api/v1/events/{id}/sources.', 'duplicate',
                ['existing_event_id' => $certain[0]['event_id'], 'matches' => $certain]);
        }
        $uncertain = array_values(array_filter($matches,
            static fn (array $match): bool => $match['decision'] === 'review'
                && !in_array($match['event_id'], $distinctFrom, true)));

        return Transaction::run($this->pdo, function () use ($event, $actor, $runId, $note, $candidate,
                                                  $uncertain, $input): array {
            if ($uncertain !== []) {
                $candidateId = $candidate['id'] ?? $this->queueCandidate($input, $actor, $runId);
                $this->queueReviews($candidateId, $uncertain);
                $this->log->record($actor, $runId, 'candidate', $candidateId, 'queue-review',
                    null, ['matches' => $uncertain], $note);

                return ['status' => 'review', 'candidate_id' => $candidateId, 'matches' => $uncertain];
            }

            $event['id'] ??= $this->store->newId($event);
            $this->store->insert($event);
            $after = $this->store->load($event['id']);
            $this->log->record($actor, $runId, 'event', $event['id'], 'publish', null, $after, $note);
            if ($candidate !== null) {
                $this->closeCandidate($candidate['id'], 'imported', $event['id'], $note, $actor, $runId);
            }

            return ['status' => 'published', 'event' => $after];
        });
    }

    /**
     * Upraví akci. Posílají se jen měněná pole; objekty `price` a `source`
     * se nahrazují celé. Bez výslovného `last_verified_at` se nastaví teď,
     * protože úprava znamená, že ji někdo znovu ověřil.
     *
     * @param array<string, mixed> $patch
     * @return array<string, mixed>
     */
    public function update(string $id, array $patch, Actor $actor, ?string $runId, string $note): array
    {
        $before = $this->mustLoad($id);
        if (array_key_exists('id', $patch) && $patch['id'] !== $id) {
            throw DomainError::invalid(['id' => 'ID akce se nemění.']);
        }
        $merged = array_merge(self::editable($before), $patch, ['id' => $id]);
        if (!array_key_exists('last_verified_at', $patch)) {
            $merged['last_verified_at'] = $this->clock->timestamp();
        }
        $event = EventInput::normalize($merged, $this->catalog, $this->clock);

        return Transaction::run($this->pdo, function () use ($id, $event, $before, $actor, $runId, $note): array {
            $this->store->update($event);
            $after = $this->store->load($id);
            $this->log->record($actor, $runId, 'event', $id, 'update', $before, $after, $note);

            return $after;
        });
    }

    /** @return array<string, mixed> */
    public function cancel(string $id, Actor $actor, ?string $runId, string $note): array
    {
        return $this->changeState($id, 'cancel', $actor, $runId, $note,
            fn () => $this->store->setCancelled($id, true));
    }

    /** Stáhne akci z veřejného webu; záznam i historie zůstávají. */
    public function withdraw(string $id, Actor $actor, ?string $runId, string $note): array
    {
        return $this->changeState($id, 'withdraw', $actor, $runId, $note,
            fn () => $this->store->setStatus($id, 'draft'));
    }

    /** @return array{added: bool, event: array<string, mixed>} */
    public function addSource(string $id, string $url, ?string $sourceId, Actor $actor,
                              ?string $runId, ?string $note): array
    {
        $before = $this->mustLoad($id);
        try {
            UrlNormalizer::normalize($url);
        } catch (\InvalidArgumentException $error) {
            throw DomainError::invalid(['url' => $error->getMessage()]);
        }
        if ($sourceId !== null && !$this->catalog->sourceExists($sourceId)) {
            throw DomainError::invalid(['source_id' => 'Zdroj není v registru.']);
        }

        return Transaction::run($this->pdo, function () use ($id, $url, $sourceId, $before, $actor, $runId, $note): array {
            $added = $this->store->addSource($id, $url, $sourceId);
            $after = $this->store->load($id);
            if ($added) {
                $this->log->record($actor, $runId, 'event', $id, 'add-source', $before, $after, $note);
            }

            return ['added' => $added, 'event' => $after];
        });
    }

    /** @return array<string, mixed> */
    private function changeState(string $id, string $action, Actor $actor, ?string $runId,
                                 string $note, callable $change): array
    {
        $before = $this->mustLoad($id);

        return Transaction::run($this->pdo, function () use ($id, $action, $before, $actor, $runId, $note, $change): array {
            $change();
            $this->store->touchCatalog();
            $after = $this->store->load($id);
            $this->log->record($actor, $runId, 'event', $id, $action, $before, $after, $note);

            return $after;
        });
    }

    /** @return array<string, mixed> */
    private function mustLoad(string $id): array
    {
        return $this->store->load($id) ?? throw DomainError::notFound('Akce ' . $id . ' neexistuje.');
    }

    /** @return array<string, mixed> */
    private function openCandidate(string $id): array
    {
        $statement = $this->pdo->prepare('SELECT id, state FROM candidate WHERE id = ?');
        $statement->execute([$id]);
        $row = $statement->fetch();
        if ($row === false) {
            throw DomainError::notFound('Kandidát ' . $id . ' neexistuje.');
        }
        if (in_array($row['state'], CandidateService::CLOSED_STATES, true)) {
            throw DomainError::conflict('Kandidát už je uzavřený ve stavu ' . $row['state'] . '.',
                'candidate-closed');
        }

        return $row;
    }

    /** Pole, která jdou poslat zpět do EventInput. */
    private static function editable(array $event): array
    {
        return array_intersect_key($event, array_flip([
            'title', 'description', 'start_at', 'end_at', 'all_day', 'venue', 'municipality',
            'categories', 'price', 'source', 'cancelled', 'last_verified_at',
        ]));
    }

    private function queueCandidate(array $input, Actor $actor, ?string $runId): string
    {
        $id = 'api-' . substr(hash('sha256', json_encode($input, JSON_UNESCAPED_UNICODE)
            . $actor->name), 0, 16);
        $statement = $this->pdo->prepare(
            "INSERT INTO candidate (id, discovery_method, payload, state, created_at, notes)
             VALUES (:id, 'api-publish', :payload, 'needs-verification', :now, :notes)
             ON CONFLICT(id) DO NOTHING");
        $statement->execute([
            ':id' => $id,
            ':payload' => json_encode(['normalized' => $input, 'submitted_by' => $actor->name,
                'run_id' => $runId], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES),
            ':now' => $this->clock->timestamp(),
            ':notes' => 'Publikace zastavena nejistou shodou s publikovanou akcí.',
        ]);

        return $id;
    }

    /** @param list<array<string, mixed>> $matches */
    private function queueReviews(string $candidateId, array $matches): void
    {
        $statement = $this->pdo->prepare(
            "INSERT INTO match_review (candidate_id, event_id, score, breakdown, state, created_at)
             VALUES (:candidate, :event, :score, :breakdown, 'pending', :now)
             ON CONFLICT(candidate_id, event_id) DO UPDATE SET
                score = excluded.score, breakdown = excluded.breakdown");
        foreach ($matches as $match) {
            $statement->execute([
                ':candidate' => $candidateId,
                ':event' => $match['event_id'],
                ':score' => $match['score'],
                ':breakdown' => json_encode($match['breakdown'], JSON_UNESCAPED_UNICODE),
                ':now' => $this->clock->timestamp(),
            ]);
        }
    }

    private function closeCandidate(string $id, string $state, ?string $eventId, ?string $note,
                                    Actor $actor, ?string $runId): void
    {
        (new CandidateService($this->pdo, $this->clock))
            ->close($id, $state, $eventId, $note ?? 'Publikováno.', $actor, $runId);
    }

}
