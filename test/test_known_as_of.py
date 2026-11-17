"""`--knownAsOf` reads record time (spec 2026-11-15 §6.2, question 9)."""
import json
import time

import pytest
from click.testing import CliRunner

from artmind import graph_query as gq
from artmind.cli import cli


@pytest.fixture()
def kolkata(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Kolkata")
    time.tzset()
    yield
    monkeypatch.delenv("TZ")
    time.tzset()


def _recording(monkeypatch, rows=()):
    sent = []
    monkeypatch.setattr(gq, "_run_read_query", lambda cypher, params: sent.append((cypher, params)) or list(rows))
    return sent


def test_known_predicate_text():
    text = gq.known_predicate("o")
    assert "o._recorded_at <= $knownAsOf" in text and "_recorded_at IS NULL" not in text
    assert "o._retired_at IS NULL OR o._retired_at > $knownAsOf" in text


def test_a_date_means_the_end_of_that_local_day(kolkata):
    assert gq.resolve_known_as_of("2026-03-01") == "2026-03-01T18:29:59.999999+00:00"


def test_a_month_means_the_end_of_its_last_local_day(kolkata):
    assert gq.resolve_known_as_of("2026-02") == "2026-02-28T18:29:59.999999+00:00"


def test_a_datetime_without_a_zone_is_local_and_one_with_a_zone_is_kept(kolkata):
    assert gq.resolve_known_as_of("2026-03-01T10:00:00") == "2026-03-01T04:30:00+00:00"
    assert gq.resolve_known_as_of("2026-03-01T10:00:00+00:00") == "2026-03-01T10:00:00+00:00"


def test_now_is_utc_and_garbage_is_refused():
    assert gq.resolve_known_as_of("now").endswith("+00:00")
    assert gq.resolve_known_as_of(None) is None and gq.resolve_known_as_of(" ") is None
    with pytest.raises(ValueError, match="--knownAsOf"):
        gq.resolve_known_as_of("yesterday-ish")
    with pytest.raises(ValueError, match="--knownAsOf"):
        gq.resolve_known_as_of("2026-13")


def test_entity_history_sends_both_labels_and_the_param(monkeypatch):
    sent = _recording(monkeypatch)
    out = gq.entity_history(["pj"], key="a|PERSON|pj", known_as_of="2026-03-01")
    cypher, params = sent[0]
    assert params["knownAsOf"].endswith("+00:00") and params["knownAsOf"].startswith("2026-03-0")
    assert "o:Observation OR o:ObservationHistory" in cypher and "_retired_at > $knownAsOf" in cypher
    assert "o._recorded_at <= $knownAsOf" in cypher
    # Status is as of T: a node retired after T was current then.
    assert ("CASE WHEN o._retired_at IS NULL OR o._retired_at > $knownAsOf "
            "THEN 'current' ELSE 'history' END AS status") in cypher
    assert out["parameters"]["knownAsOf"] == params["knownAsOf"]
    assert "warnings" not in out


def test_entity_history_without_known_as_of_keeps_the_label_status(monkeypatch):
    sent = _recording(monkeypatch)
    gq.entity_history(["pj"], key="a|PERSON|pj")
    cypher, params = sent[0]
    assert "knownAsOf" not in params and "knownAsOf" not in cypher
    assert "CASE WHEN o:Observation THEN 'current' ELSE 'history' END AS status" in cypher


def test_entity_history_as_of_is_a_point(monkeypatch):
    sent = _recording(monkeypatch)
    gq.entity_history(["pj"], key="a|PERSON|pj", as_of="2026-03-01")
    cypher, params = sent[0]
    assert "o._valid_to IS NULL OR o._valid_to > $asOf" in cypher
    assert params["asOf"] == "2026-03-01"


def test_a_warning_names_approximate_record_time(monkeypatch):
    monkeypatch.setattr(gq, "_run_read_query", lambda c, p: [{"observation": {"key": "k", "_record_time_approx": True},
                                                              "status": "current"}])
    out = gq.entity_history(["pj"], key="k", known_as_of="2026-03-01")
    assert any("approximate" in w for w in out["warnings"])


def test_document_history_filters_observations_and_reads_status_as_of_t(monkeypatch):
    rows = [{"document": {"id": "d1", "name": "a.md"}, "retired": False, "approx": 2,
             "observations": [{"key": "a|PERSON|pj", "status": "current", "doc_version": 1}]}]
    sent = _recording(monkeypatch, rows)
    out = gq.document_history(["pj"], "a.md", known_as_of="2026-03-01")
    cypher, params = sent[0]
    assert params["knownAsOf"].endswith("+00:00")
    assert "o.doc_id = d.id AND ($knownAsOf IS NULL OR (o._recorded_at <= $knownAsOf" in cypher
    assert "status: CASE WHEN o._retired_at IS NULL OR o._retired_at > $knownAsOf" in cypher
    assert "NOT (d._retired_at IS NULL OR d._retired_at > $knownAsOf) AS retired" in cypher
    assert out["known_as_of"] == params["knownAsOf"]
    assert any("2 row(s) carry approximate record time" in w for w in out["warnings"])


def test_document_history_without_known_as_of_is_unchanged(monkeypatch):
    sent = _recording(monkeypatch)
    gq.document_history(["pj"], "a.md")
    cypher, params = sent[0]
    assert "knownAsOf" not in params and "knownAsOf" not in cypher
    assert "d:DocumentHistory AS retired" in cypher


def test_pattern10_filters_chunks_over_both_labels(monkeypatch):
    sent = _recording(monkeypatch)
    out = gq.execute_pattern(["pj"], "pattern10", documentName="a", knownAsOf="2026-03-01")
    cypher, params = sent[0]
    assert "MATCH (d:DocumentHistory)" in cypher
    assert "WHERE (c:DocChunk OR c:DocChunkHistory) AND ($knownAsOf IS NULL OR (c._recorded_at <= $knownAsOf" in cypher
    assert "c._retired_at IS NULL OR c._retired_at > $knownAsOf" in cypher
    assert params["knownAsOf"].endswith("+00:00")
    assert out["parameters"]["knownAsOf"] == params["knownAsOf"]


def test_pattern10_warns_on_approximate_chunks(monkeypatch):
    row = {"document": {"id": "d1", "name": "a.md"}, "chunk": {"id": "d1_0"}, "_rowsTotal": 1, "_approx": True}
    monkeypatch.setattr(gq, "_run_read_query", lambda c, p: [dict(row)] if "PART_OF" in c else [])
    out = gq.execute_pattern(["pj"], "pattern10", documentName="a", knownAsOf="now")
    assert "_approx" not in out["rows"][0]
    assert any("approximate" in w for w in out["warnings"])


def test_known_as_of_is_refused_on_other_patterns():
    with pytest.raises(ValueError, match="--knownAsOf is not available for pattern1"):
        gq.execute_pattern(["pj"], "pattern1", entityClass="PERSON", knownAsOf="now")


def _cli(args):
    result = CliRunner().invoke(cli, ["query", *args, "--domain", "pj", "--compact"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_the_cli_passes_known_as_of_through(monkeypatch):
    seen = {}
    monkeypatch.setattr(gq, "entity_history", lambda *a, **kw: seen.update(entity=kw) or {"rows": []})
    monkeypatch.setattr(gq, "document_history", lambda *a, **kw: seen.update(document=kw) or {"rows": []})
    monkeypatch.setattr(gq, "execute_pattern", lambda **kw: seen.update(pattern=kw) or {"rows": []})
    _cli(["entity-history", "--key", "a|PERSON|pj", "--knownAsOf", "2026-03-01"])
    _cli(["document-history", "--documentName", "a.md", "--knownAsOf", "2026-03-01"])
    _cli(["graph", "pattern10", "--documentName", "a.md", "--knownAsOf", "2026-03-01"])
    assert seen["entity"]["known_as_of"] == "2026-03-01"
    assert seen["document"]["known_as_of"] == "2026-03-01"
    assert seen["pattern"]["knownAsOf"] == "2026-03-01" and seen["pattern"]["includeHistory"] is True


def test_a_bad_known_as_of_is_a_usage_failure(monkeypatch):
    result = CliRunner().invoke(cli, ["query", "entity-history", "--domain", "pj", "--key", "k",
                                      "--knownAsOf", "soon"])
    assert result.exit_code == 1 and "--knownAsOf" in result.output
