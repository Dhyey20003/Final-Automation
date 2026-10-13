// Photos: upload (files, folders, ZIPs, PDFs), review and confirm matches, sequence mapping, PDF crop, missing photos.
import { get, post, upload } from '../api.js';
import { h, mount, clear, icon, toast, showError, modal, confirmDialog, spinner, emptyState, fmtDateTime, badge, field, tabs, debounce } from '../ui.js';
import { can, setPage } from '../app.js';
import { lightbox, openProfile } from './employees.js';

let P = null;

export async function render(root, rest) {
  setPage('Photos');
  const tab = rest[0] === 'missing' ? 'missing' : 'review';
  const body = h('div');
  const bar = tabs([['review', 'Upload and review'], ['missing', 'Missing photos']].filter(([k]) => k === 'missing' || can('photos.manage')),
    can('photos.manage') ? tab : 'missing', (k) => { location.hash = k === 'missing' ? '#/photos/missing' : '#/photos'; });
  mount(root, bar, body);
  if (tab === 'missing' || !can('photos.manage')) return renderMissing(body);
  P = { root: body, batchId: rest[0] && /^\d+$/.test(rest[0]) ? +rest[0] : null, filter: 'pending', chosen: new Map(), checked: new Set() };
  if (P.batchId) return openBatch(P.batchId);
  return renderBatches();
}

async function renderBatches() {
  mount(P.root, spinner());
  let batches;
  try { batches = await get('/api/photo-batches'); } catch (e) { return mount(P.root, h('p', null, e.message)); }
  const start = h('button', { class: 'btn btn-primary' }, icon('upload'), 'Start a new photo upload');
  start.onclick = async () => { try { const r = await post('/api/photo-batches', { label: 'Photo upload' }); location.hash = `#/photos/${r.id}`; } catch (e) { showError(e); } };
  mount(P.root,
    h('div', { class: 'card' }, h('div', { class: 'card-title' }, h('h3', null, 'Photo uploads in progress'), start),
      h('p', { class: 'muted' }, 'Upload single photos, many photos, a whole folder, ZIP files or PDFs. Nothing is assigned until you confirm it on the review screen. Facial recognition is never used.'),
      batches.length ? h('div', { class: 'table-wrap' }, h('table', { class: 'table' }, h('thead', null, h('tr', null, ['#', 'Started', 'By', 'Files', 'Waiting for review', ''].map((t) => h('th', null, t)))),
        h('tbody', null, batches.map((b) => h('tr', null, h('td', null, b.id), h('td', null, fmtDateTime(b.created_at)), h('td', null, b.created_by), h('td', null, b.n), h('td', null, b.pending),
          h('td', null, h('a', { class: 'btn btn-secondary btn-sm', href: `#/photos/${b.id}` }, 'Open'))))))) :
        emptyState('No uploads in progress', 'Start a new upload to add or replace employee photos.')));
}

async function openBatch(id) {
  mount(P.root, spinner('Loading upload…'));
  try { P.data = await get(`/api/photo-batches/${id}`); } catch (e) { return mount(P.root, h('p', null, e.message)); }
  P.data.candidates.forEach((c) => {
    if (c.status === 'pending' && c.suggestion && !P.chosen.has(c.id)) {
      P.chosen.set(c.id, { id: c.suggestion.employee_id, label: `${c.suggestion.name} (${c.suggestion.rhc_id})`, has_photo: c.suggestion.has_photo });
      if (c.suggestion.certain) P.checked.add(c.id);
    }
  });
  drawBatch();
}

