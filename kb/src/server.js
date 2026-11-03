// REST surface. Management routes need x-api-key (KB_ADMIN_KEY) or a loopback caller;
// /api/knowledge/search is open on loopback for the chatbot and rate-limited per IP.
import express from "express";
import { pathToFileURL } from "node:url";
import { settings, websites, getWebsite } from "./config.js";
import { embedQuery, embeddingInfo, queryEmbeddingStatus } from "./embed/gemini.js";
import { LOCAL_DIM, LOCAL_MODEL, embedQueryLocal, embedTextsLocal, loadLocal, localStatus } from "./embed/local.js";
import { JobManager } from "./jobs.js";
import { log } from "./log.js";
import { expandQuery } from "./search/hinglish.js";
import { KnowledgeStore } from "./store/sqlite.js";

/**
 * Local vectors for every site: load the CPU model, then embed whatever chunks lack a vector (resumable,
 * ~130 s for 900 chunks the first time, nothing afterwards). Search uses the local path only once a
 * site is fully covered, so a half-built index never answers.
 */
export async function prepareLocalVectors(store, siteIds, { onDone } = {}) {
  if (!LOCAL_MODEL) return false;
  if (!(await loadLocal())) return false;
  for (const id of siteIds) {
    const cov = store.localCoverage(id, LOCAL_MODEL);
    if (cov.have >= cov.want) continue;
    const t0 = Date.now();
    log.info("building local vectors", { website: id, missing: cov.want - cov.have });
    try {
      const n = await store.ensureLocalVectors(id, LOCAL_MODEL, LOCAL_DIM, (rows) => embedTextsLocal(rows));
      log.info("local vectors ready", { website: id, embedded: n, ms: Date.now() - t0 });
    } catch (err) {
      log.error("local vector build failed", { website: id, err });
    }
  }
  onDone?.();
  return true;
}

const MAX_CRAWL_CONCURRENCY = 2; // Chromium tabs; the laptop also runs TTS/LLM relays

