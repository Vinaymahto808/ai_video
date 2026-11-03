import { chromium } from "playwright";
const B = "https://neha-bot-mkjv.onrender.com";
const out = process.env.OUT;
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
const errs = [];
page.on("console", m => { if (m.type() === "error") errs.push(m.text().slice(0, 160)); });
page.on("requestfailed", r => errs.push("FAILED " + r.url().slice(0, 120)));
await page.goto(B + "/", { waitUntil: "networkidle", timeout: 90000 });
await page.waitForTimeout(2500);
const launcher = await page.evaluate(() => { const n = document.querySelector("[id*=hhbot],[class*=hhbot],[id*=neha],iframe[src*=embed]"); return n ? (n.tagName + " " + (n.id || n.className)).slice(0, 80) : null; });
await page.screenshot({ path: out + "/render_root.png" });
const btn = page.locator("[id*=hhbot] button, [class*=hhbot] button, button:has-text('Neha')").first();
let opened = false;
try { await btn.click({ timeout: 8000 }); opened = true; await page.waitForTimeout(4000); } catch (e) { errs.push("launcher click: " + String(e).slice(0, 100)); }
await page.screenshot({ path: out + "/render_open.png" });
const frames = page.frames().map(f => f.url()).filter(u => u.includes("embed"));
console.log(JSON.stringify({ launcher, opened, frames, errs: errs.slice(0, 8) }, null, 1));
await browser.close();
