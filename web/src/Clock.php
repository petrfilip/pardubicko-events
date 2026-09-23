<?php

declare(strict_types=1);

namespace Pardubicko;

use DateTimeImmutable;

/** Aktuální čas v místní zóně; v testech se podstrčí pevný okamžik. */
final class Clock
{
    public const FORMAT = 'Y-m-d\TH:i:sP';

    public function __construct(private readonly ?DateTimeImmutable $fixed = null)
    {
    }

    public function now(): DateTimeImmutable
    {
        return ($this->fixed ?? new DateTimeImmutable('now'))->setTimezone(Format::zone());
    }

    public function timestamp(): string
    {
        return $this->now()->format(self::FORMAT);
    }
}
