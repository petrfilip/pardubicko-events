import { renderCalendar, resolveCalendarWeek } from './calendar.js';
import { loadEventData } from './data.js';
import { createFilters, filterEvents } from './filters.js';
import { eventCountLabel, formatUpdated } from './format.js';
import { renderList } from './list.js';

const state = {
  manifest: null,
  events: [],
  filters: null,
  view: 'list',
  calendarWeek: '',
};

const elements = {
  count: document.getElementById('count'),
  updated: document.getElementById('updated'),
  error: document.getElementById('error'),
  events: document.getElementById('events'),
  calendar: document.getElementById('calendar'),
  listView: document.getElementById('listView'),
  calendarView: document.getElementById('calendarView'),
};

function showError(error) {
  elements.error.hidden = false;
  elements.error.textContent = `Data se nepodařilo načíst: ${error.message}`;
  elements.count.textContent = 'Chyba načítání';
  console.error(error);
}

function setView(view) {
  state.view = view;
  const listActive = view === 'list';
  elements.events.hidden = !listActive;
  elements.calendar.hidden = listActive;
  elements.listView.classList.toggle('active', listActive);
  elements.calendarView.classList.toggle('active', !listActive);
  elements.listView.setAttribute('aria-pressed', String(listActive));
  elements.calendarView.setAttribute('aria-pressed', String(!listActive));

  if (!listActive && !state.filters.values().week) {
    const weekId = resolveCalendarWeek(state.manifest.weeks, state.calendarWeek);
    state.calendarWeek = weekId;
    state.filters.setWeek(weekId);
    return;
  }
  render();
}

function render() {
  const filterValues = state.filters.values();
  if (filterValues.week) state.calendarWeek = filterValues.week;
  const filteredEvents = filterEvents(state.events, filterValues);
  elements.count.textContent = eventCountLabel(filteredEvents.length);

  if (state.view === 'list') {
    renderList(elements.events, filteredEvents);
    return;
  }

  const weekId = resolveCalendarWeek(state.manifest.weeks, filterValues.week || state.calendarWeek);
  renderCalendar(elements.calendar, {
    events: filteredEvents,
    weeks: state.manifest.weeks,
    weekId,
    onWeekChange: nextWeek => state.filters.setWeek(nextWeek),
  });
}

async function init() {
  try {
    const { manifest, events } = await loadEventData();
    state.manifest = manifest;
    state.events = events;
    state.calendarWeek = resolveCalendarWeek(manifest.weeks, '');
    state.filters = createFilters({ events, weeks: manifest.weeks, onChange: render });

    elements.updated.textContent = manifest.generated_at
      ? `Aktualizováno ${formatUpdated(manifest.generated_at)}`
      : '';
    elements.listView.addEventListener('click', () => setView('list'));
    elements.calendarView.addEventListener('click', () => setView('calendar'));
    render();
  } catch (error) {
    showError(error);
  }
}

init();
