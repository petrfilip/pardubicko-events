<?php use Pardubicko\EventFilter; ?>
<form class="filters" action="/hledat" method="get" role="search">
  <div class="filter-primary">
    <label class="field field-grow">
      <span>Co hledáte</span>
      <input type="search" name="q" value="<?= e($filter->query) ?>" placeholder="koncert, výstava, divadlo…">
    </label>
    <label class="field">
      <span>Od</span>
      <input type="date" name="od" value="<?= e($filter->from) ?>">
    </label>
    <label class="field">
      <span>Do</span>
      <input type="date" name="do" value="<?= e($filter->to) ?>">
    </label>
    <button class="button" type="submit">Najít akce</button>
  </div>
  <details class="advanced"<?= $filter->hasAdvanced() ? ' open' : '' ?>>
    <summary>Další filtry</summary>
    <div class="filter-secondary">
      <label class="field">
        <span>Obec</span>
        <select name="obec">
          <option value="">Všechny obce</option>
          <?php foreach ($municipalities as $municipality): ?>
            <option value="<?= e($municipality['slug']) ?>"<?= $filter->municipality === $municipality['slug'] ? ' selected' : '' ?>>
              <?= e($municipality['name']) ?> (<?= (int) $municipality['event_count'] ?>)
            </option>
          <?php endforeach; ?>
        </select>
      </label>
      <label class="field">
        <span>Druh akce</span>
        <select name="kategorie">
          <option value="">Všechny druhy</option>
          <?php foreach ($categories as $category): ?>
            <?php if ($category['axis'] !== 'kind') { continue; } ?>
            <option value="<?= e($category['id']) ?>"<?= $filter->category === $category['id'] ? ' selected' : '' ?>>
              <?= e($category['label']) ?> (<?= (int) $category['event_count'] ?>)
            </option>
          <?php endforeach; ?>
          <?php if (array_filter($categories, static fn (array $item): bool => $item['axis'] === 'audience') !== []): ?>
            <optgroup label="Pro koho">
              <?php foreach ($categories as $category): ?>
                <?php if ($category['axis'] !== 'audience') { continue; } ?>
                <option value="<?= e($category['id']) ?>"<?= $filter->category === $category['id'] ? ' selected' : '' ?>>
                  <?= e($category['label']) ?> (<?= (int) $category['event_count'] ?>)
                </option>
              <?php endforeach; ?>
            </optgroup>
          <?php endif; ?>
        </select>
      </label>
      <label class="field">
        <span>Vstupné</span>
        <select name="cena">
          <option value="">Libovolné</option>
          <?php foreach (EventFilter::PRICES as $value => $label): ?>
            <option value="<?= e($value) ?>"<?= $filter->price === $value ? ' selected' : '' ?>><?= e($label) ?></option>
          <?php endforeach; ?>
        </select>
      </label>
      <label class="check">
        <input type="checkbox" name="budouci" value="1"<?= $filter->futureOnly ? ' checked' : '' ?>>
        <span>Jen probíhající a budoucí</span>
      </label>
    </div>
  </details>
  <?php if ($filter->isActive()): ?>
    <a class="clear-filters" href="/hledat">Zrušit všechny filtry</a>
  <?php endif; ?>
</form>
