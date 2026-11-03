/**
 * Transport adapters. The widget talks to ONE interface:
 *
 *   openSession({persona, context}) -> {session_id, greeting, chunks, persona, provider}
 *   chat({session_id, text}, onEvent(event, data))   // resolves when the turn is complete
 *   health() -> {ok, stt, tts, avatar, kb, ...}
 *   ttsUrl(text, voice) -> string          stt(blob) -> string | null
 *   avatar(path, body) -> {ok, status, data}
 *   close() -> release the stream/socket
 *
 * `SseTransport` implements it over human_bot's REST + Server-Sent-Events endpoints
 * (POST /session, POST /chat as text/event-stream, GET /tts, POST /stt, POST /avatar/*).
 * `WebSocketTransport` is the same contract over one socket for a future WS/LiveKit backend;
 * swapping transports is one line in panel bootstrap, nothing else moves.
 */

const HEALTH_TTL_MS = 5000; // avatar.mount() and mic.start() both ask; one request serves both

export class SseTransport {
  /**
   * @param {{base: string, turnTimeoutMs?: number}} o  base = e.g. "http://127.0.0.1:4001";
   * turnTimeoutMs = inactivity deadline for one /chat stream (reset on every event).
   */
  constructor({ base, turnTimeoutMs = 20000 }) {
    this.base = base.replace(/\/+$/, "");
    this.turnTimeoutMs = turnTimeoutMs;
    this.abort = null;
    this.healthCache = null; // {at, promise}
  }

  async openSession({ persona, context }) {
    const r = await fetch(this.base + "/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ persona: persona || undefined, sessionContext: context }),
    });
    if (!r.ok) throw new Error("session " + r.status);
    const res = await r.json();
    this.sessionId = res.session_id || null; // /stt tags each utterance's audio with it
    return res;
  }

  /** Tell the server the call is over: it finishes the conversation record (analysis, audio) and forgets the session. */
  endSession(session_id) {
    if (!session_id) return;
    if (this.sessionId === session_id) this.sessionId = null;
    const body = JSON.stringify({ session_id });
    try {
      if (navigator.sendBeacon?.(this.base + "/end", body)) return;
    } catch { /* fall through */ }
    fetch(this.base + "/end", { method: "POST", headers: { "Content-Type": "application/json" }, body, keepalive: true }).catch(() => {});
  }

  async chat({ session_id, text }, onEvent) {
    this.abort?.abort(); // barge-in: drop the previous stream client-side as well
    const ac = (this.abort = new AbortController());
    // Inactivity deadline: a stream that goes quiet (proxy hang, stalled model) is aborted and the
    // turn ends with an `error` event, so the session can recover instead of staying in "thinking".
    let timer = 0, timedOut = false;
    const arm = () => {
      clearTimeout(timer);
      timer = setTimeout(() => { timedOut = true; ac.abort(); }, this.turnTimeoutMs);
    };
    const on = (event, data) => { arm(); onEvent(event, data); };
    try {
      arm();
      const r = await fetch(this.base + "/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify({ session_id, text }),
        signal: ac.signal,
      });
      if (!r.ok || !r.body) throw new Error("chat " + r.status);
      await parseSse(r.body, on, ac.signal);
    } catch (e) {
      if (!timedOut) throw e;
    } finally {
      clearTimeout(timer);
      if (this.abort === ac) this.abort = null;
    }
    if (timedOut) onEvent("error", { error: "turn timeout: no reply for " + Math.round(this.turnTimeoutMs / 1000) + " s" });
  }

  /** /health, shared for HEALTH_TTL_MS: mount() and mic.start() call it back to back. */
  health() {
    const now = Date.now();
    if (this.healthCache && now - this.healthCache.at < HEALTH_TTL_MS) return this.healthCache.promise;
    const promise = fetch(this.base + "/health")
      .then((r) => (r.ok ? r.json() : { ok: false }))
      .catch((e) => { if (this.healthCache?.promise === promise) this.healthCache = null; throw e; });
    this.healthCache = { at: now, promise };
    return promise;
  }

  /** `emotion` (the sentence tag) selects the server's rate/pitch preset; prefetch and playback must agree. */
  ttsUrl(text, voice, emotion) {
    const q = new URLSearchParams({ text });
    if (voice) q.set("voice", voice);
    if (emotion && emotion !== "neutral") q.set("emo", emotion);
    return this.base + "/tts?" + q.toString();
  }

  async stt(blob) {
    const r = await fetch(this.base + "/stt" + (this.sessionId ? "?session_id=" + encodeURIComponent(this.sessionId) : ""), {
      method: "POST",
      headers: { "Content-Type": blob.type || "audio/webm" },
      body: blob,
    });
    if (r.status === 503) return null; // server says: use the browser recogniser
    if (!r.ok) throw new Error("stt " + r.status);
    return (await r.json()).text ?? "";
  }

  /** Live talking-head relay. API keys stay on the server; the browser only gets a session token/SDP. */
  avatar(path, body) {
    return fetch(this.base + "/avatar/" + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body ?? {}),
    }).then(async (r) => ({ ok: r.ok, status: r.status, data: await r.json().catch(() => ({})) }));
  }

  /** Barge-in already cancels the in-flight turn; close() is for teardown. */
  close() { this.abort?.abort(); this.abort = null; }
}

