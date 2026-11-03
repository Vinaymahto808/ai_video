// Render one URL the way the crawler does, save HTML, and report what extraction keeps/removes.
//   node scripts/debug_render.js https://autocarrepair.in/faq
import { chromium } from "playwright";
import { writeFileSync, mkdirSync } from "node:fs";
import * as cheerio from "cheerio";
import { extractPage } from "../src/extract/extract.js";
import { settings } from "../src/config.js";
import { settleSpa } from "../src/crawler/crawl.js";

const url = process.argv[2] ?? "https://autocarrepair.in/faq";
const browser = await chromium.launch({ headless: true, args: ["--disable-gpu", "--no-sandbox"] });
const page = await browser.newPage({ userAgent: settings.userAgent });
await page.route("**/*", (r) => (["image", "media", "font"].includes(r.request().resourceType()) ? r.abort() : r.continue()));
const t0 = Date.now();
const resp = await page.goto(url, { waitUntil: "domcontentloaded", timeout: 45000 });
const expanded = await settleSpa(page);
const painted = true;
console.log("accordions expanded:", expanded);
const innerText = await page.evaluate(() => document.body.innerText);
const html = await page.content();
await browser.close();
mkdirSync("data/debug", { recursive: true });
writeFileSync("data/debug/page.html", html);
console.log({ status: resp?.status(), ms: Date.now() - t0, painted, innerTextChars: innerText.trim().length, htmlBytes: html.length });
console.log("innerText head:", innerText.trim().slice(0, 400).replace(/\n+/g, " | "));

const $ = cheerio.load(html);
console.log("#root children:", $("#root").children().length, "| main:", $("main").length, "| h1:", $("h1").length, "| p:", $("p").length);
const top = [];
$("#root *").each((_, el) => { const c = $(el).attr("class"); if (c) for (const k of c.split(/\s+/)) top.push(k); });
const freq = {};
for (const k of top) freq[k] = (freq[k] ?? 0) + 1;
console.log("top classes:", Object.entries(freq).sort((a, b) => b[1] - a[1]).slice(0, 25).map(([k, v]) => `${k}:${v}`).join(" "));

const ex = extractPage(html, url);
console.log({ title: ex.title, pageType: ex.pageType, textChars: ex.text.length, headings: ex.headings.length, links: ex.links.length });
console.log("markdown head:\n", ex.markdown.slice(0, 600));
