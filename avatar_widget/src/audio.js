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
 * Words that cannot end a thought. A transcript that stops on one of these is a mid-sentence pause:
 * shortening the silence window for it would clip the rest of the sentence, so the gate keeps the full
 * `silenceMs`. Connectors only (not content words), so "kal aaya tha" and "haan" stay complete.
 */
const DANGLING = new Set((
  "aur and or ya to bhi ki ke ka ko mein main me se par pe tak jo jab tab agar lekin magar " +
  "kyunki matlab yaani because like the a an of in on at with for is are was were that this " +
  "will can could would should so um uh hmm"
).split(" "));

/**
 * Does this transcript look like a finished thought? Pure and deliberately conservative: an empty
 * transcript or one ending on a connector ("aur…", "ki…", "of…") is not. Used only to allow an
 * *earlier* end; a false "complete" costs a clipped word, so unknown scripts (Devanagari) are
 * treated as complete only when they are non-empty without a trailing comma.
 */
export function looksComplete(text) {
  const raw = String(text || "").trim();
  if (!raw) return false;
  if (/[,;:\-–—]$/.test(raw)) return false;
  const words = raw.toLowerCase().replace(/[^\p{L}\p{N}\s']/gu, " ").trim().split(/\s+/).filter(Boolean);
  if (!words.length) return false;
  return !DANGLING.has(words[words.length - 1]);
}

/**
 * Loudness gate for the mic loop (pure; unit-tested). Feed it one RMS level per tick and it answers:
 *   "arm"      first loud frame: start the recorder now (pre-roll) but do not report speech yet
 *   "start"    loud for holdMs (speakingHoldMs while the avatar talks): real speech, keep the recording
 *   "discard"  the burst died before the hold: it was noise, throw the recording away
 *   "end"      silence for silenceMs after speech (or fastSilenceMs when the tail is clean)
 *   "early"    earlyMs of silence: snapshot the recording and transcribe it while the customer may continue
 *   "resume"   speech came back after an "early" snapshot: that transcription is stale
 *   null       nothing changed
 * The floor follows the room between utterances (noise = noise*0.97 + level*0.03 per tick); while
 * speech is armed/active it only tracks downwards quickly and upwards very slowly, so a long
 * utterance does not raise it but a constant hum that tripped the gate eventually becomes "floor"
 * instead of an endless utterance. The gate is max(threshold, floor * noiseMul), and speakingMul×
 * that while the avatar is speaking so its own voice from a phone speaker does not interrupt it.
 *
 * Adaptive endpoint: `silenceMs` is the ceiling, not the answer. With `fastSilenceMs` set, the turn
 * ends there once the room is quiet again (`lastAbove` older than `tailQuietMs`) or once the early
 * transcript says the thought is finished. A fading tail ("…ji") keeps energy above `tailGate`, so it
 * still gets the full `silenceMs`; a noisy room raises `tailGate` and does the same. Defaults are the
 * old fixed behaviour (fastSilenceMs 0), so callers opt in.
 */
export function createGate({
  threshold = 0.012, holdMs = 60, speakingHoldMs = 220, silenceMs = 700, dropMs = 120,
  noiseMul = 3, speakingMul = 2.5, floorAlpha = 0.03, talkingUpAlpha = 0.003, downAlpha = 0.1,
  maxTalkMs = 15000, earlyMs = 0,
  fastSilenceMs = 0, tailQuietMs = 320, tailMul = 1.5, minSilenceMs = 300,
} = {}) {
  let noise = 0, talking = false, armed = false, armedAt = 0, lastLoud = 0, lastAbove = 0;
  let talkingSince = 0, earlySent = false, earlyComplete = false;
  const gate = (speaking) => {
    const g = Math.max(threshold, noise * noiseMul);
    return speaking ? g * speakingMul : g;
  };
  // "any energy above the room floor", not "speech": detects a soft tail the speech gate would miss.
  const tailGate = () => Math.max(threshold * 0.6, noise * tailMul);
  const stop = () => { talking = false; earlySent = false; earlyComplete = false; };
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
      if (talking && (loud || level > tailGate())) lastAbove = now;
      if (talking) {
        if (now - talkingSince > maxTalkMs) { stop(); return "end"; } // runaway: hand it to STT anyway
        if (loud) {
          lastLoud = now;
          // speech resumed after an "early" snapshot: that transcription covers only part of the utterance
          if (earlySent) { earlySent = false; earlyComplete = false; return "resume"; }
          return null;
        }
        if (now - lastLoud > silenceMs) { stop(); return "end"; }
        // Fast end: the ceiling only applies while there is still something to wait for. A transcript
        // that stands on its own can shorten the window to minSilenceMs; otherwise the tail must be
        // quiet for tailQuietMs. Both checks run on the current frame, so resumed speech always wins.
        const floorMs = earlyComplete ? minSilenceMs : fastSilenceMs;
        if (fastSilenceMs && now - lastLoud > floorMs
            && (earlyComplete || now - lastAbove > tailQuietMs)) { stop(); return "end"; }
        // Early STT (earlyMs > 0): the pause is probably the end of the sentence; start transcribing now,
        // so by the time the end is confirmed the text is (nearly) ready. Saves ~silenceMs - earlyMs.
        if (earlyMs && !earlySent && now - lastLoud > earlyMs) { earlySent = true; return "early"; }
        return null;
      }
      if (loud) {
        lastLoud = now;
        if (!armed) { armed = true; armedAt = now; return "arm"; }
        if (now - armedAt >= (speaking ? speakingHoldMs : holdMs)) {
          armed = false; talking = true; talkingSince = now; lastAbove = now; return "start";
        }
        return null;
      }
      if (armed && now - lastLoud > dropMs) { armed = false; return "discard"; }
      return null;
    },
    /** The early snapshot's transcript. Returns true when it can stand alone (see looksComplete). */
    earlyTranscript(text) { if (talking) earlyComplete = looksComplete(text); return earlyComplete; },
    /**
     * End the turn now if enough silence has passed and the caller has evidence the thought is over
     * (a complete early transcript). Returns true when the turn was ended.
     */
    requestEnd(now = Date.now()) {
      if (!talking || now - lastLoud < minSilenceMs || now - lastAbove < minSilenceMs) return false;
      stop(); return true;
    },
    reset() { stop(); armed = false; },
  };
}

