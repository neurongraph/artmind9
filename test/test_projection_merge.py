"""The pure projection merge: winner selection, merge by shape, and the
conflict-vs-temporal decision.

These are the assertions that a mocked Neo4j session cannot make for us. A
bare `MagicMock()` returns a truthy result for any Cypher, so logic expressed
in a query is logic no test checks; everything here is a pure function over
dicts and every row is checked on its value.
"""
import pytest

from artmind.projection import affected_keys, merge_observations


def obs(**kw):
    """An observation with the fields the merge actually reads.

    Note the default `doc_version`: **1**. Every document in a healthy vault
    sits at version 1, so any implementation that orders by `doc_version`
    finds every observation tied and cannot pick a winner.
    """
    base = {
        "id": kw.pop("id", None) or f"obs_{kw.get('doc_id', 'd')}_{kw.get('name', 'n')}",
        "name": "SmartSaver Account Tier 2 Rate",
        "canonical_name": "SmartSaver Account Tier 2 Rate",
        "entity_class": "RATE_ENTRY",
        "domain": "banking.reference",
        "_status": "latest",
        "_kind": "recurrent",
        "doc_version": 1,
    }
    base.update(kw)
    return base


# ── trap 1: the winner is the latest DOCUMENT valid_from, not doc_version ────


def test_winner_is_latest_document_valid_from_with_every_doc_at_version_1():
    """The vertical slice, at the merge level. Three documents, all version 1,
    three different effective dates. March wins on date alone."""
    result = merge_observations([
        obs(doc_id="jan", _doc_valid_from="2026-01-15", _valid_from="2026-01-15", rate_value=4.70),
        obs(doc_id="mar", _doc_valid_from="2026-03-01", _valid_from="2026-03-01", rate_value=4.50),
        obs(doc_id="feb", _doc_valid_from="2026-02-01", _valid_from="2026-02-01", rate_value=4.60),
    ])
    assert result["props"]["rate_value"] == 4.50
    assert result["temporal_props"] == ["rate_value"]
    assert result["conflicts"] == []


def test_a_higher_doc_version_does_not_beat_a_later_valid_from():
    """Explicitly pins the trap: version 9 with an early date must lose to
    version 1 with a late one."""
    result = merge_observations([
        obs(doc_id="old", doc_version=9, _doc_valid_from="2026-01-15", _valid_from="2026-01-15", rate_value=4.70),
        obs(doc_id="new", doc_version=1, _doc_valid_from="2026-03-01", _valid_from="2026-03-01", rate_value=4.50),
    ])
    assert result["props"]["rate_value"] == 4.50


def test_input_order_does_not_change_the_winner():
    import itertools
    rows = [
        obs(doc_id="jan", _doc_valid_from="2026-01-15", _valid_from="2026-01-15", rate_value=4.70),
        obs(doc_id="feb", _doc_valid_from="2026-02-01", _valid_from="2026-02-01", rate_value=4.60),
        obs(doc_id="mar", _doc_valid_from="2026-03-01", _valid_from="2026-03-01", rate_value=4.50),
    ]
    for permutation in itertools.permutations(rows):
        assert merge_observations(list(permutation))["props"]["rate_value"] == 4.50


# ── trap 8: merge by shape, and no string concatenation anywhere ────────────


def test_scalars_take_a_winner_and_are_never_unioned():
    result = merge_observations([
        obs(doc_id="a", _doc_valid_from="2026-01-01", _valid_from="2026-01-01", rate_value=3.75),
        obs(doc_id="b", _doc_valid_from="2026-02-01", _valid_from="2026-02-01", rate_value=4.60),
        obs(doc_id="c", _doc_valid_from="2026-03-01", _valid_from="2026-03-01", rate_value=4.50),
    ])
    assert result["props"]["rate_value"] == 4.50
    assert not isinstance(result["props"]["rate_value"], list)


