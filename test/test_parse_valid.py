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


@pytest.mark.parametrize("value, expected", [
    ("2020-01-01 to 9999-12-31", ValidTime("2020-01-01", None, "day", None)),
    ("9999 to 9999", ValidTime("9999-01-01", None, "year", None)),
    ("9999-12-30/31", ValidTime("9999-12-30", None, "day", None)),
    ("9999-12-31", ValidTime("9999-12-31", None, "day", None)),
])
def test_an_end_at_the_open_end_sentinel_is_open_and_never_raises(value, expected):
    assert parse_valid(value) == expected


@pytest.mark.parametrize("value", ["9999-12-31", "9999-12", "9999"])
def test_implied_end_past_the_last_date_is_open(value):
    assert parse_valid(value).implied_end is None


@pytest.mark.parametrize("value, expected", [
    ("2026-03-15 10:00 - 2026-03-16 12:00", ValidTime("2026-03-15", "2026-03-17", "day", "10:00")),
    ("2026-03-15T10:00 to 2026-03-15T12:00", ValidTime("2026-03-15", "2026-03-16", "day", "10:00")),
    ("2026-03-15 - 2026-03-15T23:00", ValidTime("2026-03-15", "2026-03-16", "day", "23:00")),
    ("2026-03-15 10:30 TO 2026-03-16", ValidTime("2026-03-15", "2026-03-17", "day", "10:30")),
    ("2026-03-15T10:30:00Z", ValidTime("2026-03-15", None, "day", "10:30:00Z")),
    ("2026-03-15 12:00 EST", ValidTime("2026-03-15", None, "day", "12:00 EST")),
])
def test_timed_values_and_ranges(value, expected):
    assert parse_valid(value) == expected


def test_residual_text_keeps_its_order():
    assert parse_valid("2026-03-15 at 10:30").time_of_day == "at 10:30"


@pytest.mark.parametrize("value", ["1500 units", "2024 budget", "0000", "0000 to 0001",
                                   "2026-03-15 and 2026-03-20"])
def test_prose_after_a_year_or_a_second_date_is_no_date(value):
    assert parse_valid(value) is None


def test_prose_after_a_month_is_kept():
    assert parse_valid("2026-03 roughly") == ValidTime("2026-03-01", None, "month", "roughly")


@pytest.mark.parametrize("value, start, precision", [
    ("1 Jan 2026", "2026-01-01", "day"), ("Sept 2026", "2026-09-01", "month"),
    ("Mar 2026", "2026-03-01", "month"), ("Dec 5, 2026", "2026-12-05", "day"),
])
def test_abbreviated_month_names(value, start, precision):
    got = parse_valid(value)
    assert (got.start, got.precision) == (start, precision)
