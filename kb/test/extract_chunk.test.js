import { test } from "node:test";
import assert from "node:assert/strict";
import { cleanMarkdown, extractPage } from "../src/extract/extract.js";
import { chunkMarkdown } from "../src/chunk/chunk.js";
import { parseSitemap } from "../src/crawler/sitemap.js";

const HTML = `<!doctype html><html lang="en"><head><title>Car AC Repair | ACR</title>
<meta name="description" content="AC repair in Gurgaon">
<link rel="canonical" href="https://autocarrepair.in/car-ac-repair/">
<script>window.x=1</script><style>.a{}</style></head>
<body>
<nav><a href="/">Home</a><a href="/services">Services</a></nav>
<div class="cookie-banner">We use cookies</div>
<main>
<h1>Car AC Repair in Gurgaon</h1>
<p>We repair car AC compressors, condensers and cooling coils for all brands.</p>
<h2>Pricing</h2>
<table><tr><th>Service</th><th>Price</th></tr><tr><td>AC gas top-up</td><td>Rs 1,499</td></tr><tr><td>Compressor</td><td>From Rs 8,000</td></tr></table>
<h2>FAQ</h2>
<ul><li>How long does it take? About 2 hours.</li><li>Warranty? 6 months on parts.</li></ul>
<a href="/contact?utm_source=footer">Contact</a>
<img src="/x.png" alt="ac">
</main>
<footer>© ACR · <a href="/privacy-policy">Privacy</a></footer>
</body></html>`;

test("extractPage keeps main content, drops nav/footer/scripts/cookie bar, converts tables", () => {
  const p = extractPage(HTML, "https://autocarrepair.in/car-ac-repair?utm_source=x");
  assert.equal(p.url, "https://autocarrepair.in/car-ac-repair");
  assert.equal(p.canonical, "https://autocarrepair.in/car-ac-repair");
  assert.equal(p.title, "Car AC Repair in Gurgaon");
  assert.equal(p.description, "AC repair in Gurgaon");
  assert.equal(p.pageType, "service");
  assert.match(p.markdown, /^# Car AC Repair in Gurgaon/);
  assert.match(p.markdown, /\| AC gas top-up \| Rs 1,499 \|/);
  assert.match(p.markdown, /- How long does it take\?/);
  assert.doesNotMatch(p.markdown, /cookies|window\.x|© ACR|Privacy/);
  assert.ok(p.links.includes("https://autocarrepair.in/contact"));
  assert.equal(p.contentHash.length, 64);
  assert.deepEqual(p.headings.map((h) => h.text), ["Car AC Repair in Gurgaon", "Pricing", "FAQ"]);
});

test("content hash is stable for identical content and changes when text changes", () => {
  const a = extractPage(HTML, "https://autocarrepair.in/a");
  const b = extractPage(HTML, "https://autocarrepair.in/a");
  const c = extractPage(HTML.replace("Rs 1,499", "Rs 1,599"), "https://autocarrepair.in/a");
  assert.equal(a.contentHash, b.contentHash);
  assert.notEqual(a.contentHash, c.contentHash);
});

test("cleanMarkdown dedupes repeated short lines and collapses blanks", () => {
  const md = "Home\n\n\n\nHome\nServices\n# Title\n---\nServices\nBody text here.";
  assert.equal(cleanMarkdown(md), "Home\n\nServices\n# Title\n\nBody text here.");
});

test("chunkMarkdown respects size, overlap, heading context and keeps tables whole", () => {
  const para = "Brake pads should be checked every ten thousand kilometres. ".repeat(40); // ~2400 chars
  const md = `# Brakes\n\n## Inspection\n\n${para}\n\n## Prices\n\n| Item | Price |\n| --- | --- |\n| Pads | 2500 |\n| Discs | 6000 |`;
  const chunks = chunkMarkdown(md, { size: 1200, overlap: 200, title: "Brakes" });
  assert.ok(chunks.length >= 3, `expected >=3 chunks, got ${chunks.length}`);
  for (const c of chunks) assert.ok(c.chars <= 1200 + 250, `chunk too long: ${c.chars}`);
  const inspection = chunks.filter((c) => c.heading === "Brakes > Inspection");
  assert.ok(inspection.length >= 2);
  assert.ok(inspection.every((c) => c.text.startsWith("Brakes > Inspection")));
  // overlap: the tail of chunk i reappears at the head of chunk i+1
  const tail = inspection[0].text.slice(-60);
  assert.ok(inspection[1].text.includes(tail.slice(0, 40)));
  const table = chunks.find((c) => c.heading === "Brakes > Prices");
  assert.match(table.text, /\| Pads \| 2500 \|\n\| Discs \| 6000 \|/);
  assert.equal(new Set(chunks.map((c) => c.index)).size, chunks.length);
});

test("chunkMarkdown drops noise-only chunks and is configurable", () => {
  const md = "# T\n\nok\n\n## S\n\n" + "word ".repeat(300);
  const small = chunkMarkdown(md, { size: 400, overlap: 50 });
  const big = chunkMarkdown(md, { size: 2000, overlap: 50 });
  assert.ok(small.length > big.length);
  assert.ok(!small.some((c) => c.text === "T\n\nok"));
});

test("parseSitemap handles urlset and sitemapindex", () => {
  const idx = parseSitemap(`<?xml version="1.0"?><sitemapindex><sitemap><loc>https://a.in/s1.xml</loc></sitemap><sitemap><loc>https://a.in/s2.xml</loc></sitemap></sitemapindex>`);
  assert.equal(idx.kind, "index");
  assert.deepEqual(idx.entries.map((e) => e.loc), ["https://a.in/s1.xml", "https://a.in/s2.xml"]);
  const set = parseSitemap(`<urlset><url><loc>https://a.in/</loc><lastmod>2026-01-01</lastmod></url><url><loc>https://a.in/x?a=1&amp;b=2</loc></url></urlset>`);
  assert.equal(set.kind, "urlset");
  assert.deepEqual(set.entries, [{ loc: "https://a.in/", lastmod: "2026-01-01" }, { loc: "https://a.in/x?a=1&b=2", lastmod: null }]);
});

test("looksLikeErrorPage flags SPA error boundaries but not real content", async () => {
  const { looksLikeErrorPage } = await import("../src/pipeline.js");
  assert.equal(looksLikeErrorPage({ text: "Something Broke. A part of the page hit an unexpected error. Reload Page" }), true);
  assert.equal(looksLikeErrorPage({ text: "Our Centres. Moti Nagar is an ACR facility equipped with diagnostic tools. ".repeat(30) }), false);
});

test("title keeps spaces between adjacent inline elements", () => {
  const p = extractPage(`<html><body><main><h1><span>Collision Repair</span><span>Without Compromise</span></h1><p>${"text ".repeat(40)}</p></main></body></html>`, "https://autocarrepair.in/");
  assert.equal(p.title, "Collision Repair Without Compromise");
});
