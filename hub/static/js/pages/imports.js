// Import Data: upload, purpose, mapping template, preview, resolve, commit, results, history and undo.
import { get, post, del, upload, download, qs } from '../api.js';
import { h, mount, clear, icon, toast, showError, modal, confirmDialog, spinner, emptyState, fmtDate, fmtDateTime, badge, field, select, tabs } from '../ui.js';
import { state, can, setPage } from '../app.js';

const PURPOSE_HELP = {
  historical: 'Past or archived HR data. New people are stored outside the active directory. Existing current employees only get blank fields filled in.',
  roster: 'The full list of current employees for the chosen locations. It decides who is active. Anyone missing is shown for your confirmation before being marked Left.',
  partial: 'Updates some employees. Missing employees are never deactivated. Blank cells are ignored unless you choose otherwise.',
  designation: 'Only updates designations, matched by RHC ID.',
  selected: 'Only updates the fields you choose, matched by RHC ID.',
};
const STATUS_LABEL = { new: 'New', historical_new: 'New (historical)', update: 'Update', promote: 'Becomes current', enrich: 'Fill blanks',
  unchanged: 'Unchanged', error: 'Error', duplicate: 'Duplicate', unmatched: 'Not matched', outside_scope: 'Outside scope', skipped: 'Skipped' };
const STATUS_COLOR = { new: 'green', historical_new: 'teal', update: 'blue', promote: 'purple', enrich: 'teal', unchanged: 'grey', error: 'red',
  duplicate: 'red', unmatched: 'amber', outside_scope: 'grey', skipped: 'grey' };

let W = null;

export async function render(root, rest) {
  setPage('Import Data');
  const top = h('div');
  const body = h('div');
  let tab = rest[0] === 'history' ? 'history' : rest[0] === 'screenshots' ? 'screenshots' : 'new';
  const draw = () => {
    mount(top, tabs([['new', 'Spreadsheet import'], ['screenshots', 'Screenshots'], ['history', 'Import history']], tab, (k) => { tab = k; location.hash = k === 'new' ? '#/imports' : '#/imports/' + k; }));
    if (tab === 'history') renderHistory(body); else if (tab === 'screenshots') renderScreenshots(body, rest[1]); else startWizard(body);
  };
  mount(root, top, body);
  draw();
}

// ---------------------------------------------------------------- wizard
async function startWizard(root) {
  const [purposes, templates] = await Promise.all([get('/api/imports/purposes'), get('/api/import-templates')]);
  W = { root, purposes, templates, step: 1, purpose: 'partial', batch: null, sheet: null, headerRow: 1, mapping: {}, options: {},
    resolutions: {}, templateId: null, plan: null, filter: 'all' };
  drawStep();
}

function stepper() {
  const names = ['Upload', 'Map columns', 'Preview and resolve', 'Results'];
  return h('div', { class: 'steps' }, names.map((n, i) => h('span', { class: 'step' + (W.step === i + 1 ? ' active' : W.step > i + 1 ? ' done' : '') }, `${i + 1}. ${n}`)));
}

function drawStep() {
  const parts = [stepper()];
  if (W.step === 1) parts.push(stepUpload());
  if (W.step === 2) parts.push(stepMapping());
  if (W.step === 3) parts.push(stepPreview());
  if (W.step === 4) parts.push(stepResults());
  mount(W.root, ...parts);
}

function stepUpload() {
  const radios = Object.entries(W.purposes).map(([k, label]) => {
    const r = h('input', { type: 'radio', name: 'purpose', value: k, checked: W.purpose === k });
    r.onchange = () => { W.purpose = k; };
    return h('label', { class: 'check card', style: { padding: '10px 12px', margin: '0 0 8px' } }, r,
      h('div', null, h('div', { style: { fontWeight: 600 } }, label), h('div', { class: 'muted small' }, PURPOSE_HELP[k])));
  });
  const input = h('input', { type: 'file', accept: '.xlsx,.csv', class: 'hidden' });
  const progress = h('div', { class: 'progress hidden' }, h('div'));
  const zone = h('div', { class: 'dropzone' }, icon('upload'), h('p', null, h('strong', null, 'Drag an Excel (.xlsx) or CSV file here')),
    h('p', null, 'or'), h('button', { class: 'btn btn-primary', onclick: () => input.click() }, 'Choose file'),
    h('p', { class: 'small', style: { marginTop: '10px' } }, 'Formulas are read as their saved values and never run. Macro workbooks (.xlsm) and old .xls files are not accepted.'), progress);
  const go = async (file) => {
    if (!file) return;
    const fd = new FormData(); fd.append('file', file);
    progress.classList.remove('hidden');
    try {
      const res = await upload('/api/imports/upload', fd, (f) => { progress.firstChild.style.width = (f * 100) + '%'; });
      W.batch = res; W.sheet = res.sheets[0]; W.step = 2;
      await loadSheet();
    } catch (e) { showError(e); progress.classList.add('hidden'); }
  };
  input.onchange = () => go(input.files[0]);
  zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('over'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('over'));
  zone.addEventListener('drop', (e) => { e.preventDefault(); zone.classList.remove('over'); go(e.dataTransfer.files[0]); });
  return h('div', { class: 'grid-2' },
    h('div', { class: 'card' }, h('h3', null, '1. What is this file?'), radios),
    h('div', { class: 'card' }, h('h3', null, '2. Choose the file'), zone, input,
      h('p', { class: 'muted small', style: { marginTop: '12px' } }, 'Need a starting point? ',
        h('a', { href: '/static/samples/employee_import_template.xlsx', download: '' }, 'Download the import template'), ' or the ',
        h('a', { href: '/static/samples/synthetic_current_roster.csv', download: '' }, 'sample roster (CSV)'), '.')));
}

