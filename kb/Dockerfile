# Playwright base image ships Chromium + system deps; Node 22 satisfies node:sqlite.
FROM mcr.microsoft.com/playwright:v1.49.0-noble

ENV NODE_ENV=production \
    KB_HOST=0.0.0.0 \
    KB_PORT=4100 \
    KB_DATA_DIR=/app/data \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --omit=dev --no-audit --no-fund
COPY src ./src

RUN mkdir -p /app/data && chown -R pwuser:pwuser /app
USER pwuser
VOLUME ["/app/data"]
EXPOSE 4100
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD node -e "fetch('http://127.0.0.1:4100/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["node", "src/server.js"]
