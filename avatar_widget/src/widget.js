/**
 * Orchestrator: wires session <-> audio <-> avatar <-> UI inside panel.html, and speaks the
 * postMessage bridge to loader.js on the host page (see schemas/bridge.schema.json).
 *
 * Voice-only by design: no text input, no chat log, no persisted transcript.
 */
import { ConversationSession, State } from "./session.js";
import { SseTransport, WebSocketTransport } from "./transport.js";
import { Speaker, Mic } from "./audio.js";
import { Avatar } from "./avatar.js";

export function createWidget(cfg) {
  const transport = cfg.ws ? new WebSocketTransport({ url: cfg.ws, ttsBase: cfg.api }) : new SseTransport({ base: cfg.api });
  const speaker = new Speaker({ transport, voice: cfg.voice });
  const mic = new Mic({ transport, lang: cfg.lang || "hi-IN" });
  const avatar = new Avatar({ transport, speaker, portraitUrl: cfg.portrait });
  const bridge = makeBridge(cfg.hostOrigin);
  let session = null;
  let starting = false;
  let micStarting = null; // in-flight Mic.start(): the button must not treat it as "on" or "off" yet
  let ctx = cfg.context || {};
  let micWanted = false; // the customer's choice (mic button); a hidden tab pauses it, not changes it
  let wakeLock = null;

  const ui = cfg.ui; // {video, img, status, micBtn, endBtn, level, name, sub, tapToStart?}

  bridge.on((msg) => {
    if (msg.type === "aw:ctx" && msg.ctx) ctx = msg.ctx;
    if (msg.type === "aw:close") end(false);
    if (msg.type === "aw:open" && !session) openFromHost();
  });

  /**
   * aw:open arrives by postMessage, i.e. with no gesture inside this frame. If the browser still
   * blocks audio here (iOS, strict autoplay), show the tap UI so the first tap lands in the iframe
   * and unlocks it; otherwise start straight away.
   */
  async function openFromHost() {
    if (session || starting) return;
    const ua = navigator.userActivation;
    let blocked = false;
    if (ua && !ua.hasBeenActive) {
      await speaker.unlock();
      blocked = !!speaker.ctx && !speaker.unlocked;
    }
    if (blocked && typeof ui.tapToStart === "function") ui.tapToStart(start);
    else start();
  }

  function setStatus(text, state) {
    ui.status.textContent = text;
    ui.status.dataset.state = state;
    document.body.dataset.state = state;
    bridge.send({ type: "aw:state", state });
  }

  async function holdWake() {
    try {
      if (wakeLock || !navigator.wakeLock || document.visibilityState !== "visible") return;
      wakeLock = await navigator.wakeLock.request("screen");
      wakeLock.addEventListener?.("release", () => { wakeLock = null; });
    } catch { wakeLock = null; } // denied (low battery, not visible): the call still works
  }
  function releaseWake() {
    const w = wakeLock; wakeLock = null;
    try { w?.release(); } catch { /* ignore */ }
  }

  async function start() {
    if (session || starting) return;
    starting = true;
    try {
      await speaker.unlock();
    } catch { /* autoplay policy: the greeting will still try, and the user can tap mic */ }
    session = new ConversationSession({ transport, context: ctx, persona: cfg.persona });

    session.on("state", ({ next }) => {
      const label = { connecting: "Connecting…", greeting: "", listening: "Listening", thinking: "Thinking", speaking: "", ended: "Call ended", error: "Connection problem" }[next] ?? "";
      setStatus(label, next);
      if (next === State.LISTENING) avatar.listen();
      if (next === State.THINKING) avatar.think();
    });
    session.on("assistant", (a) => a.chunks.forEach((c) => speaker.enqueue({ ...c, generation: a.generation })));
    session.on("sentence", (s) => speaker.enqueue({ text: s.text, emotion: s.emotion, gesture: s.gesture, generation: s.generation }));
    session.on("final", (f) => {
      // Sentences were already queued as they streamed. Only when none streamed (e.g. a fallback
      // answer assembled server-side) do we speak the final text's delivery chunks.
      if (!f.streamed) (f.chunks?.length ? f.chunks : [{ text: f.text }]).forEach((c) => speaker.enqueue({ ...c, generation: f.generation }));
      else if (!speaker.playing && !speaker.queue.length) session.doneSpeaking(f.generation);
    });
    session.on("backchannel", (b) => {
      avatar.backchannel();
      // Spoken at once ("haan ji", "acha" are warm on the server) so the customer hears a reaction while
      // the model is still writing; the answer's sentences queue behind it.
      if (b?.text) speaker.enqueue({ text: b.text, emotion: "neutral", gesture: "head_wobble", generation: b.generation });
    });
    // The session owns the generation counter: the Speaker adopts it, never bumps its own.
    session.on("barge-in", ({ generation }) => { speaker.cut(generation); avatar.cut(); mic.setSpeaking(false); });
    session.on("error", (e) => bridge.send({ type: "aw:error", message: String(e?.message || e) }));

    const mine = session; // end() may null `session` while we await below (host closed mid-connect)
    try {
      // Mount *in parallel* with the call. The greeting is audio-first: waiting for the live avatar's
      // first frame (Simli: 1-3 s measured) before even opening the session put all of that in front of
      // the customer's first words. The puppet portrait is painted from the first frame; when the live
      // renderer takes over, the host is told with `aw:mode` (a late, additive event).
      const mounted = avatar.mount(ui.video, ui.img).then(
        (mode) => mode,
        (e) => { bridge.send({ type: "aw:error", message: "avatar: " + String(e?.message || e) }); return null; },
      );
      const res = await mine.start();
      if (!res || session !== mine) { await mounted; return; }
      ui.name.textContent = res.persona?.name || res.persona?.display_name || cfg.name || "Neha";
      ui.sub.textContent = res.persona?.company || cfg.company || "";
      bridge.send({ type: "aw:ready", mode: avatar.mode, persona: res.persona?.id });
      mounted.then((mode) => {
        if (mode && mode !== "puppet" && session === mine) bridge.send({ type: "aw:mode", mode });
      });
      holdWake();
      try {
        await mic.start();
        if (session !== mine) mic.stop();
        else if (mic.active) { micWanted = true; ui.micBtn.dataset.on = "1"; }
        else { micWanted = false; ui.micBtn.dataset.on = "0"; } // no recorder / recogniser: do not show a live mic
      } catch (e) { ui.micBtn.dataset.on = "0"; bridge.send({ type: "aw:error", message: "mic permission: " + e.message }); }
    } catch (e) {
      if (session !== mine) return; // already torn down by end(); its error is not ours to report
      bridge.send({ type: "aw:error", message: String(e?.message || e) });
      await end(false); // leave the panel in a state where "Call again" / a reload works
    } finally {
      if (session === mine) starting = false; // end() already cleared it when it tore this attempt down
    }
  }

  async function end(notifyHost = true) {
    micWanted = false; starting = false;
    mic.stop(); mic.setSpeaking(false); speaker.cut(0); avatar.destroy(); releaseWake();
    ui.micBtn.dataset.on = "0";
    if (session && session.state !== State.ENDED) {
      const s = session;
      const id = await s.end();
      if (id) bridge.send({ type: "aw:lead", session_id: id, turns: s.transcript.length }); // nothing to report for a call that never opened
    }
    session = null;
    transport.close?.(); // abort an in-flight SSE stream / close the WS turn
    if (notifyHost) bridge.send({ type: "aw:close" });
  }

  // Audio/mic listeners are registered once; they consult the current session (null after end()).
  speaker.on("start", (item) => {
    mic.setSpeaking(true); // the gate rises while our own voice can leak back through the speakers
    if (!session || session.state === State.ENDED) return;
    avatar.speak(item);
    setStatus(ui.name.textContent + " is speaking", "speaking");
  });
  speaker.on("end", () => { if (!speaker.playing && !speaker.queue.length) mic.setSpeaking(false); });
  speaker.on("idle", ({ generation }) => { mic.setSpeaking(false); session?.doneSpeaking(generation); });
  mic.on("utterance", ({ text }) => session?.say(text));
  mic.on("speech-start", () => {
    // The utterance that follows will be session.generation + 1 (say() bumps it); adopt that now so
    // the rest of the interrupted answer is dropped but the coming reply is not.
    if (session && (session.state === State.SPEAKING || session.state === State.GREETING)) {
      speaker.cut(session.generation + 1); avatar.cut(); mic.setSpeaking(false);
    }
  });
  mic.on("level", (l) => ui.level.style.setProperty("--level", Math.min(1, l * 12).toFixed(2)));
  mic.on("error", (e) => bridge.send({ type: "aw:error", message: "mic: " + e.message }));
  // The mic can stop on its own (a fatal recogniser error, e.g. iOS has none): the button must follow
  // the hardware, not stay blue over a dead microphone. `micWanted` is the customer's choice and is
  // deliberately left alone, so a hidden tab still takes the mic back when it returns.
  mic.on("stopped", () => { ui.micBtn.dataset.on = "0"; });

  ui.micBtn.addEventListener("click", async () => {
    if (micStarting) return; // a tap while getUserMedia is opening must not cancel its own start
    if (mic.active) { micWanted = false; mic.stop(); ui.micBtn.dataset.on = "0"; return; }
    micWanted = true;
    ui.micBtn.dataset.busy = "1";
    micStarting = mic.start();
    try {
      await micStarting;
      if (!mic.active) throw new Error("microphone unavailable");
      ui.micBtn.dataset.on = "1";
    } catch (e) {
      // getUserMedia can take a second on a cold device; without this the button used to stay on
      // (and a second tap cancelled the start, which looked like "the mic will not turn on again").
      micWanted = false; ui.micBtn.dataset.on = "0";
      bridge.send({ type: "aw:error", message: "mic: " + (e?.message || e) });
    } finally { micStarting = null; ui.micBtn.dataset.busy = "0"; }
  });
  ui.endBtn.addEventListener("click", () => end(true));
  window.addEventListener("pagehide", () => end(false));

  // Background tab: release the mic (the OS keeps the indicator on otherwise) and take it back when
  // the tab returns; the wake lock is released by the browser on hide and must be re-requested.
  document.addEventListener("visibilitychange", async () => {
    const live = session && session.state !== State.ENDED;
    if (document.hidden) {
      if (mic.active) { mic.stop(); ui.micBtn.dataset.on = "0"; }
      return;
    }
    if (!live) return;
    holdWake();
    if (micWanted && !mic.active) {
      try {
        await mic.start();
        ui.micBtn.dataset.on = mic.active ? "1" : "0";
      } catch (e) { ui.micBtn.dataset.on = "0"; bridge.send({ type: "aw:error", message: "mic: " + e.message }); }
    }
  });

  return { start, end, unlock: () => speaker.unlock(), get session() { return session; } };
}

/**
 * postMessage bridge with origin pinning. In an iframe the host origin comes from the loader's
 * `?host=` query or its first aw:ctx. Nothing is posted to "*": messages produced before the origin
 * is known wait in a short queue and go out once the first aw:ctx pins it.
 */
function makeBridge(hostOrigin) {
  let origin = hostOrigin || "";
  const handlers = new Set();
  const pending = [];
  const embedded = window.parent !== window;
  const flush = () => { while (pending.length) window.parent.postMessage(pending.shift(), origin); };
  window.addEventListener("message", (e) => {
    if (!e.data || typeof e.data.type !== "string" || !e.data.type.startsWith("aw:")) return;
    if (!origin && e.data.type === "aw:ctx") { origin = e.origin; flush(); } // first contact pins the host
    if (origin && e.origin !== origin) return;
    handlers.forEach((h) => h(e.data));
  });
  return {
    on: (h) => handlers.add(h),
    send: (msg) => {
      if (!embedded) return;
      if (!origin) { pending.push(msg); if (pending.length > 32) pending.shift(); return; }
      window.parent.postMessage(msg, origin);
    },
  };
}
