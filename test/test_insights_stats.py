import math

import pytest

from artmind.insights import stats


@pytest.mark.parametrize("period, expected", [("year", "2024"), ("quarter", "2024-Q2"), ("month", "2024-05")])
def test_period_of(period, expected):
    assert stats.period_of("2024-05-17", period) == expected


def test_period_of_rejects_unknown_period():
    with pytest.raises(ValueError):
        stats.period_of("2024-05-17", "week")


def test_days_between_and_median_gap():
    assert stats.days_between("2024-01-01", "2024-01-31") == 30
    assert stats.median_gap_days(["2024-01-01", "2024-01-11", "2024-01-21", "2024-03-01"]) == 10.0
    assert stats.median_gap_days(["2024-01-01"]) == 0.0


def test_theil_sen_is_robust_to_one_outlier():
    assert stats.theil_sen_slope([1, 2, 3, 4, 100]) == 1.0
    assert stats.theil_sen_slope([5]) == 0.0


def test_support_weight_and_rate():
    assert stats.support_weight(30) == 1.0
    assert stats.support_weight(120) == 1.0
    assert math.isclose(stats.support_weight(7.5), 0.5)
    assert stats.rate_per_100(5, 50) == 10.0
    assert stats.rate_per_100(5, 0) == 0.0
