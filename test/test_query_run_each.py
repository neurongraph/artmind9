"""`query run --each FILE` (P3 spec §8.6, planner v2 §7.5): a fixed program per claim. Invented claims."""

import json

import pytest

from artmind.query_run import each
from artmind.query_run.capture import StepResult


def _write(tmp_path, text, name="claims.jsonl"):
    path = tmp_path / name
    path.write_text(text)
    return path


def test_load_jsonl_and_yaml(tmp_path):
    a = each.load_claims(_write(tmp_path, '{"id": "c1", "claim": "fees rose"}\n{"id": "c2", "claim": "x", "source_doc": "memo.md"}\n'))
    assert [(c.id, c.claim, c.source_doc) for c in a] == [("c1", "fees rose", None), ("c2", "x", "memo.md")]
    b = each.load_claims(_write(tmp_path, "- id: c1\n  claim: fees rose\n", "claims.yaml"))
    assert [c.id for c in b] == ["c1"]


@pytest.mark.parametrize("text, message", [
    ('{"claim": "no id"}\n', "id"),
    ('{"id": "c1"}\n', "claim"),
    ('{"id": "c1", "claim": "a"}\n{"id": "c1", "claim": "b"}\n', "duplicate"),
    ("not json\n", "line 1"),
])
def test_bad_claims_files(tmp_path, text, message):
    with pytest.raises(each.ClaimsError, match=message):
        each.load_claims(_write(tmp_path, text))


def test_the_item_program_excludes_the_source_and_unions_two_searches():
    plan = each.item_plan(each.Claim("c1", "fees rose in March", "memo.md"), ["bank"])
    (par, pick) = plan["program"]
    runs = [c["run"] for c in par["children"]]
    assert runs[0].startswith("artmind query vector-text --shape brief --domain bank --topK 10 ")
    assert runs[1].startswith("artmind query survey --shape brief --domain bank --topDocs 10 ")
    assert all("'fees rose in March'" in r for r in runs)
    assert pick["pick"]["how"] == "union" and plan["scope"]["excluded_documents"] == ["memo.md"]


def test_a_claim_is_cut_to_the_character_cap():
    plan = each.item_plan(each.Claim("c1", "x" * 5000, None), ["bank"])
    assert len(plan["program"][0]["children"][0]["run"]) < 5000


def test_run_each_writes_one_bundle_per_item_and_a_summary(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    payload = {"command": "query vector-text", "rows": [
        {"passage_id": "p1", "chunk_id": "c1", "text": "t", "relevance": 0.5, "doc": {"id": "d1", "title": "T"}},
        {"passage_id": "p2", "chunk_id": "c2", "text": "t", "relevance": 0.4, "doc": {"id": "memo", "title": "memo"}}]}

    def step(argv, sid):
        return StepResult(sid, list(argv), 0, None, payload, 3.0, 50)

    claims = [each.Claim("c1", "a", "memo"), each.Claim("c2", "b", None)]
    summary = each.run_each(claims, ["bank"], step_fn=step, max_items=50)
    assert summary["items"] == 2 and summary["escalated"] == 0 and summary["exec_ms_p90"] is not None
    lines = (tmp_path / "logs" / "runs" / f"{summary['handle']}.jsonl").read_text().splitlines()
    first = json.loads(lines[0])
    assert first["id"] == "c1" and first["bundle"]["bundle_version"] == 1
    assert all((e["doc"] or {}).get("id") != "memo" for e in first["bundle"]["evidence"])  # the claim's own source is left out


def test_max_items_caps_the_batch(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    claims = [each.Claim(f"c{i}", "a", None) for i in range(5)]
    summary = each.run_each(claims, ["bank"], step_fn=lambda a, s: StepResult(s, a, 0, None, {"rows": []}, 1.0, 1),
                            max_items=2)
    assert summary["items"] == 2 and summary["skipped_items"] == 3


def test_both_item_commands_resolve_in_the_click_tree():
    import shlex

    import click

    from artmind.cli import cli

    plan = each.item_plan(each.Claim("c1", "fees rose", None), ["bank"])
    for child in plan["program"][0]["children"]:
        tokens = shlex.split(child["run"])
        assert tokens[0] == "artmind"
        command, i = cli, 1
        while i < len(tokens) and hasattr(command, "commands") and tokens[i] in command.commands:
            command = command.commands[tokens[i]]
            i += 1
        assert not hasattr(command, "commands"), child["run"]
        by_name = {opt: p for p in command.params for opt in getattr(p, "opts", [])}
        rest, j = tokens[i:], 0
        while j < len(rest):
            if rest[j].startswith("--"):
                param = by_name.get(rest[j])
                assert param is not None, f"unknown option {rest[j]} in {child['run']}"
                if not getattr(param, "is_flag", False):
                    value = rest[j + 1]
                    if isinstance(param.type, click.IntRange):
                        param.type.convert(value, param, None)
                    j += 1
            j += 1


def test_drop_source_matches_ids_titles_and_names_with_or_without_extension():
    for source in ("memo", "memo.md", "MEMO.MD"):
        for doc in ({"id": "memo"}, {"id": "memo.md"}, {"title": "Memo.md"}, {"doc_id": "memo"}):
            printed = {"evidence": [{"doc": doc}, {"doc": {"id": "other"}}], "documents": [{**doc}, {"doc_id": "other"}]}
            out = each._drop_source(printed, source)
            assert len(out["evidence"]) == 1 and len(out["documents"]) == 1, (source, doc)
