"""
app.py
------
FinPulse Streamlit dashboard.

Connects to PostgreSQL, renders summary metric cards, interactive Plotly
price/volatility charts, and a statistical regime-drift panel powered by
`drift_detector.compute_drift` (two-sample Kolmogorov-Smirnov test).
"""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import List

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from drift_detector import compute_drift

# Load variables from a local .env file if one is present (walks up from this
# file's directory to the project root). Inside Docker Compose this is a
# harmless no-op — real env vars are already injected via `env_file`/
# `environment` and load_dotenv() never overrides an already-set variable.
# This is what lets you run `streamlit run app.py` directly on your host
# machine (e.g. for quick UI debugging on Windows) and still pick up the
# same .env used by `docker compose up`.
load_dotenv()

# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("finpulse.dashboard")

TABLE_NAME = "ohlcv_metrics"

st.set_page_config(
    page_title="FinPulse | Market Drift Monitor",
    page_icon="📈",
    layout="wide",
)


# --------------------------------------------------------------------------
# Configuration helpers
# --------------------------------------------------------------------------
def _build_db_url() -> str:
    """Assemble a PostgreSQL connection URL from environment variables.

    POSTGRES_HOST defaults to 'localhost' so this dashboard also runs
    out-of-the-box with `streamlit run app.py` directly on your host
    machine, against a Postgres container whose port has been published
    to the host (the `db` service's `5432:5432` mapping).

    Inside Docker Compose, docker-compose.yml explicitly sets
    POSTGRES_HOST=db as a container environment variable for this service,
    which always takes precedence over this fallback and over anything in
    a local .env file — 'db' only resolves via Docker's internal DNS.
    """
    user = os.getenv("POSTGRES_USER", "finpulse")
    password = os.getenv("POSTGRES_PASSWORD", "finpulse_pw")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db_name = os.getenv("POSTGRES_DB", "finpulse_db")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db_name}"


@st.cache_resource(show_spinner=False)
def get_engine() -> Engine:
    """
    Create (and cache across reruns) a SQLAlchemy engine, retrying on
    startup since the dashboard container may become ready before
    PostgreSQL finishes accepting connections.
    """
    max_retries = int(os.getenv("MAX_RETRIES", "5"))
    backoff = int(os.getenv("RETRY_BACKOFF_SECONDS", "5"))
    db_url = _build_db_url()
    engine = create_engine(db_url, pool_pre_ping=True, future=True)

    attempt = 0
    while True:
        attempt += 1
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("Dashboard connected to PostgreSQL (attempt %d).", attempt)
            return engine
        except OperationalError as exc:
            if attempt >= max_retries:
                logger.error("Exhausted %d DB connection attempts: %s", max_retries, exc)
                raise
            logger.warning(
                "DB connection attempt %d/%d failed. Retrying in %ds...",
                attempt,
                max_retries,
                backoff,
            )
            time.sleep(backoff)


@st.cache_data(ttl=300, show_spinner=False)
def get_available_tickers(_engine: Engine) -> List[str]:
    try:
        with _engine.connect() as conn:
            result = conn.execute(
                text(f"SELECT DISTINCT ticker FROM {TABLE_NAME} ORDER BY ticker;")
            )
            return [row[0] for row in result]
    except Exception:
        logger.exception("Failed to load ticker list.")
        return []


@st.cache_data(ttl=300, show_spinner=False)
def load_ticker_data(_engine: Engine, ticker: str) -> pd.DataFrame:
    query = text(
        f"""
        SELECT trade_date, open, high, low, close, volume,
               daily_return, rolling_volatility_20d
        FROM {TABLE_NAME}
        WHERE ticker = :ticker
        ORDER BY trade_date ASC;
        """
    )
    with _engine.connect() as conn:
        df = pd.read_sql(query, conn, params={"ticker": ticker})
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df


# --------------------------------------------------------------------------
# UI rendering helpers
# --------------------------------------------------------------------------
def render_metric_cards(df: pd.DataFrame) -> None:
    latest = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else latest

    price_delta = latest["close"] - prev["close"]
    price_delta_pct = (price_delta / prev["close"] * 100) if prev["close"] else 0.0

    col1, col2, col3, col4 = st.columns(4)
    col1.metric(
        "Latest Close",
        f"{latest['close']:,.2f}",
        f"{price_delta:+.2f} ({price_delta_pct:+.2f}%)",
    )
    col2.metric(
        "Daily Return",
        f"{(latest['daily_return'] or 0) * 100:.2f}%",
    )
    vol_value = latest["rolling_volatility_20d"]
    col3.metric(
        "20D Ann. Volatility",
        f"{vol_value * 100:.2f}%" if pd.notna(vol_value) else "N/A",
    )
    col4.metric(
        "Latest Trade Date",
        latest["trade_date"].strftime("%Y-%m-%d"),
    )


