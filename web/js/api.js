// fetch wrapper for the kiosk backend: base URL and keys from the settings panel,
// timing, the {"success": false, "error": {...}} envelope and the request log.

export class ApiError extends Error {
  constructor(message, { status = 0, code = null, body = null, requestId = null } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.body = body;
    this.requestId = requestId;
  }
}

const isJson = (response) =>
  (response.headers.get('Content-Type') || '').includes('application/json');

export class ApiClient {
  /**
   * @param {() => {apiUrl: string, kioskKey: string, adminKey: string}} getSettings
   * @param {(entry: object) => void} onLog called once per request
   */
  constructor(getSettings, onLog) {
    this.getSettings = getSettings;
    this.onLog = onLog;
  }

  url(path) {
    return this.getSettings().apiUrl.trim().replace(/\/+$/, '') + path;
  }

  /**
   * @param {'GET'|'POST'} method
   * @param {string} path
   * @param {object} [opts]
   * @param {object} [opts.json] JSON body
   * @param {FormData} [opts.form] multipart body
   * @param {boolean} [opts.admin] send X-Admin-Key
   * @param {'json'|'audio'} [opts.as] 'audio': read a successful non-JSON body as a Blob
   * @param {number} [opts.timeoutMs]
   * @returns {Promise<{status: number, data: any, blob: Blob|null, headers: Headers,
   *   headersMs: number, ms: number, requestId: string|null}>}
   */
  async request(method, path, opts = {}) {
    const settings = this.getSettings();
    const headers = {};
    if (settings.kioskKey) headers['X-Kiosk-Key'] = settings.kioskKey;
    if (opts.admin && settings.adminKey) headers['X-Admin-Key'] = settings.adminKey;
    let body;
    if (opts.json !== undefined) {
      headers['Content-Type'] = 'application/json';
      body = JSON.stringify(opts.json);
    } else if (opts.form) {
      body = opts.form;
    }

    const entry = { time: new Date(), method, path, status: null, ms: null, requestId: null, detail: null, error: null };
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), opts.timeoutMs ?? 60_000);
    const started = performance.now();
    try {
      let response;
      try {
        response = await fetch(this.url(path), { method, headers, body, signal: controller.signal });
      } catch (err) {
        const reason = err.name === 'AbortError'
          ? 'Timed out'
          : 'Network error or blocked by CORS (check the API URL and CORS_ALLOWED_ORIGINS)';
        entry.error = reason;
        throw new ApiError(reason);
      }
      const headersMs = Math.round(performance.now() - started);
      entry.status = response.status;
      entry.requestId = response.headers.get('X-Request-ID');

      let data = null;
      let blob = null;
      if (isJson(response)) {
        data = await response.json().catch(() => null);
      } else if (opts.as === 'audio' && response.ok) {
        blob = await response.blob();
      } else {
        data = await response.text();
      }
      const ms = Math.round(performance.now() - started);
      entry.ms = ms;
      entry.detail = blob ? { content_type: blob.type, bytes: blob.size, first_byte_ms: headersMs } : data;

      if (!response.ok) {
        const error = data && typeof data === 'object' ? data.error : null;
        const message = error?.message || (typeof data === 'string' && data) || `HTTP ${response.status}`;
        entry.error = error?.code ? `${error.code}: ${message}` : message;
        throw new ApiError(entry.error, {
          status: response.status,
          code: error?.code ?? null,
          body: data,
          requestId: entry.requestId,
        });
      }
      return { status: response.status, data, blob, headers: response.headers, headersMs, ms, requestId: entry.requestId };
    } finally {
      clearTimeout(timer);
      if (entry.ms === null) entry.ms = Math.round(performance.now() - started);
      this.onLog(entry);
    }
  }
}

/** Decodes the push-to-talk metadata header: base64url(JSON), UTF-8. */
export function decodeKioskResponseHeader(value) {
  if (!value) return null;
  const base64 = value.replace(/-/g, '+').replace(/_/g, '/');
  const padded = base64 + '='.repeat((4 - (base64.length % 4)) % 4);
  const bytes = Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
  return JSON.parse(new TextDecoder().decode(bytes));
}
