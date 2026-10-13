// Settings: account, general, custom fields, users and access, integration tokens, email, backups, network, audit, about.
import { get, post, put, upload, qs } from '../api.js';
import { h, mount, icon, toast, showError, modal, confirmDialog, spinner, emptyState, fmtDateTime, fmtBytes, badge, field, select, tabs, debounce } from '../ui.js';
import { state, can, setPage, renderChangePassword, refreshSystem } from '../app.js';

const TABS = [
  ['account', 'My account', null], ['general', 'General', 'settings.manage'], ['fields', 'Custom fields', 'fields.manage'],
  ['users', 'Users and access', 'users.manage'], ['tokens', 'Integration tokens', 'users.manage'], ['email', 'Email', 'settings.manage'],
  ['backups', 'Backups and restore', 'backups.manage'], ['network', 'Network', 'settings.manage'], ['posts', 'Posts and OCR', 'settings.manage'],
  ['audit', 'Audit log', 'audit.view'], ['about', 'About', null],
];

export async function render(root, rest) {
  setPage('Settings');
  const allowed = TABS.filter(([, , p]) => !p || can(p));
  const tab = allowed.find(([k]) => k === rest[0]) ? rest[0] : allowed[0][0];
  const body = h('div');
  mount(root, tabs(allowed.map(([k, l]) => [k, l]), tab, (k) => { location.hash = '#/settings/' + k; }), body);
  mount(body, spinner());
  try { await PAGES[tab](body); } catch (e) { mount(body, h('div', { class: 'card' }, h('p', null, e.message))); }
}

const PAGES = { account, general, fields, users, tokens, email, backups, network, posts: postsSettings, audit, about };

// ---------------------------------------------------------------- posts and OCR
async function postsSettings(root) {
  const d = await get('/api/posts/settings');
  const ps = d.posts;
  const ann = h('textarea', { class: 'input', rows: 8 }); ann.value = ps.announcement_template;
  const subj = h('input', { class: 'input', value: ps.email_subject });
  const body = h('textarea', { class: 'input', rows: 10 }); body.value = ps.email_body;
  const askD = h('input', { class: 'input', value: ps.ask_designation });
  const askL = h('textarea', { class: 'input', rows: 2 }); askL.value = ps.ask_logo;
  const match = h('input', { class: 'input', value: ps.reply_subject_match });
  const folder = h('input', { class: 'input', value: ps.mail_folder || '', placeholder: 'Inbox (default) or a subfolder name' });
  const days = h('input', { class: 'input', type: 'number', value: ps.days_back });
  const minb = h('input', { class: 'input', type: 'number', value: ps.min_logo_bytes });
  const ignore = h('textarea', { class: 'input', rows: 4 }); ignore.value = (ps.ignore_logo_names || []).join('\n');
  const sender = h('input', { class: 'input', value: ps.request_sender || '', placeholder: 'Leave empty for the default Outlook account' });
  const cc = h('input', { class: 'input', value: (ps.request_cc || []).join('; ') });
  const remind = h('input', { class: 'input', type: 'number', value: ps.reminder_hours });
  const auto = h('input', { type: 'checkbox', checked: !!ps.auto_create_on_assignment });
  const tpl = h('input', { class: 'input', value: ps.template_path || '', placeholder: d.default_template });
  const tess = h('input', { class: 'input', value: d.ocr.tesseract_path || '', placeholder: 'C:\\Program Files\\Tesseract-OCR\\tesseract.exe' });
  const lang = h('input', { class: 'input', value: d.ocr.lang || 'eng' });
  const save = h('button', { class: 'btn btn-primary' }, 'Save');
  save.onclick = async () => {
    try {
      await put('/api/posts/settings', { posts: { announcement_template: ann.value, email_subject: subj.value, email_body: body.value, ask_designation: askD.value, ask_logo: askL.value,
        reply_subject_match: match.value, mail_folder: folder.value, days_back: +days.value, min_logo_bytes: +minb.value, ignore_logo_names: ignore.value, request_sender: sender.value,
        request_cc: cc.value.split(/[;,]/).map((x) => x.trim()).filter(Boolean), reminder_hours: +remind.value, auto_create_on_assignment: auto.checked, template_path: tpl.value },
      ocr: { tesseract_path: tess.value, lang: lang.value } });
      toast('Saved.', 'success'); postsSettings(root);
    } catch (e) { showError(e); }
  };
  const legacyFolder = h('input', { class: 'input', placeholder: 'C:\\Users\\...\\Desktop\\files (the old Relay Automation Hub folder)' });
  const legacyBox = h('div');
  const legacyBtn = h('button', { class: 'btn btn-secondary' }, 'Preview what would be imported');
  legacyBtn.onclick = async () => {
    try {
      const plan = await post('/api/posts-legacy/preview', { folder: legacyFolder.value });
      const decisions = { profiles: {}, posts: {} };
      const reviewRows = plan.review.map((r) => { const sel = select([['', 'Skip']].concat(r.candidates.map((c) => [c.id, c.label])), ''); sel.onchange = () => { decisions[r.kind === 'profile' ? 'profiles' : 'posts'][r.file] = sel.value ? +sel.value : undefined; };
        return h('tr', null, h('td', null, r.kind), h('td', null, r.file), h('td', null, r.reason), h('td', null, sel)); });
      const commitBtn = h('button', { class: 'btn btn-primary' }, 'Import now');
      commitBtn.onclick = async () => {
        const ok = await confirmDialog({ title: 'Import legacy Post Studio data', confirmLabel: 'Import', message: 'Logos become client logos, headshots become employee photos (new version), and log.xlsx posts become post projects (published if they were ticked Uploaded). The old folder is not changed.' });
        if (!ok) return;
        try { const r = await post('/api/posts-legacy/commit', { folder: legacyFolder.value, decisions }); mount(legacyBox, h('div', { class: 'banner banner-info' }, icon('check'), h('div', null, `Imported ${r.logos} logos, ${r.profiles} photos, ${r.posts} posts.`, r.skipped.length ? h('ul', { class: 'msgs' }, r.skipped.map((x) => h('li', null, x))) : null, r.errors.length ? h('ul', { class: 'msgs err' }, r.errors.map((x) => h('li', null, x))) : null))); }
        catch (e) { showError(e); }
      };
      mount(legacyBox, h('dl', { class: 'kv small' }, Object.entries(plan.found).map(([k, v]) => [h('dt', null, k), h('dd', { class: 'mono' }, v || h('span', { class: 'muted' }, 'not found'))])),
        h('p', null, `${plan.logos.length} logos, ${plan.profiles.length} headshots, ${plan.posts.length} posts found. ${plan.review.length} need a decision:`),
        plan.review.length ? h('div', { class: 'table-wrap', style: { maxHeight: '260px' } }, h('table', { class: 'table small' }, h('thead', null, h('tr', null, ['Kind', 'File', 'Why', 'Employee'].map((t) => h('th', null, t)))), h('tbody', null, reviewRows))) : null,
        h('div', { style: { marginTop: '10px' } }, commitBtn));
    } catch (e) { showError(e); }
  };
  mount(root,
    h('div', { class: 'card' }, h('h3', null, 'Deployment posts'),
      h('label', { class: 'check' }, auto, 'Create a post project automatically when an employee is saved with a client assignment'),
      field('Announcement text', ann, 'Placeholders: {name}, {client}, {pronoun}. Used for Copy text.'), field('Post template (PNG, 600 x 600)', tpl, 'Leave empty for the bundled template.')),
    h('div', { class: 'card' }, h('h3', null, 'Logo and designation request emails'),
      h('div', { class: 'form-grid' }, field('Subject', subj, '{client}, {first_name}, {ref} (the reference is required so replies can be matched).'), field('Send from (Outlook account email)', sender), field('CC', cc), field('Hours before a repeat request counts as a reminder', remind)),
      field('Message', body, 'Placeholders: {first_name}, {client}, {name}, {asks}.'), field('Designation line (asked only when missing)', askD), field('Logo line (asked only when missing)', askL)),
    h('div', { class: 'card' }, h('h3', null, 'Reading replies'),
      h('div', { class: 'form-grid' }, field('Subject phrase to look for', match), field('Outlook folder', folder), field('Look back (days)', days), field('Smallest logo (bytes)', minb)),
      field('Attachment name fragments that are usually signatures or icons (one per line)', ignore, 'Such attachments are not chosen automatically but can still be selected by hand.')),
    h('div', { class: 'card' }, h('h3', null, 'OCR for screenshot imports'),
      h('p', { class: 'small muted' }, d.ocr.detected ? `Tesseract found: ${d.ocr.detected}` : 'Tesseract OCR was not found. Install it (for example the UB Mannheim installer), then enter the path to tesseract.exe here.'),
      h('div', { class: 'form-grid' }, field('tesseract.exe', tess), field('Language', lang))),
    h('div', { class: 'row' }, save),
    h('div', { class: 'card' }, h('h3', null, 'Bring in data from the old Post Studio'),
      h('p', { class: 'small muted' }, 'Point this at the old files folder (it contains Data\\Posts\\log.xlsx, Raw_Files\\Posts\\saved_logos, Raw_Files\\saved_profile and Output\\Posts). Uncertain name matches are listed for you to decide; nothing is guessed.'),
      field('Old folder', legacyFolder), legacyBtn, legacyBox));
}