async function loadSheet() {
  mount(W.root, stepper(), spinner('Reading the file…'));
  try {
    W.info = await get(`/api/imports/${W.batch.batch_id}/sheet?` + qs({ sheet: W.sheet, header_row: W.headerRow, template_id: W.templateId || '' }));
    W.mapping = { ...W.info.suggested_mapping };
    if (W.info.template) {
      W.options = { ...W.info.template.options };
      W.purpose = W.info.template.purpose || W.purpose;
    }
    drawStep();
  } catch (e) { showError(e); W.step = 1; drawStep(); }
}

function stepMapping() {
  const info = W.info;
  const fields = info.fields.filter((f) => f.importable);
  const sheetSel = select(info.sheets, info.sheet);
  sheetSel.onchange = () => { W.sheet = sheetSel.value; W.headerRow = 1; loadSheet(); };
  const hdr = h('input', { class: 'input', type: 'number', min: 1, value: W.headerRow, style: { width: '90px' } });
  hdr.onchange = () => { W.headerRow = Math.max(1, +hdr.value || 1); loadSheet(); };
  const tplSel = select([['', 'No template (automatic suggestions)'], ...W.templates.map((t) => [t.id, `${t.name} (${W.purposes[t.purpose] || t.purpose})`])], W.templateId || '');
  tplSel.onchange = () => { W.templateId = tplSel.value ? +tplSel.value : null; loadSheet(); };
  const purposeSel = select(Object.entries(W.purposes), W.purpose);
  purposeSel.onchange = () => { W.purpose = purposeSel.value; drawStep(); };

  const raw = h('div', { class: 'table-wrap', style: { maxHeight: '240px' } }, h('table', { class: 'table small' },
    h('tbody', null, info.raw_preview.map((r, i) => h('tr', { style: { cursor: 'pointer', background: i + 1 === W.headerRow ? 'var(--blue-soft)' : '' },
      title: 'Click to use this row as the header row', onclick: () => { W.headerRow = i + 1; loadSheet(); } },
    h('td', { class: 'muted' }, i + 1), r.map((c) => h('td', { class: 'nowrap' }, c ?? '')))))));

  const opts = [['__ignore__', 'Ignore this column'], ...fields.map((f) => [f.key, f.label + (f.custom ? ' (custom)' : '')])];
  const mapRows = info.headers.map((hname, i) => {
    const s = select(opts, W.mapping[i] || '__ignore__');
    s.onchange = () => { if (s.value === '__ignore__') delete W.mapping[i]; else W.mapping[i] = s.value; };
    const samples = info.sample_rows.map((r) => r[i]).filter((v) => v !== null && v !== '').slice(0, 3).join(' · ');
    return h('tr', null, h('td', null, h('strong', null, hname)), h('td', null, s), h('td', { class: 'muted small' }, samples));
  });

  // Options
  const o = W.options;
  const locs = state.meta.locations;
  const scopeBoxes = locs.map((l) => { const cb = h('input', { type: 'checkbox', checked: (o.scope || []).includes(l) });
    cb.onchange = () => { o.scope = locs.filter((x, j) => scopeBoxes[j].firstChild.checked); }; return h('label', { class: 'check' }, cb, l); });
  const emailFb = h('input', { type: 'checkbox', checked: !!o.email_fallback });
  emailFb.onchange = () => { o.email_fallback = emailFb.checked; };
  const mappedFields = () => Object.values(W.mapping);
  const checkList = (key, list, help) => h('div', null, help ? h('div', { class: 'help muted small' }, help) : null,
    h('div', { class: 'grid-3' }, list.map((f) => { const cb = h('input', { type: 'checkbox', checked: (o[key] || []).includes(f.key) });
      cb.onchange = () => { o[key] = (o[key] || []).filter((x) => x !== f.key); if (cb.checked) o[key].push(f.key); };
      return h('label', { class: 'check small' }, cb, f.label); })));
  const clearable = fields.filter((f) => !['rhc_id', 'full_name', 'employment_status', 'location'].includes(f.key));
  const optionCards = [
    field('Location scope', h('div', { class: 'row' }, scopeBoxes), W.purpose === 'roster' ? 'Required for a complete roster: only employees in these locations can be marked Left.' : 'Optional: rows outside these locations are skipped.', W.purpose === 'roster'),
    h('label', { class: 'check' }, emailFb, h('div', null, 'Also match by official email when the RHC ID is missing', h('div', { class: 'muted small' }, 'Only an exact, unique email is used. Names are never used to merge records.'))),
  ];
  if (W.purpose === 'selected') optionCards.push(field('Fields this import may update', checkList('selected_fields', fields.filter((f) => !['rhc_id'].includes(f.key)))));
  if (W.purpose === 'partial') {
    const cu = h('input', { type: 'checkbox', checked: !!o.create_unmatched }); cu.onchange = () => { o.create_unmatched = cu.checked; };
    optionCards.push(h('label', { class: 'check' }, cu, 'Create new employees for rows whose RHC ID is not found'));
  }
  if (W.purpose === 'roster') {
    if (!o.enrich_fields) o.enrich_fields = ['date_of_joining', 'date_of_birth', 'official_email', 'department', 'designation'];
    optionCards.push(field('Approved historical fields', checkList('enrich_fields', fields.filter((f) => !['rhc_id', 'full_name', 'employment_status', 'location'].includes(f.key))),
      'When a historical-only record appears in this roster, these historical values are kept. Other historical values are cleared.'));
  }
  optionCards.push(h('details', null, h('summary', null, 'Blank cells (advanced)'),
    checkList('blank_clears', clearable, 'By default blank cells never erase existing data. Tick a field only if a blank cell should clear it.')));

  const tplName = h('input', { class: 'input', placeholder: 'Optional: save this mapping as a template' });
  const previewBtn = h('button', { class: 'btn btn-primary' }, 'Preview changes');
  previewBtn.onclick = async () => {
    previewBtn.disabled = true;
    try { await runPreview(tplName.value.trim()); } finally { previewBtn.disabled = false; }
  };
  const prev = info.previous_imports || [];
  return h('div', null,
    prev.length ? h('div', { class: 'banner banner-warn', style: { margin: '0 0 12px' } }, icon('alert'),
      h('div', null, h('strong', null, 'This exact file was imported before. '), prev.map((p) => `#${p.id} ${p.filename} by ${p.operator} on ${fmtDateTime(p.committed_at)} (${p.status})`).join('; '),
        '. You will be asked to confirm before importing it again.')) : null,
    h('div', { class: 'grid-2' },
      h('div', { class: 'card' }, h('div', { class: 'card-title' }, h('h3', null, `File: ${info.filename}`), h('span', { class: 'muted' }, `${info.data_row_count} data rows`)),
        h('div', { class: 'form-grid' }, field('Import purpose', purposeSel), field('Mapping template', tplSel), field('Worksheet', sheetSel), field('Header row', hdr, 'Or click a row below.')),
        info.formula_cells ? h('p', { class: 'small', style: { color: 'var(--amber)' } }, `${info.formula_cells} cells contain formulas. Their saved values are used; formulas are not run.`) : null,
        raw),
      h('div', { class: 'card' }, h('h3', null, 'Options'), optionCards)),
    h('div', { class: 'card' }, h('div', { class: 'card-title' }, h('h3', null, 'Match columns to fields'), h('span', { class: 'muted small' }, 'Unmapped columns are ignored.')),
      h('div', { class: 'table-wrap map-table' }, h('table', { class: 'table' }, h('thead', null, h('tr', null, h('th', null, 'Column in file'), h('th', null, 'Store as'), h('th', null, 'Sample values'))), h('tbody', null, mapRows))),
      h('div', { class: 'row', style: { marginTop: '12px' } }, h('div', { style: { width: '360px' } }, tplName), h('div', { class: 'grow' }),
        h('button', { class: 'btn btn-secondary', onclick: discard }, 'Cancel import'), previewBtn)));
}

