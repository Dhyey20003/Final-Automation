// Employees: directory grid, inline editing, paste preview, add/edit form, profile with photo and history.
import { get, post, patch, qs, download, upload } from '../api.js';
import { h, mount, clear, icon, toast, showError, modal, confirmDialog, spinner, emptyState, fmtDate, fmtDateTime, fmtBytes,
  badge, field, select, tabs, store, debounce } from '../ui.js';
import { DataGrid } from '../grid.js';
import { state, can, setPage } from '../app.js';

const VIEWS = [['active', 'Active'], ['pending', 'Pending review'], ['left', 'Left'], ['directory', 'All current records'],
  ['historical', 'Historical only'], ['archived', 'Archived']];
const DEFAULT_COLS = ['photo', 'rhc_id', 'full_name', 'designation', 'department', 'official_email', 'location', 'date_of_joining',
  'employment_status', 'assignment_status', 'client_name', 'bench_days'];

let ui = null;

export async function render(root) {
  const meta = state.meta;
  const saved = store('emp.view.' + state.user.id) || {};
  ui = {
    root, meta, q: '', view: saved.view || 'active', page: 1, pageSize: saved.pageSize || 50,
    sort: saved.sort || 'full_name', dir: saved.dir || 'asc', filters: {},
    visible: saved.visible || DEFAULT_COLS.filter((k) => meta.fields.some((f) => f.key === k)),
    widths: saved.widths || {},
  };
  const actions = [];
  if (can('employees.export')) actions.push(h('button', { class: 'btn btn-secondary', onclick: exportView }, icon('download'), 'Export to Excel'));
  if (can('employees.create')) actions.push(h('button', { class: 'btn btn-primary', onclick: () => openForm(null) }, icon('plus'), 'Add employee'));
  setPage('Employees', actions);

  const search = h('input', { class: 'input', type: 'search', placeholder: 'Search name, RHC ID or email', 'aria-label': 'Search employees' });
  search.addEventListener('input', debounce(() => { ui.q = search.value; ui.page = 1; load(); }, 300));
  ui.chips = h('div', { class: 'chips', role: 'group', 'aria-label': 'Which records' });
  renderChips();
  ui.countEl = h('span', { class: 'muted' });
  const toolbar = h('div', { class: 'grid-toolbar' },
    h('div', { class: 'search' }, icon('search'), search), ui.chips, h('div', { class: 'grow' }),
    h('button', { class: 'btn btn-secondary btn-sm', onclick: () => { ui.filters = {}; ui.grid.filters = {}; ui.grid.render(); ui.page = 1; load(); } }, 'Clear filters'),
    h('button', { class: 'btn btn-secondary btn-sm', onclick: chooseColumns }, icon('columns'), 'Columns'));

  ui.grid = new DataGrid({
    columns: buildColumns(), sort: ui.sort, dir: ui.dir, widths: ui.widths,
    onSort: (s, d) => { ui.sort = s; ui.dir = d; save(); load(); },
    onFilter: (f) => { ui.filters = f; ui.page = 1; load(); },
    onEdit: inlineEdit, onPaste: pasteCells, onOpen: (row) => openProfile(row.id),
    onWidths: (w) => { ui.widths = w; save(); },
  });
  ui.status = h('div');
  ui.pager = h('div', { class: 'pager' });
  mount(root, toolbar, ui.status, ui.grid.wrap, ui.pager,
    h('p', { class: 'muted small', style: { marginTop: '8px' } },
      'Tip: click a cell and press Enter to edit, or paste several rows copied from Excel with Ctrl+V. Every paste is previewed before saving.'));
  await load();
}

function save() {
  store('emp.view.' + state.user.id, { view: ui.view, pageSize: ui.pageSize, sort: ui.sort, dir: ui.dir, visible: ui.visible, widths: ui.widths });
}

function renderChips() {
  mount(ui.chips, VIEWS.map(([k, label]) => h('button', { class: 'chip' + (ui.view === k ? ' active' : ''), onclick: () => { ui.view = k; ui.page = 1; save(); renderChips(); load(); } }, label)));
}

