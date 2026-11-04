// Customer-call Q&A -> knowledge pages. Reads kb/seed/acr_customer_qa.clean.csv (tools/clean_qa.py) and
// the voice rewrites in kb/seed/qa_rewrites.json (tools/rewrite_qa.py), writes one page + one chunk per
// question into the same store the crawl uses, embeds with the Gemini document model (the website's
// model; the store refuses to mix) and with the local CPU model for the low-latency path.
//
// Re-runnable: a page whose content hash is unchanged is left alone; a changed one is replaced. The crawl
// never touches these pages (page_type "qa" is excluded from deactivateUnseen), but a full --reindex
// wipes the website, so run this again after one.
//
//   node src/ingest_qa.js [--website auto_car_repair] [--csv path] [--dry]
import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { ROOT_DIR, settings, websites } from "./config.js";
import { embedTexts, embeddingInfo } from "./embed/gemini.js";
import { LOCAL_DIM, LOCAL_MODEL, embedTextsLocal, loadLocal } from "./embed/local.js";
import { log } from "./log.js";
import { KnowledgeStore } from "./store/sqlite.js";

const args = process.argv.slice(2);
const opt = (k, d) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : d; };
const DRY = args.includes("--dry");
const SITE_ID = opt("--website", "auto_car_repair");
const CSV = resolve(opt("--csv", resolve(ROOT_DIR, "seed", "acr_customer_qa.clean.csv")));
const REWRITES = resolve(ROOT_DIR, "seed", "qa_rewrites.json");

/** RFC 4180 parser (quoted fields, doubled quotes, newlines inside quotes). */
function parseCsv(text) {
  const rows = []; let row = [], field = "", q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"') { if (text[i + 1] === '"') { field += '"'; i++; } else q = false; }
      else field += c;
    } else if (c === '"') q = true;
    else if (c === ",") { row.push(field); field = ""; }
    else if (c === "\n" || c === "\r") { if (c === "\r" && text[i + 1] === "\n") i++; row.push(field); rows.push(row); row = []; field = ""; }
    else field += c;
  }
  if (field.length || row.length) { row.push(field); rows.push(row); }
  const [head, ...body] = rows.filter((r) => r.length > 1 || (r[0] ?? "").trim());
  return body.map((r) => Object.fromEntries(head.map((h, i) => [h, r[i] ?? ""])));
}

const sha = (s) => createHash("sha256").update(s).digest("hex").slice(0, 32);

function buildPage(r, rewrites) {
  const rw = rewrites[r.id]?.text?.trim();
  const answer = rw || r.answer;
  const lines = [answer];
  if (rw) lines.push(`Call note: ${r.answer}`);                 // the facts the line was written from, for the model to check
  if (r.answer_extra) lines.push(`Also noted: ${r.answer_extra}`);
  if (r.answer_en) lines.push(`English: ${r.answer_en}`);
  if (r.has_price === "1") lines.push("(Prices quoted on customer calls, approximate; the exact amount is confirmed after the car is seen.)");
  const text = lines.join("\n");
  const aliases = r.aliases ? r.aliases.split(" || ").map((s) => s.trim()).filter(Boolean) : [];
  const heading = [r.question, r.question_en, ...aliases].filter(Boolean).join(" | ");
  const site = websites[SITE_ID];
  const url = `https://${site?.domain ?? "autocarrepair.in"}/faq/customer-calls#${r.id}`;
  const page = {
    url, canonical: url, title: r.question, description: r.question_en || r.question, pageType: "qa", lang: "hi-Latn",
    contentHash: sha(heading + "\n" + text),
  };
  return { page, chunk: { index: 0, heading, text, hash: sha(text) }, seen: Number(r.times_seen) || 1, category: r.category };
}

async function main() {
  const site = websites[SITE_ID];
  if (!site) throw new Error(`unknown website ${SITE_ID}; registered: ${Object.keys(websites).join(", ")}`);
  if (!existsSync(CSV)) throw new Error(`missing ${CSV} (run python kb/tools/clean_qa.py)`);
  const rows = parseCsv(readFileSync(CSV, "utf8").replace(/^﻿/, ""));
  const rewrites = existsSync(REWRITES) ? JSON.parse(readFileSync(REWRITES, "utf8")) : {};
  const items = rows.map((r) => buildPage(r, rewrites));
  const voiced = items.filter((_, i) => rewrites[rows[i].id]?.text).length;
  log.info("qa rows", { rows: rows.length, voiced, csv: CSV });
  if (DRY) { for (const it of items.slice(0, 3)) console.log(it.page.url, "\n", it.chunk.heading, "\n", it.chunk.text, "\n"); return; }

  const store = new KnowledgeStore(settings.dbPath);
  try {
    store.upsertWebsite(site);
    const { model, dim } = embeddingInfo();
    store.assertModelCompatible(site.id, model, dim);
    const todo = items.filter((it) => { const ex = store.getPage(site.id, it.page.url); return !(ex && ex.status === "active" && ex.content_hash === it.page.contentHash); });
    log.info("qa pages to (re)index", { changed: todo.length, unchanged: items.length - todo.length });
    const BATCH = 16;
    let written = 0;
    for (let i = 0; i < todo.length; i += BATCH) {
      const slice = todo.slice(i, i + BATCH);
      // the embedding sees the question(s) as title and the answer as text, like a crawled page does
      const vecs = await embedTexts(slice.map((it) => ({ text: it.chunk.text, title: it.chunk.heading.slice(0, 200) })));
      slice.forEach((it, j) => {
        it.chunk.vec = vecs[j];
        store.replacePage({ websiteId: site.id, page: it.page, chunks: [it.chunk], model, dim, jobId: null, httpStatus: 200 });
        written++;
      });
      log.info("qa indexed", { done: written, of: todo.length });
    }
    // rows that left the sheet (merged, dropped, re-keyed) must leave the index too: delete qa pages not in it
    const keep = new Set(items.map((it) => it.page.url));
    const stale = store.db.prepare("SELECT id, url FROM pages WHERE website_id = ? AND page_type = 'qa'").all(site.id).filter((p) => !keep.has(p.url));
    if (stale.length) {
      const del = store.db.prepare("DELETE FROM pages WHERE id = ?");   // chunks and vectors cascade (foreign_keys=ON)
      store.db.exec("BEGIN"); try { for (const p of stale) del.run(p.id); store.db.exec("COMMIT"); } catch (e) { store.db.exec("ROLLBACK"); throw e; }
      store.bump(site.id);
      log.info("stale qa pages removed", { removed: stale.length });
    }
    // local vectors for the hybrid-local path (same convention as server startup; resumable)
    if (LOCAL_MODEL && (await loadLocal())) {
      const n = await store.ensureLocalVectors(site.id, LOCAL_MODEL, LOCAL_DIM, (rs) => embedTextsLocal(rs));
      log.info("local vectors", { embedded: n, coverage: store.localCoverage(site.id, LOCAL_MODEL) });
    }
    store.checkpoint();
    const qa = store.db.prepare("SELECT COUNT(*) n FROM pages WHERE website_id=? AND page_type='qa' AND status='active'").get(site.id).n;
    log.info("qa ingest done", { qa_pages: qa, stats: store.stats(site.id) });
  } finally {
    store.close();
  }
}

main().catch((err) => { log.error("qa ingest failed", { err }); process.exit(1); });
