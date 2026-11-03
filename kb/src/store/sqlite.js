// SQLite store (node:sqlite, no native build). Vectors live as Float32 BLOBs; search is an exact
// cosine scan per website, cached in memory. At a few thousand chunks this is sub-10 ms. The public
// surface (upsertWebsite / getPage / replacePage / markInactive / search / stats) is what a pgvector
// store would implement when Postgres is available.
import { DatabaseSync } from "node:sqlite";
import { mkdirSync } from "node:fs";
import { dirname } from "node:path";

const SCHEMA = `
CREATE TABLE IF NOT EXISTS websites (
  id TEXT PRIMARY KEY, name TEXT, domain TEXT, start_url TEXT,
  created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS pages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  website_id TEXT NOT NULL REFERENCES websites(id),
  url TEXT NOT NULL, canonical TEXT, title TEXT, description TEXT, page_type TEXT, lang TEXT,
  content_hash TEXT, status TEXT NOT NULL DEFAULT 'active', http_status INTEGER, error TEXT,
  chunk_count INTEGER NOT NULL DEFAULT 0,
  first_seen_at INTEGER NOT NULL, last_crawled_at INTEGER, last_changed_at INTEGER, last_seen_job INTEGER,
  UNIQUE(website_id, url)
);
CREATE INDEX IF NOT EXISTS pages_site_status ON pages(website_id, status);
CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
  website_id TEXT NOT NULL,
  chunk_index INTEGER NOT NULL, heading TEXT, text TEXT NOT NULL, content_hash TEXT NOT NULL,
  embedding BLOB NOT NULL, dim INTEGER NOT NULL, model TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  UNIQUE(page_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS chunks_site ON chunks(website_id);
CREATE TABLE IF NOT EXISTS crawl_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  website_id TEXT NOT NULL, mode TEXT NOT NULL, status TEXT NOT NULL,
  started_at INTEGER NOT NULL, finished_at INTEGER, stats TEXT, error TEXT
);
-- second vector per chunk from the local CPU model (embed/local.js); the Gemini vector in chunks.embedding stays
CREATE TABLE IF NOT EXISTS chunk_vectors (
  chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
  model TEXT NOT NULL, dim INTEGER NOT NULL, embedding BLOB NOT NULL,
  PRIMARY KEY(chunk_id, model)
);
`;

// Lexical index over the same chunks (external-content FTS5, kept in step by triggers). It answers in
// ~1 ms with no API quota: the search route uses it when the query embedding misses its deadline or
// the Gemini circuit is open, so a Gemini outage degrades retrieval instead of removing it.
const FTS_SCHEMA = `
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, heading, content='chunks', content_rowid='id', tokenize='unicode61');
CREATE TRIGGER IF NOT EXISTS chunks_fts_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, text, heading) VALUES (new.id, new.text, new.heading);
END;
CREATE TRIGGER IF NOT EXISTS chunks_fts_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text, heading) VALUES ('delete', old.id, old.text, old.heading);
END;
CREATE TRIGGER IF NOT EXISTS chunks_fts_au AFTER UPDATE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text, heading) VALUES ('delete', old.id, old.text, old.heading);
  INSERT INTO chunks_fts(rowid, text, heading) VALUES (new.id, new.text, new.heading);
END;
`;

const now = () => Date.now();

