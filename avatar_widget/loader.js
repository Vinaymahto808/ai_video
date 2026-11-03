/*!
 * avatar_widget loader — one script tag on any dealer page.
 *
 *   <script src="https://WIDGET-HOST/loader.js"
 *           data-api="https://BOT-HOST"            human_bot origin (default: same origin as this file)
 *           data-persona="auto-car-repair"          persona id (optional; server default otherwise)
 *           data-label="Talk to Neha" data-accent="#0b2f6b" data-ring="#2f8bff"
 *           data-position="right" data-teaser="7000" defer></script>
 *
 * Optional: data-ws="ws://HOST:4101" (use the WebSocket shim instead of SSE), data-voice, data-lang.
 *
 * Launcher lives in a closed shadow root (host CSS cannot touch it); the call UI is panel.html in an
 * iframe (our CSS cannot leak out). Public API: window.AvatarWidget.{open,close,toggle,context,on}.
 * Only page URL/title/model are sent as context; no cookies, forms or DOM scraping.
 */
(function () {
  "use strict";
  // Bump on every release: appended as ?v= to panel.html, which passes it on to src/*.js, so a CDN
  // or browser cache never mixes an old panel with new modules. (Classic script: no `export`.)
  var VERSION = "2026.11.03b";
  if (window.__avatarWidgetLoaded) return;
  window.__avatarWidgetLoaded = true;

  var tag = document.currentScript || (function () { var s = document.getElementsByTagName("script"); return s[s.length - 1]; })();
  var attr = function (n, d) { return (tag && tag.getAttribute("data-" + n)) || d; };
  var WIDGET_BASE = tag && tag.src ? tag.src.replace(/\/loader\.js.*$/, "") : "";
  if (!WIDGET_BASE) return;

  /* HTML-escape anything that came from a data-* attribute before it goes into innerHTML. */
  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  /* Colours end up in a style sheet, so only accept real colour syntax. */
  function color(v, d) {
    v = String(v || "").trim();
    return /^(#[0-9a-f]{3,8}|rgba?\([0-9.,%\s]+\)|hsla?\([0-9.,%\s]+\)|[a-z]{3,20})$/i.test(v) ? v : d;
  }
  function sget(k) { try { return sessionStorage.getItem(k); } catch (e) { return null; } }
  function sset(k, v) { try { sessionStorage.setItem(k, v); } catch (e) { /* private mode / blocked storage */ } }

  var API = attr("api", WIDGET_BASE);
  var PERSONA = attr("persona", "");
  var WS = attr("ws", "");
  var VOICE = attr("voice", "");
  var LANG = attr("lang", "");
  var LABEL = attr("label", "Talk to Neha");
  var ACCENT = color(attr("accent", "#0b2f6b"), "#0b2f6b");
  var RING = color(attr("ring", "#2f8bff"), "#2f8bff");
  var SIDE = attr("position", "right") === "left" ? "left" : "right";
  var TEASER_MS = parseInt(attr("teaser", "7000"), 10);
  if (!isFinite(TEASER_MS) || TEASER_MS < 0) TEASER_MS = 7000;
  var PANEL_ORIGIN = (function () { try { return new URL(WIDGET_BASE).origin; } catch (e) { return location.origin; } })();

  /* ---- page context: only what the greeting needs ---- */
  function pageContext() {
    var slug = (location.pathname || "").replace(/^\/+|\/+$/g, "").split("/").pop() || "";
    var model = slug.replace(/[-_]+/g, " ").replace(/\b\w/g, function (c) { return c.toUpperCase(); });
    if (/^(index|home)?$/i.test(model)) model = "";
    // origin + path only: query strings and fragments can carry tokens, emails, UTM ids
    return { page: { url: location.origin + location.pathname, title: document.title, model: model, source: "website_widget" }, visitor: visitorId() };
  }
  /* A long-lived random id in this browser so the assistant recognises a returning customer and picks the
     earlier conversation back up. No personal data; cleared with site data. */
  function visitorId() {
    try {
      var v = localStorage.getItem("aw_visitor");
      if (!v || !/^[A-Za-z0-9_-]{8,64}$/.test(v)) {
        var bytes = new Uint8Array(16); (window.crypto || window.msCrypto).getRandomValues(bytes);
        v = ""; for (var i = 0; i < bytes.length; i++) v += "abcdefghijklmnopqrstuvwxyz0123456789"[bytes[i] % 36];
        localStorage.setItem("aw_visitor", v);
      }
      return v;
    } catch (e) { return ""; }
  }
  var ctx = pageContext();

  /* ---- shadow-root launcher ---- */
  var host = document.createElement("div");
  host.setAttribute("data-avatar-widget", "");
  var root = host.attachShadow({ mode: "closed" });
  root.innerHTML =
    '<style>' +
    ':host{all:initial;position:fixed;' + SIDE + ':20px;bottom:20px;z-index:2147483000;font:14px system-ui,sans-serif}' +
    '.btn{all:unset;cursor:pointer;display:flex;align-items:center;gap:10px;background:#fff;color:#111;border-radius:999px;padding:6px 14px 6px 6px;box-shadow:0 10px 30px rgba(0,0,0,.25);transition:transform .15s}' +
    '.btn:hover{transform:translateY(-2px)}' +
    '.ava{width:46px;height:46px;border-radius:50%;background:#ddd center/cover no-repeat;box-shadow:0 0 0 3px ' + RING + ';animation:pulse 2.4s infinite}' +
    '@keyframes pulse{0%,100%{box-shadow:0 0 0 3px ' + RING + '}50%{box-shadow:0 0 0 7px rgba(47,139,255,.25)}}' +
    '.lbl{font-weight:600;white-space:nowrap}' +
    '.teaser{position:absolute;bottom:64px;' + SIDE + ':0;background:#fff;color:#111;border-radius:12px;padding:10px 12px;box-shadow:0 10px 30px rgba(0,0,0,.2);max-width:240px;display:none}' +
    '.panel{position:fixed;' + SIDE + ':20px;bottom:88px;width:360px;height:560px;max-height:calc(100vh - 110px);max-height:calc(100dvh - 110px);border-radius:18px;overflow:hidden;box-shadow:0 20px 60px rgba(0,0,0,.35);display:none;background:#0e1116}' +
    '.panel iframe{width:100%;height:100%;border:0;display:block}' +
    '@media (max-width:520px){.panel{' + SIDE + ':0;left:0;bottom:0;width:100%;height:78vh;height:78dvh;max-height:none;border-radius:18px 18px 0 0}}' +
    '</style>' +
    '<div class="teaser" part="teaser">Hi! Koi sawaal ho toh poochhiye — I can help with price, booking and offers.</div>' +
    '<button class="btn" part="launcher" aria-haspopup="dialog" aria-expanded="false"><span class="ava"></span><span class="lbl">' + esc(LABEL) + '</span></button>' +
    '<div class="panel" part="panel" role="dialog" aria-label="' + esc(LABEL) + '"></div>';
  var btn = root.querySelector(".btn"), panel = root.querySelector(".panel"), teaser = root.querySelector(".teaser");
  var frame = null, open = false, listeners = {};

  // Set through the CSSOM, not an inline style string, so the URL cannot break out of the attribute.
  try { root.querySelector(".ava").style.backgroundImage = 'url("' + API.replace(/"/g, "%22") + '/avatar.jpg")'; } catch (e) { /* ignore */ }

  function emit(type, d) { (listeners[type] || []).forEach(function (f) { try { f(d); } catch (e) {} }); }
  function panelUrl() {
    var q = new URLSearchParams({ api: API, persona: PERSONA, accent: ACCENT, ring: RING, host: location.origin, v: VERSION });
    if (WS) q.set("ws", WS);
    if (VOICE) q.set("voice", VOICE);
    if (LANG) q.set("lang", LANG);
    return WIDGET_BASE + "/panel.html?" + q.toString();
  }
  function post(msg) { if (frame && frame.contentWindow) frame.contentWindow.postMessage(msg, PANEL_ORIGIN); }
  function show() {
    if (open) return;
    open = true; teaser.style.display = "none"; panel.style.display = "block"; btn.setAttribute("aria-expanded", "true");
    if (!frame) {
      frame = document.createElement("iframe");
      frame.allow = "microphone; autoplay; camera 'none'";
      frame.title = LABEL;
      frame.src = panelUrl();
      frame.addEventListener("load", function () { post({ type: "aw:ctx", ctx: ctx }); post({ type: "aw:open" }); });
      panel.appendChild(frame);
    } else { post({ type: "aw:ctx", ctx: ctx }); post({ type: "aw:open" }); }
    emit("open");
  }
  function hide() {
    if (!open) return;
    open = false; panel.style.display = "none"; btn.setAttribute("aria-expanded", "false");
    post({ type: "aw:close" });
    emit("close");
  }
  btn.addEventListener("click", function () { open ? hide() : show(); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && open) hide(); });
  window.addEventListener("message", function (e) {
    if (e.origin !== PANEL_ORIGIN || !e.data || typeof e.data.type !== "string") return;
    var d = e.data;
    if (d.type === "aw:close") { hide(); if (frame) { frame.remove(); frame = null; } }
    if (d.type === "aw:state") emit("state", d.state);
    if (d.type === "aw:ready") emit("ready", d);
    if (d.type === "aw:lead") emit("lead", d);
    if (d.type === "aw:error") emit("error", d.message);
  });
  if (TEASER_MS > 0 && !sget("aw_teased")) {
    setTimeout(function () {
      if (open) return;
      teaser.style.display = "block"; sset("aw_teased", "1");
      setTimeout(function () { teaser.style.display = "none"; }, 8000);
    }, TEASER_MS);
  }

  function mount() { (document.body || document.documentElement).appendChild(host); }
  document.body ? mount() : document.addEventListener("DOMContentLoaded", mount);

  window.AvatarWidget = {
    version: VERSION,
    open: show, close: hide, toggle: function () { open ? hide() : show(); },
    get context() { return ctx; },
    on: function (type, fn) { (listeners[type] = listeners[type] || []).push(fn); return function () { listeners[type] = listeners[type].filter(function (f) { return f !== fn; }); }; },
  };
})();
