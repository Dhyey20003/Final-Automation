// Tasks and Exports: IT and HR headshot tasks, periods, packages, manifests, Outlook dispatch and manual completion.
import { get, post, put, download } from '../api.js';
import { h, mount, icon, toast, showError, modal, confirmDialog, promptDialog, spinner, emptyState, fmtDate, fmtDateTime, fmtBytes, badge, field, select } from '../ui.js';
import { can, setPage, refreshSystem } from '../app.js';

let T = null;

export async function render(root, rest) {
  setPage('Tasks and Exports');
  T = { root };
  if (rest[0] === 'period' && rest[1]) return renderPeriod(+rest[1]);
  return renderOverview();
}

function scheduleText(cfg) {
  const s = cfg.schedule;
  if (s.type === 'interval') return s.anchor_date ? `Every ${s.interval_days} days from ${fmtDate(s.anchor_date)} at ${s.time}` : `Every ${s.interval_days} days (anchor date not set)`;
  return `Monthly on day ${s.day_of_month} at ${s.time} (last day of shorter months)`;
}

async function renderOverview() {
  mount(T.root, spinner());
  let data;
  try { data = await get('/api/tasks'); } catch (e) { return mount(T.root, h('p', null, e.message)); }
  T.data = data;
  const outlookBox = h('div', { class: 'card' });
  drawOutlook(outlookBox, data.outlook_mode, null);
  mount(T.root, outlookBox, h('div', { class: 'grid-2', style: { marginTop: '16px' } }, data.tasks.map(taskCard)),
    h('div', { class: 'card', style: { marginTop: '16px' } }, h('h3', null, 'How sending works'),
      h('ul', { class: 'msgs' },
        h('li', null, 'Each period gets one ZIP per location and one email with both ZIPs attached.'),
        h('li', null, '"Submitted to Outlook" means Outlook accepted the message. "Confirmed in Sent Items" means it was found in Sent Items. Neither proves the recipients received it.'),
        h('li', null, 'Uncertain sends are never retried automatically. Check Outlook, then record what happened.'),
        h('li', null, 'Scheduled work only runs while this computer is on and Relay Employee Hub is running in your Windows session.'))));
}

async function drawOutlook(box, mode, status) {
  const modeLabel = { classic: 'Classic Outlook (automation)', manual: 'Manual (new Outlook or Outlook on the web)', dryrun: 'Dry-run (nothing is sent)' }[mode] || mode;
  const check = h('button', { class: 'btn btn-secondary btn-sm' }, 'Check Outlook now');
  check.onclick = async () => { check.disabled = true; try { drawOutlook(box, mode, await get('/api/outlook/status')); } catch (e) { showError(e); } finally { check.disabled = false; } };
  mount(box, h('div', { class: 'card-title' }, h('h3', null, icon('mail'), ' Email'), check),
    h('dl', { class: 'kv' }, h('dt', null, 'Mode'), h('dd', null, modeLabel),
      status ? [h('dt', null, 'Status'), h('dd', null, status.available ? badge('Available', 'green') : badge('Not available', 'red'), ' ', status.message || ''),
        status.version ? [h('dt', null, 'Outlook version'), h('dd', null, status.version)] : null,
        status.profile ? [h('dt', null, 'Profile'), h('dd', null, status.profile)] : null,
        status.accounts && status.accounts.length ? [h('dt', null, 'Accounts'), h('dd', null, status.accounts.map((a) => `${a.name} <${a.smtp}>`).join(', '))] : null,
        status.new_outlook_hint ? [h('dt', null, 'Note'), h('dd', null, status.new_outlook_hint)] : null] : null),
    mode === 'manual' ? h('p', { class: 'muted small', style: { marginTop: '8px' } }, 'Packages are still prepared. Use "Prepare ready folder" in a period to get the ZIPs and the email text, send from your mail app, then mark the period as sent.') : null);
}

