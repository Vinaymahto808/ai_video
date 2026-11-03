/**
 * Conversation session: owns the turn state machine and the in-memory transcript of one call.
 *
 *   idle -> connecting -> greeting -> listening -> thinking -> speaking -> listening ... -> ended
 *
 * Barge-in: a user utterance that arrives while `speaking` moves straight to `thinking` and bumps
 * `generation`, so late events from the superseded turn are dropped. The human_bot server applies
 * the same rule and confirms it with an SSE `interrupted` event.
 *
 * The transcript here is in-memory only; since 2026-11-02 the *server* records the conversation (disclosed
 * in the greeting) and recognises a returning visitor by the id the loader sends in the context.
 */
import { Emitter } from "./events.js";

export const State = Object.freeze({
  IDLE: "idle", CONNECTING: "connecting", GREETING: "greeting", LISTENING: "listening",
  THINKING: "thinking", SPEAKING: "speaking", ENDED: "ended", ERROR: "error",
});

/** Spoken through the normal sentence path when a turn dies (transport error / inactivity timeout). */
export const RECOVERY_LINE = "Ek second, line thodi kharab hai. Dobara boliye.";
const RECOVERY_FALLBACK_MS = 8000; // if the audio layer never reports done, listen again anyway

const ALLOWED = {
  idle: ["connecting", "ended"],
  connecting: ["greeting", "error", "ended"],
  greeting: ["listening", "thinking", "ended"],
  listening: ["thinking", "ended"],
  thinking: ["speaking", "listening", "error", "ended"],
  speaking: ["listening", "thinking", "ended"],
  error: ["connecting", "ended"],
  ended: ["connecting"],
};

export class ConversationSession extends Emitter {
  /** @param {{transport: object, context?: object, persona?: string}} opts */
  constructor({ transport, context = {}, persona = "" }) {
    super();
    this.transport = transport;
    this.context = context;
    this.persona = persona;
    this.state = State.IDLE;
    this.id = null;
    this.generation = 0;
    this.transcript = [];
    this.meta = {};
  }

  /** @returns {boolean} whether the transition happened (illegal moves are logged, not forced). */
  #go(next, detail) {
    if (this.state === next) return false;
    if (!ALLOWED[this.state]?.includes(next)) {
      console.warn("[session] illegal transition " + this.state + " -> " + next);
      return false;
    }
    const prev = this.state;
    this.state = next;
    this.emit("state", { prev, next, detail });
    return true;
  }

  async start() {
    if (this.id) return { session_id: this.id, greeting: "", chunks: [], persona: this.meta.persona, provider: this.meta.provider };
    if (this.state === State.ENDED) return null; // a session object is single-use
    this.#go(State.CONNECTING);
    try {
      const res = await this.transport.openSession({ persona: this.persona, context: this.context });
      this.id = res.session_id;
      this.meta = { persona: res.persona, provider: res.provider };
      this.#go(State.GREETING);
      this.transcript.push({ role: "assistant", text: res.greeting, t: Date.now() });
      this.emit("assistant", {
        text: res.greeting,
        chunks: res.chunks?.length ? res.chunks : [{ text: res.greeting, emotion: "warm", gesture: "small_nod" }],
        greeting: true,
        generation: this.generation,
      });
      return res;
    } catch (e) {
      this.#go(State.ERROR, e);
      this.emit("error", e);
      throw e;
    }
  }

  /** Called by the audio layer when the assistant has finished speaking the current turn. */
  doneSpeaking(generation) {
    if (generation !== this.generation) return;
    if (this.state === State.SPEAKING || this.state === State.GREETING) this.#go(State.LISTENING);
  }

  /** One user utterance from STT. Acts as barge-in when the assistant is mid-sentence. */
  async say(text) {
    text = (text ?? "").trim();
    if (!text || !this.id) return;
    if (this.state === State.IDLE || this.state === State.CONNECTING || this.state === State.ENDED || this.state === State.ERROR) return;
    const bargeIn = this.state === State.SPEAKING || this.state === State.GREETING;
    const gen = ++this.generation;
    if (bargeIn) this.emit("barge-in", { generation: gen });
    this.transcript.push({ role: "user", text, t: Date.now() });
    this.emit("user", { text, generation: gen });
    this.#go(State.THINKING);

    let spoke = false;   // state moved to SPEAKING for this turn
    let streamed = 0;    // number of `sentence` events received (0 => speak `final.chunks`)
    try {
      await this.transport.chat({ session_id: this.id, text }, (event, data) => {
        if (gen !== this.generation) return; // superseded by a newer utterance or end()
        switch (event) {
          case "meta": this.meta.provider = data.provider; break;
          case "backchannel": this.emit("backchannel", { text: data.text, generation: gen }); break;
          case "sources": this.emit("sources", data.results ?? []); break;
          case "sentence":
            if (!spoke) { spoke = true; this.#go(State.SPEAKING); }
            streamed++;
            this.emit("sentence", { ...data, generation: gen });
            break;
          case "interrupted": break; // server confirms our barge-in
          case "final":
            this.transcript.push({ role: "assistant", text: data.text, t: Date.now() });
            if (!spoke) { spoke = true; this.#go(State.SPEAKING); }
            this.emit("final", { ...data, generation: gen, streamed });
            break;
          case "error":
            this.emit("error", new Error(data.error ?? "server error"));
            if (this.#recover(gen)) spoke = true;
            break;
          default: break;
        }
      });
      if (!spoke && gen === this.generation) this.#go(State.LISTENING);
    } catch (e) {
      if (e?.name === "AbortError") return; // our own barge-in cancelled this stream
      if (gen === this.generation) {
        this.emit("error", e);
        if (!this.#recover(gen)) this.#go(State.LISTENING);
      }
    }
  }

  /**
   * A turn failed: say a short recovery line as if it were the answer's sentence, so the customer
   * hears something instead of silence, then the audio layer's doneSpeaking() returns to listening.
   * @returns {boolean} whether the line was issued (false when the turn is stale or the call ended)
   */
  #recover(gen) {
    if (gen !== this.generation || this.state === State.ENDED || this.state === State.ERROR) return false;
    if (this.state !== State.SPEAKING && !this.#go(State.SPEAKING)) return false;
    this.emit("sentence", { index: 0, text: RECOVERY_LINE, emotion: "warm", gesture: "small_nod", generation: gen, recovery: true });
    const t = setTimeout(() => this.doneSpeaking(gen), RECOVERY_FALLBACK_MS);
    t.unref?.();
    return true;
  }

  async end() {
    if (this.state === State.ENDED) return this.id;
    const id = this.id;
    this.generation++; // invalidate in-flight turn callbacks before the state moves
    this.#go(State.ENDED);
    this.emit("ended", { session_id: id, turns: this.transcript.length });
    this.id = null;
    // the server closes the conversation record (analysis, audio merge); fire-and-forget
    try { this.transport.endSession?.(id); } catch { /* ignore */ }
    return id;
  }
}
