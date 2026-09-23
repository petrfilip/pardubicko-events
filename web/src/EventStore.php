<?php

declare(strict_types=1);

namespace Pardubicko;

use DateInterval;
use DatePeriod;
use DateTimeImmutable;
use PDO;

/**
 * Zápis a čtení akcí v kanonickém tvaru.
 *
 * Kanonický tvar je zároveň to, co vrací API a co se ukládá do historie
 * změn jako `before` a `after`. Obsahuje i odvozené údaje (týdny, zdroje),
 * aby historie ukazovala, jak akce vypadala celá.
 */
final class EventStore
{
    public function __construct(
        private readonly PDO $pdo,
        private readonly Catalog $catalog,
        private readonly Clock $clock,
    ) {
    }

    /** @return array<string, mixed>|null */
    public function load(string $id): ?array
    {
        $statement = $this->pdo->prepare('SELECT * FROM event WHERE id = ?');
        $statement->execute([$id]);
        $row = $statement->fetch();
        if ($row === false) {
            return null;
        }
        $categories = $this->pdo->prepare(
            'SELECT category_id FROM event_category WHERE event_id = ? ORDER BY position');
        $categories->execute([$id]);
        $weeks = $this->pdo->prepare(
            'SELECT w.id FROM event_week ew JOIN week w ON w.id = ew.week_id
             WHERE ew.event_id = ? ORDER BY w.position');
        $weeks->execute([$id]);
        $sources = $this->pdo->prepare(
            'SELECT url, source_id, first_seen_at, last_seen_at FROM event_source
             WHERE event_id = ? ORDER BY first_seen_at, url');
        $sources->execute([$id]);

        return [
            'id' => (string) $row['id'],
            'status' => (string) $row['status'],
            'title' => (string) $row['title'],
            'description' => $row['description'],
            'start_at' => (string) $row['start_at'],
            'end_at' => $row['end_at'],
            'all_day' => (bool) $row['all_day'],
            'venue' => $row['venue'],
            'municipality' => (string) $row['municipality_name'],
            'municipality_id' => $row['municipality_id'] === null ? null : (int) $row['municipality_id'],
            'categories' => array_map('strval', $categories->fetchAll(PDO::FETCH_COLUMN)),
            'price' => [
                'type' => (string) $row['price_type'],
                'text' => $row['price_text'],
                'amount' => $row['price_amount'] === null ? null : $row['price_amount'] + 0,
                'currency' => $row['price_currency'],
            ],
            'source' => ['type' => (string) $row['source_type'], 'url' => (string) $row['source_url']],
            'cancelled' => (bool) $row['cancelled'],
            'last_verified_at' => $row['last_verified_at'],
            'updated_at' => $row['updated_at'],
            'weeks' => array_map('strval', $weeks->fetchAll(PDO::FETCH_COLUMN)),
            'sources' => $sources->fetchAll(),
        ];
    }

    public function exists(string $id): bool
    {
        $statement = $this->pdo->prepare('SELECT 1 FROM event WHERE id = ?');
        $statement->execute([$id]);

        return $statement->fetchColumn() !== false;
    }

    /** ID ze slugu názvu, obce a data; při kolizi s číselnou příponou. */
    public function newId(array $event): string
    {
        $title = substr(Slug::make((string) $event['title']), 0, 70);
        $base = trim(implode('-', array_filter([
            rtrim($title, '-'),
            Slug::make((string) $event['municipality']),
            substr((string) $event['start_at'], 0, 10),
        ])), '-');
        $id = $base;
        for ($suffix = 2; $this->exists($id); $suffix++) {
            $id = $base . '-' . $suffix;
        }

        return $id;
    }

    /** @param array<string, mixed> $event kanonická akce z EventInput */
    public function insert(array $event, string $status = 'published'): void
    {
        $now = $this->clock->timestamp();
        $statement = $this->pdo->prepare(
            'INSERT INTO event (id, title, description, start_at, end_at, all_day, venue,
                municipality_name, municipality_id, price_type, price_text, price_amount,
                price_currency, source_type, source_url, cancelled, status, last_verified_at,
                first_seen_at, last_seen_at, match_title_norm, updated_at)
             VALUES (:id, :title, :description, :start_at, :end_at, :all_day, :venue,
                :municipality, :municipality_id, :price_type, :price_text, :price_amount,
                :price_currency, :source_type, :source_url, :cancelled, :status,
                :last_verified_at, :now, :now, :norm, :now)',
        );
        $statement->execute($this->row($event) + [':status' => $status, ':now' => $now]);
        $this->addSource((string) $event['id'], (string) $event['source']['url'], null);
        $this->writeDerived($event);
    }

    /** @param array<string, mixed> $event kanonická akce z EventInput */
    public function update(array $event): void
    {
        $statement = $this->pdo->prepare(
            'UPDATE event SET title = :title, description = :description,
                start_at = :start_at, end_at = :end_at, all_day = :all_day, venue = :venue,
                municipality_name = :municipality, municipality_id = :municipality_id,
                price_type = :price_type, price_text = :price_text,
                price_amount = :price_amount, price_currency = :price_currency,
                source_type = :source_type, source_url = :source_url,
                cancelled = :cancelled, last_verified_at = :last_verified_at,
                match_title_norm = :norm, updated_at = :now
             WHERE id = :id',
        );
        $statement->execute($this->row($event) + [':now' => $this->clock->timestamp()]);
        $this->addSource((string) $event['id'], (string) $event['source']['url'], null);
        $this->writeDerived($event);
    }

    public function setStatus(string $id, string $status): void
    {
        $statement = $this->pdo->prepare(
            'UPDATE event SET status = :status, updated_at = :now WHERE id = :id');
        $statement->execute([':status' => $status, ':now' => $this->clock->timestamp(), ':id' => $id]);
    }

    public function setCancelled(string $id, bool $cancelled): void
    {
        $statement = $this->pdo->prepare(
            'UPDATE event SET cancelled = :cancelled, updated_at = :now WHERE id = :id');
        $statement->execute([
            ':cancelled' => $cancelled ? 1 : 0,
            ':now' => $this->clock->timestamp(),
            ':id' => $id,
        ]);
    }

    /** Přidá zdroj; existující vazba jen posune `last_seen_at`. Vrací true u nové. */
    public function addSource(string $eventId, string $url, ?string $sourceId): bool
    {
        $now = $this->clock->timestamp();
        $exists = $this->pdo->prepare('SELECT 1 FROM event_source WHERE event_id = ? AND url = ?');
        $exists->execute([$eventId, $url]);
        $new = $exists->fetchColumn() === false;
        $statement = $this->pdo->prepare(
            'INSERT INTO event_source (event_id, source_id, url, first_seen_at, last_seen_at)
             VALUES (:event, :source, :url, :now, :now)
             ON CONFLICT(event_id, url) DO UPDATE SET last_seen_at = excluded.last_seen_at,
                source_id = COALESCE(event_source.source_id, excluded.source_id)',
        );
        $statement->execute([':event' => $eventId, ':source' => $sourceId, ':url' => $url, ':now' => $now]);

        return $new;
    }

    public function touchCatalog(): void
    {
        $statement = $this->pdo->prepare(
            "INSERT INTO repo_meta (key, value) VALUES ('catalog_updated_at', :now)
             ON CONFLICT(key) DO UPDATE SET value = excluded.value");
        $statement->execute([':now' => $this->clock->timestamp()]);
    }

    /**
     * Zařazení do týdnů podle ADR 0006: akce patří do každého týdne, se
     * kterým se překrývá. Týden, ve kterém akce začíná, se založí, pokud
     * ještě neexistuje; dřívější týdny dlouhé akce se nezakládají, jinak by
     * jedna letní výstava vyrobila řadu jinak prázdných týdnů. Přepočet
     * převzatých dat týdny nezakládá vůbec, jen doplní chybějící vazby.
     */
    public function syncWeeks(string $eventId, bool $createStartWeek = true): void
    {
        $statement = $this->pdo->prepare('SELECT start_at, end_at FROM event WHERE id = ?');
        $statement->execute([$eventId]);
        $row = $statement->fetch();
        if ($row === false) {
            return;
        }
        if ($createStartWeek) {
            $this->ensureWeek(self::weekOf(substr((string) $row['start_at'], 0, 10)));
        }

        $from = substr((string) $row['start_at'], 0, 10);
        $to = substr((string) ($row['end_at'] ?? $row['start_at']), 0, 10);
        $this->pdo->prepare('DELETE FROM event_week WHERE event_id = ?')->execute([$eventId]);
        $link = $this->pdo->prepare(
            'INSERT INTO event_week (event_id, week_id, position)
             SELECT :event, id, 0 FROM week WHERE date_from <= :to AND date_to >= :from');
        $link->execute([':event' => $eventId, ':from' => $from, ':to' => $to]);
    }

    /** Založí týden a přiřadí do něj všechny akce, které ho překrývají. */
    public function ensureWeek(string $weekId): void
    {
        $exists = $this->pdo->prepare('SELECT 1 FROM week WHERE id = ?');
        $exists->execute([$weekId]);
        if ($exists->fetchColumn() !== false) {
            return;
        }
        [$from, $to] = self::weekBounds($weekId);
        $insert = $this->pdo->prepare(
            "INSERT INTO week (id, date_from, date_to, file, generated_at, position)
             VALUES (:id, :from, :to, '', :now, :position)");
        $insert->execute([
            ':id' => $weekId,
            ':from' => $from,
            ':to' => $to,
            ':now' => $this->clock->timestamp(),
            ':position' => (int) substr($weekId, 0, 4) * 100 + (int) substr($weekId, 6, 2),
        ]);
        $link = $this->pdo->prepare(
            "INSERT OR IGNORE INTO event_week (event_id, week_id, position)
             SELECT id, :week, 0 FROM event
             WHERE substr(start_at, 1, 10) <= :to
               AND substr(COALESCE(end_at, start_at), 1, 10) >= :from");
        $link->execute([':week' => $weekId, ':from' => $from, ':to' => $to]);
    }

    public static function weekOf(string $date): string
    {
        return (new DateTimeImmutable($date))->format('o-\WW');
    }

    /** @return array{0: string, 1: string} pondělí a neděle */
    public static function weekBounds(string $weekId): array
    {
        $monday = (new DateTimeImmutable())->setISODate(
            (int) substr($weekId, 0, 4), (int) substr($weekId, 6, 2), 1);

        return [$monday->format('Y-m-d'), $monday->modify('+6 days')->format('Y-m-d')];
    }

    /** Týdny, se kterými se rozsah překrývá; pro filtr kandidátů. */
    public static function weeksBetween(string $from, string $to): array
    {
        $weeks = [];
        $period = new DatePeriod(new DateTimeImmutable($from), new DateInterval('P1D'),
            (new DateTimeImmutable($to))->modify('+1 day'));
        foreach ($period as $day) {
            $weeks[$day->format('o-\WW')] = true;
        }

        return array_keys($weeks);
    }

    /** @param array<string, mixed> $event */
    private function writeDerived(array $event): void
    {
        $id = (string) $event['id'];
        $this->pdo->prepare('DELETE FROM event_category WHERE event_id = ?')->execute([$id]);
        $insert = $this->pdo->prepare(
            'INSERT INTO event_category (event_id, position, name, category_id) VALUES (?, ?, ?, ?)');
        foreach (array_values($event['categories']) as $position => $categoryId) {
            $insert->execute([$id, $position, $categoryId, $categoryId]);
        }

        $this->pdo->prepare('DELETE FROM event_fts WHERE event_id = ?')->execute([$id]);
        $this->pdo->prepare(
            'INSERT INTO event_fts (event_id, title, description, venue, municipality, categories)
             VALUES (?, ?, ?, ?, ?, ?)',
        )->execute([
            $id, $event['title'], $event['description'] ?? '', $event['venue'] ?? '',
            $event['municipality'], $this->catalog->categorySearchTerms($event['categories']),
        ]);

        $this->syncWeeks($id);
        $this->touchCatalog();
    }

    /**
     * @param array<string, mixed> $event
     * @return array<string, mixed>
     */
    private function row(array $event): array
    {
        return [
            ':id' => $event['id'],
            ':title' => $event['title'],
            ':description' => $event['description'],
            ':start_at' => $event['start_at'],
            ':end_at' => $event['end_at'],
            ':all_day' => $event['all_day'] ? 1 : 0,
            ':venue' => $event['venue'],
            ':municipality' => $event['municipality'],
            ':municipality_id' => $event['municipality_id'],
            ':price_type' => $event['price']['type'],
            ':price_text' => $event['price']['text'],
            ':price_amount' => $event['price']['amount'],
            ':price_currency' => $event['price']['currency'],
            ':source_type' => $event['source']['type'],
            ':source_url' => $event['source']['url'],
            ':cancelled' => $event['cancelled'] ? 1 : 0,
            ':last_verified_at' => $event['last_verified_at'],
            ':norm' => Text::fold((string) $event['title']),
        ];
    }
}
