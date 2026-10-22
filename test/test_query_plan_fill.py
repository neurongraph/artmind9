"""Scalar view params from the question (spec 2026-10-22 §4.2 view_match)."""

from datetime import date

from artmind.kg_views.model import ParamSpec
from artmind.query_plan import fill

TODAY = date(2026, 10, 22)


def _fill(spec, question):
    return fill.fill_scalar(spec, question, TODAY, set())


def test_an_int_in_range_is_taken_and_out_of_range_numbers_are_not():
    year = ParamSpec("year", "int", min=1900, max=2100)
    days = ParamSpec("merge_within_days", "int", default=10, min=0, max=60)
    used: set = set()
    assert fill.fill_scalar(year, "trips in 2025", TODAY, used)["value"] == 2025
    assert fill.fill_scalar(days, "trips in 2025", TODAY, used) == {"type": "int", "via": "default", "value": 10}


def test_relative_years():
    year = ParamSpec("year", "int", min=1900, max=2100)
    assert _fill(year, "what happened last year")["value"] == 2025
    assert _fill(year, "anything this year?")["value"] == 2026


def test_dates_in_three_spellings():
    when = ParamSpec("on", "date")
    assert _fill(when, "on 2026-01-15")["value"] == "2026-01-15"
    assert _fill(when, "on 15 January 2026")["value"] == "2026-01-15"
    assert _fill(when, "in March 2026")["value"] == "2026-03-01"


def test_a_string_is_only_ever_a_quoted_span():
    text = ParamSpec("phrase", "string")
    assert _fill(text, 'notes saying "low tide"')["value"] == "low tide"
    assert _fill(text, "notes saying low tide") is None


def test_bool_and_list_use_their_default_or_are_missing():
    assert _fill(ParamSpec("flag", "bool", default=False), "anything") == {"type": "bool", "via": "default", "value": False}
    assert _fill(ParamSpec("flag", "bool"), "anything") is None
    assert _fill(ParamSpec("names", "list", items="string"), "anything") is None


def test_combinations_are_capped():
    groups = {"a": [{"_id": 1}, {"_id": 2}], "b": [{"_id": 3}, {"_id": 4}, {"_id": 5}]}
    assert len(fill.combinations(groups, cap=6)) == 6
    assert fill.combinations(groups, cap=4) is None
    assert fill.combinations({}, cap=4) == [{}]


def test_numbers_before_sentence_punctuation_fill_but_dotted_versions_do_not():
    year = ParamSpec("year", "int", min=1900, max=2100)
    top = ParamSpec("limit", "int", min=1, max=100)
    version = ParamSpec("version", "int")
    assert _fill(year, "Show trips in 2025.")["value"] == 2025
    assert _fill(top, "top 5.")["value"] == 5
    assert _fill(top, "top 5, please")["value"] == 5
    assert _fill(version, "version 1.2.3") is None


def test_ints_skip_decimals_and_floats_take_them():
    count = ParamSpec("count", "int")
    ratio = ParamSpec("ratio", "float")
    assert _fill(count, "above 2.5 then 7")["value"] == 7
    assert _fill(ratio, "above 2.5")["value"] == 2.5
    assert _fill(ratio, "above 3")["value"] == 3.0


def test_thousands_separators_and_huge_digit_runs():
    limit = ParamSpec("limit", "int")
    assert _fill(limit, "top 2,500")["value"] == 2500
    assert _fill(limit, "top 1,234,567 rows")["value"] == 1234567
    assert _fill(limit, "ids 1,2,3") is None
    assert _fill(limit, "x " + "9" * 5000) is None


def test_negative_numbers_are_not_filled():
    value = ParamSpec("delta", "int")
    assert _fill(value, "change of -5") is None


def test_today_is_word_bounded_and_tried_after_explicit_dates():
    since = ParamSpec("since", "date")
    until = ParamSpec("until", "date")
    used: set = set()
    question = "from 2025-05-01 until today"
    assert fill.fill_scalar(since, question, TODAY, used)["value"] == "2025-05-01"
    assert fill.fill_scalar(until, question, TODAY, used)["value"] == "2026-10-22"
    assert _fill(since, "Until TODAY") ["value"] == "2026-10-22"
    assert _fill(since, "the todays list") is None
    used = {(0, 5)}
    assert fill.fill_scalar(since, "today", TODAY, used) is None


def test_relative_years_need_a_range_that_admits_a_year_and_no_explicit_number():
    year = ParamSpec("year", "int", min=1900, max=2100)
    limit = ParamSpec("limit", "int")
    assert _fill(year, "in 2023 compared to last year")["value"] == 2023
    assert _fill(year, "top 5 trips Last Year")["value"] == 2025
    assert _fill(year, "my forelast yearly list") is None
    assert _fill(limit, "trips last year") is None
    assert _fill(ParamSpec("limit", "int", min=1, max=100), "trips last year") is None
    used: set = set()
    assert fill.fill_scalar(year, "last year vs this year", TODAY, used)["value"] == 2025
    assert fill.fill_scalar(year, "last year vs this year", TODAY, used)["value"] == 2026


def test_an_invalid_day_does_not_fall_back_to_the_month():
    when = ParamSpec("on", "date")
    assert _fill(when, "on 30 February 2026") is None
    assert _fill(when, "on 2026-02-30") is None
    assert _fill(when, "30 February 2026 or March 2026")["value"] == "2026-03-01"
