// Sitemap discovery: robots.txt Sitemap: lines -> /sitemap.xml -> recursive sitemap indexes.
import { log } from "../log.js";

const LOC_RE = /<loc>\s*([^<\s]+)\s*<\/loc>/gi;
const LASTMOD_RE = /<lastmod>\s*([^<\s]+)\s*<\/lastmod>/i;

/** Pure parser so it can be unit tested. Returns {kind:'index'|'urlset', entries:[{loc,lastmod}]} */
export function parseSitemap(xml) {
  const kind = /<sitemapindex[\s>]/i.test(xml) ? "index" : "urlset";
  const entries = [];
  const blockRe = kind === "index" ? /<sitemap[\s>][\s\S]*?<\/sitemap>/gi : /<url[\s>][\s\S]*?<\/url>/gi;
  const blocks = xml.match(blockRe) ?? [];
  for (const b of blocks) {
    const loc = [...b.matchAll(LOC_RE)][0]?.[1];
    if (!loc) continue;
    const lastmod = b.match(LASTMOD_RE)?.[1] ?? null;
    entries.push({ loc: decodeXml(loc.trim()), lastmod });
  }
  // some sitemaps omit <url> wrappers; fall back to bare <loc>s
  if (!blocks.length) for (const m of xml.matchAll(LOC_RE)) entries.push({ loc: decodeXml(m[1].trim()), lastmod: null });
  return { kind, entries };
}

function decodeXml(s) {
  return s.replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'");
}

/**
 * Collect every page URL reachable through sitemaps. Depth-limited, deduped, never throws.
 * `candidates` are extra sitemap URLs to try (robots.txt usually supplies them).
 */
export async function discoverSitemapUrls(site, { userAgent, candidates = [], fetchImpl = fetch, maxDepth = 3, maxSitemaps = 50 } = {}) {
  const start = new URL(site.startUrl);
  const seeds = new Set([...candidates, `${start.origin}/sitemap.xml`, `${start.origin}/sitemap_index.xml`, `${start.origin}/wp-sitemap.xml`]);
  const seen = new Set();
  const pages = new Map(); // loc -> lastmod
  const queue = [...seeds].map((u) => ({ url: u, depth: 0 }));
  let fetched = 0;

  while (queue.length && fetched < maxSitemaps) {
    const { url, depth } = queue.shift();
    if (seen.has(url) || depth > maxDepth) continue;
    seen.add(url);
    let xml;
    try {
      const res = await fetchImpl(url, { headers: { "user-agent": userAgent, accept: "application/xml,text/xml;q=0.9,*/*;q=0.1" }, signal: AbortSignal.timeout(20000) });
      fetched++;
      if (!res.ok) continue;
      const ct = res.headers.get("content-type") ?? "";
      xml = await res.text();
      if (!/xml/i.test(ct) && !/<(urlset|sitemapindex)/i.test(xml)) continue; // SPA shell or HTML 200
    } catch (err) {
      log.debug("sitemap fetch failed", { url, err });
      continue;
    }
    const { kind, entries } = parseSitemap(xml);
    if (kind === "index") {
      for (const e of entries) queue.push({ url: e.loc, depth: depth + 1 });
    } else {
      for (const e of entries) if (!pages.has(e.loc)) pages.set(e.loc, e.lastmod);
    }
    log.info("sitemap parsed", { website: site.id, url, kind, entries: entries.length });
  }
  return { pages: [...pages].map(([loc, lastmod]) => ({ loc, lastmod })), sitemapsFetched: fetched };
}
