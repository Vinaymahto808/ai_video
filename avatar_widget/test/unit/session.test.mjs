import test from "node:test";
import assert from "node:assert/strict";
import { ConversationSession, State, RECOVERY_LINE } from "../../src/session.js";

const flush = () => new Promise((r) => setImmediate(r));

class FakeTransport {
  constructor() {
    this.opens = [];
    this.chats = [];
    this.fail = null;
  }
  async openSession(opts) {
    this.opens.push(opts);
    if (this.fail) throw this.fail;
    return {
      session_id: "s1",
      greeting: "Namaste!",
      chunks: [{ text: "Namaste!", emotion: "warm", gesture: "small_nod" }],
      persona: { id: "auto-car-repair", name: "Neha", company: "Auto Car Repair" },
      provider: "fake",
    };
  }
  /** Each call parks a controllable turn so tests can feed events in any order. */
  chat(opts, onEvent) {
    return new Promise((resolve, reject) => {
      this.chats.push({ ...opts, onEvent, resolve, reject });
    });
  }
}

function make() {
  const transport = new FakeTransport();
  const s = new ConversationSession({ transport, persona: "auto-car-repair", context: { page: { model: "Creta" } } });
  return { transport, s };
}

test("start opens a session, emits greeting and assistant chunks", async () => {
  const { transport, s } = make();
  const states = [];
  s.on("state", ({ next }) => states.push(next));
  const assistant = new Promise((r) => s.on("assistant", r));

  const res = await s.start();

  assert.equal(res.session_id, "s1");
  assert.equal(s.id, "s1");
  assert.equal(s.state, State.GREETING);
  assert.deepEqual(states, [State.CONNECTING, State.GREETING]);
  assert.equal(transport.opens.length, 1);
  assert.equal(transport.opens[0].persona, "auto-car-repair");
  assert.deepEqual(transport.opens[0].context, { page: { model: "Creta" } });
  const a = await assistant;
  assert.equal(a.greeting, true);
  assert.equal(a.chunks[0].text, "Namaste!");
  assert.deepEqual(s.transcript.map((t) => t.role), ["assistant"]);
});

test("a turn goes listening -> thinking -> speaking and records both sides", async () => {
  const { transport, s } = make();
  await s.start();
  s.doneSpeaking(0);
  assert.equal(s.state, State.LISTENING);

  const finals = [];
  s.on("final", (f) => finals.push(f));
  const turn = s.say("  service ka price?  ");
  await flush();

  assert.equal(s.state, State.THINKING);
  assert.equal(transport.chats.length, 1);
  assert.equal(transport.chats[0].text, "service ka price?");
  assert.equal(transport.chats[0].session_id, "s1");

  transport.chats[0].onEvent("sentence", { index: 0, text: "Haan ji." });
  assert.equal(s.state, State.SPEAKING);
  transport.chats[0].onEvent("final", { text: "Haan ji.", chunks: [] });
  transport.chats[0].resolve();
  await turn;

  assert.equal(s.state, State.SPEAKING); // doneSpeaking comes from the audio layer
  assert.equal(finals.length, 1);
  assert.equal(finals[0].streamed, 1);
  assert.deepEqual(s.transcript.map((t) => t.role), ["assistant", "user", "assistant"]);
  s.doneSpeaking(finals[0].generation);
  assert.equal(s.state, State.LISTENING);
});

test("barge-in bumps the generation and drops late events from the old turn", async () => {
  const { transport, s } = make();
  await s.start();
  s.doneSpeaking(0);

  const turn1 = s.say("first");
  await flush();
  transport.chats[0].onEvent("sentence", { text: "one" });
  assert.equal(s.state, State.SPEAKING);

  const gen1 = s.generation;
  const bargeIns = [];
  const finals = [];
  s.on("barge-in", (b) => bargeIns.push(b));
  s.on("final", (f) => finals.push(f));

  const turn2 = s.say("stop");
  await flush();
  assert.equal(s.generation, gen1 + 1);
  assert.equal(bargeIns.length, 1);
  assert.equal(s.state, State.THINKING);

  transport.chats[0].onEvent("final", { text: "too late", chunks: [] }); // stale
  transport.chats[0].resolve();
  assert.equal(finals.length, 0);

  transport.chats[1].onEvent("final", { text: "fresh", chunks: [{ text: "fresh" }] });
  transport.chats[1].resolve();
  await Promise.all([turn1, turn2]);
  assert.equal(finals.length, 1);
  assert.equal(finals[0].text, "fresh");
  assert.deepEqual(s.transcript.filter((t) => t.role === "assistant").map((t) => t.text), ["Namaste!", "fresh"]);
});

