/**
 * Audio pipeline: sentence-level TTS queue with barge-in, plus hands-free mic capture.
 *
 * Output: each SSE `sentence` is queued the moment it arrives (server TTS via /tts; browser
 * speechSynthesis as fallback). Playback is serial so lip-sync gets one clip at a time.
 * `cut(generation)` silences everything within one audio frame; that is what barge-in calls.
 *
 * Generations: the ConversationSession owns the counter. The Speaker never increments it; `cut(g)`
 * adopts the session's value and `enqueue` drops only items older than it, so an interruption can
 * never leave the Speaker ahead of the session (which used to silence every later sentence).
 *
 * Input: `Mic` records one utterance at a time behind an adaptive loudness gate (noise floor,
 * sustained-loudness check, pre-roll) and posts the blob to /stt. If the server has no STT it
 * falls back to Web Speech Recognition.
 *
 * Voice rule for this project: only female Indian voices (Neha persona). Never pick a male voice.
 */
import { Emitter } from "./events.js";

const FEMALE_HINTS = /(swara|neerja|heera|kalpana|aditi|kajal|priya|divya|rani|ananya|aarohi|lekha|female|woman)/i;
// \b so "female" is not read as "male"; the names are the Microsoft/Google/Apple Indian male voices.
const MALE_HINTS = /\b(male|man|madhur|prabhat|rohit|aman|pratham|rohan|ravi|hemant|arjun|rishi)\b/i;
const DEVANAGARI = /[ऀ-ॿ]/;

/**
 * Pick a browser voice for `text`: Hindi script -> hi-IN, else en-IN. Preference order: a female
 * voice in that language, a female hi/en-IN voice, a non-male hi/en-IN voice, then the same two
 * steps for any hi-* or en-* voice. A voice that looks male is never returned; null means "none safe".
 * Pure, so it is unit-tested without a browser.
 */
export function pickVoice(voices, text) {
  const lang = DEVANAGARI.test(text || "") ? "hi-IN" : "en-IN";
  const list = (voices || []).filter((v) => v && typeof v.lang === "string");
  const norm = (v) => v.lang.replace("_", "-").toLowerCase();
  const exact = (v) => norm(v) === lang.toLowerCase();
  const indian = (v) => /^(hi|en)-in$/.test(norm(v));
  const family = (v) => /^(hi|en)(-|$)/.test(norm(v));
  const female = (v) => FEMALE_HINTS.test(v.name || "");
  const notMale = (v) => !MALE_HINTS.test(v.name || "");
  const voice = list.find((v) => exact(v) && female(v))
    ?? list.find((v) => indian(v) && female(v))
    ?? list.find((v) => indian(v) && notMale(v))
    ?? list.find((v) => family(v) && female(v))
    ?? list.find((v) => family(v) && notMale(v))
    ?? null;
  return { lang, voice };
}

export class Speaker extends Emitter {
  #finish = null; // settles the in-flight server clip promise (cut() calls it)

  constructor({ transport, voice = "", preferServer = true }) {
    super();
    this.transport = transport; this.voice = voice; this.preferServer = preferServer;
    this.queue = []; this.playing = null; this.generation = 0;
    this.audio = new Audio(); this.audio.preload = "auto"; this.audio.crossOrigin = "anonymous";
    this.ctx = null; this.analyser = null;
    this.voices = [];
    this.#warmVoices();
  }

