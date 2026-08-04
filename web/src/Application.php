<?php

declare(strict_types=1);

namespace Pardubicko;

use DateInterval;
use DatePeriod;
use DateTimeImmutable;
use InvalidArgumentException;
use JsonException;
use PDO;

final class Application
{
    private readonly EventRepository $events;
    private readonly InboxRepository $inbox;
    private readonly View $view;

    public function __construct(
        PDO $reader,
        ?PDO $writer = null,
        ?DateTimeImmutable $now = null,
        ?View $view = null,
    ) {
        $this->events = new EventRepository($reader, $now);
        $this->inbox = new InboxRepository($writer ?? $reader);
        $this->view = $view ?? new View();
    }

    /**
     * @param array<string, mixed> $query
     * @param array<string, string> $headers
     */
    public function handle(string $method, string $path, array $query = [],
                           array $headers = [], string $body = ''): Response
    {
        $router = new Router();
        $router->get('~^/$~', fn (): Response => $this->home($query));
        $router->get('~^/akce/(?P<id>[a-z0-9][a-z0-9-]*)$~',
            fn (array $params): Response => $this->detail($params['id']));
        $router->get('~^/obec/(?P<slug>[a-z0-9][a-z0-9-]*)$~',
            fn (array $params): Response => $this->municipality($params['slug'], $query));
        $router->get('~^/kalendar/(?P<week>\d{4}-W\d{2})$~',
            fn (array $params): Response => $this->calendar($params['week'], $query));
        $router->get('~^/hledat$~', fn (): Response => $this->search($query));
        $router->get('~^/sitemap\.xml$~', fn (): Response => $this->sitemap());
        $router->get('~^/robots\.txt$~', fn (): Response => $this->robots());
        $router->get('~^/api/health$~', fn (): Response => $this->health());
        $router->post('~^/api/inbox$~',
            fn (): Response => $this->submitInbox($headers, $body));

        $response = $router->dispatch(strtoupper($method), $path)
            ?? $this->notFound();

        return new Response($response->body, $response->status, array_merge([
            'X-Content-Type-Options' => 'nosniff',
            'Referrer-Policy' => 'strict-origin-when-cross-origin',
            'Content-Security-Policy' => "default-src 'self'; style-src 'self'; "
                . "img-src 'self' data:; script-src 'self' 'unsafe-inline'; "
                . "base-uri 'self'; frame-ancestors 'none'",
        ], $response->headers));
    }

    /** @param array<string, mixed> $query */
    private function home(array $query): Response
    {
        $legacyId = is_string($query['event'] ?? null) ? (string) $query['event'] : '';
        if (preg_match('/^[a-z0-9][a-z0-9-]*$/', $legacyId) === 1) {
            return Response::redirect('/akce/' . rawurlencode($legacyId), 301);
        }

        $filter = EventFilter::fromQuery($query);
        $week = $this->events->defaultWeek();
        if ($week !== null) {
            $filter = $filter->with(['week' => $week['id'], 'page' => $filter->page]);
        }
        $title = $week === null ? 'Akce na Pardubicku a v okolí'
            : 'Akce v týdnu ' . Format::weekLabel($week);

        return $this->listing($filter, '/', $title,
            'Ověřené kulturní, sportovní a komunitní události z obcí obou krajů.',
            ['tyden']);
    }

    /** @param array<string, mixed> $query */
    private function search(array $query): Response
    {
        $filter = EventFilter::fromQuery($query);
        $title = $filter->query === '' ? 'Hledání akcí'
            : 'Výsledky pro „' . $filter->query . '“';

        return $this->listing($filter, '/hledat', $title,
            'Filtry se vyhodnocují na serveru a celý stav zůstává v adrese stránky.');
    }

    /** @param array<string, mixed> $query */
    private function municipality(string $slug, array $query): Response
    {
        $municipality = $this->events->municipalityBySlug($slug);
        if ($municipality === null) {
            return $this->notFound('Obec nebyla nalezena.');
        }
        $filter = EventFilter::fromQuery($query)->with([
            'municipality' => $slug,
            'page' => EventFilter::fromQuery($query)->page,
        ]);

        return $this->listing($filter, '/obec/' . rawurlencode($slug),
            'Akce v obci ' . $municipality['name'],
            Format::eventCountLabel((int) $municipality['event_count']) . ' v katalogu.',
            ['obec']);
    }

