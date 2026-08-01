import { renderCategoryBadges } from './badges.js';
import {
  addDays,
  dateKey,
  eventEnd,
  eventStart,
  formatCalendarDay,
  formatCalendarTime,
  formatEventWhen,
  formatWeekLabel,
  parseLocalDate,
} from './format.js';

const LONG_RUNNING_CATEGORIES = new Set(['výstava', 'umění', 'muzeum', 'expozice']);
const DAY_MS = 24 * 60 * 60 * 1000;

function eventTouchesDay(event, day) {
  const startKey = dateKey(eventStart(event));
  const endKey = dateKey(eventEnd(event));
  const dayKey = dateKey(day);
  return startKey <= dayKey && endKey >= dayKey;
}

function eventCalendarDaySpan(event) {
  if (!event.end_at) return 1;
  const start = parseLocalDate(dateKey(eventStart(event)));
  const end = parseLocalDate(dateKey(eventEnd(event)));
  return Math.round((end - start) / DAY_MS) + 1;
}

function isLongRunningExhibition(event) {
  const categories = (event.categories || []).map(category => category.toLocaleLowerCase('cs'));
  return categories.some(category => LONG_RUNNING_CATEGORIES.has(category))
    && eventCalendarDaySpan(event) >= 7;
}

function createEventButton(event, day, onEventOpen, timeLabel = '') {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'calendar-event';
  button.title = [event.title, event.venue, event.municipality].filter(Boolean).join(' — ');
  button.addEventListener('click', () => onEventOpen(event));

  const time = document.createElement('span');
  time.className = 'calendar-event-time';
  time.textContent = timeLabel || formatCalendarTime(event, day);

  const heading = document.createElement('span');
  heading.className = 'calendar-event-heading';

  const title = document.createElement('span');
  title.className = 'calendar-event-title';
  title.textContent = event.cancelled ? `${event.title} — ZRUŠENO` : event.title;

  const badges = document.createElement('span');
  badges.className = 'calendar-event-badges category-badges';
  renderCategoryBadges(badges, event.categories, { limit: 1 });

  heading.append(title, badges);
  button.append(time, heading);
  return button;
}

function renderOngoingEvents(events, onEventOpen) {
  if (events.length === 0) return null;

  const section = document.createElement('section');
  section.className = 'calendar-ongoing';
  section.setAttribute('aria-labelledby', 'calendarOngoingTitle');

  const heading = document.createElement('h3');
  heading.id = 'calendarOngoingTitle';
  heading.textContent = 'Probíhá tento týden';

  const description = document.createElement('p');
  description.textContent = 'Dlouhodobé výstavy a expozice zobrazené pouze jednou.';

  const list = document.createElement('div');
  list.className = 'calendar-ongoing-list';

  for (const event of events) {
    const label = formatEventWhen(event).split('\n')[0];
    list.appendChild(createEventButton(event, eventStart(event), onEventOpen, label));
  }

  section.append(heading, description, list);
  return section;
}

export function resolveCalendarWeek(weeks, selectedWeekId, now = new Date()) {
  if (selectedWeekId && weeks.some(week => week.id === selectedWeekId)) return selectedWeekId;
  const today = dateKey(now);
  return weeks.find(week => week.from <= today && week.to >= today)?.id || weeks[0]?.id || '';
}

export function renderCalendar(root, { events, weeks, weekId, onWeekChange, onEventOpen }) {
  root.replaceChildren();
  const weekIndex = weeks.findIndex(week => week.id === weekId);
  const week = weeks[weekIndex];

  if (!week) {
    const message = document.createElement('p');
    message.className = 'message';
    message.textContent = 'Pro kalendář není dostupný žádný týden.';
    root.appendChild(message);
    return;
  }

  const navigation = document.createElement('div');
  navigation.className = 'calendar-navigation';

  const previous = document.createElement('button');
  previous.type = 'button';
  previous.textContent = '← Předchozí';
  previous.disabled = weekIndex <= 0;
  previous.addEventListener('click', () => onWeekChange(weeks[weekIndex - 1].id));

  const heading = document.createElement('h2');
  heading.textContent = formatWeekLabel(week);

  const next = document.createElement('button');
  next.type = 'button';
  next.textContent = 'Další →';
  next.disabled = weekIndex >= weeks.length - 1;
  next.addEventListener('click', () => onWeekChange(weeks[weekIndex + 1].id));

  navigation.append(previous, heading, next);

  const ongoingEvents = events.filter(isLongRunningExhibition);
  const datedEvents = events.filter(event => !isLongRunningExhibition(event));
  const ongoing = renderOngoingEvents(ongoingEvents, onEventOpen);

  const grid = document.createElement('div');
  grid.className = 'calendar-grid';
  const firstDay = parseLocalDate(week.from);

  for (let offset = 0; offset < 7; offset += 1) {
    const day = addDays(firstDay, offset);
    const dayEvents = datedEvents.filter(event => eventTouchesDay(event, day));
    const column = document.createElement('article');
    column.className = 'calendar-day';

    const dayHeading = document.createElement('h3');
    dayHeading.textContent = formatCalendarDay(day);
    column.appendChild(dayHeading);

    if (dayEvents.length === 0) {
      const empty = document.createElement('p');
      empty.className = 'calendar-empty';
      empty.textContent = 'Bez akcí';
      column.appendChild(empty);
    } else {
      for (const event of dayEvents) {
        column.appendChild(createEventButton(event, day, onEventOpen));
      }
    }

    grid.appendChild(column);
  }

  root.append(navigation);
  if (ongoing) root.appendChild(ongoing);
  root.appendChild(grid);
}
