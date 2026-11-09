import json

from click.testing import CliRunner

from artmind import identity, vector_query
from artmind.cli import cli
from _envelope import plain

NARRATOR = {"entity_class": "Person", "name": "Ann Lee", "aliases": ["Annie"]}


def ent(i, name, cls="Person"):
    return {"entity": {"_id": i, "name": name, "entity_class": cls, "_domain": "d"},
            "score": 0.5, "matched_by": ["fulltext"]}


def run(monkeypatch, rows, ref, narrator=NARRATOR, domains=("d",)):
    calls = []

    def fake(doms, reference, top_k):
        calls.append(reference)
        return {"domains": doms, "query_type": "entity_resolve", "question": reference,
                "parameters": {"topK": top_k}, "rows": rows}

    monkeypatch.setattr(vector_query, "entity_resolve", fake)
    monkeypatch.setattr(identity.domain_config, "narrator", lambda d: narrator)
    args = ["query", "entity-resolve", ref]
    for d in domains:
        args += ["--domain", d]
    res = CliRunner().invoke(cli, args)
    assert res.exit_code == 0, res.output
    return calls, plain(json.loads(res.output))


def test_me_maps_to_narrator(monkeypatch):
    calls, out = run(monkeypatch, [ent("1", "Ann Lee"), ent("2", "Bob")], "me")
    assert calls == ["Ann Lee"]
    assert out["narrator"] == {"name": "Ann Lee", "entity_class": "Person"}
    assert out["rows"][0]["matched_by"] == ["fulltext", "narrator"]
    assert out["rows"][1]["matched_by"] == ["fulltext"]


def test_alias_row_tagged(monkeypatch):
    _, out = run(monkeypatch, [ent("1", "Annie")], "I")
    assert out["rows"][0]["matched_by"] == ["fulltext", "narrator"]


def test_me_without_narrator(monkeypatch):
    calls, out = run(monkeypatch, [ent("1", "Bob")], "me", narrator=None)
    assert calls == ["me"]
    assert "narrator" not in out


def test_leading_first_person_keeps_reference(monkeypatch):
    calls, out = run(monkeypatch, [ent("1", "Bob")], "my wife")
    assert calls == ["my wife"]
    assert out["narrator"]["name"] == "Ann Lee"
    assert out["rows"][0]["matched_by"] == ["fulltext"]


def test_broken_config_warns(monkeypatch):
    def boom(d):
        raise identity.domain_config.DomainConfigError("broken file")
    calls = []
    monkeypatch.setattr(vector_query, "entity_resolve",
                        lambda doms, r, k: calls.append(r) or {"rows": [], "parameters": {}})
    monkeypatch.setattr(identity.domain_config, "narrator", boom)
    res = CliRunner().invoke(cli, ["query", "entity-resolve", "me", "--domain", "d"])
    out = json.loads(res.output)
    assert calls == ["me"] and "broken file" in out["warnings"][0]


def test_multi_domain_disagreeing_narrators_not_applied(monkeypatch):
    calls = []
    monkeypatch.setattr(vector_query, "entity_resolve",
                        lambda doms, r, k: calls.append(r) or {"rows": [], "parameters": {}})
    monkeypatch.setattr(identity.domain_config, "narrator",
                        lambda d: {"entity_class": "Person", "name": d, "aliases": []})
    res = CliRunner().invoke(cli, ["query", "entity-resolve", "me", "--domain", "a", "--domain", "b"])
    assert res.exit_code == 0 and calls == ["me"] and "narrator" not in json.loads(res.output)


def test_twins_grouped_rows_untouched(monkeypatch):
    rows = [ent("1", "Acme Corp"), ent("2", "Other"), ent("3", "acme corp.")]
    _, out = run(monkeypatch, rows, "acme", narrator=None)
    assert [r["entity"]["_id"] for r in out["rows"]] == ["1", "2", "3"]
    assert out["twins"] == [{"name": "Acme Corp", "entity_class": "Person", "ids": ["1", "3"]}]
    assert out["warnings"] == ["2 entities share the name 'Acme Corp' (Person): "
                               "treat them as one unless the text distinguishes them"]


def test_no_twins_no_key(monkeypatch):
    _, out = run(monkeypatch, [ent("1", "A"), ent("2", "B")], "x", narrator=None)
    assert "twins" not in out and "warnings" not in out
