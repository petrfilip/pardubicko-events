<?php use Pardubicko\Format; ?>
<header class="page-heading">
  <p class="eyebrow">Týdenní pohled</p>
  <h1><?= e($title) ?></h1>
  <p><?= e($description) ?></p>
</header>
<nav class="week-switcher" aria-label="Vybrat týden">
  <?php foreach ($weeks as $candidate): ?>
    <a<?= $candidate['id'] === $week['id'] ? ' aria-current="page" class="active"' : '' ?> href="/kalendar/<?= e($candidate['id']) ?>"><?= e($candidate['id']) ?><small><?= e(Format::weekLabel($candidate)) ?></small></a>
  <?php endforeach; ?>
</nav>
<?= $view->partial('filter_form', compact('filter', 'municipalities', 'categories')) ?>
<div class="calendar-grid">
  <?php foreach ($days as $day): ?>
    <section class="calendar-day">
      <h2><?= e(Format::dayHeading($day['date'])) ?></h2>
      <?php if ($day['events'] === []): ?><p class="muted">Bez akcí</p><?php endif; ?>
      <?php foreach ($day['events'] as $event): ?>
        <article><time><?= e(Format::calendarTime($event, $day['date'])) ?></time><a href="/akce/<?= e(rawurlencode($event['id'])) ?>"><?= e($event['title']) ?></a><small><?= e($event['municipality_name']) ?></small></article>
      <?php endforeach; ?>
    </section>
  <?php endforeach; ?>
</div>
