// Rendered HTML -> clean Markdown + metadata. Boilerplate (nav, footer, cookie bars, scripts) is
// removed structurally; what is left is converted with Turndown so headings, lists and tables survive.
import { createHash } from "node:crypto";
import * as cheerio from "cheerio";
import TurndownService from "turndown";
import { classifyPageType, normalizeUrl } from "../crawler/urls.js";

// Removed everywhere: never carries answerable text.
const HARD_STRIP = [
  "script", "style", "noscript", "template", "iframe", "svg", "canvas", "video", "audio", "object", "embed",
  "input", "select", "textarea", ".sr-only", ".visually-hidden",
  "[data-kb-copied]", // original accordion panel; the crawler copied its content next to the button
];
// Removed inside the chosen content container: site chrome and overlays. Buttons, labels, forms and
// hidden/collapsed panels are deliberately kept — on SPA sites FAQ questions are <button>s and the
// answers sit in collapsed panels.
const CHROME_STRIP = [
  "nav", "footer", "header", "aside",
  "[role=navigation]", "[role=banner]", "[role=contentinfo]", "[role=dialog]",
  "[class*=cookie]", "[id*=cookie]", "[class*=consent]", "[class*=newsletter]",
  "[class*=breadcrumb]", "[class*=share-]", "[class*=social-]", "[class*=advert]", "[class*=banner-ad]",
  "[class*=whatsapp-float]", "[class*=sticky-cta]", "[class*=chat-widget]",
  ".menu", ".navbar", ".nav-menu", ".site-header", ".site-footer", "#header", "#footer", "#menu", "#sidebar", ".sidebar",
  ".wp-block-comments", "#comments", ".comments-area", ".related-posts",
];

const MAIN_SELECTORS = ["main", "article", "[role=main]", "#main", "#content", ".main-content", ".content", "#root", "#app", "body"];

const td = new TurndownService({ headingStyle: "atx", codeBlockStyle: "fenced", bulletListMarker: "-", emDelimiter: "*" });
td.remove(["script", "style", "noscript"]);
// Tables become GitHub-style markdown so specs/prices stay aligned. Cheerio does the conversion
// (Turndown's DOM lacks querySelectorAll); a placeholder keeps the table's position through Turndown.
function tableToMarkdown($, table) {
  const rows = [];
  $(table).find("tr").each((_, tr) => {
    const cells = [];
    $(tr).find("th,td").each((__, c) => cells.push($(c).text().replace(/\s+/g, " ").trim().replace(/\|/g, "\\|")));
    if (cells.some((c) => c)) rows.push(cells);
  });
  if (!rows.length) return "";
  const width = Math.max(...rows.map((r) => r.length));
  const pad = (r) => [...r, ...Array(width - r.length).fill("")];
  const lines = [`| ${pad(rows[0]).join(" | ")} |`, `| ${Array(width).fill("---").join(" | ")} |`];
  for (const r of rows.slice(1)) lines.push(`| ${pad(r).join(" | ")} |`);
  return lines.join("\n");
}
const TABLE_TOKEN = (i) => `KBTABLEPLACEHOLDER${i}END`;
// images carry no answerable text; drop them, keep link text only
td.addRule("img", { filter: "img", replacement: () => "" });
td.addRule("links", { filter: "a", replacement: (content) => content });

export function sha256(s) {
  return createHash("sha256").update(s).digest("hex");
}

/** Collapse whitespace, dedupe repeated lines, strip markdown leftovers that carry no meaning. */
export function cleanMarkdown(md) {
  const seen = new Set();
  const out = [];
  for (let raw of md.split("\n")) {
    let line = raw.replace(/[ \t ]+/g, " ").replace(/\s+$/g, "");
    if (/^\s*[-*_]{3,}\s*$/.test(line)) line = ""; // hr
    if (/^\s*(\\|\*\*|__)\s*$/.test(line)) line = "";
    if (line.trim() === "") {
      if (out.length && out[out.length - 1] !== "") out.push("");
      continue;
    }
    const key = line.trim().toLowerCase();
    // same non-heading short line repeated (menus rendered twice, etc.)
    if (!line.startsWith("#") && key.length < 80 && seen.has(key)) continue;
    seen.add(key);
    out.push(line);
  }
  return out.join("\n").trim();
}

/**
 * @param {string} html rendered DOM
 * @param {string} url  final URL after redirects
 * @returns {{url,canonical,title,description,headings,markdown,text,links,contentHash,pageType,lang}}
 */
export function extractPage(html, url) {
  const $ = cheerio.load(html);
  // join text nodes with spaces so "<span>Collision Repair</span><span>Without Compromise</span>" keeps its gap
  const spaced = (el) => el.contents().map((_, n) => (n.type === "text" ? $(n).text() : spaced($(n)))).get().join(" ");
  const h1 = $("h1").first();
  const title = ((h1.length ? spaced(h1) : "") || $("meta[property='og:title']").attr("content") || $("title").text() || "").replace(/\s+/g, " ").trim();
  const description = ($("meta[name='description']").attr("content") || $("meta[property='og:description']").attr("content") || "").trim();
  const canonicalRaw = $("link[rel='canonical']").attr("href");
  const canonical = canonicalRaw ? normalizeUrl(canonicalRaw, url) ?? normalizeUrl(url) : normalizeUrl(url);
  const lang = ($("html").attr("lang") || "").slice(0, 5) || null;

  // internal links before we strip nav (the crawler still discovers links itself; these feed metadata)
  const links = new Set();
  $("a[href]").each((_, a) => {
    const n = normalizeUrl($(a).attr("href"), url);
    if (n) links.add(n);
  });

  $(HARD_STRIP.join(",")).remove();
  $("*").contents().each(function () {
    if (this.type === "comment") $(this).remove();
  });

  // pick the content container first, then remove chrome inside it (so a page whose only <main>
  // wrapper carries a "header"-like class is not emptied before we look at it)
  let $main = null;
  for (const sel of MAIN_SELECTORS) {
    const el = $(sel).first();
    if (el.length && el.text().replace(/\s+/g, " ").trim().length > 80) {
      $main = el;
      break;
    }
  }
  if (!$main) $main = $("body");
  $main.find(CHROME_STRIP.join(",")).remove();
  // a container that *is* chrome (body fallback) still loses top-level nav/footer
  if ($main.is("body")) $("body > nav, body > header, body > footer").remove();

  const headings = [];
  $main.find("h1,h2,h3").each((_, h) => {
    const text = $(h).text().replace(/\s+/g, " ").trim();
    if (text) headings.push({ level: Number(h.tagName[1]), text });
  });

  const tables = [];
  $main.find("table").each((i, t) => {
    const md = tableToMarkdown($, t);
    tables.push(md);
    $(t).replaceWith(`<p>${TABLE_TOKEN(i)}</p>`);
  });
  let markdown = td.turndown($main.html() ?? "").replace(/ /g, " ");
  tables.forEach((md, i) => { markdown = markdown.replace(TABLE_TOKEN(i), md ? `\n\n${md}\n\n` : ""); });
  markdown = cleanMarkdown(markdown);
  if (title && !markdown.startsWith("# ")) markdown = `# ${title}\n\n${markdown}`;
  const text = markdown.replace(/[#*_>|`-]+/g, " ").replace(/\s+/g, " ").trim();

  return {
    url: normalizeUrl(url),
    canonical,
    title,
    description,
    headings,
    markdown,
    text,
    links: [...links],
    contentHash: sha256(markdown),
    pageType: classifyPageType(url, title),
    lang,
  };
}
