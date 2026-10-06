// Browser version of the kiosk's realtime voice mode. Audio flows browser <-> OpenAI
// over WebRTC. The backend creates the call (POST /api/realtime/session) and runs the
// tools over its own sideband connection; this page only plays, listens and measures.

import { openMicrophone, playFileInto, startNoiseFloor } from './media.js';

const CHANNEL_OPEN_TIMEOUT_MS = 15_000;

const median = (values) => {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)];
};

export class RealtimeTester {
  /**
   * @param {import('./api.js').ApiClient} api
   * @param {object} on callbacks: state(state), turn(id, role, text, final), tool(info),
   *   event(event), latency(ms), error(message), ended(reason), streams({input, output})
   */
  constructor(api, on) {
    this.api = api;
    this.on = on;
    this.state = 'idle';
    this.active = false;
  }

  setState(state) {
    if (state === this.state) return;
    this.state = state;
    this.on.state(state);
  }

  /**
   * @param {{kioskId: string, sessionId: string, language: string, storeId?: string,
   *   source: 'mic'|'file', greeting: boolean}} options
   */
  async start(options) {
    this.options = options;
    this.metrics = { firstAudio: [], turns: [], interruptions: 0, tools: 0 };
    this.speechStoppedAt = null;
    this.seenToolOutputs = new Set();
    this.typedCount = 0;
    this.setState('connecting');
    const started = performance.now();
    try {
      this.audioContext = new AudioContext();
      await this.audioContext.resume();
      if (options.source === 'mic') {
        this.inputStream = await openMicrophone();
      } else {
        this.fileInput = this.audioContext.createMediaStreamDestination();
        startNoiseFloor(this.audioContext, this.fileInput);
        this.inputStream = this.fileInput.stream;
      }

      const pc = new RTCPeerConnection();
      this.pc = pc;
      this.remoteAudio = new Audio();
      this.remoteAudio.autoplay = true;
      pc.ontrack = (e) => {
        this.remoteAudio.srcObject = e.streams[0];
        this.on.streams({ input: this.inputStream, output: e.streams[0] });
      };
      pc.addTrack(this.inputStream.getAudioTracks()[0], this.inputStream);

      const channel = pc.createDataChannel('oai-events');
      this.channel = channel;
      channel.onmessage = (e) => this.handle(e.data);
      const opened = new Promise((resolve) => {
        channel.onopen = resolve;
      });

      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      const { data } = await this.api.request('POST', '/api/realtime/session', {
        json: {
          kiosk_id: options.kioskId,
          session_id: options.sessionId,
          language: options.language,
          ...(options.storeId ? { store_id: options.storeId } : {}),
          sdp: offer.sdp,
        },
        timeoutMs: 20_000,
      });
      this.session = data;
      this.active = true;
      await pc.setRemoteDescription({ type: 'answer', sdp: data.sdp });

      let timeout;
      await Promise.race([
        opened,
        new Promise((_, reject) => {
          timeout = setTimeout(
            () => reject(new Error('The WebRTC connection did not open (UDP blocked by a firewall?).')),
            CHANNEL_OPEN_TIMEOUT_MS,
          );
        }),
      ]).finally(() => clearTimeout(timeout));

      this.connectionMs = Math.round(performance.now() - started);
      this.startedAt = performance.now();
      channel.onclose = () => this.stop('remote_closed');
      pc.onconnectionstatechange = () => {
        if (pc.connectionState === 'failed') this.stop('connection_failed');
      };
      this.setState('listening');
      if (options.greeting && data.greeting_event) this.send(data.greeting_event);
      return data;
    } catch (err) {
      await this.stop('start_failed');
      this.setState('error');
      throw err;
    }
  }

  send(event) {
    if (this.channel?.readyState === 'open') {
      this.channel.send(JSON.stringify(event));
      this.on.event({ ...event, _sent: true });
    }
  }

  /** Types a customer question into the call; the assistant answers by voice. */
  sendText(text) {
    const id = `typed-${++this.typedCount}`;
    this.on.turn(id, 'customer', text, true);
    this.speechStoppedAt = performance.now();
    this.setState('thinking');
    this.send({
      type: 'conversation.item.create',
      item: { type: 'message', role: 'user', content: [{ type: 'input_text', text }] },
    });
    this.send({ type: 'response.create' });
  }

  /** File mode: plays a recording into the call as the customer's voice. */
  async playFile(file) {
    if (!this.fileInput) throw new Error('Start the conversation in "Audio files" mode first.');
    this.currentFile?.stop();
    this.currentFile = await playFileInto(this.audioContext, this.fileInput, file);
    return this.currentFile;
  }

  setMuted(muted) {
    this.inputStream?.getAudioTracks().forEach((t) => {
      t.enabled = !muted;
    });
  }

