import test from "node:test";
import assert from "node:assert/strict";

globalThis.window = globalThis;
globalThis.Audio = class { pause() {} load() {} removeAttribute() {} play() { return Promise.resolve(); } };
const { createGate, looksComplete } = await import("../../src/audio.js");

/** Feed `ms` worth of 40 ms frames at `level`, collecting non-null verdicts. One clock for the whole file. */
let clock = 1000;
function run(gate, frames, speaking = false) {
  const out = [];
  for (const [level, ms] of frames) {
    for (let i = 0; i < ms; i += 40) { const r = gate.feed(level, clock, speaking); if (r) out.push(r); clock += 40; }
  }
  return out;
}

test("quiet room: a sustained burst arms at the first loud frame and starts after 60 ms", () => {
  const g = createGate();
  const seq = run(g, [[0.002, 2000], [0.05, 200]]);
  assert.deepEqual(seq, ["arm", "start"]);
  assert.ok(g.talking);
});

test("a one-frame click arms the recorder and is then discarded, never reported as speech", () => {
  const g = createGate();
  const seq = run(g, [[0.002, 1000], [0.08, 40], [0.002, 1000]]);
  assert.deepEqual(seq, ["arm", "discard"]);
  assert.equal(g.talking, false);
});

test("the gate follows the noise floor: a fan that used to trip 0.012 no longer does", () => {
  const g = createGate();
  const first = run(g, [[0.02, 12000]]); // steady hum well above the fixed threshold
  // the very first frames can only be read as speech; the floor then overtakes the hum and ends it
  assert.deepEqual(first, ["arm", "start", "end"]);
  assert.equal(g.talking, false);
  assert.ok(g.noise > 0.015, "floor learned the hum");
  assert.ok(g.gate(false) > 0.05, "gate rose to ~3x the floor");
  const seq = run(g, [[0.02, 2000]]);
  assert.deepEqual(seq, [], "hum alone never arms");
  assert.deepEqual(run(g, [[0.2, 200]]), ["arm", "start"], "a real voice still gets through");
});

test("while the avatar speaks, the gate is 2.5x higher and needs 220 ms of sustained loudness", () => {
  const g = createGate();
  run(g, [[0.002, 1000]]);
  const quietGate = g.gate(false);
  assert.ok(Math.abs(g.gate(true) - quietGate * 2.5) < 1e-9);
  // echo from a phone speaker: loud enough for the quiet gate, not for the speaking gate
  assert.deepEqual(run(g, [[quietGate * 1.5, 1000]], true), []);
  const floorAfterEcho = g.noise; // the floor is allowed to learn the echo level (that is the point)
  assert.ok(floorAfterEcho < quietGate * 1.5);
  // the customer really talking over her: arm, then start only after 220 ms
  const seq = run(g, [[0.2, 400]], true);
  assert.deepEqual(seq, ["arm", "start"]);
  // the loud burst itself is barely learned as "room noise" (slow upward tracking while talking)
  assert.ok(g.noise < floorAfterEcho + 0.01, "floor after a 0.2 burst is " + g.noise);
  assert.ok(g.gate(false) < 0.08, "the next utterance still clears the gate easily: " + g.gate(false));
});

test("a 15 s runaway utterance is cut and handed to STT; a long real sentence keeps a low floor", () => {
  const g = createGate();
  run(g, [[0.002, 1000]]);
  // 10 s of speech with dips between words: the floor follows the dips, not the peaks
  const frames = [];
  for (let i = 0; i < 50; i++) frames.push([0.12, 160], [0.004, 40]);
  const seq = run(g, frames);
  assert.deepEqual(seq, ["arm", "start"]);
  assert.ok(g.noise < 0.02, "floor stayed low through speech: " + g.noise);
  assert.ok(g.gate(false) < 0.06, "gate stays far below the speech level: " + g.gate(false));
  const tail = run(g, [[0.12, 6000]]);
  assert.equal(tail[0], "end", "max utterance length reached: " + tail);
});

test("speech ends after silenceMs of quiet and the floor keeps learning afterwards", () => {
  const g = createGate({ silenceMs: 700 });
  run(g, [[0.002, 500], [0.1, 300]]);
  assert.ok(g.talking);
  const seq = run(g, [[0.002, 600]]);
  assert.deepEqual(seq, [], "600 ms is not yet silence");
  assert.deepEqual(run(g, [[0.002, 200]]), ["end"]);
  assert.equal(g.talking, false);
});

