"""kg_views.model: every rule in spec 2026-10-09 section 4.4."""

import copy
from datetime import date

import pytest

from artmind.kg_views.model import (
    ParamError,
    ParamSpec,
    ViewError,
    coerce_value,
    cypher_param_names,
    final_return_aliases,
    parse_view,
)

CYPHER = """\
MATCH (p:Entity {_id: $product})
MATCH path = (p)-[:RELATES_TO*1..6]-(r:Entity {entity_class: 'Regulation'})
WHERE length(path) <= $max_depth
  AND all(x IN nodes(path) WHERE x._domain IN $domains)
RETURN r._id AS _id, r.name AS regulation, min(length(path)) AS hops
ORDER BY hops, regulation
"""


def good() -> dict:
    return {
        "name": "regulation_exposure",
        "version": 1,
        "domains": ["finance"],
        "summary": "Regulations reaching a product",
        "examples": ["Which regulations apply to the mortgage product?"],
        "params": {
            "product": {"type": "entity", "entity_class": "Product", "description": "start"},
            "max_depth": {"type": "int", "default": 4, "min": 1, "max": 6},
        },
        "cypher": CYPHER,
        "max_rows": 200,
        "presentation": {
            "format": "table",
            "table": {"columns": ["regulation", "hops"], "headers": {"hops": "Distance"}},
        },
        "provenance": {"created": date(2026, 10, 9), "origin": "authored", "source_question": None},
    }


def parse(data: dict, folder: str = "regulation_exposure"):
    return parse_view(data, folder)


def test_good_view_parses():
    spec = parse(good())
    assert spec.name == "regulation_exposure"
    assert spec.params["max_depth"].default == 4
    assert spec.params["product"].required is True
    assert spec.params["max_depth"].required is False
    assert spec.max_rows == 200
    assert spec.provenance["created"] == "2026-10-09"
    assert spec.presentation.to_dict()["table"]["columns"] == ["regulation", "hops"]


def test_max_rows_defaults_to_200():
    data = good()
    del data["max_rows"]
    assert parse(data).max_rows == 200


@pytest.mark.parametrize(
    "mutate, fragment",
    [
        (lambda d: d.pop("summary"), "missing required keys"),
        (lambda d: d.update(extra=1), "unknown keys"),
        (lambda d: d.update(materialize="cache"), "materialize"),
        (lambda d: d.update(name="Bad-Name"), "name must match"),
        (lambda d: d.update(name="other"), "must equal the folder name"),
        (lambda d: d.update(version=0), "positive integer"),
        (lambda d: d.update(version=True), "positive integer"),
        (lambda d: d.update(domains=[]), "at least 1"),
        (lambda d: d.update(examples=[]), "at least 1"),
        (lambda d: d.update(examples=["q"] * 6), "at most 5"),
        (lambda d: d.update(summary="two\nlines"), "one-line"),
        (lambda d: d.update(max_rows=0), "max_rows"),
        (lambda d: d.update(cypher=CYPHER + "\nSET p.x = 1"), "write operation"),
        (lambda d: d.update(cypher=CYPHER.replace("$domains", "['finance']")), r"\$domains"),
        (lambda d: d["provenance"].update(origin="bogus"), "origin"),
        (lambda d: d["provenance"].update(origin="promoted"), "source_question"),
        (lambda d: d["provenance"].pop("created"), "provenance"),
    ],
)
def test_top_level_rules(mutate, fragment):
    data = good()
    mutate(data)
    with pytest.raises(ViewError, match=fragment):
        parse(data)


def test_reserved_domains_param_rejected():
    data = good()
    data["params"]["domains"] = {"type": "string"}
    with pytest.raises(ViewError, match="reserved"):
        parse(data)


def test_undeclared_cypher_param_rejected():
    data = good()
    data["cypher"] = CYPHER.replace("$max_depth", "$other_depth")
    with pytest.raises(ViewError, match=r"undeclared parameters \['\$other_depth'\]"):
        parse(data)


def test_unused_declared_param_rejected():
    data = good()
    data["params"]["spare"] = {"type": "string"}
    with pytest.raises(ViewError, match="never used"):
        parse(data)


def test_param_inside_string_literal_is_not_a_use():
    data = good()
    data["cypher"] = CYPHER.replace("'Regulation'", "'costs $5'")
    parse(data)  # '$5' lives in a string literal and is not a parameter