export function createApp({ store, jobs }) {
  const app = express();
  app.disable("x-powered-by");
  if (settings.trustProxy) app.set("trust proxy", settings.trustProxy === "1" ? 1 : settings.trustProxy);
  app.use(express.json({ limit: "64kb" }));

  const isLoopback = (req) => ["127.0.0.1", "::1", "::ffff:127.0.0.1"].includes(req.ip);
  const hasAdminKey = (req) => Boolean(settings.adminKey) && req.get("x-api-key") === settings.adminKey;

  // ---- rate limit (fixed window, per IP) ----
  // The chatbot calls from loopback (or with the admin key) on behalf of every customer, so those
  // callers are exempt; the limit protects the public surface from one noisy client.
  const hits = new Map();
  app.use((req, res, next) => {
    if (isLoopback(req) || hasAdminKey(req)) return next();
    const now = Date.now();
    const key = req.ip;
    const e = hits.get(key) ?? { n: 0, t: now };
    if (now - e.t > 60_000) { e.n = 0; e.t = now; }
    e.n++;
    hits.set(key, e);
    if (hits.size > 5000) { // drop the oldest windows, not everyone
      for (const [k, v] of hits) { if (now - v.t > 60_000) hits.delete(k); if (hits.size <= 2500) break; }
      if (hits.size > 5000) hits.clear();
    }
    if (e.n > 120) return res.status(429).json({ error: "rate limited" });
    next();
  });

  const requireAdmin = (req, res, next) => {
    if (settings.adminKey) {
      if (hasAdminKey(req)) return next();
      return res.status(401).json({ error: "x-api-key required" });
    }
    if (isLoopback(req)) return next();
    return res.status(403).json({ error: "management endpoints are loopback-only until KB_ADMIN_KEY is set" });
  };

  const bad = (res, msg) => res.status(400).json({ error: msg });
  // registered sites only: the API never registers a new website (see config.getWebsite)
  const siteParam = (v) => (typeof v === "string" && v.length < 200 ? getWebsite(v, { register: false }) : null);

  app.get("/health", (req, res) => {
    const emb = queryEmbeddingStatus();
    const sites = Object.fromEntries(Object.keys(websites).map((id) => [id, store.stats(id).chunks]));
    const last = jobs.status();
    const degraded = [];
    if (!settings.embedding.apiKey) degraded.push("no embedding key");
    if (emb.circuit === "open") degraded.push("embedding circuit open (lexical fallback)");
    if (last.state === "failed") degraded.push("last crawl failed");
    if (!store.fts) degraded.push("no FTS5 (vector search only)");
    const local = localStatus();
    const coverage = Object.fromEntries(Object.keys(websites).map((id) => [id, store.localCoverage(id, LOCAL_MODEL)]));
    const body = { ok: degraded.length === 0, degraded, service: "web-avatar-crawler", job: last.state, uptime_s: Math.round(process.uptime()),
      query_path: settings.search.queryProvider === "local" && local.ready ? "hybrid-local" : "gemini" };
    if (isLoopback(req) || hasAdminKey(req)) Object.assign(body, { embedding: { ...embeddingInfo(), ...emb }, local: { ...local, coverage }, chunks: sites, fts: store.fts, rss_mb: Math.round(process.memoryUsage().rss / 1048576) });
    res.status(body.ok ? 200 : 503).json(body);
  });

  // ---- crawler management ----
  const startBody = (req, res) => {
    const { website, maxPages, maxConcurrency, renderJavaScript } = req.body ?? {};
    const site = siteParam(website);
    if (!site) return bad(res, `website must be one of ${Object.keys(websites).join(", ")} or a URL on their domains`);
    if (maxPages !== undefined && (!Number.isInteger(maxPages) || maxPages < 1 || maxPages > settings.crawl.maxPages)) return bad(res, `maxPages must be 1..${settings.crawl.maxPages}`);
    if (maxConcurrency !== undefined && (!Number.isInteger(maxConcurrency) || maxConcurrency < 1 || maxConcurrency > MAX_CRAWL_CONCURRENCY)) return bad(res, `maxConcurrency must be 1..${MAX_CRAWL_CONCURRENCY}`);
    if (renderJavaScript !== undefined && typeof renderJavaScript !== "boolean") return bad(res, "renderJavaScript must be boolean");
    return { site, opts: { maxPages, maxConcurrency, renderJavaScript } };
  };
  const launch = (mode) => (req, res) => {
    const parsed = startBody(req, res);
    if (!parsed) return;
    try {
      res.status(202).json(jobs.start(parsed.site.id, parsed.opts, mode));
    } catch (err) {
      if (err.code === "BUSY") return res.status(409).json({ error: err.message, status: jobs.status() });
      log.error("job start failed", { err });
      res.status(500).json({ error: "job start failed" }); // detail is in the log, not the response
    }
  };
  app.post("/api/crawler/start", requireAdmin, launch("crawl"));
  app.post("/api/crawler/refresh", requireAdmin, launch("refresh"));
  app.post("/api/knowledge/reindex", requireAdmin, launch("reindex"));
  app.post("/api/crawler/stop", requireAdmin, (_req, res) => res.json(jobs.stop()));
  app.get("/api/crawler/status", requireAdmin, (_req, res) => res.json(jobs.status()));
  app.get("/api/crawler/jobs", requireAdmin, (req, res) => {
    const site = req.query.website ? siteParam(String(req.query.website)) : null;
    res.json(store.jobs(site?.id ?? "", Math.min(Number(req.query.limit) || 10, 100)));
  });
  app.get("/api/crawler/pages", requireAdmin, (req, res) => {
    const site = siteParam(String(req.query.website ?? ""));
    if (!site) return bad(res, "website required");
    const status = ["", "active", "inactive", "failed"].includes(String(req.query.status ?? "")) ? String(req.query.status ?? "") : "";
    const limit = Math.min(Math.max(Number(req.query.limit) || 100, 1), 500);
    const offset = Math.max(Number(req.query.offset) || 0, 0);
    res.json({ website_id: site.id, pages: store.listPages(site.id, { status, limit, offset }), limit, offset });
  });
  app.get("/api/crawler/stats", requireAdmin, (req, res) => {
    const site = req.query.website ? siteParam(String(req.query.website)) : null;
    const ids = site ? [site.id] : Object.keys(websites);
    res.json({ job: jobs.status(), websites: ids.map((id) => store.stats(id)) });
  });

  // ---- knowledge search (used by human_bot) ----
  const search = async (req, res) => {
    const src = req.method === "GET" ? req.query : (req.body ?? {});
    const q = String(src.q ?? src.query ?? "").trim();
    const site = siteParam(String(src.website ?? ""));
    if (!site) return bad(res, "website required");
    if (!q || q.length > 1000) return bad(res, "q must be 1..1000 chars");
    const k = Math.min(Math.max(Number(src.k) || settings.search.defaultK, 1), 20);
    const minScore = src.min_score !== undefined ? Number(src.min_score) : settings.search.minScore;
    if (Number.isNaN(minScore) || minScore < -1 || minScore > 1) return bad(res, "min_score must be -1..1");
    const pageType = src.page_type ? String(src.page_type).split(",").map((s) => s.trim()).filter(Boolean) : undefined;
    const t0 = Date.now();
    const provider = String(src.provider ?? settings.search.queryProvider).toLowerCase(); // per-request override for A/B
    const expanded = expandQuery(q);
    let mode;
    let results;
    // 1. Low-latency path (default): local CPU vectors + lexical index, rank-fused. ~20-100 ms, no quota.
    //    Used only when the model is loaded and the site is fully covered (never a half-built index).
    if (provider === "local" && localStatus().ready && store.fts) {
      const cov = store.localCoverage(site.id, LOCAL_MODEL);
      if (cov.want > 0 && cov.have >= cov.want) {
        try {
          const qv = await embedQueryLocal(expanded);
          const vec = store.searchLocal(site.id, LOCAL_MODEL, qv, { k: 20, pageType });
          const lex = store.searchText(site.id, expanded, { k: 20, pageType });
          results = KnowledgeStore.fuse(vec, lex, { k });
          mode = "hybrid-local";
        } catch (err) {
          log.warn("local search failed, using gemini", { err: String(err.message).slice(0, 160) });
        }
      }
    }
    // 2. Quality path: Gemini vector search inside its deadline (settings.search.embedTimeoutMs; cache hits
    //    are instant), fused with the lexical index when available.
    if (!results) {
      try {
        const vec = await embedQuery(q);
        const vecHits = store.search(site.id, vec, { k: store.fts ? 20 : k, pageType, minScore });
        results = store.fts ? KnowledgeStore.fuse(vecHits, store.searchText(site.id, expanded, { k: 20, pageType }), { k }) : vecHits;
        mode = store.fts ? "hybrid-gemini" : "vector";
      } catch (err) {
        // 3. Fallback: lexical only, so a slow or rate-limited Gemini degrades ranking, not the wait.
        if (!store.fts) {
          log.error("search failed", { err });
          return res.status(503).json({ error: "search unavailable" });
        }
        mode = "lexical";
        log.warn("query embedding unavailable, lexical fallback", { err: String(err.message).slice(0, 160) });
        results = store.searchText(site.id, expanded, { k, pageType });
      }
    }
    res.json({ website_id: site.id, query: q, k, min_score: minScore, mode, embedding: embeddingInfo(), latency_ms: Date.now() - t0, results });
  };
  app.get("/api/knowledge/search", search);
  app.post("/api/knowledge/search", search);

  app.use((_req, res) => res.status(404).json({ error: "not found" }));
  app.use((err, _req, res, _next) => { // eslint-disable-line no-unused-vars
    log.error("unhandled", { err });
    res.status(err.type === "entity.parse.failed" ? 400 : 500).json({ error: err.type === "entity.parse.failed" ? "invalid JSON" : "internal error" });
  });
  return app;
}