async function runPreview(saveAs) {
  const body = { purpose: W.purpose, sheet: W.sheet, header_row: W.headerRow, mapping: W.mapping, options: W.options,
    resolutions: W.resolutions, template_id: W.templateId, save_template_as: saveAs || undefined };
  try {
    const plan = await post(`/api/imports/${W.batch.batch_id}/preview`, body);
    if (saveAs && plan.template_id) { W.templateId = plan.template_id; W.templates = await get('/api/import-templates'); toast('Mapping template saved.', 'success'); }
    W.plan = plan;
    if (plan.mapping_errors && plan.mapping_errors.length) {
      modal({ title: 'The mapping needs attention', body: h('ul', { class: 'msgs err' }, plan.mapping_errors.map((m) => h('li', null, m))), actions: [{ label: 'OK', kind: 'primary' }] });
      return;
    }
    W.deactivate = new Set(plan.deactivations.map((d) => d.employee_id));
    W.step = 3; drawStep();
  } catch (e) { showError(e); }
}

function stepPreview() {
  const plan = W.plan;
  const s = plan.summary;
  const stat = (n, label, cls) => h('div', { class: 'stat' }, h('div', { class: 'stat-num', style: cls ? { color: `var(--${cls})` } : null }, n || 0), h('div', { class: 'stat-label' }, label));
  const counts = h('div', { class: 'grid-3', style: { gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))' } },
    stat(s.new, 'New records', 'green'), stat(s.historical_new, 'New historical-only'), stat((s.update || 0) + (s.promote || 0) + (s.enrich || 0), 'Records updated', 'blue'),
    stat(s.unchanged, 'Unchanged'), stat((s.error || 0) + (s.duplicate || 0), 'Blocking errors', 'red'), stat(s.unmatched, 'Not matched', 'amber'),
    stat(s.outside_scope, 'Outside scope'), stat(s.skipped, 'Skipped'), stat(s.deactivations, 'Proposed to mark Left', 'amber'));
  const filters = [['all', 'All rows'], ['changes', 'Changes'], ['problems', 'Errors and duplicates'], ['unmatched', 'Not matched'], ['unchanged', 'Unchanged'], ['other', 'Skipped / outside scope']];
  const rowsFor = (f) => plan.rows.filter((r) => f === 'all' || (f === 'changes' && ['new', 'historical_new', 'update', 'promote', 'enrich'].includes(r.status)) ||
    (f === 'problems' && ['error', 'duplicate'].includes(r.status)) || (f === 'unmatched' && r.status === 'unmatched') || (f === 'unchanged' && r.status === 'unchanged') ||
    (f === 'other' && ['skipped', 'outside_scope'].includes(r.status)));
  const tableBox = h('div');
  const drawTable = () => {
    const rows = rowsFor(W.filter);
    mount(tableBox, tabs(filters.map(([k, l]) => [k, `${l} (${rowsFor(k).length})`]), W.filter, (k) => { W.filter = k; drawTable(); }),
      rows.length ? h('div', { class: 'table-wrap', style: { maxHeight: '55vh' } }, h('table', { class: 'table' },
        h('thead', null, h('tr', null, ['Row', 'Result', 'RHC ID / name', 'Matched to', 'Changes', 'Messages', 'Action'].map((t) => h('th', null, t)))),
        h('tbody', null, rows.slice(0, 1000).map(rowView)))) : emptyState('Nothing in this group'),
      rows.length > 1000 ? h('p', { class: 'muted small' }, `Showing the first 1000 of ${rows.length} rows. Download the report for the full list.`) : null);
  };
  drawTable();

  // Deactivations
  let deactBox = null;
  const lwd = h('input', { class: 'input', type: 'date', style: { width: '180px' } });
  const confirmDeact = h('input', { type: 'checkbox' });
  if (plan.deactivations.length) {
    const all = h('input', { type: 'checkbox', checked: W.deactivate.size === plan.deactivations.length });
    all.onchange = () => { W.deactivate = new Set(all.checked ? plan.deactivations.map((d) => d.employee_id) : []); drawStep(); };
    deactBox = h('div', { class: 'card', style: { borderColor: '#f3d9a8' } },
      h('h3', null, `${plan.deactivations.length} active employees in ${(W.options.scope || []).join(', ')} are missing from this roster`),
      h('p', { class: 'muted' }, 'They will only be marked Left if you tick them and confirm below. Leave them unticked to keep them active.'),
      h('div', { class: 'table-wrap', style: { maxHeight: '260px' } }, h('table', { class: 'table' },
        h('thead', null, h('tr', null, h('th', null, all), h('th', null, 'RHC ID'), h('th', null, 'Name'), h('th', null, 'Location'), h('th', null, 'Current status'))),
        h('tbody', null, plan.deactivations.map((d) => { const cb = h('input', { type: 'checkbox', checked: W.deactivate.has(d.employee_id) });
          cb.onchange = () => { if (cb.checked) W.deactivate.add(d.employee_id); else W.deactivate.delete(d.employee_id); };
          return h('tr', null, h('td', null, cb), h('td', null, d.rhc_id), h('td', null, d.name), h('td', null, d.location), h('td', null, badge(d.status))); })))),
      h('div', { class: 'row', style: { marginTop: '10px' } }, field('Last working date for those without one (optional)', lwd)),
      h('label', { class: 'check' }, confirmDeact, h('strong', null, 'I confirm the ticked employees have left and should be marked Left.')));
  }
  const prev = plan.previous_imports || [];
  const ackRepeat = h('input', { type: 'checkbox' });
  const commitBtn = h('button', { class: 'btn btn-primary', disabled: plan.blocking > 0 }, 'Commit import');
  commitBtn.onclick = async () => {
    const payload = { plan_hash: plan.plan_hash, acknowledge_repeat: ackRepeat.checked, deactivate_ids: [...W.deactivate],
      confirm_deactivations: confirmDeact.checked, deactivation_last_working_date: lwd.value || null };
    if (payload.deactivate_ids.length && !payload.confirm_deactivations) return toast('Tick the confirmation for employees to be marked Left, or untick them.', 'error');
    const changes = (s.new || 0) + (s.historical_new || 0) + (s.update || 0) + (s.promote || 0) + (s.enrich || 0);
    const ok = await confirmDialog({ title: 'Commit import', confirmLabel: 'Commit',
      message: `This will create or update ${changes} records${payload.deactivate_ids.length ? ` and mark ${payload.deactivate_ids.length} employees as Left` : ''}. All changes are saved together and can be undone later from Import history if nothing has changed since.` });
    if (!ok) return;
    commitBtn.disabled = true;
    try { W.result = await post(`/api/imports/${W.batch.batch_id}/commit`, payload); W.step = 4; drawStep(); toast('Import committed.', 'success'); }
    catch (e) { showError(e); if (e.status === 409) runPreview(); }
    finally { commitBtn.disabled = plan.blocking > 0; }
  };
  return h('div', null, counts,
    plan.blocking ? h('div', { class: 'banner banner-warn', style: { margin: '12px 0 0' } }, icon('alert'), h('div', null,
      `${plan.blocking} rows have blocking errors. Fix the file and upload it again, or skip those rows. `,
      h('button', { class: 'btn btn-secondary btn-sm', onclick: () => { plan.rows.filter((r) => ['error', 'duplicate'].includes(r.status)).forEach((r) => { W.resolutions[r.row_no] = 'skip'; }); runPreview(); } }, 'Skip all rows with errors'))) : null,
    h('div', { class: 'card', style: { marginTop: '12px' } }, tableBox),
    deactBox,
    h('div', { class: 'card' }, h('div', { class: 'row' },
      h('button', { class: 'btn btn-secondary', onclick: () => { W.step = 2; drawStep(); } }, 'Back to mapping'),
      h('button', { class: 'btn btn-secondary', onclick: () => download(`/api/imports/${W.batch.batch_id}/report`).catch(showError) }, icon('download'), 'Download preview report'),
      h('div', { class: 'grow' }),
      prev.length ? h('label', { class: 'check' }, ackRepeat, 'Import this file again (it was imported before)') : null,
      h('button', { class: 'btn btn-secondary', onclick: discard }, 'Cancel import'), commitBtn)));
}