def test_descriptions_are_never_concatenated():
    """The `"A | B"` accretive merge produced 512 self-repeating descriptions."""
    result = merge_observations([
        obs(doc_id="a", _doc_valid_from="2026-01-01", description="The January rate."),
        obs(doc_id="b", _doc_valid_from="2026-02-01", description="The February rate."),
    ])
    description = result["props"]["description"]
    assert description == "The February rate."
    assert " | " not in description


def test_no_merged_value_anywhere_contains_the_accretion_separator():
    result = merge_observations([
        obs(doc_id="a", _doc_valid_from="2026-01-01", description="A", type="savings_rate_tier", owner="Ops"),
        obs(doc_id="b", _doc_valid_from="2026-02-01", description="B", type="variable_rate", owner="Product"),
    ])
    for key, value in result["props"].items():
        if isinstance(value, str):
            assert " | " not in value, f"{key} was concatenated"


def test_lists_union_and_never_conflict():
    result = merge_observations([
        obs(doc_id="a", _doc_valid_from="2026-01-01", _valid_from="2026-01-01",
            regulatory_basis=["FCA Handbook", "CONC"]),
        obs(doc_id="b", _doc_valid_from="2026-02-01", _valid_from="2026-02-01",
            regulatory_basis=["CONC", "BCOBS"]),
    ])
    assert result["props"]["regulatory_basis"] == ["FCA Handbook", "CONC", "BCOBS"]
    assert result["conflicts"] == []
    assert result["temporal_props"] == []


def test_a_property_is_a_list_property_if_any_observation_asserts_a_list():
    """Shape is decided by the data, not by a declaration — one observation
    emitting a bare string must not turn a list property into a scalar."""
    result = merge_observations([
        obs(doc_id="a", _doc_valid_from="2026-01-01", audience="Customers"),
        obs(doc_id="b", _doc_valid_from="2026-02-01", audience=["Staff", "Media"]),
    ])
    assert result["props"]["audience"] == ["Customers", "Staff", "Media"]


def test_type_takes_the_winner():
    result = merge_observations([
        obs(doc_id="a", _doc_valid_from="2026-01-01", _valid_from="2026-01-01", type="savings_rate_tier"),
        obs(doc_id="b", _doc_valid_from="2026-02-01", _valid_from="2026-02-01", type="variable_rate"),
    ])
    assert result["props"]["type"] == "variable_rate"


# ── name, aliases, context ──────────────────────────────────────────────────


def test_name_comes_from_canonical_name_not_the_verbatim_name():
    """Raw wordings live on the observations and flow into aliases; the
    Entity's name is the reconciled one."""
    result = merge_observations([
        obs(doc_id="a", _doc_valid_from="2026-01-01",
            name="SmartSaver Account Tier 2 Rate — 4.70% AER (£10,001–£50,000), effective 2026-01-15",
            canonical_name="SmartSaver Account Tier 2 Rate"),
        obs(doc_id="b", _doc_valid_from="2026-02-01",
            name="£10,001–£50,000",
            canonical_name="SmartSaver Account Tier 2 Rate"),
    ])
    assert result["props"]["name"] == "SmartSaver Account Tier 2 Rate"


def test_name_is_the_longest_canonical_name_tie_broken_by_frequency():
    result = merge_observations([
        obs(doc_id="a", _doc_valid_from="2026-01-01", canonical_name="SmartSaver Rate"),
        obs(doc_id="b", _doc_valid_from="2026-02-01", canonical_name="SmartSaver Account Tier 2 Rate"),
    ])
    assert result["props"]["name"] == "SmartSaver Account Tier 2 Rate"

    tie = merge_observations([
        obs(id="1", doc_id="a", _doc_valid_from="2026-01-01", canonical_name="Rate A"),
        obs(id="2", doc_id="b", _doc_valid_from="2026-02-01", canonical_name="Rate B"),
        obs(id="3", doc_id="c", _doc_valid_from="2026-03-01", canonical_name="Rate B"),
    ])
    assert tie["props"]["name"] == "Rate B"