function buildColumns() {
  const byKey = Object.fromEntries(ui.meta.fields.map((f) => [f.key, f]));
  const order = [...ui.visible, ...ui.meta.fields.map((f) => f.key).filter((k) => !ui.visible.includes(k))];
  return order.filter((k) => byKey[k]).map((k) => {
    const f = byKey[k];
    const col = { key: k, label: f.label, type: f.type, options: f.options, help: f.help, visible: ui.visible.includes(k),
      editable: !!f.can_edit && can('employees.edit'), width: k === 'full_name' ? 190 : k === 'official_email' ? 220 : k === 'photo' ? 54 : 140 };
    if (f.type === 'date' && !f.masked) col.format = fmtDate;
    if (k === 'photo') {
      Object.assign(col, { key: 'photo_id', label: '', sortable: false, filterable: false, editable: false,
        render: (row) => row.photo_id ? h('img', { class: 'thumb-sm', src: `/api/photos/${row.photo_id}/thumb`, alt: '' }) : h('span', { class: 'no-photo', title: 'No photo' }) });
    }
    if (k === 'full_name') {
      col.sticky = false;
      col.render = (row) => h('a', { href: '#', onclick: (e) => { e.preventDefault(); openProfile(row.id); } }, row.full_name || '(no name)');
      col.cls = 'name-cell';
      col.editable = false;
    }
    if (k === 'employment_status' || k === 'assignment_status') col.render = (row) => row[k] ? badge(row[k]) : document.createTextNode('');
    if (['created_at', 'updated_at'].includes(k)) col.format = fmtDateTime;
    return col;
  });
}

async function load() {
  mount(ui.status, spinner('Loading employees…'));
  try {
    const data = await get('/api/employees?' + qs({ q: ui.q, view: ui.view, sort: ui.sort, dir: ui.dir, page: ui.page, page_size: ui.pageSize,
      filters: Object.keys(ui.filters).length ? ui.filters : '' }));
    clear(ui.status);
    ui.data = data;
    ui.grid.setRows(data.rows);
    if (!data.rows.length) {
      const hasFilters = ui.q || Object.values(ui.filters).some((v) => v);
      mount(ui.status, emptyState(hasFilters ? 'No employees match these filters' : 'No employees here yet',
        hasFilters ? 'Try a different search or clear the filters.' : 'Add employees one by one, or import an HR spreadsheet from the Import Data section.',
        !hasFilters && can('imports.run') ? h('a', { class: 'btn btn-primary', href: '#/imports' }, 'Import a spreadsheet') : null));
    }
    renderPager();
  } catch (e) { mount(ui.status, h('div', { class: 'card' }, h('p', null, e.message), h('button', { class: 'btn btn-secondary', onclick: load }, 'Retry'))); }
}

function renderPager() {
  const { total, page, page_size } = ui.data;
  const pages = Math.max(1, Math.ceil(total / page_size));
  const size = select([25, 50, 100, 200], ui.pageSize, { style: { width: 'auto' }, 'aria-label': 'Rows per page' });
  size.onchange = () => { ui.pageSize = +size.value; ui.page = 1; save(); load(); };
  mount(ui.pager,
    h('div', null, `${total} ${total === 1 ? 'record' : 'records'}`),
    h('div', { class: 'row' }, 'Rows per page', size,
      h('button', { class: 'btn btn-secondary btn-sm', disabled: page <= 1, onclick: () => { ui.page--; load(); } }, 'Previous'),
      h('span', null, `Page ${page} of ${pages}`),
      h('button', { class: 'btn btn-secondary btn-sm', disabled: page >= pages, onclick: () => { ui.page++; load(); } }, 'Next')));
}

function chooseColumns() {
  const boxes = ui.meta.fields.map((f) => {
    const cb = h('input', { type: 'checkbox', checked: ui.visible.includes(f.key) });
    return { f, cb, el: h('label', { class: 'check' }, cb, h('span', null, f.label || 'Photo', f.custom ? h('span', { class: 'muted small' }, ' (custom)') : null)) };
  });
  modal({
    title: 'Choose columns', body: h('div', { class: 'grid-3' }, boxes.map((b) => b.el)),
    actions: [
      { label: 'Reset to default', onClick: () => { ui.visible = DEFAULT_COLS.filter((k) => ui.meta.fields.some((f) => f.key === k)); apply(); } },
      { label: 'Cancel' },
      { label: 'Apply', kind: 'primary', onClick: () => { ui.visible = boxes.filter((b) => b.cb.checked).map((b) => b.f.key); apply(); } },
    ],
  });
  function apply() { save(); ui.grid.setColumns(buildColumns()); }
}

