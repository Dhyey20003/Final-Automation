// Application shell: sign-in, first-run setup, navigation and routing.
import { get, post, setCsrf } from './api.js';
import { h, icon, mount, toast, showError, field, modal } from './ui.js';

export const state = { user: null, meta: null, system: null, route: null };
export const can = (action) => !!(state.user && state.user.actions.includes(action));

const SECTIONS = [
  { key: 'employees', label: 'Employees', icon: 'users', perm: 'employees.view', load: () => import('./pages/employees.js') },
  { key: 'imports', label: 'Import Data', icon: 'upload', perm: 'imports.run', load: () => import('./pages/imports.js') },
  { key: 'photos', label: 'Photos', icon: 'photo', perm: 'photos.view', load: () => import('./pages/photos.js') },
  { key: 'posts', label: 'Posts', icon: 'post', perm: 'posts.view', load: () => import('./pages/posts.js') },
  { key: 'videos', label: 'Videos', icon: 'video', perm: 'videos.view', load: () => import('./pages/videos.js') },
  { key: 'tasks', label: 'Tasks and Exports', icon: 'tasks', perm: 'tasks.view', load: () => import('./pages/tasks.js') },
  { key: 'settings', label: 'Settings', icon: 'settings', perm: null, load: () => import('./pages/settings.js') },
];

const app = document.getElementById('app');

async function boot() {
  let st;
  try { st = await get('/api/auth/state', { noAuthRedirect: true }); }
  catch (e) { mount(app, h('div', { class: 'auth' }, h('div', { class: 'auth-card' }, h('h1', null, 'Cannot connect'), h('p', null, e.message)))); return; }
  if (st.setup_required) return renderSetup(st);
  if (!st.user) return renderLogin(st);
  state.user = st.user;
  setCsrf(st.user.csrf_token);
  if (st.user.must_change_password) return renderChangePassword(true);
  try {
    [state.meta, state.system] = await Promise.all([get('/api/meta'), get('/api/system')]);
    window.HUB_TZ = state.meta.timezone;
  } catch (e) { showError(e); }
  renderShell();
}

function authCard(title, subtitle, ...content) {
  return h('div', { class: 'auth' }, h('div', { class: 'auth-card' },
    h('div', { class: 'brand-name', style: { marginBottom: '14px' } }, 'Relay ', h('span', null, 'Employee Hub')),
    h('h1', null, title), subtitle ? h('p', { class: 'muted' }, subtitle) : null, ...content));
}

function renderSetup(st) {
  if (!st.setup_allowed_here) {
    mount(app, authCard('Setup required', 'First-run setup must be completed on the computer where Relay Employee Hub is installed.'));
    return;
  }
  const email = h('input', { class: 'input', type: 'email', autocomplete: 'username', required: true });
  const name = h('input', { class: 'input', autocomplete: 'name' });
  const pw = h('input', { class: 'input', type: 'password', autocomplete: 'new-password' });
  const pw2 = h('input', { class: 'input', type: 'password', autocomplete: 'new-password' });
  const btn = h('button', { class: 'btn btn-primary', type: 'submit' }, 'Create administrator account');
  const form = h('form', { onsubmit: async (e) => {
    e.preventDefault();
    if (pw.value !== pw2.value) return toast('The two passwords do not match.', 'error');
    btn.disabled = true;
    try {
      await post('/api/auth/setup', { email: email.value, display_name: name.value, password: pw.value }, { noAuthRedirect: true });
      toast('Administrator account created.', 'success');
      boot();
    } catch (err) { showError(err); } finally { btn.disabled = false; }
  } },
  field('Work email (username)', email, null, true), field('Your name', name, null, true),
  field('Password', pw, 'At least 10 characters. There are no default passwords.', true), field('Repeat password', pw2, null, true), btn);
  mount(app, authCard('Welcome', 'Create the first administrator account. This screen only appears once.', form));
}

function renderLogin() {
  const email = h('input', { class: 'input', type: 'email', autocomplete: 'username' });
  const pw = h('input', { class: 'input', type: 'password', autocomplete: 'current-password' });
  const btn = h('button', { class: 'btn btn-primary', type: 'submit' }, 'Sign in');
  const form = h('form', { onsubmit: async (e) => {
    e.preventDefault(); btn.disabled = true;
    try { await post('/api/auth/login', { email: email.value, password: pw.value }, { noAuthRedirect: true }); boot(); }
    catch (err) { showError(err); } finally { btn.disabled = false; }
  } }, field('Email', email), field('Password', pw), btn,
  h('p', { class: 'muted small', style: { marginTop: '12px' } }, 'Forgot your password? Ask an administrator to reset it.'));
  mount(app, authCard('Sign in', null, form));
  setTimeout(() => email.focus(), 50);
}

