/**
 * LivenessSession: the framework-agnostic engine behind <LivenessCheck />.
 *
 * Privacy by design: raw video and audio never leave the device as media streams. In the browser we
 *   - track the face (MediaPipe WebAssembly),
 *   - reduce each frame to the mean colour of three skin regions (the rPPG signal),
 *   - send a 128x128 greyscale face crop ~5 times a second (for moire / screen / print analysis),
 *   - send 16-bit mono audio chunks (for voice anti-spoofing; optional).
 * The server keeps none of it after the session ends, only scores, flags and a hash-chained audit trail.
 */
import { collectFingerprint } from "./fingerprint";
import { createFaceTracker, type FaceObservation, type FaceTracker } from "./face";
import {
  DEFAULT_OPTIONS,
  type CaptureCallbacks,
  type LivenessOptions,
  type Phase,
  type ServerUpdate,
  type SessionInfo,
} from "./types";

const PATCH = 128;
const FLUSH_MS = 200;
const PATCH_EVERY_N_FRAMES = 6;

function b64FromBytes(bytes: Uint8Array): string {
  let s = "";
  const CH = 0x8000;
  for (let i = 0; i < bytes.length; i += CH) s += String.fromCharCode(...bytes.subarray(i, i + CH));
  return btoa(s);
}

interface Frame {
  t: number;
  rois: number[][];
  yaw: number;
  blink: number;
}

export class LivenessSession {
  private opts: LivenessOptions;
  private ws?: WebSocket;
  private stream?: MediaStream;
  private tracker: FaceTracker | null = null;
  private audioCtx?: AudioContext;
  private canvas = document.createElement("canvas");
  private ctx = this.canvas.getContext("2d", { willReadFrequently: true })!;
  private patchCanvas = document.createElement("canvas");
  private patchCtx = this.patchCanvas.getContext("2d", { willReadFrequently: true })!;
  private frames: Frame[] = [];
  private patches: { t: number; data: string }[] = [];
  private frameCount = 0;
  private lastFlush = 0;
  private startedAt = 0;
  private challengeStart: number | null = null;
  private challengeEnd: number | null = null;
  private phase: Phase = "idle";
  private stopped = false;
  private finishSent = false;
  private lastBox: FaceObservation["box"] | null = null;
  private lastFaceSeen = 0;
  private rafHandle = 0;
  private timer = 0;

  constructor(
    private info: SessionInfo,
    private video: HTMLVideoElement,
    private cb: CaptureCallbacks,
    opts: Partial<LivenessOptions> = {},
  ) {
    this.opts = { ...DEFAULT_OPTIONS, ...opts };
    this.patchCanvas.width = PATCH;
    this.patchCanvas.height = PATCH;
  }

  private setPhase(phase: Phase, secondsLeft = 0) {
    this.phase = phase;
    this.cb.onPhase(phase, { secondsLeft, challenge: this.info.challenge });
  }

  async start(): Promise<void> {
    try {
      this.setPhase("starting");
      this.stream = await this.openMedia();
      this.video.srcObject = this.stream;
      this.video.muted = true;
      this.video.playsInline = true;
      await this.video.play();

      this.tracker = await createFaceTracker();
      const fingerprint = await collectFingerprint(this.stream);
      await this.openSocket(fingerprint);
      if (this.opts.audio && this.stream.getAudioTracks().length) await this.startAudio(this.stream);

      this.startedAt = performance.now();
      this.lastFlush = this.startedAt;
      this.scheduleFrame();
      this.timer = window.setInterval(() => this.tickPhase(), 100);
    } catch (e) {
      this.fail(e instanceof Error ? e.message : String(e));
    }
  }

  stop() {
    this.stopped = true;
    cancelAnimationFrame(this.rafHandle);
    clearInterval(this.timer);
    this.stream?.getTracks().forEach((t) => t.stop());
    this.audioCtx?.close().catch(() => undefined);
    this.tracker?.close();
    if (this.ws && this.ws.readyState <= WebSocket.OPEN) this.ws.close();
  }

