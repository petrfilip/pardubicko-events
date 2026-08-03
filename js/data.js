import { createCategoryDictionary } from './categories.js';

const MANIFEST_URL = 'data/manifest.json';
const CATEGORIES_URL = 'config/categories.json';

async function fetchJson(url) {
  const response = await fetch(url, { cache: 'no-store' });
  if (!response.ok) {
    throw new Error(`${url}: HTTP ${response.status}`);
  }
  return response.json();
}

function validateManifest(manifest) {
  if (!manifest || !Array.isArray(manifest.weeks) || manifest.weeks.length === 0) {
    throw new Error('Manifest neobsahuje žádné týdny.');
  }

  const ids = new Set();
  for (const week of manifest.weeks) {
    if (!week.id || !week.from || !week.to || !week.file) {
      throw new Error(`Neúplná položka týdne ${week.id || '(bez ID)'}.`);
    }
    if (ids.has(week.id)) {
      throw new Error(`Duplicitní týden v manifestu: ${week.id}.`);
    }
    ids.add(week.id);
  }
}

function validateWeekData(week, data) {
  if (data.week && data.week !== week.id) {
    throw new Error(`${week.file}: očekáván týden ${week.id}, nalezen ${data.week}.`);
  }
  if (!Array.isArray(data.events)) {
    throw new Error(`${week.file}: pole events chybí nebo není platné.`);
  }
}

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') {
    return Object.keys(value).sort().reduce((result, key) => {
      result[key] = canonical(value[key]);
      return result;
    }, {});
  }
  return value;
}

// Kopie jedné akce v různých týdnech se podle ADR 0001 liší pouze polem
// `week`. Identita je proto celý záznam bez něj, ne vybraný výčet polí.
// Užší výčet znamenal, že rozdíl v popisu neprošel jako chyba, ale jako
// nedeterministicky vybraná varianta — a přesně to se v datech stalo.
function eventIdentity(event) {
  const rest = { ...event };
  delete rest.week;
  return JSON.stringify(canonical(rest));
}

function differingKeys(first, second) {
  const keys = new Set([...Object.keys(first), ...Object.keys(second)]);
  keys.delete('week');
  return [...keys].filter(key => (
    JSON.stringify(canonical(first[key])) !== JSON.stringify(canonical(second[key]))
  ));
}

function validateEventIds(events, categories) {
  const seen = new Map();

  for (const event of events) {
    if (!event.id) throw new Error('Nalezena akce bez ID.');
    if (!Array.isArray(event.categories) || event.categories.length === 0) {
      throw new Error(`Akce ${event.id} nemá kategorii.`);
    }
    if (event.categories.some(category => !categories.byId[category])) {
      throw new Error(`Akce ${event.id} obsahuje nekanonickou kategorii.`);
    }
    if (!event.categories.some(category => categories.axis(category) === 'kind')) {
      throw new Error(`Akce ${event.id} nemá kategorii osy kind.`);
    }

    const previous = seen.get(event.id);
    if (previous && eventIdentity(previous) !== eventIdentity(event)) {
      throw new Error(
        `Konfliktní záznamy se stejným ID akce: ${event.id}. `
        + `Liší se: ${differingKeys(previous, event).join(', ')}.`,
      );
    }

    seen.set(event.id, event);
  }
}

// Slučuje se podle `id`. Shodnost kopií hlídá validateEventIds; kdyby se
// slučovalo podle identity, rozdílné kopie by se nesloučily a přežily by
// jako dvě karty pod jedním ID.
function deduplicateEvents(events) {
  const uniqueEvents = new Map();

  for (const event of events) {
    const existing = uniqueEvents.get(event.id);

    if (existing) {
      existing._week_ids = [...new Set([...existing._week_ids, event.week].filter(Boolean))];
      continue;
    }

    uniqueEvents.set(event.id, {
      ...event,
      _week_ids: event.week ? [event.week] : [],
    });
  }

  return [...uniqueEvents.values()];
}

export async function loadEventData() {
  const [manifest, categoryConfig] = await Promise.all([
    fetchJson(MANIFEST_URL),
    fetchJson(CATEGORIES_URL),
  ]);
  const categories = createCategoryDictionary(categoryConfig);
  validateManifest(manifest);

  const weekFiles = await Promise.all(
    manifest.weeks.map(async week => {
      const data = await fetchJson(week.file);
      validateWeekData(week, data);
      return data.events;
    }),
  );
  const rawEvents = weekFiles.flat();
  validateEventIds(rawEvents, categories);

  return { manifest, categories, events: deduplicateEvents(rawEvents) };
}