test("doneSpeaking ignores a superseded generation and is harmless while thinking", async () => {
  const { s } = make();
  await s.start();
  const gen = s.generation;
  s.doneSpeaking(gen + 1); // stale audio callback
  assert.equal(s.state, State.GREETING);
  s.doneSpeaking(gen);
  assert.equal(s.state, State.LISTENING);
  s.doneSpeaking(gen);
  assert.equal(s.state, State.LISTENING);
});

test("say is ignored before a session exists and after it ends", async () => {
  const { transport, s } = make();
  await s.say("too early");
  assert.equal(transport.chats.length, 0);

  await s.start();
  s.doneSpeaking(0);
  const turn = s.say("hello"); // FakeTransport parks it until the test resolves it
  await flush();
  assert.equal(transport.chats.length, 1);

  const ended = [];
  s.on("ended", (e) => ended.push(e));
  const id = await s.end();
  assert.equal(id, "s1");
  assert.equal(s.state, State.ENDED);
  await s.say("after end");
  assert.equal(transport.chats.length, 1);
  assert.equal(ended.length, 1);
});

test("end is idempotent", async () => {
  const { s } = make();
  await s.start();
  const ended = [];
  s.on("ended", (e) => ended.push(e));

  const first = await s.end();
  const second = await s.end();
  assert.equal(first, "s1");
  assert.equal(second, null);
  assert.equal(ended.length, 1);
  assert.equal(s.id, null);
});

test("end invalidates an in-flight turn so its events are dropped", async () => {
  const { transport, s } = make();
  await s.start();
  s.doneSpeaking(0);
  const turn = s.say("question");
  await flush();

  const spurious = [];
  s.on("sentence", (x) => spurious.push(x));
  await s.end();
  transport.chats[0].onEvent("sentence", { text: "ghost" });
  transport.chats[0].resolve();
  await turn;

  assert.equal(spurious.length, 0);
  assert.equal(s.state, State.ENDED);
});

test("a transport error mid-turn speaks the recovery line, then listens once it is done", async () => {
  const { transport, s } = make();
  await s.start();
  s.doneSpeaking(0);
  const sentences = [];
  const errors = [];
  s.on("sentence", (x) => sentences.push(x));
  s.on("error", (e) => errors.push(e));

  const turn = s.say("hello?");
  await flush();
  transport.chats[0].reject(new TypeError("Failed to fetch"));
  await turn;

  assert.equal(errors.length, 1);
  assert.equal(sentences.length, 1);
  assert.equal(sentences[0].text, RECOVERY_LINE);
  assert.equal(sentences[0].recovery, true);
  assert.equal(sentences[0].generation, s.generation);
  assert.equal(s.state, State.SPEAKING);
  s.doneSpeaking(sentences[0].generation); // the audio layer finished the line
  assert.equal(s.state, State.LISTENING);
});

test("a server `error` event mid-turn also recovers through the sentence path", async () => {
  const { transport, s } = make();
  await s.start();
  s.doneSpeaking(0);
  const sentences = [];
  s.on("sentence", (x) => sentences.push(x));
  const turn = s.say("hello?");
  await flush();
  transport.chats[0].onEvent("error", { error: "turn timeout" });
  transport.chats[0].resolve();
  await turn;
  assert.deepEqual(sentences.map((x) => x.text), [RECOVERY_LINE]);
  assert.equal(s.state, State.SPEAKING);
});

test("a stale turn's failure is ignored (no recovery line for a superseded generation)", async () => {
  const { transport, s } = make();
  await s.start();
  s.doneSpeaking(0);
  const sentences = [];
  s.on("sentence", (x) => sentences.push(x));
  const turn1 = s.say("first");
  await flush();
  const turn2 = s.say("second");
  await flush();
  transport.chats[0].reject(new Error("old stream died"));
  await turn1;
  assert.equal(sentences.length, 0);
  assert.equal(s.state, State.THINKING);
  transport.chats[1].onEvent("final", { text: "ok", chunks: [] });
  transport.chats[1].resolve();
  await turn2;
});

test("a failed open lands in error and rethrows", async () => {
  const { transport, s } = make();
  transport.fail = new Error("upstream down");
  const errors = [];
  s.on("error", (e) => errors.push(e));

  await assert.rejects(s.start(), /upstream down/);
  assert.equal(s.state, State.ERROR);
  assert.equal(errors.length, 1);
});