  // ---------------------------------------------------------------- media
  private async openMedia(): Promise<MediaStream> {
    const video: MediaTrackConstraints = {
      width: { ideal: 640 },
      height: { ideal: 480 },
      frameRate: { ideal: 30 },
      facingMode: "user",
    };
    // Voice anti-spoofing wants the raw signal, so the browser's voice processing is switched off.
    const audio: MediaTrackConstraints = {
      channelCount: 1,
      echoCancellation: false,
      noiseSuppression: false,
      autoGainControl: false,
    };
    if (!this.opts.audio) return navigator.mediaDevices.getUserMedia({ video });
    try {
      return await navigator.mediaDevices.getUserMedia({ video, audio });
    } catch (e) {
      if (e instanceof DOMException && e.name === "NotAllowedError") throw e;
      return navigator.mediaDevices.getUserMedia({ video }); // no microphone: continue video-only
    }
  }

  private async startAudio(stream: MediaStream) {
    try {
      const ctx = new AudioContext({ sampleRate: 16000 });
      await ctx.audioWorklet.addModule("/zerospoof-audio-worklet.js");
      const src = ctx.createMediaStreamSource(new MediaStream(stream.getAudioTracks()));
      const node = new AudioWorkletNode(ctx, "zerospoof-capture");
      let pending: Float32Array[] = [];
      let n = 0;
      node.port.onmessage = (ev: MessageEvent<Float32Array>) => {
        pending.push(ev.data);
        n += ev.data.length;
        if (n >= ctx.sampleRate / 4) {
          const pcm = new Int16Array(n);
          let o = 0;
          for (const chunk of pending) {
            for (let i = 0; i < chunk.length; i++) pcm[o++] = Math.max(-1, Math.min(1, chunk[i])) * 32767;
          }
          pending = [];
          n = 0;
          this.send({ type: "audio", sr: ctx.sampleRate, pcm: b64FromBytes(new Uint8Array(pcm.buffer)) });
        }
      };
      src.connect(node); // not connected to the destination: nothing is played back
      this.audioCtx = ctx;
    } catch (e) {
      console.warn("[zerospoof] audio capture unavailable, continuing without it:", e);
    }
  }

