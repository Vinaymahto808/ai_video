# Put the bot on hanshyundai.com

One script tag. The widget is a floating launcher; the panel is our `/embed` page inside an
iframe, so the dealer site's CSS can never break it and the bot's CSS can never leak out
(shadow DOM + iframe).

```html
<!-- before </body>, on every page -->
<script src="https://BOT-HOST/widget.js"
        data-persona="hans-hyundai"
        data-label="Chat with Riya"
        data-accent="#0b2f6b"
        data-position="right" defer></script>
```

Replace `BOT-HOST` with the public HTTPS origin of the bot server (see **Hosting** below).
`http://127.0.0.1:4002` only works for local development — Chrome blocks a public HTTPS page
from loading a private-network widget.

## What it does

| Thing | Behaviour |
|---|---|
| Page context | Reads the URL slug (`/hyundai-creta` → "Hyundai Creta") and sends it as `sessionContext`, so the bot opens with "Creta ke baare mein hi baat karni hai?" |
| Brand | `data-persona` picks the persona from `/personas`; name and company in the header come from that JSON |
| Teaser | After 7 s an unopened widget shows a one-line nudge, once per tab |
| Mobile | Below 520 px the panel goes full width and full height |
| Mini | The minimize icon in the card header shrinks the card to a passport-size photo of Neha (150×190, 130×165 on phones) while the call keeps running hands-free; a tap on the photo or the restore icon brings the full card back. The card sends `hhbot:size` (`mini`/`normal`) and the launcher resizes. |
| Keyboard | `Esc` closes the panel |
| Isolation | Shadow DOM for the launcher, iframe for the app, `z-index: 2147483000` |

## JavaScript API

```js
HansHyundaiBot.open();     // open the panel
HansHyundaiBot.close();    // close it
HansHyundaiBot.toggle();
HansHyundaiBot.context;    // what was sent to the bot ({page:{url,title,model}})
```

Use it from your own CTAs:

```html
<button onclick="HansHyundaiBot.open()">Talk to a consultant</button>
```

## Data sent to the bot

```json
{"page": {"url": "https://www.hanshyundai.com/hyundai-creta",
          "title": "Hyundai Creta On Road Price in New Delhi | Creta 2026",
          "model": "Hyundai Creta",
          "source": "website_widget"}}
```

Nothing else is read from the page — no form fields, no cookies, no DOM scraping.

## Hosting

The bot server already sends `Content-Security-Policy: frame-ancestors *` and
`Cross-Origin-Resource-Policy: cross-origin`, so any dealer domain can embed it. It needs a
public **HTTPS** origin, because:

- `https://hanshyundai.com` + `http://.../widget.js` = mixed content, blocked by the browser;
- public site → `127.0.0.1` = blocked by Chrome's Local Network Access checks.

Three ways to get HTTPS:

1. **Reverse proxy on the same box** — Caddy or nginx in front of `127.0.0.1:4002`, e.g.
   `bot.hanshyundai.com { reverse_proxy 127.0.0.1:4002 }` (Caddy gets the cert automatically).
2. **Cloudflare Tunnel** — `cloudflared tunnel --url http://127.0.0.1:4002` gives a temporary
   `https://*.trycloudflare.com` origin. Good for a demo, no account needed.
3. **Any PaaS** — Docker/Fly/Render; the app is stdlib Python, `python human_bot/server/app.py --port $PORT`.

Then the tag becomes:

```html
<script src="https://bot.hanshyundai.com/widget.js" data-persona="hans-hyundai" defer></script>
```

If their CMS or WAF sets a `Content-Security-Policy` with `script-src`, allow-list that
origin (`script-src 'self' https://bot.hanshyundai.com`) and `frame-src` the same.

## Before it faces customers

- Replace the indicative prices in `persona/persona_hans_hyundai.json` → `offline.models`
  with the live Hans Hyundai price sheet; the prompt quotes those strings verbatim.
- The bot already discloses "AI sales consultant" in the panel and hands off when the
  customer asks for a human. Check that against Hyundai India's brand/dealer marketing rules.
- Voice input needs mic permission; on browsers without `SpeechRecognition` the customer
  types instead, and the bot still speaks.

## Previews

Real page (`https://www.hanshyundai.com/hyundai-creta`), widget injected locally, Chrome at
1440×900: `research/previews/01-launcher-on-site.png` … `07-mobile.png`.
