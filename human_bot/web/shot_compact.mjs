// Screenshot the compact embed card and the launcher panel, headless, for a visual check.
//   node human_bot/web/shot_compact.mjs   (human_bot must be running on :4001)
import { chromium } from "../../kb/node_modules/playwright/index.mjs";
import { mkdirSync } from "node:fs";

const OUT = new URL("./shots/", import.meta.url).pathname.replace(/^\/(?=[A-Za-z]:)/, "");
mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch({ args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", "--autoplay-policy=no-user-gesture-required"] });
const ctx = await browser.newContext({ permissions: ["microphone"], viewport: { width: 320, height: 480 }, deviceScaleFactor: 2 });
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
await page.goto("http://127.0.0.1:4001/embed?persona=auto-car-repair", { waitUntil: "networkidle" });
await page.screenshot({ path: OUT + "embed_pre.png" });
await page.click("#go");
await page.waitForTimeout(3500);
await page.click("#kbd");
await page.fill("#txt", "kya ha no do");
await page.click("#send");
await page.waitForTimeout(6000);
await page.screenshot({ path: OUT + "embed_text.png" });
const metrics = await page.evaluate(() => {
  const r = (sel) => { const el = document.querySelector(sel); if (!el) return null; const b = el.getBoundingClientRect(); return { w: Math.round(b.width), h: Math.round(b.height), top: Math.round(b.top), bottom: Math.round(b.bottom) }; };
  return { header: r("header"), log: r("#log"), composer: r(".composer"), mic: r("#mic"), icon: r("#kbd"), note: r(".note"), msgs: document.querySelectorAll("#log .msg").length, fontMsg: getComputedStyle(document.querySelector(".msg.bot") || document.body).fontSize };
});
console.log("embed metrics:", JSON.stringify(metrics));

const page2 = await ctx.newPage();
await page2.setViewportSize({ width: 1200, height: 800 });
await page2.goto("http://127.0.0.1:4001/demo.html", { waitUntil: "networkidle" }).catch(() => page2.goto("http://127.0.0.1:4001/demo", { waitUntil: "networkidle" }));
await page2.waitForTimeout(1200);
await page2.screenshot({ path: OUT + "demo_closed.png" });
const opened = await page2.evaluate(() => {
  const host = document.getElementById("hh-human-bot");
  const root = host && (host.shadowRoot || host);
  const btn = root && (root.querySelector(".bubble") || root.querySelector(".launcher"));
  if (btn) btn.click();
  return !!btn;
});
await page2.waitForTimeout(2500);
const panel = await page2.evaluate(() => {
  const host = document.getElementById("hh-human-bot");
  const root = host && (host.shadowRoot || host);
  const p = root && root.querySelector(".panel");
  if (!p) return null;
  const b = p.getBoundingClientRect();
  return { w: Math.round(b.width), h: Math.round(b.height), open: p.classList.contains("open") };
});
console.log("launcher clicked:", opened, "panel:", JSON.stringify(panel));
await page2.screenshot({ path: OUT + "demo_open.png" });
console.log("page errors:", errors.length ? errors : "none");
console.log("shots in", OUT);
await browser.close();
