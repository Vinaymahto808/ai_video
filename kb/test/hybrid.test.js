import test from "node:test";
import assert from "node:assert/strict";
import { expandQuery } from "../src/search/hinglish.js";
import { KnowledgeStore } from "../src/store/sqlite.js";

test("Hinglish automotive words are expanded to English search terms", () => {
  const q = expandQuery("brake se awaaz aa rahi hai");
  assert.ok(q.includes("noise"), q);
  assert.ok(q.includes("brakes"), q);
  assert.equal(expandQuery("insurance claim status"), "insurance claim status insurance claim");
  assert.equal(expandQuery("hello"), "hello"); // nothing to expand: unchanged
  assert.equal(expandQuery(""), "");
});

test("rank fusion merges vector and lexical hits, caps two chunks per page, keeps the best score", () => {
  const hit = (id, page, score) => ({ chunk_id: id, page_id: page, url: "u" + page, title: "t", page_type: "page", heading: "", text: "x", chunk_index: 0, score });
  const vec = [hit(1, "A", 0.9), hit(2, "A", 0.88), hit(3, "A", 0.87), hit(4, "B", 0.8)];
  const lex = [hit(5, "C", 0.85), hit(4, "B", 0.7), hit(1, "A", 0.6)];
  const out = KnowledgeStore.fuse(vec, lex, { k: 5 });
  assert.ok(out.length <= 5);
  // chunk 1 and 4 appear in both lists: they rank first
  assert.deepEqual(out.slice(0, 2).map((h) => h.chunk_id).sort(), [1, 4]);
  assert.equal(out.find((h) => h.chunk_id === 1).score, 0.9); // best of the two sources
  assert.equal(out.filter((h) => h.url === "uA").length, 2); // page cap
  assert.ok(out.every((h) => !("page_id" in h)), "page_id is internal");
});

test("local vector table: coverage, insert, search", async () => {
  const s = new KnowledgeStore(":memory:");
  s.upsertWebsite({ id: "w", name: "w", domain: "w.in", startUrl: "https://w.in/" });
  const vec = (a, b) => Float32Array.from([a, b]);
  s.replacePage({ websiteId: "w", page: { url: "https://w.in/a", canonical: null, title: "A", description: null, pageType: "page", lang: "en", contentHash: "p1" }, chunks: [{ index: 0, text: "brake pads", hash: "h1", vec: vec(1, 0) }, { index: 1, text: "ac gas", hash: "h2", vec: vec(0, 1) }], model: "m", dim: 2, jobId: 1 });
  assert.deepEqual(s.localCoverage("w", "local"), { have: 0, want: 2 });
  const n = await s.ensureLocalVectors("w", "local", 2, async (rows) => rows.map((r) => (r.text.includes("brake") ? vec(0.9, 0.1) : vec(0.1, 0.9))));
  assert.equal(n, 2);
  assert.deepEqual(s.localCoverage("w", "local"), { have: 2, want: 2 });
  const hits = s.searchLocal("w", "local", vec(1, 0), { k: 2 });
  assert.equal(hits[0].text, "brake pads");
  assert.ok(hits[0].score > hits[1].score);
  // cascade: deleting the page removes its local vectors too
  s.wipeWebsite("w");
  assert.deepEqual(s.localCoverage("w", "local"), { have: 0, want: 0 });
  s.close();
});
