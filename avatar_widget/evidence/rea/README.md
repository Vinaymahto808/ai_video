# REA evidence — DaveAI (www.iamdave.ai), captured 2026-11-01

Captured with the REA CLI (`rea-agents` 6.5.0, run through `npx`/global `rea`); the REA MCP server is
not registered for Claude Code on this machine, so the CLI was used instead. Every file here is a
canonical REA Evidence record (`evidence_id`, `operation`, `normalized_result`, `limitations`).
Nothing was fetched beyond public pages; no account, no form submission, no authenticated surface.

| File | Evidence ID | Operation | What it shows |
|---|---|---|---|
| `ev_80f95376_inspect_web_page_avatar.json` | `ev_80f9537651f26775…` | `inspect_web_page` (passive, CDP, 8 s) | `/solutions/virtual-sales-avatar/` after load: 1 frame, 0 network requests, 0 WebSockets, 0 workers, 0 console events, 0 storage keys, 3 inline scripts, 68 resources (CSS/font/image only). Attach-limited: observation began after load. |
| `ev_43ee71a1_analyze_web_bundle_home.json` | `ev_43ee71a19a240f0b…` | `analyze_web_bundle` (homepage) | 3 inline scripts parsed (3 906 AST nodes): no routes, no endpoint candidates, no WebMCP declarations, no source maps. |
| `ev_ff5f25ab_scenario_no_interaction.json` | `ev_ff5f25ab1ea39c21…` | `capture_browser_scenario` (launch, fresh profile) | Avatar page → homepage → `/solutions/gryd/`, 10 s settle each, **no user input**: 230 requests, all `www.iamdave.ai`, types document 3 / image 50 / stylesheet 149 / font 27 / other 1. **Zero `<script>` requests.** |
| `ev_95d45636_scenario_with_interaction.json` | `ev_95d45636…` | `capture_browser_scenario` (launch, fresh profile) | Same pages with one click + one key press per page: 326 requests across 26 hosts, 96 scripts, 14 fetch, 14 xhr, 3 ping, 2 frames attached, 6 console events, 14 failed requests (beacons aborted at navigation). Final DOM snapshot of the homepage retained (sha256 `0d553982…`, 320 726 bytes) plus hashed cookie inventory. |
| `scenario_*.input.json` | — | scenario inputs | Exact declared actions and capture selections for the two runs. |

## Observations (all from the records above)

1. **Every script on the site is interaction-gated by WP Rocket "Delay JavaScript".**
   The served HTML carries 32 `<script type="rocketlazyloadscript" data-rocket-src=…>` tags and an inline
   `RocketLazyLoadScripts` 2.0.3 loader that waits for `keydown|mousedown|mousemove|touchstart|wheel|click|…`.
   Without input (`ev_ff5f25ab`) no JavaScript at all is requested, not even jQuery or Google Tag Manager.
2. **After the first input, the script set is WordPress + Elementor + vendor tags only.** 24 first-party
   scripts (jQuery 3.7.1, Elementor 4.2.4, Elementor Pro 3.30.1, Vamtam theme, GTranslate, the WP Rocket
   minified Facebook SDK copy) plus Google Tag Manager container `GTM-PPGSPBRH`, which then loads:
   GA4 `G-0TT9GCBXFT`, Google Ads `AW-851524655`, LinkedIn Insight (pid 5877074), Microsoft Clarity
   `jbcoujrsnr`, Leadfeeder `YEgkB8lxmdv8ep3Z`, a Customers.ai/MobileMonkey consent-timestamp stub
   (883 bytes, sets `localStorage.__cai_sts`, opens no widget), and the Facebook JS SDK (app
   `2198582457156622`). All XHR/fetch traffic is analytics collection (`/g/collect`, `/ccm/collect`,
   `n.clarity.ms/collect`, LinkedIn `/wa/`, YouTube `log_event`).
3. **The "avatar" on the solutions page is a YouTube embed.** Elementor video widgets with
   `youtube_url` `5PZxAv7lPyE` and `PS0oXjPGyTg`; the only non-tracking iframe attached is
   `https://www.youtube.com/embed/5PZxAv7lPyE?…enablejsapi=1&origin=https://www.iamdave.ai`, driven by
   `youtube.com/iframe_api`. The console warning about a `postMessage` origin mismatch comes from that
   YouTube widget API, not from a DaveAI script.