  // ---------------------------------------------------------------- websocket
  private openSocket(fingerprint: Record<string, unknown>): Promise<void> {
    return new Promise((resolve, reject) => {
      const url = `${this.info.ws_url}?token=${encodeURIComponent(this.info.client_token)}`;
      const ws = new WebSocket(url);
      this.ws = ws;
      const timeout = window.setTimeout(() => reject(new Error("Could not reach the ZeroSpoof server")), 8000);
      ws.onopen = () => ws.send(JSON.stringify({ type: "hello", fingerprint, faceTracker: this.tracker !== null }));
      ws.onerror = () => reject(new Error("Connection to the ZeroSpoof server failed"));
      ws.onclose = (ev) => {
        clearTimeout(timeout);
        if (!this.finishSent && !this.stopped && this.phase !== "done") {
          this.fail(ev.reason || `Connection closed (${ev.code})`);
        }
      };
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data as string);
        if (msg.type === "ready") {
          clearTimeout(timeout);
          resolve();
        } else if (msg.type === "update") this.cb.onUpdate(msg as ServerUpdate);
        else if (msg.type === "final") {
          this.setPhase("done");
          this.cb.onFinal(msg as ServerUpdate);
          this.stop();
        } else if (msg.type === "error") console.warn("[zerospoof] server:", msg.message);
      };
    });
  }

  private send(obj: unknown) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(obj));
  }

  // ---------------------------------------------------------------- capture loop
  private scheduleFrame() {
    if (this.stopped) return;
    const v = this.video as HTMLVideoElement & {
      requestVideoFrameCallback?: (cb: (now: number) => void) => number;
    };
    if (v.requestVideoFrameCallback) v.requestVideoFrameCallback((now) => this.onFrame(now));
    else this.rafHandle = requestAnimationFrame((now) => this.onFrame(now));
  }

  private roiMean(x: number, y: number, w: number, h: number): number[] {
    const ix = Math.max(0, Math.floor(x));
    const iy = Math.max(0, Math.floor(y));
    const iw = Math.max(1, Math.min(this.canvas.width - ix, Math.floor(w)));
    const ih = Math.max(1, Math.min(this.canvas.height - iy, Math.floor(h)));
    const d = this.ctx.getImageData(ix, iy, iw, ih).data;
    let r = 0, g = 0, b = 0, n = 0;
    for (let i = 0; i < d.length; i += 8) {
      // every 2nd pixel
      r += d[i];
      g += d[i + 1];
      b += d[i + 2];
      n++;
    }
    return n ? [r / n, g / n, b / n] : [0, 0, 0];
  }

  private onFrame(now: number) {
    if (this.stopped) return;
    this.scheduleFrame();
    const v = this.video;
    if (v.readyState < 2 || !v.videoWidth) return;

    if (this.canvas.width !== v.videoWidth) {
      this.canvas.width = v.videoWidth;
      this.canvas.height = v.videoHeight;
    }
    this.ctx.drawImage(v, 0, 0);

    let yaw = 0;
    let blink = 0;
    const obs = this.tracker?.detect(v, now) ?? null;
    if (obs) {
      this.lastBox = obs.box;
      this.lastFaceSeen = now;
      yaw = obs.yaw;
      blink = obs.blink;
    } else if (!this.tracker) {
      const W = v.videoWidth, H = v.videoHeight; // fallback: centred crop, no yaw/blink available
      this.lastBox = { x: W * 0.28, y: H * 0.12, w: W * 0.44, h: H * 0.7 };
      this.lastFaceSeen = now;
    }
    const found = now - this.lastFaceSeen < 1000 && this.lastBox !== null;
    this.cb.onFace(found, yaw);
    if (!found || !this.lastBox) return;

    const { x, y, w, h } = this.lastBox;
    const rois = [
      this.roiMean(x + 0.3 * w, y + 0.08 * h, 0.4 * w, 0.17 * h), // forehead
      this.roiMean(x + 0.12 * w, y + 0.5 * h, 0.26 * w, 0.25 * h), // cheek
      this.roiMean(x + 0.62 * w, y + 0.5 * h, 0.26 * w, 0.25 * h), // cheek
    ];
    this.frames.push({ t: now, rois, yaw, blink });
    this.frameCount++;

    if (this.frameCount % PATCH_EVERY_N_FRAMES === 0) {
      this.patchCtx.drawImage(v, x, y, Math.max(8, w), Math.max(8, h), 0, 0, PATCH, PATCH);
      const px = this.patchCtx.getImageData(0, 0, PATCH, PATCH).data;
      const gray = new Uint8Array(PATCH * PATCH);
      for (let i = 0, j = 0; i < px.length; i += 4, j++) {
        gray[j] = (px[i] * 77 + px[i + 1] * 150 + px[i + 2] * 29) >> 8;
      }
      this.patches.push({ t: now, data: b64FromBytes(gray) });
    }

    if (now - this.lastFlush >= FLUSH_MS) this.flush(now);
  }

  private flush(now: number) {
    this.lastFlush = now;
    if (!this.frames.length && !this.patches.length) return;
    this.send({ type: "video", frames: this.frames, patches: this.patches });
    this.frames = [];
    this.patches = [];
  }

  // ---------------------------------------------------------------- phases
  private tickPhase() {
    if (this.stopped || this.finishSent) return;
    const now = performance.now();
    const el = (now - this.startedAt) / 1000;
    const { prepareSeconds: P, holdSeconds: H, challengeSeconds: C } = this.opts;
    if (el < P) return this.maybeSet("prepare", P - el);
    if (el < P + H) return this.maybeSet("hold", P + H - el);
    if (el < P + H + C) {
      if (this.challengeStart === null) this.challengeStart = now;
      return this.maybeSet("challenge", P + H + C - el);
    }
    // finished: report the challenge window, flush what is left, ask for the verdict
    this.challengeEnd = now;
    this.finishSent = true;
    clearInterval(this.timer);
    this.flush(now);
    if (this.info.challenge && this.challengeStart !== null) {
      this.send({ type: "challenge", nonce: this.info.challenge.nonce, start: this.challengeStart, end: this.challengeEnd });
    }
    this.send({ type: "finish" });
    this.setPhase("finishing");
  }

  private lastPhaseKey = "";
  private maybeSet(phase: Phase, secondsLeft: number) {
    const key = `${phase}:${Math.ceil(secondsLeft)}`;
    if (key === this.lastPhaseKey) return;
    this.lastPhaseKey = key;
    this.setPhase(phase, Math.ceil(secondsLeft));
  }

  private fail(message: string) {
    if (this.phase === "error" || this.phase === "done") return;
    this.setPhase("error");
    this.cb.onError(message);
    this.stop();
  }
}
