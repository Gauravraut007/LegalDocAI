# syntax=docker/dockerfile:1.6
ARG PYTHON_VERSION=3.11

FROM python:${PYTHON_VERSION}-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential gcc libpq-dev curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt ./
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# ============================== RUNTIME =====================================
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=core.settings.development

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 curl ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 1000 app \
 && useradd  --system --uid 1000 --gid app --create-home --home-dir /home/app app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=app:app . /app

RUN mkdir -p /data/uploads /app/staticfiles \
 && chown -R app:app /data /app/staticfiles

USER app

# Best-effort collectstatic (won't fail build if a setting is unresolved at build time).
RUN python manage.py collectstatic --noinput || true

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz/ || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "core.asgi:application"]
