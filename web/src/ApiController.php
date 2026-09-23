<?php

declare(strict_types=1);

namespace Pardubicko;

use InvalidArgumentException;
use JsonException;
use PDO;

/**
 * HTTP API v1 pro agenty a pipeline (ADR 0008).
 *
 * Každý požadavek nese `Authorization: Bearer <token>`. Zápis navíc
 * `X-Run-Id` s označením běhu agenta, aby šlo celý běh dohledat a později
 * vrátit. Tělo zápisu je obálka: data jsou v pojmenovaném poli a vedle nich
 * `note` s důvodem změny.
 *
 * Chyby mají vždy tvar `{error, code, ...}`; u neplatných dat navíc
 * `fields` s důvodem pro každé pole.
 */
final class ApiController
{
    private const MAX_BODY = 262144;
    private const MAX_LIMIT = 200;

    private readonly EventService $events;
    private readonly EventStore $store;
    private readonly CandidateService $candidates;
    private readonly SourceService $sources;
    private readonly ChangeLog $log;

    public function __construct(
        private readonly PDO $pdo,
        private readonly Clock $clock,
    ) {
        $this->events = new EventService($pdo, $clock);
        $this->store = new EventStore($pdo, new Catalog($pdo), $clock);
        $this->candidates = new CandidateService($pdo, $clock);
        $this->sources = new SourceService($pdo, $clock);
        $this->log = new ChangeLog($pdo, $clock);
    }

    /**
     * @param array<string, mixed> $query
     * @param array<string, string> $headers
     */
    public function handle(string $method, string $path, array $query, array $headers, string $body): Response
    {
        $actor = (new TokenRepository($this->pdo, $this->clock))
            ->authenticate(self::bearer($headers));
        if ($actor === null) {
            return new Response(
                Response::json(['error' => 'Neplatný nebo chybějící token.', 'code' => 'unauthorized'])->body,
                401,
                ['Content-Type' => 'application/json; charset=utf-8', 'Cache-Control' => 'no-store',
                 'WWW-Authenticate' => 'Bearer'],
            );
        }
        if (strlen($body) > self::MAX_BODY) {
            return self::error('Tělo požadavku je příliš velké.', 413, 'too-large');
        }

        $runId = self::header($headers, 'X-Run-Id');
        $write = $method !== 'GET';
        if ($write && ($runId === null || preg_match('/^[A-Za-z0-9][A-Za-z0-9:._-]{0,119}$/', $runId) !== 1)) {
            return self::error('Zápis vyžaduje hlavičku X-Run-Id s označením běhu.', 400, 'run-id-required');
        }

        try {
            $input = $write ? self::decode($body) : [];
            $response = $this->route($actor, $runId, $method, $path, $query, $input);
        } catch (DomainError $error) {
            return Response::json($error->toArray(), $error->status);
        } catch (JsonException) {
            return self::error('Tělo musí být JSON objekt.', 400, 'invalid-json');
        }

        return $response ?? self::error('Neznámý endpoint. Přehled je v GET /api/v1.', 404, 'not-found');
    }

