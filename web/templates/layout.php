<?php use Pardubicko\Format; ?>
<!doctype html>
<html lang="cs">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title><?= e($title) ?> · Pardubicko events</title>
  <meta name="description" content="<?= e($description) ?>">
  <link rel="canonical" href="<?= e($canonical) ?>">
  <link rel="stylesheet" href="/assets/styles.css">
</head>
<body>
  <a class="skip-link" href="#obsah">Přejít na obsah</a>
  <header class="site-header">
    <div class="shell header-row">
      <a class="brand" href="/" aria-label="Pardubicko events – úvod">
        <span class="brand-mark" aria-hidden="true">P</span>
        <span><strong>Pardubicko</strong><small>události zblízka</small></span>
      </a>
      <nav aria-label="Hlavní navigace">
        <a href="/">Přehled</a>
        <?php if (($weeks ?? []) !== []): ?>
          <a href="/kalendar/<?= e((string) $weeks[0]['id']) ?>">Kalendář</a>
        <?php endif; ?>
        <a href="/hledat">Hledat</a>
      </nav>
    </div>
  </header>
  <main id="obsah" class="shell main-content">
    <?= $content ?>
  </main>
  <footer class="site-footer">
    <div class="shell footer-row">
      <p>Ověřitelné tipy na akce z Pardubického a Královéhradeckého kraje.</p>
      <?php if (($generatedAt ?? '') !== ''): ?>
        <p>Data aktualizována <?= e(Format::updated((string) $generatedAt)) ?></p>
      <?php endif; ?>
    </div>
  </footer>
</body>
</html>
