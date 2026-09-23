<?php

declare(strict_types=1);

namespace Pardubicko;

use DateInterval;
use DateTimeImmutable;
use Exception;
use InvalidArgumentException;

/**
 * Kontrola a normalizace akce před zápisem.
 *
 * Vstupní tvar je týž jako v někdejších týdenních JSON (ADR 0001) bez pole
 * `week`, které se odvozuje. Pravidla vycházejí z vize, kapitoly 8: nic se
 * nedomýšlí, neznámý konec je `null`, zdroj musí být konkrétní stránka.
 * Neznámé pole je chyba, aby se překlep agenta neztratil potichu.
 *
 * Výsledkem je kanonická akce: obec a kategorie přeložené přes číselníky,
 * výchozí hodnoty doplněné.
 */
final class EventInput
{
    private const FIELDS = [
        'id', 'title', 'description', 'start_at', 'end_at', 'all_day', 'venue',
        'municipality', 'categories', 'price', 'source', 'cancelled', 'last_verified_at',
    ];

    private const TIMESTAMP = '/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}([+-]\d{2}:\d{2}|Z)$/';

    /** Nejvzdálenější přípustný začátek; za ním je skoro jistě špatný rok. */
    private const MAX_FUTURE = 'P2Y';
    private const MAX_PAST = 'P1Y';

    /** @var array<string, string> */
    private array $errors = [];

    private function __construct(
        private readonly Catalog $catalog,
        private readonly Clock $clock,
    ) {
    }

    /**
     * @param array<string, mixed> $data
     * @return array<string, mixed> kanonická akce
     */
    public static function normalize(array $data, Catalog $catalog, Clock $clock): array
    {
        return (new self($catalog, $clock))->run($data);
    }

    /**
     * @param array<string, mixed> $data
     * @return array<string, mixed>
     */
    private function run(array $data): array
    {
        foreach (array_keys($data) as $key) {
            if (!in_array($key, self::FIELDS, true)) {
                $this->errors[(string) $key] = $key === 'week'
                    ? 'Pole week se neposílá; týdny se odvozují z termínu.'
                    : 'Neznámé pole.';
            }
        }

        $event = [
            'id' => $this->id($data['id'] ?? null),
            'title' => $this->requiredText($data, 'title', 300),
            'description' => $this->optionalText($data, 'description', 5000),
            'start_at' => $this->timestamp($data, 'start_at', true),
            'end_at' => $this->timestamp($data, 'end_at', false),
            'all_day' => $this->boolean($data, 'all_day'),
            'venue' => $this->optionalText($data, 'venue', 300),
            'municipality' => null,
            'municipality_id' => null,
            'categories' => $this->categories($data['categories'] ?? null),
            'price' => $this->price($data['price'] ?? null),
            'source' => $this->source($data['source'] ?? null),
            'cancelled' => $this->boolean($data, 'cancelled'),
            'last_verified_at' => $this->timestamp($data, 'last_verified_at', false)
                ?? $this->clock->timestamp(),
        ];

        $municipality = $this->requiredText($data, 'municipality', 120);
        if ($municipality !== null) {
            $resolved = $this->catalog->municipality($municipality);
            if ($resolved === null) {
                $this->errors['municipality'] = sprintf(
                    'Obec „%s“ není jednoznačně v číselníku ani mezi aliasy. '
                    . 'U místní části použij obec, pod kterou patří.', $municipality);
            } else {
                $event['municipality'] = $resolved['name'];
                $event['municipality_id'] = $resolved['id'];
            }
        }

        $this->checkRange($event);

        if ($this->errors !== []) {
            throw DomainError::invalid($this->errors);
        }

        return $event;
    }

    private function id(mixed $value): ?string
    {
        if ($value === null) {
            return null;
        }
        if (!is_string($value) || preg_match('/^[a-z0-9]+(-[a-z0-9]+)*$/', $value) !== 1
            || strlen($value) > 120) {
            $this->errors['id'] = 'ID je slug z malých písmen, číslic a pomlček, nejvýš 120 znaků.';
            return null;
        }

        return $value;
    }

