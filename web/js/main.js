import { ApiClient, decodeKioskResponseHeader } from './api.js';
import { Recorder, attachLevelMeter } from './media.js';
import { RealtimeTester } from './realtime.js';
import { Conversation, audioPlayer, base64ToBlob, chip, el, fmtMs, jsonBlock } from './ui.js';

const $ = (id) => document.getElementById(id);

const EXAMPLES = {
  'my-MM': [
    'Coca Cola ဘယ်မှာရှိလဲ',
    'Head & Shoulders shampoo ဘယ်မှာရှိလဲ',
    'ဆိုင်ဘယ်အချိန်ပိတ်လဲ',
    'Parking ရှိလား',
    'ပြန်အပ်လို့ရလား',
    'အရပ် 170 cm weight 70 kg BMI တွက်ပေးပါ',
    'iPhone ရှိလား',
  ],
  'en-US': [
    'Where is the Coca Cola?',
    'Do you have shampoo?',
    'What time do you close?',
    'Is there parking?',
    'What is your return policy?',
    'I am 170 cm and 70 kg. What is my BMI?',
    'Do you sell iPhones?',
  ],
};
const TTS_SAMPLES = {
  'my-MM': 'Coca Cola 1L ကို Aisle A03 မှာ ရှာနိုင်ပါတယ်။',
  'en-US': 'You can find Coca Cola 1L in aisle A03.',
};
const STATE_LABELS = {
  idle: 'Idle',
  connecting: 'Connecting…',
  listening: 'Listening',
  thinking: 'Thinking',
  tool: 'Looking it up',
  speaking: 'Speaking',
  error: 'Error',
};

// ---------------------------------------------------------------- settings

const SETTINGS_KEY = 'kiosk-console.settings';
const KEYS_KEY = 'kiosk-console.keys';

function store(name) {
  try {
    return window[name];
  } catch {
    return null;
  }
}
const local = store('localStorage');
const session = store('sessionStorage');

function readJson(storage, key) {
  try {
    return JSON.parse(storage?.getItem(key) || 'null');
  } catch {
    return null;
  }
}
function writeJson(storage, key, value) {
  try {
    if (value === null) storage?.removeItem(key);
    else storage?.setItem(key, JSON.stringify(value));
  } catch {
    // Storage blocked (private window): settings just aren't remembered.
  }
}

function newSessionId() {
  const bytes = crypto.getRandomValues(new Uint8Array(4));
  return `WEB-${Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')}`;
}

function defaultApiUrl() {
  const { protocol, origin, pathname } = window.location;
  return protocol.startsWith('http') && pathname.startsWith('/console') ? origin : 'http://127.0.0.1:8010';
}

function settings() {
  return {
    apiUrl: $('api-url').value.trim(),
    kioskKey: $('kiosk-key').value.trim(),
    adminKey: $('admin-key').value.trim(),
    kioskId: $('kiosk-id').value.trim(),
    storeId: $('store-id').value.trim(),
    language: $('language').value,
    sessionId: $('session-id').value.trim(),
    rememberKeys: $('remember-keys').checked,
  };
}

function loadSettings() {
  const saved = readJson(local, SETTINGS_KEY) || {};
  $('api-url').value = saved.apiUrl || defaultApiUrl();
  $('kiosk-id').value = saved.kioskId || 'KIOSK-001';
  $('store-id').value = saved.storeId || '';
  $('language').value = saved.language || 'my-MM';
  $('remember-keys').checked = Boolean(saved.rememberKeys);
  const keys = readJson(saved.rememberKeys ? local : session, KEYS_KEY) || {};
  $('kiosk-key').value = keys.kioskKey || '';
  $('admin-key').value = keys.adminKey || '';
  $('session-id').value = newSessionId();
}

function saveSettings() {
  const s = settings();
  writeJson(local, SETTINGS_KEY, {
    apiUrl: s.apiUrl,
    kioskId: s.kioskId,
    storeId: s.storeId,
    language: s.language,
    rememberKeys: s.rememberKeys,
  });
  // Keys stay in this tab only unless the tester opts in.
  const keys = { kioskKey: s.kioskKey, adminKey: s.adminKey };
  writeJson(s.rememberKeys ? local : session, KEYS_KEY, keys);
  writeJson(s.rememberKeys ? session : local, KEYS_KEY, null);
}

