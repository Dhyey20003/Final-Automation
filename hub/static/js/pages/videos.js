// Videos: status of the Video Studio service, its queue, and a button to open its own interface.
import { get, post } from '../api.js';
import { h, mount, icon, toast, showError, spinner, emptyState, badge, fmtDateTime } from '../ui.js';
import { can, setPage } from '../app.js';

const STATE_LABEL = { preparing: 'Preparing', queued: 'Queued', rendering: 'Rendering', cleaning_audio: 'Cleaning audio', completed: 'Completed',
  failed: 'Failed', interrupted: 'Interrupted' };
let timer = null;

export async function render(root) {
  setPage('Videos');
  const body = h('div');
  mount(root, body);
  if (timer) clearInterval(timer);
  const draw = async () => {
    let st;
    try { st = await get('/api/videos/status'); } catch (e) { mount(body, h('div', { class: 'card' }, h('p', null, e.message))); return; }
    if (!document.body.contains(body)) { clearInterval(timer); return; }
    mount(body, view(st, draw));
  };
  mount(body, spinner('Checking the Video Studio…'));
  await draw();
  timer = setInterval(draw, 4000);
}

function view(st, redraw) {
  const openBtn = h('a', { class: 'btn btn-primary', href: st.url, target: '_blank', rel: 'noopener' }, icon('video'), 'Open Video Studio');
  const startBtn = h('button', { class: 'btn btn-secondary', onclick: async () => { try { await post('/api/videos/start'); toast('Starting the Video Studio…'); setTimeout(redraw, 1500); } catch (e) { showError(e); } } }, icon('refresh'), 'Start Video Studio');
  const status = st.reachable ? badge('Running', 'green') : st.running ? badge('Starting', 'blue') : badge('Not running', 'red');
  const worker = st.worker || {};
  const jobs = st.jobs || [];
  const busy = jobs.find((j) => j.id === worker.job);
  const waiting = jobs.filter((j) => j.status === 'queued').length;
  const rows = jobs.slice().reverse().map((j) => h('tr', null,
    h('td', null, h('strong', null, j.name), h('div', { class: 'small muted' }, `${j.n_open || 0} intro, ${j.n_ans || 0} answer clips, ${j.n_cap || 0} captions`)),
    h('td', null, badge(STATE_LABEL[j.status] || j.status, { completed: 'green', failed: 'red', interrupted: 'amber', queued: 'blue', preparing: 'blue' }[j.status] || 'purple'),
      j.attempts > 1 ? h('div', { class: 'small muted' }, `attempt ${j.attempts}`) : null),
    h('td', { class: 'small' }, fmtDateTime(j.created_at), j.finished_at ? h('div', { class: 'muted' }, 'finished ' + fmtDateTime(j.finished_at)) : null),
    h('td', { class: 'small' }, j.status === 'completed' ? h('div', null, h('div', { class: 'mono' }, j.clean_output || j.output || ''),
      j.cleanup_error ? h('div', { style: { color: 'var(--amber)' } }, j.cleanup_error) : j.engine ? h('div', { class: 'muted' }, 'audio: ' + j.engine) : null) : null,
      j.error ? h('div', { class: 'err' }, j.error) : null),
    h('td', { class: 'nowrap' }, can('videos.operate') && ['failed', 'interrupted'].includes(j.status) ? h('button', { class: 'btn btn-link btn-sm', onclick: () => act('retry', j) }, 'Retry') : null,
      can('videos.operate') && !['preparing', 'rendering', 'cleaning_audio'].includes(j.status) ? h('button', { class: 'btn btn-link btn-sm', onclick: () => act('remove', j) }, 'Remove') : null)));
  async function act(action, j) {
    try { await post(`/api/videos/jobs/${action}`, { id: j.id }); toast(action === 'retry' ? `Retrying ${j.name}.` : `Removed ${j.name} from the queue.`, 'success'); redraw(); }
    catch (e) { showError(e); }
  }
  return h('div', null,
    h('div', { class: 'card' }, h('div', { class: 'card-title' }, h('h3', null, 'Video Studio ', status), h('div', { class: 'row' }, st.reachable ? openBtn : null, !st.reachable ? startBtn : null)),
      h('p', { class: 'muted' }, 'The Video Studio keeps its own interface for trimming, captions and rendering. It opens in a new browser tab. ',
        'Press Confirm & Make Video there: the clips are prepared, queued and rendered automatically, one video at a time, including the audio clean up.'),
      st.disabled_reason ? h('div', { class: 'banner banner-warn', style: { margin: '8px 0 0' } }, icon('alert'), h('div', null, st.disabled_reason)) : null,
      st.last_error ? h('div', { class: 'banner banner-warn', style: { margin: '8px 0 0' } }, icon('alert'), h('div', null, st.last_error)) : null,
      !st.reachable && st.running && !st.disabled_reason ? h('p', { class: 'small muted' }, 'Starting… this page refreshes by itself.') : null,
      h('dl', { class: 'kv', style: { marginTop: '10px' } }, h('dt', null, 'Address'), h('dd', null, h('a', { href: st.url, target: '_blank', rel: 'noopener' }, st.url)),
        h('dt', null, 'Finished videos'), h('dd', { class: 'mono' }, st.output_folder || ''),
        h('dt', null, 'FFmpeg / FFprobe'), h('dd', null, st.ffmpeg && st.ffprobe ? 'found' : h('span', { style: { color: 'var(--red)' } }, 'missing (see Settings > About > Dependencies)')),
        st.engine ? [h('dt', null, 'Automatic captions'), h('dd', null, st.engine.name === 'off' ? 'off: ' + st.engine.note : st.engine.note)] : null,
        st.denoise ? [h('dt', null, 'Audio clean up'), h('dd', null, st.denoise.enabled ? (st.denoise.keep_original ? 'on, keeping both files' : 'on, replacing the original') : 'off')] : null)),
    h('div', { class: 'card' }, h('div', { class: 'card-title' }, h('h3', null, 'Queue'),
      h('span', { class: 'muted' }, worker.busy ? `Working on ${busy ? busy.name : 'a video'} (${STATE_LABEL[worker.stage] || worker.stage || 'busy'})${waiting ? `, ${waiting} waiting` : ''}` : waiting ? 'Starting the next video…' : 'Idle')),
      jobs.length ? h('div', { class: 'table-wrap' }, h('table', { class: 'table queue-table' }, h('thead', null, h('tr', null, ['Candidate', 'State', 'Added', 'Result', ''].map((t) => h('th', null, t)))), h('tbody', null, rows)))
        : emptyState(st.reachable ? 'No videos in the queue' : 'The Video Studio is not running', st.reachable ? 'Confirmed videos appear here with their progress. The queue survives restarts.' : '')));
}
