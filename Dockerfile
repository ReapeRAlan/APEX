# ============================================================
# APEX Backend — Multi-stage Dockerfile
# Stage 1: CUDA base + system deps
# Stage 2: Python dependencies
# Stage 3: Application code
# ============================================================

# ── Stage 1: CUDA base with system dependencies ──
FROM nvidia/cuda:12.1.1-runtime-ubuntu22.04 AS base

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=utf-8

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 \
    python3.11-venv \
    python3.11-dev \
    python3-pip \
    gdal-bin \
    libgdal-dev \
    libgeos-dev \
    libproj-dev \
    libspatialindex-dev \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set python3.11 as default
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1 \
    && update-alternatives --install /usr/bin/python python /usr/bin/python3.11 1

# ── Stage 2: Python dependencies ──
FROM base AS deps

WORKDIR /app

COPY backend/requirements.txt /app/requirements.txt

# Install PyTorch with CUDA 12.1 support
RUN pip install --no-cache-dir \
    torch==2.3.0+cu121 \
    torchvision==0.18.0+cu121 \
    --extra-index-url https://download.pytorch.org/whl/cu121

RUN pip install --no-cache-dir -r requirements.txt

# Download spaCy model for NLP
RUN python -m spacy download es_core_news_lg || true

# ── Stage 3: Application ──
FROM deps AS app

WORKDIR /app

# Copy backend code
COPY backend/ /app/backend/

# Copy data directories if they exist
COPY data/ /app/data/ 

# Create necessary directories
RUN mkdir -p /app/db /app/logs /app/data/tiles

# Expose port
EXPOSE 8008

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8008/ || exit 1

# Run with uvicorn
CMD ["python", "-m", "uvicorn", "backend.main:app", \
     "--host", "0.0.0.0", "--port", "8008", "--workers", "2"]
