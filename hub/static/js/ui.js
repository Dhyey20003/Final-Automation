// Small DOM toolkit: element builder, toasts, modals, confirmations, loading states and formatters.

export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === undefined || v === null || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
      else if (k === 'html') el.innerHTML = v; // only used with static, trusted markup (icons)
      else if (k === 'dataset') Object.assign(el.dataset, v);
      else if (v === true) el.setAttribute(k, '');
      else el.setAttribute(k, v);
    }
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }
export function mount(el, ...children) { clear(el); append(el, children); return el; }

const ICONS = {
  users: '<path d="M16 11a4 4 0 1 0-4-4 4 4 0 0 0 4 4Zm-8 1a3 3 0 1 0-3-3 3 3 0 0 0 3 3Zm8 1c-2.7 0-8 1.3-8 4v2h16v-2c0-2.7-5.3-4-8-4Zm-8 .5C5.3 13.5 1 14.8 1 17.3V19h5v-2c0-1.3.7-2.4 1.9-3.3Z"/>',
  upload: '<path d="M12 3 6.5 8.5l1.4 1.4L11 6.8V16h2V6.8l3.1 3.1 1.4-1.4ZM4 18v2h16v-2Z"/>',
  photo: '<path d="M4 5h16a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1Zm1 2v8.6l3.5-3.5 2.5 2.5 4-4L19 14.6V7Zm4.5 1A1.5 1.5 0 1 1 8 9.5 1.5 1.5 0 0 1 9.5 8Z"/>',
  tasks: '<path d="M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4Z"/><path d="M3 3h12v2H3zM3 7h8v2H3z"/>',
  settings: '<path d="M19.4 13a7.6 7.6 0 0 0 0-2l2.1-1.6-2-3.4-2.5 1a7.3 7.3 0 0 0-1.7-1L15 3h-4l-.4 2.6a7.3 7.3 0 0 0-1.7 1l-2.5-1-2 3.4L6.6 11a7.6 7.6 0 0 0 0 2l-2.1 1.6 2 3.4 2.5-1a7.3 7.3 0 0 0 1.7 1L11 21h4l.4-2.6a7.3 7.3 0 0 0 1.7-1l2.5 1 2-3.4ZM13 15.5a3.5 3.5 0 1 1 3.5-3.5 3.5 3.5 0 0 1-3.5 3.5Z" transform="translate(-1 0)"/>',
  search: '<path d="M10 4a6 6 0 1 0 3.7 10.7l4.8 4.8 1.4-1.4-4.8-4.8A6 6 0 0 0 10 4Zm0 2a4 4 0 1 1-4 4 4 4 0 0 1 4-4Z"/>',
  plus: '<path d="M11 5h2v6h6v2h-6v6h-2v-6H5v-2h6z"/>',
  download: '<path d="M11 4h2v8.2l3.1-3.1 1.4 1.4L12 16l-5.5-5.5 1.4-1.4 3.1 3.1ZM4 18h16v2H4z"/>',
  close: '<path d="m6.4 5 5.6 5.6L17.6 5 19 6.4 13.4 12l5.6 5.6-1.4 1.4-5.6-5.6L6.4 19 5 17.6l5.6-5.6L5 6.4z"/>',
  columns: '<path d="M3 4h18v16H3Zm2 2v12h4V6Zm6 0v12h2V6Zm4 0v12h4V6Z"/>',
  mail: '<path d="M3 5h18v14H3Zm2 2v.5l7 4.5 7-4.5V7Zm14 2.8-7 4.5-7-4.5V17h14Z"/>',
  logout: '<path d="M10 17v-3H3v-4h7V7l5 5Zm2-14h7a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-7v-2h7V5h-7Z"/>',
  alert: '<path d="M12 2 1 21h22Zm1 15h-2v-2h2Zm0-4h-2V9h2Z"/>',
  file: '<path d="M6 2h8l6 6v14H6Zm7 1.5V9h5.5Z"/>',
  crop: '<path d="M7 1v4H3v2h4v10h10v4h2v-4h4v-2H9V1Zm2 6v0h8v8h2V5H9Z"/>',
  post: '<path d="M4 3h16a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Zm1 2v14h14V5Zm2 2h6v2H7Zm0 4h10v2H7Zm0 4h10v2H7Z"/>',
  video: '<path d="M3 6h13a1 1 0 0 1 1 1v3.2l4-2.4v8.4l-4-2.4V17a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1Zm1 2v8h11V8Z"/>',
  copy: '<path d="M8 3h11a1 1 0 0 1 1 1v12h-2V5H8Zm-4 4h11a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1Zm1 2v10h9V9Z"/>',
  refresh: '<path d="M12 5a7 7 0 0 1 6.3 4H16v2h6V5h-2v2.3A9 9 0 0 0 3.1 10h2.1A7 7 0 0 1 12 5Zm6.8 9a7 7 0 0 1-12.1 2H8v-2H2v6h2v-2.3A9 9 0 0 0 20.9 14Z"/>',
  check: '<path d="M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4Z"/>',
};

