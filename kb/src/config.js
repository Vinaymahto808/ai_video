// Central config: env + the website registry. Secrets come from kb/.env, falling back to
// ../voice_clone/.env (the keys already on this machine). Nothing here is logged.
import { config as loadEnv } from "dotenv";
import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
// Installed as a dependency (…/node_modules/web-avatar-crawler) the package must not write inside
// itself: data and .env then live in the consuming project's working directory.
const INSTALLED = /[\\/]node_modules[\\/]/.test(ROOT);
const BASE = INSTALLED ? process.cwd() : ROOT;
for (const f of [resolve(process.cwd(), ".env"), resolve(ROOT, ".env"), resolve(ROOT, "../voice_clone/.env")]) {
  if (existsSync(f)) loadEnv({ path: f, override: false });
}

const env = (k, d) => (process.env[k] ?? d);
const int = (k, d) => Number.parseInt(env(k, d), 10);

export const ROOT_DIR = ROOT;
export const DATA_DIR = resolve(BASE, env("KB_DATA_DIR", "data"));

export const settings = {
  port: int("KB_PORT", 4100),
  host: env("KB_HOST", "127.0.0.1"),
  adminKey: env("KB_ADMIN_KEY", ""), // empty => management endpoints accept localhost only
  dbPath: resolve(DATA_DIR, env("KB_DB_FILE", "kb.sqlite")),
  crawleeDir: resolve(DATA_DIR, "crawlee"),
  userAgent: env("KB_USER_AGENT", "Mozilla/5.0 (compatible; TSG-KB-Crawler/0.1; +https://thesachdevgroup.com)"),
  embedding: {
    provider: env("KB_EMBED_PROVIDER", "gemini"),
    model: env("KB_EMBED_MODEL", "gemini-embedding-001"),
    dim: int("KB_EMBED_DIM", 768),
    batchSize: int("KB_EMBED_BATCH", 32),
    maxRetries: int("KB_EMBED_RETRIES", 6),
    apiKey: env("GEMINI_API_KEY", env("gemini_api_key", "")),
    // local CPU model for the low-latency query path (see embed/local.js); "" disables it
    localModel: env("KB_LOCAL_MODEL", "Xenova/multilingual-e5-small"),
  },
  chunk: {
    size: int("KB_CHUNK_SIZE", 1200),
    overlap: int("KB_CHUNK_OVERLAP", 200),
    minPageChars: int("KB_MIN_PAGE_CHARS", 120),
  },
  search: {
    defaultK: int("KB_SEARCH_K", 5),
    minScore: Number.parseFloat(env("KB_SEARCH_MIN_SCORE", "0.55")),
    // a query embedding slower than this falls back to the lexical (FTS5) index so the bot never waits
    embedTimeoutMs: int("KB_QUERY_TIMEOUT_MS", 2000),
    // "local": hybrid local-vector + lexical (~20-100 ms) when the local model and vectors are ready, else gemini;
    // "gemini": always the API vector path (0.5-1.2 s) with lexical fallback
    queryProvider: env("KB_QUERY_PROVIDER", "local"),
  },
  // "loopback" / "1" when a reverse proxy sits in front (so req.ip is the client, not the proxy)
  trustProxy: env("KB_TRUST_PROXY", ""),
  crawl: {
    maxPages: int("KB_MAX_PAGES", 1000),
    maxConcurrency: int("KB_MAX_CONCURRENCY", 2),
    navigationTimeoutSecs: int("KB_NAV_TIMEOUT", 45),
    requestTimeoutSecs: int("KB_REQ_TIMEOUT", 90),
    maxRetries: int("KB_MAX_RETRIES", 2),
    // if a refresh sees fewer than this share of previously-active pages, nothing is deactivated
    deactivateGuardRatio: Number.parseFloat(env("KB_DEACTIVATE_GUARD", "0.5")),
  },
};

// One entry per brand. Knowledge is isolated by website.id everywhere (pages, chunks, search).
const COMMON_DISALLOW = [
  /\/(cart|checkout|pay|payment|login|logout|register|signup|sign-in|account|profile|my-|booking-history|admin|wp-admin|wp-login|wp-json|livewire|sanctum|xmlrpc)\b/i,
  /\/(feed|rss|comments\/feed)\/?$/i,
  /\.(jpe?g|png|gif|webp|svg|ico|pdf|zip|rar|mp4|mp3|avi|mov|woff2?|ttf|css|js|json|xml)$/i,
  /[?&](s|search|q|replytocom|share|print)=/i,
  /\/(tag|author|page)\/\d*\/?$/i,
];