@pytest.mark.parametrize(
    "pdata, fragment",
    [
        ({"type": "wat"}, "type must be one of"),
        ({"type": "enum"}, "values"),
        ({"type": "list"}, "items"),
        ({"type": "int", "default": "x"}, "param 'max_depth'"),
        ({"type": "int", "default": 9, "max": 6}, "above the maximum"),
        ({"type": "int", "min": 5, "max": 1}, "min is above max"),
        ({"type": "enum", "values": ["a"], "default": "b"}, "not one of"),
        ({"type": "entity", "default": "x"}, "cannot have a default"),
        ({"type": "string", "entity_class": "X"}, "entity_class is only for entity"),
        ({"type": "string", "min": 1}, "only for int/float"),
        ({"type": "int", "bogus": 1}, "unknown keys"),
    ],
)
def test_param_rules(pdata, fragment):
    data = good()
    data["params"]["max_depth"] = pdata
    with pytest.raises(ViewError, match=fragment):
        parse(data)


@pytest.mark.parametrize(
    "presentation, fragment",
    [
        ({"format": "pie"}, "format must be one of"),
        ({"format": "table"}, "block is required"),
        ({"format": "table", "table": {"columns": ["hops"]}, "list": {"label": "hops"}}, "do not match"),
        ({"format": "table", "table": {"columns": ["nope"]}}, "does not alias"),
        ({"format": "table", "table": {"columns": ["hops"], "headers": {"x": "X"}}}, "not in columns"),
        ({"format": "list", "list": {"detail": "hops"}}, "needs"),
        ({"format": "tree", "tree": {"id": "_id", "parent": "hops"}}, "needs"),
        ({"format": "mermaid_graph", "mermaid_graph": {"source": "_id", "target": "zzz"}}, "does not alias"),
    ],
)
def test_presentation_rules(presentation, fragment):
    data = good()
    data["presentation"] = presentation
    with pytest.raises(ViewError, match=fragment):
        parse(data)


def test_fingerprint_ignores_summary_and_version_but_not_cypher():
    a = parse(good())
    d2 = good()
    d2["version"] = 2
    d2["summary"] = "different words"
    assert parse(d2).fingerprint() == a.fingerprint()
    d3 = good()
    d3["cypher"] = CYPHER.replace("*1..6", "*1..5")
    assert parse(d3).fingerprint() != a.fingerprint()
    d4 = copy.deepcopy(good())
    d4["presentation"]["table"]["headers"] = {"hops": "Hops"}
    assert parse(d4).fingerprint() != a.fingerprint()


# ── cypher inspection ────────────────────────────────────────────────────────


def test_final_return_aliases():
    assert final_return_aliases(CYPHER) == ["_id", "regulation", "hops"]


def test_aliases_handle_distinct_nested_commas_and_unaliased_items():
    cy = "MATCH (n) RETURN DISTINCT n.name AS name, collect({a: 1, b: 2}) AS bag, n.x, size([1,2]) AS k LIMIT 5"
    assert final_return_aliases(cy) == ["name", "bag", "n.x", "k"]


def test_aliases_use_the_last_return():
    cy = "CALL () { MATCH (a) RETURN a } MATCH (b) RETURN b.id AS id ORDER BY id"
    assert final_return_aliases(cy) == ["id"]


def test_return_star_rejected():
    with pytest.raises(ViewError, match=r"RETURN \*"):
        final_return_aliases("MATCH (n) RETURN *")


def test_cypher_param_names_ignores_strings_and_comments():
    cy = "MATCH (n) WHERE n.a = $a AND n.b = '$b' // $c\nRETURN n"
    assert cypher_param_names(cy) == {"a"}


# ── coercion ─────────────────────────────────────────────────────────────────


def spec(**kw) -> ParamSpec:
    return ParamSpec(name="p", **kw)


@pytest.mark.parametrize(
    "kw, raw, expected",
    [
        ({"type": "string"}, "abc", "abc"),
        ({"type": "int"}, "42", 42),
        ({"type": "float"}, "2.5", 2.5),
        ({"type": "bool"}, "Yes", True),
        ({"type": "bool"}, "0", False),
        ({"type": "date"}, "2026-10-09", "2026-10-09"),
        ({"type": "date"}, date(2026, 10, 9), "2026-10-09"),
        ({"type": "enum", "values": ("a", "b")}, "b", "b"),
        ({"type": "list", "items": "int"}, "1, 2,3", [1, 2, 3]),
        ({"type": "list", "items": "string"}, ["x", "y"], ["x", "y"]),
    ],
)
def test_coerce_ok(kw, raw, expected):
    assert coerce_value(spec(**kw), raw) == expected