function taskCard(t) {
  const cfg = t.config;
  const em = cfg.email || {};
  const inc = T.data.inclusions[cfg.inclusion] || 'Not chosen yet';
  const actions = [];
  if (can('tasks.operate')) actions.push(h('button', { class: 'btn btn-primary btn-sm', onclick: () => prepareNow(t) }, 'Prepare packages now'));
  if (can('tasks.configure')) {
    actions.push(h('button', { class: 'btn btn-secondary btn-sm', onclick: () => taskSettings(t) }, 'Task settings'));
    actions.push(h('button', { class: 'btn btn-secondary btn-sm', onclick: () => toggleEnabled(t) }, t.enabled ? 'Turn schedule off' : 'Turn schedule on'));
    actions.push(h('button', { class: 'btn btn-secondary btn-sm', onclick: () => autoSendDialog(t) }, t.auto_send_enabled ? 'Turn automatic sending off' : 'Turn on automatic sending'));
    if (t.needs_revalidation) actions.push(h('button', { class: 'btn btn-accent btn-sm', onclick: () => revalidate(t) }, 'I have re-checked this task'));
  }
  const periods = t.periods;
  return h('div', { class: 'card task-card' + (t.kind === 'HR' ? ' hr' : '') },
    h('div', { class: 'card-title' }, h('h2', null, t.name), h('div', { class: 'row' }, t.enabled ? badge('Scheduled', 'green') : badge('Schedule off', 'grey'),
      t.auto_send_enabled ? badge('Automatic sending on', 'purple') : badge('Manual sending', 'grey'))),
    t.needs_revalidation ? h('div', { class: 'banner banner-warn', style: { margin: '0 0 10px' } }, icon('alert'), h('div', null, t.revalidation_reason || 'Needs to be re-checked.')) : null,
    h('dl', { class: 'kv small' },
      h('dt', null, 'Schedule'), h('dd', null, scheduleText(cfg)),
      h('dt', null, 'Next due'), h('dd', null, t.next_due ? `${t.next_due.label} (${fmtDateTime(t.next_due.due_at)})` : 'Not scheduled'),
      h('dt', null, 'Include'), h('dd', null, inc),
      h('dt', null, 'File names'), h('dd', { class: 'mono' }, cfg.naming_template + (cfg.output_format !== 'original' ? ` (converted to ${cfg.output_format.toUpperCase()})` : '')),
      h('dt', null, 'Archives'), h('dd', { class: 'mono' }, cfg.archive_name_template),
      h('dt', null, 'Locations'), h('dd', null, cfg.locations.join(', ')),
      h('dt', null, 'Recipients'), h('dd', null, em.to && em.to.length ? `To: ${em.to.join(', ')}${em.cc && em.cc.length ? ' · CC: ' + em.cc.join(', ') : ''}` : h('span', { style: { color: 'var(--amber)' } }, 'Not set')),
      h('dt', null, 'Sender'), h('dd', null, em.sender || h('span', { class: 'muted' }, 'Not chosen'))),
    h('div', { class: 'row', style: { margin: '12px 0' } }, actions),
    h('h3', { style: { marginTop: '8px' } }, 'Periods', t.overdue_count ? h('span', null, ' ', badge(`${t.overdue_count} overdue`, 'red')) : null),
    periods.length ? h('div', { class: 'table-wrap' }, h('table', { class: 'table' }, h('thead', null, h('tr', null, ['Period', 'Due', 'Status', ''].map((x) => h('th', null, x)))),
      h('tbody', null, periods.map((p) => h('tr', null, h('td', null, p.period_label, p.backlog ? h('div', { class: 'small', style: { color: 'var(--amber)' } }, 'Missed while app was off') : null),
        h('td', { class: 'small nowrap' }, p.due_local, p.overdue ? h('div', null, badge('Overdue', 'red')) : null),
        h('td', null, badge(p.status), p.status_detail && p.status_detail.message ? h('div', { class: 'small muted', style: { maxWidth: '360px' } }, p.status_detail.message.slice(0, 180)) : null),
        h('td', null, h('a', { class: 'btn btn-secondary btn-sm', href: `#/tasks/period/${p.id}` }, 'Open'))))))) :
      emptyState('No periods yet', t.enabled ? 'Periods appear when they become due, or use "Prepare packages now".' : 'Turn the schedule on, or use "Prepare packages now" for a one-off package.'));
}

async function prepareNow(t) {
  if (!t.config.inclusion) { toast('Choose the inclusion setting in Task settings first.', 'warn'); if (can('tasks.configure')) taskSettings(t); return; }
  const ok = await confirmDialog({ title: `Prepare ${t.name}`, confirmLabel: 'Prepare',
    message: 'Packages are prepared for the current period (or the next one if the current period is complete). Nothing is sent.' });
  if (!ok) return;
  const m = modal({ title: 'Preparing packages', body: spinner('Validating employees and photos, building ZIP files…'), actions: [], dismissible: false });
  try { const res = await post(`/api/tasks/${t.id}/prepare`, {}); m.close(); location.hash = `#/tasks/period/${res.period.id}`; }
  catch (e) { m.close(); showError(e); renderOverview(); }
}

