# =============================================================================
# FinPulse Dashboard — slim Python 3.11 build serving Streamlit on :8501
# =============================================================================
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

WORKDIR /app

# libpq-dev/gcc needed to build psycopg2; curl needed for the healthcheck
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py drift_detector.py ./

RUN useradd --create-home --shell /bin/bash finpulse \
    && chown -R finpulse:finpulse /app
USER finpulse

EXPOSE 8501

HEALTHCHECK --interval=15s --timeout=5s --retries=5 --start-period=15s \
    CMD curl --fail http://localhost:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
