# syntax=docker/dockerfile:1.6
# ----------------------------------------------------------------------------
# FastAPI service - multi-stage build.
# Stage 1 (builder): install Python deps + pre-download SBERT model.
# Stage 2 (runtime): slim image with system deps for OCR, PDF, libmagic.
# ----------------------------------------------------------------------------

ARG PYTHON_VERSION=3.11

# ============================== BUILDER STAGE ===============================
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        libpq-dev \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY requirements.txt ./
# python-magic-bin is Windows-only; strip before installing on Linux.
RUN sed -i '/python-magic-bin/d' requirements.txt \
 && python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# Pre-download the local sentence-transformers model into the image so the
# runtime container starts offline-friendly.
ARG EMBEDDING_MODEL_NAME=BAAI/bge-small-en-v1.5
ENV HF_HOME=/opt/hf_cache \
    SENTENCE_TRANSFORMERS_HOME=/opt/hf_cache \
    TRANSFORMERS_OFFLINE=0
RUN /opt/venv/bin/python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('${EMBEDDING_MODEL_NAME}')"

# ============================== RUNTIME STAGE ===============================
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    HF_HOME=/opt/hf_cache \
    SENTENCE_TRANSFORMERS_HOME=/opt/hf_cache \
    TRANSFORMERS_OFFLINE=1

# Runtime system deps: tesseract (OCR), poppler-utils (pdf2image),
# libmagic1 (python-magic), libgl1 (image libs), libpq (psycopg2), curl (HC).
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
        poppler-utils \
        libmagic1 \
        libgl1 \
        libglib2.0-0 \
        libpq5 \
        curl \
        ca-certificates \
        tini \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN groupadd --system --gid 1000 app \
 && useradd  --system --uid 1000 --gid app --create-home --home-dir /home/app app

# Copy venv + HF cache from builder
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/hf_cache /opt/hf_cache

WORKDIR /app
COPY --chown=app:app . /app

# Data directories owned by non-root user (mounted as named volumes in compose)
RUN mkdir -p /data/faiss /data/uploads /data/uploads/processed \
 && chown -R app:app /data /opt/hf_cache

USER app

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8001/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
