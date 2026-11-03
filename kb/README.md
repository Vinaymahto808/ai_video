# kb — website crawler + RAG knowledge base

Crawls a dealer website, turns pages into clean Markdown, chunks and embeds them, and serves a
semantic search API that `human_bot/` injects into its prompt. Multi-brand by design; one website
is live today: **Auto Car Repair** (`auto_car_repair`, autocarrepair.in).

```
kb/
  src/
    config.js          env + website registry (auto_car_repair, galaxy_toyota, hans_hyundai)
    crawler/urls.js    URL normalisation, allow/deny rules, page-type classifier
    crawler/robots.js  robots.txt (allow/disallow, crawl-delay, Sitemap:)
    crawler/sitemap.js sitemap + sitemap-index discovery
    crawler/crawl.js   Crawlee PlaywrightCrawler (SPA rendering, link discovery, retries)
    extract/extract.js rendered HTML -> Markdown + metadata (boilerplate removal, tables kept)
    chunk/chunk.js     heading-aware chunking, 1200/200 default, tables kept whole
    embed/gemini.js    gemini-embedding-001, batched, retried, dimension-checked, L2-normalised
    store/sqlite.js    node:sqlite store: websites / pages / chunks(embedding BLOB) / crawl_jobs
    pipeline.js        page -> hash compare -> chunk -> embed -> transactional replace
    jobs.js            one job at a time: crawl / refresh / reindex, stop flag, deactivation guard
    server.js          Express API
    cli.js             crawl / search / stats without the server
  test/                node:test suites (no network)
  scripts/             debug_render.js (render one URL, show extraction), debug_strip.js, e2e_human_bot.py
  data/                kb.sqlite + Crawlee queues (git-ignore this)
```

## Why this shape (and what differs from the master prompt)

* **No Ollama, no Postgres on this laptop** (7.6 GB RAM, 0.8 GB free, no Docker). Embeddings use
  the Gemini key already in `../voice_clone/.env`; storage is SQLite through Node's built-in
  `node:sqlite` with an exact cosine scan. For a few thousand chunks this answers in milliseconds.
  The store's method surface (`replacePage / touchPage / deactivateUnseen / search / stats`) is
  what a pgvector implementation would provide; swapping it is a one-file change.
* **Playwright is mandatory for autocarrepair.in**: every URL returns the same 9.7 KB React shell with
  an empty `#root`. Images, fonts, media and tracker hosts are blocked per request to keep RAM low —
  **stylesheets are not**: Vite route chunks preload their CSS and fall into an error boundary
  ("Something Broke") when it is aborted. Pages that render such an error / not-found view are
  recorded as failures and never indexed (`looksLikeErrorPage` in `pipeline.js`).
* The crawler waits for painted text (not `networkidle`, which analytics beacons keep busy), then
  clicks collapsed accordions / `<details>` and copies each controlled panel into the DOM so FAQ
  answers are captured even on single-open accordions.
* **The chatbot here is `human_bot/`** — the earlier Galaxy Toyota assistant (app.db, Chroma,
  Salesforce) is not on this machine. Integration is a `knowledge` block in the persona and a
  per-turn retrieval in `human_bot/server/app.py`; nothing in human_bot was removed or rewritten.

## Run

```powershell
cd kb
npm install                       # once
npx playwright install chromium   # once (~150 MB)
copy .env.example .env            # optional; GEMINI_API_KEY falls back to ../voice_clone/.env

npm test                          # 12 unit/integration tests, no network
node src/cli.js crawl auto_car_repair --max-pages 30 --concurrency 1   # small first
node src/cli.js search auto_car_repair "car AC repair price gurgaon"
npm start                         # API on http://127.0.0.1:4100
```

Then start the bot with the ACR persona and talk to it:

```powershell
python human_bot\server\app.py --port 4003 --persona auto-car-repair
```

Every `/chat` turn now emits an SSE `sources` event (URLs + titles used) and the LLM prompt carries a
"Verified website information" block with the strict rule: service/price/location/offer facts only
from those passages, otherwise "I cannot verify this right now" + call-back. If kb is down, the bot
keeps working and says it cannot verify.

## Install as an npm package (`web-avatar-crawler`)

The folder is also a publishable package. Build and install it anywhere (no registry needed):

```bash
cd kb && npm pack                      # → web-avatar-crawler-0.1.0.tgz
cd ../my-site-bot && npm install ../kb/web-avatar-crawler-0.1.0.tgz
npx playwright install chromium        # once per machine
echo GEMINI_API_KEY=... > .env         # or export it
```

CLI (any https URL works — unknown hosts are registered on the fly; data goes to `./data/kb.sqlite` in the cwd):

```bash
npx web-avatar-crawler crawl  https://example.com --max-pages 50 --name "Example Motors"
npx web-avatar-crawler search https://example.com "opening hours" --k 3
npx web-avatar-crawler stats  https://example.com
npx web-avatar-crawler serve  --port 4100           # REST API below
```

Programmatic:

```js
import { createKnowledgeBase } from "web-avatar-crawler";
const kb = createKnowledgeBase();                                   // or { dbPath }
await kb.crawl("https://example.com", { maxPages: 50 });            // mode: "crawl" | "refresh" | "reindex"
const hits = await kb.search("https://example.com", "opening hours", { k: 3 });
await kb.serve(4100);                                               // same REST API as `serve`
await kb.close();
```

