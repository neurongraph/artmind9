"""`query entity-history --key/--name` and `query document-history`: history
reachable after the projection GC removed an entity. Synthetic data only."""

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from artmind import graph_query
from artmind.cli import cli


@pytest.fixture()
def recorder(monkeypatch):
    calls: list[tuple[str, dict]] = []
    rows: list[dict] = []

    def fake_run(cypher, params):
        calls.append((cypher, params))
        return list(rows)

    monkeypatch.setattr(graph_query, "_run_read_query", fake_run)
    return calls, rows


def _obs(key, status, **extra):
    name = key.split("|")[0].title()
    return {"observation": {"id": f"o-{key}-{status}", "key": key, "name": name,
                            "doc_id": "d1", "_valid_from": "2026-01-01", **extra},
            "status": status}


# ── entity-history lookups ───────────────────────────────────────────────────


def test_entity_id_resolves_through_the_live_entity(recorder):
    calls, _ = recorder
    graph_query.entity_history(["journal"], "e1")
    cypher, params = calls[0]
    assert "MATCH (ent:Entity {_id: $entityId})" in cypher
    assert params["entityId"] == "e1"
    assert "key" not in params and "nameKey" not in params


def test_key_matches_observations_directly_without_an_entity(recorder):
    calls, rows = recorder
    rows.append(_obs("zorvan|PERSON|journal", "history"))
    result = graph_query.entity_history(["journal"], key="zorvan|PERSON|journal")
    cypher, params = calls[0]
    assert ":Entity" not in cypher
    assert "o.key = $key" in cypher
    assert "ObservationHistory" in cypher
    assert params["key"] == "zorvan|PERSON|journal"
    assert params["domains"] == ["journal"]
    assert result["key"] == "zorvan|PERSON|journal"
    assert result["keys"] == ["zorvan|PERSON|journal"]
    assert result["rows"][0]["status"] == "history"


def test_name_is_normalized_like_an_aggregate_key(recorder):
    calls, _ = recorder
    graph_query.entity_history(["journal"], name="  Zorvan  Quill ", entity_class="PERSON")
    cypher, params = calls[0]
    assert ":Entity" not in cypher
    assert params["nameKey"] == "zorvan quill"
    assert params["nameLower"] == "zorvan  quill"
    assert params["entityClass"] == "PERSON"
    assert "split(o.key, '|')[0] = $nameKey" in cypher
    assert "o.entity_class = $entityClass" in cypher


def test_name_without_class_has_no_class_filter(recorder):
    calls, _ = recorder
    graph_query.entity_history(["journal"], name="Zorvan")
    cypher, params = calls[0]
    assert "entityClass" not in params
    assert "$entityClass" not in cypher


def test_name_lists_every_matching_key(recorder):
    _, rows = recorder
    rows.extend([_obs("zorvan|PERSON|journal", "current"), _obs("zorvan|PLACE|journal", "history")])
    result = graph_query.entity_history(["journal"], name="Zorvan")
    assert result["keys"] == ["zorvan|PERSON|journal", "zorvan|PLACE|journal"]
    assert [r["status"] for r in result["rows"]] == ["current", "history"]


@pytest.mark.parametrize("kwargs", [
    {},
    {"entity_id": "e1", "key": "k|C|d"},
    {"key": "k|C|d", "name": "k"},
    {"entity_id": " ", "key": ""},
])
def test_exactly_one_lookup_is_required(recorder, kwargs):
    with pytest.raises(ValueError, match="exactly one"):
        graph_query.entity_history(["journal"], **kwargs)
    assert recorder[0] == []


def test_entity_class_needs_name(recorder):
    with pytest.raises(ValueError, match="--entityClass"):
        graph_query.entity_history(["journal"], key="k|C|d", entity_class="C")


def test_property_rows_carry_status(recorder):
    _, rows = recorder
    rows.append(_obs("zorvan|PERSON|journal", "history", role="Scout"))
    result = graph_query.entity_history(["journal"], key="zorvan|PERSON|journal", property="role")
    assert result["rows"] == [{
        "value": "Scout", "valid_from": "2026-01-01", "valid_to": None, "doc_id": "d1",
        "observation_id": "o-zorvan|PERSON|journal-history", "status": "history",
    }]