function drawBatch() {
  const d = P.data;
  const input = h('input', { type: 'file', multiple: true, accept: 'image/*,.zip,.pdf', class: 'hidden' });
  const folder = h('input', { type: 'file', multiple: true, webkitdirectory: true, class: 'hidden' });
  const progress = h('div', { class: 'progress hidden' }, h('div'));
  const send = async (files) => {
    if (!files.length) return;
    const fd = new FormData();
    [...files].forEach((f) => fd.append('files', f, f.webkitRelativePath || f.name));
    progress.classList.remove('hidden');
    try {
      await upload(`/api/photo-batches/${d.batch.id}/files`, fd, (x) => { progress.firstChild.style.width = (x * 100) + '%'; });
      toast(`${files.length} ${files.length === 1 ? 'file' : 'files'} added. Check the matches below before assigning.`, 'success');
      openBatch(d.batch.id);
    } catch (e) { showError(e); progress.classList.add('hidden'); }
  };
  input.onchange = () => send(input.files);
  folder.onchange = () => send(folder.files);
  const zone = h('div', { class: 'dropzone' }, h('strong', null, 'Drag photos, a folder\'s files, ZIPs or PDFs here'), ' or ',
    h('button', { class: 'btn btn-secondary btn-sm', onclick: () => input.click() }, 'Choose files'), ' ',
    h('button', { class: 'btn btn-secondary btn-sm', onclick: () => folder.click() }, 'Choose a folder'),
    h('div', { class: 'small', style: { marginTop: '6px' } }, 'Photos whose filename contains an RHC ID (for example RHC1234.jpg) are pre-selected. File or page order is never assumed to match employees.'), progress, input, folder);
  zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('over'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('over'));
  zone.addEventListener('drop', (e) => { e.preventDefault(); zone.classList.remove('over'); send(e.dataTransfer.files); });

  const c = d.counts;
  const summary = h('div', { class: 'banner banner-info', style: { margin: '12px 0 0' } }, icon('photo'), h('div', null,
    h('strong', null, 'Upload summary: '), `${d.candidates.length} files · ${c.suggested_certain || 0} matched clearly (pre-ticked) · ${c.suggested_uncertain || 0} suggestions to confirm · ${c.unmatched || 0} with no match · ${c.assigned || 0} assigned · ${c.error || 0} problems. `,
    h('span', { class: 'small' }, 'A filename that is exactly one employee\'s first and last name (for example "John Smith.jpg" for John Michael Smith) is matched; a name that fits several people is left for you to choose.')));
  const filters = [['pending', `Waiting for review (${c.pending || 0})`], ['certain', `Pre-selected (${c.suggested_certain || 0})`], ['uncertain', `Suggestions to confirm (${c.suggested_uncertain || 0})`],
    ['unmatched', `No match (${c.unmatched || 0})`], ['assigned', `Assigned (${c.assigned || 0})`], ['skipped', `Skipped (${c.skipped || 0})`], ['error', `Problems (${c.error || 0})`], ['all', 'All']];
  const list = visible();
  const readyCount = [...P.checked].filter((id) => P.chosen.has(id) && d.candidates.find((x) => x.id === id && x.status === 'pending')).length;
  const assignBtn = h('button', { class: 'btn btn-primary', disabled: !readyCount }, `Assign ${readyCount} ticked photo${readyCount === 1 ? '' : 's'}`);
  assignBtn.onclick = () => assignTicked();
  const toolbar = h('div', { class: 'row', style: { margin: '12px 0' } },
    h('button', { class: 'btn btn-secondary btn-sm', onclick: () => { list.filter((x) => x.status === 'pending' && P.chosen.has(x.id)).forEach((x) => P.checked.add(x.id)); drawBatch(); } }, 'Tick all shown with an employee'),
    h('button', { class: 'btn btn-secondary btn-sm', onclick: () => { P.checked.clear(); drawBatch(); } }, 'Untick all'),
    h('button', { class: 'btn btn-secondary btn-sm', onclick: skipTicked }, 'Skip ticked'),
    h('button', { class: 'btn btn-secondary btn-sm', onclick: mappingDialog }, 'Paste a filename to RHC ID list'),
    h('button', { class: 'btn btn-secondary btn-sm', onclick: sequenceDialog }, 'Sequence mapping'),
    h('div', { class: 'grow' }), assignBtn);

  mount(P.root,
    h('div', { class: 'row-between', style: { marginBottom: '10px' } }, h('div', null, h('a', { href: '#/photos' }, '← All uploads'), h('span', { class: 'muted' }, `  Upload #${d.batch.id} started ${fmtDateTime(d.batch.created_at)} by ${d.batch.created_by}`)),
      h('button', { class: 'btn btn-secondary btn-sm', onclick: closeBatch }, 'Finish and close this upload')),
    zone,
    d.candidates.length ? summary : null,
    d.pdfs.length ? pdfSummary(d.pdfs) : null,
    h('div', { class: 'chips', style: { marginTop: '14px' } }, filters.map(([k, l]) => h('button', { class: 'chip' + (P.filter === k ? ' active' : ''), onclick: () => { P.filter = k; drawBatch(); } }, l))),
    toolbar,
    list.length ? h('div', { class: 'cand-grid' }, list.map(card)) : emptyState(d.candidates.length ? 'Nothing in this group' : 'No files yet', d.candidates.length ? '' : 'Add files above to start.'));
}

