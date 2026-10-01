"""
db_utils.py
-----------
PostgreSQL connection management, schema migration, and idempotent
(deduplicated) upserts for FinPulse OHLCV + volatility records.

This module is intentionally self-contained so it can be imported by both
the ingestor worker and, if needed, ad-hoc maintenance scripts.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

# Load variables from a local .env file if one is present (walks up from this
# file's directory to the project root). Inside Docker Compose this is a
# harmless no-op: real env vars are already injected via `env_file`/
# `environment` and load_dotenv() never overrides an already-set variable.
# This is what lets someone run `python scheduler.py` (or import db_utils)
# directly on a Windows/macOS/Linux host, outside any container, and still
# pick up the same .env used by `docker compose up`.
load_dotenv()

logger = logging.getLogger("finpulse.db_utils")

TABLE_NAME = "ohlcv_metrics"

CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    id                      BIGSERIAL PRIMARY KEY,
    ticker                  VARCHAR(20)      NOT NULL,
    trade_date              DATE             NOT NULL,
    open                    DOUBLE PRECISION,
    high                    DOUBLE PRECISION,
    low                     DOUBLE PRECISION,
    close                   DOUBLE PRECISION,
    volume                  BIGINT,
    daily_return            DOUBLE PRECISION,
    rolling_volatility_20d  DOUBLE PRECISION,
    ingested_at             TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_ticker_date UNIQUE (ticker, trade_date)
);
"""

CREATE_INDEX_SQL = f"""
CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_ticker_date
    ON {TABLE_NAME} (ticker, trade_date DESC);
"""

UPSERT_SQL = f"""
INSERT INTO {TABLE_NAME} (
    ticker, trade_date, open, high, low, close, volume,
    daily_return, rolling_volatility_20d
) VALUES (
    :ticker, :trade_date, :open, :high, :low, :close, :volume,
    :daily_return, :rolling_volatility_20d
)
ON CONFLICT (ticker, trade_date)
DO UPDATE SET
    open                   = EXCLUDED.open,
    high                   = EXCLUDED.high,
    low                    = EXCLUDED.low,
    close                  = EXCLUDED.close,
    volume                 = EXCLUDED.volume,
    daily_return           = EXCLUDED.daily_return,
    rolling_volatility_20d = EXCLUDED.rolling_volatility_20d,
    ingested_at             = NOW();
"""


def _build_db_url() -> str:
    """Assemble a PostgreSQL connection URL from environment variables.

    POSTGRES_HOST defaults to 'localhost' so that this module works
    out-of-the-box for local, non-containerized runs (e.g. `python
    scheduler.py` on a Windows/macOS/Linux host talking to a Postgres
    container whose port has been published to the host, such as the
    `db` service's `5432:5432` mapping in docker-compose.yml).

    Inside Docker Compose, this default is irrelevant: docker-compose.yml
    explicitly sets `POSTGRES_HOST=db` as a container environment variable
    for the `ingestor` and `dashboard` services, which always takes
    precedence over both this fallback and anything in a local .env file.
    'db' only resolves via Docker's internal DNS and will NOT work if you
    run this module directly on your host machine — use 'localhost' (or
    '127.0.0.1') there instead.
    """
    user = os.getenv("POSTGRES_USER", "finpulse")
    password = os.getenv("POSTGRES_PASSWORD", "finpulse_pw")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db_name = os.getenv("POSTGRES_DB", "finpulse_db")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db_name}"


def get_engine(
    max_retries: Optional[int] = None,
    retry_backoff_seconds: Optional[int] = None,
) -> Engine:
    """
    Create a SQLAlchemy engine and verify connectivity with retry/backoff.

    Retries handle the common startup race where the ingestor container
    is ready before Postgres has fully accepted connections, even behind
    a healthcheck-gated `depends_on`.
    """
    max_retries = max_retries or int(os.getenv("MAX_RETRIES", "5"))
    retry_backoff_seconds = retry_backoff_seconds or int(
        os.getenv("RETRY_BACKOFF_SECONDS", "5")
    )

    db_url = _build_db_url()
    engine = create_engine(db_url, pool_pre_ping=True, future=True)

    attempt = 0
    while True:
        attempt += 1
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("Successfully connected to PostgreSQL (attempt %d).", attempt)
            return engine
        except OperationalError as exc:
            if attempt >= max_retries:
                logger.error(
                    "Exhausted %d connection attempts to PostgreSQL: %s",
                    max_retries,
                    exc,
                )
                raise
            logger.warning(
                "DB connection attempt %d/%d failed (%s). Retrying in %ds...",
                attempt,
                max_retries,
                exc.__class__.__name__,
                retry_backoff_seconds,
            )
            time.sleep(retry_backoff_seconds)


def init_schema(engine: Engine) -> None:
    """Create the target table and supporting index if they do not exist."""
    with engine.begin() as conn:
        conn.execute(text(CREATE_TABLE_SQL))
        conn.execute(text(CREATE_INDEX_SQL))
    logger.info("Schema verified/created for table '%s'.", TABLE_NAME)


def upsert_dataframe(engine: Engine, df: pd.DataFrame, ticker: str) -> int:
    """
    Idempotently insert or update rows from `df` into the metrics table.

    Deduplication is enforced at the database level via the
    UNIQUE (ticker, trade_date) constraint combined with
    `ON CONFLICT ... DO UPDATE`, so re-running the ingestor (e.g. after a
    restart, or on overlapping lookback windows) never creates duplicates.

    Returns the number of rows submitted for upsert.
    """
    if df.empty:
        logger.info("No rows to upsert for ticker '%s'.", ticker)
        return 0

    records = df.to_dict(orient="records")

    with engine.begin() as conn:
        conn.execute(text(UPSERT_SQL), records)

    logger.info("Upserted %d row(s) for ticker '%s'.", len(records), ticker)
    return len(records)


def get_distinct_tickers(engine: Engine) -> list[str]:
    """Return all tickers currently present in the metrics table."""
    with engine.connect() as conn:
        result = conn.execute(
            text(f"SELECT DISTINCT ticker FROM {TABLE_NAME} ORDER BY ticker;")
        )
        return [row[0] for row in result]


def load_ticker_history(engine: Engine, ticker: str) -> pd.DataFrame:
    """Load the full historical record for a single ticker, ordered by date."""
    query = text(
        f"""
        SELECT trade_date, open, high, low, close, volume,
               daily_return, rolling_volatility_20d
        FROM {TABLE_NAME}
        WHERE ticker = :ticker
        ORDER BY trade_date ASC;
        """
    )
    with engine.connect() as conn:
        df = pd.read_sql(query, conn, params={"ticker": ticker})
    return df
