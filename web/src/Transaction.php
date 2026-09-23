<?php

declare(strict_types=1);

namespace Pardubicko;

use PDO;
use Throwable;

/** Spustí práci v transakci; při výjimce ji vrátí a výjimku pošle dál. */
final class Transaction
{
    /**
     * @template T
     * @param callable(): T $work
     * @return T
     */
    public static function run(PDO $pdo, callable $work): mixed
    {
        $pdo->beginTransaction();
        try {
            $result = $work();
            $pdo->commit();

            return $result;
        } catch (Throwable $error) {
            $pdo->rollBack();
            throw $error;
        }
    }
}
