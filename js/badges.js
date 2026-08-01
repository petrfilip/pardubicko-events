const CATEGORY_TONES = new Map([
  ['koncert', 'music'],
  ['hudba', 'music'],
  ['klasická hudba', 'music'],
  ['pop', 'music'],
  ['folklór', 'music'],
  ['kino', 'film'],
  ['divadlo', 'theatre'],
  ['sport', 'sport'],
  ['cyklistika', 'sport'],
  ['výstava', 'exhibition'],
  ['umění', 'exhibition'],
  ['muzeum', 'exhibition'],
  ['rodiny', 'family'],
  ['gastro', 'food'],
  ['historie', 'history'],
  ['prohlídka', 'history'],
  ['festival', 'festival'],
  ['pouť', 'festival'],
  ['jarmark', 'festival'],
  ['trhy', 'festival'],
]);

function createBadge(category) {
  const badge = document.createElement('span');
  const normalized = category.toLocaleLowerCase('cs');
  const tone = CATEGORY_TONES.get(normalized) || 'default';
  badge.className = `category-badge category-badge-${tone}`;
  badge.textContent = category;
  return badge;
}

export function renderCategoryBadges(root, categories, { limit = Infinity } = {}) {
  root.replaceChildren();
  const visibleCategories = (categories || []).filter(Boolean).slice(0, limit);

  if (visibleCategories.length === 0) {
    root.appendChild(createBadge('Bez kategorie'));
    return;
  }

  for (const category of visibleCategories) {
    root.appendChild(createBadge(category));
  }
}