function visible() {
  const all = P.data.candidates;
  switch (P.filter) {
    case 'pending': return all.filter((c) => c.status === 'pending');
    case 'certain': return all.filter((c) => c.status === 'pending' && c.suggestion && c.suggestion.certain);
    case 'uncertain': return all.filter((c) => c.status === 'pending' && c.suggestion && !c.suggestion.certain);
    case 'unmatched': return all.filter((c) => c.status === 'pending' && !c.suggestion);
    case 'all': return all;
    default: return all.filter((c) => c.status === P.filter);
  }
}

function card(c) {
  const bid = P.data.batch.id;
  const img = c.has_image ? h('img', { src: `/api/photo-batches/${bid}/candidates/${c.id}/thumb`, alt: c.original_name || '', loading: 'lazy' }) : h('span', { class: 'muted small' }, 'No image');
  const imgBox = h('div', { class: 'cand-img', onclick: () => c.has_image && lightbox(`/api/photo-batches/${bid}/candidates/${c.id}/image`, c.original_name) }, img, h('span', { class: 'cand-seq' }, '#' + c.seq));
  const parts = [h('div', { class: 'cand-name' }, c.original_name || '(unnamed)'), h('div', { class: 'muted' }, c.source_ref || '')];
  if (c.width) parts.push(h('div', { class: 'muted' }, `${c.width}×${c.height} ${c.format || ''}`));
  if (c.context_text) parts.push(h('div', { class: 'small', title: 'Text found near this image in the PDF' }, 'PDF text: ', h('em', null, c.context_text.slice(0, 120))));
  if (c.flags && c.flags.length) parts.push(h('ul', { class: 'flags' }, c.flags.map((f) => h('li', null, f))));
  if (c.status === 'error') parts.push(h('div', { style: { color: 'var(--red)' } }, c.message));
  if (c.status === 'assigned' && c.assigned) parts.push(h('div', null, badge('Assigned', 'green'), ' ', h('a', { href: '#', onclick: (e) => { e.preventDefault(); openProfile(c.assigned.employee_id); } }, `${c.assigned.name} (${c.assigned.rhc_id})`)));
  if (c.status === 'skipped') parts.push(h('div', null, badge('Skipped'), ' ', h('button', { class: 'btn btn-link btn-sm', onclick: () => setStatus([c.id], true) }, 'Review again')));
  let checked = false;
  if (c.status === 'pending') {
    if (c.suggestion) parts.push(h('div', null, c.suggestion.certain ? badge('Pre-selected', 'green') : badge('Suggestion: please confirm', 'amber'),
      h('div', { class: 'muted' }, c.suggestion.method)));
    const chosen = P.chosen.get(c.id);
    const picker = employeePicker(chosen, (emp) => {
      if (emp) { P.chosen.set(c.id, emp); P.checked.add(c.id); } else { P.chosen.delete(c.id); P.checked.delete(c.id); }
      drawBatch();
    });
    parts.push(picker);
    if (chosen && chosen.has_photo) parts.push(h('div', { class: 'small muted' }, 'This employee already has a photo; it will be replaced and the old one kept in history.'));
    checked = P.checked.has(c.id);
    const cb = h('input', { type: 'checkbox', checked, disabled: !chosen });
    cb.onchange = () => { if (cb.checked) P.checked.add(c.id); else P.checked.delete(c.id); drawBatch(); };
    parts.push(h('div', { class: 'row' }, h('label', { class: 'check' }, cb, 'Assign this photo'), h('div', { class: 'grow' }),
      c.has_image ? h('button', { class: 'btn btn-link btn-sm', onclick: () => cropDialog(c) }, 'Crop') : null,
      h('button', { class: 'btn btn-link btn-sm', onclick: () => setStatus([c.id], false) }, 'Skip')));
  }
  return h('div', { class: 'cand' + (checked ? ' checked' : '') }, imgBox, h('div', { class: 'cand-body' }, parts));
}