async function toggleEnabled(t) {
  try { await post(`/api/tasks/${t.id}/enable`, { enabled: !t.enabled }); toast(t.enabled ? 'Schedule turned off.' : 'Schedule turned on.', 'success'); renderOverview(); }
  catch (e) { showError(e); if (!t.enabled) taskSettings(t); }
}

async function revalidate(t) {
  const ok = await confirmDialog({ title: 'Confirm task details', confirmLabel: 'Confirm',
    message: `Confirm that the sender (${t.config.email.sender || 'not set'}), recipients (${(t.config.email.to || []).join(', ') || 'not set'}) and this computer are correct for ${t.name}. Automatic sending stays off until you turn it on again.` });
  if (!ok) return;
  try { await post(`/api/tasks/${t.id}/revalidate`); toast('Task confirmed.', 'success'); refreshSystem(); renderOverview(); } catch (e) { showError(e); }
}

async function autoSendDialog(t) {
  if (t.auto_send_enabled) {
    const ok = await confirmDialog({ title: 'Turn off automatic sending', message: 'Packages will still be prepared on schedule, but emails will only be sent when someone clicks Send.', confirmLabel: 'Turn off' });
    if (!ok) return;
    try { await post(`/api/tasks/${t.id}/auto-send`, { enabled: false }); toast('Automatic sending is off.', 'success'); renderOverview(); } catch (e) { showError(e); }
    return;
  }
  const em = t.config.email;
  const cb = h('input', { type: 'checkbox' });
  modal({ title: `Turn on automatic sending for ${t.name}`,
    body: h('div', null, h('p', null, 'When a period is due and its packages are complete, the email will be sent through Outlook without asking each time.'),
      h('dl', { class: 'kv small' }, h('dt', null, 'Sender'), h('dd', null, em.sender || 'Not chosen'), h('dt', null, 'To'), h('dd', null, (em.to || []).join(', ') || 'Not set'),
        h('dt', null, 'CC'), h('dd', null, (em.cc || []).join(', ') || 'None'), h('dt', null, 'Schedule'), h('dd', null, scheduleText(t.config))),
      h('ul', { class: 'msgs', style: { margin: '10px 0' } }, h('li', null, 'Incomplete packages, missed periods and uncertain results are never sent automatically.'),
        h('li', null, 'The computer must be on with Relay Employee Hub and Outlook available in your Windows session.')),
      h('label', { class: 'check' }, cb, h('strong', null, 'I confirm emails may be sent automatically to these recipients.'))),
    actions: [{ label: 'Cancel' }, { label: 'Turn on', kind: 'primary', onClick: async () => {
      await post(`/api/tasks/${t.id}/auto-send`, { enabled: true, confirm: cb.checked }); toast('Automatic sending is on.', 'success'); refreshSystem(); renderOverview();
    } }] });
}

