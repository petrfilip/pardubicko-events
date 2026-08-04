/**
 * Testy načítací vrstvy frontendu (`js/data.js`).
 *
 * Modul do té doby testy neměl, přestože obsahuje pravidlo identity kopií
 * podle ADR 0001 — tedy logiku, jejíž tichá vada už jednou způsobila, že
 * dvě kopie akce s odlišným popisem přežily pod jedním ID.
 *
 * Spuštění bez jakékoli závislosti:
 *
 *     node tools/frontend/test_data.mjs
 */

import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');

const failures = [];

function check(name, condition, detail = '') {
  if (!condition) failures.push(name + (detail ? `: ${detail}` : ''));
}

/** Nahradí fetch čtením z paměti, případně z repozitáře. */
function installFetch(files) {
  globalThis.fetch = async url => {
    if (files && Object.prototype.hasOwnProperty.call(files, url)) {
      return { ok: true, status: 200, json: async () => files[url] };
    }
    if (files) {
      return { ok: false, status: 404, json: async () => ({}) };
    }
    const body = await readFile(join(REPO_ROOT, url), 'utf8');
    return { ok: true, status: 200, json: async () => JSON.parse(body) };
  };
}

// Modul se importuje jednou; fetch se přepíná před každým voláním.
const { loadEventData } = await import(join(REPO_ROOT, 'js', 'data.js'));

// ---------------------------------------------------------------------------
// Nad skutečnými daty repozitáře
// ---------------------------------------------------------------------------

installFetch(null);
const real = await loadEventData();

check('skutečná data se načtou', real.events.length > 0);
check('načetl se slovník kategorií', real.categories.byAxis.kind.length > 0);
check('slovník vrací český popisek', real.categories.label('hudba') === 'Koncerty a hudba');
check('kopie se sloučily podle ID',
  real.events.length === new Set(real.events.map(e => e.id)).size,
  'po sloučení zbyla duplicitní ID');

const multiWeek = real.events.filter(e => e._week_ids.length > 1);
check('vícetýdenní akce mají vyplněné _week_ids', multiWeek.length > 0,
  'žádná akce nezasahuje do více týdnů — zkontroluj testovací předpoklad');
for (const event of multiWeek) {
  check(`_week_ids je bez duplicit (${event.id})`,
    event._week_ids.length === new Set(event._week_ids).size);
}

// ---------------------------------------------------------------------------
// Umělé případy
// ---------------------------------------------------------------------------

const BASE_EVENT = {
  id: 'akce-2026-08-01',
  week: '2026-W31',
  title: 'Akce',
  description: 'Popis.',
  start_at: '2026-08-01T10:00:00+02:00',
  end_at: '2026-08-05T12:00:00+02:00',
  all_day: false,
  venue: 'Náměstí',
  municipality: 'Chrudim',
  categories: ['hudba'],
  price: { type: 'free', text: 'Zdarma' },
  source: { type: 'official', url: 'https://example.org/akce/1' },
  cancelled: false,
};

function repoWith(secondEvent) {
  return {
    'data/manifest.json': {
      schema_version: 1,
      generated_at: '2026-07-27T08:00:00+02:00',
      weeks: [
        { id: '2026-W31', from: '2026-07-27', to: '2026-08-02', file: 'data/weeks/2026-W31.json' },
        { id: '2026-W32', from: '2026-08-03', to: '2026-08-09', file: 'data/weeks/2026-W32.json' },
      ],
    },
    'config/categories.json': {
      schema_version: 1,
      axes: [
        { id: 'kind', label: 'Druh akce', required: true },
        { id: 'audience', label: 'Pro koho', required: false },
      ],
      categories: [
        { id: 'hudba', axis: 'kind', order: 10, label: 'Koncerty a hudba' },
        { id: 'rodiny', axis: 'audience', order: 200, label: 'Pro rodiny s dětmi' },
      ],
      aliases: [
        { alias: 'koncert', category_id: 'hudba' },
        { alias: 'rodina', category_id: 'rodiny' },
      ],
    },
    'data/weeks/2026-W31.json': {
      schema_version: 1, week: '2026-W31', events: [{ ...BASE_EVENT }],
    },
    'data/weeks/2026-W32.json': {
      schema_version: 1, week: '2026-W32', events: [secondEvent],
    },
  };
}

async function expectRejection(name, secondEvent, needle) {
  installFetch(repoWith(secondEvent));
  try {
    await loadEventData();
    failures.push(`${name}: nevyhozena žádná chyba`);
  } catch (error) {
    check(`${name} – správná zpráva`, error.message.includes(needle),
      `zpráva byla ${JSON.stringify(error.message)}`);
  }
}

// Shodná kopie se sloučí.
installFetch(repoWith({ ...BASE_EVENT, week: '2026-W32' }));
const merged = await loadEventData();
check('shodná kopie se sloučí do jedné akce', merged.events.length === 1,
  `zbylo ${merged.events.length}`);
check('sloučená akce zná oba týdny',
  JSON.stringify(merged.events[0]?._week_ids) === JSON.stringify(['2026-W31', '2026-W32']));

// Rozdíl v popisu je chyba — právě tenhle případ dřív proklouzl.
await expectRejection('kopie lišící se popisem',
  { ...BASE_EVENT, week: '2026-W32', description: 'Jiný popis.' }, 'description');

// Rozdíl v čase ověření je chyba.
await expectRejection('kopie lišící se časem ověření',
  { ...BASE_EVENT, week: '2026-W32', last_verified_at: '2026-08-02T09:00:00+02:00' },
  'last_verified_at');

// Rozdíl ve vnořeném objektu je chyba.
await expectRejection('kopie lišící se cenou',
  { ...BASE_EVENT, week: '2026-W32', price: { type: 'paid', text: '100 Kč' } }, 'price');

// Rozdíl v pořadí kategorií je chyba, protože pořadí je součást záznamu.
await expectRejection('kopie lišící se pořadím kategorií',
  { ...BASE_EVENT, week: '2026-W32', categories: ['hudba', 'rodiny'] }, 'categories');

// Publikovaná data musí používat jen kanonická ID a obsahovat druh akce.
await expectRejection('alias není platná publikovaná kategorie',
  { ...BASE_EVENT, week: '2026-W32', categories: ['koncert'] }, 'nekanonickou');
await expectRejection('samotná cílová skupina nestačí',
  { ...BASE_EVENT, week: '2026-W32', categories: ['rodiny'] }, 'osy kind');

// Akce bez ID je chyba.
await expectRejection('akce bez ID',
  { ...BASE_EVENT, week: '2026-W32', id: undefined }, 'bez ID');

// ---------------------------------------------------------------------------

if (failures.length) {
  console.log(`NEPROŠLO ${failures.length} kontrol:\n`);
  for (const item of failures) console.log(' - ' + item);
  process.exit(1);
}
console.log('Všechny kontroly prošly.');
