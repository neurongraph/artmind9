"""Small, dependency-free statistics shared by detectors."""

from __future__ import annotations

import math
import statistics
from datetime import date


def period_of(day: str, period: str) -> str:
    """'2024-05-17' -> '2024' | '2024-Q2' | '2024-05'. Malformed days raise ValueError."""
    d = date.fromisoformat(day[:10])
    if period == "year":
        return f"{d.year:04d}"
    if period == "quarter":
        return f"{d.year:04d}-Q{(d.month - 1) // 3 + 1}"
    if period == "month":
        return f"{d.year:04d}-{d.month:02d}"
    raise ValueError(f"unknown period {period!r}")


def days_between(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def median_gap_days(days: list[str] | tuple[str, ...]) -> float:
    """Median gap between consecutive distinct days; 0.0 with fewer than two days."""
    ordered = sorted({date.fromisoformat(d) for d in days})
    if len(ordered) < 2:
        return 0.0
    return float(statistics.median((b - a).days for a, b in zip(ordered, ordered[1:])))


def theil_sen_slope(values: list[float]) -> float:
    """Median of pairwise slopes: a trend that one odd period cannot swing.

    The x axis is the index of each value, so the values must be evenly spaced periods.
    """
    n = len(values)
    if n < 2:
        return 0.0
    slopes = [(values[j] - values[i]) / (j - i) for i in range(n) for j in range(i + 1, n)]
    return float(statistics.median(slopes))


def support_weight(support: float, full: float = 30) -> float:
    """Down-weights findings resting on little evidence; 1.0 at `full` and above."""
    return min(1.0, math.sqrt(max(support, 0) / full))


def rate_per_100(count: int, docs: int) -> float:
    return 100.0 * count / docs if docs else 0.0
