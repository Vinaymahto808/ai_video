import { chromium } from "../../kb/node_modules/playwright/index.mjs";

/**
 * Motion check for the puppet renderer. No human_bot needed: test/motion.html stubs the transport
 * to "puppet" and drives the same pose API widget.js drives. Needs only `python avatar_widget\serve.py`.
 *
 *   node avatar_widget\test\motion.mjs
 */
const URL = process.env.MOTION_URL || "http://127.0.0.1:4300/test/motion.html";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 420, height: 640 } });
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));

await page.goto(URL, { waitUntil: "load" });
await page.waitForFunction(() => window.__motion?.ready === true, null, { timeout: 8000 });

const { mode, poses, series } = await page.evaluate(async () => {
  const sample = () => {
    const t0 = performance.now();
    const out = [];
    return new Promise((resolve) => {
      const step = () => {
        out.push({ t: Math.round(performance.now() - t0), ...window.__motion.read(), gap: window.__motion.cover() });
        if (performance.now() - t0 < 2600) requestAnimationFrame(step);
        else resolve(out);
      };
      step();
    });
  };
  return { mode: window.__motion.mode, poses: window.__motion.poses, series: await sample() };
});

/**
 * Mouth phase: the patch is a ::after of the portrait container, so this also proves the channel
 * reaches it. Silence first, then a loud "speech" frame, then a peak reading of what CSS renders.
 */
const mouthRun = await page.evaluate(async () => {
  const m = window.__motion;
  const quiet = m.mouth();
  m.setLoud(true);
  await m.avatar.speak({ text: "namaste", emotion: "warm", gesture: "open_palm" });
  const peak = await new Promise((resolve) => {
    let best = { v: 0, opacity: 0, height: 0 };
    const t0 = performance.now();
    const step = () => {
      const s = m.mouth();
      if (s.v > best.v) best = { ...s };
      if (performance.now() - t0 < 700) requestAnimationFrame(step);
      else resolve(best);
    };
    step();
  });
  m.setLoud(false);
  return { quiet, peak };
});

const range = (key) => {
  const v = series.map((s) => s[key]);
  return Math.max(...v) - Math.min(...v);
};
const tail = series.slice(-12);
const avg = (key) => tail.reduce((a, s) => a + s[key], 0) / tail.length;
const maxAbs = (key, rows) => Math.max(...rows.map((s) => Math.abs(s[key])));

const checks = [
  ["puppet mode", mode === "puppet"],
  ["roll moves (tilt + wobble)", range("rot") > 1.5],
  ["nod moves vertically", range("ty") > 0.5],
  ["sway moves horizontally", range("tx") > 0.1],
  ["breathing changes zoom", range("zoom") > 0.002],
  ["roll stays inside the cover budget", maxAbs("rot", series) <= 3.65],
  ["zoom never drops below the cover budget", Math.min(...series.map((s) => s.zoom)) >= 1.13],
  ["the frame edge never shows", Math.min(...series.map((s) => s.gap)) >= 0],
  ["cut() drops the nod and holds the listening tilt", Math.abs(avg("ty")) < 0.8 && avg("rot") < -0.4],
  ["mouth patch is shut at rest", mouthRun.quiet.v < 0.01 && mouthRun.quiet.opacity < 0.02],
  ["voice opens the mouth patch", mouthRun.peak.v > 0.5 && mouthRun.peak.opacity > 0.4],
  ["the rendered patch grows, not just the variable", mouthRun.peak.height > mouthRun.quiet.height * 2],
  ["pose API fired in order", ["listening", "thinking", "speaking"].every((s) => poses.some((p) => p.state === s))],
  ["head_wobble reached the renderer", poses.some((p) => p.gesture === "head_wobble")],
  ["no page errors", errors.length === 0],
];

let failed = 0;
for (const [name, ok] of checks) {
  console.log((ok ? "ok   " : "FAIL ") + name);
  if (!ok) failed++;
}
console.log("\nseries: " + series.length + " frames over " + series.at(-1).t + "ms");
console.log("ranges: rot " + range("rot").toFixed(2) + "deg, tx " + range("tx").toFixed(2) +
  "%, ty " + range("ty").toFixed(2) + "%, zoom " + range("zoom").toFixed(4) +
  ", min corner gap " + Math.min(...series.map((s) => s.gap)).toFixed(1) + "px");
console.log("poses: " + poses.map((p) => p.state + "/" + p.gesture).join(" → "));
console.log("mouth: rest " + mouthRun.quiet.v.toFixed(2) + " (opacity " + mouthRun.quiet.opacity.toFixed(2) +
  ", " + mouthRun.quiet.height.toFixed(1) + "px) → peak " + mouthRun.peak.v.toFixed(2) +
  " (opacity " + mouthRun.peak.opacity.toFixed(2) + ", " + mouthRun.peak.height.toFixed(1) + "px)");

await browser.close();
process.exit(failed ? 1 : 0);
