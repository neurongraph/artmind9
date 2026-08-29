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
        # No `_status` (Phase 4) — the label (:Observation vs
        # :ObservationHistory) is the only latest/history signal now; there
        # is no property left to carry.
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


def test_a_mixed_type_union_is_coerced_to_strings_not_left_to_crash_neo4j():
    """Neo4j arrays must be homogeneously typed. A property is a *list*
    property the moment ANY contributing observation asserts a list (see
    `test_a_property_is_a_list_property_if_any_observation_asserts_a_list`),
    so a bare scalar from one observation and a list from another still
    union — and if the scalar's TYPE disagrees with the list items' type
    (one chunk extracts `training_required: true`, another a list of
    descriptive strings for the same key), the union comes out mixed. This
    is an unformatted property hint, the same failure class the scorecard's
    watch list names, just surfacing here as a hard write failure instead of
    a reviewable conflict (a list property never reaches the conflict path).
    Regression for a real crash found live during the Phase 8 cutover: a
    `banking.policy` union of str/bool values raised
    `Neo.ClientError.Statement.TypeError` and rolled back the whole rebuild
    transaction for the entire domain.
    """
    result = merge_observations([
        obs(doc_id="a", _doc_valid_from="2026-01-01", training_required=True),
        obs(doc_id="b", _doc_valid_from="2026-02-01",
            training_required=["annual_fraud_awareness", "fraud_scenario_training"]),
    ])
    values = result["props"]["training_required"]
    assert values == ["True", "annual_fraud_awareness", "fraud_scenario_training"]
    assert all(isinstance(v, str) for v in values)


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


def test_a_property_can_be_BOTH_temporal_and_conflicted():
    """Three observations: two share an instant and disagree, one is later.

    The property genuinely varies over time AND is disputed at one instant.
    These are independent facts and both are recorded — recording only the
    conflict would answer "does this rate change over time?" with no, and a
    single bad extraction inside one document would erase the whole temporal
    history. This is the shape the live run hit."""
    result = merge_observations([
        obs(id="1", doc_id="a", _kind="recurrent", _doc_valid_from="2026-01-01", _valid_from="2026-01-01", rate_value=4.70),
        obs(id="2", doc_id="b", _kind="recurrent", _doc_valid_from="2026-01-15", _valid_from="2026-01-01", rate_value=4.65),
        obs(id="3", doc_id="c", _kind="recurrent", _doc_valid_from="2026-02-01", _valid_from="2026-02-01", rate_value=4.60),
    ])
    assert [c["property"] for c in result["conflicts"]] == ["rate_value"]
    assert result["temporal_props"] == ["rate_value"]


def test_a_disagreement_at_a_SINGLE_instant_is_a_conflict_only():
    """Nothing varies over time here — there is only one instant."""
    result = merge_observations([
        obs(id="1", doc_id="a", _kind="recurrent", _doc_valid_from="2026-01-01", _valid_from="2026-01-01", rate_value=4.70),
        obs(id="2", doc_id="b", _kind="recurrent", _doc_valid_from="2026-01-15", _valid_from="2026-01-01", rate_value=5.25),
    ])
    assert [c["property"] for c in result["conflicts"]] == ["rate_value"]
    assert result["temporal_props"] == []


def test_the_same_dispute_repeated_at_every_instant_is_not_temporal_variation():
    """Both instants carry the same two values, so nothing changed between
    them — it is one unresolved disagreement, twice."""
    result = merge_observations([
        obs(id="1", doc_id="a", _kind="recurrent", _doc_valid_from="2026-01-01", _valid_from="2026-01-01", rate_value=4.70),
        obs(id="2", doc_id="b", _kind="recurrent", _doc_valid_from="2026-01-02", _valid_from="2026-01-01", rate_value=5.25),
        obs(id="3", doc_id="c", _kind="recurrent", _doc_valid_from="2026-02-01", _valid_from="2026-02-01", rate_value=4.70),
        obs(id="4", doc_id="d", _kind="recurrent", _doc_valid_from="2026-02-02", _valid_from="2026-02-01", rate_value=5.25),
    ])
    assert [c["property"] for c in result["conflicts"]] == ["rate_value"]
    assert result["temporal_props"] == []