async function taskSettings(t) {
  const cfg = JSON.parse(JSON.stringify(t.config));
  const s = cfg.schedule; const em = cfg.email;
  let outlook = null;
  try { outlook = await get('/api/outlook/status'); } catch (_) { /* optional */ }
  const name = h('input', { class: 'input', value: t.name });
  const anchor = h('input', { class: 'input', type: 'date', value: s.anchor_date || '' });
  const interval = h('input', { class: 'input', type: 'number', min: 1, max: 365, value: s.interval_days || 14 });
  const dom = h('input', { class: 'input', type: 'number', min: 1, max: 31, value: s.day_of_month || 1 });
  const time = h('input', { class: 'input', type: 'time', value: s.time || '10:00' });
  const incRadios = Object.entries(T.data.inclusions).map(([k, label]) => { const r = h('input', { type: 'radio', name: 'inc', value: k, checked: cfg.inclusion === k }); return { k, r, el: h('label', { class: 'check' }, r, label) }; });
  const naming = h('input', { class: 'input mono', value: cfg.naming_template });
  const fmt = select(Object.entries(T.data.output_formats), cfg.output_format);
  const arch = h('input', { class: 'input mono', value: cfg.archive_name_template });
  const locBoxes = T.data.locations.map((l) => { const c = h('input', { type: 'checkbox', checked: cfg.locations.includes(l) }); return { l, c, el: h('label', { class: 'check' }, c, l) }; });
  const accounts = (outlook && outlook.accounts) || [];
  const sender = accounts.length ? select([['', '(choose an account)'], ...accounts.map((a) => [a.smtp, `${a.name} <${a.smtp}>`])], em.sender || '') : h('input', { class: 'input', type: 'email', value: em.sender || '', placeholder: 'Account email in Outlook' });
  const to = h('input', { class: 'input', value: (em.to || []).join('; '), placeholder: 'name@company.com; other@company.com' });
  const cc = h('input', { class: 'input', value: (em.cc || []).join('; ') });
  const subj = h('input', { class: 'input', value: em.subject_template });
  const bodyT = h('textarea', { class: 'input', rows: 8 }); bodyT.value = em.body_template;
  const sig = h('input', { class: 'input', value: em.signature_name || '', placeholder: 'Relay Human Cloud' });
  const ref = h('input', { type: 'checkbox', checked: em.include_reference !== false });
  const limit = h('input', { class: 'input', type: 'number', min: 1, max: 150, value: cfg.attachment_limit_mb });
  const manifestIn = h('input', { type: 'checkbox', checked: !!cfg.include_manifest_in_zip });
  const catchUp = h('input', { class: 'input', type: 'number', min: 1, max: 720, value: cfg.catch_up_hours || 48 });
  const body = h('div', { class: 'form-grid' },
    h('div', { class: 'form-section' }, 'General'), field('Task name', name), h('div'),
    h('div', { class: 'form-section' }, 'Schedule (Asia/Kolkata unless changed in Settings)'),
    s.type === 'interval' ? [field('Anchor date', anchor, 'The first due date. Later periods follow every N days.'), field('Every N days', interval)] :
      [field('Day of month', dom, 'For days beyond the end of a month, the last day of that month is used.'), h('div')],
    field('Time', time), field('Catch-up window (hours)', catchUp, 'A period missed by more than this is never sent automatically; it is flagged for review.'),
    h('div', { class: 'form-section' }, 'Package contents'),
    h('div', { class: 'span-2' }, field('Which employees to include (required)', h('div', null, incRadios.map((x) => x.el)))),
    field('Photo file names', naming, `Placeholders: ${T.data.name_tokens.map((x) => '{' + x + '}').join(' ')}. Duplicate names get _<RHC ID> added.`),
    field('Image format', fmt, 'Converting creates a real JPG or PNG file, not just a new extension.'),
    field('ZIP file name', arch, `Placeholders: ${T.data.archive_tokens.map((x) => '{' + x + '}').join(' ')}`),
    field('Locations (one ZIP each)', h('div', { class: 'row' }, locBoxes.map((x) => x.el))),
    h('label', { class: 'check span-2' }, manifestIn, 'Also put manifest.json inside each ZIP (the manifest is always kept in the app).'),
    h('div', { class: 'form-section' }, 'Email'),
    field('Sender account', sender, accounts.length ? 'Accounts from your Outlook profile.' : 'Check Outlook on the Tasks page to choose from your accounts.'),
    field('Attachment size limit (MB)', limit, 'Many mail servers reject messages over about 20 MB.'),
    field('To', to, 'Separate addresses with ; or ,. Recipients are never filled in automatically.'), field('CC', cc),
    h('div', { class: 'span-2' }, field('Subject', subj)),
    h('div', { class: 'span-2' }, field('Body', bodyT, `Placeholders: ${T.data.email_tokens.map((x) => '{' + x + '}').join(' ')}`)),
    field('Signature name', sig), h('label', { class: 'check' }, ref, 'Add a short reference line (used to find the email in Sent Items)'));
  modal({ title: `${t.name} settings`, wide: true, body, actions: [{ label: 'Cancel' }, { label: 'Save settings', kind: 'primary', onClick: async () => {
    const inc = incRadios.find((x) => x.r.checked);
    const newCfg = {
      schedule: s.type === 'interval' ? { anchor_date: anchor.value || null, interval_days: +interval.value, time: time.value } : { day_of_month: +dom.value, time: time.value },
      inclusion: inc ? inc.k : null, naming_template: naming.value.trim(), output_format: fmt.value, archive_name_template: arch.value.trim(),
      locations: locBoxes.filter((x) => x.c.checked).map((x) => x.l), attachment_limit_mb: +limit.value, include_manifest_in_zip: manifestIn.checked,
      catch_up_hours: +catchUp.value,
      email: { sender: sender.value.trim(), to: to.value, cc: cc.value, subject_template: subj.value, body_template: bodyT.value, signature_name: sig.value.trim(), include_reference: ref.checked },
    };
    await put(`/api/tasks/${t.id}`, { version: t.version, name: name.value.trim(), config: newCfg });
    toast('Task settings saved.', 'success'); renderOverview();
  } }] });
}

