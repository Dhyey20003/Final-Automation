// Posts: deployment post projects (list, editor with live preview), clients and logos, email requests and replies, backfill.
import { get, post, patch, del, upload, download, qs } from '../api.js';
import { h, mount, clear, icon, toast, showError, modal, confirmDialog, spinner, emptyState, badge, field, select, tabs, fmtDateTime, debounce } from '../ui.js';
import { state, can, setPage } from '../app.js';
import { employeePicker } from './photos.js';
import { openProfile } from './employees.js';

const STATUS_LABEL = { needs_assets: 'Needs attention', ready: 'Ready', published: 'Published' };
const TABS = [['posts', 'Posts'], ['clients', 'Clients and logos'], ['requests', 'Requests and replies'], ['backfill', 'Backfill']];
let P = null;

export async function render(root, rest) {
  if (rest[0] && /^\d+$/.test(rest[0])) return editor(root, +rest[0]);
  setPage('Posts');
  const tab = TABS.find(([k]) => k === rest[0]) ? rest[0] : 'posts';
  const body = h('div');
  mount(root, tabs(TABS, tab, (k) => { location.hash = k === 'posts' ? '#/posts' : '#/posts/' + k; }), body);
  mount(body, spinner());
  try { await ({ posts: listView, clients: clientsView, requests: requestsView, backfill: backfillView })[tab](body); }
  catch (e) { mount(body, h('div', { class: 'card' }, h('p', null, e.message))); }
}

// ---------------------------------------------------------------- list
async function listView(root) {
  P = P && P.kind === 'list' ? P : { kind: 'list', status: '', q: '' };
  const actions = [];
  if (can('posts.manage')) {
    actions.push(h('button', { class: 'btn btn-secondary', onclick: async () => { try { const r = await post('/api/posts/complete-pending'); toast(`${r.completed} waiting posts were completed.`, 'success'); listView(root); } catch (e) { showError(e); } } }, icon('refresh'), 'Complete waiting posts'));
    actions.push(h('button', { class: 'btn btn-primary', onclick: () => newPostDialog(root) }, icon('plus'), 'New post'));
  }
  setPage('Posts', actions);
  const data = await get('/api/posts?' + qs({ status: P.status, q: P.q }));
  const c = data.counts;
  const chips = [['', `All (${c.needs_assets + c.ready + c.published})`], ['needs_assets', `Needs attention (${c.needs_assets})`], ['ready', `Ready (${c.ready})`], ['published', `Published (${c.published})`]];
  const search = h('input', { class: 'input', type: 'search', placeholder: 'Search employee, client or post ID', value: P.q, 'aria-label': 'Search posts' });
  search.addEventListener('input', debounce(() => { P.q = search.value; listView(root); }, 300));
  const hint = h('p', { class: 'muted small' }, 'A post is created automatically when an employee is saved with a client assignment. Saving the same assignment again never creates a second post. ',
    c.pending_replies ? h('a', { href: '#/posts/requests' }, `${c.pending_replies} email replies are waiting for review.`) : null);
  mount(root,
    h('div', { class: 'grid-toolbar' }, h('div', { class: 'search' }, icon('search'), search),
      h('div', { class: 'chips' }, chips.map(([k, l]) => h('button', { class: 'chip' + (P.status === k ? ' active' : ''), onclick: () => { P.status = k; listView(root); } }, l)))),
    hint,
    data.projects.length ? h('div', { class: 'post-grid' }, data.projects.map(card)) :
      emptyState('No posts here yet', 'Assign a client to an employee (Employees > Edit > Assignment) and a post project appears here. Past deployments can be added from the Backfill tab.'));
  if (search.value) search.focus();
}

function card(p) {
  const img = p.has_render ? h('img', { src: `/api/posts/${p.id}/image?v=${encodeURIComponent(p.last_rendered_at || '')}`, alt: '' })
    : h('div', { class: 'muted small', style: { padding: '12px', textAlign: 'center' } }, p.missing.length ? 'Waiting for: ' + p.missing.map((m) => m.split('.')[0]).join(', ') : 'Not generated yet');
  return h('div', { class: 'post-card' },
    h('div', { class: 'post-img', onclick: () => { location.hash = '#/posts/' + p.id; } }, img),
    h('div', { class: 'post-body' },
      h('div', { class: 'row-between' }, h('div', { class: 'post-name' }, p.data.display_name || p.employee.full_name), badge(STATUS_LABEL[p.status] || p.status, { needs_assets: 'amber', ready: 'blue', published: 'green' }[p.status])),
      h('div', null, p.data.client_display || (p.client && p.client.name) || h('span', { class: 'muted' }, 'no client'), h('span', { class: 'muted' }, ` · ${p.data.designation || 'no designation'}`)),
      h('div', { class: 'muted small' }, `${p.employee.rhc_id || ''} · ${p.project_uid} · v${p.draft_version}${p.has_unpublished_changes ? ' (unpublished changes)' : ''}`),
      p.missing.length ? h('ul', { class: 'missing' }, p.missing.map((m) => h('li', null, m))) : null,
      p.review_flags.length ? h('ul', { class: 'flags' }, p.review_flags.slice(0, 2).map((f) => h('li', null, f))) : null,
      h('div', { class: 'row', style: { marginTop: 'auto' } }, h('a', { class: 'btn btn-secondary btn-sm', href: '#/posts/' + p.id }, 'Open'),
        p.has_render ? h('button', { class: 'btn btn-secondary btn-sm', onclick: () => copyImage(p.id) }, icon('copy'), 'Copy image') : null,
        p.requests.length ? h('span', { class: 'small muted' }, `${p.requests.length} request${p.requests.length === 1 ? '' : 's'}`) : null)));
}