    /** @param array<string, mixed> $data */
    private function requiredText(array $data, string $field, int $max): ?string
    {
        $value = $data[$field] ?? null;
        if (!is_string($value) || trim($value) === '') {
            $this->errors[$field] = 'Povinný neprázdný text.';
            return null;
        }

        return $this->limit($field, trim($value), $max);
    }

    /** @param array<string, mixed> $data */
    private function optionalText(array $data, string $field, int $max): ?string
    {
        $value = $data[$field] ?? null;
        if ($value === null || (is_string($value) && trim($value) === '')) {
            return null;
        }
        if (!is_string($value)) {
            $this->errors[$field] = 'Očekává se text nebo null.';
            return null;
        }

        return $this->limit($field, trim($value), $max);
    }

    private function limit(string $field, string $value, int $max): string
    {
        if (mb_strlen($value, 'UTF-8') > $max) {
            $this->errors[$field] = sprintf('Nejvýš %d znaků.', $max);
        }

        return $value;
    }

    /** @param array<string, mixed> $data */
    private function boolean(array $data, string $field): bool
    {
        $value = $data[$field] ?? false;
        if (!is_bool($value)) {
            $this->errors[$field] = 'Očekává se true nebo false.';
            return false;
        }

        return $value;
    }

    /** @param array<string, mixed> $data */
    private function timestamp(array $data, string $field, bool $required): ?string
    {
        $value = $data[$field] ?? null;
        if ($value === null) {
            if ($required) {
                $this->errors[$field] = 'Povinný čas ve tvaru 2026-08-15T18:00:00+02:00.';
            }
            return null;
        }
        if (!is_string($value) || preg_match(self::TIMESTAMP, $value) !== 1) {
            $this->errors[$field] = 'Čas ve tvaru 2026-08-15T18:00:00+02:00.';
            return null;
        }
        try {
            $parsed = new DateTimeImmutable($value);
        } catch (Exception) {
            $this->errors[$field] = 'Neplatné datum nebo čas.';
            return null;
        }
        if ($parsed->format('Y-m-d\TH:i:s') !== substr($value, 0, 19)) {
            $this->errors[$field] = 'Neplatné datum nebo čas.';
            return null;
        }
        $local = $parsed->setTimezone(Format::zone());
        if ($parsed->getOffset() !== $local->getOffset()) {
            $this->errors[$field] = sprintf(
                'Čas musí být v místním čase Europe/Prague; k tomuto datu platí posun %s.',
                $local->format('P'));
            return null;
        }

        return $value;
    }

    /** @param array<string, mixed> $event */
    private function checkRange(array $event): void
    {
        if ($event['start_at'] === null) {
            return;
        }
        $start = new DateTimeImmutable($event['start_at']);
        if ($event['end_at'] !== null && new DateTimeImmutable($event['end_at']) < $start) {
            $this->errors['end_at'] = 'Konec je před začátkem.';
        }
        $now = $this->clock->now();
        if ($start > $now->add(new DateInterval(self::MAX_FUTURE))
            || $start < $now->sub(new DateInterval(self::MAX_PAST))) {
            $this->errors['start_at'] = 'Začátek je víc než rok v minulosti nebo dva roky '
                . 'v budoucnosti; zkontroluj rok.';
        }
    }

    /** @return list<string> */
    private function categories(mixed $value): array
    {
        if (!is_array($value) || $value === [] || !array_is_list($value)) {
            $this->errors['categories'] = 'Neprázdný seznam kategorií.';
            return [];
        }
        $ids = [];
        foreach ($value as $index => $name) {
            $id = is_string($name) ? $this->catalog->categoryId($name) : null;
            if ($id === null) {
                $this->errors['categories[' . $index . ']'] = sprintf(
                    'Neznámá kategorie %s; seznam je v GET /api/v1/taxonomy.',
                    json_encode($name, JSON_UNESCAPED_UNICODE));
                continue;
            }
            if (!in_array($id, $ids, true)) {
                $ids[] = $id;
            }
        }
        if ($ids !== [] && !array_filter($ids, fn (string $id): bool =>
                $this->catalog->categoryAxis($id) === 'kind')) {
            $this->errors['categories'] = 'Aspoň jedna kategorie musí být z osy kind '
                . '(druh akce), ne jen cílová skupina.';
        }

        return $ids;
    }