// start the server only when this file is the entry point (not when imported by index.js / cli.js / -e)
if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  const loopbackHost = ["127.0.0.1", "::1", "localhost"].includes(settings.host);
  if (!loopbackHost && !settings.adminKey) {
    // Off loopback every management route (crawl, reindex = wipe) would be open to the network.
    log.error("refusing to start: KB_HOST is not loopback and KB_ADMIN_KEY is not set", { host: settings.host });
    process.exit(2);
  }
  const store = new KnowledgeStore(settings.dbPath);
  if (!store.fts) log.warn("FTS5 unavailable: no lexical fallback", { err: store.ftsError });
  for (const w of Object.values(websites)) store.upsertWebsite(w);
  const jobs = new JobManager(store);
  const app = createApp({ store, jobs });
  const srv = app.listen(settings.port, settings.host, () => log.info("kb listening", { url: `http://${settings.host}:${settings.port}`, db: settings.dbPath, embedding: embeddingInfo(), fts: store.fts }));
  // local low-latency index: model load (~19 s) + any missing chunk vectors, in the background; search uses
  // Gemini until it is ready. Re-run after every crawl job so new pages get local vectors too.
  prepareLocalVectors(store, Object.keys(websites)).catch((err) => log.error("local vectors", { err }));
  jobs.onFinished = () => prepareLocalVectors(store, Object.keys(websites)).catch(() => {});
  srv.keepAliveTimeout = 65_000; // longer than a typical proxy's upstream keep-alive
  let stopping = false;
  const shutdown = async (sig) => {
    if (stopping) return;
    stopping = true;
    log.info("shutting down", { sig });
    const force = setTimeout(() => { log.warn("shutdown deadline hit, exiting"); process.exit(1); }, 10_000);
    force.unref();
    jobs.stop();
    srv.close();
    srv.closeAllConnections?.();
    await jobs.wait().catch(() => {});
    store.checkpoint();
    store.close();
    process.exit(0);
  };
  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);
  process.on("unhandledRejection", (err) => log.error("unhandled rejection", { err }));
  process.on("uncaughtException", (err) => { log.error("uncaught exception", { err }); shutdown("uncaughtException"); });
}
