<?php

declare(strict_types=1);

namespace Pardubicko;

/**
 * Kdo zapisuje. Agent je vždy konkrétní token; jeho název se ukládá do
 * historie změn a počítá se k němu denní limit publikací.
 */
final class Actor
{
    public function __construct(
        public readonly string $name,
        public readonly string $kind,
        public readonly ?int $dailyPublishLimit = null,
    ) {
    }

    public static function agent(string $name, int $dailyPublishLimit): self
    {
        return new self($name, 'agent', $dailyPublishLimit);
    }

    public static function admin(): self
    {
        return new self('admin', 'admin');
    }

    public static function system(string $name): self
    {
        return new self($name, 'system');
    }

    public function isAgent(): bool
    {
        return $this->kind === 'agent';
    }
}