// ---------------------------------------------------------------- account
async function account(root) {
  const u = state.user;
  const actions = state.meta ? state.meta.actions : {};
  mount(root, h('div', { class: 'card' }, h('h3', null, 'My account'),
    h('dl', { class: 'kv' }, h('dt', null, 'Name'), h('dd', null, u.display_name), h('dt', null, 'Email'), h('dd', null, u.email),
      h('dt', null, 'Role'), h('dd', null, u.role_label), h('dt', null, 'Locations'), h('dd', null, u.locations.length ? u.locations.join(', ') : 'All locations'),
      h('dt', null, 'Date of birth access'), h('dd', null, { full: 'Full date', day_month: 'Day and month only', none: 'Hidden' }[u.dob_access]),
      h('dt', null, 'Hidden fields'), h('dd', null, u.restricted_fields.length ? u.restricted_fields.join(', ') : 'None'),
      h('dt', null, 'I can'), h('dd', null, h('ul', { class: 'msgs' }, u.actions.map((a) => h('li', null, actions[a] || a))))),
    h('button', { class: 'btn btn-secondary', style: { marginTop: '12px' }, onclick: () => renderChangePassword(false) }, 'Change password')));
}

// ---------------------------------------------------------------- general
async function general(root) {
  const g = await get('/api/settings/general');
  const locs = h('textarea', { class: 'input', rows: 3 }); locs.value = g.locations.join('\n');
  const tz = h('input', { class: 'input', value: g.timezone });
  const order = select([['DMY', 'Day first (31-12-2026), Indian style'], ['MDY', 'Month first (12-31-2026)']], g.date_order);
  const idle = h('input', { class: 'input', type: 'number', value: g.session_idle_minutes });
  const abs = h('input', { class: 'input', type: 'number', value: g.session_absolute_hours });
  const lim = g.limits || {};
  const limInputs = Object.fromEntries(Object.entries(lim).map(([k, v]) => [k, h('input', { class: 'input', type: 'number', value: v })]));
  const labels = { max_upload_mb: 'Largest spreadsheet upload (MB)', max_image_mb: 'Largest single image (MB)', max_image_pixels: 'Largest image (pixels)', max_zip_entries: 'Most files in one ZIP',
    max_zip_total_mb: 'Largest ZIP contents (MB)', max_zip_ratio: 'ZIP compression ratio limit', max_pdf_pages: 'Most pages in one PDF', max_import_rows: 'Most rows in one import' };
  mount(root, h('div', { class: 'card' }, h('h3', null, 'General'),
    h('div', { class: 'form-grid' },
      field('Locations (one per line)', locs, 'Locations in use by employees cannot be removed. New locations can be added to tasks in Task settings.'),
      h('div', null, field('Timezone', tz, 'Default Asia/Kolkata. Used for schedules, bench days and dates.'), field('How to read dates like 03/04/2026', order)),
      field('Sign out after inactivity (minutes)', idle), field('Maximum session length (hours)', abs),
      h('div', { class: 'form-section' }, 'Upload limits'),
      Object.entries(limInputs).map(([k, el]) => field(labels[k] || k, el))),
    h('button', { class: 'btn btn-primary', onclick: async () => {
      try {
        await put('/api/settings/general', { locations: locs.value.split('\n').map((x) => x.trim()).filter(Boolean), timezone: tz.value.trim(), date_order: order.value,
          session_idle_minutes: +idle.value, session_absolute_hours: +abs.value, limits: Object.fromEntries(Object.entries(limInputs).map(([k, el]) => [k, +el.value])) });
        toast('Settings saved.', 'success'); state.meta = await get('/api/meta');
      } catch (e) { showError(e); }
    } }, 'Save')));
}