  /** Browser voices load asynchronously on most engines; warm the list so the first fallback is not male. */
  #warmVoices() {
    const synth = globalThis.speechSynthesis;
    if (!synth) return;
    const load = () => { try { this.voices = synth.getVoices() || []; } catch { /* ignore */ } };
    load();
    try { synth.addEventListener?.("voiceschanged", load); } catch { /* ignore */ }
    if (!this.voices.length && !synth.addEventListener) { try { synth.onvoiceschanged = load; } catch { /* ignore */ } }
  }

  /** Call from a user gesture so autoplay policy allows later playback. Safe to call repeatedly. */
  async unlock() {
    try {
      this.ctx ??= new (window.AudioContext || window.webkitAudioContext)();
      if (this.ctx.state !== "running") await this.ctx.resume();
    } catch { /* ignore */ }
  }

  /** True when the output context exists and is allowed to run (no autoplay block). */
  get unlocked() { return !!this.ctx && this.ctx.state === "running"; }

  async #resume() {
    if (this.ctx?.state === "suspended") { try { await this.ctx.resume(); } catch { /* ignore */ } }
  }

  /** AnalyserNode on the playback element so the puppet avatar can drive a mouth from loudness. */
  analyserNode() {
    if (!this.ctx) return null;
    if (!this.analyser) {
      try {
        this.analyser = this.ctx.createAnalyser();
        this.analyser.fftSize = 512;
        this.analyser.smoothingTimeConstant = 0.3; // default .8 lags the lips ~100 ms behind the voice
        const src = this.ctx.createMediaElementSource(this.audio);
        src.connect(this.analyser); this.analyser.connect(this.ctx.destination);
      } catch { this.analyser = null; }
    }
    return this.analyser;
  }

  enqueue({ text, emotion = "neutral", gesture = "idle", generation = 0 }) {
    if (generation < this.generation) return; // belongs to an interrupted turn
    this.generation = generation;
    // Tell the server to start synthesizing now (it usually already has, when it emitted the sentence);
    // playback later streams from /tts, so the first words play ~1 s in rather than after the whole clip.
    if (this.preferServer) { try { fetch(this.transport.ttsUrl(text, this.voice, emotion) + "&prefetch=1").catch(() => {}); } catch { /* ignore */ } }
    this.queue.push({ text, emotion, gesture, generation });
    this.#drain();
  }

  /**
   * Barge-in / teardown: stop playback now and drop the queue. `generation` is the session's new
   * value (the turn that is allowed to speak next); without it the counter is left where it is, so
   * a repeated cut never advances past the session.
   */
  cut(generation) {
    if (Number.isFinite(generation)) this.generation = generation;
    this.queue.length = 0;
    const finish = this.#finish; this.#finish = null;
    try { this.audio.pause(); this.audio.removeAttribute("src"); this.audio.load(); } catch { /* ignore */ }
    try { speechSynthesis.cancel(); } catch { /* ignore */ }
    if (this.playing) { const p = this.playing; this.playing = null; this.emit("end", p); }
    finish?.(true); // the clip promise resolves as "handled" so #drain can report idle
  }

  async #drain() {
    if (this.playing || !this.queue.length) return;
    const item = (this.playing = this.queue.shift());
    this.emit("start", item);
    try {
      const ok = this.preferServer && (await this.#playServer(item));
      if (!ok && this.playing === item) await this.#playBrowser(item);
    } catch (e) { console.warn("[speaker]", e); }
    if (this.playing === item) { this.playing = null; this.emit("end", item); }
    if (!this.queue.length && !this.playing) this.emit("idle", { generation: item.generation });
    this.#drain();
  }

  async #playServer(item) {
    await this.#resume(); // a panel opened by postMessage has no in-frame gesture yet; resume on every play
    if (this.playing !== item) return true; // cut while resuming counts as handled
    return new Promise((resolve) => {
      const a = this.audio;
      let settled = false;
      const finish = (ok) => {
        if (settled) return;
        settled = true;
        if (this.#finish === finish) this.#finish = null;
        a.onended = a.onerror = null;
        resolve(ok);
      };
      this.#finish = finish;
      a.onended = () => finish(true);
      // a 503 (no server voice) or a dropped stream surfaces here; a cut is settled by cut() itself
      a.onerror = () => finish(this.playing !== item);
      a.src = this.transport.ttsUrl(item.text, this.voice, item.emotion); // streamed MP3: plays while the server still synthesizes
      a.play().catch(() => finish(this.playing !== item));
    });
  }

  async #playBrowser(item) {
    if (!("speechSynthesis" in window)) return;
    await this.#resume();
    if (this.playing !== item) return;
    return new Promise((resolve) => {
      if (!this.voices.length) { try { this.voices = speechSynthesis.getVoices() || []; } catch { /* ignore */ } }
      const { lang, voice } = pickVoice(this.voices, item.text);
      // The inventory is known and holds no safe (female / non-male) Indian voice: stay silent rather
      // than let the OS default (often male) speak as Neha. An empty inventory means "not loaded yet".
      if (!voice && this.voices.length) return resolve();
      const u = new SpeechSynthesisUtterance(item.text);
      u.lang = lang;
      if (voice) u.voice = voice;
      u.rate = 1.02; u.pitch = 1.05;
      u.onend = u.onerror = () => resolve();
      speechSynthesis.speak(u);
    });
  }
}

/**
 * Loudness gate for the mic loop (pure; unit-tested). Feed it one RMS level per tick and it answers:
 *   "arm"      first loud frame: start the recorder now (pre-roll) but do not report speech yet
 *   "start"    loud for holdMs (speakingHoldMs while the avatar talks): real speech, keep the recording
 *   "discard"  the burst died before the hold: it was noise, throw the recording away
 *   "end"      silence for silenceMs after speech
 *   null       nothing changed
 * The floor follows the room between utterances (noise = noise*0.97 + level*0.03 per tick); while
 * speech is armed/active it only tracks downwards quickly and upwards very slowly, so a long
 * utterance does not raise it but a constant hum that tripped the gate eventually becomes "floor"
 * instead of an endless utterance. The gate is max(threshold, floor * noiseMul), and speakingMul×
 * that while the avatar is speaking so its own voice from a phone speaker does not interrupt it.
 */
