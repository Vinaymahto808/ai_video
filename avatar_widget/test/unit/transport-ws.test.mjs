import test from "node:test";
import assert from "node:assert/strict";
import { WebSocketTransport } from "../../src/transport.js";

const OPEN = 1;
const CLOSED = 3;
const flush = () => new Promise((r) => setImmediate(r));

class FakeSocket {
  static instances = [];
  constructor(url) {
    this.url = url;
    this.readyState = 0;
    this.sent = [];
    FakeSocket.instances.push(this);
  }
  send(data) {
    if (this.readyState !== OPEN) throw new Error("not open");
    this.sent.push(JSON.parse(data));
  }
  close() { this.readyState = CLOSED; this.onclose?.({ code: 1000 }); }
  /* --- test controls --- */
  open() { this.readyState = OPEN; this.onopen?.({}); }
  message(obj) { this.onmessage?.({ data: JSON.stringify(obj) }); }
  serverClose(code = 1006) { this.readyState = CLOSED; this.onclose?.({ code }); }
}

function make(opts = {}) {
  FakeSocket.instances.length = 0;
  return new WebSocketTransport({ url: "ws://test/socket", WebSocketImpl: FakeSocket, timeoutMs: 200, ...opts });
}

/** Connect + answer the session RPC, leaving the transport ready for a chat turn. */
async function connected(t) {
  const p = t.openSession({ persona: "p" });
  const ws = FakeSocket.instances.at(-1);
  ws.open();
  await flush();
  ws.message({ reply_to: ws.sent[0].id, data: { session_id: "s1", greeting: "hi" } });
  await p;
  return ws;
}

test("openSession sends a session frame and resolves with data", async () => {
  const t = make();
  const p = t.openSession({ persona: "auto-car-repair", context: { page: { model: "Creta" } } });
  const ws = FakeSocket.instances.at(-1);
  assert.equal(ws.url, "ws://test/socket");
  assert.equal(ws.readyState, 0);
  ws.open();
  await flush();

  assert.equal(ws.sent.length, 1);
  assert.deepEqual(ws.sent[0], {
    id: 1, type: "session", persona: "auto-car-repair", context: { page: { model: "Creta" } },
  });

  ws.message({ reply_to: 1, data: { session_id: "s1", greeting: "Namaste" } });
  assert.deepEqual(await p, { session_id: "s1", greeting: "Namaste" });
});

test("a socket that closes during connect rejects instead of hanging", async () => {
  const t = make();
  const p = t.openSession({});
  const ws = FakeSocket.instances.at(-1);
  ws.serverClose(1006);
  await assert.rejects(p, /ws closed before open \(1006\)/);
});

test("a connect that never opens times out", async () => {
  const t = make({ timeoutMs: 20 });
  const p = t.openSession({});
  await assert.rejects(p, /connect timeout/);
});

test("an unanswered RPC times out", async () => {
  const t = make({ timeoutMs: 20 });
  const p = t.openSession({});
  FakeSocket.instances.at(-1).open();
  await assert.rejects(p, /reply timeout for session/);
});

test("an RPC rejects when the socket closes before replying", async () => {
  const t = make();
  const p = t.openSession({});
  const ws = FakeSocket.instances.at(-1);
  ws.open();
  await flush();
  ws.serverClose();
  await assert.rejects(p, /ws closed/);
});

test("chat forwards turn events and resolves on final", async () => {
  const t = make();
  const ws = await connected(t);
  const events = [];
  const turn = t.chat({ session_id: "s1", text: "price?" }, (e, d) => events.push([e, d]));
  await flush();

  assert.equal(ws.sent.at(-1).type, "chat");
  assert.equal(ws.sent.at(-1).text, "price?");
  assert.ok(ws.sent.some((f) => f.type === "abort")); // previous turn is told to stop

  ws.message({ type: "turn", event: "backchannel", data: { text: "acha" } });
  ws.message({ type: "turn", event: "sentence", data: { text: "Haan ji." } });
  ws.message({ type: "turn", event: "final", data: { text: "Haan ji." } });
  await turn;

  assert.deepEqual(events.map(([e]) => e), ["backchannel", "sentence", "final"]);
});

test("a new chat resolves the superseded turn as interrupted", async () => {
  const t = make();
  const ws = await connected(t);
  const first = [];
  const turn1 = t.chat({ session_id: "s1", text: "one" }, (e, d) => first.push([e, d]));
  await flush();

  const turn2 = t.chat({ session_id: "s1", text: "two" }, () => {});
  await turn1;
  assert.deepEqual(first.map(([e]) => e), ["interrupted"]);
  assert.equal(first[0][1].reason, "superseded");
  assert.equal(ws.sent.at(-1).text, "two");
  await flush();
  ws.message({ type: "turn", event: "error", data: { error: "test cleanup" } });
  await turn2;
});

test("a socket that dies mid-turn resolves the turn with an error event", async () => {
  const t = make();
  const ws = await connected(t);
  const events = [];
  const turn = t.chat({ session_id: "s1", text: "hi" }, (e, d) => events.push([e, d]));
  await flush();
  ws.serverClose(1006);
  await turn;
  assert.deepEqual(events.map(([e]) => e), ["error"]);
  assert.equal(events[0][1].error, "ws closed");
});

test("malformed JSON frames are ignored, not thrown", async () => {
  const t = make();
  const ws = await connected(t);
  const events = [];
  const turn = t.chat({ session_id: "s1", text: "hi" }, (e, d) => events.push(e));
  await flush();
  ws.onmessage({ data: "{not json" });
  ws.message({ type: "turn", event: "final", data: { text: "ok" } });
  await turn;
  assert.deepEqual(events, ["final"]);
});

test("close() ends the turn and reports a closed transport", async () => {
  const t = make();
  const ws = await connected(t);
  const events = [];
  const turn = t.chat({ session_id: "s1", text: "hi" }, (e) => events.push(e));
  await flush();
  t.close();
  await turn;
  assert.deepEqual(events, ["interrupted"]);
  assert.equal(ws.readyState, CLOSED);
  assert.deepEqual(await t.health(), { ok: false, transport: "ws" });
});

test("ttsUrl falls back to the HTTP origin of the ws URL", () => {
  const t = make();
  assert.equal(t.ttsUrl("a b", "v"), "http://test/socket/tts?text=a+b&voice=v");
});
