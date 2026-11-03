// Crawlee + Playwright site crawl. Seeds from sitemaps, discovers internal links, respects
// robots.txt, stays on approved hosts, and hands every rendered page to `onPage`.
import { Configuration, PlaywrightCrawler, log as crawleeLog } from "crawlee";
import { settings } from "../config.js";
import { log } from "../log.js";
import { loadRobots } from "./robots.js";
import { discoverSitemapUrls } from "./sitemap.js";
import { isAllowedUrl, normalizeUrl } from "./urls.js";

crawleeLog.setLevel(crawleeLog.LEVELS.WARNING);

// Stylesheets stay enabled: Vite/React route chunks preload their CSS and throw into an error
// boundary ("Something Broke") when that request is aborted.
const BLOCKED_RESOURCES = new Set(["image", "media", "font"]);

/**
 * SPA settle: wait until real text is painted (not networkidle — analytics beacons keep sites
 * "busy" for 15 s+), let React finish, then expand collapsed accordions / <details> / "read more"
 * so FAQ answers and spec panels are in the DOM when we snapshot.
 */
export async function settleSpa(page) {
  await page.waitForFunction(() => (document.body?.innerText ?? "").trim().length > 200, null, { timeout: 10000 }).catch(() => {});
  // text length stable across two 400 ms reads => the app has finished mounting sections
  await page.waitForFunction(() => new Promise((resolve) => {
    const len = () => (document.body?.innerText ?? "").length;
    const a = len();
    setTimeout(() => resolve(len() === a), 400);
  }), null, { timeout: 6000, polling: 450 }).catch(() => {});

  const expanded = await page.evaluate(async () => {
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    const sel = 'button[aria-expanded="false"], [role="button"][aria-expanded="false"], button[aria-controls]:not([aria-expanded="true"]), details:not([open]) > summary';
    const els = [...document.querySelectorAll(sel)].filter((e) => {
      const r = e.getBoundingClientRect();
      return e.closest("main, article, [role=main], #root, #app") && r.width > 0 && !e.closest("nav, header, footer");
    }).slice(0, 60);
    let n = 0;
    for (const e of els) {
      try {
        e.click();
        await sleep(120);
        // single-open accordions close the previous panel: copy this panel's HTML next to its button
        const id = e.getAttribute("aria-controls");
        const panel = id ? document.getElementById(id) : null;
        if (panel && panel.innerText.trim() && !panel.hasAttribute("data-kb-copied")) {
          const copy = document.createElement("div");
          copy.setAttribute("data-kb-expanded", "");
          copy.innerHTML = panel.innerHTML;
          panel.setAttribute("data-kb-copied", "");
          e.insertAdjacentElement("afterend", copy);
        }
        n++;
      } catch { /* ignore */ }
    }
    return n;
  }).catch(() => 0);
  if (expanded) await page.waitForTimeout(300);
  return expanded;
}
const BLOCKED_HOST_RE = /(googletagmanager|google-analytics|doubleclick|facebook\.net|hotjar|clarity\.ms|intercom|tawk\.to|zoho|whatsapp|youtube|vimeo)/i;

/**
 * @param {object} site           website config
 * @param {object} opts           {maxPages, maxConcurrency, renderJavaScript, resume}
 * @param {object} hooks          {onPage({url, finalUrl, html, httpStatus}), onFailed({url, error}), shouldStop()}
 * @returns stats
 */
