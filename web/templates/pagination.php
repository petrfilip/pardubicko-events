<?php if ($pages > 1): ?>
  <nav class="pagination" aria-label="Stránkování">
    <?php if ($page > 1): ?>
      <a rel="prev" href="<?= e($filter->url($path, ['page' => $page - 1], $paginationDrop)) ?>">← Předchozí</a>
    <?php endif; ?>
    <span>Strana <?= (int) $page ?> z <?= (int) $pages ?></span>
    <?php if ($page < $pages): ?>
      <a rel="next" href="<?= e($filter->url($path, ['page' => $page + 1], $paginationDrop)) ?>">Další →</a>
    <?php endif; ?>
  </nav>
<?php endif; ?>