    /** @return array{type: string, text: ?string, amount: int|float|null, currency: ?string} */
    private function price(mixed $value): array
    {
        $price = ['type' => 'unknown', 'text' => null, 'amount' => null, 'currency' => null];
        if ($value === null) {
            return $price;
        }
        if (!is_array($value)) {
            $this->errors['price'] = 'Očekává se objekt {type, text, amount, currency}.';
            return $price;
        }
        foreach (array_keys($value) as $key) {
            if (!array_key_exists((string) $key, $price)) {
                $this->errors['price.' . $key] = 'Neznámé pole.';
            }
        }
        $type = $value['type'] ?? 'unknown';
        if (!in_array($type, ['free', 'paid', 'unknown'], true)) {
            $this->errors['price.type'] = 'Jedna z hodnot free, paid, unknown.';
        } else {
            $price['type'] = $type;
        }
        $text = $value['text'] ?? null;
        if ($text !== null && !is_string($text)) {
            $this->errors['price.text'] = 'Text nebo null.';
        } else {
            $price['text'] = $text === null || trim($text) === '' ? null : trim($text);
        }
        $amount = $value['amount'] ?? null;
        if ($amount !== null && (!is_int($amount) && !is_float($amount) || $amount < 0)) {
            $this->errors['price.amount'] = 'Nezáporné číslo nebo null.';
        } else {
            $price['amount'] = $amount;
        }
        $currency = $value['currency'] ?? null;
        if ($currency !== null && (!is_string($currency) || preg_match('/^[A-Z]{3}$/', $currency) !== 1)) {
            $this->errors['price.currency'] = 'Kód měny, např. CZK, nebo null.';
        } else {
            $price['currency'] = $currency;
        }

        return $price;
    }

    /** @return array{type: ?string, url: ?string} */
    private function source(mixed $value): array
    {
        $source = ['type' => null, 'url' => null];
        if (!is_array($value)) {
            $this->errors['source'] = 'Povinný objekt {type, url} s konkrétní stránkou akce.';
            return $source;
        }
        foreach (array_keys($value) as $key) {
            if (!array_key_exists((string) $key, $source)) {
                $this->errors['source.' . $key] = 'Neznámé pole.';
            }
        }
        $type = $value['type'] ?? null;
        if (!is_string($type) || preg_match('/^[a-z]+(-[a-z]+)*$/', $type) !== 1) {
            $this->errors['source.type'] = 'Typ zdroje jako slug, např. official, facebook, ticketing.';
        } else {
            $source['type'] = $type;
        }
        $url = $value['url'] ?? null;
        $source['url'] = is_string($url) ? trim($url) : null;
        $problem = is_string($url) ? $this->sourceUrlProblem(trim($url)) : 'Povinný odkaz.';
        if ($problem !== null) {
            $this->errors['source.url'] = $problem;
        }

        return $source;
    }

    private function sourceUrlProblem(string $url): ?string
    {
        if (preg_match('~^https?://~i', $url) !== 1) {
            return 'Odkaz musí začínat http:// nebo https://.';
        }
        try {
            UrlNormalizer::normalize($url);
        } catch (InvalidArgumentException $error) {
            return $error->getMessage();
        }
        $parts = parse_url($url);
        $path = (string) ($parts['path'] ?? '');
        if (($path === '' || $path === '/') && !isset($parts['query'])) {
            return 'Odkaz vede na homepage; zdrojem musí být konkrétní stránka akce.';
        }
        if ($this->catalog->isRegistryUrl($url)) {
            return 'Odkaz vede na výpis zdroje z registru, ne na detail akce.';
        }

        return null;
    }
}
