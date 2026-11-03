// Gemini embeddings (gemini-embedding-001). Batched, retried, dimension-checked, L2-normalised so
// cosine similarity is a plain dot product in the store. Swap this module to change provider; the
// store records model+dim on every chunk and refuses to mix them.
import { settings } from "../config.js";
import { log } from "../log.js";

const BASE = "https://generativelanguage.googleapis.com/v1beta/models";
const MAX_PER_REQUEST = 100;

export class EmbeddingError extends Error {}

function l2norm(v) {
  let s = 0;
  for (const x of v) s += x * x;
  const n = Math.sqrt(s) || 1;
  return Float32Array.from(v, (x) => x / n);
}

/** Gemini puts the wait it wants in the body ("retryDelay": "37s"); honour it, else back off. */
function retryDelayMs(res, text, fallback) {
  const header = Number(res.headers.get("retry-after")) * 1000;
  if (header > 0) return header;
  const m = text.match(/"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"/);
  if (m) return Math.ceil(Number(m[1]) * 1000) + 500;
  return fallback;
}

// Keep the TLS connection to Google open between customer turns (Node's default pool drops idle
// sockets after ~4 s, so most queries paid a fresh TCP+TLS handshake). undici ships inside Node but is
// only importable when installed as a package; skip silently when it is not.
try {
  const { Agent, setGlobalDispatcher } = await import("undici");
  setGlobalDispatcher(new Agent({ keepAliveTimeout: 60_000, keepAliveMaxTimeout: 120_000, connections: 8 }));
} catch { /* undici not installed as a package: default dispatcher */ }

