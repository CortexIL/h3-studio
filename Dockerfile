# Build the React frontend. Node exists only in this stage: the image that runs
# keeps Python as its only runtime. The type check runs as part of the build,
# so a type error fails the deploy instead of shipping.
FROM node:24-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim

# ffmpeg is not optional: the audio strip remuxes finished clips with it, and the
# mock backend renders real MP4s with it. curl is here for the healthcheck.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg curl \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies before source, so editing a route does not re-resolve pip.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY web ./web
COPY --from=web /web/dist ./frontend/dist

# Non-root. Nothing here writes to the image; everything that persists is in
# Postgres or the object store.
RUN useradd --create-home --uid 10001 h3 && chown -R h3:h3 /app
USER h3

EXPOSE 8777

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8777/api/health || exit 1

CMD ["python", "-m", "app.main"]