async function exportView() {
  const cols = ui.visible.filter((k) => k !== 'photo');
  try {
    toast('Preparing the Excel file…');
    await download('/api/employees/export?' + qs({ q: ui.q, view: ui.view, sort: ui.sort, dir: ui.dir, columns: cols.join(','),
      filters: Object.keys(ui.filters).length ? ui.filters : '' }), 'Employees.xlsx');
  } catch (e) { showError(e); }
}

// ---------------------------------------------------------------- inline editing
async function inlineEdit(row, col, value) {
  try {
    const res = await patch(`/api/employees/${row.id}`, { version: row.version, changes: { [col.key]: value } });
    Object.assign(row, res.employee);
    if (res.warnings && res.warnings.length) toast(res.warnings.join('\n'), 'warn');
    else toast(`Saved ${col.label}.`, 'success', 2000);
  } catch (e) {
    if (e.status === 409) return conflict(row, { [col.key]: value }, e);
    showError(e);
    throw e;
  }
}

function conflict(row, mine, err) {
  const current = (err.details && err.details.current) || {};
  const byKey = Object.fromEntries(ui.meta.fields.map((f) => [f.key, f]));
  const rows = Object.entries(mine).map(([k, v]) => h('tr', null, h('td', null, byKey[k] ? byKey[k].label : k),
    h('td', null, String(row[k] ?? '')), h('td', null, String(current[k] ?? '')), h('td', null, String(v ?? ''))));
  modal({
    title: 'Someone else changed this record', wide: true,
    body: h('div', null, h('p', null, `${row.full_name} (${row.rhc_id}) was changed by another user after you loaded it. Nothing has been overwritten.`),
      h('div', { class: 'table-wrap' }, h('table', { class: 'table' }, h('thead', null, h('tr', null, h('th', null, 'Field'), h('th', null, 'When you loaded it'), h('th', null, 'Now saved'), h('th', null, 'Your change'))), h('tbody', null, rows)))),
    actions: [
      { label: 'Keep the saved version', onClick: () => { Object.assign(row, current); ui.grid.render(); } },
      { label: 'Apply my change on top', kind: 'primary', onClick: async () => {
        const res = await patch(`/api/employees/${row.id}`, { version: current.version, changes: mine });
        Object.assign(row, res.employee); ui.grid.render(); toast('Saved.', 'success');
      } },
    ],
  });
}

