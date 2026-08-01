import { renderCategoryBadges } from './badges.js';
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
  };

  function close() {
    if (dialog.open) dialog.close();
  }

  for (const button of dialog.querySelectorAll('[data-detail-close]')) {
    button.addEventListener('click', close);
  }

  dialog.addEventListener('click', event => {
    if (event.target === dialog) close();
  });

  function open(event) {
    elements.when.textContent = formatEventWhen(event);
    renderCategoryBadges(elements.categories, event.categories);
    elements.title.textContent = event.cancelled ? `${event.title} — ZRUŠENO` : event.title;
    elements.place.textContent = [event.venue, event.municipality].filter(Boolean).join(', ');
    elements.description.textContent = event.description || '';
    elements.description.hidden = !event.description;
    elements.price.textContent = event.price?.text || 'Vstupné neuvedeno';
    elements.sourceType.textContent = sourceLabel(event.source?.type);

    if (event.source?.url) {
      elements.source.href = event.source.url;
      elements.source.hidden = false;
    } else {
      elements.source.removeAttribute('href');
      elements.source.hidden = true;
    }

    dialog.showModal();
  }

  return { open, close };
}