// ---------------------------------------------------------------- custom fields
async function fields(root) {
  const d = await get('/api/fields');
  const add = h('button', { class: 'btn btn-primary' }, icon('plus'), 'Add field');
  add.onclick = () => fieldDialog(null, d.types, root);
  mount(root, h('div', { class: 'card' }, h('div', { class: 'card-title' }, h('h3', null, 'Custom fields'), add),
    h('p', { class: 'muted' }, 'Custom fields appear in the employee form, the profile, the table (if enabled), imports and exports. Fields can be switched off but not deleted, so history is kept.'),
    d.custom.length ? h('div', { class: 'table-wrap' }, h('table', { class: 'table' }, h('thead', null, h('tr', null, ['Name', 'Type', 'Options', 'Required', 'Sensitive', 'In table', 'Status', ''].map((x) => h('th', null, x)))),
      h('tbody', null, d.custom.map((f) => h('tr', null, h('td', null, f.label, h('div', { class: 'mono muted' }, f.key)), h('td', null, d.types[f.field_type] || f.field_type),
        h('td', { class: 'small' }, f.options.join(', ')), h('td', null, f.required ? 'Yes' : ''), h('td', null, f.sensitive ? 'Yes' : ''), h('td', null, f.show_in_table ? 'Yes' : ''),
        h('td', null, f.active ? badge('Active', 'green') : badge('Switched off')),
        h('td', null, h('button', { class: 'btn btn-link btn-sm', onclick: () => fieldDialog(f, d.types, root) }, 'Edit'))))))) :
      emptyState('No custom fields yet', 'Add text, number, date, dropdown or yes/no fields.')));
}

function fieldDialog(f, types, root) {
  const label = h('input', { class: 'input', value: f ? f.label : '' });
  const type = select(Object.entries(types), f ? f.field_type : 'text');
  const opts = h('textarea', { class: 'input', rows: 4, placeholder: 'One option per line' }); opts.value = f ? f.options.join('\n') : '';
  const req = h('input', { type: 'checkbox', checked: f ? !!f.required : false });
  const sens = h('input', { type: 'checkbox', checked: f ? !!f.sensitive : false });
  const table = h('input', { type: 'checkbox', checked: f ? !!f.show_in_table : true });
  const active = h('input', { type: 'checkbox', checked: f ? !!f.active : true });
  const help = h('input', { class: 'input', value: f ? f.help_text || '' : '' });
  const optField = field('Dropdown options', opts);
  const sync = () => { optField.style.display = type.value === 'dropdown' ? '' : 'none'; };
  type.onchange = sync; sync();
  modal({ title: f ? `Edit ${f.label}` : 'Add custom field', body: h('div', null, field('Field name', label), field('Type', type, f ? 'The type can only change while no record has a value.' : ''), optField, field('Help text', help),
    h('label', { class: 'check' }, req, 'Required when adding or editing an employee'),
    h('label', { class: 'check' }, sens, 'Sensitive (cannot be shared with integration tokens)'),
    h('label', { class: 'check' }, table, 'Available as a table column'),
    f ? h('label', { class: 'check' }, active, 'Active (switch off to hide it everywhere; values are kept)') : null),
  actions: [{ label: 'Cancel' }, { label: 'Save', kind: 'primary', onClick: async () => {
    const payload = { label: label.value, field_type: type.value, options: opts.value.split('\n'), required: req.checked, sensitive: sens.checked, show_in_table: table.checked, help_text: help.value, active: active.checked };
    if (f) await put(`/api/fields/${f.id}`, payload); else await post('/api/fields', payload);
    toast('Field saved.', 'success'); state.meta = await get('/api/meta'); fields(root);
  } }] });
}

