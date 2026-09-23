<?php

declare(strict_types=1);

namespace Pardubicko;

use PDO;
use RuntimeException;
use Throwable;

/**
 * Migrace schématu podle `PRAGMA user_version`.
 *
 * Migrace jsou soubory `web/migrations/NNNN_popis.sql` a aplikují se
 * vzestupně, každá ve vlastní transakci společně s posunem verze. Když
 * migrace selže, databáze zůstane na poslední úspěšné verzi.
 */
final class Migrator
{
    public function __construct(
        private readonly PDO $pdo,
        private readonly string $directory = __DIR__ . '/../migrations',
    ) {
    }

    public function currentVersion(): int
    {
        return (int) $this->pdo->query('PRAGMA user_version')->fetchColumn();
    }

    public function latestVersion(): int
    {
        $versions = array_keys($this->migrations());

        return $versions === [] ? 0 : max($versions);
    }

    /** @return list<int> aplikované verze */
    public function migrate(): array
    {
        $applied = [];
        foreach ($this->migrations() as $version => $path) {
            if ($version <= $this->currentVersion()) {
                continue;
            }
            $sql = (string) file_get_contents($path);
            $this->pdo->beginTransaction();
            try {
                $this->pdo->exec($sql);
                $this->pdo->exec('PRAGMA user_version = ' . $version);
                $this->pdo->commit();
            } catch (Throwable $error) {
                $this->pdo->rollBack();
                throw new RuntimeException(sprintf(
                    'Migrace %s selhala: %s', basename($path), $error->getMessage(),
                ), 0, $error);
            }
            $applied[] = $version;
        }

        return $applied;
    }

    /** @return array<int, string> verze => cesta, vzestupně */
    private function migrations(): array
    {
        $migrations = [];
        foreach (glob($this->directory . '/[0-9][0-9][0-9][0-9]_*.sql') ?: [] as $path) {
            $version = (int) substr(basename($path), 0, 4);
            if (isset($migrations[$version])) {
                throw new RuntimeException('Duplicitní číslo migrace ' . $version . '.');
            }
            $migrations[$version] = $path;
        }
        ksort($migrations);

        return $migrations;
    }
}
