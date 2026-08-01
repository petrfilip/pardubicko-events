import { renderCategoryBadges } from './badges.js';
import { buildGoogleCalendarUrl, downloadIcs, eventShareUrl } from './calendar-export.js';
import { formatEventWhen, sourceLabel } from './format.js';

export function createEventDetail(dialog = document.getElementById('eventDetail')) {
  const elements = {
    when: dialog.querySelector('.event-detail-when'),
    categories: dialog.querySelector('.event-detail-categories'),
    title: dialog.querySelector('.event-detail-title'),
    place: dialog.querySelector('.event-detail-place'),
    description: dialog.querySelector('.event-detail-description'),
    price: dialog.querySelector('.event-detail-price'),
    sourceType: dialog.querySelector('.event-detail-source-type'),
    source: dialog.querySelector('.event-detail-source'),
    google: dialog.querySelector('.event-detail-google'),
    ics: dialog.querySelector('.event-detail-ics'),
    share: dialog.querySelector('.event-detail-share'),
    status: dialog.querySelector('.event-detail-action-status'),
  };
  let currentEvent = null;

  function close() {
    if (dialog.open) dialog.close();
  }

  function setStatus(message = '') {
    elements.status.textContent = message;
    elements.status.hidden = !message;
  }

  async function copyShareUrl(event) {
    const url = eventShareUrl(event);
    try {
      await navigator.clipboard.writeText(url);
      setStatus('Odkaz byl zkopírován.');
    } catch {
      window.prompt('Zkopírujte odkaz na akci:', url);
    }
  }

  async function shareCurrentEvent() {
    if (!currentEvent) return;
    const url = eventShareUrl(currentEvent);
    const shareData = {
      title: currentEvent.title,
      text: [currentEvent.title, elements.when.textContent, elements.place.textContent]
        .filter(Boolean)
        .join(' — '),
      url,
    };

    if (navigator.share) {
      try {
        await navigator.share(shareData);
        return;
      } catch (error) {
        if (error.name === 'AbortError') return;
      }
    }

    await copyShareUrl(currentEvent);
  }

  for (const button of dialog.querySelectorAll('[data-detail-close]')) {
    button.addEventListener('click', close);
  }

  elements.ics.addEventListener('click', () => {
    if (currentEvent) downloadIcs(currentEvent);
  });
  elements.share.addEventListener('click', shareCurrentEvent);

  dialog.addEventListener('click', event => {
    if (event.target === dialog) close();
  });

  function open(event) {
    currentEvent = event;
    setStatus();
    elements.when.textContent = formatEventWhen(event);
    renderCategoryBadges(elements.categories, event.categories);
    elements.title.textContent = event.cancelled ? `${event.title} — ZRUŠENO` : event.title;
    elements.place.textContent = [event.venue, event.municipality].filter(Boolean).join(', ');
    elements.description.textContent = event.description || '';
    elements.description.hidden = !event.description;
    elements.price.textContent = event.price?.text || 'Vstupné neuvedeno';
    elements.sourceType.textContent = sourceLabel(event.source?.type);
    elements.google.href = buildGoogleCalendarUrl(event);

    if (event.source?.url) {
      elements.source.href = event.source.url;
      elements.source.hidden = false;
    } else {
      elements.source.removeAttribute('href');
      elements.source.hidden = true;
    }

    if (!dialog.open) dialog.showModal();
  }

  return { open, close };
}
