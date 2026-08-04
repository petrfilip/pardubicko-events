<?php

declare(strict_types=1);

namespace Pardubicko;

use RuntimeException;

final class View
{
    public function __construct(
        private readonly string $directory = __DIR__ . '/../templates',
    ) {
    }

    /** @param array<string, mixed> $data */
    public function page(string $template, array $data): string
    {
        $content = $this->partial($template, $data);

        return $this->partial('layout', $data + ['content' => $content]);
    }

    /** @param array<string, mixed> $data */
    public function partial(string $template, array $data = []): string
    {
        $path = $this->directory . '/' . $template . '.php';
        if (!is_file($path)) {
            throw new RuntimeException('Šablona neexistuje: ' . $template);
        }

        extract($data, EXTR_SKIP);
        $view = $this;
        ob_start();
        require $path;

        return (string) ob_get_clean();
    }
}
