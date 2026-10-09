"""kg_views.runner: assert the Cypher and parameters actually SENT.

Pattern from test_update.py: a `run_side_effect` recorder on the session. A bare
MagicMock session answers every query happily, so counts alone prove nothing.
"""

from unittest.mock import MagicMock, patch

import pytest
from neo4j.exceptions import ClientError, ServiceUnavailable

from artmind.kg_views import runner
from artmind.kg_views.model import ViewError, parse_view

CYPHER = (
    "MATCH (p:Entity {_id: $product}) "
    "WHERE p._domain IN $domains AND p.depth <= $max_depth "
    "RETURN p._id AS _id, p.name AS name ORDER BY name"
)


def make_spec(max_rows=200, domains=("finance",)):
    return parse_view(
        {
            "name": "v1", "version": 3, "domains": list(domains), "summary": "s", "examples": ["q"],
            "params": {
                "product": {"type": "entity", "entity_class": "Product"},
                "max_depth": {"type": "int", "default": 4},
            },
            "cypher": CYPHER, "max_rows": max_rows,
            "presentation": {"format": "list", "list": {"label": "name"}},
            "provenance": {"created": "2026-10-09", "origin": "authored"},
        },
        "v1",
    )


class Recorder:
    def __init__(self, rows, plan=None, raises=None):
        self.calls: list[tuple[str, dict]] = []
        self.queries: list = []
        self.rows, self.plan, self.raises = rows, plan, raises

    def run_side_effect(self, cypher, parameters=None, **kwargs):
        self.queries.append(cypher)
        cypher = getattr(cypher, "text", cypher)
        self.calls.append((cypher, parameters))
        if self.raises and not cypher.lstrip().startswith("EXPLAIN"):
            raise self.raises
        result = MagicMock()
        result.__iter__.return_value = iter(self.rows)
        result.consume.return_value.plan = self.plan
        return result

    def patch(self):
        session = MagicMock()
        session.run.side_effect = self.run_side_effect
        ctx = MagicMock()
        ctx.__enter__.return_value = session
        return patch("artmind.kg_views.runner.read_session", return_value=ctx)


def resolved(status="ok", **kw):
    from artmind.kg_views.params import Resolution

    base = dict(
        status=status,
        bindings={"product": "ent_9", "max_depth": 4},
        echo={"product": {"input": "mortgage", "_id": "ent_9", "name": "Retail Mortgage",
                          "entity_class": "Product"}, "max_depth": 4},
    )
    base.update(kw)
    return Resolution(**base)


def family(domain):
    return {"finance": ["finance", "finance.retail"]}.get(domain, [domain])


def run(spec, rec, resolution=None, **kw):
    with rec.patch(), patch.object(runner, "expand_domain_family", side_effect=family), patch.object(
        runner.params_mod, "resolve_params", return_value=resolution or resolved()
    ) as rp:
        out = runner.run_view(spec, ["finance"], {"product": "mortgage"}, **kw)
    return out, rp


def test_sends_the_cypher_with_resolved_id_and_family_expanded_domains():
    rec = Recorder([{"_id": "ent_9", "name": "Retail Mortgage"}])
    out, rp = run(make_spec(), rec)
    assert rec.calls == [
        (CYPHER, {"product": "ent_9", "max_depth": 4, "domains": ["finance", "finance.retail"]})
    ]
    assert rp.call_args.args[2] == ["finance", "finance.retail"]  # params resolved against the family
    assert out["status"] == "ok" and out["rows"] == [{"_id": "ent_9", "name": "Retail Mortgage"}]
    assert out["params"]["product"]["_id"] == "ent_9"
    assert out["view"] == "v1" and out["version"] == 3 and out["domains"] == ["finance"]
    assert out["presentation"] == {"format": "list", "list": {"label": "name"}}
    assert "rendered" not in out


def test_render_markdown_included_on_request_and_internal_props_stripped():
    rec = Recorder([{"_id": "a", "name": "A", "embedding": [0.1]}])
    out, _ = run(make_spec(), rec, render=True)
    assert out["rendered"] == "- **A**"
    assert "embedding" not in out["rows"][0]


def test_truncates_to_max_rows_and_reports_totals():
    rec = Recorder([{"_id": str(i), "name": f"n{i}"} for i in range(5)])
    out, _ = run(make_spec(max_rows=2), rec, render=True)
    assert [r["_id"] for r in out["rows"]] == ["0", "1"]
    assert (out["rows_total"], out["truncated"]) == (5, True)
    assert out["rendered"].endswith("_Showing 2 of 5 rows._")


