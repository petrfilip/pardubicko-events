const MANIFEST_URL = 'data/manifest.json';

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

function validateEventIds(events) {
  const ids = new Set();
  for (const event of events) {
    if (!event.id) throw new Error('Nalezena akce bez ID.');
    if (ids.has(event.id)) throw new Error(`Duplicitní ID akce: ${event.id}.`);
    ids.add(event.id);
  }
}

function identityPart(value) {
  return String(value || '').trim().toLocaleLowerCase('cs');
}

function eventIdentity(event) {
  return [
    identityPart(event.title),
    event.start_at || '',
    event.end_at || '',
    identityPart(event.venue),
    identityPart(event.municipality),
    event.source?.url || '',
  ].join('\u001f');
}

function deduplicateEvents(events) {
  const uniqueEvents = new Map();

  for (const event of events) {
    const key = eventIdentity(event);
    const existing = uniqueEvents.get(key);

    if (existing) {
      existing._week_ids = [...new Set([...existing._week_ids, event.week].filter(Boolean))];
      existing._source_ids = [...new Set([...existing._source_ids, event.id])];
      existing.categories = [...new Set([...(existing.categories || []), ...(event.categories || [])])];
      continue;
    }

    uniqueEvents.set(key, {
      ...event,
      _week_ids: event.week ? [event.week] : [],
      _source_ids: [event.id],
    });
  }

  return [...uniqueEvents.values()];
}

export async function loadEventData() {
  const manifest = await fetchJson(MANIFEST_URL);
  validateManifest(manifest);

  const weekFiles = await Promise.all(
    manifest.weeks.map(async week => {
      const data = await fetchJson(week.file);
      validateWeekData(week, data);
      return data.events;
    }),
  );
  const rawEvents = weekFiles.flat();
  validateEventIds(rawEvents);

  return { manifest, events: deduplicateEvents(rawEvents) };
}
