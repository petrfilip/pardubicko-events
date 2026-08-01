import { renderCalendar, resolveCalendarWeek } from './calendar.js';
import { loadEventData } from './data.js';
import { createEventDetail } from './detail.js';
import { createFilters, eventsForWeek, filterEvents } from './filters.js';
import { eventCountLabel, formatUpdated } from './format.js';
import { renderList } from './list.js';

const state = {
  manifest: null,
  events: [],
  filters: null,
  detail: null,
  view: 'calendar',
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
  render();
}

function render() {
  const filterValues = state.filters.values();
  const filteredEvents = filterEvents(state.events, filterValues, { weeks: state.manifest.weeks });

  if (state.view === 'list') {
    elements.count.textContent = eventCountLabel(filteredEvents.length);
    renderList(elements.events, filteredEvents);
    return;
  }

  const weekId = resolveCalendarWeek(state.manifest.weeks, filterValues.week || state.calendarWeek);
  const week = state.manifest.weeks.find(item => item.id === weekId);
  const calendarEvents = eventsForWeek(filteredEvents, week);
  state.calendarWeek = weekId;
  elements.count.textContent = eventCountLabel(calendarEvents.length);

  renderCalendar(elements.calendar, {
    events: calendarEvents,
    weeks: state.manifest.weeks,
    weekId,
    onWeekChange: nextWeek => {
      state.calendarWeek = nextWeek;
      if (filterValues.week) state.filters.setWeek(nextWeek);
      else render();
    },
    onEventOpen: state.detail.open,
  });
}

async function init() {
  try {
    const { manifest, events } = await loadEventData();
    state.manifest = manifest;
    state.events = events;
    state.calendarWeek = resolveCalendarWeek(manifest.weeks, '');
    state.filters = createFilters({ events, weeks: manifest.weeks, onChange: render });
    state.detail = createEventDetail();

    elements.updated.textContent = manifest.generated_at
      ? `Aktualizováno ${formatUpdated(manifest.generated_at)}`
      : '';
    elements.listView.addEventListener('click', () => setView('list'));
    elements.calendarView.addEventListener('click', () => setView('calendar'));
    setView('calendar');
  } catch (error) {
    showError(error);
  }
}

init();
