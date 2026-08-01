const TIME_ZONE = 'Europe/Prague';
const dateFormatter = new Intl.DateTimeFormat('cs-CZ', {
  day: 'numeric',
  month: 'numeric',
  year: 'numeric',
  timeZone: TIME_ZONE,
});
const weekdayDateFormatter = new Intl.DateTimeFormat('cs-CZ', {
  weekday: 'long',
  day: 'numeric',
  month: 'numeric',
  timeZone: TIME_ZONE,
});
const timeFormatter = new Intl.DateTimeFormat('cs-CZ', {
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
  timeZone: TIME_ZONE,
});
const updatedFormatter = new Intl.DateTimeFormat('cs-CZ', {
  dateStyle: 'medium',
  timeStyle: 'short',
  timeZone: TIME_ZONE,
});
const dateKeyFormatter = new Intl.DateTimeFormat('en-CA', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  timeZone: TIME_ZONE,
});

export function eventStart(event) {
  return new Date(event.start_at);
}

export function eventEnd(event) {
  return event.end_at ? new Date(event.end_at) : eventStart(event);
}

export function isFuture(event, now = new Date()) {
  if (event.end_at) return eventEnd(event) >= now;
  return dateKey(eventStart(event)) >= dateKey(now);
}

export function dateKey(date) {
  const parts = dateKeyFormatter.formatToParts(date);
  const values = Object.fromEntries(parts.map(part => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

export function isSameCalendarDay(first, second) {
  return dateKey(first) === dateKey(second);
}

export function parseLocalDate(value) {
  return new Date(`${value}T12:00:00Z`);
}

export function addDays(date, count) {
  const next = new Date(date);
  next.setUTCDate(next.getUTCDate() + count);
  return next;
}

export function formatEventWhen(event) {
  const start = eventStart(event);
  const end = event.end_at ? eventEnd(event) : null;

  if (event.all_day) {
    if (end && !isSameCalendarDay(start, end)) {
      return `${dateFormatter.format(start)} – ${dateFormatter.format(end)}\ncelý den`;
    }
    return `${dateFormatter.format(start)}\ncelý den`;
  }

  if (end && !isSameCalendarDay(start, end)) {
    return `${dateFormatter.format(start)} ${timeFormatter.format(start)}\n– ${dateFormatter.format(end)} ${timeFormatter.format(end)}`;
  }

  if (end) {
    return `${dateFormatter.format(start)}\n${timeFormatter.format(start)}–${timeFormatter.format(end)}`;
  }

  return `${dateFormatter.format(start)}\nod ${timeFormatter.format(start)}`;
}

export function formatCalendarDay(date) {
  return weekdayDateFormatter.format(date);
}

export function formatCalendarTime(event, day) {
  if (event.all_day) return 'celý den';

  const start = eventStart(event);
  const end = event.end_at ? eventEnd(event) : null;
  if (isSameCalendarDay(start, day)) return timeFormatter.format(start);
  if (end && isSameCalendarDay(end, day)) return `do ${timeFormatter.format(end)}`;
  return 'pokračuje';
}

export function formatWeekLabel(week) {
  const from = parseLocalDate(week.from);
  const to = parseLocalDate(week.to);
  return `${dateFormatter.format(from)} – ${dateFormatter.format(to)} (${week.id})`;
}

export function formatUpdated(value) {
  if (!value) return '';
  return updatedFormatter.format(new Date(value));
}

export function sourceLabel(type) {
  return ({
    official: 'Oficiální zdroj',
    facebook: 'Facebook Event',
    ticketing: 'Prodej vstupenek',
    regional: 'Regionální kalendář',
    'local-organizer': 'Místní pořadatel',
  })[type] || 'Zdroj akce';
}

export function eventCountLabel(count) {
  if (count === 1) return '1 akce';
  if (count >= 2 && count <= 4) return `${count} akce`;
  return `${count} akcí`;
}