const fv = (v) => (typeof v === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(v) ? fmtDate(v) : String(v));

function rowView(r) {
  const actions = [];
  if (r.status === 'skipped') actions.push(h('button', { class: 'btn btn-link btn-sm', onclick: () => resolve(r.row_no, null) }, 'Include again'));
  else {
    if (['error', 'duplicate', 'unmatched', 'new', 'historical_new', 'update', 'promote', 'enrich'].includes(r.status)) actions.push(h('button', { class: 'btn btn-link btn-sm', onclick: () => resolve(r.row_no, 'skip') }, 'Skip row'));
    (r.suggestions || []).forEach((sg) => actions.push(h('button', { class: 'btn btn-link btn-sm', title: 'Only if you are sure this row is this person',
      onclick: () => linkConfirm(r, sg) }, `Link to ${sg.name} (${sg.rhc_id})`)));
    if (r.status === 'unmatched' && W.purpose === 'partial') actions.push(h('button', { class: 'btn btn-link btn-sm', onclick: () => resolve(r.row_no, 'create') }, 'Create as new'));
  }
  return h('tr', null, h('td', { class: 'muted' }, r.row_no),
    h('td', null, h('span', { class: 'badge badge-' + (STATUS_COLOR[r.status] || 'grey') }, STATUS_LABEL[r.status] || r.status)),
    h('td', null, h('div', null, r.rhc_id || h('span', { class: 'muted' }, '(no ID)')), h('div', { class: 'small muted' }, r.name || '')),
    h('td', { class: 'small' }, r.employee_label || '', r.match_method ? h('div', { class: 'muted' }, 'by ' + r.match_method) : null),
    h('td', { class: 'small' }, (r.changes || []).length ? h('ul', { class: 'change-list' }, r.changes.map((c) => h('li', null, `${c.label}: `,
      c.old !== null && c.old !== undefined && c.old !== '' ? [h('span', { class: 'old' }, fv(c.old)), ' → '] : null, h('span', { class: 'new' }, c.new === null ? '(cleared)' : fv(c.new))))) : ''),
    h('td', { class: 'small' }, r.errors.length ? h('ul', { class: 'msgs err' }, r.errors.map((m) => h('li', null, m))) : null,
      r.warnings.length ? h('ul', { class: 'msgs warn' }, r.warnings.map((m) => h('li', null, m))) : null),
    h('td', null, actions));
}