def test_aliases_keep_a_raw_name_that_merely_shares_the_key():
    """The measurement-laden wording is what a document actually said, and it
    is the most informative string in the set — excluding it because it
    normalizes to the same key would throw that away."""
    long_name = "SmartSaver Account Tier 2 Rate — 4.70% AER, effective 2026-01-15"
    result = merge_observations([
        obs(doc_id="a", _doc_valid_from="2026-01-01",
            name=long_name, canonical_name="SmartSaver Account Tier 2 Rate"),
        obs(doc_id="b", _doc_valid_from="2026-02-01",
            name="SmartSaver Account Tier 2 Rate", canonical_name="SmartSaver Account Tier 2 Rate"),
    ])
    assert long_name in result["props"]["aliases"]
    assert "SmartSaver Account Tier 2 Rate" not in result["props"]["aliases"]


def test_aliases_union_raw_names_and_declared_aliases_excluding_the_chosen_name():
    result = merge_observations([
        obs(doc_id="a", _doc_valid_from="2026-01-01",
            name="Tier 2 Rate", canonical_name="SmartSaver Account Tier 2 Rate",
            aliases=["SAV-001 T2"]),
        obs(doc_id="b", _doc_valid_from="2026-02-01",
            name="SmartSaver Account Tier 2 Rate", canonical_name="SmartSaver Account Tier 2 Rate"),
    ])
    assert "Tier 2 Rate" in result["props"]["aliases"]
    assert "SAV-001 T2" in result["props"]["aliases"]
    assert "SmartSaver Account Tier 2 Rate" not in result["props"]["aliases"]


def test_context_unions_and_is_capped():
    from artmind.projection import CONTEXT_CAP
    rows = [
        obs(id=f"o{i}", doc_id=f"d{i}", _doc_valid_from=f"2026-01-{i + 1:02d}",
            context=[f"snippet {i}", "shared snippet"])
        for i in range(CONTEXT_CAP + 5)
    ]
    result = merge_observations(rows)
    assert len(result["props"]["context"]) == CONTEXT_CAP
    assert result["props"]["context"].count("shared snippet") == 1


# ── trap 9: conflict vs temporal variation, by kind × valid_from ────────────


def test_recurrent_with_different_valid_from_is_temporal_variation():
    result = merge_observations([
        obs(doc_id="a", _kind="recurrent", _doc_valid_from="2026-01-01", _valid_from="2026-01-01", rate_value=4.70),
        obs(doc_id="b", _kind="recurrent", _doc_valid_from="2026-02-01", _valid_from="2026-02-01", rate_value=4.60),
    ])
    assert result["temporal_props"] == ["rate_value"]
    assert result["conflicts"] == []
    assert result["props"]["_temporal_props"] == ["rate_value"]


def test_recurrent_with_the_same_valid_from_is_a_conflict():
    result = merge_observations([
        obs(id="1", doc_id="a", _kind="recurrent", _doc_valid_from="2026-01-01", _valid_from="2026-01-01", rate_value=4.70),
        obs(id="2", doc_id="b", _kind="recurrent", _doc_valid_from="2026-02-01", _valid_from="2026-01-01", rate_value=4.60),
    ])
    assert result["temporal_props"] == []
    assert [c["property"] for c in result["conflicts"]] == ["rate_value"]


def test_occurrent_disagreement_is_always_a_conflict_even_across_dates():
    """A completed event's attributes do not drift. Two sources disagreeing
    about a past incident is a defect in the corpus, not history."""
    result = merge_observations([
        obs(doc_id="a", _kind="occurrent", _doc_valid_from="2026-01-01", _valid_from="2026-01-01", attendee_count=12),
        obs(doc_id="b", _kind="occurrent", _doc_valid_from="2026-02-01", _valid_from="2026-02-01", attendee_count=15),
    ])
    assert result["temporal_props"] == []
    assert [c["property"] for c in result["conflicts"]] == ["attendee_count"]


