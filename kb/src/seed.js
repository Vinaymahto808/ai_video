// First boot on a host without a persistent disk (Render free plan): copy the committed crawl snapshot
// (seed/kb.sqlite: 80 pages + 919 chunks + local vectors of autocarrepair.in, public site content) into
// data/ so the server answers immediately. A kb.sqlite that is already there is never touched.
import { copyFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { DATA_DIR, ROOT_DIR, settings } from "./config.js";

const seed = resolve(ROOT_DIR, "seed", "kb.sqlite");
if (existsSync(settings.dbPath)) {
  console.log(`[seed] ${settings.dbPath} exists, keeping it`);
} else if (!existsSync(seed)) {
  console.log(`[seed] no ${seed}; the server starts with an empty knowledge base`);
} else {
  mkdirSync(dirname(settings.dbPath), { recursive: true });
  mkdirSync(DATA_DIR, { recursive: true });
  copyFileSync(seed, settings.dbPath);
  console.log(`[seed] copied ${seed} -> ${settings.dbPath}`);
}
