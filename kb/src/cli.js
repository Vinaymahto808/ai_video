// CLI (also exposed as the `web-avatar-crawler` bin).
//   web-avatar-crawler crawl  <website_id|url> [--max-pages 30] [--concurrency 2] [--name "Brand"] [--refresh|--reindex]
//   web-avatar-crawler search <website_id|url> "car AC repair gurgaon" [--k 5] [--type faq] [--min 0.55]
//   web-avatar-crawler stats  [website_id|url]
//   web-avatar-crawler serve  [--port 4100] [--host 127.0.0.1]
import { settings, websites, getWebsite, defineWebsite, registerWebsite } from "./config.js";
import { embedQuery } from "./embed/gemini.js";
import { JobManager } from "./jobs.js";
import { createApp } from "./server.js";
import { log } from "./log.js";
import { KnowledgeStore } from "./store/sqlite.js";

const [cmd, website, ...rest] = process.argv.slice(2);
const flag = (name, d) => { const i = rest.indexOf(`--${name}`); return i >= 0 ? rest[i + 1] : d; };
const has = (name) => rest.includes(`--${name}`);
const usage = () => {
  console.log(`usage:
  web-avatar-crawler crawl  <website_id|url> [--max-pages N] [--concurrency N] [--name "Brand"] [--refresh|--reindex]
  web-avatar-crawler search <website_id|url> "question" [--k 5] [--type page_type] [--min 0.55]
  web-avatar-crawler stats  [website_id|url]
  web-avatar-crawler serve  [--port 4100] [--host 127.0.0.1]

registered websites: ${Object.keys(websites).join(", ") || "(none; pass any https URL)"}
env: GEMINI_API_KEY (required), KB_DATA_DIR, KB_PORT, KB_ADMIN_KEY … see .env.example`);
};

if (!cmd || has("help") || cmd === "help") { usage(); process.exit(cmd ? 0 : 2); }

const store = new KnowledgeStore(settings.dbPath);
for (const w of Object.values(websites)) store.upsertWebsite(w);

if (cmd === "crawl") {
  if (!website) { usage(); process.exit(2); }
  // --name sets the brand name a URL is registered under (shown by the API and the bot)
  if (flag("name")) { const s = getWebsite(website); if (s) store.upsertWebsite(registerWebsite({ ...s, name: flag("name") })); }
  const jobs = new JobManager(store);
  const mode = has("reindex") ? "reindex" : has("refresh") ? "refresh" : "crawl";
  jobs.start(website, { maxPages: Number(flag("max-pages", 0)) || undefined, maxConcurrency: Number(flag("concurrency", 0)) || undefined }, mode);
  process.on("SIGINT", () => jobs.stop());
  await jobs.wait();
  console.log(JSON.stringify(jobs.status(), null, 2));
  console.log(JSON.stringify(store.stats(getWebsite(website)?.id), null, 2));
  store.close();
} else if (cmd === "search") {
  const site = getWebsite(website);
  if (!site) { console.error(`unknown website: ${website}`); process.exit(2); }
  const q = rest.filter((a, i) => !a.startsWith("--") && !(i > 0 && rest[i - 1].startsWith("--"))).join(" ");
  if (!q) { usage(); process.exit(2); }
  const vec = await embedQuery(q);
  const results = store.search(site.id, vec, { k: Number(flag("k", settings.search.defaultK)), pageType: flag("type"), minScore: Number(flag("min", settings.search.minScore)) });
  for (const r of results) console.log(`\n[${r.score}] ${r.title}  (${r.page_type})\n${r.url}\n${r.heading ? "§ " + r.heading + "\n" : ""}${r.text.slice(0, 400).replace(/\n+/g, " ")}…`);
  if (!results.length) console.log("no results above min score");
  store.close();
} else if (cmd === "stats") {
  const id = website ? getWebsite(website)?.id : undefined;
  console.log(JSON.stringify({ stats: store.stats(id), jobs: store.jobs(id, 5) }, null, 2));
  store.close();
} else if (cmd === "serve") {
  // `serve` takes no website argument: treat the first token as a flag list too
  const args = [website, ...rest].filter(Boolean);
  const f = (name, d) => { const i = args.indexOf(`--${name}`); return i >= 0 ? args[i + 1] : d; };
  const port = Number(f("port", settings.port)); const host = f("host", settings.host);
  const jobs = new JobManager(store);
  const srv = createApp({ store, jobs }).listen(port, host, () => log.info("kb listening", { url: `http://${host}:${port}`, db: settings.dbPath }));
  const shutdown = async () => { jobs.stop(); srv.close(); await jobs.wait().catch(() => {}); store.close(); process.exit(0); };
  process.on("SIGINT", shutdown); process.on("SIGTERM", shutdown);
} else {
  usage();
  store.close();
  process.exitCode = 2;
}