  handle(raw) {
    let event;
    try {
      event = JSON.parse(raw);
    } catch {
      return;
    }
    this.on.event(event);
    const now = performance.now();
    switch (event.type) {
      case 'input_audio_buffer.speech_started':
        if (this.state === 'speaking') this.metrics.interruptions += 1;
        this.speechStoppedAt = null;
        this.on.turn(event.item_id, 'customer', '', false);
        this.setState('listening');
        break;
      case 'input_audio_buffer.speech_stopped':
        this.speechStoppedAt = now;
        this.setState('thinking');
        break;
      case 'conversation.item.input_audio_transcription.completed':
        this.on.turn(event.item_id, 'customer', (event.transcript || '').trim() || '…', true);
        break;
      case 'response.output_item.added':
        if (event.item?.type === 'function_call') this.setState('tool');
        break;
      case 'response.output_item.done':
        if (event.item?.type === 'function_call') {
          this.metrics.tools += 1;
          this.on.tool({ phase: 'call', callId: event.item.call_id, name: event.item.name, arguments: event.item.arguments });
        }
        break;
      case 'conversation.item.added':
      case 'conversation.item.created':
      case 'conversation.item.done': {
        const item = event.item;
        if (item?.type === 'function_call_output' && !this.seenToolOutputs.has(item.call_id)) {
          this.seenToolOutputs.add(item.call_id);
          this.on.tool({ phase: 'result', callId: item.call_id, output: item.output });
        }
        break;
      }
      case 'response.output_audio_transcript.delta':
        this.on.turn(event.item_id, 'assistant', event.delta, false, true);
        break;
      case 'response.output_audio_transcript.done':
        this.on.turn(event.item_id, 'assistant', event.transcript, true);
        break;
      case 'output_audio_buffer.started':
        if (this.speechStoppedAt !== null) {
          const ms = Math.round(now - this.speechStoppedAt);
          this.metrics.firstAudio.push(ms);
          this.turnStartedAt = this.speechStoppedAt;
          this.speechStoppedAt = null;
          this.on.latency(ms);
        }
        this.setState('speaking');
        break;
      case 'output_audio_buffer.stopped':
      case 'output_audio_buffer.cleared':
        if (this.turnStartedAt) {
          this.metrics.turns.push(Math.round(now - this.turnStartedAt));
          this.turnStartedAt = null;
        }
        if (this.state === 'speaking') this.setState('listening');
        break;
      case 'response.done': {
        const response = event.response || {};
        const output = response.output || [];
        if (output.length && output.every((o) => o.type === 'function_call')) {
          this.setState('tool'); // the spoken answer comes in the follow-up response
        } else if (response.status !== 'completed' && this.state !== 'speaking') {
          this.setState('listening');
        }
        if (response.status === 'failed') {
          this.on.error(response.status_details?.error?.message || 'The response failed.');
        }
        break;
      }
      case 'error':
        this.on.error(event.error?.message || 'Realtime error');
        break;
      default:
        break;
    }
  }

  summary() {
    return {
      connectionMs: this.connectionMs ?? null,
      lastFirstAudioMs: this.metrics?.firstAudio.at(-1) ?? null,
      medianFirstAudioMs: median(this.metrics?.firstAudio ?? []),
      answers: this.metrics?.firstAudio.length ?? 0,
      interruptions: this.metrics?.interruptions ?? 0,
      tools: this.metrics?.tools ?? 0,
    };
  }

  clientMetrics() {
    const payload = {
      kiosk_id: this.options.kioskId,
      realtime_connection_ms: this.connectionMs,
      time_to_first_audio_ms: median(this.metrics.firstAudio),
      conversation_turn_ms: median(this.metrics.turns),
      interrupt_count: this.metrics.interruptions,
      reconnect_count: 0,
      session_duration_seconds: this.startedAt
        ? Math.round((performance.now() - this.startedAt) / 100) / 10
        : null,
    };
    return Object.fromEntries(Object.entries(payload).filter(([, v]) => v !== null && v !== undefined));
  }

  /** Ends the call. `reason` 'client' reports metrics and tells the backend. */
  async stop(reason = 'client') {
    const wasActive = this.active;
    this.active = false;
    const callId = this.session?.realtime_session_id;
    const notifyBackend = wasActive && callId && reason !== 'remote_closed';
    const metrics = notifyBackend && this.metrics ? this.clientMetrics() : null;
    this.session = null;
    this.currentFile?.stop();
    this.currentFile = null;
    if (this.channel) {
      this.channel.onclose = null;
      this.channel.close();
    }
    if (this.pc) {
      this.pc.onconnectionstatechange = null;
      this.pc.close();
    }
    this.inputStream?.getTracks().forEach((t) => t.stop());
    if (this.remoteAudio) this.remoteAudio.srcObject = null;
    this.audioContext?.close().catch(() => {});
    this.channel = this.pc = this.inputStream = this.fileInput = this.audioContext = null;
    if (wasActive) {
      this.setState(reason === 'connection_failed' ? 'error' : 'idle');
      this.on.ended(reason);
    }

    if (notifyBackend) {
      const base = `/api/realtime/session/${encodeURIComponent(callId)}`;
      await this.api.request('POST', `${base}/metrics`, { json: metrics }).catch(() => {});
      await this.api.request('POST', `${base}/end`, { json: { kiosk_id: this.options.kioskId } }).catch(() => {});
    }
  }
}