/**
 * Parse a text/event-stream body into (event, data) callbacks. Handles multi-line data, CRLF,
 * comment/keep-alive lines, and a final event whose terminating blank line never arrives
 * (proxies and `HTTP/1.0` servers do close straight after the last `data:` line).
 */
export async function parseSse(body, onEvent, signal) {
  const reader = body.getReader();
  const dec = new TextDecoder();
  let buf = "";

  const dispatch = (block) => {
    let event = "message";
    const data = [];
    for (const raw of block.split(/\r?\n/)) {
      const line = raw.endsWith("\r") ? raw.slice(0, -1) : raw;
      if (!line || line.startsWith(":")) continue; // named field-less comment / keep-alive
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
    }
    if (!data.length) return;
    let parsed = data.join("\n");
    try { parsed = JSON.parse(parsed); } catch { /* keep raw string */ }
    onEvent(event, parsed);
  };

  // An abort must also wake a read() that is parked on a silent stream (the inactivity deadline
  // relies on it; browsers reject the pending read themselves, polyfills and fakes may not).
  const onAbort = () => { reader.cancel().catch(() => {}); };
  signal?.addEventListener?.("abort", onAbort, { once: true });
  try {
    if (signal?.aborted) { await reader.cancel().catch(() => {}); return; }
    for (;;) {
      const { value, done } = await reader.read();
      if (signal?.aborted) { await reader.cancel().catch(() => {}); return; }
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.search(/\r?\n\r?\n/)) !== -1) {
        const block = buf.slice(0, idx);
        buf = buf.slice(idx).replace(/^\r?\n\r?\n/, "");
        dispatch(block);
      }
    }
    buf += dec.decode();
    const tail = buf.replace(/(?:\r?\n)+$/, "");
    if (tail.trim()) dispatch(tail);
  } catch (e) {
    if (e?.name === "AbortError" || signal?.aborted) return;
    throw e;
  } finally {
    signal?.removeEventListener?.("abort", onAbort);
  }
}

/**
 * Same contract over one WebSocket. Frames both ways are JSON:
 *   -> {id, type:"health"}                         <- {reply_to:id, data:{ok,...}}
 *   -> {id, type:"session", persona, context}      <- {reply_to:id, data:{session_id,...}}
 *   -> {id, type:"avatar", path, body}              <- {reply_to:id, data:{ok,status,data}}
 *   -> {id, type:"chat", session_id, text}          <- {type:"turn", event, data} ... until final/error/interrupted
 *   -> {type:"abort"}                               (barge-in; no reply expected)
 *
 * Every wait is bounded: connect timeout, RPC timeout, turn timeout, and close rejection. A socket
 * that dies mid-turn resolves the turn with an `error` event instead of hanging the state machine.
 */
export class WebSocketTransport {
  constructor({ url, ttsBase = "", WebSocketImpl = globalThis.WebSocket, timeoutMs = 12000, turnTimeoutMs = 60000 }) {
    this.url = url;
    this.ttsBase = (ttsBase || url.replace(/^ws/, "http")).replace(/\/+$/, "");
    this.WebSocketImpl = WebSocketImpl;
    this.timeoutMs = timeoutMs;
    this.turnTimeoutMs = turnTimeoutMs;
    this.ws = null;
    this.pending = new Map(); // id -> {resolve, reject, timer}
    this.seq = 0;
    this.turn = null; // {onEvent, done, timer}
    this.healthCache = null; // {at, promise}
  }

