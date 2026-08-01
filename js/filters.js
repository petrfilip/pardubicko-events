import { dateKey, eventEnd, eventStart, formatWeekLabel, isFuture } from './format.js';

const collator = new Intl.Collator('cs');

function unique(values) {
  return [...new Set(values.filter(Boolean))].sort(collator.compare);
}

function searchableText(event) {
  return [
    event.title,
    event.description,
    event.venue,
    event.municipality,
    ...(event.categories || []),
  ]
    .filter(Boolean)
    .join(' ')
    .toLocaleLowerCase('cs');
}

function appendOptions(select, values, label = value => value) {
  for (const value of values) {
    const option = document.createElement('option');
    option.value = typeof value === 'string' ? value : value.id;
    option.textContent = label(value);
    select.appendChild(option);
  }
}

function eventOverlapsWeek(event, week) {
  if (!week) return false;
  return dateKey(eventStart(event)) <= week.to && dateKey(eventEnd(event)) >= week.from;
}

export function eventsForWeek(events, week) {
  return week ? events.filter(event => eventOverlapsWeek(event, week)) : [];
}

export function createFilters({ events, weeks, onChange }) {
  const elements = {
    search: document.getElementById('search'),
    week: document.getElementById('week'),
    municipality: document.getElementById('municipality'),
    category: document.getElementById('category'),
    price: document.getElementById('price'),
    futureOnly: document.getElementById('futureOnly'),
  };

  appendOptions(elements.week, weeks, formatWeekLabel);
  appendOptions(elements.municipality, unique(events.map(event => event.municipality)));
  appendOptions(elements.category, unique(events.flatMap(event => event.categories || [])));

  elements.search.addEventListener('input', onChange);
  elements.week.addEventListener('change', onChange);
  elements.municipality.addEventListener('change', onChange);
  elements.category.addEventListener('change', onChange);
  elements.price.addEventListener('change', onChange);
  elements.futureOnly.addEventListener('change', onChange);

  function values() {
    return {
      query: elements.search.value.trim().toLocaleLowerCase('cs'),
      week: elements.week.value,
      municipality: elements.municipality.value,
      category: elements.category.value,
      price: elements.price.value,
      futureOnly: elements.futureOnly.checked,
    };
  }

  function setWeek(weekId) {
    if (elements.week.value === weekId) return;
    elements.week.value = weekId;
    onChange();
  }

  return { values, setWeek };
}

export function filterEvents(events, filters, { weeks = [], now = new Date() } = {}) {
  const selectedWeek = filters.week ? weeks.find(week => week.id === filters.week) : null;

  return events
    .filter(event => !filters.query || searchableText(event).includes(filters.query))
    .filter(event => !filters.week || (selectedWeek
      ? eventOverlapsWeek(event, selectedWeek)
      : (event._week_ids || [event.week]).includes(filters.week)))
    .filter(event => !filters.municipality || event.municipality === filters.municipality)
    .filter(event => !filters.category || (event.categories || []).includes(filters.category))
    .filter(event => !filters.price || (event.price?.type || 'unknown') === filters.price)
    .filter(event => !filters.futureOnly || isFuture(event, now))
    .sort((first, second) => eventStart(first) - eventStart(second) || collator.compare(first.title, second.title));
}
