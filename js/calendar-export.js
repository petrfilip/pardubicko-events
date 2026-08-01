import { addDays, dateKey, eventEnd, eventStart, parseLocalDate } from './format.js';

const DEFAULT_DURATION_MS = 2 * 60 * 60 * 1000;

function compactUtc(date) {
  return date.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z');
}

function compactDate(value) {
  return value.replaceAll('-', '');
}

function exportedEnd(event) {
  return event.end_at ? eventEnd(event) : new Date(eventStart(event).getTime() + DEFAULT_DURATION_MS);
}

function exportNote(event) {
  return event.end_at ? '' : 'Čas ukončení pořadatel neuvedl; export používá orientační délku 2 hodiny.';
}

function eventLocation(event) {
  return [event.venue, event.municipality].filter(Boolean).join(', ');
}

function eventDetails(event, shareUrl) {
  return [
    event.description,
    exportNote(event),
    event.source?.url ? `Zdroj: ${event.source.url}` : '',
    `Detail akce: ${shareUrl}`,
  ].filter(Boolean).join('\n\n');
}

function calendarDates(event) {
  if (event.all_day) {
    const start = dateKey(eventStart(event));
    const inclusiveEnd = dateKey(eventEnd(event));
    const exclusiveEnd = dateKey(addDays(parseLocalDate(inclusiveEnd), 1));
    return `${compactDate(start)}/${compactDate(exclusiveEnd)}`;
  }

  return `${compactUtc(eventStart(event))}/${compactUtc(exportedEnd(event))}`;
}

function escapeIcs(value = '') {
  return String(value)
    .replaceAll('\\', '\\\\')
    .replaceAll('\r\n', '\n')
    .replaceAll('\r', '\n')
    .replaceAll('\n', '\\n')
    .replaceAll(';', '\\;')
    .replaceAll(',', '\\,');
}

function foldIcsLine(line) {
  const chunks = [];
  let remaining = line;
  while (remaining.length > 73) {
    chunks.push(remaining.slice(0, 73));
    remaining = remaining.slice(73);
  }
  chunks.push(remaining);
  return chunks.join('\r\n ');
}

function safeFilename(value) {
  return value
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-zA-Z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .toLocaleLowerCase('en') || 'akce';
}

export function eventShareUrl(event) {
  const url = new URL(window.location.href);
  url.searchParams.set('event', event.id);
  return url.toString();
}

export function buildGoogleCalendarUrl(event) {
  const shareUrl = eventShareUrl(event);
  const url = new URL('https://calendar.google.com/calendar/render');
  url.searchParams.set('action', 'TEMPLATE');
  url.searchParams.set('text', event.cancelled ? `${event.title} — ZRUŠENO` : event.title);
  url.searchParams.set('dates', calendarDates(event));
  url.searchParams.set('details', eventDetails(event, shareUrl));
  url.searchParams.set('location', eventLocation(event));
  url.searchParams.set('ctz', 'Europe/Prague');
  return url.toString();
}

export function buildIcs(event) {
  const shareUrl = eventShareUrl(event);
  const lines = [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'PRODID:-//Akce Pardubicko//CS',
    'CALSCALE:GREGORIAN',
    'METHOD:PUBLISH',
    'BEGIN:VEVENT',
    `UID:${escapeIcs(event.id)}@pardubicko-events`,
    `DTSTAMP:${compactUtc(new Date())}`,
  ];

  if (event.all_day) {
    const start = dateKey(eventStart(event));
    const inclusiveEnd = dateKey(eventEnd(event));
    const exclusiveEnd = dateKey(addDays(parseLocalDate(inclusiveEnd), 1));
    lines.push(`DTSTART;VALUE=DATE:${compactDate(start)}`);
    lines.push(`DTEND;VALUE=DATE:${compactDate(exclusiveEnd)}`);
  } else {
    lines.push(`DTSTART:${compactUtc(eventStart(event))}`);
    lines.push(`DTEND:${compactUtc(exportedEnd(event))}`);
  }

  lines.push(`SUMMARY:${escapeIcs(event.cancelled ? `${event.title} — ZRUŠENO` : event.title)}`);
  lines.push(`DESCRIPTION:${escapeIcs(eventDetails(event, shareUrl))}`);
  lines.push(`LOCATION:${escapeIcs(eventLocation(event))}`);
  lines.push(`URL:${escapeIcs(shareUrl)}`);
  lines.push('END:VEVENT', 'END:VCALENDAR');

  return `${lines.map(foldIcsLine).join('\r\n')}\r\n`;
}

export function downloadIcs(event) {
  const blob = new Blob([buildIcs(event)], { type: 'text/calendar;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `${safeFilename(event.title)}-${dateKey(eventStart(event))}.ics`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
