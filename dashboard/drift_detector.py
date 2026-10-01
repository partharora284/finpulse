"""
drift_detector.py
------------------
Isolated statistical module implementing regime-shift / distribution-drift
detection via the two-sample Kolmogorov-Smirnov (KS) test.

Design rationale:
  The KS test is distribution-free (non-parametric), making it well suited
  to financial returns series, which are typically fat-tailed and not
  normally distributed. We compare the empirical distribution of the most
  recent `recent_window` daily returns against a prior `baseline_window`
  of returns immediately preceding it. A statistically significant
  difference (p < threshold) is interpreted as evidence of a distributional
  regime shift (e.g. a volatility spike or trend reversal).

This module has no Streamlit or DB dependencies, so it can be unit tested
or reused independently of the dashboard UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd
from scipy.stats import ks_2samp


@dataclass
class DriftResult:
    """Structured result of a KS two-sample drift analysis."""

    statistic: float
    p_value: float
    drift_detected: bool
    threshold: float
    recent_n: int
    baseline_n: int
    recent_mean: float
    baseline_mean: float
    recent_std: float
    baseline_std: float
    insufficient_data: bool = False
    message: Optional[str] = None


def compute_drift(
    returns: pd.Series,
    recent_window: int = 20,
    baseline_window: int = 60,
    p_value_threshold: float = 0.05,
) -> DriftResult:
    """
    Run a two-sample KS test comparing the most recent `recent_window`
    daily returns against the `baseline_window` of returns immediately
    preceding them.

    Parameters
    ----------
    returns : pd.Series
        Chronologically ordered (oldest -> newest) daily return series,
        with NaNs already dropped or tolerated (they are dropped here).
    recent_window : int
        Number of most-recent observations treated as the "test" sample.
    baseline_window : int
        Number of observations immediately preceding the recent window,
        treated as the "reference" sample.
    p_value_threshold : float
        Significance level below which drift is flagged.

    Returns
    -------
    DriftResult
        Dataclass with the KS statistic, p-value, and a boolean drift flag.
        If insufficient data is available, `insufficient_data=True` and
        `statistic`/`p_value` are set to NaN-safe defaults (0.0 / 1.0).
    """
    clean_returns = returns.dropna()

    min_required = recent_window + baseline_window
    if len(clean_returns) < min_required:
        return DriftResult(
            statistic=0.0,
            p_value=1.0,
            drift_detected=False,
            threshold=p_value_threshold,
            recent_n=min(len(clean_returns), recent_window),
            baseline_n=max(0, len(clean_returns) - recent_window),
            recent_mean=float("nan"),
            baseline_mean=float("nan"),
            recent_std=float("nan"),
            baseline_std=float("nan"),
            insufficient_data=True,
            message=(
                f"Insufficient history for drift analysis: need at least "
                f"{min_required} observations, have {len(clean_returns)}."
            ),
        )

    recent_sample = clean_returns.iloc[-recent_window:]
    baseline_sample = clean_returns.iloc[-(recent_window + baseline_window) : -recent_window]

    ks_statistic, p_value = ks_2samp(recent_sample, baseline_sample)
    drift_detected = bool(p_value < p_value_threshold)

    return DriftResult(
        statistic=float(ks_statistic),
        p_value=float(p_value),
        drift_detected=drift_detected,
        threshold=p_value_threshold,
        recent_n=len(recent_sample),
        baseline_n=len(baseline_sample),
        recent_mean=float(recent_sample.mean()),
        baseline_mean=float(baseline_sample.mean()),
        recent_std=float(recent_sample.std()),
        baseline_std=float(baseline_sample.std()),
        insufficient_data=False,
        message=(
            "Statistically significant distribution shift detected."
            if drift_detected
            else "No statistically significant distribution shift detected."
        ),
    )