async function linkConfirm(r, sg) {
  const ok = await confirmDialog({ title: 'Link row to an existing employee', confirmLabel: 'Link',
    message: `Row ${r.row_no} (${r.name || ''}${r.rhc_id ? ', ' + r.rhc_id : ''}) will update ${sg.name} (${sg.rhc_id}). The match is by name only, so confirm this is the same person.` });
  if (ok) resolve(r.row_no, 'link:' + sg.id);
}

function resolve(rowNo, value) {
  if (value) W.resolutions[rowNo] = value; else delete W.resolutions[rowNo];
  runPreview();
}

function stepResults() {
  const s = W.result.summary;
  return h('div', { class: 'card' }, h('h3', null, 'Import complete'),
    h('dl', { class: 'kv' }, h('dt', null, 'Records created'), h('dd', null, s.created), h('dt', null, 'Records updated'), h('dd', null, s.updated),
      h('dt', null, 'Marked Left'), h('dd', null, s.deactivated), h('dt', null, 'Unchanged'), h('dd', null, s.unchanged || 0),
      h('dt', null, 'Skipped or not imported'), h('dd', null, (s.skipped || 0) + (s.unmatched || 0) + (s.outside_scope || 0))),
    h('div', { class: 'row', style: { marginTop: '14px' } },
      h('button', { class: 'btn btn-secondary', onclick: () => download(`/api/imports/${W.batch.batch_id}/report`).catch(showError) }, icon('download'), 'Download results and error report'),
      h('a', { class: 'btn btn-secondary', href: '#/employees' }, 'Go to Employees'),
      h('button', { class: 'btn btn-primary', onclick: () => startWizard(W.root) }, 'Start another import')));
}

async function discard() {
  if (!W.batch) return startWizard(W.root);
  const ok = await confirmDialog({ title: 'Cancel import', message: 'Nothing from this file has been saved. Cancel it?', confirmLabel: 'Cancel import', danger: true });
  if (!ok) return;
  try { await post(`/api/imports/${W.batch.batch_id}/discard`); } catch (_) { /* already gone */ }
  startWizard(W.root);
}

// ---------------------------------------------------------------- screenshots
const SHOT_FIELDS = [['full_name', 'Name'], ['official_email', 'Email'], ['designation', 'Designation'], ['client_name', 'Client'], ['rhc_id', 'RHC ID']];
const SHOT_STATUS = { auto: 'Will update', review: 'Needs your decision', unmatched: 'No employee found', unchanged: 'No change', skipped: 'Skipped', applied: 'Applied', error: 'Error' };