def test_an_occurrent_property_is_never_temporal_even_when_it_varies():
    """A completed event's attributes do not drift."""
    result = merge_observations([
        obs(doc_id="a", _kind="occurrent", _doc_valid_from="2026-01-01", _valid_from="2026-01-01", attendee_count=12),
        obs(doc_id="b", _kind="occurrent", _doc_valid_from="2026-02-01", _valid_from="2026-02-01", attendee_count=15),
    ])
    assert [c["property"] for c in result["conflicts"]] == ["attendee_count"]
    assert result["temporal_props"] == []


def test_an_int_and_its_string_form_are_not_a_conflict():
    """Regression for neurongraph/artmind9#13: `2` and `"2"` are the same
    fact recorded in two Python types, not a same-instant disagreement --
    `_write_conflicts()` stringifies both before storage anyway, so they
    render identically the moment anyone reads the stored conflict."""
    result = merge_observations([
        obs(id="1", doc_id="a", _kind="occurrent", _doc_valid_from="2026-01-01", _valid_from="2026-01-01", value=2),
        obs(id="2", doc_id="b", _kind="occurrent", _doc_valid_from="2026-01-01", _valid_from="2026-01-01", value="2"),
    ])
    assert result["conflicts"] == []
    assert result["props"]["value"] == "2"


def test_an_int_and_a_genuinely_different_string_still_conflict():
    """The type-blind comparison must not swallow a real disagreement."""
    result = merge_observations([
        obs(id="1", doc_id="a", _kind="occurrent", _doc_valid_from="2026-01-01", _valid_from="2026-01-01", value=2),
        obs(id="2", doc_id="b", _kind="occurrent", _doc_valid_from="2026-01-01", _valid_from="2026-01-01", value="3"),
    ])
    assert [c["property"] for c in result["conflicts"]] == ["value"]


def test_an_int_and_its_string_form_at_different_instants_is_not_temporal_variation():
    """The same type-blindness applies to the `varies_across_instants` check
    three lines below the distinctness one -- a value that merely changed
    Python type between documents, not value, has not "varied" over time."""
    result = merge_observations([
        obs(id="1", doc_id="a", _kind="recurrent", _doc_valid_from="2026-01-01", _valid_from="2026-01-01", rate_value=2),
        obs(id="2", doc_id="b", _kind="recurrent", _doc_valid_from="2026-02-01", _valid_from="2026-02-01", rate_value="2"),
    ])
    assert result["temporal_props"] == []
    assert result["conflicts"] == []


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
    assert result["props"]["_id"] == expected
    assert merge_observations(rows)["props"]["_id"] == expected


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


def test_a_disputed_instant_does_not_manufacture_temporal_variation():
    """From the live run. January's three chunks read the Tier 2 lower bound as
    10001, 10000, 10001; February and March both say 10001. The range never
    changed — one chunk misread it, and that is already a conflict.

    Comparing each instant's full value SET would see {10001, 10000} vs {10001}
    and call it variation. Comparing each instant's WINNER — the same rule the
    Entity uses to answer "what is it now" — correctly says it did not vary."""
    result = merge_observations([
        obs(id="1", doc_id="jan", _doc_valid_from="2026-01-15", _valid_from="2026-01-15", balance_min="10001"),
        obs(id="2", doc_id="jan", _doc_valid_from="2026-01-15", _valid_from="2026-01-15", balance_min="10000"),
        obs(id="3", doc_id="jan", _doc_valid_from="2026-01-15", _valid_from="2026-01-15", balance_min="10001"),
        obs(id="4", doc_id="feb", _doc_valid_from="2026-02-01", _valid_from="2026-02-01", balance_min="10001"),
        obs(id="5", doc_id="mar", _doc_valid_from="2026-03-01", _valid_from="2026-03-01", balance_min="10001"),
    ])
    assert result["temporal_props"] == [], "the boundary never changed"
    assert [c["property"] for c in result["conflicts"]] == ["balance_min"], "but January disagreed with itself"


def test_a_disputed_instant_does_not_hide_genuine_temporal_variation():
    """The converse, and the reason this is a refinement rather than a revert:
    a property that really does change each month still lands in
    `_temporal_props` even when one instant is disputed."""
    result = merge_observations([
        obs(id="1", doc_id="jan", _doc_valid_from="2026-01-15", _valid_from="2026-01-15", rate_value=4.70),
        obs(id="2", doc_id="jan", _doc_valid_from="2026-01-15", _valid_from="2026-01-15", rate_value=5.25),
        obs(id="3", doc_id="feb", _doc_valid_from="2026-02-01", _valid_from="2026-02-01", rate_value=4.60),
        obs(id="4", doc_id="mar", _doc_valid_from="2026-03-01", _valid_from="2026-03-01", rate_value=4.50),
    ])
    assert result["temporal_props"] == ["rate_value"]
    assert [c["property"] for c in result["conflicts"]] == ["rate_value"]