// Simple crop: draw a rectangle on the image, the cropped PNG is added to the upload as a new file.
function cropDialog(c) {
  const bid = P.data.batch.id;
  const img = h('img', { src: `/api/photo-batches/${bid}/candidates/${c.id}/image`, alt: c.original_name, draggable: 'false' });
  const rect = h('div', { class: 'crop-rect hidden' });
  const stage = h('div', { class: 'crop-stage' }, img, rect);
  let box = null, start = null;
  const pos = (e) => { const r = img.getBoundingClientRect(); return [Math.max(0, Math.min(r.width, e.clientX - r.left)), Math.max(0, Math.min(r.height, e.clientY - r.top))]; };
  stage.addEventListener('mousedown', (e) => { e.preventDefault(); start = pos(e); box = null; rect.classList.add('hidden'); });
  stage.addEventListener('mousemove', (e) => {
    if (!start) return;
    const [x, y] = pos(e); const side = Math.max(Math.abs(x - start[0]), Math.abs(y - start[1]));
    box = { x: Math.min(start[0], x), y: Math.min(start[1], y), w: side, h: side };
    Object.assign(rect.style, { left: box.x + 'px', top: box.y + 'px', width: box.w + 'px', height: box.h + 'px' }); rect.classList.remove('hidden');
  });
  document.addEventListener('mouseup', () => { start = null; }, { once: false });
  modal({ title: `Crop ${c.original_name}`, wide: true, body: h('div', null, h('p', { class: 'small muted' }, 'Drag a square over the face. The crop is added as a new file in this upload; the original stays untouched.'), stage),
    actions: [{ label: 'Cancel' }, { label: 'Add cropped photo', kind: 'primary', onClick: async () => {
      if (!box || box.w < 10) { toast('Drag a square on the picture first.', 'error'); return false; }
      const scale = img.naturalWidth / img.getBoundingClientRect().width;
      const canvas = document.createElement('canvas'); const side = Math.round(box.w * scale);
      canvas.width = side; canvas.height = side;
      canvas.getContext('2d').drawImage(img, Math.round(box.x * scale), Math.round(box.y * scale), side, side, 0, 0, side, side);
      const blob = await new Promise((res) => canvas.toBlob(res, 'image/png'));
      const fd = new FormData(); fd.append('files', blob, c.original_name.replace(/\.[^.]+$/, '') + ' (cropped).png');
      await upload(`/api/photo-batches/${bid}/files`, fd);
      toast('Cropped photo added to this upload.', 'success'); openBatch(bid);
    } }] });
}