export class KnowledgeStore {
  constructor(path) {
    if (path !== ":memory:") mkdirSync(dirname(path), { recursive: true });
    this.db = new DatabaseSync(path);
    // busy_timeout: a CLI crawl and the server can share the file; without it a concurrent write
    // throws SQLITE_BUSY immediately and the page is recorded as a failure.
    this.db.exec("PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON; PRAGMA synchronous=NORMAL; PRAGMA busy_timeout=5000;");
    this.db.exec(SCHEMA);
    this.fts = false;
    try {
      this.db.exec(FTS_SCHEMA);
      // first run on an existing db: index what is already there. (COUNT(*) on an external-content
      // FTS table reads the content table, so the docsize shadow table is what says what is indexed.)
      const have = this.db.prepare("SELECT COUNT(*) n FROM chunks_fts_docsize").get().n;
      const want = this.db.prepare("SELECT COUNT(*) n FROM chunks").get().n;
      if (have !== want) this.db.exec("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')");
      this.fts = true;
    } catch (err) {
      // SQLite built without FTS5: vector search only (logged by the caller through `fts`)
      this.ftsError = err.message;
    }
    this._cache = new Map(); // website_id -> {version, rows:[{id,page_id,vec,...}]}
    this._localCache = new Map(); // website_id|model -> same, for the local-model vectors
    this._version = new Map();
    this._prep();
  }

  _prep() {
    const d = this.db;
    this.q = {
      upsertSite: d.prepare(`INSERT INTO websites(id,name,domain,start_url,created_at,updated_at) VALUES(?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET name=excluded.name, domain=excluded.domain, start_url=excluded.start_url, updated_at=excluded.updated_at`),
      getPage: d.prepare(`SELECT * FROM pages WHERE website_id=? AND url=?`),
      insertPage: d.prepare(`INSERT INTO pages(website_id,url,canonical,title,description,page_type,lang,content_hash,status,http_status,error,chunk_count,first_seen_at,last_crawled_at,last_changed_at,last_seen_job)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`),
      updatePage: d.prepare(`UPDATE pages SET canonical=?,title=?,description=?,page_type=?,lang=?,content_hash=?,status=?,http_status=?,error=?,chunk_count=?,last_crawled_at=?,last_changed_at=?,last_seen_job=? WHERE id=?`),
      touchPage: d.prepare(`UPDATE pages SET last_crawled_at=?, last_seen_job=?, status='active', http_status=?, error=NULL WHERE id=?`),
      failPage: d.prepare(`UPDATE pages SET last_crawled_at=?, last_seen_job=?, http_status=?, error=? WHERE id=?`),
      deleteChunks: d.prepare(`DELETE FROM chunks WHERE page_id=?`),
      insertChunk: d.prepare(`INSERT INTO chunks(page_id,website_id,chunk_index,heading,text,content_hash,embedding,dim,model,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)`),
      chunkModel: d.prepare(`SELECT model, dim, COUNT(*) n FROM chunks WHERE website_id=? GROUP BY model, dim`),
      loadVecs: d.prepare(`SELECT c.id, c.page_id, c.chunk_index, c.heading, c.text, c.embedding, c.dim, p.url, p.title, p.page_type
        FROM chunks c JOIN pages p ON p.id=c.page_id WHERE c.website_id=? AND p.status='active'`),
      listPages: d.prepare(`SELECT id,url,title,page_type,status,http_status,content_hash,chunk_count,last_crawled_at,last_changed_at,error FROM pages WHERE website_id=? AND (?='' OR status=?) ORDER BY id LIMIT ? OFFSET ?`),
      countPages: d.prepare(`SELECT status, COUNT(*) n FROM pages WHERE website_id=? GROUP BY status`),
      countChunks: d.prepare(`SELECT COUNT(*) n FROM chunks WHERE website_id=?`),
      activeNotSeen: d.prepare(`SELECT id,url FROM pages WHERE website_id=? AND status='active' AND (last_seen_job IS NULL OR last_seen_job<>?)`),
      countActive: d.prepare(`SELECT COUNT(*) n FROM pages WHERE website_id=? AND status='active'`),
      setInactive: d.prepare(`UPDATE pages SET status='inactive' WHERE id=?`),
      insertJob: d.prepare(`INSERT INTO crawl_jobs(website_id,mode,status,started_at) VALUES(?,?,?,?)`),
      finishJob: d.prepare(`UPDATE crawl_jobs SET status=?, finished_at=?, stats=?, error=? WHERE id=?`),
      lastJobs: d.prepare(`SELECT * FROM crawl_jobs WHERE (?='' OR website_id=?) ORDER BY id DESC LIMIT ?`),
      wipeSite: d.prepare(`DELETE FROM pages WHERE website_id=?`),
      dataVersion: d.prepare(`PRAGMA data_version`),
      missingLocal: d.prepare(`SELECT c.id, c.text, p.title FROM chunks c JOIN pages p ON p.id=c.page_id
        WHERE c.website_id=? AND NOT EXISTS (SELECT 1 FROM chunk_vectors v WHERE v.chunk_id=c.id AND v.model=?) LIMIT ?`),
      countLocal: d.prepare(`SELECT COUNT(*) n FROM chunk_vectors v JOIN chunks c ON c.id=v.chunk_id WHERE c.website_id=? AND v.model=?`),
      insertLocal: d.prepare(`INSERT OR REPLACE INTO chunk_vectors(chunk_id, model, dim, embedding) VALUES(?,?,?,?)`),
      loadLocal: d.prepare(`SELECT c.id, c.page_id, c.chunk_index, c.heading, c.text, v.embedding, v.dim, p.url, p.title, p.page_type
        FROM chunk_vectors v JOIN chunks c ON c.id=v.chunk_id JOIN pages p ON p.id=c.page_id
        WHERE c.website_id=? AND v.model=? AND p.status='active'`),
      ftsSearch: this.fts ? d.prepare(`SELECT c.id, c.page_id, c.chunk_index, c.heading, c.text, p.url, p.title, p.page_type, bm25(chunks_fts, 1.0, 0.6) AS rank
        FROM chunks_fts f JOIN chunks c ON c.id = f.rowid JOIN pages p ON p.id = c.page_id
        WHERE chunks_fts MATCH ? AND c.website_id = ? AND p.status = 'active' ORDER BY rank LIMIT ?`) : null,
    };
  }