def render_price_volatility_tab(df: pd.DataFrame) -> None:
    price_fig = go.Figure()
    price_fig.add_trace(
        go.Scatter(
            x=df["trade_date"],
            y=df["close"],
            mode="lines",
            name="Close Price",
            line=dict(color="#2563eb", width=1.8),
        )
    )
    price_fig.update_layout(
        title="Price History",
        xaxis_title="Date",
        yaxis_title="Price",
        height=420,
        margin=dict(l=10, r=10, t=50, b=10),
        template="plotly_white",
    )
    st.plotly_chart(price_fig, use_container_width=True)

    vol_fig = go.Figure()
    vol_fig.add_trace(
        go.Scatter(
            x=df["trade_date"],
            y=df["rolling_volatility_20d"],
            mode="lines",
            name="20D Annualized Volatility",
            line=dict(color="#dc2626", width=1.8),
            fill="tozeroy",
            fillcolor="rgba(220, 38, 38, 0.08)",
        )
    )
    vol_fig.update_layout(
        title="20-Day Rolling Annualized Volatility",
        xaxis_title="Date",
        yaxis_title="Annualized Volatility",
        yaxis_tickformat=".0%",
        height=380,
        margin=dict(l=10, r=10, t=50, b=10),
        template="plotly_white",
    )
    st.plotly_chart(vol_fig, use_container_width=True)


def render_drift_tab(df: pd.DataFrame, recent_window: int, baseline_window: int, p_threshold: float) -> None:
    st.subheader("Regime Drift Detection — Two-Sample Kolmogorov-Smirnov Test")
    st.caption(
        f"Comparing the most recent {recent_window} trading days of daily returns "
        f"against the prior {baseline_window}-day baseline distribution."
    )

    result = compute_drift(
        df.set_index("trade_date")["daily_return"],
        recent_window=recent_window,
        baseline_window=baseline_window,
        p_value_threshold=p_threshold,
    )

    if result.insufficient_data:
        st.info(result.message)
        return

    if result.drift_detected:
        st.error(
            f"⚠️ **Regime Drift Alert** — p-value = {result.p_value:.4f} "
            f"(< {p_threshold}). {result.message}"
        )
    else:
        st.success(
            f"✅ No regime drift detected — p-value = {result.p_value:.4f} "
            f"(≥ {p_threshold})."
        )

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("KS Statistic", f"{result.statistic:.4f}")
    m2.metric("p-value", f"{result.p_value:.4f}")
    m3.metric("Recent Mean Return", f"{result.recent_mean * 100:.3f}%")
    m4.metric("Baseline Mean Return", f"{result.baseline_mean * 100:.3f}%")

    hist_fig = go.Figure()
    recent_series = df["daily_return"].dropna().iloc[-recent_window:]
    baseline_series = df["daily_return"].dropna().iloc[
        -(recent_window + baseline_window) : -recent_window
    ]
    hist_fig.add_trace(
        go.Histogram(x=baseline_series, name="Baseline", opacity=0.6, marker_color="#94a3b8")
    )
    hist_fig.add_trace(
        go.Histogram(x=recent_series, name="Recent", opacity=0.6, marker_color="#dc2626")
    )
    hist_fig.update_layout(
        title="Return Distribution: Recent vs. Baseline",
        barmode="overlay",
        xaxis_title="Daily Return",
        yaxis_title="Frequency",
        height=380,
        margin=dict(l=10, r=10, t=50, b=10),
        template="plotly_white",
    )
    st.plotly_chart(hist_fig, use_container_width=True)


def render_raw_data_tab(df: pd.DataFrame) -> None:
    st.dataframe(
        df.sort_values("trade_date", ascending=False),
        use_container_width=True,
        height=500,
    )
    csv_bytes = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Download CSV",
        data=csv_bytes,
        file_name="finpulse_export.csv",
        mime="text/csv",
    )


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main() -> None:
    st.title("📈 FinPulse — Market Data Pipeline & Drift Monitor")
    st.caption(
        "Automated OHLCV ingestion, rolling volatility, and KS-test-based "
        "statistical regime drift detection."
    )

    try:
        engine = get_engine()
    except Exception:
        st.error(
            "Could not connect to the PostgreSQL database after multiple retries. "
            "Please verify the `db` service is healthy and check container logs."
        )
        st.stop()

    tickers = get_available_tickers(engine)

    with st.sidebar:
        st.header("Configuration")
        if not tickers:
            st.warning(
                "No data found yet. The ingestor may still be fetching its "
                "first batch — try refreshing in a moment."
            )
            st.stop()

        selected_ticker = st.selectbox("Ticker", options=tickers, index=0)

        st.divider()
        st.subheader("Drift Test Parameters")
        recent_window = st.slider("Recent window (days)", 5, 60, int(os.getenv("DRIFT_RECENT_WINDOW", "20")))
        baseline_window = st.slider(
            "Baseline window (days)", 20, 180, int(os.getenv("DRIFT_BASELINE_WINDOW", "60"))
        )
        p_threshold = st.number_input(
            "Significance threshold (p-value)",
            min_value=0.01,
            max_value=0.20,
            value=float(os.getenv("DRIFT_P_VALUE_THRESHOLD", "0.05")),
            step=0.01,
        )

        if st.button("🔄 Refresh Data"):
            st.cache_data.clear()
            st.rerun()

    df = load_ticker_data(engine, selected_ticker)

    if df.empty:
        st.warning(f"No records found for ticker '{selected_ticker}' yet.")
        st.stop()

    st.subheader(f"Summary — {selected_ticker}")
    render_metric_cards(df)

    tab_overview, tab_drift, tab_raw = st.tabs(
        ["📊 Price & Volatility", "🚨 Drift Analysis", "🗂️ Raw Data"]
    )
    with tab_overview:
        render_price_volatility_tab(df)
    with tab_drift:
        render_drift_tab(df, recent_window, baseline_window, p_threshold)
    with tab_raw:
        render_raw_data_tab(df)


if __name__ == "__main__":
    main()