// Set once the backend has answered a cross-origin request (CORS is configured).
let crossOriginWorks = false;

function updateWarnings() {
  const warnings = [];
  const { protocol, origin } = window.location;
  let apiOrigin = null;
  try {
    apiOrigin = new URL(settings().apiUrl).origin;
  } catch {
    warnings.push('The API URL is not a valid URL.');
  }
  if (protocol === 'file:') {
    warnings.push('Opened from a file: browsers block API calls from file:// pages. Serve the console (see web/README.md).');
  } else if (apiOrigin && apiOrigin !== origin && !crossOriginWorks) {
    warnings.push(`Cross-origin: the backend must allow ${origin} in CORS_ALLOWED_ORIGINS.`);
  }
  if (protocol === 'https:' && apiOrigin?.startsWith('http:')) {
    warnings.push('An https page cannot call an http API (mixed content).');
  }
  if (!window.isSecureContext) {
    warnings.push('Not a secure context: the microphone and realtime voice need https:// or http://localhost.');
  }
  const box = $('context-warning');
  box.replaceChildren(...warnings.map((w) => el('span', { text: w })));
  box.hidden = warnings.length === 0;
}

// ---------------------------------------------------------------- request log

function addLog(entry) {
  const list = $('request-log');
  const ok = !entry.error;
  const detail = entry.detail === null || entry.detail === undefined
    ? null
    : typeof entry.detail === 'string' ? entry.detail : JSON.stringify(entry.detail, null, 2);
  const node = el(
    'details',
    { class: `log-entry ${ok ? 'ok' : 'bad'}` },
    el(
      'summary',
      {},
      el('span', { class: 'log-time', text: entry.time.toLocaleTimeString() }),
      el('span', { class: 'log-method', text: entry.method }),
      el('span', { class: 'log-path', text: entry.path }),
      el('span', { class: 'log-status', text: entry.status ?? 'ERR' }),
      el('span', { class: 'log-ms', text: fmtMs(entry.ms) }),
    ),
    el(
      'div',
      { class: 'log-body' },
      entry.requestId && el('div', { class: 'muted small', text: `X-Request-ID ${entry.requestId}` }),
      entry.error && el('div', { class: 'error-text', text: entry.error }),
      detail && el('pre', { text: detail.length > 20_000 ? `${detail.slice(0, 20_000)}…` : detail }),
    ),
  );
  list.prepend(node);
  while (list.children.length > 200) list.lastChild.remove();
}

const api = new ApiClient(settings, addLog);

// ---------------------------------------------------------------- connection check

async function checkConnection() {
  saveSettings();
  const out = $('check-result');
  const status = $('server-status');
  out.replaceChildren(chip('info', 'Checking…'));
  const chips = [];
  try {
    await api.request('GET', '/health', { timeoutMs: 10_000 });
    chips.push(chip('ok', 'Live'));
    crossOriginWorks = true;
    updateWarnings();
  } catch (e) {
    out.replaceChildren(chip('bad', 'Unreachable'), el('p', { class: 'error-text small', text: e.message }));
    status.textContent = 'Unreachable';
    status.dataset.state = 'bad';
    return;
  }
  try {
    await api.request('GET', '/health/ready', { timeoutMs: 10_000 });
    chips.push(chip('ok', 'Ready'));
  } catch (e) {
    const failing = Object.entries(e.body?.checks || {}).filter(([, v]) => !v).map(([k]) => k);
    chips.push(chip('bad', failing.length ? `Not ready: ${failing.join(', ')}` : e.message));
  }
  let mode = '';
  try {
    const { data } = await api.request('GET', '/api/kiosk/config', { timeoutMs: 10_000 });
    mode = data.voice_mode;
    chips.push(chip('info', `Mode: ${data.voice_mode}`));
    chips.push(chip(data.realtime_available ? 'ok' : 'bad', 'Realtime'));
    chips.push(chip(data.chained_available ? 'ok' : 'bad', 'Push-to-talk'));
  } catch (e) {
    chips.push(chip('bad', e.status === 401 ? 'Kiosk key missing or wrong' : e.message));
  }
  out.replaceChildren(...chips);
  const healthy = chips.every((c) => !c.classList.contains('bad'));
  status.textContent = `${new URL(settings().apiUrl).host}${mode ? ` · ${mode}` : ''}`;
  status.dataset.state = healthy ? 'ok' : 'bad';
}