Lower-level exports: `KnowledgeStore`, `JobManager`, `createApp`, `extractPage`, `chunkMarkdown`, `embedTexts`/`embedQuery`, `runCrawl`, `defineWebsite`/`registerWebsite`, URL/sitemap/robots helpers.
Publishing to a registry (`npm publish`) is deliberately not wired: `license` is UNLICENSED and the name is unscoped — decide the scope/visibility first.

## API

Management routes need `x-api-key: $KB_ADMIN_KEY`; with no key configured they accept loopback
callers only. 120 requests/min per IP.

| Method | Path | Body / query | Notes |
|---|---|---|---|
| GET | `/health` | | embedding model, websites, job state |
| POST | `/api/crawler/start` | `{website, maxPages?, maxConcurrency?, renderJavaScript?}` | incremental: unchanged pages (same content hash) are skipped |
| POST | `/api/crawler/refresh` | same | incremental **and** marks pages not seen as `inactive` once the crawl completes |
| POST | `/api/knowledge/reindex` | same | wipes **that website only**, then full crawl |
| POST | `/api/crawler/stop` | | graceful: finishes in-flight pages |
| GET | `/api/crawler/status` | | live counters |
| GET | `/api/crawler/stats` | `?website=` | per-website pages by status, chunk count, models |
| GET | `/api/crawler/pages` | `?website=&status=&limit=&offset=` | |
| GET | `/api/crawler/jobs` | `?website=&limit=` | crawl history |
| GET/POST | `/api/knowledge/search` | `q, website, k?, min_score?, page_type?` | returns `[{score,url,title,page_type,heading,text}]`, ≤2 chunks per page |

`website` accepts an id (`auto_car_repair`) or any URL on that brand's domain.

## Incremental sync rules

1. Every page's Markdown is hashed; same hash → only `last_crawled_at` is touched (no re-embed).
2. Changed hash → chunks are replaced inside one transaction; no duplicates possible (`UNIQUE(page_id, chunk_index)`).
3. Pages that fail are recorded with the error but **keep their existing chunks**.
4. `refresh`/`reindex` deactivate pages not seen in the run — only if the run completed and saw at
   least `KB_DEACTIVATE_GUARD` (50 %) of previously active pages. A broken crawl cannot erase knowledge.
5. Inactive pages are excluded from search but kept with history.
6. The store refuses to mix embedding models/dimensions inside a website; change the model and run `reindex`.

## Crawl scope

Allowed: approved hosts only (`allowedHosts`), robots.txt honoured, same-URL dedupe after
normalisation (fragment, tracking params, trailing slash, default ports). Denied by pattern:
cart/checkout/pay/login/account/profile/admin/livewire/sanctum, feeds, media and asset files,
search/share query URLs, tag/author/paginated archives, and per-site extras (`/explore`,
`/become-an-agent`, `/coupons` for ACR). Caps: `maxPages`, `maxConcurrency` (≤8), nav 45 s,
handler 90 s, 2 retries.

## Adding a website

Any https URL can be crawled without config: `node src/cli.js crawl https://example.com --name "Example"`
registers it as `example_com` (host slug; www/non-www both allowed, common cart/login/asset paths
excluded). For a brand you will keep, add an entry in `src/config.js → websites` instead so extra
`disallow` rules, `maxPages` and the id are explicit, then create a persona JSON in
`human_bot/persona/` with `knowledge.website_id`. Either way: run with `--max-pages 50` first and
read `GET /api/crawler/pages` before a full run.

## Deployment

* `Dockerfile` builds on the Playwright image (Chromium included). Mount `/app/data` for the DB.
  `docker build -t tsg-kb kb && docker run -p 4100:4100 -e GEMINI_API_KEY=… -e KB_ADMIN_KEY=… -v kbdata:/app/data tsg-kb`
* Bare metal: `npm ci && npx playwright install --with-deps chromium && npm start` under a process
  manager (pm2 / NSSM on Windows). Keep `KB_HOST=127.0.0.1` and put nginx in front if exposed.
* Health: `GET /health`; alert when `job` stays `running` for hours or `/api/crawler/stats` shows a
  rising `failed` count. Logs are JSON lines on stdout/stderr; keys are redacted.
* Schedule `POST /api/crawler/refresh` nightly (Task Scheduler / cron) for continuous sync.

## Limits and honest notes

* **Gemini free tier = 1,000 embedding requests per day per model** (`EmbedContentRequestsPerDay…-FreeTier`),
  and every chunk in a batch counts as one request. 80 pages of autocarrepair.in ≈ 920 chunks, so one
  full re-embed uses the day's allowance; the ~330-page site (~3k chunks) needs a paid key or 3–4 days
  of incremental crawls (unchanged pages cost nothing). Per-minute 429s are retried with Gemini's
  `retryDelay`; a per-day 429 fails fast so the chatbot falls back to "cannot verify" instead of waiting.
  Query vectors are cached in memory (2,000 LRU), so repeated questions spend no quota.
* Exact cosine scan is fine to ~50k chunks; past that, move the store to pgvector (HNSW).
* Boilerplate removal is structural (nav/footer/cookie/form selectors + repeated-line dedupe); a
  site-wide frequency filter is the next improvement if headers leak into chunks.
* Hindi/Hinglish queries work through the multilingual embedding model; the site content is English.