@pytest.mark.parametrize(
    "kw, raw",
    [
        ({"type": "int"}, "abc"),
        ({"type": "int", "min": 1}, "0"),
        ({"type": "int", "max": 3}, "4"),
        ({"type": "float"}, "x"),
        ({"type": "bool"}, "maybe"),
        ({"type": "date"}, "09/10/2026"),
        ({"type": "enum", "values": ("a",)}, "z"),
        ({"type": "list", "items": "int"}, "1,x"),
    ],
)
def test_coerce_rejects_and_names_the_param(kw, raw):
    with pytest.raises(ParamError, match="param 'p'"):
        coerce_value(spec(**kw), raw)


# ── hardening ────────────────────────────────────────────────────────────────


def _with_cypher(cy: str) -> dict:
    data = good()
    data["cypher"] = cy
    data["params"] = {}
    data["presentation"] = {"format": "table", "table": {"columns": ["a"]}}
    return data


def test_domains_only_in_comment_is_not_scoped():
    with pytest.raises(ViewError, match=r"\$domains"):
        parse(_with_cypher("MATCH (n) // $domains\nRETURN n.a AS a"))


def test_domains_only_in_string_literal_is_not_scoped():
    with pytest.raises(ViewError, match=r"\$domains"):
        parse(_with_cypher("MATCH (n) WHERE n.x = '$domains' RETURN n.a AS a"))


def test_write_keyword_in_string_literal_is_accepted():
    parse(_with_cypher("MATCH (n {name: 'Set Theory'}) WHERE n._domain IN $domains RETURN n.a AS a"))


def test_multiple_statements_rejected():
    with pytest.raises(ViewError, match="one statement per view"):
        parse(_with_cypher("MATCH (n) WHERE n._domain IN $domains RETURN n.a AS a; MATCH (m) RETURN m.a AS a"))


def test_trailing_semicolon_and_semicolon_in_string_ok():
    parse(_with_cypher("MATCH (n {t: 'a;b'}) WHERE n._domain IN $domains RETURN n.a AS a ;\n"))


def test_aliases_ignore_limit_like_property_names():
    cy = "MATCH (n) RETURN n.a AS a, n.skipper AS b, n.limit AS c"
    assert final_return_aliases(cy) == ["a", "b", "c"]
    assert final_return_aliases("MATCH (n) RETURN n.a, n.limit") == ["n.a", "n.limit"]


def test_backticked_span_is_not_a_param():
    assert cypher_param_names("MATCH (n) WHERE n.`x$e` = $a RETURN n") == {"a"}


def test_backticked_plain_alias_is_unwrapped():
    assert final_return_aliases("MATCH (n) RETURN n.a AS `a`, n.b AS b") == ["a", "b"]


def test_backticked_unusable_alias_error_hint():
    data = _with_cypher("MATCH (n) WHERE n._domain IN $domains RETURN n.a AS `my col`")
    data["presentation"]["table"]["columns"] = ["my col"]
    with pytest.raises(ViewError, match="plain identifiers"):
        parse(data)


def test_malformed_columns_is_viewerror_not_typeerror():
    data = good()
    data["presentation"] = {"format": "table", "table": {"columns": "hops", "headers": {"hops": "H"}}}
    with pytest.raises(ViewError, match="columns"):
        parse(data)


def test_yaml_boolean_hint():
    data = good()
    data["domains"] = [False]
    with pytest.raises(ViewError, match="quote YAML booleans"):
        parse(data)


def test_created_datetime_normalised_to_date():
    from datetime import datetime

    data = good()
    data["provenance"]["created"] = datetime(2026, 10, 9, 12, 30)
    assert parse(data).provenance["created"] == "2026-10-09"


@pytest.mark.parametrize("raw", ["nan", "inf", "-inf", float("nan"), float("inf")])
def test_float_rejects_non_finite(raw):
    with pytest.raises(ParamError, match="param 'p'"):
        coerce_value(spec(type="float"), raw)


def test_aliases_union_lowercase_order_limit_distinct():
    assert final_return_aliases("MATCH (a) RETURN a.x AS x UNION MATCH (b) RETURN b.y AS x") == ["x"]
    assert final_return_aliases("match (a) return a.x as x, a.y as y") == ["x", "y"]
    assert final_return_aliases("MATCH (a) RETURN a.x AS x ORDER BY x LIMIT 3") == ["x"]
    assert final_return_aliases("MATCH (a) RETURN DISTINCT a.x AS x, a.y AS y") == ["x", "y"]
