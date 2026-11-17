"""`temporal.parse_valid` (spec 2026-11-15 §3.1). Invented values only."""
import pytest

from artmind.temporal import ValidTime, parse_iso, parse_valid


@pytest.mark.parametrize("value, expected", [
    ("2026-01-07", ValidTime("2026-01-07", None, "day", None)),
    ("2026-01-07 (evening)", ValidTime("2026-01-07", None, "day", "evening")),
    ("2026-07-02 09:18 UTC", ValidTime("2026-07-02", None, "day", "09:18 UTC")),
    ("2023-09-07 to 2023-09-08 (multi-day)", ValidTime("2023-09-07", "2023-09-09", "day", "multi-day")),
    ("2026-01-27/28", ValidTime("2026-01-27", "2026-01-29", "day", None)),
    ("2026-01-27 – 2026-02-03", ValidTime("2026-01-27", "2026-02-04", "day", None)),
    ("2026-01-27 - 2026-02-03", ValidTime("2026-01-27", "2026-02-04", "day", None)),
    ("from 2026-03-01 until 2026-03-05", ValidTime("2026-03-01", "2026-03-06", "day", None)),
    ("2026-06 (planned)", ValidTime("2026-06-01", None, "month", "planned")),
    ("March 2026", ValidTime("2026-03-01", None, "month", None)),
    ("2026-Q1", ValidTime("2026-01-01", None, "quarter", None)),
    ("Q3 2026", ValidTime("2026-07-01", None, "quarter", None)),
    ("H2 2026", ValidTime("2026-07-01", None, "half", None)),
    ("2026 (coming)", ValidTime("2026-01-01", None, "year", "coming")),
    ("15 March 2026", ValidTime("2026-03-15", None, "day", None)),
    ("15/03/2026", ValidTime("2026-03-15", None, "day", None)),
    ("2026-01-07 evening", ValidTime("2026-01-07", None, "day", "evening")),
])
def test_parse_valid(value, expected):
    assert parse_valid(value) == expected


def test_range_precision_is_the_coarser_end():
    got = parse_valid("2026-01-15 to 2026-03")
    assert (got.precision, got.end) == ("month", "2026-04-01")


@pytest.mark.parametrize("value", ["Ongoing", "TBD", "next review", "", None, "today", "2026-02-30"])
def test_no_date(value):
    assert parse_valid(value) is None


def test_time_of_day_is_capped():
    assert len(parse_valid("2026-01-07 (" + "x" * 80 + ")").time_of_day) == 40


@pytest.mark.parametrize("value, end", [("2026-Q1", "2026-04-01"), ("2026-02", "2026-03-01"),
                                        ("2026", "2027-01-01"), ("2026-02-03", "2026-02-04"),
                                        ("H2 2026", "2027-01-01"), ("2026-12", "2027-01-01")])
def test_implied_end_of_a_partial_date(value, end):
    assert parse_valid(value).implied_end == end


def test_parse_iso_is_the_start():
    assert parse_iso("2023-09-07 to 2023-09-08") == "2023-09-07"
    assert parse_iso("2026-Q1") == "2026-01-01"
