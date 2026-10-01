# =============================================================================
# FinPulse Ingestor — slim, reproducible Python 3.11 build
# =============================================================================
FROM python:3.11-slim AS base

# Prevent .pyc files and enable unbuffered stdout for real-time Docker logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps required to build psycopg2 (compiled against libpq)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first to leverage Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY db_utils.py scheduler.py ./

# Run as a non-root user for defense-in-depth
RUN useradd --create-home --shell /bin/bash finpulse \
    && chown -R finpulse:finpulse /app
USER finpulse

CMD ["python", "scheduler.py"]