async function renderScreenshots(root, bid) {
  if (bid && /^\d+$/.test(bid)) return screenshotReview(root, +bid);
  mount(root, spinner());
  let batches = [];
  try { batches = await get('/api/screenshots'); } catch (e) { return mount(root, h('p', null, e.message)); }
  const input = h('input', { type: 'file', accept: 'image/*', multiple: true, class: 'hidden' });
  const progress = h('div', { class: 'progress hidden' }, h('div'));
  const send = async (files) => {
    const list = [...files].filter((f) => /^image\//.test(f.type) || /\.(png|jpe?g|bmp|webp|tiff?|gif)$/i.test(f.name));
    if (!list.length) return toast('Choose picture files (PNG or JPG screenshots).', 'warn');
    const fd = new FormData(); list.forEach((f, i) => fd.append('files', f, f.name || `pasted-${i + 1}.png`));
    progress.classList.remove('hidden');
    try {
      const res = await upload('/api/screenshots/upload', fd, (x) => { progress.firstChild.style.width = (x * 100) + '%'; });
      location.hash = '#/imports/screenshots/' + res.batch.id;
    } catch (e) { showError(e); progress.classList.add('hidden'); }
  };
  input.onchange = () => send(input.files);
  const zone = h('div', { class: 'paste-zone', tabindex: '0', role: 'button', 'aria-label': 'Paste or drop screenshots' },
    h('p', null, h('strong', null, 'Paste a screenshot here (Ctrl+V), drag pictures in, or ')), h('button', { class: 'btn btn-primary', onclick: () => input.click() }, 'Choose screenshots'),
    h('p', { class: 'small', style: { marginTop: '10px' } }, 'The text is read with OCR, matched to employees, and shown to you before anything is saved. Only fields that are visible in the picture are updated; nothing else is touched.'), progress, input);
  zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('over'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('over'));
  zone.addEventListener('drop', (e) => { e.preventDefault(); zone.classList.remove('over'); send(e.dataTransfer.files); });
  const onPaste = (e) => {
    const items = [...(e.clipboardData && e.clipboardData.items || [])].filter((it) => it.kind === 'file' && /^image\//.test(it.type));
    if (!items.length) return;
    e.preventDefault(); send(items.map((it) => it.getAsFile()));
  };
  zone.addEventListener('paste', onPaste);
  document.addEventListener('paste', onPaste, { once: true });
  const open = batches.filter((b) => ['uploaded', 'previewed'].includes(b.status));
  const done = batches.filter((b) => !['uploaded', 'previewed'].includes(b.status));
  mount(root, h('div', { class: 'card' }, h('h3', null, 'Import employee details from screenshots'), zone),
    open.length ? h('div', { class: 'card' }, h('h3', null, 'Waiting for review'), h('div', { class: 'table-wrap' }, h('table', { class: 'table' }, h('tbody', null, open.map((b) => h('tr', null, h('td', null, b.filename), h('td', { class: 'small' }, fmtDateTime(b.created_at)), h('td', null, b.operator), h('td', null, h('a', { class: 'btn btn-secondary btn-sm', href: '#/imports/screenshots/' + b.id }, 'Review')))))))) : null,
    h('div', { class: 'card' }, h('h3', null, 'Earlier screenshot imports'), done.length ? h('div', { class: 'table-wrap' }, h('table', { class: 'table' }, h('thead', null, h('tr', null, ['File', 'When', 'By', 'Status', 'Result', ''].map((t) => h('th', null, t)))),
      h('tbody', null, done.map((b) => h('tr', null, h('td', null, b.filename), h('td', { class: 'small nowrap' }, fmtDateTime(b.committed_at || b.created_at)), h('td', null, b.operator), h('td', null, badge(b.status)),
        h('td', { class: 'small' }, b.summary && b.summary.applied !== undefined ? `${b.summary.applied} updated, ${b.summary.skipped} skipped, ${b.summary.posts_created || 0} posts created` : ''),
        h('td', null, h('a', { class: 'btn btn-link btn-sm', href: '#/imports/screenshots/' + b.id }, 'Open'), ['committed', 'partially_undone'].includes(b.status) && can('imports.undo') ? h('a', { class: 'btn btn-link btn-sm', href: '#/imports/history' }, 'Undo (history)') : null)))))) :
      emptyState('No screenshot imports yet', 'Each import keeps the picture and the changes it made, and can be undone from Import history.')));
  setTimeout(() => zone.focus(), 50);
}

async function screenshotReview(root, bid) {
  mount(root, spinner('Reading the screenshot…'));
  let d;
  try { d = await get(`/api/screenshots/${bid}`); } catch (e) { return mount(root, h('p', null, e.message)); }
  const readOnly = d.batch.status !== 'previewed';
  const rowsState = d.rows.map((r) => ({ ...r, approve: r.status === 'auto', inputs: {} }));
  const table = h('tbody');
  const drawRows = () => mount(table, rowsState.map((r) => {
    const emp = r.employee;
    const inputs = {};
    SHOT_FIELDS.forEach(([k]) => { inputs[k] = h('input', { class: 'input', value: r.extracted[k] || '', placeholder: '(not in picture)', disabled: readOnly }); });
    r.inputs = inputs;
    const cand = r.candidates && r.candidates.length ? select([['', '(choose employee)'], ...r.candidates.map((c) => [c.id, `${c.full_name} (${c.rhc_id})${c.client_name ? ' · ' + c.client_name : ''}`])], r.employee_id || '', { disabled: readOnly }) : null;
    if (cand) cand.onchange = () => { r.employee_id = cand.value ? +cand.value : null; };
    const picker = !readOnly && !emp ? employeePickerLazy(r) : null;
    const approve = h('input', { type: 'checkbox', checked: r.approve, disabled: readOnly || !['auto', 'review'].includes(r.status) || !r.employee_id });
    approve.onchange = () => { r.approve = approve.checked; };
    return h('tr', { class: 'shot-row' }, h('td', null, r.seq),
      h('td', null, badge(SHOT_STATUS[r.status] || r.status, r.status), r.match_method ? h('div', { class: 'small muted' }, 'matched by ' + r.match_method) : null),
      h('td', null, SHOT_FIELDS.map(([k, label]) => h('div', { class: 'field', style: { marginBottom: '4px' } }, h('label', { class: 'small muted' }, label), inputs[k]))),
      h('td', null, emp ? h('div', null, h('strong', null, emp.full_name), ` (${emp.rhc_id})`, h('div', { class: 'small muted' }, `now: ${emp.current.designation || 'no designation'} · ${emp.current.client_name || 'no client'} · ${emp.current.official_email || 'no email'}`)) : null,
        cand ? h('div', { style: { marginTop: '4px' } }, cand) : null, picker,
        !readOnly ? h('button', { class: 'btn btn-link btn-sm', onclick: () => { r.skip = !r.skip; reanalyse(); } }, r.status === 'skipped' ? 'Include again' : 'Skip row') : null),
      h('td', null, r.changes.length ? h('ul', { class: 'change-list' }, r.changes.map((c) => h('li', null, `${c.label}: `, c.old ? [h('span', { class: 'old' }, c.old), ' → '] : null, h('span', { class: 'new' }, c.new), c.review ? h('span', { class: 'small', style: { color: 'var(--amber)' } }, ' (identity field: needs your tick)') : null))) : h('span', { class: 'muted small' }, r.status === 'unchanged' ? 'Record already matches' : ''),
        r.messages.length ? h('ul', { class: 'msgs warn' }, r.messages.map((m) => h('li', null, m))) : null,
        r.status === 'applied' ? h('div', { class: 'small', style: { color: 'var(--green)' } }, `Applied: ${(r.applied.fields || []).join(', ')}${r.applied.post && r.applied.post.outcome === 'created' ? ' · deployment post created' : ''}`) : null),
      h('td', null, h('label', { class: 'check' }, approve, 'Apply')));
  }));
  drawRows();
  async function reanalyse() {
    const rows = rowsState.map((r) => ({ extracted: Object.fromEntries(SHOT_FIELDS.map(([k]) => [k, r.inputs[k] ? r.inputs[k].value : r.extracted[k]])), employee_id: r.employee_id || null, skip: !!r.skip }));
    try { await post(`/api/screenshots/${bid}/rows`, { rows }); screenshotReview(root, bid); } catch (e) { showError(e); }
  }
  const addRow = h('button', { class: 'btn btn-secondary btn-sm', disabled: readOnly, onclick: () => { rowsState.push({ seq: rowsState.length + 1, status: 'review', extracted: {}, candidates: [], changes: [], messages: [], applied: {}, approve: false }); drawRows(); } }, icon('plus'), 'Add a row by hand');
  const ocrBox = h('textarea', { class: 'input', rows: 8, disabled: readOnly }); ocrBox.value = d.ocr_text || '';
  const textBtn = h('button', { class: 'btn btn-secondary btn-sm', disabled: readOnly, onclick: async () => { try { await post(`/api/screenshots/${bid}/text`, { text: ocrBox.value }); screenshotReview(root, bid); } catch (e) { showError(e); } } }, 'Re-read from this text');
  const commitBtn = h('button', { class: 'btn btn-primary', disabled: readOnly }, 'Apply ticked rows');
  commitBtn.onclick = async () => {
    const approve = rowsState.filter((r) => r.approve && r.id).map((r) => r.id);
    const n = rowsState.filter((r) => r.approve).length;
    if (!n) return toast('Nothing is ticked.', 'warn');
    const ok = await confirmDialog({ title: 'Apply screenshot import', confirmLabel: 'Apply', message: `${n} employee records will be updated with the fields shown. Other fields are not touched. New client assignments create deployment posts. This can be undone from Import history.` });
    if (!ok) return;
    try { const r = await post(`/api/screenshots/${bid}/commit`, { approve_rows: approve }); toast(`${r.summary.applied} records updated, ${r.summary.skipped} skipped, ${r.summary.posts_created} deployment posts created.`, 'success', 8000); screenshotReview(root, bid); }
    catch (e) { showError(e); }
  };
  const discardBtn = h('button', { class: 'btn btn-secondary', disabled: readOnly, onclick: async () => { if (await confirmDialog({ title: 'Discard', message: 'Nothing has been saved from this screenshot. Discard it?', confirmLabel: 'Discard', danger: true })) { try { await post(`/api/screenshots/${bid}/discard`); location.hash = '#/imports/screenshots'; } catch (e) { showError(e); } } } }, 'Discard');
  const c = d.counts;
  mount(root,
    h('div', { class: 'row-between', style: { marginBottom: '10px' } }, h('a', { href: '#/imports/screenshots' }, '← All screenshot imports'), badge(d.batch.status)),
    d.problems.length ? h('div', { class: 'banner banner-warn', style: { margin: '0 0 12px' } }, icon('alert'), h('ul', { class: 'msgs' }, d.problems.map((m) => h('li', null, m)))) : null,
    h('div', { class: 'grid-2' },
      h('div', { class: 'card' }, h('h3', null, 'Screenshot' + (d.files.length > 1 ? 's' : '')), h('div', { class: 'row' }, d.files.map((f, i) => h('img', { src: `/api/screenshots/${bid}/files/${i}`, alt: f.name, style: { maxWidth: '100%', maxHeight: '320px', border: '1px solid var(--border)', borderRadius: '6px', cursor: 'zoom-in' }, onclick: () => modal({ title: f.name, wide: 'xl', body: h('div', { class: 'lightbox' }, h('img', { src: `/api/screenshots/${bid}/files/${i}` })) }) })))),
      h('div', { class: 'card' }, h('h3', null, 'Text read from the picture'), h('p', { class: 'small muted' }, 'Fix OCR mistakes here (or add lines such as "Designation: Senior Developer") and re-read. Lines that were not understood are listed per row.'), ocrBox, h('div', { style: { marginTop: '6px' } }, textBtn))),
    h('div', { class: 'card' }, h('div', { class: 'card-title' }, h('h3', null, `Employees found: ${c.auto || 0} ready to update, ${c.review || 0} need a decision, ${c.unmatched || 0} not matched, ${c.unchanged || 0} unchanged`), h('div', { class: 'row' }, addRow, h('button', { class: 'btn btn-secondary btn-sm', disabled: readOnly, onclick: reanalyse }, icon('refresh'), 'Re-check with my edits'))),
      h('div', { class: 'table-wrap' }, h('table', { class: 'table' }, h('thead', null, h('tr', null, ['#', 'Result', 'Read from the picture', 'Employee', 'Changes', ''].map((t) => h('th', null, t)))), table)),
      h('div', { class: 'row', style: { marginTop: '12px' } }, discardBtn, h('div', { class: 'grow' }), h('span', { class: 'small muted' }, 'Rows with identity changes (name, email, RHC ID) or several possible employees are applied only if you tick them.'), commitBtn)));
}

function employeePickerLazy(r) {
  const box = h('div', { style: { marginTop: '4px' } });
  import('./photos.js').then(({ employeePicker }) => mount(box, employeePicker(null, (e) => { r.employee_id = e ? e.id : null; })));
  return box;
}

// ---------------------------------------------------------------- history
async function renderHistory(root) {
  mount(root, spinner());
  let rows, templates;
  try { [rows, templates] = await Promise.all([get('/api/imports'), get('/api/import-templates')]); } catch (e) { mount(root, h('p', null, e.message)); return; }
  const list = rows.filter((r) => r.status !== 'uploaded');
  if (W && W.purposes) W.purposes.screenshot = 'Screenshot import';
  mount(root,
    h('div', { class: 'card' }, h('h3', null, 'Import batches'),
      list.length ? h('div', { class: 'table-wrap' }, h('table', { class: 'table' },
        h('thead', null, h('tr', null, ['#', 'File', 'Purpose', 'Scope', 'Operator', 'When', 'Status', 'Summary', ''].map((t) => h('th', null, t)))),
        h('tbody', null, list.map((b) => h('tr', null, h('td', null, b.id), h('td', null, b.filename, h('div', { class: 'mono muted', title: 'SHA-256 of the file' }, (b.file_hash || '').slice(0, 12))),
          h('td', null, W && W.purposes ? W.purposes[b.purpose] || b.purpose || '' : b.purpose || '', b.template_name ? h('div', { class: 'muted small' }, 'Template: ' + b.template_name) : null),
          h('td', null, (b.scope || []).join(', ')), h('td', { class: 'small' }, b.operator),
          h('td', { class: 'small nowrap' }, fmtDateTime(b.committed_at || b.created_at)), h('td', null, badge(b.status)),
          h('td', { class: 'small' }, b.summary && b.summary.created !== undefined ? `${b.summary.created} created, ${b.summary.updated} updated, ${b.summary.deactivated} marked Left` : ''),
          h('td', { class: 'nowrap' },
            h('button', { class: 'btn btn-link btn-sm', onclick: () => download(`/api/imports/${b.id}/report`).catch(showError) }, 'Report'),
            can('imports.undo') && ['committed', 'partially_undone'].includes(b.status) ? h('button', { class: 'btn btn-link btn-sm', onclick: () => undoFlow(b, root) }, 'Undo') : null)))))) :
        emptyState('No imports yet', 'Committed imports appear here with their file, purpose, operator and changes.')),
    h('div', { class: 'card' }, h('h3', null, 'Saved mapping templates'),
      templates.length ? h('div', { class: 'table-wrap' }, h('table', { class: 'table' }, h('thead', null, h('tr', null, ['Name', 'Purpose', 'Mapped columns', 'Updated', ''].map((t) => h('th', null, t)))),
        h('tbody', null, templates.map((t) => h('tr', null, h('td', null, t.name), h('td', null, t.purpose), h('td', { class: 'small' }, Object.keys(t.mapping).join(', ')),
          h('td', { class: 'small' }, fmtDateTime(t.updated_at)),
          h('td', null, h('button', { class: 'btn btn-link btn-sm', onclick: async () => {
            if (!(await confirmDialog({ title: 'Delete template', message: `Delete the mapping template "${t.name}"? Past imports are not affected.`, confirmLabel: 'Delete', danger: true }))) return;
            try { await del(`/api/import-templates/${t.id}`); toast('Template deleted.', 'success'); renderHistory(root); } catch (e) { showError(e); }
          } }, 'Delete'))))))) : emptyState('No templates yet', 'Save a mapping as a template during an import to reuse it next time.')));
}

async function undoFlow(b, root) {
  const loading = modal({ title: 'Checking whether this import can be undone', body: spinner(), actions: [] });
  let plan;
  try { plan = await get(`/api/imports/${b.id}/undo-preview`); } catch (e) { loading.close(); return showError(e); }
  loading.close();
  const body = h('div', null,
    h('p', null, `${plan.revertible.length} changes can be reverted safely.`),
    plan.conflicts.length ? h('div', null, h('p', { style: { color: 'var(--red)' } }, `${plan.conflicts.length} changes were modified again after this import and will not be overwritten:`),
      h('div', { class: 'table-wrap', style: { maxHeight: '240px' } }, h('table', { class: 'table small' }, h('thead', null, h('tr', null, h('th', null, 'Employee'), h('th', null, 'Field'), h('th', null, 'Reason'))),
        h('tbody', null, plan.conflicts.map((c) => h('tr', null, h('td', null, c.employee || c.employee_id), h('td', null, c.field || c.action), h('td', null, c.reason))))))) : null);
  const actions = [{ label: 'Cancel' }];
  if (plan.conflicts.length && plan.revertible.length) actions.push({ label: 'Undo only the changes without conflicts', kind: 'secondary', onClick: () => doUndo('non_conflicting') });
  if (!plan.conflicts.length && plan.revertible.length) actions.push({ label: 'Undo this import', kind: 'danger', onClick: () => doUndo('all') });
  modal({ title: `Undo import #${b.id}`, body, wide: true, actions });
  async function doUndo(mode) {
    const res = await post(`/api/imports/${b.id}/undo`, { mode });
    toast(`Undo finished: ${res.reverted} changes reverted${res.failed.length ? `, ${res.failed.length} could not be reverted` : ''}.`, res.failed.length ? 'warn' : 'success');
    renderHistory(root);
  }
}
