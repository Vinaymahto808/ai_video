// Programmatic API of web-avatar-crawler.
//
//   import { createKnowledgeBase } from "web-avatar-crawler";
//   const kb = createKnowledgeBase();                       // data/kb.sqlite in the cwd
//   await kb.crawl("https://example.com", { maxPages: 50 });
//   const hits = await kb.search("https://example.com", "opening hours");
//   kb.serve(4100);                                          // REST API (see README)
//
// Lower-level pieces are exported too so a host app can run only the parts it needs.
import { settings, websites, getWebsite, defineWebsite, registerWebsite, DATA_DIR, ROOT_DIR } from "./config.js";
import { KnowledgeStore } from "./store/sqlite.js";
import { JobManager } from "./jobs.js";
import { createApp } from "./server.js";
import { embedQuery, embedTexts, embeddingInfo, EmbeddingError } from "./embed/gemini.js";
import { extractPage, cleanMarkdown } from "./extract/extract.js";
import { chunkMarkdown } from "./chunk/chunk.js";
import { runCrawl } from "./crawler/crawl.js";
import { normalizeUrl, isAllowedUrl, classifyPageType } from "./crawler/urls.js";
import { parseSitemap, discoverSitemapUrls } from "./crawler/sitemap.js";
import { loadRobots } from "./crawler/robots.js";
import { processPage, looksLikeErrorPage } from "./pipeline.js";
import { log } from "./log.js";

export {
  settings, websites, getWebsite, defineWebsite, registerWebsite, DATA_DIR, ROOT_DIR,
  KnowledgeStore, JobManager, createApp,
  embedQuery, embedTexts, embeddingInfo, EmbeddingError,
  extractPage, cleanMarkdown, chunkMarkdown,
  runCrawl, normalizeUrl, isAllowedUrl, classifyPageType, parseSitemap, discoverSitemapUrls, loadRobots,
  processPage, looksLikeErrorPage, log,
};

/**
 * One object that owns a store + job manager.
 * @param {{dbPath?: string}} [opts]
 */
export function createKnowledgeBase(opts = {}) {
  const store = new KnowledgeStore(opts.dbPath ?? settings.dbPath);
  for (const w of Object.values(websites)) store.upsertWebsite(w);
  const jobs = new JobManager(store);
  let server = null;
  return {
    store,
    jobs,
    /** Register a site with custom options before crawling (optional; URLs auto-register). */
    addWebsite: (url, siteOpts) => { const s = registerWebsite(defineWebsite(url, siteOpts)); store.upsertWebsite(s); return s; },
    /** Incremental crawl ('crawl'), 'refresh' (deactivates vanished pages) or 'reindex' (wipe first). */
    async crawl(websiteIdOrUrl, crawlOpts = {}, mode = "crawl") {
      jobs.start(websiteIdOrUrl, crawlOpts, mode);
      await jobs.wait();
      return { job: jobs.status(), stats: store.stats(getWebsite(websiteIdOrUrl)?.id) };
    },
    stop: () => jobs.stop(),
    status: () => jobs.status(),
    stats: (websiteIdOrUrl) => store.stats(websiteIdOrUrl ? getWebsite(websiteIdOrUrl)?.id : undefined),
    /** Semantic search; returns [{score,url,title,page_type,heading,text}] above minScore. */
    async search(websiteIdOrUrl, query, { k = settings.search.defaultK, pageType, minScore = settings.search.minScore } = {}) {
      const site = getWebsite(websiteIdOrUrl);
      if (!site) throw new Error(`unknown website: ${websiteIdOrUrl}`);
      const vec = await embedQuery(query);
      return store.search(site.id, vec, { k, pageType, minScore });
    },
    /** Start the REST API; resolves to the http.Server. */
    serve(port = settings.port, host = settings.host) {
      return new Promise((resolve) => {
        server = createApp({ store, jobs }).listen(port, host, () => {
          log.info("kb listening", { url: `http://${host}:${port}`, db: store.path ?? settings.dbPath, embedding: embeddingInfo() });
          resolve(server);
        });
      });
    },
    async close() {
      jobs.stop();
      await jobs.wait().catch(() => {});
      if (server) await new Promise((r) => server.close(r));
      store.close();
    },
  };
}