@pytest.mark.parametrize(
    "resolution, expected",
    [
        (resolved("needs_disambiguation", param="product", candidates=[{"_id": "a"}], bindings={}, echo={}),
         {"param": "product", "candidates": [{"_id": "a"}]}),
        (resolved("no_match", param="product", input="zzz", bindings={}, echo={}),
         {"param": "product", "input": "zzz"}),
        (resolved("needs_disambiguation", param="product", candidates=[{"_id": "a"}],
                  bindings={"max_depth": 4}, echo={"max_depth": 4}),
         {"param": "product", "candidates": [{"_id": "a"}], "params": {"max_depth": 4}}),
        (resolved("no_match", param="product", input="zzz",
                  bindings={"max_depth": 4}, echo={"max_depth": 4}),
         {"param": "product", "input": "zzz", "params": {"max_depth": 4}}),
    ],
)
def test_unresolved_entity_returns_no_rows_and_runs_no_cypher(resolution, expected):
    rec = Recorder([{"_id": "x"}])
    out, _ = run(make_spec(), rec, resolution=resolution)
    assert rec.calls == []
    assert out["status"] == resolution.status
    for key, value in expected.items():
        assert out[key] == value
    for absent in ("rows", "rows_total", "truncated", "presentation", "rendered"):
        assert absent not in out
    assert out["params"] == resolution.echo


def test_domain_guard_refuses_uncovered_domains_before_touching_the_graph():
    rec = Recorder([])
    with rec.patch(), patch.object(runner, "expand_domain_family") as fam:
        with pytest.raises(ViewError, match="cannot run for \\['hr'\\]"):
            runner.run_view(make_spec(), ["finance", "hr"], {})
    fam.assert_not_called()
    assert rec.calls == []


def test_subdomain_of_a_view_domain_is_allowed():
    rec = Recorder([])
    with rec.patch(), patch.object(runner, "expand_domain_family", side_effect=lambda d: [d]), patch.object(
        runner.params_mod, "resolve_params", return_value=resolved()
    ):
        out = runner.run_view(make_spec(), ["finance.retail"], {})
    assert rec.calls[0][1]["domains"] == ["finance.retail"]
    assert out["rows"] == []


def test_neo4j_error_is_wrapped_with_view_name_and_version():
    error = ClientError._hydrate_neo4j(code="Neo.ClientError.Statement.SyntaxError", message="boom")
    rec = Recorder([], raises=error)
    with rec.patch(), patch.object(runner, "expand_domain_family", side_effect=family), patch.object(
        runner.params_mod, "resolve_params", return_value=resolved()
    ):
        with pytest.raises(ViewError, match=r"view v1 v3 failed: boom") as ei:
            runner.run_view(make_spec(), ["finance"], {})
    assert ei.value.__cause__ is error


def test_driver_error_during_query_is_wrapped():
    error = ServiceUnavailable("db down")
    rec = Recorder([], raises=error)
    with rec.patch(), patch.object(runner, "expand_domain_family", side_effect=family), patch.object(
        runner.params_mod, "resolve_params", return_value=resolved()
    ):
        with pytest.raises(ViewError, match=r"view v1 v3 failed: db down") as ei:
            runner.run_view(make_spec(), ["finance"], {})
    assert ei.value.__cause__ is error


def test_driver_error_before_the_query_is_wrapped():
    error = ServiceUnavailable("db down")
    rec = Recorder([])
    with rec.patch(), patch.object(runner, "expand_domain_family", side_effect=error):
        with pytest.raises(ViewError, match=r"view v1 v3 failed") as ei:
            runner.run_view(make_spec(), ["finance"], {})
    assert ei.value.__cause__ is error
    assert rec.calls == []
    with rec.patch(), patch.object(runner, "expand_domain_family", side_effect=family), patch.object(
        runner.params_mod, "resolve_params", side_effect=error
    ):
        with pytest.raises(ViewError, match=r"view v1 v3 failed") as ei:
            runner.run_view(make_spec(), ["finance"], {})
    assert ei.value.__cause__ is error


def test_view_and_explain_queries_carry_the_timeout():
    rec = Recorder([], plan=None)
    run(make_spec(), rec, explain=True)
    assert [q.timeout for q in rec.queries] == [runner.VIEW_QUERY_TIMEOUT_S] * 2
    assert runner.VIEW_QUERY_TIMEOUT_S == 60
    assert [q.text for q in rec.queries] == [CYPHER, "EXPLAIN " + CYPHER]


def test_explain_sends_explain_prefixed_cypher_with_the_same_bindings():
    plan = {"operatorType": "ProduceResults", "children": [{"operatorType": "Sort", "children": []}]}
    rec = Recorder([], plan=plan)
    out, _ = run(make_spec(), rec, explain=True)
    explain_cypher, explain_params = rec.calls[1]
    assert explain_cypher == "EXPLAIN " + CYPHER
    assert explain_params == rec.calls[0][1]
    assert out["plan"] == {
        "operator": "ProduceResults",
        "children": [{"operator": "Sort", "children": []}],
    }


def test_explain_keeps_a_leading_cypher_version_prefix_first():
    spec = make_spec()
    object.__setattr__(spec, "cypher", "CYPHER 25 " + CYPHER)
    rec = Recorder([], plan=None)
    with rec.patch():
        assert runner.explain_plan(spec, {"domains": []}) is None
    assert rec.calls[0][0] == "CYPHER 25 EXPLAIN " + CYPHER
