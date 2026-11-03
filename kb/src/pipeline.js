// One rendered page in -> chunks + embeddings in the store, or a cheap "unchanged" touch.
import { settings } from "./config.js";
import { chunkMarkdown } from "./chunk/chunk.js";
import { embedTexts, embeddingInfo } from "./embed/gemini.js";
import { extractPage } from "./extract/extract.js";
import { log } from "./log.js";

const ERROR_VIEW_RE = /(something (broke|went wrong)|unexpected error|error boundary|unable to preload css|page not found|404\b[^\n]{0,40}not found|reload (the )?page to recover|chunkloaderror)/i;

/** Short pages dominated by a client-side error / not-found view are not knowledge. */
export function looksLikeErrorPage(page) {
  return page.text.length < 1500 && ERROR_VIEW_RE.test(page.text);
}

/**
 * @returns {{outcome:'indexed'|'unchanged'|'thin'|'failed', chunks:number, url:string}}
 */
export async function processPage({ store, site, url, html, httpStatus, jobId }) {
  const t0 = Date.now();
  const page = extractPage(html, url);
  const finalUrl = page.url ?? url;

  if (!page.text || page.text.length < settings.chunk.minPageChars) {
    store.recordFailure(site.id, finalUrl, jobId, httpStatus, "thin: no meaningful content after extraction");
    log.info("page skipped (thin)", { website: site.id, url: finalUrl, chars: page.text?.length ?? 0 });
    return { outcome: "thin", chunks: 0, url: finalUrl };
  }
  if (looksLikeErrorPage(page)) {
    // existing chunks (if any) are kept; the page is only flagged
    store.recordFailure(site.id, finalUrl, jobId, httpStatus, "spa-error: page rendered an error boundary / not-found view");
    log.warn("page skipped (error view)", { website: site.id, url: finalUrl, chars: page.text.length });
    return { outcome: "thin", chunks: 0, url: finalUrl };
  }

  const existing = store.getPage(site.id, finalUrl);
  if (existing && existing.status === "active" && existing.content_hash === page.contentHash) {
    store.touchPage(existing.id, jobId, httpStatus);
    log.debug("page unchanged", { website: site.id, url: finalUrl });
    return { outcome: "unchanged", chunks: existing.chunk_count, url: finalUrl };
  }

  const { model, dim } = embeddingInfo();
  store.assertModelCompatible(site.id, model, dim);

  const chunks = chunkMarkdown(page.markdown, { size: settings.chunk.size, overlap: settings.chunk.overlap, title: page.title });
  if (!chunks.length) {
    store.recordFailure(site.id, finalUrl, jobId, httpStatus, "thin: produced no chunks");
    return { outcome: "thin", chunks: 0, url: finalUrl };
  }

  // the embedding sees "title / heading" context so short chunks are not orphaned
  const vecs = await embedTexts(chunks.map((c) => ({ text: c.text, title: page.title })));
  chunks.forEach((c, i) => { c.vec = vecs[i]; });

  store.replacePage({ websiteId: site.id, page, chunks, model, dim, jobId, httpStatus });
  log.info(existing ? "page updated" : "page indexed", {
    website: site.id, url: finalUrl, type: page.pageType, chunks: chunks.length, ms: Date.now() - t0,
  });
  return { outcome: "indexed", chunks: chunks.length, url: finalUrl, changed: Boolean(existing) };
}
