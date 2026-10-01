"""
scheduler.py
------------
FinPulse ingestor entry point.

Responsibilities:
  1. On startup (and on a recurring schedule), fetch daily historical OHLCV
     data for each configured ticker via yfinance.
  2. Compute daily returns and a 20-day annualized rolling volatility.
  3. Ensure the target schema exists, then idempotently upsert results into
     PostgreSQL, deduplicated on (ticker, trade_date).

Runs as a long-lived foreground process suitable for `docker compose up`.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import List

import numpy as np
import pandas as pd
import schedule
import yfinance as yf
from dotenv import load_dotenv

from db_utils import get_engine, init_schema, upsert_dataframe

# Load a local .env file if present (walks up from this file's directory to
# the project root). This is a no-op inside Docker Compose, where real env
# vars are already injected — see the identical note in db_utils.py. This
# call is harmless/redundant with db_utils' own load_dotenv() (it's a no-op
# once variables are already loaded into os.environ) but is kept explicit
# here too since scheduler.py is this container's actual entry point.
load_dotenv()

# --------------------------------------------------------------------------
# Logging configuration — unbuffered, stdout-based, Docker-log friendly
# --------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("finpulse.scheduler")

TRADING_DAYS_PER_YEAR = 252


def _get_tickers() -> List[str]:
    raw = os.getenv("TICKERS", "^NSEI,SPY")
    tickers = [t.strip() for t in raw.split(",") if t.strip()]
    if not tickers:
        logger.warning("No tickers configured; falling back to default SPY.")
        return ["SPY"]
    return tickers


def _get_config() -> dict:
    return {
        "tickers": _get_tickers(),
        "lookback_period": os.getenv("LOOKBACK_PERIOD", "2y"),
        "volatility_window": int(os.getenv("VOLATILITY_WINDOW", "20")),
        "poll_interval_minutes": int(os.getenv("POLL_INTERVAL_MINUTES", "1440")),
        "max_retries": int(os.getenv("MAX_RETRIES", "5")),
        "retry_backoff_seconds": int(os.getenv("RETRY_BACKOFF_SECONDS", "5")),
    }


def fetch_ohlcv(ticker: str, lookback_period: str, max_retries: int, backoff: int) -> pd.DataFrame:
    """
    Download historical daily OHLCV data for `ticker` via yfinance, with
    retry/backoff to tolerate transient network or upstream API failures.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            logger.info("Fetching '%s' data (period=%s, attempt %d)...", ticker, lookback_period, attempt)
            data = yf.download(
                ticker,
                period=lookback_period,
                interval="1d",
                auto_adjust=True,
                progress=False,
                threads=False,
            )
            if data.empty:
                raise ValueError(f"yfinance returned an empty dataset for '{ticker}'.")

            # yfinance can return a MultiIndex column structure for single
            # tickers in some versions; flatten defensively.
            if isinstance(data.columns, pd.MultiIndex):
                data.columns = data.columns.get_level_values(0)

            data = data.rename(
                columns={
                    "Open": "open",
                    "High": "high",
                    "Low": "low",
                    "Close": "close",
                    "Volume": "volume",
                }
            )
            data = data.reset_index().rename(columns={"Date": "trade_date"})
            logger.info("Fetched %d row(s) for '%s'.", len(data), ticker)
            return data[["trade_date", "open", "high", "low", "close", "volume"]]

        except Exception as exc:  # noqa: BLE001 - broad by design at API boundary
            if attempt >= max_retries:
                logger.error(
                    "Exhausted %d attempts fetching '%s': %s", max_retries, ticker, exc
                )
                raise
            logger.warning(
                "Fetch attempt %d/%d for '%s' failed (%s). Retrying in %ds...",
                attempt,
                max_retries,
                ticker,
                exc,
                backoff,
            )
            time.sleep(backoff)


def compute_metrics(df: pd.DataFrame, volatility_window: int) -> pd.DataFrame:
    """
    Compute daily simple returns and annualized rolling volatility.

    Volatility is annualized as: std(daily_return, window) * sqrt(252)
    """
    df = df.sort_values("trade_date").copy()
    df["daily_return"] = df["close"].pct_change()
    df["rolling_volatility_20d"] = df["daily_return"].rolling(
        window=volatility_window, min_periods=volatility_window
    ).std() * np.sqrt(TRADING_DAYS_PER_YEAR)

    # Replace NaN/inf produced by pct_change()/rolling() warm-up periods
    # with None so they serialize cleanly to SQL NULLs.
    df = df.replace([np.inf, -np.inf], np.nan)
    df["daily_return"] = df["daily_return"].astype(object).where(df["daily_return"].notna(), None)
    df["rolling_volatility_20d"] = (
        df["rolling_volatility_20d"].astype(object).where(df["rolling_volatility_20d"].notna(), None)
    )

    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    return df


def run_ingest_job() -> None:
    """Single end-to-end ingest pass across all configured tickers."""
    config = _get_config()
    logger.info("=== Starting ingest job for tickers: %s ===", config["tickers"])

    try:
        engine = get_engine(
            max_retries=config["max_retries"],
            retry_backoff_seconds=config["retry_backoff_seconds"],
        )
        init_schema(engine)
    except Exception:
        logger.exception("Fatal: could not establish DB connection/schema. Skipping this run.")
        return

    for ticker in config["tickers"]:
        try:
            raw_df = fetch_ohlcv(
                ticker,
                config["lookback_period"],
                config["max_retries"],
                config["retry_backoff_seconds"],
            )
            metrics_df = compute_metrics(raw_df, config["volatility_window"])
            metrics_df.insert(0, "ticker", ticker)
            upsert_dataframe(engine, metrics_df, ticker)
        except Exception:
            # Isolate failures per-ticker so one bad symbol doesn't kill the batch.
            logger.exception("Ingest failed for ticker '%s'; continuing with next ticker.", ticker)
            continue

    logger.info("=== Ingest job complete ===")


def main() -> None:
    config = _get_config()
    interval = config["poll_interval_minutes"]

    logger.info(
        "FinPulse ingestor starting | tickers=%s | interval=%d min | lookback=%s",
        config["tickers"],
        interval,
        config["lookback_period"],
    )

    # Run immediately on startup so the dashboard has data without waiting
    # a full scheduling cycle.
    run_ingest_job()

    schedule.every(interval).minutes.do(run_ingest_job)

    logger.info("Entering scheduled polling loop (every %d minutes)...", interval)
    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    main()
