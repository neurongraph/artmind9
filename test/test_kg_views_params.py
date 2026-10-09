"""kg_views.params: typed binding, entity auto-resolve, @<_id> bypass."""

from unittest.mock import MagicMock, patch

import pytest

from artmind.kg_views.model import ViewError, parse_view
from artmind.kg_views.params import parse_param_args, resolve_params

CYPHER = (
    "MATCH (p:Entity {_id: $product}) WHERE p._domain IN $domains AND p.n <= $depth "
    "RETURN p._id AS _id, p.name AS name"
)


def make_spec(entity_class="Product"):
    product = {"type": "entity"}
    if entity_class:
        product["entity_class"] = entity_class
    return parse_view(
        {
            "name": "v1",
            "version": 1,
            "domains": ["finance"],
            "summary": "s",
            "examples": ["q"],
            "params": {"product": product, "depth": {"type": "int", "default": 4, "min": 1, "max": 6}},
            "cypher": CYPHER,
            "presentation": {"format": "list", "list": {"label": "name"}},
            "provenance": {"created": "2026-10-09", "origin": "authored"},
        },
        "v1",
    )


def hit(_id, name, cls="Product", obs=3):
    return {"entity": {"_id": _id, "name": name, "entity_class": cls, "observation_count": obs}, "score": 0.1}


def patched_resolve(rows):
    return patch(
        "artmind.kg_views.params.vector_query.entity_resolve", return_value={"rows": rows}
    )


def test_parse_param_args_splits_on_first_equals():
    assert parse_param_args(("a=1", "b=x=y")) == {"a": "1", "b": "x=y"}


@pytest.mark.parametrize("bad", [("novalue",), ("=x",), ("a=1", "a=2")])
def test_parse_param_args_rejects_malformed(bad):
    with pytest.raises(ViewError):
        parse_param_args(bad)


def test_single_candidate_is_accepted_and_default_applied():
    with patched_resolve([hit("e1", "Retail Mortgage")]) as resolve:
        res = resolve_params(make_spec(), {"product": "mortgage"}, ["finance"])
    resolve.assert_called_once_with(["finance"], "mortgage", 10)
    assert res.status == "ok"
    assert res.bindings == {"product": "e1", "depth": 4}
    assert res.echo["product"] == {
        "input": "mortgage", "_id": "e1", "name": "Retail Mortgage", "entity_class": "Product",
    }
    assert res.echo["depth"] == 4


def test_exact_name_wins_over_other_candidates_case_insensitively():
    rows = [hit("e1", "Mortgage Insurance"), hit("e2", "mortgage"), hit("e3", "Mortgage Broker")]
    with patched_resolve(rows):
        res = resolve_params(make_spec(), {"product": "Mortgage"}, ["finance"])
    assert res.status == "ok" and res.bindings["product"] == "e2"


def test_two_exact_name_matches_are_ambiguous():
    rows = [hit("e1", "Mortgage"), hit("e2", "Mortgage")]
    with patched_resolve(rows):
        res = resolve_params(make_spec(), {"product": "Mortgage"}, ["finance"])
    assert res.status == "needs_disambiguation"
    assert [c["_id"] for c in res.candidates] == ["e1", "e2"]
    assert res.param == "product"


def test_several_inexact_candidates_need_disambiguation_top_five():
    rows = [hit(f"e{i}", f"Mortgage {i}") for i in range(8)]
    with patched_resolve(rows):
        res = resolve_params(make_spec(), {"product": "mortgage"}, ["finance"])
    assert res.status == "needs_disambiguation"
    assert len(res.candidates) == 5
    assert set(res.candidates[0]) == {"_id", "name", "entity_class", "observation_count"}
    assert res.bindings == {}


def test_entity_class_filter_drops_other_classes():
    rows = [hit("e1", "Mortgage", cls="Regulation"), hit("e2", "Mortgage Plus", cls="Product")]
    with patched_resolve(rows):
        res = resolve_params(make_spec("Product"), {"product": "Mortgage"}, ["finance"])
    assert res.status == "ok" and res.bindings["product"] == "e2"


def test_no_candidates_is_no_match():
    with patched_resolve([hit("e1", "Mortgage", cls="Regulation")]):
        res = resolve_params(make_spec("Product"), {"product": "Mortgage"}, ["finance"])
    assert (res.status, res.param, res.input) == ("no_match", "product", "Mortgage")


def at_id_session(record):
    session = MagicMock()
    session.run.return_value.single.return_value = record
    ctx = MagicMock()
    ctx.__enter__.return_value = session
    return patch("artmind.kg_views.params.read_session", return_value=ctx), session


def test_at_id_skips_resolution_and_sends_the_id():
    record = {"_id": "ent_9", "name": "Retail Mortgage", "entity_class": "Product", "domain": "finance.retail"}
    session_patch, session = at_id_session(record)
    with session_patch, patch("artmind.kg_views.params.vector_query.entity_resolve") as resolve:
        res = resolve_params(make_spec(), {"product": "@ent_9"}, ["finance", "finance.retail"])
    resolve.assert_not_called()
    assert session.run.call_args.args[1] == {"id": "ent_9"}
    assert res.bindings["product"] == "ent_9"
    assert res.echo["product"]["input"] == "@ent_9"


@pytest.mark.parametrize(
    "record, fragment",
    [
        (None, "no entity with _id"),
        ({"_id": "x", "name": "n", "entity_class": "Product", "domain": "hr"}, "outside the requested domains"),
        ({"_id": "x", "name": "n", "entity_class": "Person", "domain": "finance"}, "expected 'Product'"),
    ],
)
def test_at_id_is_still_verified(record, fragment):
    session_patch, _ = at_id_session(record)
    with session_patch, pytest.raises(ViewError, match=fragment):
        resolve_params(make_spec(), {"product": "@x"}, ["finance"])


def test_missing_required_unknown_and_bad_values():
    with pytest.raises(ViewError, match="needs --param product"):
        resolve_params(make_spec(), {}, ["finance"])
    with pytest.raises(ViewError, match="no parameter"):
        resolve_params(make_spec(), {"product": "x", "nope": "1"}, ["finance"])
    with patched_resolve([hit("e1", "M")]), pytest.raises(ViewError, match="param 'depth'"):
        resolve_params(make_spec(), {"product": "M", "depth": "9"}, ["finance"])