async function call(url, body, { retries = settings.embedding.maxRetries, timeoutMs = 60000 } = {}) {
  let delay = 2000;
  for (let attempt = 0; ; attempt++) {
    let res;
    try {
      res = await fetch(url, {
        method: "POST",
        headers: { "content-type": "application/json", "x-goog-api-key": settings.embedding.apiKey },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(timeoutMs),
      });
    } catch (err) {
      if (attempt >= retries) throw new EmbeddingError(`embedding request failed: ${err.message}`);
      log.warn("embedding network error, retrying", { attempt, err });
      await sleep(delay); delay = Math.min(delay * 2, 60000); continue;
    }
    if (res.ok) return res.json();
    const text = await res.text(); // full body: quotaId / retryDelay live in the details array
    if (res.status === 429 && /PerDay/i.test(text)) {
      // daily free-tier allowance is gone until the reset; waiting minutes will not help
      throw new EmbeddingError(`embedding daily quota exhausted (${text.match(/"quotaId"\s*:\s*"([^"]+)"/)?.[1] ?? "per-day"}); retry after the daily reset or use a paid key`);
    }
    if ((res.status === 429 || res.status >= 500) && attempt < retries) {
      const quota = res.status === 429 && /quota/i.test(text);
      // a quota 429 needs at least the window Gemini names (often 30-60 s); a plain 429 just backs off
      const wait = Math.max(retryDelayMs(res, text, delay), quota ? 15000 : 0);
      log.warn("embedding API throttled, retrying", { status: res.status, attempt, waitMs: wait, quota });
      await sleep(wait); delay = Math.min(delay * 2, 60000); continue;
    }
    throw new EmbeddingError(`embedding API ${res.status}: ${text.slice(0, 300)}`);
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export function embeddingInfo() {
  return { provider: "gemini", model: settings.embedding.model, dim: settings.embedding.dim };
}

export function assertConfigured() {
  if (!settings.embedding.apiKey) throw new EmbeddingError("GEMINI_API_KEY / gemini_api_key is not set");
}

/**
 * Embed documents. `items` = [{text, title?}]. Returns Float32Array[] in the same order.
 * taskType RETRIEVAL_DOCUMENT for stored chunks, RETRIEVAL_QUERY for user questions.
 */
export async function embedTexts(items, { taskType = "RETRIEVAL_DOCUMENT", retries, timeoutMs } = {}) {
  assertConfigured();
  const { model, dim, batchSize } = settings.embedding;
  const out = new Array(items.length);
  const step = Math.min(batchSize, MAX_PER_REQUEST);
  const callOpts = { ...(retries !== undefined ? { retries } : {}), ...(timeoutMs !== undefined ? { timeoutMs } : {}) };
  for (let i = 0; i < items.length; i += step) {
    const slice = items.slice(i, i + step);
    const body = {
      requests: slice.map((it) => ({
        model: `models/${model}`,
        content: { parts: [{ text: it.text }] },
        taskType,
        outputDimensionality: dim,
        ...(it.title && taskType === "RETRIEVAL_DOCUMENT" ? { title: it.title.slice(0, 200) } : {}),
      })),
    };
    const t0 = Date.now();
    const data = await call(`${BASE}/${model}:batchEmbedContents`, body, callOpts);
    const vecs = data?.embeddings ?? [];
    if (vecs.length !== slice.length) throw new EmbeddingError(`expected ${slice.length} embeddings, got ${vecs.length}`);
    vecs.forEach((e, j) => {
      const v = e.values ?? [];
      if (v.length !== dim) throw new EmbeddingError(`embedding dimension ${v.length} != configured ${dim}`);
      out[i + j] = l2norm(v);
    });
    log.debug("embedded batch", { n: slice.length, ms: Date.now() - t0 });
  }
  return out;
}

// Query vectors are cached: repeated customer questions cost no quota and answer in ~0 ms.
const QUERY_CACHE = new Map();
const QUERY_CACHE_MAX = 2000;
const INFLIGHT = new Map(); // key -> promise: identical concurrent questions share one API call

// Circuit breaker for *queries* only. Indexing keeps its long retries; a customer turn cannot wait
// minutes, so a query gets one attempt inside the deadline, and after 3 straight failures every query
// fails fast for 30 s (the caller then answers from the lexical index).
const BREAKER = { failures: 0, openUntil: 0, lastError: "" };
const BREAKER_TRIP = 3;
const BREAKER_OPEN_MS = 30_000;

export function queryEmbeddingStatus() {
  const open = Date.now() < BREAKER.openUntil;
  return { ok: !open && Boolean(settings.embedding.apiKey), circuit: open ? "open" : "closed", failures: BREAKER.failures, last_error: open ? BREAKER.lastError : "", cache_size: QUERY_CACHE.size };
}

export async function embedQuery(text, { timeoutMs = settings.search.embedTimeoutMs } = {}) {
  const key = text.trim().toLowerCase().replace(/\s+/g, " ");
  const hit = QUERY_CACHE.get(key);
  if (hit) { QUERY_CACHE.delete(key); QUERY_CACHE.set(key, hit); return hit; } // LRU touch
  if (Date.now() < BREAKER.openUntil) throw new EmbeddingError(`embedding circuit open: ${BREAKER.lastError}`);
  const running = INFLIGHT.get(key);
  if (running) return running;
  const p = (async () => {
    try {
      const [v] = await embedTexts([{ text }], { taskType: "RETRIEVAL_QUERY", retries: 0, timeoutMs });
      BREAKER.failures = 0;
      if (QUERY_CACHE.size >= QUERY_CACHE_MAX) QUERY_CACHE.delete(QUERY_CACHE.keys().next().value);
      QUERY_CACHE.set(key, v);
      return v;
    } catch (err) {
      BREAKER.lastError = String(err.message).slice(0, 160);
      if (++BREAKER.failures >= BREAKER_TRIP) {
        BREAKER.openUntil = Date.now() + BREAKER_OPEN_MS;
        log.warn("query embedding circuit opened", { failures: BREAKER.failures, forMs: BREAKER_OPEN_MS, err: BREAKER.lastError });
      }
      throw err;
    } finally {
      INFLIGHT.delete(key);
    }
  })();
  INFLIGHT.set(key, p);
  return p;
}