    /**
     * @param array<string, mixed> $query
     * @param array<string, mixed> $input
     */
    private function route(Actor $actor, ?string $runId, string $method, string $path,
                           array $query, array $input): ?Response
    {
        $id = '(?P<id>[a-z0-9][a-z0-9:._-]*)';
        $router = new Router();

        $router->get('~^/api/v1/?$~', fn (): Response => $this->index());
        $router->get('~^/api/v1/me$~', fn (): Response => Response::json([
            'token' => $actor->name,
            'daily_publish_limit' => $actor->dailyPublishLimit,
            'published_today' => $this->log->publicationsToday($actor),
        ]));
        $router->get('~^/api/v1/taxonomy$~', fn (): Response => $this->taxonomy());

        $router->get('~^/api/v1/events$~', function () use ($query): Response {
            [$limit, $offset] = self::page($query);
            return Response::json((new EventQuery($this->pdo, $this->store))->search($query, $limit, $offset)
                + ['limit' => $limit, 'offset' => $offset]);
        });
        $router->post('~^/api/v1/events$~', function () use ($actor, $runId, $input): Response {
            $result = $this->events->publish(
                self::object($input, 'event'), $actor, $runId, self::optionalNote($input),
                self::optionalString($input, 'candidate_id'), self::stringList($input, 'distinct_from'));
            return Response::json($result, $result['status'] === 'published' ? 201 : 202);
        });
        $router->get("~^/api/v1/events/$id$~", fn (array $p): Response =>
            Response::json($this->store->load($p['id'])
                ?? throw DomainError::notFound('Akce ' . $p['id'] . ' neexistuje.')));
        $router->patch("~^/api/v1/events/$id$~", fn (array $p): Response => Response::json(
            $this->events->update($p['id'], self::object($input, 'changes'), $actor, $runId, self::note($input))));
        $router->get("~^/api/v1/events/$id/history$~", fn (array $p): Response => Response::json([
            'changes' => $this->log->entries(['entity' => 'event', 'entity_id' => $p['id']]),
        ]));
        $router->post("~^/api/v1/events/$id/cancel$~", fn (array $p): Response => Response::json(
            $this->events->cancel($p['id'], $actor, $runId, self::note($input))));
        $router->post("~^/api/v1/events/$id/withdraw$~", fn (array $p): Response => Response::json(
            $this->events->withdraw($p['id'], $actor, $runId, self::note($input))));
        $router->post("~^/api/v1/events/$id/sources$~", function (array $p) use ($actor, $runId, $input): Response {
            $result = $this->events->addSource($p['id'], self::requiredString($input, 'url'),
                self::optionalString($input, 'source_id'), $actor, $runId, self::optionalNote($input));
            return Response::json($result, $result['added'] ? 201 : 200);
        });

        $router->get('~^/api/v1/candidates$~', function () use ($query): Response {
            [$limit, $offset] = self::page($query);
            $states = self::states($query['state'] ?? null);
            return Response::json($this->candidates->list(array_filter([
                'states' => $states,
                'week' => self::weekParameter($query),
                'source_id' => is_string($query['source_id'] ?? null) ? $query['source_id'] : null,
            ], static fn ($value): bool => $value !== null), $limit, $offset)
                + ['limit' => $limit, 'offset' => $offset]);
        });
        $router->post('~^/api/v1/candidates$~', function () use ($actor, $runId, $input): Response {
            $result = $this->candidates->submit(self::object($input, 'candidate'), $actor, $runId);
            return Response::json($result, $result['result'] === 'created' ? 201 : 200);
        });
        $router->get("~^/api/v1/candidates/$id$~", fn (array $p): Response =>
            Response::json($this->candidates->get($p['id'])
                ?? throw DomainError::notFound('Kandidát ' . $p['id'] . ' neexistuje.')));
        $router->post("~^/api/v1/candidates/$id/resolve$~", fn (array $p): Response => Response::json(
            $this->candidates->resolve($p['id'], self::requiredString($input, 'state'),
                self::optionalString($input, 'event_id'), self::note($input), $actor, $runId)));

        $router->get('~^/api/v1/match-reviews$~', fn (): Response => Response::json([
            'reviews' => $this->candidates->pendingReviews(),
        ]));
        $router->post('~^/api/v1/match-reviews/(?P<id>\d+)/decide$~', fn (array $p): Response =>
            Response::json($this->candidates->decideReview((int) $p['id'],
                self::requiredString($input, 'decision'), self::note($input), $actor, $runId)));

        $router->get('~^/api/v1/sources$~', fn (): Response => Response::json([
            'sources' => $this->sources->list(in_array($query['due'] ?? null, ['1', 'true'], true)),
        ]));
        $router->get("~^/api/v1/sources/$id$~", fn (array $p): Response =>
            Response::json($this->sources->get($p['id'])
                ?? throw DomainError::notFound('Zdroj ' . $p['id'] . ' neexistuje.')));
        $router->patch("~^/api/v1/sources/$id$~", fn (array $p): Response => Response::json(
            $this->sources->update($p['id'], self::object($input, 'changes'), $actor, $runId, self::note($input))));

        $router->post('~^/api/v1/runs$~', fn (): Response => Response::json(
            (new RunService($this->pdo, $this->clock))->report((string) $runId, self::object($input, 'run'), $actor),
            201));
        $router->get("~^/api/v1/runs/$id$~", fn (array $p): Response =>
            Response::json((new RunService($this->pdo, $this->clock))->get($p['id'])
                ?? throw DomainError::notFound('Běh ' . $p['id'] . ' neexistuje.')));

        $router->get('~^/api/v1/changes$~', function () use ($query): Response {
            $filter = array_filter([
                'run_id' => $query['run_id'] ?? null,
                'actor' => $query['actor'] ?? null,
                'entity' => $query['entity'] ?? null,
                'entity_id' => $query['entity_id'] ?? null,
            ], 'is_string');
            if (isset($query['since_id']) && ctype_digit((string) $query['since_id'])) {
                $filter['since_id'] = (int) $query['since_id'];
            }
            return Response::json(['changes' => $this->log->entries($filter, self::page($query)[0])]);
        });

        $router->post('~^/api/v1/inbox$~', function () use ($input): Response {
            try {
                $result = (new InboxRepository($this->pdo))->submit(
                    self::requiredString($input, 'url'), self::optionalString($input, 'note'));
            } catch (InvalidArgumentException $error) {
                throw DomainError::invalid(['url' => $error->getMessage()]);
            }
            return Response::json($result, $result['duplicate'] ? 200 : 202);
        });

        $response = $router->dispatch($method, $path);
        if ($response !== null && $response->status === 405) {
            return self::error('Metoda není pro tento endpoint povolená.', 405, 'method-not-allowed');
        }

        return $response;
    }

