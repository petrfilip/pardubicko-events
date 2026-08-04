<?php

declare(strict_types=1);

namespace Pardubicko;

use DateTimeImmutable;
use PDO;

/**
 * Čtení publikovaných akcí.
 *
 * Tady je **jediná** implementace filtrování. Podmínky se skládají z
 * pevných řetězců a hodnoty se předávají výhradně jako vázané parametry;
 * z požadavku se do SQL nikdy nedostane text.
 *
 * Dvě věci, na kterých závisí správnost výpisu:
 *
 * 1. **Žádné JOINy na vazební tabulky.** Akce může být zařazená ve více
 *    týdnech (`event_week`) a mít několik kategorií (`event_category`).
 *    Filtr proto používá `EXISTS`, takže řádek akce zůstane jeden a
 *    stránkování i počty sedí. Kategorie se dotahují samostatným dotazem.
 * 2. **Stav akce se neukládá.** `past` / `ongoing` / `future` se odvozuje
 *    z `start_at`, `end_at` a aktuálního času (pravidlo z `README.md`).
 *    Výraz je konstanta `STATE_SQL` a používá se současně pro výpis stavu
 *    i pro filtr „pouze budoucí akce“, aby existoval jen jednou.
 */
final class EventRepository
{
    private const STATE_SQL = "CASE
            WHEN e.end_at IS NOT NULL AND datetime(e.end_at) < datetime(:now) THEN 'past'
            WHEN e.end_at IS NULL AND substr(e.start_at, 1, 10) < :today THEN 'past'
            WHEN datetime(e.start_at) <= datetime(:now) THEN 'ongoing'
            ELSE 'future'
        END";

    private const COLUMNS = 'e.id, e.title, e.description, e.start_at, e.end_at, e.all_day,
        e.venue, e.municipality_name, e.price_type, e.price_text, e.price_amount,
        e.price_currency, e.source_type, e.source_url, e.cancelled, e.last_verified_at';

    /** @var list<array{name: string, slug: string, event_count: int}>|null */
    private ?array $municipalities = null;

    private readonly DateTimeImmutable $now;

    public function __construct(
        private readonly PDO $pdo,
        ?DateTimeImmutable $now = null,
    ) {
        $this->now = $now ?? new DateTimeImmutable('now', Format::zone());
    }

    // -----------------------------------------------------------------
    // Výpisy
    // -----------------------------------------------------------------

    /**
     * Akce odpovídající filtru, seřazené podle začátku.
     *
     * @return list<array<string, mixed>>
     */
    public function search(EventFilter $filter, ?int $limit = null, int $offset = 0): array
    {
        $parameters = [];
        $where = implode(' AND ', $this->conditions($filter, $parameters));
        $parameters[':now'] = $this->nowValue();
        $parameters[':today'] = $this->todayValue();

        $sql = 'SELECT ' . self::COLUMNS . ', ' . self::STATE_SQL . ' AS state
            FROM event e
            WHERE ' . $where . '
            ORDER BY datetime(e.start_at), e.match_title_norm, e.id';

        if ($limit !== null) {
            $sql .= ' LIMIT :limit OFFSET :offset';
        }

        $statement = $this->pdo->prepare($sql);
        foreach ($parameters as $name => $value) {
            $statement->bindValue($name, $value);
        }
        if ($limit !== null) {
            $statement->bindValue(':limit', $limit, PDO::PARAM_INT);
            $statement->bindValue(':offset', $offset, PDO::PARAM_INT);
        }
        $statement->execute();

        return $this->withCategories($statement->fetchAll());
    }

    public function count(EventFilter $filter): int
    {
        $parameters = [];
        $where = implode(' AND ', $this->conditions($filter, $parameters));

        $statement = $this->pdo->prepare('SELECT COUNT(*) FROM event e WHERE ' . $where);
        $statement->execute($parameters);

        return (int) $statement->fetchColumn();
    }