export function icon(name, cls = '') {
  const span = document.createElement('span');
  span.className = 'icon ' + cls;
  span.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true" fill="currentColor">${ICONS[name] || ''}</svg>`;
  return span;
}

// ---------------------------------------------------------------- toasts
export function toast(message, kind = 'info', timeout = 5000) {
  const root = document.getElementById('toasts');
  const t = h('div', { class: `toast toast-${kind}`, role: kind === 'error' ? 'alert' : 'status' },
    h('div', { class: 'toast-msg' }, message),
    h('button', { class: 'toast-close', 'aria-label': 'Dismiss', onclick: () => t.remove() }, '×'));
  root.appendChild(t);
  if (timeout) setTimeout(() => t.remove(), kind === 'error' ? Math.max(timeout, 9000) : timeout);
  return t;
}

export function showError(e, prefix = '') {
  let msg = (prefix ? prefix + ' ' : '') + (e && e.message ? e.message : String(e));
  const fields = e && e.details && e.details.fields;
  if (fields && typeof fields === 'object') {
    const parts = Object.entries(fields).map(([k, v]) => `${k === '_' ? '' : k + ': '}${v}`);
    if (parts.length) msg += ' ' + parts.join('; ');
  }
  toast(msg, 'error');
}

// ---------------------------------------------------------------- modals
export function modal({ title, body, actions = [], wide = false, onClose, dismissible = true }) {
  const root = document.getElementById('modal-root');
  const previous = document.activeElement;
  const close = () => { overlay.remove(); document.removeEventListener('keydown', onKey); if (onClose) onClose(); if (previous && previous.focus) previous.focus(); };
  const onKey = (e) => { if (e.key === 'Escape' && dismissible) close(); };
  const footer = h('div', { class: 'modal-footer' });
  const dialog = h('div', { class: 'modal' + (wide === 'xl' ? ' modal-xl' : wide ? ' modal-wide' : ''), role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
    h('div', { class: 'modal-header' }, h('h2', null, title),
      dismissible ? h('button', { class: 'icon-btn', 'aria-label': 'Close', onclick: close }, icon('close')) : null),
    h('div', { class: 'modal-body' }, body),
    footer);
  const overlay = h('div', { class: 'modal-overlay', onmousedown: (e) => { if (e.target === overlay && dismissible) close(); } }, dialog);
  const api = { close, dialog, footer, setActions };
  function setActions(list) {
    clear(footer);
    list.forEach((a) => {
      const b = h('button', { class: 'btn ' + (a.kind ? 'btn-' + a.kind : 'btn-secondary'), type: 'button' }, a.label);
      b.addEventListener('click', async () => {
        if (a.onClick) {
          b.disabled = true; const old = b.textContent; b.textContent = a.busy || 'Working…';
          try { const r = await a.onClick(api); if (r !== false && a.close !== false) close(); }
          catch (err) { showError(err); }
          finally { b.disabled = false; b.textContent = old; }
        } else close();
      });
      footer.appendChild(b);
    });
  }
  setActions(actions);
  document.addEventListener('keydown', onKey);
  root.appendChild(overlay);
  const first = dialog.querySelector('input, select, textarea, button.btn-primary');
  if (first) setTimeout(() => first.focus(), 30);
  return api;
}

export function confirmDialog({ title, message, confirmLabel = 'Confirm', danger = false, requireText = null, details = null }) {
  return new Promise((resolve) => {
    let done = false;
    const input = requireText ? h('input', { class: 'input', placeholder: `Type ${requireText} to confirm`, 'aria-label': 'Confirmation text' }) : null;
    const body = h('div', null, h('p', null, message), details, requireText ? h('div', { class: 'field' }, h('label', null, `Type ${requireText} to continue`), input) : null);
    const m = modal({
      title, body, onClose: () => { if (!done) resolve(false); },
      actions: [
        { label: 'Cancel', kind: 'secondary' },
        { label: confirmLabel, kind: danger ? 'danger' : 'primary', onClick: () => {
          if (requireText && input.value.trim() !== requireText) { toast(`Type ${requireText} exactly to confirm.`, 'error'); return false; }
          done = true; resolve(true);
        } },
      ],
    });
    return m;
  });
}

export function promptDialog({ title, label, value = '', multiline = false, confirmLabel = 'Save', help = '' }) {
  return new Promise((resolve) => {
    let done = false;
    const input = multiline ? h('textarea', { class: 'input', rows: 4 }) : h('input', { class: 'input' });
    input.value = value;
    modal({
      title, body: h('div', { class: 'field' }, h('label', null, label), input, help ? h('div', { class: 'help' }, help) : null),
      onClose: () => { if (!done) resolve(null); },
      actions: [{ label: 'Cancel' }, { label: confirmLabel, kind: 'primary', onClick: () => { done = true; resolve(input.value); } }],
    });
  });
}

// ---------------------------------------------------------------- loading / empty states
export function spinner(text = 'Loading…') {
  return h('div', { class: 'loading', role: 'status' }, h('span', { class: 'spinner' }), h('span', null, text));
}

export function emptyState(title, text, action) {
  return h('div', { class: 'empty' }, h('div', { class: 'empty-title' }, title), text ? h('p', null, text) : null, action || null);
}

export async function withBusy(button, fn, label = 'Working…') {
  const old = button.textContent;
  button.disabled = true; button.textContent = label;
  try { return await fn(); } finally { button.disabled = false; button.textContent = old; }
}

// ---------------------------------------------------------------- formatting
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
export function fmtDate(v) {
  if (!v) return '';
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(v);
  if (!m) return v;
  return `${m[3]}-${MONTHS[+m[2] - 1]}-${m[1]}`;
}
export function fmtDateTime(v) {
  if (!v) return '';
  const d = new Date(v);
  if (isNaN(d)) return v;
  return d.toLocaleString('en-IN', { timeZone: window.HUB_TZ || 'Asia/Kolkata', day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}
export function fmtBytes(n) {
  if (n === null || n === undefined) return '';
  if (n < 1024) return n + ' B';
  if (n < 1048576) return (n / 1024).toFixed(1) + ' KB';
  return (n / 1048576).toFixed(1) + ' MB';
}

const STATUS_CLASS = {
  'Active': 'green', 'Left': 'grey', 'Pending Review': 'amber', 'Client': 'blue', 'Bench': 'purple', 'Internal': 'teal', 'Unknown': 'grey',
  'Due': 'blue', 'Preparing': 'blue', 'Ready': 'green', 'Needs Attention': 'amber', 'Submitted to Outlook': 'purple',
  'Confirmed in Sent Items': 'green', 'Failed': 'red', 'Delivery Uncertain': 'red', 'Manually Completed': 'teal',
  'verified': 'green', 'failed': 'red', 'committed': 'green', 'undone': 'grey', 'partially_undone': 'amber', 'previewed': 'blue',
  'uploaded': 'blue', 'discarded': 'grey', 'pending': 'blue', 'assigned': 'green', 'skipped': 'grey', 'error': 'red',
  'needs_assets': 'amber', 'ready': 'blue', 'published': 'green', 'auto': 'green', 'review': 'amber', 'unmatched': 'red', 'unchanged': 'grey',
  'applied': 'green', 'queued': 'blue', 'preparing': 'blue', 'rendering': 'purple', 'cleaning_audio': 'purple', 'completed': 'green',
  'interrupted': 'amber', 'submitted': 'purple', 'draft': 'blue', 'replied': 'green', 'uncertain': 'amber', 'sending': 'blue', 'ignored': 'grey',
  'cancelled': 'grey',
};
export function badge(text, cls) {
  return h('span', { class: 'badge badge-' + (cls || STATUS_CLASS[text] || 'grey') }, text);
}

export function field(label, control, help, required) {
  return h('div', { class: 'field' }, h('label', null, label, required ? h('span', { class: 'req', title: 'Required' }, ' *') : null), control, help ? h('div', { class: 'help' }, help) : null);
}

export function select(options, value, attrs = {}) {
  const s = h('select', { class: 'input', ...attrs });
  options.forEach((o) => {
    const [v, label] = Array.isArray(o) ? o : [o, o];
    const opt = h('option', { value: v }, label);
    if (String(v) === String(value ?? '')) opt.selected = true;
    s.appendChild(opt);
  });
  return s;
}

export function tabs(items, active, onChange) {
  return h('div', { class: 'tabs', role: 'tablist' }, items.map(([key, label]) =>
    h('button', { class: 'tab' + (key === active ? ' active' : ''), role: 'tab', 'aria-selected': key === active ? 'true' : 'false', onclick: () => onChange(key) }, label)));
}

export function debounce(fn, ms = 300) {
  let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

export function store(key, value) {
  try {
    if (value === undefined) { const v = localStorage.getItem('hub.' + key); return v ? JSON.parse(v) : null; }
    localStorage.setItem('hub.' + key, JSON.stringify(value));
  } catch (_) { return null; }
  return null;
}