/**
 * Which microphone path to take, decided from the server's `stt` field.
 *
 * The field is the authority when present: a provider string ("groq:whisper-large-v3-turbo") means the
 * server can transcribe, while "browser only (no groq key)" means it cannot. An object {ready|ok} is
 * accepted for older builds. A *missing* field means "not stated", not "unavailable" — public
 * deployments used to hide it as operational detail, and reading that as unavailable pushed every call
 * onto the browser recogniser, which iOS does not have and Android drops after one turn. Unknown is
 * therefore server-first; /stt answers 503 when it really cannot, and that still switches paths live.
 *
 * Pure, so the decision is unit-tested without a browser.
 */
export function prefersBrowserStt(stt, hasRecorder = true) {
  if (!hasRecorder) return true;
  if (typeof stt === "string") return !stt.trim() || /^browser/i.test(stt);
  if (stt && typeof stt === "object") return !(stt.ready ?? stt.ok ?? true);
  return false;
}

export class Mic extends Emitter {
  /**
   * earlyMs: snapshot + transcribe after this much silence (0 disables). fastSilenceMs: end the turn
   * there when the tail is clean (0 keeps the fixed silenceMs); silenceMs is always the ceiling.
   */
  constructor({ transport, lang = "hi-IN", silenceMs = 700, earlyMs = 200, fastSilenceMs = 450, tailQuietMs = 320, minMs = 350, threshold = 0.012, tickMs = 40 }) {
    super();
    Object.assign(this, { transport, lang, silenceMs, earlyMs, fastSilenceMs, tailQuietMs, minMs, threshold, tickMs });
    this.stream = null; this.rec = null; this.active = false; this.useBrowser = false; this.sr = null; this.ctx = null;
    this.timer = 0; this.srRetry = 0; this.speaking = false; this.gate = null; this.early = null;
  }

