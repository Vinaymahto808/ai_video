# human_bot: the Python voice server (LLM + Groq Whisper STT + Edge TTS + puppet avatar assets).
#
# Built from the repository root (Railway uploads the project, then builds this file):
#   RAILWAY_DOCKERFILE_PATH=deploy/railway/bot.Dockerfile
#
# Only edge-tts is a third-party runtime dependency; everything else is Python stdlib.
# pip pin matches the version measured on the dev machine (2026-11-04).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Edge TTS speaks WSS to Microsoft; the slim image needs the CA bundle to verify it.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN pip install "edge-tts==7.3.1"

WORKDIR /app

# No site mirror in the container (HUMAN_BOT_SITE_MIRROR=0): this is the API + demo page only.
COPY human_bot ./human_bot

# Avatar assets the server reads at request time:
#   prepared/  face.jpg, face.json, idle.mp4, clips_neha/
#   path_b/    mouth shapes, expression/reaction layers, manifests (2D puppet)
COPY tran_moment/prepared ./tran_moment/prepared
COPY tran_moment/path_b ./tran_moment/path_b

# Recordings + conversations.sqlite land here. Ephemeral unless a volume is mounted at /app/human_bot/data.
RUN mkdir -p /app/human_bot/data/audio

EXPOSE 8080

# Railway injects PORT when a domain is attached with that target port; fall back to 8080 for `docker run`.
CMD ["sh", "-c", "exec python human_bot/server/app.py --host 0.0.0.0 --port ${PORT:-8080} --persona auto-car-repair"]
