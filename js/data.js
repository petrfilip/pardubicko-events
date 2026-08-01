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
  const events = weekFiles.flat();
  validateEventIds(events);

  return { manifest, events };
}
