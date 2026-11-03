// Customer-call Q&A bundle: the qa pages with their Gemini document vectors, carried to a host that has no
// disk and must not read them from the public repo. Render mounts it as a secret file
// (/etc/secrets/qa_pages.jsonl.gz.b64); src/seed.js imports it at boot. Format: base64(gzip(jsonl)), one
// line per page {url,title,description,lang,page_hash,heading,text,chunk_hash,model,dim,vec16} where
// vec16 is the float16 vector as base64. Idempotent: an unchanged page (same page_hash) is skipped.
//
//   node src/qa_bundle.js export > qa_pages.jsonl.gz.b64      (from the local database)
//   node src/qa_bundle.js import <file> [--website auto_car_repair]
import { existsSync, readFileSync } from "node:fs";
import { gunzipSync, gzipSync } from "node:zlib";
import { settings, websites } from "./config.js";
import { log } from "./log.js";
import { KnowledgeStore } from "./store/sqlite.js";

export const DEFAULT_PATHS = [process.env.KB_QA_BUNDLE, "/etc/secrets/qa_pages.jsonl.gz.b64", "qa_pages.jsonl.gz.b64"].filter(Boolean);

/** IEEE 754 half -> float. (Float16Array is not in every Node release this runs on.) */
function f16to32(h) {
  const s = (h & 0x8000) ? -1 : 1, e = (h >> 10) & 0x1f, f = h & 0x3ff;
  if (e === 0) return s * f * 2 ** -24;
  if (e === 31) return f ? NaN : s * Infinity;
  return s * (1 + f / 1024) * 2 ** (e - 15);
}
function decodeVec16(b64, dim) {
  const buf = Buffer.from(b64, "base64");
  const out = new Float32Array(dim);
  for (let i = 0; i < dim; i++) out[i] = f16to32(buf.readUInt16LE(i * 2));
  // renormalise: the float16 rounding moved the unit vector a hair
  let n = 0; for (let i = 0; i < dim; i++) n += out[i] * out[i];
  n = Math.sqrt(n) || 1; for (let i = 0; i < dim; i++) out[i] /= n;
  return out;
}

export function readBundle(path) {
  const b64 = readFileSync(path, "utf8").replace(/\s+/g, "");
  const jsonl = gunzipSync(Buffer.from(b64, "base64")).toString("utf8");
  return jsonl.split("\n").filter(Boolean).map((l) => JSON.parse(l));
}

/** @returns {{imported:number, unchanged:number, total:number}} */
export function importBundle(store, siteId, rows) {
  const site = websites[siteId];
  if (!site) throw new Error(`unknown website ${siteId}`);
  store.upsertWebsite(site);
  let imported = 0, unchanged = 0;
  for (const r of rows) {
    const ex = store.getPage(siteId, r.url);
    if (ex && ex.status === "active" && ex.content_hash === r.page_hash) { unchanged++; continue; }
    store.assertModelCompatible(siteId, r.model, r.dim);
    const page = { url: r.url, canonical: r.url, title: r.title, description: r.description, pageType: "qa", lang: r.lang, contentHash: r.page_hash };
    const chunk = { index: 0, heading: r.heading, text: r.text, hash: r.chunk_hash, vec: decodeVec16(r.vec16, r.dim) };
    const pageId = store.replacePage({ websiteId: siteId, page, chunks: [chunk], model: r.model, dim: r.dim, jobId: null, httpStatus: 200 });
    if (r.local16 && r.local_model) {  // local CPU-model vector too, so a host never embeds at boot
      const cid = store.db.prepare("SELECT id FROM chunks WHERE page_id = ? AND chunk_index = 0").get(pageId)?.id;
      const lv = decodeVec16(r.local16, r.local_dim);
      if (cid) store.q.insertLocal.run(cid, r.local_model, r.local_dim, Buffer.from(lv.buffer, lv.byteOffset, lv.byteLength));
    }
    imported++;
  }
  if (imported) store.checkpoint();
  return { imported, unchanged, total: rows.length };
}

/** Boot hook (src/seed.js): import the first bundle file that exists; never throws. */
export function importBundleIfPresent(siteId = "auto_car_repair") {
  const path = DEFAULT_PATHS.find((p) => existsSync(p));
  if (!path) return null;
  const store = new KnowledgeStore(settings.dbPath);
  try {
    const t0 = Date.now();
    const res = importBundle(store, siteId, readBundle(path));
    log.info("qa bundle imported", { path, ...res, ms: Date.now() - t0 });
    return res;
  } catch (err) {
    log.error("qa bundle import failed (search continues without it)", { path, err: String(err.message).slice(0, 200) });
    return null;
  } finally {
    store.close();
  }
}

function exportBundle(siteId) {
  const store = new KnowledgeStore(settings.dbPath);
  try {
    const rows = store.db.prepare(`SELECT p.url, p.title, p.description, p.lang, p.content_hash AS page_hash, c.heading, c.text,
        c.content_hash AS chunk_hash, c.embedding, c.dim, c.model, v.embedding AS local_embedding, v.dim AS local_dim, v.model AS local_model
        FROM pages p JOIN chunks c ON c.page_id = p.id LEFT JOIN chunk_vectors v ON v.chunk_id = c.id
        WHERE p.website_id = ? AND p.page_type = 'qa' AND p.status = 'active' ORDER BY p.id`).all(siteId);
    const half = (buf, dim) => { const f32 = new Float32Array(buf.buffer, buf.byteOffset, dim); const h = Buffer.alloc(dim * 2); for (let i = 0; i < dim; i++) h.writeUInt16LE(f32to16(f32[i]), i * 2); return h.toString("base64"); };
    const lines = rows.map((r) => {
      const { embedding, local_embedding, ...rest } = r; // eslint-disable-line no-unused-vars
      const extra = local_embedding ? { local16: half(local_embedding, r.local_dim) } : { local_dim: undefined, local_model: undefined };
      return JSON.stringify({ ...rest, vec16: half(embedding, r.dim), ...extra });
    });
    process.stdout.write(gzipSync(Buffer.from(lines.join("\n"), "utf8"), { level: 9 }).toString("base64"));
    process.stderr.write(`exported ${rows.length} qa pages\n`);
  } finally {
    store.close();
  }
}
function f32to16(v) {
  if (Number.isNaN(v)) return 0x7e00;
  const s = v < 0 ? 0x8000 : 0; v = Math.abs(v);
  if (v === 0) return s;
  if (v >= 65504) return s | 0x7bff;
  let e = Math.floor(Math.log2(v)); let m = v / 2 ** e - 1;
  if (e < -14) { m = v / 2 ** -14; return s | Math.round(m * 1024); }
  return s | ((e + 15) << 10) | Math.round(m * 1024);
}

const isMain = process.argv[1] && /qa_bundle\.js$/.test(process.argv[1]);
if (isMain) {
  const [cmd, file] = process.argv.slice(2);
  const site = process.argv.includes("--website") ? process.argv[process.argv.indexOf("--website") + 1] : "auto_car_repair";
  if (cmd === "export") exportBundle(site);
  else if (cmd === "import" && file) {
    const store = new KnowledgeStore(settings.dbPath);
    try { console.log(importBundle(store, site, readBundle(file))); } finally { store.close(); }
  } else { console.log("usage: node src/qa_bundle.js export | import <file> [--website id]"); process.exit(2); }
}
