// Render only text and validated host-mediated links. Provider content is never markup.
export function nextDay(day, offset = 1) {
  const at = new Date(`${day}T12:00:00Z`);
  at.setUTCDate(at.getUTCDate() + offset);
  return at.toISOString().slice(0, 10);
}

export function serviceDay(start, query) {
  const day = start.slice(0, 10), time = start.slice(11, 16);
  return query.time_from > query.time_to && time <= query.time_to ? nextDay(day, -1) : day;
}

export function safeLink(value) {
  if (typeof value !== 'string' || value.length > 2048 || /[\x00-\x20\x7f\\]/.test(value)) return null;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && !url.username && !url.password && !url.port ? url.href : null;
  } catch { return null; }
}

export function render(document, data, openLink, options = {}) {
  const root = document.getElementById('grid');
  root.replaceChildren();
  if (!data?.query || !Array.isArray(data.results)) {
    document.getElementById('summary').textContent = 'Waiting for an availability result.';
    return;
  }
  const query = data.query, dates = [];
  for (let day = query.date_from; day <= query.date_to && dates.length < 7; day = nextDay(day)) dates.push(day);
  document.getElementById('summary').textContent = `Party ${query.party_size} · ${query.time_from}–${query.time_to} · Venue local time. Observations, never holds.`;
  const node = (tag, text, parent) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = String(text);
    parent.append(element);
    return element;
  };
  const table = node('table', undefined, root), head = node('tr', undefined, node('thead', undefined, table));
  node('th', 'Venue / provider', head).scope = 'col';
  for (const day of dates) node('th', day, head).scope = 'col';
  const body = node('tbody', undefined, table);
  for (const result of data.results.slice(0, 100)) {
    const slots = Array.isArray(result.slots) ? result.slots : [];
    if (options.onlyAvailable && !slots.some(s => !options.kind || s.kind === options.kind)) continue;
    const row = node('tr', undefined, body), label = node('th', undefined, row);
    label.scope = 'row';
    node('strong', result.venue_name, label);
    node('div', `${result.channel} · ${result.time_zone || 'time zone unknown'}`, label);
    if (result.observed_at) node('small', `${result.observed_at} · ${result.served_from}`, label);
    if (result.note) node('p', result.note, label);
    if (Number.isInteger(result.min_party)) node('p', `Online minimum: ${result.min_party}`, label);
    if (Number.isInteger(result.max_party)) node('p', `Online maximum: ${result.max_party}`, label);
    if (result.contact) node('p', result.contact, label);
    const link = safeLink(result.booking_link?.url);
    if (link) {
      const button = node('button', 'Open booking page', label);
      button.type = 'button';
      button.addEventListener('click', () => openLink(link));
    }
    for (const day of dates) {
      const cell = node('td', undefined, row);
      const visible = slots.filter(s => serviceDay(s.start, query) === day && (!options.kind || s.kind === options.kind));
      for (const slot of visible.slice(0, 50)) {
        const block = node('div', undefined, cell);
        block.className = 'slot';
        node('strong', `${slot.start.slice(11, 16)} · ${slot.kind}`, block);
        if (slot.start.slice(0, 10) !== day) node('small', `Next day ${slot.start.slice(0, 10)}`, block);
        for (const field of ['area', 'experience']) if (slot[field]) node('span', slot[field], block);
        if (slot.price?.display) node('span', slot.price.display, block);
        if (slot.requires_card) node('span', 'Card required on provider page', block);
      }
      if (visible.length > 50) node('small', `${visible.length - 50} more matching slots in the structured result.`, cell);
      const checked = result.coverage?.dates_checked || [];
      const complete = checked.includes(day) && (query.time_from <= query.time_to || checked.includes(nextDay(day)));
      if (!complete && ['ok', 'none_available', 'closed'].includes(result.status)) node('p', 'Coverage incomplete; unchecked times are unknown.', cell);
      if (!visible.length) node('p', complete && ['ok', 'none_available'].includes(result.status) ? 'No matching slots observed' : String(result.status).replaceAll('_', ' '), cell);
      if (result.slots_omitted) node('small', 'Result is abbreviated; more slots may exist.', cell);
    }
  }
  if (!body.children.length) node('p', 'No venues match these filters.', root);
}
