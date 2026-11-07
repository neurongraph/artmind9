"""The planner marks bound nodes and leaves their placeholders to the executor (P3 spec §4.3)."""

from artmind.query_plan import planner, program as prog

VT = "artmind query vector-text --shape brief --domain {domains} --topK 5 {search_term} --compact"
CHUNKS = "artmind query chunks --idList {anchor.top_chunk} --expand 2 --shape brief --compact"
P10 = "artmind query graph pattern10 --domain {domains} --documentName {doc} --compact"
CTX = {"domains": "journal", "domain_list": ("journal",), "search_term": "trip"}


def test_a_bound_run_node_is_filled_and_lists_its_binds():
    node = prog.Node("around", "run", run=CHUNKS)
    out = planner._node_json(node, CTX)
    assert out["filled"] is True and out["binds"] == ["anchor.top_chunk"]
    assert out["run"] == CHUNKS


def test_a_map_over_a_bound_list_has_no_items_yet():
    do = prog.Node("day", "run", run=P10)
    node = prog.Node("dated", "map", map=prog.MapSpec("anchor.window_docs", "doc", do, 6))
    out = planner._node_json(node, CTX)
    assert out["map"]["over_bound"] == "anchor.window_docs"
    assert out["map"]["items"] == [] and out["map"]["expanded"] == []
    assert out["map"]["do"]["run"] == P10.replace("{domains}", "journal")


def test_expand_commands_skips_bound_maps():
    do = prog.Node("day", "run", run=P10)
    node = prog.Node("dated", "map", map=prog.MapSpec("anchor.window_docs", "doc", do, 6))
    assert planner._expand_commands((node,), CTX, {}) == []


def test_the_per_domain_branch_keeps_binds():
    ctx = {"domains": "a,b", "domain_list": ("a", "b"), "search_term": "trip"}
    run = "artmind query graph pattern10 --domain {domain} --documentName {anchor.top_doc} --compact"
    out = planner._node_json(prog.Node("day", "run", run=run), ctx)
    assert out.get("per_domain") and len(out["children"]) == 2
    assert all(c.get("binds") == ["anchor.top_doc"] for c in out["children"])