def test_agreement_is_never_a_conflict_for_either_kind():
    for kind in ("recurrent", "occurrent"):
        result = merge_observations([
            obs(doc_id="a", _kind=kind, _doc_valid_from="2026-01-01", _valid_from="2026-01-01", rate_value=4.50),
            obs(doc_id="b", _kind=kind, _doc_valid_from="2026-02-01", _valid_from="2026-02-01", rate_value=4.50),
        ])
        assert result["conflicts"] == []
        assert result["temporal_props"] == []


def test_a_same_instant_disagreement_wins_over_temporal_variation():
    """Three observations: two share an instant and disagree, one is later.
    The same-instant disagreement makes it a conflict, not history."""
    result = merge_observations([
        obs(id="1", doc_id="a", _kind="recurrent", _doc_valid_from="2026-01-01", _valid_from="2026-01-01", rate_value=4.70),
        obs(id="2", doc_id="b", _kind="recurrent", _doc_valid_from="2026-01-15", _valid_from="2026-01-01", rate_value=4.65),
        obs(id="3", doc_id="c", _kind="recurrent", _doc_valid_from="2026-02-01", _valid_from="2026-02-01", rate_value=4.60),
    ])
    assert [c["property"] for c in result["conflicts"]] == ["rate_value"]
    assert result["temporal_props"] == []


def test_a_conflicted_property_still_gets_the_winners_value():
    """A resolvable answer beats no answer; the :Conflict carries the dispute."""
    result = merge_observations([
        obs(id="1", doc_id="a", _kind="occurrent", _doc_valid_from="2026-01-01", _valid_from="2026-01-01", attendee_count=12),
        obs(id="2", doc_id="b", _kind="occurrent", _doc_valid_from="2026-02-01", _valid_from="2026-02-01", attendee_count=15),
    ])
    assert result["props"]["attendee_count"] == 15


def test_conflict_records_every_side_with_its_provenance():
    result = merge_observations([
        obs(id="obs-a", doc_id="doc-a", _kind="occurrent", _doc_valid_from="2026-01-01", _valid_from="2026-01-01", attendee_count=12),
        obs(id="obs-b", doc_id="doc-b", _kind="occurrent", _doc_valid_from="2026-02-01", _valid_from="2026-02-01", attendee_count=15),
    ])
    values = result["conflicts"][0]["values"]
    assert {v["value"] for v in values} == {12, 15}
    assert {v["observation_id"] for v in values} == {"obs-a", "obs-b"}
    assert {v["doc_id"] for v in values} == {"doc-a", "doc-b"}


# ── determinism ─────────────────────────────────────────────────────────────


def test_the_entity_id_is_the_hash_of_the_key_and_is_stable():
    from artmind.observations import aggregate_key, entity_id
    rows = [obs(doc_id="a", _doc_valid_from="2026-01-01", rate_value=4.5)]
    result = merge_observations(rows)
    expected = entity_id(aggregate_key("SmartSaver Account Tier 2 Rate", "RATE_ENTRY", "banking.reference"))
    assert result["props"]["id"] == expected
    assert merge_observations(rows)["props"]["id"] == expected


def test_merging_is_a_pure_function_and_does_not_mutate_its_input():
    rows = [
        obs(doc_id="a", _doc_valid_from="2026-01-01", rate_value=4.70),
        obs(doc_id="b", _doc_valid_from="2026-02-01", rate_value=4.60),
    ]
    before = [dict(r) for r in rows]
    merge_observations(rows)
    assert rows == before


def test_no_observations_raises_rather_than_writing_an_empty_entity():
    with pytest.raises(ValueError):
        merge_observations([])