test("early STT: a pause of earlyMs yields one 'early', resumed speech yields 'resume', silenceMs yields 'end'", () => {
  const g = createGate({ threshold: 0.01, holdMs: 60, silenceMs: 700, earlyMs: 350, floorAlpha: 0 });
  let t = 0;
  const feed = (level, ms) => { const out = []; for (let i = 0; i < ms / 20; i++) { t += 20; out.push(g.feed(level, t)); } return out; };
  feed(0.2, 200);                                   // speech: arm -> start
  assert.ok(g.talking, "talking after sustained speech");
  const pause1 = feed(0.0, 400);                    // 400 ms pause: exactly one "early", no "end"
  assert.equal(pause1.filter((x) => x === "early").length, 1);
  assert.ok(!pause1.includes("end"));
  const more = feed(0.2, 100);                      // customer continues: the early result is stale
  assert.equal(more.filter((x) => x === "resume").length, 1);
  const pause2 = feed(0.0, 800);                    // real end: early again, then end
  assert.equal(pause2.filter((x) => x === "early").length, 1);
  assert.equal(pause2.filter((x) => x === "end").length, 1);
  assert.ok(!g.talking);
});

test("early STT is off by default (earlyMs 0): no 'early' signals", () => {
  const g = createGate({ threshold: 0.01, holdMs: 60, silenceMs: 700, floorAlpha: 0 });
  let t = 0;
  const feed = (level, ms) => { const out = []; for (let i = 0; i < ms / 20; i++) { t += 20; out.push(g.feed(level, t)); } return out; };
  feed(0.2, 200);
  const pause = feed(0.0, 600);
  assert.ok(!pause.includes("early"));
});

test("looksComplete: a transcript that stops on a connector is a mid-sentence pause", () => {
  assert.equal(looksComplete("haan"), true);
  assert.equal(looksComplete("Theek hai ji."), true);
  assert.equal(looksComplete("haan ji,"), false, "trailing comma: more is coming");
  assert.equal(looksComplete("package ka"), false);
  assert.equal(looksComplete("main aapko bata deti hoon ki"), false);
  assert.equal(looksComplete(""), false);
});

test("adaptive endpoint: a clean tail ends at fastSilenceMs, not silenceMs", () => {
  const g = createGate({ threshold: 0.01, holdMs: 60, silenceMs: 700, fastSilenceMs: 450, floorAlpha: 0 });
  let t = 0;
  const feed = (level, ms) => { const out = []; for (let i = 0; i < ms / 20; i++) { t += 20; out.push(g.feed(level, t)); } return out; };
  feed(0.2, 200); // last loud frame at t=200
  const pause = feed(0.0, 520);
  assert.equal(pause.filter((x) => x === "end").length, 1, "exactly one end: " + pause);
  assert.equal(pause.indexOf("end"), 22, "ended ~460 ms after the last loud frame, not 700");
  assert.equal(g.talking, false);
});

test("adaptive endpoint: a fading tail keeps waiting, so '…ji' is not clipped", () => {
  const g = createGate({ threshold: 0.01, holdMs: 60, silenceMs: 700, fastSilenceMs: 450, tailQuietMs: 320, floorAlpha: 0 });
  let t = 0;
  const feed = (level, ms) => { const out = []; for (let i = 0; i < ms / 20; i++) { t += 20; out.push(g.feed(level, t)); } return out; };
  feed(0.2, 200);
  const fade = feed(0.012, 300); // above tailGate (~0.009) but below the speech gate (~0.018)
  assert.ok(!fade.includes("end"), "the fade itself is not the end: " + fade);
  const after = feed(0.0, 800);
  assert.equal(after.filter((x) => x === "end").length, 1);
  assert.ok(520 + after.indexOf("end") * 20 > 700, "waited past the clean-tail point because of the tail");
});

test("a complete early transcript allows requestEnd at minSilenceMs; an incomplete one does not", () => {
  const g = createGate({ threshold: 0.01, holdMs: 60, silenceMs: 700, fastSilenceMs: 0, floorAlpha: 0 });
  let t = 0;
  const feed = (level, ms) => { const out = []; for (let i = 0; i < ms / 20; i++) { t += 20; out.push(g.feed(level, t)); } return out; };
  feed(0.2, 200);
  feed(0.012, 260); // soft tail keeps lastAbove fresh (last update at t=460)
  assert.equal(g.earlyTranscript("main aapko bata deti hoon ki"), false);
  assert.equal(g.requestEnd(700), false, "only 240 ms since the tail");
  assert.equal(g.earlyTranscript("haan ji"), true);
  assert.equal(g.requestEnd(780), true, "300 ms of silence and a thought that stands alone");
  assert.equal(g.talking, false);
});