export const websites = {
  auto_car_repair: {
    id: "auto_car_repair",
    name: "Auto Car Repair",
    domain: "autocarrepair.in",
    startUrl: "https://autocarrepair.in/",
    allowedHosts: ["autocarrepair.in", "www.autocarrepair.in"],
    disallow: [...COMMON_DISALLOW, /\/(explore|become-an-agent|not-found|coupons)\b/i],
    renderJavaScript: true, // React SPA: the server returns an empty #root shell
    maxPages: 600,
    maxConcurrency: 2,
    language: "en",
  },
  galaxy_toyota: {
    id: "galaxy_toyota",
    name: "Galaxy Toyota",
    domain: "galaxytoyota.in",
    startUrl: "https://galaxytoyota.in/",
    allowedHosts: ["galaxytoyota.in", "www.galaxytoyota.in"],
    disallow: COMMON_DISALLOW,
    renderJavaScript: true,
    maxPages: 1500,
    maxConcurrency: 2,
    language: "en",
  },
  hans_hyundai: {
    id: "hans_hyundai",
    name: "Hans Hyundai",
    domain: "hanshyundai.com",
    startUrl: "https://hanshyundai.com/",
    allowedHosts: ["hanshyundai.com", "www.hanshyundai.com"],
    disallow: COMMON_DISALLOW,
    renderJavaScript: true,
    maxPages: 1500,
    maxConcurrency: 2,
    language: "en",
  },
};

/**
 * Build a website entry for any public site from its URL. The id is a slug of the bare host
 * (`https://www.example.co.in/` → `example_co_in`); www/non-www are both allowed.
 */
export function defineWebsite(url, opts = {}) {
  const u = new URL(url);
  if (!/^https?:$/.test(u.protocol)) throw new Error(`unsupported URL scheme: ${u.protocol}`);
  const bare = u.hostname.toLowerCase().replace(/^www\./, "");
  const id = opts.id ?? bare.replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  return {
    id,
    name: opts.name ?? bare,
    domain: bare,
    startUrl: `${u.protocol}//${u.hostname}${u.pathname === "/" ? "/" : u.pathname}`,
    allowedHosts: [bare, `www.${bare}`],
    disallow: [...COMMON_DISALLOW, ...(opts.disallow ?? [])],
    renderJavaScript: opts.renderJavaScript ?? true,
    maxPages: opts.maxPages ?? settings.crawl.maxPages,
    maxConcurrency: opts.maxConcurrency ?? settings.crawl.maxConcurrency,
    language: opts.language ?? "en",
  };
}

/** Add (or replace) a website in the registry; returns the stored entry. */
export function registerWebsite(site) {
  if (!site?.id || !site.startUrl || !Array.isArray(site.allowedHosts)) throw new Error("website needs id, startUrl, allowedHosts");
  websites[site.id] = site;
  return site;
}

/**
 * Resolve a website by id, or by any URL on a registered host. From the CLI (`register: true`) an
 * http(s) URL on an unknown host is registered on the fly, so `crawl https://any-site.com` just works.
 * The HTTP API passes `register: false`: an unauthenticated caller must not be able to add sites
 * (unbounded registry growth, and a crawl of a private address is one POST away otherwise).
 */
export function getWebsite(idOrUrl, { register = true } = {}) {
  if (!idOrUrl) return null;
  if (websites[idOrUrl]) return websites[idOrUrl];
  let u;
  try { u = new URL(idOrUrl); } catch { return null; }
  if (!/^https?:$/.test(u.protocol)) return null;
  const host = u.hostname.toLowerCase();
  const known = Object.values(websites).find((w) => w.allowedHosts.includes(host));
  if (known || !register) return known ?? null;
  if (isPrivateHost(host)) throw new Error(`refusing to register a private or local host: ${host}`);
  return registerWebsite(defineWebsite(idOrUrl));
}

/** Loopback, link-local, RFC1918 and bare hostnames: never a crawl target (SSRF guard). */
export function isPrivateHost(host) {
  const h = host.toLowerCase().replace(/^\[|\]$/g, "");
  if (h === "localhost" || h.endsWith(".localhost") || h.endsWith(".local") || h.endsWith(".internal") || !h.includes(".")) return true;
  if (/^(127\.|10\.|0\.|169\.254\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(h)) return true;
  if (/^(::1|::|fc|fd|fe80)/i.test(h)) return true;
  return false;
}