export function employeePicker(chosen, onPick) {
  const input = h('input', { class: 'input', placeholder: 'Type a name or RHC ID', value: chosen ? chosen.label : '', 'aria-label': 'Choose employee' });
  const listEl = h('div', { class: 'picker-list hidden', role: 'listbox' });
  let items = []; let hl = -1;
  const search = debounce(async () => {
    const q = input.value.trim();
    if (!q) { listEl.classList.add('hidden'); if (chosen) onPick(null); return; }
    try { items = await get('/api/employees/lookup?q=' + encodeURIComponent(q)); } catch (_) { items = []; }
    hl = -1;
    mount(listEl, items.length ? items.map((e, i) => h('div', { class: 'picker-item', role: 'option', onmousedown: (ev) => { ev.preventDefault(); pick(i); } },
      `${e.full_name} (${e.rhc_id})`, h('span', { class: 'muted' }, ` ${e.location || ''}${e.employment_status !== 'Active' ? ' · ' + e.employment_status : ''}`))) : h('div', { class: 'picker-item muted' }, 'No matching employee'));
    listEl.classList.remove('hidden');
  }, 200);
  function pick(i) { const e = items[i]; if (!e) return; listEl.classList.add('hidden'); onPick({ id: e.id, label: `${e.full_name} (${e.rhc_id})`, has_photo: !!e.current_photo_id }); }
  input.addEventListener('input', search);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') { hl = Math.min(items.length - 1, hl + 1); } else if (e.key === 'ArrowUp') { hl = Math.max(0, hl - 1); }
    else if (e.key === 'Enter') { e.preventDefault(); pick(hl < 0 ? 0 : hl); return; } else return;
    listEl.querySelectorAll('.picker-item').forEach((el, i) => el.classList.toggle('hl', i === hl));
  });
  input.addEventListener('blur', () => setTimeout(() => listEl.classList.add('hidden'), 150));
  return h('div', { class: 'picker' }, input, listEl);
}

async function assignTicked() {
  const items = [...P.checked].filter((id) => P.chosen.has(id)).map((id) => ({ candidate_id: id, employee_id: P.chosen.get(id).id }));
  const dupEmp = {};
  items.forEach((i) => { dupEmp[i.employee_id] = (dupEmp[i.employee_id] || 0) + 1; });
  if (Object.values(dupEmp).some((n) => n > 1)) return toast('Two or more ticked photos are assigned to the same employee. Keep only one.', 'error');
  const uncertain = items.filter((i) => { const c = P.data.candidates.find((x) => x.id === i.candidate_id); return !(c.suggestion && c.suggestion.certain && c.suggestion.employee_id === i.employee_id); }).length;
  const ok = await confirmDialog({ title: 'Assign photos', confirmLabel: 'Assign',
    message: `Assign ${items.length} photos to the selected employees?${uncertain ? ` ${uncertain} of them were chosen or confirmed by you rather than matched by RHC ID.` : ''} Existing photos are kept as earlier versions.` });
  if (!ok) return;
  try {
    const res = await post(`/api/photo-batches/${P.data.batch.id}/assign`, { assignments: items });
    res.assigned.forEach((a) => { P.checked.delete(a.candidate_id); P.chosen.delete(a.candidate_id); });
    toast(`${res.assigned.length} photos assigned.` + (res.problems.length ? `\n${res.problems.length} could not be assigned: ${res.problems.map((p) => p.reason).join('; ')}` : ''), res.problems.length ? 'warn' : 'success');
    openBatch(P.data.batch.id);
  } catch (e) { showError(e); }
}

async function skipTicked() {
  const ids = [...P.checked];
  if (!ids.length) return toast('Tick the photos to skip first.', 'warn');
  setStatus(ids, false);
}

async function setStatus(ids, undo) {
  try { await post(`/api/photo-batches/${P.data.batch.id}/skip`, { ids, undo }); ids.forEach((i) => P.checked.delete(i)); openBatch(P.data.batch.id); }
  catch (e) { showError(e); }
}

function mappingDialog() {
  const ta = h('textarea', { class: 'input mono', rows: 10, placeholder: 'IMG_0457.jpg, RHC1234\nIMG_0458.jpg, RHC1235' });
  modal({ title: 'Map filenames to RHC IDs', wide: true,
    body: h('div', null, h('p', null, 'Paste two columns copied from Excel: the filename and the RHC ID. Matching files are pre-selected for review.'), ta),
    actions: [{ label: 'Cancel' }, { label: 'Apply mapping', kind: 'primary', onClick: async () => {
      const res = await post(`/api/photo-batches/${P.data.batch.id}/mapping`, { text: ta.value });
      toast(`${res.applied} files mapped.` + (res.problems.length ? '\n' + res.problems.slice(0, 8).join('\n') : ''), res.problems.length ? 'warn' : 'success');
      P.chosen.clear(); P.checked.clear(); openBatch(P.data.batch.id);
    } }] });
}