// ---------------------------------------------------------------- users
async function users(root) {
  const d = await get('/api/users');
  const add = h('button', { class: 'btn btn-primary' }, icon('plus'), 'Add user');
  add.onclick = () => userDialog(null, d, root);
  mount(root, h('div', { class: 'card' }, h('div', { class: 'card-title' }, h('h3', null, 'Users'), add),
    h('p', { class: 'muted' }, 'Everyone signs in with their email and their own password. New users get a one-time temporary password and must choose their own at first sign-in.'),
    h('div', { class: 'table-wrap' }, h('table', { class: 'table' }, h('thead', null, h('tr', null, ['User', 'Role', 'Locations', 'Restrictions', 'Last sign-in', 'Status', ''].map((x) => h('th', null, x)))),
      h('tbody', null, d.users.map((u) => h('tr', null, h('td', null, u.display_name, h('div', { class: 'muted small' }, u.email)), h('td', null, u.role_label),
        h('td', null, u.location_scope.length ? u.location_scope.join(', ') : 'All'),
        h('td', { class: 'small' }, [u.restricted_fields.length ? `Hidden: ${u.restricted_fields.join(', ')}` : '', u.role !== 'admin' ? `DOB: ${u.dob_access.replace('_', ' ')}` : '',
          u.extra_permissions.length ? `Extra: ${u.extra_permissions.join(', ')}` : '', u.revoked_permissions.length ? `Removed: ${u.revoked_permissions.join(', ')}` : ''].filter(Boolean).join(' · ')),
        h('td', { class: 'small' }, fmtDateTime(u.last_login_at)),
        h('td', null, u.is_active ? badge('Active', 'green') : badge('Disabled'), u.locked ? badge('Locked', 'red') : null, u.must_change_password ? h('div', { class: 'small muted' }, 'Must change password') : null),
        h('td', { class: 'nowrap' }, h('button', { class: 'btn btn-link btn-sm', onclick: () => userDialog(u, d, root) }, 'Edit'),
          h('button', { class: 'btn btn-link btn-sm', onclick: () => resetPw(u, root) }, 'Reset password')))))))),
    h('div', { class: 'card' }, h('h3', null, 'About permissions'),
      h('ul', { class: 'msgs' }, h('li', null, 'Permissions are enforced by the server for every page, download, photo, import and export.'),
        h('li', null, 'Windows administrators on this computer can read the data folder directly; application permissions cannot prevent that. Protect the computer and the data folder with Windows accounts.'),
        h('li', null, 'Fetch-only access for other local tools uses integration tokens (see the Integration tokens tab); tokens cannot sign in to this interface.'))));
}

function userDialog(u, d, root) {
  const email = h('input', { class: 'input', type: 'email', value: u ? u.email : '', disabled: !!u });
  const name = h('input', { class: 'input', value: u ? u.display_name : '' });
  const role = select(Object.entries(d.roles).map(([k, v]) => [k, v.label]), u ? u.role : 'viewer');
  const locBoxes = d.locations.map((l) => { const c = h('input', { type: 'checkbox', checked: u ? u.location_scope.includes(l) : false }); return { l, c, el: h('label', { class: 'check' }, c, l) }; });
  const dob = select([['day_month', 'Day and month only'], ['full', 'Full date'], ['none', 'Hidden']], u ? u.dob_access : 'day_month');
  const fieldKeys = state.meta.fields.filter((f) => !['id', 'rhc_id', 'full_name', 'photo'].includes(f.key));
  const restr = fieldKeys.map((f) => { const c = h('input', { type: 'checkbox', checked: u ? u.restricted_fields.includes(f.key) : false }); return { k: f.key, c, el: h('label', { class: 'check small' }, c, f.label) }; });
  const grantable = Object.entries(d.actions).filter(([k]) => !d.admin_only.includes(k));
  const permBoxes = grantable.map(([k, label]) => {
    const inRole = () => d.roles[role.value].actions.includes(k);
    const c = h('input', { type: 'checkbox' });
    const set = () => { c.checked = inRole() ? !(u && u.revoked_permissions.includes(k)) : !!(u && u.extra_permissions.includes(k)); };
    set();
    return { k, c, inRole, set, el: h('label', { class: 'check small' }, c, label) };
  });
  role.onchange = () => permBoxes.forEach((p) => { p.c.checked = p.inRole(); });
  const active = h('input', { type: 'checkbox', checked: u ? u.is_active : true });
  const body = h('div', null, h('div', { class: 'form-grid' }, field('Email (username)', email), field('Name', name), field('Role', role,
    'Administrator: everything. Editor: edit employees, imports, photos. Viewer: read-only. Export Operator: exports and task operations.'),
    field('Date of birth access', dob), h('div', { class: 'span-2' }, field('Limit to locations (none ticked = all locations)', h('div', { class: 'row' }, locBoxes.map((x) => x.el))))),
  h('details', null, h('summary', null, 'Permissions for this user (adjust the role defaults)'), h('div', { class: 'grid-2' }, permBoxes.map((p) => p.el)),
    h('p', { class: 'muted small' }, 'For Viewers, tick "Export employee data to Excel" to allow exports. Administrator-only permissions come from the Administrator role.')),
  h('details', null, h('summary', null, 'Hide fields from this user'), h('div', { class: 'grid-3' }, restr.map((x) => x.el))),
  u ? h('label', { class: 'check' }, active, 'Account active (untick to block sign-in; history is kept)') : null);
  modal({ title: u ? `Edit ${u.display_name}` : 'Add user', wide: true, body, actions: [{ label: 'Cancel' }, { label: 'Save', kind: 'primary', onClick: async () => {
    const r = role.value;
    const extra = permBoxes.filter((p) => p.c.checked && !d.roles[r].actions.includes(p.k)).map((p) => p.k);
    const revoked = permBoxes.filter((p) => !p.c.checked && d.roles[r].actions.includes(p.k)).map((p) => p.k);
    const payload = { display_name: name.value, role: r, location_scope: locBoxes.filter((x) => x.c.checked).map((x) => x.l), dob_access: dob.value,
      restricted_fields: restr.filter((x) => x.c.checked).map((x) => x.k), extra_permissions: r === 'admin' ? [] : extra, revoked_permissions: r === 'admin' ? [] : revoked };
    if (u) { payload.is_active = active.checked; await put(`/api/users/${u.id}`, payload); toast('User saved.', 'success'); }
    else { const res = await post('/api/users', { email: email.value, ...payload }); showSecret('User created', `Give ${res.user.email} this temporary password. They must change it when they first sign in. It is shown only once.`, res.temporary_password); }
    users(root);
  } }] });
}