def test_system_keys_never_leak_onto_the_entity_as_domain_properties():
    result = merge_observations([
        obs(doc_id="a", _doc_valid_from="2026-01-01", chunk_id="c1", rate_value=4.5)
    ])
    for leaked in ("chunk_id", "doc_id", "doc_version", "_status", "canonical_name", "_doc_valid_from"):
        assert leaked not in result["props"]


# ── description / synthesis seam ────────────────────────────────────────────


def test_description_falls_back_to_the_winner_without_a_synthesis():
    result = merge_observations([
        obs(doc_id="a", _doc_valid_from="2026-01-01", description="January wording."),
        obs(doc_id="b", _doc_valid_from="2026-02-01", description="February wording."),
    ])
    assert result["props"]["description"] == "February wording."
    assert result["props"]["_description_source"] == "observation"


def test_a_current_synthesis_is_used():
    rows = [obs(id="o1", doc_id="a", _doc_valid_from="2026-01-01", description="raw")]
    current_hash = merge_observations(rows)["props"]["_observation_set_hash"]
    result = merge_observations(rows, synthesis={
        "text": "A coherent passage.", "observation_set_hash": current_hash, "observation_ids": ["o1"],
    })
    assert result["props"]["description"] == "A coherent passage."
    assert result["props"]["_description_source"] == "synthesis"
    assert "_description_stale" not in result["props"]


def test_a_grown_set_keeps_the_synthesis_but_marks_it_stale():
    rows = [
        obs(id="o1", doc_id="a", _doc_valid_from="2026-01-01", description="raw a"),
        obs(id="o2", doc_id="b", _doc_valid_from="2026-02-01", description="raw b"),
    ]
    result = merge_observations(rows, synthesis={
        "text": "A coherent passage.", "observation_set_hash": "stale", "observation_ids": ["o1"],
    })
    assert result["props"]["description"] == "A coherent passage."
    assert result["props"]["_description_stale"] is True


def test_a_shrunk_set_discards_the_synthesis():
    """It may assert content the corpus has since retracted."""
    rows = [obs(id="o1", doc_id="a", _doc_valid_from="2026-01-01", description="raw a")]
    result = merge_observations(rows, synthesis={
        "text": "Mentions the retracted February rate.",
        "observation_set_hash": "stale",
        "observation_ids": ["o1", "o2"],
    })
    assert result["props"]["description"] == "raw a"
    assert result["props"]["_description_source"] == "observation"


# ── trap 6: affected keys are a union of four sets ─────────────────────────


K1 = ("smartsaver account tier 2 rate", "RATE_ENTRY", "banking.reference")
K2 = ("smartsaver account tier 3 rate", "RATE_ENTRY", "banking.reference")
K3 = ("overdraft rate", "RATE_ENTRY", "banking.reference")
K4 = ("unrelated thing", "PRODUCT", "banking.policy")


def test_incoming_and_prior_are_both_included():
    """The prior version's keys are set 2, and missing them is how a renamed
    entity leaves an orphan behind."""
    assert affected_keys(incoming=[K1], prior=[K2]) == {K1, K2}


def test_a_rename_between_versions_includes_the_abandoned_key():
    assert K2 in affected_keys(incoming=[K1], prior=[K1, K2])


def test_retired_document_keys_are_included():
    assert affected_keys(retired=[K3]) == {K3}


def test_a_same_as_group_touching_either_set_pulls_in_the_whole_group():
    assert affected_keys(incoming=[K1], same_as_groups=[[K1, K2, K3]]) == {K1, K2, K3}


def test_a_same_as_group_touching_neither_set_is_left_alone():
    assert affected_keys(incoming=[K1], same_as_groups=[[K2, K3]]) == {K1}


def test_all_four_sets_union():
    assert affected_keys(
        incoming=[K1], prior=[K2], retired=[K3], same_as_groups=[[K3, K4]]
    ) == {K1, K2, K3, K4}


def test_no_inputs_is_an_empty_set_not_an_error():
    assert affected_keys() == set()
