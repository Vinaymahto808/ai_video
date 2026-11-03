// Screenshot the human_bot launcher (closed + open) on a dark test page.
//   node scripts/shot_widget.js http://127.0.0.1:4003 auto-car-repair
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const BOT = process.argv[2] ?? "http://127.0.0.1:4003";
const PERSONA = process.argv[3] ?? "auto-car-repair";
mkdirSync("data/debug", { recursive: true });

const html = `<!doctype html><html><body style="margin:0;background:#0e1420;color:#9fb0c9;font-family:Segoe UI">
<div style="padding:40px;max-width:720px"><h1 style="color:#fff">Auto Car Repair — test page</h1>
<p>Multi-brand car service & collision repair across Delhi NCR. ${"Lorem ipsum body copy. ".repeat(30)}</p></div>
<script src="${BOT}/widget.js" data-persona="${PERSONA}" data-accent="#0b2f6b" defer></script></body></html>`;

const browser = await chromium.launch({ headless: true, args: ["--disable-gpu", "--no-sandbox"] });
const page = await browser.newPage({ viewport: { width: 1200, height: 720 } });
page.on("console", (m) => { if (["error", "warning"].includes(m.type())) console.log("console:", m.type(), m.text()); });
page.on("pageerror", (e) => console.log("pageerror:", e.message));
page.on("requestfailed", (r) => console.log("requestfailed:", r.url(), r.failure()?.errorText));
await page.route("**/test.html", (r) => r.fulfill({ contentType: "text/html; charset=utf-8", body: html }));
await page.goto(BOT + "/test.html");
await page.waitForTimeout(1500);
console.log("host mounted:", await page.locator("#hh-human-bot").count());
await page.screenshot({ path: "data/debug/widget_closed.png", clip: { x: 980, y: 560, width: 220, height: 160 } });
const host = page.locator("#hh-human-bot");
await host.evaluate((h) => h.shadowRoot.querySelector("#open").dispatchEvent(new MouseEvent("mouseover", { bubbles: true })));
await page.waitForTimeout(8000); // teaser appears at 7 s
await page.screenshot({ path: "data/debug/widget_teaser.png", clip: { x: 780, y: 480, width: 420, height: 240 } });
await host.evaluate((h) => h.shadowRoot.querySelector("#open").click());
await page.waitForTimeout(2500);
await page.screenshot({ path: "data/debug/widget_open.png", clip: { x: 790, y: 0, width: 410, height: 720 } });
try {
  await page.frameLocator("#frame").locator("#go").click({ timeout: 5000 });
  await page.waitForTimeout(4000);
  await page.screenshot({ path: "data/debug/widget_live.png", clip: { x: 790, y: 0, width: 410, height: 720 } });
} catch (e) { console.log("live shot skipped:", String(e.message).slice(0, 120)); }
await browser.close();
console.log("saved data/debug/widget_closed.png, widget_teaser.png, widget_open.png");
