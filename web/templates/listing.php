<?php use Pardubicko\Format; ?>
<header class="page-heading">
  <p class="eyebrow">Pardubicko · Hradecko</p>
  <h1><?= e($title) ?></h1>
  <p><?= e($description) ?></p>
</header>
<?= $view->partial('filter_form', compact('filter', 'municipalities', 'categories')) ?>
<div class="result-heading">
  <h2><?= e(Format::eventCountLabel($count)) ?></h2>
  <?php if (($weeks ?? []) !== []): ?>
    <a href="/kalendar/<?= e((string) ($filter->week !== '' ? $filter->week : $weeks[0]['id'])) ?>">Zobrazit jako kalendář</a>
  <?php endif; ?>
</div>
<?php if ($events === []): ?>
  <div class="empty"><h2>Nic jsme nenašli</h2><p>Zkuste širší období nebo méně filtrů.</p></div>
<?php else: ?>
  <div class="event-list">
    <?php foreach ($events as $event): ?><?= $view->partial('event_card', compact('event')) ?><?php endforeach; ?>
  </div>
<?php endif; ?>
<?= $view->partial('pagination', compact('filter', 'path', 'page', 'pages', 'paginationDrop')) ?>
