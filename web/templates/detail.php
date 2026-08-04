<?php use Pardubicko\Format; use Pardubicko\Slug; ?>
<nav class="breadcrumbs" aria-label="Drobečková navigace"><a href="/">Přehled</a><span>›</span><span>Detail akce</span></nav>
<article class="event-detail">
  <header>
    <div class="badges">
      <?php foreach ($event['categories'] as $category): ?>
        <a class="badge" href="/hledat?kategorie=<?= e(rawurlencode($category['id'])) ?>"><?= e($category['label']) ?></a>
      <?php endforeach; ?>
      <?php if (!empty($event['cancelled'])): ?><span class="badge badge-danger">Zrušeno</span><?php endif; ?>
    </div>
    <h1><?= e($event['title']) ?></h1>
    <p class="detail-lead"><?= e(Format::when($event)) ?></p>
  </header>
  <dl class="facts">
    <div><dt>Kdy</dt><dd><?= e(Format::when($event)) ?></dd></div>
    <div><dt>Kde</dt><dd><?= e($event['venue'] ?: $event['municipality_name']) ?><br><a href="/obec/<?= e(Slug::make($event['municipality_name'])) ?>"><?= e($event['municipality_name']) ?></a></dd></div>
    <div><dt>Vstupné</dt><dd><?= e(Format::price($event)) ?></dd></div>
  </dl>
  <?php if (($event['description'] ?? '') !== ''): ?>
    <section class="prose"><h2>O akci</h2><p><?= nl2br(e($event['description'])) ?></p></section>
  <?php endif; ?>
  <section class="source-box">
    <h2>Ověření a zdroje</h2>
    <p><a class="button button-secondary" href="<?= e($event['source_url']) ?>" target="_blank" rel="noopener noreferrer">Otevřít zdroj akce</a></p>
    <?php if (($event['last_verified_at'] ?? '') !== ''): ?><p>Ověřeno <?= e(Format::updated($event['last_verified_at'])) ?></p><?php endif; ?>
    <?php if ($eventSources !== []): ?><ul><?php foreach ($eventSources as $source): ?><li><a href="<?= e($source['url']) ?>" target="_blank" rel="noopener noreferrer"><?= e($source['source_name'] ?: $source['url']) ?></a></li><?php endforeach; ?></ul><?php endif; ?>
  </section>
</article>
<script type="application/ld+json"><?= json_encode($jsonLd, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_HEX_TAG | JSON_HEX_AMP) ?></script>
