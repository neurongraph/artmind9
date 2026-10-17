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


def period_start(label: str) -> str:
    """'2024' -> '2024-01-01', '2024-Q3' -> '2024-07-01', '2024-05' -> '2024-05-01'."""
    if "-Q" in label:
        year, q = label.split("-Q")
        return f"{year}-{(int(q) - 1) * 3 + 1:02d}-01"
    if len(label) == 7:
        return f"{label}-01"
    return f"{label}-01-01"


def two_proportion_z(x1: int, n1: int, x2: int, n2: int) -> float | None:
    """z for 'share x1/n1 differs from x2/n2'; None when undefined (an empty side, or a pooled share of 0 or 1)."""
    if n1 <= 0 or n2 <= 0:
        return None
    pooled = (x1 + x2) / (n1 + n2)
    if pooled <= 0 or pooled >= 1:
        return None
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    return (x1 / n1 - x2 / n2) / se


def levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def bh_qvalues(pvalues: list[float]) -> list[float]:
    """Benjamini-Hochberg adjusted p-values (q-values), in the input order."""
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    q = [0.0] * m
    running = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        running = min(running, pvalues[i] * m / rank)
        q[i] = running
    return q
