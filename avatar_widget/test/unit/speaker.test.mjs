import test from "node:test";
import assert from "node:assert/strict";

/*
 * Speaker touches window/Audio at construction. Stub the minimum: no AudioContext, no
 * speechSynthesis (so the browser path resolves at once), an inert Audio element.
 * preferServer:false keeps /tts and fetch out of the picture; the generation logic is the subject.
 */
globalThis.window = globalThis;
globalThis.Audio = class {
  constructor() { this.preload = ""; this.crossOrigin = ""; this.paused = true; }
  pause() { this.paused = true; }
  load() {}
  removeAttribute() {}
  play() { return Promise.resolve(); }
};

const { Speaker, pickVoice } = await import("../../src/audio.js");

const flush = () => new Promise((r) => setTimeout(r, 0));

function make() {
  const transport = { ttsUrl: (t) => "http://x/tts?text=" + encodeURIComponent(t) };
  const sp = new Speaker({ transport, preferServer: false });
  const started = [];
  const idles = [];
  sp.on("start", (i) => started.push(i.text));
  sp.on("idle", (i) => idles.push(i.generation));
  return { sp, started, idles };
}

test("enqueue after cut(generation) with an equal generation plays", async () => {
  const { sp, started, idles } = make();
  sp.cut(3);
  assert.equal(sp.generation, 3);
  sp.enqueue({ text: "haan ji", generation: 3 });
  await flush();
  assert.deepEqual(started, ["haan ji"]);
  assert.deepEqual(idles, [3]);
});

test("an item from a lower generation is dropped, a higher one is adopted", async () => {
  const { sp, started } = make();
  sp.cut(5);
  sp.enqueue({ text: "stale", generation: 4 });
  await flush();
  assert.deepEqual(started, []);
  assert.equal(sp.generation, 5);
  sp.enqueue({ text: "fresh", generation: 6 });
  await flush();
  assert.deepEqual(started, ["fresh"]);
  assert.equal(sp.generation, 6);
});

test("a double cut does not advance the generation (the session owns the counter)", async () => {
  const { sp, started } = make();
  sp.enqueue({ text: "first", generation: 1 });
  await flush();
  sp.cut(2);
  sp.cut(2);
  sp.cut(); // no argument: leave it where it is
  assert.equal(sp.generation, 2);
  sp.enqueue({ text: "second", generation: 2 });
  await flush();
  assert.deepEqual(started, ["first", "second"]);
});

test("cut() drops the queue, ends the playing item, and reports idle so the session can listen", async () => {
  const { sp, idles } = make();
  const ended = [];
  sp.on("end", (i) => ended.push(i.text));
  // Hold the browser path open by giving the stub a speechSynthesis that never calls onend.
  const utterances = [];
  globalThis.speechSynthesis = { cancel() { this.cancelled = true; }, getVoices: () => [{ name: "Microsoft Swara Online", lang: "hi-IN" }], speak: (u) => utterances.push(u) };
  globalThis.SpeechSynthesisUtterance = class { constructor(t) { this.text = t; } };
  try {
    sp.enqueue({ text: "lambi baat", generation: 7 });
    sp.enqueue({ text: "aur baat", generation: 7 });
    await flush();
    assert.equal(sp.playing?.text, "lambi baat");
    assert.equal(sp.queue.length, 1);
    sp.cut(8);
    assert.equal(sp.queue.length, 0);
    assert.equal(sp.playing, null);
    assert.deepEqual(ended, ["lambi baat"]);
    assert.equal(globalThis.speechSynthesis.cancelled, true);
    // the interrupted utterance finishing later must not resurrect anything
    utterances[0].onend?.();
    await flush();
    assert.equal(sp.playing, null);
    assert.equal(idles.at(-1), 7);
  } finally {
    delete globalThis.speechSynthesis;
    delete globalThis.SpeechSynthesisUtterance;
  }
});

test("cut(0) at the end of a call lets the next call's greeting (generation 0) play", async () => {
  const { sp, started } = make();
  sp.cut(4);
  sp.cut(0);
  sp.enqueue({ text: "Namaste", generation: 0 });
  await flush();
  assert.deepEqual(started, ["Namaste"]);
});

test("pickVoice: Hindi text -> hi-IN, English -> en-IN, female first, never a male voice", () => {
  const voices = [
    { name: "Microsoft Ravi - English (India)", lang: "en-IN" },
    { name: "Microsoft Heera - English (India)", lang: "en-IN" },
    { name: "Microsoft Hemant - Hindi (India)", lang: "hi-IN" },
    { name: "Google हिन्दी", lang: "hi-IN" },
    { name: "Microsoft Swara Online (Natural) - Hindi (India)", lang: "hi-IN" },
  ];
  const hi = pickVoice(voices, "सर्विस का प्राइस क्या है");
  assert.equal(hi.lang, "hi-IN");
  assert.match(hi.voice.name, /Swara/);
  const en = pickVoice(voices, "Periodic service price?");
  assert.equal(en.lang, "en-IN");
  assert.match(en.voice.name, /Heera/);
  // only male Indian voices plus one unlabelled: the unlabelled one wins, never Ravi/Hemant
  const noFemale = pickVoice(voices.filter((v) => !/Heera|Swara/.test(v.name)), "hello");
  assert.equal(noFemale.voice.name, "Google हिन्दी");
  const onlyMale = pickVoice(voices.filter((v) => /Ravi|Hemant/.test(v.name)), "hello");
  assert.equal(onlyMale.voice, null);
  // "female" must not be read as "male"
  assert.match(pickVoice([{ name: "English India Female", lang: "en_IN" }], "hi").voice.name, /Female/);
  assert.equal(pickVoice([], "hi").voice, null);
});