// ---------------------------------------------------------------- tabs

function selectTab(name) {
  for (const tab of document.querySelectorAll('[role=tab]')) {
    tab.setAttribute('aria-selected', String(tab.dataset.tab === name));
  }
  for (const panel of document.querySelectorAll('.tab-panel')) {
    panel.hidden = panel.dataset.panel !== name;
  }
  writeJson(local, 'kiosk-console.tab', name);
}

// ---------------------------------------------------------------- realtime

const rtConversation = new Conversation($('rt-conversation'));
const rtToolNotes = new Map();
let rtMeters = [];
let rtEventsStart = performance.now();
let rtSessionInfo = null;

function prettyArgs(raw) {
  try {
    return JSON.stringify(JSON.parse(raw));
  } catch {
    return raw ?? '';
  }
}

function renderRealtimeMetrics() {
  const m = realtime.summary();
  const items = [
    ['Connect', fmtMs(m.connectionMs)],
    ['First audio (last)', fmtMs(m.lastFirstAudioMs)],
    ['First audio (median)', fmtMs(m.medianFirstAudioMs)],
    ['Answers', m.answers],
    ['Tool calls', m.tools],
    ['Interruptions', m.interruptions],
  ];
  $('rt-metrics').replaceChildren(
    ...items.map(([label, value]) => el('div', {}, el('dt', { text: label }), el('dd', { text: String(value) }))),
  );
}

function logRealtimeEvent(event) {
  if (!$('rt-all-events').checked && event.type?.endsWith('.delta')) return;
  const list = $('rt-events');
  const seconds = ((performance.now() - rtEventsStart) / 1000).toFixed(1);
  const { _sent: sent, ...payload } = event;
  list.prepend(
    el(
      'li',
      { class: sent ? 'sent' : event.type === 'error' ? 'error' : '' },
      el(
        'details',
        {},
        el('summary', {}, el('span', { class: 'muted', text: `${seconds}s ` }), sent ? '→ ' : '', event.type),
        el('pre', { text: JSON.stringify(payload, null, 2) }),
      ),
    ),
  );
  while (list.children.length > 400) list.lastChild.remove();
}

function stopMeters() {
  rtMeters.forEach((stop) => stop());
  rtMeters = [];
}

const realtime = new RealtimeTester(api, {
  state(state) {
    const pill = $('rt-state');
    pill.dataset.state = state;
    pill.textContent = STATE_LABELS[state] ?? state;
  },
  turn(id, role, text, final, append = false) {
    rtConversation.upsert(id, role, text, { final, append });
  },
  tool(info) {
    if (info.phase === 'call') {
      const node = rtConversation.note(
        el('span', {}, el('strong', { text: 'Tool ' }), el('code', { text: `${info.name}(${prettyArgs(info.arguments)})` })),
        'tool',
      );
      rtToolNotes.set(info.callId, node);
      renderRealtimeMetrics();
    } else {
      const node = rtToolNotes.get(info.callId) || rtConversation.note('Tool result', 'tool');
      let output = info.output;
      try {
        output = JSON.parse(info.output);
      } catch {
        // plain text output
      }
      node.append(jsonBlock(output, 'result'));
    }
  },
  event: logRealtimeEvent,
  latency: renderRealtimeMetrics,
  error(message) {
    rtConversation.note(message, 'error');
  },
  ended(reason) {
    stopMeters();
    setRealtimeRunning(false);
    const labels = {
      client: 'Conversation ended.',
      remote_closed: 'The backend ended the call (idle timeout, time limit or replaced).',
      connection_failed: 'The WebRTC connection failed.',
      start_failed: 'Could not start the conversation.',
    };
    rtConversation.note(labels[reason] ?? `Ended (${reason}).`, reason === 'client' ? 'system' : 'error');
    renderRealtimeMetrics();
  },
  streams({ input, output }) {
    stopMeters();
    rtMeters = [
      attachLevelMeter(realtime.audioContext, input, $('rt-mic-meter')),
      attachLevelMeter(realtime.audioContext, output, $('rt-out-meter')),
    ];
  },
});

const rtSource = () => document.querySelector('input[name=rt-source]:checked').value;