export function renderChangePassword(forced) {
  const cur = h('input', { class: 'input', type: 'password', autocomplete: 'current-password' });
  const pw = h('input', { class: 'input', type: 'password', autocomplete: 'new-password' });
  const pw2 = h('input', { class: 'input', type: 'password', autocomplete: 'new-password' });
  const submit = async () => {
    if (pw.value !== pw2.value) { toast('The new passwords do not match.', 'error'); return false; }
    await post('/api/auth/change-password', { current_password: cur.value, new_password: pw.value });
    toast('Password changed.', 'success');
    if (forced) boot();
    return true;
  };
  const body = h('div', null, forced ? h('p', null, 'Your password was set by an administrator. Choose your own password to continue.') : null,
    field(forced ? 'Temporary password' : 'Current password', cur), field('New password', pw, 'At least 10 characters.'), field('Repeat new password', pw2));
  if (forced) {
    const btn = h('button', { class: 'btn btn-primary' }, 'Change password');
    btn.onclick = async () => { btn.disabled = true; try { await submit(); } catch (e) { showError(e); } finally { btn.disabled = false; } };
    mount(app, authCard('Change your password', null, body, btn));
  } else {
    modal({ title: 'Change password', body, actions: [{ label: 'Cancel' }, { label: 'Change password', kind: 'primary', onClick: submit }] });
  }
}

let navEl, contentEl, titleEl, actionsEl, bannerEl;

function renderShell() {
  navEl = h('nav', { class: 'nav', 'aria-label': 'Main' });
  contentEl = h('div', { class: 'content', id: 'content' });
  titleEl = h('h1');
  actionsEl = h('div', { class: 'row' });
  bannerEl = h('div');
  const u = state.user;
  const sidebar = h('aside', { class: 'sidebar' },
    h('div', { class: 'brand' }, h('div', { class: 'brand-name' }, 'Relay ', h('span', null, 'Employee Hub')), h('div', { class: 'brand-sub' }, 'Relay Human Cloud')),
    navEl,
    h('div', { class: 'sidebar-foot' },
      h('div', { class: 'who' }, u.display_name), h('div', { class: 'muted' }, u.role_label + (u.locations.length ? ` · ${u.locations.join(', ')}` : '')),
      h('div', { class: 'row', style: { marginTop: '8px' } },
        h('button', { class: 'btn btn-link btn-sm', onclick: () => renderChangePassword(false) }, 'Change password'),
        h('button', { class: 'btn btn-link btn-sm', onclick: logout }, icon('logout'), 'Sign out'))));
  mount(app, h('div', { class: 'shell' }, sidebar, h('main', { class: 'main' }, h('div', { class: 'topbar' }, titleEl, actionsEl), bannerEl, contentEl)));
  SECTIONS.forEach((s) => {
    if (s.perm && !can(s.perm)) return;
    navEl.appendChild(h('a', { href: '#/' + s.key, dataset: { key: s.key } }, icon(s.icon), s.label));
  });
  renderBanners();
  route();
}

function renderBanners() {
  const sys = state.system || {};
  const items = [];
  (sys.warnings || []).forEach((w) => items.push(h('div', { class: 'banner banner-warn' }, icon('alert'), h('div', null, w))));
  (sys.revalidation_needed || []).forEach((t) => items.push(h('div', { class: 'banner banner-warn' }, icon('alert'),
    h('div', null, h('strong', null, `${t.name}: `), t.revalidation_reason || 'Automatic sending needs to be re-checked by an administrator.'))));
  if (sys.outlook_mode === 'dryrun') items.push(h('div', { class: 'banner banner-info' }, icon('mail'),
    h('div', null, 'Email is in dry-run mode: nothing is sent. An administrator can switch to classic Outlook in Settings > Email.')));
  mount(bannerEl, items);
}

export async function refreshSystem() {
  try { state.system = await get('/api/system'); renderBanners(); } catch (_) { /* ignore */ }
}

export function setPage(title, actions = []) {
  mount(titleEl, title);
  mount(actionsEl, actions);
}

async function route() {
  const hash = location.hash.replace(/^#\/?/, '') || 'employees';
  const [key, ...rest] = hash.split('/');
  const section = SECTIONS.find((s) => s.key === key && (!s.perm || can(s.perm))) || SECTIONS.find((s) => !s.perm || can(s.perm));
  navEl.querySelectorAll('a').forEach((a) => a.classList.toggle('active', a.dataset.key === section.key));
  state.route = { key: section.key, rest };
  mount(contentEl, h('div', { class: 'loading' }, h('span', { class: 'spinner' }), 'Loading…'));
  setPage(section.label);
  try {
    const mod = await section.load();
    await mod.render(contentEl, rest);
  } catch (e) {
    mount(contentEl, h('div', { class: 'card' }, h('h3', null, 'This page could not be loaded'), h('p', null, e.message || String(e)),
      h('button', { class: 'btn btn-secondary', onclick: route }, 'Try again')));
  }
}

async function logout() {
  try { await post('/api/auth/logout'); } catch (_) { /* already signed out */ }
  state.user = null;
  boot();
}

window.addEventListener('hashchange', () => { if (state.user && navEl) route(); });
window.addEventListener('hub:signed-out', () => { if (state.user) { state.user = null; toast('Your session ended. Please sign in again.', 'warn'); boot(); } });
window.addEventListener('hub:password-change', () => renderChangePassword(true));

boot();