  close() { this.db.close(); }

  /** Consistent snapshot copy (VACUUM INTO): safe while the server runs, includes everything in the WAL. */
  backupTo(path) {
    mkdirSync(dirname(path), { recursive: true });
    this.db.exec(`VACUUM INTO '${String(path).replace(/'/g, "''")}'`);
    return path;
  }

  /** Fold the WAL back into the main file (after a job): a plain file copy of kb.sqlite then has everything. */
  checkpoint() { try { this.db.exec("PRAGMA wal_checkpoint(TRUNCATE)"); } catch { /* reader active: next time */ } }

  bump(websiteId) { this._version.set(websiteId, (this._version.get(websiteId) ?? 0) + 1); }

  upsertWebsite(site) {
    const t = now();
    this.q.upsertSite.run(site.id, site.name, site.domain, site.startUrl, t, t);
  }

  getPage(websiteId, url) { return this.q.getPage.get(websiteId, url) ?? null; }

  /** Refuse to mix embedding models inside one website. */
  assertModelCompatible(websiteId, model, dim) {
    const rows = this.q.chunkModel.all(websiteId);
    const other = rows.find((r) => r.n > 0 && (r.model !== model || r.dim !== dim));
    if (other) throw new Error(`website ${websiteId} already holds ${other.n} chunks from ${other.model}/${other.dim}; requested ${model}/${dim}. Reindex explicitly.`);
  }

  /** Page unchanged: just record that we saw it. */
  touchPage(pageId, jobId, httpStatus) { this.q.touchPage.run(now(), jobId, httpStatus ?? null, pageId); }

  recordFailure(websiteId, url, jobId, httpStatus, error) {
    const existing = this.getPage(websiteId, url);
    const t = now();
    if (existing) this.q.failPage.run(t, jobId, httpStatus ?? null, String(error).slice(0, 500), existing.id);
    else this.q.insertPage.run(websiteId, url, null, null, null, null, null, null, "failed", httpStatus ?? null, String(error).slice(0, 500), 0, t, t, null, jobId);
  }