function setRealtimeRunning(running, starting = false) {
  $('rt-start').disabled = running || starting;
  $('rt-stop').disabled = !running;
  $('rt-mute').disabled = !running;
  $('rt-mute').textContent = 'Mute';
  $('rt-text').disabled = !running;
  $('rt-text-form').querySelector('button').disabled = !running;
  for (const radio of document.querySelectorAll('input[name=rt-source]')) radio.disabled = running || starting;
  for (const button of $('rt-file-list').querySelectorAll('button')) button.disabled = !running || rtSource() !== 'file';
  document.body.classList.toggle('call-active', running);
}

async function startRealtime() {
  saveSettings();
  const s = settings();
  rtConversation.clear();
  rtToolNotes.clear();
  $('rt-events').replaceChildren();
  $('rt-session-info').textContent = '';
  rtEventsStart = performance.now();
  setRealtimeRunning(false, true);
  try {
    rtSessionInfo = await realtime.start({
      kioskId: s.kioskId,
      sessionId: s.sessionId,
      language: s.language,
      storeId: s.storeId,
      source: rtSource(),
      greeting: $('rt-greeting').checked,
    });
    const i = rtSessionInfo;
    $('rt-session-info').textContent = `${i.model} · voice ${i.voice} · ${i.turn_detection} · `
      + `idle ${i.idle_timeout_seconds}s · max ${i.max_session_seconds}s · call ${i.realtime_session_id}`;
    setRealtimeRunning(true);
    renderRealtimeMetrics();
  } catch (e) {
    setRealtimeRunning(false);
    rtConversation.note(e.message, 'error');
  }
}

function renderFileChips() {
  const list = $('rt-file-list');
  list.replaceChildren(
    ...Array.from($('rt-files').files, (file) => {
      const button = el('button', { type: 'button', class: 'chip-button', text: `▶ ${file.name}` });
      button.disabled = !realtime.active;
      button.addEventListener('click', async () => {
        try {
          const playing = await realtime.playFile(file);
          button.classList.add('playing');
          playing.ended.then(() => button.classList.remove('playing'));
        } catch (e) {
          rtConversation.note(e.message, 'error');
        }
      });
      return button;
    }),
  );
}

// ---------------------------------------------------------------- push-to-talk

const pttConversation = new Conversation($('ptt-conversation'));
const pttRecorder = new Recorder();

function timingTable(timing) {
  const rows = Object.entries(timing || {}).filter(([, v]) => v !== null && v !== undefined);
  if (!rows.length) return null;
  return el('span', { class: 'timing' }, rows.map(([k, v]) => el('span', { text: `${k.replace(/_ms$/, '')} ${fmtMs(v)}` })));
}

async function sendVoice(blob, filename) {
  saveSettings();
  const s = settings();
  const form = new FormData();
  form.append('audio', blob, filename);
  form.append('kiosk_id', s.kioskId);
  form.append('session_id', s.sessionId);
  form.append('language', s.language);
  if (s.storeId) form.append('store_id', s.storeId);
  const json = $('ptt-mode').value === 'json';

  const customer = pttConversation.add('customer', '', { pending: true });
  customer.addMeta(el('span', { class: 'muted', text: `${filename} · ${(blob.size / 1024).toFixed(0)} KB` }));
  const assistant = pttConversation.add('assistant', '', { pending: true });
  try {
    const res = await api.request('POST', `/api/kiosk/voice${json ? '?response=json' : ''}`, {
      form,
      as: 'audio',
      timeoutMs: 60_000,
    });
    const meta = json ? res.data : decodeKioskResponseHeader(res.headers.get('X-Kiosk-Response'));
    const audio = json
      ? meta.audio_base64 && base64ToBlob(meta.audio_base64, meta.audio_format === 'wav' ? 'audio/wav' : 'audio/mpeg')
      : res.blob;
    customer.setText(meta.transcript || '…');
    assistant.setText(meta.answer);
    assistant.addMeta(
      chip('info', meta.action),
      timingTable(meta.timing),
      el('span', {
        class: 'muted',
        text: json
          ? `round trip ${fmtMs(res.ms)}`
          : `first audio byte at the browser ${fmtMs(res.headersMs)} · download done ${fmtMs(res.ms)}`,
      }),
      meta.data && Object.keys(meta.data).length > 0 && jsonBlock(meta.data, 'data'),
      audio && audioPlayer(audio),
    );
  } catch (e) {
    const body = e.body && typeof e.body === 'object' ? e.body : {};
    customer.setText(body.transcript || '…');
    if (body.answer) {
      assistant.setText(body.answer);
      assistant.fail(`Voice failed (${e.code}); the kiosk shows the answer as text.`);
    } else {
      assistant.setText('');
      assistant.fail(e.message);
    }
  }
}

