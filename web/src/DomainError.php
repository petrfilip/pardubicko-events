<?php

declare(strict_types=1);

namespace Pardubicko;

use RuntimeException;

/**
 * Odmítnutý zápis. Nese HTTP status a strojově čitelné podrobnosti, aby
 * agent z odpovědi poznal, co má opravit, a nemusel hádat z textu.
 */
final class DomainError extends RuntimeException
{
    /** @param array<string, mixed> $details */
    public function __construct(
        string $message,
        public readonly int $status = 422,
        public readonly string $errorCode = 'invalid',
        public readonly array $details = [],
    ) {
        parent::__construct($message);
    }

    /** @param array<string, string> $fields pole => důvod */
    public static function invalid(array $fields): self
    {
        return new self('Data neprošla kontrolou.', 422, 'invalid', ['fields' => $fields]);
    }

    public static function notFound(string $message): self
    {
        return new self($message, 404, 'not-found');
    }

    /** @param array<string, mixed> $details */
    public static function conflict(string $message, string $code, array $details = []): self
    {
        return new self($message, 409, $code, $details);
    }

    /** @return array<string, mixed> */
    public function toArray(): array
    {
        return ['error' => $this->getMessage(), 'code' => $this->errorCode] + $this->details;
    }
}