  /**
   * Replace a page and all its chunks atomically. `chunks` = [{index, heading, text, hash, vec:Float32Array}]
   */
  replacePage({ websiteId, page, chunks, model, dim, jobId, httpStatus }) {
    const t = now();
    this.db.exec("BEGIN"); // node:sqlite has no transaction helper; BEGIN/COMMIT by hand
    try {
      let pageId;
      const existing = this.getPage(websiteId, page.url);
      if (existing) {
        this.q.updatePage.run(page.canonical, page.title, page.description, page.pageType, page.lang, page.contentHash, "active", httpStatus ?? null, null, chunks.length, t, t, jobId, existing.id);
        pageId = existing.id;
        this.q.deleteChunks.run(pageId);
      } else {
        const r = this.q.insertPage.run(websiteId, page.url, page.canonical, page.title, page.description, page.pageType, page.lang, page.contentHash, "active", httpStatus ?? null, null, chunks.length, t, t, t, jobId);
        pageId = Number(r.lastInsertRowid);
      }
      for (const c of chunks) {
        this.q.insertChunk.run(pageId, websiteId, c.index, c.heading ?? "", c.text, c.hash, Buffer.from(c.vec.buffer, c.vec.byteOffset, c.vec.byteLength), dim, model, t);
      }
      this.db.exec("COMMIT");
      this.bump(websiteId);
      return pageId;
    } catch (err) {
      this.db.exec("ROLLBACK");
      throw err;
    }
  }

  /**
   * After a *completed* crawl, pages not seen in this job are marked inactive — unless the crawl saw
   * too few pages (guardRatio), in which case nothing is touched. Returns the number deactivated.
   */
  deactivateUnseen(websiteId, jobId, seenCount, guardRatio = 0.5) {
    const active = this.q.countActive.get(websiteId).n;
    if (active && seenCount < active * guardRatio) return { deactivated: 0, skipped: true, active, seen: seenCount };
    const rows = this.q.activeNotSeen.all(websiteId, jobId);
    this.db.exec("BEGIN");
    try {
      for (const r of rows) this.q.setInactive.run(r.id);
      this.db.exec("COMMIT");
    } catch (e) { this.db.exec("ROLLBACK"); throw e; }
    if (rows.length) this.bump(websiteId);
    return { deactivated: rows.length, skipped: false, active, seen: seenCount, urls: rows.map((r) => r.url) };
  }

  /** Full re-index: drop everything for one website only. */
  wipeWebsite(websiteId) {
    this.q.wipeSite.run(websiteId); // chunks cascade
    this.bump(websiteId);
  }

  _vectors(websiteId) {
    // PRAGMA data_version changes whenever *another connection* committed (a CLI crawl while the
    // server runs), so the cache also refreshes across processes, not only after our own writes.
    const v = `${this._version.get(websiteId) ?? 0}:${this.q.dataVersion.get()?.data_version ?? 0}`;
    const c = this._cache.get(websiteId);
    if (c && c.version === v) return c.rows;
    const rows = this.q.loadVecs.all(websiteId).map((r) => ({
      ...r,
      vec: new Float32Array(r.embedding.buffer, r.embedding.byteOffset, r.embedding.byteLength / 4),
      embedding: undefined,
    }));
    this._cache.set(websiteId, { version: v, rows });
    return rows;
  }

  // ---------------------------------------------------------------- local vectors (second model)
  /** How many active chunks of this site have a vector from `model`, and how many chunks exist. */
  localCoverage(websiteId, model) {
    return { have: this.q.countLocal.get(websiteId, model).n, want: this.q.countChunks.get(websiteId).n };
  }

