// Local query/passage embeddings on the CPU (no API, no quota): Xenova/multilingual-e5-small, int8 ONNX
// through @huggingface/transformers. Measured on the laptop (2026-11-02): 9-11 ms per query, 131 s to
// embed the 919 ACR chunks once, ~19 s model load, model files cached under data/models.
//
// Quality note: on its own e5-small is weaker than Gemini on Hinglish phrasing; the search route fuses it
// with the FTS5 lexical index and expands Hinglish automotive words first (search/hinglish.js), which
// brought it level with Gemini on 9/12 test questions. Gemini stays the quality path and the fallback.
import { resolve } from "node:path";
import { DATA_DIR, settings } from "../config.js";
import { log } from "../log.js";

export const LOCAL_MODEL = settings.embedding.localModel;
export const LOCAL_DIM = 384;

let pipe = null;          // loaded pipeline
let loading = null;       // promise while loading
let failure = "";

export function localStatus() {
  return { model: LOCAL_MODEL, dim: LOCAL_DIM, ready: Boolean(pipe), loading: Boolean(loading) && !pipe, error: failure || undefined };
}

/** Load the model once (background at startup). Resolves to true when usable. */
export async function loadLocal() {
  if (pipe) return true;
  if (loading) return loading;
  loading = (async () => {
    const t0 = Date.now();
    try {
      const tf = await import("@huggingface/transformers");
      tf.env.cacheDir = resolve(DATA_DIR, "models");
      tf.env.allowRemoteModels = true;
      pipe = await tf.pipeline("feature-extraction", LOCAL_MODEL, { dtype: "q8" });
      await pipe(["query: warm up"], { pooling: "mean", normalize: true });
      log.info("local embedding model ready", { model: LOCAL_MODEL, ms: Date.now() - t0 });
      return true;
    } catch (err) {
      failure = String(err.message).slice(0, 200);
      log.warn("local embedding model unavailable", { model: LOCAL_MODEL, err: failure });
      return false;
    } finally {
      loading = null;
    }
  })();
  return loading;
}

async function run(texts) {
  if (!pipe) throw new Error("local embedding model not loaded");
  const out = await pipe(texts, { pooling: "mean", normalize: true });
  const [n, d] = out.dims;
  const rows = new Array(n);
  for (let i = 0; i < n; i++) rows[i] = Float32Array.from(out.data.slice(i * d, (i + 1) * d));
  return rows;
}

/** e5 convention: "query: " for questions, "passage: " (with the page title) for stored chunks. */
export async function embedQueryLocal(text) {
  const [v] = await run(["query: " + text]);
  return v;
}

export async function embedTextsLocal(items, { batch = 16 } = {}) {
  const out = [];
  for (let i = 0; i < items.length; i += batch) {
    const slice = items.slice(i, i + batch).map((it) => "passage: " + (it.title ? it.title + " / " : "") + String(it.text).slice(0, 1500));
    out.push(...await run(slice));
  }
  return out;
}
