/** Čisté testy kategorií, filtrování a URL stavu bez DOM. */

import { readFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const config = JSON.parse(await readFile(join(REPO_ROOT, 'config/categories.json'), 'utf8'));
const { createCategoryDictionary } = await import(join(REPO_ROOT, 'js/categories.js'));
const { categoryTone } = await import(join(REPO_ROOT, 'js/badges.js'));
const { isLongRunningExhibition } = await import(join(REPO_ROOT, 'js/calendar.js'));
const {
  filterEvents,
  filterStateFromUrl,
  searchableText,
  urlWithFilterState,
} = await import(join(REPO_ROOT, 'js/filters.js'));

const failures = [];

function check(name, condition, detail = '') {
  if (!condition) failures.push(name + (detail ? `: ${detail}` : ''));
}

const categories = createCategoryDictionary(config);

check('slovník má dvě osy', Object.keys(categories.axes).length === 2);
check('druhy jsou seřazené', categories.byAxis.kind[0]?.id === 'hudba');
check('publikuje české názvy', categories.label('rodiny') === 'Pro rodiny s dětmi');
check('normalizuje alias s diakritikou', categories.canonical('Klasická hudba') === 'hudba');
check('neznámou hodnotu nevymýšlí', categories.canonical('zcela neznámé') === null);

const baseEvent = {
  id: 'hudebni-program',
  title: 'Večerní program',
  description: '',
  venue: 'Park',
  municipality: 'Pardubice',
  categories: ['hudba', 'rodiny'],
  start_at: '2026-08-08T18:00:00+02:00',
  end_at: '2026-08-08T20:00:00+02:00',
  price: { type: 'free' },
};
const sportEvent = {
  ...baseEvent,
  id: 'sportovni-program',
  title: 'Běh',
  categories: ['sport'],
};

const search = searchableText(baseEvent, categories);
check('fulltext obsahuje český label kategorie', search.includes('koncerty a hudba'));
check('fulltext obsahuje alias', search.includes('klasická-hudba'));

const commonFilters = {
  query: '', week: '', municipality: '', price: '', futureOnly: false,
  dateFrom: '', dateTo: '',
};
check('filtr druhu funguje nezávisle',
  filterEvents([baseEvent, sportEvent], { ...commonFilters, kind: 'sport', audience: '' })[0]?.id
    === 'sportovni-program');
check('filtr cílové skupiny funguje nezávisle',
  filterEvents([baseEvent, sportEvent], { ...commonFilters, kind: '', audience: 'rodiny' })[0]?.id
    === 'hudebni-program');

const initialUrl = new URL('https://example.test/?event=detail-1&legacy=keep');
const state = {
  query: 'koncert', week: '2026-W32', municipality: 'Pardubice',
  kind: 'hudba', audience: 'rodiny', price: 'free', futureOnly: true,
  dateRange: 'weekend',
};
const url = urlWithFilterState(initialUrl, state);
const restored = filterStateFromUrl(url);
check('URL round-trip zachová filtry',
  Object.entries(state).every(([key, value]) => restored[key] === value));
check('URL zachová parametr detailu', url.searchParams.get('event') === 'detail-1');
check('URL zachová cizí parametr', url.searchParams.get('legacy') === 'keep');

check('kanonická hudba má barevný tón', categoryTone('hudba') === 'music');
check('alias se pro tón nepoužívá', categoryTone('koncert') === 'default');

check('sedmidenní výstava je dlouhodobá', isLongRunningExhibition({
  categories: ['vystavy'],
  start_at: '2026-08-01T00:00:00+02:00',
  end_at: '2026-08-07T23:59:59+02:00',
}));
check('alias výstavy není kanonická hodnota', !isLongRunningExhibition({
  categories: ['výstava'],
  start_at: '2026-08-01T00:00:00+02:00',
  end_at: '2026-08-07T23:59:59+02:00',
}));

if (failures.length) {
  console.log(`NEPROŠLO ${failures.length} kontrol:\n`);
  for (const item of failures) console.log(' - ' + item);
  process.exit(1);
}

console.log('Všechny kontroly prošly.');