export async function runCrawl(site, opts, hooks) {
  const ua = settings.userAgent;
  const maxPages = Math.min(opts.maxPages ?? site.maxPages ?? settings.crawl.maxPages, settings.crawl.maxPages);
  const maxConcurrency = Math.max(1, Math.min(opts.maxConcurrency ?? site.maxConcurrency ?? settings.crawl.maxConcurrency, 8));

  const stats = {
    discovered: 0, seeded: 0, crawled: 0, failed: 0, skipped: 0, robotsBlocked: 0, offsite: 0,
    startedAt: Date.now(), finishedAt: null, stopped: false,
  };

  const robots = await loadRobots(site, { userAgent: ua });
  const { pages: sitemapPages, sitemapsFetched } = await discoverSitemapUrls(site, { userAgent: ua, candidates: robots.sitemaps });
  stats.sitemaps = sitemapsFetched;

  const seen = new Set();
  const accept = (raw, base) => {
    const u = normalizeUrl(raw, base);
    if (!u) return null;
    if (!isAllowedUrl(u, site)) { stats.offsite++; return null; }
    if (!robots.isAllowed(u)) { stats.robotsBlocked++; return null; }
    if (seen.has(u)) return null;
    seen.add(u);
    stats.discovered++;
    return u;
  };

  const seeds = [];
  const start = accept(site.startUrl);
  if (start) seeds.push(start);
  for (const p of sitemapPages) {
    const u = accept(p.loc);
    if (u) seeds.push(u);
    if (seeds.length >= maxPages) break;
  }
  stats.seeded = seeds.length;
  log.info("crawl seeded", { website: site.id, seeds: seeds.length, sitemaps: sitemapsFetched, maxPages, maxConcurrency });

  const config = new Configuration({
    storageDir: `${settings.crawleeDir}/${site.id}`,
    persistStorage: true,
    purgeOnStart: !opts.resume,
  });

  const crawler = new PlaywrightCrawler({
    maxRequestsPerCrawl: maxPages,
    maxConcurrency,
    minConcurrency: 1,
    maxRequestRetries: settings.crawl.maxRetries,
    requestHandlerTimeoutSecs: settings.crawl.requestTimeoutSecs,
    navigationTimeoutSecs: settings.crawl.navigationTimeoutSecs,
    useSessionPool: false,
    persistCookiesPerSession: false,
    launchContext: {
      userAgent: ua,
      launchOptions: { headless: true, args: ["--disable-gpu", "--disable-dev-shm-usage", "--no-sandbox", "--js-flags=--max-old-space-size=256"] },
    },
    browserPoolOptions: { useFingerprints: false, retireBrowserAfterPageCount: 40 },
    preNavigationHooks: [
      async ({ page }) => {
        await page.route("**/*", (route) => {
          const req = route.request();
          if (BLOCKED_RESOURCES.has(req.resourceType()) || BLOCKED_HOST_RE.test(req.url())) return route.abort();
          return route.continue();
        });
      },
    ],
    async requestHandler({ request, page, response, enqueueLinks }) {
      if (hooks.shouldStop?.()) { stats.stopped = true; await crawler.autoscaledPool?.abort(); return; }
      if (robots.crawlDelayMs) await page.waitForTimeout(robots.crawlDelayMs);

      await settleSpa(page);

      const finalUrl = normalizeUrl(page.url()) ?? request.url;
      const httpStatus = response?.status() ?? null;
      if (finalUrl !== request.url && !isAllowedUrl(finalUrl, site)) { stats.offsite++; return; } // redirected off-site
      if (httpStatus && httpStatus >= 400) {
        stats.failed++;
        await hooks.onFailed?.({ url: request.url, httpStatus, error: `HTTP ${httpStatus}` });
        return;
      }
      const html = await page.content();
      stats.crawled++;
      await hooks.onPage?.({ url: request.url, finalUrl, html, httpStatus });

      await enqueueLinks({
        strategy: "all",
        transformRequestFunction: (req) => {
          const u = accept(req.url, finalUrl);
          if (!u) return false;
          req.url = u;
          req.uniqueKey = u;
          return req;
        },
      });
    },
    async failedRequestHandler({ request }, error) {
      stats.failed++;
      log.warn("page failed after retries", { website: site.id, url: request.url, err: error });
      await hooks.onFailed?.({ url: request.url, httpStatus: null, error: error?.message ?? String(error) });
    },
  }, config);

  try {
    await crawler.run(seeds);
  } finally {
    stats.finishedAt = Date.now();
    stats.durationMs = stats.finishedAt - stats.startedAt;
    await crawler.teardown?.().catch(() => {});
  }
  return stats;
}
