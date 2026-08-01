import {
  addDays,
  dateKey,
  eventEnd,
  eventStart,
  formatCalendarDay,
  formatCalendarTime,
  formatWeekLabel,
  parseLocalDate,
} from './format.js';

function eventTouchesDay(event, day) {
  const startKey = dateKey(eventStart(event));
  const endKey = dateKey(eventEnd(event));
  const dayKey = dateKey(day);
  return startKey <= dayKey && endKey >= dayKey;
}

function createEventLink(event, day) {
  const link = document.createElement('a');
  link.className = 'calendar-event';
  link.href = event.source?.url || '#';
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  link.title = [event.title, event.venue, event.municipality].filter(Boolean).join(' — ');

  const time = document.createElement('span');
  time.className = 'calendar-event-time';
  time.textContent = formatCalendarTime(event, day);

  const title = document.createElement('span');
  title.className = 'calendar-event-title';
  title.textContent = event.cancelled ? `${event.title} — ZRUŠENO` : event.title;

  link.append(time, title);
  return link;
}

export function resolveCalendarWeek(weeks, selectedWeekId, now = new Date()) {
  if (selectedWeekId) return selectedWeekId;
  const today = dateKey(now);
  return weeks.find(week => week.from <= today && week.to >= today)?.id || weeks[0]?.id || '';
}

export function renderCalendar(root, { events, weeks, weekId, onWeekChange }) {
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

  const grid = document.createElement('div');
  grid.className = 'calendar-grid';
  const firstDay = parseLocalDate(week.from);

  for (let offset = 0; offset < 7; offset += 1) {
    const day = addDays(firstDay, offset);
    const dayEvents = events.filter(event => eventTouchesDay(event, day));
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
        column.appendChild(createEventLink(event, day));
      }
    }

    grid.appendChild(column);
  }

  root.append(navigation, grid);
}
