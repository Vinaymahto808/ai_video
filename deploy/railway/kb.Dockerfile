# kb: the Node knowledge-base service (SQLite + FTS5 + Gemini embeddings + search API).
#
# Built from the repository root:
#   RAILWAY_DOCKERFILE_PATH=deploy/railway/kb.Dockerfile
#
# Node 24 ships `node:sqlite`, so there is no native build step. Playwright is a runtime dependency of
# the crawl job only; --ignore-scripts skips its browser download (~300 MB) because Railway only answers
# search queries. Crawling from this image needs `npx playwright install chromium` added back.
FROM node:24-bookworm-slim

ENV NODE_ENV=production \
    KB_HOST=0.0.0.0 \
    KB_PORT=4100 \
    KB_DATA_DIR=/app/data

WORKDIR /app

COPY kb/package.json kb/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund --ignore-scripts

COPY kb/src ./src
# seed/kb.sqlite: the committed crawl snapshot (public site content) copied into data/ on first boot.
# seed/qa_pages.jsonl.gz.b64: customer-call Q&A bundle, present in the `railway up` archive (not in git);
# seed.js imports it when KB_QA_BUNDLE points at it. Missing file is not an error.
COPY kb/seed ./seed

# Ephemeral unless a Railway volume is attached at /app/data (railway volume add -s kb -m /app/data).
# Railway rejects the Docker VOLUME instruction, so it is deliberately absent.
RUN mkdir -p /app/data

EXPOSE 4100

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:'+(process.env.KB_PORT||4100)+'/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

# seed.js is idempotent: it copies the snapshot only when data/kb.sqlite is absent and skips QA pages
# whose page_hash is unchanged.
CMD ["sh", "-c", "node src/seed.js && exec node src/server.js"]