# ── document-history ─────────────────────────────────────────────────────────


def _doc_row(observations, retired=False):
    return {"document": {"id": "d1", "name": "2030-01-02.md", "path": "Journal/2030-01-02.md", "version": 2},
            "retired": retired, "observations": observations}


def test_document_history_query_spans_both_labels(recorder):
    calls, _ = recorder
    graph_query.document_history(["journal"], "2030-01-02.md")
    cypher, params = calls[0]
    assert "d:Document OR d:DocumentHistory" in cypher
    assert "o:Observation OR o:ObservationHistory" in cypher
    assert "o.doc_id = d.id" in cypher
    assert params["documentName"] == "2030-01-02.md"
    assert params["domains"] == ["journal"]
    assert params["limit"] == graph_query.DOCUMENT_HISTORY_MAX_DOCUMENTS + 1


def test_document_history_reports_dropped_keys(recorder):
    _, rows = recorder
    rows.append(_doc_row([
        {"id": "a", "key": "zorvan|PERSON|journal", "status": "history", "doc_version": 1},
        {"id": "b", "key": "zorvin|PERSON|journal", "status": "current", "doc_version": 2},
        {"id": "c", "key": "garden|PLACE|journal", "status": "current", "doc_version": 2},
        {"id": "d", "key": "garden|PLACE|journal", "status": "history", "doc_version": 1},
    ]))
    result = graph_query.document_history(["journal"], "2030-01-02.md")
    row = result["rows"][0]
    assert row["dropped_keys"] == ["zorvan|PERSON|journal"]
    assert row["summary"] == {"current": 2, "history": 2, "history_versions": [1]}
    assert row["retired"] is False
    assert result["truncated"] is False


def test_document_history_retired_document_drops_everything(recorder):
    _, rows = recorder
    rows.append(_doc_row([{"id": "a", "key": "zorvan|PERSON|journal", "status": "history", "doc_version": 3}],
                         retired=True))
    row = graph_query.document_history(["journal"], "d1")["rows"][0]
    assert row["retired"] is True
    assert row["dropped_keys"] == ["zorvan|PERSON|journal"]


def test_document_history_caps_documents(recorder):
    _, rows = recorder
    rows.extend(_doc_row([]) for _ in range(graph_query.DOCUMENT_HISTORY_MAX_DOCUMENTS + 1))
    result = graph_query.document_history(["journal"], "2030-01-02.md")
    assert len(result["rows"]) == graph_query.DOCUMENT_HISTORY_MAX_DOCUMENTS
    assert result["truncated"] is True


def test_document_history_requires_a_name(recorder):
    with pytest.raises(ValueError, match="--documentName"):
        graph_query.document_history(["journal"], "  ")


# ── CLI ──────────────────────────────────────────────────────────────────────


def test_cli_entity_history_passes_key_and_name():
    runner = CliRunner()
    with patch("artmind.cli.graph_query.entity_history", return_value={"rows": []}) as query:
        result = runner.invoke(cli, ["query", "entity-history", "--domain", "journal",
                                     "--name", "Zorvan", "--entityClass", "PERSON"])
    assert result.exit_code == 0, result.output
    query.assert_called_once_with(["journal"], None, as_of=None, property=None,
                                  key=None, name="Zorvan", entity_class="PERSON")


def test_cli_entity_history_reports_a_missing_lookup():
    result = CliRunner().invoke(cli, ["query", "entity-history", "--domain", "journal"])
    assert result.exit_code != 0
    assert "exactly one" in result.output


def test_cli_document_history_outputs_json():
    payload = {"command": "document_history", "rows": []}
    with patch("artmind.cli.graph_query.document_history", return_value=payload) as query:
        result = CliRunner().invoke(cli, ["query", "document-history", "--domain", "journal",
                                          "--documentName", "2030-01-02.md", "--compact"])
    assert result.exit_code == 0, result.output
    query.assert_called_once_with(["journal"], "2030-01-02.md")
    assert json.loads(result.output) == payload


# ── H4d: document-history name matching (spec 2026-10-22 §5.4.4) ─────────────