// ---------------------------------------------------------------- period detail
async function renderPeriod(id) {
  mount(T.root, spinner());
  let d;
  try { d = await get(`/api/periods/${id}`); } catch (e) { return mount(T.root, h('p', null, e.message)); }
  const p = d.period;
  const current = d.packages.filter((x) => x.status !== 'superseded');
  const older = d.packages.filter((x) => x.status === 'superseded');
  const locked = ['Submitted to Outlook', 'Confirmed in Sent Items', 'Manually Completed', 'Delivery Uncertain'].includes(p.status);
  const op = can('tasks.operate');
  const actions = [];
  if (op && !locked) {
    actions.push(h('button', { class: 'btn btn-secondary', onclick: () => regen(id, current.length) }, current.length ? 'Regenerate (new revision)' : 'Prepare packages'));
    if (current.some((x) => x.omitted_count) || (current[0] && (current[0].manifest.employees_without_location || []).length)) actions.push(h('button', { class: 'btn btn-secondary', onclick: () => acceptOmissions(id) }, 'Accept omissions'));
  }
  const sendable = op && p.status === 'Ready';
  const mode = d.outlook_mode;
  if (sendable && mode !== 'manual') {
    actions.push(h('button', { class: 'btn btn-secondary', onclick: () => dispatchNow(id, 'draft', d) }, 'Create draft in Outlook'));
    actions.push(h('button', { class: 'btn btn-primary', onclick: () => dispatchNow(id, 'send', d) }, icon('mail'), 'Send now'));
  }
  if (op && current.length && !['Confirmed in Sent Items', 'Manually Completed'].includes(p.status)) {
    actions.push(h('button', { class: 'btn btn-secondary', onclick: () => readyFolder(id) }, 'Prepare ready folder'));
    if (!d.dispatches.some((x) => ['in_progress', 'submitted', 'uncertain'].includes(x.status))) {
      actions.push(h('button', { class: 'btn btn-secondary', onclick: () => manualComplete(id, false) }, 'Mark as sent manually'));
      actions.push(h('button', { class: 'btn btn-link', onclick: () => manualComplete(id, true) }, 'Mark complete with nothing sent'));
    }
  } else if (op && !current.length && p.status === 'Needs Attention') {
    actions.push(h('button', { class: 'btn btn-link', onclick: () => manualComplete(id, true) }, 'Mark complete with nothing sent'));
  }
  mount(T.root,
    h('div', { style: { marginBottom: '10px' } }, h('a', { href: '#/tasks' }, '← Tasks and Exports')),
    h('div', { class: 'card' }, h('div', { class: 'card-title' }, h('div', null, h('h2', null, `${d.task.name}: ${p.period_label}`), h('div', { class: 'muted' }, `Due ${p.due_local}`)), badge(p.status)),
      p.status_detail && p.status_detail.message ? h('div', { class: 'detail-box' }, p.status_detail.message) : null,
      p.status_detail && p.status_detail.problems && p.status_detail.problems.length ? h('ul', { class: 'msgs warn', style: { marginTop: '8px' } }, p.status_detail.problems.map((x) => h('li', null, x))) : null,
      h('div', { class: 'row', style: { marginTop: '12px' } }, actions)),
    h('div', { class: 'card' }, h('h3', null, 'Packages'),
      current.length ? h('div', { class: 'table-wrap' }, h('table', { class: 'table' }, h('thead', null, h('tr', null, ['Location', 'ZIP file', 'Photos', 'Left out', 'Size', 'Revision', 'State', ''].map((x) => h('th', null, x)))),
        h('tbody', null, current.map((pk) => h('tr', null, h('td', null, pk.location), h('td', { class: 'mono' }, pk.zip_name || h('span', { class: 'muted' }, 'No ZIP (nothing to include)')),
          h('td', null, pk.included_count), h('td', null, pk.omitted_count ? h('span', { style: { color: 'var(--amber)' } }, pk.omitted_count) : 0),
          h('td', null, fmtBytes(pk.bytes)), h('td', null, 'rev ' + pk.revision),
          h('td', null, pk.is_complete ? badge('Complete', 'green') : pk.omissions_accepted_by ? badge('Incomplete, accepted', 'amber') : badge('Incomplete', 'red'),
            pk.omissions_accepted_by ? h('div', { class: 'small muted' }, `Accepted by ${pk.omissions_accepted_by}: ${pk.omissions_accept_note}`) : null),
          h('td', { class: 'nowrap' }, h('button', { class: 'btn btn-link btn-sm', onclick: () => showManifest(pk.id) }, 'Manifest'),
            pk.zip_path && op ? h('button', { class: 'btn btn-link btn-sm', onclick: () => download(`/api/packages/${pk.id}/download`, pk.zip_name).catch(showError) }, 'Download') : null)))))) :
        emptyState('No packages prepared yet', op ? 'Click "Prepare packages" to validate employees and build the ZIP files.' : ''),
      older.length ? h('details', { style: { marginTop: '10px' } }, h('summary', null, `${older.length} earlier revisions (kept unchanged)`),
        h('ul', null, older.map((pk) => h('li', { class: 'small' }, `${pk.location} rev ${pk.revision}: ${pk.included_count} photos, ${pk.omitted_count} left out, created ${fmtDateTime(pk.created_at)} by ${pk.created_by} `,
          h('button', { class: 'btn btn-link btn-sm', onclick: () => showManifest(pk.id) }, 'Manifest'))))) : null),
    d.email_preview ? h('div', { class: 'card' }, h('h3', null, 'Email preview'),
      h('dl', { class: 'kv small' }, h('dt', null, 'From'), h('dd', null, d.email_preview.sender || '(default Outlook account)'), h('dt', null, 'To'), h('dd', null, d.email_preview.to.join('; ') || h('span', { style: { color: 'var(--red)' } }, 'No recipients configured')),
        h('dt', null, 'CC'), h('dd', null, d.email_preview.cc.join('; ')), h('dt', null, 'Subject'), h('dd', null, d.email_preview.subject),
        h('dt', null, 'Attachments'), h('dd', null, d.email_preview.attachments.map((a) => `${a.name} (${fmtBytes(a.bytes)})`).join(', ') || 'None')),
      h('pre', { class: 'email', style: { marginTop: '10px' } }, d.email_preview.body)) : null,
    h('div', { class: 'card' }, h('h3', null, 'Send history'),
      d.dispatches.length ? h('div', { class: 'table-wrap' }, h('table', { class: 'table' }, h('thead', null, h('tr', null, ['Reference', 'Type', 'Result', 'When', 'By', 'Notes', ''].map((x) => h('th', null, x)))),
        h('tbody', null, d.dispatches.map((x) => h('tr', null, h('td', { class: 'mono' }, x.dispatch_uid), h('td', null, { draft: 'Draft', send: 'Send now', auto: 'Automatic', manual: 'Manual' }[x.mode] || x.mode),
          h('td', null, badge(dispatchLabel(x.status), dispatchColor(x.status))), h('td', { class: 'small nowrap' }, fmtDateTime(x.confirmed_at || x.submitted_at || x.created_at)),
          h('td', { class: 'small' }, x.created_by), h('td', { class: 'small' }, x.error || x.notes || ''),
          h('td', { class: 'nowrap' }, op && ['submitted', 'uncertain', 'draft_created'].includes(x.status) ? h('button', { class: 'btn btn-link btn-sm', onclick: () => reconcile(x.id, id) }, 'Check Outlook') : null,
            op && ['submitted', 'uncertain'].includes(x.status) ? h('button', { class: 'btn btn-link btn-sm', onclick: () => resolve(x, id) }, 'Record outcome') : null)))))) :
        emptyState('Nothing sent yet')));
}

