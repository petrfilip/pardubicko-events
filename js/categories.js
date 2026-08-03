function normalize(value) {
  return String(value || '')
    .trim()
    .toLocaleLowerCase('cs')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[\s_]+/g, '-')
    .replace(/-{2,}/g, '-')
    .replace(/^-|-$/g, '');
}

export function createCategoryDictionary(config) {
  if (!config || !Array.isArray(config.axes) || !Array.isArray(config.categories)) {
    throw new Error('Slovník kategorií nemá osy nebo kategorie.');
  }

  const axes = Object.fromEntries(config.axes.map(axis => [axis.id, { ...axis }]));
  const categories = [...config.categories]
    .sort((a, b) => (a.order ?? Number.MAX_SAFE_INTEGER) - (b.order ?? Number.MAX_SAFE_INTEGER));
  const byId = Object.fromEntries(categories.map(category => [category.id, { ...category }]));
  const aliases = Object.fromEntries(categories.map(category => [category.id, []]));

  for (const entry of config.aliases || []) {
    if (!byId[entry.category_id]) {
      throw new Error(`Alias ${entry.alias} míří na neznámou kategorii.`);
    }
    aliases[entry.category_id].push(entry.alias);
  }

  const byAxis = {};
  for (const axis of config.axes) {
    byAxis[axis.id] = categories.filter(category => category.axis === axis.id);
  }

  return {
    axes,
    categories,
    byId,
    byAxis,
    aliases,
    label(id) { return byId[id]?.label || id; },
    axis(id) { return byId[id]?.axis || ''; },
    searchText(id) {
      const category = byId[id];
      if (!category) return id;
      return [id, category.label, ...(aliases[id] || [])].join(' ');
    },
    canonical(value) {
      const key = normalize(value);
      const direct = categories.find(category => normalize(category.id) === key);
      if (direct) return direct.id;
      const alias = (config.aliases || []).find(entry => normalize(entry.alias) === key);
      return alias?.category_id || null;
    },
  };
}