// ---------------------------------------------------------------- paste
async function pasteCells(r0, c0, matrix) {
  if (!can('employees.edit')) return toast('You do not have permission to edit.', 'error');
  const cols = ui.grid.visibleColumns();
  const updates = []; const skipped = [];
  matrix.forEach((vals, i) => {
    const row = ui.grid.rows[r0 + i];
    if (!row) { skipped.push(`Row ${i + 1} of the pasted data is beyond the rows on this page.`); return; }
    const changes = {};
    vals.forEach((v, j) => {
      const col = cols[c0 + j];
      if (!col) return;
      if (!col.editable) { skipped.push(`${col.label || 'Photo'} cannot be edited by pasting.`); return; }
      changes[col.key] = v.trim() === '' ? null : v.trim();
    });
    if (Object.keys(changes).length) updates.push({ id: row.id, version: row.version, changes, _row: row });
  });
  if (!updates.length) return toast('Nothing to paste into editable cells here.', 'warn');
  const loading = modal({ title: 'Checking pasted values', body: spinner('Validating…'), actions: [] });
  let res;
  try { res = await post('/api/employees/bulk-preview', { updates: updates.map(({ _row, ...u }) => u) }); }
  catch (e) { loading.close(); return showError(e); }
  loading.close();
  const byKey = Object.fromEntries(ui.meta.fields.map((f) => [f.key, f]));
  const body = h('div', null,
    h('p', null, res.ok ? 'Review the changes below. They are saved together, or not at all.' : 'Some rows have problems. Fix the data and paste again; nothing has been saved.'),
    [...new Set(skipped)].length ? h('ul', { class: 'msgs warn' }, [...new Set(skipped)].map((s) => h('li', null, s))) : null,
    h('div', { class: 'table-wrap', style: { maxHeight: '55vh' } }, h('table', { class: 'table' },
      h('thead', null, h('tr', null, h('th', null, 'Employee'), h('th', null, 'Changes'), h('th', null, 'Problems'))),
      h('tbody', null, res.results.map((r, i) => h('tr', null,
        h('td', null, `${updates[i]._row.full_name} (${updates[i]._row.rhc_id})`),
        h('td', null, r.changes.length ? h('ul', { class: 'change-list' }, r.changes.map((c) => h('li', null, `${byKey[c.field] ? byKey[c.field].label : c.field}: `,
          h('span', { class: 'old' }, c.old ?? '(blank)'), ' → ', h('span', { class: 'new' }, c.new ?? '(blank)')))) : h('span', { class: 'muted' }, 'No change')),
        h('td', null, Object.keys(r.errors).length ? h('ul', { class: 'msgs err' }, Object.entries(r.errors).map(([k, v]) => h('li', null, `${byKey[k] ? byKey[k].label + ': ' : ''}${v}`))) :
          r.warnings.length ? h('ul', { class: 'msgs warn' }, r.warnings.map((w) => h('li', null, w))) : '')))))));
  modal({
    title: `Paste preview (${updates.length} rows)`, body, wide: true,
    actions: res.ok ? [{ label: 'Cancel' }, { label: 'Save all changes', kind: 'primary', onClick: async () => {
      const out = await post('/api/employees/bulk-update', { updates: updates.map(({ _row, ...u }) => u) });
      toast(`Saved ${out.fields_changed} changes in ${out.rows} rows.`, 'success'); load();
    } }] : [{ label: 'Close' }],
  });
}

// ---------------------------------------------------------------- add / edit form
export function inputFor(f, value) {
  let el;
  if (f.masked) return h('input', { class: 'input', value: value ?? '', disabled: true, title: 'You can see day and month only.' });
  if (f.type === 'dropdown') el = select([['', '(not set)'], ...f.options.map((o) => [o, o])], value ?? '');
  else if (f.type === 'yesno') el = select([['', '(not set)'], ['Yes', 'Yes'], ['No', 'No']], value ?? '');
  else if (f.type === 'date') el = h('input', { class: 'input', type: 'date', value: value ?? '' });
  else if (f.type === 'number') el = h('input', { class: 'input', type: 'number', step: 'any', value: value ?? '' });
  else if (f.type === 'longtext') { el = h('textarea', { class: 'input', rows: 3 }); el.value = value ?? ''; }
  else el = h('input', { class: 'input', type: f.type === 'email' ? 'email' : 'text', value: value ?? '' });
  return el;
}