function dispatchLabel(s) {
  return { in_progress: 'In progress', draft_created: 'Draft created', submitted: 'Submitted to Outlook', confirmed_sent: 'Confirmed in Sent Items', failed: 'Failed',
    uncertain: 'Delivery uncertain', manual_completed: 'Manually completed', manual_confirmed: 'Confirmed by operator' }[s] || s;
}
function dispatchColor(s) { return { draft_created: 'blue', submitted: 'purple', confirmed_sent: 'green', failed: 'red', uncertain: 'red', manual_completed: 'teal', manual_confirmed: 'teal' }[s] || 'grey'; }

async function regen(id, hasPackages) {
  if (hasPackages) {
    const ok = await confirmDialog({ title: 'Regenerate packages', confirmLabel: 'Regenerate', message: 'A new revision is built from the current data. The existing revision is kept unchanged for the record.' });
    if (!ok) return;
  }
  const m = modal({ title: 'Preparing packages', body: spinner('Building ZIP files…'), actions: [], dismissible: false });
  try { await post(`/api/periods/${id}/${hasPackages ? 'regenerate' : 'prepare'}`); m.close(); toast('Packages prepared.', 'success'); }
  catch (e) { m.close(); showError(e); }
  renderPeriod(id);
}

async function acceptOmissions(id) {
  const note = await promptDialog({ title: 'Accept omissions', label: 'Why is it acceptable to send without these employees?', multiline: true, confirmLabel: 'Accept',
    help: 'This is recorded with your name. The packages stay marked as incomplete.' });
  if (note === null) return;
  try { await post(`/api/periods/${id}/accept-omissions`, { note }); toast('Omissions accepted.', 'success'); renderPeriod(id); } catch (e) { showError(e); }
}