async function resetPw(u, root) {
  const ok = await confirmDialog({ title: 'Reset password', confirmLabel: 'Reset', message: `Create a new temporary password for ${u.email}? They will be signed out and must choose a new password.` });
  if (!ok) return;
  try { const r = await post(`/api/users/${u.id}/reset-password`); showSecret('Temporary password', `Give this to ${u.email} securely. It is shown only once.`, r.temporary_password); users(root); } catch (e) { showError(e); }
}

function showSecret(title, text, secret) {
  modal({ title, body: h('div', null, h('p', null, text), h('pre', { class: 'email mono', style: { fontSize: '15px' } }, secret)),
    actions: [{ label: 'Copy', onClick: async () => { try { await navigator.clipboard.writeText(secret); toast('Copied.', 'success', 2000); } catch (_) { toast('Select the text and press Ctrl+C.', 'warn'); } return false; } }, { label: 'Done', kind: 'primary' }] });
}

// ---------------------------------------------------------------- tokens
async function tokens(root) {
  const list = await get('/api/tokens');
  const add = h('button', { class: 'btn btn-primary' }, icon('plus'), 'Create token');
  add.onclick = () => tokenDialog(root);
  mount(root, h('div', { class: 'card' }, h('div', { class: 'card-title' }, h('h3', null, 'Read-only integration tokens (fetch-only)'), add),
    h('p', { class: 'muted' }, 'For other programs on this computer or office network that need to read active employee data. Tokens are read-only, limited to chosen locations and fields, can never read date of birth or notes, cannot sign in to this interface, and can be revoked at any time. Only a hash of each token is stored.'),
    list.length ? h('div', { class: 'table-wrap' }, h('table', { class: 'table' }, h('thead', null, h('tr', null, ['Name', 'Starts with', 'Locations', 'Fields', 'Photos', 'Created', 'Last used', ''].map((x) => h('th', null, x)))),
      h('tbody', null, list.map((t) => h('tr', null, h('td', null, t.name), h('td', { class: 'mono' }, t.prefix + '…'), h('td', null, t.location_scope.length ? t.location_scope.join(', ') : 'All'),
        h('td', { class: 'small' }, t.allowed_fields.join(', ')), h('td', null, t.include_photos ? 'Yes' : 'No'), h('td', { class: 'small' }, `${fmtDateTime(t.created_at)} by ${t.created_by}`),
        h('td', { class: 'small' }, fmtDateTime(t.last_used_at)),
        h('td', null, t.revoked_at ? badge('Revoked') : h('button', { class: 'btn btn-link btn-sm', onclick: async () => {
          if (!(await confirmDialog({ title: 'Revoke token', message: `Programs using "${t.name}" will stop working immediately.`, confirmLabel: 'Revoke', danger: true }))) return;
          try { await post(`/api/tokens/${t.id}/revoke`); toast('Token revoked.', 'success'); tokens(root); } catch (e) { showError(e); }
        } }, 'Revoke'))))))) : emptyState('No tokens', 'Create a token only when another local tool needs read-only access.'),
    h('h3', { style: { marginTop: '16px' } }, 'How a program uses a token'),
    h('pre', { class: 'email mono' }, 'GET http://<this computer>:<port>/api/fetch/v1/employees\nAuthorization: Bearer rhub_...\n\nOptional: ?location=Ahmedabad\nPhotos (if allowed): /api/fetch/v1/employees/<RHC ID>/photo')));
}

function tokenDialog(root) {
  const name = h('input', { class: 'input', placeholder: 'For example: Birthday email tool' });
  const locBoxes = state.meta.locations.map((l) => { const c = h('input', { type: 'checkbox' }); return { l, c, el: h('label', { class: 'check' }, c, l) }; });
  const allowed = state.meta.fields.filter((f) => !f.sensitive && !['photo', 'date_of_birth', 'notes'].includes(f.key));
  const defaults = ['rhc_id', 'full_name', 'designation', 'department', 'official_email', 'location'];
  const fb = allowed.map((f) => { const c = h('input', { type: 'checkbox', checked: defaults.includes(f.key) }); return { k: f.key, c, el: h('label', { class: 'check small' }, c, f.label) }; });
  const photos = h('input', { type: 'checkbox' });
  modal({ title: 'Create integration token', wide: true, body: h('div', null, field('Name', name), field('Locations (none ticked = all)', h('div', { class: 'row' }, locBoxes.map((x) => x.el))),
    field('Fields the token may read', h('div', { class: 'grid-3' }, fb.map((x) => x.el))), h('label', { class: 'check' }, photos, 'Allow reading current photos')),
  actions: [{ label: 'Cancel' }, { label: 'Create', kind: 'primary', onClick: async () => {
    const r = await post('/api/tokens', { name: name.value, location_scope: locBoxes.filter((x) => x.c.checked).map((x) => x.l), allowed_fields: fb.filter((x) => x.c.checked).map((x) => x.k), include_photos: photos.checked });
    showSecret('Token created', 'Copy this token into the other program now. It is shown only once.', r.secret); tokens(root);
  } }] });
}