    private function index(): Response
    {
        return Response::json([
            'version' => 1,
            'auth' => 'Authorization: Bearer <token>; zápis navíc X-Run-Id: <běh>',
            'endpoints' => [
                'GET /api/v1/me' => 'token, denní limit a dnešní publikace',
                'GET /api/v1/taxonomy' => 'kategorie, obce a aliasy',
                'GET /api/v1/events?week=&from=&to=&municipality=&category=&q=&status=&changed_since=' => 'výpis akcí',
                'POST /api/v1/events {event, note?, candidate_id?, distinct_from?}' => 'publikace ověřené akce',
                'GET /api/v1/events/{id}' => 'detail akce',
                'PATCH /api/v1/events/{id} {changes, note}' => 'úprava akce',
                'GET /api/v1/events/{id}/history' => 'historie změn akce',
                'POST /api/v1/events/{id}/cancel {note}' => 'zrušení akce',
                'POST /api/v1/events/{id}/withdraw {note}' => 'stažení z webu',
                'POST /api/v1/events/{id}/sources {url, source_id?, note?}' => 'další zdroj akce',
                'GET /api/v1/candidates?state=&week=&source_id=' => 'fronta kandidátů',
                'POST /api/v1/candidates {candidate}' => 'nový kandidát s deduplikací',
                'GET /api/v1/candidates/{id}' => 'kandidát a jeho shody',
                'POST /api/v1/candidates/{id}/resolve {state, event_id?, note}' => 'rozhodnutí o kandidátovi',
                'GET /api/v1/match-reviews' => 'nejisté shody',
                'POST /api/v1/match-reviews/{id}/decide {decision, note}' => 'merged nebo separate',
                'GET /api/v1/sources?due=1' => 'registr zdrojů, splatné ke kontrole',
                'PATCH /api/v1/sources/{id} {changes, note}' => 'úprava zdroje',
                'POST /api/v1/runs {run: {started_at, finished_at, status, offline?, report?, sources}}'
                    => 'report běhu pipeline; run_id je X-Run-Id',
                'GET /api/v1/runs/{id}' => 'běh a výsledky zdrojů',
                'GET /api/v1/changes?run_id=&actor=&entity=&entity_id=&since_id=' => 'historie změn',
                'POST /api/v1/inbox {url, note?}' => 'ručně vložený odkaz',
            ],
        ]);
    }