async function dispatchNow(id, mode, d, confirmDraft = false) {
  const pv = d.email_preview;
  if (mode === 'send' && !confirmDraft) {
    const ok = await confirmDialog({ title: 'Send now', confirmLabel: 'Send', message: `Send "${pv.subject}" to ${pv.to.join('; ')}${pv.cc.length ? ' (CC ' + pv.cc.join('; ') + ')' : ''} with ${pv.attachments.length} attachments through Outlook?` });
    if (!ok) return;
  }
  const m = modal({ title: mode === 'draft' ? 'Creating draft' : 'Sending', body: spinner('Talking to Outlook…'), actions: [], dismissible: false });
  try {
    const res = await post(`/api/periods/${id}/dispatch`, { mode, confirm_draft_exists: confirmDraft });
    m.close();
    const msg = { draft_created: 'Draft created in Outlook Drafts with the attachments. Review and send it from Outlook.', submitted: 'Outlook accepted the email for sending. It will show as confirmed once it appears in Sent Items.',
      confirmed_sent: 'The email was found in Sent Items.', uncertain: 'The result is uncertain. Check Outlook before doing anything else.', failed: 'Outlook did not send the email: ' + (res.error || '') }[res.status] || res.status;
    toast(msg, ['failed', 'uncertain'].includes(res.status) ? 'error' : 'success', 9000);
  } catch (e) {
    m.close();
    if (e.code === 'draft_exists') {
      const ok = await confirmDialog({ title: 'A draft already exists', message: e.message, confirmLabel: 'Send anyway', danger: true });
      if (ok) return dispatchNow(id, mode, d, true);
    } else showError(e);
  }
  renderPeriod(id);
}

async function readyFolder(id) {
  try {
    const r = await post(`/api/periods/${id}/ready-folder`);
    const copyBtn = (text, label) => h('button', { class: 'btn btn-secondary btn-sm', onclick: async () => { try { await navigator.clipboard.writeText(text); toast(`${label} copied.`, 'success', 2000); } catch (_) { toast('Copy failed; select the text and press Ctrl+C.', 'warn'); } } }, 'Copy ' + label.toLowerCase());
    modal({ title: 'Ready to send manually', wide: true, body: h('div', null,
      h('p', null, 'The ZIP files and the email text are in this folder:'), h('pre', { class: 'email mono' }, r.folder),
      h('p', { class: 'muted small' }, 'Attach the ZIP files yourself: a mailto link cannot attach files.'),
      h('dl', { class: 'kv small' }, h('dt', null, 'To'), h('dd', null, r.to.join('; ')), h('dt', null, 'CC'), h('dd', null, r.cc.join('; ')), h('dt', null, 'Attach'), h('dd', null, r.attachments.join(', '))),
      h('div', { class: 'row', style: { margin: '10px 0' } }, copyBtn(r.to.join('; '), 'Recipients'), copyBtn(r.subject, 'Subject'), copyBtn(r.body, 'Body')),
      field('Subject', h('input', { class: 'input', value: r.subject, readonly: true })), h('pre', { class: 'email' }, r.body)),
    actions: [{ label: 'Open folder', onClick: async () => { const o = await post(`/api/periods/${id}/open-ready-folder`); if (!o.opened) toast('Open the folder in File Explorer using the path shown.', 'warn'); return false; } },
      { label: 'Close' }, { label: 'I have sent it: mark as sent', kind: 'primary', onClick: () => manualComplete(id, false) }] });
  } catch (e) { showError(e); }
}

