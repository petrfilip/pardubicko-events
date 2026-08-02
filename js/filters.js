import {
  addDays,
  dateKey,
  eventEnd,
  eventStart,
  formatWeekLabel,
  isFuture,
  parseLocalDate,
} from './format.js';

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

function eventOverlapsRange(event, from, to) {
  return dateKey(eventStart(event)) <= to && dateKey(eventEnd(event)) >= from;
}

function eventOverlapsWeek(event, week) {
  return Boolean(week) && eventOverlapsRange(event, week.from, week.to);
}

function resolveQuickDateRange(mode, now = new Date()) {
  if (!mode) return null;

  const today = parseLocalDate(dateKey(now));
  let from = today;
  let to = today;

  if (mode === 'tomorrow') {
    from = addDays(today, 1);
    to = from;
  } else if (mode === 'weekend') {
    const day = today.getUTCDay();
    const daysToFriday = day === 0 ? -2 : 5 - day;
    from = addDays(today, daysToFriday);
    to = addDays(from, 2);
  }

  return { from: dateKey(from), to: dateKey(to) };
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
    quickDateButtons: [...document.querySelectorAll('[data-date-range]')],
    advancedToggle: document.getElementById('advancedToggle'),
    advancedFilters: document.getElementById('advancedFilters'),
  };
  let activeDateRange = '';

  appendOptions(elements.week, weeks, formatWeekLabel);
  appendOptions(elements.municipality, unique(events.map(event => event.municipality)));
  appendOptions(elements.category, unique(events.flatMap(event => event.categories || [])));

  function updateQuickDateButtons() {
    for (const button of elements.quickDateButtons) {
      const active = button.dataset.dateRange === activeDateRange;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    }
  }

  function updateAdvancedFilterIndicator() {
    const count = [
      elements.municipality.value,
      elements.category.value,
      elements.price.value,
      elements.futureOnly.checked,
    ].filter(Boolean).length;

    elements.advancedToggle?.classList.toggle('has-active-filters', count > 0);
    elements.advancedToggle?.setAttribute(
      'aria-label',
      count > 0 ? `Další filtry, aktivní: ${count}` : 'Další filtry',
    );
  }

  function notifyAdvancedFilterChange() {
    updateAdvancedFilterIndicator();
    onChange();
  }

  function setDateRange(value, notify = true) {
    activeDateRange = value;
    if (value) elements.week.value = '';
    updateQuickDateButtons();
    if (notify) onChange();
  }

  elements.search.addEventListener('input', onChange);
  elements.week.addEventListener('change', () => {
    if (elements.week.value && activeDateRange) setDateRange('', false);
    onChange();
  });
  elements.municipality.addEventListener('change', notifyAdvancedFilterChange);
  elements.category.addEventListener('change', notifyAdvancedFilterChange);
  elements.price.addEventListener('change', notifyAdvancedFilterChange);
  elements.futureOnly.addEventListener('change', notifyAdvancedFilterChange);

  for (const button of elements.quickDateButtons) {
    button.addEventListener('click', () => setDateRange(button.dataset.dateRange || ''));
  }

  elements.advancedToggle?.addEventListener('click', () => {
    const open = elements.advancedFilters.classList.toggle('is-open');
    elements.advancedToggle.setAttribute('aria-expanded', String(open));
  });

  updateQuickDateButtons();
  updateAdvancedFilterIndicator();

  function values(now = new Date()) {
    const quickRange = resolveQuickDateRange(activeDateRange, now);
    return {
      query: elements.search.value.trim().toLocaleLowerCase('cs'),
      week: elements.week.value,
      municipality: elements.municipality.value,
      category: elements.category.value,
      price: elements.price.value,
      futureOnly: elements.futureOnly.checked,
      dateRange: activeDateRange,
      dateFrom: quickRange?.from || '',
      dateTo: quickRange?.to || '',
    };
  }

  function setWeek(weekId) {
    if (activeDateRange) setDateRange('', false);
    if (elements.week.value === weekId) {
      onChange();
      return;
    }
    elements.week.value = weekId;
    onChange();
  }

  return { values, setWeek, setDateRange };
}

export function filterEvents(events, filters, { weeks = [], now = new Date() } = {}) {
  const selectedWeek = filters.week ? weeks.find(week => week.id === filters.week) : null;

  return events
    .filter(event => !filters.query || searchableText(event).includes(filters.query))
    .filter(event => !filters.week || (selectedWeek
      ? eventOverlapsWeek(event, selectedWeek)
      : (event._week_ids || [event.week]).includes(filters.week)))
    .filter(event => !filters.dateFrom || eventOverlapsRange(event, filters.dateFrom, filters.dateTo))
    .filter(event => !filters.municipality || event.municipality === filters.municipality)
    .filter(event => !filters.category || (event.categories || []).includes(filters.category))
    .filter(event => !filters.price || (event.price?.type || 'unknown') === filters.price)
    .filter(event => !filters.futureOnly || isFuture(event, now))
    .sort((first, second) => eventStart(first) - eventStart(second) || collator.compare(first.title, second.title));
}
