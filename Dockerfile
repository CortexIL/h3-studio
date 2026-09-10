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

# Non-root. Nothing here writes to the image; everything that persists is in
# Postgres or the object store.
RUN useradd --create-home --uid 10001 h3 && chown -R h3:h3 /app
USER h3

EXPOSE 8777

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8777/api/health || exit 1

CMD ["python", "-m", "app.main"]