    private function taxonomy(): Response
    {
        $categories = $this->pdo->query(
            'SELECT id, axis, label, description FROM category ORDER BY axis, sort_order, id')->fetchAll();
        $aliases = [];
        foreach ($this->pdo->query('SELECT alias, category_id FROM category_alias ORDER BY alias') as $row) {
            $aliases[(string) $row['category_id']][] = (string) $row['alias'];
        }
        foreach ($categories as $index => $category) {
            $categories[$index]['aliases'] = $aliases[$category['id']] ?? [];
        }

        return Response::json([
            'categories' => $categories,
            'municipalities' => array_map(static fn (array $row): array => $row + ['id' => (int) $row['id']],
                $this->pdo->query('SELECT id, name, district, region FROM municipality ORDER BY name')->fetchAll()),
            'municipality_aliases' => $this->pdo->query(
                'SELECT a.alias, a.municipality_id, m.name AS municipality FROM municipality_alias a
                 JOIN municipality m ON m.id = a.municipality_id ORDER BY a.alias')->fetchAll(),
        ]);
    }

    /** @return array<string, mixed> */
    private static function decode(string $body): array
    {
        if (trim($body) === '') {
            return [];
        }
        $data = json_decode($body, true, 64, JSON_THROW_ON_ERROR);
        if (!is_array($data) || (array_is_list($data) && $data !== [])) {
            throw new JsonException('Tělo není objekt.');
        }

        return $data;
    }

    /** @return array<string, mixed> */
    private static function object(array $input, string $field): array
    {
        $value = $input[$field] ?? null;
        if (!is_array($value) || (array_is_list($value) && $value !== [])) {
            throw DomainError::invalid([$field => 'Povinný objekt.']);
        }

        return $value;
    }

    private static function note(array $input): string
    {
        $note = self::optionalNote($input);
        if ($note === null) {
            throw DomainError::invalid(['note' => 'Povinný stručný důvod změny.']);
        }

        return $note;
    }

    private static function optionalNote(array $input): ?string
    {
        $note = $input['note'] ?? null;
        if ($note !== null && !is_string($note)) {
            throw DomainError::invalid(['note' => 'Text.']);
        }
        $note = $note === null ? '' : trim($note);

        return $note === '' ? null : mb_substr($note, 0, 2000, 'UTF-8');
    }

    private static function requiredString(array $input, string $field): string
    {
        return self::optionalString($input, $field)
            ?? throw DomainError::invalid([$field => 'Povinný text.']);
    }

    private static function optionalString(array $input, string $field): ?string
    {
        $value = $input[$field] ?? null;
        if ($value !== null && !is_string($value)) {
            throw DomainError::invalid([$field => 'Text.']);
        }

        return $value === null || trim($value) === '' ? null : trim($value);
    }

    /** @return list<string> */
    private static function stringList(array $input, string $field): array
    {
        $value = $input[$field] ?? [];
        if (!is_array($value) || !array_is_list($value) || array_filter($value, 'is_string') !== $value) {
            throw DomainError::invalid([$field => 'Seznam textů.']);
        }

        return $value;
    }

    /** @return list<string>|null */
    private static function states(mixed $value): ?array
    {
        if ($value === null || $value === '' || $value === 'open') {
            return null;
        }
        $states = is_array($value) ? $value : explode(',', (string) $value);
        $known = array_merge(CandidateService::OPEN_STATES, CandidateService::CLOSED_STATES);
        foreach ($states as $state) {
            if (!in_array($state, $known, true)) {
                throw DomainError::invalid(['state' => 'Známé stavy: open, ' . implode(', ', $known) . '.']);
            }
        }

        return array_values($states);
    }

    private static function weekParameter(array $query): ?string
    {
        $week = $query['week'] ?? null;
        if ($week === null || $week === '') {
            return null;
        }
        if (!is_string($week) || preg_match('/^\d{4}-W\d{2}$/', $week) !== 1) {
            throw DomainError::invalid(['week' => 'ISO týden, např. 2026-W33.']);
        }

        return $week;
    }

    /** @return array{0: int, 1: int} */
    private static function page(array $query): array
    {
        $limit = ctype_digit((string) ($query['limit'] ?? '')) ? (int) $query['limit'] : 50;
        $offset = ctype_digit((string) ($query['offset'] ?? '')) ? (int) $query['offset'] : 0;

        return [max(1, min(self::MAX_LIMIT, $limit)), $offset];
    }

    /** @param array<string, string> $headers */
    private static function bearer(array $headers): string
    {
        $authorization = self::header($headers, 'Authorization') ?? '';

        return preg_match('/^Bearer\s+(\S+)$/i', $authorization, $match) === 1 ? $match[1] : '';
    }

    /** @param array<string, string> $headers */
    private static function header(array $headers, string $name): ?string
    {
        foreach ($headers as $key => $value) {
            if (strcasecmp((string) $key, $name) === 0) {
                $value = trim((string) $value);
                return $value === '' ? null : $value;
            }
        }

        return null;
    }

    private static function error(string $message, int $status, string $code): Response
    {
        return Response::json(['error' => $message, 'code' => $code], $status);
    }
}