4. **No DaveAI runtime exists on the public site.** Across both scenarios: no request to any
   `*.iamdave.ai` host other than `www`, no WebSocket connection, no EventSource, no worker, no WebGL /
   `<canvas>` / `<video>` element from DaveAI, no chat launcher element in the final DOM, no DaveAI-owned
   cookie or storage key (cookies present: `_ga`, `_ga_*`, `_gcl_au`, `_clck`, `_clsk`, `_lfa`, …).
   The homepage DOM mentions WhatsApp only in copy and in the BYD "WhatsApp Chatbot" story tag.

## Re-run (2026-11-01, second execution of the same inputs)

The two scenario inputs were executed again, unchanged, into `rerun/` (same Chromium 155, headless,
fresh profile, same declared actions). Output was redirected with `cmd` rather than PowerShell so the
JSON stayed byte-faithful UTF-8.

| File | Evidence ID | Requests | Scripts | Hosts | Final DOM |
|---|---|---|---|---|---|
| `rerun/no_interaction.json` | `ev_7b801f4b9b643041…` | 230 | **0** | 1 | `207d40e7…`, 297 844 B — **byte-identical** to `ev_ff5f25ab` |
| `rerun/with_interaction.json` | `ev_72d83716de5c9c7c…` | 326 | 96 | 26 | `326bfb2d…`, 320 720 B (was `0d553982…`, 320 726 B) |

1. **Both runs reproduce the original counts exactly** — 230 / 0 scripts / 1 host and 326 / 96 / 26, the
   same resource-type split (149 stylesheet, 50 image, 27 font, 3 document, 1 other; 103 stylesheet, 96
   script, 73 image, 19 font, 14 fetch, 14 xhr, 3 document, 3 ping, 1 other), the same 14 failed beacons,
   2 attached frames and 6 console events. The "no DaveAI runtime" result is stable, not a one-off.
2. **No non-`www` `*.iamdave.ai` host appears in either run**, and no WebSocket / EventSource / worker
   event exists in any record.
3. **The only host-set difference is Microsoft Clarity's rotating collection edge** —
   `n.clarity.ms` in the first pass, `f.clarity.ms` and `a.clarity.ms` in re-runs. Same 10 requests;
   total host count stays 26.
4. **The with-input DOM changed only inside analytics URLs.** After normalising digits, the sole
   remaining difference is the variable number of `tag_exp` tokens in Google Ads URLs. No element,
   script, iframe or storage key differs between runs.
5. The re-run console adds one warning — `The powerPreference option is currently ignored when calling
   requestAdapter()` — a WebGPU adapter probe from a third-party tag; no DaveAI host is involved.

## Re-check (2026-11-03, before the feature map in `research/daveai/`)

`scenario_with_interaction.input.json` executed once more, unchanged, into `recheck_2026-11-03/`
(`rea capture-browser-scenario <file>`; the CLI takes the input as an argument, not on stdin).

| File | Evidence ID | Requests | Scripts | Hosts | WebSocket / worker | non-`www` iamdave hosts |
|---|---|---|---|---|---|---|
| `recheck_2026-11-03/with_interaction.json` | `ev_c5b1c9951866770e…` | 331 | 98 | 25 | 0 | none |

1. **First-party script set is identical** to 2026-11-01: the same 36 WordPress / Elementor Pro /
   Vamtam / jQuery files, `sdk.js` still the Facebook SDK.
2. **Only vendor rotations differ**: Microsoft Clarity `0.8.72` → `0.8.75` plus a `0.8.77-beta` copy,
   a new YouTube embed player hash (`11a45ffb` → `4efb8b2c`), a new Google `js/th` challenge script,
   and `px4.ads.linkedin.com` no longer appears (hosts 26 → 25).
3. Same 2 attached frames (YouTube), 6 console events (jQuery Migrate, Facebook SDK age, YouTube
   `postMessage` origin, WebGPU `powerPreference`), 16 failed beacons at navigation (was 14).
4. Conclusion unchanged: **no DaveAI runtime on the public site.** The feature-level reverse
   engineering therefore rests on page copy only; see `research/daveai/FEATURES.md`.

## Limitations recorded by REA

Scenario network records carry no initiator stacks; receipt order does not prove causality. Bodies
and header values were not selected. Static bundle findings are observations of captured source, not
proof of execution. Passive inspection started after page load, so its empty network section is
attach-limited, which is why the launch-mode scenarios were run.
