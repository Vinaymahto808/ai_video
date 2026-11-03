import { chromium } from "../../kb/node_modules/playwright/index.mjs";
const out = process.argv[2] || decodeURIComponent(new URL(".", import.meta.url).pathname).replace(/^\/(?=[A-Za-z]:)/, "");
const browser = await chromium.launch({ args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", "--autoplay-policy=no-user-gesture-required"] });
const ctx = await browser.newContext({ permissions: ["microphone"], viewport: { width: 1280, height: 800 } });
const page = await ctx.newPage();
const logs = [];
page.on("console", (m) => logs.push("[page] " + m.type() + " " + m.text()));
page.on("pageerror", (e) => logs.push("[page] ERROR " + e.message));
const reqs = [];
page.on("request", (r) => { const u = r.url(); if (/4001|4101|4300/.test(u)) reqs.push(r.method() + " " + u.replace(/^https?:\/\/127\.0\.0\.1/, "")); });
const url = process.env.SMOKE_URL || "http://127.0.0.1:4300/demo.html?api=http://127.0.0.1:4001&persona=auto-car-repair";
console.log("url:", url);
await page.goto(url, { waitUntil: "networkidle" });
const host = page.locator("div[data-avatar-widget]");
console.log("launcher host present:", await host.count());
await page.waitForFunction(() => window.AvatarWidget && typeof window.AvatarWidget.open === "function");
await page.evaluate(() => window.AvatarWidget.open());
const frame = await page.waitForFunction(() => document.querySelector("div[data-avatar-widget]") && true).then(() => page.frames().find((f) => f.url().includes("panel.html")) || null);
await page.waitForTimeout(1500);
const pf = page.frames().find((f) => f.url().includes("panel.html"));
console.log("panel frame:", pf ? pf.url() : "MISSING");
if (pf) {
  pf.on("console", (m) => logs.push("[panel] " + m.type() + " " + m.text()));
  // If this browser blocks audio in a frame opened without an in-frame gesture, the panel shows
  // "Tap to start"; tap it the way a customer would. (Chromium with no-user-gesture-required skips it.)
  const tapped = await pf.waitForFunction(() => document.body.dataset.state !== "idle", null, { timeout: 8000 })
    .then(() => pf.evaluate(() => document.body.dataset.state === "tap")).catch(() => false);
  if (tapped) { await pf.locator("#tap").click(); console.log("tap-to-start shown: tapped"); }
  else console.log("tap-to-start shown: no");
  await pf.waitForFunction(() => ["listening", "speaking", "greeting", "thinking"].includes(document.body.dataset.state), null, { timeout: 25000 }).catch(() => {});
  const st = await pf.evaluate(() => ({ state: document.body.dataset.state, name: document.getElementById("name").textContent, sub: document.getElementById("sub").textContent, status: document.getElementById("status").textContent, img: document.getElementById("img").src, mic: document.getElementById("mic").dataset.on }));
  console.log("panel state:", JSON.stringify(st));
  // simulate one utterance through the widget's session (bypasses the fake mic's silence)
  await pf.waitForTimeout(3000);
  const turn = await pf.evaluate(async () => {
    const w = window.__aw; if (!w) return "no __aw handle";
    const s = w.session; if (!s) return "no session";
    const seen = [];
    s.on("*", (t, d) => seen.push(t + (d && d.text ? ":" + String(d.text).slice(0, 60) : "")));
    await s.say("Periodic service ka price kya hai?");
    await new Promise((r) => setTimeout(r, 1500));
    return { state: s.state, transcript: s.transcript.length, mic: document.getElementById("mic").dataset.on, faceHeight: document.querySelector(".face").getBoundingClientRect().height, events: seen.slice(0, 14) };
  });
  console.log("turn:", JSON.stringify(turn));
}
const bridgeLog = await page.evaluate(() => document.getElementById("log").textContent);
console.log("bridge log:\n" + bridgeLog);
await page.screenshot({ path: out + "/smoke_open.png" });
await page.evaluate(() => window.AvatarWidget.close());
await page.waitForTimeout(500);
console.log("requests:\n  " + [...new Set(reqs)].join("\n  "));
console.log("console:\n  " + logs.filter((l) => !/favicon/.test(l)).slice(0, 30).join("\n  "));
await browser.close();
