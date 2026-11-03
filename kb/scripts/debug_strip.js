// For the saved data/debug/page.html: how many chars inside <main> each strip selector would remove.
import { readFileSync } from "node:fs";
import * as cheerio from "cheerio";

const html = readFileSync("data/debug/page.html", "utf8");
const SELECTORS = [
  "script", "style", "noscript", "template", "iframe", "svg", "canvas", "video", "audio", "object", "embed",
  "nav", "footer", "header", "aside", "form", "button", "input", "select", "textarea", "label",
  "[role=navigation]", "[role=banner]", "[role=contentinfo]", "[role=dialog]", "[aria-hidden=true]",
  "[hidden]", ".sr-only", ".visually-hidden",
  "[class*=cookie]", "[id*=cookie]", "[class*=consent]", "[class*=popup]", "[class*=modal]", "[class*=newsletter]",
  "[class*=breadcrumb]", "[class*=share]", "[class*=social]", "[class*=advert]", "[class*=banner-ad]",
  "[class*=whatsapp-float]", "[class*=floating]", "[class*=sticky-cta]", "[class*=chat-widget]",
  ".menu", ".navbar", ".nav-menu", ".site-header", ".site-footer", "#header", "#footer", "#menu", "#sidebar", ".sidebar",
];
const txt = (el) => el.text().replace(/\s+/g, " ").trim().length;
const $0 = cheerio.load(html);
const mainText = txt($0("main").first());
console.log("main chars:", mainText, "| body chars:", txt($0("body")));
for (const sel of SELECTORS) {
  const $ = cheerio.load(html);
  const m = $("main").first();
  const n = m.find(sel).length;
  if (!n) continue;
  m.find(sel).remove();
  const lost = mainText - txt(m);
  if (lost > 0) console.log(`${sel.padEnd(24)} matches=${n} removes=${lost} chars`);
}
const $ = cheerio.load(html);
console.log("\nmain outer structure:");
$("main").first().children().each((i, c) => console.log(` [${i}] <${c.tagName} class="${($(c).attr("class") || "").slice(0, 80)}"> ${txt($(c))} chars`));