function sequenceDialog() {
  const pending = P.data.candidates.filter((c) => c.status === 'pending' && c.has_image);
  if (!pending.length) return toast('There are no pending images to map.', 'warn');
  const order = pending.map((c) => c.id);
  const listBox = h('div');
  const ta = h('textarea', { class: 'input mono', rows: 12, placeholder: 'One RHC ID per line, in the same order as the images' });
  const drawList = () => mount(listBox, h('div', { class: 'table-wrap', style: { maxHeight: '320px' } }, h('table', { class: 'table small' }, h('tbody', null, order.map((id, i) => {
    const c = pending.find((x) => x.id === id);
    return h('tr', null, h('td', null, i + 1), h('td', null, h('img', { class: 'thumb-sm', src: `/api/photo-batches/${P.data.batch.id}/candidates/${id}/thumb`, alt: '' })),
      h('td', null, c.original_name), h('td', { class: 'nowrap' },
        h('button', { class: 'btn btn-link btn-sm', disabled: i === 0, onclick: () => { [order[i - 1], order[i]] = [order[i], order[i - 1]]; drawList(); } }, '↑'),
        h('button', { class: 'btn btn-link btn-sm', disabled: i === order.length - 1, onclick: () => { [order[i + 1], order[i]] = [order[i], order[i + 1]]; drawList(); } }, '↓'),
        h('button', { class: 'btn btn-link btn-sm', onclick: () => { order.splice(i, 1); drawList(); } }, 'Remove')));
  })))));
  drawList();
  modal({ title: 'Sequence mapping', wide: 'xl',
    body: h('div', null, h('p', null, 'Use this only when you have an explicit list of employees in the same order as these images. Reorder or remove images on the left, paste the ordered RHC IDs on the right, then check the side-by-side preview.'),
      h('div', { class: 'grid-2' }, h('div', null, h('h3', null, 'Images in order'), listBox), h('div', null, h('h3', null, 'Employees in order'), ta))),
    actions: [{ label: 'Cancel' }, { label: 'Preview pairs', kind: 'primary', close: false, onClick: async (m) => {
      const prev = await post(`/api/photo-batches/${P.data.batch.id}/sequence-preview`, { candidate_ids: order, rhc_list: ta.value });
      m.close();
      sequencePreview(prev, order, ta.value);
    } }] });
}

function sequencePreview(prev, order, rhcText) {
  const bid = P.data.batch.id;
  const body = h('div', null,
    prev.problems.length ? h('div', { class: 'banner banner-warn', style: { margin: '0 0 12px' } }, icon('alert'), h('ul', { class: 'msgs' }, prev.problems.map((p) => h('li', null, p)))) :
      h('p', null, 'Check every pair. Assign only if each photo is the person next to it.'),
    h('div', { class: 'table-wrap', style: { maxHeight: '60vh' } }, h('table', { class: 'table' }, h('thead', null, h('tr', null, ['#', 'Photo', 'File', 'Employee'].map((t) => h('th', null, t)))),
      h('tbody', null, prev.pairs.map((p) => h('tr', null, h('td', null, p.position),
        h('td', null, p.candidate_id ? h('img', { src: `/api/photo-batches/${bid}/candidates/${p.candidate_id}/thumb`, alt: '', style: { height: '72px', borderRadius: '6px' } }) : '-'),
        h('td', { class: 'small' }, p.original_name || '-'), h('td', null, p.employee_name ? `${p.employee_name} (${p.rhc_id})` : h('span', { style: { color: 'var(--red)' } }, p.rhc_id ? `${p.rhc_id} not found` : '-'))))))));
  const confirmBox = h('input', { type: 'checkbox' });
  if (prev.ok) body.appendChild(h('label', { class: 'check', style: { marginTop: '12px' } }, confirmBox, h('strong', null, 'I have checked every pair above.')));
  modal({ title: 'Sequence preview', wide: true, body, actions: prev.ok ? [{ label: 'Back' , onClick: () => sequenceDialog() }, { label: 'Assign all pairs', kind: 'primary', onClick: async () => {
    if (!confirmBox.checked) { toast('Tick the confirmation after checking every pair.', 'error'); return false; }
    const res = await post(`/api/photo-batches/${bid}/sequence-assign`, { candidate_ids: order, rhc_list: rhcText, confirm: true });
    toast(`${res.assigned.length} photos assigned by confirmed sequence.` + (res.problems.length ? ` ${res.problems.length} problems.` : ''), res.problems.length ? 'warn' : 'success');
    P.chosen.clear(); P.checked.clear(); openBatch(bid);
  } }] : [{ label: 'Close' }] });
}