    /** @return array<string, mixed>|null */
    public function byId(string $id): ?array
    {
        $statement = $this->pdo->prepare(
            'SELECT ' . self::COLUMNS . ', ' . self::STATE_SQL . " AS state
             FROM event e
             WHERE e.id = :id AND e.status = 'published'",
        );
        $statement->execute([
            ':id' => $id,
            ':now' => $this->nowValue(),
            ':today' => $this->todayValue(),
        ]);

        $event = $statement->fetch();
        if ($event === false) {
            return null;
        }

        return $this->withCategories([$event])[0];
    }

    // -----------------------------------------------------------------
    // Skládání podmínek
    // -----------------------------------------------------------------

    /**
     * @param array<string, string> $parameters
     * @return list<string>
     */
    private function conditions(EventFilter $filter, array &$parameters): array
    {
        $conditions = ["e.status = 'published'"];

        if ($filter->query !== '') {
            $match = FtsQuery::fromUserInput($filter->query);
            if ($match === null) {
                // Dotaz bez jediného použitelného tokenu (např. jen „???“).
                $conditions[] = '1 = 0';
            } else {
                $conditions[] = 'e.id IN (SELECT event_id FROM event_fts WHERE event_fts MATCH :fts)';
                $parameters[':fts'] = $match;
            }
        }

        if ($filter->municipality !== '') {
            $municipality = $this->municipalityBySlug($filter->municipality);
            if ($municipality === null) {
                $conditions[] = '1 = 0';
            } else {
                $conditions[] = 'e.municipality_name = :municipality';
                $parameters[':municipality'] = $municipality['name'];
            }
        }

        if ($filter->category !== '') {
            $conditions[] = 'EXISTS (SELECT 1 FROM event_category ec
                JOIN category c ON c.id = ec.category_id
                WHERE ec.event_id = e.id AND c.id = :category)';
            $parameters[':category'] = $filter->category;
        }

        if ($filter->price !== '') {
            $conditions[] = 'e.price_type = :price';
            $parameters[':price'] = $filter->price;
        }

