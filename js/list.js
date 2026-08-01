import { renderCategoryBadges } from './badges.js';
import { formatEventWhen, sourceLabel } from './format.js';

export function renderList(root, events) {
  root.replaceChildren();

  if (events.length === 0) {
    const message = document.createElement('p');
    message.className = 'message';
    message.textContent = 'Žádné akce neodpovídají zvoleným filtrům.';
    root.appendChild(message);
    return;
  }

  const template = document.getElementById('event-template');
  const fragment = document.createDocumentFragment();

  for (const event of events) {
    const card = template.content.cloneNode(true);
    card.querySelector('.event-date').textContent = formatEventWhen(event);
    renderCategoryBadges(card.querySelector('.event-category'), event.categories, { limit: 1 });
    card.querySelector('.event-price').textContent = event.price?.text || 'Vstupné neuvedeno';
    card.querySelector('.event-title').textContent = event.cancelled ? `${event.title} — ZRUŠENO` : event.title;
    card.querySelector('.event-place').textContent = [event.venue, event.municipality].filter(Boolean).join(', ');

    const description = card.querySelector('.event-description');
    description.textContent = event.description || '';
    description.hidden = !event.description;

    card.querySelector('.event-source-type').textContent = sourceLabel(event.source?.type);
    const link = card.querySelector('.event-source');
    if (event.source?.url) {
      link.href = event.source.url;
    } else {
      link.remove();
    }

    fragment.appendChild(card);
  }

  root.appendChild(fragment);
}