async function closeBatch() {
  const pending = P.data.candidates.filter((c) => c.status === 'pending').length;
  const ok = await confirmDialog({ title: 'Finish this upload', confirmLabel: 'Finish and close',
    message: pending ? `${pending} files are still waiting for review and will be discarded. Assigned photos are kept.` : 'Staged copies of the uploaded files will be removed. Assigned photos are kept.', danger: pending > 0 });
  if (!ok) return;
  try { await post(`/api/photo-batches/${P.data.batch.id}/close`); toast('Upload closed.', 'success'); location.hash = '#/photos'; } catch (e) { showError(e); }
}

// ---------------------------------------------------------------- PDF tools
function pdfSummary(pdfs) {
  return h('div', { class: 'card', style: { marginTop: '12px' } }, h('h3', null, 'PDF files'),
    pdfs.map((p) => {
      const flagged = p.pages.filter((pg) => pg.flags.length);
      return h('div', { class: 'row-between', style: { padding: '6px 0', borderBottom: '1px solid var(--border)' } },
        h('div', null, icon('file'), ' ', h('strong', null, p.name), h('span', { class: 'muted' }, ` · ${p.page_count} pages · ${p.pages.reduce((a, b) => a + b.image_count, 0)} images extracted`),
          flagged.length ? h('div', { class: 'small', style: { color: 'var(--amber)' } }, `${flagged.length} pages need attention (scanned layout, no text or unsupported images). Use the page viewer to crop photos manually.`) : null),
        h('button', { class: 'btn btn-secondary btn-sm', onclick: () => pdfViewer(p, 1) }, icon('crop'), 'Open page viewer'));
    }));
}

