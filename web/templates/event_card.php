<?php use Pardubicko\Format; ?>
<article class="event-card">
  <div class="event-date" aria-label="<?= e(Format::when($event)) ?>">
    <?php $start = Format::start($event); ?>
    <strong><?= e($start->format('j.')) ?></strong>
    <span><?= e($start->format('n.')) ?></span>
    <small><?= !empty($event['all_day']) ? 'celý den' : e($start->format('H:i')) ?></small>
  </div>
  <div class="event-body">
    <div class="badges">
      <?php if (($event['state'] ?? '') !== 'future' && Format::stateLabel((string) $event['state']) !== ''): ?>
        <span class="badge badge-state"><?= e(Format::stateLabel((string) $event['state'])) ?></span>
      <?php endif; ?>
      <?php foreach ($event['categories'] as $category): ?>
        <a class="badge" href="/hledat?kategorie=<?= e(rawurlencode($category['id'])) ?>"><?= e($category['label']) ?></a>
      <?php endforeach; ?>
    </div>
    <h2><a href="/akce/<?= e(rawurlencode($event['id'])) ?>"><?= e($event['title']) ?></a></h2>
    <p class="event-meta"><?= e(Format::when($event)) ?> · <?= e($event['municipality_name']) ?></p>
    <?php if (($event['venue'] ?? '') !== ''): ?><p><?= e($event['venue']) ?></p><?php endif; ?>
    <p class="price"><?= e(Format::price($event)) ?></p>
  </div>
</article>