export function createGate({
  threshold = 0.012, holdMs = 60, speakingHoldMs = 220, silenceMs = 700, dropMs = 120,
  noiseMul = 3, speakingMul = 2.5, floorAlpha = 0.03, talkingUpAlpha = 0.003, downAlpha = 0.1,
  maxTalkMs = 15000, earlyMs = 0,
} = {}) {
  let noise = 0, talking = false, armed = false, armedAt = 0, lastLoud = 0, talkingSince = 0, earlySent = false;
  const gate = (speaking) => {
    const g = Math.max(threshold, noise * noiseMul);
    return speaking ? g * speakingMul : g;
  };
  return {
    get noise() { return noise; },
    get talking() { return talking; },
    get armed() { return armed; },
    gate,
    feed(level, now, speaking = false) {
      const loud = level > gate(speaking);
      // a loud onset frame is speech until proven otherwise: never learn it at the fast rate
      if (!talking && !armed && !loud) noise = noise * (1 - floorAlpha) + level * floorAlpha;
      else if (level < noise) noise = noise * (1 - downAlpha) + level * downAlpha;
      else noise = noise * (1 - talkingUpAlpha) + level * talkingUpAlpha;
      if (talking) {
        if (now - talkingSince > maxTalkMs) { talking = false; earlySent = false; return "end"; } // runaway: hand it to STT anyway
        if (loud) {
          lastLoud = now;
          // speech resumed after an "early" snapshot: that transcription covers only part of the utterance
          if (earlySent) { earlySent = false; return "resume"; }
          return null;
        }
        if (now - lastLoud > silenceMs) { talking = false; earlySent = false; return "end"; }
        // Early STT (earlyMs > 0): the pause is probably the end of the sentence; start transcribing now,
        // so by the time silenceMs confirms it the text is (nearly) ready. Saves ~silenceMs - earlyMs.
        if (earlyMs && !earlySent && now - lastLoud > earlyMs) { earlySent = true; return "early"; }
        return null;
      }
      if (loud) {
        lastLoud = now;
        if (!armed) { armed = true; armedAt = now; return "arm"; }
        if (now - armedAt >= (speaking ? speakingHoldMs : holdMs)) { armed = false; talking = true; talkingSince = now; return "start"; }
        return null;
      }
      if (armed && now - lastLoud > dropMs) { armed = false; return "discard"; }
      return null;
    },
    reset() { talking = false; armed = false; earlySent = false; },
  };
}

export class Mic extends Emitter {
  /** earlyMs: start transcribing after this much silence (0 disables); silenceMs still decides the turn end. */
  constructor({ transport, lang = "hi-IN", silenceMs = 700, earlyMs = 350, minMs = 350, threshold = 0.012, tickMs = 40 }) {
    super();
    Object.assign(this, { transport, lang, silenceMs, earlyMs, minMs, threshold, tickMs });
    this.stream = null; this.rec = null; this.active = false; this.useBrowser = false; this.sr = null; this.ctx = null;
    this.timer = 0; this.speaking = false; this.gate = null; this.early = null;
  }

  /** Tell the gate the avatar is talking (its own voice reaches the mic through phone speakers). */
  setSpeaking(on) { this.speaking = !!on; }

  async start() {
    if (this.active) return;
    this.active = true;
    const health = await this.transport.health().catch(() => ({}));
    const stt = health?.stt;
    // human_bot reports stt as a provider string ("groq:whisper-large-v3-turbo"); older builds as {ready|ok}.
    const sttReady = typeof stt === "string" ? stt.length > 0 : !!(stt?.ready ?? stt?.ok ?? false);
    this.useBrowser = !sttReady || typeof MediaRecorder === "undefined";
    if (!this.active) return; // stopped while /health was in flight
    if (this.useBrowser) return this.#startBrowser();
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch (e) {
      this.active = false; // permission denied: leave the widget able to retry
      throw e;
    }
    if (!this.active) { this.stream.getTracks().forEach((t) => t.stop()); this.stream = null; return; }
    this.emit("ready", { mode: "server" });
    this.#loop();
  }