async function manualComplete(id, nothing) {
  const note = await promptDialog({ title: nothing ? 'Mark complete with nothing sent' : 'Mark as sent manually', multiline: true, confirmLabel: 'Mark complete',
    label: nothing ? 'Why is nothing being sent for this period?' : 'How and when was it sent? (for example: sent from new Outlook to it-team@... at 10:15)',
    help: 'Recorded in the audit log with your name.' });
  if (note === null) return;
  try { await post(`/api/periods/${id}/manual-complete`, { note, nothing_sent: nothing }); toast('Period marked complete.', 'success'); renderPeriod(id); } catch (e) { showError(e); }
}

async function reconcile(dispatchId, periodId) {
  try { const r = await post(`/api/dispatches/${dispatchId}/reconcile`); toast(`Outlook checked: ${dispatchLabel(r.dispatch.status)}.`, 'info'); renderPeriod(periodId); } catch (e) { showError(e); }
}

async function resolve(x, periodId) {
  const outcome = select([['', 'Choose…'], ['sent', 'It was sent (I found it in Sent Items)'], ['not_sent', 'It was NOT sent (not in Sent Items or Outbox)']], '');
  const note = h('textarea', { class: 'input', rows: 3, placeholder: 'What did you check in Outlook?' });
  modal({ title: `Record the outcome of ${x.dispatch_uid}`, body: h('div', null,
    h('p', null, 'Open Outlook and look in Sent Items and the Outbox for an email with this reference. Only then record what you found.'),
    field('Outcome', outcome), field('Note', note)),
  actions: [{ label: 'Cancel' }, { label: 'Save outcome', kind: 'primary', onClick: async () => {
    await post(`/api/dispatches/${x.id}/resolve`, { outcome: outcome.value, note: note.value }); toast('Outcome recorded.', 'success'); renderPeriod(periodId);
  } }] });
}

async function showManifest(pkgId) {
  let m;
  try { m = await get(`/api/packages/${pkgId}/manifest`); } catch (e) { return showError(e); }
  const tbl = (rows, cols) => rows.length ? h('div', { class: 'table-wrap', style: { maxHeight: '260px' } }, h('table', { class: 'table small' }, h('thead', null, h('tr', null, cols.map(([, l]) => h('th', null, l)))),
    h('tbody', null, rows.map((r) => h('tr', null, cols.map(([k]) => h('td', null, typeof r[k] === 'boolean' ? (r[k] ? 'Yes' : '') : r[k] ?? ''))))))) : h('p', { class: 'muted' }, 'None');
  modal({ title: `Manifest: ${m.location}, ${m.reporting_period} (rev ${m.revision})`, wide: 'xl', body: h('div', null,
    h('dl', { class: 'kv small' }, h('dt', null, 'Generated'), h('dd', null, `${m.generated_at_local} by ${m.generated_by}`), h('dt', null, 'Included'), h('dd', null, m.inclusion_label),
      h('dt', null, 'Complete'), h('dd', null, m.complete ? 'Yes' : 'No' + (m.acceptance && m.acceptance.by ? ` (omissions accepted by ${m.acceptance.by}: ${m.acceptance.note})` : '')),
      h('dt', null, 'ZIP'), h('dd', { class: 'mono' }, `${m.zip_name || '(none)'} ${m.zip_sha256 ? 'SHA-256 ' + m.zip_sha256.slice(0, 16) + '…' : ''}`),
      m.inclusion === 'changed' ? [h('dt', null, 'Unchanged, not included'), h('dd', null, m.unchanged_not_included)] : null),
    h('h3', { style: { marginTop: '12px' } }, `Employees (${m.employees.length})`),
    tbl(m.employees, [['rhc_id', 'RHC ID'], ['name', 'Name'], ['filename', 'File in ZIP'], ['source_filename', 'Original file'], ['photo_version', 'Photo version'], ['renamed_for_duplicate', 'Renamed (duplicate)'], ['converted_from', 'Converted from'], ['change_reason', 'Why included']]),
    h('h3', { style: { marginTop: '12px' } }, `Left out (${m.exclusions.length})`), tbl(m.exclusions, [['rhc_id', 'RHC ID'], ['name', 'Name'], ['reason', 'Reason']]),
    (m.employees_without_location || []).length ? [h('h3', { style: { marginTop: '12px' } }, 'Active employees with no location (in no package)'), tbl(m.employees_without_location, [['rhc_id', 'RHC ID'], ['name', 'Name']])] : null) });
}
