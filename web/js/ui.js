// Small DOM helpers. All text goes in through textContent, never innerHTML:
// transcripts and answers come from customers and models.

export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat()) {
    if (child === undefined || child === null || child === false) continue;
    node.append(child instanceof Node ? child : String(child));
  }
  return node;
}

export function fmtMs(ms) {
  if (ms === null || ms === undefined) return '–';
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${ms} ms`;
}

export function jsonBlock(value, summary) {
  const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  return el('details', { class: 'json' }, el('summary', { text: summary }), el('pre', { text }));
}

export function chip(kind, text) {
  return el('span', { class: `chip ${kind}`, text });
}

export function audioPlayer(blob, { autoplay = true } = {}) {
  const audio = el('audio', { controls: true, src: URL.createObjectURL(blob) });
  if (autoplay) audio.play().catch(() => {});
  return audio;
}

export function base64ToBlob(base64, type) {
  const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
  return new Blob([bytes], { type });
}

const ROLE_LABELS = { customer: 'Customer', assistant: 'Assistant' };

class Bubble {
  constructor(role) {
    this.text = el('div', { class: 'text' });
    this.meta = el('div', { class: 'meta' });
    this.node = el('div', { class: `bubble ${role}` }, el('div', { class: 'who', text: ROLE_LABELS[role] }), this.text, this.meta);
  }

  setText(text, { append = false, pending = false } = {}) {
    if (append) this.text.textContent += text;
    else this.text.textContent = text;
    this.node.classList.toggle('pending', pending && !this.text.textContent);
    return this;
  }

  addMeta(...nodes) {
    this.meta.append(...nodes.filter(Boolean));
    return this;
  }

  fail(message) {
    this.node.classList.remove('pending');
    this.node.classList.add('failed');
    this.addMeta(el('span', { class: 'error-text', text: message }));
    return this;
  }
}

/** Chat-style transcript. Bubbles can be addressed by id (realtime item ids). */
export class Conversation {
  constructor(container) {
    this.container = container;
    this.byId = new Map();
  }

  clear() {
    this.container.replaceChildren();
    this.byId.clear();
  }

  add(role, text, { pending = false } = {}) {
    const bubble = new Bubble(role).setText(text, { pending });
    this.container.append(bubble.node);
    this.scroll();
    return bubble;
  }

  /** Creates or updates the bubble for `id` (append = streaming delta). */
  upsert(id, role, text, { final = true, append = false } = {}) {
    let bubble = this.byId.get(id);
    if (!bubble) {
      bubble = this.add(role, '');
      this.byId.set(id, bubble);
    }
    bubble.setText(text, { append, pending: !final });
    this.scroll();
    return bubble;
  }

  note(content, kind = 'system') {
    const node = el('div', { class: `note ${kind}` }, content);
    this.container.append(node);
    this.scroll();
    return node;
  }

  scroll() {
    this.container.scrollTop = this.container.scrollHeight;
  }
}