async function toggleRecording(recorder, button, onRecording) {
  if (recorder.active) {
    button.textContent = 'Record';
    button.classList.remove('recording');
    const recording = await recorder.stop();
    await onRecording(recording);
    return;
  }
  try {
    await recorder.start();
    button.textContent = 'Stop and send';
    button.classList.add('recording');
  } catch (e) {
    alert(e.message);
  }
}

// ---------------------------------------------------------------- agent

const agentConversation = new Conversation($('agent-conversation'));

async function speak(text, language) {
  const res = await api.request('POST', '/api/tts', { json: { text, language }, as: 'audio', timeoutMs: 30_000 });
  return {
    player: audioPlayer(res.blob),
    summary: `TTS first byte ${fmtMs(res.headersMs)} at the browser, `
      + `${fmtMs(Number(res.headers.get('X-TTS-First-Byte-Ms')) || null)} at the provider `
      + `(${res.headers.get('X-TTS-Provider') || '?'}), ${(res.blob.size / 1024).toFixed(0)} KB, done ${fmtMs(res.ms)}`,
  };
}

async function askAgent(text) {
  saveSettings();
  const s = settings();
  agentConversation.add('customer', text);
  const bubble = agentConversation.add('assistant', '', { pending: true });
  try {
    const { data, ms } = await api.request('POST', '/api/agent', {
      json: {
        kiosk_id: s.kioskId,
        session_id: s.sessionId,
        language: s.language,
        message: text,
        ...(s.storeId ? { store_id: s.storeId } : {}),
      },
    });
    bubble.setText(data.answer);
    bubble.addMeta(
      chip('info', data.action),
      el('span', { class: 'muted', text: `server ${fmtMs(data.duration_ms)} · round trip ${fmtMs(ms)}` }),
      data.tool_calls?.length > 0 && jsonBlock(data.tool_calls, `tool calls (${data.tool_calls.length})`),
      data.data && Object.keys(data.data).length > 0 && jsonBlock(data.data, 'data'),
    );
    if ($('agent-speak').checked) {
      const { player, summary } = await speak(data.answer, s.language);
      bubble.addMeta(el('span', { class: 'muted', text: summary }), player);
    }
  } catch (e) {
    bubble.fail(e.message);
  }
}

function renderExamples() {
  const language = $('language').value;
  $('agent-examples').replaceChildren(
    ...EXAMPLES[language].map((text) => el('button', { type: 'button', class: 'chip-button', text, onclick: () => askAgent(text) })),
  );
  const tts = $('tts-text');
  if (!tts.value || Object.values(TTS_SAMPLES).includes(tts.value)) tts.value = TTS_SAMPLES[language];
}

// ---------------------------------------------------------------- STT / TTS / admin

const sttRecorder = new Recorder();

function resultCard(...children) {
  const card = el('div', { class: 'result-card' }, ...children);
  return card;
}

async function transcribe(blob, filename) {
  saveSettings();
  const s = settings();
  const form = new FormData();
  form.append('audio', blob, filename);
  form.append('language', s.language);
  form.append('session_id', s.sessionId);
  form.append('kiosk_id', s.kioskId);
  const results = $('stt-results');
  const card = resultCard(el('div', { class: 'muted small', text: `${filename} · ${(blob.size / 1024).toFixed(0)} KB · transcribing…` }));
  results.prepend(card);
  try {
    const { data, ms } = await api.request('POST', '/api/stt', { form });
    card.replaceChildren(
      el('div', { class: 'result-text', text: data.text }),
      el('div', { class: 'muted small', text: `${filename} · ${data.language} · server ${fmtMs(data.duration_ms)} · round trip ${fmtMs(ms)}` }),
    );
  } catch (e) {
    card.replaceChildren(el('div', { class: 'error-text', text: e.message }), el('div', { class: 'muted small', text: filename }));
  }
}