  stop() {
    this.active = false;
    clearInterval(this.timer); this.timer = 0;
    try { if (this.rec && this.rec.state !== "inactive") { this.rec.keep = false; this.rec.stop(); } } catch { /* ignore */ }
    this.rec = null;
    this.stream?.getTracks().forEach((t) => t.stop()); this.stream = null;
    try { this.sr?.stop(); } catch { /* ignore */ }
    this.sr = null;
    try { this.ctx?.close(); } catch { /* ignore */ }
    this.ctx = null;
    this.gate?.reset();
    this.emit("stopped");
  }

  /* ---- server STT path: gated utterance recorder ---- */
  #loop() {
    const ctx = (this.ctx = new (window.AudioContext || window.webkitAudioContext)());
    const src = ctx.createMediaStreamSource(this.stream);
    const an = ctx.createAnalyser(); an.fftSize = 1024; src.connect(an);
    const buf = new Float32Array(an.fftSize);
    const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find((m) => MediaRecorder.isTypeSupported(m)) || "";
    const gate = (this.gate = createGate({ threshold: this.threshold, silenceMs: this.silenceMs, earlyMs: this.earlyMs }));
    let startedAt = 0;
    const rms = () => { an.getFloatTimeDomainData(buf); let s = 0; for (const v of buf) s += v * v; return Math.sqrt(s / buf.length); };

    // The recorder starts on the first loud frame (pre-roll, so the first word is kept) and is only
    // kept once the gate confirms sustained speech; a noise blip is discarded in onstop.
    const startRec = () => {
      const chunks = [];
      const rec = new MediaRecorder(this.stream, mime ? { mimeType: mime } : undefined);
      rec.keep = false;
      rec.chunks = chunks;
      rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
      rec.onstop = async () => {
        if (this.rec === rec) this.rec = null;
        const early = this.early; this.early = null;
        if (!rec.keep || !this.active || Date.now() - startedAt < this.minMs) return;
        const blob = new Blob(chunks, { type: mime || "audio/webm" });
        this.emit("processing");
        try {
          // the early snapshot (taken ~350 ms into the pause) is the same utterance: reuse its result
          let text = early ? await early.catch(() => "") : "";
          if (!text) text = await this.transport.stt(blob);
          if (text === null) { // server STT gone: switch to the browser recogniser
            this.useBrowser = true; this.stop(); this.active = true; return this.#startBrowser();
          }
          if (text.trim()) this.emit("utterance", { text: text.trim() });
        } catch (e) { this.emit("error", e); }
      };
      this.rec = rec;
      rec.start(250); startedAt = Date.now();
    };
    // Early STT: a Blob of the chunks so far is a complete WebM (the header is in the first chunk),
    // so it can be transcribed while the recorder keeps running in case the customer continues.
    const earlyStt = () => {
      const rec = this.rec;
      if (!rec || !rec.keep || !rec.chunks?.length) return;
      const blob = new Blob(rec.chunks, { type: mime || "audio/webm" });
      this.early = this.transport.stt(blob);
    };
    const stopRec = (keep) => {
      const rec = this.rec;
      if (!rec) return;
      rec.keep = keep;
      try { if (rec.state !== "inactive") rec.stop(); } catch { /* ignore */ }
    };

    const tick = () => {
      if (!this.active || this.ctx !== ctx) return;
      let level = 0;
      try { level = rms(); } catch { return; }
      this.emit("level", level);
      switch (gate.feed(level, Date.now(), this.speaking)) {
        case "arm": startRec(); break;
        case "start": if (this.rec) this.rec.keep = true; else startRec(); this.emit("speech-start"); break;
        case "discard": stopRec(false); break;
        case "early": earlyStt(); break;
        case "resume": this.early = null; break; // partial transcription is stale; the end-of-turn upload will cover it all
        case "end": this.emit("speech-end"); stopRec(true); break;
        default: break;
      }
    };
    // setInterval, not requestAnimationFrame: a background tab stops painting but must keep listening.
    clearInterval(this.timer);
    this.timer = setInterval(tick, this.tickMs);
  }

  /* ---- browser fallback: Web Speech API ---- */
  #startBrowser() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) {
      this.active = false;
      this.emit("error", new Error("no speech recognition available"));
      return;
    }
    const sr = (this.sr = new SR());
    sr.lang = this.lang; sr.continuous = true; sr.interimResults = true;
    sr.onresult = (e) => {
      const r = e.results[e.results.length - 1];
      if (r.isFinal) this.emit("utterance", { text: r[0].transcript.trim() });
      else this.emit("speech-start");
    };
    sr.onend = () => { if (this.active && this.sr === sr) { try { sr.start(); } catch { /* ignore */ } } };
    sr.onerror = (e) => this.emit("error", new Error(e.error));
    sr.start();
    this.emit("ready", { mode: "browser" });
  }
}