function newPostDialog(root) {
  let chosen = null;
  const picker = employeePicker(null, (e) => { chosen = e; });
  const client = h('input', { class: 'input', placeholder: 'Client name' });
  const start = h('input', { class: 'input', type: 'date' });
  modal({ title: 'New deployment post', body: h('div', null, h('p', { class: 'muted small' }, 'Use this for a deployment that is not (or not yet) on the employee record. It does not change the employee record.'),
    field('Employee', picker, null, true), field('Client', client, null, true), field('Deployment start (optional)', start)),
  actions: [{ label: 'Cancel' }, { label: 'Create post', kind: 'primary', onClick: async () => {
    if (!chosen) { toast('Choose the employee.', 'error'); return false; }
    const r = await post('/api/posts', { employee_id: chosen.id, client_name: client.value, assignment_start: start.value || null });
    toast(r.outcome === 'created' ? 'Post project created.' : 'A post for this deployment already exists; opening it.', r.outcome === 'created' ? 'success' : 'warn');
    location.hash = '#/posts/' + r.project.id;
  } }] });
}

// ---------------------------------------------------------------- clipboard helpers
async function fetchBlob(url, opts) {
  const res = await fetch(url, { credentials: 'same-origin', ...(opts || {}) });
  if (!res.ok) { let msg = 'Request failed'; try { msg = (await res.json()).error.message; } catch (_) { /* ignore */ } throw new Error(msg); }
  return res.blob();
}

async function copyImage(id) {
  try {
    const blob = await fetchBlob(`/api/posts/${id}/image`);
    if (!navigator.clipboard || !window.ClipboardItem) throw new Error('This browser cannot copy images. Use Export instead.');
    await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
    toast('Image copied. Press Ctrl+V in Teams, Outlook or Word.', 'success');
  } catch (e) { showError(e, 'Copy failed.'); }
}

async function copyText(p) {
  try {
    if (navigator.clipboard && window.ClipboardItem) {
      await navigator.clipboard.write([new ClipboardItem({ 'text/html': new Blob([p.announcement_html], { type: 'text/html' }), 'text/plain': new Blob([p.announcement_plain], { type: 'text/plain' }) })]);
    } else await navigator.clipboard.writeText(p.announcement_plain);
    toast('Announcement copied. The name stays bold when pasted into Teams or Outlook.', 'success');
  } catch (e) { showError(e, 'Copy failed.'); }
}