  /**
   * Embed every chunk that has no vector from `model` yet, in batches, persisting as it goes (resumable;
   * a restart continues where it stopped). `embedFn(items)` -> Float32Array[]; items = [{id,text,title}].
   */
  async ensureLocalVectors(websiteId, model, dim, embedFn, { batch = 16, onProgress } = {}) {
    let done = 0;
    for (;;) {
      const rows = this.q.missingLocal.all(websiteId, model, batch);
      if (!rows.length) break;
      const vecs = await embedFn(rows);
      this.db.exec("BEGIN");
      try {
        rows.forEach((r, i) => {
          const v = vecs[i];
          this.q.insertLocal.run(r.id, model, dim, Buffer.from(v.buffer, v.byteOffset, v.byteLength));
        });
        this.db.exec("COMMIT");
      } catch (e) { this.db.exec("ROLLBACK"); throw e; }
      done += rows.length;
      this._localCache.delete(websiteId + "|" + model);
      onProgress?.(done);
    }
    return done;
  }

  _localVectors(websiteId, model) {
    const key = websiteId + "|" + model;
    const v = `${this._version.get(websiteId) ?? 0}:${this.q.dataVersion.get()?.data_version ?? 0}`;
    const c = this._localCache.get(key);
    if (c && c.version === v) return c.rows;
    const rows = this.q.loadLocal.all(websiteId, model).map((r) => ({
      ...r,
      vec: new Float32Array(r.embedding.buffer, r.embedding.byteOffset, r.embedding.byteLength / 4),
      embedding: undefined,
    }));
    this._localCache.set(key, { version: v, rows });
    return rows;
  }

  /** Cosine search over the local-model vectors; same result shape as search(). */
  searchLocal(websiteId, model, queryVec, { k = 5, pageType } = {}) {
    const rows = this._localVectors(websiteId, model);
    const types = pageType ? (Array.isArray(pageType) ? pageType : [pageType]) : null;
    const scored = [];
    for (const r of rows) {
      if (types && !types.includes(r.page_type)) continue;
      if (r.vec.length !== queryVec.length) continue;
      let s = 0;
      const a = r.vec, b = queryVec;
      for (let i = 0; i < a.length; i++) s += a[i] * b[i];
      scored.push({ score: s, row: r });
    }
    scored.sort((x, y) => y.score - x.score);
    return scored.slice(0, k).map(({ score, row }) => ({ score: Number(score.toFixed(4)), chunk_id: row.id, page_id: row.page_id, url: row.url, title: row.title, page_type: row.page_type, heading: row.heading, text: row.text, chunk_index: row.chunk_index }));
  }

  /**
   * Reciprocal-rank fusion of two ranked lists (vector + lexical) by chunk, at most 2 chunks per page.
   * The reported score is the best of the two sources' scores so min_score consumers keep working.
   */
  static fuse(vectorHits, lexicalHits, { k = 5, rrfK = 60 } = {}) {
    const acc = new Map(); // chunk_id -> {hit, rrf}
    const add = (list) => list.forEach((h, r) => {
      const e = acc.get(h.chunk_id) ?? { hit: h, rrf: 0 };
      e.rrf += 1 / (rrfK + r);
      if (h.score > e.hit.score) e.hit = { ...e.hit, score: h.score };
      acc.set(h.chunk_id, e);
    });
    add(vectorHits); add(lexicalHits);
    const perPage = new Map();
    const out = [];
    for (const { hit } of [...acc.values()].sort((a, b) => b.rrf - a.rrf)) {
      const key = hit.page_id ?? hit.url;
      const n = perPage.get(key) ?? 0;
      if (n >= 2) continue;
      perPage.set(key, n + 1);
      const { page_id, ...rest } = hit; // eslint-disable-line no-unused-vars
      out.push(rest);
      if (out.length >= k) break;
    }
    return out;
  }

