const CATEGORY_TONES = new Map([
  ['hudba', 'music'],
  ['tanec', 'music'],
  ['film', 'film'],
  ['divadlo', 'theatre'],
  ['sport', 'sport'],
  ['vystavy', 'exhibition'],
  ['rodiny', 'family'],
  ['deti', 'family'],
  ['gastro', 'food'],
  ['pamatky', 'history'],
  ['festivaly', 'festival'],
  ['slavnosti', 'festival'],
  ['trhy', 'festival'],
]);

export function categoryTone(categoryId) {
  return CATEGORY_TONES.get(categoryId) || 'default';
}

function createBadge(categoryId, dictionary) {
  const badge = document.createElement('span');
  const tone = categoryTone(categoryId);
  badge.className = `category-badge category-badge-${tone}`;
  badge.textContent = dictionary?.label(categoryId) || categoryId;
  return badge;
}

export function renderCategoryBadges(
  root,
  categories,
  { limit = Infinity, dictionary = null } = {},
) {
  root.replaceChildren();
  const visibleCategories = (categories || []).filter(Boolean).slice(0, limit);

  if (visibleCategories.length === 0) {
    const badge = document.createElement('span');
    badge.className = 'category-badge category-badge-default';
    badge.textContent = 'Bez kategorie';
    root.appendChild(badge);
    return;
  }

  for (const category of visibleCategories) {
    root.appendChild(createBadge(category, dictionary));
  }
}