    /** @param array<string, mixed> $query */
    private function calendar(string $weekId, array $query): Response
    {
        $week = $this->events->weekById($weekId);
        if ($week === null) {
            return $this->notFound('Týden nebyl nalezen.');
        }
        $base = EventFilter::fromQuery($query);
        $filter = $base->with(['week' => $weekId, 'page' => 1]);
        $events = $this->events->search($filter);
        $days = [];
        $from = new DateTimeImmutable($week['date_from'] . 'T00:00:00', Format::zone());
        $to = new DateTimeImmutable($week['date_to'] . 'T00:00:00', Format::zone());
        foreach (new DatePeriod($from, new DateInterval('P1D'), $to->modify('+1 day')) as $day) {
            $dayEvents = [];
            foreach ($events as $event) {
                if (Format::dateKey(Format::start($event)) <= Format::dateKey($day)
                    && Format::dateKey(Format::end($event)) >= Format::dateKey($day)) {
                    $dayEvents[] = $event;
                }
            }
            $days[] = ['date' => $day, 'events' => $dayEvents];
        }

        return Response::html($this->view->page('calendar', $this->common([
            'title' => 'Kalendář ' . Format::weekLabel($week),
            'description' => 'Týdenní kalendář akcí bez potřeby JavaScriptu.',
            'canonical' => Config::baseUrl() . '/kalendar/' . rawurlencode($weekId),
            'week' => $week,
            'weeks' => $this->events->weeks(),
            'days' => $days,
            'filter' => $filter,
        ])));
    }

    private function detail(string $id): Response
    {
        $event = $this->events->byId($id);
        if ($event === null) {
            return $this->notFound('Akce nebyla nalezena.');
        }
        $canonical = Config::baseUrl() . '/akce/' . rawurlencode($id);
        $jsonLd = [
            '@context' => 'https://schema.org',
            '@type' => 'Event',
            'name' => $event['title'],
            'description' => $event['description'] ?: $event['title'],
            'startDate' => $event['start_at'],
            'url' => $canonical,
            'eventStatus' => !empty($event['cancelled'])
                ? 'https://schema.org/EventCancelled'
                : 'https://schema.org/EventScheduled',
            'location' => [
                '@type' => 'Place',
                'name' => $event['venue'] ?: $event['municipality_name'],
                'address' => [
                    '@type' => 'PostalAddress',
                    'addressLocality' => $event['municipality_name'],
                ],
            ],
        ];
        if (is_string($event['end_at']) && $event['end_at'] !== '') {
            $jsonLd['endDate'] = $event['end_at'];
        }
        if ($event['price_type'] === 'free') {
            $jsonLd['isAccessibleForFree'] = true;
        }

        return Response::html($this->view->page('detail', $this->common([
            'title' => $event['title'],
            'description' => $event['description'] ?: Format::when($event),
            'canonical' => $canonical,
            'event' => $event,
            'eventWeeks' => $this->events->weeksForEvent($id),
            'eventSources' => $this->events->sourcesForEvent($id),
            'jsonLd' => $jsonLd,
        ])));
    }

    private function listing(EventFilter $filter, string $path, string $title,
                             string $description, array $drop = []): Response
    {
        $count = $this->events->count($filter);
        $pages = max(1, (int) ceil($count / Config::PER_PAGE));
        $page = min($filter->page, $pages);
        if ($page !== $filter->page) {
            $filter = $filter->with(['page' => $page]);
        }
        $items = $this->events->search(
            $filter, Config::PER_PAGE, ($page - 1) * Config::PER_PAGE);
        $canonicalPath = $filter->url($path, [], $drop);

        return Response::html($this->view->page('listing', $this->common([
            'title' => $title,
            'description' => $description,
            'canonical' => Config::baseUrl() . $canonicalPath,
            'events' => $items,
            'count' => $count,
            'pages' => $pages,
            'page' => $page,
            'path' => $path,
            'paginationDrop' => $drop,
            'filter' => $filter,
        ])));
    }

