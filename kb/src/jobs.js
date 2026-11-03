// One crawl job at a time per process. Owns the stop flag, live stats, and the post-crawl
// deactivation of pages that disappeared (guarded so a bad crawl cannot erase knowledge).
import { resolve } from "node:path";
import { settings, getWebsite, DATA_DIR } from "./config.js";
import { runCrawl } from "./crawler/crawl.js";
import { log } from "./log.js";
import { processPage } from "./pipeline.js";
import { assertConfigured } from "./embed/gemini.js";

export class JobManager {
  constructor(store) {
    this.store = store;
    this.active = null; // {id, website, mode, startedAt, stats, stopRequested, promise}
    this.last = null;
  }

  status() {
    const j = this.active ?? this.last;
    if (!j) return { state: "idle" };
    return {
      state: this.active ? "running" : j.state,
      job_id: j.id, website_id: j.website.id, mode: j.mode,
      started_at: j.startedAt, finished_at: j.finishedAt ?? null,
      stop_requested: Boolean(j.stopRequested),
      stats: this._stats(j),
      error: j.error ?? null,
    };
  }

  _stats(j) {
    const s = j.stats;
    return {
      discovered: s.discovered, seeded: s.seeded, crawled: s.crawled, failed: s.failed,
      indexed: s.indexed, updated: s.updated, unchanged: s.unchanged, thin: s.thin,
      embed_failed: s.embedFailed, chunks_indexed: s.chunksIndexed,
      robots_blocked: s.robotsBlocked, offsite: s.offsite,
      pending: Math.max(0, s.discovered - s.crawled - s.failed),
      deactivated: s.deactivated ?? 0, deactivation_skipped: s.deactivationSkipped ?? false,
      duration_ms: (j.finishedAt ?? Date.now()) - j.startedAt,
    };
  }

  /**
   * mode: 'crawl' (incremental), 'refresh' (same, but deactivates unseen pages when complete),
   *       'reindex' (wipe this website first, then full crawl)
   */
  start(websiteIdOrUrl, opts = {}, mode = "crawl") {
    if (this.active) throw Object.assign(new Error(`job ${this.active.id} already running for ${this.active.website.id}`), { code: "BUSY" });
    const site = getWebsite(websiteIdOrUrl);
    if (!site) throw Object.assign(new Error(`unknown website: ${websiteIdOrUrl}`), { code: "UNKNOWN_WEBSITE" });
    assertConfigured();

    this.store.upsertWebsite(site);
    if (mode === "reindex") {
      // The db is the only copy of the crawl: snapshot it before the wipe, so a site outage or an
      // exhausted embedding quota mid-reindex can be undone by copying the backup back.
      const stamp = new Date().toISOString().replace(/[:.]/g, "-");
      const file = resolve(DATA_DIR, "backups", `kb-before-reindex-${site.id}-${stamp}.sqlite`);
      this.store.backupTo(file);
      log.info("backup written before reindex", { file });
      this.store.wipeWebsite(site.id);
    }
    const id = this.store.startJob(site.id, mode);
    const job = {
      id, website: site, mode, startedAt: Date.now(), stopRequested: false, state: "running",
      stats: { discovered: 0, seeded: 0, crawled: 0, failed: 0, indexed: 0, updated: 0, unchanged: 0, thin: 0, embedFailed: 0, chunksIndexed: 0, robotsBlocked: 0, offsite: 0 },
    };
    this.active = job;
    const jl = log.child({ job: id, website: site.id, mode });
    jl.info("job started", { maxPages: opts.maxPages, maxConcurrency: opts.maxConcurrency });

    job.promise = runCrawl(site, { ...opts, resume: false }, {
      shouldStop: () => job.stopRequested,
      onPage: async ({ finalUrl, html, httpStatus }) => {
        try {
          const r = await processPage({ store: this.store, site, url: finalUrl, html, httpStatus, jobId: id });
          if (r.outcome === "indexed") { job.stats.indexed++; job.stats.chunksIndexed += r.chunks; if (r.changed) job.stats.updated++; }
          else if (r.outcome === "unchanged") job.stats.unchanged++;
          else job.stats.thin++;
        } catch (err) {
          job.stats.embedFailed++;
          try { this.store.recordFailure(site.id, finalUrl, id, httpStatus, err.message); } catch { /* busy db */ }
          jl.error("page processing failed", { url: finalUrl, err });
        }
      },
      onFailed: async ({ url, httpStatus, error }) => {
        try { this.store.recordFailure(site.id, url, id, httpStatus, error); }
        catch (err) { jl.error("could not record failure", { url, err }); } // a busy db must not crash the job
      },
    }).then((crawlStats) => {
      Object.assign(job.stats, {
        discovered: crawlStats.discovered, seeded: crawlStats.seeded, crawled: crawlStats.crawled,
        failed: crawlStats.failed, robotsBlocked: crawlStats.robotsBlocked, offsite: crawlStats.offsite,
      });
      const complete = !crawlStats.stopped && crawlStats.crawled + crawlStats.failed >= crawlStats.discovered * 0.9;
      if ((mode === "refresh" || mode === "reindex") && complete) {
        const seen = job.stats.indexed + job.stats.unchanged;
        const d = this.store.deactivateUnseen(site.id, id, seen, settings.crawl.deactivateGuardRatio);
        job.stats.deactivated = d.deactivated;
        job.stats.deactivationSkipped = d.skipped;
        if (d.skipped) jl.warn("deactivation skipped: crawl saw too few pages", { active: d.active, seen });
        else if (d.deactivated) jl.info("pages deactivated", { n: d.deactivated, urls: d.urls.slice(0, 20) });
      }
      job.state = crawlStats.stopped ? "stopped" : "completed";
    }).catch((err) => {
      job.state = "failed";
      job.error = err.message;
      jl.error("job failed", { err });
    }).finally(() => {
      job.finishedAt = Date.now();
      this.store.finishJob(id, job.state, this._stats(job), job.error);
      this.store.checkpoint(); // fold the WAL into kb.sqlite so a file-level backup is complete
      jl.info("job finished", { state: job.state, ...this._stats(job) });
      this.last = job;
      this.active = null;
      try { this.onFinished?.(job); } catch { /* listener errors never affect the job */ }
    });
    return { job_id: id, website_id: site.id, mode };
  }

  stop() {
    if (!this.active) return { stopped: false, reason: "no active job" };
    this.active.stopRequested = true;
    return { stopped: true, job_id: this.active.id };
  }

  /** Await the running job (CLI use). */
  async wait() { if (this.active) await this.active.promise; }
}
