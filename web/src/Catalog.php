<?php

declare(strict_types=1);

namespace Pardubicko;

use PDO;

/**
 * Číselníky, proti kterým se ověřují zapisovaná data: obce, aliasy obcí,
 * kategorie a registr zdrojů. Načítají se jednou za požadavek.
 */
final class Catalog
{
    /** @var array<string, list<array{id: int, name: string}>>|null */
    private ?array $municipalities = null;

    /** @var array<string, string>|null alias => category id */
    private ?array $categoryLookup = null;

    /** @var array<string, array{axis: string, label: string}>|null */
    private ?array $categories = null;

    public function __construct(private readonly PDO $pdo)
    {
    }

    /**
     * Obec podle názvu nebo aliasu. Shodné názvy obcí se v ČR opakují; bez
     * okresu se vazba neodhaduje, takže víceznačný název vrátí null.
     *
     * @return array{id: int, name: string}|null
     */
    public function municipality(string $name): ?array
    {
        if ($this->municipalities === null) {
            $this->municipalities = [];
            foreach ($this->pdo->query('SELECT id, name FROM municipality') as $row) {
                $this->municipalities[Text::fold((string) $row['name'])][] =
                    ['id' => (int) $row['id'], 'name' => (string) $row['name']];
            }
            $aliases = $this->pdo->query(
                'SELECT a.alias, m.id, m.name FROM municipality_alias a
                 JOIN municipality m ON m.id = a.municipality_id');
            foreach ($aliases as $row) {
                // Doložený alias má přednost před víceznačným názvem.
                $this->municipalities[Text::fold((string) $row['alias'])] =
                    [['id' => (int) $row['id'], 'name' => (string) $row['name']]];
            }
        }
        $matches = $this->municipalities[Text::fold($name)] ?? [];

        return count($matches) === 1 ? $matches[0] : null;
    }

    /** Kanonické ID kategorie podle ID, názvu nebo aliasu. */
    public function categoryId(string $value): ?string
    {
        $this->loadCategories();

        return $this->categoryLookup[Text::fold($value)] ?? null;
    }

    public function categoryAxis(string $id): ?string
    {
        $this->loadCategories();

        return $this->categories[$id]['axis'] ?? null;
    }

    /** Text pro fulltext: ID, popisek a aliasy kategorií. */
    public function categorySearchTerms(array $ids): string
    {
        $this->loadCategories();
        $terms = [];
        foreach ($ids as $id) {
            $terms[] = $id;
            $terms[] = $this->categories[$id]['label'] ?? '';
            foreach ($this->categoryLookup as $alias => $target) {
                if ($target === $id) {
                    $terms[] = $alias;
                }
            }
        }

        return implode(' ', array_unique(array_filter($terms, 'strlen')));
    }

    /** Normalizované URL všech zdrojů v registru; výpis zdroje není detail akce. */
    public function isRegistryUrl(string $url): bool
    {
        $normalized = UrlNormalizer::normalize($url);
        foreach ($this->pdo->query('SELECT url FROM source') as $row) {
            try {
                if (UrlNormalizer::normalize((string) $row['url']) === $normalized) {
                    return true;
                }
            } catch (\InvalidArgumentException) {
                continue;
            }
        }

        return false;
    }

    public function sourceExists(string $id): bool
    {
        $statement = $this->pdo->prepare('SELECT 1 FROM source WHERE id = ?');
        $statement->execute([$id]);

        return $statement->fetchColumn() !== false;
    }

    private function loadCategories(): void
    {
        if ($this->categories !== null) {
            return;
        }
        $this->categories = [];
        $this->categoryLookup = [];
        foreach ($this->pdo->query('SELECT id, axis, label FROM category') as $row) {
            $id = (string) $row['id'];
            $this->categories[$id] = [
                'axis' => (string) ($row['axis'] ?? 'kind'),
                'label' => (string) $row['label'],
            ];
            $this->categoryLookup[Text::fold($id)] = $id;
        }
        foreach ($this->pdo->query('SELECT alias, category_id FROM category_alias') as $row) {
            $this->categoryLookup[Text::fold((string) $row['alias'])] = (string) $row['category_id'];
        }
    }
}
