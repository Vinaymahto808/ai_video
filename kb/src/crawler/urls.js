// URL hygiene: one canonical form per page so the queue, the DB and the dedupe all agree.
const TRACKING = new Set([
  "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
  "gclid", "fbclid", "msclkid", "dclid", "yclid", "mc_cid", "mc_eid", "ref", "igshid", "_ga", "_gl",
]);

/** Normalize a URL for dedupe. Returns null when it is not an http(s) URL. */
export function normalizeUrl(input, base) {
  let u;
  try {
    u = new URL(input, base);
  } catch {
    return null;
  }
  if (u.protocol !== "http:" && u.protocol !== "https:") return null;
  u.hash = "";
  u.hostname = u.hostname.toLowerCase();
  u.username = u.password = "";
  if ((u.protocol === "https:" && u.port === "443") || (u.protocol === "http:" && u.port === "80")) u.port = "";
  for (const k of [...u.searchParams.keys()]) {
    if (TRACKING.has(k.toLowerCase()) || k.toLowerCase().startsWith("utm_")) u.searchParams.delete(k);
  }
  u.searchParams.sort();
  // collapse duplicate slashes and a trailing slash (keep "/" for the root)
  let path = u.pathname.replace(/\/{2,}/g, "/");
  if (path.length > 1 && path.endsWith("/")) path = path.slice(0, -1);
  // index.html / index.php are the directory itself
  path = path.replace(/\/index\.(html?|php)$/i, "") || "/";
  u.pathname = path;
  return u.toString();
}

/** True when the URL is on an approved host and not on a disallowed path. */
export function isAllowedUrl(url, site) {
  let u;
  try {
    u = new URL(url);
  } catch {
    return false;
  }
  if (!site.allowedHosts.includes(u.hostname.toLowerCase())) return false;
  const target = u.pathname + u.search;
  return !(site.disallow ?? []).some((re) => re.test(target));
}

/** Coarse page type from URL (and title when the URL is silent). Used for filtering at search time. */
export function classifyPageType(url, title = "") {
  let path = "/";
  try {
    path = new URL(url).pathname.toLowerCase();
  } catch { /* keep "/" */ }
  const t = title.toLowerCase();
  if (path === "/" || path === "") return "home";
  if (/\/blog|\/car-service-guides|\/news|\/article/.test(path)) return "blog";
  if (/faq/.test(path) || /faq|frequently asked/.test(t)) return "faq";
  if (/contact|service-centers?|locations?|showroom|branch|near-me|-in-(delhi|gurgaon|gurugram|noida|faridabad|ghaziabad)\b/.test(path)) return "location";
  if (/offer|coupon|deal|promotion|discount|festive/.test(path)) return "offers";
  if (/about|team|career|testimonial|gallery|corporate|privacy|terms|sitemap/.test(path)) return "about";
  if (/insurance|detailing|service|repair|maintenance|denting|painting|ac-|clutch|suspension|battery|tyre|wheel|lights|glass|engine|body/.test(path)) return "service";
  if (/car|vehicle|model|variant|price|brochure|new-|suv|sedan|hatchback|ev|electric/.test(path)) return "product";
  return "page";
}

/** Short stable id for a URL (used in logs, never as a DB key). */
export function hostOf(url) {
  try {
    return new URL(url).hostname.toLowerCase();
  } catch {
    return "";
  }
}
