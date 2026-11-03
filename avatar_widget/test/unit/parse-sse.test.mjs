import test from "node:test";
import assert from "node:assert/strict";
import { parseSse } from "../../src/transport.js";

const enc = new TextEncoder();

/** A body that delivers `chunks` and then closes. */
function bodyOf(chunks) {
  return new ReadableStream({
    start(c) {
      for (const chunk of chunks) c.enqueue(enc.encode(chunk));
      c.close();
    },
  });
}

async function collect(chunks, signal) {
  const got = [];
  await parseSse(bodyOf(chunks), (event, data) => got.push([event, data]), signal);
  return got;
}

test("flushes a trailing event that never got its blank line", async () => {
  const got = await collect(['event: sentence\ndata: {"text":"hi"}']);
  assert.deepEqual(got, [["sentence", { text: "hi" }]]);
});

test("handles an event split across read chunks", async () => {
  const got = await collect(['event: sen', 'tence\ndata: {"text":"split"}', "\n\n"]);
  assert.deepEqual(got, [["sentence", { text: "split" }]]);
});

test("handles CRLF, comments and multi-line data", async () => {
  const got = await collect([
    ": keep-alive\r\n\r\n",
    "event: final\r\n",
    'data: {"text":"line one",\r\n',
    'data: "more":true}\r\n\r\n',
  ]);
  assert.deepEqual(got, [["final", { text: "line one", more: true }]]);
});

test("keeps non-JSON data as a string", async () => {
  const got = await collect(["data: plain text\n\n"]);
  assert.deepEqual(got, [["message", "plain text"]]);
});

test("ignores blocks with no data field", async () => {
  const got = await collect(["event: ping\n\n", "data: real\n\n"]);
  assert.deepEqual(got, [["message", "real"]]);
});

test("a pre-aborted signal reads nothing", async () => {
  const ac = new AbortController();
  ac.abort();
  const got = await collect(['data: {"n":1}\n\n'], ac.signal);
  assert.deepEqual(got, []);
});

test("a signal aborted mid-stream stops dispatching later events", async () => {
  const ac = new AbortController();
  const got = [];
  const timers = [];
  const body = new ReadableStream({
    start(c) {
      c.enqueue(enc.encode('data: {"n":1}\n\n'));
      // Abort after the first chunk is handed over; the second read must not dispatch.
      timers.push(setTimeout(() => {
        ac.abort();
        try { c.enqueue(enc.encode('data: {"n":2}\n\n')); } catch { /* cancelled */ }
      }, 5));
      timers.push(setTimeout(() => { try { c.close(); } catch { /* cancelled */ } }, 20));
    },
    cancel() { for (const t of timers) clearTimeout(t); },
  });
  await parseSse(body, (event, data) => got.push(data), ac.signal);
  assert.deepEqual(got, [{ n: 1 }]);
});
