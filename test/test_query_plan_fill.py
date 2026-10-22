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