async function synthesize() {
  saveSettings();
  const text = $('tts-text').value.trim();
  if (!text) return;
  const card = resultCard(el('div', { class: 'muted small', text: 'Synthesizing…' }));
  $('tts-results').prepend(card);
  try {
    const { player, summary } = await speak(text, settings().language);
    card.replaceChildren(el('div', { class: 'result-text', text }), el('div', { class: 'muted small', text: summary }), player);
  } catch (e) {
    card.replaceChildren(el('div', { class: 'error-text', text: e.message }));
  }
}

async function adminCall(method, path, admin = true) {
  saveSettings();
  const out = $('admin-output');
  out.textContent = `${method} ${path} …`;
  try {
    const { data, ms } = await api.request(method, path, { admin, timeoutMs: 30_000 });
    out.textContent = `${method} ${path} · ${fmtMs(ms)}\n\n${JSON.stringify(data, null, 2)}`;
  } catch (e) {
    out.textContent = `${method} ${path} failed: ${e.message}\n\n${e.body ? JSON.stringify(e.body, null, 2) : ''}`;
  }
}

// ---------------------------------------------------------------- wiring

function init() {
  loadSettings();
  updateWarnings();
  renderExamples();
  renderRealtimeMetrics();
  selectTab(readJson(local, 'kiosk-console.tab') || 'realtime');

  for (const id of ['api-url', 'kiosk-key', 'admin-key', 'kiosk-id', 'store-id', 'remember-keys']) {
    $(id).addEventListener('change', () => {
      if (id === 'api-url') crossOriginWorks = false;
      saveSettings();
      updateWarnings();
    });
  }
  $('language').addEventListener('change', () => {
    saveSettings();
    renderExamples();
  });
  $('new-session').addEventListener('click', () => {
    $('session-id').value = newSessionId();
  });
  $('check-btn').addEventListener('click', checkConnection);
  $('clear-log').addEventListener('click', () => $('request-log').replaceChildren());
  for (const tab of document.querySelectorAll('[role=tab]')) {
    tab.addEventListener('click', () => selectTab(tab.dataset.tab));
  }

  // Realtime
  for (const radio of document.querySelectorAll('input[name=rt-source]')) {
    radio.addEventListener('change', () => {
      $('rt-file-controls').hidden = rtSource() !== 'file';
    });
  }
  $('rt-file-controls').hidden = rtSource() !== 'file';
  $('rt-start').addEventListener('click', startRealtime);
  $('rt-stop').addEventListener('click', () => realtime.stop('client'));
  $('rt-mute').addEventListener('click', () => {
    const muted = $('rt-mute').textContent === 'Mute';
    realtime.setMuted(muted);
    $('rt-mute').textContent = muted ? 'Unmute' : 'Mute';
  });
  $('rt-files').addEventListener('change', renderFileChips);
  $('rt-text-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const text = $('rt-text').value.trim();
    if (!text || !realtime.active) return;
    $('rt-text').value = '';
    realtime.sendText(text);
  });
  window.addEventListener('pagehide', () => {
    if (realtime.active) realtime.stop('client');
  });

  // Push-to-talk
  $('ptt-record').addEventListener('click', () =>
    toggleRecording(pttRecorder, $('ptt-record'), (r) => sendVoice(r.blob, r.filename)));
  $('ptt-send-file').addEventListener('click', () => {
    const file = $('ptt-file').files[0];
    if (file) sendVoice(file, file.name);
  });
  $('ptt-clear').addEventListener('click', () => pttConversation.clear());

  // Agent
  $('agent-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const text = $('agent-text').value.trim();
    if (!text) return;
    $('agent-text').value = '';
    askAgent(text);
  });
  $('agent-clear').addEventListener('click', () => agentConversation.clear());

  // STT / TTS / admin
  $('stt-record').addEventListener('click', () =>
    toggleRecording(sttRecorder, $('stt-record'), (r) => transcribe(r.blob, r.filename)));
  $('stt-send-file').addEventListener('click', () => {
    const file = $('stt-file').files[0];
    if (file) transcribe(file, file.name);
  });
  $('tts-speak').addEventListener('click', synthesize);
  $('admin-status').addEventListener('click', () => adminCall('GET', '/admin/cache'));
  $('admin-refresh').addEventListener('click', () => adminCall('POST', '/admin/cache/refresh'));
  $('admin-ready').addEventListener('click', () => adminCall('GET', '/health/ready', false));
  $('admin-config').addEventListener('click', () => adminCall('GET', '/api/kiosk/config', false));

  if (window.location.protocol.startsWith('http')) checkConnection();
}

init();
