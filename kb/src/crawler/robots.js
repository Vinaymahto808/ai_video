// robots.txt: honour Disallow/Allow/Crawl-delay for our UA and harvest Sitemap: lines.
import robotsParser from "robots-parser";
import { log } from "../log.js";

export async function loadRobots(site, { userAgent, fetchImpl = fetch } = {}) {
  const robotsUrl = new URL("/robots.txt", site.startUrl).toString();
  let text = "";
  try {
    const res = await fetchImpl(robotsUrl, { headers: { "user-agent": userAgent }, signal: AbortSignal.timeout(15000) });
    if (res.ok) text = await res.text();
    else log.warn("robots.txt not available, crawling with defaults", { website: site.id, status: res.status });
  } catch (err) {
    log.warn("robots.txt fetch failed, crawling with defaults", { website: site.id, err });
  }
  const parser = robotsParser(robotsUrl, text);
  const crawlDelay = parser.getCrawlDelay(userAgent) ?? parser.getCrawlDelay("*") ?? 0;
  const sitemaps = parser.getSitemaps?.() ?? [];
  return {
    raw: text,
    sitemaps,
    crawlDelayMs: Math.round((crawlDelay || 0) * 1000),
    isAllowed: (url) => {
      const v = parser.isAllowed(url, userAgent);
      return v === undefined ? true : v;
    },
  };
}
