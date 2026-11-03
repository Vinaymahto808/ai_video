import { test } from "node:test";
import assert from "node:assert/strict";
import { KnowledgeStore } from "../src/store/sqlite.js";
import { createApp } from "../src/server.js";
import { websites } from "../src/config.js";

const unit = (arr) => { const n = Math.hypot(...arr); return Float32Array.from(arr, (x) => x / n); };
const page = (url, hash = "h1") => ({ url, canonical: url, title: "T " + url, description: "", pageType: "service", lang: "en", contentHash: hash });
const chunk = (i, vec, text = "chunk " + i) => ({ index: i, heading: "H", text, hash: "c" + i, vec: unit(vec) });

test("store: insert, search ranks by cosine, website isolation, incremental replace, inactive excluded", () => {
  const s = new KnowledgeStore(":memory:");
  s.upsertWebsite(websites.auto_car_repair);
  s.upsertWebsite(websites.galaxy_toyota);
  const job = s.startJob("auto_car_repair", "crawl");

  s.replacePage({ websiteId: "auto_car_repair", page: page("https://autocarrepair.in/ac"), chunks: [chunk(0, [1, 0, 0], "ac repair"), chunk(1, [0, 1, 0], "ac gas")], model: "m", dim: 3, jobId: job, httpStatus: 200 });
  s.replacePage({ websiteId: "galaxy_toyota", page: page("https://galaxytoyota.in/x"), chunks: [chunk(0, [1, 0, 0], "toyota")], model: "m", dim: 3, jobId: job, httpStatus: 200 });

  const r = s.search("auto_car_repair", unit([0.9, 0.1, 0]), { k: 5 });
  assert.equal(r[0].text, "ac repair");
  assert.ok(r[0].score > r[1].score);
  assert.ok(r.every((x) => x.url.startsWith("https://autocarrepair.in")), "no cross-website leakage");
  assert.equal(s.search("galaxy_toyota", unit([1, 0, 0]), { k: 5 }).length, 1);

  // model mismatch is refused
  assert.throws(() => s.assertModelCompatible("auto_car_repair", "other", 3), /already holds/);
  assert.doesNotThrow(() => s.assertModelCompatible("auto_car_repair", "m", 3));

  // unchanged content -> touch, same chunk count; changed -> replace, no duplicates
  const p0 = s.getPage("auto_car_repair", "https://autocarrepair.in/ac");
  assert.equal(p0.content_hash, "h1");
  s.replacePage({ websiteId: "auto_car_repair", page: page("https://autocarrepair.in/ac", "h2"), chunks: [chunk(0, [0, 0, 1], "ac new")], model: "m", dim: 3, jobId: job, httpStatus: 200 });
  const p1 = s.getPage("auto_car_repair", "https://autocarrepair.in/ac");
  assert.equal(p1.id, p0.id);
  assert.equal(p1.content_hash, "h2");
  assert.equal(p1.chunk_count, 1);
  assert.equal(s.stats("auto_car_repair").chunks, 1);
  assert.equal(s.search("auto_car_repair", unit([1, 0, 0]), { k: 5, minScore: 0.5 }).length, 0);

  // deactivation guard: a second job that saw too little changes nothing
  const job2 = s.startJob("auto_car_repair", "refresh");
  s.replacePage({ websiteId: "auto_car_repair", page: page("https://autocarrepair.in/b"), chunks: [chunk(0, [0, 1, 0])], model: "m", dim: 3, jobId: job2, httpStatus: 200 });
  const guard = s.deactivateUnseen("auto_car_repair", job2, 0, 0.5);
  assert.equal(guard.skipped, true);
  assert.equal(s.getPage("auto_car_repair", "https://autocarrepair.in/ac").status, "active");
  const d = s.deactivateUnseen("auto_car_repair", job2, 2, 0.5);
  assert.equal(d.deactivated, 1);
  assert.equal(s.getPage("auto_car_repair", "https://autocarrepair.in/ac").status, "inactive");
  assert.equal(s.search("auto_car_repair", unit([0, 0, 1]), { k: 5 }).length, 1, "inactive pages are not searchable");
  // galaxy untouched by all of the above
  assert.equal(s.stats("galaxy_toyota").chunks, 1);

  // failure recording does not delete knowledge
  s.recordFailure("auto_car_repair", "https://autocarrepair.in/b", job2, 500, "boom");
  assert.equal(s.getPage("auto_car_repair", "https://autocarrepair.in/b").status, "active");
  s.wipeWebsite("galaxy_toyota");
  assert.equal(s.stats("galaxy_toyota").chunks, 0);
  assert.equal(s.stats("auto_car_repair").chunks, 2);
  s.close();
});

test("api: validation, auth and 404s (no network, no crawl)", async () => {
  const store = new KnowledgeStore(":memory:");
  const jobs = { status: () => ({ state: "idle" }), start: () => { throw Object.assign(new Error("x"), { code: "BUSY" }); }, stop: () => ({ stopped: false }) };
  const app = createApp({ store, jobs });
  const srv = app.listen(0, "127.0.0.1");
  await new Promise((r) => srv.once("listening", r));
  const base = `http://127.0.0.1:${srv.address().port}`;
  const j = async (path, init) => { const r = await fetch(base + path, init); return { status: r.status, body: await r.json() }; };

  assert.equal((await j("/health")).body.ok, true);
  assert.equal((await j("/api/crawler/start", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ website: "evil.com" }) })).status, 400);
  assert.equal((await j("/api/crawler/start", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ website: "auto_car_repair", maxPages: 0 }) })).status, 400);
  assert.equal((await j("/api/crawler/start", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ website: "auto_car_repair", maxConcurrency: 99 }) })).status, 400);
  assert.equal((await j("/api/crawler/start", { method: "POST", headers: { "content-type": "application/json" }, body: "{bad" })).status, 400);
  assert.equal((await j("/api/crawler/start", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ website: "https://autocarrepair.in/" }) })).status, 409, "busy manager -> 409");
  assert.equal((await j("/api/knowledge/search?website=auto_car_repair")).status, 400);
  assert.equal((await j("/api/knowledge/search?q=x")).status, 400);
  assert.equal((await j("/api/knowledge/search?q=x&website=auto_car_repair&min_score=5")).status, 400);
  assert.equal((await j("/api/crawler/pages")).status, 400);
  assert.equal((await j("/api/crawler/pages?website=auto_car_repair")).body.pages.length, 0);
  assert.equal((await j("/nope")).status, 404);
  srv.close();
  store.close();
});