// ---------------------------------------------------------------- email
async function email(root) {
  const s = await get('/api/settings/outlook');
  const modes = [['classic', 'Classic Outlook (Windows automation)', 'Creates drafts and sends through your existing Outlook profile. Requires classic Outlook for Windows, not the new Outlook.'],
    ['manual', 'Manual', 'For new Outlook or Outlook on the web: packages are placed in a ready folder with the subject and body to copy; you send and mark the period as sent.'],
    ['dryrun', 'Dry-run (testing)', 'Nothing is ever sent. Messages are written to a folder so you can check them.']];
  const radios = modes.map(([k, l, d]) => { const r = h('input', { type: 'radio', name: 'mode', value: k, checked: s.mode === k }); return { k, r, el: h('label', { class: 'check card', style: { margin: '0 0 8px', padding: '10px' } }, r, h('div', null, h('strong', null, l), h('div', { class: 'muted small' }, d))) }; });
  const scen = select([['success', 'Normal: send succeeds and appears in Sent Items'], ['fail_before_send', 'Outlook refuses before sending'], ['uncertain', 'Unclear result (tests uncertain handling)'], ['stuck_in_outbox', 'Stays in Outbox']], s.dryrun_scenario);
  const statusBox = h('div');
  mount(root, h('div', { class: 'card' }, h('h3', null, 'Email mode'), radios.map((x) => x.el),
    field('Dry-run scenario', scen, 'Only used in dry-run mode, for testing how the app handles each outcome.'),
    !s.is_windows ? h('p', { class: 'muted small' }, 'This computer is not running Windows, so classic Outlook automation is not available here.') : null,
    h('div', { class: 'row' }, h('button', { class: 'btn btn-primary', onclick: async () => {
      const m = radios.find((x) => x.r.checked).k;
      if (m !== s.mode && !(await confirmDialog({ title: 'Change email mode', message: 'Automatic sending will be switched off for all tasks until an administrator turns it on again.', confirmLabel: 'Change mode' }))) return;
      try { await put('/api/settings/outlook', { mode: m, dryrun_scenario: scen.value }); toast('Email settings saved.', 'success'); refreshSystem(); email(root); } catch (e) { showError(e); }
    } }, 'Save'), h('button', { class: 'btn btn-secondary', onclick: async () => {
      mount(statusBox, spinner('Checking Outlook…'));
      try { const st = await get('/api/outlook/status'); mount(statusBox, h('div', { class: 'detail-box', style: { marginTop: '10px' } }, st.available ? 'Available. ' : 'Not available. ', st.message || '',
        st.accounts && st.accounts.length ? `\nAccounts: ${st.accounts.map((a) => a.smtp).join(', ')}` : '', st.new_outlook_hint ? '\n' + st.new_outlook_hint : '')); } catch (e) { mount(statusBox, ''); showError(e); }
    } }, 'Check Outlook now')), statusBox,
    h('p', { class: 'muted small', style: { marginTop: '12px' } }, 'This app never uses SMTP passwords, Microsoft Graph or cloud services, and never bypasses Outlook security prompts or company policies.')));
}

// ---------------------------------------------------------------- backups
async function backups(root) {
  const [cfg, list] = await Promise.all([get('/api/settings/backup'), get('/api/backups')]);
  const dest = h('input', { class: 'input', value: cfg.destination || '', placeholder: cfg.default_destination });
  const daily = h('input', { type: 'checkbox', checked: cfg.daily_enabled });
  const time = h('input', { class: 'input', type: 'time', value: cfg.daily_time });
  const keep = h('input', { class: 'input', type: 'number', value: cfg.retention });
  const pk = h('input', { type: 'checkbox', checked: cfg.include_exports });
  const now = h('button', { class: 'btn btn-primary' }, 'Back up now');
  now.onclick = async () => { now.disabled = true; now.textContent = 'Backing up and verifying…'; try { const r = await post('/api/backups'); toast(`Backup created and verified: ${r.path}`, 'success', 8000); backups(root); } catch (e) { showError(e); } finally { now.disabled = false; now.textContent = 'Back up now'; } };
  const restoreInput = h('input', { type: 'file', accept: '.zip', class: 'hidden' });
  restoreInput.onchange = async () => {
    if (!restoreInput.files.length) return;
    const fd = new FormData(); fd.append('file', restoreInput.files[0]);
    const m = modal({ title: 'Checking backup', body: spinner('Verifying every file…'), actions: [], dismissible: false });
    try { const r = await upload('/api/backups/inspect-upload', fd); m.close(); restoreDialog(r, { upload_ref: r.upload_ref }); } catch (e) { m.close(); showError(e); }
  };
  mount(root,
    h('div', { class: 'card' }, h('div', { class: 'card-title' }, h('h3', null, 'Backups'), h('div', { class: 'row' }, now)),
      h('p', { class: 'muted' }, 'A full backup contains a consistent snapshot of the database (all employees, custom fields, templates, users, settings and task history), every photo version and thumbnail, and the prepared package snapshots. Each backup is verified after it is written.'),
      h('div', { class: 'form-grid' }, field('Backup folder', dest, `Leave empty to use ${cfg.default_destination}. An external drive such as E:\\RelayHubBackups works well. It must not be inside the photos or exports folders.`),
        h('div', null, h('label', { class: 'check' }, daily, 'Automatic daily backup while the app is running'), field('Daily backup time', time), field('Daily backups to keep', keep)),
        h('label', { class: 'check' }, pk, 'Include prepared package snapshots')),
      h('button', { class: 'btn btn-secondary', onclick: async () => { try { await put('/api/settings/backup', { destination: dest.value, daily_enabled: daily.checked, daily_time: time.value, retention: +keep.value, include_exports: pk.checked }); toast('Backup settings saved.', 'success'); } catch (e) { showError(e); } } }, 'Save backup settings')),
    h('div', { class: 'card' }, h('div', { class: 'card-title' }, h('h3', null, 'Backup history'), h('div', null, h('button', { class: 'btn btn-secondary', onclick: () => restoreInput.click() }, 'Restore from a backup file…'), restoreInput)),
      list.length ? h('div', { class: 'table-wrap' }, h('table', { class: 'table' }, h('thead', null, h('tr', null, ['When', 'Type', 'File', 'Size', 'Status', ''].map((x) => h('th', null, x)))),
        h('tbody', null, list.map((b) => h('tr', null, h('td', { class: 'small nowrap' }, fmtDateTime(b.created_at), h('div', { class: 'muted' }, b.created_by)), h('td', null, b.kind),
          h('td', { class: 'small mono', style: { wordBreak: 'break-all' } }, b.path || ''), h('td', null, fmtBytes(b.bytes)),
          h('td', null, badge(b.status, b.status === 'verified' ? 'green' : b.status.includes('fail') ? 'red' : 'grey'), b.notes ? h('div', { class: 'small muted' }, b.notes) : null),
          h('td', { class: 'nowrap' }, b.status === 'verified' ? [h('button', { class: 'btn btn-link btn-sm', onclick: async () => { try { const r = await post(`/api/backups/${b.id}/verify`); toast(r.ok ? 'Backup verified: all files match.' : 'Problems: ' + r.problems.join('; '), r.ok ? 'success' : 'error'); } catch (e) { showError(e); } } }, 'Verify'),
            h('button', { class: 'btn btn-link btn-sm', onclick: async () => { try { const r = await post(`/api/backups/${b.id}/inspect`); restoreDialog(r, { backup_id: b.id }); } catch (e) { showError(e); } } }, 'Restore…')] : null)))))) :
        emptyState('No backups yet', 'Click "Back up now" to create the first one.')),
    h('div', { class: 'card' }, h('h3', null, 'Moving to another computer'),
      h('ol', { class: 'msgs' }, h('li', null, 'Recommended: create a backup here, install the app on the new computer, and restore the backup there.'),
        h('li', null, 'Alternatively stop the app here (About > Stop application), then copy the whole data folder while it is stopped, and point the new installation at it.'),
        h('li', null, 'After a restore or move, automatic sending stays off until an administrator re-checks the sender, recipients and task ownership.'),
        h('li', null, 'Never run two copies against the same data, and do not keep the live data folder in OneDrive or SharePoint.'))));
}