        if ($filter->week !== '') {
            $conditions[] = 'EXISTS (SELECT 1 FROM event_week w
                WHERE w.event_id = e.id AND w.week_id = :week)';
            $parameters[':week'] = $filter->week;
        }

        if ($filter->from !== '' || $filter->to !== '') {
            // Překryv termínu s rozsahem. Porovnává se datum tak, jak je
            // zapsané (místní čas), ne převedené na UTC — jinak by akce
            // začínající o půlnoci spadla na předchozí den.
            $conditions[] = 'substr(e.start_at, 1, 10) <= :dateTo
                AND substr(COALESCE(e.end_at, e.start_at), 1, 10) >= :dateFrom';
            $parameters[':dateTo'] = $filter->to !== '' ? $filter->to : '9999-12-31';
            $parameters[':dateFrom'] = $filter->from !== '' ? $filter->from : '0000-01-01';
        }

        if ($filter->futureOnly) {
            $conditions[] = '(' . self::STATE_SQL . ") <> 'past'";
            $parameters[':now'] = $this->nowValue();
            $parameters[':today'] = $this->todayValue();
        }

        return $conditions;
    }

    // -----------------------------------------------------------------
    // Číselníky pro formulář filtrů
    // -----------------------------------------------------------------

    /** @return list<array{id: string, date_from: string, date_to: string, position: int}> */
    public function weeks(): array
    {
        return $this->pdo
            ->query('SELECT id, date_from, date_to, generated_at, position FROM week ORDER BY position')
            ->fetchAll();
    }

    /** @return array<string, mixed>|null */
    public function weekById(string $id): ?array
    {
        foreach ($this->weeks() as $week) {
            if ($week['id'] === $id) {
                return $week;
            }
        }

        return null;
    }

    /**
     * Nejbližší týden: ten, do kterého spadá dnešek, jinak první
     * následující, jinak poslední známý.
     *
     * @return array<string, mixed>|null
     */
    public function defaultWeek(): ?array
    {
        $weeks = $this->weeks();
        if ($weeks === []) {
            return null;
        }

        $today = $this->todayValue();
        foreach ($weeks as $week) {
            if ($week['date_from'] <= $today && $week['date_to'] >= $today) {
                return $week;
            }
        }
        foreach ($weeks as $week) {
            if ($week['date_from'] > $today) {
                return $week;
            }
        }

        return $weeks[count($weeks) - 1];
    }

    /** @return list<array{name: string, slug: string, event_count: int}> */
    public function municipalities(): array
    {
        if ($this->municipalities !== null) {
            return $this->municipalities;
        }

        $rows = $this->pdo->query(
            "SELECT municipality_name AS name, COUNT(*) AS event_count
             FROM event
             WHERE status = 'published' AND municipality_name <> ''
             GROUP BY municipality_name",
        )->fetchAll();

        $municipalities = [];
        foreach ($rows as $row) {
            $municipalities[] = [
                'name' => (string) $row['name'],
                'slug' => Slug::make((string) $row['name']),
                'event_count' => (int) $row['event_count'],
            ];
        }

        // Řazení podle slugu je bez rozšíření `intl` nejbližší české abecedě:
        // diakritika je odstraněná, takže „Ústí“ nekončí až za „Žamberk“.
        usort($municipalities, static fn (array $a, array $b): int => strcmp($a['slug'], $b['slug']));

        return $this->municipalities = $municipalities;
    }

    /** @return array{name: string, slug: string, event_count: int}|null */
    public function municipalityBySlug(string $slug): ?array
    {
        foreach ($this->municipalities() as $municipality) {
            if ($municipality['slug'] === $slug) {
                return $municipality;
            }
        }

        return null;
    }

    /** @return list<array{id: string, label: string, axis: string, event_count: int}> */
    public function categories(): array
    {
        $rows = $this->pdo->query(
            "SELECT c.id, c.label, COALESCE(c.axis, 'kind') AS axis,
                    COUNT(DISTINCT ec.event_id) AS event_count,
                    COALESCE(c.sort_order, 9999) AS sort_order
             FROM category c
             JOIN event_category ec ON ec.category_id = c.id
             JOIN event e ON e.id = ec.event_id
             WHERE e.status = 'published'
             GROUP BY c.id, c.label, c.axis, c.sort_order
             ORDER BY c.axis, sort_order, c.id",
        )->fetchAll();

        return array_map(
            static fn (array $row): array => [
                'id' => (string) $row['id'],
                'label' => (string) $row['label'],
                'axis' => (string) $row['axis'],
                'event_count' => (int) $row['event_count'],
            ],
            $rows,
        );
    }

    // -----------------------------------------------------------------
    // Doplňková data k akci
    // -----------------------------------------------------------------

    /** @return list<array{id: string, date_from: string, date_to: string}> */
    public function weeksForEvent(string $eventId): array
    {
        $statement = $this->pdo->prepare(
            'SELECT w.id, w.date_from, w.date_to
             FROM event_week ew
             JOIN week w ON w.id = ew.week_id
             WHERE ew.event_id = :id
             ORDER BY w.position',
        );
        $statement->execute([':id' => $eventId]);

        return $statement->fetchAll();
    }

    /** @return list<array{url: string, source_id: ?string, last_seen_at: ?string}> */
    public function sourcesForEvent(string $eventId): array
    {
        $statement = $this->pdo->prepare(
            'SELECT es.url, es.source_id, es.last_seen_at, s.name AS source_name
             FROM event_source es
             LEFT JOIN source s ON s.id = es.source_id
             WHERE es.event_id = :id
             ORDER BY es.url',
        );
        $statement->execute([':id' => $eventId]);

        return $statement->fetchAll();
    }

    /**
     * Kategorie pro načtenou stránku akcí. Samostatný dotaz místo JOINu —
     * viz poznámka v hlavičce třídy.
     *
     * @param list<array<string, mixed>> $events
     * @return list<array<string, mixed>>
     */
    private function withCategories(array $events): array
    {
        if ($events === []) {
            return [];
        }

        $placeholders = [];
        $parameters = [];
        foreach (array_values($events) as $index => $event) {
            $placeholders[] = ':id' . $index;
            $parameters[':id' . $index] = $event['id'];
        }

        $statement = $this->pdo->prepare(
            "SELECT ec.event_id, c.id, c.label, COALESCE(c.axis, 'kind') AS axis
             FROM event_category ec
             JOIN category c ON c.id = ec.category_id
             WHERE ec.event_id IN (" . implode(', ', $placeholders) . ')
             ORDER BY ec.event_id, ec.position',
        );
        $statement->execute($parameters);

        $byEvent = [];
        foreach ($statement->fetchAll() as $row) {
            $byEvent[(string) $row['event_id']][] = [
                'id' => (string) $row['id'],
                'label' => (string) $row['label'],
                'axis' => (string) $row['axis'],
            ];
        }

        foreach ($events as $index => $event) {
            $events[$index]['categories'] = $byEvent[(string) $event['id']] ?? [];
        }

        return array_values($events);
    }

    // -----------------------------------------------------------------
    // Sitemapa a provozní přehled
    // -----------------------------------------------------------------

    /** @return list<array{id: string, lastmod: ?string}> */
    public function publishedForSitemap(): array
    {
        return $this->pdo->query(
            "SELECT id, COALESCE(last_verified_at, last_seen_at, first_seen_at) AS lastmod
             FROM event
             WHERE status = 'published'
             ORDER BY datetime(start_at)",
        )->fetchAll();
    }

    /** @return array<string, int> */
    public function stats(): array
    {
        $row = $this->pdo->query(
            "SELECT
                (SELECT COUNT(*) FROM event WHERE status = 'published') AS events_published,
                (SELECT COUNT(*) FROM event) AS events_total,
                (SELECT COUNT(*) FROM week) AS weeks,
                (SELECT COUNT(*) FROM source) AS sources,
                (SELECT COUNT(*) FROM source WHERE enabled = 1) AS sources_enabled",
        )->fetch();

        return array_map('intval', $row === false ? [] : $row);
    }

    public function generatedAt(): ?string
    {
        $statement = $this->pdo->prepare('SELECT value FROM repo_meta WHERE key = :key');
        $statement->execute([':key' => 'manifest_generated_at']);
        $value = $statement->fetchColumn();

        return is_string($value) ? $value : null;
    }

    /** @return list<array<string, mixed>> */
    public function sourceHealth(): array
    {
        $rows = $this->pdo->query(
            "SELECT s.id, s.name, s.enabled,
                    COALESCE(h.state, 'unknown') AS state,
                    h.last_checked_at, h.last_success_at, h.last_item_at,
                    h.consecutive_failures, h.note,
                    f.fetched_at AS last_fetch_at, f.http_status, f.error AS last_fetch_error
             FROM source s
             LEFT JOIN source_health h ON h.source_id = s.id
             LEFT JOIN source_fetch f ON f.id = (
                 SELECT sf.id FROM source_fetch sf
                 WHERE sf.source_id = s.id ORDER BY sf.id DESC LIMIT 1
             )
             ORDER BY s.priority = 'high' DESC, s.id",
        )->fetchAll();

        return array_map(static function (array $row): array {
            $row['enabled'] = (bool) $row['enabled'];
            $row['consecutive_failures'] = (int) ($row['consecutive_failures'] ?? 0);
            $row['http_status'] = $row['http_status'] !== null ? (int) $row['http_status'] : null;
            return $row;
        }, $rows);
    }

    public function now(): DateTimeImmutable
    {
        return $this->now;
    }

    private function nowValue(): string
    {
        return $this->now->format('Y-m-d\TH:i:sP');
    }

    private function todayValue(): string
    {
        return $this->now->format('Y-m-d');
    }
}