    private function sitemap(): Response
    {
        $base = Config::baseUrl();
        $urls = [["loc" => $base . '/', 'lastmod' => $this->events->generatedAt()]];
        foreach ($this->events->publishedForSitemap() as $event) {
            $urls[] = [
                'loc' => $base . '/akce/' . rawurlencode((string) $event['id']),
                'lastmod' => $event['lastmod'],
            ];
        }
        $xml = "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
            . "<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">\n";
        foreach ($urls as $url) {
            $xml .= '  <url><loc>' . self::xml((string) $url['loc']) . '</loc>';
            if (is_string($url['lastmod']) && $url['lastmod'] !== '') {
                $xml .= '<lastmod>' . self::xml(substr($url['lastmod'], 0, 10)) . '</lastmod>';
            }
            $xml .= "</url>\n";
        }

        return Response::xml($xml . "</urlset>\n");
    }

    private function robots(): Response
    {
        return Response::text("User-agent: *\nAllow: /\n\nSitemap: "
            . Config::baseUrl() . "/sitemap.xml\n");
    }

    private function health(): Response
    {
        $sources = $this->events->sourceHealth();
        $states = [];
        foreach ($sources as $source) {
            $states[$source['state']] = ($states[$source['state']] ?? 0) + 1;
        }

        return Response::json([
            'status' => 'ok',
            'generated_at' => $this->events->generatedAt(),
            'stats' => $this->events->stats(),
            'source_states' => $states,
            'sources' => $sources,
        ]);
    }

    /** @param array<string, string> $headers */
    private function submitInbox(array $headers, string $body): Response
    {
        $token = Config::inboxToken();
        $authorization = $this->header($headers, 'Authorization') ?? '';
        $provided = preg_match('/^Bearer\s+(.+)$/i', $authorization, $match) === 1
            ? trim($match[1]) : '';
        if ($token === null || $provided === '' || !hash_equals($token, $provided)) {
            return new Response(
                Response::json(['error' => 'Neplatný nebo chybějící token.'], 401)->body,
                401,
                ['Content-Type' => 'application/json; charset=utf-8',
                 'Cache-Control' => 'no-store',
                 'WWW-Authenticate' => 'Bearer'],
            );
        }

        try {
            $payload = json_decode($body, true, 32, JSON_THROW_ON_ERROR);
        } catch (JsonException) {
            return Response::json(['error' => 'Tělo musí být validní JSON.'], 400);
        }
        if (!is_array($payload) || !is_string($payload['url'] ?? null)) {
            return Response::json(['error' => 'Pole url je povinné.'], 422);
        }
        $note = is_string($payload['note'] ?? null) ? $payload['note'] : null;
        try {
            $result = $this->inbox->submit($payload['url'], $note);
        } catch (InvalidArgumentException $error) {
            return Response::json(['error' => $error->getMessage()], 422);
        }

        return Response::json($result, $result['duplicate'] ? 200 : 202);
    }

    private function notFound(string $message = 'Stránka nebyla nalezena.'): Response
    {
        return Response::html($this->view->page('error', $this->common([
            'title' => 'Nenalezeno',
            'description' => $message,
            'canonical' => Config::baseUrl() . '/404',
            'status' => 404,
            'message' => $message,
        ])), 404);
    }

    /** @param array<string, mixed> $data
     * @return array<string, mixed>
     */
    private function common(array $data): array
    {
        return $data + [
            'municipalities' => $this->events->municipalities(),
            'categories' => $this->events->categories(),
            'weeks' => $this->events->weeks(),
            'generatedAt' => $this->events->generatedAt(),
        ];
    }

    /** @param array<string, string> $headers */
    private function header(array $headers, string $name): ?string
    {
        foreach ($headers as $key => $value) {
            if (strcasecmp($key, $name) === 0) {
                return $value;
            }
        }

        return null;
    }

    private static function xml(string $value): string
    {
        return htmlspecialchars($value, ENT_XML1 | ENT_QUOTES, 'UTF-8');
    }
}
