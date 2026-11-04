/**
 * Mic lifecycle regressions.
 *
 * The shipped widget used to fall back to the browser recogniser whenever /health did not carry an
 * `stt` field — which every public deployment hid — so the mic went deaf after the first turn on
 * Android and never worked at all on iOS. These tests pin (a) the path decision and (b) that a
 * stopped mic can always be started again, including after a recogniser start failure.
 */
import test from "node:test";
import assert from "node:assert/strict";

globalThis.window = globalThis;

let level = 0; // amplitude the fake analyser reports
class FakeAnalyser {
  constructor() { this.fftSize = 1024; }
  connect() {}
  getFloatTimeDomainData(buf) { buf.fill(level); }
}
class FakeAudioContext {
  createMediaStreamSource() { return { connect() {} }; }
  createAnalyser() { return new FakeAnalyser(); }
  close() { this.closed = true; }
}
window.AudioContext = FakeAudioContext;

let gumCalls = 0;
Object.defineProperty(globalThis, "navigator", {
  configurable: true,
  value: {
    mediaDevices: {
      getUserMedia: async () => { gumCalls++; return { getTracks: () => [{ stop() {} }] }; },
    },
  },
});

class FakeMediaRecorder {
  static isTypeSupported() { return true; }
  constructor() { this.state = "inactive"; this.keep = false; this.chunks = []; }
  start() { this.state = "recording"; }
  stop() { this.state = "inactive"; }
}

const { Mic, prefersBrowserStt } = await import("../../src/audio.js");

const tick = (ms = 0) => new Promise((r) => setTimeout(r, ms));

test("STT path: a missing /health.stt means 'unknown', so the server path is used", () => {
  // The public /health did not say anything about STT; reading that as "no server STT" is the bug.
  assert.equal(prefersBrowserStt(undefined), false);
  assert.equal(prefersBrowserStt(null), false);
  assert.equal(prefersBrowserStt("groq:whisper-large-v3-turbo"), false, "server can transcribe");
  assert.equal(prefersBrowserStt({ ready: true }), false, "older build shape");
  assert.equal(prefersBrowserStt("browser only (no groq key)"), true, "server says it cannot");
  assert.equal(prefersBrowserStt({ ready: false }), true);
  assert.equal(prefersBrowserStt(undefined, false), true, "no MediaRecorder: nothing to upload");
});

test("Mic.start uses the server recorder when /health has no stt, and stop() releases the loop", async () => {
  globalThis.MediaRecorder = FakeMediaRecorder;
  const mic = new Mic({ transport: { health: async () => ({ ok: true }), stt: async () => "haan ji" }, tickMs: 5, minMs: 0 });
  const levels = [];
  mic.on("level", () => levels.push(1));
  level = 0.05;

  await mic.start();
  assert.equal(mic.useBrowser, false, "unknown STT is not 'no STT'");
  assert.ok(mic.timer, "capture loop started");
  await tick(40);
  assert.ok(levels.length > 0, "levels keep flowing while listening");

  mic.stop();
  assert.equal(mic.active, false);
  assert.equal(mic.timer, 0, "loop interval cleared");
  assert.equal(mic.ctx, null);
});

test("mic can be turned back on after stop (the reported 'mic will not turn on again')", async () => {
  globalThis.MediaRecorder = FakeMediaRecorder;
  const before = gumCalls;
  const mic = new Mic({ transport: { health: async () => ({ ok: true }), stt: async () => "" }, tickMs: 5, minMs: 0 });

  for (let i = 0; i < 3; i++) {
    await mic.start();
    assert.equal(mic.active, true, "start #" + (i + 1));
    assert.ok(mic.timer, "loop #" + (i + 1));
    mic.stop();
    assert.equal(mic.active, false, "stop #" + (i + 1));
  }
  assert.equal(gumCalls - before, 3, "each start re-acquires the device");
});

function fakeRecognition({ failOnStart = false } = {}) {
  const S = function SpeechRecognition() {
    this.started = 0;
    this.stopped = 0;
    S.last = this;
    this.start = () => {
      if (S.failNextStart) { S.failNextStart = false; throw new Error("InvalidStateError: recognition has already started"); }
      if (failOnStart) throw new Error("InvalidStateError: recognition has already started");
      this.started++;
    };
    this.stop = () => { this.stopped++; };
    this.abort = () => {};
  };
  S.failNextStart = false;
  return S;
}

test("browser recogniser: a failed start reports 'stopped' instead of pretending the mic is on", async () => {
  const SR = fakeRecognition({ failOnStart: true });
  window.SpeechRecognition = SR;
  const mic = new Mic({ transport: { health: async () => ({ ok: true, stt: "browser only (no groq key)" }) } });
  const errors = [], stopped = [];
  mic.on("error", (e) => errors.push(e));
  mic.on("stopped", () => stopped.push(1));

  await mic.start();
  assert.equal(mic.useBrowser, true);
  assert.equal(mic.active, false, "no recogniser running");
  assert.equal(mic.sr, null);
  assert.equal(errors.length, 1);
  assert.deepEqual(stopped, [1], "the button can turn itself off");
});

test("browser recogniser: a later end restarts it, and a poisoned restart retries instead of going deaf", async () => {
  const SR = fakeRecognition();
  window.SpeechRecognition = SR;
  const mic = new Mic({ transport: { health: async () => ({ ok: true, stt: "browser only (no groq key)" }) } });
  const stopped = [];
  mic.on("stopped", () => stopped.push(1));
  await mic.start();
  const sr = SR.last;
  assert.equal(sr.started, 1);

  sr.onend(); // Chrome ends a continuous session after a pause; the restart is deferred, never re-entrant
  await tick(320);
  assert.equal(mic.active, true);
  assert.equal(sr.started, 2, "recogniser listening again");

  SR.failNextStart = true; // restart can throw InvalidStateError; that must not leave a dead "on" mic
  sr.onend();
  await tick(320);
  assert.equal(mic.active, false);
  assert.equal(mic.sr, null);
  assert.equal(stopped.length >= 1, true);
});

test("browser recogniser: a fatal permission error stops the mic and can be retried", async () => {
  const SR = fakeRecognition();
  window.SpeechRecognition = SR;
  const mic = new Mic({ transport: { health: async () => ({ ok: true, stt: "browser only" }) } });
  await mic.start();
  SR.last.onerror({ error: "not-allowed" });
  assert.equal(mic.active, false, "permission loss is not a temporary restart");
  await mic.start();
  assert.equal(mic.active, true, "a later tap starts a fresh recogniser");
  mic.stop();
});
