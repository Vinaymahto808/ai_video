import test from "node:test";
import assert from "node:assert/strict";
import { SseTransport } from "../../src/transport.js";

/** A fetch stub whose /chat body is a stream the test writes to (or leaves silent). */
function fakeFetch({ onHealth } = {}) {
  let controller = null;
  let health = 0;
  const f = async (url, init) => {
    if (String(url).endsWith("/health")) { health++; onHealth?.(); return { ok: true, json: async () => ({ ok: true, n: health }) }; }
    const enc = new TextEncoder();
    const body = new ReadableStream({
      start(c) { controller = { write: (s) => c.enqueue(enc.encode(s)), close: () => c.close() }; },
      cancel() { controller = null; },
    });
    init?.signal?.addEventListener("abort", () => { /* reader.cancel() is driven by parseSse */ });
    return { ok: true, status: 200, body };
  };
  return { fetch: f, get stream() { return controller; }, get healthCalls() { return health; } };
}

test("a stream that goes quiet is aborted after the inactivity deadline and ends with an error event", async () => {
  const fx = fakeFetch();
  globalThis.fetch = fx.fetch;
  const t = new SseTransport({ base: "http://bot", turnTimeoutMs: 80 });
  const events = [];
  const t0 = Date.now();
  await t.chat({ session_id: "s", text: "hi" }, (e, d) => events.push([e, d]));
  const took = Date.now() - t0;
  assert.ok(took >= 70 && took < 1500, "resolved by the deadline, took " + took + " ms");
  assert.equal(events.length, 1);
  assert.equal(events[0][0], "error");
  assert.match(events[0][1].error, /timeout/);
  assert.equal(t.abort, null);
});

test("every event resets the deadline; a normal final does not time out", async () => {
  const fx = fakeFetch();
  globalThis.fetch = fx.fetch;
  const t = new SseTransport({ base: "http://bot", turnTimeoutMs: 120 });
  const events = [];
  const p = t.chat({ session_id: "s", text: "hi" }, (e, d) => events.push(e));
  for (let i = 0; i < 4; i++) {
    await new Promise((r) => setTimeout(r, 70)); // each gap is below the deadline, the total is above it
    fx.stream.write("event: sentence\ndata: {\"text\":\"s" + i + "\"}\n\n");
  }
  await new Promise((r) => setTimeout(r, 20));
  fx.stream.write("event: final\ndata: {\"text\":\"done\"}\n\n");
  fx.stream.close();
  await p;
  assert.deepEqual(events, ["sentence", "sentence", "sentence", "sentence", "final"]);
});

test("health() is shared for a few seconds so mount() and mic.start() make one request", async () => {
  const fx = fakeFetch();
  globalThis.fetch = fx.fetch;
  const t = new SseTransport({ base: "http://bot" });
  const [a, b] = await Promise.all([t.health(), t.health()]);
  assert.equal(fx.healthCalls, 1);
  assert.deepEqual(a, b);
  t.healthCache.at -= 10000; // expire
  await t.health();
  assert.equal(fx.healthCalls, 2);
});
