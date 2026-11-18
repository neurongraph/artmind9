"""Winner selection (spec 2026-11-15 §6.2, §1.5; plan question 10). Synthetic observations."""
import random

from artmind.projection import merge_observations


def _o(i, value, valid_from, doc_valid_from, precision="day", kind="recurrent", **extra):
    return {"id": f"o{i}", "key": "rate|RATE|b", "canonical_name": "Rate", "entity_class": "RATE", "_domain": "b",
            "_kind": kind, "rate": value, "_valid_from": valid_from, "_doc_valid_from": doc_valid_from,
            "_valid_precision": precision, "doc_version": 1, **extra}


def test_a_later_fact_in_an_older_document_wins():
    merged = merge_observations([_o(1, "4.5", "2026-03-01", "2026-01-10"), _o(2, "4.7", "2026-02-01", "2026-02-15")])
    assert merged["props"]["rate"] == "4.5" and merged["props"]["_valid_from"] == "2026-03-01"


def test_a_default_date_never_beats_a_real_one():
    merged = merge_observations([_o(1, "4.5", "2026-11-15", "2026-11-15", precision="none"),
                                 _o(2, "4.7", "2026-02-01", "2026-02-01")])
    assert merged["props"]["rate"] == "4.7" and merged["props"]["_valid_from"] == "2026-02-01"


def test_two_default_dates_fall_back_to_the_latest_document():
    merged = merge_observations([_o(1, "4.5", "2026-11-15", "2026-11-15", precision="none"),
                                 _o(2, "4.7", "2026-11-16", "2026-11-16", precision="none")])
    assert merged["props"]["rate"] == "4.7"


def test_the_same_fact_date_is_broken_by_the_latest_document():
    merged = merge_observations([_o(1, "4.5", "2026-03-01", "2026-03-20"), _o(2, "4.7", "2026-03-01", "2026-03-05")])
    assert merged["props"]["rate"] == "4.5"


def test_occurrent_classes_keep_the_document_order():
    merged = merge_observations([_o(1, "a", "2026-03-01", "2026-01-10", kind="occurrent"),
                                 _o(2, "b", "2026-02-01", "2026-02-15", kind="occurrent")])
    assert merged["props"]["rate"] == "b"
    assert [c["property"] for c in merged["conflicts"]] == ["rate"]


def test_an_ended_later_fact_still_wins_and_carries_its_end():
    """The sort ignores `_valid_to`: the latest fact is in force until its own end, which the
    Entity then shows (spec §7)."""
    merged = merge_observations([
        _o(1, "4.5", "2026-03-01", "2026-03-01", _valid_to="2026-06-01", _valid_to_by="doc:b"),
        _o(2, "4.7", "2026-02-01", "2026-02-01"),
    ])
    assert merged["props"]["rate"] == "4.5" and merged["props"]["_valid_to"] == "2026-06-01"


def test_an_ended_earlier_fact_loses_to_an_open_later_one():
    merged = merge_observations([
        _o(1, "4.7", "2026-02-01", "2026-02-01", _valid_to="2026-03-01", _valid_to_by="doc:b"),
        _o(2, "4.5", "2026-03-01", "2026-03-01"),
    ])
    assert merged["props"]["rate"] == "4.5" and "_valid_to" not in merged["props"]


def test_a_later_fact_varies_over_time_without_a_conflict():
    merged = merge_observations([_o(1, "4.5", "2026-03-01", "2026-01-10"), _o(2, "4.7", "2026-02-01", "2026-02-15")])
    assert merged["temporal_props"] == ["rate"] and merged["conflicts"] == []


def test_a_full_tie_is_decided_by_id_whatever_the_input_order():
    observations = [_o(i, f"v{i}", "2026-03-01", "2026-03-01") for i in range(1, 6)]
    winners = set()
    for seed in range(10):
        shuffled = observations[:]
        random.Random(seed).shuffle(shuffled)
        winners.add(merge_observations(shuffled)["props"]["rate"])
    assert winners == {"v5"}


def test_two_default_dated_values_conflict_rather_than_vary():
    merged = merge_observations([_o(1, "4.5", "2026-11-15", "2026-11-15", precision="none"),
                                 _o(2, "4.7", "2026-11-16", "2026-11-16", precision="none")])
    assert merged["temporal_props"] == [] and [c["property"] for c in merged["conflicts"]] == ["rate"]


def test_a_default_dated_value_against_a_real_date_still_varies():
    merged = merge_observations([_o(1, "4.5", "2026-11-15", "2026-11-15", precision="none"),
                                 _o(2, "4.7", "2026-02-01", "2026-02-01")])
    assert merged["temporal_props"] == ["rate"] and merged["conflicts"] == []
