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

export function searchableText(event, categories = null) {
  return [
    event.title,
    event.description,
    event.venue,
    event.municipality,
    ...(event.categories || []).map(category => (
      categories?.searchText(category) || category
    )),
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

const URL_PARAMETERS = {
  query: 'q',
  week: 'week',
  municipality: 'municipality',
  kind: 'kind',
  audience: 'audience',
  price: 'price',
  futureOnly: 'future',
  dateRange: 'date',
};

export function filterStateFromUrl(value) {
  const url = value instanceof URL ? value : new URL(value, 'https://example.invalid/');
  return {
    query: url.searchParams.get(URL_PARAMETERS.query) || '',
    week: url.searchParams.get(URL_PARAMETERS.week) || '',
    municipality: url.searchParams.get(URL_PARAMETERS.municipality) || '',
    kind: url.searchParams.get(URL_PARAMETERS.kind) || '',
    audience: url.searchParams.get(URL_PARAMETERS.audience) || '',
    price: url.searchParams.get(URL_PARAMETERS.price) || '',
    futureOnly: url.searchParams.get(URL_PARAMETERS.futureOnly) === '1',
    dateRange: url.searchParams.get(URL_PARAMETERS.dateRange) || '',
  };
}

export function urlWithFilterState(value, state) {
  const url = new URL(value.toString());
  for (const [field, parameter] of Object.entries(URL_PARAMETERS)) {
    const raw = field === 'futureOnly' ? (state[field] ? '1' : '') : (state[field] || '');
    if (raw) url.searchParams.set(parameter, raw);
    else url.searchParams.delete(parameter);
  }
  return url;
}

function hasOption(select, value) {
  return !value || [...select.options].some(option => option.value === value);
}

export function createFilters({ events, weeks, categories, onChange }) {
  const elements = {
    search: document.getElementById('search'),
    week: document.getElementById('week'),
    municipality: document.getElementById('municipality'),
    kind: document.getElementById('kind'),
    audience: document.getElementById('audience'),
    price: document.getElementById('price'),
    futureOnly: document.getElementById('futureOnly'),
    quickDateButtons: [...document.querySelectorAll('[data-date-range]')],
    advancedToggle: document.getElementById('advancedToggle'),
    advancedFilters: document.getElementById('advancedFilters'),
  };
  let activeDateRange = '';

  appendOptions(elements.week, weeks, formatWeekLabel);
  appendOptions(elements.municipality, unique(events.map(event => event.municipality)));
  appendOptions(elements.kind, categories.byAxis.kind || [], category => category.label);
  appendOptions(elements.audience, categories.byAxis.audience || [], category => category.label);

  const initial = filterStateFromUrl(new URL(window.location.href));
  elements.search.value = initial.query;
  if (hasOption(elements.week, initial.week)) elements.week.value = initial.week;
  if (hasOption(elements.municipality, initial.municipality)) {
    elements.municipality.value = initial.municipality;
  }
  if (hasOption(elements.kind, initial.kind)) elements.kind.value = initial.kind;
  if (hasOption(elements.audience, initial.audience)) elements.audience.value = initial.audience;
  if (hasOption(elements.price, initial.price)) elements.price.value = initial.price;
  elements.futureOnly.checked = initial.futureOnly;
  if (['today', 'tomorrow', 'weekend'].includes(initial.dateRange)) {
    activeDateRange = initial.dateRange;
    elements.week.value = '';
  }

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
      elements.kind.value,
      elements.audience.value,
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
    syncUrl();
    onChange();
  }

  function setDateRange(value, notify = true) {
    activeDateRange = value;
    if (value) elements.week.value = '';
    updateQuickDateButtons();
    if (notify) {
      syncUrl();
      onChange();
    }
  }

  elements.search.addEventListener('input', () => {
    syncUrl();
    onChange();
  });
  elements.week.addEventListener('change', () => {
    if (elements.week.value && activeDateRange) setDateRange('', false);
    syncUrl();
    onChange();
  });
  elements.municipality.addEventListener('change', notifyAdvancedFilterChange);
  elements.kind.addEventListener('change', notifyAdvancedFilterChange);
  elements.audience.addEventListener('change', notifyAdvancedFilterChange);
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
      kind: elements.kind.value,
      audience: elements.audience.value,
      price: elements.price.value,
      futureOnly: elements.futureOnly.checked,
      dateRange: activeDateRange,
      dateFrom: quickRange?.from || '',
      dateTo: quickRange?.to || '',
    };
  }

  function syncUrl() {
    history.replaceState({}, '', urlWithFilterState(new URL(window.location.href), values()));
  }

  function setWeek(weekId) {
    if (activeDateRange) setDateRange('', false);
    if (elements.week.value === weekId) {
      syncUrl();
      onChange();
      return;
    }
    elements.week.value = weekId;
    syncUrl();
    onChange();
  }

  return { values, setWeek, setDateRange };
}

export function filterEvents(
  events,
  filters,
  { weeks = [], now = new Date(), categories = null } = {},
) {
  const selectedWeek = filters.week ? weeks.find(week => week.id === filters.week) : null;

  return events
    .filter(event => !filters.query || searchableText(event, categories).includes(filters.query))
    .filter(event => !filters.week || (selectedWeek
      ? eventOverlapsWeek(event, selectedWeek)
      : (event._week_ids || [event.week]).includes(filters.week)))
    .filter(event => !filters.dateFrom || eventOverlapsRange(event, filters.dateFrom, filters.dateTo))
    .filter(event => !filters.municipality || event.municipality === filters.municipality)
    .filter(event => !filters.kind || (event.categories || []).includes(filters.kind))
    .filter(event => !filters.audience || (event.categories || []).includes(filters.audience))
    .filter(event => !filters.price || (event.price?.type || 'unknown') === filters.price)
    .filter(event => !filters.futureOnly || isFuture(event, now))
    .sort((first, second) => eventStart(first) - eventStart(second) || collator.compare(first.title, second.title));
}