# ── override_key: a same-as merge unit's identity must be the canonical's,
# not whatever the unioned set's own name choice would produce ──────────────


def test_override_key_wins_over_the_unioned_sets_own_name_choice():
    """Two aliases merged by a same-as group: without an override, the LONGER
    canonical_name among the union would win (per _choose_name) and could
    diverge from the group's curated canonical. override_key must always be
    the Entity's identity when given -- a human's assertion beats the
    heuristic."""
    from artmind.observations import aggregate_key, entity_id

    canonical_key = aggregate_key("FCA", "REGULATOR", "banking.reference")
    result = merge_observations(
        [
            obs(
                id="1", doc_id="a", name="FCA", canonical_name="FCA",
                entity_class="REGULATOR", domain="banking.reference",
                _doc_valid_from="2026-01-01", _valid_from="2026-01-01",
            ),
            obs(
                id="2", doc_id="b", name="Financial Conduct Authority",
                canonical_name="Financial Conduct Authority",  # longer -- would win _choose_name
                entity_class="REGULATOR", domain="banking.reference",
                _doc_valid_from="2026-02-01", _valid_from="2026-02-01",
            ),
        ],
        override_key=canonical_key,
    )
    assert result["props"]["_id"] == entity_id(canonical_key)
    assert result["props"]["key"] == "fca|REGULATOR|banking.reference"
    # display name is still free to be the longer, more informative wording
    assert result["props"]["name"] == "Financial Conduct Authority"


def test_without_override_key_a_single_keys_merge_is_unaffected():
    """For an ordinary (non-merged) key, every observation already shares one
    stored key, so passing override_key=key is a no-op vs. the old behavior."""
    result = merge_observations([
        obs(id="1", doc_id="a", _doc_valid_from="2026-01-01", _valid_from="2026-01-01", rate_value=4.5),
    ])
    from artmind.observations import aggregate_key

    key = aggregate_key("SmartSaver Account Tier 2 Rate", "RATE_ENTRY", "banking.reference")
    assert result["props"]["key"] == "|".join(key)


# ── _plan_groups: merge within (class, domain), link across it ─────────────


def test_plan_groups_merges_same_class_and_domain():
    from artmind.projection import _plan_groups

    canonical = ("fca", "REGULATOR", "banking.reference")
    member = ("financial conduct authority", "REGULATOR", "banking.reference")
    unit_of, members_of, links = _plan_groups({canonical, member}, [[canonical, member]])
    assert unit_of[member] == canonical
    assert unit_of[canonical] == canonical
    assert set(members_of[canonical]) == {canonical, member}
    assert links == []


def test_plan_groups_links_across_domain_instead_of_merging():
    from artmind.projection import _plan_groups

    canonical = ("fca", "REGULATOR", "banking.reference")
    member = ("fca", "AUTHORITY", "banking.risk_governance")  # different class AND domain
    unit_of, members_of, links = _plan_groups({canonical, member}, [[canonical, member]])
    assert member not in unit_of
    assert canonical not in unit_of  # no merge unit at all -- pure link group
    assert links == [(member, canonical)]


def test_plan_groups_splits_a_mixed_group_by_member():
    from artmind.projection import _plan_groups

    canonical = ("fca", "REGULATOR", "banking.reference")
    merge_member = ("financial conduct authority", "REGULATOR", "banking.reference")
    link_member = ("fca", "AUTHORITY", "banking.risk_governance")
    keys = {canonical, merge_member, link_member}
    unit_of, members_of, links = _plan_groups(keys, [[canonical, merge_member, link_member]])
    assert unit_of[merge_member] == canonical
    assert set(members_of[canonical]) == {canonical, merge_member}
    assert links == [(link_member, canonical)]


def test_plan_groups_ignores_a_group_touching_none_of_the_keys():
    from artmind.projection import _plan_groups

    unrelated = [("x", "C", "d"), ("y", "C", "d")]
    unit_of, members_of, links = _plan_groups({("a", "C", "d")}, [unrelated])
    assert unit_of == {}
    assert members_of == {}
    assert links == []