  /**
   * Lexical search (FTS5 bm25) over the same chunks. Scores are mapped to 0.55..0.85 so callers that
   * threshold on cosine keep working; `mode: "lexical"` in the API response says which path answered.
   */
  searchText(websiteId, query, { k = 5, pageType } = {}) {
    if (!this.q.ftsSearch) return [];
    const terms = String(query).toLowerCase().match(/[\p{L}\p{N}]{2,}/gu) ?? [];
    if (!terms.length) return [];
    const match = [...new Set(terms)].slice(0, 12).map((t) => `"${t.replace(/"/g, "")}"`).join(" OR ");
    let rows;
    try { rows = this.q.ftsSearch.all(match, websiteId, k * 4); } catch { return []; }
    const types = pageType ? (Array.isArray(pageType) ? pageType : [pageType]) : null;
    const top = rows[0]?.rank ?? -1;
    const perPage = new Map();
    const out = [];
    for (const r of rows) {
      if (types && !types.includes(r.page_type)) continue;
      const n = perPage.get(r.page_id) ?? 0;
      if (n >= 2) continue;
      perPage.set(r.page_id, n + 1);
      const ratio = top ? Math.max(0, Math.min(1, r.rank / top)) : 1; // bm25 is negative, lower = better
      out.push({ score: Number((0.55 + 0.3 * ratio).toFixed(4)), chunk_id: r.id, page_id: r.page_id, url: r.url, title: r.title, page_type: r.page_type, heading: r.heading, text: r.text, chunk_index: r.chunk_index });
      if (out.length >= k) break;
    }
    return out;
  }

  /**
   * Cosine search within one website. Vectors are stored normalised, so score = dot product.
   * filters: {pageType?: string|string[], minScore?: number}
   */
  search(websiteId, queryVec, { k = 5, pageType, minScore = 0 } = {}) {
    const rows = this._vectors(websiteId);
    const types = pageType ? (Array.isArray(pageType) ? pageType : [pageType]) : null;
    const scored = [];
    for (const r of rows) {
      if (types && !types.includes(r.page_type)) continue;
      if (r.vec.length !== queryVec.length) continue;
      let s = 0;
      const a = r.vec, b = queryVec;
      for (let i = 0; i < a.length; i++) s += a[i] * b[i];
      if (s >= minScore) scored.push({ score: s, row: r });
    }
    scored.sort((x, y) => y.score - x.score);
    // at most 2 chunks per page so one long page cannot crowd the context
    const perPage = new Map();
    const out = [];
    for (const { score, row } of scored) {
      const n = perPage.get(row.page_id) ?? 0;
      if (n >= 2) continue;
      perPage.set(row.page_id, n + 1);
      out.push({ score: Number(score.toFixed(4)), chunk_id: row.id, url: row.url, title: row.title, page_type: row.page_type, heading: row.heading, text: row.text, chunk_index: row.chunk_index });
      if (out.length >= k) break;
    }
    return out;
  }

  listPages(websiteId, { status = "", limit = 100, offset = 0 } = {}) {
    return this.q.listPages.all(websiteId, status, status, limit, offset);
  }

  stats(websiteId) {
    const pages = Object.fromEntries(this.q.countPages.all(websiteId).map((r) => [r.status, r.n]));
    const chunks = this.q.countChunks.get(websiteId).n;
    const models = this.q.chunkModel.all(websiteId);
    return { website_id: websiteId, pages, chunks, models };
  }

  startJob(websiteId, mode) {
    const r = this.q.insertJob.run(websiteId, mode, "running", now());
    return Number(r.lastInsertRowid);
  }
  finishJob(jobId, status, stats, error = null) {
    this.q.finishJob.run(status, now(), JSON.stringify(stats ?? {}), error ? String(error).slice(0, 500) : null, jobId);
  }
  jobs(websiteId = "", limit = 10) {
    return this.q.lastJobs.all(websiteId, websiteId, limit).map((j) => ({ ...j, stats: j.stats ? JSON.parse(j.stats) : null }));
  }
}