function openForm(emp) {
  const fields = ui.meta.fields.filter((f) => f.can_edit && f.editable);
  const inputs = {};
  const groups = {};
  fields.forEach((f) => { (groups[f.group] = groups[f.group] || []).push(f); });
  const grid = h('div', { class: 'form-grid' });
  Object.entries(groups).forEach(([g, list]) => {
    grid.appendChild(h('div', { class: 'form-section' }, g));
    list.forEach((f) => {
      const val = emp ? emp[f.key] : (f.key === 'employment_status' ? 'Active' : f.key === 'assignment_status' ? 'Unknown' : '');
      inputs[f.key] = inputFor(f, val);
      const wrap = field(f.label, inputs[f.key], f.help, f.required);
      if (f.type === 'longtext') wrap.classList.add('span-2');
      grid.appendChild(wrap);
    });
  });
  const errBox = h('div');
  modal({
    title: emp ? `Edit ${emp.full_name}` : 'Add employee', wide: true, body: h('div', null, errBox, grid),
    actions: [{ label: 'Cancel' }, { label: emp ? 'Save changes' : 'Add employee', kind: 'primary', onClick: async () => {
      const values = {};
      Object.entries(inputs).forEach(([k, el]) => {
        const v = el.value.trim ? el.value.trim() : el.value;
        if (emp) { if (String(v ?? '') !== String(emp[k] ?? '')) values[k] = v === '' ? null : v; }
        else if (v !== '') values[k] = v;
      });
      try {
        let res;
        if (emp) {
          if (!Object.keys(values).length) { toast('No changes to save.'); return true; }
          res = await patch(`/api/employees/${emp.id}`, { version: emp.version, changes: values });
        } else res = await post('/api/employees', { values });
        toast(emp ? 'Changes saved.' : 'Employee added.', 'success');
        if (res.post && res.post.outcome === 'created') toast('A deployment post project was created. Find it under Posts.', 'success', 7000);
        else if (res.post && res.post.outcome === 'renamed') toast('The client name correction was applied to the existing post project.', 'success', 7000);
        if (res.warnings && res.warnings.length) toast(res.warnings.join('\n'), 'warn');
        load();
        if (emp) openProfile(emp.id);
        return true;
      } catch (e) {
        if (e.status === 409) { conflict(emp, values, e); return true; }
        const fe = (e.details && e.details.fields) || {};
        mount(errBox, h('div', { class: 'banner banner-warn', style: { margin: '0 0 12px' } }, icon('alert'), h('div', null, e.message,
          Object.keys(fe).length ? h('ul', { class: 'msgs' }, Object.entries(fe).map(([k, v]) => h('li', null, v))) : null)));
        return false;
      }
    } }],
  });
}

// ---------------------------------------------------------------- profile
let drawer = null;
export async function openProfile(id) {
  if (drawer) drawer.remove();
  const body = h('div', { class: 'drawer-body' }, spinner());
  const head = h('div', { class: 'drawer-head' });
  const panel = h('div', { class: 'drawer', role: 'dialog', 'aria-label': 'Employee profile' }, head, body);
  const overlay = h('div', { class: 'drawer-overlay', onclick: () => close() });
  drawer = h('div', null, overlay, panel);
  document.body.appendChild(drawer);
  const onKey = (e) => { if (e.key === 'Escape' && !document.querySelector('.modal-overlay')) close(); };
  document.addEventListener('keydown', onKey);
  function close() { if (drawer) drawer.remove(); drawer = null; document.removeEventListener('keydown', onKey); }
  let data;
  try { data = await get(`/api/employees/${id}`); } catch (e) { mount(body, h('p', null, e.message)); return; }
  const emp = data.employee;
  const photo = emp.photo_id ? h('img', { class: 'profile-photo', src: `/api/photos/${emp.photo_id}/thumb`, alt: `Photo of ${emp.full_name}`, onclick: () => lightbox(`/api/photos/${emp.photo_id}/file`, emp.full_name) })
    : h('div', { class: 'profile-photo empty' }, 'No photo');
  const fileInput = h('input', { type: 'file', accept: 'image/*', class: 'hidden' });
  fileInput.onchange = async () => {
    if (!fileInput.files.length) return;
    const fd = new FormData(); fd.append('file', fileInput.files[0]);
    try { await upload(`/api/employees/${id}/photo`, fd); toast('Photo replaced. The previous version is kept in the history.', 'success'); openProfile(id); load(); }
    catch (e) { showError(e); }
  };
  const actions = h('div', { class: 'row' },
    can('employees.edit') ? h('button', { class: 'btn btn-primary btn-sm', onclick: () => { close(); openForm(emp); } }, 'Edit') : null,
    can('photos.manage') ? h('button', { class: 'btn btn-secondary btn-sm', onclick: () => fileInput.click() }, emp.photo_id ? 'Replace photo' : 'Add photo') : null,
    emp.photo_id ? h('button', { class: 'btn btn-secondary btn-sm', onclick: () => lightbox(`/api/photos/${emp.photo_id}/file`, emp.full_name) }, 'View full size') : null,
    can('employees.archive') ? (emp.archived
      ? h('button', { class: 'btn btn-secondary btn-sm', onclick: () => archive(emp, false) }, 'Restore from archive')
      : h('button', { class: 'btn btn-secondary btn-sm', onclick: () => archive(emp, true) }, 'Archive')) : null,
    fileInput);
  mount(head, photo, h('div', { class: 'grow' },
    h('h2', null, emp.full_name), h('div', { class: 'muted' }, `${emp.rhc_id} · Internal ID ${emp.id}`),
    h('div', { class: 'row', style: { marginTop: '6px' } }, emp.employment_status ? badge(emp.employment_status) : null, emp.assignment_status ? badge(emp.assignment_status) : null,
      emp.record_scope === 'historical' ? badge('Historical only', 'amber') : null, emp.archived ? badge('Archived', 'grey') : null),
    h('div', { style: { marginTop: '10px' } }, actions)),
  h('button', { class: 'icon-btn', 'aria-label': 'Close', onclick: close }, icon('close')));

  let tab = 'details';
  const tabBody = h('div');
  const tabBar = h('div');
  const drawTabs = () => {
    const tabList = [['details', 'Details'], ['assignment', 'Assignment history'], ['changes', 'Change history'], ['photos', 'Photo versions']];
    if (can('posts.view')) tabList.push(['posts', 'Posts']);
    mount(tabBar, tabs(tabList, tab, (k) => { tab = k; drawTabs(); }));
    mount(tabBody, renderTab(tab, data));
  };
  mount(body, data.warnings.length ? h('div', { class: 'banner banner-warn', style: { margin: '0 0 12px' } }, icon('alert'), h('ul', { class: 'msgs' }, data.warnings.map((w) => h('li', null, w)))) : null, tabBar, tabBody);
  drawTabs();

  async function archive(e, on) {
    const ok = await confirmDialog({ title: on ? 'Archive employee' : 'Restore employee', confirmLabel: on ? 'Archive' : 'Restore',
      message: on ? `${e.full_name} will be hidden from the directory. All history and photos are kept and the record can be restored.` : `${e.full_name} will return to the directory.` });
    if (!ok) return;
    try { await post(`/api/employees/${e.id}/${on ? 'archive' : 'unarchive'}`, { version: e.version }); toast(on ? 'Archived.' : 'Restored.', 'success'); openProfile(e.id); load(); }
    catch (err) { showError(err); }
  }
}