def _dispatch(monkeypatch, *, exact_rows, suffix_names, closest_names):
    from artmind import graph_query

    calls = []

    def fake_run(cypher, params):
        calls.append((cypher, params))
        if "names" in params:
            return [{"name": n} for n in suffix_names]
        if "fragment" in params:
            return [{"name": n} for n in closest_names]
        if "documentName" in params:
            return exact_rows.get(params["documentName"], [])
        raise AssertionError(f"unrecognised query: {params}")

    monkeypatch.setattr(graph_query, "_run_read_query", fake_run)
    return calls


_ROW = {"document": {"id": "d1", "name": "policy_alpha.md", "path": "p/policy_alpha.md", "version": 2},
        "retired": False, "observations": []}


def test_a_unique_suffixed_name_is_used_with_a_warning(monkeypatch):
    from artmind import graph_query

    calls = _dispatch(monkeypatch, exact_rows={"policy_alpha.md": [_ROW]},
                      suffix_names=["policy_alpha.md"], closest_names=[])
    result = graph_query.document_history(["f"], "policy_alpha")
    assert result["rows"][0]["document"]["name"] == "policy_alpha.md"
    assert result["matched_name"] == "policy_alpha.md"
    assert result["warnings"] == ["no document named 'policy_alpha'; matched 'policy_alpha.md'"]
    suffix_call = next(p for c, p in calls if "names" in p)
    assert suffix_call["names"] == ["policy_alpha.md", "policy_alpha.pdf", "policy_alpha.docx",
                                    "policy_alpha.txt", "policy_alpha.html"]


def test_no_match_returns_closest_names_and_next(monkeypatch):
    from artmind import graph_query

    _dispatch(monkeypatch, exact_rows={}, suffix_names=[], closest_names=["policy_alpha.md", "policy_alpha_v2.md"])
    result = graph_query.document_history(["f"], "alpha")
    assert result["rows"] == []
    assert result["closest"] == ["policy_alpha.md", "policy_alpha_v2.md"]
    assert result["next"] == (
        "artmind query document-history --domain f --documentName <one of closest> --compact"
    )


def test_two_suffixed_candidates_are_not_guessed(monkeypatch):
    from artmind import graph_query

    _dispatch(monkeypatch, exact_rows={}, suffix_names=["a.md", "a.pdf"], closest_names=["a.md", "a.pdf"])
    result = graph_query.document_history(["f"], "a")
    assert result["rows"] == [] and result["closest"] == ["a.md", "a.pdf"]
    assert "matched_name" not in result


def test_an_exact_name_needs_no_second_query(monkeypatch):
    from artmind import graph_query

    calls = _dispatch(monkeypatch, exact_rows={"policy_alpha.md": [_ROW]}, suffix_names=[], closest_names=[])
    result = graph_query.document_history(["f"], "policy_alpha.md")
    assert len(calls) == 1 and "warnings" not in result


def test_mixed_case_input_sends_casefolded_names(monkeypatch):
    from artmind import graph_query

    calls = _dispatch(monkeypatch, exact_rows={}, suffix_names=[], closest_names=[])
    graph_query.document_history(["f"], "Policy_Alpha")
    suffix_call = next(p for c, p in calls if "names" in p)
    assert suffix_call["names"][0] == "policy_alpha.md"
    assert all(n == n.casefold() for n in suffix_call["names"])


def test_no_closest_names_means_no_next(monkeypatch):
    from artmind import graph_query

    _dispatch(monkeypatch, exact_rows={}, suffix_names=[], closest_names=[])
    result = graph_query.document_history(["f"], "zzz")
    assert result["rows"] == [] and result["closest"] == []
    assert "next" not in result


def test_a_bare_suffix_skips_the_fragment_query(monkeypatch):
    from artmind import graph_query

    calls = _dispatch(monkeypatch, exact_rows={}, suffix_names=[], closest_names=["x.md"])
    result = graph_query.document_history(["f"], ".md")
    assert result["closest"] == [] and "next" not in result
    assert not any("fragment" in p for c, p in calls)


def test_next_quotes_and_joins_domains(monkeypatch):
    from artmind import graph_query

    _dispatch(monkeypatch, exact_rows={}, suffix_names=[], closest_names=["a.md"])
    result = graph_query.document_history(["f", "g h"], "a")
    assert "--domain 'f,g h' --documentName" in result["next"]