  #connect() {
    if (this.ws && this.ws.readyState === 1) return Promise.resolve();
    if (!this.WebSocketImpl) return Promise.reject(new Error("WebSocket unavailable"));
    this.#dropSocket();
    return new Promise((resolve, reject) => {
      let ws;
      try { ws = new this.WebSocketImpl(this.url); } catch (e) { reject(e); return; }
      this.ws = ws;
      let settled = false;
      const timer = setTimeout(() => {
        if (settled) return;
        settled = true;
        try { ws.close(); } catch { /* ignore */ }
        reject(new Error("ws connect timeout"));
      }, this.timeoutMs);
      const fail = (e) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        if (this.ws === ws) this.ws = null;
        reject(e instanceof Error ? e : new Error("ws error"));
      };
      ws.onopen = () => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        this.#attach(ws);
        resolve();
      };
      ws.onerror = (e) => fail(e?.error ?? new Error("ws error"));
      ws.onclose = (e) => fail(new Error("ws closed before open (" + (e?.code ?? "?") + ")"));
    });
  }

  #attach(ws) {
    ws.onmessage = (m) => {
      let f;
      try { f = JSON.parse(m.data); } catch { return; }
      if (f.reply_to && this.pending.has(f.reply_to)) {
        const p = this.pending.get(f.reply_to);
        this.pending.delete(f.reply_to);
        clearTimeout(p.timer);
        if (f.error) p.reject(new Error(String(f.error)));
        else p.resolve(f);
        return;
      }
      if (f.type === "turn") this.#deliver(f.event, f.data);
    };
    ws.onclose = () => {
      if (this.ws === ws) this.ws = null;
      this.#failAll(new Error("ws closed"));
      this.#deliver("error", { error: "ws closed" }); // a dead socket must not hang a turn
    };
    ws.onerror = () => { /* a close event always follows */ };
  }

  #dropSocket() {
    const ws = this.ws;
    this.ws = null;
    if (!ws) return;
    try { ws.onopen = ws.onmessage = ws.onclose = ws.onerror = null; } catch { /* ignore */ }
    try { ws.close(); } catch { /* ignore */ }
  }

  #failAll(err) {
    for (const p of this.pending.values()) { clearTimeout(p.timer); p.reject(err); }
    this.pending.clear();
  }

  #send(frame) {
    if (!this.ws || this.ws.readyState !== 1) return false;
    try { this.ws.send(JSON.stringify(frame)); return true; } catch { return false; }
  }

  #rpc(frame) {
    if (!this.ws || this.ws.readyState !== 1) return Promise.reject(new Error("ws not open"));
    const id = ++this.seq;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error("ws reply timeout for " + (frame.type || "frame")));
      }, this.timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      if (!this.#send({ id, ...frame })) {
        clearTimeout(timer);
        this.pending.delete(id);
        reject(new Error("ws send failed"));
      }
    });
  }

  /** Feed one turn event; a terminal event resolves the pending chat() call exactly once. */
  #deliver(event, data) {
    const t = this.turn;
    if (!t) return;
    try { t.onEvent(event, data); } catch (e) { console.warn("[ws] turn listener threw", e); }
    if (event === "final" || event === "error" || event === "interrupted") {
      this.turn = null;
      clearTimeout(t.timer);
      t.done();
    }
  }

  /** A turn that will never finish (superseded / socket closed / timeout) still resolves. */
  #abortTurn(reason, extra) {
    const t = this.turn;
    if (!t) return;
    this.turn = null;
    clearTimeout(t.timer);
    try { t.onEvent("interrupted", { reason, ...extra }); } catch { /* ignore */ }
    t.done();
  }

  async openSession(o) { await this.#connect(); return (await this.#rpc({ type: "session", ...o })).data; }

  async chat(o, onEvent) {
    await this.#connect();
    this.#abortTurn("superseded"); // barge-in: the previous turn is no longer interesting
    this.#send({ type: "abort" }); // ask the server to stop generating it too
    return new Promise((resolve) => {
      const timer = setTimeout(() => this.#deliver("error", { error: "turn timeout" }), this.turnTimeoutMs);
      this.turn = { onEvent, done: resolve, timer };
      if (!this.#send({ type: "chat", ...o })) this.#deliver("error", { error: "ws not open" });
    });
  }

  /** Relay the backend's /health so mode selection (live avatar) and mic STT behave like SSE. */
  health() {
    const now = Date.now();
    if (this.healthCache && now - this.healthCache.at < HEALTH_TTL_MS) return this.healthCache.promise;
    const promise = (async () => {
      try { await this.#connect(); return (await this.#rpc({ type: "health" })).data ?? { ok: true, transport: "ws" }; }
      catch { this.healthCache = null; return { ok: false, transport: "ws" }; }
    })();
    this.healthCache = { at: now, promise };
    return promise;
  }

  ttsUrl(text, voice, emotion) {
    const q = new URLSearchParams({ text });
    if (voice) q.set("voice", voice);
    if (emotion && emotion !== "neutral") q.set("emo", emotion);
    return this.ttsBase + "/tts?" + q.toString();
  }

  async stt() { return null; } // binary uploads are out of scope for the WS contract; Mic falls back

  /** Live talking-head control is relayed as an RPC so the shim can forward it to /avatar/*. */
  async avatar(path, body) {
    try {
      await this.#connect();
      return (await this.#rpc({ type: "avatar", path, body: body ?? {} })).data ?? { ok: false, status: 502, data: {} };
    } catch (e) {
      return { ok: false, status: 0, data: { error: String(e?.message || e) } };
    }
  }

  close() {
    this.#abortTurn("closed");
    this.#failAll(new Error("ws closed"));
    this.#dropSocket();
  }
}