function renderTab(tab, data) {
  const emp = data.employee;
  const byKey = Object.fromEntries(state.meta.fields.map((f) => [f.key, f]));
  if (tab === 'details') {
    const groups = {};
    state.meta.fields.forEach((f) => { if (f.key !== 'photo') (groups[f.group] = groups[f.group] || []).push(f); });
    return h('div', null, Object.entries(groups).map(([g, list]) => h('div', { style: { marginBottom: '16px' } }, h('h3', null, g),
      h('dl', { class: 'kv' }, list.map((f) => [h('dt', null, f.label), h('dd', null, display(f, emp[f.key]))])))));
  }
  if (tab === 'assignment') {
    if (!data.assignment_history.length) return emptyState('No earlier assignments', 'Previous client or bench periods appear here when the assignment changes.');
    return h('div', { class: 'table-wrap' }, h('table', { class: 'table' }, h('thead', null, h('tr', null, ['Status', 'Client', 'Assignment', 'Bench', 'Recorded'].map((t) => h('th', null, t)))),
      h('tbody', null, data.assignment_history.map((a) => h('tr', null, h('td', null, a.assignment_status || ''), h('td', null, a.client_name || ''),
        h('td', null, `${fmtDate(a.assignment_start)} to ${fmtDate(a.assignment_end) || 'open'}`), h('td', null, a.bench_start ? `${fmtDate(a.bench_start)} to ${fmtDate(a.bench_end) || 'open'}` : ''),
        h('td', { class: 'small' }, fmtDateTime(a.recorded_at), h('div', { class: 'muted' }, a.recorded_by)))))));
  }
  if (tab === 'posts') {
    const box = h('div', null, spinner());
    get('/api/posts?employee_id=' + emp.id).then((d) => mount(box, d.projects.length ? h('div', { class: 'table-wrap' }, h('table', { class: 'table' }, h('thead', null, h('tr', null, ['Client', 'Status', 'Updated', ''].map((t) => h('th', null, t)))),
      h('tbody', null, d.projects.map((p) => h('tr', null, h('td', null, p.data.client_display || (p.client && p.client.name) || ''), h('td', null, badge({ needs_assets: 'Needs attention', ready: 'Ready', published: 'Published' }[p.status] || p.status, { needs_assets: 'amber', ready: 'blue', published: 'green' }[p.status]), p.missing.length ? h('div', { class: 'small muted' }, p.missing.map((m) => m.split('.')[0]).join(', ')) : null),
        h('td', { class: 'small' }, fmtDateTime(p.updated_at)), h('td', null, h('a', { class: 'btn btn-link btn-sm', href: '#/posts/' + p.id, onclick: () => { if (drawer) drawer.remove(); drawer = null; } }, 'Open'))))))) :
      emptyState('No deployment posts', 'Saving a client assignment on this record creates a post project automatically.'))).catch((e) => mount(box, h('p', null, e.message)));
    return box;
  }
  if (tab === 'changes') {
    if (!data.history.length) return emptyState('No changes recorded');
    return h('div', { class: 'table-wrap' }, h('table', { class: 'table' }, h('thead', null, h('tr', null, ['When', 'Who', 'Field', 'Change', 'Source'].map((t) => h('th', null, t)))),
      h('tbody', null, data.history.map((c) => h('tr', null, h('td', { class: 'nowrap small' }, fmtDateTime(c.at)), h('td', { class: 'small' }, c.by),
        h('td', null, c.field === '__created__' ? 'Record created' : (byKey[c.field] ? byKey[c.field].label : c.field)),
        h('td', null, c.field === '__created__' ? '' : [h('span', { class: 'old' }, fmtVal(c.old)), ' → ', h('span', { class: 'new' }, fmtVal(c.new))]),
        h('td', { class: 'small muted' }, c.source + (c.batch ? ` #${c.batch}` : '')))))));
  }
  if (!data.photos.length) return emptyState('No photos yet', can('photos.manage') ? 'Use Add photo above, or upload photos in the Photos section.' : '');
  return h('div', { class: 'table-wrap' }, h('table', { class: 'table' }, h('thead', null, h('tr', null, ['', 'Version', 'Original filename', 'Size', 'Uploaded', 'Source'].map((t) => h('th', null, t)))),
    h('tbody', null, data.photos.map((p) => h('tr', null,
      h('td', null, h('img', { class: 'thumb-sm', src: `/api/photos/${p.id}/thumb`, alt: '', style: { cursor: 'zoom-in' }, onclick: () => lightbox(`/api/photos/${p.id}/file`, p.original_filename) })),
      h('td', null, `v${p.version_no} `, p.is_current ? badge('Current', 'green') : ''), h('td', { class: 'small' }, p.original_filename || ''),
      h('td', { class: 'small nowrap' }, `${p.width}×${p.height}, ${fmtBytes(p.bytes)}`),
      h('td', { class: 'small' }, fmtDateTime(p.uploaded_at), h('div', { class: 'muted' }, p.uploaded_by)), h('td', { class: 'small muted' }, p.source || ''))))));
}

function fmtVal(v) { if (v === null || v === undefined || v === '') return '(blank)'; if (/^\d{4}-\d{2}-\d{2}$/.test(v)) return fmtDate(v); return String(v); }

function display(f, v) {
  if (f.key === 'bench_days') return v === null || v === undefined ? h('span', { class: 'muted' }, 'Not on bench') : `${v} days`;
  if (v === null || v === undefined || v === '') return h('span', { class: 'muted' }, 'Not set');
  if (f.type === 'date' && !f.masked) return fmtDate(v);
  if (f.type === 'meta' && /T/.test(v)) return fmtDateTime(v);
  if (f.key === 'employment_status' || f.key === 'assignment_status') return badge(v);
  return String(v);
}

export function lightbox(src, title) {
  modal({ title: title || 'Photo', wide: true, body: h('div', { class: 'lightbox' }, h('img', { src, alt: title || '' })) });
}
