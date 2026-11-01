from artmind.topics import build, prompts, store
from artmind.topics.gather import Material, Source


def _materials():
    a = Material("D1", "One", "one.md", "j", "S1", [Source("c1_p01", "c1", "Notice is 30 days.")])
    b = Material("D2", "Two", "two.md", "j", None, [Source("c2_p01", "c2", "Unrelated.")])
    return [a, b]


LINKS = {"c1_p01": {"path": "p/one.md", "title": "One", "header_path": "Terms", "doc_id": "D1", "name": "one.md",
                    "version": "v1"},
         "c2_p01": {"path": "p/two.md", "title": "Two", "doc_id": "D2", "name": "two.md", "version": "v2"}}


def _map(prompt):
    if "Unrelated." in prompt:
        return {"relevant": False, "notes": []}
    return {"relevant": True, "notes": [{"claim": "Notice is 30 days.", "passages": ["c1_p01", "bogus"]}]}


def _reduce(prompt):
    assert "[#c1_p01]" in prompt and "bogus" not in prompt
    return "## Overview\n\nNotice is 30 days [#c1_p01][#zzz].\n\n## Where sources differ\n\nNone found."


def test_map_prompt_lists_passages_with_ids_and_parse_filters_ids():
    p = prompts.map_prompt("notice", None, _materials()[0])
    assert "[c1_p01] Notice is 30 days." in p and "S1" in p
    parsed = prompts.parse_map({"relevant": True, "notes": [{"claim": "x", "passages": ["c1_p01", "no"]}]}, {"c1_p01"})
    assert parsed == {"relevant": True, "notes": [{"claim": "x", "passages": ["c1_p01"]}]}
    assert prompts.parse_map("junk", {"c1_p01"}) is None


def test_build_writes_a_checked_cited_page(tmp_path):
    out = build.build_page("Notice periods", ["j"], out=tmp_path, model="m", anchor={"kind": "topic"},
                           gathered=(_materials(), LINKS), map_llm=_map, reduce_llm=_reduce,
                           now="2026-11-02T10:00:00Z")
    page = store.read_page(tmp_path, "notice-periods")
    assert out["path"] == str(tmp_path / "notice-periods.md") and out["calls"] == 3
    assert page.meta["sources"] == [{"doc_id": "D1", "name": "one.md", "content_sha": "v1", "passages": ["c1_p01"]}]
    assert page.meta["checks"] == {"uncited_paragraphs": 1, "unsupported_numbers": 0, "removed_citations": 1}
    assert page.meta["anchor"] == {"kind": "topic"} and page.meta["built"] == "2026-11-02T10:00:00Z"
    assert "Notice is 30 days [^1]." in page.body and "[^1]: [[p/one|One]]" in page.body
    assert out["documents"] == ["one.md"]


def test_dry_run_counts_calls_and_writes_nothing(tmp_path):
    def forbidden(prompt):
        raise AssertionError("LLM called in a dry run")

    out = build.build_page("Notice periods", ["j"], out=tmp_path, model="m", anchor={"kind": "topic"},
                           gathered=(_materials(), LINKS), map_llm=forbidden, reduce_llm=forbidden, dry_run=True)
    assert out["calls"] == 3 and out["dry_run"] is True and not list(tmp_path.iterdir())


def test_nothing_relevant_writes_no_page(tmp_path):
    out = build.build_page("x", ["j"], out=tmp_path, model="m", anchor={"kind": "topic"},
                           gathered=(_materials(), LINKS), map_llm=lambda p: {"relevant": False, "notes": []},
                           reduce_llm=_reduce)
    assert out["path"] is None and "no document" in out["error"] and not list(tmp_path.iterdir())


def test_an_entity_anchor_is_recorded_without_its_chunks(tmp_path):
    entity = {"kind": "entity", "id": "e1", "key": "k", "name": "Acme", "entity_class": "ORG",
              "description": "A supplier.", "observation_set_hash": "h", "chunks": [{"id": "c1"}]}
    build.build_page("Acme", ["j"], out=tmp_path, model="m", anchor=entity, gathered=(_materials(), LINKS),
                     map_llm=_map, reduce_llm=_reduce)
    meta = store.read_page(tmp_path, "acme").meta
    assert meta["anchor"] == {"kind": "entity", "key": "k", "name": "Acme", "observation_set_hash": "h"}


def test_long_notes_are_reduced_in_groups(tmp_path, monkeypatch):
    monkeypatch.setattr(prompts, "REDUCE_INPUT_CHARS", 10)
    calls = []

    def reduce(prompt):
        calls.append("merge" if "partial pages" in prompt else "reduce")
        return "## Overview\n\nNotice is 30 days [#c1_p01]."

    mats = [Material(f"D{i}", f"T{i}", f"n{i}.md", "j", None, [Source(f"c{i}_p01", f"c{i}", "Notice is 30 days.")])
            for i in range(3)]
    links = {f"c{i}_p01": {"path": f"n{i}.md", "doc_id": f"D{i}", "name": f"n{i}.md", "version": "v"} for i in range(3)}

    def mapper(prompt):
        cid = prompt.split("[")[-1].split("]")[0]
        return {"relevant": True, "notes": [{"claim": "Notice is 30 days.", "passages": [cid]}]}

    build.build_page("t", ["j"], out=tmp_path, model="m", anchor={"kind": "topic"}, gathered=(mats, links),
                     map_llm=mapper, reduce_llm=reduce)
    assert calls.count("reduce") == 3 and calls.count("merge") == 1