function restoreDialog(r, ref) {
  const m = r.manifest || {};
  const body = h('div', null,
    r.ok ? h('p', null, 'The backup passed all checks.') : h('ul', { class: 'msgs err' }, r.problems.map((p) => h('li', null, p))),
    (r.warnings || []).length ? h('ul', { class: 'msgs warn' }, r.warnings.map((w) => h('li', null, w))) : null,
    h('dl', { class: 'kv small' }, h('dt', null, 'Created'), h('dd', null, `${fmtDateTime(m.created_at)} by ${m.created_by || ''}`), h('dt', null, 'On computer'), h('dd', null, m.source_host || ''),
      h('dt', null, 'Contents'), h('dd', null, m.counts ? `${m.counts.employees} employees, ${m.counts.photos} photo versions, ${m.counts.packages} packages, ${m.counts.users} users` : ''),
      h('dt', null, 'Files'), h('dd', null, r.file_count)),
    r.ok ? h('div', { class: 'banner banner-warn', style: { margin: '12px 0 0' } }, icon('alert'), h('div', null, 'Restoring replaces ALL current data with this backup. A safety backup of the current data is taken first. Everyone will need to sign in again, and automatic sending will be switched off until re-checked.')) : null);
  const input = h('input', { class: 'input', placeholder: 'Type RESTORE' });
  if (r.ok) body.appendChild(field('Type RESTORE to continue', input));
  modal({ title: 'Restore from backup', wide: true, body, actions: r.ok ? [{ label: 'Cancel' }, { label: 'Restore now', kind: 'danger', onClick: async () => {
    if (input.value.trim() !== 'RESTORE') { toast('Type RESTORE exactly.', 'error'); return false; }
    await post('/api/backups/restore', { ...ref, confirm_text: 'RESTORE' });
    toast('Restore complete. Please sign in again.', 'success', 8000);
    setTimeout(() => location.reload(), 1500);
  } }] : [{ label: 'Close' }] });
}

// ---------------------------------------------------------------- network
async function network(root) {
  const n = await get('/api/settings/network');
  const on = h('input', { type: 'checkbox', checked: n.lan_enabled });
  const port = h('input', { class: 'input', type: 'number', value: n.port });
  const nets = h('textarea', { class: 'input mono', rows: 3 }); nets.value = (n.allowed_networks || []).join('\n');
  mount(root, h('div', { class: 'card' }, h('h3', null, 'Office network access'),
    h('p', null, 'By default only this computer can open Relay Employee Hub. You can allow other computers on the office network to use the same central database. The app is never exposed to the internet.'),
    h('div', { class: 'banner banner-warn', style: { margin: '0 0 12px' } }, icon('alert'), h('div', null, 'Office network access uses plain HTTP. Enable it only on a trusted office network, and allow the port in Windows Firewall for private networks only. A restart of the app is needed after changing these settings.')),
    h('label', { class: 'check' }, on, h('strong', null, 'Allow access from the office network')),
    h('div', { class: 'form-grid' }, field('Port', port), field('Allowed private networks (one per line)', nets, 'For example 192.168.1.0/24. Public addresses are always refused.')),
    h('p', { class: 'muted small' }, `Currently listening on ${n.currently_bound}. Other computers would use http://<this computer's name or IP>:${n.port}/`),
    h('button', { class: 'btn btn-primary', onclick: async () => {
      let confirm = false;
      if (on.checked && !n.lan_enabled) { confirm = await confirmDialog({ title: 'Enable office network access', message: 'Other computers on the allowed networks will be able to reach the sign-in page. Everyone still needs their own account.', confirmLabel: 'Enable' }); if (!confirm) return; }
      try { await put('/api/settings/network', { lan_enabled: on.checked, confirm, port: +port.value, allowed_networks: nets.value.split('\n').map((x) => x.trim()).filter(Boolean) }); toast('Saved. Restart Relay Employee Hub to apply.', 'success', 8000); network(root); } catch (e) { showError(e); }
    } }, 'Save')));
}

