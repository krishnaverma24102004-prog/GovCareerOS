# GovCareerOS API image (also runs the migrate and scheduler services in docker-compose.yml).
# The code imports itself as "govcareeros.backend...", so it lives in /app/govcareeros with PYTHONPATH=/app.
#
#   docker build -t govcareeros-api .
#   docker build -t govcareeros-api --build-arg INSTALL_PLAYWRIGHT=1 --build-arg INSTALL_OCR=1 .
#
# No .env is copied (see .dockerignore): configuration comes from the environment at run time.
FROM python:3.12-slim-bookworm

# 1 = Chromium for the browser-rendered sources (SSC, some state portals); adds ~500 MB
ARG INSTALL_PLAYWRIGHT=0
# 1 = Tesseract (English) for scanned notice pages; without it scanned pages are reported as unreadable
ARG INSTALL_OCR=0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata \
    STORAGE_PATH=/data/storage \
    LOG_FORMAT=json

WORKDIR /app

RUN if [ "$INSTALL_OCR" = "1" ]; then \
        apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
        && rm -rf /var/lib/apt/lists/*; \
    fi

# Runtime requirements only: everything above the "# dev / test" marker
COPY requirements.txt /tmp/requirements.txt
RUN sed '/^# dev/,$d' /tmp/requirements.txt > /tmp/runtime.txt \
    && pip install -r /tmp/runtime.txt \
    && rm /tmp/requirements.txt /tmp/runtime.txt

RUN if [ "$INSTALL_PLAYWRIGHT" = "1" ]; then \
        python -m playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/*; \
    fi

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /data/storage && chown -R app:app /data

COPY --chown=root:root . /app/govcareeros

USER app
EXPOSE 8080
VOLUME ["/data/storage"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4).status == 200 else 1)"]

# --proxy-headers: the scheme (https, for HSTS) and client address come from X-Forwarded-* sent by the addresses in
# FORWARDED_ALLOW_IPS (uvicorn reads it from the environment; default 127.0.0.1). WEB_CONCURRENCY sets workers.
CMD ["uvicorn", "govcareeros.backend.api.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--no-server-header"]
