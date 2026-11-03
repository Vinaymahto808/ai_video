/*!
 * Human Bot — embeddable video sales widget.
 * Drop this one line before </body>:
 *   <script src="http://127.0.0.1:4002/widget.js" data-persona="hans-hyundai" defer></script>
 *
 * Behaviour and assets are original to this project. No third-party avatar art,
 * voice samples or prompt text is copied.
 */
(function () {
  "use strict";
  var tag = document.currentScript || (function () {
    var all = document.getElementsByTagName("script");
    return all[all.length - 1];
  })();
  var CFG = window.__HHBOT_CONFIG || {};
  var BASE = CFG.base || (tag && tag.src ? tag.src.replace(/\/widget\.js.*$/, "") : "");
  if (!BASE) return;
  /* the card's origin: postMessage is sent to it and only accepted from it */
  var ORIGIN = "";
  try { ORIGIN = new URL(BASE, location.href).origin; } catch (e) {}
  if (!ORIGIN || ORIGIN === "null") return;

  /* data-* attributes come from the host page's markup: text is escaped before it goes into innerHTML and
     colours must be a hex value or a plain CSS colour name (no url(), no ;, no quotes) */
  function escHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; });
  }
  function safeColor(v, dflt) {
    v = String(v || "").trim();
    return (/^#[0-9a-f]{3,8}$/i.test(v) || /^[a-z]{3,20}$/i.test(v)) ? v : dflt;
  }
  var PERSONA = CFG.persona || (tag && tag.getAttribute("data-persona")) || "";
  var LABEL = escHtml((tag && tag.getAttribute("data-label")) || "Talk to sales");
  var ACCENT = safeColor(tag && tag.getAttribute("data-accent"), "#0b2f6b");
  var SIDE = ((tag && tag.getAttribute("data-position")) || "right").toLowerCase() === "left" ? "left" : "right";
  /* "bubble" (default): round photo with a glowing ring, like a live video presence.
     "pill": the older photo + text capsule. */
  var LAUNCHER = ((tag && tag.getAttribute("data-launcher")) || "bubble").toLowerCase() === "pill" ? "pill" : "bubble";
  var RING = safeColor(tag && tag.getAttribute("data-ring"), "#2f8bff");
  if (window.__hhBotLoaded) return;
  window.__hhBotLoaded = true;

  /* page context: which model page is the visitor on? */
  var MODELS = {
    "hyundai-creta": "Hyundai Creta", "hyundai-creta-n-line": "Creta N Line",
    "hyundai-creta-electric": "Creta Electric", "hyundai-venue": "Hyundai Venue",
    "hyundai-venue-n-line": "Venue N Line", "hyundai-exter": "Hyundai Exter",
    "hyundai-verna": "Hyundai Verna", "hyundai-alcazar": "Hyundai Alcazar",
    "hyundai-aura": "Hyundai Aura", "hyundai-grand-i10-nios": "Grand i10 NIOS",
    "hyundai-all-new-i20": "Hyundai i20", "hyundai-i20-n-line": "i20 N Line"
  };
  function pageContext() {
    var path = (location.pathname || "").replace(/^\/+|\/+$/g, "").toLowerCase();
    var slug = path.split("/").pop() || "";
    var model = MODELS[slug] || MODELS["hyundai-" + slug] || "";
    if (!model) {
      var t = (document.title || "").toLowerCase();
      for (var k in MODELS) {
        var short = MODELS[k].replace(/^hyundai\s+/i, "").toLowerCase();
        if (short && t.indexOf(short) !== -1) { model = MODELS[k]; break; }
      }
    }
    return {
      page: {
        url: location.href,
        title: document.title,
        model: model,
        source: "website_widget"
      }
    };
  }
  var ctx = pageContext();

  var host = document.createElement("div");
  host.id = "hh-human-bot";
  host.style.cssText = "position:fixed;z-index:2147483000;inset:auto 0 0 auto;width:0;height:0;";
  document.body.appendChild(host);
  var root = host.attachShadow ? host.attachShadow({ mode: "open" }) : host;

  var style = document.createElement("style");
  style.textContent = [
    ":host{all:initial}",
    "*{box-sizing:border-box;font-family:'Segoe UI',Inter,system-ui,-apple-system,sans-serif}",
    ".wrap{position:fixed;bottom:20px;" + SIDE + ":20px;display:flex;flex-direction:column;align-items:flex-" + SIDE + ";gap:12px}",
    /* portrait video card: the iframe draws its own header/controls, so no bar here */
    ".panel{width:320px;height:min(480px,calc(100vh - 60px));height:min(480px,calc(100dvh - 60px));background:#000;border-radius:18px;overflow:hidden;",
    "  box-shadow:0 30px 80px rgba(8,18,34,.45),0 0 0 1px rgba(255,255,255,.06);display:none;flex-direction:column;",
    "  transform:translateX(24px) scale(.98);opacity:0;transition:transform .22s cubic-bezier(.2,.8,.2,1),opacity .18s ease,width .25s ease,height .25s ease}",
    ".panel.open{display:flex;transform:none;opacity:1}",
    /* mini: the card's minimize icon turns it into a passport-size photo of Neha; the call keeps going.
       Size follows the screen: ~12% of the viewport width, never smaller than 120 px or larger than 170 px. */
    ".panel.mini{width:clamp(120px,12vw,170px);height:auto;aspect-ratio:150/190;border-radius:16px}",
    /* tablets and small laptops: a little wider card, bounded by the viewport height */
    "@media(min-width:521px) and (max-width:1024px){.panel{width:min(340px,calc(100vw - 40px));height:min(540px,calc(100dvh - 48px))}}",
    /* large screens: the card may use more height, the width stays readable */
    "@media(min-width:1440px){.panel{width:360px;height:min(560px,calc(100dvh - 60px))}}",
    /* landscape phones (short viewport): full height, keep the photo small */
    "@media(max-height:500px) and (max-width:1024px){.wrap{bottom:8px}.panel{height:calc(100dvh - 16px);max-height:calc(100dvh - 16px)}.panel.mini{width:110px}}",
    ".panel .bar{display:none}",
    ".bar{display:flex;align-items:center;gap:8px;padding:8px 10px 8px 14px;background:" + ACCENT + ";color:#fff;font-size:12px;font-weight:600;letter-spacing:.2px}",
    ".bar .dot{width:7px;height:7px;border-radius:50%;background:#57e0a5;box-shadow:0 0 0 3px rgba(87,224,165,.22)}",
    ".bar .x{margin-left:auto;background:rgba(255,255,255,.14);border:0;color:#fff;width:26px;height:26px;",
    "  border-radius:8px;cursor:pointer;font-size:14px;line-height:1}",
    ".bar .x:hover{background:rgba(255,255,255,.26)}",
    "iframe{flex:1;width:100%;border:0;display:block;background:#fff}",
    ".launcher{display:flex;align-items:center;gap:10px;background:#fff;border:1px solid #e3e8ef;border-radius:999px;",
    "  padding:6px 13px 6px 6px;cursor:pointer;box-shadow:0 10px 30px rgba(8,18,34,.18);transition:transform .15s ease,box-shadow .15s ease}",
    ".launcher:hover{transform:translateY(-1px);box-shadow:0 14px 34px rgba(8,18,34,.24)}",
    ".launcher .pic{width:36px;height:36px;border-radius:50%;object-fit:cover;background:#0b1522;display:block}",
    ".launcher .txt{display:flex;flex-direction:column;align-items:flex-start;line-height:1.15}",
    ".launcher .txt b{font-size:12px;color:#0d1520;font-weight:650}",
    ".launcher .txt span{font-size:10.5px;color:#66748a}",
    ".launcher .pulse{width:8px;height:8px;border-radius:50%;background:#15b77a;margin-left:2px;",
    "  box-shadow:0 0 0 0 rgba(21,183,122,.5);animation:p 2.2s infinite}",
    "@keyframes p{0%{box-shadow:0 0 0 0 rgba(21,183,122,.55)}70%{box-shadow:0 0 0 9px rgba(21,183,122,0)}100%{box-shadow:0 0 0 0 rgba(21,183,122,0)}}",
    ".teaser{max-width:230px;background:#fff;border:1px solid #e3e8ef;border-radius:12px;border-bottom-" + SIDE + "-radius:5px;",
    "  padding:9px 12px;font-size:12px;color:#28323f;box-shadow:0 10px 26px rgba(8,18,34,.14);display:none;position:relative}",
    ".teaser.show{display:block}",
    ".teaser b{color:" + ACCENT + "}",
    ".teaser .close{position:absolute;top:4px;right:6px;border:0;background:none;color:#95a1b1;cursor:pointer;font-size:13px}",
    /* bubble launcher: dark disc, photo, glowing ring that slowly rotates, live dot, name tag on hover */
    ".bubble{position:relative;width:68px;height:68px;border:0;padding:0;background:none;cursor:pointer;outline:none}",
    ".bubble .ring{position:absolute;inset:0;border-radius:50%;padding:3px;",
    "  background:conic-gradient(from 0deg," + RING + " 0 62%,rgba(47,139,255,.15) 62% 78%," + RING + " 78% 100%);",
    "  -webkit-mask:radial-gradient(farthest-side,transparent calc(100% - 3.5px),#000 calc(100% - 3px));mask:radial-gradient(farthest-side,transparent calc(100% - 3.5px),#000 calc(100% - 3px));",
    "  animation:spin 6s linear infinite}",
    ".bubble .glow{position:absolute;inset:-6px;border-radius:50%;box-shadow:0 0 0 0 rgba(47,139,255,.45),0 12px 34px rgba(8,18,34,.35);animation:breathe 2.6s ease-out infinite}",
    ".bubble .disc{position:absolute;inset:5px;border-radius:50%;background:#0b1522;overflow:hidden;box-shadow:inset 0 0 0 1px rgba(255,255,255,.08)}",
    ".bubble .disc img{width:100%;height:100%;object-fit:cover;object-position:50% 18%;display:block}",
    ".bubble .orbit{position:absolute;inset:0;border-radius:50%;pointer-events:none;animation:orbit 6s linear infinite}",
    ".bubble .live{position:absolute;top:-7px;left:50%;margin-left:-8px;width:16px;height:16px;border-radius:50%;background:#15b77a;border:3px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.3)}",
    ".bubble .tag{position:absolute;bottom:50%;" + (SIDE === "right" ? "right" : "left") + ":calc(100% + 12px);transform:translateY(50%);white-space:nowrap;",
    "  background:#0d1520;color:#fff;font-size:12px;font-weight:600;padding:7px 11px;border-radius:9px;opacity:0;pointer-events:none;transition:opacity .15s ease;box-shadow:0 8px 20px rgba(8,18,34,.25)}",
    ".bubble .tag:after{content:'';position:absolute;top:50%;" + (SIDE === "right" ? "right:-5px" : "left:-5px") + ";width:10px;height:10px;background:#0d1520;transform:translateY(-50%) rotate(45deg)}",
    ".bubble:hover .tag,.bubble:focus-visible .tag{opacity:1}",
    ".bubble:hover .glow{box-shadow:0 0 0 7px rgba(47,139,255,.18),0 16px 40px rgba(8,18,34,.4)}",
    "@keyframes spin{to{transform:rotate(360deg)}}",
    "@keyframes orbit{from{transform:rotate(135deg)}to{transform:rotate(495deg)}}",
    "@keyframes breathe{0%{box-shadow:0 0 0 0 rgba(47,139,255,.45),0 12px 34px rgba(8,18,34,.35)}70%{box-shadow:0 0 0 14px rgba(47,139,255,0),0 12px 34px rgba(8,18,34,.35)}100%{box-shadow:0 0 0 0 rgba(47,139,255,0),0 12px 34px rgba(8,18,34,.35)}}",
    "@media(max-width:520px){.wrap{" + SIDE + ":10px;bottom:12px;width:calc(100vw - 20px)}.panel{width:100%;height:calc(100dvh - 24px);max-height:calc(100dvh - 24px);border-radius:18px}",
    "  .panel.mini{width:clamp(110px,32vw,140px);height:auto;max-height:none;aspect-ratio:150/190;border-radius:14px;margin-" + (SIDE === "right" ? "left" : "right") + ":auto}",
    "  .launcher{padding:5px 11px 5px 5px}.launcher .pic{width:34px;height:34px}.bubble{width:62px;height:62px}}"
  ].join("\n");

  var wrap = document.createElement("div");
  wrap.className = "wrap";
  wrap.innerHTML =
    '<div class="teaser" id="teaser"><button class="close" aria-label="dismiss">✕</button>' +
    '<b id="tname">Hi!</b> <span id="ttext">Creta ke offers ya test drive ke liye baat karein?</span></div>' +
    '<div class="panel" id="panel"><div class="bar"><span class="dot"></span>' +
    '<span id="barname">Sales</span><button class="x" id="close" aria-label="close">✕</button></div>' +
    '<iframe id="frame" title="Sales consultant" allow="microphone; autoplay; clipboard-write"></iframe></div>' +
    (LAUNCHER === "bubble"
      ? '<button class="bubble" id="open" aria-label="' + LABEL + '">' +
        '<span class="glow"></span><span class="ring"></span>' +
        '<span class="disc"><img src="' + BASE + '/avatar.jpg" alt=""></span>' +
        '<span class="orbit" aria-hidden="true"><span class="live"></span></span>' +
        '<span class="tag" id="lname">' + LABEL + '</span></button>'
      : '<button class="launcher" id="open" aria-label="' + LABEL + '">' +
        '<img class="pic" src="' + BASE + '/avatar.jpg" alt="">' +
        '<span class="txt"><b id="lname">' + LABEL + '</b><span>Video · replies in seconds</span></span>' +
        '<i class="pulse"></i></button>');

  root.appendChild(style);
  root.appendChild(wrap);

  var panel = wrap.querySelector("#panel");
  var frame = wrap.querySelector("#frame");
  var teaser = wrap.querySelector("#teaser");
  var loaded = false;
  var open = false;

  function frameUrl() {
    var p = new URLSearchParams();
    if (PERSONA) p.set("persona", PERSONA);
    p.set("accent", ACCENT);
    p.set("ctx", JSON.stringify(ctx));
    p.set("autostart", "1");   // the click on the launcher is the user gesture: the card greets at once, no start button
    p.set("host", location.origin);   // the card pins its postMessage traffic to this page's origin
    /* data-clips="neha" (default) | "sample" — which reaction-clip set the card plays */
    var clips = tag && tag.getAttribute("data-clips");
    if (clips) p.set("clips", clips);
    return BASE + "/embed?" + p.toString();
  }

  function toFrame(msg) {
    try { frame.contentWindow.postMessage(msg, ORIGIN); } catch (e) {}
  }
  function show() {
    open = true;
    if (!loaded) {
      frame.src = frameUrl();
      loaded = true;
      frame.addEventListener("load", function () { toFrame({ type: "hhbot:ctx", ctx: ctx }); });
    } else {
      toFrame({ type: "hhbot:open" });   // re-opened: the card re-arms its mic and idle motion
    }
    panel.classList.add("open");
    panel.classList.remove("mini");   // the launcher always opens the full card
    teaser.classList.remove("show");
    wrap.querySelector("#open").style.display = "none";
    try { frame.contentWindow.focus(); } catch (e) {}
    try {
      var b = JSON.parse(sessionStorage.getItem("hhbot_seen") || "{}");
      b.open = 1; sessionStorage.setItem("hhbot_seen", JSON.stringify(b));
    } catch (e) {}
  }
  function hide() {
    open = false;
    panel.classList.remove("open");
    wrap.querySelector("#open").style.display = LAUNCHER === "bubble" ? "block" : "flex";
    if (loaded) toFrame({ type: "hhbot:close" });   // hidden card: mic off, speech cut
  }
  function toggle() { open ? hide() : show(); }

  wrap.querySelector("#open").addEventListener("click", show);
  wrap.querySelector("#close").addEventListener("click", hide);
  teaser.querySelector(".close").addEventListener("click", function (e) {
    e.stopPropagation(); teaser.classList.remove("show");
  });
  teaser.addEventListener("click", show);
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && open) hide(); });
  window.addEventListener("message", function (e) {
    if (e.origin !== ORIGIN || e.source !== frame.contentWindow) return;   // only our own card may collapse the panel
    var d = e.data || {};
    if (d.type === "hhbot:close") hide();
    if (d.type === "hhbot:size") panel.classList.toggle("mini", d.size === "mini");
  });

  /* name + teaser copy come from the persona registry, so the widget stays brand-correct */
  fetch(BASE + "/personas").then(function (r) { return r.json(); }).then(function (d) {
    var p = (d.personas || []).filter(function (x) { return x.id === (PERSONA || d.default); })[0];
    if (!p) return;
    wrap.querySelector("#lname").textContent = "Chat with " + p.name;
    wrap.querySelector("#barname").textContent = p.name + " · " + p.company;
    wrap.querySelector("#tname").textContent = p.name + ":";
    var m = ctx.page && ctx.page.model;
    var service = p.business_type === "service";
    wrap.querySelector("#ttext").textContent = service
      ? (m ? m + " ki service ya repair ke liye baat karein? Free pickup available hai."
           : "Car service ya repair chahiye? Main quote aur pickup arrange kar deti hoon.")
      : (m ? m + " ke price, offers ya test drive ke liye baat karein?"
           : "Kaunsi car dekh rahe hain? Main help kar deti hoon.");
  }).catch(function () {});

  setTimeout(function () {
    if (open) return;
    try { if ((JSON.parse(sessionStorage.getItem("hhbot_seen") || "{}").open)) return; } catch (e) {}
    teaser.classList.add("show");
  }, 7000);

  window.HansHyundaiBot = { open: show, close: hide, toggle: toggle, context: ctx };
})();