// ---------------------------------------------------------------- audit
async function audit(root) {
  let q = ''; let offset = 0;
  const box = h('div');
  const search = h('input', { class: 'input', type: 'search', placeholder: 'Search action, user or details', style: { width: '320px' } });
  search.addEventListener('input', debounce(() => { q = search.value; offset = 0; load(); }, 300));
  mount(root, h('div', { class: 'card' }, h('div', { class: 'card-title' }, h('h3', null, 'Audit log'), search), box));
  async function load() {
    mount(box, spinner());
    try {
      const r = await get('/api/audit?' + qs({ q, limit: 100, offset }));
      mount(box, r.rows.length ? h('div', { class: 'table-wrap', style: { maxHeight: '65vh' } }, h('table', { class: 'table small' }, h('thead', null, h('tr', null, ['When', 'Who', 'Action', 'Item', 'Details'].map((x) => h('th', null, x)))),
        h('tbody', null, r.rows.map((a) => h('tr', null, h('td', { class: 'nowrap' }, fmtDateTime(a.ts)), h('td', null, a.actor, a.ip ? h('div', { class: 'muted' }, a.ip) : null), h('td', null, a.action),
          h('td', null, `${a.entity || ''} ${a.entity_id || ''}`), h('td', { class: 'mono', style: { maxWidth: '520px', whiteSpace: 'pre-wrap', wordBreak: 'break-word' } }, a.details ? JSON.stringify(a.details).slice(0, 600) : '')))))) : emptyState('No matching entries'),
      h('div', { class: 'pager' }, h('span', null, `${r.total} entries`), h('div', { class: 'row' },
        h('button', { class: 'btn btn-secondary btn-sm', disabled: offset === 0, onclick: () => { offset = Math.max(0, offset - 100); load(); } }, 'Newer'),
        h('button', { class: 'btn btn-secondary btn-sm', disabled: offset + 100 >= r.total, onclick: () => { offset += 100; load(); } }, 'Older'))));
    } catch (e) { mount(box, h('p', null, e.message)); }
  }
  load();
}

// ---------------------------------------------------------------- about
async function dependenciesCard() {
  let d;
  try { d = await get('/api/system/dependencies'); } catch (e) { return h('div', { class: 'card' }, h('p', null, e.message)); }
  return h('div', { class: 'card' }, h('h3', null, 'Dependencies and bundled assets'),
    h('p', { class: 'small muted' }, 'Checked just now on this computer. Nothing is reported as bundled unless the file really exists.'),
    h('div', { class: 'table-wrap' }, h('table', { class: 'table small' }, h('thead', null, h('tr', null, ['Component', 'Status', 'Details', 'Needed for', 'How to fix'].map((t) => h('th', null, t)))),
      h('tbody', null, d.items.map((i) => h('tr', null, h('td', null, i.label), h('td', null, h('span', { class: i.ok ? 'dep-ok' : i.optional ? 'dep-opt' : 'dep-bad' }, i.ok ? 'OK' : i.optional ? 'Optional, missing' : 'Missing')),
        h('td', { class: 'mono', style: { maxWidth: '360px', wordBreak: 'break-all' } }, i.detail), h('td', null, i.needed_for), h('td', null, i.ok ? '' : i.fix)))))));
}

async function about(root) {
  const s = await get('/api/system');
  const depCard = await dependenciesCard();
  mount(root, depCard, h('div', { class: 'card' }, h('h3', null, 'Relay Employee Hub'),
    h('dl', { class: 'kv' }, h('dt', null, 'Version'), h('dd', null, s.version), h('dt', null, 'Database schema'), h('dd', null, s.schema_version),
      s.data_dir ? [h('dt', null, 'Data folder'), h('dd', { class: 'mono' }, s.data_dir), h('dt', null, 'Computer'), h('dd', null, s.host), h('dt', null, 'Listening on'), h('dd', null, s.bound),
        h('dt', null, 'Records'), h('dd', null, `${s.counts.employees} employee records (${s.counts.active} active), ${s.counts.photos} photo versions, ${s.counts.users} users`),
        h('dt', null, 'Scheduler'), h('dd', null, s.scheduler.last_tick ? `Last run ${fmtDateTime(s.scheduler.last_tick)}` : 'Not run yet', s.scheduler.last_error ? h('div', { style: { color: 'var(--red)' } }, s.scheduler.last_error) : null)] : null),
    h('p', { class: 'muted small', style: { marginTop: '12px' } }, 'All data stays on this computer. The interface uses no internet services; only Outlook sending (when enabled) leaves this computer. Third-party licence notices are in THIRD_PARTY_LICENSES.md in the installation folder.'),
    can('settings.manage') ? h('button', { class: 'btn btn-secondary', style: { marginTop: '8px' }, onclick: async () => {
      if (!(await confirmDialog({ title: 'Stop Relay Employee Hub', message: 'The application will stop for everyone. Scheduled tasks and automatic backups will not run until it is started again (double-click the desktop shortcut).', confirmLabel: 'Stop', danger: true }))) return;
      try { const r = await post('/api/system/shutdown'); toast(r.stopping ? 'Stopping. You can close this tab.' : 'Stop is only available when started from the launcher.', r.stopping ? 'success' : 'warn'); } catch (e) { showError(e); }
    } }, 'Stop application') : null));
}