function pdfViewer(pdf, pageNo) {
  const bid = P.data.batch.id;
  let rect = null;
  const pageInfo = pdf.pages[pageNo - 1];
  const img = h('img', { src: `/api/photo-batches/${bid}/pdfs/${pdf.id}/pages/${pageNo}`, alt: `Page ${pageNo}`, draggable: 'false' });
  const stage = h('div', { class: 'pdf-stage' }, img);
  const boxEl = h('div', { class: 'pdf-rect hidden' });
  stage.appendChild(boxEl);
  P.data.candidates.filter((c) => c.pdf_id === pdf.id && c.page_no === pageNo && c.bbox).forEach((c) => {
    const [x0, y0, x1, y1] = c.bbox;
    stage.appendChild(h('div', { class: 'pdf-box', title: `#${c.seq} ${c.original_name}`, style: { left: x0 * 100 + '%', top: y0 * 100 + '%', width: (x1 - x0) * 100 + '%', height: (y1 - y0) * 100 + '%' } }));
  });
  let start = null;
  stage.addEventListener('mousedown', (e) => {
    const r = stage.getBoundingClientRect();
    start = [(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height];
    rect = null; boxEl.classList.remove('hidden');
    document.addEventListener('mouseup', () => { start = null; }, { once: true });
    Object.assign(boxEl.style, { left: start[0] * 100 + '%', top: start[1] * 100 + '%', width: '0', height: '0' });
  });
  stage.addEventListener('mousemove', (e) => {
    if (!start) return;
    const r = stage.getBoundingClientRect();
    const cur = [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))];
    rect = [Math.min(start[0], cur[0]), Math.min(start[1], cur[1]), Math.max(start[0], cur[0]), Math.max(start[1], cur[1])];
    Object.assign(boxEl.style, { left: rect[0] * 100 + '%', top: rect[1] * 100 + '%', width: (rect[2] - rect[0]) * 100 + '%', height: (rect[3] - rect[1]) * 100 + '%' });
  });
  const pages = h('div', { class: 'page-list' }, pdf.pages.map((pg) => h('button', { class: 'btn btn-sm ' + (pg.page_no === pageNo ? 'btn-primary' : 'btn-secondary'), onclick: () => { m.close(); pdfViewer(pdf, pg.page_no); } },
    `Page ${pg.page_no}`, pg.flags.length ? ' ⚠' : '', h('span', { class: 'muted small' }, ` ${pg.image_count} img`))));
  const m = modal({ title: `${pdf.name}: page ${pageNo}`, wide: 'xl',
    body: h('div', { class: 'pdf-layout' }, pages, h('div', null,
      pageInfo.flags.length ? h('ul', { class: 'msgs warn' }, pageInfo.flags.map((f) => h('li', null, f))) : null,
      h('p', { class: 'muted small' }, 'Dashed boxes show images already extracted. Drag a rectangle around a photo to crop it manually, then add it to the review list.'),
      stage,
      pageInfo.text_excerpt ? h('details', { style: { marginTop: '10px' } }, h('summary', null, 'Text on this page'), h('pre', { class: 'email small' }, pageInfo.text_excerpt)) : null)),
    actions: [{ label: 'Close' }, { label: 'Crop selection and add to review', kind: 'primary', close: false, onClick: async () => {
      if (!rect) { toast('Drag a rectangle on the page first.', 'warn'); return false; }
      await post(`/api/photo-batches/${bid}/pdfs/${pdf.id}/crop`, { page_no: pageNo, rect });
      toast('Cropped photo added to the review list. Choose the employee on its card.', 'success');
      rect = null; boxEl.classList.add('hidden');
      const d = await get(`/api/photo-batches/${bid}`); P.data = d; drawBatch();
      return false;
    } }] });
}

// ---------------------------------------------------------------- missing photos
async function renderMissing(root) {
  mount(root, spinner());
  let rows;
  try { rows = await get('/api/photos/missing'); } catch (e) { return mount(root, h('p', null, e.message)); }
  if (!rows.length) return mount(root, h('div', { class: 'card' }, emptyState('Every active employee has a photo', 'Employees without a photo are listed here and are reported as omissions when packages are prepared.')));
  mount(root, h('div', { class: 'card' }, h('div', { class: 'card-title' }, h('h3', null, `${rows.length} active employees have no photo`)),
    h('div', { class: 'table-wrap' }, h('table', { class: 'table' }, h('thead', null, h('tr', null, ['RHC ID', 'Name', 'Location', 'Designation', ''].map((t) => h('th', null, t)))),
      h('tbody', null, rows.map((r) => {
        const inp = h('input', { type: 'file', accept: 'image/*', class: 'hidden' });
        inp.onchange = async () => {
          if (!inp.files.length) return;
          const fd = new FormData(); fd.append('file', inp.files[0]);
          try { await upload(`/api/employees/${r.id}/photo`, fd); toast(`Photo added for ${r.full_name}.`, 'success'); renderMissing(root); } catch (e) { showError(e); }
        };
        return h('tr', null, h('td', null, r.rhc_id), h('td', null, h('a', { href: '#', onclick: (e) => { e.preventDefault(); openProfile(r.id); } }, r.full_name)), h('td', null, r.location || h('span', { class: 'muted' }, 'Not set')),
          h('td', null, r.designation || ''), h('td', null, can('photos.manage') ? [h('button', { class: 'btn btn-secondary btn-sm', onclick: () => inp.click() }, 'Upload photo'), inp] : null));
      }))))));
}
