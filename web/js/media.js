// Microphone recording, audio files as a "microphone", and level meters.

export const MIC_CONSTRAINTS = {
  audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
};

export async function openMicrophone() {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error('The microphone needs a secure page: open the console over https:// or http://localhost.');
  }
  return navigator.mediaDevices.getUserMedia(MIC_CONSTRAINTS);
}

// Preference order; the backend accepts webm, ogg and m4a.
const RECORDER_TYPES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4'];

function extensionFor(mimeType) {
  if (mimeType.includes('mp4')) return 'm4a';
  if (mimeType.includes('ogg')) return 'ogg';
  return 'webm';
}

/** Push-to-talk recorder: start() ... stop() -> {blob, filename, seconds}. */
export class Recorder {
  get active() {
    return this.recorder?.state === 'recording';
  }

  async start() {
    this.stream = await openMicrophone();
    const mimeType = RECORDER_TYPES.find((t) => MediaRecorder.isTypeSupported(t));
    this.chunks = [];
    this.recorder = new MediaRecorder(this.stream, mimeType ? { mimeType } : undefined);
    this.recorder.ondataavailable = (e) => {
      if (e.data.size) this.chunks.push(e.data);
    };
    this.startedAt = performance.now();
    this.recorder.start();
  }

  stop() {
    return new Promise((resolve) => {
      this.recorder.onstop = () => {
        this.stream.getTracks().forEach((t) => t.stop());
        const type = this.recorder.mimeType || 'audio/webm';
        resolve({
          blob: new Blob(this.chunks, { type }),
          filename: `recording.${extensionFor(type)}`,
          seconds: (performance.now() - this.startedAt) / 1000,
        });
      };
      this.recorder.stop();
    });
  }

  cancel() {
    if (this.active) {
      this.recorder.onstop = null;
      this.recorder.stop();
    }
    this.stream?.getTracks().forEach((t) => t.stop());
  }
}

/**
 * Keeps a faint noise floor (about -66 dBFS) on `destination`. Chrome sends no audio
 * packets at all while a Web Audio track is digitally silent, so between recordings
 * the server would never hear the customer stop talking (no speech_stopped, no
 * answer). A real microphone always has some noise, so it doesn't need this.
 */
export function startNoiseFloor(audioContext, destination, amplitude = 0.0005) {
  const buffer = audioContext.createBuffer(1, audioContext.sampleRate * 2, audioContext.sampleRate);
  const samples = buffer.getChannelData(0);
  for (let i = 0; i < samples.length; i += 1) samples[i] = (Math.random() * 2 - 1) * amplitude;
  const source = audioContext.createBufferSource();
  source.buffer = buffer;
  source.loop = true;
  source.connect(destination);
  source.start();
  return () => source.stop();
}

/** Plays a decoded audio file into `destination` (a node feeding the call). */
export async function playFileInto(audioContext, destination, file) {
  const buffer = await audioContext.decodeAudioData(await file.arrayBuffer());
  const source = audioContext.createBufferSource();
  source.buffer = buffer;
  source.connect(destination);
  const ended = new Promise((resolve) => {
    source.onended = resolve;
  });
  source.start();
  return { seconds: buffer.duration, ended, stop: () => source.stop() };
}

/** Drives `meter` (sets --level 0..1) from a MediaStream. Returns a stop function. */
export function attachLevelMeter(audioContext, stream, meter) {
  const analyser = audioContext.createAnalyser();
  analyser.fftSize = 512;
  const source = audioContext.createMediaStreamSource(stream);
  source.connect(analyser);
  const samples = new Float32Array(analyser.fftSize);
  let frame = 0;
  const tick = () => {
    analyser.getFloatTimeDomainData(samples);
    let sum = 0;
    for (const s of samples) sum += s * s;
    const rms = Math.sqrt(sum / samples.length);
    meter.style.setProperty('--level', Math.min(1, rms * 4).toFixed(3));
    frame = requestAnimationFrame(tick);
  };
  tick();
  return () => {
    cancelAnimationFrame(frame);
    source.disconnect();
    meter.style.setProperty('--level', '0');
  };
}