  /** Tell the gate the avatar is talking (its own voice reaches the mic through phone speakers). */
  setSpeaking(on) { this.speaking = !!on; }

  async start() {
    if (this.active) return;
    this.active = true;
    const health = await this.transport.health().catch(() => null);
    this.useBrowser = prefersBrowserStt(health?.stt, typeof MediaRecorder !== "undefined");
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
    clearTimeout(this.srRetry); this.srRetry = 0;
    try { if (this.rec && this.rec.state !== "inactive") { this.rec.keep = false; this.rec.stop(); } } catch { /* ignore */ }
    this.rec = null;
    this.stream?.getTracks().forEach((t) => t.stop()); this.stream = null;
    try { this.sr?.stop(); } catch { /* ignore */ }
    this.sr = null;
    try { this.ctx?.close(); } catch { /* ignore */ }
    this.ctx = null;
    this.gate?.reset();
    this.early = null; // a snapshot from the old recording must not answer the next one
    this.emit("stopped");
  }

  /* ---- server STT path: gated utterance recorder ---- */
  #loop() {
    const ctx = (this.ctx = new (window.AudioContext || window.webkitAudioContext)());
    const src = ctx.createMediaStreamSource(this.stream);
    const an = ctx.createAnalyser(); an.fftSize = 1024; src.connect(an);
    const buf = new Float32Array(an.fftSize);
    const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find((m) => MediaRecorder.isTypeSupported(m)) || "";
    const gate = (this.gate = createGate({
      threshold: this.threshold, silenceMs: this.silenceMs, earlyMs: this.earlyMs,
      fastSilenceMs: this.fastSilenceMs, tailQuietMs: this.tailQuietMs,
    }));
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
      const p = this.transport.stt(blob);
      p.then((text) => {
        // A transcript that stands on its own ("haan", "theek hai") is evidence the customer is done:
        // end now instead of waiting out the rest of silenceMs. A mid-clause one ("…ki") keeps it.
        if (this.early !== p) return; // stale: speech resumed after the snapshot
        if (gate.earlyTranscript(text) && gate.requestEnd()) { this.emit("speech-end"); stopRec(true); }
      }).catch(() => { /* onstop re-transcribes the full clip */ });
      this.early = p;
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
      // iOS Safari has no recogniser at all. Fail visibly so the button cannot claim the mic is on.
      this.active = false;
      this.emit("error", new Error("no speech recognition in this browser"));
      this.emit("stopped");
      return;
    }
    const sr = (this.sr = new SR());
    sr.lang = this.lang; sr.continuous = true; sr.interimResults = true;
    const arm = () => {
      if (!this.active || this.sr !== sr) return;
      clearTimeout(this.srRetry);
      // Restarting straight from onend throws InvalidStateError ("recognition has already started")
      // in Chrome, and the swallowed throw left the mic on but deaf. A tick later is always legal.
      this.srRetry = setTimeout(() => {
        if (!this.active || this.sr !== sr) return;
        try { sr.start(); } catch (e) { this.#browserDead(e); }
      }, 250);
    };
    sr.onresult = (e) => {
      const r = e.results[e.results.length - 1];
      if (r.isFinal) this.emit("utterance", { text: r[0].transcript.trim() });
      else this.emit("speech-start");
    };
    sr.onend = arm;
    sr.onerror = (e) => {
      this.emit("error", new Error(e.error));
      // Permission or device gone: no restart will succeed, and a green button would be a lie.
      if (/not-allowed|service-not-allowed|audio-capture/.test(e.error || "")) this.stop();
    };
    try { sr.start(); } catch (e) { return this.#browserDead(e); }
    this.emit("ready", { mode: "browser" });
  }

  #browserDead(e) {
    this.active = false;
    clearTimeout(this.srRetry); this.srRetry = 0;
    try { this.sr?.abort?.(); } catch { /* ignore */ }
    this.sr = null;
    this.emit("error", e instanceof Error ? e : new Error(String(e?.message || e || "speech recognition failed")));
    this.emit("stopped");
  }
}