// ---------------------------------------------------------------- editor
async function editor(root, id) {
  let p;
  try { p = await get(`/api/posts/${id}`); } catch (e) { mount(root, h('div', { class: 'card' }, h('p', null, e.message), h('a', { href: '#/posts' }, 'Back to posts'))); return; }
  const settings = await get('/api/posts/settings');
  const draft = JSON.parse(JSON.stringify(p.data));
  const editable = can('posts.manage');
  const E = { root, p, draft, dirty: false, previewUrl: null };
  setPage(`Post: ${p.data.display_name || p.employee.full_name}`, [h('a', { class: 'btn btn-secondary', href: '#/posts' }, 'Back to posts')]);

  // ---- preview
  const img = h('img', { alt: 'Post preview' });
  const pstatus = h('div', { class: 'preview-status' });
  const refreshPreview = debounce(async () => {
    pstatus.textContent = 'Rendering preview…';
    try {
      const blob = await fetchBlob(`/api/posts/${id}/preview`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': state.user.csrf_token }, body: JSON.stringify({ data: E.draft }) });
      if (E.previewUrl) URL.revokeObjectURL(E.previewUrl);
      E.previewUrl = URL.createObjectURL(blob); img.src = E.previewUrl;
      pstatus.textContent = E.dirty ? 'Preview of unsaved changes.' : 'Preview matches the saved draft.';
    } catch (e) { pstatus.textContent = 'Preview not possible: ' + e.message; }
  }, 350);
  const changed = () => { E.dirty = true; saveBtn.disabled = false; refreshPreview(); };

  // ---- text fields
  const overrides = new Set(draft.overrides || []);
  const srcNote = (key) => {
    const isOv = overrides.has(key);
    const src = p.source_snapshot[key];
    return h('div', { class: 'override-note' }, isOv ? ['Edited for this post only. ', src ? `Employee/client record says "${src}". ` : '',
      editable ? h('button', { class: 'btn btn-link btn-sm', onclick: async () => { try { await post(`/api/posts/${id}/reset-field`, { field: key }); toast('Following the record again.', 'success'); editor(root, id); } catch (e) { showError(e); } } }, 'Follow the record again') : null]
      : 'Follows the employee/client record. Editing here changes only this post.');
  };
  const textInput = (key, label) => {
    const el = h('input', { class: 'input', value: draft[key] || '', disabled: !editable });
    el.oninput = () => { draft[key] = el.value; overrides.add(key); changed(); };
    return field(label, el, srcNote(key));
  };
  const pron = select(settings.pronouns.map((x) => [x, x]), draft.pronoun || 'him', { disabled: !editable });
  pron.onchange = () => { draft.pronoun = pron.value; changed(); };
  const ann = h('textarea', { class: 'input', rows: 8, disabled: !editable }); ann.value = draft.announcement || settings.posts.announcement_template;
  ann.oninput = () => { draft.announcement = ann.value; changed(); };

  // ---- assets
  const photoRow = h('div', { class: 'asset-row' });
  const logoRow = h('div', { class: 'asset-row' });
  const drawAssets = () => {
    mount(photoRow, p.photo_options.map((ph) => h('div', { class: 'asset-pick' + (draft.photo_id === ph.id && !draft.custom_photo_path ? ' active' : ''), title: `v${ph.version_no} ${ph.original_filename || ''}`,
      onclick: () => { if (!editable) return; draft.photo_id = ph.id; draft.custom_photo_path = null; overrides.add('photo_id'); drawAssets(); changed(); } },
    h('img', { src: `/api/photos/${ph.id}/thumb`, alt: '' }), `v${ph.version_no}${ph.is_current ? ' (current)' : ''}`)),
    draft.custom_photo_path ? h('div', { class: 'asset-pick active' }, h('div', { style: { width: '64px', height: '64px', background: '#f2f3f7', borderRadius: '6px', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '11px' } }, 'custom'), 'Post only') : null,
    !p.photo_options.length && !draft.custom_photo_path ? h('span', { class: 'muted small' }, 'No photo on the employee record yet.') : null);
    mount(logoRow, p.logo_options.map((lg) => h('div', { class: 'asset-pick' + (draft.logo_id === lg.id && !draft.custom_logo_path ? ' active' : ''), title: `v${lg.version_no} ${lg.original_filename || ''} (${lg.source || ''})`,
      onclick: () => { if (!editable) return; draft.logo_id = lg.id; draft.custom_logo_path = null; overrides.add('logo_id'); drawAssets(); changed(); } },
    h('img', { src: `/api/logos/${lg.id}/thumb`, alt: '' }), `v${lg.version_no}${lg.is_current ? ' (current)' : ''}`)),
    draft.custom_logo_path ? h('div', { class: 'asset-pick active' }, h('div', { style: { width: '64px', height: '64px', background: '#f2f3f7', borderRadius: '6px', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '11px' } }, 'custom'), 'Post only') : null,
    !p.logo_options.length && !draft.custom_logo_path ? h('span', { class: 'muted small' }, p.client ? 'No logo for this client yet. Upload one under Clients and logos, or request it by email.' : 'No client linked.') : null);
  };
  drawAssets();
  const assetUpload = (kind, label) => {
    const inp = h('input', { type: 'file', accept: 'image/*', class: 'hidden' });
    inp.onchange = async () => {
      if (!inp.files.length) return;
      const fd = new FormData(); fd.append('file', inp.files[0]);
      try { await upload(`/api/posts/${id}/asset?kind=${kind}`, fd); toast(`${label} added to this post only.`, 'success'); editor(root, id); } catch (e) { showError(e); }
    };
    return h('span', null, h('button', { class: 'btn btn-link btn-sm', disabled: !editable, onclick: () => inp.click() }, `Upload a ${label.toLowerCase()} just for this post`), inp);
  };
  const clientLogoUpload = () => {
    if (!p.client) return null;
    const inp = h('input', { type: 'file', accept: 'image/*', class: 'hidden' });
    inp.onchange = async () => {
      if (!inp.files.length) return;
      const fd = new FormData(); fd.append('file', inp.files[0]);
      try { await upload(`/api/clients/${p.client.id}/logo`, fd); toast(`Logo saved for ${p.client.name} and shared with their other posts.`, 'success'); editor(root, id); } catch (e) { showError(e); }
    };
    return h('span', null, h('button', { class: 'btn btn-link btn-sm', disabled: !editable, onclick: () => inp.click() }, `Upload the ${p.client.name} logo (shared)`), inp);
  };

  // ---- sliders
  const sliderRow = (label, min, max, step, value, onchange, fmt = (v) => v) => {
    const r = h('input', { type: 'range', min, max, step, value, disabled: !editable });
    const out = h('span', { class: 'small muted' }, fmt(value));
    r.oninput = () => { out.textContent = fmt(+r.value); onchange(+r.value); changed(); };
    return h('div', { class: 'slider-row' }, h('span', null, label), r, out);
  };
  const crop = draft.photo_crop || { x: 0, y: 0, w: 1, h: 1 };
  const cropState = { zoom: Math.round(1 / Math.min(crop.w || 1, crop.h || 1) * 100) / 100, ox: crop.w < 1 ? crop.x / (1 - crop.w) : 0.5, oy: crop.h < 1 ? crop.y / (1 - crop.h) : 0.5 };
  const applyCrop = () => {
    if (cropState.zoom <= 1.001) { draft.photo_crop = null; return; }
    const w = 1 / cropState.zoom, hh = 1 / cropState.zoom;
    draft.photo_crop = { x: (1 - w) * cropState.ox, y: (1 - hh) * cropState.oy, w, h: hh };
  };
  const lay = draft.layout || (draft.layout = {});
  const L = (sec, key, def) => (lay[sec] && lay[sec][key] !== undefined ? lay[sec][key] : settings.layout_defaults[sec][key] ?? def);
  const setL = (sec, key, v) => { lay[sec] = lay[sec] || {}; lay[sec][key] = v; };
  const logoMode = select([['contain', 'Fit inside the box'], ['cover', 'Fill the box (crops edges)']], L('logo', 'mode', 'contain'), { disabled: !editable });
  logoMode.onchange = () => { setL('logo', 'mode', logoMode.value); changed(); };

  // ---- actions
  const saveBtn = h('button', { class: 'btn btn-primary', disabled: true }, 'Save draft');
  saveBtn.onclick = async () => {
    saveBtn.disabled = true;
    try {
      const changes = {}; ['display_name', 'designation', 'client_display', 'pronoun', 'announcement', 'photo_id', 'photo_crop', 'logo_id', 'layout'].forEach((k) => { changes[k] = draft[k] ?? null; });
      await patch(`/api/posts/${id}`, { version: p.version, changes, render: true });
      toast('Draft saved' + (p.status === 'published' ? ' as a new version. The published picture is kept.' : '.'), 'success');
      editor(root, id);
    } catch (e) { showError(e); saveBtn.disabled = false; }
  };
  const act = (label, fn, kind = 'secondary', enabled = true) => h('button', { class: `btn btn-${kind} btn-sm`, disabled: !enabled, onclick: async () => { try { await fn(); } catch (e) { showError(e); } } }, label);
  const actions = h('div', { class: 'btn-row-wrap' },
    editable ? act('Regenerate', async () => { await post(`/api/posts/${id}/render`); toast('Post generated.', 'success'); editor(root, id); }, 'secondary', !p.missing.length || true) : null,
    act('Export PNG', () => download(`/api/posts/${id}/export`), 'secondary', p.has_render),
    act('Copy image', () => copyImage(id), 'secondary', p.has_render),
    act('Copy text', () => copyText(p)),
    editable && p.status !== 'published' ? act('Mark as published', async () => { await post(`/api/posts/${id}/publish`); toast('Marked as published. Later edits create a new draft version.', 'success'); editor(root, id); }, 'accent', p.has_render) : null,
    editable && p.status === 'published' ? act('Unpublish', async () => { if (await confirmDialog({ title: 'Unpublish', message: 'The post goes back to Ready. The published picture is kept in the version history.', confirmLabel: 'Unpublish' })) { await post(`/api/posts/${id}/unpublish`); editor(root, id); } }) : null,
    can('posts.email') ? act('Request by email', () => requestDialog(p, () => editor(root, id)), 'secondary', p.missing.some((m) => /logo|Designation/i.test(m))) : null,
    editable && p.review_flags.length ? act('Dismiss notes', async () => { await post(`/api/posts/${id}/dismiss-flags`); editor(root, id); }) : null,
    editable && p.status !== 'published' ? act('Delete', async () => { if (await confirmDialog({ title: 'Delete post project', message: 'Delete this post project and its versions? The employee record is not affected.', confirmLabel: 'Delete', danger: true })) { await del(`/api/posts/${id}`); location.hash = '#/posts'; } }, 'danger') : null);

  const versions = h('details', null, h('summary', null, `Versions (${p.versions_list.length})`),
    h('div', { class: 'table-wrap', style: { maxHeight: '260px', marginTop: '8px' } }, h('table', { class: 'table small' }, h('tbody', null, p.versions_list.map((v) => h('tr', null,
      h('td', null, v.has_file ? h('img', { class: 'thumb-sm', src: `/api/posts/${id}/versions/${v.id}/image`, style: { borderRadius: '4px', cursor: 'zoom-in' }, onclick: () => modal({ title: `Version ${v.version_no}`, wide: true, body: h('div', { class: 'lightbox' }, h('img', { src: `/api/posts/${id}/versions/${v.id}/image` })) }) }) : ''),
      h('td', null, `v${v.version_no} `, badge(v.kind, v.kind === 'published' ? 'green' : v.kind === 'legacy' ? 'teal' : 'blue')), h('td', { class: 'small' }, fmtDateTime(v.created_at), h('div', { class: 'muted' }, v.created_by)),
      h('td', { class: 'small muted' }, v.note || ''),
      h('td', null, editable ? h('button', { class: 'btn btn-link btn-sm', onclick: async () => { try { await post(`/api/posts/${id}/versions/${v.id}/restore`); toast('Version restored as the current draft.', 'success'); editor(root, id); } catch (e) { showError(e); } } }, 'Restore') : null)))))));

  const banners = [];
  if (p.missing.length) banners.push(h('div', { class: 'banner banner-warn' }, icon('alert'), h('div', null, h('strong', null, 'Cannot be generated yet. '), h('ul', { class: 'msgs' }, p.missing.map((m) => h('li', null, m))), h('div', { class: 'small' }, 'The post is kept and completes by itself as soon as the missing item is added.'))));
  if (p.review_flags.length) banners.push(h('div', { class: 'banner banner-info' }, icon('alert'), h('div', null, h('ul', { class: 'msgs' }, p.review_flags.map((f) => h('li', null, f))))));
  if (p.has_unpublished_changes) banners.push(h('div', { class: 'banner banner-info' }, icon('alert'), h('div', null, `This post was published as version ${p.published_version}. The current draft (v${p.draft_version}) has changes that are not published yet.`)));

  const summary = h('dl', { class: 'kv small' }, h('dt', null, 'Employee'), h('dd', null, h('a', { href: '#', onclick: (e) => { e.preventDefault(); openProfile(p.employee.id); } }, `${p.employee.full_name} (${p.employee.rhc_id})`), h('span', { class: 'muted' }, ` · record says: ${p.employee.designation || 'no designation'}, ${p.employee.client_name || 'no client'}`)),
    h('dt', null, 'Client'), h('dd', null, p.client ? h('a', { href: '#/posts/clients' }, p.client.name) : h('span', { class: 'muted' }, 'none')),
    h('dt', null, 'Status'), h('dd', null, badge(STATUS_LABEL[p.status] || p.status, { needs_assets: 'amber', ready: 'blue', published: 'green' }[p.status]), ` · ${p.project_uid} · draft v${p.draft_version}`, p.published_at ? ` · published ${fmtDateTime(p.published_at)} by ${p.published_by}` : ''),
    p.requests.length ? [h('dt', null, 'Email requests'), h('dd', null, p.requests.map((r) => h('div', null, badge(r.status), ` ${r.request_uid} to ${r.to_email || '?'} ${r.sent_at ? fmtDateTime(r.sent_at) : ''}`, r.send_error ? h('span', { style: { color: 'var(--red)' } }, ` ${r.send_error}`) : null)))] : null);

  mount(root, ...banners,
    h('div', { class: 'editor' },
      h('div', { class: 'preview-box' }, h('div', { class: 'card' }, img, pstatus, h('div', { style: { marginTop: '10px' } }, actions), h('div', { style: { marginTop: '10px' } }, saveBtn, ' ', h('span', { class: 'small muted' }, 'Save draft keeps every version and never changes the employee record.')))),
      h('div', null,
        h('div', { class: 'card' }, h('h3', null, 'About this post'), summary),
        h('div', { class: 'card' }, h('h3', null, 'Text'), h('div', { class: 'form-grid' }, textInput('display_name', 'Employee display name'), textInput('designation', 'Designation'), textInput('client_display', 'Client display name'), field('Pronoun in the announcement', pron)),
          field('Announcement text', ann, 'Placeholders: {name}, {client}, {pronoun}. Copy text puts this on the clipboard with the name in bold.')),
        h('div', { class: 'card' }, h('h3', null, 'Profile photo'), photoRow, h('div', { class: 'row', style: { marginTop: '6px' } }, assetUpload('photo', 'Photo'), srcNote('photo_id')),
          h('div', { style: { marginTop: '10px' } }, sliderRow('Zoom', 1, 3, 0.05, cropState.zoom, (v) => { cropState.zoom = v; applyCrop(); }, (v) => v.toFixed(2) + 'x'),
            sliderRow('Horizontal position', 0, 1, 0.01, cropState.ox, (v) => { cropState.ox = v; applyCrop(); }, (v) => Math.round(v * 100) + '%'),
            sliderRow('Vertical position', 0, 1, 0.01, cropState.oy, (v) => { cropState.oy = v; applyCrop(); }, (v) => Math.round(v * 100) + '%'),
            sliderRow('Circle size', 150, 260, 1, L('photo', 'size', 213), (v) => setL('photo', 'size', v), (v) => v + ' px'))),
        h('div', { class: 'card' }, h('h3', null, 'Client logo'), logoRow, h('div', { class: 'row', style: { marginTop: '6px' } }, clientLogoUpload(), assetUpload('logo', 'Logo'), srcNote('logo_id')),
          h('div', { style: { marginTop: '10px' } }, field('Fit', logoMode),
            sliderRow('Scale', 0.5, 1.5, 0.01, L('logo', 'scale', 1), (v) => setL('logo', 'scale', v), (v) => Math.round(v * 100) + '%'),
            sliderRow('Shift left / right', -60, 60, 1, L('logo', 'dx', 0), (v) => setL('logo', 'dx', v), (v) => v + ' px'),
            sliderRow('Shift up / down', -40, 40, 1, L('logo', 'dy', 0), (v) => setL('logo', 'dy', v), (v) => v + ' px'),
            sliderRow('Padding', 0, 30, 1, L('logo', 'padding', 8), (v) => setL('logo', 'padding', v), (v) => v + ' px'))),
        h('div', { class: 'card' }, h('h3', null, 'Text size and placement'),
          sliderRow('Name size (largest)', 14, 34, 1, L('name', 'max_size', 25), (v) => setL('name', 'max_size', v), (v) => v + ' pt'),
          sliderRow('Name vertical position', 240, 290, 1, L('name', 'y', 264), (v) => setL('name', 'y', v), (v) => v + ' px'),
          sliderRow('Designation size (largest)', 10, 26, 1, L('designation', 'max_size', 19), (v) => setL('designation', 'max_size', v), (v) => v + ' pt'),
          sliderRow('Designation vertical position', 270, 320, 1, L('designation', 'y', 288), (v) => setL('designation', 'y', v), (v) => v + ' px'),
          sliderRow('Letter spacing (designation)', 0, 4, 1, L('designation', 'spacing', 1), (v) => setL('designation', 'spacing', v), (v) => v + ' px'),
          h('p', { class: 'small muted' }, 'Text shrinks automatically to fit the template width. Defaults reproduce the original Post Studio layout.')),
        h('div', { class: 'card' }, versions))));
  refreshPreview();
}

// ---------------------------------------------------------------- email request dialog
async function requestDialog(p, done) {
  let prev;
  try { prev = await get(`/api/posts/${p.id}/request-preview`); } catch (e) { return showError(e); }
  const to = h('input', { class: 'input', type: 'email', value: prev.to });
  const cc = h('input', { class: 'input', value: (prev.cc || []).join('; ') });
  const subject = h('input', { class: 'input', value: prev.subject });
  const body = h('textarea', { class: 'input', rows: 12 }); body.value = prev.body;
  const dup = h('input', { type: 'checkbox' });
  const hasRecent = prev.warnings.some((w) => /already sent/.test(w));
  const send = async (mode) => {
    const payload = { to: to.value.trim(), cc: cc.value.split(/[;,]/).map((x) => x.trim()).filter(Boolean), subject: subject.value, body: body.value, asks: prev.asks, mode, confirm_duplicate: dup.checked };
    try {
      const r = await post(`/api/posts/${p.id}/request`, payload);
      toast(r.status === 'submitted' ? `Request sent to ${r.to_email}.` : r.status === 'draft' ? 'Saved as a draft in Outlook.' : r.status === 'uncertain' ? 'Outlook gave no clear result; check Sent Items before sending again.' : 'Sending failed: ' + (r.send_error || ''), r.status === 'failed' ? 'error' : r.status === 'uncertain' ? 'warn' : 'success');
      done();
    } catch (e) { if (e.code === 'request_exists') { toast(e.message, 'warn'); return false; } showError(e); return false; }
  };
  modal({ title: `Request ${prev.asks.join(' and ') || 'nothing'} by email`, wide: true,
    body: h('div', null,
      prev.warnings.length ? h('div', { class: 'banner banner-warn', style: { margin: '0 0 12px' } }, icon('alert'), h('ul', { class: 'msgs' }, prev.warnings.map((w) => h('li', null, w)))) : null,
      h('p', { class: 'small muted' }, 'Review the recipient and the text. Nothing is sent until you press Send. Replies are matched to this exact request by the reference in the subject.'),
      field('To', to), field('CC', cc), field('Subject', subject), field('Message', body),
      hasRecent ? h('label', { class: 'check' }, dup, h('strong', null, 'Send again as a reminder (a request was already sent).')) : null,
      prev.previous.length ? h('details', null, h('summary', null, `Earlier requests (${prev.previous.length})`), h('ul', { class: 'msgs' }, prev.previous.map((r) => h('li', null, `${r.request_uid}: ${r.status} ${r.sent_at ? fmtDateTime(r.sent_at) : ''} to ${r.to_email || ''} (${(r.asks || []).join(', ')})`)))) : null),
    actions: [{ label: 'Cancel' }, { label: 'Save as Outlook draft', onClick: () => send('draft') }, { label: 'Send', kind: 'primary', onClick: () => send('send'), busy: 'Sending…' }] });
}

// ---------------------------------------------------------------- clients
async function clientsView(root) {
  setPage('Posts');
  const q = h('input', { class: 'input', type: 'search', placeholder: 'Search clients', 'aria-label': 'Search clients' });
  const list = h('div');
  const load = async () => {
    const rows = await get('/api/clients?' + qs({ q: q.value }));
    mount(list, rows.length ? h('div', { class: 'table-wrap' }, h('table', { class: 'table' }, h('thead', null, h('tr', null, ['Logo', 'Client', 'Employees', 'Posts', ''].map((t) => h('th', null, t)))),
      h('tbody', null, rows.map((c) => h('tr', null,
        h('td', null, c.current_logo_id ? h('img', { class: 'thumb-sm', style: { borderRadius: '4px', width: '40px', height: '40px', cursor: 'zoom-in' }, src: `/api/logos/${c.current_logo_id}/thumb`, alt: '', onclick: () => modal({ title: c.name, body: h('div', { class: 'lightbox' }, h('img', { src: `/api/logos/${c.current_logo_id}/file` })) }) }) : h('span', { class: 'badge badge-amber' }, 'no logo')),
        h('td', null, h('strong', null, c.name)), h('td', null, c.employees), h('td', null, c.projects),
        h('td', { class: 'nowrap' }, can('posts.manage') ? [logoUploadBtn(c, load), h('button', { class: 'btn btn-link btn-sm', onclick: () => clientDialog(c, load) }, 'Details'),
          h('button', { class: 'btn btn-link btn-sm', onclick: async () => { const { promptDialog } = await import('../ui.js'); const name = await promptDialog({ title: 'Rename client', label: 'Client name', value: c.name, help: 'Use this for spelling corrections. Posts move with the client.' }); if (name && name !== c.name) { try { await patch(`/api/clients/${c.id}`, { name }); toast('Client renamed.', 'success'); load(); } catch (e) { showError(e); } } } }, 'Rename')] : h('button', { class: 'btn btn-link btn-sm', onclick: () => clientDialog(c, load) }, 'Details'))))))) :
      emptyState('No clients yet', 'Clients are created from employee client assignments and from logo uploads.'));
  };
  q.addEventListener('input', debounce(load, 300));
  const add = h('button', { class: 'btn btn-primary' }, icon('plus'), 'Add client');
  add.onclick = async () => { const { promptDialog } = await import('../ui.js'); const name = await promptDialog({ title: 'Add client', label: 'Client name', confirmLabel: 'Add' }); if (name) { try { await post('/api/clients', { name }); load(); } catch (e) { showError(e); } } };
  const sync = h('button', { class: 'btn btn-secondary' }, icon('refresh'), 'Create clients from employee records');
  sync.onclick = async () => { try { const r = await post('/api/clients/sync'); toast(`${r.created} clients created from employee assignments (no posts were created).`, 'success'); load(); } catch (e) { showError(e); } };
  mount(root, h('div', { class: 'card' }, h('div', { class: 'card-title' }, h('h3', null, 'Clients and logos'), h('div', { class: 'row' }, can('posts.manage') ? [sync, add] : null)),
    h('p', { class: 'muted small' }, 'One logo per client, shared by every post for that client. Every uploaded version is kept; the current one is replaced only after the new file is saved and checked.'),
    h('div', { class: 'search', style: { marginBottom: '10px' } }, icon('search'), q), list));
  load();
}

function logoUploadBtn(c, reload) {
  const inp = h('input', { type: 'file', accept: 'image/*', class: 'hidden' });
  inp.onchange = async () => {
    if (!inp.files.length) return;
    const fd = new FormData(); fd.append('file', inp.files[0]);
    try { await upload(`/api/clients/${c.id}/logo`, fd); toast(`Logo saved for ${c.name}. Waiting posts for this client are generated now.`, 'success'); reload(); } catch (e) { showError(e); }
  };
  return h('span', null, h('button', { class: 'btn btn-link btn-sm', onclick: () => inp.click() }, c.current_logo_id ? 'Replace logo' : 'Upload logo'), inp);
}

async function clientDialog(c, reload) {
  let d;
  try { d = await get(`/api/clients/${c.id}`); } catch (e) { return showError(e); }
  const body = h('div', null,
    h('h3', null, 'Logo versions'),
    d.logos.length ? h('div', { class: 'att-grid' }, d.logos.map((l) => h('div', { class: 'att' + (l.is_current ? ' chosen' : '') }, h('img', { src: `/api/logos/${l.id}/thumb`, alt: '' }),
      h('div', null, `v${l.version_no} `, l.is_current ? badge('Current', 'green') : null), h('div', { class: 'muted' }, `${l.width}×${l.height} · ${l.source || ''}`), h('div', { class: 'muted' }, fmtDateTime(l.uploaded_at)),
      !l.is_current && can('posts.manage') ? h('button', { class: 'btn btn-link btn-sm', onclick: async () => { try { await post(`/api/clients/${c.id}/logos/${l.id}/make-current`); toast('Logo version restored.', 'success'); m.close(); reload(); } catch (e) { showError(e); } } }, 'Use this one') : null))) : h('p', { class: 'muted' }, 'No logo yet.'),
    h('h3', { style: { marginTop: '14px' } }, 'Employees assigned to this client'),
    d.employees.length ? h('ul', { class: 'msgs' }, d.employees.map((e) => h('li', null, h('a', { href: '#', onclick: (ev) => { ev.preventDefault(); m.close(); openProfile(e.id); } }, `${e.full_name} (${e.rhc_id})`), h('span', { class: 'muted' }, ` · ${e.designation || 'no designation'}`)))) : h('p', { class: 'muted' }, 'None.'),
    h('p', { class: 'small muted', style: { marginTop: '10px' } }, 'Designations are never copied between people at the same client. Only the logo is shared.'));
  const m = modal({ title: c.name, wide: true, body, actions: [{ label: 'Close' }] });
}

// ---------------------------------------------------------------- requests and replies
async function requestsView(root) {
  setPage('Posts');
  const [reqs, replies] = await Promise.all([get('/api/post-requests'), get('/api/post-replies')]);
  const recheck = h('input', { type: 'checkbox' });
  const scanBtn = h('button', { class: 'btn btn-primary', disabled: !can('posts.email') }, icon('mail'), 'Check replies now');
  scanBtn.onclick = async () => {
    scanBtn.disabled = true; scanBtn.textContent = 'Reading Outlook…';
    try {
      const r = await post('/api/post-replies/scan', { recheck: recheck.checked });
      toast(`${r.scanned} messages looked at: ${r.new} new, ${r.applied} applied automatically, ${r.review} waiting for review, ${r.skipped} already handled.` + (r.errors.length ? ` ${r.errors.length} could not be processed.` : ''), r.errors.length ? 'warn' : 'success', 9000);
      requestsView(root);
    } catch (e) { showError(e); scanBtn.disabled = false; scanBtn.textContent = 'Check replies now'; }
  };
  const pending = replies.filter((r) => r.status === 'pending' || r.status === 'error');
  const handled = replies.filter((r) => !['pending', 'error'].includes(r.status));
  mount(root,
    h('div', { class: 'card' }, h('div', { class: 'card-title' }, h('h3', null, 'Replies'), h('div', { class: 'row' }, h('label', { class: 'check' }, recheck, 'Also re-check replies handled before'), scanBtn)),
      h('p', { class: 'muted small' }, 'Replies are matched to the exact request by the reference in the subject, then by the Outlook conversation, then by the sender. Clear matches with one usable logo are applied automatically; everything else is listed here for you to decide. Nothing is ever sent from this screen.'),
      pending.length ? h('div', null, pending.map((r) => replyCard(r, () => requestsView(root)))) : emptyState('No replies waiting', 'Press Check replies now to read Outlook.'),
      handled.length ? h('details', { style: { marginTop: '12px' } }, h('summary', null, `Handled replies (${handled.length})`), h('div', { class: 'table-wrap' }, h('table', { class: 'table small' }, h('tbody', null, handled.map((r) => h('tr', null, h('td', null, badge(r.status)), h('td', null, r.sender_email), h('td', null, r.subject), h('td', { class: 'nowrap' }, fmtDateTime(r.received_at)), h('td', null, r.full_name ? `${r.full_name} (${r.rhc_id})` : ''), h('td', { class: 'muted' }, (r.applied.notes || []).join(' ') || r.applied.note || r.error || ''))))))) : null),
    h('div', { class: 'card' }, h('h3', null, 'Requests sent'),
      reqs.length ? h('div', { class: 'table-wrap' }, h('table', { class: 'table' }, h('thead', null, h('tr', null, ['Reference', 'Employee', 'Client', 'Asked for', 'To', 'Status', 'When', ''].map((t) => h('th', null, t)))),
        h('tbody', null, reqs.map((r) => h('tr', null, h('td', { class: 'mono small' }, r.request_uid), h('td', null, h('a', { href: '#/posts/' + r.project_id }, `${r.full_name} (${r.rhc_id})`)), h('td', null, r.client_name || ''),
          h('td', null, (r.asks || []).join(', ')), h('td', { class: 'small' }, r.to_email), h('td', null, badge(r.status), r.send_error ? h('div', { class: 'small', style: { color: 'var(--red)' } }, r.send_error) : null, r.followup_error ? h('div', { class: 'small', style: { color: 'var(--amber)' } }, 'Sent, but a later step failed: ' + r.followup_error) : null),
          h('td', { class: 'small nowrap' }, fmtDateTime(r.sent_at || r.created_at)),
          h('td', null, can('posts.email') && ['uncertain', 'failed', 'sending'].includes(r.status) ? h('button', { class: 'btn btn-link btn-sm', onclick: () => markDialog(r, () => requestsView(root)) }, 'Record outcome') : null)))))) :
        emptyState('No requests yet', 'Open a post that is missing a logo or designation and use Request by email.')));
}

function replyCard(r, reload) {
  const cands = (r.applied && r.applied.candidates) || [];
  const chosen = { att: null, project: r.project_id || (cands.length === 1 ? cands[0].project_id : null) };
  const accepted = r.attachments.filter((a) => a.accepted);
  if (accepted.length === 1) chosen.att = r.attachments.indexOf(accepted[0]);
  const attCards = h('div', { class: 'att-grid' });
  const drawAtts = () => mount(attCards, r.attachments.length ? r.attachments.map((a, i) => h('div', { class: 'att' + (a.accepted ? '' : ' rejected') + (chosen.att === i ? ' chosen' : ''), onclick: () => { chosen.att = chosen.att === i ? null : i; drawAtts(); } },
    a.width ? h('img', { src: `/api/post-replies/${r.id}/attachments/${i}`, alt: '' }) : h('div', { class: 'muted', style: { height: '90px', display: 'flex', alignItems: 'center', justifyContent: 'center' } }, 'no preview'),
    h('div', null, h('strong', null, a.name)), h('div', { class: 'muted' }, `${a.width ? `${a.width}×${a.height} · ` : ''}${Math.round((a.size || 0) / 1024)} KB`),
    a.accepted ? h('div', { style: { color: 'var(--green)' } }, chosen.att === i ? 'Selected as the logo' : 'Usable as a logo (click to select)') : h('div', { class: 'why' }, a.reason, ' ', h('span', { class: 'muted' }, chosen.att === i ? '(selected anyway)' : '(click to choose it anyway)')))) : h('span', { class: 'muted small' }, 'No attachments.'));
  drawAtts();
  const desigBox = h('input', { type: 'checkbox', checked: !!r.designation });
  const desigText = h('input', { class: 'input', value: r.designation || '', placeholder: 'Designation found in the reply (edit if needed)' });
  const updEmp = h('input', { type: 'checkbox' });
  const projSel = cands.length ? select([['', 'Choose the post this reply belongs to…'], ...cands.map((c) => [c.project_id, c.label])], chosen.project || '') : null;
  if (projSel) projSel.onchange = () => { chosen.project = projSel.value ? +projSel.value : null; };
  const apply = h('button', { class: 'btn btn-primary btn-sm', disabled: !can('posts.email') }, 'Apply');
  apply.onclick = async () => {
    if (!chosen.project) return toast('Choose the post this reply belongs to.', 'error');
    if (chosen.att === null && !(desigBox.checked && desigText.value.trim())) return toast('Select a logo attachment or tick the designation.', 'error');
    try {
      const res = await post(`/api/post-replies/${r.id}/apply`, { project_id: chosen.project, attachment_index: chosen.att, designation: desigBox.checked, designation_text: desigText.value, update_employee: updEmp.checked, force: true });
      toast((res.applied.notes || []).join(' ') || 'Applied.', 'success', 8000); reload();
    } catch (e) { showError(e); }
  };
  const ignore = h('button', { class: 'btn btn-secondary btn-sm', disabled: !can('posts.email'), onclick: async () => { try { await post(`/api/post-replies/${r.id}/ignore`, { note: 'ignored by operator' }); reload(); } catch (e) { showError(e); } } }, 'Ignore');
  return h('div', { class: 'card', style: { marginTop: '10px' } },
    h('div', { class: 'row-between' }, h('div', null, h('strong', null, r.sender_name || r.sender_email), h('span', { class: 'muted' }, ` <${r.sender_email}> · ${fmtDateTime(r.received_at)}`), h('div', null, r.subject)),
      r.status === 'error' ? badge('Could not be processed', 'red') : r.match_certain ? badge('Matched: ' + r.match_method, 'green') : badge(r.match_method ? 'Uncertain: ' + r.match_method : 'Not matched', 'amber')),
    r.error ? h('div', { class: 'banner banner-warn', style: { margin: '8px 0' } }, icon('alert'), h('div', null, r.error, ' ', h('span', { class: 'small' }, 'Press Check replies now with re-check ticked to retry.'))) : null,
    r.full_name ? h('div', { class: 'small' }, 'Post: ', h('a', { href: '#/posts/' + r.project_id }, `${r.full_name} (${r.rhc_id}) · ${r.project_uid}`)) : projSel ? field('Which post?', projSel) : h('div', { class: 'small', style: { color: 'var(--amber)' } }, 'No open post could be linked to this sender. You can still apply it by opening the post and uploading the logo there.'),
    h('details', null, h('summary', null, 'Message text'), h('pre', { class: 'email' }, r.body_excerpt || '(empty)')),
    h('div', { style: { margin: '10px 0 6px', fontWeight: 600 } }, 'Attachments'), attCards,
    h('div', { class: 'row', style: { marginTop: '10px' } }, h('label', { class: 'check' }, desigBox, 'Use this designation for the post:'), h('div', { style: { width: '320px' } }, desigText)),
    h('label', { class: 'check small' }, updEmp, 'Also update the designation on the employee record (only this employee, never others at the same client)'),
    h('div', { class: 'row', style: { marginTop: '8px' } }, apply, ignore));
}

function markDialog(r, reload) {
  const note = h('textarea', { class: 'input', rows: 3, placeholder: 'What did you find in Outlook (Sent Items, Outbox, Drafts)?' });
  modal({ title: `Record the outcome of ${r.request_uid}`, body: h('div', null, h('p', null, 'The email was not confirmed as sent. Check Outlook, then record what happened. Nothing is resent automatically.'), field('What you checked', note)),
    actions: [{ label: 'Cancel' }, { label: 'It was NOT sent', kind: 'secondary', onClick: async () => { await post(`/api/post-requests/${r.id}/mark`, { status: 'failed', note: note.value }); reload(); } },
      { label: 'It was sent', kind: 'primary', onClick: async () => { await post(`/api/post-requests/${r.id}/mark`, { status: 'submitted', note: note.value }); reload(); } }] });
}

// ---------------------------------------------------------------- backfill
async function backfillView(root) {
  setPage('Posts');
  const d = await get('/api/posts/backfill');
  const boxes = d.candidates.map((c) => ({ c, cb: h('input', { type: 'checkbox' }) }));
  const btn = h('button', { class: 'btn btn-primary', disabled: !can('posts.manage') || !boxes.length }, 'Create posts for ticked employees');
  btn.onclick = async () => {
    const ids = boxes.filter((b) => b.cb.checked).map((b) => b.c.id);
    if (!ids.length) return toast('Tick at least one employee.', 'warn');
    const ok = await confirmDialog({ title: 'Create post projects', message: `Create ${ids.length} post projects for existing client assignments? Posts whose photo, logo or designation is missing stay in Needs attention until the item is added. No emails are sent.`, confirmLabel: 'Create' });
    if (!ok) return;
    try { const r = await post('/api/posts/backfill', { employee_ids: ids }); toast(`${r.created} post projects created.`, 'success'); location.hash = '#/posts'; } catch (e) { showError(e); }
  };
  const all = h('input', { type: 'checkbox' }); all.onchange = () => boxes.forEach((b) => { b.cb.checked = all.checked; });
  mount(root, h('div', { class: 'card' }, h('div', { class: 'card-title' }, h('h3', null, 'Client assignments without a post'), btn),
    h('p', { class: 'muted small' }, 'Imported or historical assignments do not create posts automatically. Choose the ones that should get a deployment post.'),
    boxes.length ? h('div', { class: 'table-wrap' }, h('table', { class: 'table' }, h('thead', null, h('tr', null, h('th', null, all), ['RHC ID', 'Employee', 'Client', 'Start', 'Designation', 'Photo', 'Logo'].map((t) => h('th', null, t)))),
      h('tbody', null, boxes.map(({ c, cb }) => h('tr', null, h('td', null, cb), h('td', null, c.rhc_id), h('td', null, c.full_name), h('td', null, c.client_name), h('td', null, c.assignment_start || ''), h('td', null, c.designation || h('span', { class: 'muted' }, 'missing')),
        h('td', null, c.current_photo_id ? 'yes' : h('span', { class: 'muted' }, 'missing')), h('td', null, c.has_logo ? 'yes' : h('span', { class: 'muted' }, 'missing'))))))) :
      emptyState('Nothing to backfill', 'Every current client assignment already has a post project.')));
}
