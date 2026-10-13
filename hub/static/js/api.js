// API client: session cookie + CSRF header, friendly errors, downloads and uploads with progress.
let csrfToken = null;

export function setCsrf(token) { csrfToken = token; }

export class ApiError extends Error {
  constructor(status, body) {
    const err = (body && body.error) || {};
    super(err.message || `Request failed (${status})`);
    this.status = status;
    this.code = err.code || 'error';
    this.details = err.details;
  }
}

async function parse(res) {
  const type = res.headers.get('content-type') || '';
  if (type.includes('application/json')) return res.json();
  return res.text();
}

export async function api(method, url, body, opts = {}) {
  const headers = { 'Accept': 'application/json' };
  if (method !== 'GET' && csrfToken) headers['X-CSRF-Token'] = csrfToken;
  const init = { method, headers, credentials: 'same-origin' };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) { headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body); }
  let res;
  try { res = await fetch(url, init); }
  catch (e) { throw new ApiError(0, { error: { message: 'The application is not responding. Check that Relay Employee Hub is still running.' } }); }
  const data = await parse(res);
  if (!res.ok) {
    const e = new ApiError(res.status, typeof data === 'object' ? data : { error: { message: String(data).slice(0, 300) } });
    if (res.status === 401 && !opts.noAuthRedirect) window.dispatchEvent(new CustomEvent('hub:signed-out'));
    if (e.code === 'password_change_required') window.dispatchEvent(new CustomEvent('hub:password-change'));
    throw e;
  }
  return data;
}

export const get = (url, opts) => api('GET', url, undefined, opts);
export const post = (url, body, opts) => api('POST', url, body === undefined ? {} : body, opts);
export const put = (url, body) => api('PUT', url, body);
export const patch = (url, body) => api('PATCH', url, body);
export const del = (url) => api('DELETE', url);

export function qs(params) {
  const u = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== '') u.set(k, typeof v === 'object' ? JSON.stringify(v) : v); });
  return u.toString();
}

// Download a file through fetch so errors can be shown instead of a broken download.
export async function download(url, fallbackName) {
  const res = await fetch(url, { credentials: 'same-origin' });
  if (!res.ok) {
    const data = await parse(res);
    throw new ApiError(res.status, typeof data === 'object' ? data : { error: { message: 'Download failed' } });
  }
  const blob = await res.blob();
  const cd = res.headers.get('content-disposition') || '';
  const m = /filename="?([^";]+)"?/i.exec(cd);
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = m ? m[1] : fallbackName || 'download';
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

// Upload with progress callback (fraction 0..1). Resolves with parsed JSON.
export function upload(url, formData, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', url);
    xhr.withCredentials = true;
    if (csrfToken) xhr.setRequestHeader('X-CSRF-Token', csrfToken);
    xhr.upload.onprogress = (e) => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
    xhr.onload = () => {
      let data = null;
      try { data = JSON.parse(xhr.responseText); } catch (_) { data = { error: { message: xhr.responseText.slice(0, 300) } }; }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new ApiError(xhr.status, data));
    };
    xhr.onerror = () => reject(new ApiError(0, { error: { message: 'Upload failed. The application may have stopped.' } }));
    xhr.send(formData);
  });
}
