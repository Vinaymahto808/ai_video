import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { sampleGesture, gestureNames, gestureOrigin, idleProfile } from "../../src/avatar.js";
import { LEARNED_GESTURES, GESTURE_SOURCES, IDLE_STATS } from "../../src/motion-data.js";

const persona = JSON.parse(readFileSync(
  new URL("../../../human_bot/persona/persona_auto_car_repair.json", import.meta.url),
  "utf8",
));

const CH = ["pitch", "roll", "dx", "dy", "zoom"];
const peak = (name, ch, sign = 1) => {
  let best = 0;
  for (let p = 0; p <= 1.0001; p += 0.01) best = Math.max(best, sign * sampleGesture(name, p)[ch]);
  return best;
};

test("every persona gesture has a motion track", () => {
  const declared = new Set(Object.values(persona.avatar.gestures).flat());
  declared.add("idle");
  for (const name of declared) {
    assert.ok(gestureNames.includes(name), "no motion for gesture " + name);
  }
});

test("the footage-driven set covers the clip-backed gestures and says where each came from", () => {
  for (const name of ["small_nod", "slow_nod", "look_up", "both_hands", "open_palm", "nod", "tilt_head", "lean_in"]) {
    assert.equal(gestureOrigin(name), "learned", name + " should come from footage");
    assert.ok(GESTURE_SOURCES[name]?.kind, name + " has a provenance record");
  }
  // head_wobble has no dedicated footage: learned when a clean window was mined, else authored.
  assert.ok(["learned", "authored"].includes(gestureOrigin("head_wobble")));
  assert.equal(gestureOrigin("freeze"), "learned"); // rest tracks are generated too
  assert.equal(gestureOrigin("does_not_exist"), "none");
});

test("a gesture returns to exact rest at both ends", () => {
  for (const name of gestureNames) {
    const start = sampleGesture(name, 0);
    const end = sampleGesture(name, 1);
    for (const ch of CH) {
      assert.ok(Math.abs(start[ch]) < 1e-9, name + " starts at rest on " + ch);
      assert.ok(Math.abs(end[ch]) < 1e-9, name + " returns to rest on " + ch);
    }
  }
});

test("keyframes are ordered, within 0..1, and tracks are smoothstep between them", () => {
  for (const [name, g] of Object.entries(LEARNED_GESTURES)) {
    assert.ok(g.dur >= 120, name + " has a duration");
    for (const ch of CH) {
      const track = g[ch];
      if (!track) continue;
      assert.equal(track[0][0], 0);
      assert.equal(track[track.length - 1][0], 1);
      for (let i = 1; i < track.length; i++) assert.ok(track[i][0] > track[i - 1][0], name + "." + ch + " keyframes ascend");
      // midpoint between two keyframes is the smoothstep midpoint (0.5) of their values
      const [a, b] = [track[0], track[1]];
      const mid = sampleGesture(name, (a[0] + b[0]) / 2)[ch];
      assert.ok(Math.abs(mid - (a[1] + b[1]) / 2) < 1e-6, name + "." + ch + " eases");
    }
  }
});

test("nods go chin-down and look_up goes the other way", () => {
  assert.ok(peak("small_nod", "pitch") > 1.5, "small_nod pitches down");
  assert.ok(peak("nod", "pitch") > 1.5, "nod pitches down");
  assert.ok(peak("slow_nod", "pitch") > 1.5, "slow_nod pitches down");
  assert.ok(peak("look_up", "pitch", -1) > 0.8, "look_up lifts the chin");
});

test("head wobble alternates sides, which is the Indian agreement nod", () => {
  assert.ok(peak("head_wobble", "roll") > 1.0, "rolls one way");
  assert.ok(peak("head_wobble", "roll", -1) > 1.0, "and the other");
  assert.equal(sampleGesture("head_wobble", 1).roll, 0);
});

test("tilt_head holds a tilt rather than nodding", () => {
  const tilt = Math.max(peak("tilt_head", "roll"), peak("tilt_head", "roll", -1));
  assert.ok(tilt > 1.0, "tilt_head rolls");
});

test("lean_in and both_hands come toward the camera", () => {
  for (const name of ["lean_in", "both_hands"]) {
    assert.ok(peak(name, "zoom") > 0.005 || peak(name, "dy") > 0.3, name + " moves toward the viewer");
  }
});

test("unknown gestures and out-of-range progress are inert", () => {
  assert.deepEqual(sampleGesture("does_not_exist", 0.5), { pitch: 0, roll: 0, dx: 0, dy: 0, zoom: 0 });
  assert.deepEqual(sampleGesture("nod", -3), { pitch: 0, roll: 0, dx: 0, dy: 0, zoom: 0 });
  assert.deepEqual(sampleGesture("nod", 9), { pitch: 0, roll: 0, dx: 0, dy: 0, zoom: 0 });
});

test("no track leaves the frame budget", () => {
  for (const name of gestureNames) {
    for (let p = 0; p <= 1.0001; p += 0.02) {
      const s = sampleGesture(name, p);
      assert.ok(Math.abs(s.roll) <= 3.6 + 1e-9, name + " roll exceeds the frame budget");
      assert.ok(Math.abs(s.pitch) <= 6 + 1e-9, name + " pitch exceeds the budget");
      assert.ok(Math.abs(s.dx) <= 1.5 + 1e-9 && Math.abs(s.dy) <= 1.5 + 1e-9, name + " travel exceeds the budget");
      assert.ok(s.zoom >= -0.005 - 1e-9 && s.zoom <= 0.04 + 1e-9, name + " zoom exceeds the budget");
    }
  }
});

test("idle profile is learned from footage and stays inside sane bounds", () => {
  assert.ok(IDLE_STATS.calm_seconds >= 2, "some calm footage was measured");
  assert.ok(IDLE_STATS.blink_s.count >= 1, "at least one blink was observed");
  assert.ok(idleProfile.driftRoll >= 0.4 && idleProfile.driftRoll <= 1.6);
  assert.ok(idleProfile.retargetMs >= 2500 && idleProfile.retargetMs <= 8000);
  assert.ok(idleProfile.blinkMs >= 1800 && idleProfile.blinkMs <= 6000);
  assert.ok(idleProfile.blinkDurMs >= 80 && idleProfile.blinkDurMs <= 220);
  assert.ok(idleProfile.breathMs >= 2500 && idleProfile.breathMs <= 7000);
});
